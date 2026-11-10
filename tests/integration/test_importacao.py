"""Importação de fatura: plano, gravação, regras e reimportação."""

from __future__ import annotations

from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from typer.testing import CliRunner

from financas import cli
from financas.config import get_settings
from financas.db.models import Categoria, Lancamento, Parcela
from financas.domain.erros import NaoEncontrado, OperacaoInvalida, SemPermissao
from financas.integrations.fatura.itau import ler_fatura
from financas.services import importacao as imp
from financas.services import lancamentos as svc
from financas.services.importacao import RegraCategoria, Regras, Situacao
from financas.services.integridade import verificar_integridade
from tests import factories as f

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("limpar_banco")]

D = Decimal
HOJE = date(2026, 11, 10)
NOV = date(2026, 11, 1)
LINHAS: list[tuple[date, str, str | None, float]] = [
    (date(2026, 10, 15), "Pagamento Boleto", None, -900.0),
    (date(2026, 11, 6), "Clinica Exemplosao Paulobra", "Parcela 1 de 5", 210),
    (date(2026, 11, 1), "Petshopsao Paulobra", None, 169.8),
    (date(2026, 10, 25), "Ifd*mercado Comerciosao Paulobra", None, 44.11),
    (date(2026, 10, 25), "Ifd*mercado Comerciosao Paulobra", None, 44.11),
    (date(2026, 10, 8), "Cancelamento Parcial De Compra - Lojinhasao Paulobra", None, -199.9),
    (date(2026, 10, 3), "Lojinhasao Paulobra", "Parcela 2 de 6", 58.3),
    (date(2026, 5, 4), "Xy *Moveis Planejad Br So Paulo      Bra", "Parcela 7 de 18", 206.72),
    (date(2026, 10, 9), "Loja Sem Regrasao Paulobra", None, 18.9),
]
REGRAS = Regras(
    ignorar=("lojinha",),
    categorias=(
        RegraCategoria("Saúde", ("clinica", "planejad")),
        RegraCategoria("Pets", ("petshop",)),
        RegraCategoria("Mercado", ("ifd*mercado",)),
        RegraCategoria("Restaurantes", ("ifd*",)),
    ),
)
# Total da fatura sem as duas linhas da Lojinha.
NO_MES = D("693.64")


@pytest.fixture
def clock():  # type: ignore[no-untyped-def]
    return f.relogio(HOJE)


@pytest.fixture
def arquivo(tmp_path: Path) -> Path:
    return f.planilha_fatura(tmp_path / "fatura.xlsx", LINHAS)


async def _importar(s: AsyncSession, clock, arquivo: Path, regras: Regras = REGRAS):  # type: ignore[no-untyped-def]
    titular = await imp.membro_por_nome(s, "carlos")
    plano = await imp.planejar(s, titular, ler_fatura(arquivo), regras)
    return plano, await imp.executar(s, clock, plano)


