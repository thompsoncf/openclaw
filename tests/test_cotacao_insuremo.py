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


def test_o_conector_e_carregado_pelo_nome_na_variavel(monkeypatch):
    """`COTACAO_PROVEDOR=insuremo` tem que achar `finance/cotacao_insuremo.py`
    sozinho — sem ninguém editar a lista de provedores."""
    monkeypatch.setenv("COTACAO_PROVEDOR", "insuremo")
    assert cp.provedor_ativo().chave == "insuremo"


# ════════════════════════════════════════════════════════════════════════
# A AUTENTICAÇÃO (CAS) e a EMISSÃO — páginas "Policy Lifecycle API Hands-on",
# "Policy Persistence & Query API" e "Quotation", lidas em 21/09/2026.
# ════════════════════════════════════════════════════════════════════════

#: Resposta de `/quotation/core/quotation/v1/calculate` da página Quotation.
#: Vale por si: é o TERCEIRO exemplo independente em que a comissão é o
#: percentual sobre o LÍQUIDO — 930 × 30% = 279, o `Commission` que ela manda.
AMOSTRA_QUOTATION = {
    "AdjustedPremium": 1004.4, "AnnualPremium": 930, "BeforeVatPremium": 930,
    "Commission": 279, "CommissionRate": 0.3, "DuePremium": 1004.4,
    "GrossPremium": 1004.4, "IsPremiumCalcSuccess": "Y", "ProductCode": "TBTI",
    "QuotationNo": "QTBTI000001790127", "TotalInterest": 50.22,
    "TotalPremium": 1054.62, "Vat": 74.4, "VatRate": 0.08,
}

#: Resposta de `/proposal/core/proposal/v1/createEx` (Policy Persistence API).
AMOSTRA_CRIAR = {
    "ProductCode": "TBTI", "ProductVersion": "1.0",
    "PolicyId": "10019269467,D8EAF6E6320D6DECBEAA19535E8597D9",
    "PolicyElementId": 10019269467, "ProposalNo": "PABTBTI0001302269",
    "ProposalStatus": "1", "PolicyStatus": 1, "VersionSeq": 1,
}
#: E a de `updateEx` — mesmo objeto, versão nova.
AMOSTRA_SALVAR = dict(AMOSTRA_CRIAR, VersionSeq=2)


def test_a_comissao_do_exemplo_da_QUOTATION_tambem_bate(prov):
    """Terceira amostra, outra página, mesmo resultado: 930 × 30% = 279."""
    o = prov._oferta_de(AMOSTRA_QUOTATION, PRODUTO)
    assert o.premio_liquido_centavos == 93000 and o.iof_centavos == 7440
    assert o.premio_total_centavos == 100440       # DuePremium, não TotalPremium
    assert o.comissao_estimada() == 27900          # = o `Commission: 279` da doc


def test_o_juro_da_amostra_da_quotation_fica_de_fora(prov):
    """TotalPremium 1054.62 = 1004.40 + 50.22 de juro. Juro não é prêmio."""
    o = prov._oferta_de(AMOSTRA_QUOTATION, PRODUTO)
    assert o.premio_total_centavos != 105462


# ─────────────────────────────────────────────────────── o token do CAS

def test_o_token_sai_do_CAS_e_vai_no_cabecalho(prov, monkeypatch):
    """`POST /cas/ebao/v2/json/tickets` — usuário e senha, não client_id/secret."""
    monkeypatch.delenv("COTACAO_INSUREMO_API_KEY", raising=False)
    monkeypatch.setenv("COTACAO_INSUREMO_USUARIO", "corretora")
    monkeypatch.setenv("COTACAO_INSUREMO_SENHA", "segredo")
    chamadas = []
    monkeypatch.setattr(prov, "_pedir_ticket", lambda: (chamadas.append(1), ("tk-123", 3600))[1])
    assert prov._cabecalhos()["Authorization"] == "Bearer tk-123"
    prov._cabecalhos()
    assert len(chamadas) == 1, "o token tem que ficar guardado até expirar"


def test_a_chave_fixa_dispensa_o_CAS(prov, monkeypatch):
    """Quem já tem token não precisa mandar usuário e senha pela rede."""
    def _nunca():
        raise AssertionError("não devia pedir ticket com API_KEY configurada")
    monkeypatch.setattr(prov, "_pedir_ticket", _nunca)
    assert prov._cabecalhos()["Authorization"] == "Bearer chave-de-teste"


def test_o_nome_do_cabecalho_e_configuravel(prov, monkeypatch):
    """A doc diz que o token é "appended" e não dá o nome do header. Confirmar na
    coleção do Postman tem que ser trocar uma variável, não mexer em código."""
    monkeypatch.setenv("COTACAO_INSUREMO_HEADER_TOKEN", "X-Auth-Token")
    monkeypatch.setenv("COTACAO_INSUREMO_PREFIXO_TOKEN", "")
    h = prov._cabecalhos()
    assert h["X-Auth-Token"] == "chave-de-teste" and "Authorization" not in h


