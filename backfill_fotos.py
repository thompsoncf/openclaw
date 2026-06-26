#!/usr/bin/env python3
"""Backfill: sugere foto pra produtos já cadastrados sem foto.

Uso:
  python backfill_fotos.py

Rodar no Render Shell ou local. Sugere foto pra cada produto pelo nome.
Produtos sem match (foto_url fica None) caem no ícone da categoria na vitrine.
"""
from finance import galeria_fotos
from db.conexao import get_pool

pool = get_pool()
contagem = 0

with pool.connection() as c:
    prods = c.execute(
        "select id, nome from catalogo_produtos where foto_url is null and ativo order by id"
    ).fetchall()

    print(f"Encontrados {len(prods)} produtos sem foto.")

    for pid, nome in prods:
        url = galeria_fotos.sugerir_foto(nome)
        if url:
            c.execute(
                "update catalogo_produtos set foto_url=%s where id=%s",
                (url, pid)
            )
            print(f"  ✓ {nome:30} → sugerida")
            contagem += 1
        else:
            print(f"  - {nome:30} → sem match (ícone da categoria)")

    c.commit()

print(f"\nBackfill completo! {contagem} produtos com foto sugerida.")
