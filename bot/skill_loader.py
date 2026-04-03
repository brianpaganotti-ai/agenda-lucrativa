"""
skill_loader.py — Carrega e executa skills Python de skills/.

Interface de skill:
  def run(context: dict, provider) -> str

  context: dict com variáveis da skill (ex: {"topic": "...", "quantity": 10})
  provider: instância de ModelProvider com .generate(prompt, tier)

Uso:
  loader = SkillLoader(skills_dir=Path("skills"))
  result = loader.execute("brainstorm", context={"topic": "café"}, provider=gemini)
"""

import importlib.util
import logging
from pathlib import Path
from typing import Optional

from bot.providers import ModelProvider

logger = logging.getLogger(__name__)


class SkillError(Exception):
    pass


class SkillLoader:

    def __init__(self, skills_dir: Path):
        self.skills_dir = Path(skills_dir)

    # ------------------------------------------------------------------
    # Descoberta
    # ------------------------------------------------------------------

    def list_skills(self) -> list[dict]:
        """Retorna lista de skills disponíveis com nome e descrição."""
        skills = []
        for path in sorted(self.skills_dir.glob("*.py")):
            if path.stem.startswith("_"):
                continue
            meta = self._load_meta(path)
            skills.append({"name": path.stem, **meta})
        return skills

    def _load_meta(self, path: Path) -> dict:
        """Lê NAME e DESCRIPTION do módulo sem executá-lo completamente."""
        try:
            spec = importlib.util.spec_from_file_location(path.stem, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
            return {
                "description": getattr(mod, "DESCRIPTION", ""),
                "tier": getattr(mod, "DEFAULT_TIER", "powerful"),
            }
        except Exception as e:
            logger.warning("Erro ao carregar meta de %s: %s", path.name, e)
            return {"description": "", "tier": "powerful"}

    # ------------------------------------------------------------------
    # Execução
    # ------------------------------------------------------------------

    def execute(
        self,
        skill_name: str,
        context: dict,
        provider: ModelProvider,
        tier: Optional[str] = None,
    ) -> str:
        """
        Carrega e executa uma skill pelo nome.

        skill_name: nome do arquivo sem .py (ex: "brainstorm")
        context: dict de variáveis passadas à skill
        provider: ModelProvider a usar
        tier: "fast" | "powerful" (se None, usa DEFAULT_TIER da skill)
        """
        path = self.skills_dir / f"{skill_name}.py"
        if not path.exists():
            raise SkillError(f"Skill '{skill_name}' não encontrada em {self.skills_dir}")

        try:
            spec = importlib.util.spec_from_file_location(skill_name, path)
            mod = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(mod)
        except Exception as e:
            raise SkillError(f"Erro ao importar skill '{skill_name}': {e}") from e

        if not hasattr(mod, "run"):
            raise SkillError(f"Skill '{skill_name}' não tem função run(context, provider)")

        # Resolve tier: argumento > DEFAULT_TIER do módulo > "powerful"
        resolved_tier = tier or getattr(mod, "DEFAULT_TIER", "powerful")

        # Injeta tier no context para que a skill possa usá-lo se quiser
        ctx = {**context, "_tier": resolved_tier}

        logger.info("Executando skill '%s' tier=%s", skill_name, resolved_tier)
        try:
            return mod.run(ctx, provider)
        except Exception as e:
            raise SkillError(f"Erro na execução de '{skill_name}': {e}") from e

    # ------------------------------------------------------------------
    # Helper: normaliza nome de skill
    # ------------------------------------------------------------------

    @staticmethod
    def normalize_name(name: str) -> str:
        """'Write Plan' → 'write_plan', 'autoresearch' → 'autoresearch'"""
        return name.strip().lower().replace(" ", "_").replace("-", "_")
