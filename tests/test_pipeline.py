"""
Testes com dados simulados (sem chamar a Brapi de verdade) para validar:
- cálculo de cada estratégia isoladamente
- normalização em percentil
- agregação ponderada final, incluindo o caso de empresa não elegível
  numa das estratégias (reponderação).
"""

from datetime import date

from app.config import PesosEstrategias
from app.models import Indicadores
from app.ranking.aggregator import gerar_ranking
from app.strategies.bazin import BazinStrategy
from app.strategies.graham import GrahamStrategy
from app.strategies.magic_formula import MagicFormulaStrategy


def _empresa(ticker, preco, lpa, vpa, ev=None, ebit=None, roe=None, roic=None, **extra):
    return Indicadores(
        ticker=ticker,
        nome=f"Empresa {ticker}",
        setor="Teste",
        data_referencia=date.today(),
        preco_atual=preco,
        lpa=lpa,
        vpa=vpa,
        valor_firma=ev,
        ebit=ebit,
        roe=roe,
        roic=roic,
        **extra,
    )


def test_graham_calcula_margem_seguranca_corretamente():
    # Preço justo = sqrt(22.5 * 2 * 10) = sqrt(450) ~= 21.21
    empresa = _empresa("AAA3", preco=15.0, lpa=2.0, vpa=10.0)
    resultado = GrahamStrategy().calcular(empresa)
    assert resultado.elegivel
    assert round(resultado.score_bruto, 2) == round(21.2132 / 15.0, 2)


def test_graham_marca_nao_elegivel_sem_lpa_positivo():
    empresa = _empresa("BBB3", preco=15.0, lpa=-1.0, vpa=10.0)
    resultado = GrahamStrategy().calcular(empresa)
    assert not resultado.elegivel


def test_magic_formula_usa_roe_como_proxy_quando_roic_ausente():
    empresa = _empresa("CCC3", preco=10, lpa=1, vpa=5, ev=1000, ebit=200, roe=0.18)
    resultado = MagicFormulaStrategy().calcular(empresa)
    assert resultado.elegivel
    assert resultado.detalhes["qualidade_fonte"] == "roe (proxy)"


def test_bazin_usa_dividendo_medio_5a_quando_disponivel():
    empresa = _empresa("AAA3", preco=20, lpa=1, vpa=5, dividendo_medio_5a=1.5)
    resultado = BazinStrategy().calcular(empresa)
    # preco_teto = 1.5 / 0.06 = 25 -> margem = 25/20 = 1.25
    assert resultado.elegivel
    assert resultado.detalhes["fonte_dividendo"] == "media_5_anos"
    assert round(resultado.score_bruto, 2) == 1.25


def test_bazin_usa_dividend_yield_como_proxy_quando_sem_media_5a():
    empresa = _empresa("BBB3", preco=10, lpa=1, vpa=5, dividend_yield=0.08)
    resultado = BazinStrategy().calcular(empresa)
    # dividendo implícito = 0.08 * 10 = 0.8 -> preco_teto = 0.8/0.06 ~= 13.33 -> margem ~= 1.33
    assert resultado.elegivel
    assert resultado.detalhes["fonte_dividendo"] == "yield_ultimo_ano (proxy)"


def test_bazin_marca_nao_elegivel_sem_nenhum_dado_de_dividendo():
    empresa = _empresa("CCC3", preco=10, lpa=1, vpa=5)
    resultado = BazinStrategy().calcular(empresa)
    assert not resultado.elegivel


def test_ranking_pondera_e_ordena_corretamente():
    # AAA3: forte em Graham e Magic Formula -> deve liderar
    # BBB3: mediano nas duas
    # CCC3: não elegível em Magic Formula (sem EV/EBIT) mas ok em Graham
    empresas = [
        _empresa("AAA3", preco=10, lpa=2, vpa=10, ev=800, ebit=200, roe=0.20),
        _empresa("BBB3", preco=20, lpa=1.5, vpa=8, ev=1500, ebit=150, roe=0.10),
        _empresa("CCC3", preco=12, lpa=1.8, vpa=9, ev=None, ebit=None, roe=0.15),
        _empresa("DDD3", preco=30, lpa=0.5, vpa=4, ev=2000, ebit=50, roe=0.05),
        _empresa("EEE3", preco=8, lpa=1.2, vpa=6, ev=600, ebit=90, roe=0.12),
    ]

    pesos = PesosEstrategias(
        magic_formula=50, piotroski=0, graham=50, bazin=0, buffett_like=0, peg_lynch=0
    )
    ranking = gerar_ranking(empresas, [GrahamStrategy(), MagicFormulaStrategy()], pesos)

    assert ranking[0].posicao == 1
    tickers_no_ranking = {linha.ticker for linha in ranking}
    assert "CCC3" in tickers_no_ranking  # não elegível numa estratégia, mas continua no ranking (reponderado)

    ccc3 = next(linha for linha in ranking if linha.ticker == "CCC3")
    assert "magic_formula" not in ccc3.scores_por_estrategia
    assert "graham" in ccc3.scores_por_estrategia


def test_empresa_sem_nenhum_dado_fica_fora_do_ranking():
    empresas = [
        _empresa("AAA3", preco=10, lpa=2, vpa=10, ev=800, ebit=200, roe=0.20),
        _empresa("BBB3", preco=20, lpa=1.5, vpa=8, ev=1500, ebit=150, roe=0.10),
        _empresa("CCC3", preco=12, lpa=1.8, vpa=9, ev=700, ebit=120, roe=0.15),
        _empresa("DDD3", preco=30, lpa=0.5, vpa=4, ev=2000, ebit=50, roe=0.05),
        _empresa("VAZIA3", preco=5, lpa=None, vpa=None, ev=None, ebit=None, roe=None),
    ]
    ranking = gerar_ranking(empresas, [GrahamStrategy(), MagicFormulaStrategy()])
    tickers_no_ranking = {linha.ticker for linha in ranking}
    assert "VAZIA3" not in tickers_no_ranking
