"""Plano de tratamento da clínica (fase 5): a proposta do que o médico indicou, o
aceite e a cobrança da decisão.

Desenho aprovado: docs/mockups/clinica_planos_pacotes_assinatura.html, seções 03
("o plano de tratamento") e 11 (passos 1 e 2). Tabela: 379. Telas:
web/painel_clinica_planos.py (/painel/clinica/planos e a página pública /plano/{token}).

COMO ANDA
  1. Depois da consulta finalizada ("o médico propôs tratamento? sim"), a recepção
     monta o plano: procedimentos do catálogo, sessões de cada um, valor, desconto e
     as formas de pagamento (Pix à vista, com desconto opcional; N× no cartão; entrada
     + parcelas mensais). Vale 7 dias.
  2. DESCONTO TEM DONO (decisão E): até o teto a recepção dá sozinha; acima dele o
     plano espera o dono ou o gestor aprovar antes de sair.
  3. Sai pelo WhatsApp, com o link. O paciente aceita no link (nome e forma) ou
     respondendo 1 (Pix), 2 (cartão) ou 3 (parcelado).
  4. Aceito: o card vai pra Fechado com o valor, cada parcela vira um título a
     receber, e a recepção é avisada.
  5. A DECISÃO É COBRADA SOZINHA (passo 2): D+1 "conseguiu ver?", D+3 o lembrete
     com as formas; na véspera de vencer a recepção é avisada; vencido, sai da rua.
     Se o paciente responde qualquer coisa, o automático para e fica com a pessoa.

A PROPOSTA NÃO É PRONTUÁRIO: o nome do procedimento e o valor, nunca diagnóstico,
queixa ou promessa de resultado. Os lembretes automáticos nem o procedimento dizem.
"""
from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import date, datetime, time, timedelta, timezone

from finance import clinica_agenda as ca
from finance import clinica_config as cc

_log = logging.getLogger("clinica.planos")

_LOCK = 771165                 # vizinho das travas da agenda, do agente e das vagas
MAX_ITENS = 6
FORMAS = (("pix", "Pix à vista"), ("cartao", "Cartão de crédito"), ("parcelado", "Entrada + parcelas"))
FORMA_D = dict(FORMAS)
STATUS_D = {"rascunho": "rascunho", "aguardando_aprovacao": "esperando o dono aprovar o desconto",
            "enviado": "enviado", "aceito": "aceito", "recusado": "recusado", "vencido": "vencido",
            "cancelado": "cancelado"}
_RESPOSTA = {"1": "pix", "2": "cartao", "3": "parcelado"}
_RE_RESPOSTA = re.compile(r"^\s*([123])\s*[.!)✅👍]*\s*$")


# ------------------------------------------------------------------ config

def config(c, conta_id: int) -> dict:
    padrao = {"teto_desconto": 10.0, "pix_desconto": 0.0, "cartao_parcelas": 4, "validade_dias": 7,
              "cobranca": "ligado"}
    try:
        with c.transaction():
            r = c.execute("""select planos_teto_desconto_pct, planos_pix_desconto_pct, planos_cartao_parcelas,
                                    planos_validade_dias, planos_cobranca
                               from clinica_agenda_config where conta_id=%s""", (conta_id,)).fetchone()
    except Exception:  # noqa: BLE001 — migração 379 ainda não rodou
        return padrao
    if not r:
        return padrao
    return {"teto_desconto": float(r[0]), "pix_desconto": float(r[1]), "cartao_parcelas": int(r[2]),
            "validade_dias": int(r[3]), "cobranca": r[4]}


def _pct(txt, padrao: float = 0.0) -> float | None:
    t = str(txt if txt is not None else "").strip().replace("%", "").replace(",", ".")
    if not t:
        return padrao
    try:
        v = float(t)
    except ValueError:
        return None
    return v if 0 <= v <= 100 else None


def salvar_config(c, conta_id: int, *, teto, pix, parcelas, validade, cobranca: str) -> str | None:
    teto_v, pix_v = _pct(teto, 10), _pct(pix, 0)
    if teto_v is None or pix_v is None or pix_v > 50:
        return "Desconto inválido: use um número de 0 a 100 (Pix até 50)."
    try:
        parc = int(str(parcelas).strip() or 4)
        val = int(str(validade).strip() or 7)
    except ValueError:
        return "Parcelas e validade são números inteiros."
    if not 1 <= parc <= 24 or not 1 <= val <= 60:
        return "Parcelas de 1 a 24 e validade de 1 a 60 dias."
    if cobranca not in ("off", "ligado"):
        return "Modo inválido."
    c.execute("""insert into clinica_agenda_config (conta_id, planos_teto_desconto_pct, planos_pix_desconto_pct,
                                                     planos_cartao_parcelas, planos_validade_dias, planos_cobranca)
                 values (%s,%s,%s,%s,%s,%s) on conflict (conta_id) do update
                 set planos_teto_desconto_pct=excluded.planos_teto_desconto_pct,
                     planos_pix_desconto_pct=excluded.planos_pix_desconto_pct,
                     planos_cartao_parcelas=excluded.planos_cartao_parcelas,
                     planos_validade_dias=excluded.planos_validade_dias,
                     planos_cobranca=excluded.planos_cobranca, atualizado_em=now()""",
              (conta_id, teto_v, pix_v, parc, val, cobranca))
    return None


# ------------------------------------------------------------------ as contas

