"""
Testes do parser de DRE da CVM. Como este ambiente não tem acesso à
internet, não é possível baixar o arquivo real aqui — este teste usa um
CSV sintético que reproduz o formato documentado publicamente pela CVM
(colunas, delimitador ';', encoding Latin-1, ORDEM_EXERC, ESCALA_MOEDA).

⚠️ Antes de usar em produção, valide `buscar_lucro_liquido_por_ano` (que
baixa o arquivo real) para 2-3 empresas conhecidas contra o demonstrativo
publicado no site de RI de cada empresa — este teste garante que a
LÓGICA de parsing está correta, não que o schema assumido bate 100% com
o arquivo real de todos os anos (a CVM já alterou nomes de coluna entre
versões, conforme o changelog do portal).
"""

from app.data_sources.cvm_client import (
    parsear_composicao_capital_por_cnpj,
    parsear_lucro_liquido_por_cnpj,
    parsear_proventos_por_cnpj,
)

CABECALHO = (
    "CNPJ_CIA;DT_REFER;VERSAO;DENOM_CIA;CD_CVM;GRUPO_DFP;MOEDA;ESCALA_MOEDA;"
    "ORDEM_EXERC;DT_INI_EXERC;DT_FIM_EXERC;CD_CONTA;DS_CONTA;VL_CONTA;ST_CONTA_FIXA"
)


def _linha(cnpj, ordem, cd_conta, ds_conta, valor, escala="MIL"):
    return (
        f"{cnpj};2024-12-31;1;EMPRESA TESTE;12345;DF Consolidado;REAL;{escala};"
        f"{ordem};2024-01-01;2024-12-31;{cd_conta};{ds_conta};{valor};S"
    )


def test_extrai_lucro_liquido_da_linha_correta():
    csv_texto = "\n".join([
        CABECALHO,
        _linha("33.000.167/0001-01", "ÚLTIMO", "3.11", "Lucro/Prejuízo Consolidado do Período", "1000"),
        _linha("33.000.167/0001-01", "PENÚLTIMO", "3.11", "Lucro/Prejuízo Consolidado do Período", "800"),
    ])
    resultado = parsear_lucro_liquido_por_cnpj(csv_texto)
    assert resultado["33000167000101"] == 1_000_000.0  # 1000 * 1000 (escala MIL)


def test_ignora_linha_de_exercicio_anterior():
    csv_texto = "\n".join([
        CABECALHO,
        _linha("11.111.111/0001-11", "PENÚLTIMO", "3.11", "Lucro/Prejuízo Consolidado do Período", "500"),
    ])
    resultado = parsear_lucro_liquido_por_cnpj(csv_texto)
    assert "11111111000111" not in resultado


def test_ignora_linha_de_lucro_atribuivel_a_nao_controladores():
    csv_texto = "\n".join([
        CABECALHO,
        _linha("22.222.222/0001-22", "ÚLTIMO", "3.11", "Lucro/Prejuízo Consolidado do Período", "1000"),
        _linha("22.222.222/0001-22", "ÚLTIMO", "3.11.02", "Lucro Atribuível a Sócios Não Controladores", "50"),
    ])
    resultado = parsear_lucro_liquido_por_cnpj(csv_texto)
    # deve pegar o lucro total (1000), não sobrescrever com a linha de não controladores
    assert resultado["22222222000122"] == 1_000_000.0


def test_respeita_escala_unidade_sem_multiplicar():
    csv_texto = "\n".join([
        CABECALHO,
        _linha("33.333.333/0001-33", "ÚLTIMO", "3.11", "Lucro/Prejuízo Consolidado do Período", "1000", escala="UNIDADE"),
    ])
    resultado = parsear_lucro_liquido_por_cnpj(csv_texto)
    assert resultado["33333333000133"] == 1000.0


def test_lucro_negativo_prejuizo():
    csv_texto = "\n".join([
        CABECALHO,
        _linha("44.444.444/0001-44", "ÚLTIMO", "3.11", "Lucro/Prejuízo Consolidado do Período", "-2500"),
    ])
    resultado = parsear_lucro_liquido_por_cnpj(csv_texto)
    assert resultado["44444444000144"] == -2_500_000.0


