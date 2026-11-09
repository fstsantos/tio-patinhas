"""Hash de senhas com argon2 (argon2-cffi)."""

from __future__ import annotations

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

_hasher = PasswordHasher()

SENHA_MIN = 10


def gerar_hash(senha: str) -> str:
    if len(senha) < SENHA_MIN:
        raise ValueError(f"A senha deve ter pelo menos {SENHA_MIN} caracteres.")
    return _hasher.hash(senha)


def verificar(senha_hash: str, senha: str) -> bool:
    try:
        return _hasher.verify(senha_hash, senha)
    except (VerificationError, InvalidHashError):
        return False


def precisa_rehash(senha_hash: str) -> bool:
    return _hasher.check_needs_rehash(senha_hash)
