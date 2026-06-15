"""Painel ADM do OpenClaw: a torre de controle do dono do sistema.

SEGURANCA: toda rota exige que a conta logada tenha is_admin=true (setado por
SQL, nunca pela interface). Quem nao for admin recebe 404 - nem revela que a
area existe. Toda acao administrativa e' registrada na auditoria (eventos_conta).
"""
from datetime import date

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, DictLoader, select_autoescape

from db.conexao import get_pool
from contas import contas as ct
from web.portal import brl

router = APIRouter()


# ---------- guarda de admin ----------

def _admin(request: Request):
    """Retorna a conta logada SE for admin; senao None."""
    cid = request.session.get("conta_id")
    if not cid:
        return None
    pool = get_pool()
    with pool.connection() as c:
        row = c.execute(
            "select id, nome, is_admin from contas where id = %s", (cid,)
        ).fetchone()
    if not row or not row[2]:
        return None
    return row


_NEGADO = HTMLResponse("<h1>404</h1>", status_code=404)


# ---------- templates ----------

_ADMIN_BASE = """<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Admin - OpenClaw</title>
<style>
:root { color-scheme: dark; }
body{margin:0;font-family:system-ui,-apple-system,sans-serif;background:#0e0e0f;
 color:#ececec;display:flex;flex-direction:column;align-items:center}
.topo{width:100%;max-width:1000px;display:flex;justify-content:space-between;
 align-items:center;padding:1.2rem 1rem;box-sizing:border-box}
.topo a{color:#5dcaa5;text-decoration:none;margin-left:1rem}
.logo{font-weight:600}.logo span{color:#e0a83d}
.wrap{width:100%;max-width:1000px;padding:0 1rem 3rem;box-sizing:border-box}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:1rem 0}
.metric{background:#161617;border:1px solid #2a2a2b;border-radius:10px;padding:1rem}
.metric span{display:block;font-size:.8rem;color:#a8a8a3;margin-bottom:.3rem}
.metric b{font-size:1.5rem;font-weight:500}
.card{background:#161617;border:1px solid #2a2a2b;border-radius:12px;padding:1.3rem;margin:1rem 0}
h1{font-size:1.3rem;font-weight:500}h2{font-size:1.05rem;font-weight:500;margin:0 0 .6rem}
table{width:100%;border-collapse:collapse;font-size:.9rem}
td,th{padding:.5rem .4rem;border-bottom:1px solid #2a2a2b;text-align:left}
input,select{padding:.5rem .6rem;border-radius:7px;border:1px solid #333;background:#0e0e0f;color:#ececec;font-size:.9rem}
button{padding:.45rem .8rem;border:0;border-radius:7px;background:#1d9e75;color:#fff;cursor:pointer;font-size:.85rem}
button:hover{background:#22b485}
button.warn{background:#8a5a1c}button.warn:hover{background:#a86c22}
button.danger{background:#6e2b2b}button.danger:hover{background:#8a3636}
.tag{display:inline-block;padding:.1rem .5rem;border-radius:999px;font-size:.75rem;border:1px solid #444;color:#bbb}
.tag.ativa{border-color:#1d9e75;color:#5dcaa5}.tag.trial{border-color:#3a78c2;color:#7ab0e8}
.tag.suspensa,.tag.inadimplente{border-color:#8a3636;color:#e89a9a}
.mut{color:#a8a8a3;font-size:.85rem}
form.inline{display:inline;margin:0}
</style></head><body>
<div class="topo"><span class="logo">OpenClaw <span>· admin</span></span>
<span><a href="/admin">Contas</a><a href="/admin/qr">QR notas</a><a href="/admin/precos">Preços</a><a href="/painel">Meu painel</a><a href="/sair">Sair</a></span></div>
<div class="wrap">{% block conteudo %}{% endblock %}</div>
</body></html>"""

