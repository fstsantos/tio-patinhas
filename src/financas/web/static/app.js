// Comportamentos da interface. Sem scripts inline (CSP).
"use strict";

function alternarParcelas() {
  const marcado = document.querySelector("input[name=tipo]:checked");
  const campo = document.querySelector("[data-parcelas]");
  if (campo && marcado) campo.hidden = marcado.value !== "CREDITO";
}

function brl(v) {
  return v.toLocaleString("pt-BR", { style: "currency", currency: "BRL" });
}

function desenharGraficos() {
  const fonte = document.getElementById("graficos");
  if (!fonte || typeof Chart === "undefined") return;
  const g = JSON.parse(fonte.dataset.graficos);
  const eixo = { ticks: { callback: (v) => brl(v) } };
  const dica = { callbacks: { label: (c) => `${c.dataset.label || c.label}: ${brl(c.parsed.y ?? c.parsed)}` } };
  const novo = (id, cfg) => { const el = document.getElementById(id); if (el) new Chart(el, cfg); };
  const base = { responsive: true, maintainAspectRatio: false, plugins: { tooltip: dica } };
  novo("g-evolucao", { type: "bar", data: { labels: g.evolucao.rotulos, datasets: [
    { label: "Receitas", data: g.evolucao.receitas, backgroundColor: "#15803d" },
    { label: "Despesas consideradas", data: g.evolucao.despesas, backgroundColor: "#b42318" },
  ] }, options: { ...base, scales: { y: eixo } } });
  novo("g-acumulado", { type: "line", data: { labels: g.evolucao.rotulos, datasets: [
    { label: "Saldo acumulado", data: g.evolucao.acumulado, borderColor: "#1f5fbf", tension: .25, fill: false },
  ] }, options: { ...base, scales: { y: eixo } } });
  novo("g-distribuicao", { type: "bar", data: { labels: g.distribuicao.rotulos, datasets: [
    { label: "Despesas", data: g.distribuicao.valores, backgroundColor: "#64748b" },
  ] }, options: { ...base, indexAxis: "y", plugins: { ...base.plugins, legend: { display: false },
    tooltip: { callbacks: { label: (c) => brl(c.parsed.x) } } }, scales: { x: eixo } } });
  novo("g-compromissos", { type: "bar", data: { labels: g.compromissos.rotulos, datasets: [
    { label: "Parcelas pendentes", data: g.compromissos.valores, backgroundColor: "#b45309" },
  ] }, options: { ...base, plugins: { ...base.plugins, legend: { display: false } }, scales: { y: eixo } } });
}

document.addEventListener("DOMContentLoaded", () => {
  document.querySelectorAll("[data-alterna-parcelas]").forEach((r) => r.addEventListener("change", alternarParcelas));
  alternarParcelas();
  desenharGraficos();
});
