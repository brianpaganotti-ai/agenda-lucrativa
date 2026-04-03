"""
OpenClaw — Telegram Bot para controle do OpenSquad 24/7 no GCP
Intervalo 2 — Agenda Lucrativa / Nexus Workspace

Comandos disponíveis:
  /start          — boas-vindas e ajuda
  /squads         — lista squads disponíveis
  /run <nome>     — executa um squad
  /status         — squads em execução agora
  /logs <nome>    — últimas 50 linhas do log do squad
  /approve        — aprova checkpoint pendente
  /reject         — rejeita checkpoint pendente
  /stop <nome>    — interrompe execução de um squad
"""

import asyncio
import glob
import json
import logging
import os
import signal
import subprocess
import time
from pathlib import Path
from typing import Optional

from google.cloud import secretmanager
from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    Application,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------
logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)

NEXUS_DIR = Path(os.getenv("NEXUS_DIR", "/opt/nexus"))
SQUADS_DIR = NEXUS_DIR / "squads"
OPENSQUAD_DIR = NEXUS_DIR / "_opensquad"
CHECKPOINTS_DIR = OPENSQUAD_DIR / "checkpoints"
LOGS_DIR = NEXUS_DIR / "logs"
PROJECT_ID = os.getenv("PROJECT_ID", "project-87c1c65b-10d3-40d5-999")

# Processos em execução: {squad_name: subprocess.Popen}
running_squads: dict[str, subprocess.Popen] = {}


# ---------------------------------------------------------------------------
# Secret Manager
# ---------------------------------------------------------------------------
def load_secret(secret_name: str) -> str:
    """Lê um secret do GCP Secret Manager."""
    client = secretmanager.SecretManagerServiceClient()
    name = f"projects/{PROJECT_ID}/secrets/{secret_name}/versions/latest"
    response = client.access_secret_version(request={"name": name})
    return response.payload.data.decode("utf-8").strip()


def get_bot_token() -> str:
    token = os.getenv("TELEGRAM_BOT_TOKEN")
    if token:
        return token
    try:
        return load_secret("telegram-bot-token")
    except Exception as e:
        logger.error("Não foi possível carregar telegram-bot-token: %s", e)
        raise


def get_allowed_user_id() -> Optional[int]:
    """ID do usuário Telegram autorizado (opcional, segurança extra)."""
    uid = os.getenv("TELEGRAM_ALLOWED_USER_ID")
    if uid:
        return int(uid)
    try:
        value = load_secret("telegram-allowed-user-id")
        return int(value) if value else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
ALLOWED_USER_ID = None  # populado no startup
GEMINI_API_KEY: str = ""  # carregado no startup via Secret Manager

# Modelo via Vertex AI (usa service account da VM — sem custo de API key separada)
OPENCODE_MODEL = os.getenv("OPENCODE_MODEL", "google/gemini-2.5-flash-preview-04-17")


def is_authorized(update: Update) -> bool:
    if ALLOWED_USER_ID is None:
        return True
    return update.effective_user.id == ALLOWED_USER_ID


def list_squads() -> list[str]:
    """Lista squads disponíveis (arquivos .yaml em squads/)."""
    if not SQUADS_DIR.exists():
        return []
    return sorted(
        p.stem for p in SQUADS_DIR.glob("*.yaml")
    )


def squad_log_path(squad_name: str) -> Path:
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    return LOGS_DIR / f"{squad_name}.log"


def tail_log(squad_name: str, lines: int = 50) -> str:
    log_path = squad_log_path(squad_name)
    if not log_path.exists():
        return f"Sem logs para `{squad_name}` ainda."
    with open(log_path, "r") as f:
        all_lines = f.readlines()
    tail = all_lines[-lines:]
    return "".join(tail) if tail else "Log vazio."


def list_pending_checkpoints() -> list[dict]:
    """Retorna lista de checkpoints pendentes de aprovação."""
    CHECKPOINTS_DIR.mkdir(parents=True, exist_ok=True)
    checkpoints = []
    for cp_file in sorted(CHECKPOINTS_DIR.glob("*.json")):
        try:
            with open(cp_file) as f:
                data = json.load(f)
            if data.get("status") == "pending":
                data["_file"] = str(cp_file)
                checkpoints.append(data)
        except Exception:
            pass
    return checkpoints


def resolve_checkpoint(cp_file: str, decision: str) -> None:
    """Escreve a decisão no arquivo de checkpoint para retomar o pipeline."""
    with open(cp_file, "r+") as f:
        data = json.load(f)
        data["status"] = decision  # "approved" ou "rejected"
        data["resolved_at"] = time.time()
        f.seek(0)
        json.dump(data, f, indent=2)
        f.truncate()


