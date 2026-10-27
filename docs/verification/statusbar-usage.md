# Launcher de status e painel de uso

## Instalação e comandos

Requer Python 3.11+, Codex CLI e tmux (`PATH` ou `~/.local/bin/tmux`). Na raiz da cópia local:

```sh
sh install-statusbar.sh --enable-telemetry --activate-bash
```

Sem configuração prévia, coleta começa desligada. `--enable-telemetry` habilita coleta global; sem a flag, instalação preserva escolha existente. Sem `--activate-bash`, não há encaminhamento automático de `codex` em novas sessões. O instalador cria `~/.local/bin/codex-status`, instala bundle isolado e faz backup do `~/.bashrc` antes de alterar. Se ativou Bash, abra nova sessão ou execute `source ~/.bashrc`. Com coleta desligada, instalação apenas do painel não requer Codex nem tmux; barra interativa depende de pré-voo dessas ferramentas. Destinos não reconhecidos são preservados, e falha restaura estado gerenciado.

```sh
codex-status telemetry on --scope project
codex-status telemetry off --scope project
codex-status telemetry on --scope global
codex-status telemetry off --scope global
codex-status usage
codex-status usage --project . --no-open
```

Qualquer veto `enabled:false` global ou de projeto mantém coleta desligada; configuração inválida também bloqueia. O dashboard é snapshot HTML local e offline em `.model-router/usage.html`, com filtros diário, semanal e acumulado. `usage` não sincroniza fontes: execute `sync` antes ou mantenha `watch` ativo para publicar dados recentes.

Para remover launcher, bundle e bloco Bash gerenciado, mantendo backup da configuração:

```sh
python3 model-router/scripts/statusbar_install.py --uninstall
```

A remoção não apaga configurações nem histórico de telemetria.

## Escopo e interpretação

Barra é adicional ao Codex e requer tmux; TUI nativa não é alterada. Abrir nova sessão é necessário para a função Bash ativada pela instalação. Escopo deriva do projeto local e respeita `-C`/`--cd`; não representa exclusivamente a thread aberta. Chamadas remotas, não interativas, sem TTY ou aninhadas passam ao Codex original.

Não há framework frontend, servidor, API ou banco SQL para o painel. Ele lê cada manifesto diário `current.json` e somente o resumo JSON da geração nele indicada. `sync`/`watch` regeneram publicação. Histórico JSONL não é somado de novo. O painel não consulta SQLite.

Uso local não mede cota nem faturamento oficial da conta. Cache de entrada é parcela da entrada, não deve ser somado duas vezes. `null` e cobertura parcial indicam dados desconhecidos. Modelo solicitado/reportado não comprova backend efetivo; estados `observed_running` e `stale` são observações, não prova de processo vivo.

## Estado de verificação

Suíte final após correção dos metadados de parentesco: **86 testes aprovados**, saída 0. Dois testes novos cobrem recuperação de relação e papel via banco e garantem idempotência sem duplicar tokens. O parser usa `thread_spawn` atual e fallback legado `spawn`. Revisão encerrada com aprovação; três follow-ups resolvidos. QA Chrome headless isolado verificou cinco filtros no DOM e imagem. Smoke PTY confirmou worker/modelo em fixture controlada, coleta desligada sem escrita, Ctrl-C e encerramento com código 7 e limpeza; sinais HUP (129) e TERM (143) também preservaram códigos e limpeza.

Instalação pessoal concluída: backup do Bash criado, versão local do Codex e `exec --help` verificados, função disponível em novo Bash. Opt-out bloqueou escrita no banco; reativação permitiu sincronização (revisão publicada 8) e geração de `.model-router/usage.html`. Auditoria nativa confirmou associação de parentesco a partir de `thread_spawn`; registros `spawn` legados também têm fallback. Testes unitários confirmam recuperação via banco e idempotência sem duplicar tokens. Smoke operacional em PTY isolado confirmou barra com um worker observado, modelo reportado, modelo de coordenador oculto, retorno zero e limpeza do socket. A auditoria não comparou conjunto exato de identidades antigas; contagens continuam limitadas à evidência local disponível e podem ter cobertura parcial. Evidências finais registradas em `final-green.txt`, `native-parent-fix.md`, `native-parent-red.txt`, `native-parent-real-qa.txt`, `personal-install.txt` e relatório de revisão da execução. Observações de worker/modelo não confirmam backend efetivo nem cobertura completa, e não prometem comportamento de sessão já aberta.
