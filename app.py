from flask import Flask, jsonify, request
from flask_cors import CORS
import pandas as pd
import numpy as np
import requests
import yfinance as yf
import warnings
import time

warnings.filterwarnings("ignore")

app = Flask(__name__)
CORS(app)

# ==============================================================================
# BASE DE ATIVOS B3 (Amostragem Ouro - As 15 gigantes da bolsa)
# ==============================================================================
NOMES_B3 = {
    "PETR4": "Petrobras", "VALE3": "Vale S.A.", "ITUB4": "Itaú Unibanco", 
    "BBDC4": "Banco Bradesco", "BBAS3": "Banco do Brasil", "ABEV3": "Ambev S.A.", 
    "WEGE3": "WEG Equip.", "ELET3": "Eletrobras", "RENT3": "Localiza", 
    "B3SA3": "B3", "SUZB3": "Suzano", "JBSS3": "JBS", 
    "RADL3": "Raia Drogasil", "CSNA3": "Siderúrgica Nac.", "GGBR4": "Gerdau"
}

_CACHE = {"df": None, "updated_at": 0}
CACHE_TTL = 1800 # Salva os dados na memória por 30 minutos

def obter_dados_base():
    global _CACHE
    agora = time.time()
    
    if _CACHE["df"] is not None and (agora - _CACHE["updated_at"]) < CACHE_TTL:
        return _CACHE["df"].copy()
        
    resultados = []
    
    # Cabeçalho limpo simulando um navegador moderno
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "application/json"
    }

    # ARQUITETURA "SNIPER": Bate na API nativa do Yahoo Finance sequencialmente.
    # 15 ações x 0.5s de atraso = 7.5 segundos de processamento (Longe do Timeout de 30s do Render e sem Erro 429).
    for ticker, nome in NOMES_B3.items():
        url_api = f"https://query2.finance.yahoo.com/v10/finance/quoteSummary/{ticker}.SA?modules=summaryDetail,defaultKeyStatistics,financialData"
        
        try:
            r = requests.get(url_api, headers=headers, timeout=5)
            
            if r.status_code == 200:
                json_data = r.json()
                res = json_data.get('quoteSummary', {}).get('result', [])
                if not res:
                    continue
                    
                data = res[0]
                summary = data.get('summaryDetail', {})
                stats = data.get('defaultKeyStatistics', {})
                financials = data.get('financialData', {})

                # Função auxiliar para extrair o valor "raw" com segurança
                def get_raw(grupo, campo, padrao=0.0):
                    return grupo.get(campo, {}).get('raw', padrao)

                preco = get_raw(financials, 'currentPrice') or get_raw(summary, 'previousClose')
                if not preco: 
                    continue

                pl = get_raw(summary, 'trailingPE')
                pvp = get_raw(stats, 'priceToBook')
                dy = get_raw(summary, 'dividendYield')
                roic = get_raw(financials, 'returnOnAssets') # Proxy para ROIC
                roe = get_raw(financials, 'returnOnEquity')
                margem = get_raw(financials, 'profitMargins')
                evebit = get_raw(stats, 'enterpriseToEbitda')
                crescimento = get_raw(financials, 'revenueGrowth')
                lpa = get_raw(stats, 'trailingEps')
                vpa = get_raw(stats, 'bookValue')
                volume = get_raw(summary, 'volume')
                
                div_eq = get_raw(financials, 'debtToEquity')
                divida_patrimonio = div_eq / 100.0 if div_eq else 0.0

                resultados.append({
                    'ticker': ticker,
                    'nome': nome,
                    'logo': f"https://raw.githubusercontent.com/thefintz/icones-b3/main/icones/{ticker[:4]}.png",
                    'preco': float(preco),
                    'pl': float(pl),
                    'pvp': float(pvp),
                    'lpa': float(lpa) if lpa > 0 else float(preco/pl if pl > 0 else 0),
                    'vpa': float(vpa) if vpa > 0 else float(preco/pvp if pvp > 0 else 0),
                    'dy': float(dy),
                    'roic': float(roic),
                    'roe': float(roe),
                    'margem': float(margem),
                    'evebit': float(evebit),
                    'crescimento': float(crescimento),
                    'liquidez': float(volume * preco),
                    'patrimonio': float(get_raw(financials, 'totalRevenue')),
                    'divida_patrimonio': float(divida_patrimonio)
                })

            # O Segredo contra o Bloqueio: Respira por meio segundo antes de pedir a próxima ação
            time.sleep(0.5)

        except Exception as e:
            print(f"Erro na extração via API para {ticker}: {e}")
            continue

    df = pd.DataFrame(resultados)
    
    if df.empty:
        return pd.DataFrame()

    df = df.fillna(0)
    
    _CACHE["df"] = df
    _CACHE["updated_at"] = agora
    return df.copy()

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
    link_relatorio = f"https://br.financas.yahoo.com/quote/{ticker_input}.SA/key-statistics"

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
        # Gráfico Real Time direto no yfinance (seguro pois é uma única ação por vez)
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
