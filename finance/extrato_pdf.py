"""Extrato bancário em PDF virando as mesmas transações que o OFX produz.

POR QUE ISTO EXISTE
O importador de extrato (finance/ofx_import.py) só aceita OFX, e nem todo banco
oferece OFX — vários só dão PDF. Como o resto do caminho (prévia, sugestão de
categoria, aprendizado por contraparte, deduplicação) já está pronto e é bom,
aqui a gente NÃO constrói um importador novo: constrói só a porta de entrada. A
saída deste módulo é um `OfxExtrato`, e daí pra frente é tudo o mesmo código.

O RISCO E A TRAVA
Parsear PDF é frágil por natureza: o layout é visual, não semântico. Numa planilha
de dinheiro, um parser que erra em silêncio é pior que um que não existe — ele
enche o livro caixa de lançamento errado e ninguém percebe até a conciliação.

A defesa é o próprio extrato: o banco imprime o SALDO corrente em várias linhas.
Se `saldo_anterior + soma(lançamentos no intervalo) == saldo_impresso` fechar em
todos os pontos, então cada valor e cada sinal estão certos — não por parecerem
certos, mas por aritmética. Quando não fecha, `parsear_pdf` LEVANTA em vez de
devolver: melhor o cliente ouvir "não consegui ler este extrato" do que receber
lançamento inventado. Foi essa trava que pegou o primeiro bug do parser (ver
`_SECAO_ANEXO` abaixo) antes de qualquer olho humano.

O QUE O PDF NÃO TEM
O OFX traz `FITID`, identificador único por transação, e é dele que sai a
idempotência ao reimportar. PDF não tem nada equivalente, então a chave aqui é
sintética (data + valor + descrição + ordem no dia). Ela reconhece o MESMO PDF
importado duas vezes, mas não reconhece o mesmo lançamento vindo por OFX e por
PDF — pra isso quem trabalha é o `buscar_duplicata` (valor + data) que o
LivroCaixa já roda na prévia e que já desmarca o item por padrão.
"""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date
from decimal import Decimal

from .ofx_import import OfxExtrato, OfxTransacao


class ExtratoPdfInvalido(ValueError):
    """PDF ilegível, banco desconhecido ou aritmética que não fecha."""


# ---------------------------------------------------------------- utilidades

RE_DDMM = re.compile(r"^\d{2}/\d{2}$")
RE_DINHEIRO = re.compile(r"^-?[\d.]{1,15},\d{2}-?$")


def _valor(txt: str) -> Decimal:
    """'1.234,56-' -> Decimal('-1234.56').

    O sufixo '-' é como o Santander marca débito (o sinal vem DEPOIS do número,
    não antes). Sem ele é crédito."""
    negativo = txt.endswith("-")
    n = Decimal(txt.rstrip("-").replace(".", "").replace(",", "."))
    return -n if negativo else n


# Duas palavras pertencem à mesma linha visual se os topos diferem menos que
# isto. As linhas do extrato ficam ~9,5pt umas das outras, então 3pt separa com
# folga sem juntar vizinhas.
_TOLERANCIA_LINHA = 3.0


def _linhas_por_y(pagina) -> list[list[dict]]:
    """Agrupa as palavras da página em linhas visuais, ordenadas por x.

    `extract_text()` não serve aqui: ele lineariza a página e, num extrato de
    duas colunas de texto, gruda a data de um lançamento no fim do anterior e
    joga o nome do favorecido pra linha de baixo. Por coordenada isso some.

    O agrupamento é por PROXIMIDADE, não por `round(top)`. Arredondar parece
    equivalente e não é: uma linha real tinha o histórico em top=673.4999 e o
    valor em 673.5000, e o arredondamento mandou cada metade pra um balde — o
    lançamento perdeu a descrição e o vizinho de cima herdou um favorecido que
    era o histórico do de baixo. O erro não aparece no saldo (os valores estavam
    todos lá), só na leitura: é o tipo de defeito que a conferência aritmética
    NÃO pega, e por isso vale um comentário."""
    # PyMuPDF devolve (x0, y0, x1, y1, texto, bloco, linha, palavra); normaliza
    # pro formato que o resto do módulo usa.
    palavras = sorted(
        ({"x0": p[0], "top": p[1], "text": p[4]} for p in pagina.get_text("words")),
        key=lambda w: (w["top"], w["x0"]))
    linhas: list[list[dict]] = []
    for w in palavras:
        if linhas and abs(w["top"] - linhas[-1][0]["top"]) <= _TOLERANCIA_LINHA:
            linhas[-1].append(w)
        else:
            linhas.append([w])
    return [sorted(l, key=lambda w: w["x0"]) for l in linhas]


