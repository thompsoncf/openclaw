"""A tela da classificação da conta a pagar — o formulário, a edição e a memória.

O que se prova aqui, e por quê:

  * **a edição não apaga em silêncio.** Um título cujo centro foi DESATIVADO depois
    perderia a opção no select, que cairia em "— sem —"; salvar pra corrigir o
    valor apagaria o centro. A opção atual tem que estar lá, marcada;
  * **os marcadores de presença** (`tem_plano`, `tem_centro`) só existem quando o
    select existe — senão o campo ausente chegaria vazio, e vazio apaga;
  * **a memória só preenche o que a pessoa não escolheu**, e o que ela lembrou pro
    fornecedor anterior sai quando o fornecedor muda. Isto roda no Chromium: a
    lição de 19/09/2026 é que "a função está na página" não prova nada.
"""
from __future__ import annotations

import json
import os
import re
from datetime import date, timedelta

import pytest

from tests.test_portal_js_sintaxe import PAGINAS, _BASE_CTX, _CONTA
from web import portal as pt

HOJE = date.today()
_PLANO = [{"grupo": 5, "nome": "Despesas Operacionais", "contas": [
    {"id": 10, "codigo": "5.1.01", "nome": "Aluguel e Condomínio"},
    {"id": 11, "codigo": "5.1.10", "nome": "Serviços Terceirizados"}]}]
_CENTROS = [{"id": 1, "nome": "DESPESA FIXA"}, {"id": 2, "nome": "DESPESA EVENTUAL"},
            {"id": 3, "nome": "INVESTIMENTO"}]


def _titulo(**kw):
    t = {"id": 7, "tipo": "pagar", "descricao": "ALUGUEL", "contraparte": "IMOB X",
         "valor_centavos": 150000, "vencimento": HOJE + timedelta(days=5),
         "status": "aberto", "recorrente": False, "categoria": "Fornecedores",
         "cobranca_link_url": None, "pago_em": None, "lancamento_id": None,
         "cliente_id": None, "cliente_nome": None, "acrescimo_centavos": 0,
         "atrasado": False, "aprovacao": "autorizado", "aprovacao_motivo": None,
         "pago_sem_autorizacao": False, "criado_nome": "", "aprovado_nome": "",
         "aprovado_em": None, "periodicidade": None, "valor_variavel": False,
         "proxima": None, "sem_fornecedor": False, "conciliar": None, "prazo": "",
         "plano_conta_id": None, "plano_codigo": "", "plano_nome": "",
         "centro_custo_id": None, "centro_nome": ""}
    t.update(kw)
    return t


def _render(titulos, plano=_PLANO, centros=_CENTROS):
    from finance import empresa as emp
    blocos = [{"titulo": "✅ Liberadas — pode pagar", "cor": "ok", "decide": False,
               "itens": titulos, "centavos": 0, "dica": ""},
              {"titulo": "⏳ Esperando liberação", "cor": "esp", "decide": True,
               "itens": [], "centavos": 0, "dica": ""},
              {"titulo": "A receber", "cor": "rec", "decide": False, "itens": [],
               "centavos": 0, "dica": ""}]
    ctx = dict(_BASE_CTX)
    ctx.update(PAGINAS["empresa"])
    ctx.update(titulos=titulos, tit_blocos=blocos,
               tit_atrasadas=pt._lente_atrasadas(blocos), pode_liberar=True,
               RITMOS=[], RITMO_SELO={}, hoje_iso=HOJE.isoformat(),
               MULTA_ATRASO_PCT=2, JUROS_MORA_PCT_MES=1,
               plano_opcoes=plano, centros_ativos=centros,
               CAT_TITULO={"pagar": emp.categorias_titulo("pagar"),
                           "receber": emp.categorias_titulo("receber")})
    return pt._env.get_template("empresa").render(conta=_CONTA, **ctx)


def _form_edicao(html, tid=7):
    i = html.index(f'action="/painel/empresa/titulo/{tid}/descricao"')
    return html[i:html.index("</form>", i)]


# ═══════════════════════════════════════════ a edição (sem navegador)

def test_a_edicao_mostra_o_que_ja_esta_classificado():
    f = _form_edicao(_render([_titulo(plano_conta_id=11, plano_codigo="5.1.10",
                                      centro_custo_id=1, centro_nome="DESPESA FIXA")]))
    assert re.search(r'<option value="11" selected>5\.1\.10', f)
    assert re.search(r'<option value="1" selected>DESPESA FIXA', f)


def test_centro_desativado_continua_marcado_e_nao_vira_sem():
    f = _form_edicao(_render([_titulo(centro_custo_id=99, centro_nome="ANTIGO")]))
    assert '<option value="99" selected>ANTIGO (inativo)</option>' in f


def test_plano_desligado_continua_marcado_e_nao_vira_sem():
    f = _form_edicao(_render([_titulo(plano_conta_id=55, plano_codigo="5.1.03",
                                      plano_nome="Marketing")]))
    assert '<option value="55" selected>5.1.03 Marketing (desligada)</option>' in f


def test_sem_plano_ligado_o_marcador_nao_vai():
    """Sem o select, o campo chega vazio — e sem `tem_plano` o servidor não o lê
    como "apagar"."""
    f = _form_edicao(_render([_titulo()], plano=[], centros=[]))
    assert 'name="tem_plano"' not in f and 'name="tem_centro"' not in f
    com = _form_edicao(_render([_titulo()]))
    assert 'name="tem_plano"' in com and 'name="tem_centro"' in com


