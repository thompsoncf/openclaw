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
from datetime import date

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


def test_contrato_pronto_mas_nunca_enviado_e_azul_e_pede_mandar_pra_assinar():
    """O contrato nasce quando o sinal cai — antes de qualquer clique em "mandar".
    Enquanto ninguém apertou o botão, isto não é uma pendência com prazo correndo
    (por isso azul, não âmbar): é só o passo seguinte do fluxo normal."""
    r = v.linha_do_funil(status="aprovada", nunca_enviada=False, enviado_em="19/08",
                         contrato_numero=5, contrato_assinado=False)
    assert textos(r) == ["Contrato pronto — ainda não mandou pra assinar"]
    assert r["acao"] == {"chave": "assinar", "texto": "Mandar pra assinar"}
    assert tons(r) == ["azul"]


def test_contrato_enviado_e_ambar_e_pede_reenviar_pra_assinar():
    """O selo mandava "mande o link pro cliente" e NÃO HAVIA BOTÃO: o link ficava
    escondido no menu de três pontos. Agora a ação existe — e vem antes de fechar,
    porque enquanto o papel não volta assinado não há negócio pra fechar.

    E O BOTÃO MUDA DE NOME depois do primeiro envio: "Mandar" vira "Reenviar" —
    era exatamente a falta dessa distinção que fazia o card de um contrato JÁ
    mandado (conta Prime Eventos/Bianca, 28/08) parecer que nada tinha saído."""
    r = v.linha_do_funil(status="aprovada", nunca_enviada=False, enviado_em="19/08",
                         contrato_numero=5, contrato_assinado=False,
                         contrato_enviado_em=date(2026, 8, 19), hoje=date(2026, 8, 19))
    assert textos(r) == ["Aguardando assinatura há hoje"]
    assert r["acao"] == {"chave": "assinar", "texto": "Reenviar pra assinar"}
    assert tons(r) == ["ambar"]


def test_contrato_assinado_vira_fechar_negocio_e_nao_selo():
    r = v.linha_do_funil(status="aprovada", nunca_enviada=False, enviado_em="19/08",
                         contrato_numero=5, contrato_assinado=True)
    assert r["selos"] == []
    # "Fechar NEGÓCIO", não "Fechar contrato": a ação faz a mesma coisa nos dois
    # mundos (muda o status e gera os títulos a receber), e o contrato assinado já
    # está fechado — prometer fechá-lo de novo é o que confundia.
    assert r["acao"] == {"chave": "fechar", "texto": "Fechar negócio"}
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
                         "Plano não fecha com o total",
                         "Contrato pronto — ainda não mandou pra assinar"]
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
                             "assinar", "fechar", "enviar")


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


# ================================================== QUEM É O CLIENTE DA LINHA
#
# O funil montava o título com `cliente` e `empresa` — dois campos livres pra mesma
# coisa. Medido em produção em 25/08, nos 26 orçamentos existentes: 19 apareciam
# como "−", 2 apareciam como TELEFONE, 2 com o nome repetido. 23 de 26 degradados.

def test_o_cadastro_do_cliente_ganha_dos_campos_livres():
    """Dos três campos, `clientes.nome` era o único certo nas 26 linhas. O #12 é o
    caso real: o cadastro diz "Ana Clara Marques", o campo livre diz "Clara"."""
    r = v.titulo_do_funil(cadastro="Ana Clara Marques", empresa="Ana Clara Marques",
                          cliente="Clara Marques", modo="evento", numero=12)
    assert r["titulo"] == "Ana Clara Marques"


def test_telefone_nunca_vira_titulo():
    """Os #8 e #9 de produção tinham o telefone no campo `cliente` e apareciam
    assim na tela. Número identifica, mas não é nome — a cadeia pula e segue."""
    r = v.titulo_do_funil(cadastro="", empresa="Larissa Rakel Almeida Rodriges",
                          cliente="86994160050", modo="evento", numero=9)
    assert r["titulo"] == "Larissa Rakel Almeida Rodriges"