async def test_importa_fatura_inteira(
    fabrica: async_sessionmaker[AsyncSession], clock, arquivo: Path
) -> None:  # type: ignore[no-untyped-def]
    async with fabrica() as s, s.begin():
        await f.membro(s, "Carlos", admin=True)
        plano, criados = await _importar(s, clock, arquivo)
    assert [i.situacao for i in plano.itens] == [
        Situacao.NOVA,
        Situacao.NOVA,
        Situacao.NOVA,
        Situacao.NOVA,
        Situacao.IGNORADA,
        Situacao.IGNORADA,
        Situacao.NOVA,
        Situacao.NOVA,
    ]
    assert plano.total(Situacao.NOVA) == NO_MES
    assert plano.total(Situacao.IGNORADA) == D("-141.60")
    assert len(criados) == 6
    async with fabrica() as s:
        assert await verificar_integridade(s) == []
        # As parcelas de novembro somam o total da fatura menos o que foi ignorado.
        soma = await s.scalar(select(func.sum(Parcela.valor)).where(Parcela.competencia == NOV))
        assert soma == NO_MES
        linhas = (
            await s.execute(
                select(Lancamento, Categoria.nome)
                .outerjoin(Categoria, Categoria.id == Lancamento.categoria_id)
                .order_by(Lancamento.id)
            )
        ).all()
        assert [(x.descricao, x.valor_total, x.qtd_parcelas, x.origem, c) for x, c in linhas] == [
            ("Clinica Exemplo", D("1050.00"), 5, "CLI", "Saúde"),
            ("Petshop", D("169.80"), 1, "CLI", "Pets"),
            ("Ifd*mercado Comercio", D("44.11"), 1, "CLI", "Mercado"),
            ("Ifd*mercado Comercio", D("44.11"), 1, "CLI", "Mercado"),
            ("Xy *Moveis Planejad Br", D("3720.96"), 18, "CLI", "Saúde"),
            ("Loja Sem Regra", D("18.90"), 1, "CLI", None),
        ]
        assert not [x for x, _c in linhas if "lojinha" in x.descricao_normalizada]
        # Parcelado antigo: compra inteira, da 1ª parcela (mai) à 18ª (out/2027).
        antigo = linhas[4][0]
        parcelas = await svc.parcelas_vigentes(s, antigo.id)
        assert antigo.primeira_competencia == date(2026, 5, 1)
        assert (parcelas[0].competencia, parcelas[6].competencia, parcelas[-1].competencia) == (
            date(2026, 5, 1),
            NOV,
            date(2027, 10, 1),
        )
        assert {p.valor for p in parcelas} == {D("206.72")}
        # Compra de outubro que caiu na fatura de novembro: 1ª parcela em M+1.
        assert linhas[1][0].primeira_competencia == NOV
        assert sorted(c.nome for c in (await s.execute(select(Categoria))).scalars()) == [
            "Mercado",
            "Pets",
            "Saúde",
        ]


