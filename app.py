from flask import Flask, jsonify, request
from flask_cors import CORS
import pandas as pd
import numpy as np
import requests
import warnings
import time

# Módulo de persistência de banco de dados
import database

warnings.filterwarnings("ignore")

app = Flask(__name__)
CORS(app)

# Inicializa a estrutura do banco ao ligar o servidor
database.inicializar_banco()

_CACHE = {"df": None, "updated_at": 0}
CACHE_TTL = 1800 # 30 minutos em memória

def obter_dados_base():
    global _CACHE
    agora = time.time()
    
    if _CACHE["df"] is not None and (agora - _CACHE["updated_at"]) < CACHE_TTL:
        return _CACHE["df"].copy()
        
    print("⏳ [API] Varrendo a B3 inteira via TradingView Scanner...")
    
    url_tv = "https://scanner.tradingview.com/brazil/scan"
    
    # MAPEAMENTO OFICIAL DE COLUNAS DO TRADINGVIEW
    cols = [
        "name",                                    # Ticker
        "description",                             # Nome da Empresa
        "close",                                   # Preço da Cotação
        "volume",                                  # Volume Negociado
        "price_earnings_ttm",                      # P/L Pronto (TV)
        "price_book_ratio",                        # P/VP Pronto (TV)
        "dividend_yield_recent",                   # DY recente % (TV)
        "return_on_equity",                        # ROE % (TV)
        "return_on_invested_capital",              # ROIC % (TV)
        "net_margin",                              # Margem Líquida % (TV)
        "enterprise_value_ebitda_ttm",             # EV/EBITDA (TV)
        "earnings_per_share_basic_ttm",            # LPA (TV)
        "dps_common_stock_prim_issue_fy",          # Dividendo por Ação Real (DPA 12M)
        "total_revenue_growth_5y",                 # Crescimento Receita 5 Anos (CAGR)
        "earnings_per_share_diluted_growth_ttm_yoy", # Crescimento Lucro YoY
        "total_equity",                            # Patrimônio Líquido Total
        "total_debt"                               # Dívida Total
    ]
    
    payload = {
        "markets": ["brazil"],
        "filter": [
            {"left": "type", "operation": "equal", "right": "stock"}
        ],
        "columns": cols,
        "sort": {"sortBy": "volume", "sortOrder": "desc"},
        "range": [0, 500]
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Content-Type": "application/json"
    }
    
    resultados = []
    
    try:
        r = requests.post(url_tv, json=payload, headers=headers, timeout=20)
        
        if r.status_code != 200:
            print(f"🚨 [ERRO TV] Status {r.status_code}: {r.text}")
            if _CACHE["df"] is not None: return _CACHE["df"].copy()
            return pd.DataFrame()
            
        dados = r.json()
        linhas = dados.get("data", [])
        
        for item in linhas:
            ticker_completo = item.get("s", "")
            ticker = ticker_completo.split(":")[-1] if ":" in ticker_completo else ticker_completo
            
            if len(ticker) > 6 or len(ticker) < 4:
                continue
            
            val = item.get("d", [])
            if not val or len(val) < len(cols):
                continue
            
            # MONTA UM DICIONÁRIO DINÂMICO PARA NUNCA LER A COLUNA ERRADA
            row_data = dict(zip(cols, val))
            
            def get_val(key, padrao=0.0):
                v = row_data.get(key)
                if v is None: return padrao
                try:
                    return float(v)
                except (ValueError, TypeError):
                    return padrao

            preco = get_val("close")
            if preco <= 0.05: continue 
            
            nome_empresa = row_data.get("description") if row_data.get("description") else f"Cia {ticker}"
            volume = get_val("volume")
            
            pl = get_val("price_earnings_ttm")
            pvp = get_val("price_book_ratio")
            dy = get_val("dividend_yield_recent") / 100.0  # Converte de % para decimal
            roe = get_val("return_on_equity") / 100.0
            roic = get_val("return_on_invested_capital") / 100.0
            margem = get_val("net_margin") / 100.0
            evebit = get_val("enterprise_value_ebitda_ttm")
            lpa = get_val("earnings_per_share_basic_ttm")
            dpa_12m = get_val("dps_common_stock_prim_issue_fy")
            
            # Se o DPA vier zerado do TradingView, reconstrói o DPA via (Preço * DY)
            if dpa_12m <= 0 and dy > 0:
                dpa_12m = preco * dy
            elif dpa_12m > 0 and dy <= 0:
                dy = dpa_12m / preco
                
            # Cálculo exato do VPA: Preço / P/VP
            vpa = (preco / pvp) if pvp > 0 else (preco / 1.0)
            if lpa <= 0 and pl > 0:
                lpa = preco / pl
            
            # Crescimento CAGR (Receita 5 Anos ou Fallback YoY do Lucro)
            crescimento_5y = get_val("total_revenue_growth_5y")
            crescimento_yoy = get_val("earnings_per_share_diluted_growth_ttm_yoy")
            
            if crescimento_5y != 0:
                crescimento_bruto = crescimento_5y
            elif crescimento_yoy != 0:
                crescimento_bruto = crescimento_yoy
            else:
                crescimento_bruto = (roe * 100.0 * 0.5) if roe > 0 else 5.0
                
            crescimento = crescimento_bruto / 100.0
            
            patrimonio = get_val("total_equity")
            divida_total = get_val("total_debt")
            liquidez = volume * preco
            divida_patrimonio = (divida_total / patrimonio) if patrimonio > 0 else 0.0

            resultados.append({
                "ticker": ticker,
                "nome": nome_empresa,
                "logo": f"https://raw.githubusercontent.com/thefintz/icones-b3/main/icones/{ticker[:4]}.png",
                "preco": preco,
                "pl": pl,
                "pvp": pvp,
                "lpa": lpa,
                "vpa": vpa,
                "dy": dy,
                "dpa_12m": dpa_12m,
                "roic": roic,
                "roe": roe,
                "margem": margem,
                "evebit": evebit,
                "crescimento": crescimento,
                "liquidez": liquidez,
                "patrimonio": patrimonio,
                "divida_patrimonio": divida_patrimonio
            })
            
        df = pd.DataFrame(resultados)
        
        if not df.empty:
            df = df.fillna(0)
            _CACHE["df"] = df
            _CACHE["updated_at"] = agora
            print(f"✅ [API] {len(df)} ações da B3 mapeadas e calculadas com sucesso.")
            return df.copy()
            
    except Exception as e:
        print(f"🚨 [ERRO CRÍTICO] Falha no processamento: {e}")
        if _CACHE["df"] is not None: return _CACHE["df"].copy()
        
    return pd.DataFrame()

