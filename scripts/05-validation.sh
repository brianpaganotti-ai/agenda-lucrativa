#!/bin/bash
# =============================================================================
# 05-validation.sh
# Valida o setup completo do Nexus após a VM estar provisionada.
# Roda da sua máquina local (precisa do gcloud autenticado).
#
# Uso: bash scripts/05-validation.sh
# =============================================================================

set -euo pipefail

PROJECT_ID="project-87c1c65b-10d3-40d5-999"
VM_NAME="agenda-nexus"
ZONE="us-central1-a"
BOT_USERNAME="Nexusorquestradorbot"

GREEN='\033[0;32m'; RED='\033[0;31m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
ok()   { echo -e "${GREEN}[✓]${NC} $*"; }
fail() { echo -e "${RED}[✗]${NC} $*"; FAILURES=$((FAILURES+1)); }
warn() { echo -e "${YELLOW}[!]${NC} $*"; }
info() { echo -e "${BLUE}[→]${NC} $*"; }

FAILURES=0

echo ""
echo "==========================================="
echo "  Nexus GCP — Validação do Setup Completo  "
echo "==========================================="
echo ""

# ---------------------------------------------------------------------------
# 1. Verificar VM existe e está rodando
# ---------------------------------------------------------------------------
info "1. Verificando VM $VM_NAME..."

VM_STATUS=$(gcloud compute instances describe "$VM_NAME" \
  --zone="$ZONE" --project="$PROJECT_ID" \
  --format="value(status)" 2>/dev/null || echo "NOT_FOUND")

if [ "$VM_STATUS" = "RUNNING" ]; then
  ok "VM $VM_NAME está RUNNING"
elif [ "$VM_STATUS" = "NOT_FOUND" ]; then
  fail "VM $VM_NAME não encontrada. Execute primeiro: bash scripts/01-create-vm.sh"
else
  fail "VM $VM_NAME status: $VM_STATUS (esperado: RUNNING)"
fi

# Obter IP externo
EXTERNAL_IP=$(gcloud compute instances describe "$VM_NAME" \
  --zone="$ZONE" --project="$PROJECT_ID" \
  --format="value(networkInterfaces[0].accessConfigs[0].natIP)" 2>/dev/null || echo "")

if [ -n "$EXTERNAL_IP" ]; then
  ok "IP externo: $EXTERNAL_IP"
else
  warn "IP externo não encontrado (pode estar usando IP interno apenas)"
fi

# ---------------------------------------------------------------------------
# 2. Verificar serviços systemd na VM
# ---------------------------------------------------------------------------
info "2. Verificando serviços systemd na VM..."

