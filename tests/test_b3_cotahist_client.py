"""
Testes do parser de cotações da B3 (COTAHIST). Como este ambiente não
tem acesso à internet, não é possível baixar o arquivo real aqui — estes
testes constroem linhas sintéticas byte a byte, nas posições exatas
documentadas no layout oficial da B3 (revisão 13/04/2017), para validar
que o parser lê os campos certos nas posições certas.

⚠️ Antes de usar em produção, confira o preço de fechamento de 1-2
tickers conhecidos, retornado por `buscar_cotacoes_ano`, contra o valor
publicado no site da B3 ou em qualquer corretora.
"""

from datetime import date

from app.data_sources.b3_cotahist_client import parsear_cotacoes, preco_mais_recente_por_ticker


def _linha_cotacao(data_pregao="20241230", codbdi="02", codneg="PETR4", tpmerc="010", preco_ultimo_centavos=3785):
    """Monta uma linha de 245 caracteres seguindo o layout oficial do
    registro tipo 01, preenchendo apenas os campos que o parser lê
    (os demais ficam com espaços/zeros, irrelevantes para os testes)."""
    linha = [" "] * 245

    def preencher(inicio_1indexed, fim_1indexed, valor, alinhar_direita=True, char_preenchimento="0"):
        largura = fim_1indexed - inicio_1indexed + 1
        valor_str = str(valor)
        if alinhar_direita:
            valor_str = valor_str.rjust(largura, char_preenchimento)
        else:
            valor_str = valor_str.ljust(largura, char_preenchimento if char_preenchimento != "0" else " ")
        for i, c in enumerate(valor_str[:largura]):
            linha[inicio_1indexed - 1 + i] = c

    preencher(1, 2, "01")
    preencher(3, 10, data_pregao)
    preencher(11, 12, codbdi)
    preencher(13, 24, codneg, alinhar_direita=False, char_preenchimento=" ")
    preencher(25, 27, tpmerc)
    preencher(109, 121, preco_ultimo_centavos)  # PREULT, 13 dígitos, 2 casas implícitas

    return "".join(linha)


def test_extrai_preco_ultimo_de_ticker_correto():
    txt = _linha_cotacao(codneg="PETR4", preco_ultimo_centavos=3785)
    resultado = parsear_cotacoes(txt, {"PETR4"})
    assert resultado[("PETR4", date(2024, 12, 30))] == 37.85


def test_ignora_ticker_nao_solicitado():
    txt = _linha_cotacao(codneg="VALE3", preco_ultimo_centavos=6000)
    resultado = parsear_cotacoes(txt, {"PETR4"})
    assert ("VALE3", date(2024, 12, 30)) not in resultado
    assert len(resultado) == 0


def test_ignora_mercado_diferente_de_vista():
    txt = _linha_cotacao(codneg="PETR4", tpmerc="020")  # fracionário, não à vista
    resultado = parsear_cotacoes(txt, {"PETR4"})
    assert len(resultado) == 0


def test_ignora_codbdi_diferente_de_lote_padrao():
    txt = _linha_cotacao(codneg="PETR4", codbdi="08")  # recuperação judicial
    resultado = parsear_cotacoes(txt, {"PETR4"})
    assert len(resultado) == 0


def test_multiplas_linhas_multiplos_tickers():
    linhas = [
        _linha_cotacao(codneg="PETR4", preco_ultimo_centavos=3785, data_pregao="20241230"),
        _linha_cotacao(codneg="VALE3", preco_ultimo_centavos=6120, data_pregao="20241230"),
        _linha_cotacao(codneg="ITUB4", preco_ultimo_centavos=3350, data_pregao="20241230"),
    ]
    txt = "\n".join(linhas)
    resultado = parsear_cotacoes(txt, {"PETR4", "VALE3", "ITUB4"})
    assert resultado[("PETR4", date(2024, 12, 30))] == 37.85
    assert resultado[("VALE3", date(2024, 12, 30))] == 61.20
    assert resultado[("ITUB4", date(2024, 12, 30))] == 33.50


def test_preco_mais_recente_por_ticker_pega_a_data_maxima():
    linhas = [
        _linha_cotacao(codneg="PETR4", preco_ultimo_centavos=3000, data_pregao="20240102"),
        _linha_cotacao(codneg="PETR4", preco_ultimo_centavos=3785, data_pregao="20241230"),
        _linha_cotacao(codneg="PETR4", preco_ultimo_centavos=3500, data_pregao="20240615"),
    ]
    txt = "\n".join(linhas)
    cotacoes = parsear_cotacoes(txt, {"PETR4"})
    mais_recente = preco_mais_recente_por_ticker(cotacoes)
    assert mais_recente["PETR4"] == (date(2024, 12, 30), 37.85)
