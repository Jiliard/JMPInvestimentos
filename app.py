import os
import time
import warnings
import numpy as np
import pandas as pd
import requests
from flask import Flask, jsonify, request
from flask_cors import CORS

import database

# Desativa avisos desnecessários do Pandas/Numpy
warnings.filterwarnings("ignore")

app = Flask(__name__)
CORS(app)

# Inicializa as tabelas no Supabase na inicialização
database.inicializar_banco()

# Configuração de Segurança e Cache em RAM
CRON_SECRET_KEY = os.getenv("CRON_SECRET_KEY", "jmp_secret_cron_2026")
CACHE_RAM_TTL = 300  # Tempo de vida do cache em segundos (5 min)
_CACHE_MEMORIA = {"df": None, "updated_at": 0}


# =============================================================================
# HELPER FUNCTIONS (Funções Auxiliares)
# =============================================================================
def _converter_float(valor, padrao=0.0):
    """Converte valores com segurança para float, evitando erros de parse."""
    if valor is None or str(valor).strip() in ['', 'None', 'null', 'undefined']:
        return float(padrao)
    try:
        return float(valor)
    except (ValueError, TypeError):
        return float(padrao)


def _limpar_lista(lista):
    """Substitui valores nulos/NaN em listas por 0.0 para serialização JSON."""
    return [float(x) if pd.notnull(x) else 0.0 for x in lista]


# =============================================================================
# 1. EXTRAÇÃO DE DADOS (TradingView Scanner)
# =============================================================================
def buscar_e_calcular_dados_tradingview():
    """Obtém os dados financeiros da B3 via TradingView e calcula os indicadores."""
    print("⏳ [TRADINGVIEW] A extrair e calcular dados da B3...")
    
    url_tv = "https://scanner.tradingview.com/brazil/scan"
    cols = [
        "name", "description", "close", "volume", "price_earnings_ttm",
        "price_book_ratio", "dividend_yield_recent", "return_on_equity",
        "return_on_invested_capital", "net_margin", "enterprise_value_ebitda_ttm",
        "earnings_per_share_basic_ttm", "dps_common_stock_prim_issue_fy",
        "total_revenue_growth_5y", "earnings_per_share_diluted_growth_ttm_yoy",
        "total_equity", "total_debt"
    ]
    
    payload = {
        "markets": ["brazil"],
        "filter": [{"left": "type", "operation": "equal", "right": "stock"}],
        "columns": cols,
        "sort": {"sortBy": "volume", "sortOrder": "desc"},
        "range": [0, 500]
    }
    
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36",
        "Content-Type": "application/json"
    }
    
    try:
        r = requests.post(url_tv, json=payload, headers=headers, timeout=25)
        if r.status_code != 200:
            print(f"🚨 [ERRO TV] Status {r.status_code}: {r.text}")
            return pd.DataFrame()
            
        resultados = []
        for item in r.json().get("data", []):
            ticker_completo = item.get("s", "")
            ticker = ticker_completo.split(":")[-1] if ":" in ticker_completo else ticker_completo
            
            # Filtra apenas tickers válidos de ações
            if not (4 <= len(ticker) <= 6):
                continue
            
            val = item.get("d", [])
            if not val or len(val) < len(cols):
                continue
            
            row = dict(zip(cols, val))
            preco = _converter_float(row.get("close"))
            
            if preco <= 0.05:
                continue

            # Mapeamento e normalização dos indicadores
            pl = _converter_float(row.get("price_earnings_ttm"))
            pvp = _converter_float(row.get("price_book_ratio"))
            dy = _converter_float(row.get("dividend_yield_recent")) / 100.0
            roe = _converter_float(row.get("return_on_equity")) / 100.0
            roic = _converter_float(row.get("return_on_invested_capital")) / 100.0
            margem = _converter_float(row.get("net_margin")) / 100.0
            evebit = _converter_float(row.get("enterprise_value_ebitda_ttm"))
            lpa = _converter_float(row.get("earnings_per_share_basic_ttm"))
            dpa_12m = _converter_float(row.get("dps_common_stock_prim_issue_fy"))
            
            # Ajuste de DPA e Dividend Yield caso um esteja ausente
            if dpa_12m <= 0 < dy:
                dpa_12m = preco * dy
            elif dpa_12m > 0 >= dy:
                dy = dpa_12m / preco
                
            # Cálculo de VPA e ajuste do LPA via P/L
            vpa = (preco / pvp) if pvp > 0 else preco
            if lpa <= 0 < pl:
                lpa = preco / pl
            
            # Lógica de cálculo da taxa de crescimento
            crescimento_5y = _converter_float(row.get("total_revenue_growth_5y"))
            crescimento_yoy = _converter_float(row.get("earnings_per_share_diluted_growth_ttm_yoy"))
            
            if crescimento_5y != 0:
                crescimento_bruto = crescimento_5y
            elif crescimento_yoy != 0:
                crescimento_bruto = crescimento_yoy
            else:
                crescimento_bruto = (roe * 100.0 * 0.5) if roe > 0 else 5.0
                
            patrimonio = _converter_float(row.get("total_equity"))
            divida_total = _converter_float(row.get("total_debt"))

            resultados.append({
                "ticker": ticker,
                "nome": row.get("description") or f"Cia {ticker}",
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
                "crescimento": crescimento_bruto / 100.0,
                "liquidez": _converter_float(row.get("volume")) * preco,
                "patrimonio": patrimonio,
                "divida_total": divida_total,
                "divida_patrimonio": (divida_total / patrimonio) if patrimonio > 0 else 0.0
            })
            
        df = pd.DataFrame(resultados)
        return df.fillna(0) if not df.empty else pd.DataFrame()
            
    except Exception as e:
        print(f"🚨 [ERRO CRÍTICO FETCH TV]: {e}")
        return pd.DataFrame()


