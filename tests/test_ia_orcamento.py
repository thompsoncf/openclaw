"""O orçamento que a IA da regra por número monta (migração 392, finance/ia_orcamento.py).

O que cada bloco segura:

* O PLANO tem o sinal (a frase que o sistema inteiro reconhece) e o saldo antes da festa.
* A IA MONTA mas NÃO MANDA: o orçamento vai pra conferência, quem confere é avisado,
  e o cliente ouve o valor aproximado só quando os itens estão liberados pra IA.
* CONFERIR E MANDAR sai uma vez, pelo chip da conversa, e fica registrado como envio;
  só quem a regra escolheu (ou a gerência) confere.
* A APROVAÇÃO segura a data por 72h (e não pela regra de 60 dias do vendedor), sob a
  trava do dia; dia com festa não é segurado — a equipe decide.
* O RELÓGIO: a mensagem do sinal, os lembretes, a data liberada, a data confirmada.
* O COMPROVANTE chama o dono uma vez.
"""
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from psycopg_pool import ConnectionPool

from finance import agenda as ag
from finance import agente
from finance import chip_regra as cr
from finance import ia_orcamento as iao
from finance import vendas

BASE = Path(__file__).resolve().parents[1] / "db" / "migracoes"
BRT = ag.BRT
EMPRESA, CHIP2 = 34, 36
NUM = "558699990001"

