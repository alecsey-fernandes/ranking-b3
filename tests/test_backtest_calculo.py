from datetime import date

from app.backtest.calculo import ItemCarteira, montar_resultado_backtest


def test_calcula_rentabilidade_positiva_de_um_ticker():
    cotacoes_ano_inicio = {("PETR4", date(2023, 1, 2)): 20.0}
    precos_atuais = {"PETR4": (date(2026, 7, 21), 40.0)}

    resultado = montar_resultado_backtest(
        itens=[ItemCarteira(ticker="PETR4", quantidade=100)],
        data_inicio=date(2023, 1, 2),
        cotacoes_ano_inicio=cotacoes_ano_inicio,
        precos_atuais=precos_atuais,
    )

    item = resultado.itens[0]
    assert item.status == "ok"
    assert item.valor_investido == 2000.0
    assert item.valor_atual == 4000.0
    assert item.rentabilidade_pct == 100.0
    assert resultado.rentabilidade_total_pct == 100.0
    assert resultado.tickers_com_problema == []


def test_data_inicio_em_fim_de_semana_usa_proximo_pregao_disponivel():
    # Sexta 2023-01-06 é feriado fictício no teste; próximo pregão só segunda 2023-01-09
    cotacoes_ano_inicio = {("VALE3", date(2023, 1, 9)): 80.0}
    precos_atuais = {"VALE3": (date(2026, 7, 21), 100.0)}

    resultado = montar_resultado_backtest(
        itens=[ItemCarteira(ticker="VALE3", quantidade=10)],
        data_inicio=date(2023, 1, 7),  # sábado
        cotacoes_ano_inicio=cotacoes_ano_inicio,
        precos_atuais=precos_atuais,
    )

    item = resultado.itens[0]
    assert item.status == "ok"
    assert item.data_pregao_inicio == date(2023, 1, 9)
    assert item.preco_inicio == 80.0


def test_ticker_sem_cotacao_de_inicio_fica_fora_do_total_mas_aparece_no_detalhe():
    cotacoes_ano_inicio = {}  # nada disponível pro ticker pedido
    precos_atuais = {"ITSA4": (date(2026, 7, 21), 10.0)}

    resultado = montar_resultado_backtest(
        itens=[ItemCarteira(ticker="ITSA4", quantidade=50)],
        data_inicio=date(2023, 1, 2),
        cotacoes_ano_inicio=cotacoes_ano_inicio,
        precos_atuais=precos_atuais,
    )

    item = resultado.itens[0]
    assert item.status == "sem_cotacao_inicio"
    assert item.valor_investido is None
    assert resultado.tickers_com_problema == ["ITSA4"]
    assert resultado.valor_total_investido == 0.0
    assert resultado.rentabilidade_total_pct is None


def test_carteira_com_dois_tickers_soma_valores_totais_corretamente():
    cotacoes_ano_inicio = {
        ("PETR4", date(2024, 3, 1)): 30.0,
        ("VALE3", date(2024, 3, 1)): 60.0,
    }
    precos_atuais = {
        "PETR4": (date(2026, 7, 21), 33.0),
        "VALE3": (date(2026, 7, 21), 54.0),
    }

    resultado = montar_resultado_backtest(
        itens=[
            ItemCarteira(ticker="PETR4", quantidade=100),
            ItemCarteira(ticker="VALE3", quantidade=50),
        ],
        data_inicio=date(2024, 3, 1),
        cotacoes_ano_inicio=cotacoes_ano_inicio,
        precos_atuais=precos_atuais,
    )

    # PETR4: investido 3000, atual 3300 (+300)
    # VALE3: investido 3000, atual 2700 (-300)
    assert resultado.valor_total_investido == 6000.0
    assert resultado.valor_total_atual == 6000.0
    assert resultado.rentabilidade_total_pct == 0.0
    assert resultado.tickers_com_problema == []


def test_ticker_em_letras_minusculas_e_normalizado():
    cotacoes_ano_inicio = {("WEGE3", date(2023, 1, 2)): 40.0}
    precos_atuais = {"WEGE3": (date(2026, 7, 21), 50.0)}

    resultado = montar_resultado_backtest(
        itens=[ItemCarteira(ticker="wege3", quantidade=10)],
        data_inicio=date(2023, 1, 2),
        cotacoes_ano_inicio=cotacoes_ano_inicio,
        precos_atuais=precos_atuais,
    )

    assert resultado.itens[0].ticker == "WEGE3"
    assert resultado.itens[0].status == "ok"