# =============================================================================
# 2. CAMADA DE CACHE HÍBRIDO (RAM + Supabase)
# =============================================================================
import threading

# Controle do estado da atualização em segundo plano
_ATUALIZANDO_EM_SEGUNDO_PLANO = False

def _atualizar_dados_background():
    """Roda a busca no TradingView e atualiza o Supabase em segundo plano."""
    global _CACHE_MEMORIA, _ATUALIZANDO_EM_SEGUNDO_PLANO
    try:
        print("🔄 [BACKGROUND] Buscando dados atualizados do TradingView...")
        df_novo = buscar_e_calcular_dados_tradingview()
        if not df_novo.empty:
            database.salvar_cache_acoes(df_novo)
            
            # Recarrega no cache de RAM com o dado novo
            registros = database.ler_cache_acoes()
            if registros:
                df = pd.DataFrame(registros)
                df['logo'] = df['ticker'].apply(lambda t: f"https://raw.githubusercontent.com/thefintz/icones-b3/main/icones/{t[:4]}.png")
                if 'divida_patrimonio' not in df.columns:
                    df['divida_patrimonio'] = np.where(df['patrimonio'] > 0, df['divida_total'] / df['patrimonio'], 0.0)
                
                _CACHE_MEMORIA["df"] = df
                _CACHE_MEMORIA["updated_at"] = time.time()
                print("✅ [BACKGROUND] Cache em RAM e Supabase atualizados com sucesso!")
    except Exception as e:
        print(f"🚨 [ERRO BACKGROUND UPDATE]: {e}")
    finally:
        _ATUALIZANDO_EM_SEGUNDO_PLANO = False


def obter_dados_base():
    """Retorna instantaneamente o cache (RAM ou Supabase) e atualiza em segundo plano se expirado."""
    global _CACHE_MEMORIA, _ATUALIZANDO_EM_SEGUNDO_PLANO
    agora = time.time()

    # 1. Se o cache em RAM for recente (< 5 min), retorna na hora (< 5ms)
    if _CACHE_MEMORIA["df"] is not None and (agora - _CACHE_MEMORIA["updated_at"]) < CACHE_RAM_TTL:
        return _CACHE_MEMORIA["df"].copy()

    # 2. Se a RAM expirou, lê imediatamente o último dado salvo no Supabase
    registros = database.ler_cache_acoes()
    
    if registros:
        df = pd.DataFrame(registros)
        df['logo'] = df['ticker'].apply(lambda t: f"https://raw.githubusercontent.com/thefintz/icones-b3/main/icones/{t[:4]}.png")
        if 'divida_patrimonio' not in df.columns:
            df['divida_patrimonio'] = np.where(df['patrimonio'] > 0, df['divida_total'] / df['patrimonio'], 0.0)
            
        _CACHE_MEMORIA["df"] = df
        _CACHE_MEMORIA["updated_at"] = agora

        # Dispara atualização em background se não houver outra em andamento
        if not _ATUALIZANDO_EM_SEGUNDO_PLANO:
            _ATUALIZANDO_EM_SEGUNDO_PLANO = True
            threading.Thread(target=_atualizar_dados_background, daemon=True).start()

        # Retorna o dado do banco IMEDIATAMENTE sem esperar o TradingView
        return df.copy()

    # 3. Fallback (apenas se o Supabase estiver 100% vazio na 1ª vez)
    print("⚠️ [BANCO VAZIO] Realizando sincronização primária síncrona...")
    df_novo = buscar_e_calcular_dados_tradingview()
    if not df_novo.empty:
        database.salvar_cache_acoes(df_novo)
        return obter_dados_base()
        
    return pd.DataFrame()


