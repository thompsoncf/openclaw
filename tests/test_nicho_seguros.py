"""O nicho de CORRETORA DE SEGUROS (conta 37, Liberal Seguros).

O que se prende aqui é a diferença que fez este nicho existir em vez de encaixar
a corretora num dos 22 que já havia: **numa corretora quem paga é a seguradora,
não o segurado**. Todo o resto — vocabulário, CNAE, categoria de receita — é
consequência disso.

O teste que mais importa é `test_a_persona_nao_manda_o_cliente_pra_contraparte`.
Existe um `_molde_servico` que advocacia, agência, consultoria, arquitetura e
tecnologia reaproveitam, e ele ensina ao agente "o nome do cliente vai na
CONTRAPARTE". Está certo lá e errado aqui. O dia em que alguém "limpar" o código
juntando seguros ao molde, o sintoma vai ser mudo: os títulos a receber passam a
nascer com o segurado no lugar de quem deve, e o "quem me deve" da corretora
enche de gente que não deve nada a ela. Este teste é o que transforma isso em
falha vermelha.
"""
import pytest

from finance import cnpj_info, nichos, novidades as nv
from finance.models import (CATEGORIAS_RECEITA, canonizar_categoria,
                            receita_e_operacional)
from finance.vendas import modo_por_nicho


# ── o vocabulário ─────────────────────────────────────────────────────────
def test_o_nicho_existe_e_e_de_servico():
    assert nichos.nicho_existe("seguros")
    assert nichos.label_do_nicho("seguros") == "Corretora de Seguros"
    # corretora não tem estoque: ela intermedeia, não revende
    assert nichos.vende_servico("seguros") is True
    assert nichos.vende_produto("seguros") is False


def test_a_unidade_padrao_e_apolice():
    """Primeiro da lista é o que o formulário já vem preenchido."""
    assert nichos.unidade_padrao("seguros") == "apolice"


@pytest.mark.parametrize("u", ["apolice", "veiculo", "vida", "mensal", "avulso"])
def test_as_unidades_da_corretora_valem(u):
    assert nichos.unidade_valida_para("seguros", u)


def test_o_vocabulario_de_mercearia_nao_vale_aqui():
    """O que a Liberal veria hoje, sem este nicho: ela cai em 'generico', cujo
    vocabulário é kg/caixa/pacote. Apólice não se vende por quilo."""
    for u in ("kg", "caixa", "pacote"):
        assert not nichos.unidade_valida_para("seguros", u)


def test_auto_e_a_categoria_padrao():
    """Auto é o carro-chefe da corretora, e primeiro da lista é o padrão."""
    cats = nichos.categorias_do_nicho("seguros")
    assert cats[0] == "auto"
    assert {"frota", "vida", "residencial", "empresarial", "condominio",
            "saude", "rc", "garantia", "viagem"} <= set(cats)


def test_o_nicho_aparece_no_select():
    slugs = {n["slug"] for n in nichos.lista_nichos()}
    assert "seguros" in slugs


# ── a persona ─────────────────────────────────────────────────────────────
def test_a_persona_nao_manda_o_cliente_pra_contraparte():
    """O TESTE DESTE ARQUIVO. Ver o docstring do módulo: reusar o molde de
    serviço aqui faz o título a receber nascer com o nome errado no lugar de
    quem deve."""
    p = nichos._PERSONAS_NICHO["seguros"]
    assert "O nome do cliente vai na CONTRAPARTE" not in p
    assert "SEGURADORA" in p
    # e diz explicitamente quem é o devedor
    assert "SEGURADORA na CONTRAPARTE" in p


def test_a_persona_ensina_o_que_so_existe_aqui():
    p = nichos._PERSONAS_NICHO["seguros"]
    assert "COMISSÃO" in p                    # a receita é % do prêmio
    assert "periodicidade='anual'" in p       # apólice é de 12 meses
    assert "'Comissoes'" in p                 # a categoria certa
    # prêmio de cliente não é despesa dela — o erro caro do outro lado
    assert "NUNCA É DESPESA DELA" in p