_SQL = """
create table nichos (id bigserial primary key, slug text, nome text);
create table contas (id bigint primary key, nome text, chip_de bigint, nicho_id bigint,
  nome_fantasia text, razao_social text, endereco text, bairro text, cidade text, uf text,
  pix_chave text, pix_tipo text, pix_recebedor text, pix_cidade text);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text, ativo boolean default true, whatsapp text);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, contato text, whatsapp text, telefone text, status text default 'novo',
  segmento text, cidade text, uf text, temperatura text,
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text default 'whatsapp', contato_ref text, contato_nome text,
  status text default 'aberta', agente_ativo boolean default true,
  responsavel_membro_id bigint, ultima_msg_em timestamptz default now(), chip_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, texto text, membro_id bigint, provider_sid text,
  status text, criado_em timestamptz default now());
create unique index idx_mensagens_sid_conversa
  on mensagens (conversa_id, provider_sid) where provider_sid is not null;
create table orcamentos (id bigserial primary key, conta_id bigint, cliente text, empresa text,
  modulos jsonb, itens jsonb, escopo text, evento jsonb, setup_centavos bigint,
  mensal_centavos bigint, primeiro_ano_centavos bigint, whatsapp text, n_modulos int,
  criado_por text, token text, status text, modo text, numero int, parcelas jsonb,
  aprovada_em timestamptz, sinal_pago_em timestamptz, evento_agenda_id bigint,
  sinal_centavos bigint, contato text, criado_em timestamptz default now());
create table eventos_agenda (id bigserial primary key, conta_id bigint, membro_id bigint,
  titulo text, inicio timestamptz, fim timestamptz, local text, descricao text,
  status text default 'ativo', tipo text default 'pessoal', lembrete_min int,
  link_online text, prospeccao_id bigint, cliente_id bigint, ics_token text,
  pre_reserva_ate timestamptz, sinal_centavos bigint, tipo_evento text, convidados int,
  hora_sugerida boolean default false, ocupa_espaco boolean, desfecho text, marcado_por text,
  criado_em timestamptz default now());
create table agente_config (conta_id bigint primary key, ativo boolean default false,
  limiar_confianca int default 80, horario text default 'comercial', tom text default 'informal',
  max_trocas int default 4, escalar_para text, pode_responder boolean default true,
  pode_qualificar boolean default false, pode_agendar boolean default false,
  pode_orcamento boolean default true, orcamento_proativo boolean default false,
  agendar_modo text default 'off');
create table agente_conhecimento (id bigserial primary key, conta_id bigint, tipo text,
  pergunta text, resposta text, ordem int default 0);
create table servicos_catalogo (id bigserial primary key, conta_id bigint, slug text,
  nome text, descricao text default '', setup_centavos bigint default 0,
  mensal_centavos bigint default 0, custo_centavos bigint default 0, ativo boolean default true,
  ordem int default 0, categoria text, foto_url text, icone text, duracao_min int,
  agente_diz_preco boolean not null default false);
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_ia_orcamento_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=2, max_size=6, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.execute("insert into nichos (slug, nome) values ('eventos','Eventos')")
        c.execute("""insert into contas (id, nome, chip_de, nicho_id, nome_fantasia, cidade)
                     values (%s,'Prime',null,1,'Prime Eventos','Teresina'),
                            (%s,'CP Thiago',%s,null,null,null)""", (EMPRESA, CHIP2, EMPRESA))
        for m in ("388_regra_por_chip.sql", "390_ia_marca_visita.sql", "392_ia_orcamento.sql"):
            c.execute((BASE / m).read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def prime(pool, monkeypatch):
    avisos = []
    monkeypatch.setattr(cr, "notificar", lambda pool_, conta, mid, t, corpo, url:
                        avisos.append((mid, t, url)) or True)
    with pool.connection() as c:
        ids = {n: c.execute("insert into membros (conta_id, nome, email, papel) values (%s,%s,%s,%s) "
                            "returning id", (EMPRESA, n.title(), f"{n.lower()}@x.com", p)).fetchone()[0]
               for n, p in (("ZAQ", "vendedor"), ("JACQUELINE", "vendedor"),
                            ("MANOEL", "dono"), ("PEDRO", "vendedor"))}
        assert cr.salvar(c, EMPRESA, CHIP2, {"ativa": True, "membro_id": ids["ZAQ"], "ia_ligada": True,
                                             "aviso_agenda_membro_id": ids["JACQUELINE"],
                                             "aviso_dono_membro_id": ids["MANOEL"]})["ok"]
        assert iao.salvar(c, EMPRESA, CHIP2, {"orc_ia": True}) is None
        c.execute("update chip_regra set vale_desde = now() - interval '1 day'")
        c.commit()
        regra = cr.regra(c, EMPRESA, CHIP2)
        ocfg = iao.config(c, regra)
    return {"ids": ids, "regra": regra, "ocfg": ocfg, "avisos": avisos}


def _lead(pool, prime, nome="Larissa"):
    with pool.connection() as c:
        lead = c.execute("insert into prospeccao (conta_id, vendedor_id, contato, whatsapp) "
                         "values (%s,%s,%s,%s) returning id",
                         (EMPRESA, prime["ids"]["ZAQ"], nome, "+" + NUM)).fetchone()[0]
        c.execute("insert into chip_regra_leads (prospeccao_id, conta_id, chip_id, membro_id) "
                  "values (%s,%s,%s,%s)", (lead, EMPRESA, CHIP2, prime["ids"]["ZAQ"]))
        conv = c.execute("insert into conversas (conta_id, prospeccao_id, contato_ref, chip_id) "
                         "values (%s,%s,%s,%s) returning id", (EMPRESA, lead, NUM, CHIP2)).fetchone()[0]
        c.commit()
    return lead, conv


def _orcamento(pool, conv, lead, *, total=720000, aprovada=None, sinal_pago=None, festa="2027-03-13",
               estado="conferir"):
    plano = iao.parcelas(total, {"data": festa}, {"sinal_pct": 30}, hoje=date(2026, 9, 26))
    with pool.connection() as c:
        oid = c.execute(
            """insert into orcamentos (conta_id, empresa, itens, evento, setup_centavos, criado_por,
                                       token, status, modo, numero, parcelas, aprovada_em, sinal_pago_em)
               values (%s,'Larissa',%s::jsonb,%s::jsonb,%s,'agente','tok','rascunho','evento',1,
                       %s::jsonb,%s,%s) returning id""",
            (EMPRESA, json.dumps([{"nome": "PACOTE PRIME", "qtd": 1, "setup": total / 100}]),
             json.dumps({"data": festa, "inicio": "20:00", "convidados": 90, "tipo": "15 anos"}),
             total, json.dumps(plano), aprovada, sinal_pago)).fetchone()[0]
        c.execute("insert into ia_orcamentos (orcamento_id, conta_id, prospeccao_id, conversa_id, estado) "
                  "values (%s,%s,%s,%s,%s)", (oid, EMPRESA, lead, conv, estado))
        c.commit()
    return oid


# ══════════════════════════════════════════════ o plano

def test_o_plano_tem_o_sinal_que_o_sistema_reconhece_e_o_saldo_antes_da_festa():
    p = iao.parcelas(720000, {"data": "2027-03-13"}, {"sinal_pct": 30}, hoje=date(2026, 9, 26))
    assert p[0]["obs"] == vendas.OBS_SINAL and p[0]["valor_centavos"] == 216000
    assert vendas.valor_do_sinal(p) == 216000
    assert p[1]["valor_centavos"] == 504000 and p[1]["venc"] == "2027-02-11"
    # festa daqui a 10 dias: o saldo vence junto com o sinal, nunca antes nem no passado
    q = iao.parcelas(100000, {"data": "2026-10-06"}, {"sinal_pct": 30}, hoje=date(2026, 9, 26))
    assert q[1]["venc"] == "2026-09-29"


def test_sem_a_chave_nao_ha_orcamento_pela_ia(pool, prime):
    with pool.connection() as c:
        c.execute("update chip_regra set orc_ia=false")
        assert iao.config(c, prime["regra"]) is None
        assert iao.config_tela(c, prime["regra"]["id"])["ligado"] is False


# ══════════════════════════════════════════════ a IA monta, não manda

@pytest.fixture()
def agente_ia(pool, prime, monkeypatch):
    from core import brain as _brain
    from finance import evento_lead as _evl
    from finance import proposta_lead as _pl
    with pool.connection() as c:
        c.execute("insert into agente_config (conta_id) values (%s)", (EMPRESA,))
        c.execute("""insert into servicos_catalogo (conta_id, slug, nome, setup_centavos, agente_diz_preco)
                     values (%s,'pacote','PACOTE PRIME - SEXTA A DOMINGO - 2027',780000,true),
                            (%s,'kids','ÁREA KIDS',20000,true)""", (EMPRESA, EMPRESA))
        c.commit()
    estado = {"json": {}, "prompts": [], "enviados": []}

    class _Brain:
        def chamar(self, system, mensagens):
            estado["prompts"].append(mensagens[0]["content"])
            return SimpleNamespace(content=[SimpleNamespace(type="text",
                                                            text=json.dumps(estado["json"]))])
    monkeypatch.setattr(_brain, "Brain", _Brain)
    monkeypatch.setattr(agente, "_RAJADA_S", 0)
    monkeypatch.setattr(agente, "_nota_gemeo", lambda *a, **k: "")
    monkeypatch.setattr(_evl, "gravar", lambda *a, **k: None)
    monkeypatch.setattr(_pl, "ligar", lambda *a, **k: None)
    monkeypatch.setattr(agente, "_mandar", lambda c, conta, canal, dest, texto, conv=None:
                        estado["enviados"].append((conv, texto)) or {"ok": True,
                                                                    "sid": f"WA{len(estado['enviados'])}"})
    return estado


def _fala(pool, conv, texto):
    with pool.connection() as c:
        c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, criado_em)
                     values (%s,'whatsapp','in','lead',%s, now() - interval '30 seconds')""",
                  (conv, texto))
        c.commit()


