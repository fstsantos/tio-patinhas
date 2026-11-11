"""Web: autenticação, CSRF, permissões no backend, telas e igualdade com o bot."""

from __future__ import annotations

import re
from collections.abc import AsyncIterator
from datetime import date
from decimal import Decimal
from pathlib import Path

import httpx
import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from financas.bot.processador import Mensagem, Processador
from financas.config import Settings
from financas.db.models import Categoria, Lancamento, Membro, Papel, Recorrencia
from financas.services import lancamentos as svc_lanc
from financas.services import senhas
from financas.web import app as web_app
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
    # Data futura (o relógio do teste está em 20/03/2026) é aceita.
    assert r.status_code == 303


async def test_listagem_filtros_e_htmx(cliente: httpx.AsyncClient, pessoas: dict[str, int]) -> None:
    await entrar(cliente, "ana")
    await _criar(cliente, descricao="Farmácia")
    await _criar(cliente, descricao="Mercado", tipo="RECEITA")
    r = await cliente.get("/lancamentos?texto=farmacia")
    assert "Farmácia" in r.text and "Mercado" not in r.text.split("<tbody>")[1]
    r = await cliente.get("/lancamentos?tipo=RECEITA", headers={"HX-Request": "true"})
    assert "<html" not in r.text and "1 lançamento(s)" in r.text


def _codigos(html: str) -> list[int]:
    """Códigos na ordem em que aparecem nas linhas da tabela."""
    corpo = html.split("<tbody>")[1].split("</tbody>", maxsplit=1)[0]
    return [int(x) for x in re.findall(r'href="/lancamentos/(\d+)"', corpo)]


async def test_listagem_ordenacao(cliente: httpx.AsyncClient, pessoas: dict[str, int]) -> None:
    await entrar(cliente, "adm")
    ana, bia = str(pessoas["ana"]), str(pessoas["bia"])
    a = await _criar(cliente, descricao="Aluguel", valor="50,00", data="2026-03-10", titular=bia)
    b = await _criar(
        cliente, descricao="Bônus", valor="900,00", data="2026-03-05", tipo="RECEITA", titular=ana
    )
    c = await _criar(
        cliente,
        descricao="Celular",
        valor="300,00",
        data="2026-03-15",
        tipo="CREDITO",
        parcelas="3",
        titular=ana,
    )

    async def ordem(consulta: str) -> list[int]:
        r = await cliente.get(f"/lancamentos?{consulta}")
        assert r.status_code == 200
        return _codigos(r.text)

    # Padrão e parâmetros inválidos: data, mais recente primeiro.
    assert await ordem("") == [c, a, b]
    assert await ordem("ordem=senha_hash&dir=qualquer") == [c, a, b]
    assert await ordem("ordem=data&dir=asc") == [b, a, c]
    assert await ordem("ordem=codigo&dir=asc") == [a, b, c]
    assert await ordem("ordem=codigo") == [c, b, a]  # sem sentido: números do maior ao menor
    assert await ordem("ordem=valor&dir=asc") == [a, c, b]
    assert await ordem("ordem=valor&dir=desc") == [b, c, a]
    assert await ordem("ordem=tipo") == [c, a, b]  # Crédito, Débito, Receita
    assert await ordem("ordem=descricao") == [a, b, c]  # Aluguel, Bônus, Celular
    assert await ordem("ordem=descricao&dir=desc") == [c, b, a]
    assert await ordem("ordem=titular") == [c, b, a]  # Ana (mais recente primeiro), Bia
    assert await ordem("ordem=titular&dir=desc") == [a, c, b]
    assert await ordem("ordem=origem") == [c, a, b]  # todos da web: vale o desempate por data

    # A ordenação convive com os filtros, o cabeçalho inverte o sentido e a marca aparece.
    r = await cliente.get("/lancamentos?tipo=DEBITO&ordem=valor&dir=asc")
    assert _codigos(r.text) == [a]
    assert 'aria-sort="ascending"' in r.text
    assert 'href="/lancamentos?tipo=DEBITO&amp;status=ATIVO&amp;ordem=valor&amp;dir=desc"' in r.text
    assert (
        'href="/lancamentos?tipo=DEBITO&amp;status=ATIVO&amp;ordem=titular&amp;dir=asc"' in r.text
    )
    assert '<input type="hidden" name="ordem" value="valor" form="filtros">' in r.text


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


# ------------------------------------------------------------------ regressões


