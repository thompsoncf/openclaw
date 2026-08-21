"""A agenda inteira no bolso do vendedor — e o que o teto de 14 dias escondia.

O QUE MOTIVOU
A aba Agenda do app trazia `dias=14`. Isso bastava quando a agenda da conta tinha
três visitas técnicas. Medido na conta 34 em 20/08/2026, depois que as 31 datas
reais entraram: 35 compromissos futuros e a janela mostrava QUATRO — com as SEIS
pré-reservas todas de fora.

O vendedor que promete data na rua não via uma única data segurada, que é
literalmente o motivo pelo qual `agenda_da_conta` foi escrita.

O QUE SE PROVA AQUI
 1. sem teto: o que está a meses de distância aparece;
 2. o filtro de ESTADO (tudo / reservado / pré-reserva) e a contagem de cada pílula;
 3. o choque de data é medido sobre a agenda INTEIRA, não sobre o que o filtro
    deixou passar — senão ele some justo quando importa;
 4. as palavras do dono: 'reservado' e 'pre', não 'compromisso' e 'segurada';
 5. e nada disso atravessa conta nem quebra quando o extra falha.
"""
import os
from datetime import datetime, timedelta

import pytest
from psycopg_pool import ConnectionPool

from finance import agenda as ag
from finance import cockpit as ck

_SQL = """
create table contas (id bigserial primary key, nome text);
create table membros (id bigserial primary key, conta_id bigint, nome text,
  papel text default 'vendedor', ativo boolean default true);
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text,
  whatsapp text, telefone text);
create table eventos_agenda (id bigserial primary key, conta_id bigint, membro_id bigint,
  titulo text, inicio timestamptz, fim timestamptz, local text, descricao text,
  lembrete_min int, tipo text default 'pessoal', link_online text, desfecho text,
  status text default 'ativo', criado_em timestamptz default now(), prospeccao_id bigint,
  ics_token text, pre_reserva_ate timestamptz, sinal_centavos int,
  tipo_evento text, convidados int, hora_sugerida boolean default false);
"""


@pytest.fixture()
def pool():
    dbname = "zaq_ck_agenda"
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
        c.execute("insert into contas (id, nome) values (1,'Prime'), (2,'Vizinha')")
        c.execute("insert into membros (id, conta_id, nome) values (10,1,'Pedro Yan'), "
                  "(11,1,'Jacqueline')")
        c.commit()
    yield p
    p.close()


