"""O orçamento que a IA da regra por número MONTA — e alguém CONFERE antes de ir
(migração 392, etapa 3 do vendedor IA).

A DECISÃO DO DONO (26/09/2026): "o ideal é sempre dar valor aproximado e deixar
alguém conferir antes de fechar até a IA ficar 100% treinada". Então:

  1. A IA monta o orçamento (pacote + adicionais do catálogo, sinal de 30%, validade
     de 7 dias) e diz ao cliente o valor APROXIMADO — só se os itens estão liberados
     pra IA dizer preço — e que a proposta sai conferida.
  2. Quem confere (a Jacqueline, na Prime) recebe o aviso com um cartão no app:
     "Conferir e mandar" (sai o link pro cliente, pelo chip da conversa), "Editar"
     (o orçamento de sempre) ou "Descartar".
  3. O cliente aprovou online: a data fica SEGURADA por 72h esperando o sinal —
     orçamento de vendedor segue com a regra de sempre (até 60 dias antes da festa).
     Sob trava do DIA: dia que já tem festa não é segurado de novo; a equipe decide.
  4. O relógio (`rodar`): a mensagem do sinal na aprovação (com o Pix copia-e-cola,
     se a empresa tem chave), lembretes em 24h e 48h, "a data foi liberada" quando a
     reserva vence, e "data confirmada!" quando o sinal é confirmado.
  5. Foto ou documento do cliente depois da aprovação é o COMPROVANTE: o dono recebe
     "comprovante chegou" com o botão de confirmar o sinal (o de sempre).

Tudo tolerante: banco sem a 392 = sem orçamento pela IA, e o resto segue.
"""
from __future__ import annotations

import logging
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone

from finance import agenda as ag

_log = logging.getLogger(__name__)

_LOCK = 771173          # o relógio do sinal
_LOCK_DIA = 771174      # + (conta, dia): uma reserva de data por vez no mesmo dia

MAX_FALHAS = 5
INTERVALO_FALHA = timedelta(minutes=15)
#: a janela das mensagens que o relógio inicia ao cliente (hora de Brasília)
HORAS_CLIENTE = (8, 20)


# ------------------------------------------------------------------ config

