"""Remarcar pelo celular — e o defeito que remarcar tinha desde a pré-reserva existir.

DOIS TRABALHOS AQUI

1. O CONSERTO. `remarcar_evento` forçava `status='ativo'` sem exceção. Estava certo
   quando foi escrito: o mundo era ativo × cancelado e reativar era a intenção. A
   pré-reserva chegou na migração 160 e a função nunca foi revisitada — então mudar a
   data de uma negociação a transformava em RESERVA FIRME, calada. Reproduzido contra
   o banco em 21/08/2026: a pré-reserva de R$ 6.000 da Prime, cujo sinal ninguém
   pagou, passava a parecer vendida só por mudar de sábado. E com prazo ficava pior:
   'ativo' com `pre_reserva_ate` pendurado, que `expirar_pre_reservas` nunca recolhe.

2. O REMARCAR NO APP, e SÓ EM VISITA. O app só sabia criar compromisso. Visita é o
   cliente indo conhecer o espaço: remarcar é rotina e o cliente pede na conversa.
   Mudar a data de uma FESTA mexe em contrato, em sinal e às vezes na data que outro
   cliente queria — isso fica no painel, com o dono.
"""
import os
from datetime import datetime, timedelta

import pytest
from psycopg_pool import ConnectionPool

from finance import agenda as ag
from finance import cockpit as ck

CONTA, VEND, OUTRO = 1, 10, 11

_SQL = """
create table contas (id bigserial primary key, nome text, nome_fantasia text,
  -- `razao_social` é lida por `endereco_empresa`, que monta o nome do salão pra
  -- mensagem do cliente; sem ela a consulta estoura com UndefinedColumn
  razao_social text, endereco text, bairro text, cidade text, uf text,
  cep text, telefone text);
create table membros (id bigserial primary key, conta_id bigint, nome text,
  papel text default 'vendedor', ativo boolean default true);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, contato text, whatsapp text, telefone text, status text default 'novo',
  estagio text default 'lead', ultimo_contato_em timestamptz,
  atualizado_em timestamptz default now());
create table prospeccao_atividades (id bigserial primary key, prospeccao_id bigint,
  membro_id bigint, tipo text, resultado text, descricao text,
  criado_em timestamptz default now());
create table eventos_agenda (id bigserial primary key, conta_id bigint, membro_id bigint,
  titulo text, inicio timestamptz, fim timestamptz, local text, descricao text,
  lembrete_min int, tipo text default 'pessoal', link_online text, desfecho text,
  status text default 'ativo', criado_em timestamptz default now(), prospeccao_id bigint,
  ics_token text, pre_reserva_ate timestamptz, sinal_centavos int,
  tipo_evento text, convidados int, hora_sugerida boolean default false);
"""


@pytest.fixture()
def pool():
    dbname = "zaq_remarcar"
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
        c.execute("insert into contas (id, nome, nome_fantasia, endereco) "
                  "values (%s,'Prime','Salão Prime','Rua X, 100')", (CONTA,))
        c.execute("insert into membros (id, conta_id, nome) values (%s,%s,'Pedro Yan'), "
                  "(%s,%s,'Jacqueline')", (VEND, CONTA, OUTRO, CONTA))
        c.commit()
    yield p
    p.close()


@pytest.fixture(autouse=True)
def _sem_whatsapp(monkeypatch):
    """Nenhum teste daqui manda mensagem de verdade. Quem testa o aviso o liga."""
    import finance.whatsapp_out as wo
    monkeypatch.setattr(wo, "enviar", lambda *a, **k: {"ok": False, "erro": "desligado"})


def _daqui(dias, hora=15):
    return (datetime.now(ag.BRT) + timedelta(days=dias)).replace(
        hour=hora, minute=0, second=0, microsecond=0)


def _lead(pool, *, vend=VEND, nome="Camila", wa="5586999990000"):
    with pool.connection() as c:
        lid = c.execute("insert into prospeccao (conta_id, vendedor_id, contato, whatsapp) "
                        "values (%s,%s,%s,%s) returning id",
                        (CONTA, vend, nome, wa)).fetchone()[0]
        c.commit()
    return lid


