"""O tipo de despesa — fixa, eventual, investimento — SEPARADO do centro de custo.

Correção do dono em 24/09/2026, sobre a entrega 4: "fixa, eventual, investimento
não é centro de custo, tem que ser separado para ter maior clareza no relatório
do gestor". Opção A escolhida por ele: o que já estava num daqueles três
"centros" ganha o tipo, e o centro fica como está.

O QUE ESTE ARQUIVO FIXA:

  * a cópia da 325 preenche o tipo pelos três centros, SÓ onde está vazio, e não
    mexe no centro de ninguém (regra "não mexer em centro de custos");
  * o tipo anda com a conta a pagar: nasce, edita (None não mexe, "" apaga,
    estranho é ignorado), vai pro caixa na baixa, repete na recorrente, e a
    conciliação só preenche o vazio;
  * a memória do fornecedor lembra o tipo;
  * o quadro do gestor soma por tipo e pelo VALOR, e a lista "sem tipo" sugere
    pela forma da descrição, sem chutar pelo plano;
  * no WhatsApp, "foi investimento" vira TIPO, nunca centro;
  * as telas: três botões na conta a pagar, select no Financeiro, quadro na
    Empresa — nada disso em conta a receber.
"""
from __future__ import annotations

import os
from datetime import date, timedelta
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import empresa as emp
from finance import tipo_despesa as td
from finance.livro_caixa import LivroCaixa
from finance.models import Lancamento, Tipo

_BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"
_MIGRACOES = ("018_chave_nfce_lancamentos.sql",
              "053_modulo_pj.sql",
              "057_natureza_lancamento.sql",
              "064_clientes_lojista.sql",
              "066_pessoas_identidade.sql",
              "067_titulos_cliente.sql",
              "131_pessoa_cnpj.sql",
              "132_plano_contas_centros_custo.sql",
              "143_plano_contas_locacao_buffet_servicos.sql",
              "195_titulo_aprovacao.sql",
              "196_titulo_recorrencia.sql",
              "197_titulo_acrescimo.sql",
              "317_titulo_classificacao.sql")

CONTA, OUTRA = 661, 662
HOJE = date.today()


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1,
                           open=True)
    dbname = "zaq_tipo_despesa"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s",
                  (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=4, open=True,
                       kwargs={"prepare_threshold": None})
    init_schema(p)
    for m in _MIGRACOES:
        with p.connection() as c:
            c.execute((_BASE / m).read_text(encoding="utf-8"))
            c.commit()
    from contas import equipe as _eq
    _eq.garantir_tabela(p)
    with p.connection() as c:
        for cid, nome in ((CONTA, "Prime Eventos"), (OUTRA, "Vizinha")):
            c.execute("insert into contas (id, nome, tipo) values (%s,%s,'pj') "
                      "on conflict (id) do nothing", (cid, nome))
        c.commit()
    yield p
    p.close()


def _migra_325(pool):
    with pool.connection() as c:
        c.execute((_BASE / "325_tipo_despesa.sql").read_text(encoding="utf-8"))
        c.commit()


def _lanc(pool, valor, *, centro=None, tipo_d=None, desc=None, quando=None,
          conta=CONTA, natureza="empresa"):
    return LivroCaixa(pool, conta).adicionar(
        Lancamento(tipo=Tipo.DESPESA, valor_centavos=valor, categoria="Servicos",
                   descricao=desc or f"gasto {valor}", data=quando or HOJE,
                   origem="foto", natureza=natureza, centro_custo_id=centro,
                   tipo_despesa=tipo_d), forcar=True).id


def _col(pool, tabela, id_, col):
    with pool.connection() as c:
        return c.execute(f"select {col} from {tabela} where id=%s", (id_,)).fetchone()[0]


# ═══════════════════════════════════════════ a cópia da 325 (opção A)
# Roda PRIMEIRO no arquivo: monta o cenário de antes da 325 e aplica a migração.

