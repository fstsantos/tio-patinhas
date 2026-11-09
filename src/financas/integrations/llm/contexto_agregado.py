"""Contexto agregado e anonimizado para análises futuras por LLM (spec 8).

A LLM nunca consulta o banco: recebe apenas este resumo estruturado, produzido a partir
dos serviços de relatório. Nomes de membros viram rótulos ("Membro A"); não há
descrições livres individuais, só agregados por descrição normalizada/categoria.
"""

from __future__ import annotations

from collections import defaultdict
from decimal import Decimal
from string import ascii_uppercase
from typing import Any

from financas.services.relatorios import ResumoAnual, ResumoMensal


def rotulos_membros(nomes: list[str]) -> dict[str, str]:
    unicos = sorted(set(nomes))
    return {
        n: f"Membro {ascii_uppercase[i % 26]}{'' if i < 26 else i // 26}"
        for i, n in enumerate(unicos)
    }


def contexto_mensal(r: ResumoMensal, *, max_grupos: int = 15) -> dict[str, Any]:
    o = r.orcamento
    nomes = [x.membro for x in r.receitas + r.debitos] + [p.membro for p in r.parcelas]
    rotulos = rotulos_membros(nomes)
    por_membro: dict[str, Decimal] = defaultdict(lambda: Decimal("0"))
    for d in r.debitos:
        por_membro[rotulos[d.membro]] += d.valor
    return {
        "fatos_observados": {
            "mes": str(o.mes),
            "situacao": o.situacao.value,
            "receitas": str(o.receitas),
            "despesas_a_vista": str(o.debitos),
            "parcelas_do_mes": str(o.parcelas),
            "saldo": str(o.saldo),
            "dias_restantes": o.dias_restantes,
            "compras_credito_no_mes": str(r.total_credito_contratado),
            "despesas_por_categoria": {g.nome: str(g.total) for g in r.por_categoria[:max_grupos]},
            "despesas_por_descricao": {g.nome: str(g.total) for g in r.por_descricao[:max_grupos]},
            "despesas_a_vista_por_membro": {k: str(v) for k, v in sorted(por_membro.items())},
        },
        "observacao": "Valores em BRL. Parcelas são compromissos previstos, não pagamentos.",
    }


def contexto_anual(r: ResumoAnual) -> dict[str, Any]:
    return {
        "fatos_observados": {
            "ano": r.ano,
            "meses": [
                {
                    "mes": str(m.orcamento.mes),
                    "receitas": str(m.orcamento.receitas),
                    "despesas": str(m.orcamento.despesas_consideradas),
                    "saldo": str(m.orcamento.saldo),
                }
                for m in r.meses
            ],
            "saldo_acumulado": str(r.saldo),
        },
        "observacao": "Saldo acumulado começa em zero em janeiro; não é saldo bancário.",
    }
