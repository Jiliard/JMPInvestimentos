import os
import psycopg2
from psycopg2.extras import execute_values
from datetime import datetime

# URL da Nuvem (Supabase) via Pooler
# Coloque a sua URL com a senha real aqui
URL_PADRAO = "postgresql://postgres.mhyeccidsziqeqvsmryj:HB6C8pfwOpotari7@aws-0-us-west-2.pooler.supabase.com:6543/postgres?pgbouncer=true"
URL_BANCO_NUVEM = os.getenv("DATABASE_URL", URL_PADRAO)

def conectar_banco():
    return psycopg2.connect(URL_BANCO_NUVEM, connect_timeout=10)

def inicializar_banco():
    """Cria a tabela no PostgreSQL da nuvem se não existir."""
    try:
        conexao = conectar_banco()
        cursor = conexao.cursor()
        
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
        
        conexao.commit()
        cursor.close()
        conexao.close()
        print("✅ [POSTGRES] Tabela de histórico inicializada no Supabase com sucesso!")
    except Exception as e:
        print(f"🚨 [ERRO CONEXÃO POSTGRES]: {e}")

def salvar_historico_ranking(df_ranqueado, metodo):
    """Insere o histórico direto na nuvem do Supabase."""
    if df_ranqueado.empty:
        return
        
    try:
        conexao = conectar_banco()
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
        conexao.close()
        print(f"💾 [NUVEM] {len(df_ranqueado)} registros gravados no Supabase para o método '{metodo}'.")
    except Exception as e:
        print(f"🚨 [ERRO SALVAR BANCO]: {e}")