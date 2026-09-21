"""O conector da InsureMO (`finance/cotacao_insuremo.py`), contra as AMOSTRAS DA DOC.

As respostas usadas aqui são as da página "Policy Rating API" da InsureMO, lidas
em 21/09/2026 — não são inventadas. É essa a diferença entre um conector que
passa no teste e um que funciona na primeira chamada real.

O que estes testes prendem, em ordem de quanto custaria errar:

* `test_o_total_sai_do_DuePremium_e_nao_do_GrossPremium` — na MESMA página,
  `GrossPremium` é 10.8 (com imposto) no exemplo de proposta e 300 (sem imposto)
  no de endosso. Ler o total dele daria, no segundo caso, prêmio 8% menor e
  comissão 8% maior, em silêncio.
* `test_juro_de_parcelamento_nao_entra_no_premio` — `TotalPremium` traz juro de
  financiamento. Juro não é prêmio, e somá-lo infla a comissão.
* `test_a_comissao_bate_com_a_que_a_insuremo_mandou` — a doc manda `Commission`
  em dinheiro E `CommissionRate` em percentual. Se a nossa conta sobre o líquido
  não der o mesmo número, uma das duas está errada — e é a nossa.
* `test_calculo_recusado_nao_vira_oferta_de_zero` — oferta de R$ 0,00 fica em
  primeiro lugar no comparativo pra sempre.
"""
import json

import pytest

from finance import cotacao_provedores as cp
from finance.cotacao_insuremo import ProvedorInsureMO

# ─────────────────── as amostras da doc (docs.insuremo.com, Policy Rating API)

#: Resposta de `/proposal/core/proposal/v1/calculateEx` — produto TBTI, 1 cobertura.
#: Recortada nos campos que o conector lê; os valores são os da doc, verbatim.
AMOSTRA_PROPOSTA = {
    "AdjustedPremium": 10.8, "AnnualPremium": 10, "BeforeVatPremium": 10,
    "Commission": 1, "CommissionRate": 0.1, "DuePremium": 10.8,
    "GrossPremium": 10.8, "IsPremiumCalcSuccess": "Y",
    "ProductCode": "TBTI", "ProposalNo": "PABTBTI0001352950",
    "SumInsured": 300000, "TotalPremium": 10.8, "Vat": 0.8, "VatRate": 0.08,
    "PolicyLobList": [{
        "ProductCode": "TBTI",
        "PolicyRiskList": [{
            "RiskName": "InsuredName",
            "PolicyCoverageList": [{
                "CoverageName": "Baggage Loss or Damage", "DuePremium": 10.8,
                "ProductElementCode": "C100692", "SumInsured": 300000, "Vat": 0.8,
            }],
        }],
    }],
}

#: Resposta do endosso, onde `GrossPremium` (300) NÃO inclui o imposto (24) e
#: `TotalPremium` (340.2) traz 16.2 de juro. É a amostra que pega o erro.
AMOSTRA_ENDOSSO = {
    "AdjustedPremium": 300, "BeforeVatPremium": 300, "Commission": 30,
    "CommissionRate": 0.1, "DuePremium": 324, "GrossPremium": 300,
    "PremiumInterest": 15, "TaxInterest": 1.2, "TotalInterest": 16.2,
    "TotalPremium": 340.2, "Vat": 24, "ProductCode": "TBTI",
}

PRODUTO = {"codigo": "AUTO_BR", "versao": "1.0", "seguradora": "Seguradora X",
           "risco": "R10007", "coberturas": [{"codigo": "C100692", "soma_segurada": 100000}],
           "campos_veiculo": {"placa": "LicensePlateNo", "fipe": "FipeCode",
                              "ano_modelo": "ModelYear"}}

RISCO = {
    "segurado": {"nome": "Fulano de Tal", "cpf": "52998224725",
                 "nascimento": "1985-04-12", "cep": "64000000"},
    "veiculo": {"placa": "ABC1D23", "fipe": "004445-0", "ano_modelo": 2022,
                "marca_modelo": "FIAT ARGO 1.0"},
    "cobertura": {"vigencia_inicio": "2026-10-01", "classe_bonus": 5},
}


