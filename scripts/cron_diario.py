"""O cron diário: o resumo da Fase B no Telegram do admin e a faxina do
`conversas_log` (roda 1x/dia no Render, serviço `openclaw-cron-diario-va`).

POR QUE ESTE ARQUIVO EXISTE
O cron rodava `python -c "from web.app import _setup; ..."` — carregava o painel
INTEIRO só pra pegar a conexão com o banco. Desde o #740 (19/09) o painel se
recusa a subir no Render sem `PORTAL_SECRET` forte (é a chave que assina o
login de cada conta), e o cron de Virginia, criado em 20/09, não tem essa
variável: morreu todo dia às 12:00 UTC de 21/09 a 27/09 sem fazer nada.

A saída NÃO é copiar o `PORTAL_SECRET` pro cron — espalharia a chave que abre
qualquer conta num serviço que não assina sessão nenhuma. É o cron não depender
do painel: aqui só entra o banco (`db.conexao.get_pool`) e as duas funções.

De quebra, sai o `init_schema` que o `_setup()` do painel rodava — DDL em
produção todo dia sem necessidade (ver a seção 2 do CLAUDE.md) — e o cérebro da
IA, que o cron nunca usou.

Cada passo é independente: se o resumo falhar, a faxina roda assim mesmo, e
vice-versa. Se algum passo QUEBRAR, o processo sai com 1 — o Render manda o
e-mail de "cronjob failed", que foi como este problema apareceu.

Uso (Start Command do cron no Render):  python -m scripts.cron_diario
"""
from __future__ import annotations

import logging
import sys

_log = logging.getLogger("openclaw.cron_diario")

#: dias que o `conversas_log` guarda — o mesmo 30 do comando antigo
DIAS_CONVERSAS_LOG = 30


def rodar(pool=None) -> bool:
    """Roda os dois passos. Devolve True se nenhum quebrou."""
    if pool is None:
        from db.conexao import get_pool
        pool = get_pool()
    ok = True

    try:
        from finance.notificar import alerta_fase_b
        if alerta_fase_b(pool, sempre=True):
            _log.info("resumo da Fase B enviado ao admin")
        else:
            _log.warning("resumo da Fase B NÃO enviado: falta TELEGRAM_TOKEN ou o "
                         "Telegram do admin (/admin ou ADMIN_TELEGRAM_ID)")
    except Exception:  # noqa: BLE001 — a faxina roda mesmo assim
        _log.exception("resumo da Fase B quebrou")
        ok = False

    try:
        from finance.observabilidade import expurgar_antigos
        n = expurgar_antigos(pool, DIAS_CONVERSAS_LOG)
        _log.info("faxina do conversas_log: %s linhas com mais de %s dias",
                  n, DIAS_CONVERSAS_LOG)
    except Exception:  # noqa: BLE001
        _log.exception("faxina do conversas_log quebrou")
        ok = False

    return ok


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    sys.exit(0 if rodar() else 1)
