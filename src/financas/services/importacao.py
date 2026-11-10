"""Importação de fatura de cartão: cada linha vira uma compra no crédito.

- Compra parcelada: é registrada inteira (valor da parcela × N), com o mês da 1ª
  parcela calculado a partir do mês da fatura. Uma parcela k > 1 recria a compra
  desde a 1ª parcela, nos meses anteriores.
- Regras (arquivo TOML): trechos de descrição a ignorar e trechos que definem a
  categoria. A comparação ignora acentos e maiúsculas; vale a primeira regra que casar.
- Reimportação: uma linha que já tem compra ativa equivalente (titular, data,
  descrição, parcelas e valor) não é criada de novo. Editar a compra depois de
  importada desfaz essa equivalência.
"""

from __future__ import annotations

import tomllib
from dataclasses import dataclass
from decimal import Decimal
from enum import StrEnum
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from financas.clock import Clock
from financas.db.models import (
    Lancamento,
    Membro,
    Origem,
    StatusLancamento,
    TipoCategoria,
    TipoLancamento,
)
from financas.domain.erros import ErroDominio, NaoEncontrado, OperacaoInvalida
from financas.domain.texto import normalizar
from financas.integrations.fatura.itau import Fatura, LinhaFatura
from financas.services import categorias
from financas.services import lancamentos as svc
from financas.services.lancamentos import DadosLancamento

CENTAVO = Decimal("0.01")
# O nome do estabelecimento vem truncado em 22 ou 23 caracteres conforme o formato
# da linha; a comparação usa o prefixo comum de 22, sem espaços.
_PREFIXO_DESCRICAO = 22


class Situacao(StrEnum):
    NOVA = "nova"
    EXISTENTE = "já existe"
    IGNORADA = "ignorada"
    ERRO = "erro"


@dataclass(frozen=True, slots=True)
class RegraCategoria:
    nome: str
    trechos: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Regras:
    ignorar: tuple[str, ...] = ()
    categorias: tuple[RegraCategoria, ...] = ()

    def ignora(self, descricao: str) -> bool:
        alvo = normalizar(descricao)
        return any(t in alvo for t in self.ignorar)

    def categoria(self, descricao: str) -> str | None:
        alvo = normalizar(descricao)
        for regra in self.categorias:
            if any(t in alvo for t in regra.trechos):
                return regra.nome
        return None


def _trechos(valor: object, onde: str) -> tuple[str, ...]:
    if not isinstance(valor, list) or not all(isinstance(t, str) and t.strip() for t in valor):
        raise OperacaoInvalida(f"Regras: '{onde}' deve ser uma lista de textos não vazios.")
    return tuple(normalizar(t) for t in valor)


