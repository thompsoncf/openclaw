"""O mapa das obras (finance/obra_mapas.py, web/painel_obras_mapa.py; desenho
docs/mockups/obras_mapa_3d.html, migração 482).

`test_rotas_do_mapa_entram_antes_da_ficha` é o que impede a regressão boba que
derruba a tela inteira: /painel/obras/mapa batendo na ficha /painel/obras/{id}
e morrendo no 422 do int.
"""
import io
import json
import os
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from db.conexao import init_schema
from finance import obra_mapas as om
from finance import obras as ob
from web import painel_obras as po
from web import painel_obras_mapa as pom

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
              "365_obras_lembrete_segunda.sql",
              "367_pix_da_empresa.sql", "369_obra_fotos.sql", "371_obra_etapa_pagamentos.sql",
              "478_obra_quadras.sql", "482_obra_mapas.sql")
_BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_obra_mapas_test"
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


def _png(w=400, h=300) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (200, 220, 210)).save(buf, "PNG")
    return buf.getvalue()


def _pdf(w=200, h=100) -> bytes:
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (w, h), (255, 255, 255)).save(buf, "PDF")
    return buf.getvalue()


def _lote(rotulo, x, y, larg=60, alt=100, **kw):
    d = {"rotulo": rotulo, "x": x, "y": y, "larg": larg, "alt": alt, "situacao": "meu"}
    d.update(kw)
    return d


# ── a área e os riscos ────────────────────────────────────────────────────
def test_criar_area_e_nome_duplicado(pool, conta):
    m = om.criar(pool, conta, "Santa Marina 2", "Paço do Lumiar")
    with pytest.raises(ValueError, match="existe"):
        om.criar(pool, conta, "santa marina 2")
    areas = om.listar(pool, conta)
    assert [a["nome"] for a in areas if a["id"] == m["id"]] == ["Santa Marina 2"]
    assert areas[0]["n_lotes"] == 0 and not areas[0]["tem_planta"]


def test_salvar_lotes_upserta_e_apaga_o_que_saiu(pool, conta):
    m = om.criar(pool, conta, "Área A")
    om.salvar_lotes(pool, conta, m["id"], [_lote("1", 0, 0), _lote("2", 70, 0), _lote("3", 140, 0)])
    antes = om.lotes(pool, conta, m["id"])
    assert [l["rotulo"] for l in antes] == ["1", "2", "3"]
    # o 1 muda de lugar (mesmo id), o 3 some, e o 4 nasce
    novo = [dict(antes[0], x=500, situacao="vago"), antes[1], _lote("4", 210, 0)]
    om.salvar_lotes(pool, conta, m["id"], novo)
    depois = om.lotes(pool, conta, m["id"])
    assert {l["rotulo"] for l in depois} == {"1", "2", "4"}
    l1 = next(l for l in depois if l["rotulo"] == "1")
    assert l1["id"] == antes[0]["id"] and l1["x"] == 500 and l1["situacao"] == "vago"
    # sem planta, o chão cresce pra caber o desenho
    assert om.obter(pool, conta, m["id"])["altura"] == 300


def test_fileira_apara_a_borda_e_valida(pool, conta):
    m = om.criar(pool, conta, "Área B")
    # o último lote da fileira passa 10 da borda pelo arredondamento: é aparado
    om.salvar_lotes(pool, conta, m["id"], [_lote("7", 940, 0, larg=70)])
    assert om.lotes(pool, conta, m["id"])[0]["larg"] == 60
    with pytest.raises(ValueError, match="fora da planta"):
        om.salvar_lotes(pool, conta, m["id"], [_lote("x", 1200, 0)])
    with pytest.raises(ValueError, match="pequeno"):
        om.salvar_lotes(pool, conta, m["id"], [_lote("x", 0, 0, larg=4)])
    with pytest.raises(ValueError, match="Situação"):
        om.salvar_lotes(pool, conta, m["id"], [_lote("x", 0, 0, situacao="meio")])


def test_obra_so_entra_num_lote_e_da_conta_certa(pool, conta):
    m = om.criar(pool, conta, "Área C")
    o = ob.criar_obra(pool, conta, "Casa C1", "casa")
    with pytest.raises(ValueError, match="dois lotes"):
        om.salvar_lotes(pool, conta, m["id"],
                        [_lote("1", 0, 0, obra_id=o["id"]), _lote("2", 70, 0, obra_id=o["id"])])
    with pool.connection() as c:
        outra = c.execute("insert into contas (tipo, nome) values ('pj', 'Outra') "
                          "returning id").fetchone()[0]
        c.commit()
    alheia = ob.criar_obra(pool, outra, "Casa alheia", "casa")
    with pytest.raises(ValueError, match="não é desta conta"):
        om.salvar_lotes(pool, conta, m["id"], [_lote("1", 0, 0, obra_id=alheia["id"])])
    om.salvar_lotes(pool, conta, m["id"], [_lote("1", 0, 0, obra_id=o["id"])])
    m2 = om.criar(pool, conta, "Área C2")
    with pytest.raises(ValueError, match="outra área"):
        om.salvar_lotes(pool, conta, m2["id"], [_lote("1", 0, 0, obra_id=o["id"])])


