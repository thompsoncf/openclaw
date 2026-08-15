"""Catálogo de serviços por conta (multi-tenant).

Cada empresa monta o próprio catálogo do que vende — nome, setup, mensalidade e
custo. Substitui a lista de módulos que era chumbada no código. Empresa nova
começa VAZIA e cadastra o que vende.

Regras:
  • Escopo por conta_id (sagrado) — uma conta nunca vê/edita o catálogo de outra.
  • Exclusão é SOFT (ativo=false): orçamentos antigos referenciam o slug, então
    não apagamos de verdade.
  • slug é o identificador estável usado no orçamento (jsonb modulos). Único por
    conta; gerado a partir do nome.

Valores sempre em CENTAVOS aqui dentro (a tela converte de/para reais).
"""
from __future__ import annotations

import re

# Categorias do serviço no nicho EVENTOS. Servem pra agrupar os itens e mostrar
# o SUBTOTAL POR CATEGORIA na folha do orçamento — e são o mesmo vocabulário das
# receitas que a migração 143 criou no plano de contas, pra o que o cliente lê no
# papel bater com o que a empresa vê na DRE. A primeira é o padrão.
# Vocabulário do orçamento de EVENTO: o que a tela oferece e o que a folha
# imprime (a lista inteira, com a escolhida em destaque, como no formulário de
# papel). Um lugar só, pra tela e folha nunca divergirem.
TIPOS_EVENTO = ["Aniversário", "Casamento", "Confraternização", "Corporativo",
                "Infantil", "Suítes"]
TIPOS_CONTRATO = ["Locação de espaço", "Locação de móveis e utensílios",
                  "Serviços terceirizados"]

CATEGORIAS_EVENTOS = [
    "Locação de espaço",
    "Buffet",
    "Locação de móveis e utensílios",
    "Serviços terceirizados",
    "Outros",
]

# Modelo pronto (nicho tecnologia) — o mesmo catálogo que era chumbado. Serve de
# atalho pra quem não quer começar do zero. slug preservado pra casar com
# orçamentos já salvos que usavam esses ids.
STARTER_TECNOLOGIA = [
    {"slug": "atendimento", "nome": "Agente de Atendimento", "desc": "Atendimento 24/7 multicanal", "setup": 4500,  "mensal": 1200, "custo": 430},
    {"slug": "automacao",   "nome": "Automação de Processos", "desc": "Workflows e RPA",             "setup": 6000,  "mensal": 1500, "custo": 520},
    {"slug": "crm",         "nome": "CRM / Leads",            "desc": "Pipeline e gestão de leads",  "setup": 3500,  "mensal": 900,  "custo": 300},
    {"slug": "sdr",         "nome": "SDR Digital",            "desc": "Prospecção e qualificação",   "setup": 5000,  "mensal": 1400, "custo": 480},
    {"slug": "docs",        "nome": "Análise de Documentos",  "desc": "Extração e leitura com IA",   "setup": 4000,  "mensal": 1100, "custo": 360},
    {"slug": "bi",          "nome": "BI / Dashboard",         "desc": "Indicadores em tempo real",   "setup": 5500,  "mensal": 1300, "custo": 410},
    {"slug": "voz",         "nome": "Agente de Voz",          "desc": "Ligações com IA",             "setup": 7000,  "mensal": 1800, "custo": 640},
    {"slug": "financeiro",  "nome": "Automação Financeira",   "desc": "Conciliação e cobrança",      "setup": 6500,  "mensal": 1600, "custo": 560},
    {"slug": "custom",      "nome": "Sistema Sob Medida",     "desc": "Desenvolvimento dedicado",    "setup": 12000, "mensal": 2500, "custo": 1100},
]


def garantir_tabela(pool):
    """Cria a tabela em runtime (o deploy não roda migração sozinho)."""
    with pool.connection() as c:
        c.execute("""
            create table if not exists servicos_catalogo (
                id              bigserial primary key,
                conta_id        bigint not null references contas(id) on delete cascade,
                slug            text not null,
                nome            text not null,
                descricao       text default '',
                setup_centavos  bigint default 0,
                mensal_centavos bigint default 0,
                custo_centavos  bigint default 0,
                ativo           boolean not null default true,
                ordem           int default 0,
                criado_em       timestamptz not null default now(),
                unique (conta_id, slug)
            )""")
        # espelha a migração 148 (categoria/foto do item no orçamento de evento)
        c.execute("""
            alter table servicos_catalogo add column if not exists categoria text;
            alter table servicos_catalogo add column if not exists foto_url  text;
            alter table servicos_catalogo add column if not exists icone     text;
        """)
        c.execute("create index if not exists idx_servicos_catalogo_conta "
                  "on servicos_catalogo (conta_id, ativo, ordem)")
        c.commit()


def _slugify(nome: str, existentes: set[str]) -> str:
    base = re.sub(r"[^a-z0-9]+", "-", (nome or "").lower()).strip("-")[:40] or "servico"
    slug, i = base, 2
    while slug in existentes:
        slug = f"{base}-{i}"
        i += 1
    return slug


