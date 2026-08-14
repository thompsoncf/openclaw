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


def frase_nomes(nomes: list[str]) -> str:
    """Junta nomes em português: 'Ana', 'Ana e Carlos', 'Ana, Carlos e Bia'."""
    nomes = [n for n in nomes if n]
    if not nomes:
        return ""
    if len(nomes) == 1:
        return nomes[0]
    return ", ".join(nomes[:-1]) + " e " + nomes[-1]


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


# Categorias do compromisso (pro portal colorir e separar pessoal/empresa).
TIPOS = ("pessoal", "empresa", "fornecedor")


def _fmt_evento(row) -> dict:
    return {"id": row[0], "titulo": row[1], "inicio": row[2], "fim": row[3],
            "local": row[4], "descricao": row[5], "lembrete_min": row[6],
            "criado_em": row[7] if len(row) > 7 else None,
            "tipo": (row[8] if len(row) > 8 else None) or "pessoal",
            "desfecho": row[9] if len(row) > 9 else None,
            "link_online": row[10] if len(row) > 10 else None}


_COLS = ("id, titulo, inicio, fim, local, descricao, lembrete_min, criado_em, tipo, "
        "desfecho, link_online")


def criar_evento(pool, conta_id: int, titulo: str, inicio: datetime, *,
                 membro_id: int | None = None, fim: datetime | None = None,
                 local: str | None = None, descricao: str | None = None,
                 lembrete_min: int | None = None, tipo: str = "pessoal",
                 link_online: str | None = None,
                 prospeccao_id: int | None = None) -> dict:
    """prospeccao_id liga o evento a um lead (ex.: retorno de contato) — some da
    Agenda pro cliente, mas fica clicável a partir da ficha do lead (migração 136)."""
    tipo = tipo if tipo in TIPOS else "pessoal"
    # prospeccao_id só entra no INSERT quando informado — mantém compatível com
    # bancos/testes que ainda não rodaram a migração 136 (que criou a coluna).
    colunas = "conta_id, membro_id, titulo, inicio, fim, local, descricao, lembrete_min, tipo, link_online"
    valores = [conta_id, membro_id, titulo.strip(), inicio, fim, local, descricao,
               lembrete_min, tipo, link_online]
    if prospeccao_id is not None:
        colunas += ", prospeccao_id"
        valores.append(prospeccao_id)
    with pool.connection() as c:
        row = c.execute(
            f"""insert into eventos_agenda ({colunas})
               values ({','.join(['%s'] * len(valores))})
               returning """ + _COLS,
            valores,
        ).fetchone()
        c.commit()
    return _fmt_evento(row)


def evento_por_id(pool, conta_id: int, evento_id: int) -> dict | None:
    """Um evento ativo da conta (pra montar o card de compartilhar convites)."""
    with pool.connection() as c:
        r = c.execute(
            "select " + _COLS + " from eventos_agenda "
            "where id=%s and conta_id=%s and status='ativo'",
            (evento_id, conta_id)).fetchone()
    return _fmt_evento(r) if r else None


def evento_por_id_qualquer_status(pool, conta_id: int, evento_id: int) -> dict | None:
    """Igual evento_por_id, mas também acha CANCELADO — pra reaproveitar
    (remarcar) um compromisso que não aconteceu."""
    with pool.connection() as c:
        r = c.execute(
            "select " + _COLS + " from eventos_agenda where id=%s and conta_id=%s",
            (evento_id, conta_id)).fetchone()
    return _fmt_evento(r) if r else None


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


