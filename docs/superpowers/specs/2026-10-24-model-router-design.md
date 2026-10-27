# Model Router — desenho

Status: aprovado pelo usuário em 2026-10-24. Abordagem escolhida: subagentes com alternativa CLI.

## Intenção

Criar uma skill reutilizável para Codex CLI e aplicativo Codex que integre planos Superpowers e outras especificações. Antes de cada tarefa, selecionar modelo e esforço de raciocínio compatíveis com sua complexidade, reduzindo uso desnecessário de modelos caros e contexto repetido.

Requisitos informados pelo usuário: tarefa deve declarar modelo e nível baixo, médio ou alto; criação e execução de testes, comandos Docker e outras operações devem usar o modelo mais barato configurado; transição automática conforme configuração. O usuário confirmou ambos os clientes e escolheu subagentes com alternativa CLI.

## Abordagens

1. **Escolhida: skill + subagentes + executor CLI.** Subagentes recebem modelo e esforço explícitos. Sem capacidade nativa de selecionar modelo, um executor inicia `codex exec` com os mesmos parâmetros. Exige um pequeno utilitário local, mas oferece alternativa entre clientes.
2. **Somente subagentes.** Menos código, mas execução precisa parar se cliente não permitir selecionar modelo. Não atende à alternativa escolhida.
3. **Somente instruções nos planos.** Registra intenção, mas não efetua seleção. Insuficiente para automação pedida.

## Política

- Configuração TOML versionada define perfis `economy`, `standard` e `strong`, com identificadores reais de modelo, esforços aceitos e ordem explícita de custo. Não deduzir preço pelo nome ou disponibilidade no catálogo.
- Proposta inicial: `economy = gpt-6-luna`, `standard = gpt-6.1-sol`, `strong = gpt-6-astra`. São valores editáveis encontrados no catálogo local; ordem de custo configurada deve ser confirmada pelo responsável pela instalação, sem alegar preços verificados.
- Separar complexidade da tarefa de esforço de raciocínio. Ambos usam baixo/médio/alto na explicação; esforço enviado ao Codex usa `low`, `medium`, `high`.
- Mudanças pequenas, mecânicas e bem delimitadas: perfil econômico. Implementação comum com várias partes: padrão. Arquitetura, ambiguidade ou raciocínio difícil: forte, com justificativa.
- **Criar testes, executar testes, lint, build, Docker e comandos operacionais: modelo econômico, esforço `low` por padrão.** Não aumentar modelo automaticamente, mesmo após falha. Dividir tarefas mistas antes de executar; implementação funcional segue sua própria classificação.
- Falha de tarefa econômica gera resultado e diagnóstico curto. Se exigir análise especializada, registrar nova tarefa de diagnóstico claramente separada; não transferir implementação/execução dos testes ou operações para modelo caro disfarçada de diagnóstico.
- Modelo indisponível ou troca não suportada: tentar alternativa CLI, quando disponível. Se ambas as vias falharem, parar tarefa com causa explícita. Não herdar silenciosamente modelo do pai.
- Primeira versão não faz escalada após falha: seleção automática ocorre antes da tarefa, por classificação e configuração. Nova tentativa com modelo diferente exige atualizar explicitamente contrato/configuração; tarefas obrigatoriamente econômicas permanecem nessa categoria. Regras automáticas de escalada ficam fora desta entrega.

## Contrato da tarefa

Planos Markdown mantêm seu formato e ganham um bloco JSON por tarefa executável. Outras ferramentas podem produzir o mesmo contrato. Campos: `id`, `task_type`, `complexity`, `profile`, `model`, `reasoning_effort`, `reason`, `instructions` e `acceptance_criteria`.

Tipos iniciais: `implementation`, `test_authoring`, `test_execution`, `operations`, `analysis`, `review`. Classificação exige inspecionar objetivo real, não somente rótulo. Testes misturados com funcionalidade devem virar tarefas separadas, preservando dependências e sequência TDD quando aplicável.

Uma tarefa de implementação pode entregar código após tarefa econômica escrever teste que falha; outra tarefa econômica executa verificação. Sequência permanece serial quando houver arquivos compartilhados. Não criar agentes paralelos apenas para economizar.

## Execução

