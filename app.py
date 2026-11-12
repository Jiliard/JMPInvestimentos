"""
Servidor Flask do JMP Investimentos.

Os dados ficam em memória. Ao subir, o servidor busca no TradingView e no banco
(Supabase) ao mesmo tempo e usa o que chegar primeiro. Dados com mais de 5 minutos
são atualizados em segundo plano, e cada coleta fica gravada no banco (última
coleta para a primeira carga + registro diário por pregão para o backtesting).

Rotas:
    GET /api/rankings          ranking por método e filtros (?data=AAAA-MM-DD só para backtesting)
    GET /api/historico/periodo primeiro e último dia do histórico
    GET /api/tickers           lista de tickers para a busca
    GET /api/analise           ficha da empresa e gráfico técnico
    GET /api/saude             verificação leve usada para manter o servidor acordado
    GET /api/coletar           coleta e grava agora (rotina cron.yml)
"""

import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import numpy as np
import pandas as pd
from flask import Flask, abort, jsonify, request, send_from_directory
from flask_cors import CORS

import coleta
import cron_updater
import database
import estrategias

app = Flask(__name__)
CORS(app, expose_headers=["X-Data-Base"])   # o front na Vercel lê a data dos dados

IDADE_MAXIMA = timedelta(minutes=5)          # dado mais velho que isso é atualizado em segundo plano
ARQUIVOS_PUBLICOS = {"index.html", "script.js", "style.css", "favicon.svg"}

_cache = {"df": None, "gravou_no_banco": False,  # dados atuais em memória (responde sem ir ao banco)
          "complementos_em": None}                # dia em que o Ibovespa e os eventos foram gravados
_cache_historico = {}
_cache_graficos = {}                         # (ticker, período) -> (gráfico, momento da busca)
_tarefas = set()                             # atualizações em andamento (evita duplicadas)
_lock = threading.Lock()
_lock_primeira_carga = threading.Lock()
_lock_coleta_manual = threading.Lock()


# =============================================================================
# AUXILIARES
# =============================================================================
_numero = database.numero_ou_none   # NaN/infinito/vazio viram None, para gerar JSON válido


def _para_json(df):
    df = df.drop(columns=["updated_at", "data"], errors="ignore").replace([np.inf, -np.inf], np.nan)
    return df.astype(object).where(df.notna(), None).to_dict(orient="records")


def _montar_df(registros):
    """DataFrame padronizado a partir do banco (lista de registros) ou da coleta (DataFrame)."""
    df = pd.DataFrame(registros, copy=True)   # cópia: não altera o DataFrame da coleta
    for coluna in database.COLUNAS_NUMERICAS:
        df[coluna] = pd.to_numeric(df[coluna], errors="coerce")
    df["logo"] = "https://raw.githubusercontent.com/thefintz/icones-b3/main/icones/" + df["ticker"].str[:4] + ".png"
    return df


def _ler_filtros(args):
    """Parâmetro ausente = padrão do Quadro 4; parâmetro vazio = filtro desligado."""
    return {chave: (padrao if chave not in args else _numero(args.get(chave)))
            for chave, padrao in estrategias.FILTROS_PADRAO.items()}


def _desatualizado(momento):
    """True se o dado não tem data, é velho ou está no futuro (gravado em outro fuso)."""
    if momento is None or pd.isna(momento):
        return True
    idade = database.agora_b3() - momento
    return idade > IDADE_MAXIMA or idade < -IDADE_MAXIMA


def _em_segundo_plano(chave, funcao, *args):
    """Roda `funcao` em outra thread, uma vez por chave."""
    with _lock:
        if chave in _tarefas:
            return
        _tarefas.add(chave)

    def tarefa():
        try:
            funcao(*args)
        except Exception as erro:
            print(f"🚨 [ATUALIZAÇÃO] {chave}: {erro}")
        finally:
            _tarefas.discard(chave)

    threading.Thread(target=tarefa, daemon=True).start()


# =============================================================================
# DADOS DAS AÇÕES
# =============================================================================
def _coletar_acoes():
    """Coleta no TradingView, atualiza a memória e depois grava no banco."""
    bruto = coleta.buscar_acoes_tradingview()
    if bruto.empty:
        return None
    df = _montar_df(bruto)
    df["updated_at"] = database.agora_b3()
    _cache["df"] = df   # memória primeiro: quem espera recebe na hora
    _cache["gravou_no_banco"] = database.salvar_coleta(bruto)
    return df


def _carregar_do_banco():
    """Usa a última coleta salva, se a memória ainda estiver vazia (o TradingView não chegou antes)."""
    registros = database.ler_cache()
    if registros and _cache["df"] is None:
        _cache["df"] = _montar_df(registros)


