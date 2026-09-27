"""Certificado em NUVEM (prontuário, fase 4): o cliente dos provedores PSC do ITI.

Os provedores de assinatura em nuvem credenciados na ICP-Brasil (BirdID, VIDaaS, SafeID,
NeoID, RemoteID…) seguem a mesma API, a do DOC-ICP-17.01 (OAuth 2 com PKCE):

  1. o Zaq manda o profissional pra página do provedor (/v0/oauth/authorize); ele
     autoriza no aplicativo do celular, e o provedor volta pro Zaq com um código;
  2. o código vira um token (/v0/oauth/token), que vale alguns minutos;
  3. com o token: o certificado (/v0/oauth/certificate-discovery) e a assinatura dos
     hashes (/v0/oauth/signature), TODOS de uma vez — é o "assinar as 9 de hoje".

A chave privada nunca sai do provedor: o Zaq manda só o hash e recebe a assinatura.

Cada provedor pede o cadastro da clínica como aplicação (client_id, client_secret e o
endereço de volta, {app}/painel/clinica/certificado/volta). No Render:
  PSC_<PROVEDOR>_CLIENT_ID, PSC_<PROVEDOR>_CLIENT_SECRET e, se não for o padrão abaixo,
  PSC_<PROVEDOR>_URL. Sem as credenciais, o provedor aparece como "ainda não ligado".
"""
from __future__ import annotations

import base64
import hashlib
import os
import secrets

#: chave: (nome, URL padrão da API). Sem URL padrão, ela vem do Render (PSC_<CHAVE>_URL).
PROVEDORES = {
    "birdid": ("BirdID (Soluti)", "https://api.birdid.com.br"),
    "vidaas": ("VIDaaS (Valid)", "https://certificado.vidaas.com.br"),
    "safeid": ("SafeID (Safeweb)", ""),
    "neoid": ("NeoID (Serpro)", ""),
    "remoteid": ("RemoteID (Certisign)", ""),
}
SHA256_OID = "2.16.840.1.101.3.4.2.1"
VALIDADE_S = 600          # o token vale 10 minutos: dá pra assinar o lote e acabou


class ErroPSC(Exception):
    """O provedor recusou ou não respondeu (a mensagem vai pra tela)."""


def _conf(chave: str) -> tuple[str, str, str] | None:
    if chave not in PROVEDORES:
        return None
    k = chave.upper()
    url = (os.environ.get(f"PSC_{k}_URL") or PROVEDORES[chave][1]).rstrip("/")
    cid = os.environ.get(f"PSC_{k}_CLIENT_ID") or ""
    sec = os.environ.get(f"PSC_{k}_CLIENT_SECRET") or ""
    return (url, cid, sec) if url and cid and sec else None


def nome(chave: str | None) -> str:
    return PROVEDORES.get(chave or "", (chave or "",))[0]


def ligados() -> dict[str, str]:
    """Os provedores com credenciais no Render: {chave: nome}."""
    return {k: v[0] for k, v in PROVEDORES.items() if _conf(k)}


def pkce() -> tuple[str, str]:
    """(verifier, challenge S256)."""
    v = secrets.token_urlsafe(48)
    ch = base64.urlsafe_b64encode(hashlib.sha256(v.encode()).digest()).rstrip(b"=").decode()
    return v, ch


def url_autorizar(chave: str, volta: str, state: str, challenge: str) -> str | None:
    conf = _conf(chave)
    if not conf:
        return None
    from urllib.parse import urlencode
    url, cid, _s = conf
    return f"{url}/v0/oauth/authorize?" + urlencode({
        "response_type": "code", "client_id": cid, "redirect_uri": volta, "scope": "signature_session",
        "state": state, "code_challenge": challenge, "code_challenge_method": "S256", "lifetime": VALIDADE_S})


def _http():
    import httpx
    return httpx.Client(timeout=30)


def _chamar(fn):
    """A chamada ao provedor: sem resposta (rede, tempo) vira ErroPSC, com a mensagem."""
    import httpx
    try:
        return fn()
    except httpx.HTTPError as e:
        raise ErroPSC("o provedor não respondeu, tente de novo") from e


def _json(r) -> dict:
    try:
        j = r.json()
    except Exception:  # noqa: BLE001
        j = {}
    if r.status_code >= 400:
        raise ErroPSC(str(j.get("error_description") or j.get("message") or j.get("error")
                          or f"o provedor respondeu {r.status_code}")[:200])
    return j if isinstance(j, dict) else {}


def token(chave: str, code: str, volta: str, verifier: str) -> str:
    conf = _conf(chave)
    if not conf:
        raise ErroPSC("Este provedor não está ligado no Zaq.")
    url, cid, sec = conf
    with _http() as h:
        j = _json(_chamar(lambda: h.post(f"{url}/v0/oauth/token", data={
            "grant_type": "authorization_code", "client_id": cid, "client_secret": sec, "code": code,
            "redirect_uri": volta, "code_verifier": verifier})))
    t = j.get("access_token")
    if not t:
        raise ErroPSC("O provedor não autorizou.")
    return str(t)


def _der(txt: str) -> bytes:
    """O certificado vem em base64 (às vezes com o cabeçalho PEM)."""
    t = "".join(ln for ln in str(txt).splitlines() if "-----" not in ln)
    return base64.b64decode(t)


def certificados(chave: str, tok: str) -> list[tuple[str, bytes, list[bytes]]]:
    """[(alias, certificado DER, cadeia DER)] do profissional (quem escolhe é quem chama)."""
    url, _c, _s = _conf(chave) or ("", "", "")
    with _http() as h:
        j = _json(_chamar(lambda: h.get(f"{url}/v0/oauth/certificate-discovery",
                                        headers={"Authorization": f"Bearer {tok}"})))
    saida = []
    for c0 in j.get("certificates") or []:
        try:
            cadeia = [_der(x) for x in (c0.get("certificate_chain") or c0.get("chain") or []) if x]
            saida.append((str(c0.get("alias") or ""), _der(c0.get("certificate") or ""), cadeia))
        except (ValueError, TypeError):
            continue
    if not saida:
        raise ErroPSC("O provedor não mostrou nenhum certificado.")
    return saida


def assinar(chave: str, tok: str, alias: str, hashes: list[bytes]) -> list[bytes]:
    """A assinatura crua (PKCS#1 v1.5) de cada hash SHA-256, na mesma ordem."""
    url, _c, _s = _conf(chave) or ("", "", "")
    corpo = {"certificate_alias": alias, "hashes": [
        {"id": str(i), "alias": f"item-{i}", "hash": base64.b64encode(h).decode(),
         "hash_algorithm": SHA256_OID, "signature_format": "RAW"} for i, h in enumerate(hashes)]}
    with _http() as h:
        j = _json(_chamar(lambda: h.post(f"{url}/v0/oauth/signature", json=corpo,
                                         headers={"Authorization": f"Bearer {tok}"})))
    por_id = {str(s.get("id")): s.get("raw_signature") for s in (j.get("signatures") or [])}
    if any(not por_id.get(str(i)) for i in range(len(hashes))):
        raise ErroPSC("O provedor não devolveu todas as assinaturas.")
    return [base64.b64decode(por_id[str(i)]) for i in range(len(hashes))]
