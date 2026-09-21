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
    texto = papel().replace("ALLIANZ", "OUTRA SEGURADORA")
    L = ap.ler_texto(texto)
    assert L.reconhecida is False and L.seguradora is None
    assert "seguradora" not in L.campos                     # não inventa quem é
    assert any("Não reconheci" in a for a in L.avisos)
    # mas o que os rótulos genéricos acharam, preenche
    assert L.campos["vigencia_fim"] == date(2027, 7, 23)


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