def _primeira_carga(limite_segundos=60):
    """
    Servidor recém-iniciado: busca no TradingView e no banco ao mesmo tempo e
    responde com o primeiro que chegar. Medido: TradingView ~0,9 s; banco ~2,8 s
    (conexão nova). O banco serve de reserva se o TradingView falhar.
    """
    pronto = threading.Event()
    terminadas = []
    with _lock:
        _tarefas.add("acoes")               # evita que outra coleta comece enquanto esta roda

    def executar(funcao):
        try:
            funcao()
        except Exception as erro:
            print(f"🚨 [PRIMEIRA CARGA] {funcao.__name__}: {erro}")
        finally:
            if funcao is _coletar_acoes:
                _tarefas.discard("acoes")
        terminadas.append(funcao)
        if _cache["df"] is not None or len(terminadas) == 2:
            pronto.set()

    for funcao in (_coletar_acoes, _carregar_do_banco):
        threading.Thread(target=executar, args=(funcao,), daemon=True).start()
    pronto.wait(limite_segundos)


def dados_atuais():
    """Últimos dados em memória; se estiverem velhos, atualiza em segundo plano."""
    if _cache["df"] is None:
        with _lock_primeira_carga:          # vários acessos ao mesmo tempo disparam uma só carga
            if _cache["df"] is None:
                _primeira_carga()

    df = _cache["df"]
    if df is None:
        return None
    if _desatualizado(df["updated_at"].max()):
        _em_segundo_plano("acoes", _coletar_acoes)
    return df


def atualizar_agora(intervalo_minimo):
    """
    Coleta imediata (botão de atualizar da tela e rotina cron.yml). Se a última coleta
    gravada no banco tem menos de `intervalo_minimo`, reaproveita-a. Retorna os dados
    ou None se a API não respondeu.
    """
    with _lock_coleta_manual:
        df = _cache["df"]
        if df is not None and _cache["gravou_no_banco"] and database.agora_b3() - df["updated_at"].max() < intervalo_minimo:
            return df
        return _coletar_acoes()


def dados_historicos(data_ref):
    """(DataFrame, dia usado) do último pregão coletado até `data_ref` (backtesting)."""
    if data_ref not in _cache_historico:
        registros, dia = database.ler_historico(data_ref)
        resultado = (_montar_df(registros) if registros else pd.DataFrame(), dia)
        if dia is None or data_ref >= database.agora_b3().date():
            return resultado             # hoje ainda pode mudar: não guarda
        _cache_historico[data_ref] = resultado
    return _cache_historico[data_ref]


def _ao_iniciar():
    """Migra o banco e pré-carrega os dados fora da inicialização do servidor.
    Rodar na importação travava o processo do gunicorn além do limite de 30 s."""
    database.inicializar_banco()
    dados_atuais()


threading.Thread(target=_ao_iniciar, daemon=True).start()


# =============================================================================
# ROTAS
# =============================================================================
@app.route("/")
def home():
    return send_from_directory(".", "index.html")


@app.route("/<path:arquivo>")
def arquivos(arquivo):
    if arquivo not in ARQUIVOS_PUBLICOS:   # nunca expõe código, .env ou .git
        abort(404)
    return send_from_directory(".", arquivo)


@app.route("/api/rankings")
def rankings():
    metodo = request.args.get("metodo", "graham")
    if metodo not in estrategias.METODOS:
        return jsonify({"error": f"Método '{metodo}' desconhecido."}), 400

    data_texto = (request.args.get("data") or "").strip()
    if data_texto:   # modo backtesting: usa somente dados coletados até a data
        try:
            data_ref = datetime.strptime(data_texto, "%Y-%m-%d").date()
        except ValueError:
            return jsonify({"error": "Data inválida. Use o formato AAAA-MM-DD."}), 400
        if data_ref > database.agora_b3().date():
            return jsonify({"error": "A data de referência não pode estar no futuro."}), 400
        df, dia = dados_historicos(data_ref)
        if dia is None:
            inicio, _ = database.periodo_historico()
            if inicio:
                msg = f"Não há pregão registrado até {data_ref:%d/%m/%Y}. O histórico começa em {inicio:%d/%m/%Y}."
            else:
                msg = "O histórico ainda está vazio: o registro diário dos pregões começa após a implantação desta versão."
            return jsonify({"error": msg}), 404
        data_base = dia.isoformat()
    else:
        if request.args.get("atualizar") == "1":   # botão de atualizar da tela
            atualizar_agora(timedelta(minutes=1))
        df = dados_atuais()
        if df is None or df.empty:
            return jsonify([])
        data_base = f"{df['updated_at'].max():%Y-%m-%dT%H:%M}"

    resposta = jsonify(_para_json(estrategias.ranquear(df, metodo, _ler_filtros(request.args))))
    resposta.headers["X-Data-Base"] = data_base
    return resposta


