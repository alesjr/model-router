# Telemetry Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development. Steps use checkbox (`- [ ]`) syntax for tracking. Contracts below separate authorship, execution and implementation; workers never delegate.

**Goal:** Entregar monitor local de agentes nativos e router, com histórico diário de sessões/modelos/tokens sem duplicação.

**Architecture:** Coletor incremental de rollouts, SQLite transacional como fonte canônica e exportações JSONL/JSON versionadas por data. CLI de observação e integração opcional ao executor preservam contratos existentes.

**Tech Stack:** Python 3.11+, biblioteca padrão, SQLite, unittest, JSONL, zoneinfo.

**Spec:** `docs/superpowers/specs/2026-10-26-telemetry-design.md`, aprovado pelo usuário em 2026-10-26.

**Status:** implementação e revisão focal concluídas em 2026-10-26; 51/51 testes e smoke instalado aprovados. Migração/coleta real preservaram respostas sem duplicação. Evidências em `docs/verification/telemetry.md`.

## Global Constraints

- Cobrir agentes nativos do Codex e processos iniciados pelo router no projeto.
- Arquivos de origem somente leitura; sem prompts, respostas, comandos ou stdout/stderr nos registros de telemetria.
- Identidade de agente por thread; sessão raiz pode ser compartilhada. Resposta única por `(thread_id, response_id)`.
- Cache/raciocínio são parcelas, não somas adicionais. Campos ausentes permanecem desconhecidos.
- Fontes sobrepostas nunca são somadas; fallback ambíguo fica não aditivo e explicitamente parcial.
- UTC padrão; zona IANA configurável. Metadados de modelo não comprovam backend efetivo.
- Nenhuma chamada real de modelo, mudança global, plugin, permissões ou bypass Git nesta implementação.
- Todos os comandos shell neste workspace começam com `rtk`.
- Diretório sem Git: entregar arquivos locais, sem criar Git, worktree ou commits.
- Usuário autorizou implementação do desenho. Delegação sequencial segue AGENTS; não há nova escolha de método a solicitar.

## Review Focus

1. Pai e filhos com `session_id` compartilhado: testar threads distintas e agregação única por resposta.
2. Reuso, cópia, rotação e retomada: testar persistência de dedup e recuperação de linhas parciais.
3. Mistura de fontes/modelos e counters cumulativos: testar subtotal conhecido, sem dupla contagem nem atribuição arbitrária.
4. Concorrência e publicação interrompida: testar banco sem duplicação e manifesto que referencia somente geração completa.
5. Integração do subprocesso: testar stdout JSON único, timeout e falha de telemetria sem mudar resultado/permissões.

## Interfaces e arquivos

- `model-router/scripts/telemetry_core.py`: `Telemetry(project: Path, sources: list[Path] | None = None, logs_dir: Path | None = None, timezone: str = "UTC")`; `sync() -> dict`; `status(session_id: str | None = None, stale_after: float = 300, now: datetime | None = None) -> dict`; `summary(date: str, session_id: str | None = None) -> dict`; `close() -> None`; `record_invocation(invocation: dict) -> None` aceita somente whitelist de metadados. A implementação consolidou início/atualização no único método usado pelo wrapper, sem persistir objeto de tarefa/prompts.
- Helpers adjacentes de parser/store/export podem ser extraídos se reduzirem responsabilidades; interface pública acima permanece estável.
- `model-router/scripts/telemetry.py`: `main(argv: list[str] | None = None) -> int`, CLI `sync`, `status`, `watch`, `summary`; opções do desenho após subcomando; `--json` para consultas, `--date` obrigatório em summary. `watch` sai limpo com Ctrl-C.
- `model-router/scripts/router.py`: preservar `execute_task(task, cwd, timeout)` e comportamento padrão; adicionar argumentos opcionais keyword-only `telemetry: bool = False`, `telemetry_project: Path | None = None`. A modalidade ativa pode usar helper `router_telemetry.py` com `Popen`, leitura simultânea de stderr/stdout e timeout. Importação opcional deve funcionar em execução por caminho instalado e carregamento por importlib nos testes.
- `tests/test_telemetry.py`, `tests/test_router_telemetry.py`: unittest com fixtures sintéticas em TemporaryDirectory; sem ler logs reais nem chamar modelos.
- README, SKILL, integração e AGENTS: uso portátil; não inserir paths pessoais nem substituir documentação existente.