def test_a_copia_preenche_pelo_centro_e_nao_mexe_no_centro(pool):
    from finance import plano_contas as pc
    # antes da 325 a coluna não existe: os lançamentos entram sem ela
    fixa = pc.criar_centro(pool, CONTA, "DESPESA FIXA")["id"]
    evt = pc.criar_centro(pool, CONTA, "Despesa Eventual")["id"]
    inv = pc.criar_centro(pool, CONTA, " INVESTIMENTO ")["id"]
    area = pc.criar_centro(pool, CONTA, "BUFFET")["id"]
    vizinho = pc.criar_centro(pool, OUTRA, "DESPESA FIXA")["id"]
    ids = {"fixa": _lanc(pool, 1000, centro=fixa), "evt": _lanc(pool, 2000, centro=evt),
           "inv": _lanc(pool, 3000, centro=inv), "area": _lanc(pool, 4000, centro=area),
           "nada": _lanc(pool, 5000), "viz": _lanc(pool, 6000, centro=vizinho, conta=OUTRA)}
    # dinheiro que ENTRA não é "despesa fixa", mesmo com o centro
    rec = LivroCaixa(pool, CONTA).adicionar(
        Lancamento(tipo=Tipo.RECEITA, valor_centavos=7000, categoria="Vendas",
                   descricao="aporte", data=HOJE, origem="foto", natureza="empresa",
                   centro_custo_id=inv), forcar=True).id
    t = emp.criar_titulo(pool, CONTA, "pagar", "ALUGUEL", 150000, HOJE,
                         centro_custo_id=fixa)["id"]
    _migra_325(pool)
    assert _col(pool, "lancamentos", ids["fixa"], "tipo_despesa") == "fixa"
    assert _col(pool, "lancamentos", ids["evt"], "tipo_despesa") == "eventual"
    assert _col(pool, "lancamentos", ids["inv"], "tipo_despesa") == "investimento"
    assert _col(pool, "lancamentos", ids["area"], "tipo_despesa") is None
    assert _col(pool, "lancamentos", ids["nada"], "tipo_despesa") is None
    assert _col(pool, "lancamentos", ids["viz"], "tipo_despesa") == "fixa"
    assert _col(pool, "lancamentos", rec, "tipo_despesa") is None
    assert _col(pool, "titulos", t, "tipo_despesa") == "fixa"
    # O CENTRO FICA COMO ESTÁ — em todos
    for k, cid in (("fixa", fixa), ("evt", evt), ("inv", inv), ("area", area)):
        assert _col(pool, "lancamentos", ids[k], "centro_custo_id") == cid
    assert _col(pool, "titulos", t, "centro_custo_id") == fixa
    with pool.connection() as c:
        assert c.execute("select count(*) from centros_custo where conta_id=%s",
                         (CONTA,)).fetchone()[0] == 4
    # só o vazio: quem já tem tipo não é trocado, e rodar de novo não muda nada
    with pool.connection() as c:
        c.execute("update lancamentos set tipo_despesa='investimento' where id=%s",
                  (ids["fixa"],))
        c.commit()
    _migra_325(pool)
    assert _col(pool, "lancamentos", ids["fixa"], "tipo_despesa") == "investimento"


def test_o_banco_recusa_tipo_fora_dos_tres(pool):
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        with pool.connection() as c:
            c.execute("insert into lancamentos (conta_id, tipo, valor_centavos, categoria, "
                      "data, tipo_despesa) values (%s,'despesa',1,'x',%s,'variavel')",
                      (CONTA, HOJE))


@pytest.fixture()
def limpo(pool):
    with pool.connection() as c:
        for t in ("titulos", "lancamentos", "centros_custo"):
            c.execute(f"delete from {t} where conta_id in (%s,%s)", (CONTA, OUTRA))
        c.commit()
    yield


# ═══════════════════════════════════════════ o que as pessoas escrevem

