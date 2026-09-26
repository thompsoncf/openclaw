"""Pacote com saldo, retorno programado e o lembrete que traz o paciente de volta
(fase 6 da clínica). Tabelas: 381. Tela: web/painel_clinica_pacotes.py.

Desenho aprovado: docs/mockups/clinica_planos_pacotes_assinatura.html, seção 04, e
clinica_visao_geral.html ("primeira sessão", "dois meses depois — retorno").

  PLANO ACEITO → SALDO. Cada procedimento do catálogo no plano vira um pacote: as
  sessões compradas, o intervalo entre elas (o "prazo de volta" do atendimento, ou
  21 dias) e a validade (12 meses — decisão B da parte 3).

  FINALIZOU → BAIXA. O atendimento finalizado daquele procedimento (ou uma "Sessão
  de pacote") baixa uma sessão do pacote mais antigo do paciente, com a data e o
  agendamento. Ali mesmo a tela oferece "marcar a próxima" — é o gesto que derruba o
  cancelamento de 21% para 4%.

  RETORNO. Ao finalizar, a recepção marca "o médico pediu retorno em N dias". Sete
  dias antes do prazo o paciente é chamado; marcou com o profissional, saiu da fila.

  OS LEMBRETES (só no horário de atendimento, 1 mensagem automática por paciente por
  dia somando vaga, plano e voltar a chamar, respeitando SAIR/PARAR):
    - a próxima sessão está liberada (passou o intervalo e não marcou);
    - o retorno está chegando;
    - o pacote vence em 60 dias com saldo.
  Nenhum diz o procedimento.

  PARCELA ATRASADA (decisão C): nasce desligado; ligado pelo dono, sessão do pacote
  não se marca com parcela do plano em atraso.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, time, timedelta, timezone

from finance import clinica_agenda as ca
from finance import clinica_config as cc

_log = logging.getLogger("clinica.pacotes")

_LOCK = 771166
INTERVALO_PADRAO = 21
AVISO_VALIDADE_DIAS = 60
RELEMBRAR_DIAS = 14          # lembrete da próxima sessão: no máximo um a cada 14 dias
RETORNO_PERDE_DIAS = 30      # retorno sem marcar 30 dias depois do prazo sai da fila


# ------------------------------------------------------------------ config

def config(c, conta_id: int) -> dict:
    padrao = {"validade_meses": 12, "lembretes": "ligado", "retorno_aviso_dias": 7, "bloqueia_atrasado": False}
    try:
        with c.transaction():
            r = c.execute("""select pacote_validade_meses, pacote_lembretes, retorno_aviso_dias,
                                    pacote_bloqueia_atrasado
                               from clinica_agenda_config where conta_id=%s""", (conta_id,)).fetchone()
    except Exception:  # noqa: BLE001 — migração 381 ainda não rodou
        return padrao
    if not r:
        return padrao
    return {"validade_meses": int(r[0]), "lembretes": r[1], "retorno_aviso_dias": int(r[2]),
            "bloqueia_atrasado": bool(r[3])}


def salvar_config(c, conta_id: int, *, validade, lembretes: str, aviso, bloqueia: bool) -> str | None:
    try:
        meses = int(str(validade).strip() or 12)
        dias = int(str(aviso).strip() or 7)
    except ValueError:
        return "Validade e aviso são números inteiros."
    if not 1 <= meses <= 60 or not 1 <= dias <= 30:
        return "Validade de 1 a 60 meses e aviso de 1 a 30 dias."
    if lembretes not in ("off", "ligado"):
        return "Modo inválido."
    c.execute("""insert into clinica_agenda_config (conta_id, pacote_validade_meses, pacote_lembretes,
                                                     retorno_aviso_dias, pacote_bloqueia_atrasado)
                 values (%s,%s,%s,%s,%s) on conflict (conta_id) do update
                 set pacote_validade_meses=excluded.pacote_validade_meses,
                     pacote_lembretes=excluded.pacote_lembretes, retorno_aviso_dias=excluded.retorno_aviso_dias,
                     pacote_bloqueia_atrasado=excluded.pacote_bloqueia_atrasado, atualizado_em=now()""",
              (conta_id, meses, lembretes, dias, bool(bloqueia)))
    return None


def _mais_meses(d: date, meses: int) -> date:
    m = d.month - 1 + meses
    ano, mes = d.year + m // 12, m % 12 + 1
    for dia in (d.day, 30, 29, 28):
        try:
            return date(ano, mes, dia)
        except ValueError:
            continue
    return date(ano, mes, 28)


# ------------------------------------------------------------------ leitura

_COLS = """k.id, k.plano_id, k.prospeccao_id, k.paciente_nome, k.paciente_fone, k.servico_id, k.nome,
           k.profissional_id, k.sessoes_total, k.sessoes_usadas, k.intervalo_dias, k.validade_ate, k.estado,
           k.encerrado_motivo, k.criado_em,
           (select max(e.inicio) from clinica_pacote_consumos u
              join eventos_agenda e on e.id = u.evento_id and e.conta_id = u.conta_id
             where u.pacote_id = k.id and u.conta_id = k.conta_id)"""


def _dict(r, hoje: date) -> dict:
    ultima = r[15]
    base = ca.local(ultima).date() if ultima else ca.local(r[14]).date()
    proxima = base + timedelta(days=int(r[10])) if ultima else base
    saldo = int(r[8]) - int(r[9])
    return {"id": r[0], "plano_id": r[1], "lead": r[2], "paciente": r[3], "fone": r[4], "servico_id": r[5],
            "nome": r[6], "profissional_id": r[7], "total": int(r[8]), "usadas": int(r[9]), "saldo": saldo,
            "intervalo": int(r[10]), "validade_ate": r[11], "estado": r[12], "motivo": r[13],
            "criado_em": r[14], "ultima": ultima, "proxima": proxima, "proxima_n": int(r[9]) + 1,
            "vence_logo": bool(r[11] and saldo > 0 and r[11] - timedelta(days=AVISO_VALIDADE_DIAS) <= hoje)}


def pacote(c, conta_id: int, pacote_id: int, hoje: date | None = None) -> dict | None:
    r = c.execute(f"select {_COLS} from clinica_pacotes k where k.id=%s and k.conta_id=%s",
                  (pacote_id, conta_id)).fetchone()
    return _dict(r, hoje or ca.hoje_br()) if r else None


def listar(c, conta_id: int, hoje: date | None = None, lead: int | None = None) -> list[dict]:
    hoje = hoje or ca.hoje_br()
    try:
        with c.transaction():
            rows = c.execute(
                f"""select {_COLS} from clinica_pacotes k where k.conta_id=%s"""
                + (" and k.prospeccao_id=%s" if lead else "") + """
                     order by k.estado <> 'ativo', k.paciente_nome, k.id limit 300""",
                (conta_id, lead) if lead else (conta_id,)).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [_dict(r, hoje) for r in rows]


def consumos(c, conta_id: int, pacote_id: int) -> list[dict]:
    return [{"evento_id": r[0], "inicio": r[1], "quando": f"{ca.dia_txt(r[1])} {ca.hora_txt(r[1])}", "prof": r[2]}
            for r in c.execute(
                """select u.evento_id, e.inicio, coalesce(p.nome, '')
                     from clinica_pacote_consumos u
                     join eventos_agenda e on e.id = u.evento_id and e.conta_id = u.conta_id
                     left join clinica_profissionais p on p.id = e.profissional_id and p.conta_id = e.conta_id
                    where u.conta_id=%s and u.pacote_id=%s order by e.inicio""", (conta_id, pacote_id)).fetchall()]


def _tem_futuro(c, conta_id: int, k: dict, agora: datetime) -> bool:
    """O paciente já tem a próxima sessão marcada (esse procedimento, ou "Sessão de pacote").

    Conta o atendimento de HOJE que ainda não foi finalizado, mesmo depois da hora: a
    paciente que está na cadeira às 15h não pode receber "sua 3ª sessão já pode ser
    marcada" porque a recepção ainda não clicou Finalizar."""
    return c.execute(
        r"""select 1 from eventos_agenda e
              left join servicos_catalogo s on s.id = e.servico_id and s.conta_id = e.conta_id
             where e.conta_id=%s and e.status='ativo' and e.situacao in ('agendado','confirmado','presente','atendimento')
               and e.inicio >= %s and (e.servico_id = %s or s.categoria = 'sessao')
               and (e.prospeccao_id = %s
                    or (length(%s) >= 8 and right(regexp_replace(coalesce(e.paciente_fone,''), '\D', '', 'g'), 8) = %s))
             limit 1""",
        (conta_id, ca.utc(ca.hoje_br(agora), time(0)), k["servico_id"], k["lead"], ca._digitos(k["fone"]),
         ca._digitos(k["fone"])[-8:])).fetchone() is not None