def achar_por_titulo_passado(pool, conta_id: int, termo: str, agora: datetime,
                             dias: int = 30) -> list[dict]:
    """Eventos ATIVOS e JÁ PASSADOS (últimos `dias`) cujo título casa com o termo
    — pra marcar desfecho (aconteceu/não aconteceu) por nome, já que o compromisso
    some de achar_por_titulo (só olha o futuro) assim que a data passa."""
    termo = (termo or "").strip()
    if not termo:
        return []
    with pool.connection() as c:
        rows = c.execute(
            "select " + _COLS + " from eventos_agenda "
            "where conta_id=%s and status='ativo' and inicio < %s and inicio >= %s and titulo ilike %s "
            "order by inicio desc",
            (conta_id, agora, agora - timedelta(days=dias), f"%{termo}%"),
        ).fetchall()
    return [_fmt_evento(r) for r in rows]


def vocabulario_stt(pool, conta_id: int, limite_chars: int = 200) -> str:
    """Nomes que aparecem na agenda da conta (títulos dos próximos + convidados)
    pra dar de DICA pro transcritor de voz — ajuda a acertar 'Mailson', 'Smart
    Center' etc. Curto (o prompt do Whisper é limitado) e best-effort."""
    palavras: list[str] = []
    try:
        for ev in proximos(pool, conta_id, limite=20):
            palavras.append(ev["titulo"])
    except Exception:  # noqa: BLE001
        pass
    try:
        with pool.connection() as c:
            rows = c.execute(
                "select distinct nome from evento_convidados "
                "where conta_id=%s and nome is not null limit 40", (conta_id,)).fetchall()
        palavras += [r[0] for r in rows]
    except Exception:  # noqa: BLE001
        pass
    vistos: set[str] = set()
    out: list[str] = []
    for p in palavras:
        p = (p or "").strip()
        k = p.lower()
        if p and k not in vistos:
            vistos.add(k)
            out.append(p)
    return ", ".join(out)[:limite_chars]


def _sem_acento(s: str) -> str:
    import unicodedata
    return "".join(c for c in unicodedata.normalize("NFD", (s or "").lower())
                   if unicodedata.category(c) != "Mn")


def sugerir_por_titulo(pool, conta_id: int, termo: str, limite: int = 4) -> list[dict]:
    """Eventos futuros ORDENADOS por semelhança com o termo — pra SUGERIR quando
    não bate exato (a voz costuma trocar/mangar o nome). Combina sobreposição de
    palavras (peso maior) com similaridade de string (difflib)."""
    import difflib
    evs = proximos(pool, conta_id, limite=100)
    if not evs:
        return []
    alvo = _sem_acento(termo)
    if not alvo:
        return evs[:limite]
    palavras = {p for p in alvo.split() if len(p) >= 3}

    def score(ev: dict) -> float:
        t = _sem_acento(ev["titulo"])
        ratio = difflib.SequenceMatcher(None, alvo, t).ratio()
        overlap = len(palavras & {p for p in t.split() if len(p) >= 3})
        return overlap * 2.0 + ratio

    ranked = sorted(evs, key=score, reverse=True)
    bons = [e for e in ranked if score(e) >= 0.34]     # tem ALGUMA semelhança
    return (bons or ranked)[:limite]


