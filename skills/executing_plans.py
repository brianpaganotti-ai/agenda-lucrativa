"""
executing_plans.py — Executa planos markdown etapa por etapa.

Tier: POWERFUL (execução) + FAST (checkpoints)
Input:
  plan_file     (str, obrigatório) — caminho do arquivo .md do plano
                  (relativo a nexus_dir/tmp/ ou absoluto)
  nexus_dir     (str, default "/opt/nexus") — diretório base do nexus
  start_step    (int, default 1) — etapa inicial
  auto_continue (bool, default False) — executa sem parar para checkpoints

Output: markdown com resumo das etapas executadas e estado final
  Persiste estado em ESTADO.md após cada etapa
"""

import json
import re
from pathlib import Path

DESCRIPTION = "Executa planos markdown passo a passo com checkpoints e persistência de estado"
DEFAULT_TIER = "powerful"

_STATE_FILE = "ESTADO.md"


def _parse_steps(plan_text: str) -> list[dict]:
    """Extrai etapas numeradas de um plano markdown."""
    steps = []
    # Padrão: "1. **Nome** ..." ou "## Etapas\n1. ..."
    # Extrai blocos numerados dentro da seção Etapas
    etapas_match = re.search(r'## Etapas\n(.*?)(?=\n## |\Z)', plan_text, re.DOTALL)
    section = etapas_match.group(1) if etapas_match else plan_text

    pattern = re.compile(r'(\d+)\.\s+\*\*([^*]+)\*\*([^\n]*(?:\n(?!\d+\.).*)*)', re.MULTILINE)
    for m in pattern.finditer(section):
        num = int(m.group(1))
        title = m.group(2).strip()
        body = (m.group(2) + m.group(3)).strip()
        steps.append({"num": num, "title": title, "body": body})

    if not steps:
        # Fallback: linhas iniciando com dígito
        for line in section.splitlines():
            m = re.match(r'(\d+)\.\s+(.+)', line.strip())
            if m:
                steps.append({"num": int(m.group(1)), "title": m.group(2)[:60], "body": m.group(2)})

    return sorted(steps, key=lambda s: s["num"])


def _load_state(state_path: Path) -> dict:
    if not state_path.exists():
        return {"completed_steps": [], "results": {}}
    try:
        text = state_path.read_text(encoding="utf-8")
        m = re.search(r'```json\n(.*?)\n```', text, re.DOTALL)
        if m:
            return json.loads(m.group(1))
    except Exception:
        pass
    return {"completed_steps": [], "results": {}}


def _save_state(state_path: Path, state: dict) -> None:
    content = (
        "# Estado de Execução do Plano\n\n"
        "```json\n"
        f"{json.dumps(state, ensure_ascii=False, indent=2)}\n"
        "```\n"
    )
    state_path.write_text(content, encoding="utf-8")


def run(context: dict, provider) -> str:
    plan_file = context.get("plan_file", "")
    if not plan_file:
        return "**Erro:** Parâmetro `plan_file` é obrigatório."

    nexus_dir = Path(context.get("nexus_dir", "/opt/nexus"))
    start_step = int(context.get("start_step", 1))
    auto_continue = str(context.get("auto_continue", "false")).lower() in ("true", "1", "yes")
    tier = context.get("_tier", DEFAULT_TIER)

    # Resolve caminho do plano
    plan_path = Path(plan_file)
    if not plan_path.is_absolute():
        plan_path = nexus_dir / "tmp" / plan_file

    if not plan_path.exists():
        return f"**Erro:** Arquivo de plano não encontrado: `{plan_path}`"

    plan_text = plan_path.read_text(encoding="utf-8")
    steps = _parse_steps(plan_text)

    if not steps:
        return "**Erro:** Nenhuma etapa encontrada no plano. Verifique o formato do arquivo."

    state_path = nexus_dir / "tmp" / _STATE_FILE
    state = _load_state(state_path)

    results = []
    executed = 0

    for step in steps:
        if step["num"] < start_step:
            continue
        if step["num"] in state["completed_steps"]:
            results.append(f"**Etapa {step['num']}** ({step['title']}): ✅ já concluída (estado anterior)")
            continue

        # Executa a etapa com tier POWERFUL
        exec_prompt = (
            f"Você está executando uma etapa de um plano estratégico.\n\n"
            f"PLANO COMPLETO:\n{plan_text[:3000]}\n\n"
            f"ETAPA ATUAL — {step['num']}. {step['title']}:\n{step['body']}\n\n"
            f"Etapas já concluídas: {state['completed_steps']}\n\n"
            f"Execute esta etapa:\n"
            f"1. Analise o contexto e as etapas anteriores\n"
            f"2. Produza o resultado concreto desta etapa\n"
            f"3. Finalize com '**Resultado:** [resumo em 1 frase]'"
        )

        result = provider.generate(exec_prompt, tier=tier)

        # Checkpoint: se não auto_continue, gera pergunta de continuação (FAST)
        if not auto_continue and executed > 0:
            checkpoint_prompt = (
                f"Etapa {step['num']} ({step['title']}) concluída.\n\n"
                f"Resultado: {result[:300]}\n\n"
                f"Formule uma pergunta curta e direta perguntando ao usuário se deve continuar "
                f"para a próxima etapa. Máximo 1 linha."
            )
            question = provider.generate(checkpoint_prompt, tier="fast")
            result += f"\n\n---\n⏸️ **Checkpoint:** {question}"

        state["completed_steps"].append(step["num"])
        state["results"][str(step["num"])] = result[:500]
        _save_state(state_path, state)

        results.append(f"**Etapa {step['num']}** — {step['title']}\n{result}")
        executed += 1

        if not auto_continue:
            break  # Para após primeira etapa não concluída, aguarda confirmação

    summary = f"# Execução do Plano\n\n"
    summary += f"Etapas concluídas: {state['completed_steps']}\n\n"
    summary += "\n\n---\n\n".join(results) if results else "Nenhuma etapa executada."

    if executed == 0 and all(s["num"] in state["completed_steps"] for s in steps):
        summary += "\n\n🏁 **Plano concluído!** Todas as etapas foram executadas."

    return summary
