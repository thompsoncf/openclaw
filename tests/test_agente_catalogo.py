"""Como o catálogo é APRESENTADO à IA — e por que "R$ 0" não pode existir.

O catálogo nasceu para serviço recorrente (setup + mensalidade), e a linha que ia
para a IA era sempre `setup R$X, mensal R$Y`. Duas contas reais quebram esse
formato, e nas duas o estrago aparece na conversa com o cliente:

**Locação.** A Prime Eventos tem 26 itens com mensal ZERO — pacote do espaço
(R$ 8.800 sexta a domingo), DJ, hora extra, taxa de limpeza. "mensal R$ 0" num
aluguel de salão não quer dizer nada: confunde, e no pior caso o cliente entende
que existe uma mensalidade zerada que vira cobrança depois.

**Preço não cadastrado.** Serviço com os dois valores em zero (uma assessoria de
consórcio, na base) virava `setup R$0, mensal R$0` — e a IA lendo isso pode
dizer ao cliente que é DE GRAÇA. Zero aqui nunca significa gratuito; significa
que ninguém preencheu.

Estes testes prendem o formato porque ele é texto que chega no cliente: a IA lê
esta linha e repete o número. Um "R$ 0" que escape daqui é desconto que a empresa
não deu.
"""
from finance.agente import _linha_catalogo, _reais


def _item(nome="Pacote", slug="pacote", setup=0, mensal=0):
    return {"nome": nome, "slug": slug, "setup_centavos": setup, "mensal_centavos": mensal}


# ------------------------------------------------------------------ formatação

def test_reais_sem_centavos_e_com_milhar():
    assert _reais(880000) == "R$ 8.800"
    assert _reais(576000) == "R$ 5.760"
    assert _reais(5000) == "R$ 50"
    assert _reais(0) == "R$ 0"          # a função formata; quem decide não usar é a linha


# ------------------------------------------------------- locação: só um preço

def test_locacao_nao_fala_em_mensalidade():
    """O caso da Prime: preço único, sem mensal. O cliente não pode ouvir 'mensal R$ 0'."""
    linha = _linha_catalogo(_item("PACOTE ESSENCIAL - SEXTA A DOMINGO - 2027",
                                  "pacote-sexta-domingo-2027", setup=880000))
    assert "R$ 8.800" in linha
    assert "mensal" not in linha.lower()
    assert "R$ 0" not in linha


def test_item_avulso_barato_continua_legivel():
    linha = _linha_catalogo(_item("LOCAÇÃO LEDS", "leds", setup=5000))
    assert linha == "- LOCAÇÃO LEDS (slug leds): R$ 50"


# --------------------------------------------------- recorrente: os dois preços

def test_servico_recorrente_mostra_entrada_e_mensal():
    linha = _linha_catalogo(_item("Agente de Atendimento", "agente", setup=450000, mensal=120000))
    assert "R$ 4.500" in linha and "R$ 1.200" in linha
    assert "por mês" in linha


def test_so_mensalidade_sem_entrada():
    linha = _linha_catalogo(_item("Suporte", "suporte", mensal=90000))
    assert "R$ 900 por mês" in linha
    assert "entrada" not in linha


# ------------------------------------------------ zero não é grátis, é em branco

def test_sem_preco_cadastrado_vira_sob_consulta():
    """O que protege a empresa: zero é campo vazio, nunca desconto."""
    linha = _linha_catalogo(_item("Assessoria de Consórcio", "assessoria"))
    assert "sob consulta" in linha
    assert "R$ 0" not in linha
    assert "grátis" not in linha.lower() and "gratuito" not in linha.lower()


def test_sob_consulta_manda_a_ia_perguntar():
    """Não basta esconder o zero: a IA precisa saber que ali ela pergunta, não inventa."""
    linha = _linha_catalogo(_item("Serviço novo", "novo"))
    assert "não invente" in linha.lower() or "pergunte" in linha.lower()


# ------------------------------------------------------------------ o slug fica

def test_slug_continua_na_linha():
    """É por ele que a IA devolve os itens escolhidos e o orçamento é montado —
    sem slug, a ação de orçamento não casa nada."""
    for item in (_item(setup=100000), _item(mensal=100000), _item()):
        assert "(slug pacote)" in _linha_catalogo(item)


def test_campo_ausente_nao_estoura():
    """Catálogo antigo ou consulta que não trouxe a coluna não pode derrubar o
    atendimento inteiro no meio de uma conversa."""
    assert "sob consulta" in _linha_catalogo({"nome": "X", "slug": "x"})
    assert "R$ 300" in _linha_catalogo({"nome": "X", "slug": "x", "setup_centavos": 30000,
                                        "mensal_centavos": None})
