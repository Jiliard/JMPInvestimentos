"""
Camada de persistência no PostgreSQL (Supabase).

O banco tem dois objetivos e uma tabela para cada um:

    acoes_cache       Última coleta de cada ação (1 linha por ticker). Permite que a
                      plataforma abra na hora, mesmo antes de consultar a API.
    historico_diario  Um registro por PREGÃO e ação (chave: data + ticker), com todos
                      os campos usados pelos filtros e pelos métodos. É a base do
                      backtesting: o ranking de uma data passada usa somente o que
                      estava registrado até aquele pregão.

Todos os valores gravados vêm da API (TradingView), sem estimativas.
A conexão vem exclusivamente da variável de ambiente DATABASE_URL
(no desenvolvimento local, pode ser definida em um arquivo .env).
"""

import math
import os
import threading
from contextlib import contextmanager
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2 import pool as pg_pool
from psycopg2.extras import RealDictCursor, execute_values

try:
    from dotenv import load_dotenv  # opcional, só para desenvolvimento local
    load_dotenv()
except ImportError:
    pass


URL_BANCO = os.getenv("DATABASE_URL")
URL_SERVIDOR = os.getenv("URL_SERVIDOR", "https://jmpinvestimentos.onrender.com")
FUSO_B3 = ZoneInfo("America/Sao_Paulo")

# Colunas gravadas nas duas tabelas (a ordem importa para os INSERTs).
COLUNAS_TEXTO = ["ticker", "nome", "setor", "industria"]
COLUNAS_NUMERICAS = [
    "preco", "pl", "pvp", "dy", "roic", "roe", "margem", "crescimento", "liquidez",
    "lpa", "vpa", "dpa_12m", "evebit", "patrimonio", "divida_total", "divida_patrimonio",
]
COLUNAS = COLUNAS_TEXTO + COLUNAS_NUMERICAS

# Uma coleta com menos ações que isso é tratada como parcial (falha da API) e
# não remove do cache as ações que ficaram de fora.
MINIMO_ACOES_COLETA_COMPLETA = 200

_pool = None
_pool_lock = threading.Lock()


def agora_b3():
    """Data/hora atual no fuso da B3, sem tzinfo (formato gravado no banco)."""
    return datetime.now(FUSO_B3).replace(tzinfo=None)


def pregao_de(momento):
    """
    Pregão a que os dados de uma coleta se referem. Antes das 10h a API ainda
    mostra o fechamento do dia anterior; no sábado e no domingo, o de sexta.
    (A mesma regra está em SQL na migração de inicializar_banco.)
    """
    dia = (momento - timedelta(hours=10)).date()
    return dia - timedelta(days=max(0, dia.weekday() - 4))


# =============================================================================
# CONEXÃO (pool seguro para várias threads)
# =============================================================================
def _obter_pool():
    """Cria o pool de conexões na primeira chamada e o reutiliza depois."""
    global _pool
    if _pool is None:
        with _pool_lock:
            if _pool is None:
                if not URL_BANCO:
                    raise RuntimeError("Variável de ambiente DATABASE_URL não configurada.")
                _pool = pg_pool.ThreadedConnectionPool(1, 5, URL_BANCO, connect_timeout=10)
    return _pool


@contextmanager
def _transacao(dict_cursor=False):
    """
    Entrega um cursor dentro de uma transação exclusiva da thread atual.

    Commit no sucesso, rollback em erro. Conexões quebradas (o pooler do
    Supabase encerra conexões ociosas) são descartadas em vez de voltar ao pool.
    """
    pool = _obter_pool()
    conexao = pool.getconn()
    descartar = False
    try:
        if conexao.closed:
            raise psycopg2.InterfaceError("conexão fechada")
        fabrica = RealDictCursor if dict_cursor else None
        with conexao.cursor(cursor_factory=fabrica) as cursor:
            yield cursor
        conexao.commit()
    except (psycopg2.OperationalError, psycopg2.InterfaceError):
        descartar = True
        raise
    except Exception:
        try:
            conexao.rollback()
        except Exception:
            descartar = True
        raise
    finally:
        pool.putconn(conexao, close=descartar or bool(conexao.closed))


def _executar(funcao, dict_cursor=False):
    """Executa `funcao(cursor)`; se a conexão estiver morta, tenta mais uma vez."""
    for tentativa in range(2):
        try:
            with _transacao(dict_cursor) as cursor:
                return funcao(cursor)
        except (psycopg2.OperationalError, psycopg2.InterfaceError):
            if tentativa == 1:
                raise


def _consultar(sql, parametros=None):
    """Executa um SELECT e devolve a lista de linhas como dicionários."""
    def _buscar(cursor):
        cursor.execute(sql, parametros)
        return cursor.fetchall()
    return _executar(_buscar, dict_cursor=True) or []


def _numero_ou_null(valor):
    """Converte para float; NaN, infinito e None viram NULL no banco."""
    try:
        numero = float(valor)
    except (TypeError, ValueError):
        return None
    return numero if math.isfinite(numero) else None


