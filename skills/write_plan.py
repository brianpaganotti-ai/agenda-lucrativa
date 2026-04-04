"""
write_plan.py — Criação de planos estratégicos detalhados.

Tier: POWERFUL
Input:
  goal        (str, obrigatório) — objetivo principal do plano
  audience    (str, obrigatório) — público-alvo
  constraints (str, default "") — limitações, restrições ou recursos disponíveis
  timeframe   (str, default "") — prazo ou horizonte de tempo

Output: markdown com seções padronizadas
  ## Objetivo / ## Público / ## Etapas / ## Métricas / ## Próximo Passo
"""

from pathlib import Path

DESCRIPTION = "Cria planos estratégicos completos com etapas, métricas e próximos passos"
DEFAULT_TIER = "powerful"

_BP_PATH = Path(__file__).parent.parent / "_opensquad/core/best-practices/strategist.md"


def _load_best_practices() -> str:
    if _BP_PATH.exists():
        return f"\n\nDIRETRIZES ESTRATÉGICAS:\n{_BP_PATH.read_text(encoding='utf-8')[:2000]}"
    return ""


def run(context: dict, provider) -> str:
    goal = context.get("goal", "")
    if not goal:
        return "**Erro:** Parâmetro `goal` é obrigatório."

    audience = context.get("audience", "público geral")

    constraints = context.get("constraints", "")
    timeframe = context.get("timeframe", "")
    tier = context.get("_tier", DEFAULT_TIER)

    bp = _load_best_practices()

    constraints_block = f"\n**Restrições/Recursos:** {constraints}" if constraints else ""
    timeframe_block = f"\n**Prazo:** {timeframe}" if timeframe else ""

    prompt = (
        f"Crie um plano estratégico detalhado para:\n\n"
        f"**Objetivo:** {goal}\n"
        f"**Público-alvo:** {audience}"
        f"{constraints_block}"
        f"{timeframe_block}"
        f"{bp}\n\n"
        f"Estruture o plano com exatamente estas seções em markdown:\n\n"
        f"## Objetivo\n"
        f"Reafirme o objetivo de forma clara e mensurável (1-2 frases).\n\n"
        f"## Público\n"
        f"Perfil detalhado do público: características, dores, desejos, comportamentos.\n\n"
        f"## Etapas\n"
        f"Lista numerada de etapas sequenciais. Cada etapa:\n"
        f"- **Nome da etapa** (prazo estimado)\n"
        f"- O que fazer e como fazer\n"
        f"- Resultado esperado\n\n"
        f"## Métricas\n"
        f"KPIs específicos e mensuráveis para acompanhar o progresso.\n\n"
        f"## Próximo Passo\n"
        f"Uma única ação concreta e imediata para começar hoje.\n\n"
        f"Seja específico, prático e acionável. Evite generalidades."
    )

    return provider.generate(prompt, tier=tier)
