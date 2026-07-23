"""Configuração de NICHOS de venda (Zaq Vendas / Fornecedor).

Cada nicho define o VOCABULÁRIO que o lojista vê ao cadastrar produtos:
- unidades  -> como o produto é medido/vendido (kg, peça, caixa, ml...)
- categorias -> como o produto é agrupado (fruta, masculino, medicamento...)

Isto é config EM CÓDIGO de propósito: enquanto nós definimos os nichos, um mapa
Python é rápido, versionado no Git e sob controle total. Se um dia quisermos que
o admin/cliente gerencie nichos pela tela sem deploy, migra-se para uma tabela —
as funções abaixo (unidades_do_nicho / categorias_do_nicho / ...) viram a única
coisa a trocar, sem mexer em quem as consome.

O nicho de uma conta é guardado em contas.nicho_id (FK -> nichos). A tabela
`nichos` (migração 031) tem: id, nome, slug, tipo ('produto'|'servico'). Este
mapa é indexado pelo SLUG do nicho, casando com a tabela.

IMPORTANTE: a primeira unidade e a primeira categoria de cada nicho são tratadas
como o PADRÃO (default) quando o lojista não escolhe.
"""
from __future__ import annotations


# ---------------------------------------------------------------------------
# O MAPA: slug do nicho -> {label, unidades, categorias}
# ---------------------------------------------------------------------------
# 'label' é só um nome amigável pra UI. 'unidades' e 'categorias' são as listas
# que aparecem nos selects do cadastro de produto. A ordem importa: [0] é o padrão.
NICHOS: dict[str, dict] = {
    # ---- NICHOS DE PRODUTO (tem estoque: saldo sobe/desce) ----
    "hortifruti": {
        "label": "Hortifrúti",
        "vende_produto": True, "vende_servico": False,
        "unidades": ["kg", "duzia", "maco", "unidade", "bandeja", "litro", "pacote"],
        "categorias": ["fruta", "verdura", "legume", "tempero", "outro"],
    },
    "vestuario": {
        "label": "Vestuário / Acessórios",
        "vende_produto": True, "vende_servico": False,
        "unidades": ["peca", "par", "unidade"],
        "categorias": ["masculino", "feminino", "infantil", "calcado", "acessorio", "outro"],
    },
    "minimercado": {
        "label": "Minimercado / Mercearia",
        "vende_produto": True, "vende_servico": False,
        "unidades": ["unidade", "kg", "pacote", "litro", "caixa", "fardo"],
        "categorias": ["alimento", "bebida", "limpeza", "higiene", "outro"],
    },
    "alimentacao": {
        "label": "Alimentação / Lanche",
        "vende_produto": True, "vende_servico": False,
        "unidades": ["unidade", "porcao", "prato", "litro"],
        "categorias": ["salgado", "doce", "bebida", "prato", "combo", "outro"],
    },
    "farmacia": {
        "label": "Farmácia / Saúde",
        "vende_produto": True, "vende_servico": False,
        "unidades": ["caixa", "unidade", "frasco", "ml", "comprimido"],
        "categorias": ["medicamento", "higiene", "infantil", "beleza", "outro"],
    },
    "beleza": {
        "label": "Beleza / Cosméticos",
        "vende_produto": True, "vende_servico": False,
        "unidades": ["unidade", "frasco", "kit"],
        "categorias": ["cabelo", "pele", "maquiagem", "perfume", "unha", "outro"],
    },
    "generico": {
        "label": "Outro / Genérico",
        "vende_produto": True, "vende_servico": False,
        "unidades": ["unidade", "kg", "caixa", "pacote", "litro"],
        "categorias": [],  # livre: o lojista digita a categoria que quiser
    },
    # ---- NICHOS DE SERVIÇO (nao tem estoque: presta e registra) ----
    "consultoria": {
        "label": "Consultoria / Assessoria empresarial",
        "vende_produto": False, "vende_servico": True,
        "unidades": ["mensal", "hora", "projeto", "diagnostico", "avulso"],
        "categorias": ["gestao", "financeira", "rh", "marketing", "processos", "outro"],
    },
    "tecnologia": {
        "label": "Tecnologia / Software",
        "vende_produto": False, "vende_servico": True,
        "unidades": ["mensal", "projeto", "hora", "sprint", "licenca"],
        "categorias": ["desenvolvimento", "suporte", "infra", "consultoria", "licenca", "outro"],
    },
    "advocacia": {
        "label": "Advocacia / Escritório de advocacia",
        "vende_produto": False, "vende_servico": True,
        "unidades": ["mensal", "honorario", "processo", "hora", "avulso"],
        "categorias": ["consultivo", "contencioso", "trabalhista", "civel", "criminal", "tributario", "outro"],
    },
    "arquitetura": {
        "label": "Arquitetura / Engenharia",
        "vende_produto": False, "vende_servico": True,
        "unidades": ["projeto", "etapa", "medicao", "hora", "m2", "mensal"],
        "categorias": ["projeto", "execucao", "reforma", "laudo", "acompanhamento", "outro"],
    },
    "agencia": {
        "label": "Agência de marketing / publicidade",
        "vende_produto": False, "vende_servico": True,
        "unidades": ["mensal", "projeto", "campanha", "hora", "avulso"],
        "categorias": ["social_media", "trafego", "criacao", "site", "consultoria", "outro"],
    },
    "salao": {
        "label": "Beleza / Salão (serviço)",
        "vende_produto": False, "vende_servico": True,
        "unidades": ["sessao", "hora", "pacote"],
        "categorias": ["cabelo", "unha", "estetica", "barba", "outro"],
    },
    "educacao": {
        "label": "Educação / Aulas",
        "vende_produto": False, "vende_servico": True,
        "unidades": ["aula", "hora", "modulo", "pacote", "mensal"],
        "categorias": ["reforco", "idioma", "musica", "curso", "outro"],
    },
    "servicos_gerais": {
        "label": "Serviços gerais",
        "vende_produto": False, "vende_servico": True,
        "unidades": ["servico", "hora", "diaria", "visita", "orcamento"],
        "categorias": ["manutencao", "reparo", "instalacao", "limpeza", "outro"],
    },
    "contabilidade": {
        "label": "Contabilidade / Escritório contábil",
        "vende_produto": False, "vende_servico": True,
        "unidades": ["mensal", "honorario", "avulso", "hora", "servico"],
        "categorias": ["contabil", "fiscal", "folha", "abertura_empresa",
                       "societario", "imposto_renda", "consultoria", "outro"],
    },
    # ---- NICHOS MISTOS (vendem PRODUTO e SERVICO) ----
    "oficina": {
        "label": "Oficina / Auto",
        "vende_produto": True, "vende_servico": True,
        "unidades": ["peca", "unidade", "litro", "hora", "servico", "orcamento"],
        "categorias": ["peca", "acessorio", "oleo_fluido", "mao_de_obra", "revisao", "outro"],
    },
    "petshop": {
        "label": "Petshop",
        "vende_produto": True, "vende_servico": True,
        "unidades": ["unidade", "kg", "pacote", "sessao", "hora", "banho"],
        "categorias": ["racao", "acessorio", "medicamento", "banho_tosa", "veterinario", "outro"],
    },
    "salao_completo": {
        "label": "Salão completo (produto + serviço)",
        "vende_produto": True, "vende_servico": True,
        "unidades": ["unidade", "frasco", "sessao", "hora", "pacote"],
        "categorias": ["cosmetico", "cabelo", "unha", "estetica", "barba", "outro"],
    },
    "assistencia": {
        "label": "Assistência técnica",
        "vende_produto": True, "vende_servico": True,
        "unidades": ["peca", "unidade", "servico", "hora", "orcamento"],
        "categorias": ["peca", "acessorio", "conserto", "diagnostico", "outro"],
    },
}

