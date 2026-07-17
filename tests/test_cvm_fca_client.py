"""
Testes do módulo de diagnóstico do FCA (valor_mobiliario). Como não
temos confirmação do schema real deste arquivo específico, estes testes
validam o COMPORTAMENTO DEFENSIVO do módulo (expõe colunas reais, cai
para amostra genérica quando o filtro não bate) — não validam a
interpretação de nenhum campo específico, porque ainda não sabemos os
nomes reais.
"""

from app.data_sources.cvm_fca_client import inspecionar_valor_mobiliario_de_texto


def test_expoe_colunas_reais_do_cabecalho():
    csv_texto = "CNPJ_Companhia;Nome_Empresarial;Coluna_Desconhecida\n33000167000101;PETROBRAS;abc"
    resultado = inspecionar_valor_mobiliario_de_texto(csv_texto, cnpjs_filtro=None)
    assert resultado["colunas_encontradas"] == ["CNPJ_Companhia", "Nome_Empresarial", "Coluna_Desconhecida"]


def test_filtro_por_cnpj_funciona_quando_nome_da_coluna_bate():
    csv_texto = (
        "CNPJ_Companhia;Nome_Empresarial\n"
        "33000167000101;PETROBRAS\n"
        "33592510000154;VALE\n"
    )
    resultado = inspecionar_valor_mobiliario_de_texto(csv_texto, cnpjs_filtro={"33000167000101"})
    assert resultado["total_linhas_amostra"] == 1
    assert resultado["amostra"][0]["Nome_Empresarial"] == "PETROBRAS"
    assert "aviso" not in resultado


def test_cai_para_amostra_generica_quando_coluna_cnpj_tem_nome_diferente():
    # Coluna de CNPJ com nome que NÃO está entre os candidatos testados
    csv_texto = (
        "Cnpj_Da_Empresa;Nome_Empresarial\n"
        "33000167000101;PETROBRAS\n"
        "33592510000154;VALE\n"
    )
    resultado = inspecionar_valor_mobiliario_de_texto(csv_texto, cnpjs_filtro={"33000167000101"})
    assert "aviso" in resultado
    assert resultado["total_linhas_amostra"] == 2  # amostra genérica, sem filtro
    assert "Cnpj_Da_Empresa" in resultado["colunas_encontradas"]


def test_sem_filtro_retorna_amostra_generica_direto():
    csv_texto = "Col1;Col2\nA;B\nC;D\n"
    resultado = inspecionar_valor_mobiliario_de_texto(csv_texto, cnpjs_filtro=None)
    assert resultado["total_linhas_amostra"] == 2
    assert "aviso" not in resultado


def test_respeita_limite_de_amostra():
    linhas = "\n".join(f"33000167000101;linha{i}" for i in range(50))
    csv_texto = "CNPJ_Companhia;Dado\n" + linhas
    resultado = inspecionar_valor_mobiliario_de_texto(csv_texto, cnpjs_filtro={"33000167000101"}, limite_amostra=5)
    assert resultado["total_linhas_amostra"] == 5
