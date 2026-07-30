#!/usr/bin/env python3
"""Cria o TEMPLATE de 1º contato da prospecção no Twilio (Content API) e pede a
aprovação do WhatsApp. Rode ONDE as credenciais Twilio existem (ex.: shell do
Render), nunca com credenciais no código.

    python scripts/criar_template_twilio.py

Precisa das envs TWILIO_ACCOUNT_SID e TWILIO_AUTH_TOKEN.

No fim, imprime o Content SID (HX...). Coloque-o na env do serviço web:
    TWILIO_TMPL_PROSPEC_SID=HXxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
Depois que o WhatsApp APROVAR (pode levar de minutos a horas), o botão
"Enviar convite (WhatsApp)" na ficha do lead passa a funcionar 'frio'.

Corpo aprovado (variáveis, NA ORDEM):
    {{1}} = empresa que envia (nome fantasia da conta)
    {{2}} = empresa do lead
"""
import os
import sys

import httpx

FRIENDLY = "prospec_1contato_ptbr"
LANG = "pt_BR"

# Corpo do template. Precisa FISGAR no 1º contato frio: dor concreta + benefício
# + CTA de baixo atrito ("1 minuto") + botão. Curto e sem promessa exagerada
# (ajuda na aprovação do WhatsApp).
BODY = ("Oi! 👋 Aqui é da {{1}}. A gente ajuda empresas como a {{2}} a organizar "
        "o atendimento e parar de perder cliente por demora na resposta. "
        "Posso te mostrar em 1 minuto como ficaria aí? 👇")

TYPES = {
    "twilio/quick-reply": {
        "body": BODY,
        "actions": [
            {"id": "interesse", "title": "Quero ver"},
            {"id": "agora_nao", "title": "Agora não"},
        ],
    },
    # fallback de texto puro (WhatsApp usa o quick-reply; o texto cobre outros canais)
    "twilio/text": {
        "body": BODY + "\n\nResponda *QUERO* que eu te mando na hora.",
    },
}


def _auth():
    sid = os.environ.get("TWILIO_ACCOUNT_SID")
    tok = os.environ.get("TWILIO_AUTH_TOKEN")
    if not (sid and tok):
        print("ERRO: defina TWILIO_ACCOUNT_SID e TWILIO_AUTH_TOKEN no ambiente.")
        sys.exit(1)
    return (sid, tok)


def criar_conteudo(auth) -> str:
    payload = {
        "friendly_name": FRIENDLY,
        "language": LANG,
        "variables": {"1": "Sua Empresa", "2": "Empresa do Lead"},
        "types": TYPES,
    }
    r = httpx.post("https://content.twilio.com/v1/Content",
                   json=payload, auth=auth, timeout=30)
    if r.status_code not in (200, 201):
        print(f"ERRO ao criar o conteúdo: HTTP {r.status_code}\n{r.text[:600]}")
        sys.exit(1)
    sid = r.json().get("sid")
    print(f"✓ Conteúdo criado: {sid}")
    return sid


def pedir_aprovacao(auth, content_sid: str) -> None:
    # categoria MARKETING (1º contato frio). O nome precisa ser único e minúsculo.
    payload = {"name": FRIENDLY, "category": "MARKETING"}
    r = httpx.post(
        f"https://content.twilio.com/v1/Content/{content_sid}/ApprovalRequests/whatsapp",
        json=payload, auth=auth, timeout=30)
    if r.status_code not in (200, 201):
        print(f"AVISO: não consegui pedir aprovação automática (HTTP {r.status_code}).\n"
              f"{r.text[:600]}\nVocê pode submeter pela Content Template Builder do Twilio.")
        return
    print("✓ Aprovação do WhatsApp solicitada (categoria MARKETING).")
    print("  Acompanhe o status no Twilio Console › Messaging › Content Template Builder.")


def main():
    auth = _auth()
    sid = criar_conteudo(auth)
    pedir_aprovacao(auth, sid)
    print("\n=========================================================")
    print("Coloque esta env no serviço web e reinicie:")
    print(f"    TWILIO_TMPL_PROSPEC_SID={sid}")
    print("=========================================================")


if __name__ == "__main__":
    main()
