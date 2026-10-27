# Model Router Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [x]`) syntax for tracking.

**Goal:** Entregar skill para selecionar modelo e esforço por tarefa no aplicativo Codex e CLI, com testes e operações presos ao modelo econômico.

**Architecture:** Skill conduz classificação e despacho nativo; utilitário Python valida política e contratos e oferece alternativa `codex exec`. Não existe API Python inventada para ferramentas internas do aplicativo. Trabalhadores recebem contexto mínimo e instrução de não delegar novamente.

**Tech Stack:** Python 3.11+, biblioteca padrão (`tomllib`, `json`, `subprocess`, `unittest`), Markdown, TOML e metadados YAML.

**Spec:** `docs/superpowers/specs/2026-10-24-model-router-design.md` — aprovado.

**Status:** concluído em 2026-10-24. Re-review final: conformidade PASS, qualidade PASS e entrega local pronta. Evidências em `docs/verification/model-router.md`; ledger em `.superpowers/sdd/model-router/progress.md`.

**Refinamento final:** tarefa mista com funcionalidade e testes novos usa quatro unidades sequenciais: autoria (somente escrita) -> execução RED -> implementação funcional -> execução GREEN. Autoria isolada não executa testes. Metadados JSON das tarefas abaixo registram roteamento e decisões da proposta original; são históricos e não substituem o escopo final documentado na skill e referência. Detalhes e limites em `docs/verification/model-router.md` e `.superpowers/sdd/model-router/final-rereview-report.md`.

## Global Constraints

- Python 3.11+ e biblioteca padrão; TOML para política; JSON para contratos e resultados.
- Mensagens e documentação em português, código com nomes claros em inglês.
- Criar testes, executar testes, lint, build, Docker e comandos operacionais: modelo econômico, esforço `low` por padrão.
- Primeira versão não faz escalada após falha.
- Não alterar sandbox, aprovações, autenticação ou permissões para fazer alternativa funcionar.
- Não editar arquivos de cache do plugin.
- Instalação/ativação global não faz parte desta proposta inicial.
- Todos os comandos shell começam com `rtk`; usar `rtk proxy` para programas sem suporte específico.
- Diretório atual não é repositório Git. Não criar worktree, commits ou push; entregar arquivos locais e registrar verificações.

## Review Focus

- Tarefa de testes rotulada complexa ou com modelo caro: `test_forced_economy` deve forçar perfil econômico ou rejeitar contrato conflitante antes de executar.
- Perfil econômico não ocupa menor posição no custo configurado: `test_invalid_cost_order` deve rejeitar política, sem adivinhar preços.
- Instruções com espaços, aspas, `$()` e quebras de linha: `test_prompt_is_stdin` deve preservá-las em stdin, nunca executar shell.
- Modelo indisponível, timeout ou subprocesso falha: `test_execution_failure` e `test_timeout` devem devolver erro e não iniciar nova tentativa cara.
- Cliente sem seleção explícita, tarefa mista e tentativa de recursão: cenários comportamentais devem optar por CLI, dividir tarefa e impedir redelegação, respectivamente.

## Estrutura e interfaces

- `model-router/SKILL.md`: classificação, validação, despacho nativo e limite da troca.
- `model-router/agents/openai.yaml`: nome, descrição e prompt de invocação, conforme skill-creator.
- `model-router/routing.toml`: perfis `economy`, `standard`, `strong`; lista `cost_order`; esforços permitidos e defaults.
- `model-router/scripts/router.py`: `load_policy(path: Path) -> dict`, `resolve_task(task: dict, policy: dict) -> dict`, `build_command(task: dict, cwd: Path) -> list[str]`, `execute_task(task: dict, cwd: Path, timeout: int) -> dict`, `main(argv: list[str] | None = None) -> int`.
- `model-router/references/integration.md`: contrato, integração Superpowers, instalação manual e restrições do aplicativo.
- `tests/test_router.py`: testes da política e executor, criados e executados no modelo econômico.
- `tests/skill-scenarios.md`: estímulos e rubrica para avaliação da skill em contexto novo.
- `docs/verification/model-router.md`: comandos, resultados e limites de cobertura real/simulada.
- `AGENTS.md`: RTK, comunicação concisa e ativação local da política, com escape para trabalhadores já roteados.
- `README.md`: instalação e comandos de uso deste projeto, sem duplicar referência interna da skill.