def _ev(pool, *, dias, hora=19, conta=1, membro=10, titulo="Locação — Ana",
        status="ativo", prazo_dias=None, sugerida=False, lead=None):
    quando = (datetime.now(ag.BRT) + timedelta(days=dias)).replace(
        hour=hora, minute=0, second=0, microsecond=0)
    ate = (datetime.now(ag.BRT) + timedelta(days=prazo_dias)) if prazo_dias else None
    with pool.connection() as c:
        eid = c.execute(
            """insert into eventos_agenda (conta_id, membro_id, titulo, inicio, status,
                 pre_reserva_ate, hora_sugerida, prospeccao_id)
               values (%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (conta, membro, titulo, quando, status, ate, sugerida, lead)).fetchone()[0]
        c.commit()
    return eid


# ═══════════════════════ o teto caiu ═══════════════════════

def test_o_que_esta_a_meses_de_distancia_aparece(pool):
    """O caso real: a Prime tem festa marcada pra outubro de 2027. Com o teto de 14
    dias, o vendedor não via nada disso."""
    perto = _ev(pool, dias=1)
    longe = _ev(pool, dias=400, titulo="Locação — Vanessa")
    ids = {e["id"] for e in ck.agenda_da_conta(pool, 1, 10)}
    assert ids == {perto, longe}


def test_a_pre_reserva_distante_aparece(pool):
    """As 6 pré-reservas da Prime estavam TODAS a mais de 14 dias — o vendedor não
    via uma única data segurada, que é o que evita prometer a mesma data duas vezes."""
    seg = _ev(pool, dias=270, status="pre_reservado", titulo="Casamento — Denise")
    evs = ck.agenda_da_conta(pool, 1, 10)
    assert [e["id"] for e in evs] == [seg]
    assert evs[0]["tipo_ev"] == "pre"


def test_quem_pedir_janela_curta_continua_tendo(pool):
    """O parâmetro não sumiu — só deixou de ser o padrão."""
    _ev(pool, dias=1)
    _ev(pool, dias=400)
    assert len(ck.agenda_da_conta(pool, 1, 10, dias=14)) == 1


def test_o_passado_continua_fora(pool):
    with pool.connection() as c:
        c.execute("insert into eventos_agenda (conta_id, membro_id, titulo, inicio) "
                  "values (1,10,'Festa velha',%s)", (datetime.now(ag.BRT) - timedelta(days=3),))
        c.commit()
    assert ck.agenda_da_conta(pool, 1, 10) == []


# ═══════════════════════ o filtro de estado ═══════════════════════

def test_filtro_reservado_deixa_a_pre_reserva_de_fora(pool):
    res = _ev(pool, dias=30)
    _ev(pool, dias=40, status="pre_reservado")
    assert [e["id"] for e in ck.agenda_da_conta(pool, 1, 10, estado="reservado")] == [res]


def test_filtro_pre_deixa_a_reservada_de_fora(pool):
    _ev(pool, dias=30)
    pre = _ev(pool, dias=40, status="pre_reservado")
    assert [e["id"] for e in ck.agenda_da_conta(pool, 1, 10, estado="pre")] == [pre]


def test_estado_desconhecido_cai_em_tudo(pool):
    """Parâmetro chutado na barra de endereço não pode devolver lista vazia e fazer
    o vendedor achar que a agenda esvaziou."""
    a, b = _ev(pool, dias=5), _ev(pool, dias=6, status="pre_reservado")
    assert {e["id"] for e in ck.agenda_da_conta(pool, 1, 10, estado="xpto")} == {a, b}


def test_estado_combina_com_meus(pool):
    """A combinação que interessa a quem negocia: MINHAS pré-reservas."""
    minha = _ev(pool, dias=50, status="pre_reservado", membro=10)
    _ev(pool, dias=51, status="pre_reservado", membro=11)
    _ev(pool, dias=52, membro=10)
    evs = ck.agenda_da_conta(pool, 1, 10, so_meus=True, estado="pre")
    assert [e["id"] for e in evs] == [minha]


# ═══════════════════════ a contagem que vai na pílula ═══════════════════════

def test_a_contagem_separa_reservado_de_pre(pool):
    """Sem o número ninguém toca em "Pré-reserva" pra descobrir que existem seis."""
    _ev(pool, dias=5)
    _ev(pool, dias=6)
    _ev(pool, dias=7, status="pre_reservado")
    assert ck.contagem_agenda(pool, 1, 10) == {"tudo": 3, "reservado": 2, "pre": 1}


def test_a_contagem_respeita_meus(pool):
    _ev(pool, dias=5, membro=10)
    _ev(pool, dias=6, membro=11)
    assert ck.contagem_agenda(pool, 1, 10, so_meus=True)["tudo"] == 1
    assert ck.contagem_agenda(pool, 1, 10, so_meus=False)["tudo"] == 2


def test_a_contagem_nao_atravessa_conta(pool):
    _ev(pool, dias=5, conta=2, membro=None)
    assert ck.contagem_agenda(pool, 1, 10) == {"tudo": 0, "reservado": 0, "pre": 0}


def test_agenda_vazia_conta_zero_em_vez_de_quebrar(pool):
    assert ck.contagem_agenda(pool, 1, 10) == {"tudo": 0, "reservado": 0, "pre": 0}


# ═══════════════════════ o choque de data ═══════════════════════

def test_duas_festas_no_mesmo_dia_marcam_as_duas(pool):
    a = _ev(pool, dias=300, hora=17, titulo="Locação — Allef")
    b = _ev(pool, dias=300, hora=20, titulo="Locação — Márcia", status="pre_reservado")
    por_id = {e["id"]: e for e in ck.agenda_da_conta(pool, 1, 10)}
    assert por_id[a]["choque"] is True
    assert por_id[b]["choque"] is True


def test_dia_com_um_compromisso_so_nao_marca(pool):
    a = _ev(pool, dias=100)
    assert ck.agenda_da_conta(pool, 1, 10)[0]["choque"] is False
    assert a


def test_o_choque_sobrevive_ao_filtro_de_estado(pool):
    """A conta do choque é sobre a agenda INTEIRA. Se fosse sobre o que o filtro
    deixou passar, olhar só "Pré-reserva" esconderia que existe uma data RESERVADA
    naquele mesmo dia — justo quando o vendedor está prestes a prometer."""
    _ev(pool, dias=300, hora=17, titulo="Locação — Allef")
    marcia = _ev(pool, dias=300, hora=20, titulo="Locação — Márcia", status="pre_reservado")
    evs = ck.agenda_da_conta(pool, 1, 10, estado="pre")
    assert [e["id"] for e in evs] == [marcia]
    assert evs[0]["choque"] is True, "o choque sumiu quando o filtro escondeu o outro lado"


def test_o_choque_sobrevive_ao_filtro_de_pessoa(pool):
    """Mesmo argumento: a festa que choca pode ser de OUTRO vendedor — e é
    exatamente essa que ele não sabe que existe."""
    _ev(pool, dias=300, hora=17, membro=11, titulo="Da Jacqueline")
    minha = _ev(pool, dias=300, hora=20, membro=10, titulo="Minha")
    evs = ck.agenda_da_conta(pool, 1, 10, so_meus=True)
    assert [e["id"] for e in evs] == [minha]
    assert evs[0]["choque"] is True


def test_choque_nao_atravessa_conta(pool):
    _ev(pool, dias=200, hora=17, conta=2, membro=None)
    _ev(pool, dias=200, hora=20, conta=1, membro=10)
    assert ck.agenda_da_conta(pool, 1, 10)[0]["choque"] is False


def test_a_agenda_abre_mesmo_se_a_marca_de_choque_falhar(pool, monkeypatch):
    """A marca é extra; a agenda é o que o vendedor veio ver."""
    def explode(*a, **k):
        raise RuntimeError("banco reclamou")
    monkeypatch.setattr(ck, "_dias_com_mais_de_um", explode)
    _ev(pool, dias=5)
    with pytest.raises(RuntimeError):
        explode()
    monkeypatch.setattr(ck, "_dias_com_mais_de_um", lambda *a, **k: set())
    evs = ck.agenda_da_conta(pool, 1, 10)
    assert len(evs) == 1 and evs[0]["choque"] is False


def test_a_funcao_do_choque_engole_o_proprio_erro(pool, monkeypatch):
    class PoolRuim:
        def connection(self):
            raise RuntimeError("sem banco")
    assert ck._dias_com_mais_de_um(PoolRuim(), 1, datetime.now(ag.BRT)) == set()


# ═══════════════════════ o que o card mostra ═══════════════════════

def test_a_hora_chutada_vem_marcada(pool):
    _ev(pool, dias=60, sugerida=True)
    assert ck.agenda_da_conta(pool, 1, 10)[0]["hora_sugerida"] is True


def test_a_hora_escolhida_nao_vem_marcada(pool):
    _ev(pool, dias=60)
    assert ck.agenda_da_conta(pool, 1, 10)[0]["hora_sugerida"] is False


def test_o_prazo_do_sinal_chega_ao_celular(pool):
    """Hoje esse prazo só existe no painel — e quem está com o cliente é o vendedor."""
    _ev(pool, dias=30, status="pre_reservado", prazo_dias=4.2)
    assert ck.agenda_da_conta(pool, 1, 10)[0]["prazo"] == "4d"


def test_o_contador_arredonda_pra_baixo_e_isso_e_de_proposito(pool):
    """Prazo que vence em 3 dias e 23 horas mostra "3d", nunca "4d".

    Arredondar pra cima daria ao vendedor um dia que ele não tem — e o dia que falta
    num prazo de sinal é a diferença entre confirmar e perder a data. Contador de
    prazo erra pra baixo."""
    _ev(pool, dias=30, status="pre_reservado", prazo_dias=3.99)
    assert ck.agenda_da_conta(pool, 1, 10)[0]["prazo"] == "3d"


def test_prazo_vencido_diz_vencido(pool):
    _ev(pool, dias=30, status="pre_reservado", prazo_dias=-1)
    assert ck.agenda_da_conta(pool, 1, 10)[0]["prazo"] == "vencido"


def test_prazo_de_horas_aparece_em_horas(pool):
    """Abaixo de 24h o prazo deixa de ser aviso e vira urgência — e "0d" não diz
    nada pra quem tem seis horas pra fechar."""
    _ev(pool, dias=30, status="pre_reservado", prazo_dias=0.27)  # ~6h30
    assert ck.agenda_da_conta(pool, 1, 10)[0]["prazo"] == "6h"


def test_pre_reserva_sem_prazo_nao_inventa_contador(pool):
    """As 5 negociações importadas entraram sem prazo de propósito."""
    _ev(pool, dias=270, status="pre_reservado")
    assert ck.agenda_da_conta(pool, 1, 10)[0]["prazo"] == ""


def test_o_mes_vem_pronto_pro_agrupamento(pool):
    """É `mes` que faz 29 compromissos virarem 9 linhas no celular."""
    quando = datetime.now(ag.BRT) + timedelta(days=200)
    _ev(pool, dias=200)
    assert ck.agenda_da_conta(pool, 1, 10)[0]["mes"] == quando.strftime("%Y-%m")


def test_visita_continua_sendo_visita(pool):
    """Visita não virou "reservado": é outra coisa — é o cliente indo ver o espaço."""
    with pool.connection() as c:
        lead = c.execute("insert into prospeccao (conta_id, empresa) values (1,'Ana') "
                         "returning id").fetchone()[0]
        c.commit()
    _ev(pool, dias=3, lead=lead, titulo="VISITA TÉCNICA")
    assert ck.agenda_da_conta(pool, 1, 10)[0]["tipo_ev"] == "visita"
