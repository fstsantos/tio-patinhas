// Tema claro/escuro. Carregado no <head> sem defer para aplicar o tema antes da
// primeira pintura (sem "piscar" claro). Sem scripts inline (CSP).
// Sem escolha salva, segue o sistema; a escolha do botão fica no localStorage
// deste navegador.
"use strict";

(function () {
  const CHAVE = "tema";
  const sistema = window.matchMedia("(prefers-color-scheme: dark)");

  function salvo() {
    try {
      const t = localStorage.getItem(CHAVE);
      return t === "escuro" || t === "claro" ? t : null;
    } catch (e) {
      return null;
    }
  }

  // Cópia em memória: vale mesmo quando o localStorage não está disponível.
  let escolhido = salvo();

  function atual() {
    return escolhido || (sistema.matches ? "escuro" : "claro");
  }

  function aplicar(tema) {
    document.documentElement.dataset.tema = tema;
    document.querySelectorAll("[data-alternar-tema]").forEach((botao) => {
      const escuro = tema === "escuro";
      botao.setAttribute("aria-pressed", String(escuro));
      botao.title = escuro ? "Usar modo claro" : "Usar modo escuro";
      const icone = botao.querySelector("[data-icone-tema]");
      if (icone) icone.textContent = escuro ? "☀️" : "🌙";
    });
    document.dispatchEvent(new CustomEvent("tema-alterado", { detail: { tema } }));
  }

  aplicar(atual());

  sistema.addEventListener("change", () => {
    if (!escolhido) aplicar(atual());
  });

  document.addEventListener("DOMContentLoaded", () => {
    aplicar(atual());
    document.querySelectorAll("[data-alternar-tema]").forEach((botao) => {
      botao.addEventListener("click", () => {
        const novo = atual() === "escuro" ? "claro" : "escuro";
        escolhido = novo;
        try {
          localStorage.setItem(CHAVE, novo);
        } catch (e) {
          // Sem armazenamento (navegação privada etc.): vale só para esta página.
        }
        aplicar(novo);
      });
    });
  });
})();
