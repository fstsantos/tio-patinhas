"""Gestão de lançamentos (spec 7.2). Permissões validadas nos serviços."""

from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal
from typing import Any
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, Response
from starlette.datastructures import FormData

from financas.db.models import Membro, Origem, StatusLancamento, TipoLancamento
from financas.db.session import transacao
from financas.domain.dinheiro import formatar_brl, parse_valor
from financas.domain.erros import ErroDominio, NaoEncontrado, SemPermissao
from financas.services import auditoria, autorizacao, categorias, membros
from financas.services import lancamentos as svc
from financas.web.app import estado, formulario, redirecionar, render, usuario_atual
from financas.web.auth import Usuario
from financas.web.routes.dashboard import inteiro_ou_none

router = APIRouter(prefix="/lancamentos")


@dataclass
class Formulario:
    """Valores digitados (strings) e erros por campo, para reexibir o formulário."""

    valores: dict[str, str] = field(default_factory=dict)
    erros: dict[str, str] = field(default_factory=dict)
    erro_geral: str | None = None


def _data(valor: str | None) -> date | None:
    try:
        return date.fromisoformat(valor) if valor else None
    except ValueError:
        return None


def _mes(valor: str) -> date | None:
    """``aaaa-mm`` (campo ``type=month``) → primeiro dia do mês; inválido → None."""
    try:
        return date.fromisoformat(f"{valor}-01")
    except ValueError:
        return None


def _valor_opcional(valor: str | None) -> Decimal | None:
    try:
        return parse_valor(valor) if valor else None
    except ErroDominio:
        return None


def _filtro(request: Request, usuario: Usuario) -> tuple[svc.FiltroLancamentos, dict[str, str]]:
    q = request.query_params
    tipo = q.get("tipo") or None
    status_txt = q.get("status", "ATIVO")
    status = (
        None
        if status_txt == "TODOS"
        else (
            StatusLancamento(status_txt)
            if status_txt in StatusLancamento.__members__
            else StatusLancamento.ATIVO
        )
    )
    if not usuario.eh_admin:
        status, status_txt = StatusLancamento.ATIVO, "ATIVO"
    filtro = svc.FiltroLancamentos(
        membro_id=inteiro_ou_none(q.get("membro")),
        tipo=TipoLancamento(tipo) if tipo and tipo in TipoLancamento.__members__ else None,
        status=status,
        texto=(q.get("texto") or "").strip() or None,
        categoria_id=inteiro_ou_none(q.get("categoria")),
        data_de=_data(q.get("de")),
        data_ate=_data(q.get("ate")),
        valor_min=_valor_opcional(q.get("valor_min")),
        valor_max=_valor_opcional(q.get("valor_max")),
    )
    valores = {
        k: q.get(k, "")
        for k in ("membro", "tipo", "texto", "categoria", "de", "ate", "valor_min", "valor_max")
    }
    valores["status"] = status_txt
    return filtro, valores


async def _opcoes(s: Any) -> dict[str, Any]:
    return {
        "membros": await membros.listar(s, incluir_inativos=True),
        "categorias": await categorias.listar(s),
    }


@router.get("", response_class=HTMLResponse)
async def listar(request: Request, usuario: Usuario = Depends(usuario_atual)) -> HTMLResponse:
    e = estado(request)
    filtro, valores = _filtro(request, usuario)
    pagina = inteiro_ou_none(request.query_params.get("pagina")) or 1
    async with e.fabrica() as s:
        resultado = await svc.listar(s, usuario.membro, filtro, pagina=pagina)
        opcoes = await _opcoes(s)
    parcial = bool(request.headers.get("HX-Request"))
    template = "lancamentos/_tabela.html" if parcial else "lancamentos/lista.html"
    consulta = urlencode({k: v for k, v in valores.items() if v})
    return render(
        request, template, usuario, resultado=resultado, filtro=valores, consulta=consulta, **opcoes
    )


# ------------------------------------------------------------------ criação/edição


