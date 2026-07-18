"""
Cliente para o Portal de Dados Abertos da CVM (dados.cvm.gov.br),
especificamente o conjunto DFP (Demonstrações Financeiras Padronizadas).

Fonte oficial, gratuita e sem limite de requisições — diferente da Brapi,
não depende de plano pago. O trade-off é que os dados vêm em lote (um
arquivo .zip por ano, com TODAS as companhias abertas do Brasil dentro),
não por empresa individual — por isso a estratégia aqui é: baixar o
arquivo do ano uma vez, indexar por CNPJ em memória, e responder consultas
de múltiplos tickers a partir desse índice, em vez de uma requisição por
empresa.

URL do arquivo de um ano: 
  https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS/dfp_cia_aberta_{ANO}.zip

Dentro do zip, o arquivo relevante para lucro líquido é a Demonstração de
Resultado (DRE) consolidada:
  dfp_cia_aberta_DRE_con_{ANO}.csv
(caem para a versão "_ind_" — individual, não consolidada — quando a
empresa não publica DRE consolidada, ex: empresas sem subsidiárias)

⚠️ IMPORTANTE — não testado contra o arquivo real: este ambiente de
desenvolvimento não tem acesso à internet, então o parsing abaixo foi
validado com um CSV sintético que reproduz o formato documentado
publicamente pela CVM (ver tests/test_cvm_client.py), não com o arquivo
real baixado. Antes de rodar em produção, valide o resultado de
`buscar_lucro_liquido_por_ano` para 2-3 empresas conhecidas contra o
demonstrativo publicado no site de RI da própria empresa.
"""

from __future__ import annotations

import csv
import io
import logging
import zipfile
from pathlib import Path

from app.data_sources.csv_diagnostico import inspecionar_csv_de_texto

logger = logging.getLogger(__name__)

BASE_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/DFP/DADOS"
CACHE_DIR = Path("cache_cvm")

# Conta padrão do plano de contas da CVM para "Lucro/Prejuízo Consolidado
# do Período" na DRE. Usado como filtro primário; DS_CONTA serve de
# checagem/fallback caso o código varie entre anos ou tipo de empresa
# (ex: instituições financeiras têm plano de contas um pouco diferente).
CODIGO_CONTA_LUCRO_LIQUIDO = "3.11"
DESCRICOES_LUCRO_LIQUIDO_ACEITAS = (
    "lucro/prejuízo consolidado do período",
    "lucro/prejuízo do período",
    "lucro ou prejuízo líquido consolidado do exercício",
    "lucro ou prejuízo do período",
)
# Linhas a evitar mesmo que contenham "lucro" e "período" — são
# desagregações do lucro total, não o lucro líquido da empresa como um todo.
DESCRICOES_A_EXCLUIR = ("atribuível", "não controlad", "sócios da empresa controladora")


class CvmClientError(Exception):
    """Erro ao baixar ou interpretar arquivos de dados abertos da CVM."""


def _url_dfp_ano(ano: int) -> str:
    return f"{BASE_URL}/dfp_cia_aberta_{ano}.zip"


async def _baixar_zip_ano(ano: int) -> Path:
    """Baixa o zip anual da DFP, com cache em disco (arquivos são grandes,
    ~dezenas de MB, e não mudam com frequência dentro do mesmo ano).
    Devolve o caminho do arquivo, não o conteúdo — evita segurar o zip
    inteiro em memória quando só precisamos listar/ler um arquivo
    específico de dentro dele."""
    import httpx  # import local: mantém este módulo testável (parsing puro) sem exigir httpx instalado

    CACHE_DIR.mkdir(exist_ok=True)
    caminho_cache = CACHE_DIR / f"dfp_cia_aberta_{ano}.zip"

    if caminho_cache.exists():
        logger.info("Usando cache local para DFP %d", ano)
        return caminho_cache

    url = _url_dfp_ano(ano)
    logger.info("Baixando DFP %d da CVM: %s", ano, url)
    caminho_parcial = CACHE_DIR / f"dfp_cia_aberta_{ano}.zip.partial"
    try:
        async with httpx.AsyncClient(timeout=60.0) as client:
            async with client.stream("GET", url, follow_redirects=True) as resp:
                if resp.status_code != 200:
                    raise CvmClientError(f"CVM retornou status {resp.status_code} para DFP {ano} (url: {url})")
                with open(caminho_parcial, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)
    except httpx.HTTPError as exc:
        if caminho_parcial.exists():
            caminho_parcial.unlink()
        raise CvmClientError(f"Falha de rede ao baixar DFP {ano}: {exc}") from exc

    caminho_parcial.rename(caminho_cache)
    return caminho_cache


