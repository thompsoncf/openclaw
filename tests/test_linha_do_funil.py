"""A LINHA DO FUNIL: o que ela mostra, e — principalmente — o que ela CALA.

POR QUE ESTE ARQUIVO EXISTE. A linha tinha virado uma fileira de oito emojis sem
rótulo (✏️ 🔗 📄 ✉️ 📎 📜 ↗ 🗑), cinco deles "abrir ou mandar um documento" pra
DOIS documentos diferentes, e uma sequência de selos coloridos que pintava tudo
que tinha acontecido. Uma proposta perfeitamente em dia carregava cinco selos
verdes dizendo que estava tudo bem — e, no meio desse verde todo, o selo que
importava (a data que caiu, a parcela paga sem comprovante) tinha exatamente o
mesmo tamanho e o mesmo peso, e sumia.

A regra que enxuga sem esconder nada, e que este arquivo guarda:

    SELO É PENDÊNCIA.   O que já aconteceu desce pro resumo, em cinza.
    AÇÃO É UMA SÓ.      A primeira da ordem que o dono escolheu.
    SOME O BOTÃO,       nunca o aviso: as outras pendências continuam nos selos.

`linha_do_funil` é pura de propósito — a redação que o dono lê está testada aqui,
sem banco e sem tela.
"""
from finance import vendas as v


def tons(r):
    return [s["tom"] for s in r["selos"]]


def textos(r):
    return [s["texto"] for s in r["selos"]]


def chave(r):
    return (r["acao"] or {}).get("chave")


SEGURADA = {"estado": v.DATA_SEGURADA, "texto": "Data segurada até 22/08 18:00",
            "dica": "Se o sinal não entrar até lá, a data volta a ficar livre."}
RESERVADA = {"estado": v.DATA_RESERVADA, "texto": "Data reservada", "dica": ""}
FORA = {"estado": v.DATA_FORA, "texto": "Fora da agenda", "dica": "Não entrou na agenda."}
LIBERADA = {"estado": v.DATA_LIBERADA, "texto": "Data liberada", "dica": "O prazo venceu."}


# ---------------------------------------------------- o que NÃO vira selo

def test_proposta_em_dia_nao_tem_selo_nenhum():
    """O caso que motivou a mudança: tudo certo = nada na barra.

    Antes esta mesma linha desenhava cinco selos verdes. Se "está tudo bem"
    ocupa espaço, "tem coisa errada" não tem onde aparecer."""
    r = v.linha_do_funil(
        status="fechado", data_estado=RESERVADA, sinal="R$ 2.670,00",
        sinal_pago=True, pagamentos={"pagas": 3, "total": 3, "sem_comprovante": 0},
        enviado_em="19/08 15:40", contrato_numero=5, contrato_assinado=True,
        aprovada_por="Maria Helena", nunca_enviada=False)
    assert r["selos"] == []
    assert r["acao"] is None


def test_o_que_ja_aconteceu_vai_pro_resumo_e_nao_some():
    """Enxugar não é esconder: o mesmo conteúdo, em cinza, no subtítulo."""
    r = v.linha_do_funil(
        status="fechado", data_estado=RESERVADA, sinal="R$ 2.670,00",
        sinal_pago=True, pagamentos={"pagas": 3, "total": 3, "sem_comprovante": 0},
        enviado_em="19/08 15:40", contrato_numero=5, contrato_assinado=True,
        aprovada_por="Maria Helena", nunca_enviada=False)
    for pedaco in ("aprovada por Maria Helena", "enviada 19/08 15:40",
                   "sinal R$ 2.670,00 recebido", "data reservada",
                   "3 de 3 pagas", "contrato nº 5 assinado", "contrato fechado"):
        assert pedaco in r["resumo"], pedaco


def test_a_ordem_do_resumo_e_a_da_historia_da_empresa():
    """Fechou o negócio → o dinheiro entrou → a data está de pé → assinou.

    A ordem é decisão, não acaso da sequência do código: o resumo é montado num
    lugar só justamente pra isso."""
    r = v.linha_do_funil(
        status="fechado", data_estado=RESERVADA, sinal="R$ 900,00", sinal_pago=True,
        pagamentos={"pagas": 1, "total": 3, "sem_comprovante": 0},
        enviado_em="19/08 15:40", contrato_numero=5, contrato_assinado=True,
        aprovada_por="Ana", nunca_enviada=False)
    assert r["resumo"] == ("aprovada por Ana · enviada 19/08 15:40 · "
                           "sinal R$ 900,00 recebido · data reservada · "
                           "1 de 3 pagas · contrato nº 5 assinado · contrato fechado")


