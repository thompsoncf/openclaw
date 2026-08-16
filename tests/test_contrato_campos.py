"""O contrato que não tem números próprios.

Contrato e catálogo guardavam cópias dos mesmos valores, mantidas por mãos
diferentes — e elas divergiram. Medido no contrato vigente da Prime Eventos
contra o catálogo dela em 16/08/2026:

    hora extra       contrato R$ 600,00/h    catálogo R$ 620,00
    taxa de limpeza  contrato R$ 600,00      catálogo R$ 400,00

Não é teórico: em 15/08 o agente cotou "R$ 620 por hora" a um cliente que, se
fechasse, assinaria um contrato dizendo R$ 600.

A correção é estrutural — a cláusula cita o ITEM, não o número:

    "Taxa de Utilização Excedente de {preco.hora-extra} por hora"

Estes testes prendem as duas propriedades que fazem isso valer a pena:

1. **O preço vem do catálogo, sempre.** Mudar o catálogo muda o contrato, sem
   ninguém reescrever cláusula. É o teste que impede alguém de "otimizar"
   guardando o valor no texto.
2. **Campo que falta não some.** Num contrato, valor que evapora em silêncio é
   pior que valor errado: a cláusula continua gramaticalmente inteira, sem o
   número que lhe dava sentido. O campo fica visível e entra em `faltas`.

Tudo puro — nada aqui toca banco.
"""
import pytest

from finance import contrato as ct

CATALOGO = [
    {"slug": "hora-extra", "setup_centavos": 62000},
    {"slug": "taxa-de-limpeza", "setup_centavos": 40000},
    {"slug": "dj", "setup_centavos": 150000},
]
ORCAMENTO = {
    "cliente": "Thompson Ferreira", "cnpj": "000.000.000-00",
    "setup_centavos": 890000, "numero": 27,
    "evento": {"data": "31/12/2026", "inicio": "21:00", "convidados": 50, "tipo": "Casamento"},
}
EMPRESA = {"razao_social": "M S DE SOUSA JUNIOR FESTAS E EVENTOS LTDA",
           "cnpj": "52.752.898/0001-58"}


def _ctx(**kw):
    base = dict(catalogo=CATALOGO, orcamento=ORCAMENTO, modelo=None, empresa=EMPRESA)
    base.update(kw)
    return ct.contexto(**base)


# ------------------------------------------------- o preço vem do catálogo

def test_a_clausula_cita_o_item_e_recebe_o_preco_do_catalogo():
    txt, faltas = ct.preencher(
        "Taxa de Utilização Excedente de {preco.hora-extra} por hora.", _ctx())
    assert txt == "Taxa de Utilização Excedente de R$ 620,00 por hora."
    assert faltas == []


def test_corrigir_o_catalogo_corrige_o_contrato():
    """O teste que dá sentido a tudo: é a MESMA cláusula, e o número muda porque
    o catálogo mudou. Se alguém guardar o valor no texto, isto quebra."""
    clausula = "A limpeza poderá ser contratada por {preco.taxa-de-limpeza}."
    antes, _ = ct.preencher(clausula, _ctx())
    corrigido = [dict(s, setup_centavos=60000) if s["slug"] == "taxa-de-limpeza" else s
                 for s in CATALOGO]
    depois, _ = ct.preencher(clausula, _ctx(catalogo=corrigido))
    assert antes == "A limpeza poderá ser contratada por R$ 400,00."
    assert depois == "A limpeza poderá ser contratada por R$ 600,00."


def test_item_fora_do_catalogo_vira_falta_e_nao_some():
    txt, faltas = ct.preencher("Segurança: {preco.seguranca-particular}.", _ctx())
    assert "{preco.seguranca-particular}" in txt      # continua VISÍVEL no documento
    assert faltas == ["preco.seguranca-particular"]


# ------------------------------------------------------- dinheiro e percentual

def test_dinheiro_de_documento_tem_centavos():
    """Aqui é contrato, não conversa de WhatsApp: R$ 8.900,00, não R$ 8.900."""
    assert ct.reais(890000) == "R$ 8.900,00"
    assert ct.reais(62000) == "R$ 620,00"
    assert ct.reais(0) == "R$ 0,00"
    assert ct.reais(None) == "R$ 0,00"


def test_percentual_inteiro_sai_inteiro():
    assert ct.pct(30) == "30%"
    assert ct.pct(2.5) == "2,5%"
    assert ct.pct("abc") == "0%"


# ------------------------------------------------------------ o que o orçamento dá

def test_entrada_e_saldo_saem_do_sinal_da_casa():
    ctx = _ctx()
    assert ctx["valor"]["total"] == "R$ 8.900,00"
    assert ctx["valor"]["entrada"] == "R$ 2.670,00"      # 30%
    assert ctx["valor"]["saldo"] == "R$ 6.230,00"


def test_mudar_o_sinal_muda_entrada_e_saldo():
    ctx = _ctx(modelo={"regras": {"sinal_pct": 50}})
    assert ctx["valor"]["entrada"] == "R$ 4.450,00"
    assert ctx["valor"]["saldo"] == "R$ 4.450,00"


def test_o_evento_que_o_cliente_informou_entra_na_clausula():
    txt, faltas = ct.preencher(
        "evento do tipo {evento.tipo}, no dia {evento.data}, às {evento.inicio}, "
        "para {evento.convidados} convidados", _ctx())
    assert txt == ("evento do tipo Casamento, no dia 31/12/2026, às 21:00, "
                   "para 50 convidados")
    assert faltas == []


