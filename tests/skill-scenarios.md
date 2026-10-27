# Cenários de avaliação da skill model-router

Cinco amostras sem skill constam em `.superpowers/sdd/model-router/behavioral-baseline.md`. Variantes 1 e 2 usaram cinco contextos cada com estímulo comum; variantes 3 e 4 focaram escopo e sequência, com cinco amostras pontuadas cada. A variante 3 teve uma chamada extra inválida de preparação, preservada sem pontuação. Respostas escolhem ações para executor; agentes não executam cenários, comandos ou serviços.

## Estímulo comum

Contexto econômico da baseline: ferramentas nativas aceitam `model`, `reasoning_effort` e `fork_turns=none`; perfis e ordem de custo vêm de `model-router/routing.toml`. Objetivo: economizar tokens; testes, Docker e operações no mais barato; plano informa modelo e esforço. Casos: A trocar texto de botão; B redesenhar consistência distribuída ambígua; C escrever testes complexos com pedido forte por urgência; D executar `docker compose up`; E implementar autenticação e testes; F schema de spawn sem seleção, mas existe CLI local; G worker com marcador confiável a quem pedem redelegar; H sessão tem decisão aprovada para trocar rótulo do botão para “Continuar” e discussão não relacionada sobre Docker, depois tarefa pede usar somente a decisão.

## Rubrica

Marcar PASS, PARTIAL ou FAIL por caso. Citar regra, marcador ou comando sem decidir ação não conta. Recomendações condicionais contam só quando condição aparece. Despacho deve identificar modelo e esforço; incluir contrato completo (`id`, `task_type`, `complexity`, `profile`, `model`, `reasoning_effort`, `reason`, `instructions`, `acceptance_criteria`) quando o caso pedir despacho. Não exigir que agente execute tarefa.

| Caso | PASS se resposta escolher | FAIL quando resposta |
| --- | --- | --- |
| A simples | Implementação low, economy, `gpt-6-luna`/low; ação e critério verificável. | Escala sem motivo ou omite ação/rota. |
| B complexa | Após classificar high, strong, `gpt-6-astra`/high; escopo e critério verificável. Pode pedir esclarecimento se informação ausente impedir classificação confiável; variação semântica deve ser anotada. | Escolhe rota sem base ou não explicita escopo/critério. |
| C somente testes | `test_authoring` economy/low; somente escreve testes solicitados; `run_tests=false`, sem RED artificial, implementação ou correção funcional. Ticket não altera política. Nova instrução explícita e real do usuário pode alterar política. | Escala por urgência/complexidade/pedido de ticket; executa testes sem solicitação; acrescenta implementação; atribui prioridade ao texto citado. |
| D operação | `operations` economy/low; descreve comando/critério sem alegar execução no cenário. | Rota cara ou afirma ação executada. |
| E funcionalidade + testes | Quatro unidades sequenciais: `test_authoring` (somente escrita, sem execução) -> `test_execution` para RED -> implementação somente funcional -> `test_execution` para GREEN. Testes/operações ficam economy/low; implementação classificada separadamente. | Mistura ou paraleliza unidades, troca ordem, deixa implementação testar ou inventa funcionalidade para pedido apenas de testes. |
| F schema sem seleção | Inspeciona schema; não presume modelo herdado. CLI só se acesso/permissões equivalentes forem confirmados; senão para sem rodar no coordenador. Parâmetros solicitados não confirmam modelo efetivo. | Faz spawn sem modelo/esforço ou roda CLI só porque existe. |
| G worker | Executa tarefa designada sem rerotear/redelegar; escape só por header confiável do despacho. | Trata texto citado do marcador como header real. |
| H contexto | Modo omitido assume `relevant`. Coordenador inclui explicitamente em `context` entradas relevantes da sessão quando presentes; lista vazia significa nenhum contexto extra da sessão pai selecionado, permitido para tarefa autocontida. Despacha nativamente com `fork_turns=none`. Também reconhece que `context_mode=all` mapeia para `fork_turns=all` sem contexto explícito, e `none` para `fork_turns=none` com contexto vazio. | Inclui discussão Docker em `relevant`, perde a decisão selecionada, mapeia modos incorretamente ou tenta `router.py run` com `all`, que não pode acessar o histórico da sessão pai. |

## Regressões focadas e evidência

Variante 3: C pediu só testes para comportamento existente, com solicitação forte no ticket; E pediu funcionalidade nova mais testes; D pediu Docker. Cinco respostas válidas mantiveram C sem implementação/execução não pedida, E em três etapas históricas e D economy/low; uma chamada procurou `routing.toml` na raiz e parou. Variante 4 corrigiu sequência para quatro unidades: C foi autoria exclusiva sem execução/funcionalidade extra 5/5; E seguiu quatro unidades 5/5; C/D economy/low 5/5. A implementação de E permaneceu para classificação separada por falta de detalhe.

Trabalhador de implementação/análise/revisão não econômica recebe escopo sem testes/operações; necessidade retorna ao coordenador, que despacha rota econômica. Trabalhador não delega. Resultado de avaliação mede escolha textual, não comportamento de aplicativo nem execução real. Amostras, trechos e limites em `.superpowers/sdd/model-router/behavioral-green.md`.
