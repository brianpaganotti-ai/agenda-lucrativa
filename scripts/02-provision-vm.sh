#!/bin/bash
# =============================================================================
# 02-provision-vm.sh
# Startup script da VM agenda-nexus — executa automaticamente no 1º boot.
# NÃO executar manualmente. Passado via --metadata-from-file ao criar a VM.
# =============================================================================

set -euo pipefail

LOG="/var/log/agenda-provision.log"
exec > >(tee -a "$LOG") 2>&1

echo ""; echo "=============================="
echo "  NEXUS VM Provisioning"
echo "  $(date)"
echo "=============================="; echo ""

# Só executa uma vez
if [ -f /opt/.provisioned ]; then
  echo "[SKIP] VM já provisionada. Saindo."
  exit 0
fi

PROJECT_ID="project-87c1c65b-10d3-40d5-999"
NEXUS_DIR="/opt/nexus"
REPO_URL="https://github.com/brianpaganotti-ai/agenda-lucrativa.git"
BRANCH="claude/gcloud-workspace-setup-4Sm2I"

# ---------------------------------------------------------------------------
# 1. Sistema base
# ---------------------------------------------------------------------------
echo "[INFO] Atualizando sistema..."
apt-get update -qq
apt-get install -y -qq \
  curl git python3-pip python3-venv \
  build-essential ca-certificates gnupg \
  caddy jq

echo "[OK] Sistema base instalado."

# ---------------------------------------------------------------------------
# 2. Node.js 20
# ---------------------------------------------------------------------------
echo "[INFO] Instalando Node.js 20..."
curl -fsSL https://deb.nodesource.com/setup_20.x | bash -
apt-get install -y -qq nodejs
node --version && npm --version
echo "[OK] Node.js instalado."

# ---------------------------------------------------------------------------
# 3. OpenCode CLI
# ---------------------------------------------------------------------------
echo "[INFO] Instalando OpenCode CLI..."
npm install -g opencode-ai
opencode --version || true
echo "[OK] OpenCode instalado."

# ---------------------------------------------------------------------------
# 4. Playwright + Chromium
# ---------------------------------------------------------------------------
echo "[INFO] Instalando Playwright + Chromium..."
npx --yes playwright install chromium --with-deps
echo "[OK] Playwright/Chromium instalado."

# ---------------------------------------------------------------------------
# 5. Usuário de serviço + diretório do projeto
# ---------------------------------------------------------------------------
echo "[INFO] Criando usuário de serviço 'nexus'..."
useradd -r -s /bin/false -d "$NEXUS_DIR" nexus 2>/dev/null || true

echo "[INFO] Clonando repositório..."
mkdir -p "$NEXUS_DIR"
git clone --branch "$BRANCH" "$REPO_URL" "$NEXUS_DIR"
cd "$NEXUS_DIR"
echo "[OK] Repositório clonado em $NEXUS_DIR"

# ---------------------------------------------------------------------------
# 6. OpenSquad init
# ---------------------------------------------------------------------------
echo "[INFO] Inicializando OpenSquad..."
cd "$NEXUS_DIR"
npx --yes opensquad init || echo "[WARN] opensquad init falhou — pode precisar de interação manual."

# ---------------------------------------------------------------------------
# 7. Python venv
# ---------------------------------------------------------------------------
echo "[INFO] Configurando Python venv..."
python3 -m venv "$NEXUS_DIR/venv"

# Instalar dependências do app Flask
"$NEXUS_DIR/venv/bin/pip" install --quiet -r "$NEXUS_DIR/app/requirements.txt"

# Instalar dependências do bot (criadas no Intervalo 2, instala se existir)
if [ -f "$NEXUS_DIR/bot/requirements.txt" ]; then
  "$NEXUS_DIR/venv/bin/pip" install --quiet -r "$NEXUS_DIR/bot/requirements.txt"
fi

echo "[OK] Python venv configurado."

# ---------------------------------------------------------------------------
# 8. Serviços systemd
# ---------------------------------------------------------------------------
echo "[INFO] Instalando serviços systemd..."

if [ -d "$NEXUS_DIR/systemd" ]; then
  for svc in "$NEXUS_DIR"/systemd/*.service; do
    cp "$svc" /etc/systemd/system/
    echo "[OK] Serviço instalado: $(basename $svc)"
  done
  systemctl daemon-reload

  # Habilita agenda-api sempre (Flask)
  systemctl enable agenda-api 2>/dev/null || true
  systemctl start agenda-api 2>/dev/null || true

  # Habilita openclaw-bot se existir o script
  if [ -f "$NEXUS_DIR/bot/telegram_bot.py" ]; then
    systemctl enable openclaw-bot 2>/dev/null || true
    systemctl start openclaw-bot 2>/dev/null || true
  fi
else
  echo "[WARN] Diretório systemd/ não encontrado. Serviços serão configurados no Intervalo 2."
fi

# ---------------------------------------------------------------------------
# 9. Caddy (HTTPS automático)
# ---------------------------------------------------------------------------
echo "[INFO] Configurando Caddy..."
cat > /etc/caddy/Caddyfile <<'EOF'
# Substitua pelo seu domínio ou IP estático quando disponível
# Por ora, serve localmente na porta 80
:80 {
    reverse_proxy localhost:8080
}
EOF
systemctl enable caddy
systemctl restart caddy
echo "[OK] Caddy configurado."

# ---------------------------------------------------------------------------
# 10. Google Cloud Ops Agent
# ---------------------------------------------------------------------------
echo "[INFO] Instalando Google Cloud Ops Agent..."
curl -sSO https://dl.google.com/cloudagents/add-google-cloud-ops-agent-repo.sh
bash add-google-cloud-ops-agent-repo.sh --also-install --version=latest
systemctl enable google-cloud-ops-agent
echo "[OK] Ops Agent instalado."

# ---------------------------------------------------------------------------
# 11. Permissões corretas para o usuário de serviço
# ---------------------------------------------------------------------------
echo "[INFO] Ajustando permissões para usuário nexus..."
chown -R nexus:nexus "$NEXUS_DIR"
chmod -R 750 "$NEXUS_DIR"

# ---------------------------------------------------------------------------
# 12. Marcar como provisionado
# ---------------------------------------------------------------------------
touch /opt/.provisioned
echo ""
echo "=============================="
echo "  Provisionamento concluído!"
echo "  $(date)"
echo "=============================="
echo "  Próximo passo: bash scripts/03-deploy.sh"
