# Agent Operating Model

Este arquivo define a coordenação entre agentes sem alterar o runtime atual do bot.

## Princípios
- O branch e o código atual em operação continuam sendo a fonte de verdade técnica.
- Mudanças de arquitetura e processo devem nascer em PR separado.
- GitHub é a memória compartilhada entre sessões e agentes.
- O dashboard mostra estado; os arquivos explicam intenção, contexto e handoff.

## Camadas
1. **Código operacional**
   - Bot, skills, deploy e runtime.
2. **Memória compartilhada**
   - `ESTADO.md`, `ROADMAP.md`, `ops/AGENT_HANDOFF.md`, `ops/TASK_CURRENT.md`.
3. **Playbooks por agente**
   - `agents/claude/CLAUDE.md`
   - `agents/gemini/AGENT_GUIDE.md`
   - `agents/gpt/GPT_PLAYBOOK.md`
4. **Dashboard**
   - GitHub Project com campos padronizados.
5. **Laboratório**
   - Repo privado separado para testes de agentes, hooks, prompts e rotinas.

## Regras
- Não usar um único arquivo para roadmap, histórico, instrução e tarefa atual ao mesmo tempo.
- Cada sessão deve começar lendo apenas os arquivos do escopo.
- Qualquer agente deve propor diff mínimo antes de implementar.
- PRs de processo/documentação não devem alterar comportamento do bot.

## Fluxo sugerido
1. Atualizar `ops/TASK_CURRENT.md` com a tarefa ativa.
2. Atualizar `ops/AGENT_HANDOFF.md` com contexto curto entre sessões.
3. Usar o dashboard para status, risco, owner e próximo check.
4. Implementar em branch separado.
5. Revisar e consolidar em PR próprio.
