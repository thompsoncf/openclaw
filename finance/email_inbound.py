"""Puxador de e-mails RECEBIDOS via IMAP pro inbox omnichannel.

Credencial POR CONTA (banco `canais_config`): cada empresa configura o próprio
endereço + senha de app na aba Canais. Fallback pro env (SMTP_USER/SMTP_SENHA)
quando o endereço da conta é o mesmo do SMTP global — assim a senha de app do
envio serve pra receber também. Tolerante a falta de config: no-op, nada quebra.
Só stdlib (imaplib/email).

canais_config (canal='email'): identificador = endereço; imap_senha = senha de app;
imap_host = servidor (default imap.gmail.com); ultimo_uid = checkpoint.
"""
from __future__ import annotations

import email
import html as _html
import imaplib
import logging
import os
import re
from email.header import decode_header, make_header
from email.utils import parseaddr, parsedate_to_datetime

_log = logging.getLogger("openclaw.email_in")
_TIMEOUT = 20
_LOCK_KEY = 918273645          # advisory lock do poller (evita 2 workers juntos)


# --------------------------------------------------------------- config

def _env_senha_para(endereco: str) -> str | None:
    """Se o endereço da conta é o mesmo do SMTP global, a senha de app do env serve."""
    u = (os.environ.get("SMTP_USER") or "").strip().lower()
    s = os.environ.get("SMTP_SENHA")
    return s if (u and s and u == (endereco or "").strip().lower()) else None


def _conta_cfg(pool, conta_id: int, canal: str = "email") -> dict | None:
    """Config IMAP de uma CAIXA da conta (banco + fallback env). `canal`='email' é a
    caixa principal; 'email2' é a secundária. None se não dá pra logar."""
    with pool.connection() as c:
        r = c.execute(
            """select identificador, imap_senha, imap_host, ultimo_uid
                 from canais_config where conta_id=%s and canal=%s and ativo""",
            (conta_id, canal)).fetchone()
    if not r or not r[0]:
        return None
    user = r[0].strip()
    senha = "".join((r[1] or "").split()) or _env_senha_para(user)   # senha de app não tem espaços
    if not senha:
        return None
    return {"host": (r[2] or "imap.gmail.com").strip(), "port": 993,
            "user": user, "senha": senha, "ultimo_uid": r[3]}


def configurado_conta(pool, conta_id: int, canal: str = "email") -> bool:
    return _conta_cfg(pool, conta_id, canal) is not None


def remetente_conta(pool, conta_id: int, canal: str = "email") -> str | None:
    """O e-mail que a EMPRESA usa pra ENVIAR pela caixa `canal` — só a caixa PRÓPRIA
    dela (canais_config). Nunca cai no SMTP global do ambiente: esse é o e-mail de
    sistema do Zaq (boas-vindas, convites), não pode aparecer como se fosse desta
    conta nem misturar com o de outra empresa. Sem caixa própria = None."""
    cfg = _conta_cfg(pool, conta_id, canal)
    return cfg["user"] if cfg else None


def _smtp_host(imap_host: str) -> str:
    h = (imap_host or "").strip().lower()
    return h.replace("imap", "smtp", 1) if "imap" in h else (h or "smtp.gmail.com")


def enviar_conta(pool, conta_id: int, destino: str, assunto: str, html: str,
                 texto_alt: str | None = None, from_nome: str | None = None,
                 reply_to: str | None = None, list_unsub: str = "", canal: str = "email") -> bool:
    """Envia PELA caixa `canal` da empresa (mesma senha de app do IMAP serve pro SMTP).
    Se a caixa não existe, NÃO envia — nunca usa o SMTP global no lugar da empresa,
    senão o e-mail sai (e a resposta do lead cai) na caixa de outra conta."""
    from finance import email_sender as es
    cfg = _conta_cfg(pool, conta_id, canal)
    if not cfg:
        return False
    return es.enviar_com_creds(cfg["user"], cfg["senha"], _smtp_host(cfg["host"]), 587,
                               destino, assunto, html, texto_alt, from_nome, reply_to,
                               list_unsub=list_unsub)


