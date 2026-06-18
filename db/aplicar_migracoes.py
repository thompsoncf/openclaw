"""Aplica todas as migrações SQL da pasta db/migracoes/ em ordem.

Usa uma tabela de rastreamento (schema_migrations) para saber quais já rodaram.
Assim é seguro rodar N vezes — só executa as novas.

Uso:
    python -m db.aplicar_migracoes              # aplica pendentes
    python -m db.aplicar_migracoes --forcar     # reaplica tudo (perigoso!)
"""
import os
import sys
from pathlib import Path
from psycopg_pool import ConnectionPool

def criar_tabela_rastreamento(pool):
    """Cria a tabela schema_migrations se não existir."""
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """create table if not exists schema_migrations (
                    id serial primary key,
                    nome text unique not null,
                    executada_em timestamptz default now()
                )"""
            )
        conn.commit()

def migracao_ja_rodou(pool, nome: str) -> bool:
    """Verifica se uma migração já foi executada."""
    try:
        with pool.connection() as conn:
            # Use cursor explicitamente para evitar prepared statement reutilizado
            with conn.cursor() as cur:
                cur.execute(
                    "select 1 from schema_migrations where nome = %s", (nome,)
                )
                r = cur.fetchone()
        return r is not None
    except Exception:
        return False

def registrar_migracao(pool, nome: str):
    """Registra que uma migração foi executada."""
    try:
        with pool.connection() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "insert into schema_migrations (nome) values (%s)", (nome,)
                )
            conn.commit()
    except Exception as e:
        if "duplicate" not in str(e).lower():
            raise

def aplicar_migracoes(pool, forcar: bool = False):
    """Lê e executa todos os .sql da pasta migracoes/ em ordem numérica."""
    criar_tabela_rastreamento(pool)

    migracoes_dir = Path(__file__).parent / "migracoes"
    if not migracoes_dir.exists():
        print("Diretório de migrações não encontrado.")
        return 0

    arquivos = sorted(migracoes_dir.glob("*.sql"))
    if not arquivos:
        print("Nenhuma migração encontrada.")
        return 0

    com_execucao = []
    for arquivo in arquivos:
        nome = arquivo.name
        if not forcar and migracao_ja_rodou(pool, nome):
            print(f"⊘ {nome} (já rodou)")
            continue

        sql = arquivo.read_text(encoding="utf-8")
        try:
            with pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute(sql)
                conn.commit()
            registrar_migracao(pool, nome)
            com_execucao.append(nome)
            print(f"✓ {nome}")
        except Exception as e:
            # Se a tabela/índice já existe (migração já rodou no passado, sem rastreamento),
            # registra como concluída e continua (idempotência)
            if "already exists" in str(e).lower() or "duplicate" in str(e).lower():
                registrar_migracao(pool, nome)
                print(f"⊘ {nome} (já existia, registrada como concluída)")
            else:
                print(f"✗ {nome}: {e}")
                if not forcar:
                    raise
                print("  (continuando porque --forcar foi usado)")

    return len(com_execucao)

def main():
    forcar = "--forcar" in sys.argv
    if forcar:
        print("⚠️  Modo --forcar: vai reexecutar TODAS as migrações (perigoso!)")

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Falta DATABASE_URL na env.")
        sys.exit(1)

    pool = ConnectionPool(url, min_size=1, max_size=1, open=True)
    n = aplicar_migracoes(pool, forcar=forcar)
    print(f"\n✓ {n} migração(ões) executada(s).")
    pool.close()

if __name__ == "__main__":
    main()
