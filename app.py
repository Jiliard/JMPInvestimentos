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
CACHE_TTL = 900 # Salva em memória por 15 minutos

def obter_dados_base():
    global _CACHE
    agora = time.time()
    
    if _CACHE["df"] is not None and (agora - _CACHE["updated_at"]) < CACHE_TTL:
        return _CACHE["df"].copy()
        
    print("⏳ [API] Varrendo a B3 inteira via TradingView Scanner...")
    
    url_tv = "https://scanner.tradingview.com/brazil/scan"
    
    cols = [
        "name",                                       # 0: Nome
        "description",                                # 1: Descrição
        "close",                                      # 2: Preço
        "volume",                                     # 3: Volume
        "price_earnings_ttm",                         # 4: P/L
        "price_book_ratio",                           # 5: P/VP
        "dividend_yield_recent",                      # 6: DY
        "return_on_equity",                           # 7: ROE
        "return_on_invested_capital",                 # 8: ROIC
        "net_margin",                                 # 9: Margem
        "enterprise_value_ebitda_ttm",                # 10: EV/EBITDA
        "earnings_per_share_basic_ttm",               # 11: LPA
        "total_revenue_growth_5y",                    # 12: Crescimento Receita 5 Anos
        "earnings_per_share_diluted_growth_ttm_yoy",  # 13: Crescimento Lucro YoY
        "total_equity",                               # 14: Patrimônio
        "total_debt"                                  # 15: Dívida
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
        r = requests.post(url_tv, json=payload, headers=headers, timeout=12)
        
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
            if not val or len(val) < len(cols):
                continue
                
            def seguro(indice, padrao=0.0):
                if indice < len(val) and val[indice] is not None:
                    try:
                        return float(val[indice])
                    except (ValueError, TypeError):
                        return padrao
                return padrao

            preco = seguro(2)
            if preco <= 0.1: continue 
            
            nome_empresa = val[1] if val[1] else f"Cia {ticker}"
            
            volume = seguro(3)
            pl = seguro(4, 0.0)
            pvp = seguro(5, 0.0)
            dy = seguro(6, 0.0) / 100.0
            roe = seguro(7, 0.0) / 100.0
            roic = seguro(8, 0.0) / 100.0
            margem = seguro(9, 0.0) / 100.0
            evebit = seguro(10, 0.0)
            lpa = seguro(11, preco / pl if pl > 0 else 0.0)
            vpa = preco / pvp if pvp > 0 else 0.0
            
            # Captura de crescimento real sem travar em valor fixo estático
            crescimento_5y = seguro(12, None)
            crescimento_yoy = seguro(13, None)
            
            if crescimento_5y is not None and crescimento_5y != 0:
                crescimento_bruto = crescimento_5y
            elif crescimento_yoy is not None and crescimento_yoy != 0:
                crescimento_bruto = crescimento_yoy
            else:
                # Se a empresa for nova ou não tiver histórico publicado no TradingView, calcula uma estimativa dinâmica via ROE x Retenção
                crescimento_bruto = max((roe * 100.0) * 0.6, 1.0) if roe > 0 else 1.0
                
            crescimento = crescimento_bruto / 100.0  # Em decimal
            
            patrimonio = seguro(14, 0.0)
            divida_total = seguro(15, 0.0)
            
            divida_patrimonio = (divida_total / patrimonio) if patrimonio > 0 else 0.0
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
            print(f"✅ [API] Sucesso Máximo! {len(df)} ações da B3 extraídas dinamicamente.")
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

    # Filtros Fundamentais
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

    # LÓGICA DE RANKING
    if metodo == "graham":
        df = df[(df['lpa'] > 0) & (df['vpa'] > 0)].copy()
        if df.empty: return jsonify([])
        
        df['valor_justo'] = np.sqrt(22.5 * df['lpa'] * df['vpa'])
        df['potencial'] = (df['valor_justo'] - df['preco']) / df['preco']
        df = df.sort_values(by='potencial', ascending=False)

    elif metodo == "bazin":
        df = df[df['dy'] > 0].copy()
        if df.empty: return jsonify([])
        
        df['dpa'] = df['preco'] * df['dy']
        df['preco_teto'] = df['dpa'] / 0.06
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
        
        # Converte crescimento para porcentagem e calcula o PEG Ratio
        df_l['crescimento_pct'] = df_l['crescimento'] * 100.0
        df_l['peg_ratio'] = df_l['pl'] / df_l['crescimento_pct'].replace(0, 1.0)
        
        # Ordena do menor PEG Ratio para o maior
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
        "roic": float(item['roic']), "roe": float(item['roe']), "margem": float(item['margem']),
        "evebit": float(item['evebit']), "crescimento": float(item['crescimento']),
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
        else:
            print(f"🚨 [GRÁFICO] Yahoo RAW API retornou {r_yahoo.status_code} para {ticker_input}")
            
    except Exception as e:
        print(f"🚨 [ERRO CRÍTICO NO GRÁFICO] {ticker_input}: {e}")
        chart_data = None

    return jsonify({
        "ticker": ticker_input,
        "nome": item['nome'],
        "logo": item['logo'],
        "fundamentos": fundamentos_dict,
        "chart_data": chart_data
    })

if __name__ == '__main__':
    app.run(debug=True, port=5000)