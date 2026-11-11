"""Recorrências mensais: moldes de receitas, despesas e compras no crédito.

Cada recorrência gera um lançamento por mês (origem ``RECORRENCIA``), criado logo que
o mês começa, com a data do dia combinado — mesmo que ele ainda não tenha chegado: um
lançamento com data futura já conta no saldo e no orçamento do mês. Depois de gerado,
é um lançamento como outro qualquer: pode ser editado ou cancelado sem afetar o molde.

A recorrência de crédito gera uma compra à vista (1 parcela), que cai no mês seguinte
ao da compra, como qualquer compra sem mês da 1ª parcela informado. A importação de
fatura reconhece essas compras (ver ``services/importacao.py``).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from typing import Any

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from financas.clock import Clock
from financas.db.models import (
    AcaoAuditoria,
    Categoria,
    Lancamento,
    Membro,
    Origem,
    Recorrencia,
    TipoLancamento,
)
from financas.domain.calendario import Mes
from financas.domain.dinheiro import validar_limites
from financas.domain.erros import (
    DataInvalida,
    ErroDominio,
    NaoEncontrado,
    OperacaoInvalida,
    SemPermissao,
)
from financas.domain.recorrencia import data_no_mes, meses_a_gerar, validar_dia, validar_periodo
from financas.domain.texto import validar_descricao
from financas.services import auditoria, autorizacao
from financas.services import lancamentos as svc_lancamentos
from financas.services.lancamentos import DadosLancamento

log = logging.getLogger(__name__)

ENTIDADE = "recorrencia"
TIPOS = (TipoLancamento.RECEITA, TipoLancamento.DEBITO, TipoLancamento.CREDITO)


@dataclass(frozen=True, slots=True)
class DadosRecorrencia:
    tipo: TipoLancamento
    valor: Decimal
    descricao: str
    dia: int
    # Primeiro mês; None = este mês se o dia ainda não passou, senão o mês seguinte.
    inicio: Mes | None = None
    fim: Mes | None = None
    categoria_id: int | None = None


def inicio_padrao(dia: int, hoje: date) -> Mes:
    """Este mês se o dia ainda não passou; senão o seguinte (o deste mês já foi lançado à mão)."""
    atual = Mes.de_data(hoje)
    return atual if data_no_mes(atual, dia) >= hoje else atual.somar(1)


def proxima_data(rec: Recorrencia) -> date | None:
    """Data do próximo lançamento a gerar; None se encerrada ou terminada."""
    if not rec.ativa:
        return None
    mes = Mes.de_data(rec.inicio)
    if rec.gerada_ate is not None:
        mes = max(mes, Mes.de_data(rec.gerada_ate).somar(1))
    if rec.fim is not None and mes > Mes.de_data(rec.fim):
        return None
    return data_no_mes(mes, rec.dia)


def pode_alterar(ator: Membro | None, rec: Recorrencia) -> bool:
    """Próprias (titular) para membros; qualquer uma para admin."""
    if ator is None or not ator.ativo:
        return False
    return ator.eh_admin or rec.membro_id == ator.id


def _exigir_alterar(ator: Membro | None, rec: Recorrencia) -> None:
    if not pode_alterar(ator, rec):
        raise SemPermissao("Você só pode alterar as suas próprias recorrências.")


def _foto(rec: Recorrencia) -> dict[str, Any]:
    return {
        "id": rec.id,
        "tipo": rec.tipo,
        "valor": str(rec.valor),
        "descricao": rec.descricao,
        "categoria_id": rec.categoria_id,
        "membro_id": rec.membro_id,
        "dia": rec.dia,
        "inicio": rec.inicio.isoformat(),
        "fim": rec.fim.isoformat() if rec.fim else None,
        "ativa": rec.ativa,
    }


def _validar(dados: DadosRecorrencia) -> tuple[Decimal, str]:
    if dados.tipo not in TIPOS:
        raise OperacaoInvalida("Tipo de recorrência inválido.")
    validar_dia(dados.dia)
    return validar_limites(dados.valor), validar_descricao(dados.descricao)


# ------------------------------------------------------------------ consultas


async def obter(s: AsyncSession, recorrencia_id: int, *, travar: bool = False) -> Recorrencia:
    consulta = select(Recorrencia).where(Recorrencia.id == recorrencia_id)
    if travar:
        consulta = consulta.with_for_update()
    rec = (await s.execute(consulta)).scalar_one_or_none()
    if rec is None:
        raise NaoEncontrado("Recorrência não encontrada.")
    return rec


async def listar(s: AsyncSession) -> list[Recorrencia]:
    r = await s.execute(
        select(Recorrencia).order_by(Recorrencia.ativa.desc(), Recorrencia.dia, Recorrencia.id)
    )
    return list(r.scalars())


# ------------------------------------------------------------------ operações


async def criar(
    s: AsyncSession,
    clock: Clock,
    ator: Membro,
    dados: DadosRecorrencia,
    *,
    origem: Origem = Origem.WEB,
    titular_id: int | None = None,
) -> Recorrencia:
    """Cria o molde e já gera o lançamento deste mês, se ele começa agora."""
    titular_id = ator.id if titular_id is None else titular_id
    autorizacao.exigir_criar_para(ator, titular_id)
    if titular_id != ator.id:
        titular = await s.get(Membro, titular_id)
        if titular is None or not titular.ativo:
            raise NaoEncontrado("Membro titular não encontrado ou inativo.")
    valor, descricao = _validar(dados)
    hoje = clock.hoje()
    inicio = dados.inicio or inicio_padrao(dados.dia, hoje)
    if inicio < Mes.de_data(hoje):
        raise DataInvalida(
            "A recorrência não pode começar num mês que já passou.",
            sugestao="Registre os meses anteriores como lançamentos comuns.",
        )
    validar_periodo(inicio, dados.fim)
    await svc_lancamentos.validar_categoria(s, dados.categoria_id, dados.tipo)

    agora = clock.agora()
    rec = Recorrencia(
        tipo=dados.tipo.value,
        valor=valor,
        descricao=descricao,
        categoria_id=dados.categoria_id,
        membro_id=titular_id,
        criado_por=ator.id,
        dia=dados.dia,
        inicio=inicio.primeiro_dia,
        fim=dados.fim.primeiro_dia if dados.fim else None,
        ativa=True,
        created_at=agora,
        updated_at=agora,
    )
    s.add(rec)
    await s.flush()
    await auditoria.registrar(
        s,
        clock,
        acao=AcaoAuditoria.CRIACAO,
        entidade=ENTIDADE,
        entidade_id=rec.id,
        autor_id=ator.id,
        origem=origem,
        depois=_foto(rec),
    )
    await gerar(s, clock, recorrencia_id=rec.id)
    return rec


async def editar(
    s: AsyncSession,
    clock: Clock,
    ator: Membro,
    recorrencia_id: int,
    novos: DadosRecorrencia,
    *,
    origem: Origem = Origem.WEB,
) -> Recorrencia:
    """Vale para os meses ainda não gerados. Tipo, titular e primeiro mês não mudam."""
    rec = await obter(s, recorrencia_id, travar=True)
    _exigir_alterar(ator, rec)
    if novos.tipo.value != rec.tipo:
        raise OperacaoInvalida("O tipo da recorrência não pode ser alterado.")
    valor, descricao = _validar(novos)
    validar_periodo(Mes.de_data(rec.inicio), novos.fim)
    # Categoria mantida não é revalidada: ela pode ter sido desativada depois.
    if novos.categoria_id != rec.categoria_id:
        await svc_lancamentos.validar_categoria(s, novos.categoria_id, novos.tipo)
    antes = _foto(rec)
    rec.valor = valor
    rec.descricao = descricao
    rec.dia = novos.dia
    rec.fim = novos.fim.primeiro_dia if novos.fim else None
    rec.categoria_id = novos.categoria_id
    rec.updated_at = clock.agora()
    await s.flush()
    await _auditar_edicao(s, clock, ator, rec, antes, origem=origem)
    return rec


async def definir_ativa(
    s: AsyncSession,
    clock: Clock,
    ator: Membro,
    recorrencia_id: int,
    ativa: bool,
    *,
    origem: Origem = Origem.WEB,
) -> Recorrencia:
    """Encerra (para de gerar; o já lançado fica) ou reativa.

    Ao reativar, os meses que passaram com ela encerrada não são gerados; o mês
    corrente é, se ainda não tinha sido.
    """
    rec = await obter(s, recorrencia_id, travar=True)
    _exigir_alterar(ator, rec)
    if rec.ativa == ativa:
        return rec
    antes = _foto(rec)
    rec.ativa = ativa
    rec.updated_at = clock.agora()
    if ativa:
        anterior = Mes.de_data(clock.hoje()).somar(-1).primeiro_dia
        if rec.gerada_ate is None or rec.gerada_ate < anterior:
            rec.gerada_ate = anterior
    await s.flush()
    await _auditar_edicao(s, clock, ator, rec, antes, origem=origem)
    if ativa:
        await gerar(s, clock, recorrencia_id=rec.id)
    return rec


async def _auditar_edicao(
    s: AsyncSession,
    clock: Clock,
    ator: Membro,
    rec: Recorrencia,
    antes: dict[str, Any],
    *,
    origem: Origem,
) -> None:
    await auditoria.registrar(
        s,
        clock,
        acao=AcaoAuditoria.EDICAO,
        entidade=ENTIDADE,
        entidade_id=rec.id,
        autor_id=ator.id,
        origem=origem,
        antes=antes,
        depois=_foto(rec),
    )


# ------------------------------------------------------------------ geração


async def gerar(
    s: AsyncSession, clock: Clock, *, recorrencia_id: int | None = None
) -> list[Lancamento]:
    """Gera os lançamentos que faltam até o mês corrente. Pode rodar quantas vezes for.

    Cada recorrência é travada enquanto é tratada e guarda até que mês já foi gerada;
    o banco ainda impede dois lançamentos da mesma recorrência no mesmo mês. Meses em
    que o sistema ficou parado são recuperados na execução seguinte.
    """
    atual = Mes.de_data(clock.hoje())
    consulta = (
        select(Recorrencia)
        .where(
            Recorrencia.ativa,
            Recorrencia.inicio <= atual.primeiro_dia,
            or_(Recorrencia.gerada_ate.is_(None), Recorrencia.gerada_ate < atual.primeiro_dia),
        )
        .order_by(Recorrencia.id)
        .with_for_update()
    )
    if recorrencia_id is not None:
        consulta = consulta.where(Recorrencia.id == recorrencia_id)
    criados: list[Lancamento] = []
    for rec in (await s.execute(consulta)).scalars():
        try:
            async with s.begin_nested():
                criados += await _gerar_uma(s, clock, rec, atual)
        except ErroDominio as e:
            # Fica para a próxima execução; as outras recorrências seguem.
            log.warning("recorrencia_nao_gerada", extra={"recorrencia_id": rec.id, "erro": str(e)})
    return criados


async def _gerar_uma(
    s: AsyncSession, clock: Clock, rec: Recorrencia, atual: Mes
) -> list[Lancamento]:
    meses = meses_a_gerar(
        Mes.de_data(rec.inicio),
        Mes.de_data(rec.fim) if rec.fim else None,
        Mes.de_data(rec.gerada_ate) if rec.gerada_ate else None,
        atual,
    )
    criados: list[Lancamento] = []
    titular = await s.get(Membro, rec.membro_id)
    assert titular is not None
    if meses and not titular.ativo:
        # Titular inativo não recebe lançamentos; esses meses ficam sem gerar.
        log.warning("recorrencia_titular_inativo", extra={"recorrencia_id": rec.id})
        meses = []
    if meses:
        # Assina quem criou a recorrência; se não puder mais lançar para o titular, o titular.
        criador = await s.get(Membro, rec.criado_por)
        pode = (
            criador is not None and criador.ativo and (criador.eh_admin or criador.id == titular.id)
        )
        ator = criador if pode and criador is not None else titular
        categoria_id = rec.categoria_id
        if categoria_id is not None:
            categoria = await s.get(Categoria, categoria_id)
            if categoria is None or not categoria.ativa:
                categoria_id = None  # desativada depois: o lançamento sai sem categoria
        for mes in meses:
            dados = DadosLancamento(
                tipo=TipoLancamento(rec.tipo),
                valor=rec.valor,
                descricao=rec.descricao,
                data_operacao=data_no_mes(mes, rec.dia),
                qtd_parcelas=1 if rec.tipo == TipoLancamento.CREDITO.value else None,
                categoria_id=categoria_id,
            )
            r = await svc_lancamentos.criar(
                s,
                clock,
                ator,
                dados,
                origem=Origem.RECORRENCIA,
                titular_id=titular.id,
                recorrencia=(rec.id, mes.primeiro_dia),
            )
            criados.append(r.lancamento)
            log.info(
                "recorrencia_gerada",
                extra={"recorrencia_id": rec.id, "lancamento_id": r.lancamento.id, "mes": str(mes)},
            )
    rec.gerada_ate = atual.primeiro_dia
    await s.flush()
    return criados
