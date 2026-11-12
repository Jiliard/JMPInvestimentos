"""
Registro diário no banco, sem depender do servidor Flask.

Usado pela rotina cron.yml do GitHub Actions (coleta direta, independente do
servidor no Render) e manualmente: `python cron_updater.py`.

1. Indicadores do TradingView -> acoes_cache e historico_diario.
2. Depois das 19h: fechamento do Ibovespa e eventos das ações -> indice_diario e
   eventos_acoes (o servidor também faz isso; gravar duas vezes só atualiza).
"""

import os
import sys
from urllib.parse import urlparse

import coleta
import database


def aviso(mensagem):
    """No GitHub Actions, vira um aviso no resumo da execução (sem abrir o log)."""
    mensagem = " ".join(str(mensagem).split())
    print(f"::warning title=Coleta direta::{mensagem}" if os.getenv("GITHUB_ACTIONS") else f"🚨 {mensagem}")


def registrar_complementos():
    """Ibovespa e eventos (dividendos e desdobramentos) das ações atuais. True se gravou."""
    fechamentos, eventos = coleta.buscar_complementos_backtesting(database.tickers_atuais())
    return bool(fechamentos) and database.salvar_complementos(fechamentos, eventos)


def executar_job():
    """Executa a coleta e a gravação. Retorna True se tudo deu certo."""
    print("🔄 [CRON] Iniciando atualização do Supabase...")
    try:
        database._executar(lambda cursor: cursor.execute("SELECT 1;"))
    except Exception as erro:
        host = urlparse(database.URL_BANCO or "").hostname or "DATABASE_URL vazia"
        aviso(f"Sem conexão com o banco ({host}): {erro}")
        return False
    database.inicializar_banco()

    df = coleta.buscar_acoes_tradingview()
    if df.empty:
        aviso("O TradingView não retornou dados (falha da API ou acesso recusado a este servidor).")
        return False
    if not database.salvar_coleta(df):
        aviso("Falha ao gravar a coleta no banco (detalhes no log).")
        return False

    if database.agora_b3().hour >= 19 and not registrar_complementos():
        aviso("Indicadores gravados, mas não foi possível registrar o Ibovespa e os eventos.")

    print("✅ [CRON] Atualização concluída.")
    return True


if __name__ == "__main__":
    # Código de saída != 0 indica que a coleta não foi gravada
    sys.exit(0 if executar_job() else 1)
