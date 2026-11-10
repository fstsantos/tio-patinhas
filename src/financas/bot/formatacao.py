"""Textos das respostas do bot (texto simples, sem parse_mode)."""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from financas.db.models import Lancamento, Parcela, TipoLancamento
from financas.domain.calendario import Mes, SituacaoMes
from financas.domain.dinheiro import arredondar, formatar_brl
from financas.interpretation import comandos as c
from financas.services.relatorios import ResumoAnual, ResumoCredito, ResumoMensal

R = formatar_brl
_NOME_TIPO = {
    TipoLancamento.DEBITO.value: "Débito",
    TipoLancamento.CREDITO.value: "Crédito",
    TipoLancamento.RECEITA.value: "Ganho",
}
_MAX_ITENS = 15


def _data(d: date) -> str:
    return f"{d:%d/%m/%Y}"


def lancamento_registrado(lanc: Lancamento, parcelas: list[Parcela]) -> str:
    cab = {
        TipoLancamento.DEBITO.value: "✅ Débito registrado",
        TipoLancamento.RECEITA.value: "✅ Ganho registrado",
        TipoLancamento.CREDITO.value: "✅ Compra no crédito registrada",
    }[lanc.tipo]
    linhas = [f"{cab} — código {lanc.id}"]
    if lanc.tipo != TipoLancamento.CREDITO.value:
        linhas.append(f"{R(lanc.valor_total)} · {lanc.descricao} · {_data(lanc.data_operacao)}")
        return "\n".join(linhas)
    valores = sorted({p.valor for p in parcelas}, reverse=True)
    if len(valores) == 1:
        parcela_txt = f"{lanc.qtd_parcelas}x de {R(valores[0])}"
    else:
        parcela_txt = f"{lanc.qtd_parcelas}x de {R(valores[-1])} a {R(valores[0])}"
    linhas += [
        f"Total: {R(lanc.valor_total)} em {parcela_txt}",
        f"{lanc.descricao} · compra em {_data(lanc.data_operacao)}",
        "",
        "Cronograma (mês de referência):",
    ]
    linhas += [
        f"  {p.numero}/{lanc.qtd_parcelas}  {Mes.de_data(p.competencia).abreviado} — {R(p.valor)}"
        for p in parcelas
    ]
    return "\n".join(linhas)


def lancamento_cancelado(lanc: Lancamento) -> str:
    extra = " e todas as suas parcelas" if lanc.tipo == TipoLancamento.CREDITO.value else ""
    return (
        f"🗑️ Lançamento {lanc.id} cancelado{extra}.\n"
        f"{_NOME_TIPO[lanc.tipo]} {R(lanc.valor_total)} · {lanc.descricao}"
    )


def pedido_cancelamento(lanc: Lancamento, motivo: str | None) -> str:
    extra = (
        f"\nTodas as {lanc.qtd_parcelas} parcelas também serão canceladas."
        if lanc.tipo == TipoLancamento.CREDITO.value
        else ""
    )
    mot = f"\nMotivo: {motivo}" if motivo else ""
    return (
        f"Cancelar o lançamento {lanc.id}?\n"
        f"{_NOME_TIPO[lanc.tipo]} {R(lanc.valor_total)} · {lanc.descricao} · "
        f"{_data(lanc.data_operacao)}{extra}{mot}"
    )


def comando_de_voz(cmd: c.Lancamento) -> str:
    if isinstance(cmd, c.Credito):
        corpo = (
            f"Crédito {R(cmd.valor_total)} em {cmd.parcelas}x · {cmd.descricao} · "
            f"compra em {_data(cmd.data)}"
        )
    else:
        nome = "Débito" if isinstance(cmd, c.Debito) else "Ganho"
        corpo = f"{nome} {R(cmd.valor)} · {cmd.descricao} · {_data(cmd.data)}"
    return f"🎙️ Entendi:\n{corpo}\n\nConfirma o registro?"


