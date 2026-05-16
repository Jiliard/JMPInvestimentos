from flask import Flask, jsonify, request
from flask_cors import CORS
import pandas as pd
import numpy as np
import requests
import yfinance as yf
import cloudscraper
import warnings
import time
import io

warnings.filterwarnings("ignore")

app = Flask(__name__)
CORS(app)

# ==============================================================================
# BASE DE ATIVOS B3 (Nomes oficiais para cruzamento)
# ==============================================================================
NOMES_B3 = {
    "PETR4": "Petrobras", "VALE3": "Vale S.A.", "ITUB4": "Itaú Unibanco", "BBDC4": "Banco Bradesco",
    "BBAS3": "Banco do Brasil", "ABEV3": "Ambev S.A.", "WEGE3": "WEG Equipamentos", "ELET3": "Eletrobras",
    "RENT3": "Localiza", "B3SA3": "B3", "SUZB3": "Suzano", "RDOR3": "Rede D'Or",
    "RADL3": "Raia Drogasil", "CSNA3": "Siderúrgica Nac.", "GGBR4": "Gerdau", "USIM5": "Usiminas",
    "JBSS3": "JBS", "MRFG3": "Marfrig", "BEEF3": "Minerva", "CMIG4": "Cemig",
    "SBSP3": "Sabesp", "CPLE6": "Copel", "ENEV3": "Eneva", "EGIE3": "Engie",
    "CCRO3": "Grupo CCR", "GOAU4": "Metalúrgica Gerdau", "KLBN11": "Klabin", "CYRE3": "Cyrela",
    "MRVE3": "MRV", "EZTC3": "EZTEC", "LREN3": "Lojas Renner", "MGLU3": "Magazine Luiza",
    "ASAI3": "Assaí", "CRFB3": "Carrefour", "NTCO3": "Natura", "TIMS3": "TIM",
    "VIVT3": "Vivo", "HYPE3": "Hypera", "FLRY3": "Fleury", "TOTS3": "Totvs",
    "CSAN3": "Cosan", "RAIZ4": "Raízen", "VBBR3": "Vibra Energia", "UGPA3": "Ultrapar",
    "BRKM5": "Braskem", "CIEL3": "Cielo", "PSSA3": "Porto Seguro", "BBSE3": "BB Seguridade",
    "CXSE3": "Caixa Seguridade", "MDIA3": "M. Dias Branco", "SMTO3": "São Martinho", "SLCE3": "SLC Agrícola",
    "ALOS3": "Allos", "IGTI11": "Iguatemi", "MULT3": "Multiplan", "TAEE11": "Taesa",
    "TRPL4": "ISA CTEEP", "SANB11": "Santander", "BPAC11": "BTG Pactual", "PRIO3": "Prio",
    "RECV3": "PetroRecôncavo", "SOMA3": "Grupo Soma", "ARZZ3": "Arezzo", "CVCB3": "CVC",
    "GOLL4": "Gol", "AZUL4": "Azul", "EMBR3": "Embraer", "POMO4": "Marcopolo"
}

_CACHE = {"df": None, "updated_at": 0}
CACHE_TTL = 3600 # Salva os dados na memória por 1 hora (Alta velocidade)

