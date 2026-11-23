"""
Servidor Flask do JMP Investimentos.

Os dados das ações ficam em memória. Ao subir, o servidor busca no TradingView e no
banco (Supabase) ao mesmo tempo e usa o que chegar primeiro. Dados com mais de
5 minutos são atualizados em segundo plano, e cada coleta é gravada no banco
(última coleta para a primeira carga e registro diário por pregão para o backtesting).

Rotas:
    GET /api/rankings           ranking por método e filtros (?data=AAAA-MM-DD usa o histórico)
    GET /api/historico/periodo  primeiro e último pregão registrados
    GET /api/tickers            lista de tickers para a busca
    GET /api/analise            ficha da empresa, gráfico de preços e comparação com o setor
    GET /api/saude              chamada pelas rotinas agendadas; mantém o servidor ativo
    GET /api/coletar            coleta e grava agora (rotina cron.yml)
"""

import logging
import threading
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote_plus

import numpy as np
import pandas as pd
from flask import Flask, abort, jsonify, request, send_from_directory
from flask_cors import CORS

import coleta
import database
import estrategias
import rotina_diaria
from database import numero_ou_none

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
log = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app, expose_headers=["X-Data-Base"])   # o front-end na Vercel lê a data dos dados neste cabeçalho

IDADE_MAXIMA = timedelta(minutes=5)          # dados mais velhos são atualizados em segundo plano
INICIO_LPA_MEDIO = datetime(2026, 11, 16)    # primeiro pregão gravado com o LPA médio de 3 anos (Graham)
ARQUIVOS_PUBLICOS = {"index.html", "script.js", "style.css", "favicon.svg"}
URL_LOGOS = "https://raw.githubusercontent.com/thefintz/icones-b3/main/icones/"

# Estado em memória (compartilhado entre as threads do servidor)
_cache = {
    "df": None,                  # última coleta das ações
    "gravou_no_banco": False,    # a última coleta foi gravada com sucesso
    "complementos_em": None,     # dia em que o Ibovespa e os eventos foram gravados
}
_cache_historico = {}            # data de referência -> (DataFrame, pregão usado)
_cache_liquidez = {}             # ano -> (registros do critério do NEFIN, momento da leitura)
_cache_graficos = {}             # (ticker, período) -> (gráfico, momento da busca)
_tarefas = set()                 # tarefas em segundo plano em andamento (evita duplicadas)
_lock_tarefas = threading.Lock()
_lock_primeira_carga = threading.Lock()
_lock_coleta = threading.Lock()


# =============================================================================
# AUXILIARES
# =============================================================================
def _para_json(df):
    df = df.drop(columns=["updated_at", "data"], errors="ignore").replace([np.inf, -np.inf], np.nan)
    return df.astype(object).where(df.notna(), None).to_dict(orient="records")


def _montar_df(registros):
    """DataFrame padronizado a partir do banco (lista de registros) ou da coleta (DataFrame)."""
    df = pd.DataFrame(registros, copy=True)   # cópia: não altera o DataFrame que vai para o banco
    for coluna in database.COLUNAS_NUMERICAS:
        df[coluna] = pd.to_numeric(df[coluna], errors="coerce") if coluna in df else np.nan
    df["ebit_ev"] = estrategias.rendimento_operacional(df)   # medida de preço de Greenblatt
    df["logo"] = URL_LOGOS + df["ticker"].str[:4] + ".png"
    return df


def _ler_filtros(args):
    """Parâmetro ausente usa o padrão (liquidez de R$ 500 mil); parâmetro vazio desliga o filtro."""
    return {chave: (padrao if chave not in args else numero_ou_none(args.get(chave)))
            for chave, padrao in estrategias.FILTROS_PADRAO.items()}