# ── a planta ──────────────────────────────────────────────────────────────
def test_planta_imagem_guarda_caminho_e_proporcao(pool, conta):
    m = om.criar(pool, conta, "Área D")
    subidas = {}
    r = om.guardar_planta(pool, conta, m["id"], _png(400, 300), "image/png",
                          subir=lambda cam, dados, ct: subidas.setdefault(cam, (len(dados), ct)),
                          remover=lambda cam: None)
    assert r["altura"] == 750 and r["caminho"].startswith(f"mapas/{conta}/")
    assert list(subidas.values())[0][1] == "image/png"
    d = om.obter(pool, conta, m["id"])
    assert d["tem_planta"] and d["altura"] == 750
    with pytest.raises(ValueError, match="PDF ou imagem"):
        om.guardar_planta(pool, conta, m["id"], b"x", "text/plain", subir=lambda *a: None)


def test_planta_pdf_vira_png(pool, conta):
    m = om.criar(pool, conta, "Área E")
    subidas = {}
    r = om.guardar_planta(pool, conta, m["id"], _pdf(200, 100), "application/pdf",
                          subir=lambda cam, dados, ct: subidas.setdefault(cam, (dados[:8], ct)))
    assert r["altura"] == 500 and r["caminho"].endswith(".png")
    cabeca, ct = list(subidas.values())[0]
    assert ct == "image/png" and cabeca.startswith(b"\x89PNG")
    with pytest.raises(ValueError, match="PDF"):
        om.guardar_planta(pool, conta, m["id"], b"nao e pdf", "application/pdf",
                          subir=lambda *a: None)


# ── a casa por camadas e a vista ──────────────────────────────────────────
def test_casa_pecas_pela_etapa_e_pelo_pct(pool, conta):
    o = ob.criar_obra(pool, conta, "Casa F", "casa")
    ob.salvar_etapas(pool, conta, o["id"], [(None, "Fundação", 40), (None, "Estrutura", 30),
                                            (None, "Pintura", 30)])
    o = ob.obter_obra(pool, conta, o["id"])
    ob.marcar_etapa(pool, conta, o["id"], o["etapas"][0]["id"], concluida=True)
    o = ob.obter_obra(pool, conta, o["id"])          # 40%
    p = om.casa_pecas(o)
    # fundação pela etapa com o nome; estrutura tem etapa e NÃO está feita;
    # paredes não têm etapa — cai no % acumulado (40 >= 32)
    assert p["fund"] and not p["pil"] and p["par"] and not p["telh"] and not p["pint"]


def test_vista_resolve_obra_vago_e_terceiro(pool, conta):
    m = om.criar(pool, conta, "Área G")
    o = ob.criar_obra(pool, conta, "Casa G1", "casa")
    ob.salvar_etapas(pool, conta, o["id"], [(None, "Fundação", 40), (None, "Cobertura", 60)])
    o = ob.obter_obra(pool, conta, o["id"])
    ob.marcar_etapa(pool, conta, o["id"], o["etapas"][0]["id"], concluida=True)
    om.salvar_lotes(pool, conta, m["id"], [
        _lote("1", 0, 0, obra_id=o["id"]), _lote("2", 70, 0, situacao="vago"),
        _lote("3", 140, 0, situacao="terceiro")])
    v = om.vista(pool, conta, m["id"])
    casa, vago, terc = v["lotes"]
    assert casa["st"] == "f2" and casa["pct"] == 40 and casa["obra_nome"] == "Casa G1"
    assert casa["casa"]["fund"] and not casa["casa"]["telh"]
    assert casa["n_etapas"] == "1 de 2 etapas" and casa["etapas"][0] == ["Fundação", True]
    assert vago["st"] == "vago" and "obra_nome" not in vago
    assert terc["st"] == "terceiro"


# ── o painel ──────────────────────────────────────────────────────────────
def _painel(pool, conta, monkeypatch):
    for mod in (po, pom):
        monkeypatch.setattr(mod, "get_pool", lambda: pool)
    linha = [None] * 17
    linha[0] = conta
    monkeypatch.setattr(po, "conta_logada", lambda request: tuple(linha))
    monkeypatch.setattr(po, "nicho_da_conta", lambda c: "construcao")
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="t")
    app.include_router(pom.router)       # ANTES, como no web/app.py
    app.include_router(po.router)

    @app.post("/_entrar")
    async def _entrar(request: Request):
        request.session["papel"] = "dono"
        return {"ok": True}

    c = TestClient(app, follow_redirects=False)
    c.post("/_entrar", json={})
    return c


