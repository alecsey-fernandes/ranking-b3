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
  violaria os termos de uso. Alternativas: Brapi (em uso), Yahoo Finance
  (não oficial), dados abertos da CVM (oficial, mais trabalhoso de parsear).