# União de TODAS as unidades de todos os nichos. Serve pra validação frouxa no
# banco (o check foi removido; aqui garantimos que uma unidade conhecida passa).
TODAS_UNIDADES: set[str] = {
    u for cfg in NICHOS.values() for u in cfg["unidades"]
}

# Nicho usado quando a conta não tem nicho definido (fallback seguro).
NICHO_PADRAO = "generico"


# ---------------------------------------------------------------------------
# HELPERS de leitura (é isto que o resto do sistema usa)
# ---------------------------------------------------------------------------
def nicho_existe(slug: str) -> bool:
    return (slug or "").strip().lower() in NICHOS


def config_do_nicho(slug: str | None) -> dict:
    """Devolve a config do nicho (ou a do genérico se não achar)."""
    s = (slug or "").strip().lower()
    return NICHOS.get(s, NICHOS[NICHO_PADRAO])


def unidades_do_nicho(slug: str | None) -> list[str]:
    """Lista de unidades válidas para o nicho."""
    return list(config_do_nicho(slug)["unidades"])


def categorias_do_nicho(slug: str | None) -> list[str]:
    """Lista de categorias sugeridas para o nicho (pode ser vazia = livre)."""
    return list(config_do_nicho(slug)["categorias"])


def unidade_padrao(slug: str | None) -> str:
    """Primeira unidade do nicho = o padrão."""
    us = unidades_do_nicho(slug)
    return us[0] if us else "unidade"


