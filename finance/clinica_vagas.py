"""Vaga liberada (fase 4 da clínica, primeira parte): o horário que abre por
cancelamento vai pra quem cabe nele, e fica com quem responder "1" primeiro.

Desenho aprovado: docs/mockups/clinica_agenda_itinerante.html, seção 04 ("o motor
de ocupação"). A tela é /painel/clinica/vagas (web/painel_clinica_vagas.py); o
poller chama `rodar` a cada ciclo.

COMO ANDA UMA VAGA
  1. Uma consulta da agenda da clínica é cancelada com pelo menos 1h de aviso e o
     horário continua livre → nasce a vaga ('aguardando').
  2. No modo 'aprova' (o padrão do 1º mês: oferta é mensagem que o paciente não
     pediu) a recepção aperta "Aprovar e mandar"; no 'auto' o poller manda sozinho.
     Só dentro do horário de atendimento da Régua.
  3. A 1ª rodada vai pra 3 pessoas ao mesmo tempo, valendo 20 minutos, "é de quem
     responder primeiro". Sem "1" em 20 min, a 2ª rodada vai sozinha pra mais 5.
     Ninguém → a vaga fica livre na agenda.
  4. "1" → marca na hora (a mesma `clinica_agenda.agendar`, que reconfere o horário
     com a trava da agenda) e responde "Prontinho". Quem responde "1" depois ouve
     "acabou de ser preenchida". "2" → "sem problema". PARAR → nunca mais recebe
     aviso de vaga.
  5. Com menos de 1h de aviso não manda nada: a recepção oferece no balcão.

QUEM É CHAMADO, EM ORDEM DE GRUPO (sem pontos: uma frase diz por quê):
  pediu   — o agente passou pra recepção "quer marcar" e ainda não marcou;
  vem     — já vem à clínica nesse dia, com outro profissional;
  preco   — recebeu o preço da consulta e não marcou (o "voltar a chamar");
  retorno — o prazo de volta do atendimento dele passou.
FICA DE FORA: quem já tem horário nesse dia com o profissional ou naquela hora,
quem já tem consulta marcada, quem pediu PARAR ou SAIR, quem já recebeu mensagem
automática hoje (1 por pessoa por dia, somando o voltar a chamar), quem não tem
nome ou celular, e atendimento que não cabe no horário.

A MENSAGEM NUNCA DIZ O PROCEDIMENTO: profissional, dia, hora e lugar. E sempre
ensina PARAR.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, time, timedelta, timezone

from finance import clinica_agenda as ca
from finance import clinica_config as cc

_log = logging.getLogger("clinica.vagas")

_LOCK = 771164            # vizinho das travas da agenda e do agente (771161–771163)
AVISO_MIN = timedelta(minutes=60)   # menos que isso: ofereça no balcão
VALIDADE = timedelta(minutes=20)    # quanto vale o convite de cada rodada
POR_RODADA = {1: 3, 2: 5}
DIAS = 14                            # cancelamento até 2 semanas pra frente vira vaga
GRUPOS = (("pediu", "Pediu horário"), ("vem", "Já vem nesse dia"),
          ("preco", "Recebeu o preço"), ("retorno", "Retorno vencido"))
GRUPO_D = dict(GRUPOS)
MODOS = (("off", "Desligado: não procura vaga"),
         ("aprova", "A recepção aprova cada horário (recomendado no 1º mês)"),
         ("auto", "Automático: manda sozinho no horário de atendimento"))

_RE_SIM = re.compile(r"^\s*(1|sim|quero|pode ser|pode|eu quero|fico|confirmo)\s*[.!)✅👍]*\s*$", re.I)
_RE_NAO = re.compile(r"^\s*(2|n[aã]o|n[aã]o posso|n[aã]o d[aá]|n[aã]o quero)\s*[.!,]*\s*(obrigad[oa])?\s*[.!]*\s*$",
                     re.I)
_RE_PARAR = re.compile(r"^\s*(parar?|pare|sair)\s*[.!]*\s*$", re.I)
_RE_NUMERO = re.compile(r"^\s*[12]\s*[.!)✅👍]*\s*$")


# ------------------------------------------------------------------ config

def config(c, conta_id: int) -> dict:
    try:
        with c.transaction():
            r = c.execute("select vagas_modo, vagas_teto_dia from clinica_agenda_config where conta_id=%s",
                          (conta_id,)).fetchone()
    except Exception:  # noqa: BLE001 — migração 365 ainda não rodou
        return {"modo": "off", "teto_dia": 20}
    return {"modo": r[0] if r else "aprova", "teto_dia": int(r[1]) if r else 20}


def salvar_config(c, conta_id: int, modo: str, teto: int | None) -> str | None:
    if modo not in dict(MODOS):
        return "Modo inválido."
    teto = teto if teto and 1 <= int(teto) <= 100 else 20
    c.execute("""insert into clinica_agenda_config (conta_id, vagas_modo, vagas_teto_dia) values (%s,%s,%s)
                 on conflict (conta_id) do update set vagas_modo=excluded.vagas_modo,
                     vagas_teto_dia=excluded.vagas_teto_dia, atualizado_em=now()""",
              (conta_id, modo, int(teto)))
    return None


# ------------------------------------------------------------------ leitura

def _n8(fone: str | None) -> str:
    return ca._digitos(fone)[-8:]


def vaga(c, conta_id: int, vaga_id: int) -> dict | None:
    r = c.execute(
        """select v.id, v.profissional_id, v.inicio, v.fim, v.clinica_local_id, v.origem_evento_id,
                  v.estado, v.rodada, v.oferta_ate, v.aprovada_em, v.preenchida_evento_id, v.preenchida_em,
                  coalesce(e.paciente_nome, ''), v.criado_em
             from clinica_vagas v
             left join eventos_agenda e on e.id = v.origem_evento_id and e.conta_id = v.conta_id
            where v.id=%s and v.conta_id=%s""", (vaga_id, conta_id)).fetchone()
    return _vaga_dict(c, conta_id, r) if r else None


def _vaga_dict(c, conta_id: int, r) -> dict:
    prof = next((p for p in cc.listar_profissionais(c, conta_id, so_ativos=False) if p["id"] == r[1]), None)
    ev_fake = {"local_id": r[4], "profissional_id": r[1]}
    dur = int((r[3] - r[2]).total_seconds() // 60)
    return {"id": r[0], "profissional_id": r[1], "prof": prof["nome"] if prof else "", "inicio": r[2],
            "fim": r[3], "local_id": r[4], "origem_evento_id": r[5], "estado": r[6], "rodada": r[7],
            "oferta_ate": r[8], "aprovada_em": r[9], "preenchida_evento_id": r[10], "preenchida_em": r[11],
            "cancelou": r[12], "criado_em": r[13], "dur": dur,
            "dia": ca.dia_txt(r[2]), "hora": ca.hora_txt(r[2]), "onde": ca._onde(c, conta_id, ev_fake)}


def listar(c, conta_id: int, agora: datetime) -> list[dict]:
    """Abertas (aguardando e em oferta) primeiro, pelo horário; depois as dos
    últimos 7 dias já resolvidas."""
    try:
        with c.transaction():
            rows = c.execute(
                """select v.id, v.profissional_id, v.inicio, v.fim, v.clinica_local_id, v.origem_evento_id,
                          v.estado, v.rodada, v.oferta_ate, v.aprovada_em, v.preenchida_evento_id,
                          v.preenchida_em, coalesce(e.paciente_nome, ''), v.criado_em
                     from clinica_vagas v
                     left join eventos_agenda e on e.id = v.origem_evento_id and e.conta_id = v.conta_id
                    where v.conta_id=%s
                      and (v.estado in ('aguardando','oferta') or v.atualizado_em > %s - interval '7 days')
                    order by v.estado not in ('aguardando','oferta'), v.inicio limit 60""",
                (conta_id, agora)).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [_vaga_dict(c, conta_id, r) for r in rows]


def ofertas(c, conta_id: int, vaga_id: int) -> list[dict]:
    return [{"id": r[0], "nome": r[1], "grupo": r[2], "grupo_d": GRUPO_D.get(r[2], r[2]), "porque": r[3],
             "rodada": r[4], "estado": r[5], "enviada": ca.hora_txt(r[6]), "respondida": r[7],
             "conversa_id": r[8]}
            for r in c.execute(
                """select id, nome, grupo, porque, rodada, estado, enviada_em, respondida_em, conversa_id
                     from clinica_vaga_ofertas where conta_id=%s and vaga_id=%s order by rodada, id""",
                (conta_id, vaga_id)).fetchall()]


def esperando(c, conta_id: int) -> int:
    """Quantos horários esperam a recepção aprovar (a tela Hoje e a Agenda avisam)."""
    try:
        with c.transaction():
            return c.execute("""select count(*) from clinica_vagas where conta_id=%s and estado='aguardando'
                                  and aprovada_em is null""", (conta_id,)).fetchone()[0]
    except Exception:  # noqa: BLE001
        return 0


def em_oferta(c, conta_id: int) -> set[tuple[int, datetime]]:
    """(profissional, início) dos horários com convite na rua: o agente não oferece o
    mesmo horário a outra pessoa ("ninguém promete o mesmo horário duas vezes")."""
    try:
        with c.transaction():
            return {(r[0], r[1]) for r in c.execute(
                "select profissional_id, inicio from clinica_vagas where conta_id=%s and estado='oferta'",
                (conta_id,)).fetchall()}
    except Exception:  # noqa: BLE001
        return set()


# ------------------------------------------------------------------ nascer, fechar

def detectar(c, conta_id: int, agora: datetime) -> int:
    """Consulta cancelada com pelo menos 1h de aviso, horário ainda livre → vaga."""
    novas = 0
    for eid, prof, ini, fim, loc in c.execute(
            """select e.id, e.profissional_id, e.inicio, coalesce(e.fim, e.inicio + interval '30 minutes'),
                      e.clinica_local_id
                 from eventos_agenda e
                where e.conta_id=%s and e.situacao is not null and e.profissional_id is not null
                  and (e.situacao = 'cancelou' or e.status = 'cancelado')
                  and e.inicio > %s and e.inicio < %s
                  and not exists (select 1 from clinica_vagas v where v.conta_id = e.conta_id
                                     and v.profissional_id = e.profissional_id and v.inicio = e.inicio)""",
            (conta_id, agora + AVISO_MIN, agora + timedelta(days=DIAS))).fetchall():
        if ca.ocupados(c, conta_id, prof, ini, fim):
            continue                        # já marcaram outra pessoa ali
        r = c.execute("""insert into clinica_vagas (conta_id, profissional_id, inicio, fim, clinica_local_id,
                                                     origem_evento_id)
                         values (%s,%s,%s,%s,%s,%s) on conflict do nothing returning id""",
                      (conta_id, prof, ini, fim, loc, eid)).fetchone()
        novas += 1 if r else 0
    return novas


def _estado(c, conta_id: int, vaga_id: int, estado: str, **extra) -> None:
    sets = ", ".join(f"{k}=%s" for k in extra)
    c.execute(f"update clinica_vagas set estado=%s, atualizado_em=now(){', ' + sets if sets else ''} "
              "where id=%s and conta_id=%s", (estado, *extra.values(), vaga_id, conta_id))


def fechar(c, conta_id: int, agora: datetime) -> None:
    """Horário ocupado por fora → 'ocupada'; perto demais sem convite → 'balcao';
    convite vencido perto demais → 'livre'."""
    for v in [vaga(c, conta_id, r[0]) for r in c.execute(
            "select id from clinica_vagas where conta_id=%s and estado in ('aguardando','oferta')",
            (conta_id,)).fetchall()]:
        if v is None:
            continue
        if ca.ocupados(c, conta_id, v["profissional_id"], v["inicio"], v["fim"]):
            _estado(c, conta_id, v["id"], "ocupada")
        elif v["inicio"] <= agora + AVISO_MIN:
            if v["estado"] == "aguardando":
                _estado(c, conta_id, v["id"], "balcao")
            elif v["oferta_ate"] and v["oferta_ate"] <= agora:
                _estado(c, conta_id, v["id"], "livre")


# ------------------------------------------------------------------ quem cabe

def _tipo_para(c, conta_id: int, prof_id: int, categoria: str, dur: int) -> dict | None:
    prof = next((p for p in ca._profs_que_atendem(c, conta_id) if p["id"] == prof_id), None)
    if not prof:
        return None
    tipos = [t for t in cc.listar_tipos(c, conta_id)
             if t["categoria"] == categoria and t["id"] in prof["tipos"] and t["duracao_min"] <= dur]
    # a "consulta" de verdade antes da consulta social e da avaliação
    tipos.sort(key=lambda t: (t["slug"] != categoria, "social" in t["slug"]))
    return tipos[0] if tipos else None


def _dias(delta: timedelta) -> str:
    d = max(0, delta.days)
    return "hoje" if d == 0 else ("há 1 dia" if d == 1 else f"há {d} dias")


def _sinais(c, conta_id: int, agora: datetime, prof_id: int, inicio: datetime) -> list[dict]:
    """Todo mundo que PODERIA ser chamado, com o grupo e o porquê (antes dos filtros)."""
    out = []
    # pediu: o agente passou "quer marcar" pra recepção (fase 3b)
    try:
        with c.transaction():
            for conv, lead, quando in c.execute(
                    """select r.conversa_id, r.prospeccao_id, max(r.criado_em) from clinica_repasses r
                        where r.conta_id=%s and r.resolvido_em is null and r.criado_em > %s - interval '14 days'
                          and (r.motivo = 'marcar' or 'marcar' = any(r.motivos))
                        group by r.conversa_id, r.prospeccao_id""", (conta_id, agora)).fetchall():
                out.append({"conversa_id": conv, "lead": lead, "grupo": "pediu", "quando": quando,
                            "categoria": "consulta", "porque": f"pediu horário pelo WhatsApp {_dias(agora - quando)}"})
    except Exception:  # noqa: BLE001
        pass
    # preco: recebeu o preço da consulta e não marcou (voltar a chamar, #843)
    try:
        with c.transaction():
            for conv, lead, quando in c.execute(
                    """select distinct on (t.conversa_id) t.conversa_id, t.prospeccao_id, pm.criado_em
                         from voltar_a_chamar_toques t
                         join mensagens pm on pm.id = t.preco_msg_id
                        where t.conta_id=%s and t.estado in ('pendente','enviado')
                          and pm.criado_em > %s - interval '21 days'
                        order by t.conversa_id, pm.criado_em desc""", (conta_id, agora)).fetchall():
                out.append({"conversa_id": conv, "lead": lead, "grupo": "preco", "quando": quando,
                            "categoria": "consulta", "porque": f"recebeu o preço da consulta {_dias(agora - quando)}"})
    except Exception:  # noqa: BLE001
        pass
    # retorno: o prazo de volta do último atendimento com ESTE profissional passou
    for lead, ultimo, volta, pac, fone in c.execute(
            """select distinct on (e.prospeccao_id) e.prospeccao_id, e.inicio, s.volta_dias,
                      coalesce(e.paciente_nome, ''), coalesce(e.paciente_fone, '')
                 from eventos_agenda e
                 join servicos_catalogo s on s.id = e.servico_id and s.conta_id = e.conta_id
                where e.conta_id=%s and e.profissional_id=%s and e.situacao='finalizado'
                  and e.prospeccao_id is not null and s.volta_dias is not null and s.volta_dias > 0
                order by e.prospeccao_id, e.inicio desc""", (conta_id, prof_id)).fetchall():
        vence = ultimo + timedelta(days=int(volta))
        if vence <= inicio and vence > agora - timedelta(days=120):
            conv = ca._conversa(c, conta_id, {"lead": lead, "fone": fone})
            if conv:
                atraso = (ca.hoje_br(agora) - ca.local(vence).date()).days
                out.append({"conversa_id": conv, "lead": lead, "grupo": "retorno", "quando": vence,
                            "categoria": "retorno", "nome_pac": pac,
                            "porque": (f"retorno vencido há {atraso} dia{'s' if atraso != 1 else ''}"
                                       if atraso > 0 else f"retorno vence em {ca.dia_txt(vence)}")})
    return out


def candidatos(c, conta_id: int, v: dict, agora: datetime) -> tuple[list[dict], list[dict]]:
    """(quem chamar, em ordem; quem ficou de fora e por quê)."""
    from finance.clinica_agente import _nome_de_gente
    chamar, fora, vistos = [], [], set()
    ja = {r[0] for r in c.execute("select conversa_id from clinica_vaga_ofertas where vaga_id=%s and conta_id=%s",
                                  (v["id"], conta_id)).fetchall()}
    inicio_dia = ca.utc(ca.local(agora).date(), time(0))
    bloq = _bloqueados(c, conta_id)
    dia_ini = ca.utc(ca.local(v["inicio"]).date(), time(0))
    ordem = {g: i for i, (g, _d) in enumerate(GRUPOS)}
    sinais = sorted(_sinais(c, conta_id, agora, v["profissional_id"], v["inicio"]),
                    key=lambda s: (ordem[s["grupo"]], s["grupo"] == "retorno", -s["quando"].timestamp()
                                   if s["grupo"] != "retorno" else s["quando"].timestamp()))
    for s in sinais:
        if s["conversa_id"] in vistos:
            continue
        vistos.add(s["conversa_id"])
        info = c.execute(
            """select coalesce(nullif(p.contato,''), nullif(cv.contato_nome,''), p.empresa, ''),
                      coalesce(nullif(p.whatsapp,''), nullif(p.telefone,''), cv.contato_ref, ''), cv.prospeccao_id
                 from conversas cv left join prospeccao p on p.id = cv.prospeccao_id and p.conta_id = cv.conta_id
                where cv.id=%s and cv.conta_id=%s and cv.canal='whatsapp'""",
            (s["conversa_id"], conta_id)).fetchone()
        if not info:
            continue
        nome = _nome_de_gente(s.get("nome_pac") or info[0])
        fone, lead = info[1], s["lead"] or info[2]
        item = {**s, "nome": nome or (info[0] or "Sem nome"), "fone": fone, "lead": lead,
                "grupo_d": GRUPO_D[s["grupo"]]}

        def sai(motivo):
            fora.append({**item, "motivo": motivo})
        if s["conversa_id"] in ja:
            continue
        if not nome:
            sai("sem nome no WhatsApp")
            continue
        if len(ca._digitos(fone)) < 10:
            sai("sem celular")
            continue
        if _n8(fone) in bloq:
            sai(bloq[_n8(fone)])
            continue
        tipo = _tipo_para(c, conta_id, v["profissional_id"], s["categoria"], v["dur"])
        if not tipo:
            sai(f"{v['prof']} não tem {s['categoria']} que caiba em {v['dur']} min")
            continue
        evs = c.execute(
            r"""select e.profissional_id, e.inicio, coalesce(e.fim, e.inicio + interval '30 minutes')
                  from eventos_agenda e
                 where e.conta_id=%s and e.status='ativo' and e.situacao in ('agendado','confirmado','presente','atendimento')
                   and e.inicio > %s
                   and (e.prospeccao_id = %s
                        or right(regexp_replace(coalesce(e.paciente_fone,''), '\D', '', 'g'), 8) = %s)""",
            (conta_id, agora, lead, _n8(fone))).fetchall()
        mesmo_dia = [e for e in evs if dia_ini <= e[1] < dia_ini + timedelta(days=1)]
        if any(e[1] < v["fim"] and v["inicio"] < e[2] for e in mesmo_dia):
            sai("está em outro atendimento nessa hora")
            continue
        if any(e[0] == v["profissional_id"] for e in mesmo_dia):
            sai(f"já tem horário com {v['prof']} nesse dia")
            continue
        if evs and not mesmo_dia and s["grupo"] in ("pediu", "preco"):
            sai("já marcou consulta")
            continue
        if _recebeu_hoje(c, conta_id, s["conversa_id"], inicio_dia):
            sai("já recebeu mensagem automática hoje")
            continue
        if mesmo_dia:
            item = {**item, "grupo": "vem", "grupo_d": GRUPO_D["vem"],
                    "porque": f"já vem nesse dia às {ca.hora_txt(mesmo_dia[0][1])} · {item['porque']}"}
        chamar.append({**item, "servico_id": tipo["id"], "tipo": tipo["nome"]})
    chamar.sort(key=lambda x: ordem[x["grupo"]])
    return chamar, fora


def _bloqueados(c, conta_id: int) -> dict[str, str]:
    """numero8 → por que não chama: SAIR/não é paciente (voltar a chamar) e PARAR daqui."""
    out = {}
    try:
        with c.transaction():
            for n8, motivo in c.execute("select numero8, motivo from voltar_a_chamar_bloqueios where conta_id=%s",
                                        (conta_id,)).fetchall():
                out[n8] = "pediu pra não receber mensagem" if motivo == "saiu" else "marcado como não paciente"
    except Exception:  # noqa: BLE001
        pass
    for (fone,) in c.execute("select fone from clinica_vaga_ofertas where conta_id=%s and estado='parar'",
                             (conta_id,)).fetchall():
        out[_n8(fone)] = "pediu PARAR aos avisos de vaga"
    return out


def _recebeu_hoje(c, conta_id: int, conversa_id: int, inicio_dia: datetime) -> bool:
    """1 mensagem automática por pessoa por dia, somando vaga e voltar a chamar."""
    if c.execute("""select 1 from clinica_vaga_ofertas where conta_id=%s and conversa_id=%s
                     and enviada_em >= %s and estado <> 'falhou' limit 1""",
                 (conta_id, conversa_id, inicio_dia)).fetchone():
        return True
    try:
        with c.transaction():
            return c.execute("""select 1 from voltar_a_chamar_toques where conta_id=%s and conversa_id=%s
                                 and estado='enviado' and enviado_em >= %s limit 1""",
                             (conta_id, conversa_id, inicio_dia)).fetchone() is not None
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------ mandar

def _quando_txt(inicio: datetime, agora: datetime) -> str:
    d, hoje = ca.local(inicio).date(), ca.hoje_br(agora)
    if d == hoje:
        return "hoje"
    if d == hoje + timedelta(days=1):
        return "amanhã"
    artigo = "no" if d.isoweekday() in (6, 7) else "na"
    return f"{artigo} {ca._DIA_LONGO[d.isoweekday()]}, {d:%d/%m},"


def texto_oferta(v: dict, nome: str, agora: datetime) -> str:
    from finance.voltar_a_chamar import primeiro_nome
    n = primeiro_nome(nome)
    minutos = int(VALIDADE.total_seconds() // 60)
    return (f"Oi{', ' + n if n else ''}! Abriu uma vaga com {v['prof']} {_quando_txt(v['inicio'], agora)} "
            f"às {v['hora']}{v['onde']}. Quer ficar com ela?\n"
            f"Responda 1 para confirmar. É por ordem de resposta, e ela vale pelos próximos {minutos} minutos 🙂\n"
            "(Se não quiser receber avisos de vaga, responda PARAR.)")


def _mandar(c, conta_id: int, conversa_id: int, fone: str, texto: str) -> dict:
    from finance import agente
    destino = fone if fone.startswith("+") else "+" + ca._digitos(fone)
    try:
        res = agente._mandar(c, conta_id, "whatsapp", destino, texto, conversa_id) or {}
    except Exception as e:  # noqa: BLE001
        _log.warning("vagas: envio falhou (conversa %s): %s", conversa_id, e)
        res = {"ok": False, "erro": type(e).__name__}
    if res.get("ok"):
        mid = c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, provider_sid)
                           values (%s,'whatsapp','out','bot',%s,%s) returning id""",
                        (conversa_id, texto, res.get("sid"))).fetchone()[0]
        c.execute("update conversas set ultima_msg_em=now() where id=%s and conta_id=%s", (conversa_id, conta_id))
        res["mensagem_id"] = mid
    return res