# ------------------------------------------------------------------ Santander

# Colunas medidas num extrato "CONSOLIDADO INTELIGENTE" (página de 595pt):
# data ≈34 · histórico ≈65-320 · valor ≈433 · saldo ≈513. As bandas são largas
# de propósito — outra emissão do mesmo banco desloca alguns pontos.
_X_DATA_FIM = 60
_X_HIST_INI, _X_HIST_FIM = 60, 400
_X_VALOR_INI, _X_VALOR_FIM = 400, 480
_X_SALDO_INI = 480

# O extrato do Santander NÃO tem cabeçalho de tabela — os lançamentos
# simplesmente começam. Os ANEXOS, sim, e é neles que a gente se ancora: o
# extrato vale do começo do documento até o primeiro anexo.
#
# Isto não é preciosismo. Sem este corte o parser lia também o anexo de PIX, que
# REPETE os mesmos lançamentos noutro layout, mais o quadro "Saldos por Período"
# e a tabela de indicadores econômicos do fim: 269 lançamentos em vez de 233, com
# ~52 linhas fantasmas, e a conferência de saldo caindo de 22/22 pra 22/55.
#
# "Compras com Cartão de Débito" ficou DE FORA de propósito: essa frase aparece
# duas vezes com sentidos diferentes — uma como total no resumo da conta (no meio
# do extrato) e outra como título do anexo. Usá-la como marca cortava o extrato
# na primeira página.
_SECAO_ANEXO = re.compile(
    r"saldos por per[ií]odo"
    r"|data\s+n[uú]mero do cart[aã]o"
    r"|data\s+canal\s+tipo",
    re.I)

_RE_AG_CONTA = re.compile(r"^(\d{4})\s+([\d.\-]{6,})$")
_MESES = {"janeiro": 1, "fevereiro": 2, "março": 3, "marco": 3, "abril": 4,
          "maio": 5, "junho": 6, "julho": 7, "agosto": 8, "setembro": 9,
          "outubro": 10, "novembro": 11, "dezembro": 12}
_RE_COMPETENCIA = re.compile(r"\b(" + "|".join(_MESES) + r")/(\d{4})\b", re.I)


@dataclass
class _Bruto:
    """Um lançamento como ele sai do papel, antes de virar OfxTransacao."""
    dia: str | None          # 'dd/mm'
    historico: str
    valor: Decimal           # com sinal
    saldo: Decimal | None    # saldo corrente impresso, quando houver
    favorecido: str = ""


def _extrair_santander(pdf) -> tuple[list[_Bruto], dict]:
    lancamentos: list[_Bruto] = []
    meta: dict = {}
    parar = False
    for pagina in pdf:
        if parar:
            break
        for palavras in _linhas_por_y(pagina):
            texto_linha = " ".join(w["text"] for w in palavras)
            if _SECAO_ANEXO.search(texto_linha):
                parar = True
                break

            if "agencia" not in meta:
                m = _RE_AG_CONTA.match(texto_linha.strip())
                if m:
                    meta["agencia"], meta["conta"] = m.group(1), m.group(2)
            if "competencia" not in meta:
                m = _RE_COMPETENCIA.search(texto_linha)
                if m:
                    meta["competencia"] = (int(m.group(2)), _MESES[m.group(1).lower()])

            dia = valor = saldo = None
            partes: list[str] = []
            for w in palavras:
                x, t = w["x0"], w["text"]
                if x < _X_DATA_FIM and RE_DDMM.match(t):
                    dia = t
                elif _X_HIST_INI <= x < _X_HIST_FIM:
                    partes.append(t)
                elif _X_VALOR_INI <= x < _X_VALOR_FIM and RE_DINHEIRO.match(t):
                    valor = _valor(t)
                elif x >= _X_SALDO_INI and RE_DINHEIRO.match(t):
                    saldo = _valor(t)

            historico = " ".join(partes).strip(" -")
            if valor is not None:
                lancamentos.append(_Bruto(dia, historico, valor, saldo))
            elif historico and lancamentos and len(historico) > 3:
                # Linha sem valor logo abaixo de um lançamento: é a segunda linha
                # da descrição, onde o Santander imprime o favorecido do PIX ou o
                # estabelecimento da compra. Só a primeira conta — o resto é
                # rodapé/aviso, que não pode virar nome de ninguém.
                ultimo = lancamentos[-1]
                if not ultimo.favorecido:
                    ultimo.favorecido = historico
    return lancamentos, meta