# ---------------------------------------------------------------------------
# Execução de squads
# ---------------------------------------------------------------------------
async def stream_squad_output(
    squad_name: str,
    proc: subprocess.Popen,
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
) -> None:
    """Lê stdout do processo e envia para o Telegram em blocos."""
    buffer = []
    last_send = time.time()
    log_file = open(squad_log_path(squad_name), "a")

    try:
        for line in proc.stdout:
            log_file.write(line)
            log_file.flush()
            buffer.append(line.rstrip())

            # Envia bloco a cada 2s ou 20 linhas
            if time.time() - last_send > 2 or len(buffer) >= 20:
                chunk = "\n".join(buffer)
                if chunk.strip():
                    await context.bot.send_message(
                        chat_id=update.effective_chat.id,
                        text=f"```\n{chunk[-3800:]}\n```",
                        parse_mode=ParseMode.MARKDOWN_V2,
                    )
                buffer.clear()
                last_send = time.time()

        proc.wait()
        if buffer:
            chunk = "\n".join(buffer)
            if chunk.strip():
                await context.bot.send_message(
                    chat_id=update.effective_chat.id,
                    text=f"```\n{chunk[-3800:]}\n```",
                    parse_mode=ParseMode.MARKDOWN_V2,
                )

        status = "✅ Concluído" if proc.returncode == 0 else f"❌ Erro (código {proc.returncode})"
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text=f"*Squad `{squad_name}` — {status}*",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    finally:
        log_file.close()
        running_squads.pop(squad_name, None)


# ---------------------------------------------------------------------------
# Handlers de comandos
# ---------------------------------------------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    text = (
        "🤖 *OpenClaw — Nexus Bot*\n\n"
        "Comandos disponíveis:\n"
        "`/squads` — lista squads disponíveis\n"
        "`/run <nome>` — executa um squad\n"
        "`/status` — squads em execução\n"
        "`/logs <nome>` — logs do squad\n"
        "`/approve` — aprova checkpoint pendente\n"
        "`/reject` — rejeita checkpoint pendente\n"
        "`/stop <nome>` — interrompe execução\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN_V2)


