"""O que a agenda tem pra alguém conferir — e por que cada peça precisa existir.

Isto nasceu de uma agenda real: 31 compromissos da Prime Eventos que viviam num
sistema antigo e vieram pro Zaq de uma vez. Três buracos apareceram na hora de
trazer, e cada um deles vira um teste aqui.

 1. DUAS FESTAS NO MESMO DIA não são "conflito de horário". O 10/07/2027 tem uma
    locação às 17:00 e outra às 20:00. Pela conta de sobreposição que a agenda já
    fazia (`conflitos`, que trata evento sem fim como 1h) isso NÃO colide — 17–18h
    contra 20–21h. E são duas festas no mesmo salão.

 2. HORA CHUTADA tem que ficar marcada. Oito das 31 tinham data e não tinham hora,
    porque nesse negócio se vende o DIA. `inicio` é not null, então alguma hora foi
    gravada — e uma hora chutada sem marca é indistinguível de uma escolhida.

 3. FESTA SEM DONO é festa que ninguém está tocando. A vendedora que fez seis
    daquelas datas saiu da empresa, e uma delas é uma negociação ainda aberta.

E uma quarta, que é do modelo e não da tela: dá pra segurar uma data SEM prazo.
Antes, a única forma de nascer pré-reservado era dando um prazo — e o job de
expiração cancelaria sozinho um casamento marcado pra dali a nove meses.

Roda com banco de TESTE separado (ver tests/conftest.py).
"""
import os
from datetime import timedelta
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import agenda as ag


@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4,
                       open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    migr = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    with p.connection() as c:
        for nome in ("098_agenda.sql", "099_agenda_tipo.sql", "130_evento_desfecho.sql",
                     "131_evento_link_online.sql", "160_agenda_pre_reserva.sql",
                     "163_evento_sinal_esperado.sql",
                     "179_agenda_tipo_e_hora_sugerida.sql"):
            c.execute((migr / nome).read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


@pytest.fixture(autouse=True)
def _limpa(pool):
    with pool.connection() as c:
        c.execute("truncate table eventos_agenda restart identity cascade")
        c.commit()
    yield


@pytest.fixture()
def conta_id(pool):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome) values ('pj','Prime Teste') "
                        "returning id").fetchone()[0]
        c.commit()
    return cid


@pytest.fixture()
def membro_id(pool, conta_id):
    with pool.connection() as c:
        # 'membro' e não 'vendedor': o schema mínimo destes testes é o de origem,
        # sem as migrações que ampliaram os papéis. Qual papel é não importa aqui —
        # o que se testa é ter ou não ter dono.
        mid = c.execute("insert into membros (conta_id, nome, papel) "
                        "values (%s,'Pedro Yan','membro') returning id",
                        (conta_id,)).fetchone()[0]
        c.commit()
    return mid


def _daqui(dias: int, hora: int, minuto: int = 0):
    """Uma data futura em Brasília — as pendências só olham pra frente."""
    base = ag.agora_brt() + timedelta(days=dias)
    return base.replace(hour=hora, minute=minuto, second=0, microsecond=0)


# ═════════════════════════ segurar sem prazo ═════════════════════════

def test_segurar_nasce_pre_reservado_sem_prazo_correndo(pool, conta_id):
    """O caso da Denise: casamento em negociação pra daqui a nove meses.

    Com prazo, `expirar_pre_reservas` cancelaria sozinho quando ele vencesse."""
    ev = ag.criar_evento(pool, conta_id, "Casamento — Denise", _daqui(270, 16),
                         segurar=True)
    assert ev["status"] == ag.PRE_RESERVADO
    assert ev["pre_reserva_ate"] is None


def test_data_segurada_sem_prazo_nao_expira(pool, conta_id):
    """A trava de verdade: mesmo rodando a expiração muito à frente no tempo, uma
    reserva sem prazo continua de pé. É o que separa 'segurada' de 'com prazo'."""
    ev = ag.criar_evento(pool, conta_id, "Casamento — Denise", _daqui(270, 16),
                         segurar=True)
    com_prazo = ag.criar_evento(pool, conta_id, "Aniversário", _daqui(30, 15),
                                pre_reserva_ate=ag.agora_brt() + timedelta(days=5))

    expirados = ag.expirar_pre_reservas(pool, ag.agora_brt() + timedelta(days=3650))

    ids = {e["id"] for e in expirados}
    assert com_prazo["id"] in ids, "reserva COM prazo vencido tinha que expirar"
    assert ev["id"] not in ids, "reserva sem prazo foi cancelada — o robô comeu a negociação"
    with pool.connection() as c:
        st = c.execute("select status from eventos_agenda where id=%s", (ev["id"],)).fetchone()[0]
    assert st == ag.PRE_RESERVADO


