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

#: O resumo COMO O SERVIDOR MANDA. Completo de propósito: com um payload pela
#: metade, `kbLeadHtml` estoura, o `.catch` engole o erro e troca a janela por
#: "Falha de rede." — e o teste mediria a caixa de erro achando que mediu a
#: janela. (Foi o que aconteceu comigo em 19/09 ao investigar o layout: o `[0]`
#: de `d.temp_pill` faltando virou "Falha de rede." no navegador.)
_RESUMO = {
    "ok": True, "empresa": "Duda Prado", "status": "contatado",
    "temperatura": "quente", "temp_cor": "#e0574f",
    "temp_pill": ["#241313", "#F0A8A2"], "vendedor_nome": "JACQUELINE PRIME",
    "parado_txt": "parado há 12 dias", "contato": "Duda Prado",
    "whatsapp": "+5599999038733", "atividades": [], "canais": [],
    "canais_contato": [{"ic": "💬", "label": "WhatsApp", "respondeu": True}],
}

#: O que o navegador roda depois de carregar a página: cala a rede, planta um
#: botão igual ao que a tela monta e clica nele. Sem o stub, o `fetch` do resumo
#: iria pro `file://` e o teste mediria a rede, não a função.
_ROTEIRO = """(resumo) => {
  window.__erros = [];
  window.onerror = function(m){ window.__erros.push(String(m)); };
  // O dublê responde como uma Response DE VERDADE: desde 19/09 quem consome isto
  // é o `zapFetch`, que olha o status, o cabeçalho da versão e o corpo como
  // TEXTO (pra distinguir "não é JSON" de "é JSON dizendo não"). Um dublê com só
  // `{ok, json}` faz a janela cair na caixa de erro — e o teste mediria a caixa
  // de erro achando que mediu a janela.
  window.fetch = function(){
    return Promise.resolve({
      ok: true, status: 200,
      headers: {get: function(){ return null; }},
      text: function(){ return Promise.resolve(JSON.stringify(resumo)); },
      json: function(){ return Promise.resolve(resumo); },
    });
  };
  var d = document.createElement('div');
  d.style.cssText = 'position:fixed;top:120px;left:120px';
  d.innerHTML = '<button type="button" id="zaq-alvo">Abrir ficha</button>';
  document.body.appendChild(d);
  try { kbAbrirLead(null, 7, document.getElementById('zaq-alvo')); }
  catch (e) { window.__erros.push(String(e)); }
  return true;
}"""

