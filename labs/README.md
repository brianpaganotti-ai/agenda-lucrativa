# Labs / Private Test Repo Blueprint

Este diretório documenta a estratégia de laboratório.

## Objetivo
Manter testes de agentes, prompts, hooks, dashboards e rotinas fora do repositório operacional.

## Recomendação
Criar um repositório privado separado para:
- testes de playbooks por agente
- experimentos com hooks e comandos
- protótipos de dashboard e handoff
- validação de fluxos antes de levar ao repo principal

## Regra
Nada experimental deve entrar no runtime operacional sem passar por PR específico.
