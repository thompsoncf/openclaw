"""Sugestão de fotos de hortifruti via Unsplash (API com chave).

Quando o fornecedor cadastra "Banana prata", o sistema busca no Unsplash uma foto de
banana e sugere. O Unsplash PERMITE/EXIGE hotlink (diferente do Pixabay que dava
403), então as URLs funcionam embutidas na loja.

Precisa da env UNSPLASH_ACCESS_KEY (registro gratuito em unsplash.com/developers).
Sem a chave, sugerir_foto retorna None (cai no ícone / o fornecedor cola link manual).

Tradução PT->EN simples pros termos comuns (a busca no Unsplash funciona melhor em inglês).

Funções:
  sugerir_foto(nome) -> URL de uma foto (str | None)
  opcoes_de_foto(nome, n=4) -> até n URLs (pro fornecedor escolher)
"""
from __future__ import annotations

import os
import unicodedata
import urllib.parse
import urllib.request
import json as _json


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.strip().lower()


# Tradução PT -> EN dos hortifruti comuns (busca no Unsplash acha melhor em inglês).
# Além disso, mapeia variações pro termo base (banana prata -> banana).
_TRADUCAO: dict[str, str] = {
    "banana": "banana", "maca": "apple", "laranja": "orange", "mamao": "papaya",
    "melancia": "watermelon", "abacaxi": "pineapple", "manga": "mango fruit",
    "uva": "grapes", "limao": "lime", "morango": "strawberry", "melao": "melon",
    "pera": "pear", "abacate": "avocado", "goiaba": "guava", "maracuja": "passion fruit",
    "tomate": "tomato", "cenoura": "carrot", "cebola": "onion", "batata": "potato",
    "pimentao": "bell pepper", "abobora": "pumpkin", "berinjela": "eggplant",
    "abobrinha": "zucchini", "pepino": "cucumber", "beterraba": "beetroot",
    "quiabo": "okra", "mandioca": "cassava", "milho": "corn cob",
    "alface": "lettuce", "couve-flor": "cauliflower", "couve": "kale",
    "rucula": "arugula", "espinafre": "spinach", "repolho": "cabbage",
    "brocolis": "broccoli", "cebolinha": "chives", "salsa": "parsley",
    "coentro": "cilantro", "manjericao": "basil", "alho": "garlic",
    "gengibre": "ginger", "pimenta": "chili pepper", "hortela": "mint leaves",
}


def _termo_busca(nome_produto: str) -> str | None:
    """Acha o termo de busca (em inglês) a partir do nome do produto."""
    n = _norm(nome_produto)
    if not n:
        return None
    palavras = set(n.replace("-", " ").split())
    # Compostas primeiro (couve-flor)
    for chave, en in _TRADUCAO.items():
        if "-" in chave and set(chave.replace("-", " ").split()).issubset(palavras):
            return en
    # Palavra inteira
    for chave, en in _TRADUCAO.items():
        if "-" not in chave and chave in palavras:
            return en
    # Substring (plural)
    for chave, en in _TRADUCAO.items():
        if "-" not in chave and chave in n:
            return en
    return None


def _buscar_unsplash(termo: str, n: int = 4) -> list[str]:
    """Busca no Unsplash e retorna até n URLs de foto. [] se sem chave ou erro."""
    chave = os.environ.get("UNSPLASH_ACCESS_KEY")
    if not chave:
        return []
    try:
        q = urllib.parse.quote(termo + " food fresh")
        url = (f"https://api.unsplash.com/search/photos?query={q}"
               f"&per_page={n}&orientation=squarish&content_filter=high")
        req = urllib.request.Request(url, headers={
            "Authorization": f"Client-ID {chave}",
            "Accept-Version": "v1",
        })
        with urllib.request.urlopen(req, timeout=8) as resp:
            dados = _json.loads(resp.read().decode("utf-8"))
        # Usa a URL "small" (rápida, ~400px) – boa pra card/miniatura
        return [r["urls"]["small"] for r in dados.get("results", [])[:n]
                if r.get("urls", {}).get("small")]
    except Exception:
        return []


def opcoes_de_foto(nome_produto: str, n: int = 4) -> list[str]:
    """Até n URLs sugeridas pra esse produto (pro fornecedor escolher). [] se não achar."""
    termo = _termo_busca(nome_produto)
    if not termo:
        return []
    return _buscar_unsplash(termo, n)


def sugerir_foto(nome_produto: str) -> str | None:
    """A 1ª foto sugerida (pro cadastro automático). None se não achar/sem chave."""
    ops = opcoes_de_foto(nome_produto, n=1)
    return ops[0] if ops else None


def tem_traducao(nome_produto: str) -> bool:
    """True se o nome casa com algum hortifruti conhecido (independe de ter chave)."""
    return _termo_busca(nome_produto) is not None
