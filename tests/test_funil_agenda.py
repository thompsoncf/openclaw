"""A ponte entre o funil e a Agenda (finance/funil_agenda). Regra 6 do fluxo V3.

O que estes testes protegem, em uma frase cada:
  * nasce desligada: sem a caixa marcada na Régua, a passada não faz nada;
  * lead sem data de evento não vira compromisso inventado;
  * quem já tem compromisso ligado não ganha um segundo;
  * o evento digitado à mão antes de fechar é LIGADO, não duplicado;
  * a passada é idempotente — rodar duas vezes não cria dois.
"""
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import funil_agenda as fa

MIG = Path(__file__).resolve().parent.parent / "db" / "migracoes"
CONTA = 13
HOJE = date(2026, 9, 11)

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text, contato text,
  status text default 'novo', estagio text default 'lead', vendedor_id bigint,
  evento_em date, evento_tipo text, evento_convidados int,
  criado_em timestamptz default now());
create table funil_etapas (id bigserial primary key, conta_id bigint, chave text,
  rotulo text, ordem int default 0);
-- as colunas que `agenda.criar_evento` escreve de verdade. O stub existe pra o
-- teste não depender do schema inteiro, mas o que ele omite vira erro só aqui e
-- nunca em produção — então ele acompanha a função, e não o contrário.
create table eventos_agenda (id bigserial primary key, conta_id bigint, membro_id bigint,
  titulo text, inicio timestamptz, fim timestamptz, local text, descricao text,
  lembrete_min int, tipo text default 'pessoal', link_online text,
  status text default 'ativo', pre_reserva_ate timestamptz, sinal_centavos int,
  prospeccao_id bigint, tipo_evento text, convidados int, hora_sugerida boolean default false,
  cliente_id bigint, ics_token text, desfecho text, criado_em timestamptz default now());
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_funil_agenda_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        # a migração de verdade: é o que faz o teste perceber que ela não chegou
        c.execute((MIG / "238_etapa_sai_do_quadro.sql").read_text(encoding="utf-8"))
        for ch, o in (("contatado", 10), ("ganho", 900)):
            c.execute("""insert into funil_etapas (conta_id, chave, rotulo, ordem)
                         values (%s,%s,%s,%s)""", (CONTA, ch, ch.capitalize(), o))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def limpo(pool):
    with pool.connection() as con:
        con.execute("delete from eventos_agenda")
        con.execute("delete from prospeccao")
        con.execute("update funil_etapas set agenda_ao_entrar=false where conta_id=%s", (CONTA,))
        con.commit()
    return pool


def _lead(pool, status="ganho", data=HOJE, tipo="Casamento", quem="Ana e Léo"):
    with pool.connection() as c:
        lid = c.execute("""insert into prospeccao (conta_id, contato, status, evento_em, evento_tipo)
                           values (%s,%s,%s,%s,%s) returning id""",
                        (CONTA, quem, status, data, tipo)).fetchone()[0]
        c.commit()
    return lid


def _liga(pool, chave="ganho"):
    with pool.connection() as c:
        c.execute("update funil_etapas set agenda_ao_entrar=true where conta_id=%s and chave=%s",
                  (CONTA, chave))
        c.commit()


def _eventos(pool):
    with pool.connection() as c:
        return c.execute("""select id, titulo, prospeccao_id, tipo_evento, hora_sugerida
                              from eventos_agenda order by id""").fetchall()


# ------------------------------------------------------------------ desligada

def test_nasce_desligada_e_a_passada_nao_encontra_conta(limpo):
    """Nenhuma etapa nasce com a caixa marcada: no dia do deploy nada acontece."""
    _lead(limpo)
    assert fa.rodar(limpo) == {"contas": 0, "criados": 0, "ligados": 0}
    assert _eventos(limpo) == []


def test_lead_sem_data_nao_vira_compromisso_inventado(limpo):
    """Sem data não há o que agendar — inventar uma seria pôr na agenda do dono um
    compromisso que não existe."""
    _liga(limpo)
    _lead(limpo, data=None)
    assert fa.rodar(limpo)["criados"] == 0
    assert _eventos(limpo) == []


def test_lead_em_outra_etapa_nao_e_alcancado(limpo):
    _liga(limpo, "ganho")
    _lead(limpo, status="contatado")
    assert fa.rodar(limpo)["criados"] == 0


# ------------------------------------------------------------------ criando