async def test_editar_mantendo_categoria_desativada(
    cliente: httpx.AsyncClient, pessoas: dict[str, int], fabrica: async_sessionmaker[AsyncSession]
) -> None:
    """Desativar a categoria não pode impedir a edição de quem já a usava."""
    async with fabrica() as s, s.begin():
        cat = await f.categoria(s, "Mercado")
        outra = await f.categoria(s, "Lazer")
    await entrar(cliente, "ana")
    lid = await _criar(cliente, categoria=str(cat.id))
    async with fabrica() as s, s.begin():
        for c in (cat, outra):
            (await s.get(Categoria, c.id)).ativa = False  # type: ignore[union-attr]

    base = {"valor": "12,00", "descricao": "mercado", "data": "2026-03-20", "revisao": "1"}
    token = await csrf_de(cliente, f"/lancamentos/{lid}/editar")
    r = await cliente.post(
        f"/lancamentos/{lid}/editar",
        data={**base, "categoria": str(cat.id), "csrf_token": token},
    )
    assert r.status_code == 303, r.text
    async with fabrica() as s:
        lanc = await s.get(Lancamento, lid)
        assert lanc is not None
        assert (lanc.valor_total, lanc.categoria_id) == (Decimal("12.00"), cat.id)

    # Trocar para outra categoria inativa continua proibido.
    r = await cliente.post(
        f"/lancamentos/{lid}/editar",
        data={**base, "revisao": "2", "categoria": str(outra.id), "csrf_token": token},
    )
    assert r.status_code == 404
    assert "inativa" in r.text


@pytest.mark.parametrize("mes", ["0001-01", "9999-12", "12/9999", "1999-12", "2101-01", "abc"])
async def test_dashboard_mes_fora_do_intervalo_usa_o_atual(
    cliente: httpx.AsyncClient, pessoas: dict[str, int], mes: str
) -> None:
    await entrar(cliente, "ana")
    r = await cliente.get("/", params={"mes": mes})
    assert r.status_code == 200
    assert "Março de 2026" in r.text


async def test_js_alterna_todos_os_campos_de_credito() -> None:
    """O formulário tem dois campos só de crédito (parcelas e mês da 1ª parcela)."""
    raiz = Path(web_app.__file__).parent
    form = (raiz / "templates/lancamentos/form.html").read_text(encoding="utf-8")
    js = (raiz / "static/app.js").read_text(encoding="utf-8")
    assert form.count("data-parcelas") == 2
    assert 'querySelectorAll("[data-parcelas]")' in js
    assert 'querySelector("[data-parcelas]")' not in js


async def test_alternador_de_tema(cliente: httpx.AsyncClient, pessoas: dict[str, int]) -> None:
    """O botão de modo escuro aparece no login e no topo, e tema.js carrega no <head>."""
    login = await cliente.get("/login")
    assert "data-alternar-tema" in login.text and 'class="tema-solto"' in login.text
    await entrar(cliente, "ana")
    r = await cliente.get("/")
    assert r.text.count("data-alternar-tema") == 1 and 'class="tema-solto"' not in r.text
    cabeca = r.text.split("</head>")[0]
    # Sem defer (evita piscar) e com versão na URL (evita CSS/JS antigo do cache).
    assert re.search(r'<script src="/static/tema\.js\?v=[0-9a-f]{10}"></script>', cabeca)
    assert re.search(r'href="/static/app\.css\?v=[0-9a-f]{10}"', cabeca)
    js = await cliente.get("/static/tema.js")
    assert js.status_code == 200 and "localStorage" in js.text
    css = (Path(web_app.__file__).parent / "static/app.css").read_text(encoding="utf-8")
    assert ':root[data-tema="escuro"]' in css


# ------------------------------------------------------------------ recorrências


async def _recorrencia(c: httpx.AsyncClient, **dados: str) -> httpx.Response:
    base = {"tipo": "DEBITO", "valor": "1.500,00", "descricao": "Aluguel", "dia": "25"}
    token = await csrf_de(c, "/recorrentes/nova")
    return await c.post("/recorrentes", data={**base, **dados, "csrf_token": token})


