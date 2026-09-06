"""Toda proposta nasce numerada, por qualquer porta.

O QUE ACONTECEU
Quatro portas criam orçamento — painel, app do vendedor, prospecção e agente — e
só o PAINEL numerava. As outras três inseriam sem `numero`, e a proposta só
ganhava o dela se alguém depois a abrisse e salvasse no painel. Quem criava e
mandava tudo pelo celular ficava com uma proposta sem número pra sempre, no card
e no funil.

Medido em 06/09/2026: existia UMA proposta criada pelo app em toda a produção
(`canal='cockpit'`), e era exatamente a única sem número — 1 de 35. Um caso só
porque o construtor do app é novo; o comunicado que acabou de sair manda os
vendedores trabalharem por lá, então a exceção viraria a regra.

O QUE ESTE ARQUIVO PRENDE
1. a série é POR CONTA e contínua — duas contas não disputam número;
2. colisão de número reexecuta e acerta na volta seguinte;
3. o retry usa SAVEPOINT: o que foi gravado antes na mesma transação sobrevive à
   colisão — no agente e no app a proposta nasce no meio de uma transação que já
   gravou a conversa e o lead;
4. esgotadas as tentativas, devolve None em vez de numerar errado — duas propostas
   com o mesmo número se confundem na hora de citar uma.
"""
import os

import pytest
from psycopg.errors import UniqueViolation
from psycopg_pool import ConnectionPool

from finance import vendas

_SQL = """
create table contas (id bigserial primary key, nome text);
create table orcamentos (id bigserial primary key, conta_id bigint, cliente text,
  numero int, criado_em timestamptz default now());
create unique index ux_orc_conta_numero on orcamentos (conta_id, numero);
create table rastro (id bigserial primary key, o_que text);
"""


@pytest.fixture()
def pool():
    dbname = "zaq_numero_ao_nascer"
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1,
                           open=True, kwargs={"autocommit": True, "prepare_threshold": None})
    with admin.connection() as c:
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True,
                       kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.execute("insert into contas (id, nome) values (34,'Prime'),(3,'ZAQ')")
        c.commit()
    yield p
    p.close()


def _criar(c, conta_id, nome="Cliente"):
    """Uma porta qualquer criando proposta — o mesmo desenho das quatro reais."""
    return vendas.com_retry_numero(c, lambda: c.execute(
        f"insert into orcamentos (conta_id, cliente, numero) values (%s,%s,{vendas.NUMERO_SQL}) "
        "returning id, numero", (conta_id, nome, conta_id)).fetchone())


def _numeros(pool, conta_id):
    with pool.connection() as c:
        return [r[0] for r in c.execute(
            "select numero from orcamentos where conta_id=%s order by id", (conta_id,))]


# ═══════════════ a série ═══════════════

def test_a_primeira_proposta_da_conta_e_a_numero_1(pool):
    with pool.connection() as c:
        r = _criar(c, 34)
        c.commit()
    assert r[1] == 1


def test_a_serie_e_continua(pool):
    with pool.connection() as c:
        for _ in range(5):
            _criar(c, 34)
        c.commit()
    assert _numeros(pool, 34) == [1, 2, 3, 4, 5]


def test_cada_conta_tem_a_propria_serie(pool):
    """Duas contas não disputam número: a Prime na 8 não empurra a ZAQ pra 9."""
    with pool.connection() as c:
        for _ in range(3):
            _criar(c, 34)
        for _ in range(2):
            _criar(c, 3)
        c.commit()
    assert _numeros(pool, 34) == [1, 2, 3]
    assert _numeros(pool, 3) == [1, 2]


def test_continua_de_onde_a_conta_parou(pool):
    """Proposta antiga já numerada no painel: a nova vem depois dela, não do 1."""
    with pool.connection() as c:
        c.execute("insert into orcamentos (conta_id, cliente, numero) values (34,'antiga',17)")
        r = _criar(c, 34)
        c.commit()
    assert r[1] == 18


# ═══════════════ a colisão ═══════════════