def _liquidez(ano):
    """
    Critério de liquidez do NEFIN gravado para `ano` (relido a cada hora: a rotina completa as faltas).
    Leitura vazia não fica guardada: o cálculo pode terminar logo depois e deve aparecer na hora.
    """
    registros, lido_em = _cache_liquidez.get(ano, (None, None))
    if not registros or database.agora_b3() - lido_em > timedelta(hours=1):
        registros = database.ler_liquidez_anual(ano)
        _cache_liquidez[ano] = (registros, database.agora_b3())
    return registros


def _com_liquidez(df, data_ref):
    """Anexa o critério de liquidez do ano anterior a `data_ref`. (DataFrame, True se havia o critério)."""
    registros = _liquidez(data_ref.year - 1)
    return estrategias.anexar_liquidez(df, registros), bool(registros)


def _desatualizado(momento):
    """True se o dado não tem data, é velho ou está no futuro (gravado em outro fuso)."""
    if momento is None or pd.isna(momento):
        return True
    return abs(database.agora_b3() - momento) > IDADE_MAXIMA


def _em_pregao(momento):
    """Dia útil entre 10h e 19h (o pregão fecha às 17h ou 18h e a API tem 15 min de atraso)."""
    return momento.weekday() < 5 and 10 <= momento.hour < 19


def _em_segundo_plano(chave, funcao, *args):
    """Executa `funcao` em outra thread, no máximo uma vez por chave ao mesmo tempo."""
    with _lock_tarefas:
        if chave in _tarefas:
            return
        _tarefas.add(chave)

    def tarefa():
        try:
            funcao(*args)
        except Exception:
            log.exception(f"Falha na tarefa em segundo plano {chave}")
        finally:
            _tarefas.discard(chave)

    threading.Thread(target=tarefa, daemon=True).start()


def _erro(mensagem, status):
    return jsonify({"erro": mensagem}), status


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
    _cache["df"] = df   # memória primeiro: quem está esperando recebe na hora
    _cache["gravou_no_banco"] = database.salvar_coleta(bruto)
    return df


def _carregar_do_banco():
    """Usa a última coleta gravada, se a memória ainda estiver vazia."""
    registros = database.ler_cache()
    if registros and _cache["df"] is None:
        _cache["df"] = _montar_df(registros)


def _primeira_carga(limite_segundos=60):
    """
    Servidor recém-iniciado: busca no TradingView e no banco ao mesmo tempo e responde
    com o primeiro que chegar. O banco também serve de reserva se o TradingView falhar.
    """
    pronto = threading.Event()
    terminadas = []
    with _lock_tarefas:
        _tarefas.add("acoes")    # impede que outra coleta comece enquanto esta roda

    def executar(funcao):
        try:
            funcao()
        except Exception:
            log.exception(f"Falha na primeira carga ({funcao.__name__})")
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
        with _lock_primeira_carga:          # vários acessos simultâneos disparam uma só carga
            if _cache["df"] is None:
                _primeira_carga()

    df = _cache["df"]
    if df is not None and _desatualizado(df["updated_at"].max()):
        _em_segundo_plano("acoes", _coletar_acoes)
    return df


def atualizar_agora(intervalo_minimo):
    """
    Coleta imediata (botão de atualizar e rotina cron.yml). Reaproveita a última coleta
    se ela foi gravada no banco há menos de `intervalo_minimo`. None se a API não respondeu.
    """
    with _lock_coleta:
        df = _cache["df"]
        if (df is not None and _cache["gravou_no_banco"]
                and database.agora_b3() - df["updated_at"].max() < intervalo_minimo):
            return df
        return _coletar_acoes()


def dados_historicos(data_ref):
    """(DataFrame, pregão usado) do último pregão registrado até `data_ref`."""
    if data_ref not in _cache_historico:
        registros, pregao = database.ler_historico(data_ref)
        resultado = (_montar_df(registros) if registros else pd.DataFrame(), pregao)
        if pregao is None or data_ref >= database.agora_b3().date():
            return resultado             # o dia de hoje ainda pode mudar: não guarda
        _cache_historico[data_ref] = resultado
    return _cache_historico[data_ref]


