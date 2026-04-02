#!/bin/bash
# =============================================================================
# 03-deploy.sh
# Envia atualizações de código para a VM e reinicia os serviços.
# Executar LOCALMENTE sempre que houver mudanças no código.
# Uso: bash scripts/03-deploy.sh
# =============================================================================

set -euo pipefail

PROJECT_ID="project-87c1c65b-10d3-40d5-999"
ZONE="us-central1-a"
VM_NAME="agenda-nexus"
NEXUS_DIR="/opt/nexus"
BRANCH="claude/gcloud-workspace-setup-4Sm2I"

GREEN='\033[0;32m'; BLUE='\033[0;34m'; NC='\033[0m'
log()  { echo -e "${GREEN}[OK]${NC} $*"; }
info() { echo -e "${BLUE}[INFO]${NC} $*"; }

echo ""; echo "==========================="
echo "  Deploy → $VM_NAME"
echo "==========================="; echo ""

# Verificar se VM está rodando
STATUS=$(gcloud compute instances describe "$VM_NAME" \
  --zone="$ZONE" --project="$PROJECT_ID" \
  --format="value(status)" 2>/dev/null || echo "NOT_FOUND")

if [ "$STATUS" != "RUNNING" ]; then
  echo "[ERRO] VM '$VM_NAME' não está rodando (status: $STATUS)"
  echo "Ligue a VM primeiro: gcloud compute instances start $VM_NAME --zone=$ZONE --project=$PROJECT_ID"
  exit 1
fi

info "Sincronizando código..."

# Arquivos a sincronizar (excluindo arquivos sensíveis e gerados)
gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT_ID" \
  -- "cd $NEXUS_DIR && git fetch origin $BRANCH && git reset --hard origin/$BRANCH"

log "Código atualizado via git."

info "Reinstalando dependências Python..."
gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT_ID" \
  -- "cd $NEXUS_DIR && venv/bin/pip install --quiet -r app/requirements.txt $([ -f bot/requirements.txt ] && echo '-r bot/requirements.txt' || echo '')"

info "Reinstalando serviços systemd..."
gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT_ID" -- "
  if [ -d $NEXUS_DIR/systemd ]; then
    sudo cp $NEXUS_DIR/systemd/*.service /etc/systemd/system/
    sudo systemctl daemon-reload
    echo 'Serviços atualizados.'
  fi
"

info "Reiniciando serviços..."
gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT_ID" \
  -- "sudo systemctl restart agenda-api 2>/dev/null || true && sudo systemctl restart openclaw-bot 2>/dev/null || true"

log "Serviços reiniciados."

# Verificação rápida
info "Verificando saúde da API..."
sleep 3
VM_IP=$(gcloud compute instances describe "$VM_NAME" \
  --zone="$ZONE" --project="$PROJECT_ID" \
  --format="value(networkInterfaces[0].accessConfigs[0].natIP)")

if curl -sf "http://${VM_IP}:8080/health" | grep -q "healthy"; then
  log "API respondendo em http://${VM_IP}:8080/health"
else
  echo "[WARN] API não respondeu ainda. Verifique:"
  echo "  gcloud compute ssh $VM_NAME --zone=$ZONE -- 'sudo journalctl -u agenda-api -n 30'"
fi

echo ""
echo "==========================="
echo "  Deploy concluído!"
echo "  VM IP: $VM_IP"
echo "==========================="
