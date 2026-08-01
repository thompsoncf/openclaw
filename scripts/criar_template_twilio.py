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
    {{1}} = nome de quem envia (responsável da conta)
    {{2}} = cargo de quem envia (ex.: CEO)
    {{3}} = empresa que envia (nome fantasia da conta)
    {{4}} = empresa do lead
"""
import os
import sys

import httpx

FRIENDLY = "prospec_1contato_ptbr"
LANG = "pt_BR"

# Corpo do template. Pegada de APRESENTAÇÃO + REFERÊNCIA: quem envia se apresenta
# (nome/cargo/empresa) e convida o lead a conhecê-lo ANTES de qualquer oferta —
# "quem me conhece antes, engaja melhor". Os 3 botões são de resposta rápida, então
# QUALQUER clique volta pra gente (esquenta o lead): "Quero te conhecer" recebe o
# Instagram, "Quero o material" recebe o material, "Agora não" encerra sem queimar.
# Curto e sem promessa exagerada (ajuda na aprovação do WhatsApp). Sem link no
# corpo (o Instagram/material vão na resposta ao clique).
BODY = ("Oi! 👋 Aqui é o {{1}}, {{2}} da {{3}} — a gente usa tecnologia e IA pra "
        "ajudar negócios como a {{4}} a atender e vender melhor, sem complicar. "
        "Antes de qualquer coisa, prefiro que você me conheça. Como quer começar? 👇")

TYPES = {
    "twilio/quick-reply": {
        "body": BODY,
        # títulos sem emoji e ≤ 20 chars (limite do WhatsApp/Twilio p/ quick-reply)
        "actions": [
            {"id": "conhecer", "title": "Quero te conhecer"},
            {"id": "material", "title": "Quero o material"},
            {"id": "agora_nao", "title": "Agora não"},
        ],
    },
    # fallback de texto puro (WhatsApp usa o quick-reply; o texto cobre outros canais)
    "twilio/text": {
        "body": BODY + "\n\nResponda *CONHECER* pra ver meu perfil ou *MATERIAL* que eu te envio.",
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
        "variables": {"1": "Thompson", "2": "CEO", "3": "Sua Empresa",
                      "4": "Empresa do Lead"},
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
