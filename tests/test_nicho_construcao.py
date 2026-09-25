"""O nicho de CONSTRUÇÃO E REFORMA (conta 33, PX2 Empreendimentos).

A PX2 constrói casa popular pra vender pelo Minha Casa Minha Vida e faz reforma
(respostas do dono em 25/09/2026). O desenho aprovado está em
docs/mockups/nicho_construcao.html.

Os testes que mais importam:

`test_a_persona_pergunta_de_qual_obra` — o custo por casa nasce dessa pergunta.
Sem ela, a nota de material cai sem obra, como as 13 despesas de obra da PX2
até 25/09.

`test_a_persona_nao_promete_credito` — quem aprova é a Caixa, e "taxa de
liberação" é o golpe clássico do crédito habitacional.

`test_engenharia_com_secundario_de_obra_e_construcao` — o CNAE principal da PX2
é 71.12-0-00, que pela descrição vira arquitetura.
"""
import io
import json
from pathlib import Path

import pytest

from finance import cnpj_info, nichos, novidades as nv, raio_x_perfil as rxp
from finance.models import CATEGORIAS_DESPESA, CATEGORIAS_RECEITA
from finance.vendas import modo_por_nicho

_MIG = Path(__file__).resolve().parent.parent / "db" / "migracoes"


# ── o nome ────────────────────────────────────────────────────────────────
def test_o_ramo_se_chama_construcao_e_reforma():
    assert nichos.label_do_nicho("construcao") == "Construção e reforma"


def test_o_nome_da_tabela_e_o_label_do_codigo_sao_o_mesmo():
    """O painel lê o label do CÓDIGO; o admin lê o nome da TABELA."""
    sql = (_MIG / "348_nicho_construcao_e_reforma.sql").read_text(encoding="utf-8")
    assert f"update nichos set nome = '{nichos.label_do_nicho('construcao')}'" in sql
    assert f"select '{nichos.label_do_nicho('construcao')}', 'construcao'" in sql


def test_o_slug_nao_mudou_e_segue_servico_puro():
    """contas.nicho_id aponta pro id, e o código inteiro casa pelo slug."""
    assert nichos.nicho_existe("construcao")
    assert nichos.vende_servico("construcao") is True
    assert nichos.vende_produto("construcao") is False


# ── o vocabulário ─────────────────────────────────────────────────────────
def test_reforma_se_orca_por_metro_quadrado():
    assert nichos.unidade_padrao("construcao") == "m2"
    assert nichos.label_unidade("m2") == "m²"


@pytest.mark.parametrize("u", ["m2", "empreitada", "diaria", "etapa", "unidade",
                               "obra", "medicao"])
def test_as_unidades_de_obra_valem(u):
    assert nichos.unidade_valida_para("construcao", u)


def test_as_unidades_novas_tem_rotulo():
    assert nichos.label_unidade("empreitada") == "empreitada"
    assert nichos.label_unidade("obra") == "obra"


def test_as_categorias_sao_do_que_ela_vende_nao_do_que_ela_gasta():
    cats = nichos.categorias_do_nicho("construcao")
    assert cats[0] == "reforma"
    assert {"pintura", "eletrica", "hidraulica", "piso_revestimento"} <= set(cats)
    # custo da obra mora na despesa e no plano de contas, não no catálogo
    assert not {"material", "mao_de_obra", "subempreiteiro"} & set(cats)


def test_quem_deve_a_construtora_nao_deve_medicao():
    """Quem deve é o comprador da casa (a entrada) ou o cliente da reforma."""
    assert nichos.rotulo_receber("construcao") == "A receber"


# ── a persona ─────────────────────────────────────────────────────────────
def _persona() -> str:
    return nichos.persona_do_nicho("construcao")


def test_a_persona_saiu_do_molde_de_escritorio():
    p = _persona()
    assert "CONSTRUÇÃO E REFORMA" in p
    assert "'Honorarios'" not in p and "mensalidade" not in p.lower()


def test_a_persona_pergunta_de_qual_obra():
    p = _persona()
    assert "DE QUAL OBRA" in p
    assert "dividir entre as obras" in p
    assert "centro_custo" in p
    assert "Pergunte uma vez" in p


