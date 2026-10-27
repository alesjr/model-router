# Telemetria local de agentes, modelos e tokens

Status: **APROVADO** pelo usuário em 2026-10-26 para implementação local.
Revisão técnica resolvida; execução segue o plano e os contratos de trabalho do projeto.
Data: 2026-10-26. Aprovação não autoriza execução real de modelos nem mudanças globais.

## Objetivo e decisões do usuário

O usuário solicitou um painel CLI com quantidade de subagentes e modelos, consumo por modelo,
arquivos JSON por data com identificação e agrupamento de sessão, atualização ao final das tarefas
e reuso de sessões sem duplicação.
Escolheu explicitamente cobrir **agentes nativos do Codex e processos iniciados pelo router no projeto**.
Essa cobertura é requisito confirmado; arquitetura, nomes de comandos e formatos abaixo são propostas.
O painel mostrará também o coordenador, separado dos trabalhadores, para explicitar seu consumo.
Não haverá estimativa de preço, economia percentual, alteração da TUI do Codex ou confirmação fictícia do backend.

## Evidências e limites das fontes

A inspeção local sanitizada do Codex 0.164.0 encontrou os seguintes dados:

| Fonte | Evidência disponível | Limite |
| --- | --- | --- |
| `session_meta.id` | Identificador de thread individual | Não confundir com sessão raiz |
| `session_meta.session_id` | Pode agrupar pai e filhos | Não é chave única de agente |
| `session_meta.source` | Objeto com origem `subagent`, incluindo `source.subagent.spawn.parent_thread_id` | Parser precisa tratar variantes observadas |
| `turn_context` | Modelo e `turn_id`; esforço pode faltar | Modelo reportado pelo contexto, não backend verificado |
| `token_usage_record` | `thread_id`, `session_id`, `turn_id`, `root_turn_id`, `response_id` | Schema local sujeito a mudanças |
| `token_usage_record.usage` | Consumo de uma resposta | Preferir a snapshots acumulados |
| `turn_token_usage` / `thread_token_usage` | Acumulados de turno/thread | Nunca somar como novas respostas |
| `event_msg` de `token_count` | `info.total_token_usage` e `last_token_usage` | Acumulado e última resposta sobrepõem fontes anteriores |
| `task_started` / `task_complete` | Transições observadas com `turn_id` | Ausência de conclusão não comprova processo ativo |