def unidade_valida_para(slug: str | None, unidade: str) -> bool:
    """A unidade pertence ao vocabulário do nicho?"""
    u = (unidade or "").strip().lower()
    return u in {x.lower() for x in unidades_do_nicho(slug)}


def label_do_nicho(slug: str | None) -> str:
    return config_do_nicho(slug)["label"]


def lista_nichos() -> list[dict]:
    """Todos os nichos pra montar um select: [{slug, label, ...}, ...]."""
    return [{"slug": s, "label": cfg["label"],
             "vende_produto": bool(cfg.get("vende_produto", True)),
             "vende_servico": bool(cfg.get("vende_servico", False))}
            for s, cfg in NICHOS.items()]


# ---------------------------------------------------------------------------
# O QUE O NICHO VENDE: produto (tem estoque), servico (sem estoque), ou ambos
# ---------------------------------------------------------------------------
def vende_produto(slug: str | None) -> bool:
    """O nicho vende PRODUTO (tem estoque)? Default True."""
    return bool(config_do_nicho(slug).get("vende_produto", True))


def vende_servico(slug: str | None) -> bool:
    """O nicho vende SERVICO (sem estoque)? Default False."""
    return bool(config_do_nicho(slug).get("vende_servico", False))


def eh_misto(slug: str | None) -> bool:
    """Vende os DOIS (produto e servico)?"""
    return vende_produto(slug) and vende_servico(slug)


# --- compat: alguns lugares ainda pensam em 'tipo' unico. Derivamos do par. ---
def tipo_do_nicho(slug: str | None) -> str:
    """Compat: 'servico' se vende servico e NAO produto; senao 'produto'.
    Prefira vende_produto()/vende_servico() pra nichos mistos."""
    if vende_servico(slug) and not vende_produto(slug):
        return "servico"
    return "produto"


def eh_servico(slug: str | None) -> bool:
    """Compat: True so pra servico PURO. Nichos mistos retornam False aqui —
    use vende_servico() se quiser saber se oferece servico."""
    return vende_servico(slug) and not vende_produto(slug)


def lista_nichos_por_tipo(tipo: str) -> list[dict]:
    """Nichos que oferecem determinado tipo. 'produto' -> todos que vendem
    produto (inclui mistos); 'servico' -> todos que vendem servico (inclui
    mistos). [{slug, label}, ...]."""
    if tipo == "servico":
        pred = vende_servico
    else:
        pred = vende_produto
    return [{"slug": s, "label": cfg["label"]}
            for s, cfg in NICHOS.items() if pred(s)]


# ---------------------------------------------------------------------------
# LABELS amigáveis de unidade (pra exibição). Mantém curto; cai no próprio
# nome quando não houver tradução.
# ---------------------------------------------------------------------------
_UNIDADE_LABEL = {
    "kg": "kg", "duzia": "dúzia", "maco": "maço", "unidade": "un",
    "bandeja": "bandeja", "litro": "litro", "pacote": "pacote",
    "peca": "peça", "par": "par", "caixa": "caixa", "fardo": "fardo",
    "porcao": "porção", "prato": "prato", "frasco": "frasco", "ml": "ml",
    "comprimido": "comprimido", "kit": "kit",
    "hora": "hora", "modulo": "módulo", "projeto": "projeto", "mensal": "mensal",
    "sessao": "sessão", "aula": "aula", "servico": "serviço", "diaria": "diária",
    "visita": "visita", "orcamento": "orçamento",
    "honorario": "honorário", "avulso": "avulso",
    "processo": "processo", "diagnostico": "diagnóstico", "sprint": "sprint",
    "licenca": "licença", "etapa": "etapa", "medicao": "medição", "m2": "m²",
    "campanha": "campanha",
}


