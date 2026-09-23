"""O LEITOR DA APÓLICE EM PDF (finance/apolice_pdf): o que sai do papel, e o que não.

Testa `ler_texto` com TEXTO SINTÉTICO que imita o layout da Allianz — sem PDF e
sem dado de pessoa real. O que se testa é a leitura dos rótulos; o pymupdf é de
quem o mantém.

Os três testes que guardam defeitos ENCONTRADOS no PDF real em 18/09/2026, antes
de virarem código de tela:

* `test_campo_vazio_e_None_e_nao_o_rotulo_seguinte` — "Placa: \\n" (carro zero km)
  devolvia 'CONDIÇÕES GERAIS: 07/2026', porque `\\s*` atravessa a quebra de linha;
* `test_o_email_e_da_segurada_e_nao_do_corretor` — "E-mail:" aparece primeiro no
  bloco do corretor; o primeiro casamento devolvia o e-mail da F.F. Apolinário;
* `test_preco_nao_tem_dois_pontos` — "Preço Líquido\\nR$ 3.807,57": o rótulo com
  dois-pontos obrigatório não achava o dinheiro, e as duas checagens que fecham
  sozinhas ficavam mudas.

E o que o módulo NÃO faz, também fixado: `test_a_tabela_de_coberturas_fica_no_pdf`.
Três somas automáticas deram três números errados no mockup; a soma não é lida.
"""
from datetime import date

import pytest

from finance import apolice_pdf as ap

# CPF sintético que passa no dígito verificador (gerador: 111.444.777-35)
CPF_OK = "111.444.777-35"

PAPEL = """Nº Proposta: 139041981
AUTO | AUTOMÓVEL | ALLIANZ | PROPOSTA
SEU CORRETOR
CORRETORA EXEMPLO LTDA
E-mail: corretora@exemplo.com.br
Telefone: 8540067300
SUAS INFORMAÇÕES
Nome: FULANA DE TAL EXEMPLO
CPF/CNPJ: {cpf}
Tel: 86999990000
E-mail: fulana@exemplo.com
Endereço: RUA UM, 1, AP 1 - BAIRRO - CIDADE/UF - 12345678
INFORMAÇÕES DO CONDUTOR PRINCIPAL
Nome: FULANA DE TAL EXEMPLO
CPF: {cpf}
Idade: 71 anos
Estado Civil: Casado[a] ou convive em união estável
INFORMAÇÕES DO SEU SEGURO
Vigência: das 24H de 23/07/2026 às 24H de 23/07/2027
Nº da Proposta: 139041981
Ramo: 31 - Automóvel
Veículo:  GEELY  EX2  MAX
Cód. FIPE: 088006-0
Placa: {placa}
Condições Gerais: 07/2026
Chassi: LB3EH1SFXTX044613
Classe Bônus: 00
Zero Km: Sim
Ano/Modelo: 2026
CEP Pernoite: 71937-180
Coberturas
Casco - Básica Compreensiva
100% FIPE *
R$ 1.684,70
RCF* - Danos Materiais
R$ 200.000,00
R$ 1.422,43
Preço Líquido
R$ {liquido}
Preço Total (IOF + Juros inclusos)
R$ {total}
FRANQUIA - PARTICIPAÇÃO OBRIGATÓRIA
Franquia
Valor (R$)
25% da Normal
4.088,88
Vencimento: 5
Parcelas
Valor da Parcela
1
R$ 1.022,14
2
R$ 1.022,14
3
R$ 1.022,14
4
R$ 1.022,15
"""


def papel(**kw):
    base = dict(cpf=CPF_OK, placa="", liquido="3.807,57", total="4.088,57")
    base.update(kw)
    return PAPEL.format(**base)


def checagem(L, nome):
    return next((ok for n, ok, _ in L.checagens if n.startswith(nome)), None)


# ------------------------------------------------------------------ o feliz

def test_reconhece_a_allianz_e_le_os_campos_rotulados():
    L = ap.ler_texto(papel(), paginas=7)
    assert L.reconhecida and L.seguradora == "Allianz" and L.ok()
    c = L.campos
    assert c["vigencia_inicio"] == date(2026, 7, 23)
    assert c["vigencia_fim"] == date(2027, 7, 23)          # o campo de ouro
    assert c["numero_proposta"] == "139041981"
    assert c["classe_bonus"] == "00"
    assert c["modelo"] == "GEELY EX2 MAX"                   # os espaços duplos somem
    assert c["ano"] == "2026"
    assert c["chassi"] == "LB3EH1SFXTX044613"
    assert c["fipe"] == "088006-0"
    assert c["cep_pernoite"] == "71937180"
    assert c["zero_km"] is True
    assert c["premio_centavos"] == 380757
    assert c["total_centavos"] == 408857
    assert c["iof_centavos"] == 28100                       # derivado, não lido
    assert c["franquia_centavos"] == 408888
    assert c["parcelas"] == 4
    assert c["dia_vencimento"] == 5
    assert c["condutor_idade"] == 71
    assert c["situacao"] == "proposta"                      # tem PROPOSTA e não tem nº de apólice


def test_as_checagens_que_fecham_sozinhas_fecham():
    L = ap.ler_texto(papel())
    assert checagem(L, "soma das parcelas") is True
    assert checagem(L, "CPF") is True
    assert checagem(L, "vigência") is True
    assert L.avisos == []


# --------------------------------------------- os três defeitos do PDF real

def test_campo_vazio_e_None_e_nao_o_rotulo_seguinte():
    """"Placa: \\n" no carro zero km. `\\s*` atravessava a quebra e devolvia
    'CONDIÇÕES GERAIS: 07/2026' como placa. A resposta certa é None."""
    L = ap.ler_texto(papel(placa=""))
    assert L.campos.get("placa") is None
    assert "placa" in L.nao_achou
    L2 = ap.ler_texto(papel(placa="ABC1D23"))
    assert L2.campos["placa"] == "ABC1D23"


def test_o_email_e_da_segurada_e_nao_do_corretor():
    """"E-mail:" vem primeiro no bloco do CORRETOR. Sem ancorar em "SUAS
    INFORMAÇÕES", o primeiro casamento devolvia o e-mail da corretora como se
    fosse o da segurada — rótulo igual em bloco diferente, o erro mais silencioso."""
    L = ap.ler_texto(papel())
    assert L.campos["email"] == "fulana@exemplo.com"
    assert L.campos["telefone"] == "86999990000"            # e não o 8540067300 da corretora
    assert L.campos["nome"] == "FULANA DE TAL EXEMPLO"
    assert L.campos["cpf"] == "11144477735"


def test_preco_nao_tem_dois_pontos():
    """"Preço Líquido\\nR$ 3.807,57" — rótulo sem dois-pontos, valor na linha de
    baixo. Com `_rotulo` (que exige o dois-pontos) o dinheiro não saía, e as duas
    checagens de dinheiro ficavam mudas."""
    L = ap.ler_texto(papel())
    assert L.campos["premio_centavos"] == 380757
    assert L.campos["total_centavos"] == 408857
    assert "premio_centavos" not in L.nao_achou


# ------------------------------------------------------ o que ele NÃO faz

def test_a_tabela_de_coberturas_fica_no_pdf():
    """Três somas automáticas, três números errados (R$ 493.807,57 · 1.602,00 ·
    1.148,44). A soma NÃO é lida; as coberturas ficam no PDF anexado."""
    L = ap.ler_texto(papel())
    assert "coberturas" not in L.campos
    assert not any("cobertura" in n.lower() for n, _, _ in L.checagens)


def test_checagem_que_nao_fecha_APARECE_como_falha():
    """Dinheiro lido errado não pode virar dado no banco em silêncio."""
    L = ap.ler_texto(papel(total="9.999,99"))
    assert checagem(L, "soma das parcelas") is False


