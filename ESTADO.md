# Estado da Implementação — NEXUS GCP Workspace

> Atualizado automaticamente. Para retomar: "continue de onde parou"

## Progresso por Intervalo

| Intervalo | Descrição | Status | Commit |
|-----------|-----------|--------|--------|
| 1 | Infraestrutura & Scripts VM | ✅ CONCLUÍDO | `interval-1-infra` |
| 2 | Telegram Bot (OpenClaw) | ⏳ PENDENTE | — |
| 3 | OpenSquad Squads & Skills | ⏳ PENDENTE | — |
| 4 | Testes & Hardening | ⏳ PENDENTE | — |

## Arquivos Criados (Intervalo 1)

- `scripts/01-create-vm.sh` — cria VM agenda-nexus, APIs, SA, firewall, secrets
- `scripts/02-provision-vm.sh` — startup script da VM (Node20, OpenCode, Playwright, OpenSquad)
- `scripts/03-deploy.sh` — atualiza código na VM via git + reinicia serviços

## Arquivos Deletados

- `Dockerfile` (raiz), `app.yaml`, `app/Dockerfile` — dead code sem containers
- `scripts/setup_scheduler_agenda.sh`, `scripts/agenda_reconcile_v2.sh`, `scripts/update_agenda_v8.sh` — templates irrelevantes

## Próximo Intervalo (2) — Telegram Bot

Criar os seguintes arquivos:

### `bot/telegram_bot.py`
Bot principal (python-telegram-bot async). Comandos:
- `/start` — boas-vindas
- `/squads` — lista squads disponíveis em `squads/`
- `/run <nome>` — executa squad via `opencode run -p "/opensquad run <nome>"`
- `/status` — squads em execução
- `/logs <nome>` — últimas 50 linhas do log
- `/approve` / `/reject` — aprovação de checkpoints

Carrega secrets do Secret Manager:
- `telegram-bot-token`
- `gemini-api-key`

Execução de squad:
```python
proc = subprocess.Popen(
    ["opencode", "run", "-p", f"/opensquad run {squad_name}"],
    cwd="/opt/nexus",
    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True
)
# Stream chunks para Telegram
```

### `bot/requirements.txt`
```
python-telegram-bot>=20.0
google-cloud-secret-manager
google-cloud-firestore
```

### `systemd/openclaw-bot.service`
```ini
[Unit]
Description=OpenClaw Telegram Bot
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/nexus
ExecStart=/opt/nexus/venv/bin/python bot/telegram_bot.py
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### `systemd/agenda-api.service`
```ini
[Unit]
Description=Agenda Lucrativa API
After=network.target

[Service]
Type=simple
User=root
WorkingDirectory=/opt/nexus/app
ExecStart=/opt/nexus/venv/bin/gunicorn --bind 0.0.0.0:8080 app:app
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

### `scripts/04-setup-secrets.sh`
Script auxiliar para preencher os secrets no Secret Manager com valores reais.
Pede input do usuário para cada secret.

## Informações da Infraestrutura

| Campo | Valor |
|-------|-------|
| Project ID | `project-87c1c65b-10d3-40d5-999` |
| VM Name | `agenda-nexus` |
| Zone | `us-central1-a` |
| Machine Type | `e2-standard-4` (4 vCPU / 16 GB) |
| OS | Ubuntu 24.04 LTS |
| Disk | 50 GB pd-balanced |
| Service Account | `agenda-lucrativa-sa@project-87c1c65b-10d3-40d5-999.iam.gserviceaccount.com` |
| Branch de trabalho | `claude/gcloud-workspace-setup-4Sm2I` |

## VMs Deletadas

- `nexus-v2` (us-central1-a) — deletada pelo 01-create-vm.sh
- `instance-20260326-232934` (us-central1-c, 10.128.0.2) — deletada pelo 01-create-vm.sh

## Como Retomar

1. Abrir nova sessão
2. Dizer: **"continue de onde parou"**
3. Eu lerei este arquivo e continuarei pelo Intervalo 2