_ADMIN_HOME = """{% extends "abase" %}{% block conteudo %}
<h1>Torre de controle</h1>
{% if aviso %}<div class="metric" style="border-color:#1d9e75;color:#9fe8c9">{{ aviso }}</div>{% endif %}
<div class="cards">
<div class="metric"><span>Contas</span><b>{{ resumo.total }}</b></div>
<div class="metric"><span>Em trial</span><b>{{ resumo.trial }}</b></div>
<div class="metric"><span>Ativas</span><b>{{ resumo.ativa }}</b></div>
<div class="metric"><span>Vencendo em 7 dias</span><b>{{ resumo.vencendo }}</b></div>
<div class="metric"><span>Receita mensal estimada</span><b>{{ brl(resumo.mrr) }}</b></div>
</div>

<div class="card"><h2>Contas</h2>
<form method="post" action="/admin/precos/importar" style="margin-bottom:.8rem; display:inline">
<button title="Lê os cupons de mercado e atualiza o banco de preços do comparador">↻ Atualizar banco de preços</button></form>
<form method="get" action="/admin" style="margin-bottom:.8rem">
<input name="busca" placeholder="buscar nome, e-mail ou documento" value="{{ busca or '' }}" style="width:60%">
<button>Buscar</button></form>
<table><tr><th>ID</th><th>Nome</th><th>Tipo</th><th>Plano</th><th>Status</th><th>Vence</th><th>Membros</th><th>Ações</th></tr>
{% for c in contas %}<tr>
<td>{{ c.id }}</td><td>{{ c.nome }}<br><span class="mut">{{ c.email or '-' }}</span></td>
<td>{{ c.tipo|upper }}</td><td>{{ c.plano or '-' }}</td>
<td><span class="tag {{ c.status }}">{{ c.status }}</span></td>
<td>{{ c.vencimento.strftime('%d/%m/%y') if c.vencimento else '-' }}</td>
<td>{{ c.membros }}</td>
<td>
<form class="inline" method="post" action="/admin/conta/{{ c.id }}/ativar"><button>Ativar +30d</button></form>
<form class="inline" method="post" action="/admin/conta/{{ c.id }}/suspender"><button class="warn">Suspender</button></form>
</td></tr>{% endfor %}
</table></div>

<div class="card"><h2>Auditoria recente</h2>
<table><tr><th>Quando</th><th>Conta</th><th>Evento</th><th>Detalhe</th></tr>
{% for e in eventos %}<tr>
<td class="mut">{{ e.criado_em.strftime('%d/%m %H:%M') }}</td>
<td>{{ e.conta_id }}</td><td>{{ e.tipo }}</td><td class="mut">{{ e.detalhe }}</td></tr>{% endfor %}
</table></div>
{% endblock %}"""

_ADMIN_QR = """{% extends "abase" %}{% block conteudo %}
<h1>Leitura de QR code das notas</h1>
{% if aviso %}<div class="card" style="border-color:#1d9e75">{{ aviso }}</div>{% endif %}
<div class="cards">
<div class="metric"><span>Fotos/PDFs recebidos</span><b>{{ resumo.total }}</b></div>
<div class="metric"><span>QR lido com sucesso</span><b style="color:#5dcaa5">{{ resumo.leu }}</b></div>
<div class="metric"><span>Sem QR</span><b style="color:#e89a9a">{{ resumo.sem }}</b></div>
<div class="metric"><span>Taxa de leitura</span><b>{{ resumo.taxa }}%</b></div>
</div>

<div class="card"><h2>Últimas notas recebidas</h2>
<table><tr><th>Quando</th><th>Conta</th><th>Tipo</th><th>Foto (px / KB)</th><th>Leu?</th><th>UF</th><th>Emitida</th><th>Chave / CNPJ emitente</th></tr>
{% for r in leituras %}<tr>
<td class="mut">{{ r.criado_em.strftime('%d/%m %H:%M') }}</td>
<td>{{ r.conta_id or '-' }}</td>
<td class="mut">{{ 'PDF' if r.media_type == 'application/pdf' else 'foto' }}</td>
<td class="mut" style="font-size:.78rem">{% if r.img_largura %}{{ r.img_largura }}×{{ r.img_altura }}{% if r.img_bytes %} · {{ (r.img_bytes/1024)|round|int }}KB{% endif %}{% else %}-{% endif %}</td>
<td>{% if r.leu %}<span class="tag ativa">sim</span>{% else %}<span class="tag suspensa">não</span>{% endif %}</td>
<td>{{ r.uf or '-' }}</td>
<td class="mut">{{ r.data_emissao.strftime('%m/%Y') if r.data_emissao else '-' }}</td>
<td class="mut" style="font-size:.72rem">{% if r.chave %}{{ r.chave }}<br>CNPJ {{ r.cnpj_emitente }}{% else %}-{% endif %}</td>
</tr>{% endfor %}
</table>
{% if not leituras %}<p class="mut">Nenhuma nota recebida ainda.</p>{% endif %}
</div>
{% endblock %}"""

