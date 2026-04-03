# Estado da Implementação — NEXUS GCP Workspace

> Atualizado automaticamente. Para retomar: "continue de onde parou"

## Progresso por Intervalo

| Intervalo | Descrição | Status | Commit |
|-----------|-----------|--------|--------|
| 1 | Infraestrutura & Scripts VM | ✅ CONCLUÍDO | `interval-1-infra` |
| 2 | Telegram Bot (OpenClaw) | ✅ CONCLUÍDO | `interval-2-bot` |
| 3 | OpenSquad Squads & Skills | ✅ CONCLUÍDO | `interval-3-squads` |
| 4 | Testes & Hardening | ✅ CONCLUÍDO | `interval-4-hardening` |
| Pré-A | Segurança e confiabilidade | ✅ CONCLUÍDO | `b8e3e5e` |
| A | Providers + SkillLoader | ✅ CONCLUÍDO | `b8e3e5e` |
| B | 7 Skills Python | ✅ CONCLUÍDO | `77db9e9` |
| C | Orchestrator + 7 comandos Telegram | ✅ CONCLUÍDO | `ebc9eea` |
| **D** | **Deploy + Testes end-to-end** | **⏳ PENDENTE** | — |

---

## Fase 2 — Skill System + Multi-Model Orchestrator

### O que foi implementado (branch `claude/gcloud-workspace-setup-4Sm2I`)

**Pré-Intervalo A (segurança e confiabilidade):**
- `systemd/openclaw-bot.service` — usuário `nexus` (não root), `ReadWritePaths=/opt/nexus`
- `scripts/02-provision-vm.sh` — cria usuário `nexus` antes dos diretórios
- `config/opencode.json` — apenas `read`/`list` auto-aprovados
- `bot/executor.py` — `run_id` por execução, log em `logs/{squad}/{run_id}.log`, sem dados simulados
- `bot/telegram_bot.py` — `squad_log_path()` lê log mais recente

**Intervalo A:**
- `.claude/agents/explorer.md` — subagente Haiku para leitura de arquivos
- `bot/providers.py` — `GeminiProvider` (Vertex AI ADC) + `ClaudeProvider` (Anthropic API)
- `config/providers.json` — gemini enabled, claude disabled por padrão
- `bot/skill_loader.py` — `SkillLoader.execute(skill, context, provider)`

**Intervalo B — 7 Skills:**
| Skill | Tier | Descrição |
|-------|------|-----------|
| `brainstorm` | FAST | Ideias estruturadas `[{titulo, desenvolvimento, aplicacao}]` |
| `write_plan` | POWERFUL | Plano markdown (Objetivo/Etapas/Métricas/Próximo Passo) |
| `executing_plans` | POWERFUL+FAST | Executa plano .md passo a passo, persiste `ESTADO.md` |
| `autoresearch` | FAST+POWERFUL | Serper busca (FAST) → síntese estruturada (POWERFUL) |
| `frontend_design` | POWERFUL | HTML → PNG via Playwright (fallback HTML) |
| `squad_runner` | POWERFUL | Wraps SquadExecutor, `/run` preservado 100% |
| `custom` | FAST | Executa skills YAML de `skills/custom/` |

**Intervalo C:**
- `bot/orchestrator.py` — Gemini FAST classifica intenção → skill + parâmetros
- `bot/telegram_bot.py` — 7 novos comandos: `/skill`, `/ask`, `/design`, `/research`, `/providers`, `/skills`, `/usage`
- `_TrackingProvider` — rastreia FAST vs POWERFUL por sessão
- `bot/requirements.txt` — adicionado `anthropic>=0.45.0`
- `scripts/04-setup-secrets.sh` — adicionado `claude-api-key`
- `systemd/openclaw-bot.service` — adicionado `DEFAULT_PROVIDER=gemini`

---

## Intervalo D — Deploy + Testes (PRÓXIMO PASSO)

### 1. Merge/deploy na VM

