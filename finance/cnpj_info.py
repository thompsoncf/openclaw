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
    """Consulta o CNPJ na BrasilAPI e devolve {nome, endereco, cidade, uf}.
    None se nao achar ou falhar. nome usa fantasia (mais reconhecivel) com
    fallback pra razao social."""
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
    # endereco = logradouro + numero (o bairro vira campo proprio)
    _log = (dados.get("logradouro") or "").strip()
    _num = (dados.get("numero") or "").strip()
    endereco = (f"{_log}, {_num}" if _log and _num else _log or _num) or None
    bairro = (dados.get("bairro") or "").strip() or None
    cep = "".join(c for c in str(dados.get("cep") or "") if c.isdigit()) or None
    cidade = (dados.get("municipio") or "").strip() or None
    uf = (dados.get("uf") or "").strip() or None
    cnae_desc = (dados.get("cnae_fiscal_descricao") or "").strip() or None
    ramo = classificar_ramo(cnae_desc)
    return {"nome": nome, "endereco": endereco, "bairro": bairro, "cep": cep,
            "cidade": cidade, "uf": uf, "cnae": cnae_desc, "ramo": ramo}


# mapeia palavras-chave do CNAE -> ramo (departamento) da loja.
# o CNAE da Receita descreve a atividade; classificamos em ramos uteis pra comparar.
_RAMOS = [
    ("Farmacia",     ("farmac", "drogaria", "medicament")),
    ("Restaurante",  ("restaurante", "lanchonete", "refeicao", "bar ", "fast",
                      "pizzaria", "sorveteria", "casas de cha", "sucos")),
    ("Padaria",      ("padaria", "panific", "confeitaria")),
    ("Acougue",      ("acougue", "carnes", "frigorific", "casa de carne",
                      "casa de carnes")),
    ("Hortifruti",   ("hortifruti", "horti", "frutas", "verdura", "legume",
                      "hortigranjeiro")),
    ("Supermercado", ("supermerc", "hipermerc", "minimerc", "mercearia",
                      "mercadorias em geral", "alimenticios", "varejista de merc",
                      "atacadista de alimentos", "alimentos")),
]


def classificar_ramo(cnae_desc: str | None) -> str | None:
    """Classifica a loja num ramo (departamento) a partir da descricao do CNAE.
    Ex: 'Comercio varejista de produtos farmaceuticos' -> 'Farmacia'. None se nao
    casar (a loja fica sem ramo, ou usa a categoria do Claude como reserva)."""
    if not cnae_desc:
        return None
    import unicodedata
    t = unicodedata.normalize("NFKD", cnae_desc.lower())
    t = "".join(c for c in t if not unicodedata.combining(c))
    for ramo, chaves in _RAMOS:
        if any(k in t for k in chaves):
            return ramo
    return None


# mapeia a CATEGORIA do lancamento (que o Claude define) -> ramo, como RESERVA
# quando nao ha' CNPJ pra consultar o CNAE.
_CATEGORIA_RAMO = {
    "Mercado": "Supermercado",
    "Saude": "Farmacia",
    "Restaurante": "Restaurante",
}


def ramo_por_categoria(categoria: str | None) -> str | None:
    """Reserva: deriva o ramo da categoria do lancamento quando nao temos CNAE."""
    return _CATEGORIA_RAMO.get((categoria or "").strip())
