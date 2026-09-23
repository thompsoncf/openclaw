"""O painel da diferença na baixa, rodando no Chromium — pedido 9, 23/09/2026.

"Sempre pergunta pro gestor": o painel só abre quando o que entrou não é a
parcela, mostra só o lado que vale (a menos ou a mais), e a baixa não sai sem
uma escolha. Isto roda no navegador de verdade: a lição de 19/09/2026 é que "a
função está na página" não prova que ela faz o que diz.
"""
from __future__ import annotations

import os
from datetime import date, timedelta

import pytest

from tests.test_portal_js_sintaxe import PAGINAS, _BASE_CTX, _CONTA
from web import portal as pt

HOJE = date.today()


def _receber(**kw):
    t = {"id": 7, "tipo": "receber", "descricao": "Evento · Sinal", "contraparte": "Maria",
         "valor_centavos": 234000, "vencimento": HOJE - timedelta(days=2),
         "status": "aberto", "recorrente": False, "categoria": "Serviços",
         "cobranca_link_url": None, "pago_em": None, "lancamento_id": None,
         "cliente_id": None, "cliente_nome": None, "acrescimo_centavos": 0,
         "atrasado": True, "aprovacao": "autorizado", "aprovacao_motivo": None,
         "pago_sem_autorizacao": False, "criado_nome": "", "aprovado_nome": "",
         "aprovado_em": None, "periodicidade": None, "valor_variavel": False,
         "proxima": None, "sem_fornecedor": False, "conciliar": None, "prazo": "",
         "plano_conta_id": None, "plano_codigo": "", "plano_nome": "",
         "centro_custo_id": None, "centro_nome": ""}
    t.update(kw)
    return t


def _render(alvos):
    t = _receber()
    blocos = [{"titulo": "A receber", "cor": "rec", "decide": False, "itens": [t],
               "centavos": t["valor_centavos"], "dica": ""}]
    ctx = dict(_BASE_CTX)
    ctx.update(PAGINAS["empresa"])
    ctx.update(titulos=[t], tit_blocos=blocos, tit_atrasadas=pt._lente_atrasadas(blocos),
               pode_liberar=True, RITMOS=[], RITMO_SELO={}, hoje_iso=HOJE.isoformat(),
               MULTA_ATRASO_PCT=2, JUROS_MORA_PCT_MES=1,
               alvos_credito={7: alvos})
    return pt._env.get_template("empresa").render(conta=_CONTA, **ctx)


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


_ALVOS = [{"id": 9, "descricao": "Evento · Parcela 1/5", "valor_centavos": 114200,
           "vencimento": HOJE + timedelta(days=28)}]

#: registrado DEPOIS dos da página: lê se alguém barrou o envio e segura a
#: navegação, pra o teste continuar na mesma página
_ESPIA = """() => { window.__envios = [];
  document.addEventListener('submit', function(e){
    window.__envios.push(e.defaultPrevented); e.preventDefault(); }); }"""


def _pagina(navegador, tmp_path, alvos=_ALVOS):
    arq = tmp_path / "empresa.html"
    arq.write_text(_render(alvos), encoding="utf-8")
    pag = navegador.new_page(viewport={"width": 390, "height": 900})
    erros: list[str] = []
    pag.on("pageerror", lambda e: erros.append(str(e)))
    pag.goto(arq.as_uri())
    pag.evaluate(_ESPIA)
    pag.evaluate("() => titBaixaToggle(document.querySelector("
                 "'form.tit-baixa').closest('.tit-lin').querySelector('button'))")
    return pag, erros


def _digita(pag, valor):
    pag.fill("form.tit-baixa input[name=recebido]", valor)
    pag.dispatch_event("form.tit-baixa input[name=recebido]", "input")


def _visivel(pag, sel):
    return pag.evaluate(f"() => getComputedStyle(document.querySelector('{sel}')).display")


def _envia(pag):
    pag.evaluate("() => document.querySelector('form.tit-baixa').requestSubmit()")
    return pag.evaluate("() => window.__envios[window.__envios.length - 1]")


def test_valor_igual_nao_abre_painel_e_a_baixa_sai(navegador, tmp_path):
    pag, erros = _pagina(navegador, tmp_path)
    try:
        assert _visivel(pag, "form.tit-baixa") == "flex"
        assert _visivel(pag, ".tit-dif") == "none"
        assert _envia(pag) is False                 # ninguém barrou
        assert not erros, erros
    finally:
        pag.close()


def test_a_menos_mostra_so_o_lado_do_menos_e_exige_escolha(navegador, tmp_path):
    pag, erros = _pagina(navegador, tmp_path)
    try:
        _digita(pag, "2.000,00")
        assert _visivel(pag, ".tit-dif") == "flex"
        assert _visivel(pag, ".tit-dif-menos") == "flex"
        assert _visivel(pag, ".tit-dif-mais") == "none"
        assert "R$ 340,00 a menos" in pag.inner_text(".tit-dif-txt")
        assert "R$ 340,00" in pag.inner_text(".tit-dif-val")
        assert _envia(pag) is True                  # sem escolha, barrado
        pag.check("input[name=destino][value=restante]")
        assert _envia(pag) is False
        assert not erros, erros
    finally:
        pag.close()


def test_a_mais_mostra_o_abater_e_troca_de_lado_desmarca(navegador, tmp_path):
    pag, erros = _pagina(navegador, tmp_path)
    try:
        _digita(pag, "2.415,00")
        assert _visivel(pag, ".tit-dif-mais") == "flex"
        assert _visivel(pag, ".tit-dif-menos") == "none"
        assert "R$ 75,00 a mais" in pag.inner_text(".tit-dif-txt")
        pag.check("input[name=destino][value=abater]")
        # mudou de ideia: agora entrou a menos — o "abater" não pode ir junto
        _digita(pag, "2.000,00")
        assert pag.evaluate("() => !!document.querySelector('input[name=destino]:checked')") \
            is False
        assert _envia(pag) is True
        assert not erros, erros
    finally:
        pag.close()


def test_sem_parcela_pra_abater_so_sobra_juros(navegador, tmp_path):
    pag, erros = _pagina(navegador, tmp_path, alvos=[])
    try:
        _digita(pag, "2.415,00")
        assert pag.query_selector("input[name=destino][value=abater]") is None
        assert "não tem outra parcela" in pag.inner_text(".tit-dif-mais")
        pag.check("input[name=destino][value=juros]")
        assert _envia(pag) is False
        assert not erros, erros
    finally:
        pag.close()
