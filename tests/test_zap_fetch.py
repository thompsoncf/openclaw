"""O zapFetch: um caminho só pra falar com o servidor, medido num navegador.

POR QUE ESTE ARQUIVO EXISTE. O painel dizia "Falha de rede." pra CINCO coisas
diferentes — rede caída, deploy em andamento, sessão expirada, 500 do servidor e
aba com o JS de antes do deploy — e não registrava nenhuma. Foi por isso que "o
que houve às 19:33 do dia 15?" não teve resposta.

Cada caso aqui é um teste, porque o valor do módulo é EXATAMENTE distinguir os
cinco. Um teste só de "mostrou algum aviso" passaria verde com o código velho.

E a medição é no NAVEGADOR, com `fetch` trocado por um dublê, porque o que se
está testando é comportamento de promessa: o que o chamador recebe, quantas vezes
a rede foi tentada, e o que ficou escrito na tela. Ler o .js não responde nada
disso — foi lendo o .py que eu deixei subir um botão morto em 19/09.

Pula sozinho onde não houver Chromium, como o test_cockpit_rodape_render. A rede
de CI é o `test_a_janela_nao_usa_mais_fetch_cru`, que roda em qualquer lugar.
"""
from __future__ import annotations

import json
import os

import pytest

from web import zap_fetch as zf

#: A página mínima: o CSS, o JS e a versão que "esta aba carregou". Nada de
#: template — o módulo tem que se sustentar sozinho, que é a razão de ele viver
#: no BASE e não em cada tela.
_PAGINA = """<!doctype html><meta charset="utf-8">
<style>%s</style>
<script>window.ZAQ_VERSAO='v1';</script>
<script>%s</script>
""" % (zf.CSS, zf.JS)

#: O dublê de `fetch`. `plano` é uma lista de respostas, consumida na ordem: cada
#: item é {status, corpo, versao} ou {erro:1} pra "o fetch nem saiu". Uma LISTA e
#: não um valor único porque a retentativa é metade do que se está medindo.
_DUBLE = """(plano) => {
  window.__chamadas = 0;
  window.__registros = [];
  window.fetch = function(url, op){
    // o registro do ocorrido vai pra uma rota própria e não conta como tentativa
    if(String(url).indexOf('/painel/erro-cliente') === 0){
      window.__registros.push(JSON.parse((op && op.body) || '{}'));
      return Promise.resolve({ok:true, status:200, headers:{get:function(){return null;}},
                              text:function(){return Promise.resolve('{}');}});
    }
    var i = Math.min(window.__chamadas, plano.length - 1);
    window.__chamadas++;
    var p = plano[i];
    if(p.erro) return Promise.reject(new TypeError('Failed to fetch'));
    return Promise.resolve({
      ok: p.status >= 200 && p.status < 300,
      status: p.status,
      headers: {get: function(h){ return h === 'X-Zaq-Versao' ? (p.versao || null) : null; }},
      text: function(){ return Promise.resolve(p.corpo == null ? '' : p.corpo); },
    });
  };
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


@pytest.fixture()
def roda(navegador, tmp_path):
    """Roda um zapFetch contra um plano de respostas e devolve o que aconteceu."""
    alvo = tmp_path / "zap.html"
    alvo.write_text(_PAGINA, encoding="utf-8")

    def _r(plano, op=None, espera_ms=0):
        pag = navegador.new_page()
        quebras: list[str] = []
        pag.on("pageerror", lambda e: quebras.append(str(e)))
        pag.goto(alvo.as_uri())
        pag.evaluate(_DUBLE, plano)
        pag.evaluate(
            "(op) => { window.__d = 'pendente';"
            "  zapFetch('/alvo', op).then(function(d){ window.__d = d; }); }",
            op or {})
        pag.wait_for_timeout(espera_ms or 200)
        fora = {
            "d": pag.evaluate("() => window.__d"),
            "chamadas": pag.evaluate("() => window.__chamadas"),
            "registros": pag.evaluate("() => window.__registros"),
            "aviso": pag.evaluate(
                "() => { var a = document.querySelector('.zap-aviso');"
                "        return a ? a.innerText.replace(/\\s+/g,' ').trim() : ''; }"),
            "acao": pag.evaluate(
                "() => { var b = document.querySelector('.zap-aviso .bt');"
                "        return b ? b.textContent : ''; }"),
            "quebras": quebras,
        }
        pag.close()
        return fora

    return _r


OK = {"status": 200, "corpo": json.dumps({"ok": True, "n": 7})}


# ── o caminho feliz ──────────────────────────────────────────────────────────

def test_resposta_boa_volta_inteira_e_sem_aviso(roda):
    r = roda([OK])
    assert r["d"] == {"ok": True, "n": 7}
    assert r["aviso"] == "", "não houve falha nenhuma; não se avisa nada"
    assert r["chamadas"] == 1


def test_um_NAO_do_servidor_nao_e_falha_do_zapFetch(roda):
    """`{ok:false, erro:'motivo_obrigatorio', motivos:[…]}` vem com HTTP 400 e é
    JUSTAMENTE o que a folha "por que perdeu" precisa ler. Se o zapFetch engolisse
    o 400, a folha morreria — e ela nasceu no #712 porque o painel fazia isso."""
    corpo = json.dumps({"ok": False, "erro": "motivo_obrigatorio",
                        "motivos": [{"chave": "preco", "rotulo": "Preço"}]})
    r = roda([{"status": 400, "corpo": corpo}])
    assert r["d"]["erro"] == "motivo_obrigatorio"
    assert r["d"]["motivos"][0]["chave"] == "preco"
    assert r["aviso"] == "", "o servidor respondeu e decidiu — quem avisa é a tela"