def test_NAO_existe_checagem_circular_de_iof():
    """`iof_centavos` é DERIVADO (total − líquido): "líquido + IOF = total" fecharia
    sempre, por construção. Uma checagem que não pode falhar não é checagem — e
    foi um teste tentando fazê-la falhar que a denunciou."""
    L = ap.ler_texto(papel(total="9.999,99"))
    assert checagem(L, "líquido + IOF") is None
    assert L.campos["iof_centavos"] == 999999 - 380757     # derivado, e dito assim


def test_cpf_invalido_vira_aviso():
    L = ap.ler_texto(papel(cpf="111.111.111-11"))
    assert checagem(L, "CPF") is False
    assert any("CPF" in a for a in L.avisos)


def test_layout_desconhecido_avisa_e_nao_chuta():
    """Sem layout e sem nome no papel: não inventa a seguradora, e avisa.

    O AVISO MUDOU EM 22/09/2026, e o que ele diz agora é o que saiu. Dizia "Não
    reconheci o layout desta seguradora" mesmo quando o genérico tinha tirado o
    papel inteiro pelos rótulos — e quem lia isso ia digitar tudo à mão achando
    que nada tinha sido lido. O que este teste continua segurando é o essencial:
    seguradora não se chuta, e leitura sem layout sempre pede conferência.
    """
    texto = papel().replace("ALLIANZ", "OUTRA SEGURADORA")
    L = ap.ler_texto(texto)
    assert L.reconhecida is False and L.seguradora is None
    assert "seguradora" not in L.campos                     # não inventa quem é
    assert L.como == "rotulos"                              # leu, só que sem layout
    assert any("rótulos" in a and "onfira" in a for a in L.avisos)
    # e o que os rótulos genéricos acharam, preenche
    assert L.campos["vigencia_fim"] == date(2027, 7, 23)


def test_papel_que_nao_tem_rotulo_nenhum_diz_que_nao_leu():
    """O outro lado do mesmo selo: sem nada identificável, `como` é 'nada' e o
    aviso manda cadastrar à mão em vez de fingir que leu."""
    L = ap.ler_texto("Prezado cliente, segue em anexo o documento solicitado. "
                     * 20)
    assert L.como == "nada" and L.reconhecida is False
    assert any("à mão" in a for a in L.avisos)


def test_toda_leitura_sai_carimbada_com_a_versao_do_leitor():
    """É o carimbo que deixa o painel saber que uma leitura guardada envelheceu."""
    g = ap.resumo_para_guardar(ap.ler_texto(papel()))
    assert g["versao"] == ap.VERSAO and g["como"] == "layout"


def test_sem_vigencia_nao_esta_ok():
    """Sem fim de vigência a apólice não entra em régua nenhuma — `ok()` é o
    portão que a tela usa pra habilitar o botão de cadastrar."""
    texto = papel().replace("Vigência: das 24H de 23/07/2026 às 24H de 23/07/2027\n", "")
    L = ap.ler_texto(texto)
    assert L.ok() is False
    assert "vigencia_fim" in L.nao_achou


# ------------------------------------------------------------- a ponte

def test_para_formulario_fala_a_lingua_do_cadastro_manual():
    """A conferência pré-preenche o MESMO formulário e salva pelo MESMO caminho.
    Não existe um segundo jeito de gravar apólice."""
    f = ap.para_formulario(ap.ler_texto(papel()))
    assert f["seguradora"] == "Allianz" and f["ramo"] == "auto"
    assert f["vigencia_fim"] == "2027-07-23"               # o <input type=date> quer ISO
    assert f["premio"] == "3807,57" and f["iof"] == "281,00"   # o `_cent` da tela lê vírgula
    assert f["parcelas"] == "4" and f["dia_vencimento"] == "5"
    assert f["cpf"] == "11144477735" and f["nome"] == "FULANA DE TAL EXEMPLO"


def test_resumo_para_guardar_e_json_e_nao_tem_o_texto():
    import json
    r = ap.resumo_para_guardar(ap.ler_texto(papel(), paginas=7))
    json.dumps(r)                                            # datas viram string
    assert r["campos"]["vigencia_fim"] == "2027-07-23"
    assert r["reconhecida"] is True and r["paginas"] == 7
    assert "texto" not in r and "FULANA" not in json.dumps(r).replace("FULANA DE TAL EXEMPLO", "")


# ------------------------------------------------------------ o arquivo

def test_bytes_que_nao_sao_pdf_dao_erro_legivel():
    with pytest.raises(ValueError, match="PDF"):
        ap.texto_do_pdf(b"isto nao e um pdf, e um texto")


def test_arquivo_grande_demais_e_recusado_antes_de_abrir():
    with pytest.raises(ValueError, match="16 MB"):
        ap.texto_do_pdf(b"x" * (ap.TETO_BYTES + 1))


# ────────── é apólice? de quem? (21/09/2026) ──────────
#
# O caso que trouxe estes testes: o corretor da Liberal mandou a apólice de um
# Jeep Renegade da Azul pro assistente, e o sistema registrou um LEMBRETE DE PAGAR
# PARCELA. O leitor sabia dizer "não conheço este layout"; não sabia dizer "mas
# isto é uma apólice" — e é a segunda pergunta que decide a gaveta.

AZUL = """AZUL SEGUROS
Companhia de seguros do grupo Porto Seguro
Processo SUSEP nº 15414.900123/2026-11
APÓLICE DE SEGURO DE AUTOMÓVEL
Nº da Apólice: 09 26 0531 2875389
Vigência: das 24H de 26/09/2026 às 24H de 26/09/2027
SUAS INFORMAÇÕES
Nome: LUZIA AUREA EXEMPLO
Veículo: JEEP RENEGADE 1.3 T270
Placa: QRQ4H54
Prêmio Líquido
R$ 2.665,76
Franquia: 4.000,00
Coberturas contratadas
"""

PIX = """Comprovante de transferência
Pix enviado
Valor: R$ 250,00
Data: 20/09/2026
Destinatário: FULANO DE TAL
Instituição: BANCO EXEMPLO S.A.
ID da transação: E1234567820260920
"""


def test_a_apolice_da_azul_e_reconhecida_como_apolice():
    """Sem o layout dela — só pelas marcas que todo seguro tem."""
    assert ap.e_apolice(AZUL)


def test_o_comprovante_de_pix_nao_e_apolice():
    """O que este teste protege é o caixa: a corretora usa o MESMO número pras duas
    coisas, e assumir o Pix como apólice quebraria o uso de todo dia."""
    assert not ap.e_apolice(PIX)


def test_a_proposta_allianz_passa_pelo_layout_e_nao_pelas_marcas():
    """Ela não escreve SUSEP nem "apólice" — é proposta. Reconhecer o layout é a
    prova mais forte que existe, e vale mais que a contagem de marcas."""
    assert not ap.e_apolice(papel()), "as marcas sozinhas não a pegam"
    L = ap.ler_texto(papel())
    assert L.reconhecida and L.e_apolice


def test_uma_marca_sozinha_nao_basta():
    assert not ap.e_apolice("Recibo de pagamento do seguro do carro")


def test_o_nome_da_seguradora_sai_sem_o_layout():
    L = ap.ler_texto(AZUL)
    assert not L.reconhecida, "o layout da Azul ainda não está medido"
    assert L.e_apolice
    assert L.campos["seguradora"] == "Azul Seguros"


def test_ganha_a_seguradora_que_aparece_mais_no_alto():
    """A apólice da Azul cita o grupo Porto Seguro na linha seguinte. Quem emite
    está no cabeçalho, e é um palpite — quem corrige é a conferência."""
    nome, _ = ap.nomear_seguradora(AZUL)
    assert nome == "Azul Seguros"


