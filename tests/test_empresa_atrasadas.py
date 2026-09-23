"""A pílula "⚠️ Atrasadas" da aba Empresa — a conta, a tela e o CLIQUE.

Pedido do dono em 23/09/2026: "dentro da aba empresas tem 3 card lá, só falta
esse". Os "3 card" são as pílulas de filtro dos títulos (Esperando liberação,
A receber e Tudo — a "✅ Liberadas" existe, mas estava zerada na Prime e pílula
sem conta não é desenhada). O que faltava era a das contas vencidas.

O QUE ESTE ARQUIVO FIXA, e por quê:

  * **ela é LENTE, não bloco.** Os três blocos particionam a lista; "atrasada"
    atravessa os três. Um quarto bloco faria a mesma conta aparecer duas vezes e
    "Tudo" parar de somar certo. O teste do navegador conta as linhas visíveis
    e exige que cada uma apareça UMA vez;
  * **só conta A PAGAR.** A receber vencida é cliente devendo ao dono, e isso
    tem casa no card Carteira de clientes. Somar as duas anunciaria uma dívida
    que não é dele;
  * **ela não rouba a abertura.** A tela continua abrindo no bloco que pede
    decisão (Esperando liberação) — a pílula nova é um toque, não a porta;
  * **clicar tem que FUNCIONAR.** A lição de 19/09 (test_janela_abre_no_navegador):
    perguntar se a função está na página não prova nada. O teste clica.
"""
from __future__ import annotations

import os
import re
from datetime import date

import pytest
from jinja2 import DictLoader, Environment

from web import portal as pt


def _t(desc, apro="aguardando", tipo="pagar", atrasado=False, cent=10000):
    return {"id": abs(hash(desc)) % 10_000, "descricao": desc, "contraparte": "",
            "valor_centavos": cent, "aprovacao": apro, "tipo": tipo,
            "vencimento": date(2026, 9, 15), "atrasado": atrasado,
            "prazo": "", "cliente_nome": None, "cliente_id": None,
            "criado_nome": None, "aprovado_nome": None, "aprovacao_motivo": None,
            "sem_fornecedor": False, "conciliar": None, "cobranca_link_url": None,
            "periodicidade": None, "valor_variavel": False, "proxima": None}


def _blocos(lib=(), esp=(), rec=()):
    """Os três blocos, montados como a rota monta."""
    soma = lambda xs: sum(x["valor_centavos"] for x in xs)  # noqa: E731
    return [
        {"titulo": "✅ Liberadas — pode pagar", "cor": "ok", "decide": False,
         "itens": list(lib), "centavos": soma(lib), "dica": ""},
        {"titulo": "⏳ Esperando liberação", "cor": "esp", "decide": True,
         "itens": list(esp), "centavos": soma(esp), "dica": ""},
        {"titulo": "A receber", "cor": "rec", "decide": False,
         "itens": list(rec), "centavos": soma(rec), "dica": ""},
    ]


# ───────────────────────────────────────────────────────── a conta (sem banco)

def test_so_conta_a_pagar_vencida():
    blocos = _blocos(
        lib=[_t("LIB vencida", "autorizado", atrasado=True, cent=500)],
        esp=[_t("ESP vencida", atrasado=True, cent=700), _t("ESP em dia")],
        rec=[_t("RECEB vencida", "autorizado", tipo="receber", atrasado=True, cent=9999)],
    )
    tot = pt._lente_atrasadas(blocos)
    assert tot == {"n": 2, "centavos": 1200}, (
        "a receber vencida entrou na conta — ela é cliente devendo, não dívida do dono")


def test_cada_bloco_sabe_quantas_tem():
    blocos = _blocos(
        lib=[_t("L1", "autorizado", atrasado=True, cent=100)],
        esp=[_t("E1", atrasado=True, cent=200), _t("E2", atrasado=True, cent=300),
             _t("E3")],
        rec=[_t("R1", "autorizado", tipo="receber", atrasado=True)],
    )
    pt._lente_atrasadas(blocos)
    por = {b["cor"]: (b["n_atrasadas"], b["atrasadas_centavos"]) for b in blocos}
    assert por == {"ok": (1, 100), "esp": (2, 500), "rec": (0, 0)}