def test_prazo_explicito_continua_mandando_no_status(pool, conta_id):
    """`segurar` não pode atropelar quem passou prazo: prazo implica segurar."""
    ate = ag.agora_brt() + timedelta(days=5)
    ev = ag.criar_evento(pool, conta_id, "Aniversário", _daqui(30, 15), pre_reserva_ate=ate)
    assert ev["status"] == ag.PRE_RESERVADO
    assert ev["pre_reserva_ate"] is not None


def test_sem_segurar_e_sem_prazo_o_compromisso_e_firme(pool, conta_id):
    ev = ag.criar_evento(pool, conta_id, "Locação — Jonas", _daqui(1, 21))
    assert ev["status"] == "ativo"
    assert ev["pre_reserva_ate"] is None


# ═════════════════════════ campos do nicho eventos ═════════════════════════

def test_guarda_tipo_convidados_e_marca_de_hora(pool, conta_id):
    ev = ag.criar_evento(pool, conta_id, "Buffet 150 — Erlane", _daqui(150, 19),
                         tipo_evento="Buffet", convidados=150, hora_sugerida=True)
    assert ev["tipo_evento"] == "Buffet"
    assert ev["convidados"] == 150
    assert ev["hora_sugerida"] is True


def test_sem_os_campos_do_nicho_o_evento_continua_normal(pool, conta_id):
    """Compromisso de outro nicho não ganha nada: null, null, e hora NÃO sugerida."""
    ev = ag.criar_evento(pool, conta_id, "Reunião", _daqui(2, 10))
    assert ev["tipo_evento"] is None
    assert ev["convidados"] is None
    assert ev["hora_sugerida"] is False


def test_hora_sugerida_e_falsa_por_padrao_no_banco(pool, conta_id):
    """O default tem que ser o caso inofensivo: hora escolhida por gente. Se o
    default fosse true, TODA agenda existente apareceria como 'a conferir'."""
    ag.criar_evento(pool, conta_id, "Reunião", _daqui(2, 10))
    with pool.connection() as c:
        assert c.execute("select count(*) from eventos_agenda where hora_sugerida"
                         ).fetchone()[0] == 0


# ═════════════════════════ choque de DATA (não de horário) ═════════════════════════

def test_duas_festas_no_mesmo_dia_sem_sobrepor_horario_ainda_e_choque(pool, conta_id):
    """O caso 10/07/2027: 17:00 e 20:00, sem fim marcado.

    Este é o teste que justifica a função existir — a conta de sobreposição que a
    agenda já tinha diz que não há conflito nenhum aqui."""
    dia = 300
    a = ag.criar_evento(pool, conta_id, "Locação — Allef", _daqui(dia, 17))
    b = ag.criar_evento(pool, conta_id, "Locação — Márcia", _daqui(dia, 20), segurar=True)

    # o que a checagem ANTIGA responde, pra deixar a diferença registrada
    assert ag.conflitos(pool, conta_id, _daqui(dia, 20), None, ignorar_id=b["id"]) == [], \
        "se isto passou a achar conflito, a função de choque por DIA pode ter virado redundante"

    choques = ag.choques_de_data(pool, conta_id)
    assert len(choques) == 1
    assert {e["id"] for e in choques[0]["eventos"]} == {a["id"], b["id"]}


def test_dia_com_um_compromisso_so_nao_e_choque(pool, conta_id):
    ag.criar_evento(pool, conta_id, "Locação — Jonas", _daqui(10, 21))
    assert ag.choques_de_data(pool, conta_id) == []


def test_choque_conta_a_pre_reserva_junto(pool, conta_id):
    """É a data segurada que ninguém lembra que está ocupada — deixá-la de fora
    esconderia justamente o choque mais provável."""
    ag.criar_evento(pool, conta_id, "Locação — Allef", _daqui(200, 17))
    ag.criar_evento(pool, conta_id, "Locação — Márcia", _daqui(200, 20), segurar=True)
    assert len(ag.choques_de_data(pool, conta_id)) == 1