def test_a_linha_diz_a_classificacao_quando_existe():
    html = _render([_titulo(plano_codigo="5.1.10", plano_nome="Serviços Terceirizados",
                            centro_nome="DESPESA FIXA", plano_conta_id=11,
                            centro_custo_id=1)])
    assert "5.1.10 Serviços Terceirizados · DESPESA FIXA" in html


def test_a_rota_de_editar_so_le_a_classe_com_os_marcadores():
    """A regra que protege o centro de um form antigo mora na rota: sem
    `tem_classe`, nada; sem `tem_plano`/`tem_centro`, aquele campo não."""
    import inspect
    src = inspect.getsource(pt.empresa_titulo_descricao)
    assert 'plano_conta_id.strip() if tem_plano else None' in src
    assert 'centro_custo_id.strip() if tem_centro else None' in src
    assert 'if tem_classe in ("1", "on", "true"):' in src


# ═══════════════════════════════════════════ a memória (navegador)

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


#: O servidor dublado: cada fornecedor tem (ou não) uma lembrança.
_MEMORIAS = {
    "IMOB X": {"categoria": "Moradia", "plano_conta_id": 10, "centro_custo_id": 1,
               "de": "ALUGUEL AGOSTO"},
    "PEDREIRO": {"categoria": "Construcao", "plano_conta_id": 11, "centro_custo_id": 3,
                 "de": "REFORMA"},
}
_DUBLE = """(mem) => {
  window.__chamadas = [];
  window.fetch = function(url){
    window.__chamadas.push(String(url));
    var cp = decodeURIComponent((String(url).match(/contraparte=([^&]*)/) || [,''])[1]);
    var corpo = {memoria: mem[cp] || null};
    return Promise.resolve({ok: true, status: 200, headers: {get: () => null},
      text: () => Promise.resolve(JSON.stringify(corpo)),
      json: () => Promise.resolve(corpo)});
  };
}"""


def _pagina(navegador, tmp_path):
    alvo = tmp_path / "empresa.html"
    alvo.write_text(_render([_titulo()]), encoding="utf-8")
    pag = navegador.new_page(viewport={"width": 1200, "height": 900})
    erros: list[str] = []
    pag.on("pageerror", lambda e: erros.append(str(e)))
    pag.goto(alvo.as_uri())
    pag.evaluate(_DUBLE, _MEMORIAS)
    return pag, erros


def _fornecedor(pag, nome):
    pag.fill("#tit-cli-input", nome)
    pag.dispatch_event("#tit-cli-input", "change")
    pag.wait_for_timeout(120)


def _estado(pag):
    return pag.evaluate("""() => Object.fromEntries(
      ['tit-plano','tit-centro','tit-cat-pagar'].map(id => {
        const s = document.getElementById(id);
        return [id, [s.value, s.classList.contains('lembrado')]]; }))""")


def test_a_memoria_preenche_e_marca_o_que_lembrou(navegador, tmp_path):
    pag, erros = _pagina(navegador, tmp_path)
    try:
        _fornecedor(pag, "IMOB X")
        assert _estado(pag) == {"tit-plano": ["10", True], "tit-centro": ["1", True],
                                "tit-cat-pagar": ["Moradia", True]}
        assert "ALUGUEL AGOSTO" in pag.inner_text("#tit-mem-dica")
        assert not erros, erros
    finally:
        pag.close()


def test_a_escolha_da_pessoa_nao_e_trocada_por_lembranca(navegador, tmp_path):
    pag, erros = _pagina(navegador, tmp_path)
    try:
        pag.select_option("#tit-centro", "2")            # a pessoa escolheu
        _fornecedor(pag, "IMOB X")
        e = _estado(pag)
        assert e["tit-centro"] == ["2", False], e         # ficou a dela
        assert e["tit-plano"] == ["10", True], e          # o resto a memória pôs
        assert not erros, erros
    finally:
        pag.close()


def test_trocar_de_fornecedor_tira_a_lembranca_do_anterior(navegador, tmp_path):
    pag, erros = _pagina(navegador, tmp_path)
    try:
        _fornecedor(pag, "IMOB X")
        _fornecedor(pag, "FORNECEDOR NOVO")               # sem memória
        assert _estado(pag) == {"tit-plano": ["", False], "tit-centro": ["", False],
                                "tit-cat-pagar": ["Fornecedores", False]}
        assert pag.inner_text("#tit-mem-dica").strip() == ""
        _fornecedor(pag, "PEDREIRO")                      # outra memória
        assert _estado(pag)["tit-centro"] == ["3", True]
        assert not erros, erros
    finally:
        pag.close()


def test_a_receber_troca_o_vocabulario_da_categoria(navegador, tmp_path):
    pag, erros = _pagina(navegador, tmp_path)
    try:
        pag.select_option('select[name="tipo"]', "receber")
        pag.wait_for_timeout(80)
        vis = pag.evaluate("""() => ['tit-cat-pagar','tit-cat-receber'].map(id => {
            const s = document.getElementById(id);
            return [id, s.disabled, getComputedStyle(s).display !== 'none']; })""")
        assert vis == [["tit-cat-pagar", True, False], ["tit-cat-receber", False, True]]
        # e o que vai no POST é só a de receber
        dados = pag.evaluate("""() => new FormData(document.getElementById('tit-cat-receber').form)
                                .getAll('categoria')""")
        assert dados == ["Vendas"], dados
        assert not erros, erros
    finally:
        pag.close()
