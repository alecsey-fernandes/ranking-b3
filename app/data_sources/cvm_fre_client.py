"""
Cliente para o Formulário de Referência (FRE) da CVM — especificamente o
arquivo `fre_cia_aberta_distribuicao_dividendos` (Item 3.5 do Anexo 24 da
ICVM 480), candidato à fonte de histórico de dividendos por ação para
alimentar a estratégia de Bazin (`app/strategies/bazin.py`).

⚠️ DIAGNÓSTICO, NÃO PRODUÇÃO — mesmo motivo dos outros diagnósticos
(cvm_fca_client.py, dfp-arquivos): não temos confirmação do nome exato
das colunas desse arquivo específico. Este módulo baixa o arquivo real e
expõe cabeçalho + amostra para inspeção humana antes de qualquer parser
de produção usar esse dado.

URL do arquivo anual:
  https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FRE/DADOS/fre_cia_aberta_{ANO}.zip
Arquivo candidato dentro do zip:
  fre_cia_aberta_distribuicao_dividendos_{ANO}.csv
"""

from __future__ import annotations

import logging
import zipfile
from pathlib import Path

from app.data_sources.csv_diagnostico import inspecionar_csv_de_texto

logger = logging.getLogger(__name__)

BASE_URL = "https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FRE/DADOS"
CACHE_DIR = Path("cache_cvm_fre")


class CvmFreClientError(Exception):
    """Erro ao baixar ou interpretar arquivos do Formulário de Referência (FRE) da CVM."""


def _url_fre_ano(ano: int) -> str:
    return f"{BASE_URL}/fre_cia_aberta_{ano}.zip"


async def _baixar_zip_ano(ano: int) -> Path:
    """Baixa (streaming para disco, com cache) o zip anual do FRE."""
    import httpx  # import local: mantém este módulo testável sem exigir httpx instalado

    CACHE_DIR.mkdir(exist_ok=True)
    caminho_cache = CACHE_DIR / f"fre_cia_aberta_{ano}.zip"

    if caminho_cache.exists():
        logger.info("Usando cache local para FRE %d", ano)
        return caminho_cache

    url = _url_fre_ano(ano)
    logger.info("Baixando FRE %d da CVM (streaming para disco): %s", ano, url)
    caminho_parcial = CACHE_DIR / f"fre_cia_aberta_{ano}.zip.partial"

    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            async with client.stream("GET", url, follow_redirects=True) as resp:
                if resp.status_code != 200:
                    raise CvmFreClientError(
                        f"CVM retornou status {resp.status_code} para FRE {ano} (url: {url})"
                    )
                with open(caminho_parcial, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)
    except httpx.HTTPError as exc:
        if caminho_parcial.exists():
            caminho_parcial.unlink()
        raise CvmFreClientError(f"Falha de rede ao baixar FRE {ano}: {exc}") from exc

    caminho_parcial.rename(caminho_cache)
    return caminho_cache


def listar_arquivos_do_zip(caminho_zip: Path) -> list[str]:
    with zipfile.ZipFile(caminho_zip) as zf:
        return zf.namelist()


def inspecionar_arquivo_do_zip(
    caminho_zip: Path, nome_arquivo: str, cnpjs_filtro: set[str] | None = None, limite_amostra: int = 20
) -> dict:
    with zipfile.ZipFile(caminho_zip) as zf:
        if nome_arquivo not in zf.namelist():
            raise CvmFreClientError(f"Arquivo {nome_arquivo} não encontrado no zip. Use listar_arquivos_fre primeiro.")
        # Arquivos da CVM são tradicionalmente em Latin-1 (ISO-8859-1), não UTF-8.
        conteudo = zf.read(nome_arquivo).decode("latin-1")
    return inspecionar_csv_de_texto(conteudo, cnpjs_filtro, limite_amostra)


async def listar_arquivos_fre(ano: int) -> list[str]:
    caminho_zip = await _baixar_zip_ano(ano)
    return listar_arquivos_do_zip(caminho_zip)


async def inspecionar_arquivo_fre(
    ano: int, nome_arquivo: str, cnpjs_filtro: set[str] | None = None, limite_amostra: int = 20
) -> dict:
    caminho_zip = await _baixar_zip_ano(ano)
    return inspecionar_arquivo_do_zip(caminho_zip, nome_arquivo, cnpjs_filtro, limite_amostra)