def config(c, regra: dict | None) -> dict | None:
    """A parte "orçamento" da regra, ou None quando a IA não monta orçamento."""
    if not regra or not regra.get("id"):
        return None
    try:
        with c.transaction():
            r = c.execute("""select orc_ia, orc_conferente_id, orc_sinal_pct, orc_validade_dias,
                                    orc_reserva_h, aviso_agenda_membro_id, aviso_dono_membro_id
                               from chip_regra where id=%s""", (regra["id"],)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not r or not r[0]:
        return None
    return {"conferente_id": r[1] or r[5], "sinal_pct": int(r[2]), "validade_dias": int(r[3]),
            "reserva_h": int(r[4]), "dono_id": r[6]}


def config_tela(c, regra_id: int | None) -> dict:
    base = {"ligado": False, "conferente_id": None, "sinal_pct": 30, "validade_dias": 7,
            "reserva_h": 72}
    if not regra_id:
        return base
    try:
        with c.transaction():
            r = c.execute("""select orc_ia, orc_conferente_id, orc_sinal_pct, orc_validade_dias,
                                    orc_reserva_h from chip_regra where id=%s""",
                          (regra_id,)).fetchone()
    except Exception:  # noqa: BLE001
        return base
    if not r:
        return base
    return {"ligado": bool(r[0]), "conferente_id": r[1], "sinal_pct": int(r[2]),
            "validade_dias": int(r[3]), "reserva_h": int(r[4])}


def salvar(c, conta_id: int, chip_id: int, f: dict) -> str | None:
    """Grava a parte "orçamento" da regra. Quem confere tem que ser da empresa."""
    conf = None
    try:
        conf = int(f.get("orc_conferente_id") or 0) or None
    except (TypeError, ValueError):
        conf = None
    if conf and not c.execute("select 1 from membros where id=%s and conta_id=%s and ativo",
                              (conf, conta_id)).fetchone():
        return "Escolha quem confere o orçamento entre as pessoas da equipe desta empresa."

    def _n(k, lo, hi, padrao):
        try:
            return max(lo, min(hi, int(f.get(k))))
        except (TypeError, ValueError):
            return padrao
    c.execute("""update chip_regra set orc_ia=%s, orc_conferente_id=%s, orc_sinal_pct=%s,
                        orc_validade_dias=%s, orc_reserva_h=%s
                  where conta_id=%s and chip_id=%s""",
              (bool(f.get("orc_ia")), conf, _n("orc_sinal_pct", 0, 100, 30),
               _n("orc_validade_dias", 1, 60, 7), _n("orc_reserva_h", 12, 240, 72),
               conta_id, chip_id))
    return None


def tem_pix(pool, conta_id: int) -> bool:
    try:
        from finance import pix
        return bool((pix.da_conta(pool, conta_id) or {}).get("chave"))
    except Exception:  # noqa: BLE001
        return False


# ------------------------------------------------------------------ montar

def _reais(centavos: int) -> str:
    """R$ 2.345 / R$ 2.345,67 — com os centavos quando há: é o valor que o Pix cobra."""
    c = int(centavos or 0)
    inteiro = f"{c // 100:,}".replace(",", ".")
    return f"R$ {inteiro}" + (f",{c % 100:02d}" if c % 100 else "")


def _pct(ocfg: dict) -> int:
    """O sinal em %, com 0 valendo (sem sinal) — `or 30` transformaria 0 em 30."""
    v = (ocfg or {}).get("sinal_pct")
    return 30 if v is None else max(0, min(100, int(v)))


def parcelas(total_centavos: int, evento: dict, ocfg: dict, hoje: date | None = None) -> list[dict]:
    """O plano de pagamento do orçamento da IA: o SINAL (a primeira parcela, com a
    frase que o sistema inteiro reconhece — `vendas.OBS_SINAL`) e o saldo, 30 dias
    antes da festa (ou no dia, se a festa é antes disso). Quem confere pode mudar."""
    from finance import vendas
    hoje = hoje or ag.agora_brt().date()
    sinal = round(int(total_centavos) * _pct(ocfg) / 100)
    festa = ag.parse_data((evento or {}).get("data"))
    # o sinal vence em 3 dias (nunca depois da festa); o saldo, 30 dias antes da festa
    # — e nunca antes do sinal
    venc_sinal = min(hoje + timedelta(days=3), festa) if festa else hoje + timedelta(days=3)
    out = []
    if sinal:
        out.append({"obs": vendas.OBS_SINAL, "venc": venc_sinal.isoformat(),
                    "forma": "Pix", "valor_centavos": sinal})
    saldo = int(total_centavos) - sinal
    if saldo > 0:
        venc = (festa - timedelta(days=30)) if festa else hoje + timedelta(days=30)
        venc = max(venc, venc_sinal if sinal else hoje)
        if festa:
            venc = min(venc, festa)
        out.append({"obs": "Saldo", "venc": venc.isoformat(), "forma": "Pix",
                    "valor_centavos": saldo})
    return out


def condicoes(base: str, ocfg: dict) -> str:
    validade = f"{base} Validade: {int(ocfg.get('validade_dias') or 7)} dias."
    if not _pct(ocfg):
        return validade
    return (f"{validade} O sinal de {_pct(ocfg)}% confirma a reserva da data; depois da "
            f"aprovação a data fica segurada por {int(ocfg.get('reserva_h') or 72)} horas "
            "esperando o sinal.")


def para_conferir(pool, c, conta_id: int, conversa_id: int, conv, orc_id: int, itens: list[dict],
                  ocfg: dict, *, preco_liberado: bool, total_centavos: int, quem: str) -> str:
    """O orçamento montado vai pra FILA DA CONFERÊNCIA, e a pessoa que confere é
    avisada. Devolve o texto pro cliente (quem manda é o agente)."""
    c.execute("""insert into ia_orcamentos (orcamento_id, conta_id, prospeccao_id, conversa_id)
                 values (%s,%s,%s,%s) on conflict (orcamento_id) do nothing""",
              (orc_id, conta_id, conv[1], conversa_id))
    c.commit()
    from finance import chip_regra as _cr
    nome = ""
    if ocfg.get("conferente_id"):
        r = c.execute("select coalesce(nullif(nome,''),'') from membros where id=%s and conta_id=%s",
                      (ocfg["conferente_id"], conta_id)).fetchone()
        from finance.voltar_a_chamar import primeiro_nome
        n = primeiro_nome(r[0]) if r else ""
        nome = n[:1].upper() + n[1:].lower() if n else ""
        _cr.notificar(pool, conta_id, ocfg["conferente_id"], "📝 Orçamento da IA pra conferir",
                      f"{quem}: {', '.join(i['nome'] for i in itens[:3])}"
                      + (f" · {_reais(total_centavos)}" if total_centavos else ""),
                      f"/cockpit/ia-orcamento/{orc_id}")
    valor = (f" Fica em torno de {_reais(total_centavos)}." if preco_liberado and total_centavos
             else "")
    return (f"Montei o seu orçamento 😊{valor} "
            + (f"A {nome} confere" if nome else "A equipe confere")
            + " e eu te mando o link pra você ver tudo e aprovar.")


# ------------------------------------------------------------------ a conferência

def pendente(pool, conta_id: int, orc_id: int) -> dict | None:
    """O cartão da conferência: o orçamento montado pela IA, se ainda espera."""
    try:
        with pool.connection() as c:
            r = c.execute(
                """select o.id, o.numero, o.token, o.itens, o.evento, o.setup_centavos, o.parcelas,
                          i.estado, i.prospeccao_id, i.conversa_id,
                          coalesce(nullif(p.contato,''), nullif(p.empresa,''), o.empresa, 'Cliente')
                     from ia_orcamentos i join orcamentos o on o.id = i.orcamento_id
                     left join prospeccao p on p.id = i.prospeccao_id and p.conta_id = i.conta_id
                    where i.orcamento_id=%s and i.conta_id=%s and o.conta_id=%s""",
                (orc_id, conta_id, conta_id)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not r:
        return None
    from finance import vendas
    return {"id": r[0], "numero": r[1], "token": r[2], "itens": r[3] or [], "evento": r[4] or {},
            "total_centavos": int(r[5] or 0), "sinal_centavos": vendas.valor_do_sinal(r[6]),
            "estado": r[7], "lead_id": r[8], "conversa_id": r[9], "quem": r[10]}


def pode_conferir(pool, conta_id: int, membro_id: int, papel: str, orc_id: int) -> bool:
    """Quem confere: a pessoa escolhida na regra, ou a gerência."""
    if papel in ("dono", "gestor"):
        return True
    try:
        with pool.connection() as c:
            r = c.execute(
                """select coalesce(cr.orc_conferente_id, cr.aviso_agenda_membro_id)
                     from ia_orcamentos i join conversas cv on cv.id = i.conversa_id
                     join chip_regra cr on cr.conta_id = cv.conta_id
                      and cr.chip_id = coalesce(cv.chip_id, cv.conta_id)
                    where i.orcamento_id=%s and i.conta_id=%s""", (orc_id, conta_id)).fetchone()
    except Exception:  # noqa: BLE001
        return False
    return bool(r and r[0] == membro_id)


def mandar(pool, conta_id: int, membro_id: int, orc_id: int) -> dict:
    """Conferido: o link vai pro cliente pela conversa (o chip dela), gravado como fala
    da IA, e o envio fica registrado como o de qualquer proposta. Reivindica ANTES —
    dois toques não mandam duas vezes."""
    from finance import agente
    # os dados principais (regra do dono, 01/10/2026) — ANTES de reivindicar, pra
    # recusa não tirar o orçamento da fila de conferir
    from finance import contrato as _ctr
    falta = _ctr.pendencias_pra_mandar(pool, conta_id, orc_id)
    if falta:
        return {"ok": False, "erro": _ctr.texto_pendencias(falta), "faltam": falta}
    with pool.connection() as c:
        pegou = c.execute(
            """update ia_orcamentos set estado='enviado', conferido_por=%s, conferido_em=now()
                where orcamento_id=%s and conta_id=%s and estado='conferir'
            returning conversa_id""", (membro_id, orc_id, conta_id)).fetchone()
        c.commit()
    if not pegou:
        return {"ok": False, "erro": "Este orçamento já foi conferido."}
    try:
        return _mandar_link(pool, conta_id, orc_id, pegou[0])
    except Exception:  # noqa: BLE001 — nada saiu: volta pra fila, e o botão tenta de novo
        _log.warning("ia_orcamento: mandar %s falhou", orc_id, exc_info=True)
        _devolver(pool, orc_id)
        return {"ok": False, "erro": "Não consegui mandar. Tente de novo."}


def _devolver(pool, orc_id: int) -> None:
    with pool.connection() as c:
        c.execute("""update ia_orcamentos set estado='conferir', conferido_por=null,
                            conferido_em=null where orcamento_id=%s""", (orc_id,))
        c.commit()


def _mandar_link(pool, conta_id: int, orc_id: int, conversa_id: int) -> dict:
    from finance import agente
    d = pendente(pool, conta_id, orc_id)
    if not d or not conversa_id:
        _devolver(pool, orc_id)
        return {"ok": False, "erro": "Não achei a conversa deste orçamento."}
    from finance.email_sender import _app_url
    link = f"{_app_url()}/proposta/{d['token']}"
    texto = ("Aqui está o seu orçamento 😊\n" + link
             + "\n\nÉ só abrir, conferir e aprovar por lá."
             + (f" O sinal de {_reais(d['sinal_centavos'])} garante a data." if d["sinal_centavos"] else ""))
    with pool.connection() as c:
        r = c.execute("""select coalesce(p.whatsapp, p.telefone, cv.contato_ref)
                           from conversas cv left join prospeccao p
                                on p.id = cv.prospeccao_id and p.conta_id = cv.conta_id
                          where cv.id=%s and cv.conta_id=%s""", (conversa_id, conta_id)).fetchone()
        res = (agente._mandar(c, conta_id, "whatsapp", r[0], texto, conversa_id)
               if r and r[0] else {"ok": False})
        if not (res or {}).get("ok"):
            c.rollback()
            _devolver(pool, orc_id)
            return {"ok": False, "erro": "Não consegui mandar pelo WhatsApp. Tente de novo."}
        try:
            agente._add_bot_msg(c, conversa_id, "whatsapp", texto, res.get("sid"))
            c.commit()
        except Exception:  # noqa: BLE001 — saiu; só não ficou gravado na conversa
            c.rollback()
    try:
        from finance import proposta_email as _pe
        _pe.registrar(pool, conta_id, orc_id, destino=str(r[0] or "")[:200], remetente_usado="",
                      ok=True, por="agente", canal="whatsapp")
    except Exception:  # noqa: BLE001
        _log.warning("ia_orcamento: envio da proposta %s não registrado", orc_id, exc_info=True)
    return {"ok": True}


def descartar(pool, conta_id: int, membro_id: int, orc_id: int) -> dict:
    with pool.connection() as c:
        n = c.execute("""update ia_orcamentos set estado='descartado', conferido_por=%s,
                                conferido_em=now()
                          where orcamento_id=%s and conta_id=%s and estado='conferir'""",
                      (membro_id, orc_id, conta_id)).rowcount
        c.commit()
    return {"ok": bool(n)}


# ------------------------------------------------------------------ a reserva de 72h

def da_ia(pool, conta_id: int, orc_id: int) -> dict | None:
    """O orçamento é da IA? Devolve a config de orçamento da regra dele (pra reserva)."""
    try:
        with pool.connection() as c:
            r = c.execute(
                """select cr.orc_reserva_h, coalesce(cr.orc_conferente_id, cr.aviso_agenda_membro_id),
                          cr.aviso_dono_membro_id
                     from ia_orcamentos i join conversas cv on cv.id = i.conversa_id
                     left join chip_regra cr on cr.conta_id = cv.conta_id
                      and cr.chip_id = coalesce(cv.chip_id, cv.conta_id)
                    where i.orcamento_id=%s and i.conta_id=%s
                      -- só o que saiu pelo "Conferir e mandar": editado e mandado pela
                      -- tela de sempre, ou descartado, é orçamento de vendedor
                      and i.estado='enviado'""", (orc_id, conta_id)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not r:
        return None
    return {"reserva_h": int(r[0] or 72), "conferente_id": r[1], "dono_id": r[2]}


@contextmanager
def trava_do_dia(pool, conta_id: int, dia: date):
    """Uma reserva de data por vez no MESMO DIA da mesma conta: duas aprovações no
    mesmo segundo não seguram a mesma data duas vezes. Trava de sessão numa conexão
    só dela, com prazo (não prende o pool esperando)."""
    import time
    chave = int(conta_id) * 100000 + (dia.toordinal() % 100000)
    with pool.connection() as lk:
        prazo = time.monotonic() + 15
        pegou = False
        while not pegou:
            pegou = lk.execute("select pg_try_advisory_lock(%s::int, %s::int)",
                               (_LOCK_DIA, chave % 2147483647)).fetchone()[0]
            if not pegou:
                if time.monotonic() > prazo:
                    break
                time.sleep(0.3)
        try:
            yield pegou
        finally:
            if pegou:
                lk.execute("select pg_advisory_unlock(%s::int, %s::int)",
                           (_LOCK_DIA, chave % 2147483647))
            lk.commit()


def festa_no_dia(pool, conta_id: int, inicio: datetime) -> bool:
    """O dia já tem festa (reservada ou segurada)? Um salão, uma festa por dia — a
    decisão de caber duas é da equipe, não da IA."""
    loc = inicio.astimezone(ag.BRT)
    d0 = datetime(loc.year, loc.month, loc.day, tzinfo=ag.BRT)
    with pool.connection() as c:
        return bool(c.execute(
            """select 1 from eventos_agenda
                where conta_id=%s and status in ('ativo','pre_reservado')
                  and (tipo_evento is not null or status='pre_reservado')
                  and inicio >= %s and inicio < %s limit 1""",
            (conta_id, d0, d0 + timedelta(days=1))).fetchone())


def prazo(inicio: datetime, horas: int, agora: datetime | None = None) -> datetime:
    """72h a partir de agora, nunca depois da festa."""
    agora = agora or datetime.now(timezone.utc)
    return min(agora + timedelta(hours=int(horas or 72)), inicio)


def bloquear(pool, conta_id: int, orc_id: int, cfg: dict, motivo: str, quando: datetime) -> None:
    """A data não foi segurada (o dia já tem festa): marca, e a equipe decide."""
    from finance import chip_regra as _cr
    try:
        with pool.connection() as c:
            c.execute("update ia_orcamentos set bloqueio=%s where orcamento_id=%s and conta_id=%s",
                      (motivo, orc_id, conta_id))
            c.commit()
    except Exception:  # noqa: BLE001
        pass
    dia = f"{quando.astimezone(ag.BRT):%d/%m}"
    titulo, corpo = (
        ("⚠️ Aprovado, mas a data já tem festa",
         f"O cliente aprovou o orçamento da IA pra {dia}, e esse dia já tem festa ou data "
         "segurada. A data NÃO foi segurada: decida e fale com o cliente.")
        if motivo == "dia_com_festa" else
        ("⚠️ Aprovado, mas a data não foi segurada",
         f"O cliente aprovou o orçamento da IA pra {dia}, e não consegui segurar a data "
         "agora. Confira a agenda e segure pela tela do orçamento."))
    for mid in {cfg.get("conferente_id"), cfg.get("dono_id")} - {None}:
        _cr.notificar(pool, conta_id, mid, titulo, corpo, f"/cockpit/orcamentos/{orc_id}")


# ------------------------------------------------------------------ o comprovante

_MIDIA = ("📷", "📄", "🖼")


def _ultima_e_midia(c, conversa_id: int | None, texto: str) -> bool:
    """A última mensagem do cliente é uma foto ou documento? Pela coluna da mídia
    (a foto com legenda também conta); banco sem a coluna, pelo marcador do texto."""
    if conversa_id:
        try:
            with c.transaction():
                r = c.execute("""select coalesce(midia_tipo,'') from mensagens
                                  where conversa_id=%s and autor='lead'
                                  order by criado_em desc, id desc limit 1""",
                              (conversa_id,)).fetchone()
            return bool(r and r[0] in ("imagem", "documento"))
        except Exception:  # noqa: BLE001
            pass
    return (texto or "").strip().startswith(_MIDIA)


def comprovante(pool, c, conta_id: int, lead_id: int, texto: str | None,
                conversa_id: int | None = None) -> str | None:
    """O COMPROVANTE do sinal: foto ou documento do cliente com o orçamento da IA
    aprovado, a mensagem do sinal já enviada, a data ainda SEGURADA e o sinal em
    aberto (e maior que zero). Avisa o dono e devolve o texto pro cliente — UMA vez:
    depois disso, foto é foto (referência de decoração, print), e a IA responde.
    None quando não é o caso."""
    if not _ultima_e_midia(c, conversa_id, texto or ""):
        return None
    try:
        with c.transaction():
            r = c.execute(
                """select i.orcamento_id, cr.aviso_dono_membro_id,
                          coalesce(nullif(p.contato,''), nullif(p.empresa,''), 'O cliente'),
                          o.numero, o.parcelas
                     from ia_orcamentos i join orcamentos o on o.id = i.orcamento_id
                     join conversas cv on cv.id = i.conversa_id
                     join eventos_agenda e on e.id = o.evento_agenda_id
                     left join chip_regra cr on cr.conta_id = cv.conta_id
                      and cr.chip_id = coalesce(cv.chip_id, cv.conta_id)
                     left join prospeccao p on p.id = i.prospeccao_id and p.conta_id = i.conta_id
                    where i.conta_id=%s and i.prospeccao_id=%s and i.estado='enviado'
                      and i.aprovado_msg_em is not null and i.comprovante_em is null
                      and o.aprovada_em is not null and o.sinal_pago_em is null
                      and e.status='pre_reservado'
                    order by o.aprovada_em desc limit 1""", (conta_id, lead_id)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    from finance import vendas
    if not r or not vendas.valor_do_sinal(r[4]):
        return None
    with pool.connection() as w:
        pegou = w.execute("update ia_orcamentos set comprovante_em=now() where orcamento_id=%s "
                          "and comprovante_em is null returning orcamento_id", (r[0],)).fetchone()
        w.commit()
    if not pegou:
        return None
    if r[1]:
        from finance import chip_regra as _cr
        _cr.notificar(pool, conta_id, r[1], "💰 Comprovante do sinal chegou",
                      f"{r[2]}, orçamento nº {r[3]}. Confira e toque em \"Sinal recebido\" "
                      "pra firmar a data.", f"/cockpit/orcamentos/{r[0]}")
    return ("Recebi, obrigada! 🙌 O comprovante já foi pra conferência — assim que o sinal "
            "for confirmado, eu te aviso que a data está garantida.")


# ------------------------------------------------------------------ o relógio do sinal

def _texto_aprovado(sinal: int, ate: datetime | None, pix_txt: str, bloqueio: str | None,
                    reservou: bool, dono: str = "") -> str:
    if bloqueio or not reservou:
        # a data NÃO foi segurada (dia com festa, sem hora de início, trava): não se
        # promete reserva que não existe — a equipe confirma
        return ("Recebi a sua aprovação, obrigada! 🎉 Vou confirmar a disponibilidade da data "
                "com a equipe e já te retorno.")
    if not sinal:
        return ("Aprovado, obrigada! 🎉 A sua data está reservada. Qualquer coisa, é só me "
                "chamar aqui.")
    prazo_txt = f" até {ate.astimezone(ag.BRT):%d/%m às %Hh}" if ate else ""
    quem = f"O {dono}" if dono else "A equipe"
    return (f"Aprovado, obrigada! 🎉 Pra garantir a data, o sinal é de {_reais(sinal)}"
            f" — a data fica segurada{prazo_txt}.\n"
            + (f"Pix copia e cola:\n{pix_txt}\n" if pix_txt
               else f"{quem} te passa os dados do pagamento. ")
            + "Assim que pagar, me manda o comprovante aqui 😊")


def _passo(pool, conta_id: int, i: dict, coluna: str, texto: str,
          agora: datetime | None = None, automatica: bool = True) -> bool:
    """Reivindica, manda pelo chip da conversa e grava; falha devolve e conta.

    `automatica`: a mensagem que o SISTEMA inicia (lembrete, data liberada, sinal
    confirmado) passa pelo TETO DO CHIP (`finance.teto_chip`) e conta nele. A resposta
    à aprovação que o cliente acabou de fazer é conversa, não disparo. O prazo
    estourado conta como enviado (`festa_rotinas.talvez_saiu`)."""
    from finance import agente
    from finance import festa_rotinas as _frt
    from finance import teto_chip as _tc
    agora = agora or datetime.now(timezone.utc)
    with pool.connection() as c:
        if automatica and not _tc.pode(c, conta_id, i["conversa_id"], agora):
            c.commit()
            return False
        pegou = c.execute(f"update ia_orcamentos set {coluna}=now() where orcamento_id=%s "
                          f"and {coluna} is null returning orcamento_id", (i["id"],)).fetchone()
        c.commit()
        if not pegou:
            return False
        try:
            r = c.execute("""select coalesce(p.whatsapp, p.telefone, cv.contato_ref)
                               from conversas cv left join prospeccao p
                                    on p.id = cv.prospeccao_id and p.conta_id = cv.conta_id
                              where cv.id=%s and cv.conta_id=%s""",
                          (i["conversa_id"], conta_id)).fetchone()
            res = agente._mandar(c, conta_id, "whatsapp", r[0], texto, i["conversa_id"]) \
                if r and r[0] else {"ok": False}
        except Exception as e:  # noqa: BLE001
            _log.warning("ia_orcamento: envio falhou (orçamento %s): %s", i["id"], e)
            c.rollback()
            res = {"ok": False, "erro": str(e)}
        res = res or {}
        if res.get("ok") or _frt.talvez_saiu(res):
            if automatica:
                _tc.registrar(c, conta_id, i["conversa_id"], "ia_orcamento", agora)
            try:
                if res.get("ok"):
                    agente._add_bot_msg(c, i["conversa_id"], "whatsapp", texto, res.get("sid"))
                c.execute("update ia_orcamentos set envio_falhas=0, envio_falhou_em=null "
                          "where orcamento_id=%s", (i["id"],))
                c.commit()
            except Exception:  # noqa: BLE001
                c.rollback()
            return True
        c.execute(f"""update ia_orcamentos set {coluna}=null, envio_falhas=envio_falhas+1,
                                               envio_falhou_em=now() where orcamento_id=%s""",
                  (i["id"],))
        c.commit()
        return False


def rodar(pool, agora: datetime | None = None) -> dict:
    """Um ciclo do relógio do sinal. Janelas, não minutos exatos (o poller atrasa)."""
    agora = agora or datetime.now(timezone.utc)
    out = {"aprovados": 0, "lembretes": 0, "liberadas": 0, "confirmadas": 0}
    with pool.connection() as lk:
        try:
            if not lk.execute("select pg_try_advisory_lock(%s)", (_LOCK,)).fetchone()[0]:
                return out
        except Exception:  # noqa: BLE001
            return out
        try:
            try:
                with pool.connection() as c:
                    rows = c.execute(
                        """select i.orcamento_id, i.conta_id, i.conversa_id, i.aprovado_msg_em,
                                  i.lembrete_24_em, i.lembrete_48_em, i.liberada_msg_em,
                                  i.confirmada_msg_em, i.bloqueio, o.aprovada_em, o.sinal_pago_em,
                                  o.parcelas, e.status, e.pre_reserva_ate, o.evento_agenda_id,
                                  cr.aviso_agenda_membro_id,
                                  coalesce(nullif(p.contato,''), nullif(p.empresa,''), 'O cliente'),
                                  coalesce(cr.orc_reserva_h, 72), coalesce(md.nome, '')
                             from ia_orcamentos i
                             left join membros md on md.id = (
                               select cr2.aviso_dono_membro_id from chip_regra cr2
                                join conversas cv2 on cv2.id = i.conversa_id
                               where cr2.conta_id = cv2.conta_id
                                 and cr2.chip_id = coalesce(cv2.chip_id, cv2.conta_id))
                             join orcamentos o on o.id = i.orcamento_id and o.conta_id = i.conta_id
                             join conversas cv on cv.id = i.conversa_id and cv.conta_id = i.conta_id
                             left join chip_regra cr on cr.conta_id = cv.conta_id
                              and cr.chip_id = coalesce(cv.chip_id, cv.conta_id)
                             left join eventos_agenda e on e.id = o.evento_agenda_id
                             left join prospeccao p on p.id = i.prospeccao_id and p.conta_id = i.conta_id
                            where i.estado='enviado' and o.aprovada_em is not null
                              and o.aprovada_em > %s
                              and cv.agente_ativo and cv.status <> 'pendente'
                              and i.envio_falhas < %s
                              and (i.envio_falhou_em is null or i.envio_falhou_em < %s)""",
                        (agora - timedelta(days=15), MAX_FALHAS, agora - INTERVALO_FALHA)).fetchall()
            except Exception:  # noqa: BLE001
                return out
            from finance import vendas
            # A MENSAGEM QUE O RELÓGIO INICIA SÓ SAI DAS 8H ÀS 20H: o lembrete das 24h de
            # uma aprovação às 23h caía às 23h do dia seguinte, e a data liberada saía
            # na hora em que a reserva vencia — de madrugada (revisão de 27/09/2026). A
            # resposta à aprovação sai na hora: foi o cliente que acabou de agir.
            loc_h = agora.astimezone(ag.BRT).hour
            janela = HORAS_CLIENTE[0] <= loc_h < HORAS_CLIENTE[1]
            for r in rows:
                i = dict(zip(("id", "conta_id", "conversa_id", "aprovado_msg_em", "l24", "l48",
                              "liberada_msg_em", "confirmada_msg_em", "bloqueio", "aprovada_em",
                              "sinal_pago_em", "parcelas", "ev_status", "pre_ate", "ev_id",
                              "agenda_id", "quem", "reserva_h", "dono"), r))
                conta = i["conta_id"]
                try:
                    sinal = vendas.valor_do_sinal(i["parcelas"])
                    if i["sinal_pago_em"]:
                        if not i["confirmada_msg_em"] and i["aprovado_msg_em"] and janela:
                            if _passo(pool, conta, i, "confirmada_msg_em",
                                      "Sinal confirmado! 🎉 A sua data está garantida. "
                                      "Qualquer coisa, é só me chamar aqui.", agora):
                                out["confirmadas"] += 1
                        continue
                    if not i["aprovado_msg_em"]:
                        # a reserva nasce em segundo plano logo depois da aprovação:
                        # espera ela (ou o bloqueio) antes de dizer até quando vale
                        if not i["ev_id"] and not i["bloqueio"] \
                                and agora - i["aprovada_em"] < timedelta(minutes=10):
                            continue
                        from finance.voltar_a_chamar import primeiro_nome
                        dono = primeiro_nome(i["dono"]) if i["dono"] else ""
                        dono = dono[:1].upper() + dono[1:].lower() if dono else ""
                        pix_txt = _pix(pool, conta, sinal, i["id"]) if sinal else ""
                        # a resposta à aprovação: na hora, se o cliente acabou de aprovar;
                        # atrasada (o poller parado, a reserva demorando), espera a janela
                        if not janela and agora - i["aprovada_em"] > timedelta(minutes=30):
                            continue
                        if _passo(pool, conta, i, "aprovado_msg_em",
                                  _texto_aprovado(sinal, i["pre_ate"], pix_txt, i["bloqueio"],
                                                  bool(i["ev_id"]), dono),
                                  agora, automatica=False):
                            out["aprovados"] += 1
                        continue
                    # LIBERADA = a reserva VENCEU. O dono cancelar antes ("apareceu quem
                    # paga hoje") ou cancelar à mão não é "o seu prazo acabou"
                    if (i["ev_status"] == "cancelado" and not i["liberada_msg_em"]
                            and i["pre_ate"] and i["pre_ate"] <= agora):
                        if janela and _passo(pool, conta, i, "liberada_msg_em",
                                  "Oi! O prazo pra garantir a sua data acabou e ela foi liberada 😕 "
                                  "Se ainda quiser, me chama que eu vejo se continua livre.",
                                  agora):
                            out["liberadas"] += 1
                            if i["agenda_id"]:
                                from finance import chip_regra as _cr
                                _cr.notificar(pool, conta, i["agenda_id"], "📅 Data liberada sem sinal",
                                              f"{i['quem']}: a reserva de 72h venceu sem sinal.",
                                              f"/cockpit/orcamentos/{i['id']}")
                        continue
                    if i["ev_status"] != "pre_reservado" or i["bloqueio"] or not sinal \
                            or not janela:
                        continue
                    desde = agora - i["aprovada_em"]
                    ate = i["pre_ate"]
                    # os lembretes acompanham a reserva: com 72h, aos 24h e aos 48h
                    terco = timedelta(hours=int(i["reserva_h"]) / 3)
                    if not i["l24"] and desde >= terco:
                        if _passo(pool, conta, i, "lembrete_24_em",
                                  f"Oi! Passando pra lembrar do sinal de {_reais(sinal)} 😊 A sua "
                                  f"data está segurada até {ate.astimezone(ag.BRT):%d/%m às %Hh}."
                                  if ate else f"Oi! Passando pra lembrar do sinal de {_reais(sinal)} 😊",
                                  agora):
                            out["lembretes"] += 1
                    elif i["l24"] and not i["l48"] and desde >= 2 * terco:
                        if _passo(pool, conta, i, "lembrete_48_em",
                                  "Oi! Último lembrete: a reserva da sua data vence "
                                  + (f"{ate.astimezone(ag.BRT):%d/%m às %Hh}" if ate else "em breve")
                                  + ". Se precisar de ajuda com o pagamento, me chama 🙏",
                                  agora):
                            out["lembretes"] += 1
                except Exception as e:  # noqa: BLE001
                    _log.warning("ia_orcamento.rodar: orçamento %s: %s", i["id"], e)
        finally:
            lk.execute("select pg_advisory_unlock(%s)", (_LOCK,))
    return out


def _pix(pool, conta_id: int, centavos: int, orc_id: int) -> str:
    """O Pix copia-e-cola do sinal, com a chave da PRÓPRIA empresa (finance/pix.py), ou
    vazio quando a empresa não cadastrou chave — aí o dono manda os dados."""
    try:
        from finance import pix
        d = pix.da_conta(pool, conta_id) or {}
        if not d.get("chave"):
            return ""
        return pix.copia_e_cola(d["chave"], int(centavos), d.get("recebedor") or "",
                                d.get("cidade") or "", txid=f"ORC{orc_id}")
    except Exception:  # noqa: BLE001
        _log.warning("ia_orcamento: Pix do orçamento %s não saiu", orc_id, exc_info=True)
        return ""
