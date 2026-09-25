"""A agenda da clínica: o dia com uma coluna por profissional, a semana de um
profissional, o agendamento em 20 segundos e a confirmação na véspera.

Fase 2 do plano aprovado (docs/mockups/clinica_visao_geral.html, seção 10), com
as telas Agenda e Novo agendamento do protótipo. O cadastro (quem atende, onde,
quando) é da fase 1 (finance/clinica_config.py); aqui se marca em cima dele.

O ZAQ É A AGENDA (o Amigo saiu em 25/09/2026). O horário livre sai de um lugar só:
grade − bloqueios − agendamentos (`livres`). Duas recepcionistas marcando o mesmo
horário: o `pg_advisory_xact_lock` por profissional faz a segunda esperar a
primeira e reconferir — só uma passa.

OS SETE STATUS (`eventos_agenda.situacao`):
    agendado → confirmado → presente → atendimento → finalizado
    faltou · cancelou     (cancelado e falta não ocupam horário)

AS MENSAGENS NUNCA DIZEM O MOTIVO CLÍNICO: "sua consulta", "seu atendimento" —
nunca o nome do procedimento (LGPD, art. 11; regra 9 do resumo aprovado).
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, time, timedelta, timezone

from finance import clinica_config as cc
from finance import funil_regua as fr

_log = logging.getLogger("openclaw.clinica_agenda")
_BR = fr._UTC_BR
_LOCK = 771161          # vizinho do voltar a chamar (771160)
_LOCK_MARCAR = 771162   # + o id do profissional: uma marcação por vez em cada agenda

SITUACOES = (("agendado", "Agendado"), ("confirmado", "Confirmado"), ("presente", "Presente"),
             ("atendimento", "Em atendimento"), ("finalizado", "Finalizado"),
             ("faltou", "Faltou"), ("cancelou", "Cancelou"))
SIT_D = dict(SITUACOES)
PROXIMOS = {
    "agendado": ("confirmado", "presente", "faltou", "cancelou"),
    "confirmado": ("presente", "faltou", "cancelou"),
    "presente": ("atendimento", "finalizado"),
    "atendimento": ("finalizado",),
    "faltou": ("agendado",),        # marcou falta por engano
    "finalizado": (),
    "cancelou": (),
}
NAO_OCUPA = ("cancelou", "faltou")
ORIGENS = ("Instagram", "Indicação", "Google", "Já é paciente", "Outro")
_SEMANA = {1: "seg", 2: "ter", 3: "qua", 4: "qui", 5: "sex", 6: "sáb", 7: "dom"}
_RE_SIM = re.compile(r"^\s*(1|sim|confirm)", re.I)
_RE_REMARCAR = re.compile(r"^\s*(2|remarc|n[ãa]o\s+(vou|posso|consigo))", re.I)
_PALAVRA = {"consulta": "consulta", "retorno": "retorno", "sessao": "sessão"}


# ------------------------------------------------------------------ tempo

def local(dt: datetime) -> datetime:
    return (dt + _BR).replace(tzinfo=None)


def utc(dia: date, hora: time) -> datetime:
    return (datetime.combine(dia, hora) - _BR).replace(tzinfo=timezone.utc)


def dia_txt(dt_utc: datetime) -> str:
    loc = local(dt_utc)
    return f"{_SEMANA[loc.isoweekday()]} {loc:%d/%m}"


def hora_txt(dt_utc: datetime) -> str:
    return f"{local(dt_utc):%H:%M}"


def hoje_br(agora: datetime | None = None) -> date:
    return local(agora or datetime.now(timezone.utc)).date()


# ------------------------------------------------------------------ leitura

def _eventos(c, conta_id: int, de: datetime, ate: datetime, profissional_id: int | None = None) -> list[dict]:
    rows = c.execute(
        """select e.id, e.profissional_id, e.inicio, coalesce(e.fim, e.inicio + interval '30 minutes'),
                  e.situacao, coalesce(e.paciente_nome, e.titulo), e.paciente_fone, e.servico_id,
                  s.nome, s.cor, s.categoria, e.clinica_local_id, e.encaixe, e.origem,
                  e.confirmacao_enviada_em, e.confirmado_em, e.pede_remarcar_em, e.prospeccao_id,
                  coalesce(e.descricao, '')
             from eventos_agenda e
             left join servicos_catalogo s on s.id = e.servico_id and s.conta_id = e.conta_id
            where e.conta_id = %s and e.situacao is not null
              and e.inicio < %s and coalesce(e.fim, e.inicio) > %s"""
        + (" and e.profissional_id = %s" if profissional_id else "") + " order by e.inicio, e.id",
        (conta_id, ate, de) + ((profissional_id,) if profissional_id else ())).fetchall()
    return [{"id": r[0], "profissional_id": r[1], "inicio": r[2], "fim": r[3], "situacao": r[4],
             "paciente": r[5] or "Paciente", "fone": r[6] or "", "servico_id": r[7],
             "tipo": r[8] or "Atendimento", "cor": r[9] or cc.CORES[0], "categoria": r[10] or "",
             "local_id": r[11], "encaixe": r[12], "origem": r[13] or "",
             "confirmacao_enviada_em": r[14], "confirmado_em": r[15], "pede_remarcar_em": r[16],
             "lead": r[17], "observacao": r[18], "hora": hora_txt(r[2]), "fim_txt": hora_txt(r[3]),
             "sit_txt": SIT_D.get(r[4], r[4])} for r in rows]


def evento(c, conta_id: int, evento_id: int) -> dict | None:
    r = c.execute("select inicio from eventos_agenda where id=%s and conta_id=%s and situacao is not null",
                  (evento_id, conta_id)).fetchone()
    if not r:
        return None
    evs = [e for e in _eventos(c, conta_id, r[0] - timedelta(minutes=1), r[0] + timedelta(days=1))
           if e["id"] == evento_id]
    return evs[0] if evs else None


def ocupados(c, conta_id: int, profissional_id: int, de: datetime, ate: datetime,
             ignorar: int | None = None) -> list[tuple[datetime, datetime]]:
    return [(e["inicio"], e["fim"]) for e in _eventos(c, conta_id, de, ate, profissional_id)
            if e["situacao"] not in NAO_OCUPA and e["id"] != ignorar]


def livres(c, conta_id: int, profissional_id: int, servico_id: int, de: date, dias: int = 7,
           agora: datetime | None = None, limite: int | None = None,
           ignorar: int | None = None) -> list[dict]:
    """Horário livre de verdade: grade − bloqueios − o que já está marcado."""
    agora = agora or datetime.now(timezone.utc)
    ini, fim = utc(de, time(0)), utc(de + timedelta(days=dias), time(0))
    return cc.livres(c, conta_id, profissional_id, servico_id, de, dias, agora,
                     ocupados(c, conta_id, profissional_id, ini, fim, ignorar), limite)


def _profs_que_atendem(c, conta_id: int) -> list[dict]:
    return [p for p in cc.listar_profissionais(c, conta_id) if p["funcao"] != "Recepção, não atende"]


def _linhas(faixas_por_prof: dict, eventos: list[dict]) -> list[time]:
    """As linhas da grade do dia: de meia em meia hora dentro de qualquer faixa, mais
    o início de todo agendamento (encaixe fora da meia hora também aparece)."""
    marcas = set()
    for faixas in faixas_por_prof.values():
        for f in faixas:
            m = f["inicio"].hour * 60 + f["inicio"].minute
            fim = f["fim"].hour * 60 + f["fim"].minute
            while m < fim:
                marcas.add(time(m // 60, m % 60))
                m += cc.PASSO_MIN
    for e in eventos:
        marcas.add(local(e["inicio"]).time())
    return sorted(marcas)


def _celulas(linhas: list[time], faixas: list[dict], evs: list[dict], dia: date,
             agora: datetime) -> list[dict]:
    """Uma célula por linha: o agendamento que começa ali, "continua" (coberto por um
    anterior), "livre" (dentro da faixa, no futuro), "passou" ou "fora"."""
    out = []
    for h in linhas:
        ini = utc(dia, h)
        comecam = [e for e in evs if local(e["inicio"]).time() == h]
        ativos = [e for e in evs if e["situacao"] not in NAO_OCUPA]
        coberto = any(e["inicio"] < ini < e["fim"] for e in ativos)
        dentro = any(f["inicio"] <= h < f["fim"] for f in faixas)
        if comecam:
            out.append({"tipo": "ev", "evs": comecam, "hora": h})
        elif coberto:
            out.append({"tipo": "continua", "hora": h})
        elif dentro and ini > agora:
            out.append({"tipo": "livre", "hora": h})
        elif dentro:
            out.append({"tipo": "passou", "hora": h})
        else:
            out.append({"tipo": "fora", "hora": h})
    return out


def _ocupacao(faixas: list[dict], evs: list[dict]) -> int | None:
    aberto = sum((f["fim"].hour * 60 + f["fim"].minute) - (f["inicio"].hour * 60 + f["inicio"].minute)
                 for f in faixas)
    if not aberto:
        return None
    usado = sum(int((e["fim"] - e["inicio"]).total_seconds() // 60) for e in evs
                if e["situacao"] not in NAO_OCUPA)
    return min(100, round(100 * usado / aberto))


def dia(c, conta_id: int, data: date, agora: datetime, local_id: int | None = None) -> dict:
    grade, bloqueios = cc.listar_grade(c, conta_id), cc.listar_bloqueios(c, conta_id, data)
    profs = _profs_que_atendem(c, conta_id)
    evs = _eventos(c, conta_id, utc(data, time(0)), utc(data + timedelta(days=1), time(0)))
    faixas = {}
    for p in profs:
        fs = cc.faixas_do_dia(grade, bloqueios, p["id"], data)
        faixas[p["id"]] = [f for f in fs if not local_id or f["local_id"] == local_id]
    if local_id:
        evs = [e for e in evs if e["local_id"] in (None, local_id)]
    # só aparece coluna de quem atende hoje (ou tem agendamento hoje)
    colunas = [p for p in profs if faixas[p["id"]] or any(e["profissional_id"] == p["id"] for e in evs)]
    linhas = _linhas({p["id"]: faixas[p["id"]] for p in colunas}, evs)
    grade_dia = [{"prof": p, "faixas": faixas[p["id"]],
                  "celulas": _celulas(linhas, faixas[p["id"]],
                                      [e for e in evs if e["profissional_id"] == p["id"]], data, agora)}
                 for p in colunas]
    abertos = [f for p in colunas for f in faixas[p["id"]]]
    vivos = [e for e in evs if e["situacao"] not in ("finalizado", "cancelou", "faltou")]
    return {"data": data, "linhas": linhas, "colunas": grade_dia, "sem_ninguem": not colunas,
            "ocupacao": _ocupacao(abertos, evs),
            "confirmados": sum(1 for e in vivos if e["situacao"] != "agendado"),
            "a_confirmar": len(vivos),
            "faltas": sum(1 for e in evs if e["situacao"] == "faltou"),
            "remarcar": [e for e in evs if e["pede_remarcar_em"] and e["situacao"] == "agendado"]}


def semana(c, conta_id: int, profissional_id: int, segunda: date, agora: datetime) -> dict:
    grade, bloqueios = cc.listar_grade(c, conta_id), cc.listar_bloqueios(c, conta_id, segunda)
    dias = [segunda + timedelta(days=i) for i in range(7)]
    evs = _eventos(c, conta_id, utc(segunda, time(0)), utc(segunda + timedelta(days=7), time(0)),
                   profissional_id)
    por_dia = {d: cc.faixas_do_dia(grade, bloqueios, profissional_id, d) for d in dias}
    ev_dia = {d: [e for e in evs if local(e["inicio"]).date() == d] for d in dias}
    # domingo só entra se ele atende ou tem algo marcado
    mostrar = [d for d in dias if d.isoweekday() < 7 or por_dia[d] or ev_dia[d]]
    linhas = _linhas({d: por_dia[d] for d in mostrar}, evs)
    cols = [{"dia": d, "rotulo": f"{_SEMANA[d.isoweekday()]} {d:%d/%m}", "hoje": d == hoje_br(agora),
             "faixas": por_dia[d], "ocupacao": _ocupacao(por_dia[d], ev_dia[d]),
             "celulas": _celulas(linhas, por_dia[d], ev_dia[d], d, agora)} for d in mostrar]
    abertos = [f for d in mostrar for f in por_dia[d]]
    return {"segunda": segunda, "linhas": linhas, "colunas": cols,
            "ocupacao": _ocupacao(abertos, evs),
            "livres": sum(1 for col in cols for cel in col["celulas"] if cel["tipo"] == "livre"),
            "faltas": sum(1 for e in evs if e["situacao"] == "faltou")}


def buscar_pacientes(c, conta_id: int, termo: str, limite: int = 8) -> list[dict]:
    t = (termo or "").strip()
    if len(t) < 2:
        return []
    dig = re.sub(r"\D", "", t)
    rows = c.execute(
        r"""select id, coalesce(nullif(contato,''), empresa), coalesce(nullif(whatsapp,''), telefone, ''), status
             from prospeccao
            where conta_id = %s
              and (empresa ilike %s or contato ilike %s
                   or (length(%s) >= 4 and (regexp_replace(coalesce(whatsapp,''), '\D', '', 'g') like %s
                                         or regexp_replace(coalesce(telefone,''), '\D', '', 'g') like %s)))
            order by atualizado_em desc nulls last limit %s""",
        (conta_id, f"%{t}%", f"%{t}%", dig, f"%{dig}%", f"%{dig}%", limite)).fetchall()
    return [{"id": r[0], "nome": r[1], "fone": r[2], "status": r[3]} for r in rows]


# ------------------------------------------------------------------ escrita

def _digitos(fone: str | None) -> str:
    return re.sub(r"\D", "", fone or "")


def _lead_do_paciente(c, conta_id: int, lead_id: int | None, nome: str, fone: str) -> tuple[int | None, str, str, str | None]:
    """(lead_id, nome, fone, erro). Acha pelo id, pelo celular, ou cria o card."""
    if lead_id:
        r = c.execute("""select id, coalesce(nullif(contato,''), empresa), coalesce(nullif(whatsapp,''), telefone, '')
                           from prospeccao where id=%s and conta_id=%s""", (lead_id, conta_id)).fetchone()
        if not r:
            return None, "", "", "Paciente não encontrado."
        return r[0], r[1], r[2], None
    nome = (nome or "").strip()
    dig = _digitos(fone)
    if not nome:
        return None, "", "", "Informe o nome do paciente."
    if len(dig) < 10:
        return None, "", "", "Informe o celular com DDD."
    if not dig.startswith("55"):
        dig = "55" + dig
    r = c.execute(
        r"""select id, coalesce(nullif(contato,''), empresa) from prospeccao
             where conta_id=%s and (right(regexp_replace(coalesce(whatsapp,''), '\D', '', 'g'), 8) = %s
                                    or right(regexp_replace(coalesce(telefone,''), '\D', '', 'g'), 8) = %s)
             order by atualizado_em desc nulls last limit 1""", (conta_id, dig[-8:], dig[-8:])).fetchone()
    if r:
        return r[0], r[1] or nome, "+" + dig, None
    lid = c.execute(
        """insert into prospeccao (conta_id, empresa, contato, whatsapp, tipo, origem, temperatura,
                                   status, estagio)
           values (%s,%s,%s,%s,'pf','agenda_clinica','quente','novo','lead') returning id""",
        (conta_id, nome[:250], nome[:250], "+" + dig)).fetchone()[0]
    return lid, nome, "+" + dig, None


def _mover_card(c, conta_id: int, lead_id: int, membro_id: int | None) -> None:
    """Marcar consulta leva o card pra "Consulta agendada" (chave `qualificado`).
    Só pra frente: card em Plano de tratamento ou fechado fica onde está."""
    r = c.execute("select status from prospeccao where id=%s and conta_id=%s for update",
                  (lead_id, conta_id)).fetchone()
    if r and r[0] in ("novo", "contatado", "follow_up"):
        c.execute("update prospeccao set status='qualificado', atualizado_em=now() where id=%s and conta_id=%s",
                  (lead_id, conta_id))
        fr.registrar_movimento(c, conta_id, lead_id, r[0], "qualificado", "agenda", membro_id)


def _faixa_do_horario(c, conta_id: int, profissional_id: int, inicio: datetime) -> dict | None:
    loc = local(inicio)
    for f in cc.faixas_do_dia(cc.listar_grade(c, conta_id), cc.listar_bloqueios(c, conta_id, loc.date()),
                              profissional_id, loc.date()):
        if f["inicio"] <= loc.time() < f["fim"]:
            return f
    return None


def agendar(c, conta_id: int, *, profissional_id: int, servico_id: int, inicio: datetime,
            lead_id: int | None = None, nome: str = "", fone: str = "", origem: str = "",
            observacao: str = "", encaixe: bool = False, membro_id: int | None = None,
            agora: datetime | None = None) -> tuple[int | None, str | None]:
    """Marca. Devolve (evento_id, None) ou (None, erro pra tela). Não faz commit."""
    agora = agora or datetime.now(timezone.utc)
    prof = next((p for p in _profs_que_atendem(c, conta_id) if p["id"] == profissional_id), None)
    if not prof:
        return None, "Profissional não encontrado."
    if servico_id not in prof["tipos"]:
        return None, f"{prof['nome']} não faz esse atendimento (Configurar › Profissionais)."
    tipo = next((t for t in cc.listar_tipos(c, conta_id) if t["id"] == servico_id), None)
    if not tipo:
        return None, "Atendimento não encontrado."
    if inicio <= agora:
        return None, "Esse horário já passou."
    fim = inicio + timedelta(minutes=tipo["duracao_min"])
    # uma marcação por vez nesta agenda: a segunda espera e reconfere
    c.execute("select pg_advisory_xact_lock(%s::int, %s::int)", (_LOCK_MARCAR, int(profissional_id)))
    dia_ = local(inicio).date()
    livre = any(x["inicio"] == inicio for x in
                livres(c, conta_id, profissional_id, servico_id, dia_, 1, agora))
    faixa = _faixa_do_horario(c, conta_id, profissional_id, inicio)
    if not livre:
        if not encaixe:
            return None, "Esse horário não está livre. Escolha outro, ou marque como encaixe."
        if not faixa:
            return None, "Encaixe só dentro do horário de atendimento do profissional."
        usados = c.execute(
            """select count(*) from eventos_agenda where conta_id=%s and profissional_id=%s and encaixe
                  and situacao not in ('cancelou','faltou') and inicio >= %s and inicio < %s""",
            (conta_id, profissional_id, utc(dia_, time(0)), utc(dia_ + timedelta(days=1), time(0)))).fetchone()[0]
        limite = max((f["encaixes"] for f in cc.faixas_do_dia(
            cc.listar_grade(c, conta_id), cc.listar_bloqueios(c, conta_id, dia_), profissional_id, dia_)),
            default=0)
        if usados >= limite:
            return None, f"Os encaixes do dia já acabaram ({limite})."
    lid, nome_pac, fone_pac, erro = _lead_do_paciente(c, conta_id, lead_id, nome, fone)
    if erro:
        return None, erro
    _mover_card(c, conta_id, lid, membro_id)
    loc_id = faixa["local_id"] if faixa else None
    loc = next((x for x in cc.listar_locais(c, conta_id) if x["id"] == loc_id), None)
    eid = c.execute(
        """insert into eventos_agenda
             (conta_id, titulo, inicio, fim, local, descricao, tipo, prospeccao_id, profissional_id,
              servico_id, clinica_local_id, situacao, situacao_em, origem, paciente_nome, paciente_fone,
              encaixe, marcado_por, status)
           values (%s,%s,%s,%s,%s,%s,'empresa',%s,%s,%s,%s,'agendado',now(),%s,%s,%s,%s,'recepcao','ativo')
           returning id""",
        (conta_id, f"{nome_pac} · {tipo['nome']}"[:200], inicio, fim, loc["nome"] if loc else None,
         (observacao or "").strip()[:500] or None, lid, profissional_id, servico_id, loc_id,
         origem if origem in ORIGENS else None, nome_pac[:120], fone_pac, bool(encaixe and not livre))).fetchone()[0]
    return eid, None


def mudar_situacao(c, conta_id: int, evento_id: int, nova: str) -> str | None:
    ev = c.execute("select situacao from eventos_agenda where id=%s and conta_id=%s and situacao is not null for update",
                   (evento_id, conta_id)).fetchone()
    if not ev:
        return "Agendamento não encontrado."
    if nova not in PROXIMOS.get(ev[0], ()):
        return f"De {SIT_D.get(ev[0], ev[0])} não dá pra ir pra {SIT_D.get(nova, nova)}."
    c.execute(
        """update eventos_agenda
              set situacao=%s, situacao_em=now(),
                  status = case when %s = 'cancelou' then 'cancelado' else 'ativo' end,
                  confirmado_em = case when %s = 'confirmado' then now() else confirmado_em end
            where id=%s and conta_id=%s""", (nova, nova, nova, evento_id, conta_id))
    return None


def remarcar(c, conta_id: int, evento_id: int, novo_inicio: datetime, agora: datetime | None = None) -> str | None:
    agora = agora or datetime.now(timezone.utc)
    ev = c.execute("""select profissional_id, servico_id, situacao from eventos_agenda
                       where id=%s and conta_id=%s and situacao is not null for update""",
                   (evento_id, conta_id)).fetchone()
    if not ev:
        return "Agendamento não encontrado."
    if ev[2] in ("finalizado", "cancelou", "presente", "atendimento"):
        return "Esse agendamento não pode mais ser remarcado."
    if novo_inicio <= agora:
        return "Esse horário já passou."
    c.execute("select pg_advisory_xact_lock(%s::int, %s::int)", (_LOCK_MARCAR, int(ev[0])))
    dia_ = local(novo_inicio).date()
    if not any(x["inicio"] == novo_inicio for x in
               livres(c, conta_id, ev[0], ev[1], dia_, 1, agora, ignorar=evento_id)):
        return "Esse horário não está livre."
    dur = c.execute("select coalesce(duracao_min, 30) from servicos_catalogo where id=%s and conta_id=%s",
                    (ev[1], conta_id)).fetchone()
    faixa = _faixa_do_horario(c, conta_id, ev[0], novo_inicio)
    c.execute(
        """update eventos_agenda
              set inicio=%s, fim=%s, situacao='agendado', situacao_em=now(), status='ativo',
                  clinica_local_id=%s, confirmacao_enviada_em=null, confirmado_em=null,
                  pede_remarcar_em=null, encaixe=false
            where id=%s and conta_id=%s""",
        (novo_inicio, novo_inicio + timedelta(minutes=(dur[0] if dur else 30)),
         faixa["local_id"] if faixa else None, evento_id, conta_id))
    return None


# ------------------------------------------------------------------ mensagens

def _palavra(ev: dict) -> str:
    """O nome que vai na mensagem: consulta, retorno, sessão — ou "atendimento".
    Nunca o procedimento (botox, biópsia...): mensagem não diz motivo clínico."""
    if (ev.get("tipo") or "").strip().lower() == "avaliação":
        return "avaliação"
    return _PALAVRA.get(ev.get("categoria") or "", "atendimento")


def _genero(palavra: str) -> tuple[str, str]:
    """("Sua", "a") pra consulta/avaliação/sessão; ("Seu", "o") pra retorno/atendimento."""
    return ("Sua", "a") if palavra in ("consulta", "avaliação", "sessão") else ("Seu", "o")


def _onde(c, conta_id: int, ev: dict) -> str:
    loc = next((x for x in cc.listar_locais(c, conta_id, so_ativos=False) if x["id"] == ev.get("local_id")), None)
    if not loc:
        return ""
    return f", no {loc['nome']}" + (f" ({loc['endereco']})" if loc["endereco"] else "")


def _prof_nome(c, conta_id: int, ev: dict) -> str:
    p = next((x for x in cc.listar_profissionais(c, conta_id, so_ativos=False)
              if x["id"] == ev.get("profissional_id")), None)
    return p["nome"] if p else ""


def texto_marcado(c, conta_id: int, ev: dict) -> str:
    from finance.voltar_a_chamar import primeiro_nome
    n = primeiro_nome(ev["paciente"])
    palavra = _palavra(ev)
    art, fim = _genero(palavra)
    return (f"Prontinho!! ✅ {n + ', ' + art.lower() if n else art} {palavra} com {_prof_nome(c, conta_id, ev)} "
            f"está marcad{fim} para {dia_txt(ev['inicio'])} às {ev['hora']}{_onde(c, conta_id, ev)}. "
            "Na véspera eu te mando um lembrete 😊")


def texto_vespera(c, conta_id: int, ev: dict) -> str:
    from finance.voltar_a_chamar import primeiro_nome
    n = primeiro_nome(ev["paciente"])
    return (f"Oi{', ' + n if n else ''}! Amanhã você tem {_palavra(ev)} às {ev['hora']} com "
            f"{_prof_nome(c, conta_id, ev)}{_onde(c, conta_id, ev)}. Confirma? "
            "Responda 1 para confirmar ou 2 se precisar remarcar.")


def _conversa(c, conta_id: int, ev: dict) -> int | None:
    dig = _digitos(ev.get("fone"))
    r = c.execute(
        r"""select cv.id from conversas cv
             where cv.conta_id=%s and cv.canal='whatsapp'
               and (cv.prospeccao_id = %s
                    or (length(%s) >= 8 and right(regexp_replace(coalesce(cv.contato_ref,''), '\D', '', 'g'), 8) = %s))
             order by cv.ultima_msg_em desc nulls last limit 1""",
        (conta_id, ev.get("lead"), dig, dig[-8:])).fetchone()
    return r[0] if r else None


def enviar(c, conta_id: int, ev: dict, texto: str, *, autor: str, membro_id: int | None = None) -> dict:
    """Manda pelo mesmo chip da conversa do paciente (se houver) e grava na conversa."""
    dig = _digitos(ev.get("fone"))
    if len(dig) < 10:
        return {"ok": False, "erro": "sem_numero"}
    conv = _conversa(c, conta_id, ev)
    from finance import agente
    try:
        destino = ev["fone"] if (ev.get("fone") or "").startswith("+") else "+" + dig
        res = agente._mandar(c, conta_id, "whatsapp", destino, texto, conv) or {}
    except Exception as e:  # noqa: BLE001
        _log.warning("agenda da clínica: envio falhou (evento %s): %s", ev.get("id"), e)
        res = {"ok": False, "erro": type(e).__name__}
    if res.get("ok") and conv:
        c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, membro_id, provider_sid)
                     values (%s,'whatsapp','out',%s,%s,%s,%s)""",
                  (conv, autor, texto, membro_id, res.get("sid")))
        c.execute("update conversas set ultima_msg_em=now() where id=%s and conta_id=%s", (conv, conta_id))
    return res


