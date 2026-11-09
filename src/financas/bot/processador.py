"""Orquestração das mensagens do bot, independente do python-telegram-bot.

Os handlers do Telegram só convertem ``Update`` em ``Mensagem`` e enviam a
``Resposta``. Aqui ficam: filtro de chats, autenticação pelo id do Telegram,
idempotência, interpretação, confirmações e chamadas aos serviços.

Mensagens de chats não permitidos ou de remetentes não cadastrados são ignoradas
sem registrar o conteúdo em log (spec 6.3).
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from financas.bot import formatacao as fmt
from financas.bot.paginacao import dividir
from financas.clock import Clock
from financas.db.models import Membro, Origem, StatusConfirmacao, TipoConfirmacao, TipoLancamento
from financas.db.session import transacao
from financas.domain.calendario import Mes
from financas.domain.erros import ErroDominio, OperacaoInvalida
from financas.integrations.llm.base import LLMProvider
from financas.integrations.stt.base import ErroTranscricao, SpeechToText
from financas.interpretation import comandos as c
from financas.interpretation.interpretador import interpretar_voz
from financas.interpretation.parser_texto import interpretar, palavra_chave
from financas.services import autorizacao, confirmacoes, idempotencia, membros, relatorios
from financas.services import lancamentos as svc
from financas.services.confirmacoes import Desfecho
from financas.services.lancamentos import DadosLancamento

log = logging.getLogger(__name__)

PREFIXO_CONFIRMACAO = "c"
REENVIAR = "Envie de novo, por voz ou texto, com essa informação."


@dataclass(frozen=True, slots=True)
class Mensagem:
    chat_id: int
    chat_privado: bool
    message_id: int
    user_id: int
    texto: str | None = None
    update_id: int | None = None
    editada: bool = False


@dataclass(frozen=True, slots=True)
class Botao:
    rotulo: str
    dados: str


@dataclass(frozen=True, slots=True)
class Resposta:
    textos: list[str]
    botoes: list[Botao] = field(default_factory=list)

    def para_json(self) -> dict[str, Any]:
        return {"textos": self.textos, "botoes": [asdict(b) for b in self.botoes]}

    @classmethod
    def de_json(cls, dados: dict[str, Any]) -> Resposta:
        return cls(list(dados["textos"]), [Botao(**b) for b in dados.get("botoes", [])])

    @classmethod
    def blocos(cls, blocos: list[str]) -> Resposta:
        return cls(dividir(blocos))


@dataclass(frozen=True, slots=True)
class RespostaBotao:
    texto: str
    alerta: bool = False  # True: só um aviso ao usuário, sem alterar a mensagem
    editar_mensagem: bool = False


def dados_botao(conf_id: uuid.UUID, confirmar: bool) -> str:
    return f"{PREFIXO_CONFIRMACAO}:{conf_id}:{'s' if confirmar else 'n'}"


def _botoes_confirmacao(conf_id: uuid.UUID, sim: str, nao: str) -> list[Botao]:
    return [Botao(sim, dados_botao(conf_id, True)), Botao(nao, dados_botao(conf_id, False))]


def _dados_lancamento(cmd: c.Lancamento) -> DadosLancamento:
    if isinstance(cmd, c.Credito):
        return DadosLancamento(
            TipoLancamento.CREDITO, cmd.valor_total, cmd.descricao, cmd.data, cmd.parcelas
        )
    tipo = TipoLancamento.DEBITO if isinstance(cmd, c.Debito) else TipoLancamento.RECEITA
    return DadosLancamento(tipo, cmd.valor, cmd.descricao, cmd.data)


def _erro(e: ErroDominio) -> Resposta:
    texto = f"⚠️ {e.mensagem}"
    if e.sugestao:
        texto += f"\n{e.sugestao}"
    return Resposta([texto])


Acao = Callable[[AsyncSession, Membro], Awaitable[tuple[Resposta, int | None]]]


class Processador:
    def __init__(
        self,
        fabrica: async_sessionmaker[AsyncSession],
        clock: Clock,
        *,
        chats_permitidos: set[int],
        bot_username: str | None = None,
        stt: SpeechToText | None = None,
        llm: LLMProvider | None = None,
        timeout_stt: float = 120.0,
        timeout_llm: float = 15.0,
    ) -> None:
        self.fabrica = fabrica
        self.clock = clock
        self.chats_permitidos = chats_permitidos
        self.bot_username = bot_username
        self.stt = stt
        self.llm = llm
        self.timeout_stt = timeout_stt
        self.timeout_llm = timeout_llm

    # ------------------------------------------------------------ autenticação

    def chat_permitido(self, msg: Mensagem) -> bool:
        return msg.chat_privado or msg.chat_id in self.chats_permitidos

    async def membro_ativo(self, user_id: int) -> Membro | None:
        async with self.fabrica() as s:
            return await membros.ativo_por_telegram(s, user_id)

    # ------------------------------------------------------------ entradas

    async def texto(self, msg: Mensagem) -> Resposta | None:
        if not self.chat_permitido(msg) or not msg.texto:
            return None
        membro = await self.membro_ativo(msg.user_id)
        if msg.editada:
            eh_comando = palavra_chave(msg.texto, self.bot_username) is not None
            return Resposta([fmt.MENSAGEM_EDITADA]) if membro and eh_comando else None
        resultado = interpretar(msg.texto, self.clock.hoje(), self.bot_username)
        if membro is None:
            if msg.chat_privado and isinstance(resultado, c.Start):
                return Resposta([fmt.NAO_AUTORIZADO])
            return None
        if isinstance(resultado, c.NaoReconhecido):
            return Resposta([fmt.SUGESTAO_AJUDA]) if msg.chat_privado else None
        if isinstance(resultado, c.ErroComando):
            return Resposta([fmt.erro_comando(resultado)])
        log.info("comando", extra={"comando": type(resultado).__name__, "membro_id": membro.id})
        return await self._executar(msg, membro, resultado, Origem.TELEGRAM_TEXTO)

    async def voz(self, msg: Mensagem, audio: bytes, formato: str = "ogg") -> Resposta | None:
        if not self.chat_permitido(msg):
            return None
        membro = await self.membro_ativo(msg.user_id)
        if membro is None:
            return None
        async with self.fabrica() as s:
            anterior = await idempotencia.buscar(s, msg.chat_id, msg.message_id)
        if anterior is not None:
            return Resposta.de_json(anterior)
        if self.stt is None:
            return Resposta(
                ["A transcrição de voz não está configurada. Envie o comando por texto."]
            )
        try:
            transcricao = await asyncio.wait_for(
                self.stt.transcrever(audio, formato), timeout=self.timeout_stt
            )
        except (TimeoutError, ErroTranscricao) as e:
            log.warning("transcricao_falhou", extra={"erro": type(e).__name__})
            return Resposta(["Não consegui transcrever o áudio. Tente de novo ou envie por texto."])

        interp = await interpretar_voz(
            transcricao, self.clock.hoje(), llm=self.llm, timeout_llm=self.timeout_llm
        )
        ouvi = f"🎙️ Ouvi: “{transcricao.strip()}”"
        log.info(
            "voz_interpretada",
            extra={
                "membro_id": membro.id,
                "via_llm": interp.via_llm,
                "reconhecido": interp.comando is not None,
            },
        )
        cmd = interp.comando
        if cmd is None:
            pergunta = interp.pergunta or "Não entendi."
            return await self._idempotente(
                msg,
                _fixa(Resposta([f"{ouvi}\n\n❓ {pergunta}\n{REENVIAR}"])),
            )
        if isinstance(cmd, c.Debito | c.Credito | c.Ganho):
            return await self._idempotente(msg, self._acao_confirmar_voz(msg, cmd, ouvi))
        return await self._executar(msg, membro, cmd, Origem.TELEGRAM_VOZ)

    async def botao(self, user_id: int, chat_id: int, dados: str) -> RespostaBotao:
        try:
            prefixo, id_txt, escolha = dados.split(":")
            conf_id = uuid.UUID(id_txt)
            if prefixo != PREFIXO_CONFIRMACAO or escolha not in {"s", "n"}:
                raise ValueError(dados)
        except ValueError:
            return RespostaBotao("Botão inválido.", alerta=True)
        membro = await self.membro_ativo(user_id)
        if membro is None:
            return RespostaBotao("Você não está autorizado.", alerta=True)
        confirmar = escolha == "s"
        async with transacao(self.fabrica) as s:
            ator = await s.get(Membro, membro.id)
            assert ator is not None
            r = await confirmacoes.resolver(
                s, self.clock, conf_id, membro_id=ator.id, chat_id=chat_id, confirmar=confirmar
            )
            match r.desfecho:
                case Desfecho.OUTRO_MEMBRO:
                    return RespostaBotao(
                        "Somente quem enviou o comando pode responder.", alerta=True
                    )
                case Desfecho.JA_RESOLVIDA:
                    return RespostaBotao("Esta confirmação já foi respondida.", alerta=True)
                case Desfecho.EXPIRADA | Desfecho.NAO_ENCONTRADA:
                    return RespostaBotao(
                        "⌛ Confirmação expirada. Envie o comando novamente.", editar_mensagem=True
                    )
            conf = r.confirmacao
            assert conf is not None
            if not confirmar:
                texto = (
                    "Cancelamento descartado; o lançamento foi mantido."
                    if conf.tipo == TipoConfirmacao.CANCELAMENTO.value
                    else "❌ Operação cancelada. Nada foi registrado."
                )
                return RespostaBotao(texto, editar_mensagem=True)
            try:
                async with s.begin_nested():
                    texto, lancamento_id = await self._executar_confirmacao(
                        s, ator, conf.tipo, conf.payload
                    )
                    conf.lancamento_id = lancamento_id
            except ErroDominio as e:
                conf.status = StatusConfirmacao.CANCELADA.value
                return RespostaBotao(f"⚠️ Não foi possível concluir: {e}", editar_mensagem=True)
            return RespostaBotao(texto, editar_mensagem=True)

    # ------------------------------------------------------------ execução

    async def _executar(
        self, msg: Mensagem, membro: Membro, cmd: c.Comando, origem: Origem
    ) -> Resposta:
        hoje = self.clock.hoje()
        match cmd:
            case c.Debito() | c.Credito() | c.Ganho():
                return await self._idempotente(msg, self._acao_lancar(cmd, origem))
            case c.Cancelar(codigo=codigo, motivo=motivo):
                return await self._idempotente(
                    msg, self._acao_pedir_cancelamento(msg, codigo, motivo)
                )
            case c.Desfazer():
                return await self._idempotente(msg, self._acao_desfazer(msg))
            case c.Ajuda():
                return Resposta([fmt.AJUDA])
            case c.Start():
                return Resposta([fmt.start(membro.nome)])
            case c.Ultimos():
                async with self.fabrica() as s:
                    return Resposta([fmt.ultimos(await svc.ultimos(s, membro.id))])
            case c.ResumoMes(mes=mes):
                async with self.fabrica() as s:
                    r = await relatorios.resumo_mensal(s, mes or Mes.de_data(hoje), hoje)
                return Resposta.blocos(fmt.resumo_mensal(r))
            case c.ResumoAno(ano=ano):
                async with self.fabrica() as s:
                    ra = await relatorios.resumo_anual(s, ano or hoje.year, hoje)
                return Resposta.blocos(fmt.resumo_anual(ra))
            case c.ResumoCredito():
                async with self.fabrica() as s:
                    rc = await relatorios.resumo_credito(s, hoje)
                return Resposta.blocos(fmt.resumo_credito(rc))
        raise AssertionError(f"comando não tratado: {cmd!r}")  # pragma: no cover

    async def _idempotente(self, msg: Mensagem, acao: Acao) -> Resposta:
        """Executa a ação e grava (chat_id, message_id) na mesma transação."""
        for tentativa in range(2):
            try:
                async with transacao(self.fabrica) as s:
                    anterior = await idempotencia.buscar(s, msg.chat_id, msg.message_id)
                    if anterior is not None:
                        return Resposta.de_json(anterior)
                    ator = await membros.ativo_por_telegram(s, msg.user_id)
                    if ator is None:
                        return Resposta(["Você não está autorizado."])
                    try:
                        async with s.begin_nested():
                            resposta, lancamento_id = await acao(s, ator)
                    except ErroDominio as e:
                        resposta, lancamento_id = _erro(e), None
                    await idempotencia.registrar(
                        s,
                        self.clock,
                        chat_id=msg.chat_id,
                        message_id=msg.message_id,
                        update_id=msg.update_id,
                        resultado=resposta.para_json(),
                        lancamento_id=lancamento_id,
                    )
                    return resposta
            except IntegrityError:
                # Reenvio concorrente gravou primeiro; a próxima volta lê a resposta dele.
                if tentativa == 1:
                    raise
        raise AssertionError("inalcançável")  # pragma: no cover

    def _acao_lancar(self, cmd: c.Lancamento, origem: Origem) -> Acao:
        async def acao(s: AsyncSession, ator: Membro) -> tuple[Resposta, int | None]:
            r = await svc.criar(s, self.clock, ator, _dados_lancamento(cmd), origem=origem)
            return Resposta([fmt.lancamento_registrado(r.lancamento, r.parcelas)]), r.lancamento.id

        return acao

    def _acao_confirmar_voz(self, msg: Mensagem, cmd: c.Lancamento, ouvi: str) -> Acao:
        async def acao(s: AsyncSession, ator: Membro) -> tuple[Resposta, int | None]:
            svc.validar_dados(_dados_lancamento(cmd), self.clock.hoje())
            conf = await confirmacoes.criar(
                s,
                self.clock,
                tipo=TipoConfirmacao.LANCAMENTO,
                membro_id=ator.id,
                chat_id=msg.chat_id,
                message_id=msg.message_id,
                payload=c.lancamento_para_json(cmd),
            )
            texto = f"{ouvi}\n\n{fmt.comando_de_voz(cmd)}"
            return Resposta(
                [texto], _botoes_confirmacao(conf.id, "✅ Confirmar", "❌ Cancelar")
            ), None

        return acao

    async def _pedir_cancelamento(
        self, s: AsyncSession, ator: Membro, msg: Mensagem, lancamento_id: int, motivo: str | None
    ) -> Resposta:
        lanc = await svc.obter(s, lancamento_id)
        autorizacao.exigir_alterar(ator, lanc)
        if lanc.status != "ATIVO":
            raise OperacaoInvalida(f"O lançamento {lanc.id} já está cancelado.")
        conf = await confirmacoes.criar(
            s,
            self.clock,
            tipo=TipoConfirmacao.CANCELAMENTO,
            membro_id=ator.id,
            chat_id=msg.chat_id,
            message_id=msg.message_id,
            payload={"lancamento_id": lanc.id, "motivo": motivo},
            lancamento_id=lanc.id,
        )
        return Resposta(
            [fmt.pedido_cancelamento(lanc, motivo)],
            _botoes_confirmacao(conf.id, "🗑️ Confirmar cancelamento", "Manter"),
        )

    def _acao_pedir_cancelamento(self, msg: Mensagem, codigo: int, motivo: str | None) -> Acao:
        async def acao(s: AsyncSession, ator: Membro) -> tuple[Resposta, int | None]:
            return await self._pedir_cancelamento(s, ator, msg, codigo, motivo), None

        return acao

    def _acao_desfazer(self, msg: Mensagem) -> Acao:
        async def acao(s: AsyncSession, ator: Membro) -> tuple[Resposta, int | None]:
            lanc = await svc.ultimo_desfazivel(s, self.clock, ator)
            if lanc is None:
                return Resposta(
                    [
                        "Nenhum lançamento seu dos últimos 10 minutos para desfazer."
                        '\nUse "últimos" e "cancelar <código>".'
                    ]
                ), None
            return await self._pedir_cancelamento(s, ator, msg, lanc.id, "desfazer"), None

        return acao

    async def _executar_confirmacao(
        self, s: AsyncSession, ator: Membro, tipo: str, payload: dict[str, Any]
    ) -> tuple[str, int]:
        if tipo == TipoConfirmacao.LANCAMENTO.value:
            cmd = c.lancamento_de_json(payload)
            r = await svc.criar(
                s, self.clock, ator, _dados_lancamento(cmd), origem=Origem.TELEGRAM_VOZ
            )
            return fmt.lancamento_registrado(r.lancamento, r.parcelas), r.lancamento.id
        lanc = await svc.cancelar(
            s,
            self.clock,
            ator,
            int(payload["lancamento_id"]),
            origem=Origem.TELEGRAM_TEXTO,
            motivo=payload.get("motivo"),
        )
        return fmt.lancamento_cancelado(lanc), lanc.id


def _fixa(resposta: Resposta) -> Acao:
    async def acao(_s: AsyncSession, _ator: Membro) -> tuple[Resposta, int | None]:
        return resposta, None

    return acao