def listar_arquivos_do_zip(caminho_zip: Path) -> list[str]:
    """Lista os nomes de todos os arquivos dentro do zip anual do DFP —
    usado para descobrir o nome exato do arquivo de composição de
    capital (quantidade de ações), que ainda não foi confirmado."""
    with zipfile.ZipFile(caminho_zip) as zf:
        return zf.namelist()


def inspecionar_arquivo_do_zip(
    caminho_zip: Path, nome_arquivo: str, cnpjs_filtro: set[str] | None = None, limite_amostra: int = 20
) -> dict:
    """Lê um arquivo específico de dentro do zip e devolve cabeçalho +
    amostra de linhas, para conferência humana antes de virar parser de
    produção — mesmo padrão usado em cvm_fca_client.py."""
    with zipfile.ZipFile(caminho_zip) as zf:
        if nome_arquivo not in zf.namelist():
            raise CvmClientError(f"Arquivo {nome_arquivo} não encontrado no zip. Use listar_arquivos_dfp primeiro.")
        # Arquivos da CVM são tradicionalmente em Latin-1 (ISO-8859-1), não UTF-8.
        conteudo = zf.read(nome_arquivo).decode("latin-1")
    return inspecionar_csv_de_texto(conteudo, cnpjs_filtro, limite_amostra)


async def listar_arquivos_dfp(ano: int) -> list[str]:
    """Baixa (ou usa cache) o zip do ano e lista os arquivos dentro dele."""
    caminho_zip = await _baixar_zip_ano(ano)
    return listar_arquivos_do_zip(caminho_zip)


async def inspecionar_arquivo_dfp(
    ano: int, nome_arquivo: str, cnpjs_filtro: set[str] | None = None, limite_amostra: int = 20
) -> dict:
    """Baixa (ou usa cache) o zip do ano e inspeciona um arquivo
    específico dentro dele (cabeçalho + amostra), sem interpretar o
    significado de nenhuma coluna ainda."""
    caminho_zip = await _baixar_zip_ano(ano)
    return inspecionar_arquivo_do_zip(caminho_zip, nome_arquivo, cnpjs_filtro, limite_amostra)


def _extrair_csv_do_zip(caminho_zip: Path, ano: int, sufixo: str) -> str:
    """Extrai o texto de um CSV específico de dentro do zip anual (ex: sufixo='DRE_con')."""
    nome_arquivo = f"dfp_cia_aberta_{sufixo}_{ano}.csv"
    with zipfile.ZipFile(caminho_zip) as zf:
        if nome_arquivo not in zf.namelist():
            disponiveis = ", ".join(zf.namelist()[:10])
            raise CvmClientError(
                f"Arquivo {nome_arquivo} não encontrado no zip. Alguns arquivos disponíveis: {disponiveis}"
            )
        # Arquivos da CVM são tradicionalmente em Latin-1 (ISO-8859-1), não UTF-8.
        return zf.read(nome_arquivo).decode("latin-1")