def test_papel_sem_seguradora_conhecida_nao_chuta():
    nome, trecho = ap.nomear_seguradora("APÓLICE\nSUSEP\nSegurado: FULANO\nVigência: 01/01/2027")
    assert nome is None and trecho is None


def test_o_aviso_de_layout_desconhecido_diz_qual_seguradora():
    L = ap.ler_texto(AZUL)
    assert any("Azul Seguros" in a and "layout" in a for a in L.avisos)


def test_o_que_da_pra_ler_da_azul_e_lido():
    """Rótulo é rótulo em qualquer seguradora: o genérico pega o que estiver
    marcado, e o resto fica em branco de propósito."""
    L = ap.ler_texto(AZUL)
    assert L.campos["nome"] == "LUZIA AUREA EXEMPLO"
    assert L.campos["vigencia_fim"] == date(2027, 9, 26)
    assert L.campos["placa"] == "QRQ4H54"


def test_o_resumo_guardado_leva_a_marca_e_o_nome_achado():
    d = ap.resumo_para_guardar(ap.ler_texto(AZUL))
    assert d["e_apolice"] is True and d["reconhecida"] is False
    assert d["seguradora"] == "Azul Seguros"


# ────────── o segurado é quem comprou, não quem vendeu (21/09/2026) ──────────
#
# Medido na apólice Mapfre de 11 páginas que o corretor mandou pelo assistente. O
# genérico devolveu segurado "MAPFRE SEGUROS GERAIS S/A", CNPJ 06.324.020/0001-02
# e "AV DAS NACOES UNIDAS, 14.261" — a sede da seguradora, com cara de dado bom.
# Confirmar aquilo criaria um CLIENTE chamado Mapfre.

MAPFRE = """MAPFRE SEGUROS GERAIS S/A
Nome: MAPFRE SEGUROS GERAIS S/A
CPF/CNPJ: 06.324.020/0001-02
Endereço: AV DAS NACOES UNIDAS, 14.261 - ALA A
Processo SUSEP 15414.900000/2026-00
APÓLICE DE SEGURO DE AUTOMÓVEL
Placa: PIR5077
Chassi: 9BFZH55L8J8038259
Coberturas contratadas
Prêmio Líquido
R$ 1.200,00
"""

MAPFRE_COM_BLOCO = MAPFRE + """
DADOS DO SEGURADO
Nome: JOAO BATISTA MACHADO
CPF/CNPJ: 111.444.777-35
Endereço: RUA DAS FLORES, 100 - TERESINA
"""


def test_a_seguradora_nao_vira_a_segurada():
    """O defeito que este teste existe pra impedir: um cliente chamado Mapfre."""
    L = ap.ler_texto(MAPFRE)
    assert "nome" not in L.campos
    assert "cpf" not in L.campos
    assert "endereco" not in L.campos
    assert "nome" in L.nao_achou


def test_o_bloco_inteiro_cai_junto_com_o_nome():
    """CPF e endereço vieram do MESMO bloco errado — jogar fora só o nome deixaria
    a apólice com o CNPJ da Mapfre no campo do cliente."""
    L = ap.ler_texto(MAPFRE)
    assert not any(k in L.campos for k in ("nome", "cpf", "telefone", "email", "endereco"))


def test_o_aviso_diz_o_que_saiu_e_por_que_foi_apagado():
    """A segunda rede, pro papel que TEM âncora e mesmo assim entrega a
    seguradora: a âncora diz onde o bloco começa, não de quem ele é."""
    L = ap.ler_texto(MAPFRE + "\nDADOS DO SEGURADO\n"
                     "Nome: MAPFRE SEGUROS GERAIS S/A\nCPF/CNPJ: 06.324.020/0001-02\n")
    assert any("MAPFRE SEGUROS GERAIS S/A" in a and "própria" in a for a in L.avisos)
    assert "nome" not in L.campos and "cpf" not in L.campos


def test_o_que_da_pra_ler_da_mapfre_continua_lido():
    """Apagar o bloco do segurado não pode levar junto o que estava certo."""
    L = ap.ler_texto(MAPFRE)
    assert L.e_apolice and L.campos["seguradora"] == "Mapfre"
    assert L.campos["placa"] == "PIR5077"
    assert L.campos["chassi"] == "9BFZH55L8J8038259"


def test_com_o_bloco_do_segurado_o_cliente_certo_sai():
    """A âncora genérica: 'DADOS DO SEGURADO' vale onde 'SUAS INFORMAÇÕES' não existe."""
    L = ap.ler_texto(MAPFRE_COM_BLOCO)
    assert L.campos["nome"] == "JOAO BATISTA MACHADO"
    assert L.campos["cpf"] == "11144477735"
    assert "RUA DAS FLORES" in L.campos["endereco"]


def test_a_ancora_nao_casa_com_seguradora():
    """'SEGURADORA' aparece antes de 'SEGURADO' em quase toda apólice. Casar com
    ela colocaria a âncora no bloco errado — que é o defeito de origem."""
    trecho, ancora = ap._bloco_do_segurado("SEGURADORA: MAPFRE\nDADOS DO SEGURADO\nNome: FULANO")
    assert ancora and "SEGURADORA" not in ancora.upper().replace("SEGURADO", "")
    assert "Nome: FULANO" in trecho


def test_o_papel_sem_ancora_avisa_em_vez_de_chutar():
    L = ap.ler_texto(MAPFRE)
    assert any("em branco de propósito" in a for a in L.avisos)


def test_a_allianz_continua_lendo_a_segurada_certa():
    """A âncora nova não pode tirar o que já funcionava."""
    L = ap.ler_texto(papel())
    assert L.campos["nome"] == "FULANA DE TAL EXEMPLO"
    assert L.campos["email"] == "fulana@exemplo.com"


def test_segurado_pessoa_juridica_nao_e_recusado():
    """Recusar todo LTDA jogaria fora metade das apólices de frota. O teste é o
    nome de uma seguradora conhecida dentro do nome, não 'parece empresa'."""
    assert not ap._e_a_propria_seguradora("TRANSPORTES BOA VIAGEM LTDA")
    assert ap._e_a_propria_seguradora("MAPFRE SEGUROS GERAIS S/A")


# ────────── o layout da Mapfre (21/09/2026) ──────────
#
# Medido na apólice de 11 páginas que o corretor mandou pelo assistente. O texto
# abaixo imita a ESTRUTURA do papel — os cabeçalhos em caixa alta e os rótulos —
# com nome, CPF, telefone e endereço inventados: dado de cliente não entra no
# repositório (regra 0).
#
# O que este layout ensinou, e a Allianz não tinha ensinado:
#
# * CADA BLOCO tem "Nome:", "CPF:" e "Endereço:" — seguradora, sucursal, corretor
#   e segurado. Ler do texto solto devolve o primeiro, que é a seguradora.
# * "DADOS DA SEGURADORA" casa com o padrão de âncora "DADOS D[OA] SEGURAD[OA]"
#   por prefixo. Sem `(?!RA)` a âncora pousa no bloco errado — o defeito que ela
#   existe pra evitar.
# * a vigência vem em DOIS rótulos, um por data, e o IOF vem em LINHA PRÓPRIA
#   (na Allianz ele é derivado de total − líquido).

