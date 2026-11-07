"""
Coleta de dados nas APIs financeiras.

- TradingView Scanner: indicadores fundamentalistas de todas as ações da B3.
- Yahoo Finance: histórico de cotações para o gráfico técnico.

Regra geral: os indicadores vêm prontos da API. Quando a API não traz um
dado, ele fica como NaN (exibido como "N/D") e nunca é estimado ou zerado.
As únicas contas feitas aqui são conversões diretas de dados da própria API
(ex.: DPA = preço x DY; liquidez = volume médio x preço).
"""

import re

import numpy as np
import pandas as pd
import requests

URL_TRADINGVIEW = "https://scanner.tradingview.com/brazil/scan"
URL_YAHOO = "https://query2.finance.yahoo.com/v8/finance/chart/{ticker}.SA"
CABECALHOS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}

# Campos pedidos ao TradingView (todos com cobertura verificada para a B3).
CAMPOS_TRADINGVIEW = [
    "name",                               # ticker
    "description",                        # nome da empresa
    "sector",                             # setor (classificação TradingView)
    "industry",                           # indústria (subdivisão do setor)
    "close",                              # cotação
    "average_volume_30d_calc",            # volume médio de 30 pregões (ações)
    "price_earnings_ttm",                 # P/L (12 meses)
    "price_book_fq",                      # P/VP (último trimestre)
    "book_value_per_share_fq",            # VPA
    "earnings_per_share_basic_ttm",       # LPA (12 meses)
    "dividends_yield_current",            # DY dos últimos 12 meses (%)
    "return_on_equity",                   # ROE (%)
    "return_on_invested_capital",         # ROIC (%)
    "net_margin",                         # margem líquida (%)
    "enterprise_value_to_ebit_ttm",       # EV/EBIT
    "earnings_per_share_basic_cagr_5y",   # CAGR do lucro por ação em 5 anos (%)
    "total_equity_fq",                    # patrimônio líquido
    "total_debt",                         # dívida bruta
    "debt_to_equity",                     # dívida / patrimônio
]

# Lote padrão da B3: 4 caracteres + 1 ou 2 dígitos (PETR4, TAEE11).
# Exclui o mercado fracionário (PETR4F), que duplicaria a mesma ação.
PADRAO_TICKER = re.compile(r"[A-Z0-9]{4}\d{1,2}")

# Fora do escopo da plataforma (FIIs), segundo a seção 1.6 da monografia.
INDUSTRIAS_FORA_DO_ESCOPO = {"Real Estate Investment Trusts"}


def _numero(valor):
    """Converte o valor da API em float; ausente ou inválido vira NaN."""
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return np.nan
    return numero if np.isfinite(numero) else np.nan


def _percentual(valor):
    """A API envia percentuais como 12.5 (=12,5%); aqui viram fração (0.125)."""
    return _numero(valor) / 100.0


# =============================================================================
# TRADINGVIEW: INDICADORES FUNDAMENTALISTAS
# =============================================================================
def _montar_acao(campos):
    """Transforma a resposta bruta de um ativo no registro usado pela plataforma."""
    preco = _numero(campos["close"])

    # P/VP da API é exatamente preço / VPA; se o campo pronto faltar, aplica a mesma conta.
    vpa = _numero(campos["book_value_per_share_fq"])
    pvp = _numero(campos["price_book_fq"])
    if np.isnan(pvp) and vpa and not np.isnan(vpa):
        pvp = preco / vpa

    dy = _percentual(campos["dividends_yield_current"])

    return {
        "ticker": campos["name"],
        "nome": campos["description"] or campos["name"],
        "setor": campos["sector"] or "Outros",
        "industria": campos["industry"] or "Outros",
        "preco": preco,
        "pl": _numero(campos["price_earnings_ttm"]),
        "pvp": pvp,
        "lpa": _numero(campos["earnings_per_share_basic_ttm"]),
        "vpa": vpa,
        "dy": dy,
        "dpa_12m": preco * dy,  # dividendos por ação nos últimos 12 meses (R$)
        "roe": _percentual(campos["return_on_equity"]),
        "roic": _percentual(campos["return_on_invested_capital"]),
        "margem": _percentual(campos["net_margin"]),
        "evebit": _numero(campos["enterprise_value_to_ebit_ttm"]),
        "crescimento": _percentual(campos["earnings_per_share_basic_cagr_5y"]),
        # Liquidez = volume financeiro MÉDIO diário, e não o volume parcial do dia
        "liquidez": _numero(campos["average_volume_30d_calc"]) * preco,
        "patrimonio": _numero(campos["total_equity_fq"]),
        "divida_total": _numero(campos["total_debt"]),
        "divida_patrimonio": _numero(campos["debt_to_equity"]),
    }


