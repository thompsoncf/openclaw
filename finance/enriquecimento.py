"""Enriquecimento/verificação de canais do lead.

Dado o site da empresa (que a captação do Maps traz), raspa a home + a página de
contato e descobre: **e-mail**, **@Instagram** e **link de WhatsApp**. Valida o
e-mail (sintaxe + o domínio resolve) e detecta se o telefone é celular (provável
WhatsApp). Tudo best-effort: qualquer falha → devolve o que achou (nunca estoura).

Não faz cold nem manda nada — só descobre e marca quais canais o lead tem, pra a
lista ficar pronta pra abordar (o e-mail é o canal frio; WhatsApp/IG = morno).
"""
from __future__ import annotations

import re
import socket
from urllib.parse import urljoin, urlparse

import httpx

_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
_IG_RE = re.compile(r"instagram\.com/([A-Za-z0-9_.]+)", re.I)
_IG_EXT = re.compile(r"\.(php|js|css|html?|ico|png|jpe?g|gif|svg|json|xml|txt)$", re.I)
_WA_RE = re.compile(r"(?:wa\.me/|api\.whatsapp\.com/send\?phone=)\+?(\d{8,15})", re.I)
_UA = {"User-Agent": "Mozilla/5.0 (compatible; ZaqBot/1.0; +https://app.zaq-ia.com)"}
_TIMEOUT = 9
_LIXO = ("sentry.", "example.com", "wixpress", "godaddy", "sentry-next",
         "@sentry", ".png", ".jpg", ".gif", ".webp", "domain.com", "email.com",
         "your@", "seuemail", "nome@", "u003e")
_IG_RESERVADO = {"p", "reel", "reels", "explore", "accounts", "stories", "tv", "", "about"}


def _norm_site(site: str) -> str:
    s = (site or "").strip()
    if not s:
        return ""
    return s if s.startswith("http") else "https://" + s


def _dominio(url: str) -> str:
    try:
        return (urlparse(url).hostname or "").lower().replace("www.", "")
    except Exception:  # noqa: BLE001
        return ""


def _baixar(url: str) -> str:
    # max_redirects baixo: alguns sites entram em loop de /error (302 infinito) e
    # travariam a raspagem — 3 saltos bastam pro caso normal.
    try:
        with httpx.Client(headers=_UA, timeout=_TIMEOUT, follow_redirects=True, max_redirects=3) as cli:
            r = cli.get(url)
        if r.status_code < 400 and "html" in r.headers.get("content-type", ""):
            return r.text[:500000]
    except Exception:  # noqa: BLE001
        return ""
    return ""


def _validar_email(email: str) -> bool:
    email = (email or "").strip().lower()
    if not _EMAIL_RE.fullmatch(email) or "@" not in email:
        return False
    dom = email.rsplit("@", 1)[1]
    try:
        socket.getaddrinfo(dom, None)     # o domínio resolve (não garante entrega, mas filtra lixo)
        return True
    except Exception:  # noqa: BLE001
        return False


def _eh_movel_br(telefone: str) -> bool:
    d = re.sub(r"\D", "", telefone or "")
    if d.startswith("55"):
        d = d[2:]
    return len(d) == 11 and d[2] == "9"    # DDD + 9XXXXXXXX


def enriquecer(site: str = "", telefone: str = "", email_atual: str = "") -> dict:
    """Raspa o site e devolve {email, email_ok, instagram, whatsapp}. Best-effort."""
    out = {"email": None, "email_ok": False, "instagram": None, "whatsapp": None}
    base = _norm_site(site)
    html = ""
    if base:
        vistos = 0
        for p in ("", "/contato", "/contact", "/fale-conosco", "/contatos"):
            html += "\n" + _baixar(urljoin(base, p) if p else base)
            vistos += 1
            if len(html) > 350000 or vistos >= 3:
                break

    # e-mail: prefere e-mails do próprio domínio do site; descarta lixo
    site_dom = _dominio(base)
    achados = [e.lower() for e in _EMAIL_RE.findall(html)]
    achados = [e for e in achados if not any(x in e for x in _LIXO)]
    achados = sorted(set(achados),
                     key=lambda e: (0 if site_dom and e.endswith("@" + site_dom) else 1, len(e)))
    email = achados[0] if achados else ((email_atual or "").strip().lower() or None)
    if email:
        out["email"] = email
        out["email_ok"] = _validar_email(email)

    # instagram (descarta reservados e caminhos de arquivo tipo rsrc.php, static.js…)
    for m in _IG_RE.finditer(html):
        h = m.group(1).strip("/").lower()
        if h in _IG_RESERVADO or len(h) < 2 or _IG_EXT.search(h):
            continue
        out["instagram"] = "@" + h
        break

    # whatsapp: link wa.me no site é o sinal forte; senão, celular BR é provável
    w = _WA_RE.search(html)
    if w:
        out["whatsapp"] = "+" + re.sub(r"\D", "", w.group(1))
    elif _eh_movel_br(telefone):
        d = re.sub(r"\D", "", telefone)
        out["whatsapp"] = "+" + (d if d.startswith("55") else "55" + d)
    return out
