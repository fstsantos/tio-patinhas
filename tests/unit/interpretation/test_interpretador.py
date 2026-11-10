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
            c.Debito(D("10.50"), "Mercado", HOJE),
        ),
        ("débito 45 reais farmácia ontem", c.Debito(D("45.00"), "Farmácia", date(2026, 3, 19))),
        ("gastei cinquenta reais na padaria", c.Debito(D("50.00"), "Padaria", HOJE)),
        (
            "crédito de mil e duzentos em dez vezes na loja de móveis",
            c.Credito(D("1200.00"), 10, "Loja de móveis", HOJE),
        ),
        (
            "comprei uma geladeira de três mil reais em dez vezes",
            c.Credito(D("3000.00"), 10, "Comprei uma geladeira", HOJE),
        ),
        ("recebi três mil e quinhentos de salário", c.Ganho(D("3500.00"), "Salário", HOJE)),
        ("ganho 3500 salário", c.Ganho(D("3500.00"), "Salário", HOJE)),
        ("resumo mês", c.ResumoMes()),
        ("débito de dez e vinte no mercado", c.Debito(D("10.20"), "Mercado", HOJE)),
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


def _um(**campos: object) -> dict[str, object]:
    return {"lancamentos": [_item(**campos)], "duvida": None}


def _item(**campos: object) -> dict[str, object]:
    return {
        "tipo": "debito",
        "valor": "10,00",
        "parcelas": None,
        "descricao": "x",
        "data": None,
    } | campos


async def test_llm_usada_quando_heuristica_falha() -> None:
    llm = LLMFalsa(_um(valor="32,90", descricao="uber"))
    r = await interpretar_voz("aquela corrida custou trinta e dois e noventa", HOJE, llm=llm)
    assert r.comando == c.Debito(D("32.90"), "Uber", HOJE)
    assert r.via_llm and r.lote == ()
    assert "Hoje é 2026-03-20 (sexta)" in llm.chamadas[0]


async def test_local_primeiro_llm_nao_e_chamada() -> None:
    llm = LLMFalsa(_um(valor="999,00"))
    r = await interpretar_voz("gastei cinquenta reais na padaria", HOJE, llm=llm)
    assert r.comando == c.Debito(D("50.00"), "Padaria", HOJE) and not r.via_llm
    assert llm.chamadas == []


async def test_varios_lancamentos_no_mesmo_audio() -> None:
    falado = (
        "registra pra mim um débito no valor de 20 reais de cerveja "
        "e um débito de 15 reais de petisco"
    )
    resposta = {
        "lancamentos": [
            _item(valor="20,00", descricao="cerveja"),
            _item(valor="15,00", descricao="petisco"),
            _item(tipo="credito", valor="300,00", parcelas=3, descricao="tênis", data="2026-03-25"),
        ],
        "duvida": None,
    }
    r = await interpretar_voz(falado, HOJE, llm=LLMFalsa(resposta))
    assert r.comando is None and r.pergunta is None and r.via_llm
    assert r.lote == (
        c.Debito(D("20.00"), "Cerveja", HOJE),
        c.Debito(D("15.00"), "Petisco", HOJE),
        c.Credito(D("300.00"), 3, "Tênis", date(2026, 3, 25)),
    )
    # Sem LLM, o mesmo áudio continua virando pergunta.
    assert "mais de um valor" in ((await interpretar_voz(falado, HOJE)).pergunta or "")


async def test_data_por_extenso_e_centavos_via_llm() -> None:
    llm = LLMFalsa(_um(tipo="ganho", valor="4.321,50", descricao="salário", data="2026-11-15"))
    r = await interpretar_voz(
        "lançar receita no dia 15 de novembro no valor de R$ 4.321 e 50 centavos", HOJE, llm=llm
    )
    assert r.comando == c.Ganho(D("4321.50"), "Salário", date(2026, 11, 15))


async def test_local_suspeito_pede_segunda_opiniao() -> None:
    # O parser entende um débito só, com o segundo lançamento engolido na descrição.
    falado = "débito 20 cerveja débito 15 petisco"
    local = await interpretar_voz(falado, HOJE)
    assert local.comando == c.Debito(D("20.00"), "Cerveja débito 15 petisco", HOJE)
    dois = {
        "lancamentos": [
            _item(valor="20", descricao="cerveja"),
            _item(valor="15", descricao="petisco"),
        ],
        "duvida": None,
    }
    r = await interpretar_voz(falado, HOJE, llm=LLMFalsa(dois))
    assert [x.descricao for x in r.lote] == ["Cerveja", "Petisco"]
    # LLM fora do ar, sem proposta ou com proposta inválida: vale o resultado local.
    for llm in (
        LLMFalsa(erro=ErroLLM("fora do ar")),
        LLMFalsa(None),
        LLMFalsa({"lancamentos": [], "duvida": "não entendi"}),
        LLMFalsa({"lancamentos": [_item(valor="10.50")], "duvida": None}),
    ):
        assert (await interpretar_voz(falado, HOJE, llm=llm)).comando == local.comando


async def test_proposta_invalida_da_llm_vira_pergunta() -> None:
    for resposta in (
        _um(valor="10.50"),
        _um(tipo="transferencia"),
        _um(data="2027-13-01"),
        _um(tipo="credito", parcelas=None),
        {"lancamentos": [{"valor": "10"}], "duvida": None},
        # Um item inválido derruba o lote inteiro.
        {"lancamentos": [_item(), _item(valor="abc")], "duvida": None},
        {"lancamentos": [_item()] * 11, "duvida": None},
        {"lancamentos": "nada", "duvida": None},
        {"tipo": "debito", "valor": "10", "descricao": "x"},
    ):
        r = await interpretar_voz("blá blá", HOJE, llm=LLMFalsa(resposta))
        assert r.comando is None and r.lote == () and r.pergunta


async def test_duvida_da_llm() -> None:
    # Sem pergunta local melhor, a dúvida da LLM não substitui a pergunta determinística...
    llm = LLMFalsa({"lancamentos": [], "duvida": "  Qual foi o\n valor da compra?  "})
    r = await interpretar_voz("blá blá", HOJE, llm=llm)
    assert r.comando is None and r.pergunta
    # ...mas é ela que aparece quando a etapa local não tem pergunta própria.
    from financas.interpretation.interpretador import interpretar_com_llm

    assert await interpretar_com_llm("blá", HOJE, llm) == ((), "Qual foi o valor da compra?")


async def test_falha_ou_timeout_da_llm() -> None:
    r = await interpretar_voz("blá", HOJE, llm=LLMFalsa(erro=ErroLLM("fora do ar")))
    assert r.comando is None and r.pergunta

    class Lenta(LLMFalsa):
        async def completar_json(self, sistema: str, usuario: str, esquema):  # type: ignore[no-untyped-def]
            import asyncio

            await asyncio.sleep(1)

    r = await interpretar_voz("blá", HOJE, llm=Lenta(), timeout_llm=0.01)
    assert r.comando is None and r.pergunta


async def test_ambiguidade_parcela_nao_vai_para_llm() -> None:
    llm = LLMFalsa(_um(tipo="credito", valor="1000", parcelas=10, descricao="loja"))
    r = await interpretar_voz("dez parcelas de cem na loja", HOJE, llm=llm)
    assert r.comando is None
    assert llm.chamadas == []
