"""
Mapeamento entre ticker (código de negociação na B3) e CNPJ da companhia.

Por que isso existe: a CVM identifica empresas por CNPJ nos arquivos de
dados abertos (DFP/ITR), não pelo código de negociação (ticker) usado na
B3 — uma mesma empresa pode ter vários tickers (ex: units, ON e PN) para
um único CNPJ. Não há um endpoint público único e simples que faça esse
de-para automaticamente; a fonte mais confiável é o Formulário Cadastral
(FCA) da própria CVM ou a listagem de empresas da B3.

⚠️ ATENÇÃO: os CNPJs abaixo foram preenchidos a partir de conhecimento
geral, MAS este ambiente de desenvolvimento não tem acesso à internet
para validar cada um contra a fonte oficial (CVM/B3) antes da entrega.
**Confira cada CNPJ no Formulário Cadastral da CVM (dados.cvm.gov.br,
dataset "Cias Abertas: Documentos: Formulário Cadastral") ou na página
"Sobre a Empresa" de cada ação em b3.com.br antes de usar em produção.**
Um CNPJ errado silenciosamente traz o histórico de lucro de OUTRA
empresa — o tipo de erro que não dá erro em tempo de execução.
"""

from __future__ import annotations

# ticker -> CNPJ (formato: apenas dígitos, sem pontuação)
TICKER_PARA_CNPJ: dict[str, str] = {
    "PETR4": "33000167000101",  # Petróleo Brasileiro S.A. - Petrobras
    "PETR3": "33000167000101",
    "VALE3": "33592510000154",  # Vale S.A.
    "ITUB4": "60872504000123",  # Itaú Unibanco Holding S.A.
    "BBDC4": "60746948000112",  # Banco Bradesco S.A.
    "ABEV3": "07526557000100",  # Ambev S.A.
    "WEGE3": "84429695000111",  # WEG S.A.
    "BBAS3": "00000000000191",  # Banco do Brasil S.A.
    "B3SA3": "09346601000125",  # B3 S.A. - Brasil, Bolsa, Balcão
    "RENT3": "16670085000155",  # Localiza Rent a Car S.A.
    "EGIE3": "02474103000119",  # Engie Brasil Energia S.A.
}


def cnpj_para_ticker(cnpj_normalizado: str) -> str | None:
    """Busca reversa: CNPJ (apenas dígitos) -> ticker. Retorna None se não mapeado."""
    for ticker, cnpj in TICKER_PARA_CNPJ.items():
        if cnpj == cnpj_normalizado:
            return ticker
    return None


def normalizar_cnpj(cnpj: str) -> str:
    """Remove pontuação de um CNPJ (ex: '33.000.167/0001-01' -> '33000167000101')."""
    return "".join(c for c in cnpj if c.isdigit())
