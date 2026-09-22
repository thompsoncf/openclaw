"""O nicho de CLÍNICA (conta 39, Espaço Pelle Clínica Dermatológica).

O que se prende aqui é o que fez o nicho nascer MISTO: a clínica atende E vende
produto de beleza, junto com o atendimento ou separado. Se alguém "simplificar"
pra serviço puro, o dermocosmético perde o estoque; se virar produto puro, a
consulta perde a aba de serviço e o funil.

Os dois testes que mais importam:

`test_a_persona_nao_da_conselho_medico` — o agente é do caixa, não do
consultório. Indicar tratamento pra problema de pele não é detalhe de tom.

`test_a_persona_nao_poe_dado_de_saude_no_lancamento` — diagnóstico é dado
sensível na LGPD, e a descrição de um título a receber não é prontuário.
"""
from pathlib import Path

import pytest

from finance import cnpj_info, nichos, raio_x_perfil as rxp
from finance.models import CATEGORIAS_DESPESA, CATEGORIAS_RECEITA
from finance.vendas import modo_por_nicho


# ── o vocabulário ─────────────────────────────────────────────────────────
def test_o_nicho_existe_e_e_misto():
    assert nichos.nicho_existe("clinica")
    assert nichos.vende_servico("clinica") is True
    # vende produto de beleza junto com o atendimento ou separado (dono, 22/09)
    assert nichos.vende_produto("clinica") is True
    assert nichos.eh_misto("clinica") is True


def test_o_nome_da_tabela_e_o_label_do_codigo_sao_o_mesmo():
    """O painel lê o label do CÓDIGO; o admin lê o nome da TABELA."""
    sql = (Path(__file__).resolve().parent.parent / "db" / "migracoes"
           / "308_nicho_clinica.sql").read_text(encoding="utf-8")
    assert f"select '{nichos.label_do_nicho('clinica')}', 'clinica'" in sql


def test_a_migracao_nao_atropela_a_escolha_da_conta():
    sql = (Path(__file__).resolve().parent.parent / "db" / "migracoes"
           / "308_nicho_clinica.sql").read_text(encoding="utf-8")
    assert "where id = 39\n   and nicho_id is null;" in sql


def test_a_consulta_e_o_padrao():
    assert nichos.unidade_padrao("clinica") == "consulta"
    assert nichos.categorias_do_nicho("clinica")[0] == "consulta"


@pytest.mark.parametrize("u", ["consulta", "sessao", "procedimento", "pacote",
                               "unidade", "frasco", "kit"])
def test_servico_e_produto_tem_unidade(u):
    assert nichos.unidade_valida_para("clinica", u)


def test_o_produto_de_beleza_tem_categoria():
    cats = nichos.categorias_do_nicho("clinica")
    assert {"dermocosmetico", "cosmetico"} <= set(cats)
    assert {"procedimento", "estetica"} <= set(cats)


def test_a_unidade_nova_tem_rotulo_legivel():
    assert nichos.label_unidade("consulta") == "consulta"
    assert nichos.label_unidade("procedimento") == "procedimento"


def test_o_nicho_aparece_nas_duas_listas_por_tipo():
    assert "clinica" in {n["slug"] for n in nichos.lista_nichos_por_tipo("produto")}
    assert "clinica" in {n["slug"] for n in nichos.lista_nichos_por_tipo("servico")}


def test_paciente_nao_deve_fiado():
    """Vende produto, então o padrão seria 'Fiado' — errado pra clínica."""
    assert nichos.rotulo_receber("clinica") == "A receber"


# ── a persona ─────────────────────────────────────────────────────────────
def test_a_persona_nao_da_conselho_medico():
    p = nichos.persona_do_nicho("clinica")
    assert "NADA DE CONSELHO MÉDICO" in p
    assert "Não indique tratamento" in p


def test_a_persona_nao_poe_dado_de_saude_no_lancamento():
    p = nichos.persona_do_nicho("clinica")
    assert "DADO DE SAÚDE NÃO ENTRA NO LANÇAMENTO" in p
    assert "nunca diagnóstico" in p


def test_a_persona_separa_atendimento_de_produto():
    p = nichos.persona_do_nicho("clinica")
    assert "'Honorarios'" in p and "'Vendas'" in p
    assert "DOIS lançamentos" in p


def test_as_categorias_que_a_persona_cita_existem():
    for c in ("Honorarios", "Vendas"):
        assert c in CATEGORIAS_RECEITA
    for c in ("Insumos", "Compras", "Impostos", "Assinaturas"):
        assert c in CATEGORIAS_DESPESA


def test_a_persona_da_clinica_nao_vazou_pros_outros():
    for slug, p in nichos._PERSONAS_NICHO.items():
        if slug != "clinica":
            assert "CONSELHO MÉDICO" not in p and "paciente" not in p.lower()


# ── o CNAE ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("desc", [
    "Atividade médica ambulatorial restrita a consultas",
    "Atividade médica ambulatorial com recursos para realização de procedimentos cirúrgicos",
    "Atividade odontológica",
    "Atividades de fisioterapia",
    "Atividades de psicologia e psicanálise",
])
def test_cnae_de_saude_vira_clinica(desc):
    assert cnpj_info.nicho_do_cnae(desc) == "clinica"


@pytest.mark.parametrize("desc,esperado", [
    ("Atividades de estética e outros serviços de cuidados com a beleza", "salao"),
    ("Cabeleireiros, manicure e pedicure", "salao"),
    ("Comércio varejista de cosméticos, produtos de perfumaria e de higiene pessoal", "beleza"),
    ("Comércio varejista de produtos farmacêuticos", "farmacia"),
])
def test_os_vizinhos_de_beleza_e_saude_continuam_onde_estavam(desc, esperado):
    """'Clinica' entrou ANTES de Salao na lista — a prova de que não roubou
    o salão de estética nem a loja de cosmético."""
    assert cnpj_info.nicho_do_cnae(desc) == esperado


# ── o modo e o perfil ─────────────────────────────────────────────────────
def test_clinica_nao_e_evento():
    assert modo_por_nicho("clinica") == "recorrente"


def test_o_perfil_do_raio_x_e_recorrente():
    assert rxp.perfil_por_nicho("clinica") == "recorrente"