# =============================================================================
# 3. ROTAS DA API
# =============================================================================
@app.route('/api/tickers', methods=['GET'])
def get_tickers():
    """Retorna a lista ordenada dos tickers disponíveis com sufixo da B3 (.SA)."""
    df = obter_dados_base()
    if df.empty:
        return jsonify([])
    return jsonify(sorted((df['ticker'] + ".SA").tolist()))


@app.route('/api/rankings', methods=['GET'])
def get_rankings():
    """Aplica os filtros dinâmicos e calcula as metodologias de valuation."""
    df = obter_dados_base()
    if df.empty:
        return jsonify([])

    # Leitura dos filtros de parâmetros da requisição
    metodo = request.args.get('metodo', 'graham')
    liq_min = _converter_float(request.args.get('liq_min'), 100000)
    pl_max = _converter_float(request.args.get('pl_max'), 30)
    pvp_max = _converter_float(request.args.get('pvp_max'), 3)
    divida_max = _converter_float(request.args.get('divida_max'), 3.0)
    
    dy_min = _converter_float(request.args.get('dy_min'), 0) / 100.0
    roe_min = _converter_float(request.args.get('roe_min'), 0) / 100.0
    roic_min = _converter_float(request.args.get('roic_min'), 0) / 100.0
    margem_min = _converter_float(request.args.get('margem_min'), 0) / 100.0
    cagr_min = _converter_float(request.args.get('cagr_min'), 0) / 100.0

    # Aplicação de filtros
    mask = (df['liquidez'] >= liq_min)
    if pl_max > 0: 
        mask &= (df['pl'] <= pl_max) & (df['pl'] > 0)
    if pvp_max > 0: 
        mask &= (df['pvp'] <= pvp_max) & (df['pvp'] > 0)
    if divida_max > 0: 
        mask &= (df['divida_patrimonio'] <= divida_max)
    
    mask &= (df['dy'] >= dy_min) & (df['roe'] >= roe_min) & (df['roic'] >= roic_min)
    mask &= (df['margem'] >= margem_min) & (df['crescimento'] >= cagr_min)

    df = df[mask].copy().replace([np.inf, -np.inf], np.nan).fillna(0)
    if df.empty:
        return jsonify([])

    # Metodologias de Valuation
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
        df_l = df[(df['pl'] > 0) & (df['crescimento'] > 0)].copy()
        if df_l.empty: return jsonify([])
        df_l['peg_ratio'] = df_l['pl'] / (df_l['crescimento'] * 100.0)
        df = df_l.sort_values(by='peg_ratio', ascending=True)
        df['potencial'] = df['crescimento']

    df = df.reset_index(drop=True)
    df['rank'] = df.index + 1

    return jsonify(df.to_dict(orient='records'))


