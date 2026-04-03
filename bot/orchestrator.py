"""
orchestrator.py — Roteamento de intenção via Gemini (FAST tier).

Recebe mensagem em linguagem natural → decide qual skill chamar + extrai parâmetros.
Usado pelo comando /ask no Telegram.
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
]

_SKILL_DESCRIPTIONS = """
brainstorm: gerar ideias, criatividade, lista de ideias, sugestões, opções, alternativas
write_plan: criar plano, planejamento estratégico, estratégia, roteiro, campanha, cronograma
executing_plans: executar plano, continuar plano, próxima etapa, rodar etapa do plano
autoresearch: pesquisar, pesquisa, buscar informação, investigar, o que é, como funciona
frontend_design: criar imagem, design visual, post, story, carrossel, layout, instagram, arte
squad_runner: rodar squad, prospecção, executar pipeline, disparar squad
custom: skill personalizada, usar skill yaml, skill customizada
""".strip()


class Orchestrator:
    """
    Classifica intenção e extrai parâmetros usando o tier FAST.
    Retorna {"skill": str, "params": dict, "explanation": str}.
    """

    def __init__(self, provider):
        self._provider = provider

    def route(self, message: str) -> dict:
        prompt = (
            f"Você é um roteador de intenções para um assistente de negócios.\n\n"
            f"SKILLS DISPONÍVEIS:\n{_SKILL_DESCRIPTIONS}\n\n"
            f"MENSAGEM DO USUÁRIO: {message}\n\n"
            f"Sua tarefa:\n"
            f"1. Identifique qual skill melhor atende a intenção\n"
            f"2. Extraia os parâmetros relevantes da mensagem\n\n"
            f"Parâmetros por skill:\n"
            f"- brainstorm: topic (obrigatório), quantity (padrão 10), context\n"
            f"- write_plan: goal (obrigatório), audience (obrigatório), constraints, timeframe\n"
            f"- executing_plans: plan_file (obrigatório), start_step, auto_continue\n"
            f"- autoresearch: topic (obrigatório), depth (quick/deep), output_format (summary/report/bullets)\n"
            f"- frontend_design: format (instagram_post/story/instagram_carousel), theme, content (obrigatório), brand_colors\n"
            f"- squad_runner: squad_name (obrigatório), variables\n"
            f"- custom: skill_file (obrigatório), variables\n\n"
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

        logger.warning("Orchestrator: JSON inválido, usando brainstorm como fallback. Raw: %s", raw[:200])
        return {
            "skill": "brainstorm",
            "params": {"topic": message},
            "explanation": "fallback — não foi possível classificar a intenção",
        }
