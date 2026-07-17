"""
Normaliza os scores brutos (escalas heterogêneas entre estratégias) para
percentil 0-100 dentro do universo de empresas analisado, respeitando a
`direcao` de cada estratégia (maior_melhor vs menor_melhor).

Por que percentil e não min-max ou z-score: percentil é robusto a
outliers (comuns em dados fundamentalistas — ex: EV/EBIT negativo ou
absurdamente alto por lucro momentaneamente baixo) e é diretamente
interpretável ("essa empresa está no top 10% em Graham").
"""

from __future__ import annotations

from app.config import settings
from app.models import ResultadoEstrategia
from app.strategies.base import Direcao


def normalizar_para_percentil(
    resultados: list[ResultadoEstrategia], direcao: Direcao
) -> dict[str, float]:
    """
    Recebe os resultados de UMA estratégia para várias empresas e devolve
    {ticker: percentil_0_100}. Empresas não elegíveis ficam de fora do dict
    (tratadas depois no agregador, não recebem score dessa estratégia).
    """
    elegiveis = [r for r in resultados if r.elegivel]

    if len(elegiveis) < settings.min_empresas_para_percentil:
        # Universo pequeno demais para um percentil ter significado estatístico;
        # melhor não atribuir score do que atribuir um percentil enganoso.
        return {}

    ordenados = sorted(
        elegiveis,
        key=lambda r: r.score_bruto,
        reverse=(direcao == Direcao.MAIOR_MELHOR),
    )

    n = len(ordenados)
    percentis: dict[str, float] = {}
    for posicao, resultado in enumerate(ordenados):
        # posicao 0 = melhor colocado -> percentil 100; último -> percentil próximo de 0
        percentil = 100.0 * (n - posicao) / n
        percentis[resultado.ticker] = round(percentil, 2)

    return percentis
