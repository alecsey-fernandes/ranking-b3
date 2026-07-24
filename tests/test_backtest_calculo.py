from datetime import date

from app.backtest.calculo import (
    EventoSocietario,
    ItemCarteira,
    _achar_preco_no_ou_antes,
    detectar_candidatos_eventos_societarios,
    montar_resultado_backtest,
)


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


def test_desdobramento_ajusta_quantidade_e_valor_final():
    # Desdobramento 1:2 no meio do período: 100 ações a 40 -> viram 200 a 25
    # (preço de mercado se ajusta pra baixo após o split; valor final deve
    # bater com o que teria sido sem desdobramento, na proporção certa)
    cotacoes_ano_inicio = {("TEST3", date(2023, 1, 2)): 40.0}
    precos_atuais = {"TEST3": (date(2026, 7, 21), 25.0)}
    item = ItemCarteira(
        ticker="TEST3",
        quantidade=100,
        eventos_societarios=[EventoSocietario(data=date(2024, 6, 1), fator=2.0)],
    )

    resultado = montar_resultado_backtest(
        itens=[item],
        data_inicio=date(2023, 1, 2),
        cotacoes_ano_inicio=cotacoes_ano_inicio,
        precos_atuais=precos_atuais,
    )

    ri = resultado.itens[0]
    assert ri.quantidade_final == 200
    assert ri.valor_atual == 5000.0


def test_dividendo_sem_reinvestir_vira_caixa_acumulado():
    cotacoes_ano_inicio = {("DIV3", date(2023, 1, 2)): 10.0}
    precos_atuais = {"DIV3": (date(2026, 7, 21), 12.0)}
    precos_fim_de_ano = {"DIV3": {2023: (date(2023, 12, 28), 11.0)}}
    dividendos_por_ticker = {"DIV3": {2023: 1.0}}  # R$1/ação pago em 2023

    resultado = montar_resultado_backtest(
        itens=[ItemCarteira(ticker="DIV3", quantidade=100)],
        data_inicio=date(2023, 1, 2),
        cotacoes_ano_inicio=cotacoes_ano_inicio,
        precos_atuais=precos_atuais,
        reinvestir_dividendos=False,
        dividendos_por_ticker=dividendos_por_ticker,
        precos_fim_de_ano=precos_fim_de_ano,
    )

    ri = resultado.itens[0]
    assert ri.dividendos_recebidos == 100.0  # 100 ações x R$1
    assert ri.quantidade_final == 100  # não comprou ações novas
    assert ri.valor_atual == 100 * 12.0 + 100.0  # preço atual x qtd + caixa acumulado
    assert ri.valor_atual_somente_preco == 100 * 12.0  # coluna de comparação, sem dividendo


def test_dividendo_sem_preco_de_fim_de_ano_ainda_vira_caixa_quando_nao_reinveste():
    # Sem `precos_fim_de_ano` informado (caso real quando reinvestir_dividendos=False,
    # que agora pula essa busca de propósito) — o dividendo ainda deve ser
    # contabilizado como caixa, usando 31/dez como data aproximada do evento.
    cotacoes_ano_inicio = {("DIV3", date(2023, 1, 2)): 10.0}
    precos_atuais = {"DIV3": (date(2026, 7, 21), 12.0)}
    dividendos_por_ticker = {"DIV3": {2023: 1.0}}

    resultado = montar_resultado_backtest(
        itens=[ItemCarteira(ticker="DIV3", quantidade=100)],
        data_inicio=date(2023, 1, 2),
        cotacoes_ano_inicio=cotacoes_ano_inicio,
        precos_atuais=precos_atuais,
        reinvestir_dividendos=False,
        dividendos_por_ticker=dividendos_por_ticker,
        precos_fim_de_ano={},  # de propósito: simula não ter buscado nenhum preço extra
    )

    ri = resultado.itens[0]
    assert ri.dividendos_recebidos == 100.0
    assert ri.quantidade_final == 100
    assert ri.valor_atual == 100 * 12.0 + 100.0