def _mandados_hoje(c, conta_id: int, agora: datetime) -> int:
    return c.execute("""select count(*) from clinica_vaga_ofertas where conta_id=%s and enviada_em >= %s
                         and estado <> 'falhou'""",
                     (conta_id, ca.utc(ca.local(agora).date(), time(0)))).fetchone()[0]


def rodada(c, conta_id: int, v: dict, n: int, agora: datetime, cfg: dict) -> int:
    """Manda a rodada `n` (1: 3 pessoas; 2: mais 5). Devolve quantas saíram. Cada
    convite é gravado e COMMITADO antes do WhatsApp: se cair no meio, a pessoa fica
    sem convite, nunca com dois."""
    chamar, _fora = candidatos(c, conta_id, v, agora)
    vagas_no_teto = max(0, cfg["teto_dia"] - _mandados_hoje(c, conta_id, agora))
    escolhidos = chamar[:min(POR_RODADA[n], vagas_no_teto)]
    if not escolhidos:
        return 0
    _estado(c, conta_id, v["id"], "oferta", rodada=n, oferta_ate=agora + VALIDADE)
    saiu = 0
    for p in escolhidos:
        r = c.execute(
            """insert into clinica_vaga_ofertas (conta_id, vaga_id, conversa_id, prospeccao_id, nome, fone,
                                                 servico_id, grupo, porque, rodada, enviada_em)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) on conflict do nothing returning id""",
            (conta_id, v["id"], p["conversa_id"], p["lead"], p["nome"], p["fone"], p["servico_id"],
             p["grupo"], p["porque"][:200], n, agora)).fetchone()
        c.commit()
        if not r:
            continue
        res = _mandar(c, conta_id, p["conversa_id"], p["fone"], texto_oferta(v, p["nome"], agora))
        if res.get("ok"):
            c.execute("update clinica_vaga_ofertas set mensagem_id=%s where id=%s and conta_id=%s",
                      (res["mensagem_id"], r[0], conta_id))
            _pular_toque_de_hoje(c, conta_id, p["conversa_id"], agora)
            saiu += 1
        else:
            c.execute("update clinica_vaga_ofertas set estado='falhou' where id=%s and conta_id=%s", (r[0], conta_id))
        c.commit()
    if not saiu:
        _estado(c, conta_id, v["id"], "livre")
        c.commit()
    return saiu


