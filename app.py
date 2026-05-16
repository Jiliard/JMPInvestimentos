from flask import Flask, jsonify, request
from flask_cors import CORS
import pandas as pd
import numpy as np
import requests
import yfinance as yf
import warnings
import time
import io

warnings.filterwarnings("ignore")

app = Flask(__name__)
CORS(app)

# ==============================================================================
# BASE DE ATIVOS B3 PARA CRUZAMENTO DE NOMES
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
CACHE_TTL = 3600 # Salva os dados em memória por 1 hora para performance e segurança

def obter_dados_base():
    global _CACHE
    agora = time.time()
    
    if _CACHE["df"] is not None and (agora - _CACHE["updated_at"]) < CACHE_TTL:
        return _CACHE["df"].copy()
        
    url_base = "https://www.fundamentus.com.br/resultado.php"
    headers = {'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36'}
    
    # ROLETA DE PROXIES: Lemos o HTML cru. O código não trava se um proxy devolver Erro 502/403.
    rotas = [
        url_base, # 1. Tenta direto (caso o IP do Render esteja limpo)
        f"https://api.allorigins.win/raw?url={url_base}", # 2. Proxy Raw (HTML puro, sem JSON)
        f"https://api.codetabs.com/v1/proxy?quest={url_base}", # 3. Proxy Alternativo
        f"https://corsproxy.io/?{url_base}" # 4. Proxy de Backup
    ]
    
    html_valido = None
    
    for rota in rotas:
        try:
            r = requests.get(rota, headers=headers, timeout=12)
            # VALIDAÇÃO CRÍTICA: Só aceitamos a resposta se contiver a estrutura da tabela
            if r.status_code == 200 and '<table' in r.text and 'Papel' in r.text:
                html_valido = r.text
                break # Achou a tabela! Sai da roleta.
        except Exception:
            continue # Se der timeout ou falhar, pula para a próxima rota sem travar o Python

    # Se todas as rotas falharem, devolvemos vazio para o site exibir a mensagem padrão de erro, mas sem tela travada
    if not html_valido:
        return pd.DataFrame()
        
    # Lendo o HTML com io.StringIO para garantir compatibilidade com Pandas e evitar Warnings
    tabelas = pd.read_html(io.StringIO(html_valido), thousands='.', decimal=',')
    df = tabelas[0]
    
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
    
    df['lpa'] = df.apply(lambda r: r['preco'] / r['pl'] if pd.notnull(r['pl']) and r['pl'] > 0 else 0, axis=1)
    df['vpa'] = df.apply(lambda r: r['preco'] / r['pvp'] if pd.notnull(r['pvp']) and r['pvp'] > 0 else 0, axis=1)
    
    df['logo'] = df['ticker'].apply(lambda x: f"https://raw.githubusercontent.com/thefintz/icones-b3/main/icones/{str(x)[:4]}.png")
    df['nome'] = df['ticker'].apply(lambda t: NOMES_B3.get(t[:4], f"Companhia {t[:4]} S.A."))
    
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
        # AQUI O YAHOO FINANCE ESTÁ SEGURO POIS SÓ BAIXAMOS 1 ATIVO POR VEZ (Não dá Timeout nem Erro 429)
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
