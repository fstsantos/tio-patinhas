"""Categorias (somente admin)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from starlette.datastructures import FormData

from financas.db.models import Membro, TipoCategoria
from financas.db.session import transacao
from financas.domain.erros import ErroDominio, NaoEncontrado
from financas.services import categorias
from financas.web.app import admin_atual, estado, formulario, redirecionar, render
from financas.web.auth import Usuario

router = APIRouter(prefix="/categorias")


async def _pagina(
    request: Request, usuario: Usuario, *, erro: str | None = None, status: int = 200
) -> HTMLResponse:
    async with estado(request).fabrica() as s:
        lista = await categorias.listar(s)
    return render(
        request,
        "categorias/lista.html",
        usuario,
        categorias=lista,
        erro=erro,
        tipos=list(TipoCategoria),
        status=status,
    )


@router.get("", response_class=HTMLResponse)
async def listar(request: Request, usuario: Usuario = Depends(admin_atual)) -> HTMLResponse:
    return await _pagina(request, usuario)


@router.post("", response_class=HTMLResponse)
@router.post("/{categoria_id}", response_class=HTMLResponse)
async def salvar(
    request: Request,
    categoria_id: int | None = None,
    usuario: Usuario = Depends(admin_atual),
    form: FormData = Depends(formulario),
) -> Response:
    e = estado(request)
    tipo_txt = str(form.get("tipo", ""))
    tipo = TipoCategoria(tipo_txt) if tipo_txt in TipoCategoria.__members__ else None
    if tipo is None:
        return await _pagina(request, usuario, erro="Escolha o tipo da categoria.", status=422)
    try:
        async with transacao(e.fabrica) as s:
            ator = await s.get(Membro, usuario.membro.id)
            assert ator is not None
            nome = str(form.get("nome", ""))
            if categoria_id is None:
                await categorias.criar(s, e.clock, ator, nome, tipo)
            else:
                await categorias.atualizar(
                    s,
                    e.clock,
                    ator,
                    categoria_id,
                    nome=nome,
                    tipo=tipo,
                    ativa=bool(form.get("ativa")),
                )
    except NaoEncontrado:
        raise
    except ErroDominio as ex:
        return await _pagina(request, usuario, erro=str(ex), status=422)
    return redirecionar("/categorias?msg=categoria_salva")
