"""O PERFIL do Raio-X: o que a empresa vende decide o que o Raio-X mede.

Aprovado em 05/09/2026 (docs/mockups/raio_x_por_nicho.html), depois de o dono
apontar que o Raio-X das Peças 1 a 3 assumia festa pra toda conta: a ZAQ, que
vende sistema por mensalidade, via "tipo de festa" e "dia da festa" em 43 leads
que nunca terão data.

Três perfis, lidos dos portões que JÁ EXISTEM (nada novo pra configurar):

  eventos      modo do orçamento é 'evento' (finance.vendas.modo_por_nicho).
               Festa com data, visita ao espaço, contrato de locação.
  recorrente   vende serviço sem ser evento — ou NÃO ESCOLHEU NICHO (decisão do
               dono, 05/09: conta sem nicho cai aqui, que é o perfil sem festa,
               e a tela pede pra escolher o nicho em Empresa).
               Setup + mensalidade, reunião, lead PJ com segmento e porte.
  produto      vende produto e não vende serviço: caixa e pedido, sem funil nem
               vendedor. O Raio-X não se aplica — some do menu, o aviso não sai.

O perfil carrega o VOCABULÁRIO (visita × reunião, festa × serviço), quais
FILTROS e BLOCOS o painel do dono mostra, as FAIXAS do "responda hoje" no app e
a LISTA DE MOTIVOS de perda. Tudo que antes estava fixo em código lê daqui, e é
aqui que os testes conferem que nenhuma palavra de festa vaza pro recorrente.
"""
from __future__ import annotations

import re

from finance import nichos as _n

PERFIS = ("eventos", "recorrente", "produto")

#: por que perdeu — a lista completa (check da migração 213). O perfil escolhe seis.
MOTIVOS_TODOS = (
    ("sumiu_apos_proposta", "Sumiu depois da proposta"),
    ("data_indisponivel", "Data indisponível"),
    ("ficou_com_atual", "Ficou com o fornecedor atual"),
    ("achou_caro", "Achou caro"),
    ("fora_do_escopo", "Fora do escopo"),
    ("sem_interesse", "Sem interesse"),
    ("outro", "Outro"),
)
_MOTIVOS_POR_PERFIL = {
    "eventos": ("sumiu_apos_proposta", "data_indisponivel", "achou_caro", "fora_do_escopo", "sem_interesse", "outro"),
    "recorrente": ("sumiu_apos_proposta", "ficou_com_atual", "achou_caro", "fora_do_escopo", "sem_interesse", "outro"),
    "produto": (),
}

_PERFIS = {
    "eventos": {
        "chave": "eventos", "rotulo": "eventos",
        "vocab": {"data": True, "compromisso": "visita", "compromissos": "visitas",
                  "compromisso_kpi": "visitas que aconteceram", "pedido": "festa"},
        "filtros": ("periodo", "vendedor", "tipo", "mes", "dia", "conv", "origem", "hora"),
        "blocos": ("demanda_agenda", "dia_festa", "tipos", "ciclo", "perdas", "hora"),
        "faixas": ("data_abriu", "pergunta", "festa", "proposta", "toque", "visita"),
    },
    "recorrente": {
        "chave": "recorrente", "rotulo": "serviço recorrente",
        "vocab": {"data": False, "compromisso": "reunião", "compromissos": "reuniões",
                  "compromisso_kpi": "reuniões que aconteceram", "pedido": "serviço"},
        "filtros": ("periodo", "vendedor", "segmento", "porte", "uf", "servico", "origem", "hora"),
        "blocos": ("mrr", "segmentos", "servicos", "reunioes", "ciclo", "perdas", "hora"),
        "faixas": ("pergunta", "proposta", "toque", "visita"),
    },
    "produto": {
        "chave": "produto", "rotulo": "produto",
        "vocab": {"data": False, "compromisso": "compromisso", "compromissos": "compromissos",
                  "compromisso_kpi": "compromissos que aconteceram", "pedido": "pedido"},
        "filtros": (), "blocos": (), "faixas": (),
    },
}


def perfil_por_nicho(slug: str | None) -> str:
    """eventos · recorrente · produto. Sem nicho → recorrente (ver o docstring)."""
    from finance.vendas import modo_por_nicho
    s = (slug or "").strip().lower()
    if not s or not _n.nicho_existe(s):
        return "recorrente"
    if modo_por_nicho(s) == "evento":
        return "eventos"
    if _n.vende_servico(s):
        return "recorrente"
    return "produto"


def perfil(slug: str | None) -> dict:
    """O perfil inteiro pra um slug de nicho (puro, sem banco)."""
    chave = perfil_por_nicho(slug)
    p = dict(_PERFIS[chave])
    p["vocab"] = dict(p["vocab"])
    p["motivos"] = tuple((k, r) for k, r in MOTIVOS_TODOS if k in _MOTIVOS_POR_PERFIL[chave])
    p["nicho"] = (slug or "").strip().lower() or None
    p["nicho_escolhido"] = bool(p["nicho"]) and _n.nicho_existe(p["nicho"])
    p["aplica"] = chave != "produto"
    return p