@app.route("/api/historico/periodo")
def historico_periodo():
    inicio, fim = database.periodo_historico()
    return jsonify({"inicio": inicio and inicio.isoformat(), "fim": fim and fim.isoformat()})


def _em_pregao(momento):
    """Dia útil entre 10h e 19h (o pregão fecha às 17h ou 18h e a API tem 15 min de atraso)."""
    return momento.weekday() < 5 and 10 <= momento.hour < 19


@app.route("/api/saude")
def saude():
    """
    Chamada a cada 10 minutos pela rotina agendada no Supabase (pg_cron) e, como reserva,
    pelo GitHub Actions (manter_acordado.yml), para o servidor não dormir. Durante o
    pregão, também dispara em segundo plano uma coleta se os dados tiverem mais de
    20 minutos. Depois das 19h dos dias úteis, grava uma vez o fechamento do Ibovespa
    e os eventos das ações. Assim o servidor grava sozinho o registro diário no banco.
    """
    df = _cache["df"]
    agora = database.agora_b3()
    if _em_pregao(agora) and (df is None or agora - df["updated_at"].max() > timedelta(minutes=20)):
        _em_segundo_plano("acoes", _coletar_acoes)
    if agora.weekday() < 5 and agora.hour >= 19 and _cache["complementos_em"] != agora.date():
        _em_segundo_plano("complementos", _registrar_complementos, agora.date())
    return jsonify({"status": "ok", "dados_em_memoria": df is not None})


def _registrar_complementos(dia):
    if cron_updater.registrar_complementos():
        _cache["complementos_em"] = dia


@app.route("/api/coletar")
def coletar():
    """
    Coleta na API e grava no banco agora, e responde se deu certo. Chamada pela rotina
    cron.yml do GitHub Actions, que assim acompanha a gravação sem acessar o banco nem
    o TradingView diretamente. Se a última coleta tiver menos de 5 minutos, apenas
    confirma (evita coletas repetidas em sequência).
    """
    df = atualizar_agora(IDADE_MAXIMA)
    if df is None:
        return jsonify({"status": "erro", "motivo": "a API do TradingView não retornou dados"}), 503
    if not _cache["gravou_no_banco"]:
        return jsonify({"status": "erro", "motivo": "falha ao gravar no banco"}), 503
    momento = df["updated_at"].max()
    return jsonify({"status": "ok", "acoes": len(df), "coleta": f"{momento:%d/%m/%Y %H:%M}",
                    "pregao": f"{database.pregao_de(momento):%d/%m/%Y}"})


@app.route("/api/tickers")
def tickers():
    df = dados_atuais()
    return jsonify([] if df is None else sorted(df["ticker"] + ".SA"))


# =============================================================================
# ANÁLISE DETALHADA
# =============================================================================
# Período -> (dias exibidos, gráfico semanal?)
PERIODOS = {"30 Dias": (31, False), "6 Meses": (183, False), "1 Ano": (366, False),
            "5 Anos": (1827, True), "10 Anos": (3653, True)}


