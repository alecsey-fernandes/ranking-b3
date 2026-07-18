"""
Utilitário genérico para inspecionar um CSV desconhecido (colunas ainda
não confirmadas) — usado pelos módulos de diagnóstico (FCA, DFP) para
expor o cabeçalho real e uma amostra de linhas para conferência humana,
antes de qualquer campo virar parte de um parser de produção.

Ver `app/data_sources/cvm_fca_client.py` para o raciocínio completo por
trás dessa cautela (um nome de coluna errado não dá erro, silenciosamente
traz um número errado).
"""

from __future__ import annotations

import csv
import io

CANDIDATOS_COLUNA_CNPJ = ("CNPJ_Companhia", "CNPJ_CIA", "CNPJ")


def inspecionar_csv_de_texto(
    conteudo_csv: str,
    cnpjs_filtro: set[str] | None,
    limite_amostra: int = 20,
    delimitador: str = ";",
) -> dict:
    """
    Lê um CSV (texto já em memória) e devolve o cabeçalho (nomes reais
    das colunas) + linhas de amostra, filtradas pelos CNPJs pedidos
    quando informado. Não interpreta o significado de nenhuma coluna.

    Se o filtro por CNPJ não encontrar nenhuma linha (nome de coluna de
    CNPJ diferente dos candidatos testados), cai para uma amostra
    genérica em vez de devolver um resultado vazio sem explicação.
    """
    leitor = csv.DictReader(io.StringIO(conteudo_csv), delimiter=delimitador)
    colunas = leitor.fieldnames or []
    todas_linhas = list(leitor)

    amostra_filtrada = []
    if cnpjs_filtro:
        for linha in todas_linhas:
            valor_cnpj_bruto = next(
                (linha[c] for c in CANDIDATOS_COLUNA_CNPJ if c in linha and linha[c]), ""
            )
            cnpj_linha = "".join(c for c in valor_cnpj_bruto if c.isdigit())
            if cnpj_linha in cnpjs_filtro:
                amostra_filtrada.append(linha)
                if len(amostra_filtrada) >= limite_amostra:
                    break

    if cnpjs_filtro and not amostra_filtrada:
        return {
            "colunas_encontradas": colunas,
            "aviso": (
                f"Filtro por CNPJ não encontrou nenhuma linha usando os nomes de coluna "
                f"testados ({', '.join(CANDIDATOS_COLUNA_CNPJ)}) — o nome real da coluna de "
                f"CNPJ neste arquivo provavelmente é outro. Mostrando amostra genérica (sem "
                f"filtro) para inspeção; confira 'colunas_encontradas' para o nome correto."
            ),
            "total_linhas_amostra": min(limite_amostra, len(todas_linhas)),
            "amostra": todas_linhas[:limite_amostra],
        }

    amostra = amostra_filtrada if cnpjs_filtro else todas_linhas[:limite_amostra]
    return {"colunas_encontradas": colunas, "total_linhas_amostra": len(amostra), "amostra": amostra}
