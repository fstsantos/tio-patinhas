# ADR 0005 — Importação de fatura de cartão sem chave de importação

- **Situação:** aceita (2026-11-10)
- **Decisão:** `financas importar-fatura` transforma cada linha da fatura fechada do
  Itaú (`.xlsx`) numa compra no crédito, pelo mesmo serviço usado por bot e web
  (origem `CLI`). O mês de cada parcela vem de `lancamentos.primeira_competencia` =
  mês da fatura − (*k* − 1); uma parcela *k* > 1 recria a compra inteira desde a 1ª
  parcela. A reimportação é evitada comparando dados (titular, data, descrição,
  parcelas e valor), sem coluna de chave de importação.
- **Motivo:** o fechamento do cartão faz muitas compras caírem fora da regra
  padrão M + 1; o mês real é o da fatura. Importar a compra inteira mantém o total da
  compra correto e faz as faturas seguintes reconhecerem as parcelas restantes. A
  comparação por dados evita mudar o esquema.
- **Consequência:** editar data, descrição, parcelas ou valor de uma compra importada
  faz a próxima importação tratá-la como nova (a prévia mostra isso antes de gravar).
  O valor total das parceladas é parcela × N; se a fatura concentrar centavos de
  arredondamento numa parcela, o total pode diferir em centavos do valor real — a
  comparação tolera até 1 centavo por parcela.
