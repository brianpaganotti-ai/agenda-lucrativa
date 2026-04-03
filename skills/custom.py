"""
custom.py — Executa skills customizadas definidas em YAML.

Tier: FAST (padrão — cada skill YAML pode sobrescrever)
Input:
  skill_file  (str, obrigatório) — nome do arquivo YAML sem .yaml
                  (buscado em skills/custom/)
  variables   (dict | str, default {}) — variáveis para interpolar no prompt
  nexus_dir   (str, default "/opt/nexus") — diretório base

Formato do YAML de skill customizada:
  name: "nome da skill"
  description: "descrição"
  tier: "fast"          # fast | powerful
  input_vars:           # lista de variáveis esperadas
    - nome
    - segmento
  output_format: "text" # text | json | markdown
  prompt_template: |
    Seu prompt aqui com {{nome}} e {{segmento}}.

Output: resultado gerado pelo provider (str)
"""

import json
import re
from pathlib import Path

import yaml

DESCRIPTION = "Executa skills customizadas definidas em arquivos YAML"
DEFAULT_TIER = "fast"


def _resolve_skill_path(skill_file: str, nexus_dir: Path) -> Path | None:
    """Procura o YAML em skills/custom/ tanto no repo quanto no nexus_dir."""
    candidates = [
        Path(__file__).parent / "custom" / f"{skill_file}.yaml",
        nexus_dir / "skills" / "custom" / f"{skill_file}.yaml",
    ]
    for p in candidates:
        if p.exists():
            return p
    return None


def _interpolate(template: str, variables: dict) -> str:
    """Substitui {{VARIAVEL}} no template."""
    result = template
    for key, value in variables.items():
        result = result.replace(f"{{{{{key}}}}}", str(value))
        result = result.replace(f"{{{{{key.upper()}}}}}", str(value))
    return result


def run(context: dict, provider) -> str:
    skill_file = context.get("skill_file", "")
    if not skill_file:
        return "**Erro:** Parâmetro `skill_file` é obrigatório."

    nexus_dir = Path(context.get("nexus_dir", "/opt/nexus"))

    # Variáveis fornecidas pelo usuário
    variables_raw = context.get("variables", {})
    if isinstance(variables_raw, str):
        try:
            variables_raw = json.loads(variables_raw)
        except json.JSONDecodeError:
            # Tenta parsear "key=value key2=value2"
            variables_raw = dict(
                pair.split("=", 1) for pair in variables_raw.split()
                if "=" in pair
            )

    # Merge com context (exclui chaves internas)
    _INTERNAL = {"skill_file", "nexus_dir", "variables", "_tier"}
    extra_vars = {k: v for k, v in context.items() if k not in _INTERNAL}
    variables = {**variables_raw, **extra_vars}

    # Carrega YAML da skill
    yaml_path = _resolve_skill_path(skill_file, nexus_dir)
    if not yaml_path:
        return (
            f"**Erro:** Skill `{skill_file}` não encontrada.\n"
            f"Crie `skills/custom/{skill_file}.yaml` com campos: name, prompt_template, tier."
        )

    try:
        with open(yaml_path, encoding="utf-8") as f:
            skill_def = yaml.safe_load(f)
    except Exception as e:
        return f"**Erro:** Falha ao carregar `{yaml_path}`: {e}"

    # Valida campos obrigatórios
    prompt_template = skill_def.get("prompt_template", "")
    if not prompt_template:
        return f"**Erro:** `{yaml_path}` não tem campo `prompt_template`."

    # Tier: contexto > YAML > default
    tier = context.get("_tier") or skill_def.get("tier", DEFAULT_TIER)

    # Verifica variáveis obrigatórias
    missing = []
    for var in skill_def.get("input_vars", []):
        if var not in variables:
            missing.append(var)
    if missing:
        return f"**Erro:** Variáveis obrigatórias não fornecidas: {', '.join(missing)}"

    # Interpola template
    prompt = _interpolate(prompt_template, variables)

    # Instrução de formato de saída
    output_format = skill_def.get("output_format", "text")
    if output_format == "json":
        prompt += "\n\nRetorne SOMENTE JSON válido, sem markdown."
    elif output_format == "markdown":
        prompt += "\n\nFormate a resposta em markdown."

    return provider.generate(prompt, tier=tier)