#: Um ✕ redondo de canto tem 24px. Acima disto ele não é mais um ✕: é a faixa
#: que o `button{width:100%}` global produz quando ninguém o vence — foi assim
#: que ele apareceu na Comunicação, uma barra verde da largura toda do popover.
_XIS_MAX = 40


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
    pag.evaluate(_ROTEIRO, _RESUMO)
    pag.wait_for_timeout(350)
    fora = {
        "popup": pag.evaluate("() => !!document.querySelector('.leadpop')"),
        "erros": quebras + (pag.evaluate("() => window.__erros || []") or []),
        "medidas": pag.evaluate(
            """() => {
              var pop = document.querySelector('.leadpop');
              if (!pop) return null;
              var x = pop.querySelector('.pop-close');
              var chips = pop.querySelector('.lp-chips');
              return {
                larg: Math.round(pop.getBoundingClientRect().width),
                xis: x ? Math.round(x.getBoundingClientRect().width) : null,
                chips_estouram: chips ? chips.scrollWidth > chips.clientWidth + 1 : false,
                fora_da_tela: pop.getBoundingClientRect().right > innerWidth + 1
                              || pop.getBoundingClientRect().left < -1,
                texto: (pop.innerText || '').slice(0, 60),
              };
            }"""),
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
    m = r["medidas"]
    assert "Falha de rede" not in m["texto"], (
        f"{tela}: a janela abriu na caixa de erro, não no resumo — {m['texto']!r}")
    assert "Duda Prado" in m["texto"], f"{tela}: a janela não desenhou o lead"


@pytest.mark.parametrize("tela", TELAS)
def test_o_xis_de_fechar_e_um_xis_e_nao_uma_faixa(navegador, tmp_path, tela):
    """O SEGUNDO defeito de 19/09, mandado pelo dono num print: na Comunicação o
    ✕ era uma BARRA VERDE da largura inteira do popover.

    Mesma raiz do `kbFecharChat`, uma camada abaixo: `.pop-close` morava só no
    `balao_css`, e a Comunicação carrega só `janela_css`. Sem a regra, o botão
    caía no `button{width:100%;margin-top:1.4rem}` global — o mesmo que já mordeu
    o ✕ de excluir do card e o ✕ da busca. Em JS a dependência que falta dá botão
    morto; em CSS, dá faixa.
    """
    m = _abre(navegador, tmp_path, tela)["medidas"]
    assert m and m["xis"] is not None, f"{tela}: a janela abriu sem botão de fechar"
    assert m["xis"] <= _XIS_MAX, (
        f"{tela}: o ✕ está com {m['xis']}px de largura (o popover tem "
        f"{m['larg']}px). Isso não é um ✕, é a faixa do `button{{width:100%}}` "
        f"global — falta `.pop-close` nesta tela.")


@pytest.mark.parametrize("tela", TELAS)
def test_a_janela_cabe_na_tela_e_os_chips_nao_estouram(navegador, tmp_path, tela):
    m = _abre(navegador, tmp_path, tela)["medidas"]
    assert not m["fora_da_tela"], f"{tela}: a janela nasceu pra fora da viewport"
    assert not m["chips_estouram"], (
        f"{tela}: a fileira de situação passa da largura do popover — os chips "
        f"precisam quebrar linha, não sumir pela direita")


def test_o_xis_sobrevive_ao_erro():
    """Os caminhos de erro trocavam o popover inteiro por uma frase — e levavam o
    ✕ junto. Quem batia num erro ficava com uma caixa que só sai no Esc ou no
    clique fora, e ninguém adivinha isso olhando uma frase numa caixa."""
    from web import janela_lead as _janela
    assert "function _leadPopErro(" in _janela.JS
    assert "cx-empty\">Falha de rede." not in _janela.JS, (
        "algum caminho de erro voltou a apagar o botão de fechar junto")
    assert "cx-empty\">Não consegui abrir." not in _janela.JS


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


# ── a janela INTEIRA à vista (25/09/2026) ─────────────────────────────────────
# O print do dono: o "Abrir ficha" do Follow-up fica no canto direito do cartão,
# e com o cartão no meio da tela sobravam ~300px embaixo. A janela abria ali, com
# essa altura — e o topo dela (nome, Ligar/WhatsApp, os dois grupos de situação)
# não encolhe: `overflow:hidden` cortava "Encerrar" e o histórico, sem barra pra
# rolar. E o botão de suporte do WhatsApp (z-index 9999) cobria o canto.

#: um resumo ALTO de propósito: duas atividades e o evento, como o da Liane
_RESUMO_ALTO = dict(_RESUMO, evento_fmt="🎂 Aniversário · 12/02/2028 · 100 convidados",
                    evento_tipo="Aniversário", evento_iso="2028-02-12", evento_convidados=100,
                    valor_fmt="R$ 12.500,00", telefone="86999990000",
                    atividades=[{"tipo_rot": "WhatsApp", "resultado_rot": "Interessado",
                                 "descricao": "Mandou a lista de convidados", "cor": "#3ee0a6",
                                 "quando": "21/09 10:12"},
                                {"tipo_rot": "Ligação", "resultado_rot": "Retornar",
                                 "descricao": "Pediu pra ligar depois das 18h", "cor": "#e0b45f",
                                 "quando": "19/09 16:40"}])

_ABRE_EM = """(a) => {
  window.fetch = function(){
    return Promise.resolve({ok: true, status: 200, headers: {get: function(){ return null; }},
      text: function(){ return Promise.resolve(JSON.stringify(a.resumo)); }});
  };
  if (!document.querySelector('.wa-suporte')) {
    var w = document.createElement('a'); w.className = 'wa-suporte'; document.body.appendChild(w);
  }
  var d = document.createElement('div');
  d.style.cssText = 'position:fixed;top:' + a.top + 'px;left:' + a.left + 'px';
  d.innerHTML = '<button type="button" id="zaq-alvo" style="width:auto;margin:0">Abrir ficha</button>';
  document.body.appendChild(d);
  kbAbrirLead(null, 1438, document.getElementById('zaq-alvo'));
  return true;
}"""

_MEDE = """() => {
  var pop = document.querySelector('.leadpop'), r = pop.getBoundingClientRect();
  var b = document.getElementById('zaq-alvo').getBoundingClientRect();
  var cobre = !(r.right <= b.left || r.left >= b.right || r.bottom <= b.top || r.top >= b.bottom);
  var fim = pop.querySelector('.lp-sit-h.fim');
  var ws = document.querySelector('.wa-suporte');
  return {top: r.top, bottom: r.bottom, left: r.left, right: r.right,
          vh: innerHeight, vw: innerWidth,
          escondido: pop.scrollHeight - pop.clientHeight,
          rola: getComputedStyle(pop).overflowY,
          encerrar: !!fim, texto: pop.innerText,
          suporte: ws ? getComputedStyle(ws).visibility : null, cobre: cobre};
}"""


def _abre_em(navegador, tmp_path, *, top, left, largura=1280, altura=860):
    alvo = tmp_path / "janela_posicao.html"
    alvo.write_text(_render("prospeccao"), encoding="utf-8")
    pag = navegador.new_page(viewport={"width": largura, "height": altura})
    pag.goto(alvo.as_uri())
    pag.wait_for_timeout(200)
    pag.evaluate(_ABRE_EM, {"resumo": _RESUMO_ALTO, "top": top, "left": left})
    pag.wait_for_timeout(350)
    m = pag.evaluate(_MEDE)
    fechou = pag.evaluate("() => { kbFecharLead(); var w = document.querySelector('.wa-suporte');"
                          " return w ? getComputedStyle(w).visibility : null; }")
    pag.close()
    return m, fechou


@pytest.mark.parametrize("onde,top,left", [
    ("o botão do print: canto direito, meio da tela", 505, 1126),
    ("botão colado no pé da tela", 820, 1126),
    ("botão no topo", 60, 300),
    ("botão na esquerda, meio da tela", 430, 40),
])
def test_a_janela_abre_inteira_a_vista(navegador, tmp_path, onde, top, left):
    """Em tela de computador comum a janela cabe inteira — então ela TEM que
    aparecer inteira, de "Liane" até o histórico, sem nada escondido."""
    m, _ = _abre_em(navegador, tmp_path, top=top, left=left)
    assert m["top"] >= 0 and m["bottom"] <= m["vh"] + 0.5, (
        f"{onde}: a janela saiu da tela ({m['top']:.0f}→{m['bottom']:.0f} de {m['vh']})")
    assert m["left"] >= 0 and m["right"] <= m["vw"] + 0.5, f"{onde}: saiu pela lateral"
    assert m["escondido"] <= 1, (
        f"{onde}: {m['escondido']}px da janela ficaram escondidos — é o corte do print")
    assert m["encerrar"] and "Pediu pra ligar" in m["texto"], (
        f"{onde}: 'Encerrar' ou o histórico não chegaram à tela")
    assert not m["cobre"], (
        f"{onde}: a janela abriu POR CIMA do botão que a abriu — o ponteiro fica "
        f"parado em cima de um chip, e um clique duplo muda a situação sem querer")


def test_em_tela_baixa_a_janela_rola_em_vez_de_cortar(navegador, tmp_path):
    """Notebook pequeno com o navegador em janela: nem a tela inteira basta. Aí a
    janela ocupa a altura toda e ROLA — nada fica inalcançável."""
    m, _ = _abre_em(navegador, tmp_path, top=300, left=900, largura=1100, altura=420)
    assert m["top"] >= 0 and m["bottom"] <= m["vh"] + 0.5
    assert m["rola"] == "auto", "janela maior que a tela sem rolagem é a janela cortada"


def test_no_celular_a_janela_cabe_na_largura(navegador, tmp_path):
    m, _ = _abre_em(navegador, tmp_path, top=500, left=250, largura=375, altura=740)
    assert m["left"] >= 0 and m["right"] <= m["vw"] + 0.5
    assert m["top"] >= 0 and m["bottom"] <= m["vh"] + 0.5


def test_o_botao_de_suporte_sai_da_frente_e_volta(navegador, tmp_path):
    m, fechou = _abre_em(navegador, tmp_path, top=505, left=1126)
    assert m["suporte"] == "hidden", "o botão do WhatsApp de suporte cobre o canto da janela"
    assert fechou == "visible", "fechou a janela e o botão de suporte não voltou"


def test_o_lugar_da_janela_e_decidido_depois_de_medir():
    """A guarda na FONTE, que roda no CI (lá não há Chromium e os testes acima
    pulam). A regra velha abria embaixo com a altura que sobrasse
    (`abaixo>=260`) — foi ela que cortou a janela."""
    from web import janela_lead as _janela
    js, css = _janela.JS, _janela.CSS
    assert "abaixo>=260" not in js, "voltou a regra que abria com a altura que sobrava"
    corpo = js[js.index("function kbAbrirLead("):js.index("function kbLeadHtml(")]
    depois = corpo[corpo.index("pop.innerHTML=kbLeadHtml(d,id);"):]
    assert "_leadPopPosiciona(pop)" in depois, (
        "a janela tem que se reposicionar DEPOIS que o resumo chega — é quando ela "
        "ganha a altura de verdade")
    assert "overflow-y:auto" in css[css.index(".leadpop{"):css.index("}", css.index(".leadpop{"))]
    assert "body.lp-aberta .wa-suporte{visibility:hidden}" in css


# ── o RESUMO DA IA (25/09/2026, docs/mockups/funil_resumo_ia.html) ────────────
# A mesma moldura da janela do lead, aberta pelo ⋯ do card. Medido no navegador,
# na página do quadro de verdade, com a rede trocada por um dublê que responde por
# URL e por método — o que se mede é: o GET lê o guardado sem gastar, o POST só
# vai quando não há resumo, a janela fica inteira na tela, e "Usar na conversa"
# SÓ PREENCHE o campo do balão (decisão 1 do dono).

_PACOTE = {
    "ok": True, "lead": {"nome": "Camila Rocha", "etapa": "Negociação", "vendedor": "Jacqueline"},
    "fatos": {"n_total": 23, "n_lidas": 23,
              "bola": {"quem": "voce", "txt": "A bola está com você há 2 dias"}},
    "tem_conversa": True, "conv": {"id": 501, "canal": "whatsapp", "aba": "conversas"},
    "resumo": None, "resumo_id": None, "voto": None, "feito_txt": "", "novas": 0, "ia": True,
    "compromisso": "visita",
}
_RESUMO_IA = {
    "quer": "Contratar o pacote de sábado.",
    "em_que_pe": ["Recebeu a proposta nº 212 em 22/09.", "Perguntou se parcela em 10x."],
    "pode_travar": ["Achou o valor alto.", "Vai ver outro espaço no sábado."],
    "proximo_passo": "Responder o parcelamento hoje.",
    "nao_sei": ["A conversa e o orçamento não dizem se parcela em 10x. Confirme antes de prometer."],
    "mensagem": "Oi, Camila! Sobre o parcelamento, vou confirmar e te respondo hoje. "
                "Que tal vir conhecer o espaço antes de sábado?",
}

_DUBLE_IA = """(a) => {
  window.__rede = [];
  window.__erros = [];
  window.onerror = function(m){ window.__erros.push(String(m)); };
  function resp(o){ return Promise.resolve({ok: true, status: 200,
      headers: {get: function(){ return null; }},
      text: function(){ return Promise.resolve(JSON.stringify(o)); }}); }
  window.fetch = function(url, op){
    var m = ((op && op.method) || 'GET').toUpperCase();
    window.__rede.push(m + ' ' + url);
    if (String(url).indexOf('/comunicacao/thread/') >= 0)
      return resp({ok: true, msgs: [], pode_responder: true, conversa_id: 501});
    if (String(url).indexOf('/resumo-ia') >= 0 && m === 'GET') return resp(a.get);
    if (String(url).indexOf('/resumo-ia') >= 0 && m === 'POST') return resp(a.post);
    return resp({ok: true});
  };
  var d = document.createElement('div');
  d.className = 'kbcard'; d.id = 'card-ia'; d.setAttribute('data-id', '1438');
  d.style.cssText = 'position:fixed;top:' + a.top + 'px;left:' + a.left + 'px;width:240px';
  d.innerHTML = '<span class="emp">Camila Rocha</span>';
  document.body.appendChild(d);
  kbAbrirResumoIA(null, 1438, d);
  return true;
}"""


def _abre_resumo(navegador, tmp_path, *, get, post=None, top=505, left=900,
                 largura=1280, altura=860, depois=None):
    alvo = tmp_path / "resumo_ia.html"
    alvo.write_text(_render("prospeccao"), encoding="utf-8")
    pag = navegador.new_page(viewport={"width": largura, "height": altura})
    quebras: list[str] = []
    pag.on("pageerror", lambda e: quebras.append(str(e)))
    pag.goto(alvo.as_uri())
    pag.wait_for_timeout(200)
    pag.evaluate(_DUBLE_IA, {"get": get, "post": post or get, "top": top, "left": left})
    pag.wait_for_timeout(400)
    fora = pag.evaluate("""() => {
      var pop = document.querySelector('.leadpop.lpia'), c = document.getElementById('card-ia');
      if (!pop) return null;
      var r = pop.getBoundingClientRect(), b = c.getBoundingClientRect();
      var ta = pop.querySelector('#lpia-msg');
      return {top: r.top, bottom: r.bottom, left: r.left, right: r.right, vh: innerHeight, vw: innerWidth,
              escondido: pop.scrollHeight - pop.clientHeight, texto: pop.innerText,
              caixa: ta ? ta.value : null, naosei: pop.querySelectorAll('.lpia-naosei').length,
              cobre: !(r.right <= b.left || r.left >= b.right || r.bottom <= b.top || r.top >= b.bottom),
              rede: window.__rede.slice()};
    }""")
    extra = pag.evaluate(depois) if depois else None
    fora = dict(fora or {}, erros=quebras + (pag.evaluate("() => window.__erros") or []), extra=extra)
    pag.close()
    return fora


def test_o_resumo_guardado_abre_sem_gastar_e_inteiro(navegador, tmp_path):
    m = _abre_resumo(navegador, tmp_path, get=dict(_PACOTE, resumo=_RESUMO_IA, resumo_id=9,
                                                   feito_txt="feito há 2 h"))
    assert not m["erros"], m["erros"]
    assert m["rede"] == ["GET /painel/prospeccao/1438/resumo-ia"], "gastou IA ao reabrir"
    for pedaco in ("Camila Rocha", "A bola está com você há 2 dias", "leu as 23 mensagens",
                   "O que o cliente quer", "Em que pé está", "O que pode travar",
                   "Próximo passo", "Confirme antes de prometer", "Nada é enviado sozinho"):
        assert pedaco.lower() in m["texto"].lower(), pedaco   # os rótulos saem em caixa alta
    assert m["caixa"] == _RESUMO_IA["mensagem"] and m["naosei"] == 1
    assert m["top"] >= 0 and m["bottom"] <= m["vh"] + .5 and m["right"] <= m["vw"] + .5
    assert m["escondido"] <= 1, "a janela do resumo cortou conteúdo"
    assert not m["cobre"], "a janela abriu por cima do card"


def test_sem_resumo_guardado_a_janela_pede_um(navegador, tmp_path):
    m = _abre_resumo(navegador, tmp_path, get=_PACOTE,
                     post=dict(_PACOTE, resumo=_RESUMO_IA, resumo_id=9, feito_txt="feito há 1 min"))
    assert not m["erros"], m["erros"]
    assert m["rede"] == ["GET /painel/prospeccao/1438/resumo-ia",
                         "POST /painel/prospeccao/1438/resumo-ia"]
    assert m["caixa"] == _RESUMO_IA["mensagem"]


def test_chegou_mensagem_nova_avisa_e_nao_gasta_sozinho(navegador, tmp_path):
    m = _abre_resumo(navegador, tmp_path, get=dict(_PACOTE, resumo=_RESUMO_IA, resumo_id=9, novas=2,
                                                   feito_txt="feito há 3 h"))
    assert "2 mensagens novas depois deste resumo" in m["texto"] and "Atualizar" in m["texto"]
    assert len(m["rede"]) == 1


def test_sem_conversa_oferece_a_primeira_mensagem(navegador, tmp_path):
    m = _abre_resumo(navegador, tmp_path, get=dict(_PACOTE, tem_conversa=False,
                                                   fatos={"n_total": 0, "n_lidas": 0, "bola": {}}))
    assert not m["erros"], m["erros"]
    assert "ainda não trocou mensagem" in m["texto"] and "Sugerir a primeira mensagem" in m["texto"]
    assert len(m["rede"]) == 1, "chamou a IA de resumo sem conversa"


def test_usar_na_conversa_so_preenche_o_balao(navegador, tmp_path):
    """Decisão 1: o texto vai pro campo do balão; ninguém envia. `kbResponderChat`
    (o envio) não pode ser chamado, e o texto tem que estar inteiro no campo."""
    m = _abre_resumo(navegador, tmp_path, get=dict(_PACOTE, resumo=_RESUMO_IA, resumo_id=9),
                     depois="""() => new Promise(function(ok){
                       window.__enviou = 0;
                       var orig = window.kbResponderChat;
                       window.kbResponderChat = function(){ window.__enviou++; };
                       document.querySelector('.leadpop.lpia .lpia-bt.pri').click();
                       setTimeout(function(){
                         var ta = document.getElementById('cp-input');
                         ok({campo: ta ? ta.value : null, enviou: window.__enviou,
                             janela: !!document.querySelector('.leadpop.lpia'),
                             rede: window.__rede.slice()});
                       }, 500);
                     })""")
    assert not m["erros"], m["erros"]
    e = m["extra"]
    assert e["campo"] == _RESUMO_IA["mensagem"], "o texto não chegou ao campo do balão"
    assert e["enviou"] == 0 and not e["janela"]
    assert "POST /painel/prospeccao/1438/resumo-ia/9/usado" in e["rede"]
    assert not any("responder" in u for u in e["rede"]), "mandou mensagem"


def test_no_celular_o_resumo_cabe_inteiro(navegador, tmp_path):
    m = _abre_resumo(navegador, tmp_path, get=dict(_PACOTE, resumo=_RESUMO_IA, resumo_id=9),
                     top=420, left=20, largura=390, altura=780)
    assert m["left"] >= 0 and m["right"] <= m["vw"] + .5
    assert m["top"] >= 0 and m["bottom"] <= m["vh"] + .5