# ------------------------------------------------------------------ confirmação na véspera

def config(c, conta_id: int) -> dict:
    try:
        with c.transaction():
            r = c.execute("select confirmacao_modo, confirmacao_hora from clinica_agenda_config where conta_id=%s",
                          (conta_id,)).fetchone()
    except Exception:  # noqa: BLE001 — migração 351 ainda não rodou
        r = None
    return {"confirmacao_modo": r[0] if r else "off", "confirmacao_hora": r[1] if r else 10}


def salvar_config(c, conta_id: int, modo: str, hora: int | None) -> str | None:
    if modo not in ("off", "ligado"):
        return "Modo inválido."
    hora = hora if hora and 7 <= int(hora) <= 19 else 10
    c.execute("""insert into clinica_agenda_config (conta_id, confirmacao_modo, confirmacao_hora)
                 values (%s,%s,%s) on conflict (conta_id) do update
                 set confirmacao_modo=excluded.confirmacao_modo, confirmacao_hora=excluded.confirmacao_hora,
                     atualizado_em=now()""", (conta_id, modo, int(hora)))
    return None


def ler_respostas(c, conta_id: int, agora: datetime) -> int:
    """O paciente respondeu 1 (confirma) ou 2 (remarcar) depois da mensagem da véspera."""
    mudou = 0
    for eid, enviada, lead, fone in c.execute(
            """select id, confirmacao_enviada_em, prospeccao_id, paciente_fone from eventos_agenda
                where conta_id=%s and situacao='agendado' and confirmacao_enviada_em is not null
                  and pede_remarcar_em is null and inicio > %s""", (conta_id, agora)).fetchall():
        conv = _conversa(c, conta_id, {"lead": lead, "fone": fone})
        if not conv:
            continue
        r = c.execute("""select m.texto from mensagens m join conversas cv on cv.id = m.conversa_id
                          where m.conversa_id=%s and cv.conta_id=%s and m.direcao='in' and m.criado_em > %s
                          order by m.criado_em, m.id limit 1""", (conv, conta_id, enviada)).fetchone()
        if not r:
            continue
        if _RE_SIM.search(r[0] or ""):
            c.execute("""update eventos_agenda set situacao='confirmado', situacao_em=now(), confirmado_em=now()
                          where id=%s and conta_id=%s and situacao='agendado'""", (eid, conta_id))
            mudou += 1
        elif _RE_REMARCAR.search(r[0] or ""):
            c.execute("update eventos_agenda set pede_remarcar_em=now() where id=%s and conta_id=%s",
                      (eid, conta_id))
            mudou += 1
    return mudou


