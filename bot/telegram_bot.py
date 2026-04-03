"""
OpenClaw — Telegram Bot para controle do OpenSquad 24/7 no GCP
Intervalo 2 — Agenda Lucrativa / Nexus Workspace

Comandos disponíveis:
  /start            — boas-vindas e ajuda
  /squads           — lista squads disponíveis
  /run <nome>       — executa um squad
  /status           — squads em execução agora
  /logs <nome>      — últimas 50 linhas do log do squad
  /approve          — aprova checkpoint pendente
  /reject           — rejeita checkpoint pendente
  /stop <nome>      — interrompe execução de um squad

  /skill <nome> [key=value ...]  — executa uma skill diretamente
  /ask <mensagem>               — roteamento automático via orquestrador
  /design <briefing>            — atalho: frontend_design (instagram_post)
  /research <tópico>            — atalho: autoresearch
  /providers                    — lista providers e status
  /skills                       — lista skills disponíveis
  /usage                        — chamadas FAST vs POWERFUL da sessão
"""

import asyncio
import json
import logging
import os
import re
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
from orchestrator import Orchestrator
from providers import ModelProvider, get_default_provider, load_providers
from skill_loader import SkillError, SkillLoader

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

# Paths para providers e skills (relativos ao repo, independente do cwd)
_REPO_ROOT = Path(__file__).parent.parent
PROVIDERS_CONFIG = _REPO_ROOT / "config" / "providers.json"
SKILLS_DIR = _REPO_ROOT / "skills"

# Tasks em execução: {squad_name: asyncio.Task}
running_squads: dict[str, asyncio.Task] = {}

# Providers, orquestrador e skills (populados em main())
DEFAULT_PROVIDER: Optional[ModelProvider] = None
SKILL_LOADER: Optional[SkillLoader] = None
ORCHESTRATOR: Optional[Orchestrator] = None
CLAUDE_API_KEY: str = ""
OPENCODE_MODEL = os.getenv("OPENCODE_MODEL", "gemini-2.5-flash")
DEFAULT_PROVIDER_NAME = os.getenv("DEFAULT_PROVIDER", "gemini")

# Rastreamento de uso FAST vs POWERFUL
USAGE: dict[str, int] = {"fast": 0, "powerful": 0}

# Globals carregados em startup
ALLOWED_USER_ID = None
SERPER_API_KEY: str = ""


# ---------------------------------------------------------------------------
# TrackingProvider — rastreia chamadas por tier
# ---------------------------------------------------------------------------
class _TrackingProvider:
    """Wrapper transparente que incrementa USAGE[tier] a cada generate()."""

    def __init__(self, provider: ModelProvider):
        self._p = provider

    def generate(self, prompt: str, tier: str = "powerful") -> str:
        key = tier if tier in USAGE else "powerful"
        USAGE[key] += 1
        return self._p.generate(prompt, tier=tier)

    @property
    def name(self) -> str:
        return self._p.name

    def is_available(self) -> bool:
        return self._p.is_available()


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
    uid = os.getenv("TELEGRAM_ALLOWED_USER_ID")
    if uid:
        return int(uid)
    try:
        value = load_secret("telegram-allowed-user-id")
        return int(value) if value else None
    except Exception:
        return None


# ---------------------------------------------------------------------------
# Helpers gerais
# ---------------------------------------------------------------------------
def is_authorized(update: Update) -> bool:
    if ALLOWED_USER_ID is None:
        return True
    return update.effective_user.id == ALLOWED_USER_ID


def list_squads() -> list[str]:
    if not SQUADS_DIR.exists():
        return []
    return sorted(p.stem for p in SQUADS_DIR.glob("*.yaml"))


def squad_log_path(squad_name: str) -> Path:
    """Retorna o log mais recente do squad (por run_id timestamp)."""
    squad_logs = LOGS_DIR / squad_name
    squad_logs.mkdir(parents=True, exist_ok=True)
    logs = sorted(squad_logs.glob("*.log"), reverse=True)
    return logs[0] if logs else squad_logs / "empty.log"


def tail_log(squad_name: str, lines: int = 50) -> str:
    log_path = squad_log_path(squad_name)
    if not log_path.exists():
        return f"Sem logs para `{squad_name}` ainda."
    with open(log_path, "r") as f:
        all_lines = f.readlines()
    tail = all_lines[-lines:]
    header = f"[run: {log_path.stem}]\n"
    return header + ("".join(tail) if tail else "Log vazio.")


