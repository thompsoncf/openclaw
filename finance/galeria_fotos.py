"""Galeria de fotos de hortifruti curada pelo Zaq.

Quando o fornecedor cadastra um produto ("Banana prata"), o sistema SUGERE a foto
certa pelo nome – o fornecedor só confirma. Sem upload, sem API externa, sem risco
de foto errada: as fotos são curadas (uma boa por item comum).

Como funciona:
  sugerir_foto("Banana prata") -> URL da foto de banana (ou None se não tiver)

O mapa associa palavras-chave do nome a uma URL de foto. A busca normaliza o nome
(sem acento, minúsculo) e procura a melhor correspondência. Fallback: None (a vitrine
mostra o ícone da categoria, que já existe).

As URLs apontam pra fotos de uso comercial livre (Pixabay/Pexels). Pra produção, o
ideal é o Zaq hospedar as imagens (Supabase Storage / CDN próprio) e trocar as URLs
aqui – mas o mapa e a lógica continuam os mesmos.
"""
from __future__ import annotations

import unicodedata


def _norm(s: str) -> str:
    """Minúsculo, sem acento, sem espaços nas pontas."""
    s = unicodedata.normalize("NFKD", s or "")
    s = "".join(c for c in s if not unicodedata.combining(c))
    return s.strip().lower()


# Mapa: palavra-chave (normalizada) -> URL da foto.
# A chave é a palavra que aparece no nome do produto. Ordem importa: chaves mais
# específicas (ex 'banana prata') antes das genéricas ('banana'), pra casar melhor.
# URLs de bancos grátis (uso comercial livre). Trocar por CDN do Zaq em produção.
_GALERIA: dict[str, str] = {
    # frutas
    "banana": "https://cdn.pixabay.com/photo/2018/09/24/19/15/bananas-3700718_640.jpg",
    "maca": "https://cdn.pixabay.com/photo/2016/11/30/15/00/apple-1872997_640.jpg",
    "laranja": "https://cdn.pixabay.com/photo/2016/10/22/20/02/oranges-1761032_640.jpg",
    "mamao": "https://cdn.pixabay.com/photo/2017/05/07/08/56/papaya-2291813_640.jpg",
    "melancia": "https://cdn.pixabay.com/photo/2016/07/20/21/53/watermelon-1531427_640.jpg",
    "abacaxi": "https://cdn.pixabay.com/photo/2016/05/05/12/27/pineapple-1373471_640.jpg",
    "manga": "https://cdn.pixabay.com/photo/2016/03/05/19/02/mango-1238676_640.jpg",
    "uva": "https://cdn.pixabay.com/photo/2016/08/11/08/49/grapes-1585297_640.jpg",
    "limao": "https://cdn.pixabay.com/photo/2017/06/30/11/56/lime-2459702_640.jpg",
    "morango": "https://cdn.pixabay.com/photo/2018/04/29/11/54/strawberries-3358461_640.jpg",
    "melao": "https://cdn.pixabay.com/photo/2018/07/11/21/51/melon-3532232_640.jpg",
    "pera": "https://cdn.pixabay.com/photo/2016/08/11/08/43/pears-1585282_640.jpg",
    "abacate": "https://cdn.pixabay.com/photo/2017/05/11/19/44/avocado-2305375_640.jpg",
    "goiaba": "https://cdn.pixabay.com/photo/2018/10/03/21/65/guava-3722806_640.jpg",
    "maracuja": "https://cdn.pixabay.com/photo/2019/03/29/06/24/passion-fruit-4087743_640.jpg",
    # legumes
    "tomate": "https://cdn.pixabay.com/photo/2011/03/16/16/01/tomatoes-5356_640.jpg",
    "cenoura": "https://cdn.pixabay.com/photo/2016/08/03/20/47/carrots-1568067_640.jpg",
    "cebola": "https://cdn.pixabay.com/photo/2016/03/05/19/02/onions-1238243_640.jpg",
    "batata": "https://cdn.pixabay.com/photo/2014/10/22/18/43/potatoes-498169_640.jpg",
    "pimentao": "https://cdn.pixabay.com/photo/2016/08/11/08/45/peppers-1585291_640.jpg",
    "abobora": "https://cdn.pixabay.com/photo/2017/08/07/19/47/pumpkin-2604017_640.jpg",
    "chuchu": "https://cdn.pixabay.com/photo/2020/08/30/12/protein-5530900_640.jpg",
    "berinjela": "https://cdn.pixabay.com/photo/2016/08/03/20/47/eggplant-1568041_640.jpg",
    "abobrinha": "https://cdn.pixabay.com/photo/2017/07/20/15/00/zucchini-2522891_640.jpg",
    "pepino": "https://cdn.pixabay.com/photo/2016/03/05/19/02/cucumber-1238255_640.jpg",
    "beterraba": "https://cdn.pixabay.com/photo/2016/07/22/15/12/beetroot-1535400_640.jpg",
    "quiabo": "https://cdn.pixabay.com/photo/2018/07/12/13/56/okra-3533571_640.jpg",
    "mandioca": "https://cdn.pixabay.com/photo/2020/04/12/13/27/cassava-5034025_640.jpg",
    "milho": "https://cdn.pixabay.com/photo/2016/08/11/08/49/corn-1585294_640.jpg",
    # verduras / folhas
    "alface": "https://cdn.pixabay.com/photo/2016/03/05/19/02/lettuce-1238250_640.jpg",
    "couve": "https://cdn.pixabay.com/photo/2018/01/05/20/26/kale-3064974_640.jpg",
    "rucula": "https://cdn.pixabay.com/photo/2020/05/19/13/15/arugula-5189322_640.jpg",
    "espinafre": "https://cdn.pixabay.com/photo/2016/03/05/19/02/spinach-1238249_640.jpg",
    "repolho": "https://cdn.pixabay.com/photo/2016/03/05/19/02/cabbage-1238246_640.jpg",
    "agriao": "https://cdn.pixabay.com/photo/2020/02/24/13/applewatercress-4876741_640.jpg",
    "brocolis": "https://cdn.pixabay.com/photo/2016/03/05/19/02/broccoli-1238250_640.jpg",
    "couve-flor": "https://cdn.pixabay.com/photo/2017/05/12/08/29/cauliflower-2306494_640.jpg",
    # temperos / cheiro-verde
    "cebolinha": "https://cdn.pixabay.com/photo/2018/04/24/16/05/chives-3347536_640.jpg",
    "salsa": "https://cdn.pixabay.com/photo/2016/03/05/19/02/parsley-1238252_640.jpg",
    "coentro": "https://cdn.pixabay.com/photo/2017/05/07/08/56/cilantro-2291811_640.jpg",
    "manjericao": "https://cdn.pixabay.com/photo/2016/03/05/19/02/basil-1238255_640.jpg",
    "alho": "https://cdn.pixabay.com/photo/2014/08/15/12/12/garlic-418543_640.jpg",
    "gengibre": "https://cdn.pixabay.com/photo/2016/08/11/08/49/ginger-1585297_640.jpg",
    "pimenta": "https://cdn.pixabay.com/photo/2017/01/11/19/56/chili-1972425_640.jpg",
    "hortela": "https://cdn.pixabay.com/photo/2016/03/05/19/02/mint-1238248_640.jpg",
}


