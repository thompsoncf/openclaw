"""Importação de planilha (CSV) para o catálogo do fornecedor (Fase 1.1).

O fornecedor exporta a planilha dele como CSV ("Salvar como CSV" no Excel) e sobe.
Esta lógica LÊ de forma TOLERANTE e devolve um PREVIEW pra revisão – NUNCA importa
direto. O fornecedor confere/corrige o que foi lido antes de criar os produtos.

Princípios:
- Tolerante a cabeçalhos variados: 'produto'/'nome'/'item'/'descricao' -> nome;
  'preco'/'valor'/'venda' -> preço; 'unidade'/'und'/'un' -> unidade; etc.
- Ignora colunas que não reconhece (não quebra).
- Aceita preço em formato BR ("6,90") ou US ("6.90").
- Linhas sem nome ou sem preço viram "linha com problema" no preview (não somem;
  o fornecedor decide o que fazer).
- NÃO grava nada no banco. Só interpreta e devolve. Quem grava é a UI, depois da
  revisão, chamando catalogo.criar_produto pra cada linha aprovada.

Sem dependência nova: usa o módulo csv padrão do Python.
"""
from __future__ import annotations

import csv
import io
import re
import unicodedata
from typing import Optional


# nomes de coluna aceitos (lower, sem acento) -> campo canônico
_MAPA_COLUNAS = {
    "nome": "nome", "produto": "nome", "item": "nome", "descricao": "nome",
    "descricão": "nome", "mercadoria": "nome",
    "unidade": "unidade", "und": "unidade", "un": "unidade", "medida": "unidade",
    "preco": "preco", "preço": "preco", "valor": "preco", "venda": "preco",
    "preco_venda": "preco", "preço_venda": "preco", "preco venda": "preco",
    "categoria": "categoria", "tipo": "categoria", "grupo": "categoria",
    "minimo": "estoque_minimo", "mínimo": "estoque_minimo",
    "estoque_minimo": "estoque_minimo", "estoque minimo": "estoque_minimo",
}

# unidades aceitas (normaliza variações)
_MAPA_UNIDADES = {
    "kg": "kg", "quilo": "kg", "quilograma": "kg", "kilo": "kg", "k": "kg",
    "duzia": "duzia", "dúzia": "duzia", "dz": "duzia",
    "unidade": "unidade", "un": "unidade", "und": "unidade", "uni": "unidade", "u": "unidade",
    "maco": "maco", "maço": "maco", "mc": "maco",
    "bandeja": "bandeja", "bdj": "bandeja",
    "litro": "litro", "lt": "litro", "l": "litro",
    "pacote": "pacote", "pct": "pacote", "pc": "pacote",
}


def _sem_acento(s: str) -> str:
    """Remove acentos de uma string."""
    return "".join(
        ch for ch in unicodedata.normalize("NFKD", s or "")
        if not unicodedata.combining(ch)
    )


def _norm_cabecalho(col: str) -> Optional[str]:
    """Mapeia um nome de coluna da planilha pro campo canônico (ou None)."""
    chave = _sem_acento((col or "").strip().lower())
    return _MAPA_COLUNAS.get(chave)


def _parse_preco_centavos(valor: str) -> Optional[int]:
    """Converte '6,90' / '6.90' / 'R$ 6,90' / '690' -> centavos. None se não der."""
    if valor is None:
        return None
    s = str(valor).strip()
    if not s:
        return None
    # tira R$, espaços e letras
    s = re.sub(r"[^\d,.\-]", "", s)
    if not s:
        return None
    # normaliza separador: se tem vírgula E ponto, o último é o decimal
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")   # BR: 1.234,56
        else:
            s = s.replace(",", "")                       # US: 1,234.56
    elif "," in s:
        s = s.replace(",", ".")                          # 6,90 -> 6.90
    try:
        reais = float(s)
    except ValueError:
        return None
    if reais < 0:
        return None
    return int(round(reais * 100))


def _parse_unidade(valor: str) -> str:
    """Normaliza a unidade. Default kg se não reconhecer."""
    chave = _sem_acento((valor or "").strip().lower())
    return _MAPA_UNIDADES.get(chave, "kg")


def _parse_qtd(valor: str) -> Optional[float]:
    """Converte quantidade/mínimo BR/US -> float. None se vazio/inválido."""
    if valor is None:
        return None
    s = re.sub(r"[^\d,.\-]", "", str(valor).strip())
    if not s:
        return None
    if "," in s and "." in s:
        if s.rfind(",") > s.rfind("."):
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    elif "," in s:
        s = s.replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


def _detectar_dialeto(amostra: str) -> csv.Dialect:
    """Detecta separador (vírgula/ponto-vírgula/tab). CSV BR costuma usar ';'."""
    try:
        return csv.Sniffer().sniff(amostra, delimiters=",;\t")
    except csv.Error:
        class _D(csv.Dialect):
            delimiter = ";" if amostra.count(";") > amostra.count(",") else ","
            quotechar = '"'
            doublequote = True
            skipinitialspace = True
            lineterminator = "\r\n"
            quoting = csv.QUOTE_MINIMAL
        return _D()


