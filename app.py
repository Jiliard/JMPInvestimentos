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

# CHAVE DE API OFICIAL DA BRAPI (Configurada com o seu Token)
BRAPI_TOKEN = "q6nberPzw9REsXXGDXPj1b"

# ==============================================================================
# BASE DE TRADUÇÃO DE NOMES DA B3
# ==============================================================================
NOMES_B3 = {
    "PETR": "Petrobras", "VALE": "Vale S.A.", "ITUB": "Itaú Unibanco", "BBDC": "Banco Bradesco",
    "BBAS": "Banco do Brasil", "ABEV": "Ambev S.A.", "WEGE": "WEG Equipamentos", "ELET": "Eletrobras",
    "RENT": "Localiza Rent a Car", "B3SA": "B3 Bolsa e Balcão", "SUZB": "Suzano Papel", "RDOR": "Rede D'Or São Luiz",
    "RADL": "Raia Drogasil", "CSNA": "Siderúrgica Nacional", "GGBR": "Gerdau S.A.", "USIM": "Usiminas",
    "JBSS": "JBS Alimentos", "MRFG": "Marfrig Global", "BEEF": "Minerva Foods", "CMIG": "Cemig Energia",
    "SBSP": "Sabesp Saneamento", "CPLE": "Copel Energia", "ENEV": "Eneva Geração", "EGIE": "Engie Brasil",
    "CCRO": "Grupo CCR", "GOAU": "Metalúrgica Gerdau", "KLBN": "Klabin Celulose", "CYRE": "Cyrela Empreendimentos",
    "MRVE": "MRV Engenharia", "EZTC": "EZTEC Construtora", "LREN": "Lojas Renner", "MGLU": "Magazine Luiza",
    "ASAI": "Assaí Atacadista", "CRFB": "Carrefour Brasil", "NTCO": "Natura &Co", "TIMS": "TIM Brasil",
    "VIVT": "Telefônica Brasil (Vivo)", "HYPE": "Hypera Pharma", "FLRY": "Grupo Fleury", "TOTS": "Totvs Tecnologia",
    "CSAN": "Cosan S.A.", "RAIZ": "Raízen Energia", "VBBR": "Vibra Energia", "UGPA": "Ultrapar Participações",
    "BRKM": "Braskem Química", "CIEL": "Cielo S.A.", "PSSA": "Porto Seguro", "BBSE": "BB Seguridade",
    "CXSE": "Caixa Seguridade", "MDIA": "M. Dias Branco", "SMTO": "São Martinho", "SLCE": "SLC Agrícola",
    "ALOS": "Allos Shoppings", "IGTI": "Iguatemi S.A.", "MULT": "Multiplan Empreendimentos", "TAEE": "Taesa Transmissão",
    "TRPL": "ISA CTEEP", "SANB": "Banco Santander", "BPAC": "BTG Pactual", "PRIO": "Prio Petróleo",
    "RECV": "PetroRecôncavo", "SOMA": "Grupo Soma", "ARZZ": "Arezzo&Co", "CVCB": "CVC Viagens",
    "GOLL": "Gol Linhas Aéreas", "AZUL": "Azul Linhas Aéreas", "EMBR": "Embraer Aviação", "POMO": "Marcopolo"
}

_CACHE = {"df": None, "updated_at": 0}
CACHE_TTL = 1800 # Cache de 30 minutos em memória para alta performance

