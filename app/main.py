from __future__ import annotations

import json
import logging
import uuid
from datetime import date
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException, Query
from fastapi.responses import FileResponse

from app.analysis.consistencia import calcular_consistencia_lucro
from app.backtest.calculo import (
    BacktestError,
    EventoSocietario,
    ItemCarteira,
    calcular_backtest_carteira,
    resultado_para_dict,
)
from app.backtest.schemas import CarteiraBacktestRequest
from app.config import PesosEstrategias, pesos_padrao
from app.data_sources.brapi_client import BrapiClient, BrapiClientError
from app.data_sources.cvm_client import CvmClientError, inspecionar_arquivo_dfp, listar_arquivos_dfp
from app.data_sources.cvm_fca_client import CvmFcaClientError, inspecionar_valor_mobiliario
from app.data_sources.cvm_fre_client import CvmFreClientError, inspecionar_arquivo_fre, listar_arquivos_fre
from app.data_sources.ticker_mapping import TICKER_PARA_CNPJ
from app.db.connection import get_connection
from app.db.repository import (
    buscar_backtest_job,
    buscar_eventos_societarios,
    buscar_evolucao_ranking,
    buscar_historico_indicadores,
    buscar_ultimo_preco_valido,
    concluir_backtest_job,
    criar_backtest_job,
    falhar_backtest_job,
)
from app.jobs.atualizar_precos_b3 import atualizar_precos_b3
from app.jobs.importar_cvm import importar_lucro_historico_cvm
from app.jobs.scheduler import coletar_e_persistir, iniciar_scheduler
from app.ranking.montagem import montar_indicadores_para_ranking
from app.ranking.aggregator import gerar_ranking
from app.strategies.bazin import BazinStrategy
from app.strategies.buffett_like import BuffettLikeStrategy
from app.strategies.graham import GrahamStrategy
from app.strategies.magic_formula import MagicFormulaStrategy
from app.strategies.peg_lynch import PegLynchStrategy
from app.strategies.piotroski import PiotroskiStrategy

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Ranking B3 - Análise Fundamentalista",
    description="Ranking de empresas da B3 combinando múltiplas estratégias fundamentalistas em média ponderada, com histórico persistido.",
    version="0.2.0",
)


def _resolver_pesos(
    peso_graham: float | None,
    peso_bazin: float | None,
    peso_piotroski: float | None,
    peso_buffett_like: float | None,
    peso_peg_lynch: float | None,
    peso_magic_formula: float | None,
) -> PesosEstrategias:
    """
    Resolve os pesos a usar no ranking: se NENHUM peso customizado foi
    informado, usa `pesos_padrao`. Se algum foi informado, exige TODOS
    os 6 (evita ambiguidade de "o que fazer com os que faltaram") e
    valida que a soma dá 100 — devolve 422 com mensagem clara se não der.
    """
    pesos_informados = [peso_graham, peso_bazin, peso_piotroski, peso_buffett_like, peso_peg_lynch, peso_magic_formula]
    if all(p is None for p in pesos_informados):
        return pesos_padrao

    if any(p is None for p in pesos_informados):
        raise HTTPException(
            status_code=400,
            detail=(
                "Para customizar os pesos, informe os 6 (peso_graham, peso_bazin, peso_piotroski, "
                "peso_buffett_like, peso_peg_lynch, peso_magic_formula). Se quiser usar o padrão, não informe nenhum."
            ),
        )

    pesos = PesosEstrategias(
        graham=peso_graham,
        bazin=peso_bazin,
        piotroski=peso_piotroski,
        buffett_like=peso_buffett_like,
        peg_lynch=peso_peg_lynch,
        magic_formula=peso_magic_formula,
    )
    try:
        pesos.validar_soma()
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return pesos

# Dashboard web (app/static/index.html) — HTML/CSS/JS puro, autocontido
# num único arquivo (sem CSS/JS separados, sem build step), consumindo os
# mesmos endpoints da API na mesma origem (sem precisar de CORS). Serve na
# raiz para abrir direto pela URL do Railway, sem interferir em /docs
# (Swagger) nem nos demais endpoints. `directory` é resolvido a partir
# deste arquivo (não do diretório de trabalho), para funcionar igual
# rodando localmente ou no Railway independente de onde o processo é
# iniciado.
_DIR_STATIC = Path(__file__).parent / "static"


@app.get("/", include_in_schema=False)
async def dashboard():
    return FileResponse(_DIR_STATIC / "index.html")


@app.get("/backtest", include_in_schema=False)
async def pagina_backtest():
    return FileResponse(_DIR_STATIC / "backtest.html")


