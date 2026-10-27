# Launcher Codex, uso local em HTML e controle de telemetria

Status: **APROVADO** pelo usuário em 2026-10-26, incluindo launcher, HTML local e controle de telemetria. Implementação e instalação pessoal autorizadas; sem execução de inferência para validação.

## Objetivo e escolha confirmada

Exibir quantidade de trabalhadores em execução observada e modelos usados em uma barra inferior durante qualquer trabalho iniciado pelo usuário. O usuário escolheu **barra adicional com launcher**, usando tmux, em vez de alterar/compilar o Codex CLI.

Refinamento confirmado: acrescentar dashboard de uso **em página HTML local simples**, inspirado na organização do `/usage` do Codex, sem frameworks, e configuração para desativar telemetria. O dashboard usará os JSONs publicados da telemetria como base de dados, sem consultar o SQLite nem criar outro banco.

A cobertura segue a telemetria aprovada: agentes nativos e execuções do router no projeto. Modelo reportado pelo contexto não comprova backend; estado observado não comprova processo vivo.

## Evidência e alternativas

Codex CLI 0.164.0 instalado: menu `/statusline` foi percorrido integralmente, sem salvar alterações ou enviar prompt. Seus 26 itens incluem modelo da thread, esforço e tokens, mas não contagem/modelos de subagentes. A [referência oficial](https://learn.chatgpt.com/docs/config-file/config-reference) define `tui.status_line` como lista de identificadores; os [comandos oficiais](https://learn.chatgpt.com/docs/developer-commands) documentam o seletor.

- **Escolhido pelo usuário:** launcher com rodapé tmux adicional, alimentado pelo coletor existente. Requer tmux, atualmente ausente.
- Alterar Codex CLI: permitiria modificar exatamente o rodapé original, com compilação e manutenção por atualização; alternativa não escolhida.
- Usar títulos de thread para transportar métricas não será adotado: mudaria histórico/identidade das sessões sem necessidade.

## Comportamento proposto

Ao iniciar `codex` em um terminal Bash novo, o launcher abre CLI com uma barra adicional, por exemplo:

```text
Agentes obs.: 3 | gpt-6-luna ×2 | gpt-6.1-sol ×1 | incertos: 0
```

- Atualização a cada dois segundos, sem chamada de modelo e sem um segundo terminal de monitoramento.
- Contagem considera somente `role=worker` com `status=observed_running`. Coordenador não entra nessa quantidade. Threads com estado antigo/incerto aparecem separadamente.
- Agrupamento de modelos usa contexto reportado; fallback solicitado, quando houver, será identificado. Modelo ausente aparece como desconhecido.
- Escopo é o projeto do diretório inicial, respeitando `-C`/`--cd`; inclui descendentes comprovados e outras sessões do mesmo projeto. A barra informa esse escopo, sem fingir monitoramento exclusivo da thread aberta.
- Sessões remotas ou escopo que não possa ser demonstrado mostram indisponibilidade; não recebem contagem local inventada.
- Logs diários e SQLite continuam no `.model-router/` desse projeto, com as mesmas regras de deduplicação, cobertura e privacidade.

## Dashboard mínimo de uso

Uma única página local em HTML/CSS/JavaScript puro, com fonte do sistema e nenhuma dependência/CDN. Não haverá serviço hospedado, login, framework, build frontend ou API da conta. A geração e a abertura da página usarão apenas Python da biblioteca padrão.

Interface proposta:

```text
Uso local do projeto                 Última coleta: …
[ Diário ] [ Semanal ] [ Acumulado ]  Data: …
Total conhecido | Entrada | Entrada em cache | Saída

Modelo          Entrada   Cache   Saída   Total
…
Sessão          Total
…
Cobertura: completa/parcial/indisponível
```

Os períodos seguem a organização familiar do [uso no Codex](https://learn.chatgpt.com/docs/developer-commands): diário, semanal e acumulado. Aqui representam o dia selecionado, os últimos sete dias até essa data e todo histórico local disponível. Não representam saldo, limites ou faturamento oficial da conta.

- Ler somente `logs/YYYY-MM-DD/current.json` e o `summary.json` da geração publicada indicada. Não varrer nem somar todas as revisões; JSONL permanece histórico de auditoria e não é somado novamente ao resumo.
- Validar schema, data, timezone e caminhos dos manifestos; ler apenas arquivos dentro da raiz de logs selecionada. Arquivos parciais/ausentes e mistura de fusos devem aparecer como cobertura incompleta.
- Agregar totais conhecidos por modelo e sessão. `null` aparece como indisponível; cache e raciocínio continuam parcelas dos respectivos totais, sem dupla soma. Não exibir gráficos de crédito, metas, rankings ou métricas extras.
- Gerar HTML autocontido a partir desses JSONs, com dados embutidos escapados e filtragem de período no navegador. Mensagens, respostas e lista de requests não entram na página. A saída é um snapshot com horário explícito; gerar novamente atualiza o arquivo.
- `codex-status usage` gera e abre página no navegador local; opção sem abertura imprime caminho para ambientes sem interface gráfica. O dashboard continua funcionando com coleta desligada, usando histórico existente.
- Não substituir ou interceptar `/usage` dentro do Codex: esse comando continua representando uso da conta, enquanto esta página representa registros locais do projeto.

## Configuração e desativação

Arquivo JSON global em `~/.config/model-router/settings.json` (ou `$XDG_CONFIG_HOME`) e opção por projeto em `.model-router.json`:

```json
{"telemetry": {"enabled": false}}
```

- Padrão sem configuração: desativada. Habilitação será explícita durante instalação ou por `codex-status telemetry on`.
- `codex-status telemetry off` grava desativação global. `enabled:false` explícito global ou no projeto bloqueia coleta; projeto não pode contornar desativação global. Sem veto, uma habilitação explícita permite coleta. Configuração inválida bloqueia coleta com diagnóstico claro.
- A regra vale para launcher, `sync`, `watch`, `router.py run --telemetry` e chamadas do coletor. Verificar antes de ler sessões do Codex, criar banco ou gravar eventos. Uma flag de execução não sobrepõe desativação configurada.
- Processos de acompanhamento verificam configuração a cada ciclo; desativação interrompe próximas coletas. Worker Codex continua sua tarefa; logger não muda sucesso/falha da execução.
- Launcher com coleta desativada executa Codex diretamente. Não inicia monitor/tmux para métricas indisponíveis. Histórico existente não é apagado automaticamente e permanece disponível para leitura explícita pelo dashboard.
- Configuração não lê credenciais nem altera `config.toml`, autenticação ou permissões do Codex. A escolha de habilitar para o usuário atual será explícita no comando de instalação documentado.

## Launcher e ciclo de vida

`codex-status` resolve executável Codex original pelo PATH e usa servidor/sessão tmux próprios, sem reconfigurar outras sessões tmux. Todos os argumentos são preservados.

Comandos não interativos (`exec`, `review`, `login`, `--help`, `--version` etc.), execução sem TTY e chamadas aninhadas seguem direto para o Codex original. Isso evita recursão no router e em trabalhadores.

Somente sessões interativas ganham barra. Coleta acompanha processo do CLI; encerramento normal, Ctrl-C de cancelamento e saída devem preservar interação nativa. Ao fechar Codex, coletor e sessão do launcher são encerrados. Não instalar daemon/serviço global.

Tratar conteúdo da barra como texto limitado/sanitizado: IDs/modelos não podem injetar comandos shell ou formatação tmux. Processos são iniciados por argumentos, sem interpolar prompts em shell.

## Instalação proposta

Comando prático na cópia do projeto: `sh install-statusbar.sh`.

- Verificar Python, Codex e tmux; instalar tmux no prefixo pessoal quando possível, sem exigir senha administrativa ou alterar permissões. Se dependências locais não puderem ser atendidas, relatar bloqueio específico.
- Instalar launcher/coletores em prefixo pessoal isolado; não sobrescrever pacote npm nem a política da skill instalada.
- Disponibilizar `~/.local/bin/codex-status`.
- Registrar bloco identificado em `~/.bashrc` para encaminhar `codex` ao launcher em futuras sessões interativas; criar backup antes da alteração e permitir remoção/restauração do bloco. Se houver função/alias Codex preexistente, preservar e relatar conflito, sem sobrescrever silenciosamente.
- Disponibilizar comando explícito `codex-status` para uso imediato e documentar como carregar configuração no terminal atual. A sessão Codex já aberta não será substituída ou encerrada durante instalação.
- Instalação sem habilitação explícita mantém coleta desativada. Documentar opção `--enable-telemetry` para instalar com consentimento e comando posterior `codex-status telemetry off` para desativar. Usuários que desejem apenas consultar uso local poderão gerar HTML sem ativar coleta nem instalar tmux.
- Instalação/remoção idempotentes; nenhuma alteração em modelos, autenticação, sandbox, plugins ou configuração do rodapé nativo.

## Verificação necessária

Testes sintéticos: texto da barra por modelo/estado, ausência de dupla contagem do coordenador, argumentos e caminhos com espaços, bypass não interativo/aninhado, falha de dependência, configuração Bash com backup/bloco idempotente e proteção contra injeção.

Dashboard: fixtures JSON com revisões repetidas, períodos, modelos e sessões, valores ausentes, cache e cobertura parcial; nenhuma consulta SQLite nem importação do coletor para renderizar HTML. Conteúdo potencialmente malicioso deve permanecer texto, sem HTML/script executável.

Desativação: padrão desligado, vetos global/projeto, `--telemetry` sem contornar configuração, nenhum acesso às sessões/banco antes da checagem, parada no próximo ciclo, histórico legível sem iniciar coleta. Testes antigos com coleta autorizada usarão configuração explícita nas fixtures.

Smoke real em tmux isolado com processo fake: barra atualiza, redimensiona e encerra sem processo coletor órfão. Verificar launcher instalado e comandos Codex de ajuda/versão sem iniciar inferência. A suíte de telemetria existente deve continuar passando.

README e referência de integração receberão comandos de instalação, ativação, remoção e limites. Nenhuma execução paga de modelo é necessária para validar esta integração.
