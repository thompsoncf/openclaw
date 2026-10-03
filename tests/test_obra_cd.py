"""O CD das obras (finance/obra_pedidos.py, web/painel_deposito.py, migração 670;
desenho docs/mockups/obras_cd_almoxarifado.html, aprovado em 03/10/2026).

`test_o_recebi_e_que_move_o_estoque` é o contrato da decisão 4 do dono: pedir,
separar e despachar não mexem no estoque — só o "recebi", e só o que chegou.
"""
import os
from datetime import date
from decimal import Decimal
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from contas import equipe as eq
from db.conexao import init_schema
from finance import obra_campo as oc
from finance import obra_material as omat
from finance import obra_pedidos as op
from finance import obras as ob
from web import app_obra as ao
from web import painel_deposito as pd

_MIGRACOES = ("016_unidade_medida.sql", "018_chave_nfce_lancamentos.sql", "019_codigo_gtin.sql",
              "032_catalogo_estoque.sql", "053_modulo_pj.sql", "057_natureza_lancamento.sql",
              "058_dados_empresa.sql", "060_unidade_generica.sql", "064_clientes_lojista.sql",
              "066_pessoas_identidade.sql", "067_titulos_cliente.sql", "072_membro_login_web.sql",
              "131_pessoa_cnpj.sql", "132_plano_contas_centros_custo.sql",
              "143_plano_contas_locacao_buffet_servicos.sql", "182_clientes_papel.sql",
              "186_plano_aporte_socios.sql", "195_titulo_aprovacao.sql",
              "196_titulo_recorrencia.sql", "197_titulo_acrescimo.sql",
              "317_titulo_classificacao.sql", "325_tipo_despesa.sql",
              "336_plano_fardamentos.sql", "349_plano_obras.sql", "351_obras.sql",
              "353_obra_venda_documentos.sql", "355_reforma_orcamento.sql",
              "365_obras_lembrete_segunda.sql", "367_pix_da_empresa.sql", "369_obra_fotos.sql",
              "371_obra_etapa_pagamentos.sql", "478_obra_quadras.sql", "484_obra_material.sql",
              "550_obra_mestre_e_campo.sql", "670_obra_pedidos_cd.sql")
_BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"
# as migrações com prefixo de data (db/nova_migracao.py): acha pelo nome — o
# "Saiu" do painel agora monta a viagem (obra_romaneio, PR 3c)
_MIGRACOES += tuple(p.name for _n in ("obra_ferramentas", "obra_conferencia_nota", "obra_inventario_rotativo",
                                      "obra_romaneio_e_onde") for p in sorted(_BASE.glob(f"*_{_n}.sql")))


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_obra_cd_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname} with (force)")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=4, open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    with p.connection() as c:
        c.execute("create table if not exists precos_observados (id bigserial primary key, item_id bigint)")
        c.commit()
    for m in _MIGRACOES:
        with p.connection() as c:
            c.execute((_BASE / m).read_text(encoding="utf-8"))
            c.commit()
    yield p
    p.close()


@pytest.fixture()
def conta(pool):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome, nome_fantasia) values "
                        "('pj', 'Pablo', 'PX2 Empreendimentos') returning id").fetchone()[0]
        c.commit()
    return cid


def _membro(pool, conta, papel="mestre", nome="Seu Zé"):
    with pool.connection() as c:
        mid = c.execute("insert into membros (conta_id, nome, papel, ativo) values (%s,%s,%s,true) "
                        "returning id", (conta, nome, papel)).fetchone()[0]
        c.commit()
    return mid


