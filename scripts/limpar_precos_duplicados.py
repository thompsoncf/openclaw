"""Limpa precos duplicados no banco (precos_observados): mesmo produto + preco +
loja (ou mercado) + data, mantendo apenas 1. Os duplicados vinham de cupons com
varias unidades do mesmo produto (ex: 3 Farmax) antes da dedup no _observar_precos.

Idempotente. Uso (no Render Shell):
    python -m scripts.limpar_precos_duplicados
"""
from db.conexao import get_pool


def limpar_duplicados() -> int:
    """Remove duplicatas mantendo o menor id de cada grupo
    (descricao_norm, valor_unitario_centavos, coalesce(loja_id,0), mercado, data_compra)."""
    pool = get_pool()
    with pool.connection() as c:
        antes = c.execute("select count(*) from precos_observados").fetchone()[0]
        c.execute(
            """delete from precos_observados p
               where exists (
                 select 1 from precos_observados q
                 where q.descricao_norm = p.descricao_norm
                   and q.valor_unitario_centavos = p.valor_unitario_centavos
                   and coalesce(q.loja_id,0) = coalesce(p.loja_id,0)
                   and coalesce(q.mercado,'') = coalesce(p.mercado,'')
                   and q.data_compra = p.data_compra
                   and q.id < p.id
               )""")
        c.commit()
        depois = c.execute("select count(*) from precos_observados").fetchone()[0]
    return antes - depois


if __name__ == "__main__":
    removidos = limpar_duplicados()
    print(f"Precos duplicados removidos: {removidos}")
    pool = get_pool()
    with pool.connection() as c:
        total = c.execute("select count(*) from precos_observados").fetchone()[0]
        unicos = c.execute(
            "select count(distinct descricao_norm) from precos_observados").fetchone()[0]
        print(f"Precos restantes: {total} | produtos unicos: {unicos}")
