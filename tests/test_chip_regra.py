"""A regra por número (migração 386, finance/chip_regra.py) e a IA que atende nela.

O CASO QUE ESTE ARQUIVO FIXA (26/09/2026). A Prime (conta 34) tem dois chips e um
rodízio só. O dono pôs um tráfego pago no chip "CP Thiago" (conta 36) e quer que todo
contato NOVO desse número vá para o "zaq teste", com a IA atendendo — pra medir a IA
contra a equipe. O que a regra promete, e cada teste abaixo segura um pedaço:

* só contato novo, depois de ligada; nunca tira lead de ninguém;
* quem já é cliente de alguém pelo outro chip fica com essa pessoa, sem IA;
* a IA sai da conversa quando alguém da equipe responde — e o eco da própria IA
  não conta como "alguém respondeu";
* numa rajada de mensagens, uma resposta só;
* horário próprio, com o recado de fora do horário uma vez só;
* o que vem da tela é conferido contra a conta (chip e pessoas);
* o preço que a IA diz é só o liberado, e sempre de referência;
* a IA de um nicho não fala o vocabulário de outro (CLAUDE.md §6).

Schema mínimo dos caminhos exercitados; a 386 entra inteira, lida do arquivo.
"""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import agente as ag
from finance import chip_regra as cr

BASE = Path(__file__).resolve().parents[1] / "db" / "migracoes"
EMPRESA, CHIP2, OUTRA = 34, 36, 99
NUM = "558699990001"

