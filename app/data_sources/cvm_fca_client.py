"""
Cliente para a seção "Valores Mobiliários" do Formulário Cadastral (FCA)
da CVM — a fonte candidata para quantidade de ações emitidas por
empresa, necessária para calcular LPA/VPA (Graham) e dividendo por ação
(Bazin) a partir dos totais que já temos (lucro líquido via CVM/DFP,
preço via B3).

⚠️ DIAGNÓSTICO, NÃO PRODUÇÃO para a maior parte deste módulo: diferente
de cvm_client.py e b3_cotahist_client.py, a maioria das funções aqui
ainda NÃO interpreta semanticamente os dados — não temos confirmação do
nome exato de todas as colunas desse arquivo (o site da CVM bloqueou
acesso automatizado ao dicionário de dados a partir deste ambiente de
desenvolvimento). Em vez de adivinhar nomes de coluna (risco real: um
nome errado não dá erro, silenciosamente traz um número errado que
alimentaria diretamente o cálculo de LPA/VPA), a maior parte deste
módulo baixa o arquivo real e expõe o cabeçalho + algumas linhas de
amostra para inspeção humana — via `inspecionar_valor_mobiliario` e o
endpoint `GET /diagnostico/cvm/fca-valor-mobiliario`.

✅ EXCEÇÃO — resolução de CNPJ (produção): `resolver_cnpjs_ausentes` e
`parsear_ticker_cnpj_por_codigo_negociacao`, ao final do módulo, JÁ são
produção. Usam as colunas `Codigo_Negociacao` e `CNPJ_Companhia`, que
foram confirmadas contra o cadastro oficial da CVM em produção ao
validar os 10 CNPJs hardcoded em `app/data_sources/ticker_mapping.py`
(ver docstring lá) — não é um palpite novo. Substituem a necessidade de
adicionar cada ticker manualmente naquele dicionário.

Depois de confirmar os nomes reais das colunas restantes (rodando o
diagnóstico em produção, onde há acesso à internet), o parser definitivo
de extração de quantidade de ações deve ser adicionado aqui, seguindo o
mesmo padrão de `cvm_client.py` (função pura, testável, com testes
usando o formato confirmado).

URL do arquivo anual:
  https://dados.cvm.gov.br/dados/CIA_ABERTA/DOC/FCA/DADOS/fca_cia_aberta_{ANO}.zip
Arquivo esperado dentro do zip:
  fca_cia_aberta_valor_mobiliario_{ANO}.csv
"""

from __future__ import annotations

import csv
import io
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


# ---------------------------------------------------------------------------
# De-para ticker -> CNPJ (produção), substituindo a necessidade de manter
# `TICKER_PARA_CNPJ` (app/data_sources/ticker_mapping.py) como lista fixa
# atualizada manualmente um ticker por vez.
#
# ✅ Colunas usadas aqui (`Codigo_Negociacao`, `CNPJ_Companhia`) NÃO são um
# palpite: são as mesmas já confirmadas contra o cadastro oficial da CVM ao
# validar os 10 CNPJs hardcoded em ticker_mapping.py (ver docstring lá —
# "checados contra a coluna CNPJ_Companhia do FCA... que também confirma o
# Codigo_Negociacao"). Isso foi feito com acesso à produção; este ambiente
# de desenvolvimento não conseguiu baixar o arquivo real para reconfirmar
# (bloqueio de acesso automatizado pelo site da CVM). Por isso a função
# abaixo tem um fallback defensivo: se essas colunas não existirem no
# arquivo do ano pedido (schema mudou), levanta erro claro em vez de
# devolver um mapeamento vazio ou usar uma coluna errada silenciosamente.
# ---------------------------------------------------------------------------

COLUNA_CODIGO_NEGOCIACAO = "Codigo_Negociacao"
COLUNA_CNPJ_COMPANHIA = "CNPJ_Companhia"


