"""Tarifas do WhatsApp Business Platform (Meta) — custo por mensagem de template.

A Meta cobra por mensagem de template ENTREGUE; texto livre dentro da janela de 24h é
grátis. O preço varia pela CATEGORIA (marketing/utility/authentication/service) e pelo
país de quem RECEBE. O webhook de status traz `pricing.category` e `pricing.billable`,
mas NÃO traz o valor — o valor sai desta tabela.

Fonte: tabela oficial da Meta para o Brasil, em BRL, vigente 1º/jul/2026
(developers.facebook.com/docs/whatsapp/pricing). Como o público do ZAQ é Brasil,
trabalhamos com a tarifa BR; categoria desconhecida cai em marketing (conservador).
"""
from __future__ import annotations

# R$ por mensagem de template entregue (Brasil).
TARIFA_BRL = {
    "marketing": 0.3217,
    "utility": 0.0350,
    "authentication": 0.0350,
    "service": 0.0,          # atendimento/texto livre na janela = grátis
}


def custo_brl(categoria: str | None, cobravel: bool = True) -> float:
    """Custo em R$ de uma mensagem, pela categoria. `cobravel=False` (ex.: janela de
    ponto de entrada gratuito / dentro da janela) zera o valor. Categoria vazia ou
    desconhecida é tratada como marketing (o caso mais caro — não subestima o custo)."""
    if cobravel is False:
        return 0.0
    cat = (categoria or "").strip().lower()
    return TARIFA_BRL.get(cat, TARIFA_BRL["marketing"])