_SQL = """
create table contas (id bigint primary key, nome text, chip_de bigint);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text, ativo boolean default true, cockpit_pausado boolean default false,
  whatsapp text, whatsapp_id text);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, contato text, cnpj text, cpf text, tipo text default 'pj',
  telefone text, whatsapp text, email text, origem text, origem_codigo text,
  status text default 'novo', temperatura text default 'frio', estagio text default 'base',
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text default 'whatsapp', contato_ref text, contato_nome text,
  status text default 'aberta', agente_ativo boolean default false,
  responsavel_membro_id bigint, janela_expira_em timestamptz,
  ultima_msg_em timestamptz default now(), criado_em timestamptz default now(),
  chip_id bigint, visto_ate_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, texto text, membro_id bigint, provider_sid text,
  status text, criado_em timestamptz default now(),
  midia_ref jsonb, midia_tipo text, midia_meta jsonb, midia_arquivo text,
  midia_guardada_em timestamptz, midia_guardada_por bigint);
create unique index idx_mensagens_sid_conversa
  on mensagens (conversa_id, provider_sid) where provider_sid is not null;
create table wa_contatos (conta_id bigint, numero8 text, nome text,
  da_agenda boolean default false, primary key (conta_id, numero8));
create table servicos_catalogo (id bigserial primary key, conta_id bigint, slug text,
  nome text, setup_centavos bigint default 0, mensal_centavos bigint default 0,
  ativo boolean default true, duracao_min int, agente_diz_preco boolean not null default false);
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_chip_regra_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=4, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.execute((BASE / "386_regra_por_chip.sql").read_text(encoding="utf-8"))
        c.execute("insert into contas (id, nome, chip_de) values (%s,'Prime',null),"
                  "(%s,'CP Thiago',%s),(%s,'Outra',null)", (EMPRESA, CHIP2, EMPRESA, OUTRA))
        c.commit()
    yield p
    p.close()


def _membro(c, nome, conta=EMPRESA, ativo=True):
    return c.execute("insert into membros (conta_id, nome, email, ativo) values (%s,%s,%s,%s) "
                     "returning id", (conta, nome, f"{nome.lower()}@x.com", ativo)).fetchone()[0]


@pytest.fixture()
def equipe(pool):
    with pool.connection() as c:
        ids = {n: _membro(c, n) for n in ("ZAQ", "JACQUELINE", "MANOEL", "PEDRO")}
        c.commit()
    return ids


def _regra(c, membro, **kw):
    f = {"ativa": True, "membro_id": membro, "ia_ligada": True, "ia_horario": "24h",
         "ia_apresentacao": "Sou a assistente Zaq, da Prime Eventos", **kw}
    r = cr.salvar(c, EMPRESA, CHIP2, f)
    assert r["ok"], r
    # o teste liga a regra "no passado": `vale_desde` é now() e o contato chega agora
    c.execute("update chip_regra set vale_desde = now() - interval '1 minute'")
    c.commit()


# ══════════════════════════════════════════════ quem recebe

def test_contato_novo_pelo_chip_vai_pro_dono_da_regra(pool, equipe):
    with pool.connection() as c:
        _regra(c, equipe["ZAQ"])
        r = cr.dono_do_contato_novo(c, EMPRESA, CHIP2, contato_novo=True)
        assert r and r["membro_id"] == equipe["ZAQ"]


def test_retomada_e_lead_antigo_ficam_fora(pool, equipe):
    """"Só contatos novos" (decisão do dono): a retomada segue no rodízio."""
    with pool.connection() as c:
        _regra(c, equipe["ZAQ"])
        assert cr.dono_do_contato_novo(c, EMPRESA, CHIP2, contato_novo=False) is None


def test_o_chip_principal_sem_regra_segue_no_rodizio(pool, equipe):
    with pool.connection() as c:
        _regra(c, equipe["ZAQ"])
        # chip nulo = o principal (a própria empresa), que não tem regra
        assert cr.dono_do_contato_novo(c, EMPRESA, None, contato_novo=True) is None


def test_membro_que_saiu_nao_recebe_lead(pool, equipe):
    with pool.connection() as c:
        _regra(c, equipe["ZAQ"])
        c.execute("update membros set ativo=false where id=%s", (equipe["ZAQ"],))
        assert cr.dono_do_contato_novo(c, EMPRESA, CHIP2, contato_novo=True) is None


def test_antes_de_ligar_nao_vale(pool, equipe):
    with pool.connection() as c:
        _regra(c, equipe["ZAQ"])
        c.execute("update chip_regra set vale_desde = now() + interval '1 day'")
        assert cr.dono_do_contato_novo(c, EMPRESA, CHIP2, contato_novo=True) is None


def test_banco_sem_a_386_nao_derruba_a_entrada(pool):
    """Tudo tolerante: sem a tabela, 'sem regra' — e a transação do webhook, que
    carrega a mensagem do cliente, segue viva."""
    with pool.connection() as c:
        c.execute("drop table chip_regra cascade")
        assert cr.dono_do_contato_novo(c, EMPRESA, CHIP2, contato_novo=True) is None
        assert c.execute("select 1").fetchone() == (1,)


def test_atribuir_nao_tira_lead_de_ninguem(pool, equipe):
    with pool.connection() as c:
        _regra(c, equipe["ZAQ"])
        r = cr.regra(c, EMPRESA, CHIP2)
        lead = c.execute("insert into prospeccao (conta_id, vendedor_id) values (%s,%s) "
                         "returning id", (EMPRESA, equipe["PEDRO"])).fetchone()[0]
        assert cr.atribuir(c, EMPRESA, lead, None, r) is None
        assert c.execute("select vendedor_id from prospeccao where id=%s",
                         (lead,)).fetchone()[0] == equipe["PEDRO"]


def test_atribuir_liga_a_ia_mas_nao_por_cima_de_quem_assumiu(pool, equipe):
    with pool.connection() as c:
        _regra(c, equipe["ZAQ"])
        r = cr.regra(c, EMPRESA, CHIP2)
        saidas = []
        for status in ("aberta", "pendente"):
            lead = c.execute("insert into prospeccao (conta_id) values (%s) returning id",
                             (EMPRESA,)).fetchone()[0]
            conv = c.execute("insert into conversas (conta_id, prospeccao_id, chip_id, status) "
                             "values (%s,%s,%s,%s) returning id",
                             (EMPRESA, lead, CHIP2, status)).fetchone()[0]
            assert cr.atribuir(c, EMPRESA, lead, conv, r) == equipe["ZAQ"]
            saidas.append(c.execute("select agente_ativo, responsavel_membro_id from conversas "
                                    "where id=%s", (conv,)).fetchone())
        assert saidas == [(True, equipe["ZAQ"]), (False, equipe["ZAQ"])]


def test_regra_da_conversa_so_vale_pro_lead_do_dono(pool, equipe):
    """O lead que era do Pedro antes da regra continua do Pedro: a IA não entra."""
    with pool.connection() as c:
        _regra(c, equipe["ZAQ"])
        convs = {}
        for quem in ("ZAQ", "PEDRO"):
            lead = c.execute("insert into prospeccao (conta_id, vendedor_id) values (%s,%s) "
                             "returning id", (EMPRESA, equipe[quem])).fetchone()[0]
            convs[quem] = c.execute("insert into conversas (conta_id, prospeccao_id, chip_id) "
                                    "values (%s,%s,%s) returning id",
                                    (EMPRESA, lead, CHIP2)).fetchone()[0]
        assert cr.regra_da_conversa(c, EMPRESA, convs["ZAQ"])
        assert cr.regra_da_conversa(c, EMPRESA, convs["PEDRO"]) is None


def test_dono_do_gemeo(pool, equipe):
    """O mesmo número já é lead do Pedro (pelo principal): a ficha nova vai pro Pedro."""
    with pool.connection() as c:
        c.execute("insert into prospeccao (conta_id, vendedor_id, whatsapp) values (%s,%s,%s)",
                  (EMPRESA, equipe["PEDRO"], "+" + NUM))
        novo = c.execute("insert into prospeccao (conta_id, whatsapp) values (%s,%s) returning id",
                         (EMPRESA, "+" + NUM)).fetchone()[0]
        outro = c.execute("insert into prospeccao (conta_id, whatsapp) values (%s,%s) returning id",
                          (EMPRESA, "+558611112222")).fetchone()[0]
        assert cr.dono_do_gemeo(c, EMPRESA, novo) == equipe["PEDRO"]
        assert cr.dono_do_gemeo(c, EMPRESA, outro) is None


# ══════════════════════════════════════════════ a entrada da mensagem, de ponta a ponta

@pytest.fixture()
def inbound(pool, monkeypatch):
    """O `_wa_inbound_conversa` de verdade, com os avisos em threads desligados."""
    from finance import cockpit as ck
    from finance import distribuicao as dist
    from web import painel_prospeccao as pp
    avisados = []
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(dist, "avisar_vendedor", lambda *a, **k: avisados.append(a[2]))
    monkeypatch.setattr(ck, "avisar_mensagem", lambda *a, **k: None)
    # sem as tabelas do rodízio, o rodízio falha no savepoint — é o "sem regra"
    monkeypatch.setattr(dist, "atribuir_se_sem_dono", lambda *a, **k: None)

    def recebe(texto, sid, chip=CHIP2, numero=NUM):
        return pp._wa_inbound_conversa(c_holder[0], EMPRESA, numero, texto, sid, "Ana", False,
                                       chip_id=chip)
    c_holder = []
    return recebe, c_holder, avisados


def test_contato_novo_do_chip_cai_no_zaq_com_a_ia_ligada(pool, equipe, inbound):
    recebe, ch, avisados = inbound
    with pool.connection() as c:
        _regra(c, equipe["ZAQ"])
        ch.append(c)
        conv, nova = recebe("oi, quanto custa o espaço?", "s1")
        c.commit()
        lead, ia = c.execute("""select p.vendedor_id, cv.agente_ativo from conversas cv
                                  join prospeccao p on p.id = cv.prospeccao_id
                                 where cv.id=%s""", (conv,)).fetchone()
    assert nova and lead == equipe["ZAQ"] and ia is True
    assert avisados == [], "o lead é da IA: ninguém da equipe recebe o 'lead novo pra você'"


def test_cliente_do_pedro_no_outro_chip_fica_com_o_pedro_sem_ia(pool, equipe, inbound):
    recebe, ch, avisados = inbound
    with pool.connection() as c:
        _regra(c, equipe["ZAQ"])
        c.execute("insert into prospeccao (conta_id, vendedor_id, whatsapp, estagio) "
                  "values (%s,%s,%s,'lead')", (EMPRESA, equipe["PEDRO"], "+" + NUM))
        c.execute("""insert into conversas (conta_id, prospeccao_id, contato_ref, chip_id)
                     select %s, id, %s, null from prospeccao""", (EMPRESA, NUM))
        ch.append(c)
        conv, _ = recebe("oi, vi o anúncio", "s1")
        c.commit()
        lead, ia = c.execute("""select p.vendedor_id, cv.agente_ativo from conversas cv
                                  join prospeccao p on p.id = cv.prospeccao_id
                                 where cv.id=%s""", (conv,)).fetchone()
    assert lead == equipe["PEDRO"] and ia is False
    assert avisados == [equipe["PEDRO"]]


def test_regra_desligada_devolve_o_chip_ao_rodizio(pool, equipe, inbound):
    recebe, ch, _ = inbound
    with pool.connection() as c:
        _regra(c, equipe["ZAQ"])
        assert cr.salvar(c, EMPRESA, CHIP2, {"ativa": False, "membro_id": equipe["ZAQ"]})["ok"]
        ch.append(c)
        conv, _ = recebe("oi", "s1")
        c.commit()
        lead = c.execute("select p.vendedor_id from conversas cv join prospeccao p "
                         "on p.id = cv.prospeccao_id where cv.id=%s", (conv,)).fetchone()[0]
    assert lead is None


# ══════════════════════════════════════════════ a tela salva

def test_salvar_confere_chip_e_pessoas_contra_a_conta(pool, equipe):
    with pool.connection() as c:
        de_fora = _membro(c, "INTRUSO", conta=OUTRA)
        assert not cr.salvar(c, EMPRESA, OUTRA, {"ativa": True, "membro_id": equipe["ZAQ"]})["ok"]
        assert not cr.salvar(c, EMPRESA, CHIP2, {"ativa": True, "membro_id": de_fora})["ok"]
        assert not cr.salvar(c, EMPRESA, CHIP2, {"ativa": True, "membro_id": equipe["ZAQ"],
                                                 "aviso_dono_membro_id": de_fora})["ok"]
        assert not cr.salvar(c, EMPRESA, CHIP2, {"ativa": True})["ok"]
        assert not cr.salvar(c, EMPRESA, CHIP2, {"ativa": True, "membro_id": equipe["ZAQ"],
                                                 "ia_horario": "proprio", "ia_dias": []})["ok"]


def test_vale_desde_so_recomeca_quando_religa(pool, equipe):
    with pool.connection() as c:
        f = {"ativa": True, "membro_id": equipe["ZAQ"]}
        cr.salvar(c, EMPRESA, CHIP2, f)
        c.execute("update chip_regra set vale_desde = '2026-09-01'")
        cr.salvar(c, EMPRESA, CHIP2, {**f, "ia_apresentacao": "outra"})
        assert c.execute("select vale_desde::date::text from chip_regra").fetchone()[0] == "2026-09-01"
        cr.salvar(c, EMPRESA, CHIP2, {**f, "ativa": False})
        cr.salvar(c, EMPRESA, CHIP2, f)
        assert c.execute("select vale_desde::date = current_date from chip_regra").fetchone()[0]


def test_listar_traz_a_regra_de_cada_chip(pool, equipe):
    with pool.connection() as c:
        _regra(c, equipe["ZAQ"])
        chips = [{"id": EMPRESA, "principal": True}, {"id": CHIP2, "principal": False}]
        lst = cr.listar(c, EMPRESA, chips)
    assert lst[0]["regra"] is None
    assert lst[1]["regra"]["membro_id"] == equipe["ZAQ"] and lst[1]["regra"]["vale_desde_txt"]


# ══════════════════════════════════════════════ horário

def _utc(dia_semana_brt, hora_brt):
    # 2026-09-28 é segunda
    base = datetime(2026, 9, 28, tzinfo=timezone(timedelta(hours=-3)))
    return (base + timedelta(days=dia_semana_brt, hours=hora_brt)).astimezone(timezone.utc)


def test_horario_proprio_em_hora_de_brasilia():
    r = {"ia_horario": "proprio", "ia_dias": [0, 1, 2, 3, 4], "ia_hora_ini": 8, "ia_hora_fim": 18}
    assert cr.ia_pode_falar(r, _utc(0, 8))
    assert not cr.ia_pode_falar(r, _utc(0, 18))
    assert not cr.ia_pode_falar(r, _utc(0, 7))
    assert not cr.ia_pode_falar(r, _utc(5, 10))      # sábado
    assert cr.ia_pode_falar({"ia_horario": "24h"}, _utc(6, 3))


def test_recado_de_fora_usa_a_hora_de_abertura():
    assert "às 9h" in cr.texto_fora({"ia_hora_ini": 9})
    assert cr.texto_fora({"ia_fora_texto": "  volto já  "}) == "volto já"


# ══════════════════════════════════════════════ a conversa: pausa, rajada, fora do horário

def _conversa(c, equipe):
    lead = c.execute("insert into prospeccao (conta_id, vendedor_id) values (%s,%s) returning id",
                     (EMPRESA, equipe["ZAQ"])).fetchone()[0]
    return c.execute("insert into conversas (conta_id, prospeccao_id, chip_id, agente_ativo) "
                     "values (%s,%s,%s,true) returning id", (EMPRESA, lead, CHIP2)).fetchone()[0]


def _msg(c, conv, autor, texto, *, seg_atras=0, sid=None, membro=None):
    c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, provider_sid,
                                        membro_id, criado_em)
                 values (%s,'whatsapp',%s,%s,%s,%s,%s, now() - %s * interval '1 second')""",
              (conv, "in" if autor == "lead" else "out", autor, texto, sid, membro, seg_atras))