def list_pending_checkpoints() -> list[dict]:
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
    with open(cp_file, "r+") as f:
        data = json.load(f)
        data["status"] = decision
        data["resolved_at"] = time.time()
        f.seek(0)
        json.dump(data, f, indent=2)
        f.truncate()


async def _safe_send(bot, chat_id: str, text: str, title: str = "") -> None:
    """Envia texto longo de forma segura, truncando e tentando markdown."""
    header = f"*{title}*\n\n" if title else ""
    max_len = 3900 - len(header)

    parts = []
    remaining = text
    while remaining:
        chunk = remaining[:max_len]
        remaining = remaining[max_len:]
        if remaining:
            chunk += "\n\n`[... continua]`"
        parts.append(header + chunk if not parts else chunk)

    for part in parts:
        try:
            await bot.send_message(chat_id=chat_id, text=part, parse_mode=ParseMode.MARKDOWN)
        except Exception:
            try:
                await bot.send_message(chat_id=chat_id, text=part.replace("*", "").replace("`", ""))
            except Exception as e:
                logger.error("Falha ao enviar mensagem: %s", e)


def _parse_skill_args(args: list[str]) -> tuple[str, dict]:
    """
    Parseia argumentos do /skill: nome key=value key="multi word" ...
    Ex: brainstorm topic="café artesanal" quantity=5
    """
    if not args:
        return "", {}

    skill_name = SkillLoader.normalize_name(args[0])
    params: dict = {}

    rest = " ".join(args[1:]).strip()

    # Extrai key="valor com espaços" e key=valor_simples
    pattern = re.compile(r'(\w+)=(?:"([^"]*)"|([\S]*))')
    for m in pattern.finditer(rest):
        key = m.group(1)
        value = m.group(2) if m.group(2) is not None else m.group(3)
        params[key] = value

    # Se não houve key=value, trata texto restante como parâmetro primário
    if not params and rest:
        params["topic"] = rest

    return skill_name, params


def _inject_base_context(params: dict) -> dict:
    """Injeta nexus_dir, project_id e serper_key no contexto da skill."""
    return {
        "nexus_dir": str(NEXUS_DIR),
        "project_id": PROJECT_ID,
        "serper_key": SERPER_API_KEY,
        **params,
    }


