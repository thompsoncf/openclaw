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
from datetime import time

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

# A LISTA DE MOTIVOS QUE CADA NICHO RECEBE AO ABRIR A TELA PELA PRIMEIRA VEZ
# (migração 235). Daqui em diante é da CONTA: o dono liga, desliga, renomeia,
# reordena e acrescenta o que for dele, sem deploy.
#
# Os dez de eventos são os que o dono da Prime pediu em 11/09/2026, na ordem dele.
# `MOTIVOS_TODOS` acima continua existindo pro Raio-X agregar o histórico de quem
# foi perdido ANTES desta migração — apagar de lá reescreveria o passado.
#
# (chave, rótulo, exige descrição)
_SEMENTE_MOTIVOS = {
    "eventos": (
        ("nao_respondeu", "Não respondeu — após as tentativas de follow-up", False),
        ("achou_caro", "Preço — acima do orçamento dele", False),
        ("fechou_concorrente", "Fechou com concorrente", False),
        ("desistiu_evento", "Desistiu / cancelou o evento", False),
        ("data_indisponivel", "Data indisponível", False),
        ("localizacao", "Localização / distância", False),
        ("fora_do_escopo", "Estrutura não atendeu à necessidade", False),
        ("condicao_pagamento", "Condição de pagamento", False),
        ("evento_adiado", "Evento adiado, sem nova data", False),
        ("outro", "Outro", True),
    ),
    "recorrente": (
        ("nao_respondeu", "Não respondeu — após as tentativas de follow-up", False),
        ("achou_caro", "Preço — acima do orçamento dele", False),
        ("ficou_com_atual", "Ficou com o fornecedor atual", False),
        ("sumiu_apos_proposta", "Sumiu depois da proposta", False),
        ("fora_do_escopo", "Fora do escopo do que vendemos", False),
        ("condicao_pagamento", "Condição de pagamento", False),
        ("sem_interesse", "Sem interesse", False),
        ("outro", "Outro", True),
    ),
    # produto não tem funil nem vendedor: não há perda de lead pra motivar
    "produto": (),
}


def semente_motivos(chave_perfil: str) -> tuple:
    """(chave, rótulo, exige_descricao) que a conta recebe na primeira abertura."""
    return _SEMENTE_MOTIVOS.get(chave_perfil) or ()


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

# ---------------------------------------------------------------- o funil do perfil
#
# O PADRÃO DE FUNIL DE CADA NICHO, e por que ele mora aqui.
#
# Até 11/09/2026 cada número da régua era `NOT NULL DEFAULT <valor>` no banco: o
# valor era COPIADO pra dentro da conta no dia em que ela nascia. Medido nesse dia:
# as 6 contas com config carregavam exatamente os mesmos 17 valores, e nenhuma
# delas tinha escolhido nenhum. O efeito de copiar é que melhorar o padrão depois
# não alcança ninguém — cada conta carrega a cópia velha e, olhando o banco, parece
# ter escolhido aquilo.
#
# Agora a coluna nasce NULL e NULL quer dizer "usa o padrão do meu perfil". Quem
# nunca mexeu recebe a melhoria no mesmo dia; quem mexeu mantém a escolha. É a
# mesma ideia do vocabulário e dos motivos logo acima: o que é do NICHO fica aqui,
# versionado e revisado, e o que é da EMPRESA fica no banco.
#
# Os números de 'eventos' são os que já estavam em produção — este passo é só a
# mecânica, e mudar valor aqui muda o comportamento de quem herda. Os de
# 'recorrente' repetem por enquanto, com UMA diferença declarada: `fu_festa_dias`
# é None, porque quem vende mensalidade não tem segundo relógio. Isso era um `if`
# escondido no motor (`tem_data`); aqui vira propriedade do perfil, que é onde dá
# pra ler sem abrir o código do motor.
_FUNIL_POR_PERFIL = {
    "eventos": {
        "janela_dias": "1,2,3,4,5,6", "janela_abre": time(8, 0), "janela_fecha": time(19, 0),
        "sem_resposta_min": 120, "bola_nossa_min": 240, "bola_cliente_min": 4320,
        "escala_min": 240, "teto_avisos_dia": 5,
        "fu_proposta_dias": 3, "fu_toques_dias": "2,4,7,15", "fu_festa_dias": 30,
        "fu_teto_dia": 15,
    },
    "recorrente": {
        "janela_dias": "1,2,3,4,5", "janela_abre": time(8, 0), "janela_fecha": time(19, 0),
        "sem_resposta_min": 120, "bola_nossa_min": 240, "bola_cliente_min": 4320,
        "escala_min": 240, "teto_avisos_dia": 5,
        "fu_proposta_dias": 3, "fu_toques_dias": "2,4,7,15", "fu_festa_dias": None,
        "fu_teto_dia": 15,
    },
    # produto não tem funil nem vendedor (ver o docstring): nada a herdar.
    "produto": None,
}

#: as chaves que a conta pode sobrescrever. Os MODOS (gatilhos/cobranca/follow_up)
#: ficam de fora de propósito: 'off' é uma escolha, não uma herança — quem liga
#: uma automação está dizendo algo sobre a empresa dele, não sobre o ramo.
CHAVES_FUNIL = tuple(_FUNIL_POR_PERFIL["eventos"])


def funil_padrao(chave_perfil: str) -> dict:
    """O padrão de funil do perfil. Perfil sem funil (produto) devolve {}."""
    return dict(_FUNIL_POR_PERFIL.get(chave_perfil) or {})


def funil_resolvido(chave_perfil: str, da_conta: dict) -> tuple[dict, set]:
    """Junta o padrão do nicho com o que a conta escolheu.

    Devolve (valores, escolhidas) — `escolhidas` são as chaves que a CONTA gravou,
    e é o que a tela usa pra dizer "definido por você" em vez de "padrão do nicho".
    Sem isso o dono não tem como saber se um número é escolha dele ou herança, e
    "voltar ao padrão" vira um botão que ninguém sabe o que faz.
    """
    base = funil_padrao(chave_perfil)
    escolhidas = {k for k in CHAVES_FUNIL if da_conta.get(k) is not None}
    return dict(base, **{k: da_conta[k] for k in escolhidas}), escolhidas


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