def mandar_vesperas(c, conta_id: int, agora: datetime, cfg: dict, janela: dict, limite: int = 3) -> int:
    """Na hora escolhida (e dentro do horário da clínica), a mensagem de quem tem
    horário amanhã. No máximo `limite` por ciclo: o resto sai nos próximos."""
    if local(agora).hour < cfg["confirmacao_hora"] or not fr.dentro_da_janela(agora, janela):
        return 0
    amanha = hoje_br(agora) + timedelta(days=1)
    evs = [e for e in _eventos(c, conta_id, utc(amanha, time(0)), utc(amanha + timedelta(days=1), time(0)))
           if e["situacao"] == "agendado" and not e["confirmacao_enviada_em"]
           and local(e["inicio"]).date() == amanha]
    feitos = 0
    for ev in evs[:limite]:
        # marca antes de mandar: se cair no meio, o paciente fica sem a mensagem, e nunca com duas
        r = c.execute("""update eventos_agenda set confirmacao_enviada_em=%s
                          where id=%s and conta_id=%s and confirmacao_enviada_em is null returning id""",
                      (agora, ev["id"], conta_id)).fetchone()
        c.commit()
        if r and enviar(c, conta_id, ev, texto_vespera(c, conta_id, ev), autor="bot").get("ok"):
            feitos += 1
        c.commit()
    return feitos


