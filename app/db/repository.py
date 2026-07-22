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
    "ebitda", "receita_liquida", "patrimonio_liquido", "lpa", "vpa", "p_l", "p_vp", "ev_ebit",
    "ev_ebitda", "peg_ratio", "roe", "roic", "margem_liquida", "margem_ebit",
    "divida_liquida_ebitda", "liquidez_corrente", "dividend_yield",
    "acoes_em_circulacao", "acoes_dado_suspeito", "dividendo_por_acao",
    "ativo_total", "ativo_circulante", "passivo_circulante",
    "passivo_nao_circulante", "caixa_operacional", "lucro_bruto",
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
    Idempotente: rodar a coleta duas vezes no mesmo dia atualiza o
    snapshot em vez de duplicar (chave única ticker + data_referencia).

    Atualização é um MERGE campo a campo, não uma substituição total: se
    esta chamada trouxer um campo como None (ex: uma atualização que só
    tem preço, vinda da B3, sem os outros indicadores), o valor já salvo
    anteriormente para esse campo é preservado em vez de apagado. Isso
    permite combinar fontes diferentes (Brapi, CVM, B3) no mesmo snapshot
    do dia sem uma sobrescrever os dados da outra.
    """
    salvar_empresa(conn, indicadores)

    valores = [getattr(indicadores, campo) for campo in _CAMPOS_INDICADOR]
    colunas = ", ".join(_CAMPOS_INDICADOR)
    placeholders = ", ".join(["?"] * len(_CAMPOS_INDICADOR))
    atualizacoes = ", ".join(
        [f"{campo} = COALESCE(excluded.{campo}, indicador_snapshot.{campo})" for campo in _CAMPOS_INDICADOR]
    )

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


def buscar_empresa(conn: sqlite3.Connection, ticker: str) -> Optional[dict]:
    """Busca o cadastro básico (nome, setor) de uma empresa."""
    cursor = conn.execute("SELECT ticker, nome, setor FROM empresa WHERE ticker = ?", (ticker,))
    row = cursor.fetchone()
    return dict(row) if row else None


def buscar_media_dividendo_5a(conn: sqlite3.Connection, ticker: str) -> Optional[float]:
    """
    Calcula a média de `dividendo_por_acao` dos últimos 5 snapshots
    anuais persistidos (vindos da importação CVM) — o valor que a
    estratégia de Bazin (`app/strategies/bazin.py`) realmente usa
    (`dividendo_medio_5a`), em vez do dividendo de um único ano, que
    pode ser distorcido por um ano excepcionalmente bom ou ruim.

    Retorna None se não houver nenhum ano com dividendo registrado
    (empresa que não paga proventos, ou histórico ainda não importado).
    """
    cursor = conn.execute(
        """
        SELECT dividendo_por_acao FROM indicador_snapshot
        WHERE ticker = ? AND dividendo_por_acao IS NOT NULL
        ORDER BY data_referencia DESC
        LIMIT 5
        """,
        (ticker,),
    )
    valores = [row["dividendo_por_acao"] for row in cursor.fetchall()]
    if not valores:
        return None
    return sum(valores) / len(valores)


def buscar_ultimo_preco_valido(conn: sqlite3.Connection, ticker: str) -> Optional[dict]:
    """Snapshot mais recente com preço real (preco_atual > 0) — filtra os
    snapshots "sentinela" da CVM (preco_atual=0.0, só fundamentos)."""
    cursor = conn.execute(
        """
        SELECT * FROM indicador_snapshot
        WHERE ticker = ? AND preco_atual > 0
        ORDER BY data_referencia DESC
        LIMIT 1
        """,
        (ticker,),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def buscar_ultimos_fundamentos_cvm(conn: sqlite3.Connection, ticker: str) -> Optional[dict]:
    """Snapshot mais recente com LPA calculado (vindo da importação CVM) —
    representa o ano fiscal mais recente com fundamentos completos e
    confiáveis (ações não suspeitas) disponíveis para esse ticker."""
    cursor = conn.execute(
        """
        SELECT * FROM indicador_snapshot
        WHERE ticker = ? AND lpa IS NOT NULL
        ORDER BY data_referencia DESC
        LIMIT 1
        """,
        (ticker,),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def buscar_fundamentos_cvm_ano_anterior(
    conn: sqlite3.Connection, ticker: str, data_referencia_atual: date
) -> Optional[dict]:
    """Snapshot de fundamentos CVM (LPA calculado) mais recente ANTERIOR a
    `data_referencia_atual` — usado para comparar ano-a-ano nos critérios
    do Piotroski F-Score (ver app/ranking/montagem.py). Não assume que o
    ano anterior é exatamente `data_referencia_atual - 1 ano`; pega o
    fundamento mais recente disponível antes da data atual, já que a
    importação CVM pode ter lacunas (ano sem dado publicado, empresa nova
    na bolsa etc.)."""
    cursor = conn.execute(
        """
        SELECT * FROM indicador_snapshot
        WHERE ticker = ? AND lpa IS NOT NULL AND data_referencia < ?
        ORDER BY data_referencia DESC
        LIMIT 1
        """,
        (ticker, data_referencia_atual.isoformat()),
    )
    row = cursor.fetchone()
    return dict(row) if row else None


def buscar_historico_fundamentos_cvm(conn: sqlite3.Connection, ticker: str, anos: int = 5) -> list[dict]:
    """Até `anos` snapshots de fundamentos CVM (LPA calculado) mais
    recentes de um ticker, mais recente primeiro — usado para avaliar
    consistência de ROE e margem ao longo do tempo (Buffett-like, ver
    app/ranking/montagem.py). Inclui o ano mais recente (o mesmo que
    `buscar_ultimos_fundamentos_cvm` retornaria)."""
    cursor = conn.execute(
        """
        SELECT * FROM indicador_snapshot
        WHERE ticker = ? AND lpa IS NOT NULL
        ORDER BY data_referencia DESC
        LIMIT ?
        """,
        (ticker, anos),
    )
    return [dict(row) for row in cursor.fetchall()]


def listar_tickers_com_historico(conn: sqlite3.Connection) -> list[str]:
    cursor = conn.execute("SELECT DISTINCT ticker FROM indicador_snapshot ORDER BY ticker")
    return [row["ticker"] for row in cursor.fetchall()]
