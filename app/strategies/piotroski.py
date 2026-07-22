"""
Piotroski F-Score (Joseph Piotroski, "Value Investing: The Use of
Historical Financial Statement Information to Separate Winners from
Losers").

9 critérios binários (1 ponto cada, 0 a 9 no total), agrupados em 3
categorias:

**Rentabilidade**
1. Lucro líquido positivo
2. Fluxo de caixa operacional positivo
3. ROA (retorno sobre ativos) melhorou frente ao ano anterior
4. Fluxo de caixa operacional > Lucro líquido (qualidade do lucro —
   empresa que "ganha caixa de verdade", não só contabilmente)

**Alavancagem, liquidez e financiamento**
5. Alavancagem (dívida/ativos) caiu frente ao ano anterior
6. Liquidez corrente melhorou frente ao ano anterior
7. Não houve diluição de acionistas (quantidade de ações não aumentou)

**Eficiência operacional**
8. Margem bruta melhorou frente ao ano anterior
9. Giro de ativos (receita/ativos) melhorou frente ao ano anterior

Score bruto = quantidade de critérios atendidos (0-9), maior é melhor.

Os critérios "melhorou frente ao ano anterior" (3, 5, 6, 8, 9) e o de
diluição (7) chegam **já calculados** como booleanos em `Indicadores`
(campos `piotroski_*`) — computados em `app/ranking/montagem.py`, que
compara o snapshot mais recente com o do ano anterior. Essa estratégia
só combina o que já foi calculado; não faz nenhuma comparação temporal
por conta própria.
"""

from __future__ import annotations

from app.models import Indicadores, ResultadoEstrategia
from app.strategies.base import Direcao, Estrategia


class PiotroskiStrategy(Estrategia):
    nome = "piotroski"
    direcao = Direcao.MAIOR_MELHOR

    def calcular(self, indicadores: Indicadores) -> ResultadoEstrategia:
        # Critérios mínimos para a estratégia fazer sentido: precisamos
        # pelo menos saber se o lucro e o caixa operacional são
        # positivos. Sem isso, não há base nenhuma para pontuar.
        if indicadores.lucro_liquido is None or indicadores.caixa_operacional is None:
            return ResultadoEstrategia(
                ticker=indicadores.ticker,
                estrategia=self.nome,
                score_bruto=0.0,
                elegivel=False,
                detalhes={"motivo": "lucro líquido ou caixa operacional ausentes"},
            )

        criterios: dict[str, bool] = {}

        criterios["lucro_positivo"] = indicadores.lucro_liquido > 0
        criterios["caixa_operacional_positivo"] = indicadores.caixa_operacional > 0
        criterios["caixa_maior_que_lucro"] = indicadores.caixa_operacional > indicadores.lucro_liquido

        # Critérios comparativos (ano-a-ano) — só contam quando o dado
        # está disponível (None é tratado como "não atendido", não como
        # erro, já que nem toda empresa tem 2 anos de histórico ainda).
        criterios["roa_melhorou"] = bool(indicadores.piotroski_roa_melhorou)
        criterios["alavancagem_caiu"] = bool(indicadores.piotroski_alavancagem_caiu)
        criterios["liquidez_melhorou"] = bool(indicadores.piotroski_liquidez_melhorou)
        criterios["sem_diluicao"] = bool(indicadores.piotroski_sem_diluicao)
        criterios["margem_melhorou"] = bool(indicadores.piotroski_margem_melhorou)
        criterios["giro_melhorou"] = bool(indicadores.piotroski_giro_melhorou)

        score = sum(1 for atendido in criterios.values() if atendido)

        return ResultadoEstrategia(
            ticker=indicadores.ticker,
            estrategia=self.nome,
            score_bruto=float(score),
            elegivel=True,
            detalhes={"criterios_atendidos": criterios, "total": score},
        )
