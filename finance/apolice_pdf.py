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


def _data(txt: str | None) -> date | None:
    if not txt:
        return None
    m = re.search(r"(\d{2})/(\d{2})/(\d{4})", txt)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(0), "%d/%m/%Y").date()
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
    m = re.search(re.escape(rotulo) + r"[ \t]*:[ \t]*(" + ate + ")", texto)
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
_ANCORAS_SEGURADO = (
    r"SUAS\s+INFORMA[ÇC][ÕO]ES",
    r"DADOS\s+D[OA]\s+SEGURAD[OA](?!RA)",
    r"DADOS\s+D[OA]\s+CLIENTE",
    r"IDENTIFICA[ÇC][ÃA]O\s+D[OA]\s+SEGURAD[OA](?!RA)",
    r"\bSEGURAD[OA]\b(?!RA)",          # o rótulo solto, nunca "SEGURADORA"
)


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
        m = re.search(padrao, texto, re.I)
        if m:
            achados.append((m.start(), m.end(), m.group(0)))
    if not achados:
        return None, None
    ini, fim_ancora, ancora = min(achados)
    # o próximo cabeçalho DEPOIS da âncora fecha o bloco
    prox = _CABECALHO.search(texto, fim_ancora)
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


_LAYOUTS = (
    # (nome, teste de reconhecimento, leitor)
    ("Allianz", lambda t: bool(re.search(r"\bALLIANZ\b", t, re.I)) and "Nº da Proposta" in t, _allianz),
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
    if c.get("cpf"):
        from finance import validadoc
        ok = validadoc.valida_cpf(c["cpf"])
        L.checagens.append(("CPF: dígito verificador", ok, c["cpf"][:3] + "…" + c["cpf"][-2:]))
        if not ok:
            L.avisos.append("O CPF lido não passa no dígito verificador — confira antes de salvar.")
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


def ler_texto(texto: str, paginas: int = 0, proibidos: tuple[str, ...] = ()) -> Leitura:
    """`proibidos`: nome, documento e e-mail da PRÓPRIA corretora e dos membros
    dela. Nada disso pode ser o segurado, e é a trava que não depende de conhecer
    o layout — ver `_e_a_propria_casa`."""
    L = Leitura(paginas=paginas)
    L.proibidos = tuple(proibidos)
    for nome, reconhece, leitor in _LAYOUTS:
        if reconhece(texto):
            L.seguradora, L.reconhecida = nome, True
            leitor(texto, L)
            break
    if not L.reconhecida:
        # layout desconhecido: tenta o genérico, e AVISA
        _allianz(texto, L)           # os rótulos genéricos são os mesmos por enquanto
        L.campos.pop("seguradora", None)
        # o NOME sai mesmo sem o layout, e é o que deixa o aviso reconhecível
        nome, trecho = nomear_seguradora(texto)
        if nome:
            L.campos["seguradora"], L.trechos["seguradora"] = nome, trecho
            L.avisos.append(f"Achei {nome} no papel, mas não conheço o layout dela — "
                            "o que achei está preenchido, o resto ficou em branco. "
                            "Confira tudo.")
        else:
            L.avisos.append("Não reconheci o layout desta seguradora — o que achei está "
                            "preenchido, o resto ficou em branco. Confira tudo.")
    # LAYOUT RECONHECIDO JÁ É A PROVA. Sem esta linha, a proposta Allianz enxuta
    # (que não escreve SUSEP nem a palavra "apólice", por ser proposta) reprovava
    # nas marcas e caía no caixa — o defeito que este arquivo existe pra evitar,
    # com o único layout que eu sei ler.
    L.e_apolice = L.reconhecida or e_apolice(texto)
    _checar(L)
    return L


def ler(conteudo: bytes, proibidos: tuple[str, ...] = ()) -> Leitura:
    texto, paginas = texto_do_pdf(conteudo)
    return ler_texto(texto, paginas, proibidos)


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
        "campos": {k: s(v) for k, v in L.campos.items()},
        "checagens": [{"nome": n, "ok": ok, "detalhe": d} for n, ok, d in L.checagens],
        "nao_achou": L.nao_achou, "avisos": L.avisos,
    }
