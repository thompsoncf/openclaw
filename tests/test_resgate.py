"""O resgate da IA (migração 396, finance/resgate.py).

O CASO QUE ESTE ARQUIVO FIXA (27/09/2026). O dono da Prime (conta 34) quer que o lead
que a equipe deixou parado 7 dias vá, no 8º, pro "zaq teste" — o membro IA da regra do
chip CP Thiago —, e que o vendedor só fique com ele justificando no histórico da ficha.
O chip principal é QR, e 317 dos 326 parados estão nele: o resgate não pode virar
disparo em massa. Cada teste segura um pedaço do que foi prometido no mockup
(docs/mockups/resgate_ia.html):

* o relógio: mensagem nossa ou nota do PRÓPRIO vendedor recomeçam; nota do sistema ou
  de outra pessoa, não;
* visita marcada pra frente nunca vai; lead esquentado tem prazo maior (ou nunca);
* festa que passou ou é já não é resgate;
* a ordem da fila: cliente esperando, festa mais perto, abertos, perdidos;
* Ensaio: prévia pro supervisor, nenhum lead muda de dono, no ritmo do teto;
* Ligado: aviso ao vendedor, 48h, e só então a passagem — pelo chip da conversa;
* o freio: envio que falha devolve o lead; 3 falhas pausam; chip fora do ar espera;
* depois: a IA da regra responde o lead resgatado; "pare" encerra; gente que fala tira
  a IA da conversa; o follow-up não cobra o membro IA.

Schema mínimo dos caminhos exercitados; a 388 e a 396 entram inteiras, lidas do arquivo.
A IA e o WhatsApp são trocados por dublês — nada sai daqui.
"""
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import chip_regra as cr
from finance import resgate as rg

BASE = Path(__file__).resolve().parents[1] / "db" / "migracoes"
EMPRESA, CHIP2, OUTRA = 34, 36, 99
SUPERVISOR = "11987654321"

_SQL = """
create table nichos (id bigserial primary key, nome text, slug text);
create table contas (id bigint primary key, nome text, chip_de bigint, nicho_id bigint);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text, ativo boolean default true, whatsapp text);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, contato text, telefone text, whatsapp text,
  status text default 'contatado', temperatura text default 'frio', estagio text default 'lead',
  evento_em date, orcamento_id bigint,
  perda_motivo text, perda_descricao text, perda_em timestamptz, perda_etapa text,
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text default 'whatsapp', contato_ref text, status text default 'aberta',
  agente_ativo boolean default false, responsavel_membro_id bigint,
  ultima_msg_em timestamptz default now(), criado_em timestamptz default now(), chip_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, texto text, membro_id bigint, provider_sid text,
  status text, criado_em timestamptz default now());
create unique index idx_mensagens_sid_conversa
  on mensagens (conversa_id, provider_sid) where provider_sid is not null;
create table prospeccao_atividades (id bigserial primary key, prospeccao_id bigint,
  membro_id bigint, tipo text, resultado text, descricao text not null default '',
  criado_em timestamptz not null default now());
create table eventos_agenda (id bigserial primary key, conta_id bigint, membro_id bigint,
  titulo text, inicio timestamptz, status text default 'ativo', tipo_evento text,
  tipo text, prospeccao_id bigint);
create table orcamentos (id bigserial primary key, conta_id bigint, status text,
  criado_em timestamptz default now());
create table orcamento_envios (orcamento_id bigint, conta_id bigint, ok boolean);
create table contratos (id bigserial primary key, conta_id bigint, orcamento_id bigint,
  substitui_id bigint, assinado_em timestamptz, status text);
create table funil_movimentos (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  de text, para text, motivo text, membro_id bigint, criado_em timestamptz default now());
create table funil_etapas (conta_id bigint, chave text, fase text, gatilho text);
create table ia_orcamentos (orcamento_id bigint primary key, conta_id bigint,
  prospeccao_id bigint, estado text default 'conferir', bloqueio text);
create table canais_config (conta_id bigint, canal text, provedor text, ativo boolean,
  desconectado_em timestamptz, rotulo text);
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_resgate_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=6, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.execute((BASE / "388_regra_por_chip.sql").read_text(encoding="utf-8"))
        c.execute((BASE / "396_resgate_ia.sql").read_text(encoding="utf-8"))
        c.execute((BASE / "398_resgate_toques.sql").read_text(encoding="utf-8"))
        c.execute((BASE / "401_ia_fora_da_esteira.sql").read_text(encoding="utf-8"))
        c.execute((BASE / "403_resgate_origem_e_espelho.sql").read_text(encoding="utf-8"))
        c.execute((BASE / "409_resgate_teste_chip.sql").read_text(encoding="utf-8"))
        c.execute((BASE / "445_resgate_previa_sem_dobro.sql").read_text(encoding="utf-8"))
        c.execute((BASE / "446_resgate_previa_sem_dobro_de_verdade.sql").read_text(encoding="utf-8"))
        c.execute((BASE / "610_travas.sql").read_text(encoding="utf-8"))
        c.execute("insert into nichos (nome, slug) values ('Eventos','eventos')")
        c.execute("insert into contas (id, nome, chip_de, nicho_id) values "
                  "(%s,'Prime',null,1),(%s,'CP Thiago',%s,null),(%s,'Outra',null,1)",
                  (EMPRESA, CHIP2, EMPRESA, OUTRA))
        c.execute("insert into canais_config values (%s,'whatsapp','qr',true,null),"
                  "(%s,'whatsapp','qr',true,null)", (EMPRESA, CHIP2))
        c.execute("insert into funil_etapas (conta_id, chave, fase) values (%s,'fechado','fechamento')", (EMPRESA,))
        c.commit()
    yield p
    p.close()


def _membro(c, nome, conta=EMPRESA, ativo=True):
    return c.execute("insert into membros (conta_id, nome, email, ativo) values (%s,%s,%s,%s) "
                     "returning id", (conta, nome, f"{nome.lower()}@x.com", ativo)).fetchone()[0]


@pytest.fixture()
def equipe(pool):
    with pool.connection() as c:
        ids = {n: _membro(c, n) for n in ("ZAQ", "PEDRO", "JACQUELINE", "MANOEL")}
        r = cr.salvar(c, EMPRESA, CHIP2, {"ativa": True, "membro_id": ids["ZAQ"], "ia_ligada": True,
                                          "ia_horario": "24h",
                                          "ia_apresentacao": "Sou a assistente Zaq, da Prime"})
        assert r["ok"], r
        c.commit()
    return ids


@pytest.fixture()
def duble(monkeypatch):
    """A IA escreve sempre a mesma coisa; o WhatsApp guarda o que 'saiu'."""
    saiu, avisos = [], []
    from finance import whatsapp_out as wo
    monkeypatch.setattr(rg, "redigir",
                        lambda pool, conta, lead, regra, agora=None: f"Oi {lead['quem']}, voltei!")
    monkeypatch.setattr(rg, "redigir_toque",
                        lambda pool, conta, lead, n, agora=None: f"Toque {n} pra {lead['quem']}")
    monkeypatch.setattr(wo, "preparar", lambda c, conta: {"conta": conta})
    estado = {"ok": True}

    def _env(destino, numero, texto, *, chip_id=None):
        saiu.append({"numero": numero, "texto": texto, "chip": chip_id})
        return {"ok": estado["ok"], "sid": f"S{len(saiu)}", "erro": None if estado["ok"] else "caiu"}
    monkeypatch.setattr(wo, "enviar_pronto", _env)
    monkeypatch.setattr(cr, "notificar",
                        lambda pool, conta, mid, titulo, corpo, url: avisos.append((mid, titulo, corpo)))
    return {"saiu": saiu, "avisos": avisos, "estado": estado}


def _cfg(c, equipe, **kw):
    f = {"modo": "ensaio", "membro_id": equipe["ZAQ"], "dias": 7, "aquecido_dias": 14,
         "teto_dia": 20, "hora_ini": 0, "hora_fim": 24, "dias_semana": list(range(7)),
         "supervisor_whatsapp": SUPERVISOR, "incluir_perdidos": True, "aviso_vendedor": True, **kw}
    r = rg.salvar(c, EMPRESA, f)
    assert r["ok"], r
    c.commit()


def _lead(c, vendedor, *, dias=10, quem="Carla", ultimo="out", status="contatado", evento=None,
          chip=None, numero="5586999990001", orcamento=False):
    """Um lead com conversa: a última mensagem há `dias` dias, nossa (`out`) ou do
    cliente (`in`)."""
    quando = datetime.now(timezone.utc) - timedelta(days=dias)
    orc = None
    if orcamento:
        orc = c.execute("insert into orcamentos (conta_id, criado_em) values (%s,%s) returning id",
                        (EMPRESA, quando)).fetchone()[0]
    lid = c.execute("""insert into prospeccao (conta_id, vendedor_id, contato, whatsapp, status,
                                               evento_em, orcamento_id, criado_em)
                       values (%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                    (EMPRESA, vendedor, quem, numero, status, evento, orc,
                     quando - timedelta(days=1))).fetchone()[0]
    cv = c.execute("""insert into conversas (conta_id, prospeccao_id, contato_ref, chip_id,
                                             ultima_msg_em)
                      values (%s,%s,%s,%s,%s) returning id""",
                   (EMPRESA, lid, numero, chip, quando)).fetchone()[0]
    c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, criado_em)
                 values (%s,'whatsapp','in','lead','quanto fica?',%s)""",
              (cv, quando - timedelta(hours=1 if ultimo == "out" else -1)))
    c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, criado_em)
                 values (%s,'whatsapp','out','humano','a partir de R$ 5 mil',%s)""", (cv, quando))
    c.commit()
    return lid, cv


def _ids(xs):
    return [x["id"] for x in xs]


# ══════════════════════════════════════════════ o relógio

def test_passa_no_8o_dia_e_nao_antes(pool, equipe):
    with pool.connection() as c:
        _cfg(c, equipe)
        oito, _ = _lead(c, equipe["PEDRO"], dias=8)
        seis, _ = _lead(c, equipe["PEDRO"], dias=6, numero="5586999990002")
        assert _ids(rg.fila(c, EMPRESA)) == [oito]


def test_nota_do_vendedor_segura_e_recomeca_o_relogio(pool, equipe):
    with pool.connection() as c:
        _cfg(c, equipe)
        lid, _ = _lead(c, equipe["PEDRO"], dias=10)
        r = rg.justificar(c, EMPRESA, lid, equipe["PEDRO"], "cliente viajando, volta dia 10")
        c.commit()
        assert r["ok"]
        assert rg.fila(c, EMPRESA) == []
        x = rg.leads(c, EMPRESA)[0]
        assert x["segurado"] and x["vence_em"] > datetime.now(timezone.utc) + timedelta(days=6)


def test_nota_de_outro_ou_do_sistema_nao_segura(pool, equipe):
    with pool.connection() as c:
        _cfg(c, equipe)
        lid, _ = _lead(c, equipe["PEDRO"], dias=10)
        # a Jacqueline não pode segurar o lead do Pedro — e a tela recusa
        assert not rg.justificar(c, EMPRESA, lid, equipe["JACQUELINE"], "é meu agora")["ok"]
        c.execute("insert into prospeccao_atividades (prospeccao_id, membro_id, tipo, descricao) "
                  "values (%s,%s,'nota','nota dela'),(%s,null,'nota','movido pela régua')",
                  (lid, equipe["JACQUELINE"], lid))
        c.commit()
        assert _ids(rg.fila(c, EMPRESA)) == [lid]


def test_nota_sem_texto_nao_segura(pool, equipe):
    with pool.connection() as c:
        _cfg(c, equipe)
        lid, _ = _lead(c, equipe["PEDRO"], dias=10)
        assert not rg.justificar(c, EMPRESA, lid, equipe["PEDRO"], "  ")["ok"]
        c.execute("insert into prospeccao_atividades (prospeccao_id, membro_id, tipo, descricao) "
                  "values (%s,%s,'ligacao','')", (lid, equipe["PEDRO"]))
        c.commit()
        assert _ids(rg.fila(c, EMPRESA)) == [lid]


# ══════════════════════════════════════════════ o lead que o vendedor esquentou

def test_visita_marcada_pra_frente_nunca_vai(pool, equipe):
    """"se ele marcou a visita a IA não se entromete" — dono, 27/09."""
    with pool.connection() as c:
        _cfg(c, equipe)
        lid, _ = _lead(c, equipe["JACQUELINE"], dias=30)
        c.execute("insert into eventos_agenda (conta_id, titulo, inicio, prospeccao_id) "
                  "values (%s,'Visita — Carla',now() + interval '3 days',%s)", (EMPRESA, lid))
        c.commit()
        assert rg.fila(c, EMPRESA) == []


