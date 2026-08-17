"""Aplica todas as migrações SQL da pasta db/migracoes/ em ordem.

Usa uma tabela de rastreamento (schema_migrations) para saber quais já rodaram.
Assim é seguro rodar N vezes — só executa as novas.

Uso:
    python -m db.aplicar_migracoes              # aplica pendentes
    python -m db.aplicar_migracoes --forcar     # reaplica tudo (perigoso!)
"""
import os
import sys
import time
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
    # NUNCA responder False por causa de um erro. "Não sei" não é "não rodou": o
    # chamador reexecuta a migração, e reexecutar migração é destrutivo por
    # natureza (drop/recreate de constraint, backfill que soma de novo).
    #
    # Aconteceu em 17/ago: três migrações registradas desde julho (090, 109, 127)
    # foram reexecutadas num deploy porque este SELECT falhou e o except respondeu
    # "não rodou". A 127 derruba e recria o check de prospeccao_atividades sem
    # 'engajamento', valor que a 169 passou a usar — e o pre-deploy morreu com
    # "violated by some row", deixando o serviço no código anterior.
    #
    # A falha do SELECT em si é esperada de vez em quando: o pooler do Supabase é
    # transaction-mode, e o prepared statement que o psycopg cria depois de
    # algumas execuções pode não existir no backend que atende a próxima. Por isso
    # a tentativa é repetida com `prepare=False` antes de desistir — e, se ainda
    # assim falhar, o erro SOBE e o deploy pára. Deploy que pára é problema; deploy
    # que reaplica migração antiga em silêncio é incidente.
    ultimo = None
    for tentativa in range(3):
        try:
            with pool.connection() as conn:
                with conn.cursor() as cur:
                    cur.execute("select 1 from schema_migrations where nome = %s",
                                (nome,), prepare=False)
                    return cur.fetchone() is not None
        except Exception as e:  # noqa: BLE001 — relançado abaixo se insistir
            ultimo = e
            time.sleep(0.4 * (tentativa + 1))
    raise RuntimeError(
        f"não deu pra saber se a migração {nome} já rodou ({ultimo}). "
        "Abortando: reexecutar migração já aplicada pode destruir dado."
    ) from ultimo

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

# Chave arbitrária do advisory lock (serializa deploys/instâncias concorrentes).
_LOCK_MIGRACOES = 728194


def _obter_lock(lock_conn, tentativas: int = 12, espera: float = 1.5) -> bool:
    """Tenta o advisory lock SEM bloquear (pg_try_advisory_lock), com algumas
    tentativas curtas. Retorna True se pegou (a transação fica ABERTA segurando o
    lock/backend até o unlock no fim).

    Por que não pg_advisory_lock (bloqueante)? Os dois preDeployCommand do Render
    (web + worker) sobem juntos: um pega o lock, o outro ficava BLOQUEADO em
    pg_advisory_lock e era morto pelo statement_timeout do banco ("canceling
    statement due to statement timeout"). Com o try não-bloqueante isso não
    acontece: ou espera educadamente o outro terminar, ou segue mesmo assim —
    as migrações são idempotentes e o próprio Postgres serializa o DDL.
    """
    for i in range(tentativas):
        try:
            with lock_conn.cursor() as cur:
                cur.execute("select pg_try_advisory_lock(%s)", (_LOCK_MIGRACOES,))
                if cur.fetchone()[0]:
                    return True     # deixa a transação aberta segurando o lock
        except Exception:
            pass
        lock_conn.rollback()        # solta a transação/backend e tenta de novo
        if i < tentativas - 1:
            time.sleep(espera)
    return False


def aplicar_migracoes(pool, forcar: bool = False):
    """Lê e executa todos os .sql da pasta migracoes/ em ordem numérica.

    Serializado (best-effort) por um advisory lock não-bloqueante: se dois
    processos (web e worker do Render, ou 2 instâncias) deployarem juntos, um
    espera o outro. Se o lock não vier a tempo, segue mesmo assim — as migrações
    são idempotentes (if not exists / registro em schema_migrations) e o Postgres
    serializa o DDL, então não há corrida perigosa.
    """
    criar_tabela_rastreamento(pool)

    with pool.connection() as lock_conn:
        pegou = _obter_lock(lock_conn)
        if not pegou:
            print("⚠ advisory lock ocupado (outra instância migrando?) — "
                  "seguindo mesmo assim; migrações são idempotentes.")
        try:
            return _aplicar(pool, forcar)
        finally:
            if pegou:
                try:
                    lock_conn.execute("select pg_advisory_unlock(%s)",
                                      (_LOCK_MIGRACOES,))
                    lock_conn.commit()
                except Exception:
                    pass


def _aplicar(pool, forcar: bool = False):
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
                    # migração não pode morrer por statement_timeout curto do
                    # banco (Supabase/Render impõem um); SET LOCAL vale só nesta
                    # transação, sem afetar as conexões normais do app.
                    cur.execute("set local statement_timeout = 0")
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
        # Trava de segurança: --forcar exige confirmação explícita
        if os.environ.get("PERMITIR_FORCAR") != "SIM":
            print("BLOQUEADO: --forcar pode APAGAR dados (cascade/drop).")
            print("Se tem certeza E é banco descartável, rode com:")
            print("  PERMITIR_FORCAR=SIM python -m db.aplicar_migracoes --forcar")
            sys.exit(1)

    url = os.environ.get("DATABASE_URL")
    if not url:
        print("Falta DATABASE_URL na env.")
        sys.exit(1)

    # max_size=2: uma conexão segura o advisory lock, a outra roda as migrações.
    pool = ConnectionPool(url, min_size=1, max_size=2, open=True)
    n = aplicar_migracoes(pool, forcar=forcar)
    print(f"\n✓ {n} migração(ões) executada(s).")
    pool.close()

if __name__ == "__main__":
    main()
