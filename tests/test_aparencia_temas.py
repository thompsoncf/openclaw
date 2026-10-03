"""Temas do Zaq, fase 1 (docs/mockups/zaq_temas.html, aprovado em 03/10/2026).

O que esta trava garante:
1. O escuro não muda: sem `data-tema`, o `<html>` e o CSS do painel são os de antes.
2. O claro tem TODAS as cores do escuro (uma esquecida viraria a do escuro no meio
   da tela clara) e todas se leem (contraste WCAG ≥ 4,5 pro texto).
3. O misto redeclara os apelidos dentro do menu; sem isso `--card` herdaria o claro.
4. Só a conta piloto vê outro tema; qualquer outra lê "escuro" e não tem a tela.
5. A pessoa escolhe o dela; vendedor não muda o da empresa.
"""
import inspect
import os
import re
from pathlib import Path

import pytest

from contas import aparencia as ap
from web import tema

RAIZ = Path(__file__).resolve().parent.parent


# ---------- as cores ----------

def _tokens(bloco: str) -> dict:
    return dict(re.findall(r"(--[a-z0-9-]+):\s*([^;]+);", bloco))


def _lum(hexa: str) -> float:
    hexa = hexa.lstrip("#")
    if len(hexa) == 3:
        hexa = "".join(ch * 2 for ch in hexa)
    canais = []
    for i in (0, 2, 4):
        x = int(hexa[i:i + 2], 16) / 255
        canais.append(x / 12.92 if x <= 0.04045 else ((x + 0.055) / 1.055) ** 2.4)
    return 0.2126 * canais[0] + 0.7152 * canais[1] + 0.0722 * canais[2]


def _contraste(a: str, b: str) -> float:
    x, y = _lum(a), _lum(b)
    return (max(x, y) + 0.05) / (min(x, y) + 0.05)


def test_o_claro_tem_todas_as_cores_do_escuro():
    escuro, claro = _tokens(tema._ESCURO_CORES), _tokens(tema._CLARO)
    assert set(escuro) == set(claro), (
        f"faltam no claro: {sorted(set(escuro) - set(claro))}; "
        f"sobram: {sorted(set(claro) - set(escuro))}")


@pytest.mark.parametrize("texto", ["--text", "--text-dim", "--neon", "--ambar", "--coral", "--azul"])
def test_texto_do_claro_se_le(texto):
    c = _tokens(tema._CLARO)
    for fundo in ("--bg", "--bg-2", "--surface"):
        assert _contraste(c[texto], c[fundo]) >= 4.5, f"{texto} sobre {fundo}"


def test_botao_verde_do_claro_se_le():
    c = _tokens(tema._CLARO)
    assert _contraste(c["--sobre-verde"], c["--neon"]) >= 4.5


@pytest.mark.parametrize("cor,fundo", [("--ambar", "--ambar-fundo"), ("--coral", "--coral-fundo"),
                                       ("--azul", "--azul-fundo"), ("--neon", "--neon-fundo")])
def test_aviso_do_claro_se_le(cor, fundo):
    c = _tokens(tema._CLARO)
    assert _contraste(c[cor], c[fundo]) >= 4.5


def test_o_escuro_nao_mudou():
    """O escuro continua sendo o `_TOKENS` de sempre, e nada de tema entra nele."""
    assert "data-tema" not in tema.css() and "data-tema" not in tema.variaveis()
    assert tema._ESCURO_CORES.startswith("color-scheme: dark")
    assert _tokens(tema._ESCURO_CORES)["--bg"] == "#0A0F0C"
    assert _tokens(tema._ESCURO_CORES)["--neon"] == "#25D366"


def test_o_misto_redeclara_os_apelidos_no_menu():
    css = tema.temas()
    menu = css.split('html[data-tema="misto"] .side', 1)[1].split("}", 1)[0]
    assert "color-scheme: dark" in menu
    for apelido in ("--card:var(--surface)", "--txt:var(--text)", "--verde:var(--neon)"):
        assert apelido in menu, f"sem {apelido} o menu do misto herda o valor claro"
    for parte in tema._MENU:
        assert f'html[data-tema="misto"] {parte}' in css