def _linha_para_tupla(linha):
    """Converte uma linha do DataFrame na tupla de valores esperada pelos INSERTs."""
    textos = tuple(None if linha.get(c) is None else str(linha.get(c)) for c in COLUNAS_TEXTO)
    numeros = tuple(_numero_ou_null(linha.get(c)) for c in COLUNAS_NUMERICAS)
    return textos + numeros


# =============================================================================
# CRIAÇÃO DAS TABELAS (e migração única do formato antigo)
# =============================================================================
def _sql_colunas():
    return ",\n".join(f"{c} {'VARCHAR(150)' if c in COLUNAS_TEXTO[1:] else 'REAL'}" for c in COLUNAS[1:])


def inicializar_banco():
    """
    Cria as tabelas, se não existirem, e migra o formato antigo uma única vez:
    - historico_acoes (várias linhas por dia, colunas sem uso) vira historico_diario,
      mantendo só a última coleta completa de cada pregão;
    - graficos_cache é removida (os gráficos ficam em memória no servidor).
    """
    lista = ", ".join(COLUNAS)
    # Mesma regra de pregao_de(): antes das 10h vale o dia anterior; fim de semana vale sexta
    pregao_sql = """(d - CASE EXTRACT(ISODOW FROM d) WHEN 6 THEN 1 WHEN 7 THEN 2 ELSE 0 END)"""
    sql = f"""
        CREATE TABLE IF NOT EXISTS acoes_cache (
            ticker VARCHAR(10) PRIMARY KEY,
            {_sql_colunas()},
            updated_at TIMESTAMP NOT NULL
        );
        CREATE TABLE IF NOT EXISTS historico_diario (
            data DATE NOT NULL,
            ticker VARCHAR(10) NOT NULL,
            {_sql_colunas()},
            coletado_em TIMESTAMP NOT NULL,
            PRIMARY KEY (data, ticker)
        );

        DO $$
        BEGIN
            IF to_regclass('public.historico_acoes') IS NOT NULL THEN
                INSERT INTO historico_diario (data, {lista}, coletado_em)
                SELECT DISTINCT ON (dia, ticker) dia, {lista}, data_coleta
                FROM (SELECT *, {pregao_sql} AS dia
                      FROM (SELECT *, (data_coleta - INTERVAL '10 hours')::date AS d FROM historico_acoes) a
                      WHERE industria IS NOT NULL) h
                ORDER BY dia, ticker, data_coleta DESC
                ON CONFLICT (data, ticker) DO NOTHING;
                DROP TABLE historico_acoes;
            END IF;
        END $$;
        DROP TABLE IF EXISTS graficos_cache;
    """
    try:
        _executar(lambda cursor: cursor.execute(sql))
        print("✅ [BANCO] Tabelas prontas no Supabase.")
    except Exception as erro:
        print(f"🚨 [BANCO] Erro ao inicializar tabelas: {erro}")
    agendar_rotina()


def agendar_rotina():
    """
    Rotina diária garantida, agendada DENTRO do Supabase (extensões pg_cron e pg_net).

    A cada 10 minutos o banco chama a rota /api/saude do servidor. Isso:
      - mantém o servidor do Render acordado (o plano gratuito dorme após 15 min
        sem acesso), então a plataforma responde na hora com os últimos dados;
      - durante o pregão, faz o servidor coletar na API e gravar o dia no banco.

    O agendador do banco é mais confiável que o do GitHub Actions, que atrasa
    ou pula execuções; o cron.yml continua como segunda rotina, independente.
    `cron.schedule` com o mesmo nome substitui o agendamento: pode rodar sempre.
    """
    sql = f"""
        CREATE EXTENSION IF NOT EXISTS pg_cron;
        CREATE EXTENSION IF NOT EXISTS pg_net;
        SELECT cron.schedule('jmp_rotina_diaria', '*/10 * * * *',
                             $$SELECT net.http_get('{URL_SERVIDOR}/api/saude', timeout_milliseconds := 60000)$$);
    """
    try:
        _executar(lambda cursor: cursor.execute(sql))
        print("✅ [BANCO] Rotina diária agendada no Supabase (a cada 10 min).")
    except Exception as erro:
        print(f"🚨 [BANCO] Não foi possível agendar a rotina no Supabase: {erro}")


