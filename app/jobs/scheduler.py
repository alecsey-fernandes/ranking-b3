"""
Job de coleta periódica: busca indicadores atuais na Brapi, persiste um
snapshot histórico, recalcula o ranking e persiste esse resultado também.

Frequência recomendada: fundamentalistas de balanço mudam por
trimestre/ano (ITR/DFP), mas preço/múltiplos mudam todo dia. Rodar
DIARIAMENTE captura a variação de preço nos múltiplos com granularidade
suficiente para "consistência ao longo dos anos" sem sobrecarregar a
API gratuita da Brapi. Ajustável via `INTERVALO_COLETA_HORAS`.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import date

from apscheduler.schedulers.background import BackgroundScheduler

from app.config import pesos_padrao
from app.data_sources.brapi_client import BrapiClient, BrapiClientError
from app.db.connection import get_connection, inicializar_schema
from app.db.repository import salvar_snapshot_indicadores, salvar_snapshot_ranking
from app.ranking.aggregator import gerar_ranking
from app.strategies.bazin import BazinStrategy
from app.strategies.buffett_like import BuffettLikeStrategy
from app.strategies.graham import GrahamStrategy
from app.strategies.magic_formula import MagicFormulaStrategy
from app.strategies.peg_lynch import PegLynchStrategy
from app.strategies.piotroski import PiotroskiStrategy

logger = logging.getLogger(__name__)

ESTRATEGIAS_ATIVAS = [
    GrahamStrategy(),
    MagicFormulaStrategy(),
    BazinStrategy(),
    PiotroskiStrategy(),
    BuffettLikeStrategy(),
    PegLynchStrategy(),
]
INTERVALO_COLETA_HORAS = 24


async def coletar_e_persistir(tickers: list[str]) -> dict:
    """Executa uma coleta completa: busca dados, persiste snapshot de cada
    empresa, calcula o ranking do dia e persiste esse snapshot também.
    Retorna um resumo para log/monitoramento."""
    client = BrapiClient()
    try:
        empresas = await client.buscar_lote(tickers)
    except BrapiClientError as exc:
        logger.error("Falha na coleta: %s", exc)
        return {"sucesso": False, "erro": str(exc)}

    if not empresas:
        return {"sucesso": False, "erro": "nenhuma empresa retornada pela fonte de dados"}

    with get_connection() as conn:
        for empresa in empresas:
            salvar_snapshot_indicadores(conn, empresa)

        ranking = gerar_ranking(empresas, ESTRATEGIAS_ATIVAS, pesos_padrao)
        salvar_snapshot_ranking(conn, ranking, date.today(), pesos_padrao.model_dump())

    logger.info("Coleta concluída: %d empresas, %d no ranking", len(empresas), len(ranking))
    return {"sucesso": True, "empresas_coletadas": len(empresas), "empresas_ranqueadas": len(ranking)}


def _executar_coleta_sync(tickers: list[str]) -> None:
    """Wrapper síncrono, necessário porque APScheduler chama jobs de forma síncrona."""
    resultado = asyncio.run(coletar_e_persistir(tickers))
    if not resultado["sucesso"]:
        logger.warning("Job de coleta terminou com erro: %s", resultado.get("erro"))


def iniciar_scheduler(tickers: list[str]) -> BackgroundScheduler:
    """Inicializa o schema do banco (se necessário) e agenda a coleta periódica.
    Chamado no startup da aplicação (app/main.py)."""
    inicializar_schema()

    scheduler = BackgroundScheduler()
    scheduler.add_job(
        _executar_coleta_sync,
        "interval",
        hours=INTERVALO_COLETA_HORAS,
        args=[tickers],
        id="coleta_diaria",
        next_run_time=None,  # não roda imediatamente no startup; só no próximo intervalo. Ver /coleta/executar para rodar na hora.
    )
    scheduler.start()
    logger.info("Scheduler iniciado: coleta a cada %dh para %d tickers", INTERVALO_COLETA_HORAS, len(tickers))
    return scheduler
