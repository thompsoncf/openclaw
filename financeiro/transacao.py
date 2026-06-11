"""
Lógica financeira: registrar gastos, calcular saldo
"""
from db.conexao import get_conn, return_conn
from datetime import datetime

class Livro:
    def __init__(self, usuario_id: str):
        self.usuario_id = usuario_id

    def registrar_gasto(self, valor: float, categoria: str, descricao: str = ""):
        """Registra um gasto no livro caixa"""
        conn = get_conn()
        cursor = conn.cursor()

        try:
            cursor.execute(
                """
                INSERT INTO transacoes (usuario_id, categoria, valor, descricao)
                VALUES (%s, %s, %s, %s)
                RETURNING id, criado_em
                """,
                (self.usuario_id, categoria, valor, descricao)
            )
            resultado = cursor.fetchone()
            conn.commit()
            return {
                'id': resultado[0],
                'valor': valor,
                'categoria': categoria,
                'timestamp': resultado[1]
            }
        except Exception as e:
            conn.rollback()
            raise e
        finally:
            cursor.close()
            return_conn(conn)

    def saldo_total(self) -> float:
        """Calcula saldo total (soma de gastos)"""
        conn = get_conn()
        cursor = conn.cursor()

        try:
            cursor.execute(
                "SELECT COALESCE(SUM(valor), 0) FROM transacoes WHERE usuario_id = %s",
                (self.usuario_id,)
            )
            resultado = cursor.fetchone()
            return float(resultado[0]) if resultado else 0.0
        finally:
            cursor.close()
            return_conn(conn)

    def ultimas_transacoes(self, limite: int = 5):
        """Retorna as últimas N transações"""
        conn = get_conn()
        cursor = conn.cursor()

        try:
            cursor.execute(
                """
                SELECT id, categoria, valor, descricao, data_transacao
                FROM transacoes
                WHERE usuario_id = %s
                ORDER BY data_transacao DESC
                LIMIT %s
                """,
                (self.usuario_id, limite)
            )
            resultado = cursor.fetchall()
            return [
                {
                    'id': r[0],
                    'categoria': r[1],
                    'valor': float(r[2]),
                    'descricao': r[3],
                    'data': r[4]
                }
                for r in resultado
            ]
        finally:
            cursor.close()
            return_conn(conn)
