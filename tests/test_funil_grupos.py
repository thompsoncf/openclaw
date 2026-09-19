"""As três abas do funil de Serviços, e a data que abre a linha no nicho evento.

POR QUE ISTO EXISTE. Em 18/09/2026 a Prime (conta 34) tinha 27 propostas numa
lista só, sem filtro e sem busca — e QUINZE eram rascunho, a mais velha de
19/08. O que precisava de alguém hoje ficava no meio do que já fechou e do que
está com o cliente; a única forma de separar era ler as 27 linhas.

A regra das abas é DERIVADA do que a linha já mostra (`linha_do_funil`), e é
isso que este arquivo protege: "Precisa de mim" tem que conter exatamente as
linhas com botão verde ou selo coral. Uma segunda régua escrita à parte
divergiria da primeira, e aí a aba prometeria uma coisa e a lista mostraria
outra.
"""
from __future__ import annotations

from datetime import date

import pytest

from finance import vendas as v


def _painel(**kw):
    """A linha de verdade, montada pelo mesmo caminho que a tela usa."""
    base = dict(status="rascunho", nunca_enviada=False, tem_contrato=True)
    base.update(kw)
    return v.linha_do_funil(**base)


# ------------------------------------------------------------------- as abas

def test_botao_verde_manda_a_linha_pra_precisa_de_mim():
    """A promessa da aba: onde há ação, há trabalho meu."""
    pn = _painel(status="rascunho", nunca_enviada=True)
    assert pn["acao"], "o teste parou de testar: esta linha deveria ter ação"
    assert v.grupo_do_funil(status="rascunho", painel=pn) == v.GRUPO_MEU


def test_selo_coral_sem_botao_tambem_e_trabalho_meu():
    """Coral quer dizer "custa dinheiro se ficar assim". Mesmo sem botão verde,
    não pode descansar em "Com o cliente"."""
    pn = {"selos": [{"texto": "Fora da agenda", "tom": "coral"}], "acao": None}
    assert v.grupo_do_funil(status="aprovada", painel=pn) == v.GRUPO_MEU


def test_proposta_com_o_cliente_e_sem_pendencia_nao_me_cobra():
    pn = {"selos": [], "acao": None}
    assert v.grupo_do_funil(status="enviado", painel=pn) == v.GRUPO_CLIENTE


def test_fechada_e_limpa_sai_do_caminho():
    assert v.grupo_do_funil(status="fechado", painel={"selos": [], "acao": None}) \
        == v.GRUPO_FECHADA


def test_fechada_com_parcela_sem_comprovante_volta_pra_minha_aba():
    """O caso da Claudia (nº 18, conta 34): contrato assinado, R$ 2.500 recebidos
    e nenhum comprovante anexado. "Fechadas" tem que querer dizer "não preciso
    olhar" — senão a aba vira depósito."""
    pn = _painel(status="fechado", contrato_numero=5, contrato_assinado=True,
                 pagamentos={"pagas": 1, "total": 3, "sem_comprovante": 1})
    assert any(s["tom"] == "coral" for s in pn["selos"])
    assert v.grupo_do_funil(status="fechado", painel=pn) == v.GRUPO_MEU


def test_sem_painel_nenhum_a_linha_nao_inventa_urgencia():
    """Quem chamar sem o painel não pode receber "precisa de mim" de brinde."""
    assert v.grupo_do_funil(status="enviado") == v.GRUPO_CLIENTE
    assert v.grupo_do_funil(status="fechado") == v.GRUPO_FECHADA


def test_os_tres_grupos_tem_rotulo_e_nao_sobra_nenhum():
    """A tela desenha as abas a partir de `GRUPOS`; um grupo sem rótulo seria uma
    aba que some, e uma linha sem aba some junto."""
    chaves = {c for c, _ in v.GRUPOS}
    assert chaves == {v.GRUPO_MEU, v.GRUPO_CLIENTE, v.GRUPO_FECHADA}
    assert all(rotulo.strip() for _, rotulo in v.GRUPOS)


@pytest.mark.parametrize("status", ["rascunho", "enviado", "aprovada", "fechado", ""])
def test_toda_linha_cai_em_alguma_aba(status):
    """Nenhum status pode ficar fora das três — linha sem aba é linha invisível."""
    g = v.grupo_do_funil(status=status, painel={"selos": [], "acao": None})
    assert g in {c for c, _ in v.GRUPOS}


# ------------------------------------------------------- a data que abre a linha

def test_a_data_da_festa_vira_dia_e_mes_curto():
    d = v.data_da_linha({"data": "2027-07-24"})
    assert d["dia"] == "24" and d["mes"] == "jul 27"
    assert d["titulo"] == "24/07/2027" and d["sem_data"] is False


def test_dia_de_um_digito_vem_com_zero_pra_coluna_nao_dancar():
    assert v.data_da_linha({"data": "2027-09-04"})["dia"] == "04"


def test_a_proposta_22_sem_data_sai_marcada():
    """Renata Tatiana, nº 22, R$ 9.650, enviada ao cliente sem tipo e sem data.
    Hoje ela passa despercebida no meio das outras 26."""
    for evento in (None, {}, {"data": ""}, {"data": "sei lá"}, "nem é dict"):
        d = v.data_da_linha(evento)
        assert d["sem_data"] is True
        assert d["mes"] == "sem data"


def test_no_recorrente_nao_existe_bloco_de_data():
    """A ZAQ vende mensalidade: uma coluna de data vazia em 43 leads foi
    exatamente o erro do Raio-X (seção 6 do CLAUDE.md)."""
    assert v.data_da_linha({"data": "2027-07-24"}, modo="recorrente") is None
    assert v.data_da_linha(None, modo="recorrente") is None


def test_o_ano_vira_dois_digitos_e_a_virada_de_seculo_nao_quebra():
    assert v.data_da_linha({"data": "2028-02-05"})["mes"] == "fev 28"
    assert v.data_da_linha({"data": "2100-01-09"})["mes"] == "jan 00"


def test_datas_reais_da_prime_saem_como_o_dono_le():
    """As cinco primeiras do funil dela, na ordem em que aparecem."""
    esperado = {
        "2027-01-30": ("30", "jan 27"),
        "2027-06-19": ("19", "jun 27"),
        "2026-12-17": ("17", "dez 26"),
        "2027-09-04": ("04", "set 27"),
        "2027-05-07": ("07", "mai 27"),
    }
    for iso, (dia, mes) in esperado.items():
        d = v.data_da_linha({"data": iso})
        assert (d["dia"], d["mes"]) == (dia, mes), iso
        assert d["iso"] == iso
        assert date.fromisoformat(d["iso"])
