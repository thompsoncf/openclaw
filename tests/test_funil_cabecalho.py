"""O cabeçalho do funil e o cartão, 25/09/2026. Mockup aprovado pelo dono
(docs/mockups/funil_cabecalho.html) com as respostas "1 - A" (o número da conversa
no 💬 de cada card), "b - sim" (as abas leves valem pra todas as telas da
Prospecção) e um pedido a mais: "faltou o nome do vendedor no card, senão toda vez
tenho que clicar nos ... pra saber".

O que este teste protege:
  * **as abas são leves em todas as telas** — é uma barra só (NAVBAR_CSS), e a aba
    aberta é marcada por um sublinhado, não por um botão verde cheio;
  * **o título diz o nome da EMPRESA** (nome fantasia), e não o do dono da conta —
    na Prime aparecia "MANOEL SOARES";
  * **o aviso do modelo do ramo virou um selo** na linha do título, e a faixa de
    duas linhas saiu;
  * **"Entraram em" é uma escolha só**;
  * **o nome do vendedor está no card** (gerência), escrito normal, com desempate
    pra dois vendedores com o mesmo primeiro nome — e a troca muda o nome só
    depois do OK do servidor;
  * **o número da conversa no 💬** tira a palavra que todos os números da conta
    têm em comum ("CP").
"""
import inspect

from tests.test_funil_por_mes import CONTA, _card, _lead, pool  # noqa: F401
from tests.test_funil_topo import _de, _html, _vendedor
from tests.test_painel_js_sintaxe import _render
from web import painel_prospeccao as pp

TPL = pp._KANBAN_TPL
FONTE = inspect.getsource(pp)


# ------------------------------------------------------------------ as abas
def test_as_abas_sao_leves_e_a_aberta_tem_sublinhado():
    css = pp.NAVBAR_CSS
    aba = css.split(".pnav{display:")[1].split("}")[0]
    assert "border:0" in aba and "background:transparent" in aba, "a aba voltou a ter contorno"
    aberta = css.split(".pnav.on{")[1].split("}")[0]
    assert "box-shadow:inset 0 -2px 0 var(--verde)" in aberta
    assert "background:var(--verde)" not in aberta, "a aba aberta voltou a ser um botão verde cheio"
    # é a MESMA barra do Follow-up e dos Anúncios: muda em todas as telas de uma vez
    assert pp.NAVBAR_CSS in pp._CSS


# ------------------------------------------------------------------ o título
def test_o_titulo_diz_o_nome_da_empresa_e_nao_o_do_dono(monkeypatch, pool):
    with pool.connection() as c:
        c.execute("alter table contas add column nome_fantasia text, add column razao_social text")
        c.execute("insert into contas (id, nome, nome_fantasia) values (%s,'MANOEL SOARES','PRIME EVENTOS')",
                  (CONTA,))
        c.commit()
    _lead(pool, "Alguém")
    html = _html(monkeypatch, pool)
    assert 'class="kbconta">PRIME EVENTOS<' in html
    assert "MANOEL SOARES" not in html.split('id="kbtit"')[1].split("</div>")[0]


def test_sem_a_coluna_do_nome_fantasia_o_quadro_abre_igual(monkeypatch, pool):
    """A leitura do nome da empresa é tolerante (savepoint): instalação sem a coluna
    abre o funil com o nome de sempre, em vez de derrubar a tela por um título."""
    lid = _lead(pool, "Alguém")
    assert f'data-id="{lid}"' in _html(monkeypatch, pool)


def test_o_aviso_do_modelo_virou_um_selo_na_linha_do_titulo():
    html = _render("prospeccao", gerencia=True, ramo_fora=1, ramo_rotulo="eventos")
    tit = html.split('id="kbtit"')[1].split('class="kbacoes"')[0]
    assert 'class="kbramo"' in tit and "1 coluna fora do modelo de eventos" in tit
    assert 'href="/painel/prospeccao/regua#modelo"' in tit
    assert "Seu funil não está no modelo" not in html, "a faixa de duas linhas voltou"
    assert 'class="kbramo"' not in _render("prospeccao", gerencia=True, ramo_fora=0)


def test_entraram_em_e_uma_escolha_so():
    assert '<span class="kbseg" role="group" aria-label="Entraram em">' in TPL
    assert ".foco .kbseg .pil.on{" in FONTE


# ------------------------------------------------------------------ o nome do vendedor
def test_os_nomes_curtos_escrevem_normal_e_desempatam_homonimos():
    curtos = pp._nomes_curtos({1: "JACQUELINE PRIME", 2: "pedro yan", 3: "Pedro Lima", 4: None, 5: "Thiago"})
    assert curtos == {1: "Jacqueline", 2: "Pedro Y.", 3: "Pedro L.", 4: "", 5: "Thiago"}


def test_o_card_mostra_o_nome_do_vendedor_pra_gerencia(monkeypatch, pool):
    ja, p1, p2 = (_vendedor(pool, "JACQUELINE PRIME"), _vendedor(pool, "Pedro Yan"),
                  _vendedor(pool, "Pedro Lima"))
    a, b, c_ = _lead(pool, "Cliente A"), _lead(pool, "Cliente B"), _lead(pool, "Cliente C")
    _de(pool, a, ja)
    _de(pool, b, p1)
    _de(pool, c_, p2)
    livre = _lead(pool, "Cliente Livre")
    html = _html(monkeypatch, pool)
    assert 'onclick="kbVendPop(event,this)">Jacqueline</button>' in _card(html, a)
    assert ">Pedro Y.</button>" in _card(html, b) and ">Pedro L.</button>" in _card(html, c_)
    assert 'class="kbav kbvn livre"' in _card(html, livre) and ">livre</button>" in _card(html, livre)
    # a lista de troca leva o MESMO nome curto
    assert 'data-curto="Pedro Y."' in html and 'data-curto="Jacqueline"' in html


def test_o_vendedor_nao_ve_o_proprio_nome_em_todo_card(monkeypatch, pool):
    ja = _vendedor(pool, "Jacqueline Prime")
    lid = _lead(pool, "Da Jacqueline")
    _de(pool, lid, ja)
    html = _html(monkeypatch, pool, gerencia=False, membro_id=ja)
    assert f'data-id="{lid}"' in html and "kbvn" not in _card(html, lid)


def test_a_troca_muda_o_nome_so_depois_do_ok():
    fonte = FONTE.split("function kbAtribuirVendedor(av,id,novo,nome,curto)")[1][:900]
    ok = fonte.index("if(!d.ok)")
    assert fonte.index("av.textContent=novo?(curto||nome||''):'livre'") > ok
    assert "b.getAttribute('data-curto')" in FONTE.split("function kbVendEscolhe(")[1][:400]


# ------------------------------------------------------------------ o número no 💬
def test_o_numero_da_conversa_tira_a_palavra_que_todos_tem_em_comum():
    assert pp._rotulos_de_chip("CP Zarb", {36: "CP Thiago"}) == {None: "Zarb", 36: "Thiago"}
    # sem palavra em comum, ou com um nome de uma palavra só, fica como está
    assert pp._rotulos_de_chip("Vendas", {9: "Suporte"}) == {None: "Vendas", 9: "Suporte"}
    assert pp._rotulos_de_chip("CP Zarb", {9: "CP"}) == {None: "CP Zarb", 9: "CP"}
    # o principal sem nome e o filho sem nome ganham um nome que se entende
    assert pp._rotulos_de_chip("", {9: ""}) == {None: "principal", 9: "outro número"}
