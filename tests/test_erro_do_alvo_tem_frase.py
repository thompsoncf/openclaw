"""A ficha do lead dizia "erro" e mais nada.

`campanha_alvos` guarda a falha em dois campos: `wa_erro_codigo` (do provedor) e
`wa_erro_msg` (texto). A tela lia SÓ a mensagem — e a maior parte das falhas chega
por webhook de status, que traz o `ErrorCode` sem frase nenhuma junto. Resultado:
o motivo estava gravado o tempo todo e o dono via um tooltip vazio.

Medido em produção: 40 alvos em erro numa conta, TODOS com código e nenhum com
frase. Nenhum deles dizia por que falhou.

`rotulo_erro_alvo` fecha isso: mensagem do provedor quando existe (é mais
específica que qualquer texto nosso), senão a tradução do código, senão o código
cru — que ainda é uma informação, ao contrário do vazio.
"""
import pytest

from finance import prospec_convite as pc


# ------------------------------------------------- a mensagem do provedor manda

def test_mensagem_do_provedor_ganha_do_rotulo():
    """Ela é mais específica que o texto genérico do código."""
    r = pc.rotulo_erro_alvo("63024", "O número +55 86 9xxx não está no WhatsApp")
    assert r == "O número +55 86 9xxx não está no WhatsApp"


def test_mensagem_em_branco_nao_vale_como_mensagem():
    """'' e '   ' são o caso comum do webhook — tem que cair no código."""
    for vazio in ("", "   ", None):
        assert pc.rotulo_erro_alvo("63024", vazio) == pc.ERRO_ALVO_ROT["63024"]


# --------------------------------------------------- código vira frase legível

@pytest.mark.parametrize("codigo", sorted(pc.ERRO_ALVO_ROT))
def test_codigo_conhecido_vira_frase(codigo):
    r = pc.rotulo_erro_alvo(codigo)
    assert r == pc.ERRO_ALVO_ROT[codigo]
    assert codigo not in r          # é frase pro dono, não o número cru


def test_o_caso_que_apareceu_na_conta():
    """63024 era 37 dos 40 alvos mudos."""
    assert "não tem WhatsApp" in pc.rotulo_erro_alvo("63024")


# ------------------------------------------- código desconhecido não some nem mente

def test_codigo_sem_traducao_mostra_o_numero_em_vez_de_chutar():
    """63049 e 63032 apareceram na conta e o projeto não sabe o significado exato.
    Inventar um motivo é pior que dizer 'recusou, o código é este'."""
    for codigo in ("63049", "63032", "99999"):
        r = pc.rotulo_erro_alvo(codigo)
        assert codigo in r and "recusou" in r


def test_sem_codigo_e_sem_mensagem_devolve_vazio():
    """Aí não há o que dizer mesmo — e a tela não põe tooltip."""
    assert pc.rotulo_erro_alvo(None, None) == ""
    assert pc.rotulo_erro_alvo("", "") == ""


def test_nunca_devolve_so_espaco():
    """Um tooltip com espaço em branco é o mesmo vazio, disfarçado."""
    for cod, msg in (("63024", None), ("63049", ""), (None, "  texto  "), ("", None)):
        r = pc.rotulo_erro_alvo(cod, msg)
        assert r == r.strip()
