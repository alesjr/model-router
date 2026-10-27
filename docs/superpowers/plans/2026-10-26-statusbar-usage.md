# Statusbar and Local Usage Implementation Plan

> **For agentic workers:** Use superpowers:subagent-driven-development. Workers receive separate authored-test, execution, production and operations contracts and never delegate.

**Goal:** Entregar launcher com barra de agentes, dashboard HTML local baseado em JSON e desativação efetiva da telemetria.
**Architecture:** Configuração JSON compartilhada bloqueia coleta antes de efeitos; dashboard lê somente manifestos/resumos JSON; launcher Python supervisiona Codex em tmux próprio e reutiliza coletor existente. Instalador pessoal ativa Bash com backup.
**Tech Stack:** Python3.11+ stdlib, HTML/CSS/JavaScript sem frameworks, tmux para barra apenas.
**Spec:** `docs/superpowers/specs/2026-10-26-statusbar-launcher-design.md`, aprovado em 2026-10-26.

## Global Constraints

- Nenhum framework, serviço web, CDN, conta externa ou inferência durante validação. Dashboard é HTML autocontido local.
- Dashboard só lê JSON publicado; não importa coletor/SQLite nem soma revisões ou JSONL novamente.
- Sem configuração, telemetria desligada. `false` explícito global ou projeto veta coleta; sem veto, `true` explícito habilita. JSON inválido bloqueia coleta.
- Desativação impede leitura de sessões, criação do banco e escrita de eventos, inclusive diante de `--telemetry`. Histórico continua legível pelo dashboard.
- Global: `$XDG_CONFIG_HOME/model-router/settings.json`, fallback `~/.config/model-router/settings.json`. Projeto: `<project>/.model-router.json`.
- Ativação pessoal/Bash/tmux autorizada pelo usuário; não alterar pacote npm, auth, sandbox ou TUI nativa. Não fechar sessão Codex atual.
- Comandos shell sempre `rtk`; diretório sem Git, sem commits/worktree. Preservar relatórios anteriores.
- Testes/operações economy/low; implementação standard/medium, revisão roteada separadamente. Execução já autorizada; sem nova escolha de método.

## Review Focus

1. Kill switch global/projeto inclusive objeto já aberto: testar default off, veto sobre flag, parada watch/record e ausência de efeitos.
2. Manifestos e XSS: testar revisões antigas ignoradas, caminho fora logs recusado, null/cache e texto malicioso escapado.
3. Wrapper: testar passthrough argv/stdin/exit em non-TTY, subcomandos, nested e disabled; nem criar tmux nessas vias.
4. tmux/PTY: preservar Ctrl-C, resize, status realista e limpar coletor; validar com Codex fake em tmux isolado.
5. Instalador: backup/idempotência/remoção e conflito Bash sem sobrescrever funções/aliases existentes; pacote portátil com espaços.

## Interfaces decididas

### Configuração (`model-router/scripts/telemetry_settings.py`)

- `get_settings(project: Path) -> dict`: retorna `enabled: bool`, `reason: str`, caminhos/proveniência seguros; não cria nada. Valores ausentes não habilitam. Vetos explícitos têm precedência. Arquivo malformado => enabled false com reason claro.
- `set_telemetry(enabled: bool, project: Path | None = None, scope: str = "global") -> Path`: escrita atômica preserva chaves extras; JSON existente inválido não é sobrescrito silenciosamente.
- `TelemetryDisabled(RuntimeError)` e `require_enabled(project: Path) -> None`: erro claro antes de qualquer efeito de coleta.
- Core verifica no construtor ANTES mkdir/connect e nas entradas de escrita `sync`, `record_invocation`, `regenerate_timezone`. Objeto já aberto não continua gravando após veto.
- CLI telemetria inclui `config --project PATH --scope global|project --enabled true|false`. Comandos de coleta bloqueados retornam mensagem/JSON de desativação sem erro de worker; não abrem banco.
- `router.execute_task(... telemetry=True)` com veto usa execução padrão do trabalhador e informa desativação separada; worker resultado preservado. Mudança para off durante stream interrompe próximos registros, sem afetar drenagem/resultado.

### Dashboard (`model-router/scripts/usage_dashboard.py`)

- `read_usage(logs_dir: Path) -> dict`: dados diários normalizados e diagnósticos, somente `YYYY-MM-DD/current.json` e resumo referenciado dentro da geração daquele dia. Ignora outras revisões/JSONL, protege caminhos e mistura de zonas/versões.
- `usage_view(data: dict, period: str = "weekly", date: str | None = None) -> dict`: períodos `daily`, `weekly` (7dias até data), `cumulative` (histórico disponível até data); retorna totals/by_model/by_session/coverage, sem inferir créditos/cotas. Campos ausentes => null ou subtotal explicitamente parcial, cache não somado ao total.
- `build_dashboard(project: Path, logs_dir: Path | None = None, output: Path | None = None, open_browser: bool = False) -> Path`: HTML autocontido com dados/views embutidos escapados, destino padrão `.model-router/usage.html`; nenhum acesso SQLite, fonte Codex ou coleta. Pode ler configuração para mostrar estado ligado/desligado. Renderização de modelo/sessão por textContent, nunca HTML de entrada.
- `main(argv=None) -> int`: `--project`, `--logs-dir`, `--output`, `--open`; gera snapshot e imprime caminho. UI com períodos/data, quatro métricas e tabelas modelo/sessão; sem charts/rankings/metas.
- Template opcional em `model-router/assets/usage.html`; se usado, instalador copia assets também. Pode manter template no módulo para reduzir embalagem; decisão do implementador.

### Launcher (`model-router/scripts/codex_status.py`)