MAPFRE_PAPEL = """APÓLICE DE SEGURO AUTO
DADOS GERAIS
Processo SUSEP Automóvel Nº: 15414.100326/2004-83
Nº Apólice: 0330433570731
Vigência início 24h do dia: 10/09/2026
Término 24h do dia: 10/09/2027
Nº Proposta: 2310313044698
DADOS DA SEGURADORA
Nome: MAPFRE SEGUROS GERAIS S/A
CNPJ: 61.074.175/0001-38
Endereço: AV DAS NACOES UNIDAS, 14.261 - ALA A
DADOS DO CORRETOR
Código interno e nome: 42161 - CORRETORA EXEMPLO LTDA
CPF/CNPJ: 06.324.020/0001-02
Telefone: 8632150747
Endereço: SENADOR AREA LEAO 2185
DADOS DO SEGURADO
Nome: FULANA DE TAL EXEMPLO
Tipo de pessoa: FÍSICA
CPF: {cpf}
Endereço: RUA DAS FLORES 3929
Cidade: TERESINA
Telefone celular: 86999990000
QUESTIONÁRIO DE AVALIAÇÃO DE RISCO
Nome do principal condutor: FULANA DE TAL EXEMPLO
Data de nascimento: 11/05/1958
Estado Civil: CASADO
CEP do local onde o veículo pernoita: 64064-210
IMPORTANTE: Declarações inverídicas implicarão a perda da garantia.
DADOS DO VEÍCULO
Marca/Modelo: FORD KA HATCH 1.0 SE/SE PLUS TIVCT (FL
Ano do modelo: 2018
Placa: PIR5077
0 KM: NÃO
Nº Chassi: 9BFZH55L8J8038259
VALOR DA INDENIZAÇÃO DA COBERTURA BÁSICA DE CASCO
Código na Tabela de Referência: 003408-8
FRANQUIA
Descrição
Tipo
Valor (R$)
CASCO DEDUTÍVEL
REDUZIDA 50%
3.234,20
PARA-BRISA
150,00
DEMONSTRATIVO DE PRÊMIO - VALORES EM R$
Prêmio líquido: 2.613,01
IOF: 192,84
Prêmio total: 2.805,85
PAGAMENTO DO PRÊMIO - VALORES EM R$
Nº de parcela: 12
Vencimento da 1ª parcela: 16/10/2026
Valor da 1ª parcela: 233,82
VENCIMENTO DAS PARCELAS - VALORES EM R$
Parcela
Data
Valor
02
15/11/2026
233,82
08
14/05/2027
233,82
03
15/12/2026
233,82
09
13/06/2027
233,82
04
14/01/2027
233,82
10
13/07/2027
233,82
05
13/02/2027
233,82
11
12/08/2027
233,82
06
15/03/2027
233,82
12
10/09/2027
233,83
07
14/04/2027
233,82
"""


def _so_digitos(v):
    return "".join(ch for ch in v if ch.isdigit())


def mapfre(**kw):
    return MAPFRE_PAPEL.format(**dict({"cpf": CPF_OK}, **kw))


def test_a_mapfre_e_reconhecida_pelo_layout():
    L = ap.ler_texto(mapfre())
    assert L.reconhecida and L.seguradora == "Mapfre" and L.e_apolice


def test_a_segurada_vem_do_bloco_dela_e_nao_do_primeiro_nome_do_papel():
    """Três blocos antes do dela têm "Nome:" — seguradora, sucursal e corretor."""
    L = ap.ler_texto(mapfre())
    assert L.campos["nome"] == "FULANA DE TAL EXEMPLO"
    assert L.campos["cpf"] == _so_digitos(CPF_OK)
    assert L.campos["endereco"] == "RUA DAS FLORES 3929"
    assert L.campos["telefone"] == "86999990000"


def test_o_cnpj_do_corretor_nao_entra_no_cpf_da_segurada():
    """O genérico pegou "CPF/CNPJ:" do bloco do CORRETOR — o CNPJ da própria
    corretora no campo do cliente."""
    L = ap.ler_texto(mapfre())
    assert "06324020000102" not in L.campos["cpf"]


def test_a_ancora_nao_pousa_em_dados_da_seguradora():
    """"DADOS DA SEGURADORA" é prefixo de "DADOS DA SEGURADO" + RA. Sem o `(?!RA)`
    a âncora casa com ela e o bloco lido é o da seguradora."""
    trecho, ancora = ap._bloco_do_segurado(mapfre())
    assert ancora == "DADOS DO SEGURADO"
    # e o primeiro "Nome:" DEPOIS da âncora é o da cliente, não o da seguradora
    import re as _re
    assert _re.search(r"Nome: *(.+)", trecho).group(1).strip() == "FULANA DE TAL EXEMPLO"


def test_a_vigencia_vem_dos_dois_rotulos():
    L = ap.ler_texto(mapfre())
    assert L.campos["vigencia_inicio"] == date(2026, 9, 10)
    assert L.campos["vigencia_fim"] == date(2027, 9, 10)


def test_o_iof_da_mapfre_e_lido_e_nao_derivado():
    """Na Allianz o IOF é total − líquido; aqui ele tem linha própria."""
    L = ap.ler_texto(mapfre())
    assert L.campos["premio_centavos"] == 261301
    assert L.campos["iof_centavos"] == 19284
    assert L.campos["total_centavos"] == 280585


def test_as_doze_parcelas_fecham_o_total():
    """A 1ª vem rotulada; da 2ª em diante, numa tabela de duas colunas. A soma é a
    única checagem com fonte independente do papel."""
    L = ap.ler_texto(mapfre())
    assert L.campos["parcelas"] == 12
    assert len(L.campos["parcelas_centavos"]) == 12
    assert sum(L.campos["parcelas_centavos"]) == L.campos["total_centavos"]
    assert ("soma das parcelas = total", True) in [(n, ok) for n, ok, _ in L.checagens]


def test_a_franquia_e_a_do_casco_e_nao_a_do_para_brisa():
    """A tabela lista uma dúzia de franquias; a que importa é a primeira linha."""
    L = ap.ler_texto(mapfre())
    assert L.campos["franquia_centavos"] == 323420


def test_o_veiculo_sai_do_bloco_do_veiculo():
    L = ap.ler_texto(mapfre())
    assert L.campos["placa"] == "PIR5077"
    assert L.campos["chassi"] == "9BFZH55L8J8038259"
    assert L.campos["ano"] == "2018"
    assert L.campos["zero_km"] is False
    assert L.campos["fipe"] == "003408-8"


def test_a_mapfre_nao_deixa_campo_em_branco():
    """A medição do PDF real: 25 campos, nenhum `nao_achou`, três checagens
    fechando. Este teste é o que impede o layout de regredir em silêncio."""
    L = ap.ler_texto(mapfre())
    assert L.nao_achou == [], L.nao_achou
    assert all(ok for _n, ok, _d in L.checagens)


def test_a_apolice_emitida_e_vigente_e_nao_proposta():
    assert ap.ler_texto(mapfre()).campos["situacao"] == "vigente"


# ────────── o bloco fecha, e a casa não é o cliente (21/09/2026, Porto Seguro) ──────────
#
# Terceiro caso do mesmo dia, e o que mostrou a causa raiz. A apólice Porto Seguro
# saiu com segurado "LIBERAL NETO CONS E CORG DE SEGS LTDA" — a PRÓPRIA CORRETORA
# — e o e-mail do corretor junto, enquanto CPF e telefone ficaram vazios.
#
# Aquilo não era a âncora errada: era o bloco SEM FIM. `_bloco_do_segurado`
# devolvia da âncora até o fim do papel, então um rótulo que falta no bloco certo
# era buscado nos blocos seguintes até achar. Bloco fechado transforma isso em
# campo vazio, que é o comportamento certo.
#
# E a segunda rede é genérica de verdade: nenhuma lista de seguradoras pega a
# corretora, porque a corretora é outra em cada instalação. A conta sabe o próprio
# nome, e nada que é dela pode ser o cliente.