def ultimos(lancs: list[Lancamento]) -> str:
    if not lancs:
        return "Você não tem lançamentos ativos."
    linhas = ["🧾 Seus últimos lançamentos:"]
    for lanc in lancs:
        parc = f" em {lanc.qtd_parcelas}x" if lanc.qtd_parcelas else ""
        linhas.append(
            f"• {lanc.id} · {_NOME_TIPO[lanc.tipo]} {R(lanc.valor_total)}{parc} · "
            f"{lanc.descricao} · {_data(lanc.data_operacao)}"
        )
    linhas.append("\nPara cancelar: cancelar <código> [motivo]")
    return "\n".join(linhas)


def _percentual(parte: Decimal, todo: Decimal) -> str:
    if todo <= 0:
        return "—"
    return f"{arredondar(parte * 100 / todo):.0f}%".replace(".", ",")


def resumo_mensal(r: ResumoMensal, filtro: str | None = None) -> list[str]:
    o = r.orcamento
    titulo = f"📊 Resumo de {o.mes.por_extenso}"
    if filtro:
        titulo += f" — {filtro}"
    blocos = [titulo]

    realizado = [
        "REALIZADO",
        f"Receitas: {R(o.receitas)}",
        f"Despesas à vista: {R(o.debitos)}",
    ]
    blocos.append("\n".join(realizado))

    previsto = ["COMPROMISSOS DO MÊS (parcelas)", f"Total de parcelas: {R(o.parcelas)}"]
    for p in r.parcelas[:_MAX_ITENS]:
        previsto.append(f"• {p.descricao} {p.numero}/{p.total_parcelas} — {R(p.valor)}")
    if len(r.parcelas) > _MAX_ITENS:
        previsto.append(f"… e mais {len(r.parcelas) - _MAX_ITENS} parcelas")
    blocos.append("\n".join(previsto))

    saldo = ["SALDO DO MÊS", f"Receitas − despesas − parcelas = {R(o.saldo)}"]
    if o.deficit:
        saldo.append(f"⚠️ Déficit de {R(-o.saldo)}")
    saldo.append(
        f"Despesas consideradas: {R(o.despesas_consideradas)} "
        f"({_percentual(o.despesas_consideradas, o.receitas)} das receitas)"
    )
    if o.situacao is SituacaoMes.ENCERRADO:
        saldo.append(f"Mês encerrado. Saldo final: {R(o.saldo)}")
    else:
        saldo.append(f"Dias restantes (incluindo hoje): {o.dias_restantes}")
        assert o.orcamento_diario is not None
        extra = " (sem saldo disponível)" if o.saldo <= 0 else ""
        saldo.append(f"Orçamento diário sugerido: {R(o.orcamento_diario)}{extra}")
    blocos.append("\n".join(saldo))

    if r.compras_credito:
        proj = [
            "PROJEÇÃO (informativo, fora do saldo)",
            f"Compras no crédito feitas neste mês: {R(r.total_credito_contratado)} "
            f"({len(r.compras_credito)} compra(s))",
        ]
        if r.impacto_futuro:
            proj.append("Impacto previsto nos meses seguintes:")
        proj += [f"  {m.abreviado}: {R(v)}" for m, v in r.impacto_futuro[:12]]
        if len(r.impacto_futuro) > 12:
            proj.append(f"  … e mais {len(r.impacto_futuro) - 12} meses")
        blocos.append("\n".join(proj))

    if r.por_categoria:
        blocos.append(
            "\n".join(
                ["POR CATEGORIA"]
                + [f"• {g.nome}: {R(g.total)}" for g in r.por_categoria[:_MAX_ITENS]]
            )
        )
    if r.por_descricao:
        blocos.append(
            "\n".join(
                ["POR DESCRIÇÃO"]
                + [
                    f"• {g.nome}: {R(g.total)}"
                    + (f" ({g.quantidade}x)" if g.quantidade > 1 else "")
                    for g in r.por_descricao[:_MAX_ITENS]
                ]
            )
        )
    if r.receitas:
        blocos.append(
            "\n".join(
                ["RECEITAS CONSIDERADAS"]
                + [
                    f"• {x.descricao} — {R(x.valor)} ({x.data:%d/%m}, {x.membro})"
                    for x in r.receitas[:_MAX_ITENS]
                ]
            )
        )
    return blocos


