"""Semeia a tabela `lojas` a partir dos CNPJs ja' lidos pelo QR (qr_leituras).

Cada CNPJ distinto vira uma loja (com UF). Nome/endereco ficam vazios e se
completam quando um cupom novo dessa loja chegar (_achar_ou_criar_loja atualiza).
Idempotente: rodar varias vezes nao duplica (on conflict do nothing).

Uso (no Render Shell):
    python -m scripts.semear_lojas
"""
from db.conexao import get_pool


def semear_lojas() -> int:
    pool = get_pool()
    with pool.connection() as c:
        rows = c.execute(
            """select distinct cnpj_emitente, uf from qr_leituras
               where cnpj_emitente is not null
                 and length(cnpj_emitente) = 14
                 and cnpj_emitente not in (select cnpj from lojas)"""
        ).fetchall()
        n = 0
        for cnpj, uf in rows:
            c.execute(
                "insert into lojas (cnpj, uf) values (%s,%s) on conflict (cnpj) do nothing",
                (cnpj, uf))
            n += 1
        c.commit()
    return n


def completar_lojas_vazias() -> int:
    """Completa nome/endereco das lojas que estao sem esses dados, consultando
    o CNPJ na Receita (BrasilAPI). Tolerante: se a rede falhar, pula a loja."""
    from finance.cnpj_info import consultar_cnpj
    pool = get_pool()
    completadas = 0
    with pool.connection() as c:
        vazias = c.execute(
            "select id, cnpj from lojas where nome is null or endereco is null or ramo is null"
        ).fetchall()
    for loja_id, cnpj in vazias:
        info = consultar_cnpj(cnpj)
        if not info:
            continue
        with pool.connection() as c:
            c.execute(
                """update lojas set
                     nome = coalesce(nome, %s),
                     endereco = coalesce(endereco, %s),
                     cidade = coalesce(cidade, %s),
                     uf = coalesce(uf, %s),
                     ramo = coalesce(ramo, %s),
                     cnae = coalesce(cnae, %s)
                   where id = %s""",
                (info.get("nome"), info.get("endereco"),
                 info.get("cidade"), info.get("uf"),
                 info.get("ramo"), info.get("cnae"), loja_id))
            c.commit()
        completadas += 1
    return completadas


if __name__ == "__main__":
    n = semear_lojas()
    print(f"Lojas semeadas a partir do QR: {n}")
    c = completar_lojas_vazias()
    print(f"Lojas completadas via Receita (BrasilAPI): {c}")
    pool = get_pool()
    with pool.connection() as conn:
        total = conn.execute("select count(*) from lojas").fetchone()[0]
        print(f"Total de lojas agora: {total}")
        for cnpj, nome, end, uf in conn.execute(
                "select cnpj, nome, endereco, uf from lojas order by id").fetchall():
            print(f"  {cnpj} ({uf}) - {nome or '(sem nome)'} - {end or '(sem endereco)'}")
