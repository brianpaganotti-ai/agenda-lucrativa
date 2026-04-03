"""
autoresearch.py — Pesquisa automatizada em duas fases.

Fase 1 — FAST: busca via Serper + triagem de fontes relevantes
Fase 2 — POWERFUL: síntese estruturada

Tier: FAST (fase 1) + POWERFUL (fase 2)
Input:
  topic         (str, obrigatório) — tema da pesquisa
  depth         (str, default "quick") — "quick" | "deep"
  output_format (str, default "summary") — "summary" | "report" | "bullets"
  serper_key    (str, default "") — chave Serper (usa env SERPER_API_KEY se vazio)

Output: relatório estruturado em markdown
  Seções: Fontes / Achados / Dados / Conclusão Acionável
"""

import json
import os
from pathlib import Path

import requests

DESCRIPTION = "Pesquisa automatizada com síntese inteligente (FAST busca + POWERFUL síntese)"
DEFAULT_TIER = "powerful"

_BP_PATH = Path(__file__).parent.parent / "_opensquad/core/best-practices/researching.md"


def _load_best_practices() -> str:
    if _BP_PATH.exists():
        return f"\n\nDIRETRIZES DE PESQUISA:\n{_BP_PATH.read_text(encoding='utf-8')[:1500]}"
    return ""


def _serper_search(query: str, serper_key: str, num: int = 10) -> dict:
    if not serper_key:
        return {"organic": [], "error": "serper_key não configurada"}
    try:
        r = requests.post(
            "https://google.serper.dev/search",
            headers={"X-API-KEY": serper_key, "Content-Type": "application/json"},
            json={"q": query, "num": num, "hl": "pt-br", "gl": "br"},
            timeout=15,
        )
        return r.json()
    except Exception as e:
        return {"organic": [], "error": str(e)}


def _format_serper_results(raw: dict) -> str:
    """Formata resultados Serper para o prompt."""
    lines = []
    for item in raw.get("organic", [])[:8]:
        title = item.get("title", "")
        snippet = item.get("snippet", "")
        link = item.get("link", "")
        lines.append(f"- [{title}]({link})\n  {snippet}")
    if raw.get("error"):
        lines.append(f"[Erro na busca: {raw['error']}]")
    return "\n".join(lines) if lines else "Nenhum resultado encontrado."


def run(context: dict, provider) -> str:
    topic = context.get("topic", "")
    if not topic:
        return "**Erro:** Parâmetro `topic` é obrigatório."

    depth = context.get("depth", "quick")
    output_format = context.get("output_format", "summary")
    serper_key = context.get("serper_key", "") or os.environ.get("SERPER_API_KEY", "")

    bp = _load_best_practices()

    # --- Fase 1: FAST — busca e triagem ---
    queries = [topic]
    if depth == "deep":
        triage_prompt = (
            f"Gere 3 queries de busca distintas e complementares para pesquisar: {topic}\n"
            f"Retorne SOMENTE um array JSON: [\"query1\", \"query2\", \"query3\"]"
        )
        triage_result = provider.generate(triage_prompt, tier="fast")
        try:
            import re
            m = re.search(r'\[.*?\]', triage_result, re.DOTALL)
            if m:
                queries = json.loads(m.group()) + [topic]
        except Exception:
            pass

    all_results = {}
    for q in queries[:3]:
        raw = _serper_search(q, serper_key, num=10 if depth == "deep" else 6)
        all_results[q] = _format_serper_results(raw)

    results_block = "\n\n".join(
        f"**Query:** {q}\n{res}" for q, res in all_results.items()
    )

    # Triagem FAST: extrai trechos mais relevantes
    triage_prompt = (
        f"Analise estes resultados de busca sobre '{topic}' e extraia os trechos mais relevantes.\n\n"
        f"RESULTADOS:\n{results_block[:4000]}\n\n"
        f"Retorne SOMENTE os 5-7 trechos mais informativos, formatados como:\n"
        f"- [Fonte]: trecho relevante"
    )
    key_findings = provider.generate(triage_prompt, tier="fast")

    # --- Fase 2: POWERFUL — síntese estruturada ---
    if output_format == "bullets":
        format_spec = (
            "Retorne um markdown com bullets concisos:\n"
            "## Principais Achados\n- bullet\n## O que fazer\n- ação"
        )
    elif output_format == "report":
        format_spec = (
            "Retorne um relatório completo em markdown com:\n"
            "## Contexto\n## Fontes\n## Achados\n## Dados e Números\n## Conclusão Acionável"
        )
    else:
        format_spec = (
            "Retorne um resumo executivo em markdown:\n"
            "## Achados Principais\n## Dados Relevantes\n## Conclusão Acionável"
        )

    synthesis_prompt = (
        f"Sintetize a pesquisa sobre: **{topic}**{bp}\n\n"
        f"TRECHOS SELECIONADOS:\n{key_findings}\n\n"
        f"RESULTADOS COMPLETOS:\n{results_block[:3000]}\n\n"
        f"{format_spec}\n\n"
        f"Seja factual, cite dados quando disponíveis, e forneça insights acionáveis."
    )

    return provider.generate(synthesis_prompt, tier="powerful")
