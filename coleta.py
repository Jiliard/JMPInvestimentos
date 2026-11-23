"""
Coleta de dados nas APIs financeiras.

- TradingView Scanner: indicadores fundamentalistas de todas as ações da B3.
- Yahoo Finance: histórico de cotações para o gráfico técnico e, para o backtesting,
  fechamento do Ibovespa e eventos das ações (dividendos e desdobramentos).
- B3 (Séries Históricas, arquivo anual COTAHIST): uma vez por ano, a negociação diária do
  ano anterior usada no critério de liquidez do NEFIN.

Regra geral: os indicadores vêm prontos da API. Quando a API não traz um
dado, ele fica como NaN (exibido como "N/D") e nunca é estimado, copiado ou zerado.
As únicas contas feitas aqui usam apenas dados da própria API: conversão de
percentual em fração, DPA = preço x DY, liquidez em R$ = volume médio x preço e
LPA médio dos três últimos exercícios (critério de Graham).
"""

import datetime
import io
import logging
import re
import time
import zipfile
from concurrent.futures import ThreadPoolExecutor

import numpy as np
import pandas as pd
import requests

log = logging.getLogger(__name__)

URL_TRADINGVIEW = "https://scanner.tradingview.com/brazil/scan"
URL_YAHOO = "https://query2.finance.yahoo.com/v8/finance/chart/{simbolo}"   # ações: PETR4.SA; Ibovespa: ^BVSP
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
    "earnings_per_share_basic_fy_h",      # LPA de cada exercício, do mais recente ao mais antigo
    "dividends_yield_current",            # DY dos últimos 12 meses (%)
    "return_on_equity",                   # ROE (%)
    "return_on_invested_capital",         # ROIC (%)
    "net_margin",                         # margem líquida (%)
    "ebit_ttm",                           # EBIT: lucro operacional (12 meses)
    "enterprise_value_current",           # EV: valor da empresa (ações + dívidas - caixa)
    "earnings_per_share_basic_cagr_5y",   # CAGR do lucro por ação em 5 anos (%)
    "total_equity_fq",                    # patrimônio líquido
    "total_debt",                         # dívida bruta
    "debt_to_equity",                     # dívida / patrimônio
]

# Lote padrão da B3: 4 caracteres + 1 ou 2 dígitos (PETR4, ONCO11).
# Exclui só o mercado fracionário (PETR4F), que duplicaria a mesma ação.
# Units (final 11) são mantidas quando a API as classifica como ação. A maioria
# (TAEE11, SANB11, KLBN11) vem como "fundo/ETF" e SEM indicadores; nesses casos a
# empresa entra pelas classes ON/PN (TAEE3, TAEE4), que têm os dados completos.
PADRAO_TICKER = re.compile(r"[A-Z0-9]{4}\d{1,2}")

# Fundos de investimento ficam fora do escopo (seção 1.6 da monografia): FIIs, FIAGROs,
# FICs, FIPs e FISETs negociados com final 11, que não têm balanço de empresa.
INDUSTRIAS_FORA_DO_ESCOPO = {"Real Estate Investment Trusts", "Investment Trusts/Mutual Funds"}
PADRAO_FUNDO = re.compile(r"\b(fundos?|fic|fiset)\b", re.IGNORECASE)


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


def _lpa_tres_exercicios(historico):
    """LPA dos três últimos exercícios (mais recente primeiro); None se faltar algum ano."""
    anos = [_numero(v) for v in (historico or [])[:3]]
    return anos if len(anos) == 3 and not any(np.isnan(anos)) else None