# Registro de bancos. Cada entrada diz como se reconhecer e como extrair; o
# resto do módulo não sabe de banco nenhum.
_BANCOS = [
    {
        "id": "033",
        "nome": "Santander",
        "marca": re.compile(r"banco santander|santander\.com\.br", re.I),
        "extrair": _extrair_santander,
    },
]


# ------------------------------------------------------- conferência de saldo

def conferir_saldo(lancamentos: list[_Bruto]) -> list[str]:
    """Confere a aritmética contra o saldo corrente impresso pelo banco.

    Devolve a lista de divergências (vazia = tudo fecha). É esta função que
    autoriza ou barra a importação inteira — ver o cabeçalho do módulo."""
    pontos = [i for i, l in enumerate(lancamentos) if l.saldo is not None]
    problemas = []
    for a, b in zip(pontos, pontos[1:]):
        esperado = lancamentos[a].saldo + sum(l.valor for l in lancamentos[a + 1:b + 1])
        if esperado != lancamentos[b].saldo:
            problemas.append(
                f"entre {lancamentos[a].dia or '?'} e {lancamentos[b].dia or '?'}: "
                f"a soma dá {esperado} mas o extrato imprime {lancamentos[b].saldo}")
    return problemas


# ------------------------------------------------------------------- fachada

def _data_completa(dia: str | None, competencia: tuple[int, int] | None) -> date | None:
    """'dd/mm' + a competência do extrato -> date. O ano não vem na linha; vem do
    título ('julho/2026'). Dezembro/janeiro: se o mês da linha for MAIOR que o da
    competência, o lançamento é do ano anterior (extrato de janeiro trazendo 31/12)."""
    if not dia or not competencia:
        return None
    ano, mes_comp = competencia
    d, m = int(dia[:2]), int(dia[3:5])
    if m > mes_comp:
        ano -= 1
    try:
        return date(ano, m, d)
    except ValueError:
        return None


def _fitid(chave_conta: str, dt: date, valor: Decimal, descricao: str, ordem: int) -> str:
    """Identificador sintético — o PDF não tem FITID.

    Inclui a ordem dentro do dia porque dois PIX iguais pra mesma pessoa no mesmo
    dia são indistinguíveis no papel; sem ela, o segundo seria descartado como
    duplicata do primeiro. Reimportar o MESMO PDF gera as mesmas chaves (é o que
    garante idempotência); o mesmo lançamento vindo por OFX gera outra, e quem
    pega esse caso é o buscar_duplicata do LivroCaixa."""
    crua = f"{chave_conta}|{dt.isoformat()}|{valor}|{descricao}|{ordem}"
    return "pdf" + hashlib.sha1(crua.encode("utf-8")).hexdigest()[:16]