def test_visita_que_ja_aconteceu_da_prazo_maior(pool, equipe):
    with pool.connection() as c:
        _cfg(c, equipe)
        lid, _ = _lead(c, equipe["JACQUELINE"], dias=10)
        c.execute("insert into eventos_agenda (conta_id, titulo, inicio, prospeccao_id) "
                  "values (%s,'Visita — Carla',now() - interval '11 days',%s)", (EMPRESA, lid))
        c.commit()
        assert rg.fila(c, EMPRESA) == []                 # 10 dias < 14
        c.execute("update mensagens set criado_em = criado_em - interval '6 days'")
        c.execute("update prospeccao set criado_em = criado_em - interval '6 days'")
        c.execute("update eventos_agenda set inicio = inicio - interval '6 days'")
        c.commit()
        assert _ids(rg.fila(c, EMPRESA)) == [lid]         # 16 dias ≥ 14


def test_orcamento_com_aquecido_nunca_fica_com_o_vendedor(pool, equipe):
    with pool.connection() as c:
        _cfg(c, equipe, aquecido_dias="nunca")
        _lead(c, equipe["PEDRO"], dias=40, orcamento=True)
        assert rg.fila(c, EMPRESA) == []
        assert rg.config(c, EMPRESA)["aquecido_dias"] is None


def test_festa_que_passou_ou_e_ja_fica_fora(pool, equipe):
    hoje = datetime.now(timezone(timedelta(hours=-3))).date()
    with pool.connection() as c:
        _cfg(c, equipe)
        _lead(c, equipe["PEDRO"], evento=hoje - timedelta(days=5))
        _lead(c, equipe["PEDRO"], evento=hoje + timedelta(days=2), numero="5586999990002")
        ok, _ = _lead(c, equipe["PEDRO"], evento=hoje + timedelta(days=20), numero="5586999990003")
        assert _ids(rg.fila(c, EMPRESA)) == [ok]


def test_ganho_e_lead_da_propria_ia_ficam_fora(pool, equipe):
    with pool.connection() as c:
        _cfg(c, equipe)
        _lead(c, equipe["PEDRO"], status="fechado")
        _lead(c, equipe["ZAQ"], numero="5586999990002")
        assert rg.fila(c, EMPRESA) == []


# ══════════════════════════════════════════════ a ordem da fila

def test_a_ordem_da_fila(pool, equipe):
    hoje = datetime.now(timezone(timedelta(hours=-3))).date()
    with pool.connection() as c:
        _cfg(c, equipe)
        perdido, _ = _lead(c, equipe["PEDRO"], dias=9, status="perdido", numero="1")
        aberto_velho, _ = _lead(c, equipe["PEDRO"], dias=30, numero="2")
        aberto_novo, _ = _lead(c, equipe["PEDRO"], dias=9, numero="3")
        festa_longe, _ = _lead(c, equipe["PEDRO"], dias=9, evento=hoje + timedelta(days=60), numero="4")
        festa_perto, _ = _lead(c, equipe["PEDRO"], dias=20, evento=hoje + timedelta(days=10), numero="5")
        esperando, _ = _lead(c, equipe["PEDRO"], dias=12, ultimo="in", numero="6")
        assert _ids(rg.fila(c, EMPRESA)) == [esperando, festa_perto, festa_longe, aberto_novo,
                                             aberto_velho, perdido]


def test_sem_os_perdidos(pool, equipe):
    with pool.connection() as c:
        _cfg(c, equipe, incluir_perdidos=False)
        _lead(c, equipe["PEDRO"], status="perdido")
        assert rg.fila(c, EMPRESA) == []


# ══════════════════════════════════════════════ o cartão

def test_ligar_exige_a_regra_do_membro_com_a_ia(pool, equipe):
    with pool.connection() as c:
        cr.salvar(c, EMPRESA, CHIP2, {"ativa": True, "membro_id": equipe["ZAQ"], "ia_ligada": False})
        c.commit()
        r = rg.salvar(c, EMPRESA, {"modo": "ligado", "membro_id": equipe["ZAQ"],
                                   "supervisor_whatsapp": SUPERVISOR, "dias_semana": ["0"]})
        assert not r["ok"] and "IA" in r["erro"]
        # o Ensaio não precisa: nada vai pra cliente
        r = rg.salvar(c, EMPRESA, {"modo": "ensaio", "membro_id": equipe["ZAQ"],
                                   "supervisor_whatsapp": SUPERVISOR, "dias_semana": ["0"]})
        assert r["ok"], r


def test_o_cartao_confere_membro_e_supervisor(pool, equipe):
    with pool.connection() as c:
        de_fora = _membro(c, "FORA", conta=OUTRA)
        c.commit()
        base = {"modo": "ensaio", "supervisor_whatsapp": SUPERVISOR, "dias_semana": ["0"]}
        assert not rg.salvar(c, EMPRESA, {**base, "membro_id": de_fora})["ok"]
        # quem não é dono de regra por número não recebe resgate
        assert not rg.salvar(c, EMPRESA, {**base, "membro_id": equipe["PEDRO"]})["ok"]
        assert not rg.salvar(c, EMPRESA, {**base, "membro_id": equipe["ZAQ"],
                                          "supervisor_whatsapp": ""})["ok"]
        assert not rg.salvar(c, EMPRESA, {**base, "membro_id": equipe["ZAQ"],
                                          "supervisor_whatsapp": "123"})["ok"]
        assert rg.salvar(c, EMPRESA, {**base, "membro_id": equipe["ZAQ"]})["ok"]
        c.commit()
        assert rg.config(c, EMPRESA)["supervisor_whatsapp"] == "55" + SUPERVISOR


def test_horario_do_resgate():
    cfg = {"dias_semana": [0, 1, 2, 3, 4, 5], "hora_ini": 9, "hora_fim": 19}
    qua_14h = datetime(2026, 9, 30, 17, 0, tzinfo=timezone.utc)      # 14h em Brasília
    qua_20h = datetime(2026, 9, 30, 23, 0, tzinfo=timezone.utc)
    dom_14h = datetime(2026, 10, 4, 17, 0, tzinfo=timezone.utc)
    assert rg.pode_rodar_agora(cfg, qua_14h)
    assert not rg.pode_rodar_agora(cfg, qua_20h)
    assert not rg.pode_rodar_agora(cfg, dom_14h)


# ══════════════════════════════════════════════ o Ensaio

def test_ensaio_manda_a_previa_pro_supervisor_e_nao_mexe_no_lead(pool, equipe, duble):
    with pool.connection() as c:
        _cfg(c, equipe)
        lid, _ = _lead(c, equipe["PEDRO"])
    rg.rodar(pool)
    assert len(duble["saiu"]) == 1
    assert duble["saiu"][0]["numero"] == "55" + SUPERVISOR
    assert "Ensaio" in duble["saiu"][0]["texto"] and "Nada foi enviado" in duble["saiu"][0]["texto"]
    with pool.connection() as c:
        assert c.execute("select vendedor_id from prospeccao where id=%s", (lid,)).fetchone()[0] == equipe["PEDRO"]
        assert c.execute("select count(*) from resgate_leads").fetchone()[0] == 0
    # a mesma prévia não sai de novo (o espaçamento também seguraria; aqui é o dedup)
    with pool.connection() as c:
        c.execute("update resgate_envios set criado_em = now() - interval '1 hour'")
        c.commit()
    rg.rodar(pool)
    assert len(duble["saiu"]) == 1


def test_com_a_trava_de_outro_worker_o_ciclo_nao_roda(pool, equipe, duble):
    """03/10/2026, Prime: os dois workers rodaram o ciclo juntos (a trava advisory, atrás
    do pooler, era pega numa conexão de servidor e solta em outra) e o lead #881 recebeu
    a retomada que a IA tinha decidido não mandar. A trava agora é a linha `travas`."""
    with pool.connection() as c:
        _cfg(c, equipe)
        _lead(c, equipe["PEDRO"])
        c.execute("insert into travas (nome, dono, expira_em) "
                  "values ('resgate', 'outro-worker', now() + interval '10 minutes')")
        c.commit()
    rg.rodar(pool)
    assert duble["saiu"] == []
    with pool.connection() as c:
        # a passada que não levou não apaga a trava do outro
        assert c.execute("select dono from travas where nome='resgate'").fetchone()[0] == "outro-worker"
        # o outro worker morreu no meio: vencido o prazo, o próximo ciclo leva
        c.execute("update travas set expira_em = now() - interval '1 second'")
        c.commit()
    rg.rodar(pool)
    assert len(duble["saiu"]) == 1
    with pool.connection() as c:
        assert c.execute("select count(*) from travas").fetchone()[0] == 0   # soltou no fim


def test_o_ritmo_espacamento_e_teto(pool, equipe, duble, monkeypatch):
    with pool.connection() as c:
        _cfg(c, equipe, teto_dia=2)
        for i in range(4):
            _lead(c, equipe["PEDRO"], numero=f"558699999000{i}")
    rg.rodar(pool)
    rg.rodar(pool)                                    # 2ª passada: espaçamento segura
    assert len(duble["saiu"]) == 1
    # sem espaçamento daqui pra frente: quem segura agora é só o teto
    monkeypatch.setattr(rg, "ESPACO_MIN", -60)
    rg.rodar(pool)
    rg.rodar(pool)                                    # teto de 2 no dia
    assert len(duble["saiu"]) == 2


def test_fora_do_horario_nada_sai(pool, equipe, duble):
    with pool.connection() as c:
        _cfg(c, equipe)
        _lead(c, equipe["PEDRO"])
    dom_3h = datetime(2026, 10, 4, 6, 0, tzinfo=timezone.utc)
    with pool.connection() as c:
        rg.salvar(c, EMPRESA, {"modo": "ensaio", "membro_id": equipe["ZAQ"],
                               "supervisor_whatsapp": SUPERVISOR, "dias_semana": ["0"],
                               "hora_ini": 9, "hora_fim": 19})
        c.commit()
    rg.rodar(pool, agora=dom_3h)
    assert duble["saiu"] == []


# ══════════════════════════════════════════════ o Ligado

def _ligar(c, equipe, **kw):
    _cfg(c, equipe, modo="ligado", **kw)


def test_ligado_avisa_o_vendedor_e_so_passa_48h_depois(pool, equipe, duble):
    with pool.connection() as c:
        _ligar(c, equipe)
        lid, cv = _lead(c, equipe["PEDRO"])
    rg.rodar(pool)
    assert duble["saiu"] == []                        # nada vai pro cliente no 1º dia
    assert len(duble["avisos"]) == 1 and duble["avisos"][0][0] == equipe["PEDRO"]
    assert "Segurar este lead" in duble["avisos"][0][2]
    rg.rodar(pool)
    assert len(duble["avisos"]) == 1                  # um aviso por dia por vendedor
    with pool.connection() as c:
        c.execute("update resgate_envios set criado_em = now() - interval '49 hours'")
        c.commit()
    rg.rodar(pool)
    assert len(duble["saiu"]) == 1
    assert duble["saiu"][0]["numero"] == "5586999990001"
    with pool.connection() as c:
        assert c.execute("select vendedor_id from prospeccao where id=%s", (lid,)).fetchone()[0] == equipe["ZAQ"]
        r = c.execute("select vendedor_antes, estado, ativo from resgate_leads where prospeccao_id=%s",
                      (lid,)).fetchone()
        assert r == (equipe["PEDRO"], "chamado", True)
        assert c.execute("select agente_ativo, responsavel_membro_id from conversas where id=%s",
                         (cv,)).fetchone() == (True, equipe["ZAQ"])
        nota = c.execute("select descricao, membro_id from prospeccao_atividades "
                         "where prospeccao_id=%s", (lid,)).fetchone()
        assert nota[1] is None and "Era de PEDRO" in nota[0]
        # a retomada ficou gravada como fala do bot (é o que tira o lead do relógio)
        assert c.execute("select count(*) from mensagens where conversa_id=%s and autor='bot'",
                         (cv,)).fetchone()[0] == 1


def test_o_vendedor_segura_depois_do_aviso(pool, equipe, duble):
    with pool.connection() as c:
        _ligar(c, equipe)
        lid, _ = _lead(c, equipe["PEDRO"])
    rg.rodar(pool)
    with pool.connection() as c:
        rg.justificar(c, EMPRESA, lid, equipe["PEDRO"], "cliente pediu pra falar dia 10")
        c.execute("update resgate_envios set criado_em = now() - interval '49 hours'")
        c.commit()
    rg.rodar(pool)
    assert duble["saiu"] == []


