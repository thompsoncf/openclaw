#!/usr/bin/env python3
"""Cria o TEMPLATE de LEMBRETE de compromisso no Twilio (Content API) e pede a
aprovação do WhatsApp. Rode ONDE as credenciais Twilio existem (ex.: shell do
Render), nunca com credenciais no código.

    python scripts/criar_template_lembrete.py

Precisa das envs TWILIO_ACCOUNT_SID e TWILIO_AUTH_TOKEN.

No fim, imprime o Content SID (HX...). Coloque-o na env do serviço web:
    TWILIO_TMPL_LEMBRETE_SID=HXxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx

PRA QUE SERVE: o "aviso antes da reunião" (agenda) só sai como texto livre
enquanto a janela de 24h do WhatsApp estiver aberta — e ela conta das 24h desde
a ÚLTIMA MENSAGEM do convidado. Como o convite normalmente vai dias antes, na
hora do lembrete (30 min antes) a janela quase sempre já expirou; sem este
template, o convidado não recebe nada. Quem confirma pela página pública
(/convite/<token>) nunca abre janela nenhuma — pra esses, o template é o único
caminho. (No provedor 'qr' isso não se aplica: lá não existe janela nem
template, o aviso sai livre sempre.)

Corpo aprovado (variáveis, NA ORDEM — batem com finance/convites.py:
`variaveis = {"1": titulo, "2": hora, "3": str(faltam_min)}`):
    {{1}} = título do compromisso   (ex.: "Reunião Consultoria Dadan")
    {{2}} = horário HH:MM           (ex.: "09:30")
    {{3}} = minutos que faltam      (ex.: "30")
"""
import os
import sys

import httpx

FRIENDLY = "lembrete_compromisso_ptbr"
LANG = "pt_BR"

# Corpo do lembrete. Transacional puro: fala de um compromisso que a própria
# pessoa confirmou, sem oferta e sem link — é o que caracteriza UTILITY pra Meta
# (aprova mais fácil e custa ~9x menos que marketing: R$ 0,035 vs R$ 0,3217, ver
# finance/wa_precos.py). Espelha o texto livre de convites.texto_lembrete_convidado
# pra quem recebe pelos dois caminhos ver a mesma coisa. A saída ("é só responder
# aqui") reforça o caráter transacional e abre a janela de 24h se a pessoa
# responder — aí a conversa segue de graça.
BODY = ("⏰ Lembrete: seu compromisso *{{1}}* começa às {{2}} — daqui a ~{{3}} "
        "minutos. Se precisar remarcar, é só responder aqui.")

TYPES = {
    # texto puro: lembrete não precisa de botão (quem quiser responder, responde
    # livre — e a resposta abre a janela de 24h).
    "twilio/text": {"body": BODY},
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
        # valores só de amostra pra Meta validar o formato do corpo
        "variables": {"1": "Reunião de alinhamento", "2": "09:30", "3": "30"},
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
    # categoria UTILITY: lembrete de um compromisso que a pessoa já confirmou —
    # não é promoção. Além de aprovar mais fácil, é ~9x mais barato que MARKETING.
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
    print(f"    TWILIO_TMPL_LEMBRETE_SID={sid}")
    print("=========================================================")
    print("Depois que o WhatsApp APROVAR, o aviso antes da reunião passa a sair")
    print("também pra quem confirmou pela página pública ou confirmou há mais de 24h.")


if __name__ == "__main__":
    main()
