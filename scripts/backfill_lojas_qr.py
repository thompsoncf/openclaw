"""Backfill: cria as lojas faltantes a partir do CNPJ embutido na chave do QR.

Pega leituras de QR (qr_leituras) cujo CNPJ (chave, posicoes 7-20) ainda NAO tem
loja cadastrada e cria a loja via _achar_ou_criar_loja - que consulta a BrasilAPI
pra preencher nome/endereco. So' CRIA loja (pra aparecer em "Lojas cadastradas");
nao re-liga precos antigos (esses nao guardam o CNPJ; somem pela janela de 90 dias).

Rodar no Render Shell:
    python -m scripts.backfill_lojas_qr
"""
from db.conexao import get_pool
from finance.livro_caixa import LivroCaixa


def main():
    pool = get_pool()
    with pool.connection() as conn:
        orfas = conn.execute(
            """select distinct substring(q.chave from 7 for 14) as cnpj
               from qr_leituras q
               left join lojas l on l.cnpj = substring(q.chave from 7 for 14)
               where q.chave is not null
                 and length(q.chave) = 44
                 and l.id is null"""
        ).fetchall()

    print(f"{len(orfas)} CNPJ(s) orfao(s) (QR lido sem loja cadastrada).")
    if not orfas:
        print("Nada a fazer.")
        return

    livro = LivroCaixa(pool, 0)   # _achar_ou_criar_loja nao usa conta_id
    for (cnpj,) in orfas:
        with pool.connection() as conn:
            try:
                loja_id = livro._achar_ou_criar_loja(conn, cnpj, None, None, None, None)
                conn.commit()
                print(f"  {cnpj} -> loja_id {loja_id}")
            except Exception as e:  # noqa: BLE001
                print(f"  {cnpj} -> ERRO: {e}")


if __name__ == "__main__":
    main()