def test_quem_responde_pelo_celular_pausa_a_ia(pool, equipe):
    with pool.connection() as c:
        _regra(c, equipe["ZAQ"])
        r = cr.regra(c, EMPRESA, CHIP2)
        conv = _conversa(c, equipe)
        _msg(c, conv, "lead", "oi", seg_atras=30)
        _msg(c, conv, "bot", "Oi! Sou a assistente Zaq", seg_atras=20)
        assert not cr.pausar_se_humano(c, EMPRESA, conv, r)
        _msg(c, conv, "humano", "deixa comigo, Ana!", seg_atras=10)
        assert cr.pausar_se_humano(c, EMPRESA, conv, r)
        assert c.execute("select status, agente_ativo from conversas where id=%s",
                         (conv,)).fetchone() == ("pendente", False)


def test_o_eco_da_propria_ia_nao_pausa(pool, equipe):
    """O eco da mensagem da IA chega pelo webhook de saída como 'humano'. Se ele
    ganha a corrida, `_add_bot_msg` transforma a linha em bot; e o texto igual a uma
    fala recente da IA é o seguro."""
    with pool.connection() as c:
        _regra(c, equipe["ZAQ"])
        r = cr.regra(c, EMPRESA, CHIP2)
        conv = _conversa(c, equipe)
        _msg(c, conv, "lead", "oi", seg_atras=30)
        _msg(c, conv, "humano", "Oi! Sou a assistente Zaq", sid="WA1")    # o eco chegou antes
        ag._add_bot_msg(c, conv, "whatsapp", "Oi! Sou a assistente Zaq", "WA1")
        assert c.execute("select count(*), min(autor) from mensagens where provider_sid='WA1'"
                         ).fetchone() == (1, "bot")
        assert not cr.pausar_se_humano(c, EMPRESA, conv, r)


