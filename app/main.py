from __future__ import annotations

import logging

from fastapi import FastAPI, HTTPException, Query

from app.analysis.consistencia import calcular_consistencia_lucro
from app.config import PesosEstrategias, pesos_padrao
from app.data_sources.brapi_client import BrapiClient, BrapiClientError
from app.data_sources.cvm_client import CvmClientError, inspecionar_arquivo_dfp, listar_arquivos_dfp
from app.data_sources.cvm_fca_client import CvmFcaClientError, inspecionar_valor_mobiliario
from app.data_sources.ticker_mapping import TICKER_PARA_CNPJ
from app.db.connection import get_connection
from app.db.repository import buscar_evolucao_ranking, buscar_historico_indicadores
from app.jobs.atualizar_precos_b3 import atualizar_precos_b3
from app.jobs.importar_cvm import importar_lucro_historico_cvm
from app.jobs.scheduler import coletar_e_persistir, iniciar_scheduler
from app.ranking.aggregator import gerar_ranking
from app.strategies.bazin import BazinStrategy
from app.strategies.graham import GrahamStrategy
from app.strategies.magic_formula import MagicFormulaStrategy

logging.basicConfig(level=logging.INFO)

app = FastAPI(
    title="Ranking B3 - Análise Fundamentalista",
    description="Ranking de empresas da B3 combinando múltiplas estratégias fundamentalistas em média ponderada, com histórico persistido.",
    version="0.2.0",
)

# Universo padrão do MVP: lista pequena e fixa para validar o pipeline.
# Em produção isso vem de uma tabela `empresas` populada a partir da
# listagem oficial da B3 (pregão + fracionário), não hardcoded.
UNIVERSO_MVP = [
    "PETR4", "VALE3", "ITUB4", "BBDC4", "ABEV3",
    "WEGE3", "BBAS3", "B3SA3", "RENT3", "EGIE3",
]

ESTRATEGIAS_MVP = [GrahamStrategy(), MagicFormulaStrategy(), BazinStrategy()]


@app.on_event("startup")
def _on_startup():
    iniciar_scheduler(UNIVERSO_MVP)


@app.get("/diagnostico/cvm/dfp-arquivos")
async def diagnostico_dfp_arquivos(
    ano: int = Query(default=2024, description="Ano do DFP a inspecionar"),
):
    """
    ⚠️ Endpoint de DIAGNÓSTICO. Lista todos os arquivos dentro do zip
    anual do DFP da CVM (o mesmo já usado para lucro líquido) — usado
    para descobrir o nome exato do arquivo de composição de capital
    (candidato à quantidade de ações emitidas), que ainda não foi
    confirmado.
    """
    try:
        nomes = await listar_arquivos_dfp(ano)
    except CvmClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ano": ano, "total_arquivos": len(nomes), "arquivos": sorted(nomes)}


@app.get("/diagnostico/cvm/dfp-arquivo")
async def diagnostico_dfp_arquivo(
    ano: int = Query(default=2024, description="Ano do DFP a inspecionar"),
    nome_arquivo: str = Query(..., description="Nome exato do arquivo dentro do zip (ver /diagnostico/cvm/dfp-arquivos)"),
    tickers: list[str] = Query(default=UNIVERSO_MVP, description="Filtra a amostra pelos CNPJs desses tickers"),
):
    """
    ⚠️ Endpoint de DIAGNÓSTICO, não de produção. Inspeciona um arquivo
    específico dentro do zip do DFP (cabeçalho real + amostra de linhas),
    para confirmação humana antes de construirmos o parser definitivo de
    quantidade de ações. Não interpreta nenhum campo ainda.
    """
    cnpjs = {TICKER_PARA_CNPJ[t] for t in tickers if t in TICKER_PARA_CNPJ}
    try:
        resultado = await inspecionar_arquivo_dfp(ano, nome_arquivo, cnpjs_filtro=cnpjs or None)
    except CvmClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return resultado


@app.get("/diagnostico/cvm/fca-valor-mobiliario")
async def diagnostico_fca_valor_mobiliario(
    ano: int = Query(default=2025, description="Ano do Formulário Cadastral a inspecionar"),
    tickers: list[str] = Query(default=UNIVERSO_MVP, description="Filtra a amostra pelos CNPJs desses tickers"),
):
    """
    ⚠️ Endpoint de DIAGNÓSTICO, não de produção. Baixa o Formulário
    Cadastral (FCA) da CVM — seção Valores Mobiliários, candidata a fonte
    de quantidade de ações emitidas por empresa — e devolve o cabeçalho
    real do CSV (nomes exatos das colunas) + uma amostra de linhas, para
    confirmação humana antes de construirmos o parser definitivo.

    Não interpreta nenhum campo ainda: apenas expõe o que existe. Veja
    `app/data_sources/cvm_fca_client.py` para o motivo dessa cautela.
    """
    cnpjs = {TICKER_PARA_CNPJ[t] for t in tickers if t in TICKER_PARA_CNPJ}
    try:
        resultado = await inspecionar_valor_mobiliario(ano, cnpjs_filtro=cnpjs or None)
    except CvmFcaClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return resultado


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/ranking")
async def obter_ranking(
    tickers: list[str] = Query(default=UNIVERSO_MVP, description="Lista de tickers a incluir no ranking"),
):
    client = BrapiClient()
    try:
        empresas = await client.buscar_lote(tickers)
    except BrapiClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if not empresas:
        raise HTTPException(status_code=404, detail="Nenhuma empresa encontrada para os tickers informados")

    ranking = gerar_ranking(empresas, ESTRATEGIAS_MVP, pesos_padrao)
    return {"total_empresas": len(ranking), "pesos_aplicados": pesos_padrao.model_dump(), "ranking": ranking}


