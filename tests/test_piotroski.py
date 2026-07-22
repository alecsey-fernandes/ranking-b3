"""Testes da estratégia Piotroski F-Score."""

from datetime import date

from app.models import Indicadores
from app.strategies.piotroski import PiotroskiStrategy


def _empresa(ticker, lucro_liquido, caixa_operacional, **flags):
    return Indicadores(
        ticker=ticker,
        nome=ticker,
        setor=None,
        data_referencia=date.today(),
        preco_atual=10.0,
        lucro_liquido=lucro_liquido,
        caixa_operacional=caixa_operacional,
        **flags,
    )


def test_empresa_forte_atende_todos_os_9_criterios():
    forte = _empresa(
        "FORTE3", lucro_liquido=100.0, caixa_operacional=150.0,
        piotroski_roa_melhorou=True, piotroski_alavancagem_caiu=True,
        piotroski_liquidez_melhorou=True, piotroski_sem_diluicao=True,
        piotroski_margem_melhorou=True, piotroski_giro_melhorou=True,
    )
    resultado = PiotroskiStrategy().calcular(forte)
    assert resultado.elegivel
    assert resultado.score_bruto == 9.0


def test_criterio_caixa_maior_que_lucro_conta_mesmo_com_ambos_negativos():
    """Critério clássico: caixa operacional > lucro líquido, mesmo que
    os dois sejam negativos (ex: -10 > -50) — mede qualidade do
    resultado (accruals), não se ambos são positivos."""
    fraca = _empresa("FRACA3", lucro_liquido=-50.0, caixa_operacional=-10.0)
    resultado = PiotroskiStrategy().calcular(fraca)
    assert resultado.elegivel
    assert resultado.score_bruto == 1.0
    assert resultado.detalhes["criterios_atendidos"]["caixa_maior_que_lucro"] is True
    assert resultado.detalhes["criterios_atendidos"]["lucro_positivo"] is False


def test_empresa_sem_dado_minimo_nao_elegivel():
    sem_dado = Indicadores(ticker="SEMDADO3", nome="Sem Dado", setor=None, data_referencia=date.today(), preco_atual=10.0)
    resultado = PiotroskiStrategy().calcular(sem_dado)
    assert not resultado.elegivel


def test_criterios_comparativos_ausentes_contam_como_nao_atendidos_sem_erro():
    """Quando não há ano anterior para comparar (piotroski_* = None),
    a estratégia não deve falhar — só não pontua esses critérios."""
    empresa = _empresa("PARCIAL3", lucro_liquido=50.0, caixa_operacional=60.0)
    resultado = PiotroskiStrategy().calcular(empresa)
    assert resultado.elegivel
    # lucro_positivo + caixa_operacional_positivo + caixa_maior_que_lucro = 3
    assert resultado.score_bruto == 3.0