def test_add_bot_msg_nao_colide_com_sid_vazio(pool, equipe):
    with pool.connection() as c:
        conv = _conversa(c, equipe)
        ag._add_bot_msg(c, conv, "whatsapp", "um", "")
        ag._add_bot_msg(c, conv, "whatsapp", "dois", "")
        assert c.execute("select count(*) from mensagens where conversa_id=%s and autor='bot'",
                         (conv,)).fetchone()[0] == 2


def test_historico_de_antes_da_regra_nao_pausa(pool, equipe):
    with pool.connection() as c:
        _regra(c, equipe["ZAQ"])
        r = cr.regra(c, EMPRESA, CHIP2)
        conv = _conversa(c, equipe)
        _msg(c, conv, "humano", "conversa antiga pelo celular", seg_atras=3600)
        _msg(c, conv, "lead", "oi de novo")
        assert not cr.pausar_se_humano(c, EMPRESA, conv, r)


def test_rajada_responde_uma_vez(pool, equipe):
    """Três mensagens, três webhooks: a volta que responde lê todas; as outras veem
    que a última fala é da IA e não respondem de novo."""
    with pool.connection() as c:
        _regra(c, equipe["ZAQ"])
        r = cr.regra(c, EMPRESA, CHIP2)
        conv = _conversa(c, equipe)
        for i, t in enumerate(("oi", "tudo bem?", "queria saber do espaço")):
            _msg(c, conv, "lead", t, seg_atras=10 - i)
        assert cr.tem_o_que_responder(c, conv, r)
        _msg(c, conv, "bot", "Oi! Tudo ótimo…")
        assert not cr.tem_o_que_responder(c, conv, r)


