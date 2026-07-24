"""
Cálculo de backtest de carteira: dado um conjunto de posições (ticker +
quantidade) e uma data de início, responde "quanto essa carteira teria
rendido se tivesse sido montada naquela data e mantida até hoje" — usando
o preço de fechamento oficial da B3 (COTAHIST), a mesma fonte gratuita já
usada em app/jobs/atualizar_precos_b3.py, combinado com os dividendos
anuais por ação já persistidos via importação CVM (app/jobs/importar_cvm.py).

A lógica é dividida em duas camadas, seguindo o mesmo padrão já usado em
b3_cotahist_client.py (parsing puro vs. busca em rede):

- `montar_resultado_backtest` (+ `_simular_posicao`): função PURA, recebe
  as cotações/dividendos já carregados em memória e faz só a matemática.
  Testável diretamente com dados sintéticos, sem rede nem banco (ver
  tests/test_backtest_calculo.py).
- `calcular_backtest_carteira`: wrapper async que busca as cotações reais
  via `buscar_cotacoes_ano` e os dividendos já persistidos via
  `buscar_dividendos_por_ano`, e delega a matemática para as funções puras.

Etapa 2 (esta versão) — o que mudou desde a etapa 1:
- Dividendos/JCP agora SEMPRE entram no cálculo (antes eram ignorados).
  `reinvestir_dividendos` controla só o que fazer com eles: `False`
  (padrão) = somados como caixa acumulado ao valor final, sem comprar
  mais ações; `True` = reinvestidos automaticamente em novas ações
  (fracionárias, só para fins de simulação) no preço de fechamento mais
  próximo do fim de cada ano em que houve dividendo.
- Desdobramentos/grupamentos agora podem ser informados por ticker
  (`eventos_societarios`) e são aplicados à quantidade na data certa,
  antes de aplicar qualquer dividendo que tenha vindo depois.

⚠️ Limitações conhecidas:
- Eventos societários (splits/bonificações) chegam já prontos em
  `ItemCarteira.eventos_societarios` — quem decide a origem é o chamador
  (ver `_mesclar_eventos_societarios` em app/main.py): combina os já
  persistidos automaticamente (tabela `evento_societario`, hoje vazia —
  aguardando confirmação de fonte gratuita via CVM/FRE, mesma cautela já
  aplicada a outras fontes CVM) com os informados manualmente no payload
  (que têm prioridade na mesma data, servindo de correção). Este módulo
  em si é agnóstico à origem — só aplica a lista que recebe.
- O valor anual de dividendo por ação vem da importação CVM (agregado do
  ano fiscal, ligado a 31/dez) — não das datas reais de pagamento/JCP.
  O preço usado para "comprar" as ações reinvestidas é o do último pregão
  disponível daquele ano, uma aproximação (não a data real do provento).
  Anos sem essa importação rodada (`/coleta/cvm/lucro-historico`) para o
  ticker simplesmente não entram no cálculo — não é tratado como "zero
  dividendo", é tratado como "dado ausente" (fica de fora, e o valor
  total fica subestimado nesse caso).
- Preço de início usa o primeiro pregão disponível NA data pedida ou
  logo APÓS ela (dentro de uma janela de tolerância) — cobre o caso de
  a data cair num fim de semana/feriado. A data efetivamente usada vem
  sempre informada na resposta (`data_pregao_inicio`).
- Carteira não é persistida nesta etapa — cada chamada recalcula do zero.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from datetime import date, timedelta

from app.data_sources.b3_cotahist_client import (
    B3ClientError,
    buscar_cotacoes_ano,
    preco_mais_recente_por_ticker,
)
from app.db.connection import get_connection
from app.db.repository import buscar_dividendos_por_ano

logger = logging.getLogger(__name__)

JANELA_TOLERANCIA_DIAS_INICIO = 10
ANOS_FALLBACK_PRECO_ATUAL = 2


class BacktestError(Exception):
    """Erro ao calcular o backtest da carteira (ex: falha ao baixar COTAHIST)."""


@dataclass
class EventoSocietario:
    """Desdobramento (fator > 1) ou grupamento (fator < 1) de um ticker."""

    data: date
    fator: float


@dataclass
class ItemCarteira:
    ticker: str
    quantidade: float
    eventos_societarios: list[EventoSocietario] = field(default_factory=list)


@dataclass
class ResultadoItemBacktest:
    ticker: str
    quantidade: float
    status: str  # "ok" | "sem_cotacao_inicio" | "sem_cotacao_atual"
    data_pregao_inicio: date | None
    preco_inicio: float | None
    data_pregao_atual: date | None
    preco_atual: float | None
    quantidade_final: float | None
    valor_investido: float | None
    valor_atual: float | None
    rentabilidade_pct: float | None
    variacao_absoluta: float | None
    dividendos_recebidos: float | None
    valor_atual_somente_preco: float | None
    rentabilidade_pct_somente_preco: float | None


@dataclass
class ResultadoBacktest:
    data_inicio_pedida: date
    reinvestir_dividendos: bool
    itens: list[ResultadoItemBacktest]
    valor_total_investido: float
    valor_total_atual: float
    rentabilidade_total_pct: float | None
    variacao_absoluta_total: float
    dividendos_totais_recebidos: float
    valor_total_atual_somente_preco: float
    rentabilidade_total_pct_somente_preco: float | None
    tickers_com_problema: list[str]


def _achar_preco_inicio(
    cotacoes_ano_inicio: dict[tuple[str, date], float], ticker: str, data_alvo: date
) -> tuple[date, float] | None:
    limite = data_alvo + timedelta(days=JANELA_TOLERANCIA_DIAS_INICIO)
    candidatos = [
        (data_pregao, preco)
        for (tk, data_pregao), preco in cotacoes_ano_inicio.items()
        if tk == ticker and data_alvo <= data_pregao <= limite
    ]
    if not candidatos:
        return None
    return min(candidatos, key=lambda candidato: candidato[0])


def _achar_preco_no_ou_antes(
    cotacoes_ano: dict[tuple[str, date], float], ticker: str, data_alvo: date
) -> tuple[date, float] | None:
    """Acha o pregão mais recente do ticker NA data pedida ou ANTES dela
    (dentro da janela de tolerância) — usado quando o usuário informa uma
    `data_fim` explícita, em vez de sempre usar o pregão mais recente
    disponível (ver `_buscar_preco_atual_com_fallback`)."""
    limite_inferior = data_alvo - timedelta(days=JANELA_TOLERANCIA_DIAS_INICIO)
    candidatos = [
        (data_pregao, preco)
        for (tk, data_pregao), preco in cotacoes_ano.items()
        if tk == ticker and limite_inferior <= data_pregao <= data_alvo
    ]
    if not candidatos:
        return None
    return max(candidatos, key=lambda candidato: candidato[0])


def _simular_posicao(
    quantidade_inicial: float,
    data_pregao_inicio: date,
    data_pregao_atual: date,
    preco_atual: float,
    eventos_societarios: list[EventoSocietario],
    dividendos_por_ano: dict[int, float],
    precos_fim_de_ano: dict[int, tuple[date, float]],
    reinvestir_dividendos: bool,
) -> dict:
    linha_do_tempo: list[tuple[date, str, object]] = []

    for evento in eventos_societarios:
        if data_pregao_inicio < evento.data <= data_pregao_atual:
            linha_do_tempo.append((evento.data, "split", evento.fator))

    for ano, valor_por_acao in dividendos_por_ano.items():
        preco_do_ano = precos_fim_de_ano.get(ano)
        if preco_do_ano is not None:
            data_evento, preco_evento = preco_do_ano
        else:
            # Sem preço real do fim do ano disponível: usa 31/dez como
            # aproximação só para ORDENAR o evento na linha do tempo. Sem
            # reinvestimento o preço nem é usado (vira caixa), então esse
            # caso é o normal quando reinvestir_dividendos=False (ver
            # `calcular_backtest_carteira`, que só busca preço de fim de
            # ano quando reinvestir_dividendos=True).
            data_evento, preco_evento = date(ano, 12, 31), None
        if data_pregao_inicio < data_evento <= data_pregao_atual:
            linha_do_tempo.append((data_evento, "dividendo", (valor_por_acao, preco_evento)))

    linha_do_tempo.sort(key=lambda evento: evento[0])

    quantidade = quantidade_inicial
    quantidade_apos_splits_sem_dividendos = quantidade_inicial
    caixa_dividendos = 0.0
    dividendos_recebidos_total = 0.0

    for _data_evento, tipo, payload in linha_do_tempo:
        if tipo == "split":
            fator = payload
            quantidade *= fator
            quantidade_apos_splits_sem_dividendos *= fator
        else:
            valor_por_acao, preco_evento = payload
            recebido = valor_por_acao * quantidade
            dividendos_recebidos_total += recebido
            if reinvestir_dividendos and preco_evento:
                quantidade += recebido / preco_evento
            else:
                caixa_dividendos += recebido

    return {
        "quantidade_final": quantidade,
        "valor_atual": preco_atual * quantidade + caixa_dividendos,
        "valor_atual_somente_preco": preco_atual * quantidade_apos_splits_sem_dividendos,
        "dividendos_recebidos_total": dividendos_recebidos_total,
    }


def montar_resultado_backtest(
    itens: list[ItemCarteira],
    data_inicio: date,
    cotacoes_ano_inicio: dict[tuple[str, date], float],
    precos_atuais: dict[str, tuple[date, float]],
    reinvestir_dividendos: bool = False,
    dividendos_por_ticker: dict[str, dict[int, float]] | None = None,
    precos_fim_de_ano: dict[str, dict[int, tuple[date, float]]] | None = None,
) -> ResultadoBacktest:
    dividendos_por_ticker = dividendos_por_ticker or {}
    precos_fim_de_ano = precos_fim_de_ano or {}

    resultados: list[ResultadoItemBacktest] = []
    tickers_com_problema: list[str] = []
    valor_total_investido = 0.0
    valor_total_atual = 0.0
    valor_total_atual_somente_preco = 0.0
    dividendos_totais_recebidos = 0.0

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
                    quantidade_final=None,
                    valor_investido=None,
                    valor_atual=None,
                    rentabilidade_pct=None,
                    variacao_absoluta=None,
                    dividendos_recebidos=None,
                    valor_atual_somente_preco=None,
                    rentabilidade_pct_somente_preco=None,
                )
            )
            continue

        data_pregao_inicio, preco_inicio = inicio
        data_pregao_atual, preco_atual = atual

        simulacao = _simular_posicao(
            quantidade_inicial=item.quantidade,
            data_pregao_inicio=data_pregao_inicio,
            data_pregao_atual=data_pregao_atual,
            preco_atual=preco_atual,
            eventos_societarios=item.eventos_societarios or [],
            dividendos_por_ano=dividendos_por_ticker.get(ticker, {}),
            precos_fim_de_ano=precos_fim_de_ano.get(ticker, {}),
            reinvestir_dividendos=reinvestir_dividendos,
        )

        valor_investido = preco_inicio * item.quantidade
        valor_atual = simulacao["valor_atual"]
        valor_atual_somente_preco = simulacao["valor_atual_somente_preco"]

        valor_total_investido += valor_investido
        valor_total_atual += valor_atual
        valor_total_atual_somente_preco += valor_atual_somente_preco
        dividendos_totais_recebidos += simulacao["dividendos_recebidos_total"]

        resultados.append(
            ResultadoItemBacktest(
                ticker=ticker,
                quantidade=item.quantidade,
                status="ok",
                data_pregao_inicio=data_pregao_inicio,
                preco_inicio=preco_inicio,
                data_pregao_atual=data_pregao_atual,
                preco_atual=preco_atual,
                quantidade_final=simulacao["quantidade_final"],
                valor_investido=valor_investido,
                valor_atual=valor_atual,
                rentabilidade_pct=((valor_atual / valor_investido) - 1) * 100 if valor_investido else None,
                variacao_absoluta=valor_atual - valor_investido,
                dividendos_recebidos=simulacao["dividendos_recebidos_total"],
                valor_atual_somente_preco=valor_atual_somente_preco,
                rentabilidade_pct_somente_preco=(
                    ((valor_atual_somente_preco / valor_investido) - 1) * 100 if valor_investido else None
                ),
            )
        )

    rentabilidade_total_pct = (
        ((valor_total_atual / valor_total_investido) - 1) * 100 if valor_total_investido else None
    )
    rentabilidade_total_pct_somente_preco = (
        ((valor_total_atual_somente_preco / valor_total_investido) - 1) * 100 if valor_total_investido else None
    )

    return ResultadoBacktest(
        data_inicio_pedida=data_inicio,
        reinvestir_dividendos=reinvestir_dividendos,
        itens=resultados,
        valor_total_investido=valor_total_investido,
        valor_total_atual=valor_total_atual,
        rentabilidade_total_pct=rentabilidade_total_pct,
        variacao_absoluta_total=valor_total_atual - valor_total_investido,
        dividendos_totais_recebidos=dividendos_totais_recebidos,
        valor_total_atual_somente_preco=valor_total_atual_somente_preco,
        rentabilidade_total_pct_somente_preco=rentabilidade_total_pct_somente_preco,
        tickers_com_problema=tickers_com_problema,
    )


async def _buscar_preco_atual_com_fallback(tickers: list[str], ano_base: int) -> dict[str, tuple[date, float]]:
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


async def _buscar_precos_fim_de_ano(
    tickers: list[str],
    dividendos_por_ticker: dict[str, dict[int, float]],
    cotacoes_ano_inicio: dict[tuple[str, date], float],
    precos_atuais: dict[str, tuple[date, float]],
    ano_inicio: int,
    ano_atual: int,
) -> dict[str, dict[int, tuple[date, float]]]:
    precos_fim_de_ano: dict[str, dict[int, tuple[date, float]]] = {ticker: {} for ticker in tickers}

    precos_ano_inicio = preco_mais_recente_por_ticker(cotacoes_ano_inicio)
    for ticker in tickers:
        if ano_inicio in dividendos_por_ticker.get(ticker, {}) and ticker in precos_ano_inicio:
            precos_fim_de_ano[ticker][ano_inicio] = precos_ano_inicio[ticker]

    for ticker in tickers:
        if ano_atual in dividendos_por_ticker.get(ticker, {}) and ticker in precos_atuais:
            precos_fim_de_ano[ticker][ano_atual] = precos_atuais[ticker]

    anos_faltando: dict[int, list[str]] = {}
    for ticker, dividendos in dividendos_por_ticker.items():
        for ano in dividendos:
            if ano not in precos_fim_de_ano[ticker]:
                anos_faltando.setdefault(ano, []).append(ticker)

    for ano, tickers_do_ano in anos_faltando.items():
        try:
            cotacoes_ano = await buscar_cotacoes_ano(ano, tickers_do_ano)
        except B3ClientError as exc:
            logger.warning("Falha ao buscar COTAHIST %d para valorizar dividendos no backtest: %s", ano, exc)
            continue
        for ticker, dado in preco_mais_recente_por_ticker(cotacoes_ano).items():
            precos_fim_de_ano[ticker][ano] = dado

    return precos_fim_de_ano


async def _buscar_preco_final(tickers: list[str], data_fim: date) -> dict[str, tuple[date, float]]:
    """Busca o preço de cada ticker NA data_fim pedida ou no pregão
    disponível mais próximo ANTES dela (dentro da janela de tolerância).
    Se não achar nada no ano de data_fim (ex: data_fim é 2 de janeiro,
    antes do 1º pregão do ano), recua para o fim do(s) ano(s) anterior(es)
    — mesmo espírito do fallback de `_buscar_preco_atual_com_fallback`."""
    faltando = set(tickers)
    encontrados: dict[str, tuple[date, float]] = {}

    for delta in range(ANOS_FALLBACK_PRECO_ATUAL + 1):
        if not faltando:
            break
        ano = data_fim.year - delta
        alvo_no_ano = data_fim if delta == 0 else date(ano, 12, 31)
        try:
            cotacoes = await buscar_cotacoes_ano(ano, list(faltando))
        except B3ClientError as exc:
            logger.warning("Falha ao buscar COTAHIST %d para preço de fim do backtest: %s", ano, exc)
            continue
        for ticker in list(faltando):
            achado = _achar_preco_no_ou_antes(cotacoes, ticker, alvo_no_ano)
            if achado:
                encontrados[ticker] = achado
                faltando.discard(ticker)

    return encontrados


async def calcular_backtest_carteira(
    itens: list[ItemCarteira],
    data_inicio: date,
    reinvestir_dividendos: bool = False,
    data_fim: date | None = None,
    hoje: date | None = None,
) -> ResultadoBacktest:
    hoje = hoje or date.today()
    if data_fim is not None and data_fim > hoje:
        raise BacktestError("Data final não pode ser no futuro.")
    data_fim_efetiva = data_fim or hoje
    if data_inicio > data_fim_efetiva:
        raise BacktestError("Data de início não pode ser depois da data final.")
    if not itens:
        raise BacktestError("A carteira precisa ter pelo menos um item.")

    tickers = list(dict.fromkeys(item.ticker.upper() for item in itens))

    inicio_cronometro = time.monotonic()
    try:
        cotacoes_ano_inicio = await buscar_cotacoes_ano(data_inicio.year, tickers)
    except B3ClientError as exc:
        raise BacktestError(
            f"Falha ao buscar cotações da B3 para o ano de início ({data_inicio.year}): {exc}"
        ) from exc
    logger.info("Backtest: cotações do ano de início (%d) prontas em %.1fs", data_inicio.year, time.monotonic() - inicio_cronometro)

    inicio_cronometro = time.monotonic()
    if data_fim is not None:
        precos_atuais = await _buscar_preco_final(tickers, data_fim)
    else:
        precos_atuais = await _buscar_preco_atual_com_fallback(tickers, hoje.year)
    logger.info("Backtest: preço final pronto em %.1fs", time.monotonic() - inicio_cronometro)

    with get_connection() as conn:
        dividendos_por_ticker = {
            ticker: buscar_dividendos_por_ano(conn, ticker, data_inicio.year, data_fim_efetiva.year) for ticker in tickers
        }

    # Preço de fim de ano só é necessário para VALORIZAR o reinvestimento
    # (comprar ações com o dividendo). Sem reinvestimento, o dividendo vira
    # caixa e o preço nem é usado — pular essa busca evita baixar/processar
    # anos extras de COTAHIST à toa no caso mais comum (padrão é não
    # reinvestir), que era uma causa real de requisições lentas.
    precos_fim_de_ano: dict[str, dict[int, tuple[date, float]]] = {}
    if reinvestir_dividendos:
        precos_fim_de_ano = await _buscar_precos_fim_de_ano(
            tickers, dividendos_por_ticker, cotacoes_ano_inicio, precos_atuais, data_inicio.year, data_fim_efetiva.year
        )

    return montar_resultado_backtest(
        itens,
        data_inicio,
        cotacoes_ano_inicio,
        precos_atuais,
        reinvestir_dividendos=reinvestir_dividendos,
        dividendos_por_ticker=dividendos_por_ticker,
        precos_fim_de_ano=precos_fim_de_ano,
    )


def resultado_para_dict(resultado: ResultadoBacktest) -> dict:
    return {
        "data_inicio_pedida": resultado.data_inicio_pedida.isoformat(),
        "reinvestir_dividendos": resultado.reinvestir_dividendos,
        "valor_total_investido": round(resultado.valor_total_investido, 2),
        "valor_total_atual": round(resultado.valor_total_atual, 2),
        "variacao_absoluta_total": round(resultado.variacao_absoluta_total, 2),
        "rentabilidade_total_pct": (
            round(resultado.rentabilidade_total_pct, 2) if resultado.rentabilidade_total_pct is not None else None
        ),
        "dividendos_totais_recebidos": round(resultado.dividendos_totais_recebidos, 2),
        "valor_total_atual_somente_preco": round(resultado.valor_total_atual_somente_preco, 2),
        "rentabilidade_total_pct_somente_preco": (
            round(resultado.rentabilidade_total_pct_somente_preco, 2)
            if resultado.rentabilidade_total_pct_somente_preco is not None
            else None
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
        "quantidade_final": item.quantidade_final,
        "valor_investido": round(item.valor_investido, 2) if item.valor_investido is not None else None,
        "valor_atual": round(item.valor_atual, 2) if item.valor_atual is not None else None,
        "rentabilidade_pct": round(item.rentabilidade_pct, 2) if item.rentabilidade_pct is not None else None,
        "variacao_absoluta": round(item.variacao_absoluta, 2) if item.variacao_absoluta is not None else None,
        "dividendos_recebidos": round(item.dividendos_recebidos, 2) if item.dividendos_recebidos is not None else None,
        "valor_atual_somente_preco": (
            round(item.valor_atual_somente_preco, 2) if item.valor_atual_somente_preco is not None else None
        ),
        "rentabilidade_pct_somente_preco": (
            round(item.rentabilidade_pct_somente_preco, 2)
            if item.rentabilidade_pct_somente_preco is not None
            else None
        ),
    }