def _registrar_complementos(dia):
    if rotina_diaria.registrar_complementos():
        _cache["complementos_em"] = dia


def _ao_iniciar():
    """Prepara o banco e carrega os dados fora da importação do módulo
    (feito na importação, travava o gunicorn além do limite de 30 s)."""
    database.inicializar_banco()
    dados_atuais()
    _em_segundo_plano("liquidez", rotina_diaria.registrar_liquidez_anual)


threading.Thread(target=_ao_iniciar, daemon=True).start()


# =============================================================================
# PÁGINAS
# =============================================================================
@app.route("/")
def home():
    return send_from_directory(".", "index.html")


@app.route("/<path:arquivo>")
def arquivos(arquivo):
    if arquivo not in ARQUIVOS_PUBLICOS:   # nunca expõe código, .env ou .git
        abort(404)
    return send_from_directory(".", arquivo)


# =============================================================================
# API: RANKINGS E ROTINAS
# =============================================================================
@app.route("/api/rankings")
def rankings():
    metodo = request.args.get("metodo", "graham")
    if metodo not in estrategias.METODOS:
        return _erro(f"Método '{metodo}' desconhecido.", 400)

    data_texto = (request.args.get("data") or "").strip()
    if data_texto:   # backtesting: só os dados registrados até a data
        try:
            data_ref = datetime.strptime(data_texto, "%Y-%m-%d").date()
        except ValueError:
            return _erro("Data inválida. Use o formato AAAA-MM-DD.", 400)
        if data_ref > database.agora_b3().date():
            return _erro("A data de referência não pode estar no futuro.", 400)
        df, pregao = dados_historicos(data_ref)
        if pregao is None:
            inicio, _ = database.periodo_historico()
            if inicio:
                return _erro(f"Não há pregão registrado até {data_ref:%d/%m/%Y}. O histórico começa em {inicio:%d/%m/%Y}.", 404)
            return _erro("O histórico ainda está vazio.", 404)
        if metodo == "graham" and df["lpa_medio_3a"].isna().all():
            return _erro(f"O pregão de {pregao:%d/%m/%Y} não tem o lucro médio de três anos usado por Graham, "
                         f"registrado a partir de {INICIO_LPA_MEDIO:%d/%m/%Y}. Escolha uma data posterior ou outra estratégia.", 404)
        data_base = pregao.isoformat()
        dia = pregao
    else:
        if request.args.get("atualizar") == "1":
            atualizar_agora(timedelta(minutes=1))
        df = dados_atuais()
        if df is None or df.empty:
            return jsonify([])
        data_base = f"{df['updated_at'].max():%Y-%m-%dT%H:%M}"
        dia = df["updated_at"].max().date()

    filtros = _ler_filtros(request.args)
    df, tem_liquidez = _com_liquidez(df, dia)
    if filtros["liq_min"] is not None and not tem_liquidez:
        return _erro(f"O critério de liquidez de {dia.year - 1} ainda está sendo calculado. "
                     "Tente de novo em alguns minutos ou deixe a liquidez mínima vazia.", 503)
    resposta = jsonify(_para_json(estrategias.ranquear(df, metodo, filtros)))
    resposta.headers["X-Data-Base"] = data_base
    return resposta


@app.route("/api/historico/periodo")
def historico_periodo():
    inicio, fim = database.periodo_historico()
    return jsonify({"inicio": inicio and inicio.isoformat(), "fim": fim and fim.isoformat()})


@app.route("/api/tickers")
def tickers():
    df = dados_atuais()
    return jsonify([] if df is None else sorted(df["ticker"] + ".SA"))


