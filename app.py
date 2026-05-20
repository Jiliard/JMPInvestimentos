from flask import Flask, jsonify, request
from flask_cors import CORS
import pandas as pd
import numpy as np
import requests
import warnings
import time

warnings.filterwarnings("ignore")

app = Flask(__name__)
CORS(app)

# CHAVE DA BRAPI PARA O GRÁFICO INDIVIDUAL
BRAPI_TOKEN = "q6nberPzw9REsXXGDXPj1b"

_CACHE = {"df": None, "updated_at": 0}
CACHE_TTL = 900 # Salva em memória por 15 minutos

def obter_dados_base():
    global _CACHE
    agora = time.time()
    
    if _CACHE["df"] is not None and (agora - _CACHE["updated_at"]) < CACHE_TTL:
        return _CACHE["df"].copy()
        
    print("⏳ [API] Varrendo a B3 inteira via TradingView Scanner...")
    
    url_tv = "https://scanner.tradingview.com/brazil/scan"
    
    payload = {
        "markets": ["brazil"],
        "filter": [
            {"left": "type", "operation": "equal", "right": "stock"}
        ],
        "columns": [
            "name", "description", "close", "volume", "price_earnings_ttm", 
            "price_book_ratio", "dividend_yield_recent", "return_on_equity", 
            "return_on_invested_capital", "net_margin", "enterprise_value_ebitda_ttm", 
            "earnings_per_share_basic_ttm"
        ],
        "sort": {"sortBy": "volume", "sortOrder": "desc"},
        "range": [0, 500]
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Content-Type": "application/json"
    }
    
    resultados = []
    
    try:
        r = requests.post(url_tv, json=payload, headers=headers, timeout=10)
        
        if r.status_code != 200:
            print(f"🚨 [ERRO TV] Status {r.status_code}: {r.text}")
            return pd.DataFrame()
            
        dados = r.json()
        linhas = dados.get("data", [])
        
        for item in linhas:
            ticker_completo = item.get("s", "")
            ticker = ticker_completo.split(":")[-1] if ":" in ticker_completo else ticker_completo
            
            if len(ticker) > 6 or len(ticker) < 4:
                continue
            
            val = item.get("d", [])
            if not val or len(val) < 12:
                continue
                
            def seguro(indice, padrao=0.0):
                if val[indice] is None: return padrao
                return float(val[indice])

            preco = seguro(2)
            if preco <= 0.1: continue 
            
            nome_empresa = val[1] if val[1] else f"Cia {ticker}"
            
            volume = seguro(3)
            pl = seguro(4, 15.0)
            pvp = seguro(5, 1.5)
            dy = seguro(6, 0.0) / 100.0
            roe = seguro(7, 0.0) / 100.0
            roic = seguro(8, 0.0) / 100.0
            margem = seguro(9, 0.0) / 100.0
            evebit = seguro(10, 10.0)
            lpa = seguro(11, preco / pl if pl > 0 else 0)
            vpa = preco / pvp if pvp > 0 else 0.0
            
            crescimento = 0.05 
            patrimonio = 5000000000.0
            divida_patrimonio = 0.4
            liquidez = volume * preco

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
            print(f"✅ [API] Sucesso Máximo! {len(df)} ações da B3 extraídas.")
            return df.copy()
            
    except Exception as e:
        print(f"🚨 [ERRO CRÍTICO] Falha na comunicação geral: {e}")
        
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

    metodo = request.args.get('metodo', 'graham')
    liq_min = float(request.args.get('liq_min', 100000))
    pl_max = float(request.args.get('pl_max', 30))
    pvp_max = float(request.args.get('pvp_max', 3))
    dy_min = float(request.args.get('dy_min', 0)) / 100
    roe_min = float(request.args.get('roe_min', 0)) / 100
    roic_min = float(request.args.get('roic_min', 0)) / 100
    margem_min = float(request.args.get('margem_min', 0)) / 100
    cagr_min = float(request.args.get('cagr_min', 0)) / 100

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

    if metodo == "graham":
        df['valor_justo'] = df.apply(lambda r: np.sqrt(22.5 * r['lpa'] * r['vpa']) if r['lpa'] > 0 and r['vpa'] > 0 else 0, axis=1)
        df['potencial'] = (df['valor_justo'] - df['preco']) / df['preco'].replace(0, 1)
        df = df.sort_values(by='potencial', ascending=False)
    elif metodo == "bazin":
        df['preco_teto'] = (df['preco'] * df['dy']) / 0.06
        df['potencial'] = (df['preco_teto'] - df['preco']) / df['preco'].replace(0, 1)
        df = df.sort_values(by='potencial', ascending=False)
    elif metodo == "greenblatt":
        df_m = df[(df['evebit'] > 0) & (df['roic'] > 0)].copy()
        df_m['score'] = df_m['roic'].rank(ascending=False) + df_m['evebit'].rank(ascending=True)
        df = df_m.sort_values(by='score', ascending=True)
        df['potencial'] = df['score']
    elif metodo == "lynch":
        df_l = df[(df['pl'] > 0) & (df['crescimento'] > 0)].copy()
        df_l['peg_ratio'] = df_l['pl'] / (df_l['crescimento'] * 100)
        df = df_l.sort_values(by='peg_ratio', ascending=True)
        df['potencial'] = df['crescimento']

    df = df.reset_index(drop=True)
    df['rank'] = df.index + 1

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
        "roic": float(item['roic']), "roe": float(item['roe']), "margem": float(item['margem']),
        "evebit": float(item['evebit']), "crescimento": float(item['crescimento']),
        "liquidez": float(item['liquidez']), "patrimonio": float(item.get('patrimonio', 0)),
        "divida_patrimonio": float(item.get('divida_patrimonio', 0)),
        "links": {"site_ri": site_ri, "relatorio_oficial": link_relatorio}
    }

    # Tradução do período do site para o padrão suportado pela Brapi API
    p_map = {"30 Dias": "1mo", "6 Meses": "6mo", "1 Ano": "1y", "5 Anos": "5y", "10 Anos": "10y"}
    range_api = p_map.get(periodo_solicitado, "1y")
    chart_data = None

    try:
        # A MÁGICA FINAL: Usamos a Brapi para desenhar o gráfico com precisão!
        url_chart = f"https://brapi.dev/api/quote/{ticker_input}?range={range_api}&interval=1d&token={BRAPI_TOKEN}"
        headers = {"User-Agent": "Mozilla/5.0"}
        r = requests.get(url_chart, headers=headers, timeout=10)
        
        if r.status_code == 200:
            dados_json = r.json()
            results = dados_json.get("results", [])
            
            if results and "historicalDataPrice" in results[0]:
                hist = results[0]["historicalDataPrice"]
                
                # O Pandas devora essa lista e cria o DataFrame do gráfico instantaneamente
                df_chart = pd.DataFrame(hist)
                
                if not df_chart.empty and 'close' in df_chart.columns:
                    # Tenta converter a data de Timestamp para String legível
                    try:
                        df_chart['date'] = pd.to_datetime(df_chart['date'], unit='s').dt.strftime('%Y-%m-%d')
                    except Exception:
                        df_chart['date'] = pd.to_datetime(df_chart['date']).dt.strftime('%Y-%m-%d')
                        
                    series_close = df_chart['close']
                    
                    # Matemática do RSI
                    delta = series_close.diff()
                    gain = delta.where(delta > 0, 0).rolling(14).mean()
                    loss = -delta.where(delta < 0, 0).rolling(14).mean()
                    rs = gain / loss.replace(0, np.nan)
                    rsi = (100 - (100 / (1 + rs))).fillna(50).tolist()
                    
                    # Matemática das Médias Móveis Seguras
                    ma50 = series_close.rolling(window=min(50, len(series_close))).mean().bfill().tolist()
                    ma200 = series_close.rolling(window=min(200, len(series_close))).mean().bfill().tolist()
                    
                    chart_data = {
                        "dates": df_chart['date'].tolist(),
                        "open": df_chart['open'].tolist(),
                        "high": df_chart['high'].tolist(),
                        "low": df_chart['low'].tolist(),
                        "close": df_chart['close'].tolist(),
                        "volume": df_chart.get('volume', pd.Series([0]*len(df_chart))).tolist(),
                        "rsi": rsi,
                        "ma50": ma50,
                        "ma200": ma200
                    }
    except Exception as e:
        print(f"🚨 [ERRO NO GRÁFICO BRAPI] {ticker_input}: {e}")
        pass 

    return jsonify({
        "ticker": ticker_input,
        "nome": item['nome'],
        "logo": item['logo'],
        "fundamentos": fundamentos_dict,
        "chart_data": chart_data
    })

if __name__ == '__main__':
    app.run(debug=True, port=5000)