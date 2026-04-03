"""
executor.py — Executa pipelines OpenSquad-style diretamente via Gemini API.

Substitui o OpenCode completamente. Cada step do squad YAML é executado
com Gemini function calling: o modelo pode ler/escrever arquivos, pesquisar
no Google (Serper), enviar WhatsApp e Telegram — tudo em Python puro, sem
dependência de ferramenta externa.
"""

import asyncio
import json
import logging
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

import yaml

logger = logging.getLogger(__name__)

try:
    import google.generativeai as genai
    from google.generativeai.types import FunctionDeclaration, Tool
    import google.ai.generativelanguage as glm
    GENAI_OK = True
except ImportError:
    GENAI_OK = False
    logger.error("Instale: pip install google-generativeai")


# ---------------------------------------------------------------------------
# Definição das ferramentas disponíveis para o Gemini
# ---------------------------------------------------------------------------

_TOOL_SCHEMA = [
    FunctionDeclaration(
        name="read_file",
        description=(
            "Lê o conteúdo de um arquivo no diretório do projeto. "
            "Use caminhos relativos: tmp/leads.json, squads/prospeccao.yaml, etc."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Caminho relativo ao diretório do projeto"}
            },
            "required": ["path"],
        },
    ),
    FunctionDeclaration(
        name="write_file",
        description=(
            "Escreve conteúdo em um arquivo no diretório do projeto. "
            "Use caminhos relativos: tmp/resultado.json, etc."
        ),
        parameters={
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "Caminho relativo ao diretório do projeto"},
                "content": {"type": "string", "description": "Conteúdo completo do arquivo"},
            },
            "required": ["path", "content"],
        },
    ),
    FunctionDeclaration(
        name="serper_search",
        description=(
            "Pesquisa no Google via Serper API. "
            "Retorna resultados orgânicos e dados de empresas locais."
        ),
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "Termo de busca"}
            },
            "required": ["query"],
        },
    ),
    FunctionDeclaration(
        name="send_telegram",
        description="Envia uma mensagem de texto para o usuário via Telegram.",
        parameters={
            "type": "object",
            "properties": {
                "message": {"type": "string", "description": "Texto da mensagem"}
            },
            "required": ["message"],
        },
    ),
    FunctionDeclaration(
        name="send_whatsapp",
        description="Envia uma mensagem WhatsApp para um número de telefone.",
        parameters={
            "type": "object",
            "properties": {
                "phone": {
                    "type": "string",
                    "description": "Número no formato E.164, ex: +5511999999999",
                },
                "message": {"type": "string", "description": "Texto da mensagem"},
            },
            "required": ["phone", "message"],
        },
    ),
]


# ---------------------------------------------------------------------------
# SquadExecutor
# ---------------------------------------------------------------------------

