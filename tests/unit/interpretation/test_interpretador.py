from datetime import date
from decimal import Decimal

import pytest

from financas.integrations.llm.base import ErroLLM, LLMFalsa
from financas.interpretation import comandos as c
from financas.interpretation.interpretador import interpretar_voz

HOJE = date(2026, 3, 20)
D = Decimal


@pytest.mark.parametrize(
    ("falado", "esperado"),
    [
        (
            "Registra um débito de dez reais e cinquenta no mercado.",
            c.Debito(D("10.50"), "mercado", HOJE),
        ),
        ("débito 45 reais farmácia ontem", c.Debito(D("45.00"), "farmácia", date(2026, 3, 19))),
        ("gastei cinquenta reais na padaria", c.Debito(D("50.00"), "padaria", HOJE)),
        (
            "crédito de mil e duzentos em dez vezes na loja de móveis",
            c.Credito(D("1200.00"), 10, "loja de móveis", HOJE),
        ),
        (
            "comprei uma geladeira de três mil reais em dez vezes",
            c.Credito(D("3000.00"), 10, "comprei uma geladeira", HOJE),
        ),
        ("recebi três mil e quinhentos de salário", c.Ganho(D("3500.00"), "salário", HOJE)),
        ("ganho 3500 salário", c.Ganho(D("3500.00"), "salário", HOJE)),
        ("resumo mês", c.ResumoMes()),
        ("débito de dez e vinte no mercado", c.Debito(D("10.20"), "mercado", HOJE)),
    ],
)
async def test_interpretacoes(falado: str, esperado: c.Comando) -> None:
    r = await interpretar_voz(falado, HOJE)
    assert r.pergunta is None
    assert r.comando == esperado


@pytest.mark.parametrize(
    ("falado", "trecho"),
    [
        ("dez parcelas de cem na loja", "cada parcela ou o total"),
        ("crédito de mil reais na loja", "quantas parcelas"),
        ("débito no mercado", "valor"),
        ("paguei cinquenta reais", "descrição"),
        ("cinquenta reais no mercado", "débito"),
        ("débito de 10 reais mercado 20 reais", "mais de um valor"),
        ("recebi e gastei dez reais", "débito"),
        ("", "entender"),
    ],
)
async def test_perguntas(falado: str, trecho: str) -> None:
    r = await interpretar_voz(falado, HOJE)
    assert r.comando is None
    assert r.pergunta is not None and trecho.lower() in r.pergunta.lower()


async def test_llm_usada_quando_heuristica_falha() -> None:
    llm = LLMFalsa({"tipo": "debito", "valor": "32,90", "descricao": "uber", "data": None})
    r = await interpretar_voz("aquela corrida custou trinta e dois e noventa", HOJE, llm=llm)
    assert r.comando == c.Debito(D("32.90"), "uber", HOJE)
    assert r.via_llm
    assert llm.chamadas


async def test_proposta_invalida_da_llm_vira_pergunta() -> None:
    for resposta in (
        {"tipo": "debito", "valor": "10.50", "descricao": "x"},
        {"tipo": "transferencia", "valor": "1", "descricao": "x"},
        {"tipo": "debito", "valor": "10", "descricao": "x", "data": "2027-13-01"},
        {"valor": "10"},
    ):
        r = await interpretar_voz("blá blá", HOJE, llm=LLMFalsa(resposta))
        assert r.comando is None and r.pergunta


async def test_falha_ou_timeout_da_llm() -> None:
    r = await interpretar_voz("blá", HOJE, llm=LLMFalsa(erro=ErroLLM("fora do ar")))
    assert r.comando is None and r.pergunta

    class Lenta(LLMFalsa):
        async def propor_comando(self, transcricao: str, hoje: date):  # type: ignore[no-untyped-def]
            import asyncio

            await asyncio.sleep(1)

    r = await interpretar_voz("blá", HOJE, llm=Lenta(), timeout_llm=0.01)
    assert r.comando is None and r.pergunta


async def test_ambiguidade_parcela_nao_vai_para_llm() -> None:
    llm = LLMFalsa({"tipo": "credito", "valor": "1000", "parcelas": 10, "descricao": "loja"})
    r = await interpretar_voz("dez parcelas de cem na loja", HOJE, llm=llm)
    assert r.comando is None
    assert llm.chamadas == []