_PEDIDO = {"acao": "orcamento", "resposta": "Perfeito!",
           "servicos": [{"slug": "pacote", "qtd": 1}, {"slug": "kids", "qtd": 1}],
           "evento": {"data": "2027-03-13", "inicio": "20:00", "convidados": 90, "tipo": "15 anos"}}


def test_a_ia_monta_e_manda_pra_conferencia_sem_link(pool, prime, agente_ia):
    lead, conv = _lead(pool, prime)
    agente_ia["json"] = _PEDIDO
    _fala(pool, conv, "quero o orçamento prévio")
    agente.atender(pool, EMPRESA, conv)
    pedir = agente_ia["prompts"][0]
    assert "|orcamento" in pedir and "ORÇAMENTO: pergunte se o cliente prefere" in pedir
    txt = agente_ia["enviados"][0][1]
    assert "/proposta/" not in txt and "Fica em torno de R$ 8.000" in txt and "Jacqueline confere" in txt
    with pool.connection() as c:
        o = c.execute("select id, parcelas, escopo from orcamentos").fetchone()
        est = c.execute("select estado from ia_orcamentos where orcamento_id=%s", (o[0],)).fetchone()[0]
    assert est == "conferir" and vendas.valor_do_sinal(o[1]) == 240000
    assert "Validade: 7 dias" in o[2] and "72 horas" in o[2]
    assert prime["avisos"][0][0] == prime["ids"]["JACQUELINE"]
    assert prime["avisos"][0][2] == f"/cockpit/ia-orcamento/{o[0]}"


