"""O nicho de LOJA DE SUPLEMENTO COM COZINHA (conta 16, SUPER FIT).

O que se prende aqui é a diferença que fez este nicho existir em vez de deixar a
SuperFit em 'alimentacao': **ela TRANSFORMA matéria-prima**. Uma lanchonete monta
prato com o que compra pronto; esta loja compra frango e batata-doce, que não vão
pra prateleira — viram marmita. Todo o resto (vocabulário, categorias de despesa,
persona) é consequência disso.

Os dois testes que mais importam:

`test_a_persona_separa_insumo_de_mercadoria` — se alguém simplificar a persona
juntando insumo a "compra pra revenda", o frango vira produto do catálogo e o
custo da cozinha volta a se misturar com a mercadoria da prateleira. O sintoma é
mudo: o CMV continua saindo, só que errado.

`test_a_persona_nao_da_conselho_de_saude` — suplemento não é medicamento. Um
agente que responde "o que tomar pra emagrecer" põe a loja num lugar onde ela não
pode estar, e isso não é detalhe de tom: é o limite do que o sistema pode dizer.
"""
import pytest

from finance import cnpj_info, nichos, novidades as nv, raio_x_perfil as rxp
from finance.models import CATEGORIAS_DESPESA, canonizar_categoria
from finance.vendas import modo_por_nicho


# ── o vocabulário ─────────────────────────────────────────────────────────
def test_o_nicho_existe_e_e_misto():
    assert nichos.nicho_existe("suplementos")
    assert nichos.label_do_nicho("suplementos") == "Suplementos / Nutrição esportiva"
    # produto: ela revende pote. serviço: o PLANO de marmitas, que é recorrente.
    assert nichos.vende_produto("suplementos") is True
    assert nichos.vende_servico("suplementos") is True
    assert nichos.eh_misto("suplementos") is True


def test_a_unidade_padrao_e_pote():
    """Primeiro da lista é o que o formulário já vem preenchido — e pote é a
    unidade de quase tudo que ela revende (900g, 300g, 220g)."""
    assert nichos.unidade_padrao("suplementos") == "pote"


@pytest.mark.parametrize("u", ["pote", "unidade", "caixa", "frasco", "sache",
                               "kit", "marmita", "kg"])
def test_as_unidades_da_loja_valem(u):
    assert nichos.unidade_valida_para("suplementos", u)


def test_o_vocabulario_de_lanchonete_nao_vale_aqui():
    """O que a SuperFit via até hoje, no nicho 'alimentacao': prato, porção,
    combo. Whey não se vende por porção, nem se serve em prato."""
    for u in ("porcao", "prato"):
        assert nichos.unidade_valida_para("alimentacao", u)
        assert not nichos.unidade_valida_para("suplementos", u)


def test_whey_e_a_categoria_padrao():
    """13 dos 27 produtos dela são whey — metade da loja, e primeiro da lista é
    o padrão do formulário."""
    cats = nichos.categorias_do_nicho("suplementos")
    assert cats[0] == "whey"
    assert {"creatina", "pre_treino", "aminoacido", "cafeina", "termogenico",
            "vitamina", "colageno", "barra_snack", "acessorio"} <= set(cats)


def test_a_cozinha_tem_categoria_e_o_insumo_tambem():
    """O que separa este nicho de uma loja que só revende: `marmita` é o que ela
    vende, `insumo` é o que ela compra pra fazer. Sem os dois, a cozinha não cabe
    no catálogo."""
    cats = nichos.categorias_do_nicho("suplementos")
    assert "marmita" in cats
    assert "insumo" in cats


def test_fruta_nao_e_categoria_daqui():
    """As 10 linhas em 'fruta' do catálogo dela vieram do vocabulário do
    hortifrúti, pelos SABORES (morango, pêssego e manga, creme de coco). É o erro
    que este nicho existe pra não repetir."""
    assert "fruta" not in nichos.categorias_do_nicho("suplementos")
    assert "fruta" in nichos.categorias_do_nicho("hortifruti")


def test_o_nicho_aparece_no_select_e_nas_duas_listas_por_tipo():
    assert "suplementos" in {n["slug"] for n in nichos.lista_nichos()}
    # é misto: aparece pra quem filtra produto E pra quem filtra serviço
    assert "suplementos" in {n["slug"] for n in nichos.lista_nichos_por_tipo("produto")}
    assert "suplementos" in {n["slug"] for n in nichos.lista_nichos_por_tipo("servico")}


def test_a_unidade_nova_tem_rotulo_legivel():
    assert nichos.label_unidade("sache") == "sachê"
    assert nichos.label_unidade("pote") == "pote"
    assert nichos.label_unidade("marmita") == "marmita"


