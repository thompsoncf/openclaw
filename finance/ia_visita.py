"""A IA da regra por número MARCANDO a visita ao espaço (migração 390, etapa 2).

POR QUE EXISTE. Na Prime, quem visitou o espaço fechou 10,5%; quem não visitou,
cerca de 1,3%. A visita é o que mais separa quem fecha, e marcar era o passo que
dependia de gente: a IA da etapa 1 pegava a preferência e chamava a Jacqueline. Aqui
ela marca sozinha — mas só dentro de uma grade tirada das visitas que deram certo, e
conferindo tudo o que um humano conferiria antes de prometer um horário.

O QUE A IA CONFERE ANTES DE MARCAR (`cabe`)
  * a GRADE do chip (dia × hora, com meia hora aceita: 9h30 cabe se 9h está na grade);
  * a antecedência: de 3 horas a 14 dias;
  * a AGENDA inteira da conta (`agenda.conflitos`): outra visita, festa, pré-reserva,
    compromisso — com os 30 minutos de folga depois da visita;
  * a FESTA do dia: nada nas 3 horas antes de uma festa, e o horário "só sem festa"
    (19h) só num dia sem festa nenhuma;
  * o que já foi COMBINADO NAS CONVERSAS e não está na agenda (`combinado_em_conversa`):
    se um vendedor escreveu "visita 01/10 às 17h" pra outro cliente, a IA não marca
    em cima — chama a anfitriã, que decide.

E MARCA SOB TRAVA (`marcar`): uma marcação por vez na conta, conferindo tudo DE NOVO
dentro da trava. Duas conversas escolhendo o mesmo horário no mesmo segundo não
viram duas visitas no mesmo horário.

O CAMINHO É O DE SEMPRE. `cockpit.agendar_visita` cria o compromisso, liga no card,
move o lead pra "qualificado" e gera o convite .ics; `cockpit.remarcar_visita` move a
MESMA visita. A visita conta pro dono do lead (o zaq teste — "tudo conta pro zaq
teste", decisão do dono), e quem recebe o cliente é a anfitriã, avisada na hora.

DEPOIS DE MARCAR (`rodar`, no relógio do poller de web/app.py): véspera às 18h
("1 confirma, 2 remarca"), 2h antes, e, nos horários que pedem confirmação no dia
(15h/16h, 57% de comparecimento), a pergunta sai às 9h do próprio dia. Silêncio NÃO
cancela: a anfitriã é avisada de que ninguém confirmou. Falta marcada na agenda vira
um "sentimos sua falta, quer remarcar?". Tudo pelo chip da conversa.
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone

from finance import agenda as ag

_log = logging.getLogger(__name__)

_LOCK = 771171           # o relógio da confirmação (vizinho das travas da clínica)
_LOCK_MARCAR = 771172    # + a conta: uma marcação de visita por vez

#: os estados de um horário da grade
OK, CONF_DIA, SEM_FESTA = "ok", "conf_dia", "sem_festa"
ESTADOS = (OK, CONF_DIA, SEM_FESTA)
ROTULO_ESTADO = {OK: "✓", CONF_DIA: "conf.", SEM_FESTA: "s/ festa"}

#: A GRADE DE FÁBRICA, tirada das visitas da Prime desde agosto (mockup, versão 5):
#: manhã (8h–11h) 92% vieram; 12h–14h, 25% (fora); 15h–16h, 57% (só confirmando no
#: dia); 17h–18h, 89%. Segunda só fim de tarde; sábado só de manhã (à tarde tem
#: festa); domingo só a pedido — a IA chama a anfitriã. Chave = dia da semana do
#: Python (0 = segunda), valor = {hora: estado}.
_TER_A_SEX = {"9": OK, "10": OK, "11": OK, "15": CONF_DIA, "16": CONF_DIA,
              "17": OK, "18": OK, "19": SEM_FESTA}
GRADE_PADRAO = {"0": {"17": OK, "18": OK}, "1": dict(_TER_A_SEX), "2": dict(_TER_A_SEX),
                "3": dict(_TER_A_SEX), "4": dict(_TER_A_SEX),
                "5": {"9": OK, "10": OK, "11": OK}, "6": {}}
HORAS_DA_GRADE = range(8, 21)       # o que a tela deixa marcar
DIAS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")

#: a festa sem hora de fim ocupa isto (a festa da Prime vira a noite)
_FESTA_SEM_FIM_H = 8


# ------------------------------------------------------------------ config

def config(c, regra: dict | None) -> dict | None:
    """A parte "visita" da regra, ou None quando a IA não marca (chave desligada,
    banco sem a 390). Savepoint: roda dentro do atendimento."""
    if not regra or not regra.get("id"):
        return None
    try:
        with c.transaction():
            r = c.execute("""select visita_marca, visita_anfitria_id, visita_grade, visita_dur_min,
                                    visita_folga_min, visita_antes_festa_h, visita_min_h,
                                    visita_max_dias
                               from chip_regra where id=%s""", (regra["id"],)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not r or not r[0]:
        return None
    return {"anfitria_id": r[1], "grade": grade_valida(r[2]) or GRADE_PADRAO,
            "dur": int(r[3] or 60), "folga": int(r[4] or 0), "antes_festa_h": int(r[5] or 0),
            "min_h": int(r[6] or 0), "max_dias": int(r[7] or 14)}


def config_tela(c, regra_id: int | None) -> dict:
    """O que a tela mostra — com a chave desligada também (e a grade de fábrica
    quando a regra ainda não tem a sua)."""
    base = {"marca": False, "anfitria_id": None, "grade": GRADE_PADRAO, "dur": 60, "folga": 30,
            "antes_festa_h": 3, "min_h": 3, "max_dias": 14}
    if not regra_id:
        return base
    try:
        with c.transaction():
            r = c.execute("""select visita_marca, visita_anfitria_id, visita_grade, visita_dur_min,
                                    visita_folga_min, visita_antes_festa_h, visita_min_h,
                                    visita_max_dias
                               from chip_regra where id=%s""", (regra_id,)).fetchone()
    except Exception:  # noqa: BLE001
        return base
    if not r:
        return base
    return {"marca": bool(r[0]), "anfitria_id": r[1], "grade": grade_valida(r[2]) or GRADE_PADRAO,
            "dur": int(r[3]), "folga": int(r[4]), "antes_festa_h": int(r[5]), "min_h": int(r[6]),
            "max_dias": int(r[7])}


def grade_do_form(form_get) -> dict:
    """A grade que veio da tela: campos g_<dia>_<hora> com o estado (ou vazio)."""
    return {str(d): {str(h): form_get(f"g_{d}_{h}") for h in HORAS_DA_GRADE
                     if form_get(f"g_{d}_{h}") in ESTADOS}
            for d in range(7)}


def salvar(c, conta_id: int, chip_id: int, f: dict) -> str | None:
    """Grava a parte "visita" da regra (depois do `chip_regra.salvar`). Devolve o erro
    ou None. A anfitriã tem que ser desta empresa e ativa."""
    anf = None
    try:
        anf = int(f.get("visita_anfitria_id") or 0) or None
    except (TypeError, ValueError):
        anf = None
    if anf and not c.execute("select 1 from membros where id=%s and conta_id=%s and ativo",
                             (anf, conta_id)).fetchone():
        return "Escolha a anfitriã entre as pessoas da equipe desta empresa."
    marca = bool(f.get("visita_marca"))
    grade = grade_valida(f.get("visita_grade")) or GRADE_PADRAO
    if marca and not any(grade.values()):
        return "Pra IA marcar visita, a grade precisa de pelo menos um horário."

    def _n(k, lo, hi, padrao):
        try:
            return max(lo, min(hi, int(f.get(k))))
        except (TypeError, ValueError):
            return padrao
    import json
    c.execute("""update chip_regra set visita_marca=%s, visita_anfitria_id=%s, visita_grade=%s::jsonb,
                        visita_dur_min=%s, visita_folga_min=%s, visita_antes_festa_h=%s,
                        visita_min_h=%s, visita_max_dias=%s
                  where conta_id=%s and chip_id=%s""",
              (marca, anf, json.dumps(grade), _n("visita_dur_min", 15, 240, 60),
               _n("visita_folga_min", 0, 120, 30), _n("visita_antes_festa_h", 0, 12, 3),
               _n("visita_min_h", 1, 72, 3), _n("visita_max_dias", 1, 60, 14), conta_id, chip_id))
    return None


def grade_valida(g) -> dict | None:
    """Normaliza o que vem do banco ou da tela: {"0".."6": {"8".."20": estado}}."""
    if not isinstance(g, dict):
        return None
    out = {}
    for d in range(7):
        horas = g.get(str(d)) or {}
        if not isinstance(horas, dict):
            horas = {}
        out[str(d)] = {str(int(h)): e for h, e in horas.items()
                       if str(h).isdigit() and int(h) in HORAS_DA_GRADE and e in ESTADOS}
    return out


def estado(grade: dict, ini: datetime) -> str | None:
    """O estado do horário na grade, ou None (fora dela). Meia hora cabe na hora
    cheia de antes: 9h30 vale se 9h está na grade — é o "aceita meia hora" do dono."""
    loc = ini.astimezone(ag.BRT)
    if loc.minute not in (0, 30) or loc.second:
        return None
    return (grade.get(str(loc.weekday())) or {}).get(str(loc.hour))


# ------------------------------------------------------------------ o que ocupa

def _festas(c, conta_id: int, ini: datetime, fim: datetime) -> list[tuple[datetime, datetime]]:
    """As festas (reservadas ou seguradas) que tocam a janela. Festa é `tipo_evento`
    preenchido ou data pré-reservada — nunca a visita (título "Visita…", sem tipo)."""
    rows = c.execute(
        """select inicio, fim from eventos_agenda
            where conta_id=%s and status in ('ativo','pre_reservado')
              and (tipo_evento is not null or status='pre_reservado')
              and inicio < %s and coalesce(fim, inicio + make_interval(hours => %s)) > %s""",
        (conta_id, fim, _FESTA_SEM_FIM_H, ini)).fetchall()
    return [(a, b or a + timedelta(hours=_FESTA_SEM_FIM_H)) for a, b in rows]


def combinado_em_conversa(c, conta_id: int, ini: datetime, conversa_id: int | None) -> list[int]:
    """Conversas (de OUTROS clientes) em que alguém combinou visita neste dia e hora,
    nos últimos 21 dias. É a conferência que o dono pediu ("é bom ver também o que já
    foi marcado nas conversas"): o vendedor que combinou pelo celular e não pôs na
    agenda. Conservador de propósito — só casa com o dia E a hora escritos."""
    loc = ini.astimezone(ag.BRT)
    dia = rf"\m0?{loc.day}/0?{loc.month}\M"
    hora = (rf"\m{loc.hour}\s*(h|hs|horas|:00)" if loc.minute == 0
            else rf"\m{loc.hour}\s*(h|:)\s*30")
    rows = c.execute(
        """select distinct m.conversa_id from mensagens m
             join conversas cv on cv.id = m.conversa_id
            where cv.conta_id=%s and m.criado_em > now() - interval '21 days'
              and m.conversa_id is distinct from %s
              and m.texto ~* 'visit' and m.texto ~ %s and m.texto ~* %s
            limit 5""", (conta_id, conversa_id, dia, hora)).fetchall()
    return [r[0] for r in rows]


def cabe(pool, conta_id: int, cfg: dict, ini: datetime, agora: datetime, *,
         conversa_id: int | None = None, ignorar_evento: int | None = None) -> tuple[bool, str]:
    """(cabe?, motivo). Motivos: fora_da_grade, cedo, longe, festa, ocupado, combinado."""
    est = estado(cfg["grade"], ini)
    if not est:
        return False, "fora_da_grade"
    if ini < agora + timedelta(hours=cfg["min_h"]):
        return False, "cedo"
    if ini > agora + timedelta(days=cfg["max_dias"]):
        return False, "longe"
    fim = ini + timedelta(minutes=cfg["dur"] + cfg["folga"])
    loc = ini.astimezone(ag.BRT)
    dia_ini = datetime(loc.year, loc.month, loc.day, tzinfo=ag.BRT)
    with pool.connection() as c:
        # a festa: nada nas N horas antes dela nem durante; "só sem festa" = dia limpo
        festas = _festas(c, conta_id, dia_ini, dia_ini + timedelta(days=1))
        for (fi, ff) in festas:
            if ini < ff and fim > fi - timedelta(hours=cfg["antes_festa_h"]):
                return False, "festa"
        if est == SEM_FESTA and festas:
            return False, "festa"
        if combinado_em_conversa(c, conta_id, ini, conversa_id):
            return False, "combinado"
    if ag.conflitos(pool, conta_id, ini, fim, ignorar_id=ignorar_evento):
        return False, "ocupado"
    return True, est


def ofertas(pool, conta_id: int, cfg: dict, agora: datetime, n: int = 3, *,
            conversa_id: int | None = None, ignorar_evento: int | None = None) -> list[datetime]:
    """Até `n` horários livres, um por dia, nos próximos dias. Hora cheia sempre (a
    meia hora é o cliente que propõe); o horário que pede confirmação no dia só entra
    quando o dia não tem outro."""
    out: list[datetime] = []
    loc = agora.astimezone(ag.BRT)
    for k in range(cfg["max_dias"] + 1):
        if len(out) >= n:
            break
        d = (loc + timedelta(days=k)).date()
        horas = cfg["grade"].get(str(d.weekday())) or {}
        ordem = sorted(horas, key=lambda h: (horas[h] == CONF_DIA, int(h)))
        for h in ordem:
            ini = datetime(d.year, d.month, d.day, int(h), tzinfo=ag.BRT)
            ok, _ = cabe(pool, conta_id, cfg, ini, agora, conversa_id=conversa_id,
                         ignorar_evento=ignorar_evento)
            if ok:
                out.append(ini)
                break
    return out


_DIA_SEMANA = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")


def fmt(ini: datetime) -> str:
    """"quinta 01/10 às 17h" / "sábado 03/10 às 9h30"."""
    loc = ini.astimezone(ag.BRT)
    h = f"{loc.hour}h" + (f"{loc.minute:02d}" if loc.minute else "")
    return f"{_DIA_SEMANA[loc.weekday()]} {loc:%d/%m} às {h}"


LETRAS = "ABC"


def texto_ofertas(horarios: list[datetime]) -> str:
    return "\n".join(f"{LETRAS[i]}) {fmt(x)}" for i, x in enumerate(horarios[:len(LETRAS)]))


def guardar_ofertas(c, conta_id: int, conversa_id: int, horarios: list[datetime]) -> None:
    try:
        with c.transaction():
            c.execute("""insert into ia_ofertas (conversa_id, conta_id, horarios) values (%s,%s,%s)
                         on conflict (conversa_id) do update set horarios=excluded.horarios,
                                                                 criado_em=now()""",
                      (conversa_id, conta_id, horarios))
    except Exception:  # noqa: BLE001
        pass


_RE_LETRA = re.compile(r"^\s*(?:letra\s+|op[çc][ãa]o\s+)?([abc])\s*[).!,]?\s*(?:por favor|pf)?\s*$", re.I)


def horario_da_letra(c, conta_id: int, conversa_id: int, texto: str | None) -> datetime | None:
    """O cliente respondeu só "B"? Devolve o horário B da última oferta (até 3 dias)."""
    m = _RE_LETRA.match(texto or "")
    if not m:
        return None
    try:
        with c.transaction():
            r = c.execute("""select horarios from ia_ofertas where conversa_id=%s and conta_id=%s
                               and criado_em > now() - interval '3 days'""",
                          (conversa_id, conta_id)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    i = LETRAS.index(m.group(1).upper())
    return r[0][i] if r and r[0] and len(r[0]) > i else None


# ------------------------------------------------------------------ a visita viva

def visita_viva(c, conta_id: int, lead_id: int) -> dict | None:
    """A visita que a IA marcou e ainda vai acontecer (ou aconteceu há pouco)."""
    try:
        with c.transaction():
            r = c.execute(
                """select v.evento_id, e.inicio, v.confirmado_em, v.pede_remarcar_em,
                          v.remarcacoes, v.vespera_em, v.duas_horas_em, v.conf_no_dia
                     from ia_visitas v join eventos_agenda e on e.id = v.evento_id
                    where v.conta_id=%s and v.prospeccao_id=%s and e.status='ativo'
                      and e.inicio > now() - interval '2 hours'
                    order by e.inicio desc limit 1""", (conta_id, lead_id)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not r:
        return None
    return {"evento_id": r[0], "inicio": r[1], "confirmado_em": r[2], "pede_remarcar_em": r[3],
            "remarcacoes": int(r[4] or 0), "vespera_em": r[5], "duas_horas_em": r[6],
            "conf_no_dia": bool(r[7])}


MAX_REMARCACOES = 2


def _nome(c, conta_id: int, membro_id) -> str:
    if not membro_id:
        return ""
    r = c.execute("select coalesce(nullif(nome,''), '') from membros where id=%s and conta_id=%s",
                  (membro_id, conta_id)).fetchone()
    from finance.voltar_a_chamar import primeiro_nome
    n = primeiro_nome(r[0]) if r else ""
    return n[:1].upper() + n[1:].lower() if n else ""


def marcar(pool, conta_id: int, regra: dict, cfg: dict, lead_id: int, conversa_id: int | None,
           ini: datetime, agora: datetime, *, quem: str = "O cliente") -> dict:
    """Marca (ou remarca a MESMA) visita, sob trava e conferindo tudo de novo.

    Devolve {ok, motivo?, texto?, evento_id?, remarcou?}. `texto` é a confirmação que
    vai pro cliente (quem manda é o agente, pelo chip da conversa)."""
    from finance import cockpit as ck
    from finance import chip_regra as _cr
    with pool.connection() as lk:
        # trava de SESSÃO numa conexão só dela (o mesmo desenho do relógio da clínica):
        # os passos abaixo abrem as próprias conexões, e a trava tem que durar todos
        lk.execute("select pg_advisory_lock(%s::int, %s::int)", (_LOCK_MARCAR, int(conta_id)))
        try:
            with pool.connection() as c:
                viva = visita_viva(c, conta_id, lead_id)
                c.commit()
            if viva and viva["remarcacoes"] >= MAX_REMARCACOES:
                return {"ok": False, "motivo": "remarcacoes"}
            ok, motivo = cabe(pool, conta_id, cfg, ini, agora, conversa_id=conversa_id,
                              ignorar_evento=viva["evento_id"] if viva else None)
            if not ok:
                return {"ok": False, "motivo": motivo}
            loc = ini.astimezone(ag.BRT)
            data, hora = loc.strftime("%Y-%m-%d"), loc.strftime("%H:%M")
            conf_dia = motivo == CONF_DIA
            if viva:
                r = ck.remarcar_visita(pool, conta_id, None, viva["evento_id"], data=data,
                                       hora=hora, avisar_cliente=False, gestao=True)
                if not r.get("ok"):
                    return {"ok": False, "motivo": "falhou"}
                with pool.connection() as c:
                    c.execute("""update ia_visitas set remarcacoes=remarcacoes+1, conf_no_dia=%s,
                                        vespera_em=null, duas_horas_em=null, confirmado_em=null,
                                        pede_remarcar_em=null, sem_resposta_em=null, falta_em=null
                                  where evento_id=%s and conta_id=%s""",
                              (conf_dia, viva["evento_id"], conta_id))
                    c.commit()
                evento_id = viva["evento_id"]
            else:
                r = ck.agendar_visita(pool, conta_id, regra["membro_id"], lead_id, data=data,
                                      hora=hora, dur_min=cfg["dur"], lembrete_min=None,
                                      avisar_cliente=False)
                if not r.get("ok"):
                    return {"ok": False, "motivo": "falhou"}
                evento_id = r["evento_id"]
                with pool.connection() as c:
                    anf = _nome(c, conta_id, cfg.get("anfitria_id"))
                    c.execute("""update eventos_agenda set marcado_por='ia',
                                        descricao = coalesce(descricao,'') || %s
                                  where id=%s and conta_id=%s""",
                              (f"\nMarcada pela IA. Recebe: {anf}." if anf else "\nMarcada pela IA.",
                               evento_id, conta_id))
                    c.execute("""insert into ia_visitas (evento_id, conta_id, prospeccao_id,
                                                         conversa_id, conf_no_dia)
                                 values (%s,%s,%s,%s,%s) on conflict (evento_id) do nothing""",
                              (evento_id, conta_id, lead_id, conversa_id, conf_dia))
                    c.commit()
        finally:
            lk.execute("select pg_advisory_unlock(%s::int, %s::int)", (_LOCK_MARCAR, int(conta_id)))
    with pool.connection() as c:
        anf = _nome(c, conta_id, cfg.get("anfitria_id"))
    texto = (("Remarcado! ✅ " if viva else "Marcado! ✅ ") + fmt(ini)
             + (f", com a {anf}" if anf else "") + "."
             + (f"\n📍 {r.get('local')}" if r.get("local") else "")
             + (f"\n📎 Adicione ao seu calendário: {r['ics_url']}" if r.get("ics_url") else "")
             + ("\nNo dia eu te chamo pra confirmar 😊" if conf_dia else ""))
    if cfg.get("anfitria_id"):
        _cr.notificar(pool, conta_id, cfg["anfitria_id"],
                      ("🔁 A IA remarcou uma visita" if viva else "📅 A IA marcou uma visita pra você"),
                      f"{quem}, {fmt(ini)}.", f"/cockpit/lead/{lead_id}")
    return {"ok": True, "texto": texto, "evento_id": evento_id, "remarcou": bool(viva)}


# ------------------------------------------------------------------ a resposta 1/2

def responder_confirmacao(c, conta_id: int, lead_id: int, texto: str | None) -> tuple[str | None, str | None]:
    """O cliente respondeu ao "1 confirma, 2 remarca"? Devolve (o_que, texto_pra_ele):
    ("confirmou", "…"), ("remarcar", None) — a IA oferece outros horários —, ou
    (None, None) quando não era isso. Só vale com a pergunta feita e sem resposta."""
    from finance.clinica_agenda import _RE_REMARCAR, _RE_SIM
    v = visita_viva(c, conta_id, lead_id)
    if not v or not (v["vespera_em"] or v["duas_horas_em"]) or v["confirmado_em"] \
            or v["pede_remarcar_em"]:
        return None, None
    if _RE_SIM.match(texto or ""):
        c.execute("update ia_visitas set confirmado_em=now() where evento_id=%s and conta_id=%s",
                  (v["evento_id"], conta_id))
        return "confirmou", f"Confirmadíssimo! 🎉 Te esperamos {fmt(v['inicio'])}."
    if _RE_REMARCAR.match(texto or ""):
        c.execute("update ia_visitas set pede_remarcar_em=now() where evento_id=%s and conta_id=%s",
                  (v["evento_id"], conta_id))
        return "remarcar", None
    return None, None


# ------------------------------------------------------------------ o relógio

def texto_vespera(ini: datetime, nome: str, agora: datetime, empresa: str) -> str:
    loc, hoje = ini.astimezone(ag.BRT), agora.astimezone(ag.BRT).date()
    quando = "Hoje" if loc.date() == hoje else "Amanhã" if loc.date() == hoje + timedelta(days=1) \
        else fmt(ini).split(" às ")[0].capitalize()
    h = f"{loc.hour}h" + (f"{loc.minute:02d}" if loc.minute else "")
    return (f"Oi{', ' + nome if nome else ''}! {quando} às {h} é a sua visita ao {empresa} 😊 "
            "Confirma? Responda 1 para confirmar ou 2 se precisar remarcar.")


def texto_duas_horas(ini: datetime, empresa: str, local: str, confirmado: bool) -> str:
    loc = ini.astimezone(ag.BRT)
    h = f"{loc.hour}h" + (f"{loc.minute:02d}" if loc.minute else "")
    return (f"Daqui a pouco, às {h}, te esperamos no {empresa}! 📍 {local}"
            + ("" if confirmado else "\nConsegue vir? Responda 1 para confirmar ou 2 para remarcar."))


TEXTO_FALTA = ("Oi! Sentimos sua falta na visita 😕 Quer marcar outro dia pra conhecer o espaço? "
               "Me diz o que fica melhor pra você.")


def _momento_vespera(ini: datetime, conf_no_dia: bool) -> datetime:
    loc = ini.astimezone(ag.BRT)
    if conf_no_dia:
        return datetime(loc.year, loc.month, loc.day, 9, tzinfo=ag.BRT)
    d = loc.date() - timedelta(days=1)
    return datetime(d.year, d.month, d.day, 18, tzinfo=ag.BRT)


def _mandar(c, conta_id: int, conversa_id: int, texto: str) -> bool:
    """Pelo chip da conversa, gravado como fala da IA (autor bot)."""
    from finance import agente
    r = c.execute("""select coalesce(p.whatsapp, p.telefone, cv.contato_ref)
                       from conversas cv left join prospeccao p
                            on p.id = cv.prospeccao_id and p.conta_id = cv.conta_id
                      where cv.id=%s and cv.conta_id=%s""", (conversa_id, conta_id)).fetchone()
    if not r or not r[0]:
        return False
    res = agente._mandar(c, conta_id, "whatsapp", r[0], texto, conversa_id) or {}
    if not res.get("ok"):
        return False
    agente._add_bot_msg(c, conversa_id, "whatsapp", texto, res.get("sid"))
    return True


def _passo(pool, conta_id: int, v: dict, coluna: str, texto: str) -> bool:
    """Reivindica o passo (a coluna nula vira agora) ANTES de mandar, e devolve a
    reivindicação se o envio falhar — o próximo ciclo tenta de novo. É o mesmo desenho
    da véspera da clínica: dois workers nunca mandam a mesma pergunta duas vezes."""
    with pool.connection() as c:
        pegou = c.execute(f"update ia_visitas set {coluna}=now() where evento_id=%s and {coluna} is null "
                          "returning evento_id", (v["evento_id"],)).fetchone()
        c.commit()
        if not pegou:
            return False
        ok = False
        try:
            ok = _mandar(c, conta_id, v["conversa_id"], texto)
            c.commit()
        except Exception as e:  # noqa: BLE001
            _log.warning("ia_visita: envio falhou (evento %s): %s", v["evento_id"], e)
            c.rollback()
        if not ok:
            c.execute(f"update ia_visitas set {coluna}=null where evento_id=%s", (v["evento_id"],))
            c.commit()
        return ok


def rodar(pool, agora: datetime | None = None) -> dict:
    """Um ciclo do relógio: véspera, 2h antes, "ninguém confirmou" e a falta.
    Janelas, e não minutos exatos: o poller anda de ~2 em ~2 minutos e atrasa."""
    agora = agora or datetime.now(timezone.utc)
    out = {"vesperas": 0, "duas_horas": 0, "sem_resposta": 0, "faltas": 0}
    with pool.connection() as lk:
        try:
            if not lk.execute("select pg_try_advisory_lock(%s)", (_LOCK,)).fetchone()[0]:
                return out
        except Exception:  # noqa: BLE001 — banco sem a 390
            return out
        try:
            try:
                with pool.connection() as c:
                    rows = c.execute(
                        """select v.evento_id, v.conta_id, v.prospeccao_id, v.conversa_id,
                                  v.conf_no_dia, v.vespera_em, v.duas_horas_em, v.confirmado_em,
                                  v.pede_remarcar_em, v.sem_resposta_em, v.falta_em, v.criado_em,
                                  e.inicio, e.desfecho, e.local,
                                  coalesce(nullif(p.contato,''), nullif(p.empresa,''), '')
                             from ia_visitas v
                             join eventos_agenda e on e.id = v.evento_id and e.conta_id = v.conta_id
                             left join prospeccao p on p.id = v.prospeccao_id and p.conta_id = v.conta_id
                            where e.status='ativo' and v.conversa_id is not null
                              and e.inicio between %s and %s""",
                        (agora - timedelta(days=3), agora + timedelta(days=2))).fetchall()
            except Exception:  # noqa: BLE001
                return out
            from finance.cockpit import endereco_empresa
            from finance.voltar_a_chamar import primeiro_nome
            from finance import chip_regra as _cr
            for r in rows:
                v = dict(zip(("evento_id", "conta_id", "lead", "conversa_id", "conf_no_dia",
                              "vespera_em", "duas_horas_em", "confirmado_em", "pede_remarcar_em",
                              "sem_resposta_em", "falta_em", "criado_em", "inicio", "desfecho",
                              "local", "quem"), r))
                conta, ini = v["conta_id"], v["inicio"]
                try:
                    esp = endereco_empresa(pool, conta)
                    nome = primeiro_nome(v["quem"])
                    nome = nome[:1].upper() + nome[1:].lower() if nome else ""
                    if v["desfecho"] == "nao_realizado":
                        if not v["falta_em"] and _passo(pool, conta, v, "falta_em", TEXTO_FALTA):
                            out["faltas"] += 1
                        continue
                    if ini <= agora or v["pede_remarcar_em"]:
                        continue
                    momento = _momento_vespera(ini, v["conf_no_dia"])
                    if (not v["vespera_em"] and not v["confirmado_em"] and agora >= momento
                            and v["criado_em"] < momento and ini - agora > timedelta(hours=2, minutes=30)):
                        if _passo(pool, conta, v, "vespera_em",
                                  texto_vespera(ini, nome, agora, esp["nome"])):
                            out["vesperas"] += 1
                        continue
                    if not v["duas_horas_em"] and ini - agora <= timedelta(hours=2) \
                            and v["criado_em"] < ini - timedelta(hours=2):
                        if _passo(pool, conta, v, "duas_horas_em",
                                  texto_duas_horas(ini, esp["nome"],
                                                   v["local"] or esp["endereco"] or esp["nome"],
                                                   bool(v["confirmado_em"]))):
                            out["duas_horas"] += 1
                        continue
                    if (v["vespera_em"] and not v["confirmado_em"] and not v["sem_resposta_em"]
                            and ini - agora <= timedelta(minutes=90)):
                        with pool.connection() as c:
                            pegou = c.execute("""update ia_visitas set sem_resposta_em=now()
                                                  where evento_id=%s and sem_resposta_em is null
                                                  returning evento_id""", (v["evento_id"],)).fetchone()
                            anf = None
                            if pegou:
                                reg = _cr.regra_da_conversa(c, conta, v["conversa_id"])
                                anf = (config(c, reg) or {}).get("anfitria_id") if reg else None
                            c.commit()
                        if pegou and anf:
                            _cr.notificar(pool, conta, anf, "⏰ Visita sem confirmação",
                                          f"{v['quem'] or 'O cliente'} não confirmou a visita de "
                                          f"{fmt(ini)}. Continua marcada.",
                                          f"/cockpit/lead/{v['lead']}")
                            out["sem_resposta"] += 1
                except Exception as e:  # noqa: BLE001
                    _log.warning("ia_visita.rodar: evento %s: %s", v["evento_id"], e)
        finally:
            lk.execute("select pg_advisory_unlock(%s)", (_LOCK,))
    return out
