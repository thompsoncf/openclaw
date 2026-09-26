"""As duas perguntas do plano de tratamento que NÃO têm conta pra filtrar.

1. De quem é este token? A página /plano/{token} é aberta pelo paciente, sem login:
   o token (secrets.token_urlsafe, único na 379) É a chave. Resolvido o dono, todo o
   resto (finance/clinica_planos.py) roda com `conta_id` como sempre.
2. Que contas têm plano na rua? O poller passa por elas uma a uma.

Ficam aqui, isoladas, pra que o teste de escopo (tests/test_escopo_conta.py) aceite
estas duas e continue cobrando `conta_id` de todo o resto de clinica_planos.py.
"""
from __future__ import annotations


def dono_do_token(c, token: str) -> tuple[int, int] | None:
    """(plano_id, conta_id) do token, ou None."""
    if not token or len(token) > 64:
        return None
    r = c.execute("select id, conta_id from clinica_planos where token=%s", (token,)).fetchone()
    return (r[0], r[1]) if r else None


def contas_com_plano_enviado(c) -> list[int]:
    return [r[0] for r in c.execute(
        "select distinct conta_id from clinica_planos where status='enviado'").fetchall()]
