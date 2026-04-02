#!/bin/bash
# =============================================================================
# 06-monitoring.sh
# Configura Cloud Ops Agent (logging + monitoring) na VM e cria
# uptime check para o endpoint /health da API Flask.
#
# Uso: bash scripts/06-monitoring.sh
# Pré-requisito: VM agenda-nexus rodando, gcloud autenticado
# =============================================================================

set -euo pipefail

PROJECT_ID="project-87c1c65b-10d3-40d5-999"
VM_NAME="agenda-nexus"
ZONE="us-central1-a"

GREEN='\033[0;32m'; BLUE='\033[0;34m'; NC='\033[0m'
log()  { echo -e "${GREEN}[OK]${NC} $*"; }
info() { echo -e "${BLUE}[→]${NC} $*"; }

echo ""
echo "======================================"
echo "  Nexus — Configuração de Monitoring  "
echo "======================================"
echo ""

# ---------------------------------------------------------------------------
# 1. Habilitar APIs necessárias
# ---------------------------------------------------------------------------
info "Habilitando APIs de monitoring..."
gcloud services enable \
  monitoring.googleapis.com \
  logging.googleapis.com \
  opsconfig.googleapis.com \
  --project="$PROJECT_ID"
log "APIs habilitadas."

# ---------------------------------------------------------------------------
# 2. Instalar Cloud Ops Agent na VM
# ---------------------------------------------------------------------------
info "Instalando Cloud Ops Agent na VM $VM_NAME..."

gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT_ID" \
  --command="
    # Instala o Ops Agent (logging + monitoring)
    curl -sSO https://dl.google.com/cloudagents/add-google-cloud-ops-agent-repo.sh
    sudo bash add-google-cloud-ops-agent-repo.sh --also-install
    sudo systemctl enable --now google-cloud-ops-agent
    echo 'Ops Agent instalado e rodando.'
  " --quiet

log "Cloud Ops Agent instalado."

# ---------------------------------------------------------------------------
# 3. Configurar coleta de logs dos serviços systemd
# ---------------------------------------------------------------------------
info "Configurando coleta de logs (openclaw-bot + agenda-api)..."

gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT_ID" \
  --command="
    sudo tee /etc/google-cloud-ops-agent/config.yaml > /dev/null << 'OPSCONFIG'
