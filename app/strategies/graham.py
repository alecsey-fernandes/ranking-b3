"""
Fórmula de Graham (Benjamin Graham, "O Investidor Inteligente").

Preço Justo = sqrt(22.5 * LPA * VPA)

Constante 22,5 = P/L máximo de 15 x P/VP máximo de 1,5, os limites que
Graham considerava aceitáveis para uma empresa "defensiva". A estratégia
mede a margem de segurança: quanto maior (Preço Justo / Preço Atual),
mais "descontada" a ação está frente ao critério de Graham.

Score bruto = Preço Justo / Preço Atual (>1 sugere subvalorização).
Só é calculável para empresas com LPA e VPA positivos — Graham assumia
empresas lucrativas e com patrimônio líquido positivo; fora disso o
critério não é aplicável (não é um "score ruim", é "não elegível").
"""

from __future__ import annotations

import math

from app.models import Indicadores, ResultadoEstrategia
from app.strategies.base import Direcao, Estrategia


class GrahamStrategy(Estrategia):
    nome = "graham"
    direcao = Direcao.MAIOR_MELHOR

    def calcular(self, indicadores: Indicadores) -> ResultadoEstrategia:
        lpa = indicadores.lpa
        vpa = indicadores.vpa
        preco = indicadores.preco_atual

        if not lpa or not vpa or lpa <= 0 or vpa <= 0 or not preco:
            return ResultadoEstrategia(
                ticker=indicadores.ticker,
                estrategia=self.nome,
                score_bruto=0.0,
                elegivel=False,
                detalhes={"motivo": "LPA e/ou VPA ausentes ou não positivos"},
            )

        preco_justo = math.sqrt(22.5 * lpa * vpa)
        margem_seguranca = preco_justo / preco

        return ResultadoEstrategia(
            ticker=indicadores.ticker,
            estrategia=self.nome,
            score_bruto=margem_seguranca,
            elegivel=True,
            detalhes={"preco_justo": round(preco_justo, 2), "preco_atual": preco},
        )
