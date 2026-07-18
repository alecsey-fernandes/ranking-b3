"""
Modelos de dados centrais da plataforma.

`Indicadores` representa o conjunto bruto de dados fundamentalistas de uma
empresa em um determinado momento, coletado da fonte de dados (ex: Brapi).
Todas as estratégias consomem esse mesmo objeto, então adicionar uma nova
fonte de dado só exige mapear os campos aqui — as estratégias não mudam.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from typing import Optional


@dataclass
class Indicadores:
    """Indicadores fundamentalistas de uma empresa em uma data de referência."""

    ticker: str
    nome: str
    setor: Optional[str]
    data_referencia: date

    # Preço e valor de mercado
    preco_atual: float
    valor_mercado: Optional[float] = None
    valor_firma: Optional[float] = None  # EV (Enterprise Value)

    # Resultado
    lucro_liquido: Optional[float] = None
    ebit: Optional[float] = None
    ebitda: Optional[float] = None
    receita_liquida: Optional[float] = None
    patrimonio_liquido: Optional[float] = None

    # Por ação
    lpa: Optional[float] = None  # Lucro por ação
    vpa: Optional[float] = None  # Valor patrimonial por ação

    # Múltiplos
    p_l: Optional[float] = None
    p_vp: Optional[float] = None
    ev_ebit: Optional[float] = None
    ev_ebitda: Optional[float] = None
    peg_ratio: Optional[float] = None

    # Rentabilidade
    roe: Optional[float] = None
    roic: Optional[float] = None
    margem_liquida: Optional[float] = None
    margem_ebit: Optional[float] = None

    # Endividamento / liquidez
    divida_liquida_ebitda: Optional[float] = None
    liquidez_corrente: Optional[float] = None

    # Dividendos
    dividend_yield: Optional[float] = None
    dividendo_medio_5a: Optional[float] = None

    # Histórico de crescimento (usado no F-Score, Buffett-like, PEG)
    crescimento_lucro_pct_5a: Optional[float] = None
    lucro_liquido_ano_anterior: Optional[float] = None
    roe_historico_5a: Optional[list[float]] = None

    # Composição de capital (CVM) — usado para calcular LPA/VPA quando
    # não vêm prontos da fonte de preço/múltiplos. `acoes_dado_suspeito`
    # sinaliza quando o valor falhou num teste de plausibilidade na
    # origem (ver LIMIAR_MINIMO_PLAUSIVEL_ACOES em cvm_client.py) — um
    # caso real já foi confirmado (Vale S.A., DFP 2024, valor ~1000x
    # menor que o real). Consumidores desse campo (estratégias) devem
    # tratar `acoes_em_circulacao` como não confiável quando esse
    # sinalizador for True, em vez de usá-lo silenciosamente.
    acoes_em_circulacao: Optional[float] = None
    acoes_dado_suspeito: Optional[bool] = None


@dataclass
class ResultadoEstrategia:
    """Saída padronizada de qualquer estratégia aplicada a uma empresa."""

    ticker: str
    estrategia: str
    score_bruto: float  # valor na escala nativa da estratégia (ver docstring de cada estratégia)
    elegivel: bool = True  # False quando faltam dados essenciais para calcular
    detalhes: Optional[dict] = None


@dataclass
class RankingFinal:
    """Linha final do ranking ponderado de uma empresa."""

    ticker: str
    nome: str
    setor: Optional[str]
    score_final: float  # 0-100, média ponderada dos percentis por estratégia
    posicao: int
    scores_por_estrategia: dict[str, float]  # percentil 0-100 de cada estratégia
