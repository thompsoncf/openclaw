"""Convite de 1º contato da PROSPECÇÃO por WhatsApp, via TEMPLATE aprovado (Twilio).

O texto livre só funciona dentro da janela de 24h. Pra falar 'frio' com um lead
que nunca respondeu, o WhatsApp exige um TEMPLATE aprovado. Aqui a gente dispara
esse template pelo número da empresa (canais_config, provedor Twilio).

Config: TWILIO_TMPL_PROSPEC_SID = o 'HX...' do template já aprovado no Twilio
(criado por scripts/criar_template_twilio.py). Enquanto não existir, a UI só
mostra o envio de texto (dentro da janela) e o wa.me externo.

Corpo aprovado (variáveis, NA ORDEM):
  {{1}} = nome de quem envia (responsável da conta)
  {{2}} = cargo de quem envia (ex.: CEO)
  {{3}} = nome da empresa que envia (nome fantasia da conta)
  {{4}} = nome da empresa do lead
"""
from __future__ import annotations

import os


def sid_template() -> str:
    """SID de fallback (env global). Usado pelo convite 1-a-1 da ficha e por
    campanhas que não têm um template próprio setado."""
    return (os.environ.get("TWILIO_TMPL_PROSPEC_SID") or "").strip()


def sid_efetivo(camp_sid: str | None = None) -> str:
    """O template que vale de verdade: o da campanha (se setado) ou o da env."""
    return (camp_sid or "").strip() or sid_template()


def template_configurado(camp_sid: str | None = None) -> bool:
    """True quando dá pra disparar o convite frio sozinho: template aprovado
    (SID da campanha OU da env) + credenciais Twilio presentes. O número é o da
    empresa (resolvido no envio)."""
    from . import whatsapp_twilio as wa
    return bool(sid_efetivo(camp_sid) and wa.configurado())


def enviar_convite(pool, conta_id: int, alvo_id: int, numero: str | None = None) -> dict:
    """Dispara o template de 1º contato pro número do lead, PELO NÚMERO DA EMPRESA
    (Twilio). Funciona fora da janela de 24h. Retorno tolerante: {'ok': bool, ...}.

    `numero`: número escolhido no seletor da ficha; se vazio, usa o WhatsApp/telefone
    do lead."""
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
        numero = ((numero or "").strip() or wa_num or tel or "").strip()
        if not numero:
            return {"ok": False, "erro": "sem_numero"}
        idn = _conta_identidade(c, conta_id)
        variaveis = {"1": (idn.get("responsavel") or idn.get("empresa") or "nós"),
                     "2": (idn.get("cargo") or "CEO"),
                     "3": (idn.get("empresa") or "nós"),
                     "4": (empresa_lead or "sua empresa")}
        return wout.enviar_template(c, conta_id, numero, sid, variaveis)
