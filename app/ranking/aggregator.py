"""
Agregador do ranking final.

Fluxo:
1. Aplica cada estratégia disponível a todas as empresas do universo.
2. Normaliza os scores brutos de cada estratégia em percentil 0-100
   (ver `normalizer.py`).
3. Calcula a média ponderada dos percentis, usando os pesos de
   `config.PesosEstrategias`.
4. Empresas não elegíveis numa estratégia simplesmente não contam o peso
   dessa estratégia no denominador daquela empresa (reponderação), em vez
   de receber score 0 — isso evita punir uma empresa só por faltar um
   dado (ex: sem histórico de dividendos ainda não penaliza tanto quanto
   "essa empresa paga zero dividendo").
"""

from __future__ import annotations

from app.config import PesosEstrategias, pesos_padrao
from app.models import Indicadores, RankingFinal
from app.ranking.normalizer import normalizar_para_percentil
from app.strategies.base import Estrategia

# Mapeia o atributo de peso em PesosEstrategias para o `nome` da estratégia.
# Ao adicionar uma estratégia nova, basta adicionar a entrada aqui e o
# campo correspondente em PesosEstrategias (app/config.py).
_ATRIBUTO_PESO_POR_ESTRATEGIA = {
    "magic_formula": "magic_formula",
    "piotroski": "piotroski",
    "graham": "graham",
    "bazin": "bazin",
    "buffett_like": "buffett_like",
    "peg_lynch": "peg_lynch",
}


def gerar_ranking(
    empresas: list[Indicadores],
    estrategias: list[Estrategia],
    pesos: PesosEstrategias = pesos_padrao,
) -> list[RankingFinal]:
    pesos.validar_soma()

    # 1 e 2: calcular + normalizar cada estratégia
    percentis_por_estrategia: dict[str, dict[str, float]] = {}
    for estrategia in estrategias:
        resultados = [estrategia.calcular(emp) for emp in empresas]
        percentis_por_estrategia[estrategia.nome] = normalizar_para_percentil(
            resultados, estrategia.direcao
        )

    # 3 e 4: média ponderada com reponderação por elegibilidade
    linhas: list[RankingFinal] = []
    for empresa in empresas:
        scores_empresa: dict[str, float] = {}
        soma_ponderada = 0.0
        soma_pesos_aplicaveis = 0.0

        for estrategia in estrategias:
            peso_attr = _ATRIBUTO_PESO_POR_ESTRATEGIA.get(estrategia.nome)
            if peso_attr is None:
                continue
            peso = getattr(pesos, peso_attr)

            percentil = percentis_por_estrategia[estrategia.nome].get(empresa.ticker)
            if percentil is None:
                continue  # empresa não elegível ou universo pequeno demais nessa estratégia

            scores_empresa[estrategia.nome] = percentil
            soma_ponderada += percentil * peso
            soma_pesos_aplicaveis += peso

        if soma_pesos_aplicaveis == 0:
            # Empresa não elegível em NENHUMA estratégia calculada -> fora do ranking
            continue

        score_final = soma_ponderada / soma_pesos_aplicaveis
        linhas.append(
            RankingFinal(
                ticker=empresa.ticker,
                nome=empresa.nome,
                setor=empresa.setor,
                score_final=round(score_final, 2),
                posicao=0,  # preenchido abaixo, após ordenar
                scores_por_estrategia=scores_empresa,
            )
        )

    linhas.sort(key=lambda linha: linha.score_final, reverse=True)
    for i, linha in enumerate(linhas, start=1):
        linha.posicao = i

    return linhas
