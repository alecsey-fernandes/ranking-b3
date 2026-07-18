"""
Deriva LPA (lucro por ação) e VPA (valor patrimonial por ação) a partir
dos componentes brutos coletados de fontes separadas (lucro líquido e
patrimônio líquido via CVM/DFP, quantidade de ações via CVM/composição
de capital) — usado quando esses indicadores não vêm prontos de uma
fonte como a Brapi.

Regra central: NUNCA calcula quando a quantidade de ações está marcada
como suspeita (ver LIMIAR_MINIMO_PLAUSIVEL_ACOES em cvm_client.py — caso
real confirmado: Vale S.A. no DFP 2024 com ações ~1000x menores que o
real). Um LPA/VPA calculado sobre uma quantidade de ações errada não é
"aproximado" — é uma fração com denominador errado, potencialmente por
uma ordem de grandeza inteira, o que inverteria completamente a
conclusão de uma estratégia como Graham. Preferimos não calcular a
calcular errado silenciosamente.
"""

from __future__ import annotations

from typing import Optional


def derivar_lpa_vpa(
    lucro_liquido: Optional[float],
    patrimonio_liquido: Optional[float],
    acoes_em_circulacao: Optional[float],
    acoes_dado_suspeito: Optional[bool],
) -> tuple[Optional[float], Optional[float]]:
    """Retorna (lpa, vpa). Qualquer componente ausente, ações <= 0, ou
    ações marcadas como suspeitas resulta em None para o respectivo
    indicador — nunca um valor calculado sobre dado não confiável."""
    if not acoes_em_circulacao or acoes_em_circulacao <= 0 or acoes_dado_suspeito:
        return None, None

    lpa = (lucro_liquido / acoes_em_circulacao) if lucro_liquido is not None else None
    vpa = (patrimonio_liquido / acoes_em_circulacao) if patrimonio_liquido is not None else None
    return lpa, vpa
