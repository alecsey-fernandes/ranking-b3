"""
Importa histórico de lucro líquido da CVM (múltiplos anos) e persiste
como snapshots — alimentando diretamente a análise de consistência de
lucro (app/analysis/consistencia.py) e o endpoint /empresas/{ticker}/historico
já existentes, sem precisar de nenhum endpoint novo para consumir esses dados.

Diferente da coleta via Brapi (que persiste TODOS os indicadores de um
momento), essa importação é fundamentalista pura, histórica e de baixa
frequência (a CVM publica DFP uma vez por ano, com reapresentações
semanais possíveis) — por isso os snapshots gerados aqui têm
`preco_atual=0.0` como sentinela explícito: "sem preço, só fundamentos
históricos da CVM". Esses snapshots não devem ser usados como entrada do
`gerar_ranking()` (que precisa de preço); servem para
`calcular_consistencia_lucro()`, que não usa preço.
"""

from __future__ import annotations

import logging
from datetime import date

from app.data_sources.cvm_client import (
    CvmClientError,
    buscar_composicao_capital_por_ano,
    buscar_lucro_liquido_por_ano,
    buscar_patrimonio_liquido_por_ano,
    buscar_proventos_por_ano,
)
from app.data_sources.ticker_mapping import TICKER_PARA_CNPJ
from app.db.connection import get_connection, inicializar_schema
from app.db.repository import salvar_snapshot_indicadores
from app.models import Indicadores

logger = logging.getLogger(__name__)


async def importar_lucro_historico_cvm(
    tickers: list[str], anos: list[int]
) -> dict:
    """
    Para cada ano solicitado, baixa/parseia o DRE consolidado da CVM uma
    única vez (todas as empresas de uma vez) e persiste o lucro líquido
    dos tickers do nosso universo que tiverem CNPJ mapeado.

    Retorna um resumo por ano: quantos tickers foram encontrados vs.
    quantos ficaram de fora (sem CNPJ mapeado ou sem dado publicado
    naquele ano — ex: empresa criada/listada depois).
    """
    inicializar_schema()

    tickers_sem_cnpj = [t for t in tickers if t not in TICKER_PARA_CNPJ]
    if tickers_sem_cnpj:
        logger.warning(
            "Tickers sem CNPJ mapeado em ticker_mapping.py (ignorados): %s", tickers_sem_cnpj
        )

    resumo_por_ano: dict[int, dict] = {}

    for ano in anos:
        try:
            lucro_por_cnpj = await buscar_lucro_liquido_por_ano(ano)
        except CvmClientError as exc:
            logger.error("Falha ao importar DFP %d: %s", ano, exc)
            resumo_por_ano[ano] = {"sucesso": False, "erro": str(exc)}
            continue

        try:
            composicao_por_cnpj = await buscar_composicao_capital_por_ano(ano)
        except CvmClientError as exc:
            # Não aborta a importação do lucro líquido por causa disso —
            # composição de capital é um enriquecimento, não o dado principal.
            logger.warning("Falha ao importar composição de capital %d: %s", ano, exc)
            composicao_por_cnpj = {}

        try:
            patrimonio_por_cnpj = await buscar_patrimonio_liquido_por_ano(ano)
        except CvmClientError as exc:
            logger.warning("Falha ao importar patrimônio líquido %d: %s", ano, exc)
            patrimonio_por_cnpj = {}

        try:
            proventos_por_cnpj = await buscar_proventos_por_ano(ano)
        except CvmClientError as exc:
            logger.warning("Falha ao importar proventos (dividendos/JCP) %d: %s", ano, exc)
            proventos_por_cnpj = {}

        encontrados = []
        nao_encontrados = []
        suspeitos = []

        with get_connection() as conn:
            for ticker in tickers:
                cnpj = TICKER_PARA_CNPJ.get(ticker)
                if not cnpj:
                    continue

                lucro = lucro_por_cnpj.get(cnpj)
                if lucro is None:
                    nao_encontrados.append(ticker)
                    continue

                composicao = composicao_por_cnpj.get(cnpj)
                acoes_em_circulacao = composicao["acoes_em_circulacao"] if composicao else None
                acoes_dado_suspeito = composicao["suspeito"] if composicao else None
                if composicao and composicao["suspeito"]:
                    suspeitos.append(ticker)

                patrimonio_liquido = patrimonio_por_cnpj.get(cnpj)
                proventos = proventos_por_cnpj.get(cnpj)
                proventos_total = proventos["proventos_total"] if proventos else None

                # LPA/VPA só são calculados quando temos ações em
                # circulação E esse valor passou no teste de
                # plausibilidade — um `acoes_dado_suspeito=True` (ver
                # cvm_client.py) significaria LPA/VPA inflados ~1000x,
                # então preferimos deixar os campos vazios a entregar um
                # número silenciosamente errado para as estratégias.
                lpa = None
                vpa = None
                dividendo_por_acao = None
                if acoes_em_circulacao and not acoes_dado_suspeito and acoes_em_circulacao > 0:
                    if lucro is not None:
                        lpa = lucro / acoes_em_circulacao
                    if patrimonio_liquido is not None:
                        vpa = patrimonio_liquido / acoes_em_circulacao
                    if proventos_total is not None:
                        dividendo_por_acao = proventos_total / acoes_em_circulacao

                indicadores = Indicadores(
                    ticker=ticker,
                    nome=ticker,  # nome "de verdade" já deve estar cadastrado pela coleta via Brapi; upsert não sobrescreve com pior dado por acidente porque salvar_snapshot_indicadores faz upsert do nome também — ver observação no README sobre ordem de coleta recomendada
                    setor=None,
                    data_referencia=date(ano, 12, 31),
                    preco_atual=0.0,  # sentinela: sem preço, só fundamentos históricos (ver docstring do módulo)
                    lucro_liquido=lucro,
                    acoes_em_circulacao=acoes_em_circulacao,
                    acoes_dado_suspeito=acoes_dado_suspeito,
                    lpa=lpa,
                    vpa=vpa,
                    dividendo_por_acao=dividendo_por_acao,
                )
                salvar_snapshot_indicadores(conn, indicadores)
                encontrados.append(ticker)

        resumo_por_ano[ano] = {
            "sucesso": True,
            "tickers_importados": encontrados,
            "tickers_sem_dado_no_ano": nao_encontrados,
            "tickers_com_acoes_suspeitas": suspeitos,
        }
        logger.info(
            "DFP %d importado: %d tickers ok, %d sem dado, %d com ações suspeitas",
            ano, len(encontrados), len(nao_encontrados), len(suspeitos),
        )

    return {"tickers_sem_cnpj_mapeado": tickers_sem_cnpj, "por_ano": resumo_por_ano}
