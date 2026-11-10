"""Fluxos do bot com mensagens simuladas (sem rede)."""

from __future__ import annotations

import asyncio
import logging
from datetime import date
from decimal import Decimal
from itertools import count

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from financas.bot.processador import Mensagem, Processador, Resposta
from financas.db.models import ConfirmacaoPendente, Lancamento, UpdateProcessado
from financas.domain.calendario import Mes
from financas.domain.erros import OperacaoInvalida
from financas.integrations.llm.base import LLMFalsa
from financas.integrations.stt.base import ErroTranscricao
from financas.integrations.stt.falso import TranscricaoFalsa
from financas.services import lancamentos as svc
from financas.services import relatorios
from financas.services.integridade import verificar_integridade
from tests import factories as f

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("limpar_banco")]

HOJE = date(2026, 3, 20)
GRUPO = -100123
ANA, BIA, ESTRANHO = 111, 222, 999
_ids = count(1)


@pytest.fixture
async def bot(fabrica: async_sessionmaker[AsyncSession]) -> Processador:
    async with fabrica() as s, s.begin():
        await f.membro(s, "Ana", telegram_id=ANA)
        await f.membro(s, "Bia", telegram_id=BIA)
        await f.membro(s, "Inativa", telegram_id=333, ativo=False)
    return Processador(
        fabrica,
        f.relogio(HOJE),
        chats_permitidos={GRUPO},
        bot_username="TioBot",
        stt=TranscricaoFalsa(),
    )


def msg(
    texto: str | None,
    user: int = ANA,
    chat: int = GRUPO,
    *,
    mid: int | None = None,
    editada: bool = False,
) -> Mensagem:
    return Mensagem(
        chat_id=chat,
        chat_privado=chat > 0,
        message_id=mid or next(_ids),
        user_id=user,
        texto=texto,
        update_id=next(_ids),
        editada=editada,
    )


async def contar(fabrica: async_sessionmaker[AsyncSession], modelo: type) -> int:
    async with fabrica() as s:
        return int(await s.scalar(select(func.count()).select_from(modelo)) or 0)


async def test_debito_e_reenvio_idempotente(
    bot: Processador, fabrica: async_sessionmaker[AsyncSession]
) -> None:
    m = msg("débito 10,50 compras de mercado")
    r1 = await bot.texto(m)
    assert r1 is not None and "Débito registrado" in r1.textos[0]
    assert "R$ 10,50" in r1.textos[0]
    r2 = await bot.texto(m)
    assert r2 == r1
    assert await contar(fabrica, Lancamento) == 1
    assert await contar(fabrica, UpdateProcessado) == 1


async def test_reenvio_concorrente_nao_duplica(
    bot: Processador, fabrica: async_sessionmaker[AsyncSession]
) -> None:
    m = msg("ganho 100 salário")
    respostas = await asyncio.gather(*(bot.texto(m) for _ in range(4)))
    assert len({r.textos[0] for r in respostas if r}) == 1
    assert await contar(fabrica, Lancamento) == 1


async def test_credito_mostra_cronograma(bot: Processador) -> None:
    r = await bot.texto(msg("crédito 1000,00 10 mercado livre"))
    assert r is not None
    t = r.textos[0]
    assert "Total: R$ 1.000,00 em 10x de R$ 100,00" in t
    assert "1/10  abr/2026" in t and "10/10  jan/2027" in t
    assert "código" in t


