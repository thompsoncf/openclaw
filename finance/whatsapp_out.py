"""Dispatcher de SAÍDA do WhatsApp por conta: escolhe Twilio ou Cloud API.

Cada empresa configura o WhatsApp em canais_config (canal='whatsapp') com um
`provedor`:
  * 'twilio' (padrão) → número da empresa em `identificador`, credenciais no env.
  * 'cloud'  → número PRÓPRIO na Cloud API da Meta: `wa_phone_id` + `token` (por conta).
  * 'qr'     → sessão tipo WhatsApp Web (serviço à parte; ainda não implementado aqui).

Recebe um cursor já aberto (`c`) porque quem chama (painel/agente) já está numa
transação. Sempre best-effort: retorna {"ok":False,"erro":...} em vez de estourar.
"""
from __future__ import annotations

from finance import whatsapp_cloud as _cloud
from finance import whatsapp_twilio as _twilio


def _row(c, conta_id):
    return c.execute(
        """select coalesce(provedor,'twilio'), identificador, wa_phone_id, token
             from canais_config where conta_id=%s and canal='whatsapp' and ativo""",
        (conta_id,)).fetchone()


def configurado_conta(c, conta_id) -> bool:
    """A empresa consegue ENVIAR WhatsApp agora? (provedor + credenciais prontos)."""
    r = _row(c, conta_id)
    if not r:
        return False
    prov = r[0]
    if prov == "cloud":
        return _cloud.configurado(r[2], r[3])
    if prov == "qr":
        return False        # serviço externo (futuro)
    return _twilio.configurado() and bool(r[1])


def enviar(c, conta_id, numero, texto) -> dict:
    """Manda um texto pro `numero` do lead pelo provedor configurado da empresa."""
    r = _row(c, conta_id)
    if not r:
        return {"ok": False, "erro": "sem_numero_empresa"}
    prov = r[0]
    if prov == "cloud":
        return _cloud.enviar_texto(r[2], r[3], numero, texto)
    if prov == "qr":
        return {"ok": False, "erro": "qr_indisponivel"}
    return _twilio.enviar_texto(r[1], numero, texto)
