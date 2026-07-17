"""
Consistência de lucro ao longo do tempo.

Diferente das estratégias em app/strategies/, que avaliam uma empresa num
único momento, este módulo olha o HISTÓRICO de snapshots (persistido via
app/db) para responder: "essa empresa tem lucro estável e crescente ano
após ano, ou é errática?" — o critério central que você apontou como
mais importante para decisão de longo prazo.

Precisa de pelo menos alguns snapshots persistidos para fazer sentido.
Com poucos pontos de dado, os resultados são marcados como
`confiavel=False` (dado insuficiente), nunca omitidos silenciosamente.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class ConsistenciaLucro:
    ticker: str
    pontos_analisados: int
    confiavel: bool
    anos_lucro_positivo: Optional[int] = None
    anos_total: Optional[int] = None
    anos_com_crescimento: Optional[int] = None
    coeficiente_variacao: Optional[float] = None  # desvio padrão / média (menor = mais estável)
    score_consistencia: Optional[float] = None  # 0-100
    detalhes: dict = field(default_factory=dict)

    MINIMO_PONTOS = 3  # abaixo disso, não dá pra falar de "tendência"


def calcular_consistencia_lucro(
    ticker: str, historico_lucros: list[float]
) -> ConsistenciaLucro:
    """
    `historico_lucros` deve vir ordenado do mais ANTIGO para o mais RECENTE
    (o chamador é responsável por essa ordenação, tipicamente lendo
    `indicador_snapshot.lucro_liquido` ordenado por data_referencia ASC).
    """
    n = len(historico_lucros)

    if n < ConsistenciaLucro.MINIMO_PONTOS:
        return ConsistenciaLucro(
            ticker=ticker,
            pontos_analisados=n,
            confiavel=False,
            detalhes={"motivo": f"mínimo de {ConsistenciaLucro.MINIMO_PONTOS} snapshots necessário, há {n}"},
        )

    anos_positivo = sum(1 for lucro in historico_lucros if lucro > 0)

    crescimentos = 0
    for anterior, atual in zip(historico_lucros, historico_lucros[1:]):
        if anterior > 0 and atual > anterior:
            crescimentos += 1

    media = statistics.fmean(historico_lucros)
    if media != 0 and n > 1:
        desvio = statistics.stdev(historico_lucros)
        cv = abs(desvio / media)
    else:
        cv = None

    # Score de consistência (0-100), combinando 3 sinais:
    # - % dos períodos com lucro positivo (peso maior: empresa que dá prejuízo é desqualificante)
    # - % dos períodos com crescimento período a período
    # - estabilidade (inverso do coeficiente de variação, limitado pra não deixar valores extremos dominarem)
    pct_positivo = anos_positivo / n
    pct_crescimento = crescimentos / (n - 1) if n > 1 else 0.0
    estabilidade = max(0.0, 1 - min(cv, 2.0) / 2.0) if cv is not None else 0.0

    score = (pct_positivo * 50) + (pct_crescimento * 30) + (estabilidade * 20)

    return ConsistenciaLucro(
        ticker=ticker,
        pontos_analisados=n,
        confiavel=True,
        anos_lucro_positivo=anos_positivo,
        anos_total=n,
        anos_com_crescimento=crescimentos,
        coeficiente_variacao=round(cv, 3) if cv is not None else None,
        score_consistencia=round(score, 2),
        detalhes={
            "pct_periodos_lucro_positivo": round(pct_positivo * 100, 1),
            "pct_periodos_com_crescimento": round(pct_crescimento * 100, 1),
        },
    )
