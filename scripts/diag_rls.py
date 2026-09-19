"""DIAGNÓSTICO SOMENTE-LEITURA do estado de RLS (Row Level Security).

NÃO ALTERA NADA. Só faz SELECT no catálogo do Postgres (pg_roles, pg_class,
pg_policies). Nenhum insert/update/delete/alter, nenhum commit de escrita.
Pode rodar em produção com segurança.

Existe pra responder UMA pergunta antes de mexer em RLS:
    o papel com que o app conecta está SUJEITO a RLS, ou a ignora?

Por que isso é o que importa: a migração 075 já ligou RLS em `prospeccao` e
`prospeccao_atividades`, e não existe NENHUMA policy no repositório. RLS ligada
sem policy = nega tudo. Como o painel de prospecção funciona, o papel do app
necessariamente ignora RLS (dono de tabela ou superusuário). Se for esse o caso,
ligar RLS em mais tabelas não protege NADA — passaria no teste por engano.

Uso:
    python -m scripts.diag_rls
"""
from __future__ import annotations

import os
import sys

from dotenv import load_dotenv

load_dotenv()

for _f in (sys.stdout, sys.stderr):
    try:
        _f.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def main() -> None:
    url = os.environ.get("DATABASE_URL")
    if not url:
        sys.exit("❌ DATABASE_URL não definida. (Rode com a do banco que quer diagnosticar.)")

    import psycopg

    # autocommit=True só pra não abrir transação de escrita; só rodamos SELECT.
    # connect_timeout: sem isso, banco inalcançável pendura o terminal calado.
    with psycopg.connect(url, autocommit=True, connect_timeout=15) as c:
        quem = c.execute(
            "select current_user, session_user, current_database()").fetchone()
        print(f"\n┌─ CONEXÃO")
        print(f"│  current_user .... {quem[0]}")
        print(f"│  session_user .... {quem[1]}")
        print(f"│  database ........ {quem[2]}")

        papel = c.execute(
            """select rolsuper, rolbypassrls
                 from pg_roles where rolname = current_user""").fetchone()
        supera, bypassa = (papel or (False, False))
        print(f"\n┌─ O PAPEL IGNORA RLS?")
        print(f"│  superusuário .... {supera}")
        print(f"│  BYPASSRLS ....... {bypassa}")

        # dono das tabelas: dono ignora RLS a menos que exista FORCE
        donas = c.execute(
            """select count(*) from pg_class cl
                 join pg_namespace n on n.oid = cl.relnamespace
                where n.nspname='public' and cl.relkind='r'
                  and pg_get_userbyid(cl.relowner) = current_user""").fetchone()[0]
        total = c.execute(
            """select count(*) from pg_class cl
                 join pg_namespace n on n.oid = cl.relnamespace
                where n.nspname='public' and cl.relkind='r'""").fetchone()[0]
        print(f"│  é DONO de ....... {donas} de {total} tabelas do schema public")

        # RLS ligada / forçada, e policies existentes
        rls = c.execute(
            """select cl.relname, cl.relrowsecurity, cl.relforcerowsecurity
                 from pg_class cl
                 join pg_namespace n on n.oid = cl.relnamespace
                where n.nspname='public' and cl.relkind='r' and cl.relrowsecurity
                order by cl.relname""").fetchall()
        pols = c.execute(
            "select schemaname, tablename, policyname from pg_policies "
            "where schemaname='public' order by tablename").fetchall()

        print(f"\n┌─ TABELAS COM RLS LIGADA ({len(rls)})")
        for nome, ligada, forcada in rls:
            marca = "FORÇADA" if forcada else "não forçada (dono fura)"
            print(f"│  {nome:35s} {marca}")
        if not rls:
            print("│  (nenhuma)")

        print(f"\n┌─ POLICIES EXISTENTES ({len(pols)})")
        for _s, tab, pol in pols:
            print(f"│  {tab:35s} {pol}")
        if not pols:
            print("│  (nenhuma)")

        # ---------------------------------------------------------- veredito
        print("\n" + "═" * 62)
        ignora = bool(supera or bypassa or donas > 0)
        if ignora:
            motivo = ("superusuário" if supera else
                      "BYPASSRLS" if bypassa else "dono das tabelas")
            print(f"VEREDITO: o app IGNORA RLS ({motivo}).")
            print("→ Ligar RLS em mais tabelas NÃO protegeria nada hoje: o teste")
            print("  passaria por engano. O pré-requisito é conectar com um papel")
            print("  SEM bypass, ou usar FORCE ROW LEVEL SECURITY.")
            perigosas = [n for n, _l, f in rls if not f]
            if perigosas:
                print(f"\n⚠  {len(perigosas)} tabela(s) com RLS ligada e SEM policy só")
                print("   funcionam porque o app fura a RLS. No dia em que o papel")
                print("   mudar, elas passam a devolver ZERO linhas:")
                for n in perigosas[:8]:
                    print(f"     - {n}")
        else:
            print("VEREDITO: o app ESTÁ sujeito a RLS.")
            print("→ Cuidado: tabela com RLS ligada e sem policy devolve ZERO linhas.")
        print("═" * 62)
        print("\n(diagnóstico somente-leitura — nada foi alterado)\n")


if __name__ == "__main__":
    main()
