"""Web: autenticação, CSRF, permissões no backend, telas e igualdade com o bot."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from financas.bot.processador import Mensagem, Processador
from financas.config import Settings
from financas.db.models import Lancamento, Membro, Papel
from financas.services import lancamentos as svc_lanc
from financas.services import senhas
from financas.web.app import criar_app
from tests import factories as f

pytestmark = [pytest.mark.integration, pytest.mark.usefixtures("limpar_banco")]

HOJE = date(2026, 3, 20)
SENHA = "senha-bem-forte-1"


@pytest.fixture
def clock():  # type: ignore[no-untyped-def]
    return f.relogio(HOJE)


@pytest.fixture
async def pessoas(fabrica: async_sessionmaker[AsyncSession]) -> dict[str, int]:
    async with fabrica() as s, s.begin():
        ids = {}
        for nome, papel, tg in (
            ("Adm", Papel.ADMIN, 1),
            ("Ana", Papel.MEMBRO, 2),
            ("Bia", Papel.MEMBRO, 3),
        ):
            m = Membro(
                nome=nome,
                papel=papel.value,
                ativo=True,
                telegram_user_id=tg,
                login_web=nome.lower(),
                senha_hash=senhas.gerar_hash(SENHA),
            )
            s.add(m)
            await s.flush()
            ids[nome.lower()] = m.id
        return ids


@pytest.fixture
async def cliente(
    fabrica: async_sessionmaker[AsyncSession], clock
) -> AsyncIterator[httpx.AsyncClient]:  # type: ignore[no-untyped-def]
    app = criar_app(Settings(_env_file=None), fabrica=fabrica, clock=clock)  # type: ignore[call-arg]
    transporte = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transporte, base_url="http://teste") as c:
        yield c


def _csrf(html: str) -> str:
    m = re.search(r'name="csrf_token" value="([^"]+)"', html)
    assert m, "csrf não encontrado"
    return m.group(1)


async def entrar(c: httpx.AsyncClient, login: str, senha: str = SENHA) -> httpx.Response:
    pagina = await c.get("/login")
    return await c.post(
        "/login",
        data={"login": login, "senha": senha, "csrf_token": _csrf(pagina.text), "proximo": "/"},
    )


async def csrf_de(c: httpx.AsyncClient, url: str = "/lancamentos/novo") -> str:
    return _csrf((await c.get(url)).text)


# ------------------------------------------------------------------ autenticação


async def test_health_sem_login(cliente: httpx.AsyncClient) -> None:
    r = await cliente.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}
    assert (await cliente.get("/saude")).status_code == 404


async def test_login_e_sessao(cliente: httpx.AsyncClient, pessoas: dict[str, int]) -> None:
    r = await cliente.get("/")
    assert r.status_code == 303 and r.headers["location"] == "/login"
    r = await entrar(cliente, "ana")
    assert r.status_code == 303
    cookie = r.headers["set-cookie"]
    assert "HttpOnly" in cookie and "samesite=lax" in cookie.lower()
    r = await cliente.get("/")
    assert r.status_code == 200 and "Saldo do mês" in r.text
    assert r.headers["x-frame-options"] == "DENY"
    assert "default-src 'self'" in r.headers["content-security-policy"]


async def test_login_invalido_e_limite(cliente: httpx.AsyncClient, pessoas: dict[str, int]) -> None:
    for _ in range(5):
        r = await entrar(cliente, "ana", "errada-errada")
        assert r.status_code == 401 and "inválidos" in r.text
    r = await entrar(cliente, "ana")  # senha certa, mas bloqueado
    assert r.status_code == 429
    r = await entrar(cliente, "bia")  # outro login no mesmo IP ainda pode
    assert r.status_code == 303


async def test_login_sem_csrf(cliente: httpx.AsyncClient, pessoas: dict[str, int]) -> None:
    r = await cliente.post("/login", data={"login": "ana", "senha": SENHA})
    assert r.status_code == 403


async def test_inativo_nao_entra(
    cliente: httpx.AsyncClient, pessoas: dict[str, int], fabrica: async_sessionmaker[AsyncSession]
) -> None:
    async with fabrica() as s, s.begin():
        ana = await s.get(Membro, pessoas["ana"])
        assert ana is not None
        ana.ativo = False
    assert (await entrar(cliente, "ana")).status_code == 401


async def test_logout_revoga(cliente: httpx.AsyncClient, pessoas: dict[str, int]) -> None:
    await entrar(cliente, "ana")
    token = cliente.cookies.get("tp_sessao")
    r = await cliente.post("/sair", data={"csrf_token": await csrf_de(cliente)})
    assert r.status_code == 303
    cliente.cookies.set("tp_sessao", token or "")
    assert (await cliente.get("/")).status_code == 303


async def test_redirecionamento_htmx_e_proximo(cliente: httpx.AsyncClient) -> None:
    r = await cliente.get("/lancamentos", headers={"HX-Request": "true"})
    assert r.status_code == 204 and r.headers["HX-Redirect"] == "/login?proximo=/lancamentos"


# ------------------------------------------------------------------ CSRF e permissões


async def test_post_sem_csrf_rejeitado(cliente: httpx.AsyncClient, pessoas: dict[str, int]) -> None:
    await entrar(cliente, "ana")
    dados = {"tipo": "DEBITO", "valor": "10,00", "descricao": "x", "data": "2026-03-20"}
    assert (await cliente.post("/lancamentos", data=dados)).status_code == 403
    dados["csrf_token"] = "forjado"
    assert (await cliente.post("/lancamentos", data=dados)).status_code == 403


async def test_membro_nao_acessa_admin(cliente: httpx.AsyncClient, pessoas: dict[str, int]) -> None:
    await entrar(cliente, "ana")
    token = await csrf_de(cliente)
    assert (await cliente.get("/membros")).status_code == 403
    assert (await cliente.get("/categorias")).status_code == 403
    r = await cliente.post(
        "/membros",
        data={"csrf_token": token, "nome": "X", "papel": "admin", "ativo": "1", "telegram": "77"},
    )
    assert r.status_code == 403
    r = await cliente.post(
        "/categorias", data={"csrf_token": token, "nome": "X", "tipo": "DESPESA"}
    )
    assert r.status_code == 403


async def _criar(c: httpx.AsyncClient, **dados: str) -> int:
    base = {"tipo": "DEBITO", "valor": "10,00", "descricao": "mercado", "data": "2026-03-20"}
    r = await c.post("/lancamentos", data={**base, **dados, "csrf_token": await csrf_de(c)})
    assert r.status_code == 303, r.text
    return int(r.headers["location"].split("/")[2].split("?")[0])


async def test_criar_editar_cancelar_proprio(
    cliente: httpx.AsyncClient, pessoas: dict[str, int], fabrica: async_sessionmaker[AsyncSession]
) -> None:
    await entrar(cliente, "ana")
    lid = await _criar(
        cliente,
        tipo="CREDITO",
        valor="1.000,00",
        parcelas="10",
        descricao="TV <script>alert(1)</script>",
    )
    r = await cliente.get(f"/lancamentos/{lid}?msg=criado")
    assert "Lançamento registrado." in r.text
    assert "<script>alert(1)</script>" not in r.text and "&lt;script&gt;" in r.text
    assert "abr/2026" in r.text and "jan/2027" in r.text
    assert "Histórico de alterações" not in r.text  # auditoria só para admin

    token = await csrf_de(cliente, f"/lancamentos/{lid}/editar")
    r = await cliente.post(
        f"/lancamentos/{lid}/editar",
        data={
            "csrf_token": token,
            "valor": "1.200,00",
            "parcelas": "12",
            "descricao": "TV",
            "data": "2026-03-20",
            "revisao": "1",
        },
    )
    assert r.status_code == 303
    # edição concorrente com revisão antiga
    r = await cliente.post(
        f"/lancamentos/{lid}/editar",
        data={
            "csrf_token": token,
            "valor": "1.300,00",
            "parcelas": "12",
            "descricao": "TV",
            "data": "2026-03-20",
            "revisao": "1",
        },
    )
    assert r.status_code == 422 and "alterado por outra pessoa" in r.text
    r = await cliente.get(f"/lancamentos/{lid}")
    assert "parcelas substituídas" in r.text

    r = await cliente.post(
        f"/lancamentos/{lid}/cancelar", data={"csrf_token": token, "motivo": "teste"}
    )
    assert r.status_code == 303
    async with fabrica() as s:
        lanc = await s.get(Lancamento, lid)
        assert lanc is not None and lanc.status == "CANCELADO" and lanc.origem == "WEB"
    # membro não vê cancelados
    assert (await cliente.get(f"/lancamentos/{lid}")).status_code == 404
    r = await cliente.get("/lancamentos?status=CANCELADO")
    assert "#" + str(lid) not in r.text


async def test_membro_nao_altera_de_outro(
    cliente: httpx.AsyncClient, pessoas: dict[str, int], fabrica: async_sessionmaker[AsyncSession]
) -> None:
    await entrar(cliente, "bia")
    lid = await _criar(cliente)
    cliente.cookies.clear()
    await entrar(cliente, "ana")
    token = await csrf_de(cliente)
    r = await cliente.get(f"/lancamentos/{lid}")
    assert r.status_code == 200 and "Cancelar lançamento" not in r.text  # pode consultar
    assert (await cliente.get(f"/lancamentos/{lid}/editar")).status_code == 403
    r = await cliente.post(f"/lancamentos/{lid}/cancelar", data={"csrf_token": token})
    assert r.status_code == 403
    r = await cliente.post(
        f"/lancamentos/{lid}/editar",
        data={
            "csrf_token": token,
            "valor": "1,00",
            "descricao": "x",
            "data": "2026-03-20",
            "revisao": "1",
        },
    )
    assert r.status_code == 403
    r = await cliente.post(
        f"/lancamentos/{lid}/restaurar", data={"csrf_token": token, "motivo": "x"}
    )
    assert r.status_code == 403
    # titular forjado: membro não lança em nome de outro (campo ignorado)
    lid2 = await _criar(cliente, titular=str(pessoas["bia"]))
    async with fabrica() as s:
        lanc = await s.get(Lancamento, lid2)
        assert lanc is not None and lanc.membro_id == pessoas["ana"]


async def test_admin_lanca_para_outro_e_restaura(
    cliente: httpx.AsyncClient, pessoas: dict[str, int], fabrica: async_sessionmaker[AsyncSession]
) -> None:
    await entrar(cliente, "adm")
    lid = await _criar(cliente, titular=str(pessoas["ana"]))
    async with fabrica() as s:
        lanc = await s.get(Lancamento, lid)
        assert lanc is not None and (lanc.membro_id, lanc.criado_por) == (
            pessoas["ana"],
            pessoas["adm"],
        )
    token = await csrf_de(cliente)
    await cliente.post(f"/lancamentos/{lid}/cancelar", data={"csrf_token": token})
    r = await cliente.post(
        f"/lancamentos/{lid}/restaurar", data={"csrf_token": token, "motivo": ""}
    )
    assert r.status_code == 422 and "motivo" in r.text
    r = await cliente.post(
        f"/lancamentos/{lid}/restaurar", data={"csrf_token": token, "motivo": "engano"}
    )
    assert r.status_code == 303
    r = await cliente.get(f"/lancamentos/{lid}")
    assert "Histórico de alterações" in r.text and "Restauracao" in r.text
    r = await cliente.get("/lancamentos?status=TODOS")
    assert f"#{lid}" in r.text


async def test_formulario_invalido(cliente: httpx.AsyncClient, pessoas: dict[str, int]) -> None:
    await entrar(cliente, "ana")
    token = await csrf_de(cliente)
    r = await cliente.post(
        "/lancamentos",
        data={
            "csrf_token": token,
            "tipo": "DEBITO",
            "valor": "10.50",
            "descricao": "",
            "data": "2026-03-20",
        },
    )
    assert r.status_code == 422
    assert "Você quis dizer 10,50?" in r.text and "Informe a descrição" in r.text
    r = await cliente.post(
        "/lancamentos",
        data={
            "csrf_token": token,
            "tipo": "DEBITO",
            "valor": "10",
            "descricao": "x",
            "data": "2026-03-21",
        },
    )
    assert r.status_code == 422 and "futuras" in r.text


async def test_listagem_filtros_e_htmx(cliente: httpx.AsyncClient, pessoas: dict[str, int]) -> None:
    await entrar(cliente, "ana")
    await _criar(cliente, descricao="Farmácia")
    await _criar(cliente, descricao="Mercado", tipo="RECEITA")
    r = await cliente.get("/lancamentos?texto=farmacia")
    assert "Farmácia" in r.text and "Mercado" not in r.text.split("<tbody>")[1]
    r = await cliente.get("/lancamentos?tipo=RECEITA", headers={"HX-Request": "true"})
    assert "<html" not in r.text and "1 lançamento(s)" in r.text


# ------------------------------------------------------------------ membros e categorias


async def test_admin_gerencia_membros(
    cliente: httpx.AsyncClient, pessoas: dict[str, int], fabrica: async_sessionmaker[AsyncSession]
) -> None:
    await entrar(cliente, "adm")
    token = await csrf_de(cliente, "/membros/novo")
    r = await cliente.post(
        "/membros",
        data={
            "csrf_token": token,
            "nome": "Caio",
            "telegram": "44",
            "papel": "membro",
            "ativo": "1",
        },
    )
    assert r.status_code == 303
    r = await cliente.post(
        "/membros",
        data={
            "csrf_token": token,
            "nome": "Dup",
            "telegram": "44",
            "papel": "membro",
            "ativo": "1",
        },
    )
    assert r.status_code == 422 and "já pertence a Caio" in r.text
    r = await cliente.post(
        "/membros", data={"csrf_token": token, "nome": "SemTg", "papel": "membro", "ativo": "1"}
    )
    assert r.status_code == 422 and "id do Telegram" in r.text
    # último admin não pode se rebaixar
    r = await cliente.post(
        f"/membros/{pessoas['adm']}",
        data={
            "csrf_token": token,
            "nome": "Adm",
            "telegram": "1",
            "papel": "membro",
            "ativo": "1",
            "login": "adm",
        },
    )
    assert r.status_code == 422 and "pelo menos um administrador" in r.text
    # desativar membro: lista mostra bloqueio no bot e lançamentos ficam
    r = await cliente.post(
        f"/membros/{pessoas['ana']}",
        data={
            "csrf_token": token,
            "nome": "Ana",
            "telegram": "2",
            "papel": "membro",
            "login": "ana",
        },
    )
    assert r.status_code == 303
    r = await cliente.get("/membros")
    assert "Bloqueado (inativo)" in r.text and "Habilitado" in r.text


async def test_admin_gerencia_categorias(
    cliente: httpx.AsyncClient, pessoas: dict[str, int]
) -> None:
    await entrar(cliente, "adm")
    token = await csrf_de(cliente, "/categorias")
    r = await cliente.post(
        "/categorias", data={"csrf_token": token, "nome": "Mercado", "tipo": "DESPESA"}
    )
    assert r.status_code == 303
    r = await cliente.post(
        "/categorias", data={"csrf_token": token, "nome": "mercado", "tipo": "AMBAS"}
    )
    assert r.status_code == 422 and "Já existe" in r.text
    r = await cliente.get("/categorias")
    assert 'value="Mercado"' in r.text


# ------------------------------------------------------------------ bot × web


async def test_numeros_identicos_bot_e_web(
    cliente: httpx.AsyncClient,
    pessoas: dict[str, int],
    fabrica: async_sessionmaker[AsyncSession],
    clock,
) -> None:  # type: ignore[no-untyped-def]
    bot = Processador(fabrica, clock, chats_permitidos={-1})
    n = iter(range(1, 100))

    async def enviar(texto: str, user: int = 2) -> str:
        r = await bot.texto(Mensagem(-1, False, next(n), user, texto))
        assert r is not None
        return "\n".join(r.textos)

    await enviar("ganho 3500 salário 05/03")
    await enviar("crédito 300 3 geladeira 10/02")
    await enviar("crédito 1000 10x tv")
    await entrar(cliente, "bia")
    await _criar(cliente, valor="150,00", descricao="mercado")
    texto_bot = await enviar("resumo mês")
    html = (await cliente.get("/")).text
    for esperado in (
        "R$ 3.500,00",
        "R$ 150,00",
        "R$ 100,00",
        "R$ 3.250,00",
        "R$ 270,83",
        "R$ 1.000,00",
    ):
        assert esperado in texto_bot, esperado
        assert esperado in html, esperado
    # cancelamento pela web reflete no bot
    async with fabrica() as s:
        geladeira = await s.scalar(select(Lancamento.id).where(Lancamento.descricao == "geladeira"))
    cliente.cookies.clear()
    await entrar(cliente, "adm")
    await cliente.post(
        f"/lancamentos/{geladeira}/cancelar", data={"csrf_token": await csrf_de(cliente)}
    )
    texto_bot = await enviar("resumo mês")
    html = (await cliente.get("/")).text
    assert "R$ 3.350,00" in texto_bot and "R$ 3.350,00" in html


async def test_credito_com_mes_da_primeira_parcela(
    cliente: httpx.AsyncClient, pessoas: dict[str, int], fabrica: async_sessionmaker[AsyncSession]
) -> None:
    await entrar(cliente, "ana")
    lid = await _criar(
        cliente,
        tipo="CREDITO",
        valor="300,00",
        parcelas="3",
        primeira_competencia="2026-03",
        data="2026-03-02",
    )
    r = await cliente.get(f"/lancamentos/{lid}")
    assert "1ª parcela" in r.text and "(informada)" in r.text
    async with fabrica() as s:
        parcelas = await svc_lanc.parcelas_vigentes(s, lid)
        assert [p.competencia for p in parcelas] == [
            date(2026, 3, 1),
            date(2026, 4, 1),
            date(2026, 5, 1),
        ]
    # edição exibe o mês e, sem mexer nele, não reparcela
    r = await cliente.get(f"/lancamentos/{lid}/editar")
    assert 'value="2026-03"' in r.text
    token = _csrf(r.text)
    r = await cliente.post(
        f"/lancamentos/{lid}/editar",
        data={
            "csrf_token": token,
            "valor": "300,00",
            "parcelas": "3",
            "primeira_competencia": "2026-03",
            "descricao": "loja",
            "data": "2026-03-02",
            "revisao": "1",
        },
    )
    assert r.status_code == 303
    async with fabrica() as s:
        assert {p.revisao_lancamento for p in await svc_lanc.parcelas_vigentes(s, lid)} == {1}


@pytest.mark.parametrize(
    ("mes", "mensagem"), [("2026-07", "1ª parcela inválido"), ("março", "aaaa-mm")]
)
async def test_mes_da_primeira_parcela_invalido(
    cliente: httpx.AsyncClient, pessoas: dict[str, int], mes: str, mensagem: str
) -> None:
    await entrar(cliente, "ana")
    dados = {
        "tipo": "CREDITO",
        "valor": "300,00",
        "parcelas": "3",
        "primeira_competencia": mes,
        "descricao": "loja",
        "data": "2026-03-02",
        "csrf_token": await csrf_de(cliente),
    }
    r = await cliente.post("/lancamentos", data=dados)
    assert r.status_code == 422 and mensagem in r.text
