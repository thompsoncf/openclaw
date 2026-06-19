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

    # ---------- escrita ----------

    def adicionar(self, descricao: str, quantidade: float = 1,
                  unidade: str | None = None,
                  preco_estimado_centavos: int | None = None,
                  fonte_preco: str | None = None) -> int:
        """Adiciona um item a' lista da conta. Retorna o id do item.

        Se um item com a mesma descricao (ignorando acento/caixa) ja' estiver na
        lista e NAO comprado, nao duplica - retorna o id do existente. Isso evita
        a lista encher de 'arroz, arroz, arroz' por duplo-clique ou reenvio.
        """
        descricao = (descricao or "").strip()
        if not descricao:
            return 0
        import unicodedata
        norm = unicodedata.normalize("NFKD", descricao.lower())
        norm = "".join(ch for ch in norm if not unicodedata.combining(ch)).strip()
        with self.pool.connection() as c:
            existente = c.execute(
                """select id from lista_compras
                   where conta_id = %s and comprado = false
                     and lower(translate(descricao,
                         'Ã¡Ã Ã¢Ã£Ã¤Ã©Ã¨ÃªÃ«Ã­Ã¬Ã®Ã¯Ã³Ã²Ã´ÃµÃ¶ÃºÃ¹Ã»Ã¼Ã§ÃÃÃÃÃÃÃÃÃÃÃÃÃÃÃÃÃÃÃÃÃÃÃ',
                         'aaaaaeeeeiiiiooooouuuucAAAAAEEEEIIIIOOOOOUUUUC')) = %s
                   limit 1""",
                (self.conta_id, norm)).fetchone()
            if existente:
                return existente[0]
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
        """Adiciona varios itens de uma vez (ex: lista ditada por voz). Retorna
        quantos foram REALMENTE adicionados (novos).

        Pula itens que ja' estao na lista (nao comprados) e duplicatas dentro da
        propria chamada, pra nao encher de repetidos.
        """
        itens = [(d or "").strip() for d in descricoes if (d or "").strip()]
        if not itens:
            return 0
        antes = {i["id"] for i in self.listar(incluir_comprados=False)}
        novos = 0
        for d in itens:
            novo_id = self.adicionar(d)
            if novo_id and novo_id not in antes:
                antes.add(novo_id)
                novos += 1
        return novos

    def marcar_comprado(self, item_id: int, comprado: bool = True) -> bool:
        """Marca/desmarca um item como comprado. So' age em item DESTA conta."""
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
        """Tira da lista tudo que ja' foi comprado (faxina pos-compra)."""
        with self.pool.connection() as c:
            r = c.execute(
                "delete from lista_compras where conta_id = %s and comprado",
                (self.conta_id,),
            )
            c.commit()
            return r.rowcount

    def limpar_tudo(self) -> int:
        """Apaga a lista INTEIRA (pendentes e comprados). Usar com confirmacao."""
        with self.pool.connection() as c:
            r = c.execute(
                "delete from lista_compras where conta_id = %s",
                (self.conta_id,),
            )
            c.commit()
            return r.rowcount

    def finalizar_compra(self) -> dict:
        """Fecha a compra: arquiva os itens COMPRADOS no historico e os tira da lista.
        Os PENDENTES continuam na lista pra proxima compra. Sem total (so' data e
        quantidade de itens). Retorna {compra_id, itens} ou {compra_id: None, itens: 0}
        se nao havia nada comprado.
        """
        with self.pool.connection() as c:
            # conta quantos comprados ha' agora
            row = c.execute(
                "select count(*) from lista_compras where conta_id = %s and comprado",
                (self.conta_id,),
            ).fetchone()
            n_itens = int(row[0]) if row else 0
            if n_itens == 0:
                return {"compra_id": None, "itens": 0}

            # cria o registro da compra (so' data e n de itens)
            compra = c.execute(
                """insert into compras_historico (conta_id, membro_id, total_itens)
                   values (%s, %s, %s) returning id""",
                (self.conta_id, self.membro_id, n_itens),
            ).fetchone()
            compra_id = int(compra[0])

            # tira os comprados da lista ativa (ja' estao arquivados no historico)
            c.execute(
                "delete from lista_compras where conta_id = %s and comprado",
                (self.conta_id,),
            )
            c.commit()
        return {"compra_id": compra_id, "itens": n_itens}

    def listar_historico(self, limite: int = 50) -> list[dict]:
        """Compras finalizadas DESTA conta, mais recentes primeiro."""
        sql = """select h.id, h.total_itens, h.criado_em,
                        coalesce(m.nome, '-') as quem
                 from compras_historico h
                 left join membros m on m.id = h.membro_id
                 where h.conta_id = %s
                 order by h.id desc
                 limit %s"""
        with self.pool.connection() as c:
            rows = c.execute(sql, (self.conta_id, limite)).fetchall()
        cols = ["id", "total_itens", "criado_em", "quem"]
        return [dict(zip(cols, r)) for r in rows]

    # ---------- leitura ----------

    def listar(self, incluir_comprados: bool = True) -> list[dict]:
        """Itens da lista DESTA conta. Pendentes primeiro, mais novos no topo."""
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
        """Contagem e estimativa total dos itens PENDENTES."""
        with self.pool.connection() as c:
            row = c.execute(
                """select count(*) filter (where not comprado) as pendentes,
                          count(*) filter (where comprado) as comprados,
                          coalesce(sum(preco_estimado_centavos) filter (where not comprado),0) as estimado
                   from lista_compras where conta_id = %s""",
                (self.conta_id,),
            ).fetchone()
        return {"pendentes": row[0], "comprados": row[1], "estimado_centavos": int(row[2])}

    # ---------- gaveta de preco (plugavel) ----------

    def estimar_precos(self, estimador) -> int:
        """Preenche preco_estimado dos itens pendentes SEM preco, usando o
        `estimador(descricao) -> (centavos|None, fonte|None)`.

        `estimador` e' injetado de fora (historico proprio agora, SEFAZ depois),
        entao a lista nao se acopla a nenhuma fonte. Retorna quantos foram preenchidos.
        """
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