def _pular_toque_de_hoje(c, conta_id: int, conversa_id: int, agora: datetime) -> None:
    """O convite ocupa o toque do dia do voltar a chamar ("no lugar do 3º toque")."""
    fim_do_dia = ca.utc(ca.local(agora).date() + timedelta(days=1), time(0))
    try:
        with c.transaction():
            c.execute("""update voltar_a_chamar_toques set estado='pulado'
                          where conta_id=%s and conversa_id=%s and estado='pendente' and devido_em < %s""",
                      (conta_id, conversa_id, fim_do_dia))
    except Exception:  # noqa: BLE001
        pass


def aprovar(c, conta_id: int, vaga_id: int, membro_id: int | None, agora: datetime, janela: dict) -> str:
    """"Aprovar e mandar". Devolve o código do aviso da tela."""
    from finance import funil_regua as fr
    v = vaga(c, conta_id, vaga_id)
    if not v or v["estado"] != "aguardando":
        return "ja_tratada"
    if ca.ocupados(c, conta_id, v["profissional_id"], v["inicio"], v["fim"]):
        _estado(c, conta_id, vaga_id, "ocupada")
        c.commit()
        return "ocupada"
    if v["inicio"] <= agora + AVISO_MIN:
        _estado(c, conta_id, vaga_id, "balcao")
        c.commit()
        return "balcao"
    c.execute("update clinica_vagas set aprovada_em=%s, aprovada_por=%s where id=%s and conta_id=%s",
              (agora, membro_id, vaga_id, conta_id))
    c.commit()
    if not fr.dentro_da_janela(agora, janela):
        return "aprovada_fora"          # o poller manda quando a clínica abrir
    return "mandada" if rodada(c, conta_id, v, 1, agora, config(c, conta_id)) else "ninguem"


