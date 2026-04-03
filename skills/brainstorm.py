"""
brainstorm.py — Geração de ideias estruturadas.

Tier: FAST
Input:
  topic     (str, obrigatório) — tema do brainstorm
  quantity  (int, default 10) — número de ideias a gerar
  context   (str, default "") — contexto adicional
  format    (str, default "structured") — "structured" | "list"

Output: JSON str → lista de ideias
  [{"titulo": "...", "desenvolvimento": "...", "aplicacao": "..."}]
"""

from pathlib import Path

DESCRIPTION = "Gera ideias criativas e estruturadas sobre qualquer tema"
DEFAULT_TIER = "fast"

_BP_PATH = Path(__file__).parent.parent / "_opensquad/core/best-practices/copywriting.md"


def _load_best_practices() -> str:
    if _BP_PATH.exists():
        return f"\n\nDIRETRIZES DE COPYWRITING:\n{_BP_PATH.read_text(encoding='utf-8')[:2000]}"
    return ""


def run(context: dict, provider) -> str:
    topic = context.get("topic", "")
    if not topic:
        return '{"erro": "Parâmetro topic é obrigatório"}'

    quantity = int(context.get("quantity", 10))
    extra_context = context.get("context", "")
    fmt = context.get("format", "structured")
    tier = context.get("_tier", DEFAULT_TIER)

    bp = _load_best_practices()
    ctx_block = f"\nCONTEXTO ADICIONAL: {extra_context}" if extra_context else ""

    if fmt == "structured":
        output_spec = (
            f'Retorne SOMENTE um array JSON válido com {quantity} ideias, sem markdown:\n'
            f'[{{"titulo":"nome curto","desenvolvimento":"2-3 frases explicando a ideia","aplicacao":"como aplicar na prática"}}]'
        )
    else:
        output_spec = (
            f'Retorne SOMENTE um array JSON com {quantity} strings, sem markdown:\n'
            f'["ideia 1", "ideia 2", ...]'
        )

    prompt = (
        f"Faça um brainstorm criativo e divergente sobre: {topic}{ctx_block}{bp}\n\n"
        f"Processo:\n"
        f"1. Pense amplamente — explore ângulos convencionais E não-convencionais\n"
        f"2. Filtre as {quantity} ideias com maior potencial de impacto\n"
        f"3. Para cada ideia: título memorável, desenvolvimento claro, aplicação prática\n\n"
        f"{output_spec}"
    )

    return provider.generate(prompt, tier=tier)
