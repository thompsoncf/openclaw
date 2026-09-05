"""O Raio-X do dono (finance/raio_x_dono + web/painel_raio_x): o placar do
período com os oito filtros, uma linha por vendedor, e os blocos que o Zaq
enriquece sozinho.

Um cenário só, montado uma vez, com `agora` fixo (segunda 07/09/2026 10h, BRT):
cinco leads de setembro com tipo, data, convidados, origem e hora de chegada
diferentes — cada filtro tem que devolver exatamente o lead que ele descreve.

Banco dedicado e descartável; aplica a 209 (perda_motivo, origem_cliente).
"""
import os
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from psycopg_pool import ConnectionPool

from finance import raio_x_dono as rxd
from finance import raio_x_perfil as rxp

EVENTOS = rxp.perfil("eventos")
RECORRENTE = rxp.perfil("consultoria")

BRT = ZoneInfo("America/Sao_Paulo")
MIG = Path(__file__).resolve().parent.parent / "db" / "migracoes"
AGORA = datetime(2026, 9, 7, 10, 0, tzinfo=BRT)      # segunda

_SQL = """
create table contas (id bigserial primary key, nome text, nome_fantasia text,
  criado_em timestamptz not null default now());
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text default 'vendedor', ativo boolean default true);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, contato text, status text default 'novo', evento_em date, evento_tipo text,
  evento_convidados int, orcamento_id bigint, origem text, segmento text, porte text, uf text,
  criado_em timestamptz default now(), atualizado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  contato_ref text, contato_nome text, criado_em timestamptz default now());
create table mensagens (id bigserial primary key, conversa_id bigint, direcao text,
  autor text default 'humano', membro_id bigint, texto text default '', provider_sid text,
  criado_em timestamptz default now());
create table orcamentos (id bigserial primary key, cliente text, status text default 'rascunho',
  primeiro_ano_centavos bigint default 0, mensal_centavos bigint default 0, setup_centavos bigint default 0,
  itens jsonb, criado_em timestamptz default now(), aprovada_em timestamptz, sinal_pago_em timestamptz);
create table contratos (id bigserial primary key, conta_id bigint, orcamento_id bigint,
  status text default 'enviado', valor_centavos bigint, assinado_em timestamptz,
  enviado_em timestamptz, criado_em timestamptz default now());
create table eventos_agenda (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  titulo text, inicio timestamptz, status text default 'ativo', desfecho text,
  tipo text default 'empresa', tipo_evento text);
create table wa_qr_log (id bigserial primary key, conta_id bigint, nivel text default 'warn',
  msg text not null default '', dados jsonb, criado_em timestamptz not null default now());
create table wa_decifra_diario (dia date not null, conta_id bigint not null, from_me boolean not null,
  ocorrencias int not null default 0, ids_distintos int not null default 0,
  chegaram int, nunca_chegaram int, correlacionado_em timestamptz,
  apurado_em timestamptz not null default now(), primary key (dia, conta_id, from_me));
"""


