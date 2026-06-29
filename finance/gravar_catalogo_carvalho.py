"""finance/gravar_catalogo_carvalho.py

Grava os produtos coletados do catálogo Carvalho no banco de preços.
Uso: python -m finance.gravar_catalogo_carvalho

Lê /tmp/catalogo_carvalho.json (gerado por catalogo_carvalho.py),
normaliza as descrições e insere em precos_observados com fonte='catalogo'.
"""
from __future__ import annotations

import json
import os
from datetime import date

import psycopg2.pool

from finance.banco_precos import normalizar


def gravar_catalogo(arquivo: str = "/tmp/catalogo_carvalho.json") -> None:
    """Lê JSON do catálogo e grava em precos_observados com fonte='catalogo'."""
    db_url = os.environ.get("DATABASE_URL")
    if not db_url:
        print("ERRO: DATABASE_URL não configurada")
        return

    with open(arquivo) as f:
        registros = json.load(f)
    print(f"Carregados {len(registros)} registros do catálogo")

    pool = psycopg2.pool.SimpleConnectionPool(1, 10, db_url)
    hoje = date.today()
    inseridos = 0

    try:
        with pool.getconn() as conn:
            with conn.cursor() as cur:
                for reg in registros:
                    desc = reg.get("descricao", "")
                    desc_norm = normalizar(desc)
                    if not desc_norm:
                        continue

                    # Insere ou atualiza (evita duplicatas)
                    cur.execute(
                        """
                        insert into precos_observados (
                            rede, mercado, descricao_original, descricao_norm,
                            valor_unitario_centavos, unidade, data_compra,
                            gtin, sku, fonte, loja_id
                        ) values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                        on conflict (rede, mercado, descricao_norm, data_compra, fonte)
                        do nothing
                        """,
                        (
                            reg.get("rede"),
                            reg.get("loja_nome"),
                            desc,
                            desc_norm,
                            reg.get("preco_centavos"),
                            reg.get("unidade", "UN"),
                            hoje,  # data de hoje (catálogo coletado hoje)
                            reg.get("gtin"),
                            reg.get("sku"),
                            "catalogo",  # fonte fixa
                            None,  # sem loja_id (é catálogo online, não loja física)
                        ),
                    )
                    if cur.rowcount > 0:
                        inseridos += 1

            conn.commit()
    finally:
        pool.closeall()

    print(f"Inseridos: {inseridos} registros")
    print("✓ Catálogo gravado com fonte='catalogo'")


if __name__ == "__main__":
    gravar_catalogo()