def test_preco_nao_liberado_nao_vira_valor_na_mensagem(pool, prime, agente_ia):
    with pool.connection() as c:
        c.execute("update servicos_catalogo set agente_diz_preco=false where slug='kids'")
        c.commit()
    lead, conv = _lead(pool, prime)
    agente_ia["json"] = _PEDIDO
    _fala(pool, conv, "quero o orçamento")
    agente.atender(pool, EMPRESA, conv)
    assert "R$" not in agente_ia["enviados"][0][1]


def test_sem_data_a_ia_pergunta_antes_de_montar(pool, prime, agente_ia):
    lead, conv = _lead(pool, prime)
    agente_ia["json"] = dict(_PEDIDO, evento={"data": "2027-03-13"})
    _fala(pool, conv, "quero o orçamento")
    agente.atender(pool, EMPRESA, conv)
    assert "o horário de início e quantos convidados" in agente_ia["enviados"][0][1]
    with pool.connection() as c:
        assert c.execute("select count(*) from orcamentos").fetchone()[0] == 0


# ══════════════════════════════════════════════ conferir e mandar

def test_conferir_e_mandar_sai_uma_vez_pelo_chip_da_conversa(pool, prime, monkeypatch):
    from finance import proposta_email as _pe
    saiu, registros = [], []
    monkeypatch.setattr(agente, "_mandar", lambda c, conta, canal, dest, texto, conv=None:
                        saiu.append((conv, texto)) or {"ok": True, "sid": "WA1"})
    monkeypatch.setattr(_pe, "registrar", lambda *a, **k: registros.append(k.get("por")))
    lead, conv = _lead(pool, prime)
    oid = _orcamento(pool, conv, lead)
    j = prime["ids"]["JACQUELINE"]
    assert iao.mandar(pool, EMPRESA, j, oid)["ok"]
    assert saiu[0][0] == conv and "/proposta/tok" in saiu[0][1] and "R$ 2.160" in saiu[0][1]
    assert registros == ["agente"]
    assert iao.mandar(pool, EMPRESA, j, oid) == {"ok": False, "erro": "Este orçamento já foi conferido."}
    assert len(saiu) == 1


def test_envio_que_falha_volta_pra_fila(pool, prime, monkeypatch):
    monkeypatch.setattr(agente, "_mandar", lambda *a, **k: {"ok": False})
    lead, conv = _lead(pool, prime)
    oid = _orcamento(pool, conv, lead)
    assert not iao.mandar(pool, EMPRESA, prime["ids"]["JACQUELINE"], oid)["ok"]
    assert iao.pendente(pool, EMPRESA, oid)["estado"] == "conferir"


