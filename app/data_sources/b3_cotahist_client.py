"""
Cliente para os arquivos de Cotações Históricas (COTAHIST) da B3 —
fonte gratuita, oficial, sem limite de requisições e sem necessidade de
token, resolvendo o preço de mercado que a CVM não fornece.

Formato: arquivo texto de largura fixa (não CSV), um arquivo por ano,
contendo o pregão de TODOS os papéis negociados na B3 naquele ano (ações,
opções, termo, etc. — não só o que nos interessa). Layout oficial:
https://www.b3.com.br/data/files/C8/F3/08/B4/297BE410F816C9E492D828A8/SeriesHistoricas_Layout.pdf
(revisão de 13/04/2017 — posições de coluna estáveis há anos).

URL do arquivo anual:
  https://bvmf.bmfbovespa.com.br/InstDados/SerHist/COTAHIST_A{ANO}.ZIP

⚠️ Memória: o arquivo anual descompactado pode passar de centenas de MB
(cobre TODOS os papéis do ano inteiro). Por isso todo o caminho de
produção (download -> disco, leitura -> streaming linha a linha) evita
carregar o conteúdo inteiro em memória de uma vez — importante em
ambientes de hospedagem com RAM limitada (o sintoma de não fazer isso é
a aplicação reiniciar sozinha no meio da requisição, por falta de
memória).

⚠️ Não testado contra o arquivo real: este ambiente de desenvolvimento
não tem acesso à internet. O parsing segue o layout oficial documentado,
validado com linhas sintéticas (ver tests/test_b3_cotahist_client.py).
Antes de confiar nos preços em produção, confira 1-2 tickers contra o
valor publicado no site da B3 ou em qualquer corretora.
"""

from __future__ import annotations

import asyncio
import io
import logging
import time
import zipfile
from datetime import date
from pathlib import Path
from typing import Iterable, Iterator

logger = logging.getLogger(__name__)

BASE_URL = "https://bvmf.bmfbovespa.com.br/InstDados/SerHist"
CACHE_DIR = Path("cache_b3")

# Alguns sites da B3 ficam atrás de proteção que bloqueia requisições sem
# um User-Agent que pareça vir de um navegador de verdade (retornam erro
# 520 da Cloudflare em vez do arquivo). Usamos um User-Agent de navegador
# comum para evitar esse bloqueio.
HEADERS_NAVEGADOR = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
    )
}

# Mercado à vista (ações negociadas normalmente, não fracionário/opções/termo)
TPMERC_VISTA = "010"
# Lote padrão (exclui ações sancionadas, em recuperação judicial, etc. —
# ver tabela de CODBDI no layout oficial)
CODBDI_LOTE_PADRAO = "02"


class B3ClientError(Exception):
    """Erro ao baixar ou interpretar arquivos de cotações históricas da B3."""


def _url_cotahist_ano(ano: int) -> str:
    return f"{BASE_URL}/COTAHIST_A{ano}.ZIP"


async def _baixar_zip_ano(ano: int) -> Path:
    """
    Baixa o zip anual da COTAHIST diretamente para um arquivo em disco,
    em streaming (grava em pedaços conforme chegam, nunca acumula a
    resposta inteira em memória) e devolve o caminho do arquivo cacheado.
    Chamadas seguintes para o mesmo ano reusam o arquivo já baixado.
    """
    import httpx  # import local: mantém este módulo testável (parsing puro) sem exigir httpx instalado

    CACHE_DIR.mkdir(exist_ok=True)
    caminho_cache = CACHE_DIR / f"COTAHIST_A{ano}.ZIP"

    if caminho_cache.exists():
        logger.info("Usando cache local para COTAHIST %d", ano)
        return caminho_cache

    url = _url_cotahist_ano(ano)
    logger.info("Baixando COTAHIST %d da B3 (streaming para disco): %s", ano, url)
    caminho_parcial = CACHE_DIR / f"COTAHIST_A{ano}.ZIP.partial"

    try:
        async with httpx.AsyncClient(timeout=180.0, headers=HEADERS_NAVEGADOR) as client:
            async with client.stream("GET", url, follow_redirects=True) as resp:
                if resp.status_code != 200:
                    raise B3ClientError(
                        f"B3 retornou status {resp.status_code} para COTAHIST {ano} (url: {url})"
                    )
                with open(caminho_parcial, "wb") as f:
                    async for chunk in resp.aiter_bytes(chunk_size=1024 * 1024):
                        f.write(chunk)
    except httpx.HTTPError as exc:
        if caminho_parcial.exists():
            caminho_parcial.unlink()
        raise B3ClientError(f"Falha de rede ao baixar COTAHIST {ano}: {exc}") from exc

    caminho_parcial.rename(caminho_cache)
    return caminho_cache


def _nome_txt_no_zip(zf: zipfile.ZipFile, ano: int) -> str:
    """Encontra o nome do arquivo .TXT dentro do zip (tenta o nome
    convencional primeiro; a B3 já variou formato em versões antigas)."""
    nome_esperado = f"COTAHIST_A{ano}.TXT"
    nomes = zf.namelist()
    nome_real = nome_esperado if nome_esperado in nomes else next(
        (n for n in nomes if n.upper().endswith(".TXT")), None
    )
    if nome_real is None:
        raise B3ClientError(f"Nenhum arquivo .TXT encontrado no zip de COTAHIST {ano}. Conteúdo: {nomes}")
    return nome_real


