"""
executor.py — Executa pipelines OpenSquad-style via Vertex AI (Gemini 2.5 Flash).

Usa Application Default Credentials da VM (service account agenda-lucrativa-sa)
— sem API key separada. Cada step do squad YAML roda com Gemini function calling:
o modelo pode ler/escrever arquivos, pesquisar (Serper), enviar WhatsApp e Telegram.
"""

import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Callable, Coroutine, Optional

import yaml

logger = logging.getLogger(__name__)

try:
    import vertexai
    from vertexai.generative_models import (
        FunctionDeclaration,
        GenerativeModel,
        Part,
        Tool,
    )
    VERTEXAI_OK = True
except ImportError:
    VERTEXAI_OK = False
    logger.error("Instale: pip install google-cloud-aiplatform")


# ---------------------------------------------------------------------------
# Schema das ferramentas disponíveis para o Gemini
# ---------------------------------------------------------------------------

def _build_tools() -> "Tool":
    return Tool(function_declarations=[
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
            description="Escreve conteúdo em um arquivo. Use caminhos relativos: tmp/resultado.json",
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
            description="Pesquisa no Google via Serper API. Retorna resultados orgânicos e dados de empresas locais.",
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
                    "phone": {"type": "string", "description": "Número E.164, ex: +5511999999999"},
                    "message": {"type": "string", "description": "Texto da mensagem"},
                },
                "required": ["phone", "message"],
            },
        ),
    ])


# ---------------------------------------------------------------------------
# SquadExecutor
# ---------------------------------------------------------------------------

class SquadExecutor:
    """
    Executa um squad YAML usando Vertex AI (Gemini 2.5 Flash) com function calling.
    Usa ADC da VM — sem API key separada.
    """

    MAX_TOOL_ROUNDS = 40

    def __init__(
        self,
        squad_yaml: Path,
        nexus_dir: Path,
        project_id: str,
        location: str = "us-central1",
        serper_api_key: str = "",
        telegram_token: str = "",
        telegram_chat_id: Optional[str] = None,
        default_model: str = "gemini-2.5-flash",
        gemini_api_key: str = "",  # mantido por compatibilidade, não usado com Vertex
    ):
        if not VERTEXAI_OK:
            raise RuntimeError("google-cloud-aiplatform não está instalado")

        self.nexus_dir = nexus_dir
        self.tmp_dir = nexus_dir / "tmp"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

        self.serper_api_key = serper_api_key
        self.telegram_token = telegram_token
        self.telegram_chat_id = telegram_chat_id
        self.default_model = default_model

        vertexai.init(project=project_id, location=location)

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
            return json.dumps({
                "organic": [{"title": f"Busca simulada: {query}", "snippet": "Configure serper-api-key"}]
            })
        import requests
        try:
            resp = requests.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": self.serper_api_key, "Content-Type": "application/json"},
                json={"q": query, "num": 20, "hl": "pt-br", "gl": "br"},
                timeout=15,
            )
            return resp.text
        except Exception as e:
            return json.dumps({"error": str(e)})

    def _send_telegram(self, message: str) -> str:
        if not self.telegram_token or not self.telegram_chat_id:
            return "Telegram não configurado"
        import requests
        try:
            resp = requests.post(
                f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
                json={"chat_id": self.telegram_chat_id, "text": message, "parse_mode": "Markdown"},
                timeout=10,
            )
            data = resp.json()
            return "enviado" if data.get("ok") else f"erro: {data.get('description')}"
        except Exception as e:
            return f"Erro Telegram: {e}"

    def _send_whatsapp(self, phone: str, message: str) -> str:
        import requests
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
        logger.info("[tool] %s(%s)", name, {k: str(v)[:80] for k, v in args.items()})
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
        model_name = step.get("model", self.default_model)
        # Normaliza: "google/gemini-2.5-flash" → "gemini-2.5-flash"
        if "/" in model_name:
            model_name = model_name.split("/", 1)[1]
        # Troca modelos descontinuados
        if model_name in ("gemini-2.0-flash", "gemini-1.5-flash"):
            model_name = self.default_model

        model = GenerativeModel(
            model_name=model_name,
            tools=[_build_tools()],
        )

        prompt = step.get("prompt", "")
        for key, val in variables.items():
            prompt = prompt.replace(f"{{{{{key}}}}}", str(val))

        logger.info("[step:%s] model=%s prompt_len=%d", step["id"], model_name, len(prompt))

        chat = model.start_chat()
        response = chat.send_message(prompt)

        # Loop de tool calls
        for round_n in range(self.MAX_TOOL_ROUNDS):
            fc_parts = [
                p for p in response.candidates[0].content.parts
                if hasattr(p, "function_call") and p.function_call.name
            ]
            if not fc_parts:
                break

            tool_responses = []
            for part in fc_parts:
                fc = part.function_call
                result = self._dispatch(fc.name, dict(fc.args))
                tool_responses.append(
                    Part.from_function_response(
                        name=fc.name,
                        response={"result": result},
                    )
                )

            logger.info("[step:%s] round=%d tools=%d", step["id"], round_n, len(tool_responses))
            response = chat.send_message(tool_responses)

        # Texto final
        for part in response.candidates[0].content.parts:
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
                    pass

        await notify(f"🚀 Squad *{self.squad.get('name', '?')}* iniciado — {len(pipeline)} step(s)")

        for step in pipeline:
            step_id = step["id"]
            step_name = step.get("name", step_id)

            await notify(f"▶️ *{step_name}*...")

            try:
                result = await asyncio.to_thread(self._run_step_sync, step, variables)
                results[step_id] = result

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

        await notify(f"🏁 Squad *{self.squad.get('name', '?')}* finalizado")
        return results
