"""
PEG Ratio (Peter Lynch, "One Up on Wall Street").

PEG = (Preço / Lucro) / Taxa de crescimento anual do lucro (%)

A ideia de Lynch: uma empresa "cara" pelo P/L pode ainda estar barata se
seu lucro cresce rápido o suficiente para justificar o múltiplo. PEG < 1
sugere que o preço não está acompanhando o crescimento (potencialmente
subvalorizada); PEG > 1 sugere o oposto.

Score bruto = PEG (menor é melhor — ver `direcao`).

Taxa de crescimento usada: CAGR do lucro líquido dos últimos anos
disponíveis (até 5), calculado em `app/ranking/montagem.py` a partir do
histórico de fundamentos CVM (`crescimento_lucro_pct_5a`).

Só é calculável quando:
- P/L é positivo (LPA e preço positivos — lucro negativo não tem P/L
  interpretável);
- a taxa de crescimento é positiva (Lynch usava o PEG para achar
  "crescimento a preço razoável"; empresa encolhendo não se encaixa
  nesse critério, e um PEG com crescimento negativo/zero no
  denominador não tem leitura econômica sensata).

Fora disso, a empresa é marcada como não elegível (não é um "score
ruim", é "critério não aplicável").
"""

from __future__ import annotations

from app.models import Indicadores, ResultadoEstrategia
from app.strategies.base import Direcao, Estrategia


class PegLynchStrategy(Estrategia):
    nome = "peg_lynch"
    direcao = Direcao.MENOR_MELHOR

    def calcular(self, indicadores: Indicadores) -> ResultadoEstrategia:
        lpa = indicadores.lpa
        preco = indicadores.preco_atual
        crescimento = indicadores.crescimento_lucro_pct_5a

        if not lpa or lpa <= 0 or not preco or preco <= 0:
            return ResultadoEstrategia(
                ticker=indicadores.ticker,
                estrategia=self.nome,
                score_bruto=0.0,
                elegivel=False,
                detalhes={"motivo": "LPA e/ou preço ausentes ou não positivos"},
            )

        if crescimento is None or crescimento <= 0:
            return ResultadoEstrategia(
                ticker=indicadores.ticker,
                estrategia=self.nome,
                score_bruto=0.0,
                elegivel=False,
                detalhes={"motivo": "taxa de crescimento do lucro ausente ou não positiva (histórico insuficiente ou empresa encolhendo)"},
            )

        p_l = preco / lpa
        peg = p_l / crescimento

        return ResultadoEstrategia(
            ticker=indicadores.ticker,
            estrategia=self.nome,
            score_bruto=peg,
            elegivel=True,
            detalhes={
                "p_l": round(p_l, 2),
                "crescimento_lucro_pct_5a": round(crescimento, 2),
                "peg": round(peg, 2),
            },
        )
