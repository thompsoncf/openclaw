"""Sinal de rolagem na faixa de abas de Relatórios.

Depois do PR #571 (cartão 720px -> 1500px + coluna elástica), a faixa de 9
abas e a tabela cabem inteiras em qualquer tela de 1280px pra cima. Só que em
notebook de 13" (~1024px, comum) ainda sobram 2-3 abas fora da área visível —
e a faixa (`.abas`) esconde a barra de scroll de propósito, então rolar era um
gesto que ninguém sabia que existia. Foi o que confundiu o dono a achar a aba
Agenda em 26/08.

A correção é uma sombra nas pontas do trilho (`#rel-abas`), que aparece só do
lado que ainda tem aba escondida e some sozinha quando chega na ponta — mesmo
padrão de lista horizontal de app/catálogo. Puramente visual: nenhum dado,
rota ou `_dados_*` muda.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from web import portal as pt

_TPL = pt._RELATORIOS


def test_o_trilho_tem_a_regra_de_sombra_nas_duas_pontas():
    assert "#rel-abas.rel-fade::before" in _TPL and "#rel-abas.rel-fade::after" in _TPL
    assert "#rel-abas.no-left::before{opacity:0}" in _TPL
    assert "#rel-abas.no-right::after{opacity:0}" in _TPL


def test_nao_mexeram_na_classe_abas_compartilhada():
    """`.abas` é usada em outras telas (PDV, por exemplo) — a sombra tem que
    viver presa ao id `#rel-abas`, exclusivo desta tela."""
    fonte = open(pt.__file__, encoding="utf-8").read()
    assert re.search(r"\n\.abas\{[^}]*overflow-x:auto", fonte), \
        "a regra genérica .abas sumiu ou mudou — outras telas usam ela"
    assert "#rel-abas.rel-fade" in _TPL and ".abas.rel-fade" not in fonte


# ── o comportamento, rodando de verdade ──────────────────────────────────────
_CENARIO = r"""
%s
var out = {};
function chamaMarcador(){
  // pega o listener de "scroll" que o addEventListener guardou
  MARCAR();
  return {noLeft: trilho.classList.has("no-left"),
          noRight: trilho.classList.has("no-right")};
}
out.no_inicio = chamaMarcador();
trilho.scrollLeft = 40;  // rolou um pouco pro meio
out.no_meio = chamaMarcador();
trilho.scrollLeft = trilho.scrollWidth - trilho.clientWidth;  // rolou até o fim
out.no_fim = chamaMarcador();
console.log(JSON.stringify(out));
"""


def _monta_ambiente(js_bloco: str, *, cabe_tudo: bool) -> str:
    """DOM de mentira com um `trilho` (#rel-abas) — largura visível de 300px,
    conteúdo de 300px (cabe_tudo) ou 500px (não cabe, precisa rolar)."""
    largura_conteudo = 300 if cabe_tudo else 500
    return r"""
function _classList(){
  var s = {};
  return {
    add: function(c){ s[c] = true; },
    toggle: function(c, v){ s[c] = !!v; },
    has: function(c){ return !!s[c]; },
  };
}
var trilho = {
  scrollLeft: 0, clientWidth: 300, scrollWidth: %d,
  classList: _classList(),
  _scrollFn: null,
  addEventListener: function(ev, fn){ if (ev === "scroll") this._scrollFn = fn; },
};
var MARCAR = function(){ trilho._scrollFn(); };
var _ativa = null;  // sem aba ativa neste teste — só o rel-fade importa
global.document = {
  getElementById: function(id){ return id === "rel-abas" ? trilho : null; },
  querySelector: function(sel){ return sel.indexOf(".aba.ativa") !== -1 ? _ativa : null; },
};
global.window = { addEventListener: function(){} };
""" % largura_conteudo + js_bloco


@pytest.fixture(scope="module")
def bloco_js() -> str:
    m = re.search(r"<script>\s*(// com 8 abas.*?)</script>", _TPL, re.S)
    assert m, "não achei o bloco de JS do trilho de abas no template"
    return m.group(1)


def _roda(js_bloco: str, *, cabe_tudo: bool) -> dict:
    if not shutil.which("node"):
        pytest.skip("sem node no ambiente")
    ambiente = _monta_ambiente(js_bloco, cabe_tudo=cabe_tudo)
    script = ambiente + "\n" + (_CENARIO % "")
    r = subprocess.run(["node", "-e", script], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr[:900]
    return json.loads(r.stdout.strip().splitlines()[-1])


def test_no_inicio_so_a_sombra_da_direita_aparece(bloco_js):
    e = _roda(bloco_js, cabe_tudo=False)
    assert e["no_inicio"] == {"noLeft": True, "noRight": False}


def test_no_meio_as_duas_sombras_aparecem(bloco_js):
    e = _roda(bloco_js, cabe_tudo=False)
    assert e["no_meio"] == {"noLeft": False, "noRight": False}


def test_no_fim_so_a_sombra_da_esquerda_aparece(bloco_js):
    e = _roda(bloco_js, cabe_tudo=False)
    assert e["no_fim"] == {"noLeft": False, "noRight": True}


def test_quando_cabe_tudo_nenhuma_sombra_aparece(bloco_js):
    """Com o cartão de 1500px do #571, o caso comum (1280px+) é o trilho caber
    inteiro — a sombra não pode aparecer de enfeite quando não tem nada
    escondido pra nenhum dos dois lados."""
    e = _roda(bloco_js, cabe_tudo=True)
    assert e["no_inicio"] == {"noLeft": True, "noRight": True}
