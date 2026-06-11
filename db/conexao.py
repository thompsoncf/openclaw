"""
Conexão com Supabase e inicialização de schema
"""
import os
import psycopg2
from psycopg2 import pool
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv('DATABASE_URL')
_pool = None

def get_pool():
    global _pool
    if _pool is None:
        _pool = psycopg2.pool.SimpleConnectionPool(1, 5, DATABASE_URL)
    return _pool

def get_conn():
    return get_pool().getconn()

def return_conn(conn):
    get_pool().putconn(conn)

def init_schema(pool_obj):
    """Cria tabelas de uma vez (idempotente)"""
    conn = pool_obj.getconn()
    cursor = conn.cursor()

    try:
        # Tabela de transações financeiras
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS transacoes (
                id SERIAL PRIMARY KEY,
                usuario_id TEXT NOT NULL,
                categoria TEXT NOT NULL,
                valor NUMERIC(10, 2) NOT NULL,
                descricao TEXT,
                data_transacao TIMESTAMP DEFAULT NOW(),
                criado_em TIMESTAMP DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS idx_usuario_id ON transacoes(usuario_id);
            CREATE INDEX IF NOT EXISTS idx_data_transacao ON transacoes(data_transacao);
        """)

        # Tabela de cupons (foto) — para Fase 2
        cursor.execute("""
            CREATE TABLE IF NOT EXISTS cupons (
                id SERIAL PRIMARY KEY,
                usuario_id TEXT NOT NULL,
                url_foto TEXT,
                texto_extraido TEXT,
                valor NUMERIC(10, 2),
                categoria TEXT,
                processado BOOLEAN DEFAULT FALSE,
                criado_em TIMESTAMP DEFAULT NOW()
            );

            CREATE INDEX IF NOT EXISTS idx_cupom_usuario ON cupons(usuario_id);
        """)

        conn.commit()
        print("✅ Schema inicializado com sucesso")
    except Exception as e:
        conn.rollback()
        print(f"❌ Erro ao inicializar schema: {e}")
    finally:
        cursor.close()
        pool_obj.putconn(conn)