def obter_dados_base():
    global _CACHE
    agora = time.time()
    
    if _CACHE["df"] is not None and (agora - _CACHE["updated_at"]) < CACHE_TTL:
        return _CACHE["df"].copy()
        
    # Endpoint da Brapi que lista todas as ações brasileiras com múltiplos e fundamentos
    url_api = f"https://brapi.dev/api/quote/list?token={BRAPI_TOKEN}"
    
    try:
        r = requests.get(url_api, timeout=15)
        
        if r.status_code != 200:
            print(f"Erro na API Brapi. Status: {r.status_code}")
            return pd.DataFrame()
            
        dados_json = r.json()
        lista_stocks = dados_json.get("stocks", [])
        
        df_raw = pd.DataFrame(lista_stocks)
        if df_raw.empty:
            return pd.DataFrame()
            
        resultados = []
        
        for _, stock in df_raw.iterrows():
            ticker = stock.get('stock', '')
            
            # Filtro para ignorar opções ou tickers inválidos (Mantém apenas ações de 4 a 6 caracteres)
            if not ticker or len(ticker) < 4 or len(ticker) > 6:
                continue
                
            # Extração limpa e mapeamento de chaves numéricas tratadas contra valores nulos (None)
            preco = float(stock.get('close', 0.0) or 0.0)
            if preco <= 0:
                continue
                
            # Múltiplos diretos vindos da API oficial
            pl = float(stock.get('pe', 0.0) or 0.0)
            pvp = float(stock.get('pb', 0.0) or 0.0)
            dy = float(stock.get('dividendYield', 0.0) or 0.0) / 100.0 # Transforma ex: 5.4% em 0.054
            
            # Métricas de eficiência baseadas no histórico financeiro mapeado pela Brapi
            # Nota: ROA/ROE costumam vir em porcentagem simples na Brapi (ex: 15.2 para 15.2%)
            roe = float(stock.get('roe', 0.0) or 0.0) / 100.0
            roic = float(stock.get('roic', 0.0) or 0.0) / 100.0
            margem = float(stock.get('netProfitMargin', 0.0) or 0.0) / 100.0
            
            # Indicadores de crescimento e volume financeiro
            volume = float(stock.get('volume', 0.0) or 0.0)
            liquidez = volume * preco
            crescimento = float(stock.get('revenueGrowth3Y', 0.0) or 0.0) / 100.0 # Crescimento composto aproximado
            evebit = float(stock.get('enterpriseValueEbitda', 0.0) or 0.0)
            
            # Cálculo matemático reverso para gerar LPA e VPA com base no preço real e múltiplos de mercado
            lpa = preco / pl if pl > 0 else 0.0
            vpa = preco / pvp if pvp > 0 else 0.0
            
            resultados.append({
                'ticker': ticker,
                'nome': NOMES_B3.get(ticker[:4], stock.get('name', f"Companhia {ticker}")),
                'logo': stock.get('logo', f"https://raw.githubusercontent.com/thefintz/icones-b3/main/icones/{ticker[:4]}.png"),
                'preco': preco,
                'pl': pl,
                'pvp': pvp,
                'lpa': lpa,
                'vpa': vpa,
                'dy': dy,
                'roic': roic,
                'roe': roe,
                'margem': margem,
                'evebit': evebit,
                'crescimento': crescimento,
                'liquidez': liquidez,
                'patrimonio': float(stock.get('marketCap', 0.0) or 0.0), # Mapeia Valor de Mercado como proxy de tamanho
                'divida_patrimonio': 0.0 # Brapi não traz Dívida Líquida no endpoint de lista comum
            })
            
        df = pd.DataFrame(resultados)
        if not df.empty:
            df = df.fillna(0)
            _CACHE["df"] = df
            _CACHE["updated_at"] = agora
            return df.copy()
            
    except Exception as e:
        print(f"Erro crítico no processamento da API Brapi: {e}")
        
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
        return jsonify({"error": f"Ativo {ticker_input} não encontrado na base de dados oficial."})
        
    item = empresa_data.iloc[0].replace([np.inf, -np.inf], np.nan).fillna(0)
    
    site_ri = f"https://www.google.com/search?q=RI+Relações+com+Investidores+{item['nome']}"
    link_relatorio = f"https://brapi.dev/dashboard"

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
        # Consulta ao yfinance mantida isolada para o gráfico histórico do ativo (Sem riscos de Rate Limit por ser unitário)
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
