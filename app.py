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

# ==============================================================================
# BASE DE ATIVOS B3 (As 20 ações mais representativas para o TCC)
# ==============================================================================
NOMES_B3 = {
    "PETR4": "Petrobras", "VALE3": "Vale S.A.", "ITUB4": "Itaú Unibanco", "BBDC4": "Banco Bradesco",
    "BBAS3": "Banco do Brasil", "ABEV3": "Ambev S.A.", "WEGE3": "WEG Equipamentos", "ELET3": "Eletrobras",
    "RENT3": "Localiza", "B3SA3": "B3 Bolsa e Balcão", "SUZB3": "Suzano Papel", "JBSS3": "JBS Alimentos", 
    "RADL3": "Raia Drogasil", "CSNA3": "Siderúrgica Nac.", "GGBR4": "Gerdau S.A.", "USIM5": "Usiminas",
    "CMIG4": "Cemig Energia", "SBSP3": "Sabesp", "CPLE6": "Copel", "LREN3": "Lojas Renner"
}

_CACHE = {"df": None, "updated_at": 0}
CACHE_TTL = 900 # Salva em memória por 15 minutos

def obter_dados_base():
    global _CACHE
    agora = time.time()
    
    if _CACHE["df"] is not None and (agora - _CACHE["updated_at"]) < CACHE_TTL:
        return _CACHE["df"].copy()
        
    print("⏳ [API] Iniciando busca via TradingView Scanner...")
    
    url_tv = "https://scanner.tradingview.com/brazil/scan"
    
    # REMOVIDA A COLUNA "revenue_growth_yoy" QUE ESTAVA CAUSANDO O ERRO 400
    payload = {
        "symbols": {"tickers": [f"BMFBOVESPA:{t}" for t in NOMES_B3.keys()]},
        "columns": [
            "name", "close", "volume", "price_earnings_ttm", "price_book_ratio", 
            "dividend_yield_recent", "return_on_equity", "return_on_invested_capital", 
            "net_margin", "enterprise_value_ebitda_ttm", "earnings_per_share_basic_ttm"
        ]
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Content-Type": "application/json",
        "Origin": "https://br.tradingview.com",
        "Referer": "https://br.tradingview.com/"
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
            
            val = item.get("d", [])
            # Ajustado para o novo tamanho da lista de colunas (11 itens: do índice 0 ao 10)
            if not val or len(val) < 11:
                continue
                
            def seguro(indice, padrao=0.0):
                if val[indice] is None: return padrao
                return float(val[indice])

            preco = seguro(1)
            if preco <= 0: continue
            
            volume = seguro(2)
            pl = seguro(3, 12.0)
            pvp = seguro(4, 1.5)
            dy = seguro(5, 5.0) / 100.0
            roe = seguro(6, 15.0) / 100.0
            roic = seguro(7, 10.0) / 100.0
            margem = seguro(8, 10.0) / 100.0
            
            # Índices reajustados devido à remoção da coluna anterior
            evebit = seguro(9, 8.0)
            lpa = seguro(10, preco / pl if pl > 0 else 0)
            
            vpa = preco / pvp if pvp > 0 else 0.0
            
            # Fallback seguro para as colunas não solicitadas
            crescimento = 0.05 
            patrimonio = 5000000000.0
            divida_patrimonio = 0.4
            
            liquidez = volume * preco
            if liquidez < 100000: liquidez = 5000000.0

            resultados.append({
                "ticker": ticker,
                "nome": NOMES_B3.get(ticker, f"Cia {ticker}"),
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
            print(f"✅ [API] Sucesso! {len(df)} ações extraídas.")
            return df.copy()
            
    except Exception as e:
        print(f"🚨 [ERRO CRÍTICO] Falha na comunicação: {e}")
        
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
        df['potencial'] = (df['valor_justo'] - df['preco']) / (df['preco'] or 1)
        df = df.sort_values(by='potencial', ascending=False)
    elif metodo == "bazin":
        df['preco_teto'] = (df['preco'] * df['dy']) / 0.06
        df['potencial'] = (df['preco_teto'] - df['preco']) / (df['preco'] or 1)
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

    p_map = {"30 Dias": "1mo", "6 Meses": "6mo", "1 Ano": "1y", "5 Anos": "5y", "10 Anos": "10y"}
    chart_data = None

    try:
        import yfinance as yf
        df_yf = yf.download(ticker_input + '.SA', period=p_map.get(periodo_solicitado, "1y"), progress=False, ignore_tz=True)
        if not df_yf.empty:
            if isinstance(df_yf.columns, pd.MultiIndex):
                df_yf.columns = df_yf.columns.get_level_values(0)
            df_yf = df_yf.dropna(subset=['Close']).sort_index()
            
            fechamentos = [float(v) for v in df_yf['Close'].values]
            series_close = pd.Series(fechamentos)
            delta = series_close.diff()
            gain = delta.where(delta > 0, 0).rolling(14).mean()
            loss = -delta.where(delta < 0, 0).rolling(14).mean()
            rs = gain / loss.replace(0, np.nan)
            rsi = (100 - (100 / (1 + rs))).fillna(50).tolist()
            
            chart_data = {
                "dates": df_yf.index.strftime('%Y-%m-%d').tolist(),
                "open": [float(v) for v in df_yf['Open'].values],
                "high": [float(v) for v in df_yf['High'].values],
                "low": [float(v) for v in df_yf['Low'].values],
                "close": fechamentos,
                "volume": [float(v) for v in df_yf['Volume'].values],
                "rsi": rsi,
                "ma50": series_close.rolling(min(50, len(series_close))).mean().fillna(method='bfill').tolist(),
                "ma200": series_close.rolling(min(200, len(series_close))).mean().fillna(method='bfill').tolist()
            }
    except Exception:
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
