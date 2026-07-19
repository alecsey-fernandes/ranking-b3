"""
Combina o preço mais recente (coletado da B3) com os fundamentos mais
recentes (importados da CVM) num único `Indicadores` por ticker — a peça
que faltava para rodar `gerar_ranking()` inteiramente com dados
gratuitos, sem depender de plano pago da Brapi.

As duas fontes são persistidas em datas diferentes (preço é diário,
fundamentos são anuais), então cada uma vive em snapshots separados no
banco. Este módulo não persiste nada novo — só lê o que já está salvo e
monta o objeto combinado em memória, na hora de gerar o ranking.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date

from app.db.repository import (
    buscar_empresa,
    buscar_media_dividendo_5a,
    buscar_ultimo_preco_valido,
    buscar_ultimos_fundamentos_cvm,
)
from app.models import Indicadores

logger = logging.getLogger(__name__)


def montar_indicadores_para_ranking(conn: sqlite3.Connection, tickers: list[str]) -> list[Indicadores]:
    """
    Para cada ticker, busca o snapshot de preço mais recente (B3) e o
    snapshot de fundamentos mais recente (CVM) já persistidos, e monta
    um `Indicadores` combinado. Tickers sem preço OU sem fundamentos
    disponíveis são simplesmente omitidos do resultado (não é possível
    ranqueá-los ainda) — não geram erro.
    """
    resultado: list[Indicadores] = []

    for ticker in tickers:
        preco_row = buscar_ultimo_preco_valido(conn, ticker)
        fundamentos_row = buscar_ultimos_fundamentos_cvm(conn, ticker)

        if preco_row is None:
            logger.info("Ticker %s sem preço coletado ainda (rode /coleta/b3/precos) — omitido do ranking", ticker)
            continue
        if fundamentos_row is None:
            logger.info(
                "Ticker %s sem fundamentos CVM ainda (rode /coleta/cvm/lucro-historico) — omitido do ranking",
                ticker,
            )
            continue

        empresa_row = buscar_empresa(conn, ticker)
        nome = empresa_row["nome"] if empresa_row else ticker
        setor = empresa_row["setor"] if empresa_row else None

        dividendo_medio_5a = buscar_media_dividendo_5a(conn, ticker)

        resultado.append(
            Indicadores(
                ticker=ticker,
                nome=nome,
                setor=setor,
                data_referencia=date.fromisoformat(preco_row["data_referencia"]),
                preco_atual=preco_row["preco_atual"],
                lucro_liquido=fundamentos_row.get("lucro_liquido"),
                lpa=fundamentos_row.get("lpa"),
                vpa=fundamentos_row.get("vpa"),
                acoes_em_circulacao=fundamentos_row.get("acoes_em_circulacao"),
                acoes_dado_suspeito=(
                    bool(fundamentos_row["acoes_dado_suspeito"])
                    if fundamentos_row.get("acoes_dado_suspeito") is not None
                    else None
                ),
                dividend_yield=fundamentos_row.get("dividend_yield"),
                dividendo_medio_5a=dividendo_medio_5a,
                # EV/EBIT/ROIC/ROE ainda não têm fonte gratuita integrada
                # (ver README > "Limitações conhecidas") — ficam None, o
                # que faz a Fórmula Mágica marcar a empresa como não
                # elegível em vez de usar um dado ausente silenciosamente.
            )
        )

    return resultado