# ── as cinco causas, uma a uma ───────────────────────────────────────────────

def test_1_sem_internet_diz_sem_internet(roda):
    r = roda([{"erro": 1}], op={"method": "POST"})
    assert r["d"] is None
    assert "sem internet" in r["aviso"].lower() or "não consegui falar" in r["aviso"].lower()


def test_2_deploy_em_andamento_TENTA_DE_NOVO_sozinho(roda):
    """502/503/504 é a janela do deploy: o Render derruba e sobe o serviço em
    segundos. Na maioria das vezes a pessoa não vê aviso nenhum — o toque só
    demora um pouco mais. É o caso mais provável do "Falha de rede." que o dono
    viu, e o único dos cinco que se resolve sozinho."""
    r = roda([{"status": 503, "corpo": "<html>service unavailable</html>"}, OK],
             espera_ms=3200)
    assert r["chamadas"] >= 2, "não tentou de novo — o deploy vira erro pra pessoa"
    assert r["d"] == {"ok": True, "n": 7}, "a segunda tentativa deu certo e o valor tem que chegar"


def test_2b_a_retentativa_nao_e_infinita(roda):
    r = roda([{"status": 503, "corpo": "nope"}], espera_ms=9000)
    assert r["chamadas"] == 3, "1 tentativa + 2 retentativas, e para"
    assert r["d"] is None
    assert "erro do nosso lado" in r["aviso"].lower()


def test_2c_POST_nao_e_repetido_sozinho(roda):
    """Repetir um POST que o servidor PODE ter processado cria duas vezes a mesma
    coisa. A retentativa automática é só pra método seguro; num POST o aviso
    aparece e quem decide repetir é a pessoa."""
    r = roda([{"status": 503, "corpo": "nope"}], op={"method": "POST"}, espera_ms=600)
    assert r["chamadas"] == 1, "repetiu um POST sozinho"


def test_3_sessao_expirada_oferece_entrar_de_novo(roda):
    """401 traz JSON (`{ok:false, erro:'login'}`), mas devolver isso faria a tela
    dizer "não consegui salvar (login)". Sessão expirada tem um caminho só."""
    r = roda([{"status": 401, "corpo": json.dumps({"ok": False, "erro": "login"})}])
    assert r["d"] is None
    assert "sessão expirou" in r["aviso"].lower()
    assert r["acao"] == "Entrar de novo"


def test_4_erro_do_servidor_avisa_E_REGISTRA(roda):
    """O registro é o motivo de este módulo vir antes das outras 110 chamadas:
    sem ele, "o que houve às 19:33?" continua sem resposta."""
    r = roda([{"status": 500, "corpo": "Internal Server Error"}], op={"method": "POST"})
    assert r["d"] is None
    assert "erro do nosso lado" in r["aviso"].lower()
    assert len(r["registros"]) == 1, "o ocorrido não foi registrado"
    reg = r["registros"][0]
    assert reg["status"] == 500 and reg["metodo"] == "POST"
    assert "Internal Server Error" in reg["corpo"]


def test_5_aba_de_antes_do_deploy_oferece_recarregar(roda):
    """A aba carregou com 'v1'; a resposta vem carimbada 'v2'. O JS daqui pode não
    entender mais o que vem — e um corpo novo lido por um JS velho é o pior dos
    dois mundos. Vale ANTES de olhar o corpo, de propósito."""
    r = roda([{"status": 200, "corpo": json.dumps({"ok": True}), "versao": "v2"}])
    assert r["d"] is None
    assert "desatualizada" in r["aviso"].lower()
    assert r["acao"] == "Recarregar"