def test_multiplas_empresas_no_mesmo_arquivo():
    csv_texto = "\n".join([
        CABECALHO,
        _linha("11.111.111/0001-11", "ÚLTIMO", "3.11", "Lucro/Prejuízo Consolidado do Período", "100"),
        _linha("22.222.222/0001-22", "ÚLTIMO", "3.11", "Lucro/Prejuízo Consolidado do Período", "200"),
        _linha("33.333.333/0001-33", "ÚLTIMO", "3.11", "Lucro/Prejuízo Consolidado do Período", "300"),
    ])
    resultado = parsear_lucro_liquido_por_cnpj(csv_texto)
    assert len(resultado) == 3
    assert resultado["11111111000111"] == 100_000.0
    assert resultado["22222222000122"] == 200_000.0
    assert resultado["33333333000133"] == 300_000.0


CABECALHO_COMPOSICAO_CAPITAL = "CNPJ_CIA;DT_REFER;VERSAO;DENOM_CIA;QT_ACAO_ORDIN_CAP_INTEGR;QT_ACAO_PREF_CAP_INTEGR;QT_ACAO_TOTAL_CAP_INTEGR;QT_ACAO_ORDIN_TESOURO;QT_ACAO_PREF_TESOURO;QT_ACAO_TOTAL_TESOURO"


def _linha_composicao_capital(cnpj, versao, ordinarias, preferenciais, total_capital, total_tesouro):
    return f"{cnpj};2024-12-31;{versao};EMPRESA TESTE;{ordinarias};{preferenciais};{total_capital};0;0;{total_tesouro}"


def test_composicao_capital_calcula_acoes_em_circulacao():
    csv_texto = "\n".join([
        CABECALHO_COMPOSICAO_CAPITAL,
        _linha_composicao_capital("33.000.167/0001-01", 1, 7442454142, 5602042788, 13044496930, 155764169),
    ])
    resultado = parsear_composicao_capital_por_cnpj(csv_texto)
    dado = resultado["33000167000101"]
    assert dado["acoes_em_circulacao"] == 13044496930 - 155764169
    assert dado["suspeito"] is False


def test_composicao_capital_usa_versao_mais_recente():
    csv_texto = "\n".join([
        CABECALHO_COMPOSICAO_CAPITAL,
        _linha_composicao_capital("11.111.111/0001-11", 1, 1000000, 0, 1000000, 0),
        _linha_composicao_capital("11.111.111/0001-11", 2, 2000000, 0, 2000000, 0),  # retificação
    ])
    resultado = parsear_composicao_capital_por_cnpj(csv_texto)
    assert resultado["11111111000111"]["acoes_total_capital"] == 2000000
    assert resultado["11111111000111"]["versao"] == 2


def test_composicao_capital_marca_suspeito_caso_real_vale_2024():
    """Reproduz o caso real descoberto em produção: a Vale reportou
    4.539.008 ações totais no DFP 2024, quando o valor real é ~4,5
    bilhões (confirmado contra atas de assembleia de acionistas) — um
    erro de magnitude na própria fonte oficial. O parser não deve
    silenciosamente confiar nesse valor implausível."""
    csv_texto = "\n".join([
        CABECALHO_COMPOSICAO_CAPITAL,
        _linha_composicao_capital("33.592.510/0001-54", 3, 4539008, 0, 4539008, 270288),
    ])
    resultado = parsear_composicao_capital_por_cnpj(csv_texto)
    dado = resultado["33592510000154"]
    assert dado["suspeito"] is True


def test_composicao_capital_marca_suspeito_quando_tesouro_maior_que_capital():
    # inconsistência interna do arquivo: tesouraria não pode ser maior que o capital total
    csv_texto = "\n".join([
        CABECALHO_COMPOSICAO_CAPITAL,
        _linha_composicao_capital("22.222.222/0001-22", 1, 500000000, 500000000, 1000000000, 2000000000),
    ])
    resultado = parsear_composicao_capital_por_cnpj(csv_texto)
    assert resultado["22222222000122"]["suspeito"] is True


def test_composicao_capital_empresa_normal_nao_marcada_como_suspeita():
    csv_texto = "\n".join([
        CABECALHO_COMPOSICAO_CAPITAL,
        _linha_composicao_capital("84.429.695/0001-11", 1, 4197317998, 0, 4197317998, 1780620),
    ])
    resultado = parsear_composicao_capital_por_cnpj(csv_texto)
    assert resultado["84429695000111"]["suspeito"] is False
    assert resultado["84429695000111"]["acoes_em_circulacao"] == 4197317998 - 1780620


