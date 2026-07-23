"""
Schemas do corpo da requisição do endpoint POST /backtest/carteira.

Único lugar da plataforma que recebe um corpo JSON estruturado (os demais
endpoints usam query params) — uma carteira é uma lista de itens, o que
não se presta bem a query params repetidos. Por isso, só aqui, usamos
Pydantic (já vem como dependência transitiva do FastAPI) em vez do padrão
dataclass usado no restante do projeto.

⚠️ Diferença de UI no Swagger (`/docs`) importante para quem só usou os
outros endpoints até agora: como aqui o corpo é um JSON estruturado (não
query params), o Swagger mostra um ÚNICO campo de texto com um JSON de
exemplo pronto para editar — não uma lista de campos separados como
`ticker`, `tipo texto` / `quantidade`, `tipo número` um por um. Os campos
`ticker`, `quantidade`, `nome_carteira` etc. continuam existindo (são a
estrutura desse JSON) — eles só aparecem DENTRO da caixa de texto, não
como inputs individuais. É esperado que o valor de exemplo mostre algo
genérico como "string"/`1` até você editar; os `json_schema_extra`
abaixo trocam esse exemplo por um mais realista.
"""

from __future__ import annotations

from datetime import date

from pydantic import BaseModel, ConfigDict, Field


class EventoSocietarioRequest(BaseModel):
    """
    Desdobramento ou grupamento (split/reverse split) ocorrido no
    período do backtest para um ticker específico.

    ⚠️ Informado manualmente pelo usuário nesta etapa: não existe ainda
    uma fonte gratuita automática confirmada para eventos societários na
    plataforma (mesma cautela já aplicada a outras fontes CVM ainda não
    confirmadas — ver diagnósticos em app/data_sources/). O COTAHIST
    usado para os preços não indica quando um desdobramento aconteceu,
    então sem essa informação o backtest ficaria distorcido para
    tickers que passaram por um evento desses no período.
    """

    model_config = ConfigDict(json_schema_extra={"example": {"data": "2024-06-03", "fator": 2.0}})

    data: date = Field(..., description="Data em que o desdobramento/grupamento passou a valer")
    fator: float = Field(
        ...,
        gt=0,
        description=(
            "Fator multiplicativo sobre a quantidade de ações. Ex: desdobramento 1:2 "
            "(cada ação virou 2) = 2.0; grupamento 10:1 (cada 10 ações viraram 1) = 0.1. "
            "Se houve mais de um evento no período, informe um por data — o backtest "
            "aplica todos em ordem cronológica."
        ),
    )


class ItemCarteiraRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={"example": {"ticker": "PETR4", "quantidade": 100, "eventos_societarios": []}}
    )

    ticker: str = Field(..., description="Código de negociação do ativo, ex: PETR4")
    quantidade: float = Field(..., gt=0, description="Quantidade de ações desse ticker na carteira, na data de início")
    eventos_societarios: list[EventoSocietarioRequest] | None = Field(
        default=None,
        description="Desdobramentos/grupamentos desse ticker ocorridos entre a data de início e hoje (opcional)",
    )


class CarteiraBacktestRequest(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "data_inicio": "2023-01-02",
                "itens": [
                    {"ticker": "PETR4", "quantidade": 100},
                    {"ticker": "VALE3", "quantidade": 50},
                ],
                "reinvestir_dividendos": False,
                "nome_carteira": "Minha carteira de teste",
            }
        }
    )

    data_inicio: date = Field(
        ..., description="Data em que a carteira teria sido montada (AAAA-MM-DD)"
    )
    data_fim: date | None = Field(
        default=None,
        description=(
            "Data final do backtest (AAAA-MM-DD, opcional). Se não informado, usa o "
            "pregão disponível mais recente (hoje ou o último dia útil com cotação)."
        ),
    )
    itens: list[ItemCarteiraRequest] = Field(
        ..., min_length=1, description="Composição da carteira: tickers e quantidades"
    )
    reinvestir_dividendos: bool = Field(
        default=False,
        description=(
            "Dividendos/JCP recebidos no período agora entram sempre no cálculo. Esta "
            "flag controla só o QUE fazer com eles: false (padrão) = somados como caixa "
            "acumulado ao valor final, sem comprar mais ações; true = reinvestidos "
            "automaticamente em novas ações (fracionárias, para fins de simulação) no "
            "preço de fechamento mais próximo do fim de cada ano — ver limitações em "
            "app/backtest/calculo.py."
        ),
    )
    nome_carteira: str | None = Field(
        default=None,
        description="Nome/descrição opcional da carteira, só para identificação na resposta (não é persistido nesta etapa)",
    )