def test_sem_aviso_ligado_passa_direto(pool, equipe, duble):
    with pool.connection() as c:
        _ligar(c, equipe, aviso_vendedor=False)
        _lead(c, equipe["PEDRO"])
    rg.rodar(pool)
    assert len(duble["saiu"]) == 1 and duble["avisos"] == []


def test_sai_pelo_chip_da_conversa(pool, equipe, duble):
    with pool.connection() as c:
        _ligar(c, equipe, aviso_vendedor=False)
        _lead(c, equipe["PEDRO"], chip=CHIP2)
    rg.rodar(pool)
    assert duble["saiu"][0]["chip"] == CHIP2


def test_envio_que_falha_devolve_o_lead_e_3_falhas_pausam(pool, equipe, duble, monkeypatch):
    duble["estado"]["ok"] = False
    monkeypatch.setattr(rg, "ESPACO_MIN", -60)      # uma tentativa por passada, sem esperar
    with pool.connection() as c:
        _ligar(c, equipe, aviso_vendedor=False)
        ids = [_lead(c, equipe["PEDRO"], numero=f"558699999000{i}")[0] for i in range(4)]
    for _ in range(3):
        rg.rodar(pool)
    with pool.connection() as c:
        assert all(c.execute("select vendedor_id from prospeccao where id=%s", (i,)).fetchone()[0]
                   == equipe["PEDRO"] for i in ids)
        assert c.execute("select count(*) from resgate_leads").fetchone()[0] == 0
        assert c.execute("select count(*) from prospeccao_atividades").fetchone()[0] == 0
    rg.rodar(pool)
    with pool.connection() as c:
        cfg = rg.config(c, EMPRESA)
    assert cfg["pausado_em"] and "falharam" in cfg["pausado_motivo"]
    # o freio avisou o supervisor (a última tentativa de envio é esse aviso)
    assert "pausado" in duble["saiu"][-1]["texto"]


def test_chip_fora_do_ar_espera_sem_pausar(pool, equipe, duble):
    with pool.connection() as c:
        _ligar(c, equipe, aviso_vendedor=False)
        lid, _ = _lead(c, equipe["PEDRO"])
        c.execute("update canais_config set desconectado_em = now() where conta_id=%s", (EMPRESA,))
        c.commit()
    rg.rodar(pool)
    rg.rodar(pool)
    textos = [s["texto"] for s in duble["saiu"]]
    assert len(textos) == 1 and "fora do ar" in textos[0]        # o aviso, uma vez só
    with pool.connection() as c:
        assert c.execute("select vendedor_id from prospeccao where id=%s", (lid,)).fetchone()[0] == equipe["PEDRO"]
        assert not rg.config(c, EMPRESA)["pausado_em"]


# ══════════════════════════════════════════════ depois da passagem

def _resgatado(pool, equipe, duble):
    with pool.connection() as c:
        _ligar(c, equipe, aviso_vendedor=False)
        lid, cv = _lead(c, equipe["PEDRO"])
    rg.rodar(pool)
    assert len(duble["saiu"]) == 1
    return lid, cv


def test_a_regra_do_membro_ia_responde_o_lead_resgatado(pool, equipe, duble):
    lid, cv = _resgatado(pool, equipe, duble)
    with pool.connection() as c:
        r = cr.regra_da_conversa(c, EMPRESA, cv)
        assert r and r["membro_id"] == equipe["ZAQ"] and r.get("resgate")
        # o que o Pedro falou antes do resgate não é "alguém respondeu"
        assert not cr.pausar_se_humano(c, EMPRESA, cv, r)
        assert rg.leads_da_ia(c, EMPRESA) == {lid}


def test_pare_encerra_e_o_cliente_nunca_mais_e_chamado(pool, equipe, duble):
    lid, cv = _resgatado(pool, equipe, duble)
    with pool.connection() as c:
        c.execute("insert into mensagens (conversa_id, canal, direcao, autor, texto) "
                  "values (%s,'whatsapp','in','lead','não quero mais, obrigada')", (cv,))
        c.commit()
    rg.rodar(pool)
    with pool.connection() as c:
        assert c.execute("select estado, opt_out from resgate_leads where prospeccao_id=%s",
                         (lid,)).fetchone() == ("parou", True)
        # devolvido ao Pedro e parado de novo, continua fora da fila
        c.execute("update resgate_leads set ativo=false")
        c.execute("update prospeccao set vendedor_id=%s", (equipe["PEDRO"],))
        c.execute("update mensagens set criado_em = now() - interval '20 days'")
        c.commit()
        assert rg.fila(c, EMPRESA) == []


def test_resposta_normal_conta_como_respondeu(pool, equipe, duble):
    lid, cv = _resgatado(pool, equipe, duble)
    with pool.connection() as c:
        c.execute("insert into mensagens (conversa_id, canal, direcao, autor, texto) "
                  "values (%s,'whatsapp','in','lead','oi! ainda tem a data?')", (cv,))
        c.commit()
    rg.rodar(pool)
    with pool.connection() as c:
        assert c.execute("select estado from resgate_leads where prospeccao_id=%s",
                         (lid,)).fetchone()[0] == "respondeu"


def test_gente_da_equipe_falou_a_ia_sai_e_o_supervisor_sabe(pool, equipe, duble):
    lid, cv = _resgatado(pool, equipe, duble)
    with pool.connection() as c:
        c.execute("insert into mensagens (conversa_id, canal, direcao, autor, texto) "
                  "values (%s,'whatsapp','out','humano','Oi Carla, aqui é o Pedro')", (cv,))
        c.commit()
    rg.rodar(pool)
    with pool.connection() as c:
        assert c.execute("select estado from resgate_leads where prospeccao_id=%s",
                         (lid,)).fetchone()[0] == "pausado"
        assert c.execute("select agente_ativo, status from conversas where id=%s",
                         (cv,)).fetchone() == (False, "pendente")
    assert "saí da conversa" in duble["saiu"][-1]["texto"]


def test_o_gestor_devolve_o_lead(pool, equipe, duble):
    lid, _ = _resgatado(pool, equipe, duble)
    with pool.connection() as c:
        c.execute("update prospeccao set vendedor_id=%s where id=%s", (equipe["JACQUELINE"], lid))
        c.commit()
    rg.rodar(pool)
    with pool.connection() as c:
        assert c.execute("select estado, ativo from resgate_leads where prospeccao_id=%s",
                         (lid,)).fetchone() == ("devolvido", False)
        assert rg.leads_da_ia(c, EMPRESA) == set()


# ══════════════════════════════════════════════ o supervisor

def test_so_o_numero_do_supervisor_e_do_supervisor(pool, equipe):
    with pool.connection() as c:
        assert not rg.e_do_supervisor(c, EMPRESA, "55" + SUPERVISOR)      # resgate desligado
        _cfg(c, equipe)
        assert rg.e_do_supervisor(c, EMPRESA, "55" + SUPERVISOR)
        assert rg.e_do_supervisor(c, EMPRESA, "+55 (11) 98765-4321")
        assert not rg.e_do_supervisor(c, EMPRESA, "5586999990001")
        assert not rg.e_do_supervisor(c, OUTRA, "55" + SUPERVISOR)


def test_o_resumo_do_dia(pool, equipe, duble):
    lid, cv = _resgatado(pool, equipe, duble)
    with pool.connection() as c:
        c.execute("insert into mensagens (conversa_id, canal, direcao, autor, texto) "
                  "values (%s,'whatsapp','in','lead','pare de me mandar mensagem')", (cv,))
        c.commit()
    rg.rodar(pool)
    with pool.connection() as c:
        txt = rg.resumo(c, EMPRESA, rg.config(c, EMPRESA), datetime.now(timezone.utc))
    assert "1 chamado" in txt and "1 respondeu" in txt and "pediu pra parar" in txt
    assert "Fila: 0" in txt


def test_o_resumo_sai_uma_vez_no_fim_do_horario(pool, equipe, duble):
    with pool.connection() as c:
        rg.salvar(c, EMPRESA, {"modo": "ensaio", "membro_id": equipe["ZAQ"],
                               "supervisor_whatsapp": SUPERVISOR, "dias_semana": list(range(7)),
                               "hora_ini": 0, "hora_fim": 1})
        c.commit()
    agora = datetime.now(timezone.utc)
    if agora.astimezone(timezone(timedelta(hours=-3))).hour < 1:
        pytest.skip("meia-noite em Brasília: o fim do horário do teste ainda não chegou")
    rg.rodar(pool, agora=agora)
    rg.rodar(pool, agora=agora)
    resumos = [s for s in duble["saiu"] if "Ensaio do resgate · hoje" in s["texto"]]
    assert len(resumos) == 1


def test_o_app_mostra_quando_o_lead_vai_pro_resgate(pool, equipe):
    with pool.connection() as c:
        _cfg(c, equipe)                                    # Ensaio: o app não diz nada
        lid, _ = _lead(c, equipe["PEDRO"], dias=6)
        assert rg.situacao_do_lead(c, EMPRESA, lid) is None
        _ligar(c, equipe)
        st = rg.situacao_do_lead(c, EMPRESA, lid)
        assert st and not st["vencido"] and st["dias"] == 1
        longe, _ = _lead(c, equipe["PEDRO"], dias=1, numero="5586999990002")
        assert rg.situacao_do_lead(c, EMPRESA, longe) is None


# ══════════════════════════════════════════════ o que a revisão pegou

@pytest.mark.parametrize("txt", ["quero orçamento para 100 pessoas", "Parece ótimo!", "Parabéns!",
                                 "quando chega o orçamento?", "não quero sair tarde da festa",
                                 "não quero buffet, só o salão", "pode parar o carro na frente?"])
def test_conversa_normal_nao_e_pedido_de_parar(txt):
    assert not rg.RE_PARAR.search(txt)


@pytest.mark.parametrize("txt", ["pare", "Pare de me mandar mensagem", "não quero mais, obrigada",
                                 "nao tenho interesse", "me tira da lista", "já fechei com outro",
                                 "desisti", "não me mande mais nada"])
def test_pedido_de_parar(txt):
    assert rg.RE_PARAR.search(txt)


def test_envio_que_falha_devolve_a_conversa_como_estava(pool, equipe, duble):
    """A IA ligada numa conversa que voltou pro vendedor responderia o cliente dele."""
    duble["estado"]["ok"] = False
    with pool.connection() as c:
        _ligar(c, equipe, aviso_vendedor=False)
        lid, cv = _lead(c, equipe["PEDRO"])
        c.execute("update conversas set status='pendente', agente_ativo=false, "
                  "responsavel_membro_id=%s where id=%s", (equipe["PEDRO"], cv))
        c.commit()
    rg.rodar(pool)
    with pool.connection() as c:
        assert c.execute("select status, agente_ativo, responsavel_membro_id from conversas "
                         "where id=%s", (cv,)).fetchone() == ("pendente", False, equipe["PEDRO"])


def test_o_lead_que_falhou_nao_trava_a_fila(pool, equipe, duble, monkeypatch):
    with pool.connection() as c:
        _ligar(c, equipe, aviso_vendedor=False)
        ruim, _ = _lead(c, equipe["PEDRO"], dias=9, ultimo="in", numero="5586999990001")
        bom, _ = _lead(c, equipe["PEDRO"], dias=9, numero="5586999990002")
    duble["estado"]["ok"] = False
    rg.rodar(pool)
    duble["estado"]["ok"] = True
    monkeypatch.setattr(rg, "ESPACO_MIN", -60)
    rg.rodar(pool)
    assert [s["numero"] for s in duble["saiu"]] == ["5586999990001", "5586999990002"]


def test_um_chip_fora_do_ar_nao_para_os_outros(pool, equipe, duble):
    with pool.connection() as c:
        _ligar(c, equipe, aviso_vendedor=False)
        _lead(c, equipe["PEDRO"], dias=9, ultimo="in", chip=CHIP2, numero="5586999990001")
        _lead(c, equipe["PEDRO"], dias=9, numero="5586999990002")
        c.execute("update canais_config set desconectado_em = now() where conta_id=%s", (CHIP2,))
        c.commit()
    rg.rodar(pool)
    assert [s["numero"] for s in duble["saiu"]] == ["5586999990002"]


def test_desligar_e_devolver_libera_os_leads_pro_follow_up(pool, equipe, duble):
    lid, cv = _resgatado(pool, equipe, duble)
    with pool.connection() as c:
        rg.salvar(c, EMPRESA, {"modo": "off", "membro_id": equipe["ZAQ"], "dias_semana": ["0"]})
        c.execute("update prospeccao set vendedor_id=%s where id=%s", (equipe["PEDRO"], lid))
        c.commit()
        assert rg.leads_da_ia(c, EMPRESA) == set()          # na hora, sem esperar o ciclo
    rg.rodar(pool)
    with pool.connection() as c:
        assert c.execute("select estado, ativo from resgate_leads").fetchone() == ("devolvido", False)
        assert c.execute("select agente_ativo, responsavel_membro_id from conversas where id=%s",
                         (cv,)).fetchone() == (False, equipe["PEDRO"])


