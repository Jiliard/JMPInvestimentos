"""
Camada de persistência no PostgreSQL (Supabase).

Tabelas:
    acoes_cache      Último snapshot de cada ação (1 linha por ticker). É o que a
                     plataforma exibe no dia a dia.
    historico_acoes  Um snapshot por dia de coleta (o último do dia), usado para
                     consultas retroativas e para o backtesting da monografia.
    graficos_cache   Último gráfico consultado de cada ação e período, para a
                     tela de análise abrir na hora.

A conexão vem exclusivamente da variável de ambiente DATABASE_URL
(no desenvolvimento local, pode ser definida em um arquivo .env).
"""

import math
import os
import threading
from contextlib import contextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

import psycopg2
from psycopg2 import pool as pg_pool
from psycopg2.extras import Json, RealDictCursor, execute_values

try:
    from dotenv import load_dotenv  # opcional, só para desenvolvimento local
    load_dotenv()
except ImportError:
    pass


URL_BANCO = os.getenv("DATABASE_URL")
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
# CRIAÇÃO E MIGRAÇÃO DAS TABELAS
# =============================================================================
def inicializar_banco():
    """
    Cria as tabelas e migra versões antigas para o schema atual.

    `CREATE TABLE IF NOT EXISTS` não altera tabelas já existentes, por isso
    cada coluna também é adicionada com `ADD COLUMN IF NOT EXISTS`.
    """
    def _definicao(coluna):
        return f"{coluna} VARCHAR(150)" if coluna in COLUNAS_TEXTO else f"{coluna} REAL"

    adicionar = "\n".join(
        f"ALTER TABLE {tabela} ADD COLUMN IF NOT EXISTS {_definicao(c)};"
        for tabela in ("acoes_cache", "historico_acoes")
        for c in COLUNAS[1:]
    )

    sql = f"""
        CREATE TABLE IF NOT EXISTS acoes_cache (
            ticker VARCHAR(10) PRIMARY KEY,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS historico_acoes (
            id SERIAL PRIMARY KEY,
            ticker VARCHAR(10) NOT NULL,
            data_coleta TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        CREATE TABLE IF NOT EXISTS graficos_cache (
            ticker VARCHAR(10) NOT NULL,
            periodo VARCHAR(20) NOT NULL,
            dados JSONB NOT NULL,
            updated_at TIMESTAMP NOT NULL,
            PRIMARY KEY (ticker, periodo)
        );
        ALTER TABLE acoes_cache ADD COLUMN IF NOT EXISTS updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP;
        ALTER TABLE historico_acoes ADD COLUMN IF NOT EXISTS data_coleta TIMESTAMP;
        {adicionar}

        -- Migra dados do schema antigo (data_registro / nome_empresa), se existir
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name = 'historico_acoes' AND column_name = 'data_registro') THEN
                UPDATE historico_acoes SET data_coleta = data_registro WHERE data_coleta IS NULL;
            END IF;
            IF EXISTS (SELECT 1 FROM information_schema.columns
                       WHERE table_name = 'historico_acoes' AND column_name = 'nome_empresa') THEN
                UPDATE historico_acoes SET nome = nome_empresa WHERE nome IS NULL;
            END IF;
        END $$;

        ALTER TABLE historico_acoes ALTER COLUMN data_coleta SET DEFAULT CURRENT_TIMESTAMP;
        CREATE INDEX IF NOT EXISTS idx_historico_data ON historico_acoes (data_coleta DESC);
        CREATE INDEX IF NOT EXISTS idx_historico_ticker_data ON historico_acoes (ticker, data_coleta DESC);
    """
    try:
        _executar(lambda cursor: cursor.execute(sql))
        print("✅ [BANCO] Tabelas prontas no Supabase.")
    except Exception as erro:
        print(f"🚨 [BANCO] Erro ao inicializar tabelas: {erro}")