def obter_dados_base():
    global _CACHE
    agora = time.time()
    
    if _CACHE["df"] is not None and (agora - _CACHE["updated_at"]) < CACHE_TTL:
        return _CACHE["df"].copy()
        
    url_alvo = "https://www.fundamentus.com.br/resultado.php"
    html_valido = None
    
    # --------------------------------------------------------------------------
    # TRÍPLICE BLINDAGEM CONTRA FIREWALLS
    # --------------------------------------------------------------------------
    
    # Estratégia 1: Cloudscraper (Simula um Google Chrome humano perfeitamente)
    try:
        scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'windows', 'desktop': True})
        r = scraper.get(url_alvo, timeout=15)
        r.encoding = 'ISO-8859-1'
        if r.status_code == 200 and '<table' in r.text and 'Papel' in r.text:
            html_valido = r.text
    except Exception as e:
        print(f"Rota 1 (Cloudscraper) falhou: {e}")

    # Estratégia 2: Requests nativo com Headers Premium
    if not html_valido:
        try:
            headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36'}
            r = requests.get(url_alvo, headers=headers, timeout=15)
            r.encoding = 'ISO-8859-1'
            if r.status_code == 200 and '<table' in r.text and 'Papel' in r.text:
                html_valido = r.text
        except Exception as e:
            print(f"Rota 2 (Requests) falhou: {e}")

    # Estratégia 3: Proxy JSON com Tratamento de Erro (Evita o erro Expecting value)
    if not html_valido:
        try:
            proxy_url = f"https://api.allorigins.win/get?url={url_alvo}"
            r = requests.get(proxy_url, timeout=15)
            if r.status_code == 200:
                try:
                    dados = r.json()
                    html_proxy = dados.get('contents', '')
                    if '<table' in html_proxy and 'Papel' in html_proxy:
                        html_valido = html_proxy
                except ValueError:
                    pass # O Proxy devolveu um erro HTML, o código não quebra.
        except Exception as e:
            print(f"Rota 3 (Proxy) falhou: {e}")

    # Se mesmo após as 3 rotas falhar, devolve vazio para não travar a tela
    if not html_valido:
        return pd.DataFrame()

    # --------------------------------------------------------------------------
    # PROCESSAMENTO DE DADOS 
    # --------------------------------------------------------------------------
    try:
        tabelas = pd.read_html(io.StringIO(html_valido), thousands='.', decimal=',')
        df = tabelas[0]
        
        # Filtra para ter apenas as ações validadas do nosso portfólio B3
        df = df[df['Papel'].isin(NOMES_B3.keys())].copy()
        
        cols_percent = ['Div.Yield', 'Mrg Ebit', 'Mrg. Líq.', 'ROIC', 'ROE', 'Cresc. Rec.5a']
        for col in cols_percent:
            if col in df.columns:
                df[col] = df[col].astype(str).str.replace('.', '', regex=False).str.replace(',', '.', regex=False).str.replace('%', '', regex=False)
                df[col] = pd.to_numeric(df[col], errors='coerce') / 100.0

        df = df.rename(columns={
            'Papel': 'ticker', 'Cotação': 'preco', 'Mrg. Líq.': 'margem',
            'Liq.2meses': 'liquidez', 'Cresc. Rec.5a': 'crescimento', 'Div.Yield': 'dy',
            'P/L': 'pl', 'P/VP': 'pvp', 'EV/EBIT': 'evebit', 'ROIC': 'roic', 'ROE': 'roe',
            'Patrim. Líq': 'patrimonio', 'Dív.Líq/ Patrim.': 'divida_patrimonio'
        })

        for col in ['pl', 'pvp', 'evebit', 'patrimonio', 'divida_patrimonio', 'preco', 'liquidez']:
            if col in df.columns:
                df[col] = pd.to_numeric(df[col].astype(str).str.replace('.', '', regex=False).str.replace(',', '.', regex=False), errors='coerce')
        
        # Criação de LPA e VPA com segurança (Evita divisão por zero)
        df['lpa'] = df.apply(lambda r: r['preco'] / r['pl'] if pd.notnull(r['pl']) and r['pl'] != 0 else 0, axis=1)
        df['vpa'] = df.apply(lambda r: r['preco'] / r['pvp'] if pd.notnull(r['pvp']) and r['pvp'] != 0 else 0, axis=1)
        
        df['logo'] = df['ticker'].apply(lambda x: f"https://raw.githubusercontent.com/thefintz/icones-b3/main/icones/{str(x)[:4]}.png")
        df['nome'] = df['ticker'].apply(lambda t: NOMES_B3.get(t, f"Companhia {t} S.A."))
        
        _CACHE["df"] = df
        _CACHE["updated_at"] = agora
        return df.copy()
        
    except Exception as e:
        print(f"Erro no processamento do Pandas: {e}")
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
    link_relatorio = f"https://www.fundamentus.com.br/detalhes.php?papel={ticker_input}"

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
        # AQUI É SEGURO: A requisição de UM ÚNICO ATIVO não aciona o Error 429 do Yahoo.
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
