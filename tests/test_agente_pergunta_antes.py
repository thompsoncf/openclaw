"""Quando o agente PODE montar um orçamento — e as duas vezes em que ele não pode.

No primeiro teste com um cliente de verdade o agente montou orçamento em toda
pergunta de preço. Duas consequências, e o dono do produto viu as duas:

    "toda hora ele monta um orçamento; o ideal é sempre perguntar se a pessoa
     quer um orçamento — não faz sentido enviar direto"
    "montar orçamento sem a data, a hora e a quantidade de convidados não pode"

E o estrago do segundo não é teórico. Sem data, ela cotou "150 pessoas" juntando
o PACOTE ESSENTIAL - SEGUNDA A QUINTA (R$ 5.760) COM o SEXTA A DOMINGO (R$ 7.200)
no mesmo documento e somando: **Total: R$ 13.360** por um salão que custa um ou
outro. São alternativas, e é a data que decide qual — a mesma data que ninguém
tinha perguntado.

As duas travas vivem no CÓDIGO, não na instrução da IA. Instrução a IA às vezes
ignora, e o que sai daqui é preço com o nome da empresa em cima.

O convite é frase fixa (`_CONVITE_ORCAMENTO`) porque é ela que o código procura no
histórico pra saber se já ofereceu. Se a IA reescrevesse o convite a cada vez, a
trava dependeria de a IA se comportar — e o ponto da trava é não depender.

Funções puras: nada aqui precisa de banco nem de WhatsApp.
"""
import pytest

from finance.agente import (_CONVITE_ORCAMENTO, _falta_pro_orcamento, _lista_br,
                            _ja_ofereceu_orcamento, _texto_oferecendo, _texto_perguntando)

COMPLETO = {"data": "2026-12-31", "inicio": "21:00", "convidados": 50}


# ------------------------------------------- sem data, hora e convidados não orça

def test_conversa_crua_falta_tudo():
    assert _falta_pro_orcamento({}) == ["a data do evento", "o horário de início",
                                        "quantos convidados"]


def test_com_os_tres_nao_falta_nada():
    assert _falta_pro_orcamento(COMPLETO) == []


@pytest.mark.parametrize("chave, rotulo", [("data", "a data do evento"),
                                           ("inicio", "o horário de início"),
                                           ("convidados", "quantos convidados")])
def test_qualquer_um_dos_tres_que_falte_segura_o_orcamento(chave, rotulo):
    assert _falta_pro_orcamento({**COMPLETO, chave: None}) == [rotulo]


def test_o_caso_dos_150_convidados():
    """O chamado: ele disse quantas pessoas e mais nada. Faltam data e hora — e é a
    data que escolhe ENTRE os dois pacotes que ela somou."""
    assert _falta_pro_orcamento({"convidados": 150}) == ["a data do evento",
                                                         "o horário de início"]


def test_evento_ausente_nao_estoura():
    assert len(_falta_pro_orcamento(None)) == 3


def test_a_pergunta_sai_legivel():
    txt = _texto_perguntando("Segunda a quinta sai R$ 5.760.", _falta_pro_orcamento({}))
    assert "Segunda a quinta sai R$ 5.760." in txt          # responde o que dá
    assert "a data do evento, o horário de início e quantos convidados" in txt
    assert _CONVITE_ORCAMENTO not in txt                    # ainda não é hora de oferecer


def test_lista_em_portugues():
    assert _lista_br(["a"]) == "a"
    assert _lista_br(["a", "b"]) == "a e b"
    assert _lista_br(["a", "b", "c"]) == "a, b e c"
    assert _lista_br([]) == ""


# --------------------------------------------------- e pergunta antes de mandar

def test_conversa_nova_ninguem_ofereceu():
    assert _ja_ofereceu_orcamento([("in", "lead", "quanto custa?")]) is False
    assert _ja_ofereceu_orcamento([]) is False
    assert _ja_ofereceu_orcamento(None) is False


def test_o_convite_do_agente_conta():
    msgs = [("in", "lead", "quanto custa?"),
            ("out", "bot", "Sai R$ 5.760.\n\n" + _CONVITE_ORCAMENTO),
            ("in", "lead", "sim")]
    assert _ja_ofereceu_orcamento(msgs) is True


def test_cliente_repetindo_a_frase_nao_vale():
    """A trava é "o AGENTE ofereceu", não "a frase apareceu": senão bastava o cliente
    colar o texto pra pular a etapa."""
    assert _ja_ofereceu_orcamento([("in", "lead", _CONVITE_ORCAMENTO)]) is False


def test_vendedor_falando_nao_conta_como_oferta_do_agente():
    assert _ja_ofereceu_orcamento([("out", "humano", _CONVITE_ORCAMENTO)]) is False


def test_texto_sem_a_frase_nao_conta():
    """Resposta que fala de orçamento mas não CONVIDA não abre a porta."""
    msgs = [("out", "bot", "Te mando um orçamento assim que tiver os dados.")]
    assert _ja_ofereceu_orcamento(msgs) is False


def test_a_oferta_carrega_a_resposta_junto():
    txt = _texto_oferecendo("O pacote de sexta a domingo sai R$ 7.200.")
    assert txt.startswith("O pacote de sexta a domingo sai R$ 7.200.")
    assert txt.endswith(_CONVITE_ORCAMENTO)


def test_oferta_sem_resposta_ainda_pergunta():
    assert _texto_oferecendo("") == _CONVITE_ORCAMENTO


def test_a_frase_do_convite_e_uma_pergunta():
    """Ela é procurada por igualdade no histórico; se alguém "melhorar" o texto sem
    olhar aqui, a trava para de reconhecer a própria oferta e o agente nunca mais
    monta orçamento nenhum."""
    assert _CONVITE_ORCAMENTO.endswith("?")