def label_unidade(unidade: str | None) -> str:
    u = (unidade or "").strip().lower()
    return _UNIDADE_LABEL.get(u, u or "un")


# ---------------------------------------------------------------------------
# PERSONA POR NICHO — o "sentimento" do sistema molda ao ramo da empresa.
# Cada bloco é ANEXADO ao prompt PJ (ver tools_pj.bloco_persona_pj) e ensina o
# bot a falar/ajudar como aquele negócio. Vazio ('') = sem molde (persona PJ
# genérica). Adicionar um ramo aqui é o único passo pra ele "sentir" diferente.
# ---------------------------------------------------------------------------
def _molde_servico(ramo: str, com_quem: str, faturamento: str, a_pagar: str) -> str:
    """Molde comum dos ramos de SERVIÇO que faturam por honorário/mensalidade
    (advocacia, consultoria, arquitetura, agência, tecnologia). Mesma espinha da
    contabilidade: recebível recorrente por cliente + carteira + proativo."""
    return (
        f"RAMO DA EMPRESA: {ramo}. Você fala com {com_quem} — vá direto, sem "
        f"explicar o básico da área (ele é o especialista). Você é o braço "
        f"OPERACIONAL rápido do negócio dele.\n"
        f"- FATURAMENTO: {faturamento} O nome do cliente vai na CONTRAPARTE; "
        f"mensalidade/retainer -> título a RECEBER RECORRENTE (recorrente=true, o "
        f"sistema projeta o mês seguinte); recebimento pontual -> a receber avulso.\n"
        f"- A PAGAR (só custos SEUS): {a_pagar}\n"
        "- A folha aqui é a da SUA equipe (não a dos clientes).\n"
        "- CARTEIRA: \"quem me deve\", \"clientes do mês\" -> consultar_empresa. Ao "
        "receber de um cliente que ainda não está na base, ofereça cadastrá-lo "
        "(cadastrar_cliente) e ligue o título à ficha dele.\n"
        "- CATEGORIA da receita: 'Honorarios' (recorrente/serviço principal) ou "
        "'Consultoria' (projeto/assessoria pontual).\n"
        "- SEJA PROATIVO (sem encher): se notar atrasado na carteira, avise em 1 "
        "linha quem está devendo e ofereça cobrar (Pix); no início do mês lembre "
        "dos recorrentes. Nunca insista nem repita a cada mensagem."
    )


