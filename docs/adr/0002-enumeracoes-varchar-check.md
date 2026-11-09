# ADR 0002 — Enumerações como VARCHAR + CHECK

- **Situação:** aceita (Etapa 2)
- **Decisão:** tipos, situações e origens são `VARCHAR` com `CHECK (... IN (...))`, e
  não tipos `ENUM` nativos do PostgreSQL.
- **Motivo:** adicionar/remover valores em `ENUM` nativo exige DDL especial e não é
  transacional em todas as situações; `CHECK` é alterado numa migração comum.
