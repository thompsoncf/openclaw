"""A janela do lead ABRE, em toda tela que a serve — medido num navegador.

POR QUE ESTE ARQUIVO EXISTE. Em 19/09/2026 a Comunicação passou a abrir a janela
("Abrir ficha" virou pop-up em vez de navegar). Subiu quebrada: o botão não fazia
NADA. E o teste que eu tinha escrito passou verde, porque ele perguntava a coisa
errada — `"function kbAbrirLead(" in html`, ou seja, "a função está na página?".
Estava. Chamá-la é que estourava na primeira linha:

    function kbAbrirLead(ev,id,cardEl){
      if(ev)ev.stopPropagation();
      kbFecharChat();        ← ReferenceError aqui

`kbFecharChat` mora em web/balao_conversa.py. As duas telas que abriam a janela
até então — o funil e o Follow-up — carregam os DOIS módulos, então o nome sempre
existiu por acidente. A Comunicação tem o chat próprio dela e carrega só a janela.

O sintoma é o pior que existe numa tela: nada acontece. Sem alerta, sem faixa, sem
recarregar — o `ReferenceError` fica no console, que quem usa o painel não abre.

Então a pergunta aqui é a única que valia: CLICAR e ver se abriu. É o mesmo
princípio do test_painel_js_sintaxe (renderizar de verdade em vez de ler o .py),
um passo adiante — lá o JS precisa COMPILAR, aqui ele precisa RODAR.

Pula sozinho onde não houver Chromium, como o test_cockpit_rodape_render.
"""
from __future__ import annotations

import os

import pytest

from tests.test_painel_js_sintaxe import PAGINAS, _render

#: As telas do painel de prospecção que servem `janela_js`. Crescer esta lista é
#: de graça e é o ponto: a tela nova que passar a abrir a janela entra aqui, e o
#: teste descobre sozinho se ela trouxe as dependências junto.
TELAS = ("prospeccao", "prospeccao_ficha", "prospeccao_comunicacao")

#: O que o navegador roda depois de carregar a página: cala a rede, planta um
#: botão igual ao que a tela monta e clica nele. Sem o stub, o `fetch` do resumo
#: iria pro `file://` e o teste mediria a rede, não a função.
_ROTEIRO = """() => {
  window.__erros = [];
  window.onerror = function(m){ window.__erros.push(String(m)); };
  window.fetch = function(){
    return Promise.resolve({ok:true, json:function(){
      return Promise.resolve({ok:true, empresa:'Teste', status:'contatado'});
    }});
  };
  var d = document.createElement('div');
  d.style.cssText = 'position:fixed;top:120px;left:120px';
  d.innerHTML = '<button type="button" id="zaq-alvo">Abrir ficha</button>';
  document.body.appendChild(d);
  try { kbAbrirLead(null, 7, document.getElementById('zaq-alvo')); }
  catch (e) { window.__erros.push(String(e)); }
  return true;
}"""


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


def _abre(navegador, tmp_path, tela: str) -> dict:
    alvo = tmp_path / f"{tela}.html"
    alvo.write_text(_render(tela), encoding="utf-8")
    pag = navegador.new_page(viewport={"width": 1200, "height": 850})
    quebras: list[str] = []
    pag.on("pageerror", lambda e: quebras.append(str(e)))
    pag.goto(alvo.as_uri())
    pag.wait_for_timeout(250)
    pag.evaluate(_ROTEIRO)
    pag.wait_for_timeout(250)
    fora = {
        "popup": pag.evaluate("() => !!document.querySelector('.leadpop')"),
        "erros": quebras + (pag.evaluate("() => window.__erros || []") or []),
    }
    pag.close()
    return fora


@pytest.mark.parametrize("tela", TELAS)
def test_a_janela_do_lead_abre_de_verdade(navegador, tmp_path, tela):
    """Clicar tem que pôr a janela na tela. Em nenhuma das três o botão pode ser
    um botão morto."""
    assert tela in PAGINAS, f"{tela} não tem contexto em test_painel_js_sintaxe"
    r = _abre(navegador, tmp_path, tela)
    assert not r["erros"], f"{tela}: a janela estourou ao abrir — {r['erros']}"
    assert r["popup"], (
        f"{tela}: cliquei em 'Abrir ficha' e nada apareceu. É o defeito de "
        f"19/09: a função existe na página, mas uma dependência que ela usa não.")


def test_a_janela_nao_depende_de_nome_que_pode_nao_existir():
    """A guarda, na FONTE — e é esta que roda no CI.

    O teste do navegador acima é quem ACHOU o defeito, mas ele pula onde não há
    Chromium, e o CI desta base não instala nenhum. Se a proteção dependesse só
    dele, o próximo `kbFecharChat()` cru passaria verde exatamente como o de hoje.

    A conferência é POR LINHA, não por arquivo. A primeira versão desta função
    perguntava `f"window.{nome}" in _janela.JS` — e passava na mutação, porque o
    `kbAbrirSegurado` (que nasceu com a guarda) já tinha a string em algum lugar
    do mesmo arquivo. Assertiva que casa com texto de OUTRO trecho é a mesma
    armadilha do comentário que também contém a palavra procurada.
    """
    import re

    from web import balao_conversa as _balao
    from web import janela_lead as _janela

    do_balao = set(re.findall(r"^function (\w+)", _balao.JS, re.M))
    # DOIS JEITOS CERTOS, e a janela usa os dois. O `cxEscK` ela PROVÊ sozinha
    # (`window.cxEscK = window.cxEscK || …`, no topo do módulo), e aí existe em
    # qualquer página — exigir guarda nas ~50 chamadas dele seria ruído. O
    # `kbFecharChat` ela não provê: ali a guarda é por chamada.
    proprios = set(re.findall(r"window\.(\w+)\s*=\s*window\.\1\s*\|\|", _janela.JS))
    cobrar = sorted(do_balao - proprios)
    for n, bruta in enumerate(_janela.JS.splitlines(), 1):
        # COMENTÁRIO NÃO É CHAMADA. O comentário que explica este defeito CITA
        # `kbFecharChat()`, e a primeira versão desta varredura acusou o próprio
        # texto que a documenta — a mesma armadilha de casar assertiva com palavra
        # que também aparece em comentário. `(?<!:)` poupa o `//` de uma URL.
        linha = re.sub(r"(?<!:)//.*$", "", bruta)
        for nome in cobrar:
            # chamada de verdade: `nome(` que não é `.nome(` nem `window.nome`
            if not re.search(rf"(?<![.\w]){nome}\s*\(", linha):
                continue
            assert f"window.{nome}" in linha, (
                f"linha {n} chama `{nome}()`, que mora no balão de conversa, sem "
                f"conferir na MESMA linha se ele existe:\n    {bruta.strip()}\n"
                f"Nas telas que carregam só a janela (Comunicação, ficha) isso é "
                f"ReferenceError na primeira linha da função — e o botão morre "
                f"calado, sem alerta nenhum pra quem usa.")
