"""Importação de extrato bancário (OFX) pros lançamentos.

Duas responsabilidades bem separadas:
  1. parsear_ofx()      — lê o SGML/XML do arquivo e devolve as transações cruas.
  2. sugerir_classificacao() — heurística por texto (MEMO/NAME) pra chutar
     categoria e conta contábil. Não sabe nada de banco de dados; quem sabe
     "o que já foi lançado antes" e "aprender com a contraparte" é o
     LivroCaixa (finance/livro_caixa.py), que tem a conexão.

Formato do arquivo: OFX 1.x em SGML — tags de folha às vezes vêm sem fechamento
(`<TRNTYPE>CREDIT` sem `</TRNTYPE>`), então normalizamos pra XML antes de
parsear. Isso é o que biblitecas como ofxparse fazem por baixo dos panos;
como o projeto não tem essa dependência, fazemos na mão (é pouca coisa).
"""
from __future__ import annotations

import re
import unicodedata
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date
from decimal import Decimal, InvalidOperation


class OfxInvalido(ValueError):
    """Arquivo não é um OFX válido (ou não tem nenhuma transação de conta)."""


def decodificar(bruto: bytes) -> str:
    """Bancos brasileiros costumam exportar em cp1252/latin1 (o próprio
    cabeçalho do OFX geralmente diz `CHARSET:1252`), mas alguns já mandam
    UTF-8. Tenta UTF-8 primeiro (falha rápido se for cp1252 de verdade — os
    acentos do português batem em byte inválido) e cai pro cp1252."""
    for enc in ("utf-8", "cp1252"):
        try:
            return bruto.decode(enc)
        except UnicodeDecodeError:
            continue
    return bruto.decode("utf-8", errors="replace")


@dataclass
class OfxTransacao:
    fitid: str
    data: date
    valor_centavos: int          # sempre positivo — o sinal vira `tipo`
    tipo: str                    # 'receita' | 'despesa'
    trntype: str                 # CREDIT/DEBIT/... cru, só pra debug/kind icon
    memo: str
    name: str
    checknum: str
    refnum: str

    @property
    def descricao(self) -> str:
        """Descrição pra exibir/gravar: prioriza NAME (mais legível, geralmente
        traz quem pagou/recebeu); cai pro MEMO quando não tem NAME."""
        return (self.name or self.memo or "").strip()


@dataclass
class OfxExtrato:
    banco_id: str
    agencia: str
    conta: str
    tipo_conta: str
    moeda: str
    periodo_ini: date | None
    periodo_fim: date | None
    saldo_final_centavos: int | None
    saldo_data: date | None
    transacoes: list[OfxTransacao]

    def chave_conta(self) -> str:
        """Identificador estável da conta bancária de origem — usado no prefixo
        da chave de idempotência (não precisa ser bonito, só único)."""
        return f"{self.banco_id}:{self.agencia}:{self.conta}"


def _sem_acento(s: str) -> str:
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def _normalizar(s: str) -> str:
    return _sem_acento(s or "").lower()


# Prefixos que o próprio banco cola no NAME/MEMO da transação (quem pagou/quem
# recebeu vem "sujo" de rótulo do sistema bancário). Tira só isso — o resto do
# texto (nome da contraparte) fica intacto — pra "Recebimento Pix Fulano" e
# "Fulano" (como o cliente digitou um lançamento manual dele antes) casarem no
# aprendizado por contraparte. Ordem importa: mais específico primeiro.
_PREFIXOS_CONTRAPARTE = [
    "transferencia recebida - pix sicoob", "transf.recebida - pix sicoob",
    "transf recebida - pix sicoob", "recebimento pix ", "pagamento pix ",
    "rem.: ", "rem.:", "rem: ",
]


def chave_contraparte(descricao: str) -> str:
    """Normaliza uma descrição (NAME/MEMO do OFX, ou texto digitado à mão) pra
    comparar contrapartes entre lançamentos, ignorando o rótulo que o banco
    prefixa. Não tenta ser perfeito (nome pode vir truncado pelo banco) — é
    heurística de v1."""
    norm = " ".join(_normalizar(descricao).split())
    for prefixo in _PREFIXOS_CONTRAPARTE:
        if norm.startswith(prefixo):
            return norm[len(prefixo):].strip()
    return norm