def _ler_formulario(
    form: FormData, *, tipo_fixo: str | None = None
) -> tuple[Formulario, svc.DadosLancamento | None, int | None]:
    campos = (
        "tipo",
        "valor",
        "parcelas",
        "primeira_competencia",
        "descricao",
        "data",
        "categoria",
        "titular",
        "motivo",
        "revisao",
    )
    f = Formulario(valores={k: str(form.get(k, "")).strip() for k in campos})
    v = f.valores
    if tipo_fixo:
        v["tipo"] = tipo_fixo
    tipo = TipoLancamento(v["tipo"]) if v["tipo"] in TipoLancamento.__members__ else None
    if tipo is None:
        f.erros["tipo"] = "Escolha o tipo."
    valor = None
    try:
        valor = parse_valor(v["valor"])
    except ErroDominio as e:
        f.erros["valor"] = str(e)
    parcelas = None
    primeira_competencia = None
    if tipo is TipoLancamento.CREDITO:
        parcelas = inteiro_ou_none(v["parcelas"])
        if parcelas is None:
            f.erros["parcelas"] = "Informe de 1 a 48 parcelas."
        if v["primeira_competencia"]:
            primeira_competencia = _mes(v["primeira_competencia"])
            if primeira_competencia is None:
                f.erros["primeira_competencia"] = "Informe o mês no formato aaaa-mm."
    data_op = _data(v["data"])
    if data_op is None:
        f.erros["data"] = "Informe a data."
    if not v["descricao"]:
        f.erros["descricao"] = "Informe a descrição."
    if f.erros or tipo is None or valor is None or data_op is None:
        return f, None, None
    dados = svc.DadosLancamento(
        tipo,
        valor,
        v["descricao"],
        data_op,
        parcelas,
        inteiro_ou_none(v["categoria"]),
        primeira_competencia,
    )
    return f, dados, inteiro_ou_none(v["titular"])


@router.get("/novo", response_class=HTMLResponse)
async def novo(request: Request, usuario: Usuario = Depends(usuario_atual)) -> HTMLResponse:
    e = estado(request)
    async with e.fabrica() as s:
        opcoes = await _opcoes(s)
    f = Formulario(
        valores={
            "tipo": request.query_params.get("tipo", "DEBITO"),
            "data": e.clock.hoje().isoformat(),
            "titular": str(usuario.membro.id),
        }
    )
    return render(request, "lancamentos/form.html", usuario, form=f, lancamento=None, **opcoes)


@router.post("", response_class=HTMLResponse)
async def criar(
    request: Request,
    usuario: Usuario = Depends(usuario_atual),
    form: FormData = Depends(formulario),
) -> Response:
    e = estado(request)
    f, dados, titular = _ler_formulario(form)
    if dados is not None:
        try:
            async with transacao(e.fabrica) as s:
                ator = await s.get(Membro, usuario.membro.id)
                assert ator is not None
                r = await svc.criar(
                    s,
                    e.clock,
                    ator,
                    dados,
                    origem=Origem.WEB,
                    titular_id=titular if usuario.eh_admin else None,
                )
            return redirecionar(f"/lancamentos/{r.lancamento.id}?msg=criado")
        except SemPermissao:
            raise
        except ErroDominio as erro:
            f.erro_geral = str(erro)
    async with e.fabrica() as s:
        opcoes = await _opcoes(s)
    return render(
        request, "lancamentos/form.html", usuario, status=422, form=f, lancamento=None, **opcoes
    )


async def _carregar_visivel(s: Any, usuario: Usuario, lancamento_id: int) -> Any:
    lanc = await svc.obter(s, lancamento_id)
    if (
        lanc.status == StatusLancamento.CANCELADO.value
        and not autorizacao.pode_ver_cancelados_e_auditoria(usuario.membro)
    ):
        raise NaoEncontrado(f"Lançamento #{lancamento_id} não encontrado.")
    return lanc


@router.get("/{lancamento_id}", response_class=HTMLResponse)
async def detalhe(
    request: Request, lancamento_id: int, usuario: Usuario = Depends(usuario_atual)
) -> HTMLResponse:
    e = estado(request)
    async with e.fabrica() as s:
        lanc = await _carregar_visivel(s, usuario, lancamento_id)
        todas = await svc.todas_parcelas(s, lanc.id)
        titular = await s.get(Membro, lanc.membro_id)
        autor = await s.get(Membro, lanc.criado_por)
        cat = (await categorias.listar(s)) if lanc.categoria_id else []
        historico = (
            await auditoria.historico(s, lanc.id)
            if autorizacao.pode_ver_cancelados_e_auditoria(usuario.membro)
            else None
        )
        nomes = {m.id: m.nome for m in await membros.listar(s)}
    vigentes = [p for p in todas if p.status != "SUBSTITUIDA"]
    revisoes: dict[int, list[Any]] = defaultdict(list)
    for p in todas:
        if p.status == "SUBSTITUIDA":
            revisoes[p.revisao_lancamento].append(p)
    categoria = next((c.nome for c in cat if c.id == lanc.categoria_id), None)
    return render(
        request,
        "lancamentos/detalhe.html",
        usuario,
        lanc=lanc,
        vigentes=sorted(vigentes, key=lambda p: p.numero),
        revisoes=sorted(revisoes.items(), reverse=True),
        titular=titular,
        autor=autor,
        categoria=categoria,
        historico=historico,
        nomes=nomes,
        pode_alterar=autorizacao.pode_alterar(usuario.membro, lanc),
    )


