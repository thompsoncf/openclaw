"""O backfill renomeia só quem nasceu com o número no lugar do nome.

O risco de um script assim é passar por cima de nome bom. A seleção é a parte que
importa: lead com nome de verdade não pode ser tocado, nem que o nome seja curto,
esquisito ou pareça um código.
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from scripts.backfill_nome_lead import _candidatos

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text not null,
  contato text, whatsapp text, telefone text, atualizado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text, contato_nome text, chip_id bigint);
create table wa_contatos (conta_id bigint, numero8 text, nome text,
  primary key (conta_id, numero8));
"""

CONTA = 4


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_backfill_nome_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=2, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.commit()
    yield p
    p.close()


def _lead(c, empresa, whats="+558694867388", contato=None):
    return c.execute("insert into prospeccao (conta_id, empresa, whatsapp, contato) "
                     "values (%s,%s,%s,%s) returning id",
                     (CONTA, empresa, whats, contato)).fetchone()[0]


def test_renomeia_pela_agenda_do_celular(pool):
    with pool.connection() as c:
        lead = _lead(c, "558694867388")
        c.execute("insert into wa_contatos (conta_id, numero8, nome) values (%s,%s,%s)",
                  (CONTA, "94867388", "Mercado Avenida"))
        c.commit()
        achados = _candidatos(c)
    assert [(r[0], r[3], r[4]) for r in achados] == [(lead, "Mercado Avenida", "agenda")]


def test_agenda_ganha_do_nome_da_conversa(pool):
    with pool.connection() as c:
        lead = _lead(c, "+55 86 9486-7388")
        c.execute("insert into wa_contatos (conta_id, numero8, nome) values (%s,%s,%s)",
                  (CONTA, "94867388", "Mercado Avenida"))
        c.execute("insert into conversas (conta_id, prospeccao_id, canal, contato_nome) "
                  "values (%s,%s,'whatsapp','Merc Avenida')", (CONTA, lead))
        c.commit()
        achados = _candidatos(c)
    assert achados[0][3] == "Mercado Avenida"


def test_sem_agenda_usa_o_nome_da_conversa(pool):
    with pool.connection() as c:
        lead = _lead(c, "558694867388")
        c.execute("insert into conversas (conta_id, prospeccao_id, canal, contato_nome) "
                  "values (%s,%s,'whatsapp','Joana Ribeiro')", (CONTA, lead))
        c.commit()
        achados = _candidatos(c)
    assert [(r[0], r[3], r[4]) for r in achados] == [(lead, "Joana Ribeiro", "conversa")]


def test_nao_encosta_em_lead_que_ja_tem_nome(pool):
    with pool.connection() as c:
        _lead(c, "Mercado Avenida")
        _lead(c, "Padaria 24h")          # tem dígito, mas não é só número
        _lead(c, "Loja 5586")
        c.execute("insert into wa_contatos (conta_id, numero8, nome) values (%s,%s,%s)",
                  (CONTA, "94867388", "Outro Nome"))
        c.commit()
        assert _candidatos(c) == []


def test_numero_curto_demais_nao_conta_como_numero(pool):
    """Um lead chamado '123' não é telefone nenhum — melhor não adivinhar."""
    with pool.connection() as c:
        _lead(c, "123")
        c.execute("insert into wa_contatos (conta_id, numero8, nome) values (%s,%s,%s)",
                  (CONTA, "94867388", "Mercado Avenida"))
        c.commit()
        assert _candidatos(c) == []


def test_sem_nome_em_lugar_nenhum_fica_como_esta(pool):
    with pool.connection() as c:
        _lead(c, "558694867388")
        c.commit()
        assert _candidatos(c) == []


def test_sem_aplicar_nao_grava_nada(pool, monkeypatch):
    import scripts.backfill_nome_lead as bf
    with pool.connection() as c:
        lead = _lead(c, "558694867388")
        c.execute("insert into wa_contatos (conta_id, numero8, nome) values (%s,%s,%s)",
                  (CONTA, "94867388", "Mercado Avenida"))
        c.commit()
    monkeypatch.setattr(bf, "get_pool", lambda: pool)
    monkeypatch.setattr(bf.sys, "argv", ["backfill_nome_lead"])     # sem --aplicar
    bf.main()
    with pool.connection() as c:
        assert c.execute("select empresa from prospeccao where id=%s",
                         (lead,)).fetchone()[0] == "558694867388"


def test_aplicar_grava_o_nome_e_preenche_o_contato(pool, monkeypatch):
    import scripts.backfill_nome_lead as bf
    with pool.connection() as c:
        renomear = _lead(c, "558694867388")
        com_contato = _lead(c, "558611112222", whats="+558611112222", contato="Seu Zé")
        intacto = _lead(c, "Padaria do Bairro", whats="+558633334444")
        c.execute("insert into wa_contatos (conta_id, numero8, nome) values (%s,%s,%s), (%s,%s,%s)",
                  (CONTA, "94867388", "Mercado Avenida", CONTA, "11112222", "Joana Ribeiro"))
        c.commit()
    monkeypatch.setattr(bf, "get_pool", lambda: pool)
    monkeypatch.setattr(bf.sys, "argv", ["backfill_nome_lead", "--aplicar"])
    bf.main()
    with pool.connection() as c:
        assert c.execute("select empresa, contato from prospeccao where id=%s",
                         (renomear,)).fetchone() == ("Mercado Avenida", "Mercado Avenida")
        # contato que já existia não é sobrescrito
        assert c.execute("select empresa, contato from prospeccao where id=%s",
                         (com_contato,)).fetchone() == ("Joana Ribeiro", "Seu Zé")
        assert c.execute("select empresa from prospeccao where id=%s",
                         (intacto,)).fetchone()[0] == "Padaria do Bairro"


def test_agenda_de_outra_conta_nao_vaza(pool):
    with pool.connection() as c:
        _lead(c, "558694867388")
        c.execute("insert into wa_contatos (conta_id, numero8, nome) values (999,%s,%s)",
                  ("94867388", "Lead de Outra Empresa"))
        c.commit()
        assert _candidatos(c) == []