@pytest.mark.parametrize("entra,sai", [("Fixa", "fixa"), ("DESPESA FIXA", "fixa"),
                                       ("fixo", "fixa"), ("Eventual", "eventual"),
                                       ("investimentos", "investimento"),
                                       ("", None), (None, None), ("variável", "eventual"),
                                       ("outra coisa", None)])
def test_normalizar(entra, sai):
    assert td.normalizar(entra) == sai


# ═══════════════════════════════════════════ a conta a pagar

def test_a_conta_nasce_com_tipo_e_so_se_for_a_pagar(pool, limpo):
    a = emp.criar_titulo(pool, CONTA, "pagar", "ALUGUEL", 150000, HOJE, tipo_despesa="Fixa")
    b = emp.criar_titulo(pool, CONTA, "receber", "SINAL", 150000, HOJE,
                         tipo_despesa="fixa")
    c_ = emp.criar_titulo(pool, CONTA, "pagar", "X", 1000, HOJE, tipo_despesa="lixo")
    assert (a["tipo_despesa"], b["tipo_despesa"], c_["tipo_despesa"]) == ("fixa", None, None)
    tits = {t["id"]: t for t in emp.listar_titulos(pool, CONTA)}
    assert tits[a["id"]]["tipo_despesa"] == "fixa"
    assert tits[b["id"]]["tipo_despesa"] is None


def test_editar_none_nao_mexe_vazio_apaga_estranho_ignora(pool, limpo):
    t = emp.criar_titulo(pool, CONTA, "pagar", "ALUGUEL", 150000, HOJE,
                         tipo_despesa="fixa")["id"]
    emp.editar_titulo(pool, CONTA, t, descricao="ALUGUEL SET")
    assert _col(pool, "titulos", t, "tipo_despesa") == "fixa"
    emp.editar_titulo(pool, CONTA, t, tipo_despesa="lixo")
    assert _col(pool, "titulos", t, "tipo_despesa") == "fixa"
    emp.editar_titulo(pool, CONTA, t, tipo_despesa="investimento")
    assert _col(pool, "titulos", t, "tipo_despesa") == "investimento"
    emp.editar_titulo(pool, CONTA, t, tipo_despesa="")
    assert _col(pool, "titulos", t, "tipo_despesa") is None


def test_a_baixa_leva_o_tipo_pro_caixa_e_a_recorrente_repete(pool, limpo):
    t = emp.criar_titulo(pool, CONTA, "pagar", "ALUGUEL", 150000, HOJE,
                         periodicidade="mensal", tipo_despesa="fixa")["id"]
    r = emp.dar_baixa_titulo(pool, CONTA, t, data_pagto=HOJE)
    assert _col(pool, "lancamentos", r["lancamento_id"], "tipo_despesa") == "fixa"
    assert _col(pool, "titulos", r["proximo_titulo_id"], "tipo_despesa") == "fixa"


def test_baixa_sem_tipo_continua_sem(pool, limpo):
    t = emp.criar_titulo(pool, CONTA, "pagar", "X", 1000, HOJE)["id"]
    r = emp.dar_baixa_titulo(pool, CONTA, t, data_pagto=HOJE)
    assert _col(pool, "lancamentos", r["lancamento_id"], "tipo_despesa") is None


def test_conciliar_preenche_so_o_vazio(pool, limpo):
    t1 = emp.criar_titulo(pool, CONTA, "pagar", "ÁGUA", 8622, HOJE, contraparte="ÁGUAS",
                          tipo_despesa="fixa")["id"]
    l1 = _lanc(pool, 8622, desc="Águas de Teresina")
    assert emp.conciliar_titulo(pool, CONTA, t1, l1)["ok"]
    assert _col(pool, "lancamentos", l1, "tipo_despesa") == "fixa"
    t2 = emp.criar_titulo(pool, CONTA, "pagar", "LUZ", 9000, HOJE, contraparte="EQUATORIAL",
                          tipo_despesa="fixa")["id"]
    l2 = _lanc(pool, 9000, desc="Equatorial luz", tipo_d="eventual")
    assert emp.conciliar_titulo(pool, CONTA, t2, l2)["ok"]
    assert _col(pool, "lancamentos", l2, "tipo_despesa") == "eventual"   # o de quem olhou