def test_evento_cancelado_nao_gera_choque(pool, conta_id):
    a = ag.criar_evento(pool, conta_id, "Locação — Allef", _daqui(200, 17))
    b = ag.criar_evento(pool, conta_id, "Cancelado", _daqui(200, 20))
    ag.cancelar_evento(pool, conta_id, b["id"])
    assert ag.choques_de_data(pool, conta_id) == [], \
        "data liberada continua contando como ocupada"
    assert a["id"]


def test_choque_no_passado_nao_aparece(pool, conta_id):
    """Dia que já passou não tem o que resolver — e a Prime tem anos de histórico."""
    ontem = ag.agora_brt() - timedelta(days=1)
    for h in (17, 20):
        ag.criar_evento(pool, conta_id, f"Festa {h}h", ontem.replace(hour=h, minute=0))
    assert ag.choques_de_data(pool, conta_id) == []


def test_choque_de_madrugada_nao_escorrega_pro_dia_seguinte(pool, conta_id):
    """A armadilha do fuso, e ela é sutil: em nome POSIX o sinal é INVERTIDO, então
    'UTC-03' significa UTC+3 — um deslocamento de +6h sobre Brasília.

    Os horários aqui foram escolhidos pra CRUZAR a fronteira desse deslocamento
    (18:00 em Brasília). Uma primeira versão deste teste usava 19:00 e 22:30 e
    passava com o fuso errado: as duas festas escorregavam JUNTAS pro dia seguinte
    e continuavam agrupadas. Com 17:00 e 22:30 elas se separam — a das 17:00 fica no
    dia, a das 22:30 vai pro seguinte — e o choque some da tela, que é o estrago
    real."""
    dia = 250
    a = ag.criar_evento(pool, conta_id, "Formatura", _daqui(dia, 17))
    b = ag.criar_evento(pool, conta_id, "Confraternização", _daqui(dia, 22, 30))

    choques = ag.choques_de_data(pool, conta_id)
    assert len(choques) == 1, "a festa da noite escorregou pro dia seguinte"
    assert {e["id"] for e in choques[0]["eventos"]} == {a["id"], b["id"]}
    assert choques[0]["dia"] == _daqui(dia, 17).date()


def test_choque_e_por_conta(pool):
    """Multi-tenant: festa de outra empresa no mesmo dia não é choque nenhum."""
    with pool.connection() as c:
        c1 = c.execute("insert into contas (tipo,nome) values ('pj','Prime') returning id").fetchone()[0]
        c2 = c.execute("insert into contas (tipo,nome) values ('pj','Doce Mell') returning id").fetchone()[0]
        c.commit()
    ag.criar_evento(pool, c1, "Festa da Prime", _daqui(120, 19))
    ag.criar_evento(pool, c2, "Festa da Doce Mell", _daqui(120, 20))
    assert ag.choques_de_data(pool, c1) == []
    assert ag.choques_de_data(pool, c2) == []


def test_tres_no_mesmo_dia_vem_num_grupo_so(pool, conta_id):
    for h in (14, 17, 20):
        ag.criar_evento(pool, conta_id, f"Festa {h}h", _daqui(90, h))
    choques = ag.choques_de_data(pool, conta_id)
    assert len(choques) == 1
    assert len(choques[0]["eventos"]) == 3


def test_choques_vem_ordenados_por_dia(pool, conta_id):
    for dia in (200, 100):
        for h in (17, 20):
            ag.criar_evento(pool, conta_id, f"Festa {dia}/{h}", _daqui(dia, h))
    choques = ag.choques_de_data(pool, conta_id)
    assert [c["dia"] for c in choques] == sorted(c["dia"] for c in choques)
    assert len(choques) == 2


# ═════════════════════════ horas a conferir ═════════════════════════

def test_so_a_hora_chutada_entra_na_lista(pool, conta_id):
    chutada = ag.criar_evento(pool, conta_id, "Aniversário — Marilene", _daqui(300, 19),
                              hora_sugerida=True)
    ag.criar_evento(pool, conta_id, "Locação — Jonas", _daqui(1, 21))
    lista = ag.horas_a_conferir(pool, conta_id)
    assert [e["id"] for e in lista] == [chutada["id"]]