def calcular(itens: list[dict], desconto_pct: float, pix_pct: float, parcelas: int) -> dict:
    """Os números do plano, do jeito que a página e a mensagem mostram. Arredonda a
    parcela pra baixo e põe o resto na 1ª, pra soma bater no centavo."""
    subtotal = sum(int(i["valor_unit_centavos"]) * int(i["sessoes"]) for i in itens)
    desconto = round(subtotal * float(desconto_pct) / 100)
    total = subtotal - desconto
    pix = total - round(total * float(pix_pct) / 100)
    n = max(1, int(parcelas))
    base = total // n
    # a referência é o preço de TABELA: item do catálogo vale o preço do catálogo
    # (baixar o valor por sessão é desconto); item avulso vale o que foi digitado
    ref = sum(max(int(i.get("preco_catalogo_centavos") or 0), 0) * int(i["sessoes"])
              if int(i.get("preco_catalogo_centavos") or 0) > 0
              else int(i["valor_unit_centavos"]) * int(i["sessoes"]) for i in itens)
    efetivo = round(100 * (1 - pix / ref), 2) if ref > 0 else 0.0
    return {"subtotal": subtotal, "desconto": desconto, "total": total, "pix": pix,
            "parcelas": n, "parcela": base, "primeira": total - base * (n - 1),
            "referencia": ref, "desconto_efetivo": max(0.0, efetivo)}


def limpar_itens(brutos: list[dict], tipos: dict[int, dict]) -> tuple[list[dict], str | None]:
    """[{servico_id, nome, sessoes, valor}] do formulário → itens do plano. O nome do
    catálogo manda quando o item é do catálogo (ninguém reescreve "Criolipólise")."""
    itens = []
    for b in brutos[:MAX_ITENS]:
        sid = b.get("servico_id")
        nome = (b.get("nome") or "").strip()
        if sid:
            t = tipos.get(int(sid))
            if not t:
                return [], "Atendimento não encontrado no catálogo."
            nome = t["nome"]
        if not nome:
            continue
        try:
            sessoes = int(str(b.get("sessoes") or "1").strip())
        except ValueError:
            return [], f"Sessões de {nome}: use um número inteiro."
        if not 1 <= sessoes <= 60:
            return [], f"Sessões de {nome}: de 1 a 60."
        valor = cc.centavos(b.get("valor")) if str(b.get("valor") or "").strip() else \
            (tipos.get(int(sid))["preco_centavos"] if sid else None)
        if valor is None or valor <= 0:
            return [], f"Informe o valor de {nome} (por sessão)."
        itens.append({"servico_id": int(sid) if sid else None, "nome": nome[:80], "sessoes": sessoes,
                      "valor_unit_centavos": int(valor),
                      "preco_catalogo_centavos": int(tipos[int(sid)]["preco_centavos"] or 0) if sid else 0})
    if not itens:
        return [], "Coloque pelo menos um procedimento."
    return itens, None


# ------------------------------------------------------------------ leitura

_COLS = """p.id, p.prospeccao_id, p.evento_id, p.profissional_id, p.paciente_nome, p.paciente_fone, p.itens,
           p.subtotal_centavos, p.desconto_pct, p.total_centavos, p.pix_desconto_pct, p.cartao_parcelas,
           p.parcelado, p.validade_ate, p.status, p.token, p.enviado_em, p.visto_em, p.aceito_em,
           p.aceito_forma, p.aceito_nome, p.aceito_por, p.titulos, p.criado_em, p.desconto_aprovado_em,
           p.conta_id, p.toque1_em, p.toque3_em, p.conversa_id, p.ultima_msg_id, p.mensagem_id,
           to_char(p.atualizado_em, 'YYYY-MM-DD"T"HH24:MI:SS.US')"""


def _dict(c, r) -> dict:
    itens = r[6] if isinstance(r[6], list) else json.loads(r[6] or "[]")
    conta_id = r[25]
    contas = calcular(itens, float(r[8]), float(r[10]), int(r[11]))
    prof = next((p for p in cc.listar_profissionais(c, conta_id, so_ativos=False) if p["id"] == r[3]), None)
    return {"id": r[0], "lead": r[1], "evento_id": r[2], "profissional_id": r[3], "prof": prof["nome"] if prof else "",
            "paciente": r[4], "fone": r[5], "itens": itens, "desconto_pct": float(r[8]),
            "pix_desconto_pct": float(r[10]), "cartao_parcelas": int(r[11]), "parcelado": r[12],
            "validade_ate": r[13], "status": r[14], "status_d": STATUS_D.get(r[14], r[14]), "token": r[15],
            "enviado_em": r[16], "visto_em": r[17], "aceito_em": r[18], "aceito_forma": r[19],
            "forma_d": FORMA_D.get(r[19] or "", ""), "aceito_nome": r[20], "aceito_por": r[21],
            "titulos": r[22] if isinstance(r[22], list) else json.loads(r[22] or "[]"), "criado_em": r[23],
            "desconto_aprovado_em": r[24], "conta_id": conta_id, "toque1_em": r[26], "toque3_em": r[27],
            "conversa_id": r[28], "ultima_msg_id": r[29], "mensagem_id": r[30], "versao": r[31],
            **contas}


def plano(c, conta_id: int, plano_id: int) -> dict | None:
    r = c.execute(f"select {_COLS} from clinica_planos p where p.id=%s and p.conta_id=%s",
                  (plano_id, conta_id)).fetchone()
    return _dict(c, r) if r else None


