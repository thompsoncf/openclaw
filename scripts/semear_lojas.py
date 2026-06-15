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


if __name__ == "__main__":
    n = semear_lojas()
    print(f"Lojas semeadas a partir do QR: {n}")
    pool = get_pool()
    with pool.connection() as c:
        total = c.execute("select count(*) from lojas").fetchone()[0]
        print(f"Total de lojas agora: {total}")
        for cnpj, uf in c.execute("select cnpj, uf from lojas order by id").fetchall():
            print(f"  CNPJ {cnpj} ({uf})")
