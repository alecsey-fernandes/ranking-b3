"""
Schemas do corpo da requisição do endpoint POST /backtest/carteira.

Único lugar da plataforma que recebe um corpo JSON estruturado (os demais
endpoints usam query params) — uma carteira é uma lista de itens, o que
não se presta bem a query params repetidos. Por isso, só aqui, usamos
Pydantic (já vem como dependência transitiva do FastAPI) em vez do padrão
dataclass usado no restante do projeto.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, Field


class ItemCarteiraRequest(BaseModel):
    ticker: str = Field(..., description="Código de negociação do ativo, ex: PETR4")
    quantidade: float = Field(..., gt=0, description="Quantidade de ações desse ticker na carteira")


class CarteiraBacktestRequest(BaseModel):
    data_inicio: date = Field(
        ..., description="Data em que a carteira teria sido montada (AAAA-MM-DD)"
    )
    itens: list[ItemCarteiraRequest] = Field(
        ..., min_length=1, description="Composição da carteira: tickers e quantidades"
    )
    nome_carteira: str | None = Field(
        default=None,
        description="Nome/descrição opcional da carteira, só para identificação na resposta (não é persistido nesta etapa)",
    )