- `format_status(payload: dict) -> str`: linha segura, limitada, conta workers `observed_running`, exclui coordenador, modelos reportados/solicitados distintos e incertos separados; nada de formato/comandos tmux vindo de dados.
- `should_wrap(argv: list[str], *, is_tty: bool, nested: bool, enabled: bool) -> bool`: apenas interativo normal/resume/fork; help/version, exec e demais operações sem TUI, non-TTY, nested e disabled => false.
- `resolve_project(argv: list[str], cwd: Path) -> Path`: respeita `-C`, `--cd`, formas com `=`; argumentos originais preservados para CLI. Remoto => bar indisponível ou passthrough explícito, sem mostrar métricas locais como remotas.
- `main(argv=None) -> int`: resolve CLI real via PATH; subcomandos do wrapper `usage` e `telemetry on|off [--scope global|project]` encaminham helpers. Via passthrough usa exec direto.
- Via interativa: sessão/server tmux próprios, supervisão por helper interno, coleta a cada2s e atualização de barra. Ctrl-C não mata supervisor enquanto CLI cancela turno; saída CLI escreve exit status para parent e encerra coletor/sessão. Não deixar daemon/coletor órfão.
- Módulo não toca `.bashrc`; ativação é responsabilidade do instalador.

### Instalação (`model-router/scripts/statusbar_install.py`, `install-statusbar.sh`)

- `install_statusbar(prefix: Path, bashrc: Path, *, enable_telemetry: bool = False, activate_bash: bool = False) -> dict`: bundle isolado em prefix/share/model-router/statusbar e comando prefix/bin/codex-status. Copiar scripts/assets excluindo caches; atualizações com backup/staging.
- Bundle preserva subpastas `scripts/` e `assets/`; entrypoint aponta a `scripts/codex_status.py`. Isso mantém resolução de template igual à cópia do projeto.
- `uninstall_statusbar(prefix: Path, bashrc: Path) -> dict`: remover só comando/bundle gerenciados e bloco marcado, preservando texto alheio e histórico/config do usuário.
- Bloco `# >>> model-router statusbar >>>` / `# <<< model-router statusbar <<<` define função codex apontando launcher. Backup antes alterar. Conflito alias/função externa => erro sem sobrescrever.
- CLI: `sh install-statusbar.sh [--prefix PATH] [--bashrc PATH] [--enable-telemetry] [--activate-bash] [--uninstall]`. Instalação sem flags mantém coleta off. Dashboard/disabled não exigem tmux; habilitação depende de tmux disponível no PATH/prefix pessoal.
- Dependência tmux pode ser instalada pela unidade operations no prefix pessoal deste ambiente usando pacote apt verificado, sem sudo/senha; script geral documenta requisito e erro claro caso ausente, sem instalar ferramentas globais silenciosamente.

## Unidades e contratos resolvidos

1. **Autoria:** `test_authoring`, economy/gpt-6-luna/low, escreve `tests/test_statusbar_usage.py` (ou arquivos separados) e adapta SOMENTE fixtures dos testes existentes para opt-in explícito/isolamento HOME/XDG. Não altera asserções legadas nem executa.
2. **RED:** `test_execution`, economy/gpt-6-luna/low, executa unittest, relata falhas esperadas de módulos/interfaces ausentes e baseline.
3. **Implementação A:** `implementation`, medium/standard/gpt-6.1-sol/medium, configura settings+gates em core/CLI/router. Proibido testes/operações.
4. **Implementação B:** `implementation`, medium/standard/gpt-6.1-sol/medium, dashboard JSON/HTML. Arquivos exclusivos, pode trabalhar paralelo com A após RED; sem testes/operações.
5. **Implementação C:** `implementation`, medium/standard/gpt-6.1-sol/medium, launcher+instalador. Usa interfaces acima; executar depois que uma vaga estiver livre, sem editar arquivos de A/B.
6. **GREEN:** `test_execution`, economy/gpt-6-luna/low, suíte completa+smoke fake instalado/browser/tmux, sem mudanças de código. Falhas voltam a unidades respectivas com escopos preservados.
7. **Revisão:** `review` high/strong/gpt-6-astra/high, integração completa privacidade/JSON/PTY/install, somente leitura.
8. **Documentação:** `implementation` low/economy/gpt-6-luna/low, README/integração com instalar/uso/on/off/remover/limites, sem executar testes.
9. **Instalação pessoal:** `operations` economy/gpt-6-luna/low, após testes/revisão instalar com `--enable-telemetry --activate-bash`, backup e validar usando fake/ajuda/versão sem inferência. Gerar dashboard real dos JSONs existentes.

Cada despacho registra contrato completo com id/task_type/complexity/profile/model/reasoning_effort/reason/instructions/acceptance_criteria no prompt e ledger `.superpowers/sdd/statusbar-usage/progress.md`.

## Checklist e aceitação

- [x] Testes escritos antes produção e RED observado; fixtures antigas habilitam coleta conscientemente sem ler config real.
- [x] Default off e veto prevalecem sobre launcher, API, CLI e router; histórico HTML funciona desligado.
- [x] Dashboard usa JSON publicado, sem soma duplicada/revisões/SQLite, filtros e escape verificáveis.
- [x] Launcher preserva argumentos/exit/sinais e barra tem agentes/modelos sem coordenador indevido.
- [x] Instalador com backup/bloco/remover e tmux pessoal disponível; nenhum pacote npm/autenticação alterado.
- [x] Suite e smoke reais/fakes necessários passam, revisão limpa e docs atualizados.
- [x] Instalação e dashboard gerados para usuário atual sem encerrar sessão existente.

Ruling: método de execução já é definido por AGENTS e implementação/instalação autorizadas no desenho; não solicitar nova confirmação de plano. Nenhum git/worktree ou publicação se aplica.
