# Coding Agent Python

MVP de um agente IA focado em tarefas de codigo. Ele usa o OpenAI Agents SDK para orquestrar um agente principal com ferramentas locais controladas para inspecionar projetos, editar arquivos dentro de um workspace permitido e rodar comandos de verificacao com allowlist.

## O que vem neste MVP

- Agente principal `coding_agent` com instrucoes de engenharia.
- Ferramentas locais:
  - `list_files`: lista arquivos do workspace.
  - `read_file`: le arquivos de texto com limite de tamanho.
  - `search_files`: procura texto em arquivos UTF-8 do workspace.
  - `apply_patch`: altera arquivos existentes por substituicao exata e retorna diff.
  - `diff_file`: compara um arquivo com conteudo proposto.
  - `git_diff`: mostra `git diff` quando Git esta disponivel.
  - `project_summary`: resume estrutura, extensoes e arquivos-chave do workspace.
  - `profile_table`: perfil de CSV/Excel/Parquet com tipos, nulos, exemplos e estatisticas.
  - `missing_report`: ranking de nulos por coluna.
  - `correlations`: correlacoes Pearson/Spearman gerais ou contra um target.
  - `group_summary`: metricas numericas por categoria/cohort/segmento.
  - `outlier_report`: outliers por regra IQR.
  - `target_analysis`: relacao exploratoria entre variaveis e um target.
  - `forecast_baseline`: previsao naive/media movel.
  - `forecast_arima`: previsao ARIMA via statsmodels.
  - `forecast_prophet`: previsao Prophet quando a dependencia estiver instalada.
  - `predictive_model`: modelo exploratorio scikit-learn para classificacao/regressao.
  - `plot_correlation_heatmap`: heatmap PNG de correlacoes.
  - `plot_missingness`: grafico PNG de nulos.
  - `plot_distribution`: histograma/barplot PNG de uma coluna.
  - `plot_group_metric`: metrica por grupo em PNG.
  - `plot_time_series`: serie temporal em PNG.
  - `plot_interactive_scatter`: scatter interativo em HTML.
  - `discover_large_tables`: descobre CSV/Parquet grandes para consultas locais.
  - `large_table_schema`: inspeciona schema/amostra com DuckDB.
  - `query_large_tables`: roda SQL read-only em CSV/Parquet grandes via DuckDB.
  - `write_file`: cria ou substitui arquivos dentro do workspace.
  - `run_command`: roda apenas comandos permitidos.
- Protecoes basicas:
  - bloqueio de path traversal fora do workspace.
  - bloqueio de arquivos muito grandes.
  - allowlist para comandos de shell.
  - modo `dry_run` para inspecionar sem escrever.
- Testes unitarios das regras de seguranca das ferramentas.

Esse e o caminho recomendado para agente com ferramentas. O modelo `qwen/qwen2.5-coder-32b-instruct` responde rapido, mas a NVIDIA nao habilita tool use nele. O modelo `qwen/qwen3-coder-480b-a35b-instruct` tambem pode ser usado, mas tende a ser bem mais lento. O backend `agents` continua disponivel para OpenAI puro via Agents SDK, mas o backend `chat` funciona melhor com provedores compativeis como NVIDIA, Groq, OpenRouter, Hugging Face e Ollama.

## Uso

Por padrao o agente usa o diretorio atual como workspace e roda em `dry_run`.

```powershell
python -m coding_agent "Explique a estrutura deste projeto"
```

Comandos uteis de diagnostico:

```powershell
python -m coding_agent --version
python -m coding_agent --show-config
```

Para apontar para outro projeto:

```powershell
python -m coding_agent "Encontre bugs provaveis e sugira correcoes" --workspace C:\caminho\do\projeto
```

Para permitir escrita de arquivos:

```powershell
python -m coding_agent "Crie um teste unitario para tools" --workspace . --write
```

Para alteracoes em arquivos existentes, o agente deve preferir `apply_patch`. Ela exige que o texto antigo exista exatamente no arquivo e devolve um diff unificado antes de escrever. Com `AGENT_DRY_RUN=true`, nenhuma alteracao e gravada.

Modos de workflow:

```powershell
python -m coding_agent "Adicione uma melhoria pequena" --task-flow --write
python -m coding_agent "Revise riscos neste projeto" --review-only
python -m coding_agent "Planeje adicionar cache" --dry-run-plan
python -m coding_agent "Ajuste a CLI" --task-flow --write --target-test "py -m pytest"
python -m coding_agent "Revise a CLI" --review-only --task-log
```

- `--task-flow`: força o ciclo inspecionar, planejar, aplicar patch, verificar e revisar.
- `--review-only`: bloqueia escrita e foca em achados de review.
- `--dry-run-plan`: produz plano e diffs em dry-run, sem gravar.
- `--target-test`: adiciona comandos exatos de verificacao, se estiverem na allowlist.
- `--task-log`: registra prompt e resposta em markdown dentro do workspace.

Perfis de rotina:

- `--sql-mode`: foco em dialeto, grao, joins, CTEs, filtros, nulos e performance.
- `--python-ml`: foco em pandas, features, leakage, split, metricas, backtest e forecast.
- `--java-mode`: foco em contratos, classes, excecoes, colecoes, testes e riscos runtime.
- `--debug`: foco em reproducao, causa raiz, menor fix e verificacao estreita.
- `--analysis`: foco em metrica, grao, populacao, hipoteses, vieses e validacao.

## Interface Web

O projeto tambem inclui uma interface local para conversar com o agente, acompanhar progresso e ver arquivos/graficos gerados no workspace.

A tela permite escolher perfil SQL, Python/ML, Java, Debug ou Analysis; alternar entre review, dry-run e task-flow; habilitar escrita quando necessario; enviar arquivos; acompanhar eventos do run em tempo real; ver modelo e limite de tokens carregados; usar presets de tarefas; copiar respostas; e continuar um run anterior quando a resposta precisar seguir.

Para arquivos grandes, use o carregamento local de workspace e as consultas DuckDB. O agente nao precisa enviar o CSV inteiro ao modelo: ele descobre tabelas, inspeciona schema e executa SQL read-only localmente, retornando apenas resultados pequenos.
