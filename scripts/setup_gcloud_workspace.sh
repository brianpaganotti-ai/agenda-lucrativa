#!/bin/bash
# =============================================================================
# setup_gcloud_workspace.sh
# Configura o workspace do Google Cloud para o projeto Agenda Lucrativa
# Uso: bash scripts/setup_gcloud_workspace.sh
# =============================================================================

set -euo pipefail

# ---------------------------------------------------------------------------
# VARIAVEIS — altere conforme necessário
# ---------------------------------------------------------------------------
PROJECT_ID="project-87c1c65b-10d3-40d5-999"
REGION="us-central1"
SA_NAME="agenda-lucrativa-sa"
SA_DISPLAY="Agenda Lucrativa Service Account"
KEY_FILE=".gcloud/agenda-lucrativa-key.json"
CLOUD_RUN_SERVICE="agenda-lucrativa"
SCHEDULER_JOB="agenda-execucao-diaria"
SCHEDULER_SCHEDULE="0 8 * * *"   # 08:00 UTC todo dia
FIRESTORE_LOCATION="us-east1"

# Cores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

log()  { echo -e "${GREEN}[OK]${NC} $*"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
info() { echo -e "${BLUE}[INFO]${NC} $*"; }
err()  { echo -e "${RED}[ERRO]${NC} $*" >&2; }

# ---------------------------------------------------------------------------
# 1. PRÉ-REQUISITOS
# ---------------------------------------------------------------------------
echo ""
echo "============================================="
echo "  Agenda Lucrativa — GCloud Workspace Setup  "
echo "============================================="
echo ""

info "Verificando pré-requisitos..."

if ! command -v gcloud &>/dev/null; then
  err "gcloud CLI não encontrado. Instale via: https://cloud.google.com/sdk/docs/install"
  exit 1
fi

if ! command -v docker &>/dev/null; then
  warn "docker não encontrado — etapas de build serão puladas."
  DOCKER_AVAILABLE=false
else
  DOCKER_AVAILABLE=true
fi

log "gcloud CLI encontrado: $(gcloud --version | head -1)"

# ---------------------------------------------------------------------------
# 2. AUTENTICAÇÃO E PROJETO
# ---------------------------------------------------------------------------
info "Configurando projeto: $PROJECT_ID"

gcloud config set project "$PROJECT_ID"
gcloud config set compute/region "$REGION"

# Verifica se está autenticado
if ! gcloud auth print-identity-token &>/dev/null; then
  warn "Não autenticado. Iniciando login..."
  gcloud auth login
fi

log "Projeto configurado: $PROJECT_ID | Região: $REGION"

# ---------------------------------------------------------------------------
# 3. HABILITAR APIs
# ---------------------------------------------------------------------------
info "Habilitando APIs necessárias..."

APIS=(
  "cloudrun.googleapis.com"
  "firestore.googleapis.com"
  "appengine.googleapis.com"
  "cloudscheduler.googleapis.com"
  "containerregistry.googleapis.com"
  "artifactregistry.googleapis.com"
  "secretmanager.googleapis.com"
  "iam.googleapis.com"
  "iamcredentials.googleapis.com"
  "cloudbuild.googleapis.com"
  "sqladmin.googleapis.com"
  "storage.googleapis.com"
  "logging.googleapis.com"
  "monitoring.googleapis.com"
)

for api in "${APIS[@]}"; do
  if gcloud services list --enabled --filter="name:$api" --format="value(name)" | grep -q "$api"; then
    log "API já habilitada: $api"
  else
    info "Habilitando: $api"
    gcloud services enable "$api" --project="$PROJECT_ID"
    log "Habilitada: $api"
  fi
done

# ---------------------------------------------------------------------------
# 4. SERVICE ACCOUNT
# ---------------------------------------------------------------------------
info "Configurando Service Account: $SA_NAME"

SA_EMAIL="${SA_NAME}@${PROJECT_ID}.iam.gserviceaccount.com"

if gcloud iam service-accounts describe "$SA_EMAIL" --project="$PROJECT_ID" &>/dev/null; then
  log "Service account já existe: $SA_EMAIL"
else
  gcloud iam service-accounts create "$SA_NAME" \
    --display-name="$SA_DISPLAY" \
    --project="$PROJECT_ID"
  log "Service account criada: $SA_EMAIL"
fi

# Papéis necessários para a aplicação
ROLES=(
  "roles/datastore.user"
  "roles/run.invoker"
  "roles/cloudsql.client"
  "roles/secretmanager.secretAccessor"
  "roles/storage.objectViewer"
  "roles/logging.logWriter"
  "roles/monitoring.metricWriter"
  "roles/cloudscheduler.jobRunner"
)

for role in "${ROLES[@]}"; do
  info "Atribuindo papel: $role"
  gcloud projects add-iam-policy-binding "$PROJECT_ID" \
    --member="serviceAccount:${SA_EMAIL}" \
    --role="$role" \
    --quiet
  log "Papel atribuído: $role"
done

# ---------------------------------------------------------------------------
# 5. GERAR CHAVE DA SERVICE ACCOUNT
# ---------------------------------------------------------------------------
mkdir -p .gcloud

if [ -f "$KEY_FILE" ]; then
  warn "Arquivo de chave já existe: $KEY_FILE (não sobrescrevendo)"
else
  info "Gerando chave JSON da service account..."
  gcloud iam service-accounts keys create "$KEY_FILE" \
    --iam-account="$SA_EMAIL" \
    --project="$PROJECT_ID"
  log "Chave gerada em: $KEY_FILE"
  warn "IMPORTANTE: Nunca commit o arquivo $KEY_FILE no git!"
fi

export GOOGLE_APPLICATION_CREDENTIALS="$(pwd)/$KEY_FILE"
log "GOOGLE_APPLICATION_CREDENTIALS configurado."

# ---------------------------------------------------------------------------
# 6. FIRESTORE — banco de dados
# ---------------------------------------------------------------------------
info "Configurando Firestore (modo Native)..."

if gcloud firestore databases list --project="$PROJECT_ID" 2>/dev/null | grep -q "projects/"; then
  log "Firestore já configurado."
else
  gcloud firestore databases create \
    --location="$FIRESTORE_LOCATION" \
    --project="$PROJECT_ID" || warn "Firestore já pode existir — verifique no Console."
  log "Firestore criado em: $FIRESTORE_LOCATION"
fi

# ---------------------------------------------------------------------------
# 7. SECRET MANAGER — variáveis sensíveis
# ---------------------------------------------------------------------------
info "Criando secrets no Secret Manager..."

create_secret_if_missing() {
  local name="$1"
  local value="$2"
  if gcloud secrets describe "$name" --project="$PROJECT_ID" &>/dev/null; then
    log "Secret já existe: $name"
  else
    echo -n "$value" | gcloud secrets create "$name" \
      --data-file=- \
      --project="$PROJECT_ID"
    log "Secret criado: $name"
  fi
}

# Secrets placeholder — substitua pelos valores reais
create_secret_if_missing "agenda-api-key"        "SUBSTITUA_PELO_VALOR_REAL"
create_secret_if_missing "agenda-secret-key"     "SUBSTITUA_PELO_VALOR_REAL"
create_secret_if_missing "agenda-whatsapp-token" "SUBSTITUA_PELO_VALOR_REAL"
create_secret_if_missing "agenda-serper-key"     "SUBSTITUA_PELO_VALOR_REAL"
create_secret_if_missing "agenda-email-password" "SUBSTITUA_PELO_VALOR_REAL"

# ---------------------------------------------------------------------------
# 8. ARTIFACT REGISTRY — repositório de imagens Docker
# ---------------------------------------------------------------------------
REPO_NAME="agenda-lucrativa-repo"
info "Configurando Artifact Registry: $REPO_NAME"

if gcloud artifacts repositories describe "$REPO_NAME" \
    --location="$REGION" --project="$PROJECT_ID" &>/dev/null; then
  log "Repositório já existe: $REPO_NAME"
else
  gcloud artifacts repositories create "$REPO_NAME" \
    --repository-format=docker \
    --location="$REGION" \
    --description="Imagens Docker do Agenda Lucrativa" \
    --project="$PROJECT_ID"
  log "Repositório criado: $REPO_NAME"
fi

# Configura autenticação Docker para o Artifact Registry
gcloud auth configure-docker "${REGION}-docker.pkg.dev" --quiet
log "Docker autenticado para: ${REGION}-docker.pkg.dev"

IMAGE_URL="${REGION}-docker.pkg.dev/${PROJECT_ID}/${REPO_NAME}/agenda-lucrativa"

# ---------------------------------------------------------------------------
# 9. BUILD E DEPLOY NO CLOUD RUN (opcional)
# ---------------------------------------------------------------------------
if [ "$DOCKER_AVAILABLE" = true ] && [ -f "app/Dockerfile" ]; then
  info "Construindo imagem Docker..."
  docker build -t "$IMAGE_URL:latest" -f app/Dockerfile app/
  docker push "$IMAGE_URL:latest"
  log "Imagem enviada: $IMAGE_URL:latest"

  info "Fazendo deploy no Cloud Run..."
  gcloud run deploy "$CLOUD_RUN_SERVICE" \
    --image="$IMAGE_URL:latest" \
    --platform=managed \
    --region="$REGION" \
    --service-account="$SA_EMAIL" \
    --set-env-vars="PROJECT_ID=${PROJECT_ID}" \
    --set-secrets="API_KEY=agenda-api-key:latest,SECRET_KEY=agenda-secret-key:latest,WHATSAPP_TOKEN=agenda-whatsapp-token:latest" \
    --allow-unauthenticated \
    --min-instances=0 \
    --max-instances=3 \
    --memory=512Mi \
    --cpu=1 \
    --project="$PROJECT_ID"

  SERVICE_URL=$(gcloud run services describe "$CLOUD_RUN_SERVICE" \
    --region="$REGION" --project="$PROJECT_ID" \
    --format="value(status.url)")
  log "Cloud Run deploy concluído: $SERVICE_URL"
else
  warn "Docker não disponível ou Dockerfile ausente — deploy no Cloud Run pulado."
  SERVICE_URL="https://${CLOUD_RUN_SERVICE}-<hash>-uc.a.run.app"
fi

# ---------------------------------------------------------------------------
# 10. CLOUD SCHEDULER — jobs agendados
# ---------------------------------------------------------------------------
info "Configurando Cloud Scheduler..."

# Garante App Engine habilitado (necessário para Scheduler)
gcloud app describe --project="$PROJECT_ID" &>/dev/null || \
  gcloud app create --region="$REGION" --project="$PROJECT_ID" 2>/dev/null || true

if gcloud scheduler jobs describe "$SCHEDULER_JOB" \
    --location="$REGION" --project="$PROJECT_ID" &>/dev/null; then
  log "Job já existe: $SCHEDULER_JOB"
else
  gcloud scheduler jobs create http "$SCHEDULER_JOB" \
    --location="$REGION" \
    --schedule="$SCHEDULER_SCHEDULE" \
    --time-zone="America/Sao_Paulo" \
    --uri="${SERVICE_URL}/executar" \
    --http-method=POST \
    --message-body='{"source":"scheduler"}' \
    --headers="Content-Type=application/json" \
    --oidc-service-account-email="$SA_EMAIL" \
    --project="$PROJECT_ID"
  log "Job criado: $SCHEDULER_JOB (${SCHEDULER_SCHEDULE})"
fi

# ---------------------------------------------------------------------------
# 11. GCLOUD CONFIG — atualiza arquivo local
# ---------------------------------------------------------------------------
info "Atualizando .gcloud/config.yaml..."
cat > .gcloud/config.yaml <<EOF
# Google Cloud Configuration — gerado por setup_gcloud_workspace.sh

[core]
project = ${PROJECT_ID}
account = $(gcloud config get-value account 2>/dev/null || echo "seu-email@example.com")

[compute]
region = ${REGION}

[run]
region = ${REGION}
EOF
log ".gcloud/config.yaml atualizado."

# ---------------------------------------------------------------------------
# 12. RESUMO FINAL
# ---------------------------------------------------------------------------
echo ""
echo "============================================="
echo "       SETUP CONCLUÍDO COM SUCESSO!          "
echo "============================================="
echo ""
echo "  Projeto     : $PROJECT_ID"
echo "  Região      : $REGION"
echo "  SA Email    : $SA_EMAIL"
echo "  Chave JSON  : $KEY_FILE"
echo "  Imagem      : $IMAGE_URL:latest"
echo "  Cloud Run   : $SERVICE_URL"
echo "  Scheduler   : $SCHEDULER_JOB ($SCHEDULER_SCHEDULE America/Sao_Paulo)"
echo ""
echo "  Próximos passos:"
echo "  1. Atualize os secrets no Secret Manager com os valores reais:"
echo "     gcloud secrets versions add <nome> --data-file=-"
echo "  2. Configure o .env com GOOGLE_APPLICATION_CREDENTIALS=$KEY_FILE"
echo "  3. Teste o serviço: curl \$SERVICE_URL/health"
echo ""
