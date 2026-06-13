"""Permissoes por papel de membro - a fonte unica da verdade.

Papeis:
- dono:     tudo (financas, lista, gestao da conta).
- membro:   financas + lista (esposa, filhos). Nao gere a conta.
- restrito: SO' a lista de compras (empregada, governanta). Financas ficam
            invisiveis - nem no chat (o agente nem recebe as ferramentas
            financeiras), nem no portal (rotas financeiras bloqueadas).

A trava de privacidade do papel 'restrito' e' no CODIGO, nao na persona:
um membro restrito nunca recebe as ferramentas financeiras, entao o agente
literalmente nao tem como consultar saldo/lancamentos dele.
"""

PAPEIS = ("dono", "membro", "restrito")


def pode_financas(papel: str) -> bool:
    """Ve saldo, lancamentos, relatorios, registra gastos."""
    return papel in ("dono", "membro")


def pode_lista(papel: str) -> bool:
    """Usa a lista de compras (criar, ver, marcar)."""
    return papel in ("dono", "membro", "restrito")


def pode_gerir_conta(papel: str) -> bool:
    """Adiciona/remove membros, troca plano (gestao)."""
    return papel == "dono"


def rotulo(papel: str) -> str:
    return {"dono": "Dono", "membro": "Membro", "restrito": "Restrito (só lista)"}.get(papel, papel)