def parsear_pdf(bruto: bytes) -> OfxExtrato:
    """PDF de extrato -> OfxExtrato, ou levanta ExtratoPdfInvalido.

    Levanta (em vez de devolver o que deu) quando: o PDF não abre, o banco não é
    reconhecido, não há lançamento nenhum, ou a conferência de saldo não fecha."""
    try:
        import pymupdf                                          # PyMuPDF
    except ImportError:                                         # pragma: no cover
        try:
            import fitz as pymupdf
        except ImportError as e:
            raise ExtratoPdfInvalido("leitura de PDF indisponível neste servidor") from e

    try:
        pdf = pymupdf.open(stream=bruto, filetype="pdf")
    except Exception as e:
        raise ExtratoPdfInvalido(f"não consegui abrir o PDF: {e}") from e

    with pdf:
        amostra = "\n".join(pdf[i].get_text() for i in range(min(3, pdf.page_count)))
        if not amostra.strip():
            raise ExtratoPdfInvalido(
                "este PDF não tem texto — parece um extrato digitalizado (foto/scan), "
                "que ainda não sabemos ler. Peça ao banco o PDF original ou o arquivo OFX.")

        banco = next((b for b in _BANCOS if b["marca"].search(amostra)), None)
        if banco is None:
            raise ExtratoPdfInvalido(
                "não reconheci o banco deste extrato. Por enquanto lemos PDF de: "
                + ", ".join(b["nome"] for b in _BANCOS)
                + ". Para os outros, use o arquivo OFX.")

        lancamentos, meta = banco["extrair"](pdf)

    if not lancamentos:
        raise ExtratoPdfInvalido(
            "não encontrei lançamento nenhum neste PDF. Confira se é o extrato da "
            "conta corrente, e não o comprovante ou a fatura do cartão.")

    problemas = conferir_saldo(lancamentos)
    if problemas:
        raise ExtratoPdfInvalido(
            "li os lançamentos mas a conta não fecha com o saldo impresso no extrato, "
            "então não vou importar nada pra não sujar seu caixa. "
            + problemas[0]
            + (f" (e mais {len(problemas) - 1})" if len(problemas) > 1 else "")
            + ". Se puder, use o arquivo OFX deste mesmo período.")

    competencia = meta.get("competencia")
    chave_conta = f"{banco['id']}:{meta.get('agencia', '')}:{meta.get('conta', '')}"

    # A data só aparece quando MUDA; as linhas seguintes herdam a anterior.
    dia_corrente = None
    ordem_no_dia: dict[str, int] = {}
    transacoes: list[OfxTransacao] = []
    for l in lancamentos:
        if l.dia:
            dia_corrente = l.dia
        dia = l.dia or dia_corrente
        dt = _data_completa(dia, competencia)
        if dt is None:
            continue
        ordem_no_dia[dia] = ordem_no_dia.get(dia, 0) + 1
        descricao = (l.historico + (" · " + l.favorecido if l.favorecido else "")).strip()
        centavos = int(abs(l.valor) * 100)
        transacoes.append(OfxTransacao(
            fitid=_fitid(chave_conta, dt, l.valor, descricao, ordem_no_dia[dia]),
            data=dt,
            valor_centavos=centavos,
            tipo="receita" if l.valor > 0 else "despesa",
            trntype="CREDIT" if l.valor > 0 else "DEBIT",
            # `name` leva o texto INTEIRO (histórico + favorecido) porque é dele
            # que sai OfxTransacao.descricao, e é a descrição que alimenta tanto a
            # tela quanto o sugerir_classificacao. As palavras que dão categoria
            # ("posto", "drogaria", "mercado") moram no favorecido; o que a
            # operação é ("PIX ENVIADO") mora no histórico. Separados, cada metade
            # sozinha classifica mal.
            memo=l.historico, name=descricao,
            checknum="", refnum="",
        ))

    if not transacoes:
        raise ExtratoPdfInvalido(
            "achei os lançamentos mas não consegui datar nenhum — não encontrei o "
            "mês de referência no PDF.")

    datas = [t.data for t in transacoes]
    saldo_final = next((l.saldo for l in reversed(lancamentos) if l.saldo is not None), None)
    return OfxExtrato(
        banco_id=banco["id"], agencia=meta.get("agencia", ""), conta=meta.get("conta", ""),
        tipo_conta="CHECKING", moeda="BRL",
        periodo_ini=min(datas), periodo_fim=max(datas),
        saldo_final_centavos=int(saldo_final * 100) if saldo_final is not None else None,
        saldo_data=max(datas),
        transacoes=transacoes,
    )