def parar(c, conta_id: int, vaga_id: int) -> bool:
    r = c.execute("""update clinica_vagas set estado='livre', atualizado_em=now()
                      where id=%s and conta_id=%s and estado in ('aguardando','oferta') returning id""",
                  (vaga_id, conta_id)).fetchone()
    return r is not None


# ------------------------------------------------------------------ respostas

def _respostas(c, conta_id: int, conversa_id: int | None, agora: datetime) -> list[tuple]:
    """(criado_em, oferta_id, texto) das respostas por ler, na ordem em que chegaram.
    Só conta mensagem que veio DEPOIS do convite e sem outra mensagem nossa no meio
    (aí o "sim" responde a ela) — o "1"/"2" puro vale mesmo assim."""
    extra = " and o.conversa_id=%s" if conversa_id else ""
    rows = c.execute(
        """select m.criado_em, o.id, m.texto,
                  exists (select 1 from mensagens x where x.conversa_id = m.conversa_id and x.direcao = 'out'
                            and x.id <> coalesce(o.mensagem_id, 0)
                            and x.criado_em > o.enviada_em and x.criado_em < m.criado_em)
             from clinica_vaga_ofertas o
             join mensagens m on m.conversa_id = o.conversa_id and m.direcao = 'in' and m.criado_em > o.enviada_em
            where o.conta_id=%s and o.estado in ('enviada','perdeu') and o.avisada_em is null
              and o.enviada_em > %s - interval '2 days'""" + extra + """
            order by m.criado_em, m.id""",
        (conta_id, agora, conversa_id) if conversa_id else (conta_id, agora)).fetchall()
    return [(r[0], r[1], r[2] or "") for r in rows if not r[3] or _RE_NUMERO.match(r[2] or "")]


