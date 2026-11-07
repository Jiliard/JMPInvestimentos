"""
Filtros e métodos de ranqueamento (Seção 2.4 e Tabela 3 da monografia).

    Graham      Preço Justo = √(22,5 × LPA × VPA)                Equações (1) e (2)
    Bazin       Preço Teto = DPA 12m ÷ 0,06 (só DY ≥ 6%)          Equação (3)
    Greenblatt  Posição(ROIC) + Posição(EV/EBIT)                  Equação (4)
    Lynch       PEG = P/L ÷ crescimento do lucro (%)              Equação (5)
"""

import numpy as np
import pandas as pd

METODOS = ("graham", "bazin", "greenblatt", "lynch")

# Filtros padrão (Tabela 3). Percentuais em %.
FILTROS_PADRAO = {
    "liq_min": 100_000, "pl_max": 20.0, "pvp_max": 2.0, "roe_min": 10.0, "roic_min": 10.0,
    "margem_min": 5.0, "cagr_min": 0.0, "divida_max": 1.5, "dy_min": 0.0,
}

# Greenblatt exclui instituições financeiras e utilidade pública (balanços não comparáveis).
# A exclusão é por indústria: no TradingView o setor "Finance" também reúne
# incorporadoras e shoppings, que não são instituições financeiras.
GREENBLATT_SETORES_EXCLUIDOS = {"Utilities"}
GREENBLATT_INDUSTRIAS_EXCLUIDAS = {
    "Major Banks", "Regional Banks", "Savings Banks", "Investment Banks/Brokers",
    "Investment Managers", "Financial Conglomerates", "Financial Publishing/Services",
    "Multi-Line Insurance", "Property/Casualty Insurance", "Life/Health Insurance",
    "Specialty Insurance", "Insurance Brokers/Services",
}


def uma_acao_por_empresa(df):
    """
    Mantém só a classe de ação mais líquida de cada empresa (ex.: PETR4 em vez de PETR3).
    As 4 primeiras letras do ticker identificam a empresa na B3; os métodos
    ranqueiam empresas, então classes repetidas distorceriam as posições.
    """
    df = df.sort_values("liquidez", ascending=False)
    return df[~df["ticker"].str[:4].duplicated()]


def aplicar_filtros(df, f):
    """Filtros do usuário. Valor None (campo vazio) ou máximo 0 desliga o filtro."""
    m = pd.Series(True, index=df.index)
    if f["liq_min"] is not None:
        m &= df["liquidez"] >= f["liq_min"]
    if f["pl_max"]:   # P/L negativo = prejuízo
        m &= df["pl"].between(0, f["pl_max"], inclusive="right")
    if f["pvp_max"]:  # P/VP negativo = patrimônio negativo
        m &= df["pvp"].between(0, f["pvp_max"], inclusive="right")
    if f["divida_max"]:
        m &= (df["patrimonio"] > 0) & (df["divida_patrimonio"] <= f["divida_max"])
    for chave, coluna in [("dy_min", "dy"), ("roe_min", "roe"), ("roic_min", "roic"),
                          ("margem_min", "margem"), ("cagr_min", "crescimento")]:
        if f[chave] is not None:
            m &= df[coluna] >= f[chave] / 100
    return df[m]   # ação sem o dado (NaN) não passa: comparação com NaN é falsa


def graham(df):
    df = df[(df["lpa"] > 0) & (df["vpa"] > 0)].copy()
    df["valor_justo"] = np.sqrt(22.5 * df["lpa"] * df["vpa"])
    df["potencial"] = df["valor_justo"] / df["preco"] - 1        # margem de segurança
    return df.sort_values("potencial", ascending=False)


def bazin(df):
    df = df[df["dy"] >= 0.06].copy()
    df["preco_teto"] = df["dpa_12m"] / 0.06
    df["potencial"] = df["preco_teto"] / df["preco"] - 1         # desconto frente ao teto
    return df.sort_values("potencial", ascending=False)


def greenblatt(df):
    excluir = df["setor"].isin(GREENBLATT_SETORES_EXCLUIDOS) | df["industria"].isin(GREENBLATT_INDUSTRIAS_EXCLUIDAS)
    excluir |= df["setor"].isna() | df["industria"].isna()      # sem setor não dá para garantir a regra
    df = df[~excluir & (df["evebit"] > 0) & (df["roic"] > 0)].copy()
    df["pos_roic"] = df["roic"].rank(ascending=False, method="min")
    df["pos_evebit"] = df["evebit"].rank(ascending=True, method="min")
    df["potencial"] = df["pos_roic"] + df["pos_evebit"]           # pontuação (menor é melhor)
    return df.sort_values(["potencial", "pos_roic"])


def lynch(df):
    df = df[(df["pl"] > 0) & (df["crescimento"] > 0)].copy()
    df["peg_ratio"] = df["pl"] / (df["crescimento"] * 100)
    df["potencial"] = df["crescimento"]
    return df.sort_values("peg_ratio")


def ranquear(df, metodo, filtros):
    """Uma ação por empresa -> filtros -> método -> coluna "rank" (1 = melhor)."""
    df = aplicar_filtros(uma_acao_por_empresa(df), filtros)
    metodos = {"graham": graham, "bazin": bazin, "greenblatt": greenblatt, "lynch": lynch}
    df = metodos[metodo](df).reset_index(drop=True)
    df["rank"] = df.index + 1
    return df