# ---------------------------------------------------------------------------
# Handlers existentes
# ---------------------------------------------------------------------------
async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    provider_name = DEFAULT_PROVIDER.name if DEFAULT_PROVIDER else "nenhum"
    text = (
        "🤖 *OpenClaw — Nexus Bot*\n\n"
        f"Provider ativo: `{provider_name}`\n\n"
        "*Squads:*\n"
        "`/squads` — lista squads disponíveis\n"
        "`/run <nome>` — executa um squad\n"
        "`/status` — squads em execução\n"
        "`/logs <nome>` — logs do squad\n"
        "`/stop <nome>` — interrompe execução\n\n"
        "*Skills:*\n"
        "`/skill <nome> [key=value ...]` — executa skill\n"
        "`/ask <mensagem>` — roteamento automático\n"
        "`/design <briefing>` — gerar imagem Instagram\n"
        "`/research <tópico>` — pesquisa automatizada\n"
        "`/skills` — lista skills disponíveis\n\n"
        "*Sistema:*\n"
        "`/providers` — providers e status\n"
        "`/usage` — chamadas FAST vs POWERFUL\n"
        "`/approve` / `/reject` — checkpoints\n"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_squads(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    squads = list_squads()
    if not squads:
        await update.message.reply_text(
            "Nenhum squad encontrado em `squads/`.\nCrie squads adicionando arquivos `.yaml` em `squads/`.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return
    lines = ["*Squads disponíveis:*\n"]
    for s in squads:
        marker = "🔄 " if s in running_squads else "▶️ "
        lines.append(f"{marker}`{s}`")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_run(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    if not context.args:
        await update.message.reply_text("Uso: `/run <nome-do-squad>`", parse_mode=ParseMode.MARKDOWN)
        return

    squad_name = context.args[0].strip()

    if squad_name in running_squads:
        await update.message.reply_text(
            f"⚠️ Squad `{squad_name}` já está em execução.",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    squad_file = SQUADS_DIR / f"{squad_name}.yaml"
    if not squad_file.exists():
        squads = list_squads()
        available = ", ".join(f"`{s}`" for s in squads) if squads else "_nenhum_"
        await update.message.reply_text(
            f"❌ Squad `{squad_name}` não encontrado.\n\nDisponíveis: {available}",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    await update.message.reply_text(
        f"🚀 Iniciando squad `{squad_name}`...",
        parse_mode=ParseMode.MARKDOWN,
    )

    chat_id = str(update.effective_chat.id)

    async def progress_cb(msg: str) -> None:
        try:
            await context.bot.send_message(
                chat_id=chat_id,
                text=msg,
                parse_mode=ParseMode.MARKDOWN,
            )
        except Exception:
            try:
                await context.bot.send_message(chat_id=chat_id, text=msg)
            except Exception:
                pass

    async def _run_squad() -> None:
        try:
            executor = SquadExecutor(
                squad_yaml=squad_file,
                nexus_dir=NEXUS_DIR,
                project_id=PROJECT_ID,
                location="us-central1",
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
        await update.message.reply_text("Nenhum squad em execução no momento.", parse_mode=ParseMode.MARKDOWN)
        return
    lines = ["*Squads em execução:*\n"]
    for name in active:
        lines.append(f"🔄 `{name}`")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_logs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    if not context.args:
        await update.message.reply_text("Uso: `/logs <nome-do-squad>`", parse_mode=ParseMode.MARKDOWN)
        return
    squad_name = context.args[0].strip()
    log_text = tail_log(squad_name)
    if len(log_text) > 3800:
        log_text = "[... truncado ...]\n" + log_text[-3800:]
    await update.message.reply_text(
        f"*Logs de `{squad_name}`:*\n```\n{log_text}\n```",
        parse_mode=ParseMode.MARKDOWN,
    )


async def cmd_approve(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    checkpoints = list_pending_checkpoints()
    if not checkpoints:
        await update.message.reply_text("Nenhum checkpoint pendente no momento.", parse_mode=ParseMode.MARKDOWN)
        return
    await _show_checkpoint(update, context, checkpoints[0], "approve")


async def cmd_reject(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    checkpoints = list_pending_checkpoints()
    if not checkpoints:
        await update.message.reply_text("Nenhum checkpoint pendente no momento.", parse_mode=ParseMode.MARKDOWN)
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
    keyboard = [[
        InlineKeyboardButton("✅ Aprovar", callback_data=f"cp:approve:{cp_file}"),
        InlineKeyboardButton("❌ Rejeitar", callback_data=f"cp:reject:{cp_file}"),
    ]]
    await update.message.reply_text(
        f"*Checkpoint — Squad `{squad}`*\n\n{message}",
        reply_markup=InlineKeyboardMarkup(keyboard),
        parse_mode=ParseMode.MARKDOWN,
    )


async def callback_checkpoint(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    query = update.callback_query
    await query.answer()
    parts = query.data.split(":", 2)
    if len(parts) != 3 or parts[0] != "cp":
        return
    _, decision, cp_file = parts
    try:
        resolve_checkpoint(cp_file, decision)
        emoji = "✅" if decision == "approve" else "❌"
        label = "aprovado" if decision == "approve" else "rejeitado"
        await query.edit_message_text(f"{emoji} Checkpoint *{label}*.", parse_mode=ParseMode.MARKDOWN)
    except Exception as e:
        await query.edit_message_text(f"Erro ao processar checkpoint: {e}")


async def cmd_stop(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not is_authorized(update):
        return
    if not context.args:
        await update.message.reply_text("Uso: `/stop <nome-do-squad>`", parse_mode=ParseMode.MARKDOWN)
        return
    squad_name = context.args[0].strip()
    task = running_squads.get(squad_name)
    if not task or task.done():
        await update.message.reply_text(
            f"Squad `{squad_name}` não está em execução.", parse_mode=ParseMode.MARKDOWN
        )
        return
    task.cancel()
    running_squads.pop(squad_name, None)
    await update.message.reply_text(f"⛔ Squad `{squad_name}` interrompido.", parse_mode=ParseMode.MARKDOWN)


# ---------------------------------------------------------------------------
# Novos handlers — Skills
# ---------------------------------------------------------------------------
async def cmd_skill(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Executa uma skill diretamente: /skill <nome> [key=value ...]"""
    if not is_authorized(update):
        return
    if not SKILL_LOADER or not DEFAULT_PROVIDER:
        await update.message.reply_text("⚠️ Skill system não inicializado.", parse_mode=ParseMode.MARKDOWN)
        return
    if not context.args:
        skills = SKILL_LOADER.list_skills()
        names = "\n".join(f"• `{s['name']}` — {s['description']}" for s in skills)
        await update.message.reply_text(
            f"Uso: `/skill <nome> [key=value ...]`\n\n*Skills disponíveis:*\n{names}",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    skill_name, params = _parse_skill_args(context.args)
    params = _inject_base_context(params)
    chat_id = str(update.effective_chat.id)

    await update.message.reply_text(f"⚙️ Executando `{skill_name}`...", parse_mode=ParseMode.MARKDOWN)

    try:
        result = await asyncio.to_thread(SKILL_LOADER.execute, skill_name, params, DEFAULT_PROVIDER)
        await _safe_send(context.bot, chat_id, result, title=f"Skill: {skill_name}")
    except SkillError as e:
        await context.bot.send_message(chat_id=chat_id, text=f"❌ {e}")
    except Exception as e:
        logger.exception("Erro inesperado na skill %s", skill_name)
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Erro inesperado: {e}")


async def cmd_ask(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/ask <mensagem> — roteamento automático via orquestrador."""
    if not is_authorized(update):
        return
    if not ORCHESTRATOR or not SKILL_LOADER or not DEFAULT_PROVIDER:
        await update.message.reply_text("⚠️ Orquestrador não inicializado.", parse_mode=ParseMode.MARKDOWN)
        return
    if not context.args:
        await update.message.reply_text("Uso: `/ask <sua mensagem>`", parse_mode=ParseMode.MARKDOWN)
        return

    message = " ".join(context.args)
    chat_id = str(update.effective_chat.id)

    thinking_msg = await update.message.reply_text("🤔 Analisando...", parse_mode=ParseMode.MARKDOWN)

    try:
        route = await asyncio.to_thread(ORCHESTRATOR.route, message)
        skill_name = route["skill"]
        params = _inject_base_context(route.get("params", {}))

        await context.bot.edit_message_text(
            chat_id=chat_id,
            message_id=thinking_msg.message_id,
            text=f"⚙️ Roteado para `{skill_name}`...",
            parse_mode=ParseMode.MARKDOWN,
        )

        result = await asyncio.to_thread(SKILL_LOADER.execute, skill_name, params, DEFAULT_PROVIDER)

        # Apaga mensagem de progresso
        try:
            await context.bot.delete_message(chat_id=chat_id, message_id=thinking_msg.message_id)
        except Exception:
            pass

        await _safe_send(context.bot, chat_id, result, title=f"/{skill_name.replace('_', '-')}")

    except SkillError as e:
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=thinking_msg.message_id, text=f"❌ {e}"
        )
    except Exception as e:
        logger.exception("Erro no /ask")
        await context.bot.edit_message_text(
            chat_id=chat_id, message_id=thinking_msg.message_id, text=f"❌ Erro: {e}"
        )


async def cmd_design(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/design <briefing> — atalho para frontend_design (instagram_post)."""
    if not is_authorized(update):
        return
    if not SKILL_LOADER or not DEFAULT_PROVIDER:
        await update.message.reply_text("⚠️ Skill system não inicializado.")
        return
    if not context.args:
        await update.message.reply_text(
            "Uso: `/design <briefing>`\n\nEx: `/design post açaí tropical marca laranja`",
            parse_mode=ParseMode.MARKDOWN,
        )
        return

    briefing = " ".join(context.args)
    chat_id = str(update.effective_chat.id)

    params = _inject_base_context({
        "format": "instagram_post",
        "theme": "profissional e moderno",
        "content": briefing,
    })

    await update.message.reply_text("🎨 Gerando design...", parse_mode=ParseMode.MARKDOWN)

    try:
        result = await asyncio.to_thread(SKILL_LOADER.execute, "frontend_design", params, DEFAULT_PROVIDER)

        # Se resultado é caminho de arquivo PNG, envia como foto
        result_path = Path(result.strip())
        if result_path.exists() and result_path.suffix == ".png":
            with open(result_path, "rb") as f:
                await context.bot.send_document(chat_id=chat_id, document=f, filename=result_path.name)
        else:
            await _safe_send(context.bot, chat_id, result, title="Design")
    except Exception as e:
        logger.exception("Erro no /design")
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Erro no design: {e}")


async def cmd_research(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/research <tópico> — atalho para autoresearch."""
    if not is_authorized(update):
        return
    if not SKILL_LOADER or not DEFAULT_PROVIDER:
        await update.message.reply_text("⚠️ Skill system não inicializado.")
        return
    if not context.args:
        await update.message.reply_text("Uso: `/research <tópico>`", parse_mode=ParseMode.MARKDOWN)
        return

    topic = " ".join(context.args)
    chat_id = str(update.effective_chat.id)

    params = _inject_base_context({"topic": topic, "depth": "quick"})

    await update.message.reply_text(f"🔍 Pesquisando *{topic}*...", parse_mode=ParseMode.MARKDOWN)

    try:
        result = await asyncio.to_thread(SKILL_LOADER.execute, "autoresearch", params, DEFAULT_PROVIDER)
        await _safe_send(context.bot, chat_id, result, title=f"Pesquisa: {topic}")
    except Exception as e:
        logger.exception("Erro no /research")
        await context.bot.send_message(chat_id=chat_id, text=f"❌ Erro na pesquisa: {e}")


async def cmd_providers(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/providers — lista providers e status."""
    if not is_authorized(update):
        return

    config_path = PROVIDERS_CONFIG
    providers_info = []

    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            cfg = json.load(f)
        default_name = cfg.get("default", "gemini")
        for name, pcfg in cfg.get("providers", {}).items():
            enabled = "✅" if pcfg.get("enabled") else "❌"
            is_default = " *(padrão)*" if name == default_name else ""
            models = pcfg.get("models", {})
            fast = models.get("fast", "?")
            powerful = models.get("powerful", "?")
            providers_info.append(
                f"{enabled} `{name}`{is_default}\n"
                f"   FAST: `{fast}`\n"
                f"   POWERFUL: `{powerful}`"
            )
    else:
        providers_info.append("config/providers.json não encontrado.")

    active = DEFAULT_PROVIDER.name if DEFAULT_PROVIDER else "nenhum"
    text = f"*Providers configurados:*\nAtivo: `{active}`\n\n" + "\n\n".join(providers_info)
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


async def cmd_skills_list(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/skills — lista skills disponíveis."""
    if not is_authorized(update):
        return
    if not SKILL_LOADER:
        await update.message.reply_text("⚠️ Skill system não inicializado.")
        return

    skills = SKILL_LOADER.list_skills()
    if not skills:
        await update.message.reply_text("Nenhuma skill encontrada em `skills/`.", parse_mode=ParseMode.MARKDOWN)
        return

    lines = ["*Skills disponíveis:*\n"]
    for s in skills:
        tier_icon = "⚡" if s.get("tier") == "fast" else "🧠"
        desc = s.get("description", "")
        lines.append(f"{tier_icon} `{s['name']}` — {desc}")

    lines.append("\n⚡ FAST  🧠 POWERFUL")
    await update.message.reply_text("\n".join(lines), parse_mode=ParseMode.MARKDOWN)


async def cmd_usage(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/usage — distribuição de chamadas FAST vs POWERFUL."""
    if not is_authorized(update):
        return
    total = USAGE["fast"] + USAGE["powerful"]
    fast_pct = int(USAGE["fast"] / total * 100) if total else 0
    powerful_pct = 100 - fast_pct if total else 0
    provider_name = DEFAULT_PROVIDER.name if DEFAULT_PROVIDER else "nenhum"
    text = (
        f"*Uso da sessão — Provider: `{provider_name}`*\n\n"
        f"⚡ FAST: `{USAGE['fast']}` chamadas ({fast_pct}%)\n"
        f"🧠 POWERFUL: `{USAGE['powerful']}` chamadas ({powerful_pct}%)\n"
        f"📊 Total: `{total}` chamadas"
    )
    await update.message.reply_text(text, parse_mode=ParseMode.MARKDOWN)


# ---------------------------------------------------------------------------
# Polling de checkpoints — notifica proativamente
# ---------------------------------------------------------------------------
async def checkpoint_polling(context: ContextTypes.DEFAULT_TYPE) -> None:
    if ALLOWED_USER_ID is None:
        return
    checkpoints = list_pending_checkpoints()
    for cp in checkpoints:
        notified_flag = Path(cp["_file"] + ".notified")
        if notified_flag.exists():
            continue
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
            parse_mode=ParseMode.MARKDOWN,
        )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------
def main() -> None:
    global ALLOWED_USER_ID, SERPER_API_KEY, CLAUDE_API_KEY
    global DEFAULT_PROVIDER, SKILL_LOADER, ORCHESTRATOR

    token = get_bot_token()
    ALLOWED_USER_ID = get_allowed_user_id()

    # Serper API key
    try:
        SERPER_API_KEY = load_secret("serper-api-key")
        logger.info("Serper API key carregada.")
    except Exception as e:
        SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")
        logger.warning("Serper key não carregada do SM: %s", e)

    # Claude API key (opcional)
    try:
        CLAUDE_API_KEY = load_secret("claude-api-key")
        logger.info("Claude API key carregada.")
    except Exception as e:
        CLAUDE_API_KEY = os.getenv("CLAUDE_API_KEY", "")
        if CLAUDE_API_KEY:
            logger.info("Claude API key carregada via env var.")
        else:
            logger.info("Claude API key não configurada (provider desativado): %s", e)

    # Carrega providers
    try:
        providers = load_providers(
            config_path=PROVIDERS_CONFIG,
            project_id=PROJECT_ID,
            claude_api_key=CLAUDE_API_KEY,
        )
        raw_provider = get_default_provider(providers, PROVIDERS_CONFIG)
        if raw_provider:
            DEFAULT_PROVIDER = _TrackingProvider(raw_provider)
            logger.info("Provider padrão: %s", raw_provider.name)
        else:
            logger.error("Nenhum provider disponível — skills não funcionarão")
    except Exception as e:
        logger.error("Erro ao carregar providers: %s", e)

    # Inicializa SkillLoader e Orchestrator
    try:
        SKILL_LOADER = SkillLoader(skills_dir=SKILLS_DIR)
        logger.info("SkillLoader inicializado: %d skills", len(SKILL_LOADER.list_skills()))
    except Exception as e:
        logger.error("Erro ao inicializar SkillLoader: %s", e)

    if DEFAULT_PROVIDER:
        ORCHESTRATOR = Orchestrator(provider=DEFAULT_PROVIDER)
        logger.info("Orchestrator inicializado.")

    if ALLOWED_USER_ID:
        logger.info("Bot restrito ao usuário ID: %s", ALLOWED_USER_ID)
    else:
        logger.warning("TELEGRAM_ALLOWED_USER_ID não configurado — bot aceita qualquer usuário")

    app = Application.builder().token(token).build()

    # Handlers existentes
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("squads", cmd_squads))
    app.add_handler(CommandHandler("run", cmd_run))
    app.add_handler(CommandHandler("status", cmd_status))
    app.add_handler(CommandHandler("logs", cmd_logs))
    app.add_handler(CommandHandler("approve", cmd_approve))
    app.add_handler(CommandHandler("reject", cmd_reject))
    app.add_handler(CommandHandler("stop", cmd_stop))

    # Novos handlers — skills e orquestrador
    app.add_handler(CommandHandler("skill", cmd_skill))
    app.add_handler(CommandHandler("ask", cmd_ask))
    app.add_handler(CommandHandler("design", cmd_design))
    app.add_handler(CommandHandler("research", cmd_research))
    app.add_handler(CommandHandler("providers", cmd_providers))
    app.add_handler(CommandHandler("skills", cmd_skills_list))
    app.add_handler(CommandHandler("usage", cmd_usage))

    # Callbacks de botões inline
    app.add_handler(CallbackQueryHandler(callback_checkpoint, pattern=r"^cp:"))

    # Job periódico: polling de checkpoints a cada 10s
    app.job_queue.run_repeating(checkpoint_polling, interval=10, first=5)

    logger.info("OpenClaw iniciando polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)


if __name__ == "__main__":
    main()
