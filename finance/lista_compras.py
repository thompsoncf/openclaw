"""Lista de compras colaborativa - escopo por CONTA (multi-tenant).

Qualquer membro da conta adiciona itens (por chat ou portal). Marca-se ao
comprar (sai do carrinho ativo). O preco_estimado e' uma "gaveta": hoje pode
vir do historico proprio (raio-x); amanha, da API da SEFAZ. A camada de preco
e' plugavel via o parametro `estimador` (uma funcao opcional), pra nao acoplar
a lista a nenhuma fonte especifica.
"""
from __future__ import annotations


class ListaCompras:
    def __init__(self, pool, conta_id: int, membro_id: int | None = None):
        self.pool = pool
        self.conta_id = conta_id
        self.membro_id = membro_id

    def adicionar(self, descricao: str, quantidade: float = 1,
                  unidade: str | None = None,
                  preco_estimado_centavos: int | None = None,
                  fonte_preco: str | None = None) -> int:
        descricao = (descricao or "").strip()
        if not descricao:
            return 0
        with self.pool.connection() as c:
            row = c.execute(
                """insert into lista_compras
                   (conta_id, membro_id, descricao, quantidade, unidade,
                    preco_estimado_centavos, fonte_preco)
                   values (%s,%s,%s,%s,%s,%s,%s) returning id""",
                (self.conta_id, self.membro_id, descricao, quantidade, unidade,
                 preco_estimado_centavos, fonte_preco),
            ).fetchone()
            c.commit()
            return row[0]

    def adicionar_varios(self, descricoes: list[str]) -> int:
        itens = [(d or "").strip() for d in descricoes if (d or "").strip()]
        if not itens:
            return 0
        with self.pool.connection() as c:
            c.cursor().executemany(
                """insert into lista_compras (conta_id, membro_id, descricao)
                   values (%s,%s,%s)""",
                [(self.conta_id, self.membro_id, d) for d in itens],
            )
            c.commit()
        return len(itens)

    def marcar_comprado(self, item_id: int, comprado: bool = True) -> bool:
        with self.pool.connection() as c:
            r = c.execute(
                """update lista_compras
                   set comprado = %s,
                       comprado_em = case when %s then now() else null end
                   where id = %s and conta_id = %s""",
                (comprado, comprado, item_id, self.conta_id),
            )
            c.commit()
            return r.rowcount > 0

    def remover(self, item_id: int) -> bool:
        with self.pool.connection() as c:
            r = c.execute(
                "delete from lista_compras where id = %s and conta_id = %s",
                (item_id, self.conta_id),
            )
            c.commit()
            return r.rowcount > 0

    def limpar_comprados(self) -> int:
        with self.pool.connection() as c:
            r = c.execute(
                "delete from lista_compras where conta_id = %s and comprado",
                (self.conta_id,),
            )
            c.commit()
            return r.rowcount

    def listar(self, incluir_comprados: bool = True) -> list[dict]:
        sql = """select l.id, l.descricao, l.quantidade, l.unidade, l.comprado,
                        l.preco_estimado_centavos, l.fonte_preco,
                        coalesce(m.nome, '-') as quem
                 from lista_compras l
                 left join membros m on m.id = l.membro_id
                 where l.conta_id = %s"""
        if not incluir_comprados:
            sql += " and not l.comprado"
        sql += " order by l.comprado asc, l.id desc"
        with self.pool.connection() as c:
            rows = c.execute(sql, (self.conta_id,)).fetchall()
        cols = ["id", "descricao", "quantidade", "unidade", "comprado",
                "preco_estimado_centavos", "fonte_preco", "quem"]
        return [dict(zip(cols, r)) for r in rows]

    def resumo(self) -> dict:
        with self.pool.connection() as c:
            row = c.execute(
                """select count(*) filter (where not comprado) as pendentes,
                          count(*) filter (where comprado) as comprados,
                          coalesce(sum(preco_estimado_centavos) filter (where not comprado),0) as estimado
                   from lista_compras where conta_id = %s""",
                (self.conta_id,),
            ).fetchone()
        return {"pendentes": row[0], "comprados": row[1], "estimado_centavos": int(row[2])}

    def estimar_precos(self, estimador) -> int:
        pendentes = [i for i in self.listar(incluir_comprados=False)
                     if i["preco_estimado_centavos"] is None]
        n = 0
        for item in pendentes:
            centavos, fonte = estimador(item["descricao"])
            if centavos is not None:
                with self.pool.connection() as c:
                    c.execute(
                        """update lista_compras set preco_estimado_centavos=%s, fonte_preco=%s
                           where id=%s and conta_id=%s""",
                        (int(centavos), fonte, item["id"], self.conta_id),
                    )
                    c.commit()
                n += 1
        return n