def ler_planilha_csv(conteudo: str | bytes) -> dict:
    """Lê o CSV e devolve um PREVIEW pra revisão (não grava nada).

    Retorna:
      {
        "ok": bool,                  # conseguiu ler o cabeçalho?
        "colunas_reconhecidas": {...},  # nome_original -> campo canônico
        "itens": [                   # uma entrada por linha de dados
          {"linha": 2, "nome": "Tomate", "unidade": "kg",
           "preco_venda_centavos": 690, "categoria": "fruta",
           "estoque_minimo": 5.0, "problemas": []},
          ...
        ],
        "total": int, "validos": int, "com_problema": int,
        "erro": str | None,
      }

    "problemas" lista o que falta na linha (ex: "sem nome", "sem preço"). Linha com
    problema NÃO é descartada – entra no preview pro fornecedor corrigir/decidir.
    """
    if isinstance(conteudo, bytes):
        # tenta utf-8; cai pra latin-1 (Excel BR salva muito em latin-1/cp1252)
        try:
            texto = conteudo.decode("utf-8-sig")
        except UnicodeDecodeError:
            texto = conteudo.decode("latin-1", errors="replace")
    else:
        texto = conteudo

    texto = texto.strip()
    if not texto:
        return {"ok": False, "erro": "planilha vazia", "itens": [],
                "colunas_reconhecidas": {}, "total": 0, "validos": 0, "com_problema": 0}

    amostra = texto[:2048]
    dialeto = _detectar_dialeto(amostra)
    leitor = csv.reader(io.StringIO(texto), dialeto)

    linhas = list(leitor)
    if not linhas:
        return {"ok": False, "erro": "sem linhas", "itens": [],
                "colunas_reconhecidas": {}, "total": 0, "validos": 0, "com_problema": 0}

    # 1ª linha = cabeçalho. Mapeia cada coluna.
    cabecalho = linhas[0]
    col_para_campo: dict[int, str] = {}
    reconhecidas: dict[str, str] = {}
    for idx, col in enumerate(cabecalho):
        campo = _norm_cabecalho(col)
        if campo:
            col_para_campo[idx] = campo
            reconhecidas[col.strip()] = campo

    # precisa pelo menos do nome
    if "nome" not in col_para_campo.values():
        return {
            "ok": False,
            "erro": "não achei a coluna de nome do produto (tente 'nome' ou 'produto')",
            "colunas_reconhecidas": reconhecidas,
            "itens": [], "total": 0, "validos": 0, "com_problema": 0,
        }

    itens = []
    validos = com_problema = 0
    for n, linha in enumerate(linhas[1:], start=2):
        if not any((c or "").strip() for c in linha):
            continue  # linha totalmente vazia: pula
        dados = {"nome": None, "unidade": "kg", "preco_venda_centavos": None,
                 "categoria": None, "estoque_minimo": 0.0}
        for idx, campo in col_para_campo.items():
            if idx >= len(linha):
                continue
            valor = linha[idx]
            if campo == "nome":
                dados["nome"] = (valor or "").strip() or None
            elif campo == "unidade":
                dados["unidade"] = _parse_unidade(valor)
            elif campo == "preco":
                dados["preco_venda_centavos"] = _parse_preco_centavos(valor)
            elif campo == "categoria":
                dados["categoria"] = (valor or "").strip() or None
            elif campo == "estoque_minimo":
                dados["estoque_minimo"] = _parse_qtd(valor) or 0.0

        problemas = []
        if not dados["nome"]:
            problemas.append("sem nome")
        if dados["preco_venda_centavos"] is None:
            problemas.append("sem preço")
            dados["preco_venda_centavos"] = 0
        dados["linha"] = n
        dados["problemas"] = problemas
        if problemas:
            com_problema += 1
        else:
            validos += 1
        itens.append(dados)

    return {
        "ok": True,
        "erro": None,
        "colunas_reconhecidas": reconhecidas,
        "itens": itens,
        "total": len(itens),
        "validos": validos,
        "com_problema": com_problema,
    }


def importar_itens(pool, fornecedor_id: int, itens: list[dict]) -> dict:
    """Grava no catálogo os itens JÁ REVISADOS pelo fornecedor.

    Chamado pela UI DEPOIS da revisão. Cada item precisa ter nome e preço válidos
    (a UI filtra/corrige antes). Usa catalogo.criar_produto pra cada um.
    Pula itens sem nome (segurança). Retorna {criados, pulados}.

    Importação de catálogo cria PRODUTOS (não dá entrada de estoque – isso é compra).
    """
    from . import catalogo

    criados = pulados = 0
    for it in itens:
        nome = (it.get("nome") or "").strip()
        if not nome:
            pulados += 1
            continue
        try:
            catalogo.criar_produto(
                pool,
                fornecedor_id=fornecedor_id,
                nome=nome,
                unidade=it.get("unidade", "kg"),
                categoria=it.get("categoria"),
                preco_venda_centavos=int(it.get("preco_venda_centavos") or 0),
                estoque_minimo=float(it.get("estoque_minimo") or 0),
            )
            criados += 1
        except Exception:
            pulados += 1
    return {"criados": criados, "pulados": pulados}