def test_so_quem_a_regra_escolheu_ou_a_gerencia_confere(pool, prime):
    lead, conv = _lead(pool, prime)
    oid = _orcamento(pool, conv, lead)
    ids = prime["ids"]
    assert iao.pode_conferir(pool, EMPRESA, ids["JACQUELINE"], "vendedor", oid)
    assert iao.pode_conferir(pool, EMPRESA, None, "gestor", oid)
    assert not iao.pode_conferir(pool, EMPRESA, ids["PEDRO"], "vendedor", oid)
    assert iao.descartar(pool, EMPRESA, ids["JACQUELINE"], oid)["ok"]
    assert iao.pendente(pool, EMPRESA, oid)["estado"] == "descartado"


# ══════════════════════════════════════════════ a reserva de 72h

@pytest.fixture()
def reserva(pool, monkeypatch):
    from web import proposta as wp
    monkeypatch.setattr(wp, "_card_e_cliente", lambda *a, **k: (None, None))
    monkeypatch.setattr(wp, "_vendedor_do_orcamento", lambda *a, **k: None)
    monkeypatch.setattr(wp, "_avisar_conflito", lambda *a, **k: None)
    monkeypatch.setattr(ag, "get_config", lambda *a, **k: {"pre_reserva_dias": 5})
    return wp


def _d(pool, oid):
    with pool.connection() as c:
        r = c.execute("select id, conta_id, modo, evento, parcelas, empresa, evento_agenda_id, "
                      "sinal_pago_em from orcamentos where id=%s", (oid,)).fetchone()
    return dict(zip(("id", "conta_id", "modo", "evento", "parcelas", "empresa",
                     "evento_agenda_id", "sinal_pago_em"), r))


def test_orcamento_da_ia_segura_72h_e_o_do_vendedor_nao(pool, prime, reserva):
    lead, conv = _lead(pool, prime)
    oid = _orcamento(pool, conv, lead, estado="enviado")
    ev_id = reserva._reservar_na_agenda(_d(pool, oid), pool)
    with pool.connection() as c:
        ate, st = c.execute("select pre_reserva_ate, status from eventos_agenda where id=%s",
                            (ev_id,)).fetchone()
    assert st == "pre_reservado"
    assert abs((ate - datetime.now(timezone.utc)) - timedelta(hours=72)) < timedelta(minutes=2)
    # o do vendedor (sem linha em ia_orcamentos) segue a regra da festa, bem mais longa
    with pool.connection() as c:
        c.execute("delete from ia_orcamentos")
        c.execute("update orcamentos set evento_agenda_id=null, evento=%s::jsonb",
                  (json.dumps({"data": "2027-04-10", "inicio": "20:00"}),))
        c.commit()
    ev2 = reserva._reservar_na_agenda(_d(pool, oid), pool)
    with pool.connection() as c:
        ate2 = c.execute("select pre_reserva_ate from eventos_agenda where id=%s", (ev2,)).fetchone()[0]
    assert ate2 - datetime.now(timezone.utc) > timedelta(days=30)


def test_dia_com_festa_nao_e_segurado_e_a_equipe_decide(pool, prime, reserva):
    with pool.connection() as c:
        c.execute("""insert into eventos_agenda (conta_id, titulo, inicio, status, tipo_evento)
                     values (%s,'Casamento',%s,'ativo','Casamento')""",
                  (EMPRESA, datetime(2027, 3, 13, 12, tzinfo=BRT)))
        c.commit()
    lead, conv = _lead(pool, prime)
    oid = _orcamento(pool, conv, lead, estado="enviado")
    assert reserva._reservar_na_agenda(_d(pool, oid), pool) is None
    with pool.connection() as c:
        assert c.execute("select bloqueio from ia_orcamentos").fetchone()[0] == "dia_com_festa"
        assert c.execute("select count(*) from eventos_agenda").fetchone()[0] == 1
    assert {a[0] for a in prime["avisos"]} == {prime["ids"]["JACQUELINE"], prime["ids"]["MANOEL"]}


# ══════════════════════════════════════════════ o relógio do sinal

@pytest.fixture()
def envios(monkeypatch):
    saiu = []
    monkeypatch.setattr(agente, "_mandar", lambda c, conta, canal, dest, texto, conv=None:
                        saiu.append(texto) or {"ok": True, "sid": f"WA{len(saiu)}"})
    return saiu