@app.route("/api/saude")
def saude():
    """
    Chamada a cada 10 minutos pela rotina do Supabase (pg_cron) e pelo manter_acordado.yml,
    o que impede o servidor gratuito de dormir. No pregão, coleta se os dados tiverem mais
    de 20 minutos; depois das 19h dos dias úteis, grava uma vez o Ibovespa e os eventos.
    """
    df = _cache["df"]
    agora = database.agora_b3()
    if _em_pregao(agora) and (df is None or agora - df["updated_at"].max() > timedelta(minutes=20)):
        _em_segundo_plano("acoes", _coletar_acoes)
    if agora.weekday() < 5 and agora.hour >= 19 and _cache["complementos_em"] != agora.date():
        _em_segundo_plano("complementos", _registrar_complementos, agora.date())
    return jsonify({"status": "ok", "dados_em_memoria": df is not None})


@app.route("/api/coletar")
def coletar():
    """
    Coleta e grava agora, respondendo se deu certo (usada pela rotina cron.yml).
    Se a última coleta tem menos de 5 minutos, apenas confirma.
    """
    df = atualizar_agora(IDADE_MAXIMA)
    if df is None:
        return _erro("A API do TradingView não retornou dados.", 503)
    if not _cache["gravou_no_banco"]:
        return _erro("Falha ao gravar no banco.", 503)
    momento = df["updated_at"].max()
    return jsonify({"status": "ok", "acoes": len(df), "coleta": f"{momento:%d/%m/%Y %H:%M}",
                    "pregao": f"{database.pregao_de(momento):%d/%m/%Y}"})


# =============================================================================
# API: ANÁLISE DETALHADA
# =============================================================================
# Período -> (dias exibidos, gráfico semanal?)
PERIODOS = {"30 Dias": (31, False), "6 Meses": (183, False), "1 Ano": (366, False),
            "5 Anos": (1827, True), "10 Anos": (3653, True)}
DIAS_AQUECIMENTO = 300   # dias extras para as médias e o IFR já começarem calculados

# Indicadores comparados com o setor e com a B3. Nas medidas de preço, a mediana usa
# só valores positivos (P/L ou EBIT/EV negativo indica prejuízo, não ação barata).
COMPARADOS = ["pl", "pvp", "ebit_ev", "dy", "roe", "roic", "margem", "divida_patrimonio"]
MULTIPLOS = {"pl", "pvp", "ebit_ev"}


def _montar_grafico(ticker, periodo):
    """
    Cotações do Yahoo Finance com IFR(14) de Wilder e médias móveis curta e longa
    (50 e 200 pregões no diário; 10 e 40 semanas no semanal), mais o Ibovespa do período.
    """
    dias, semanal = PERIODOS[periodo]
    intervalo = "1wk" if semanal else "1d"
    fim = time.time()
    inicio = fim - (dias + DIAS_AQUECIMENTO) * 86_400
    df = coleta.buscar_cotacoes_yahoo(f"{ticker}.SA", inicio, fim, intervalo)
    if df.empty:
        return None
    ibov = coleta.buscar_cotacoes_yahoo("^BVSP", inicio, fim, intervalo)

    variacao = df["close"].diff()
    ganho = variacao.clip(lower=0).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    perda = (-variacao.clip(upper=0)).ewm(alpha=1 / 14, min_periods=14, adjust=False).mean()
    df["ifr"] = (100 - 100 / (1 + ganho / perda)).where(perda > 0, 100.0).where(ganho.notna())

    curta, longa = (10, 40) if semanal else (50, 200)
    df["media_curta"] = df["close"].rolling(curta).mean()
    df["media_longa"] = df["close"].rolling(longa).mean()

    df = df[df["date"] >= datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(days=dias)].copy()
    if df.empty:
        return None
    df["dia"] = df["date"].dt.strftime("%Y-%m-%d")

    # Ibovespa alinhado pelas datas da ação e rebaseado para começar no mesmo preço dela,
    # para que as duas linhas mostrem a variação no período na mesma escala.
    df["ibov"] = np.nan
    if not ibov.empty:
        pontos = ibov.assign(dia=ibov["date"].dt.strftime("%Y-%m-%d")).drop_duplicates("dia", keep="last")
        df["ibov"] = df["dia"].map(pontos.set_index("dia")["close"])
    base = df["ibov"].dropna()
    df["ibov_base"] = df["ibov"] / base.iloc[0] * df.loc[base.index[0], "close"] if len(base) else np.nan

    colunas = ("open", "high", "low", "close", "volume", "ifr", "media_curta", "media_longa", "ibov", "ibov_base")
    return {"datas": df["dia"].tolist(), **{c: [numero_ou_none(v) for v in df[c]] for c in colunas}}


