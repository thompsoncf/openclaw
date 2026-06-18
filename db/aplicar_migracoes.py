"""Aplica todas as migrações SQL da pasta db/migracoes/ em ordem.

Uso:
    python -m db.aplicar_migracoes              # aplica todas as pendentes
    python -m db.aplicar_migracoes --forcar     # reaplica tudo (perigoso!)
"""
import os
import sys
from pathlib import Path
from psycopg_pool import ConnectionPool

def aplicar_migracoes(pool, forcar: bool = False):
    """Lê e executa todos os .sql da pasta migracoes/ em ordem numérica."""
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
        sql = arquivo.read_text(encoding="utf-8")
        try:
            with pool.connection() as conn:
                conn.execute(sql)
                conn.commit()
            com_execucao.append(arquivo.name)
            print(f"✓ {arquivo.name}")
        except Exception as e:
            print(f"✗ {arquivo.name}: {e}")
            if forcar:
                print("  (continuando porque --forcar foi usado)")
            else:
                raise

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
