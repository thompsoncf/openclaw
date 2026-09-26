"""As fotos da obra, por etapa (finance/obra_fotos.py, migração 369).

Os testes que mais importam:

`test_a_foto_nao_abre_de_outra_conta_nem_de_outra_obra` — é a casa de um cliente.
O id na URL não pode abrir foto de outra empresa.

`test_o_cliente_so_ve_depois_do_aceite` — o link do orçamento circula antes de
fechar; a foto da casa, não.

`test_a_foto_do_whatsapp_vai_pra_obra_e_nao_entra_duas_vezes` — o webhook deixa a
imagem da mensagem no livro; a ferramenta guarda uma vez só.
"""
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from db.conexao import init_schema
from finance import comprovantes
from finance import obra_fotos as of
from finance import obra_reforma as orf
from finance import obras as ob
from finance import tools_pj
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
              "367_pix_da_empresa.sql", "369_obra_fotos.sql")
_BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"
JPG = b"\xff\xd8\xff\xe0" + b"foto-do-telhado" * 10


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_obra_fotos_test"
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
    yield p
    p.close()


@pytest.fixture()
def conta(pool):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome, nome_fantasia) values "
                        "('pj', 'Pablo', 'PX2 Empreendimentos') returning id").fetchone()[0]
        c.commit()
    return cid


@pytest.fixture()
def bucket(monkeypatch):
    """O bucket privado, em memória: sem rede e sem Supabase."""
    guardado = {}
    monkeypatch.setattr(comprovantes, "subir_em",
                        lambda caminho, dados, ct: guardado.__setitem__(caminho, (dados, ct)) or caminho)
    monkeypatch.setattr(comprovantes, "ler", lambda caminho: guardado[caminho]
                        if caminho in guardado else (_ for _ in ()).throw(ValueError("não achei")))
    monkeypatch.setattr(comprovantes, "apagar", lambda caminho: guardado.pop(caminho, None))
    monkeypatch.setattr(comprovantes, "configurado", lambda: True)
    return guardado


def _casa(pool, conta, nome="Casa 3"):
    return ob.obter_obra(pool, conta, ob.criar_obra(pool, conta, nome, "casa")["id"])


# ── guardar ───────────────────────────────────────────────────────────────
def test_guarda_na_etapa_pelo_jeito_que_a_pessoa_fala(pool, conta, bucket):
    casa = _casa(pool, conta)
    r = of.guardar(pool, conta, casa["id"], JPG, "image/jpeg", etapa="o telhado")
    assert r["etapa"] == "Cobertura" and r["caminho"] in bucket
    assert r["caminho"].startswith(f"obras/{conta}/{casa['id']}/")
    [f] = of.listar(pool, conta, casa["id"])
    assert f["etapa_chave"] == "cobertura" and f["bytes"] == len(JPG)


def test_etapa_que_a_obra_nao_tem_explica(pool, conta, bucket):
    casa = _casa(pool, conta)
    with pytest.raises(ValueError, match="não tem a etapa"):
        of.guardar(pool, conta, casa["id"], JPG, "image/jpeg", etapa="piscina")
    assert not bucket


@pytest.mark.parametrize("dados,ct,erro", [
    (b"", "image/jpeg", "vazia"), (JPG, "application/pdf", "Aceito foto"),
    (b"x" * (of.MAX_BYTES + 1), "image/png", "grande")], ids=["vazia", "pdf", "grande"])
def test_so_foto_e_de_tamanho_razoavel(pool, conta, bucket, dados, ct, erro):
    casa = _casa(pool, conta)
    with pytest.raises(ValueError, match=erro):
        of.guardar(pool, conta, casa["id"], dados, ct)


def test_por_etapa_na_ordem_da_obra_e_as_soltas_no_fim(pool, conta, bucket):
    casa = _casa(pool, conta)
    of.guardar(pool, conta, casa["id"], JPG, "image/jpeg", etapa="pintura")
    of.guardar(pool, conta, casa["id"], JPG, "image/jpeg")
    of.guardar(pool, conta, casa["id"], JPG, "image/jpeg", etapa="alicerce")
    grupos = of.por_etapa(casa, of.listar(pool, conta, casa["id"]))
    assert [g["nome"] for g in grupos] == ["Preliminares e fundação", "Pintura", "Sem etapa"]


def test_a_foto_nao_abre_de_outra_conta_nem_de_outra_obra(pool, conta, bucket):
    casa = _casa(pool, conta)
    outra_obra = _casa(pool, conta, "Casa 4")
    r = of.guardar(pool, conta, casa["id"], JPG, "image/jpeg")
    with pool.connection() as c:
        outra_conta = c.execute("insert into contas (tipo, nome) values ('pj','Outra') returning id"
                                ).fetchone()[0]
        c.commit()
    assert of.obter(pool, conta, casa["id"], r["id"])
    assert of.obter(pool, conta, outra_obra["id"], r["id"]) is None
    assert of.obter(pool, outra_conta, casa["id"], r["id"]) is None
    assert not of.apagar(pool, outra_conta, casa["id"], r["id"])
    assert of.apagar(pool, conta, casa["id"], r["id"]) and r["caminho"] not in bucket


