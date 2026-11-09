"""Adaptadores finos entre python-telegram-bot e o ``Processador``."""

from __future__ import annotations

import logging
from typing import Any

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Message, Update
from telegram.constants import ChatType
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from financas.bot.processador import Mensagem, Processador, Resposta

log = logging.getLogger(__name__)

CHAVE_PROCESSADOR = "processador"
CHAVE_DURACAO_MAXIMA = "voz_duracao_maxima"


def mensagem_de_update(update: Update) -> Mensagem | None:
    """Converte o Update em ``Mensagem``; None se não houver remetente/chat."""
    editada = update.edited_message is not None
    msg = update.edited_message or update.message
    if msg is None or msg.from_user is None or msg.from_user.is_bot:
        return None
    return Mensagem(
        chat_id=msg.chat.id,
        chat_privado=msg.chat.type == ChatType.PRIVATE,
        message_id=msg.message_id,
        user_id=msg.from_user.id,
        texto=msg.text,
        update_id=update.update_id,
        editada=editada,
    )


def _teclado(resposta: Resposta) -> InlineKeyboardMarkup | None:
    if not resposta.botoes:
        return None
    return InlineKeyboardMarkup(
        [[InlineKeyboardButton(b.rotulo, callback_data=b.dados) for b in resposta.botoes]]
    )


async def _enviar(msg: Message, resposta: Resposta) -> None:
    ultimo = len(resposta.textos) - 1
    for i, texto in enumerate(resposta.textos):
        # O teclado vai na última parte, para ficar abaixo do conteúdo.
        await msg.reply_text(
            texto, reply_markup=_teclado(resposta) if i == ultimo else None, do_quote=i == 0
        )


def _processador(context: ContextTypes.DEFAULT_TYPE) -> Processador:
    proc: Processador = context.application.bot_data[CHAVE_PROCESSADOR]
    return proc


async def on_texto(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    m = mensagem_de_update(update)
    alvo = update.effective_message
    if m is None or alvo is None:
        return
    resposta = await _processador(context).texto(m)
    if resposta is not None:
        await _enviar(alvo, resposta)


async def on_voz(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    m = mensagem_de_update(update)
    alvo = update.message
    if m is None or alvo is None:
        return
    midia = alvo.voice or alvo.audio
    if midia is None:
        return
    proc = _processador(context)
    if not await _autorizado(proc, m):
        return  # não autorizado: ignora sem baixar o áudio
    limite = int(context.application.bot_data.get(CHAVE_DURACAO_MAXIMA, 120))
    if (midia.duration or 0) > limite:
        await alvo.reply_text(f"Áudio muito longo (máx. {limite} s). Envie um comando curto.")
        return
    arquivo = await midia.get_file()
    audio = bytes(await arquivo.download_as_bytearray())
    resposta = await proc.voz(m, audio, "ogg")
    if resposta is not None:
        await _enviar(alvo, resposta)


async def _autorizado(proc: Processador, m: Mensagem) -> bool:
    return proc.chat_permitido(m) and await proc.membro_ativo(m.user_id) is not None


async def on_botao(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    q = update.callback_query
    if q is None or q.data is None or q.message is None:
        return
    r = await _processador(context).botao(q.from_user.id, q.message.chat.id, q.data)
    if r.alerta:
        await q.answer(r.texto, show_alert=True)
        return
    await q.answer()
    if r.editar_mensagem:
        await q.edit_message_text(r.texto)


async def on_erro(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    # Sem conteúdo da mensagem no log: apenas o tipo do erro e o id do update.
    update_id = update.update_id if isinstance(update, Update) else None
    log.error("erro_no_handler", exc_info=context.error, extra={"update_id": update_id})


def registrar(app: Application[Any, Any, Any, Any, Any, Any]) -> None:
    app.add_handler(MessageHandler(filters.VOICE | filters.AUDIO, on_voz))
    app.add_handler(MessageHandler(filters.TEXT, on_texto))
    app.add_handler(CallbackQueryHandler(on_botao))
    app.add_error_handler(on_erro)
