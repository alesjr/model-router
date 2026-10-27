# Verificação da telemetria

Data: 2026-10-26. GREEN e revisão focal final concluídos. Sync final da versão atual também foi concluída.

## Testes automatizados

- Comando: `rtk proxy python3 -m unittest discover -s tests -v`.
- RED: 26 testes descobertos, 17 passaram e 9 erros esperados pela ausência de módulos/argumentos de telemetria. Os 16 testes legados passaram.
- Primeiro GREEN: 42 de 43 passaram; fixture de arquivo arquivado não criava diretório pai. Corrigido somente preparo da fixture.
- GREEN após correção inicial: **43 de 43 passaram**.
- GREEN após primeira rodada de correções: **48 de 48 passaram** (etapa intermediária).
- GREEN final após regressões de revisão: **51 de 51 passaram**.
- Fontes sintéticas isoladas, `CODEX_HOME` temporário e executável Codex fake. Nenhum modelo real foi iniciado pela suíte.

## Pacote instalado

Instalação em destino temporário com espaços e sem `PYTHONPATH` do projeto. Passaram `--help`, `sync`, `status --json`, `summary --date` e `watch`. O monitor encerrou com código zero ao receber SIGINT; status mostrou `publication_lag=false`. Smoke instalado final passou. Nenhuma instalação pessoal foi sobrescrita.

## Coleta real do projeto

`sync --project .` e consultas `status`/`summary` executados sobre metadados nativos existentes, sem iniciar `codex exec` ou monitor permanente. Dados gerados somente em `.model-router/` do projeto.

- Snapshot inicial anterior à migração: revisão 1, 53 threads de agentes, 3 modelos reportados, zero diagnósticos de parsing.
- Manifesto diário, eventos e resumo presentes e com revisão correspondente nesse snapshot.
- Repetição daquele snapshot: revisão 2 por novos eventos da sessão ainda em curso; 305 chaves de resposta e 305 chaves únicas, sem duplicação.
- Exportações inspecionadas continham somente campos normalizados de metadados.
- Cobertura incompleta explicitamente indicada; contadores cumulativos alternativos e histórico não correlacionável não são somados ao subtotal por resposta.

Esses números são snapshots de verificação e mudam enquanto sessões avançam. Não representam medição de preço, faturamento ou confirmação do modelo de inferência. Tokens de contexto reenviado podem aparecer novamente em respostas distintas; isso difere de contar duas vezes a mesma resposta.

## Migração e reconciliação

Migração real de banco legado para schema 2 executada e repetida: 854 chaves de resposta existentes foram preservadas, 105 novas foram incorporadas, totalizando 959 únicas sem duplicação. Repetição manteve 4.167 observações e 53 cursores, sem remover cursor; manifesto e resumo terminaram na revisão publicada 4 com `publication_lag=false`. Os diagnósticos conservadores de proveniência migrada e cobertura incompleta permaneceram visíveis.

O marcador independente `reconciliation_version=2` invalida exportações derivadas uma única vez, sem alterar schema 2, dados ou cursores. Intervalos cumulativos sobrepostos sem precedência demonstrável agora são todos marcados não aditivos antes de alocar incrementos; nenhum ganha por ser lido primeiro. Edges copiados idênticos continuam deduplicados.

Sync final de projeto observou schema 2 e reconciliação 2: revisão 4 → 6; reabrir coletor sem sync manteve revisão 6. Das 959 chaves prévias, 60 novas foram incorporadas (1.019 únicas), com zero chaves anteriores ausentes e zero linhas duplicadas. Snapshot mostrou 53 agentes nativos e 3 modelos reportados. Manifestos de 24 e 26 de outubro, resumos exportados e artefatos estavam na revisão 6, com `publication_lag=false`. Cobertura continuou parcial e `provenance_migration_pending` continuou como diagnóstico esperado; contagens refletem esse snapshot, não uma alegação de cobertura completa.

## Documentação e limites

Verificação documental encontrou 9 links locais do README, nenhum quebrado, e nenhum caminho pessoal absoluto. Histórico de 16 testes do roteador permanece no relatório anterior; 43/43 e 48/48 são marcos intermediários desta verificação. Resultado final atual: 51/51 testes mais smoke instalado.

Interface interna do aplicativo e execução real de modelos por `router.py run --telemetry` não foram validadas. A integração foi verificada com processo fake e os registros nativos reais foram coletados separadamente. Revisor encerrou revisão focal estática dos seis achados originais e do P2 de intervalos sobrepostos; todos endereçados, sem novos achados materiais. Revisor não executou código nem testes. Estado `observed_running` é observação de lifecycle, não prova de processo vivo; modelo de contexto continua não verificado. Relatórios de cobertura conservadores fazem parte do resultado, inclusive quando parsing não produz diagnósticos.