_ADMIN_PRECOS = """{% extends "abase" %}{% block conteudo %}
<h1>Auditoria de preços coletivos</h1>
{% if aviso %}<div class="card" style="border-color:#1d9e75">{{ aviso }}</div>{% endif %}
<div class="cards">
<div class="metric"><span>Total de preços</span><b>{{ resumo.total }}</b></div>
<div class="metric"><span>Produtos únicos</span><b>{{ resumo.produtos }}</b></div>
<div class="metric"><span>Lojas</span><b>{{ resumo.lojas }}</b></div>
<div class="metric"><span>De cupom (fonte)</span><b>{{ resumo.de_cupom }}</b></div>
</div>

<div class="card"><h2>Últimos preços registrados</h2>
<table><tr><th>Quando</th><th>Produto</th><th>Preço</th><th>Lojas</th><th>UF</th><th>CNPJ</th><th>Fonte</th></tr>
{% for p in precos %}<tr>
<td class="mut">{{ p.criado_em.strftime('%d/%m %H:%M') }}</td>
<td>{{ p.descricao }}</td>
<td><b>{{ brl(p.valor_unitario_centavos) }}</b></td>
<td>{% if p.loja_nome %}{{ p.loja_nome }}{% else %}<span class="mut">-</span>{% endif %}</td>
<td class="mut">{{ p.uf or '-' }}</td>
<td class="mut" style="font-size:.8rem">{{ p.cnpj or '-' }}</td>
<td class="mut">{{ p.fonte }}</td>
</tr>{% endfor %}
</table>
{% if not precos %}<p class="mut">Nenhum preço registrado ainda.</p>{% endif %}
</div>

<div class="card"><h2>Lojas</h2>
<table><tr><th>CNPJ</th><th>Nome</th><th>UF</th><th>Endereço</th><th>Preços</th><th>Desde</th></tr>
{% for l in lojas %}<tr>
<td class="mut" style="font-size:.8rem">{{ l.cnpj }}</td>
<td>{{ l.nome or '-' }}</td>
<td class="mut">{{ l.uf or '-' }}</td>
<td class="mut">{{ l.endereco or '-' }}</td>
<td><b>{{ l.precos_count }}</b></td>
<td class="mut">{{ l.criado_em.strftime('%d/%m/%y') }}</td>
</tr>{% endfor %}
</table>
{% if not lojas %}<p class="mut">Nenhuma loja registrada ainda.</p>{% endif %}
</div>
{% endblock %}"""

_env = Environment(loader=DictLoader({"abase": _ADMIN_BASE, "ahome": _ADMIN_HOME, "aqr": _ADMIN_QR, "aprecos": _ADMIN_PRECOS}),
                   autoescape=select_autoescape())
_env.globals["brl"] = brl


# ---------- rotas ----------

@router.get("/admin", response_class=HTMLResponse)
def admin_home(request: Request, busca: str = ""):
    if _admin(request) is None:
        return _NEGADO
    pool = get_pool()
    with pool.connection() as c:
        # resumo do negocio
        tot = dict(c.execute(
            """select status, count(*) from contas group by status""").fetchall())
        total = sum(tot.values())
        vencendo = c.execute(
            """select count(*) from contas where vencimento between current_date
               and current_date + 7 and status in ('trial','ativa','inadimplente')""").fetchone()[0]
        # MRR estimado: soma do preco base dos planos das contas ativas/trial
        mrr = c.execute(
            """select coalesce(sum(p.preco_base_centavos),0)
               from contas ct join planos p on p.codigo = ct.plano
               where ct.status in ('ativa','trial')""").fetchone()[0]

        sql = """select ct.id, ct.nome, ct.email, ct.tipo, ct.plano, ct.status, ct.vencimento,
                        (select count(*) from membros m where m.conta_id = ct.id and m.ativo) as membros
                 from contas ct"""
        params: list = []
        if busca.strip():
            sql += """ where ct.nome ilike %s or ct.email ilike %s or ct.documento ilike %s"""
            termo = f"%{busca.strip()}%"; params = [termo, termo, termo]
        sql += " order by ct.id desc limit 200"
        cols = ["id", "nome", "email", "tipo", "plano", "status", "vencimento", "membros"]
        contas = [dict(zip(cols, r)) for r in c.execute(sql, params).fetchall()]

        ecols = ["conta_id", "tipo", "detalhe", "criado_em"]
        eventos = [dict(zip(ecols, r)) for r in c.execute(
            """select conta_id, tipo, detalhe, criado_em from eventos_conta
               order by id desc limit 30""").fetchall()]

    resumo = {"total": total, "trial": tot.get("trial", 0), "ativa": tot.get("ativa", 0),
              "vencendo": vencendo, "mrr": mrr}
    from types import SimpleNamespace
    contas = [SimpleNamespace(**c) for c in contas]
    eventos = [SimpleNamespace(**e) for e in eventos]
    return HTMLResponse(_env.get_template("ahome").render(
        resumo=resumo, contas=contas, eventos=eventos, busca=busca,
        aviso=request.session.pop("admin_aviso", None)))


