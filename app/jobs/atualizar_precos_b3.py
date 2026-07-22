"""
Atualiza `preco_atual` no snapshot do dia de cada ticker, usando o preço
de fechamento mais recente disponível no arquivo COTAHIST da B3.

Como o merge de `salvar_snapshot_indicadores` agora é campo a campo (ver
app/db/repository.py), essa atualização pode rodar antes ou depois da
coleta via Brapi/CVM no mesmo dia sem apagar os outros indicadores já
salvos — só preenche/atualiza o preço.
"""

from __future__ import annotations

import logging
from datetime import date

from app.data_sources.b3_cotahist_client import B3ClientError, buscar_cotacoes_ano, preco_mais_recente_por_ticker
from app.db.connection import get_connection, inicializar_schema
from app.db.repository import salvar_snapshot_indicadores
from app.models import Indicadores

logger = logging.getLogger(__name__)


async def atualizar_precos_b3(tickers: list[str], ano: int | None = None) -> dict:
    """
    Busca o preço de fechamento mais recente de cada ticker no arquivo
    COTAHIST do ano informado (default: ano corrente) e persiste como
    snapshot de hoje — apenas o campo de preço é atualizado, os demais
    indicadores já salvos (via Brapi/CVM) são preservados.
    """
    inicializar_schema()
    ano_alvo = ano or date.today().year

    # Deduplica preservando a ordem — evita repetir o mesmo ticker no
    # resumo retornado quando a lista pedida tem repetições.
    tickers = list(dict.fromkeys(tickers))

    try:
        cotacoes = await buscar_cotacoes_ano(ano_alvo, tickers)
    except B3ClientError as exc:
        logger.error("Falha ao buscar cotações da B3 (%d): %s", ano_alvo, exc)
        return {"sucesso": False, "erro": str(exc)}

    precos_mais_recentes = preco_mais_recente_por_ticker(cotacoes)

    atualizados = []
    sem_cotacao = []

    with get_connection() as conn:
        for ticker in tickers:
            dado = precos_mais_recentes.get(ticker)
            if dado is None:
                sem_cotacao.append(ticker)
                continue

            data_pregao, preco = dado
            indicadores = Indicadores(
                ticker=ticker,
                nome=ticker,  # nome de verdade preservado se já existir (ver salvar_empresa)
                setor=None,
                data_referencia=date.today(),
                preco_atual=preco,
            )
            salvar_snapshot_indicadores(conn, indicadores)
            atualizados.append({"ticker": ticker, "preco": preco, "data_pregao_origem": data_pregao.isoformat()})

    logger.info("Preços B3 atualizados: %d ok, %d sem cotação", len(atualizados), len(sem_cotacao))
    return {"sucesso": True, "ano_consultado": ano_alvo, "atualizados": atualizados, "sem_cotacao": sem_cotacao}