def test_recado_de_fora_do_horario_sai_uma_vez_e_a_ia_responde_na_abertura(pool, equipe):
    with pool.connection() as c:
        _regra(c, equipe["ZAQ"], ia_horario="proprio", ia_dias=list(range(7)),
               ia_hora_ini=0, ia_hora_fim=24)
        r = cr.regra(c, EMPRESA, CHIP2)
        conv = _conversa(c, equipe)
        _msg(c, conv, "lead", "oi", seg_atras=60)
        assert not cr.ja_mandou_fora(c, conv, r)
        _msg(c, conv, "bot", cr.texto_fora(r), seg_atras=50)
        assert cr.ja_mandou_fora(c, conv, r)
        # o recado não é resposta: quando a IA abre, a conversa ainda espera por ela
        assert cr.tem_o_que_responder(c, conv, r)
        c.commit()
        assert cr.pendentes_da_abertura(c, EMPRESA) == [conv]


def test_fora_do_horario_ninguem_e_acordado(pool, equipe):
    with pool.connection() as c:
        agora = datetime.now(timezone(timedelta(hours=-3)))
        fechado = [d for d in range(7) if d != agora.weekday()]
        _regra(c, equipe["ZAQ"], ia_horario="proprio", ia_dias=fechado)
        r = cr.regra(c, EMPRESA, CHIP2)
        conv = _conversa(c, equipe)
        _msg(c, conv, "bot", cr.texto_fora(r))
        c.commit()
        assert cr.pendentes_da_abertura(c, EMPRESA) == []