def _aprovado(pool, prime, *, ha, pre_ate=None, status="pre_reservado", sinal_pago=None):
    lead, conv = _lead(pool, prime)
    agora = datetime.now(timezone.utc)
    oid = _orcamento(pool, conv, lead, aprovada=agora - ha, sinal_pago=sinal_pago)
    with pool.connection() as c:
        ev = c.execute("""insert into eventos_agenda (conta_id, titulo, inicio, status, pre_reserva_ate,
                                                      tipo_evento)
                          values (%s,'15 anos',%s,%s,%s,'15 anos') returning id""",
                       (EMPRESA, datetime(2027, 3, 13, 20, tzinfo=BRT), status,
                        pre_ate or agora - ha + timedelta(hours=72))).fetchone()[0]
        c.execute("update orcamentos set evento_agenda_id=%s where id=%s", (ev, oid))
        c.execute("update ia_orcamentos set estado='enviado'")
        c.commit()
    return oid, conv


def test_aprovou_sem_pix_o_dono_manda_os_dados(pool, prime, envios):
    _aprovado(pool, prime, ha=timedelta(minutes=12))
    assert iao.rodar(pool)["aprovados"] == 1
    assert "sinal é de R$ 2.160" in envios[0] and "O Manoel te passa" in envios[0]
    assert iao.rodar(pool)["aprovados"] == 0


def test_aprovou_com_pix_vai_o_copia_e_cola(pool, prime, envios):
    with pool.connection() as c:
        c.execute("update contas set pix_chave='+5586999990000', pix_tipo='telefone' where id=%s",
                  (EMPRESA,))
        c.commit()
    _aprovado(pool, prime, ha=timedelta(minutes=12))
    iao.rodar(pool)
    assert "Pix copia e cola:\n000201" in envios[0]


def test_lembretes_de_24_e_48_horas(pool, prime, envios):
    oid, conv = _aprovado(pool, prime, ha=timedelta(hours=25))
    with pool.connection() as c:
        c.execute("update ia_orcamentos set aprovado_msg_em=now() - interval '25 hours'")
        c.commit()
    assert iao.rodar(pool)["lembretes"] == 1 and "lembrar do sinal" in envios[-1]
    with pool.connection() as c:
        c.execute("update orcamentos set aprovada_em=now() - interval '49 hours'")
        c.commit()
    assert iao.rodar(pool)["lembretes"] == 1 and "Último lembrete" in envios[-1]
    assert iao.rodar(pool)["lembretes"] == 0


def test_data_liberada_e_data_confirmada(pool, prime, envios):
    oid, _ = _aprovado(pool, prime, ha=timedelta(hours=73), status="cancelado")
    with pool.connection() as c:
        c.execute("update ia_orcamentos set aprovado_msg_em=now() - interval '73 hours'")
        c.commit()
    assert iao.rodar(pool)["liberadas"] == 1 and "foi liberada" in envios[-1]
    oid2, _ = _aprovado(pool, prime, ha=timedelta(hours=5), status="ativo",
                        sinal_pago=datetime.now(timezone.utc))
    with pool.connection() as c:
        c.execute("update ia_orcamentos set aprovado_msg_em=now() where orcamento_id=%s", (oid2,))
        c.commit()
    assert iao.rodar(pool)["confirmadas"] == 1 and "data está garantida" in envios[-1]


def test_conversa_que_gente_assumiu_o_relogio_nao_fala(pool, prime, envios):
    oid, conv = _aprovado(pool, prime, ha=timedelta(minutes=12))
    with pool.connection() as c:
        c.execute("update conversas set status='pendente', agente_ativo=false")
        c.commit()
    assert iao.rodar(pool)["aprovados"] == 0 and envios == []


# ══════════════════════════════════════════════ o comprovante