def processar(c, conta_id: int, agora: datetime, conversa_id: int | None = None, responder=None) -> int:
    """Lê "1", "2" e PARAR das ofertas, na ordem de chegada (a vaga é de quem
    respondeu primeiro). `responder(oferta, texto)` manda a resposta; sem ele, sai
    direto pelo WhatsApp (poller). Devolve quantas respostas tratou."""
    tratadas = 0
    for _quando, oferta_id, texto in _respostas(c, conta_id, conversa_id, agora):
        o = c.execute(
            """select o.id, o.vaga_id, o.conversa_id, o.prospeccao_id, o.nome, o.fone, o.servico_id, o.estado
                 from clinica_vaga_ofertas o where o.id=%s and o.conta_id=%s for update""",
            (oferta_id, conta_id)).fetchone()
        if not o or o[7] not in ("enviada", "perdeu"):
            continue
        oferta = {"id": o[0], "vaga_id": o[1], "conversa_id": o[2], "lead": o[3], "nome": o[4],
                  "fone": o[5], "servico_id": o[6], "estado": o[7]}
        if _RE_PARAR.match(texto):
            c.execute("update clinica_vaga_ofertas set estado='parar', respondida_em=%s, avisada_em=%s "
                      "where id=%s and conta_id=%s", (agora, agora, oferta_id, conta_id))
            _responder(c, conta_id, oferta, "Pronto! Não te mando mais avisos de vaga. Se precisar de horário, "
                       "é só chamar por aqui 😊", responder)
        elif _RE_SIM.match(texto):
            _quer(c, conta_id, oferta, agora, responder)
        elif _RE_NAO.match(texto):
            c.execute("update clinica_vaga_ofertas set estado='recusou', respondida_em=%s, avisada_em=%s "
                      "where id=%s and conta_id=%s", (agora, agora, oferta_id, conta_id))
            _responder(c, conta_id, oferta, "Sem problema! Qualquer coisa, é só chamar por aqui 😊", responder)
        else:
            continue                        # outra conversa: fica com o agente ou a recepção
        tratadas += 1
        c.commit()
    return tratadas


