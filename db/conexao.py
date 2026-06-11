"""Conexao com o Postgres.

Usa um pool de conexoes (varios usuarios mexendo ao mesmo tempo sem engasgar).
A URL do banco vem da variavel de ambiente DATABASE_URL, por exemplo:
    postgresql://openclaw:senha@localhost:5432/openclaw
"""
import os
from pathlib import Path
from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError("DATABASE_URL nao configurada (veja o .env.example).")
        # prepare_threshold=None desliga prepared statements no servidor.
        # E' o que faz o pooler do Supabase (e qualquer pgbouncer) funcionar
        # sem erros misteriosos. Custo de performance e' desprezivel no piloto.
        _pool = ConnectionPool(
            url, min_size=1, max_size=10, open=True,
            kwargs={"prepare_threshold": None},
        )
    return _pool


def init_schema(pool: ConnectionPool | None = None):
    """Cria as tabelas se ainda nao existirem. Seguro rodar varias vezes."""
    pool = pool or get_pool()
    schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    with pool.connection() as conn:
        conn.execute(schema)
        conn.commit()