def test_telefone_em_todos_os_degraus_cai_no_numero_do_orcamento():
    """Se os TRÊS forem telefone, ainda assim não se mostra um número como nome."""
    r = v.titulo_do_funil(cadastro="86999990000", empresa="(86) 9 9999-0000",
                          cliente="+5586999990000", modo="evento", numero=7)
    assert r["titulo"] == "Orçamento nº 7"


def test_nome_curto_com_digito_continua_sendo_nome():
    """A trava do teste acima: o critério é 8+ dígitos E nenhuma letra. Na dúvida a
    resposta é NÃO — perder um nome de verdade é pior que deixar passar um número."""
    for nome in ("Loja 24h", "Bar do Zé 2", "A3", "3M do Brasil"):
        assert v.titulo_do_funil(empresa=nome, numero=1)["titulo"] == nome


def test_no_evento_o_subtitulo_descreve_a_festa():
    """`orcamentos.evento` já guardava tipo, data e convidados — e isso não aparecia
    em tela nenhuma. É o que distingue duas propostas do mesmo mês."""
    r = v.titulo_do_funil(cadastro="Isabela Silva Mendes", modo="evento", numero=11,
                          evento={"tipo": "Casamento", "data": "2026-12-19",
                                  "convidados": 150})
    assert r["sub"] == "Casamento · 19/12/2026 · 150 convidados"


def test_evento_sem_dados_nao_inventa_subtitulo():
    r = v.titulo_do_funil(cadastro="Fulana", modo="evento", evento={}, numero=3)
    assert r["sub"] == ""


def test_no_recorrente_a_empresa_manda_e_o_contato_desce():
    """Aqui os dois campos TÊM sentidos diferentes e legítimos: a empresa e quem
    fala com você. O erro era a ordem — a pessoa virava título, a empresa, rodapé.
    O #2 de produção: "Renata Cachorrinha" no título, "Renatas Digital" embaixo."""
    r = v.titulo_do_funil(empresa="Renatas Digital", cliente="Renata Cachorrinha",
                          modo="recorrente", numero=2)
    assert r["titulo"] == "Renatas Digital"
    assert r["sub"] == "Renata Cachorrinha"


def test_contato_igual_ao_titulo_nao_repete():
    """O #8 de produção mostrava "Aladdin Consultoria · Aladdin Consultoria"."""
    r = v.titulo_do_funil(empresa="Aladdin Consultoria", cliente="aladdin consultoria",
                          modo="recorrente", numero=8)
    assert r["sub"] == "", "o mesmo nome não pode aparecer duas vezes na linha"


def test_contato_que_e_telefone_nao_vira_subtitulo():
    r = v.titulo_do_funil(empresa="Flash Car", cliente="86994160050",
                          modo="recorrente", numero=9)
    assert r["sub"] == ""


# As linhas REAIS de produção (25/08). Antes: 19 traços e 2 telefones. Depois:
# 26 nomes de verdade e zero "Orçamento nº N" — foi assim que a proposta foi
# aprovada, e é isso que este caso trava.
_REAIS = [
    # (cadastro, empresa, cliente, modo, esperado)
    ("Ana Clara Marques", "Ana Clara Marques", "Clara Marques", "evento", "Ana Clara Marques"),
    ("Isabela Silva Mendes", "Isabela Silva Mendes", "", "evento", "Isabela Silva Mendes"),
    ("Larissa Rakel Almeida Rodriges", "Larissa Rakel Almeida Rodriges", "86994160050",
     "evento", "Larissa Rakel Almeida Rodriges"),
    ("Josiany Rayra Soares dos Santos", "Josiany Rayra Soares dos Santos", "86998192489",
     "evento", "Josiany Rayra Soares dos Santos"),
    ("Camila Damasceno", "Camila Damasceno Rodrigues", "", "evento", "Camila Damasceno"),
    ("", "BELEZA HAIR", "", "recorrente", "BELEZA HAIR"),
    ("", "MedClin", "", "recorrente", "MedClin"),
    ("", "Renatas Digital", "Renata Cachorrinha", "recorrente", "Renatas Digital"),
    ("", "H Pernas", "Jose Carlos", "recorrente", "H Pernas"),
]


