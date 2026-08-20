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


def provedor_da_conta(c, conta_id) -> str:
    """'twilio' | 'cloud' | 'qr' — ou '' se a empresa não tem canal ativo.

    Quem chama precisa disso pra saber se as regras da API OFICIAL do WhatsApp
    (janela de 24h + template aprovado) valem: elas são da Business API
    (twilio/cloud). No 'qr' é uma sessão tipo WhatsApp Web — texto livre pra
    qualquer número, sempre, sem template."""
    r = _row(c, conta_id)
    return r[0] if r else ""


def configurado_conta(c, conta_id) -> bool:
    """A empresa consegue ENVIAR WhatsApp agora? (provedor + credenciais prontos)."""
    r = _row(c, conta_id)
    if not r:
        return False
    prov = r[0]
    if prov == "cloud":
        return _cloud.configurado(r[2], r[3])
    if prov == "qr":
        from finance import whatsapp_qr as _qr
        return _qr.configurado()   # serviço ligado; a sessão em si é checada no envio
    return _twilio.configurado() and bool(r[1])


def chip_da_conversa(c, conta_id, conversa_id) -> int | None:
    """Por qual chip esta conversa entrou — ou None (= o chip da própria empresa).

    Só vale no provedor 'qr': Twilio e Cloud API têm um número por conta, e ali a
    pergunta não existe. Confere `conta_id` de propósito: sem isso um id de conversa
    de outra empresa faria a resposta sair por um chip que não é desta.
    """
    if not conversa_id:
        return None
    r = c.execute("select chip_id from conversas where id=%s and conta_id=%s",
                  (conversa_id, conta_id)).fetchone()
    return int(r[0]) if (r and r[0]) else None


def enviar(c, conta_id, numero, texto, *, chip_id=None) -> dict:
    """Manda um texto pro `numero` do lead pelo provedor configurado da empresa.

    `chip_id` só é lido no provedor 'qr' e responde "por qual número isto sai".
    Nulo — que é o padrão e o caso de toda empresa de um chip só — sai pelo chip da
    própria empresa, exatamente como sempre saiu. Quem responde a uma CONVERSA
    deveria passar o chip dela (ver `chip_da_conversa`): sem isso o lead escreve pra
    um número e é respondido por outro, que do lado dele parece outra empresa.
    """
    r = _row(c, conta_id)
    if not r:
        return {"ok": False, "erro": "sem_numero_empresa"}
    prov = r[0]
    if prov == "cloud":
        return _cloud.enviar_texto(r[2], r[3], numero, texto)
    if prov == "qr":
        from finance import whatsapp_qr as _qr
        return _qr.enviar_texto(chip_id or conta_id, numero, texto)
    return _twilio.enviar_texto(r[1], numero, texto)


def enviar_template(c, conta_id, numero, content_sid, variaveis, mmlite=False) -> dict:
    """Dispara um TEMPLATE aprovado (fora da janela de 24h) pelo número da empresa.
    Twilio: `content_sid` é o Content SID (HX...). Cloud API (número próprio): o mesmo
    campo carrega o NOME do template aprovado na Meta. `mmlite=True` (só no provedor
    cloud) roteia pela Marketing Messages Lite API — mesmo preço, entrega otimizada."""
    r = _row(c, conta_id)
    prov = r[0] if r else "twilio"
    if prov == "cloud":
        # no Cloud API o "content_sid" é o NOME do template aprovado na Meta
        return _cloud.enviar_template(r[2], r[3], numero, content_sid, variaveis,
                                      mmlite=mmlite)
    if prov != "twilio":
        return {"ok": False, "erro": "provedor_sem_template"}
    # nunca cai no número global do env: senão a campanha de uma conta sem WhatsApp
    # próprio sairia pelo número (e identidade) de outra empresa conectada via Twilio.
    remetente = r[1] if r and r[1] else ""
    if not remetente:
        return {"ok": False, "erro": "sem_numero_empresa"}
    return _twilio.enviar_template(_twilio.normalizar_from(remetente), numero,
                                   content_sid, variaveis)