_TAG_SEM_FECHO = re.compile(r"^<([A-Za-z0-9./]+)>([^<\r\n]+)$")


def _sgml_para_xml(bruto: str) -> str:
    """Fecha tags de folha que vieram sem `</TAG>` (SGML antigo). Tags que já
    fecham na mesma linha (o caso comum nos exports mais novos, incluindo o
    Sicoob) passam direto — a regex só bate em linha SEM `<` depois do valor."""
    linhas = []
    for linha in bruto.splitlines():
        linha = linha.strip()
        if not linha:
            continue
        m = _TAG_SEM_FECHO.match(linha)
        if m:
            tag, valor = m.groups()
            valor = valor.replace("&", "&amp;")
            linha = f"<{tag}>{valor}</{tag}>"
        linhas.append(linha)
    return "\n".join(linhas)


def _corpo_ofx(bruto: str) -> str:
    """O arquivo começa com um cabeçalho tipo `OFXHEADER:100` linha a linha
    (não é XML) até a primeira tag `<OFX>`. Corta isso fora."""
    idx = bruto.find("<OFX>")
    if idx == -1:
        raise OfxInvalido("arquivo não tem a tag <OFX> — não parece um OFX")
    return bruto[idx:]


def _texto(el: ET.Element | None, tag: str) -> str:
    if el is None:
        return ""
    achou = el.find(tag)
    return (achou.text or "").strip() if achou is not None and achou.text else ""


def _data(txt: str) -> date | None:
    """'20260701120000[-3:BRT]' -> date(2026,7,1). Só os 8 primeiros dígitos
    importam pro livro-caixa (que guarda data, não hora)."""
    if not txt or len(txt) < 8 or not txt[:8].isdigit():
        return None
    try:
        return date(int(txt[0:4]), int(txt[4:6]), int(txt[6:8]))
    except ValueError:
        return None


def _centavos(txt: str) -> int:
    """Converte '567.15' ou '-300.00' pra centavos SEM assumir sinal (aqui
    ainda queremos o sinal, pra decidir receita/despesa)."""
    try:
        d = Decimal(txt.strip())
    except (InvalidOperation, AttributeError):
        raise OfxInvalido(f"valor inválido no OFX: {txt!r}")
    return int((d * 100).to_integral_value())


def parsear_ofx(conteudo: str) -> OfxExtrato:
    """Lê o texto de um arquivo .ofx e devolve os dados da conta + transações.
    Levanta OfxInvalido se não conseguir achar a estrutura mínima esperada
    (BANKACCTFROM + BANKTRANLIST)."""
    corpo = _corpo_ofx(conteudo)
    try:
        raiz = ET.fromstring(_sgml_para_xml(corpo))
    except ET.ParseError as e:
        raise OfxInvalido(f"não consegui ler o XML/SGML do OFX: {e}") from e

    stmtrs = raiz.find(".//STMTRS")
    if stmtrs is None:
        raise OfxInvalido("OFX sem <STMTRS> — não é um extrato de conta corrente")

    acct = stmtrs.find("BANKACCTFROM")
    if acct is None:
        raise OfxInvalido("OFX sem <BANKACCTFROM> — não dá pra saber de qual conta é")

    tranlist = stmtrs.find("BANKTRANLIST")
    transacoes: list[OfxTransacao] = []
    if tranlist is not None:
        for stmttrn in tranlist.findall("STMTTRN"):
            valor_txt = _texto(stmttrn, "TRNAMT")
            fitid = _texto(stmttrn, "FITID")
            dt = _data(_texto(stmttrn, "DTPOSTED"))
            if not fitid or dt is None or not valor_txt:
                continue  # transação sem os 3 campos mínimos: pula, não trava o resto
            bruto_centavos = _centavos(valor_txt)
            transacoes.append(OfxTransacao(
                fitid=fitid,
                data=dt,
                valor_centavos=abs(bruto_centavos),
                tipo="receita" if bruto_centavos >= 0 else "despesa",
                trntype=_texto(stmttrn, "TRNTYPE"),
                memo=_texto(stmttrn, "MEMO"),
                name=_texto(stmttrn, "NAME"),
                checknum=_texto(stmttrn, "CHECKNUM"),
                refnum=_texto(stmttrn, "REFNUM"),
            ))

    ledger = stmtrs.find("LEDGERBAL")
    saldo_txt = _texto(ledger, "BALAMT") if ledger is not None else ""

    return OfxExtrato(
        banco_id=_texto(acct, "BANKID"),
        agencia=_texto(acct, "BRANCHID"),
        conta=_texto(acct, "ACCTID"),
        tipo_conta=_texto(acct, "ACCTTYPE"),
        moeda=_texto(stmtrs, "CURDEF") or "BRL",
        periodo_ini=_data(_texto(tranlist, "DTSTART")) if tranlist is not None else None,
        periodo_fim=_data(_texto(tranlist, "DTEND")) if tranlist is not None else None,
        saldo_final_centavos=_centavos(saldo_txt) if saldo_txt else None,
        saldo_data=_data(_texto(ledger, "DTASOF")) if ledger is not None else None,
        transacoes=transacoes,
    )


