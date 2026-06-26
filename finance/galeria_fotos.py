"""Galeria de fotos de hortifruti curada pelo Zaq.

Quando o fornecedor cadastra um produto ("Banana prata"), o sistema SUGERE fotos pelo
nome. No catálogo, o fornecedor VÊ a foto de cada item e pode TROCAR: escolher outra da
galeria (várias opções por item), colar o link de uma foto própria, ou remover (cai no
ícone da categoria).

Funções:
  sugerir_foto(nome)   -> a 1ª foto sugerida (str | None) – pro cadastro automático
  opcoes_de_foto(nome) -> lista de URLs sugeridas pra esse item (pro seletor de troca)

Galeria curada (uso comercial livre, Pixabay). Pra produção, hospedar no Zaq e trocar
as URLs aqui – a lógica fica igual.
"""
from __future__ import annotations

import unicodedata


def _norm(s: str) -> str:
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.strip().lower()


# Mapa: palavra-chave (normalizada) -> LISTA de URLs (várias opções por item).
# A 1ª da lista é a sugestão padrão. As demais aparecem no seletor de troca.
_GALERIA: dict[str, list[str]] = {
    "banana": [
        "https://cdn.pixabay.com/photo/2018/09/24/19/15/bananas-3700718_640.jpg",
        "https://cdn.pixabay.com/photo/2017/01/26/02/06/banana-2009240_640.jpg",
        "https://cdn.pixabay.com/photo/2016/01/03/17/59/bananas-1119790_640.jpg",
    ],
    "maca": [
        "https://cdn.pixabay.com/photo/2016/11/30/15/00/apple-1872997_640.jpg",
        "https://cdn.pixabay.com/photo/2018/07/11/21/51/apple-3532285_640.jpg",
        "https://cdn.pixabay.com/photo/2017/09/26/13/31/apple-2788599_640.jpg",
    ],
    "laranja": [
        "https://cdn.pixabay.com/photo/2016/10/22/20/02/oranges-1761032_640.jpg",
        "https://cdn.pixabay.com/photo/2015/03/26/09/54/orange-690366_640.jpg",
    ],
    "mamao": ["https://cdn.pixabay.com/photo/2017/05/07/08/56/papaya-2291813_640.jpg"],
    "melancia": ["https://cdn.pixabay.com/photo/2016/07/20/21/53/watermelon-1531427_640.jpg"],
    "abacaxi": ["https://cdn.pixabay.com/photo/2016/05/05/12/27/pineapple-1373471_640.jpg"],
    "manga": ["https://cdn.pixabay.com/photo/2016/03/05/19/02/mango-1238676_640.jpg"],
    "uva": [
        "https://cdn.pixabay.com/photo/2016/08/11/08/49/grapes-1585297_640.jpg",
        "https://cdn.pixabay.com/photo/2017/08/02/14/30/grapes-2571001_640.jpg",
    ],
    "limao": ["https://cdn.pixabay.com/photo/2017/06/30/11/56/lime-2459702_640.jpg"],
    "morango": [
        "https://cdn.pixabay.com/photo/2018/04/29/11/54/strawberries-3358461_640.jpg",
        "https://cdn.pixabay.com/photo/2016/04/15/08/04/strawberries-1330459_640.jpg",
    ],
    "melao": ["https://cdn.pixabay.com/photo/2018/07/11/21/51/melon-3532232_640.jpg"],
    "pera": ["https://cdn.pixabay.com/photo/2016/08/11/08/43/pears-1585282_640.jpg"],
    "abacate": ["https://cdn.pixabay.com/photo/2017/05/11/19/44/avocado-2305375_640.jpg"],
    "goiaba": ["https://cdn.pixabay.com/photo/2018/10/03/21/65/guava-3722806_640.jpg"],
    "maracuja": ["https://cdn.pixabay.com/photo/2019/03/29/06/24/passion-fruit-4087743_640.jpg"],
    "tomate": [
        "https://cdn.pixabay.com/photo/2011/03/16/16/01/tomatoes-5356_640.jpg",
        "https://cdn.pixabay.com/photo/2016/08/11/08/49/tomatoes-1585325_640.jpg",
    ],
    "cenoura": [
        "https://cdn.pixabay.com/photo/2016/08/03/20/47/carrots-1568067_640.jpg",
        "https://cdn.pixabay.com/photo/2018/06/18/16/05/carrots-3483084_640.jpg",
    ],
    "cebola": ["https://cdn.pixabay.com/photo/2016/03/05/19/02/onions-1238243_640.jpg"],
    "batata": ["https://cdn.pixabay.com/photo/2014/10/22/18/43/potatoes-498169_640.jpg"],
    "pimentao": ["https://cdn.pixabay.com/photo/2016/08/11/08/45/peppers-1585291_640.jpg"],
    "abobora": ["https://cdn.pixabay.com/photo/2017/08/07/19/47/pumpkin-2604017_640.jpg"],
    "berinjela": ["https://cdn.pixabay.com/photo/2016/08/03/20/47/eggplant-1568041_640.jpg"],
    "abobrinha": ["https://cdn.pixabay.com/photo/2017/07/20/15/00/zucchini-2522891_640.jpg"],
    "pepino": ["https://cdn.pixabay.com/photo/2016/03/05/19/02/cucumber-1238255_640.jpg"],
    "beterraba": ["https://cdn.pixabay.com/photo/2016/07/22/15/12/beetroot-1535400_640.jpg"],
    "quiabo": ["https://cdn.pixabay.com/photo/2018/07/12/13/56/okra-3533571_640.jpg"],
    "mandioca": ["https://cdn.pixabay.com/photo/2020/04/12/13/27/cassava-5034025_640.jpg"],
    "milho": ["https://cdn.pixabay.com/photo/2016/08/11/08/49/corn-1585294_640.jpg"],
    "alface": [
        "https://cdn.pixabay.com/photo/2016/03/05/19/02/lettuce-1238250_640.jpg",
        "https://cdn.pixabay.com/photo/2018/04/29/16/24/lettuce-3359667_640.jpg",
    ],
    "couve-flor": ["https://cdn.pixabay.com/photo/2017/05/12/08/29/cauliflower-2306494_640.jpg"],
    "couve": ["https://cdn.pixabay.com/photo/2018/01/05/20/26/kale-3064974_640.jpg"],
    "rucula": ["https://cdn.pixabay.com/photo/2020/05/19/13/15/arugula-5189322_640.jpg"],
    "espinafre": ["https://cdn.pixabay.com/photo/2016/03/05/19/02/spinach-1238249_640.jpg"],
    "repolho": ["https://cdn.pixabay.com/photo/2016/03/05/19/02/cabbage-1238246_640.jpg"],
    "brocolis": ["https://cdn.pixabay.com/photo/2016/03/05/19/02/broccoli-1238250_640.jpg"],
    "cebolinha": ["https://cdn.pixabay.com/photo/2018/04/24/16/05/chives-3347536_640.jpg"],
    "salsa": ["https://cdn.pixabay.com/photo/2016/03/05/19/02/parsley-1238252_640.jpg"],
    "coentro": ["https://cdn.pixabay.com/photo/2017/05/07/08/56/cilantro-2291811_640.jpg"],
    "manjericao": ["https://cdn.pixabay.com/photo/2016/03/05/19/02/basil-1238255_640.jpg"],
    "alho": ["https://cdn.pixabay.com/photo/2014/08/15/12/12/garlic-418543_640.jpg"],
    "gengibre": ["https://cdn.pixabay.com/photo/2016/08/11/08/49/ginger-1585297_640.jpg"],
    "pimenta": ["https://cdn.pixabay.com/photo/2017/01/11/19/56/chili-1972425_640.jpg"],
    "hortela": ["https://cdn.pixabay.com/photo/2016/03/05/19/02/mint-1238248_640.jpg"],
}