def test_dividendo_reinvestido_compra_acoes_no_preco_do_evento():
    cotacoes_ano_inicio = {("DIV3", date(2023, 1, 2)): 10.0}
    precos_atuais = {"DIV3": (date(2026, 7, 21), 12.0)}
    precos_fim_de_ano = {"DIV3": {2023: (date(2023, 12, 28), 11.0)}}
    dividendos_por_ticker = {"DIV3": {2023: 1.0}}

    resultado = montar_resultado_backtest(
        itens=[ItemCarteira(ticker="DIV3", quantidade=100)],
        data_inicio=date(2023, 1, 2),
        cotacoes_ano_inicio=cotacoes_ano_inicio,
        precos_atuais=precos_atuais,
        reinvestir_dividendos=True,
        dividendos_por_ticker=dividendos_por_ticker,
        precos_fim_de_ano=precos_fim_de_ano,
    )

    ri = resultado.itens[0]
    acoes_compradas = 100.0 / 11.0  # caixa recebido / preço do pregão de fim de 2023
    assert abs(ri.quantidade_final - (100 + acoes_compradas)) < 1e-9
    assert abs(ri.valor_atual - (100 + acoes_compradas) * 12.0) < 1e-9


def test_achar_preco_no_ou_antes_pega_pregao_mais_recente_disponivel():
    cotacoes = {
        ("PETR4", date(2024, 6, 10)): 35.0,
        ("PETR4", date(2024, 6, 14)): 36.0,
        ("PETR4", date(2024, 6, 17)): 37.0,
    }
    # 16/06/2024 é domingo, sem pregão: deve cair pro pregão anterior (14/06)
    assert _achar_preco_no_ou_antes(cotacoes, "PETR4", date(2024, 6, 16)) == (date(2024, 6, 14), 36.0)
    # data com pregão exato: usa o dela mesmo, não recua
    assert _achar_preco_no_ou_antes(cotacoes, "PETR4", date(2024, 6, 17)) == (date(2024, 6, 17), 37.0)


def test_deteccao_pega_bonificacao_pequena_estilo_beef3():
    # BEEF3 em 30/04/2025: fator real 1129/1000 = 1.129 -> preço cai ~11,4%
    serie = [
        (date(2025, 4, 29), 10.00),
        (date(2025, 4, 30), 10.00 / 1.129),  # ~8.86
        (date(2025, 5, 2), 8.90),
    ]
    candidatos = detectar_candidatos_eventos_societarios(serie)
    assert len(candidatos) == 1
    candidato = candidatos[0]
    assert candidato["data"] == date(2025, 4, 30)
    assert candidato["variacao_pct"] < -8.0  # queda de ~11,4%, acima do limiar de 8%
    assert abs(candidato["fator_sugerido"] - 1.129) < 0.01


def test_deteccao_ignora_variacao_normal_do_dia_a_dia():
    serie = [
        (date(2024, 1, 2), 10.00),
        (date(2024, 1, 3), 10.30),  # +3%, dentro do normal
        (date(2024, 1, 4), 9.90),   # -3,9%, dentro do normal
    ]
    assert detectar_candidatos_eventos_societarios(serie) == []


def test_deteccao_nao_repete_alerta_de_evento_ja_conhecido():
    serie = [
        (date(2024, 6, 2), 40.00),
        (date(2024, 6, 3), 20.00),  # desdobramento 1:2 -> cai 50%
    ]
    # sem eventos conhecidos -> detecta
    assert len(detectar_candidatos_eventos_societarios(serie)) == 1
    # já sabendo do evento nessa data -> não alerta de novo
    ja_conhecido = [EventoSocietario(data=date(2024, 6, 3), fator=2.0)]
    assert detectar_candidatos_eventos_societarios(serie, ja_conhecido) == []