# ══════════════════════════════════════════════ o aviso pra equipe

def test_aviso_registra_e_nao_repete_o_mesmo_assunto(pool, equipe, monkeypatch):
    from finance import cockpit as ck
    push = []
    monkeypatch.setattr(ck, "enviar_push", lambda *a, **k: push.append(a[2]))
    with pool.connection() as c:
        _regra(c, equipe["ZAQ"], aviso_agenda_membro_id=equipe["JACQUELINE"],
               aviso_dono_membro_id=equipe["MANOEL"])
        c.execute("update membros set email=null")   # sem e-mail: não tenta mandar
        c.commit()
        r = cr.regra(c, EMPRESA, CHIP2)
        conv = _conversa(c, equipe)
        c.commit()
    assert cr.avisar(pool, EMPRESA, r, "visita", conversa_id=conv, resumo="sábado 10h") \
        == equipe["JACQUELINE"]
    assert cr.avisar(pool, EMPRESA, r, "visita", conversa_id=conv) is None
    assert cr.avisar(pool, EMPRESA, r, "desconto", conversa_id=conv) == equipe["MANOEL"]
    with pool.connection() as c:
        assert c.execute("select motivo, membro_id from ia_avisos order by id").fetchall() == [
            ("visita", equipe["JACQUELINE"]), ("desconto", equipe["MANOEL"])]
    assert push == [equipe["JACQUELINE"], equipe["MANOEL"]]


# ══════════════════════════════════════════════ o que a IA diz

def test_preco_so_o_liberado_e_sempre_de_referencia(pool):
    with pool.connection() as c:
        c.execute("""insert into servicos_catalogo (conta_id, slug, nome, setup_centavos,
                       agente_diz_preco) values
                       (%s,'pacote','PACOTE PRIME - SEXTA A DOMINGO - 2026',720000,true),
                       (%s,'dj','DJ',172500,false)""", (EMPRESA, EMPRESA))
        esc = ag._precos_escondidos(c, EMPRESA, todos=True)
        # fora da regra, só a clínica esconde: o agente geral de sempre não muda
        assert ag._precos_escondidos(c, EMPRESA) == set()
    assert esc == {"dj"}
    pac = ag._linha_catalogo({"nome": "PACOTE", "slug": "pacote", "setup_centavos": 720000},
                             False, aproximado=True)
    dj = ag._linha_catalogo({"nome": "DJ", "slug": "dj", "setup_centavos": 172500},
                            True, aproximado=True)
    assert "a partir de R$ 7.200" in pac
    assert "R$" not in dj and "NÃO liberado" in dj


def test_banco_sem_a_chave_esconde_todos_os_precos_da_regra(pool):
    with pool.connection() as c:
        c.execute("alter table servicos_catalogo drop column agente_diz_preco")
        assert ag._precos_escondidos(c, EMPRESA, todos=True) is None


