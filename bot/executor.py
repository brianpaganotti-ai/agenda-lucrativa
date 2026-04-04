"""
executor.py — Executa pipelines OpenSquad-style via Vertex AI (Gemini 2.5 Flash).

Arquitetura: cada step tem um handler Python dedicado.
Gemini gera apenas texto — Python faz todo I/O, API calls e parse.
Sem function calling (incompatível com Gemini 2.5 Flash via Vertex AI SDK).
"""

import asyncio
import json
import logging
import re
from datetime import datetime
from pathlib import Path
from typing import Callable, Coroutine, Optional

import requests
import yaml

logger = logging.getLogger(__name__)

try:
    import vertexai
    from vertexai.generative_models import GenerativeModel, GenerationConfig
    VERTEXAI_OK = True
except ImportError:
    VERTEXAI_OK = False
    logger.error("Instale: pip install google-cloud-aiplatform")


# ---------------------------------------------------------------------------
# SquadExecutor
# ---------------------------------------------------------------------------

class SquadExecutor:
    """
    Executa um squad YAML.
    Gemini 2.5 Flash (Vertex AI) para geração de texto.
    Python puro para I/O, Serper, WhatsApp, Telegram.
    """

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
        gemini_api_key: str = "",  # não usado com Vertex AI
    ):
        if not VERTEXAI_OK:
            raise RuntimeError("google-cloud-aiplatform não instalado")

        self.nexus_dir = nexus_dir
        self.tmp_dir = nexus_dir / "tmp"
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

        squad_name = Path(squad_yaml).stem
        self.run_id = f"{squad_name}_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
        self.log_path = nexus_dir / "logs" / squad_name / f"{self.run_id}.log"
        self.log_path.parent.mkdir(parents=True, exist_ok=True)

        self.serper_api_key = serper_api_key
        self.telegram_token = telegram_token
        self.telegram_chat_id = telegram_chat_id
        # Normaliza nome do modelo (remove prefixo "google/", "vertex/", etc.)
        if "/" in default_model:
            default_model = default_model.split("/", 1)[1]
        self.default_model = default_model

        vertexai.init(project=project_id, location=location)
        self.model = GenerativeModel(
            model_name=default_model,
            generation_config=GenerationConfig(temperature=0.7, max_output_tokens=8192),
        )

        with open(squad_yaml, encoding="utf-8") as f:
            self.squad: dict = yaml.safe_load(f)

    # -------------------------------------------------------------------------
    # Gemini — geração de texto simples
    # -------------------------------------------------------------------------

    def _gemini(self, prompt: str) -> str:
        """Chama Gemini e retorna o texto gerado."""
        response = self.model.generate_content(prompt)
        return response.text.strip()

    # -------------------------------------------------------------------------
    # Utilidades de arquivo
    # -------------------------------------------------------------------------

    def _read(self, filename: str) -> str:
        p = self.tmp_dir / filename
        return p.read_text(encoding="utf-8") if p.exists() else ""

    def _write(self, filename: str, content: str) -> None:
        p = self.tmp_dir / filename
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(content, encoding="utf-8")
        logger.info("Escrito: %s (%d bytes)", filename, len(content))

    def _extract_json(self, text: str):
        """Extrai JSON de resposta do Gemini (ignora markdown code fences)."""
        # Remove ```json ... ``` ou ``` ... ```
        text = re.sub(r"```(?:json)?\s*", "", text)
        text = re.sub(r"```\s*$", "", text, flags=re.MULTILINE).strip()
        # Tenta encontrar array ou objeto
        for pattern in (r"\[[\s\S]*\]", r"\{[\s\S]*\}"):
            m = re.search(pattern, text)
            if m:
                try:
                    return json.loads(m.group())
                except json.JSONDecodeError:
                    pass
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            return None

    # -------------------------------------------------------------------------
    # APIs externas
    # -------------------------------------------------------------------------

    def _serper(self, query: str) -> dict:
        if not self.serper_api_key:
            return {"organic": [], "localResults": []}
        try:
            r = requests.post(
                "https://google.serper.dev/search",
                headers={"X-API-KEY": self.serper_api_key, "Content-Type": "application/json"},
                json={"q": query, "num": 20, "hl": "pt-br", "gl": "br"},
                timeout=15,
            )
            return r.json()
        except Exception as e:
            logger.error("Serper error: %s", e)
            return {"organic": [], "localResults": []}

    def _whatsapp_send(self, phone: str, message: str) -> str:
        try:
            r = requests.post(
                "http://localhost:5000/send",
                json={"phone": phone, "message": message},
                timeout=30,
            )
            return r.json().get("status", "ok")
        except Exception as e:
            return f"erro: {e}"

    def _telegram_send(self, text: str) -> None:
        if not self.telegram_token or not self.telegram_chat_id:
            return
        try:
            requests.post(
                f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
                json={"chat_id": self.telegram_chat_id, "text": text},
                timeout=10,
            )
        except Exception as e:
            logger.warning("Telegram send error: %s", e)

    # -------------------------------------------------------------------------
    # Handlers por step
    # -------------------------------------------------------------------------

    def _step_buscador(self, variables: dict) -> str:
        cidade = variables.get("CIDADE", "São Paulo")
        segmento = variables.get("SEGMENTO", "salões de beleza")
        limite = int(variables.get("LIMITE", "20"))

        # 1. Busca real via Serper
        raw = self._serper(f"{segmento} em {cidade}")

        # 2. Gemini extrai e normaliza para lista de leads
        if not raw.get("organic") and not raw.get("localResults") and not raw.get("places"):
            self._write("leads_brutos.json",
                json.dumps({"status": "sem_resultados", "leads": []}, ensure_ascii=False))
            raise RuntimeError(
                f"Serper não retornou resultados para '{segmento} em {cidade}'. "
                "Verifique a serper-api-key ou tente outro segmento/cidade.")

        prompt = (
            f"Você recebeu resultados de busca para '{segmento} em {cidade}'.\n\n"
            f"DADOS BRUTOS:\n{json.dumps(raw, ensure_ascii=False)[:6000]}\n\n"
            f"Extraia até {limite} negócios locais que tenham telefone.\n"
            f"Retorne SOMENTE um array JSON válido, sem texto adicional, sem markdown:\n"
            f'[{{"nome":"...","endereco":"...","telefone":"...","website":"...","instagram":""}}]\n'
            f"Use aspas duplas. Retorne [] se não houver negócios com telefone."
        )
        result = self._gemini(prompt)
        leads = self._extract_json(result)
        if not isinstance(leads, list):
            leads = []

        if not leads:
            self._write("leads_brutos.json",
                json.dumps({"status": "sem_resultados", "leads": []}, ensure_ascii=False))
            raise RuntimeError(
                f"Nenhum lead com telefone encontrado para '{segmento} em {cidade}'. "
                "Tente segmento ou cidade diferente.")

        self._write("leads_brutos.json", json.dumps(leads, ensure_ascii=False, indent=2))
        return f"Encontrados {len(leads)} leads brutos."

    def _step_qualificador(self, variables: dict) -> str:
        raw = self._read("leads_brutos.json")
        if not raw:
            return "leads_brutos.json não encontrado."

        segmento = variables.get("SEGMENTO", "negócio local")
        prompt = (
            f"Qualifique estes leads para {segmento}.\n\n"
            f"LEADS:\n{raw}\n\n"
            f"Critérios:\n"
            f"- Telefone celular (9 após DDD): +3\n"
            f"- Website ou Instagram: +3\n"
            f"- Negócio local (não franquia): +2\n"
            f"- Dados completos: +2\n\n"
            f"Retorne SOMENTE array JSON dos leads com score >= 6, sem markdown:\n"
            f'[{{"nome":"...","telefone":"...","website":"...","instagram":"...","score":8,"motivo":"..."}}]'
        )
        result = self._gemini(prompt)
        leads_q = self._extract_json(result)
        if not isinstance(leads_q, list):
            leads_q = []

        self._write("leads_qualificados.json", json.dumps(leads_q, ensure_ascii=False, indent=2))

        resumo = (
            f"Qualificação concluída:\n"
            f"- Total qualificados: {len(leads_q)}\n"
            f"- Scores: {[l.get('score', 0) for l in leads_q]}"
        )
        self._write("qualificacao_resumo.txt", resumo)
        return resumo

    def _step_redator(self, variables: dict) -> str:
        leads_raw = self._read("leads_qualificados.json")
        if not leads_raw:
            return "leads_qualificados.json não encontrado."

        segmento = variables.get("SEGMENTO", "negócio local")
        prompt = (
            f"Crie mensagens de WhatsApp para estes leads de {segmento}.\n\n"
            f"LEADS:\n{leads_raw}\n\n"
            f"Regras:\n"
            f"- Tom profissional mas descontraído, em português\n"
            f"- Máximo 3 parágrafos curtos\n"
            f"- Mencione o nome do negócio\n"
            f"- Destaque 1 benefício específico para {segmento}\n"
            f"- CTA claro para agendar conversa\n"
            f"- Máximo 2 emojis por mensagem\n\n"
            f"Retorne SOMENTE array JSON, sem markdown:\n"
            f'[{{"lead":"nome","telefone":"+5511...","mensagem":"texto aqui"}}]'
        )
        result = self._gemini(prompt)
        msgs = self._extract_json(result)
        if not isinstance(msgs, list):
            msgs = []

        self._write("mensagens_whatsapp.json", json.dumps(msgs, ensure_ascii=False, indent=2))
        return f"{len(msgs)} mensagens redigidas."

    def _step_disparador(self, variables: dict) -> str:
        msgs_raw = self._read("mensagens_whatsapp.json")
        if not msgs_raw:
            return "mensagens_whatsapp.json não encontrado."

        try:
            msgs = json.loads(msgs_raw)
        except json.JSONDecodeError:
            return "Erro ao parsear mensagens_whatsapp.json."

        resultados = []
        for msg in msgs:
            phone = msg.get("telefone", "")
            text = msg.get("mensagem", "")
            lead = msg.get("lead", "?")
            status = self._whatsapp_send(phone, text)
            resultados.append({"lead": lead, "telefone": phone, "status": status})
            import time; time.sleep(3)

        enviados = sum(1 for r in resultados if "erro" not in r["status"].lower())
        relatorio = {"total": len(resultados), "enviados": enviados,
                     "falhas": len(resultados) - enviados, "detalhes": resultados}
        self._write("relatorio_prospeccao.json", json.dumps(relatorio, ensure_ascii=False, indent=2))
        return f"Disparado: {enviados}/{len(resultados)} enviados."

    def _step_notificador(self, variables: dict) -> str:
        resumo = self._read("qualificacao_resumo.txt")
        relatorio_raw = self._read("relatorio_prospeccao.json")

        msg = "📊 *Prospecção concluída!*\n\n"
        if resumo:
            msg += resumo + "\n\n"
        if relatorio_raw:
            try:
                rel = json.loads(relatorio_raw)
                msg += (
                    f"📤 WhatsApp:\n"
                    f"- Enviadas: {rel.get('enviados', 0)}\n"
                    f"- Falhas: {rel.get('falhas', 0)}"
                )
            except Exception:
                pass

        self._telegram_send(msg.replace("*", ""))
        return msg

    # Handler genérico para steps não mapeados
    def _step_generic(self, step: dict, variables: dict) -> str:
        prompt = step.get("prompt", "")
        for k, v in variables.items():
            prompt = prompt.replace(f"{{{{{k}}}}}", str(v))
        return self._gemini(prompt)

    # Mapa step_id → handler
    _HANDLERS = {
        "buscador": _step_buscador,
        "qualificador": _step_qualificador,
        "redator": _step_redator,
        "disparador": _step_disparador,
        "notificador": _step_notificador,
        # conteudo-instagram
        "pesquisador": lambda self, v: self._step_generic({"prompt": "Pesquise tendências de Instagram."}, v),
        "criador": lambda self, v: self._step_generic({"prompt": "Crie conteúdo."}, v),
        "revisor": lambda self, v: self._step_generic({"prompt": "Revise o conteúdo."}, v),
        "aprovacao": lambda self, v: self._step_generic({"prompt": "Apresente preview."}, v),
        "finalizador": lambda self, v: self._step_generic({"prompt": "Finalize."}, v),
    }

    def _run_step_sync(self, step: dict, variables: dict) -> str:
        step_id = step["id"]
        handler = self._HANDLERS.get(step_id)
        if handler:
            return handler(self, variables)
        return self._step_generic(step, variables)

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

        squad_name = self.squad.get("name", "?")

        def _log(msg: str) -> None:
            ts = datetime.now().strftime("%H:%M:%S")
            with open(self.log_path, "a", encoding="utf-8") as lf:
                lf.write(f"[{ts}] {msg}\n")

        _log(f"RUN_ID={self.run_id}")
        await notify(f"🚀 Squad *{squad_name}* iniciado — {len(pipeline)} step(s)")
        _log(f"iniciado — {len(pipeline)} steps")

        for step in pipeline:
            step_id = step["id"]
            step_name = step.get("name", step_id)

            await notify(f"▶️ *{step_name}*...")
            _log(f"[{step_id}] iniciando")

            try:
                result = await asyncio.to_thread(self._run_step_sync, step, variables)
                results[step_id] = result
                _log(f"[{step_id}] OK — {result[:120]}")

                preview = result[:300].strip()
                if len(result) > 300:
                    preview += "…"
                await notify(f"✅ *{step_name}*\n```\n{preview}\n```")

            except asyncio.CancelledError:
                _log(f"[{step_id}] CANCELADO")
                await notify(f"⛔ Cancelado em *{step_name}*")
                raise
            except Exception as exc:
                logger.exception("Erro no step %s", step_id)
                _log(f"[{step_id}] ERRO — {exc}")
                results[step_id] = f"ERRO: {exc}"
                await notify(f"❌ *{step_name}*: {exc}")

        _log("finalizado")
        await notify(f"🏁 Squad *{squad_name}* finalizado")
        return results