def _visita(pool, *, dias=5, lead=None, membro=VEND, dur=60, token="tok-velho"):
    ini = _daqui(dias)
    with pool.connection() as c:
        eid = c.execute(
            """insert into eventos_agenda (conta_id, membro_id, titulo, inicio, fim,
                 local, prospeccao_id, ics_token, tipo)
               values (%s,%s,'Visita — Camila',%s,%s,'Salão Prime',%s,%s,'empresa')
               returning id""",
            (CONTA, membro, ini, ini + timedelta(minutes=dur), lead, token)).fetchone()[0]
        c.commit()
    return eid


def _st(pool, eid):
    with pool.connection() as c:
        return c.execute("select status, pre_reserva_ate, inicio, fim, ics_token "
                         "from eventos_agenda where id=%s", (eid,)).fetchone()


# ═════════════════ 1 · o conserto do remarcar ═════════════════

def test_pre_reserva_remarcada_continua_pre_reserva(pool):
    """O defeito de verdade: a data segurada virava vendida só por mudar de sábado."""
    ev = ag.criar_evento(pool, CONTA, "Casamento — Denise", _daqui(200), segurar=True)
    ag.remarcar_evento(pool, CONTA, ev["id"], _daqui(207))
    assert _st(pool, ev["id"])[0] == ag.PRE_RESERVADO


def test_o_prazo_do_sinal_sobrevive_ao_remarcar(pool):
    """Sem isto, sobrava 'ativo' com prazo pendurado — um estado que
    `expirar_pre_reservas` nunca recolhe, porque ela só olha `pre_reservado`."""
    ate = ag.agora_brt() + timedelta(days=5)
    ev = ag.criar_evento(pool, CONTA, "Aniversário", _daqui(30), pre_reserva_ate=ate)
    ag.remarcar_evento(pool, CONTA, ev["id"], _daqui(33))
    st, prazo, *_ = _st(pool, ev["id"])
    assert st == ag.PRE_RESERVADO
    assert prazo is not None, "o prazo do sinal se perdeu"


def test_a_pre_reserva_remarcada_ainda_expira(pool):
    """A prova de que o estado é COERENTE depois de remarcar, e não só bonito: o
    robô da expiração continua enxergando a data."""
    ate = ag.agora_brt() + timedelta(days=5)
    ev = ag.criar_evento(pool, CONTA, "Aniversário", _daqui(30), pre_reserva_ate=ate)
    ag.remarcar_evento(pool, CONTA, ev["id"], _daqui(33))
    expirados = ag.expirar_pre_reservas(pool, ag.agora_brt() + timedelta(days=10))
    assert ev["id"] in {e["id"] for e in expirados}


def test_cancelado_remarcado_volta_a_viver(pool):
    """Era a intenção original do 'ativo' — reaproveitar um compromisso que não
    aconteceu. Não pode ter sido perdida no conserto."""
    ev = ag.criar_evento(pool, CONTA, "Reunião", _daqui(5))
    ag.cancelar_evento(pool, CONTA, ev["id"])
    assert _st(pool, ev["id"])[0] == "cancelado"
    ag.remarcar_evento(pool, CONTA, ev["id"], _daqui(9))
    assert _st(pool, ev["id"])[0] == "ativo"


def test_ativo_remarcado_continua_ativo(pool):
    ev = ag.criar_evento(pool, CONTA, "Reunião", _daqui(5))
    ag.remarcar_evento(pool, CONTA, ev["id"], _daqui(9))
    assert _st(pool, ev["id"])[0] == "ativo"


def test_remarcar_limpa_o_desfecho(pool):
    """Remarcar significa "isso vai acontecer nessa data" — um "não realizado" de
    antes não pode ficar grudado na data nova."""
    ev = ag.criar_evento(pool, CONTA, "Reunião", _daqui(-2))
    ag.marcar_desfecho(pool, CONTA, ev["id"], "nao_realizado",
                       ag.agora_brt())
    ag.remarcar_evento(pool, CONTA, ev["id"], _daqui(9))
    with pool.connection() as c:
        assert c.execute("select desfecho from eventos_agenda where id=%s",
                         (ev["id"],)).fetchone()[0] is None


def test_remarcar_nao_atravessa_conta(pool):
    ev = ag.criar_evento(pool, CONTA, "Reunião", _daqui(5))
    assert ag.remarcar_evento(pool, 999, ev["id"], _daqui(9)) is False


# ═════════════════ 2 · só visita ═════════════════

def test_a_festa_reservada_nao_abre_pra_remarcar(pool):
    """Sem lead pendurado não é visita — é festa, reunião, bloqueio de data."""
    ev = ag.criar_evento(pool, CONTA, "Locação — Jonas", _daqui(5), membro_id=VEND)
    assert ck.visita_para_remarcar(pool, CONTA, VEND, ev["id"]) is None


