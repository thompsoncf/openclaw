"""A aba Empresa reorganizada — pedido do dono em 24/09/2026.

"vamos dar uma atenção pro layout da aba empresas, vamos dar uma ajustada logo na
página toda". Mockup aprovado em `docs/mockups/empresa_layout.html`, com as
respostas do dono:

  1. duas colunas no computador — "pode ser, se couber com o ajuste bom";
  2. formulário aberto no PC e fechado no celular — "pode ser";
  3. os botões de cada conta — "melhore" (o menu ⋯ virou o próximo passo da
     conta à vista e o resto num painel que abre, com nome);
  4. configurações no fim — "ótimo".

Medido na tela da Prime antes da mudança: 6.410 px no PC e 8.705 px no celular,
as contas a pagar eram o 9º bloco de 11, e o formulário cortava os campos ("A p",
"opciona") com o "+ Add" antes de categoria, plano e centro.

O que este teste protege:

  * **o topo não inventa número.** Cada cartão repete, pela MESMA regra, um número
    que a aba já mostra mais abaixo — atrasadas só a pagar, caixa sem saldo pede o
    saldo em vez de anunciar rombo, e pendência zerada não vira chip;
  * **a ordem da página** — contas primeiro, configuração por último;
  * **a largura é desta tela só** (o `.card.larga` compartilhado fica nos 720 px);
  * **o formulário na ordem em que se preenche**, com todos os campos de antes e o
    botão depois da classificação;
  * **cada conta mostra no máximo dois botões**, e nada some: editar, repetir e
    apagar moram no painel, e "dar baixa" existe em toda conta com valor.
"""
from datetime import date

from jinja2 import DictLoader, Environment

from web import portal as pt

TPL = pt._EMPRESA


# ── o resumo do topo ────────────────────────────────────────────────────────

def _t(tipo="pagar", cent=10000, atrasado=False, aprovacao="aguardando"):
    return {"tipo": tipo, "valor_centavos": cent, "atrasado": atrasado,
            "aprovacao": aprovacao}


def _dre(**kw):
    base = {"mes": 9, "ano": 2026, "resultado_centavos": -754194,
            "receitas_centavos": 4424819, "despesas_centavos": 5179013,
            "a_definir_n": 0}
    base.update(kw)
    return base


def test_o_topo_soma_como_as_secoes():
    titulos = [_t(cent=287777, atrasado=True), _t(cent=25190),
               _t("receber", 120000, atrasado=True, aprovacao="autorizado"),
               _t("receber", 660000, aprovacao="autorizado")]
    planej = {"saldo_centavos": 677087, "sobra_centavos": -1358720, "algum_velho": False}
    r = pt._empresa_resumo(titulos, planej, _dre(), 17, None, 11, True)
    assert (r["pagar_n"], r["pagar_centavos"]) == (2, 312967)
    assert (r["pagar_atr_n"], r["pagar_atr_centavos"]) == (1, 287777)
    assert (r["receber_n"], r["receber_centavos"]) == (2, 780000)
    # a receber vencida é cliente devendo: não entra nas "atrasadas" do topo,
    # igual à pílula ⚠️ Atrasadas (`_lente_atrasadas`)
    assert (r["receber_atr_n"], r["receber_atr_centavos"]) == (1, 120000)
    assert r["sobra_centavos"] == -1358720
    assert r["resultado_centavos"] == -754194


def test_pendencia_zerada_nao_vira_chip():
    r = pt._empresa_resumo([], {"saldo_centavos": None, "sobra_centavos": None},
                           _dre(), 0, [{"sem": {"n": 0, "centavos": 0}, "total": 0}],
                           0, True)
    assert r["pendencias"] == []


def test_as_pendencias_dizem_quantas_e_levam_pra_onde_resolve():
    quadro = [{"sem": {"n": 3, "centavos": 100}, "total": 1000},
              {"sem": {"n": 41, "centavos": 3033310}, "total": 5179013}]
    r = pt._empresa_resumo([], None, _dre(a_definir_n=3), 17, quadro, 11, True)
    textos = {p["texto"]: p["href"] for p in r["pendencias"]}
    assert textos["⏳ 11 esperando sua liberação"] == "#titulos"
    assert textos["17 lançamentos sem conta contábil"] == "#a-classificar"
    assert textos["3 lançamentos fora da DRE"].startswith("/painel/financeiro?mes=9")
    # o mês é o ÚLTIMO do quadro (o atual), e a porcentagem é pelo valor
    assert textos["41 despesas sem tipo · 59% do valor"] == "#despesas-por-tipo"