def test_lead_que_a_regra_deu_nao_e_resgatado(pool, equipe):
    with pool.connection() as c:
        _cfg(c, equipe)
        lid, _ = _lead(c, equipe["PEDRO"])
        c.execute("insert into chip_regra_leads (prospeccao_id, conta_id, chip_id, membro_id) "
                  "values (%s,%s,%s,%s)", (lid, EMPRESA, CHIP2, equipe["PEDRO"]))
        c.commit()
        assert rg.fila(c, EMPRESA) == []


def test_supervisor_de_outro_ddd_nao_e_o_supervisor(pool, equipe):
    with pool.connection() as c:
        _cfg(c, equipe)
        assert rg.e_do_supervisor(c, EMPRESA, "551187654321")          # sem o nono dígito
        assert not rg.e_do_supervisor(c, EMPRESA, "5586987654321")     # mesmos 8, outro DDD


def test_testar_so_com_o_resgate_ligado_ou_em_ensaio(pool, equipe, duble):
    with pool.connection() as c:
        _cfg(c, equipe)
        _lead(c, equipe["PEDRO"])
        rg.salvar(c, EMPRESA, {"modo": "off", "membro_id": equipe["ZAQ"],
                               "supervisor_whatsapp": SUPERVISOR, "dias_semana": ["0"]})
        c.commit()
    r = rg.testar(pool, EMPRESA)
    assert not r["ok"] and "Ensaio" in r["erro"]
    assert duble["saiu"] == []


def test_o_supervisor_sem_teste_recebe_um_lembrete_so_e_a_reentrega_nao_duplica(pool, equipe, duble):
    with pool.connection() as c:
        _cfg(c, equipe)
    rg.responder_supervisor(pool, EMPRESA, "ok", "M1")
    rg.responder_supervisor(pool, EMPRESA, "ok", "M1")          # reentrega
    rg.responder_supervisor(pool, EMPRESA, "valeu", "M2")
    assert len(duble["saiu"]) == 1 and "supervisor do resgate" in duble["saiu"][0]["texto"]
    with pool.connection() as c:
        # a mensagem dele fica guardada, mesmo sem virar conversa
        assert c.execute("select count(*) from resgate_envios where tipo='do_supervisor'"
                         ).fetchone()[0] == 2


def test_o_resgatado_que_escreveu_fora_do_horario_e_atendido_na_abertura(pool, equipe, duble):
    lid, cv = _resgatado(pool, equipe, duble)
    with pool.connection() as c:
        c.execute("update chip_regra set ia_horario='proprio', ia_dias='{0,1,2,3,4,5,6}', "
                  "ia_hora_ini=0, ia_hora_fim=24")
        c.execute("insert into mensagens (conversa_id, canal, direcao, autor, texto, status, criado_em) "
                  "values (%s,'whatsapp','in','lead','oi, ainda tem?',null, now() + interval '1 second'),"
                  "(%s,'whatsapp','out','bot','volto às 8h',%s, now() + interval '2 seconds')",
                  (cv, cv, cr.STATUS_FORA))
        c.commit()
        assert cv in cr.pendentes_da_abertura(c, EMPRESA)



# ══════════════════════════════════════════════ etapa 2: os toques e o perdido

def _atrasar(c, dias, lead=None):
    """Volta o relógio do resgate `dias` dias (a entrada e o último envio)."""
    c.execute("""update resgate_leads set entrou_em = entrou_em - make_interval(days => %s),
                        ultimo_envio_em = ultimo_envio_em - make_interval(days => %s)"""
              + (" where prospeccao_id=%s" if lead else ""),
              (dias, dias, lead) if lead else (dias, dias))
    c.execute("update resgate_envios set criado_em = criado_em - make_interval(days => %s)", (dias,))
    c.commit()


def test_o_2o_toque_so_no_dia_3(pool, equipe, duble):
    lid, cv = _resgatado(pool, equipe, duble)
    with pool.connection() as c:
        _atrasar(c, 2)
    rg.rodar(pool)
    assert len(duble["saiu"]) == 1                              # dia 2: nada
    with pool.connection() as c:
        _atrasar(c, 1)
    rg.rodar(pool)
    assert duble["saiu"][-1]["texto"] == "Toque 2 pra Carla"
    assert duble["saiu"][-1]["numero"] == "5586999990001"
    with pool.connection() as c:
        assert c.execute("select toques from resgate_leads").fetchone()[0] == 2
        assert c.execute("select count(*) from mensagens where conversa_id=%s and autor='bot'",
                         (cv,)).fetchone()[0] == 2


def test_o_3o_toque_no_dia_7_e_nunca_colado_no_2o(pool, equipe, duble):
    _resgatado(pool, equipe, duble)
    with pool.connection() as c:
        _atrasar(c, 6)                                           # o 2º atrasou até o dia 6
    rg.rodar(pool)
    assert duble["saiu"][-1]["texto"] == "Toque 2 pra Carla"
    with pool.connection() as c:
        _atrasar(c, 1)                                           # dia 7, mas 1 dia depois do 2º
    rg.rodar(pool)
    assert len(duble["saiu"]) == 2
    with pool.connection() as c:
        _atrasar(c, 2)
    rg.rodar(pool)
    assert duble["saiu"][-1]["texto"] == "Toque 3 pra Carla"


def test_quem_respondeu_nao_recebe_toque(pool, equipe, duble):
    lid, cv = _resgatado(pool, equipe, duble)
    with pool.connection() as c:
        c.execute("insert into mensagens (conversa_id, canal, direcao, autor, texto) "
                  "values (%s,'whatsapp','in','lead','vou ver com meu marido')", (cv,))
        c.commit()
        _atrasar(c, 4)
    rg.rodar(pool)
    assert len(duble["saiu"]) == 1


def test_3_toques_sem_resposta_viram_perdido_no_dia_10(pool, equipe, duble):
    lid, cv = _resgatado(pool, equipe, duble)
    with pool.connection() as c:
        c.execute("update resgate_leads set toques=3")
        _atrasar(c, 2)
    rg.rodar(pool)
    with pool.connection() as c:
        assert c.execute("select status from prospeccao where id=%s", (lid,)).fetchone()[0] == "contatado"
        _atrasar(c, 1)
    rg.rodar(pool)
    with pool.connection() as c:
        assert c.execute("select status, perda_motivo, vendedor_id from prospeccao where id=%s",
                         (lid,)).fetchone() == ("perdido", "nao_respondeu", equipe["ZAQ"])
        assert c.execute("select de, para from funil_movimentos where prospeccao_id=%s",
                         (lid,)).fetchone() == ("contatado", "perdido")
        assert c.execute("select estado, ativo from resgate_leads").fetchone() == ("perdido", True)
    assert len(duble["saiu"]) == 1                               # o perdido não manda nada
    # o cliente escreve depois: o resgate registra a resposta, e a IA (dona) segue
    with pool.connection() as c:
        c.execute("insert into mensagens (conversa_id, canal, direcao, autor, texto) "
                  "values (%s,'whatsapp','in','lead','oi, desculpa a demora!')", (cv,))
        c.commit()
    rg.rodar(pool)
    with pool.connection() as c:
        assert c.execute("select estado from resgate_leads").fetchone()[0] == "respondeu"
        assert cr.regra_da_conversa(c, EMPRESA, cv) is not None


def test_o_toque_vem_antes_do_lead_novo_da_fila(pool, equipe, duble):
    _resgatado(pool, equipe, duble)
    with pool.connection() as c:
        _lead(c, equipe["PEDRO"], numero="5586999990009")
        _atrasar(c, 3)
    rg.rodar(pool)
    assert duble["saiu"][-1]["texto"] == "Toque 2 pra Carla"
    assert len(duble["saiu"]) == 2


def test_o_toque_conta_no_teto(pool, equipe, duble):
    _resgatado(pool, equipe, duble)
    with pool.connection() as c:
        _atrasar(c, 3)
        c.execute("update resgate_config set teto_dia=1")
        c.execute("insert into resgate_envios (conta_id, tipo) values (%s,'retomada')", (EMPRESA,))
        c.commit()
    rg.rodar(pool)
    assert len(duble["saiu"]) == 1


# ══════════════════════════════════════════════ etapa 2: a aba Resgate do Desafio

def test_a_aba_resgate_do_desafio(pool, equipe, duble, monkeypatch):
    from finance import desafio_ia as dia
    from finance import visita as vis
    monkeypatch.setattr(vis, "vende_festa", lambda pool, conta: True)
    lid, cv = _resgatado(pool, equipe, duble)
    with pool.connection() as c:
        c.execute("insert into mensagens (conversa_id, canal, direcao, autor, texto) "
                  "values (%s,'whatsapp','in','lead','quero sim!')", (cv,))
        c.execute("insert into eventos_agenda (conta_id, titulo, inicio, prospeccao_id) "
                  "values (%s,'Visita — Carla', now() + interval '2 days', %s)", (EMPRESA, lid))
        c.commit()
    rg.rodar(pool)
    hoje = datetime.now(timezone(timedelta(hours=-3))).date()
    d = dia.resgate(pool, EMPRESA, hoje.replace(day=1), hoje + timedelta(days=1))
    t = d["total"]
    assert (t["chamados"], t["responderam"], t["resp_7d_pct"], t["visitas"], t["contratos"]) == \
        (1, 1, 100, 1, 0)
    assert [f["nome"] for f in d["faixas"]] == ["Aberto"]


def test_a_regua_do_vendedor(pool, equipe):
    """O vendedor voltou a chamar depois de 7 dias de silêncio: o cliente respondeu em
    até 7 dias? Mensagem da IA não entra na régua."""
    from finance import desafio_ia as dia
    with pool.connection() as c:
        lid, cv = _lead(c, equipe["PEDRO"], dias=40)
        c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, criado_em)
                     values (%s,'whatsapp','out','humano','e aí, ainda quer?', now() - interval '30 days'),
                            (%s,'whatsapp','in','lead','quero!', now() - interval '28 days')""", (cv, cv))
        lid2, cv2 = _lead(c, equipe["PEDRO"], dias=40, numero="5586999990002")
        c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, criado_em)
                     values (%s,'whatsapp','out','bot','oi de novo', now() - interval '30 days')""", (cv2,))
        c.commit()
    r = dia.regua_do_vendedor(pool, EMPRESA)
    assert r == {"retomadas": 1, "responderam": 1, "resp_pct": 100, "fecharam": 0}


# ══════════════════════════════════════════════ o que a revisão da etapa 2 pegou

def test_o_toque_nao_sai_se_o_cliente_respondeu_enquanto_a_ia_escrevia(pool, equipe, duble, monkeypatch):
    lid, cv = _resgatado(pool, equipe, duble)
    with pool.connection() as c:
        _atrasar(c, 3)

    def _escreve_e_o_cliente_responde(pool_, conta, lead, n, agora=None):
        with pool_.connection() as c2:
            c2.execute("insert into mensagens (conversa_id, canal, direcao, autor, texto) "
                       "values (%s,'whatsapp','in','lead','oi! tô aqui')", (cv,))
            c2.commit()
        return "Toque que não pode sair"
    monkeypatch.setattr(rg, "redigir_toque", _escreve_e_o_cliente_responde)
    rg.rodar(pool)
    assert "Toque que não pode sair" not in [s["texto"] for s in duble["saiu"]]
    with pool.connection() as c:
        assert c.execute("select toques from resgate_leads").fetchone()[0] == 1


def test_card_fechado_nao_recebe_toque_nem_vira_perdido(pool, equipe, duble):
    lid, cv = _resgatado(pool, equipe, duble)
    with pool.connection() as c:
        c.execute("update prospeccao set status='fechado' where id=%s", (lid,))
        _atrasar(c, 3)
    rg.rodar(pool)
    assert len(duble["saiu"]) == 1
    with pool.connection() as c:
        c.execute("update resgate_leads set toques=3")
        _atrasar(c, 4)
    rg.rodar(pool)
    with pool.connection() as c:
        assert c.execute("select estado from resgate_leads").fetchone()[0] == "chamado"
        assert c.execute("select status from prospeccao where id=%s", (lid,)).fetchone()[0] == "fechado"


