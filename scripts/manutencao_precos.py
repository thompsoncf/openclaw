"""CLI de manutencao do banco de precos (roda no Render, periodico ou manual).

    python -m scripts.manutencao_precos                 # arquiva antigos + recalcula vigentes
    python -m scripts.manutencao_precos --so-arquivar   # so' (A)
    python -m scripts.manutencao_precos --so-vigentes   # so' (B)
    python -m scripts.manutencao_precos --meses 12      # muda janela de arquivamento
"""
import os
import sys

from psycopg_pool import ConnectionPool

from finance.manutencao_precos import arquivar_antigos, recalcular_vigentes


def main():
    args = sys.argv[1:]
    meses = 6
    if "--meses" in args:
        try:
            meses = int(args[args.index("--meses") + 1])
        except (IndexError, ValueError):
            pass
    so_arquivar = "--so-arquivar" in args
    so_vigentes = "--so-vigentes" in args

    pool = ConnectionPool(os.environ["DATABASE_URL"], open=True)

    if not so_vigentes:
        n = arquivar_antigos(pool, meses=meses)
        print(f"(A) Arquivados {n} precos com mais de {meses} meses.")
    if not so_arquivar:
        n = recalcular_vigentes(pool, dias=90)
        print(f"(B) Recalculados {n} precos vigentes (mediana, ultimos 90 dias).")
    print("Manutencao concluida. ✓")


if __name__ == "__main__":
    main()