def resumo_anual(r: ResumoAnual, filtro: str | None = None) -> list[str]:
    titulo = f"📅 Resumo de {r.ano}" + (f" — {filtro}" if filtro else "")
    meses = []
    for m in r.meses:
        o = m.orcamento
        meses.append(
            f"{o.mes.abreviado}\n"
            f"  Receitas {R(o.receitas)} · À vista {R(o.debitos)} · Parcelas {R(o.parcelas)}\n"
            f"  Despesas {R(o.despesas_consideradas)} · Saldo {R(o.saldo)} · "
            f"Acumulado {R(m.saldo_acumulado)}"
        )
    totais = (
        f"TOTAIS DE {r.ano}\n"
        f"Receitas: {R(r.receitas)}\nDespesas à vista: {R(r.debitos)}\n"
        f"Parcelas: {R(r.parcelas)}\nDespesas consideradas: {R(r.despesas)}\n"
        f"Saldo acumulado: {R(r.saldo)}\n\n"
        "O saldo acumulado começa em zero em janeiro e não é saldo bancário."
    )
    return [titulo, *meses, totais]


def resumo_credito(r: ResumoCredito) -> list[str]:
    if not r.blocos:
        return ["💳 Nenhuma parcela pendente do mês atual em diante."]
    blocos = ["💳 Parcelas pendentes (mês atual em diante)"]
    for b in r.blocos:
        linhas = [f"{b.mes.abreviado} — subtotal {R(b.subtotal)}"]
        linhas += [
            f"• {p.descricao} {p.numero}/{p.total_parcelas} — {R(p.valor)} "
            f"(compra {p.data_compra:%d/%m/%Y}, {p.membro})"
            for p in b.parcelas
        ]
        blocos.append("\n".join(linhas))
    blocos.append(f"Total pendente: {R(r.total)}")
    return blocos


AJUDA = """🦆 Tio Patinhas — comandos

Lançamentos (data opcional no fim: ontem, dd/mm ou dd/mm/aaaa):
• débito <valor> <descrição> [data]
  ex.: débito 10,50 compras de mercado
  ex.: débito 45,00 farmácia ontem
• crédito <valor TOTAL> <parcelas> <descrição> [data]
  ex.: crédito 1000,00 10x mercado livre
  (a 1ª parcela entra no mês seguinte ao da compra)
• ganho <valor> <descrição> [data]
  ex.: ganho 3500,00 salário

Consultas:
• resumo mês [mm/aaaa]
• resumo ano [aaaa]
• resumo crédito
• últimos — seus 10 lançamentos mais recentes

Correções:
• cancelar <código> [motivo] — pede confirmação
• desfazer — cancela seu último lançamento (até 10 min)
• Editar uma mensagem não altera o lançamento: cancele e registre de novo.

Dicas de valores:
• Use vírgula nos centavos: 10,50 (não 10.50)
• Ponto só para milhar: 1.000 ou 12.345,67
• No máximo duas casas decimais; R$ é opcional
• dd/mm sem ano vale para o ano corrente; datas futuras são aceitas

Voz: envie um áudio, ex.: "débito de dez reais e cinquenta no mercado".
Todo comando de voz pede confirmação antes de registrar."""

NAO_AUTORIZADO = (
    "Olá! Este bot é de uso privado e você não está autorizado. "
    "Se você faz parte da família, peça ao administrador para cadastrar seu acesso."
)

SUGESTAO_AJUDA = 'Não entendi. Envie "ajuda" para ver os comandos disponíveis.'

MENSAGEM_EDITADA = (
    '✏️ Editar mensagens não altera lançamentos. Use "cancelar <código>" e registre novamente.'
)


def start(nome: str) -> str:
    return (
        f"Olá, {nome}! Seu Telegram está vinculado a um membro ativo. ✅\n"
        'Envie "ajuda" para ver os comandos, ou comece com:\n'
        "débito 10,50 mercado"
    )


def erro_comando(e: c.ErroComando) -> str:
    texto = f"⚠️ {e.mensagem}"
    if e.sugestao:
        texto += f"\n{e.sugestao}"
    return texto