def test_foto_depois_da_aprovacao_e_o_comprovante_uma_vez(pool, prime):
    oid, conv = _aprovado(pool, prime, ha=timedelta(hours=3))
    with pool.connection() as c:
        lead = c.execute("select prospeccao_id from conversas where id=%s", (conv,)).fetchone()[0]
        # antes da mensagem do sinal sair, foto não é comprovante
        assert iao.comprovante(pool, c, EMPRESA, lead, "📷 Foto") is None
        c.execute("update ia_orcamentos set aprovado_msg_em=now()")
        c.commit()
        assert iao.comprovante(pool, c, EMPRESA, lead, "oi") is None
        t = iao.comprovante(pool, c, EMPRESA, lead, "📷 Foto")
        assert t and "comprovante" in t
        # a segunda foto é foto (referência de decoração, print): a IA responde
        assert iao.comprovante(pool, c, EMPRESA, lead, "📄 Documento") is None
    manoel = [a for a in prime["avisos"] if a[0] == prime["ids"]["MANOEL"]]
    assert len(manoel) == 1 and manoel[0][2] == f"/cockpit/orcamentos/{oid}"


def test_foto_com_a_data_ja_liberada_nao_e_comprovante(pool, prime):
    oid, conv = _aprovado(pool, prime, ha=timedelta(hours=80), status="cancelado")
    with pool.connection() as c:
        c.execute("update ia_orcamentos set aprovado_msg_em=now()")
        c.commit()
        lead = c.execute("select prospeccao_id from conversas where id=%s", (conv,)).fetchone()[0]
        assert iao.comprovante(pool, c, EMPRESA, lead, "📷 Foto") is None


def test_foto_com_sinal_ja_pago_nao_e_comprovante(pool, prime):
    oid, conv = _aprovado(pool, prime, ha=timedelta(hours=3), status="ativo",
                          sinal_pago=datetime.now(timezone.utc))
    with pool.connection() as c:
        lead = c.execute("select prospeccao_id from conversas where id=%s", (conv,)).fetchone()[0]
        assert iao.comprovante(pool, c, EMPRESA, lead, "📷 Foto") is None



def test_o_cartao_da_conferencia_no_app(pool, prime, monkeypatch):
    """A página renderiza, e o JavaScript dela é válido (o node confere)."""
    import shutil
    import subprocess
    from web import painel_cockpit as pc
    lead, conv = _lead(pool, prime)
    oid = _orcamento(pool, conv, lead)
    monkeypatch.setattr(pc, "get_pool", lambda: pool)
    monkeypatch.setattr(pc, "_sessao_e_papel", lambda req: (EMPRESA, prime["ids"]["JACQUELINE"], "vendedor"))
    html = pc.cockpit_ia_orcamento(SimpleNamespace(session={}), oid).body.decode()
    assert "Conferir e mandar" in html and "PACOTE PRIME" in html and "R$ 2.160" in html
    js = html.rsplit("<script>", 1)[1].split("</script>", 1)[0]
    if shutil.which("node"):
        r = subprocess.run(["node", "--check", "-"], input=js, capture_output=True, text=True)
        assert r.returncode == 0, r.stderr
    # outro vendedor não vê
    monkeypatch.setattr(pc, "_sessao_e_papel", lambda req: (EMPRESA, prime["ids"]["PEDRO"], "vendedor"))
    assert pc.cockpit_ia_orcamento(SimpleNamespace(session={}), oid).status_code == 303



# ══════════════════════════════════════════════ o que a revisão de 26/09 achou

def test_sinal_zero_por_cento_e_zero():
    p = iao.parcelas(100000, {"data": "2027-03-13"}, {"sinal_pct": 0}, hoje=date(2026, 9, 26))
    assert vendas.valor_do_sinal(p) == 0 and p == [
        {"obs": "Saldo", "venc": "2027-02-11", "forma": "Pix", "valor_centavos": 100000}]
    assert "sinal" not in iao.condicoes("Base.", {"sinal_pct": 0, "validade_dias": 7})