# ── Heurística de categoria/plano de contas por texto ────────────────────────
# Cada regra: (padrão a procurar no MEMO+NAME normalizado, categoria, código do
# plano de contas ou None, confiança). "alta" = aplica direto; "media" = aplica
# mas marcado como sugestão (o cliente confere); None = não arrisca, vira "a
# classificar". Ordem importa: a primeira regra que bater vence.
_REGRAS_DESPESA = [
    (("aluguel",),                          "Moradia",     "5.1.01", "alta"),
    (("pacote servico", "pacote de servico", "tarifa banc", "manutencao de conta"),
                                             "Servicos",    "6.1.02", "alta"),
    (("taxa de cartao", "maquininha"),      "Servicos",    "6.1.03", "alta"),
    (("mercado", "supermercado", "hortifruti", "acougue"),
                                             "Mercado",     "3.1.03", "media"),
    (("posto", "combustivel", "auto posto"),"Transporte",  "5.1.08", "media"),
    (("farmacia", "drogaria"),              "Saude",       None,     "media"),
    (("restaurante", "lanchonete", "cafe", "pizzaria"),
                                             "Restaurante", None,     "media"),
]

# Padrões que a gente RECONHECE mas propositalmente não arrisca categoria —
# tem informação demais faltando (fatura consolidada, convênio sem conta
# parecida). Cai em "a classificar" com um motivo explicado pro cliente.
_PADROES_SEM_PALPITE = [
    ("boleto intercredis", "fatura consolidada — não dá pra saber a conta contábil sem abrir os itens"),
    ("conv. seguros", "não existe conta parecida no plano ainda"),
    ("conv seguros", "não existe conta parecida no plano ainda"),
]


@dataclass
class Sugestao:
    categoria: str | None
    confianca: str | None          # 'alta' | 'media' | None
    plano_conta_codigo: str | None
    motivo: str | None = None


def sugerir_classificacao(txn: OfxTransacao) -> Sugestao:
    """Só olha pro texto da transação (MEMO/NAME/TRNTYPE) — nenhuma consulta ao
    banco. Aprendizado por contraparte (mesmo Pix recorrente) é outra camada,
    que mora no LivroCaixa porque precisa do histórico da conta."""
    alvo = _normalizar(f"{txn.memo} {txn.name}")

    if txn.tipo == "receita":
        # Pix/transferência recebida sem padrão de texto forte: melhor não
        # arriscar "Vendas" de qualquer receita — fica pro aprendizado por
        # contraparte ou pra revisão manual.
        return Sugestao(categoria=None, confianca=None, plano_conta_codigo=None)

    for padrao, motivo in _PADROES_SEM_PALPITE:
        if padrao in alvo:
            return Sugestao(categoria=None, confianca=None, plano_conta_codigo=None,
                            motivo=motivo)

    for palavras, categoria, plano, confianca in _REGRAS_DESPESA:
        if any(p in alvo for p in palavras):
            return Sugestao(categoria=categoria, confianca=confianca,
                            plano_conta_codigo=plano)

    return Sugestao(categoria=None, confianca=None, plano_conta_codigo=None)
