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
<span><a href="/admin">Contas</a><a href="/painel">Meu painel</a><a href="/sair">Sair</a></span></div>
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

_env = Environment(loader=DictLoader({"abase": _ADMIN_BASE, "ahome": _ADMIN_HOME}),
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
