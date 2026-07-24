"""Liga (ou desliga) o Módulo Doméstica pra uma conta — cortesia/piloto.

O gate do módulo é `conta_modulos` (não é automático por plano). Este script é o
atalho pra ativar numa conta de teste sem mexer no admin.

PRÉ-REQUISITO: a migração 094 já ter rodado (ela cadastra 'domestica' em
`modulos`, pra onde `conta_modulos.modulo` faz FK). O `db.aplicar_migracoes`
roda no deploy; se precisar à mão: `python -m db.aplicar_migracoes`.

Rodar no Render Shell:
    python -m scripts.liberar_domestica --email voce@exemplo.com
    python -m scripts.liberar_domestica --conta-id 42
    python -m scripts.liberar_domestica --email voce@exemplo.com --off   # desliga
"""
import argparse
import sys

from db.conexao import get_pool
from finance import domestica_repo as dom


def _achar_conta(pool, email: str | None, conta_id: int | None):
    with pool.connection() as c:
        if conta_id is not None:
            r = c.execute(
                "select id, nome, email from contas where id=%s",
                (conta_id,)).fetchone()
        else:
            r = c.execute(
                "select id, nome, email from contas where lower(email)=lower(%s) "
                "order by id limit 1", (email,)).fetchone()
    return r


def _tem_modulo_no_catalogo(pool) -> bool:
    with pool.connection() as c:
        return c.execute(
            "select 1 from modulos where codigo='domestica'").fetchone() is not None


def main() -> int:
    ap = argparse.ArgumentParser(description="Liga/desliga o Módulo Doméstica numa conta.")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--email", help="e-mail da conta")
    g.add_argument("--conta-id", type=int, help="id da conta")
    ap.add_argument("--off", action="store_true", help="desliga em vez de ligar")
    args = ap.parse_args()

    pool = get_pool()

    if not _tem_modulo_no_catalogo(pool):
        print("✗ 'domestica' não está em `modulos` — rode a migração 094 primeiro:\n"
              "    python -m db.aplicar_migracoes")
        return 1

    conta = _achar_conta(pool, args.email, args.conta_id)
    if not conta:
        alvo = args.email or f"id {args.conta_id}"
        print(f"✗ conta não encontrada: {alvo}")
        return 1

    conta_id, nome, email = conta
    dom.liberar_modulo(pool, conta_id, ativo=not args.off)
    estado = "DESLIGADO" if args.off else "LIGADO"
    print(f"✓ Módulo Doméstica {estado} pra conta #{conta_id} "
          f"({nome or '—'} · {email or 'sem e-mail'}).")
    if not args.off:
        print("  Recarregue o painel: a aba 'Doméstica' aparece no grupo Pessoal.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
