"""
Cliente para os arquivos de Cotações Históricas (COTAHIST) da B3 —
fonte gratuita, oficial, sem limite de requisições e sem necessidade de
token, resolvendo o preço de mercado que a CVM não fornece.

Formato: arquivo texto de largura fixa (não CSV), um arquivo por ano,
contendo o pregão de TODOS os papéis negociados na B3 naquele ano. Layout
oficial documentado em:
https://www.b3.com.br/data/files/C8/F3/08/B4/297BE410F816C9E492D828A8/SeriesHistoricas_Layout.pdf
(revisão de 13/04/2017 — as posições de coluna são estáveis há anos).

URL do arquivo anual:
  http://bvmf.bmfbovespa.com.br/InstDados/SerHist/COTAHIST_A{ANO}.ZIP

⚠️ IMPORTANTE — não testado contra o arquivo real: este ambiente de
desenvolvimento não tem acesso à internet. O parsing abaixo segue o
layout oficial documentado (posições de coluna exatas, tipo de registro,
formato numérico), validado aqui com linhas sintéticas que reproduzem
esse layout (ver tests/test_b3_cotahist_client.py). Antes de confiar nos
preços em produção, confira o preço de fechamento de 1-2 tickers contra
o valor publicado no site da B3 ou em qualquer corretora.
"""

from __future__ import annotations

import io
import logging
import zipfile
from datetime import date
from pathlib import Path

logger = logging.getLogger(__name__)

BASE_URL = "http://bvmf.bmfbovespa.com.br/InstDados/SerHist"
CACHE_DIR = Path("cache_b3")

# Mercado à vista (ações negociadas normalmente, não fracionário/opções/termo)
TPMERC_VISTA = "010"
# Lote padrão (exclui ações sancionadas, em recuperação judicial, etc. —
# ver tabela de CODBDI no layout oficial)
CODBDI_LOTE_PADRAO = "02"


class B3ClientError(Exception):
    """Erro ao baixar ou interpretar arquivos de cotações históricas da B3."""


def _url_cotahist_ano(ano: int) -> str:
    return f"{BASE_URL}/COTAHIST_A{ano}.ZIP"


async def _baixar_zip_ano(ano: int) -> bytes:
    import httpx  # import local: mantém este módulo testável (parsing puro) sem exigir httpx instalado

    CACHE_DIR.mkdir(exist_ok=True)
    caminho_cache = CACHE_DIR / f"COTAHIST_A{ano}.ZIP"

    if caminho_cache.exists():
        logger.info("Usando cache local para COTAHIST %d", ano)
        return caminho_cache.read_bytes()

    url = _url_cotahist_ano(ano)
    logger.info("Baixando COTAHIST %d da B3: %s", ano, url)
    async with httpx.AsyncClient(timeout=120.0) as client:
        try:
            resp = await client.get(url, follow_redirects=True)
        except httpx.HTTPError as exc:
            raise B3ClientError(f"Falha de rede ao baixar COTAHIST {ano}: {exc}") from exc

    if resp.status_code != 200:
        raise B3ClientError(f"B3 retornou status {resp.status_code} para COTAHIST {ano} (url: {url})")

    caminho_cache.write_bytes(resp.content)
    return resp.content


def _extrair_txt_do_zip(conteudo_zip: bytes, ano: int) -> str:
    """Extrai o texto do arquivo de cotações de dentro do zip anual.
    Tenta o nome de arquivo convencional primeiro; se não bater (a B3 já
    variou maiúsculas/formato em versões antigas), usa o primeiro .TXT
    encontrado no zip como fallback, em vez de falhar."""
    nome_esperado = f"COTAHIST_A{ano}.TXT"
    with zipfile.ZipFile(io.BytesIO(conteudo_zip)) as zf:
        nomes = zf.namelist()
        nome_real = nome_esperado if nome_esperado in nomes else next(
            (n for n in nomes if n.upper().endswith(".TXT")), None
        )
        if nome_real is None:
            raise B3ClientError(f"Nenhum arquivo .TXT encontrado no zip de COTAHIST {ano}. Conteúdo: {nomes}")
        # Arquivos da B3 são tradicionalmente em Latin-1 (ISO-8859-1), não UTF-8.
        return zf.read(nome_real).decode("latin-1")


def _parsear_valor_v99(campo: str) -> float:
    """Campos de preço no COTAHIST são numéricos de largura fixa com 2
    casas decimais IMPLÍCITAS (sem ponto/vírgula no arquivo) — ex: o
    texto '0000002150' representa 21.50."""
    return int(campo) / 100.0


def parsear_cotacoes(conteudo_txt: str, tickers_desejados: set[str]) -> dict[tuple[str, date], float]:
    """
    Faz o parsing de um arquivo COTAHIST_A{ano}.TXT e retorna
    {(ticker, data_pregao): preco_ultimo_negocio} apenas para os tickers
    pedidos, no mercado à vista (TPMERC='010') e lote padrão (CODBDI='02')
    — evita pegar cotação de opções, termo, fracionário ou papéis com
    situação especial (recuperação judicial etc.) por engano.
    """
    resultado: dict[tuple[str, date], float] = {}

    for linha in conteudo_txt.splitlines():
        if len(linha) < 245 or linha[0:2] != "01":
            continue  # ignora header (00), trailer (99) e linhas malformadas

        codbdi = linha[10:12]
        codneg = linha[12:24].strip()
        tpmerc = linha[24:27]

        if tpmerc != TPMERC_VISTA or codbdi != CODBDI_LOTE_PADRAO:
            continue
        if codneg not in tickers_desejados:
            continue

        try:
            data_pregao_str = linha[2:10]  # AAAAMMDD
            data_pregao = date(int(data_pregao_str[0:4]), int(data_pregao_str[4:6]), int(data_pregao_str[6:8]))
            preco_ultimo = _parsear_valor_v99(linha[108:121])
        except (ValueError, IndexError) as exc:
            logger.warning("Linha ignorada por erro de parsing (%s): %r", exc, linha[:50])
            continue

        resultado[(codneg, data_pregao)] = preco_ultimo

    return resultado


async def buscar_cotacoes_ano(ano: int, tickers: list[str]) -> dict[tuple[str, date], float]:
    """Baixa (ou usa cache) o arquivo anual e retorna as cotações de
    fechamento de cada (ticker, data) do ano inteiro para os tickers
    pedidos — o arquivo cobre TODOS os pregões do ano, então dá pra
    escolher a data mais recente ou uma data específica a partir do
    resultado, sem baixar de novo."""
    conteudo_zip = await _baixar_zip_ano(ano)
    conteudo_txt = _extrair_txt_do_zip(conteudo_zip, ano)
    return parsear_cotacoes(conteudo_txt, set(tickers))


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
