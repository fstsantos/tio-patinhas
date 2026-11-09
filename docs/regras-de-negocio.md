# Regras de negócio

Documento vivo; complementa a especificação original do projeto. Implementação de referência
entre parênteses.

## Valores monetários (`domain/dinheiro.py`)

- Entrada em pt-BR: vírgula decimal; ponto só como milhar em grupos exatos de três
  (`1.000`, `12.345,67`). `10.50` e `1.5` são rejeitados com a sugestão "Você quis dizer 10,50?".
- Mais de duas casas decimais: rejeitado (nunca arredondado).
- Limites: R$ 0,01 a R$ 1.000.000,00. Somente `Decimal`; banco em `NUMERIC(14,2)`.
- Arredondamento de exibição/divisão: `ROUND_HALF_UP` em centavos.

## Calendário e meses de referência (`domain/calendario.py`)

- Fuso `America/Sao_Paulo`; "hoje" vem do relógio injetável (`clock.py`).
- Mês = intervalo semiaberto `[dia 1, dia 1 do mês seguinte)`.
- Dias restantes incluem hoje; mês encerrado = 0; mês futuro = todos os dias.

## Datas financeiras

- Lançamentos retroativos são permitidos.
- **Datas futuras são proibidas para débito, ganho e crédito na v1** (decisão aprovada em
  2026-11-09, resolvendo a divergência entre as seções 3 e 5.2 da spec). Garantido pelos
  serviços e pelo CHECK `ck_lancamentos_data_nao_futura`.

## Parcelamento (`domain/parcelamento.py`)

- A parcela *k* (1..n) de uma compra feita no mês M pertence ao mês M + *k*
  (`competencia` = dia 1 desse mês). Compra em dezembro → 1ª parcela em janeiro.
- Não há dia de vencimento nem fechamento de fatura.
- **Limitação conhecida:** compras feitas após o fechamento real do cartão (fim do mês)
  costumam cair na fatura de M + 2; a v1 aceita essa diferença.
- Centavos: base = total ÷ n truncada; o resto em centavos vai, R$ 0,01 cada, para as
  primeiras parcelas. Ex.: R$ 100,00 / 3 = 33,34 + 33,33 + 33,33. Soma sempre exata.
- Cada parcela precisa de ao menos R$ 0,01 (total em centavos ≥ n).
- 1 a 48 parcelas.

## Saldo mensal e orçamento diário (`domain/orcamento.py`)

```
Saldo = Receitas(M) − Débitos(M) − Parcelas com competência M
Orçamento diário = Saldo ÷ dias restantes (se Saldo > 0); 0 caso contrário
```

- Mês encerrado: exibe saldo final, sem orçamento diário.
- Compras no crédito feitas em M não entram no saldo de M (são informativas).
- Sem transporte de saldo entre meses.

## Integridade (migração `0001`, `services/integridade.py`)

Garantida pelos serviços e, como segunda barreira, por *constraint triggers*
`DEFERRABLE INITIALLY DEFERRED` verificados no commit:

1. Soma das parcelas vigentes (status ≠ `SUBSTITUIDA`) = `valor_total`; as vigentes
   formam uma única revisão numerada 1..`qtd_parcelas`.
2. Lançamento cancelado não tem parcela pendente; lançamento ativo não tem parcela
   vigente cancelada.
3. Só compras no crédito têm parcelas; parcelas órfãs são impedidas pela FK.

A tabela `auditoria` é somente de inserção (trigger bloqueia `UPDATE`/`DELETE`).

`financas verificar-integridade` executa as mesmas verificações sobre os dados
existentes e retorna código 1 se houver problemas.

## Revisões e concorrência

`lancamentos.revisao` é o controle otimista de concorrência (SQLAlchemy
`version_id_col`) e também identifica a revisão das parcelas
(`parcelas.revisao_lancamento` = revisão do lançamento quando as parcelas foram geradas).
Reparcelar marca as vigentes como `SUBSTITUIDA` e gera novas com a nova revisão.

## Cancelamento, edição e restauração (`services/lancamentos.py`)

- Nada é excluído fisicamente. Cancelar marca o lançamento como `CANCELADO` (com data,
  autor e motivo) e, numa compra, todas as parcelas vigentes como `CANCELADA`, na
  mesma transação.
- **Editar** (somente lançamentos ativos; o tipo não muda):
  - descrição/categoria → altera só o lançamento;
  - valor, data ou parcelas de uma compra → parcelas vigentes viram `SUBSTITUIDA` e
    uma nova revisão de parcelas é gerada.
  - A edição informa a revisão lida (`revisao_esperada`); se outra pessoa alterou
    antes, a operação falha com conflito (controle otimista).
- **Restaurar:** somente admin, com motivo obrigatório; reativa o lançamento e as
  parcelas da revisão mais recente.
- Toda criação, edição, cancelamento e restauração grava auditoria com autor, origem,
  motivo e fotos antes/depois (inclusive parcelas).
- Permissões (`services/autorizacao.py`): membro cria e altera só os próprios
  lançamentos (titular); admin, qualquer um e em nome de qualquer membro ativo.

## Relatórios (`services/relatorios.py`)

- Sempre recalculados do estado atual; só lançamentos ativos e parcelas pendentes.
- **Resumo mensal:** receitas, débitos e parcelas com mês de referência no mês; saldo
  e orçamento diário pela fórmula acima; compras no crédito feitas no mês aparecem só
  como informação (total contratado e impacto por mês seguinte); despesas (débitos +
  parcelas do mês) agrupadas por categoria (quando houver alguma) e por descrição
  normalizada.
- **Resumo anual:** os 12 meses (mesmo sem lançamentos), cada um com
  `calcular_orcamento`; saldo acumulado começa em zero em janeiro.
- **Resumo de crédito:** parcelas pendentes do mês corrente em diante, por mês.
- Filtro opcional por membro (titular).

## Bot: idempotência, confirmações e voz

- Cada comando que altera estado grava `(chat_id, message_id)` na mesma transação; um
  reenvio recebe a mesma resposta sem duplicar.
- `cancelar` e `desfazer` pedem confirmação por botão; `desfazer` só alcança o último
  lançamento ativo do próprio membro criado há até 10 minutos.
- Confirmações valem 5 minutos, só o autor responde, e a transição `PENDENTE →
  EXECUTADA` é um `UPDATE` condicional atômico (execução única sob concorrência).
- Mensagens editadas não alteram lançamentos; o bot orienta a cancelar e registrar de
  novo (só responde se o texto editado for um comando).
- Voz: transcrição → números falados → mesmo parser → extração determinística →
  LLM opcional (só propõe; a proposta passa pelo mesmo parser) → confirmação. A
  ambiguidade "valor da parcela × total" nunca é resolvida pela LLM: exige o usuário.
- Consultas por voz (ex.: "resumo crédito") são respondidas sem confirmação, pois não
  registram nada.