def _eh_linha_de_lucro_liquido(cd_conta: str, ds_conta: str) -> bool:
    ds_lower = ds_conta.strip().lower()

    if any(exclusao in ds_lower for exclusao in DESCRICOES_A_EXCLUIR):
        return False

    if cd_conta.strip() == CODIGO_CONTA_LUCRO_LIQUIDO:
        return True

    return any(descricao in ds_lower for descricao in DESCRICOES_LUCRO_LIQUIDO_ACEITAS)


def parsear_lucro_liquido_por_cnpj(conteudo_csv: str) -> dict[str, float]:
    """
    Faz o parsing de um CSV de DRE da CVM e retorna {cnpj_normalizado: lucro_liquido}.

    Considera apenas linhas com ORDEM_EXERC == 'ÚLTIMO' — os arquivos da
    CVM também incluem o exercício anterior como comparação, e incluir
    essas linhas duplicaria/confundiria os valores.

    Respeita ESCALA_MOEDA: quando os valores estão em milhares ('MIL'),
    multiplica por 1000 para obter o valor real em reais.
    """
    leitor = csv.DictReader(io.StringIO(conteudo_csv), delimiter=";")
    resultado: dict[str, float] = {}

    for linha in leitor:
        try:
            if linha.get("ORDEM_EXERC", "").strip().upper() != "ÚLTIMO":
                continue

            cd_conta = linha.get("CD_CONTA", "")
            ds_conta = linha.get("DS_CONTA", "")
            if not _eh_linha_de_lucro_liquido(cd_conta, ds_conta):
                continue

            cnpj = "".join(c for c in linha.get("CNPJ_CIA", "") if c.isdigit())
            if not cnpj:
                continue

            valor_bruto = float(linha.get("VL_CONTA", "0").replace(",", "."))
            escala = linha.get("ESCALA_MOEDA", "").strip().upper()
            fator = 1000.0 if escala == "MIL" else 1.0

            resultado[cnpj] = valor_bruto * fator
        except (ValueError, KeyError) as exc:
            logger.warning("Linha ignorada por erro de parsing: %s (%s)", exc, linha)
            continue

    return resultado


async def buscar_lucro_liquido_por_ano(ano: int) -> dict[str, float]:
    """
    Retorna {cnpj_normalizado: lucro_liquido} para todas as companhias
    com DRE consolidada publicada naquele ano. Baixa e cacheia o zip
    anual inteiro na primeira chamada do ano; chamadas seguintes usam o
    cache em disco.
    """
    caminho_zip = await _baixar_zip_ano(ano)
    try:
        csv_dre = _extrair_csv_do_zip(caminho_zip, ano, "DRE_con")
    except CvmClientError:
        # Nem toda empresa (ou nem todo ano) tem DRE consolidada disponível;
        # não deveria acontecer para o arquivo inteiro, mas se o nome do
        # recurso mudar, isso pelo menos falha com uma mensagem clara.
        raise

    return parsear_lucro_liquido_por_cnpj(csv_dre)


# Colunas confirmadas em produção (ver diagnóstico em
# /diagnostico/cvm/dfp-arquivo) no arquivo
# dfp_cia_aberta_composicao_capital_{ANO}.csv.
COLUNA_CNPJ_COMPOSICAO_CAPITAL = "CNPJ_CIA"
COLUNA_VERSAO_COMPOSICAO_CAPITAL = "VERSAO"
COLUNA_ACOES_ORDINARIAS = "QT_ACAO_ORDIN_CAP_INTEGR"
COLUNA_ACOES_PREFERENCIAIS = "QT_ACAO_PREF_CAP_INTEGR"
COLUNA_ACOES_TOTAL_CAPITAL = "QT_ACAO_TOTAL_CAP_INTEGR"
COLUNA_ACOES_TOTAL_TESOURO = "QT_ACAO_TOTAL_TESOURO"