def listar(pool, conta_id: int) -> list[dict]:
    """Serviços ativos da conta, na ordem definida."""
    with pool.connection() as c:
        rows = c.execute(
            """select id, slug, nome, descricao, setup_centavos, mensal_centavos,
                      custo_centavos, ordem, categoria, foto_url, icone
                 from servicos_catalogo
                where conta_id=%s and ativo
                order by ordem, id""", (conta_id,)).fetchall()
    return [{"id": r[0], "slug": r[1], "nome": r[2], "descricao": r[3] or "",
             "setup_centavos": int(r[4] or 0), "mensal_centavos": int(r[5] or 0),
             "custo_centavos": int(r[6] or 0), "ordem": r[7] or 0,
             "categoria": r[8] or "", "foto_url": r[9] or "",
             "icone": r[10] or ""} for r in rows]


def slugs_validos(pool, conta_id: int) -> set[str]:
    """Slugs ativos da conta — pra validar seleção de módulos num orçamento."""
    return {s["slug"] for s in listar(pool, conta_id)}


def salvar(pool, conta_id: int, *, id: int | None = None, nome: str,
           descricao: str = "", setup_centavos: int = 0,
           mensal_centavos: int = 0, custo_centavos: int = 0,
           categoria: str = "", foto_url: str = "", icone: str = "") -> dict:
    """Cria (id vazio) ou edita (id preenchido) um serviço do catálogo da conta."""
    nome = (nome or "").strip()
    if not nome:
        return {"ok": False, "erro": "Informe o nome do serviço."}
    setup_centavos = max(0, int(setup_centavos or 0))
    mensal_centavos = max(0, int(mensal_centavos or 0))
    custo_centavos = max(0, int(custo_centavos or 0))
    with pool.connection() as c:
        if id:
            r = c.execute(
                """update servicos_catalogo
                      set nome=%s, descricao=%s, setup_centavos=%s,
                          mensal_centavos=%s, custo_centavos=%s,
                          categoria=%s, foto_url=%s, icone=%s
                    where id=%s and conta_id=%s and ativo
                  returning id, slug""",
                (nome, descricao or "", setup_centavos, mensal_centavos,
                 custo_centavos, (categoria or "").strip() or None,
                 (foto_url or "").strip() or None,
                 (icone or "").strip() or None, id, conta_id)).fetchone()
            c.commit()
            if not r:
                return {"ok": False, "erro": "Serviço não encontrado."}
            return {"ok": True, "id": r[0], "slug": r[1]}
        existentes = {x[0] for x in c.execute(
            "select slug from servicos_catalogo where conta_id=%s", (conta_id,)).fetchall()}
        slug = _slugify(nome, existentes)
        ordem = c.execute(
            "select coalesce(max(ordem),0)+1 from servicos_catalogo where conta_id=%s",
            (conta_id,)).fetchone()[0]
        nid = c.execute(
            """insert into servicos_catalogo
                   (conta_id, slug, nome, descricao, setup_centavos,
                    mensal_centavos, custo_centavos, ordem, categoria, foto_url,
                    icone)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
            (conta_id, slug, nome, descricao or "", setup_centavos,
             mensal_centavos, custo_centavos, ordem,
             (categoria or "").strip() or None,
             (foto_url or "").strip() or None,
             (icone or "").strip() or None)).fetchone()[0]
        c.commit()
        return {"ok": True, "id": nid, "slug": slug}


def excluir(pool, conta_id: int, id: int) -> dict:
    """Soft-delete: some da tela mas não quebra orçamento antigo que usa o slug."""
    with pool.connection() as c:
        r = c.execute(
            """update servicos_catalogo set ativo=false
                where id=%s and conta_id=%s and ativo returning id""",
            (id, conta_id)).fetchone()
        c.commit()
    return {"ok": bool(r)}


def importar_modelo(pool, conta_id: int, itens: list[dict] | None = None) -> dict:
    """Semeia o catálogo da conta com um modelo pronto (default: tecnologia).

    Idempotente por slug: não duplica o que já existe. Valores do modelo em REAIS.
    """
    itens = itens or STARTER_TECNOLOGIA
    with pool.connection() as c:
        existentes = {x[0] for x in c.execute(
            "select slug from servicos_catalogo where conta_id=%s", (conta_id,)).fetchall()}
        ordem0 = c.execute(
            "select coalesce(max(ordem),0) from servicos_catalogo where conta_id=%s",
            (conta_id,)).fetchone()[0]
        inseridos = 0
        for it in itens:
            slug = it.get("slug") or _slugify(it["nome"], existentes)
            if slug in existentes:
                continue
            existentes.add(slug)
            ordem0 += 1
            c.execute(
                """insert into servicos_catalogo
                       (conta_id, slug, nome, descricao, setup_centavos,
                        mensal_centavos, custo_centavos, ordem)
                   values (%s,%s,%s,%s,%s,%s,%s,%s)""",
                (conta_id, slug, it["nome"], it.get("desc", "") or it.get("descricao", ""),
                 int(it.get("setup", 0)) * 100, int(it.get("mensal", 0)) * 100,
                 int(it.get("custo", 0)) * 100, ordem0))
            inseridos += 1
        c.commit()
    return {"ok": True, "inseridos": inseridos}
