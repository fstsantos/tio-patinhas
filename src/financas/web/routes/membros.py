"""Gestão de membros (somente admin)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from starlette.datastructures import FormData

from financas.db.models import Membro, Papel
from financas.db.session import transacao
from financas.domain.erros import ErroDominio, NaoEncontrado
from financas.services import membros
from financas.web.app import admin_atual, estado, formulario, redirecionar, render
from financas.web.auth import Usuario
from financas.web.routes.dashboard import inteiro_ou_none

router = APIRouter(prefix="/membros")


def _dados(form: FormData) -> tuple[dict[str, str], membros.DadosMembro | None, str | None]:
    v = {k: str(form.get(k, "")).strip() for k in ("nome", "telegram", "papel", "login")}
    v["ativo"] = "1" if form.get("ativo") else ""
    telegram = inteiro_ou_none(v["telegram"])
    if v["telegram"] and telegram is None:
        return v, None, "O id do Telegram deve ser numérico."
    papel = Papel(v["papel"]) if v["papel"] in {p.value for p in Papel} else Papel.MEMBRO
    senha = str(form.get("senha", "")) or None
    dados = membros.DadosMembro(
        v["nome"], papel, bool(v["ativo"]), telegram, v["login"] or None, senha
    )
    return v, dados, None


@router.get("", response_class=HTMLResponse)
async def listar(request: Request, usuario: Usuario = Depends(admin_atual)) -> HTMLResponse:
    async with estado(request).fabrica() as s:
        lista = await membros.listar(s)
    return render(request, "membros/lista.html", usuario, membros=lista)


@router.get("/novo", response_class=HTMLResponse)
async def novo(request: Request, usuario: Usuario = Depends(admin_atual)) -> HTMLResponse:
    return render(
        request,
        "membros/form.html",
        usuario,
        membro=None,
        valores={"papel": "membro", "ativo": "1"},
        erro=None,
    )


@router.get("/{membro_id}", response_class=HTMLResponse)
async def editar_form(
    request: Request, membro_id: int, usuario: Usuario = Depends(admin_atual)
) -> HTMLResponse:
    async with estado(request).fabrica() as s:
        m = await s.get(Membro, membro_id)
    if m is None:
        raise NaoEncontrado("Membro não encontrado.")
    valores = {
        "nome": m.nome,
        "telegram": str(m.telegram_user_id or ""),
        "papel": m.papel,
        "login": m.login_web or "",
        "ativo": "1" if m.ativo else "",
    }
    return render(request, "membros/form.html", usuario, membro=m, valores=valores, erro=None)


@router.post("", response_class=HTMLResponse)
@router.post("/{membro_id}", response_class=HTMLResponse)
async def salvar(
    request: Request,
    membro_id: int | None = None,
    usuario: Usuario = Depends(admin_atual),
    form: FormData = Depends(formulario),
) -> Response:
    e = estado(request)
    valores, dados, erro = _dados(form)
    membro = None
    if dados is not None:
        try:
            async with transacao(e.fabrica) as s:
                ator = await s.get(Membro, usuario.membro.id)
                assert ator is not None
                if membro_id is None:
                    await membros.criar(s, e.clock, ator, dados)
                else:
                    await membros.atualizar(s, e.clock, ator, membro_id, dados)
            return redirecionar("/membros?msg=membro_salvo")
        except NaoEncontrado:
            raise
        except ErroDominio as ex:
            erro = str(ex)
    if membro_id is not None:
        async with e.fabrica() as s:
            membro = await s.get(Membro, membro_id)
    return render(
        request, "membros/form.html", usuario, status=422, membro=membro, valores=valores, erro=erro
    )