# ── a persona ─────────────────────────────────────────────────────────────
def test_a_persona_separa_insumo_de_mercadoria():
    """O TESTE DESTE ARQUIVO. Ver o docstring do módulo."""
    p = nichos._PERSONAS_NICHO["suplementos"]
    assert "INSUMO NÃO É MERCADORIA" in p
    assert "'Insumos'" in p and "'Embalagens'" in p
    # e diz onde NÃO lançar: 'Mercado' é compra de casa
    assert "NUNCA lance isso em 'Mercado'" in p


def test_a_persona_nao_da_conselho_de_saude():
    """O OUTRO TESTE DESTE ARQUIVO. Ver o docstring do módulo."""
    p = nichos._PERSONAS_NICHO["suplementos"]
    assert "NADA DE CONSELHO DE SAÚDE" in p
    assert "não recomende" in p and "dose" in p
    assert "nutricionista ou médico" in p


def test_a_persona_sabe_que_a_marmita_e_fresca():
    """Confirmado pelo dono em 13/09/2026. Congelada acumularia lote por semanas;
    fresca tem estoque do DIA — e é isso que o agente precisa saber antes de
    falar de saldo de prato."""
    p = nichos._PERSONAS_NICHO["suplementos"]
    assert "FRESCA, FEITA NA HORA" in p
    assert "estoque de prato" in p and "do DIA" in p


def test_a_persona_ensina_que_o_item_e_marca_sabor_gramatura():
    p = nichos._PERSONAS_NICHO["suplementos"]
    assert "MARCA + SABOR + GRAMATURA" in p
    assert "confirme o SABOR" in p


def test_a_persona_nao_reusa_o_molde_de_servico():
    """O molde ensina que mensalidade de cliente é o forte do faturamento. Esta é
    uma LOJA DE BALCÃO: o grosso é venda avulsa, e o plano é o extra."""
    p = nichos._PERSONAS_NICHO["suplementos"]
    assert "O nome do cliente vai na CONTRAPARTE" not in p
    # a LINHA do molde, com o hífen — e não a palavra solta: a persona daqui diz
    # "MARGEM E GIRO, NÃO FATURAMENTO", que é o oposto do que o molde ensina
    assert "\n- FATURAMENTO:" not in p
    assert "\n- CARTEIRA:" not in p


def test_a_persona_de_suplementos_nao_vazou_pros_outros():
    """Se 'insumo' ou 'marmita' aparecerem no molde compartilhado, cinco ramos de
    honorário passam a ouvir conselho de cozinha."""
    for slug in ("advocacia", "consultoria", "agencia", "tecnologia",
                 "arquitetura", "contabilidade", "seguros"):
        p = nichos._PERSONAS_NICHO[slug]
        assert "INSUMO" not in p and "marmita" not in p.lower()


# ── o CNAE ────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("desc", [
    "Comércio varejista de suplementos alimentares",
    "Comercio varejista de suplementos nutricionais",
    "Comércio varejista de produtos naturais",
])
def test_cnae_descritivo_de_suplemento_vira_o_nicho(desc):
    assert cnpj_info.classificar_ramo(desc) == "Suplementos"
    assert cnpj_info.nicho_do_cnae(desc) == "suplementos"


def test_o_cnae_generico_da_superfit_continua_ambiguo():
    """HONESTIDADE DO ALCANCE, e é de propósito. O 4729-6/99 que a Receita deu
    pra SUPER FIT não tem a palavra suplemento: ele descreve comércio de
    alimentos em geral, e cai em mercearia. Nenhuma regra de texto resolve isso
    sem roubar as mercearias de verdade — quem decide é a escolha do nicho na
    tela Empresa. Este teste existe pra ninguém 'consertar' o mapa alargando a
    chave e quebrar o minimercado."""
    desc = ("Comércio varejista de produtos alimentícios em geral ou "
            "especializado em produtos alimentícios não especificados anteriormente")
    assert cnpj_info.nicho_do_cnae(desc) == "minimercado"


@pytest.mark.parametrize("desc,esperado", [
    ("Comércio varejista de mercadorias em geral - minimercados", "minimercado"),
    ("Padaria e confeitaria com predominância de revenda", "alimentacao"),
    ("Comércio varejista de produtos farmacêuticos", "farmacia"),
    ("Comércio varejista de hortifrutigranjeiros", "hortifruti"),
])
def test_os_ramos_vizinhos_de_alimento_continuam_onde_estavam(desc, esperado):
    """'Suplementos' entrou ANTES de Supermercado (cuja chave "alimentos" pegaria
    uma loja de whey). A ordem da lista é o que decide — este teste é a prova de
    que entrar no meio dela não roubou ninguém."""
    assert cnpj_info.nicho_do_cnae(desc) == esperado