def test_a_versao_igual_nao_incomoda_ninguem(roda):
    r = roda([{"status": 200, "corpo": json.dumps({"ok": True}), "versao": "v1"}])
    assert r["d"] == {"ok": True} and r["aviso"] == ""


# ── o que NÃO se registra ────────────────────────────────────────────────────

def test_nao_registra_sessao_expirada_nem_resposta_normal(roda):
    """401 é rotina — registrar daria uma linha por aba esquecida durante a noite.
    E `{ok:false}` é conversa normal, não ocorrido."""
    assert roda([{"status": 401, "corpo": "{}"}])["registros"] == []
    assert roda([{"status": 400, "corpo": json.dumps({"ok": False})}])["registros"] == []


# ── a tela ───────────────────────────────────────────────────────────────────

def test_o_aviso_de_erro_NAO_some_sozinho(roda):
    """Um recado que some antes de ser lido é o mesmo que não ter recado. O que
    deu certo some; o que deu errado fica até a pessoa mandar embora."""
    r = roda([{"status": 500, "corpo": "boom"}], op={"method": "POST"}, espera_ms=5000)
    assert r["aviso"], "o aviso de erro sumiu sozinho"


def test_o_texto_do_servidor_entra_como_TEXTO_e_nao_como_html(navegador, tmp_path):
    """O aviso carrega pedaço de resposta do servidor. Resposta de servidor não
    entra no DOM como HTML — nem a nossa, que amanhã pode ecoar algo de fora."""
    alvo = tmp_path / "zap.html"
    alvo.write_text(_PAGINA, encoding="utf-8")
    pag = navegador.new_page()
    pag.goto(alvo.as_uri())
    pag.evaluate("() => zapAviso('<img src=x onerror=\"window.__xss=1\">', {detalhe:'<b>oi</b>'})")
    pag.wait_for_timeout(150)
    assert pag.evaluate("() => window.__xss") is None
    assert pag.evaluate("() => document.querySelector('.zap-aviso img')") is None
    pag.close()


# ── a rede que roda no CI (sem navegador) ────────────────────────────────────

def test_a_janela_nao_usa_mais_fetch_cru():
    """A janela do lead foi a primeira a migrar — é onde o dono bateu no erro.
    Um `fetch(` cru que volte ali traz junto o `.catch` com a frase única."""
    import re

    from web import janela_lead as _janela
    cruas = [l for l in _janela.JS.splitlines()
             if re.search(r"(?<![.\w])fetch\s*\(", re.sub(r"(?<!:)//.*$", "", l))
             and "zapFetch" not in l]
    assert not cruas, "voltou `fetch` cru na janela do lead:\n  " + "\n  ".join(cruas)


def test_a_janela_nao_alerta_mais_falha_de_rede():
    from web import janela_lead as _janela
    assert "Falha de rede" not in _janela.JS, (
        "a frase única voltou — quem diz o que houve agora é o zapFetch")


def test_o_zap_entra_no_template_BASE_e_nao_tela_a_tela():
    """Em 19/09 duas telas quebraram no mesmo dia por usarem algo que só existia
    porque OUTRA tela tinha carregado o módulo. Infraestrutura que toda tela usa
    mora no BASE."""
    from web import portal as _portal
    assert "{{ zap_js }}" in _portal._BASE and "{{ zap_css }}" in _portal._BASE
    assert "window.ZAQ_VERSAO=" in _portal._BASE, (
        "sem a versão da aba o zapFetch não sabe dizer que ela ficou pra trás")


def test_o_servidor_carimba_a_versao_em_toda_resposta():
    import inspect

    from web import app as _app
    fonte = inspect.getsource(_app._cabecalhos_seguranca)
    assert "X-Zaq-Versao" in fonte, (
        "sem o carimbo, o desencontro de versão só apareceria no próximo minuto "
        "redondo, pela faixa — e não no toque que falhou")


# ── a rota que guarda o ocorrido (migração 297) ──────────────────────────────

_MIG = __import__("pathlib").Path(__file__).resolve().parent.parent / "db" / "migracoes"


