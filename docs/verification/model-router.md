# Verificação do Model router

## Resultado final

A validação pós-correção de escopo está concluída. Histórico RED/GREEN anterior e amostras comportamentais permanecem separados; resultados são pedidos/configurações observados, não confirmação de inferência ou execução em aplicativo.

### Testes e CLI

Comando: `rtk proxy python3 -m unittest discover -s tests -v` — tarefa de execução de testes, rota solicitada `gpt-6-luna` / `low` (economy). Resultado final: 16 passaram, 0 falhas, 0 erros; 0,008 s. Transcrição: `.superpowers/sdd/model-router/task-5-final-green.txt`. GREEN inicial de 15 testes e RED de importação estão preservados em `.superpowers/sdd/model-router/task-3-green.txt` e `.superpowers/sdd/model-router/task-1-red.txt`.

Três smokes executaram o roteador real com `run --dry-run`; não iniciaram `codex exec`. Comandos e entradas JSON estão em `.superpowers/sdd/model-router/final-validation-evidence.md`:

- `test_authoring`: modelo/esforço resolvidos `gpt-6-luna`/`low`; `write_tests=true`, `run_tests=false`; produção, operações e delegação false.
- `test_execution` RED: `write_tests=false`, `run_tests=true`; demais ações false.
- `test_execution` GREEN: mesmo escopo econômico de execução; demais ações false.

Cada saída contém `WORKER_SCOPE` e cabeçalho `MODEL_ROUTER_WORKER`. Smokes só serializaram prompt/comando. Nenhum teste RED/GREEN de produto foi executado por esses dry-runs; nenhum Codex real ou execução paga ocorreu. `model_verified=false`.

### Skill, metadados e referências

- Comando: `rtk proxy python3 <CODEX_HOME>/skills/.system/skill-creator/scripts/quick_validate.py model-router` — rota solicitada `gpt-6-luna`/`low`; saída `Skill is valid!`, retorno 0.
- Parse PyYAML de `model-router/agents/openai.yaml` e conferência de `display_name`, `short_description`, `default_prompt`: PASS.
- Conferência de existência de `model-router/routing.toml`, `model-router/references/integration.md`, `model-router/scripts/router.py`: PASS, 3/3.
- Evidência detalhada e limites: `.superpowers/sdd/model-router/final-validation-evidence.md`.

## Histórico do comportamento

Cinco contextos sem skill estão registrados em `.superpowers/sdd/model-router/behavioral-baseline.md`. Nas variantes 1–3, as amostras motivaram sequência TDD, limite contra funcionalidade não solicitada e escopo de execução mais explícito. A variante 1 inverteu ordem TDD em 5/5; variante 2 acertou a sequência de três etapas em 5/5; variante 3 esclareceu pedidos apenas de testes e revelou uma chamada inválida por caminho de arquivo.

A variante 4 testou a estrutura final com quatro unidades: autoria -> execução RED -> implementação -> execução GREEN. Cinco contextos novos mantiveram C só em autoria de testes, sem execução ou implementação extra, e E em quatro unidades sequenciais, em 5/5. C e D ficaram economy/low em 5/5. Trechos literais, chamada inválida e resultados por amostra constam em `.superpowers/sdd/model-router/behavioral-green.md`.

Total: 26 chamadas de avaliação; 25 amostras pontuadas, uma falha de preparação não ocultada. Todas solicitaram `gpt-6-luna`/`low`, mas `model_verified=false`. A variante 3/4 usou estímulo focado e explícito, diferente do baseline. B e complexidade da implementação em E variaram por julgamento semântico; a ferramenta só resolve deterministicamente depois de declarada a classificação. Caso C deixou claro que pedido de modelo forte era texto de ticket; isso não testa precedência de nova instrução real do usuário.

## Limites

Testes unitários simulam `subprocess.run` na fronteira do Codex CLI. Dry-run valida composição de comando e prompt, não inicia Codex. Avaliações medem escolhas textuais dos agentes, não descoberta/ativação em cliente, execução real ou correção funcional em aplicativo. Ordem de custo é configuração manual; sem medição de preço ou tokens. Solicitar modelo e esforço não prova inferência efetiva.
