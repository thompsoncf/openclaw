"""O orçamento que o agente monta — o texto do WhatsApp e os números da folha.

Um orçamento real, gerado pelo agente da Prime Eventos em 15/08, saiu assim pro
cliente (mensagem 77490, orçamento 17):

    • PACOTE ESSENCIAL - SEGUNDA A QUINTA - 2027: R$ 0/mês
    • HORA EXTRA: R$ 0/mês
    • DJ: R$ 0/mês
    Setup: R$ 11920
    Total mensal: R$ 0

E a folha da proposta, no mesmo orçamento, mostrava a linha do pacote como
R$ 780.000,00 enquanto o total dizia R$ 11.920,00 — o papel se contradizendo
sozinho na frente do cliente.

São quatro defeitos independentes, e cada um chega no cliente por um caminho:

1. "R$ 0/mês" — o `_linha_catalogo` já tinha aprendido que zero é campo em branco,
   nunca desconto; este texto era montado à parte e ficou pra trás.
2. "R$ 11920" — dinheiro sem ponto de milhar.
3. A CENTENA DE VEZES. `itens[].setup` é lido em REAIS pela folha (o painel grava
   `round(centavos/100)`, o fechamento multiplica por 100 de volta). Só o agente
   gravava centavos ali, e a folha multiplicava por 100 outra vez.
4. A QUANTIDADE. O cliente pediu 6 horas de festa; a linha da hora extra saía
   "R$ 620", o preço de UMA. O número que ele leu não era o que ele pediu.

Mais o bloco "O evento" da proposta (data, convidados, início, encerramento), que
saía vazio porque o agente nunca gravava nada ali: o cliente dizia "31/12,
casamento, 21h, 50 convidados" e recebia um papel que não repetia nenhuma das
quatro coisas.

Funções puras — é texto que vai pro cliente, então trava-se o texto.
"""
import pytest

from finance.agente import (_bloco_orcamento, _evento_do_json, _itens_escolhidos,
                            _linha_orcamento)

LINK = "https://app.zaq-ia.com/proposta/abc123"


def _item(nome="DJ", setup=150000, mensal=0, qtd=1, slug="dj"):
    return {"nome": nome, "slug": slug, "setup_centavos": setup,
            "mensal_centavos": mensal, "qtd": qtd}


# --------------------------------------------------------- a linha do item

def test_locacao_mostra_o_preco_e_nao_fala_de_mes():
    linha = _linha_orcamento(_item("PACOTE ESSENTIAL - SEGUNDA A QUINTA - 2026", 576000))
    assert linha == "• PACOTE ESSENTIAL - SEGUNDA A QUINTA - 2026: R$ 5.760"
    assert "mês" not in linha and "R$ 0" not in linha


def test_quantidade_multiplica_e_mostra_o_unitario():
    """6 horas de festa num pacote de 4 são 2 horas extras. O cliente tem que ler
    o total da linha, e conferir de onde ele veio."""
    linha = _linha_orcamento(_item("HORA EXTRA", 62000, qtd=2))
    assert linha == "• HORA EXTRA (2×): R$ 1.240 (R$ 620 cada)"


def test_qtd_um_nao_polui_a_linha():
    assert "(1×)" not in _linha_orcamento(_item(qtd=1))
    assert "cada" not in _linha_orcamento(_item(qtd=1))


def test_recorrente_mantem_entrada_e_mensalidade():
    linha = _linha_orcamento(_item("Agente de Atendimento", 450000, 120000))
    assert "R$ 4.500 de entrada + R$ 1.200 por mês" in linha


def test_so_mensal():
    assert _linha_orcamento(_item("Suporte", 0, 90000)) == "• Suporte: R$ 900 por mês"


def test_sem_preco_nao_vira_zero():
    """Zero é campo vazio. A IA anunciando "R$ 0" é desconto que a empresa não deu."""
    linha = _linha_orcamento(_item("Serviço novo", 0, 0))
    assert "sob consulta" in linha
    assert "R$ 0" not in linha and "grátis" not in linha.lower()


# ------------------------------------------------------- o bloco inteiro

