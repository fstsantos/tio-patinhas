import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import httpx
import pytest

from financas.domain.calendario import Mes, SituacaoMes
from financas.domain.orcamento import calcular_orcamento
from financas.integrations.llm.anthropic import LLMAnthropic
from financas.integrations.llm.base import ESQUEMA, SISTEMA, ErroLLM
from financas.integrations.llm.contexto_agregado import contexto_mensal, rotulos_membros
from financas.integrations.llm.openai_compativel import LLMOpenAICompativel
from financas.interpretation import comandos as c
from financas.interpretation.interpretador import interpretar_voz
from financas.services.relatorios import Grupo, ItemLancamento, ResumoMensal

anthropic = pytest.importorskip("anthropic")
HOJE = date(2026, 3, 20)


class ClienteFalso:
    def __init__(self, resposta: Any = None, erro: Exception | None = None) -> None:
        self.chamadas: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._criar)
        self.resposta, self.erro = resposta, erro

    async def _criar(self, **k: Any) -> Any:
        self.chamadas.append(k)
        if self.erro:
            raise self.erro
        return self.resposta


def _resposta(dados: Any, stop: str = "end_turn") -> Any:
    return SimpleNamespace(
        stop_reason=stop,
        content=[SimpleNamespace(type="text", text=json.dumps(dados))],
        usage=SimpleNamespace(input_tokens=600, output_tokens=40),
    )


UBER = {
    "lancamentos": [
        {"tipo": "debito", "valor": "32,90", "parcelas": None, "descricao": "uber", "data": None}
    ],
    "duvida": None,
}


async def test_anthropic_proposta_validada_pelo_parser() -> None:
    cli = ClienteFalso(_resposta(UBER))
    llm = LLMAnthropic("k", cliente=cli)
    r = await interpretar_voz("aquela corrida deu trinta e dois e noventa", HOJE, llm=llm)
    assert r.comando == c.Debito(Decimal("32.90"), "Uber", HOJE) and r.via_llm
    req = cli.chamadas[0]
    assert req["model"] == "claude-haiku-5-5"
    assert req["output_config"] == {
        "effort": "low",
        "format": {"type": "json_schema", "schema": ESQUEMA},
    }
    assert req["system"] == SISTEMA
    assert "Transcrição: aquela corrida" in req["messages"][0]["content"]
    assert LLMAnthropic("k", modelo="claude-opus-5-5", cliente=cli).modelo == "claude-opus-5-5"


@pytest.mark.parametrize(
    "resposta",
    [
        _resposta({}, stop="refusal"),
        _resposta({}, stop="max_tokens"),
        _resposta([1, 2]),
        SimpleNamespace(stop_reason="end_turn", content=[], usage=None),
    ],
)
async def test_anthropic_sem_proposta(resposta: Any) -> None:
    llm = LLMAnthropic("k", cliente=ClienteFalso(resposta))
    assert await llm.completar_json("s", "u", ESQUEMA) is None


async def test_anthropic_erros_viram_erro_llm() -> None:
    httpx2 = pytest.importorskip("httpx2")
    req = httpx2.Request("POST", "https://api.invalid")
    erro = anthropic.APITimeoutError(request=req)
    with pytest.raises(ErroLLM, match="timeout"):
        await LLMAnthropic("k", cliente=ClienteFalso(erro=erro)).completar_json("s", "u", {})
    with pytest.raises(ErroLLM, match="APIConnectionError"):
        await LLMAnthropic(
            "k", cliente=ClienteFalso(erro=anthropic.APIConnectionError(request=req))
        ).completar_json("s", "u", {})
    limite = anthropic.RateLimitError(
        "limite", response=httpx2.Response(429, request=req), body=None
    )
    with pytest.raises(ErroLLM, match=r"RateLimitError \(429\)"):
        await LLMAnthropic("k", cliente=ClienteFalso(erro=limite)).completar_json("s", "u", {})
    ruim = SimpleNamespace(
        stop_reason="end_turn", content=[SimpleNamespace(type="text", text="não json")], usage=None
    )
    with pytest.raises(ErroLLM, match="JSON"):
        await LLMAnthropic("k", cliente=ClienteFalso(ruim)).completar_json("s", "u", {})