PORTO = """PORTO SEGURO CIA DE SEGUROS GERAIS
Processo SUSEP 15414.000000/2026-00
APÓLICE DE SEGURO DE AUTOMÓVEL
Vigência: 13/08/2026 a 13/08/2027
DADOS DO SEGURADO
Razão Social: FULANA DE TAL EXEMPLO
Placa: PIH0G84
DADOS DO CORRETOR
Nome: LIBERAL NETO CONS E CORG DE SEGS LTDA
E-mail: corretor@exemplo.com
Endereço: R Jaime Da Silveira, 725
COBERTURAS CONTRATADAS
Casco 100% FIPE
"""

CASA = ("Liberal Neto", "LIBERAL NETO CONS E CORG DE SEGS LTDA",
        "06.324.020/0001-02", "corretor@exemplo.com")


def test_o_bloco_do_segurado_termina_no_proximo_cabecalho():
    """A causa raiz: o bloco ia até o FIM do papel, e o "Nome:" que faltava era
    emprestado do bloco do corretor, três cabeçalhos adiante."""
    trecho, _anc = ap._bloco_do_segurado(PORTO)
    assert "Placa: PIH0G84" in trecho, "o bloco certo entra inteiro"
    assert "LIBERAL NETO" not in trecho, "e para antes do bloco do corretor"


def test_rotulo_que_falta_fica_faltando_e_nao_vem_do_vizinho():
    """O invariante é o BLOCO, não o rótulo: `Razão Social` hoje é lido (entrou nos
    sinônimos do genérico em 22/09/2026), e o que não pode acontecer é o valor vir
    do bloco do corretor, três cabeçalhos adiante."""
    L = ap.ler_texto(PORTO)
    assert L.campos.get("nome") == "FULANA DE TAL EXEMPLO"
    assert L.campos.get("nome") != "LIBERAL NETO CONS E CORG DE SEGS LTDA"
    # o bloco do segurado deste papel não tem telefone: some, não é emprestado
    assert "telefone" not in L.campos and "telefone" in L.nao_achou


def test_a_corretora_nao_entra_como_cliente_nem_com_o_bloco_aberto():
    """A segunda rede, pro papel em que a âncora leva mesmo ao bloco errado."""
    papel = PORTO.replace("Razão Social: FULANA DE TAL EXEMPLO",
                          "Nome: LIBERAL NETO CONS E CORG DE SEGS LTDA")
    L = ap.ler_texto(papel, proibidos=CASA)
    assert "nome" not in L.campos
    assert any("própria corretora" in a for a in L.avisos)


def test_o_email_do_corretor_some_sem_levar_o_nome_junto():
    """A distinção que a Porto de verdade ensinou (22/09/2026): o contato do
    corretor DENTRO do bloco do cliente é comum — foi ele quem preencheu a
    proposta — e não quer dizer bloco errado. Some o campo; o nome e o CPF, que
    são os certos, ficam."""
    papel = PORTO.replace("DADOS DO CORRETOR\n", "")
    L = ap.ler_texto(papel, proibidos=CASA)
    assert "email" not in L.campos, "o e-mail da casa não fica"
    assert L.campos.get("nome") == "FULANA DE TAL EXEMPLO", "e não leva o nome junto"


def test_cliente_de_verdade_passa_com_a_lista_ligada():
    """A trava não pode recusar quem não é da casa."""
    L = ap.ler_texto(mapfre(), proibidos=CASA)
    assert L.campos["nome"] == "FULANA DE TAL EXEMPLO"
    assert L.avisos == []


def test_a_casa_e_reconhecida_por_nome_parcial_e_por_documento():
    assert ap._e_a_propria_casa("LIBERAL NETO CONS E CORG DE SEGS LTDA", CASA)
    assert ap._e_a_propria_casa("06324020000102", CASA)
    assert ap._e_a_propria_casa("Corretor@Exemplo.COM", CASA)
    assert not ap._e_a_propria_casa("SOLANGE MARIA LIMA MELO", CASA)


def test_nome_curto_nao_vira_trava_larga():
    """Um nome de conta de três letras casaria dentro de qualquer palavra."""
    assert not ap._e_a_propria_casa("MARIA APARECIDA", ("ZAQ",))


def test_sem_lista_de_proibidos_nada_muda():
    """A lista é rede extra; sem ela o leitor tem que continuar igual."""
    assert ap.ler_texto(mapfre()).campos["nome"] == "FULANA DE TAL EXEMPLO"


# ────────── o layout da Porto Seguro (21/09/2026) ──────────
#
# Medida na apólice de 15 páginas que o corretor mandou. Três coisas que só ela
# tem, e cada uma quebrou uma suposição do módulo:
#
# 1. os cabeçalhos NÃO são em caixa alta — são "Dados cadastrais", "Dados do
#    Corretor". O fechamento genérico de bloco não vê nenhum;
# 2. o E-MAIL DO CORRETOR está no bloco do cliente, porque foi ele quem preencheu
#    a proposta. Derrubar o bloco por isso jogaria fora o nome e o CPF certos;
# 3. é MARCA LICENCIADA: "Itaú Seguro Auto" emitido pela Porto.
#
# Texto sintético com a estrutura do papel; nome, CPF e contatos inventados.

PORTO_PAPEL = """Sua apólice chegou!
Itaú Seguro Auto Compacto
Fulana De Tal Exemplo
Hyundai Hb20S Comfort Plus 1.6 16V Flex
Válida até 13/08/2027
Itaú Seguro Auto é uma marca licenciada para uso da Porto Seguro Companhia de Seguros
Gerais. Apólice emitida pela Porto Seguro Companhia de Seguros Gerais.
Dados Gerais
  Dados da sua apólice
  Apólice:  0531 09 2835980
Item:  19
  Classe de bônus:  0
  Proposta:  20 33165281
Data de emissão:  17/08/2026
  Vigência:  Das 24h do dia 13/08/2026 às 24h do dia 13/08/2027 Processo SUSEP Nº.
15414.900656/2016-13
  Dados cadastrais
  Nome do segurado(a):  Fulana De Tal Exemplo
  CPF:  {cpf}
Data de nasc.:  08/05/1990
  Endereço:  R Das Flores, 725
  Celular:  (86) 999990000
E-mail:  corretor@exemplo.com
  Dados do veículo segurado
  Veículo:  Hyundai Hb20S Comfort Plus 1.6 16V Flex
  Ano: 2015
   Modelo: 2015
Chassi:  9BHBG41DAFP458463
  Placa:  PIH0G84
Código Tabela FIPE:  151009
Dados do Corretor
  Nome:  Corretora Exemplo Ltda
  Email: contato@corretoraexemplo.com.br
Telefone:  (86) 32150747
  Endereço: Av Senador Area Leao 2185
Valores do seu seguro
  Prêmio líquido
R$ 4.567,46
  IOF
R$ 337,08
  Total do Seguro
R$ 4.904,54
  Franquias do seu veículo
Valor (R$)
  Parabrisa
R$ 240,00
Dados do pagamento
Parcela
Valor
Vencimento
1
R$ 490,45
27/08/2026
7
R$ 490,45
27/02/2027
2
R$ 490,45
27/09/2026
8
R$ 490,45
27/03/2027
3
R$ 490,45
27/10/2026
9
R$ 490,45
27/04/2027
4
R$ 490,45
27/11/2026
10
R$ 490,49
27/05/2027
5
R$ 490,45
27/12/2026
6
R$ 490,45
27/01/2027
"""

PORTO_CASA = ("Corretora Exemplo Ltda", "corretor@exemplo.com",
              "contato@corretoraexemplo.com.br")


def porto(**kw):
    return PORTO_PAPEL.format(**dict({"cpf": CPF_OK}, **kw))


