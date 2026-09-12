"""A temperatura sugerida pelos fatos (finance/temperatura). § 3 e § 5 do Projeto
Adaptado da Prime.

O DEFEITO QUE ORIGINOU ISTO, medido em 12/09/2026: 281 dos 284 leads em "Contatado"
da conta 34 estavam QUENTE — e 15 dos 21 PERDIDOS também. Todo lead é carimbado
quente na promoção, e o único que esfria é o agente de IA, desligado naquela conta.

O que estes testes protegem:
  * as três temperaturas saem dos fatos, na ordem do § 3;
  * lead fechado ou perdido fica FORA — foi por isso que 15 perdidos ficaram quentes;
  * nasce desligado, e 'observando' não escreve NADA;
  * cada mudança deixa linha no histórico, com de→para;
  * os limiares vêm do ramo e a conta pode sobrescrever.
"""
import os
from datetime import datetime, timedelta, timezone

import pytest
from psycopg_pool import ConnectionPool

from finance import temperatura as tp

CONTA = 61
AGORA = datetime(2026, 9, 12, 12, 0, tzinfo=timezone.utc)
CFG = {"temp_quente_h": 48, "temp_morno_dias": 7, "temp_frio_tentativas": 3}


# ══════════════════════════════════════════════ a regra, pura

def _s(ult_in=None, ult_out=None, tent=0, cfg=None):
    return tp.sugerir(ult_in=ult_in, ult_out=ult_out, tentativas=tent,
                      agora=AGORA, cfg=cfg or CFG)[0]


def test_cliente_que_acabou_de_falar_e_quente():
    """Responder é o sinal comercial mais barato e honesto que existe: quem pede
    orçamento, informa data ou pergunta disponibilidade está antes de tudo
    RESPONDENDO."""
    assert _s(ult_in=AGORA - timedelta(hours=3)) == "quente"
    assert _s(ult_in=AGORA - timedelta(hours=47)) == "quente"


def test_passou_da_janela_e_deixa_de_ser_quente():
    assert _s(ult_in=AGORA - timedelta(hours=49), ult_out=AGORA) == "morno"


def test_quem_nunca_respondeu_e_frio():
    """É o "informações insuficientes para identificar intenção" do § 3."""
    assert _s(ult_out=AGORA - timedelta(days=1)) == "frio"
    assert _s() == "frio"


def test_tres_tentativas_sem_resposta_esfriam():
    assert _s(ult_in=AGORA - timedelta(days=3), tent=2) == "morno"
    assert _s(ult_in=AGORA - timedelta(days=3), tent=3) == "frio"


def test_silencio_longo_esfria_mesmo_sem_tentativa():
    assert _s(ult_in=AGORA - timedelta(days=8)) == "frio"


def test_conversa_dos_dois_lados_com_a_bola_com_ele_e_morno():
    assert _s(ult_in=AGORA - timedelta(days=4), ult_out=AGORA - timedelta(hours=2)) == "morno"


def test_a_temperatura_nunca_volta_vazia():
    """"Não sei" viraria um quarto estado que a tela não sabe pintar."""
    for caso in ({}, {"ult_in": AGORA}, {"ult_out": AGORA}, {"tent": 99}):
        t, porque = tp.sugerir(ult_in=caso.get("ult_in"), ult_out=caso.get("ult_out"),
                               tentativas=caso.get("tent", 0), agora=AGORA, cfg=CFG)
        assert t in tp.TEMPERATURAS and porque, caso


def test_o_ramo_muda_o_limiar():
    """Eventos decide rápido; mensalidade respira mais devagar. Chamar de frio no 2º
    dia quem vende sistema seria inventar perda."""
    lento = {"temp_quente_h": 72, "temp_morno_dias": 14, "temp_frio_tentativas": 4}
    ha_60h = AGORA - timedelta(hours=60)
    assert _s(ult_in=ha_60h) == "morno"
    assert _s(ult_in=ha_60h, cfg=lento) == "quente"


