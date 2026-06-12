"""Livro-caixa de UM usuario dentro de UMA CONTA.

Todo metodo e' escopado por conta_id: a pessoa so' enxerga e mexe no que e' dela.
O membro_id (autor) e' registrado pra auditoria. Recebe o pool de conexoes e
pega/devolve conexao a cada operacao.
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
        """Id do lancamento mais recente da conta (pra anexar itens 'desse cupom')."""
        with self.pool.connection() as conn:
            row = conn.execute(
                "select id from lancamentos where conta_id = %s order by id desc limit 1",
                (self.conta_id,),
            ).fetchone()
        return int(row[0]) if row else None

    def registrar_itens(self, lancamento_id: int, itens: list[dict]) -> int:
        """Salva os itens de um cupom, ligados a um lancamento da PROPRIA CONTA.

        Cada item: {descricao, quantidade, valor_unitario_centavos, valor_total_centavos}.
        Retorna quantos itens foram salvos (0 se o lancamento nao for da conta).
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