def test_parcela_que_ainda_nao_venceu_nao_vira_coral():
    """Cobrar papel de parcela que ninguém pagou encheria a linha de coral que
    o dono não tem como resolver."""
    r = v.linha_do_funil(
        status="aprovada", data_estado=RESERVADA,
        pagamentos={"pagas": 0, "total": 3, "sem_comprovante": 0},
        enviado_em="19/08 15:40", nunca_enviada=False)
    assert r["selos"] == []


# ---------------------------------------------------- o que VIRA selo

def test_data_fora_da_agenda_e_coral_e_pede_marcar():
    r = v.linha_do_funil(status="aprovada", data_estado=FORA,
                         enviado_em="19/08", nunca_enviada=False)
    assert textos(r) == ["Fora da agenda"]
    assert tons(r) == ["coral"]
    assert r["acao"] == {"chave": "marcar", "texto": "Marcar data"}


def test_data_liberada_e_coral_e_pede_segurar_de_novo():
    r = v.linha_do_funil(status="aprovada", data_estado=LIBERADA,
                         enviado_em="19/08", nunca_enviada=False)
    assert tons(r) == ["coral"]
    assert chave(r) == "resegurar"


def test_data_segurada_leva_o_prazo_no_texto_do_selo():
    """O prazo é o dado — "segurada" sem "até quando" não serve pra decidir nada."""
    r = v.linha_do_funil(status="aprovada", data_estado=SEGURADA,
                         enviado_em="19/08", nunca_enviada=False)
    assert textos(r) == ["Data segurada até 22/08 18:00"]
    assert tons(r) == ["pre"]
    assert r["selos"][0]["dica"] == SEGURADA["dica"]
    assert chave(r) == "sinal"


def test_comprovante_no_singular_e_no_plural():
    um = v.linha_do_funil(status="aprovada", nunca_enviada=False, enviado_em="19/08",
                          pagamentos={"pagas": 1, "total": 3, "sem_comprovante": 1})
    dois = v.linha_do_funil(status="aprovada", nunca_enviada=False, enviado_em="19/08",
                            pagamentos={"pagas": 2, "total": 3, "sem_comprovante": 2})
    assert textos(um) == ["1 parcela sem comprovante"]
    assert textos(dois) == ["2 parcelas sem comprovante"]
    assert tons(um) == ["coral"]


def test_plano_que_nao_fecha_com_o_total_sobe_pra_linha():
    """Era um aviso que só aparecia DENTRO do confirm de "Fechar contrato": quem
    não clicava nunca ficava sabendo. E é dinheiro — os títulos a receber saem
    pelo valor das PARCELAS, não pelo que a folha declara."""
    r = v.linha_do_funil(status="aprovada", nunca_enviada=False, enviado_em="19/08",
                         plano_difere=1)
    assert textos(r) == ["Plano não fecha com o total"]
    assert tons(r) == ["ambar"]
    assert "PARCELAS" in r["selos"][0]["dica"]


def test_contrato_aguardando_assinatura_e_ambar_e_nao_oferece_fechar():
    r = v.linha_do_funil(status="aprovada", nunca_enviada=False, enviado_em="19/08",
                         contrato_numero=5, contrato_assinado=False)
    assert textos(r) == ["Contrato aguardando assinatura"]
    assert tons(r) == ["ambar"]
    assert r["acao"] is None


def test_contrato_assinado_vira_fechar_contrato_e_nao_selo():
    r = v.linha_do_funil(status="aprovada", nunca_enviada=False, enviado_em="19/08",
                         contrato_numero=5, contrato_assinado=True)
    assert r["selos"] == []
    assert r["acao"] == {"chave": "fechar", "texto": "Fechar contrato"}
    assert "contrato nº 5 assinado" in r["resumo"]


def test_ja_fechado_nao_oferece_fechar_de_novo():
    r = v.linha_do_funil(status="fechado", nunca_enviada=False, enviado_em="19/08",
                         contrato_numero=5, contrato_assinado=True)
    assert r["acao"] is None


