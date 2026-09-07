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

POR QUE OLHA MAIS DE UMA TELA (07/09/2026). O balão de conversa saiu do funil pra
`web/balao_conversa.py` quando o Raio-X passou a abrir o MESMO balão. Duas coisas
mudaram pra este teste: `kbAbrirChat` deixou de estar no arquivo do funil (a
fonte tem `{{ balao_js }}`, e o Jinja é que junta), e passou a existir uma
segunda tela com `onclick=`. Por isso cada página é lida COMO CHEGA NO NAVEGADOR
— com o balão já colado no lugar do marcador — e conferida SEPARADAMENTE: função
global numa tela não alcança o clique da outra.
"""
import re
from pathlib import Path

import pytest

from web import balao_conversa as _balao

RAIZ = Path(__file__).resolve().parent.parent / "web"


def _pagina(nome: str) -> str:
    """A fonte da tela com o balão já colado — é o que o navegador recebe.

    Só o `{{ balao_js }}` é substituído: o `{{ balao_css }}` mora num `<style>` e
    não tem função nenhuma pra alcançar."""
    return RAIZ.joinpath(nome).read_text(encoding="utf-8").replace(
        "{{ balao_js }}", _balao.JS
    )


TELAS = {
    "painel_prospeccao.py": _pagina("painel_prospeccao.py"),
    "painel_raio_x.py": _pagina("painel_raio_x.py"),
    # o Follow-up é a TERCEIRA tela a abrir o mesmo balão (07/09/2026). Entrar
    # aqui não é zelo: a tela só tem `{{ balao_js }}`, então quem esquecer de
    # injetar deixa o 💬 na tela com o clique morto, sem nada errado no HTML.
    "painel_follow_up.py": _pagina("painel_follow_up.py"),
}
TEXTO = TELAS["painel_prospeccao.py"]

# palavras que parecem chamada mas não são função nossa
_PALAVRAS = {"if", "for", "while", "switch", "return", "typeof", "function", "catch",
             "new", "delete", "void", "in", "of", "do", "else"}
_NATIVOS = {"alert", "confirm", "prompt", "fetch", "setTimeout", "setInterval",
            "parseInt", "parseFloat", "Number", "String", "Boolean", "Array",
            "encodeURIComponent", "decodeURIComponent", "event", "this", "window"}


def _handlers(texto: str = TEXTO):
    """Funções chamadas por `onclick=`. Ignora chamada de método (`x.foo()`), que
    não depende do escopo global, e o conteúdo de strings — um `confirm('… o
    acompanhamento (aberturas) …')` tem prosa que parece chamada de função."""
    nomes = set()
    for trecho in re.findall(r'onclick="([^"]+)"', texto):
        trecho = re.sub(r"'[^']*'", "''", trecho)      # some com o texto das strings
        for m in re.finditer(r"(\.?)\b([A-Za-z_$][\w$]*)\s*\(", trecho):
            ponto, nome = m.group(1), m.group(2)
            if not ponto and nome not in _PALAVRAS and nome not in _NATIVOS:
                nomes.add(nome)
    return nomes


def _globais(texto: str = TEXTO):
    """Funções em profundidade de chaves ZERO dentro de algum `<script>`."""
    out = set()
    for bloco in re.findall(r"<script[^>]*>(.*?)</script>", texto, re.S):
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
    return out | set(re.findall(r"window\.([A-Za-z_$][\w$]*)\s*=", texto))


def test_a_deteccao_funciona():
    """Guarda do próprio teste, nos dois sentidos — senão ele viraria um teste
    vazio que passa sempre.

    `paintTot` mora DENTRO do IIFE do polling e é o contraexemplo perfeito: existe,
    é usada, e não é global."""
    g = _globais()
    assert "capToggle" in g and "secToggle" in g      # global de verdade
    assert "paintTot" not in g and "paint" not in g   # presas no IIFE
    assert len(_handlers()) > 10


def test_as_duas_telas_tem_clique_pra_conferir():
    """Guarda da leitura de cada tela: arquivo renomeado ou marcador do balão
    trocado deixaria a lista vazia, e o teste passaria sem conferir nada."""
    for tela, texto in TELAS.items():
        assert _handlers(texto), f"{tela} não tem nenhum onclick= pra conferir"


@pytest.mark.parametrize(
    "tela,nome",
    sorted((t, n) for t, txt in TELAS.items() for n in _handlers(txt)),
)
def test_toda_funcao_de_onclick_e_alcancavel(tela, nome):
    assert nome in _globais(TELAS[tela]), (
        f"`{nome}` é chamada por onclick= em {tela} mas não está no escopo global "
        "dessa tela — provavelmente dentro de um IIFE/closure, ou definida só em "
        "OUTRA tela. O HTML renderiza certo e o clique morre num ReferenceError, "
        "sem nada acontecer na tela."
    )


def test_kpiabre_esta_no_topo():
    """O caso concreto, travado por nome: os KPIs de campanha abrem por onclick,
    e foi exatamente aqui que o bug apareceu em produção."""
    assert "kpiAbre" in _handlers()
    assert "kpiAbre" in _globais()


def test_o_balao_do_follow_up_alcanca_a_funcao_do_modulo():
    """Mesmo caso do Raio-X, na tela do Follow-up: a prévia da conversa é um
    botão que chama `kbAbrirChat`, e a função só existe no módulo do balão."""
    fu = TELAS["painel_follow_up.py"]
    assert "kbAbrirChat" in _handlers(fu)
    assert "kbAbrirChat" in _globais(fu)


def test_o_balao_do_raio_x_alcanca_a_funcao_do_modulo():
    """O caso que quebrou na extração: o Raio-X chama `kbAbrirChat` no onclick, e
    a função só existe em `web/balao_conversa.py`. Se alguém tirar o
    `{{ balao_js }}` da tela, o botão 💬 vira um clique morto."""
    rx = TELAS["painel_raio_x.py"]
    assert "kbAbrirChat" in _handlers(rx)
    assert "kbAbrirChat" in _globais(rx)


def test_nenhum_tojson_cru_dentro_de_atributo_de_aspas_duplas():
    """`|tojson` dentro de `atributo="…"` corta o atributo — em TODA a web/.

    07/09/2026: o 💬 do Raio-X passava o nome do cliente com `|tojson`, que
    devolve Markup com aspas DUPLAS de verdade. A primeira delas ENCERRA o
    atributo, e o navegador recebia `kbAbrirChat(event,12,'conversas',this,` —
    chamada cortada na vírgula, clique morto sem nada no HTML parecendo errado.

    Não é a primeira vez: o #598 quebrou o mesmo jeito num `onsubmit="confirm(…)"`
    com a descrição do título, e a saída lá foi `data-confirmar` com `|e`. Duas
    vezes é padrão, então vira teste — o remédio é `|tojson|forceescape` (ou
    mandar o valor por `data-…` e ler no JS).

    Só olha atributo de ASPAS DUPLAS: em atributo de aspas simples o `tojson`
    já é seguro, porque ele escapa a apóstrofe como \\u0027.
    """
    culpados = []
    for arq in sorted(RAIZ.glob("*.py")):
        # comentário de Jinja fora: é onde o #598 DOCUMENTA a armadilha, citando
        # o `onsubmit="confirm({{...|tojson}})"` que não se deve escrever. As
        # quebras de linha ficam, pra o número da linha continuar batendo.
        texto = re.sub(r"{#.*?#}", lambda m: "\n" * m.group(0).count("\n"),
                       arq.read_text(encoding="utf-8"), flags=re.S)
        for m in re.finditer(r'[A-Za-z_-]+="[^"\n]*"', texto):
            trecho = m.group(0)
            if "tojson" in trecho and "forceescape" not in trecho:
                linha = texto.count("\n", 0, m.start()) + 1
                culpados.append(f"{arq.name}:{linha}  {trecho[:90]}")
    assert not culpados, (
        "atributo de aspas duplas com |tojson cru — o atributo termina na "
        "primeira aspa do JSON e o handler morre:\n" + "\n".join(culpados)
    )
