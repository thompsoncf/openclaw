"""O funil novo de eventos, parte 1 — as colunas e os gatilhos (migração 412,
finance/funil_regua.py, docs/mockups/funil_novo_eventos.html).

O que cada teste segura:
* a migração muda SÓ a Prime (34): renomeia, traz a Data segurada de volta ao quadro,
  tira a coluna Follow-up vazia (nunca apaga), cria as três colunas com o gatilho
  ligado e a fase certa — e rodar de novo não muda nada;
* o banco marca a ficha completa uma vez só, por qualquer caminho, e desmarca se um
  campo some;
* os gatilhos: ficha completa → Qualificado; visita realizada → Visita feita; a festa
  que passou → Pós-festa, só de venda fechada; nenhum puxa card pra trás;
* a agenda leva o card: faltou volta pra Qualificado com "remarcar", e o gatilho da
  visita criada não o devolve (TRAVA 3); visita nova, sim;
* o modelo de eventos das contas novas tem o mesmo desenho, e o Pós-festa nasce na
  fase pós-venda.
"""
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import funil_modelo as fm
from finance import funil_regua as fr
from finance import raio_x_perfil as rxp

BASE = Path(__file__).resolve().parents[1] / "db" / "migracoes"
PRIME, OUTRA = 34, 35

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text,
  status text default 'novo', estagio text default 'lead', orcamento_id bigint,
  vendedor_id bigint, evento_tipo text, evento_em date, evento_convidados int,
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table prospeccao_atividades (id bigserial primary key, prospeccao_id bigint,
  membro_id bigint, tipo text, resultado text, descricao text not null default '',
  criado_em timestamptz not null default now());
create table funil_etapas (id bigserial primary key, semeado_de text, conta_id bigint,
  chave text, rotulo text, ordem int default 0, fixa boolean default false,
  fase text default 'venda', prazo_min integer, gatilho text, gatilho_ativo boolean default false,
  teto_dias integer, renovacoes_max integer not null default 0,
  exige_justificativa boolean not null default true, renova_sozinho_h integer,
  saidas_permitidas text, toques_dias text, exige_motivo boolean not null default false,
  reativa_para text, sai_do_quadro boolean not null default false,
  agenda_ao_entrar boolean not null default false, criado_em timestamptz default now(),
  unique (conta_id, chave));
create table funil_regua (conta_id bigint primary key, gatilhos_modo text default 'off',
  cobranca_modo text default 'off', janela_dias text default '1,2,3,4,5,6',
  janela_abre time default '08:00', janela_fecha time default '19:00',
  sem_resposta_min int, bola_nossa_min int, bola_cliente_min int, escala_min int,
  teto_avisos_dia int);
create table funil_movimentos (id bigserial primary key, conta_id bigint,
  prospeccao_id bigint, de text, para text, motivo text, membro_id bigint,
  criado_em timestamptz default now());
create table eventos_agenda (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  titulo text, inicio timestamptz, fim timestamptz, status text default 'ativo', desfecho text,
  criado_em timestamptz default now(), tipo_evento text);
