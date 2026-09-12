"""Fundir duas etapas (finance/funil_fusao): os leads de uma passam para a outra.

Nasceu do Projeto Adaptado da Prime (12/09/2026), que pede NEGOCIAÇÃO como coluna
única — na conta 34, juntar "Agendado Visita" (14 leads) e "Proposta" (20).

O que estes testes protegem, em uma frase cada:
  * nenhum lead some: o que sai de uma etapa chega inteiro na outra;
  * cada lead movido deixa linha no histórico, com autor — é o que torna desfazível;
  * a etapa de origem NÃO é apagada: sai do quadro e continua existindo;
  * etapa fixa não funde, e fundir numa etapa inexistente não faz nada;
  * desfazer devolve só quem a fusão levou, e não desfaz a venda que aconteceu depois;
  * uma conta não alcança a outra.
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import funil_fusao as ff

CONTA = 91
OUTRA = 92

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text,
  contato text, status text default 'novo', estagio text default 'lead',
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table funil_etapas (id bigserial primary key, conta_id bigint, chave text,
  rotulo text, ordem int not null default 0, fixa boolean not null default false,
  sai_do_quadro boolean not null default false, unique (conta_id, chave));
create table funil_movimentos (id bigserial primary key, conta_id bigint,
  prospeccao_id bigint, de text, para text, motivo text, membro_id bigint,
  criado_em timestamptz default now());
"""

_ETAPAS = (("novo", "Novo", 0, True), ("contatado", "Contatado", 10, False),
           ("qualificado", "Agendado Visita", 30, False),
           ("proposta", "Proposta", 40, False),
           ("ganho", "Fechado", 900, True), ("perdido", "Perdido", 910, True))


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_funil_fusao_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def limpo(pool):
    with pool.connection() as c:
        c.execute("delete from funil_movimentos")
        c.execute("delete from prospeccao")
        c.execute("delete from funil_etapas")
        for conta in (CONTA, OUTRA):
            for ch, rot, ordem, fixa in _ETAPAS:
                c.execute("""insert into funil_etapas (conta_id, chave, rotulo, ordem, fixa)
                             values (%s,%s,%s,%s,%s)""", (conta, ch, rot, ordem, fixa))
        c.commit()
    return pool


def _leads(pool, chave, n, conta=CONTA, estagio="lead"):
    with pool.connection() as c:
        for i in range(n):
            c.execute("""insert into prospeccao (conta_id, contato, status, estagio)
                         values (%s,%s,%s,%s)""", (conta, f"{chave}-{i}", chave, estagio))
        c.commit()


def _conta_em(pool, chave, conta=CONTA):
    with pool.connection() as c:
        return c.execute("""select count(*) from prospeccao
                             where conta_id=%s and status=%s and estagio='lead'""",
                         (conta, chave)).fetchone()[0]


def _fora_do_quadro(pool, chave, conta=CONTA):
    with pool.connection() as c:
        return c.execute("select sai_do_quadro from funil_etapas where conta_id=%s and chave=%s",
                         (conta, chave)).fetchone()[0]


def _movimentos(pool, conta=CONTA):
    with pool.connection() as c:
        return c.execute("""select de, para, motivo, membro_id from funil_movimentos
                             where conta_id=%s order by id""", (conta,)).fetchall()


# ------------------------------------------------------------------ o plano

def test_o_plano_conta_os_leads_sem_mexer_em_nada(limpo):
    _leads(limpo, "qualificado", 14)
    with limpo.connection() as c:
        p = ff.plano(c, CONTA, "qualificado", "proposta")
    assert (p["leads"], p["erro"]) == (14, None)
    assert p["rotulo_de"] == "Agendado Visita" and p["rotulo_para"] == "Proposta"
    assert _conta_em(limpo, "qualificado") == 14, "o plano mexeu no banco"


def test_o_plano_recusa_o_que_nao_pode(limpo):
    with limpo.connection() as c:
        assert ff.plano(c, CONTA, "qualificado", "nao_existe")["erro"] == "etapa"
        assert ff.plano(c, CONTA, "qualificado", "qualificado")["erro"] == "mesma"
        assert ff.plano(c, CONTA, "novo", "proposta")["erro"] == "fixa"
        assert ff.plano(c, CONTA, "ganho", "proposta")["erro"] == "fixa"


# ------------------------------------------------------------------ fundir

def test_o_caso_da_prime_os_34_viram_uma_coluna(limpo):
    _leads(limpo, "qualificado", 14)
    _leads(limpo, "proposta", 20)
    with limpo.connection() as c:
        r = ff.fundir(c, CONTA, "qualificado", "proposta", membro_id=7)
        c.commit()
    assert r["movidos"] == 14
    assert _conta_em(limpo, "proposta") == 34, "os 34 não chegaram inteiros"
    assert _conta_em(limpo, "qualificado") == 0


