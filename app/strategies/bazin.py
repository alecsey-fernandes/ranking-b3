"""
Fórmula de Bazin (Décio Bazin, "Faça Fortuna com Ações").

Preço Teto = Dividendo médio anual por ação / 0.06

A lógica original de Bazin: se uma empresa paga dividendos de forma
consistente, um preço que entregue 6% de dividend yield sobre o preço
pago é considerado um "preço teto" aceitável para compra — abaixo disso,
a ação está "barata" pelo critério de renda.

Score bruto = Preço Teto / Preço Atual (>1 sugere que está abaixo do
preço teto, ou seja, atrativa pelo critério de dividendos).

Fonte do dividendo médio, em ordem de preferência:
1. `dividendo_medio_5a` — média de 5 anos, o que Bazin realmente pedia
   (evita comprar por causa de um dividendo extraordinário de um ano só).
   Ainda não vem da Brapi básica; fica pronto para quando integrarmos
   histórico de proventos.
2. Fallback: dividendo implícito no yield do último ano
   (`dividend_yield * preco_atual`) — mais fraco (só 1 ano, mais sensível
   a dividendo extraordinário), mas evita descartar a empresa por falta
   de histórico longo. Marcado em `detalhes` para transparência.

Sem nenhuma das duas fontes, ou sem dividendo (empresa que não paga
proventos), a estratégia marca `elegivel=False` — Bazin não se aplica a
empresas que não distribuem dividendos, por definição.
"""

from __future__ import annotations

from app.models import Indicadores, ResultadoEstrategia
from app.strategies.base import Direcao, Estrategia

YIELD_ALVO_BAZIN = 0.06


class BazinStrategy(Estrategia):
    nome = "bazin"
    direcao = Direcao.MAIOR_MELHOR

    def calcular(self, indicadores: Indicadores) -> ResultadoEstrategia:
        preco = indicadores.preco_atual
        if not preco or preco <= 0:
            return ResultadoEstrategia(
                ticker=indicadores.ticker,
                estrategia=self.nome,
                score_bruto=0.0,
                elegivel=False,
                detalhes={"motivo": "preço atual ausente"},
            )

        dividendo_medio = indicadores.dividendo_medio_5a
        fonte = "media_5_anos"

        if dividendo_medio is None:
            if indicadores.dividend_yield:
                dividendo_medio = indicadores.dividend_yield * preco
                fonte = "yield_ultimo_ano (proxy)"
            else:
                return ResultadoEstrategia(
                    ticker=indicadores.ticker,
                    estrategia=self.nome,
                    score_bruto=0.0,
                    elegivel=False,
                    detalhes={"motivo": "sem histórico de dividendo médio nem dividend yield — empresa provavelmente não paga proventos"},
                )

        if dividendo_medio <= 0:
            return ResultadoEstrategia(
                ticker=indicadores.ticker,
                estrategia=self.nome,
                score_bruto=0.0,
                elegivel=False,
                detalhes={"motivo": "dividendo médio não positivo"},
            )

        preco_teto = dividendo_medio / YIELD_ALVO_BAZIN
        margem = preco_teto / preco

        return ResultadoEstrategia(
            ticker=indicadores.ticker,
            estrategia=self.nome,
            score_bruto=margem,
            elegivel=True,
            detalhes={
                "preco_teto": round(preco_teto, 2),
                "preco_atual": preco,
                "dividendo_medio_usado": round(dividendo_medio, 2),
                "fonte_dividendo": fonte,
            },
        )
