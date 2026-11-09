# ADR 0003 — Uma única coluna de revisão por lançamento

- **Situação:** aceita (Etapa 2)
- **Decisão:** `lancamentos.revisao` serve ao controle otimista de concorrência e à
  identificação da revisão das parcelas (`parcelas.revisao_lancamento`).
- **Motivo:** evita duas sequências de versão que poderiam divergir. Restaurar uma
  compra reativa as parcelas da maior `revisao_lancamento`, conforme a spec 5.7.
- **Consequência:** a numeração de revisões de parcelas pode ter saltos (edições de
  descrição também incrementam a revisão); isso é intencional e inofensivo.
