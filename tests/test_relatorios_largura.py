"""Os Relatórios usavam 720 px de uma tela de 2000 — e escondiam o nome do cliente.

O print de 26/08 mostrava a tabela de Vendas com os clientes lendo "ço Pelle
Clínica" e "erson Venici". Não era truncagem: o cartão herdava `.card.larga`
(720 px), a tabela tinha `min-width:640px` com `white-space:nowrap` em TODA
célula, e seis colunas de nome longo não cabiam. A tabela rolava pro lado e o que
aparecia era o MEIO dela — a coluna Data e o começo do nome ficavam fora da área
visível. Era também o que fazia a coluna Vendedor parecer vazia.

O que este teste protege:

  * a largura extra é de uma classe SÓ DO RELATÓRIO. `.card.larga` vale pra
    Equipe, Fornecedor, Portal e mais cinco telas, e várias são formulário —
    largura demais ali piora, não melhora. Se alguém "simplificar" mexendo na
    classe compartilhada, o teste quebra;
  * o teto de 1500 px continua existindo (decisão do dono em 26/08): sem teto,
    numa tela de 3440 px a linha separa o nome do cliente do valor por meio metro;
  * TODO relatório tem exatamente UMA coluna elástica, e ela é de nome livre. Zero
    elásticas devolve a rolagem lateral do print; duas brigam pela sobra;
  * a elástica corta no FIM (reticências), nunca no começo — o começo é a parte
    que identifica o cliente;
  * o cabeçalho gruda ao rolar (são até 300 linhas);
  * **o total é recalculado quando se filtra.** Este é o caso que importa mais que
    o layout inteiro: filtrar por um cliente e deixar o rodapé mostrando o total de
    todo mundo é a tela MENTINDO no número que o dono usa pra decidir. O teste roda
    o JS de verdade no Node;
  * a busca na tela não aparece junto com a busca do servidor — duas caixas na
    mesma tela é pergunta sem resposta óbvia.
"""
from __future__ import annotations

import json
import re
import shutil
import subprocess

import pytest

from web import painel_relatorios as pr
from web import portal as pt

_TPL = pt._RELATORIOS
_CSS_CARD = pt._BASE if hasattr(pt, "_BASE") else ""


# ── a largura ────────────────────────────────────────────────────────────────
def test_a_largura_extra_e_de_uma_classe_so_do_relatorio():
    assert 'class="card larga rel-full"' in _TPL, \
        "o cartão do relatório precisa da classe própria"
    assert ".card.rel-full{max-width:1500px}" in _TPL, \
        "a regra da largura tem que viver no CSS do relatório"


def test_nao_mexeram_no_card_larga_compartilhado():
    """`.card.larga` é usado por Equipe, Fornecedor, Portal e outras — várias
    delas formulário. Alargar aquela classe consertaria esta tela e estragaria as
    outras."""
    fonte = open(pt.__file__, encoding="utf-8").read()
    assert ".card.larga{max-width:720px}" in fonte, \
        "o .card.larga saiu dos 720px — isso alarga oito telas de uma vez"


def test_o_teto_existe():
    """Decisão do dono em 26/08: 1500px, não 100%. Sem teto, numa tela de 3440px o
    olho percorre meio metro entre o nome do cliente e o valor."""
    m = re.search(r"\.card\.rel-full\{max-width:(\d+)px\}", _TPL)
    assert m, "sumiu o teto de largura"
    assert 1200 <= int(m.group(1)) <= 1800, \
        f"teto em {m.group(1)}px — fora da faixa combinada"


# ── a coluna elástica ────────────────────────────────────────────────────────
def _colunas_de(dados: dict) -> list:
    return dados["colunas"]


RELATORIOS = {
    "vendas": lambda: pr._dados_vendas.__wrapped__ if False else None,
}


