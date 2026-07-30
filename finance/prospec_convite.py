"""Convite de 1º contato da PROSPECÇÃO por WhatsApp, via TEMPLATE aprovado (Twilio).

O texto livre só funciona dentro da janela de 24h. Pra falar 'frio' com um lead
que nunca respondeu, o WhatsApp exige um TEMPLATE aprovado. Aqui a gente dispara
esse template pelo número da empresa (canais_config, provedor Twilio).

Config: TWILIO_TMPL_PROSPEC_SID = o 'HX...' do template já aprovado no Twilio
(criado por scripts/criar_template_twilio.py). Enquanto não existir, a UI só
mostra o envio de texto (dentro da janela) e o wa.me externo.

Corpo aprovado (variáveis, NA ORDEM):
  {{1}} = nome da empresa que envia (nome fantasia da conta)
  {{2}} = nome da empresa do lead
"""
from __future__ import annotations

import os


def sid_template() -> str:
    return (os.environ.get("TWILIO_TMPL_PROSPEC_SID") or "").strip()


def template_configurado() -> bool:
    """True quando dá pra disparar o convite frio sozinho: template aprovado (SID
    na env) + credenciais Twilio presentes. O número é o da empresa (resolvido no
    envio)."""
    from . import whatsapp_twilio as wa
    return bool(sid_template() and wa.configurado())


def enviar_convite(pool, conta_id: int, alvo_id: int) -> dict:
    """Dispara o template de 1º contato pro número do lead, PELO NÚMERO DA EMPRESA
    (Twilio). Funciona fora da janela de 24h. Retorno tolerante: {'ok': bool, ...}."""
    sid = sid_template()
    if not sid:
        return {"ok": False, "erro": "sem_template"}
    from . import whatsapp_out as wout
    from .campanhas_motor import _conta_identidade
    with pool.connection() as c:
        row = c.execute(
            "select empresa, whatsapp, telefone from prospeccao where id=%s and conta_id=%s",
            (alvo_id, conta_id)).fetchone()
        if not row:
            return {"ok": False, "erro": "lead_nao_encontrado"}
        empresa_lead, wa_num, tel = row
        numero = (wa_num or tel or "").strip()
        if not numero:
            return {"ok": False, "erro": "sem_numero"}
        idn = _conta_identidade(c, conta_id)
        variaveis = {"1": (idn.get("empresa") or "nós"),
                     "2": (empresa_lead or "sua empresa")}
        return wout.enviar_template(c, conta_id, numero, sid, variaveis)