def test_a_porto_e_reconhecida_pelo_layout():
    L = ap.ler_texto(porto())
    assert L.reconhecida and L.seguradora == "Porto Seguro" and L.e_apolice


def test_a_segurada_sai_do_bloco_dela_com_cabecalho_em_title_case():
    """Três blocos com "Nome" antes: a capa, o rodapé e o corretor."""
    L = ap.ler_texto(porto())
    assert L.campos["nome"] == "FULANA DE TAL EXEMPLO"
    assert L.campos["cpf"] == _so_digitos(CPF_OK)
    assert L.campos["endereco"] == "R Das Flores, 725"


def test_o_email_do_corretor_no_bloco_do_cliente_some_sozinho():
    """E NÃO derruba o bloco: o nome e o CPF ali são os certos. A Porto traz o
    e-mail de quem preencheu a proposta, e isso é comum, não erro de bloco."""
    L = ap.ler_texto(porto(), proibidos=PORTO_CASA)
    assert "email" not in L.campos and "email" in L.nao_achou
    assert L.campos["nome"] == "FULANA DE TAL EXEMPLO", "o resto do bloco fica"
    assert L.campos["cpf"] == _so_digitos(CPF_OK)


def test_nome_da_casa_continua_derrubando_o_bloco_inteiro():
    """A distinção: contato pode ser do corretor; NOME diz de quem é o bloco."""
    papel = porto().replace("Nome do segurado(a):  Fulana De Tal Exemplo",
                            "Nome do segurado(a):  Corretora Exemplo Ltda")
    L = ap.ler_texto(papel, proibidos=PORTO_CASA)
    assert "nome" not in L.campos and "cpf" not in L.campos


def test_o_dinheiro_da_porto_vem_em_duas_linhas():
    L = ap.ler_texto(porto())
    assert L.campos["premio_centavos"] == 456746
    assert L.campos["iof_centavos"] == 33708
    assert L.campos["total_centavos"] == 490454


def test_as_dez_parcelas_fecham_o_total():
    """Na Porto a tabela é número / VALOR / data; na Mapfre é número / data /
    valor. Detalhe pequeno e silencioso — a soma é quem denuncia."""
    L = ap.ler_texto(porto())
    assert L.campos["parcelas"] == 10
    assert sum(L.campos["parcelas_centavos"]) == L.campos["total_centavos"]
    assert L.campos["dia_vencimento"] == 27


def test_a_franquia_do_para_brisa_nao_vira_a_do_casco():
    """Numa apólice de Indenização Integral não existe franquia de casco, e a
    primeira linha da tabela é o para-brisa. Ficar vazio é a resposta certa —
    R$ 240,00 num campo de franquia de casco é número errado com cara de certo."""
    L = ap.ler_texto(porto())
    assert "franquia_centavos" not in L.campos
    assert "franquia_centavos" in L.nao_achou


def test_marca_licenciada_preenche_a_emissora_e_avisa():
    """Quem emite é a Porto; o corretor pode registrar pela marca. O leitor não
    escolhe calado."""
    L = ap.ler_texto(porto())
    assert L.campos["seguradora"] == "Porto Seguro"
    assert any("Itaú Seguro Auto" in a and "troque o campo" in a for a in L.avisos)


def test_a_porto_so_deixa_em_branco_o_que_o_papel_nao_tem():
    L = ap.ler_texto(porto())
    assert set(L.nao_achou) <= {"franquia_centavos"}, L.nao_achou
    assert all(ok for _n, ok, _d in L.checagens)


def test_as_outras_seguradoras_nao_regridem():
    """A Porto entrou depois da Mapfre em `_LAYOUTS`; reconhecimento novo não pode
    roubar papel alheio."""
    assert ap.ler_texto(mapfre()).seguradora == "Mapfre"
    assert ap.ler_texto(papel()).seguradora == "Allianz"


# ────────── o leitor genérico, por sinônimos (22/09/2026) ──────────
#
# "tem 7 apólices novas sem layout também, dá uma olhada lá e já trata" — sete
# seguradoras numa manhã: HDI, Azul, Allianz, Tokio Marine, Bradesco, Zurich e
# Yelum. Layout por seguradora não escala; a corretora trabalha com umas vinte, e
# cada uma tem apólice, proposta e endosso.
#
# A evidência de que dá pra generalizar são os três layouts JÁ MEDIDOS: eles
# escrevem o mesmo rótulo de três jeitos. `_SINONIMOS` é essa tabela, e não um
# chute — cada linha nova entra depois que um PDF a justifica.
#
# Estes testes rodam o genérico à força (`ap._generico`) contra os papéis dos
# layouts conhecidos: se ele dá conta de Mapfre e Porto SEM o layout delas, dá
# conta da seguradora que eu nunca vi.


def _generico(texto, proibidos=()):
    L = ap.Leitura()
    L.proibidos = proibidos
    ap._generico(texto, L)
    ap._checar(L)
    return L


def test_o_generico_le_a_mapfre_sem_o_layout_dela():
    L = _generico(mapfre())
    assert L.campos["nome"] == "FULANA DE TAL EXEMPLO"
    assert L.campos["cpf"] == _so_digitos(CPF_OK)
    assert L.campos["numero_apolice"] == "0330433570731"
    assert L.campos["vigencia_fim"] == date(2027, 9, 10)
    assert L.campos["premio_centavos"] == 261301


def test_o_generico_le_a_porto_sem_o_layout_dela():
    """A Porto rotula o cliente como "Nome do segurado(a)" e o bloco como "Dados
    cadastrais" — nada disso é Allianzês, que era tudo o que o genérico falava."""
    L = _generico(porto())
    assert L.campos["nome"] == "FULANA DE TAL EXEMPLO"
    assert L.campos["numero_apolice"] == "0531092835980"
    assert L.campos["total_centavos"] == 490454


def test_a_soma_das_parcelas_fecha_nos_dois_papeis():
    """A conta com fonte independente. A 1ª parcela vem ROTULADA À PARTE nos dois
    (a tabela começa na 02); sem ela a soma não fecha e o dia do vencimento sai da
    parcela 2 — dia 15 no lugar de 16, medido no PDF da Mapfre."""
    for papel, total in ((mapfre(), 280585), (porto(), 490454)):
        L = _generico(papel)
        assert sum(L.campos["parcelas_centavos"]) == total
        assert ("soma das parcelas = total", True) in [(n, ok) for n, ok, _ in L.checagens]
    assert _generico(mapfre()).campos["dia_vencimento"] == 16


def test_o_generico_acha_o_dinheiro_nas_duas_formas():
    """"Prêmio líquido: 2.613,01" (Mapfre) e "Prêmio líquido\\nR$ 4.567,46" (Porto)
    são o mesmo campo escrito de dois jeitos."""
    assert _generico(mapfre()).campos["premio_centavos"] == 261301
    assert _generico(porto()).campos["premio_centavos"] == 456746


def test_o_iof_e_derivado_quando_o_papel_nao_traz():
    """A Allianz não tem linha de IOF; tem líquido e total. Derivar ali é honesto —
    o que não pode é derivar por cima de um IOF que o papel declarou."""
    L = _generico(papel())
    assert L.campos["iof_centavos"] == L.campos["total_centavos"] - L.campos["premio_centavos"]
    assert _generico(mapfre()).campos["iof_centavos"] == 19284, "a Mapfre declara, e vence"