async def cmd_squads(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    squads = list_squads()
    if not squads:
        await update.message.reply_text(
            "Nenhum squad encontrado em `squads/`\\.\n"
            "Crie squads via `/opensquad create` ou adicione arquivos `.yaml` em `squads/`\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    lines = ["*Squads disponíveis:*\n"]
    for s in squads:
        marker = "🔄 " if s in running_squads else "▶️ "
        lines.append(f"{marker}`{s}`")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)


async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    if not context.args:
        await update.message.reply_text("Uso: `/run <nome-do-squad>`", parse_mode=ParseMode.MARKDOWN_V2)
        return

    squad_name = context.args[0].strip()

    if squad_name in running_squads:
        await update.message.reply_text(
            f"⚠️ Squad `{squad_name}` já está em execução\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    squad_file = SQUADS_DIR / f"{squad_name}.yaml"
    if not squad_file.exists():
        squads = list_squads()
        available = ", ".join(f"`{s}`" for s in squads) if squads else "_nenhum_"
        await update.message.reply_text(
            f"❌ Squad `{squad_name}` não encontrado\\.\n\nDisponíveis: {available}",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return

    await update.message.reply_text(
        f"🚀 Iniciando squad `{squad_name}`\\.\\.\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )

    try:
        # Monta env para o OpenCode com Vertex AI
        # A VM usa Application Default Credentials via service account — sem API key
        proc_env = os.environ.copy()
        proc_env["GOOGLE_CLOUD_PROJECT"] = PROJECT_ID
        proc_env["GOOGLE_CLOUD_REGION"] = "us-central1"
        proc_env["GOOGLE_VERTEX_PROJECT"] = PROJECT_ID
        proc_env["GOOGLE_VERTEX_LOCATION"] = "us-central1"
        # Fallback: se tiver API key do AI Studio também injeta
        if GEMINI_API_KEY:
            proc_env["GOOGLE_GENERATIVE_AI_API_KEY"] = GEMINI_API_KEY
            proc_env["GEMINI_API_KEY"] = GEMINI_API_KEY

        # opencode run [message..] — mensagem como palavras separadas
        # -m google/gemini-2.0-flash — modelo explícito
        prompt_words = f"/opensquad run {squad_name}".split()
        cmd = ["opencode", "run", "-m", OPENCODE_MODEL] + prompt_words

        proc = subprocess.Popen(
            cmd,
            cwd=str(NEXUS_DIR),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
            env=proc_env,
        )
        running_squads[squad_name] = proc

        # Executa streaming em background para não bloquear o bot
        asyncio.create_task(
            stream_squad_output(squad_name, proc, update, context)
        )
    except FileNotFoundError:
        await update.message.reply_text(
            "❌ `opencode` não encontrado\\. Verifique a instalação na VM\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    if not running_squads:
        await update.message.reply_text("Nenhum squad em execução no momento\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    lines = ["*Squads em execução:*\n"]
    for name, proc in running_squads.items():
        pid = proc.pid
        lines.append(f"🔄 `{name}` \\(PID {pid}\\)")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN_V2)


async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    if not context.args:
        await update.message.reply_text("Uso: `/logs <nome-do-squad>`", parse_mode=ParseMode.MARKDOWN_V2)
        return
    squad_name = context.args[0].strip()
    log_text = tail_log(squad_name)
    # Trunca para limite do Telegram
    if len(log_text) > 3800:
        log_text = "\\[\\.\\.\\. truncado \\.\\.\\.\\.\\]\n" + log_text[-3800:]
    await update.message.reply_text(
        f"*Logs de `{squad_name}`:*\n```\n{log_text}\n```",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    checkpoints = list_pending_checkpoints()
    if not checkpoints:
        await update.message.reply_text("Nenhum checkpoint pendente no momento\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    await _show_checkpoint(update, context, checkpoints[0], "approve")


async def cmd_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    checkpoints = list_pending_checkpoints()
    if not checkpoints:
        await update.message.reply_text("Nenhum checkpoint pendente no momento\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    await _show_checkpoint(update, context, checkpoints[0], "reject")


async def _show_checkpoint(
    update: Update,
    context: ContextTypes.DEFAULT_TYPE,
    checkpoint: dict,
    action: str,
) -> None:
    message = checkpoint.get("message", "Checkpoint sem descrição")
    cp_file = checkpoint["_file"]
    squad = checkpoint.get("squad", "desconhecido")

    keyboard = [
        [
            InlineKeyboardButton("✅ Aprovar", callback_data=f"cp:approve:{cp_file}"),
            InlineKeyboardButton("❌ Rejeitar", callback_data=f"cp:reject:{cp_file}"),
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"*Checkpoint — Squad `{squad}`*\n\n{message}",
        reply_markup=reply_markup,
        parse_mode=ParseMode.MARKDOWN_V2,
    )


async def callback_checkpoint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handler de botões inline para checkpoints."""
    query = update.callback_query
    await query.answer()

    parts = query.data.split(":", 2)
    if len(parts) != 3 or parts[0] != "cp":
        return

    _, decision, cp_file = parts
    try:
        resolve_checkpoint(cp_file, decision)
        emoji = "✅" if decision == "approve" else "❌"
        await query.edit_message_text(
            f"{emoji} Checkpoint *{'aprovado' if decision == 'approve' else 'rejeitado'}*\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
    except Exception as e:
        await query.edit_message_text(f"Erro ao processar checkpoint: {e}")


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    if not context.args:
        await update.message.reply_text("Uso: `/stop <nome-do-squad>`", parse_mode=ParseMode.MARKDOWN_V2)
        return
    squad_name = context.args[0].strip()
    proc = running_squads.get(squad_name)
    if not proc:
        await update.message.reply_text(
            f"Squad `{squad_name}` não está em execução\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    proc.send_signal(signal.SIGTERM)
    running_squads.pop(squad_name, None)
    await update.message.reply_text(
        f"⛔ Squad `{squad_name}` interrompido\\.",
        parse_mode=ParseMode.MARKDOWN_V2,
    )


# ---------------------------------------------------------------------------
# Polling de checkpoints — notifica proativamente
# ---------------------------------------------------------------------------
async def checkpoint_polling(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Job periódico: verifica novos checkpoints e notifica o usuário."""
    if ALLOWED_USER_ID is None:
        return
    checkpoints = list_pending_checkpoints()
    for cp in checkpoints:
        notified_flag = Path(cp["_file"] + ".notified")
        if notified_flag.exists():
            continue
        # Marca como notificado antes de enviar
        notified_flag.touch()
        squad = cp.get("squad", "desconhecido")
        message = cp.get("message", "Checkpoint sem descrição")
        cp_file = cp["_file"]
        keyboard = [[
            InlineKeyboardButton("✅ Aprovar", callback_data=f"cp:approve:{cp_file}"),
            InlineKeyboardButton("❌ Rejeitar", callback_data=f"cp:reject:{cp_file}"),
        ]]
        await context.bot.send_message(
            chat_id=ALLOWED_USER_ID,
            text=f"⚠️ *Checkpoint aguardando aprovação*\nSquad: `{squad}`\n\n{message}",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode=ParseMode.MARKDOWN_V2,
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    global ALLOWED_USER_ID, GEMINI_API_KEY

    token = get_bot_token()
    ALLOWED_USER_ID = get_allowed_user_id()

    # Carrega Gemini API key para injetar no opencode
    try:
        GEMINI_API_KEY = load_secret("gemini-api-key")
        logger.info("Gemini API key carregada do Secret Manager.")
    except Exception as e:
        GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
        logger.warning("Gemini key não carregada do SM, usando env var: %s", e)

    if ALLOWED_USER_ID:
        logger.info("Bot restrito ao usuário ID: %s", ALLOWED_USER_ID)
    else:
        logger.warning("TELEGRAM_ALLOWED_USER_ID não configurado — bot aceita qualquer usuário")

    app = Application.builder().token(token).build()

    # Comandos
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("squads", cmd_squads))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("reject", cmd_reject))
    app.add_handler(CommandHandler("stop", cmd_stop))

    # Callbacks de botões inline
    app.add_handler(CallbackQueryHandler(callback_checkpoint, pattern=r"^cp:"))

    # Job periódico: polling de checkpoints a cada 10s
    app.job_queue.run_repeating(checkpoint_polling, interval=10, first=5)

    logger.info("OpenClaw iniciando polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
