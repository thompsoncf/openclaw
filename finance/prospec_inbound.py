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
    # A RECUSA VEM PRIMEIRO. "não quero" contém "quero", e a ordem antiga (aceite
    # antes) classificava isso como pedido de material — o Zaq respondia mandando o
    # material justamente pra quem tinha acabado de recusar, e ainda esquentava o
    # lead. Vale pra "não quero mais receber", "nao quero nada" e companhia.
    if _RECUSA.search(t):
        return "nao"
    # títulos dos botões (match por trecho estável do rótulo)
    if "conhec" in t or "seu perfil" in t or "ver perfil" in t:
        return "conhecer"
    if "material" in t:
        return "material"
    # termos livres de aceite (digitados)
    if re.search(r"\b(quero|sim|aceito|topo|bora|manda|pode mandar|claro|quero sim)\b", t):
        return "material"
    return None


# Recusa explícita. Duas famílias: a negação diante de um verbo de interesse
# ("não quero", "não tenho interesse", "sem interesse") e os pedidos de parada
# ("pare", "sair", "remover", "descadastrar"). Antes o "agora não" era casado por
# prefixo e o resto ficava de fora; um "não quero mais receber" passava batido.
_RECUSA = re.compile(
    r"\b(?:n[ãa]o|nem|sem)\s+(?:quero|queria|tenho|tenh[oa]|desejo|preciso|"
    r"interesse|obrigad[oa])\b"
    r"|\bagora\s+n[ãa]o\b"
    r"|\bsem\s+interesse\b"
    r"|\b(?:pare|parar|para\s+de|sair|remov\w*|descadastr\w*|cancel\w*)\b"
    r"|^n[ãa]o(?:\s+obrigad[oa])?$"
    r"|\bdepois\b")


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
