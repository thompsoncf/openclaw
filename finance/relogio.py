"""Que dia é hoje — no fuso de quem usa o sistema, não no do servidor.

O servidor roda em UTC. `date.today()` ali devolve o dia seguinte das 21h às
23h59 de Brasília, e foi exatamente isso que aconteceu com o lançamento 733
("Casa do Churrasco", conta 3): criado em 22/08 às 21:21 BRT — 00:21 UTC do dia
23 — e gravado com `data = 2026-08-23`. Os lançamentos 731 e 732, das 20:08 e
20:09, ficaram certos. A fronteira é 21h em ponto, que é meia-noite em UTC.

Doze lançamentos em quatro contas nasceram errados assim desde 04/07, e o
estrago não para em lançamento: relatório, campanha, comissão e régua de
cobrança usam a mesma noção de "hoje".

POR QUE ISTO EXISTE SE A VARIÁVEL TZ RESOLVERIA

Resolveria, e ela está posta nos serviços Python. Mas `TZ` só funciona se o
contêiner tiver a base de fusos em /usr/share/zoneinfo; sem ela o valor é
ignorado EM SILÊNCIO e o processo segue em UTC — a falha volta sem avisar
ninguém, no mesmo intervalo das 21h, e o sintoma é indistinguível do bug
original. Aqui a conta é explícita e não depende de variável de ambiente
nenhuma: converte de UTC pro fuso de Brasília na mão.

O `ZoneInfo` é tentado primeiro (segue horário de verão se ele voltar); o
offset fixo de -3 é a reserva, e é o que o `finance/agenda.py` já usava — o
Brasil não tem horário de verão desde 2019, então hoje os dois concordam.
"""
from datetime import date, datetime, timedelta, timezone

# Reserva: -3 fixo. Correto enquanto não houver horário de verão, e é o que
# roda se a base de fusos não estiver no contêiner.
_FIXO = timezone(timedelta(hours=-3))

try:  # pragma: no cover - depende do sistema de arquivos do contêiner
    from zoneinfo import ZoneInfo
    BR = ZoneInfo("America/Sao_Paulo")
    # ZoneInfo constrói sem erro e só falha ao ser USADO; força agora, no import,
    # pra cair na reserva aqui e não no meio de um lançamento.
    datetime.now(timezone.utc).astimezone(BR)
except Exception:  # noqa: BLE001
    BR = _FIXO


def agora() -> datetime:
    """O instante atual, com fuso, já em horário de Brasília."""
    return datetime.now(timezone.utc).astimezone(BR)


def hoje() -> date:
    """O dia de hoje em Brasília. É este o substituto de `date.today()`."""
    return agora().date()


def para_br(quando: datetime) -> datetime:
    """Traz um instante qualquer pro fuso de Brasília.

    Data ingênua é tratada como UTC — que é o que ela é quando vem de uma coluna
    lida sem fuso ou de um `datetime.utcnow()` antigo. Adivinhar o contrário
    (tratar como local) é o que produz o erro de 3h.
    """
    if quando.tzinfo is None:
        quando = quando.replace(tzinfo=timezone.utc)
    return quando.astimezone(BR)


def dia_br(quando: datetime) -> date:
    """O dia CIVIL de um instante, em Brasília. Use isto para agrupar por dia."""
    return para_br(quando).date()