def _chave_do_nome(nome_produto: str) -> str | None:
    """Acha a palavra-chave da galeria que casa com o nome do produto."""
    n = _norm(nome_produto)
    if not n:
        return None
    palavras = set(n.replace("-", " ").split())
    for chave in _GALERIA:
        if "-" in chave or " " in chave:
            partes = set(chave.replace("-", " ").split())
            if partes.issubset(palavras):
                return chave
    for chave in _GALERIA:
        if "-" not in chave and " " not in chave and chave in palavras:
            return chave
    for chave in _GALERIA:
        if "-" not in chave and " " not in chave and chave in n:
            return chave
    return None


def opcoes_de_foto(nome_produto: str) -> list[str]:
    """Lista de URLs de foto sugeridas pra esse produto (pro seletor de troca).
    Vazia se não houver match."""
    chave = _chave_do_nome(nome_produto)
    return list(_GALERIA.get(chave, [])) if chave else []


def sugerir_foto(nome_produto: str) -> str | None:
    """A 1ª foto sugerida pra o produto (pro cadastro automático). None se não achar."""
    ops = opcoes_de_foto(nome_produto)
    return ops[0] if ops else None


def tem_sugestao(nome_produto: str) -> bool:
    return bool(opcoes_de_foto(nome_produto))