def test_quem_nao_e_dono_le_que_espera_o_dono():
    r = pt._empresa_resumo([], None, _dre(), 0, None, 2, False)
    assert r["pendencias"][0]["texto"] == "⏳ 2 esperando o dono liberar"


def _topo(resumo, **ctx):
    i = TPL.index('{% set _MESES')
    j = TPL.index('{#- A BARRA DAS SEÇÕES')
    env = Environment(loader=DictLoader({"t": TPL[i:j] + "</div>"}))
    env.filters["brl"] = pt.brl
    base = {"resumo": resumo, "empresa_nome": "Prime Eventos", "empresa_doc": "",
            "dre": _dre(), "rotulo_receber": "A receber", "carteira": None}
    base.update(ctx)
    return env.get_template("t").render(**base)


def test_sem_saldo_o_cartao_pede_o_saldo_em_vez_de_um_rombo():
    r = pt._empresa_resumo([_t(cent=500000, atrasado=True)],
                           {"saldo_centavos": None, "sobra_centavos": None},
                           _dre(), 0, None, 0, True)
    html = _topo(r)
    assert "informe o saldo" in html
    assert "falta" not in html


def test_com_saldo_o_cartao_diz_quanto_falta():
    r = pt._empresa_resumo([], {"saldo_centavos": 677087, "sobra_centavos": -1358720},
                           _dre(), 0, None, 0, True)
    html = _topo(r)
    assert "Caixa da semana · falta" in html and "13.587,20" in html
    assert "Resultado de setembro" in html


def test_sem_planejamento_nao_ha_cartao_de_caixa():
    r = pt._empresa_resumo([], None, _dre(), 0, None, 0, True)
    assert "Caixa da semana" not in _topo(r)


# ── a página ───────────────────────────────────────────────────────────────

def test_contas_primeiro_configuracao_por_ultimo():
    pos = [TPL.index(m) for m in ('class="card larga emp-topo"', 'id="emp-nav"',
                                  'id="titulos"', 'id="dre"', 'id="folha"',
                                  'id="config"')]
    assert pos == sorted(pos)
    conf = TPL.index('id="config"')
    for sec in ('id="plano-contas"', 'id="centros-custo"', 'id="avisos"'):
        assert TPL.index(sec) > conf, sec


def test_a_largura_e_desta_tela_e_as_duas_colunas_so_quando_cabem():
    assert ".emp-full{width:calc(100% - 2rem);max-width:1240px;" in TPL
    assert "container-type:inline-size" in TPL
    assert "@container (min-width:1020px)" in TPL
    fonte = open(pt.__file__, encoding="utf-8").read()
    assert ".card.larga{max-width:720px}" in fonte


def test_a_folha_vazia_vira_uma_linha():
    assert '<details class="card larga sec-pc" id="folha"{% if folha.itens or erro %} open{% endif %}>' in TPL
    assert "nenhum funcionário cadastrado" in TPL


def test_a_carteira_mostra_os_cinco_maiores_e_abre_o_resto():
    assert "{% for c in carteira.clientes[:5] %}" in TPL
    assert "{% for c in carteira.clientes[5:] %}" in TPL


# ── o formulário ───────────────────────────────────────────────────────────

def _form():
    i = TPL.index('<form method="post" action="/painel/empresa/titulo" class="tit-nova-f">')
    return TPL[i:TPL.index("</form>", i)]


def test_o_formulario_tem_todos_os_campos_de_antes():
    f = _form()
    for nome in ("tipo", "descricao", "valor", "vencimento", "cliente", "categoria",
                 "plano_conta_id", "centro_custo_id", "tipo_despesa",
                 "periodicidade", "valor_variavel"):
        assert f'name="{nome}"' in f, nome
    # a caixinha desligada ("Vincule um cliente…") não marcava nada
    assert 'form="_nada"' not in TPL


def test_o_botao_vem_depois_da_classificacao():
    f = _form()
    bt = f.index("+ Adicionar conta")
    for campo in ('name="categoria"', 'name="plano_conta_id"', 'name="centro_custo_id"',
                  'name="tipo_despesa"', 'name="periodicidade"'):
        assert f.index(campo) < bt, campo


def test_valor_muda_so_aparece_com_um_ritmo():
    assert '<label id="tit-rep-var" hidden' in _form()
    assert "function titRepTroca(sel)" in TPL


def test_no_celular_o_formulario_fica_atras_do_nova_conta():
    assert '<details class="tit-nova" id="tit-nova" open>' in TPL
    assert "matchMedia('(max-width:700px)').matches) d.open=false" in TPL


