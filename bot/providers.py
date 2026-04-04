"""
providers.py — Abstração de provedores de LLM com suporte a tiers FAST/POWERFUL.

Tiers:
  FAST    → gemini-2.5-flash / claude-haiku-4-5
            Para: classificação, extração, formatação, resumos, notificações
  POWERFUL → gemini-2.5-flash / claude-sonnet-4-5
            Para: criação, planejamento, pesquisa, execução de planos, design

Adicionar novo provider: subclasse ModelProvider + registrar em load_provider().
"""

import json
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Interface base
# ---------------------------------------------------------------------------

class ModelProvider:
    """Interface comum para todos os provedores de LLM."""

    TIER_FAST = "fast"
    TIER_POWERFUL = "powerful"

    def generate(self, prompt: str, tier: str = "powerful") -> str:
        raise NotImplementedError

    @property
    def name(self) -> str:
        raise NotImplementedError

    def is_available(self) -> bool:
        return True


# ---------------------------------------------------------------------------
# Gemini via Vertex AI (Application Default Credentials — sem API key)
# ---------------------------------------------------------------------------

class GeminiProvider(ModelProvider):

    MODELS = {
        "fast":    "gemini-2.5-flash",
        "powerful": "gemini-2.5-flash",
    }

    def __init__(self, project_id: str, location: str = "us-central1"):
        try:
            import vertexai
            from vertexai.generative_models import GenerativeModel, GenerationConfig
            vertexai.init(project=project_id, location=location)
            self._GenerativeModel = GenerativeModel
            self._GenerationConfig = GenerationConfig
            self._ok = True
        except ImportError:
            logger.error("google-cloud-aiplatform não instalado")
            self._ok = False

    @property
    def name(self) -> str:
        return "gemini"

    def is_available(self) -> bool:
        return self._ok

    def generate(self, prompt: str, tier: str = "powerful") -> str:
        if not self._ok:
            raise RuntimeError("Vertex AI SDK não disponível")
        model_name = self.MODELS.get(tier, self.MODELS["powerful"])
        model = self._GenerativeModel(
            model_name=model_name,
            generation_config=self._GenerationConfig(
                temperature=0.4 if tier == "fast" else 0.7,
                max_output_tokens=2048 if tier == "fast" else 8192,
            ),
        )
        response = model.generate_content(prompt)
        return response.text.strip()


# ---------------------------------------------------------------------------
# Claude via Anthropic API (requer claude-api-key no Secret Manager)
# ---------------------------------------------------------------------------

class ClaudeProvider(ModelProvider):

    MODELS = {
        "fast":    "claude-haiku-4-5-20251001",
        "powerful": "claude-sonnet-4-6",
    }

    def __init__(self, api_key: str):
        self._api_key = api_key
        try:
            import anthropic  # noqa: F401
            self._ok = bool(api_key)
        except ImportError:
            logger.error("anthropic não instalado — pip install anthropic")
            self._ok = False

    @property
    def name(self) -> str:
        return "claude"

    def is_available(self) -> bool:
        return self._ok

    def generate(self, prompt: str, tier: str = "powerful") -> str:
        if not self._ok:
            raise RuntimeError("Claude SDK não disponível ou API key ausente")
        import anthropic
        model_name = self.MODELS.get(tier, self.MODELS["powerful"])
        client = anthropic.Anthropic(api_key=self._api_key)
        message = client.messages.create(
            model=model_name,
            max_tokens=2048 if tier == "fast" else 8192,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()


# ---------------------------------------------------------------------------
# Registry — carrega provider a partir de config/providers.json
# ---------------------------------------------------------------------------

def load_providers(
    config_path: Path,
    project_id: str = "",
    claude_api_key: str = "",
) -> dict[str, ModelProvider]:
    """
    Lê config/providers.json e instancia os providers habilitados.
    Retorna dict {provider_name: ModelProvider}.
    """
    providers: dict[str, ModelProvider] = {}

    if not config_path.exists():
        logger.warning("providers.json não encontrado — usando Gemini padrão")
        if project_id:
            providers["gemini"] = GeminiProvider(project_id)
        return providers

    with open(config_path, encoding="utf-8") as f:
        config = json.load(f)

    for name, cfg in config.get("providers", {}).items():
        if not cfg.get("enabled", False):
            continue

        provider_type = cfg.get("type", name)

        if provider_type == "gemini":
            if project_id:
                providers[name] = GeminiProvider(project_id)
            else:
                logger.warning("gemini ignorado: project_id não configurado")

        elif provider_type == "claude":
            if claude_api_key:
                providers[name] = ClaudeProvider(claude_api_key)
            else:
                logger.warning("claude ignorado: claude-api-key não configurado")

        else:
            logger.warning("Tipo de provider desconhecido: %s", provider_type)

    return providers


def get_default_provider(
    providers: dict[str, ModelProvider],
    config_path: Path,
) -> Optional[ModelProvider]:
    """Retorna o provider padrão conforme config/providers.json."""
    if not providers:
        return None

    default_name = "gemini"
    if config_path.exists():
        with open(config_path, encoding="utf-8") as f:
            config = json.load(f)
        default_name = config.get("default", "gemini")

    return providers.get(default_name) or next(iter(providers.values()))