def _dt(d, m, h, mi=0):
    return datetime(2026, m, d, h, mi, tzinfo=BRT)


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_raio_x_dono_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.execute((MIG / "209_raio_x_dono.sql").read_text(encoding="utf-8"))
        c.execute((MIG / "213_perda_motivo_por_perfil.sql").read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


@pytest.fixture(scope="module")
def cen(pool):
    """A Prime em miniatura, setembro de 2026. Ver o docstring do módulo."""
    with pool.connection() as c:
        conta = c.execute("insert into contas (nome, nome_fantasia) values ('Prime Festas','Prime') returning id").fetchone()[0]
        j = c.execute("insert into membros (conta_id, nome) values (%s,'Jaqueline Silva') returning id", (conta,)).fetchone()[0]
        p = c.execute("insert into membros (conta_id, nome) values (%s,'Pedro Yan') returning id", (conta,)).fetchone()[0]

        def lead(nome, vend, criado, *, status="novo", tipo=None, em=None, conv=None, origem_cli=None,
                 motivo=None, atualizado=None):
            return c.execute("""insert into prospeccao (conta_id, vendedor_id, contato, status, evento_tipo, evento_em,
                                   evento_convidados, origem_cliente, perda_motivo, origem, criado_em, atualizado_em)
                                values (%s,%s,%s,%s,%s,%s,%s,%s,%s,'whatsapp_inbound',%s,coalesce(%s,%s)) returning id""",
                             (conta, vend, nome, status, tipo, em, conv, origem_cli, motivo, criado, atualizado, criado)).fetchone()[0]

        def conversa(lid, criado, resposta_min):
            cv = c.execute("insert into conversas (conta_id, prospeccao_id, criado_em) values (%s,%s,%s) returning id",
                           (conta, lid, criado)).fetchone()[0]
            c.execute("insert into mensagens (conversa_id, direcao, autor, criado_em) values (%s,'in','lead',%s)", (cv, criado))
            c.execute("insert into mensagens (conversa_id, direcao, autor, criado_em) values (%s,'out','humano',%s)",
                      (cv, criado + timedelta(minutes=resposta_min)))

        def orc(lid, status, total, criado, aprovada=None):
            o = c.execute("""insert into orcamentos (cliente, status, primeiro_ano_centavos, criado_em, aprovada_em)
                             values ('x',%s,%s,%s,%s) returning id""", (status, total, criado, aprovada)).fetchone()[0]
            c.execute("update prospeccao set orcamento_id=%s where id=%s", (o, lid))
            return o

        # A · terça 10h (comercial) · casamento sáb 24/10 · 120 · instagram · respondida em 3 min
        #     proposta enviada 02/09 (R$ 5.000), contrato assinado 04/09
        a = lead("Ana", j, _dt(1, 9, 10), status="ganho", tipo="Casamento", em=date(2026, 10, 24), conv=120, origem_cli="instagram")
        conversa(a, _dt(1, 9, 10), 3)
        oa = orc(a, "fechado", 500000, _dt(2, 9, 10), aprovada=_dt(3, 9, 10))
        c.execute("insert into contratos (conta_id, orcamento_id, status, valor_centavos, assinado_em) values (%s,%s,'assinado',500000,%s)",
                  (conta, oa, _dt(4, 9, 10)))
        # B · quarta 21h (noite) · sem tipo · dom 15/11 · 80 · respondida em 60 min · rascunho de R$ 3.000
        b = lead("Bia", p, _dt(2, 9, 21), status="contatado", em=date(2026, 11, 15), conv=80)
        conversa(b, _dt(2, 9, 21), 60)
        orc(b, "rascunho", 300000, _dt(3, 9, 9))
        # C · sábado 13h (fds) · 15 anos sáb 12/12 · 200 · perdido: achou caro
        lead("Caio", j, _dt(5, 9, 13), status="perdido", tipo="15 anos", em=date(2026, 12, 12), conv=200,
             motivo="achou_caro", atualizado=_dt(6, 9, 9))
        # D · quinta 15h (comercial) · Chá (outro) sáb 10/10 · perdido sem motivo
        lead("Dora", p, _dt(3, 9, 15), status="perdido", tipo="Chá", em=date(2026, 10, 10), atualizado=_dt(6, 9, 9))
        # F · sexta 11h · formatura, sem data · proposta enviada e aprovada, sem contrato
        f = lead("Fabi", j, _dt(4, 9, 11), status="proposta", tipo="Formatura")
        orc(f, "enviado", 400000, _dt(4, 9, 15), aprovada=_dt(5, 9, 10))
        # E · 28/08 (período anterior) · casamento 20/10: conta no comparativo e na demanda × agenda
        lead("Eva", j, _dt(28, 8, 10), status="qualificado", tipo="Casamento", em=date(2026, 10, 20))
        # visitas: a da Ana aconteceu; a da Bia ninguém respondeu. Festas na agenda: out e dez.
        c.execute("insert into eventos_agenda (conta_id, prospeccao_id, titulo, inicio, desfecho) values (%s,%s,'Visita Ana',%s,'realizado')",
                  (conta, a, _dt(3, 9, 16)))
        c.execute("insert into eventos_agenda (conta_id, prospeccao_id, titulo, inicio) values (%s,%s,'Visita Bia',%s)",
                  (conta, b, _dt(4, 9, 16)))
        c.execute("insert into eventos_agenda (conta_id, titulo, inicio, tipo_evento) values (%s,'Casamento X',%s,'Casamento')",
                  (conta, _dt(17, 10, 18)))
        c.execute("insert into eventos_agenda (conta_id, titulo, inicio, tipo_evento) values (%s,'Locação Y',%s,'Locação')",
                  (conta, _dt(5, 12, 18)))
        c.execute("insert into eventos_agenda (conta_id, titulo, inicio, tipo_evento, status) values (%s,'Cancelada',%s,'Casamento','cancelado')",
                  (conta, _dt(24, 10, 18)))
        c.commit()
    return {"conta": conta, "j": j, "p": p}


def _f(**kw):
    base = {"periodo": "mes"}
    base.update(kw)
    return rxd.filtros(base, EVENTOS)


# ------------------------------------------------------------------ os filtros, puros

def test_filtros_normalizam_e_recusam_o_que_nao_esta_na_lista():
    f = rxd.filtros({"periodo": "xyz", "vendedor": "abc", "tipo": "Bolo", "mes": "2026-13", "dia": "x",
                     "conv": "1000", "origem": "tiktok", "hora": "madrugada"})
    assert f == {"periodo": "mes", "de": None, "ate": None, "vendedor": None, "tipo": "", "mes": "",
                 "dia": "", "conv": "", "origem": "", "hora": "", "segmento": "", "porte": "", "uf": "", "servico": ""}
    f = rxd.filtros({"periodo": "datas", "de": "2026-09-01", "ate": "2026-09-05", "vendedor": "7",
                     "tipo": "sem", "mes": "2026-10", "dia": "sabado", "conv": "60a99", "origem": "indicacao", "hora": "fds"})
    assert f["periodo"] == "datas" and f["de"] == date(2026, 9, 1) and f["ate"] == date(2026, 9, 5)
    assert f["vendedor"] == 7 and f["tipo"] == "sem" and f["mes"] == "2026-10" and f["dia"] == "sabado"
    assert f["conv"] == "60a99" and f["origem"] == "indicacao" and f["hora"] == "fds"
    # datas sem "ate" (ou invertidas) caem no mês, sem erro
    assert rxd.filtros({"periodo": "datas", "de": "2026-09-10", "ate": "2026-09-01"})["periodo"] == "mes"


def test_janela_por_datas_fecha_no_fim_do_ultimo_dia():
    f = rxd.filtros({"periodo": "datas", "de": "2026-09-01", "ate": "2026-09-05"})
    ini, fim, rot = rxd.janela_f(f, AGORA)
    assert ini == datetime(2026, 9, 1, tzinfo=BRT) and fim == datetime(2026, 9, 6, tzinfo=BRT)
    assert rot == "01/09 a 05/09"


def test_rotulos_das_listas():
    assert rxd.rotulo_motivo("achou_caro") == "Achou caro" and rxd.rotulo_motivo(None) == "sem motivo"
    assert rxd.rotulo_origem("indicacao") == "Indicação" and rxd.rotulo_origem("x") == ""


# ------------------------------------------------------------------ o placar

def test_placar_do_mes_sem_filtro(pool, cen):
    d = rxd.dono(pool, cen["conta"], _f(), AGORA, perfil=EVENTOS)
    p = d["placar"]
    assert d["rotulo"] == "01/09 a 07/09"
    assert p["leads"] == 5 and p["leads_com_data"] == 4 and p["leads_sem_tipo"] == 1
    assert p["pico"] == "ter 10h"                       # empate: o primeiro dia/hora
    assert p["primeira_n"] == 2 and p["primeira_min"] == 32 and p["primeira_em_5"] == 1
    assert p["primeira_comercial"] == 3 and p["primeira_noite"] == 60
    assert p["propostas"] == 2 and p["propostas_valor"] == 900000 and p["rascunhos"] == 1
    assert p["contratos"] == 1 and p["contratos_valor"] == 500000 and p["sem_assinar"] == 1
    assert p["visitas_ok"] == 1 and p["visitas_nao"] == 0 and p["visitas_sem_resposta"] == 1
    assert p["visitas_pct"] == 100 and p["visitas_confiavel"] is True
    # o período anterior (agosto) só tem a Eva
    assert d["anterior"]["leads"] == 1 and d["anterior"]["primeira_min"] is None
    assert rxd.delta(p["leads"], d["anterior"]["leads"]) == {"n": 4, "pct": 400}
    assert rxd.delta(p["primeira_min"], None) is None


@pytest.mark.parametrize("filtro, esperados", [
    ({"tipo": "Casamento"}, {"Ana"}),
    ({"tipo": "sem"}, {"Bia"}),
    ({"tipo": "outro"}, {"Dora"}),
    ({"hora": "comercial"}, {"Ana", "Dora", "Fabi"}),
    ({"hora": "noite"}, {"Bia"}),
    ({"hora": "fds"}, {"Caio"}),
    ({"dia": "sabado"}, {"Ana", "Caio", "Dora"}),
    ({"dia": "resto"}, {"Bia"}),
    ({"conv": "100a149"}, {"Ana"}),
    ({"conv": "60a99"}, {"Bia"}),
    ({"conv": "200mais"}, {"Caio"}),
    ({"origem": "instagram"}, {"Ana"}),
    ({"mes": "2026-10"}, {"Ana", "Dora"}),
])
def test_cada_filtro_devolve_o_lead_que_descreve(pool, cen, filtro, esperados):
    f = _f(**filtro)
    w, wv = rxd._where(f)
    with pool.connection() as c:
        nomes = {r[0] for r in c.execute(
            f"select p.contato from prospeccao p where p.conta_id = %s and p.criado_em >= %s{w}",
            [cen["conta"], datetime(2026, 9, 1, tzinfo=BRT), *wv]).fetchall()}
    assert nomes == esperados, filtro
    assert rxd.dono(pool, cen["conta"], f, AGORA, perfil=EVENTOS)["placar"]["leads"] == len(esperados)


def test_filtro_por_vendedor_corta_o_placar_e_a_linha_por_vendedor(pool, cen):
    d = rxd.dono(pool, cen["conta"], _f(vendedor=str(cen["p"])), AGORA, perfil=EVENTOS)
    assert d["placar"]["leads"] == 2                    # Bia e Dora
    assert [v["nome"] for v in d["vendedores"]] == ["Pedro Yan"]
    d = rxd.dono(pool, cen["conta"], _f(), AGORA, perfil=EVENTOS)
    assert [v["primeiro_nome"] for v in d["vendedores"]] == ["Jaqueline", "Pedro"]
    jaq = d["vendedores"][0]
    assert jaq["semana"]["leads"] == 3 and len(jaq["semana"]["contratos"]) == 1 and jaq["hoje"] >= 0


# ------------------------------------------------------------------ os blocos

def test_demanda_x_agenda_seis_meses_a_partir_do_corrente(pool, cen):
    d = rxd.dono(pool, cen["conta"], _f(), AGORA, perfil=EVENTOS)["demanda_agenda"]
    assert [m["rotulo"] for m in d] == ["set", "out", "nov", "dez", "jan 27", "fev 27"]
    por = {m["rotulo"]: (m["pedindo"], m["agenda"]) for m in d}
    assert por["out"] == (2, 1)      # Ana e Eva pedindo (Dora é perdida); 1 festa (a cancelada não conta)
    assert por["nov"] == (1, 0)      # Bia
    assert por["dez"] == (0, 1)      # Caio é perdido; a locação está na agenda


def test_dia_da_festa_e_tipo_com_ticket(pool, cen):
    d = rxd.dono(pool, cen["conta"], _f(), AGORA, perfil=EVENTOS)
    dias = {x["rotulo"]: x["n"] for x in d["dia_festa"]}
    assert dias["sáb"] == 3 and dias["dom"] == 1 and sum(dias.values()) == 4
    tipos = {t["tipo"]: t for t in d["tipos"]}
    assert set(tipos) == {"Casamento", "sem tipo", "15 anos", "Outro", "Formatura"}
    assert tipos["Casamento"]["ticket_centavos"] == 500000 and tipos["Formatura"]["ticket_centavos"] == 400000
    assert tipos["sem tipo"]["ticket_centavos"] is None and tipos["sem tipo"]["n"] == 1   # rascunho não é ticket
    assert d["tipos"][0]["tipo"] == "Casamento" and d["tipos"][-1]["tipo"] == "sem tipo"


def test_ciclo_e_perdas(pool, cen):
    d = rxd.dono(pool, cen["conta"], _f(), AGORA, perfil=EVENTOS)
    c = d["ciclo"]
    assert c["lead_proposta_n"] == 2 and 0 < c["lead_proposta_dias"] <= 1.0   # Ana 1 dia, Fabi 4h
    assert c["proposta_contrato_n"] == 1 and c["proposta_contrato_dias"] == 2.0
    assert [v["nome"] for v in c["por_vendedor"]] == ["Jaqueline"]
    p = d["perdas"]
    assert p["total"] == 2 and p["sem_motivo"] == 1
    assert p["itens"][0] == {"chave": "achou_caro", "rotulo": "Achou caro", "n": 1}
    assert d["confianca"] is not None and d["confianca"]["religou"] == 0


def test_bloco_que_falha_nao_derruba_a_tela(pool, cen, monkeypatch):
    def estoura(*a, **k):
        raise RuntimeError("tabela sumiu")
    monkeypatch.setattr(rxd, "_perdas", estoura)
    d = rxd.dono(pool, cen["conta"], _f(), AGORA, perfil=EVENTOS)
    assert d["perdas"] is None and d["placar"]["leads"] == 5 and d["ciclo"] is not None


# ------------------------------------------------------------------ a tela e o gate

def _req(papel="dono", **q):
    from types import SimpleNamespace
    from starlette.datastructures import QueryParams, URL
    return SimpleNamespace(session={"conta_id": 1, "papel": papel}, query_params=QueryParams(q),
                           url=URL("/painel/raio-x"), state=SimpleNamespace(), headers={}, cookies={})


def test_so_dono_e_gestor_abrem_a_tela(monkeypatch):
    import web.painel_raio_x as prx
    monkeypatch.setattr(prx, "conta_logada", lambda req: (1, "pj", "Prime"))
    for papel in ("vendedor", "financeiro"):
        r = prx.painel_raio_x(_req(papel))
        assert r.status_code == 303 and r.headers["location"] == "/painel", papel
    monkeypatch.setattr(prx, "conta_logada", lambda req: None)
    assert prx.painel_raio_x(_req()).headers["location"] == "/login"


def test_a_rota_esta_na_lista_do_gestor_e_nao_na_do_vendedor():
    from contas import equipe as eq
    assert "/painel/raio-x" in eq.rotas_do_papel("gestor")
    assert "/painel/raio-x" not in eq.rotas_do_papel("vendedor")
    assert "/painel/raio-x" not in eq.rotas_do_papel("financeiro")


def test_a_tela_renderiza_o_placar_os_filtros_e_os_blocos(pool, cen, monkeypatch):
    import web.painel_raio_x as prx
    from web import portal
    monkeypatch.setattr(prx, "conta_logada", lambda req: (cen["conta"], "pj", "Prime"))
    monkeypatch.setattr(prx, "get_pool", lambda: pool)
    monkeypatch.setattr(rxd, "agora_brt", lambda agora=None: AGORA)
    monkeypatch.setattr(rxd, "perfil_da_conta", lambda pool, conta_id: EVENTOS)
    capt = {}

    def fake_render(nome, request, **ctx):
        capt.update(nome=nome, ctx=ctx)
        from fastapi.responses import HTMLResponse
        tpl = portal._env.get_template(nome)
        # o miolo, sem o shell do painel (que precisa de sessão real e conta completa)
        bloco = tpl.blocks["conteudo"]
        return HTMLResponse("".join(bloco(tpl.new_context(dict(ctx, request=request)))))
    monkeypatch.setattr(prx, "_render", fake_render)
    html = bytes(prx.painel_raio_x(_req(tipo="Casamento", hora="comercial")).body).decode("utf-8")
    assert capt["nome"] == "raio_x" and capt["ctx"]["secao_ativa"] == "raio_x"
    assert capt["ctx"]["f"]["tipo"] == "Casamento" and capt["ctx"]["f"]["hora"] == "comercial"
    # a barra, com o filtro ligado marcado e o "limpar"
    assert 'name="tipo"' in html and 'value="Casamento" selected' in html and "limpar filtros" in html
    # o placar (só a Ana): 1 lead, 3 min, 1 proposta, 1 contrato
    assert ">1</b><span>leads</span>" in html and ">3 min</b>" in html
    assert "propostas · R$ 5.000" in html and "contratos · R$ 5.000" in html
    # os blocos e a confiança
    for t in ("Demanda × agenda", "Dia da festa", "Tipo de festa e ticket", "Do lead à proposta",
              "Por que perdeu", "Hora que chegou", "Confiança do dado", "Por vendedor"):
        assert t in html, t
    assert "Jaqueline Silva" in html


# ------------------------------------------------------------------ o perfil recorrente (a ZAQ em miniatura)

@pytest.fixture(scope="module")
def zaq(pool):
    """Consultoria que vende sistema por mensalidade: lead PJ com segmento, porte
    e UF; orçamento com mensalidade e itens do catálogo; reunião na agenda."""
    with pool.connection() as c:
        conta = c.execute("insert into contas (nome, nome_fantasia) values ('ZAQ Sistemas','ZAQ') returning id").fetchone()[0]
        v = c.execute("insert into membros (conta_id, nome) values (%s,'Vendedor Um') returning id", (conta,)).fetchone()[0]

        def lead(nome, criado, seg, porte, uf, status="novo", motivo=None):
            return c.execute("""insert into prospeccao (conta_id, vendedor_id, contato, status, segmento, porte, uf, perda_motivo, origem, criado_em, atualizado_em)
                                values (%s,%s,%s,%s,%s,%s,%s,%s,'whatsapp_inbound',%s,%s) returning id""",
                             (conta, v, nome, status, seg, porte, uf, motivo, criado, criado)).fetchone()[0]

        def orc(lid, status, mensal, itens, criado, aprovada=None):
            import json
            o = c.execute("""insert into orcamentos (cliente, status, primeiro_ano_centavos, mensal_centavos, itens, criado_em, aprovada_em)
                             values ('x',%s,%s,%s,%s::jsonb,%s,%s) returning id""",
                          (status, mensal * 12, mensal, json.dumps(itens), criado, aprovada)).fetchone()[0]
            c.execute("update prospeccao set orcamento_id=%s where id=%s", (o, lid))
            return o
        a = lead("Ótica Central", _dt(1, 9, 10), "Comércio varejista de artigos de óptica", "Microempresa", "PI", status="ganho")
        oa = orc(a, "fechado", 89000, [{"nome": "Agente de Atendimento", "mensal_centavos": 59000}, {"nome": "CRM / Leads", "mensal_centavos": 30000}], _dt(2, 9, 10), aprovada=_dt(3, 9, 10))
        c.execute("insert into contratos (conta_id, orcamento_id, status, valor_centavos, assinado_em) values (%s,%s,'assinado',1068000,%s)", (conta, oa, _dt(4, 9, 10)))
        b = lead("Clínica Sorriso", _dt(2, 9, 21), "Atividade odontológica", "Empresa de Pequeno Porte", "PI", status="proposta")
        orc(b, "enviado", 129000, [{"nome": "Agente de Atendimento", "mensal_centavos": 59000}, {"nome": "Automação Financeira", "mensal_centavos": 70000}], _dt(3, 9, 9))
        lead("Loja Bella", _dt(3, 9, 15), "Loja", "Microempresa", "MA")
        lead("Sem Nada", _dt(4, 9, 11), "", "", "")
        lead("Contábil Souza", _dt(5, 9, 13), "Atividades de contabilidade", "Demais", "PI", status="perdido", motivo="ficou_com_atual")
        c.execute("insert into eventos_agenda (conta_id, prospeccao_id, titulo, inicio, desfecho) values (%s,%s,'Reunião Ótica',%s,'realizado')", (conta, a, _dt(3, 9, 16)))
        c.execute("insert into eventos_agenda (conta_id, prospeccao_id, titulo, inicio) values (%s,%s,'Reunião Clínica',%s)", (conta, b, _dt(4, 9, 16)))
        c.commit()
    return {"conta": conta, "v": v}


def _fr(**kw):
    base = {"periodo": "mes"}
    base.update(kw)
    return rxd.filtros(base, RECORRENTE)


def test_no_recorrente_os_filtros_de_festa_sao_ignorados_e_os_de_segmento_valem(zaq, pool):
    f = _fr(tipo="Casamento", dia="sabado", conv="60a99", mes="2026-10", segmento="clinica", porte="epp", uf="pi", servico="Agente de Atendimento")
    assert f["tipo"] == "" and f["dia"] == "" and f["conv"] == "" and f["mes"] == ""
    assert f["segmento"] == "clinica" and f["porte"] == "epp" and f["uf"] == "PI" and f["servico"] == "Agente de Atendimento"


@pytest.mark.parametrize("filtro, esperados", [
    ({"segmento": "loja"}, {"Ótica Central", "Loja Bella"}),
    ({"segmento": "clinica"}, {"Clínica Sorriso"}),
    ({"segmento": "escritorio"}, {"Contábil Souza"}),
    ({"segmento": "sem"}, {"Sem Nada"}),
    ({"porte": "me"}, {"Ótica Central", "Loja Bella"}),
    ({"porte": "epp"}, {"Clínica Sorriso"}),
    ({"porte": "sem"}, {"Sem Nada"}),
    ({"uf": "ma"}, {"Loja Bella"}),
    ({"servico": "Automação Financeira"}, {"Clínica Sorriso"}),
    ({"servico": "Agente de Atendimento"}, {"Ótica Central", "Clínica Sorriso"}),
])
def test_cada_filtro_do_recorrente_devolve_o_lead_que_descreve(pool, zaq, filtro, esperados):
    w, wv = rxd._where(_fr(**filtro))
    with pool.connection() as c:
        nomes = {r[0] for r in c.execute(
            f"select p.contato from prospeccao p where p.conta_id = %s{w}", [zaq["conta"], *wv]).fetchall()}
    assert nomes == esperados, filtro


def test_placar_e_blocos_do_recorrente(pool, zaq):
    d = rxd.dono(pool, zaq["conta"], _fr(), AGORA, perfil=RECORRENTE)
    p = d["placar"]
    assert p["leads"] == 5 and p["propostas"] == 2 and p["propostas_mensal"] == 89000 + 129000
    assert p["contratos"] == 1 and p["contratos_mensal"] == 89000
    assert p["visitas_ok"] == 1 and p["visitas_sem_resposta"] == 1
    # os blocos de festa não rodam; os do recorrente sim
    assert d["demanda_agenda"] is None and d["dia_festa"] is None and d["tipos"] is None
    assert [(m["rotulo"], m["proposta"], m["fechada"]) for m in d["mrr"]] == [("set", 218000, 89000)]
    seg = {x["chave"]: (x["n"], x["fechou"]) for x in d["segmentos"]}
    assert seg == {"loja": (2, 1), "clinica": (1, 0), "escritorio": (1, 0), "sem": (1, 0)}
    assert d["segmentos"][0]["chave"] == "loja" and d["segmentos"][-1]["chave"] == "sem"
    sv = d["servicos"]
    assert sv["historico"] is False
    assert sv["itens"][0] == {"nome": "Agente de Atendimento", "n": 2, "mensal_centavos": 59000}
    assert {x["nome"] for x in sv["itens"]} == {"Agente de Atendimento", "CRM / Leads", "Automação Financeira"}
    perdas = d["perdas"]
    assert perdas["total"] == 1 and perdas["itens"][0]["chave"] == "ficou_com_atual"
    assert "data_indisponivel" not in {x["chave"] for x in perdas["itens"]}


def test_servicos_sem_proposta_no_periodo_cai_no_historico(pool, zaq):
    f = rxd.filtros({"periodo": "datas", "de": "2026-07-01", "ate": "2026-07-31"}, RECORRENTE)
    d = rxd.dono(pool, zaq["conta"], f, AGORA, perfil=RECORRENTE)
    assert d["placar"]["propostas"] == 0 and d["servicos"]["historico"] is True and d["servicos"]["itens"]


def test_a_tela_do_recorrente_nao_fala_de_festa(pool, zaq, monkeypatch):
    import web.painel_raio_x as prx
    from web import portal
    monkeypatch.setattr(prx, "conta_logada", lambda req: (zaq["conta"], "pj", "ZAQ"))
    monkeypatch.setattr(prx, "get_pool", lambda: pool)
    monkeypatch.setattr(rxd, "agora_brt", lambda agora=None: AGORA)
    monkeypatch.setattr(rxd, "perfil_da_conta", lambda pool, conta_id: RECORRENTE)

    def fake_render(nome, request, **ctx):
        from fastapi.responses import HTMLResponse
        tpl = portal._env.get_template(nome)
        return HTMLResponse("".join(tpl.blocks["conteudo"](tpl.new_context(dict(ctx, request=request)))))
    monkeypatch.setattr(prx, "_render", fake_render)
    html = bytes(prx.painel_raio_x(_req(segmento="loja")).body).decode("utf-8")
    baixo = html.lower()
    for palavra in ("festa", "convidados", "visita", "sábado", "casamento"):
        assert palavra not in baixo, palavra
    for t in ("Segmento", "Porte", "UF", "Serviço", "Mensalidade proposta × fechada", "Segmento que chega",
              "Serviço mais proposto", "Reuniões que aconteceram", "reuniões que aconteceram", "/mês", "Por que perdeu"):
        assert t in html, t
    assert 'value="loja" selected' in html and ">2</b><span>leads</span>" in html
    assert "Sua conta ainda não escolheu o nicho" not in html


def test_conta_sem_nicho_ve_o_aviso_pra_escolher(pool, zaq, monkeypatch):
    import web.painel_raio_x as prx
    from web import portal
    monkeypatch.setattr(prx, "conta_logada", lambda req: (zaq["conta"], "pj", "ZAQ"))
    monkeypatch.setattr(prx, "get_pool", lambda: pool)
    monkeypatch.setattr(rxd, "perfil_da_conta", lambda pool, conta_id: rxp.perfil(None))

    def fake_render(nome, request, **ctx):
        from fastapi.responses import HTMLResponse
        tpl = portal._env.get_template(nome)
        return HTMLResponse("".join(tpl.blocks["conteudo"](tpl.new_context(dict(ctx, request=request)))))
    monkeypatch.setattr(prx, "_render", fake_render)
    html = bytes(prx.painel_raio_x(_req()).body).decode("utf-8")
    assert "Sua conta ainda não escolheu o nicho" in html and "festa" not in html.lower()


def test_conta_de_produto_nao_tem_raio_x(pool, monkeypatch):
    import web.painel_raio_x as prx
    monkeypatch.setattr(prx, "conta_logada", lambda req: (1, "pj", "Loja"))
    monkeypatch.setattr(prx, "get_pool", lambda: pool)
    monkeypatch.setattr(rxd, "perfil_da_conta", lambda pool, conta_id: rxp.perfil("hortifruti"))
    r = prx.painel_raio_x(_req())
    assert r.status_code == 303 and r.headers["location"] == "/painel"
