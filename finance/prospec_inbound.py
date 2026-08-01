"""Botões do template de 1º contato da prospecção (quick reply do WhatsApp).

O lead recebe o convite frio com 3 botões — "Quero te conhecer", "Quero o
material" e "Agora não". Quando toca em um deles, o texto do botão volta pra gente
no webhook. Aqui a gente CLASSIFICA o clique e MONTA a resposta automática
(Instagram / material), pra não depender da IA adivinhar. A orquestração (achar o
lead, esquentar, gravar conversa, enviar) fica no webhook, que já tem os helpers.

Os títulos dos botões estão em scripts/criar_template_twilio.py — mantenha os
termos casáveis aqui (`conhec`, `material`, `agora n`) em sincronia com eles.
"""
from __future__ import annotations

import re


def classificar(texto: str) -> str | None:
    """Devolve 'conhecer' | 'material' | 'nao' | None.

    Casa com os títulos dos botões do template e com alguns termos livres
    equivalentes (o lead pode digitar em vez de tocar). Retorna None quando não é
    um aceite/recusa reconhecível — aí o fluxo normal (IA) segue."""
    t = (texto or "").strip().lower()
    if not t:
        return None
    # títulos dos botões (match por trecho estável do rótulo)
    if "conhec" in t or "seu perfil" in t or "ver perfil" in t:
        return "conhecer"
    if "material" in t:
        return "material"
    if t.startswith("agora n") or t in ("não", "nao", "não obrigado", "nao obrigado"):
        return "nao"
    # termos livres de aceite (digitados)
    if re.search(r"\b(quero|sim|aceito|topo|bora|manda|pode mandar|claro|quero sim)\b", t):
        return "material"
    if re.search(r"\b(depois|agora nao|agora não|para|parar|sair|remover)\b", t):
        return "nao"
    return None


def normalizar_instagram(v: str) -> str:
    """Aceita '@perfil', 'perfil' ou uma URL e devolve uma URL clicável (ou '')."""
    v = (v or "").strip()
    if not v:
        return ""
    if v.startswith("http://") or v.startswith("https://"):
        return v
    return "https://www.instagram.com/" + v.lstrip("@").strip("/")


def resposta(tipo: str, idn: dict, material: str = "") -> str:
    """Texto da resposta automática ao clique. `idn` é o _conta_identidade
    (usa `instagram`). `material` é a URL do material da campanha (pode ser '')."""
    ig = normalizar_instagram((idn or {}).get("instagram", ""))
    material = (material or "").strip()
    if tipo == "conhecer":
        if ig:
            return ("Show! 😊 Esse é o meu Instagram: " + ig +
                    "\n\nDá uma olhada no que a gente faz por lá — e quando quiser, eu "
                    "te mando aquele material rápido, sem compromisso. Tô por aqui! 🙌")
        return ("Show! 😊 Me conta: prefere que eu te mande o material agora ou trocar "
                "uma ideia rápida por aqui? Tô à disposição! 🙌")
    if tipo == "material":
        if material:
            extra = (" — e, se quiser me conhecer melhor, meu Instagram é " + ig) if ig else ""
            return ("Perfeito! 📎 Segue o material pra você dar uma olhada com calma:\n" +
                    material + "\n\nQualquer dúvida é só me chamar aqui" + extra + ". 🙌")
        extra = (" Se quiser me conhecer melhor, meu Instagram é " + ig + ".") if ig else ""
        return ("Perfeito! 📎 Já já te mando o material — e qualquer dúvida, é só me "
                "chamar por aqui." + extra + " 🙌")
    # 'nao' — encerra educado, sem queimar o contato
    return ("Sem problema nenhum! 🙂 Fico à disposição — se mudar de ideia, é só me "
            "chamar por aqui. Sucesso! 👊")