from app.data_sources.cvm_client import parsear_patrimonio_liquido_por_cnpj


def test_extrai_patrimonio_liquido_da_linha_correta():
    csv_texto = "\n".join([
        CABECALHO,
        _linha("33.000.167/0001-01", "ÚLTIMO", "2.03", "Patrimônio Líquido Consolidado", "50000"),
        _linha("33.000.167/0001-01", "PENÚLTIMO", "2.03", "Patrimônio Líquido Consolidado", "45000"),
    ])
    resultado = parsear_patrimonio_liquido_por_cnpj(csv_texto)
    assert resultado["33000167000101"] == 50_000_000.0  # 50000 * 1000 (escala MIL)


def test_patrimonio_liquido_nao_confunde_com_lucro_liquido():
    # Mesmo arquivo com as duas contas -- cada parser deve pegar só a sua
    csv_texto = "\n".join([
        CABECALHO,
        _linha("11.111.111/0001-11", "ÚLTIMO", "3.11", "Lucro/Prejuízo Consolidado do Período", "1000"),
        _linha("11.111.111/0001-11", "ÚLTIMO", "2.03", "Patrimônio Líquido Consolidado", "20000"),
    ])
    lucro = parsear_lucro_liquido_por_cnpj(csv_texto)
    patrimonio = parsear_patrimonio_liquido_por_cnpj(csv_texto)
    assert lucro["11111111000111"] == 1_000_000.0
    assert patrimonio["11111111000111"] == 20_000_000.0


CABECALHO_DVA = (
    "CNPJ_CIA;DT_REFER;VERSAO;DENOM_CIA;CD_CVM;GRUPO_DFP;MOEDA;ESCALA_MOEDA;"
    "ORDEM_EXERC;DT_INI_EXERC;DT_FIM_EXERC;CD_CONTA;DS_CONTA;VL_CONTA;ST_CONTA_FIXA"
)


def _linha_dva(cnpj, ordem, cd_conta, ds_conta, valor, escala="MIL"):
    return (
        f"{cnpj};2024-12-31;1;EMPRESA TESTE;12345;DVA;REAL;{escala};"
        f"{ordem};2024-01-01;2024-12-31;{cd_conta};{ds_conta};{valor};S"
    )


def test_proventos_soma_dividendos_e_jcp():
    csv_texto = "\n".join([
        CABECALHO_DVA,
        _linha_dva("33.000.167/0001-01", "ÚLTIMO", "7.08.04.01", "Juros sobre o Capital Próprio", "22041000"),
        _linha_dva("33.000.167/0001-01", "ÚLTIMO", "7.08.04.02", "Dividendos", "14091000"),
    ])
    resultado = parsear_proventos_por_cnpj(csv_texto)
    dado = resultado["33000167000101"]
    assert dado["dividendos"] == 14091000000.0
    assert dado["jcp"] == 22041000000.0
    assert dado["proventos_total"] == 36132000000.0


def test_proventos_ignora_exercicio_anterior():
    csv_texto = "\n".join([
        CABECALHO_DVA,
        _linha_dva("11.111.111/0001-11", "PENÚLTIMO", "7.08.04.02", "Dividendos", "5000"),
    ])
    resultado = parsear_proventos_por_cnpj(csv_texto)
    assert "11111111000111" not in resultado


def test_proventos_empresa_sem_distribuicao_fica_zerada():
    csv_texto = "\n".join([
        CABECALHO_DVA,
        _linha_dva("22.222.222/0001-22", "ÚLTIMO", "7.08.01", "Pessoal", "1000"),  # não é dividendo nem JCP
    ])
    resultado = parsear_proventos_por_cnpj(csv_texto)
    assert "22222222000122" not in resultado  # nenhuma linha de provento encontrada


from app.data_sources.cvm_client import (
    parsear_ativos_por_cnpj,
    parsear_caixa_operacional_por_cnpj,
    parsear_passivos_por_cnpj,
    parsear_receita_lucro_bruto_por_cnpj,
)


def _linha_generica(cnpj, ordem, cd_conta, ds_conta, valor, escala="MIL"):
    return (
        f"{cnpj};2024-12-31;1;EMPRESA TESTE;12345;GRUPO;REAL;{escala};"
        f"{ordem};2024-01-01;2024-12-31;{cd_conta};{ds_conta};{valor};S"
    )


