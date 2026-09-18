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
    # O SEGURADO — lido só DEPOIS de "SUAS INFORMAÇÕES". O bloco do corretor vem
    # antes no papel e tem "E-mail:" também: sem este corte o primeiro casamento
    # devolvia o e-mail da F.F. Apolinário como se fosse o da segurada. Rótulo
    # igual em bloco diferente é o jeito mais silencioso de errar.
    i = texto.find("SUAS INFORMAÇÕES")
    seg = texto[i:] if i >= 0 else texto
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
    # o condutor (só o que muda o preço na renovação)
    pega("condutor_idade", "Idade", lambda v: int(_digitos(v)) if _digitos(v) else None)
    pega("condutor_estado_civil", "Estado Civil")

    # a situação: o documento diz o que é
    c["situacao"] = "proposta" if re.search(r"\bPROPOSTA\b", texto) and not c.get("numero_apolice") else "vigente"


_LAYOUTS = (
    # (nome, teste de reconhecimento, leitor)
    ("Allianz", lambda t: bool(re.search(r"\bALLIANZ\b", t, re.I)) and "Nº da Proposta" in t, _allianz),
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


def ler_texto(texto: str, paginas: int = 0) -> Leitura:
    L = Leitura(paginas=paginas)
    for nome, reconhece, leitor in _LAYOUTS:
        if reconhece(texto):
            L.seguradora, L.reconhecida = nome, True
            leitor(texto, L)
            break
    if not L.reconhecida:
        # layout desconhecido: tenta o genérico, e AVISA
        _allianz(texto, L)           # os rótulos genéricos são os mesmos por enquanto
        L.campos.pop("seguradora", None)
        L.avisos.append("Não reconheci o layout desta seguradora — o que achei está "
                        "preenchido, o resto ficou em branco. Confira tudo.")
    _checar(L)
    return L


def ler(conteudo: bytes) -> Leitura:
    texto, paginas = texto_do_pdf(conteudo)
    return ler_texto(texto, paginas)


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
        "seguradora": L.seguradora, "reconhecida": L.reconhecida, "paginas": L.paginas,
        "campos": {k: s(v) for k, v in L.campos.items()},
        "checagens": [{"nome": n, "ok": ok, "detalhe": d} for n, ok, d in L.checagens],
        "nao_achou": L.nao_achou, "avisos": L.avisos,
    }