@pytest.fixture()
def prov(monkeypatch):
    monkeypatch.setenv("COTACAO_INSUREMO_BASE_URL", "https://exemplo.insuremo.test")
    monkeypatch.setenv("COTACAO_INSUREMO_API_KEY", "chave-de-teste")
    monkeypatch.setenv("COTACAO_INSUREMO_ORG", "10002")
    monkeypatch.setenv("COTACAO_INSUREMO_AGENTE", "LIBERAL01")
    monkeypatch.setenv("COTACAO_INSUREMO_PRODUTOS", json.dumps([PRODUTO]))
    return ProvedorInsureMO()


# ─────────────────────────────────────────────────────────── o dinheiro

def test_a_amostra_da_doc_vira_oferta(prov):
    o = prov._oferta_de(AMOSTRA_PROPOSTA, PRODUTO)
    assert o.seguradora == "Seguradora X"
    assert o.premio_liquido_centavos == 1000     # BeforeVatPremium 10,00
    assert o.iof_centavos == 80                  # Vat 0,80
    assert o.premio_total_centavos == 1080       # DuePremium 10,80
    assert o.ref_externa == "PABTBTI0001352950"
    assert o.coberturas[0]["nome"] == "Baggage Loss or Damage"


def test_a_comissao_bate_com_a_que_a_insuremo_mandou(prov):
    """A doc manda `Commission: 1` e `CommissionRate: 0.1`. A nossa conta é sobre
    o LÍQUIDO (10,00 × 10% = 1,00) — e tem que dar o mesmo número."""
    o = prov._oferta_de(AMOSTRA_PROPOSTA, PRODUTO)
    assert o.comissao_pct == 10.0
    assert o.comissao_estimada() == 100          # R$ 1,00 — igual ao `Commission` da doc


def test_o_total_sai_do_DuePremium_e_nao_do_GrossPremium(prov):
    """A armadilha da própria doc: aqui `GrossPremium` é 300 e NÃO inclui o
    imposto de 24. Quem lê o total dele cobra 8% a menos e estima 8% a mais de
    comissão — nos dois sentidos errado, e sem nenhum sinal na tela."""
    o = prov._oferta_de(AMOSTRA_ENDOSSO, PRODUTO)
    assert o.premio_total_centavos == 32400      # DuePremium 324,00
    assert o.premio_liquido_centavos == 30000    # BeforeVatPremium 300,00
    assert o.iof_centavos == 2400                # Vat 24,00
    assert o.comissao_estimada() == 3000         # 10% de 300,00 = o `Commission: 30` da doc


def test_juro_de_parcelamento_nao_entra_no_premio(prov):
    """`TotalPremium` 340,20 = prêmio 324,00 + 16,20 de juro. Juro de
    financiamento não é prêmio: entra, e a comissão sai maior do que é."""
    o = prov._oferta_de(AMOSTRA_ENDOSSO, PRODUTO)
    assert o.premio_total_centavos != 34020


def test_sem_DuePremium_o_total_e_a_soma_e_nao_um_chute(prov):
    amostra = dict(AMOSTRA_ENDOSSO)
    amostra.pop("DuePremium")
    o = prov._oferta_de(amostra, PRODUTO)
    assert o.premio_total_centavos == 32400      # 300,00 + 24,00


def test_calculo_recusado_nao_vira_oferta_de_zero(prov):
    """Oferta de R$ 0,00 ficaria em primeiro lugar no comparativo pra sempre."""
    amostra = dict(AMOSTRA_PROPOSTA, IsPremiumCalcSuccess="N")
    assert prov._oferta_de(amostra, PRODUTO) is None


# ─────────────────────────────────────────────────────────── o pedido

def test_o_veiculo_entra_pelo_mapa_do_produto(prov):
    """O schema genérico da InsureMO não tem placa nem FIPE — quem nomeia esses
    campos é a configuração do tenant, e é ela que o mapa carrega."""
    corpo = prov._envelope(RISCO, PRODUTO)
    r = corpo["PolicyLobList"][0]["PolicyRiskList"][0]
    assert r["LicensePlateNo"] == "ABC1D23"
    assert r["FipeCode"] == "004445-0"
    assert r["ModelYear"] == 2022
    assert r["IdNo"] == "52998224725" and r["DateOfBirth"] == "1985-04-12"
    assert corpo["AgentCode"] == "LIBERAL01" and corpo["OrgCode"] == "10002"
    assert corpo["PremiumCurrencyCode"] == "BRL"