# =============================================================================
# GRAVAÇÃO
# =============================================================================
def salvar_coleta(df):
    """
    Grava uma coleta completa da API em uma única transação:

    1. Atualiza `acoes_cache` (UPSERT por ticker) e remove ações que saíram
       da coleta (deslistadas, fracionárias antigas etc.).
    2. Registra a coleta em `historico_diario` no pregão a que ela se refere
       (UPSERT por data + ticker): fica sempre a última coleta de cada pregão.

    Retorna True se gravou com sucesso.
    """
    if df.empty:
        return False

    lista = ", ".join(COLUNAS)
    atualizacoes = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLUNAS[1:])
    sql_cache = f"""
        INSERT INTO acoes_cache ({lista}, updated_at) VALUES %s
        ON CONFLICT (ticker) DO UPDATE SET {atualizacoes}, updated_at = EXCLUDED.updated_at;
    """
    sql_remover_antigas = "DELETE FROM acoes_cache WHERE NOT (ticker = ANY(%s));"
    sql_historico = f"""
        INSERT INTO historico_diario (data, {lista}, coletado_em) VALUES %s
        ON CONFLICT (data, ticker) DO UPDATE SET {atualizacoes}, coletado_em = EXCLUDED.coletado_em;
    """

    momento = agora_b3()
    pregao = pregao_de(momento)
    linhas = [_linha_para_tupla(linha) for linha in df.to_dict(orient="records")]
    tickers = [linha[0] for linha in linhas]

    def _gravar(cursor):
        execute_values(cursor, sql_cache, [linha + (momento,) for linha in linhas])
        if len(tickers) >= MINIMO_ACOES_COLETA_COMPLETA:
            cursor.execute(sql_remover_antigas, (tickers,))
        if _sem_pregao(cursor, pregao, linhas):
            return False
        execute_values(cursor, sql_historico, [(pregao,) + linha + (momento,) for linha in linhas])
        return True

    try:
        registrou = _executar(_gravar)
        destino = f"pregão {pregao:%d/%m/%Y}" if registrou else "só no cache: preços iguais ao pregão anterior (feriado)"
        print(f"🚀 [BANCO] {len(linhas)} ações gravadas (coleta {momento:%d/%m/%Y %H:%M}, {destino}).")
        return True
    except Exception as erro:
        print(f"🚨 [BANCO] Erro ao gravar coleta: {erro}")
        return False


def _sem_pregao(cursor, pregao, linhas):
    """
    True se a coleta repete os preços do último pregão registrado: em feriado de dia
    útil a API continua mostrando o fechamento anterior, e esse "dia" não pode entrar
    no histórico com outra data. Num pregão normal, a maioria das cotações muda.
    """
    cursor.execute("""
        SELECT ticker, preco FROM historico_diario
        WHERE data = (SELECT MAX(data) FROM historico_diario WHERE data < %s);
    """, (pregao,))
    anteriores = dict(cursor.fetchall())
    precos = {linha[0]: linha[COLUNAS.index("preco")] for linha in linhas}
    comuns = [t for t in precos if anteriores.get(t) and precos[t]]
    if len(comuns) < MINIMO_ACOES_COLETA_COMPLETA:
        return False
    iguais = sum(abs(precos[t] / anteriores[t] - 1) < 1e-4 for t in comuns)
    return iguais >= 0.9 * len(comuns)


# =============================================================================
# LEITURA
# =============================================================================
def ler_cache():
    """Lê a última coleta de todas as ações (primeira carga da plataforma)."""
    try:
        return _consultar("SELECT * FROM acoes_cache;")
    except Exception as erro:
        print(f"🚨 [BANCO] Erro ao ler cache: {erro}")
        return []


def ler_historico(data_ref):
    """
    Lê o registro do último PREGÃO até `data_ref` (datetime.date).

    Usar apenas um pregão evita dois erros comuns de backtesting:
      - olhar para o futuro (nenhum dado posterior a `data_ref` é usado);
      - misturar dias diferentes (uma ação deslistada meses antes não
        "reaparece" com dados velhos).

    Retorna (registros, pregao_utilizado) ou ([], None) se não houver registro.
    """
    sql = f"""
        SELECT {", ".join(COLUNAS)}, data
        FROM historico_diario
        WHERE data = (SELECT MAX(data) FROM historico_diario WHERE data <= %s);
    """
    try:
        registros = _consultar(sql, (data_ref,))
    except Exception as erro:
        print(f"🚨 [BANCO] Erro ao ler histórico: {erro}")
        return [], None
    if not registros:
        return [], None
    return registros, registros[0]["data"]


def ler_historico_acao(ticker):
    """Evolução diária dos indicadores de uma ação (um ponto por pregão registrado)."""
    sql = """
        SELECT data AS dia, preco, pl, pvp, dy, roe, roic, margem
        FROM historico_diario WHERE ticker = %s ORDER BY data;
    """
    try:
        return _consultar(sql, (ticker,))
    except Exception as erro:
        print(f"🚨 [BANCO] Erro ao ler histórico de {ticker}: {erro}")
        return []


def periodo_historico():
    """Retorna (primeiro_pregao, ultimo_pregao) registrados, ou (None, None)."""
    try:
        linha = _consultar("SELECT MIN(data) AS inicio, MAX(data) AS fim FROM historico_diario;")
    except Exception as erro:
        print(f"🚨 [BANCO] Erro ao ler período do histórico: {erro}")
        return None, None
    return (linha[0]["inicio"], linha[0]["fim"]) if linha else (None, None)
