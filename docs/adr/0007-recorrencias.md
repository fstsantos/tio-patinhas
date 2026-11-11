# ADR 0007 — Recorrências mensais como moldes que geram lançamentos no início do mês

- **Situação:** aceita (2026-11-11)
- **Contexto:** salário, aluguel e escola se repetem todo mês. A família quer que
  entrem sozinhos e que o orçamento do mês já os considere.
- **Decisão:**
  - Tabela `recorrencias` (molde: tipo, valor, descrição, categoria, titular, dia do
    mês, primeiro e último mês). Cada mês vira um **lançamento comum**, com origem
    `RECORRENCIA` e as colunas `recorrencia_id`/`recorrencia_mes`.
  - O lançamento do mês é criado **quando o mês começa** (ou ao criar a recorrência),
    com a data do dia combinado, ainda que futura. Como lançamentos com data futura já
    contam no saldo e no orçamento do mês (decisão de 2026-11-10), relatórios, bot e
    dashboard não precisaram mudar.
  - Só o mês corrente é gerado. Gerar vários meses à frente projetaria o ano no
    resumo anual, mas encheria a lista de lançamentos futuros e obrigaria a corrigir
    os já gerados a cada mudança de valor.
  - Só receita e débito. Compras no cartão chegam pela fatura importada e duplicariam.
  - Geração idempotente: `recorrencias.gerada_ate` guarda o último mês tratado, a linha
    é travada durante a geração e o banco tem único em (`recorrencia_id`,
    `recorrencia_mes`). Quem dispara é o processo web (ao subir e a cada hora) e o
    comando `financas gerar-recorrentes`; meses com o sistema parado são recuperados.
- **Alternativa descartada:** lançar só no dia do vencimento. Exigiria o orçamento
  conhecer as recorrências "a lançar", uma tabela de ocorrências e um agendador com
  avisos no bot — muito mais código para o mesmo resultado no orçamento.
- **Consequências:** um salário do dia 30 entra no saldo desde o dia 1. Valor variável
  (luz, água) é lançado pela estimativa e corrigido no lançamento do mês. Mudanças no
  molde valem só para os meses ainda não gerados. Uma migração aditiva (`0003`).