def por_token(c, token: str) -> dict | None:
    from finance.clinica_planos_publico import dono_do_token
    dono = dono_do_token(c, token)
    if not dono:
        return None
    p = plano(c, dono[1], dono[0])
    return p if p and p["status"] != "cancelado" else None


def listar(c, conta_id: int) -> list[dict]:
    try:
        with c.transaction():
            rows = c.execute(
                f"""select {_COLS} from clinica_planos p where p.conta_id=%s
                     order by p.status not in ('rascunho','aguardando_aprovacao','enviado'),
                              coalesce(p.enviado_em, p.criado_em) desc limit 80""", (conta_id,)).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [_dict(c, r) for r in rows]


def do_evento(c, conta_id: int, evento_id: int) -> dict | None:
    """O plano que já saiu desta consulta (pra tela do agendamento não criar dois)."""
    try:
        with c.transaction():
            r = c.execute("""select id from clinica_planos where conta_id=%s and evento_id=%s
                              and status <> 'cancelado' order by id desc limit 1""",
                          (conta_id, evento_id)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    return plano(c, conta_id, r[0]) if r else None


# ------------------------------------------------------------------ montar

def salvar(c, conta_id: int, *, plano_id: int | None = None, lead: int | None, evento_id: int | None,
           profissional_id: int | None, paciente: str, fone: str, itens: list[dict], desconto_pct,
           pix_desconto_pct, cartao_parcelas, parcelado: bool, membro_id: int | None,
           pode_aprovar: bool) -> tuple[int | None, str | None]:
    """Grava o rascunho. Desconto acima do teto da recepção deixa o plano ESPERANDO o
    dono (ou o gestor) aprovar — ele não sai antes disso."""
    cfg = config(c, conta_id)
    desc = _pct(desconto_pct, 0)
    pix = _pct(pix_desconto_pct, cfg["pix_desconto"])
    if desc is None or pix is None or pix > 50:
        return None, "Desconto inválido: use um número de 0 a 100 (Pix até 50)."
    try:
        parc = int(str(cartao_parcelas).strip() or cfg["cartao_parcelas"])
    except ValueError:
        return None, "Parcelas: use um número inteiro."
    if not 1 <= parc <= 24:
        return None, "Parcelas de 1 a 24."
    paciente = " ".join((paciente or "").split())
    if not paciente:
        return None, "Informe o nome do paciente."
    contas = calcular(itens, desc, pix, parc)
    # O TETO É DO DESCONTO EFETIVO: o que o paciente paga no Pix contra o preço de
    # tabela. Sem isso, "desconto 10% + Pix 50%" ou "Criolipólise a R$ 100 a sessão"
    # passavam sem ninguém aprovar.
    acima = contas["desconto_efetivo"] > cfg["teto_desconto"] + 0.005
    status = "aguardando_aprovacao" if (acima and not pode_aprovar) else "rascunho"
    aprov = (membro_id, datetime.now(timezone.utc)) if (acima and pode_aprovar) else (None, None)
    if plano_id:
        r = c.execute(
            """update clinica_planos set itens=%s, subtotal_centavos=%s, desconto_pct=%s, total_centavos=%s,
                      pix_desconto_pct=%s, cartao_parcelas=%s, parcelado=%s, paciente_nome=%s, paciente_fone=%s,
                      profissional_id=%s, status=%s, desconto_aprovado_por=%s, desconto_aprovado_em=%s,
                      atualizado_em=now()
                where id=%s and conta_id=%s and status in ('rascunho','aguardando_aprovacao') returning id""",
            (json.dumps(itens), contas["subtotal"], desc, contas["total"], pix, parc, bool(parcelado),
             paciente[:120], (fone or "")[:40], profissional_id, status, aprov[0], aprov[1],
             plano_id, conta_id)).fetchone()
        return (r[0], None) if r else (None, "Esse plano já foi enviado e não muda mais. Cancele e monte outro.")
    r = c.execute(
        """insert into clinica_planos (conta_id, prospeccao_id, evento_id, profissional_id, paciente_nome,
                                       paciente_fone, itens, subtotal_centavos, desconto_pct, total_centavos,
                                       pix_desconto_pct, cartao_parcelas, parcelado, status, criado_por,
                                       desconto_aprovado_por, desconto_aprovado_em)
           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
        (conta_id, lead, evento_id, profissional_id, paciente[:120], (fone or "")[:40], json.dumps(itens),
         contas["subtotal"], desc, contas["total"], pix, parc, bool(parcelado), status, membro_id,
         aprov[0], aprov[1])).fetchone()
    return r[0], None


def aprovar_desconto(c, conta_id: int, plano_id: int, membro_id: int | None, visto: str = "") -> bool:
    """Aprova O QUE O DONO VIU: `visto` é o `atualizado_em` da tela. Se a recepção
    mudou o plano no meio (15% virou 40%), não aprova — ele olha de novo."""
    r = c.execute("""update clinica_planos set status='rascunho', desconto_aprovado_por=%s,
                            desconto_aprovado_em=now(), atualizado_em=now()
                      where id=%s and conta_id=%s and status='aguardando_aprovacao'
                        and to_char(atualizado_em, 'YYYY-MM-DD"T"HH24:MI:SS.US') = %s returning id""",
                  (membro_id, plano_id, conta_id, visto)).fetchone()
    return r is not None


def cancelar(c, conta_id: int, plano_id: int) -> bool:
    r = c.execute("""update clinica_planos set status='cancelado', atualizado_em=now()
                      where id=%s and conta_id=%s and status in ('rascunho','aguardando_aprovacao','enviado')
                      returning id""", (plano_id, conta_id)).fetchone()
    return r is not None


# ------------------------------------------------------------------ mandar

def _app_url() -> str:
    try:
        from finance.email_sender import _app_url as u
        return u()
    except Exception:  # noqa: BLE001
        return ""


def link(p: dict) -> str:
    return f"{_app_url()}/plano/{p['token']}"


def _brl(cent: int) -> str:
    return cc.reais(cent) if cent else "R$ 0"


def formas_txt(p: dict) -> list[str]:
    out = [f"À vista no Pix: {_brl(p['pix'])}" + (f" (−{p['pix_desconto_pct']:g}%)" if p["pix_desconto_pct"] else "")]
    if p["parcelas"] > 1:
        out.append(f"Ou {p['parcelas']}× de {_brl(p['parcela'])} no cartão")
        if p["parcelado"]:
            out.append(f"Ou entrada de {_brl(p['primeira'])} + {p['parcelas'] - 1}× de {_brl(p['parcela'])} "
                       "no boleto/Pix")
    else:
        out.append(f"Ou {_brl(p['total'])} no cartão")
    return out


def texto_envio(c, p: dict) -> str:
    """A proposta no WhatsApp: o que o médico montou, o preço e como pagar. O nome do
    procedimento vai (é a proposta que o paciente pediu); diagnóstico e promessa, nunca."""
    from finance.voltar_a_chamar import primeiro_nome
    n = primeiro_nome(p["paciente"])
    quem = f"{p['prof']} montou" if p["prof"] else "Montamos"
    linhas = [f"Oi{', ' + n if n else ''}! {quem} o seu plano de tratamento 💚", ""]
    for i in p["itens"]:
        s = "sessão" if i["sessoes"] == 1 else "sessões"
        linhas.append(f"• {i['nome']} · {i['sessoes']} {s} · {_brl(i['valor_unit_centavos'] * i['sessoes'])}")
    if p["desconto"]:
        linhas.append(f"Desconto: −{_brl(p['desconto'])}")
    linhas += [f"Total: {_brl(p['total'])}", ""] + formas_txt(p)
    validade = f"{p['validade_ate']:%d/%m}" if p["validade_ate"] else ""
    opcoes = "1 para o Pix, 2 para o cartão" + (" ou 3 para entrada + parcelas" if p["parcelado"] and p["parcelas"] > 1 else "")
    linhas += ["", f"Os detalhes estão aqui: {link(p)}",
               f"Para fechar, responda {opcoes}." + (f" O plano vale até {validade}." if validade else "")]
    return "\n".join(linhas)


def _conversa_e_destino(c, conta_id: int, p: dict) -> tuple[int | None, str]:
    conv = p.get("conversa_id") or ca._conversa(c, conta_id, {"lead": p["lead"], "fone": p["fone"]})
    dig = ca._digitos(p["fone"])
    if len(dig) < 10 and conv:
        r = c.execute("select coalesce(contato_ref,'') from conversas where id=%s and conta_id=%s",
                      (conv, conta_id)).fetchone()
        dig = ca._digitos(r[0] if r else "")
    destino = ("+" + (dig if dig.startswith("55") else "55" + dig)) if len(dig) >= 10 else ""
    return conv, destino


def _mandar(c, conta_id: int, conv: int | None, destino: str, texto: str) -> dict:
    from finance import agente
    try:
        res = agente._mandar(c, conta_id, "whatsapp", destino, texto, conv) or {}
    except Exception as e:  # noqa: BLE001
        _log.warning("planos: envio falhou: %s", e)
        res = {"ok": False, "erro": type(e).__name__}
    if res.get("ok") and conv:
        res["mensagem_id"] = c.execute(
            """insert into mensagens (conversa_id, canal, direcao, autor, texto, provider_sid)
               values (%s,'whatsapp','out','bot',%s,%s) returning id""", (conv, texto, res.get("sid"))).fetchone()[0]
        c.execute("update conversas set ultima_msg_em=now() where id=%s and conta_id=%s", (conv, conta_id))
    return res


_ANTES = ("novo", "contatado", "follow_up", "qualificado")


def _mover(c, conta_id: int, lead: int | None, destino: str, valor: int | None, membro_id: int | None) -> None:
    if not lead:
        return
    from finance import funil_regua as fr
    r = c.execute("select status from prospeccao where id=%s and conta_id=%s for update", (lead, conta_id)).fetchone()
    if not r:
        return
    pode = _ANTES if destino == "proposta" else _ANTES + ("proposta",)
    if r[0] == destino and valor:
        # já está na etapa (o Finalizar pôs em Plano de tratamento): o valor é o do plano
        c.execute("update prospeccao set valor_estimado_centavos=%s, atualizado_em=now() where id=%s and conta_id=%s",
                  (valor, lead, conta_id))
        return
    if r[0] not in pode:
        return
    c.execute("""update prospeccao set status=%s, estagio='lead', atualizado_em=now(),
                        valor_estimado_centavos = coalesce(%s, valor_estimado_centavos)
                  where id=%s and conta_id=%s""", (destino, valor, lead, conta_id))
    fr.registrar_movimento(c, conta_id, lead, r[0], destino, "plano", membro_id)


def enviar(c, conta_id: int, plano_id: int, membro_id: int | None, agora: datetime | None = None) -> dict:
    """Manda o plano. Só rascunho (desconto acima do teto precisa estar aprovado)."""
    agora = agora or datetime.now(timezone.utc)
    p = plano(c, conta_id, plano_id)
    if not p:
        return {"ok": False, "erro": "Plano não encontrado."}
    if p["status"] == "aguardando_aprovacao":
        return {"ok": False, "erro": "O desconto passou do teto: o dono ou o gestor precisa aprovar antes."}
    if p["status"] != "rascunho":
        return {"ok": False, "erro": "Esse plano já foi enviado."}
    conv, destino = _conversa_e_destino(c, conta_id, p)
    if not destino:
        return {"ok": False, "erro": "O paciente não tem celular com DDD."}
    cfg = config(c, conta_id)
    token = p["token"] or secrets.token_urlsafe(16)
    validade = ca.hoje_br(agora) + timedelta(days=cfg["validade_dias"])
    r = c.execute("""update clinica_planos set status='enviado', token=%s, validade_ate=%s, enviado_em=%s,
                            conversa_id=%s, atualizado_em=now()
                      where id=%s and conta_id=%s and status='rascunho' returning id""",
                  (token, validade, agora, conv, plano_id, conta_id)).fetchone()
    if not r:
        c.rollback()
        return {"ok": False, "erro": "Esse plano já foi enviado."}
    p = plano(c, conta_id, plano_id)
    _mover(c, conta_id, p["lead"], "proposta", p["total"], membro_id)
    c.commit()                          # enviado ANTES do WhatsApp: nunca sai duas vezes
    res = _mandar(c, conta_id, conv, destino, texto_envio(c, p))
    if res.get("ok"):
        c.execute("update clinica_planos set mensagem_id=%s, ultima_msg_id=%s where id=%s and conta_id=%s",
                  (res.get("mensagem_id"), res.get("mensagem_id"), plano_id, conta_id))
    else:
        # NÃO SAIU: o plano volta pra rascunho. Um plano 'enviado' que o paciente nunca
        # recebeu seria aceito pelo "1" de outra coisa e ganharia D+1 "conseguiu ver?"
        c.execute("""update clinica_planos set status='rascunho', enviado_em=null, validade_ate=null,
                            atualizado_em=now() where id=%s and conta_id=%s and status='enviado'""",
                  (plano_id, conta_id))
    c.commit()
    return {"ok": bool(res.get("ok")), "link": link(p),
            "erro": None if res.get("ok") else "A mensagem não saiu (WhatsApp). O plano continua pronto: tente enviar de novo."}


# ------------------------------------------------------------------ o aceite

def marcar_visto(c, token: str) -> None:
    from finance.clinica_planos_publico import dono_do_token
    dono = dono_do_token(c, token)
    if dono:
        c.execute("""update clinica_planos set visto_em=now()
                      where id=%s and conta_id=%s and visto_em is null and status='enviado'""", dono)


def aceitar(pool, token: str, *, nome: str, forma: str, ip: str = "", por: str = "link",
            agora: datetime | None = None) -> bool:
    """Aceita o plano ENVIADO e dentro da validade. Idempotente (o update com o status
    é a trava). Aceito: títulos a receber, card em Fechado e aviso à recepção."""
    agora = agora or datetime.now(timezone.utc)
    nome = " ".join((nome or "").split())
    if not nome or forma not in FORMA_D:
        return False
    from finance.clinica_planos_publico import dono_do_token
    with pool.connection() as c:
        dono = dono_do_token(c, token)
        r = dono and c.execute(
            """update clinica_planos set status='aceito', aceito_em=%s, aceito_forma=%s, aceito_nome=%s,
                      aceito_ip=%s, aceito_por=%s, atualizado_em=now()
                where id=%s and conta_id=%s and status='enviado' and (validade_ate is null or validade_ate >= %s)
                  and (%s <> 'parcelado' or parcelado)
            returning id, conta_id""",
            (agora, forma, nome[:120], (ip or "")[:60], por, dono[0], dono[1], ca.hoje_br(agora),
             forma)).fetchone()
        if not r:
            c.rollback()
            return False
        plano_id, conta_id = r
        p = plano(c, conta_id, plano_id)
        _mover(c, conta_id, p["lead"], "ganho", p["pix"] if forma == "pix" else p["total"], None)
        c.commit()
    ids = _titulos(pool, p, forma, agora)
    with pool.connection() as c:
        c.execute("update clinica_planos set titulos=%s where id=%s and conta_id=%s",
                  (json.dumps(ids), plano_id, conta_id))
        c.commit()
        _avisar(c, conta_id, p, f"✅ Plano aceito: {p['paciente']}",
                f"{FORMA_D[forma]} · {_brl(p['pix'] if forma == 'pix' else p['total'])}")
    return True


def _mensal(d: date, n: int) -> date:
    m = d.month - 1 + n
    ano, mes = d.year + m // 12, m % 12 + 1
    for dia in (d.day, 30, 29, 28):
        try:
            return date(ano, mes, dia)
        except ValueError:
            continue
    return date(ano, mes, 28)


def parcelas_do_aceite(p: dict, forma: str, hoje: date) -> list[tuple[str, int, date]]:
    """(rótulo, valor, vencimento) de cada título. Pix: um, hoje. Cartão e parcelado:
    N, de mês em mês a partir de hoje (a 1ª leva o resto da divisão)."""
    if forma == "pix" or p["parcelas"] <= 1:
        return [("à vista no Pix" if forma == "pix" else "à vista", p["pix"] if forma == "pix" else p["total"], hoje)]
    rot = "cartão" if forma == "cartao" else "boleto/Pix"
    out = []
    for i in range(p["parcelas"]):
        valor = p["primeira"] if i == 0 else p["parcela"]
        nome = ("entrada" if (forma == "parcelado" and i == 0) else f"parcela {i + 1}/{p['parcelas']}")
        out.append((f"{nome} ({rot})", valor, _mensal(hoje, i)))
    return out


def _titulos(pool, p: dict, forma: str, agora: datetime) -> list[int]:
    from finance import empresa as _emp
    # a ficha do cliente pelo TELEFONE (nome parecido não é a mesma pessoa: "Maria
    # Silva" não é "Maria Silva Santos"); sem telefone único, cria uma ficha nova
    cliente_id = None
    try:
        from finance import clientes as _cli
        achado = _cli.buscar_unico_por_telefone(pool, p["conta_id"], p["fone"]) if p["fone"] else None
        cliente_id = achado["id"] if achado else _cli.criar_cliente(pool, p["conta_id"], p["paciente"],
                                                                     telefone=p["fone"] or None)
    except Exception:  # noqa: BLE001 — sem a base de clientes, o título vai sem a ficha
        cliente_id = None
    ids = []
    for rotulo, valor, venc in parcelas_do_aceite(p, forma, ca.hoje_br(agora)):
        if valor <= 0:
            continue
        try:
            t = _emp.criar_titulo(pool, p["conta_id"], "receber", f"Plano de tratamento · {p['paciente']} — {rotulo}",
                                  int(valor), venc, contraparte=p["paciente"], categoria="Vendas",
                                  cliente_id=cliente_id)
            ids.append(t["id"])
        except Exception:  # noqa: BLE001 — o aceite vale; o título a recepção lança se faltar
            _log.warning("planos: título não criado (plano %s)", p["id"], exc_info=True)
    return ids


def gerar_titulos(pool, conta_id: int, plano_id: int) -> int:
    """O plano foi aceito e os títulos não saíram (o banco caiu no meio): gera agora.
    Só com a lista vazia — nunca duplica."""
    with pool.connection() as c:
        p = plano(c, conta_id, plano_id)
    if not p or p["status"] != "aceito" or p["titulos"] or not p["aceito_forma"]:
        return 0
    ids = _titulos(pool, p, p["aceito_forma"], p["aceito_em"] or datetime.now(timezone.utc))
    with pool.connection() as c:
        c.execute("update clinica_planos set titulos=%s where id=%s and conta_id=%s and titulos = '[]'::jsonb",
                  (json.dumps(ids), plano_id, conta_id))
        c.commit()
    return len(ids)


def recusar(c, token: str) -> bool:
    from finance.clinica_planos_publico import dono_do_token
    dono = dono_do_token(c, token)
    r = dono and c.execute("""update clinica_planos set status='recusado', atualizado_em=now()
                               where id=%s and conta_id=%s and status='enviado' returning id, conta_id""",
                           dono).fetchone()
    if r:
        p = plano(c, r[1], r[0])
        _avisar(c, r[1], p, f"Plano recusado: {p['paciente']}", "Recusou pelo link. Vale uma ligação.")
    return r is not None


def _avisar(c, conta_id: int, p: dict, titulo: str, corpo: str) -> None:
    try:
        from finance import clinica_agente as cla
        try:
            from db.conexao import get_pool
            pool = get_pool()
        except Exception:  # noqa: BLE001
            pool = None
        cla._aviso(pool, conta_id, cla._recepcao(c, conta_id, p.get("lead")), titulo, corpo,
                   f"/painel/clinica/planos/{p['id']}")
    except Exception:  # noqa: BLE001
        pass


# ------------------------------------------------------------------ respostas no WhatsApp

def processar(pool, c, conta_id: int, agora: datetime, conversa_id: int | None = None, responder=None) -> int:
    """"1", "2" ou "3" depois do plano = aceite com essa forma (Pix, cartão, parcelado).

    Só vale o número puro que vem LOGO DEPOIS da última mensagem do plano (o envio ou
    um lembrete dele), na conversa por onde o plano saiu: se a recepção perguntou
    "no cartão, em quantas vezes?" e o paciente disse "3", ou se o lembrete da
    consulta ("responda 1") veio no meio, o número não é do plano. Plano que não
    saiu (sem mensagem) não é aceito por número nenhum. O "3" só quando a opção foi
    oferecida. O poller pega a trava da conversa (a mesma do agente) e pula a que o
    agente está atendendo: um número, uma resposta."""
    from finance import clinica_agente as cla
    feitos = 0
    extra = " and p.conversa_id=%s" if conversa_id else ""
    try:
        with c.transaction():
            rows = c.execute(
                """select p.id, p.token, p.conversa_id, m.texto, p.parcelado, p.paciente_nome, p.cartao_parcelas
                     from clinica_planos p
                     join mensagens m on m.conversa_id = p.conversa_id and m.direcao = 'in'
                          and m.id > p.ultima_msg_id
                    where p.conta_id=%s and p.status='enviado' and p.validade_ate >= %s
                      and p.mensagem_id is not null and p.ultima_msg_id is not null""" + extra + """
                      and (select max(x.id) from mensagens x where x.conversa_id = m.conversa_id
                              and x.direcao = 'out' and x.id < m.id) = p.ultima_msg_id
                    order by m.criado_em, m.id""",
                (conta_id, ca.hoje_br(agora), conversa_id) if conversa_id else (conta_id, ca.hoje_br(agora))
            ).fetchall()
    except Exception:  # noqa: BLE001 — sem a 379
        return 0
    for _pid, token, conv, texto, parcelado, paciente, parcelas in rows:
        m = _RE_RESPOSTA.match(texto or "")
        if not m:
            continue
        forma = _RESPOSTA[m.group(1)]
        if forma == "parcelado" and (not parcelado or int(parcelas) <= 1):
            continue
        c.commit()                          # o aceite abre as conexões dele
        if conversa_id is None and not cla.tentar_travar(c, conv):
            c.rollback()
            continue                        # o agente está nela; ele trata
        if aceitar(pool, token, nome=paciente, forma=forma, por="whatsapp", agora=agora):
            texto_ok = (f"Perfeito! Anotei: {FORMA_D[forma]}. A recepção te chama por aqui pra combinar o "
                        "pagamento e marcar a 1ª sessão 😊")
            if responder is not None:
                responder(texto_ok)
            else:
                _mandar(c, conta_id, conv, _destino_da_conversa(c, conta_id, conv), texto_ok)
            c.commit()
            feitos += 1
        else:
            c.commit()
    return feitos


def ja_respondido(c, conta_id: int, conversa_id: int) -> bool:
    """O poller já aceitou o plano por esta mensagem, um instante antes do agente: o
    agente fica quieto em vez de responder o mesmo número com a IA."""
    try:
        with c.transaction():
            return c.execute(
                """select 1 from clinica_planos p
                    where p.conta_id=%s and p.conversa_id=%s and p.aceito_por='whatsapp'
                      and p.aceito_em >= (select max(m.criado_em) from mensagens m
                                           where m.conversa_id = p.conversa_id and m.direcao = 'in')
                    limit 1""", (conta_id, conversa_id)).fetchone() is not None
    except Exception:  # noqa: BLE001
        return False


def _destino_da_conversa(c, conta_id: int, conv: int) -> str:
    r = c.execute("""select coalesce(nullif(p.whatsapp,''), nullif(p.telefone,''), cv.contato_ref, '')
                       from conversas cv left join prospeccao p on p.id = cv.prospeccao_id and p.conta_id = cv.conta_id
                      where cv.id=%s and cv.conta_id=%s""", (conv, conta_id)).fetchone()
    dig = ca._digitos(r[0] if r else "")
    return ("+" + (dig if dig.startswith("55") else "55" + dig)) if len(dig) >= 10 else ""


# ------------------------------------------------------------------ cobrar a decisão

def _paciente_falou(c, conta_id: int, p: dict) -> bool:
    """Respondeu qualquer coisa depois do plano: o automático para, fica com a pessoa."""
    conv, _d = _conversa_e_destino(c, conta_id, p)
    if not conv:
        return False
    return c.execute("""select 1 from mensagens m join conversas cv on cv.id = m.conversa_id
                         where m.conversa_id=%s and cv.conta_id=%s and m.direcao='in' and m.criado_em > %s
                         limit 1""", (conv, conta_id, p["enviado_em"])).fetchone() is not None


def texto_toque(p: dict, n: int) -> str:
    """Os lembretes automáticos: sem procedimento, sem valor de tratamento na frase de
    abertura — só "o seu plano", as formas e o link."""
    from finance.voltar_a_chamar import primeiro_nome
    nome = primeiro_nome(p["paciente"])
    oi = f"Oi{', ' + nome if nome else ''}!"
    validade = f"{p['validade_ate']:%d/%m}" if p["validade_ate"] else ""
    if n == 1:
        return (f"{oi} Conseguiu ver o seu plano de tratamento? Qualquer dúvida, é só me chamar por aqui 😊\n"
                f"{link(p)}")
    return (f"{oi} Passando pra lembrar do seu plano de tratamento: dá pra fazer no Pix à vista ou em até "
            f"{p['parcelas']}× no cartão. Ele vale até {validade}.\n{link(p)}\n"
            "Pra fechar, é só responder por aqui 😊")


def cobrar(pool, c, conta_id: int, agora: datetime) -> dict:
    """D+1 e D+3 pro paciente (dentro da janela, 1 automática por dia, respeitando
    SAIR/PARAR), a véspera pra recepção, e o vencimento."""
    from finance import clinica_vagas as cvg
    from finance import funil_regua as fr
    out = {"toques": 0, "avisos": 0, "vencidos": 0}
    hoje = ca.hoje_br(agora)
    r = c.execute("""update clinica_planos set status='vencido', atualizado_em=now()
                      where conta_id=%s and status='enviado' and validade_ate < %s returning id""",
                  (conta_id, hoje)).fetchall()
    out["vencidos"] = len(r)
    c.commit()
    cfg = config(c, conta_id)
    enviados = [plano(c, conta_id, x[0]) for x in c.execute(
        "select id from clinica_planos where conta_id=%s and status='enviado'", (conta_id,)).fetchall()]
    for p in enviados:
        if p["validade_ate"] == hoje + timedelta(days=1) and not _flag(c, conta_id, p["id"], "aviso_vespera_em"):
            c.execute("update clinica_planos set aviso_vespera_em=%s where id=%s and conta_id=%s",
                      (agora, p["id"], conta_id))
            c.commit()
            _avisar(c, conta_id, p, f"⏳ Plano vence amanhã: {p['paciente']}",
                    "Ainda sem decisão. Vale uma ligação antes de vencer.")
            out["avisos"] += 1
    if cfg["cobranca"] != "ligado" or not fr.dentro_da_janela(agora, fr.config(c, conta_id)):
        c.commit()
        return out
    bloq = cvg._bloqueados(c, conta_id)
    inicio_dia = ca.utc(hoje, time(0))
    for p in enviados:
        # D+1 primeiro; o D+3 só depois dele (plano enviado com a cobrança desligada e
        # ligada no 5º dia recebe um hoje e o outro amanhã, nunca os dois juntos)
        if not p["toque1_em"] and agora >= p["enviado_em"] + timedelta(days=1):
            n = 1
        elif p["toque1_em"] and not p["toque3_em"] and agora >= p["enviado_em"] + timedelta(days=3):
            n = 3
        else:
            continue
        if _paciente_falou(c, conta_id, p):
            continue
        conv, destino = _conversa_e_destino(c, conta_id, p)
        if not destino or not conv or destino[-8:] in bloq or cvg._recebeu_hoje(c, conta_id, conv, inicio_dia) \
                or _toque_hoje(c, conta_id, conv, inicio_dia):
            continue
        col = "toque3_em" if n == 3 else "toque1_em"
        if not c.execute(f"update clinica_planos set {col}=%s where id=%s and conta_id=%s and {col} is null "
                         "returning id", (agora, p["id"], conta_id)).fetchone():
            continue
        c.commit()
        res = _mandar(c, conta_id, conv, destino, texto_toque(p, n))
        if res.get("ok"):
            out["toques"] += 1
            c.execute("update clinica_planos set ultima_msg_id=%s where id=%s and conta_id=%s",
                      (res.get("mensagem_id"), p["id"], conta_id))
        c.commit()
    return out


def _flag(c, conta_id: int, plano_id: int, col: str) -> bool:
    return c.execute(f"select {col} is not null from clinica_planos where id=%s and conta_id=%s",
                     (plano_id, conta_id)).fetchone()[0]


def _toque_hoje(c, conta_id: int, conv: int, inicio_dia: datetime) -> bool:
    """Outro lembrete de plano pra esta conversa hoje (1 automática por dia)."""
    for p in c.execute("""select prospeccao_id, paciente_fone from clinica_planos where conta_id=%s
                           and (toque1_em >= %s or toque3_em >= %s)""", (conta_id, inicio_dia, inicio_dia)).fetchall():
        if ca._conversa(c, conta_id, {"lead": p[0], "fone": p[1]}) == conv:
            return True
    return False


def em_aberto(c, conta_id: int, agora: datetime) -> dict:
    """O que a tela Hoje mostra: desconto esperando aprovação, planos vencendo e
    planos que o paciente respondeu (o automático parou; é da recepção)."""
    try:
        with c.transaction():
            aprov = c.execute("select count(*) from clinica_planos where conta_id=%s and status='aguardando_aprovacao'",
                              (conta_id,)).fetchone()[0]
            ids = [r[0] for r in c.execute(
                "select id from clinica_planos where conta_id=%s and status='enviado' order by validade_ate",
                (conta_id,)).fetchall()]
    except Exception:  # noqa: BLE001
        return {"aprovar": 0, "vencendo": [], "responderam": []}
    hoje = ca.hoje_br(agora)
    ps = [plano(c, conta_id, i) for i in ids]
    return {"aprovar": aprov,
            "vencendo": [p for p in ps if p["validade_ate"] and p["validade_ate"] <= hoje + timedelta(days=1)],
            "responderam": [p for p in ps if _paciente_falou(c, conta_id, p)]}


# ------------------------------------------------------------------ o poller

def rodar(pool, agora: datetime | None = None) -> dict:
    agora = agora or datetime.now(timezone.utc)
    total = {"contas": 0, "aceites": 0, "toques": 0, "avisos": 0, "vencidos": 0}
    with pool.connection() as lockc:
        if not lockc.execute("select pg_try_advisory_lock(%s)", (_LOCK,)).fetchone()[0]:
            return total
        try:
            with pool.connection() as c:
                try:
                    with c.transaction():
                        from finance.clinica_planos_publico import contas_com_plano_enviado
                        contas = contas_com_plano_enviado(c)
                except Exception:  # noqa: BLE001 — sem a 379
                    contas = []
                for conta_id in contas:
                    try:
                        total["aceites"] += processar(pool, c, conta_id, agora)
                        r = cobrar(pool, c, conta_id, agora)
                        c.commit()
                        total["contas"] += 1
                        for k in ("toques", "avisos", "vencidos"):
                            total[k] += r[k]
                    except Exception:  # noqa: BLE001
                        c.rollback()
                        _log.warning("planos: conta %s falhou", conta_id, exc_info=True)
        finally:
            lockc.execute("select pg_advisory_unlock(%s)", (_LOCK,))
    return total
