import json
from datetime import date
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

import pytest

from financas.domain.calendario import Mes, SituacaoMes
from financas.domain.orcamento import calcular_orcamento
from financas.integrations.llm.anthropic import ESQUEMA, LLMAnthropic
from financas.integrations.llm.base import ErroLLM
from financas.integrations.llm.contexto_agregado import contexto_mensal, rotulos_membros
from financas.interpretation import comandos as c
from financas.interpretation.interpretador import interpretar_voz
from financas.services.relatorios import Grupo, ItemLancamento, ResumoMensal

anthropic = pytest.importorskip("anthropic")
HOJE = date(2026, 3, 20)


class ClienteFalso:
    def __init__(self, resposta: Any = None, erro: Exception | None = None) -> None:
        self.chamadas: list[dict[str, Any]] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._criar))
        self.resposta, self.erro = resposta, erro

    async def _criar(self, **k: Any) -> Any:
        self.chamadas.append(k)
        if self.erro:
            raise self.erro
        return self.resposta


def _resposta(dados: dict[str, Any], stop: str = "end_turn") -> Any:
    return SimpleNamespace(
        stop_reason=stop, content=[SimpleNamespace(type="text", text=json.dumps(dados))]
    )


async def test_proposta_validada_pelo_parser() -> None:
    cli = ClienteFalso(
        _resposta(
            {
                "reconhecido": True,
                "tipo": "debito",
                "valor": "32,90",
                "parcelas": None,
                "descricao": "uber",
                "data": None,
            }
        )
    )
    llm = LLMAnthropic("k", cliente=cli)
    r = await interpretar_voz("aquela corrida deu trinta e dois e noventa", HOJE, llm=llm)
    assert r.comando == c.Debito(Decimal("32.90"), "uber", HOJE) and r.via_llm
    req = cli.chamadas[0]
    assert req["model"] == "claude-opus-5-5"
    assert req["output_config"]["format"]["schema"] == ESQUEMA
    assert "Transcrição: aquela corrida" in req["messages"][0]["content"]


@pytest.mark.parametrize(
    "resposta",
    [
        _resposta(
            {
                "reconhecido": False,
                "tipo": "debito",
                "valor": "",
                "parcelas": None,
                "descricao": "",
                "data": None,
            }
        ),
        _resposta({}, stop="refusal"),
        _resposta({}, stop="max_tokens"),
    ],
)
async def test_sem_proposta(resposta: Any) -> None:
    assert await LLMAnthropic("k", cliente=ClienteFalso(resposta)).propor_comando("x", HOJE) is None


async def test_erros_viram_erro_llm() -> None:
    httpx2 = pytest.importorskip("httpx2")
    erro = anthropic.APITimeoutError(request=httpx2.Request("POST", "https://api.invalid"))
    with pytest.raises(ErroLLM, match="timeout"):
        await LLMAnthropic("k", cliente=ClienteFalso(erro=erro)).propor_comando("x", HOJE)
    ruim = SimpleNamespace(
        stop_reason="end_turn", content=[SimpleNamespace(type="text", text="não json")]
    )
    with pytest.raises(ErroLLM, match="JSON"):
        await LLMAnthropic("k", cliente=ClienteFalso(ruim)).propor_comando("x", HOJE)


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
