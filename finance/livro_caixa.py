"""Livro-caixa de UMA conta (tenant).

Todo metodo e' escopado por conta_id: a conta so' enxerga e mexe no que e' dela
(isolamento sagrado do multi-tenant). membro_id marca QUEM lancou (auditoria).
"""
from datetime import date, timedelta

from .models import (
    Lancamento, Tipo, centavos_para_reais, formatar_brl,
)


class LivroCaixa:
    def __init__(self, pool, conta_id: int, membro_id: int | None = None):
        self.pool = pool
        self.conta_id = conta_id
        self.membro_id = membro_id

    def adicionar(self, lanc: Lancamento) -> Lancamento:
        with self.pool.connection() as conn:
            row = conn.execute(
                """insert into lancamentos
                   (conta_id, membro_id, tipo, valor_centavos, categoria, descricao,
                    data, pagamento, origem, comprovante)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                (self.conta_id, self.membro_id, lanc.tipo.value, lanc.valor_centavos,
                 lanc.categoria, lanc.descricao, lanc.data, lanc.pagamento,
                 lanc.origem, lanc.comprovante),
            ).fetchone()
            conn.commit()
            lanc.id = row[0]
            return lanc

    def listar(self, mes: int | None = None, ano: int | None = None, limite: int = 50) -> list[Lancamento]:
        sql = "select id, tipo, valor_centavos, categoria, descricao, data, pagamento, origem, comprovante from lancamentos where conta_id = %s"
        params: list = [self.conta_id]
        if ano:
            sql += " and extract(year from data) = %s"
            params.append(ano)
        if mes:
            sql += " and extract(month from data) = %s"
            params.append(mes)
        sql += " order by data desc, id desc limit %s"
        params.append(limite)
        with self.pool.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return [
            Lancamento(id=r[0], tipo=Tipo(r[1]), valor_centavos=r[2], categoria=r[3],
                       descricao=r[4], data=r[5], pagamento=r[6], origem=r[7], comprovante=r[8])
            for r in rows
        ]

    def saldo_centavos(self) -> int:
        with self.pool.connection() as conn:
            row = conn.execute(
                """select
                     coalesce(sum(case when tipo='receita' then valor_centavos else 0 end),0)
                   - coalesce(sum(case when tipo='despesa' then valor_centavos else 0 end),0)
                   from lancamentos where conta_id = %s""",
                (self.conta_id,),
            ).fetchone()
        return int(row[0])

    def total_por_categoria(self, tipo: Tipo, mes: int | None = None, ano: int | None = None) -> dict[str, int]:
        sql = "select categoria, sum(valor_centavos) from lancamentos where conta_id = %s and tipo = %s"
        params: list = [self.conta_id, Tipo(tipo).value]
        if ano:
            sql += " and extract(year from data) = %s"
            params.append(ano)
        if mes:
            sql += " and extract(month from data) = %s"
            params.append(mes)
        sql += " group by categoria order by sum(valor_centavos) desc"
        with self.pool.connection() as conn:
            rows = conn.execute(sql, params).fetchall()
        return {r[0]: int(r[1]) for r in rows}

    def gastos_do_mes_centavos(self, ano: int, mes: int) -> int:
        with self.pool.connection() as conn:
            row = conn.execute(
                """select coalesce(sum(valor_centavos),0) from lancamentos
                   where conta_id = %s and tipo='despesa'
                   and extract(year from data)=%s and extract(month from data)=%s""",
                (self.conta_id, ano, mes),
            ).fetchone()
        return int(row[0])

    # ---------- Dashboard do cliente (Bloco C) ----------

    def resumo_mes(self, ano: int, mes: int, membro_id: int | None = None) -> dict:
        """Saldo acumulado + receitas/despesas do mes (opcional: de UM membro)."""
        cond = "conta_id = %s"
        base: list = [self.conta_id]
        if membro_id is not None:
            cond += " and membro_id = %s"; base.append(membro_id)
        with self.pool.connection() as conn:
            saldo = conn.execute(
                f"""select coalesce(sum(case when tipo='receita' then valor_centavos else -valor_centavos end),0)
                    from lancamentos where {cond}""", base).fetchone()[0]
            rec = conn.execute(
                f"""select coalesce(sum(valor_centavos),0) from lancamentos
                    where {cond} and tipo='receita'
                    and extract(year from data)=%s and extract(month from data)=%s""",
                base + [ano, mes]).fetchone()[0]
            desp = conn.execute(
                f"""select coalesce(sum(valor_centavos),0) from lancamentos
                    where {cond} and tipo='despesa'
                    and extract(year from data)=%s and extract(month from data)=%s""",
                base + [ano, mes]).fetchone()[0]
        return {"saldo": int(saldo), "receitas": int(rec), "despesas": int(desp)}

    def despesas_por_categoria(self, ano: int, mes: int, membro_id: int | None = None) -> list[tuple[str, int]]:
        cond = "conta_id = %s and tipo='despesa'"
        params: list = [self.conta_id]
        if membro_id is not None:
            cond += " and membro_id = %s"; params.append(membro_id)
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"""select categoria, sum(valor_centavos) from lancamentos
                    where {cond} and extract(year from data)=%s and extract(month from data)=%s
                    group by categoria order by sum(valor_centavos) desc""",
                params + [ano, mes]).fetchall()
        return [(r[0], int(r[1])) for r in rows]

    def evolucao_mensal(self, meses: int = 6, membro_id: int | None = None) -> list[dict]:
        """Receitas e despesas dos ultimos N meses (pra grafico de tendencia)."""
        cond = "conta_id = %s"
        params: list = [self.conta_id]
        if membro_id is not None:
            cond += " and membro_id = %s"; params.append(membro_id)
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"""select to_char(date_trunc('month', data), 'YYYY-MM') as mes,
                          coalesce(sum(case when tipo='receita' then valor_centavos else 0 end),0),
                          coalesce(sum(case when tipo='despesa' then valor_centavos else 0 end),0)
                    from lancamentos where {cond}
                    group by 1 order by 1 desc limit %s""",
                params + [meses]).fetchall()
        return [{"mes": r[0], "receitas": int(r[1]), "despesas": int(r[2])} for r in reversed(rows)]

    def lancamentos_recentes(self, ano: int, mes: int, membro_id: int | None = None,
                             tipo: str | None = None, limite: int = 50) -> list[dict]:
        cond = "l.conta_id = %s and extract(year from l.data)=%s and extract(month from l.data)=%s"
        params: list = [self.conta_id, ano, mes]
        if membro_id is not None:
            cond += " and l.membro_id = %s"; params.append(membro_id)
        if tipo in ("despesa", "receita"):
            cond += " and l.tipo = %s"; params.append(tipo)
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"""select l.data, l.descricao, l.categoria, l.tipo, l.valor_centavos,
                          l.origem, coalesce(m.nome, '-') as quem
                    from lancamentos l left join membros m on m.id = l.membro_id
                    where {cond} order by l.data desc, l.id desc limit %s""",
                params + [limite]).fetchall()
        return [{"data": r[0], "descricao": r[1], "categoria": r[2], "tipo": r[3],
                 "valor": int(r[4]), "origem": r[5], "quem": r[6]} for r in rows]

    def raiox_por_departamento(self, dias: int = 90, membro_id: int | None = None) -> dict[str, list[dict]]:
        """Itens de cupom agrupados pelo DEPARTAMENTO (= categoria do lancamento)."""
        cond = "l.conta_id = %s and i.criado_em >= now() - (%s || ' days')::interval"
        params: list = [self.conta_id, dias]
        if membro_id is not None:
            cond += " and l.membro_id = %s"; params.append(membro_id)
        with self.pool.connection() as conn:
            rows = conn.execute(
                f"""select l.categoria, i.descricao, i.valor_total_centavos
                    from itens_lancamento i join lancamentos l on l.id = i.lancamento_id
                    where {cond} order by l.categoria, i.valor_total_centavos desc""",
                params).fetchall()
        dep: dict[str, list[dict]] = {}
        for cat, desc, val in rows:
            dep.setdefault(cat, []).append({"descricao": desc, "valor": int(val)})
        return dep

    # ---------- Itens do cupom (raio-x do consumo) ----------

    def buscar_duplicata(self, valor_centavos: int, data) -> list[dict]:
        """Procura lancamentos iguais (mesmo valor e data) - sinal de cupom repetido."""
        with self.pool.connection() as conn:
            rows = conn.execute(
                """select id, descricao, data, criado_em from lancamentos
                   where conta_id = %s and valor_centavos = %s and data = %s
                   order by criado_em desc""",
                (self.conta_id, valor_centavos, data),
            ).fetchall()
        return [{"id": r[0], "descricao": r[1], "data": r[2], "criado_em": r[3]} for r in rows]

    def ultimo_lancamento_id(self) -> int | None:
        """Id do lancamento mais recente do usuario (pra anexar itens 'desse cupom')."""
        with self.pool.connection() as conn:
            row = conn.execute(
                "select id from lancamentos where conta_id = %s order by id desc limit 1",
                (self.conta_id,),
            ).fetchone()
        return int(row[0]) if row else None

    def registrar_itens(self, lancamento_id: int, itens: list[dict]) -> int:
        """Salva os itens de um cupom, ligados a um lancamento do PROPRIO usuario.

        Cada item: {descricao, quantidade, valor_unitario_centavos, valor_total_centavos}.
        Retorna quantos itens foram salvos (0 se o lancamento nao for do usuario).
        """
        with self.pool.connection() as conn:
            dono = conn.execute(
                "select 1 from lancamentos where id = %s and conta_id = %s",
                (lancamento_id, self.conta_id),
            ).fetchone()
            if not dono:
                return 0
            n = 0
            for it in itens:
                conn.execute(
                    """insert into itens_lancamento
                       (lancamento_id, descricao, quantidade,
                        valor_unitario_centavos, valor_total_centavos)
                       values (%s,%s,%s,%s,%s)""",
                    (lancamento_id, it["descricao"], it.get("quantidade", 1),
                     int(it.get("valor_unitario_centavos", 0)),
                     int(it.get("valor_total_centavos", 0))),
                )
                n += 1
            conn.commit()
            return n

    def buscar_itens(self, termo: str, dias: int = 60) -> tuple[list[dict], int]:
        """Busca itens cuja descricao casa com 'termo' (nos ultimos N dias).

        Retorna (lista_de_itens, total_centavos).
        """
        corte = date.today() - timedelta(days=dias)
        with self.pool.connection() as conn:
            rows = conn.execute(
                """select i.descricao, i.quantidade, i.valor_total_centavos, l.data, i.criado_em
                   from itens_lancamento i join lancamentos l on l.id = i.lancamento_id
                   where l.conta_id = %s and i.descricao ilike %s and l.data >= %s
                   order by l.data desc, i.id desc""",
                (self.conta_id, f"%{termo}%", corte),
            ).fetchall()
        itens = [{"descricao": r[0], "quantidade": float(r[1]),
                  "valor_total_centavos": int(r[2]), "data": r[3], "criado_em": r[4]} for r in rows]
        total = sum(i["valor_total_centavos"] for i in itens)
        return itens, total

    def listar_itens(self, dias: int = 60, limite: int = 200) -> list[dict]:
        """Lista os itens dos ultimos N dias (pra perguntas por grupo, ex: 'frutas')."""
        corte = date.today() - timedelta(days=dias)
        with self.pool.connection() as conn:
            rows = conn.execute(
                """select i.descricao, i.quantidade, i.valor_total_centavos, l.data, i.criado_em
                   from itens_lancamento i join lancamentos l on l.id = i.lancamento_id
                   where l.conta_id = %s and l.data >= %s
                   order by l.data desc, i.id desc limit %s""",
                (self.conta_id, corte, limite),
            ).fetchall()
        return [{"descricao": r[0], "quantidade": float(r[1]),
                 "valor_total_centavos": int(r[2]), "data": r[3], "criado_em": r[4]} for r in rows]
