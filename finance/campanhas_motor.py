"""Motor de disparo das campanhas de e-mail (Fase 2, etapa 3).

Roda no poller (a cada ~2min): pra cada campanha ATIVA, respeitando o limite/dia,
manda o passo devido de cada lead (D0 com IA, follow-ups por template), grava no
inbox (conversa de e-mail) e agenda o próximo passo. Para no lead que respondeu ou
descadastrou. Best-effort: nunca estoura o poller.

LGPD: todo e-mail leva link de descadastro (token assinado) + identificação da
empresa. WhatsApp entra só como link wa.me (nunca disparo frio).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import os
from datetime import date, datetime, timedelta, timezone

from finance import servicos_catalogo as scat
from finance.email_sender import _app_url, enviar_email, remetente_configurado

_log = logging.getLogger("openclaw.campanhas")
_LOCK = 771144  # advisory lock (só um worker dispara por vez)
_MAX_PASS = 12  # teto de envios por passada (o limite/dia é o teto real)


# ------------------------------------------------------------ descadastro (token)

def _seg() -> bytes:
    return (os.environ.get("PORTAL_SECRET") or "zaq-descad-fallback").encode()


def descad_token(conta_id: int, email: str) -> str:
    raw = f"{conta_id}:{(email or '').lower()}"
    sig = hmac.new(_seg(), raw.encode(), hashlib.sha256).hexdigest()[:16]
    return base64.urlsafe_b64encode(f"{raw}:{sig}".encode()).decode().rstrip("=")


def descad_verify(token: str):
    try:
        pad = token + "=" * (-len(token) % 4)
        s = base64.urlsafe_b64decode(pad.encode()).decode()
        conta_id, email, sig = s.rsplit(":", 2)
        good = hmac.new(_seg(), f"{conta_id}:{email}".encode(), hashlib.sha256).hexdigest()[:16]
        if hmac.compare_digest(good, sig):
            return int(conta_id), email
    except Exception:  # noqa: BLE001
        pass
    return None, None


# ------------------------------------------------------------ interesse (CTA)

def interesse_token(conta_id: int, prospeccao_id: int, campanha_id: int) -> str:
    raw = f"{conta_id}:{prospeccao_id}:{campanha_id}"
    sig = hmac.new(_seg(), ("int:" + raw).encode(), hashlib.sha256).hexdigest()[:16]
    return base64.urlsafe_b64encode(f"{raw}:{sig}".encode()).decode().rstrip("=")


def interesse_verify(token: str):
    try:
        pad = token + "=" * (-len(token) % 4)
        s = base64.urlsafe_b64decode(pad.encode()).decode()
        conta_id, pid, camp_id, sig = s.rsplit(":", 3)
        good = hmac.new(_seg(), f"int:{conta_id}:{pid}:{camp_id}".encode(),
                        hashlib.sha256).hexdigest()[:16]
        if hmac.compare_digest(good, sig):
            return int(conta_id), int(pid), int(camp_id)
    except Exception:  # noqa: BLE001
        pass
    return None, None, None


# ------------------------------------------------------------ conteúdo

def _fmt(txt: str, lead: dict) -> str:
    try:
        return (txt or "").format_map(_Safe({
            "empresa": lead.get("empresa") or "", "nome": lead.get("empresa") or "",
            "cidade": lead.get("cidade") or "", "uf": lead.get("uf") or "",
            "segmento": lead.get("segmento") or "sua área",
        }))
    except Exception:  # noqa: BLE001 (chave solta no texto do usuário)
        return txt or ""


class _Safe(dict):
    def __missing__(self, k):        # variável desconhecida no template → some
        return ""


def _email_ia(pool, conta_id: int, lead: dict) -> dict:
    """Agente escreve o 1º e-mail (assunto + corpo), único por lead. Fallback simples."""
    fallback = {
        "assunto": f"Uma ideia rápida pra {lead.get('empresa') or 'sua empresa'}",
        "corpo": (f"Oi, {lead.get('empresa') or ''}!\n\nVi que vocês atuam em "
                  f"{lead.get('segmento') or 'sua área'}"
                  f"{(' em ' + lead['cidade']) if lead.get('cidade') else ''}. "
                  "A gente ajuda empresas a atenderem melhor e organizarem o financeiro sem "
                  "aumentar a equipe.\n\nFaz sentido eu te mostrar em 5 min como ficaria aí?"),
    }
    try:
        from finance.agente import _conhecimento
        with pool.connection() as c:
            instr, faqs = _conhecimento(c, conta_id)
        catalogo = scat.listar(pool, conta_id)
        cat = "\n".join(f"- {s['nome']}: mensal R${s['mensal_centavos'] // 100}" for s in catalogo[:8])
        system = (
            "Você escreve e-mails de 1º contato (prospecção fria B2B) em português do Brasil, "
            "curtos (4-6 linhas), calorosos e diretos, focados no benefício. Sem enrolação, "
            "sem exagero. Termine com uma pergunta leve de call-to-action. Use SÓ o que está na "
            "base; não invente preço/promessa. Responda em JSON: "
            '{"assunto":"...","corpo":"..."}\n\n'
            f"EMPRESA (quem envia):\n{instr or '(nada)'}\n\nSERVIÇOS:\n{cat or '(sem catálogo)'}")
        pedir = (f"Escreva pra este lead:\nEmpresa: {lead.get('empresa')}\n"
                 f"Segmento: {lead.get('segmento') or '—'}\nCidade: {lead.get('cidade') or '—'}\n\n"
                 "Só o JSON.")
        from core.brain import Brain
        resp = Brain().chamar(system=system, mensagens=[{"role": "user", "content": pedir}])
        txt = "".join(getattr(b, "text", "") for b in resp.content
                      if getattr(b, "type", None) == "text").strip()
        import json
        import re
        txt = re.sub(r"^```json|^```|```$", "", txt, flags=re.M).strip()
        d = json.loads(txt)
        assunto = (d.get("assunto") or "").strip()
        corpo = (d.get("corpo") or "").strip()
        if assunto and corpo:
            return {"assunto": assunto[:200], "corpo": corpo[:4000]}
    except Exception as e:  # noqa: BLE001
        _log.info("campanha IA falhou conta=%s: %s", conta_id, e)
    return fallback


def _html(corpo: str, lead: dict, conta_nome: str, link_descad: str,
          link_interesse: str = "") -> str:
    paras = "".join(
        f'<p style="margin:0 0 12px">{_esc(p)}</p>' for p in (corpo or "").split("\n\n") if p.strip())
    cta = ""
    if link_interesse:
        cta = (
            '<table role="presentation" cellpadding="0" cellspacing="0" style="margin:18px 0 4px">'
            f'<tr><td style="border-radius:8px;background:#16a34a">'
            f'<a href="{link_interesse}" style="display:inline-block;padding:11px 22px;color:#fff;'
            'font-weight:bold;font-size:15px;text-decoration:none;border-radius:8px">✅ Tenho interesse</a>'
            '</td></tr></table>'
            '<p style="font-size:12px;color:#999;margin:2px 0 0">Clique acima e a gente te manda o material '
            'na hora.</p>')
    wa = ""
    num = "".join(ch for ch in (lead.get("whatsapp") or "") if ch.isdigit())
    if num:
        wa = (f'<p style="margin:14px 0 0"><a href="https://wa.me/{num}" '
              'style="color:#0f766e;font-weight:bold;text-decoration:none">💬 Falar no WhatsApp</a></p>')
    return (
        '<div style="font-family:system-ui,Arial,sans-serif;font-size:15px;line-height:1.6;color:#222">'
        f'{paras}{cta}{wa}'
        f'<hr style="border:0;border-top:1px solid #eee;margin:20px 0 10px">'
        f'<p style="font-size:12px;color:#999;margin:0">{_esc(conta_nome or "")} · Se não quiser mais '
        f'receber, <a href="{link_descad}" style="color:#888">descadastrar</a>.</p></div>')


def _esc(s: str) -> str:
    return (s or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


# ------------------------------------------------------------ disparo

def _agora():
    return datetime.now(timezone.utc)


def enviar_pendentes(pool) -> int:
    """Uma passada do motor. Best-effort."""
    if not remetente_configurado():
        return 0
    try:
        return _disparar(pool)
    except Exception as e:  # noqa: BLE001
        _log.info("campanhas.enviar_pendentes falhou: %s: %s", type(e).__name__, e)
        return 0


def _disparar(pool) -> int:
    enviados = 0
    with pool.connection() as lockc:
        if not lockc.execute("select pg_try_advisory_lock(%s)", (_LOCK,)).fetchone()[0]:
            return 0
        try:
            with pool.connection() as c:
                camps = c.execute(
                    """select id, conta_id, nome, limite_dia, enviados_hoje, dia_contagem
                         from campanhas where status='ativa'""").fetchall()
            hoje = date.today()
            for (cid, conta_id, nome, limite, env_hoje, dia) in camps:
                if dia != hoje:
                    env_hoje = 0
                    with pool.connection() as c:
                        c.execute("update campanhas set enviados_hoje=0, dia_contagem=%s where id=%s",
                                  (hoje, cid))
                        c.commit()
                restante = max(0, (limite or 0) - (env_hoje or 0))
                if restante <= 0:
                    continue
                n = _disparar_campanha(pool, cid, conta_id, nome, min(restante, _MAX_PASS))
                enviados += n
        finally:
            lockc.execute("select pg_advisory_unlock(%s)", (_LOCK,))
            lockc.commit()
    return enviados


def _disparar_campanha(pool, camp_id, conta_id, camp_nome, teto) -> int:
    with pool.connection() as c:
        conta_nome = (c.execute("select nome from contas where id=%s", (conta_id,)).fetchone() or [""])[0]
        passos = c.execute(
            "select ordem, dias_apos, assunto, corpo, usar_ia from campanha_passos where campanha_id=%s order by ordem",
            (camp_id,)).fetchall()
        if not passos:
            return 0
        pmap = {p[0]: {"dias": p[1], "assunto": p[2], "corpo": p[3], "ia": p[4]} for p in passos}
        alvos = c.execute(
            """select a.id, a.prospeccao_id, a.passo_atual, a.status,
                      p.empresa, p.segmento, p.cidade, p.uf, p.email, p.whatsapp
                 from campanha_alvos a join prospeccao p on p.id=a.prospeccao_id
                where a.campanha_id=%s and a.status in ('fila','enviado')
                  and (a.proximo_envio_em is null or a.proximo_envio_em <= now())
                order by a.proximo_envio_em asc nulls first limit %s""", (camp_id, teto)).fetchall()
    feitos = 0
    for a in alvos:
        (aid, pid, passo_atual, status, empresa, segmento, cidade, uf, email, whatsapp) = a
        if passo_atual not in pmap:
            _finalizar(pool, aid)
            continue
        email = (email or "").strip().lower()
        if not email or "@" not in email:
            _marcar(pool, aid, "erro")
            continue
        # descadastrado? já respondeu? → não manda
        with pool.connection() as c:
            if c.execute("select 1 from descadastros where conta_id=%s and lower(email)=%s",
                         (conta_id, email)).fetchone():
                _marcar(pool, aid, "descadastrou")
                continue
        if _respondeu(pool, conta_id, pid):
            _marcar(pool, aid, "respondeu")
            continue
        lead = {"empresa": empresa, "segmento": segmento, "cidade": cidade, "uf": uf,
                "email": email, "whatsapp": whatsapp}
        passo = pmap[passo_atual]
        if passo["ia"]:
            m = _email_ia(pool, conta_id, lead)
            assunto, corpo = m["assunto"], m["corpo"]
        else:
            assunto = _fmt(passo["assunto"] or f"Contato · {camp_nome}", lead)
            corpo = _fmt(passo["corpo"] or "", lead)
        link = _app_url() + "/descadastrar?t=" + descad_token(conta_id, email)
        link_int = _app_url() + "/tenho-interesse?t=" + interesse_token(conta_id, pid, camp_id)
        ok = enviar_email(email, assunto, _html(corpo, lead, conta_nome, link, link_int),
                          texto_alt=corpo + "\n\nTenho interesse: " + link_int,
                          from_nome=(conta_nome or None))
        if not ok:
            _marcar(pool, aid, "erro")
            continue
        _registrar_e_avancar(pool, conta_id, camp_id, aid, pid, passo_atual, pmap, assunto, corpo)
        feitos += 1
    return feitos


def _respondeu(pool, conta_id, prospeccao_id) -> bool:
    with pool.connection() as c:
        return bool(c.execute(
            """select 1 from mensagens m join conversas cv on cv.id=m.conversa_id
                where cv.conta_id=%s and cv.prospeccao_id=%s and cv.canal='email'
                  and m.direcao='in' limit 1""", (conta_id, prospeccao_id)).fetchone())


def _prox_passo(pmap, atual):
    """Devolve (proximo_ordem, dias_gap) ou (None, None) se acabou."""
    if (atual + 1) in pmap:
        gap = max(0, pmap[atual + 1]["dias"] - pmap[atual]["dias"])
        return atual + 1, gap
    return None, None


def _registrar_e_avancar(pool, conta_id, camp_id, aid, pid, passo_atual, pmap, assunto, corpo):
    prox, gap = _prox_passo(pmap, passo_atual)
    with pool.connection() as c:
        # grava no inbox (conversa de e-mail do lead)
        conv = c.execute("select id from conversas where conta_id=%s and prospeccao_id=%s and canal='email'",
                         (conta_id, pid)).fetchone()
        conv_id = conv[0] if conv else c.execute(
            """insert into conversas (conta_id, prospeccao_id, canal, status, ultima_msg_em)
               values (%s,%s,'email','aberta',now()) returning id""", (conta_id, pid)).fetchone()[0]
        c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto)
                     values (%s,'email','out','bot',%s)""", (conv_id, f"{assunto}\n\n{corpo}"[:8000]))
        c.execute("update conversas set ultima_msg_em=now() where id=%s", (conv_id,))
        # avança o alvo
        if prox is None:
            c.execute("""update campanha_alvos set status='concluido', passo_atual=%s,
                           ultima_msg_em=now(), proximo_envio_em=null where id=%s""",
                      (passo_atual + 1, aid))
        else:
            c.execute("""update campanha_alvos set status='enviado', passo_atual=%s,
                           ultima_msg_em=now(), proximo_envio_em=now()+(%s || ' days')::interval where id=%s""",
                      (prox, str(gap), aid))
        c.execute("update campanhas set enviados_hoje=enviados_hoje+1, dia_contagem=%s where id=%s",
                  (date.today(), camp_id))
        c.commit()