# =============================================================================
# TRADINGVIEW: INDICADORES FUNDAMENTALISTAS
# =============================================================================
def _montar_acao(campos):
    """Transforma a resposta bruta de um ativo no registro usado pela plataforma."""
    preco = _numero(campos["close"])
    dy = _percentual(campos["dividends_yield_current"])
    lpa_anos = _lpa_tres_exercicios(campos["earnings_per_share_basic_fy_h"])

    return {
        "ticker": campos["name"],
        "nome": campos["description"] or campos["name"],
        "setor": campos["sector"],          # ausente fica vazio: o Greenblatt exclui quem não tem setor
        "industria": campos["industry"],
        "preco": preco,
        # A API não envia P/L nem P/VP quando o lucro ou o patrimônio são negativos: ficam vazios
        "pl": _numero(campos["price_earnings_ttm"]),
        "pvp": _numero(campos["price_book_fq"]),
        "lpa": _numero(campos["earnings_per_share_basic_ttm"]),
        "vpa": _numero(campos["book_value_per_share_fq"]),
        # Graham usa o lucro médio dos três últimos anos (Equação 1 da monografia)
        "lpa_anos": lpa_anos,
        "lpa_medio_3a": float(np.mean(lpa_anos)) if lpa_anos else np.nan,
        "dy": dy,
        "dpa_12m": preco * dy,  # dividendos por ação nos últimos 12 meses (R$)
        "roe": _percentual(campos["return_on_equity"]),
        "roic": _percentual(campos["return_on_invested_capital"]),
        "margem": _percentual(campos["net_margin"]),
        "ebit": _numero(campos["ebit_ttm"]),             # Greenblatt divide o EBIT pelo EV
        "ev": _numero(campos["enterprise_value_current"]),
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
    log.info("Coletando indicadores da B3...")
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
        log.error(f"Falha na coleta: {erro}")
        return pd.DataFrame()

    acoes = []
    for item in itens:
        valores = item.get("d") or []
        if len(valores) != len(CAMPOS_TRADINGVIEW):
            continue
        campos = dict(zip(CAMPOS_TRADINGVIEW, valores))

        if not PADRAO_TICKER.fullmatch(str(campos["name"])):
            continue
        if campos["industry"] in INDUSTRIAS_FORA_DO_ESCOPO or PADRAO_FUNDO.search(campos["description"] or ""):
            continue
        if not _numero(campos["close"]) > 0:
            continue

        acoes.append(_montar_acao(campos))

    log.info(f"{len(acoes)} ações coletadas.")
    return pd.DataFrame(acoes)


# =============================================================================
# YAHOO FINANCE: HISTÓRICO DE COTAÇÕES PARA O GRÁFICO
# =============================================================================
def buscar_cotacoes_yahoo(simbolo, inicio_ts, fim_ts, intervalo):
    """
    Busca candles (abertura, máxima, mínima, fechamento, volume) no Yahoo Finance.

    `inicio_ts` e `fim_ts` são timestamps Unix; `intervalo` é "1d" ou "1wk".
    Retorna um DataFrame com a coluna "date" (UTC) ou um DataFrame vazio.
    """
    url = f"{URL_YAHOO.format(simbolo=simbolo)}?period1={int(inicio_ts)}&period2={int(fim_ts)}&interval={intervalo}"
    for tentativa in range(2):   # o Yahoo recusa pedidos de vez em quando: tenta mais uma vez
        try:
            resposta = requests.get(url, headers=CABECALHOS, timeout=15)
            resposta.raise_for_status()
            resultado = ((resposta.json().get("chart") or {}).get("result") or [None])[0]
            break
        except Exception as erro:
            if tentativa == 1:
                log.error(f"Falha ao buscar {simbolo}: {erro}")
                return pd.DataFrame()
            time.sleep(1.5)

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

    # Candles sem fechamento são descartados. Abertura, máxima, mínima e volume ausentes
    # ficam vazios (o candle não é desenhado), sem copiar outro valor no lugar.
    return df.dropna(subset=["close"]).reset_index(drop=True)


# =============================================================================
# YAHOO FINANCE: IBOVESPA E EVENTOS (BACKTESTING)
# =============================================================================
def _data_b3(timestamp):
    """Timestamp Unix -> data no fuso da B3 (o Yahoo marca eventos à 0h de Brasília)."""
    return pd.to_datetime(timestamp, unit="s", utc=True).tz_convert("America/Sao_Paulo").date()


def buscar_eventos_yahoo(ticker, dias=40):
    """
    Dividendos (R$ por ação, na data ex) e desdobramentos/grupamentos (ações novas por
    ação antiga: 2 = desdobramento 1:2; 0,1 = grupamento 10:1) dos últimos `dias`.
    Com eles e o preço bruto gravado a cada pregão, o retorno total do backtesting é
    calculado sem depender de preços "ajustados", que mudam retroativamente.
    Retorna uma lista de (data, ticker, tipo, valor); vazia se não houver eventos ou em falha.
    """
    url = f"{URL_YAHOO.format(simbolo=ticker + '.SA')}?range={dias}d&interval=1d&events=div%7Csplit"
    try:
        resposta = requests.get(url, headers=CABECALHOS, timeout=10)
        resposta.raise_for_status()
        eventos = (((resposta.json().get("chart") or {}).get("result") or [{}])[0] or {}).get("events") or {}
    except Exception as erro:
        log.error(f"Falha ao buscar eventos de {ticker}: {erro}")
        return []
    linhas = [(_data_b3(e["date"]), ticker, "dividendo", _numero(e.get("amount")))
              for e in (eventos.get("dividends") or {}).values()]
    linhas += [(_data_b3(e["date"]), ticker, "desdobramento", _numero(e.get("numerator")) / _numero(e.get("denominator")))
               for e in (eventos.get("splits") or {}).values()]
    return [linha for linha in linhas if np.isfinite(linha[3]) and linha[3] > 0]


def buscar_complementos_backtesting(tickers, dias=40):
    """
    Fechamentos diários do Ibovespa e eventos de todas as ações dos últimos `dias`.
    Busca uma janela de vários dias para recuperar sozinho dias em que a rotina não rodou.
    Retorna (lista de (data, fechamento do Ibovespa), lista de eventos).
    """
    agora = pd.Timestamp.now(tz="America/Sao_Paulo")
    ibov = buscar_cotacoes_yahoo("^BVSP", (agora - pd.Timedelta(days=dias)).timestamp(), agora.timestamp(), "1d")
    hoje = agora.date()
    fechado = agora.hour >= 19            # o fechamento de hoje só é definitivo depois do pregão
    fechamentos = [] if ibov.empty else [
        (d.date(), float(c)) for d, c in zip(ibov["date"], ibov["close"]) if d.date() < hoje or (d.date() == hoje and fechado)]

    with ThreadPoolExecutor(max_workers=8) as executor:
        eventos = [e for lista in executor.map(lambda t: buscar_eventos_yahoo(t, dias), tickers) for e in lista]
    log.info(f"Ibovespa: {len(fechamentos)} fechamentos; eventos: {len(eventos)} em {len(tickers)} ações.")
    return fechamentos, eventos


# =============================================================================
# B3: LIQUIDEZ DO ANO ANTERIOR (CRITÉRIO DO NEFIN)
# =============================================================================
# NEFIN (FEA-USP), Methodology, seção 3 - Eligibility Criteria. Uma ação é elegível no ano t se:
#   1. é a classe de ação mais negociada da empresa (volume total no ano t-1);
#   2. foi negociada em mais de 80% dos pregões do ano t-1 com volume diário acima de
#      R$ 500 mil (se listada durante t-1, conta-se da listagem até o fim do ano);
#   3. foi listada antes de dezembro do ano t-1.
# Os dados vêm das Séries Históricas da B3 (arquivo anual COTAHIST): uma linha por ação e
# pregão, com o volume financeiro negociado informado pela própria bolsa.
URL_COTAHIST = "https://bvmf.bmfbovespa.com.br/InstDados/SerHist/COTAHIST_A{ano}.ZIP"
VOLUME_DIARIO_NEFIN = 500_000
FRACAO_PREGOES_NEFIN = 0.80


def _ler_cotahist(ano):
    """
    Lê o arquivo anual de cotações da B3 e devolve {ticker: {data: volume financeiro em R$}}
    do mercado à vista (lote padrão). Layout oficial: data nas posições 3-10, código de
    negociação 13-24, tipo de mercado 25-27 (010 = à vista) e volume total 171-188 (2 decimais).
    """
    resposta = requests.get(URL_COTAHIST.format(ano=ano), headers=CABECALHOS, timeout=300)
    resposta.raise_for_status()
    negociacao = {}
    with zipfile.ZipFile(io.BytesIO(resposta.content)) as arquivo:
        with arquivo.open(arquivo.namelist()[0]) as linhas:
            for linha in linhas:
                if linha[:2] != b"01" or linha[24:27] != b"010":
                    continue
                ticker = linha[12:24].decode("latin-1").strip()
                data = datetime.date(int(linha[2:6]), int(linha[6:8]), int(linha[8:10]))
                volume = int(linha[170:188]) / 100
                dias = negociacao.setdefault(ticker, {})
                dias[data] = dias.get(data, 0.0) + volume
    return negociacao


def buscar_liquidez_anual(tickers, ano):
    """
    Critério de liquidez do NEFIN para o ano `ano` (o ano anterior ao dos rankings), com os
    arquivos de cotações da B3 de `ano` e do ano anterior a ele (para saber se a ação já
    estava listada no início de `ano`). Retorna um DataFrame com uma linha por ação: pregões
    considerados, pregões com volume acima de R$ 500 mil, volume financeiro total do ano,
    data de entrada considerada e se é elegível. Vazio em caso de falha.
    """
    try:
        negociacao = _ler_cotahist(ano)
        ja_listadas = set(_ler_cotahist(ano - 1))
    except Exception as erro:
        log.error(f"Falha ao ler as cotações da B3 de {ano}: {erro}")
        return pd.DataFrame()
    pregoes = sorted({d for dias in negociacao.values() for d in dias})
    if len(pregoes) < 200 or pregoes[-1].month != 12:      # ano ainda não encerrado
        log.error(f"Cotações de {ano} incompletas ({len(pregoes)} pregões); liquidez não calculada.")
        return pd.DataFrame()
    dezembro = datetime.date(ano, 12, 1)

    linhas = []
    for ticker in tickers:
        dias = negociacao.get(ticker, {})
        # Já negociada no ano anterior: listada antes do início do ano. Senão, a entrada
        # na bolsa é o primeiro pregão em que foi negociada no ano.
        entrada = pregoes[0] if ticker in ja_listadas else (min(dias) if dias else None)
        janela = [d for d in pregoes if entrada and d >= entrada]
        dias_acima = sum(1 for d in janela if dias.get(d, 0) > VOLUME_DIARIO_NEFIN)
        # Volume diário superado em mais de 80% dos pregões: com m = parte inteira de 0,8 x n + 1,
        # é o m-ésimo maior volume do ano (pregão sem negócio conta como zero). A ação tem mais
        # de 80% dos pregões acima de um valor X se, e somente se, esse volume for maior que X.
        # Assim o filtro aceita qualquer valor mínimo digitado pelo usuário.
        volumes = sorted((dias.get(d, 0.0) for d in janela), reverse=True)
        posicao = int(FRACAO_PREGOES_NEFIN * len(volumes)) + 1
        volume_80 = volumes[posicao - 1] if posicao <= len(volumes) else 0.0
        linhas.append({"ticker": ticker, "ano": ano, "pregoes": len(janela), "dias_acima": dias_acima,
                       "volume_ano": float(sum(dias.values())), "volume_80": float(volume_80),
                       "listagem": entrada, "listada_antes": bool(entrada is not None and entrada < dezembro),
                       "elegivel": bool(entrada is not None and entrada < dezembro and janela
                                        and dias_acima > FRACAO_PREGOES_NEFIN * len(janela))})
    log.info(f"Liquidez de {ano} (B3): {len(linhas)} ações avaliadas, "
             f"{sum(r['elegivel'] for r in linhas)} elegíveis pelo critério do NEFIN.")
    return pd.DataFrame(linhas)