def test_a_persona_separa_material_de_mao_de_obra():
    p = _persona()
    assert "MATERIAL NÃO É MÃO DE OBRA" in p
    assert "'Insumos', conta contábil 3.1.03" in p
    assert "'Servicos', conta 3.1.04" in p
    assert "Nunca 'Compras' nem 'Construcao'" in p


def test_a_persona_sabe_quando_a_casa_vira_dinheiro():
    p = _persona()
    assert "registrado no cartório" in p
    assert "habite-se" in p and "averbação" in p and "SERO" in p


def test_a_persona_nao_promete_credito():
    p = _persona()
    assert "NÃO PROMETA CRÉDITO" in p
    assert "taxa de liberação" in p
    assert "senha do gov.br" in p


def test_a_persona_manda_imposto_pro_contador():
    assert "com o contador" in _persona()


def test_as_categorias_que_a_persona_cita_existem():
    assert "Vendas" in CATEGORIAS_RECEITA
    for c in ("Insumos", "Servicos", "Impostos", "Compras", "Construcao"):
        assert c in CATEGORIAS_DESPESA


def test_as_contas_do_plano_que_a_persona_cita_existem():
    """3.1.03 e 1.1.02 são da 132; 3.1.04 e 1.1.04 nascem na 349."""
    p = _persona()
    novas = (_MIG / "349_plano_obras.sql").read_text(encoding="utf-8")
    for cod in ("3.1.04", "1.1.04"):
        assert cod in p and f"('{cod}'," in novas
    base = (_MIG / "132_plano_contas_centros_custo.sql").read_text(encoding="utf-8")
    for cod in ("3.1.03", "1.1.02"):
        assert cod in p and cod in base


def test_a_persona_da_obra_nao_vazou_pros_outros():
    for slug, p in nichos._PERSONAS_NICHO.items():
        if slug != "construcao":
            assert "DE QUAL OBRA" not in p and "Minha Casa Minha Vida" not in p, slug


# ── o CNAE ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("codigo", [4120400, "4120-4/00", "41.20-4-00", 4211101,
                                    4321500, 4322301, 4330404, 4399103])
def test_secao_f_e_construcao_pelo_codigo(codigo):
    assert cnpj_info.nicho_do_cnae("qualquer descrição", codigo) == "construcao"


def test_pintura_deixa_de_se_perder():
    desc = "Serviços de pintura de edifícios em geral"
    assert cnpj_info.nicho_do_cnae(desc) is None               # só a descrição
    assert cnpj_info.nicho_do_cnae(desc, 4330404) == "construcao"


def test_eletrica_de_obra_sai_de_servicos_gerais():
    desc = "Instalação e manutenção elétrica"
    assert cnpj_info.nicho_do_cnae(desc) == "servicos_gerais"  # só a descrição
    assert cnpj_info.nicho_do_cnae(desc, 4321500) == "construcao"


def test_engenharia_com_secundario_de_obra_e_construcao():
    """O caso da PX2 (conta 33): principal 71.12-0-00, secundários de obra."""
    assert cnpj_info.nicho_do_cnae(
        "Serviços de engenharia", 7112000, [7711000, 4120400, 4330404]) == "construcao"


def test_engenharia_sem_secundario_de_obra_continua_arquitetura():
    desc = "Serviços de engenharia"
    assert cnpj_info.nicho_do_cnae(desc, 7112000, [7119701, 7120100]) == "arquitetura"
    assert cnpj_info.nicho_do_cnae(desc, 7112000) == "arquitetura"
    assert cnpj_info.nicho_do_cnae(desc) == "arquitetura"


def test_so_engenharia_puxa_pelo_secundario():
    """Escritório de arquitetura com obra no secundário continua projeto."""
    assert cnpj_info.nicho_do_cnae(
        "Serviços de arquitetura", 7111100, [4120400]) == "arquitetura"


@pytest.mark.parametrize("desc,codigo,esperado", [
    ("Atividades de contabilidade", 6920601, "contabilidade"),
    ("Comércio varejista de produtos farmacêuticos, sem manipulação de fórmulas",
     4771701, "farmacia"),
    # varejo de material é divisão 47: o código não mexe, e fica o que a
    # descrição já dava antes deste PR
    ("Comércio varejista de materiais de construção em geral", 4744099, "construcao"),
])
def test_codigo_fora_da_secao_f_segue_pela_descricao(desc, codigo, esperado):
    assert cnpj_info.nicho_do_cnae(desc, codigo) == cnpj_info.nicho_do_cnae(desc) == esperado