def _marcar(pool, aid, status):
    with pool.connection() as c:
        c.execute("update campanha_alvos set status=%s, proximo_envio_em=null where id=%s", (status, aid))
        c.commit()


def _finalizar(pool, aid):
    _marcar(pool, aid, "concluido")


def registrar_interesse(pool, conta_id: int, prospeccao_id: int, campanha_id: int) -> dict:
    """Lead clicou 'Tenho interesse': para a sequência, marca QUENTE, manda o material
    da campanha por e-mail e deixa o agente IA assumir a conversa. Best-effort."""
    with pool.connection() as c:
        lead = c.execute(
            "select empresa, email, whatsapp from prospeccao where id=%s and conta_id=%s",
            (prospeccao_id, conta_id)).fetchone()
        if not lead:
            return {"ok": False}
        empresa, email, _wa = lead
        material = (c.execute("select coalesce(material,'') from campanhas where id=%s and conta_id=%s",
                              (campanha_id, conta_id)).fetchone() or [""])[0]
        conta_nome = (c.execute("select nome from contas where id=%s", (conta_id,)).fetchone() or [""])[0]
        # para a sequência deste lead
        c.execute("""update campanha_alvos set status='respondeu', proximo_envio_em=null
                       where campanha_id=%s and prospeccao_id=%s""", (campanha_id, prospeccao_id))
        # esquenta o lead
        c.execute("update prospeccao set temperatura='quente', atualizado_em=now() where id=%s and conta_id=%s",
                  (prospeccao_id, conta_id))
        # conversa de e-mail do lead
        conv = c.execute("select id, agente_ativo from conversas where conta_id=%s and prospeccao_id=%s and canal='email'",
                         (conta_id, prospeccao_id)).fetchone()
        conv_id = conv[0] if conv else c.execute(
            """insert into conversas (conta_id, prospeccao_id, canal, status, ultima_msg_em)
               values (%s,%s,'email','aberta',now()) returning id""", (conta_id, prospeccao_id)).fetchone()[0]
        # registra o clique como entrada (o motor/agente entende que respondeu)
        c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto)
                     values (%s,'email','in','lead','[clicou: Tenho interesse]')""", (conv_id,))
        master = c.execute("select coalesce(ativo,false) from agente_config where conta_id=%s",
                           (conta_id,)).fetchone()
        agente_on = bool(master and master[0])
        if agente_on:
            c.execute("update conversas set agente_ativo=true, status='aberta', ultima_msg_em=now() where id=%s",
                      (conv_id,))
        c.commit()
    # manda o material na hora (por e-mail), gravado como msg do bot
    enviado = False
    if email and "@" in email:
        corpo = (f"Que bom, {empresa or 'tudo bem'}! 🎉\n\n"
                 + (f"Segue nosso material pra você conhecer melhor:\n{material}\n\n" if material else "")
                 + "Posso te mostrar em 2 minutinhos como fica aí? É só responder este e-mail. 😊")
        assunto = "Seu material — " + (conta_nome or "ZAQ")
        try:
            enviado = enviar_email(email, assunto, _html(corpo, {"whatsapp": None}, conta_nome,
                                   _app_url() + "/descadastrar?t=" + descad_token(conta_id, email)),
                                   texto_alt=corpo, from_nome=(conta_nome or None))
        except Exception:  # noqa: BLE001
            enviado = False
        if enviado:
            with pool.connection() as c:
                conv = c.execute("select id from conversas where conta_id=%s and prospeccao_id=%s and canal='email'",
                                 (conta_id, prospeccao_id)).fetchone()
                if conv:
                    c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto)
                                 values (%s,'email','out','bot',%s)""", (conv[0], f"{assunto}\n\n{corpo}"[:8000]))
                    c.execute("update conversas set ultima_msg_em=now() where id=%s", (conv[0],))
                    c.commit()
    return {"ok": True, "empresa": empresa, "material_enviado": enviado}


def registrar_descadastro(pool, conta_id: int, email: str, token: str = "") -> None:
    email = (email or "").strip().lower()
    with pool.connection() as c:
        c.execute("""insert into descadastros (conta_id, email, token) values (%s,%s,%s)
                     on conflict (conta_id, lower(email)) do nothing""", (conta_id, email, token[:120]))
        c.execute("""update campanha_alvos set status='descadastrou', proximo_envio_em=null
                       where prospeccao_id in (select id from prospeccao where conta_id=%s and lower(email)=%s)
                         and status in ('fila','enviado')""", (conta_id, email))
        c.commit()
