"""
Interface base para estratégias fundamentalistas.

Cada estratégia nova (Piotroski, Bazin, Buffett-like, PEG/Lynch...) só
precisa herdar de `Estrategia` e implementar `calcular` + `direcao`.
O restante do pipeline (normalização em percentil, ponderação, ranking
final) funciona automaticamente para qualquer estratégia que siga essa
interface — não é preciso mexer no agregador ao adicionar uma nova.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from enum import Enum

from app.models import Indicadores, ResultadoEstrategia


class Direcao(str, Enum):
    """Define se, na escala nativa da estratégia, maior é melhor ou menor é melhor.
    Usado pelo normalizador para converter score_bruto em percentil corretamente."""

    MAIOR_MELHOR = "maior_melhor"
    MENOR_MELHOR = "menor_melhor"


class Estrategia(ABC):
    nome: str
    direcao: Direcao

    @abstractmethod
    def calcular(self, indicadores: Indicadores) -> ResultadoEstrategia:
        """Calcula o score bruto da estratégia para uma empresa.

        Deve retornar `elegivel=False` (em vez de lançar exceção) quando os
        dados necessários estiverem ausentes — empresas não elegíveis são
        excluídas apenas do percentil dessa estratégia, sem derrubar o
        pipeline nem zerar as outras estratégias para aquela empresa.
        """
        raise NotImplementedError
