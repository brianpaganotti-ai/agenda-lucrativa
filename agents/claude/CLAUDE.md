# Claude Agent Playbook

## Papel
Claude é usado para implementação guiada, refatoração localizada e execução com diff mínimo.

## Regras
- Ler antes de escrever.
- Não repropor fases já concluídas.
- Trabalhar apenas no escopo descrito em `ops/TASK_CURRENT.md`.
- Preservar `/run` e o runtime operacional atual, salvo instrução explícita.
- Evitar refatorações amplas.

## Protocolo
1. Ler os arquivos do escopo.
2. Resumir o que existe vs o que falta.
3. Propor mudança mínima.
4. Implementar.
5. Validar.
6. Atualizar `ops/AGENT_HANDOFF.md`.
