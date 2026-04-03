"""
squad_runner.py — Executa squads YAML via SquadExecutor.

Tier: POWERFUL
Input:
  squad_name  (str, obrigatório) — nome do squad (sem .yaml)
  variables   (dict | str, default {}) — variáveis para sobrescrever defaults do squad
  nexus_dir   (str, default "/opt/nexus") — diretório base
  project_id  (str, default "") — GCP project ID para Vertex AI
  location    (str, default "us-central1") — região Vertex AI

Output: JSON string com resultados de cada step
  {"step_id": "resultado", ...}

Wraps SquadExecutor sem modificar executor.py.
O /run do Telegram é preservado 100% — esta skill é para uso via /skill squad-runner.
"""

import asyncio
import json
from pathlib import Path
import sys

DESCRIPTION = "Executa pipelines de prospecção e automação (squads YAML)"
DEFAULT_TIER = "powerful"


def run(context: dict, provider) -> str:
    squad_name = context.get("squad_name", "")
    if not squad_name:
        return '{"erro": "Parâmetro squad_name é obrigatório"}'

    nexus_dir = Path(context.get("nexus_dir", "/opt/nexus"))
    project_id = context.get("project_id", "")
    location = context.get("location", "us-central1")

    # Variáveis extras passadas pelo usuário
    variables_raw = context.get("variables", {})
    if isinstance(variables_raw, str):
        try:
            variables_raw = json.loads(variables_raw)
        except json.JSONDecodeError:
            variables_raw = {}

    squad_yaml = nexus_dir / "squads" / f"{squad_name}.yaml"
    if not squad_yaml.exists():
        return json.dumps({"erro": f"Squad '{squad_name}' não encontrado em {nexus_dir}/squads/"})

    # Adiciona o diretório do bot ao path se necessário
    bot_dir = Path(__file__).parent.parent / "bot"
    if str(bot_dir.parent) not in sys.path:
        sys.path.insert(0, str(bot_dir.parent))

    try:
        from bot.executor import SquadExecutor
    except ImportError as e:
        return json.dumps({"erro": f"Não foi possível importar SquadExecutor: {e}"})

    try:
        executor = SquadExecutor(
            squad_yaml=squad_yaml,
            nexus_dir=nexus_dir,
            project_id=project_id,
            location=location,
        )

        # Sobrescreve variáveis do squad com as fornecidas
        if variables_raw and hasattr(executor, "squad"):
            for var in executor.squad.get("variables", []):
                if var["name"] in variables_raw:
                    var["default"] = variables_raw[var["name"]]

        results = asyncio.run(executor.run())
        return json.dumps(results, ensure_ascii=False, indent=2)

    except Exception as e:
        return json.dumps({"erro": str(e)})