def test_a_pre_reserva_nao_abre_pra_remarcar_no_app(pool):
    """Mesmo COM lead: data segurada mexe em sinal, e isso fica no painel."""
    lead = _lead(pool)
    ev = ag.criar_evento(pool, CONTA, "Casamento — Camila", _daqui(200),
                         membro_id=VEND, prospeccao_id=lead, segurar=True)
    assert ck.visita_para_remarcar(pool, CONTA, VEND, ev["id"]) is None


def test_a_visita_abre(pool):
    lead = _lead(pool)
    eid = _visita(pool, lead=lead)
    v = ck.visita_para_remarcar(pool, CONTA, VEND, eid)
    assert v and v["id"] == eid and v["quem"] == "Camila" and v["tem_numero"] is True


def test_visita_cancelada_nao_abre(pool):
    lead = _lead(pool)
    eid = _visita(pool, lead=lead)
    ag.cancelar_evento(pool, CONTA, eid)
    assert ck.visita_para_remarcar(pool, CONTA, VEND, eid) is None


# ═════════════════ 3 · posse ═════════════════

def test_o_vendedor_nao_remarca_a_visita_de_outro(pool):
    lead = _lead(pool, vend=OUTRO)
    eid = _visita(pool, lead=lead, membro=OUTRO)
    assert ck.visita_para_remarcar(pool, CONTA, VEND, eid) is None
    r = ck.remarcar_visita(pool, CONTA, VEND, eid, data="2027-01-10", hora="15:00")
    assert r["ok"] is False and r["erro"] == "escopo"


def test_gestao_remarca_qualquer_uma(pool):
    lead = _lead(pool, vend=OUTRO)
    eid = _visita(pool, lead=lead, membro=OUTRO)
    assert ck.visita_para_remarcar(pool, CONTA, VEND, eid, gestao=True) is not None
    r = ck.remarcar_visita(pool, CONTA, VEND, eid, data=_daqui(20).strftime("%Y-%m-%d"),
                           hora="15:00", avisar_cliente=False, gestao=True)
    assert r["ok"] is True


def test_nao_atravessa_conta(pool):
    lead = _lead(pool)
    eid = _visita(pool, lead=lead)
    assert ck.visita_para_remarcar(pool, 999, VEND, eid) is None
    assert ck.remarcar_visita(pool, 999, VEND, eid, data="2027-01-10",
                              hora="15:00")["ok"] is False


# ═════════════════ 4 · o que remarcar faz ═════════════════

def test_muda_a_data_e_preserva_a_duracao(pool):
    """Quem marcou 1h30 não quer virar 1h só porque mudou de dia."""
    lead = _lead(pool)
    eid = _visita(pool, lead=lead, dur=90)
    novo = _daqui(20, hora=10)
    r = ck.remarcar_visita(pool, CONTA, VEND, eid, data=novo.strftime("%Y-%m-%d"),
                           hora="10:00", avisar_cliente=False)
    assert r["ok"] is True
    _st_, _pr, ini, fim, _tk = _st(pool, eid)
    assert ini.astimezone(ag.BRT).strftime("%Y-%m-%d %H:%M") == novo.strftime("%Y-%m-%d %H:%M")
    assert (fim - ini).total_seconds() / 60 == 90


def test_o_convite_de_calendario_ganha_token_novo(pool):
    """O link antigo já está no celular do cliente e apontaria pra data velha."""
    lead = _lead(pool)
    eid = _visita(pool, lead=lead, token="tok-velho")
    ck.remarcar_visita(pool, CONTA, VEND, eid, data=_daqui(20).strftime("%Y-%m-%d"),
                       hora="15:00", avisar_cliente=False)
    assert _st(pool, eid)[4] != "tok-velho"


def test_data_invalida_nao_mexe_em_nada(pool):
    lead = _lead(pool)
    eid = _visita(pool, lead=lead)
    antes = _st(pool, eid)
    r = ck.remarcar_visita(pool, CONTA, VEND, eid, data="31/02/2027", hora="15:00")
    assert r["ok"] is False
    assert _st(pool, eid)[2] == antes[2], "a data mudou apesar do erro"