@pytest.fixture()
def banco(monkeypatch):
    """Um Postgres descartável com a 297 aplicada — a migração DE VERDADE, que é
    o único jeito de o teste perceber se a tabela não chegou em produção."""
    from psycopg_pool import ConnectionPool
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    nome = "zaq_erro_cliente_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s", (nome,))
        c.execute(f"drop database if exists {nome}")
        c.execute(f"create database {nome}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + nome
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute((_MIG / "297_erro_cliente.sql").read_text(encoding="utf-8"))
        c.commit()
    from web import portal as _portal
    monkeypatch.setattr(_portal, "get_pool", lambda: p)
    yield p
    p.close()


def _cliente():
    from fastapi.testclient import TestClient
    from web.app import app
    return TestClient(app, follow_redirects=False)


def _linhas(pool):
    with pool.connection() as c:
        return c.execute("select url, metodo, status, corpo, versao_aba, conta_id "
                         "from erro_cliente order by id").fetchall()


def test_a_rota_guarda_o_ocorrido(banco):
    r = _cliente().post("/painel/erro-cliente", json={
        "url": "/painel/prospeccao/7/status", "metodo": "POST", "status": 500,
        "corpo": "Internal Server Error", "versao_aba": "abc123"})
    assert r.status_code == 200 and r.json()["ok"] is True
    linhas = _linhas(banco)
    assert len(linhas) == 1
    assert linhas[0][0] == "/painel/prospeccao/7/status"
    assert linhas[0][1] == "POST" and linhas[0][2] == 500
    assert "Internal Server Error" in linhas[0][3]
    assert linhas[0][4] == "abc123"
    # sem sessão a linha não tem dono, e AINDA VALE: `url` e `status` são o que
    # responde "o que houve às 19:33"
    assert linhas[0][5] is None


def test_a_rota_NUNCA_estoura(banco):
    """Ela existe pra explicar falha. Se ela própria virar fonte de erro, o
    navegador tentaria registrar o erro do registro."""
    c = _cliente()
    assert c.post("/painel/erro-cliente", content=b"nao sou json",
                  headers={"Content-Type": "application/json"}).status_code == 200
    assert c.post("/painel/erro-cliente", json=["lista", "nao", "dict"]).status_code == 200
    assert c.post("/painel/erro-cliente", json={}).status_code == 200
    assert c.post("/painel/erro-cliente", json={"url": "/x", "status": "nao é número"}
                  ).status_code == 200


def test_o_corpo_gigante_e_cortado(banco):
    _cliente().post("/painel/erro-cliente",
                    json={"url": "/x" * 900, "corpo": "y" * 5000, "status": 500})
    linhas = _linhas(banco)
    assert len(linhas[0][0]) <= 400 and len(linhas[0][3]) <= 300


def test_um_laco_na_tela_nao_vira_um_laco_no_banco(banco):
    """Uma tela que entre em erro dentro de um `setInterval` mandaria um registro
    por segundo — e o que era pra ser a trilha do incidente vira o incidente."""
    from web import portal as _portal
    _portal._erro_janela.clear()
    c = _cliente()
    for _ in range(_portal._ERRO_TETO + 15):
        c.post("/painel/erro-cliente", json={"url": "/x", "status": 500})
    assert len(_linhas(banco)) == _portal._ERRO_TETO


# ── a segunda leva: o painel inteiro (20/09/2026) ────────────────────────────
# A primeira leva migrou só a janela do lead. Esta migrou 125 chamadas em 9
# arquivos. O que segura daqui pra frente é a varredura abaixo: um `fetch` cru
# novo numa tela que TEM o zapFetch volta a ser um "Falha de rede." sem causa.

#: Onde o zapFetch EXISTE: telas servidas pelo template BASE do painel
#: (web/portal.py), mais os módulos e blobs de JS que só são injetados nelas.
ARQUIVOS_DO_PAINEL = (
    "painel_prospeccao", "portal", "painel_servicos", "painel_agenda",
    "painel_apolices", "painel_conteudo", "balao_conversa", "painel_follow_up",
    "painel_aditivo", "janela_lead",
)

#: Onde ele NÃO existe — e por isso o `fetch` cru ali é o certo, não uma dívida.
#: Cada um destes monta o próprio HTML ou vive noutro Environment do Jinja.
#: Migrar sem levar o módulo junto daria `ReferenceError: zapFetch is not
#: defined`, que é o botão morto de 19/09 outra vez.
FORA_DO_PAINEL = {
    "painel_cockpit": "o app do vendedor monta o HTML na mão e tem service worker",
    "admin": 'vive noutro Environment do Jinja (o "abase")',
    "admin_precos": 'idem — noutro Environment, com o "abase"',
}

#: Blocos com shell próprio DENTRO do portal: a loja pública, o holerite, a
#: etiqueta, o PDF e a tela de revisão não passam pelo BASE do painel.
BLOCOS_SEM_BASE = {"_LOJA", "_REVISAR", "_HOLERITE", "_ETIQUETA_FORN", "_RELATORIO_PDF"}

#: Formas que NÃO são "pede JSON e usa": sonda de cabeçalho, corpo em texto,
#: `await`. Não têm o `.catch` com a frase única e não ganham nada com a troca.
_OUTRAS_FORMAS = ("method:'HEAD'", ".text()", "await ")

#: O QUE FICOU DE FORA DA SEGUNDA LEVA, por linha e com o motivo escrito. Não é
#: lista de dívida a ser encolhida no susto: cada uma destas tem uma razão de
#: continuar crua, e tirar uma daqui sem tratar a razão é reintroduzir um defeito.
FORA_COM_MOTIVO = {
    ("portal", "/painel/versao"):
        "a FAIXA de 'tem versão nova'. O zapFetch compara a versão da aba com o "
        "carimbo da resposta pra avisar 'aba desatualizada' — e é justamente "
        "quando as versões DIFEREM que esta chamada precisa responder em paz, "
        "pra a faixa (que é gentil, com 'Depois') fazer o trabalho dela. "
        "Passá-la pelo zapFetch trocaria a faixa por um aviso de erro.",
    ("painel_servicos", "{ok:r.ok"):
        "a família `{ok:r.ok, d:d}` da aba de Serviços — 11 chamadas. Aqui `res.ok` "
        "é o STATUS HTTP, e o corpo dessas rotas NÃO traz `ok`: elas sinalizam "
        "falha por status + `{erro:…}` e sucesso por `JSONResponse(r)`. Trocar "
        "por zapFetch faria `res.ok` virar um campo que não existe — e todo "
        "salvamento bem-sucedido passaria a dizer 'não consegui salvar'. O "
        "caminho certo é dar ao zapFetch um jeito de devolver o status, ou pôr "
        "`ok` no corpo dessas rotas. Uma coisa ou outra, não de carona nesta leva.",
}


def _sitios_crus(mod: str):
    """Os `fetch(` crus de um módulo, com o bloco em que caíram."""
    import importlib
    import re

    m = importlib.import_module(f"web.{mod}")
    txt = open(m.__file__, encoding="utf-8").read()
    defs = [(x.start(), x.group(1))
            for x in re.finditer(r"^(_[A-Z0-9_]+)\s*=\s*(?:r?\"\"\"|r?'''|\()", txt, re.M)]
    fora = []
    for x in re.finditer(r"(?<![.\w])fetch\s*\(", txt):
        bloco = max([(i, n) for i, n in defs if i < x.start()], default=(0, "(topo)"))[1]
        if bloco in BLOCOS_SEM_BASE:
            continue
        trecho = txt[x.start():x.start() + 300]
        if ".json()" not in trecho or any(f in trecho for f in _OUTRAS_FORMAS):
            continue
        if any(m == mod and marca in trecho for (m, marca) in FORA_COM_MOTIVO):
            continue
        linha = txt[:x.start()].count("\n") + 1
        fora.append(f"{mod}.py:{linha} ({bloco}) {trecho.splitlines()[0][:80]}")
    return fora


def test_nenhuma_tela_do_painel_volta_a_usar_fetch_cru():
    """A trava da segunda leva.

    `fetch(url).then(r => r.json())` numa tela que tem o zapFetch é uma chamada
    que, quando falhar, vai dizer "Falha de rede." pras cinco causas de novo —
    ou, pior, não dizer nada: metade delas nem tinha `.catch`.
    """
    achados = []
    for mod in ARQUIVOS_DO_PAINEL:
        achados += _sitios_crus(mod)
    assert not achados, (
        f"{len(achados)} chamada(s) crua(s) numa tela que tem o zapFetch:\n  "
        + "\n  ".join(achados)
        + "\n\nTroque por `zapFetch(url, op).then(function(d){ if(!d) return; … })`.")


def test_o_que_ficou_de_fora_ficou_por_um_motivo():
    """As três telas fora da leva não são esquecimento, e este teste é onde o
    motivo mora. Se um dia uma delas passar a carregar o módulo, é aqui que o
    registro sai — e aí ela entra na varredura de cima."""
    from web import portal as _portal
    for mod, motivo in FORA_DO_PAINEL.items():
        assert motivo, mod
        assert _sitios_crus(mod), (
            f"{mod} não tem mais `fetch` cru — ou ela ganhou o zapFetch (e "
            f"então mude de lista), ou o recorte desta varredura mudou.")
    # e a razão de elas estarem fora: o módulo entra por UM lugar só
    assert "{{ zap_js }}" in _portal._BASE
