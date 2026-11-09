# Implantação em nuvem

O mesmo código e a mesma imagem rodam na nuvem; só a configuração muda.

1. **Banco**: PostgreSQL 16 gerenciado (RDS, Cloud SQL, Azure Database, Neon…). Aponte
   `DATABASE_URL` para ele e rode `alembic upgrade head` como tarefa de release.
2. **Web**: um serviço de contêiner (Cloud Run, ECS/Fargate, Fly, Render…) com a
   imagem e o comando padrão; HTTPS na borda; `AMBIENTE=producao` (cookie `Secure`).
   O uvicorn usa `--proxy-headers`; restrinja `--forwarded-allow-ips` ao proxy da
   plataforma para que o limite de tentativas por IP veja o IP real.
3. **Bot**: defina `TELEGRAM_WEBHOOK_URL` (URL pública HTTPS do serviço do bot) e exponha
   `TELEGRAM_WEBHOOK_PORTA`. O caminho e o `secret_token` do webhook são derivados do
   token, sem expô-lo. Sem essa variável, o bot volta a usar polling (precisa de um
   processo sempre ativo e de no máximo uma instância).
4. **Segredos**: `TELEGRAM_BOT_TOKEN`, `WEB_SECRET_KEY`, senhas do banco e chaves de API
   no gerenciador de segredos da plataforma, injetados como variáveis de ambiente.
5. **Voz**: o faster-whisper precisa de ~1–2 GB de RAM (modelo `small`) e baixa o modelo
   na primeira transcrição — monte um volume em `/home/financas/.cache/huggingface` ou
   use `STT_PROVEDOR=openai` (o áudio passa a ser enviado à OpenAI).
6. **Backup**: o PostgreSQL gerenciado faz backups automáticos; mantenha também
   `scripts/backup.sh` agendado se quiser cópias portáveis fora do provedor.
7. **Escala**: a web pode ter várias instâncias (sessões ficam no banco); o bot em
   webhook também, pois a idempotência e as confirmações são garantidas pelo banco.