@app.route('/api/tickers', methods=['GET'])
def get_tickers():
    df = obter_dados_base()
    if df.empty: return jsonify([])
    tickers = sorted((df['ticker'] + ".SA").tolist())
    return jsonify(tickers)

@app.route('/api/rankings', methods=['GET'])
def get_rankings():
    df = obter_dados_base()
    if df.empty: return jsonify([])

    def para_float(valor, padrao=0.0):
        if str(valor).strip() in ['', 'None', 'null', 'undefined']:
            return float(padrao)
        try:
            return float(valor)
        except (ValueError, TypeError):
            return float(padrao)

    metodo = request.args.get('metodo', 'graham')
    liq_min = para_float(request.args.get('liq_min'), 0)
    pl_max = para_float(request.args.get('pl_max'), 0)
    pvp_max = para_float(request.args.get('pvp_max'), 0)
    dy_min = para_float(request.args.get('dy_min'), 0) / 100
    roe_min = para_float(request.args.get('roe_min'), 0) / 100
    roic_min = para_float(request.args.get('roic_min'), 0) / 100
    margem_min = para_float(request.args.get('margem_min'), 0) / 100
    cagr_min = para_float(request.args.get('cagr_min'), 0) / 100

    mask = (df['liquidez'] >= liq_min)
    if pl_max > 0: mask &= (df['pl'] <= pl_max) & (df['pl'] > 0)
    if pvp_max > 0: mask &= (df['pvp'] <= pvp_max) & (df['pvp'] > 0)
    if dy_min > 0: mask &= (df['dy'] >= dy_min)
    if roe_min > 0: mask &= (df['roe'] >= roe_min)
    if roic_min > 0: mask &= (df['roic'] >= roic_min)
    if margem_min > 0: mask &= (df['margem'] >= margem_min)
    if cagr_min > 0: mask &= (df['crescimento'] >= cagr_min)

    df = df[mask].copy()
    df = df.replace([np.inf, -np.inf], np.nan).fillna(0)

    if df.empty: return jsonify([])

    # METODOLOGIAS
    if metodo == "graham":
        df = df[(df['lpa'] > 0) & (df['vpa'] > 0)].copy()
        if df.empty: return jsonify([])
        
        df['valor_justo'] = np.sqrt(22.5 * df['lpa'] * df['vpa'])
        df['potencial'] = (df['valor_justo'] - df['preco']) / df['preco']
        df = df.sort_values(by='potencial', ascending=False)

    elif metodo == "bazin":
        df = df[df['dy'] > 0].copy()
        if df.empty: return jsonify([])
        
        df['preco_teto'] = df['dpa_12m'] / 0.06
        df['potencial'] = (df['preco_teto'] - df['preco']) / df['preco']
        df = df.sort_values(by='potencial', ascending=False)

    elif metodo == "greenblatt":
        df_m = df[(df['evebit'] > 0) & (df['roic'] > 0)].copy()
        if df_m.empty: return jsonify([])
        
        rank_roic = df_m['roic'].rank(ascending=False, method='min')
        rank_evebit = df_m['evebit'].rank(ascending=True, method='min')
        
        df_m['score'] = rank_roic + rank_evebit
        df = df_m.sort_values(by='score', ascending=True)
        df['potencial'] = df['score']

    elif metodo == "lynch":
        df_l = df[df['pl'] > 0].copy()
        if df_l.empty: return jsonify([])
        
        df_l['crescimento_pct'] = df_l['crescimento'] * 100.0
        df_l['peg_ratio'] = df_l['pl'] / df_l['crescimento_pct'].replace(0, 1.0)
        
        df = df_l.sort_values(by='peg_ratio', ascending=True)
        df['potencial'] = df['crescimento']

    df = df.reset_index(drop=True)
    df['rank'] = df.index + 1

    try:
        database.salvar_historico_ranking(df, metodo)
    except Exception as e:
        print(f"🚨 [ERRO BANCO]: {e}")

    return jsonify(df.to_dict(orient='records'))