def test_registra_a_atividade_no_lead(pool):
    """Sem a anotação, ninguém responde depois por que a visita mudou de dia."""
    lead = _lead(pool)
    eid = _visita(pool, lead=lead)
    ck.remarcar_visita(pool, CONTA, VEND, eid, data=_daqui(20).strftime("%Y-%m-%d"),
                       hora="15:00", avisar_cliente=False)
    with pool.connection() as c:
        r = c.execute("select tipo, resultado from prospeccao_atividades "
                      "where prospeccao_id=%s", (lead,)).fetchone()
    assert r == ("visita", "remarcado")


# ═════════════════ 5 · o aviso ═════════════════

def test_avisa_o_cliente_pela_conversa_da_empresa(pool, monkeypatch):
    """Pela conversa do Zaq, e não pelo WhatsApp pessoal — é o canal que fica no
    histórico do lead, e é a porta que a gente fechou de propósito."""
    enviados = []
    import finance.whatsapp_out as wo
    monkeypatch.setattr(wo, "enviar",
                        lambda c, conta, num, txt: (enviados.append((num, txt))
                                                    or {"ok": True, "sid": "x"}))
    monkeypatch.setattr("web.painel_prospeccao._registrar_msg", lambda *a, **k: None)
    lead = _lead(pool)
    eid = _visita(pool, lead=lead)
    r = ck.remarcar_visita(pool, CONTA, VEND, eid, data=_daqui(20).strftime("%Y-%m-%d"),
                           hora="10:00", avisar_cliente=True)
    assert r["avisado"] is True
    assert len(enviados) == 1
    assert "mudou de data" in enviados[0][1]
    assert r["quando"] in enviados[0][1], "a mensagem não diz a data nova"


def test_sem_avisar_nao_manda_nada(pool, monkeypatch):
    enviados = []
    import finance.whatsapp_out as wo
    monkeypatch.setattr(wo, "enviar",
                        lambda *a: (enviados.append(1) or {"ok": True, "sid": "x"}))
    lead = _lead(pool)
    eid = _visita(pool, lead=lead)
    r = ck.remarcar_visita(pool, CONTA, VEND, eid, data=_daqui(20).strftime("%Y-%m-%d"),
                           hora="10:00", avisar_cliente=False)
    assert r["avisado"] is False and enviados == []


def test_aviso_que_falha_NAO_desfaz_o_remarcar(pool):
    """A data nova já é a verdade. O vendedor prefere saber que precisa avisar na mão
    a descobrir que a visita não mudou."""
    lead = _lead(pool)
    eid = _visita(pool, lead=lead)
    novo = _daqui(20, hora=10)
    r = ck.remarcar_visita(pool, CONTA, VEND, eid, data=novo.strftime("%Y-%m-%d"),
                           hora="10:00", avisar_cliente=True)
    assert r["ok"] is True and r["avisado"] is False
    assert _st(pool, eid)[2].astimezone(ag.BRT).hour == 10, "a data voltou atrás"


def test_lead_sem_whatsapp_nao_promete_aviso(pool):
    lead = _lead(pool, wa="")
    eid = _visita(pool, lead=lead)
    r = ck.remarcar_visita(pool, CONTA, VEND, eid, data=_daqui(20).strftime("%Y-%m-%d"),
                           hora="10:00", avisar_cliente=True)
    assert r["ok"] is True
    assert r["avisado"] is False and r["tinha_numero"] is False


# ═════════════════ 6 · o dia ocupado ═════════════════

def test_diz_o_que_ja_existe_no_dia(pool):
    ag.criar_evento(pool, CONTA, "Casamento — Doutor Manoel", _daqui(300, hora=20),
                    membro_id=VEND)
    outros = ck.ocupado_no_dia(pool, CONTA, _daqui(300, hora=10))
    assert len(outros) == 1
    assert outros[0]["titulo"] == "Casamento — Doutor Manoel"
    assert outros[0]["hora"] == "20:00" and outros[0]["pre"] is False


def test_o_proprio_evento_nao_conta_como_ocupacao(pool):
    """Senão remarcar pro mesmo dia (só mudando a hora) avisaria de si mesmo."""
    lead = _lead(pool)
    eid = _visita(pool, lead=lead, dias=300)
    assert ck.ocupado_no_dia(pool, CONTA, _daqui(300), ignorar_id=eid) == []


def test_a_pre_reserva_conta_como_ocupacao(pool):
    """É justamente a data segurada que ninguém lembra que está ocupada."""
    ag.criar_evento(pool, CONTA, "Locação — Márcia", _daqui(300, hora=20), segurar=True)
    outros = ck.ocupado_no_dia(pool, CONTA, _daqui(300))
    assert len(outros) == 1 and outros[0]["pre"] is True