```bash
# Na VM (ou via gcloud ssh):
cd /opt/nexus
git fetch origin claude/gcloud-workspace-setup-4Sm2I
git checkout claude/gcloud-workspace-setup-4Sm2I
git pull

# Instalar nova dependência
/opt/nexus/venv/bin/pip install anthropic>=0.45.0

# Recarregar systemd e reiniciar bot
sudo systemctl daemon-reload
sudo systemctl restart openclaw-bot
sudo systemctl status openclaw-bot
```

### 2. Verificação rápida de logs

```bash
sudo journalctl -u openclaw-bot -n 50 --no-pager
# Esperar ver: "Provider padrão: gemini" e "SkillLoader inicializado: 7 skills"
```

### 3. Checklist de testes no Telegram

```
/providers          → Deve mostrar gemini ✅ enabled, claude ❌ disabled
/skills             → Deve listar 7 skills com ⚡/🧠
/skill brainstorm topic="prospecção açaí"    → 5-10 ideias (FAST)
/skill write-plan goal="campanha estéticas" audience="salões SP"   → plano markdown (POWERFUL)
/research salões SP pico verão              → relatório FAST+POWERFUL
/ask me ajude a criar plano de captação     → FAST roteia → write_plan POWERFUL
/usage                                      → distribuição FAST vs POWERFUL
/run prospeccao                             → funciona igual (preservado 100%)
```

### 4. Ativar Claude (quando API key disponível)

```bash
# 1. Adicionar a key ao Secret Manager:
bash scripts/04-setup-secrets.sh
# → preencher apenas o campo "Claude API Key"

# 2. Editar config/providers.json na VM:
#    "claude": { "enabled": true, ... }

# 3. Reiniciar o bot:
sudo systemctl restart openclaw-bot

# 4. Trocar provider padrão (opcional):
#    Em systemd/openclaw-bot.service:
#    Environment=DEFAULT_PROVIDER=claude
```

### 5. Copiar best-practices do VM (pendente)

```bash
# Estes arquivos existem em /opt/nexus/_opensquad/core/best-practices/
# mas NÃO estão no repo. Copiar para enriquecer os prompts das skills:
gcloud compute ssh agenda-nexus --zone=us-central1-a \
  --project=project-87c1c65b-10d3-40d5-999 -- \
  "cat /opt/nexus/_opensquad/core/best-practices/copywriting.md"
# Repetir para: strategist.md, researching.md, instagram-feed.md,
# instagram-stories.md, instagram-reels.md, whatsapp-broadcast.md
# Salvar em: _opensquad/core/best-practices/
```

---

## Arquitetura atual

```
Telegram
  │
  ├─ /run <squad>            → SquadExecutor (preservado 100%)
  ├─ /skill <nome> [args]    → SkillLoader.execute(skill, context, provider)
  ├─ /ask <mensagem>         → Orchestrator(FAST) → skill → provider(FAST|POWERFUL)
  ├─ /design <briefing>      → frontend_design (POWERFUL) → PNG via Playwright
  ├─ /research <tópico>      → autoresearch depth=quick (FAST+POWERFUL)
  ├─ /providers              → lista providers e status
  ├─ /skills                 → lista skills disponíveis
  └─ /usage                  → chamadas FAST vs POWERFUL da sessão
```

---

## Informações da Infraestrutura

| Campo | Valor |
|-------|-------|
| Project ID | `project-87c1c65b-10d3-40d5-999` |
| VM Name | `agenda-nexus` |
| Zone | `us-central1-a` |
| Machine Type | `e2-standard-4` (4 vCPU / 16 GB) |
| OS | Ubuntu 24.04 LTS |
| Service Account | `agenda-lucrativa-sa@project-87c1c65b-10d3-40d5-999.iam.gserviceaccount.com` |
| Branch de trabalho | `claude/gcloud-workspace-setup-4Sm2I` |

---

## Como Retomar

1. Abrir nova sessão
2. Dizer: **"continue de onde parou"**
3. Próximo passo: **Intervalo D** — deploy na VM + testes end-to-end (ver seção acima)