def test_a_ancora_do_segurado_nao_casa_no_meio_de_uma_frase():
    """A capa da Porto diz "consulte o manual do segurado" na página 2. Sem prender
    a âncora ao começo da linha, o bloco do cliente começava num aviso legal — e o
    nome, o CPF e o endereço sumiam todos."""
    texto = ("consulte o manual do segurado e as condições gerais\n"
             "Dados cadastrais\nNome do segurado(a): FULANA DE TAL\n")
    trecho, ancora = ap._bloco_do_segurado(texto)
    assert ancora.upper() == "DADOS CADASTRAIS"
    assert "manual do segurado" not in trecho


def test_o_bloco_fecha_em_cabecalho_de_caixa_de_titulo():
    """A Mapfre usa CAIXA ALTA; a Porto usa "Dados do Corretor". As duas formas
    fecham o bloco, senão o da Porto ia até o fim do papel."""
    texto = ("Dados cadastrais\nNome do segurado(a): FULANA\n"
             "Dados do Corretor\nNome: CORRETORA EXEMPLO LTDA\n")
    trecho, _a = ap._bloco_do_segurado(texto)
    assert "CORRETORA EXEMPLO" not in trecho


def test_a_apolice_allianz_emitida_e_reconhecida():
    """O teste exigia "Nº da Proposta", e a APÓLICE emitida traz "Nº da Apólice" —
    uma Allianz que eu já sabia ler caía no genérico."""
    emitida = papel().replace("Nº Proposta:", "Nº da Apólice:")
    assert ap.ler_texto(emitida).seguradora == "Allianz"


def test_o_generico_nao_inventa_o_que_nao_esta_rotulado():
    """O eixo do módulo inteiro: campo vazio é resposta, campo errado não é."""
    L = _generico("APÓLICE DE SEGURO\nSUSEP 123\nVigência: 01/01/2026 a 01/01/2027\n"
                  "Coberturas contratadas\nPrêmio\n")
    assert "nome" not in L.campos and "premio_centavos" not in L.campos
    assert L.campos["vigencia_fim"] == date(2027, 1, 1)


# ══════════ O PAPEL TEM DUAS DIMENSÕES (22/09/2026) ══════════
#
# Cinco PDFs do dono, de cinco seguradoras. Três deles — Yelum, Tokio Marine e
# Bradesco — saíam com TRÊS campos, e não era falta de sinônimo: nesses papéis o
# rótulo não fica na mesma LINHA do valor, fica EM CIMA dele. Achatar a página
# pra texto joga fora justamente o que liga os dois.
#
#   Nome do(a) Segurado(a)                 <- x=17.6  y=126.9
#   CPF/CNPJ                               <- x=298.2 y=126.9
#   CLAUDIA FERNANDA DO SOCORRO NUNES      <- x=17.6  y=143.9
#   372.584.513-15                         <- x=298.2 y=143.9


class _Span:
    """Uma caixa de texto com posição, como o pymupdf devolve."""
    def __init__(self, x0, y0, txt, larg=90.0, alt=11.0):
        self.d = {"text": txt, "bbox": (x0, y0, x0 + larg, y0 + alt)}


class _Pagina:
    def __init__(self, spans):
        self._spans = spans

    def get_text(self, _modo):
        return {"blocks": [{"lines": [{"spans": [s.d for s in self._spans]}]}]}


def _desenho(*linhas):
    """[(x, y, texto[, largura]), ...] → os pares que o desenho tira dali."""
    return ap.pares_do_desenho(
        [_Pagina([_Span(*c) for c in linhas])])


def test_o_valor_embaixo_do_rotulo_e_o_valor_dele():
    """O caso da Yelum, medido: dois rótulos numa faixa, dois valores na de baixo."""
    pares = _desenho((17.6, 126.9, "Nome do(a) Segurado(a)"),
                     (298.2, 126.9, "CPF/CNPJ"),
                     (17.6, 143.9, "CLAUDIA FERNANDA DO SOCORRO NUNES"),
                     (298.2, 143.9, "372.584.513-15"))
    assert pares["nome do segurado"] == "CLAUDIA FERNANDA DO SOCORRO NUNES"
    assert pares["cpf/cnpj"] == "372.584.513-15"


def test_o_pareamento_e_por_sobreposicao_e_nao_por_x_igual():
    """Dinheiro vem alinhado à direita, embaixo de um rótulo alinhado à esquerda."""
    pares = _desenho((40.0, 100.0, "Prêmio Líquido (R$)"),
                     (95.0, 118.0, "266,03"))
    assert pares["premio liquido"] == "266,03"


def test_empate_de_sobreposicao_nao_vira_dado():
    """Duas caixas embaixo cobrindo o rótulo igualzinho: ambiguidade não se
    resolve chutando."""
    pares = _desenho((0.0, 100.0, "Prêmio", 100.0),
                     (0.0, 118.0, "111,11", 50.0),
                     (50.0, 118.0, "222,22", 50.0))
    assert "premio" not in pares


def test_rotulo_embaixo_de_rotulo_conhecido_nao_e_par():
    """Cabeçalho de duas faixas: o de baixo é rótulo, não valor.

    A recusa vale pros rótulos que ESTE módulo conhece — é o que dá pra afirmar
    sem inventar uma gramática de "o que parece rótulo". O resto é barrado
    depois, pela prova de formato do campo (`_tem_a_cara`)."""
    pares = _desenho((10.0, 100.0, "Endereço"), (10.0, 118.0, "Placa"))
    assert "endereco" not in pares


def test_caixa_longe_demais_nao_e_o_valor():
    """Do outro lado da página não é "embaixo"."""
    pares = _desenho((10.0, 100.0, "Placa"), (10.0, 400.0, "QRO6093"))
    assert "placa" not in pares


def test_o_rotulo_se_reduz_ao_que_ele_e():
    """É o que faz "Prêmio Líquido (R$)" bater com "Prêmio líquido" da tabela."""
    assert ap._chave_rotulo("Prêmio Líquido (R$)") == "premio liquido"
    assert ap._chave_rotulo("Nome do(a) Segurado(a)") == "nome do segurado"
    assert ap._chave_rotulo("Nº da Apólice:") == "no da apolice"


# ── o valor tem que ter a cara do campo ───────────────────────────────────────
#
# A "Capa Frota LION MINING" do Bradesco é um formulário de caixinhas, e parear
# por desenho devolveu dali `email = "questionário de avaliação de risco"`,
# `endereco = "Bairro:"` e `modelo = "Diária:"`. Ler pela diagramação acerta
# muito e erra feio; campo vazio é melhor que campo errado.


def test_rotulo_solto_nunca_e_valor():
    assert ap._tem_a_cara("endereco", "Bairro:") is False
    assert ap._tem_a_cara("modelo", "Diária:") is False


def test_email_tem_que_ser_email():
    assert ap._tem_a_cara("email", "questionário de avaliação de risco") is False
    assert ap._tem_a_cara("email", "araujolimapi@bol.com.br") is True


def test_placa_chassi_e_cpf_tem_formato():
    assert ap._tem_a_cara("placa", "QRO6093") is True
    assert ap._tem_a_cara("placa", "Não possui") is False
    assert ap._tem_a_cara("chassi", "9BGKS48U0KG376785") is True
    assert ap._tem_a_cara("chassi", "Sim") is False
    assert ap._tem_a_cara("cpf", "372.584.513-15") is True
    assert ap._tem_a_cara("cpf", "Jurídica") is False


def test_nome_com_digito_nao_e_nome():
    """"QD RAIMUNDO PORTELA, 4" é endereço; nome de gente não tem número."""
    assert ap._tem_a_cara("nome", "MARIO JOSE VANDERLEI") is True
    assert ap._tem_a_cara("nome", "RUA DAVID CALDAS , 3082") is False


def test_campo_sem_prova_de_formato_passa():
    """A prova é uma trava, não uma lista de permissões: o que não tem prova
    definida continua valendo."""
    assert ap._tem_a_cara("obs", "qualquer coisa") is True