# ------------------------------------------------------- compatível com OpenAI


def _compativel(
    responder: Any, *, chave: str | None = "k", base: str = "https://llm.exemplo/v1/"
) -> tuple[LLMOpenAICompativel, list[httpx.Request]]:
    pedidos: list[httpx.Request] = []

    def tratar(req: httpx.Request) -> httpx.Response:
        pedidos.append(req)
        if isinstance(responder, Exception):
            raise responder
        return responder

    llm = LLMOpenAICompativel(
        chave, modelo="modelo-x", base_url=base, transporte=httpx.MockTransport(tratar)
    )
    return llm, pedidos


def _chat(conteudo: Any, status: int = 200) -> httpx.Response:
    corpo = {
        "choices": [{"message": {"content": conteudo}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": 600, "completion_tokens": 40},
    }
    return httpx.Response(status, json=corpo)


async def test_compativel_proposta_validada_pelo_parser() -> None:
    llm, pedidos = _compativel(_chat("```json\n" + json.dumps(UBER) + "\n```"))
    r = await interpretar_voz("aquela corrida deu trinta e dois e noventa", HOJE, llm=llm)
    assert r.comando == c.Debito(Decimal("32.90"), "Uber", HOJE) and r.via_llm
    (req,) = pedidos
    assert str(req.url) == "https://llm.exemplo/v1/chat/completions"
    assert req.headers["authorization"] == "Bearer k"
    corpo = json.loads(req.content)
    assert corpo["model"] == "modelo-x" and corpo["response_format"] == {"type": "json_object"}
    sistema, usuario = corpo["messages"]
    assert sistema["content"].startswith(SISTEMA) and '"lancamentos"' in sistema["content"]
    assert "Transcrição: aquela corrida" in usuario["content"]


async def test_compativel_sem_chave_e_sem_proposta() -> None:
    llm, pedidos = _compativel(_chat(None), chave=None)
    assert await llm.completar_json("s", "u", ESQUEMA) is None
    assert "authorization" not in pedidos[0].headers
    llm, _ = _compativel(_chat("[1, 2]"))
    assert await llm.completar_json("s", "u", ESQUEMA) is None


@pytest.mark.parametrize(
    ("responder", "trecho"),
    [
        (httpx.ReadTimeout("lento"), "timeout"),
        (httpx.ConnectError("sem rede"), "ConnectError"),
        (_chat("x", status=401), "HTTP 401"),
        (httpx.Response(200, json={"sem": "choices"}), "formato"),
        (httpx.Response(200, text="<html>"), "formato"),
        (_chat("isto não é json"), "JSON"),
    ],
)
async def test_compativel_erros_viram_erro_llm(responder: Any, trecho: str) -> None:
    llm, _ = _compativel(responder)
    with pytest.raises(ErroLLM, match=trecho):
        await llm.completar_json("s", "u", ESQUEMA)


def test_contexto_agregado_anonimiza() -> None:
    assert rotulos_membros(["Bia", "Ana", "Bia"]) == {"Ana": "Membro A", "Bia": "Membro B"}
    item = ItemLancamento(1, "DEBITO", "Farmácia", "farmacia", Decimal("50.00"), HOJE, "Ana", None)
    o = calcular_orcamento(
        Mes(2026, 3), HOJE, receitas=Decimal("100"), debitos=Decimal("50.00"), parcelas=Decimal("0")
    )
    r = ResumoMensal(o, [], [item], [], [], [], [], [Grupo("farmacia", Decimal("50.00"), 1)])
    ctx = contexto_mensal(r)
    texto = json.dumps(ctx, ensure_ascii=False)
    assert "Ana" not in texto and "Membro A" in texto
    assert ctx["fatos_observados"]["saldo"] == "50.00"
    assert ctx["fatos_observados"]["situacao"] == SituacaoMes.CORRENTE.value
