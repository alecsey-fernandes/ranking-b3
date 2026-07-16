"""
Fórmula Mágica de Joel Greenblatt ("The Little Book That Beats the Market").

Combina duas dimensões:
- Preço (barato): Earnings Yield = EBIT / Enterprise Value (inverso do EV/EBIT)
- Qualidade: ROIC (Return on Invested Capital)

Metodologia original: ranqueia cada empresa separadamente por Earnings
Yield e por ROIC (1 = melhor), soma as duas posições, e reordena pela
soma (menor soma = melhor). Aqui adaptamos para produzir um score_bruto
contínuo (em vez de ranking ordinal) para ser compatível com a
normalização por percentil do restante do pipeline: usamos a soma dos
dois ranks como score, onde MENOR é melhor.

ROIC não vem pronto da Brapi; quando ausente, usamos ROE como proxy de
qualidade (mais fraco, mas evita descartar a empresa). Isso fica marcado
em `detalhes` para transparência.
"""

from __future__ import annotations

from app.models import Indicadores, ResultadoEstrategia
from app.strategies.base import Direcao, Estrategia


class MagicFormulaStrategy(Estrategia):
    nome = "magic_formula"
    direcao = Direcao.MAIOR_MELHOR  # score_bruto aqui já é earnings_yield + proxy de qualidade, maior = melhor

    def calcular(self, indicadores: Indicadores) -> ResultadoEstrategia:
        ev = indicadores.valor_firma
        ebit = indicadores.ebit
        qualidade = indicadores.roic if indicadores.roic is not None else indicadores.roe

        if not ev or not ebit or ev <= 0 or ebit <= 0 or qualidade is None:
            return ResultadoEstrategia(
                ticker=indicadores.ticker,
                estrategia=self.nome,
                score_bruto=0.0,
                elegivel=False,
                detalhes={"motivo": "EV, EBIT ou indicador de qualidade (ROIC/ROE) ausentes"},
            )

        earnings_yield = ebit / ev

        # Combinação simples e monotônica das duas dimensões (ambas "maior=melhor"),
        # deixando a normalização por percentil do agregador cuidar da escala relativa
        # ao restante do universo de empresas.
        score_bruto = earnings_yield + qualidade

        return ResultadoEstrategia(
            ticker=indicadores.ticker,
            estrategia=self.nome,
            score_bruto=score_bruto,
            elegivel=True,
            detalhes={
                "earnings_yield": round(earnings_yield, 4),
                "qualidade": round(qualidade, 4),
                "qualidade_fonte": "roic" if indicadores.roic is not None else "roe (proxy)",
            },
        )
