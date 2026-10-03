"""O romaneio da viagem e o "onde" no CD (finance/obra_romaneio.py; PR 3c do desenho
docs/mockups/obras_cd_almoxarifado.html, aprovado em 03/10/2026).

`test_tudo_ou_nada` é o contrato: a viagem sai inteira ou não sai — um pedido que
já saiu não deixa os outros saírem pela metade.
"""
import os
from datetime import date
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from db.conexao import init_schema
from finance import obra_conferencia as conf
from finance import obra_material as omat
from finance import obra_pedidos as op
from finance import obra_romaneio as rom
from finance import obras as ob
from web import painel_deposito as pd

_BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"
_MIGRACOES = ["016_unidade_medida.sql", "018_chave_nfce_lancamentos.sql", "019_codigo_gtin.sql",
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
              "550_obra_mestre_e_campo.sql", "670_obra_pedidos_cd.sql"]
# as migrações com prefixo de data (db/nova_migracao.py): acha pelo nome
for _nome in ("obra_ferramentas", "obra_conferencia_nota", "obra_inventario_rotativo", "obra_romaneio_e_onde"):
    _MIGRACOES += [p.name for p in sorted(_BASE.glob(f"*_{_nome}.sql"))]


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_obra_romaneio_test"
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


def _cd(pool, conta):
    """O CD com cimento e telha (uma nota de material sem obra) e duas casas."""
    with pool.connection() as c:
        lid = c.execute("""insert into lancamentos (conta_id, tipo, valor_centavos, categoria,
                               descricao, data, natureza)
                           values (%s,'despesa',100000,'Insumos','Constrular',%s,'empresa') returning id""",
                        (conta, date.today())).fetchone()[0]
        for desc, q, un in (("CIMENTO CP II 50KG", 100, "sc"), ("TELHA CERAMICA", 1000, "un")):
            c.execute("""insert into itens_lancamento (lancamento_id, descricao, quantidade,
                             valor_unitario_centavos, valor_total_centavos, unidade)
                         values (%s,%s,%s,3290,0,%s)""", (lid, desc, q, un))
        c.commit()
    omat.absorver_lancamento(pool, conta, lid)
    conf.conferir(pool, conta, lid)                   # bateu: a nota não fica esperando
    c4 = ob.criar_obra(pool, conta, "Casa 4", "casa")
    c1 = ob.criar_obra(pool, conta, "Casa 1", "casa")
    return c4["id"], c1["id"]


def _status(pool, conta, pid):
    with pool.connection() as c:
        return c.execute("select status, viagem_id from obra_pedidos where id=%s and conta_id=%s",
                         (pid, conta)).fetchone()


def _pid(pool, conta, nome):
    return omat.achar_produto(pool, conta, nome)["id"]


# ── a viagem ──────────────────────────────────────────────────────────────
def test_despachar_junta_os_pedidos_numa_viagem(pool, conta):
    c4, c1 = _cd(pool, conta)
    rom.salvar_onde(pool, conta, _pid(pool, conta, "telha"), "  Pátio  ")
    p1 = op.criar(pool, conta, c4, itens=[("telha", 400), ("cimento", 15)])["id"]
    p2 = op.criar(pool, conta, c1, itens=[("cimento", 6)])["id"]
    op.avancar(pool, conta, p2, "separando")
    r = rom.despachar(pool, conta, [p1, p2], motorista="  Nego ", fone="(86) 99999-8888")
    assert "Saiu com Nego" in r["frase"] and "Casa 4" in r["frase"] and "Casa 1" in r["frase"]
    assert _status(pool, conta, p1) == ("saiu", r["viagem_id"]) == _status(pool, conta, p2)
    assert omat.saldo_local(pool, conta, _pid(pool, conta, "cimento"), None) == 100   # o estoque não mexe
    vg = rom.viagens(pool, conta)[0]
    assert vg["motorista"] == "Nego" and [p["obra"] for p in vg["pedidos"]] == ["Casa 1", "Casa 4"]
    assert "*Casa 4*" in vg["texto"] and "(pegar em Pátio)" in vg["texto"]
    assert vg["link"].startswith("https://wa.me/5586999998888?text=")
    # o "recebi" do mestre aparece na viagem
    op.receber(pool, conta, p2)
    vg = rom.viagens(pool, conta)[0]
    assert [p["status"] for p in vg["pedidos"]] == ["recebido", "saiu"] and vg["recebidos"] == 1


def test_tudo_ou_nada(pool, conta):
    c4, c1 = _cd(pool, conta)
    p1 = op.criar(pool, conta, c4, itens=[("cimento", 5)])["id"]
    p2 = op.criar(pool, conta, c1, itens=[("cimento", 5)])["id"]
    rom.despachar(pool, conta, [p1])
    with pytest.raises(ValueError, match="Já saiu"):
        rom.despachar(pool, conta, [p1, p2], motorista="Nego")
    assert _status(pool, conta, p2) == ("pedido", None) and len(rom.viagens(pool, conta)) == 1
    with pytest.raises(ValueError, match="Marque"):
        rom.despachar(pool, conta, [])
    with pytest.raises(ValueError, match="incompleto"):
        rom.despachar(pool, conta, [p2], fone="9999")
    assert rom.viagens(pool, conta)[0]["link"].startswith("https://wa.me/?text=")   # sem fone: escolhe


