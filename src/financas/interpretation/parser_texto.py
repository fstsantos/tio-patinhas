"""Parser determinístico dos comandos de texto (spec 3).

Usado tanto para mensagens de texto quanto para transcrições de voz normalizadas.
Palavras-chave sem distinção de maiúsculas/acentos; aceita ``/cmd`` e ``/cmd@Bot``.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from datetime import date, timedelta
from decimal import Decimal

from financas.domain.calendario import Mes
from financas.domain.dinheiro import formatar_brl, parse_valor
from financas.domain.erros import DataInvalida, ErroDominio
from financas.domain.parcelamento import validar_qtd_parcelas
from financas.domain.texto import normalizar, validar_descricao
from financas.interpretation import comandos as c

SINTAXE = {
    "debito": "débito <valor> <descrição> [data] — ex.: débito 10,50 mercado",
    "credito": "crédito <valor total> <parcelas> <descrição> [data] — ex.: crédito 1000 10x tv",
    "ganho": "ganho <valor> <descrição> [data] — ex.: ganho 3500 salário",
    "resumo": "resumo mês [mm/aaaa] · resumo ano [aaaa] · resumo crédito",
    "cancelar": "cancelar <código> [motivo] — ex.: cancelar 42 lançado em dobro",
}

_DATA = re.compile(r"(\d{1,2})/(\d{1,2})(?:/(\d{4}))?")
_PARCELAS = re.compile(r"(\d{1,3})x?", re.IGNORECASE)
_CODIGO = re.compile(r"#?(\d{1,18})")


class _Erro(Exception):
    def __init__(self, mensagem: str, sugestao: str | None = None) -> None:
        super().__init__(mensagem)
        self.mensagem = mensagem
        self.sugestao = sugestao


def _dominio(e: ErroDominio, padrao: str | None) -> _Erro:
    return _Erro(e.mensagem, e.sugestao or padrao)


def parse_data(token: str, hoje: date) -> date | None:
    """``ontem``, ``hoje``, ``dd/mm`` (ano corrente) ou ``dd/mm/aaaa``; None se não for data."""
    t = normalizar(token)
    if t == "hoje":
        return hoje
    if t == "ontem":
        return hoje - timedelta(days=1)
    m = _DATA.fullmatch(t)
    if m is None:
        return None
    dia, mes, ano = int(m[1]), int(m[2]), int(m[3]) if m[3] else hoje.year
    try:
        return date(ano, mes, dia)
    except ValueError as e:
        raise DataInvalida(f"Data inválida: '{token}'.", sugestao="Use dd/mm ou dd/mm/aaaa.") from e


def _data_e_descricao(args: list[str], hoje: date, cmd: str) -> tuple[date, str]:
    data = hoje
    if args:
        try:
            encontrada = parse_data(args[-1], hoje)
        except ErroDominio as e:
            raise _dominio(e, SINTAXE[cmd]) from e
        if encontrada is not None:
            data = encontrada
            args = args[:-1]
    try:
        descricao = validar_descricao(" ".join(args))
    except ErroDominio as e:
        raise _dominio(e, SINTAXE[cmd]) from e
    return data, descricao


def _valor(token: str | None, cmd: str) -> Decimal:
    if token is None:
        raise _Erro("Informe o valor.", SINTAXE[cmd])
    try:
        return parse_valor(token)
    except ErroDominio as e:
        raise _dominio(e, SINTAXE[cmd]) from e


def _juntar_rs(args: list[str]) -> list[str]:
    """``R$ 10,50`` (com espaço) vira um único token."""
    if len(args) >= 2 and normalizar(args[0]) == "r$":
        return [args[0] + args[1], *args[2:]]
    return args


def _debito_ganho(cmd: str) -> Callable[[list[str], date], c.Comando]:
    def interpretar(args: list[str], hoje: date) -> c.Comando:
        args = _juntar_rs(args)
        valor = _valor(args[0] if args else None, cmd)
        data, descricao = _data_e_descricao(args[1:], hoje, cmd)
        cls = c.Debito if cmd == "debito" else c.Ganho
        return cls(valor=valor, descricao=descricao, data=data)

    return interpretar


def _credito(args: list[str], hoje: date) -> c.Comando:
    args = _juntar_rs(args)
    valor = _valor(args[0] if args else None, "credito")
    if len(args) < 2 or (m := _PARCELAS.fullmatch(args[1])) is None:
        raise _Erro("Informe a quantidade de parcelas depois do valor total.", SINTAXE["credito"])
    try:
        parcelas = validar_qtd_parcelas(int(m[1]))
    except ErroDominio as e:
        raise _dominio(e, SINTAXE["credito"]) from e
    data, descricao = _data_e_descricao(args[2:], hoje, "credito")
    return c.Credito(
        valor_total=valor,
        parcelas=parcelas,
        descricao=descricao,
        data=data,
    )


def _resumo(args: list[str], hoje: date) -> c.Comando:
    if not args:
        raise _Erro("Qual resumo?", SINTAXE["resumo"])
    tipo, resto = normalizar(args[0]), args[1:]
    if tipo == "mes":
        if not resto:
            return c.ResumoMes()
        try:
            return c.ResumoMes(Mes.parse(resto[0]))
        except ErroDominio as e:
            raise _dominio(e, SINTAXE["resumo"]) from e
    if tipo == "ano":
        if not resto:
            return c.ResumoAno()
        if not re.fullmatch(r"\d{4}", resto[0]) or not 2000 <= int(resto[0]) <= 2100:
            raise _Erro(f"Ano inválido: '{resto[0]}'.", "Ex.: resumo ano 2026")
        return c.ResumoAno(int(resto[0]))
    if tipo == "credito":
        return c.ResumoCredito()
    raise _Erro(f"Resumo desconhecido: '{args[0]}'.", SINTAXE["resumo"])


def _cancelar(args: list[str], hoje: date) -> c.Comando:
    if not args or (m := _CODIGO.fullmatch(args[0])) is None:
        raise _Erro("Informe o código do lançamento (veja com 'últimos').", SINTAXE["cancelar"])
    motivo = " ".join(args[1:]).strip() or None
    return c.Cancelar(codigo=int(m[1]), motivo=motivo)


def _sem_args(cmd: c.Comando) -> Callable[[list[str], date], c.Comando]:
    return lambda _args, _hoje: cmd


_COMANDOS: dict[str, tuple[str, Callable[[list[str], date], c.Comando]]] = {
    "debito": ("debito", _debito_ganho("debito")),
    "credito": ("credito", _credito),
    "ganho": ("ganho", _debito_ganho("ganho")),
    "resumo": ("resumo", _resumo),
    "ajuda": ("ajuda", _sem_args(c.Ajuda())),
    "help": ("ajuda", _sem_args(c.Ajuda())),
    "start": ("start", _sem_args(c.Start())),
    "ultimos": ("ultimos", _sem_args(c.Ultimos())),
    "cancelar": ("cancelar", _cancelar),
    "desfazer": ("desfazer", _sem_args(c.Desfazer())),
}


def palavra_chave(texto: str, bot_username: str | None = None) -> str | None:
    """Nome canônico do comando no início do texto, ou None."""
    tokens = texto.split()
    if not tokens:
        return None
    primeiro = tokens[0].removeprefix("/")
    if "@" in primeiro:
        primeiro, _, destino = primeiro.partition("@")
        if bot_username and normalizar(destino) != normalizar(bot_username.lstrip("@")):
            return None
    entrada = _COMANDOS.get(normalizar(primeiro))
    return entrada[0] if entrada else None


def interpretar(texto: str, hoje: date, bot_username: str | None = None) -> c.Resultado:
    nome = palavra_chave(texto, bot_username)
    if nome is None:
        return c.NaoReconhecido()
    tokens = texto.split()
    _, funcao = _COMANDOS[normalizar(tokens[0].removeprefix("/").partition("@")[0])]
    try:
        return funcao(tokens[1:], hoje)
    except _Erro as e:
        return c.ErroComando(nome, e.mensagem, e.sugestao)


def para_texto(cmd: c.Lancamento) -> str:
    """Forma canônica em texto (usada para exibir comandos de voz e validar propostas)."""
    valor = formatar_brl(cmd.valor_total if isinstance(cmd, c.Credito) else cmd.valor)
    valor = valor.removeprefix("R$ ")
    data = f" {cmd.data:%d/%m/%Y}"
    if isinstance(cmd, c.Credito):
        return f"crédito {valor} {cmd.parcelas}x {cmd.descricao}{data}"
    nome = "débito" if isinstance(cmd, c.Debito) else "ganho"
    return f"{nome} {valor} {cmd.descricao}{data}"