@router.get("/{lancamento_id}/editar", response_class=HTMLResponse)
async def editar_form(
    request: Request, lancamento_id: int, usuario: Usuario = Depends(usuario_atual)
) -> HTMLResponse:
    e = estado(request)
    async with e.fabrica() as s:
        lanc = await _carregar_visivel(s, usuario, lancamento_id)
        autorizacao.exigir_alterar(usuario.membro, lanc)
        opcoes = await _opcoes(s)
    f = Formulario(valores={
        "tipo": lanc.tipo, "valor": formatar_brl(lanc.valor_total).removeprefix("R$ "),
        "parcelas": str(lanc.qtd_parcelas or ""), "descricao": lanc.descricao,
        "primeira_competencia": (
            lanc.primeira_competencia.strftime("%Y-%m") if lanc.primeira_competencia else ""
        ),
        "data": lanc.data_operacao.isoformat(), "categoria": str(lanc.categoria_id or ""),
        "titular": str(lanc.membro_id), "revisao": str(lanc.revisao),
    })  # fmt: skip
    return render(request, "lancamentos/form.html", usuario, form=f, lancamento=lanc, **opcoes)


@router.post("/{lancamento_id}/editar", response_class=HTMLResponse)
async def editar(
    request: Request,
    lancamento_id: int,
    usuario: Usuario = Depends(usuario_atual),
    form: FormData = Depends(formulario),
) -> Response:
    e = estado(request)
    async with e.fabrica() as s:
        lanc = await _carregar_visivel(s, usuario, lancamento_id)
    autorizacao.exigir_alterar(usuario.membro, lanc)
    f, dados, _titular = _ler_formulario(form, tipo_fixo=lanc.tipo)
    revisao = inteiro_ou_none(f.valores["revisao"])
    if dados is not None and revisao is not None:
        try:
            async with transacao(e.fabrica) as s:
                ator = await s.get(Membro, usuario.membro.id)
                assert ator is not None
                await svc.editar(
                    s,
                    e.clock,
                    ator,
                    lancamento_id,
                    dados,
                    origem=Origem.WEB,
                    revisao_esperada=revisao,
                    motivo=f.valores["motivo"] or None,
                )
            return redirecionar(f"/lancamentos/{lancamento_id}?msg=editado")
        except (NaoEncontrado, SemPermissao):
            raise
        except ErroDominio as erro:
            f.erro_geral = str(erro)
    async with e.fabrica() as s:
        opcoes = await _opcoes(s)
    return render(
        request, "lancamentos/form.html", usuario, status=422, form=f, lancamento=lanc, **opcoes
    )


# ------------------------------------------------------------------ cancelar/restaurar


@router.get("/{lancamento_id}/{acao}", response_class=HTMLResponse)
async def confirmar_form(
    request: Request, lancamento_id: int, acao: str, usuario: Usuario = Depends(usuario_atual)
) -> HTMLResponse:
    if acao not in {"cancelar", "restaurar"}:
        raise NaoEncontrado("Página não encontrada.")
    e = estado(request)
    async with e.fabrica() as s:
        lanc = await _carregar_visivel(s, usuario, lancamento_id)
    if acao == "cancelar":
        autorizacao.exigir_alterar(usuario.membro, lanc)
    else:
        autorizacao.exigir_admin(usuario.membro)
    return render(request, "lancamentos/confirmar.html", usuario, lanc=lanc, acao=acao, erro=None)


@router.post("/{lancamento_id}/{acao}", response_class=HTMLResponse)
async def confirmar(
    request: Request,
    lancamento_id: int,
    acao: str,
    usuario: Usuario = Depends(usuario_atual),
    form: FormData = Depends(formulario),
) -> Response:
    if acao not in {"cancelar", "restaurar"}:
        raise NaoEncontrado("Página não encontrada.")
    e = estado(request)
    motivo = str(form.get("motivo", "")).strip()
    revisao = inteiro_ou_none(str(form.get("revisao", "")))
    try:
        async with transacao(e.fabrica) as s:
            ator = await s.get(Membro, usuario.membro.id)
            assert ator is not None
            if acao == "cancelar":
                await svc.cancelar(
                    s,
                    e.clock,
                    ator,
                    lancamento_id,
                    origem=Origem.WEB,
                    motivo=motivo or None,
                    revisao_esperada=revisao,
                )
            else:
                await svc.restaurar(
                    s, e.clock, ator, lancamento_id, origem=Origem.WEB, motivo=motivo
                )
    except (NaoEncontrado, SemPermissao):
        raise
    except ErroDominio as erro:
        async with e.fabrica() as s:
            lanc = await _carregar_visivel(s, usuario, lancamento_id)
        return render(
            request,
            "lancamentos/confirmar.html",
            usuario,
            status=422,
            lanc=lanc,
            acao=acao,
            erro=str(erro),
            motivo=motivo,
        )
    sufixo = "cancelado" if acao == "cancelar" else "restaurado"
    return redirecionar(f"/lancamentos/{lancamento_id}?msg={sufixo}")