# ══════════════════════════════════════════════ com banco

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, contato text,
  status text default 'contatado', estagio text default 'lead', temperatura text,
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, direcao text,
  criado_em timestamptz);
create table funil_etapas (id bigserial primary key, conta_id bigint, chave text,
  rotulo text, ordem int default 0, fase text default 'venda');
create table funil_movimentos (id bigserial primary key, conta_id bigint,
  prospeccao_id bigint, de text, para text, motivo text, membro_id bigint,
  criado_em timestamptz default now());
create table funil_regua (conta_id bigint primary key,
  gatilhos_modo text default 'off', cobranca_modo text default 'off',
  janela_dias text default '1,2,3,4,5,6', janela_abre time default '08:00',
  janela_fecha time default '19:00', sem_resposta_min int default 120,
  bola_nossa_min int default 240, bola_cliente_min int default 4320,
  escala_min int default 240, teto_avisos_dia int default 5,
  teto_modo text not null default 'off', teto_avisar_antes int,
  temperatura_modo text, temp_quente_h int, temp_morno_dias int, temp_frio_tentativas int,
  atualizado_em timestamptz default now());
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_temperatura_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        for ch, fase in (("novo", "venda"), ("contatado", "venda"),
                         ("ganho", "fechamento"), ("perdido", "fechamento")):
            c.execute("""insert into funil_etapas (conta_id, chave, rotulo, fase)
                         values (%s,%s,%s,%s)""", (CONTA, ch, ch.capitalize(), fase))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def limpo(pool):
    with pool.connection() as c:
        c.execute("delete from mensagens")
        c.execute("delete from conversas")
        c.execute("delete from prospeccao")
        c.execute("delete from funil_movimentos")
        c.execute("delete from funil_regua")
        c.commit()
    return pool


def _lead(pool, temp="quente", status="contatado", fala=()):
    """fala = [(direcao, horas_atras), ...]"""
    with pool.connection() as c:
        lid = c.execute("""insert into prospeccao (conta_id, contato, status, estagio, temperatura)
                           values (%s,'Cliente',%s,'lead',%s) returning id""",
                        (CONTA, status, temp)).fetchone()[0]
        if fala:
            cv = c.execute("""insert into conversas (conta_id, prospeccao_id)
                              values (%s,%s) returning id""", (CONTA, lid)).fetchone()[0]
            for direcao, h in fala:
                c.execute("""insert into mensagens (conversa_id, direcao, criado_em)
                             values (%s,%s,%s)""", (cv, direcao, AGORA - timedelta(hours=h)))
        c.commit()
    return lid


def _modo(pool, modo):
    with pool.connection() as c:
        c.execute("""insert into funil_regua (conta_id, temperatura_modo) values (%s,%s)
                     on conflict (conta_id) do update set temperatura_modo=excluded.temperatura_modo""",
                  (CONTA, modo))
        c.commit()


def _temp(pool, lid):
    with pool.connection() as c:
        return c.execute("select temperatura from prospeccao where id=%s", (lid,)).fetchone()[0]


def test_lead_fechado_ou_perdido_fica_de_fora(limpo):
    """15 dos 21 perdidos da Prime estavam "quente". A temperatura é sobre quem
    ainda pode comprar."""
    _lead(limpo, status="ganho")
    _lead(limpo, status="perdido")
    vivo = _lead(limpo, status="contatado", fala=[("in", 2)])
    with limpo.connection() as c:
        linhas = tp.avaliar(c, CONTA, AGORA, CFG)
    assert [x["id"] for x in linhas] == [vivo]


def test_o_ensaio_conta_e_nao_escreve(limpo):
    """Ligar isto reescreve 281 leads de uma vez. Ver a contagem antes é o que
    separa uma decisão de um susto."""
    lid = _lead(limpo, temp="quente", fala=[("out", 10), ("out", 20), ("out", 30)])
    _modo(limpo, "observando")
    r = tp.rodar(limpo, AGORA)
    assert r["contas"] == 1 and r["ensaios"] == 1 and r["mudados"] == 0
    assert _temp(limpo, lid) == "quente", "o ensaio escreveu"
    with limpo.connection() as c:
        assert c.execute("select count(*) from funil_movimentos").fetchone()[0] == 0