def test_todo_relatorio_tem_ao_menos_uma_coluna_elastica():
    """ZERO é o bug: devolve a rolagem lateral do print de 26/08, e o que sai da
    área visível é a primeira coluna e o começo do nome.

    A regra era "exatamente uma", por medo de que duas brigassem pela sobra. Não
    brigam: com o mesmo `width` no CSS, dois pedidos que não cabem viram duas
    fatias iguais do que existe — repartimento do algoritmo da tabela, não sorte.
    Contas a pagar/receber precisam de duas (Descrição e Fornecedor são os dois
    nomes livres, e o fornecedor chega a 42 caracteres em produção). O teto de
    duas continua valendo: a partir da terceira cada fatia fica estreita demais
    pra caber nome nenhum, e a tabela vira três colunas de reticências."""
    fonte = open(pr.__file__, encoding="utf-8").read()
    # cada bloco "colunas": [...] é um relatório
    blocos = re.findall(r'"colunas":\s*\[(.*?)\]\s*,\s*\n', fonte, re.S)
    assert len(blocos) >= 6, f"esperava ao menos 6 relatórios, achei {len(blocos)}"
    for i, b in enumerate(blocos):
        n = b.count("flex=True")
        assert 1 <= n <= 2, (
            f"relatório #{i + 1} tem {n} colunas elásticas (tem que ser 1 ou 2).\n"
            f"Marque a coluna de nome livre com flex=True em _col(...): sem ela a\n"
            f"tabela volta a rolar pro lado e engole a primeira coluna.\n"
            f"Colunas: {b.strip()[:140]}")


def test_quem_tem_duas_elasticas_declara_a_divisao():
    """**Este teste substitui um meu que estava errado, e o erro custou uma ida à
    produção.** Eu tinha escrito que duas elásticas com a mesma largura pedida
    dividiriam a sobra em partes iguais sozinhas — "repartimento do algoritmo da
    tabela, não sorte" — e um teste que só conferia que as duas pediam o mesmo.
    Ele passava dizendo a verdade sobre o que eu tinha escrito, e nada sobre o que
    o navegador fazia.

    O que o navegador faz, medido no Chromium com o CSS real: numa tabela de
    largura automática, **a primeira elástica leva toda a sobra e a segunda desce
    pro piso dela**. O Fornecedor de Contas a pagar recebia 107px numa janela de
    900 e os mesmos 107px numa de 1500, cortado em 8 de 8 linhas — foi o print que
    o dono mandou em 04/09/2026.

    Então a regra é: com duas elásticas, cada uma DECLARA a sua parte, e as partes
    fecham 100."""
    fonte = open(pr.__file__, encoding="utf-8").read()
    blocos = re.findall(r'"colunas":\s*\[(.*?)\]\s*,\s*\n', fonte, re.S)
    for i, b in enumerate(blocos):
        if b.count("flex=True") < 2:
            continue
        partes = [int(m) for m in re.findall(r"parte=(\d+)", b)]
        assert len(partes) == b.count("flex=True"), (
            f"relatório #{i + 1} tem duas elásticas e não declarou a parte de "
            f"cada uma — a segunda vai pro piso de ~107px em qualquer tela")
        assert sum(partes) == 100, f"as partes somam {sum(partes)}, não 100"


def test_a_parte_declarada_chega_no_td_e_nao_so_no_th():
    """Medido também: declarar a largura no `<th>` NÃO resolve — o fornecedor volta
    pros 107px. Ela tem que estar na célula que carrega o `max-width:0`."""
    assert "td.rel-flex:nth-child(" in _TPL, \
        "a regra de divisão saiu do td — a segunda elástica volta ao piso"
    assert "col.parte" in _TPL, "a divisão deixou de vir das colunas do relatório"


def test_a_elastica_e_sempre_de_nome_livre():
    """Marcar a coluna de Valor ou de Data como elástica encolheria justamente o
    que tem tamanho previsível — e deixaria o nome, que é o longo, fixo."""
    fonte = open(pr.__file__, encoding="utf-8").read()
    # A lista é de colunas de NOME LIVRE — a que não tem largura previsível. Um
    # relatório novo com outro nome pra mesma coisa entra aqui; `valor`, `data` ou
    # qualquer coisa medida em dígitos, não. `lead` entrou com a aba "Leads do
    # chip": lá a coluna do nome se chama lead, e as vizinhas (chip, hora, espera,
    # contagem de mensagens) todas têm tamanho fixo.
    for m in re.finditer(r'_col\("(\w+)",[^)]*flex=True', fonte):
        assert m.group(1) in {"descricao", "contraparte", "cliente", "vendedor",
                              "evento", "lead"}, \
            f"coluna elástica inesperada: {m.group(1)}"


def test_o_col_carrega_o_flag():
    assert pr._col("x", "X")["flex"] is False
    assert pr._col("x", "X", flex=True)["flex"] is True