CLI pública: `router.py route --task PATH [--policy PATH]` imprime contrato resolvido; `router.py run --task PATH --cwd PATH [--policy PATH] [--timeout SEGUNDOS] [--dry-run]` executa alternativa CLI ou imprime comando e prompt sem iniciar modelo. Política padrão relativa ao script, independente do diretório atual. JSON de tarefa precisa ser objeto; rejeitar campos inválidos com mensagem útil e saída não zero.

Contrato de entrada: `id`, `task_type`, `complexity`, `instructions`, `acceptance_criteria`; `profile`, `model`, `reasoning_effort`, `reason` podem ser preenchidos pela resolução. Contrato explícito incompatível com política deve ser rejeitado. `complexity` aceita `low|medium|high`; tipos aceitos: `implementation|test_authoring|test_execution|operations|analysis|review`.

Política mapeia complexidade para perfil e esforço; tipos forçados ignoram complexidade para seleção e sempre usam `economy/low`. Ordem inicial editável: `gpt-6-luna`, `gpt-6.1-sol`, `gpt-6-astra`. Essa ordem é configuração aprovada, não prova de preços. Nunca alegar modelo confirmado se houver apenas parâmetros solicitados.

## Tarefa 1 — Testes de contrato e executor (RED)

```json
{"id":"router-tests-red","task_type":"test_authoring","complexity":"medium","profile":"economy","model":"gpt-6-luna","reasoning_effort":"low","reason":"Testes obrigatoriamente econômicos","instructions":"Criar testes unittest de política, contratos, CLI e subprocessos antes da implementação; não criar código de produção.","acceptance_criteria":["Cobertura dos casos listados abaixo","Registrar falha inicial por implementação ausente"]}
```

**Consome:** contratos e assinaturas definidos acima. **Produz:** `tests/test_router.py` e evidência RED. Autor pode inspecionar testes bons conforme skill TDD, sem delegar novamente.

- [x] Escrever testes `test_complexity_routes`: low/medium/high selecionam economy/standard/strong; esforços low/medium/high.
- [x] Escrever `test_forced_economy`: `test_authoring`, `test_execution`, `operations` sempre resolvem economy/low; campos explícitos caros são rejeitados.
- [x] Escrever testes de configuração e entrada inválidas: ordem de custo incoerente, perfis ausentes, TOML inválido, tipo/complexidade desconhecidos, JSON não objeto, instruções vazias e esforço não permitido.
- [x] Escrever `test_prompt_is_stdin`, `test_execution_failure`, `test_timeout`, erro de binário ausente e teste de dry-run sem subprocesso.
- [x] Asserções do comando: contém `codex exec --model MODEL`, `-c model_reasoning_effort=EFFORT`, diretório explícito; não contém flags de bypass; prompt via stdin; `shell` nunca verdadeiro.
- [x] Asserções do resultado: rota, mecanismo CLI, estado e saída; `model_verified` falso sem evidência efetiva; código não zero e timeout não viram sucesso.
- [x] Rodar `rtk proxy python3 -m unittest discover -s tests -v`. Registrar falha inicial esperada; não enfraquecer testes para passarem sem implementação.

## Tarefa 2 — Política e executor (GREEN)

```json
{"id":"router-implementation","task_type":"implementation","complexity":"medium","profile":"standard","model":"gpt-6.1-sol","reasoning_effort":"medium","reason":"Validação de contratos e ciclo de subprocessos","instructions":"Implementar routing.toml e scripts/router.py conforme interfaces e testes existentes; não editar nem executar testes.","acceptance_criteria":["Validação determinística","CLI route/run/dry-run funcional","Sem shell intermediário, escalada ou bypass"]}
```

**Consome:** testes e contratos da tarefa 1. **Produz:** política e utilitário nas interfaces acima.

- [x] Criar `routing.toml` com perfis, esforços e ordem de custo; não tocar configuração global.
- [x] Implementar leitura/validação e resolução dos contratos. Não classificar linguagem natural por regex; agente inspeciona conteúdo real e utilitário valida estrutura.
- [x] Implementar comando via lista, prompt stdin com marcador de trabalhador, timeout, erro de processo e relatório JSON; não inventar verificação do modelo.
- [x] Implementar `route`, `run`, `--dry-run`, erros úteis e caminhos independentes do diretório de invocação.

## Tarefa 3 — Execução dos testes (GREEN/REFACTOR)

```json
{"id":"router-tests-green","task_type":"test_execution","complexity":"medium","profile":"economy","model":"gpt-6-luna","reasoning_effort":"low","reason":"Execução de testes obrigatoriamente econômica","instructions":"Rodar suíte e smoke tests sem chamadas reais pagas; registrar resultados e falhas; não corrigir código de produção.","acceptance_criteria":["Suíte unittest passa","route e run --dry-run funcionam","Nenhum codex exec real disparado"]}
```

