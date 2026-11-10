"""Leitor da fatura do Itaú (.xlsx), com planilhas sintéticas."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest

from financas.domain.calendario import Mes
from financas.integrations.fatura.itau import ErroFatura, ler_fatura, limpar_descricao
from tests import factories as f

D = Decimal


@pytest.mark.parametrize(
    ("bruta", "limpa"),
    [
        ("Clinica Exemplosao Paulobra", "Clinica Exemplo"),
        ("Hwl Mercado Avenida Cesao Paulobra", "Hwl Mercado Avenida Ce"),
        ("Mercadolivre*mercadoliosascobra", "Mercadolivre*mercadoli"),
        ("Netflix Entretenimentobarueribra", "Netflix Entretenimento"),
        ("Br1*roupasbrusquebra", "Br1*roupas"),
        (
            "Cancelamento Parcial De Compra - Lojinhasao Paulobra",
            "Cancelamento Parcial De Compra - Lojinha",
        ),
        # Formato colunado: nome (23) + cidade (13) + " Bra".
        ("Loja Teste             Rio De Janeir Bra", "Loja Teste"),
        ("Xy *Moveis Planejad Br So Paulo      Bra", "Xy *Moveis Planejad Br"),
        ("Amazon Br              Sao Paulo     Bra", "Amazon Br"),
        # Cidade desconhecida ou sem país: fica como veio.
        ("Loja Xyzpindamonhangabra", "Loja Xyzpindamonhangabra"),
        ("Pagamento Boleto", "Pagamento Boleto"),
        ("Cobra", "Cobra"),
        ("Osascobra", "Osascobra"),
    ],
)
def test_limpar_descricao(bruta: str, limpa: str) -> None:
    assert limpar_descricao(bruta) == limpa


def test_ler_fatura(tmp_path: Path) -> None:
    arquivo = f.planilha_fatura(
        tmp_path / "fatura.xlsx",
        [
            (date(2026, 10, 15), "Pagamento Boleto", None, -500.0),
            (date(2026, 11, 6), "Clinica Exemplosao Paulobra", "Parcela 1 de 5", 210),
            (date(2026, 11, 1), "Petshopsao Paulobra", None, 169.8),
            (date(2026, 10, 8), "Cancelamento Parcial - Lojasao Paulobra", None, -19.9),
            (
                date(2026, 5, 4),
                "Xy *Moveis Planejad Br So Paulo      Bra",
                "Parcela 7 de 18",
                206.72,
            ),
        ],
    )
    fatura = ler_fatura(arquivo)
    assert (fatura.mes, fatura.total, fatura.pagamentos_descartados) == (
        Mes(2026, 11),
        D("566.62"),
        1,
    )
    assert [
        (x.linha, x.data, x.descricao, x.parcela, x.qtd_parcelas, x.valor) for x in fatura.linhas
    ] == [
        (16, date(2026, 11, 6), "Clinica Exemplo", 1, 5, D("210.00")),
        (17, date(2026, 11, 1), "Petshop", 1, 1, D("169.80")),
        (18, date(2026, 10, 8), "Cancelamento Parcial - Loja", 1, 1, D("-19.90")),
        (19, date(2026, 5, 4), "Xy *Moveis Planejad Br", 7, 18, D("206.72")),
    ]


def test_mes_com_acento(tmp_path: Path) -> None:
    arquivo = f.planilha_fatura(
        tmp_path / "f.xlsx",
        [(date(2026, 3, 1), "Lojasao Paulobra", None, 10)],
        titulo="Fatura Fechada - Março/2026",
    )
    assert ler_fatura(arquivo).mes == Mes(2026, 3)


@pytest.mark.parametrize(
    ("linhas", "extras", "trecho"),
    [
        ([(date(2026, 11, 1), "Loja", None, 10.0)], {"total": 11.0}, "não confere"),
        ([(date(2026, 11, 1), "Loja", None, 10.0)], {"titulo": "Extrato"}, "Título"),
        ([(date(2026, 11, 1), "Loja", "3 de 2", 10.0)], {}, "parcelamento inválido"),
        ([(date(2026, 11, 1), "Loja", "Parcela 3 de 2", 10.0)], {}, "parcelamento inválido"),
        ([(date(2026, 11, 1), "Loja", None, "dez")], {"total": 10.0}, "Valor inválido"),
        ([(date(2026, 11, 1), "  ", None, 10.0)], {}, "sem descrição"),
        ([], {"total": 0.0}, "Nenhum lançamento"),
    ],
)
def test_planilha_invalida(
    tmp_path: Path,
    linhas: list,
    extras: dict,
    trecho: str,  # type: ignore[type-arg]
) -> None:
    arquivo = f.planilha_fatura(tmp_path / "f.xlsx", linhas, **extras)
    with pytest.raises(ErroFatura, match=trecho):
        ler_fatura(arquivo)


def test_arquivo_que_nao_e_planilha(tmp_path: Path) -> None:
    arquivo = tmp_path / "f.xlsx"
    arquivo.write_text("não é xlsx")
    with pytest.raises(ErroFatura, match="abrir"):
        ler_fatura(arquivo)
    vazio = tmp_path / "vazio.xlsx"
    from openpyxl import Workbook

    Workbook().save(vazio)
    with pytest.raises(ErroFatura, match="Cabeçalho"):
        ler_fatura(vazio)