_PERSONAS_NICHO: dict[str, str] = {
    "contabilidade": (
        "RAMO DA EMPRESA: ESCRITÓRIO DE CONTABILIDADE. Você fala com um CONTADOR — "
        "vá direto e não explique o básico de contabilidade (ele sabe mais que você "
        "disso). Aqui você é o braço OPERACIONAL rápido do escritório dele.\n"
        "- HONORÁRIOS (o faturamento é recorrente): quando ele falar de mensalidade/"
        "honorário de cliente (\"o cliente X paga 800/mês\", \"fechei honorário de "
        "1200 com a padaria\"), registre como título a RECEBER RECORRENTE "
        "(recorrente=true) — o sistema já projeta o mês seguinte. O nome do cliente "
        "vai na contraparte.\n"
        "- GUIAS/IMPOSTOS do escritório (DAS/Simples, ISS): título a PAGAR.\n"
        "- A folha aqui é a do PRÓPRIO escritório (os funcionários dele). A folha "
        "dos clientes ele cuida por fora — não confunda nem se ofereça pra isso.\n"
        "- \"quem me deve\", \"honorários do mês\", \"carteira de clientes\" -> "
        "consultar_empresa / títulos a receber.\n"
        "- Ao lançar honorário de um cliente que AINDA NÃO está na base, ofereça "
        "cadastrá-lo (cadastrar_cliente) — assim o título fica ligado à ficha dele "
        "(a carteira). Se o dono topar, cadastre e crie o título com o nome do "
        "cliente na contraparte.\n"
        "- CATEGORIAS (com a cara do escritório): RECEITA de honorário/mensalidade "
        "-> categoria 'Honorarios'; serviço pontual/assessoria -> 'Consultoria'. "
        "DESPESA: sistema/software contábil e assinaturas -> 'Assinaturas'; "
        "anuidade CRC, certificado digital, guias -> 'Impostos'; terceiros/estagiário "
        "avulso -> 'Servicos'.\n"
        "- SEJA PROATIVO (sem encher): se ele perguntar do mês ou você notar "
        "atrasados na carteira, avise em 1 linha quem está devendo honorário e "
        "ofereça cobrar (Pix). No começo do mês, se houver honorários recorrentes, "
        "pode lembrar de lançá-los. Nunca insista nem repita a cada mensagem."
    ),
    "advocacia": _molde_servico(
        "ESCRITÓRIO DE ADVOCACIA", "um(a) advogado(a)",
        "honorário contratual/assessoria é MENSAL; honorário de êxito e atos "
        "pontuais são avulsos.",
        "anuidade da OAB, custas e DAS/impostos -> 'Impostos'; sistemas jurídicos/"
        "assinaturas -> 'Assinaturas'."),
    "consultoria": _molde_servico(
        "CONSULTORIA / ASSESSORIA EMPRESARIAL", "um(a) consultor(a)",
        "retainer/mensalidade de cliente é o forte; diagnóstico ou projeto pontual "
        "é avulso.",
        "ferramentas/assinaturas -> 'Assinaturas'; DAS/impostos -> 'Impostos'."),
    "arquitetura": _molde_servico(
        "ARQUITETURA / ENGENHARIA", "um(a) arquiteto(a)/engenheiro(a)",
        "honorário por ETAPA/MEDIÇÃO do projeto; acompanhamento de obra pode ser "
        "mensal.",
        "anuidade CAU/CREA e impostos -> 'Impostos'; softwares (CAD/BIM) -> "
        "'Assinaturas'."),
    "agencia": _molde_servico(
        "AGÊNCIA DE MARKETING / PUBLICIDADE", "o dono da agência",
        "FEE MENSAL de cliente é o forte; campanha/projeto é avulso. ATENÇÃO: verba "
        "de tráfego pago (ADS) é DO CLIENTE — não é sua receita nem seu custo, só "
        "entra se você de fato repassa.",
        "ferramentas/assinaturas -> 'Assinaturas'; DAS/impostos -> 'Impostos'."),
    "tecnologia": _molde_servico(
        "TECNOLOGIA / SOFTWARE", "o dono de uma software house / TI",
        "mensalidade de SaaS/suporte é recorrente; projeto ou sprint é avulso.",
        "infra (nuvem/servidores) e licenças -> 'Assinaturas'; DAS/impostos -> "
        "'Impostos'."),
}


def persona_do_nicho(slug: str | None) -> str:
    """Bloco de persona específico do nicho pra anexar ao prompt PJ (ou '' se o
    nicho não tem molde próprio — cai na persona PJ genérica)."""
    return _PERSONAS_NICHO.get((slug or "").strip().lower(), "")


# Rótulo do "a receber em aberto" na ficha do cliente, POR NICHO. Varejo pensa em
# "fiado"; profissional liberal em "honorários"; agência/SaaS em "mensalidades";
# o resto em "a receber". Molda a linguagem ao ramo.
_ROTULO_RECEBER = {
    "contabilidade": "Honorários",
    "advocacia": "Honorários",
    "consultoria": "Honorários",
    "arquitetura": "Honorários",
    "agencia": "Mensalidades",
    "tecnologia": "Mensalidades",
}


def rotulo_receber(slug: str | None) -> str:
    """Como chamar o 'a receber em aberto' de um cliente, conforme o ramo.
    Ex.: contabilidade -> 'Honorários'; quem vende produto -> 'Fiado'; senão
    'A receber'."""
    s = (slug or "").strip().lower()
    if s in _ROTULO_RECEBER:
        return _ROTULO_RECEBER[s]
    return "Fiado" if vende_produto(s) else "A receber"


def tem_persona(slug: str | None) -> bool:
    return bool(persona_do_nicho(slug))