### Formatos observáveis para testes

`sync` retorna pelo menos `revision`, `diagnostics`. `status` retorna `schema_version=1`, `revision`, `agents` e `counts`. Cada agente contém `thread_id`, `session_id`, `parent_thread_id`, `origin`, `role`, `status`, `turn_id`, `model_reported`, `model_requested`, esforço solicitado/reportado, `model_verified=false`, `last_seen`, `task_id` e `invocation_id` (null quando indisponível). `counts` contém agentes, trabalhadores, coordenadores, execução observada e modelos conhecidos distintos. Nomes de counts: `agents`, `workers`, `coordinators`, `observed_running`, `models`.

`summary` retorna `date`, `timezone`, `revision`, `totals`, `by_model`, `by_session`, `coverage` e `responses`. `totals` e cada entrada de `by_model` contêm as seis dimensões de usage; desconhecidos não viram zero. `by_session[id]` contém `totals` e `by_model`. Bucket de modelo desconhecido chama-se `unknown`. `coverage` expõe `complete` e diagnósticos/baselines não aditivos. Estado vazio significa sem dados conhecidos, não afirma gasto zero completo. Schema adicional pode enriquecer esses objetos sem quebrar o contrato.

Exportação diária: `logs/YYYY-MM-DD/current.json` referencia `events` e `summary` por caminhos relativos da geração `revisions/<revision>/`; manifesto contém `revision`, `timezone`, `schema_version`. JSONL exporta apenas metadados normalizados; resumo e eventos identificam a revisão. `sync` deve reconstruir publicação pendente, sem depender da chegada de novos eventos.

## Contratos resolvidos e tarefas

### 1. Autoria de testes (somente escrita)

```json
{"id":"telemetry-tests","task_type":"test_authoring","complexity":"medium","profile":"economy","model":"gpt-6-luna","reasoning_effort":"low","reason":"Autoria de testes obrigatoriamente econômica","instructions":"Escrever testes unittest sintéticos de coletor/CLI/router conforme interfaces e critérios; não executar nem alterar produção.","acceptance_criteria":["Casos de identidade/dedup/modelos/fontes/exportação e integração cobertos","Testes legados preservados","Nenhum teste/modelo executado"]}
```

- [x] Criar helpers de eventos sintéticos: session_meta root/child, turn_context, token_usage_record, token_count, task_started/complete, duas datas.
- [x] Testar request usage único, cache sem dupla soma, thread compartilhando root session, reuso em outro turno/modelo e novo objeto Telemetry.
- [x] Testar fallback baseline/deltas não aditivos, backfill, reset, modelo ausente/conflitante, dados ausentes e timestamp inválido.
- [x] Testar linha parcial, metadados tardios, arquivo arquivado/rotacionado/truncado, projeto irmão excluído e filho fora cwd incluído pelo vínculo.
- [x] Testar manifesto consistente/reconstrução e concorrência de coletores; nenhuma conversa-canário exportada.
- [x] Testar CLI isolada, --session sem remover outras sessões, stale/reuso completed e instalação copiando helpers.
- [x] Testar router ativo com binário fake local (sem modelo), eventos JSON, ligação de task/thread, stdout único, erros, timeout e sem telemetria em dry-run.

### 2. Execução RED (somente execução)

```json
{"id":"telemetry-red","task_type":"test_execution","complexity":"low","profile":"economy","model":"gpt-6-luna","reasoning_effort":"low","reason":"Execução de testes sempre econômica","instructions":"Executar unittest e identificar RED por módulo/funcionalidade de telemetria ausente; não alterar arquivos.","acceptance_criteria":["Falha esperada da funcionalidade ausente registrada","16 testes legados continuam passando"]}
```