def test_o_lead_que_fecha_vira_compromisso_pela_data_da_festa(limpo):
    _liga(limpo)
    lid = _lead(limpo)
    r = fa.rodar(limpo)
    assert (r["contas"], r["criados"], r["ligados"]) == (1, 1, 0)
    evs = _eventos(limpo)
    assert len(evs) == 1
    _id, titulo, pros, tipo_ev, sugerida = evs[0]
    assert pros == lid and tipo_ev == "Casamento"
    assert titulo == "Casamento — Ana e Léo"
    # a hora é palpite nosso (19h): a agenda mostra que foi sugerida em vez de
    # fingir que alguém escolheu
    assert sugerida is True


def test_rodar_duas_vezes_nao_cria_dois(limpo):
    """A passada roda a cada 2 minutos: não ser idempotente encheria a agenda do
    dono com o mesmo casamento trinta vezes por hora."""
    _liga(limpo); _lead(limpo)
    fa.rodar(limpo)
    assert fa.rodar(limpo)["criados"] == 0
    assert len(_eventos(limpo)) == 1


def test_compromisso_cancelado_nao_conta_como_existente(limpo):
    """Cancelou e fechou o lead de novo: a festa volta pra agenda."""
    _liga(limpo); lid = _lead(limpo)
    fa.rodar(limpo)
    with limpo.connection() as c:
        c.execute("update eventos_agenda set status='cancelado' where prospeccao_id=%s", (lid,))
        c.commit()
    assert fa.rodar(limpo)["criados"] == 1


# ------------------------------------------------------------------ ligando

def test_o_evento_digitado_a_mao_e_LIGADO_e_nao_duplicado(limpo):
    """Medido na conta 34: 86 compromissos, só 14 ligados a um lead — quase toda
    festa é digitada direto na agenda. Duplicar seria o pior erro possível: o dono
    olharia dois compromissos no mesmo dia sem saber qual é o de verdade."""
    _liga(limpo)
    lid = _lead(limpo)
    with limpo.connection() as c:
        eid = c.execute("""insert into eventos_agenda (conta_id, titulo, inicio, tipo, status)
                           values (%s,'Casamento Ana e Léo — salão 2',%s,'empresa','ativo')
                           returning id""",
                        (CONTA, datetime(2026, 9, 11, 23, 0, tzinfo=timezone.utc))).fetchone()[0]
        c.commit()
    r = fa.rodar(limpo)
    assert (r["criados"], r["ligados"]) == (0, 1)
    evs = _eventos(limpo)
    assert len(evs) == 1 and evs[0][0] == eid and evs[0][2] == lid
    # o título digitado NÃO é reescrito: sobrescrever o que a pessoa pôs seria pior
    # que não ter ponte nenhuma
    assert evs[0][1] == "Casamento Ana e Léo — salão 2"


def test_evento_de_outro_dia_nao_e_confundido_com_o_da_festa(limpo):
    _liga(limpo)
    _lead(limpo, data=HOJE)
    with limpo.connection() as c:
        c.execute("""insert into eventos_agenda (conta_id, titulo, inicio, tipo, status)
                     values (%s,'Casamento Ana e Léo',%s,'empresa','ativo')""",
                  (CONTA, datetime(2026, 9, 11, 23, 0, tzinfo=timezone.utc) + timedelta(days=9)))
        c.commit()
    r = fa.rodar(limpo)
    assert (r["criados"], r["ligados"]) == (1, 0), "ligou num evento de outro dia"


def test_evento_que_ja_tem_outra_ficha_nao_e_roubado(limpo):
    """Compromisso já ligado a OUTRO lead continua dele."""
    _liga(limpo)
    outro = _lead(limpo, quem="Bianca")
    lid = _lead(limpo, quem="Ana e Léo")
    with limpo.connection() as c:
        c.execute("""insert into eventos_agenda (conta_id, titulo, inicio, tipo, status, prospeccao_id)
                     values (%s,'Casamento Ana e Léo',%s,'empresa','ativo',%s)""",
                  (CONTA, datetime(2026, 9, 11, 23, 0, tzinfo=timezone.utc), outro))
        c.commit()
    fa.rodar(limpo)
    with limpo.connection() as c:
        donos = [r[0] for r in c.execute(
            "select prospeccao_id from eventos_agenda order by id").fetchall()]
    assert donos[0] == outro, "o evento do outro lead foi roubado"
    assert lid in donos, "o lead novo ficou sem compromisso"