def test_token_expirado_e_pedido_de_novo(prov, monkeypatch):
    monkeypatch.delenv("COTACAO_INSUREMO_API_KEY", raising=False)
    monkeypatch.setenv("COTACAO_INSUREMO_USUARIO", "u")
    monkeypatch.setenv("COTACAO_INSUREMO_SENHA", "s")
    n = []
    monkeypatch.setattr(prov, "_pedir_ticket", lambda: (n.append(1), (f"tk{len(n)}", 30))[1])
    assert prov._cabecalhos()["Authorization"] == "Bearer tk1"
    assert prov._cabecalhos()["Authorization"] == "Bearer tk2"   # vida < margem de 60s


# ──────────────────────────────────────────────── a emissão até a proposta

def _gravador(monkeypatch, prov, respostas=None):
    """Troca o `_post` por um que anota a ordem das chamadas."""
    passos = []
    respostas = respostas or {}

    def _post(caminho, payload):
        passos.append(caminho)
        if caminho in respostas:
            r = respostas[caminho]
            if isinstance(r, Exception):
                raise r
            return r
        return {}
    monkeypatch.setattr(prov, "_post", _post)
    return passos


def test_emitir_faz_validate_create_save_nessa_ordem(prov, monkeypatch):
    from finance.cotacao_insuremo import (CAMINHO_CRIAR, CAMINHO_SALVAR,
                                          CAMINHO_VALIDAR)
    passos = _gravador(monkeypatch, prov, {CAMINHO_CRIAR: AMOSTRA_CRIAR,
                                           CAMINHO_SALVAR: AMOSTRA_SALVAR})
    r = prov.emitir(RISCO, {"seguradora": "Seguradora X", "produto": "AUTO_BR"})
    assert passos == [CAMINHO_VALIDAR, CAMINHO_CRIAR, CAMINHO_SALVAR]
    assert r["numero_proposta"] == "PABTBTI0001302269"
    assert r["policy_id"].startswith("10019269467")


def test_a_apolice_NAO_e_emitida(prov, monkeypatch):
    """Decisão do dono: "só proposta". `issuePolicyEx` existe e fica de fora —
    emitir apólice em nome da corretora é outra conversa."""
    from finance.cotacao_insuremo import (CAMINHO_CRIAR, CAMINHO_EMITIR_APOLICE,
                                          CAMINHO_SALVAR)
    passos = _gravador(monkeypatch, prov, {CAMINHO_CRIAR: AMOSTRA_CRIAR,
                                           CAMINHO_SALVAR: AMOSTRA_SALVAR})
    prov.emitir(RISCO, {"seguradora": "Seguradora X", "produto": "AUTO_BR"})
    assert CAMINHO_EMITIR_APOLICE not in passos


def test_validacao_reprovada_para_antes_de_criar_lixo(prov, monkeypatch):
    """A doc: 204 quando passa, 422 com as mensagens quando não. O 422 vira erro
    com o texto dentro — e é esse texto que o corretor lê na tela."""
    from finance.cotacao_insuremo import CAMINHO_CRIAR, CAMINHO_VALIDAR
    erro = cp.ProvedorErro('InsureMO 422 em /validate: {"messages":[{"message":'
                           '"field \\"EffectiveDate\\" is mandatory"}]}')
    passos = _gravador(monkeypatch, prov, {CAMINHO_VALIDAR: erro})
    with pytest.raises(cp.ProvedorErro, match="EffectiveDate"):
        prov.emitir(RISCO, {"seguradora": "Seguradora X", "produto": "AUTO_BR"})
    assert CAMINHO_CRIAR not in passos, "não pode criar proposta que a validação recusou"


def test_proposta_sem_numero_e_erro_e_nao_sucesso_mudo(prov, monkeypatch):
    """Sem `ProposalNo` ninguém acha essa proposta depois — nem a corretora, nem a
    seguradora. Melhor cair no roteiro do portal do que fingir que deu certo."""
    from finance.cotacao_insuremo import CAMINHO_CRIAR, CAMINHO_SALVAR
    _gravador(monkeypatch, prov, {CAMINHO_CRIAR: {"PolicyId": "1,X"},
                                  CAMINHO_SALVAR: {"PolicyId": "1,X"}})
    with pytest.raises(cp.ProvedorErro, match="ProposalNo"):
        prov.emitir(RISCO, {"seguradora": "Seguradora X", "produto": "AUTO_BR"})


def test_a_oferta_escolhida_decide_o_produto(prov, monkeypatch):
    """Errar o produto mandaria a proposta pro plano errado da seguradora certa."""
    import json as _json
    produtos = [dict(PRODUTO, codigo="P1", seguradora="Alfa"),
                dict(PRODUTO, codigo="P2", seguradora="Beta")]
    monkeypatch.setenv("COTACAO_INSUREMO_PRODUTOS", _json.dumps(produtos))
    from finance.cotacao_insuremo import CAMINHO_CRIAR
    vistos = []

    def _post(caminho, payload):
        vistos.append((caminho, payload.get("ProductCode")))
        return AMOSTRA_CRIAR if caminho == CAMINHO_CRIAR else AMOSTRA_SALVAR
    monkeypatch.setattr(prov, "_post", _post)
    prov.emitir(RISCO, {"seguradora": "Beta", "produto": "P2"})
    assert vistos[0][1] == "P2"


def test_oferta_de_produto_que_nao_existe_e_recusada(prov):
    with pytest.raises(cp.ProvedorErro, match="não casa"):
        prov.emitir(RISCO, {"seguradora": "Outra", "produto": "XPTO"})


def test_o_conector_declara_que_emite(prov):
    assert prov.suporta_emissao is True
