"""Cadastro manual feito DENTRO do funil precisa nascer no funil.

O bug: o formulário "🎯 Captar Lead" que abre na tela do funil gravava o lead com
`estagio='base'` (o padrão da coluna). O JS ainda desenhava o card na coluna Novo,
então parecia ter funcionado — só que o funil lista `estagio='lead'`, e no primeiro
refresh o lead sumia. Quem cadastrou ia procurar e não achava (ele estava na Base).

A regra: quem manda é o campo `destino` do formulário. `destino=funil` (só o painel
do funil manda) nasce como lead; todo o resto — Captar leads, Base, CSV, Google —
continua nascendo na base, que é o caminho da captação em massa.
"""
import os
from types import SimpleNamespace

import pytest
from psycopg_pool import ConnectionPool

from web import painel_prospeccao as pp

CONTA = 7

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text not null, segmento text, cidade text, uf text, contato text, cargo text,
  telefone text, whatsapp text, email text, cnpj text, cpf text, tipo text default 'pj',
  temperatura text default 'frio', valor_estimado_centavos bigint default 0, origem text,
  obs text, socio text, regime_tributario text, porte text, instagram text, site_url text,
  tem_site boolean, receita jsonb, criado_por bigint, estagio text default 'base',
  status text default 'novo',
  atualizado_em timestamptz default now(), criado_em timestamptz default now(),
  constraint uq_prospeccao_conta_cnpj unique (conta_id, cnpj),
  constraint uq_prospeccao_conta_cpf unique (conta_id, cpf));
create table funil_etapas (id bigserial primary key, conta_id bigint, chave text,
  rotulo text, ordem int default 0, fixa boolean default false,
  criado_em timestamptz default now(), constraint uq_funil_etapa unique (conta_id, chave));
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_lead_funil_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.commit()
    yield p
    p.close()


def _novo(req, **kw):
    """Chama a rota como o FastAPI chamaria: todo campo do formulário presente."""
    campos = dict(segmento="", cidade="", uf="", contato="", telefone="", whatsapp="",
                  email="", cnpj="", cpf="", documento="", tipo="", temperatura="frio",
                  valor="", origem="manual", vendedor_id="", obs="", socio="",
                  regime_tributario="", porte="", cargo="", instagram="", site_url="",
                  receita="", voltar="", destino="")
    campos.update(kw)
    return pp.prospeccao_novo(req, **campos)


def _logado(monkeypatch, pool):
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_acesso", lambda req: (
        {"conta_id": CONTA, "membro_id": 1, "gerencia": True, "pode_atribuir": True}, None))
    return SimpleNamespace(session={}, headers={"x-requested-with": "fetch"})


def _estagio(pool, empresa):
    with pool.connection() as c:
        return c.execute("select estagio, status from prospeccao where empresa=%s",
                         (empresa,)).fetchone()


def test_cadastro_pelo_funil_entra_no_funil(monkeypatch, pool):
    req = _logado(monkeypatch, pool)
    r = _novo(req, empresa="LUCAS FORTES", voltar="/painel/prospeccao", destino="funil")
    assert r.status_code == 200
    assert _estagio(pool, "LUCAS FORTES") == ("lead", "novo")


def test_cadastro_sem_destino_continua_na_base(monkeypatch, pool):
    """Captar leads, CSV, Google Maps: nada disso muda de lugar."""
    req = _logado(monkeypatch, pool)
    _novo(req, empresa="PADARIA DO ZE")
    assert _estagio(pool, "PADARIA DO ZE")[0] == "base"


def test_destino_desconhecido_cai_na_base(monkeypatch, pool):
    req = _logado(monkeypatch, pool)
    _novo(req, empresa="OFICINA XPTO", destino="qualquer-coisa")
    assert _estagio(pool, "OFICINA XPTO")[0] == "base"


def test_aviso_diz_onde_o_lead_caiu(monkeypatch, pool):
    """"entrou na prospecção" era a frase que fazia a pessoa procurar no lugar errado."""
    import json
    req = _logado(monkeypatch, pool)
    no_funil = json.loads(bytes(_novo(req, empresa="AAA", destino="funil").body))
    na_base = json.loads(bytes(_novo(req, empresa="BBB").body))
    assert "funil" in no_funil["msg"] and no_funil["estagio"] == "lead"
    assert "Base" in na_base["msg"] and "Promover" in na_base["msg"]
    assert na_base["estagio"] == "base"


def test_aviso_do_funil_usa_o_rotulo_da_conta(monkeypatch, pool):
    """A 1ª etapa pode ter sido renomeada — o aviso não pode chutar "Novo"."""
    import json
    with pool.connection() as c:
        c.execute("""insert into funil_etapas (conta_id, chave, rotulo, ordem, fixa)
                     values (%s,'novo','Chegou agora',0,true)""", (CONTA,))
        c.commit()
    req = _logado(monkeypatch, pool)
    d = json.loads(bytes(_novo(req, empresa="CCC", destino="funil").body))
    assert "Chegou agora" in d["msg"]


def test_o_formulario_do_funil_manda_destino_funil():
    """Guarda de template: sem este hidden o lead volta a nascer na base."""
    assert '<input type="hidden" name="destino" value="funil">' in pp._KANBAN_TPL
    # e o painel da Base segue sem ele (captação em massa continua indo pra base)
    assert 'name="destino"' not in pp._CAPTURA_PANEL_HTML