def _atualizar_grafico(ticker, periodo):
    """Busca o gráfico e guarda em memória (o banco guarda só os dados do backtesting)."""
    grafico = _montar_grafico(ticker, periodo)
    if grafico:
        _cache_graficos[(ticker, periodo)] = (grafico, database.agora_b3())
    return grafico


def _comparativo(df, item):
    """Medianas do setor e da B3, entre as ações que atendem à liquidez mínima padrão."""
    df, tem_liquidez = _com_liquidez(df, df["updated_at"].max().date())
    base = estrategias.aplicar_filtros(df, estrategias.FILTROS_PADRAO) if tem_liquidez else df
    setor = base[base["setor"] == item["setor"]]

    def mediana(grupo, coluna):
        valores = grupo[coluna].dropna()
        return numero_ou_none((valores[valores > 0] if coluna in MULTIPLOS else valores).median())

    return {"empresas_setor": len(setor), "empresas_mercado": len(base),
            "indicadores": {c: {"setor": mediana(setor, c), "mercado": mediana(base, c)} for c in COMPARADOS}}


@app.route("/api/analise")
def analise():
    ticker = request.args.get("ticker", "").upper().strip().replace(".SA", "")
    periodo = request.args.get("periodo", "1 Ano")
    if periodo not in PERIODOS:
        periodo = "1 Ano"
    if not ticker:
        return _erro("Nenhum ativo selecionado.", 400)

    df = dados_atuais()
    empresa = None if df is None else df[df["ticker"] == ticker]
    if empresa is None or empresa.empty:
        return _erro(f"Ativo {ticker} não encontrado na base.", 404)
    item = empresa.iloc[0]

    fundamentos = {c: numero_ou_none(item[c]) for c in database.COLUNAS_NUMERICAS + ["ebit_ev"]}
    fundamentos["links"] = {
        "site_ri": "https://www.google.com/search?q=" + quote_plus(f"RI Relações com Investidores {item['nome']}"),
        "tradingview": f"https://br.tradingview.com/symbols/BMFBOVESPA-{ticker}/",
    }

    # O gráfico já buscado responde na hora; se estiver velho, é atualizado em segundo plano
    grafico, buscado_em = _cache_graficos.get((ticker, periodo), (None, None))
    if grafico is None:
        grafico = _atualizar_grafico(ticker, periodo)
    elif _desatualizado(buscado_em):
        _em_segundo_plano(f"grafico:{ticker}:{periodo}", _atualizar_grafico, ticker, periodo)

    # Evolução dos indicadores registrada no banco (um ponto por pregão)
    historico = [{"dia": r["dia"].isoformat(), **{c: numero_ou_none(r[c]) for c in r if c != "dia"}}
                 for r in database.ler_historico_acao(ticker)]

    return jsonify({"ticker": ticker, "nome": item["nome"], "setor": item["setor"], "logo": item["logo"],
                    "excluida_greenblatt": bool(estrategias.excluida_greenblatt(empresa).iloc[0]),
                    "fundamentos": fundamentos, "grafico": grafico,
                    "comparativo": _comparativo(df, item), "historico": historico})


if __name__ == "__main__":
    app.run(debug=True, port=5000)
