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


@pytest.mark.parametrize("texto", ["--text", "--text-2", "--text-dim", "--neon", "--ambar", "--coral", "--azul", "--roxo"])
def test_texto_do_claro_se_le(texto):
    c = _tokens(tema._CLARO)
    for fundo in ("--bg", "--bg-2", "--surface"):
        assert _contraste(c[texto], c[fundo]) >= 4.5, f"{texto} sobre {fundo}"


def test_botao_verde_do_claro_se_le():
    c = _tokens(tema._CLARO)
    assert _contraste(c["--sobre-verde"], c["--neon"]) >= 4.5


@pytest.mark.parametrize("tinta,botao", [("--sobre-verde", "--neon"), ("--sobre-ambar", "--ambar"),
                                         ("--sobre-roxo", "--roxo"), ("--text", "--bolha-sai")])
@pytest.mark.parametrize("bloco", ["_ESCURO_CORES", "_CLARO"])
def test_tinta_de_botao_e_bolha_se_leem_nos_dois_temas(tinta, botao, bloco):
    c = _tokens(getattr(tema, bloco))
    assert _contraste(c[tinta], c[botao]) >= 4.5, f"{tinta} sobre {botao} no {bloco}"


@pytest.mark.parametrize("cor,fundo", [("--ambar", "--ambar-fundo"), ("--coral", "--coral-fundo"),
                                       ("--azul", "--azul-fundo"), ("--neon", "--neon-fundo"),
                                       ("--roxo", "--roxo-fundo")])
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
    # a cor do texto herdada vem calculada do <body> (escura no misto): o menu
    # precisa declarar a dele, senão texto sem cor própria some no fundo escuro
    assert menu.rstrip().endswith("color:var(--text);") or "color:var(--text);" in css.split('html[data-tema="misto"] .side', 1)[1].split("}", 1)[0]


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
        # Sem criar `nichos` nem `contas.nicho_id` aqui: o banco de teste é COMPARTILHADO
        # e uma `nichos` mínima viraria a definitiva pros arquivos seguintes (a 031
        # semeia `nome`/`tipo`). A migração de aparência pula a marca da piloto quando elas não existem.
        c.execute((RAIZ / "db" / "migracoes" / "202610031411_aparencia_temas.sql").read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def contas(pool):
    with pool.connection() as c:
        piloto = c.execute("insert into contas (tipo, nome, temas_piloto) values ('pj','Piloto',true) "
                           "returning id").fetchone()[0]
        outra = c.execute("insert into contas (tipo, nome) values ('pj','Outra') returning id").fetchone()[0]
        # só as colunas do db/schema.sql: o banco novo do CI não tem `email`, e o
        # `papel` de lá só aceita 'dono'/'membro' (o padrão serve)
        m = c.execute("insert into membros (conta_id, nome) values (%s,'Ana') returning id",
                      (piloto,)).fetchone()[0]
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


# ---------- o recado depois de salvar ----------

def test_o_recado_volta_como_codigo_e_nunca_como_texto_livre():
    """Os templates do painel rodam com autoescape desligado: um `?ok=<texto>`
    ecoado na tela viraria script ou recado falso assinado pelo Zaq."""
    from web import painel_aparencia as pa
    assert pa._recado_ok("empresa-claro") == "Tema da empresa: Claro."
    assert pa._recado_ok("meu-misto") == "Seu tema: Misto."
    assert pa._recado_ok("seguir") == "Pronto: você segue o tema da empresa."
    for lixo in ("<script>alert(1)</script>", "empresa-<b>x</b>", "Ligue 0800", "meu-roxo", ""):
        assert pa._recado_ok(lixo) == "", lixo
    assert set(pa._ERROS) == {"papel", "tema", "dono"}
    fonte = inspect.getsource(pa.painel_aparencia)
    assert "_recado_ok(ok)" in fonte and '_ERROS.get(erro, "")' in fonte


def test_a_tela_escapa_o_recado_mesmo_assim():
    html = _tela(ok="<script>x</script>", erro="<b>y</b>")
    assert "<script>x</script>" not in html and "&lt;script&gt;" in html
    assert "<b>y</b>" not in html


def test_falha_na_leitura_da_piloto_nao_fica_5_minutos():
    """Uma consulta que falha uma vez não pode deixar a piloto no escuro por 5 min."""
    class PoolQuebrado:
        def connection(self):
            raise RuntimeError("banco piscou")
    ap.esquecer_cache()
    assert ap.eh_piloto(PoolQuebrado(), 987654) is False
    _, quando = ap._PILOTO_CACHE[987654]
    import time
    restante = ap._PILOTO_TTL - (time.monotonic() - quando)
    assert restante <= ap._FALHA_TTL + 1, "a falha ficou guardada tempo demais"
    ap.esquecer_cache()


@pytest.mark.parametrize("texto", ["--text", "--text-2", "--text-dim"])
def test_texto_do_escuro_se_le(texto):
    """Os tons de apoio da fase 2 também passam no escuro (o --text-2 é novo)."""
    e = _tokens(tema._ESCURO_CORES)
    for fundo in ("--bg", "--bg-2", "--surface"):
        assert _contraste(e[texto], e[fundo]) >= 4.5, f"{texto} sobre {fundo}"


# ---------- o Cockpit (app do vendedor) ----------

def test_cockpit_sem_tema_e_o_documento_de_antes():
    from web import painel_cockpit as pc
    pc._TEMA.set("escuro")
    html = pc._page("Fila", "<p>x</p>").body.decode()
    assert html.startswith("<!doctype html><html lang=pt-br><head>")
    assert "<meta name=theme-color content='#0A0F0C'>" in html and "data-tema" not in html


@pytest.mark.parametrize("t,barra", [("claro", "#F3F6F4"), ("misto", "#0A0F0C")])
def test_cockpit_com_tema_marca_o_html_e_a_barra(t, barra):
    from web import painel_cockpit as pc
    pc._TEMA.set(t)
    try:
        html = pc._page("Fila", "<p>x</p>").body.decode()
    finally:
        pc._TEMA.set("escuro")
    assert html.startswith(f"<!doctype html><html lang=pt-br data-tema={t}><head>")
    assert f"<meta name=theme-color content='{barra}'>" in html


def test_cockpit_automatico_tem_as_duas_barras():
    from web import painel_cockpit as pc
    pc._TEMA.set("auto")
    try:
        html = pc._page("Fila", "<p>x</p>").body.decode()
    finally:
        pc._TEMA.set("escuro")
    assert "media='(prefers-color-scheme: light)' content='#F3F6F4'" in html
    assert "media='(prefers-color-scheme: dark)' content='#0A0F0C'" in html


def test_a_folha_do_cockpit_tem_os_temas_e_o_misto_escurece_cabecalho_e_abas():
    """No Cockpit o "menu" do misto é o cabeçalho e a barra de abas: ficam escuros,
    o conteúdo fica claro (o painel faz o mesmo com o menu lateral)."""
    from web import painel_cockpit as pc
    assert 'html[data-tema="claro"],html[data-tema="misto"]{' in pc._CSS_TEXTO
    assert 'html[data-tema="misto"] .hdr,html[data-tema="misto"] .tabs{' in pc._CSS_TEXTO
    assert 'html[data-tema="misto"] .side' not in pc._CSS_TEXTO


def test_o_cabecalho_e_as_abas_do_cockpit_seguem_o_fundo_do_tema():
    """Eram rgba(10,15,12,…) fixos: no claro, faixa escura com título escuro (1,1 de
    contraste). O color-mix com --bg dá a MESMA cor no escuro; o rgba de antes fica
    na frente pra navegador antigo, que ignora o color-mix."""
    from web import painel_cockpit as pc
    for pct in ("72%", "92%"):
        assert f"color-mix(in srgb,var(--bg) {pct},transparent)" in pc._CSS_TEXTO


def test_o_cockpit_liga_o_tema_onde_liga_o_vocabulario():
    from web import painel_cockpit as pc
    for f in (pc._sessao, pc._gerencia):
        fonte = inspect.getsource(f)
        assert fonte.count("_ligar_tema(") == fonte.count("_ligar_voc("), f.__name__
    assert "o tema nunca derruba o app" in inspect.getsource(pc._ligar_tema)
