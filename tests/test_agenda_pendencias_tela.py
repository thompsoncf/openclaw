"""O card de pendências NA TELA — o que aparece, pra quem, e quando some.

O motor está em tests/test_agenda_pendencias.py. Aqui se prova o que a pessoa vê,
porque um aviso que o motor calcula certo e a tela não mostra não avisa ninguém.

Quatro coisas que só se verificam renderizando:
 1. o card só existe pra quem VENDE DATA — pra clínica e escritório, dois
    compromissos no mesmo dia é a rotina, não um alerta;
 2. ele SOME quando não há nada, em vez de dizer "0 pendências";
 3. o botão de cada linha abre o MÊS daquela data — mandar conferir 10/07/2027 e
    abrir agosto de 2026 é o mesmo que não avisar;
 4. o nome de quem vendeu aparece, porque a correção é uma conversa com essa
    pessoa.

Renderiza o template de verdade (Jinja + tema), com banco de teste próprio.
"""
import os
from datetime import timedelta
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from finance import agenda as ag
from web import painel_agenda as pa

BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"
CONTA = 900


@pytest.fixture()
def cli(monkeypatch):
    dbname = "zaq_pend_tela"
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1,
                           open=True, kwargs={"autocommit": True,
                                              "prepare_threshold": None})
    with admin.connection() as c:
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    pool = ConnectionPool(url, min_size=1, max_size=3, open=True,
                          kwargs={"prepare_threshold": None})
    with pool.connection() as c:
        c.execute("create table contas (id bigserial primary key, tipo text, nome text)")
        c.execute("create table membros (id bigserial primary key, conta_id bigint, "
                  "nome text, papel text)")
        for nome in ("098_agenda.sql", "099_agenda_tipo.sql", "100_evento_convidados.sql",
                     "101_agenda_lembretes.sql", "126_agenda_avisar_convidados.sql",
                     "130_evento_desfecho.sql", "131_evento_link_online.sql",
                     "132_convidado_canal_resposta.sql", "139_agenda_mensagens_log.sql",
                     "146_agenda_enviar_confirmacao.sql", "160_agenda_pre_reserva.sql",
                     "163_evento_sinal_esperado.sql",
                     "179_agenda_tipo_e_hora_sugerida.sql"):
            c.execute((BASE / nome).read_text(encoding="utf-8"))
        c.execute("insert into contas (id, tipo, nome) values (%s,'pj','Prime')", (CONTA,))
        c.execute("insert into membros (id, conta_id, nome, papel) "
                  "values (77,%s,'Pedro Yan','membro')", (CONTA,))
        c.commit()

    monkeypatch.setattr(pa, "get_pool", lambda: pool)
    monkeypatch.setattr(pa, "_acesso",
                        lambda request: ({"conta_id": CONTA, "membro_id": None,
                                          "papel": "dono"}, None))
    estado = {"vende": True}

    class _VendasFake:
        @staticmethod
        def vende_data(pool_, conta_id_):
            return estado["vende"]

        @staticmethod
        def fichas_de_eventos(pool_, conta_id_, ids):
            return {}

    monkeypatch.setattr(pa, "_vendas", lambda: _VendasFake)

    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste-de-sessao")
    app.include_router(pa.router)
    c = TestClient(app, follow_redirects=False)
    c.pool = pool
    c.estado_nicho = estado
    yield c
    pool.close()


def _daqui(dias, hora, minuto=0):
    return (ag.agora_brt() + timedelta(days=dias)).replace(
        hour=hora, minute=minuto, second=0, microsecond=0)


def _html(cli, mes=""):
    r = cli.get("/painel/agenda" + (f"?m={mes}" if mes else ""))
    assert r.status_code == 200, r.text[:400]
    return r.text


# ═══════════════════════════ some quando não há nada ═══════════════════════════

def test_agenda_sem_pendencia_nao_mostra_o_card(cli):
    ag.criar_evento(cli.pool, CONTA, "Locação — Jonas", _daqui(3, 21), membro_id=77)
    html = _html(cli)
    assert "pra conferir" not in html, "card apareceu sem ter o que conferir"
    assert 'class="pend"' not in html


# ═══════════════════════════ choque de data ═══════════════════════════

def test_choque_aparece_com_dia_horarios_e_quem_vendeu(cli):
    ag.criar_evento(cli.pool, CONTA, "Locação — Allef", _daqui(300, 17), membro_id=77)
    ag.criar_evento(cli.pool, CONTA, "Locação — Márcia", _daqui(300, 20),
                    membro_id=77, segurar=True)
    html = _html(cli)

    dia = _daqui(300, 17).date()
    assert "pra conferir" in html
    assert dia.strftime("%d/%m/%Y") in html, "o card não diz QUE DIA choca"
    assert "2 eventos no mesmo dia" in html
    assert "17:00 Locação — Allef" in html
    assert "20:00 Locação — Márcia" in html
    assert "(segurado)" in html, "não dá pra saber qual dos dois é pré-reserva"
    assert "Pedro Yan" in html, "sem o nome não dá pra saber com quem resolver"