def test_cancelado_nao_conta_como_ocupacao(pool):
    ev = ag.criar_evento(pool, CONTA, "Cancelada", _daqui(300, hora=20))
    ag.cancelar_evento(pool, CONTA, ev["id"])
    assert ck.ocupado_no_dia(pool, CONTA, _daqui(300)) == []


def test_dia_livre_nao_inventa_ocupacao(pool):
    assert ck.ocupado_no_dia(pool, CONTA, _daqui(300)) == []


def test_ocupacao_nao_atravessa_conta(pool):
    ag.criar_evento(pool, 999, "Da vizinha", _daqui(300, hora=20))
    assert ck.ocupado_no_dia(pool, CONTA, _daqui(300)) == []


def test_ocupacao_engole_o_proprio_erro(pool):
    class PoolRuim:
        def connection(self):
            raise RuntimeError("sem banco")
    assert ck.ocupado_no_dia(PoolRuim(), CONTA, _daqui(5)) == []


# ═════════════════ 7 · o portão está no POST, não só na tela ═════════════════
#
# O id vem da barra de endereço: quem manda o formulário não passa necessariamente
# pela tela. Estes três casos escaparam da primeira rodada de mutação — os testes
# acima só cobriam `visita_para_remarcar`, e a mutação que tirava o filtro de
# `remarcar_visita` sobrevivia.

def test_o_post_recusa_festa_sem_lead(pool):
    ev = ag.criar_evento(pool, CONTA, "Locação — Jonas", _daqui(5), membro_id=VEND)
    r = ck.remarcar_visita(pool, CONTA, VEND, ev["id"],
                           data=_daqui(20).strftime("%Y-%m-%d"), hora="15:00")
    assert r["ok"] is False
    assert _st(pool, ev["id"])[2].date() == _daqui(5).date(), "a festa foi remarcada"


def test_o_post_recusa_pre_reserva_mesmo_com_lead(pool):
    """Data segurada mexe em sinal. Fica no painel, com o dono — e o portão tem que
    estar aqui, não só no botão."""
    lead = _lead(pool)
    ev = ag.criar_evento(pool, CONTA, "Casamento — Camila", _daqui(200),
                         membro_id=VEND, prospeccao_id=lead, segurar=True)
    r = ck.remarcar_visita(pool, CONTA, VEND, ev["id"],
                           data=_daqui(210).strftime("%Y-%m-%d"), hora="15:00")
    assert r["ok"] is False
    st, _pr, ini, *_ = _st(pool, ev["id"])
    assert st == ag.PRE_RESERVADO, "a pré-reserva foi mexida pelo app"
    assert ini.date() == _daqui(200).date(), "a data mudou"


def test_o_post_recusa_visita_cancelada(pool):
    """Remarcar uma visita cancelada pelo app a ressuscitaria sem ninguém decidir."""
    lead = _lead(pool)
    eid = _visita(pool, lead=lead)
    ag.cancelar_evento(pool, CONTA, eid)
    r = ck.remarcar_visita(pool, CONTA, VEND, eid,
                           data=_daqui(20).strftime("%Y-%m-%d"), hora="15:00")
    assert r["ok"] is False
    assert _st(pool, eid)[0] == "cancelado"


def test_aviso_que_EXPLODE_tambem_nao_desfaz_o_remarcar(pool, monkeypatch):
    """O irmão do teste acima: lá o envio devolve ok=False; aqui ele LEVANTA.

    São caminhos diferentes no código, e só o primeiro estava coberto — a mutação
    que transformava a exceção em "ok: False" sobrevivia."""
    import finance.whatsapp_out as wo

    def explode(*a, **k):
        raise RuntimeError("WhatsApp fora do ar")
    monkeypatch.setattr(wo, "enviar", explode)

    lead = _lead(pool)
    eid = _visita(pool, lead=lead)
    r = ck.remarcar_visita(pool, CONTA, VEND, eid, data=_daqui(20).strftime("%Y-%m-%d"),
                           hora="10:00", avisar_cliente=True)
    assert r["ok"] is True, "a exceção do aviso derrubou o remarcar"
    assert r["avisado"] is False
    assert _st(pool, eid)[2].astimezone(ag.BRT).hour == 10, "a data voltou atrás"