def remarcar_evento(pool, conta_id: int, evento_id: int, inicio: datetime,
                    fim: datetime | None = None) -> bool:
    """Muda a data — e SEMPRE deixa o evento ativo de novo (reativa se estava
    cancelado/marcado como não realizado) e limpa o desfecho: remarcar significa
    'isso vai acontecer nessa data', não importa o que era antes. É o mesmo
    caminho tanto pra remarcar um ativo quanto pra reaproveitar um cancelado."""
    with pool.connection() as c:
        cur = c.execute(
            "update eventos_agenda set inicio=%s, fim=%s, status='ativo', desfecho=null "
            "where id=%s and conta_id=%s",
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


_DESFECHOS = ("realizado", "nao_realizado")


def marcar_desfecho(pool, conta_id: int, evento_id: int, desfecho: str, agora: datetime) -> bool:
    """Marca se um compromisso já passado aconteceu ou não — só pra eventos
    ATIVOS cuja data já chegou (não faz sentido marcar o futuro nem um
    cancelado, que já é sabidamente 'não aconteceu')."""
    if desfecho not in _DESFECHOS:
        return False
    with pool.connection() as c:
        cur = c.execute(
            "update eventos_agenda set desfecho=%s "
            "where id=%s and conta_id=%s and status='ativo' and inicio <= %s",
            (desfecho, evento_id, conta_id, agora),
        )
        c.commit()
        return cur.rowcount > 0


def eventos_para_reaproveitar(pool, conta_id: int, agora: datetime, limite: int = 6) -> list[dict]:
    """Compromissos recentes (últimos 90 dias) marcados como NÃO REALIZADOS —
    candidatos a reaproveitar em vez de recriar do zero. Cancelado fica de fora
    de propósito: quem cancela já decidiu que não tem previsão de acontecer, e
    listar todo cancelado lotaria a lista de sugestão à toa — só "não rolou"
    (ficou sem confirmação de que não vai mais acontecer) é sugestão útil aqui.
    Mais recentes primeiro."""
    with pool.connection() as c:
        rows = c.execute(
            "select " + _COLS + ", (select count(*) from evento_convidados ec "
            "where ec.evento_id = eventos_agenda.id) as n_convidados "
            "from eventos_agenda "
            "where conta_id=%s and inicio >= %s and inicio <= %s and desfecho='nao_realizado' "
            "order by inicio desc limit %s",
            (conta_id, agora - timedelta(days=90), agora, limite),
        ).fetchall()
    out = []
    for r in rows:
        ev = _fmt_evento(r[:-1])
        ev["n_convidados"] = r[-1]
        out.append(ev)
    return out


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


LOCAL_ONLINE = "Online"


def eh_online(local: str | None) -> bool:
    """True quando o local marcado foi o botão "reunião online" do formulário
    (ou alguém digitou exatamente isso à mão) — não tem endereço de verdade, então
    ninguém deve receber link de mapa pra ele."""
    return (local or "").strip().lower() == LOCAL_ONLINE.lower()


def link_maps(local: str | None) -> str | None:
    """URL do Google Maps pro local do evento (texto livre — endereço, nome do
    lugar etc.). None se não tiver local — ou se for reunião online, que não tem
    endereço nenhum pra mostrar no mapa — pra quem for montar mensagem pular a
    linha inteira em vez de mandar um link vazio ou sem sentido."""
    local = (local or "").strip()
    if not local or eh_online(local):
        return None
    from urllib.parse import quote
    return "https://www.google.com/maps/search/?api=1&query=" + quote(local)


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


# ---------- calendário do mês (pro portal) ----------

def eventos_mes(pool, conta_id: int, ano: int, mes: int) -> list[dict]:
    """Todos os eventos ativos que caem no mês (Brasília). Pra pintar o calendário."""
    de = datetime(ano, mes, 1, tzinfo=BRT)
    ate = datetime(ano + 1, 1, 1, tzinfo=BRT) if mes == 12 else datetime(ano, mes + 1, 1, tzinfo=BRT)
    return listar_eventos(pool, conta_id, de, ate)


# ---------- config do lembrete (opt-in) + feed .ics assinável ----------

def get_config(pool, conta_id: int) -> dict:
    """Config do lembrete da conta (cria defaults na memória se ainda não salvou)."""
    with pool.connection() as c:
        r = c.execute(
            "select lembrete_ativo, hora_resumo, aviso_antes_min, feed_token, resumo_ativo, "
            "avisar_convidados, enviar_confirmacao from agenda_config where conta_id=%s",
            (conta_id,)).fetchone()
    if not r:
        return {"lembrete_ativo": False, "resumo_ativo": False, "hora_resumo": 7,
                "aviso_antes_min": None, "feed_token": None, "avisar_convidados": True,
                "enviar_confirmacao": True}
    return {"lembrete_ativo": bool(r[0]), "hora_resumo": r[1],
            "aviso_antes_min": r[2], "feed_token": r[3], "resumo_ativo": bool(r[4]),
            "avisar_convidados": bool(r[5]), "enviar_confirmacao": bool(r[6])}


def salvar_config(pool, conta_id: int, *, resumo_ativo: bool, hora_resumo: int,
                  aviso_antes_min: int | None, avisar_convidados: bool = True,
                  enviar_confirmacao: bool = True) -> None:
    """Grava (upsert) a config do lembrete. resumo_ativo liga o "resumo do dia";
    aviso_antes_min (None = desligado) liga o "aviso antes"; avisar_convidados
    estende o "aviso antes" pra quem confirmou presença (só importa quando
    aviso_antes_min também está ligado); enviar_confirmacao liga a resposta
    automática pro convidado quando ele responde ao convite (independe dos
    outros dois). lembrete_ativo é o opt-in geral, derivado (ligado se qualquer
    um dos dois primeiros estiver)."""
    hora = hora_resumo if 0 <= (hora_resumo or 0) <= 23 else 7
    lembrete_ativo = bool(resumo_ativo) or (aviso_antes_min is not None)
    with pool.connection() as c:
        c.execute(
            """insert into agenda_config
                 (conta_id, lembrete_ativo, resumo_ativo, hora_resumo, aviso_antes_min,
                  avisar_convidados, enviar_confirmacao, atualizado_em)
               values (%s,%s,%s,%s,%s,%s,%s, now())
               on conflict (conta_id) do update set
                 lembrete_ativo     = excluded.lembrete_ativo,
                 resumo_ativo       = excluded.resumo_ativo,
                 hora_resumo        = excluded.hora_resumo,
                 aviso_antes_min    = excluded.aviso_antes_min,
                 avisar_convidados  = excluded.avisar_convidados,
                 enviar_confirmacao = excluded.enviar_confirmacao,
                 atualizado_em      = now()""",
            (conta_id, lembrete_ativo, bool(resumo_ativo), hora, aviso_antes_min,
             bool(avisar_convidados), bool(enviar_confirmacao)))
        c.commit()


def garantir_feed_token(pool, conta_id: int) -> str:
    """Devolve o token do feed .ics da conta, criando um (e a linha de config) se preciso.
    O token é o segredo que deixa o link .ics ser público sem expor a conta."""
    import secrets
    cfg = get_config(pool, conta_id)
    if cfg.get("feed_token"):
        return cfg["feed_token"]
    token = secrets.token_urlsafe(18)
    with pool.connection() as c:
        c.execute(
            """insert into agenda_config (conta_id, feed_token) values (%s,%s)
               on conflict (conta_id) do update set feed_token =
                 coalesce(agenda_config.feed_token, excluded.feed_token)""",
            (conta_id, token))
        r = c.execute("select feed_token from agenda_config where conta_id=%s",
                      (conta_id,)).fetchone()
        c.commit()
    return r[0]


def conta_por_feed_token(pool, token: str) -> int | None:
    """Acha a conta dona de um feed .ics pelo token (rota pública do .ics)."""
    token = (token or "").strip()
    if not token:
        return None
    with pool.connection() as c:
        r = c.execute("select conta_id from agenda_config where feed_token=%s",
                      (token,)).fetchone()
    return r[0] if r else None


def eventos_para_feed(pool, conta_id: int) -> list[dict]:
    """Eventos que vão no feed .ics: dos últimos 30 dias em diante (histórico curto
    + tudo que vem), com teto pra não estourar."""
    with pool.connection() as c:
        rows = c.execute(
            "select " + _COLS + " from eventos_agenda "
            "where conta_id=%s and status='ativo' and inicio >= %s "
            "order by inicio limit 500",
            (conta_id, agora_brt() - timedelta(days=30))).fetchall()
    return [_fmt_evento(r) for r in rows]