# =============================================================================
# GRAVAÇÃO
# =============================================================================
def salvar_coleta(df):
    """
    Grava uma coleta completa do TradingView em uma única transação:

    1. Atualiza `acoes_cache` (UPSERT por ticker) e remove ações que saíram
       da coleta (deslistadas, fracionárias antigas etc.).
    2. Substitui o snapshot do dia em `historico_acoes`, mantendo apenas o
       último do dia (a tabela cresce ~1 coleta por dia, não por atualização).

    Retorna True se gravou com sucesso.
    """
    if df.empty:
        return False

    lista_colunas = ", ".join(COLUNAS)
    atualizacoes = ", ".join(f"{c} = EXCLUDED.{c}" for c in COLUNAS[1:])
    sql_cache = f"""
        INSERT INTO acoes_cache ({lista_colunas}, updated_at) VALUES %s
        ON CONFLICT (ticker) DO UPDATE SET {atualizacoes}, updated_at = EXCLUDED.updated_at;
    """
    sql_remover_antigas = "DELETE FROM acoes_cache WHERE NOT (ticker = ANY(%s));"
    sql_limpar_dia = """
        DELETE FROM historico_acoes
        WHERE data_coleta >= %s::date AND data_coleta < (%s::date + 1);
    """
    sql_historico = f"INSERT INTO historico_acoes ({lista_colunas}, data_coleta) VALUES %s;"

    momento = agora_b3()
    linhas = [_linha_para_tupla(linha) + (momento,) for linha in df.to_dict(orient="records")]
    tickers = [linha[0] for linha in linhas]

    def _gravar(cursor):
        execute_values(cursor, sql_cache, linhas)
        if len(tickers) >= MINIMO_ACOES_COLETA_COMPLETA:
            cursor.execute(sql_remover_antigas, (tickers,))
        cursor.execute(sql_limpar_dia, (momento.date(), momento.date()))
        execute_values(cursor, sql_historico, linhas)

    try:
        _executar(_gravar)
        print(f"🚀 [BANCO] {len(linhas)} ações gravadas ({momento:%d/%m/%Y %H:%M}).")
        return True
    except Exception as erro:
        print(f"🚨 [BANCO] Erro ao gravar coleta: {erro}")
        return False


# =============================================================================
# LEITURA
# =============================================================================
def ler_cache():
    """Lê o último snapshot de todas as ações (tabela acoes_cache)."""
    try:
        return _consultar("SELECT * FROM acoes_cache;")
    except Exception as erro:
        print(f"🚨 [BANCO] Erro ao ler cache: {erro}")
        return []


def ler_historico(data_ref):
    """
    Lê o snapshot do último DIA DE COLETA até `data_ref` (datetime.date).

    Usar apenas um único dia evita dois erros comuns de backtesting:
      - olhar para o futuro (nenhum dado posterior a `data_ref` é usado);
      - misturar dias diferentes (uma ação deslistada meses antes não
        "reaparece" com dados velhos).

    Retorna (registros, dia_utilizado) ou ([], None) se não houver coleta.
    """
    # Só considera coletas completas (industria preenchida); registros do formato
    # antigo não têm LPA, VPA, setor nem dívida e não servem para os métodos.
    sql = f"""
        WITH ultimo_dia AS (
            SELECT MAX(data_coleta)::date AS dia
            FROM historico_acoes
            WHERE data_coleta < (%s::date + 1) AND industria IS NOT NULL
        )
        SELECT DISTINCT ON (h.ticker) {", ".join("h." + c for c in COLUNAS)}, h.data_coleta
        FROM historico_acoes h, ultimo_dia u
        WHERE h.data_coleta >= u.dia AND h.data_coleta < (u.dia + 1) AND h.industria IS NOT NULL
        ORDER BY h.ticker, h.data_coleta DESC;
    """
    try:
        registros = _consultar(sql, (data_ref,))
    except Exception as erro:
        print(f"🚨 [BANCO] Erro ao ler histórico: {erro}")
        return [], None
    if not registros:
        return [], None
    return registros, registros[0]["data_coleta"].date()


def periodo_historico():
    """Retorna (primeiro_dia, ultimo_dia) com coletas completas no histórico, ou (None, None)."""
    try:
        linha = _consultar(
            "SELECT MIN(data_coleta)::date AS inicio, MAX(data_coleta)::date AS fim "
            "FROM historico_acoes WHERE industria IS NOT NULL;"
        )
    except Exception as erro:
        print(f"🚨 [BANCO] Erro ao ler período do histórico: {erro}")
        return None, None
    return (linha[0]["inicio"], linha[0]["fim"]) if linha else (None, None)


def ler_grafico(ticker, periodo):
    """Último gráfico salvo para a ação e o período: (dados, updated_at) ou (None, None)."""
    try:
        linha = _consultar(
            "SELECT dados, updated_at FROM graficos_cache WHERE ticker = %s AND periodo = %s;",
            (ticker, periodo),
        )
    except Exception as erro:
        print(f"🚨 [BANCO] Erro ao ler gráfico: {erro}")
        return None, None
    return (linha[0]["dados"], linha[0]["updated_at"]) if linha else (None, None)


def salvar_grafico(ticker, periodo, dados):
    """Grava (ou substitui) o gráfico consultado."""
    sql = """
        INSERT INTO graficos_cache (ticker, periodo, dados, updated_at) VALUES (%s, %s, %s, %s)
        ON CONFLICT (ticker, periodo) DO UPDATE SET dados = EXCLUDED.dados, updated_at = EXCLUDED.updated_at;
    """
    try:
        _executar(lambda cursor: cursor.execute(sql, (ticker, periodo, Json(dados), agora_b3())))
    except Exception as erro:
        print(f"🚨 [BANCO] Erro ao gravar gráfico: {erro}")
