# Ranking B3 — Análise Fundamentalista

Plataforma de ranqueamento de empresas listadas na B3, combinando múltiplas
estratégias de análise fundamentalista em uma média ponderada configurável,
com histórico persistido para acompanhar a consistência de resultados ao
longo do tempo.

## Status: MVP com persistência histórica

Implementado nesta etapa:
- Coleta de dados via [Brapi](https://brapi.dev) (`app/data_sources/brapi_client.py`)
- 3 estratégias: **Fórmula de Graham**, **Fórmula Mágica (Greenblatt)** e **Bazin**
- Normalização por percentil + agregação ponderada (`app/ranking/`)
- **Persistência histórica** de indicadores e rankings (`app/db/`)
- **Análise de consistência de lucro** ao longo do tempo (`app/analysis/consistencia.py`)
- **Job agendado** de coleta diária (`app/jobs/scheduler.py`)
- API FastAPI com endpoints de ranking, histórico e consistência
- Testes unitários com dados simulados (`tests/`)

## Como rodar

```bash
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate
pip install -r requirements.txt

# opcional: registre-se em brapi.dev para um token com rate limit maior
export BRAPI_TOKEN=seu_token_aqui

uvicorn app.main:app --reload
```

Acesse `http://localhost:8000/docs` (Swagger automático) ou chame direto:

```
GET  /ranking                                  # ranking atual (calculado on-the-fly, não persiste sozinho)
POST /coleta/executar                          # dispara coleta + persistência agora (usar antes de consultar histórico)
GET  /empresas/{ticker}/historico               # indicadores persistidos ao longo do tempo
GET  /empresas/{ticker}/consistencia-lucro      # score de consistência de lucro (0-100)
GET  /ranking/evolucao?ticker=PETR4             # evolução da posição/score no ranking
```

Fluxo típico para começar a ver histórico: chame `POST /coleta/executar`
algumas vezes em dias diferentes (ou ajuste `DATABASE_PATH` e insira dados
de teste) — o scheduler automático roda a cada 24h em produção, mas não
dispara na hora do startup.

## Rodar os testes

```bash
pytest tests/ -v
```

## Arquitetura

```
app/
  models.py                 # Indicadores, ResultadoEstrategia, RankingFinal
  config.py                  # Pesos das estratégias e settings gerais
  data_sources/
    brapi_client.py          # Coleta + mapeamento Brapi -> Indicadores
  strategies/
    base.py                   # Interface Estrategia (toda estratégia nova implementa isso)
    graham.py
    magic_formula.py
    bazin.py
  ranking/
    normalizer.py              # Score bruto -> percentil 0-100
    aggregator.py               # Percentis + pesos -> ranking final
  db/
    connection.py                # Conexão SQLite + schema (empresa, indicador_snapshot, ranking_snapshot)
    repository.py                 # Salvar/consultar snapshots — único ponto de acesso a dados
  analysis/
    consistencia.py                # Consistência de lucro ao longo do histórico persistido
  jobs/
    scheduler.py                    # Coleta periódica (APScheduler) que persiste snapshots
  main.py                    # API FastAPI
tests/
  test_pipeline.py           # Estratégias + ranking
  test_persistencia.py       # Persistência + consistência de lucro
```

## Modelo de dados histórico

- **`empresa`**: cadastro básico (ticker, nome, setor).
- **`indicador_snapshot`**: um registro por (empresa, data) com todos os
  indicadores brutos daquele momento — a base para calcular consistência
  de lucro, evolução de ROE, etc. Chave única `(ticker, data_referencia)`
  torna a coleta idempotente (rodar duas vezes no mesmo dia atualiza, não duplica).
- **`ranking_snapshot`**: um registro por (empresa, data) com o resultado
  do ranking ponderado daquele dia, incluindo os pesos usados (auditoria —
  permite saber se uma mudança na posição veio de mudança nos fundamentos
  ou de mudança nos pesos configurados).

## Consistência de lucro

`app/analysis/consistencia.py` calcula, a partir do histórico persistido:
- % de períodos com lucro líquido positivo
- % de períodos com crescimento em relação ao período anterior
- coeficiente de variação (estabilidade) do lucro
- um **score de consistência (0-100)** combinando os três sinais

Precisa de pelo menos 3 snapshots para gerar um resultado `confiavel=True`
— com menos dados, retorna `confiavel=False` em vez de um número enganoso.
Esse score ainda não entra automaticamente no ranking ponderado (é uma
estratégia adicional candidata — ver "Limitações conhecidas" abaixo).

## Fonte de dados fundamentalistas: CVM (gratuita, oficial)

A Brapi deixou de oferecer os módulos fundamentalistas (`defaultKeyStatistics`,
`financialData`) de graça além de 4 tickers de teste — ver decisão registrada
abaixo em "Histórico de decisões". Migramos o **histórico de lucro líquido**
para a fonte oficial e gratuita da CVM (Comissão de Valores Mobiliários).

- **`app/data_sources/cvm_client.py`**: baixa e faz parsing dos arquivos
  anuais de DFP (Demonstrações Financeiras Padronizadas) publicados pela
  CVM em `dados.cvm.gov.br`. Cada arquivo cobre TODAS as companhias
  abertas do Brasil daquele ano — por isso a estratégia é baixar uma vez
  por ano (com cache local em `cache_cvm/`) e indexar por CNPJ em memória,
  em vez de uma requisição por empresa.
- **`app/data_sources/ticker_mapping.py`**: mapeia ticker (B3) -> CNPJ,
  porque a CVM identifica empresas por CNPJ, não por código de negociação.
  ⚠️ **Os CNPJs precisam ser conferidos** contra o Formulário Cadastral da
  CVM ou a página de cada empresa em b3.com.br antes de confiar nos dados
  em produção — este ambiente de desenvolvimento não teve acesso à
  internet para validar cada um.
- **`app/jobs/importar_cvm.py`**: orquestra a importação de múltiplos anos
  e persiste como snapshots — os mesmos que já alimentam
  `/empresas/{ticker}/consistencia-lucro` e `/empresas/{ticker}/historico`,
  sem precisar de nenhum endpoint novo para consumir esse dado.

**Disparar a importação**: `POST /coleta/cvm/lucro-historico?tickers=PETR4&anos=2021&anos=2022&anos=2023&anos=2024&anos=2025`
— a primeira chamada de cada ano é lenta (baixa um arquivo grande com
todas as empresas do Brasil); chamadas seguintes usam cache em disco.

### O que a CVM NÃO resolve (Fase 2, ainda não implementada)

A CVM cuida de balanços, não de cotação de mercado — ela **não publica
preço de ação nem quantidade de ações emitidas**. Isso significa que
Graham, Fórmula Mágica e Bazin (que precisam de preço e de indicadores
"por ação") continuam precisando de uma fonte separada. A B3 disponibiliza
cotações históricas gratuitamente (arquivos "COTAHIST"), o que resolveria
o preço; quantidade de ações emitidas provavelmente está na seção "Dados
da Empresa/Composição do Capital" que a CVM passou a incluir no DFP/ITR
mais recentemente — mas essa integração ainda não foi construída. Até lá,
os snapshots importados da CVM têm `preco_atual=0.0` como sentinela
explícito ("sem preço, só fundamentos") e **não devem ser usados como
entrada do `gerar_ranking()`** — apenas de `calcular_consistencia_lucro()`,
que não depende de preço.

### Testes do parser CVM

`tests/test_cvm_client.py` valida a lógica de parsing (filtro de
ORDEM_EXERC, código de conta do lucro líquido, escala MIL/UNIDADE, etc.)
contra um CSV **sintético** que reproduz o formato documentado pela CVM —
este ambiente de desenvolvimento não teve acesso à internet para baixar e
testar contra o arquivo real. **Antes de confiar nos números em produção**,
valide `buscar_lucro_liquido_por_ano` para 2-3 empresas conhecidas contra
o demonstrativo publicado no site de RI de cada uma.

## Fonte de preço de mercado: B3 (gratuita, oficial) — Fase 2

A CVM cuida de balanços, não de cotação — quem publica preço
gratuitamente é a própria B3, nos arquivos **COTAHIST** (Cotações
Históricas), um arquivo texto de largura fixa por ano, cobrindo TODOS os
papéis negociados naquele ano.

- **`app/data_sources/b3_cotahist_client.py`**: baixa (com cache local em
  `cache_b3/`) e faz o parsing do arquivo anual, filtrando mercado à
  vista (`TPMERC='010'`) e lote padrão (`CODBDI='02'`) — evita pegar
  cotação de opções, termo, fracionário ou papéis com situação especial
  (recuperação judicial etc.) por engano.
- **`app/jobs/atualizar_precos_b3.py`**: busca o preço de fechamento mais
  recente de cada ticker e persiste no snapshot do dia.

**Disparar a atualização**: `POST /coleta/b3/precos?tickers=PETR4&tickers=VALE3`
— por padrão usa o ano corrente; a primeira chamada do ano baixa o
arquivo inteiro (pode demorar), chamadas seguintes usam cache.

### Merge de fontes no mesmo snapshot

Como CVM, B3 e Brapi podem preencher campos diferentes do mesmo dia, o
upsert em `salvar_snapshot_indicadores` (`app/db/repository.py`) faz um
**merge campo a campo**: um campo só é sobrescrito quando a fonte que
está gravando de fato traz um valor para ele — campos ausentes (`None`)
preservam o que já estava salvo, em vez de apagar. Isso permite rodar
`/coleta/b3/precos` e `/coleta/cvm/lucro-historico` em qualquer ordem,
no mesmo dia, sem uma fonte apagar o que a outra já salvou.

### Testes

`tests/test_b3_cotahist_client.py` valida o parser contra linhas
sintéticas construídas byte a byte seguindo o layout oficial
(posições de coluna exatas, conforme documento técnico da B3) — mesma
ressalva do parser CVM: não testado contra o arquivo real por falta de
acesso à internet neste ambiente. **Confira o preço de 1-2 tickers em
produção** contra o valor publicado no site da B3 antes de confiar nos
números.

### O que ainda falta para o ranking ponderado completo (Fase 3)

Preço (B3) + lucro histórico (CVM) já cobrem a consistência de lucro e
uma parte do que as estratégias precisam. Mas Graham e Bazin também
usam **valores por ação** (LPA, VPA, dividendo por ação) — isso requer
saber a **quantidade de ações emitidas** de cada empresa, que nem CVM
DFP nem COTAHIST fornecem diretamente. Possíveis fontes: a seção "Dados
da Empresa/Composição do Capital" que a CVM passou a incluir no
DFP/ITR mais recentemente, ou o Formulário Cadastral (FCA). Ainda não
implementado.

## Migrando para Postgres

O MVP usa `sqlite3` (stdlib) por simplicidade — zero dependência externa
para começar a persistir histórico já. O SQL em `app/db/connection.py` é
ANSI padrão (sem função específica do SQLite), então migrar para Postgres
quando o volume justificar é trocar a camada de conexão, não a lógica:

1. Trocar `get_connection()` em `app/db/connection.py` para retornar uma
   conexão psycopg2 (ou usar SQLAlchemy, já estava no plano original).
2. Trocar os placeholders `?` por `%s` em `app/db/repository.py`.
3. Adicionar uma ferramenta de migração de schema (Alembic é o padrão com SQLAlchemy).

## Como adicionar uma nova estratégia

1. Criar `app/strategies/nome_da_estrategia.py`, herdando de `Estrategia`
   (`app/strategies/base.py`), implementando `calcular()`.
2. Adicionar o campo de peso correspondente em `PesosEstrategias`
   (`app/config.py`) e a entrada em `_ATRIBUTO_PESO_POR_ESTRATEGIA`
   (`app/ranking/aggregator.py`).
3. Registrar a instância da estratégia em `ESTRATEGIAS_MVP` (`app/main.py`)
   e `ESTRATEGIAS_ATIVAS` (`app/jobs/scheduler.py`).
4. Nenhuma mudança é necessária em `normalizer.py` — a normalização por
   percentil funciona genericamente para qualquer estratégia.

## Estratégias

| Estratégia | Peso definido | Status |
|---|---|---|
| Fórmula Mágica (Greenblatt) | 25% | ✅ implementada |
| Piotroski F-Score | 20% | pendente |
| Graham | 20% | ✅ implementada |
| Bazin (dividend yield) | 15% | ✅ implementada |
| Buffett-like (ROE consistente, margem, dívida) | 15% | pendente |
| PEG Ratio (Peter Lynch) | 5% | pendente |

### Bazin — detalhe da fonte de dividendo

A fórmula original de Bazin usa a **média de dividendos dos últimos 5
anos** (`dividendo_medio_5a` no modelo `Indicadores`), evitando distorção
por um dividendo extraordinário de um único ano. Esse campo ainda não é
preenchido pela Brapi básica — a estratégia hoje usa como **fallback** o
dividendo implícito no yield do último ano (`dividend_yield * preço`),
marcado explicitamente como proxy em `detalhes.fonte_dividendo`. Migrar
para a média de 5 anos de verdade requer integrar um endpoint de
histórico de proventos (Brapi tem um módulo pago para isso, ou dá para
calcular a partir do próprio histórico já persistido depois de alguns
anos de coleta).

## Limitações conhecidas / próximos passos

- **Score de consistência de lucro ainda não entra no ranking ponderado**
  — hoje é consultável separadamente via `/empresas/{ticker}/consistencia-lucro`.
  Decisão de PO: deveria virar uma "estratégia" a mais na média ponderada,
  ou um filtro de corte (ex: excluir do ranking empresas com score < X)?
- **ROIC não vem da Brapi** — Magic Formula usa ROE como proxy quando ROIC
  está ausente. Calcular ROIC de verdade exige capital investido, que vem
  do balanço patrimonial completo (CVM ou módulo adicional da Brapi).
- **Bazin usa yield de 1 ano como proxy da média de 5 anos** (ver acima).
- **Universo de empresas fixo** (`UNIVERSO_MVP` em `main.py`) — em produção
  deve vir de uma tabela populada a partir da listagem oficial da B3.
- **Coleta diária captura preço, mas fundamentos de balanço mudam por
  trimestre** — os snapshots diários vão ter muitos indicadores repetidos
  entre um trimestre e outro (normal, mas vale ter em mente ao interpretar
  o histórico: um "salto" de lucro só aparece quando a empresa publica o
  resultado, não todo dia).
- **Google Finance não foi integrado** — sem API pública oficial; scraping
  violaria os termos de uso.

## Histórico de decisões

- **Migração de Brapi para CVM (dados fundamentalistas)**: a Brapi deixou
  de oferecer os módulos `defaultKeyStatistics`/`financialData` de graça
  além de 4 tickers de teste (ver `app/data_sources/brapi_client.py`,
  ainda usado para preço/cotação simples). Optamos por migrar o histórico
  de lucro líquido para a CVM (gratuita, oficial, sem limite de
  requisições), aceitando o trade-off de mais trabalho de engenharia —
  ver seção "Fonte de dados fundamentalistas: CVM" acima.
- **Fase 2 — preço via B3 (COTAHIST)**: pelo mesmo motivo (Brapi paga),
  o preço de mercado passou a vir dos arquivos gratuitos de cotações
  históricas da própria B3, em vez da Brapi — ver seção "Fonte de preço
  de mercado: B3" acima.