def test_desligado_nao_encontra_conta_nenhuma(limpo):
    _lead(limpo, fala=[("out", 10)])
    assert tp.rodar(limpo, AGORA) == {"contas": 0, "avaliados": 0, "mudados": 0, "ensaios": 0}


def test_ligado_escreve_e_deixa_historico(limpo):
    lid = _lead(limpo, temp="quente", fala=[("out", 10), ("out", 20), ("out", 30)])
    _modo(limpo, "ligado")
    r = tp.rodar(limpo, AGORA)
    assert r["mudados"] == 1
    assert _temp(limpo, lid) == "frio"
    with limpo.connection() as c:
        mov = c.execute("""select de, para, motivo from funil_movimentos
                            where prospeccao_id=%s""", (lid,)).fetchone()
    assert mov == ("quente", "frio", "temperatura")


def test_quem_ja_esta_na_temperatura_certa_nao_vira_linha(limpo):
    """Rodar a cada 2 minutos não pode encher o histórico de "quente → quente"."""
    _lead(limpo, temp="quente", fala=[("in", 2)])
    _modo(limpo, "ligado")
    assert tp.rodar(limpo, AGORA)["mudados"] == 0
    with limpo.connection() as c:
        assert c.execute("select count(*) from funil_movimentos").fetchone()[0] == 0


def test_o_resumo_mostra_o_antes_e_o_depois(limpo):
    _lead(limpo, temp="quente", fala=[("in", 2)])                       # segue quente
    _lead(limpo, temp="quente", fala=[("out", 5), ("out", 9), ("out", 30)])  # vira frio
    # 9 dias de silêncio do cliente: passa do limiar de 7 e esfria de vez. (100h
    # seriam 4 dias — abaixo do limiar, e o lead seria MORNO.)
    _lead(limpo, temp="quente", fala=[("in", 24 * 9), ("out", 2)])
    with limpo.connection() as c:
        r = tp.resumo(tp.avaliar(c, CONTA, AGORA, CFG))
    assert r["total"] == 3 and r["mudam"] == 2
    assert r["quente"] == 1 and r["frio"] == 2


def test_a_conta_sobrescreve_o_limiar_do_ramo(limpo):
    lid = _lead(limpo, temp="quente", fala=[("in", 60)])
    with limpo.connection() as c:
        c.execute("""insert into funil_regua (conta_id, temperatura_modo, temp_quente_h)
                     values (%s,'ligado',72)""", (CONTA,))
        c.commit()
    tp.rodar(limpo, AGORA)
    assert _temp(limpo, lid) == "quente", "o limiar da conta não valeu"


def test_a_config_diz_o_que_a_conta_escolheu(limpo):
    """A Régua mostra "você" ou "padrão do ramo" ao lado de cada número. Sem saber
    qual é qual, o dono lê 48 e não tem como saber se foi ele quem pôs."""
    with limpo.connection() as c:
        c.execute("""insert into funil_regua (conta_id, temperatura_modo, temp_quente_h)
                     values (%s,'observando',72)""", (CONTA,))
        c.commit()
        cfg = tp.config(c, CONTA)
    assert cfg["temp_quente_h"] == 72 and cfg["_escolhidas_temp"] == {"temp_quente_h"}
    # 14 e não 7: este banco de teste não tem `nichos`, então o perfil cai em
    # 'recorrente' — o perfil SEM festa, que respira mais devagar. Herdou certo.
    assert cfg["temp_morno_dias"] == 14, "o não escolhido tinha que herdar do ramo"
    assert "temp_morno_dias" not in cfg["_escolhidas_temp"]
