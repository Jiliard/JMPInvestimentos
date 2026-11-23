"""
Registro diário no banco, sem depender do servidor Flask.

Executado pela rotina cron.yml do GitHub Actions (reserva independente do servidor)
ou manualmente: `python rotina_diaria.py`.

1. Indicadores do TradingView -> acoes_cache e historico_diario.
2. Depois das 19h: fechamento do Ibovespa e eventos das ações -> indice_diario e
   eventos_acoes. O servidor também faz esse registro; gravar duas vezes só atualiza.
3. Critério de liquidez do NEFIN do ano anterior -> liquidez_anual. Calculado na primeira
   rotina do ano; nas seguintes, só para as ações que ainda não têm o registro.
"""

import logging
import os
import sys
from urllib.parse import urlparse

import coleta
import database

log = logging.getLogger(__name__)


def registrar_complementos():
    """Grava o Ibovespa e os eventos (dividendos e desdobramentos) das ações atuais. True se gravou."""
    fechamentos, eventos = coleta.buscar_complementos_backtesting(database.tickers_atuais())
    return bool(fechamentos) and database.salvar_complementos(fechamentos, eventos)


def registrar_liquidez_anual():
    """
    Grava o critério de liquidez do NEFIN do ano anterior para as ações atuais que ainda
    não o têm (no começo do ano, todas). True se não faltava nada ou se gravou.
    """
    ano = database.agora_b3().year - 1
    feitos = {r["ticker"] for r in database.ler_liquidez_anual(ano)}
    faltando = [t for t in database.tickers_atuais() if t not in feitos]
    if not faltando:
        return True
    return database.salvar_liquidez_anual(coleta.buscar_liquidez_anual(faltando, ano))


def _aviso(mensagem):
    """No GitHub Actions, a linha ::warning vira um aviso no resumo da execução."""
    mensagem = " ".join(str(mensagem).split())
    if os.getenv("GITHUB_ACTIONS"):
        print(f"::warning title=Coleta direta::{mensagem}")
    else:
        log.warning(mensagem)


def executar():
    """Coleta e grava o pregão. Retorna True se a coleta foi gravada."""
    try:
        database.testar_conexao()
    except Exception as erro:
        host = urlparse(database.URL_BANCO or "").hostname or "DATABASE_URL vazia"
        _aviso(f"Sem conexão com o banco ({host}): {erro}")
        return False
    database.inicializar_banco()

    df = coleta.buscar_acoes_tradingview()
    if df.empty:
        _aviso("O TradingView não retornou dados (falha da API ou acesso recusado a este servidor).")
        return False
    if not database.salvar_coleta(df):
        _aviso("Falha ao gravar a coleta no banco (detalhes no log).")
        return False

    if database.agora_b3().hour >= 19 and not registrar_complementos():
        _aviso("Indicadores gravados, mas não foi possível registrar o Ibovespa e os eventos.")
    if not registrar_liquidez_anual():
        _aviso("Indicadores gravados, mas não foi possível registrar a liquidez do ano anterior.")
    return True


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s [%(name)s] %(message)s")
    sys.exit(0 if executar() else 1)   # código diferente de 0 marca a execução como falha
