"""A vista Atendimento do funil (finance/atendimento.py, docs/mockups/funil_atendimento.html).

O que cada teste segura:
* a etapa é a mais adiantada que o lead alcançou, e ninguém arrasta: vem da conversa
  (respondido, ofertada), da ficha (qualificado) e da agenda (marcada);
* parou = perdido, ou 3 dias sem o cliente falar antes de marcar — com a etapa;
* quem atende: a IA (regra por número com a IA ligada) ou a equipe;
* a régua compara os dois lados sempre com tudo, e o filtro de quem atende é do quadro;
* a saudação automática do celular não é resposta (nem no Desafio);
* §6: festa só pra quem vende festa; recorrente qualifica por segmento e porte, e
  oferta reunião; os outros perfis não têm a vista.
"""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import atendimento as at

BASE = Path(__file__).resolve().parents[1] / "db" / "migracoes"
BRT = timezone(timedelta(hours=-3))
EMPRESA, CHIP2 = 34, 36
AGORA = datetime(2026, 9, 27, 12, tzinfo=BRT)

_SQL = """
create table nichos (id bigserial primary key, slug text, nome text);
create table contas (id bigint primary key, nome text, chip_de bigint, nicho_id bigint);
create table canais_config (conta_id bigint, canal text, provedor text, ativo boolean,
  desconectado_em timestamptz, rotulo text);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text, ativo boolean default true, whatsapp text);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  contato text, empresa text, telefone text, whatsapp text, status text default 'contatado',
  evento_tipo text, evento_em date, evento_convidados int, segmento text, porte text,
  criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text default 'whatsapp', chip_id bigint, agente_ativo boolean default true,
  status text default 'aberta', contato_ref text);
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, texto text, membro_id bigint, provider_sid text,
  status text, criado_em timestamptz default now());
create table eventos_agenda (id bigserial primary key, conta_id bigint, membro_id bigint,
  titulo text, inicio timestamptz, status text default 'ativo', tipo text default 'empresa',
  prospeccao_id bigint, tipo_evento text);
"""


@pytest.fixture()
def pool(monkeypatch):
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_atendimento_test"
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
        c.execute("insert into contas (id, nome, chip_de) values (%s,'Prime',null),(%s,'CP Thiago',%s)",
                  (EMPRESA, CHIP2, EMPRESA))
        c.execute("insert into canais_config values (%s,'whatsapp','qr',true,null,'CP Zarb'),"
                  "(%s,'whatsapp','qr',true,null,null)", (EMPRESA, CHIP2))
        for m in ("388_regra_por_chip.sql", "401_ia_fora_da_esteira.sql"):
            c.execute((BASE / m).read_text(encoding="utf-8"))
        c.commit()
    from finance import visita as vis
    monkeypatch.setattr(vis, "vende_festa", lambda *a, **k: True)
    yield p
    p.close()


@pytest.fixture()
def prime(pool):
    with pool.connection() as c:
        ids = {n: c.execute("insert into membros (conta_id, nome, papel) values (%s,%s,'vendedor') "
                            "returning id", (EMPRESA, n)).fetchone()[0]
               for n in ("ZAQ SDR", "Jacqueline", "Pedro")}
        c.execute("""insert into chip_regra (conta_id, chip_id, membro_id, ia_ligada)
                     values (%s,%s,%s,true)""", (EMPRESA, CHIP2, ids["ZAQ SDR"]))
        c.commit()
    return ids


