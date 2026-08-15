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


# Vocabulário do nicho de EVENTOS: o serviço "Locação cozinha" ou "Pacote
# essencial" não é um hortifrúti, mas a empresa quer a foto do que está
# vendendo. Mesmo motor, outro dicionário e outro complemento de busca.
_TRADUCAO_EVENTOS: dict[str, str] = {
    "espaco": "event venue", "salao": "event hall", "casa": "event venue",
    "buffet": "buffet table", "jantar": "dinner table setting",
    "almoco": "lunch table setting", "coquetel": "cocktail party",
    "mesa": "table setting", "toalha": "table linen", "cadeira": "event chairs",
    "movel": "event furniture", "louca": "tableware", "taca": "wine glasses",
    "talher": "cutlery table", "palco": "event stage", "lounge": "lounge furniture",
    "cerimonia": "wedding ceremony", "casamento": "wedding reception",
    "aniversario": "birthday party", "infantil": "kids birthday party",
    "formatura": "graduation party", "corporativo": "corporate event",
    "confraternizacao": "company party", "decoracao": "event decoration",
    "flores": "flower arrangement", "bolo": "cake table", "doce": "dessert table",
    "entrada": "appetizers platter", "garcom": "waiter serving",
    "equipe": "event staff", "seguranca": "event security",
    "recepcionista": "event hostess", "dj": "dj party", "som": "sound system",
    "iluminacao": "event lighting", "pista": "dance floor", "tenda": "event tent",
    "piscina": "pool party", "churrasco": "barbecue", "bar": "cocktail bar",
    "drink": "cocktails", "chopp": "draft beer", "bebida": "party drinks",
    "cozinha": "commercial kitchen", "fotografia": "event photographer",
    "video": "event videographer", "brinquedo": "kids party games",
}


def _termo_busca(nome_produto: str, mapa: dict[str, str] | None = None) -> str | None:
    """Acha o termo de busca (em inglês) a partir do nome do produto/serviço."""
    mapa = mapa if mapa is not None else _TRADUCAO
    n = _norm(nome_produto)
    if not n:
        return None
    palavras = set(n.replace("-", " ").split())
    # Compostas primeiro (couve-flor)
    for chave, en in mapa.items():
        if "-" in chave and set(chave.replace("-", " ").split()).issubset(palavras):
            return en
    # Palavra inteira
    for chave, en in mapa.items():
        if "-" not in chave and chave in palavras:
            return en
    # Substring (plural)
    for chave, en in mapa.items():
        if "-" not in chave and chave in n:
            return en
    return None


def _buscar_unsplash(termo: str, n: int = 4, complemento: str = "food fresh") -> list[str]:
    """Busca no Unsplash e retorna até n URLs de foto. [] se sem chave ou erro."""
    chave = os.environ.get("UNSPLASH_ACCESS_KEY")
    if not chave:
        return []
    try:
        q = urllib.parse.quote((termo + " " + complemento).strip())
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


def opcoes_para_servico(nome_servico: str, n: int = 4) -> list[str]:
    """Até n fotos pro serviço de EVENTO (espaço, buffet, palco, garçom...).

    Sem palavra conhecida no nome, cai na busca pelo nome cru + "event" — é
    melhor mostrar quatro fotos mais ou menos do que nenhuma, já que quem
    escolhe é a empresa (e ela pode sempre subir a foto real dela).
    """
    termo = _termo_busca(nome_servico, _TRADUCAO_EVENTOS)
    if not termo:
        termo = _norm(nome_servico)[:60]
    if not termo:
        return []
    return _buscar_unsplash(termo, n, complemento="event party")


def sugerir_foto(nome_produto: str) -> str | None:
    """A 1ª foto sugerida (pro cadastro automático). None se não achar/sem chave."""
    ops = opcoes_de_foto(nome_produto, n=1)
    return ops[0] if ops else None


def tem_traducao(nome_produto: str) -> bool:
    """True se o nome casa com algum hortifruti conhecido (independe de ter chave)."""
    return _termo_busca(nome_produto) is not None
