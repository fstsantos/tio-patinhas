# ADR 0006 — LLM como reserva da interpretação de voz, com provedor trocável

- **Situação:** aceita (2026-11-10)
- **Contexto:** fala livre, datas por extenso e vários lançamentos no mesmo áudio não
  são entendidos pelas regras locais. O custo deve ser o menor possível e a troca de
  provedor, simples.
- **Decisão:**
  - A interpretação local vem sempre primeiro. A LLM só é chamada quando ela falha, ou
    quando entendeu um único lançamento numa fala com mais de um valor. Se a LLM
    falhar, vale o resultado local.
  - O prompt, o esquema da resposta (lista de lançamentos + dúvida) e a validação ficam
    em `integrations/llm/base.py`, fora dos provedores. Um provedor implementa só
    `completar_json(sistema, usuario, esquema)`.
  - Dois provedores: `anthropic` (SDK oficial, structured outputs, esforço `low`,
    padrão `claude-haiku-5-5`) e `openai` (HTTP no formato "chat completions", serve à
    OpenAI e a serviços compatíveis, inclusive locais, por `LLM_BASE_URL`).
  - Vários lançamentos viram uma confirmação única, gravada numa só transação. O
    payload da confirmação guarda a lista; o esquema do banco não muda.
- **Consequências:** o provedor compatível usa `response_format: json_object` (mais
  amplamente suportado que JSON Schema), então a forma da resposta depende do prompt e
  é conferida depois — proposta fora do formato é descartada. Recusas do modelo não têm
  nova tentativa em outro modelo: viram a pergunta local. `financas testar-llm` valida
  provedor, chave e modelo sem gravar nada.
