"""Handlers do python-telegram-bot com Updates desserializados de JSON."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from telegram import CallbackQuery, Message, Update

from financas.bot import handlers
from financas.bot.processador import Processador
from financas.integrations.stt.falso import TranscricaoFalsa
from tests import factories as f
from tests.integration.test_bot_fluxos import GRUPO, HOJE

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("limpar_banco")]


def _update(
    uid: int,
    *,
    texto: str | None = None,
    editada: bool = False,
    voz: dict[str, Any] | None = None,
    user: int = 111,
    chat: int = GRUPO,
    msg_id: int = 10,
) -> Update:
    msg: dict[str, Any] = {
        "message_id": msg_id,
        "date": 1773000000,
        "chat": {"id": chat, "type": "private" if chat > 0 else "supergroup", "title": "F"},
        "from": {"id": user, "is_bot": False, "first_name": "Ana"},
    }
    if texto is not None:
        msg["text"] = texto
    if voz is not None:
        msg["voice"] = voz
    if editada:
        msg["edit_date"] = 1773000100
    return Update.de_json(
        {"update_id": uid, ("edited_message" if editada else "message"): msg}, None
    )


@pytest.fixture
async def contexto(fabrica: async_sessionmaker[AsyncSession]) -> SimpleNamespace:
    async with fabrica() as s, s.begin():
        await f.membro(s, "Ana", telegram_id=111)
    proc = Processador(
        fabrica,
        f.relogio(HOJE),
        chats_permitidos={GRUPO},
        stt=TranscricaoFalsa("débito de vinte reais na padaria"),
    )
    return SimpleNamespace(
        application=SimpleNamespace(
            bot_data={handlers.CHAVE_PROCESSADOR: proc, handlers.CHAVE_DURACAO_MAXIMA: 60}
        )
    )


@pytest.fixture
def enviadas(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, Any]]:
    saida: list[tuple[str, Any]] = []

    async def reply_text(self: Message, texto: str, reply_markup: Any = None, **_k: Any) -> None:
        saida.append((texto, reply_markup))

    monkeypatch.setattr(Message, "reply_text", reply_text)
    return saida


def test_mensagem_de_update() -> None:
    m = handlers.mensagem_de_update(_update(5, texto="oi", editada=True, chat=111))
    assert m is not None and m.editada and m.chat_privado and m.texto == "oi"
    assert m.update_id == 5 and m.user_id == 111


async def test_on_texto_responde(
    contexto: SimpleNamespace, enviadas: list[tuple[str, Any]]
) -> None:
    await handlers.on_texto(_update(1, texto="débito 10 café"), contexto)  # type: ignore[arg-type]
    assert "Débito registrado" in enviadas[0][0]
    await handlers.on_texto(_update(2, texto="bom dia", msg_id=11), contexto)  # type: ignore[arg-type]
    assert len(enviadas) == 1  # grupo: mensagem comum ignorada


async def test_fluxo_de_voz_com_botao(
    contexto: SimpleNamespace, enviadas: list[tuple[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    class Arquivo:
        async def download_as_bytearray(self) -> bytearray:
            return bytearray(b"OggS...")

    async def get_file(self: Any, *_a: Any, **_k: Any) -> Arquivo:
        return Arquivo()

    from telegram import Voice

    monkeypatch.setattr(Voice, "get_file", get_file)
    voz = {"file_id": "x", "file_unique_id": "y", "duration": 3}
    await handlers.on_voz(_update(3, voz=voz), contexto)  # type: ignore[arg-type]
    texto, teclado = enviadas[0]
    assert "Débito R$ 20,00 · Padaria" in texto
    dados = teclado.inline_keyboard[0][0].callback_data

    respostas: list[tuple[str, dict[str, Any]]] = []

    async def answer(self: CallbackQuery, texto: str | None = None, **k: Any) -> None:
        respostas.append(("answer", {"texto": texto, **k}))

    async def edit_message_text(self: CallbackQuery, texto: str, **_k: Any) -> None:
        respostas.append(("edit", {"texto": texto}))

    monkeypatch.setattr(CallbackQuery, "answer", answer)
    monkeypatch.setattr(CallbackQuery, "edit_message_text", edit_message_text)
    upd = Update.de_json(
        {
            "update_id": 4,
            "callback_query": {
                "id": "q",
                "chat_instance": "c",
                "data": dados,
                "from": {"id": 111, "is_bot": False, "first_name": "Ana"},
                "message": {
                    "message_id": 12,
                    "date": 1773000000,
                    "chat": {"id": GRUPO, "type": "supergroup", "title": "F"},
                },
            },
        },
        None,
    )
    await handlers.on_botao(upd, contexto)  # type: ignore[arg-type]
    assert respostas[-1][0] == "edit" and "Débito registrado" in respostas[-1][1]["texto"]


async def test_voz_longa_rejeitada(
    contexto: SimpleNamespace, enviadas: list[tuple[str, Any]]
) -> None:
    voz = {"file_id": "x", "file_unique_id": "y", "duration": 600}
    await handlers.on_voz(_update(5, voz=voz), contexto)  # type: ignore[arg-type]
    assert "muito longo" in enviadas[0][0]


async def test_voz_de_estranho_nem_baixa(
    contexto: SimpleNamespace, enviadas: list[tuple[str, Any]]
) -> None:
    voz = {"file_id": "x", "file_unique_id": "y", "duration": 3}
    await handlers.on_voz(_update(6, voz=voz, user=999), contexto)  # type: ignore[arg-type]
    assert enviadas == []


def test_registrar_handlers() -> None:
    from telegram.ext import ApplicationBuilder

    app = ApplicationBuilder().token("123:abc").build()
    handlers.registrar(app)
    assert len(app.handlers[0]) == 3