def test_sem_nenhuma_atrasada_e_zero_e_nao_estoura():
    blocos = _blocos(esp=[_t("E1")])
    assert pt._lente_atrasadas(blocos) == {"n": 0, "centavos": 0}
    assert pt._lente_atrasadas(_blocos()) == {"n": 0, "centavos": 0}


def test_a_rota_usa_a_funcao_e_passa_pra_tela():
    """A conta tem que ser a MESMA que o teste exercita — senão o teste prova uma
    função que a tela não chama."""
    import inspect
    fonte = inspect.getsource(pt.painel_empresa)
    assert "_lente_atrasadas(tit_blocos)" in fonte
    assert "tit_atrasadas=tit_atrasadas" in fonte


# ───────────────────────────────────────────────────────── a tela (template real)

def _fatia():
    """Do filtro até o fim dos blocos — o trecho REAL do template."""
    tpl = pt._EMPRESA
    i = tpl.index("{#- O FILTRO.")
    j = tpl.index('{% else %}<div class="mut" style="font-size:.85rem">'
                  "Nenhum título em aberto")
    return tpl[i:j]


def _render(blocos):
    env = Environment(loader=DictLoader({"t": _fatia()}))
    env.filters["brl"] = pt.brl
    env.filters["n2"] = lambda v: f"{v:.2f}"
    tot = pt._lente_atrasadas(blocos)
    titulos = [t for b in blocos for t in b["itens"]]
    return env.get_template("t").render(
        tit_blocos=blocos, titulos=titulos, tit_atrasadas=tot,
        pode_liberar=True, RITMOS=[], RITMO_SELO={})


def test_a_pilula_aparece_e_vem_primeiro():
    html = _render(_blocos(esp=[_t("E1", atrasado=True), _t("E2")],
                           rec=[_t("R1", "autorizado", tipo="receber")]))
    faixa = html[html.index('<div class="tit-filtro">'):]
    faixa = faixa[:faixa.index("</div>")]
    rotulos = re.findall(r"</b>([^<]+)</a>", faixa)
    assert rotulos[0].strip() == "⚠️ Atrasadas", rotulos
    assert "<b>1</b>⚠️ Atrasadas" in faixa


def test_a_pilula_nao_rouba_a_abertura():
    """A tela abre no bloco que pede decisão, como sempre abriu."""
    html = _render(_blocos(esp=[_t("E1", atrasado=True)],
                           rec=[_t("R1", "autorizado", tipo="receber")]))
    assert 'class="atr" data-alvo="@atr"' in html          # sem ' on'
    assert re.search(r'class="esp on" data-alvo="tbl-esp"', html)


def test_com_um_bloco_so_a_faixa_aparece_se_houver_atrasada():
    """Antes, um bloco só = sem faixa (não há o que filtrar). Com atrasada, há."""
    so_esp_em_dia = _render(_blocos(esp=[_t("E1"), _t("E2")]))
    assert '<div class="tit-filtro">' not in so_esp_em_dia
    so_esp_vencida = _render(_blocos(esp=[_t("E1", atrasado=True), _t("E2")]))
    assert '<div class="tit-filtro">' in so_esp_vencida


def test_sem_atrasada_nao_tem_pilula():
    html = _render(_blocos(lib=[_t("L1", "autorizado")], esp=[_t("E1")]))
    assert "⚠️ Atrasadas" not in html
    assert 'data-alvo="@atr"' not in html


def test_a_marca_da_linha_so_em_a_pagar_vencida():
    html = _render(_blocos(
        esp=[_t("PAGAR VENCIDA", atrasado=True), _t("PAGAR EM DIA")],
        rec=[_t("RECEBER VENCIDA", "autorizado", tipo="receber", atrasado=True)]))
    linhas = re.findall(r'<div class="tit-lin( atr)?">\s*<div class="tit-id">\s*'
                        r'<div class="tit-desc">(?:<input[^>]*>)?([^<{]+)', html)
    marca = {d.strip(): bool(a) for a, d in linhas}
    assert marca == {"PAGAR VENCIDA": True, "PAGAR EM DIA": False,
                     "RECEBER VENCIDA": False}, marca