# ── a linha de cada conta ──────────────────────────────────────────────────

def _linhas(*titulos, pode_liberar=True, decide=True):
    i = TPL.index("{% macro tit_linha")
    j = TPL.index("{% endmacro %}", i) + len("{% endmacro %}")
    env = Environment(loader=DictLoader({"t": TPL[i:j] + (
        "{% for t in lista %}{{ tit_linha(t, decide) }}{% endfor %}")}))
    env.filters["brl"] = pt.brl
    env.filters["n2"] = lambda v: f"{v:.2f}"
    return env.get_template("t").render(
        lista=list(titulos), decide=decide, pode_liberar=pode_liberar,
        RITMOS=[("mensal", "todo mês")], RITMO_SELO={"mensal": "mensal"},
        hoje_iso="2026-09-24", CAT_TITULO={"pagar": ["Fornecedores"], "receber": ["Vendas"]},
        TIPOS_DESPESA=["fixa"], TIPO_DESPESA_ROTULO={"fixa": "Fixa"})


def _conta(apro="aguardando", tipo="pagar", cent=10000, conc=None):
    return {"id": 7, "descricao": "ENERGIA SOLAR", "contraparte": "NORDESTE",
            "valor_centavos": cent, "aprovacao": apro, "tipo": tipo,
            "vencimento": date(2026, 9, 5), "atrasado": True, "cliente_nome": None,
            "cliente_id": None, "sem_fornecedor": False, "conciliar": conc,
            "cobranca_link_url": None, "categoria": "Fornecedores"}


def _acoes(html):
    i = html.index('class="tit-acoes"')
    return html[i:html.index('<button type="button" class="tit-mais-bt"', i)]


def _painel(html):
    return html[html.index('<div class="tit-mais" hidden>'):]


def test_o_dono_decide_sem_rolar_e_a_baixa_mora_no_painel():
    html = _linhas(_conta())
    ac = _acoes(html)
    assert "✓ liberar" in ac and "✕ recusar" in ac
    assert "dar baixa" not in ac
    assert "dar baixa ✓" in _painel(html)  # "só avisa, não trava" (03/09/2026)


def test_quem_nao_e_dono_ve_a_baixa_e_nao_a_decisao():
    ac = _acoes(_linhas(_conta(), pode_liberar=False))
    assert "dar baixa ✓" in ac
    assert "liberar" not in ac and "recusar" not in ac


def test_ja_foi_paga_vem_antes_da_baixa():
    conc = {"lancamento_id": 9, "resumo": "10/08, R$ 100,00", "titulo": "Ligar",
            "confirmar": "x"}
    ac = _acoes(_linhas(_conta("autorizado", conc=conc), decide=False))
    assert ac.index("já foi paga") < ac.index("dar baixa ✓")
    # a baixa deixa de ser o botão verde: ela lançaria a despesa outra vez
    assert 'onclick="titBaixaToggle(this)" style="color:var(--verde-claro)"' not in ac


def test_no_maximo_dois_botoes_a_vista():
    conc = {"lancamento_id": 9, "resumo": "r", "titulo": "t", "confirmar": "x"}
    for conta, decide in ((_conta(), True), (_conta("recusado"), True),
                          (_conta("autorizado", conc=conc), False),
                          (_conta("autorizado", tipo="receber"), False),
                          (_conta(cent=0), True)):
        ac = _acoes(_linhas(conta, decide=decide))
        assert ac.count("<button") + ac.count("<a ") <= 2, conta["aprovacao"]


def test_editar_repetir_e_apagar_moram_no_painel():
    html = _linhas(_conta("autorizado"), decide=False)
    p = _painel(html)
    for alvo in ('class="tit-edit"', "/recorrencia", "/apagar", "🗑 apagar conta"):
        assert alvo in p, alvo
        assert alvo not in _acoes(html), alvo


def test_sem_valor_a_conta_pede_o_valor():
    ac = _acoes(_linhas(_conta(cent=0)))
    assert "✎ pôr o valor" in ac and "liberar" not in ac


# ── o aviso ────────────────────────────────────────────────────────────────

def test_o_aviso_mira_quem_tem_a_aba():
    from finance import novidades as nv
    assert "empresa" in nv.PUBLICOS_CONTA
    # portão de conta sem banco não responde "sim" (falha fechada)
    assert nv.alcanca("empresa", "eventos") is False
    sql = open("db/migracoes/338_novidade_empresa_reorganizada.sql", encoding="utf-8").read()
    assert "'empresa-reorganizada', 'novidade', 'empresa', '{dono,gestor}'" in sql
