"""
Rotina agendada (GitHub Actions, ver .github/workflows/cron.yml).

Coleta os indicadores no TradingView e grava no Supabase. Não importa o
servidor Flask: depende apenas dos módulos de coleta e de banco.
"""

import sys

import coleta
import database


def executar_job():
    """Executa a coleta e a gravação. Retorna True se tudo deu certo."""
    print("🔄 [CRON] Iniciando atualização do Supabase...")
    database.inicializar_banco()

    df = coleta.buscar_acoes_tradingview()
    if df.empty:
        print("🚨 [CRON] TradingView não retornou dados.")
        return False

    if not database.salvar_coleta(df):
        print("🚨 [CRON] Falha ao gravar no Supabase.")
        return False

    print("✅ [CRON] Atualização concluída.")
    return True


if __name__ == "__main__":
    # Código de saída != 0 faz o GitHub Actions marcar a execução como falha
    sys.exit(0 if executar_job() else 1)