def test_o_orcamento_da_prime_do_jeito_certo():
    """O caso do chamado, com o pacote do ANO do evento (2026, não 2027)."""
    itens = [_item("PACOTE ESSENTIAL - SEGUNDA A QUINTA - 2026", 576000),
             _item("HORA EXTRA", 62000, qtd=2),
             _item("DJ", 150000), _item("TAXA DE LIMPEZA", 40000)]
    txt = _bloco_orcamento(itens, LINK)
    assert "R$ 0" not in txt                  # o defeito que saiu pro cliente
    assert "/mês" not in txt                  # aluguel de salão não tem mensalidade
    assert "Total: R$ 8.900" in txt           # 5.760 + 1.240 + 1.500 + 400
    assert LINK in txt


def test_milhar_com_ponto():
    """"R$ 11920" é dinheiro escrito errado — e era assim que saía."""
    txt = _bloco_orcamento([_item("PACOTE", 1192000)], LINK)
    assert "R$ 11.920" in txt and "R$ 11920" not in txt


def test_nao_oferece_consultor():
    """A instrução da empresa proíbe: o agente atende sozinho e só passa pra
    humano se o CLIENTE pedir. O convite estava fixo no código, então ele saía
    em todo orçamento, contra a instrução."""
    txt = _bloco_orcamento([_item()], LINK)
    assert "consultor" not in txt.lower()


def test_recorrente_separa_entrada_de_mensal():
    txt = _bloco_orcamento([_item("Agente", 450000, 120000),
                            _item("CRM", 350000, 90000, slug="crm")], LINK)
    assert "Entrada: R$ 8.000" in txt
    assert "Mensal: R$ 2.100" in txt


def test_tudo_sem_preco_nao_inventa_total():
    """Somar zeros e imprimir "Total: R$ 0" é pior que admitir que falta cadastro."""
    txt = _bloco_orcamento([_item("Novo", 0, 0)], LINK)
    assert "R$ 0" not in txt
    assert "não estão cadastrados" in txt


# ------------------------------------------------ o que a IA escolheu, validado

def test_slug_fora_do_catalogo_e_descartado():
    """É o que impede a IA de inventar item — e, por tabela, inventar preço."""
    catalogo = {"dj": _item()}
    assert _itens_escolhidos({"servicos": ["dj", "pacote-que-nao-existe"]}, catalogo) == \
        [{**_item(), "qtd": 1}]


def test_aceita_slug_solto_e_objeto_com_qtd():
    catalogo = {"dj": _item(), "hora-extra": _item("HORA EXTRA", 62000, slug="hora-extra")}
    r = _itens_escolhidos({"servicos": ["dj", {"slug": "hora-extra", "qtd": 3}]}, catalogo)
    assert [(i["slug"], i["qtd"]) for i in r] == [("dj", 1), ("hora-extra", 3)]


def test_slug_repetido_entra_uma_vez():
    """A IA às vezes lista o mesmo pacote duas vezes; o orçamento saía dobrado."""
    catalogo = {"dj": _item()}
    assert len(_itens_escolhidos({"servicos": ["dj", {"slug": "dj", "qtd": 2}]}, catalogo)) == 1


@pytest.mark.parametrize("qtd, esperado", [("3", 3), (0, 1), (-5, 1), (None, 1),
                                           ("abc", 1), (5000, 999)])
def test_qtd_torta_nao_derruba_nem_estoura(qtd, esperado):
    catalogo = {"dj": _item()}
    r = _itens_escolhidos({"servicos": [{"slug": "dj", "qtd": qtd}]}, catalogo)
    assert r[0]["qtd"] == esperado


# --------------------------------------------------- o evento que o cliente disse

def test_guarda_o_que_o_cliente_falou():
    ev = _evento_do_json({"evento": {"data": "2026-12-31", "convidados": "50",
                                     "inicio": "21:00", "tipo": "Casamento"}})
    assert ev == {"data": "2026-12-31", "inicio": "21:00", "tipo": "Casamento",
                  "convidados": 50}


def test_campo_que_ele_nao_disse_fica_de_fora():
    """Nada de completar com número redondo: 50 convidados não vira 100 porque é
    o padrão da casa. A folha mostra "—" quando não sabe, e "—" é honesto."""
    ev = _evento_do_json({"evento": {"data": "2026-12-31", "convidados": "", "fim": "  "}})
    assert ev == {"data": "2026-12-31"}


def test_convidados_torto_nao_vira_zero_nem_estoura():
    for v in ("muitos", None, "0", -3):
        assert "convidados" not in _evento_do_json({"evento": {"convidados": v}})


def test_sem_evento_no_json():
    assert _evento_do_json({}) == {}
    assert _evento_do_json({"evento": "31/12"}) == {}