def parsear_ticker_cnpj_por_codigo_negociacao(conteudo_csv: str) -> dict[str, str]:
    """
    Faz o parsing de um CSV de fca_cia_aberta_valor_mobiliario e retorna
    {ticker (maiúsculo): cnpj (só dígitos)}.

    Uma empresa normalmente tem várias linhas (ON, PN, units...), cada uma
    com seu próprio `Codigo_Negociacao` — todas viram entradas separadas
    no dict retornado, todas apontando para o mesmo CNPJ da companhia.
    Linhas sem código de negociação (valor mobiliário não listado em
    bolsa, ex: debênture) são ignoradas.
    """
    leitor = csv.DictReader(io.StringIO(conteudo_csv), delimiter=";")

    primeira_linha = next(iter(leitor), None)
    if primeira_linha is not None and (
        COLUNA_CODIGO_NEGOCIACAO not in primeira_linha or COLUNA_CNPJ_COMPANHIA not in primeira_linha
    ):
        raise CvmFcaClientError(
            f"Colunas esperadas ({COLUNA_CODIGO_NEGOCIACAO!r}, {COLUNA_CNPJ_COMPANHIA!r}) não encontradas "
            f"no CSV do FCA. Colunas presentes: {list(primeira_linha.keys())}. "
            "O schema do arquivo pode ter mudado — rode GET /diagnostico/cvm/fca-valor-mobiliario "
            "para conferir o cabeçalho real antes de ajustar estas constantes."
        )

    resultado: dict[str, str] = {}

    def _processar(linha: dict) -> None:
        codigo = (linha.get(COLUNA_CODIGO_NEGOCIACAO) or "").strip().upper()
        cnpj = "".join(c for c in (linha.get(COLUNA_CNPJ_COMPANHIA) or "") if c.isdigit())
        if codigo and cnpj:
            resultado[codigo] = cnpj

    if primeira_linha is not None:
        _processar(primeira_linha)
    for linha in leitor:
        _processar(linha)

    return resultado


async def buscar_mapa_ticker_cnpj_por_ano(ano: int) -> dict[str, str]:
    """Baixa (com cache) o FCA do ano e devolve {ticker: cnpj} de todos os
    valores mobiliários com código de negociação naquele ano."""
    caminho_zip = await _baixar_zip_ano(ano)
    with zipfile.ZipFile(caminho_zip) as zf:
        nome_csv = _nome_csv_valor_mobiliario(zf, ano)
        conteudo_csv = zf.read(nome_csv).decode("latin-1")

    return parsear_ticker_cnpj_por_codigo_negociacao(conteudo_csv)


async def resolver_cnpjs_ausentes(
    tickers_sem_cnpj: list[str], anos_candidatos: list[int]
) -> tuple[dict[str, str], list[str]]:
    """
    Tenta resolver o CNPJ dos tickers informados via FCA, tentando os anos
    de `anos_candidatos` do mais recente para o mais antigo (o FCA de um
    ano só reflete o registro de negociação vigente naquele ano; um ticker
    pode não constar em anos muito antigos ou muito recentes se a empresa
    passou a negociar/deixou de negociar naquele meio-tempo). Um ticker já
    resolvido num ano mais recente não é sobrescrito por um ano mais
    antigo (prioriza o cadastro mais atual).

    Retorna (cnpj_resolvido_por_ticker, ainda_sem_cnpj). Uma falha ao
    baixar/parsear o FCA de um ano específico (ex: rede fora, ano ainda
    não publicado) é registrada em log e simplesmente pulada — não aborta
    a resolução dos demais anos/tickers.
    """
    pendentes = set(tickers_sem_cnpj)
    resolvidos: dict[str, str] = {}

    for ano in sorted(set(anos_candidatos), reverse=True):
        if not pendentes:
            break
        try:
            mapa_ano = await buscar_mapa_ticker_cnpj_por_ano(ano)
        except CvmFcaClientError as exc:
            logger.warning("Falha ao resolver CNPJs via FCA %d: %s", ano, exc)
            continue

        for ticker in list(pendentes):
            cnpj = mapa_ano.get(ticker)
            if cnpj:
                resolvidos[ticker] = cnpj
                pendentes.discard(ticker)

    return resolvidos, sorted(pendentes)