@app.post("/coleta/executar")
async def executar_coleta_manual(
    tickers: list[str] = Query(default=UNIVERSO_MVP, description="Tickers a coletar e persistir agora"),
):
    """Dispara a coleta + persistência imediatamente, sem esperar o próximo
    ciclo do scheduler. Útil para popular histórico rapidamente em dev/teste
    ou para forçar uma atualização fora do horário programado."""
    resultado = await coletar_e_persistir(tickers)
    if not resultado["sucesso"]:
        raise HTTPException(status_code=502, detail=resultado.get("erro", "falha desconhecida na coleta"))
    return resultado


@app.post("/coleta/b3/precos")
async def atualizar_precos(
    tickers: list[str] = Query(default=UNIVERSO_MVP, description="Tickers a atualizar"),
    ano: int | None = Query(default=None, description="Ano do arquivo COTAHIST a consultar (default: ano corrente)"),
):
    """
    Busca o preço de fechamento mais recente de cada ticker no arquivo
    de cotações históricas da B3 (gratuito, oficial) e persiste como
    snapshot de hoje. Complementa a importação de lucro da CVM — juntos,
    os dois preenchem preço + fundamentos sem depender de plano pago da Brapi.
    """
    resultado = await atualizar_precos_b3(tickers, ano)
    if not resultado["sucesso"]:
        raise HTTPException(status_code=502, detail=resultado.get("erro", "falha desconhecida ao buscar cotações"))
    return resultado


@app.post("/coleta/cvm/lucro-historico")
async def importar_lucro_historico(
    tickers: list[str] = Query(default=UNIVERSO_MVP, description="Tickers a importar"),
    anos: list[int] = Query(default=[2021, 2022, 2023, 2024, 2025], description="Anos a importar (um por vez, arquivo grande por ano)"),
):
    """
    Importa lucro líquido histórico direto da CVM (fonte oficial, gratuita)
    para os anos e tickers informados, e persiste como snapshots — os
    mesmos que alimentam /empresas/{ticker}/consistencia-lucro e
    /empresas/{ticker}/historico. Cada ano baixa um arquivo grande (todas
    as empresas do Brasil); a primeira chamada pode demorar bastante —
    chamadas seguintes para o mesmo ano usam cache local em disco.
    """
    resultado = await importar_lucro_historico_cvm(tickers, anos)
    return resultado


@app.get("/empresas/{ticker}/historico")
async def historico_empresa(ticker: str, limite: int = 40):
    """Histórico de indicadores persistidos para uma empresa, mais recente primeiro."""
    with get_connection() as conn:
        historico = buscar_historico_indicadores(conn, ticker.upper(), limite)
    if not historico:
        raise HTTPException(status_code=404, detail=f"Sem histórico persistido para {ticker}. Rode /coleta/executar primeiro.")
    return {"ticker": ticker.upper(), "total_snapshots": len(historico), "historico": historico}


@app.get("/empresas/{ticker}/consistencia-lucro")
async def consistencia_lucro_empresa(ticker: str):
    """
    Avalia a consistência do lucro líquido ao longo do histórico persistido:
    % de períodos com lucro positivo, % com crescimento período a período,
    e um score de estabilidade — o critério que o PO definiu como central
    para decisão de compra de longo prazo.
    """
    with get_connection() as conn:
        historico = buscar_historico_indicadores(conn, ticker.upper(), limite=100)

    if not historico:
        raise HTTPException(status_code=404, detail=f"Sem histórico persistido para {ticker}. Rode /coleta/executar primeiro.")

    # historico vem mais-recente-primeiro do repositório; a análise espera ordem cronológica
    lucros_cronologicos = [
        linha["lucro_liquido"] for linha in reversed(historico) if linha["lucro_liquido"] is not None
    ]
    resultado = calcular_consistencia_lucro(ticker.upper(), lucros_cronologicos)
    return resultado


@app.get("/ranking/evolucao")
async def evolucao_ranking_empresa(ticker: str, limite: int = 40):
    """Evolução da posição e score de uma empresa no ranking ao longo do tempo."""
    with get_connection() as conn:
        evolucao = buscar_evolucao_ranking(conn, ticker.upper(), limite)
    if not evolucao:
        raise HTTPException(status_code=404, detail=f"Sem histórico de ranking para {ticker}. Rode /coleta/executar primeiro.")
    return {"ticker": ticker.upper(), "total_snapshots": len(evolucao), "evolucao": evolucao}