def test_colisao_reexecuta_e_acerta_na_volta(pool):
    """Dois salvando no mesmo instante: o perdedor leva UniqueViolation e, na
    segunda volta, o max já é o do vencedor.

    O VENCEDOR ENTRA POR OUTRA CONEXÃO, e isso não é detalhe de montagem — é o
    cenário. Numa primeira versão deste teste eu gravava o vencedor na MESMA
    conexão, dentro do savepoint que a colisão desfaz: ele voltava junto, o max
    caía pra zero e o perdedor tirava o número 1. O teste acusou o próprio teste.
    """
    tentativas = {"n": 0}

    def executar_colidindo(c):
        tentativas["n"] += 1
        if tentativas["n"] == 1:
            # o "outro" gravou e COMITOU entre a leitura do max e a gravação
            with pool.connection() as outra:
                outra.execute("insert into orcamentos (conta_id, cliente, numero) "
                              "values (34,'vencedor',1)")
                outra.commit()
            raise UniqueViolation("duplicate key value violates unique constraint")
        return c.execute(
            f"insert into orcamentos (conta_id, cliente, numero) values (34,'perdedor',{vendas.NUMERO_SQL}) "
            "returning numero", (34,)).fetchone()

    with pool.connection() as c:
        r = vendas.com_retry_numero(c, lambda: executar_colidindo(c))
        c.commit()
    assert tentativas["n"] == 2
    assert r[0] == 2, "na segunda volta o max já era o do vencedor"
    assert _numeros(pool, 34) == [1, 2], "a série ficou com buraco ou repetida"


def test_esgotadas_as_tentativas_devolve_none_em_vez_de_numerar_errado(pool):
    def sempre_colide():
        raise UniqueViolation("duplicate key")
    with pool.connection() as c:
        assert vendas.com_retry_numero(c, sempre_colide, tentativas=3) is None


def test_outro_erro_nao_e_engolido(pool):
    """Só colisão de número reexecuta. Qualquer outra falha sobe — retry em erro
    de banco genérico mascararia um problema de verdade três vezes seguidas."""
    def quebra():
        raise RuntimeError("outra coisa")
    with pool.connection() as c:
        with pytest.raises(RuntimeError):
            vendas.com_retry_numero(c, quebra)


# ═══════════════ o savepoint ═══════════════

def test_a_colisao_nao_apaga_o_que_veio_antes_na_transacao(pool):
    """A RAZÃO DO SAVEPOINT. A versão antiga fazia `c.rollback()`, que desfaz a
    transação inteira. No agente a proposta nasce depois de a conversa já ter sido
    gravada na mesma transação — uma colisão de número apagaria a conversa junto."""
    def colide_uma_vez():
        if not colide_uma_vez.ja:
            colide_uma_vez.ja = True
            raise UniqueViolation("duplicate key")
        return "ok"
    colide_uma_vez.ja = False

    with pool.connection() as c:
        c.execute("insert into rastro (o_que) values ('a conversa, gravada antes')")
        r = vendas.com_retry_numero(c, colide_uma_vez)
        c.commit()

    assert r == "ok"
    with pool.connection() as c:
        n = c.execute("select count(*) from rastro").fetchone()[0]
    assert n == 1, "a colisão de número apagou o que já estava gravado na transação"


def test_a_tentativa_que_colidiu_e_desfeita_por_inteiro(pool):
    """O outro lado do savepoint: o que a tentativa falhada escreveu antes de
    colidir não pode vazar pra tentativa seguinte."""
    vez = {"n": 0}

    def executar(c):
        vez["n"] += 1
        c.execute("insert into rastro (o_que) values (%s)", (f"tentativa {vez['n']}",))
        if vez["n"] == 1:
            raise UniqueViolation("duplicate key")
        return "ok"

    with pool.connection() as c:
        vendas.com_retry_numero(c, lambda: executar(c))
        c.commit()
    with pool.connection() as c:
        rastro = [r[0] for r in c.execute("select o_que from rastro order by id")]
    assert rastro == ["tentativa 2"], f"vazou o rastro da tentativa falhada: {rastro}"