`usage` contém `input_tokens`, `cached_input_tokens`, `cache_write_input_tokens`, `output_tokens`,
`reasoning_output_tokens` e `total_tokens`. Campos ausentes permanecem `null`, não viram zero.
Eventos de abortamento e outras variantes serão reconhecidos somente quando houver schema conhecido.
A [documentação oficial de execução não interativa](https://learn.chatgpt.com/docs/non-interactive-mode)
descreve `codex exec --json`, `thread.started` com `thread_id` e `turn.completed` com consumo.
Ela não garante modelo efetivo nem equivalência entre todos os identificadores públicos e do rollout local.
O coletor não iniciará modelos para descobrir capacidades ou completar metadados ausentes.

## Abordagens consideradas

1. **Coletor local com integração opcional ao executor: recomendada.** Os rollouts cobrem nativos;
   eventos do router acrescentam contrato, invocação e resultado do processo, sem duplicar agentes.
2. Somente envolver `router.py run`: simples, mas não cobre subagentes nativos escolhidos pelo usuário.
3. Substituir o cliente por app-server: amplia produto e operação além do pedido. Não será adotado,
   nem haverá tentativa silenciosa de anexar outra sessão ao cliente atual.

## Componentes e escopo

Implementação proposta: Python 3.11+ e biblioteca padrão, compatível com requisitos atuais do pacote.
`model-router/scripts/telemetry.py` oferecerá CLI; parser e armazenamento poderão ficar em pequenos módulos
adjacentes, copiados pelo instalador existente. Não haverá dependência de rede ou daemon global.
O coletor lerá `$CODEX_HOME/sessions` e `$CODEX_HOME/archived_sessions`; sem a variável, usará `~/.codex`.
`--source PATH`, repetível, permitirá fontes alternativas e fixtures, sem mudar a configuração do Codex.
Arquivos de origem serão somente leitura; o coletor escreverá apenas seu armazenamento e suas exportações.
O diretório padrão será `<project>/.model-router/`, com `telemetry.sqlite3` e `logs/YYYY-MM-DD/`.
`--logs-dir PATH` permitirá outra raiz de exportação; o banco continuará vinculado ao projeto selecionado.
`--project` será obrigatório, resolvido por caminho real, sem exigir Git ou alterar permissões.

Selecionar raízes com `cwd` resolvido igual ao projeto ou dentro dele, por componentes de caminho.
Incluir descendentes dessas raízes por `parent_thread_id`, mesmo quando o filho tiver outro `cwd`.
Metadados descobertos depois poderão reconciliar filhos inicialmente sem pai, sem inclusão especulativa.
`--session ID` filtrará consultas pela sessão raiz ou thread selecionada e seus descendentes;
o resultado informará qual identidade foi resolvida e recusará uma seleção ambígua. O filtro não restringe
a coleta do projeto, não remove outras sessões do banco e não cria exportações físicas concorrentes por filtro.
Thread sem `cwd` nem vínculo demonstrável permanecerá fora do projeto, com diagnóstico agregado.
Uma raiz selecionada mantém sua atribuição ao projeto durante mudanças posteriores de `cwd`.

## Identidades e representação

| Entidade | Chave e finalidade |
| --- | --- |
| Projeto | Caminho real; impede mistura entre diretórios com nomes semelhantes |
| Sessão raiz | Identificador de agrupamento observado ou raiz derivada da árvore, com proveniência |
| Agente | `thread_id`; pai e filho que compartilham `session_id` continuam agentes distintos |
| Turno | `(thread_id, turn_id)` quando disponível; reuso da thread cria novos turnos |
| Resposta | `(thread_id, response_id)`; unidade autoritativa de consumo |
| Invocação router | UUID `invocation_id`; referência a `task_id`, thread e resultado do processo |
| Tentativa | Nova invocação, mesmo para o mesmo `task_id`; não é deduplicada como repetição de log |

`session_meta.id` alimenta a identidade da thread; `session_id` é armazenado separadamente.
Vínculos conflitantes permanecem diagnosticados, sem fusão silenciosa de threads ou sessões.
Reinício do coletor, cópia de arquivo ou movimentação para `archived_sessions` não gera consumo novo.
O número de agentes conta threads únicas; invocações vinculadas acrescentam metadados, não outro agente.
Origem do agente (`native`, `router`, `unknown`) e papel (`coordinator`, `worker`, `unknown`) são eixos distintos.
Coordenadores são raízes observadas; sem evidência suficiente o papel será desconhecido.
Para nativos sem associação explícita do coordenador, `task_id=null`; thread/turno identificam tarefa observada.
Não extrair contratos ou IDs de prompts. Uma associação explícita futura deverá registrar sua proveniência.

O registro normalizado terá versão de schema, identidade estável, proveniência, tempo original e de ingestão,
projeto, sessão, thread, pai, turno, resposta, invocação, modelo solicitado e modelo reportado.
Também conterá esforço solicitado/reportado, status, uso, qualidade da cobertura e revisão de reconciliação.
`model_verified` continuará falso, salvo futura evidência explícita de verificação com origem registrada.
Prompts, transcrições, respostas, stdout/stderr completos e comandos completos não serão exportados.
Usar whitelist de metadados; diagnósticos terão código e localização técnica, sem copiar conteúdo da linha.

## Cálculo e reconciliação de tokens

1. Preferir `token_usage_record.usage` por resposta, com unicidade persistente `(thread_id, response_id)`.
   Repetição idêntica não altera totais; revisão do mesmo registro substitui valores e marca reconciliação.
   Conflitos entre fontes sem precedência demonstrável ficam sinalizados, sem somar ambas as versões.
2. Associar modelo ao contexto do turno correspondente. Ausência de contexto usa bucket `unknown`.
   Se houver modelos conflitantes dentro do mesmo turno sem vínculo por resposta, não atribuir tudo ao último;
   manter respostas ambíguas em `unknown`, com proveniência e possibilidade de reconciliação posterior.
3. `total_tokens` observado é preferido. Na ausência dele, derivar `input_tokens + output_tokens` somente
   quando a semântica dos campos permitir. Inconsistências são exibidas, sem corrigir silenciosamente.
   `cached_input_tokens` é parcela de entrada e `reasoning_output_tokens`, parcela de saída: não somar novamente.
   `cache_write_input_tokens` permanece dimensão separada, sem inferir faturamento ou inclusão no total.
4. Sem respostas identificáveis, aceitar snapshots acumulados como fallback de cobertura parcial.
   Calcular delta somente entre checkpoints comparáveis da mesma thread e fonte; não somar snapshots.
   Primeiro snapshot é baseline histórica de data/modelo não separáveis, separado dos incrementos datáveis.
   Não lançar esse histórico inteiro no dia da coleta. Deltas entre dias ficam em intervalo não alocado
   quando não houver evidência para repartir por dia; o resumo explicita esse consumo adicional conhecido.
5. Queda de contador, truncamento ou mudança de geração provoca diagnóstico de reset/gap, sem clamp silencioso
   para zero nem dedução automática de consumo negativo. Não usar `last_token_usage` como resposta deduplicável
   se faltar identificador estável; sua repetição não permite provar uma resposta nova.
6. Se respostas detalhadas chegarem depois do fallback, substituir a cobertura sobreposta, nunca adicionar
   dois consumos para o mesmo intervalo. Registrar limites dos checkpoints e respostas já cobertas.
   Se a sobreposição não puder ser resolvida, manter a alternativa separada como não aditiva/pendente,
   sem fabricar total exato. Exibir subtotal conhecido e cobertura incompleta, inclusive por modelo.

Somente respostas com tempo original válido entram no total diário exato; tempo de ingestão não o substitui.
Datas usam UTC por padrão; `--timezone` aceita zona IANA via `zoneinfo` para agrupamento e apresentação.
A zona e a revisão estarão nos resumos; alteração da zona exige regeneração explícita das exportações.
Logs do `codex exec` e rollouts nativos nunca serão somados para representar as mesmas respostas.

## Persistência, leitura incremental e exportação

SQLite será a fonte canônica transacional: threads, turnos, respostas, invocações, checkpoints,
diagnósticos, cursores e revisões de exportação. Chaves únicas protegem deduplicação entre processos.
Leitura incremental registrará identidade do arquivo, geração, offset confirmado e impressão do trecho lido.
Linha JSONL incompleta fica pendente até terminar; linha inválida completa produz diagnóstico e avanço registrado.
Rotação, arquivo substituído ou truncado exige releitura segura; offset de arquivo não é chave de consumo.
Inserção de registros, reconciliação e avanço de cursor ocorrerão na mesma transação.
Concorrência usará transações curtas, timeout de bloqueio e erro explícito quando não puder progredir.

Cada data terá exportação JSONL normalizada e `summary.json`, contendo a mesma `revision` e zona.
Cada evento JSONL identificará sessão e thread; o resumo agrupará por sessão e modelo, além do total do dia.
O JSONL representa a visão deduplicada vigente; reconciliações regeneram datas afetadas, inclusive antigas.
Para publicar o par de forma consistente, escrever ambos em uma geração `revisions/<revision>/`,
sincronizar e trocar atomicamente um manifesto `current.json` que referencia os dois arquivos.
Leitores seguem o manifesto; não tratam dois arquivos independentes como troca atômica de um par.
Após interrupção, gerações incompletas ficam ignoradas; `sync` retoma exportações pendentes pelo banco.
Se banco avançou e exportação falhou, o painel mostra revisão publicada atrasada e permite reconstrução.
Baseline histórica e intervalos sem data ficarão em resumo de cobertura separado, fora de dias arbitrários.

## CLI e comportamento do painel

Comandos propostos, relativos à cópia de desenvolvimento; instalação usará caminho portátil da skill:

```sh
python3 model-router/scripts/telemetry.py sync --project .
python3 model-router/scripts/telemetry.py status --project .
python3 model-router/scripts/telemetry.py status --project . --json
python3 model-router/scripts/telemetry.py watch --project . --interval 2
python3 model-router/scripts/telemetry.py summary --project . --date 2026-10-26
```

`sync` coleta e exporta; `status` e `summary` mostram o último snapshot com horário/revisão e atraso explícitos.
`watch` executa ciclos de sincronização em terminal separado, atualizando o painel sem alterar a TUI nativa.
Cada ciclo lê incrementos; conclusão de turno/tarefa atualiza estado e resumo, sem escrita token a token.
O painel apresenta threads únicas, trabalhadores por origem, invocações router, modelos e tokens por modelo,
com subtotal do coordenador separado, sessão, esforço, última observação, cobertura e diagnósticos.
As contagens de modelos distinguem solicitados de reportados; `unknown` não é modelo adicional conhecido.
Um modelo reutilizado em várias threads aparece uma vez na contagem de modelos e recebe consumo agregado.
Estados: `observed_running`, `idle`, `completed`, `failed`, `unknown` e `stale`.
`observed_running` significa início observado sem encerramento, não comprovação de processo vivo.
`completed` indica o último turno/tarefa concluído, não encerramento da thread; reuso pode voltar a `observed_running`.
`stale` etiqueta observação antiga, com padrão de 300 segundos, configurável por `--stale-after SEGUNDOS`;
não prova término. Ausência de heartbeat não detecta kill.
Abortamento/interrupção conhecidos aparecem como motivo explícito; sem evento, estado final fica desconhecido.

## Integração opcional ao router

Propor `router.py run --telemetry`, sem alterar a rota nem iniciar monitoramento global automaticamente.
Por padrão, `--cwd` também define o projeto da telemetria. `--telemetry-project PATH` permitirá que execuções
em subpastas compartilhem a raiz e o banco usados por `telemetry.py --project`, preservando o diretório do trabalhador.
Gerar `invocation_id` antes de iniciar o subprocesso e registrar contrato permitido, início e resultado.
Adicionar `--json` ao `codex exec` nessa modalidade e consumir eventos incrementalmente para capturar
`thread.started` cedo, usando `Popen` ou callback equivalente, preservando timeout e permissões existentes.
O stdout público do router continuará sendo um único JSON de resultado; progresso/alertas irão para stderr.
O coletor nativo fornece consumo detalhado; o wrapper acrescenta vínculos de tarefa e estado do processo.
Consumo de `turn.completed` só será fallback se houver identidade estável e correspondência demonstrável.
Sem `turn_id`, um ordinal de execução pode identificar evento no wrapper, mas não prova correlação com rollout;
nessa situação, consumo fallback do exec fica indisponível para contabilização; não inferir correlação.
Falha de telemetria não mascara retorno, timeout ou erro do trabalhador; resultado inclui alerta separado.
`route` e `run --dry-run` não criam eventos de execução, invocações ou chamadas de modelo.

## Aceitação futura e entrega

Fixtures sintéticas deverão cobrir pai/filho com sessão compartilhada e threads distintas; reuso de thread
em múltiplos turnos; resposta repetida; snapshots em dois dias; backfill de respostas após fallback;
troca ambígua de modelo; campos ausentes; cache sem dupla soma; combinação exec/native sem duplicação;
linha parcial; arquivo inválido/truncado/rotacionado; cópia para arquivo arquivado; concorrência SQLite;
interrupção durante publicação e recuperação do par de exportações; seleção de `cwd` e descendentes;
erro do coletor preservando resultado do router; testes legados do executor; instalação copiando helpers.
Esses critérios não representam testes já escritos ou executados, nem validação de modelos reais.
README, skill e referência de integração receberão instruções portáteis; AGENTS documentará contratos
quando necessário, sem caminhos pessoais ou alteração automática da configuração global do usuário.
Próximos passos: revisão técnica deste desenho, aprovação do usuário e plano de implementação com contratos
separados para autoria de testes, execução, implementação e verificação, conforme política vigente.