@pytest.mark.parametrize("coluna", ["lista_espera", "evento_realizado"])
def test_quem_espera_a_empresa_nao_recebe_toque_nem_vira_perdido(pool, equipe, duble, coluna):
    """A Lista de espera e a Data segurada esperam a EMPRESA (a data abrir, o sinal):
    o toque "ainda faz sentido?" e o perdido por silêncio não valem (revisão de
    27/09/2026)."""
    lid, cv = _resgatado(pool, equipe, duble)
    with pool.connection() as c:
        c.execute("insert into funil_etapas (conta_id, chave, fase, gatilho) "
                  "values (%s,'evento_realizado','venda','orcamento_aprovado')", (EMPRESA,))
        c.execute("update prospeccao set status=%s where id=%s", (coluna, lid))
        _atrasar(c, 3)
    rg.rodar(pool)
    assert len(duble["saiu"]) == 1                           # só a retomada
    with pool.connection() as c:
        c.execute("update resgate_leads set toques=3")
        _atrasar(c, 4)
    rg.rodar(pool)
    with pool.connection() as c:
        assert c.execute("select status from prospeccao where id=%s", (lid,)).fetchone()[0] == coluna


def test_visita_marcada_depois_da_retomada_para_os_toques(pool, equipe, duble):
    lid, cv = _resgatado(pool, equipe, duble)
    with pool.connection() as c:
        c.execute("insert into eventos_agenda (conta_id, titulo, inicio, prospeccao_id) "
                  "values (%s,'Visita — Carla', now() + interval '2 days', %s)", (EMPRESA, lid))
        c.commit()
        _atrasar(c, 3)
    rg.rodar(pool)
    assert len(duble["saiu"]) == 1


def test_o_toque_que_falha_volta_a_contagem(pool, equipe, duble):
    _resgatado(pool, equipe, duble)
    with pool.connection() as c:
        _atrasar(c, 3)
    duble["estado"]["ok"] = False
    rg.rodar(pool)
    with pool.connection() as c:
        assert c.execute("select toques from resgate_leads").fetchone()[0] == 1
        assert c.execute("select ok from resgate_envios where tipo='toque'").fetchone()[0] is False


# ══════════════════════════════════════════════ a IA do número insiste (migração 401)
# Decisão do dono em 27/09/2026: cada trilha com a sua regra. O lead que a IA atende
# desde o primeiro "oi" não é cobrado de ninguém — a própria IA insiste, com a regra
# dos toques do resgate (finance/ia_insiste.py).

from finance import ia_insiste as ii  # noqa: E402


def _insiste(c, equipe, ligada=True):
    r = cr.salvar(c, EMPRESA, CHIP2, {"ativa": True, "membro_id": equipe["ZAQ"], "ia_ligada": True,
                                      "ia_horario": "24h", "ia_insiste": ligada})
    assert r["ok"], r
    c.commit()


def _lead_da_ia(c, equipe, *, dias=4, quem="Bia", numero="5586988880001", ultimo="bot",
                texto_in="quero saber de festa de 15 anos"):
    """O contato novo do chip da IA: ele escreveu, a IA respondeu há `dias` dias."""
    quando = datetime.now(timezone.utc) - timedelta(days=dias)
    lid = c.execute("""insert into prospeccao (conta_id, vendedor_id, contato, whatsapp, criado_em)
                       values (%s,%s,%s,%s,%s) returning id""",
                    (EMPRESA, equipe["ZAQ"], quem, numero, quando - timedelta(hours=2))).fetchone()[0]
    c.execute("insert into chip_regra_leads (prospeccao_id, conta_id, chip_id, membro_id) "
              "values (%s,%s,%s,%s)", (lid, EMPRESA, CHIP2, equipe["ZAQ"]))
    cv = c.execute("""insert into conversas (conta_id, prospeccao_id, contato_ref, chip_id,
                                             agente_ativo, responsavel_membro_id, ultima_msg_em)
                      values (%s,%s,%s,%s,true,%s,%s) returning id""",
                   (EMPRESA, lid, numero, CHIP2, equipe["ZAQ"], quando)).fetchone()[0]
    c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, criado_em)
                 values (%s,'whatsapp','in','lead',%s,%s)""", (cv, texto_in, quando - timedelta(hours=1)))
    if ultimo == "bot":
        c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, criado_em)
                     values (%s,'whatsapp','out','bot','Oi! Pra quantos convidados?',%s)""", (cv, quando))
    c.commit()
    return lid, cv


@pytest.fixture()
def expediente(monkeypatch):
    """A hora do teste é a do relógio; o horário (9h–19h, seg–sáb) é conferido à parte."""
    monkeypatch.setattr(ii, "pode_agora", lambda agora: True)


def _toques(c, lid):
    return c.execute("select toques, toque_em is not null, perdido_em is not null "
                     "from chip_regra_leads where prospeccao_id=%s", (lid,)).fetchone()


def test_a_ia_so_insiste_com_a_chave_ligada(pool, equipe, duble, expediente):
    with pool.connection() as c:
        lid, _ = _lead_da_ia(c, equipe, dias=4)
        assert ii.devidos(c, EMPRESA) == []                     # a chave nasce desligada
        _insiste(c, equipe)
        assert [x["passo"] for x in ii.devidos(c, EMPRESA)] == [1]
    assert ii.rodar(pool)["toques"] == 1
    assert duble["saiu"] == [{"numero": "5586988880001", "texto": "Toque 2 pra Bia", "chip": CHIP2}]
    with pool.connection() as c:
        assert _toques(c, lid) == (1, True, False)
        assert c.execute("select autor from mensagens where texto='Toque 2 pra Bia'").fetchone()[0] == "bot"


def test_a_chave_so_grava_com_a_ia_ligada(pool, equipe):
    with pool.connection() as c:
        r = cr.salvar(c, EMPRESA, CHIP2, {"ativa": True, "membro_id": equipe["ZAQ"], "ia_ligada": False,
                                          "ia_insiste": True})
        assert r["ok"]
        c.commit()
        assert cr.regra(c, EMPRESA, CHIP2)["ia_insiste"] is False


def test_antes_do_dia_3_a_ia_espera(pool, equipe, duble, expediente):
    with pool.connection() as c:
        _insiste(c, equipe)
        _lead_da_ia(c, equipe, dias=2)
    assert ii.rodar(pool)["toques"] == 0 and duble["saiu"] == []


def test_a_ultima_chamada_no_dia_7_e_o_perdido_no_10(pool, equipe, duble, expediente):
    with pool.connection() as c:
        _insiste(c, equipe)
        lid, cv = _lead_da_ia(c, equipe, dias=4)
    ii.rodar(pool)
    with pool.connection() as c:
        assert ii.devidos(c, EMPRESA) == []                    # o lembrete acabou de sair
        c.execute("update chip_regra_leads set toque_em = now() - interval '5 days'")
        c.execute("update resgate_envios set criado_em = now() - interval '5 days'")
        c.commit()
        assert [x["passo"] for x in ii.devidos(c, EMPRESA)] == [2]
    ii.rodar(pool)
    assert duble["saiu"][-1]["texto"] == "Toque 3 pra Bia"
    with pool.connection() as c:
        assert _toques(c, lid)[0] == 2
        c.execute("update chip_regra_leads set toque_em = now() - interval '4 days'")
        c.commit()
    assert ii.rodar(pool)["perdidos"] == 1
    with pool.connection() as c:
        assert c.execute("select status from prospeccao where id=%s", (lid,)).fetchone()[0] == "perdido"
        assert _toques(c, lid)[2] is True
    assert len(duble["saiu"]) == 2                             # o perdido não manda nada


