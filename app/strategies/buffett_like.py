"""
Buffett-like: estratégia de qualidade inspirada nos critérios que Warren
Buffett costuma citar para "empresas excelentes" — rentabilidade sobre
capital alta e consistente, margens estáveis e dívida baixa. Não é uma
fórmula publicada e fechada (como Graham ou Bazin); é uma síntese comum
desses critérios, então os limiares usados aqui (ROE mínimo, tolerância
de queda de margem, alavancagem máxima) são escolhas razoáveis, não
"regras oficiais" — ver constantes em app/ranking/montagem.py e neste
módulo.

5 critérios binários (1 ponto cada, 0 a 5 no total):

1. ROE atual positivo
2. ROE atual forte (>= 15% — piso clássico citado por Buffett)
3. ROE consistente nos últimos anos disponíveis (todos os anos do
   histórico, até 5, acima de um piso mais baixo de 10% — ver
   ROE_MINIMO_CONSISTENCIA em montagem.py). Critério "não atendido"
   (não erro) quando há menos de 2 anos de histórico.
4. Margem líquida estável (não caiu mais que ~20% frente à média dos
   anos anteriores). Critério "não atendido" quando não há ano anterior
   para comparar.
5. Dívida baixa: passivo total (circulante + não circulante) sobre
   ativo total abaixo de 50%.

Os critérios 3 e 4 chegam **já calculados** como booleanos em
`Indicadores` (`buffett_roe_consistente`, `buffett_margem_estavel`) —
computados em `app/ranking/montagem.py`, que olha o histórico de vários
anos. Esta estratégia só combina o que já foi calculado; não faz
comparação temporal por conta própria.

Score bruto = quantidade de critérios atendidos (0-5), maior é melhor.
"""

from __future__ import annotations

from app.models import Indicadores, ResultadoEstrategia
from app.strategies.base import Direcao, Estrategia

ROE_FORTE = 0.15
ALAVANCAGEM_MAXIMA = 0.50


class BuffettLikeStrategy(Estrategia):
    nome = "buffett_like"
    direcao = Direcao.MAIOR_MELHOR

    def calcular(self, indicadores: Indicadores) -> ResultadoEstrategia:
        # Critério mínimo para a estratégia fazer sentido: sem ROE não
        # há base nenhuma para avaliar rentabilidade sobre capital.
        if indicadores.roe is None:
            return ResultadoEstrategia(
                ticker=indicadores.ticker,
                estrategia=self.nome,
                score_bruto=0.0,
                elegivel=False,
                detalhes={"motivo": "ROE ausente (lucro líquido ou patrimônio líquido não disponíveis)"},
            )

        criterios: dict[str, bool] = {}

        criterios["roe_positivo"] = indicadores.roe > 0
        criterios["roe_forte"] = indicadores.roe >= ROE_FORTE
        criterios["roe_consistente"] = bool(indicadores.buffett_roe_consistente)
        criterios["margem_estavel"] = bool(indicadores.buffett_margem_estavel)

        divida_baixa = False
        if (
            indicadores.ativo_total
            and indicadores.ativo_total > 0
            and indicadores.passivo_circulante is not None
            and indicadores.passivo_nao_circulante is not None
        ):
            alavancagem = (
                indicadores.passivo_circulante + indicadores.passivo_nao_circulante
            ) / indicadores.ativo_total
            divida_baixa = alavancagem < ALAVANCAGEM_MAXIMA
        criterios["divida_baixa"] = divida_baixa

        score = sum(1 for atendido in criterios.values() if atendido)

        return ResultadoEstrategia(
            ticker=indicadores.ticker,
            estrategia=self.nome,
            score_bruto=float(score),
            elegivel=True,
            detalhes={"criterios_atendidos": criterios, "total": score, "roe": round(indicadores.roe, 4)},
        )
