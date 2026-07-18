"""
Cliente para a seção "Valores Mobiliários" do Formulário Cadastral (FCA)
da CVM — a fonte candidata para quantidade de ações emitidas por
empresa, necessária para calcular LPA/VPA (Graham) e dividendo por ação
(Bazin) a partir dos totais que já temos (lucro líquido via CVM/DFP,
preço via B3).

⚠️ DIAGNÓSTICO, NÃO PRODUÇÃO: diferente de cvm_client.py e
b3_cotahist_client.py, este módulo NÃO tenta interpretar semanticamente
os dados ainda — não temos confirmação do nome exato das colunas desse
arquivo (o site da CVM bloqueou acesso automatizado ao dicionário de
dados a partir deste ambiente de desenvolvimento). Em vez de adivinhar
nomes de coluna (risco real: um nome errado não dá erro, silenciosamente
traz um número errado que alimentaria diretamente o cálculo de LPA/VPA),
este módulo baixa o arquivo real e expõe o cabeçalho + algumas linhas de
amostra para inspeção humana — via `inspecionar_valor_mobiliario` e o
endpoint `GET /diagnostico/cvm/fca-valor-mobiliario`.

Depois de confirmar os nomes reais das colunas (rodando o diagnóstico em
produção, onde há acesso à internet), o parser definitivo de extração de
quantidade de ações deve ser adicionado aqui, seguindo o mesmo padrão de
`cvm_client.py` (função pura, testável, com testes usando o formato
confirmado).

URL do arquivo anual:
  https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FCA/DADOS/fca_cia_aberta_{ANO}.zip
Arquivo esperado dentro do zip:
  fca_cia_aberta_valor_mobiliario_{ANO}.csv
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

from app.data_sources.csv_diagnostico import inspecionar_csv_de_texto

logger = logging.getLogger(__name__)

BASE_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FCA/DADOS"
CACHE_DIR = Path("cache_cvm_fca")


class CvmFcaClientError(Exception):
    """Erro ao baixar ou interpretar arquivos do Formulário Cadastral (FCA) da CVM."""


def _url_fca_ano(ano: int) -> str:
    return f"{BASE_URL}/fca_cia_aberta_{ano}.zip"


async def _baixar_zip_ano(ano: int) -> Path:
    """Baixa (streaming para disco, com cache) o zip anual do FCA."""
    import httpx  # import local: mantém este módulo testável sem exigir httpx instalado

    CACHE_DIR.mkdir(exist_ok=True)
    caminho_cache = CACHE_DIR / f"fca_cia_aberta_{ano}.zip"

    if caminho_cache.exists():
        logger.info("Usando cache local para FCA %d", ano)
        return caminho_cache

    url = _url_fca_ano(ano)
    logger.info("Baixando FCA %d da CVM (streaming para disco): %s", ano, url)
    caminho_parcial = CACHE_DIR / f"fca_cia_aberta_{ano}.zip.partial"

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("GET", url, follow_redirects=True) as resp:
                if resp.status_code != 200:
                    raise CvmFcaClientError(
                        f"CVM retornou status {resp.status_code} para FCA {ano} (url: {url})"
                    )
                with open(caminho_parcial, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)
    except httpx.HTTPError as exc:
        if caminho_parcial.exists():
            caminho_parcial.unlink()
        raise CvmFcaClientError(f"Falha de rede ao baixar FCA {ano}: {exc}") from exc

    caminho_parcial.rename(caminho_cache)
    return caminho_cache


def _nome_csv_valor_mobiliario(zf: zipfile.ZipFile, ano: int) -> str:
    nome_esperado = f"fca_cia_aberta_valor_mobiliario_{ano}.csv"
    nomes = zf.namelist()
    if nome_esperado in nomes:
        return nome_esperado
    candidato = next((n for n in nomes if "valor_mobiliario" in n.lower()), None)
    if candidato is None:
        raise CvmFcaClientError(
            f"Nenhum arquivo 'valor_mobiliario' encontrado no zip do FCA {ano}. Conteúdo: {nomes}"
        )
    return candidato


def inspecionar_valor_mobiliario_de_texto(
    conteudo_csv: str, cnpjs_filtro: set[str] | None, limite_amostra: int = 20
) -> dict:
    """
    Lê um CSV de valor_mobiliario (já em memória — usado nos testes) e
    devolve o cabeçalho (nomes reais das colunas) + linhas de amostra,
    filtradas pelos CNPJs pedidos quando informado. Não interpreta o
    significado de nenhuma coluna — só expõe o que existe, para
    confirmação humana antes de virar um parser de produção.

    Delega para o utilitário compartilhado em csv_diagnostico.py (também
    usado pelo diagnóstico de arquivos do DFP, em cvm_client.py).
    """
    return inspecionar_csv_de_texto(conteudo_csv, cnpjs_filtro, limite_amostra)


async def inspecionar_valor_mobiliario(ano: int, cnpjs_filtro: set[str] | None = None) -> dict:
    """Baixa o arquivo real do ano e devolve cabeçalho + amostra de linhas
    (filtradas pelos CNPJs pedidos, se informado) para inspeção manual —
    ver ressalva no topo do módulo sobre por que isso é diagnóstico, não
    um parser de produção ainda."""
    caminho_zip = await _baixar_zip_ano(ano)
    with zipfile.ZipFile(caminho_zip) as zf:
        nome_csv = _nome_csv_valor_mobiliario(zf, ano)
        # Arquivos da CVM são tradicionalmente em Latin-1 (ISO-8859-1), não UTF-8.
        conteudo_csv = zf.read(nome_csv).decode("latin-1")

    return inspecionar_valor_mobiliario_de_texto(conteudo_csv, cnpjs_filtro)