def test_ativos_extrai_total_e_circulante():
    csv_texto = "\n".join([
        CABECALHO,
        _linha_generica("11.111.111/0001-11", "ÚLTIMO", "1", "Ativo Total", "1000000"),
        _linha_generica("11.111.111/0001-11", "ÚLTIMO", "1.01", "Ativo Circulante", "300000"),
    ])
    resultado = parsear_ativos_por_cnpj(csv_texto)
    assert resultado["11111111000111"]["ativo_total"] == 1_000_000_000.0
    assert resultado["11111111000111"]["ativo_circulante"] == 300_000_000.0


def test_passivos_extrai_circulante_e_nao_circulante():
    csv_texto = "\n".join([
        CABECALHO,
        _linha_generica("11.111.111/0001-11", "ÚLTIMO", "2.01", "Passivo Circulante", "150000"),
        _linha_generica("11.111.111/0001-11", "ÚLTIMO", "2.02", "Passivo Não Circulante", "400000"),
    ])
    resultado = parsear_passivos_por_cnpj(csv_texto)
    assert resultado["11111111000111"]["passivo_circulante"] == 150_000_000.0
    assert resultado["11111111000111"]["passivo_nao_circulante"] == 400_000_000.0


def test_caixa_operacional_extrai_valor_correto():
    csv_texto = "\n".join([
        CABECALHO,
        _linha_generica("11.111.111/0001-11", "ÚLTIMO", "6.01", "Caixa Líquido Atividades Operacionais", "80000"),
    ])
    resultado = parsear_caixa_operacional_por_cnpj(csv_texto)
    assert resultado["11111111000111"] == 80_000_000.0


def test_receita_lucro_bruto_extrai_ambos():
    csv_texto = "\n".join([
        CABECALHO,
        _linha_generica("11.111.111/0001-11", "ÚLTIMO", "3.01", "Receita de Venda de Bens e/ou Serviços", "500000"),
        _linha_generica("11.111.111/0001-11", "ÚLTIMO", "3.03", "Resultado Bruto", "200000"),
    ])
    resultado = parsear_receita_lucro_bruto_por_cnpj(csv_texto)
    assert resultado["11111111000111"]["receita_liquida"] == 500_000_000.0
    assert resultado["11111111000111"]["lucro_bruto"] == 200_000_000.0


def test_ativos_extrai_caixa_e_equivalentes():
    csv_texto = "\n".join([
        CABECALHO,
        _linha_generica("11.111.111/0001-11", "ÚLTIMO", "1.01.01", "Caixa e Equivalentes de Caixa", "50000"),
    ])
    resultado = parsear_ativos_por_cnpj(csv_texto)
    assert resultado["11111111000111"]["caixa_e_equivalentes"] == 50_000_000.0


def test_passivos_soma_divida_financeira_circulante_e_nao_circulante():
    csv_texto = "\n".join([
        CABECALHO,
        _linha_generica("11.111.111/0001-11", "ÚLTIMO", "2.01.04", "Empréstimos e Financiamentos", "30000"),
        _linha_generica("11.111.111/0001-11", "ÚLTIMO", "2.02.01", "Empréstimos e Financiamentos", "90000"),
    ])
    resultado = parsear_passivos_por_cnpj(csv_texto)
    assert resultado["11111111000111"]["divida_financeira"] == 120_000_000.0


def test_passivos_sem_linha_de_divida_financeira_fica_none_nao_zero():
    csv_texto = "\n".join([
        CABECALHO,
        _linha_generica("22.222.222/0001-22", "ÚLTIMO", "2.01", "Passivo Circulante", "100000"),
        _linha_generica("22.222.222/0001-22", "ÚLTIMO", "2.02", "Passivo Não Circulante", "200000"),
    ])
    resultado = parsear_passivos_por_cnpj(csv_texto)
    assert resultado["22222222000122"]["divida_financeira"] is None


def test_dre_extrai_ebit():
    csv_texto = "\n".join([
        CABECALHO,
        _linha_generica(
            "11.111.111/0001-11", "ÚLTIMO", "3.05",
            "Resultado Antes do Resultado Financeiro e dos Tributos", "120000",
        ),
    ])
    resultado = parsear_receita_lucro_bruto_por_cnpj(csv_texto)
    assert resultado["11111111000111"]["ebit"] == 120_000_000.0
