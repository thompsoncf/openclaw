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
    """Consulta o CNPJ na BrasilAPI e devolve {nome, endereco, bairro, cep, cidade, uf, email, telefone, ...}.
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
    # o CÓDIGO do CNAE e os secundários, além da descrição: é por eles que a
    # construtora com CNAE principal de engenharia deixa de virar arquitetura
    # (ver `nicho_do_cnae`)
    cnae_cod = dados.get("cnae_fiscal")
    secundarios = [s.get("codigo") for s in (dados.get("cnaes_secundarios") or [])
                   if isinstance(s, dict)]
    nicho = nicho_do_cnae(cnae_desc, cnae_cod, secundarios)
    ramo = "Construcao" if _construcao_pelo_codigo(cnae_cod, secundarios) \
        else classificar_ramo(cnae_desc)
    # contato: email e telefone vem do cadastro da Receita (pode estar
    # desatualizado) — usado so como SUGESTAO, o cliente confirma na tela.
    email = (dados.get("email") or "").strip().lower() or None
    ddd = (dados.get("ddd_telefone_1") or "").strip()
    telefone = _formata_telefone(ddd)
    return {"nome": nome, "endereco": endereco, "bairro": bairro, "cep": cep,
            "cidade": cidade, "uf": uf, "cnae": cnae_desc, "ramo": ramo,
            "nicho": nicho, "email": email, "telefone": telefone}


def _formata_telefone(bruto: str) -> str | None:
    """Formata o telefone da Receita. Ex.: '8632220000' -> '(86) 3222-0000'.
    O campo ddd_telefone_1 da BrasilAPI ja vem com DDD+numero juntos."""
    d = "".join(c for c in (bruto or "") if c.isdigit())
    if len(d) == 10:
        return f"({d[:2]}) {d[2:6]}-{d[6:]}"
    if len(d) == 11:
        return f"({d[:2]}) {d[2:7]}-{d[7:]}"
    return d or None


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
    # Loja de suplemento / nutrição esportiva. Vem ANTES de Supermercado porque
    # a chave "alimentos"/"alimenticios" de lá pega qualquer CNAE de comércio de
    # alimento — e uma loja de whey ia parar em mercearia.
    #
    # HONESTIDADE SOBRE O ALCANCE: o CNAE que a Receita costuma dar pra esse ramo
    # é o 4729-6/99 ("comércio varejista de produtos alimentícios em geral ou
    # especializado em produtos alimentícios não especificados anteriormente"),
    # que NÃO tem a palavra suplemento — é o caso da SUPER FIT (conta 16). Esse
    # continua caindo em Supermercado, e quem resolve é a escolha do nicho na tela
    # Empresa. Este ramo pega quem tem CNAE descritivo, que é o outro pedaço.
    ("Suplementos",  ("suplementos alimentares", "suplemento alimentar",
                      "suplementos nutricionais", "nutricao esportiva",
                      "produtos naturais", "casa de suplementos")),
    ("Supermercado", ("supermerc", "hipermerc", "minimerc", "mercearia",
                      "mercadorias em geral", "alimenticios", "varejista de merc",
                      "atacadista de alimentos", "alimentos")),
    ("Vestuario",    ("vestuario", "roupas", "confeccao", "artigos do vestuario",
                      "calcados", "moda", "boutique", "acessorios do vestuario")),
    # Clínica / atividade de saúde. Vem ANTES de Salao porque a chave "estetica"
    # de lá pegaria uma clínica que cita estética. Sem "veterin" de propósito:
    # clínica veterinária é do petshop. As chaves são as palavras que a Receita
    # usa nos CNAEs 8630-5 (atividade médica ambulatorial) e vizinhos.
    ("Clinica",      ("atividade medica", "atencao ambulatorial", "clinica",
                      "consultorio", "odontolog", "fisioterap", "psicolog",
                      "complementacao diagnostica")),
    # SERVICO de beleza (atende a pessoa) — vem ANTES de Beleza(produto) porque
    # "salao/cabeleireiro/manicure" é serviço, nao venda de cosmético.
    ("Salao",        ("salao", "cabelei", "manicure", "pedicure", "estetica",
                      "barbearia", "spa")),
    ("Beleza",       ("cosmetic", "perfumaria", "artigos de perfumaria")),
    # Contabilidade vem ANTES de Consultoria: "assessoria/consultoria contabil"
    # e' contabilidade, nao consultoria de TI. Palavras bem especificas do ramo.
    # Corretora de seguros. Vem ANTES de Agencia por causa de "agenciamento de
    # seguros" ("agenciamento" sozinho e' chave da Agencia) e antes de Consultoria,
    # que e' generica. Todas as chaves sao MULTI-PALAVRA de proposito: "corretor"
    # sozinho pegaria corretor de IMOVEIS (CNAE 6821-8/01), que nao e' este nicho,
    # e "seguros" sozinho pegaria a seguradora, que e' o outro lado do balcao.
    ("Seguros",      ("corretores e agentes de seguros", "corretor de seguros",
                      "corretora de seguros", "corretagem de seguros",
                      "agenciamento de seguros", "corretores de seguros",
                      "previdencia complementar")),
    ("Contabilidade",("contabil", "contabilidade", "escritorio contabil",
                      "escrituracao", "pericia contabil", "auditoria",
                      "assessoria contabil", "servicos contabeis")),
    # Ramos de SERVIÇO que faturam por honorário/mensalidade (a ordem importa:
    # os específicos vêm ANTES de Consultoria/Tecnologia, que são mais genéricos).
    ("Advocacia",    ("advocacia", "advogad", "atividades juridicas", "juridic",
                      "escritorio de advocacia", "servicos advocaticios")),
    # Construção civil (OBRAS) vem ANTES de Arquitetura: quem CONSTRÓI é obra;
    # Arquitetura fica com projeto/engenharia de projeto (design/laudo).
    ("Construcao",   ("construc", "obras", "empreiteira", "empreitada",
                      "edificac", "incorporac", "terraplenagem", "pavimentac",
                      "alvenaria", "obras de infraestrutura",
                      "instalacoes hidraulicas", "instalacoes eletricas")),
    ("Arquitetura",  ("arquitetur", "servicos de arquitetura", "servicos de engenharia",
                      "engenharia civil", "projetos de engenharia", "paisagismo",
                      "design de interiores")),
    ("Eventos",      ("organizacao de festas", "festas e eventos", "buffet",
                      "casa de festas", "casa de eventos", "cerimonial",
                      "decoracao de festas", "espaco para eventos",
                      "locacao de equipamentos para festas", "producao de eventos")),
    ("Agencia",      ("publicidade", "propaganda", "agencia de publicidade",
                      "marketing", "midias sociais", "gestao de midias",
                      "assessoria de imprensa", "agenciamento")),
    ("Tecnologia",   ("software", "sistemas", "desenvolvimento de programas",
                      "desenvolvimento de sistemas", "tecnologia da informacao",
                      "informatica", "programacao", "suporte tecnico", "hospedagem")),
    ("Consultoria",  ("consultoria", "assessoria", "gestao empresarial",
                      "consultoria em gestao", "consultoria empresarial")),
    ("Educacao",     ("educac", "ensino", "escola", "curso", "treinamento",
                      "aulas", "idiomas")),
    ("ServicosGerais",("manutencao", "reparacao", "instalacao", "reparo",
                       "servicos combinados", "limpeza predial")),
]


# ponte: ramo (de classificar_ramo) -> slug do nicho do modulo Vendas (nichos.py).
# varios ramos caem no mesmo nicho (ex: Padaria e Restaurante -> alimentacao).
_RAMO_NICHO = {
    "Farmacia":     "farmacia",
    "Restaurante":  "alimentacao",
    "Padaria":      "alimentacao",
    "Acougue":      "minimercado",
    "Hortifruti":   "hortifruti",
    "Suplementos":  "suplementos",
    "Supermercado": "minimercado",
    "Vestuario":    "vestuario",
    "Clinica":      "clinica",
    "Salao":        "salao",
    "Beleza":       "beleza",
    "Seguros":      "seguros",
    "Contabilidade": "contabilidade",
    "Advocacia":    "advocacia",
    "Construcao":   "construcao",
    "Arquitetura":  "arquitetura",
    "Eventos":      "eventos",
    "Agencia":      "agencia",
    "Tecnologia":   "tecnologia",
    "Consultoria":  "consultoria",
    "Educacao":     "educacao",
    "ServicosGerais": "servicos_gerais",
}


# O CÓDIGO DO CNAE, ANTES DA DESCRIÇÃO — só pra construção, por enquanto.
#
# A seção F da CNAE (divisões 41, 42 e 43) é construção: edifícios, obras de
# infraestrutura e os serviços especializados — pintura, elétrica, hidráulica,
# gesso, fundação. Pela descrição, metade se perdia: "Serviços de pintura de
# edifícios em geral" não casa com chave nenhuma, e "Instalação e manutenção
# elétrica" caía em serviços gerais pela palavra "manutenção".
#
# E o caso que fez isto nascer (PX2, conta 33, 25/09/2026): CNAE principal
# 71.12-0-00 "Serviços de engenharia", que pela descrição vira arquitetura, com
# 20 dos 28 secundários na seção F. Construtora com engenheiro responsável
# costuma registrar engenharia como principal e obra como secundário. Sem
# secundário de obra, engenharia continua arquitetura.
_DIVISOES_CONSTRUCAO = {"41", "42", "43"}
_ENGENHARIA = "7112000"


def _cnae7(codigo) -> str:
    """O código com 7 dígitos. A BrasilAPI manda inteiro, e inteiro perde o zero
    da frente (0111-3/01 chega como 111301)."""
    d = "".join(ch for ch in str(codigo or "") if ch.isdigit())
    return d.zfill(7) if d else ""


def _construcao_pelo_codigo(codigo, secundarios=()) -> bool:
    principal = _cnae7(codigo)
    if principal[:2] in _DIVISOES_CONSTRUCAO:
        return True
    return principal == _ENGENHARIA and any(
        _cnae7(s)[:2] in _DIVISOES_CONSTRUCAO for s in (secundarios or ()))


def nicho_do_cnae(cnae_desc: str | None, codigo=None, secundarios=()) -> str | None:
    """Deriva o SLUG do nicho (Vendas) a partir do CNAE.
    O código, quando vem, decide construção antes da descrição (ver
    `_construcao_pelo_codigo`); o resto segue pela descrição, via
    classificar_ramo. None se nao classificar (a tela cai no 'generico' ou o
    cliente escolhe)."""
    if _construcao_pelo_codigo(codigo, secundarios):
        return "construcao"
    ramo = classificar_ramo(cnae_desc)
    return _RAMO_NICHO.get(ramo) if ramo else None


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
