"""AS TRÊS TRILHAS NO QUADRO (docs/mockups/funil_tres_trilhas.html, aprovado pelo dono
em 27/09/2026 "com as recomendações").

O que este arquivo fixa, entrando pela porta da frente (o handler do quadro, como o
test_funil_selo_follow_up ensina):

  * conta sem IA do número nem resgate — a ZAQ, com um chip só — não ganha barra
    nenhuma: o quadro fica como sempre foi;
  * com a IA e o resgate, a barra das três trilhas aparece pra gerência e NUNCA pro
    vendedor (decisão 2);
  * a coluna Resgate na frente, com a origem, a etapa e o próximo passo — e o card
    SAI da coluna da etapa só na tela (a etapa do lead não muda);
  * a trilha filtra o quadro; na trilha dos vendedores a coluna Resgate some;
  * o card da IA diz "IA do número", e "gente assumiu" quando alguém da equipe
    tirou a IA da conversa (decisão 4);
  * o card que saiu do resgate carrega "veio do Resgate".
"""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest
from psycopg_pool import ConnectionPool
from starlette.datastructures import QueryParams

from finance import trilhas as tri
from tests.test_funil_por_mes import _SQL as _SQL_QUADRO
from web import painel_prospeccao as pp

BASE = Path(__file__).resolve().parents[1] / "db" / "migracoes"
CONTA, CHIP2 = 34, 36