@router.post("/admin/conta/{conta_id}/ativar")
def admin_ativar(request: Request, conta_id: int):
    adm = _admin(request)
    if adm is None:
        return _NEGADO
    pool = get_pool()
    ct.ativar(pool, conta_id, dias=30)
    ct.registrar_evento(pool, conta_id, "admin_ativou", f"por admin {adm[0]}")
    request.session["admin_aviso"] = f"Conta {conta_id} ativada por mais 30 dias."
    return RedirectResponse("/admin", status_code=303)


@router.get("/admin/qr", response_class=HTMLResponse)
def admin_qr(request: Request):
    if _admin(request) is None:
        return _NEGADO
    pool = get_pool()
    with pool.connection() as c:
        total = c.execute("select count(*) from qr_leituras").fetchone()[0]
        leu = c.execute("select count(*) from qr_leituras where leu").fetchone()[0]
        cols = ["conta_id", "chave", "uf", "cnpj_emitente", "data_emissao",
                "media_type", "leu", "criado_em", "img_largura", "img_altura", "img_bytes"]
        leituras = [dict(zip(cols, r)) for r in c.execute(
            """select conta_id, chave, uf, cnpj_emitente, data_emissao,
                      media_type, leu, criado_em, img_largura, img_altura, img_bytes
               from qr_leituras order by id desc limit 200""").fetchall()]
    sem = total - leu
    taxa = round(100 * leu / total) if total else 0
    resumo = {"total": total, "leu": leu, "sem": sem, "taxa": taxa}
    from types import SimpleNamespace
    leituras = [SimpleNamespace(**x) for x in leituras]
    return HTMLResponse(_env.get_template("aqr").render(
        resumo=resumo, leituras=leituras,
        aviso=request.session.pop("admin_aviso", None)))


@router.get("/admin/precos", response_class=HTMLResponse)
def admin_precos(request: Request):
    if _admin(request) is None:
        return _NEGADO
    pool = get_pool()
    with pool.connection() as c:
        # resumo
        total = c.execute("select count(*) from precos_observados").fetchone()[0]
        produtos = c.execute("select count(distinct descricao_norm) from precos_observados").fetchone()[0]
        lojas_count = c.execute("select count(*) from lojas").fetchone()[0]
        de_cupom = c.execute("select count(*) from precos_observados where fonte = 'cupom'").fetchone()[0]

        # ultimos precos com detalhes de loja
        cols_p = ["descricao", "valor_unitario_centavos", "loja_nome", "uf", "cnpj", "fonte", "criado_em"]
        precos = [dict(zip(cols_p, r)) for r in c.execute(
            """select po.descricao, po.valor_unitario_centavos, l.nome, l.uf, l.cnpj, po.fonte, po.criado_em
               from precos_observados po
               left join lojas l on l.id = po.loja_id
               order by po.id desc limit 100""").fetchall()]

        # lojas com contagem de precos
        cols_l = ["cnpj", "nome", "uf", "endereco", "criado_em", "precos_count"]
        lojas = [dict(zip(cols_l, r)) for r in c.execute(
            """select l.cnpj, l.nome, l.uf, l.endereco, l.criado_em, count(po.id)
               from lojas l
               left join precos_observados po on po.loja_id = l.id
               group by l.id
               order by count(po.id) desc""").fetchall()]

    resumo = {"total": total, "produtos": produtos, "lojas": lojas_count, "de_cupom": de_cupom}
    from types import SimpleNamespace
    precos = [SimpleNamespace(**p) for p in precos]
    lojas = [SimpleNamespace(**l) for l in lojas]
    return HTMLResponse(_env.get_template("aprecos").render(
        resumo=resumo, precos=precos, lojas=lojas,
        aviso=request.session.pop("admin_aviso", None)))


@router.post("/admin/precos/importar")
def admin_importar_precos(request: Request):
    adm = _admin(request)
    if adm is None:
        return _NEGADO
    from finance.banco_precos import BancoPrecos
    n = BancoPrecos(get_pool()).importar_historico()
    request.session["admin_aviso"] = f"Banco de preços atualizado: {n} preços importados dos cupons."
    return RedirectResponse("/admin", status_code=303)

@router.post("/admin/conta/{conta_id}/suspender")
def admin_suspender(request: Request, conta_id: int):
    adm = _admin(request)
    if adm is None:
        return _NEGADO
    pool = get_pool()
    ct.suspender(pool, conta_id, f"por admin {adm[0]}")
    ct.registrar_evento(pool, conta_id, "admin_suspendeu", f"por admin {adm[0]}")
    request.session["admin_aviso"] = f"Conta {conta_id} suspensa."
    return RedirectResponse("/admin", status_code=303)