def test_a_persona_nao_vazou_pros_outros_nichos():
    """Seguro é palavra que cabe em qualquer texto; se ela aparecer no molde
    compartilhado, quatro ramos passam a ouvir conselho de corretora."""
    for slug in ("advocacia", "consultoria", "agencia", "tecnologia",
                 "arquitetura", "contabilidade"):
        assert "SEGURADORA" not in nichos._PERSONAS_NICHO[slug]


# ── o CNAE ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("desc", [
    # o CNAE real da corretora: 6622-3/00
    "Corretores e agentes de seguros, de planos de previdência complementar e de saúde",
    "Corretagem de seguros",
    "Corretora de seguros",
    "Agenciamento de seguros",
])
def test_cnae_de_corretora_vira_nicho_seguros(desc):
    assert cnpj_info.classificar_ramo(desc) == "Seguros"
    assert cnpj_info.nicho_do_cnae(desc) == "seguros"


def test_corretor_de_imoveis_nao_e_corretora_de_seguros():
    """Por isso nenhuma chave é a palavra "corretor" sozinha."""
    assert cnpj_info.nicho_do_cnae(
        "Corretagem na compra e venda e avaliação de imóveis") != "seguros"


@pytest.mark.parametrize("desc,esperado", [
    ("Agências de publicidade", "agencia"),
    ("Atividades de consultoria em gestão empresarial", "consultoria"),
    ("Atividades de contabilidade", "contabilidade"),
    ("Atividades jurídicas, exceto cartórios", "advocacia"),
])
def test_os_ramos_vizinhos_continuam_onde_estavam(desc, esperado):
    """'Seguros' entrou ANTES de Agencia (por "agenciamento") e de Consultoria
    (que é genérica). A ordem da lista é o que decide — este teste é a prova de
    que entrar no meio dela não roubou ninguém."""
    assert cnpj_info.nicho_do_cnae(desc) == esperado


# ── o dinheiro ────────────────────────────────────────────────────────────
def test_comissoes_e_categoria_de_receita_e_e_faturamento():
    assert "Comissoes" in CATEGORIAS_RECEITA
    # é receita OPERACIONAL: comissão é o faturamento da corretora, não aporte
    assert receita_e_operacional("Comissoes") is True


@pytest.mark.parametrize("escrito", ["Comissoes", "comissoes", "Comissões",
                                     "COMISSÕES", "comissões"])
def test_comissao_escrita_de_qualquer_jeito_cai_na_mesma(escrito):
    assert canonizar_categoria(escrito, "receita") == "Comissoes"


# ── o modo do orçamento ───────────────────────────────────────────────────
def test_corretora_nao_e_evento():
    """Apólice renova, não é festa com sinal e data segurada. Ficar fora de
    NICHOS_EVENTO já resolve — o teste é pra ninguém "consertar" isso depois."""
    assert modo_por_nicho("seguros") == "recorrente"


def test_corretora_nao_emite_contrato_de_locacao():
    from finance.contrato import tem_contrato
    assert tem_contrato("seguros") is False


# ── a mira do aviso ───────────────────────────────────────────────────────
def test_o_portao_seguros_alcanca_so_a_corretora():
    assert nv.alcanca("seguros", "seguros") is True
    assert nv.nichos_alcancados("seguros") == {"seguros"}


@pytest.mark.parametrize("outro", ["eventos", "consultoria", "advocacia",
                                   "hortifruti", None])
def test_o_portao_seguros_nao_alcanca_mais_ninguem(outro):
    """Um aviso sobre apólice e seguradora numa conta que não tem nem uma nem
    outra é prometer tela que não abre."""
    assert nv.alcanca("seguros", outro) is False


def test_a_corretora_tambem_e_alcancada_pelos_portoes_de_familia():
    """Ela vende serviço e fatura recorrente — então continua recebendo os
    avisos gerais desses dois grupos. O portão novo estreita, não isola."""
    assert nv.alcanca("servico", "seguros") is True
    assert nv.alcanca("recorrente", "seguros") is True
    assert nv.alcanca("produto", "seguros") is False
    assert nv.alcanca("eventos", "seguros") is False
