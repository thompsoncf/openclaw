"""O balão resumido do lead no funil (rota /painel/prospeccao/{id}/resumo).

POR QUE ISTO EXISTE. Clicar num card do funil abria uma gaveta de 1080px com um
iframe carregando a ficha INTEIRA (edição de cadastro, IA, decisor Credify,
orçamento) — pesado pra só decidir a próxima ação. Virou um balão resumido: uma
rota JSON enxuta (telefone, e-mail, valor, canais, últimas 2 atividades), no
mesmo padrão de permissão da ficha completa (`_pode_ver`). Este arquivo testa
essa rota isolada do JS — o comportamento do balão em si é coberto por
tests/test_painel_js_sintaxe.py.
"""
import json
import os
from types import SimpleNamespace

import pytest
from psycopg_pool import ConnectionPool
from starlette.datastructures import QueryParams

from web import painel_prospeccao as pp

CONTA = 21

_SQL = """
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text, ativo boolean default true);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text not null, cnpj text, segmento text, cidade text, uf text,
  contato text, cargo text, telefone text, whatsapp text, email text,
  status text default 'novo', temperatura text default 'frio',
  valor_estimado_centavos bigint default 0, origem text, obs text, instagram text,
  socio text, regime_tributario text, porte text,
  ultimo_contato_em timestamptz, proximo_contato_em date,
  orcamento_id bigint, tem_site boolean, maps_url text, receita jsonb, site_url text,
  decisor_nome text, decisor_cargo text, decisor_telefone text, decisor_whatsapp boolean,
  decisor_em timestamptz, decisor_telefones jsonb,
  tipo text default 'pj', cpf text,
  cep text, endereco text, numero text, bairro text, nascimento date,
  estagio text default 'lead',
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table prospeccao_atividades (id bigserial primary key, prospeccao_id bigint,
  membro_id bigint, tipo text, resultado text, descricao text,
  agendado_para timestamptz, criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint,
  prospeccao_id bigint references prospeccao(id), canal text,
  criado_em timestamptz default now(), visto_ate_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint,
  direcao text, criado_em timestamptz default now(),
  midia_ref jsonb, midia_tipo text, midia_meta jsonb, midia_arquivo text, midia_guardada_em timestamptz, midia_guardada_por bigint);
create table funil_etapas (id bigserial primary key, conta_id bigint, chave text,
  rotulo text, ordem int default 0, fixa boolean default false,
  criado_em timestamptz default now(), constraint uq_funil_etapa unique (conta_id, chave));
create table contas (id bigserial primary key, chip_de bigint);
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_lead_resumo_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s",
                  (dbname,))
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


def _lead(pool, *, vendedor_id=None, **kw):
    campos = dict(empresa="Padaria Bom Pão", telefone="86999998888",
                  email="contato@bompao.com", valor_estimado_centavos=420000,
                  temperatura="quente", segmento="Varejo", cidade="Teresina", uf="PI",
                  cnpj="12345678000190")
    campos.update(kw)
    with pool.connection() as c:
        lid = c.execute(
            f"""insert into prospeccao (conta_id, vendedor_id, {', '.join(campos)})
                values (%s, %s, {', '.join(['%s'] * len(campos))}) returning id""",
            (CONTA, vendedor_id, *campos.values())).fetchone()[0]
        c.commit()
    return lid


def _atividade(pool, lead_id, *, tipo="ligacao", resultado="interessado", descricao=""):
    with pool.connection() as c:
        c.execute(
            "insert into prospeccao_atividades (prospeccao_id, tipo, resultado, descricao) "
            "values (%s,%s,%s,%s)", (lead_id, tipo, resultado, descricao))
        c.commit()


def _conversa_com_msg(pool, lead_id, canal, *, respondeu=True):
    with pool.connection() as c:
        cid = c.execute(
            "insert into conversas (conta_id, prospeccao_id, canal) values (%s,%s,%s) returning id",
            (CONTA, lead_id, canal)).fetchone()[0]
        c.execute("insert into mensagens (conversa_id, direcao) values (%s,%s)",
                  (cid, "in" if respondeu else "out"))
        c.commit()


def _resumo(monkeypatch, pool, alvo_id, *, gerencia=True, membro_id=1):
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_acesso", lambda req: (
        {"conta_id": CONTA, "membro_id": membro_id, "gerencia": gerencia,
         "pode_atribuir": gerencia}, None))
    req = SimpleNamespace(session={}, query_params=QueryParams(""))
    return pp.prospeccao_resumo(req, alvo_id)


def _editar_rapido(monkeypatch, pool, alvo_id, *, gerencia=True, membro_id=1, **campos):
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_acesso", lambda req: (
        {"conta_id": CONTA, "membro_id": membro_id, "gerencia": gerencia,
         "pode_atribuir": gerencia}, None))
    req = SimpleNamespace(session={}, query_params=QueryParams(""))
    kw = dict(contato="", cargo="", telefone="", whatsapp="", email="",
              instagram="", site_url="", valor="", obs="")
    kw.update(campos)
    return pp.prospeccao_editar_rapido(req, alvo_id, **kw)


def test_resumo_traz_contato_valor_e_temperatura(monkeypatch, pool):
    lid = _lead(pool)
    r = _resumo(monkeypatch, pool, lid)
    assert r.status_code == 200
    d = json.loads(bytes(r.body))
    assert d["ok"] is True
    assert d["empresa"] == "Padaria Bom Pão"
    assert d["telefone"] == "86999998888"
    assert d["email"] == "contato@bompao.com"
    assert d["valor_fmt"] == "R$ 4.200,00"
    assert d["temperatura"] == "quente"
    assert d["doc_fmt"], "CNPJ devia vir formatado (doc_fmt)"


def test_resumo_traz_os_campos_de_cadastro_que_a_v1_deixou_de_fora(monkeypatch, pool):
    """21/08, 2ª rodada: a v1 do balão só tinha telefone/e-mail/valor/documento
    — o usuário apontou que faltava o resto do cadastro (contato, WhatsApp,
    Instagram, site, observação) e que sem isso não dava pra editar rápido."""
    lid = _lead(pool, contato="Marcos Silva", cargo="sócio", whatsapp="86988887777",
               instagram="@padariabompao", site_url="https://bompao.com.br",
               obs="Fecha pedido toda 2ª feira")
    r = _resumo(monkeypatch, pool, lid)
    d = json.loads(bytes(r.body))
    assert d["contato"] == "Marcos Silva"
    assert d["cargo"] == "sócio"
    assert d["whatsapp"] == "86988887777"
    assert d["instagram"] == "@padariabompao"
    assert d["site_url"] == "https://bompao.com.br"
    assert d["site_dominio"] == "bompao.com.br"
    assert d["obs"] == "Fecha pedido toda 2ª feira"
    assert d["valor_edit"] == "4200,00", (
        "valor_edit tem que vir num formato que o próprio parser do backend aceita de volta")


def test_resumo_traz_so_as_2_atividades_mais_recentes(monkeypatch, pool):
    lid = _lead(pool)
    _atividade(pool, lid, descricao="primeira")
    _atividade(pool, lid, descricao="segunda")
    _atividade(pool, lid, descricao="terceira — a mais recente")
    r = _resumo(monkeypatch, pool, lid)
    d = json.loads(bytes(r.body))
    assert len(d["atividades"]) == 2, "o resumo tem que trazer só as últimas 2, não a ficha inteira"
    assert d["atividades"][0]["descricao"] == "terceira — a mais recente"


def test_resumo_so_traz_canal_com_conversa_de_verdade(monkeypatch, pool):
    lid = _lead(pool)
    _conversa_com_msg(pool, lid, "whatsapp", respondeu=True)
    r = _resumo(monkeypatch, pool, lid)
    d = json.loads(bytes(r.body))
    assert len(d["canais_contato"]) == 1
    assert d["canais_contato"][0]["label"] == "WhatsApp"
    assert d["canais_contato"][0]["respondeu"] is True


def test_resumo_nega_pra_vendedor_que_nao_e_dono_do_lead(monkeypatch, pool):
    """O mesmo `_pode_ver` da ficha completa: vendedor comum só vê os PRÓPRIOS
    leads. Sem isso, o balão vazaria dados de lead alheio pra quem só devia ver
    os seus no funil."""
    lid = _lead(pool, vendedor_id=99)
    r = _resumo(monkeypatch, pool, lid, gerencia=False, membro_id=1)
    assert r.status_code == 403


def test_resumo_sem_login_devolve_401(monkeypatch, pool):
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_acesso", lambda req: (None, "redirecionar"))
    req = SimpleNamespace(session={}, query_params=QueryParams(""))
    r = pp.prospeccao_resumo(req, 1)
    assert r.status_code == 401


def test_resumo_de_lead_de_outra_conta_devolve_403(monkeypatch, pool):
    lid = _lead(pool)
    with pool.connection() as c:
        c.execute("update prospeccao set conta_id=999 where id=%s", (lid,))
        c.commit()
    r = _resumo(monkeypatch, pool, lid)
    assert r.status_code == 403


def test_ficha_completa_continua_de_pe_depois_do_refactor(monkeypatch, pool):
    """`_timeline_lead`/`_canais_contato_lead` foram extraídas de dentro da
    própria `prospeccao_ficha` (agora reaproveitadas pelo /resumo) — este teste
    garante que a ficha completa, que não tinha nenhuma cobertura de execução
    até aqui, continua renderizando com canal/atividade/origem corretos depois
    da extração."""
    lid = _lead(pool)
    _atividade(pool, lid, descricao="ligou e ficou de retornar")
    _conversa_com_msg(pool, lid, "whatsapp", respondeu=True)
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_acesso", lambda req: (
        {"conta_id": CONTA, "membro_id": 1, "gerencia": True, "pode_atribuir": True}, None))
    req = SimpleNamespace(session={}, query_params=QueryParams(""))
    r = pp.prospeccao_ficha(req, lid)
    assert r.status_code == 200
    html = bytes(r.body).decode("utf-8")
    assert "Padaria Bom Pão" in html
    assert "ligou e ficou de retornar" in html
    assert "Entrou por 💬 WhatsApp" in html, (
        "origem_ch sumiu — a extração do canais_contato quebrou esse cálculo")


def test_editar_rapido_grava_os_campos_do_balao(monkeypatch, pool):
    lid = _lead(pool)
    r = _editar_rapido(monkeypatch, pool, lid, contato="Marcos Silva", cargo="sócio",
                        telefone="86977776666", whatsapp="86988887777",
                        email="novo@bompao.com", instagram="@padariabompao",
                        site_url="bompao.com.br", valor="5000,00", obs="Prefere ligação")
    assert r.status_code == 200
    d = json.loads(bytes(r.body))
    assert d["ok"] is True
    with pool.connection() as c:
        row = c.execute(
            """select contato, cargo, telefone, whatsapp, email, instagram, site_url,
                      valor_estimado_centavos, obs
                 from prospeccao where id=%s""", (lid,)).fetchone()
    assert row == ("Marcos Silva", "sócio", "86977776666", "86988887777", "novo@bompao.com",
                    "@padariabompao", "https://bompao.com.br", 500000, "Prefere ligação"), (
        "site_url sem protocolo tem que ganhar https:// automaticamente, igual a /editar")


def test_editar_rapido_nao_mexe_em_documento_tipo_ou_segmento(monkeypatch, pool):
    """O pedido explícito: documento/tipo/segmento/cidade/uf/sócio/regime/porte
    ficam só na ficha completa — a edição rápida do balão NUNCA pode tocar
    nesses campos, mesmo que o form não os envie (a rota faz um UPDATE parcial,
    não o UPDATE de tudo-de-uma-vez que a /editar da ficha completa faz)."""
    lid = _lead(pool, cnpj="12345678000190", segmento="Varejo", cidade="Teresina", uf="PI")
    _editar_rapido(monkeypatch, pool, lid, contato="Novo Nome")
    with pool.connection() as c:
        row = c.execute(
            "select cnpj, tipo, segmento, cidade, uf from prospeccao where id=%s", (lid,)).fetchone()
    assert row == ("12345678000190", "pj", "Varejo", "Teresina", "PI")


def test_editar_rapido_nega_pra_vendedor_que_nao_e_dono_do_lead(monkeypatch, pool):
    lid = _lead(pool, vendedor_id=99)
    r = _editar_rapido(monkeypatch, pool, lid, gerencia=False, membro_id=1, contato="X")
    assert r.status_code == 403
    with pool.connection() as c:
        contato = c.execute("select contato from prospeccao where id=%s", (lid,)).fetchone()[0]
    assert contato != "X", "negou o acesso mas gravou o dado mesmo assim"


def test_editar_rapido_sem_login_devolve_401(monkeypatch, pool):
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_acesso", lambda req: (None, "redirecionar"))
    req = SimpleNamespace(session={}, query_params=QueryParams(""))
    r = pp.prospeccao_editar_rapido(req, 1, contato="", cargo="", telefone="", whatsapp="",
                                    email="", instagram="", site_url="", valor="", obs="")
    assert r.status_code == 401
