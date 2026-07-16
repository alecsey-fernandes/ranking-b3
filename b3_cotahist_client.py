"""Configurações da plataforma, incluindo pesos padrão das estratégias."""

from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass
class PesosEstrategias:
    """
    Pesos percentuais de cada estratégia no ranking final ponderado.
    Devem somar 100. Ajustáveis por perfil de investidor (ver
    app/ranking/perfis.py quando essa camada existir).
    """

    magic_formula: float = 25.0
    piotroski: float = 20.0
    graham: float = 20.0
    bazin: float = 15.0
    buffett_like: float = 15.0
    peg_lynch: float = 5.0

    def validar_soma(self) -> None:
        total = (
            self.magic_formula
            + self.piotroski
            + self.graham
            + self.bazin
            + self.buffett_like
            + self.peg_lynch
        )
        if abs(total - 100.0) > 0.01:
            raise ValueError(f"Pesos das estratégias devem somar 100, soma atual: {total}")

    def model_dump(self) -> dict:
        """Compatibilidade com serialização usada no endpoint /ranking."""
        return {
            "magic_formula": self.magic_formula,
            "piotroski": self.piotroski,
            "graham": self.graham,
            "bazin": self.bazin,
            "buffett_like": self.buffett_like,
            "peg_lynch": self.peg_lynch,
        }


@dataclass
class Settings:
    brapi_base_url: str = os.environ.get("BRAPI_BASE_URL", "https://brapi.dev/api")
    brapi_token: str = os.environ.get("BRAPI_TOKEN", "")
    min_empresas_para_percentil: int = 5  # abaixo disso, percentil não é confiável


settings = Settings()
pesos_padrao = PesosEstrategias()