def test_orcamento_sem_evento_denuncia_cada_campo():
    """O agente é obrigado a levantar data, horário e convidados antes de orçar —
    mas um orçamento antigo pode não ter. O contrato não pode sair mudo sobre isso."""
    ctx = _ctx(orcamento={"cliente": "X", "setup_centavos": 100000})
    _txt, faltas = ct.preencher("{evento.data} {evento.convidados} {evento.inicio}", ctx)
    assert faltas == ["evento.data", "evento.convidados", "evento.inicio"]


# ------------------------------------------------------------ regras da casa

def test_conta_nova_usa_os_numeros_do_contrato_vigente():
    """Zero num contrato é pior que a falta: "multa de 0%" é uma cláusula que
    parece válida e não é. Quem nunca configurou herda os padrões."""
    ctx = _ctx()
    assert ctx["regra"]["sinal_pct"] == "30%"
    assert ctx["regra"]["multa_cancelamento"] == "30%"
    assert ctx["regra"]["duracao_horas"] == "5"
    assert ctx["regra"]["tolerancia_min"] == "30"


def test_a_conta_manda_no_que_configurou():
    ctx = _ctx(modelo={"regras": {"multa_cancelamento": 20, "duracao_horas": 6}})
    assert ctx["regra"]["multa_cancelamento"] == "20%"
    assert ctx["regra"]["duracao_horas"] == "6"
    assert ctx["regra"]["sinal_pct"] == "30%"          # o que não mexeu continua padrão


def test_regra_apagada_na_tela_nao_zera_o_contrato():
    """Campo esvaziado no formulário chega como string vazia; isso não pode virar
    "multa de %" no documento."""
    ctx = _ctx(modelo={"regras": {"multa_cancelamento": "", "sinal_pct": None}})
    assert ctx["regra"]["multa_cancelamento"] == "30%"
    assert ctx["regra"]["sinal_pct"] == "30%"


# ------------------------------------------------------------- o documento todo

def test_montar_devolve_clausula_por_clausula():
    doc, faltas = ct.montar(
        [{"titulo": "Cláusula 1", "corpo": "valor de {valor.total}"}], _ctx())
    assert doc == [{"titulo": "Cláusula 1", "corpo": "valor de R$ 8.900,00"}]
    assert faltas == []


def test_falta_repetida_aparece_uma_vez_so():
    doc, faltas = ct.montar([
        {"titulo": "A", "corpo": "{preco.inexistente} e {preco.inexistente}"},
        {"titulo": "B", "corpo": "{preco.inexistente}"},
    ], _ctx())
    assert faltas == ["preco.inexistente"]
    assert len(doc) == 2


def test_campo_no_titulo_tambem_e_preenchido():
    doc, _ = ct.montar([{"titulo": "Contrato de {cliente.nome}", "corpo": ""}], _ctx())
    assert doc[0]["titulo"] == "Contrato de Thompson Ferreira"


def test_texto_sem_campo_nenhum_passa_intacto():
    txt, faltas = ct.preencher("As partes elegem o foro da comarca.", _ctx())
    assert txt == "As partes elegem o foro da comarca."
    assert faltas == []


@pytest.mark.parametrize("entrada", [None, "", "{}", "{.}", "{preco}", "{PRECO.X}"])
def test_coisa_que_parece_campo_mas_nao_e(entrada):
    """Chave solta no texto (uma fórmula, um exemplo) não pode virar campo."""
    txt, faltas = ct.preencher(entrada, _ctx())
    assert faltas == []
    assert txt == (entrada or "")


# ------------------------------------------------------------ o modelo padrão

def test_o_modelo_padrao_nao_cita_campo_que_nao_existe():
    """A conta nova abre a tela com este texto. Se ele citar um campo inválido,
    todo cliente de eventos começa com um contrato furado — e o furo aparece na
    frente do cliente, não aqui."""
    _doc, faltas = ct.montar(ct.modelo_padrao(), _ctx())
    assert faltas == [], f"o modelo padrão cita campos que não resolvem: {faltas}"


def test_o_modelo_padrao_cobre_o_essencial():
    campos = ct.campos_usados(ct.modelo_padrao())
    for obrigatorio in ("cliente.nome", "evento.data", "valor.total",
                        "regra.sinal_pct", "regra.multa_cancelamento",
                        "preco.hora-extra", "preco.taxa-de-limpeza"):
        assert obrigatorio in campos


def test_campos_usados_nao_repete_e_mantem_a_ordem():
    assert ct.campos_usados([
        {"titulo": "{valor.total}", "corpo": "{cliente.nome} e {valor.total}"},
    ]) == ["valor.total", "cliente.nome"]


# ------------------------------------------------------------ só nicho evento

def test_contrato_de_locacao_e_do_nicho_de_eventos():
    assert ct.tem_contrato("eventos") is True


@pytest.mark.parametrize("nicho", ["tecnologia", "consorcio", "", None, "EVENTOS "])
def test_nicho_recorrente_nao_tem_contrato_de_locacao(nicho):
    """Uma conta recorrente teria um contrato de SERVIÇO, que é outro documento.
    A porta é a mesma do modo do orçamento (vendas.modo_por_nicho) justamente pra
    as duas nunca discordarem."""
    assert ct.tem_contrato(nicho) is False


def test_a_porta_e_a_mesma_do_modo_do_orcamento():
    """Se alguém mudar NICHOS_EVENTO, o contrato acompanha sozinho — que é o
    ponto de não ter escrito 'eventos' de novo aqui dentro."""
    from finance.vendas import modo_por_nicho
    for nicho in ("eventos", "tecnologia", "", None):
        assert ct.tem_contrato(nicho) is (modo_por_nicho(nicho) == "evento")