def test_a_memoria_do_fornecedor_lembra_o_tipo(pool, limpo):
    t = emp.criar_titulo(pool, CONTA, "pagar", "DIÁRIA EVENTO", 15000, HOJE,
                         contraparte="MARIA DIARISTA", tipo_despesa="eventual")["id"]
    emp.dar_baixa_titulo(pool, CONTA, t, data_pagto=HOJE)
    m = emp.memoria_do_fornecedor(pool, CONTA, "maria diarista", "DIÁRIA EVENTO 25/09")
    assert m and m["tipo_despesa"] == "eventual"


def test_definir_tipo_no_lancamento_e_so_desta_conta(pool, limpo):
    meu = _lanc(pool, 1000)
    alheio = _lanc(pool, 1000, conta=OUTRA)
    assert LivroCaixa(pool, CONTA).definir_tipo_despesa(meu, "Investimento")
    assert _col(pool, "lancamentos", meu, "tipo_despesa") == "investimento"
    assert not LivroCaixa(pool, CONTA).definir_tipo_despesa(alheio, "fixa")
    assert _col(pool, "lancamentos", alheio, "tipo_despesa") is None
    assert LivroCaixa(pool, CONTA).definir_tipo_despesa(meu, "")
    assert _col(pool, "lancamentos", meu, "tipo_despesa") is None


# ═══════════════════════════════════════════ o quadro do gestor

def test_o_quadro_soma_por_tipo_pelo_valor_e_so_empresa(pool, limpo):
    ant = (HOJE.replace(day=1) - timedelta(days=1)).replace(day=10)
    _lanc(pool, 150000, tipo_d="fixa")
    _lanc(pool, 30000, tipo_d="eventual")
    _lanc(pool, 20000)                                     # sem tipo
    _lanc(pool, 99900, tipo_d="fixa", natureza="pessoal")  # não entra
    _lanc(pool, 50000, tipo_d="investimento", quando=ant)
    _lanc(pool, 77700, tipo_d="fixa", conta=OUTRA)         # outra conta
    ant_m, este = td.por_mes(pool, CONTA, meses=2, hoje=HOJE)
    assert (este["ano"], este["mes"]) == (HOJE.year, HOJE.month)
    por = {x["tipo"]: (x["n"], x["centavos"]) for x in este["por_tipo"]}
    assert por == {"fixa": (1, 150000), "eventual": (1, 30000), "investimento": (0, 0)}
    assert este["sem"] == {"n": 1, "centavos": 20000}
    assert este["total"] == 200000 and este["pct_com_tipo"] == 90
    assert ant_m["total"] == 50000 and ant_m["pct_com_tipo"] == 100


def test_a_lista_sem_tipo_sugere_pela_forma_e_nao_pelo_plano(pool, limpo):
    _lanc(pool, 15000, tipo_d="eventual", desc="DIARIA 18/09 MARIA",
          quando=HOJE - timedelta(days=40))
    a = _lanc(pool, 15000, desc="DIARIA 25/09 MARIA")      # mesma forma -> sugere
    b = _lanc(pool, 90000, desc="MANUTENCAO AR")            # nenhuma igual -> nada
    _lanc(pool, 1000, desc="DIARIA 25/09 MARIA", conta=OUTRA)
    lista = {x["id"]: x for x in td.sem_tipo(pool, CONTA, HOJE.year, HOJE.month)}
    assert set(lista) == {a, b}
    assert lista[a]["sugestao"] == "eventual" and lista[b]["sugestao"] is None
    # e a sugestão não é gravada: continua sem tipo até o toque
    assert _col(pool, "lancamentos", a, "tipo_despesa") is None