def carregar_regras(caminho: Path) -> Regras:
    """Lê o TOML de regras: ``ignorar = [...]`` e tabelas ``[[categoria]]``."""
    try:
        dados = tomllib.loads(caminho.read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as e:
        raise OperacaoInvalida(f"Não foi possível ler as regras: {e}") from e
    desconhecidas = dados.keys() - {"ignorar", "categoria"}
    if desconhecidas:
        raise OperacaoInvalida(f"Regras: chave desconhecida '{sorted(desconhecidas)[0]}'.")
    tabelas = dados.get("categoria", [])
    if not isinstance(tabelas, list):
        raise OperacaoInvalida("Regras: use tabelas [[categoria]].")
    regras: list[RegraCategoria] = []
    for t in tabelas:
        nome = t.get("nome") if isinstance(t, dict) else None
        if not isinstance(nome, str) or not nome.strip():
            raise OperacaoInvalida("Regras: toda [[categoria]] precisa de 'nome'.")
        regras.append(RegraCategoria(nome.strip(), _trechos(t.get("trechos"), f"{nome}.trechos")))
    return Regras(_trechos(dados.get("ignorar", []), "ignorar"), tuple(regras))


@dataclass(frozen=True, slots=True)
class ItemPlano:
    linha: LinhaFatura
    situacao: Situacao
    categoria: str | None = None
    dados: DadosLancamento | None = None
    detalhe: str = ""


@dataclass(frozen=True, slots=True)
class Plano:
    fatura: Fatura
    titular: Membro
    itens: tuple[ItemPlano, ...]

    def com(self, situacao: Situacao) -> list[ItemPlano]:
        return [i for i in self.itens if i.situacao is situacao]

    def total(self, *situacoes: Situacao) -> Decimal:
        return sum((i.linha.valor for i in self.itens if i.situacao in situacoes), Decimal(0))


async def membro_por_nome(s: AsyncSession, nome: str) -> Membro:
    alvo = normalizar(nome)
    achados = [m for m in (await s.execute(select(Membro))).scalars() if normalizar(m.nome) == alvo]
    if len(achados) != 1 or not achados[0].ativo:
        raise NaoEncontrado(f"Membro ativo '{nome}' não encontrado.")
    return achados[0]


def _chave_descricao(descricao: str) -> str:
    return normalizar(descricao)[:_PREFIXO_DESCRICAO].replace(" ", "")


def _equivale(existente: Lancamento, dados: DadosLancamento) -> bool:
    """Mesma compra? Nas parceladas, o total pode diferir em até 1 centavo por parcela
    (a fatura pode concentrar o arredondamento numa parcela)."""
    qtd = dados.qtd_parcelas or 1
    tolerancia = CENTAVO * qtd if qtd > 1 else Decimal(0)
    return (
        existente.data_operacao == dados.data_operacao
        and existente.qtd_parcelas == qtd
        and abs(existente.valor_total - dados.valor) <= tolerancia
        and _chave_descricao(existente.descricao) == _chave_descricao(dados.descricao)
    )


def _dados(fatura: Fatura, linha: LinhaFatura) -> DadosLancamento:
    return DadosLancamento(
        tipo=TipoLancamento.CREDITO,
        valor=linha.valor * linha.qtd_parcelas,
        descricao=linha.descricao,
        data_operacao=linha.data,
        qtd_parcelas=linha.qtd_parcelas,
        primeira_competencia=fatura.mes.somar(1 - linha.parcela).primeiro_dia,
    )


async def planejar(s: AsyncSession, titular: Membro, fatura: Fatura, regras: Regras) -> Plano:
    """Classifica cada linha da fatura, sem gravar nada."""
    datas = {linha.data for linha in fatura.linhas}
    q = select(Lancamento).where(
        Lancamento.membro_id == titular.id,
        Lancamento.tipo == TipoLancamento.CREDITO.value,
        Lancamento.status == StatusLancamento.ATIVO.value,
        Lancamento.data_operacao.in_(datas),
    )
    livres = list((await s.execute(q)).scalars())
    itens: list[ItemPlano] = []
    for linha in fatura.linhas:
        if regras.ignora(linha.descricao):
            itens.append(ItemPlano(linha, Situacao.IGNORADA, detalhe="regra 'ignorar'"))
            continue
        if linha.valor <= 0:
            detalhe = "valor negativo (estorno): inclua um trecho em 'ignorar' ou lance à mão"
            itens.append(ItemPlano(linha, Situacao.ERRO, detalhe=detalhe))
            continue
        try:
            dados = svc.validar_dados(_dados(fatura, linha))
        except ErroDominio as e:
            itens.append(ItemPlano(linha, Situacao.ERRO, detalhe=str(e)))
            continue
        categoria = regras.categoria(linha.descricao)
        igual = next((x for x in livres if _equivale(x, dados)), None)
        if igual is not None:
            livres.remove(igual)  # cada compra existente cobre uma única linha
            itens.append(ItemPlano(linha, Situacao.EXISTENTE, categoria, dados, f"#{igual.id}"))
        else:
            itens.append(ItemPlano(linha, Situacao.NOVA, categoria, dados))
    return Plano(fatura, titular, tuple(itens))


async def executar(s: AsyncSession, clock: Clock, plano: Plano) -> list[Lancamento]:
    """Grava as linhas novas do plano. O titular é também o autor (origem ``CLI``)."""
    if plano.com(Situacao.ERRO):
        raise OperacaoInvalida("A fatura tem linhas com erro; nada foi importado.")
    ator = plano.titular
    ids = {normalizar(c.nome): c.id for c in await categorias.listar(s)}
    criados: list[Lancamento] = []
    for item in plano.com(Situacao.NOVA):
        assert item.dados is not None
        dados = item.dados
        if item.categoria is not None:
            chave = normalizar(item.categoria)
            if chave not in ids:
                nova = await categorias.criar(s, clock, ator, item.categoria, TipoCategoria.DESPESA)
                ids[chave] = nova.id
            dados = DadosLancamento(
                dados.tipo,
                dados.valor,
                dados.descricao,
                dados.data_operacao,
                dados.qtd_parcelas,
                ids[chave],
                dados.primeira_competencia,
            )
        criados.append((await svc.criar(s, clock, ator, dados, origem=Origem.CLI)).lancamento)
    return criados
