"""
Camada de conexão com o banco de dados histórico.

Usa `sqlite3` (stdlib) por padrão — zero dependência externa, funciona
imediatamente para desenvolvimento e para instâncias pequenas. Para
produção com maior volume/concorrência, trocar por Postgres é uma
mudança na camada de conexão, não na lógica: o SQL usado aqui é
padrão ANSI (sem funções específicas do SQLite), e o repositório
(`repository.py`) já isola todo acesso a dados dos endpoints. Ao migrar,
troque `get_connection()` para retornar uma conexão psycopg2/SQLAlchemy
e ajuste os placeholders `?` para `%s`.

Variável de ambiente `DATABASE_PATH` define o arquivo do banco
(default: `ranking_b3.db` no diretório de trabalho).
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager

DATABASE_PATH = os.environ.get("DATABASE_PATH", "ranking_b3.db")

SCHEMA = """
CREATE TABLE IF NOT EXISTS empresa (
    ticker TEXT PRIMARY KEY,
    nome TEXT NOT NULL,
    setor TEXT
);

CREATE TABLE IF NOT EXISTS indicador_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL REFERENCES empresa(ticker),
    data_referencia TEXT NOT NULL,  -- ISO date (YYYY-MM-DD)
    preco_atual REAL,
    valor_mercado REAL,
    valor_firma REAL,
    lucro_liquido REAL,
    ebit REAL,
    ebitda REAL,
    receita_liquida REAL,
    patrimonio_liquido REAL,
    lpa REAL,
    vpa REAL,
    p_l REAL,
    p_vp REAL,
    ev_ebit REAL,
    ev_ebitda REAL,
    peg_ratio REAL,
    roe REAL,
    roic REAL,
    margem_liquida REAL,
    margem_ebit REAL,
    divida_liquida_ebitda REAL,
    liquidez_corrente REAL,
    dividend_yield REAL,
    acoes_em_circulacao REAL,
    acoes_dado_suspeito INTEGER,
    dividendo_por_acao REAL,
    ativo_total REAL,
    ativo_circulante REAL,
    passivo_circulante REAL,
    passivo_nao_circulante REAL,
    caixa_operacional REAL,
    lucro_bruto REAL,
    caixa_e_equivalentes REAL,
    divida_financeira REAL,
    UNIQUE(ticker, data_referencia)
);

CREATE INDEX IF NOT EXISTS idx_indicador_ticker_data
    ON indicador_snapshot(ticker, data_referencia);

CREATE TABLE IF NOT EXISTS ranking_snapshot (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    data_calculo TEXT NOT NULL,  -- ISO date
    ticker TEXT NOT NULL REFERENCES empresa(ticker),
    score_final REAL NOT NULL,
    posicao INTEGER NOT NULL,
    scores_por_estrategia TEXT NOT NULL,  -- JSON: {"graham": 82.5, "magic_formula": 70.0, ...}
    pesos_aplicados TEXT NOT NULL,        -- JSON: pesos usados nesse cálculo (auditoria histórica)
    UNIQUE(ticker, data_calculo)
);

CREATE INDEX IF NOT EXISTS idx_ranking_ticker_data
    ON ranking_snapshot(ticker, data_calculo);
"""


def inicializar_schema() -> None:
    with get_connection() as conn:
        conn.executescript(SCHEMA)
        _migrar_colunas_novas(conn)


# `CREATE TABLE IF NOT EXISTS` acima só cria a tabela na primeira vez — não
# adiciona colunas a uma tabela que já existe (o caso do banco em produção
# no Railway, criado antes destas colunas existirem). Esta função cobre
# esse caso de forma idempotente: tenta adicionar cada coluna nova e ignora
# o erro "coluna já existe" quando ela já foi adicionada numa execução
# anterior. Toda vez que um campo novo for adicionado a `indicador_snapshot`
# depois do primeiro deploy, a coluna correspondente deve entrar aqui.
_COLUNAS_NOVAS_INDICADOR_SNAPSHOT = [
    ("caixa_e_equivalentes", "REAL"),
    ("divida_financeira", "REAL"),
]


def _migrar_colunas_novas(conn: sqlite3.Connection) -> None:
    for coluna, tipo in _COLUNAS_NOVAS_INDICADOR_SNAPSHOT:
        try:
            conn.execute(f"ALTER TABLE indicador_snapshot ADD COLUMN {coluna} {tipo}")
        except sqlite3.OperationalError as exc:
            if "duplicate column" not in str(exc).lower():
                raise


@contextmanager
def get_connection():
    conn = sqlite3.connect(DATABASE_PATH)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()