def test_festa_perto_o_saldo_nunca_vence_antes_do_sinal():
    p = iao.parcelas(100000, {"data": "2026-10-10"}, {"sinal_pct": 30}, hoje=date(2026, 9, 26))
    assert p[0]["venc"] == "2026-09-29" and p[1]["venc"] == "2026-09-29"


def test_valor_com_centavos_e_o_que_o_pix_cobra():
    assert iao._reais(234567) == "R$ 2.345,67" and iao._reais(234500) == "R$ 2.345"


def test_o_dono_cancelou_antes_nao_e_prazo_acabou(pool, prime, envios):
    _aprovado(pool, prime, ha=timedelta(hours=5), status="cancelado",
              pre_ate=datetime.now(timezone.utc) + timedelta(hours=60))
    with pool.connection() as c:
        c.execute("update ia_orcamentos set aprovado_msg_em=now()")
        c.commit()
    assert iao.rodar(pool)["liberadas"] == 0 and envios == []


def test_sem_reserva_nao_promete_data_segurada(pool, prime, envios):
    lead, conv = _lead(pool, prime)
    _orcamento(pool, conv, lead, aprovada=datetime.now(timezone.utc) - timedelta(minutes=15),
               estado="enviado")
    assert iao.rodar(pool)["aprovados"] == 1
    assert envios[0].startswith("Recebi a sua aprovação") and "segurada" not in envios[0]


def test_empresa_sem_dono_escolhido_nao_cita_nome(pool, prime, envios):
    with pool.connection() as c:
        c.execute("update chip_regra set aviso_dono_membro_id=null")
        c.commit()
    _aprovado(pool, prime, ha=timedelta(minutes=12))
    iao.rodar(pool)
    assert "A equipe te passa os dados" in envios[0] and "Manoel" not in envios[0]


def test_editado_e_mandado_pela_tela_de_sempre_e_orcamento_de_vendedor(pool, prime, reserva):
    lead, conv = _lead(pool, prime)
    oid = _orcamento(pool, conv, lead)            # ficou em 'conferir'
    ev = reserva._reservar_na_agenda(_d(pool, oid), pool)
    with pool.connection() as c:
        ate = c.execute("select pre_reserva_ate from eventos_agenda where id=%s", (ev,)).fetchone()[0]
    assert ate - datetime.now(timezone.utc) > timedelta(days=30)


def test_marcar_a_data_a_mao_nao_aplica_a_regra_da_ia(pool, prime, reserva):
    with pool.connection() as c:
        c.execute("""insert into eventos_agenda (conta_id, titulo, inicio, status, tipo_evento)
                     values (%s,'Casamento',%s,'ativo','Casamento')""",
                  (EMPRESA, datetime(2027, 3, 13, 12, tzinfo=BRT)))
        c.commit()
    lead, conv = _lead(pool, prime)
    oid = _orcamento(pool, conv, lead, estado="enviado")
    assert reserva._reservar_na_agenda(_d(pool, oid), pool, aprovacao=False)


def test_mandar_sem_a_conversa_volta_pra_fila(pool, prime):
    lead, conv = _lead(pool, prime)
    oid = _orcamento(pool, conv, lead)
    with pool.connection() as c:
        c.execute("update ia_orcamentos set conversa_id=null")
        c.commit()
    assert not iao.mandar(pool, EMPRESA, prime["ids"]["JACQUELINE"], oid)["ok"]
    assert iao.pendente(pool, EMPRESA, oid)["estado"] == "conferir"


def test_um_orcamento_esperando_conferencia_por_vez(pool, prime, agente_ia):
    lead, conv = _lead(pool, prime)
    agente_ia["json"] = _PEDIDO
    _fala(pool, conv, "quero o orçamento")
    agente.atender(pool, EMPRESA, conv)
    with pool.connection() as c:
        c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto)
                     values (%s,'whatsapp','in','lead','e aí, saiu?')""", (conv,))
        c.commit()
    agente.atender(pool, EMPRESA, conv)
    with pool.connection() as c:
        assert c.execute("select count(*) from orcamentos").fetchone()[0] == 1
    assert "já está com a equipe" in agente_ia["enviados"][-1][1]
