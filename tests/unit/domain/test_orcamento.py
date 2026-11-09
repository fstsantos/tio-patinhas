from datetime import date
from decimal import Decimal

from financas.domain.calendario import Mes, SituacaoMes
from financas.domain.orcamento import calcular_orcamento

D = Decimal


def test_mes_corrente_saldo_positivo() -> None:
    o = calcular_orcamento(
        Mes(2026, 3),
        date(2026, 3, 20),
        receitas=D("3500.00"),
        debitos=D("1000.00"),
        parcelas=D("300.00"),
    )
    assert o.saldo == D("2200.00")
    assert o.dias_restantes == 12
    assert o.orcamento_diario == D("183.33")
    assert o.despesas_consideradas == D("1300.00")
    assert not o.deficit


def test_arredondamento_half_up_no_orcamento() -> None:
    # 100,00 / 8 = 12,50 exato; 0,05 / 2 = 0,025 → 0,03
    o = calcular_orcamento(
        Mes(2026, 3), date(2026, 3, 30), receitas=D("0.05"), debitos=D("0"), parcelas=D("0")
    )
    assert o.orcamento_diario == D("0.03")


def test_ultimo_dia_do_mes() -> None:
    o = calcular_orcamento(
        Mes(2026, 2), date(2026, 2, 28), receitas=D("100.00"), debitos=D("0"), parcelas=D("0")
    )
    assert o.dias_restantes == 1
    assert o.orcamento_diario == D("100.00")


def test_deficit_orcamento_zero() -> None:
    o = calcular_orcamento(
        Mes(2026, 3),
        date(2026, 3, 5),
        receitas=D("100.00"),
        debitos=D("80.00"),
        parcelas=D("50.00"),
    )
    assert o.saldo == D("-30.00")
    assert o.deficit
    assert o.orcamento_diario == D("0.00")


def test_saldo_zero_orcamento_zero() -> None:
    o = calcular_orcamento(
        Mes(2026, 3), date(2026, 3, 5), receitas=D("100.00"), debitos=D("100.00"), parcelas=D("0")
    )
    assert o.orcamento_diario == D("0.00")


def test_mes_encerrado_sem_orcamento_diario() -> None:
    o = calcular_orcamento(
        Mes(2026, 2), date(2026, 3, 1), receitas=D("100.00"), debitos=D("10.00"), parcelas=D("0")
    )
    assert o.situacao is SituacaoMes.ENCERRADO
    assert o.saldo == D("90.00")
    assert o.orcamento_diario is None
    assert o.dias_restantes == 0


def test_mes_futuro_usa_todos_os_dias() -> None:
    o = calcular_orcamento(
        Mes(2026, 4), date(2026, 3, 31), receitas=D("300.00"), debitos=D("0"), parcelas=D("0")
    )
    assert o.situacao is SituacaoMes.FUTURO
    assert o.dias_restantes == 30
    assert o.orcamento_diario == D("10.00")