def test_hora_chutada_no_passado_nao_incomoda_mais(pool, conta_id):
    ontem = ag.agora_brt() - timedelta(days=1)
    with pool.connection() as c:
        c.execute("insert into eventos_agenda (conta_id,titulo,inicio,hora_sugerida) "
                  "values (%s,'Festa velha',%s,true)", (conta_id, ontem))
        c.commit()
    assert ag.horas_a_conferir(pool, conta_id) == []


def test_corrigir_a_hora_tira_a_linha_da_lista(pool, conta_id):
    """A lista tem que se esvaziar sozinha quando alguém acerta o horário — senão
    o alerta nunca some e vira ruído que se aprende a ignorar."""
    ev = ag.criar_evento(pool, conta_id, "Casamento — Montanna", _daqui(100, 16),
                         hora_sugerida=True)
    assert len(ag.horas_a_conferir(pool, conta_id)) == 1
    with pool.connection() as c:
        c.execute("update eventos_agenda set hora_sugerida=false where id=%s", (ev["id"],))
        c.commit()
    assert ag.horas_a_conferir(pool, conta_id) == []


# ═════════════════════════ sem vendedor ═════════════════════════

def test_festa_sem_dono_aparece_e_com_dono_nao(pool, conta_id, membro_id):
    orfa = ag.criar_evento(pool, conta_id, "Locação — Maria Gardênia", _daqui(35, 19))
    ag.criar_evento(pool, conta_id, "Locação — Jonas", _daqui(1, 21), membro_id=membro_id)
    assert [e["id"] for e in ag.sem_vendedor(pool, conta_id)] == [orfa["id"]]


def test_negociacao_sem_dono_tambem_conta(pool, conta_id):
    """O caso que trouxe isto à tona: a pré-reserva da Erlane ficou sem ninguém
    fechando quando a vendedora saiu. Data segurada sem dono é o pior dos dois."""
    ev = ag.criar_evento(pool, conta_id, "Buffet 150 — Erlane", _daqui(150, 19),
                         segurar=True)
    assert [e["id"] for e in ag.sem_vendedor(pool, conta_id)] == [ev["id"]]


# ═════════════════════════ o pacote que a tela consome ═════════════════════════

def test_pendencias_junta_as_tres_e_soma(pool, conta_id, membro_id):
    ag.criar_evento(pool, conta_id, "Locação — Allef", _daqui(300, 17), membro_id=membro_id)
    ag.criar_evento(pool, conta_id, "Locação — Márcia", _daqui(300, 20),
                    membro_id=membro_id, segurar=True)
    ag.criar_evento(pool, conta_id, "Aniversário", _daqui(60, 19),
                    membro_id=membro_id, hora_sugerida=True)
    ag.criar_evento(pool, conta_id, "Sem dono", _daqui(40, 18))

    p = ag.pendencias(pool, conta_id)
    assert len(p["choques"]) == 1
    assert len(p["horas"]) == 1
    assert len(p["sem_vendedor"]) == 1
    assert p["total"] == 3


def test_agenda_limpa_nao_tem_pendencia(pool, conta_id, membro_id):
    """`total` zero é o que faz o card sumir da tela."""
    ag.criar_evento(pool, conta_id, "Locação — Jonas", _daqui(1, 21), membro_id=membro_id)
    p = ag.pendencias(pool, conta_id)
    assert p["total"] == 0
    assert p == {"choques": [], "horas": [], "sem_vendedor": [], "total": 0}


def test_pendencias_nao_derruba_a_agenda_quando_uma_consulta_falha(pool, conta_id,
                                                                   monkeypatch):
    """Banco sem a migração 177 (ou qualquer falha): o card se cala, a agenda vive.

    A tela é o que a pessoa veio ver; o aviso é enfeite útil."""
    def explode(*a, **k):
        raise RuntimeError("column hora_sugerida does not exist")
    monkeypatch.setattr(ag, "horas_a_conferir", explode)

    ag.criar_evento(pool, conta_id, "Sem dono", _daqui(40, 18))
    p = ag.pendencias(pool, conta_id)
    assert p["horas"] == []
    assert len(p["sem_vendedor"]) == 1, "a falha de uma consulta comeu as outras"
    assert p["total"] == 1