def _responder(c, conta_id: int, oferta: dict, texto: str, responder) -> None:
    if responder is not None:
        responder(oferta, texto)
    else:
        _mandar(c, conta_id, oferta["conversa_id"], oferta["fone"], texto)


def _quer(c, conta_id: int, oferta: dict, agora: datetime, responder) -> None:
    """Respondeu "1": marca se a vaga ainda está de pé; senão, "acabou de ser preenchida"."""
    v = c.execute("select estado from clinica_vagas where id=%s and conta_id=%s for update",
                  (oferta["vaga_id"], conta_id)).fetchone()
    vv = vaga(c, conta_id, oferta["vaga_id"])
    if (oferta["estado"] == "enviada" and v and v[0] in ("oferta", "livre", "aguardando")
            and vv and vv["inicio"] > agora + timedelta(minutes=15)):
        try:
            with c.transaction():
                eid, erro = ca.agendar(c, conta_id, profissional_id=vv["profissional_id"],
                                       servico_id=oferta["servico_id"], inicio=vv["inicio"],
                                       lead_id=oferta["lead"], nome=oferta["nome"], paciente=oferta["nome"],
                                       fone=oferta["fone"], marcado_por="vaga", agora=agora)
                if erro:
                    raise ValueError(erro)
        except ValueError as e:
            _log.info("vagas: não marcou a vaga %s (%s)", oferta["vaga_id"], e)
            eid = None
        if eid:
            _estado(c, conta_id, oferta["vaga_id"], "preenchida", preenchida_evento_id=eid, preenchida_em=agora)
            c.execute("update clinica_vaga_ofertas set estado='ganhou', respondida_em=%s, avisada_em=%s "
                      "where id=%s and conta_id=%s", (agora, agora, oferta["id"], conta_id))
            c.execute("""update clinica_vaga_ofertas set estado='perdeu'
                          where vaga_id=%s and conta_id=%s and estado='enviada'""", (oferta["vaga_id"], conta_id))
            _resolver_pedido(c, conta_id, oferta["conversa_id"])
            c.commit()
            ev = ca.evento(c, conta_id, eid)
            _responder(c, conta_id, oferta, ca.texto_marcado(c, conta_id, ev, ca.config(c, conta_id)
                                                             ["confirmacao_modo"] == "ligado"), responder)
            _avisar_preenchida(c, conta_id, ev)
            return
        if v and v[0] in ("oferta", "livre", "aguardando"):
            _estado(c, conta_id, oferta["vaga_id"], "ocupada")
    c.execute("update clinica_vaga_ofertas set estado='perdeu', respondida_em=%s, avisada_em=%s "
              "where id=%s and conta_id=%s", (agora, agora, oferta["id"], conta_id))
    _responder(c, conta_id, oferta, "Ah, essa vaga acabou de ser preenchida 🙏 Te aviso na próxima! Se quiser, "
               "é só me dizer um dia e horário que eu vejo pra você.", responder)