def test_outra_conta_nao_despacha_nem_ve(pool, conta):
    c4, _ = _cd(pool, conta)
    p1 = op.criar(pool, conta, c4, itens=[("cimento", 5)])["id"]
    with pool.connection() as c:
        outra = c.execute("insert into contas (tipo, nome) values ('pj','Outra') returning id").fetchone()[0]
        c.commit()
    with pytest.raises(ValueError, match="não encontrado"):
        rom.despachar(pool, outra, [p1])
    vid = rom.despachar(pool, conta, [p1])["viagem_id"]
    assert rom.romaneio(pool, outra, vid) is None and rom.viagens(pool, outra) == []


# ── o onde ────────────────────────────────────────────────────────────────
def test_onde_limpa_e_ferramenta_nao_tem(pool, conta):
    _cd(pool, conta)
    cim = _pid(pool, conta, "cimento")
    rom.salvar_onde(pool, conta, cim, "  Baia   1 " + "x" * 60)
    assert rom.ondes(pool, conta)[cim] == ("Baia 1 " + "x" * 60)[:40]
    rom.salvar_onde(pool, conta, cim, "   ")
    assert cim not in rom.ondes(pool, conta)
    with pool.connection() as c:
        fer = c.execute("""insert into catalogo_produtos (fornecedor_id, nome, unidade, categoria, disponivel)
                           values (%s,'Betoneira','unidade','ferramenta',false) returning id""",
                        (conta,)).fetchone()[0]
        c.commit()
    rom.salvar_onde(pool, conta, fer, "Prateleira D1")
    assert fer not in rom.ondes(pool, conta)


# ── as devoluções ─────────────────────────────────────────────────────────
def test_devolucao_aparece_no_voltou_pro_cd(pool, conta):
    c4, _ = _cd(pool, conta)
    omat.mover(pool, conta, acao="levei", produto_id=_pid(pool, conta, "cimento"), quantidade=10, obra_id=c4)
    op.devolver(pool, conta, c4)
    d = rom.devolucoes(pool, conta)
    assert len(d) == 1 and d[0]["obra"] == "Casa 4" and "10 sacos" in d[0]["itens_txt"]


# ── a tela ────────────────────────────────────────────────────────────────
def _painel(pool, conta, monkeypatch, papel="almoxarife"):
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


def test_tela_monta_viagem_imprime_e_escapa(pool, conta, monkeypatch):
    c4, c1 = _cd(pool, conta)
    with pool.connection() as c:
        c.execute("update obras set nome=%s where id=%s and conta_id=%s", ("Casa <b>9</b>", c1, conta))
        c.commit()
    p1 = op.criar(pool, conta, c4, itens=[("telha", 400)])["id"]
    p2 = op.criar(pool, conta, c1, itens=[("cimento", 6)])["id"]
    c = _painel(pool, conta, monkeypatch)
    html = c.get("/painel/obras/deposito?aba=pedidos").text
    assert 'form="dp-viagem"' in html and "Montar viagem" in html
    # o "Saiu" de um pedido sozinho vira viagem de um
    r = c.post(f"/painel/obras/deposito/pedido/{p1}/saiu")
    assert "ok=" in r.headers["location"] and _status(pool, conta, p1)[1] is not None
    r = c.post("/painel/obras/deposito/viagem",
               data={"pedido": [str(p2)], "motorista": "Zé d'Ávila <script>x</script>", "fone": ""})
    assert "aba=saidas" in r.headers["location"] and "ok=" in r.headers["location"]
    html = c.get("/painel/obras/deposito?aba=saidas").text
    assert "Imprimir romaneio" in html and "Mandar pro motorista" in html
    assert "<script>x" not in html and "<b>9</b>" not in html and "Casa &lt;b&gt;9&lt;/b&gt;" in html
    vid = _status(pool, conta, p2)[1]
    papel = c.get(f"/painel/obras/deposito/viagem/{vid}/romaneio").text
    assert "Romaneio" in papel and "<script>x" not in papel and "Zé d&#39;Ávila" in papel
    assert "#" not in papel.split("<style>")[1].split("</style>")[0]       # o papel não tem cor
    r = c.get(f"/painel/obras/deposito/viagem/{vid + 999}/romaneio")
    assert "erro=" in r.headers["location"]


def test_tela_salva_o_onde_com_os_minimos(pool, conta, monkeypatch):
    _cd(pool, conta)
    cim, tel = _pid(pool, conta, "cimento"), _pid(pool, conta, "telha")
    c = _painel(pool, conta, monkeypatch)
    html = c.get("/painel/obras/deposito?aba=estoque").text
    assert 'name="onde"' in html and "Salvar mínimos e lugares" in html
    r = c.post("/painel/obras/deposito/minimo",
               data={"produto": [str(cim), str(tel)], "minimo": ["20", ""], "onde": ["Baia 1", "Pátio"]})
    assert "ok=" in r.headers["location"]
    assert rom.ondes(pool, conta) == {cim: "Baia 1", tel: "Pátio"}
    # formulário antigo (sem a coluna onde): só os mínimos, o lugar fica
    c.post("/painel/obras/deposito/minimo", data={"produto": [str(cim)], "minimo": ["25"]})
    assert rom.ondes(pool, conta)[cim] == "Baia 1"
    assert "📍 Baia 1" in c.get("/painel/obras/deposito?aba=inventario").text