"""

# as etapas da Prime como estavam em 27/09/2026 (medido em produção, só leitura)
_PRIME = [("novo", "Novo", 0, True, "venda", None, False, False, False, "semente"),
          ("contatado", "Contatado", 10, False, "venda", "resposta_nossa", True, False, False, "semente"),
          ("follow_up", "Follow-up", 20, False, "venda", None, False, False, False, "eventos"),
          ("qualificado", "Agendado Visita", 30, False, "venda", "compromisso", True, False, False, "semente"),
          ("proposta", "Proposta", 40, False, "venda", "negociacao_valores", True, False, False, "eventos"),
          ("evento_realizado", "Orcamento Assinado", 70, False, "venda", "orcamento_aprovado", True, True, True, ""),
          ("ganho", "Fechado", 900, True, "fechamento", "contrato_assinado", True, True, True, "eventos"),
          ("perdido", "Perdido", 910, True, "fechamento", None, False, False, False, "semente")]


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_funil_novo_eventos_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        for conta in (PRIME, OUTRA):
            for e in _PRIME:
                c.execute("""insert into funil_etapas (conta_id, chave, rotulo, ordem, fixa, fase,
                               gatilho, gatilho_ativo, sai_do_quadro, agenda_ao_entrar, semeado_de)
                             values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""", (conta,) + e)
            c.execute("insert into funil_regua (conta_id, gatilhos_modo) values (%s,'ligado')", (conta,))
        c.commit()
    yield p
    p.close()


def _migrar(c):
    c.execute((BASE / "412_funil_novo_eventos.sql").read_text(encoding="utf-8"))
    c.commit()


def _migrar_lista(c):
    """A 419 (parte 2b): a coluna Lista de espera. Ela também liga `festas_por_dia`
    em `contas`, que este esquema não tem — por isso a tabela nasce aqui."""
    c.execute("create table if not exists contas (id bigint primary key, festas_por_dia int)")
    c.execute("insert into contas (id) values (%s) on conflict do nothing", (PRIME,))
    c.execute((BASE / "419_lista_espera_coluna.sql").read_text(encoding="utf-8"))
    c.commit()


def _etapas(c, conta):
    return {r[0]: r[1:] for r in c.execute(
        """select chave, rotulo, ordem, fase, gatilho, gatilho_ativo, sai_do_quadro
             from funil_etapas where conta_id=%s""", (conta,)).fetchall()}


def _lead(c, status="contatado", conta=PRIME, **kw):
    cols = {"conta_id": conta, "status": status, **kw}
    return c.execute(f"insert into prospeccao ({', '.join(cols)}) values ({', '.join(['%s'] * len(cols))}) "
                     "returning id", list(cols.values())).fetchone()[0]


def _status(c, lead):
    return c.execute("select status from prospeccao where id=%s", (lead,)).fetchone()[0]


# ══════════════════════════════════════════════ a migração

def test_a_migracao_muda_so_a_prime_e_do_jeito_do_mockup(pool):
    with pool.connection() as c:
        _migrar(c)
        e = _etapas(c, PRIME)
        assert e["qualificado"][0] == "Visita marcada"
        assert e["evento_realizado"][0] == "Data segurada" and e["evento_realizado"][5] is False
        assert e["follow_up"][5] is True, "a coluna Follow-up vazia sai do quadro"
        assert e["ficha_completa"] == ("Qualificado", 20, "venda", "ficha_completa", True, False)
        assert e["visita_feita"] == ("Visita feita", 35, "venda", "compromisso_feito", True, False)
        assert e["pos_festa"] == ("Pós-festa", 905, "pos", "festa_passou", True, False)
        # nada se apaga, e a outra conta de eventos não muda
        assert {"follow_up", "evento_realizado", "qualificado"} <= set(e)
        assert _etapas(c, OUTRA) == {k: (r, o, f, g, a, s) for k, r, o, _fx, f, g, a, s, _ag, _sd in _PRIME}
        antes = _etapas(c, PRIME)
        _migrar(c)                                              # idempotente
        assert _etapas(c, PRIME) == antes


def test_a_coluna_follow_up_com_lead_nao_sai_do_quadro(pool):
    with pool.connection() as c:
        _lead(c, status="follow_up")
        _migrar(c)
        assert _etapas(c, PRIME)["follow_up"][5] is False


def test_o_banco_marca_a_ficha_completa_uma_vez_e_desmarca_se_um_campo_some(pool):
    with pool.connection() as c:
        antiga = _lead(c, evento_tipo="15 anos", evento_em=date(2027, 3, 13), evento_convidados=150,
                       atualizado_em=datetime(2026, 9, 1, tzinfo=timezone.utc))
        _migrar(c)
        # o backfill carimba quem já estava completo com o atualizado_em
        assert c.execute("select ficha_completa_em from prospeccao where id=%s",
                         (antiga,)).fetchone()[0] == datetime(2026, 9, 1, tzinfo=timezone.utc)
        nova = _lead(c, evento_tipo="casamento")
        assert c.execute("select ficha_completa_em from prospeccao where id=%s", (nova,)).fetchone()[0] is None
        c.execute("update prospeccao set evento_em='2027-05-01', evento_convidados=90 where id=%s", (nova,))
        t1 = c.execute("select ficha_completa_em from prospeccao where id=%s", (nova,)).fetchone()[0]
        assert t1 is not None
        c.execute("update prospeccao set evento_convidados=120 where id=%s", (nova,))
        assert c.execute("select ficha_completa_em from prospeccao where id=%s", (nova,)).fetchone()[0] == t1, \
            "o instante não anda a cada mexida (TRAVA 3)"
        c.execute("update prospeccao set evento_em=null where id=%s", (nova,))
        assert c.execute("select ficha_completa_em from prospeccao where id=%s", (nova,)).fetchone()[0] is None


# ══════════════════════════════════════════════ os gatilhos

def test_ficha_completa_leva_contatado_pra_qualificado_e_nao_puxa_proposta(pool):
    with pool.connection() as c:
        _migrar(c)
        a = _lead(c, "contatado", evento_tipo="15 anos", evento_em=date(2027, 3, 13), evento_convidados=150)
        b = _lead(c, "proposta", evento_tipo="15 anos", evento_em=date(2027, 3, 13), evento_convidados=150)
        falta = _lead(c, "contatado", evento_tipo="15 anos", evento_em=date(2027, 3, 13))
        c.commit()
        fr.aplicar_gatilhos(c, PRIME)
        assert (_status(c, a), _status(c, b), _status(c, falta)) == ("ficha_completa", "proposta", "contatado")


def test_visita_realizada_leva_pra_visita_feita(pool):
    with pool.connection() as c:
        _migrar(c)
        lead = _lead(c, "qualificado")
        c.execute("""insert into eventos_agenda (conta_id, prospeccao_id, titulo, inicio, desfecho)
                     values (%s,%s,'Visita',now() - interval '1 day','realizado')""", (PRIME, lead))
        c.commit()
        fr.aplicar_gatilhos(c, PRIME)
        assert _status(c, lead) == "visita_feita"


def test_a_festa_que_passou_leva_a_venda_fechada_pro_pos_festa(pool):
    ontem = (datetime.now(timezone(timedelta(hours=-3))) - timedelta(days=1)).date()
    with pool.connection() as c:
        _migrar(c)
        fechada = _lead(c, "ganho", evento_em=ontem)
        negociando = _lead(c, "proposta", evento_em=ontem)
        futura = _lead(c, "ganho", evento_em=ontem + timedelta(days=30))
        c.commit()
        fr.aplicar_gatilhos(c, PRIME)
        assert (_status(c, fechada), _status(c, negociando), _status(c, futura)) == \
            ("pos_festa", "proposta", "ganho")
        # e continua contando como venda fechada
        assert "pos_festa" in fr.chaves_fechadas(fr.etapas(c, PRIME))


# ══════════════════════════════════════════════ a agenda leva o card

def _visita(c, lead, *, dias=-1, desfecho=None):
    return c.execute("""insert into eventos_agenda (conta_id, prospeccao_id, titulo, inicio, desfecho,
                                                   criado_em)
                        values (%s,%s,'Visita',now() + %s * interval '1 day',%s, now() - interval '3 days')
                        returning id""", (PRIME, lead, dias, desfecho)).fetchone()[0]


def test_faltou_volta_pra_qualificado_com_remarcar_e_o_gatilho_nao_devolve(pool):
    with pool.connection() as c:
        _migrar(c)
        lead = _lead(c, "qualificado")
        ev = _visita(c, lead)
        c.execute("update eventos_agenda set desfecho='nao_realizado' where id=%s", (ev,))
        assert fr.card_pela_visita(c, PRIME, ev, "nao_realizado") == "ficha_completa"
        c.commit()
        assert _status(c, lead) == "ficha_completa"
        nota = c.execute("select descricao from prospeccao_atividades where prospeccao_id=%s",
                         (lead,)).fetchone()[0]
        assert nota.startswith("Faltou à visita de") and nota.endswith("remarcar.")
        # a visita que faltou continua 'ativa': o gatilho não pode devolver o card
        fr.aplicar_gatilhos(c, PRIME)
        assert _status(c, lead) == "ficha_completa"
        # visita NOVA, marcada depois, leva de volta pra Visita marcada
        c.execute("""insert into eventos_agenda (conta_id, prospeccao_id, titulo, inicio, criado_em)
                     values (%s,%s,'Visita',now() + interval '2 days', now() + interval '1 minute')""",
                  (PRIME, lead))
        c.commit()
        fr.aplicar_gatilhos(c, PRIME)
        assert _status(c, lead) == "qualificado"


def test_faltou_e_remarcou_a_mesma_visita_volta_pra_visita_marcada(pool):
    """Revisão de 27/09/2026: remarcar move a MESMA visita (não cria outra), e o card
    ficava preso em Qualificado — o gatilho não dispara pra visita que já existia."""
    with pool.connection() as c:
        _migrar(c)
        lead = _lead(c, "qualificado")
        ev = _visita(c, lead)
        c.execute("update eventos_agenda set desfecho='nao_realizado' where id=%s", (ev,))
        fr.card_pela_visita(c, PRIME, ev, "nao_realizado")
        c.commit()
        assert _status(c, lead) == "ficha_completa"
        c.execute("update eventos_agenda set inicio=now() + interval '2 days', desfecho=null "
                  "where id=%s", (ev,))
        assert fr.card_pela_remarcacao(c, PRIME, ev) == "qualificado"
        c.commit()
        assert _status(c, lead) == "qualificado"
        fr.aplicar_gatilhos(c, PRIME)
        assert _status(c, lead) == "qualificado"
        # e o card adiante não volta
        b = _lead(c, "proposta")
        ev_b = _visita(c, b, dias=2)
        assert fr.card_pela_remarcacao(c, PRIME, ev_b) is None
        assert _status(c, b) == "proposta"


def test_a_venda_fechada_depois_da_festa_tambem_chega_no_pos_festa(pool):
    """Revisão de 27/09/2026: o Ganho marcado à mão DEPOIS da festa é um movimento
    manual mais novo que a meia-noite do dia seguinte — a TRAVA 3 descartava o
    evento, e o card nunca chegava no Pós-festa."""
    antes = (datetime.now(timezone(timedelta(hours=-3))) - timedelta(days=3)).date()
    with pool.connection() as c:
        _migrar(c)
        lead = _lead(c, "proposta", evento_em=antes)
        c.execute("update prospeccao set status='ganho' where id=%s", (lead,))
        fr.registrar_movimento(c, PRIME, lead, "proposta", "ganho", "manual")
        c.commit()
        fr.aplicar_gatilhos(c, PRIME)
        assert _status(c, lead) == "pos_festa"


def test_faltou_com_outra_visita_marcada_ou_card_ja_adiante_nao_mexe(pool):
    with pool.connection() as c:
        _migrar(c)
        a = _lead(c, "qualificado")
        ev = _visita(c, a)
        _visita(c, a, dias=3)
        assert fr.card_pela_visita(c, PRIME, ev, "nao_realizado") is None
        b = _lead(c, "proposta")
        ev_b = _visita(c, b)
        assert fr.card_pela_visita(c, PRIME, ev_b, "nao_realizado") is None
        assert fr.card_pela_visita(c, PRIME, ev_b, "realizado") is None, "não pula de Proposta pra trás"
        assert _status(c, b) == "proposta"


def test_realizado_leva_na_hora_e_conta_como_mao(pool):
    with pool.connection() as c:
        _migrar(c)
        lead = _lead(c, "qualificado")
        ev = _visita(c, lead)
        assert fr.card_pela_visita(c, PRIME, ev, "realizado") == "visita_feita"
        assert c.execute("select motivo from funil_movimentos where prospeccao_id=%s",
                         (lead,)).fetchone()[0] == "agenda"


def test_conta_sem_as_colunas_novas_nao_mexe(pool):
    with pool.connection() as c:
        lead = _lead(c, "qualificado", conta=OUTRA)
        ev = c.execute("""insert into eventos_agenda (conta_id, prospeccao_id, titulo, inicio)
                          values (%s,%s,'Visita',now() - interval '1 day') returning id""",
                       (OUTRA, lead)).fetchone()[0]
        assert fr.card_pela_visita(c, OUTRA, ev, "nao_realizado") is None
        assert fr.card_pela_visita(c, OUTRA, ev, "realizado") is None


# ══════════════════════════════════════════════ o modelo das contas novas

def test_o_modelo_de_eventos_tem_o_desenho_novo():
    chaves = [e[0] for e in rxp.etapas_padrao("eventos")]
    assert chaves == ["novo", "contatado", "ficha_completa", "qualificado", "visita_feita",
                      "proposta", "evento_realizado", "ganho", "pos_festa", "lista_espera",
                      "perdido"]
    rot = {e[0]: e[1] for e in rxp.etapas_padrao("eventos")}
    assert rot["qualificado"] == "Visita marcada" and rot["evento_realizado"] == "Data segurada"
    # o recorrente não muda (§6)
    assert "ficha_completa" not in [e[0] for e in rxp.etapas_padrao("recorrente")]


def test_a_conta_nova_nasce_com_o_pos_festa_na_fase_pos_venda(pool):
    with pool.connection() as c:
        fm.semear(c, 99, "eventos")
        e = _etapas(c, 99)
        assert e["pos_festa"][2] == "pos" and e["ganho"][2] == "fechamento" and e["novo"][2] == "venda"


def test_a_prime_migrada_fica_dentro_do_modelo(pool):
    with pool.connection() as c:
        _migrar(c)
        _migrar_lista(c)
        assert c.execute("select festas_por_dia from contas where id=%s", (PRIME,)).fetchone()[0] == 1
        assert fm.desencontro(c, PRIME, "eventos") == 0
        # e o plano não propõe tirar nada que ela usa, nem criar de novo
        assert [i for i in fm.plano(c, PRIME, "eventos") if i["acao"] in ("criar", "rotulo")] == []