def test_as_linhas_de_producao_todas_resolvem_pra_um_nome():
    for cadastro, empresa, cliente, modo, esperado in _REAIS:
        r = v.titulo_do_funil(cadastro=cadastro, empresa=empresa, cliente=cliente,
                              modo=modo, numero=99)
        assert r["titulo"] == esperado, f"{empresa!r} virou {r['titulo']!r}"
        assert not r["titulo"].startswith("Orçamento nº"), \
            f"{empresa!r} caiu no último degrau — nenhuma linha real precisa dele"


# ================================================== O CONTRATO, QUE ERA DUAS COISAS

def test_aguardando_assinatura_conta_os_dias():
    """3 dias e 30 dias pedem reações diferentes, e o selo dizia a mesma coisa nos
    dois casos. `hoje` é parâmetro pra este teste não depender do relógio.

    Conta do ENVIO, não da criação: o cliente só começou a esperar quando o link
    chegou na mão dele, não quando o contrato nasceu na conta."""
    r = v.linha_do_funil(status="aprovada", nunca_enviada=False, contrato_numero=2,
                         contrato_assinado=False, contrato_enviado_em=date(2026, 8, 22),
                         hoje=date(2026, 8, 25))
    assert textos(r) == ["Aguardando assinatura há 3 dias"]


def test_um_dia_no_singular_e_hoje_por_extenso():
    def sel(d):
        return textos(v.linha_do_funil(
            status="aprovada", nunca_enviada=False, contrato_numero=2,
            contrato_assinado=False, contrato_enviado_em=d, hoje=date(2026, 8, 25)))[0]
    assert sel(date(2026, 8, 24)).endswith("há 1 dia")
    assert sel(date(2026, 8, 25)).endswith("há hoje")


def test_marcado_como_enviado_sem_data_util_nao_inventa_dias():
    """Defensivo: se por algum motivo `contrato_enviado_em` chegar preenchido mas
    não for uma data de verdade, some o "há N dias", fica o selo. Inventar
    "há 0 dias" seria pior que não dizer."""
    r = v.linha_do_funil(status="aprovada", nunca_enviada=False, contrato_numero=2,
                         contrato_assinado=False, contrato_enviado_em=True)
    assert textos(r) == ["Aguardando assinatura"]


def test_aprovada_sem_contrato_a_haver_oferece_fechar_negocio():
    """O #2 de produção estava aprovado havia 37 dias, no recorrente, SEM ação
    nenhuma na linha — e ali contrato nenhum ia nascer."""
    r = v.linha_do_funil(status="aprovada", nunca_enviada=False, tem_contrato=False)
    assert r["acao"] == {"chave": "fechar", "texto": "Fechar negócio"}


def test_aprovada_no_nicho_de_contrato_nao_ganha_selo_nem_acao_de_fechar():
    """Onde o contrato AINDA VEM (nasce com o sinal), fechar não é o passo seguinte
    — e "ainda não tem contrato" é o estado normal de quem acabou de aprovar, não
    uma pendência. Quem cobra o passo que falta é o selo do sinal."""
    r = v.linha_do_funil(status="aprovada", nunca_enviada=False, tem_contrato=True)
    assert r["selos"] == []
    assert r["acao"] is None


def test_fechado_sem_documento_fala_em_negocio_e_nao_em_contrato():
    """O #1 de produção está `fechado` com contrato NULL, e o resumo dizia
    "contrato fechado" — descrevendo mudança de status como papel assinado."""
    assert "negócio fechado" in v.linha_do_funil(status="fechado", nunca_enviada=False)["resumo"]
    assert "contrato fechado" in v.linha_do_funil(
        status="fechado", nunca_enviada=False, contrato_numero=4,
        contrato_assinado=True)["resumo"]