# Limiar de plausibilidade: empresas de capital aberto listadas na B3
# raramente têm menos de dezenas de milhões de ações em circulação — se
# o valor lido for menor que isso, é sinal de erro no dado de origem.
# Caso real confirmado em produção: no DFP de 2024, Vale S.A. reportou
# 4.539.008 ações no total, quando o valor real é ~4,5 BILHÕES
# (confirmado contra atas de assembleia de acionistas, fora deste
# sistema). O mesmo padrão (fator ~1000x menor que o esperado) aparece
# em outras empresas do mesmo arquivo (ex: Itaú Unibanco e Ambev,
# conhecidamente na casa de bilhões de ações, aparecem na casa de
# milhões) — parece um erro sistemático de escala em parte dos dados
# desse arquivo específico da CVM, não um caso isolado. 50 milhões é um
# piso seguro: nenhuma das empresas do nosso universo de teste com valor
# correto (ex: Engie, ~815 milhões) chega perto desse piso por baixo.
LIMIAR_MINIMO_PLAUSIVEL_ACOES = 50_000_000


def parsear_composicao_capital_por_cnpj(conteudo_csv: str) -> dict[str, dict]:
    """
    Faz o parsing do CSV de composição de capital e retorna
    {cnpj_normalizado: {...}} com a quantidade de ações em circulação
    (capital integralizado menos ações em tesouraria, que pertencem à
    própria empresa e não circulam no mercado) e um sinalizador
    `suspeito` quando o valor falha num teste básico de plausibilidade.

    Quando há mais de uma versão (retificação) para a mesma empresa,
    usa a de maior VERSAO — a mais recente/corrigida.
    """
    leitor = csv.DictReader(io.StringIO(conteudo_csv), delimiter=";")
    melhores_por_cnpj: dict[str, dict] = {}

    for linha in leitor:
        try:
            cnpj = "".join(c for c in linha.get(COLUNA_CNPJ_COMPOSICAO_CAPITAL, "") if c.isdigit())
            if not cnpj:
                continue

            versao = int(linha.get(COLUNA_VERSAO_COMPOSICAO_CAPITAL, "0") or "0")
            existente = melhores_por_cnpj.get(cnpj)
            if existente is not None and versao <= existente["versao"]:
                continue  # já temos uma versão igual ou mais recente para essa empresa

            ordinarias = int(linha.get(COLUNA_ACOES_ORDINARIAS, "0") or "0")
            preferenciais = int(linha.get(COLUNA_ACOES_PREFERENCIAIS, "0") or "0")
            total_capital = int(linha.get(COLUNA_ACOES_TOTAL_CAPITAL, "0") or "0")
            total_tesouro = int(linha.get(COLUNA_ACOES_TOTAL_TESOURO, "0") or "0")

            em_circulacao = total_capital - total_tesouro

            suspeito = (
                total_tesouro > total_capital  # inconsistência interna do próprio arquivo
                or em_circulacao < LIMIAR_MINIMO_PLAUSIVEL_ACOES  # implausível para uma cia aberta
            )

            melhores_por_cnpj[cnpj] = {
                "versao": versao,
                "acoes_ordinarias": ordinarias,
                "acoes_preferenciais": preferenciais,
                "acoes_total_capital": total_capital,
                "acoes_total_tesouro": total_tesouro,
                "acoes_em_circulacao": em_circulacao,
                "suspeito": suspeito,
            }
        except (ValueError, KeyError) as exc:
            logger.warning("Linha de composição de capital ignorada por erro de parsing: %s (%s)", exc, linha)
            continue

    return melhores_por_cnpj


async def buscar_composicao_capital_por_ano(ano: int) -> dict[str, dict]:
    """
    Retorna {cnpj_normalizado: {...}} com a quantidade de ações em
    circulação de cada companhia naquele ano — ver
    `parsear_composicao_capital_por_cnpj` para os campos e o
    sinalizador `suspeito`. Reusa o mesmo zip anual já baixado/cacheado
    para o lucro líquido.
    """
    caminho_zip = await _baixar_zip_ano(ano)
    csv_composicao = _extrair_csv_do_zip(caminho_zip, ano, "composicao_capital")
    return parsear_composicao_capital_por_cnpj(csv_composicao)
