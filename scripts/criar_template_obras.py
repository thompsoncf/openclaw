#!/usr/bin/env python3
"""Cria o TEMPLATE do lembrete de segunda das obras no Twilio (Content API) e pede
a aprovação do WhatsApp. Rode ONDE as credenciais Twilio existem (ex.: shell do
Render), nunca com credenciais no código.

    python scripts/criar_template_obras.py

Precisa das envs TWILIO_ACCOUNT_SID e TWILIO_AUTH_TOKEN.

No fim, imprime o Content SID (HX...). Coloque-o na env do serviço web:
    TWILIO_TMPL_OBRAS_SID=HXxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

PRA QUE SERVE: o lembrete de segunda (finance/obras_lembrete.py) sai como texto
livre só se o dono falou com o Zaq nas últimas 24 h. Na segunda de manhã isso
quase nunca é verdade — o dono da PX2, medido em 26/09/2026, tinha falado 5 dias
antes. Sem este template o lembrete cai no Telegram (que ele não tem) ou no
e-mail. Com ele, chega no WhatsApp; e quando o dono responde, a janela abre e a
conversa segue de graça.

Corpo (variáveis, NA ORDEM — batem com obras_lembrete._entregar):
    {{1}} = nome da empresa          (ex.: "PX2 Empreendimentos")
    {{2}} = as pendências, numa linha (ex.: "Casa 2: o que trava é habite-se. ...")
"""
import os
import sys

import httpx

FRIENDLY = "obras_pendencias_semana_ptbr"
LANG = "pt_BR"

# UTILITY: fala de obra e papel da própria empresa, sem oferta — é o que a Meta
# aprova como utilidade (e custa ~9x menos que marketing, ver finance/wa_precos.py).
# A variável não pode abrir nem fechar o corpo (regra da Meta), por isso o texto
# fixo antes e depois.
BODY = ("🏗️ Obras da {{1}} nesta semana: {{2}}\n\n"
        "Quando um papel sair ou uma parcela for paga, é só responder aqui que eu atualizo.")

TYPES = {"twilio/text": {"body": BODY}}


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
        # valores só de amostra pra Meta validar o formato do corpo
        "variables": {"1": "PX2 Empreendimentos",
                      "2": "Casa 2: o que trava é habite-se. Parado em casa: R$ 79.300,00."},
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
    payload = {"name": FRIENDLY, "category": "UTILITY"}
    r = httpx.post(
        f"https://content.twilio.com/v1/Content/{content_sid}/ApprovalRequests/whatsapp",
        json=payload, auth=auth, timeout=30)
    if r.status_code not in (200, 201):
        print(f"AVISO: não consegui pedir aprovação automática (HTTP {r.status_code}).\n"
              f"{r.text[:600]}\nVocê pode submeter pela Content Template Builder do Twilio.")
        return
    print("✓ Aprovação do WhatsApp solicitada (categoria UTILITY).")
    print("  Acompanhe o status no Twilio Console › Messaging › Content Template Builder.")


def main():
    auth = _auth()
    sid = criar_conteudo(auth)
    pedir_aprovacao(auth, sid)
    print("\n=========================================================")
    print("Coloque esta env no serviço web e reinicie:")
    print(f"    TWILIO_TMPL_OBRAS_SID={sid}")
    print("=========================================================")
    print("Depois que o WhatsApp APROVAR, o lembrete de segunda das obras passa a")
    print("chegar no WhatsApp mesmo sem o dono ter falado com o Zaq na véspera.")


if __name__ == "__main__":
    main()
