import os
import sys

# Garante que o Python encontre os módulos do projeto
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

from app import buscar_e_calcular_dados_tradingview
import database

def executar_job():
    print("🔄 [CRON JOB] Iniciando atualização preventiva do Supabase...")
    df = buscar_e_calcular_dados_tradingview()
    
    if not df.empty:
        database.salvar_cache_acoes(df)
        print("✅ [CRON JOB] Dados atualizados com sucesso no Supabase!")
    else:
        print("🚨 [CRON JOB] Erro ao obter dados do TradingView.")

if __name__ == "__main__":
    executar_job()