def test_rascunho_nunca_enviada_e_azul_e_pede_mandar():
    """Azul: falta um passo, mas não corre prazo nem se perde dinheiro."""
    r = v.linha_do_funil(status="rascunho")
    assert textos(r) == ["Nunca enviada ao cliente"]
    assert tons(r) == ["azul"]
    assert r["acao"] == {"chave": "enviar", "texto": "Mandar pro cliente"}
    assert r["resumo"] == ""


def test_proposta_ja_enviada_nao_pede_pra_mandar():
    r = v.linha_do_funil(status="enviado", enviado_em="19/08 15:40", nunca_enviada=False)
    assert r["selos"] == []
    assert r["resumo"] == "enviada 19/08 15:40"


# ---------------------------------------------------- a AÇÃO é uma só

def test_todas_as_pendencias_ficam_na_barra_mesmo_com_uma_acao_so():
    """O pedido do dono, em uma frase: "deixa só os avisos na barra, de tudo que
    tá pendente". Some o BOTÃO das outras, não o aviso."""
    r = v.linha_do_funil(
        status="aprovada", data_estado=LIBERADA,
        pagamentos={"pagas": 1, "total": 3, "sem_comprovante": 1},
        enviado_em="19/08", nunca_enviada=False, plano_difere=1,
        contrato_numero=5, contrato_assinado=False)
    assert textos(r) == ["Data liberada", "1 parcela sem comprovante",
                         "Plano não fecha com o total", "Contrato aguardando assinatura"]
    assert chave(r) == "resegurar"


def test_a_data_ganha_da_papelada():
    """A ordem que o dono confirmou: primeiro o que TRAVA DINHEIRO (a data que se
    perde), depois papel. Uma data liberada é uma venda que pode ir embora; um
    contrato esperando assinatura espera mais um dia."""
    r = v.linha_do_funil(status="aprovada", data_estado=FORA, enviado_em="19/08",
                         nunca_enviada=False, contrato_numero=5, contrato_assinado=True)
    assert chave(r) == "marcar"


def test_o_sinal_ganha_do_comprovante():
    r = v.linha_do_funil(status="aprovada", data_estado=SEGURADA, enviado_em="19/08",
                         nunca_enviada=False,
                         pagamentos={"pagas": 1, "total": 3, "sem_comprovante": 1})
    assert chave(r) == "sinal"


def test_o_comprovante_ganha_do_fechar_contrato():
    r = v.linha_do_funil(status="aprovada", data_estado=RESERVADA, enviado_em="19/08",
                         nunca_enviada=False, contrato_numero=5, contrato_assinado=True,
                         pagamentos={"pagas": 1, "total": 3, "sem_comprovante": 1})
    assert chave(r) == "comprovante"


def test_a_ordem_declarada_e_a_ordem_aplicada():
    """Guarda a lista contra reordenação sem querer — ela É a regra de negócio."""
    assert v._ORDEM_ACAO == ("marcar", "resegurar", "sinal", "comprovante",
                             "fechar", "enviar")


# ---------------------------------------------------- bordas

def test_sem_nada_preenchido_nao_explode():
    r = v.linha_do_funil(status="aprovada", nunca_enviada=False)
    assert r == {"selos": [], "acao": None, "resumo": "aprovada"}


def test_sinal_pago_sem_valor_nao_vira_frase_pela_metade():
    r = v.linha_do_funil(status="aprovada", sinal="", sinal_pago=True,
                         nunca_enviada=False)
    assert "sinal" not in r["resumo"]


def test_todo_selo_tem_texto_tom_e_dica():
    """O contrato que a tela consome: sem `tom` o selo cai no azul genérico e a
    urgência some; sem `texto` a barra fica com uma caixinha vazia."""
    r = v.linha_do_funil(
        status="aprovada", data_estado=SEGURADA, plano_difere=1,
        pagamentos={"pagas": 1, "total": 2, "sem_comprovante": 1},
        contrato_numero=5, contrato_assinado=False, enviado_em="19/08",
        nunca_enviada=False)
    assert len(r["selos"]) == 4
    for s in r["selos"]:
        assert set(s) == {"texto", "tom", "dica"}
        assert s["texto"] and s["tom"] in ("coral", "ambar", "pre", "azul")