# ═══════════════════════════════════════════ o WhatsApp

def _lancar(pool, **entrada):
    from finance.tools import construir_ferramentas
    fs = {f.nome: f for f in construir_ferramentas(LivroCaixa(pool, CONTA), papel="membro")}
    base = {"valor": 500.0, "categoria": "Contas de casa", "origem": "foto",
            "descricao": "Reforma do salão", "natureza": "empresa"}
    base.update(entrada)
    return fs["lancar_despesa"].executar(base)


def test_foi_investimento_vira_tipo_e_nao_centro(pool, limpo):
    from finance import plano_contas as pc
    pc.criar_centro(pool, CONTA, "INVESTIMENTO")        # o "centro" que confundia
    r = _lancar(pool, tipo_despesa="investimento")
    assert "tipo investimento" in r
    with pool.connection() as c:
        tipo_d, centro = c.execute("select tipo_despesa, centro_custo_id from lancamentos "
                                   "where conta_id=%s order by id desc limit 1",
                                   (CONTA,)).fetchone()
    assert (tipo_d, centro) == ("investimento", None)


def test_gasto_pessoal_nao_ganha_tipo(pool, limpo):
    _lancar(pool, tipo_despesa="fixa", natureza="pessoal")
    with pool.connection() as c:
        assert c.execute("select tipo_despesa from lancamentos where conta_id=%s "
                         "order by id desc limit 1", (CONTA,)).fetchone()[0] is None


def test_o_esquema_da_ferramenta_separa_tipo_de_centro(pool):
    from finance.tools import construir_ferramentas
    fs = {f.nome: f for f in construir_ferramentas(LivroCaixa(pool, CONTA), papel="dono")}
    props = fs["lancar_despesa"].parametros["properties"]
    assert props["tipo_despesa"]["enum"] == ["fixa", "eventual", "investimento"]
    assert "'foi investimento' -> o centro" not in props["centro_custo"]["description"]
    assert "NÃO é centro" in props["centro_custo"]["description"]
    assert "tipo_despesa" not in fs["lancar_receita"].parametros["properties"]


# ═══════════════════════════════════════════ as telas

def _empresa(**kw):
    from tests.test_portal_js_sintaxe import PAGINAS, _BASE_CTX, _CONTA
    import web.portal as pt
    ctx = dict(_BASE_CTX)
    ctx.update(PAGINAS["empresa"])
    ctx.update(kw)
    return pt._env.get_template("empresa").render(conta=_CONTA, **ctx)


def _mes(ano, mes, **kw):
    base = {"ano": ano, "mes": mes, "rotulo": f"Setembro/{ano}",
            "por_tipo": [{"tipo": "fixa", "rotulo": "Fixa", "n": 2, "centavos": 48381},
                         {"tipo": "eventual", "rotulo": "Eventual", "n": 2, "centavos": 30790},
                         {"tipo": "investimento", "rotulo": "Investimento", "n": 6,
                          "centavos": 346228}],
            "sem": {"n": 61, "centavos": 4753614}, "total": 5179013, "pct_com_tipo": 8}
    base.update(kw)
    return base


def test_o_quadro_do_gestor_aparece_com_a_lista_de_um_toque():
    pend = [{"id": 5, "data": HOJE, "descricao": "MARIA <b>DIARISTA</b>",
             "valor_centavos": 15000, "plano": "5.1.09 Diaristas", "centro": "",
             "sugestao": "eventual"}]
    html = _empresa(quadro_tipo=[_mes(2026, 9)], quadro_sem_tipo={(2026, 9): pend})
    i = html.index('id="despesas-por-tipo"')
    q = html[i:html.index('id="titulos"', i)]
    assert "8% do valor com tipo" in q and "R$ 47.536,14" in q
    assert "1 de setembro sem tipo — classificar agora" in q
    assert 'class="sug" data-tipo="eventual"' in q
    assert "MARIA &lt;b&gt;DIARISTA&lt;/b&gt;" in q and "<b>DIARISTA</b>" not in q
    assert q.count('onclick="qtdTipo(this, 5)"') == 3


