"""Manda o resumo semanal das contas que pediram (roda como cron, todo dia).

TODO DIA, e não só na segunda, porque o DIA é da conta: uma escolhe segunda 9h,
outra sexta 17h (`contas.resumo_semanal_dia`). Um cron por dia da semana seria
dois serviços no Render pra uma pergunta que o banco responde — e o `contas_do_dia`
devolve vazio nos outros cinco dias, o que custa uma consulta.

No Render é um serviço `cron` como o `openclaw-monitor-saldos`:

    schedule: "0 12 * * *"      # 12:00 UTC = 09:00 BRT
    startCommand: python -m scripts.resumo_semanal

As variaveis: DATABASE_URL, SMTP_USER e SMTP_SENHA (os nomes que
`finance/email_sender._cfg` le — SMTP_SENHA, nao SMTP_PASS). Sem elas o modulo
loga "envio desabilitado" e o cron termina com sucesso sem mandar nada: e por
isso que a primeira rodada tem que ser conferida em `resumo_semanal_envio`, e
nao no codigo de saida.

A hora de sexta (17h BRT) sai de um segundo horário no mesmo serviço não — sai de
o cron rodar às 12:00 UTC e a conta de sexta receber às 9h da sexta. Se alguém
escolher sexta esperando 17h, é a primeira coisa a acertar aqui; hoje o dono
escolheu segunda e a diferença não aparece.

NÃO REPETE: `resumo_semanal_envio` tem chave primária (conta, semana, destino), e
`ja_enviado` é consultado antes de montar. Rodar duas vezes no mesmo dia — deploy
no meio, retry do Render — não manda o mesmo e-mail duas vezes.

Uma conta que estourar não derruba as outras: cada uma é um try.
"""
from __future__ import annotations

import logging
import os
import sys

logging.basicConfig(level=logging.INFO,
                    format="%(asctime)s %(levelname)s %(name)s %(message)s")
log = logging.getLogger("resumo_semanal")


def main() -> int:
    if not os.getenv("DATABASE_URL"):
        log.error("sem DATABASE_URL — nada a fazer")
        return 1
    from db.conexao import get_pool
    from finance import resumo_semanal as rs

    pool = get_pool()
    contas = rs.contas_do_dia(pool)
    if not contas:
        log.info("nenhuma conta com resumo semanal hoje")
        return 0

    log.info("resumo semanal: %d conta(s)", len(contas))
    total, erros = 0, 0
    for cid in contas:
        try:
            r = rs.enviar_conta(pool, cid)
            total += int(r.get("enviados") or 0)
            log.info("conta %s: %s", cid, r)
        except Exception as e:  # noqa: BLE001 — uma conta não derruba as outras
            erros += 1
            log.exception("conta %s falhou: %s: %s", cid, type(e).__name__, e)
    log.info("resumo semanal terminado: %d e-mail(s), %d conta(s) com erro", total, erros)
    return 0


if __name__ == "__main__":
    sys.exit(main())
