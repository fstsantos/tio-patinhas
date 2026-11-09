"""Normalização de texto: comparação sem acentos e sem distinção de maiúsculas."""

from __future__ import annotations

import re
import unicodedata

from financas.domain.erros import DescricaoInvalida

DESCRICAO_MAX = 120


def normalizar(texto: str) -> str:
    """Minúsculas, sem acentos, espaços colapsados. ``'Débito  X'`` → ``'debito x'``."""
    decomposto = unicodedata.normalize("NFKD", texto)
    sem_acento = "".join(c for c in decomposto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", sem_acento).strip().casefold()


def validar_descricao(descricao: str) -> str:
    limpa = re.sub(r"\s+", " ", descricao).strip()
    if not limpa:
        raise DescricaoInvalida("Informe uma descrição.")
    if len(limpa) > DESCRICAO_MAX:
        raise DescricaoInvalida(
            f"A descrição tem {len(limpa)} caracteres; o máximo é {DESCRICAO_MAX}."
        )
    return limpa