# ───────────────────────────────────────────────────────── o CLIQUE (navegador)

def _funcao_do_filtro() -> str:
    """O `titFiltroClicar` REAL, tirado do template — JS puro, sem Jinja."""
    tpl = pt._EMPRESA
    i = tpl.index("function titFiltroClicar(a){")
    j = tpl.index("\n  }\n", i) + len("\n  }\n")
    return tpl[i:j]


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


def _pagina(navegador, tmp_path, blocos):
    corpo = _render(blocos)
    # o <style> da faixa e dos blocos mora ANTES do filtro, na mesma seção; ele é
    # o que esconde as linhas na lente, então tem que vir junto
    tpl = pt._EMPRESA
    ci = tpl.index(".tit-filtro{display:flex")
    css = tpl[ci:tpl.index("</style>", ci)]
    html = ("<!doctype html><meta charset=utf-8><style>" + css + "</style>"
            + corpo + "<script>" + _funcao_do_filtro() + "</script>")
    alvo = tmp_path / "empresa.html"
    alvo.write_text(html, encoding="utf-8")
    pag = navegador.new_page(viewport={"width": 1100, "height": 900})
    erros: list[str] = []
    pag.on("pageerror", lambda e: erros.append(str(e)))
    pag.goto(alvo.as_uri())
    return pag, erros


_VISIVEIS = """() => [...document.querySelectorAll('.tit-lin')]
  .filter(l => l.offsetParent !== null)
  .map(l => l.querySelector('.tit-desc').textContent.trim().split('\\n')[0].trim())"""
# (só a primeira linha: depois da descrição vêm os selos, "aguardando você" etc.)


def test_clicar_na_pilula_mostra_so_as_atrasadas_uma_vez_cada(navegador, tmp_path):
    blocos = _blocos(
        lib=[_t("LIB VENCIDA", "autorizado", atrasado=True), _t("LIB EM DIA", "autorizado")],
        esp=[_t("ESP VENCIDA", atrasado=True), _t("ESP EM DIA")],
        rec=[_t("RECEB VENCIDA", "autorizado", tipo="receber", atrasado=True)],
    )
    pag, erros = _pagina(navegador, tmp_path, blocos)
    try:
        pag.click('a[data-alvo="@atr"]')
        vis = pag.evaluate(_VISIVEIS)
        assert not erros, erros
        assert sorted(vis) == ["ESP VENCIDA", "LIB VENCIDA"], vis
        assert len(vis) == len(set(vis)), "uma conta apareceu duas vezes"
        # o bloco A receber não tem nenhuma a pagar vencida: some inteiro
        assert not pag.is_visible("#tbl-rec")
        # e o cabeçalho de cada bloco diz o número que está na tela, não o total
        cab = pag.inner_text("#tbl-esp .tit-bcab")
        assert "1 atrasada" in cab and "2 ·" not in cab, cab
        assert pag.get_attribute('a[data-alvo="@atr"]', "class").endswith("on")
    finally:
        pag.close()


def test_sair_da_lente_devolve_tudo(navegador, tmp_path):
    blocos = _blocos(
        esp=[_t("ESP VENCIDA", atrasado=True), _t("ESP EM DIA")],
        rec=[_t("RECEB", "autorizado", tipo="receber")],
    )
    pag, erros = _pagina(navegador, tmp_path, blocos)
    try:
        pag.click('a[data-alvo="@atr"]')
        pag.click('a[data-alvo=""]')                       # "Tudo"
        vis = pag.evaluate(_VISIVEIS)
        assert not erros, erros
        assert sorted(vis) == ["ESP EM DIA", "ESP VENCIDA", "RECEB"], vis
        cab = pag.inner_text("#tbl-esp .tit-bcab")
        assert "2 ·" in cab and "atrasada" not in cab, cab
        # e a pílula de um bloco só também limpa a lente
        pag.click('a[data-alvo="@atr"]')
        pag.click('a[data-alvo="tbl-esp"]')
        assert sorted(pag.evaluate(_VISIVEIS)) == ["ESP EM DIA", "ESP VENCIDA"]
    finally:
        pag.close()