# Universo padrão do MVP: lista pequena e fixa para validar o pipeline.
# Em produção isso vem de uma tabela `empresas` populada a partir da
# listagem oficial da B3 (pregão + fracionário), não hardcoded.
UNIVERSO_MVP = [
    "PETR4", "VALE3", "ITUB4", "BBDC4", "ABEV3",
    "WEGE3", "BBAS3", "B3SA3", "RENT3", "EGIE3",
]

ESTRATEGIAS_MVP = [
    GrahamStrategy(),
    MagicFormulaStrategy(),
    BazinStrategy(),
    PiotroskiStrategy(),
    BuffettLikeStrategy(),
    PegLynchStrategy(),
]


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
    limite_amostra: int = Query(default=20, description="Quantas linhas trazer na amostra"),
):
    """
    ⚠️ Endpoint de DIAGNÓSTICO, não de produção. Inspeciona um arquivo
    específico dentro do zip do DFP (cabeçalho real + amostra de linhas),
    para confirmação humana antes de construirmos o parser definitivo de
    quantidade de ações. Não interpreta nenhum campo ainda.
    """
    cnpjs = {TICKER_PARA_CNPJ[t] for t in tickers if t in TICKER_PARA_CNPJ}
    try:
        resultado = await inspecionar_arquivo_dfp(ano, nome_arquivo, cnpjs_filtro=cnpjs or None, limite_amostra=limite_amostra)
    except CvmClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return resultado


@app.get("/diagnostico/cvm/fre-arquivos")
async def diagnostico_fre_arquivos(
    ano: int = Query(default=2024, description="Ano do FRE a inspecionar"),
):
    """
    ⚠️ Endpoint de DIAGNÓSTICO. Lista todos os arquivos dentro do zip
    anual do Formulário de Referência (FRE) da CVM — usado para
    confirmar o nome exato do arquivo de distribuição de dividendos,
    candidato à fonte de dado para a estratégia de Bazin.
    """
    try:
        nomes = await listar_arquivos_fre(ano)
    except CvmFreClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    return {"ano": ano, "total_arquivos": len(nomes), "arquivos": sorted(nomes)}


@app.get("/diagnostico/cvm/fre-arquivo")
async def diagnostico_fre_arquivo(
    ano: int = Query(default=2024, description="Ano do FRE a inspecionar"),
    nome_arquivo: str = Query(..., description="Nome exato do arquivo dentro do zip (ver /diagnostico/cvm/fre-arquivos)"),
    tickers: list[str] = Query(default=UNIVERSO_MVP, description="Filtra a amostra pelos CNPJs desses tickers"),
):
    """
    ⚠️ Endpoint de DIAGNÓSTICO, não de produção. Inspeciona um arquivo
    específico dentro do zip do FRE (cabeçalho real + amostra de linhas),
    para confirmação humana antes de construirmos o parser definitivo de
    distribuição de dividendos.
    """
    cnpjs = {TICKER_PARA_CNPJ[t] for t in tickers if t in TICKER_PARA_CNPJ}
    try:
        resultado = await inspecionar_arquivo_fre(ano, nome_arquivo, cnpjs_filtro=cnpjs or None)
    except CvmFreClientError as exc:
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


@app.get("/pesos/padrao")
async def obter_pesos_padrao():
    """Pesos padrão de cada estratégia no ranking ponderado (somam 100).
    Usado pelo dashboard para preencher os campos de peso na primeira
    carga — para customizar, veja os parâmetros `peso_*` de /ranking e
    /ranking/dados-gratuitos."""
    return pesos_padrao.model_dump()


@app.get("/precos/atual")
async def obter_precos_atuais(
    tickers: list[str] = Query(..., description="Tickers para consultar o último preço já persistido"),
):
    """
    Último preço de fechamento já persistido de cada ticker (via
    `/coleta/b3/precos` ou `/coleta/executar`) — leitura pura do banco,
    não baixa nada da B3 na hora. Usado pela tela de BackTest para
    mostrar a cotação ao lado do campo de quantidade de cada ticker.
    Tickers sem preço persistido ainda voltam com `preco: null`.
    """
    resultado = {}
    with get_connection() as conn:
        for ticker in dict.fromkeys(t.upper() for t in tickers):  # dedup preservando ordem
            snapshot = buscar_ultimo_preco_valido(conn, ticker)
            resultado[ticker] = (
                {"preco": snapshot["preco_atual"], "data_referencia": snapshot["data_referencia"]}
                if snapshot
                else {"preco": None, "data_referencia": None}
            )
    return resultado