def _cd(pool, conta, nome="Cimento CP-II 50 kg", qtd=60, un="sc", preco=3290):
    """Põe material no CD pela NOTA (sem obra), com preço — como na vida real."""
    with pool.connection() as c:
        lid = c.execute("""insert into lancamentos (conta_id, tipo, valor_centavos, categoria,
                               descricao, data, natureza)
                           values (%s,'despesa',%s,'Insumos','Constrular',%s,'empresa') returning id""",
                        (conta, qtd * preco, date.today())).fetchone()[0]
        c.execute("""insert into itens_lancamento (lancamento_id, descricao, quantidade,
                         valor_unitario_centavos, valor_total_centavos, unidade)
                     values (%s,%s,%s,%s,%s,%s)""", (lid, nome, qtd, preco, qtd * preco, un))
        c.commit()
    omat.absorver_lancamento(pool, conta, lid)
    return omat.achar_produto(pool, conta, nome)


def _obra(pool, conta, nome="Casa 5", mestre=None):
    o = ob.criar_obra(pool, conta, nome, "casa")
    if mestre:
        oc.definir_mestre(pool, conta, o["id"], mestre)
    return ob.obter_obra(pool, conta, o["id"])


# ── o papel ───────────────────────────────────────────────────────────────
def test_almoxarife_so_tem_o_cd(monkeypatch):
    caps = eq.caps_do_papel("almoxarife")
    assert caps["deposito"] and not any(caps[k] for k in ("vendas", "financeiro", "gerir", "origens", "campo"))
    assert eq.home_do_papel("almoxarife", 3) == "/painel/obras/deposito"
    assert eq.destino_barrado("almoxarife") == "/painel/obras/deposito"
    rotas = eq.rotas_do_papel("almoxarife")
    assert "/painel/obras/deposito" in rotas and "/painel/obras" not in rotas
    assert not eq.caps_do_papel("mestre")["deposito"] and eq.caps_do_papel("financeiro")["deposito"]
    from web import painel_equipe as pe
    monkeypatch.setattr(pe, "nicho_da_conta", lambda c: "clinica")
    assert "almoxarife" not in pe._papeis_da_conta(("x",))
    monkeypatch.setattr(pe, "nicho_da_conta", lambda c: "construcao")
    assert "almoxarife" in pe._papeis_da_conta(("x",))


# ── o pedido e o "recebi" ─────────────────────────────────────────────────
def test_o_recebi_e_que_move_o_estoque(pool, conta):
    p = _cd(pool, conta)
    o = _obra(pool, conta)
    ped = op.criar(pool, conta, o["id"], itens=[("cimento", "20")], urgente=True, recado="laje")
    op.avancar(pool, conta, ped["id"], "separando")
    op.avancar(pool, conta, ped["id"], "saiu")
    assert omat.saldo_local(pool, conta, p["id"], None) == Decimal(60)       # nada se mexeu
    assert omat.saldo_local(pool, conta, p["id"], o["id"]) == Decimal(0)
    item = op.pedidos(pool, conta)[0]["itens"][0]
    r = op.receber(pool, conta, ped["id"], quantidades={item["id"]: "18"})
    assert "Faltou" in r["frase"] and "chegou 18 de 20" in r["faltou"][0]
    assert omat.saldo_local(pool, conta, p["id"], None) == Decimal(42)       # só o que chegou
    assert omat.saldo_local(pool, conta, p["id"], o["id"]) == Decimal(18)
    feito = op.pedidos(pool, conta)[0]
    assert feito["status"] == "recebido" and feito["faltou"]
    with pytest.raises(ValueError, match="Recebido"):
        op.receber(pool, conta, ped["id"])


def test_pedido_pergunta_o_material_e_cancela(pool, conta):
    _cd(pool, conta, "Cimento CP-II 50 kg")
    _cd(pool, conta, "Cimento branco 1 kg", 10, "un", 900)
    o = _obra(pool, conta)
    with pytest.raises(ValueError, match="Qual"):
        op.criar(pool, conta, o["id"], itens=[("cimento", 5)])               # ambíguo
    with pytest.raises(ValueError, match="Não conheço"):
        op.criar(pool, conta, o["id"], itens=[("granito", 5)])
    with pytest.raises(ValueError, match="maior que zero"):
        op.criar(pool, conta, o["id"], itens=[("cimento branco", 0)])
    ped = op.criar(pool, conta, o["id"], itens=[("cimento branco", 2), ("", "")])
    op.cancelar(pool, conta, ped["id"])
    assert not [x for x in op.pedidos(pool, conta) if x["id"] == ped["id"]]  # cancelado sai do quadro
    with pytest.raises(ValueError, match="fechado"):
        op.cancelar(pool, conta, ped["id"])