_SQL = _SQL_QUADRO + """
alter table funil_etapas add column if not exists fase text default 'venda';
alter table membros add column if not exists whatsapp text;
alter table conversas add column if not exists agente_ativo boolean default false;
alter table conversas add column if not exists status text default 'aberta';
alter table conversas add column if not exists contato_ref text;
alter table conversas add column if not exists responsavel_membro_id bigint;
alter table mensagens add column if not exists autor text;
alter table mensagens add column if not exists canal text;
alter table prospeccao add column if not exists contato text;
alter table prospeccao add column if not exists perda_motivo text;
create table resgate_envios (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  membro_id bigint, tipo text, ref_em timestamptz, texto text, ok boolean default true,
  erro text, criado_em timestamptz not null default now());
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_funil_trilhas_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=4, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.execute("insert into contas (id, nome, chip_de) values (%s,'Prime',null),(%s,'CP',%s)",
                  (CONTA, CHIP2, CONTA))
        for m in ("388_regra_por_chip.sql", "396_resgate_ia.sql", "398_resgate_toques.sql",
                  "401_ia_fora_da_esteira.sql", "403_resgate_origem_e_espelho.sql"):
            c.execute((BASE / m).read_text(encoding="utf-8"))
        c.execute("insert into funil_etapas (conta_id, chave, rotulo, ordem) values "
                  "(%s,'contatado','Contatado',10),(%s,'negociacao','Negociação',50),"
                  "(%s,'perdido','Perdido',910)", (CONTA, CONTA, CONTA))
        c.commit()
    yield p
    p.close()


def _membro(pool, nome, papel="vendedor"):
    with pool.connection() as c:
        mid = c.execute("insert into membros (conta_id, nome, email, papel) values (%s,%s,%s,%s) "
                        "returning id", (CONTA, nome, f"{nome.lower()}@x.com", papel)).fetchone()[0]
        c.commit()
    return mid


@pytest.fixture()
def equipe(pool):
    return {"ZAQ": _membro(pool, "ZAQ SDR"), "PEDRO": _membro(pool, "PEDRO SILVA"),
            "DONO": _membro(pool, "MANOEL", "dono")}


def _ia_e_resgate(pool, equipe, modo="ensaio"):
    with pool.connection() as c:
        c.execute("""insert into chip_regra (conta_id, chip_id, ativa, membro_id, ia_ligada, ia_insiste)
                     values (%s,%s,true,%s,true,true)""", (CONTA, CHIP2, equipe["ZAQ"]))
        c.execute("insert into resgate_config (conta_id, modo, membro_id) values (%s,%s,%s)",
                  (CONTA, modo, equipe["ZAQ"]))
        c.commit()


def _lead(pool, empresa, vend, status="contatado", *, ult="in", dias=1):
    with pool.connection() as c:
        lid = c.execute("insert into prospeccao (conta_id, empresa, status, estagio, vendedor_id) "
                        "values (%s,%s,%s,'lead',%s) returning id",
                        (CONTA, empresa, status, vend)).fetchone()[0]
        cid = c.execute("insert into conversas (conta_id, prospeccao_id, canal, agente_ativo) "
                        "values (%s,%s,'whatsapp',true) returning id", (CONTA, lid)).fetchone()[0]
        c.execute("insert into mensagens (conversa_id, direcao, texto, criado_em) "
                  "values (%s,%s,'oi', now() - make_interval(days => %s))", (cid, ult, dias))
        c.commit()
    return lid, cid


def _resgatado(pool, equipe, empresa="Duda Festa", *, estado="chamado", ativo=True):
    lid, cid = _lead(pool, empresa, equipe["ZAQ"] if ativo else equipe["PEDRO"], ult="out")
    with pool.connection() as c:
        c.execute("""insert into resgate_leads (prospeccao_id, conta_id, membro_id, vendedor_antes,
                        conversa_id, faixa, entrou_em, ativo, estado, ultimo_envio_em, toques,
                        origem, parado_desde, resumo_linha)
                     values (%s,%s,%s,%s,%s,3, now() - interval '1 day', %s, %s,
                             now() - interval '1 day', 1, 'follow_up', now() - interval '9 days',
                             'infantil pra 80 · parou: valor do domingo')""",
                  (lid, CONTA, equipe["ZAQ"], equipe["PEDRO"], cid, ativo, estado))
        c.commit()
    return lid


def _render(monkeypatch, pool, *, gerencia=True, membro=1, trilha=""):
    import finance.vendas as v
    monkeypatch.setattr(v, "vende_data", lambda pool, conta_id: True)
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_tem_follow_up", lambda conta: False)
    ctx = {"conta_id": CONTA, "membro_id": membro, "gerencia": gerencia,
           "pode_atribuir": gerencia, "conta": (CONTA, "Prime")}
    monkeypatch.setattr(pp, "_acesso", lambda req: (ctx, None))
    req = SimpleNamespace(session={}, query_params=QueryParams(""))
    r = pp.prospeccao_kanban(req, entrou="tudo", trilha=trilha)
    assert r.status_code == 200, "o quadro tem que abrir"
    return bytes(r.body).decode("utf-8")


def _coluna(html, status):
    i = html.index(f'data-status="{status}"')
    j = html.find('data-status="', i + 10)
    return html[i: j if j > 0 else len(html)]


def test_conta_sem_ia_nem_resgate_nao_ganha_barra(monkeypatch, pool, equipe):
    """A ZAQ: um chip só, sem regra e sem resgate. O quadro de sempre."""
    _lead(pool, "Evolui", equipe["PEDRO"])
    html = _render(monkeypatch, pool)
    assert "Evolui" in html
    assert '<nav class="kbtri-bar"' not in html and 'class="kbcol kbcol-rsg"' not in html


def test_a_barra_das_tres_trilhas_e_a_coluna_resgate(monkeypatch, pool, equipe):
    _ia_e_resgate(pool, equipe)
    _lead(pool, "Carla Vendedor", equipe["PEDRO"])
    _lead(pool, "Bia da IA", equipe["ZAQ"])
    _resgatado(pool, equipe)
    html = _render(monkeypatch, pool)
    assert '<nav class="kbtri-bar"' in html
    for rot in ("Vendedores", "IA do número", "Resgate da IA", "Ensaio"):
        assert rot in html
    rsg = _coluna(html, "_resgate")
    assert "Duda Festa" in rsg
    assert "veio do follow-up · era de Pedro · 9 dias parado" in rsg
    assert "etapa: Contatado" in rsg and "2º toque" in rsg
    assert "✨ infantil pra 80 · parou: valor do domingo" in rsg
    # a etapa não mudou, mas o card saiu da coluna dela — só na tela
    assert "Duda Festa" not in _coluna(html, "contatado")
    assert "🤖 IA do número" in _coluna(html, "contatado")


def test_a_trilha_filtra_o_quadro(monkeypatch, pool, equipe):
    _ia_e_resgate(pool, equipe)
    _lead(pool, "Carla Vendedor", equipe["PEDRO"])
    _lead(pool, "Bia da IA", equipe["ZAQ"])
    _resgatado(pool, equipe)
    ia = _render(monkeypatch, pool, trilha="ia")
    assert "Bia da IA" in ia and "Carla Vendedor" not in ia and 'class="kbcol kbcol-rsg"' not in ia
    vend = _render(monkeypatch, pool, trilha="vend")
    assert "Carla Vendedor" in vend and "Bia da IA" not in vend and "Duda Festa" not in vend
    rsg = _render(monkeypatch, pool, trilha="rsg")
    assert "Duda Festa" in rsg and "Carla Vendedor" not in rsg


def test_o_vendedor_nao_ve_a_barra_e_ve_o_selo_de_quem_voltou(monkeypatch, pool, equipe):
    _ia_e_resgate(pool, equipe)
    _resgatado(pool, equipe, "Lia Voltou", estado="respondeu", ativo=False)
    html = _render(monkeypatch, pool, gerencia=False, membro=equipe["PEDRO"], trilha="ia")
    assert '<nav class="kbtri-bar"' not in html and 'class="kbcol kbcol-rsg"' not in html
    assert "Lia Voltou" in html            # a trilha da URL não vale pro vendedor
    assert "♻️ veio do Resgate · era de Pedro" in html


def test_gente_assumiu_a_conversa_da_ia(monkeypatch, pool, equipe):
    _ia_e_resgate(pool, equipe)
    lid, cid = _lead(pool, "Bia da IA", equipe["ZAQ"])
    with pool.connection() as c:
        c.execute("update conversas set agente_ativo=false, status='pendente' where id=%s", (cid,))
        c.commit()
    html = _render(monkeypatch, pool)
    assert "gente assumiu" in _coluna(html, "contatado")


def test_quem_respondeu_volta_pra_etapa_com_o_selo(monkeypatch, pool, equipe):
    _ia_e_resgate(pool, equipe)
    _resgatado(pool, equipe, "Ivo Respondeu", estado="respondeu")
    html = _render(monkeypatch, pool)
    assert "Ivo Respondeu" not in _coluna(html, "_resgate")         # ninguém em andamento
    assert "♻️ veio do Resgate" in _coluna(html, "contatado")


def test_o_proximo_passo_em_texto():
    agora = datetime(2026, 9, 28, 15, 0, tzinfo=timezone.utc)
    ult = agora - timedelta(days=1)
    assert tri.passo({"ultimo_envio_em": ult, "toques": 1}, agora) == "retomada 27/09 · 2º toque em 2 dias"
    assert tri.passo({"ultimo_envio_em": ult, "toques": 2}, agora) == "2º toque 27/09 · última chamada em 2 dias"
    assert tri.passo({"ultimo_envio_em": ult, "toques": 3}, agora) == "última chamada 27/09 · vira perdido em 2 dias"
    assert tri.passo({"ultimo_envio_em": ult, "uma_vez": True}, agora).startswith("chamado 27/09 · uma vez só")
    assert tri.passo({"ultimo_envio_em": None}, agora) == "na fila · sai hoje"


def test_no_ensaio_a_coluna_mostra_os_proximos_da_fila(monkeypatch, pool, equipe):
    """27/09/2026, o dono no primeiro teste: "faltou o funil resgate, não vi". No
    Ensaio ninguém muda de dono — então a coluna mostra os próximos que a IA chamaria,
    com a origem de cada um. O lead continua no card do vendedor, na etapa dele."""
    from finance import resgate as rg
    _ia_e_resgate(pool, equipe)
    lid, _ = _lead(pool, "Olga Casamento", equipe["PEDRO"], ult="out", dias=12)
    agora = datetime.now(timezone.utc)
    monkeypatch.setattr(rg, "fila", lambda *a, **k: [
        {"id": lid, "quem": "Olga Casamento", "vendedor_id": equipe["PEDRO"], "status": "contatado",
         "origem": "follow_up", "desde": agora - timedelta(days=12), "faixa": 3,
         "evento_em": None, "uma_vez": False}])
    html = _render(monkeypatch, pool)
    rsg = _coluna(html, "_resgate")
    assert "Ensaio" in rsg and "Os próximos que a IA chamaria" in rsg
    assert "Olga Casamento" in rsg and "vem do follow-up · de Pedro · 12 dias parado" in rsg
    assert "etapa: Contatado" in rsg and "prévia no Ensaio" in rsg
    assert "Olga Casamento" in _coluna(html, "contatado")     # continua com o vendedor


def test_resgate_ligado_sem_ninguem_na_fila_diz_isso(monkeypatch, pool, equipe):
    from finance import resgate as rg
    _ia_e_resgate(pool, equipe)
    monkeypatch.setattr(rg, "fila", lambda *a, **k: [])
    html = _render(monkeypatch, pool)
    assert "ninguém na fila hoje" in _coluna(html, "_resgate")
