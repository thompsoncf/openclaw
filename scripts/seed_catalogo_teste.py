"""scripts/seed_catalogo_teste.py — popula catálogo do fornecedor pra TESTE.

Cria 8 produtos hortifruti com preço de venda + entrada de estoque, com custos
calibrados pra ficar abaixo da margem alvo (60%) — assim o montador (Fase 4)
consegue escolher TODOS sem rejeitar nenhum por margem.

⚠️ É PRA TESTE: usa números redondos, sem variação realista. Se quiser dado
mais realista depois, é só ajustar a lista PRODUTOS abaixo.

Idempotente — pode rodar várias vezes:
- Se o produto já existe (mesmo nome no catálogo), pula a criação.
- A entrada de estoque, porém, é registrada toda vez (vai acumulando saldo).
  Pra forçar uma execução limpa, rodar com `--zerar` (apaga movimentos antes).

USO no Render Shell:
    python -m scripts.seed_catalogo_teste            # popula (idempotente, soma estoque)
    python -m scripts.seed_catalogo_teste --zerar    # zera movimentos do fornecedor e popula
    python -m scripts.seed_catalogo_teste --forn 9   # pra outro fornecedor (default: ze-do-arroz)
"""
from __future__ import annotations

import argparse
import sys

from finance import catalogo as cat


# (nome, unidade, categoria, preço_venda_R$, custo_R$, qtd_entrada)
# Margem (custo/venda): tudo abaixo de 60% — montador aceita todos.
PRODUTOS = [
    # frutas
    ("Banana prata",   "kg",      "fruta",   6.00,  3.00,  30),
    ("Maçã gala",      "kg",      "fruta",   9.00,  4.50,  20),
    # legumes
    ("Tomate italiano","kg",      "legume",  8.00,  4.00,  25),
    ("Cenoura",        "kg",      "legume",  5.00,  2.50,  20),
    ("Cebola",         "kg",      "legume",  4.00,  2.00,  25),
    # verduras
    ("Alface crespa",  "unidade", "verdura", 3.50,  1.50,  40),
    ("Couve",          "maco",    "verdura", 4.00,  1.80,  30),
    # tempero
    ("Cebolinha",      "maco",    "tempero", 3.00,  1.20,  25),
]

SLUG_PADRAO = "ze-do-arroz"


def _achar_fornecedor(pool, slug: str) -> int | None:
    with pool.connection() as c:
        row = c.execute(
            "select id from contas where fornecedor_slug = %s and eh_fornecedor = true",
            (slug,),
        ).fetchone()
        return int(row[0]) if row else None


def _produto_existente(pool, fornecedor_id: int, nome: str) -> int | None:
    with pool.connection() as c:
        row = c.execute(
            "select id from catalogo_produtos where fornecedor_id = %s and lower(nome) = lower(%s) and ativo",
            (fornecedor_id, nome),
        ).fetchone()
        return int(row[0]) if row else None


def _origem_teste(pool, fornecedor_id: int) -> int:
    """Garante uma origem 'CEASA (teste)' pra associar às entradas."""
    with pool.connection() as c:
        row = c.execute(
            "select id from origem_compra where fornecedor_id = %s and nome = 'CEASA (teste)'",
            (fornecedor_id,),
        ).fetchone()
        if row:
            return int(row[0])
    return cat.criar_origem(pool, fornecedor_id, "CEASA (teste)")


def _zerar_movimentos(pool, fornecedor_id: int) -> int:
    """Apaga TODAS as movimentações do fornecedor e zera saldos. Use --zerar."""
    with pool.connection() as c:
        n = c.execute(
            "delete from estoque_mov where fornecedor_id = %s",
            (fornecedor_id,),
        ).rowcount
        c.execute(
            "update catalogo_produtos set saldo = 0, custo_medio_centavos = 0 where fornecedor_id = %s",
            (fornecedor_id,),
        )
        c.commit()
    return n


def seed(pool, fornecedor_id: int, *, zerar: bool = False) -> dict:
    relatorio = {"criados": 0, "ja_existiam": 0, "entradas": 0, "movimentos_apagados": 0}

    if zerar:
        relatorio["movimentos_apagados"] = _zerar_movimentos(pool, fornecedor_id)
        print(f"  ✓ Apagados {relatorio['movimentos_apagados']} movimentos antigos.")

    origem_id = _origem_teste(pool, fornecedor_id)

    for nome, unidade, categoria, preco, custo, qtd in PRODUTOS:
        prod_id = _produto_existente(pool, fornecedor_id, nome)
        if prod_id is None:
            prod_id = cat.criar_produto(
                pool, fornecedor_id, nome,
                unidade=unidade, categoria=categoria,
                preco_venda_centavos=int(round(preco * 100)),
                estoque_minimo=0,
            )
            relatorio["criados"] += 1
            acao = "criado"
        else:
            relatorio["ja_existiam"] += 1
            acao = "já existia"

        cat.registrar_movimentacao(
            pool, fornecedor_id, prod_id, "entrada",
            quantidade=qtd,
            custo_unit_centavos=int(round(custo * 100)),
            origem_id=origem_id,
            motivo="seed teste",
        )
        relatorio["entradas"] += 1
        margem_pct = (custo / preco) * 100 if preco else 0
        print(f"  ✓ {nome:20s} ({acao}) — +{qtd} {unidade} a R$ {custo:.2f}/u "
              f"(venda R$ {preco:.2f}, margem custo {margem_pct:.0f}%)")

    return relatorio


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="Popula catálogo do fornecedor pra teste.")
    parser.add_argument("--forn", type=int, default=None,
                        help="fornecedor_id direto (sobrepõe --slug)")
    parser.add_argument("--slug", default=SLUG_PADRAO,
                        help=f"slug do fornecedor (default: {SLUG_PADRAO})")
    parser.add_argument("--zerar", action="store_true",
                        help="apaga movimentos antigos antes (saldo limpo)")
    args = parser.parse_args(argv)

    # _setup() é onde o pool é criado em produção — mesmo padrão da janela
    from web.app import _setup
    pool = _setup()

    if args.forn is not None:
        fornecedor_id = args.forn
        print(f"→ Fornecedor id={fornecedor_id}")
    else:
        fornecedor_id = _achar_fornecedor(pool, args.slug)
        if fornecedor_id is None:
            print(f"→ Fornecedor com slug '{args.slug}' não encontrado.")
            return 1
        print(f"→ Fornecedor '{args.slug}' id={fornecedor_id}")

    print(f"→ Populando {len(PRODUTOS)} produtos...\n")
    r = seed(pool, fornecedor_id, zerar=args.zerar)
    print(f"\n✓ Pronto: {r['criados']} criados, {r['ja_existiam']} já existiam, "
          f"{r['entradas']} entradas de estoque registradas.")
    print("\nPróximo passo: rodar a janela pra montar a cesta de teste:")
    print("  from datetime import date")
    print("  from finance import janela")
    print("  from web.app import _setup")
    print("  pool = _setup()")
    print("  janela.abrir_janela_semana(pool, apenas_assinatura_id=2, data_entrega=date(2026,7,7))")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