def test_sobra_da_casa_pronta_volta_pro_cd(pool, conta):
    p = _cd(pool, conta)
    o = _obra(pool, conta, "Casa Pronta")
    omat.mover(pool, conta, acao="levei", produto_id=p["id"], quantidade=10, obra_id=o["id"])
    assert op.sobras(pool, conta) == []                                     # não está pronta
    with pool.connection() as c:
        c.execute("update obras set status='pronta' where id=%s", (o["id"],))
        c.commit()
    s = op.sobras(pool, conta)
    assert s[0]["obra"] == "Casa Pronta" and "10 sacos de Cimento" in s[0]["itens"][0]["texto"]
    assert "Voltou pro CD" in op.devolver(pool, conta, o["id"])
    assert omat.saldo_local(pool, conta, p["id"], None) == Decimal(60) and op.sobras(pool, conta) == []


def test_visao_dinheiro_parado_e_cobertura(pool, conta):
    p = _cd(pool, conta, qtd=60, preco=3290)
    o = _obra(pool, conta)
    omat.mover(pool, conta, acao="levei", produto_id=p["id"], quantidade=28, obra_id=o["id"])
    omat.salvar_minimo(pool, conta, p["id"], 40)
    v = op.visao(pool, conta)
    linha = next(r for r in v["estoque"] if r["produto_id"] == p["id"])
    assert linha["valor"] == 32 * 3290 and v["dinheiro"] == 32 * 3290      # o que sobrou no CD × preço da nota
    assert linha["cobertura"] == 32                                         # sai 1 por dia → 32 dias
    assert v["abaixo"] and v["abaixo"][0]["produto_id"] == p["id"]


# ── a tela ────────────────────────────────────────────────────────────────
def _painel(pool, conta, monkeypatch, papel="dono"):
    monkeypatch.setattr(pd, "get_pool", lambda: pool)
    linha = [None] * 17
    linha[0] = conta
    monkeypatch.setattr(pd, "conta_logada", lambda request: tuple(linha))
    monkeypatch.setattr(pd, "nicho_da_conta", lambda c: "construcao")
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="t")
    app.include_router(pd.router)

    @app.post("/_entrar")
    async def _entrar(request: Request):
        request.session["papel"] = papel
        return {"ok": True}

    c = TestClient(app, follow_redirects=False)
    c.post("/_entrar", json={})
    return c


def test_tela_do_cd_e_o_quadro(pool, conta, monkeypatch):
    _cd(pool, conta)
    o = _obra(pool, conta, "Casa Tela")
    ped = op.criar(pool, conta, o["id"], itens=[("cimento", 70)], urgente=True)
    c = _painel(pool, conta, monkeypatch)
    for aba in ("geral", "pedidos", "estoque", "entradas", "sobras"):
        assert c.get(f"/painel/obras/deposito?aba={aba}").status_code == 200, aba
    geral = c.get("/painel/obras/deposito").text
    assert "Dinheiro parado" in geral and "Casa Tela" in geral
    quadro = c.get("/painel/obras/deposito?aba=pedidos").text
    assert "urgente" in quadro and "tem 60" in quadro                       # pediu 70, o CD tem 60
    r = c.post(f"/painel/obras/deposito/pedido/{ped['id']}/separar")
    assert "ok=" in r.headers["location"]
    c.post(f"/painel/obras/deposito/pedido/{ped['id']}/saiu")
    assert "esperando o" in c.get("/painel/obras/deposito?aba=pedidos").text


