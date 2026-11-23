"""
Filtros e métodos de ranqueamento (Seção 2.5 e Quadro 4 da monografia).

    Graham      Preço Justo = √(22,5 × LPA médio 3 anos × VPA)   Equações (1) e (2)
    Bazin       Preço Teto = DPA 12m ÷ 0,06 (só DY ≥ 6%)          Equação (3)
    Greenblatt  Posição(ROIC) + Posição(EBIT/EV)                  Equação (4)
    Lynch       PEG = P/L ÷ crescimento do lucro (%)              Equação (5)

A plataforma começa sem limites nos indicadores: cada método aplica só as regras do seu
autor. O único filtro preenchido é a liquidez mínima, que segue o critério do NEFIN (FEA-USP):
volume diário acima do mínimo (R$ 500 mil) em mais de 80% dos pregões do ano anterior e
listagem antes de dezembro desse ano.
"""

import numpy as np
import pandas as pd

# Filtros padrão: liquidez mínima de R$ 500 mil (NEFIN) e indicadores sem limite.
# Percentuais em %. None = filtro desligado.
FILTROS_PADRAO = {
    "liq_min": 500_000, "pl_max": None, "pvp_max": None, "roe_min": None, "roic_min": None,
    "margem_min": None, "cagr_min": None, "divida_max": None, "dy_min": None,
}
COLUNAS_LIQUIDEZ = ["volume_80", "listada_antes", "volume_ano"]

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


def anexar_liquidez(df, liquidez):
    """
    Junta ao DataFrame das ações a negociação do ano anterior (`liquidez`: registros com
    ticker, volume_80, listada_antes e volume_ano). Ação sem registro não passa na liquidez
    mínima: não há como saber se foi negociada no ano anterior.
    """
    df = df.drop(columns=COLUNAS_LIQUIDEZ, errors="ignore")
    tabela = pd.DataFrame(liquidez, columns=["ticker"] + COLUNAS_LIQUIDEZ)
    df = df.merge(tabela, on="ticker", how="left")
    df["listada_antes"] = df["listada_antes"].fillna(False).astype(bool)
    for coluna in ("volume_80", "volume_ano"):
        df[coluna] = pd.to_numeric(df[coluna], errors="coerce")
    return df


def uma_acao_por_empresa(df):
    """
    Mantém só a classe de ação mais negociada de cada empresa (ex.: PETR4 em vez de PETR3),
    como no NEFIN: a de maior volume financeiro no ano anterior; sem esse dado, a de maior
    liquidez atual. As 4 primeiras letras do ticker identificam a empresa na B3; os métodos
    ranqueiam empresas, então classes repetidas distorceriam as posições.
    """
    volume_ano = df["volume_ano"] if "volume_ano" in df else pd.Series(np.nan, index=df.index)
    ordem = df.assign(_ano=volume_ano.fillna(-1)).sort_values(["_ano", "liquidez"], ascending=False)
    return ordem[~ordem["ticker"].str[:4].duplicated()].drop(columns="_ano")


def aplicar_filtros(df, f):
    """
    Filtros do usuário. Valor None (campo vazio) ou máximo 0 desliga o filtro.
    Liquidez mínima (critério do NEFIN): volume diário acima de `liq_min` em mais de 80%
    dos pregões do ano anterior (volume_80 > liq_min) e listagem antes de dezembro desse ano.

    Indicador ausente na API: com o filtro preenchido, a empresa sai da lista, pois não há
    como confirmar que ela cumpre o limite (a API não calcula, por exemplo, o P/L de quem
    tem prejuízo). Com o filtro vazio, o indicador ausente não interfere e aparece como N/D.
    """

    m = pd.Series(True, index=df.index)
    if f["liq_min"] is not None:   # sem negociação suficiente, o preço não é confiável nem dá para operar
        if "volume_80" in df:
            m &= (df["volume_80"] > f["liq_min"]) & df["listada_antes"]
        else:
            m &= False
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
    return df[m]


def graham(df):
    """P/L sobre o lucro médio dos três últimos exercícios x P/VP <= 22,5 (Graham)."""
    df = df[(df["lpa_medio_3a"] > 0) & (df["vpa"] > 0)].copy()
    df["valor_justo"] = np.sqrt(22.5 * df["lpa_medio_3a"] * df["vpa"])
    df["potencial"] = df["valor_justo"] / df["preco"] - 1        # margem de segurança
    return df.sort_values("potencial", ascending=False)


def bazin(df):
    df = df[df["dy"] >= 0.06].copy()
    df["preco_teto"] = df["dpa_12m"] / 0.06
    df["potencial"] = df["preco_teto"] / df["preco"] - 1         # desconto frente ao teto
    return df.sort_values("potencial", ascending=False)


def excluida_greenblatt(df):
    """Instituições financeiras e utilidade pública; sem setor não dá para garantir a regra."""
    return (df["setor"].isin(GREENBLATT_SETORES_EXCLUIDOS) | df["industria"].isin(GREENBLATT_INDUSTRIAS_EXCLUIDAS)
            | df["setor"].isna() | df["industria"].isna())


def rendimento_operacional(df):
    """
    EBIT/EV de Greenblatt: lucro operacional dividido pelo valor da empresa.
    EV zero ou negativo deixa o valor vazio (a conta não indica se a empresa é barata).
    Coletas anteriores a 23/11/2026 não têm EBIT e EV separados: nelas o valor é o
    inverso do EV/EBIT da mesma fonte, que é a mesma conta (EV/EBIT = EV ÷ EBIT).
    """
    vazio = pd.Series(np.nan, index=df.index)
    ebit, ev = df.get("ebit", vazio), df.get("ev", vazio)
    evebit = df.get("evebit", vazio)
    direto = ebit / ev.where(ev > 0)
    antigo = 1 / evebit.where(evebit != 0)
    return direto.where(ebit.notna() & ev.notna(), antigo)


def greenblatt(df):
    df = df.copy()
    df["ebit_ev"] = rendimento_operacional(df)
    df = df[~excluida_greenblatt(df) & (df["ebit_ev"] > 0) & (df["roic"] > 0)].copy()
    df["pos_roic"] = df["roic"].rank(ascending=False, method="min")
    df["pos_ebit_ev"] = df["ebit_ev"].rank(ascending=False, method="min")   # maior rendimento = mais barata
    df["potencial"] = df["pos_roic"] + df["pos_ebit_ev"]                    # pontuação (menor é melhor)
    return df.sort_values(["potencial", "ticker"])                          # empate: ordem alfabética


def lynch(df):
    df = df[(df["pl"] > 0) & (df["crescimento"] > 0)].copy()
    df["peg_ratio"] = df["pl"] / (df["crescimento"] * 100)
    return df.sort_values("peg_ratio")


METODOS = {"graham": graham, "bazin": bazin, "greenblatt": greenblatt, "lynch": lynch}


def ranquear(df, metodo, filtros):
    """Uma ação por empresa -> filtros -> método -> coluna "rank" (1 = melhor)."""
    df = aplicar_filtros(uma_acao_por_empresa(df), filtros)
    df = METODOS[metodo](df).reset_index(drop=True)
    df["rank"] = df.index + 1
    return df
