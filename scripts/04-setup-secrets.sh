#!/bin/bash
# =============================================================================
# 04-setup-secrets.sh
# Popula os secrets do Secret Manager com os valores reais.
# Execute este script UMA VEZ após criar a VM, com os valores em mãos.
#
# Uso: bash scripts/04-setup-secrets.sh
# Pré-requisito: estar autenticado no gcloud com permissão de Secret Manager
# =============================================================================

set -euo pipefail

PROJECT_ID="project-87c1c65b-10d3-40d5-999"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log()  { echo -e "${GREEN}[OK]${NC} $*"; }
info() { echo -e "${BLUE}[INFO]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }

upsert_secret() {
  local name="$1"
  local value="$2"

  if gcloud secrets describe "$name" --project="$PROJECT_ID" &>/dev/null; then
    echo -n "$value" | gcloud secrets versions add "$name" \
      --data-file=- --project="$PROJECT_ID"
    log "Secret atualizado: $name"
  else
    echo -n "$value" | gcloud secrets create "$name" \
      --data-file=- --project="$PROJECT_ID"
    log "Secret criado: $name"
  fi
}

echo ""
echo "========================================"
echo "  Configuração de Secrets — Nexus GCP  "
echo "========================================"
echo ""
info "Projeto: $PROJECT_ID"
echo ""
warn "Deixe em branco para pular um secret (mantém valor anterior)."
echo ""

# --- Telegram ---
echo "--- TELEGRAM ---"
read -rp "Token do Bot Telegram (@BotFather): " TELEGRAM_TOKEN
if [ -n "$TELEGRAM_TOKEN" ]; then
  upsert_secret "telegram-bot-token" "$TELEGRAM_TOKEN"
fi

read -rp "Seu ID Telegram (https://t.me/userinfobot): " TELEGRAM_USER_ID
if [ -n "$TELEGRAM_USER_ID" ]; then
  upsert_secret "telegram-allowed-user-id" "$TELEGRAM_USER_ID"
fi

# --- Gemini / OpenCode ---
echo ""
echo "--- GEMINI / OPENCODE ---"
read -rp "Gemini API Key (console.cloud.google.com/apis/credentials): " GEMINI_KEY
if [ -n "$GEMINI_KEY" ]; then
  upsert_secret "gemini-api-key" "$GEMINI_KEY"
fi

# --- WhatsApp ---
echo ""
echo "--- WHATSAPP (opcional) ---"
read -rp "WhatsApp Token (Meta Business): " WA_TOKEN
if [ -n "$WA_TOKEN" ]; then
  upsert_secret "whatsapp-token" "$WA_TOKEN"
fi

read -rp "WhatsApp Phone Number ID: " WA_PHONE_ID
if [ -n "$WA_PHONE_ID" ]; then
  upsert_secret "whatsapp-phone-number-id" "$WA_PHONE_ID"
fi

read -rp "WhatsApp Verify Token (webhook): " WA_VERIFY
if [ -n "$WA_VERIFY" ]; then
  upsert_secret "whatsapp-verify-token" "$WA_VERIFY"
fi

# --- Serper ---
echo ""
echo "--- SERPER (opcional, para busca web nos squads) ---"
read -rp "Serper API Key (serper.dev): " SERPER_KEY
if [ -n "$SERPER_KEY" ]; then
  upsert_secret "serper-api-key" "$SERPER_KEY"
fi

echo ""
echo "========================================"
echo "  Secrets configurados com sucesso!     "
echo "========================================"
echo ""
info "Verifique no Console:"
info "https://console.cloud.google.com/security/secret-manager?project=$PROJECT_ID"
echo ""
info "Para conceder acesso à service account:"
echo ""
cat <<EOF
gcloud projects add-iam-policy-binding $PROJECT_ID \\
  --member="serviceAccount:agenda-lucrativa-sa@${PROJECT_ID}.iam.gserviceaccount.com" \\
  --role="roles/secretmanager.secretAccessor"
EOF
echo ""
info "Próximo passo: reiniciar o bot na VM"
echo "  gcloud compute ssh agenda-nexus --zone=us-central1-a -- 'sudo systemctl restart openclaw-bot'"
echo ""
