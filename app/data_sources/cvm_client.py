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


async def _baixar_zip_ano(ano: int) -> bytes:
    """Baixa o zip anual da DFP, com cache em disco (arquivos são grandes,
    ~dezenas de MB, e não mudam com frequência dentro do mesmo ano)."""
    import httpx  # import local: mantém este módulo testável (parsing puro) sem exigir httpx instalado

    CACHE_DIR.mkdir(exist_ok=True)
    caminho_cache = CACHE_DIR / f"dfp_cia_aberta_{ano}.zip"

    if caminho_cache.exists():
        logger.info("Usando cache local para DFP %d", ano)
        return caminho_cache.read_bytes()

    url = _url_dfp_ano(ano)
    logger.info("Baixando DFP %d da CVM: %s", ano, url)
    async with httpx.AsyncClient(timeout=60.0) as client:
        try:
            resp = await client.get(url, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise CvmClientError(f"Falha de rede ao baixar DFP {ano}: {exc}") from exc

    if resp.status_code != 200:
        raise CvmClientError(f"CVM retornou status {resp.status_code} para DFP {ano} (url: {url})")

    caminho_cache.write_bytes(resp.content)
    return resp.content


def _extrair_csv_do_zip(conteudo_zip: bytes, ano: int, sufixo: str) -> str:
    """Extrai o texto de um CSV específico de dentro do zip anual (ex: sufixo='DRE_con')."""
    nome_arquivo = f"dfp_cia_aberta_{sufixo}_{ano}.csv"
    with zipfile.ZipFile(io.BytesIO(conteudo_zip)) as zf:
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
    conteudo_zip = await _baixar_zip_ano(ano)
    try:
        csv_dre = _extrair_csv_do_zip(conteudo_zip, ano, "DRE_con")
    except CvmClientError:
        # Nem toda empresa (ou nem todo ano) tem DRE consolidada disponível;
        # não deveria acontecer para o arquivo inteiro, mas se o nome do
        # recurso mudar, isso pelo menos falha com uma mensagem clara.
        raise

    return parsear_lucro_liquido_por_cnpj(csv_dre)