def test_a_ia_de_cada_nicho_fala_o_vocabulario_dele():
    """CLAUDE.md §6: quem vende festa convida pra visita; quem vende serviço, pra
    reunião — e nunca ouve falar de festa."""
    r = {"ia_apresentacao": "Sou a assistente Zaq"}
    ev = ag._regra_prompt(r, [], "eventos")
    rec = ag._regra_prompt(r, [], "recorrente")
    assert "VISITA ao espaço" in ev and "festa" in ev
    assert "REUNIÃO" in rec
    assert "festa" not in rec.lower() and "visita (" not in rec
    assert "apresente-se assim" in ev
    assert "não repita" in ag._regra_prompt(r, [("out", "bot", "oi")], "eventos")


# ══════════════════════════════════════════════ o agente de ponta a ponta (IA de mentira)

_SQL_AGENTE = """
create table nichos (id bigserial primary key, slug text, nome text);
alter table contas add column nicho_id bigint;
alter table prospeccao add column segmento text, add column cidade text, add column uf text;
create table agente_config (conta_id bigint primary key, ativo boolean default false,
  limiar_confianca int default 80, horario text default 'comercial', tom text default 'informal',
  max_trocas int default 4, escalar_para text, pode_responder boolean default false,
  pode_qualificar boolean default false, pode_agendar boolean default false,
  pode_orcamento boolean default true, orcamento_proativo boolean default false,
  agendar_modo text default 'off');
create table agente_conhecimento (id bigserial primary key, conta_id bigint, tipo text,
  pergunta text, resposta text, ordem int default 0);
alter table servicos_catalogo add column descricao text default '',
  add column custo_centavos bigint default 0, add column ordem int default 0,
  add column categoria text, add column foto_url text, add column icone text;
"""


@pytest.fixture()
def agente(pool, equipe, monkeypatch):
    """A Prime com a IA da regra ligada e uma IA de mentira que devolve o JSON pedido.
    `pode_responder` DESLIGADO de propósito: na regra quem manda é a chave dela."""
    with pool.connection() as c:
        c.execute(_SQL_AGENTE)
        c.execute("insert into nichos (slug, nome) values ('eventos','Eventos')")
        c.execute("update contas set nicho_id=(select id from nichos) where id=%s", (EMPRESA,))
        c.execute("insert into agente_config (conta_id) values (%s)", (EMPRESA,))
        c.execute("""insert into servicos_catalogo (conta_id, slug, nome, setup_centavos,
                       agente_diz_preco) values
                       (%s,'pacote','PACOTE PRIME - SEXTA A DOMINGO - 2026',720000,true),
                       (%s,'dj','DJ',172500,false)""", (EMPRESA, EMPRESA))
        c.commit()
        _regra(c, equipe["ZAQ"], aviso_agenda_membro_id=equipe["JACQUELINE"])
    from core import brain as _brain
    from finance import evento_lead as _evl
    from types import SimpleNamespace
    estado = {"json": {"acao": "responder", "resposta": "Oi! Sou a assistente Zaq 😊"},
              "prompts": [], "enviados": [], "avisos": []}

    class _Brain:
        def chamar(self, system, mensagens):
            estado["prompts"].append((system, mensagens[0]["content"]))
            import json as _j
            return SimpleNamespace(content=[SimpleNamespace(type="text",
                                                            text=_j.dumps(estado["json"]))])
    monkeypatch.setattr(_brain, "Brain", _Brain)
    monkeypatch.setattr(ag, "_RAJADA_S", 0)
    monkeypatch.setattr(ag, "_nota_gemeo", lambda *a, **k: "")
    monkeypatch.setattr(_evl, "gravar", lambda *a, **k: None)
    monkeypatch.setattr(ag, "_mandar", lambda c, conta, canal, dest, texto, conv=None:
                        estado["enviados"].append(texto) or {"ok": True, "sid": f"WA{len(estado['enviados'])}"})
    monkeypatch.setattr(cr, "avisar", lambda pool, conta, r, motivo, **k:
                        estado["avisos"].append((motivo, k.get("resumo"))))
    return estado