def test_o_codigo_tem_sete_digitos_mesmo_sem_o_zero_da_frente():
    """A BrasilAPI manda inteiro: 0111-3/01 chega como 111301 (divisão 01)."""
    assert cnpj_info._cnae7(111301) == "0111301"
    assert cnpj_info._cnae7("41.20-4-00") == "4120400"
    assert cnpj_info._cnae7(None) == ""
    assert cnpj_info.nicho_do_cnae(None, 111301) is None


class _Resposta(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _brasilapi(monkeypatch, dados: dict):
    monkeypatch.setattr(cnpj_info.urllib.request, "urlopen",
                        lambda req, timeout=None: _Resposta(json.dumps(dados).encode()))


def test_consultar_cnpj_le_o_codigo_e_os_secundarios(monkeypatch):
    _brasilapi(monkeypatch, {
        "razao_social": "CONSTRUTORA EXEMPLO LTDA", "nome_fantasia": "",
        "cnae_fiscal": 7112000, "cnae_fiscal_descricao": "Serviços de engenharia",
        "cnaes_secundarios": [
            {"codigo": 7711000, "descricao": "Locação de automóveis sem condutor"},
            {"codigo": 4120400, "descricao": "Construção de edifícios"}],
        "municipio": "LAGO DA PEDRA", "uf": "MA"})
    info = cnpj_info.consultar_cnpj("11.222.333/0001-81")
    assert info["nicho"] == "construcao"
    assert info["ramo"] == "Construcao"
    assert info["cnae"] == "Serviços de engenharia"


def test_consultar_cnpj_de_engenharia_sem_obra_segue_arquitetura(monkeypatch):
    _brasilapi(monkeypatch, {
        "razao_social": "PROJETOS EXEMPLO LTDA", "cnae_fiscal": 7112000,
        "cnae_fiscal_descricao": "Serviços de engenharia", "cnaes_secundarios": []})
    info = cnpj_info.consultar_cnpj("11222333000181")
    assert info["nicho"] == "arquitetura" and info["ramo"] == "Arquitetura"


def test_consultar_cnpj_sem_secundarios_na_resposta_nao_quebra(monkeypatch):
    _brasilapi(monkeypatch, {"razao_social": "X", "cnae_fiscal_descricao":
                             "Atividades de contabilidade", "cnaes_secundarios": None})
    assert cnpj_info.consultar_cnpj("11222333000181")["nicho"] == "contabilidade"


# ── o modo e o perfil ─────────────────────────────────────────────────────
def test_obra_nao_e_evento():
    assert modo_por_nicho("construcao") == "recorrente"


def test_o_perfil_e_o_de_obras():
    """Até 25/09 caía em recorrente. Os detalhes estão em tests/test_raio_x_obras.py."""
    assert rxp.perfil_por_nicho("construcao") == "obras"


# ── a mira do aviso ───────────────────────────────────────────────────────
def test_o_portao_construcao_alcanca_so_a_obra():
    assert nv.alcanca("construcao", "construcao") is True
    assert nv.nichos_alcancados("construcao") == {"construcao"}


@pytest.mark.parametrize("outro", ["arquitetura", "servicos_gerais", "consultoria",
                                   "advocacia", "eventos", "seguros", "clinica", None])
def test_o_portao_construcao_nao_alcanca_mais_ninguem(outro):
    """Obra, casa de MCMV e crédito da Caixa não existem em nenhum outro ramo —
    nem na arquitetura, que é projeto, nem nos serviços gerais, que é reparo."""
    assert nv.alcanca("construcao", outro) is False


def test_a_obra_continua_nos_portoes_de_familia():
    """O portão novo estreita, não isola: ela segue recebendo o que é de serviço."""
    assert nv.alcanca("servico", "construcao") is True
    assert nv.alcanca("recorrente", "construcao") is True
    assert nv.alcanca("produto", "construcao") is False
    assert nv.alcanca("eventos", "construcao") is False


def test_o_aviso_mira_o_portao_novo_e_quem_configura_a_empresa():
    sql = (_MIG / "350_novidade_nicho_construcao.sql").read_text(encoding="utf-8")
    assert "('nicho-construcao-e-reforma', 'novidade', 'construcao', '{dono,gestor}'," in sql