def salvar_config(pool, conta_id: int, endereco: str, senha: str, host: str = "",
                  canal: str = "email") -> None:
    """Salva/atualiza uma CAIXA de e-mail da conta (canal 'email' ou 'email2'). Senha
    vazia = mantém a atual."""
    endereco = (endereco or "").strip()
    host = (host or "").strip() or "imap.gmail.com"
    senha_limpa = "".join((senha or "").split())     # senha de app do Google não tem espaços
    with pool.connection() as c:
        c.execute(
            """insert into canais_config (conta_id, canal, identificador, imap_host, ativo)
               values (%s,%s,%s,%s,true)
               on conflict (conta_id, canal) do update set
                 identificador=excluded.identificador, imap_host=excluded.imap_host,
                 ativo=true, ultimo_uid=null, atualizado_em=now()""",
            (conta_id, canal, endereco, host))
        if senha_limpa:
            c.execute("update canais_config set imap_senha=%s where conta_id=%s and canal=%s",
                      (senha_limpa, conta_id, canal))
        c.commit()


# --------------------------------------------------------------- parsing

def _dec(s: str) -> str:
    try:
        return str(make_header(decode_header(s or "")))
    except Exception:  # noqa: BLE001
        return s or ""


def _strip_html(h: str) -> str:
    h = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", h)
    h = re.sub(r"(?i)<br\s*/?>", "\n", h)
    h = re.sub(r"(?i)</p\s*>", "\n", h)
    h = re.sub(r"(?s)<[^>]+>", " ", h)
    h = _html.unescape(h)
    return re.sub(r"\n{3,}", "\n\n", h).strip()


def _corpo(msg) -> str:
    if msg.is_multipart():
        for part in msg.walk():
            disp = str(part.get("Content-Disposition") or "")
            if part.get_content_type() == "text/plain" and "attachment" not in disp:
                try:
                    return (part.get_payload(decode=True) or b"").decode(
                        part.get_content_charset() or "utf-8", "replace").strip()
                except Exception:  # noqa: BLE001
                    continue
        for part in msg.walk():
            if part.get_content_type() == "text/html":
                try:
                    return _strip_html((part.get_payload(decode=True) or b"").decode(
                        part.get_content_charset() or "utf-8", "replace"))
                except Exception:  # noqa: BLE001
                    continue
        return ""
    try:
        raw = msg.get_payload(decode=True) or b""
        txt = raw.decode(msg.get_content_charset() or "utf-8", "replace")
        return _strip_html(txt) if msg.get_content_type() == "text/html" else txt.strip()
    except Exception:  # noqa: BLE001
        return ""


def _mascara(e: str) -> str:
    e = e or ""
    if "@" not in e:
        return e
    loc, dom = e.split("@", 1)
    return (loc[:3] + "***@" + dom) if len(loc) > 3 else (loc[:1] + "***@" + dom)


# --------------------------------------------------------------- IMAP

