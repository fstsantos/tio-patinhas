"""Aplicação web administrativa (FastAPI + Jinja2 + HTMX).

``uvicorn financas.web.app:criar_app --factory``. As rotas são adaptadores finos:
toda regra de negócio fica em ``financas.services``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any
from urllib.parse import quote

from fastapi import Depends, FastAPI, Request
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from starlette.datastructures import FormData

from financas.clock import Clock, RelogioSistema
from financas.config import Settings, get_settings
from financas.db.models import Origem, StatusLancamento, TipoLancamento
from financas.db.session import criar_engine, criar_fabrica
from financas.domain.calendario import FUSO, Mes
from financas.domain.dinheiro import formatar_brl
from financas.domain.erros import NaoEncontrado, SemPermissao
from financas.logging import configurar_logs
from financas.web.auth import (
    CAMPO_CSRF,
    COOKIE_SESSAO,
    CsrfInvalido,
    NaoAutenticado,
    Usuario,
    carregar_usuario,
    tokens_iguais,
)

RAIZ = Path(__file__).parent

NOMES_TIPO = {
    TipoLancamento.DEBITO.value: "Débito",
    TipoLancamento.CREDITO.value: "Crédito",
    TipoLancamento.RECEITA.value: "Receita",
}
NOMES_ORIGEM = {
    Origem.TELEGRAM_TEXTO.value: "Telegram (texto)",
    Origem.TELEGRAM_VOZ.value: "Telegram (voz)",
    Origem.WEB.value: "Web",
    Origem.CLI.value: "Linha de comando",
}
MENSAGENS = {
    "criado": "Lançamento registrado.",
    "editado": "Lançamento atualizado.",
    "cancelado": "Lançamento cancelado.",
    "restaurado": "Lançamento restaurado.",
    "membro_salvo": "Membro salvo.",
    "categoria_salva": "Categoria salva.",
}


@dataclass
class Estado:
    fabrica: async_sessionmaker[AsyncSession]
    clock: Clock
    settings: Settings
    templates: Jinja2Templates


def estado(request: Request) -> Estado:
    e: Estado = request.app.state.financas
    return e


async def usuario_atual(request: Request) -> Usuario:
    e = estado(request)
    async with e.fabrica() as s:
        usuario = await carregar_usuario(s, e.clock, request.cookies.get(COOKIE_SESSAO))
    if usuario is None:
        raise NaoAutenticado
    return usuario


async def admin_atual(usuario: Usuario = Depends(usuario_atual)) -> Usuario:
    if not usuario.eh_admin:
        raise SemPermissao("Somente administradores podem acessar esta página.")
    return usuario


async def formulario(request: Request, usuario: Usuario = Depends(usuario_atual)) -> FormData:
    """Formulário de requisição que altera estado: exige o token CSRF da sessão."""
    form = await request.form()
    enviado = form.get(CAMPO_CSRF) or request.headers.get("X-CSRF-Token")
    if not tokens_iguais(str(enviado) if enviado else None, usuario.csrf_token):
        raise CsrfInvalido
    return form


def render(
    request: Request, template: str, usuario: Usuario | None, *, status: int = 200, **contexto: Any
) -> HTMLResponse:
    e = estado(request)
    msg = MENSAGENS.get(request.query_params.get("msg", ""))
    return e.templates.TemplateResponse(
        request, template,
        {"usuario": usuario, "csrf": usuario.csrf_token if usuario else None,
         "mensagem": msg, "hoje": e.clock.hoje(), **contexto},
        status_code=status,
    )  # fmt: skip


def redirecionar(url: str) -> RedirectResponse:
    return RedirectResponse(url, status_code=303)


# ------------------------------------------------------------------ filtros


def _brl(v: Decimal | None) -> str:
    return "—" if v is None else formatar_brl(v)


def _data(d: date | None) -> str:
    return "" if d is None else f"{d:%d/%m/%Y}"


def _datahora(d: datetime | None) -> str:
    return "" if d is None else d.astimezone(FUSO).strftime("%d/%m/%Y %H:%M")


def _mes(d: date | Mes) -> str:
    return (d if isinstance(d, Mes) else Mes.de_data(d)).abreviado


def _criar_templates() -> Jinja2Templates:
    t = Jinja2Templates(directory=str(RAIZ / "templates"))
    t.env.autoescape = True
    t.env.filters.update(
        brl=_brl,
        data=_data,
        mes=_mes,
        datahora=_datahora,
        tipo=lambda v: NOMES_TIPO.get(v, v),
        origem=lambda v: NOMES_ORIGEM.get(v, v),
    )
    t.env.globals.update(StatusLancamento=StatusLancamento, TipoLancamento=TipoLancamento)
    return t


# ------------------------------------------------------------------ aplicação

CSP = (
    "default-src 'self'; script-src 'self' https://cdn.jsdelivr.net; "
    "style-src 'self'; img-src 'self' data:; connect-src 'self'; frame-ancestors 'none'; "
    "base-uri 'self'; form-action 'self'"
)


def criar_app(
    settings: Settings | None = None,
    *,
    fabrica: async_sessionmaker[AsyncSession] | None = None,
    clock: Clock | None = None,
) -> FastAPI:
    cfg = settings or get_settings()
    if fabrica is None:
        configurar_logs(cfg.log_level, json_=cfg.log_json)
        fabrica = criar_fabrica(criar_engine(cfg.database_url))
    app = FastAPI(title="Tio Patinhas", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.financas = Estado(fabrica, clock or RelogioSistema(), cfg, _criar_templates())
    app.mount("/static", StaticFiles(directory=str(RAIZ / "static")), name="static")

    @app.middleware("http")
    async def cabecalhos_seguranca(request: Request, call_next: Any) -> Response:
        resposta: Response = await call_next(request)
        resposta.headers.setdefault("Content-Security-Policy", CSP)
        resposta.headers.setdefault("X-Content-Type-Options", "nosniff")
        resposta.headers.setdefault("X-Frame-Options", "DENY")
        resposta.headers.setdefault("Referrer-Policy", "same-origin")
        if not request.url.path.startswith("/static"):
            resposta.headers.setdefault("Cache-Control", "no-store")
        return resposta

    @app.exception_handler(NaoAutenticado)
    async def _login(request: Request, _e: NaoAutenticado) -> Response:
        destino = "/login"
        if request.method == "GET" and request.url.path != "/":
            destino += "?proximo=" + quote(request.url.path)
        if request.headers.get("HX-Request"):
            return Response(status_code=204, headers={"HX-Redirect": destino})
        return redirecionar(destino)

    @app.exception_handler(CsrfInvalido)
    async def _csrf(request: Request, _e: CsrfInvalido) -> Response:
        return render(
            request,
            "erro.html",
            None,
            status=403,
            titulo="Sessão inválida",
            detalhe="O formulário expirou. Recarregue a página e tente de novo.",
        )

    @app.exception_handler(SemPermissao)
    async def _proibido(request: Request, e: SemPermissao) -> Response:
        return render(
            request, "erro.html", None, status=403, titulo="Acesso negado", detalhe=e.mensagem
        )

    @app.exception_handler(NaoEncontrado)
    async def _nao_encontrado(request: Request, e: NaoEncontrado) -> Response:
        return render(
            request, "erro.html", None, status=404, titulo="Não encontrado", detalhe=e.mensagem
        )

    @app.get("/saude", include_in_schema=False)
    async def saude() -> dict[str, str]:
        return {"status": "ok"}

    from financas.web.routes import auth, categorias, dashboard, lancamentos, membros  # noqa: PLC0415

    for modulo in (auth, dashboard, lancamentos, membros, categorias):
        app.include_router(modulo.router)
    return app
