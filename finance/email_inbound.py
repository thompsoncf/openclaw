"""Puxador de e-mails RECEBIDOS via IMAP (Google Workspace) pro inbox omnichannel.

Reusa a MESMA conta/senha-de-app do envio: a senha de app do Google vale pra IMAP
e SMTP, então sem SMTP_SENHA nada de novo é preciso — só habilitar o IMAP na caixa
(Gmail → Configurações → Encaminhamento e POP/IMAP → IMAP ativado). Tolerante a
falta de config: sem credencial vira no-op e nada quebra. O SDK é só a stdlib.

Env (todos com fallback pro SMTP já existente):
- IMAP_HOST   (default imap.gmail.com)
- IMAP_PORT   (default 993)
- IMAP_USER   (default SMTP_USER)  — o endereço da caixa
- IMAP_SENHA  (default SMTP_SENHA) — a senha de app do Google

Roteamento multi-tenant: a caixa (global) pertence à conta que tem um canal 'email'
em canais_config com identificador == IMAP_USER. O inbound entra como conversa/
mensagem (canal='email', direcao='in', autor='lead'), casando o remetente com um
lead (ou criando um lead novo, não-atribuído, igual ao inbound de WhatsApp).
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


def _cfg() -> dict | None:
    user = os.environ.get("IMAP_USER") or os.environ.get("SMTP_USER")
    senha = os.environ.get("IMAP_SENHA") or os.environ.get("SMTP_SENHA")
    if not (user and senha):
        return None
    return {"host": os.environ.get("IMAP_HOST", "imap.gmail.com"),
            "port": int(os.environ.get("IMAP_PORT", "993")),
            "user": user, "senha": senha}


def configurado() -> bool:
    return _cfg() is not None


def endereco() -> str | None:
    c = _cfg()
    return c["user"] if c else None


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
    """Extrai o texto do e-mail (prefere text/plain; cai pro html sem tags)."""
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


def buscar_novos(desde_uid: int | None = None, limite: int = 40):
    """Conecta na INBOX e devolve (lista de e-mails novos, maior_uid_visto).
    Best-effort: qualquer falha → ([], desde_uid)."""
    cfg = _cfg()
    if not cfg:
        return [], desde_uid
    out, maior = [], int(desde_uid or 0)
    try:
        M = imaplib.IMAP4_SSL(cfg["host"], cfg["port"], timeout=_TIMEOUT)
    except Exception as e:  # noqa: BLE001
        _log.info("email_in: conexão IMAP falhou: %s", e)
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
        # UID n:* pode devolver a última msg mesmo sem nova; filtra <= checkpoint
        uids = [u for u in uids if u > int(desde_uid or 0)]
        uids = sorted(uids)[-limite:]           # 1ª rodada: só as mais recentes
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
        _log.info("email_in: erro no IMAP: %s: %s", type(e).__name__, e)
        return out, (maior or desde_uid)
    finally:
        try:
            M.logout()
        except Exception:  # noqa: BLE001
            pass
    return out, (maior or desde_uid)


def _conta_do_mailbox(pool, endereco_caixa: str):
    """Conta dona da caixa: canal 'email' ativo com identificador == endereço."""
    with pool.connection() as c:
        r = c.execute(
            """select conta_id, ultimo_uid from canais_config
                where canal='email' and ativo and lower(identificador)=lower(%s) limit 1""",
            (endereco_caixa,)).fetchone()
    return (r[0], r[1]) if r else (None, None)


def sincronizar(pool, conta_id: int | None = None) -> int:
    """Puxa os e-mails novos da caixa e grava no inbox. Devolve quantos entraram.
    Se conta_id vier, confirma que a caixa é daquela conta; senão descobre pela
    config. Best-effort — nunca estoura."""
    cfg = _cfg()
    if not cfg:
        return 0
    dono, ultimo = _conta_do_mailbox(pool, cfg["user"])
    if not dono:
        return 0
    if conta_id is not None and conta_id != dono:
        return 0                      # a caixa global não é dessa conta
    conta_id = dono

    novos, maior = buscar_novos(desde_uid=ultimo)
    n = 0
    for m in novos:
        addr = m["from_email"]
        if not addr or addr == cfg["user"].strip().lower():
            continue                  # sem remetente ou é a própria caixa
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
                    lead_id = lead[0]
                else:
                    nome = (m["from_nome"] or addr)[:250]
                    lead_id = c.execute(
                        """insert into prospeccao (conta_id, vendedor_id, empresa, email,
                             origem, temperatura, status)
                           values (%s,null,%s,%s,'email_inbound','morno','novo') returning id""",
                        (conta_id, nome, addr)).fetchone()[0]
                conv = c.execute(
                    "select id from conversas where conta_id=%s and prospeccao_id=%s and canal='email'",
                    (conta_id, lead_id)).fetchone()
                if conv:
                    conv_id = conv[0]
                else:
                    conv_id = c.execute(
                        """insert into conversas (conta_id, prospeccao_id, canal, status, ultima_msg_em)
                           values (%s,%s,'email','aberta',now()) returning id""",
                        (conta_id, lead_id)).fetchone()[0]
                texto = ((m["assunto"] or "(sem assunto)") + "\n\n" + (m["corpo"] or "")).strip()
                c.execute(
                    """insert into mensagens (conversa_id, canal, direcao, autor, texto, provider_sid, criado_em)
                       values (%s,'email','in','lead',%s,%s, coalesce(%s, now()))""",
                    (conv_id, texto[:8000], m["message_id"], m["quando"]))
                c.execute(
                    "update conversas set ultima_msg_em=coalesce(%s, now()), status='aberta' where id=%s",
                    (m["quando"], conv_id))
                c.commit()
                n += 1
        except Exception as e:  # noqa: BLE001 (ex.: corrida entre 2 workers no índice único)
            _log.info("email_in: pulei um e-mail (%s): %s", addr, e)
            continue

    if maior and maior != ultimo:
        try:
            with pool.connection() as c:
                c.execute(
                    """update canais_config set ultimo_uid=%s, atualizado_em=now()
                        where conta_id=%s and canal='email'""", (maior, conta_id))
                c.commit()
        except Exception:  # noqa: BLE001
            pass
    return n


def poll_uma_vez(pool) -> int:
    """Uma passada do poller, com advisory lock (só 1 worker sincroniza por vez)."""
    if not configurado():
        return 0
    try:
        with pool.connection() as c:
            got = c.execute("select pg_try_advisory_lock(%s)", (_LOCK_KEY,)).fetchone()[0]
            if not got:
                return 0
            try:
                return sincronizar(pool)
            finally:
                c.execute("select pg_advisory_unlock(%s)", (_LOCK_KEY,))
                c.commit()
    except Exception as e:  # noqa: BLE001
        _log.info("email_in: poll falhou: %s", e)
        return 0