def test_a_elastica_corta_no_fim_e_nao_no_comeco():
    """`text-overflow:ellipsis` corta no fim. O começo é o que identifica o
    cliente — foi exatamente o que o print perdeu."""
    assert "td.rel-flex" in _TPL
    assert "text-overflow:ellipsis" in _TPL
    assert "max-width:0" in _TPL, \
        "sem max-width:0 a célula cresce com o conteúdo e a reticência nunca aparece"
    assert 'class="rel-flex" title=' in _TPL, \
        "o nome cortado tem que aparecer inteiro no title (passar o mouse)"


def test_o_cabecalho_gruda():
    assert "<thead>" in _TPL and "</tbody>" in _TPL and "<tfoot>" in _TPL, \
        "sticky header exige thead/tbody/tfoot de verdade"
    assert re.search(r"thead\s+th\{position:sticky", _TPL), \
        "o cabeçalho não gruda ao rolar"


# ── a busca ──────────────────────────────────────────────────────────────────
def test_a_busca_da_tela_nao_convive_com_a_do_servidor():
    """Duas caixas de busca na mesma tela é pergunta sem resposta óbvia. A do
    servidor (name="q") existe nas abas com filtro_extra; a da tela só pode
    aparecer nas outras."""
    i = _TPL.index('id="rel-q"')
    guarda = _TPL.rindex("{% if not dados.filtro_extra %}", 0, i)
    fecha = _TPL.index("{% endif %}", i)
    assert guarda < i < fecha, \
        "a busca na tela tem que estar dentro de {% if not dados.filtro_extra %}"
    # e a do servidor continua no ramo oposto
    j = _TPL.index('name="q"')
    assert _TPL.rindex("{% if dados.filtro_extra %}", 0, j) < j


def test_a_celula_do_total_leva_os_centavos_crus():
    """Recalcular fazendo parse de 'R$ 2.182,90' quebraria no primeiro milhar."""
    assert 'data-c="{{ row[col.chave] }}"' in _TPL


# ── o comportamento do filtro, rodando de verdade ────────────────────────────
_CENARIO = r"""
%s
var out = [];
function estado(){
  var vis = LINHAS.filter(function(tr){ return tr.style.display !== 'none'; });
  return {n: vis.length, conta: EL['rel-conta'].textContent,
          total: CEL_TOT.textContent, rotulo: ROT.textContent};
}
CX.value = ''; APLICAR(); out.push(['sem_filtro', estado()]);
CX.value = 'pelle'; APLICAR(); out.push(['filtrando', estado()]);
CX.value = 'nao_existe_zzz'; APLICAR(); out.push(['sem_resultado', estado()]);
CX.value = ''; APLICAR(); out.push(['limpou', estado()]);
console.log(JSON.stringify(out));
"""


def _monta_ambiente(js_filtro: str) -> str:
    """DOM de mentira com três linhas conhecidas, pra conferir a aritmética."""
    return r"""
var _els = {};
function _cel(txt, cents){
  return {textContent: txt, style:{}, getAttribute:function(){ return cents; },
          querySelector:function(){ return null; }};
}
function _linha(txt, cents){
  var celValor = _cel('', String(cents));
  var tr = {textContent: txt, style:{},
    querySelector:function(sel){
      if (sel === 'td[data-c]') return celValor;
      if (sel === 'td[colspan]') return null;
      return null; }};
  return tr;
}
var L1 = _linha('01/08 Espaco Pelle Clinica Aporte', 20000);
var L2 = _linha('02/08 Espaco Pelle Clinica Outros', 250000);
var L3 = _linha('03/08 Anderson Venici Outros', 25000);
var LINHAS = [L1, L2, L3];
var CEL_TOT = {textContent: 'R$ 2.950,00'};
var ROT = {textContent: 'Total'};
var _rodape = { querySelector:function(){ return CEL_TOT; }, cells:[ROT] };
var _corpo = { rows: LINHAS };
var _tbl = { tBodies: [_corpo] };
var CX = { value:'', addEventListener:function(ev, fn){ if (ev === 'input') APLICAR_REF = fn; } };
_els['rel-q'] = CX; _els['rel-conta'] = {textContent:'3'}; _els['rel-tot'] = _rodape;
var EL = _els;
var APLICAR_REF = null;
global.document = {
  getElementById:function(id){ return _els[id] || null; },
  querySelector:function(sel){ return sel.indexOf('table') !== -1 ? _tbl : null; }
};
""" + js_filtro + r"""
var APLICAR = APLICAR_REF;
"""


