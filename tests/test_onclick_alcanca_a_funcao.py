"""`onclick=` inline só enxerga o escopo GLOBAL.

O bug que motivou este teste: `kpiAbre` foi escrita dentro do IIFE que faz o
polling de métricas. O Python compilava, a suíte passava, a página carregava sem
erro e o HTML saía perfeito — mas clicar no KPI não fazia absolutamente nada,
porque o handler inline morria num ReferenceError visível só no console do
navegador.

É uma falha que teste de backend não pega e que renderização de template não
revela. Só o clique quebra.

A verificação não olha indentação (que não prova nada em JS): mede a PROFUNDIDADE
DE CHAVES dentro de cada `<script>`. Função declarada em profundidade 0 é global;
qualquer coisa mais funda está presa num IIFE ou closure e o onclick não alcança.
"""
import re
from pathlib import Path

import pytest

FONTE = Path(__file__).resolve().parent.parent / "web" / "painel_prospeccao.py"
TEXTO = FONTE.read_text(encoding="utf-8")

# palavras que parecem chamada mas não são função nossa
_PALAVRAS = {"if", "for", "while", "switch", "return", "typeof", "function", "catch",
             "new", "delete", "void", "in", "of", "do", "else"}
_NATIVOS = {"alert", "confirm", "prompt", "fetch", "setTimeout", "setInterval",
            "parseInt", "parseFloat", "Number", "String", "Boolean", "Array",
            "encodeURIComponent", "decodeURIComponent", "event", "this", "window"}


def _handlers():
    """Funções chamadas por `onclick=`. Ignora chamada de método (`x.foo()`), que
    não depende do escopo global, e o conteúdo de strings — um `confirm('… o
    acompanhamento (aberturas) …')` tem prosa que parece chamada de função."""
    nomes = set()
    for trecho in re.findall(r'onclick="([^"]+)"', TEXTO):
        trecho = re.sub(r"'[^']*'", "''", trecho)      # some com o texto das strings
        for m in re.finditer(r"(\.?)\b([A-Za-z_$][\w$]*)\s*\(", trecho):
            ponto, nome = m.group(1), m.group(2)
            if not ponto and nome not in _PALAVRAS and nome not in _NATIVOS:
                nomes.add(nome)
    return nomes


def _globais():
    """Funções em profundidade de chaves ZERO dentro de algum `<script>`."""
    out = set()
    for bloco in re.findall(r"<script[^>]*>(.*?)</script>", TEXTO, re.S):
        prof = 0
        for i, ch in enumerate(bloco):
            if ch == "{":
                prof += 1
            elif ch == "}":
                prof -= 1
            elif prof == 0:
                m = re.match(r"function\s+([A-Za-z_$][\w$]*)\s*\(", bloco[i:])
                if m:
                    out.add(m.group(1))
    return out | set(re.findall(r"window\.([A-Za-z_$][\w$]*)\s*=", TEXTO))


def test_a_deteccao_funciona():
    """Guarda do próprio teste, nos dois sentidos — senão ele viraria um teste
    vazio que passa sempre.

    `paintTot` mora DENTRO do IIFE do polling e é o contraexemplo perfeito: existe,
    é usada, e não é global."""
    g = _globais()
    assert "capToggle" in g and "secToggle" in g      # global de verdade
    assert "paintTot" not in g and "paint" not in g   # presas no IIFE
    assert len(_handlers()) > 10


@pytest.mark.parametrize("nome", sorted(_handlers()))
def test_toda_funcao_de_onclick_e_alcancavel(nome):
    assert nome in _globais(), (
        f"`{nome}` é chamada por onclick= mas não está no escopo global — "
        "provavelmente dentro de um IIFE/closure. O HTML renderiza certo e o "
        "clique morre num ReferenceError, sem nada acontecer na tela."
    )


def test_kpiabre_esta_no_topo():
    """O caso concreto, travado por nome: os KPIs de campanha abrem por onclick,
    e foi exatamente aqui que o bug apareceu em produção."""
    assert "kpiAbre" in _handlers()
    assert "kpiAbre" in _globais()
