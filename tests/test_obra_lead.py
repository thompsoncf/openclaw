"""A reforma ligada ao lead que a vendeu (finance/obra_lead.py, migração 375).

Os testes que mais importam:

`test_o_card_so_anda_pra_frente` — o orçamento reenviado não pode puxar de volta
pra Proposta o lead que já fechou, nem ressuscitar quem foi perdido.

`test_rcb_espera_a_caixa_e_fecha_no_primeiro_recebimento` — no Reforma Casa
Brasil o aceite não é dinheiro: o card para em Crédito em análise.
"""
import os
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from db.conexao import init_schema
from finance import obra_lead as ol
from finance import obra_reforma as orf
from finance import obras as ob
from web import painel_obras as po

_MIGRACOES = ("018_chave_nfce_lancamentos.sql", "053_modulo_pj.sql",
              "057_natureza_lancamento.sql", "058_dados_empresa.sql",
              "064_clientes_lojista.sql",
              "066_pessoas_identidade.sql", "067_titulos_cliente.sql",
              "131_pessoa_cnpj.sql", "132_plano_contas_centros_custo.sql",
              "143_plano_contas_locacao_buffet_servicos.sql", "182_clientes_papel.sql",
              "186_plano_aporte_socios.sql", "195_titulo_aprovacao.sql",
              "196_titulo_recorrencia.sql", "197_titulo_acrescimo.sql",
              "317_titulo_classificacao.sql", "325_tipo_despesa.sql",
              "336_plano_fardamentos.sql", "349_plano_obras.sql", "351_obras.sql",
              "353_obra_venda_documentos.sql", "355_reforma_orcamento.sql",
              "367_pix_da_empresa.sql", "369_obra_fotos.sql", "371_obra_etapa_pagamentos.sql")
_BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"
_FUNIL = """
create table if not exists prospeccao (id bigserial primary key, conta_id bigint, empresa text,
  contato text, telefone text, whatsapp text, endereco text, status text default 'novo',
  estagio text default 'lead', atualizado_em timestamptz default now(),
  criado_em timestamptz default now());
create table if not exists funil_etapas (id bigserial primary key, conta_id bigint, chave text,
  rotulo text, ordem int default 0);
create table if not exists funil_movimentos (id bigserial primary key, conta_id bigint,
  prospeccao_id bigint, de text, para text, motivo text, membro_id bigint,
  criado_em timestamptz default now());
"""
ITENS = [{"servico": "Pintura", "tipo": "mao_de_obra", "unidade": "m2",
          "quantidade": 40, "valor_unit_centavos": 2_000}]


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_obra_lead_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname} with (force)")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=4, open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    for m in _MIGRACOES:
        with p.connection() as c:
            c.execute((_BASE / m).read_text(encoding="utf-8"))
            c.commit()
    with p.connection() as c:
        c.execute(_FUNIL)
        c.execute((_BASE / "375_obra_do_lead.sql").read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def conta(pool):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome, nome_fantasia) values "
                        "('pj', 'Pablo', 'PX2 Empreendimentos') returning id").fetchone()[0]
        for i, ch in enumerate(("novo", "contatado", "follow_up", "qualificado", "proposta",
                                "credito", "ganho", "perdido")):
            c.execute("insert into funil_etapas (conta_id, chave, rotulo, ordem) values (%s,%s,%s,%s)",
                      (cid, ch, ch, i * 10))
        c.commit()
    return cid


def _lead(pool, conta, status="qualificado", contato="Márcia Souza", whatsapp="(99) 98888-7777"):
    with pool.connection() as c:
        lid = c.execute("""insert into prospeccao (conta_id, empresa, contato, whatsapp, endereco, status)
                           values (%s,'Márcia',%s,%s,'Rua A, 10',%s) returning id""",
                        (conta, contato, whatsapp, status)).fetchone()[0]
        c.commit()
    return lid


def _status(pool, lid):
    with pool.connection() as c:
        return c.execute("select status from prospeccao where id=%s", (lid,)).fetchone()[0]


def _movimentos(pool, lid):
    with pool.connection() as c:
        return c.execute("select de, para, motivo from funil_movimentos where prospeccao_id=%s order by id",
                         (lid,)).fetchall()


def _orcamento(pool, conta, obra_id, modelo="etapas"):
    orc = orf.salvar_rascunho(pool, conta, obra_id, itens=ITENS, modelo_pagamento=modelo)
    return orf.enviar(pool, conta, orc["id"])


def test_abrir_a_reforma_do_lead_uma_vez(pool, conta):
    lid = _lead(pool, conta)
    o = ol.abrir_reforma(pool, conta, lid)
    assert o["nome"] == "Reforma Márcia Souza"
    assert ol.abrir_reforma(pool, conta, lid) == o
    obra = ob.obter_obra(pool, conta, o["id"])
    assert obra["tipo"] == "reforma" and obra["endereco"] == "Rua A, 10"
    assert ol.lead_da_obra(pool, conta, o["id"])["id"] == lid


def test_nome_repetido_ganha_numero(pool, conta):
    a = ol.abrir_reforma(pool, conta, _lead(pool, conta, contato="João"))
    b = ol.abrir_reforma(pool, conta, _lead(pool, conta, contato="João"))
    assert (a["nome"], b["nome"]) == ("Reforma João", "Reforma João (2)")


