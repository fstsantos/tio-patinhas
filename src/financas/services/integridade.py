"""Verificação de integridade entre compras e parcelas (``financas verificar-integridade``).

Reproduz em consultas as mesmas regras dos constraint triggers, para auditoria
periódica e diagnóstico (o trigger impede gravações novas; esta verificação
confere o estado existente).
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_VERIFICACOES: dict[str, str] = {
    "soma das parcelas vigentes difere do valor total": """
        SELECT l.id, format('total %s, soma %s', l.valor_total, coalesce(sum(p.valor), 0))
          FROM lancamentos l
          LEFT JOIN parcelas p ON p.lancamento_id = l.id AND p.status <> 'SUBSTITUIDA'
         WHERE l.tipo = 'CREDITO'
         GROUP BY l.id, l.valor_total
        HAVING coalesce(sum(p.valor), 0) <> l.valor_total
    """,
    "quantidade de parcelas vigentes difere da quantidade contratada": """
        SELECT l.id, format('esperado %s, encontrado %s', l.qtd_parcelas, count(p.id))
          FROM lancamentos l
          LEFT JOIN parcelas p ON p.lancamento_id = l.id AND p.status <> 'SUBSTITUIDA'
         WHERE l.tipo = 'CREDITO'
         GROUP BY l.id, l.qtd_parcelas
        HAVING count(p.id) <> l.qtd_parcelas
    """,
    "lançamento cancelado com parcela pendente": """
        SELECT DISTINCT l.id, 'parcela pendente'
          FROM lancamentos l JOIN parcelas p ON p.lancamento_id = l.id
         WHERE l.status = 'CANCELADO' AND p.status = 'PENDENTE'
    """,
    "lançamento ativo com parcela vigente cancelada": """
        SELECT DISTINCT l.id, 'parcela cancelada'
          FROM lancamentos l JOIN parcelas p ON p.lancamento_id = l.id
         WHERE l.status = 'ATIVO' AND p.status = 'CANCELADA'
    """,
    "parcela de lançamento que não é compra no crédito": """
        SELECT DISTINCT l.id, l.tipo
          FROM lancamentos l JOIN parcelas p ON p.lancamento_id = l.id
         WHERE l.tipo <> 'CREDITO'
    """,
    "parcela órfã": """
        SELECT p.id, format('lancamento_id %s', p.lancamento_id)
          FROM parcelas p LEFT JOIN lancamentos l ON l.id = p.lancamento_id
         WHERE l.id IS NULL
    """,
}


@dataclass(frozen=True, slots=True)
class Problema:
    regra: str
    entidade_id: int
    detalhe: str


async def verificar_integridade(sessao: AsyncSession) -> list[Problema]:
    problemas: list[Problema] = []
    for regra, sql in _VERIFICACOES.items():
        for entidade_id, detalhe in (await sessao.execute(text(sql))).tuples():
            problemas.append(Problema(regra, int(entidade_id), str(detalhe)))
    return problemas