@app.get("/ranking")
async def obter_ranking(
    tickers: list[str] = Query(default=UNIVERSO_MVP, description="Lista de tickers a incluir no ranking"),
    peso_graham: float | None = Query(default=None, description="Peso da estratégia Graham (0-100). Informe os 6 pesos juntos, ou nenhum para usar o padrão."),
    peso_bazin: float | None = Query(default=None, description="Peso da estratégia Bazin (0-100)"),
    peso_piotroski: float | None = Query(default=None, description="Peso da estratégia Piotroski F-Score (0-100)"),
    peso_buffett_like: float | None = Query(default=None, description="Peso da estratégia Buffett-like (0-100)"),
    peso_peg_lynch: float | None = Query(default=None, description="Peso da estratégia PEG/Lynch (0-100)"),
    peso_magic_formula: float | None = Query(default=None, description="Peso da estratégia Fórmula Mágica (0-100)"),
):
    pesos = _resolver_pesos(
        peso_graham, peso_bazin, peso_piotroski, peso_buffett_like, peso_peg_lynch, peso_magic_formula
    )

    client = BrapiClient()
    try:
        empresas = await client.buscar_lote(tickers)
    except BrapiClientError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if not empresas:
        raise HTTPException(status_code=404, detail="Nenhuma empresa encontrada para os tickers informados")

    ranking = gerar_ranking(empresas, ESTRATEGIAS_MVP, pesos)
    return {"total_empresas": len(ranking), "pesos_aplicados": pesos.model_dump(), "ranking": ranking}


@app.get("/ranking/dados-gratuitos")
async def obter_ranking_dados_gratuitos(
    tickers: list[str] = Query(default=UNIVERSO_MVP, description="Lista de tickers a incluir no ranking"),
    peso_graham: float | None = Query(default=None, description="Peso da estratégia Graham (0-100). Informe os 6 pesos juntos, ou nenhum para usar o padrão."),
    peso_bazin: float | None = Query(default=None, description="Peso da estratégia Bazin (0-100)"),
    peso_piotroski: float | None = Query(default=None, description="Peso da estratégia Piotroski F-Score (0-100)"),
    peso_buffett_like: float | None = Query(default=None, description="Peso da estratégia Buffett-like (0-100)"),
    peso_peg_lynch: float | None = Query(default=None, description="Peso da estratégia PEG/Lynch (0-100)"),
    peso_magic_formula: float | None = Query(default=None, description="Peso da estratégia Fórmula Mágica (0-100)"),
):
    """
    Ranking calculado inteiramente a partir de fontes gratuitas já
    persistidas (preço via B3 + fundamentos via CVM), sem depender da
    Brapi. Requer que `/coleta/b3/precos` e `/coleta/cvm/lucro-historico`
    já tenham sido rodados antes para os tickers pedidos — tickers sem
    as duas coletas feitas são omitidos do resultado (não é erro).

    A Fórmula Mágica normalmente aparece como não aplicável a todas as
    empresas — EV/EBIT e ROIC ainda não têm fonte gratuita integrada
    (ver README).
    """
    pesos = _resolver_pesos(
        peso_graham, peso_bazin, peso_piotroski, peso_buffett_like, peso_peg_lynch, peso_magic_formula
    )

    with get_connection() as conn:
        empresas = montar_indicadores_para_ranking(conn, tickers)

    if not empresas:
        raise HTTPException(
            status_code=404,
            detail="Nenhum ticker com preço E fundamentos já coletados. Rode /coleta/b3/precos e /coleta/cvm/lucro-historico primeiro.",
        )

    ranking = gerar_ranking(empresas, ESTRATEGIAS_MVP, pesos)
    return {
        "total_empresas_com_dados_completos": len(empresas),
        "total_no_ranking": len(ranking),
        "pesos_aplicados": pesos.model_dump(),
        "ranking": ranking,
    }


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


def _mesclar_eventos_societarios(ticker: str, eventos_manuais: list) -> list[EventoSocietario]:
    """
    Combina os eventos societários já persistidos automaticamente (ver
    `evento_societario` em connection.py — hoje vazia, aguardando job de
    importação confirmado) com os informados manualmente no payload.
    Quando os dois têm um evento na MESMA data, o manual prevalece (serve
    de correção/override caso a fonte automática esteja errada ou
    incompleta para aquele ticker específico).
    """
    with get_connection() as conn:
        persistidos = buscar_eventos_societarios(conn, ticker.upper())

    datas_manuais = {evento.data for evento in eventos_manuais}
    automaticos = [
        EventoSocietario(data=date.fromisoformat(row["data_evento"]), fator=row["fator"])
        for row in persistidos
        if date.fromisoformat(row["data_evento"]) not in datas_manuais
    ]
    manuais = [EventoSocietario(data=evento.data, fator=evento.fator) for evento in eventos_manuais]
    return sorted(automaticos + manuais, key=lambda evento: evento.data)


