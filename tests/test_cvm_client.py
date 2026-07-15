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

from app.data_sources.cvm_client import parsear_lucro_liquido_por_cnpj

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