@app.route('/api/analise', methods=['GET'])
def get_analise_completa():
    ticker_input = request.args.get('ticker', '').upper().strip().replace('.SA', '')
    if not ticker_input: 
        return jsonify({"error": "Nenhum ativo selecionado."})
    
    periodo_solicitado = request.args.get('periodo', '1 Ano')
    df_base = obter_dados_base()
    
    if df_base.empty:
        return jsonify({"error": "Servidor temporariamente indisponível."})
        
    empresa_data = df_base[df_base['ticker'] == ticker_input]
    
    if empresa_data.empty:
        return jsonify({"error": f"Ativo {ticker_input} não encontrado na base."})
        
    item = empresa_data.iloc[0].replace([np.inf, -np.inf], np.nan).fillna(0)
    
    site_ri = f"https://www.google.com/search?q=RI+Relações+com+Investidores+{item['nome']}"
    link_relatorio = f"https://br.tradingview.com/symbols/BMFBOVESPA-{ticker_input}/"

    fundamentos_dict = {
        "preco": float(item['preco']), "pl": float(item['pl']), "pvp": float(item['pvp']),
        "lpa": float(item['lpa']), "vpa": float(item['vpa']), "dy": float(item['dy']),
        "dpa_12m": float(item['dpa_12m']), "roic": float(item['roic']), "roe": float(item['roe']),
        "margem": float(item['margem']), "evebit": float(item['evebit']), "crescimento": float(item['crescimento']),
        "liquidez": float(item['liquidez']), "patrimonio": float(item.get('patrimonio', 0)),
        "divida_patrimonio": float(item.get('divida_patrimonio', 0)),
        "links": {"site_ri": site_ri, "relatorio_oficial": link_relatorio}
    }

    p_map = {"30 Dias": "1mo", "6 Meses": "6mo", "1 Ano": "1y", "5 Anos": "5y", "10 Anos": "10y"}
    range_api = p_map.get(periodo_solicitado, "1y")
    
    intervalo_api = "1d"
    if range_api in ["5y", "10y"]:
        intervalo_api = "1wk"

    chart_data = None

    try:
        url_yahoo = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker_input}.SA?range={range_api}&interval={intervalo_api}"
        headers_yahoo = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"}
        
        r_yahoo = requests.get(url_yahoo, headers=headers_yahoo, timeout=10)
        
        if r_yahoo.status_code == 200:
            yahoo_json = r_yahoo.json()
            res = yahoo_json.get("chart", {}).get("result", [])
            
            if res:
                data = res[0]
                timestamps = data.get("timestamp", [])
                quote = data.get("indicators", {}).get("quote", [{}])[0]
                
                df_chart = pd.DataFrame({
                    "date": pd.to_datetime(timestamps, unit="s"),
                    "open": quote.get("open", []),
                    "high": quote.get("high", []),
                    "low": quote.get("low", []),
                    "close": quote.get("close", []),
                    "volume": quote.get("volume", [])
                })
                
                df_chart["date"] = df_chart["date"].dt.strftime("%Y-%m-%d")
                df_chart = df_chart.ffill().bfill()
                
                series_close = df_chart['close']
                
                delta = series_close.diff()
                gain = delta.where(delta > 0, 0).rolling(window=min(14, len(series_close))).mean()
                loss = -delta.where(delta < 0, 0).rolling(window=min(14, len(series_close))).mean()
                rs = gain / loss.replace(0, np.nan)
                rsi = (100 - (100 / (1 + rs))).fillna(50).tolist()
                
                ma50 = series_close.rolling(window=min(50, len(series_close))).mean().bfill().tolist()
                ma200 = series_close.rolling(window=min(200, len(series_close))).mean().bfill().tolist()
                
                def limpa_nulos(lista):
                    return [float(x) if pd.notnull(x) else 0.0 for x in lista]

                chart_data = {
                    "dates": df_chart['date'].tolist(),
                    "open": limpa_nulos(df_chart['open']),
                    "high": limpa_nulos(df_chart['high']),
                    "low": limpa_nulos(df_chart['low']),
                    "close": limpa_nulos(df_chart['close']),
                    "volume": limpa_nulos(df_chart['volume']),
                    "rsi": limpa_nulos(rsi),
                    "ma50": limpa_nulos(ma50),
                    "ma200": limpa_nulos(ma200)
                }
    except Exception as e:
        print(f"🚨 [ERRO NO GRÁFICO] {ticker_input}: {e}")

    return jsonify({
        "ticker": ticker_input,
        "nome": item['nome'],
        "logo": item['logo'],
        "fundamentos": fundamentos_dict,
        "chart_data": chart_data
    })

if __name__ == '__main__':
    app.run(debug=True, port=5000)