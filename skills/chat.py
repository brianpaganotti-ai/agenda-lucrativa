"""
chat.py — Co-Piloto Executivo: skill conversacional de fallback.

Tier: FAST
Input:
  message       (str) — mensagem atual do usuário
  summary       (str) — resumo comprimido do histórico anterior
  recent        (list) — últimas trocas verbatim [{"role", "content"}]
  username      (str) — nome do usuário Telegram
  blocked_skill (str) — skill bloqueada pelo guard (para sugerir comando)

Output: resposta conversacional em markdown simples.
  Nunca chama outras skills internamente.
"""

DESCRIPTION = "Co-Piloto Executivo — responde, orienta e sugere próximos passos"
DEFAULT_TIER = "fast"


def run(context: dict, provider) -> str:
    message       = context.get("message", "")
    summary       = context.get("summary", "")
    recent        = context.get("recent", [])
    username      = context.get("username", "usuário")
    blocked_skill = context.get("blocked_skill", "")
    tier          = context.get("_tier", DEFAULT_TIER)

    recent_block  = "\n".join(
        f"[{m['role']}]: {m['content']}" for m in recent[-6:]
    )
    summary_block = f"CONTEXTO ANTERIOR:\n{summary}\n\n" if summary else ""

    blocked_hint = ""
    if blocked_skill:
        _command_map = {
            "frontend_design": "/design",
            "squad_runner":    "/run <squad>",
            "executing_plans": "/skill executing-plans plan_file=<arquivo>",
            "custom":          "/skill custom skill_file=<nome>",
        }
        cmd = _command_map.get(blocked_skill, f"/skill {blocked_skill}")
        blocked_hint = (
            f"\nNota: o usuário parece querer '{blocked_skill}'. "
            f"Mencione que pode fazer isso via `{cmd}` e ofereça orientação.\n"
        )

    prompt = (
        f"Você é Nexus, Co-Piloto Executivo de {username}.\n"
        f"Mentalidade: CEO estratégico com visão 360° sobre o negócio.\n"
        f"Estilo: direto, proativo, sem rodeios. Respostas curtas com alto valor.\n"
        f"Sempre termine com uma sugestão de próximo passo concreto ou pergunta estratégica.\n"
        f"{blocked_hint}\n"
        f"{summary_block}"
        f"CONVERSA RECENTE:\n{recent_block}\n\n"
        f"MENSAGEM ATUAL: {message}"
    )

    return provider.generate(prompt, tier=tier)