def test_cada_lead_movido_deixa_linha_no_historico_com_autor(limpo):
    """Sem isto o relatório contaria que 14 pessoas avançaram num segundo, e
    ninguém saberia de onde vieram — nem pra desfazer."""
    _leads(limpo, "qualificado", 3)
    with limpo.connection() as c:
        ff.fundir(c, CONTA, "qualificado", "proposta", membro_id=7)
        c.commit()
    movs = _movimentos(limpo)
    assert len(movs) == 3
    assert all(m == ("qualificado", "proposta", "fusao", 7) for m in movs)


def test_a_etapa_de_origem_nao_e_apagada(limpo):
    """Os movimentos antigos apontam pra chave dela; apagar deixaria o passado órfão."""
    _leads(limpo, "qualificado", 2)
    with limpo.connection() as c:
        ff.fundir(c, CONTA, "qualificado", "proposta")
        c.commit()
        existe = c.execute("select count(*) from funil_etapas where conta_id=%s and chave='qualificado'",
                           (CONTA,)).fetchone()[0]
    assert existe == 1, "a etapa foi apagada"
    assert _fora_do_quadro(limpo, "qualificado") is True


def test_fundir_etapa_vazia_so_tira_do_quadro(limpo):
    with limpo.connection() as c:
        r = ff.fundir(c, CONTA, "qualificado", "proposta")
        c.commit()
    assert r["movidos"] == 0 and _movimentos(limpo) == []
    assert _fora_do_quadro(limpo, "qualificado") is True


def test_fundir_nao_arrasta_quem_nao_e_lead(limpo):
    """A base de captados usa o mesmo campo `status` com outro sentido."""
    _leads(limpo, "qualificado", 2)
    _leads(limpo, "qualificado", 5, estagio="base")
    with limpo.connection() as c:
        assert ff.fundir(c, CONTA, "qualificado", "proposta")["movidos"] == 2
        c.commit()
    with limpo.connection() as c:
        n = c.execute("""select count(*) from prospeccao
                          where conta_id=%s and status='qualificado' and estagio='base'""",
                      (CONTA,)).fetchone()[0]
    assert n == 5, "a fusão arrastou a base de captados"


def test_erro_nao_move_nada(limpo):
    _leads(limpo, "qualificado", 4)
    with limpo.connection() as c:
        assert ff.fundir(c, CONTA, "qualificado", "nao_existe")["movidos"] == 0
        c.commit()
    assert _conta_em(limpo, "qualificado") == 4
    assert _movimentos(limpo) == []


def test_uma_conta_nao_alcanca_a_outra(limpo):
    _leads(limpo, "qualificado", 3, conta=CONTA)
    _leads(limpo, "qualificado", 9, conta=OUTRA)
    with limpo.connection() as c:
        ff.fundir(c, CONTA, "qualificado", "proposta")
        c.commit()
    assert _conta_em(limpo, "qualificado", OUTRA) == 9, "a fusão vazou pra outra conta"
    assert _fora_do_quadro(limpo, "qualificado", OUTRA) is False


# ------------------------------------------------------------------ desfazer

def test_desfazer_devolve_quem_a_fusao_levou(limpo):
    _leads(limpo, "qualificado", 5)
    _leads(limpo, "proposta", 2)          # já estavam lá antes
    with limpo.connection() as c:
        ff.fundir(c, CONTA, "qualificado", "proposta")
        c.commit()
        r = ff.desfazer(c, CONTA, "qualificado", "proposta")
        c.commit()
    assert r["voltaram"] == 5
    assert _conta_em(limpo, "qualificado") == 5
    assert _conta_em(limpo, "proposta") == 2, "levou junto quem já estava em Proposta"
    assert _fora_do_quadro(limpo, "qualificado") is False


def test_desfazer_nao_desfaz_a_venda_que_aconteceu_depois(limpo):
    """O lead que a fusão levou pra Proposta e que FECHOU no meio do caminho fica
    fechado. Desfazer uma mudança de coluna não pode desfazer uma venda."""
    _leads(limpo, "qualificado", 3)
    with limpo.connection() as c:
        ff.fundir(c, CONTA, "qualificado", "proposta")
        c.commit()
        c.execute("""update prospeccao set status='ganho'
                      where conta_id=%s and status='proposta'
                      and id = (select min(id) from prospeccao where conta_id=%s and status='proposta')""",
                  (CONTA, CONTA))
        c.commit()
        r = ff.desfazer(c, CONTA, "qualificado", "proposta")
        c.commit()
    assert r["voltaram"] == 2
    assert _conta_em(limpo, "ganho") == 1, "desfazer arrastou de volta quem já tinha fechado"
