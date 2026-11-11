"""Recorrências mensais (receitas e despesas). Permissões validadas no serviço."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from starlette.datastructures import FormData

from financas.db.models import Membro, Recorrencia, TipoLancamento
from financas.db.session import transacao
from financas.domain.calendario import Mes
from financas.domain.dinheiro import formatar_brl, parse_valor
from financas.domain.erros import ErroDominio, NaoEncontrado, SemPermissao
from financas.services import categorias, membros
from financas.services import recorrencias as svc
from financas.web.app import estado, formulario, redirecionar, render, usuario_atual
from financas.web.auth import Usuario
from financas.web.routes.dashboard import inteiro_ou_none
from financas.web.routes.lancamentos import Formulario

router = APIRouter(prefix="/recorrentes")

TIPOS = [
    (TipoLancamento.DEBITO.value, "Despesa (débito)"),
    (TipoLancamento.RECEITA.value, "Receita"),
]
CAMPOS = ("tipo", "valor", "descricao", "dia", "inicio", "fim", "categoria", "titular")


def _mes(valor: str) -> Mes | None:
    """``aaaa-mm`` (campo ``type=month``) → mês; inválido → None."""
    try:
        ano, mes = valor.split("-")
        return Mes(int(ano), int(mes))
    except (ValueError, ErroDominio):
        return None


def _mes_campo(d: Any) -> str:
    return d.strftime("%Y-%m") if d else ""


def _ler(
    form: FormData, *, existente: Recorrencia | None = None
) -> tuple[Formulario, svc.DadosRecorrencia | None, int | None]:
    f = Formulario(valores={k: str(form.get(k, "")).strip() for k in CAMPOS})
    v = f.valores
    if existente is not None:  # não mudam na edição
        v["tipo"], v["inicio"] = existente.tipo, _mes_campo(existente.inicio)
    tipo = TipoLancamento(v["tipo"]) if v["tipo"] in dict(TIPOS) else None
    if tipo is None:
        f.erros["tipo"] = "Escolha o tipo."
    valor = None
    try:
        valor = parse_valor(v["valor"])
    except ErroDominio as e:
        f.erros["valor"] = str(e)
    if not v["descricao"]:
        f.erros["descricao"] = "Informe a descrição."
    dia = inteiro_ou_none(v["dia"])
    if dia is None:
        f.erros["dia"] = "Informe o dia do mês, de 1 a 31."
    inicio = fim = None
    if v["inicio"]:
        inicio = _mes(v["inicio"])
        if inicio is None:
            f.erros["inicio"] = "Informe o mês no formato aaaa-mm."
    if v["fim"]:
        fim = _mes(v["fim"])
        if fim is None:
            f.erros["fim"] = "Informe o mês no formato aaaa-mm."
    if f.erros or tipo is None or valor is None or dia is None:
        return f, None, None
    dados = svc.DadosRecorrencia(
        tipo, valor, v["descricao"], dia, inicio, fim, inteiro_ou_none(v["categoria"])
    )
    return f, dados, inteiro_ou_none(v["titular"])


async def _opcoes(s: Any) -> dict[str, Any]:
    return {
        "membros": await membros.listar(s, incluir_inativos=True),
        "categorias": await categorias.listar(s),
        "tipos": TIPOS,
    }


@router.get("", response_class=HTMLResponse)
async def listar(request: Request, usuario: Usuario = Depends(usuario_atual)) -> HTMLResponse:
    async with estado(request).fabrica() as s:
        todas = await svc.listar(s)
        nomes = {m.id: m.nome for m in await membros.listar(s)}
        cats = {c.id: c.nome for c in await categorias.listar(s)}
    return render(
        request,
        "recorrencias/lista.html",
        usuario,
        ativas=[r for r in todas if r.ativa],
        encerradas=[r for r in todas if not r.ativa],
        nomes=nomes,
        cats=cats,
        proximas={r.id: svc.proxima_data(r) for r in todas},
        pode=lambda r: svc.pode_alterar(usuario.membro, r),
    )


@router.get("/nova", response_class=HTMLResponse)
async def nova(request: Request, usuario: Usuario = Depends(usuario_atual)) -> HTMLResponse:
    async with estado(request).fabrica() as s:
        opcoes = await _opcoes(s)
    f = Formulario(valores={"tipo": TipoLancamento.DEBITO.value, "titular": str(usuario.membro.id)})
    return render(request, "recorrencias/form.html", usuario, form=f, rec=None, **opcoes)


@router.post("", response_class=HTMLResponse)
async def criar(
    request: Request,
    usuario: Usuario = Depends(usuario_atual),
    form: FormData = Depends(formulario),
) -> Response:
    e = estado(request)
    f, dados, titular = _ler(form)
    if dados is not None:
        try:
            async with transacao(e.fabrica) as s:
                ator = await s.get(Membro, usuario.membro.id)
                assert ator is not None
                await svc.criar(
                    s, e.clock, ator, dados, titular_id=titular if usuario.eh_admin else None
                )
            return redirecionar("/recorrentes?msg=recorrencia_salva")
        except SemPermissao:
            raise
        except ErroDominio as erro:
            f.erro_geral = str(erro)
    async with e.fabrica() as s:
        opcoes = await _opcoes(s)
    return render(
        request, "recorrencias/form.html", usuario, status=422, form=f, rec=None, **opcoes
    )


async def _carregar_alteravel(s: Any, usuario: Usuario, recorrencia_id: int) -> Recorrencia:
    rec = await svc.obter(s, recorrencia_id)
    if not svc.pode_alterar(usuario.membro, rec):
        raise SemPermissao("Você só pode alterar as suas próprias recorrências.")
    return rec


@router.get("/{recorrencia_id}/editar", response_class=HTMLResponse)
async def editar_form(
    request: Request, recorrencia_id: int, usuario: Usuario = Depends(usuario_atual)
) -> HTMLResponse:
    async with estado(request).fabrica() as s:
        rec = await _carregar_alteravel(s, usuario, recorrencia_id)
        opcoes = await _opcoes(s)
    f = Formulario(valores={
        "tipo": rec.tipo, "valor": formatar_brl(rec.valor).removeprefix("R$ "),
        "descricao": rec.descricao, "dia": str(rec.dia), "inicio": _mes_campo(rec.inicio),
        "fim": _mes_campo(rec.fim), "categoria": str(rec.categoria_id or ""),
        "titular": str(rec.membro_id),
    })  # fmt: skip
    return render(request, "recorrencias/form.html", usuario, form=f, rec=rec, **opcoes)


@router.post("/{recorrencia_id}/editar", response_class=HTMLResponse)
async def editar(
    request: Request,
    recorrencia_id: int,
    usuario: Usuario = Depends(usuario_atual),
    form: FormData = Depends(formulario),
) -> Response:
    e = estado(request)
    async with e.fabrica() as s:
        rec = await _carregar_alteravel(s, usuario, recorrencia_id)
    f, dados, _titular = _ler(form, existente=rec)
    if dados is not None:
        try:
            async with transacao(e.fabrica) as s:
                ator = await s.get(Membro, usuario.membro.id)
                assert ator is not None
                await svc.editar(s, e.clock, ator, recorrencia_id, dados)
            return redirecionar("/recorrentes?msg=recorrencia_salva")
        except (NaoEncontrado, SemPermissao):
            raise
        except ErroDominio as erro:
            f.erro_geral = str(erro)
    async with e.fabrica() as s:
        opcoes = await _opcoes(s)
    return render(request, "recorrencias/form.html", usuario, status=422, form=f, rec=rec, **opcoes)


@router.post("/{recorrencia_id}/{acao}")
async def encerrar_ou_reativar(
    request: Request,
    recorrencia_id: int,
    acao: str,
    usuario: Usuario = Depends(usuario_atual),
    _form: FormData = Depends(formulario),
) -> Response:
    if acao not in {"encerrar", "reativar"}:
        raise NaoEncontrado("Página não encontrada.")
    e = estado(request)
    async with transacao(e.fabrica) as s:
        ator = await s.get(Membro, usuario.membro.id)
        assert ator is not None
        await svc.definir_ativa(s, e.clock, ator, recorrencia_id, acao == "reativar")
    sufixo = "reativada" if acao == "reativar" else "encerrada"
    return redirecionar(f"/recorrentes?msg=recorrencia_{sufixo}")
