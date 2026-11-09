from financas.bot.paginacao import dividir


def test_agrupa_sem_quebrar_blocos() -> None:
    blocos = ["a" * 40, "b" * 40, "c" * 40]
    assert dividir(blocos, limite=90) == ["a" * 40 + "\n\n" + "b" * 40, "c" * 40]


def test_bloco_maior_que_limite_quebra_por_linhas() -> None:
    bloco = "\n".join(["x" * 30] * 5)
    partes = dividir(["inicio", bloco, "fim"], limite=70)
    assert all(len(p) <= 70 for p in partes)
    assert "".join(partes).count("x") == 150
    assert partes[0] == "inicio" and partes[-1].endswith("fim")


def test_linha_gigante() -> None:
    partes = dividir(["y" * 250], limite=100)
    assert [len(p) for p in partes] == [100, 100, 50]


def test_vazios() -> None:
    assert dividir([]) == []
    assert dividir(["", "a"]) == ["a"]