@app.route('/api/analise', methods=['GET'])
def get_analise_completa():
    """Retorna a análise detalhada e o histórico do gráfico para um ativo específico."""
    ticker_input = request.args.get('ticker', '').upper().strip().replace('.SA', '')
    if not ticker_input:
        return jsonify({"error": "Nenhum ativo selecionado."})
    
    df_base = obter_dados_base()
    if df_base.empty:
        return jsonify({"error": "Servidor temporariamente indisponível."})
        
    empresa_data = df_base[df_base['ticker'] == ticker_input]
    if empresa_data.empty:
        return jsonify({"error": f"Ativo {ticker_input} não encontrado na base."})
        
    item = empresa_data.iloc[0].replace([np.inf, -np.inf], np.nan).fillna(0)

    # Dicionário de fundamentos da empresa
    fundamentos_dict = {
        "preco": float(item['preco']), "pl": float(item['pl']), "pvp": float(item['pvp']),
        "lpa": float(item['lpa']), "vpa": float(item['vpa']), "dy": float(item['dy']),
        "dpa_12m": float(item['dpa_12m']), "roic": float(item['roic']), "roe": float(item['roe']),
        "margem": float(item['margem']), "evebit": float(item['evebit']), "crescimento": float(item['crescimento']),
        "liquidez": float(item['liquidez']), "patrimonio": float(item.get('patrimonio', 0)),
        "divida_patrimonio": float(item.get('divida_patrimonio', 0)),
        "links": {
            "site_ri": f"https://www.google.com/search?q=RI+Relações+com+Investidores+{item['nome']}",
            "relatorio_oficial": f"https://br.tradingview.com/symbols/BMFBOVESPA-{ticker_input}/"
        }
    }

    # Configuração do histórico via Yahoo Finance
    periodo_solicitado = request.args.get('periodo', '1 Ano')
    p_map = {"30 Dias": "1mo", "6 Meses": "6mo", "1 Ano": "1y", "5 Anos": "5y", "10 Anos": "10y"}
    range_api = p_map.get(periodo_solicitado, "1y")
    intervalo_api = "1wk" if range_api in ["5y", "10y"] else "1d"

    chart_data = None
    try:
        url_yahoo = f"https://query2.finance.yahoo.com/v8/finance/chart/{ticker_input}.SA?range={range_api}&interval={intervalo_api}"
        headers_yahoo = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
        
        r_yahoo = requests.get(url_yahoo, headers=headers_yahoo, timeout=10)
        if r_yahoo.status_code == 200:
            res = r_yahoo.json().get("chart", {}).get("result", [])
            if res:
                data = res[0]
                timestamps = data.get("timestamp", [])
                quote = data.get("indicators", {}).get("quote", [{}])[0]
                
                df_chart = pd.DataFrame({
                    "date": pd.to_datetime(timestamps, unit="s").strftime("%Y-%m-%d"),
                    "open": quote.get("open", []),
                    "high": quote.get("high", []),
                    "low": quote.get("low", []),
                    "close": quote.get("close", []),
                    "volume": quote.get("volume", [])
                }).ffill().bfill()
                
                # Cálculo de Indicadores Técnicos (RSI, MA50, MA200)
                series_close = df_chart['close']
                delta = series_close.diff()
                
                gain = delta.where(delta > 0, 0).rolling(window=min(14, len(series_close))).mean()
                loss = -delta.where(delta < 0, 0).rolling(window=min(14, len(series_close))).mean()
                rs = gain / loss.replace(0, np.nan)
                
                rsi = (100 - (100 / (1 + rs))).fillna(50).tolist()
                ma50 = series_close.rolling(window=min(50, len(series_close))).mean().bfill().tolist()
                ma200 = series_close.rolling(window=min(200, len(series_close))).mean().bfill().tolist()

                chart_data = {
                    "dates": df_chart['date'].tolist(),
                    "open": _limpar_lista(df_chart['open']),
                    "high": _limpar_lista(df_chart['high']),
                    "low": _limpar_lista(df_chart['low']),
                    "close": _limpar_lista(df_chart['close']),
                    "volume": _limpar_lista(df_chart['volume']),
                    "rsi": _limpar_lista(rsi),
                    "ma50": _limpar_lista(ma50),
                    "ma200": _limpar_lista(ma200)
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


# =============================================================================
# 4. CRON JOB (Atualização em Segundo Plano)
# =============================================================================
@app.route('/api/cron/update', methods=['GET', 'POST'])
def executar_cron_update():
    """Rota para ser chamada periodicamente por rotinas automatizadas (ex: GitHub Actions)."""
    global _CACHE_MEMORIA
    chave_requisicao = request.args.get('key') or request.headers.get('X-Cron-Key')
    
    if chave_requisicao != CRON_SECRET_KEY:
        return jsonify({"error": "Acesso não autorizado."}), 403

    df_novo = buscar_e_calcular_dados_tradingview()
    if not df_novo.empty:
        # Atualiza a base persistente e invalida a RAM para forçar novos dados
        database.salvar_cache_acoes(df_novo)
        _CACHE_MEMORIA["df"] = None
        _CACHE_MEMORIA["updated_at"] = 0
        
        try:
            database.salvar_historico_ranking(df_novo, "cron_update_geral")
        except Exception as e:
            print(f"🚨 [ERRO HISTÓRICO CRON]: {e}")

        return jsonify({
            "status": "sucesso", 
            "total_acoes": len(df_novo), 
            "mensagem": "Cache e Histórico Supabase atualizados com sucesso!"
        })
        
    return jsonify({"status": "erro", "mensagem": "Falha ao obter dados do TradingView."}), 500


if __name__ == '__main__':
    app.run(debug=True, port=5000)