def _conversa_da_ia(pool, equipe, texto="oi, quanto custa o espaço?"):
    with pool.connection() as c:
        conv = _conversa(c, equipe)
        c.execute("update prospeccao set whatsapp=%s", ("+" + NUM,))
        _msg(c, conv, "lead", texto, seg_atras=30)
        c.commit()
    return conv


def test_a_ia_da_regra_se_apresenta_e_esconde_o_preco_nao_liberado(pool, equipe, agente):
    conv = _conversa_da_ia(pool, equipe)
    ag.atender(pool, EMPRESA, conv)
    assert agente["enviados"] == ["Oi! Sou a assistente Zaq 😊"]
    system, pedir = agente["prompts"][0]
    assert "a partir de R$ 7.200" in system
    assert "DJ (slug dj): valor NÃO liberado" in system
    assert "apresente-se assim" in pedir and "avisar_equipe" in pedir
    with pool.connection() as c:
        assert c.execute("select autor, provider_sid from mensagens where conversa_id=%s "
                         "and direcao='out'", (conv,)).fetchall() == [("bot", "WA1")]


def test_a_ia_chama_a_jacqueline_e_segue_a_conversa(pool, equipe, agente):
    agente["json"] = {"acao": "visita", "resposta": "Que ótimo! Vou confirmar com a equipe.",
                      "visita": {"data": "2026-10-03", "hora": "10:00"},
                      "avisar_equipe": {"motivo": "visita", "resumo": "sábado 10h"}}
    conv = _conversa_da_ia(pool, equipe, "posso visitar sábado 10h?")
    ag.atender(pool, EMPRESA, conv)
    assert agente["avisos"] == [("visita", "sábado 10h")]
    assert agente["enviados"] == ["Que ótimo! Vou confirmar com a equipe."]


def test_orcamento_formal_nao_sai_pela_ia_da_regra(pool, equipe, agente):
    agente["json"] = {"acao": "orcamento", "resposta": "O pacote sai a partir de R$ 7.200.",
                      "servicos": [{"slug": "pacote", "qtd": 1}],
                      "evento": {"data": "2026-12-05", "inicio": "20:00", "convidados": 80}}
    conv = _conversa_da_ia(pool, equipe)
    ag.atender(pool, EMPRESA, conv)
    assert agente["enviados"] == ["O pacote sai a partir de R$ 7.200."]


def test_depois_que_alguem_responde_a_ia_fica_quieta(pool, equipe, agente):
    conv = _conversa_da_ia(pool, equipe)
    with pool.connection() as c:
        _msg(c, conv, "humano", "Oi Ana, é a Jacqueline!", seg_atras=10)
        _msg(c, conv, "lead", "oi Jacqueline", seg_atras=5)
        c.commit()
    ag.atender(pool, EMPRESA, conv)
    assert agente["enviados"] == [] and agente["prompts"] == []
    with pool.connection() as c:
        assert c.execute("select status, agente_ativo from conversas where id=%s",
                         (conv,)).fetchone() == ("pendente", False)


def test_fora_do_horario_so_o_recado_uma_vez(pool, equipe, agente):
    agora = datetime.now(timezone(timedelta(hours=-3)))
    with pool.connection() as c:
        c.execute("update chip_regra set ia_horario='proprio', ia_dias=%s",
                  ([d for d in range(7) if d != agora.weekday()],))
        c.commit()
    conv = _conversa_da_ia(pool, equipe)
    ag.atender(pool, EMPRESA, conv)
    with pool.connection() as c:
        _msg(c, conv, "lead", "alô?")
        c.commit()
    ag.atender(pool, EMPRESA, conv)
    assert len(agente["enviados"]) == 1 and "Nosso atendimento volta" in agente["enviados"][0]
    assert agente["prompts"] == []


def test_lead_do_pedro_no_chip_segue_o_agente_de_sempre(pool, equipe, agente):
    """Sem a regra valendo pra conversa, o caminho é o de antes — e aqui, com o
    `pode_responder` desligado, o agente de sempre não fala."""
    conv = _conversa_da_ia(pool, equipe)
    with pool.connection() as c:
        c.execute("update prospeccao set vendedor_id=%s", (equipe["PEDRO"],))
        c.commit()
    ag.atender(pool, EMPRESA, conv)
    assert agente["enviados"] == []
    assert "COMO VOCÊ ATENDE ESTE NÚMERO" not in agente["prompts"][0][1]