def perfil_da_conta(pool, conta_id: int) -> dict:
    """O perfil da conta, lido do nicho dela. Tolerante: se a leitura falhar,
    recorrente (o perfil sem festa) — errar pra esse lado não mostra festa a
    quem não vende festa."""
    slug = None
    try:
        from finance import empresa as emp
        slug = (emp.obter_dados_empresa(pool, conta_id) or {}).get("nicho")
    except Exception:  # noqa: BLE001
        slug = None
    return perfil(slug)


def motivos(chave_perfil: str) -> tuple:
    return tuple((k, r) for k, r in MOTIVOS_TODOS if k in _MOTIVOS_POR_PERFIL.get(chave_perfil, ()))


def rotulo_motivo(chave: str | None) -> str:
    return dict(MOTIVOS_TODOS).get(chave or "", "sem motivo")


# ---------------------------------------------------------------- o segmento em famílias

#: o CNAE da Receita é longo ("Atividades de estética e outros serviços de
#: cuidados com a beleza"). Pra tela e pro filtro, famílias curtas. A ordem
#: importa: a primeira que casar vence ("clínica de estética" é estética).
FAMILIAS_SEGMENTO = (
    ("estetica", "Estética / beleza", r"est[ée]tic|beleza|cabele|barbe|manicure|salão|salao"),
    ("clinica", "Clínica / saúde", r"odont|dentist|m[ée]dic|cl[ií]nic|sa[úu]de|fisiot|psic|veterin|farm[áa]c|laborat"),
    ("loja", "Loja / comércio", r"\bloja|com[ée]rcio|varej|atacad|mercad|supermerc|papelar|\bótica|\botica|boutique|magazine"),
    ("alimentacao", "Alimentação", r"restaur|lanch|\bbar\b|pizz|padar|confeit|doçar|docar|aliment|cafeter|hamburg|sorvet"),
    ("eventos", "Eventos", r"evento|festa|buffet|cerimon|casament|espet[áa]culo|congress|feira"),
    ("escritorio", "Escritório / serviços", r"contab|advoc|advog|consult|assessor|imobili|corretor|seguro|arquitet|engenh|marketing|publicid|ag[êe]ncia"),
    ("educacao", "Educação", r"educa|escola|ensino|curso|aula|faculd|universid"),
    ("construcao", "Construção", r"constru|obra|reforma|pintura|el[ée]tric|hidr[áa]ul"),
    ("tecnologia", "Tecnologia", r"tecnolog|software|sistema|inform[áa]tic|\bti\b|desenvolv"),
    ("industria", "Indústria", r"ind[úu]stri|fabrica|f[áa]brica|manufat"),
    ("auto", "Auto / oficina", r"oficina|autom|ve[íi]cul|\bcarro|moto\b|pneu|lava.?jato"),
)
_FAMILIAS_RE = [(k, r, re.compile(rx, re.I)) for k, r, rx in FAMILIAS_SEGMENTO]


def familia_segmento(segmento: str | None) -> tuple[str, str]:
    """(chave, rótulo) da família do segmento. Vazio → ('sem', 'sem segmento');
    sem família → ('outro', 'Outro')."""
    s = (segmento or "").strip()
    if not s:
        return "sem", "sem segmento"
    for k, r, rx in _FAMILIAS_RE:
        if rx.search(s):
            return k, r
    return "outro", "Outro"


def _pg(rx: str) -> str:
    """O mesmo padrão pro Postgres: a borda de palavra lá é barra-y, não barra-b."""
    return rx.replace(r"\b", r"\y")


def regex_da_familia(chave: str) -> str | None:
    """O padrão (pra `~*` no Postgres) da família, ou None se não existe."""
    for k, _, rx in FAMILIAS_SEGMENTO:
        if k == chave:
            return _pg(rx)
    return None


def familias() -> tuple:
    """As famílias como opções de filtro: (chave, rótulo), + outro e sem."""
    return tuple((k, r) for k, r, _ in FAMILIAS_SEGMENTO) + (("outro", "Outro"), ("sem", "sem segmento"))


#: o porte como a Receita devolve, em três faixas + sem
PORTES = (("me", "Microempresa", r"^micro"), ("epp", "Pequeno porte", r"pequeno"),
          ("demais", "Demais", r"^demais|m[ée]dio|grande"))


def chave_porte(porte: str | None) -> str:
    s = (porte or "").strip()
    if not s:
        return "sem"
    for k, _, rx in PORTES:
        if re.search(rx, s, re.I):
            return k
    return "demais"


def regex_do_porte(chave: str) -> str | None:
    for k, _, rx in PORTES:
        if k == chave:
            return _pg(rx)
    return None
