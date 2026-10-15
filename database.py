import os
from datetime import datetime
import psycopg2
from psycopg2.extras import RealDictCursor, execute_values

# URL Padrão e de Ambiente para Conexão com o Supabase
URL_PADRAO = "postgresql://postgres:HB6C8pfwOpotari7@db.mhyeccidsziqeqvsmryj.supabase.co:5432/postgres"
URL_BANCO_NUVEM = os.getenv("DATABASE_URL", URL_PADRAO)

# Conexão Global Persistente em Memória RAM
_CONEXAO_PERSISTENTE = None


# =============================================================================
# HELPER FUNCTIONS & GERENCIAMENTO DE CONEXÃO
# =============================================================================
def obter_conexao():
    """Reutiliza a conexão com o PostgreSQL para evitar o overhead de SSL/TCP handshake."""
    global _CONEXAO_PERSISTENTE
    try:
        if _CONEXAO_PERSISTENTE is None or _CONEXAO_PERSISTENTE.closed != 0:
            _CONEXAO_PERSISTENTE = psycopg2.connect(URL_BANCO_NUVEM, connect_timeout=5)
        return _CONEXAO_PERSISTENTE
    except Exception as e:
        print(f"🚨 [ERRO CONEXÃO POSTGRES]: {e}")
        return psycopg2.connect(URL_BANCO_NUVEM, connect_timeout=5)


def _extrair_campos_df(row):
    """Extrai e sanitiza os campos de uma linha do DataFrame para inserção no banco."""
    return (
        str(row.get('ticker', '')),
        str(row.get('nome', '')),
        float(row.get('preco', 0)),
        float(row.get('pl', 0)),
        float(row.get('pvp', 0)),
        float(row.get('dy', 0)),
        float(row.get('roic', 0)),
        float(row.get('roe', 0)),
        float(row.get('margem', 0)),
        float(row.get('crescimento', 0)),
        float(row.get('liquidez', 0)),
        float(row.get('lpa', 0)),
        float(row.get('vpa', 0)),
        float(row.get('dpa_12m', 0)),
        float(row.get('evebit', 0)),
        float(row.get('patrimonio', 0)),
        float(row.get('divida_total', 0)),
    )


# =============================================================================
# INICIALIZAÇÃO DE TABELAS
# =============================================================================
def inicializar_banco():
    """Garante a criação das tabelas 'historico_acoes' e 'acoes_cache' no Supabase."""
    query_historico = '''
        CREATE TABLE IF NOT EXISTS historico_acoes (
            id SERIAL PRIMARY KEY,
            data_registro TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            ticker VARCHAR(10) NOT NULL,
            nome_empresa VARCHAR(150),
            preco REAL,
            pl REAL,
            pvp REAL,
            dy REAL,
            roic REAL,
            roe REAL,
            margem REAL,
            crescimento REAL,
            liquidez REAL,
            metodo_ranking VARCHAR(50),
            posicao_ranking INTEGER
        );
    '''

    query_cache = '''
        CREATE TABLE IF NOT EXISTS acoes_cache (
            ticker VARCHAR(10) PRIMARY KEY,
            nome VARCHAR(150),
            preco REAL,
            pl REAL,
            pvp REAL,
            dy REAL,
            roic REAL,
            roe REAL,
            margem REAL,
            crescimento REAL,
            liquidez REAL,
            lpa REAL,
            vpa REAL,
            dpa_12m REAL,
            evebit REAL,
            patrimonio REAL,
            divida_total REAL,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
    '''

    try:
        conexao = obter_conexao()
        with conexao.cursor() as cursor:
            cursor.execute(query_historico)
            cursor.execute(query_cache)
        conexao.commit()
        print("✅ [POSTGRES] Tabelas 'historico_acoes' e 'acoes_cache' prontas no Supabase!")
    except Exception as e:
        print(f"🚨 [ERRO INICIALIZAR BANCO]: {e}")