def test_sem_despesa_nenhuma_o_quadro_nao_aparece():
    html = _empresa(quadro_tipo=[_mes(2026, 9, total=0)])
    assert 'id="despesas-por-tipo"' not in html


def test_a_conta_a_pagar_tem_os_tres_botoes():
    html = _empresa()
    i = html.index('id="tit-tipo-d"')
    bloco = html[i:html.index("</div>", i)]
    for tp in ("fixa", "eventual", "investimento"):
        assert f'name="tipo_despesa" value="{tp}"' in bloco
    assert "checked" not in bloco


def test_a_edicao_mostra_o_tipo_so_em_conta_a_pagar():
    from tests.test_titulo_classificacao_tela import _render, _titulo
    html = _render([_titulo(tipo_despesa="investimento"),
                    _titulo(id=8, tipo="receber", descricao="SINAL")])
    i = html.index('action="/painel/empresa/titulo/7/descricao"')
    f7 = html[i:html.index("</form>", i)]
    assert 'name="tem_tipo"' in f7
    assert '<option value="investimento" selected>Investimento</option>' in f7
    j = html.index('action="/painel/empresa/titulo/8/descricao"')
    assert 'name="tem_tipo"' not in html[j:html.index("</form>", j)]


def test_a_linha_da_conta_mostra_o_tipo():
    from tests.test_titulo_classificacao_tela import _render, _titulo
    html = _render([_titulo(tipo_despesa="fixa")])
    assert '<span class="tit-cls" style="color:#7bb8e6">Fixa</span>' in html


def test_a_rota_grava_o_tipo(pool, limpo, monkeypatch):
    import web.portal as pt
    monkeypatch.setattr(pt, "conta_logada", lambda req: (CONTA,))
    monkeypatch.setattr(pt, "get_pool", lambda: pool)
    lid = _lanc(pool, 1000)
    r = pt.definir_tipo_despesa_lancamento(None, lid, "eventual")
    assert r.body == b'{"ok":true}'
    assert _col(pool, "lancamentos", lid, "tipo_despesa") == "eventual"


# ═══════════════════════════════════════════ no navegador

from tests.test_titulo_classificacao_tela import (  # noqa: E402
    _DUBLE, _fornecedor, _pagina, navegador)  # noqa: F401 — fixture reaproveitada

_MEM = {"IMOB X": {"categoria": None, "plano_conta_id": None, "centro_custo_id": None,
                   "tipo_despesa": "fixa", "de": "ALUGUEL AGOSTO"},
        "OUTRO": {"categoria": None, "plano_conta_id": None, "centro_custo_id": None,
                  "tipo_despesa": None, "de": "X"}}


def _marcado(pag):
    return pag.evaluate("""() => { const r = document.querySelector(
        '#tit-tipo-d input:checked'); return [r ? r.value : null,
        document.getElementById('tit-tipo-d').classList.contains('lembrado')]; }""")


def test_a_memoria_marca_o_tipo_e_troca_de_fornecedor_desmarca(navegador, tmp_path):
    pag, erros = _pagina(navegador, tmp_path)
    try:
        pag.evaluate(_DUBLE, _MEM)
        _fornecedor(pag, "IMOB X")
        assert _marcado(pag) == ["fixa", True]
        _fornecedor(pag, "OUTRO")                  # lembrança do anterior sai
        assert _marcado(pag) == [None, False]
        assert not erros, erros
    finally:
        pag.close()


