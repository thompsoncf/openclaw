"""Conexao com o Postgres.

Usa um pool de conexoes (varios usuarios mexendo ao mesmo tempo sem engasgar).
A URL do banco vem da variavel de ambiente DATABASE_URL, por exemplo:
    postgresql://openclaw:senha@localhost:5432/openclaw
"""
import os
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path

from psycopg_pool import ConnectionPool

_pool: ConnectionPool | None = None

# UMA CONEXAO POR REQUISICAO (20/09/2026).
#
# Medido no celular do dono: a Fila abria 16 CONEXOES pra desenhar uma tela. Cada
# uma paga um `SELECT 1` de verificacao (o `check` do pool, mais abaixo) — e como
# o servico esta em Oregon e o banco em us-east-1, essa verificacao custa os
# mesmos ~100 ms de qualquer consulta. Eram ~1,6 s dos 4,0 s de banco da Fila
# gastos so em pedagio de conexao, sem desenhar nada.
#
# Agora a requisicao pega UMA conexao na primeira vez que alguem pede e devolve
# ao pool no fim. Os `with pool.connection()` espalhados pelo app continuam
# escritos do mesmo jeito — quem muda e o que eles recebem.
#
# O QUE NAO MUDA, de proposito: cada bloco continua CONFIRMANDO o proprio
# trabalho ao sair, como o pool fazia. Sem isso, uma falha no fim da requisicao
# desfaria o que ja tinha sido salvo no comeco — o tipo de mudanca que quebra em
# silencio e so aparece no dia ruim.
#
# Dict MUTAVEL dentro do ContextVar pelo mesmo motivo do db/medicao.py: as rotas
# sao `def`, o Starlette as roda numa thread com uma COPIA do contexto, e um
# `.set()` feito la dentro morreria na copia. Mexer no mesmo dict funciona dos
# dois lados.
_REQ: ContextVar[dict | None] = ContextVar("conexao_da_requisicao", default=None)

#: porta de saida sem deploy: `PG_CONEXAO_POR_REQUISICAO=0` volta ao de antes
def _ligado() -> bool:
    return os.environ.get("PG_CONEXAO_POR_REQUISICAO", "1") != "0"


def abrir_requisicao():
    """Comeca a reaproveitar conexao. Devolve o token pro `fechar_requisicao`."""
    return _REQ.set({"conn": None, "cm": None}) if _ligado() else None


def memo(chave, produtor):
    """Lembra o resultado de uma leitura DENTRO da requisicao.

    So pra dado de CONFIGURACAO que a tela le mais de uma vez — o cadastro da
    conta, o nicho — nunca pra lista que muda. A Fila lia a tabela `contas` duas
    vezes por carga (uma no cadastro da empresa, outra pro nicho das novidades):
    ~110 ms cross-region pra buscar a mesma linha de novo.

    A lembranca morre com a requisicao: fora dela (poller, crons, scripts) isto e
    uma chamada direta, sem cache nenhum — dado velho preso na memoria de um
    processo que vive dias seria bem pior que uma consulta a mais.
    """
    cofre = _REQ.get()
    if cofre is None:
        return produtor()
    lembradas = cofre.setdefault("memo", {})
    if chave not in lembradas:
        lembradas[chave] = produtor()
    return lembradas[chave]


def fechar_requisicao(token) -> None:
    """Devolve a conexao da requisicao ao pool. Nunca levanta: uma falha aqui
    viraria erro 500 numa tela que ja tinha sido desenhada."""
    if token is None:
        return
    cofre = _REQ.get() or {}
    _REQ.reset(token)
    cm = cofre.get("cm")
    if cm is None:
        return
    try:
        cm.__exit__(None, None, None)
    except Exception:  # noqa: BLE001
        pass


class _PoolComConta(ConnectionPool):
    """Pool que ANUNCIA de quem e a requisicao (app.conta_id) na conexao.

    O gancho fica aqui, no unico lugar onde conexao e entregue, em vez de nas
    centenas de `pool.connection()` espalhadas pelo app.

    Com RLS_SET_CONTA desligado (o padrao) isto e' um `if` e nada mais: mesmo
    caminho de antes. Ver db/tenant.py pro porque.
    """

    def getconn(self, timeout: float | None = None):
        conn = super().getconn(timeout)
        try:
            from db import medicao as _m
            _m.contar_conexao()
            from db import tenant as _t
            _t.aplicar(conn)
        except Exception:  # noqa: BLE001 - nunca impedir a entrega da conexao
            pass
        return conn

    @contextmanager
    def connection(self, timeout: float | None = None):
        """A conexao da REQUISICAO, quando ha uma; senao, o caminho de sempre.

        Fora de requisicao (poller, crons, scripts, suite) `_REQ` e None e isto e
        literalmente o comportamento anterior."""
        cofre = _REQ.get()
        if cofre is None:
            with super().connection(timeout) as conn:
                yield conn
            return
        conn = cofre.get("conn")
        if conn is None:
            # segura a conexao ate o fim da requisicao (ver `fechar_requisicao`)
            cm = super().connection(timeout)
            conn = cm.__enter__()
            cofre["cm"], cofre["conn"] = cm, conn
        try:
            yield conn
            conn.commit()      # cada bloco confirma o seu, como o pool fazia
        except BaseException:
            conn.rollback()
            raise


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError("DATABASE_URL nao configurada (veja o .env.example).")
        # prepare_threshold=None desliga prepared statements no servidor.
        # E' o que faz o pooler do Supabase (e qualquer pgbouncer) funcionar
        # sem erros misteriosos. Custo de performance e' desprezivel no piloto.
        #
        # check=check_connection: antes de entregar uma conexao, o pool testa
        # se ela esta viva (o pooler do Supabase fecha conexoes ociosas). Se
        # estiver morta, o pool descarta e abre outra - some o "connection is bad".
        # max_idle: fecha conexoes paradas ha mais de 2 min, antes do Supabase.
        # connection_class: a conexão que soma as próprias consultas na medição
        # por requisição (db/medicao.py). Fora de requisição medida é um `if`.
        from db.medicao import ConexaoMedida
        _pool = _PoolComConta(
            url, connection_class=ConexaoMedida, min_size=1, max_size=10, open=True,
            max_idle=120,
            check=ConnectionPool.check_connection,
            # REDE DE SEGURANCA DA CONEXAO POR REQUISICAO (20/09/2026).
            # Agora a conexao atravessa a requisicao inteira; se algum caminho
            # deixar uma transacao aberta, ela ficaria pendurada segurando a
            # conexao (o classico "idle in transaction") ate o pooler cansar.
            # 60 s e folgado pra qualquer tela e curto pra qualquer vazamento.
            # NAO poe statement_timeout aqui: o mesmo `get_pool` serve os crons,
            # e relatorio pesado tem direito de demorar.
            kwargs={"prepare_threshold": None,
                    "options": "-c idle_in_transaction_session_timeout=60s"},
        )
    return _pool


def init_schema(pool: ConnectionPool | None = None):
    """Cria as tabelas se ainda nao existirem. Seguro rodar varias vezes."""
    pool = pool or get_pool()
    schema = (Path(__file__).parent / "schema.sql").read_text(encoding="utf-8")
    with pool.connection() as conn:
        conn.execute(schema)
        conn.commit()
