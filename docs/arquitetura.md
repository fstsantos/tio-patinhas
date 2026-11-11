# Arquitetura

Monólito modular: um pacote Python (`financas`) e um PostgreSQL, executados como dois
processos — **web** (uvicorn/FastAPI) e **bot** (python-telegram-bot) — a partir da
mesma imagem. Sem microsserviços nem filas.

```
Telegram ──► bot/handlers ──► bot/processador ─┐
                                               ├─► services/* ──► db/ (SQLAlchemy) ──► PostgreSQL
Navegador ─► web/routes  ──────────────────────┘        │
                                                        └─► domain/* (puro, sem I/O)
Voz: integrations/stt ─► interpretation/* ─► (integrations/llm opcional) ─► mesmo parser
```

## Camadas

| Camada | Pacote | Responsabilidade | Regra |
| --- | --- | --- | --- |
| Domínio | `domain/` | dinheiro, calendário, parcelamento, recorrência, orçamento, texto, erros | sem I/O; 100% testável sem banco |
| Serviços | `services/` | lançamentos, recorrências, relatórios, autorização, auditoria, idempotência, confirmações, membros, categorias, integridade | únicos pontos de entrada das regras; recebem a sessão e o relógio |
| Persistência | `db/` | modelos ORM, sessão | esquema só por migrações Alembic |
| Interpretação | `interpretation/` | parser de texto, números falados, interpretador de voz | independente do Telegram |
| Integrações | `integrations/` | STT (faster-whisper, OpenAI), LLM (Anthropic, compatível com OpenAI), leitor de fatura | por interfaces (`SpeechToText`, `LLMProvider`) |
| Apresentação | `bot/`, `web/` | adaptadores finos | não contêm regra de negócio |

## Decisões principais

- **Uma função de cálculo**: `domain.orcamento.calcular_orcamento` alimenta o resumo
  mensal, o anual e o dashboard; `services.relatorios` é usado igualmente por bot e web
  (há teste de igualdade de números).
- **Integridade em duas barreiras**: serviços + constraint triggers `DEFERRABLE` (ver
  `regras-de-negocio.md`). `financas verificar-integridade` audita o estado existente.
- **Transações explícitas**: cada operação de escrita abre `transacao(fabrica)`; a
  idempotência do Telegram grava `(chat_id, message_id)` na mesma transação.
- **Relógio injetável** (`clock.py`): datas "hoje" e instantes de criação vêm do
  relógio, inclusive `created_at`/`updated_at` gravados pelos serviços.
- **Processador do bot independente do Telegram**: `bot/processador.py` recebe
  `Mensagem` e devolve `Resposta`, o que permite testar fluxos sem rede.
- **Recorrências sem agendador próprio**: o processo web confere, ao subir e a cada
  hora, se falta lançar algum mês (`web/app.py`); a geração é idempotente (ADR 0007).
- ADRs em `docs/adr/`.