def buscar_novos(cfg: dict, desde_uid: int | None = None, limite: int = 40):
    """Conecta na INBOX e devolve (lista de e-mails novos, maior_uid). Best-effort."""
    if not cfg:
        return [], desde_uid
    out, maior = [], int(desde_uid or 0)
    try:
        M = imaplib.IMAP4_SSL(cfg["host"], cfg["port"], timeout=_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        _log.info("email_in: conexão IMAP falhou (%s): %s", cfg.get("user"), e)
        return [], desde_uid
    try:
        M.login(cfg["user"], cfg["senha"])
        M.select("INBOX")
        if desde_uid:
            typ, data = M.uid("search", None, f"UID {int(desde_uid) + 1}:*")
        else:
            typ, data = M.uid("search", None, "ALL")
        if typ != "OK":
            return [], desde_uid
        uids = [int(u) for u in (data[0] or b"").split()]
        uids = sorted(u for u in uids if u > int(desde_uid or 0))[-limite:]
        for uid in uids:
            try:
                typ, md = M.uid("fetch", str(uid), "(RFC822)")
                if typ != "OK" or not md or not md[0]:
                    continue
                msg = email.message_from_bytes(md[0][1])
                nome, addr = parseaddr(msg.get("From", ""))
                try:
                    quando = parsedate_to_datetime(msg.get("Date"))
                except Exception:  # noqa: BLE001
                    quando = None
                out.append({
                    "uid": uid,
                    "message_id": (msg.get("Message-ID") or "").strip()[:250] or None,
                    "from_email": (addr or "").strip().lower(),
                    "from_nome": _dec(nome).strip(),
                    "assunto": _dec(msg.get("Subject", "")).strip(),
                    "corpo": _corpo(msg),
                    "quando": quando,
                })
                maior = max(maior, uid)
            except Exception as e:  # noqa: BLE001
                _log.info("email_in: falha lendo uid %s: %s", uid, e)
                continue
    except Exception as e:  # noqa: BLE001
        _log.info("email_in: erro no IMAP (%s): %s: %s", cfg.get("user"), type(e).__name__, e)
        return out, (maior or desde_uid)
    finally:
        try:
            M.logout()
        except Exception:  # noqa: BLE001
            pass
    return out, (maior or desde_uid)


import re as _re

_BOUNCE_REMET = ("mailer-daemon", "postmaster", "mail-daemon", "maildelivery")
_BOUNCE_ASSUNTO = ("undeliver", "delivery status", "delivery failure", "delivery incomplete",
                   "failure notice", "returned mail", "mail delivery", "delivery has failed",
                   "não foi entregue", "nao foi entregue", "falha na entrega", "address not found",
                   "recusado", "mensagem não entregue", "mensagem nao entregue")
_EMAIL_RE = _re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def _eh_bounce(m: dict) -> bool:
    """Heurística: e-mail de retorno (mailer-daemon/DSN). Cobre os bounces do Gmail."""
    de = (m.get("from_email") or "").lower()
    assunto = (m.get("assunto") or "").lower()
    if any(t in de for t in _BOUNCE_REMET):
        return True
    return (not de) and any(t in assunto for t in _BOUNCE_ASSUNTO)


def _tratar_bounce(c, conta_id: int, m: dict) -> int:
    """Acha no corpo do retorno o(s) endereço(s) que falharam, casa com leads da conta,
    marca email_ok=false e PARA a sequência (protege a reputação de envio)."""
    texto = ((m.get("assunto") or "") + "\n" + (m.get("corpo") or "")).lower()
    cands = {a for a in _EMAIL_RE.findall(texto)
             if not any(t in a for t in _BOUNCE_REMET) and "@" in a}
    if not cands:
        return 0
    n = 0
    for lead_id, in (c.execute(
            "select id from prospeccao where conta_id=%s and lower(email) = any(%s)",
            (conta_id, list(cands))).fetchall() or []):
        c.execute("update prospeccao set email_ok=false, atualizado_em=now() where id=%s", (lead_id,))
        c.execute("""update campanha_alvos set status='erro', proximo_envio_em=null
                       where prospeccao_id=%s and status in ('fila','enviado')""", (lead_id,))
        c.execute("""insert into prospeccao_atividades (prospeccao_id, membro_id, tipo, descricao)
                     values (%s, null, 'bounce', 'E-mail retornou (inválido) — sequência parada')""",
                  (lead_id,))
        try:
            from finance.campanhas_motor import evento as _ev, _campanha_do_lead as _cdl
            _ev(c, _cdl(c, lead_id), lead_id, "email", "bounce")
        except Exception:  # noqa: BLE001
            pass
        n += 1
    if n:
        c.commit()
    return n


def sincronizar(pool, conta_id: int, canal: str = "email") -> int:
    """Puxa os e-mails novos de uma CAIXA da conta ('email' ou 'email2') e grava no
    inbox. Best-effort."""
    cfg = _conta_cfg(pool, conta_id, canal)
    if not cfg:
        return 0
    novos, maior = buscar_novos(cfg, desde_uid=cfg["ultimo_uid"])
    n = 0
    for m in novos:
        addr = m["from_email"]
        # bounce (retorno de e-mail inválido) → marca e para a sequência; não vira conversa
        if _eh_bounce(m):
            try:
                with pool.connection() as c:
                    _tratar_bounce(c, conta_id, m)
            except Exception as e:  # noqa: BLE001
                _log.info("email_in: bounce não tratado: %s", e)
            continue
        if not addr or addr == cfg["user"].strip().lower():
            continue
        try:
            with pool.connection() as c:
                if m["message_id"]:
                    ja = c.execute(
                        """select 1 from mensagens msg join conversas cv on cv.id=msg.conversa_id
                            where cv.conta_id=%s and msg.provider_sid=%s limit 1""",
                        (conta_id, m["message_id"])).fetchone()
                    if ja:
                        continue
                lead = c.execute(
                    """select id from prospeccao where conta_id=%s and lower(email)=%s
                        order by atualizado_em desc limit 1""", (conta_id, addr)).fetchone()
                if lead:
                    # remetente JÁ é um lead → anexa na conversa dele
                    lead_id = lead[0]
                    conv = c.execute(
                        "select id from conversas where conta_id=%s and prospeccao_id=%s and canal='email'",
                        (conta_id, lead_id)).fetchone()
                    conv_id = conv[0] if conv else c.execute(
                        """insert into conversas (conta_id, prospeccao_id, canal, status, ultima_msg_em)
                           values (%s,%s,'email','aberta',now()) returning id""",
                        (conta_id, lead_id)).fetchone()[0]
                else:
                    # remetente NOVO → NÃO cria lead; conversa órfã (você decide depois se vira lead)
                    conv = c.execute(
                        """select id from conversas where conta_id=%s and canal='email'
                            and prospeccao_id is null and contato_ref=%s""", (conta_id, addr)).fetchone()
                    conv_id = conv[0] if conv else c.execute(
                        """insert into conversas (conta_id, prospeccao_id, canal, contato_ref, status, ultima_msg_em)
                           values (%s,null,'email',%s,'aberta',now()) returning id""",
                        (conta_id, addr)).fetchone()[0]
                texto = ((m["assunto"] or "(sem assunto)") + "\n\n" + (m["corpo"] or "")).strip()
                c.execute(
                    """insert into mensagens (conversa_id, canal, direcao, autor, texto, provider_sid, criado_em)
                       values (%s,'email','in','lead',%s,%s, coalesce(%s, now()))""",
                    (conv_id, texto[:8000], m["message_id"], m["quando"]))
                c.execute(
                    "update conversas set ultima_msg_em=coalesce(%s, now()), status='aberta' where id=%s",
                    (m["quando"], conv_id))
                if lead:      # lead respondeu → para a sequência e promove base → lead (funil)
                    c.execute("""update campanha_alvos set status='respondeu', proximo_envio_em=null
                                   where prospeccao_id=%s and status in ('fila','enviado')""", (lead_id,))
                    c.execute("""update prospeccao set estagio='lead', temperatura='quente',
                                   status=case when estagio='base' then 'novo' else status end,
                                   atualizado_em=now() where id=%s and conta_id=%s""",
                              (lead_id, conta_id))
                    try:
                        from finance.campanhas_motor import evento as _ev, _campanha_do_lead as _cdl
                        _ev(c, _cdl(c, lead_id), lead_id, "email", "respondeu")
                    except Exception:  # noqa: BLE001
                        pass
                c.commit()
                n += 1
        except Exception as e:  # noqa: BLE001 (corrida no índice único etc.)
            _log.info("email_in: pulei um e-mail (%s): %s", addr, e)
            continue

    if maior and maior != cfg["ultimo_uid"]:
        try:
            with pool.connection() as c:
                c.execute("""update canais_config set ultimo_uid=%s, atualizado_em=now()
                              where conta_id=%s and canal=%s""", (maior, conta_id, canal))
                c.commit()
        except Exception:  # noqa: BLE001
            pass
    return n


def diagnostico(pool, conta_id: int, canal: str = "email") -> dict:
    """Testa uma CAIXA da conta AO VIVO e diz em que etapa parou (pra mostrar no painel)."""
    with pool.connection() as c:
        r = c.execute(
            "select identificador, imap_senha, imap_host from canais_config where conta_id=%s and canal=%s and ativo",
            (conta_id, canal)).fetchone()
    if not r or not r[0]:
        return {"ok": False, "etapa": "config",
                "msg": "Nenhum e-mail configurado nesta conta. Preencha o endereço e a senha de app aqui na aba Canais."}
    endereco = r[0].strip()
    senha_propria = "".join((r[1] or "").split())
    senha = senha_propria or _env_senha_para(endereco)
    host = (r[2] or "imap.gmail.com").strip()
    if not senha:
        return {"ok": False, "etapa": "senha", "usuario": _mascara(endereco),
                "msg": "Falta a senha de app dessa caixa. Cole a senha de app do Google no campo abaixo e salve."}
    try:
        M = imaplib.IMAP4_SSL(host, 993, timeout=_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "etapa": "conexao", "usuario": _mascara(endereco),
                "msg": f"Não conectei no servidor IMAP ({host}): {e}"}
    try:
        M.login(endereco, senha)
    except Exception as e:  # noqa: BLE001
        try:
            M.logout()
        except Exception:  # noqa: BLE001
            pass
        dica = ""
        if "gmail" in host.lower() and senha_propria and len(senha_propria) != 16:
            dica = (f" ⚠️ A senha salva tem {len(senha_propria)} caractere(s) — a senha de app "
                    "do Google tem 16 (4 blocos de 4). Parece que não é uma senha de app.")
        return {"ok": False, "etapa": "login", "usuario": _mascara(endereco),
                "msg": ("Falha no login IMAP. Confira: (1) verificação em 2 etapas ligada + IMAP "
                        "ligado na caixa; (2) use uma SENHA DE APP do Google (não a senha normal)."
                        + dica + f" Detalhe: {e}")}
    try:
        typ, data = M.select("INBOX")
        total = int(data[0]) if typ == "OK" and data and data[0] else 0
        d = {"ok": True, "etapa": "ok", "usuario": _mascara(endereco), "inbox_total": total,
             "msg": f"✅ Conectei em {_mascara(endereco)}! A Caixa de Entrada tem {total} e-mail(s). "
                    "Se o seu teste não aparecer, ele pode estar em Spam/Promoções."}
    except Exception as e:  # noqa: BLE001
        d = {"ok": False, "etapa": "inbox", "usuario": _mascara(endereco),
             "msg": f"Loguei, mas não abri a INBOX: {e}"}
    finally:
        try:
            M.logout()
        except Exception:  # noqa: BLE001
            pass
    return d


def poll_uma_vez(pool) -> int:
    """Uma passada do poller: sincroniza toda conta com e-mail configurado, com
    advisory lock (só 1 worker por vez)."""
    total = 0
    try:
        with pool.connection() as c:
            got = c.execute("select pg_try_advisory_lock(%s)", (_LOCK_KEY,)).fetchone()[0]
            if not got:
                return 0
            try:
                caixas = c.execute(
                    "select conta_id, canal from canais_config where canal in ('email','email2') and ativo").fetchall()
                for cid, canal in caixas:
                    try:
                        total += sincronizar(pool, cid, canal)
                    except Exception:  # noqa: BLE001
                        pass
            finally:
                c.execute("select pg_advisory_unlock(%s)", (_LOCK_KEY,))
                c.commit()
    except Exception as e:  # noqa: BLE001
        _log.info("email_in: poll falhou: %s", e)
    return total
