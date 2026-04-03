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

from executor import SquadExecutor

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

# Tasks em execução: {squad_name: asyncio.Task}
running_squads: dict[str, asyncio.Task] = {}


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
ALLOWED_USER_ID = None   # populado no startup
GEMINI_API_KEY: str = "" # carregado no startup via Secret Manager
SERPER_API_KEY: str = "" # carregado no startup via Secret Manager

OPENCODE_MODEL = os.getenv("OPENCODE_MODEL", "gemini-2.0-flash")


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

    squad_yaml_path = SQUADS_DIR / f"{squad_name}.yaml"
    chat_id = str(update.effective_chat.id)

    async def progress_cb(msg: str) -> None:
        """Envia atualização de progresso para o Telegram."""
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=msg,
                parse_mode=ParseMode.MARKDOWN_V2,
            )
        except Exception as e:
            logger.warning("Falha ao enviar progresso: %s", e)
            # Tenta sem markdown
            try:
                await context.bot.send_message(chat_id=chat_id, text=msg)
            except Exception:
                pass

    async def _run_squad() -> None:
        try:
            executor = SquadExecutor(
                squad_yaml=squad_yaml_path,
                nexus_dir=NEXUS_DIR,
                gemini_api_key=GEMINI_API_KEY,
                serper_api_key=SERPER_API_KEY,
                telegram_token=context.bot.token,
                telegram_chat_id=chat_id,
                default_model=OPENCODE_MODEL,
            )
            await executor.run(progress_cb=progress_cb)
        except asyncio.CancelledError:
            pass
        except Exception as exc:
            logger.exception("Erro fatal no squad %s", squad_name)
            await context.bot.send_message(
                chat_id=chat_id,
                text=f"❌ Erro fatal no squad `{squad_name}`: {exc}",
            )
        finally:
            running_squads.pop(squad_name, None)

    task = asyncio.create_task(_run_squad())
    running_squads[squad_name] = task


async def cmd_status(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    active = {k: t for k, t in running_squads.items() if not t.done()}
    if not active:
        await update.message.reply_text("Nenhum squad em execução no momento\\.", parse_mode=ParseMode.MARKDOWN_V2)
        return
    lines = ["*Squads em execução:*\n"]
    for name in active:
        lines.append(f"🔄 `{name}`")
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
    task = running_squads.get(squad_name)
    if not task or task.done():
        await update.message.reply_text(
            f"Squad `{squad_name}` não está em execução\\.",
            parse_mode=ParseMode.MARKDOWN_V2,
        )
        return
    task.cancel()
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
    global ALLOWED_USER_ID, GEMINI_API_KEY, SERPER_API_KEY

    token = get_bot_token()
    ALLOWED_USER_ID = get_allowed_user_id()

    # Carrega Gemini API key
    try:
        GEMINI_API_KEY = load_secret("gemini-api-key")
        logger.info("Gemini API key carregada do Secret Manager.")
    except Exception as e:
        GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
        logger.warning("Gemini key não carregada do SM, usando env var: %s", e)

    # Carrega Serper API key (busca no Google)
    try:
        SERPER_API_KEY = load_secret("serper-api-key")
        logger.info("Serper API key carregada do Secret Manager.")
    except Exception as e:
        SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
        logger.warning("Serper key não carregada do SM (busca usará dados simulados): %s", e)

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