def test_o_automatico_so_vale_com_o_aparelho_claro():
    css = tema.temas()
    assert '@media (prefers-color-scheme: light){html[data-tema="auto"]{' in css
    fora_da_media = css.replace(css[css.index("@media"):css.index("}}", css.index("@media")) + 2], "")
    assert 'html[data-tema="auto"]{' not in fora_da_media


# ---------- a página ----------

def _base(**over) -> str:
    from web.portal import _env
    ctx = dict(logado=False, titulo="Teste", rota="/x", tema="escuro")
    ctx.update(over)
    return _env.get_template("base").render(**ctx)


def test_sem_tema_a_pagina_e_a_de_antes():
    html = _base()
    assert html.startswith('<!doctype html><html lang="pt-br"><head>')
    assert "data-tema" not in html


def test_sem_tema_no_contexto_tambem_e_a_de_antes():
    from web.portal import _env
    html = _env.get_template("base").render(logado=False, titulo="T", rota="/x")
    assert "data-tema" not in html


@pytest.mark.parametrize("t", ["claro", "misto", "auto"])
def test_com_tema_a_marca_e_o_css_entram(t):
    html = _base(tema=t)
    assert html.startswith(f'<!doctype html><html lang="pt-br" data-tema="{t}"><head>')
    assert 'html[data-tema="claro"],html[data-tema="misto"]{' in html


def test_o_render_le_o_tema_sem_derrubar_a_tela():
    fonte = inspect.getsource(__import__("web.portal", fromlist=["_render"])._render)
    assert "aparencia" in fonte and "o tema nunca derruba a tela" in fonte


def test_o_menu_tem_aparencia_nas_duas_versoes():
    from web import portal
    assert portal._BASE.count("navi('aparencia','/painel/aparencia'") == 2
    assert 'id="ic-aparencia"' in portal._BASE


def test_todo_papel_alcanca_a_tela():
    from contas import equipe
    for papel in ("gestor", "vendedor", "financeiro", "restrito"):
        assert "/painel/aparencia" in equipe.rotas_do_papel(papel), papel


def test_a_tela_esta_registrada_e_no_app():
    from web import app as web_app, painel_aparencia as pa
    from web.portal import _env
    assert _env.loader.mapping.get("aparencia") is pa._TPL
    assert "aparencia_router" in inspect.getsource(web_app)


def test_vendedor_nao_muda_o_tema_da_empresa():
    from web import painel_aparencia as pa
    fonte = inspect.getsource(pa.painel_aparencia_empresa)
    assert "PAPEIS_DA_EMPRESA" in fonte
    assert ap.PAPEIS_DA_EMPRESA == ("dono", "gestor")


def _tela(**over) -> str:
    from web.portal import _env
    ctx = dict(
        logado=True, titulo="Aparência", secao_ativa="aparencia", rota="/painel/aparencia",
        caps={"vendas": True, "financeiro": True, "gerir": True}, papel="dono",
        n_contextos=0, versao_app="x", ve_novidades=False, conta=None,
        tem_cesta=False, tem_pj=True, vende_produto=False, vende_servico=True,
        beta_gratis=True, plano_aviso=None, tema="claro", aparencia_ok=True,
        atual={"piloto": True, "empresa": "claro", "meu": None, "tema": "claro"},
        opcoes=[{"v": t, "nome": ap.ROTULOS[t][0], "dica": ap.ROTULOS[t][1],
                 "mini": __import__("web.painel_aparencia", fromlist=["_MINIS"])._MINIS[t]}
                for t in ap.ROTULOS],
        muda_empresa=True, tem_meu=False, rotulos=ap.ROTULOS, ok="", erro="")
    ctx.update(over)
    return _env.get_template("aparencia").render(**ctx)


def test_a_tela_do_dono():
    html = _tela()
    assert "Tema da empresa" in html and "Usar este tema na empresa" in html
    assert 'value="claro" checked' in html
    assert "Só pra mim" not in html, "o dono não tem tema pessoal: o da empresa já é o dele"
    assert "Em teste nesta conta" in html
    assert 'href="/painel/aparencia"' in html, "o item do menu"