def _resolver_pedido(c, conta_id: int, conversa_id: int) -> None:
    """O "quer marcar" que o agente passou pra recepção está resolvido: marcou."""
    try:
        with c.transaction():
            c.execute("""update clinica_repasses set resolvido_em=now() where conta_id=%s and conversa_id=%s
                          and resolvido_em is null and (motivo='marcar' or 'marcar' = any(motivos))""",
                      (conta_id, conversa_id))
    except Exception:  # noqa: BLE001
        pass


def _avisar_preenchida(c, conta_id: int, ev: dict) -> None:
    try:
        from finance import clinica_agente as cla
        try:
            from db.conexao import get_pool
            pool = get_pool()
        except Exception:  # noqa: BLE001 — sem pool o push não sai; o aviso é bônus
            pool = None
        cla._aviso(pool, conta_id, cla._recepcao(c, conta_id, ev.get("lead")), "⚡ Vaga preenchida",
                   f"{ev['paciente']} · {ca.dia_txt(ev['inicio'])} às {ev['hora']}",
                   f"/painel/clinica/agenda/evento/{ev['id']}")
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------ o poller

def passar_conta(c, conta_id: int, agora: datetime) -> dict:
    from finance import funil_regua as fr
    cfg = config(c, conta_id)
    if cfg["modo"] == "off":
        return {"novas": 0, "enviadas": 0, "respostas": 0}
    janela = fr.config(c, conta_id)
    out = {"novas": detectar(c, conta_id, agora), "enviadas": 0, "respostas": 0}
    c.commit()
    out["respostas"] = processar(c, conta_id, agora)
    fechar(c, conta_id, agora)
    c.commit()
    if not fr.dentro_da_janela(agora, janela):
        return out
    for (vid,) in c.execute(
            """select id from clinica_vagas where conta_id=%s and inicio > %s
                 and ((estado='aguardando' and (aprovada_em is not null or %s))
                      or (estado='oferta' and oferta_ate <= %s))
               order by inicio""",
            (conta_id, agora + AVISO_MIN, cfg["modo"] == "auto", agora)).fetchall():
        v = vaga(c, conta_id, vid)
        if v["estado"] == "aguardando":
            out["enviadas"] += rodada(c, conta_id, v, 1, agora, cfg)
        elif v["rodada"] == 1:
            n = rodada(c, conta_id, v, 2, agora, cfg)
            out["enviadas"] += n
        else:
            _estado(c, conta_id, vid, "livre")
            c.commit()
    # convite que venceu fora da janela: a 2ª rodada espera abrir; a vaga continua de pé
    return out


