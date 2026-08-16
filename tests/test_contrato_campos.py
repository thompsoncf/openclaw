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


# ------------------------------------------------------- a data como o Brasil lê

def test_a_data_do_evento_sai_em_dd_mm_aaaa():
    """O orçamento GRAVA em ISO — conferido nos dois orçamentos de evento em
    produção (16/08/2026): `{"data": "2026-10-10"}`. Sem formatar, a cláusula sai
    'Evento em 2026-10-10' e o quadro do objeto também: data ao contrário num
    documento que se assina e se arquiva."""
    txt, faltas = ct.preencher(
        "Evento em {evento.data}.",
        _ctx(orcamento={**ORCAMENTO, "evento": {"data": "2026-10-10"}}))
    assert txt == "Evento em 10/10/2026."
    assert faltas == []


def test_data_que_ja_veio_brasileira_nao_e_mexida():
    assert ct.data_br("31/12/2026") == "31/12/2026"


@pytest.mark.parametrize("texto", ["a combinar", "sábado que vem", ""])
def test_data_que_nao_e_data_volta_como_veio(texto):
    """Campo de texto livre: o dono pode ter escrito 'a combinar'. Trocar por
    vazio apagaria informação que ele quis dar — e um contrato com o objeto em
    branco é pior que um com o objeto impreciso."""
    assert ct.data_br(texto) == texto


def test_data_ausente_nao_vira_a_palavra_none():
    assert ct.data_br(None) == ""


# --------------------------------------------- a empresa como ela chega de verdade

# É ASSIM que `empresa.obter_dados_empresa` devolve: a chave é `documento`. O
# EMPRESA lá de cima usa `cnpj` porque é dicionário escrito à mão — e foi essa
# diferença que fez `{empresa.cnpj}` sair vazio SEMPRE em produção, sem erro
# nenhum, num campo que a paleta de campos oferecia ao dono.
EMPRESA_REAL = {"razao_social": "M S DE SOUSA JUNIOR FESTAS E EVENTOS LTDA",
                "documento": "52.752.898/0001-58", "nome_fantasia": "Prime Eventos",
                "endereco": "Av. Principal, 1000", "bairro": "Centro",
                "cidade": "Teresina", "uf": "pi", "telefone": "(86) 99999-0000",
                "email_empresa": "contato@prime.com.br"}


def test_o_cnpj_da_empresa_sai_da_chave_que_o_sistema_usa():
    txt, faltas = ct.preencher("{empresa.razao}, CNPJ {empresa.cnpj}.",
                               _ctx(empresa=EMPRESA_REAL))
    assert txt == ("M S DE SOUSA JUNIOR FESTAS E EVENTOS LTDA, "
                   "CNPJ 52.752.898/0001-58.")
    assert faltas == []


def test_o_dicionario_escrito_a_mao_com_cnpj_continua_valendo():
    """O fallback existe pra não quebrar quem monta o contexto na mão (os testes
    puros acima, e a pré-visualização do modelo)."""
    assert ct.contexto(catalogo=CATALOGO, orcamento=ORCAMENTO, modelo=None,
                       empresa={"cnpj": "11.111.111/0001-11"})["empresa"]["cnpj"] \
        == "11.111.111/0001-11"


def test_os_campos_da_empresa_que_o_contrato_de_locacao_precisa():
    """Contrato que qualifica as partes precisa de endereço completo e contato —
    sem eles a empresa completa na mão depois de imprimir."""
    e = _ctx(empresa=EMPRESA_REAL)["empresa"]
    assert e["endereco"] == "Av. Principal, 1000" and e["bairro"] == "Centro"
    assert e["cidade"] == "Teresina"
    assert e["uf"] == "PI"                       # UF sai maiúscula, veio "pi"
    assert e["telefone"] == "(86) 99999-0000"
    assert e["email"] == "contato@prime.com.br"  # a coluna é `email_empresa`


def test_os_campos_do_cliente_saem_do_orcamento():
    c = _ctx(orcamento={**ORCAMENTO, "endereco": "Rua A, 10", "cidade": "Teresina",
                        "uf": "pi", "cep": "64000-000", "email": "t@x.com",
                        "whatsapp": "86988887777"})["cliente"]
    assert c["endereco"] == "Rua A, 10" and c["cidade"] == "Teresina"
    assert c["uf"] == "PI" and c["cep"] == "64000-000" and c["email"] == "t@x.com"
    # sem telefone próprio, o WhatsApp serve — é o contato que o orçamento sempre tem
    assert c["telefone"] == "86988887777"


def test_campo_novo_da_empresa_esta_na_paleta_que_o_dono_ve():
    """Campo que existe no contexto e não na paleta é campo que ninguém usa."""
    campos = {c["campo"] for c in ct.campos_disponiveis(CATALOGO)}
    for novo in ("empresa.cidade", "empresa.uf", "empresa.telefone", "empresa.email",
                 "empresa.bairro", "cliente.endereco", "cliente.cidade", "cliente.uf",
                 "cliente.cep", "cliente.telefone", "cliente.email", "evento.local"):
        assert novo in campos


def test_a_paleta_nao_oferece_campo_que_o_contexto_nao_resolve():
    """A regra inversa, que é a que pega a falta silenciosa: se a paleta oferece
    {empresa.cnpj} e o contexto não sabe preencher, o dono escreve a cláusula e
    ela sai capenga sem ninguém avisar. Era exatamente o caso do CNPJ."""
    ctx = _ctx(empresa=EMPRESA_REAL)
    for c in ct.campos_disponiveis(CATALOGO):
        grupo, _, nome = c["campo"].partition(".")
        assert nome in ctx.get(grupo, {}), f"a paleta oferece {c['campo']}, o contexto não tem"


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
