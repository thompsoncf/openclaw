"""O DDL de runtime roda UMA VEZ POR PROCESSO — não a cada requisição.

Em 22/08/2026 três funções rodavam ALTER/CREATE/DROP em tabela viva a cada
requisição: 57 comandos no painel de serviços, 13 na equipe, 5 no catálogo. O
ACCESS EXCLUSIVE do ALTER enfileira até SELECT, e `contas`/`membros` são lidas em
toda requisição autenticada — o painel inteiro ficou lento pra todos os clientes.

Estes testes não precisam de banco: usam uma conexão de mentira que só conta os
`execute` que recebe. É isso que prova o conserto — o número de comandos DDL que
chega no banco na segunda chamada.
"""
import pytest

from core import esquema_runtime


class ConexaoFalsa:
    """Conta os execute. `info` imita psycopg (host/port/dbname) pra chave."""

    class _Info:
        def __init__(self, host, port, dbname):
            self.host, self.port, self.dbname = host, port, dbname

    def __init__(self, host="db.exemplo", port=5432, dbname="openclaw"):
        self.info = self._Info(host, port, dbname)
        self.comandos = []

    def execute(self, sql, *a, **k):
        self.comandos.append(" ".join(sql.split())[:60])

    def commit(self):
        pass


class PoolFalso:
    def __init__(self, conn, conninfo="postgresql://u:s@db.exemplo:5432/openclaw"):
        self._c, self.conninfo = conn, conninfo

    def connection(self):
        conn = self._c

        class Ctx:
            def __enter__(self):
                return conn

            def __exit__(self, *a):
                return False
        return Ctx()


@pytest.fixture(autouse=True)
def _limpo():
    esquema_runtime.esquecer()
    yield
    esquema_runtime.esquecer()


# --- o helper ---------------------------------------------------------------

def test_roda_na_primeira_e_pula_na_segunda():
    n = []
    assert esquema_runtime.garantir("k", lambda: n.append(1)) is True
    assert esquema_runtime.garantir("k", lambda: n.append(1)) is False
    assert esquema_runtime.garantir("k", lambda: n.append(1)) is False
    assert len(n) == 1


def test_falha_NAO_fica_marcada_como_feita():
    """DDL que quebrou tem que ser tentado de novo — senão um soluço no boot
    deixaria a tabela faltando pro resto da vida do processo."""
    def explode():
        raise RuntimeError("banco fora do ar")

    with pytest.raises(RuntimeError):
        esquema_runtime.garantir("k", explode)
    assert "k" not in esquema_runtime.feitos()

    n = []
    assert esquema_runtime.garantir("k", lambda: n.append(1)) is True
    assert len(n) == 1


def test_chave_separa_bancos_diferentes():
    """Processo que fala com dois bancos garante os dois — é o caso da suíte."""
    a = ConexaoFalsa(dbname="openclaw")
    b = ConexaoFalsa(dbname="openclaw_test")
    assert esquema_runtime.chave(a, "orcamentos") != esquema_runtime.chave(b, "orcamentos")

    n = []
    assert esquema_runtime.garantir(esquema_runtime.chave(a, "x"), lambda: n.append(1))
    assert esquema_runtime.garantir(esquema_runtime.chave(b, "x"), lambda: n.append(1))
    assert len(n) == 2


def test_chave_nao_carrega_senha():
    p = PoolFalso(ConexaoFalsa(), conninfo="postgresql://user:SEGREDO@db.exemplo:5432/openclaw")
    assert "SEGREDO" not in esquema_runtime.chave(p, "x")
    assert "user" not in esquema_runtime.chave(p, "x")


def test_chave_pela_conexao_e_pelo_pool_apontam_pro_mesmo_banco():
    c = ConexaoFalsa(host="db.exemplo", port=5432, dbname="openclaw")
    p = PoolFalso(c, conninfo="postgresql://u:s@db.exemplo:5432/openclaw")
    assert esquema_runtime.chave(c, "x") == esquema_runtime.chave(p, "x")


def test_esquecer_faz_rodar_de_novo():
    n = []
    esquema_runtime.garantir("k", lambda: n.append(1))
    esquema_runtime.esquecer()
    esquema_runtime.garantir("k", lambda: n.append(1))
    assert len(n) == 2


# --- os três chamadores de verdade ------------------------------------------

def test_painel_servicos_so_manda_ddl_uma_vez():
    from web.painel_servicos import _garantir_tabela
    c = ConexaoFalsa()
    _garantir_tabela(c)
    primeira = len(c.comandos)
    assert primeira >= 5, "esperava o bloco de DDL de orcamentos na primeira vez"
    _garantir_tabela(c)
    _garantir_tabela(c)
    assert len(c.comandos) == primeira, "DDL repetido depois da primeira chamada"


def test_equipe_so_manda_ddl_uma_vez():
    from contas import equipe
    c = ConexaoFalsa()
    p = PoolFalso(c)
    equipe.garantir_tabela(p)
    primeira = len(c.comandos)
    assert primeira >= 10, "esperava os 13 comandos de contas/membros"
    equipe.garantir_tabela(p)
    assert len(c.comandos) == primeira


def test_catalogo_so_manda_ddl_uma_vez():
    from finance import servicos_catalogo as cat
    c = ConexaoFalsa()
    p = PoolFalso(c)
    cat.garantir_tabela(p)
    primeira = len(c.comandos)
    assert primeira >= 3
    cat.garantir_tabela(p)
    assert len(c.comandos) == primeira


def test_bancos_diferentes_garantem_os_dois():
    """Se o processo troca de banco, o DDL roda no novo — não fica pulando."""
    from contas import equipe
    a, b = ConexaoFalsa(dbname="um"), ConexaoFalsa(dbname="dois")
    equipe.garantir_tabela(PoolFalso(a, "postgresql://u:s@db.exemplo:5432/um"))
    equipe.garantir_tabela(PoolFalso(b, "postgresql://u:s@db.exemplo:5432/dois"))
    assert a.comandos and b.comandos


def test_ddl_que_falha_e_tentado_de_novo_na_proxima_requisicao():
    """Prova ponta a ponta: se o primeiro ALTER quebra, a chamada seguinte
    reexecuta o bloco inteiro em vez de achar que já está pronto."""
    from finance import servicos_catalogo as cat

    class Quebra(ConexaoFalsa):
        def __init__(self):
            super().__init__()
            self.quebrar = True

        def execute(self, sql, *a, **k):
            if self.quebrar:
                self.quebrar = False
                raise RuntimeError("timeout no banco")
            super().execute(sql, *a, **k)

    c = Quebra()
    p = PoolFalso(c)
    with pytest.raises(RuntimeError):
        cat.garantir_tabela(p)
    assert not c.comandos
    cat.garantir_tabela(p)
    assert c.comandos, "o DDL tinha que ser retentado"
