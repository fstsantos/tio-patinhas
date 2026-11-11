"""Dashboard: indicadores do mês, evolução anual, distribuição e compromissos futuros.

Os números vêm das mesmas funções de ``services.relatorios`` usadas pelo bot.
"""

from __future__ import annotations

import json
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse

from financas.domain.calendario import Mes
from financas.domain.erros import ErroDominio
from financas.services import membros, relatorios
from financas.web.app import estado, render, usuario_atual
from financas.web.auth import Usuario

router = APIRouter()

ANO_MIN, ANO_MAX = 2000, 2100


def mes_do_campo(valor: str) -> Mes | None:
    """Mês digitado como ``mm/aaaa``; inválido → None.

    ``aaaa-mm`` continua aceito, para links antigos do dashboard.
    """
    try:
        if "-" in valor:
            ano, mes_txt = valor.split("-", 1)
            return Mes(int(ano), int(mes_txt))
        return Mes.parse(valor)
    except (ValueError, ErroDominio):
        return None


def mes_da_query(valor: str | None, padrao: Mes) -> Mes:
    mes = mes_do_campo(valor) if valor else None
    if mes is None:
        return padrao
    # Fora do intervalo aceito (o mesmo do "resumo ano" do bot), vale o padrão.
    return mes if ANO_MIN <= mes.ano <= ANO_MAX else padrao


def inteiro_ou_none(valor: str | None) -> int | None:
    try:
        return int(valor) if valor else None
    except ValueError:
        return None


def _num(v: Decimal) -> float:
    # Apenas para desenhar gráficos; todos os valores exibidos vêm formatados do Decimal.
    return float(v)


@router.get("/", response_class=HTMLResponse)
async def dashboard(request: Request, usuario: Usuario = Depends(usuario_atual)) -> HTMLResponse:
    e = estado(request)
    hoje = e.clock.hoje()
    mes = mes_da_query(request.query_params.get("mes"), Mes.de_data(hoje))
    membro_id = inteiro_ou_none(request.query_params.get("membro"))
    async with e.fabrica() as s:
        resumo = await relatorios.resumo_mensal(s, mes, hoje, membro_id=membro_id)
        anterior = await relatorios.resumo_mensal(s, mes.somar(-1), hoje, membro_id=membro_id)
        anual = await relatorios.resumo_anual(s, mes.ano, hoje, membro_id=membro_id)
        credito = await relatorios.resumo_credito(s, hoje, membro_id=membro_id)
        lista_membros = await membros.listar(s, incluir_inativos=True)
    graficos: dict[str, Any] = {
        "evolucao": {
            "rotulos": [m.orcamento.mes.abreviado for m in anual.meses],
            "receitas": [_num(m.orcamento.receitas) for m in anual.meses],
            "despesas": [_num(m.orcamento.despesas_consideradas) for m in anual.meses],
            "acumulado": [_num(m.saldo_acumulado) for m in anual.meses],
        },
        "distribuicao": {
            "rotulos": [g.nome for g in (resumo.por_categoria or resumo.por_descricao)[:10]],
            "valores": [_num(g.total) for g in (resumo.por_categoria or resumo.por_descricao)[:10]],
        },
        "compromissos": {
            "rotulos": [b.mes.abreviado for b in credito.blocos[:12]],
            "valores": [_num(b.subtotal) for b in credito.blocos[:12]],
        },
    }
    return render(
        request,
        "dashboard.html",
        usuario,
        resumo=resumo,
        anterior=anterior,
        anual=anual,
        credito=credito,
        membros=lista_membros,
        membro_id=membro_id,
        mes=mes,
        graficos=json.dumps(graficos),
    )
