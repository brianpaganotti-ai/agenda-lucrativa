"""
orchestrator.py — Roteamento de intenção via Gemini (FAST tier).

Recebe mensagem em linguagem natural → decide qual skill chamar + extrai parâmetros.
Usado pelo comando /ask e pelo handler de texto livre cmd_chat() no Telegram.
"""

import json
import logging
import re

logger = logging.getLogger(__name__)

SKILLS = [
    "brainstorm",
    "write_plan",
    "executing_plans",
    "autoresearch",
    "frontend_design",
    "squad_runner",
    "custom",
    "chat",
]

_SKILL_DESCRIPTIONS = """
brainstorm: gerar ideias, criatividade, lista de ideias, sugestões, opções, alternativas
write_plan: criar plano, planejamento estratégico, estratégia, roteiro, campanha, cronograma
executing_plans: executar plano, continuar plano, próxima etapa, rodar etapa do plano
autoresearch: pesquisar, pesquisa, buscar informação, investigar, o que é, como funciona
frontend_design: criar imagem, design visual, post, story, carrossel, layout, instagram, arte
squad_runner: rodar squad, prospecção, executar pipeline, disparar squad
custom: skill personalizada, usar skill yaml, skill customizada
chat: conversa geral, saudações, follow-up contextual, orientação rápida, perguntas sobre o sistema — quando nenhuma outra skill se aplica
""".strip()


class Orchestrator:
    """
    Classifica intenção e extrai parâmetros usando o tier FAST.
    Retorna {"skill": str, "params": dict, "explanation": str}.
    """

    def __init__(self, provider):
        self._provider = provider

    def route(self, message: str, summary: str = "", recent: list | None = None) -> dict:
        recent = recent or []

        context_block = ""
        if summary or recent:
            recent_text = "\n".join(
                f"[{m['role']}]: {m['content'][:200]}" for m in recent[-4:]
            )
            context_block = (
                f"\nCONTEXTO DA CONVERSA:\n"
                f"Resumo anterior: {summary or '(nenhum)'}\n"
                f"Mensagens recentes:\n{recent_text}\n"
            )

        prompt = (
            f"Você é um roteador de intenções para um assistente de negócios.\n\n"
            f"SKILLS DISPONÍVEIS:\n{_SKILL_DESCRIPTIONS}\n\n"
            f"MENSAGEM DO USUÁRIO: {message}"
            f"{context_block}\n\n"
            f"Sua tarefa:\n"
            f"1. Considere o contexto da conversa para entender a intenção real\n"
            f"2. Identifique qual skill melhor atende\n"
            f"3. Se for follow-up de uma conversa anterior ou pergunta simples → use chat\n"
            f"4. Extraia os parâmetros relevantes\n\n"
            f"Parâmetros por skill:\n"
            f"- brainstorm: topic (obrigatório), quantity (padrão 10), context\n"
            f"- write_plan: goal (obrigatório), audience, constraints, timeframe\n"
            f"- executing_plans: plan_file (obrigatório), start_step, auto_continue\n"
            f"- autoresearch: topic (obrigatório), depth (quick/deep), output_format (summary/report/bullets)\n"
            f"- frontend_design: format (instagram_post/story/instagram_carousel), theme, content (obrigatório), brand_colors\n"
            f"- squad_runner: squad_name (obrigatório), variables\n"
            f"- custom: skill_file (obrigatório), variables\n"
            f"- chat: message (a mensagem do usuário)\n\n"
            f"Retorne SOMENTE JSON válido, sem markdown:\n"
            f'{{"skill": "nome_da_skill", "params": {{"chave": "valor"}}, "explanation": "motivo"}}\n\n'
            f"skill deve ser exatamente um de: {', '.join(SKILLS)}"
        )

        raw = self._provider.generate(prompt, tier="fast")

        try:
            m = re.search(r'\{[\s\S]*\}', raw)
            if m:
                data = json.loads(m.group())
                if data.get("skill") in SKILLS and isinstance(data.get("params"), dict):
                    return data
        except (json.JSONDecodeError, KeyError, TypeError):
            pass

        logger.warning("Orchestrator: JSON inválido, usando chat como fallback. Raw: %s", raw[:200])
        return {
            "skill": "chat",
            "params": {"message": message},
            "explanation": "fallback — não foi possível classificar a intenção",
        }
