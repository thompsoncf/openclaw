"""Religa precos antigos (sem loja_id) as lojas existentes, casando o campo
`mercado` (texto do preco) com o nome da loja por PALAVRA-CHAVE forte.

Ex: preco com mercado="Grupo Vanguarda - Ininga" casa com a loja
"DISTRIBUICAO DE ALIMENTOS VANGUARDA S/A" pela palavra "vanguarda".

So' religa o que casa com CONFIANCA (palavra-chave especifica da loja). Precos
de mercados sem loja cadastrada (ex: Carvalho, que nunca teve CNPJ lido) ficam
como estao - esperam um cupom novo que traga o CNPJ.

Idempotente. Uso (no Render Shell):
    python -m scripts.religar_precos_lojas
"""
import unicodedata

from db.conexao import get_pool

# palavras genericas que NAO servem pra identificar uma loja especifica
_STOP = {
    "distribuicao", "de", "alimentos", "comercio", "varejista", "atacadista",
    "mercado", "supermercado", "hipermercado", "minimercado", "mercearia",
    "grupo", "do", "da", "e", "loja", "lojas", "ltda", "epp", "me", "eireli",
    "sa", "industria", "produtos", "servicos", "teresina", "ininga", "centro",
}


def _norm(s: str) -> str:
    t = unicodedata.normalize("NFKD", (s or "").lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    return t.replace("/", " ").replace("-", " ")


def _palavras_chave(nome_loja: str) -> list[str]:
    """Palavras fortes (especificas) do nome da loja, pra casar com o texto do preco."""
    return [p for p in _norm(nome_loja).split() if p not in _STOP and len(p) > 3]


def religar() -> dict:
    pool = get_pool()
    religados_total = 0
    detalhe = {}
    with pool.connection() as c:
        lojas = c.execute(
            "select id, nome from lojas where nome is not null").fetchall()
    for loja_id, nome in lojas:
        chaves = _palavras_chave(nome)
        if not chaves:
            continue
        # religa precos SEM loja cujo mercado contem alguma palavra-chave da loja
        with pool.connection() as c:
            # monta condicao OR de ilike pra cada palavra-chave
            cond = " or ".join(["lower(mercado) like %s"] * len(chaves))
            args = [f"%{k}%" for k in chaves]
            n = c.execute(
                f"""update precos_observados
                    set loja_id = %s
                    where loja_id is null and mercado is not null and ({cond})""",
                [loja_id, *args]).rowcount
            c.commit()
        if n:
            detalhe[nome] = n
            religados_total += n
    return {"total": religados_total, "por_loja": detalhe}


if __name__ == "__main__":
    r = religar()
    print(f"Precos religados a lojas: {r['total']}")
    for nome, n in r["por_loja"].items():
        print(f"  {nome}: {n} precos")
    pool = get_pool()
    with pool.connection() as c:
        sem = c.execute(
            "select count(*) from precos_observados where loja_id is null").fetchone()[0]
        print(f"Precos ainda sem loja (mercados sem CNPJ): {sem}")