async def test_reimportar_nao_duplica(
    fabrica: async_sessionmaker[AsyncSession], clock, arquivo: Path, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    async with fabrica() as s, s.begin():
        carlos = await f.membro(s, "Carlos", admin=True)
        await f.membro(s, "Bia")
        # Uma das duas compras idênticas já existe (lançada à mão, sem categoria).
        await f.lancar(
            s, clock, carlos, svc.TipoLancamento.CREDITO, "44.11", "ifd*mercado  COMERCIO",
            data=date(2026, 10, 25), parcelas=1,
        )  # fmt: skip
        plano, criados = await _importar(s, clock, arquivo)
        assert len(plano.com(Situacao.EXISTENTE)) == 1 and len(criados) == 5
    async with fabrica() as s, s.begin():
        plano, criados = await _importar(s, clock, arquivo)
        assert criados == [] and len(plano.com(Situacao.EXISTENTE)) == 6
        assert plano.total(Situacao.EXISTENTE) == NO_MES
        # Fatura seguinte: a parcela 8 de 18 é a mesma compra, mesmo no formato colado
        # e com o nome truncado de outro jeito; a Clinica passa para a parcela 2.
        dezembro = f.planilha_fatura(
            tmp_path / "dez.xlsx",
            [
                (date(2026, 5, 4), "Xy *Moveis Planejad Brsao Paulobra", "Parcela 8 de 18", 206.72),
                (date(2026, 11, 6), "Clinica Exemplosao Paulobra", "Parcela 2 de 5", 210),
                (date(2026, 11, 8), "Petshopsao Paulobra", None, 30),
            ],
            titulo="Fatura Fechada - Dezembro/2026",
        )
        plano, criados = await _importar(s, clock, dezembro)
        assert [i.situacao for i in plano.itens] == [
            Situacao.EXISTENTE,
            Situacao.EXISTENTE,
            Situacao.NOVA,
        ]
        assert [x.descricao for x in criados] == ["Petshop"]
        # Compra cancelada deixa de contar como existente; outro titular também não conta.
        await svc.cancelar(s, clock, carlos, criados[0].id, origem=svc.Origem.WEB)
        plano, _ = await _importar(s, clock, dezembro)
        assert plano.itens[2].situacao is Situacao.NOVA
    async with fabrica() as s:
        bia = await imp.membro_por_nome(s, "Bia")
        plano = await imp.planejar(s, bia, ler_fatura(arquivo), REGRAS)
        assert not plano.com(Situacao.EXISTENTE)
        assert await verificar_integridade(s) == []


async def test_erros_bloqueiam_a_importacao(
    fabrica: async_sessionmaker[AsyncSession], clock, arquivo: Path, tmp_path: Path
) -> None:  # type: ignore[no-untyped-def]
    ruim = f.planilha_fatura(
        tmp_path / "erros.xlsx",
        [
            (date(2026, 11, 1), "Lojasao Paulobra", "Parcela 1 de 49", 10),  # parcelas demais
            (date(2026, 3, 1), "Antigasao Paulobra", "Parcela 2 de 3", 10),  # 1ª parcela em out
            (date(2026, 11, 1), "Boasao Paulobra", None, 10),
        ],
    )
    async with fabrica() as s, s.begin():
        carlos = await f.membro(s, "Carlos", admin=True)
        # Sem a regra de ignorar, o estorno é um erro e nada é gravado.
        plano = await imp.planejar(s, carlos, ler_fatura(arquivo), Regras())
        (erro,) = plano.com(Situacao.ERRO)
        assert erro.linha.valor == D("-199.90") and "ignorar" in erro.detalhe
        with pytest.raises(OperacaoInvalida, match="linhas com erro"):
            await imp.executar(s, clock, plano)
        plano = await imp.planejar(s, carlos, ler_fatura(ruim), Regras())
        assert [i.situacao for i in plano.itens] == [Situacao.ERRO, Situacao.ERRO, Situacao.NOVA]
        assert (
            "parcelas inválida" in plano.itens[0].detalhe and "1ª parcela" in plano.itens[1].detalhe
        )
        assert await s.scalar(select(func.count()).select_from(Lancamento)) == 0


async def test_membro_e_permissoes(
    fabrica: async_sessionmaker[AsyncSession], clock, arquivo: Path
) -> None:  # type: ignore[no-untyped-def]
    async with fabrica() as s, s.begin():
        await f.membro(s, "Carlos")  # não é admin
        await f.membro(s, "Inativa", ativo=False)
        for nome in ("Ninguém", "Inativa"):
            with pytest.raises(NaoEncontrado):
                await imp.membro_por_nome(s, nome)
        # Quem não é admin importa, desde que não precise criar categoria.
        _, criados = await _importar(s, clock, arquivo, Regras(ignorar=("lojinha",)))
        assert len(criados) == 6
    async with fabrica() as s, s.begin():
        await svc.cancelar(
            s, clock, await imp.membro_por_nome(s, "Carlos"), 1, origem=svc.Origem.WEB
        )
        with pytest.raises(SemPermissao):
            await _importar(s, clock, arquivo)


def test_carregar_regras(tmp_path: Path) -> None:
    arq = tmp_path / "regras.toml"
    arq.write_text(
        'ignorar = ["Lojinha"]\n\n[[categoria]]\nnome = "Saúde"\n'
        'trechos = ["Clinica", "LOJA TESTE"]\n'
        '\n[[categoria]]\nnome = "Restaurantes"\ntrechos = ["ifd*"]\n',
        encoding="utf-8",
    )
    regras = imp.carregar_regras(arq)
    assert regras.ignora("Cancelamento - LOJINHA") and not regras.ignora("Petshop")
    assert regras.categoria("Loja Teste") == "Saúde"
    assert regras.categoria("Ifd*restaurante Bom") == "Restaurantes"
    assert regras.categoria("Petshop") is None
    for ruim in (
        "ignorar = 'lojinha'",
        "ignorar = ['']",
        "outra = 1",
        "categoria = 1",
        "[[categoria]]\ntrechos = ['x']",
        "[[categoria]]\nnome = 'X'",
        "isto não é toml",
    ):
        arq.write_text(ruim, encoding="utf-8")
        with pytest.raises(OperacaoInvalida, match=r"[Rr]egras"):
            imp.carregar_regras(arq)
    with pytest.raises(OperacaoInvalida):
        imp.carregar_regras(tmp_path / "nao-existe.toml")


@pytest.fixture
def runner(banco_migrado: str, monkeypatch: pytest.MonkeyPatch) -> CliRunner:
    monkeypatch.setenv("DATABASE_URL", banco_migrado)
    monkeypatch.setattr(cli, "configurar_logs", lambda *_a, **_k: None)
    get_settings.cache_clear()
    yield CliRunner()  # type: ignore[misc]
    get_settings.cache_clear()


async def test_cli_previa_e_confirmar(
    fabrica: async_sessionmaker[AsyncSession], runner: CliRunner, arquivo: Path, tmp_path: Path
) -> None:
    async with fabrica() as s, s.begin():
        await f.membro(s, "Carlos", admin=True)
    regras = tmp_path / "regras.toml"
    regras.write_text('[[categoria]]\nnome = "Pets"\ntrechos = ["petshop"]\n', encoding="utf-8")
    base = ["importar-fatura", str(arquivo), "--membro", "Carlos", "--regras", str(regras)]

    async def total() -> int:
        async with fabrica() as s:
            return await s.scalar(select(func.count()).select_from(Lancamento)) or 0

    import asyncio

    # Estorno sem regra: erro, código 1, nada gravado mesmo com --confirmar.
    r = await asyncio.to_thread(runner.invoke, cli.app, [*base, "--confirmar"])
    assert r.exit_code == 1 and "erro" in r.output and "nada foi importado" in r.output
    assert await total() == 0

    regras.write_text(
        'ignorar = ["lojinha"]\n[[categoria]]\nnome = "Pets"\ntrechos = ["petshop"]\n',
        encoding="utf-8",
    )
    r = await asyncio.to_thread(runner.invoke, cli.app, base)
    assert r.exit_code == 0, r.output
    assert "Fatura nov/2026 — total R$ 552,04 — titular Carlos" in r.output
    assert "Novas: 6 (R$ 693,64)" in r.output and "ignoradas: 2 (-R$ 141,60)" in r.output
    assert "Parcelas desta fatura em nov/2026: R$ 693,64" in r.output
    assert "Prévia: nada foi gravado" in r.output and "sem categoria" in r.output
    assert await total() == 0

    r = await asyncio.to_thread(runner.invoke, cli.app, [*base, "--confirmar"])
    assert r.exit_code == 0, r.output
    assert "6 compra(s) importada(s)." in r.output
    assert await total() == 6

    r = await asyncio.to_thread(runner.invoke, cli.app, [*base, "--confirmar"])
    assert r.exit_code == 0 and "0 compra(s) importada(s)." in r.output
    assert "já existentes: 6" in r.output and await total() == 6

    for args, trecho in (
        ([*base[:3], "Ninguém"], "não encontrado"),
        (["importar-fatura", str(regras), "--membro", "Carlos"], "abrir a planilha"),
        ([*base[:4], "--regras", str(tmp_path / "x.toml")], "ler as regras"),
    ):
        r = await asyncio.to_thread(runner.invoke, cli.app, args)
        assert r.exit_code == 1 and trecho in r.output, r.output


def test_equivalencia_de_descricao_truncada() -> None:
    # O formato colunado guarda 23 caracteres do nome; o colado, 22.
    assert imp._chave_descricao("Seguros Exemplo Cia SGx") == imp._chave_descricao(
        "Seguros Exemplo Cia SG"
    )
    assert imp._chave_descricao("Servicos* Passadoria#1") != imp._chave_descricao(
        "Servicos* Passadoria#0"
    )
