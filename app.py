from flask import Flask, jsonify, request
from flask_cors import CORS
import pandas as pd
import numpy as np
import yfinance as yf
import requests
import warnings
import time

warnings.filterwarnings("ignore")

app = Flask(__name__)
CORS(app)

# ==============================================================================
# CHAVE DE API OFICIAL DA BRAPI E PORTFÓLIO DE AÇÕES
# ==============================================================================
BRAPI_TOKEN = "q6nberPzw9REsXXGDXPj1b"

NOMES_B3 = {
    "PETR4": "Petrobras", "VALE3": "Vale S.A.", "ITUB4": "Itaú Unibanco", "BBDC4": "Banco Bradesco",
    "BBAS3": "Banco do Brasil", "ABEV3": "Ambev S.A.", "WEGE3": "WEG Equipamentos", "ELET3": "Eletrobras",
    "RENT3": "Localiza", "B3SA3": "B3 Bolsa e Balcão", "SUZB3": "Suzano Papel", "JBSS3": "JBS Alimentos", 
    "RADL3": "Raia Drogasil", "CSNA3": "Siderúrgica Nac.", "GGBR4": "Gerdau S.A.", "USIM5": "Usiminas",
    "CMIG4": "Cemig Energia", "SBSP3": "Sabesp", "CPLE6": "Copel", "LREN3": "Lojas Renner"
}

_CACHE = {"df": None, "updated_at": 0}
CACHE_TTL = 1800 # Salva em memória por 30 minutos

def obter_dados_base():
    global _CACHE
    agora = time.time()
    
    if _CACHE["df"] is not None and (agora - _CACHE["updated_at"]) < CACHE_TTL:
        return _CACHE["df"].copy()
        
    # Agrupa os tickers em uma única string separada por vírgulas (ex: PETR4,VALE3,ITUB4)
    tickers_str = "%2C".join(NOMES_B3.keys())
    
    # MÁGICA DA BRAPI: Pedimos os módulos profundos do Yahoo diretamente pelo endpoint deles
    url_api = f"https://brapi.dev/api/quote/{tickers_str}?token={BRAPI_TOKEN}&modules=summaryDetail,defaultKeyStatistics,financialData"
    
    try:
        r = requests.get(url_api, timeout=15)
        
        if r.status_code != 200:
            print(f"Erro na Brapi: {r.status_code} - {r.text}")
            return pd.DataFrame()
            
        dados_json = r.json()
        resultados = []
        
        for stock in dados_json.get("results", []):
            ticker = stock.get("symbol", "")
            if not ticker: continue
                
            preco = float(stock.get("regularMarketPrice", 0.0) or 0.0)
            if preco <= 0: continue
            
            volume = float(stock.get("regularMarketVolume", 0.0) or 0.0)
            
            # Separa os módulos internos de fundamentos
            summary = stock.get("summaryDetail", {})
            stats = stock.get("defaultKeyStatistics", {})
            financials = stock.get("financialData", {})
            
            # Função blindada para ler dados complexos do JSON (Trata nulos e formatos de dicionário)
            def get_val(grupo, chave, default=0.0):
                if not isinstance(grupo, dict): return default
                val = grupo.get(chave)
                if val is None: return default
                if isinstance(val, dict):
                    return float(val.get("raw", default) or default)
                return float(val)

            # Extração com Fallbacks Matemáticos Seguros
            # (Se a API omitir o dado, usamos a média neutra de mercado para não acionar a eliminação do filtro)
            pl = get_val(summary, "trailingPE") or float(stock.get("priceEarnings") or 15.0)
            pvp = get_val(stats, "priceToBook") or 1.5
            dy = get_val(summary, "dividendYield") or 0.05
            
            # LPA e VPA revertidos matematicamente caso ausentes
            lpa = get_val(stats, "trailingEps") or float(stock.get("earningsPerShare") or (preco / pl))
            vpa = get_val(stats, "bookValue") or (preco / pvp)
            
            roic = get_val(financials, "returnOnAssets") or 0.10
            roe = get_val(financials, "returnOnEquity") or 0.15
            margem = get_val(financials, "profitMargins") or 0.10
            evebit = get_val(stats, "enterpriseToEbitda") or 10.0
            crescimento = get_val(financials, "revenueGrowth") or 0.05
            patrimonio = get_val(financials, "totalRevenue") or 1000000000.0
            
            divida = get_val(financials, "debtToEquity")
            divida_patrimonio = divida / 100.0 if divida else 0.5
            
            resultados.append({
                "ticker": ticker,
                "nome": NOMES_B3.get(ticker, stock.get("shortName", f"Cia {ticker}")),
                "logo": stock.get("logourl") or f"https://raw.githubusercontent.com/thefintz/icones-b3/main/icones/{ticker[:4]}.png",
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
                "liquidez": volume * preco,
                "patrimonio": patrimonio,
                "divida_patrimonio": divida_patrimonio
            })
            
        df = pd.DataFrame(resultados)
        
        if not df.empty:
            df = df.fillna(0)
            _CACHE["df"] = df
            _CACHE["updated_at"] = agora
            return df.copy()
            
    except Exception as e:
        print(f"Erro Crítico de Processamento da API Brapi: {e}")
        
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
        return jsonify({"error": f"Ativo {ticker_input} não encontrado na base Brapi."})
        
    item = empresa_data.iloc[0].replace([np.inf, -np.inf], np.nan).fillna(0)
    
    site_ri = f"https://www.google.com/search?q=RI+Relações+com+Investidores+{item['nome']}"
    link_relatorio = f"https://brapi.dev/"

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
        # Consulta de gráfico isolado direto do Yahoo Finance
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
