"""
Repositório de acesso a dados históricos. Toda a lógica de SQL fica
isolada aqui — endpoints e jobs nunca montam query diretamente.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date
from typing import Optional

from app.models import Indicadores, RankingFinal

_CAMPOS_INDICADOR = [
    "preco_atual", "valor_mercado", "valor_firma", "lucro_liquido", "ebit",
    "ebitda", "receita_liquida", "lpa", "vpa", "p_l", "p_vp", "ev_ebit",
    "ev_ebitda", "peg_ratio", "roe", "roic", "margem_liquida", "margem_ebit",
    "divida_liquida_ebitda", "liquidez_corrente", "dividend_yield",
]


def salvar_empresa(conn: sqlite3.Connection, indicadores: Indicadores) -> None:
    """Upsert do cadastro básico da empresa (idempotente).
    Não sobrescreve um nome/setor já cadastrado com um placeholder: se o
    `nome` recebido for igual ao próprio ticker (convenção usada por
    importações que só têm fundamentos, sem cadastro completo — ver
    app/jobs/importar_cvm.py), mantém o nome já existente."""
    conn.execute(
        """
        INSERT INTO empresa (ticker, nome, setor) VALUES (?, ?, ?)
        ON CONFLICT(ticker) DO UPDATE SET
            nome = CASE WHEN excluded.nome != empresa.ticker THEN excluded.nome ELSE empresa.nome END,
            setor = COALESCE(excluded.setor, empresa.setor)
        """,
        (indicadores.ticker, indicadores.nome, indicadores.setor),
    )


def salvar_snapshot_indicadores(conn: sqlite3.Connection, indicadores: Indicadores) -> None:
    """
    Salva um snapshot dos indicadores de uma empresa numa data de referência.
    Idempotente: rodar a coleta duas vezes no mesmo dia sobrescreve o
    snapshot em vez de duplicar (chave única ticker + data_referencia).
    """
    salvar_empresa(conn, indicadores)

    valores = [getattr(indicadores, campo) for campo in _CAMPOS_INDICADOR]
    colunas = ", ".join(_CAMPOS_INDICADOR)
    placeholders = ", ".join(["?"] * len(_CAMPOS_INDICADOR))
    atualizacoes = ", ".join([f"{campo} = excluded.{campo}" for campo in _CAMPOS_INDICADOR])

    conn.execute(
        f"""
        INSERT INTO indicador_snapshot (ticker, data_referencia, {colunas})
        VALUES (?, ?, {placeholders})
        ON CONFLICT(ticker, data_referencia) DO UPDATE SET {atualizacoes}
        """,
        [indicadores.ticker, indicadores.data_referencia.isoformat()] + valores,
    )


def salvar_snapshot_ranking(
    conn: sqlite3.Connection,
    ranking: list[RankingFinal],
    data_calculo: date,
    pesos_aplicados: dict,
) -> None:
    """Salva o ranking completo calculado numa data, para permitir ver a evolução da posição de cada empresa ao longo do tempo.
    Garante o cadastro da empresa (upsert) independentemente de
    `salvar_snapshot_indicadores` já ter sido chamado antes — a FK
    ticker -> empresa não pode depender da ordem de chamada dos dois
    métodos."""
    pesos_json = json.dumps(pesos_aplicados)
    for linha in ranking:
        conn.execute(
            """
            INSERT INTO empresa (ticker, nome, setor) VALUES (?, ?, ?)
            ON CONFLICT(ticker) DO UPDATE SET
                nome = CASE WHEN excluded.nome != empresa.ticker THEN excluded.nome ELSE empresa.nome END,
                setor = COALESCE(excluded.setor, empresa.setor)
            """,
            (linha.ticker, linha.nome, linha.setor),
        )
        conn.execute(
            """
            INSERT INTO ranking_snapshot
                (data_calculo, ticker, score_final, posicao, scores_por_estrategia, pesos_aplicados)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT(ticker, data_calculo) DO UPDATE SET
                score_final = excluded.score_final,
                posicao = excluded.posicao,
                scores_por_estrategia = excluded.scores_por_estrategia,
                pesos_aplicados = excluded.pesos_aplicados
            """,
            (
                data_calculo.isoformat(),
                linha.ticker,
                linha.score_final,
                linha.posicao,
                json.dumps(linha.scores_por_estrategia),
                pesos_json,
            ),
        )


def buscar_historico_indicadores(
    conn: sqlite3.Connection, ticker: str, limite: int = 40
) -> list[dict]:
    """Histórico de indicadores de uma empresa, mais recente primeiro."""
    cursor = conn.execute(
        """
        SELECT * FROM indicador_snapshot
        WHERE ticker = ?
        ORDER BY data_referencia DESC
        LIMIT ?
        """,
        (ticker, limite),
    )
    return [dict(row) for row in cursor.fetchall()]


def buscar_evolucao_ranking(conn: sqlite3.Connection, ticker: str, limite: int = 40) -> list[dict]:
    """Evolução da posição e score de uma empresa no ranking ao longo do tempo."""
    cursor = conn.execute(
        """
        SELECT data_calculo, score_final, posicao, scores_por_estrategia
        FROM ranking_snapshot
        WHERE ticker = ?
        ORDER BY data_calculo DESC
        LIMIT ?
        """,
        (ticker, limite),
    )
    linhas = []
    for row in cursor.fetchall():
        d = dict(row)
        d["scores_por_estrategia"] = json.loads(d["scores_por_estrategia"])
        linhas.append(d)
    return linhas


def listar_tickers_com_historico(conn: sqlite3.Connection) -> list[str]:
    cursor = conn.execute("SELECT DISTINCT ticker FROM indicador_snapshot ORDER BY ticker")
    return [row["ticker"] for row in cursor.fetchall()]