check_service() {
  local service="$1"
  local status
  status=$(gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT_ID" \
    --command="systemctl is-active $service 2>/dev/null" \
    --quiet 2>/dev/null || echo "error")
  if [ "$status" = "active" ]; then
    ok "Serviço $service: ACTIVE"
  else
    fail "Serviço $service: $status (esperado: active)"
    info "  Para ver logs: gcloud compute ssh $VM_NAME --zone=$ZONE -- journalctl -u $service -n 30"
  fi
}

check_service "openclaw-bot"
check_service "agenda-api"

# ---------------------------------------------------------------------------
# 3. Testar endpoint /health da API Flask
# ---------------------------------------------------------------------------
info "3. Testando API Flask (:8080/health)..."

if [ -n "$EXTERNAL_IP" ]; then
  HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" \
    --max-time 10 "http://${EXTERNAL_IP}:8080/health" 2>/dev/null || echo "000")
  if [ "$HTTP_CODE" = "200" ]; then
    ok "API /health respondeu HTTP 200"
  else
    fail "API /health retornou HTTP $HTTP_CODE (esperado: 200)"
    info "  Verificar porta 8080 aberta: gcloud compute firewall-rules list --project=$PROJECT_ID"
  fi
else
  # Testar via SSH tunnel
  info "  Testando via SSH (sem IP externo)..."
  RESPONSE=$(gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT_ID" \
    --command="curl -s http://localhost:8080/health" \
    --quiet 2>/dev/null || echo "error")
  if echo "$RESPONSE" | grep -q "healthy"; then
    ok "API /health respondeu healthy (via SSH)"
  else
    fail "API /health não respondeu corretamente: $RESPONSE"
  fi
fi

# ---------------------------------------------------------------------------
# 4. Verificar secrets no Secret Manager
# ---------------------------------------------------------------------------
info "4. Verificando secrets no Secret Manager..."

check_secret() {
  local secret="$1"
  local value
  value=$(gcloud secrets versions access latest \
    --secret="$secret" --project="$PROJECT_ID" 2>/dev/null || echo "")
  if [ -n "$value" ] && [ "$value" != "SUBSTITUA_PELO_VALOR_REAL" ]; then
    ok "Secret $secret: configurado"
  else
    fail "Secret $secret: vazio ou ainda com valor placeholder"
    info "  Configure com: bash scripts/04-setup-secrets.sh"
  fi
}

check_secret "telegram-bot-token"
check_secret "telegram-allowed-user-id"
check_secret "gemini-api-key"

# WhatsApp é opcional
WA_SECRET=$(gcloud secrets versions access latest \
  --secret="whatsapp-token" --project="$PROJECT_ID" 2>/dev/null || echo "")
if [ -n "$WA_SECRET" ] && [ "$WA_SECRET" != "SUBSTITUA_PELO_VALOR_REAL" ]; then
  ok "Secret whatsapp-token: configurado"
else
  warn "Secret whatsapp-token: não configurado (opcional para testes iniciais)"
fi

# ---------------------------------------------------------------------------
# 5. Verificar OpenSquad instalado na VM
# ---------------------------------------------------------------------------
info "5. Verificando OpenSquad na VM..."

OPENSQUAD_CHECK=$(gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT_ID" \
  --command="test -d /opt/nexus/_opensquad && echo 'ok' || echo 'missing'" \
  --quiet 2>/dev/null || echo "error")

if [ "$OPENSQUAD_CHECK" = "ok" ]; then
  ok "OpenSquad instalado em /opt/nexus/_opensquad"
else
  fail "Diretório _opensquad não encontrado. OpenSquad pode não ter inicializado."
  info "  SSH na VM e execute: cd /opt/nexus && npx opensquad init"
fi

# Verificar OpenCode
OPENCODE_CHECK=$(gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT_ID" \
  --command="which opencode 2>/dev/null && echo 'ok' || echo 'missing'" \
  --quiet 2>/dev/null || echo "error")

if echo "$OPENCODE_CHECK" | grep -q "ok\|/usr/"; then
  ok "OpenCode CLI instalado"
else
  fail "OpenCode CLI não encontrado na VM"
  info "  SSH na VM e execute: npm install -g opencode-ai"
fi

# Verificar squads copiados
SQUADS_CHECK=$(gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT_ID" \
  --command="ls /opt/nexus/squads/*.yaml 2>/dev/null | wc -l" \
  --quiet 2>/dev/null || echo "0")

if [ "$SQUADS_CHECK" -ge "1" ] 2>/dev/null; then
  ok "Squads disponíveis na VM: $SQUADS_CHECK arquivo(s)"
else
  warn "Nenhum squad encontrado em /opt/nexus/squads/"
fi

# ---------------------------------------------------------------------------
# 6. Teste do bot Telegram via API
# ---------------------------------------------------------------------------
info "6. Verificando token do bot Telegram..."

TG_TOKEN=$(gcloud secrets versions access latest \
  --secret="telegram-bot-token" --project="$PROJECT_ID" 2>/dev/null || echo "")

if [ -n "$TG_TOKEN" ]; then
  TG_RESPONSE=$(curl -s --max-time 10 \
    "https://api.telegram.org/bot${TG_TOKEN}/getMe" 2>/dev/null || echo '{"ok":false}')

  if echo "$TG_RESPONSE" | grep -q '"ok":true'; then
    BOT_NAME=$(echo "$TG_RESPONSE" | grep -o '"username":"[^"]*"' | cut -d'"' -f4)
    ok "Token válido — Bot: @$BOT_NAME"
  else
    fail "Token do Telegram inválido ou inacessível"
    info "  Regenere em @BotFather com /revoke e atualize o secret"
  fi
fi

# ---------------------------------------------------------------------------
# Resumo Final
# ---------------------------------------------------------------------------
echo ""
echo "==========================================="
if [ "$FAILURES" -eq 0 ]; then
  echo -e "${GREEN}  ✅ TODOS OS CHECKS PASSARAM!${NC}"
  echo ""
  echo "  Próximos passos:"
  echo "  1. Envie /start para @${BOT_USERNAME} no Telegram"
  echo "  2. Teste: /squads (deve listar prospeccao e conteudo-instagram)"
  echo "  3. Teste: /run prospeccao"
  echo ""
  echo "  Monitoramento:"
  echo "  https://console.cloud.google.com/monitoring?project=$PROJECT_ID"
else
  echo -e "${RED}  ❌ $FAILURES CHECK(S) FALHARAM${NC}"
  echo ""
  echo "  Resolva os erros acima e rode novamente:"
  echo "  bash scripts/05-validation.sh"
fi
echo "==========================================="
echo ""