@pytest.fixture(scope="module")
def rodado(tmp_path_factory):
    if not shutil.which("node"):
        pytest.skip("sem node no ambiente")
    m = re.search(r"<script>\s*(// Filtro NA TELA.*?)</script>", _TPL, re.S)
    assert m, "não achei o bloco do filtro no template"
    js = _monta_ambiente(m.group(1))
    d = tmp_path_factory.mktemp("relq")
    alvo = d / "filtro.js"
    alvo.write_text(_CENARIO % js, encoding="utf-8")
    r = subprocess.run(["node", str(alvo)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr[:900]
    return dict(json.loads(r.stdout))


def test_sem_filtro_mostra_tudo_e_o_total_original(rodado):
    e = rodado["sem_filtro"]
    assert e["n"] == 3 and str(e["conta"]) == "3"
    assert e["total"] == "R$ 2.950,00", "sem filtro o total tem que ser o do servidor"
    assert e["rotulo"] == "Total"


def test_filtrar_recalcula_o_total(rodado):
    """O caso que importa mais que o layout inteiro. Duas linhas da Pelle somam
    R$ 2.700,00 — se aparecesse R$ 2.950,00, a tela estaria mentindo."""
    e = rodado["filtrando"]
    assert e["n"] == 2 and str(e["conta"]) == "2"
    assert e["total"] == "R$ 2.700,00", f"total não recalculou: {e['total']}"
    assert "filtro" in e["rotulo"].lower(), \
        "o rótulo tem que avisar que aquele total é do filtro, não do período"


def test_sem_resultado_zera_o_total(rodado):
    e = rodado["sem_resultado"]
    assert e["n"] == 0 and str(e["conta"]) == "0"
    assert e["total"] == "R$ 0,00"


def test_limpar_devolve_o_total_do_servidor(rodado):
    """Recalcular a soma de tudo daria o mesmo número por acaso; devolver o
    ORIGINAL garante que nenhum arredondamento nosso se meta no meio."""
    e = rodado["limpou"]
    assert e["n"] == 3 and e["total"] == "R$ 2.950,00" and e["rotulo"] == "Total"


# ── o formatador do JS tem que ser o mesmo do servidor ───────────────────────
def test_o_brl_do_js_bate_com_o_do_servidor():
    """Duas razões pra isto existir.

    A primeira: limpar o filtro devolve o total do SERVIDOR e filtrar mostra o
    total do JS. Se os dois formatarem diferente, o número muda de cara na frente
    do dono sem o valor ter mudado.

    A segunda é a que quebrou o CI em 26/08: a versão anterior usava
    `toLocaleString('pt-BR')`, que depende do ICU do runtime. Onde o ICU é
    completo dá "R$ 2.700,00"; num Node de ICU reduzido cai no padrão americano
    e dá "R$ 2,700.00". Passava aqui e falhava lá — e passaria pro cliente
    conforme o navegador dele.
    """
    if not shutil.which("node"):
        pytest.skip("sem node no ambiente")
    m = re.search(r"(function brl\(c\)\{.*?\n    \})", _TPL, re.S)
    assert m, "não achei o formatador no template"
    valores = [0, 1, 99, 100, 250, 20000, 250000, 100000, 999999, 123456789,
               -2500, -100000]
    js = m.group(1) + "\nconsole.log(JSON.stringify(%s.map(brl)));" % json.dumps(valores)
    r = subprocess.run(["node", "-e", js], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr[:600]
    do_js = json.loads(r.stdout.strip().splitlines()[-1])
    do_servidor = [pt.brl(v) for v in valores]
    assert do_js == do_servidor, (
        "JS e servidor formatam diferente:\n  js=%s\n  py=%s" % (do_js, do_servidor))


def test_o_formatador_nao_depende_de_locale():
    """toLocaleString/Intl no caminho do dinheiro é justamente o que muda de
    resultado conforme onde o código roda."""
    m = re.search(r"function brl\(c\)\{.*?\n    \}", _TPL, re.S)
    corpo = m.group(0)
    assert "toLocaleString" not in corpo and "Intl" not in corpo, \
        "o formatador voltou a depender do ICU do runtime"
