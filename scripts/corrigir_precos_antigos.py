"""Corrige RETROATIVAMENTE o preco unitario dos itens antigos (gravados antes da
defesa em 3 camadas). Onde valor_unitario x quantidade NAO bate com o total, o
unitario estava errado (ex: IA pos o total da picanha no lugar do preco/kg).
Recalcula unitario = total / quantidade e atualiza itens_lancamento E o banco de
ouro (precos_observados).

SEGURO: roda em modo --dry-run por padrao (so' MOSTRA o que mudaria, nao grava).
Pra aplicar de verdade, passar --aplicar.

Uso (no Render Shell):
    python -m scripts.corrigir_precos_antigos            # dry-run (so' mostra)
    python -m scripts.corrigir_precos_antigos --aplicar  # aplica de verdade
"""
from __future__ import annotations

import os
import sys

from psycopg_pool import ConnectionPool


def _pool() -> ConnectionPool:
    return ConnectionPool(os.environ["DATABASE_URL"], open=True)


def main():
    aplicar = "--aplicar" in sys.argv
    pool = _pool()
    # acha itens onde unitario x qtd diverge do total (tolerancia 2% ou 2 centavos)
    with pool.connection() as c:
        rows = c.execute(
            """select id, descricao, quantidade, valor_unitario_centavos,
                      valor_total_centavos
               from itens_lancamento
               where quantidade > 0 and valor_total_centavos > 0"""
        ).fetchall()

    corrigir = []
    for iid, desc, qtd, vu, vt in rows:
        qtd = float(qtd or 1) or 1.0
        vu = int(vu or 0)
        vt = int(vt or 0)
        esperado = vu * qtd
        tol = max(2, esperado * 0.02)
        if abs(esperado - vt) > tol:
            novo_vu = round(vt / qtd)
            if novo_vu != vu and novo_vu > 0:
                corrigir.append((iid, desc, qtd, vu, novo_vu, vt))

    print(f"Itens com unitario divergente: {len(corrigir)}")
    print("-" * 70)
    for iid, desc, qtd, vu, novo_vu, vt in corrigir[:50]:
        print(f"  #{iid} {desc[:30]:30} qtd={qtd:g} "
              f"unit {vu/100:.2f} -> {novo_vu/100:.2f} (total {vt/100:.2f})")
    if len(corrigir) > 50:
        print(f"  ... e mais {len(corrigir) - 50}")
    print("-" * 70)

    if not corrigir:
        print("Nada a corrigir. ✓")
        return
    if not aplicar:
        print(f"\n[DRY-RUN] {len(corrigir)} itens seriam corrigidos.")
        print("Pra aplicar de verdade: python -m scripts.corrigir_precos_antigos --aplicar")
        return

    # aplica: atualiza itens_lancamento E precos_observados (por item_id)
    with pool.connection() as c:
        for iid, desc, qtd, vu, novo_vu, vt in corrigir:
            c.execute("update itens_lancamento set valor_unitario_centavos=%s where id=%s",
                      (novo_vu, iid))
            c.execute("update precos_observados set valor_unitario_centavos=%s where item_id=%s",
                      (novo_vu, iid))
        c.commit()
    print(f"\n✓ {len(corrigir)} itens corrigidos (itens_lancamento + banco de ouro).")


if __name__ == "__main__":
    main()