def _iterar_linhas_do_zip(caminho_zip: Path, ano: int) -> Iterator[str]:
    """
    Itera as linhas do arquivo de cotações de dentro do zip UMA A UMA,
    lendo direto do arquivo em disco — nunca carrega o texto inteiro nem
    uma lista completa de linhas na memória.
    """
    with zipfile.ZipFile(caminho_zip) as zf:
        nome_real = _nome_txt_no_zip(zf, ano)
        # Arquivos da B3 são tradicionalmente em Latin-1 (ISO-8859-1), não UTF-8.
        with zf.open(nome_real) as bruto, io.TextIOWrapper(bruto, encoding="latin-1") as texto:
            for linha in texto:
                yield linha.rstrip("\n").rstrip("\r")


def _parsear_valor_v99(campo: str) -> float:
    """Campos de preço no COTAHIST são numéricos de largura fixa com 2
    casas decimais IMPLÍCITAS (sem ponto/vírgula no arquivo) — ex: o
    texto '0000002150' representa 21.50."""
    return int(campo) / 100.0


def _processar_linha(linha: str, tickers_desejados: set[str], resultado: dict[tuple[str, date], float]) -> None:
    """Processa uma única linha do COTAHIST, adicionando a `resultado`
    quando for uma cotação de mercado à vista/lote padrão de um dos
    tickers pedidos. Compartilhada entre o parsing em memória (usado nos
    testes) e o parsing em streaming (usado em produção)."""
    if len(linha) < 245 or linha[0:2] != "01":
        return  # ignora header (00), trailer (99) e linhas malformadas

    codbdi = linha[10:12]
    codneg = linha[12:24].strip()
    tpmerc = linha[24:27]

    if tpmerc != TPMERC_VISTA or codbdi != CODBDI_LOTE_PADRAO:
        return
    if codneg not in tickers_desejados:
        return

    try:
        data_pregao_str = linha[2:10]  # AAAAMMDD
        data_pregao = date(int(data_pregao_str[0:4]), int(data_pregao_str[4:6]), int(data_pregao_str[6:8]))
        preco_ultimo = _parsear_valor_v99(linha[108:121])
    except (ValueError, IndexError) as exc:
        logger.warning("Linha ignorada por erro de parsing (%s): %r", exc, linha[:50])
        return

    resultado[(codneg, data_pregao)] = preco_ultimo


def parsear_cotacoes(conteudo_txt: str, tickers_desejados: set[str]) -> dict[tuple[str, date], float]:
    """
    Faz o parsing de um texto de COTAHIST já em memória — usado nos
    testes, com um texto sintético pequeno. Para o arquivo real (que
    pode ser muito grande), use `buscar_cotacoes_ano`, que processa em
    streaming em vez de montar esse texto inteiro em memória.
    """
    resultado: dict[tuple[str, date], float] = {}
    for linha in conteudo_txt.splitlines():
        _processar_linha(linha, tickers_desejados, resultado)
    return resultado


def parsear_cotacoes_streaming(
    linhas: Iterable[str], tickers_desejados: set[str]
) -> dict[tuple[str, date], float]:
    """Mesma lógica de `parsear_cotacoes`, mas recebendo qualquer iterável
    de linhas (ex: um gerador que lê o arquivo aos poucos) em vez de um
    texto já inteiramente carregado em memória."""
    resultado: dict[tuple[str, date], float] = {}
    for linha in linhas:
        _processar_linha(linha, tickers_desejados, resultado)
    return resultado


async def buscar_cotacoes_ano(ano: int, tickers: list[str]) -> dict[tuple[str, date], float]:
    """Baixa (ou usa cache) o arquivo anual e retorna as cotações de
    fechamento de cada (ticker, data) do ano inteiro para os tickers
    pedidos. O parsing (linha a linha, de TODOS os papéis do ano — não só
    os pedidos) é bem mais pesado em CPU do que em rede: roda em thread
    separada (`asyncio.to_thread`) para não travar o event loop enquanto
    processa, permitindo que outras requisições (ex: consulta de status
    de um job de backtest em andamento) continuem sendo atendidas."""
    caminho_zip = await _baixar_zip_ano(ano)
    tickers_desejados = set(tickers)

    def _parsear() -> dict[tuple[str, date], float]:
        linhas = _iterar_linhas_do_zip(caminho_zip, ano)
        return parsear_cotacoes_streaming(linhas, tickers_desejados)

    inicio = time.monotonic()
    resultado = await asyncio.to_thread(_parsear)
    logger.info(
        "COTAHIST %d processado em %.1fs (%d tickers pedidos, %d cotações encontradas)",
        ano, time.monotonic() - inicio, len(tickers_desejados), len(resultado),
    )
    return resultado


def preco_mais_recente_por_ticker(
    cotacoes: dict[tuple[str, date], float]
) -> dict[str, tuple[date, float]]:
    """A partir do resultado de `buscar_cotacoes_ano` (ou da combinação de
    vários anos), reduz para {ticker: (data_mais_recente, preco)} —
    conveniente para popular `Indicadores.preco_atual` com a última
    cotação disponível."""
    mais_recente: dict[str, tuple[date, float]] = {}
    for (ticker, data_pregao), preco in cotacoes.items():
        atual = mais_recente.get(ticker)
        if atual is None or data_pregao > atual[0]:
            mais_recente[ticker] = (data_pregao, preco)
    return mais_recente
