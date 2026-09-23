"""O leitor da apólice em PDF: o que sai do papel, e o que fica no papel.

MEDIDO ANTES DE ESCRITO (docs/mockups/renovacoes_layout_e_import_pdf.html, na
proposta Allianz nº 139041981, 7 páginas):

  * os 25 campos ROTULADOS — no formato `Rótulo: valor` — foram todos
    encontrados, e 9 conferidos contra o cadastro feito à mão bateram 9/9;
  * a TABELA de coberturas NÃO se extrai. Três abordagens, três somas, todas
    erradas (R$ 493.807,57 · R$ 1.602,00 · R$ 1.148,44) e todas com cara de
    número. O certo, R$ 3.807,57, só saiu lendo com o olho.

Então este módulo lê SÓ o que tem rótulo, e não tenta a tabela. É de propósito e
está no nome de cada função: um leitor que erra CALADO é pior que digitar, porque
vigência lida errada é alerta que não dispara — e a tela parece funcionando.

O QUE ELE DEVOLVE nunca vai direto pro banco. Devolve um dicionário de campos, cada
um com o valor e o trecho de onde saiu, mais as CHECAGENS que fecham sozinhas
(soma das parcelas = total, dígito do CPF, fim depois do início) e a lista do que
NÃO achou. A
tela de conferência mostra tudo isso e a pessoa confirma. `salvar` é dela, não
daqui.

SEGURADORA QUE EU NÃO CONHEÇO: os rótulos genéricos são tentados mesmo assim; o que
achar, preenche; o que não achar, fica vazio e a leitura vem com `reconhecida=False`
— a tela diz "não reconheci este layout, confira tudo". Nunca chuta. Cada
seguradora nova é um bloco em `_LAYOUTS`, depois que um PDF dela chegar.
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal

#: teto do que se lê. Apólice é texto: 7 páginas da Allianz dão 60 KB. Um PDF de
#: 16 MB aqui é foto escaneada (que este leitor não lê) ou engano.
TETO_BYTES = 16 * 1024 * 1024
#: além disto o documento não é apólice, é um livro — e o texto todo fica em memória
TETO_PAGINAS = 40


@dataclass
class Leitura:
    seguradora: str | None = None          # 'Allianz' quando reconhecida
    reconhecida: bool = False
    campos: dict = field(default_factory=dict)     # chave -> valor já convertido
    trechos: dict = field(default_factory=dict)    # chave -> o pedaço do texto de onde saiu
    checagens: list = field(default_factory=list)  # [(nome, ok: bool, detalhe)]
    nao_achou: list = field(default_factory=list)  # chaves que ficaram vazias
    paginas: int = 0
    avisos: list = field(default_factory=list)
    #: o papel tem as marcas de um seguro (SUSEP, apólice, segurado, vigência)?
    #: É INDEPENDENTE de `reconhecida`: dá True na Azul, cujo layout eu não leio.
    e_apolice: bool = False
    #: nome, documento e e-mail da própria corretora — nada disso é o segurado
    proibidos: tuple = ()
    #: COMO esta leitura saiu, que é diferente de quanto ela achou:
    #: 'layout'  — o bloco da seguradora, que sabe onde cada campo mora;
    #: 'rotulos' — o genérico, que leu pelos rótulos do papel e achou coisa;
    #: 'nada'    — passou o genérico e não achou nada que identifique a apólice.
    #: A tela mostrava "layout não reconhecido" em vermelho pros DOIS últimos, e o
    #: dono leu isso como "o sistema não está reconhecendo" num papel do qual eu
    #: tinha acabado de tirar treze campos (22/09/2026).
    como: str = "nada"
    #: QUE PAPEL É ESTE: apolice | proposta | endosso | cotacao. Ver `TIPOS`.
    tipo: str = "apolice"

    def ok(self) -> bool:
        """Tem o mínimo pro alerta existir: seguradora e fim da vigência."""
        return bool(self.campos.get("seguradora") and self.campos.get("vigencia_fim"))


# ------------------------------------------------------------------ utilidades

def _dinheiro(txt: str | None) -> int | None:
    """'R$ 3.807,57' / '3.807,57' -> 380757 (centavos). None quando não é número."""
    if not txt:
        return None
    m = re.search(r"(\d{1,3}(?:\.\d{3})*|\d+),(\d{2})", txt)
    if not m:
        return None
    return int(m.group(1).replace(".", "")) * 100 + int(m.group(2))


#: MÊS POR EXTENSO, pelas TRÊS PRIMEIRAS LETRAS. A Zurich escreve a vigência como
#: "24hs do dia 31 de Março de 2026" — e escreve com a codificação estragada, que
#: chega aqui como "MarÃ§o". Casar pelo prefixo ASCII atravessa o estrago sem
#: precisar adivinhar a codificação original do arquivo.
_MESES = ("jan", "fev", "mar", "abr", "mai", "jun",
          "jul", "ago", "set", "out", "nov", "dez")
_POR_EXTENSO = re.compile(r"(\d{1,2})\s+de\s+([A-Za-zÀ-ÿ\u0080-\uffff]{3,})\s+de\s+(\d{4})",
                          re.IGNORECASE)


def _data(txt: str | None) -> date | None:
    if not txt:
        return None
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", txt)
    if m:
        try:
            return datetime.strptime(m.group(0), "%d/%m/%Y").date()
        except ValueError:
            return None
    m = _POR_EXTENSO.search(txt)
    if not m:
        return None
    pref = m.group(2)[:3].lower()
    if pref not in _MESES:
        return None
    try:
        return date(int(m.group(3)), _MESES.index(pref) + 1, int(m.group(1)))
    except ValueError:
        return None


def _digitos(txt: str | None) -> str:
    return re.sub(r"\D", "", txt or "")


def _rotulo(texto: str, rotulo: str, ate: str = r"[^\n]+") -> tuple[str | None, str | None]:
    """O valor depois de `Rótulo:` — e o trecho inteiro, pra tela mostrar de onde veio.

    A âncora é o rótulo COM dois-pontos: é o que distingue 'CPF: 287...' de uma
    frase que só menciona CPF. É por isso que os 25 saíram certos e a tabela não —
    tabela não tem rótulo por valor.
    """
    # `[ \t]*` e NÃO `\s*`: `\s` atravessa a quebra de linha, e um campo VAZIO
    # ("Placa: \n") devolvia o rótulo seguinte como se fosse o valor — a placa da
    # Maria de Fátima saiu 'CONDIÇÕES GERAIS: 07/2026'. Carro zero km não tem placa,
    # e a resposta certa pra isso é None.
    # IGNORECASE: a Zurich escreve "Nome Completo:" e a tabela dizia "Nome
    # completo" — o mesmo rótulo com outra caixa é o mesmo rótulo, e essa
    # diferença sozinha custou o nome do segurado numa apólice inteira.
    m = re.search(re.escape(rotulo) + r"[ \t]*:[ \t]*(" + ate + ")", texto, re.IGNORECASE)
    if not m:
        return None, None
    v = m.group(1).strip()
    return (v or None), m.group(0).strip()


# --------------------------------------------------- é apólice? de quem?

# AS MARCAS DE UM SEGURO, que independem de seguradora. Nasceram do caso de
# 21/09/2026: o Cássio mandou "APOLICE LUZIA AUREA.pdf" (Azul/Porto, Jeep
# Renegade) pro assistente e ele registrou um LEMBRETE DE PAGAR PARCELA — leu o
# carnê e guardou a apólice na gaveta do caixa. O leitor até então só dizia "é
# apólice" quando reconhecia o LAYOUT, e o único layout medido é o da Allianz.
#
# Reconhecer o layout e reconhecer o DOCUMENTO são perguntas diferentes: a
# primeira decide quanto dá pra ler, a segunda decide em qual gaveta ele cai. Esta
# é a segunda, e por isso é frouxa no rótulo e exigente no conjunto.
_MARCAS_APOLICE = (
    r"\bSUSEP\b",                                  # o registro do regulador
    r"\bAP[ÓO]LICE\b",
    r"PROPOSTA DE SEGURO|CERTIFICADO DE SEGURO|SEGURO AUTO|SEGURO DE AUTOM[ÓO]VEL",
    r"\bSEGURAD[OA]\b|\bSEGURADORA\b|\bESTIPULANTE\b",
    r"\bVIG[ÊE]NCIA\b",
    r"\bPR[ÊE]MIO\b|\bFRANQUIA\b|\bCOBERTURAS?\b",
)

#: quantas marcas bastam. Três porque comprovante de Pix, boleto de fornecedor e
#: nota fiscal não têm NENHUMA — o risco não é o falso positivo raro, é sequestrar
#: o caixa de quem usa o mesmo número pras duas coisas.
_MINIMO_MARCAS = 3


def e_apolice(texto: str) -> bool:
    """O documento é uma apólice/proposta de seguro? Sem olhar a seguradora."""
    return sum(bool(re.search(m, texto, re.I)) for m in _MARCAS_APOLICE) >= _MINIMO_MARCAS


# O NOME da seguradora é achável mesmo quando o layout não é. São coisas
# separadas de propósito: dizer "Azul Seguros" no aviso do WhatsApp é o que faz a
# pessoa reconhecer o documento de relance, e não custa nada ler errado — o campo
# vai pro formulário de conferência, que é onde alguém confirma.
_NOMES_SEGURADORA = (
    (r"PORTO\s*SEGURO", "Porto Seguro"),
    (r"\bAZUL\s+SEGUROS?\b", "Azul Seguros"),
    (r"\bALLIANZ\b", "Allianz"),
    (r"\bBRADESCO\s+SEGUROS?\b|\bBRADESCO\s+AUTO", "Bradesco Seguros"),
    (r"\bTOKIO\s*MARINE\b", "Tokio Marine"),
    (r"\bMAPFRE\b", "Mapfre"),
    (r"\bSOMPO\b", "Sompo"),
    (r"\bHDI\b", "HDI"),
    (r"\bZURICH\b", "Zurich"),
    (r"\bLIBERTY\b", "Liberty"),
    (r"\bSUHAI\b", "Suhai"),
    (r"\bALFA\s+SEGURADORA\b", "Alfa"),
    (r"\bYELUM\b", "Yelum"),
    (r"\bIT[AÁ]U\b", "Itaú"),
    (r"\bSULAM[ÉE]RICA\b", "SulAmérica"),
    (r"\bAKAD\b", "Akad"),
    (r"\bEZZE\b", "Ezze"),
)


def nomear_seguradora(texto: str) -> tuple[str | None, str | None]:
    """(nome, trecho) da seguradora citada mais no ALTO do papel. (None, None) se nenhuma.

    Mais no alto e não primeira-da-tabela porque o cabeçalho é onde a emissora se
    identifica; o resto do documento cita corretora, resseguradora e grupo. Ainda
    assim isto é um PALPITE de nome — quando a apólice cita duas marcas do mesmo
    grupo, ganha a que aparece antes, e quem corrige é a pessoa na conferência.
    """
    achados = []
    for padrao, nome in _NOMES_SEGURADORA:
        m = re.search(padrao, texto, re.I)
        if m:
            achados.append((m.start(), nome, m.group(0).strip()))
    if not achados:
        return None, None
    achados.sort()
    return achados[0][1], achados[0][2]


# ------------------------------------------------- o bloco de QUEM comprou

# ONDE COMEÇAM OS DADOS DO SEGURADO. A apólice traz VÁRIOS blocos com os mesmos
# rótulos — a seguradora se identifica, a corretora se identifica, e só depois vem
# o cliente. Sem âncora, o primeiro "Nome:" do papel é o da SEGURADORA.
#
# Medido em 21/09/2026, na apólice Mapfre de 11 páginas que o corretor mandou: o
# genérico devolveu segurado "MAPFRE SEGUROS GERAIS S/A", CNPJ 06.324.020/0001-02
# e endereço "AV DAS NACOES UNIDAS, 14.261" — a sede da seguradora em São Paulo,
# com cara de dado bom. Salvar aquilo criaria um CLIENTE chamado Mapfre.
#
# A âncora da Allianz (`SUAS INFORMAÇÕES`) nasceu do mesmo defeito, em 18/09, com
# o e-mail do corretor. Era específica demais: valia pra um layout só.
#
# O `(?!RA)` aparece em TODO padrão que termina em SEGURADO, e não só no rótulo
# solto: a apólice Mapfre abre com o cabeçalho "DADOS DA SEGURADORA", e sem a
# guarda o padrão "DADOS D[OA] SEGURAD[OA]" casa com o prefixo dele — a âncora
# pousaria justamente no bloco que ela existe pra evitar. Achado ao ler o PDF de
# verdade, depois de o conserto já estar escrito.
#
# TODA ÂNCORA É PRESA AO COMEÇO DA LINHA (`^\s*`). Sem isso a última delas — o
# rótulo solto — casava com a palavra "segurado" NO MEIO DE UMA FRASE: na capa da
# Porto Seguro está escrito "consulte o manual do segurado", na página 2, e o
# bloco do cliente passava a começar ali, num parágrafo de aviso legal. Medido em
# 22/09/2026, ao rodar o leitor genérico contra o PDF de verdade.
_ANCORAS_SEGURADO = (
    r"^\s*SUAS\s+INFORMA[ÇC][ÕO]ES",
    r"^\s*DADOS\s+D[OA]\s+SEGURAD[OA](?!RA)",
    r"^\s*DADOS\s+D[OA]\s+CLIENTE",
    r"^\s*DADOS\s+CADASTRAIS",          # a Porto Seguro chama assim
    r"^\s*IDENTIFICA[ÇC][ÃA]O\s+D[OA]\s+SEGURAD[OA](?!RA)",
    r"^\s*SEGURAD[OA]\s*:",             # o rótulo solto, e só quando é rótulo
)

#: onde um bloco TERMINA. A linha em caixa alta serve pra Mapfre e Allianz; a
#: Porto Seguro escreve os cabeçalhos em Caixa de Título ("Dados do Corretor",
#: "Valores do seu seguro"), e sem esta segunda forma o bloco dela ia até o fim do
#: papel — que é justamente o defeito que fechar o bloco veio resolver.
_FIM_DE_BLOCO = re.compile(
    r"^(?:[A-ZÀ-Ü0-9][A-ZÀ-Ü0-9 ºª/\.\-\(\)&,\']{5,}"
    r"|\s*(?:Dados|Valores|Coberturas|Question[áa]rio|Informa[çc][õo]es)\b[^\n]*)$",
    re.M)


#: uma linha inteira em CAIXA ALTA é cabeçalho de bloco. É a estrutura que TODA
#: apólice brasileira usa — Mapfre, Porto, Allianz —, e é o que fecha o bloco.
_CABECALHO = re.compile(r"^[A-ZÀ-Ü0-9][A-ZÀ-Ü0-9 ºª/\.\-\(\)&,'ºÇÃÕÉÊÍÓÚÂÔ]{5,}$", re.M)


def _bloco_do_segurado(texto: str) -> tuple[str | None, str | None]:
    """O bloco onde estão os dados de QUEM comprou. (None, None) se não achar.

    O BLOCO TERMINA NO PRÓXIMO CABEÇALHO, e é essa a diferença que importa. Até
    21/09/2026 isto devolvia da âncora até o FIM DO PAPEL, e o efeito apareceu na
    apólice Porto Seguro: o bloco do segurado dela não traz "Nome:" com esse
    rótulo, então a busca seguiu adiante e trouxe o nome do bloco do CORRETOR — a
    própria Liberal saiu como segurada, com o e-mail do Cássio junto.

    Bloco fechado transforma esse erro em campo VAZIO, que é o comportamento certo:
    o que falta, a pessoa preenche; o que vem errado, ela confirma sem ver.

    Vence a âncora que aparecer MAIS CEDO — âncora tardia pegaria o bloco do
    beneficiário ou o das condições gerais.
    """
    achados = []
    for padrao in _ANCORAS_SEGURADO:
        m = re.search(padrao, texto, re.I | re.M)
        if m:
            achados.append((m.start(), m.end(), m.group(0).strip()))
    if not achados:
        return None, None
    ini, fim_ancora, ancora = min(achados)
    prox = _FIM_DE_BLOCO.search(texto, fim_ancora)
    return texto[ini:prox.start() if prox else len(texto)], ancora


def _e_a_propria_casa(valor: str, proibidos: tuple[str, ...]) -> bool:
    """O que saiu é da própria corretora — nome, e-mail ou documento dela?

    A GENERALIZAÇÃO do teste da seguradora, e a que resolve o caso Porto: nenhuma
    lista de terceiros dá conta, porque o bloco que vaza é o do CORRETOR, e o
    corretor é diferente em cada instalação. Mas cada conta sabe o próprio nome, o
    dos membros e os e-mails deles — e nada disso pode ser o cliente.

    Compara por dígitos quando o valor é documento ou telefone, e por texto
    normalizado no resto: "LIBERAL NETO CONS E CORG DE SEGS LTDA" tem que bater
    com a conta chamada "Liberal Neto".
    """
    v = _achatar(valor)
    if not v:
        return False
    so_num = _digitos(valor)
    for p in proibidos:
        pd = _digitos(p)
        if so_num and pd and len(pd) >= 10 and so_num == pd:
            return True
        pv = _achatar(p)
        if pv and len(pv) >= 5 and (pv in v or v in pv):
            return True
    return False


def _achatar(txt: str) -> str:
    """Minúsculo, sem acento e sem pontuação — pra comparar nome de empresa."""
    import unicodedata
    t = unicodedata.normalize("NFKD", (txt or "").lower())
    t = "".join(ch for ch in t if not unicodedata.combining(ch))
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _e_a_propria_seguradora(nome: str) -> bool:
    """O nome lido é o da seguradora, e não o do cliente?

    O teste é o nome de uma seguradora CONHECIDA dentro dele — e não "parece
    empresa": segurado pessoa jurídica existe, e recusar todo LTDA jogaria fora
    metade das apólices de frota.
    """
    return any(re.search(p, nome or "", re.I) for p, _ in _NOMES_SEGURADORA)


# ---------------------------------------------------------------- os layouts

def _allianz(texto: str, L: Leitura) -> None:
    """A proposta/apólice Allianz Auto — o único layout medido até 18/09/2026."""
    c, t = L.campos, L.trechos

    def pega(chave, rotulo, conv=lambda v: v, ate=r"[^\n]+"):
        v, tr = _rotulo(texto, rotulo, ate)
        if v is None:
            L.nao_achou.append(chave)
            return None
        val = conv(v)
        if val is None:
            L.nao_achou.append(chave)
            return None
        c[chave] = val
        t[chave] = tr
        return val

    c["seguradora"] = "Allianz"
    c["ramo"] = "auto"
    # vigência: "das 24H de 23/07/2026 às 24H de 23/07/2027" — duas datas na linha
    vig, tr = _rotulo(texto, "Vigência")
    if vig:
        datas = re.findall(r"\d{2}/\d{2}/\d{4}", vig)
        if len(datas) >= 2:
            c["vigencia_inicio"], c["vigencia_fim"] = _data(datas[0]), _data(datas[1])
            t["vigencia_fim"] = tr
    if "vigencia_fim" not in c:
        L.nao_achou.append("vigencia_fim")

    pega("numero_proposta", "Nº da Proposta", lambda v: _digitos(v) or None)
    # apólice emitida traz "Nº da Apólice"; a proposta ainda não tem
    pega("numero_apolice", "Nº da Apólice", lambda v: _digitos(v) or None)
    pega("classe_bonus", "Classe Bônus", lambda v: v.strip()[:4])
    pega("modelo", "Veículo", lambda v: re.sub(r"\s+", " ", v).strip())
    pega("ano", "Ano/Modelo", lambda v: _digitos(v)[:4] or None)
    pega("chassi", "Chassi", lambda v: v.strip().upper() or None)
    pega("placa", "Placa", lambda v: v.strip().upper().replace("-", "") or None)
    pega("fipe", "Cód. FIPE")
    pega("cep_pernoite", "CEP Pernoite", lambda v: _digitos(v) or None)
    pega("zero_km", "Zero Km", lambda v: v.strip().lower().startswith("s"))
    # dinheiro: o rótulo NÃO tem dois-pontos, e o valor vem na linha de baixo
    # ("Preço Líquido\nR$ 3.807,57"). `_rotulo` exige o dois-pontos de propósito,
    # então aqui é outra âncora: rótulo, quebra opcional, R$.
    for chave, rotulo in (("premio_centavos", "Preço Líquido"), ("total_centavos", "Preço Total")):
        m = re.search(re.escape(rotulo) + r"[^\n]*\n?\s*R\$ ?(\d{1,3}(?:\.\d{3})*,\d{2})", texto)
        if m:
            c[chave], t[chave] = _dinheiro(m.group(1)), m.group(0).strip()
        else:
            L.nao_achou.append(chave)
    if c.get("premio_centavos") is not None and c.get("total_centavos") is not None:
        c["iof_centavos"] = c["total_centavos"] - c["premio_centavos"]
    # franquia: "25% da Normal | 4.088,88" — o primeiro valor em dinheiro depois do rótulo
    m = re.search(r"Franquia[\s\S]{0,120}?(\d{1,3}(?:\.\d{3})*,\d{2})", texto)
    if m:
        c["franquia_centavos"] = _dinheiro(m.group(1))
        t["franquia_centavos"] = m.group(0)[-60:]
    else:
        L.nao_achou.append("franquia_centavos")
    # parcelas: "Parcelas | Valor da Parcela | 1 | R$ 1.022,14 | ..."
    parcelas = re.findall(r"\n(\d{1,2})\n\s*R\$ ?(\d{1,3}(?:\.\d{3})*,\d{2})", texto)
    if parcelas:
        c["parcelas"] = max(int(n) for n, _ in parcelas)
        c["parcelas_centavos"] = [_dinheiro(v) for _, v in parcelas]
    # o papel diz só "Vencimento: 5" — sem "Dia de". Medido, não suposto.
    pega("dia_vencimento", "Vencimento", lambda v: int(_digitos(v)[:2]) if _digitos(v) else None)
    # O SEGURADO — lido só DEPOIS da âncora do bloco dele. Rótulo igual em bloco
    # diferente é o jeito mais silencioso de errar: sem o corte, "Nome:" devolve a
    # SEGURADORA e "E-mail:" devolve o do corretor.
    DO_SEGURADO = ("nome", "cpf", "telefone", "email", "endereco")
    seg, ancora = _bloco_do_segurado(texto)
    if seg is None:
        # SEM ÂNCORA NÃO SE LÊ O SEGURADO. Em branco a pessoa digita; errado ela
        # confirma sem ver, e o cadastro ganha um cliente que não existe.
        L.nao_achou.extend(DO_SEGURADO)
        L.avisos.append("Não achei onde começam os dados do segurado neste papel — "
                        "deixei nome, CPF, telefone e endereço em branco de propósito. "
                        "Preencha olhando o PDF.")
        seg = None
    if seg is not None:
        def pega_seg(chave, rotulo, conv=lambda v: v):
            v, tr = _rotulo(seg, rotulo)
            val = conv(v) if v is not None else None
            if val is None:
                L.nao_achou.append(chave)
                return None
            c[chave], t[chave] = val, tr
            return val
        pega_seg("nome", "Nome", lambda v: re.sub(r"\s+", " ", v).strip().upper() or None)
        pega_seg("cpf", "CPF/CNPJ", lambda v: _digitos(v) or None)
        pega_seg("telefone", "Tel", lambda v: _digitos(v) or None)
        pega_seg("email", "E-mail", lambda v: v.strip().lower() or None)
        pega_seg("endereco", "Endereço")
        _conferir_o_segurado(L, DO_SEGURADO)
    # o condutor (só o que muda o preço na renovação)
    pega("condutor_idade", "Idade", lambda v: int(_digitos(v)) if _digitos(v) else None)
    pega("condutor_estado_civil", "Estado Civil")

    # a situação: o documento diz o que é
    c["situacao"] = "proposta" if re.search(r"\bPROPOSTA\b", texto) and not c.get("numero_apolice") else "vigente"


def _conferir_o_segurado(L: Leitura, chaves: tuple[str, ...]) -> None:
    """O bloco lido é mesmo do CLIENTE? Se não for, vai tudo fora.

    A âncora sozinha não basta, e este é o lugar onde isso ficou provado duas
    vezes em 21/09/2026: na Mapfre saiu a SEGURADORA, na Porto Seguro saiu a
    CORRETORA, com o e-mail do corretor junto. São blocos diferentes vazando pelo
    mesmo buraco, e a resposta é a mesma.

    JOGA FORA O BLOCO INTEIRO, não só o campo que denunciou: CPF, telefone e
    endereço vieram do mesmo lugar errado, e um deles sozinho no formulário é
    exatamente o tipo de dado que alguém confirma sem olhar.
    """
    c, t = L.campos, L.trechos

    # CONTATO DA CORRETORA NO BLOCO DO CLIENTE É COMUM, e não é erro de bloco: a
    # apólice Porto Seguro traz o e-mail do corretor em "Dados cadastrais", porque
    # foi ele quem preencheu a proposta. Derrubar o bloco por causa disso jogaria
    # fora o nome e o CPF CERTOS da segurada. Some só o campo.
    for chave in ("email", "telefone"):
        if c.get(chave) and _e_a_propria_casa(str(c[chave]), L.proibidos):
            c.pop(chave, None)
            t.pop(chave, None)
            if chave not in L.nao_achou:
                L.nao_achou.append(chave)

    # NOME E DOCUMENTO são outra coisa: são eles que dizem DE QUEM é o bloco. Se
    # um deles é da casa ou da seguradora, o bloco inteiro é de outra gente.
    culpado, quem = "", ""
    if _e_a_propria_seguradora(c.get("nome", "")):
        culpado, quem = c["nome"], "a própria seguradora"
    else:
        for chave in ("nome", "cpf"):
            if c.get(chave) and _e_a_propria_casa(str(c[chave]), L.proibidos):
                culpado, quem = str(c[chave]), "da própria corretora"
                break
    if not culpado:
        return
    for chave in chaves:
        c.pop(chave, None)
        t.pop(chave, None)
        if chave not in L.nao_achou:
            L.nao_achou.append(chave)
    L.avisos.append(f"O que saiu do papel no lugar do cliente foi \"{culpado}\" — é "
                    f"{quem}, não o segurado. Apaguei os dados do segurado pra não "
                    "cadastrar errado; preencha olhando o PDF.")


def _mapfre(texto: str, L: Leitura) -> None:
    """A apólice Mapfre Auto — medida em 21/09/2026, na de 11 páginas que o
    corretor mandou (apólice 0330433570731, emitida 17/09/2026).

    O papel é organizado em blocos com cabeçalho em caixa alta, e CADA UM tem
    "Nome:", "CPF:" e "Endereço:" — seguradora, sucursal, corretor, segurado. Por
    isso aqui nada é lido do texto solto: tudo sai do bloco certo. Foi este layout
    que mostrou o preço de ler sem bloco — o genérico devolveu a MAPFRE no nome e
    o CNPJ da própria LIBERAL (o corretor) no CPF.
    """
    c, t = L.campos, L.trechos
    c["seguradora"], c["ramo"] = "Mapfre", "auto"

    def bloco(cabecalho: str, ate: tuple[str, ...]) -> str:
        i = texto.find(cabecalho)
        if i < 0:
            return ""
        resto = texto[i + len(cabecalho):]
        fins = [resto.find(x) for x in ate if resto.find(x) > 0]
        return resto[:min(fins)] if fins else resto

    def pega(chave, rotulo, onde=None, conv=lambda v: v):
        v, tr = _rotulo(onde if onde is not None else texto, rotulo)
        val = conv(v) if v is not None else None
        if val is None:
            L.nao_achou.append(chave)
            return None
        c[chave], t[chave] = val, tr
        return val

    pega("numero_apolice", "Nº Apólice", conv=lambda v: _digitos(v) or None)
    pega("numero_proposta", "Nº Proposta", conv=lambda v: _digitos(v) or None)
    # a vigência vem em DOIS rótulos, um por data — não numa linha só como a Allianz
    vi = pega("vigencia_inicio", "Vigência início 24h do dia", conv=_data)
    vf = pega("vigencia_fim", "Término 24h do dia", conv=_data)
    if vi and vf:
        t["vigencia_fim"] = f"Vigência {vi:%d/%m/%Y} → {vf:%d/%m/%Y}"

    seg = bloco("DADOS DO SEGURADO", ("QUESTIONÁRIO", "DADOS DO"))
    pega("nome", "Nome", seg, lambda v: re.sub(r"\s+", " ", v).strip().upper() or None)
    pega("cpf", "CPF", seg, lambda v: _digitos(v) or None)
    pega("endereco", "Endereço", seg)
    pega("telefone", "Telefone celular", seg, lambda v: _digitos(v) or None)

    _conferir_o_segurado(L, ("nome", "cpf", "telefone", "email", "endereco"))

    vei = bloco("DADOS DO VEÍCULO", ("VALOR DA INDENIZAÇÃO", "REVISTA DO CARRO"))
    pega("modelo", "Marca/Modelo", vei, lambda v: re.sub(r"\s+", " ", v).strip() or None)
    pega("ano", "Ano do modelo", vei, lambda v: _digitos(v)[:4] or None)
    pega("placa", "Placa", vei, lambda v: v.strip().upper().replace("-", "") or None)
    pega("chassi", "Nº Chassi", vei, lambda v: v.strip().upper() or None)
    pega("zero_km", "0 KM", vei, lambda v: v.strip().upper().startswith("S"))
    pega("fipe", "Código na Tabela de Referência")
    pega("cep_pernoite", "CEP do local onde o veículo pernoita",
         conv=lambda v: _digitos(v) or None)

    # o dinheiro vem rotulado e com dois-pontos — e o IOF vem em LINHA PRÓPRIA,
    # ao contrário da Allianz, onde ele é derivado. Aqui é lido, não calculado.
    pega("premio_centavos", "Prêmio líquido", conv=_dinheiro)
    pega("iof_centavos", "IOF", conv=_dinheiro)
    pega("total_centavos", "Prêmio total", conv=_dinheiro)
    pega("parcelas", "Nº de parcela", conv=lambda v: int(_digitos(v)) if _digitos(v) else None)
    pega("dia_vencimento", "Vencimento da 1ª parcela",
         conv=lambda v: int(_digitos(v)[:2]) if _digitos(v) else None)

    # AS PARCELAS FECHAM A CONTA. A 1ª vem rotulada; da 2ª em diante vêm numa
    # tabela de DUAS COLUNAS (02 … 08 … na mesma linha do papel), que o texto
    # extraído desenrola em trios número/data/valor.
    primeira, _tr = _rotulo(texto, "Valor da 1ª parcela")
    valores = [_dinheiro(primeira)] if _dinheiro(primeira) is not None else []
    valores += [_dinheiro(v) for _n, _d, v in
                re.findall(r"\n(\d{2})\n(\d{2}/\d{2}/\d{4})\n([\d.]*\d,\d{2})", texto)]
    if len(valores) > 1:
        c["parcelas_centavos"] = valores

    # a franquia do casco: a primeira linha da tabela, e a única que importa aqui
    m = re.search(r"CASCO DEDUTÍVEL\n[^\n]*\n\s*(\d{1,3}(?:\.\d{3})*,\d{2})", texto)
    if m:
        c["franquia_centavos"], t["franquia_centavos"] = _dinheiro(m.group(1)), m.group(0)
    else:
        L.nao_achou.append("franquia_centavos")

    risco = bloco("QUESTIONÁRIO DE AVALIAÇÃO DE RISCO", ("IMPORTANTE:", "DADOS DO"))
    nasc = _rotulo(risco, "Data de nascimento")[0]
    if _data(nasc):
        c["condutor_idade"] = (date.today() - _data(nasc)).days // 365
        t["condutor_idade"] = f"Data de nascimento: {nasc}"
    pega("condutor_estado_civil", "Estado Civil", risco,
         lambda v: re.sub(r"\s+", " ", v).strip() or None)

    c["situacao"] = "vigente" if c.get("numero_apolice") else "proposta"


def _porto(texto: str, L: Leitura) -> None:
    """A apólice Porto Seguro Auto — medida em 21/09/2026, na de 15 páginas que o
    corretor mandou (apólice 0531 09 2835980, emitida 17/08/2026).

    TRÊS COISAS QUE SÓ ELA TEM, e cada uma quebrou uma suposição do módulo:

    1. OS CABEÇALHOS NÃO SÃO EM CAIXA ALTA — são "Dados cadastrais", "Dados do
       Corretor", "Dados do veículo segurado". O fechamento genérico de bloco
       (linha em caixa alta) não vê nenhum, e por isso este layout recorta os
       blocos pelo nome, como o da Mapfre.
    2. O E-MAIL DO CORRETOR ESTÁ NO BLOCO DO CLIENTE. Não é erro de leitura: foi
       ele quem preencheu a proposta. Quem resolve é `_conferir_o_segurado`, que
       apaga o campo sem derrubar o nome e o CPF certos.
    3. É MARCA LICENCIADA: "Itaú Seguro Auto" emitido pela Porto Seguro. Quem
       emite é a Porto; quem o corretor chama pelo nome pode ser a outra. O leitor
       preenche a emissora e AVISA, em vez de escolher calado.
    """
    c, t = L.campos, L.trechos
    c["seguradora"], c["ramo"] = "Porto Seguro", "auto"

    def bloco(cabecalho: str, ate: tuple[str, ...]) -> str:
        i = texto.find(cabecalho)
        if i < 0:
            return ""
        resto = texto[i + len(cabecalho):]
        fins = [resto.find(x) for x in ate if resto.find(x) > 0]
        return resto[:min(fins)] if fins else resto

    def pega(chave, rotulo, onde=None, conv=lambda v: v):
        v, tr = _rotulo(onde if onde is not None else texto, rotulo)
        val = conv(v) if v is not None else None
        if val is None:
            L.nao_achou.append(chave)
            return None
        c[chave], t[chave] = val, tr
        return val

    dados = bloco("Dados da sua apólice", ("Dados cadastrais", "Dados do"))
    pega("numero_apolice", "Apólice", dados, lambda v: _digitos(v) or None)
    pega("numero_proposta", "Proposta", dados, lambda v: _digitos(v) or None)
    pega("classe_bonus", "Classe de bônus", dados, lambda v: v.strip()[:4])
    # "Das 24h do dia 13/08/2026 às 24h do dia 13/08/2027" — duas datas na linha
    vig, tr = _rotulo(dados or texto, "Vigência")
    datas = re.findall(r"\d{2}/\d{2}/\d{4}", vig or "")
    if len(datas) >= 2:
        c["vigencia_inicio"], c["vigencia_fim"] = _data(datas[0]), _data(datas[1])
        t["vigencia_fim"] = tr
    else:
        L.nao_achou.append("vigencia_fim")

    seg = bloco("Dados cadastrais", ("Dados do veículo", "Dados do Corretor"))
    pega("nome", "Nome do segurado(a)", seg,
         lambda v: re.sub(r"\s+", " ", v).strip().upper() or None)
    pega("cpf", "CPF", seg, lambda v: _digitos(v) or None)
    pega("endereco", "Endereço", seg)
    pega("telefone", "Celular", seg, lambda v: _digitos(v) or None)
    pega("email", "E-mail", seg, lambda v: v.strip().lower() or None)
    nasc = _rotulo(seg, "Data de nasc.")[0]
    if _data(nasc):
        c["condutor_idade"] = (date.today() - _data(nasc)).days // 365
        t["condutor_idade"] = f"Data de nasc.: {nasc}"

    _conferir_o_segurado(L, ("nome", "cpf", "telefone", "email", "endereco"))

    vei = bloco("Dados do veículo segurado", ("Dados do Corretor", "Valores do seu"))
    pega("modelo", "Veículo", vei, lambda v: re.sub(r"\s+", " ", v).strip() or None)
    pega("ano", "Ano", vei, lambda v: _digitos(v)[:4] or None)
    pega("placa", "Placa", vei, lambda v: v.strip().upper().replace("-", "") or None)
    pega("chassi", "Chassi", vei, lambda v: v.strip().upper() or None)
    pega("fipe", "Código Tabela FIPE", vei)

    # o dinheiro vem em DUAS LINHAS: o rótulo, depois "R$ valor". Mesma âncora da
    # Allianz, e pelo mesmo motivo — tabela não põe dois-pontos.
    for chave, rotulo in (("premio_centavos", "Prêmio líquido"),
                          ("iof_centavos", "IOF"),
                          ("total_centavos", "Total do Seguro")):
        m = re.search(re.escape(rotulo) + r"[^\n]*\n\s*R\$ ?(\d{1,3}(?:\.\d{3})*,\d{2})", texto)
        if m:
            c[chave], t[chave] = _dinheiro(m.group(1)), m.group(0).strip()
        else:
            L.nao_achou.append(chave)

    # as parcelas: tabela de duas colunas, na ordem número / valor / data — a
    # Mapfre usa número / data / valor. Detalhe pequeno e silencioso.
    par = re.findall(r"\n(\d{1,2})\nR\$ ?(\d{1,3}(?:\.\d{3})*,\d{2})\n(\d{2}/\d{2}/\d{4})",
                     texto)
    if par:
        c["parcelas"] = max(int(n) for n, _v, _d in par)
        c["parcelas_centavos"] = [_dinheiro(v) for _n, v, _d in par]
        primeira = min(par, key=lambda x: int(x[0]))
        c["dia_vencimento"] = int(primeira[2][:2])
        t["dia_vencimento"] = f"1ª parcela vence {primeira[2]}"
    else:
        L.nao_achou += ["parcelas", "dia_vencimento"]

    # A FRANQUIA DO CASCO NÃO É LIDA de propósito. O que a Porto lista em
    # "Franquias do seu veículo" é vidro, farol e lanterna; numa apólice de
    # Indenização Integral não existe franquia de casco, e pegar a primeira linha
    # da tabela devolveria R$ 240,00 do para-brisa como se fosse ela. Foi o que
    # aconteceu na leitura de 14:52 daquele dia.
    L.nao_achou.append("franquia_centavos")

    if re.search(r"marca licenciada para uso da Porto Seguro", texto, re.I):
        # `[uú]` porque a palavra é "Itaú" — o 'u' É o acentuado. Sem isto o
        # aviso saía sem o nome da marca, que é justamente o que ele tem de dizer.
        marca = re.search(r"^\s*((?:It[aá][uú]|[A-ZÀ-Ú][\w]+)[^\n]{0,40}Seguro\s+Auto[^\n]*)",
                          texto, re.M)
        L.avisos.append(
            "Esta apólice é EMITIDA pela Porto Seguro"
            + (f' com a marca "{marca.group(1).strip()}"' if marca else " com marca licenciada")
            + ". Preenchi a emissora; se você registra pela marca, troque o campo.")

    c["situacao"] = "vigente" if c.get("numero_apolice") else "proposta"


# ───────────────────────── o leitor genérico ─────────────────────────
#
# POR QUE ELE EXISTE, e por que ele importa mais que qualquer layout novo: em
# 22/09/2026 chegaram SETE apólices de SETE seguradoras diferentes numa manhã —
# HDI, Azul, Allianz, Tokio Marine, Bradesco, Zurich e Yelum. A corretora trabalha
# com umas vinte, e cada uma tem apólice, proposta e endosso. Layout por
# seguradora não escala: seriam sete medições, e amanhã a oitava.
#
# A EVIDÊNCIA de que dá pra generalizar são os TRÊS layouts já medidos. Eles
# escrevem o MESMO rótulo de três jeitos:
#
#   campo      Allianz            Mapfre                  Porto
#   segurado   Nome               Nome                    Nome do segurado(a)
#   CPF        CPF/CNPJ           CPF                     CPF
#   apólice    Nº da Apólice      Nº Apólice              Apólice
#   proposta   Nº da Proposta     Nº Proposta             Proposta
#   prêmio     Preço Líquido      Prêmio líquido          Prêmio líquido
#   total      Preço Total        Prêmio total            Total do Seguro
#
# Então a tabela abaixo não é chute: é o que três documentos de verdade mostraram,
# e cada linha nova entra depois que um PDF a justifica.
#
# O QUE O GENÉRICO NÃO FAZ continua valendo: não soma tabela, não chuta, e não lê
# o bloco do segurado sem âncora. Campo vazio é resposta; campo errado não é.

# ────────────────── O PAPEL TEM DUAS DIMENSÕES, O TEXTO TEM UMA ──────────────────
#
# Medido em 22/09/2026, nos cinco PDFs que o dono mandou. Três deles — Yelum,
# Tokio Marine e Bradesco — saíam com TRÊS campos: só o ramo, a seguradora e a
# situação. Não era falta de sinônimo. Era que nesses papéis o rótulo não fica na
# mesma LINHA do valor: fica EM CIMA dele, numa tabela de duas faixas.
#
# Extrair o texto achata a página lendo de cima pra baixo, e uma faixa de rótulos
# seguida de uma faixa de valores vira isto:
#
#     Nome do(a) Segurado(a)                 <- rótulo, x=17.6  y=126.9
#     CPF/CNPJ                               <- rótulo, x=298.2 y=126.9
#     CLAUDIA FERNANDA DO SOCORRO NUNES      <- valor,  x=17.6  y=143.9
#     372.584.513-15                         <- valor,  x=298.2 y=143.9
#
# Nenhuma regra sobre o texto achatado recupera isso, porque a informação que
# liga rótulo e valor — estarem na MESMA COLUNA — foi jogada fora no achatamento.
# As coordenadas ainda estão no PDF; é só não descartá-las.
#
# O pareamento é por SOBREPOSIÇÃO HORIZONTAL com a linha de baixo, e não por x
# igual: assim funciona com coluna alinhada à esquerda, ao centro ou à direita
# (dinheiro quase sempre vem à direita do rótulo dele).
#
# AS TRAVAS, porque parear por desenho pode inventar par onde não há:
#
#   * o par só vale se a sobreposição do escolhido for ESTRITAMENTE maior que a
#     do segundo colocado — empate é ambiguidade, e ambiguidade não vira dado;
#   * valor que é ele mesmo um rótulo conhecido não é valor (duas faixas de
#     rótulo seguidas, que acontece em cabeçalho de duas linhas);
#   * a linha de baixo tem que estar logo abaixo (até 26pt), não do outro lado da
#     página;
#   * e nada disso ENTRA POR CIMA: o desenho só preenche campo que a leitura do
#     texto deixou vazio. Onde os dois sabem, vence quem leu o rótulo com
#     dois-pontos, que é a forma sem ambiguidade nenhuma.


def _chave_rotulo(txt: str) -> str:
    """O rótulo reduzido ao que ele é, pra comparar com os sinônimos.

    Sem acento, sem caixa, sem o que vem entre parênteses ("(R$)", "(a)", "(%)")
    e sem pontuação nas pontas. É o que faz "Prêmio Líquido (R$)" da Yelum bater
    com "Prêmio líquido" da tabela, e "Nome do(a) Segurado(a)" com "Nome do
    segurado(a)".
    """
    t = unicodedata.normalize("NFKD", txt).encode("ascii", "ignore").decode()
    t = re.sub(r"\([^)]*\)", " ", t)
    t = re.sub(r"[^A-Za-z0-9/ ]+", " ", t).lower()
    return re.sub(r"\s+", " ", t).strip()


#: todo rótulo que este módulo conhece — usado pra recusar um "valor" que na
#: verdade é o rótulo da faixa seguinte
def _todos_os_rotulos() -> frozenset:
    fora = set()
    for tab in (_SINONIMOS, _SINONIMOS_SEGURADO, _SINONIMOS_DINHEIRO):
        for rots in tab.values():
            fora.update(_chave_rotulo(r) for r in rots)
    fora.update(_chave_rotulo(r) for r in _ROTULOS_VIGENCIA)
    return frozenset(fora)


#: quanto o valor pode estar abaixo do rótulo, em pontos de PDF
_ABAIXO = 26.0
#: quanto ele pode estar À DIREITA. Medido na Capa de Frota do Bradesco: os vãos
#: rótulo→valor vão de 3pt ("E-mail:") a 69pt ("IOF:", que é curto e por isso fica
#: longe da coluna do valor). O primeiro par ERRADO daquele formulário
#: ("Tel. Comercial:" alcançando a coluna da direita) está a 237pt — e já é barrado
#: antes, por `_cara_de_rotulo`. O vão é a segunda trava, não a primeira.
_ADIANTE = 90.0
#: duas caixas na mesma faixa horizontal
_MESMA_FAIXA = 2.0
#: texto comprido demais é parágrafo, não valor de campo
_VALOR_MAX = 120


def _cara_de_rotulo(txt: str, proibidos) -> bool:
    """Esta caixa é rótulo de outra coisa, e não um valor?

    Duas provas: termina em dois-pontos, ou é um rótulo que este módulo conhece.
    É o que faz a varredura para de andar pra direita ao encontrar a coluna
    seguinte do formulário."""
    return txt.strip().endswith(":") or _chave_rotulo(txt) in proibidos


def _a_direita(faixa, i, proibidos) -> str | None:
    """O valor que está À DIREITA do rótulo, na MESMA linha.

    A metade que faltava. `pares_do_desenho` nasceu (#809) olhando a Yelum, onde o
    rótulo fica EM CIMA do valor, e eu generalizei cedo demais: a Capa de Frota do
    Bradesco é a forma mais comum de formulário — rótulo à esquerda, valor à
    direita, na mesma linha —, e o leitor não enxergava nada dela. O achatamento
    pra texto embaralha a ordem (sai "CEP:", "CPF/CNPJ:", "Tipo Cliente:", e só
    depois os três valores), então nem "Rótulo: valor" numa linha existe pra ler.

    JUNTA as caixas seguintes enquanto elas forem valor: o telefone daquele papel
    vem partido em "(88)" e "98126-5379", e só as duas juntas são um telefone.
    Para na primeira caixa com cara de rótulo — a coluna da direita do formulário
    — ou num vão maior que `_ADIANTE`.
    """
    _y, _x0, x1, _y1, _rot = faixa[i]
    pedacos, borda = [], x1
    for (_ya, xa, xb, _yb, txt) in faixa[i + 1:]:
        if xa - borda > _ADIANTE or _cara_de_rotulo(txt, proibidos):
            break
        pedacos.append(txt)
        borda = xb
    return " ".join(pedacos) if pedacos else None


def pares_do_desenho(doc) -> dict:
    """{rótulo → [candidatos]} lidos pela POSIÇÃO: a caixa à DIREITA e a de baixo.

    DOIS CANDIDATOS, não um. A da direita vem primeiro na lista porque é a forma
    mais comum de formulário, mas nenhuma das duas posições é certa sempre — e
    escolher pela posição foi o que quebrou a Yelum quando a regra da direita
    nasceu: lá os rótulos de dinheiro ficam lado a lado numa faixa
    ("Prêmio Líquido (R$)  Adic. Franc (R$)  IOF (R$)…") com os valores na faixa
    de baixo, e o vizinho da direita não é rótulo conhecido nem termina em
    dois-pontos, então passava como se fosse o valor.

    Quem decide entre os dois é a PROVA DE FORMATO do campo (`_tem_a_cara`), em
    `_do_desenho`. O desenho oferece; o campo escolhe. É mais honesto que
    qualquer regra posicional, porque a prova sabe o que aquele campo tem que ser.

    O primeiro par de cada rótulo vence: rótulo de cabeçalho se repete página a
    página, e a primeira ocorrência é a do corpo do documento.
    """
    proibidos = _todos_os_rotulos()
    pares: dict = {}
    for pg in doc:
        caixas = []
        try:
            blocos = pg.get_text("dict")["blocks"]
        except Exception:  # noqa: BLE001
            continue
        for b in blocos:
            for ln in b.get("lines", []):
                for sp in ln.get("spans", []):
                    t = (sp.get("text") or "").strip()
                    if not t:
                        continue
                    x0, y0, x1, y1 = sp["bbox"]
                    caixas.append((y0, x0, x1, y1, t))
        caixas.sort()
        faixas: list = []
        for cx in caixas:
            if faixas and abs(cx[0] - faixas[-1][0][0]) <= _MESMA_FAIXA:
                faixas[-1].append(cx)
            else:
                faixas.append([cx])
        for i, faixa in enumerate(faixas):
            baixo = faixas[i + 1] if i + 1 < len(faixas) else None
            if baixo is not None and baixo[0][0] - max(c[3] for c in faixa) > _ABAIXO:
                baixo = None
            for j, (_y0, x0, x1, _y1, rot) in enumerate(faixa):
                chave = _chave_rotulo(rot)
                if not chave or chave in pares:
                    continue
                cands = [_a_direita(faixa, j, proibidos)]
                if baixo is not None:
                    melhor = segunda = 0.0
                    abaixo = None
                    for c in baixo:
                        ov = min(x1, c[2]) - max(x0, c[1])
                        if ov <= 0:
                            continue
                        if ov > melhor:
                            melhor, segunda, abaixo = ov, melhor, c[4]
                        elif ov > segunda:
                            segunda = ov
                    # empate é ambiguidade, e ambiguidade não vira dado
                    cands.append(abaixo if melhor > segunda else None)
                bons = [v for v in cands if v and len(v) <= _VALOR_MAX
                        and not _cara_de_rotulo(v, proibidos)]
                if bons:
                    pares[chave] = bons
    return pares


# ───────────────────────── O VALOR TEM QUE TER A CARA DO CAMPO ─────────────────
#
# Medido em 22/09/2026 na "Capa Frota LION MINING", uma cotação de frota do
# Bradesco: é um formulário de caixinhas, e o pareamento por desenho devolveu
# `email = "questionário de avaliação de risco"`, `endereco = "Bairro:"` e
# `modelo = "Diária:"`. Ler pela diagramação acerta muito e erra feio, e dado
# errado num campo é pior que campo vazio — vigência lida errada é alerta que não
# dispara, que é o pior defeito que a tela de Renovações pode ter.
#
# Então todo valor passa por uma prova de formato antes de virar campo. A prova é
# do CAMPO, não da origem: vale pro que veio do desenho e pro que veio do texto.

def _e_rotulo_solto(v: str) -> bool:
    """Termina em dois-pontos: é rótulo de outra caixa, não valor."""
    return v.strip().endswith(":")


def _palavras(v: str, minimo: int = 2) -> bool:
    return len([p for p in re.split(r"\s+", v.strip()) if len(p) > 1]) >= minimo


_FORMATOS = {
    "nome":            lambda v: _palavras(v) and 5 <= len(v) <= 80
                                 and re.fullmatch(r"[^\d:;|]+", v) is not None,
    "cpf":             lambda v: len(_digitos(v)) in (11, 14),
    "telefone":        lambda v: 10 <= len(_digitos(v)) <= 13,
    "email":           lambda v: re.fullmatch(r"[^@\s]+@[^@\s]+\.[a-zA-Z]{2,}", v.strip())
                                 is not None,
    "endereco":        lambda v: len(v.strip()) >= 8 and any(c.isalpha() for c in v),
    "placa":           lambda v: re.fullmatch(r"[A-Z]{3}-?\d[A-Z0-9]\d{2}", v.strip().upper())
                                 is not None,
    "chassi":          lambda v: re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", v.strip().upper())
                                 is not None,
    "modelo":          lambda v: _palavras(v) and 3 <= len(v) <= 90,
    "ano":             lambda v: re.search(r"\b(19|20)\d{2}\b", v) is not None,
    "cep_pernoite":    lambda v: len(_digitos(v)) == 8,
    "fipe":            lambda v: 5 <= len(_digitos(v)) <= 8,
    "numero_apolice":  lambda v: 4 <= len(_digitos(v)) <= 25,
    "numero_proposta": lambda v: 4 <= len(_digitos(v)) <= 25,
    "classe_bonus":    lambda v: re.fullmatch(r"\d{1,2}", v.strip()) is not None,
    "parcelas":        lambda v: re.search(r"\d", v) is not None,
    "dia_vencimento":  lambda v: re.search(r"\d", v) is not None,
}
#: dinheiro é a mesma prova pros três campos: tem que HAVER um número com
#: centavos. Sem isto a arbitragem entre o vizinho da direita e o de baixo não
#: funciona pra dinheiro, e "Adic. Franc (R$)" passava como valor do prêmio.
for _k in ("premio_centavos", "iof_centavos", "total_centavos", "franquia_centavos"):
    _FORMATOS[_k] = lambda v: _dinheiro(v) is not None


def _tem_a_cara(chave: str, bruto: str) -> bool:
    if bruto is None or _e_rotulo_solto(bruto):
        return False
    prova = _FORMATOS.get(chave)
    return True if prova is None else bool(prova(bruto))


def _do_desenho(pares: dict, rotulos: tuple[str, ...], chave: str = ""):
    """O primeiro candidato do desenho que TEM A CARA do campo pedido.

    `chave` é o campo de destino: sem ela, vale o primeiro candidato; com ela, a
    prova de formato arbitra entre o vizinho da direita e o de baixo.
    """
    for r in rotulos or ():
        for v in (pares or {}).get(_chave_rotulo(r)) or ():
            if not chave or _tem_a_cara(chave, v):
                return v, f"{r}\n{v}"
    return None, None


#: rótulo → variantes. A ordem importa: a primeira que casar vence, e as mais
#: específicas vêm antes ("Nome do segurado" antes de "Nome").
_SINONIMOS = {
    "numero_apolice":  ("Nº da Apólice", "Nº Apólice", "No. da Apólice", "Numero da Apolice",
                        "Número da Apólice", "Apólice nº", "Apólice"),
    "numero_proposta": ("Nº da Proposta", "Nº Proposta", "Número da Proposta",
                        "Numero da Proposta", "Proposta nº", "Proposta"),
    "classe_bonus":    ("Classe de bônus", "Classe Bônus", "Classe de Bonus", "Bônus"),
    "placa":           ("Placa",),
    "chassi":          ("Nº Chassi", "Chassi", "Nº do Chassi"),
    "modelo":          ("Marca/Modelo", "Veículo", "Marca / Modelo", "Modelo do veículo"),
    # a Tokio escreve o ano em "Ano modelo" e o carro em "Veículo"; a Zurich
    # escreve "Ano / Modelo: 2019", que é só o ano
    "ano":             ("Ano do modelo", "Ano/Modelo", "Ano Modelo", "Ano"),
    "fipe":            ("Código Tabela FIPE", "Cód. FIPE", "Código FIPE",
                        "Código na Tabela de Referência"),
    "cep_pernoite":    ("CEP Pernoite", "CEP do local onde o veículo pernoita",
                        "CEP de pernoite"),
    # "Melhor Dia Pgto." e "Quant. Parcelas" são da Capa de Frota do Bradesco
    "dia_vencimento":  ("Vencimento da 1ª parcela", "Dia de vencimento", "Vencimento",
                        "Melhor Dia Pgto."),
    "parcelas":        ("Nº de parcela", "Nº de parcelas", "Parcelas",
                        "Quantidade de parcelas", "Quant. Parcelas"),
}

#: A VIGÊNCIA, em tabela como os outros — o leitor por desenho precisa da lista.
#: "Vigência do Endosso" é da Yelum: num endosso o que vale é a vigência dele.
_ROTULOS_VIGENCIA = ("Vigência do Seguro", "Vigência do Endosso", "Período de vigência",
                     "Vigência", "Vigencia")
#: Zurich: "Início: 24hs do dia 31 de Março de 2026" / "Término: 24hs do dia ..."
_ROTULOS_INICIO = ("Vigência início 24h do dia", "Início de vigência",
                   "Início da vigência", "Data de início", "Início")
_ROTULOS_FIM = ("Término 24h do dia", "Fim de vigência", "Fim da vigência",
                "Término da vigência", "Fim de Vigência", "Válida até",
                "Data de término", "Término")


def _duas_datas(txt: str | None):
    """(início, fim) quando o trecho traz duas datas. None quando não traz.

    Serve tanto pro "das 24h de 13/08/2026 às 24h de 13/08/2027" da Porto quanto
    pro "19/09/2026 - 19/09/2027" da Tokio.
    """
    if not txt:
        return None
    ds = re.findall(r"\d{2}/\d{2}/\d{4}", txt)
    if len(ds) < 2:
        return None
    a, b = _data(ds[0]), _data(ds[1])
    return (a, b) if a and b and b > a else None


#: os do BLOCO do segurado — lidos só depois da âncora (`_bloco_do_segurado`)
_SINONIMOS_SEGURADO = {
    # "Nome do(a) Segurado(a)" é da Yelum e "Nome Completo" da Zurich — as duas
    # entraram em 22/09/2026, cada uma trazida por um PDF de verdade.
    "nome":     ("Nome do(a) Segurado(a)", "Nome do segurado(a)", "Nome do Segurado",
                 "Nome/Razão Social", "Razão Social", "Nome completo",
                 "Proponente", "Segurado", "Nome"),
    "cpf":      ("CPF/CNPJ", "CPF / CNPJ", "CNPJ/CPF", "CPF", "CNPJ"),
    "telefone": ("Telefone celular", "Celular", "Tel. celular", "Telefone", "Tel"),
    "email":    ("E-mail", "Email", "E-Mail"),
    "endereco": ("Endereço", "Endereco", "Logradouro"),
}

#: dinheiro: o rótulo pode vir com dois-pontos OU com o valor na linha de baixo
_SINONIMOS_DINHEIRO = {
    "premio_centavos": ("Prêmio líquido", "Prêmio Líquido", "Preço Líquido",
                        "Premio liquido", "Prêmio Net"),
    "iof_centavos":    ("IOF", "I.O.F."),
    # "Custo Apólice" e "Adic. Franc" são da Yelum: não entram em nenhum campo
    # hoje, e ficam aqui escritas pra não serem confundidas com o prêmio
    "total_centavos":  ("Prêmio total", "Prêmio Total", "Preço Total", "Total do Seguro",
                        "Prêmio Total do Seguro", "Valor Total"),
}

#: RÓTULO CURTO SÓ VALE NO DESENHO, e esta separação custou dois números errados.
#:
#: No desenho, o rótulo é uma CAIXA INTEIRA: "TOTAL:" é a caixa "TOTAL:", e casar
#: com ela é casar com aquele campo. No texto achatado o mesmo "Total" é um
#: PREFIXO que pega qualquer linha que comece assim.
#:
#: Medido em 23/09/2026, quando eu tinha posto os dois na mesma lista:
#:   * Capa de Frota (Bradesco): o texto sai fora de ordem e "TOTAL:" alcançou o
#:     número do IOF — total virou R$ 1.095,07 em vez de R$ 15.933,46;
#:   * Tokio Marine: "Prêmio Líquido" é também o TÍTULO da primeira cobertura, e
#:     o prêmio virou R$ 2.605,86, o de uma cobertura só, em vez dos R$ 3.276,64
#:     de "Prêmio Líquido total".
_DINHEIRO_DESENHO = {
    "premio_centavos": ("Líquido",),      # "Líquido(A+B+C)" da Capa de Frota
    "total_centavos":  ("Total",),        # "TOTAL:" da Capa de Frota
}


def _primeiro_rotulo(texto: str, rotulos: tuple[str, ...]):
    """O primeiro rótulo da lista que existir no papel. (valor, trecho) ou (None, None)."""
    for r in rotulos:
        v, tr = _rotulo(texto, r)
        if v is not None:
            return v, tr
    return None, None


def _dinheiro_rotulado(texto: str, rotulos: tuple[str, ...], solta: bool = True):
    """O valor em dinheiro depois do rótulo, com dois-pontos OU na linha de baixo.

    As duas formas aparecem nos três layouts medidos: a Mapfre escreve
    "Prêmio líquido: 2.613,01" e a Porto escreve "Prêmio líquido\nR$ 4.567,46".

    `solta=False` exige o valor na MESMA linha do rótulo. É o modo sem
    ambiguidade, pra usar antes do desenho: num formulário cujo texto sai
    embaralhado, "a linha de baixo" não é vizinha de nada.
    """
    for r in rotulos:
        # `[^\n]{0,60}?` porque o rótulo pode carregar um parêntese antes do valor:
        # a Allianz escreve "Preço Total (IOF + Juros inclusos)\nR$ 4.088,57". Sem
        # isso o genérico achava o líquido e perdia o total — e aí o IOF, que é
        # derivado dos dois, também sumia.
        #
        # E `^[ \t]*`, o rótulo no COMEÇO DA LINHA, por causa do mesmo exemplo pelo
        # avesso: a palavra "IOF" mora DENTRO daquele parêntese, e sem a âncora o
        # rótulo "IOF" casava ali e devolvia o valor do TOTAL como se fosse imposto.
        # Nos três papéis medidos todo rótulo de dinheiro abre a linha.
        # O SÍMBOLO PODE MORAR NUMA LINHA SÓ DELE. A Zurich quebra o dinheiro em
        # TRÊS linhas — "Prêmio Total\nR$\n 2.772,28" —, e a regra de uma quebra
        # só perdia o prêmio, o IOF e o total de uma apólice inteira. Duas
        # quebras no máximo, e entre elas só espaço ou "R$": o teto é o que
        # impede o rótulo de alcançar um número de outro bloco.
        vao = (r"[ \t]*:?[ \t]*\n?[ \t]*(?:R\$)?[ \t]*\n?[ \t]*R?\$? ?" if solta
               else r"[ \t]*:[ \t]*R?\$? ?")
        m = re.search(r"^[ \t]*" + re.escape(r) + r"[^\n]{0,60}?" + vao
                      + r"(\d{1,3}(?:\.\d{3})*,\d{2})", texto, re.M | re.I)
        if m:
            return _dinheiro(m.group(1)), m.group(0).strip()
    return None, None


def _parcelas_da_tabela(texto: str):
    """Os valores das parcelas, nas DUAS ordens que os papéis usam.

    Mapfre: número / data / valor. Porto: número / valor / data. O mesmo dado em
    ordem trocada — e ler na ordem errada devolve data no lugar de dinheiro, que a
    checagem da soma denuncia mas o formulário já mostrou.
    """
    num_data_valor = re.findall(
        r"\n(\d{1,2})\n(\d{2}/\d{2}/\d{4})\n\s*R?\$? ?(\d{1,3}(?:\.\d{3})*,\d{2})", texto)
    num_valor_data = re.findall(
        r"\n(\d{1,2})\n\s*R?\$? ?(\d{1,3}(?:\.\d{3})*,\d{2})\n(\d{2}/\d{2}/\d{4})", texto)
    if len(num_data_valor) >= len(num_valor_data) and num_data_valor:
        return [(int(n), _dinheiro(v), d) for n, d, v in num_data_valor]
    if num_valor_data:
        return [(int(n), _dinheiro(v), d) for n, v, d in num_valor_data]
    return []


#: O que faz uma leitura sem layout valer a pena. Não é a CONTAGEM de campos: o
#: genérico sempre grava `ramo`, e um papel de que só saiu "auto" não foi lido. É
#: ter achado ao menos uma coisa que IDENTIFICA a apólice.
_IDENTIFICAM = ("nome", "vigencia_fim", "numero_apolice", "numero_proposta",
                "placa", "chassi", "premio_centavos")


def _achou_o_bastante(L: Leitura) -> bool:
    return any(L.campos.get(k) for k in _IDENTIFICAM)


def _generico(texto: str, L: Leitura, pares: dict | None = None) -> None:
    """Lê o que estiver ROTULADO, seja qual for a seguradora.

    Substitui o antigo caminho genérico, que usava os rótulos da Allianz e por isso
    só achava campo em papel que falasse Allianzês.
    """
    c, t = L.campos, L.trechos
    c["ramo"] = "auto"

    def pega(chave, rotulos, onde=None, conv=lambda v: v, desenho=True):
        """O texto primeiro; o DESENHO só quando o texto não souber responder.

        Nessa ordem porque as duas formas não valem o mesmo: "CPF: 705…" é sem
        ambiguidade nenhuma, e "a caixa de baixo" é uma leitura da diagramação.
        Onde os dois sabem, vence o rótulo com dois-pontos."""
        v, tr = _primeiro_rotulo(onde if onde is not None else texto, rotulos)
        if not _tem_a_cara(chave, v):
            v, tr = (None, None)
        if v is None and desenho:
            v, tr = _do_desenho(pares, rotulos, chave)
        val = conv(v) if v is not None else None
        if val is None:
            L.nao_achou.append(chave)
            return None
        c[chave], t[chave] = val, tr
        return val

    pega("numero_apolice", _SINONIMOS["numero_apolice"], conv=lambda v: _digitos(v) or None)
    pega("numero_proposta", _SINONIMOS["numero_proposta"], conv=lambda v: _digitos(v) or None)
    pega("classe_bonus", _SINONIMOS["classe_bonus"], conv=lambda v: v.strip()[:4] or None)

    # A VIGÊNCIA, nas duas formas: uma linha com as duas datas (Allianz, Porto,
    # Yelum, Tokio) ou dois rótulos separados (Mapfre, Zurich).
    vig, tr = _primeiro_rotulo(texto, _ROTULOS_VIGENCIA)
    if vig is None:
        vig, tr = _do_desenho(pares, _ROTULOS_VIGENCIA)
    datas = _duas_datas(vig)
    if datas:
        c["vigencia_inicio"], c["vigencia_fim"] = datas
        t["vigencia_fim"] = tr
    else:
        vi = (_primeiro_rotulo(texto, _ROTULOS_INICIO)[0]
              or _do_desenho(pares, _ROTULOS_INICIO)[0])
        vf, trf = _primeiro_rotulo(texto, _ROTULOS_FIM)
        if vf is None:
            vf, trf = _do_desenho(pares, _ROTULOS_FIM)
        if _data(vi):
            c["vigencia_inicio"] = _data(vi)
        if _data(vf):
            c["vigencia_fim"], t["vigencia_fim"] = _data(vf), trf
    if "vigencia_fim" not in c:
        L.nao_achou.append("vigencia_fim")

    # O SEGURADO, só depois da âncora. Sem âncora não se lê — ver
    # `_bloco_do_segurado`, que também FECHA o bloco no próximo cabeçalho.
    DO_SEGURADO = ("nome", "cpf", "telefone", "email", "endereco")
    seg, _ancora = _bloco_do_segurado(texto)
    if seg is None and not pares:
        L.nao_achou.extend(DO_SEGURADO)
        L.avisos.append("Não achei onde começam os dados do segurado neste papel — "
                        "deixei nome, CPF, telefone e endereço em branco de propósito. "
                        "Preencha olhando o PDF.")
    else:
        # SEM ÂNCORA NO TEXTO, o desenho ainda responde: nos papéis de duas faixas
        # a âncora ("DADOS DO(A) SEGURADO(A)") existe e é o rótulo de CIMA — o que
        # o achatamento desmanchou foi o bloco, não o título dele. `onde=seg or ''`
        # mantém a regra de sempre: no TEXTO, nada de segurado se lê fora do bloco.
        pega("nome", _SINONIMOS_SEGURADO["nome"], seg or "",
             lambda v: re.sub(r"\s+", " ", v).strip().upper() or None)
        pega("cpf", _SINONIMOS_SEGURADO["cpf"], seg or "", lambda v: _digitos(v) or None)
        pega("telefone", _SINONIMOS_SEGURADO["telefone"], seg or "",
             lambda v: _digitos(v) or None)
        pega("email", _SINONIMOS_SEGURADO["email"], seg or "",
             lambda v: v.strip().lower() or None)
        pega("endereco", _SINONIMOS_SEGURADO["endereco"], seg or "")
        # a MESMA guarda de sempre: nada que seja da própria corretora é o cliente
        _conferir_o_segurado(L, DO_SEGURADO)

    # O VEÍCULO. Sem bloco próprio aqui: os rótulos de carro (placa, chassi) não se
    # repetem em outros blocos do papel, ao contrário de "Nome" e "CPF".
    pega("placa", _SINONIMOS["placa"], conv=lambda v: v.strip().upper().replace("-", "") or None)
    pega("chassi", _SINONIMOS["chassi"], conv=lambda v: v.strip().upper() or None)
    pega("modelo", _SINONIMOS["modelo"], conv=lambda v: re.sub(r"\s+", " ", v).strip() or None)
    pega("ano", _SINONIMOS["ano"], conv=lambda v: _digitos(v)[:4] or None)
    pega("fipe", _SINONIMOS["fipe"])
    pega("cep_pernoite", _SINONIMOS["cep_pernoite"], conv=lambda v: _digitos(v) or None)

    for chave, rotulos in _SINONIMOS_DINHEIRO.items():
        # O TEXTO PRIMEIRO, com os rótulos específicos; o DESENHO depois, com os
        # específicos MAIS os curtos de `_DINHEIRO_DESENHO`. Ver o comentário
        # daquela tabela: rótulo curto é caixa inteira no desenho e prefixo no
        # texto, e tratá-los igual trocou o total do Bradesco pelo IOF e o prêmio
        # da Tokio pelo de uma cobertura só.
        v, tr = _dinheiro_rotulado(texto, rotulos)
        if v is None:
            bruto, tr = _do_desenho(
                pares, rotulos + _DINHEIRO_DESENHO.get(chave, ()), chave)
            v = _dinheiro(bruto)
        if v is None:
            L.nao_achou.append(chave)
        else:
            c[chave], t[chave] = v, tr
    # o IOF pode ser DERIVADO quando o papel não o traz em linha própria
    if c.get("iof_centavos") is None and c.get("premio_centavos") is not None \
            and c.get("total_centavos") is not None:
        c["iof_centavos"] = c["total_centavos"] - c["premio_centavos"]

    par = _parcelas_da_tabela(texto)
    if par:
        # A 1ª PARCELA VEM ROTULADA À PARTE, fora da tabela — é assim na Mapfre
        # ("Valor da 1ª parcela: 233,82", e a tabela começa na 02) e na Porto. Sem
        # ela a soma não fecha com o total, e o `dia_vencimento` sai da parcela 2:
        # dia 15 no lugar de 16, medido no PDF da Mapfre em 22/09/2026.
        v1, _tr1 = _primeiro_rotulo(texto, ("Valor da 1ª parcela", "Valor da 1a parcela",
                                            "Valor da primeira parcela"))
        d1, tr1 = _primeiro_rotulo(texto, ("Vencimento da 1ª parcela",
                                           "Vencimento da 1a parcela"))
        valores = [v for _n, v, _d in par]
        if _dinheiro(v1) is not None and not any(n == 1 for n, _v, _d in par):
            valores.insert(0, _dinheiro(v1))
        c["parcelas"] = max(max(n for n, _v, _d in par), len(valores))
        c["parcelas_centavos"] = valores
        if _data(d1):
            c["dia_vencimento"], t["dia_vencimento"] = _data(d1).day, tr1
        else:
            primeira = min(par)
            c["dia_vencimento"] = int(primeira[2][:2])
            t["dia_vencimento"] = f"1ª parcela vence {primeira[2]}"
    else:
        pega("parcelas", _SINONIMOS["parcelas"],
             conv=lambda v: int(_digitos(v)) if _digitos(v) else None)
        pega("dia_vencimento", _SINONIMOS["dia_vencimento"],
             conv=lambda v: int(_digitos(v)[:2]) if _digitos(v) else None)

    c["situacao"] = "vigente" if c.get("numero_apolice") else "proposta"


_LAYOUTS = (
    # (nome, teste de reconhecimento, leitor)
    # `Nº da Proposta` OU `Nº da Apólice`: o teste exigia só o primeiro, e por isso
    # a APÓLICE EMITIDA da Allianz (que traz o segundo) caía no genérico — visto em
    # 22/09/2026, numa Allianz que eu já sabia ler.
    ("Allianz", lambda t: bool(re.search(r"\bALLIANZ\b", t, re.I))
                          and ("Nº da Proposta" in t or "Nº da Apólice" in t), _allianz),
    # a Mapfre escreve o próprio nome no bloco DADOS DA SEGURADORA; os dois
    # rótulos juntos separam a apólice dela de um e-mail que só a mencione.
    ("Mapfre", lambda t: bool(re.search(r"\bMAPFRE\b", t, re.I))
                         and "Nº Apólice" in t and "DADOS DO SEGURADO" in t, _mapfre),
    # a Porto se identifica no rodapé da capa; os dois rótulos juntos separam a
    # apólice dela de um e-mail que só a mencione.
    ("Porto Seguro", lambda t: bool(re.search(r"PORTO\s*SEGURO", t, re.I))
                               and "Nome do segurado(a)" in t
                               and "Dados da sua apólice" in t, _porto),
)


# ------------------------------------------------------------------- checagens

def _checar(L: Leitura) -> None:
    """As contas que fecham sozinhas — e são as ÚNICAS que este leitor faz.

    A soma das coberturas ficou de fora de propósito: é a que errou três vezes.
    As que ficam usam só campos rotulados de FONTES DIFERENTES do papel — é isso
    que faz uma checagem ser checagem, e não a mesma conta escrita duas vezes.
    """
    c = L.campos
    # NÃO existe checagem "líquido + IOF = total". O papel não traz o IOF em linha
    # própria — traz "Preço Total (IOF + Juros inclusos)" —, então `iof_centavos` é
    # DERIVADO (total − líquido) e a soma fecharia sempre, por construção. O mockup
    # de 18/09 listou essa conta como "checagem que fecha sozinha"; como observação
    # era verdade, como checagem era circular, e o teste que tentou fazê-la falhar
    # foi quem denunciou. Fica só a que tem fonte independente: as parcelas.
    if c.get("parcelas_centavos") and c.get("total_centavos") is not None:
        soma = sum(c["parcelas_centavos"])
        ok = soma == c["total_centavos"]
        L.checagens.append(("soma das parcelas = total", ok,
                            f"{len(c['parcelas_centavos'])} parcelas = {soma/100:,.2f}"))
    elif c.get("parcelas_centavos") and c.get("premio_centavos") is not None:
        # SEM TOTAL a checagem acima não roda, e um prêmio absurdo passava calado.
        # Foi o que aconteceu no endosso da Azul (id 6 da conta 37, 22/09/2026):
        # prêmio lido R$ 16,91 com quatro parcelas de R$ 284,25. Nenhuma conta
        # batia porque nenhuma conta era feita. Aqui a comparação é grosseira de
        # propósito — num endosso o prêmio adicional PODE ser bem menor que as
        # parcelas da apólice de origem, e o que se quer pegar é a ordem de
        # grandeza absurda, não o centavo.
        soma = sum(c["parcelas_centavos"])
        ok = soma <= max(1, c["premio_centavos"]) * 3
        L.checagens.append(("as parcelas cabem no prêmio", ok,
                            f"{len(c['parcelas_centavos'])} parcelas = {soma/100:,.2f}"
                            f" · prêmio = {c['premio_centavos']/100:,.2f}"))
        if not ok:
            L.avisos.append(
                f"O prêmio que eu li ({c['premio_centavos']/100:,.2f}) não cabe nas "
                f"parcelas ({soma/100:,.2f}). Um dos dois está errado — confira os "
                "dois no PDF antes de salvar.")
    if c.get("cpf"):
        # CNPJ NÃO SE VALIDA COM REGRA DE CPF. O campo guarda os dois (o segurado
        # pode ser empresa: a Capa de Frota do Bradesco é de uma mineradora), e
        # rodar `valida_cpf` num CNPJ reprovava um documento correto — o leitor
        # dizendo que o dado está errado quando o errado era a conferência.
        from finance import validadoc
        e_cnpj = len(c["cpf"]) == 14
        nome_doc = "CNPJ" if e_cnpj else "CPF"
        ok = validadoc.valida_cnpj(c["cpf"]) if e_cnpj else validadoc.valida_cpf(c["cpf"])
        L.checagens.append((f"{nome_doc}: dígito verificador", ok,
                            c["cpf"][:3] + "…" + c["cpf"][-2:]))
        if not ok:
            L.avisos.append(f"O {nome_doc} lido não passa no dígito verificador — "
                            "confira antes de salvar.")
    vi, vf = c.get("vigencia_inicio"), c.get("vigencia_fim")
    if vi and vf:
        ok = vf > vi
        L.checagens.append(("vigência: fim depois do início", ok, f"{vi:%d/%m/%Y} → {vf:%d/%m/%Y}"))


# ---------------------------------------------------------------------- ler

def texto_do_pdf(conteudo: bytes) -> tuple[str, int]:
    """(texto, páginas). Levanta ValueError se não for PDF legível.

    Separado pra o teste poder alimentar `ler_texto` direto — o que se testa é a
    LEITURA dos rótulos, não o pymupdf.
    """
    if len(conteudo) > TETO_BYTES:
        raise ValueError("Arquivo grande demais pra ser uma apólice (limite 16 MB).")
    try:
        import pymupdf
        doc = pymupdf.open(stream=conteudo, filetype="pdf")
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Não consegui abrir como PDF: {type(e).__name__}.")
    if doc.page_count > TETO_PAGINAS:
        raise ValueError(f"PDF com {doc.page_count} páginas não parece uma apólice.")
    texto = "\n".join(p.get_text() for p in doc)
    if len(texto.strip()) < 200:
        raise ValueError("O PDF não tem texto — parece imagem escaneada. "
                         "Este leitor não faz OCR; cadastre à mão.")
    return texto, doc.page_count


def texto_e_desenho(conteudo: bytes) -> tuple[str, int, dict]:
    """(texto, páginas, pares do desenho). O que `ler` usa.

    Abre o PDF UMA vez pras duas leituras. O desenho é best-effort: PDF de que o
    pymupdf não consegue tirar coordenada continua sendo lido pelo texto, que é
    como era antes dele existir.
    """
    if len(conteudo) > TETO_BYTES:
        raise ValueError("Arquivo grande demais pra ser uma apólice (limite 16 MB).")
    try:
        import pymupdf
        doc = pymupdf.open(stream=conteudo, filetype="pdf")
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Não consegui abrir como PDF: {type(e).__name__}.")
    if doc.page_count > TETO_PAGINAS:
        raise ValueError(f"PDF com {doc.page_count} páginas não parece uma apólice.")
    texto = "\n".join(p.get_text() for p in doc)
    if len(texto.strip()) < 200:
        raise ValueError("O PDF não tem texto — parece imagem escaneada. "
                         "Este leitor não faz OCR; cadastre à mão.")
    try:
        pares = pares_do_desenho(doc)
    except Exception:  # noqa: BLE001
        pares = {}
    return texto, doc.page_count, pares


# ─────────────────── QUE PAPEL É ESTE? (23/09/2026) ───────────────────
#
# Pergunta do dono, olhando os três que sobraram na fila: "veja também se é só
# proposta". É, e é mais que isso — dos três, NENHUM é a apólice emitida de um
# carro:
#
#   HDI      "PROPOSTA Anacelia Viana Lima.pdf"        uma PROPOSTA
#   Azul     "APÓLICE ENDOSSO HAVAL Manoel..."         um ENDOSSO
#   Bradesco "Capa Frota LION MINING..."               uma COTAÇÃO de frota
#
# E isso muda o que a tela deve fazer com cada um, não só o que ela escreve:
#
#   * PROPOSTA ainda não é contrato. A vigência dela é pretendida, e o número de
#     apólice não existe — por isso esses papéis "faltam campo": o campo não está
#     no papel.
#   * ENDOSSO altera uma apólice que JÁ ESTÁ na carteira. Cadastrar cria uma
#     SEGUNDA linha pro mesmo carro, e duplicar a apólice de um cliente é perder
#     informação do mesmo jeito que apagar (regra 0).
#   * COTAÇÃO não é seguro nenhum. A do Bradesco diz, no rodapé dela: "Este
#     demonstrativo de cotação não implica na aceitação desta frota".
#
# O papel se identifica no começo. Medido nos oito PDFs que a Liberal mandou,
# esta ordem separa os quatro tipos sem um erro.
_CABECALHO = 2500

_MARCAS_TIPO = (
    ("cotacao", (r"demonstrativo de cota[çc][ãa]o",
                 r"^[ \t]*RESULTADO DA FROTA[ \t]*$",
                 r"n[ãa]o implica na aceita[çc][ãa]o")),
    # o endosso vem ANTES da apólice: todo endosso traz o número da apólice que
    # ele altera, e sem esta ordem ele se apresentaria como aquela apólice
    ("endosso", (r"^[ \t]*DADOS DO ENDOSSO[ \t]*$",
                 r"Vig[êe]ncia do Endosso",
                 r"^[ \t]*Endosso[ \t]+[-–]",
                 r"^[ \t]*Opera[çc][ãa]o[ \t]*:[ \t]*Endosso")),
    # `Endosso: 0000000` NÃO entra aqui: é o campo vazio que a Zurich e a Mapfre
    # imprimem numa apólice sem endosso nenhum
    ("apolice", (r"^[ \t]*Opera[çc][ãa]o[ \t]*:[ \t]*Emiss[ãa]o da ap[óo]lice",
                 r"Sua ap[óo]lice chegou",
                 r"^[ \t]*AP[ÓO]LICE DE SEGURO",
                 r"Esta [ée] sua ap[óo]lice",
                 r"^[ \t]*N[ºo°][ \t]*Ap[óo]lice[ \t]*:[ \t]*[0-9]*[1-9]")),
    ("proposta", (r"^[ \t]*PROPOSTA\b",
                  r"^[ \t]*Proposta[ \t]+[A-ZÀ-Ý]",
                  r"Aceita[çc][ãa]o sujeita a an[áa]lise",
                  r"dados da sua proposta antes de contratar")),
)

#: como cada tipo se chama na tela, e o que ele significa pra quem vai conferir
TIPOS = {
    "apolice":  ("apólice", ""),
    "proposta": ("proposta", "Isto é uma PROPOSTA, não a apólice emitida: a "
                             "vigência é a pretendida e o número da apólice ainda "
                             "não existe. Confira quando a apólice sair."),
    "endosso":  ("endosso", "Isto é um ENDOSSO — ele ALTERA uma apólice que já "
                            "existe. Se essa apólice já está na carteira, cadastrar "
                            "aqui cria uma segunda linha pro mesmo carro; o certo é "
                            "editar a que já está lá."),
    "cotacao":  ("cotação", "Isto é uma COTAÇÃO, não um seguro contratado. Não há "
                            "apólice, vigência nem segurado definitivos pra guardar."),
}


def tipo_do_documento(texto: str) -> str:
    """'apolice' | 'proposta' | 'endosso' | 'cotacao'. O padrão é 'apolice'."""
    cab = texto[:_CABECALHO]
    for tipo, marcas in _MARCAS_TIPO:
        for m in marcas:
            if re.search(m, cab, re.M | re.I):
                return tipo
    return "apolice"


#: A VERSÃO DO LEITOR. Sobe de um toda vez que o leitor aprende alguma coisa —
#: layout novo, sinônimo novo, conserto de âncora. Fica carimbada em
#: `apolice_lida.lido->>'versao'`, e é o que deixa o painel saber que uma leitura
#: guardada é mais velha que o leitor de hoje e refazê-la sozinho.
#:
#: Nasceu em 22/09/2026, do mesmo problema duas vezes: sete apólices lidas de
#: manhã, o leitor genérico no ar às 12h45, e a fila continuando a mostrar a
#: leitura das 8h porque a releitura dependia de alguém achar e clicar um botão.
#: Ferramenta que melhora sozinha não pode pedir licença pra aplicar a melhora.
#:
#:  1 — só a Allianz, com o genérico falando Allianzês
#:  2 — Mapfre e Porto, bloco do segurado limitado, guarda da própria corretora
#:  3 — o genérico por sinônimos (#804)
#:  4 — o leitor por DESENHO (rótulo em cima do valor), data por extenso e rótulo
#:      sem caixa fixa: Yelum, Tokio Marine e Zurich saíam com três campos
#:  5 — o tipo do papel (apólice, proposta, endosso, cotação) e a conferência das
#:      parcelas contra o prêmio quando não há total
#:  6 — o desenho olha À DIREITA na mesma linha, não só embaixo; o candidato é
#:      escolhido pela prova de formato; CNPJ validado como CNPJ. A Capa de Frota
#:      do Bradesco saía com 3 campos e sai com 15
VERSAO = 6


def ler_texto(texto: str, paginas: int = 0, proibidos: tuple[str, ...] = (),
              pares: dict | None = None) -> Leitura:
    """`proibidos`: nome, documento e e-mail da PRÓPRIA corretora e dos membros
    dela. Nada disso pode ser o segurado, e é a trava que não depende de conhecer
    o layout — ver `_e_a_propria_casa`.

    `pares`: o que a POSIÇÃO na página disse (`pares_do_desenho`). Opcional — sem
    ele o leitor se comporta exatamente como antes do desenho existir, que é o que
    os testes que alimentam texto puro exercitam."""
    L = Leitura(paginas=paginas)
    L.proibidos = tuple(proibidos)
    for nome, reconhece, leitor in _LAYOUTS:
        if reconhece(texto):
            L.seguradora, L.reconhecida, L.como = nome, True, "layout"
            leitor(texto, L)
            break
    if not L.reconhecida:
        # layout desconhecido: o GENÉRICO, que lê por sinônimos em vez de falar
        # Allianzês. Era `_allianz` aqui, e por isso seguradora desconhecida vinha
        # quase vazia — sete delas numa manhã em 22/09/2026.
        _generico(texto, L, pares)
        L.campos.pop("seguradora", None)
        # o NOME sai mesmo sem o layout, e é o que deixa o aviso reconhecível
        nome, trecho = nomear_seguradora(texto)
        if nome:
            L.campos["seguradora"], L.trechos["seguradora"] = nome, trecho
        # O AVISO DEPENDE DO QUE SAIU, não de conhecer o layout. Ele dizia "não
        # reconheci o layout desta seguradora" mesmo quando o genérico tinha tirado
        # treze campos do papel — a pessoa lia "não leu" e ia digitar tudo à mão.
        L.como = "rotulos" if _achou_o_bastante(L) else "nada"
        quem = nome or "esta seguradora"
        if L.como == "rotulos":
            L.avisos.append(f"Li este papel pelos rótulos dele, sem um layout próprio "
                            f"da {quem}. O que achei está preenchido; confira, "
                            "principalmente os valores.")
        else:
            L.avisos.append(f"Não achei rótulo nenhum que eu conheça neste papel "
                            f"({quem}). Cadastre à mão, e me manda o PDF que eu "
                            "aprendo os rótulos dele.")
    # QUE PAPEL É ESTE, e o que isso muda pra quem vai conferir. Vale pros dois
    # caminhos: layout reconhecido também pode ser proposta (a Allianz da Maria
    # de Fátima é) ou endosso.
    L.tipo = tipo_do_documento(texto)
    _rotulo_tipo, recado = TIPOS.get(L.tipo, ("", ""))
    if recado:
        L.avisos.insert(0, recado)
    # proposta e cotação ainda não são contrato: nenhuma das duas entra na
    # carteira como "vigente"
    if L.tipo in ("proposta", "cotacao"):
        L.campos["situacao"] = "proposta"

    # LAYOUT RECONHECIDO JÁ É A PROVA. Sem esta linha, a proposta Allianz enxuta
    # (que não escreve SUSEP nem a palavra "apólice", por ser proposta) reprovava
    # nas marcas e caía no caixa — o defeito que este arquivo existe pra evitar,
    # com o único layout que eu sei ler.
    L.e_apolice = L.reconhecida or e_apolice(texto)
    _checar(L)
    return L


def ler(conteudo: bytes, proibidos: tuple[str, ...] = ()) -> Leitura:
    texto, paginas, pares = texto_e_desenho(conteudo)
    return ler_texto(texto, paginas, proibidos, pares)


def para_formulario(L: Leitura) -> dict:
    """Os campos no formato que `web/painel_apolices.salvar_apolice` já recebe.

    É a ponte com o cadastro manual: a conferência pré-preenche o MESMO formulário,
    e salvar passa pelo MESMO caminho. Não existe um segundo jeito de gravar apólice.
    """
    c = L.campos
    cent = lambda k: (f"{c[k]/100:.2f}".replace(".", ",") if c.get(k) is not None else "")
    return {
        "seguradora": c.get("seguradora") or "", "ramo": c.get("ramo") or "auto",
        "situacao": c.get("situacao") or "proposta",
        "numero_proposta": c.get("numero_proposta") or "",
        "numero_apolice": c.get("numero_apolice") or "",
        "vigencia_inicio": c["vigencia_inicio"].isoformat() if c.get("vigencia_inicio") else "",
        "vigencia_fim": c["vigencia_fim"].isoformat() if c.get("vigencia_fim") else "",
        "premio": cent("premio_centavos"), "iof": cent("iof_centavos"),
        "franquia": cent("franquia_centavos"),
        "classe_bonus": c.get("classe_bonus") or "",
        "parcelas": str(c["parcelas"]) if c.get("parcelas") else "",
        "dia_vencimento": str(c["dia_vencimento"]) if c.get("dia_vencimento") else "",
        "placa": c.get("placa") or "", "modelo": c.get("modelo") or "",
        "ano": c.get("ano") or "", "chassi": c.get("chassi") or "",
        # o segurado, pra achar/criar o cliente
        "nome": c.get("nome") or "", "cpf": c.get("cpf") or "",
        "telefone": c.get("telefone") or "", "email": c.get("email") or "",
        "endereco": c.get("endereco") or "",
    }


def resumo_para_guardar(L: Leitura) -> dict:
    """O que vai em `apolices.pdf_lido`: auditável, sem o texto inteiro."""
    def s(v):
        if isinstance(v, (date, datetime)):
            return v.isoformat()
        if isinstance(v, Decimal):
            return str(v)
        return v
    return {
        "seguradora": L.seguradora or L.campos.get("seguradora"),
        "reconhecida": L.reconhecida, "e_apolice": L.e_apolice, "paginas": L.paginas,
        # o carimbo do leitor que produziu isto — ver `VERSAO`
        "versao": VERSAO, "como": L.como, "tipo": L.tipo,
        "campos": {k: s(v) for k, v in L.campos.items()},
        "checagens": [{"nome": n, "ok": ok, "detalhe": d} for n, ok, d in L.checagens],
        "nao_achou": L.nao_achou, "avisos": L.avisos,
    }
