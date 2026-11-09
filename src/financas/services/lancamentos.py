"""Serviço de lançamentos: criação, cancelamento, edição/reparcelamento e restauração.

Único ponto de entrada das regras de lançamento para bot, web e CLI. Cada função
opera dentro da transação da sessão recebida; quem chama faz o commit.
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.exc import StaleDataError

from financas.clock import Clock
from financas.db.models import (
    AcaoAuditoria,
    Categoria,
    Lancamento,
    Membro,
    Origem,
    Parcela,
    StatusLancamento,
    StatusParcela,
    TipoCategoria,
    TipoLancamento,
)
from financas.domain.dinheiro import validar_limites
from financas.domain.erros import (
    ConflitoConcorrencia,
    DataInvalida,
    NaoEncontrado,
    OperacaoInvalida,
    ParcelasInvalidas,
)
from financas.domain.parcelamento import gerar_parcelas, validar_qtd_parcelas
from financas.domain.texto import normalizar, validar_descricao
from financas.services import auditoria, autorizacao

JANELA_DESFAZER = timedelta(minutes=10)
MOTIVO_MAX = 300


@dataclass(frozen=True, slots=True)
class DadosLancamento:
    tipo: TipoLancamento
    valor: Decimal
    descricao: str
    data_operacao: date
    qtd_parcelas: int | None = None
    categoria_id: int | None = None


@dataclass(frozen=True, slots=True)
class LancamentoComParcelas:
    lancamento: Lancamento
    parcelas: list[Parcela]


# ------------------------------------------------------------------ validação


def validar_dados(dados: DadosLancamento, hoje: date) -> DadosLancamento:
    valor = validar_limites(dados.valor)
    descricao = validar_descricao(dados.descricao)
    if dados.data_operacao > hoje:
        raise DataInvalida(
            "Datas futuras não são permitidas.",
            sugestao="Registre o lançamento no dia em que ele acontecer.",
        )
    qtd = dados.qtd_parcelas
    if dados.tipo is TipoLancamento.CREDITO:
        if qtd is None:
            raise ParcelasInvalidas("Informe a quantidade de parcelas.")
        validar_qtd_parcelas(qtd)
    elif qtd is not None:
        raise ParcelasInvalidas("Somente compras no crédito têm parcelas.")
    return replace(dados, valor=valor, descricao=descricao)


async def _validar_categoria(
    s: AsyncSession, categoria_id: int | None, tipo: TipoLancamento
) -> None:
    if categoria_id is None:
        return
    cat = await s.get(Categoria, categoria_id)
    if cat is None or not cat.ativa:
        raise NaoEncontrado("Categoria não encontrada ou inativa.")
    esperado = TipoCategoria.RECEITA if tipo is TipoLancamento.RECEITA else TipoCategoria.DESPESA
    if cat.tipo not in (esperado.value, TipoCategoria.AMBAS.value):
        raise OperacaoInvalida(f"A categoria '{cat.nome}' não se aplica a este tipo.")


def _validar_motivo(motivo: str | None, *, obrigatorio: bool = False) -> str | None:
    limpo = (motivo or "").strip() or None
    if obrigatorio and limpo is None:
        raise OperacaoInvalida("Informe o motivo.")
    if limpo and len(limpo) > MOTIVO_MAX:
        raise OperacaoInvalida(f"O motivo pode ter no máximo {MOTIVO_MAX} caracteres.")
    return limpo


# ------------------------------------------------------------------ consultas


async def obter(s: AsyncSession, lancamento_id: int, *, travar: bool = False) -> Lancamento:
    consulta = select(Lancamento).where(Lancamento.id == lancamento_id)
    if travar:
        consulta = consulta.with_for_update()
    lanc = (await s.execute(consulta)).scalar_one_or_none()
    if lanc is None:
        raise NaoEncontrado(f"Lançamento #{lancamento_id} não encontrado.")
    return lanc


async def parcelas_vigentes(s: AsyncSession, lancamento_id: int) -> list[Parcela]:
    r = await s.execute(
        select(Parcela)
        .where(
            Parcela.lancamento_id == lancamento_id,
            Parcela.status != StatusParcela.SUBSTITUIDA.value,
        )
        .order_by(Parcela.numero)
    )
    return list(r.scalars())


async def todas_parcelas(s: AsyncSession, lancamento_id: int) -> list[Parcela]:
    r = await s.execute(
        select(Parcela)
        .where(Parcela.lancamento_id == lancamento_id)
        .order_by(Parcela.revisao_lancamento.desc(), Parcela.numero)
    )
    return list(r.scalars())


async def ultimos(s: AsyncSession, membro_id: int, limite: int = 10) -> list[Lancamento]:
    r = await s.execute(
        select(Lancamento)
        .where(Lancamento.membro_id == membro_id, Lancamento.status == StatusLancamento.ATIVO.value)
        .order_by(Lancamento.created_at.desc(), Lancamento.id.desc())
        .limit(limite)
    )
    return list(r.scalars())


async def ultimo_desfazivel(s: AsyncSession, clock: Clock, membro: Membro) -> Lancamento | None:
    """Último lançamento ativo do membro criado por ele há no máximo 10 minutos."""
    limite = clock.agora() - JANELA_DESFAZER
    r = await s.execute(
        select(Lancamento)
        .where(
            Lancamento.membro_id == membro.id,
            Lancamento.criado_por == membro.id,
            Lancamento.status == StatusLancamento.ATIVO.value,
            Lancamento.created_at >= limite,
        )
        .order_by(Lancamento.created_at.desc(), Lancamento.id.desc())
        .limit(1)
    )
    return r.scalar_one_or_none()


# ------------------------------------------------------------------ operações


def _novas_parcelas(lanc: Lancamento, agora_: datetime) -> list[Parcela]:
    assert lanc.qtd_parcelas is not None
    return [
        Parcela(
            lancamento_id=lanc.id,
            revisao_lancamento=lanc.revisao,
            numero=p.numero,
            valor=p.valor,
            competencia=p.competencia,
            status=StatusParcela.PENDENTE.value,
            created_at=agora_,
            updated_at=agora_,
        )
        for p in gerar_parcelas(lanc.valor_total, lanc.qtd_parcelas, lanc.data_operacao)
    ]


async def criar(
    s: AsyncSession,
    clock: Clock,
    ator: Membro,
    dados: DadosLancamento,
    *,
    origem: Origem,
    titular_id: int | None = None,
) -> LancamentoComParcelas:
    titular_id = ator.id if titular_id is None else titular_id
    autorizacao.exigir_criar_para(ator, titular_id)
    if titular_id != ator.id:
        titular = await s.get(Membro, titular_id)
        if titular is None or not titular.ativo:
            raise NaoEncontrado("Membro titular não encontrado ou inativo.")
    dados = validar_dados(dados, clock.hoje())
    await _validar_categoria(s, dados.categoria_id, dados.tipo)

    agora = clock.agora()
    lanc = Lancamento(
        tipo=dados.tipo.value,
        valor_total=dados.valor,
        descricao=dados.descricao,
        descricao_normalizada=normalizar(dados.descricao),
        categoria_id=dados.categoria_id,
        membro_id=titular_id,
        criado_por=ator.id,
        data_operacao=dados.data_operacao,
        qtd_parcelas=dados.qtd_parcelas,
        status=StatusLancamento.ATIVO.value,
        origem=origem.value,
        created_at=agora,
        updated_at=agora,
    )
    s.add(lanc)
    await s.flush()
    parcelas: list[Parcela] = []
    if lanc.tipo == TipoLancamento.CREDITO.value:
        parcelas = _novas_parcelas(lanc, agora)
        s.add_all(parcelas)
        await s.flush()
    await auditoria.registrar(
        s,
        clock,
        acao=AcaoAuditoria.CRIACAO,
        entidade_id=lanc.id,
        autor_id=ator.id,
        origem=origem,
        depois=auditoria.foto_lancamento(lanc, parcelas or None),
    )
    return LancamentoComParcelas(lanc, parcelas)


async def _flush_versionado(s: AsyncSession) -> None:
    try:
        await s.flush()
    except StaleDataError as e:
        raise ConflitoConcorrencia(
            "Este lançamento foi alterado por outra pessoa.",
            sugestao="Recarregue a página e tente novamente.",
        ) from e


def _conferir_revisao(lanc: Lancamento, revisao_esperada: int | None) -> None:
    if revisao_esperada is not None and lanc.revisao != revisao_esperada:
        raise ConflitoConcorrencia(
            "Este lançamento foi alterado por outra pessoa.",
            sugestao="Recarregue a página e tente novamente.",
        )


async def cancelar(
    s: AsyncSession,
    clock: Clock,
    ator: Membro,
    lancamento_id: int,
    *,
    origem: Origem,
    motivo: str | None = None,
    revisao_esperada: int | None = None,
) -> Lancamento:
    """Cancela o lançamento e, se for compra no crédito, todas as parcelas vigentes."""
    lanc = await obter(s, lancamento_id, travar=True)
    autorizacao.exigir_alterar(ator, lanc)
    if lanc.status == StatusLancamento.CANCELADO.value:
        raise OperacaoInvalida(f"O lançamento #{lanc.id} já está cancelado.")
    _conferir_revisao(lanc, revisao_esperada)
    motivo = _validar_motivo(motivo)
    parcelas = await parcelas_vigentes(s, lanc.id)
    antes = auditoria.foto_lancamento(lanc, parcelas or None)

    agora = clock.agora()
    lanc.status = StatusLancamento.CANCELADO.value
    lanc.cancelado_em = agora
    lanc.cancelado_por = ator.id
    lanc.motivo_cancelamento = motivo
    lanc.updated_at = agora
    for p in parcelas:
        p.status = StatusParcela.CANCELADA.value
        p.updated_at = agora
    await _flush_versionado(s)
    await auditoria.registrar(
        s,
        clock,
        acao=AcaoAuditoria.CANCELAMENTO,
        entidade_id=lanc.id,
        autor_id=ator.id,
        origem=origem,
        motivo=motivo,
        antes=antes,
        depois=auditoria.foto_lancamento(lanc, parcelas or None),
    )
    return lanc


async def editar(
    s: AsyncSession,
    clock: Clock,
    ator: Membro,
    lancamento_id: int,
    novos: DadosLancamento,
    *,
    origem: Origem,
    revisao_esperada: int,
    motivo: str | None = None,
) -> LancamentoComParcelas:
    """Edita um lançamento ativo.

    Descrição/categoria: altera só o lançamento. Valor, data ou parcelas de uma compra:
    marca as parcelas vigentes como SUBSTITUIDA e gera nova revisão, na mesma transação.
    """
    lanc = await obter(s, lancamento_id, travar=True)
    autorizacao.exigir_alterar(ator, lanc)
    if lanc.status != StatusLancamento.ATIVO.value:
        raise OperacaoInvalida("Lançamentos cancelados não podem ser editados.")
    _conferir_revisao(lanc, revisao_esperada)
    if novos.tipo.value != lanc.tipo:
        raise OperacaoInvalida("O tipo do lançamento não pode ser alterado.")
    novos = validar_dados(novos, clock.hoje())
    await _validar_categoria(s, novos.categoria_id, novos.tipo)
    motivo = _validar_motivo(motivo)

    parcelas = await parcelas_vigentes(s, lanc.id)
    antes = auditoria.foto_lancamento(lanc, parcelas or None)
    reparcelar = lanc.tipo == TipoLancamento.CREDITO.value and (
        novos.valor != lanc.valor_total
        or novos.data_operacao != lanc.data_operacao
        or novos.qtd_parcelas != lanc.qtd_parcelas
    )
    mudou = reparcelar or (
        novos.valor != lanc.valor_total
        or novos.data_operacao != lanc.data_operacao
        or novos.descricao != lanc.descricao
        or novos.categoria_id != lanc.categoria_id
    )
    if not mudou:
        return LancamentoComParcelas(lanc, parcelas)

    agora = clock.agora()
    lanc.valor_total = novos.valor
    lanc.data_operacao = novos.data_operacao
    lanc.qtd_parcelas = novos.qtd_parcelas
    lanc.descricao = novos.descricao
    lanc.descricao_normalizada = normalizar(novos.descricao)
    lanc.categoria_id = novos.categoria_id
    lanc.updated_at = agora
    if reparcelar:
        for p in parcelas:
            p.status = StatusParcela.SUBSTITUIDA.value
            p.updated_at = agora
    await _flush_versionado(s)  # incrementa lanc.revisao
    if reparcelar:
        parcelas = _novas_parcelas(lanc, agora)
        s.add_all(parcelas)
        await s.flush()
    await auditoria.registrar(
        s,
        clock,
        acao=AcaoAuditoria.EDICAO,
        entidade_id=lanc.id,
        autor_id=ator.id,
        origem=origem,
        motivo=motivo,
        antes=antes,
        depois=auditoria.foto_lancamento(lanc, parcelas or None),
    )
    return LancamentoComParcelas(lanc, parcelas)


async def restaurar(
    s: AsyncSession,
    clock: Clock,
    ator: Membro,
    lancamento_id: int,
    *,
    origem: Origem,
    motivo: str,
) -> LancamentoComParcelas:
    """Somente admin, motivo obrigatório. Reativa o lançamento e as parcelas da última revisão."""
    autorizacao.exigir_admin(ator)
    motivo_ok = _validar_motivo(motivo, obrigatorio=True)
    lanc = await obter(s, lancamento_id, travar=True)
    if lanc.status != StatusLancamento.CANCELADO.value:
        raise OperacaoInvalida(f"O lançamento #{lanc.id} não está cancelado.")
    parcelas = await parcelas_vigentes(s, lanc.id)
    antes = auditoria.foto_lancamento(lanc, parcelas or None)

    agora = clock.agora()
    lanc.status = StatusLancamento.ATIVO.value
    lanc.cancelado_em = None
    lanc.cancelado_por = None
    lanc.motivo_cancelamento = None
    lanc.updated_at = agora
    for p in parcelas:
        p.status = StatusParcela.PENDENTE.value
        p.updated_at = agora
    await _flush_versionado(s)
    await auditoria.registrar(
        s,
        clock,
        acao=AcaoAuditoria.RESTAURACAO,
        entidade_id=lanc.id,
        autor_id=ator.id,
        origem=origem,
        motivo=motivo_ok,
        antes=antes,
        depois=auditoria.foto_lancamento(lanc, parcelas or None),
    )
    return LancamentoComParcelas(lanc, parcelas)
