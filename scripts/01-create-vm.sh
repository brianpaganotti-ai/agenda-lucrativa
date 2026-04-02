#!/bin/bash
# =============================================================================
# 01-create-vm.sh
# Cria a VM agenda-nexus no GCP e configura a infraestrutura base.
# Executar LOCALMENTE (com gcloud autenticado).
# Uso: bash scripts/01-create-vm.sh
# =============================================================================

set -euo pipefail

PROJECT_ID="project-87c1c65b-10d3-40d5-999"
ZONE="us-central1-a"
REGION="us-central1"
VM_NAME="agenda-nexus"
MACHINE_TYPE="e2-standard-4"
DISK_SIZE="50GB"
SA_NAME="agenda-lucrativa-sa"
SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
log()  { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
info() { echo -e "${BLUE}[INFO]${NC} $*"; }

echo ""; echo "=============================="
echo "  NEXUS VM Setup — agenda-nexus"
echo "=============================="; echo ""

gcloud config set project "$PROJECT_ID"

# ---------------------------------------------------------------------------
# 1. APIs
# ---------------------------------------------------------------------------
info "Habilitando APIs..."
gcloud services enable \
  compute.googleapis.com \
  secretmanager.googleapis.com \
  firestore.googleapis.com \
  cloudscheduler.googleapis.com \
  iam.googleapis.com \
  iamcredentials.googleapis.com \
  logging.googleapis.com \
  monitoring.googleapis.com \
  --project="$PROJECT_ID"
log "APIs habilitadas."

# ---------------------------------------------------------------------------
# 2. Service Account
# ---------------------------------------------------------------------------
info "Verificando service account: $SA_EMAIL"
if ! gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" &>/dev/null; then
  gcloud iam service-accounts create "$SA_NAME" \
    --display-name="Agenda Lucrativa / Nexus SA" \
    --project="$PROJECT_ID"
  log "Service account criada."
  info "Aguardando propagação da SA no GCP (15s)..."
  sleep 15
else
  log "Service account já existe."
fi

# Aguarda até SA estar disponível (retry até 60s)
for i in $(seq 1 12); do
  if gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" &>/dev/null; then
    break
  fi
  info "SA ainda propagando... aguardando 5s ($i/12)"
  sleep 5
done

for role in \
  roles/datastore.user \
  roles/secretmanager.secretAccessor \
  roles/logging.logWriter \
  roles/monitoring.metricWriter \
  roles/storage.objectAdmin; do
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" --role="$role" --quiet
done
log "Papéis IAM configurados."

# ---------------------------------------------------------------------------
# 3. Firewall
# ---------------------------------------------------------------------------
info "Configurando firewall..."
if ! gcloud compute firewall-rules describe agenda-allow-web --project="$PROJECT_ID" &>/dev/null; then
  gcloud compute firewall-rules create agenda-allow-web \
    --allow="tcp:80,tcp:443,tcp:8080" \
    --target-tags="agenda-server" \
    --description="HTTP/HTTPS e API Flask para agenda-nexus" \
    --project="$PROJECT_ID"
  log "Regra de firewall criada."
else
  log "Regra de firewall já existe."
fi

# ---------------------------------------------------------------------------
# 4. Secrets no Secret Manager
# ---------------------------------------------------------------------------
info "Criando secrets placeholder no Secret Manager..."
create_secret() {
  local name="$1"
  if ! gcloud secrets describe "$name" --project="$PROJECT_ID" &>/dev/null; then
    echo -n "SUBSTITUA_PELO_VALOR_REAL" | \
      gcloud secrets create "$name" --data-file=- --project="$PROJECT_ID"
    log "Secret criado: $name"
  else
    warn "Secret já existe: $name"
  fi
}

create_secret "telegram-bot-token"
create_secret "gemini-api-key"
create_secret "whatsapp-token"
create_secret "agenda-secret-key"
create_secret "serper-api-key"

echo ""
echo "⚠️  IMPORTANTE: Atualize os secrets ANTES de ligar a VM:"
echo "   echo -n 'SEU_TOKEN' | gcloud secrets versions add telegram-bot-token --data-file=- --project=$PROJECT_ID"
echo ""

# ---------------------------------------------------------------------------
# 5. Deletar VMs antigas
# ---------------------------------------------------------------------------
info "Removendo VMs antigas (se existirem)..."
gcloud compute instances delete nexus-v2 \
  --zone=us-central1-a --quiet --project="$PROJECT_ID" 2>/dev/null && log "nexus-v2 deletada." || warn "nexus-v2 não encontrada."

gcloud compute instances delete instance-20260326-232934 \
  --zone=us-central1-c --quiet --project="$PROJECT_ID" 2>/dev/null && log "instance-20260326-232934 deletada." || warn "instance-20260326-232934 não encontrada."

# ---------------------------------------------------------------------------
# 6. Criar nova VM
# ---------------------------------------------------------------------------
info "Criando VM: $VM_NAME ($MACHINE_TYPE)..."
gcloud compute instances create "$VM_NAME" \
  --zone="$ZONE" \
  --machine-type="$MACHINE_TYPE" \
  --image-family=ubuntu-2404-lts-amd64 \
  --image-project=ubuntu-os-cloud \
  --boot-disk-size="$DISK_SIZE" \
  --boot-disk-type=pd-balanced \
  --tags=agenda-server \
  --service-account="$SA_EMAIL" \
  --scopes=cloud-platform \
  --metadata-from-file=startup-script=scripts/02-provision-vm.sh \
  --metadata=enable-osconfig=TRUE \
  --restart-on-failure \
  --maintenance-policy=MIGRATE \
  --project="$PROJECT_ID"

VM_IP=$(gcloud compute instances describe "$VM_NAME" \
  --zone="$ZONE" --project="$PROJECT_ID" \
  --format="value(networkInterfaces[0].accessConfigs[0].natIP)")

log "VM criada!"
echo ""
echo "=============================="
echo "  VM: $VM_NAME"
echo "  IP externo: $VM_IP"
echo "  Zona: $ZONE"
echo "=============================="
echo ""
echo "Próximos passos:"
echo "1. Aguarde ~3 min para o provisionamento completar"
echo "2. Atualize os secrets com valores reais (ver acima)"
echo "3. Monitore o startup:"
echo "   gcloud compute ssh $VM_NAME --zone=$ZONE -- 'sudo journalctl -f'"
echo "4. Quando provisionado, execute: bash scripts/03-deploy.sh"
echo ""