1. Skill lê política e tarefa atual; classifica trechos ainda não classificados antes de executá-los.
2. Registra decisão no plano/contrato antes de despachar.
3. Prefere ferramenta nativa de subagentes com contexto novo, modelo e esforço explícitos. A própria skill instrui o agente a inspecionar o schema das ferramentas expostas no turno: usar `spawn_agent` ou equivalente somente quando suportar parâmetros de modelo e esforço, ou papel personalizado com ambos fixados. No ambiente atual, `spawn_agent` aceita `model`, `reasoning_effort` e `fork_turns`; usar `fork_turns = "none"`. Nomes exatos devem seguir schema do cliente. Sem parâmetros ou papel verificável, via nativa é indisponível. Envia caminhos, instruções e critérios essenciais; não copia conversa inteira.
4. Alternativa usa utilitário Python da biblioteca padrão para chamar `codex exec --model ... -c model_reasoning_effort=...`, com prompt via stdin e argumentos em lista, sem shell intermediário.
5. Executor impede recursão: trabalhador recebe instrução para executar somente tarefa atribuída, sem acionar roteador nem delegar novamente.
6. Resultado inclui rota solicitada, mecanismo, saída, estado e evidência disponível do modelo efetivo. Se cliente não expuser confirmação, marcar seleção como solicitada, sem alegar verificação.

Utilitário Python implementa somente alternativa CLI e validação determinística do contrato/política; não tenta chamar ferramentas internas do aplicativo. Despacho nativo é procedimento da skill, testado com cenários de agente. Esse limite evita depender de API interna inexistente entre clientes.

Não alterar sandbox, aprovações, autenticação ou permissões para fazer alternativa funcionar. A configuração vigente do cliente continua aplicável. Comandos operacionais continuam sujeitos às autorizações da tarefa.

## Integração e entrega

- Skill independente em `model-router/`, com `SKILL.md`, metadados `agents/openai.yaml`, política, referência de integração e utilitário de execução.
- Instruções orientam Superpowers a anotar tarefas ao planejar e despachar ao executar. Não editar arquivos de cache do plugin; atualizações poderiam sobrescrever alterações.
- Ativação implícita pela descrição da skill e instrução de integração em `AGENTS.md`. Disponibilizar trecho pronto para outros projetos, pois descoberta implícita isolada não garante acionamento em toda tarefa.
- Entrega neste diretório. Documentar instalação comum em `~/.agents/skills/` para clientes locais, sem sobrescrever skill existente. Instalação/ativação global não faz parte desta proposta inicial.
- Documento local `AGENTS.md` registra RTK, preferências do usuário e fluxo deste repositório, conforme intenção de inicialização.

## Limites e economia

Skill não altera modelo do agente principal em processamento. Automação escolhe modelo do próximo trabalhador/processo antes de sua execução. Um pai caro continua gerando custo ao coordenar; recomendar iniciar novas sessões com modelo econômico quando objetivo principal for economia.

Docker e testes rodam no sistema operacional; seleção de modelo controla agente que prepara comandos, escreve código e interpreta resultados. Não muda custo computacional de containers.

Subagentes adicionam contexto e coordenação. Usar contexto mínimo e reaproveitar trabalhador com mesma rota apenas quando houver suporte e isso não misturar escopos. Não prometer economia percentual sem medição.

Compatibilidade do aplicativo depende das ferramentas expostas por versão/conta e acesso a processos locais. Se aplicativo não expuser subagentes nem execução local, alternativa CLI não é acessível naquele ambiente; reportar bloqueio.

## Validação

Implementação e execução de testes deste projeto devem ser delegadas ao modelo econômico, conforme requisito do usuário. Cobrir: roteamento simples/complexo; todos os tipos obrigatoriamente econômicos; separação de tarefas mistas; parâmetros modelo/esforço; configuração inválida; modelo indisponível; falha do processo; não recursão; preservação de permissões e ausência de shell injection.

Validar caminhos nativo e CLI com ferramentas simuladas e cenários de skill em contexto novo. Execuções reais pagas somente quando necessárias e identificadas. Não afirmar aplicativo validado por teste CLI; registrar separadamente cobertura simulada e real.

## Escolhas propostas ao implementador

Python 3.11+ e biblioteca padrão; TOML para política; JSON para contratos e resultados. Mensagens e documentação em português, código com nomes claros em inglês. Sem serviço externo, daemon ou dependências adicionais.

## Fontes consultadas

- https://learn.chatgpt.com/docs/agent-configuration/subagents — clientes, arquivos de agentes e seleção de modelo/esforço.
- https://learn.chatgpt.com/docs/build-skills — descoberta e metadados de skills.
- CLI local `codex-cli 0.164.0`, ajuda `codex exec --help` e catálogo local de modelos. Catálogo não comprova preços nem acesso efetivo em execução.