def rodar(pool, agora: datetime | None = None) -> dict:
    agora = agora or datetime.now(timezone.utc)
    total = {"contas": 0, "novas": 0, "enviadas": 0, "respostas": 0}
    with pool.connection() as lockc:
        if not lockc.execute("select pg_try_advisory_lock(%s)", (_LOCK,)).fetchone()[0]:
            return total
        try:
            with pool.connection() as c:
                try:
                    with c.transaction():
                        contas = [r[0] for r in c.execute(
                            """select distinct conta_id from eventos_agenda
                                where situacao is not null and inicio > %s - interval '1 day'""",
                            (agora,)).fetchall()]
                        c.execute("select 1 from clinica_vagas where conta_id = 0 limit 1")   # a 365 já rodou?
                except Exception:  # noqa: BLE001 — migração 365 ainda não rodou
                    contas = []
                from finance import clinica_agente as cla
                for conta_id in contas:
                    try:
                        if not cla.e_clinica(c, conta_id):
                            continue
                        r = passar_conta(c, conta_id, agora)
                        c.commit()
                        total["contas"] += 1
                        for k in ("novas", "enviadas", "respostas"):
                            total[k] += r[k]
                    except Exception:  # noqa: BLE001 — uma conta não derruba as outras
                        c.rollback()
                        _log.warning("vagas: conta %s falhou", conta_id, exc_info=True)
        finally:
            lockc.execute("select pg_advisory_unlock(%s)", (_LOCK,))
    return total