def test_o_cliente_que_volta_a_falar_zera_a_conta(pool, equipe, duble, expediente):
    with pool.connection() as c:
        _insiste(c, equipe)
        lid, cv = _lead_da_ia(c, equipe, dias=10)
    ii.rodar(pool)
    with pool.connection() as c:
        # o lembrete saiu há 7 dias; ele respondeu, a IA respondeu, e ele sumiu de novo
        c.execute("update mensagens set criado_em = now() - interval '7 days' "
                  "where texto='Toque 2 pra Bia'")
        c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, criado_em)
                     values (%s,'whatsapp','in','lead','ainda estou vendo',now() - interval '5 days'),
                            (%s,'whatsapp','out','bot','Fico no aguardo!',now() - interval '4 days')""",
                  (cv, cv))
        c.execute("update chip_regra_leads set toque_em = now() - interval '7 days'")
        c.execute("update resgate_envios set criado_em = now() - interval '7 days'")
        c.commit()
        x = ii.devidos(c, EMPRESA)
        assert [(y["passo"], y["toques"]) for y in x] == [(1, 0)]   # recomeça no lembrete


@pytest.mark.parametrize("caso", ["cliente_por_ultimo", "gente_assumiu", "pediu_pra_parar",
                                  "visita_marcada", "fechado", "de_outro_vendedor",
                                  # revisão de 27/09/2026: quem espera a EMPRESA
                                  "lista_espera", "data_segurada", "orcamento_na_conferencia"])
def test_quando_a_ia_nao_insiste(pool, equipe, duble, expediente, caso):
    with pool.connection() as c:
        _insiste(c, equipe)
        lid, cv = _lead_da_ia(c, equipe, dias=5,
                              ultimo="lead" if caso == "cliente_por_ultimo" else "bot",
                              texto_in="não tenho mais interesse" if caso == "pediu_pra_parar"
                              else "quero saber de festa")
        if caso == "gente_assumiu":
            c.execute("update conversas set agente_ativo=false, status='pendente' where id=%s", (cv,))
        if caso == "visita_marcada":
            c.execute("insert into eventos_agenda (conta_id, titulo, inicio, prospeccao_id) "
                      "values (%s,'Visita — Bia',now() + interval '2 days',%s)", (EMPRESA, lid))
        if caso == "fechado":
            c.execute("update prospeccao set status='fechado' where id=%s", (lid,))
        if caso == "de_outro_vendedor":
            c.execute("update prospeccao set vendedor_id=%s where id=%s", (equipe["PEDRO"], lid))
        if caso == "lista_espera":
            c.execute("update prospeccao set status='lista_espera' where id=%s", (lid,))
        if caso == "data_segurada":
            c.execute("insert into funil_etapas (conta_id, chave, fase, gatilho) "
                      "values (%s,'evento_realizado','venda','orcamento_aprovado')", (EMPRESA,))
            c.execute("update prospeccao set status='evento_realizado' where id=%s", (lid,))
        if caso == "orcamento_na_conferencia":
            c.execute("insert into ia_orcamentos (orcamento_id, conta_id, prospeccao_id, estado) "
                      "values (7001,%s,%s,'conferir')", (EMPRESA, lid))
        c.commit()
        assert ii.devidos(c, EMPRESA) == []


def test_o_toque_que_falha_volta_a_conta_e_o_numero_espera(pool, equipe, duble, expediente):
    duble["estado"]["ok"] = False
    with pool.connection() as c:
        _insiste(c, equipe)
        lid, _ = _lead_da_ia(c, equipe, dias=4)
    assert ii.rodar(pool)["toques"] == 0
    with pool.connection() as c:
        assert _toques(c, lid) == (0, False, False)
        assert c.execute("select ok from resgate_envios where tipo='toque_ia'").fetchone()[0] is False
    duble["estado"]["ok"] = True
    ii.rodar(pool)
    assert len(duble["saiu"]) == 1                             # não tenta de novo por 7 dias


def test_o_teto_e_o_espaco_entre_dois_toques(pool, equipe, duble, expediente):
    with pool.connection() as c:
        _insiste(c, equipe)
        _lead_da_ia(c, equipe, dias=4)
        _lead_da_ia(c, equipe, dias=5, quem="Cris", numero="5586988880002")
    ii.rodar(pool)
    ii.rodar(pool)
    assert [x["texto"] for x in duble["saiu"]] == ["Toque 2 pra Cris"]   # o silêncio mais antigo 1º


def test_o_horario_da_ia_que_insiste():
    brt = timezone(timedelta(hours=-3))
    assert ii.pode_agora(datetime(2026, 9, 28, 9, 0, tzinfo=brt))           # segunda 9h
    assert not ii.pode_agora(datetime(2026, 9, 28, 19, 0, tzinfo=brt))      # 19h já fechou
    assert not ii.pode_agora(datetime(2026, 9, 27, 12, 0, tzinfo=brt))      # domingo


def test_quem_e_a_ia(pool, equipe):
    with pool.connection() as c:
        assert cr.membros_ia(c, EMPRESA) == {equipe["ZAQ"]}
        # o chip do Pedro, com o Pedro atendendo, não faz dele a IA
        c.execute("insert into contas (id, nome, chip_de) values (37,'Chip do Pedro',%s)", (EMPRESA,))
        cr.salvar(c, EMPRESA, 37, {"ativa": True, "membro_id": equipe["PEDRO"], "ia_ligada": False})
        c.commit()
        assert cr.membros_ia(c, EMPRESA) == {equipe["ZAQ"]}
        assert cr.membros_ia(c, OUTRA) == set()



# ══════════════════════════════════════════════ de onde veio, e o perdido pelo motivo
# O mockup das três trilhas, versão 2 (aprovado em 27/09/2026 com as recomendações):
# a coluna Resgate diz de onde o lead veio, o perdido é chamado conforme o motivo, o
# perdido da IA do número volta uma vez depois de 30 dias, e a IA lê o resumo da
# conversa inteira antes de escrever.

def _perdido(c, lid, motivo=None, por="manual", evento=None):
    c.execute("update prospeccao set status='perdido', perda_motivo=%s, evento_em=%s where id=%s",
              (motivo, evento, lid))
    c.execute("insert into funil_movimentos (conta_id, prospeccao_id, de, para, motivo, criado_em) "
              "values (%s,%s,'contatado','perdido',%s, now() - interval '9 days')", (EMPRESA, lid, por))
    c.commit()


def _origem(c, lid):
    return next(x for x in rg.leads(c, EMPRESA) if x["id"] == lid)["origem"]


def test_a_origem_de_cada_lead(pool, equipe):
    with pool.connection() as c:
        _cfg(c, equipe)
        parado, _ = _lead(c, equipe["PEDRO"], dias=10)
        do_vend, _ = _lead(c, equipe["PEDRO"], dias=10, numero="5586999990002")
        da_esteira, _ = _lead(c, equipe["PEDRO"], dias=10, numero="5586999990003")
        _perdido(c, do_vend, "achou_caro")
        _perdido(c, da_esteira, "outro", por="sem_tratativa")
        assert _origem(c, parado) == "follow_up"
        assert _origem(c, do_vend) == "perdido_vendedor"
        assert _origem(c, da_esteira) == "perdido_esteira"


@pytest.mark.parametrize("motivo", rg.MOTIVOS_NAO_CHAMA)
def test_o_perdido_que_nao_se_chama(pool, equipe, motivo):
    with pool.connection() as c:
        _cfg(c, equipe)
        lid, _ = _lead(c, equipe["PEDRO"], dias=10)
        _perdido(c, lid, motivo)
        assert rg.fila(c, EMPRESA) == []


def test_data_indisponivel_so_com_a_festa_a_mais_de_30_dias(pool, equipe):
    hoje = datetime.now(timezone.utc).date()
    with pool.connection() as c:
        _cfg(c, equipe)
        perto, _ = _lead(c, equipe["PEDRO"], dias=10)
        longe, _ = _lead(c, equipe["PEDRO"], dias=10, numero="5586999990002")
        sem, _ = _lead(c, equipe["PEDRO"], dias=10, numero="5586999990003")
        _perdido(c, perto, "data_indisponivel", evento=hoje + timedelta(days=20))
        _perdido(c, longe, "data_indisponivel", evento=hoje + timedelta(days=60))
        _perdido(c, sem, "data_indisponivel")
        assert _ids(rg.fila(c, EMPRESA)) == [longe]


def test_o_pedido_de_data_indisponivel_pergunta_se_e_flexivel():
    lead = {"quem": "Carla", "faixa": 4, "perda_motivo": "data_indisponivel"}
    p = rg._pedido_retomada(lead, None, True, "Cliente: tem 12/12?", 30,
                            perda=("Data indisponível", ""))
    assert "flexível" in p and "sem prometer" in p and "PERDIDO: Data indisponível" in p


def test_o_resumo_entra_no_pedido_e_o_toque_nao_manda_citar():
    resumo = {"quer": "15 anos pra 120 em 12/12", "em_que_pe": ["pediu o valor do sábado"],
              "pode_travar": ["preço"], "proximo_passo": "mandar o valor"}
    lead = {"quem": "Carla", "faixa": 3}
    p = rg._pedido_retomada(lead, None, True, "Cliente: e o sábado?", 9, resumo=resumo)
    assert "Quer: 15 anos pra 120 em 12/12" in p and "PARTE DAQUI" in p
    assert '"nao_chamar": true' in p
    t = rg._pedido_toque(lead, 2, True, "Cliente: e o sábado?", 3, resumo)
    assert "Quer: 15 anos" in t and "PARTE DAQUI" not in t
    assert rg._resumo_linha(resumo) == "15 anos pra 120 em 12/12 · parou: pediu o valor do sábado"


def test_o_nao_respondeu_e_chamado_uma_vez_so(pool, equipe, duble):
    with pool.connection() as c:
        _ligar(c, equipe, aviso_vendedor=False)
        lid, cv = _lead(c, equipe["PEDRO"], dias=10)
        _perdido(c, lid, "nao_respondeu")
    rg.rodar(pool)
    assert len(duble["saiu"]) == 1
    with pool.connection() as c:
        assert c.execute("select origem, uma_vez, perda_motivo from resgate_leads").fetchone() == \
            ("perdido_vendedor", True, "nao_respondeu")
        _atrasar(c, 4)
    rg.rodar(pool)
    assert len(duble["saiu"]) == 1                          # sem o 2º toque
    with pool.connection() as c:
        _atrasar(c, 4)
    rg.rodar(pool)
    with pool.connection() as c:
        assert c.execute("select estado from resgate_leads").fetchone()[0] == "perdido"
    assert len(duble["saiu"]) == 1


def test_a_ia_le_que_acabou_e_nao_chama(pool, equipe, duble, monkeypatch):
    monkeypatch.setattr(rg, "redigir", lambda *a, **k: {"nao_chamar": True,
                                                        "motivo": "ele disse que fechou com outro buffet"})
    with pool.connection() as c:
        _ligar(c, equipe, aviso_vendedor=False)
        lid, _ = _lead(c, equipe["PEDRO"], dias=10)
    rg.rodar(pool)
    assert not any(x["numero"] == "5586999990001" for x in duble["saiu"])   # nada pro cliente
    assert "não chamei" in duble["saiu"][0]["texto"]                         # o supervisor sabe
    with pool.connection() as c:
        assert c.execute("select vendedor_id from prospeccao where id=%s", (lid,)).fetchone()[0] == equipe["PEDRO"]
        assert c.execute("select texto from resgate_envios where tipo='descartado'").fetchone()[0] \
            == "ele disse que fechou com outro buffet"
        assert rg.fila(c, EMPRESA) == []                     # descartado pra sempre


def test_no_ensaio_a_previa_diz_que_nao_chamaria(pool, equipe, duble, monkeypatch):
    monkeypatch.setattr(rg, "redigir", lambda *a, **k: {"nao_chamar": True, "motivo": "desistiu da festa"})
    with pool.connection() as c:
        _cfg(c, equipe)
        lid, _ = _lead(c, equipe["PEDRO"], dias=10)
    rg.rodar(pool)
    with pool.connection() as c:
        txt = c.execute("select texto from resgate_envios where tipo='previa'").fetchone()[0]
        assert "Eu NÃO chamaria: desistiu da festa" in txt
        assert not c.execute("select 1 from resgate_envios where tipo='descartado'").fetchone()


def test_valor_fora_do_orcamento_nao_sai_sozinho(pool, equipe, duble, monkeypatch):
    monkeypatch.setattr(rg, "redigir", lambda *a, **k: {
        "texto": "Sai por R$ 4.999!", "resumo_linha": "",
        "avisos": ["A mensagem cita R$ 4.999, que não está no orçamento."]})
    with pool.connection() as c:
        _ligar(c, equipe, aviso_vendedor=False)
        lid, _ = _lead(c, equipe["PEDRO"], dias=10)
    rg.rodar(pool)
    assert not any(x["numero"] == "5586999990001" for x in duble["saiu"])
    with pool.connection() as c:
        assert c.execute("select vendedor_id from prospeccao where id=%s", (lid,)).fetchone()[0] == equipe["PEDRO"]
        assert c.execute("select count(*) from resgate_envios where tipo='valor_conferir'").fetchone()[0] == 1
    rg.rodar(pool)
    with pool.connection() as c:                            # não tenta de novo no mesmo dia
        assert c.execute("select count(*) from resgate_envios where tipo='valor_conferir'").fetchone()[0] == 1


def test_a_passagem_guarda_a_origem_e_o_resumo(pool, equipe, duble, monkeypatch):
    monkeypatch.setattr(rg, "redigir", lambda *a, **k: {
        "texto": "Oi Carla!", "resumo_linha": "15 anos · parou: pediu o valor", "avisos": []})
    with pool.connection() as c:
        _ligar(c, equipe, aviso_vendedor=False)
        lid, _ = _lead(c, equipe["PEDRO"], dias=9)
    rg.rodar(pool)
    with pool.connection() as c:
        origem, desde, linha = c.execute(
            "select origem, parado_desde, resumo_linha from resgate_leads").fetchone()
        assert origem == "follow_up" and linha == "15 anos · parou: pediu o valor"
        assert (datetime.now(timezone.utc) - desde).days == 9


def test_a_previa_diz_de_onde_veio_e_o_resumo(pool, equipe, duble, monkeypatch):
    monkeypatch.setattr(rg, "redigir", lambda *a, **k: {
        "texto": "Oi Carla!", "resumo_linha": "15 anos · parou: pediu o valor", "avisos": []})
    with pool.connection() as c:
        _cfg(c, equipe)
        lid, _ = _lead(c, equipe["PEDRO"], dias=10)
        _perdido(c, lid, "outro", por="sem_tratativa")
    rg.rodar(pool)
    with pool.connection() as c:
        txt = c.execute("select texto from resgate_envios where tipo='previa'").fetchone()[0]
    assert "veio dos perdidos" in txt and "✨ 15 anos · parou: pediu o valor" in txt


def test_a_previa_nao_dobra_quando_dois_workers_competem(pool, equipe, duble):
    """Migração 445 (achado em produção, 28/09/2026): o lead #1167 da Prime recebeu a
    mesma prévia duas vezes, 0,7s de diferença — dois processos do serviço
    (render.yaml, `--workers 2`) competindo pela mesma prévia. A segunda tentativa
    tem que voltar False sem mandar nada, e só uma linha pode sobrar."""
    with pool.connection() as c:
        _cfg(c, equipe)
        _lead(c, equipe["PEDRO"], dias=10)
        candidato = rg.fila(c, EMPRESA)[0]
        cfg = rg.config(c, EMPRESA)
    ok1 = rg.supervisor(pool, EMPRESA, "primeira prévia", tipo="previa",
                        lead=candidato["id"], ref_em=candidato["desde"], cfg=cfg)
    ok2 = rg.supervisor(pool, EMPRESA, "segunda prévia (não devia sair)", tipo="previa",
                        lead=candidato["id"], ref_em=candidato["desde"], cfg=cfg)
    assert ok1 is True and ok2 is False
    assert len(duble["saiu"]) == 1                       # só a primeira saiu de fato
    assert duble["saiu"][0]["texto"] == "primeira prévia"
    with pool.connection() as c:
        rows = c.execute("select texto from resgate_envios where tipo='previa' and prospeccao_id=%s",
                         (candidato["id"],)).fetchall()
    assert rows == [("primeira prévia",)]


def test_erro_de_verdade_na_previa_nao_e_perder_a_corrida(pool, equipe, duble):
    """O CASO QUE ESTE TESTE FIXA (29/09/2026, produção): entre o deploy da 445 e o da
    446, o índice único não existia de verdade (bug à parte, já corrigido) e toda
    tentativa de prévia caía num erro de BANCO, não em "perdi a corrida pro outro
    processo" — são coisas diferentes, e o `supervisor()` tratava as duas iguais,
    calado. ~90 tentativas falharam em 3h sem pausar nem avisar ninguém. Reproduz o
    estado exato (dropa o índice) e confirma: cai no erro, registra 'erro_previa'
    (não 'previa' — não pode ocupar a vaga do índice único e travar uma tentativa boa
    depois), e devolve False sem mandar nada."""
    with pool.connection() as c:
        _cfg(c, equipe)
        lid, _ = _lead(c, equipe["PEDRO"], dias=10)
        c.execute("drop index resgate_envios_previa_unica")
        c.commit()
        candidato = rg.fila(c, EMPRESA)[0]
        cfg = rg.config(c, EMPRESA)
    ok = rg.supervisor(pool, EMPRESA, "prévia", tipo="previa", lead=candidato["id"],
                       ref_em=candidato["desde"], cfg=cfg)
    assert ok is False
    assert duble["saiu"] == []                            # nada saiu de verdade
    with pool.connection() as c:
        assert not c.execute("select 1 from resgate_envios where tipo='previa' and "
                             "prospeccao_id=%s", (lid,)).fetchone()
        erro = c.execute("select ok, erro from resgate_envios where tipo='erro_previa' "
                         "and prospeccao_id=%s", (lid,)).fetchone()
    assert erro is not None and erro[0] is False
    assert "unique or exclusion" in (erro[1] or "")


def test_3_erros_de_previa_pausam_o_ensaio_e_avisam(pool, equipe, duble, monkeypatch):
    """O freio (`_freio`) só rodava no modo Ligado — o Ensaio não tinha proteção
    nenhuma contra um erro técnico se repetindo sem parar (o achado de 29/09/2026
    acima). Agora conta os `erro_previa` do dia e pausa igual ao Ligado, avisando o
    supervisor — em vez de ficar mudo por horas."""
    monkeypatch.setattr(rg, "ESPACO_MIN", -60)      # uma tentativa por passada, sem esperar
    with pool.connection() as c:
        _cfg(c, equipe)
        _lead(c, equipe["PEDRO"], dias=10)
        c.execute("drop index resgate_envios_previa_unica")
        c.commit()
    for _ in range(3):
        rg.rodar(pool)                                    # acumula as 3 falhas
    rg.rodar(pool)                                        # o freio lê as 3 e pausa (mesma
                                                            # régua do Ligado: só na PRÓXIMA
                                                            # passada, depois de contar)
    with pool.connection() as c:
        cfg = rg.config(c, EMPRESA)
    assert cfg["pausado_em"] and "erro técnico" in cfg["pausado_motivo"]
    assert "pausado" in duble["saiu"][-1]["texto"]        # o freio avisou o supervisor
    with pool.connection() as c:
        antes = c.execute("select count(*) from resgate_envios where tipo='erro_previa'").fetchone()[0]
    rg.rodar(pool)                                        # pausado: não tenta de novo
    with pool.connection() as c:
        depois = c.execute("select count(*) from resgate_envios where tipo='erro_previa'").fetchone()[0]
    assert depois == antes


def test_a_446_apaga_a_duplicata_e_recria_o_indice(pool, equipe):
    """O CASO QUE ESTE TESTE FIXA (28/09/2026, produção): o índice da 445 nunca
    existiu de verdade — um bug em `db/aplicar_migracoes.py` confundiu "dado
    duplicado bloqueando a criação" com "índice já existe" e marcou a 445 como
    concluída em `schema_migrations` mesmo com o `CREATE UNIQUE INDEX`
    falhando. As duas linhas duplicadas da Rozalia (lead #1167) continuavam
    lá. Simula esse exato estado — derruba o índice, insere a duplicata de
    novo — e reaplica o SQL da 446: só a linha mais antiga sobra, e o índice
    volta a existir."""
    with pool.connection() as c:
        c.execute("drop index if exists resgate_envios_previa_unica")
        lid, _ = _lead(c, equipe["PEDRO"], dias=10)
        ref = datetime.now(timezone.utc) - timedelta(days=10)
        c.execute("""insert into resgate_envios (conta_id, prospeccao_id, tipo, ref_em, texto, ok)
                     values (%s,%s,'previa',%s,'primeira',true)""", (EMPRESA, lid, ref))
        c.execute("""insert into resgate_envios (conta_id, prospeccao_id, tipo, ref_em, texto, ok)
                     values (%s,%s,'previa',%s,'segunda',true)""", (EMPRESA, lid, ref))
        c.commit()
        c.execute((BASE / "446_resgate_previa_sem_dobro_de_verdade.sql").read_text(encoding="utf-8"))
        c.commit()
        rows = c.execute("select texto from resgate_envios where tipo='previa' and prospeccao_id=%s",
                         (lid,)).fetchall()
        assert rows == [("primeira",)]                      # a mais antiga (menor id) fica
        assert c.execute("select indexname from pg_indexes where "
                         "indexname='resgate_envios_previa_unica'").fetchone()


def _perdido_da_ia(c, equipe, dias_perdido, numero="5586988880009"):
    lid, cv = _lead_da_ia(c, equipe, dias=dias_perdido + 10, numero=numero, quem="Rui")
    c.execute("update prospeccao set status='perdido', perda_motivo='nao_respondeu' where id=%s", (lid,))
    c.execute("update chip_regra_leads set perdido_em = now() - make_interval(days => %s), toques=2 "
              "where prospeccao_id=%s", (dias_perdido, lid))
    c.commit()
    return lid, cv


def test_a_repescagem_da_ia_volta_uma_vez_depois_de_30_dias(pool, equipe, duble):
    with pool.connection() as c:
        _ligar(c, equipe, aviso_vendedor=True)
        cedo, _ = _perdido_da_ia(c, equipe, 20)
        lid, cv = _perdido_da_ia(c, equipe, 31, numero="5586988880010")
        assert _ids(rg.fila(c, EMPRESA)) == [lid]
        assert _origem(c, lid) == "ia_numero"
    rg.rodar(pool)                                   # sem aviso a vendedor: o dono é a IA
    assert duble["avisos"] == []
    assert [x["numero"] for x in duble["saiu"]] == ["5586988880010"]
    with pool.connection() as c:
        assert c.execute("select origem, uma_vez from resgate_leads where prospeccao_id=%s",
                         (lid,)).fetchone() == ("ia_numero", True)
        # 7 dias sem resposta: fica como perdido do resgate, e nunca mais volta
        _atrasar(c, 8)
    rg.rodar(pool)
    with pool.connection() as c:
        assert c.execute("select estado from resgate_leads where prospeccao_id=%s",
                         (lid,)).fetchone()[0] == "perdido"
        c.execute("update resgate_leads set ativo=false")
        c.commit()
        assert lid not in _ids(rg.fila(c, EMPRESA))


class _Resp:
    def __init__(self, txt):
        self.content = [type("B", (), {"type": "text", "text": txt})()]
        self.usage = None
        self.stop_reason = "end_turn"


def _brain_fake(monkeypatch, txt, pedidos):
    import core.brain as cb

    class _B:
        model = "fake"

        def chamar(self, system, mensagens, **k):
            pedidos.append(mensagens[0]["content"])
            return _Resp(txt)
    monkeypatch.setattr(cb, "Brain", _B)
    # a base da empresa (instruções, catálogo) mora em tabelas que este schema não tem
    monkeypatch.setattr(rg, "_system", lambda *a, **k: "sistema")


def test_redigir_escreve_em_cima_do_resumo(pool, equipe, monkeypatch):
    pedidos = []
    _brain_fake(monkeypatch, '{"mensagem": "Oi Carla! Você perguntou do sábado…"}', pedidos)
    monkeypatch.setattr(rg, "_resumo_antes", lambda pool, conta, lid: {
        "quer": "15 anos pra 120", "em_que_pe": ["pediu o valor do sábado"]})
    monkeypatch.setattr(rg, "_valores_fora", lambda *a, **k: [])
    from finance import ia_uso
    monkeypatch.setattr(ia_uso, "registrar", lambda *a, **k: None)
    with pool.connection() as c:
        _cfg(c, equipe)
        _lead(c, equipe["PEDRO"], dias=10)
        lead = rg.fila(c, EMPRESA)[0]
    r = rg.redigir(pool, EMPRESA, lead, None)
    assert r["texto"].startswith("Oi Carla!") and r["resumo_linha"].startswith("15 anos pra 120")
    assert "Quer: 15 anos pra 120" in pedidos[0]


def test_redigir_devolve_nao_chamar(pool, equipe, monkeypatch):
    _brain_fake(monkeypatch, '{"nao_chamar": true, "motivo": "fechou com outro"}', [])
    monkeypatch.setattr(rg, "_resumo_antes", lambda *a: None)
    from finance import ia_uso
    monkeypatch.setattr(ia_uso, "registrar", lambda *a, **k: None)
    with pool.connection() as c:
        _cfg(c, equipe)
        _lead(c, equipe["PEDRO"], dias=10)
        lead = rg.fila(c, EMPRESA)[0]
    assert rg.redigir(pool, EMPRESA, lead, None) == {"nao_chamar": True, "motivo": "fechou com outro"}


# ══════════════════════════════════════════════ o "Testar comigo" marca a visita
# 27/09/2026, o dono no teste: "não gostei, ele não tá agendando a visita". O teste
# era só conversa ("pergunte o dia e diga que a equipe confirma"); agora usa a mesma
# grade e a mesma agenda da regra — oferece os livres com as letras e confirma o
# escolhido — sem gravar nada.

def _teste_aberto(pool, equipe, monkeypatch, respostas):
    """Um teste aberto, a regra marcando visita (grade e agenda de mentira) e uma IA
    que devolve, em ordem, os JSONs de `respostas`."""
    from finance import calendario
    from finance import ia_visita as iv
    BRT = iv.ag.BRT
    livres = [datetime(2026, 9, 29, 9, tzinfo=BRT), datetime(2026, 9, 30, 10, tzinfo=BRT)]
    # O cenário se passa na segunda 28/09/2026, véspera dos horários livres. Sem isto
    # o corretor de dia da semana usa o dia REAL: a partir de 30/09 o "29/09" já
    # passou, ele supõe 2027 (quarta-feira) e "terça 29/09" vira "quarta-feira 29/09".
    monkeypatch.setattr(calendario, "_hoje", lambda: date(2026, 9, 28))
    monkeypatch.setattr(rg, "_teste_visita", lambda *a, **k: {"grade": {}, "min_h": 3, "max_dias": 14})
    monkeypatch.setattr(iv, "ofertas", lambda *a, **k: list(livres))
    monkeypatch.setattr(iv, "cabe", lambda pool, conta, cfg, ini, agora, **k: (ini in livres, "ocupado"))
    monkeypatch.setattr(rg, "_system", lambda *a, **k: "sistema")
    from finance import ia_uso
    monkeypatch.setattr(ia_uso, "registrar", lambda *a, **k: None)
    import core.brain as cb
    fila = list(respostas)
    pedidos = []

    class _B:
        model = "fake"

        def chamar(self, system, mensagens, **k):
            pedidos.append(mensagens[0]["content"])
            return _Resp(fila.pop(0))
    monkeypatch.setattr(cb, "Brain", _B)
    with pool.connection() as c:
        _cfg(c, equipe)
        lid, _ = _lead(c, equipe["PEDRO"], dias=10)
        c.execute("insert into resgate_teste (conta_id, numero8, prospeccao_id, historico) "
                  "values (%s,%s,%s,'[]'::jsonb)", (EMPRESA, rg.chave_do_numero(SUPERVISOR), lid))
        c.commit()
    return livres, pedidos


def test_o_teste_oferece_os_horarios_livres_com_as_letras(pool, equipe, duble, monkeypatch):
    livres, pedidos = _teste_aberto(pool, equipe, monkeypatch, [
        '{"resposta": "Que tal conhecer o espaço?", "oferecer_horarios": true}'])
    rg.responder_supervisor(pool, EMPRESA, "quero ver o espaço", "M1")
    txt = duble["saiu"][-1]["texto"]
    assert "Que tal conhecer o espaço?" in txt
    assert "A) terça 29/09 às 9h" in txt and "B) quarta 30/09 às 10h" in txt
    assert "oferecer_horarios=true" in pedidos[0]          # a IA sabe que marca


def test_a_letra_escolhida_confirma_sem_gravar_nada(pool, equipe, duble, monkeypatch):
    livres, pedidos = _teste_aberto(pool, equipe, monkeypatch, [
        '{"resposta": "Olha os horários:", "oferecer_horarios": true}'])
    rg.responder_supervisor(pool, EMPRESA, "quero visitar", "M1")
    rg.responder_supervisor(pool, EMPRESA, "B", "M2")
    txt = duble["saiu"][-1]["texto"]
    assert txt.startswith("🧪 Prontinho! Sua visita ficou marcada pra quarta 30/09 às 10h ✅")
    assert "Nada foi gravado" in txt
    assert len(pedidos) == 1                                # a letra não chama a IA
    with pool.connection() as c:
        assert c.execute("select count(*) from eventos_agenda").fetchone()[0] == 0


def test_o_horario_que_o_cliente_diz_e_conferido_na_agenda(pool, equipe, duble, monkeypatch):
    livres, _ = _teste_aberto(pool, equipe, monkeypatch, [
        '{"resposta": "", "visita": {"data": "2026-09-29", "hora": "15:00"}}',
        '{"resposta": "Te espero lá!", "visita": {"data": "2026-09-29", "hora": "09:00"}}'])
    rg.responder_supervisor(pool, EMPRESA, "amanhã às 15h", "M1")
    assert duble["saiu"][-1]["texto"].startswith("🧪 Esse horário não está livre 😕 Tenho estes:\nA) terça 29/09 às 9h")
    rg.responder_supervisor(pool, EMPRESA, "então terça 9h", "M2")
    txt = duble["saiu"][-1]["texto"]
    assert "marcada pra terça 29/09 às 9h ✅" in txt and "Te espero lá!" in txt


# ══════════════════════════════════════════════ o teste no chip certo
# 27/09/2026, o dono: "mandei mensagem pro chip Thiago e o CP Zarb respondeu pro meu
# número". Era o teste do resgate, ainda aberto: pegava a mensagem do supervisor em
# QUALQUER chip e respondia sempre pelo principal. Mockup aprovado:
# docs/mockups/teste_resgate_chip_certo.html.

def test_o_supervisor_so_e_desviado_no_chip_do_teste_e_no_principal(pool, equipe, duble):
    with pool.connection() as c:
        _cfg(c, equipe)
        sup = "55" + SUPERVISOR
        # sem teste: o principal segura (é por onde saem os avisos), o outro chip não
        assert rg.e_do_supervisor(c, EMPRESA, sup, EMPRESA)
        assert not rg.e_do_supervisor(c, EMPRESA, sup, CHIP2)
        assert rg.e_do_supervisor(c, EMPRESA, sup)            # sem chip: o de antes, todos
        # teste aberto no chip 2: lá o supervisor é o "cliente do teste"
        _lead(c, equipe["PEDRO"], dias=10, chip=CHIP2)
    assert rg.testar(pool, EMPRESA)["ok"]
    with pool.connection() as c:
        assert rg.e_do_supervisor(c, EMPRESA, sup, CHIP2)
        assert rg.e_do_supervisor(c, EMPRESA, sup, EMPRESA)
        assert not rg.e_do_supervisor(c, EMPRESA, "5586999990009", CHIP2)


def test_o_teste_fala_pelo_chip_da_conversa_e_com_o_tubo(pool, equipe, duble):
    with pool.connection() as c:
        _cfg(c, equipe)
        c.execute("update canais_config set rotulo='CP Thiago' where conta_id=%s", (CHIP2,))
        _lead(c, equipe["PEDRO"], dias=10, chip=CHIP2)
        c.commit()
    assert rg.testar(pool, EMPRESA)["ok"]
    assert [x["chip"] for x in duble["saiu"]] == [CHIP2, CHIP2]
    assert all(x["texto"].startswith("🧪") for x in duble["saiu"])
    assert "pelo CP Thiago" in duble["saiu"][0]["texto"]
    with pool.connection() as c:
        assert c.execute("select chip_id from resgate_teste").fetchone()[0] == CHIP2


def test_o_teste_do_principal_sai_pelo_principal(pool, equipe, duble):
    with pool.connection() as c:
        _cfg(c, equipe)
        _lead(c, equipe["PEDRO"], dias=10)
    assert rg.testar(pool, EMPRESA)["ok"]
    assert [x["chip"] for x in duble["saiu"]] == [None, None]     # nulo = o da empresa
    with pool.connection() as c:
        assert c.execute("select chip_id from resgate_teste").fetchone()[0] is None


def test_o_teste_que_nao_sai_diz_por_qual_chip(pool, equipe, duble):
    with pool.connection() as c:
        _cfg(c, equipe)
        _lead(c, equipe["PEDRO"], dias=10, chip=CHIP2)
    duble["estado"]["ok"] = False
    r = rg.testar(pool, EMPRESA)
    assert not r["ok"] and "conectado" in r["erro"]


def _com_teste_no_chip2(pool, equipe, monkeypatch):
    monkeypatch.setattr(rg, "_system", lambda *a, **k: "sistema")
    monkeypatch.setattr(rg, "_teste_visita", lambda *a, **k: None)
    from finance import ia_uso
    monkeypatch.setattr(ia_uso, "registrar", lambda *a, **k: None)
    import core.brain as cb

    class _B:
        model = "fake"

        def chamar(self, system, mensagens, **k):
            return _Resp('{"resposta": "Oi! Pra quando é a festa?"}')
    monkeypatch.setattr(cb, "Brain", _B)
    with pool.connection() as c:
        _cfg(c, equipe)
        _lead(c, equipe["PEDRO"], dias=10, chip=CHIP2)
    assert rg.testar(pool, EMPRESA)["ok"]


def test_a_resposta_do_teste_sai_pelo_chip_onde_ele_escreveu(pool, equipe, duble, monkeypatch):
    _com_teste_no_chip2(pool, equipe, monkeypatch)
    rg.responder_supervisor(pool, EMPRESA, "oi, quero saber mais", "M1", CHIP2)
    assert duble["saiu"][-1] == {"numero": duble["saiu"][-1]["numero"], "chip": CHIP2,
                                 "texto": "🧪 Oi! Pra quando é a festa?"}
    with pool.connection() as c:
        hist = c.execute("select historico from resgate_teste").fetchone()[0]
    assert {"quem": "cliente", "texto": "oi, quero saber mais"} in hist


def test_no_principal_com_o_teste_em_outro_chip_ele_ouve_onde_o_teste_esta(pool, equipe, duble,
                                                                           monkeypatch):
    _com_teste_no_chip2(pool, equipe, monkeypatch)
    with pool.connection() as c:
        c.execute("update canais_config set rotulo='CP Thiago' where conta_id=%s", (CHIP2,))
        c.commit()
    n = len(duble["saiu"])
    rg.responder_supervisor(pool, EMPRESA, "ok, vi o aviso", "M1", EMPRESA)
    rg.responder_supervisor(pool, EMPRESA, "valeu", "M2", EMPRESA)      # não repete em 15 min
    assert len(duble["saiu"]) == n + 1
    ult = duble["saiu"][-1]
    assert ult["chip"] is None and "Seu teste está aberto no CP Thiago" in ult["texto"]
    with pool.connection() as c:
        hist = c.execute("select historico from resgate_teste").fetchone()[0]
    assert all(h.get("quem") != "cliente" for h in hist), "a resposta ao aviso não é fala do cliente"


def test_encerrar_o_teste_vence_na_hora_e_avisa_pelo_chip_do_teste(pool, equipe, duble):
    with pool.connection() as c:
        _cfg(c, equipe)
        _lead(c, equipe["PEDRO"], dias=10, chip=CHIP2, quem="Gabriela Souza")
    assert rg.testar(pool, EMPRESA)["ok"]
    with pool.connection() as c:
        tela = rg.teste_da_tela(c, EMPRESA)
    assert tela["quem"] == "Gabriela" and tela["n"] == 1 and tela["expira_txt"]
    assert rg.encerrar_teste(pool, EMPRESA)["ok"]
    fim = duble["saiu"][-1]
    assert fim["chip"] == CHIP2 and fim["texto"].startswith("🧪 Teste encerrado (Gabriela")
    with pool.connection() as c:
        assert rg.teste_aberto(c, EMPRESA) is None
        assert rg.teste_da_tela(c, EMPRESA) is None
        # nada apagado: a linha e o histórico ficam
        assert c.execute("select count(*) from resgate_teste").fetchone()[0] == 1
        assert not rg.e_do_supervisor(c, EMPRESA, "55" + SUPERVISOR, CHIP2)
    assert not rg.encerrar_teste(pool, EMPRESA)["ok"]


def test_o_cartao_leva_o_teste_aberto(pool, equipe, duble):
    with pool.connection() as c:
        _cfg(c, equipe)
        _lead(c, equipe["PEDRO"], dias=10)
        assert rg.tela(c, EMPRESA)["teste"] is None
    assert rg.testar(pool, EMPRESA)["ok"]
    with pool.connection() as c:
        assert rg.tela(c, EMPRESA)["teste"]["lead"]


def test_os_leads_do_numero_do_supervisor(pool, equipe):
    with pool.connection() as c:
        dele, _ = _lead(c, equipe["ZAQ"], numero="5511987654321")
        sem_nove, _ = _lead(c, equipe["ZAQ"], numero="551187654321")
        outro_ddd, _ = _lead(c, equipe["PEDRO"], numero="5586987654321")
        cliente, _ = _lead(c, equipe["PEDRO"], numero="5586999990001")
        assert rg.leads_do_supervisor(c, EMPRESA) == set()           # resgate desligado
        assert rg.sql_fora_do_supervisor(c, EMPRESA) == ("", [])
        _cfg(c, equipe)
        assert rg.leads_do_supervisor(c, EMPRESA) == {dele, sem_nove}
        fora, v = rg.sql_fora_do_supervisor(c, EMPRESA)
        ids = {r[0] for r in c.execute(
            f"select p.id from prospeccao p where p.conta_id=%s{fora}", [EMPRESA, *v]).fetchall()}
        assert ids == {outro_ddd, cliente}
        assert rg.leads_do_supervisor(c, OUTRA) == set()


# ══════════════════════════════════════════════ a regra do chip adota o supervisor

def test_a_regra_adota_a_conversa_antiga_do_supervisor(pool, equipe):
    """O número do dono já era lead desde os testes de agosto (#760, do ZAQ SDR, no
    chip Thiago). A regra só atende contato novo: sem a adoção, a IA ficava calada."""
    with pool.connection() as c:
        _cfg(c, equipe)
        c.execute("update chip_regra set vale_desde = now() - interval '30 days'")
        lid, cv = _lead(c, equipe["ZAQ"], chip=CHIP2, numero="55" + SUPERVISOR)
        assert cr.regra_da_conversa(c, EMPRESA, cv) is None
        assert cr.adotar_do_supervisor(c, EMPRESA, lid, cv, CHIP2)
        c.commit()
        assert c.execute("select agente_ativo, responsavel_membro_id from conversas where id=%s",
                         (cv,)).fetchone() == (True, equipe["ZAQ"])
        r = cr.regra_da_conversa(c, EMPRESA, cv)
        assert r and r["membro_id"] == equipe["ZAQ"]
        # a regra olha a conversa a partir da adoção: o que a equipe falou antes não pausa
        assert r["vale_desde"] > datetime.now(timezone.utc) - timedelta(minutes=5)
        assert not cr.adotar_do_supervisor(c, EMPRESA, lid, cv, CHIP2)     # uma vez só
        # o lead que a regra deu no primeiro "oi" segue o relógio da regra
        outro, cv2 = _lead(c, None, chip=CHIP2, numero="5586999990077")
        cr.atribuir(c, EMPRESA, outro, cv2, cr.regra(c, EMPRESA, CHIP2))
        assert cr.regra_da_conversa(c, EMPRESA, cv2)["vale_desde"] < \
            datetime.now(timezone.utc) - timedelta(days=29)


def test_a_regra_nao_tira_o_lead_de_vendedor(pool, equipe):
    with pool.connection() as c:
        lid, cv = _lead(c, equipe["PEDRO"], chip=CHIP2, numero="55" + SUPERVISOR)
        assert not cr.adotar_do_supervisor(c, EMPRESA, lid, cv, CHIP2)
        assert c.execute("select vendedor_id from prospeccao where id=%s", (lid,)).fetchone()[0] \
            == equipe["PEDRO"]
        # e sem regra com IA no chip, nada
        lid2, cv2 = _lead(c, None, numero="55" + SUPERVISOR)
        assert not cr.adotar_do_supervisor(c, EMPRESA, lid2, cv2, None)


def test_quem_esta_em_consulta_so_fica_fora_da_cobranca_na_clinica(pool):
    """`sql_nao_cobra`: o paciente em Consulta espera a clínica (o plano), e nenhum
    motor o chama nem o fecha como "não respondeu". Em OUTRO nicho, a etapa "Consulta"
    criada à mão tem a mesma chave e continua sendo cobrada como sempre."""
    from finance import funil_regua as fr
    with pool.connection() as c:
        n = c.execute("insert into nichos (nome, slug) values ('Clínica','clinica') returning id").fetchone()[0]
        c.execute("insert into contas (id, nome, chip_de, nicho_id) values (39,'Clínica',null,%s)", (n,))
        ids = {}
        for conta, status in ((39, "consulta"), (39, "contatado"), (EMPRESA, "consulta"), (EMPRESA, "lista_espera")):
            ids[(conta, status)] = c.execute(
                "insert into prospeccao (conta_id, empresa, status) values (%s,'X',%s) returning id",
                (conta, status)).fetchone()[0]
        cobra = {r[0] for r in c.execute(
            f"select p.id from prospeccao p where p.id = any(%s) and {fr.sql_nao_cobra('p')}",
            (list(ids.values()),)).fetchall()}
        c.rollback()
    assert cobra == {ids[(39, "contatado")], ids[(EMPRESA, "consulta")]}