class SquadExecutor:
    """
    Executa um squad YAML usando Gemini API com function calling.
    Cada agente tem acesso a ferramentas de I/O, busca e envio.
    """

    MAX_TOOL_ROUNDS = 40  # máximo de iterações de tool call por step

    def __init__(
        self,
        squad_yaml: Path,
        nexus_dir: Path,
        gemini_api_key: str,
        serper_api_key: str = "",
        telegram_token: str = "",
        telegram_chat_id: Optional[str] = None,
        default_model: str = "gemini-1.5-flash",
    ):
        if not GENAI_OK:
            raise RuntimeError("google-generativeai não está instalado")

        self.nexus_dir = nexus_dir
        self.tmp_dir = nexus_dir / "tmp"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

        self.serper_api_key = serper_api_key
        self.telegram_token = telegram_token
        self.telegram_chat_id = telegram_chat_id
        self.default_model = default_model

        genai.configure(api_key=gemini_api_key)

        with open(squad_yaml, encoding="utf-8") as f:
            self.squad: dict = yaml.safe_load(f)

    # -------------------------------------------------------------------------
    # Implementações das ferramentas (síncronas — rodam em thread)
    # -------------------------------------------------------------------------

    def _read_file(self, path: str) -> str:
        p = self.nexus_dir / path
        if not p.exists():
            return f"[ARQUIVO NÃO ENCONTRADO: {path}]"
        try:
            return p.read_text(encoding="utf-8")
        except Exception as e:
            return f"[ERRO AO LER {path}: {e}]"

    def _write_file(self, path: str, content: str) -> str:
        p = self.nexus_dir / path
        p.parent.mkdir(parents=True, exist_ok=True)
        try:
            p.write_text(content, encoding="utf-8")
            return f"OK: {path} ({len(content)} bytes escritos)"
        except Exception as e:
            return f"ERRO ao escrever {path}: {e}"

    def _serper_search(self, query: str) -> str:
        if not self.serper_api_key:
            # Retorna dados simulados para não travar o pipeline
            return json.dumps({
                "organic": [
                    {
                        "title": f"Busca simulada: {query}",
                        "snippet": "Configure SERPER_API_KEY para resultados reais.",
                        "link": "https://exemplo.com",
                    }
                ]
            })
        import requests  # noqa: PLC0415
        try:
            resp = requests.post(
                "https://google.serper.dev/search",
                headers={
                    "X-API-KEY": self.serper_api_key,
                    "Content-Type": "application/json",
                },
                json={"q": query, "num": 20, "hl": "pt-br", "gl": "br"},
                timeout=15,
            )
            return resp.text
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _send_telegram(self, message: str) -> str:
        if not self.telegram_token or not self.telegram_chat_id:
            return "Telegram não configurado (token ou chat_id ausente)"
        import requests  # noqa: PLC0415
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
                json={
                    "chat_id": self.telegram_chat_id,
                    "text": message,
                    "parse_mode": "Markdown",
                },
                timeout=10,
            )
            data = resp.json()
            return "enviado" if data.get("ok") else f"erro: {data.get('description')}"
        except Exception as e:
            return f"Erro Telegram: {e}"

    def _send_whatsapp(self, phone: str, message: str) -> str:
        import requests  # noqa: PLC0415
        try:
            resp = requests.post(
                "http://localhost:5000/send",
                json={"phone": phone, "message": message},
                timeout=30,
            )
            return resp.json().get("status", "sem status")
        except Exception as e:
            return f"Erro WhatsApp: {e}"

    def _dispatch(self, name: str, args: dict) -> str:
        """Despacha uma tool call para a implementação correta."""
        logger.info("[tool] %s(%s)", name, {k: v[:80] if isinstance(v, str) else v for k, v in args.items()})
        if name == "read_file":
            return self._read_file(args.get("path", ""))
        if name == "write_file":
            return self._write_file(args.get("path", ""), args.get("content", ""))
        if name == "serper_search":
            return self._serper_search(args.get("query", ""))
        if name == "send_telegram":
            return self._send_telegram(args.get("message", ""))
        if name == "send_whatsapp":
            return self._send_whatsapp(args.get("phone", ""), args.get("message", ""))
        return f"[ferramenta desconhecida: {name}]"

    # -------------------------------------------------------------------------
    # Execução de step (síncrono — chamado via asyncio.to_thread)
    # -------------------------------------------------------------------------

    def _run_step_sync(self, step: dict, variables: dict) -> str:
        """Executa um único agente do pipeline com tool calling loop."""
        model_name = step.get("model", self.default_model)
        # Normaliza nomes: "gemini-2.0-flash" ou "google/gemini-2.5-flash" → só o sufixo
        if "/" in model_name:
            model_name = model_name.split("/", 1)[1]

        model = genai.GenerativeModel(
            model_name=model_name,
            tools=[Tool(function_declarations=_TOOL_SCHEMA)],
        )

        # Substitui variáveis no prompt
        prompt = step.get("prompt", "")
        for key, val in variables.items():
            prompt = prompt.replace(f"{{{{{key}}}}}", str(val))

        logger.info("[step:%s] modelo=%s prompt_len=%d", step["id"], model_name, len(prompt))

        chat = model.start_chat()
        response = chat.send_message(prompt)

        # Loop de tool calls
        for round_n in range(self.MAX_TOOL_ROUNDS):
            fc_parts = [
                p for p in response.parts
                if hasattr(p, "function_call") and p.function_call.name
            ]
            if not fc_parts:
                break  # Sem mais tool calls — resposta final

            results = []
            for part in fc_parts:
                fc = part.function_call
                tool_result = self._dispatch(fc.name, dict(fc.args))
                results.append(
                    glm.Part(
                        function_response=glm.FunctionResponse(
                            name=fc.name,
                            response={"result": tool_result},
                        )
                    )
                )

            logger.info("[step:%s] round=%d tool_calls=%d", step["id"], round_n, len(results))
            response = chat.send_message(results)

        # Extrai texto final
        for part in response.parts:
            if hasattr(part, "text") and part.text:
                return part.text

        return "(step sem resposta de texto)"

    # -------------------------------------------------------------------------
    # Pipeline completo (assíncrono)
    # -------------------------------------------------------------------------

    async def run(
        self,
        progress_cb: Optional[Callable[[str], Coroutine]] = None,
    ) -> dict[str, str]:
        """
        Executa todos os steps do pipeline em sequência.
        progress_cb: async def(msg: str) — chamado a cada atualização de status.
        """
        pipeline = self.squad.get("pipeline", [])
        variables: dict[str, str] = {
            v["name"]: str(v.get("default", ""))
            for v in self.squad.get("variables", [])
        }
        results: dict[str, str] = {}

        async def notify(msg: str) -> None:
            if progress_cb:
                try:
                    await progress_cb(msg)
                except Exception:
                    pass  # notificação nunca deve travar o pipeline

        await notify(f"🚀 Squad *{self.squad.get('name', '?')}* iniciado — {len(pipeline)} step(s)")

        for step in pipeline:
            step_id = step["id"]
            step_name = step.get("name", step_id)

            await notify(f"▶️ *{step_name}*...")

            try:
                result = await asyncio.to_thread(self._run_step_sync, step, variables)
                results[step_id] = result

                # Preview curto da saída
                preview = result[:300].strip()
                if len(result) > 300:
                    preview += "…"
                await notify(f"✅ *{step_name}* concluído\n```\n{preview}\n```")

            except asyncio.CancelledError:
                await notify(f"⛔ Squad cancelado no step *{step_name}*")
                raise
            except Exception as exc:
                logger.exception("Erro no step %s", step_id)
                results[step_id] = f"ERRO: {exc}"
                await notify(f"❌ *{step_name}*: {exc}")
                # Continua nos próximos steps (não interrompe o pipeline)

        await notify(f"🏁 Squad *{self.squad.get('name', '?')}* finalizado")
        return results