def rodar(pool, agora: datetime | None = None) -> dict:
    """Uma passada da confirmação na véspera, nas contas que ligaram. Chamada pelo
    poller (web/app.py). Só perfil clínica e só com o modo ligado."""
    agora = agora or datetime.now(timezone.utc)
    total = {"contas": 0, "enviadas": 0, "respostas": 0}
    with pool.connection() as lockc:
        if not lockc.execute("select pg_try_advisory_lock(%s)", (_LOCK,)).fetchone()[0]:
            return total
        try:
            with pool.connection() as c:
                try:
                    with c.transaction():
                        contas = [r[0] for r in c.execute(
                            "select conta_id from clinica_agenda_config where confirmacao_modo='ligado'").fetchall()]
                except Exception:  # noqa: BLE001
                    contas = []
            for conta_id in contas:
                try:
                    with pool.connection() as c:
                        if fr.perfil_da_conta(c, conta_id) != "clinica":
                            continue
                        total["respostas"] += ler_respostas(c, conta_id, agora)
                        c.commit()
                        total["enviadas"] += mandar_vesperas(c, conta_id, agora, config(c, conta_id),
                                                             fr.config(c, conta_id))
                        c.commit()
                    total["contas"] += 1
                except Exception:  # noqa: BLE001
                    _log.warning("confirmação da véspera falhou na conta %s", conta_id, exc_info=True)
        finally:
            lockc.execute("select pg_advisory_unlock(%s)", (_LOCK,))
    return total