async def test_recorrencia_criar_editar_encerrar(
    cliente: httpx.AsyncClient, pessoas: dict[str, int], fabrica: async_sessionmaker[AsyncSession]
) -> None:
    await entrar(cliente, "ana")
    r = await _recorrencia(cliente)
    assert r.status_code == 303 and r.headers["location"] == "/recorrentes?msg=recorrencia_salva"
    async with fabrica() as s:
        lanc = (await s.execute(select(Lancamento))).scalar_one()
    # O deste mês já foi lançado, com a data futura do dia 25.
    assert (lanc.data_operacao, lanc.valor_total) == (date(2026, 3, 25), Decimal("1500.00"))
    assert (lanc.membro_id, lanc.origem) == (pessoas["ana"], "RECORRENCIA")
    rid = lanc.recorrencia_id

    r = await cliente.get("/recorrentes")
    assert "Aluguel" in r.text and "25/04/2026" in r.text  # próximo lançamento
    r = await cliente.get(f"/lancamentos/{lanc.id}")
    assert "Recorrência" in r.text and "vale só para este mês" in r.text
    assert "R$ 1.500,00" in (await cliente.get("/")).text  # já conta no mês

    pagina = await cliente.get(f"/recorrentes/{rid}/editar")
    assert 'value="1.500,00"' in pagina.text
    r = await cliente.post(
        f"/recorrentes/{rid}/editar",
        data={
            "csrf_token": _csrf(pagina.text),
            "valor": "1.600,00",
            "descricao": "Aluguel",
            "dia": "25",
            "fim": "2026-12",
        },
    )
    assert r.status_code == 303, r.text
    async with fabrica() as s:
        rec = await s.get_one(Recorrencia, rid)
    assert (rec.valor, rec.fim, rec.inicio) == (
        Decimal("1600.00"),
        date(2026, 12, 1),
        date(2026, 3, 1),
    )

    token = _csrf((await cliente.get("/recorrentes")).text)
    r = await cliente.post(f"/recorrentes/{rid}/encerrar", data={"csrf_token": token})
    assert r.headers["location"] == "/recorrentes?msg=recorrencia_encerrada"
    r = await cliente.get("/recorrentes")
    assert "Encerradas" in r.text and "Reativar" in r.text
    r = await cliente.post(f"/recorrentes/{rid}/reativar", data={"csrf_token": token})
    assert r.headers["location"] == "/recorrentes?msg=recorrencia_reativada"
    assert (
        await cliente.post(f"/recorrentes/{rid}/apagar", data={"csrf_token": token})
    ).status_code == 404
    async with fabrica() as s:
        assert len((await s.execute(select(Lancamento))).scalars().all()) == 1


async def test_recorrencia_formulario_invalido_e_permissoes(
    cliente: httpx.AsyncClient, pessoas: dict[str, int], fabrica: async_sessionmaker[AsyncSession]
) -> None:
    await entrar(cliente, "bia")
    r = await _recorrencia(cliente, tipo="CREDITO", valor="10.5", dia="", fim="x")
    assert r.status_code == 422
    for trecho in ("Escolha o tipo.", "Informe o dia do mês", "formato aaaa-mm"):
        assert trecho in r.text
    assert (await _recorrencia(cliente, dia="40")).status_code == 422
    assert (await _recorrencia(cliente, inicio="2026-02")).status_code == 422
    # Membro não escolhe titular: o campo é ignorado.
    assert (await _recorrencia(cliente, titular=str(pessoas["ana"]))).status_code == 303
    async with fabrica() as s:
        rec = (await s.execute(select(Recorrencia))).scalar_one()
    assert rec.membro_id == pessoas["bia"]

    cliente.cookies.clear()
    await entrar(cliente, "ana")
    r = await cliente.get("/recorrentes")
    assert "Aluguel" in r.text and f"/recorrentes/{rec.id}/editar" not in r.text  # só consulta
    token = _csrf(r.text)
    assert (await cliente.get(f"/recorrentes/{rec.id}/editar")).status_code == 403
    r = await cliente.post(f"/recorrentes/{rec.id}/encerrar", data={"csrf_token": token})
    assert r.status_code == 403
    assert (await cliente.get("/recorrentes/999/editar")).status_code == 404
    assert (await cliente.post(f"/recorrentes/{rec.id}/encerrar")).status_code == 403  # sem CSRF

    cliente.cookies.clear()
    await entrar(cliente, "adm")
    assert (
        await _recorrencia(cliente, tipo="RECEITA", titular=str(pessoas["ana"]))
    ).status_code == 303
    assert (await cliente.get(f"/recorrentes/{rec.id}/editar")).status_code == 200


async def test_web_gera_recorrencias_na_virada_do_mes(
    cliente: httpx.AsyncClient,
    pessoas: dict[str, int],
    fabrica: async_sessionmaker[AsyncSession],
    clock,
) -> None:  # type: ignore[no-untyped-def]
    await entrar(cliente, "ana")
    assert (await _recorrencia(cliente)).status_code == 303
    estado = web_app.Estado(fabrica, clock, Settings(_env_file=None), None)  # type: ignore[call-arg,arg-type]
    assert await web_app.gerar_recorrencias(estado) == 0
    clock.definir(f.relogio(date(2026, 4, 1), hora=4).agora())  # 1h de 1º de abril em São Paulo
    assert await web_app.gerar_recorrencias(estado) == 1
    assert await web_app.gerar_recorrencias(estado) == 0