def precisam_marcar(c, conta_id: int, agora: datetime) -> list[dict]:
    """Pacotes com saldo, a próxima sessão já liberada (ou chegando em 3 dias) e nada marcado."""
    hoje = ca.hoje_br(agora)
    return [k for k in listar(c, conta_id, hoje)
            if k["estado"] == "ativo" and k["saldo"] > 0 and k["proxima"] <= hoje + timedelta(days=3)
            and not _tem_futuro(c, conta_id, k, agora)]


def do_evento(c, conta_id: int, evento_id: int, hoje: date | None = None) -> dict | None:
    """O pacote que ESTE atendimento baixou (a tela do agendamento mostra "sessão 2 de 4")."""
    try:
        with c.transaction():
            r = c.execute("select pacote_id from clinica_pacote_consumos where conta_id=%s and evento_id=%s",
                          (conta_id, evento_id)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    return pacote(c, conta_id, r[0], hoje) if r else None


def para_o_evento(c, conta_id: int, ev: dict) -> dict | None:
    """O pacote que este agendamento vai baixar ao finalizar (o Finalizar não pergunta
    "o médico propôs tratamento?" numa sessão de pacote)."""
    k = _achar(c, conta_id, ev, trava=False)
    return pacote(c, conta_id, k) if k else None


def _primeiro(nome: str | None) -> str:
    from finance.clinica_agente import _palavras
    p = _palavras(nome)
    return p[0] if p else ""


def _achar(c, conta_id: int, ev: dict, trava: bool, todos: bool = False):
    """O pacote que este atendimento baixa. O MESMO procedimento vem antes da "Sessão
    de pacote" genérica (a Peeling não baixa do Laser só porque o Laser é mais antigo),
    e o PACIENTE tem que ser o mesmo: a mãe e o filho no mesmo card/celular não
    dividem saldo (o primeiro nome do agendamento bate com o do pacote).
    `todos=True` devolve todos os que casam (a regra da parcela atrasada olha todos)."""
    try:
        with c.transaction():
            rows = c.execute(
                r"""select k.id, k.paciente_nome from clinica_pacotes k
                      left join servicos_catalogo s on s.id = %s and s.conta_id = k.conta_id
                     where k.conta_id=%s and k.estado='ativo' and k.sessoes_usadas < k.sessoes_total
                       and (k.validade_ate is null or k.validade_ate >= %s)
                       and (k.servico_id = %s or s.categoria = 'sessao')
                       and (k.prospeccao_id = %s
                            or (length(%s) >= 8 and right(regexp_replace(k.paciente_fone, '\D', '', 'g'), 8) = %s))
                     order by (k.servico_id = %s) desc, k.criado_em, k.id limit 20""",
                (ev.get("servico_id"), conta_id, ca.local(ev["inicio"]).date(), ev.get("servico_id"), ev.get("lead"),
                 ca._digitos(ev.get("fone")), ca._digitos(ev.get("fone"))[-8:], ev.get("servico_id"))).fetchall()
    except Exception as e:  # noqa: BLE001
        if "clinica_pacotes" in str(e) and "does not exist" in str(e):
            return [] if todos else None    # a 381 ainda não rodou
        _log.warning("pacotes: não consegui achar o pacote (evento %s)", ev.get("id"), exc_info=True)
        return [] if todos else None
    quem = _primeiro(ev.get("paciente"))
    ids = [r[0] for r in rows if not quem or not _primeiro(r[1]) or _primeiro(r[1]) == quem]
    if todos:
        return ids
    if not ids:
        return None
    if trava:
        ok = c.execute("""select id from clinica_pacotes where id=%s and conta_id=%s and estado='ativo'
                           and sessoes_usadas < sessoes_total for update""", (ids[0], conta_id)).fetchone()
        return ok[0] if ok else None
    return ids[0]


# ------------------------------------------------------------------ nascer

def criar_do_plano(c, conta_id: int, plano: dict, agora: datetime | None = None) -> list[int]:
    """Plano aceito → um pacote por procedimento do catálogo. Idempotente (um plano
    só vira pacote uma vez). Produto e item avulso não viram saldo."""
    agora = agora or datetime.now(timezone.utc)
    try:
        with c.transaction():
            if c.execute("select 1 from clinica_pacotes where conta_id=%s and plano_id=%s limit 1",
                         (conta_id, plano["id"])).fetchone():
                return []
    except Exception:  # noqa: BLE001 — sem a 381
        return []
    cfg = config(c, conta_id)
    tipos = {t["id"]: t for t in cc.listar_tipos(c, conta_id, so_ativos=False)}
    validade = _mais_meses(ca.hoje_br(agora), cfg["validade_meses"])
    ids = []
    for i in plano["itens"]:
        t = tipos.get(i.get("servico_id") or 0)
        if not t:
            continue
        ids.append(c.execute(
            """insert into clinica_pacotes (conta_id, plano_id, prospeccao_id, paciente_nome, paciente_fone,
                                            servico_id, nome, profissional_id, sessoes_total, intervalo_dias,
                                            validade_ate)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (conta_id, plano["id"], plano.get("lead"), plano["paciente"], plano.get("fone") or "", t["id"],
             t["nome"], plano.get("profissional_id"), int(i["sessoes"]),
             int(t["volta_dias"]) if t.get("volta_dias") and 1 <= int(t["volta_dias"]) <= 365 else INTERVALO_PADRAO,
             validade)).fetchone()[0])
    return ids


# ------------------------------------------------------------------ finalizar: baixa e retorno

def ao_finalizar(c, conta_id: int, evento_id: int, retorno_dias: int | None = None) -> dict:
    """Chamado por `clinica_agenda.mudar_situacao` quando o atendimento é finalizado.
    Baixa uma sessão do pacote (se houver) e agenda o retorno pedido. Na mesma
    transação da mudança de status: finalizou, baixou."""
    out = {"pacote": None, "retorno": None}
    ev = ca.evento(c, conta_id, evento_id)
    if not ev:
        return out
    k = _achar(c, conta_id, ev, trava=True)
    if k and c.execute("""insert into clinica_pacote_consumos (conta_id, pacote_id, evento_id)
                          values (%s,%s,%s) on conflict (evento_id) do nothing returning id""",
                       (conta_id, k, evento_id)).fetchone():
        c.execute("""update clinica_pacotes set sessoes_usadas = sessoes_usadas + 1,
                            estado = case when sessoes_usadas + 1 >= sessoes_total then 'concluido' else estado end,
                            atualizado_em = now()
                      where id=%s and conta_id=%s""", (k, conta_id))
        out["pacote"] = k
    if retorno_dias and 1 <= int(retorno_dias) <= 730:
        vence = ca.local(ev["inicio"]).date() + timedelta(days=int(retorno_dias))
        try:
            with c.transaction():
                r = c.execute(
                    """insert into clinica_retornos (conta_id, prospeccao_id, evento_id, profissional_id,
                                                     paciente_nome, paciente_fone, vence_em)
                       values (%s,%s,%s,%s,%s,%s,%s) on conflict (evento_id) do nothing returning id""",
                    (conta_id, ev["lead"], evento_id, ev["profissional_id"], ev["paciente"], ev["fone"] or "",
                     vence)).fetchone()
                out["retorno"] = r[0] if r else None
        except Exception:  # noqa: BLE001 — sem a 381
            pass
    return out


def atrasado(c, conta_id: int, k: dict, hoje: date | None = None) -> bool:
    """Alguma parcela do plano deste pacote vencida e em aberto."""
    if not k.get("plano_id"):
        return False
    hoje = hoje or ca.hoje_br()
    try:
        with c.transaction():
            ids = c.execute("select titulos from clinica_planos where id=%s and conta_id=%s",
                            (k["plano_id"], conta_id)).fetchone()
            ids = [int(x) for x in (ids[0] if ids and isinstance(ids[0], list) else [])]
            if not ids:
                return False
            return c.execute("""select 1 from titulos where conta_id=%s and id = any(%s) and status='aberto'
                                 and vencimento < %s limit 1""", (conta_id, ids, hoje)).fetchone() is not None
    except Exception:  # noqa: BLE001
        return False


def bloqueio(c, conta_id: int, lead: int | None, fone: str, servico_id: int | None,
            paciente: str = "") -> str | None:
    """A regra da clínica (decisão C, nasce desligada): sessão de pacote com parcela
    atrasada não se marca. Devolve o texto do erro, ou None."""
    if not config(c, conta_id)["bloqueia_atrasado"] or not servico_id:
        return None
    ev = {"servico_id": servico_id, "lead": lead, "fone": fone, "inicio": datetime.now(timezone.utc),
          "paciente": paciente}
    for kid in _achar(c, conta_id, ev, trava=False, todos=True):
        k = pacote(c, conta_id, kid)
        if k and atrasado(c, conta_id, k):
            return "Parcela do plano em atraso: pela regra da clínica, a sessão só é marcada com a parcela em dia."
    return None


def encerrar(c, conta_id: int, pacote_id: int, motivo: str, membro_id: int | None) -> bool:
    """Encerrar com saldo (desistência, reembolso): o saldo fica registrado, com o motivo."""
    r = c.execute("""update clinica_pacotes set estado='encerrado', encerrado_motivo=%s, encerrado_por=%s,
                            encerrado_em=now(), atualizado_em=now()
                      where id=%s and conta_id=%s and estado in ('ativo','vencido') returning id""",
                  ((motivo or "").strip()[:200] or "encerrado pela clínica", membro_id, pacote_id, conta_id)).fetchone()
    return r is not None


# ------------------------------------------------------------------ retornos

def retornos(c, conta_id: int, agora: datetime, dias: int = 14) -> list[dict]:
    """Os retornos que esperam marcar, vencendo nos próximos `dias` ou já vencidos."""
    hoje = ca.hoje_br(agora)
    try:
        with c.transaction():
            rows = c.execute(
                """select r.id, r.prospeccao_id, r.paciente_nome, r.paciente_fone, r.vence_em, r.profissional_id,
                          coalesce(p.nome, ''), r.estado, r.criado_em
                     from clinica_retornos r
                     left join clinica_profissionais p on p.id = r.profissional_id and p.conta_id = r.conta_id
                    where r.conta_id=%s and r.estado='aguardando' and r.vence_em <= %s
                    order by r.vence_em""", (conta_id, hoje + timedelta(days=dias))).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [{"id": r[0], "lead": r[1], "paciente": r[2], "fone": r[3], "vence_em": r[4], "profissional_id": r[5],
             "prof": r[6], "vencido": r[4] < hoje, "criado_em": r[8]} for r in rows]


def fechar_retornos(c, conta_id: int, agora: datetime) -> int:
    """Marcou com o profissional depois do pedido → 'marcado'; 30 dias depois do prazo
    sem marcar → 'vencido' (sai da fila)."""
    n = 0
    try:
        with c.transaction():
            n += len(c.execute(
                r"""update clinica_retornos r set estado='marcado', atualizado_em=now(),
                           marcado_evento_id = (select e.id from eventos_agenda e
                                                 where e.conta_id = r.conta_id and e.id <> r.evento_id
                                                   and e.profissional_id = r.profissional_id
                                                   and e.situacao not in ('cancelou','faltou') and e.status='ativo'
                                                   and e.inicio > (select o.inicio from eventos_agenda o
                                                                    where o.id = r.evento_id and o.conta_id = r.conta_id)
                                                   and (e.prospeccao_id = r.prospeccao_id
                                                        or (length(regexp_replace(r.paciente_fone, '\D', '', 'g')) >= 8
                                                            and right(regexp_replace(coalesce(e.paciente_fone,''), '\D', '', 'g'), 8)
                                                                = right(regexp_replace(r.paciente_fone, '\D', '', 'g'), 8)))
                                                 order by e.inicio limit 1)
                     where r.conta_id=%s and r.estado='aguardando'
                       and exists (select 1 from eventos_agenda e
                                    where e.conta_id = r.conta_id and e.id <> r.evento_id
                                      and e.profissional_id = r.profissional_id
                                      and e.situacao not in ('cancelou','faltou') and e.status='ativo'
                                      and e.inicio > (select o.inicio from eventos_agenda o
                                                       where o.id = r.evento_id and o.conta_id = r.conta_id)
                                      and (e.prospeccao_id = r.prospeccao_id
                                           or (length(regexp_replace(r.paciente_fone, '\D', '', 'g')) >= 8
                                               and right(regexp_replace(coalesce(e.paciente_fone,''), '\D', '', 'g'), 8)
                                                   = right(regexp_replace(r.paciente_fone, '\D', '', 'g'), 8))))
                    returning r.id""", (conta_id,)).fetchall())
            # o horário que fechou o retorno foi cancelado (ou faltou): volta pra fila
            n += len(c.execute(
                """update clinica_retornos r set estado='aguardando', marcado_evento_id=null, atualizado_em=now()
                    where r.conta_id=%s and r.estado='marcado'
                      and exists (select 1 from eventos_agenda e where e.id = r.marcado_evento_id
                                     and e.conta_id = r.conta_id
                                     and (e.status = 'cancelado' or e.situacao in ('cancelou','faltou')))
                   returning r.id""", (conta_id,)).fetchall())
            n += len(c.execute("""update clinica_retornos set estado='vencido', atualizado_em=now()
                                   where conta_id=%s and estado='aguardando' and vence_em < %s returning id""",
                               (conta_id, ca.hoje_br(agora) - timedelta(days=RETORNO_PERDE_DIAS))).fetchall())
    except Exception:  # noqa: BLE001 — sem a 381
        return 0
    return n


def dispensar_retorno(c, conta_id: int, retorno_id: int) -> bool:
    r = c.execute("""update clinica_retornos set estado='dispensado', atualizado_em=now()
                      where id=%s and conta_id=%s and estado='aguardando' returning id""",
                  (retorno_id, conta_id)).fetchone()
    return r is not None


# ------------------------------------------------------------------ os lembretes

def _ordinal(n: int) -> str:
    return f"{n}ª"


def texto_lembrete(tipo: str, nome: str, **kw) -> str:
    """Nenhum diz o procedimento: "sua próxima sessão", "seu retorno", "seu pacote"."""
    from finance.voltar_a_chamar import primeiro_nome
    n = primeiro_nome(nome)
    oi = f"Oi{', ' + n if n else ''}!"
    if tipo == "sessao":
        return (f"{oi} Sua {_ordinal(kw['numero'])} sessão já pode ser marcada 😊 Quer que eu veja um horário "
                "pra você? É só responder por aqui.")
    if tipo == "retorno":
        return (f"{oi} Está chegando a hora do seu retorno{' com ' + kw['prof'] if kw.get('prof') else ''} "
                f"(até {kw['vence']:%d/%m}). Qual o melhor dia pra você? É só responder por aqui 😊")
    s = kw["saldo"]
    return (f"{oi} Você ainda tem {s} {'sessão' if s == 1 else 'sessões'} do seu pacote, "
            f"válida{'s' if s != 1 else ''} até {kw['validade']:%d/%m/%Y}. Quer marcar? É só responder por aqui 😊")


def _ja_lembrou(c, conta_id: int, tipo: str, ref_id: int, desde: datetime | None = None) -> bool:
    return c.execute("select 1 from clinica_lembretes where conta_id=%s and tipo=%s and ref_id=%s"
                     " and estado <> 'falhou'"
                     + (" and enviado_em >= %s" if desde else "") + " limit 1",
                     (conta_id, tipo, ref_id) + ((desde,) if desde else ())).fetchone() is not None


def _mandar_lembrete(c, conta_id: int, conv: int, destino: str, tipo: str, ref_id: int, texto: str,
                     agora: datetime) -> bool:
    from finance import clinica_planos as cp
    lid = c.execute("""insert into clinica_lembretes (conta_id, conversa_id, tipo, ref_id, enviado_em)
                       values (%s,%s,%s,%s,%s) returning id""", (conta_id, conv, tipo, ref_id, agora)).fetchone()[0]
    c.commit()                              # registrado ANTES: nunca sai duas vezes
    res = cp._mandar(c, conta_id, conv, destino, texto)
    if res.get("ok"):
        c.execute("update clinica_lembretes set mensagem_id=%s where id=%s and conta_id=%s",
                  (res.get("mensagem_id"), lid, conta_id))
    else:
        # não saiu (chip caído): não queima o lembrete nem o "1 por dia" do paciente
        c.execute("update clinica_lembretes set estado='falhou' where id=%s and conta_id=%s", (lid, conta_id))
    c.commit()
    return bool(res.get("ok"))


def lembrar(c, conta_id: int, agora: datetime) -> dict:
    """Uma passada dos lembretes: dentro da janela, 1 automática por paciente por
    dia (somando vaga, plano e voltar a chamar), sem quem pediu SAIR/PARAR."""
    from finance import clinica_planos as cp
    from finance import clinica_vagas as cvg
    from finance import funil_regua as fr
    out = {"sessao": 0, "retorno": 0, "validade": 0, "vencidos": 0}
    hoje = ca.hoje_br(agora)
    try:
        with c.transaction():
            out["vencidos"] = len(c.execute(
                """update clinica_pacotes set estado='vencido', atualizado_em=now()
                    where conta_id=%s and estado='ativo' and validade_ate < %s returning id""",
                (conta_id, hoje)).fetchall())
    except Exception:  # noqa: BLE001 — sem a 381
        return out
    fechar_retornos(c, conta_id, agora)
    c.commit()
    cfg = config(c, conta_id)
    if cfg["lembretes"] != "ligado" or not fr.dentro_da_janela(agora, fr.config(c, conta_id)):
        c.commit()
        return out
    bloq = cvg._bloqueados(c, conta_id)
    inicio_dia = ca.utc(hoje, time(0))
    profs = {p["id"]: p["nome"] for p in cc.listar_profissionais(c, conta_id, so_ativos=False)}

    def pode(lead, fone):
        conv = ca._conversa(c, conta_id, {"lead": lead, "fone": fone})
        destino = cp._destino_da_conversa(c, conta_id, conv) if conv else ""
        if not conv or not destino or destino[-8:] in bloq or cvg._recebeu_hoje(c, conta_id, conv, inicio_dia):
            return None, ""
        return conv, destino

    # o retorno primeiro (é o médico que pediu), depois a sessão, depois a validade
    for r in retornos(c, conta_id, agora, dias=cfg["retorno_aviso_dias"]):
        if r["vencido"] or _ja_lembrou(c, conta_id, "retorno", r["id"]):
            continue
        if r["criado_em"] > agora - timedelta(days=2):
            continue                        # pedido agora ("volta em 7 dias"): o paciente acabou de sair
        conv, destino = pode(r["lead"], r["fone"])
        if conv and _mandar_lembrete(c, conta_id, conv, destino, "retorno", r["id"],
                                     texto_lembrete("retorno", r["paciente"], prof=profs.get(r["profissional_id"], ""),
                                                    vence=r["vence_em"]), agora):
            out["retorno"] += 1
    for k in precisam_marcar(c, conta_id, agora):
        if k["proxima"] > hoje or k["usadas"] == 0 and k["criado_em"] > agora - timedelta(days=2):
            continue                        # ainda não liberou; o plano acabou de ser aceito
        if _ja_lembrou(c, conta_id, "sessao", k["id"], agora - timedelta(days=RELEMBRAR_DIAS)):
            continue
        conv, destino = pode(k["lead"], k["fone"])
        if conv and _mandar_lembrete(c, conta_id, conv, destino, "sessao", k["id"],
                                     texto_lembrete("sessao", k["paciente"], numero=k["proxima_n"]), agora):
            out["sessao"] += 1
    for k in listar(c, conta_id, hoje):
        if k["estado"] != "ativo" or not k["vence_logo"] or _ja_lembrou(c, conta_id, "validade", k["id"]):
            continue
        if k["criado_em"] > agora - timedelta(days=2) or _tem_futuro(c, conta_id, k, agora):
            continue                        # acabou de comprar, ou já marcou as próximas
        conv, destino = pode(k["lead"], k["fone"])
        if conv and _mandar_lembrete(c, conta_id, conv, destino, "validade", k["id"],
                                     texto_lembrete("validade", k["paciente"], saldo=k["saldo"],
                                                    validade=k["validade_ate"]), agora):
            out["validade"] += 1
    c.commit()
    return out


# ------------------------------------------------------------------ o poller

def rodar(pool, agora: datetime | None = None) -> dict:
    agora = agora or datetime.now(timezone.utc)
    total = {"contas": 0, "sessao": 0, "retorno": 0, "validade": 0, "vencidos": 0}
    with pool.connection() as lockc:
        if not lockc.execute("select pg_try_advisory_lock(%s)", (_LOCK,)).fetchone()[0]:
            return total
        try:
            with pool.connection() as c:
                try:
                    with c.transaction():
                        contas = [r[0] for r in c.execute(
                            """select conta_id from clinica_pacotes where estado='ativo'
                               union select conta_id from clinica_retornos where estado='aguardando'""").fetchall()]
                except Exception:  # noqa: BLE001 — sem a 381
                    contas = []
                for conta_id in contas:
                    try:
                        r = lembrar(c, conta_id, agora)
                        total["contas"] += 1
                        for k in ("sessao", "retorno", "validade", "vencidos"):
                            total[k] += r[k]
                    except Exception:  # noqa: BLE001
                        c.rollback()
                        _log.warning("pacotes: conta %s falhou", conta_id, exc_info=True)
        finally:
            lockc.execute("select pg_advisory_unlock(%s)", (_LOCK,))
    return total