def test_a_vigencia_fecha_na_vespera_do_aniversario(prov):
    """12 meses, não 366 dias: começar 01/10/2026 e terminar 01/10/2027 daria um
    dia a mais de cobertura — e um prêmio que não bate com o da apólice."""
    corpo = prov._envelope(RISCO, PRODUTO)
    assert corpo["EffectiveDate"] == "2026-10-01"
    assert corpo["ExpiryDate"] == "2027-09-30"


def test_produto_sem_mapa_de_veiculo_avisa_no_log(prov, caplog):
    """Preço de auto calculado sem o carro é número errado com cara de certo."""
    sem_mapa = dict(PRODUTO, campos_veiculo={})
    with caplog.at_level("WARNING"):
        prov._envelope(RISCO, sem_mapa)
    assert "campos_veiculo" in caplog.text


# ────────────────────────────────────────────── uma chamada por produto

def test_uma_chamada_por_produto_e_o_comparativo_nasce_dai(prov, monkeypatch):
    """A Calculate API precifica UM produto de UM tenant — não existe chamada que
    devolva várias seguradoras. O comparativo sai de N chamadas."""
    produtos = [dict(PRODUTO, codigo="P1", seguradora="Alfa"),
                dict(PRODUTO, codigo="P2", seguradora="Beta")]
    monkeypatch.setenv("COTACAO_INSUREMO_PRODUTOS", json.dumps(produtos))
    chamadas = []

    def _post(caminho, payload):
        chamadas.append(payload["ProductCode"])
        return dict(AMOSTRA_PROPOSTA)
    monkeypatch.setattr(prov, "_post", _post)
    ofertas = prov.cotar(RISCO)
    assert chamadas == ["P1", "P2"]
    assert [o.seguradora for o in ofertas] == ["Alfa", "Beta"]


def test_produto_que_falha_nao_derruba_os_outros(prov, monkeypatch):
    """Três preços e um erro valem mais, pro corretor, que nenhum preço."""
    produtos = [dict(PRODUTO, codigo="P1", seguradora="Alfa"),
                dict(PRODUTO, codigo="P2", seguradora="Beta")]
    monkeypatch.setenv("COTACAO_INSUREMO_PRODUTOS", json.dumps(produtos))

    def _post(caminho, payload):
        if payload["ProductCode"] == "P1":
            raise cp.ProvedorErro("500 no rating")
        return dict(AMOSTRA_PROPOSTA)
    monkeypatch.setattr(prov, "_post", _post)
    assert [o.seguradora for o in prov.cotar(RISCO)] == ["Beta"]


def test_se_todos_falham_o_erro_sobe_com_o_motivo(prov, monkeypatch):
    def _post(caminho, payload):
        raise cp.ProvedorErro("401 sem token")
    monkeypatch.setattr(prov, "_post", _post)
    with pytest.raises(cp.ProvedorErro, match="401"):
        prov.cotar(RISCO)


# ───────────────────────────────────────────────────── a configuração

def test_sem_produtos_configurados_o_erro_DIZ_o_que_configurar(monkeypatch):
    """"Configure o produto" sem dizer como só adia a pergunta."""
    monkeypatch.delenv("COTACAO_INSUREMO_PRODUTOS", raising=False)
    with pytest.raises(cp.ProvedorErro) as e:
        ProvedorInsureMO().cotar(RISCO)
    assert "COTACAO_INSUREMO_PRODUTOS" in str(e.value)
    assert "coberturas" in str(e.value)      # o exemplo vai junto


def test_produtos_com_json_quebrado_nao_passa_por_valido(monkeypatch):
    monkeypatch.setenv("COTACAO_INSUREMO_PRODUTOS", "{isto não é json}")
    with pytest.raises(cp.ProvedorErro, match="não é JSON"):
        ProvedorInsureMO().cotar(RISCO)


def test_a_emissao_ainda_nao_esta_documentada(prov):
    """Enquanto a página de emissão não for lida, o corretor segue no roteiro pro
    portal — que funciona. Ligar isto sem a doc seria inventar o contrato."""
    assert prov.suporta_emissao is False


def test_o_conector_e_carregado_pelo_nome_na_variavel(monkeypatch):
    """`COTACAO_PROVEDOR=insuremo` tem que achar `finance/cotacao_insuremo.py`
    sozinho — sem ninguém editar a lista de provedores."""
    monkeypatch.setenv("COTACAO_PROVEDOR", "insuremo")
    assert cp.provedor_ativo().chave == "insuremo"
