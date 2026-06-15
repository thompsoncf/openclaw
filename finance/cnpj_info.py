"""Consulta dados de empresa pelo CNPJ na BrasilAPI (Receita Federal).

Usado pra COMPLETAR as lojas (nome + endereco) a partir do CNPJ que o QR leu.
Dados oficiais, gratuitos. Uso pontual (uma loja por vez), respeitando o pedido
da BrasilAPI de nao fazer scan automatizado em massa.

ATENCAO REDE: depende de acesso a https://brasilapi.com.br - se o ambiente
(ex: Render) tiver allowlist de rede, esse dominio precisa estar liberado.
Tolerante a falha: qualquer erro -> retorna None (a loja fica como esta').
"""
import json
import urllib.request
import urllib.error

_URL = "https://brasilapi.com.br/api/cnpj/v1/{}"
_TIMEOUT = 8


def consultar_cnpj(cnpj: str) -> dict | None:
    """Consulta o CNPJ na BrasilAPI e devolve {nome, endereco, cidade, uf, cnae, ramo}.
    None se nao achar ou falhar. nome usa fantasia (mais reconhecivel) com
    fallback pra razao social. ramo e' derivado do CNAE."""
    cnpj = "".join(c for c in (cnpj or "") if c.isdigit())
    if len(cnpj) != 14:
        return None
    try:
        req = urllib.request.Request(
            _URL.format(cnpj),
            headers={"User-Agent": "OpenClaw/1.0", "Accept": "application/json"})
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as resp:
            if resp.status != 200:
                return None
            dados = json.loads(resp.read().decode("utf-8"))
    except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError,
            json.JSONDecodeError, ValueError, OSError):
        return None
    nome = (dados.get("nome_fantasia") or "").strip() or \
           (dados.get("razao_social") or "").strip() or None
    partes = [dados.get("logradouro"), dados.get("numero"), dados.get("bairro")]
    endereco = ", ".join(p.strip() for p in partes if p and str(p).strip()) or None
    cidade = (dados.get("municipio") or "").strip() or None
    uf = (dados.get("uf") or "").strip() or None
    cnae_desc = (dados.get("cnae_fiscal_descricao") or "").strip() or None
    ramo = classificar_ramo(cnae_desc)
    return {"nome": nome, "endereco": endereco, "cidade": cidade, "uf": uf,
            "cnae": cnae_desc, "ramo": ramo}


_RAMOS = [
    ("Farmacia",     ("farmac", "drogaria", "medicament")),
    ("Restaurante",  ("restaurante", "lanchonete", "refeicao", "bar ", "fast",
                      "pizzaria", "sorveteria", "casas de cha", "sucos")),
    ("Supermercado", ("supermerc", "hipermerc", "minimerc", "mercearia",
                      "mercadorias em geral", "alimenticios", "varejista de merc",
                      "atacadista de alimentos", "alimentos")),
]


def classificar_ramo(cnae_desc: str | None) -> str | None:
    """Classifica a loja num ramo (departamento) a partir da descricao do CNAE."""
    if not cnae_desc:
        return None
    import unicodedata
    t = unicodedata.normalize("NFKD", cnae_desc.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    for ramo, chaves in _RAMOS:
        if any(k in t for k in chaves):
            return ramo
    return None


_CATEGORIA_RAMO = {
    "Mercado": "Supermercado",
    "Saude": "Farmacia",
    "Restaurante": "Restaurante",
}


def ramo_por_categoria(categoria: str | None) -> str | None:
    """Reserva: deriva o ramo da categoria do lancamento quando nao temos CNAE."""
    return _CATEGORIA_RAMO.get((categoria or "").strip())
