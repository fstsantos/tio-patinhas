"""Login e logout."""

from __future__ import annotations

import secrets

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from starlette.datastructures import FormData

from financas.db.session import transacao
from financas.web import auth
from financas.web.app import estado, formulario, redirecionar, render

router = APIRouter()


def _proximo_seguro(valor: str | None) -> str:
    if valor and valor.startswith("/") and not valor.startswith("//") and "\\" not in valor:
        return valor
    return "/"


def _pagina_login(
    request: Request, *, erro: str | None = None, status: int = 200, login: str = ""
) -> HTMLResponse:
    token = secrets.token_urlsafe(32)
    resposta = render(
        request,
        "login.html",
        None,
        status=status,
        erro=erro,
        login=login,
        csrf_login=token,
        proximo=_proximo_seguro(request.query_params.get("proximo")),
    )
    resposta.set_cookie(
        auth.COOKIE_CSRF_LOGIN,
        token,
        httponly=True,
        samesite="lax",
        secure=estado(request).settings.em_producao,
        max_age=3600,
    )
    return resposta


@router.get("/login", response_class=HTMLResponse)
async def login_form(request: Request) -> Response:
    e = estado(request)
    async with e.fabrica() as s:
        if await auth.carregar_usuario(s, e.clock, request.cookies.get(auth.COOKIE_SESSAO)):
            return redirecionar("/")
    return _pagina_login(request)


@router.post("/login", response_class=HTMLResponse)
async def login(request: Request) -> Response:
    e = estado(request)
    form = await request.form()
    login_txt = str(form.get("login", ""))
    if not auth.tokens_iguais(
        str(form.get(auth.CAMPO_CSRF, "")), request.cookies.get(auth.COOKIE_CSRF_LOGIN)
    ):
        return _pagina_login(
            request, erro="Sessão expirada. Tente novamente.", status=403, login=login_txt
        )
    ip = auth.ip_cliente(request)
    async with transacao(e.fabrica) as s:
        if await auth.bloqueado(s, e.clock, login_txt, ip):
            return _pagina_login(
                request,
                status=429,
                login=login_txt,
                erro="Muitas tentativas. Aguarde 15 minutos e tente de novo.",
            )
        membro = await auth.autenticar(s, e.clock, login_txt, str(form.get("senha", "")), ip)
        if membro is None:
            erro = "Login ou senha inválidos."
        else:
            token = await auth.criar_sessao(
                s,
                e.clock,
                membro,
                horas=e.settings.web_sessao_horas,
                ip=ip,
                user_agent=request.headers.get("user-agent"),
            )
    if membro is None:
        return _pagina_login(request, erro=erro, status=401, login=login_txt)
    resposta = redirecionar(_proximo_seguro(str(form.get("proximo", "/"))))
    resposta.set_cookie(
        auth.COOKIE_SESSAO,
        token,
        httponly=True,
        samesite="lax",
        secure=e.settings.em_producao,
        max_age=e.settings.web_sessao_horas * 3600,
    )
    resposta.delete_cookie(auth.COOKIE_CSRF_LOGIN)
    return resposta


@router.post("/sair")
async def sair(request: Request, _form: FormData = Depends(formulario)) -> Response:
    e = estado(request)
    async with transacao(e.fabrica) as s:
        await auth.revogar(s, e.clock, request.cookies.get(auth.COOKIE_SESSAO))
    resposta = redirecionar("/login")
    resposta.delete_cookie(auth.COOKIE_SESSAO)
    return resposta
