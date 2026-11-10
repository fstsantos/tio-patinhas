from datetime import date
from decimal import Decimal

import pytest

from financas.domain.calendario import Mes
from financas.interpretation import comandos as c
from financas.interpretation.parser_texto import interpretar, para_texto

HOJE = date(2026, 3, 20)
D = Decimal


def p(texto: str, bot: str | None = "TioPatinhasBot") -> c.Resultado:
    return interpretar(texto, HOJE, bot)


@pytest.mark.parametrize(
    "texto",
    [
        "débito 10,50 compras de mercado",
        "debito 10,50 compras de mercado",
        "DEBITO 10,50 compras de mercado",
        "/debito 10,50 compras de mercado",
        "/débito@TioPatinhasBot 10,50 compras de mercado",
        "Débito R$ 10,50 compras de mercado",
        "débito R$10,50 compras  de   mercado",
        "/DEBITO@tiopatinhasbot 10,50 compras de mercado",
    ],
)
def test_debito_variacoes(texto: str) -> None:
    assert p(texto) == c.Debito(D("10.50"), "compras de mercado", HOJE)


def test_comando_para_outro_bot_ignorado() -> None:
    assert p("/debito@OutroBot 10 x") == c.NaoReconhecido()


@pytest.mark.parametrize(
    ("sufixo", "data"),
    [
        ("ontem", date(2026, 3, 19)),
        ("Hoje", HOJE),
        ("15/03", date(2026, 3, 15)),
        ("1/2", date(2026, 2, 1)),
        ("31/12/2025", date(2025, 12, 31)),
    ],
)
def test_data_opcional(sufixo: str, data: date) -> None:
    assert p(f"débito 45,00 farmácia {sufixo}") == c.Debito(D("45.00"), "farmácia", data)


@pytest.mark.parametrize(
    ("sufixo", "data"),
    [
        ("21/03", date(2026, 3, 21)),
        ("01/01/2027", date(2027, 1, 1)),
        ("25/12", date(2026, 12, 25)),  # dd/mm sem ano: sempre o ano corrente
    ],
)
def test_data_futura_aceita(sufixo: str, data: date) -> None:
    assert p(f"débito 45,00 farmácia {sufixo}") == c.Debito(D("45.00"), "farmácia", data)


def test_data_inexistente() -> None:
    r = p("ganho 10 x 30/02")
    assert isinstance(r, c.ErroComando) and "Data inválida" in r.mensagem


@pytest.mark.parametrize(
    "texto",
    [
        "crédito 1000,00 10 mercado livre",
        "credito 1.000 10x mercado livre",
        "/credito R$ 1000 10X mercado livre",
    ],
)
def test_credito(texto: str) -> None:
    assert p(texto) == c.Credito(D("1000.00"), 10, "mercado livre", HOJE)


def test_credito_com_data() -> None:
    assert p("crédito 300 3 geladeira 10/02") == c.Credito(
        D("300.00"), 3, "geladeira", date(2026, 2, 10)
    )


@pytest.mark.parametrize(
    ("texto", "trecho"),
    [
        ("crédito 1000 mercado", "parcelas"),
        ("crédito 1000 0 tv", "parcelas"),
        ("crédito 1000 49x tv", "parcelas"),
        ("crédito 1000 10", "descrição"),
        ("crédito 10.50 2 x", "10,50"),
    ],
)
def test_credito_invalido(texto: str, trecho: str) -> None:
    r = p(texto)
    assert isinstance(r, c.ErroComando)
    assert trecho in f"{r.mensagem} {r.sugestao}"


def test_ganho() -> None:
    assert p("ganho 3500,00 salario") == c.Ganho(D("3500.00"), "salario", HOJE)


@pytest.mark.parametrize(
    ("texto", "trecho"),
    [
        ("débito", "valor"),
        ("débito 10", "descrição"),
        ("débito mercado 10", "vírgula"),
        ("débito 10.50 mercado", "Você quis dizer 10,50?"),
        ("débito 0 x", "0,01"),
        ("débito 10,505 x", "duas casas"),
        ("débito -5 x", "positivo"),
        ("débito 10 " + "x" * 121, "120"),
        ("débito 10 ontem", "descrição"),
    ],
)
def test_debito_invalido(texto: str, trecho: str) -> None:
    r = p(texto)
    assert isinstance(r, c.ErroComando) and r.comando == "debito"
    assert trecho in f"{r.mensagem} {r.sugestao}"


@pytest.mark.parametrize(
    ("texto", "esperado"),
    [
        ("resumo mês", c.ResumoMes()),
        ("resumo mes", c.ResumoMes()),
        ("/resumo MÊS 02/2026", c.ResumoMes(Mes(2026, 2))),
        ("resumo ano", c.ResumoAno()),
        ("resumo ano 2025", c.ResumoAno(2025)),
        ("resumo crédito", c.ResumoCredito()),
        ("resumo credito", c.ResumoCredito()),
        ("ajuda", c.Ajuda()),
        ("help", c.Ajuda()),
        ("/start", c.Start()),
        ("start", c.Start()),
        ("últimos", c.Ultimos()),
        ("ultimos", c.Ultimos()),
        ("desfazer", c.Desfazer()),
        ("cancelar 42", c.Cancelar(42)),
        ("cancelar #42 lançado em dobro", c.Cancelar(42, "lançado em dobro")),
    ],
)
def test_outros_comandos(texto: str, esperado: c.Comando) -> None:
    assert p(texto) == esperado


@pytest.mark.parametrize(
    "texto",
    ["resumo", "resumo semana", "resumo mês 13/2026", "resumo ano 26", "cancelar", "cancelar abc"],
)
def test_outros_invalidos(texto: str) -> None:
    assert isinstance(p(texto), c.ErroComando)


@pytest.mark.parametrize("texto", ["", "   ", "oi pessoal", "comprei pão", "/foo"])
def test_nao_reconhecido(texto: str) -> None:
    assert p(texto) == c.NaoReconhecido()


def test_para_texto_ida_e_volta() -> None:
    cmds: list[c.Lancamento] = [
        c.Debito(D("1234.50"), "mercado", date(2026, 3, 1)),
        c.Credito(D("1000.00"), 10, "tv", HOJE),
        c.Ganho(D("0.01"), "troco", HOJE),
    ]
    for cmd in cmds:
        assert p(para_texto(cmd)) == cmd
        assert c.lancamento_de_json(c.lancamento_para_json(cmd)) == cmd