@app.post("/backtest/carteira", status_code=202)
async def backtest_carteira(payload: CarteiraBacktestRequest, background_tasks: BackgroundTasks):
    """
    Simula o retorno de uma carteira desde uma data de início escolhida
    até uma data final (hoje, ou o pregão mais recente disponível, se
    `data_fim` não for informado): para cada ticker, busca o preço de
    fechamento oficial da B3 (COTAHIST) na data de início (ou no primeiro
    pregão disponível logo depois, se cair em fim de semana/feriado) e o
    preço na data final (ou no último pregão disponível antes dela), e
    calcula quanto o valor investido teria virado.

    ⚠️ Roda em SEGUNDO PLANO: baixar e processar o arquivo COTAHIST de
    um ano inteiro (todos os papéis negociados na B3, não só os pedidos)
    pode levar bastante tempo na primeira vez (sem cache) — tempo demais
    para caber dentro do timeout do proxy do Railway numa única
    requisição síncrona. Por isso este endpoint devolve NA HORA (202) um
    `job_id`; consulte o resultado em `GET /backtest/carteira/{job_id}`
    (repita a consulta a cada alguns segundos até `status` virar
    `concluido` ou `erro`).

    Etapa 2: dividendos/JCP sempre entram no cálculo —
    `reinvestir_dividendos` escolhe se viram caixa acumulado (padrão,
    mais rápido: não precisa baixar cotação extra nenhuma) ou são
    reinvestidos em novas ações (mais lento: busca o preço de fechamento
    do fim de cada ano com dividendo, para cada ticker). Desdobramentos/
    grupamentos/bonificações já persistidos automaticamente (ver
    `evento_societario`) são aplicados sem precisar informar nada — hoje
    essa tabela ainda está vazia, aguardando confirmação de uma fonte
    gratuita (CVM/FRE); enquanto isso, `eventos_societarios` no payload
    continua funcionando manualmente e tem prioridade sobre qualquer
    evento automático na mesma data (serve de correção). Ver
    `app/backtest/calculo.py` para o detalhe das limitações.
    """
    itens = [
        ItemCarteira(
            ticker=item.ticker,
            quantidade=item.quantidade,
            eventos_societarios=_mesclar_eventos_societarios(item.ticker, item.eventos_societarios or []),
        )
        for item in payload.itens
    ]

    job_id = str(uuid.uuid4())
    with get_connection() as conn:
        criar_backtest_job(conn, job_id, payload.model_dump_json())

    background_tasks.add_task(_executar_backtest_em_background, job_id, itens, payload)

    return {
        "job_id": job_id,
        "status": "processando",
        "consulte_em": f"/backtest/carteira/{job_id}",
        "aviso": (
            "O cálculo roda em segundo plano e pode levar de segundos a alguns minutos "
            "na primeira vez (sem cache local do ano pedido). Consulte o job_id acima "
            "em GET /backtest/carteira/{job_id} até o status virar 'concluido' ou 'erro'."
        ),
    }


async def _executar_backtest_em_background(job_id: str, itens: list[ItemCarteira], payload: CarteiraBacktestRequest) -> None:
    """Executado pelo BackgroundTasks depois da resposta do POST já ter
    sido enviada — atualiza a linha do job em `backtest_job` ao terminar
    (sucesso ou erro), nunca deixa um job travado em 'processando'."""
    try:
        resultado = await calcular_backtest_carteira(
            itens, payload.data_inicio, reinvestir_dividendos=payload.reinvestir_dividendos, data_fim=payload.data_fim
        )
        resposta = resultado_para_dict(resultado)
        resposta["nome_carteira"] = payload.nome_carteira
        resposta["data_fim_pedida"] = payload.data_fim.isoformat() if payload.data_fim else None
        with get_connection() as conn:
            concluir_backtest_job(conn, job_id, json.dumps(resposta))
    except BacktestError as exc:
        with get_connection() as conn:
            falhar_backtest_job(conn, job_id, str(exc))
    except Exception as exc:  # noqa: BLE001 — nunca deixar o job travado por um erro inesperado
        logger.exception("Erro inesperado no job de backtest %s", job_id)
        with get_connection() as conn:
            falhar_backtest_job(conn, job_id, f"Erro inesperado: {exc}")


@app.get("/backtest/carteira/{job_id}")
async def status_backtest_carteira(job_id: str):
    """Consulta o status/resultado de um job criado por POST
    /backtest/carteira. `status`: 'processando', 'concluido' ou 'erro'."""
    with get_connection() as conn:
        job = buscar_backtest_job(conn, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail=f"Job de backtest '{job_id}' não encontrado.")
    return job