def test_lead_de_outra_conta_nao_abre(pool, conta):
    with pool.connection() as c:
        outra = c.execute("insert into contas (tipo, nome) values ('pj','Outra') returning id").fetchone()[0]
        c.commit()
    with pytest.raises(ValueError):
        ol.abrir_reforma(pool, outra, _lead(pool, conta))


def test_enviado_vai_pra_proposta_e_aceito_pra_fechado(pool, conta):
    lid = _lead(pool, conta)
    o = ol.abrir_reforma(pool, conta, lid)
    env = _orcamento(pool, conta, o["id"])
    assert _status(pool, lid) == "proposta"
    assert orf.aceitar(pool, env["token"], "Márcia", "", "")
    assert _status(pool, lid) == "ganho"
    assert [m[2] for m in _movimentos(pool, lid)] == ["orcamento_enviado", "orcamento_aceito"]


def test_rcb_espera_a_caixa_e_fecha_no_primeiro_recebimento(pool, conta):
    lid = _lead(pool, conta)
    o = ol.abrir_reforma(pool, conta, lid)
    env = _orcamento(pool, conta, o["id"], modelo="rcb")
    orf.aceitar(pool, env["token"], "Márcia", "", "")
    assert _status(pool, lid) == "credito"
    assert ol.parcela_recebida(pool, conta, o["id"]) and _status(pool, lid) == "ganho"
    assert not ol.parcela_recebida(pool, conta, o["id"])


def test_o_card_so_anda_pra_frente(pool, conta):
    lid = _lead(pool, conta, status="ganho")
    o = ol.abrir_reforma(pool, conta, lid)
    _orcamento(pool, conta, o["id"])
    assert _status(pool, lid) == "ganho"
    perdido = _lead(pool, conta, status="perdido", contato="Zé")
    o2 = ol.abrir_reforma(pool, conta, perdido)
    _orcamento(pool, conta, o2["id"])
    assert _status(pool, perdido) == "perdido" and _movimentos(pool, perdido) == []


def test_recusado_nao_mexe_no_card(pool, conta):
    lid = _lead(pool, conta)
    o = ol.abrir_reforma(pool, conta, lid)
    env = _orcamento(pool, conta, o["id"])
    orf.recusar(pool, env["token"])
    assert _status(pool, lid) == "proposta"


def test_obra_sem_lead_segue_como_antes(pool, conta):
    o = ob.criar_obra(pool, conta, "Reforma avulsa", "reforma")
    env = _orcamento(pool, conta, o["id"])
    assert orf.aceitar(pool, env["token"], "Fulano", "", "")
    assert ol.lead_da_obra(pool, conta, o["id"]) is None


def test_a_cobranca_abre_no_whatsapp_do_cliente(pool, conta):
    lid = _lead(pool, conta)
    o = ol.abrir_reforma(pool, conta, lid)
    env = _orcamento(pool, conta, o["id"])
    orf.aceitar(pool, env["token"], "Márcia", "", "")
    v = orf.orcamentos(pool, conta, o["id"])[0]
    meio = [p for p in v["parcelas"] if p.get("etapa")][0]
    obra = ob.obter_obra(pool, conta, o["id"])
    ob.marcar_etapa(pool, conta, o["id"], next(e["id"] for e in obra["etapas"] if e["chave"] == meio["etapa"]))
    [cb] = orf.cobrancas(pool, conta, ob.obter_obra(pool, conta, o["id"]))
    assert cb["wa_url"].startswith("https://wa.me/5599988887777?text=Ol%C3%A1")


@pytest.mark.parametrize("tel,esperado", [("(99) 98888-7777", "5599988887777"),
                                          ("+55 99 3222-1111", "559932221111"),
                                          ("123", ""), ("", "")])
def test_wa_numero(tel, esperado):
    assert ol.wa_numero(tel) == esperado


def test_a_ficha_da_obra_mostra_o_lead(pool, conta, monkeypatch):
    lid = _lead(pool, conta)
    o = ol.abrir_reforma(pool, conta, lid)
    monkeypatch.setattr(po, "get_pool", lambda: pool)
    linha = [None] * 17
    linha[0] = conta
    monkeypatch.setattr(po, "conta_logada", lambda request: tuple(linha))
    monkeypatch.setattr(po, "nicho_da_conta", lambda c: "construcao")
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="t")
    app.include_router(po.router)

    @app.post("/_entrar")
    async def _entrar(request: Request):
        request.session["papel"] = "dono"
        return {"ok": True}

    c = TestClient(app, follow_redirects=False)
    c.post("/_entrar", json={})
    assert f'lead: <a href="/painel/prospeccao/{lid}">Márcia Souza</a>' in c.get(f"/painel/obras/{o['id']}").text


def test_o_botao_da_ficha_do_lead_compila():
    from web import painel_prospeccao as pp
    fonte = pp._FICHA_TPL
    assert "/painel/prospeccao/{{ a.id }}/reforma" in fonte and "reforma.liberada" in fonte
    pp._env.get_template("prospeccao_ficha")