def sugerir_foto(nome_produto: str) -> str | None:
    """Sugere a URL de uma foto pra um produto, pelo nome. None se não achar
    (a vitrine cai no ícone da categoria, que já existe).

    Estratégia: normaliza o nome e procura uma palavra-chave da galeria que apareça
    nele. Casa por palavra inteira (ex 'banana prata' acha 'banana'). Tenta também
    chaves compostas primeiro (ex 'couve-flor' antes de 'couve')."""
    n = _norm(nome_produto)
    if not n:
        return None
    palavras = set(n.replace("-", " ").split())
    # 1) chaves compostas (com - ou espaço): casam se todas as partes estão no nome
    for chave, url in _GALERIA.items():
        if "-" in chave or " " in chave:
            partes = set(chave.replace("-", " ").split())
            if partes.issubset(palavras):
                return url
    # 2) chave simples: aparece como palavra inteira no nome
    for chave, url in _GALERIA.items():
        if "-" not in chave and " " not in chave and chave in palavras:
            return url
    # 3) fallback: substring (pega plural, ex 'bananas' contém 'banana')
    for chave, url in _GALERIA.items():
        if "-" not in chave and " " not in chave and chave in n:
            return url
    return None


def tem_sugestao(nome_produto: str) -> bool:
    """True se há foto sugerida pra esse nome."""
    return sugerir_foto(nome_produto) is not None