def test_a_tela_do_vendedor():
    html = _tela(papel="vendedor", muda_empresa=False, tem_meu=True,
                 atual={"piloto": True, "empresa": "misto", "meu": None, "tema": "misto"})
    assert "Usar este tema na empresa" not in html
    assert "A empresa usa: <b>Misto</b>" in html
    assert "Só pra mim" in html and 'value="seguir" checked' in html


# ---------- o banco: portão e escolha ----------

@pytest.fixture(scope="module")
def pool():
    if not os.environ.get("TEST_DATABASE_URL"):
        pytest.skip("sem TEST_DATABASE_URL")
    from psycopg_pool import ConnectionPool
    from db.conexao import init_schema
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=2,
                       open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    with p.connection() as c:
        c.execute("create table if not exists nichos (id serial primary key, slug text unique)")
        c.execute("alter table contas add column if not exists nicho_id int")
        c.execute((RAIZ / "db" / "migracoes" / "680_aparencia_temas.sql").read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def contas(pool):
    with pool.connection() as c:
        piloto = c.execute("insert into contas (tipo, nome, temas_piloto) values ('pj','Piloto',true) "
                           "returning id").fetchone()[0]
        outra = c.execute("insert into contas (tipo, nome) values ('pj','Outra') returning id").fetchone()[0]
        m = c.execute("insert into membros (conta_id, nome, email, papel) values (%s,'Ana',%s,'vendedor') "
                      "returning id", (piloto, f"ana{piloto}@temas.test")).fetchone()[0]
        c.commit()
    ap.esquecer_cache()
    yield piloto, outra, m
    with pool.connection() as c:
        c.execute("delete from membros where id=%s", (m,))
        c.execute("delete from contas where id in (%s,%s)", (piloto, outra))
        c.commit()
    ap.esquecer_cache()


def test_conta_que_nao_e_piloto_sempre_le_escuro(pool, contas):
    _, outra, _ = contas
    with pool.connection() as c:
        c.execute("update contas set tema='claro' where id=%s", (outra,))
        c.commit()
    assert ap.ler(pool, outra) == {"piloto": False, "empresa": "escuro", "meu": None, "tema": "escuro"}
    assert ap.salvar_empresa(pool, outra, "misto") is False


def test_piloto_sem_escolha_e_escuro(pool, contas):
    piloto, _, m = contas
    assert ap.ler(pool, piloto, m) == {"piloto": True, "empresa": "escuro", "meu": None, "tema": "escuro"}


def test_a_pessoa_segue_a_empresa_ate_escolher_o_dela(pool, contas):
    piloto, _, m = contas
    assert ap.salvar_empresa(pool, piloto, "misto")
    assert ap.ler(pool, piloto, m)["tema"] == "misto"
    assert ap.ler(pool, piloto)["tema"] == "misto", "o dono (sem membro) lê o da empresa"
    assert ap.salvar_meu(pool, piloto, m, "claro")
    assert ap.ler(pool, piloto, m) == {"piloto": True, "empresa": "misto", "meu": "claro", "tema": "claro"}
    assert ap.salvar_meu(pool, piloto, m, "")
    assert ap.ler(pool, piloto, m)["tema"] == "misto"


def test_tema_que_nao_existe_nao_grava(pool, contas):
    piloto, _, m = contas
    assert ap.salvar_empresa(pool, piloto, "roxo") is False
    assert ap.salvar_meu(pool, piloto, m, "roxo") is False
    with pool.connection() as c:
        with pytest.raises(Exception):
            c.execute("update contas set tema='roxo' where id=%s", (piloto,))
        c.rollback()


def test_membro_de_outra_conta_nao_vale(pool, contas):
    piloto, outra, m = contas
    with pool.connection() as c:
        c.execute("update contas set temas_piloto=true where id=%s", (outra,))
        c.commit()
    ap.esquecer_cache()
    assert ap.salvar_meu(pool, outra, m, "claro") is False
    assert ap.ler(pool, outra, m)["meu"] is None
