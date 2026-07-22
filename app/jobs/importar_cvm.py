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
    buscar_ativos_por_ano,
    buscar_caixa_operacional_por_ano,
    buscar_composicao_capital_por_ano,
    buscar_lucro_liquido_por_ano,
    buscar_passivos_por_ano,
    buscar_patrimonio_liquido_por_ano,
    buscar_proventos_por_ano,
    buscar_receita_lucro_bruto_por_ano,
)
from app.data_sources.cvm_fca_client import CvmFcaClientError, resolver_cnpjs_ausentes
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

    O CNPJ de cada ticker vem primeiro do dicionário fixo já confirmado
    manualmente (`TICKER_PARA_CNPJ`, app/data_sources/ticker_mapping.py);
    para qualquer ticker que não estiver lá, tenta resolver
    automaticamente via FCA da CVM (`resolver_cnpjs_ausentes`) antes de
    desistir dele — isso evita ter que adicionar cada ticker novo manualmente
    ao dicionário fixo só para rodar a importação.

    Retorna um resumo por ano: quantos tickers foram encontrados vs.
    quantos ficaram de fora (sem CNPJ mapeado, mesmo após tentar resolver
    via FCA, ou sem dado publicado naquele ano — ex: empresa criada/listada
    depois).
    """
    inicializar_schema()

    # Deduplica preservando a ordem — evita baixar/persistir o mesmo
    # ticker mais de uma vez quando a lista pedida tem repetições.
    tickers = list(dict.fromkeys(tickers))

    cnpj_por_ticker = {t: TICKER_PARA_CNPJ[t] for t in tickers if t in TICKER_PARA_CNPJ}
    tickers_sem_cnpj_fixo = [t for t in tickers if t not in cnpj_por_ticker]

    tickers_resolvidos_via_fca: list[str] = []
    tickers_sem_cnpj: list[str] = tickers_sem_cnpj_fixo
    if tickers_sem_cnpj_fixo:
        try:
            # Tenta resolver usando os próprios anos pedidos como candidatos
            # (o cadastro FCA de um ano reflete os tickers vigentes naquele
            # ano) — sem precisar baixar um ano extra além dos já pedidos.
            resolvidos, tickers_sem_cnpj = await resolver_cnpjs_ausentes(tickers_sem_cnpj_fixo, anos)
            cnpj_por_ticker.update(resolvidos)
            tickers_resolvidos_via_fca = sorted(resolvidos)
            if tickers_resolvidos_via_fca:
                logger.info(
                    "CNPJ resolvido via FCA para %d ticker(s) sem entrada em ticker_mapping.py: %s",
                    len(tickers_resolvidos_via_fca), tickers_resolvidos_via_fca,
                )
        except CvmFcaClientError as exc:
            logger.error("Falha ao tentar resolver CNPJs via FCA: %s", exc)
            # Não aborta a importação inteira por isso — os tickers com
            # CNPJ já conhecido (fixo) continuam sendo importados normalmente.

    if tickers_sem_cnpj:
        logger.warning(
            "Tickers sem CNPJ mapeado, mesmo após tentar resolver via FCA (ignorados): %s",
            tickers_sem_cnpj,
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

        try:
            ativos_por_cnpj = await buscar_ativos_por_ano(ano)
        except CvmClientError as exc:
            logger.warning("Falha ao importar ativos (BPA) %d: %s", ano, exc)
            ativos_por_cnpj = {}

        try:
            passivos_por_cnpj = await buscar_passivos_por_ano(ano)
        except CvmClientError as exc:
            logger.warning("Falha ao importar passivos (BPP) %d: %s", ano, exc)
            passivos_por_cnpj = {}

        try:
            caixa_operacional_por_cnpj = await buscar_caixa_operacional_por_ano(ano)
        except CvmClientError as exc:
            logger.warning("Falha ao importar caixa operacional (DFC) %d: %s", ano, exc)
            caixa_operacional_por_cnpj = {}

        try:
            receita_lucro_bruto_por_cnpj = await buscar_receita_lucro_bruto_por_ano(ano)
        except CvmClientError as exc:
            logger.warning("Falha ao importar receita/lucro bruto (DRE) %d: %s", ano, exc)
            receita_lucro_bruto_por_cnpj = {}

        encontrados = []
        nao_encontrados = []
        suspeitos = []

        with get_connection() as conn:
            for ticker in tickers:
                cnpj = cnpj_por_ticker.get(ticker)
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

                ativos = ativos_por_cnpj.get(cnpj) or {}
                passivos = passivos_por_cnpj.get(cnpj) or {}
                receita_lucro_bruto = receita_lucro_bruto_por_cnpj.get(cnpj) or {}
                caixa_operacional = caixa_operacional_por_cnpj.get(cnpj)

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
                    patrimonio_liquido=patrimonio_liquido,
                    dividendo_por_acao=dividendo_por_acao,
                    ativo_total=ativos.get("ativo_total"),
                    ativo_circulante=ativos.get("ativo_circulante"),
                    passivo_circulante=passivos.get("passivo_circulante"),
                    passivo_nao_circulante=passivos.get("passivo_nao_circulante"),
                    caixa_operacional=caixa_operacional,
                    lucro_bruto=receita_lucro_bruto.get("lucro_bruto"),
                    receita_liquida=receita_lucro_bruto.get("receita_liquida"),
                    # Fórmula Mágica (ver app/strategies/magic_formula.py e
                    # a ressalva sobre plano de contas divergente em
                    # cvm_client.py, especialmente para dívida financeira).
                    ebit=receita_lucro_bruto.get("ebit"),
                    caixa_e_equivalentes=ativos.get("caixa_e_equivalentes"),
                    divida_financeira=passivos.get("divida_financeira"),
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

    return {
        "tickers_sem_cnpj_mapeado": tickers_sem_cnpj,
        "tickers_cnpj_resolvido_via_fca": tickers_resolvidos_via_fca,
        "por_ano": resumo_por_ano,
    }