def test_o_botao_do_choque_abre_o_mes_daquela_data(cli):
    """O choque da Prime é em 2027; a agenda abre em 2026. Botão que abre o mês
    corrente manda a pessoa procurar sozinha."""
    for h in (17, 20):
        ag.criar_evento(cli.pool, CONTA, f"Festa {h}h", _daqui(300, h), membro_id=77)
    dia = _daqui(300, 17).date()
    html = _html(cli)
    assert f'href="/painel/agenda?m={dia:%Y-%m}#d{dia.isoformat()}"' in html


def test_um_compromisso_por_dia_nao_vira_alerta(cli):
    ag.criar_evento(cli.pool, CONTA, "Locação — Jonas", _daqui(10, 21), membro_id=77)
    ag.criar_evento(cli.pool, CONTA, "Locação — Ana", _daqui(11, 21), membro_id=77)
    assert "no mesmo dia" not in _html(cli)


# ═══════════════════════════ hora chutada ═══════════════════════════

def test_hora_chutada_aparece_na_lista_de_conferencia(cli):
    ag.criar_evento(cli.pool, CONTA, "Aniversário — Marilene", _daqui(340, 19),
                    membro_id=77, hora_sugerida=True)
    html = _html(cli)
    assert "horário a conferir" in html
    assert "o sistema chutou a hora" in html, \
        "a tela não diz que a hora é palpite — é a informação inteira do aviso"
    assert "Aniversário — Marilene" in html


def test_hora_confirmada_some_do_card(cli):
    """Enquanto o alerta não se esvazia sozinho ele vira paisagem."""
    ev = ag.criar_evento(cli.pool, CONTA, "Casamento — Montanna", _daqui(100, 16),
                         membro_id=77, hora_sugerida=True)
    assert "horário a conferir" in _html(cli)
    with cli.pool.connection() as c:
        c.execute("update eventos_agenda set hora_sugerida=false where id=%s", (ev["id"],))
        c.commit()
    assert "horário a conferir" not in _html(cli)


def test_a_contagem_de_horarios_e_plural_certo(cli):
    ag.criar_evento(cli.pool, CONTA, "Aniversário", _daqui(340, 19),
                    membro_id=77, hora_sugerida=True)
    assert "1 horário a conferir" in _html(cli)
    ag.criar_evento(cli.pool, CONTA, "Confraternização", _daqui(350, 19),
                    membro_id=77, hora_sugerida=True)
    assert "2 horários a conferir" in _html(cli)


# ═══════════════════════════ sem vendedor ═══════════════════════════

def test_festa_sem_dono_aparece_e_diz_se_e_negociacao(cli):
    """A pré-reserva da Erlane é o caso ruim: data segurada e ninguém fechando."""
    ag.criar_evento(cli.pool, CONTA, "Buffet 150 — Erlane", _daqui(150, 19), segurar=True)
    html = _html(cli)
    assert "1 sem vendedor" in html
    assert "ninguém está tocando" in html
    assert "Buffet 150 — Erlane" in html
    assert "(segurado)" in html


def test_festa_com_dono_nao_entra(cli):
    ag.criar_evento(cli.pool, CONTA, "Locação — Jonas", _daqui(3, 21), membro_id=77)
    assert "sem vendedor" not in _html(cli)


# ═══════════════════════════ nicho ═══════════════════════════

def test_conta_que_nao_vende_data_nao_ve_o_card(cli):
    """Pra clínica, loja e escritório, dois compromissos no mesmo dia é a rotina —
    e 'hora chutada' nem existe, porque nada importa agenda pra eles."""
    for h in (14, 17):
        ag.criar_evento(cli.pool, CONTA, f"Reunião {h}h", _daqui(20, h))
    assert "pra conferir" in _html(cli), "com nicho de eventos tinha que aparecer"

    cli.estado_nicho["vende"] = False
    html = _html(cli)
    assert "pra conferir" not in html
    assert "no mesmo dia" not in html
    assert 'class="pend"' not in html


def test_o_card_nao_atravessa_conta(cli):
    """Multi-tenant: choque da vizinha não é alerta desta conta."""
    with cli.pool.connection() as c:
        outra = c.execute("insert into contas (tipo,nome) values ('pj','Vizinha') "
                          "returning id").fetchone()[0]
        c.commit()
    for h in (17, 20):
        ag.criar_evento(cli.pool, outra, f"Festa da vizinha {h}h", _daqui(60, h))
    assert "pra conferir" not in _html(cli)


# ═══════════════════════════ tolerância ═══════════════════════════

def test_a_agenda_abre_mesmo_se_as_pendencias_falharem(cli, monkeypatch):
    """O card é enfeite útil; o calendário é o que a pessoa veio ver."""
    def explode(*a, **k):
        raise RuntimeError("banco velho")
    monkeypatch.setattr(ag, "choques_de_data", explode)
    monkeypatch.setattr(ag, "horas_a_conferir", explode)
    monkeypatch.setattr(ag, "sem_vendedor", explode)

    ag.criar_evento(cli.pool, CONTA, "Locação — Jonas", _daqui(3, 21), membro_id=77)
    html = _html(cli)
    assert "Locação — Jonas" in html, "a agenda caiu junto com o card"
    assert 'class="pend"' not in html
