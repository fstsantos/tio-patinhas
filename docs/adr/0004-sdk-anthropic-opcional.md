# ADR 0004 — LLM opcional via SDK oficial da Anthropic, com structured outputs

- **Situação:** aceita (Etapa 5)
- **Contexto:** a spec prevê LLM apenas como apoio à interpretação de voz e para
  análises futuras; nenhuma regra financeira pode depender dela.
- **Decisão:** provedor `LLMAnthropic` (extra `llm`), modelo configurável
  (`LLM_MODELO`, padrão `claude-opus-5-5`), saída restrita por JSON Schema, esforço
  `low`, timeout, fallback de recusa no servidor (`fallbacks: "default"`). A proposta
  passa pelo mesmo parser do texto e por confirmação humana.
- **Consequências:** desligado por padrão (`LLM_PROVEDOR=nenhum`). Um modelo menor
  (ex.: `claude-haiku-5-5`) reduz custo e latência e pode ser escolhido no `.env`.
