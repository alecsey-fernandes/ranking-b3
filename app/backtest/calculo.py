"""
Cálculo de backtest de carteira: dado um conjunto de posições (ticker +
quantidade) e uma data de início, responde "quanto essa carteira teria
rendido se tivesse sido montada naquela data e mantida até hoje" — usando
exclusivamente o preço de fechamento oficial da B3 (COTAHIST), a mesma
fonte gratuita já usada em app/jobs/atualizar_precos_b3.py.

A lógica é dividida em duas camadas, seguindo o mesmo padrão já usado em
b3_cotahist_client.py (parsing puro vs. busca em rede):

- `montar_resultado_backtest`: função PURA, recebe as cotações já
  carregadas em memória e faz só a matemática. Testável diretamente com
  dados sintéticos, sem rede (ver tests/test_backtest_calculo.py).
- `calcular_backtest_carteira`: wrapper async fino que busca as cotações
  reais via `buscar_cotacoes_ano` e delega para a função pura acima.

⚠️ Limitações conhecidas desta primeira versão (etapa 1 do backtest —
expostas também na resposta da API, não só aqui):
- Não considera proventos (dividendos/JCP) recebidos no período — mede
  só a variação do preço da ação, não o retorno total. Fica para uma
  próxima etapa.
- Não ajusta automaticamente por desdobramentos/grupamentos (splits) —
  o COTAHIST reflete o preço nominal de cada pregão, então um
  desdobramento no meio do período distorceria o resultado.
- Preço de início usa o primeiro pregão disponível NA data pedida ou
  logo APÓS ela (dentro de uma janela de tolerância) — cobre o caso de
  a data cair num fim de semana/feriado. A data efetivamente usada vem
  sempre informada na resposta (`data_pregao_inicio`), nunca fica
  implícita.
- Carteira não é persistida nesta etapa — cada chamada recalcula do
  zero. Persistência (nomear e salvar carteiras para revisitar depois)
  é a próxima etapa planejada.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, timedelta

from app.data_sources.b3_cotahist_client import (
    B3ClientError,
    buscar_cotacoes_ano,
    preco_mais_recente_por_ticker,
)

logger = logging.getLogger(__name__)

# Quantos dias após a data pedida aceitamos buscar o 1º pregão disponível
# (cobre fim de semana / feriado prolongado sem tornar o resultado impreciso).
JANELA_TOLERANCIA_DIAS_INICIO = 10

# Quantos anos para trás tentar achar o preço "atual" se o ano corrente ainda
# não tiver nenhum pregão do ticker (ex: 1º de janeiro, antes do 1º pregão do ano).
ANOS_FALLBACK_PRECO_ATUAL = 2


class BacktestError(Exception):
    """Erro ao calcular o backtest da carteira (ex: falha ao baixar COTAHIST)."""


@dataclass
class ItemCarteira:
    ticker: str
    quantidade: float


@dataclass
class ResultadoItemBacktest:
    ticker: str
    quantidade: float
    status: str  # "ok" | "sem_cotacao_inicio" | "sem_cotacao_atual"
    data_pregao_inicio: date | None
    preco_inicio: float | None
    data_pregao_atual: date | None
    preco_atual: float | None
    valor_investido: float | None
    valor_atual: float | None
    rentabilidade_pct: float | None
    variacao_absoluta: float | None


@dataclass
class ResultadoBacktest:
    data_inicio_pedida: date
    itens: list[ResultadoItemBacktest]
    valor_total_investido: float
    valor_total_atual: float
    rentabilidade_total_pct: float | None
    variacao_absoluta_total: float
    tickers_com_problema: list[str]


def _achar_preco_inicio(
    cotacoes_ano_inicio: dict[tuple[str, date], float], ticker: str, data_alvo: date
) -> tuple[date, float] | None:
    """Acha o primeiro pregão do ticker NA data pedida ou no primeiro
    disponível depois dela, dentro da janela de tolerância. Entre as
    cotações já carregadas em memória (um ano inteiro)."""
    limite = data_alvo + timedelta(days=JANELA_TOLERANCIA_DIAS_INICIO)
    candidatos = [
        (data_pregao, preco)
        for (tk, data_pregao), preco in cotacoes_ano_inicio.items()
        if tk == ticker and data_alvo <= data_pregao <= limite
    ]
    if not candidatos:
        return None
    return min(candidatos, key=lambda candidato: candidato[0])


def montar_resultado_backtest(
    itens: list[ItemCarteira],
    data_inicio: date,
    cotacoes_ano_inicio: dict[tuple[str, date], float],
    precos_atuais: dict[str, tuple[date, float]],
) -> ResultadoBacktest:
    """
    Função pura: recebe as cotações já buscadas (ano de início + preço
    mais recente por ticker) e calcula a rentabilidade da carteira.
    Não faz nenhuma chamada de rede — por isso é testável diretamente.
    """
    resultados: list[ResultadoItemBacktest] = []
    tickers_com_problema: list[str] = []
    valor_total_investido = 0.0
    valor_total_atual = 0.0

    for item in itens:
        ticker = item.ticker.upper()
        inicio = _achar_preco_inicio(cotacoes_ano_inicio, ticker, data_inicio)
        atual = precos_atuais.get(ticker)

        if inicio is None or atual is None:
            status = "sem_cotacao_inicio" if inicio is None else "sem_cotacao_atual"
            tickers_com_problema.append(ticker)
            resultados.append(
                ResultadoItemBacktest(
                    ticker=ticker,
                    quantidade=item.quantidade,
                    status=status,
                    data_pregao_inicio=inicio[0] if inicio else None,
                    preco_inicio=inicio[1] if inicio else None,
                    data_pregao_atual=atual[0] if atual else None,
                    preco_atual=atual[1] if atual else None,
                    valor_investido=None,
                    valor_atual=None,
                    rentabilidade_pct=None,
                    variacao_absoluta=None,
                )
            )
            continue

        data_pregao_inicio, preco_inicio = inicio
        data_pregao_atual, preco_atual = atual
        valor_investido = preco_inicio * item.quantidade
        valor_atual = preco_atual * item.quantidade

        valor_total_investido += valor_investido
        valor_total_atual += valor_atual

        resultados.append(
            ResultadoItemBacktest(
                ticker=ticker,
                quantidade=item.quantidade,
                status="ok",
                data_pregao_inicio=data_pregao_inicio,
                preco_inicio=preco_inicio,
                data_pregao_atual=data_pregao_atual,
                preco_atual=preco_atual,
                valor_investido=valor_investido,
                valor_atual=valor_atual,
                rentabilidade_pct=((valor_atual / valor_investido) - 1) * 100 if valor_investido else None,
                variacao_absoluta=valor_atual - valor_investido,
            )
        )

    rentabilidade_total_pct = (
        ((valor_total_atual / valor_total_investido) - 1) * 100 if valor_total_investido else None
    )

    return ResultadoBacktest(
        data_inicio_pedida=data_inicio,
        itens=resultados,
        valor_total_investido=valor_total_investido,
        valor_total_atual=valor_total_atual,
        rentabilidade_total_pct=rentabilidade_total_pct,
        variacao_absoluta_total=valor_total_atual - valor_total_investido,
        tickers_com_problema=tickers_com_problema,
    )


async def _buscar_preco_atual_com_fallback(tickers: list[str], ano_base: int) -> dict[str, tuple[date, float]]:
    """Tenta achar o pregão mais recente de cada ticker a partir do ano
    corrente, recuando até ANOS_FALLBACK_PRECO_ATUAL anos se o arquivo do
    ano corrente ainda não tiver nenhum pregão desses tickers."""
    faltando = set(tickers)
    encontrados: dict[str, tuple[date, float]] = {}

    for delta in range(ANOS_FALLBACK_PRECO_ATUAL + 1):
        if not faltando:
            break
        ano = ano_base - delta
        try:
            cotacoes = await buscar_cotacoes_ano(ano, list(faltando))
        except B3ClientError as exc:
            logger.warning("Falha ao buscar COTAHIST %d para preço atual do backtest: %s", ano, exc)
            continue
        for ticker, dado in preco_mais_recente_por_ticker(cotacoes).items():
            if ticker in faltando:
                encontrados[ticker] = dado
                faltando.discard(ticker)

    return encontrados


async def calcular_backtest_carteira(
    itens: list[ItemCarteira], data_inicio: date, hoje: date | None = None
) -> ResultadoBacktest:
    """Wrapper async: busca as cotações reais na B3 (COTAHIST) e delega
    a matemática para `montar_resultado_backtest`."""
    hoje = hoje or date.today()
    if data_inicio > hoje:
        raise BacktestError("Data de início não pode ser no futuro.")
    if not itens:
        raise BacktestError("A carteira precisa ter pelo menos um item.")

    tickers = list(dict.fromkeys(item.ticker.upper() for item in itens))  # dedup preservando ordem

    try:
        cotacoes_ano_inicio = await buscar_cotacoes_ano(data_inicio.year, tickers)
    except B3ClientError as exc:
        raise BacktestError(
            f"Falha ao buscar cotações da B3 para o ano de início ({data_inicio.year}): {exc}"
        ) from exc

    precos_atuais = await _buscar_preco_atual_com_fallback(tickers, hoje.year)

    return montar_resultado_backtest(itens, data_inicio, cotacoes_ano_inicio, precos_atuais)


def resultado_para_dict(resultado: ResultadoBacktest) -> dict:
    """Serializa o resultado (dataclasses com `date`) para um dict pronto
    para virar JSON — mesmo padrão de retorno em dict simples já usado
    nos outros endpoints de app/main.py."""
    return {
        "data_inicio_pedida": resultado.data_inicio_pedida.isoformat(),
        "valor_total_investido": round(resultado.valor_total_investido, 2),
        "valor_total_atual": round(resultado.valor_total_atual, 2),
        "variacao_absoluta_total": round(resultado.variacao_absoluta_total, 2),
        "rentabilidade_total_pct": (
            round(resultado.rentabilidade_total_pct, 2) if resultado.rentabilidade_total_pct is not None else None
        ),
        "tickers_com_problema": resultado.tickers_com_problema,
        "itens": [_item_para_dict(item) for item in resultado.itens],
    }


def _item_para_dict(item: ResultadoItemBacktest) -> dict:
    return {
        "ticker": item.ticker,
        "quantidade": item.quantidade,
        "status": item.status,
        "data_pregao_inicio": item.data_pregao_inicio.isoformat() if item.data_pregao_inicio else None,
        "preco_inicio": item.preco_inicio,
        "data_pregao_atual": item.data_pregao_atual.isoformat() if item.data_pregao_atual else None,
        "preco_atual": item.preco_atual,
        "valor_investido": round(item.valor_investido, 2) if item.valor_investido is not None else None,
        "valor_atual": round(item.valor_atual, 2) if item.valor_atual is not None else None,
        "rentabilidade_pct": round(item.rentabilidade_pct, 2) if item.rentabilidade_pct is not None else None,
        "variacao_absoluta": round(item.variacao_absoluta, 2) if item.variacao_absoluta is not None else None,
    }
