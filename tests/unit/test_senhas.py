import pytest

from financas.services import senhas


def test_hash_e_verificacao() -> None:
    h = senhas.gerar_hash("senha-forte-123")
    assert h.startswith("$argon2")
    assert senhas.verificar(h, "senha-forte-123")
    assert not senhas.verificar(h, "outra-senha-123")
    assert not senhas.verificar("lixo", "senha-forte-123")
    assert not senhas.precisa_rehash(h)


def test_senha_curta() -> None:
    with pytest.raises(ValueError, match="10 caracteres"):
        senhas.gerar_hash("curta")