logging:
  receivers:
    openclaw_bot:
      type: systemd_journald
      units:
        - openclaw-bot
    agenda_api:
      type: systemd_journald
      units:
        - agenda-api
    opensquad_logs:
      type: files
      include_paths:
        - /opt/nexus/logs/*.log

  service:
    pipelines:
      nexus_pipeline:
        receivers: [openclaw_bot, agenda_api, opensquad_logs]

metrics:
  receivers:
    hostmetrics:
      type: hostmetrics
      collection_interval: 60s
  service:
    pipelines:
      default_pipeline:
        receivers: [hostmetrics]
OPSCONFIG

    sudo systemctl restart google-cloud-ops-agent
    echo 'Configuração aplicada.'
  " --quiet

log "Coleta de logs configurada."

# ---------------------------------------------------------------------------
# 4. Criar Uptime Check para /health
# ---------------------------------------------------------------------------
info "Criando uptime check para /health..."

# Obter IP externo da VM
EXTERNAL_IP=$(gcloud compute instances describe "$VM_NAME" \
  --zone="$ZONE" --project="$PROJECT_ID" \
  --format="value(networkInterfaces[0].accessConfigs[0].natIP)" 2>/dev/null || echo "")

if [ -n "$EXTERNAL_IP" ]; then
  # Criar uptime check via API REST do Cloud Monitoring
  ACCESS_TOKEN=$(gcloud auth print-access-token)

  curl -s -X POST \
    "https://monitoring.googleapis.com/v3/projects/${PROJECT_ID}/uptimeCheckConfigs" \
    -H "Authorization: Bearer ${ACCESS_TOKEN}" \
    -H "Content-Type: application/json" \
    -d "{
      \"displayName\": \"Nexus API Health Check\",
      \"httpCheck\": {
        \"path\": \"/health\",
        \"port\": 8080,
        \"requestMethod\": \"GET\",
        \"validateSsl\": false
      },
      \"monitoredResource\": {
        \"type\": \"uptime_url\",
        \"labels\": {
          \"host\": \"${EXTERNAL_IP}\",
          \"project_id\": \"${PROJECT_ID}\"
        }
      },
      \"period\": \"300s\",
      \"timeout\": \"10s\"
    }" > /dev/null

  log "Uptime check criado para http://${EXTERNAL_IP}:8080/health (a cada 5min)"
else
  echo "IP externo não disponível — uptime check pulado (configure manualmente no Console)"
fi

# ---------------------------------------------------------------------------
# 5. Criar alerta de downtime (política de alertas)
# ---------------------------------------------------------------------------
info "Criando política de alertas para downtime..."

ACCESS_TOKEN=$(gcloud auth print-access-token)

NOTIFICATION_CHANNELS=$(curl -s \
  "https://monitoring.googleapis.com/v3/projects/${PROJECT_ID}/notificationChannels" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  | grep -o '"name": "[^"]*"' | head -1 | cut -d'"' -f4 || echo "")

CHANNELS_JSON="[]"
if [ -n "$NOTIFICATION_CHANNELS" ]; then
  CHANNELS_JSON="[\"$NOTIFICATION_CHANNELS\"]"
fi

curl -s -X POST \
  "https://monitoring.googleapis.com/v3/projects/${PROJECT_ID}/alertPolicies" \
  -H "Authorization: Bearer ${ACCESS_TOKEN}" \
  -H "Content-Type: application/json" \
  -d "{
    \"displayName\": \"Nexus API Downtime Alert\",
    \"conditions\": [{
      \"displayName\": \"API /health indisponível por 5min\",
      \"conditionThreshold\": {
        \"filter\": \"metric.type=\\\"monitoring.googleapis.com/uptime_check/check_passed\\\" resource.type=\\\"uptime_url\\\"\",
        \"comparison\": \"COMPARISON_LT\",
        \"thresholdValue\": 1,
        \"duration\": \"300s\",
        \"aggregations\": [{
          \"alignmentPeriod\": \"60s\",
          \"perSeriesAligner\": \"ALIGN_FRACTION_TRUE\"
        }]
      }
    }],
    \"notificationChannels\": ${CHANNELS_JSON},
    \"alertStrategy\": {
      \"autoClose\": \"604800s\"
    }
  }" > /dev/null

log "Política de alertas criada."

# ---------------------------------------------------------------------------
# 6. Configurar backup automático do _opensquad/
# ---------------------------------------------------------------------------
info "Configurando backup automático para Cloud Storage..."

BACKUP_BUCKET="gs://${PROJECT_ID}-nexus-backup"

# Criar bucket se não existir
gcloud storage buckets create "$BACKUP_BUCKET" \
  --location=us-central1 \
  --project="$PROJECT_ID" 2>/dev/null && log "Bucket criado: $BACKUP_BUCKET" \
  || log "Bucket já existe: $BACKUP_BUCKET"

# Criar script de backup na VM
gcloud compute ssh "$VM_NAME" --zone="$ZONE" --project="$PROJECT_ID" \
  --command="
    sudo tee /opt/nexus/scripts/backup.sh > /dev/null << 'BACKUP'
#!/bin/bash
DATE=\$(date +%Y%m%d_%H%M)
BUCKET=${BACKUP_BUCKET}

# Backup do estado do OpenSquad
gsutil -m rsync -r /opt/nexus/_opensquad/ \${BUCKET}/opensquad/\${DATE}/
gsutil -m rsync -r /opt/nexus/squads/ \${BUCKET}/squads/\${DATE}/
gsutil -m rsync -r /opt/nexus/logs/ \${BUCKET}/logs/\${DATE}/

echo \"Backup concluído: \${DATE}\"
BACKUP

    sudo chmod +x /opt/nexus/scripts/backup.sh

    # Agendar via cron (todo dia às 03:00 Brasília = 06:00 UTC)
    (crontab -l 2>/dev/null; echo '0 6 * * * /opt/nexus/scripts/backup.sh >> /var/log/nexus-backup.log 2>&1') | crontab -

    echo 'Backup cron configurado.'
  " --quiet

log "Backup diário configurado → $BACKUP_BUCKET"

# ---------------------------------------------------------------------------
# Resumo
# ---------------------------------------------------------------------------
echo ""
echo "======================================"
echo "  Monitoring configurado com sucesso! "
echo "======================================"
echo ""
echo "  Dashboards:"
echo "  → Logs: https://console.cloud.google.com/logs/query?project=$PROJECT_ID"
echo "  → Uptime: https://console.cloud.google.com/monitoring/uptime?project=$PROJECT_ID"
echo "  → Alertas: https://console.cloud.google.com/monitoring/alerting?project=$PROJECT_ID"
echo "  → Backup: https://console.cloud.google.com/storage/browser/${PROJECT_ID}-nexus-backup"
echo ""