def test_almoxarife_nao_ve_dinheiro_e_vendedor_nem_entra(pool, conta, monkeypatch):
    _cd(pool, conta)
    html = _painel(pool, conta, monkeypatch, papel="almoxarife").get("/painel/obras/deposito?aba=estoque").text
    assert "Valor (nota)" not in html and "R$" not in html
    r = _painel(pool, conta, monkeypatch, papel="vendedor").get("/painel/obras/deposito")
    assert r.status_code == 303 and r.headers["location"] == eq.destino_barrado("vendedor")


# ── o app do mestre ───────────────────────────────────────────────────────
def _app(pool, conta, monkeypatch, membro_id):
    monkeypatch.setattr(ao, "get_pool", lambda: pool)
    linha = [None] * 17
    linha[0] = conta
    monkeypatch.setattr(ao, "conta_logada", lambda request: tuple(linha))
    monkeypatch.setattr(ao, "nicho_da_conta", lambda c: "construcao")
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="t")
    app.include_router(ao.router)

    @app.post("/_entrar")
    async def _entrar(request: Request):
        request.session["papel"] = "mestre"
        request.session["membro_id"] = membro_id
        request.session["conta_id"] = conta
        return {"ok": True}

    c = TestClient(app, follow_redirects=False)
    c.post("/_entrar", json={})
    return c


def test_mestre_pede_recebe_e_devolve_pelo_app(pool, conta, monkeypatch):
    p = _cd(pool, conta)
    ze = _membro(pool, conta)
    o = _obra(pool, conta, "Casa do App", mestre=ze)
    c = _app(pool, conta, monkeypatch, ze)
    pagina = c.get(f"/obra/{o['id']}").text
    assert "📦" in pagina and "Pedir ao CD" in pagina and "R$" not in pagina
    r = c.post(f"/obra/{o['id']}/pedido", data={"material": ["cimento", "", ""],
                                                 "quantidade": ["15", "", ""],
                                                 "prazo": "amanha", "urgente": "1", "recado": "laje"})
    assert "ok=" in r.headers["location"]
    ped = op.pedidos(pool, conta)[0]
    assert ped["urgente"] and ped["quem"] == "Seu Zé" and ped["prazo"]
    assert "Chegou do CD?" not in c.get(f"/obra/{o['id']}").text           # ainda não saiu
    op.avancar(pool, conta, ped["id"], "saiu")
    assert "Chegou do CD?" in c.get(f"/obra/{o['id']}").text
    assert "chegou do CD?" in c.get("/obra").text                            # o selo na lista
    r = c.post(f"/obra/{o['id']}/pedido/{ped['id']}/recebi",
               data={"item_id": [str(ped["itens"][0]["id"])], "qtd": ["15"]})
    assert "ok=" in r.headers["location"]
    assert omat.saldo_local(pool, conta, p["id"], o["id"]) == Decimal(15)
    with pool.connection() as cx:
        cx.execute("update obras set status='pronta' where id=%s", (o["id"],))
        cx.commit()
    assert "A casa ficou pronta" in c.get(f"/obra/{o['id']}").text
    c.post(f"/obra/{o['id']}/devolver")
    assert omat.saldo_local(pool, conta, p["id"], o["id"]) == Decimal(0)


def test_mestre_nao_recebe_pedido_de_outra_obra(pool, conta, monkeypatch):
    _cd(pool, conta)
    ze = _membro(pool, conta)
    minha = _obra(pool, conta, "Minha", mestre=ze)
    outra = _obra(pool, conta, "Outra")
    ped = op.criar(pool, conta, outra["id"], itens=[("cimento", 5)])
    c = _app(pool, conta, monkeypatch, ze)
    r = c.post(f"/obra/{minha['id']}/pedido/{ped['id']}/recebi", data={"item_id": [], "qtd": []})
    assert "erro=" in r.headers["location"]
    assert op.pedidos(pool, conta)[0]["status"] == "pedido"