# ── data por extenso, e dinheiro em três linhas ───────────────────────────────


def test_data_por_extenso():
    """A Zurich escreve "24hs do dia 31 de Março de 2026"."""
    assert ap._data("24hs do dia 31 de Março de 2026") == date(2026, 3, 31)


def test_data_por_extenso_com_a_codificacao_estragada():
    """O mesmo PDF chega aqui com "MarÃ§o" — casar pelo prefixo ASCII atravessa
    o estrago sem precisar adivinhar a codificação do arquivo."""
    assert ap._data("24hs do dia 31 de MarÃ§o de 2026") == date(2026, 3, 31)


def test_mes_que_nao_existe_nao_vira_data():
    assert ap._data("31 de Brumário de 2026") is None


def test_a_data_com_barra_continua_vencendo():
    assert ap._data("das 24h de 13/08/2026 às 24h de 13/08/2027") == date(2026, 8, 13)


def test_dinheiro_com_o_simbolo_numa_linha_so_dele():
    """A Zurich quebra em três: "Prêmio Total\\nR$\\n 2.772,28". A regra de uma
    quebra só perdia o prêmio, o IOF e o total de uma apólice inteira."""
    v, _tr = ap._dinheiro_rotulado("Prêmio Total\nR$\n 2.772,28\n", ("Prêmio total",))
    assert v == 277228


def test_o_mesmo_rotulo_com_outra_caixa_e_o_mesmo_rotulo():
    """A tabela dizia "Nome completo" e a Zurich escreve "Nome Completo:"."""
    v, _tr = ap._rotulo("Nome Completo: MARIO JOSE VANDERLEI", "Nome completo")
    assert v == "MARIO JOSE VANDERLEI"


def test_sem_desenho_o_leitor_continua_o_de_antes():
    """`pares` é opcional: texto puro tem que se comportar como sempre se
    comportou — é o que todos os testes deste arquivo exercitam."""
    L = ap.ler_texto(papel())
    assert L.reconhecida is True and L.como == "layout"
    assert L.campos["nome"] == "FULANA DE TAL EXEMPLO"


# ══════════ QUE PAPEL É ESTE (23/09/2026) ══════════
#
# Pergunta do dono sobre os três que sobraram na fila: "veja também se é só
# proposta". É — e é mais que isso: dos três, NENHUM é a apólice emitida de um
# carro. Um é proposta, um é endosso, um é cotação de frota. Por isso "faltava
# campo": o campo não está no papel, porque o papel ainda não é o contrato.
#
# Os trechos abaixo são os cabeçalhos REAIS dos oito PDFs medidos.


def test_a_apolice_emitida_se_identifica():
    """Zurich: o papel diz o que ele é numa linha própria."""
    assert ap.tipo_do_documento("Apólice / Endosso Pessoa Física\n"
                                "Operação: Emissão da apólice\n") == "apolice"


def test_endosso_zerado_nao_faz_de_uma_apolice_um_endosso():
    """A Zurich e a Mapfre imprimem `Endosso: 0000000` numa apólice SEM endosso
    nenhum — foi o campo vazio que quase virou o tipo do documento."""
    assert ap.tipo_do_documento("APÓLICE DE SEGURO AUTO\nEndosso: 0000000\n") == "apolice"


def test_o_endosso_se_identifica():
    """Yelum."""
    assert ap.tipo_do_documento("Yelum Auto Perfil\n"
                                "Endosso - Ramo 31 Automovel - Casco\n") == "endosso"
    assert ap.tipo_do_documento("qualquer coisa\nDADOS DO ENDOSSO\n") == "endosso"


def test_o_endosso_vence_a_apolice_que_ele_altera():
    """Todo endosso traz o número da apólice que ele modifica. Sem esta ordem ele
    se apresentaria como aquela apólice — e cadastrá-lo criaria uma SEGUNDA linha
    na carteira pro mesmo carro."""
    assert ap.tipo_do_documento("DADOS DO ENDOSSO\n"
                                "Nº Apólice: 31.88.2026.0346799\n") == "endosso"


def test_a_proposta_se_identifica():
    """Tokio Marine e Allianz."""
    assert ap.tipo_do_documento("Proposta Tokio Marine Auto\n"
                                "Aceitação sujeita a análise da Seguradora\n") == "proposta"
    assert ap.tipo_do_documento("PROPOSTA\nOlá FULANA\n") == "proposta"


def test_a_cotacao_se_identifica():
    """Bradesco: "Este demonstrativo de cotação não implica na aceitação"."""
    assert ap.tipo_do_documento("RESULTADO DA FROTA\n") == "cotacao"
    assert ap.tipo_do_documento("Este demonstrativo de cotação não implica\n") == "cotacao"


def test_a_apolice_vence_a_proposta_que_deu_origem_a_ela():
    """A Porto traz "Proposta: 2033165281" na apólice já emitida."""
    assert ap.tipo_do_documento("Sua apólice chegou!\n"
                                "Proposta:  20 33165281\n") == "apolice"


def test_papel_sem_marca_nenhuma_e_tratado_como_apolice():
    """O padrão é o caso normal da tela — e a conferência é de gente, de todo jeito."""
    assert ap.tipo_do_documento("um papel qualquer sem marca") == "apolice"


def test_proposta_e_cotacao_nao_entram_como_vigentes():
    """Nenhuma das duas é contrato: pôr na carteira como "vigente" seria dizer
    que o cliente tem uma cobertura que ele não contratou."""
    L = ap.ler_texto("Proposta Tokio Marine Auto\nAceitação sujeita a análise\n" + papel())
    assert L.tipo == "proposta" and L.campos["situacao"] == "proposta"


def test_o_endosso_avisa_que_cadastrar_duplica():
    L = ap.ler_texto("DADOS DO ENDOSSO\n" + papel())
    assert L.tipo == "endosso"
    assert any("ALTERA uma apólice que já existe" in a for a in L.avisos)


def test_o_tipo_vai_carimbado_com_a_leitura():
    g = ap.resumo_para_guardar(ap.ler_texto("RESULTADO DA FROTA\n" + papel()))
    assert g["tipo"] == "cotacao"


# ── a conferência que faltava no prêmio ──────────────────────────────────────
#
# O endosso da Azul (conta 37, id 6) saiu com prêmio R$ 16,91 e quatro parcelas
# de R$ 284,25. Nenhuma conta batia porque nenhuma conta era feita: a checagem
# das parcelas só rodava quando havia TOTAL, e ali não havia.


def test_parcelas_que_nao_cabem_no_premio_sao_denunciadas():
    L = ap.Leitura()
    L.campos = {"parcelas_centavos": [28425, 28425, 28425, 28417], "premio_centavos": 1691}
    ap._checar(L)
    nome, ok, _det = L.checagens[0]
    assert nome == "as parcelas cabem no prêmio" and ok is False
    assert any("não cabe nas parcelas" in a for a in L.avisos)


def test_um_endosso_com_premio_menor_que_as_parcelas_nao_e_denunciado_a_toa():
    """Num endosso o prêmio adicional PODE ser menor que as parcelas da apólice
    de origem. A comparação é de ordem de grandeza, não de centavo."""
    L = ap.Leitura()
    L.campos = {"parcelas_centavos": [10000, 10000], "premio_centavos": 9000}
    ap._checar(L)
    assert L.checagens[0][1] is True


def test_havendo_total_a_checagem_exata_continua_sendo_a_que_vale():
    L = ap.Leitura()
    L.campos = {"parcelas_centavos": [5000, 5000], "total_centavos": 10000,
                "premio_centavos": 1}
    ap._checar(L)
    assert L.checagens[0][0] == "soma das parcelas = total" and L.checagens[0][1] is True