async def test_nao_cadastrado_ignorado_sem_logar_conteudo(
    bot: Processador, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    assert await bot.texto(msg("débito 10 segredo-xyz", user=ESTRANHO)) is None
    assert await bot.texto(msg("débito 10 segredo-xyz", user=ESTRANHO, chat=ESTRANHO)) is None
    assert await bot.texto(msg("débito 10 segredo-xyz", user=333)) is None  # inativo
    assert await bot.texto(msg("débito 10 segredo-xyz", chat=-555)) is None  # chat não permitido
    assert "segredo-xyz" not in caplog.text
    r = await bot.texto(msg("/start", user=ESTRANHO, chat=ESTRANHO))
    assert r is not None and "não está autorizado" in r.textos[0]
    assert await bot.texto(msg("/start", user=ESTRANHO)) is None  # no grupo: silêncio


async def test_start_e_ajuda(bot: Processador) -> None:
    r = await bot.texto(msg("/start@TioBot", chat=ANA))
    assert r is not None and "vinculado a um membro ativo" in r.textos[0]
    r = await bot.texto(msg("ajuda"))
    assert r is not None and "crédito <valor TOTAL>" in r.textos[0]


async def test_nao_reconhecido(bot: Processador) -> None:
    assert await bot.texto(msg("bom dia, família")) is None
    r = await bot.texto(msg("bom dia", chat=ANA))
    assert r is not None and "ajuda" in r.textos[0]


async def test_erro_de_sintaxe(bot: Processador) -> None:
    r = await bot.texto(msg("débito 10.50 mercado"))
    assert r is not None and "Você quis dizer 10,50?" in r.textos[0]


async def test_mensagem_editada(
    bot: Processador, fabrica: async_sessionmaker[AsyncSession]
) -> None:
    r = await bot.texto(msg("débito 99 mercado", editada=True))
    assert r is not None and "não altera lançamentos" in r.textos[0]
    assert await bot.texto(msg("oi gente", editada=True)) is None
    assert await contar(fabrica, Lancamento) == 0


async def _confirmar(
    bot: Processador, r: Resposta, user: int = ANA, chat: int = GRUPO, sim: bool = True
):  # type: ignore[no-untyped-def]
    botao = r.botoes[0 if sim else 1]
    return await bot.botao(user, chat, botao.dados)


async def test_cancelar_com_confirmacao(
    bot: Processador, fabrica: async_sessionmaker[AsyncSession]
) -> None:
    await bot.texto(msg("crédito 300 3 geladeira"))
    async with fabrica() as s:
        lanc_id = await s.scalar(select(Lancamento.id))
    r = await bot.texto(msg(f"cancelar {lanc_id} duplicado", user=BIA))
    assert r is not None and "só pode alterar" in r.textos[0]
    r = await bot.texto(msg(f"cancelar {lanc_id} duplicado"))
    assert r is not None and len(r.botoes) == 2 and "3 parcelas" in r.textos[0]
    outro = await _confirmar(bot, r, user=BIA)
    assert outro.alerta and "Somente quem enviou" in outro.texto
    ok = await _confirmar(bot, r)
    assert ok.editar_mensagem and "cancelado e todas as suas parcelas" in ok.texto
    de_novo = await _confirmar(bot, r)
    assert de_novo.alerta and "já foi respondida" in de_novo.texto
    async with fabrica() as s:
        assert await s.scalar(select(Lancamento.status)) == "CANCELADO"
    r = await bot.texto(msg(f"cancelar {lanc_id}"))
    assert r is not None and "já está cancelado" in r.textos[0]


async def test_manter_ao_cancelar(
    bot: Processador, fabrica: async_sessionmaker[AsyncSession]
) -> None:
    await bot.texto(msg("débito 5 café"))
    r = await bot.texto(msg("desfazer"))
    assert r is not None and r.botoes
    nao = await _confirmar(bot, r, sim=False)
    assert "mantido" in nao.texto
    async with fabrica() as s:
        assert await s.scalar(select(Lancamento.status)) == "ATIVO"


async def test_desfazer_janela(bot: Processador) -> None:
    r = await bot.texto(msg("desfazer"))
    assert r is not None and "Nenhum lançamento" in r.textos[0]
    await bot.texto(msg("débito 5 café"))
    bot.clock.avancar(minutes=11)  # type: ignore[attr-defined]
    r = await bot.texto(msg("desfazer"))
    assert r is not None and "Nenhum lançamento" in r.textos[0]


async def test_confirmacao_expirada(bot: Processador) -> None:
    await bot.texto(msg("débito 5 café"))
    r = await bot.texto(msg("desfazer"))
    assert r is not None
    bot.clock.avancar(minutes=5, seconds=1)  # type: ignore[attr-defined]
    exp = await _confirmar(bot, r)
    assert "expirada" in exp.texto


async def test_ultimos(bot: Processador) -> None:
    await bot.texto(msg("débito 5 café"))
    await bot.texto(msg("débito 7 pão", user=BIA))
    r = await bot.texto(msg("últimos"))
    assert r is not None and "café" in r.textos[0] and "pão" not in r.textos[0]


async def test_resumo_mes_igual_ao_servico(
    bot: Processador, fabrica: async_sessionmaker[AsyncSession]
) -> None:
    await bot.texto(msg("ganho 3500 salário 05/03"))
    await bot.texto(msg("débito 150 mercado", user=BIA))
    await bot.texto(msg("crédito 300 3 geladeira 10/02"))
    await bot.texto(msg("crédito 1000 10x tv"))
    r = await bot.texto(msg("resumo mês"))
    assert r is not None
    texto = "\n".join(r.textos)
    async with fabrica() as s:
        o = (await relatorios.resumo_mensal(s, Mes(2026, 3), HOJE)).orcamento
    assert o.saldo == Decimal("3250.00")
    assert "R$ 3.250,00" in texto
    assert "Orçamento diário sugerido: R$ 270,83" in texto
    assert "Compras no crédito feitas neste mês: R$ 1.000,00" in texto
    assert "Dias restantes (incluindo hoje): 12" in texto


async def test_resumos_ano_e_credito(bot: Processador) -> None:
    await bot.texto(msg("crédito 1000 10x tv"))
    r = await bot.texto(msg("resumo ano"))
    assert r is not None and "não é saldo bancário" in r.textos[-1]
    assert sum(t.count("/2026\n") for t in r.textos) == 12
    r = await bot.texto(msg("resumo crédito"))
    assert r is not None and "Total pendente: R$ 1.000,00" in r.textos[-1]


async def test_resumo_credito_paginado(bot: Processador) -> None:
    for i in range(40):
        await bot.texto(msg(f"crédito 4800 48x compra número {i} com descrição longa"))
    r = await bot.texto(msg("resumo crédito"))
    assert r is not None and len(r.textos) > 1
    assert all(len(t) <= 4096 for t in r.textos)
    # nenhum bloco de mês foi partido entre mensagens
    for t in r.textos[1:]:
        assert t.startswith(
            (
                "abr/",
                "mai/",
                "jun/",
                "jul/",
                "ago/",
                "set/",
                "out/",
                "nov/",
                "dez/",
                "jan/",
                "fev/",
                "mar/",
                "Total",
            )
        )


# ------------------------------------------------------------------ voz


async def test_voz_exige_confirmacao(
    bot: Processador, fabrica: async_sessionmaker[AsyncSession]
) -> None:
    bot.stt = TranscricaoFalsa("Registra um débito de dez reais e cinquenta no mercado")
    m = msg(None)
    r = await bot.voz(m, b"ogg")
    assert r is not None and len(r.botoes) == 2
    assert "Débito R$ 10,50 · mercado" in r.textos[0]
    assert await contar(fabrica, Lancamento) == 0
    # reenvio do mesmo áudio: mesma resposta, sem nova confirmação nem nova transcrição
    assert await bot.voz(m, b"ogg") == r
    assert bot.stt.chamadas == 1  # type: ignore[attr-defined]
    assert await contar(fabrica, ConfirmacaoPendente) == 1
    ok = await _confirmar(bot, r)
    assert "Débito registrado" in ok.texto
    async with fabrica() as s:
        assert await s.scalar(select(Lancamento.origem)) == "TELEGRAM_VOZ"
    assert (await _confirmar(bot, r)).alerta
    assert await contar(fabrica, Lancamento) == 1


DOIS = {
    "lancamentos": [
        {
            "tipo": "debito",
            "valor": "20,00",
            "parcelas": None,
            "descricao": "cerveja",
            "data": None,
        },
        {"tipo": "credito", "valor": "300,00", "parcelas": 3, "descricao": "tênis", "data": None},
    ],
    "duvida": None,
}
FALA_DUPLA = "um débito de 20 reais de cerveja e um crédito de 300 em 3 vezes de tênis"


async def test_voz_com_varios_lancamentos(
    bot: Processador, fabrica: async_sessionmaker[AsyncSession], caplog: pytest.LogCaptureFixture
) -> None:
    bot.stt = TranscricaoFalsa(FALA_DUPLA)
    bot.llm = LLMFalsa(DOIS)
    with caplog.at_level(logging.INFO):
        r = await bot.voz(msg(None), b"ogg")
    assert r is not None and [b.rotulo for b in r.botoes] == ["✅ Confirmar todos", "❌ Cancelar"]
    texto = r.textos[0]
    assert "Entendi 2 lançamentos:" in texto and "Confirma o registro de todos?" in texto
    assert "1. Débito R$ 20,00 · cerveja · 20/03/2026" in texto
    assert "2. Crédito R$ 300,00 em 3x · tênis · compra em 20/03/2026" in texto
    voz = next(x for x in caplog.records if x.message == "voz_interpretada").__dict__
    assert voz["lancamentos_no_lote"] == 2 and voz["via_llm"] and voz["comando"] is None
    assert await contar(fabrica, Lancamento) == 0
    assert await contar(fabrica, ConfirmacaoPendente) == 1

    ok = await _confirmar(bot, r)
    assert "2 lançamentos registrados" in ok.texto
    assert "Débito R$ 20,00 · cerveja" in ok.texto and "Crédito R$ 300,00 em 3x · tênis" in ok.texto
    async with fabrica() as s:
        lancs = list((await s.execute(select(Lancamento).order_by(Lancamento.id))).scalars())
        assert [(x.tipo, x.valor_total, x.origem) for x in lancs] == [
            ("DEBITO", Decimal("20.00"), "TELEGRAM_VOZ"),
            ("CREDITO", Decimal("300.00"), "TELEGRAM_VOZ"),
        ]
        conf = (await s.execute(select(ConfirmacaoPendente))).scalar_one()
        assert conf.lancamento_id == lancs[0].id and conf.status == "EXECUTADA"
        assert await verificar_integridade(s) == []
    assert (await _confirmar(bot, r)).alerta  # segundo clique não duplica
    assert await contar(fabrica, Lancamento) == 2


async def test_lote_de_voz_cancelado_ou_invalido(
    bot: Processador, fabrica: async_sessionmaker[AsyncSession]
) -> None:
    bot.stt = TranscricaoFalsa(FALA_DUPLA)
    bot.llm = LLMFalsa(DOIS)
    r = await bot.voz(msg(None), b"ogg")
    assert r is not None
    assert "Nada foi registrado" in (await _confirmar(bot, r, sim=False)).texto
    assert await contar(fabrica, Lancamento) == 0

    # Se um item falhar ao gravar, nenhum entra.
    r = await bot.voz(msg(None), b"ogg")
    assert r is not None
    original = svc.criar
    chamadas = 0

    async def falha_no_segundo(*a: object, **k: object):  # type: ignore[no-untyped-def]
        nonlocal chamadas
        chamadas += 1
        if chamadas == 2:
            raise OperacaoInvalida("falhou")
        return await original(*a, **k)  # type: ignore[arg-type]

    svc.criar = falha_no_segundo  # type: ignore[assignment]
    try:
        falha = await _confirmar(bot, r)
    finally:
        svc.criar = original
    assert "Não foi possível concluir" in falha.texto
    assert await contar(fabrica, Lancamento) == 0


async def test_voz_cancelada_nao_registra(
    bot: Processador, fabrica: async_sessionmaker[AsyncSession]
) -> None:
    bot.stt = TranscricaoFalsa("ganho de três mil de salário")
    r = await bot.voz(msg(None), b"ogg")
    assert r is not None
    nao = await _confirmar(bot, r, sim=False)
    assert "Nada foi registrado" in nao.texto
    assert await contar(fabrica, Lancamento) == 0


async def test_voz_ambigua_pergunta(
    bot: Processador, fabrica: async_sessionmaker[AsyncSession]
) -> None:
    bot.stt = TranscricaoFalsa("dez parcelas de cem na loja")
    r = await bot.voz(msg(None), b"ogg")
    assert r is not None and not r.botoes and "cada parcela ou o total" in r.textos[0]
    assert await contar(fabrica, ConfirmacaoPendente) == 0


async def test_voz_falha_de_transcricao(bot: Processador) -> None:
    bot.stt = TranscricaoFalsa(erro=ErroTranscricao("x"))
    r = await bot.voz(msg(None), b"ogg")
    assert r is not None and "Não consegui transcrever" in r.textos[0]


async def test_voz_consulta_executa_direto(bot: Processador) -> None:
    bot.stt = TranscricaoFalsa("resumo crédito")
    r = await bot.voz(msg(None), b"ogg")
    assert r is not None and "Nenhuma parcela pendente" in r.textos[0]


async def test_voz_de_nao_cadastrado_ignorada(bot: Processador) -> None:
    bot.stt = TranscricaoFalsa("débito de dez reais no mercado")
    assert await bot.voz(msg(None, user=ESTRANHO), b"ogg") is None
    assert bot.stt.chamadas == 0  # type: ignore[attr-defined]


async def test_botao_invalido_ou_de_estranho(bot: Processador) -> None:
    assert (await bot.botao(ANA, GRUPO, "lixo")).alerta
    assert (await bot.botao(ESTRANHO, GRUPO, "c:00000000-0000-0000-0000-000000000000:s")).alerta


def _eventos(caplog: pytest.LogCaptureFixture) -> dict[str, logging.LogRecord]:
    return {r.getMessage(): r for r in caplog.records if r.name == "financas.bot.processador"}


async def test_logs_descrevem_operacoes(bot: Processador, caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    await bot.texto(msg("crédito 300 3 geladeira"))
    ev = _eventos(caplog)
    cmd = ev["comando"].__dict__
    assert cmd["comando"] == "Credito" and cmd["valor_total"] == "300.00"
    assert cmd["parcelas"] == 3 and cmd["membro"] == "Ana" and cmd["chat"] == GRUPO
    reg = ev["lancamento_registrado"].__dict__
    assert reg["tipo"] == "CREDITO" and reg["valor"] == "300.00"
    assert reg["descricao"] == "geladeira" and reg["origem"] == "TELEGRAM_TEXTO"
    lanc_id = reg["lancamento_id"]

    r = await bot.texto(msg(f"cancelar {lanc_id} duplicado"))
    assert r is not None
    pedido = _eventos(caplog)["cancelamento_solicitado"].__dict__
    assert pedido["lancamento_id"] == lanc_id and pedido["motivo"] == "duplicado"
    await _confirmar(bot, r)
    cancelado = _eventos(caplog)["lancamento_cancelado"].__dict__
    assert cancelado["lancamento_id"] == lanc_id and cancelado["membro"] == "Ana"

    await bot.texto(msg(f"cancelar {lanc_id}"))
    recusa = _eventos(caplog)["comando_recusado"].__dict__
    assert "já está cancelado" in recusa["erro"]


async def test_logs_de_voz_trazem_transcricao(
    bot: Processador, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    bot.stt = TranscricaoFalsa("débito de dez reais no mercado")
    r = await bot.voz(msg(None, chat=ANA), b"ogg")
    assert r is not None
    ev = _eventos(caplog)
    voz = ev["voz_interpretada"].__dict__
    assert voz["transcricao"] == "débito de dez reais no mercado"
    assert voz["comando"] == "Debito" and voz["chat"] == "privado"
    assert ev["confirmacao_solicitada"].__dict__["descricao"] == "mercado"
    await _confirmar(bot, r, chat=ANA)
    assert _eventos(caplog)["lancamento_registrado"].__dict__["origem"] == "TELEGRAM_VOZ"
