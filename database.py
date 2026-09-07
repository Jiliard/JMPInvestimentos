import os
import psycopg2
from psycopg2.extras import execute_values, RealDictCursor
from datetime import datetime

# URL da Nuvem (Supabase) via Pooler/Direta
URL_PADRAO = "postgresql://postgres:HB6C8pfwOpotari7@db.mhyeccidsziqeqvsmryj.supabase.co:5432/postgres"
URL_BANCO_NUVEM = os.getenv("DATABASE_URL", URL_PADRAO)

# Variável de conexão persistente em memória
_CONEXAO_PERSISTENTE = None

def obter_conexao():
    """Mantém e reutiliza a conexão com o PostgreSQL para evitar o overhead de handshake SSL/TCP."""
    global _CONEXAO_PERSISTENTE
    try:
        if _CONEXAO_PERSISTENTE is None or _CONEXAO_PERSISTENTE.closed != 0:
            _CONEXAO_PERSISTENTE = psycopg2.connect(URL_BANCO_NUVEM, connect_timeout=5)
        return _CONEXAO_PERSISTENTE
    except Exception as e:
        print(f"🚨 [ERRO CONEXÃO POSTGRES]: {e}")
        # Tenta reconectar diretamente se a variável falhar
        return psycopg2.connect(URL_BANCO_NUVEM, connect_timeout=5)

def inicializar_banco():
    """Cria as tabelas no PostgreSQL da nuvem se não existirem."""
    try:
        conexao = obter_conexao()
        cursor = conexao.cursor()
        
        # 1. Tabela de Histórico (Original)
        cursor.execute('''
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
        ''')

        # 2. Tabela de Cache Atualizado (Para Carga Instantânea < 200ms)
        cursor.execute('''
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
        ''')
        
        conexao.commit()
        cursor.close()
        print("✅ [POSTGRES] Tabelas 'historico_acoes' e 'acoes_cache' inicializadas no Supabase com sucesso!")
    except Exception as e:
        print(f"🚨 [ERRO INICIALIZAR BANCO]: {e}")

def salvar_historico_ranking(df_ranqueado, metodo):
    """Insere o histórico direto na nuvem do Supabase."""
    if df_ranqueado.empty:
        return False
        
    try:
        conexao = obter_conexao()
        cursor = conexao.cursor()
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
            
        query = '''
            INSERT INTO historico_acoes (
                data_registro, ticker, nome_empresa, preco, pl, pvp, 
                dy, roic, roe, margem, crescimento, liquidez, 
                metodo_ranking, posicao_ranking
            ) VALUES %s
        '''
        
        execute_values(cursor, query, dados_lote)
        conexao.commit()
        cursor.close()
        print(f"💾 [NUVEM] {len(df_ranqueado)} registros gravados no histórico para o método '{metodo}'.")
        return True
    except Exception as e:
        print(f"🚨 [ERRO SALVAR HISTÓRICO]: {e}")
        return False

def salvar_cache_acoes(df_base):
    """Salva/Atualiza o cache completo de ações no Supabase para carga instantânea (UPSERT)."""
    if df_base.empty:
        return False

    try:
        conexao = obter_conexao()
        cursor = conexao.cursor()
        data_hoje = datetime.now()

        dados_lote = []
        for _, row in df_base.iterrows():
            dados_lote.append((
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
                data_hoje
            ))

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

        execute_values(cursor, query, dados_lote)
        conexao.commit()
        cursor.close()
        print(f"🚀 [CACHE] {len(df_base)} ações salvas/atualizadas na tabela acoes_cache!")
        return True
    except Exception as e:
        print(f"🚨 [ERRO SALVAR CACHE]: {e}")
        return False

def ler_cache_acoes():
    """Lê todas as ações direto da tabela acoes_cache em milissegundos."""
    try:
        conexao = obter_conexao()
        cursor = conexao.cursor(cursor_factory=RealDictCursor)

        cursor.execute("SELECT * FROM acoes_cache;")
        registros = cursor.fetchall()

        cursor.close()
        return list(registros)
    except Exception as e:
        print(f"🚨 [ERRO LER CACHE]: {e}")
        return []