# ── o dinheiro: as duas categorias novas ──────────────────────────────────
def test_insumos_e_embalagens_sao_categorias_de_despesa():
    assert "Insumos" in CATEGORIAS_DESPESA
    assert "Embalagens" in CATEGORIAS_DESPESA


@pytest.mark.parametrize("escrito", ["Insumos", "insumos", "INSUMOS"])
def test_insumo_escrito_de_qualquer_jeito_cai_na_mesma(escrito):
    assert canonizar_categoria(escrito, "despesa") == "Insumos"


@pytest.mark.parametrize("escrito", ["Embalagens", "embalagens", "EMBALAGENS"])
def test_embalagem_escrita_de_qualquer_jeito_cai_na_mesma(escrito):
    assert canonizar_categoria(escrito, "despesa") == "Embalagens"


def test_as_categorias_antigas_continuam_valendo():
    """A lista é a mesma pra toda conta do sistema: acrescentar duas não pode
    mexer em nenhuma das que já existiam, nem no 'Outros' de quem errar."""
    for c in ("Mercado", "Compras", "Servicos", "Impostos", "Outros"):
        assert c in CATEGORIAS_DESPESA
    assert canonizar_categoria("nao existe", "despesa") == "Outros"


def test_insumo_nao_virou_categoria_de_receita():
    assert canonizar_categoria("Insumos", "receita") == "Outros"


def test_o_agente_aprende_quando_e_insumo_e_quando_e_mercado():
    """A lista de categorias entra no prompt sozinha (é um join de
    CATEGORIAS_DESPESA), mas o GUIA de categorização é texto fixo — e sem uma
    linha nova o agente continuaria mandando a compra do atacado pra "Mercado",
    que é o erro inteiro que as categorias vieram corrigir. O guia precisa dizer
    as duas metades: o que é insumo, e que mercado de casa continua "Mercado"."""
    from finance.agente_financeiro import _persona
    p = _persona("dono")
    assert "Insumos" in p and "Embalagens" in p          # entram pelo join
    assert "PRA VIRAR PRODUTO" in p                       # o guia explica
    assert 'e\' Insumos, NAO "Mercado"' in p
    assert '"Mercado" continua sendo a compra' in p       # e a metade que fica


# ── o modo do orçamento e o perfil ────────────────────────────────────────
def test_loja_de_suplemento_nao_e_evento():
    """Plano de marmita renova; não é festa com sinal e data segurada."""
    assert modo_por_nicho("suplementos") == "recorrente"


def test_nao_emite_contrato_de_locacao():
    from finance.contrato import tem_contrato
    assert tem_contrato("suplementos") is False


def test_o_perfil_do_raio_x_e_recorrente_e_por_causa_do_plano():
    """CONSEQUÊNCIA DECLARADA de ser misto: por vender serviço, a conta sai do
    perfil 'produto' (sem funil) e entra em 'recorrente' — ganha funil, Raio-X e
    follow-up, que é o que serve pra vender plano em academia e empresa.

    Se um dia isto voltar pra 'produto', o dono perde a tela onde prospecta — e
    é melhor que a mudança apareça aqui do que no menu dele."""
    assert rxp.perfil_por_nicho("suplementos") == "recorrente"
    p = rxp.perfil("suplementos")
    assert p["aplica"] is True
    # e o vocabulário que vem junto é o de quem vende serviço, não o de festa
    assert p["vocab"]["compromisso"] == "reunião"
    assert p["vocab"]["data"] is False


# ── a mira do aviso ───────────────────────────────────────────────────────
def test_o_portao_suplementos_alcanca_so_a_loja():
    assert nv.alcanca("suplementos", "suplementos") is True
    assert nv.nichos_alcancados("suplementos") == {"suplementos"}


@pytest.mark.parametrize("outro", ["hortifruti", "minimercado", "alimentacao",
                                   "eventos", "consultoria", "seguros", None])
def test_o_portao_suplementos_nao_alcanca_mais_ninguem(outro):
    """Whey, marmita e ficha técnica não existem em nenhum outro nicho da base."""
    assert nv.alcanca("suplementos", outro) is False


def test_a_loja_tambem_e_alcancada_pelos_portoes_de_familia():
    """Ela vende produto E serviço — então continua recebendo os avisos gerais
    dos dois grupos. O portão novo estreita, não isola."""
    assert nv.alcanca("produto", "suplementos") is True
    assert nv.alcanca("servico", "suplementos") is True
    assert nv.alcanca("recorrente", "suplementos") is True
    assert nv.alcanca("eventos", "suplementos") is False
    assert nv.alcanca("seguros", "suplementos") is False