def test_a_escolha_da_pessoa_vence_a_memoria(navegador, tmp_path):
    pag, erros = _pagina(navegador, tmp_path)
    try:
        pag.evaluate(_DUBLE, _MEM)
        pag.click("#tit-tipo-d label:has(input[value=investimento])")
        _fornecedor(pag, "IMOB X")
        assert _marcado(pag) == ["investimento", False]
        assert not erros, erros
    finally:
        pag.close()


def test_conta_a_receber_esconde_e_desliga_o_tipo(navegador, tmp_path):
    pag, erros = _pagina(navegador, tmp_path)
    try:
        pag.click("#tit-tipo-d label:has(input[value=fixa])")
        pag.select_option("select[name=tipo]", "receber")
        pag.dispatch_event("select[name=tipo]", "change")
        estado = pag.evaluate("""() => { const d = document.getElementById('tit-tipo-d');
            return [d.style.display, [...d.querySelectorAll('input')].every(r => r.disabled && !r.checked)]; }""")
        assert estado == ["none", True]
        assert not erros, erros
    finally:
        pag.close()


def test_um_toque_grava_e_marca_a_linha(navegador, tmp_path):
    pend = [{"id": 5, "data": HOJE, "descricao": "MARIA DIARISTA", "valor_centavos": 15000,
             "plano": "", "centro": "", "sugestao": "eventual"}]
    arq = tmp_path / "quadro.html"
    arq.write_text(_empresa(quadro_tipo=[_mes(2026, 9)], quadro_sem_tipo={(2026, 9): pend}),
                   encoding="utf-8")
    pag = navegador.new_page(viewport={"width": 390, "height": 900})
    erros: list[str] = []
    pag.on("pageerror", lambda e: erros.append(str(e)))
    try:
        pag.goto(arq.as_uri())
        pag.evaluate("""() => { window.__corpos = [];
          window.fetch = function(url, o){ window.__corpos.push(String(url) + '|' + o.body);
            var corpo = {ok: true};
            return Promise.resolve({ok: true, status: 200, headers: {get: () => null},
              text: () => Promise.resolve(JSON.stringify(corpo)),
              json: () => Promise.resolve(corpo)}); }; }""")
        pag.click("#despesas-por-tipo summary")
        pag.click("#qtd-5 button[data-tipo=fixa]")      # a pessoa discorda da sugestão
        pag.wait_for_timeout(150)
        corpos = pag.evaluate("() => window.__corpos")
        assert corpos == ["/painel/lancamento/tipo-despesa|lancamento_id=5&tipo_despesa=fixa"]
        est = pag.evaluate("""() => [document.getElementById('qtd-5').classList.contains('feito'),
            document.querySelector('#qtd-5 button.on').dataset.tipo]""")
        assert est == [True, "fixa"]
        assert not erros, erros
    finally:
        pag.close()


# ═══════════════════════════════════════════ os avisos que ficaram errados

def test_a_326_corrige_os_avisos_da_mistura(pool):
    with pool.connection() as c:
        c.execute("""create table if not exists novidades (id bigserial primary key,
                       chave text unique, tipo text, publico text, pra_quem text[],
                       titulo text, resumo text, link text, corpo text,
                       publicado_em timestamptz)""")
        for m in ("319_novidade_comprovante_quita_conta.sql",
                  "321_novidade_planejamento_da_semana.sql",
                  "326_novidade_tipo_de_despesa.sql",
                  "326_novidade_tipo_de_despesa.sql"):          # idempotente
            c.execute((_BASE / m).read_text(encoding="utf-8"))
        c.commit()
        rows = dict(c.execute("select chave, resumo || ' ' || corpo from novidades").fetchall())
    assert "centro de custo." not in rows["planejamento-da-semana"]
    assert "Sem centro" not in rows["planejamento-da-semana"]
    assert "por tipo: fixa, eventual e investimento" in rows["planejamento-da-semana"]
    assert "qual dos seus centros" not in rows["comprovante-quita-conta"]
    assert "o Zaq marca o tipo da despesa" in rows["comprovante-quita-conta"]
    assert "tipo-de-despesa" in rows
