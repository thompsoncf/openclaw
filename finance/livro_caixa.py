"""Livro-caixa de UM usuario.

Todo metodo e' escopado por usuario_id: a pessoa so' enxerga e mexe no que e' dela.
Recebe o pool de conexoes e pega/devolve conexao a cada operacao.
"""
from datetime import date

from .models import (
    Lancamento, Tipo, centavos_para_reais, formatar_brl,
)


class LivroCaixa:
    def __init__(self, pool, usuario_id: int):
        self.pool = pool
        self.usuario_id = usuario_id

    def adicionar(self, lanc: Lancamento) -> Lancamento:
        with self.pool.connection() as conn:
            row = conn.execute(
                """insert into lancamentos
                   (usuario_id, tipo, valor_centavos, categoria, descricao,
                    data, pagamento, origem, comprovante)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                (self.usuario_id, lanc.tipo.value, lanc.valor_centavos,
                 lanc.categoria, lanc.descricao, lanc.data, lanc.pagamento,
                 lanc.origem, lanc.comprovante),
            ).fetchone()
            conn.commit()
            lanc.id = row[0]
            return lanc

    def listar(self, mes: int | None = None, ano: int | None = None, limite: int = 50) -> list[Lancamento]:
        sql = "select id, tipo, valor_centavos, categoria, descricao, data, pagamento, origem, comprovante from lancamentos where usuario_id = %s"
        params: list = [self.usuario_id]
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
                   from lancamentos where usuario_id = %s""",
                (self.usuario_id,),
            ).fetchone()
        return int(row[0])

    def total_por_categoria(self, tipo: Tipo, mes: int | None = None, ano: int | None = None) -> dict[str, int]:
        sql = "select categoria, sum(valor_centavos) from lancamentos where usuario_id = %s and tipo = %s"
        params: list = [self.usuario_id, Tipo(tipo).value]
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
                   where usuario_id = %s and tipo='despesa'
                   and extract(year from data)=%s and extract(month from data)=%s""",
                (self.usuario_id, ano, mes),
            ).fetchone()
        return int(row[0])
