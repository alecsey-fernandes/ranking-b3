"""
Cliente para a API Brapi (https://brapi.dev), fonte de dados fundamentalistas
de empresas listadas na B3 usada no MVP.

Por que Brapi na v1: já entrega indicadores fundamentalistas prontos via
`modules=defaultKeyStatistics,financialData`, evitando parsear XBRL cru da
CVM logo de cara. Trocar/complementar por CVM ou B3 direto depois é uma
questão de escrever outro client que produza o mesmo `Indicadores` — as
camadas de estratégia e ranking não mudam.
"""

from __future__ import annotations

import logging
from datetime import date, datetime
from typing import Optional

import httpx

from app.config import settings
from app.models import Indicadores

logger = logging.getLogger(__name__)


class BrapiClientError(Exception):
    """Erro ao consultar ou interpretar dados da Brapi."""


class BrapiClient:
    def __init__(self, base_url: str | None = None, token: str | None = None):
        self.base_url = base_url or settings.brapi_base_url
        self.token = token or settings.brapi_token

    def _params(self) -> dict:
        params = {"modules": "defaultKeyStatistics,financialData,balanceSheetHistory"}
        if self.token:
            params["token"] = self.token
        return params

    async def buscar_indicadores(self, ticker: str) -> Optional[Indicadores]:
        """Busca e mapeia os indicadores de uma empresa. Retorna None se o ticker não existir."""
        url = f"{self.base_url}/quote/{ticker}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            try:
                resp = await client.get(url, params=self._params())
            except httpx.HTTPError as exc:
                raise BrapiClientError(f"Falha de rede ao buscar {ticker}: {exc}") from exc

        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            raise BrapiClientError(f"Brapi retornou status {resp.status_code} para {ticker}: {resp.text[:200]}")

        payload = resp.json()
        resultados = payload.get("results") or []
        if not resultados:
            return None

        return self._mapear(resultados[0])

    async def buscar_lote(self, tickers: list[str]) -> list[Indicadores]:
        """Busca indicadores de vários tickers, ignorando os que falharem individualmente."""
        indicadores: list[Indicadores] = []
        for ticker in tickers:
            try:
                item = await self.buscar_indicadores(ticker)
                if item:
                    indicadores.append(item)
                else:
                    logger.warning("Ticker %s não encontrado na Brapi", ticker)
            except BrapiClientError as exc:
                logger.warning("Erro ao buscar %s: %s", ticker, exc)
        return indicadores

    @staticmethod
    def _mapear(raw: dict) -> Indicadores:
        """Converte o payload cru da Brapi para o modelo interno `Indicadores`."""
        key_stats = raw.get("defaultKeyStatistics") or {}
        fin_data = raw.get("financialData") or {}

        def g(d: dict, *keys, default=None):
            for k in keys:
                if d.get(k) is not None:
                    return d[k]
            return default

        return Indicadores(
            ticker=raw.get("symbol", ""),
            nome=raw.get("longName") or raw.get("shortName") or raw.get("symbol", ""),
            setor=raw.get("sector"),
            data_referencia=date.today(),
            preco_atual=raw.get("regularMarketPrice") or 0.0,
            valor_mercado=raw.get("marketCap"),
            valor_firma=g(key_stats, "enterpriseValue"),
            lucro_liquido=g(fin_data, "netIncomeToCommon"),
            ebit=g(key_stats, "ebit"),
            ebitda=g(fin_data, "ebitda"),
            receita_liquida=g(fin_data, "totalRevenue"),
            lpa=g(key_stats, "trailingEps"),
            vpa=g(key_stats, "bookValue"),
            p_l=g(key_stats, "trailingPE") or raw.get("priceEarnings"),
            p_vp=g(key_stats, "priceToBook"),
            ev_ebit=_dividir(g(key_stats, "enterpriseValue"), g(key_stats, "ebit")),
            ev_ebitda=g(key_stats, "enterpriseToEbitda"),
            peg_ratio=g(key_stats, "pegRatio"),
            roe=g(fin_data, "returnOnEquity"),
            roic=None,  # Brapi não expõe ROIC direto; calculado na camada de estratégia se houver dados
            margem_liquida=g(fin_data, "profitMargins"),
            margem_ebit=g(fin_data, "operatingMargins"),
            divida_liquida_ebitda=g(fin_data, "debtToEquity"),
            liquidez_corrente=g(fin_data, "currentRatio"),
            dividend_yield=raw.get("dividendYield") or g(key_stats, "dividendYield"),
            dividendo_medio_5a=None,  # requer histórico de proventos; fonte separada (ver TODO no README)
            crescimento_lucro_pct_5a=g(fin_data, "earningsGrowth"),
            lucro_liquido_ano_anterior=None,
            roe_historico_5a=None,
        )


def _dividir(a: Optional[float], b: Optional[float]) -> Optional[float]:
    if a is None or b in (None, 0):
        return None
    return a / b