def buscar_acoes_tradingview():
    """
    Busca os indicadores de todas as ações da B3 no TradingView.
    Retorna um DataFrame (vazio em caso de falha).
    """
    print("⏳ [TRADINGVIEW] Coletando indicadores da B3...")
    payload = {
        "markets": ["brazil"],
        "filter": [{"left": "type", "operation": "equal", "right": "stock"}],
        "columns": CAMPOS_TRADINGVIEW,
        "sort": {"sortBy": "volume", "sortOrder": "desc"},
        "range": [0, 1000],  # a B3 tem ~800 papéis (incluindo fracionários)
    }

    try:
        resposta = requests.post(URL_TRADINGVIEW, json=payload, headers=CABECALHOS, timeout=25)
        resposta.raise_for_status()
        itens = resposta.json().get("data", [])
    except Exception as erro:
        print(f"🚨 [TRADINGVIEW] Falha na coleta: {erro}")
        return pd.DataFrame()

    acoes = []
    for item in itens:
        valores = item.get("d") or []
        if len(valores) != len(CAMPOS_TRADINGVIEW):
            continue
        campos = dict(zip(CAMPOS_TRADINGVIEW, valores))

        if not PADRAO_TICKER.fullmatch(str(campos["name"])):
            continue
        if campos["industry"] in INDUSTRIAS_FORA_DO_ESCOPO:
            continue
        if not _numero(campos["close"]) > 0:
            continue

        acoes.append(_montar_acao(campos))

    print(f"✅ [TRADINGVIEW] {len(acoes)} ações coletadas.")
    return pd.DataFrame(acoes)


# =============================================================================
# YAHOO FINANCE: HISTÓRICO DE COTAÇÕES PARA O GRÁFICO
# =============================================================================
def buscar_cotacoes_yahoo(ticker, inicio_ts, fim_ts, intervalo):
    """
    Busca candles (abertura, máxima, mínima, fechamento, volume) no Yahoo Finance.

    `inicio_ts` e `fim_ts` são timestamps Unix; `intervalo` é "1d" ou "1wk".
    Retorna um DataFrame com a coluna "date" (UTC) ou um DataFrame vazio.
    """
    url = f"{URL_YAHOO.format(ticker=ticker)}?period1={int(inicio_ts)}&period2={int(fim_ts)}&interval={intervalo}"
    try:
        resposta = requests.get(url, headers=CABECALHOS, timeout=10)
        resposta.raise_for_status()
        resultado = ((resposta.json().get("chart") or {}).get("result") or [None])[0]
    except Exception as erro:
        print(f"🚨 [YAHOO] Falha ao buscar {ticker}: {erro}")
        return pd.DataFrame()

    if not resultado or not resultado.get("timestamp"):
        return pd.DataFrame()

    cotacoes = (resultado.get("indicators", {}).get("quote") or [{}])[0]
    df = pd.DataFrame({
        "date": pd.to_datetime(resultado["timestamp"], unit="s"),
        "open": cotacoes.get("open"),
        "high": cotacoes.get("high"),
        "low": cotacoes.get("low"),
        "close": cotacoes.get("close"),
        "volume": cotacoes.get("volume"),
    })

    # O Yahoo envia 0 ou vazio quando o valor ainda não foi consolidado
    precos = ["open", "high", "low", "close"]
    df[precos] = df[precos].replace(0, np.nan)

    # No último pregão o candle costuma vir vazio e o preço fica só em meta.regularMarketPrice.
    # Usa esse preço oficial como fechamento, para o gráfico bater com a cotação exibida.
    meta = resultado.get("meta", {})
    preco_atual = _numero(meta.get("regularMarketPrice"))
    hora_preco = pd.to_datetime(meta.get("regularMarketTime"), unit="s") if meta.get("regularMarketTime") else None
    if len(df) and np.isnan(df["close"].iloc[-1]) and preco_atual > 0 and hora_preco is not None \
            and hora_preco.date() == df["date"].iloc[-1].date():
        df.loc[df.index[-1], "close"] = preco_atual

    # Candles sem fechamento são descartados; sem abertura/máxima/mínima, o candle é desenhado só no fechamento
    df = df.dropna(subset=["close"]).reset_index(drop=True)
    for coluna in ("open", "high", "low"):
        df[coluna] = df[coluna].fillna(df["close"])
    df["volume"] = df["volume"].fillna(0)
    return df
