"""Agenda PRÓPRIA do Zaq (sem OAuth). Eventos por CONTA, com lembrete opt-in.

Desenho:
- O horário que a pessoa fala é interpretado em HORÁRIO DE BRASÍLIA (UTC-3, sem
  horário de verão desde 2019). Guardamos timestamptz (aware).
- O agente (Claude) converte "amanhã 15h" -> uma data/hora concreta e chama a
  ferramenta; aqui a gente parseia formatos comuns com tolerância.
- "Adicionar ao Google/Apple/Outlook": link do Google Calendar (1 toque) e .ics
  (universal). A sincronização por feed .ics assinável vem na etapa 3.

Tudo escopado por conta_id (multi-tenant sagrado).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

# Brasília: UTC-3 fixo (o Brasil não usa mais horário de verão).
BRT = timezone(timedelta(hours=-3))


def agora_brt() -> datetime:
    return datetime.now(BRT)


def parse_datahora(s: str | None) -> datetime | None:
    """Converte texto de data/hora em datetime AWARE (Brasília). Tolerante.
    Aceita 'dd/mm/aaaa HH:MM', 'dd/mm HH:MM', ISO, com/sem hora e com/sem ano."""
    s = (s or "").strip().replace("T", " ")
    if not s:
        return None
    fmts = [
        ("%d/%m/%Y %H:%M", True, True), ("%d/%m/%y %H:%M", True, True),
        ("%d/%m %H:%M", False, True), ("%Y-%m-%d %H:%M", True, True),
        ("%d/%m/%Y %H", True, True), ("%d/%m %H", False, True),
        ("%d/%m/%Y", True, False), ("%d/%m", False, False), ("%Y-%m-%d", True, False),
    ]
    for fmt, tem_ano, tem_hora in fmts:
        try:
            dt = datetime.strptime(s, fmt)
        except ValueError:
            continue
        hoje = agora_brt()
        if not tem_hora:
            dt = dt.replace(hour=9, minute=0)     # sem hora -> 09:00 (default)
        if not tem_ano:
            dt = dt.replace(year=hoje.year)
        dt = dt.replace(tzinfo=BRT)
        # sem ano e já passou neste ano -> rola pro ano que vem
        if not tem_ano and dt < hoje - timedelta(hours=1):
            dt = dt.replace(year=hoje.year + 1)
        return dt
    return None


def _fmt_evento(row) -> dict:
    return {"id": row[0], "titulo": row[1], "inicio": row[2], "fim": row[3],
            "local": row[4], "descricao": row[5], "lembrete_min": row[6],
            "criado_em": row[7] if len(row) > 7 else None}


_COLS = "id, titulo, inicio, fim, local, descricao, lembrete_min, criado_em"


def criar_evento(pool, conta_id: int, titulo: str, inicio: datetime, *,
                 membro_id: int | None = None, fim: datetime | None = None,
                 local: str | None = None, descricao: str | None = None,
                 lembrete_min: int | None = None) -> dict:
    with pool.connection() as c:
        row = c.execute(
            """insert into eventos_agenda
               (conta_id, membro_id, titulo, inicio, fim, local, descricao, lembrete_min)
               values (%s,%s,%s,%s,%s,%s,%s,%s)
               returning """ + _COLS,
            (conta_id, membro_id, titulo.strip(), inicio, fim, local, descricao, lembrete_min),
        ).fetchone()
        c.commit()
    return _fmt_evento(row)


def listar_eventos(pool, conta_id: int, de: datetime, ate: datetime) -> list[dict]:
    with pool.connection() as c:
        rows = c.execute(
            "select " + _COLS + " from eventos_agenda "
            "where conta_id=%s and status='ativo' and inicio >= %s and inicio < %s "
            "order by inicio",
            (conta_id, de, ate),
        ).fetchall()
    return [_fmt_evento(r) for r in rows]


def proximos(pool, conta_id: int, limite: int = 20) -> list[dict]:
    with pool.connection() as c:
        rows = c.execute(
            "select " + _COLS + " from eventos_agenda "
            "where conta_id=%s and status='ativo' and inicio >= %s "
            "order by inicio limit %s",
            (conta_id, agora_brt() - timedelta(hours=2), limite),
        ).fetchall()
    return [_fmt_evento(r) for r in rows]


def achar_por_titulo(pool, conta_id: int, termo: str) -> list[dict]:
    """Eventos FUTUROS cujo título casa com o termo (pra remarcar/cancelar por nome)."""
    termo = (termo or "").strip()
    if not termo:
        return []
    with pool.connection() as c:
        rows = c.execute(
            "select " + _COLS + " from eventos_agenda "
            "where conta_id=%s and status='ativo' and inicio >= %s and titulo ilike %s "
            "order by inicio",
            (conta_id, agora_brt() - timedelta(hours=2), f"%{termo}%"),
        ).fetchall()
    return [_fmt_evento(r) for r in rows]


def remarcar_evento(pool, conta_id: int, evento_id: int, inicio: datetime,
                    fim: datetime | None = None) -> bool:
    with pool.connection() as c:
        cur = c.execute(
            "update eventos_agenda set inicio=%s, fim=%s "
            "where id=%s and conta_id=%s and status='ativo'",
            (inicio, fim, evento_id, conta_id),
        )
        c.commit()
        return cur.rowcount > 0


def cancelar_evento(pool, conta_id: int, evento_id: int) -> bool:
    with pool.connection() as c:
        cur = c.execute(
            "update eventos_agenda set status='cancelado' "
            "where id=%s and conta_id=%s and status='ativo'",
            (evento_id, conta_id),
        )
        c.commit()
        return cur.rowcount > 0


# ---------- "adicionar ao calendário": Google (link) e .ics (universal) ----------

def _utc(d: datetime) -> str:
    return d.astimezone(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


def _fim_ou_1h(ev: dict) -> datetime:
    return ev.get("fim") or (ev["inicio"] + timedelta(hours=1))


def link_google(ev: dict) -> str:
    """URL do Google Calendar que adiciona o evento em 1 toque (sem login)."""
    from urllib.parse import quote
    p = (f"action=TEMPLATE&text={quote(ev['titulo'])}"
         f"&dates={_utc(ev['inicio'])}/{_utc(_fim_ou_1h(ev))}")
    if ev.get("local"):
        p += f"&location={quote(ev['local'])}"
    if ev.get("descricao"):
        p += f"&details={quote(ev['descricao'])}"
    return "https://calendar.google.com/calendar/render?" + p


def _ics_escape(t: str) -> str:
    return (str(t or "").replace("\\", "\\\\").replace(";", "\\;")
            .replace(",", "\\,").replace("\n", "\\n"))


def evento_para_ics(ev: dict) -> str:
    linhas = ["BEGIN:VEVENT", f"UID:zaq-{ev['id']}@zaq-ia.com",
              f"DTSTAMP:{_utc(ev.get('criado_em') or ev['inicio'])}",
              f"DTSTART:{_utc(ev['inicio'])}", f"DTEND:{_utc(_fim_ou_1h(ev))}",
              f"SUMMARY:{_ics_escape(ev['titulo'])}"]
    if ev.get("local"):
        linhas.append(f"LOCATION:{_ics_escape(ev['local'])}")
    if ev.get("descricao"):
        linhas.append(f"DESCRIPTION:{_ics_escape(ev['descricao'])}")
    linhas.append("END:VEVENT")
    return "\r\n".join(linhas)


def feed_ics(eventos: list[dict]) -> str:
    """Calendário .ics completo (assinável) — Google/Apple/Outlook leem."""
    cab = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Zaq//Agenda//PT",
           "CALSCALE:GREGORIAN", "X-WR-CALNAME:Zaq"]
    corpo = [evento_para_ics(e) for e in eventos]
    return "\r\n".join(cab + corpo + ["END:VCALENDAR"]) + "\r\n"


def fmt_hora(ev: dict) -> str:
    """dd/mm HH:MM em Brasília, pra mostrar pro usuário."""
    return ev["inicio"].astimezone(BRT).strftime("%d/%m %H:%M")