def test_rotas_do_mapa_entram_antes_da_ficha(pool, conta, monkeypatch):
    c = _painel(pool, conta, monkeypatch)
    r = c.get("/painel/obras/mapa")
    assert r.status_code == 200 and "Crie a primeira área" in r.text
    assert "Mapa das obras" in c.get("/painel/obras").text


def test_painel_cria_risca_e_mostra(pool, conta, monkeypatch):
    c = _painel(pool, conta, monkeypatch)
    r = c.post("/painel/obras/mapa/nova", data={"nome": "Santa Marina", "cidade": "Paço"})
    mid = om.listar(pool, conta)[0]["id"]
    assert r.headers["location"] == f"/painel/obras/mapa/{mid}/editar"
    ed = c.get(f"/painel/obras/mapa/{mid}/editar").text
    assert "Riscar os lotes" in ed and "fileira" in ed.lower()
    o = ob.criar_obra(pool, conta, "Casa SM 1", "casa")
    dados = json.dumps([_lote("1", 0, 0, obra_id=o["id"]), _lote("2", 70, 0, situacao="terceiro")])
    r = c.post(f"/painel/obras/mapa/{mid}/lotes", data={"dados": dados})
    assert r.headers["location"] == f"/painel/obras/mapa?m={mid}"
    html = c.get(f"/painel/obras/mapa?m={mid}").text
    assert "Casa SM 1" in html and '"situacao": "terceiro"' in html and "casaHtml" in html
    # o rótulo com </script> não escapa do JSON (o < vira \u003c)
    c.post(f"/painel/obras/mapa/{mid}/lotes",
           data={"dados": json.dumps([_lote("</script>", 0, 0)])})
    html = c.get(f"/painel/obras/mapa?m={mid}").text
    assert "\\u003c/script>" in html and '"rotulo": "</script>"' not in html


def test_painel_planta_sobe_e_sai_pela_rota(pool, conta, monkeypatch):
    from finance import comprovantes
    guardado = {}

    def _subir(caminho, dados, ct):
        guardado[caminho] = (dados, ct)
        return caminho

    monkeypatch.setattr(comprovantes, "subir_em", _subir)
    monkeypatch.setattr(comprovantes, "ler", lambda cam: guardado[cam])
    c = _painel(pool, conta, monkeypatch)
    m = om.criar(pool, conta, "Área H")
    r = c.post(f"/painel/obras/mapa/{m['id']}/planta",
               files={"planta": ("planta.png", _png(500, 250), "image/png")})
    assert "erro" not in r.headers["location"]
    r = c.get(f"/painel/obras/mapa/{m['id']}/planta")
    assert r.status_code == 200 and r.headers["content-type"].startswith("image/png")
    assert 'planta-img' in c.get(f"/painel/obras/mapa?m={m['id']}").text


def test_outra_conta_nao_ve_o_mapa(pool, conta, monkeypatch):
    m = om.criar(pool, conta, "Área I")
    om.salvar_lotes(pool, conta, m["id"], [_lote("1", 0, 0)])
    with pool.connection() as c:
        outra = c.execute("insert into contas (tipo, nome) values ('pj', 'Vizinha') "
                          "returning id").fetchone()[0]
        c.commit()
    cli = _painel(pool, outra, monkeypatch)
    assert "Área I" not in cli.get(f"/painel/obras/mapa?m={m['id']}").text
    assert cli.get(f"/painel/obras/mapa/{m['id']}/planta").status_code == 404
    r = cli.get(f"/painel/obras/mapa/{m['id']}/editar")
    assert r.status_code == 303 and r.headers["location"] == "/painel/obras/mapa"
    r = cli.post(f"/painel/obras/mapa/{m['id']}/lotes", data={"dados": "[]"})
    assert "erro=" in r.headers["location"]
    assert om.lotes(pool, conta, m["id"])          # o desenho do dono continua lá


def test_alerta_vira_selo_e_a_cor_continua_sendo_o_andamento(pool, conta):
    # obra começada há 40 dias sem CNO: tem alerta de documento (obra_venda)
    from datetime import date, timedelta
    m = om.criar(pool, conta, "Área Selo")
    o = ob.criar_obra(pool, conta, "Casa Selo", "casa")
    ob.salvar_etapas(pool, conta, o["id"], [(None, "Fundação", 40), (None, "Cobertura", 60)])
    o = ob.obter_obra(pool, conta, o["id"])
    ob.marcar_etapa(pool, conta, o["id"], o["etapas"][0]["id"], concluida=True)
    with pool.connection() as c:
        c.execute("update obras set inicio_em=%s where id=%s", (date.today() - timedelta(days=40), o["id"]))
        c.commit()
    om.salvar_lotes(pool, conta, m["id"], [_lote("1", 0, 0, obra_id=o["id"])])
    lote = om.vista(pool, conta, m["id"])["lotes"][0]
    assert lote["alerta"]                       # o alerta continua lá, pro selo
    assert lote["st"] == "f2"                   # e a cor é a do andamento (40%), não âmbar