**Consome:** tarefas 1 e 2. **Produz:** evidência em `docs/verification/model-router.md`.

- [x] Rodar suíte completa `rtk proxy python3 -m unittest discover -s tests -v`.
- [x] Rodar CLI com contrato temporário para `route` e `run --dry-run`; confirmar parâmetros sem iniciar modelo.
- [x] Se houver falha, retornar causa ao implementador; correção de produção preserva rota da tarefa 2. Autoria de teste adicional e novas execuções permanecem econômicas. Repetir somente após mudança ou falha concreta.

## Tarefa 4 — Skill, integração e inicialização

```json
{"id":"router-skill","task_type":"implementation","complexity":"low","profile":"economy","model":"gpt-6-luna","reasoning_effort":"low","reason":"Documentação e configuração delimitadas pelo desenho","instructions":"Criar SKILL.md, metadados, referência, README e AGENTS locais; não editar plugin nem instalar globalmente; não executar validações.","acceptance_criteria":["Integração Superpowers descrita","Trabalhador não roteia nem delega novamente","Instalação comum para aplicativo e CLI documentada"]}
```

**Consome:** contrato, utilitário e desenho. **Produz:** demais arquivos da estrutura.

- [x] Skill curta: carregar política; separar tarefas mistas; anotar plano; usar parâmetros do schema nativo ou papel fixado; alternativa CLI se seleção indisponível; parar se nenhuma via disponível.
- [x] Incluir escape de trabalhador antes de ativação automática. Preservar sequência TDD e não criar paralelo com escritas compartilhadas.
- [x] Referência inclui exemplo completo JSON, argumentos de uso, trecho AGENTS reutilizável e instalação sem sobrescrever `~/.agents/skills/model-router` existente.
- [x] Documentar que agente principal não muda e que testes/Docker são processos do sistema coordenados pelo modelo selecionado.
- [x] Criar metadados com ferramenta skill-creator conforme referência `openai_yaml.md`; sem dependências inventadas.
- [x] AGENTS local preserva instruções RTK e modo conciso; README aponta instalação e limites de validação.

## Tarefa 5 — Validação comportamental e revisão final

```json
{"id":"router-skill-validation","task_type":"test_execution","complexity":"medium","profile":"economy","model":"gpt-6-luna","reasoning_effort":"low","reason":"Avaliação e execução de validações obrigatoriamente econômicas","instructions":"Validar skill e cenários em contexto novo com e sem orientação; registrar evidência real e simulada sem afirmar teste do aplicativo real.","acceptance_criteria":["Metadados válidos","Cenários sem herança silenciosa ou recursão","Resultados e limites documentados"]}
```

**Consome:** skill completa. **Produz:** cenários e relatório final. Controlador pode despachar avaliações frescas; trabalhadores não criam seus próprios agentes.

- [x] Criar cenários: tarefa simples; tarefa complexa; teste complexo; Docker; tarefa mista; cliente sem seleção; tentativa de redelegação. Para orientação comportamental, usar controle sem skill e repetições exigidas por writing-skills; avaliar decisões, sem executar ações do cenário.
- [x] Validar skill com `quick_validate.py` do skill-creator, se disponível; registrar dependências/limitações, sem alegar validação executada caso ferramenta ausente.
- [x] Revisar implementação contra desenho e resultados. Corrigir problemas materiais respeitando rotas; reexecutar testes somente quando mudanças justificarem.
- [x] Atualizar checkboxes deste plano e relatório com contagem real de testes e cobertura dos caminhos CLI/nativo; entregar links de uso e instalação. Não declarar instalação global ou aplicativo validado sem evidência.

## Execução proposta

Subagentes sequenciais com contexto novo e modelos explícitos acima. Autoria/execução de testes e operações sempre no modelo econômico; implementação central no modelo padrão. Revisões curtas por tarefa e revisão final, sem revisores duplicados. Aprovação deste plano permite seguir até entrega local, sem nova confirmação para etapas já descritas.

## Autor revisão do plano

Cobertura: política/contratos na tarefa 2; executor na tarefa 2; casos de erro nas tarefas 1 e 3; integração/instalação local documentada na tarefa 4; caminhos nativo/alternativo e limites na tarefa 5. Interfaces usam mesmos nomes em todas as tarefas. Testes e operações possuem rotas próprias econômicas. Sem escalada, instalação global ou alterações no plugin.