# ── pelo WhatsApp ─────────────────────────────────────────────────────────
def _ferramentas(pool, conta, monkeypatch, livro):
    monkeypatch.setattr(tools_pj, "_nicho_da_conta", lambda p, c: "construcao")
    return {f.nome: f for f in tools_pj.construir_ferramentas_pj(pool, conta, None, livro=livro)}


def test_a_foto_do_whatsapp_vai_pra_obra_e_nao_entra_duas_vezes(pool, conta, bucket, monkeypatch):
    casa = _casa(pool, conta)
    livro = SimpleNamespace(midia_atual=(JPG, "image/jpeg"))
    f = _ferramentas(pool, conta, monkeypatch, livro)
    txt = f["guardar_foto_da_obra"].executar({"obra": casa["nome"], "etapa": "telhado"})
    assert "Foto guardada na Casa 3, na etapa cobertura (1 foto" in txt
    assert livro.midia_atual is None
    assert "Não chegou foto" in f["guardar_foto_da_obra"].executar({"obra": casa["nome"]})
    [foto] = of.listar(pool, conta, casa["id"])
    assert foto["origem"] == "whatsapp"


def test_sem_foto_na_mensagem_pede_de_novo(pool, conta, bucket, monkeypatch):
    casa = _casa(pool, conta)
    f = _ferramentas(pool, conta, monkeypatch, SimpleNamespace(midia_atual=None))
    assert "Não chegou foto" in f["guardar_foto_da_obra"].executar({"obra": casa["nome"]})


def test_o_prompt_separa_foto_de_obra_de_nota(pool, conta):
    _casa(pool, conta)
    assert "FOTO QUE NÃO É NOTA" in ob.bloco_persona(pool, conta)


# ── o painel e o cliente ──────────────────────────────────────────────────
def _painel(pool, conta, monkeypatch):
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
    return c


def test_mandar_ver_e_apagar_pelo_painel(pool, conta, bucket, monkeypatch):
    casa = _casa(pool, conta)
    c = _painel(pool, conta, monkeypatch)
    assert "Nenhuma foto ainda" in c.get(f"/painel/obras/{casa['id']}").text
    r = c.post(f"/painel/obras/{casa['id']}/fotos", data={"etapa": "alvenaria", "legenda": "parede"},
               files={"foto": ("parede.jpg", JPG, "image/jpeg")})
    assert r.status_code == 303 and "erro" not in r.headers["location"]
    [f] = of.listar(pool, conta, casa["id"])
    html = c.get(f"/painel/obras/{casa['id']}").text
    assert f"/painel/obras/{casa['id']}/foto/{f['id']}" in html and "Fotos da obra · 1" in html
    img = c.get(f"/painel/obras/{casa['id']}/foto/{f['id']}")
    assert img.status_code == 200 and img.content == JPG and "private" in img.headers["cache-control"]
    assert c.get(f"/painel/obras/{casa['id']}/foto/{f['id'] + 999}").status_code == 404
    r = c.post(f"/painel/obras/{casa['id']}/fotos", data={},
               files={"foto": ("nota.pdf", b"%PDF", "application/pdf")})
    assert "erro=" in r.headers["location"]
    c.post(f"/painel/obras/{casa['id']}/foto/{f['id']}/apagar")
    assert of.listar(pool, conta, casa["id"]) == []


def test_o_cliente_so_ve_depois_do_aceite(pool, conta, bucket, monkeypatch):
    o = ob.criar_obra(pool, conta, "Reforma Dona Márcia", "reforma")
    itens = [{"servico": "Pintura", "tipo": "mao_de_obra", "unidade": "m2",
              "quantidade": 40, "valor_unit_centavos": 2_000}]
    orc = orf.salvar_rascunho(pool, conta, o["id"], itens=itens, modelo_pagamento="rcb")
    env = orf.enviar(pool, conta, orc["id"])
    f = of.guardar(pool, conta, o["id"], JPG, "image/jpeg", etapa="pintura")
    c = _painel(pool, conta, monkeypatch)
    url = f"/orcamento-obra/{env['token']}/foto/{f['id']}"
    assert c.get(url).status_code == 404
    assert "Fotos da obra" not in c.get(f"/orcamento-obra/{env['token']}").text
    assert orf.aceitar(pool, env["token"], "Márcia", "", "")
    html = c.get(f"/orcamento-obra/{env['token']}").text
    assert "Fotos da obra" in html and "10% finais" in html and url in html
    assert c.get(url).content == JPG
    outra = _casa(pool, conta, "Casa 9")
    g = of.guardar(pool, conta, outra["id"], JPG, "image/jpeg")
    assert c.get(f"/orcamento-obra/{env['token']}/foto/{g['id']}").status_code == 404