def montar_grafico(ticker, periodo):
    """
    Busca as cotações no Yahoo Finance e calcula os indicadores técnicos:
    IFR(14) de Wilder e médias móveis de 50 e 200 pregões (10 e 40 semanas no semanal).
    Busca 300 dias a mais para os indicadores já começarem calculados no 1º dia exibido.
    Também traz o Ibovespa no mesmo período, para comparar o desempenho da ação com o mercado.
    """
    dias, semanal = PERIODOS.get(periodo, PERIODOS["1 Ano"])
    agora = time.time()
    intervalo = "1wk" if semanal else "1d"
    df = coleta.buscar_cotacoes_yahoo(f"{ticker}.SA", agora - (dias + 300) * 86400, agora, intervalo)
    if df.empty:
        return None
    ibov = coleta.buscar_cotacoes_yahoo("^BVSP", agora - (dias + 300) * 86400, agora, intervalo)

    variacao = df["close"].diff()
    ganho = variacao.clip(lower=0).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    perda = (-variacao.clip(upper=0)).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    df["rsi"] = (100 - 100 / (1 + ganho / perda)).where(perda > 0, 100.0).where(ganho.notna())

    curta, longa = (10, 40) if semanal else (50, 200)
    df["ma50"] = df["close"].rolling(curta).mean()
    df["ma200"] = df["close"].rolling(longa).mean()

    inicio = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=dias)
    df = df[df["date"] >= inicio].copy()
    if df.empty:
        return None
    df["dia"] = df["date"].dt.strftime("%Y-%m-%d")

    # Ibovespa alinhado pelas datas da ação e "rebaseado": começa no mesmo preço da ação,
    # então as duas linhas mostram a variação no período na mesma escala.
    df["ibov"] = np.nan
    if not ibov.empty:
        pontos = ibov.assign(dia=ibov["date"].dt.strftime("%Y-%m-%d")).drop_duplicates("dia", keep="last")
        df["ibov"] = df["dia"].map(pontos.set_index("dia")["close"])
    base = df["ibov"].dropna()
    df["ibov_base"] = df["ibov"] / base.iloc[0] * df.loc[base.index[0], "close"] if len(base) else np.nan

    colunas = ("open", "high", "low", "close", "volume", "rsi", "ma50", "ma200", "ibov", "ibov_base")
    return {"dates": df["dia"].tolist(), **{c: [_numero(v) for v in df[c]] for c in colunas}}


# Indicadores comparados com o setor e o mercado. Nos múltiplos de preço, a mediana
# usa só valores positivos (P/L negativo = prejuízo, não "ação barata").
COMPARADOS = ["pl", "pvp", "evebit", "dy", "roe", "roic", "margem", "divida_patrimonio"]
MULTIPLOS = {"pl", "pvp", "evebit"}


def _comparativo(df, item):
    """Medianas do setor e do mercado (ações com a liquidez mínima padrão)."""
    base = df[df["liquidez"] >= estrategias.FILTROS_PADRAO["liq_min"]]
    setor = base[base["setor"] == item["setor"]]

    def mediana(grupo, coluna):
        valores = grupo[coluna].dropna()
        return _numero((valores[valores > 0] if coluna in MULTIPLOS else valores).median())

    return {"empresas_setor": len(setor), "empresas_mercado": len(base),
            "indicadores": {c: {"setor": mediana(setor, c), "mercado": mediana(base, c)} for c in COMPARADOS}}


def _atualizar_grafico(ticker, periodo):
    """Busca o gráfico e guarda em memória (o banco fica só com os dados do backtesting)."""
    grafico = montar_grafico(ticker, periodo)
    if grafico:
        _cache_graficos[(ticker, periodo)] = (grafico, database.agora_b3())
    return grafico


@app.route("/api/analise")
def analise():
    ticker = request.args.get("ticker", "").upper().strip().replace(".SA", "")
    periodo = request.args.get("periodo", "1 Ano")
    periodo = periodo if periodo in PERIODOS else "1 Ano"
    if not ticker:
        return jsonify({"error": "Nenhum ativo selecionado."})

    df = dados_atuais()
    empresa = None if df is None else df[df["ticker"] == ticker]
    if empresa is None or empresa.empty:
        return jsonify({"error": f"Ativo {ticker} não encontrado na base."})
    item = empresa.iloc[0]

    fundamentos = {c: _numero(item[c]) for c in database.COLUNAS_NUMERICAS}
    fundamentos["links"] = {
        "site_ri": "https://www.google.com/search?q=" + quote_plus(f"RI Relações com Investidores {item['nome']}"),
        "relatorio_oficial": f"https://br.tradingview.com/symbols/BMFBOVESPA-{ticker}/",
    }

    # Gráfico: o já buscado responde na hora; se estiver velho, atualiza em segundo plano
    grafico, atualizado_em = _cache_graficos.get((ticker, periodo), (None, None))
    if grafico is None:
        grafico = _atualizar_grafico(ticker, periodo)
    elif _desatualizado(atualizado_em):
        _em_segundo_plano(f"grafico:{ticker}:{periodo}", _atualizar_grafico, ticker, periodo)

    # Evolução dos indicadores registrada no banco (um ponto por dia de coleta)
    historico = [{"dia": r["dia"].isoformat(), **{c: _numero(r[c]) for c in r if c != "dia"}}
                 for r in database.ler_historico_acao(ticker)]

    return jsonify({"ticker": ticker, "nome": item["nome"], "setor": item["setor"], "logo": item["logo"],
                    "excluida_greenblatt": bool(estrategias.excluida_greenblatt(empresa).iloc[0]),
                    "fundamentos": fundamentos, "chart_data": grafico,
                    "comparativo": _comparativo(df, item), "historico": historico})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
