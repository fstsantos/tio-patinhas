"""Divisão de mensagens no limite do Telegram (4.096 caracteres)."""

from __future__ import annotations

LIMITE_TELEGRAM = 4096
SEPARADOR = "\n\n"


def _quebrar_bloco(bloco: str, limite: int) -> list[str]:
    """Bloco maior que o limite: quebra por linhas (e, em último caso, por caracteres)."""
    partes: list[str] = []
    atual = ""
    for linha_original in bloco.split("\n"):
        linha = linha_original
        while len(linha) > limite:
            if atual:
                partes.append(atual)
                atual = ""
            partes.append(linha[:limite])
            linha = linha[limite:]
        candidato = f"{atual}\n{linha}" if atual else linha
        if len(candidato) > limite:
            partes.append(atual)
            atual = linha
        else:
            atual = candidato
    if atual:
        partes.append(atual)
    return partes


def dividir(blocos: list[str], limite: int = LIMITE_TELEGRAM) -> list[str]:
    """Agrupa blocos em mensagens de até ``limite`` caracteres sem quebrar um bloco,
    a menos que ele sozinho exceda o limite."""
    mensagens: list[str] = []
    atual = ""
    for bloco in (b for b in blocos if b):
        if len(bloco) > limite:
            if atual:
                mensagens.append(atual)
                atual = ""
            mensagens.extend(_quebrar_bloco(bloco, limite))
            continue
        candidato = f"{atual}{SEPARADOR}{bloco}" if atual else bloco
        if len(candidato) > limite:
            mensagens.append(atual)
            atual = bloco
        else:
            atual = candidato
    if atual:
        mensagens.append(atual)
    return mensagens