# ── a aba (pedido do dono em 02/10: "uma aba nova com tudo") ────────────────
def test_exemplo_mostra_tudo_e_nao_grava_nada(pool, conta, monkeypatch):
    def _contagem():
        with pool.connection() as c:
            return [c.execute(f"select count(*) from {t}").fetchone()[0]
                    for t in ("obras", "obra_mapas", "obra_mapa_lotes", "lancamentos")]
    antes = _contagem()
    c = _painel(pool, conta, monkeypatch)
    vazio = c.get("/painel/obras/mapa").text
    assert "Ver um exemplo pronto" in vazio
    html = c.get("/painel/obras/mapa?exemplo=1").text
    assert "Residencial Exemplo" in html and "Nada aqui é seu" in html
    assert "Casas no mapa" in html and "Precisa de atenção" in html
    assert "Depósito abaixo do mínimo" in html and "Argamassa" in html
    assert "Riscar os lotes" not in html          # o exemplo não se edita
    assert _contagem() == antes                   # e nada foi gravado


def test_vista_exemplo_tem_o_formato_da_vista_real():
    v = om.vista_exemplo()
    casas = [l for l in v["lotes"] if l.get("obra_id")]
    assert v["resumo"]["casas"] == len(casas) == 17
    assert v["resumo"]["prontas"] == 4 and v["resumo"]["alerta_obra"] == 2
    assert v["resumo"]["alerta_mat"] == 2
    l3 = next(l for l in casas if l["rotulo"] == "3")
    assert l3["casa"]["telh"] and not l3["casa"]["pint"]       # 68%: telhado sim, pintura não
    assert l3["mat_linhas"][0][0].startswith("Cimento") and "irmãs" in l3["mat_alerta"]
    assert {l["st"] for l in v["lotes"]} >= {"pronta", "f3", "f2", "f1", "f0", "vago", "terceiro"}


def test_resumo_e_perfil_completo_na_area_real(pool, conta, monkeypatch):
    m = om.criar(pool, conta, "Área Aba")
    a = ob.criar_obra(pool, conta, "Casa Aba 1", "casa")
    b = ob.criar_obra(pool, conta, "Casa Aba 2", "casa")
    for o, feitas in ((a, 2), (b, 0)):
        ob.salvar_etapas(pool, conta, o["id"], [(None, "Fundação", 50), (None, "Cobertura", 50)])
        oo = ob.obter_obra(pool, conta, o["id"])
        for e in oo["etapas"][:feitas]:
            ob.marcar_etapa(pool, conta, o["id"], e["id"], concluida=True)
    om.salvar_lotes(pool, conta, m["id"], [_lote("1", 0, 0, obra_id=a["id"]),
                                           _lote("2", 70, 0, obra_id=b["id"]),
                                           _lote("3", 140, 0, situacao="vago")])
    v = om.vista(pool, conta, m["id"])
    assert v["resumo"]["casas"] == 2 and v["resumo"]["prontas"] == 1
    assert v["resumo"]["pct"] == 50 and v["resumo"]["vagos"] == 1
    lote = v["lotes"][0]
    assert "mat_linhas" in lote and lote["fotos"] == []
    html = _painel(pool, conta, monkeypatch).get(f"/painel/obras/mapa?m={m['id']}").text
    assert "Andamento médio" in html and "pf-mattab" in html and "aten-lista" in html


def test_a_aba_esta_no_menu_e_fica_acesa():
    # renderiza o `base` direto, como tests/test_menu_bate_com_o_gate.py
    from contas import equipe as eq
    from web.portal import _env
    conta = [1, "pj", "PX2", "doc", "app_pro", None, None, None,
             False, None, None, True, None, None, True, None, "construcao"]
    html = _env.get_template("base").render(
        logado=True, papel="dono", caps=eq.caps_do_papel("dono"), conta=conta,
        tem_pj=True, vende_servico=True, vende_produto=False, secao_ativa="obras_mapa",
        n_contextos=1, ve_novidades=True, novidades_n=0, tem_cesta=False, embed=False,
        raio_x_perfil={"chave": "obras", "aplica": True, "vocab": {}})
    assert 'href="/painel/obras/mapa" class="nav-i on"' in html     # aba própria, acesa
    assert 'href="/painel/obras" class="nav-i"' in html            # e Obras apagada
    assert 'id="ic-mapa"' in html