def _lead(pool, membro, *, nome="Renata", chega=None, ia=False, resp_s=None, autor="humano",
          qualif=False, oferta=None, visita=False, status="contatado", ultimo_in=None,
          segmento=None, porte=None):
    chega = chega or AGORA - timedelta(days=1)
    with pool.connection() as c:
        lead = c.execute("""insert into prospeccao (conta_id, vendedor_id, contato, status, evento_tipo,
                                                    evento_em, evento_convidados, segmento, porte, criado_em)
                            values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                         (EMPRESA, membro, nome, status, "15 anos" if qualif else None,
                          "2027-03-13" if qualif else None, 150 if qualif else None,
                          segmento, porte, chega)).fetchone()[0]
        if ia:
            c.execute("insert into chip_regra_leads (prospeccao_id, conta_id, chip_id, membro_id) "
                      "values (%s,%s,%s,%s)", (lead, EMPRESA, CHIP2, membro))
        conv = c.execute("insert into conversas (conta_id, prospeccao_id, chip_id) values (%s,%s,%s) "
                         "returning id", (EMPRESA, lead, CHIP2 if ia else None)).fetchone()[0]
        c.execute("insert into mensagens (conversa_id, direcao, autor, texto, criado_em) "
                  "values (%s,'in','lead','oi, quero saber do espaço',%s)", (conv, chega))
        if resp_s is not None:
            c.execute("insert into mensagens (conversa_id, direcao, autor, texto, criado_em) "
                      "values (%s,'out',%s,%s,%s)",
                      (conv, autor, oferta or "Oi! Pra quando seria?", chega + timedelta(seconds=resp_s)))
        if ultimo_in:
            c.execute("insert into mensagens (conversa_id, direcao, autor, texto, criado_em) "
                      "values (%s,'in','lead','ok',%s)", (conv, ultimo_in))
        if visita:
            c.execute("""insert into eventos_agenda (conta_id, titulo, inicio, prospeccao_id)
                         values (%s,'Visita Renata',%s,%s)""", (EMPRESA, AGORA + timedelta(days=2), lead))
        c.commit()
    return lead, conv


def _col(d, chave):
    return next(c for c in d["colunas"] if c["chave"] == chave)


def _ids(col):
    return [x["id"] for x in col["cards"]]


def test_cada_lead_na_etapa_mais_adiantada(pool, prime):
    j = prime["Jacqueline"]
    chegou, _ = _lead(pool, j, ultimo_in=AGORA - timedelta(hours=2))
    resp, _ = _lead(pool, j, resp_s=60, ultimo_in=AGORA - timedelta(hours=2))
    qual, _ = _lead(pool, j, resp_s=60, qualif=True, ultimo_in=AGORA - timedelta(hours=2))
    ofe, _ = _lead(pool, j, resp_s=60, oferta="Que tal vir conhecer o espaço na terça?",
                   ultimo_in=AGORA - timedelta(hours=2))
    marc, _ = _lead(pool, j, resp_s=60, visita=True)
    d = at.dados(pool, EMPRESA, "eventos", agora=AGORA)
    assert _ids(_col(d, "chegou")) == [chegou]
    assert _ids(_col(d, "respondido")) == [resp]
    assert _ids(_col(d, "qualificado")) == [qual]
    assert _ids(_col(d, "ofertada")) == [ofe]
    assert _ids(_col(d, "marcada")) == [marc]
    assert d["total"] == 5


def test_parou_e_perdido_ou_tres_dias_calado_antes_de_marcar(pool, prime):
    j = prime["Jacqueline"]
    perdido, _ = _lead(pool, j, resp_s=60, status="perdido", ultimo_in=AGORA - timedelta(hours=1))
    sumiu, _ = _lead(pool, j, resp_s=60, qualif=True, chega=AGORA - timedelta(days=5))
    marcada_calada, _ = _lead(pool, j, resp_s=60, visita=True, chega=AGORA - timedelta(days=5))
    d = at.dados(pool, EMPRESA, "eventos", agora=AGORA)
    parou = _col(d, "parou")
    assert set(_ids(parou)) == {perdido, sumiu}
    assert {x["id"]: x["etapa"] for x in parou["cards"]}[sumiu] == "qualificado"
    assert _ids(_col(d, "marcada")) == [marcada_calada], "quem marcou não parou"


def test_quem_atende_e_a_regua_ia_x_equipe(pool, prime):
    z, j = prime["ZAQ SDR"], prime["Jacqueline"]
    _lead(pool, z, ia=True, resp_s=30, autor="bot", qualif=True, nome="Mariana")
    _lead(pool, z, ia=True, resp_s=40, autor="bot", nome="Luana")
    _lead(pool, j, resp_s=600, nome="Carlos")
    _lead(pool, j, nome="Renata")
    d = at.dados(pool, EMPRESA, "eventos", agora=AGORA)
    ia, eq = d["regua"]
    assert ia["ia"] and ia["quem"] == "Zaq" and ia["chegou"] == 2 and ia["respondido"] == 2
    assert ia["qualificado"] == 1 and ia["qualificado_pct"] == 50
    assert ia["resp_min"] == 0.6                           # mediana de 30 s e 40 s
    assert not eq["ia"] and eq["chegou"] == 2 and eq["respondido"] == 1 and eq["resp_min"] == 10.0
    card = next(x for x in _col(d, "qualificado")["cards"])
    assert card["ia"] and card["quem"] == "Zaq" and card["chip_nome"] == "CP Thiago"
    # o filtro é do quadro; a régua continua com os dois lados
    so_ia = at.dados(pool, EMPRESA, "eventos", quem="ia", agora=AGORA)
    assert sum(c["n"] for c in so_ia["colunas"]) == 2 and len(so_ia["regua"]) == 2
    so_eq = at.dados(pool, EMPRESA, "eventos", quem="equipe", agora=AGORA)
    assert {x["nome"] for c in so_eq["colunas"] for x in c["cards"]} == {"Carlos", "Renata"}
    # o chip filtra quadro e régua
    principal = at.dados(pool, EMPRESA, "eventos", chip=str(EMPRESA), agora=AGORA)
    assert principal["total"] == 2 and len(principal["regua"]) == 1


def test_a_saudacao_do_celular_nao_e_resposta(pool, prime):
    j = prime["Jacqueline"]
    saud = ("Olá, tudo bem? me chamo Thiago Pinheiro e sou o gerente de vendas da Prime eventos, "
            "estou aqui para lhe ajudar.")
    _lead(pool, j, resp_s=8, oferta=saud, chega=AGORA - timedelta(days=20), nome="Antiga")
    novo, _ = _lead(pool, j, resp_s=8, oferta=saud, nome="Renata")
    d = at.dados(pool, EMPRESA, "eventos", agora=AGORA)
    assert novo in _ids(_col(d, "chegou")), "a saudação não respondeu a Renata"


def test_recorrente_qualifica_por_segmento_e_porte_e_oferta_reuniao(pool, prime):
    j = prime["Jacqueline"]
    q, _ = _lead(pool, j, resp_s=60, segmento="Clínica", porte="pequeno",
                 ultimo_in=AGORA - timedelta(hours=1))
    ofe, _ = _lead(pool, j, resp_s=60, oferta="Posso te mostrar numa reunião amanhã?",
                   ultimo_in=AGORA - timedelta(hours=1))
    festa, _ = _lead(pool, j, resp_s=60, oferta="Vem conhecer o espaço?", qualif=True,
                     ultimo_in=AGORA - timedelta(hours=1))
    d = at.dados(pool, EMPRESA, "recorrente", agora=AGORA)
    assert _ids(_col(d, "qualificado")) == [q]
    assert _ids(_col(d, "ofertada")) == [ofe]
    assert festa in _ids(_col(d, "respondido")), "festa e 'conhecer o espaço' não contam fora de eventos"
    rot = {c["chave"]: (c["titulo"], c["sub"]) for c in d["colunas"]}
    assert rot["ofertada"][0] == "Reunião ofertada" and rot["qualificado"][1] == "segmento e porte"
    assert not any("festa" in (t + s).lower() or "visita" in (t + s).lower() for t, s in rot.values())


def test_so_eventos_e_recorrente_tem_a_vista():
    for p in ("clinica", "seguros", "obras", "produto"):
        assert at.dados(None, EMPRESA, p) is None
    rot = at.rotulos("eventos")
    assert rot["ofertada"][0] == "Visita ofertada" and "festa" in rot["qualificado"][1]


def test_lead_de_fora_do_periodo_e_sem_mensagem_nao_entram(pool, prime):
    j = prime["Jacqueline"]
    _lead(pool, j, chega=AGORA - timedelta(days=40))                 # agosto
    with pool.connection() as c:                                         # cadastrado à mão, sem conversa
        c.execute("insert into prospeccao (conta_id, vendedor_id, criado_em) values (%s,%s,%s)",
                  (EMPRESA, j, AGORA - timedelta(days=1)))
        c.commit()
    dentro, _ = _lead(pool, j)
    d = at.dados(pool, EMPRESA, "eventos", agora=AGORA)
    assert d["total"] == 1 and _ids(_col(d, "chegou")) == [dentro]
    sete = at.dados(pool, EMPRESA, "eventos", periodo="7d", agora=AGORA)
    assert sete["periodo_rot"] == "últimos 7 dias" and sete["total"] == 1
