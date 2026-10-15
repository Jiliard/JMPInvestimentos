import sys
from pathlib import Path

# Garante que o Python encontre os módulos na raiz do projeto
RAIZ_PROJETO = Path(__file__).resolve().parent
if str(RAIZ_PROJETO) not in sys.path:
    sys.path.append(str(RAIZ_PROJETO))

import database
from app import buscar_e_calcular_dados_tradingview


# =============================================================================
# EXECUÇÃO DA ROTINA AGENDADA (CRON JOB)
# =============================================================================
def executar_job():
    """Executa a varredura preventiva na B3 e sincroniza com o Supabase."""
    print("🔄 [CRON JOB] Iniciando atualização preventiva do Supabase...")
    
    try:
        df = buscar_e_calcular_dados_tradingview()
        
        if not df.empty:
            database.salvar_cache_acoes(df)
            print("✅ [CRON JOB] Dados atualizados com sucesso no Supabase!")
        else:
            print("🚨 [CRON JOB] Falha ao obter dados do TradingView (DataFrame vazio).")
            
    except Exception as e:
        print(f"🚨 [ERRO CRÍTICO NO CRON JOB]: {e}")


if __name__ == "__main__":
    executar_job()