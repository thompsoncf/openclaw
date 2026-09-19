"""Prova o PASSO A contra o banco REAL: o app.conta_id gruda na conexão?

NÃO ALTERA DADO NENHUM. Só usa set_config/current_setting (variável de sessão) e
SELECTs de catálogo. Nenhum insert/update/delete/alter. Seguro em produção.

Responde a pergunta que não dá pra deduzir do código: se a DATABASE_URL aponta
pro pooler do Supabase em modo TRANSAÇÃO (porta 6543), a conexão de servidor
troca entre transações e um SET de SESSÃO não gruda — aí o Passo B precisaria de
escopo de transação em vez de sessão. Na porta 5432 (direta) gruda.

Uso:
    RLS_SET_CONTA=1 python -m scripts.diag_tenant
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
    if not os.environ.get("DATABASE_URL"):
        sys.exit("❌ DATABASE_URL não definida.")
    os.environ["RLS_SET_CONTA"] = "1"          # o teste precisa da flag ligada

    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from db import tenant as t
    from db.conexao import get_pool

    url = os.environ["DATABASE_URL"]
    porta = url.rsplit(":", 1)[-1].split("/")[0] if ":" in url else "?"
    print(f"\n┌─ CONEXÃO\n│  porta ........... {porta}"
          f"  {'(pooler de transação — atenção)' if porta == '6543' else '(direta/sessão)'}")

    pool = get_pool()

    # 1) o pool anuncia a conta que está no ContextVar?
    t.CONTA_ATUAL.set(4242)
    with pool.connection() as c:
        lido = c.execute("select current_setting('app.conta_id', true)").fetchone()[0]
    print(f"\n┌─ 1) ANUNCIA A CONTA?\n│  esperado 4242 · lido {lido!r}")
    ok1 = lido == "4242"

    # 2) GRUDA entre statements/transações na MESMA conexão? (o teste do pooler)
    t.CONTA_ATUAL.set(777)
    with pool.connection() as c:
        antes = c.execute("select current_setting('app.conta_id', true)").fetchone()[0]
        c.commit()                     # fecha a transação: aqui o pooler pode trocar
        depois = c.execute("select current_setting('app.conta_id', true)").fetchone()[0]
    print(f"\n┌─ 2) SOBREVIVE AO COMMIT?\n│  antes {antes!r} · depois {depois!r}")
    ok2 = antes == "777" and depois == "777"

    # 3) conexão reaproveitada NÃO herda o tenant anterior (o vazamento a evitar)
    t.CONTA_ATUAL.set(None)
    with pool.connection() as c:
        limpo = c.execute("select current_setting('app.conta_id', true)").fetchone()[0]
    print(f"\n┌─ 3) NÃO VAZA PRA PRÓXIMA REQUISIÇÃO?\n│  esperado vazio · lido {limpo!r}")
    ok3 = not limpo

    print("\n" + "═" * 62)
    if ok1 and ok2 and ok3:
        print("PASSO A FUNCIONA. Escopo de SESSÃO gruda nesta conexão.")
        print("→ O Passo B (policies) pode usar current_setting('app.conta_id').")
    elif ok1 and ok3 and not ok2:
        print("PARCIAL: anuncia, mas NÃO sobrevive ao commit.")
        print("→ É o pooler em modo transação. O Passo B exigiria SET LOCAL por")
        print("  transação — mudança bem maior. Precisamos saber ANTES de criar policy.")
    else:
        print(f"FALHOU (1={ok1} 2={ok2} 3={ok3}). Não avance pro Passo B.")
    print("═" * 62)
    print("\n(nenhum dado foi alterado)\n")


if __name__ == "__main__":
    main()