# =============================================================================
# GRAVAÇÃO E PERSISTÊNCIA DE DADOS
# =============================================================================
def salvar_historico_ranking(df_ranqueado, metodo):
    """Insere o snapshot do ranking atual na tabela de histórico."""
    if df_ranqueado.empty:
        return False
        
    query = '''
        INSERT INTO historico_acoes (
            data_registro, ticker, nome_empresa, preco, pl, pvp, 
            dy, roic, roe, margem, crescimento, liquidez, 
            metodo_ranking, posicao_ranking
        ) VALUES %s
    '''
    
    try:
        conexao = obter_conexao()
        data_hoje = datetime.now()
        
        dados_lote = []
        for _, row in df_ranqueado.iterrows():
            dados_lote.append((
                data_hoje,
                str(row.get('ticker', '')),
                str(row.get('nome', '')),
                float(row.get('preco', 0)),
                float(row.get('pl', 0)),
                float(row.get('pvp', 0)),
                float(row.get('dy', 0)),
                float(row.get('roic', 0)),
                float(row.get('roe', 0)),
                float(row.get('margem', 0)),
                float(row.get('crescimento', 0)),
                float(row.get('liquidez', 0)),
                metodo,
                int(row.get('rank', 0))
            ))

        with conexao.cursor() as cursor:
            execute_values(cursor, query, dados_lote)
        conexao.commit()
        
        print(f"💾 [NUVEM] {len(df_ranqueado)} registros salvos no histórico ('{metodo}').")
        return True
    except Exception as e:
        print(f"🚨 [ERRO SALVAR HISTÓRICO]: {e}")
        return False


def salvar_cache_acoes(df_base):
    """Atualiza o cache geral de ações no Supabase utilizando UPSERT (ON CONFLICT)."""
    if df_base.empty:
        return False

    query = '''
        INSERT INTO acoes_cache (
            ticker, nome, preco, pl, pvp, dy, roic, roe, margem, 
            crescimento, liquidez, lpa, vpa, dpa_12m, evebit, patrimonio, divida_total, updated_at
        ) VALUES %s
        ON CONFLICT (ticker) DO UPDATE SET
            nome = EXCLUDED.nome,
            preco = EXCLUDED.preco,
            pl = EXCLUDED.pl,
            pvp = EXCLUDED.pvp,
            dy = EXCLUDED.dy,
            roic = EXCLUDED.roic,
            roe = EXCLUDED.roe,
            margem = EXCLUDED.margem,
            crescimento = EXCLUDED.crescimento,
            liquidez = EXCLUDED.liquidez,
            lpa = EXCLUDED.lpa,
            vpa = EXCLUDED.vpa,
            dpa_12m = EXCLUDED.dpa_12m,
            evebit = EXCLUDED.evebit,
            patrimonio = EXCLUDED.patrimonio,
            divida_total = EXCLUDED.divida_total,
            updated_at = EXCLUDED.updated_at;
    '''

    try:
        conexao = obter_conexao()
        data_hoje = datetime.now()

        dados_lote = [
            _extrair_campos_df(row) + (data_hoje,)
            for _, row in df_base.iterrows()
        ]

        with conexao.cursor() as cursor:
            execute_values(cursor, query, dados_lote)
        conexao.commit()
        
        print(f"🚀 [CACHE] {len(df_base)} ações sincronizadas na tabela acoes_cache!")
        return True
    except Exception as e:
        print(f"🚨 [ERRO SALVAR CACHE]: {e}")
        return False


# =============================================================================
# LEITURA DE DADOS
# =============================================================================
def ler_cache_acoes():
    """Lê todas as ações da tabela acoes_cache em formato de dicionários."""
    try:
        conexao = obter_conexao()
        with conexao.cursor(cursor_factory=RealDictCursor) as cursor:
            cursor.execute("SELECT * FROM acoes_cache;")
            registros = cursor.fetchall()
        return list(registros)
    except Exception as e:
        print(f"🚨 [ERRO LER CACHE]: {e}")
        return []