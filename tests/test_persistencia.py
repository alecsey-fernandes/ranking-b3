"""
Testes da persistência histórica e da consistência de lucro, usando um
banco SQLite temporário (sem tocar no banco real de dev/produção) e
dados simulados (sem chamar a Brapi).
"""

import os
import tempfile
from datetime import date, timedelta

import pytest

from app.analysis.consistencia import calcular_consistencia_lucro
from app.models import Indicadores, RankingFinal


@pytest.fixture()
def conexao_temporaria(monkeypatch):
    """Cria um banco SQLite temporário isolado para cada teste."""
    fd, caminho = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    monkeypatch.setenv("DATABASE_PATH", caminho)

    # os módulos db.connection e db.repository leem DATABASE_PATH no import,
    # então recarregamos para pegar o valor do monkeypatch
    import importlib

    import app.db.connection as connection_module

    importlib.reload(connection_module)
    connection_module.inicializar_schema()

    yield connection_module

    os.remove(caminho)


def _indicadores(ticker, data_referencia, lucro_liquido, preco=10.0):
    return Indicadores(
        ticker=ticker,
        nome=f"Empresa {ticker}",
        setor="Teste",
        data_referencia=data_referencia,
        preco_atual=preco,
        lucro_liquido=lucro_liquido,
    )


def test_salvar_e_buscar_historico_indicadores(conexao_temporaria):
    from app.db.repository import buscar_historico_indicadores, salvar_snapshot_indicadores

    hoje = date.today()
    with conexao_temporaria.get_connection() as conn:
        salvar_snapshot_indicadores(conn, _indicadores("AAA3", hoje - timedelta(days=2), 100.0))
        salvar_snapshot_indicadores(conn, _indicadores("AAA3", hoje - timedelta(days=1), 110.0))
        salvar_snapshot_indicadores(conn, _indicadores("AAA3", hoje, 120.0))

    with conexao_temporaria.get_connection() as conn:
        historico = buscar_historico_indicadores(conn, "AAA3")

    assert len(historico) == 3
    # mais recente primeiro
    assert historico[0]["lucro_liquido"] == 120.0
    assert historico[-1]["lucro_liquido"] == 100.0


def test_upsert_nao_duplica_snapshot_no_mesmo_dia(conexao_temporaria):
    from app.db.repository import buscar_historico_indicadores, salvar_snapshot_indicadores

    hoje = date.today()
    with conexao_temporaria.get_connection() as conn:
        salvar_snapshot_indicadores(conn, _indicadores("BBB3", hoje, 50.0))
        salvar_snapshot_indicadores(conn, _indicadores("BBB3", hoje, 55.0))  # mesmo dia, valor atualizado

    with conexao_temporaria.get_connection() as conn:
        historico = buscar_historico_indicadores(conn, "BBB3")

    assert len(historico) == 1
    assert historico[0]["lucro_liquido"] == 55.0


def test_salvar_e_buscar_evolucao_ranking(conexao_temporaria):
    from app.db.repository import buscar_evolucao_ranking, salvar_snapshot_ranking

    ranking_dia1 = [
        RankingFinal(ticker="AAA3", nome="Empresa AAA3", setor="Teste", score_final=80.0, posicao=1, scores_por_estrategia={"graham": 90.0}),
    ]
    ranking_dia2 = [
        RankingFinal(ticker="AAA3", nome="Empresa AAA3", setor="Teste", score_final=85.0, posicao=1, scores_por_estrategia={"graham": 95.0}),
    ]

    with conexao_temporaria.get_connection() as conn:
        salvar_snapshot_ranking(conn, ranking_dia1, date.today() - timedelta(days=1), {"graham": 100})
        salvar_snapshot_ranking(conn, ranking_dia2, date.today(), {"graham": 100})

    with conexao_temporaria.get_connection() as conn:
        evolucao = buscar_evolucao_ranking(conn, "AAA3")

    assert len(evolucao) == 2
    assert evolucao[0]["score_final"] == 85.0  # mais recente primeiro


def test_consistencia_lucro_empresa_estavel_e_crescente():
    lucros = [100.0, 110.0, 125.0, 130.0, 150.0]  # cronológico, sempre positivo e crescente
    resultado = calcular_consistencia_lucro("AAA3", lucros)

    assert resultado.confiavel
    assert resultado.anos_lucro_positivo == 5
    assert resultado.anos_com_crescimento == 4
    assert resultado.score_consistencia > 80  # deve pontuar alto


def test_consistencia_lucro_empresa_erratica():
    lucros = [100.0, -50.0, 80.0, -20.0, 60.0]  # cronológico, alternando prejuízo
    resultado = calcular_consistencia_lucro("BBB3", lucros)

    assert resultado.confiavel
    assert resultado.anos_lucro_positivo == 3
    assert resultado.score_consistencia < 60  # deve pontuar baixo frente à empresa estável


def test_consistencia_lucro_dados_insuficientes():
    resultado = calcular_consistencia_lucro("CCC3", [100.0, 110.0])  # só 2 pontos
    assert not resultado.confiavel
    assert resultado.score_consistencia is None