- [x] Rodar `rtk proxy python3 -m unittest discover -s tests -v`; registrar RED esperado, sem pedir falha artificial dos legados.

### 3. Implementação funcional (sem testes ou operações)

```json
{"id":"telemetry-implementation","task_type":"implementation","complexity":"medium","profile":"standard","model":"gpt-6.1-sol","reasoning_effort":"medium","reason":"Interfaces e algoritmos definidos; integração entre coletor transacional, CLI e executor","instructions":"Implementar módulos de telemetria e integração router conforme desenho/interfaces/testes, sem escrever ou executar testes/operações.","acceptance_criteria":["Coletor/CLI/exportação e integração funcionais conforme contrato","Dedup/reconciliação e escopo preservados","Router padrão compatível e permissões preservadas"]}
```

- [x] Implementar armazenamento versionado, leitura incremental whitelist, seleção por projeto/árvore, associações e reconciliação conservadora.
- [x] Implementar summaries e exportações atômicas por manifesto, com recuperação e dados não alocados separados.
- [x] Implementar CLI e painel observacional com todos os comandos/opções do desenho.
- [x] Implementar router `--telemetry` e `--telemetry-project`; preservar JSON público e resultado sob falhas de telemetria.

### 4. Execução GREEN (somente execução)

```json
{"id":"telemetry-green","task_type":"test_execution","complexity":"low","profile":"economy","model":"gpt-6-luna","reasoning_effort":"low","reason":"Execução de testes sempre econômica","instructions":"Executar suíte completa e smoke instalado em temporários sem modelos reais; reportar falhas, sem editar.","acceptance_criteria":["Suíte completa passa","CLI instalado gera manifesto e resumo verificáveis","Nenhuma chamada de modelo ou alteração global"]}
```

- [x] Rodar unittest completo; verificar testes com dados e binário sintéticos.
- [x] Instalar em destino temporário e executar help/sync/status/summary; confirmar helpers e caminhos com espaços.
- [x] Se houver falha, devolver ao coordenador; correções seguem escopos separados e nova execução econômica.

### 5. Documentação e revisão

```json
{"id":"telemetry-docs","task_type":"implementation","complexity":"low","profile":"economy","model":"gpt-6-luna","reasoning_effort":"low","reason":"Documentação delimitada pelo comportamento implementado","instructions":"Documentar comandos e formatos reais no README/integração/skill, sem executar testes ou mudar política.","acceptance_criteria":["Uso instalado e desenvolvimento copiáveis","Limites de contagem/estado/modelo explícitos","Sem dependências pessoais"]}
```

- [x] Documentar monitor em terminal separado, saídas por data/modelo/sessão, manifesto e timezone, reuso e fontes parciais.
- [x] Revisar implementação integral contra desenho, com contrato `review` separado e modelo explícito conforme complexidade.
- [x] Registrar evidências em `docs/verification/telemetry.md`; marcar progresso real neste plano.

## Execução e decisões

Ruling: execução sequencial por subagentes já determinada por AGENTS; aprovação do desenho autoriza implementação e verificações locais, sem nova escolha de método — custo de erro: retrabalho local reversível.
Ruling: SQLite/manifesto protegem dedup/publicação; dados desconhecidos e fallback não correlacionável continuam explícitos — custo de erro: totais incompletos declarados, sem inventar precisão.
Ruling: sem Git/worktree no diretório de origem, preservam-se arquivos locais e evidências; não inicializar repositório para satisfazer ferramentas auxiliares.

Plano auto-revisado contra as seções do desenho: fontes, projeto, identidades, tokens, exportação, CLI, integração, privacidade e entrega cobertas pelas tarefas acima. Atualizações de progresso serão registradas em `.superpowers/sdd/telemetry/progress.md`.
