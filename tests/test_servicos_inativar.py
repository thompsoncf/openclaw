"""Inativar e reativar serviço do catálogo — e o aviso do nome parecido.

O botão do catálogo se chamava "excluir" e nunca excluiu: sempre foi
`ativo=false`, pra não quebrar orçamento antigo que aponta pro slug. O furo era
não ter volta — o inativo sumia da tela.

Medido na Prime (conta 34) em 23/09/2026: quatro inativos (OUTROS, LOCAÇÃO,
LOCAÇÃO DE GERADOR DE ENERGIA, SEGURANÇA) e DOIS deles recadastrados iguais entre
os 42 ativos. Os nomes abaixo são os de lá, de propósito: a regra do "parecido"
tem que acertar exatamente esses dois e ficar quieta nos outros dois.
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import servicos_catalogo as cat


@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4,
                       open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    cat.garantir_tabela(p)
    yield p
    p.close()


def _conta(pool, nome="Empresa X"):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome) values ('pj', %s) returning id",
                        (nome,)).fetchone()[0]
        c.commit()
    return cid


@pytest.fixture()
def conta_id(pool):
    return _conta(pool)


# ───────────────────────────────────────────── a regra do nome (sem banco)

@pytest.mark.parametrize("a,b", [
    ("LOCAÇÃO DE GERADOR DE ENERGIA", "LOCAÇÃO GERADOR  DE ENERGIA"),   # o caso real
    ("SEGURANÇA", "SEGURANÇA"),                                          # o outro
    ("Segurança", "SEGURANCA"),                                          # acento e caixa
    ("Utensílios/Louças", "UTENSILIOS LOUCAS"),
])
def test_mesmo_servico_com_nome_escrito_diferente(a, b):
    assert cat._assinatura(a) == cat._assinatura(b)


@pytest.mark.parametrize("a,b", [
    ("LOCAÇÃO", "LOCAÇÃO SUÍTE"),          # uma palavra a mais é OUTRO serviço
    ("LOCAÇÃO", "LOCAÇÃO LEDS"),
    ("OUTROS", "SUPORTE OPERACIONAL"),
    ("KIDS - PULA PULA + MONITOR", "KIDS - FUTEBOL COM SABÃO + MONITOR"),
])
def test_servicos_diferentes_nao_se_confundem(a, b):
    """Igualdade de palavras, e não "contém": a Prime tem 42 ativos e uma dúzia
    começa com LOCAÇÃO. Uma regra de "contém" gritaria em todo inativo que tenha
    essa palavra — e aviso que grita sempre é aviso que ninguém lê."""
    assert cat._assinatura(a) != cat._assinatura(b)


# ───────────────────────────────────────────── inativar e reativar (banco)

def test_inativar_tira_da_lista_e_poe_na_gaveta(pool, conta_id):
    s = cat.salvar(pool, conta_id, nome="TAXA DE LIMPEZA")
    assert cat.excluir(pool, conta_id, s["id"])["ok"] is True
    assert cat.listar(pool, conta_id) == []
    gaveta = cat.listar_inativos(pool, conta_id)
    assert [g["nome"] for g in gaveta] == ["TAXA DE LIMPEZA"]


def test_reativar_traz_de_volta_com_o_mesmo_slug(pool, conta_id):
    """O slug é o que os orçamentos antigos guardam: voltar com outro seria
    voltar como outro serviço."""
    s = cat.salvar(pool, conta_id, nome="COPEIRA")
    cat.excluir(pool, conta_id, s["id"])
    assert cat.definir_ativo(pool, conta_id, s["id"], True)["ok"] is True
    vivo = cat.listar(pool, conta_id)
    assert [(v["id"], v["slug"]) for v in vivo] == [(s["id"], s["slug"])]
    assert cat.listar_inativos(pool, conta_id) == []


def test_reativar_o_que_ja_esta_ativo_nao_finge_sucesso(pool, conta_id):
    """Tela desatualizada (duas abas) tem que ouvir "não", não um ok calado."""
    s = cat.salvar(pool, conta_id, nome="GARÇON")
    assert cat.definir_ativo(pool, conta_id, s["id"], True)["ok"] is False
    cat.excluir(pool, conta_id, s["id"])
    assert cat.excluir(pool, conta_id, s["id"])["ok"] is False


def test_outra_conta_nao_reativa_nem_ve_a_gaveta(pool, conta_id):
    s = cat.salvar(pool, conta_id, nome="SERVIÇO DE DJ E SONORIZAÇÃO")
    cat.excluir(pool, conta_id, s["id"])
    intrusa = _conta(pool, "Outra")
    assert cat.definir_ativo(pool, intrusa, s["id"], True)["ok"] is False
    assert cat.listar_inativos(pool, intrusa) == []
    assert cat.listar(pool, conta_id) == []          # continua inativo na dona


def test_a_gaveta_da_prime_acerta_os_dois_gemeos_e_so_eles(pool, conta_id):
    """O retrato de 23/09/2026: 4 inativos, 2 com homônimo ativo."""
    for nome in ("OUTROS", "LOCAÇÃO", "LOCAÇÃO DE GERADOR DE ENERGIA", "SEGURANÇA"):
        s = cat.salvar(pool, conta_id, nome=nome)
        cat.excluir(pool, conta_id, s["id"])
    for nome in ("LOCAÇÃO GERADOR  DE ENERGIA", "SEGURANÇA", "LOCAÇÃO SUÍTE",
                 "LOCAÇÃO LEDS", "SUPORTE OPERACIONAL"):
        cat.salvar(pool, conta_id, nome=nome)
    gaveta = {g["nome"]: g["parecido"] for g in cat.listar_inativos(pool, conta_id)}
    assert gaveta == {
        "OUTROS": "",
        "LOCAÇÃO": "",
        "LOCAÇÃO DE GERADOR DE ENERGIA": "LOCAÇÃO GERADOR  DE ENERGIA",
        "SEGURANÇA": "SEGURANÇA",
    }


# ───────────────────────────────────────────── a tela

def test_a_tela_chama_de_inativar_e_nao_de_excluir():
    from web import painel_servicos as ps
    src = ps.__loader__.get_source(ps.__name__)
    assert 'title="Excluir serviço">🗑' not in src
    assert 'title="Inativar serviço">⊘' in src
    assert "Excluir \"'+" not in src, "sobrou um confirm dizendo 'Excluir'"
    assert "function txtInativar(nome)" in src


def test_a_rota_de_reativar_existe_e_a_lista_leva_os_inativos():
    from web import painel_servicos as ps
    rotas = {r.path for r in ps.router.routes}
    assert "/painel/servicos/catalogo/reativar" in rotas
    import inspect
    fonte = inspect.getsource(ps.painel_servicos_catalogo)
    assert "scat.listar_inativos(" in fonte and '"inativos": inativos' in fonte


# ───────────────────────────────────────────── o CLIQUE (navegador)
#
# A lição de 19/09/2026 (test_janela_abre_no_navegador): "o JS compila" e "a função
# está na página" não provam que o botão faz alguma coisa. Aqui a tela real de
# Serviços carrega com a rede dublada, a gaveta abre e o "reativar" é clicado.

_CATALOGO = {
    "itens": [{"id": 1, "slug": "seguranca", "nome": "SEGURANÇA", "descricao": "",
               "setup": 300, "mensal": 0, "custo": 0, "categoria": "", "foto_url": "",
               "icone": "", "icone_svg": ""}],
    "inativos": [
        {"id": 7, "slug": "outros", "nome": "OUTROS", "descricao": "",
         "setup": 0, "mensal": 0, "parecido": ""},
        {"id": 8, "slug": "seguranca-2", "nome": "SEGURANÇA", "descricao": "",
         "setup": 0, "mensal": 0, "parecido": "SEGURANÇA"},
    ],
    "categorias": [],
}

#: Roda ANTES dos scripts da página: a carga inicial do catálogo já tem que
#: encontrar a rede dublada. Responde como Response de verdade (o zapFetch lê
#: status, cabeçalho e o corpo como texto).
_DUBLE = """(() => {
  window.__chamadas = [];
  const resp = (obj) => Promise.resolve({ok: true, status: 200,
    headers: {get: () => null},
    text: () => Promise.resolve(JSON.stringify(obj)),
    json: () => Promise.resolve(obj)});
  window.fetch = function(url, op){
    op = op || {};
    window.__chamadas.push({url: String(url), metodo: op.method || 'GET',
                            corpo: op.body || null});
    const u = String(url);
    if (u === '/painel/servicos/catalogo' && !op.method) return resp(window.__CATALOGO);
    if (u.indexOf('/painel/servicos/catalogo/') === 0) return resp({ok: true});
    // O resto da tela (contrato, termo aditivo, funil…) fica ESPERANDO, e não
    // recebe um {ok:true} genérico: com ele o carregador do aditivo lia
    // `d.textos.disposicoes` de um objeto sem `textos` e estourava — um erro do
    // dublê, não da tela, que o teste confundiria com defeito.
    return new Promise(function(){});
  };
})();"""


@pytest.fixture(scope="module")
def navegador():
    playwright = pytest.importorskip("playwright.sync_api")
    binario = os.environ.get("PLAYWRIGHT_CHROMIUM_BIN") or None
    try:
        with playwright.sync_playwright() as p:
            nav = p.chromium.launch(executable_path=binario)
            yield nav
            nav.close()
    except Exception as e:  # noqa: BLE001 — sem navegador no ambiente, não é falha
        pytest.skip(f"sem Chromium utilizável: {e}")


def _abre_servicos(navegador, tmp_path):
    import json
    from tests.test_painel_js_sintaxe import _render
    alvo = tmp_path / "servicos.html"
    alvo.write_text(_render("servicos"), encoding="utf-8")
    pag = navegador.new_page(viewport={"width": 1200, "height": 900})
    erros: list[str] = []
    pag.on("pageerror", lambda e: erros.append(str(e)))
    pag.add_init_script("window.__CATALOGO = " + json.dumps(_CATALOGO) + ";" + _DUBLE)
    pag.goto(alvo.as_uri())
    # a própria tela carrega o catálogo ao abrir (é da closure, não dá pra chamar
    # de fora) — e é melhor assim: o teste passa pelo caminho que o dono passa
    pag.wait_for_timeout(500)
    pag.evaluate("() => document.getElementById('oc-vertodos').click()")
    pag.wait_for_timeout(150)
    return pag, erros


def test_a_gaveta_aparece_fechada_e_abre(navegador, tmp_path):
    pag, erros = _abre_servicos(navegador, tmp_path)
    try:
        assert not erros, erros
        gav = pag.evaluate("""() => { const g = document.querySelector('.oc-inativos');
            return g && {aberta: g.classList.contains('open'),
                         cab: g.querySelector('.oc-inativos-cab').innerText,
                         linhas: g.querySelectorAll('.oc-inativo-row').length}; }""")
        assert gav, "a gaveta dos inativos não foi desenhada"
        assert gav["aberta"] is False and "2 serviços inativos" in gav["cab"], gav
        pag.evaluate("() => document.querySelector('.oc-inativos-cab').click()")
        pag.wait_for_timeout(100)
        aberta = pag.evaluate("() => document.querySelector('.oc-inativos').classList.contains('open')")
        avisos = pag.evaluate("""() => [...document.querySelectorAll('.oc-inativo-row')]
            .map(r => [r.querySelector('b').innerText, !!r.querySelector('.oc-inativo-av')])""")
        assert aberta
        assert avisos == [["OUTROS", False], ["SEGURANÇA", True]], avisos
        assert not erros, erros
    finally:
        pag.close()


def test_reativar_chama_a_rota_certa(navegador, tmp_path):
    pag, erros = _abre_servicos(navegador, tmp_path)
    try:
        pag.evaluate("() => document.querySelector('.oc-inativos-cab').click()")
        pag.evaluate("() => { window.__chamadas = []; "
                     "document.querySelector('.oc-inativo-row[data-iid=\"7\"] .oc-reativar').click(); }")
        pag.wait_for_timeout(250)
        cham = pag.evaluate("() => window.__chamadas")
        assert not erros, erros
        posts = [c for c in cham if c["metodo"] == "POST"]
        assert posts and posts[0]["url"] == "/painel/servicos/catalogo/reativar", cham
        assert '"id":7' in posts[0]["corpo"].replace(" ", "")
        # e recarrega a lista depois, pra o reativado aparecer nela
        assert any(c["metodo"] == "GET" and c["url"] == "/painel/servicos/catalogo"
                   for c in cham), cham
    finally:
        pag.close()


def test_reativar_o_gemeo_pede_confirmacao_e_respeita_o_nao(navegador, tmp_path):
    pag, erros = _abre_servicos(navegador, tmp_path)
    try:
        pag.evaluate("() => document.querySelector('.oc-inativos-cab').click()")
        perguntas: list[str] = []

        def recusa(d):
            perguntas.append(d.message)
            d.dismiss()
        pag.once("dialog", recusa)
        pag.evaluate("() => { window.__chamadas = []; "
                     "document.querySelector('.oc-inativo-row[data-iid=\"8\"] .oc-reativar').click(); }")
        pag.wait_for_timeout(200)
        assert perguntas and "nome parecido" in perguntas[0], perguntas
        assert not [c for c in pag.evaluate("() => window.__chamadas") if c["metodo"] == "POST"], \
            "disse NÃO no confirm e reativou mesmo assim"
        assert not erros, erros
    finally:
        pag.close()
