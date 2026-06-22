"""Tamanhos de cesta do fornecedor (Fase 2, lado do cliente).

O fornecedor define os tamanhos (P/M/G) com PREÇO FIXO e o "esqueleto" — quantas
porções de cada grupo (frutas/legumes/verduras/temperos). É o que o cliente assina.

Modelo (decisão de negócio, validada no mercado):
- A cesta tem PREÇO FIXO por tamanho (cliente sabe quanto paga = Pix previsível).
- A COMPOSIÇÃO exata varia toda semana (o montador escolhe do estoque, respeitando
  o esqueleto e as restrições do cliente). Quando um produto está caro/em falta,
  troca por outro do mesmo grupo. Cliente paga o mesmo, recebe cesta cheia.

Tudo monetário em CENTAVOS. Funções recebem o pool e validam fornecedor_id
(multi-tenant: um fornecedor nunca mexe na cesta de outro).
"""
from __future__ import annotations

import re
import unicodedata


def _slug(texto: str) -> str:
    """Gera slug a partir do nome: 'Cesta Média' -> 'cesta-media'."""
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return t or "cesta"


def criar_tamanho(
    pool,
    fornecedor_id: int,
    nome: str,
    preco_centavos: int,
    qtd_frutas: int = 0,
    qtd_legumes: int = 0,
    qtd_verduras: int = 0,
    qtd_temperos: int = 0,
    descricao: str | None = None,
    ordem: int = 0,
) -> int:
    """Cria um tamanho de cesta. Retorna o id.

    O slug é gerado do nome; se já existir pro fornecedor, sufixa um número.
    """
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("nome do tamanho é obrigatório")
    base = _slug(nome)
    with pool.connection() as c:
        # garante slug único por fornecedor
        existe = c.execute(
            "select 1 from cesta_tamanhos where fornecedor_id=%s and slug=%s",
            (fornecedor_id, base),
        ).fetchone()
        slug = base if not existe else f"{base}-{int(__import__('time').time()) % 1000}"
        row = c.execute(
            """insert into cesta_tamanhos
                 (fornecedor_id, nome, slug, preco_centavos,
                  qtd_frutas, qtd_legumes, qtd_verduras, qtd_temperos,
                  descricao, ordem)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
               returning id""",
            (fornecedor_id, nome, slug, max(0, int(preco_centavos)),
             max(0, int(qtd_frutas)), max(0, int(qtd_legumes)),
             max(0, int(qtd_verduras)), max(0, int(qtd_temperos)),
             (descricao or "").strip() or None, int(ordem)),
        ).fetchone()
        c.commit()
    return int(row[0])


def listar_tamanhos(pool, fornecedor_id: int, so_ativos: bool = True) -> list[dict]:
    """Lista os tamanhos de cesta do fornecedor (ordenados)."""
    sql = """select id, nome, slug, preco_centavos,
                    qtd_frutas, qtd_legumes, qtd_verduras, qtd_temperos,
                    descricao, ativo, ordem
             from cesta_tamanhos where fornecedor_id = %s"""
    if so_ativos:
        sql += " and ativo"
    sql += " order by ordem, preco_centavos"
    with pool.connection() as c:
        rows = c.execute(sql, (fornecedor_id,)).fetchall()
    return [
        {
            "id": r[0], "nome": r[1], "slug": r[2],
            "preco_centavos": int(r[3] or 0),
            "qtd_frutas": r[4], "qtd_legumes": r[5],
            "qtd_verduras": r[6], "qtd_temperos": r[7],
            "total_porcoes": (r[4] or 0) + (r[5] or 0) + (r[6] or 0) + (r[7] or 0),
            "descricao": r[8], "ativo": r[9], "ordem": r[10],
        }
        for r in rows
    ]


def obter_tamanho(pool, fornecedor_id: int, tamanho_id: int) -> dict | None:
    """Retorna um tamanho específico (ou None)."""
    tams = listar_tamanhos(pool, fornecedor_id, so_ativos=False)
    for t in tams:
        if t["id"] == tamanho_id:
            return t
    return None


def atualizar_tamanho(pool, fornecedor_id: int, tamanho_id: int, **campos) -> bool:
    """Atualiza campos do tamanho (nome, preco_centavos, qtds, descricao, ordem, ativo)."""
    permitidos = {
        "nome", "preco_centavos", "qtd_frutas", "qtd_legumes",
        "qtd_verduras", "qtd_temperos", "descricao", "ordem", "ativo",
    }
    sets, vals = [], []
    for k, v in campos.items():
        if k not in permitidos:
            continue
        if k == "preco_centavos":
            v = max(0, int(v))
        if k in ("qtd_frutas", "qtd_legumes", "qtd_verduras", "qtd_temperos", "ordem"):
            v = max(0, int(v))
        sets.append(f"{k} = %s")
        vals.append(v)
    if not sets:
        return False
    sets.append("atualizado_em = now()")
    vals.extend([tamanho_id, fornecedor_id])
    with pool.connection() as c:
        cur = c.execute(
            f"""update cesta_tamanhos set {', '.join(sets)}
                where id = %s and fornecedor_id = %s""",
            tuple(vals),
        )
        c.commit()
        return cur.rowcount > 0


def arquivar_tamanho(pool, fornecedor_id: int, tamanho_id: int) -> bool:
    """Soft delete: marca ativo=false (não apaga, pra não quebrar assinaturas)."""
    with pool.connection() as c:
        cur = c.execute(
            """update cesta_tamanhos set ativo = false, atualizado_em = now()
               where id = %s and fornecedor_id = %s""",
            (tamanho_id, fornecedor_id),
        )
        c.commit()
        return cur.rowcount > 0
