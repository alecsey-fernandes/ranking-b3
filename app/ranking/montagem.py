"""
Combina o preço mais recente (coletado da B3) com os fundamentos mais
recentes (importados da CVM) num único `Indicadores` por ticker — a peça
que faltava para rodar `gerar_ranking()` inteiramente com dados
gratuitos, sem depender de plano pago da Brapi.

As duas fontes são persistidas em datas diferentes (preço é diário,
fundamentos são anuais), então cada uma vive em snapshots separados no
banco. Este módulo não persiste nada novo — só lê o que já está salvo e
monta o objeto combinado em memória, na hora de gerar o ranking.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date
from typing import Optional

from app.db.repository import (
    buscar_empresa,
    buscar_fundamentos_cvm_ano_anterior,
    buscar_historico_fundamentos_cvm,
    buscar_media_dividendo_5a,
    buscar_ultimo_preco_valido,
    buscar_ultimos_fundamentos_cvm,
)
from app.models import Indicadores

logger = logging.getLogger(__name__)


def _razao(numerador: float | None, denominador: float | None) -> float | None:
    """Divisão segura: retorna None se algum dos dois for None ou se o
    denominador for zero (evita ZeroDivisionError e ratios sem sentido
    em empresas com passivo/ativo zerado por dado incompleto)."""
    if numerador is None or denominador is None or denominador == 0:
        return None
    return numerador / denominador


def _calcular_flags_piotroski(fundamentos_atual: dict, fundamentos_anterior: dict | None) -> dict:
    """
    Calcula os 6 critérios comparativos ano-a-ano do Piotroski F-Score
    (ver app/strategies/piotroski.py), comparando o snapshot de
    fundamentos mais recente com o do ano anterior. Quando não há ano
    anterior disponível, ou quando falta algum dado necessário para uma
    comparação específica, o campo correspondente fica None — a
    estratégia trata None como "critério não atendido", sem gerar erro
    (ver test_criterios_comparativos_ausentes_contam_como_nao_atendidos_sem_erro).
    """
    flags: dict[str, bool | None] = {
        "piotroski_roa_melhorou": None,
        "piotroski_alavancagem_caiu": None,
        "piotroski_liquidez_melhorou": None,
        "piotroski_margem_melhorou": None,
        "piotroski_giro_melhorou": None,
        "piotroski_sem_diluicao": None,
    }

    if fundamentos_anterior is None:
        return flags

    roa_atual = _razao(fundamentos_atual.get("lucro_liquido"), fundamentos_atual.get("ativo_total"))
    roa_anterior = _razao(fundamentos_anterior.get("lucro_liquido"), fundamentos_anterior.get("ativo_total"))
    if roa_atual is not None and roa_anterior is not None:
        flags["piotroski_roa_melhorou"] = roa_atual > roa_anterior

    def _alavancagem(row: dict) -> float | None:
        passivo_total = None
        if row.get("passivo_circulante") is not None and row.get("passivo_nao_circulante") is not None:
            passivo_total = row["passivo_circulante"] + row["passivo_nao_circulante"]
        return _razao(passivo_total, row.get("ativo_total"))

    alavancagem_atual = _alavancagem(fundamentos_atual)
    alavancagem_anterior = _alavancagem(fundamentos_anterior)
    if alavancagem_atual is not None and alavancagem_anterior is not None:
        flags["piotroski_alavancagem_caiu"] = alavancagem_atual < alavancagem_anterior

    liquidez_atual = _razao(fundamentos_atual.get("ativo_circulante"), fundamentos_atual.get("passivo_circulante"))
    liquidez_anterior = _razao(
        fundamentos_anterior.get("ativo_circulante"), fundamentos_anterior.get("passivo_circulante")
    )
    if liquidez_atual is not None and liquidez_anterior is not None:
        flags["piotroski_liquidez_melhorou"] = liquidez_atual > liquidez_anterior

    margem_atual = _razao(fundamentos_atual.get("lucro_bruto"), fundamentos_atual.get("receita_liquida"))
    margem_anterior = _razao(fundamentos_anterior.get("lucro_bruto"), fundamentos_anterior.get("receita_liquida"))
    if margem_atual is not None and margem_anterior is not None:
        flags["piotroski_margem_melhorou"] = margem_atual > margem_anterior

    giro_atual = _razao(fundamentos_atual.get("receita_liquida"), fundamentos_atual.get("ativo_total"))
    giro_anterior = _razao(fundamentos_anterior.get("receita_liquida"), fundamentos_anterior.get("ativo_total"))
    if giro_atual is not None and giro_anterior is not None:
        flags["piotroski_giro_melhorou"] = giro_atual > giro_anterior

    # Diluição: só avalia se nenhum dos dois anos tem o dado de ações
    # marcado como suspeito (ver acoes_dado_suspeito em models.py) — um
    # valor de ações errado na origem faria essa comparação apontar
    # diluição/não-diluição falsa.
    suspeito_atual = bool(fundamentos_atual.get("acoes_dado_suspeito"))
    suspeito_anterior = bool(fundamentos_anterior.get("acoes_dado_suspeito"))
    acoes_atual = fundamentos_atual.get("acoes_em_circulacao")
    acoes_anterior = fundamentos_anterior.get("acoes_em_circulacao")
    if not suspeito_atual and not suspeito_anterior and acoes_atual is not None and acoes_anterior is not None:
        flags["piotroski_sem_diluicao"] = acoes_atual <= acoes_anterior

    return flags


# ROE mínimo considerado "forte o suficiente para contar" num ano do
# histórico, para fins do critério de consistência do Buffett-like.
# 10% é um piso conservador (bem abaixo do ideal de ~15%+ que Buffett
# buscava) — o objetivo aqui é apenas descartar anos claramente fracos,
# não exigir excelência em todos eles.
ROE_MINIMO_CONSISTENCIA = 0.10

# Quanto a margem líquida atual pode cair em relação à média dos anos
# anteriores e ainda ser considerada "estável" (80% da média histórica).
MARGEM_ESTAVEL_TOLERANCIA = 0.80


def _calcular_flags_buffett(
    historico_fundamentos: list[dict],
) -> tuple[Optional[bool], Optional[bool], Optional[list[float]]]:
    """
    A partir do histórico de fundamentos (mais recente primeiro, até 5
    anos, vindo de `buscar_historico_fundamentos_cvm`), calcula:
    - `roe_consistente`: True se houver pelo menos 2 anos de ROE
      disponíveis e TODOS estiverem acima de `ROE_MINIMO_CONSISTENCIA`.
      None se não houver histórico suficiente (menos de 2 anos) — a
      estratégia trata None como "não atendido", sem erro.
    - `margem_estavel`: True se a margem líquida do ano mais recente for
      pelo menos `MARGEM_ESTAVEL_TOLERANCIA` da média dos anos
      anteriores (não caiu bruscamente). None se não houver pelo menos
      1 ano anterior para comparar.
    - `roe_historico_5a`: lista de ROE calculados por ano disponível
      (para transparência/consumo futuro), mais recente primeiro.
    """
    roes: list[float] = []
    margens: list[float] = []
    for snapshot in historico_fundamentos:
        roe = _razao(snapshot.get("lucro_liquido"), snapshot.get("patrimonio_liquido"))
        if roe is not None:
            roes.append(roe)
        margem = _razao(snapshot.get("lucro_liquido"), snapshot.get("receita_liquida"))
        if margem is not None:
            margens.append(margem)

    roe_consistente: Optional[bool] = None
    if len(roes) >= 2:
        roe_consistente = all(r >= ROE_MINIMO_CONSISTENCIA for r in roes)

    margem_estavel: Optional[bool] = None
    if len(margens) >= 2:
        margem_atual = margens[0]
        media_anteriores = sum(margens[1:]) / len(margens[1:])
        if media_anteriores > 0:
            margem_estavel = margem_atual >= MARGEM_ESTAVEL_TOLERANCIA * media_anteriores

    return roe_consistente, margem_estavel, (roes or None)


def montar_indicadores_para_ranking(conn: sqlite3.Connection, tickers: list[str]) -> list[Indicadores]:
    """
    Para cada ticker, busca o snapshot de preço mais recente (B3) e o
    snapshot de fundamentos mais recente (CVM) já persistidos, e monta
    um `Indicadores` combinado. Tickers sem preço OU sem fundamentos
    disponíveis são simplesmente omitidos do resultado (não é possível
    ranqueá-los ainda) — não geram erro.
    """
    resultado: list[Indicadores] = []

    for ticker in tickers:
        preco_row = buscar_ultimo_preco_valido(conn, ticker)
        fundamentos_row = buscar_ultimos_fundamentos_cvm(conn, ticker)

        if preco_row is None:
            logger.info("Ticker %s sem preço coletado ainda (rode /coleta/b3/precos) — omitido do ranking", ticker)
            continue
        if fundamentos_row is None:
            logger.info(
                "Ticker %s sem fundamentos CVM ainda (rode /coleta/cvm/lucro-historico) — omitido do ranking",
                ticker,
            )
            continue

        empresa_row = buscar_empresa(conn, ticker)
        nome = empresa_row["nome"] if empresa_row else ticker
        setor = empresa_row["setor"] if empresa_row else None

        dividendo_medio_5a = buscar_media_dividendo_5a(conn, ticker)

        fundamentos_anterior_row = buscar_fundamentos_cvm_ano_anterior(
            conn, ticker, date.fromisoformat(fundamentos_row["data_referencia"])
        )
        flags_piotroski = _calcular_flags_piotroski(fundamentos_row, fundamentos_anterior_row)

        historico_fundamentos = buscar_historico_fundamentos_cvm(conn, ticker, anos=5)
        roe_consistente, margem_estavel, roe_historico = _calcular_flags_buffett(historico_fundamentos)
        roe_atual = _razao(fundamentos_row.get("lucro_liquido"), fundamentos_row.get("patrimonio_liquido"))
        margem_liquida_atual = _razao(fundamentos_row.get("lucro_liquido"), fundamentos_row.get("receita_liquida"))

        resultado.append(
            Indicadores(
                ticker=ticker,
                nome=nome,
                setor=setor,
                data_referencia=date.fromisoformat(preco_row["data_referencia"]),
                preco_atual=preco_row["preco_atual"],
                lucro_liquido=fundamentos_row.get("lucro_liquido"),
                lpa=fundamentos_row.get("lpa"),
                vpa=fundamentos_row.get("vpa"),
                acoes_em_circulacao=fundamentos_row.get("acoes_em_circulacao"),
                acoes_dado_suspeito=(
                    bool(fundamentos_row["acoes_dado_suspeito"])
                    if fundamentos_row.get("acoes_dado_suspeito") is not None
                    else None
                ),
                dividend_yield=fundamentos_row.get("dividend_yield"),
                dividendo_medio_5a=dividendo_medio_5a,
                # EV/EBIT/ROIC ainda não têm fonte gratuita integrada
                # (ver README > "Limitações conhecidas") — ficam None, o
                # que faz a Fórmula Mágica marcar a empresa como não
                # elegível em vez de usar um dado ausente silenciosamente.
                # Campos brutos do Piotroski (repassados direto do
                # snapshot CVM mais recente, para a estratégia usar nos
                # 3 critérios não-comparativos: lucro/caixa positivos e
                # caixa > lucro).
                ativo_total=fundamentos_row.get("ativo_total"),
                ativo_circulante=fundamentos_row.get("ativo_circulante"),
                passivo_circulante=fundamentos_row.get("passivo_circulante"),
                passivo_nao_circulante=fundamentos_row.get("passivo_nao_circulante"),
                caixa_operacional=fundamentos_row.get("caixa_operacional"),
                lucro_bruto=fundamentos_row.get("lucro_bruto"),
                receita_liquida=fundamentos_row.get("receita_liquida"),
                # Flags comparativas ano-a-ano do Piotroski (None quando
                # não há ano anterior ou falta algum dado necessário).
                **flags_piotroski,
                # ROE/margem atuais + histórico e flags de consistência
                # do Buffett-like (ver app/strategies/buffett_like.py).
                # ROE aqui também serve de proxy de qualidade para a
                # Fórmula Mágica quando ROIC não está disponível (ver
                # app/strategies/magic_formula.py).
                patrimonio_liquido=fundamentos_row.get("patrimonio_liquido"),
                roe=roe_atual,
                margem_liquida=margem_liquida_atual,
                roe_historico_5a=roe_historico,
                buffett_roe_consistente=roe_consistente,
                buffett_margem_estavel=margem_estavel,
            )
        )

    return resultado
