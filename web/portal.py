"""Portal do OpenClaw: cadastro, login e painel (Bloco A+B).

Vive dentro do openclaw-web. Regra sagrada: toda pagina logada enxerga
APENAS a conta da sessao (isolamento multi-tenant na camada web).
Senhas: hash scrypt (stdlib) com sal aleatorio - nunca em texto puro.
"""
import hashlib
import os
import secrets

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, DictLoader, select_autoescape

from db.conexao import get_pool
from contas import contas as ct

router = APIRouter()


# ---------- senha (scrypt, stdlib: zero dependencia extra) ----------

def hash_senha(senha: str) -> str:
    sal = secrets.token_hex(16)
    h = hashlib.scrypt(senha.encode(), salt=bytes.fromhex(sal), n=2**14, r=8, p=1)
    return f"scrypt${sal}${h.hex()}"


def verificar_senha(senha: str, guardado: str | None) -> bool:
    try:
        _alg, sal, hex_h = (guardado or "").split("$")
        h = hashlib.scrypt(senha.encode(), salt=bytes.fromhex(sal), n=2**14, r=8, p=1)
        return secrets.compare_digest(h.hex(), hex_h)
    except Exception:  # noqa: BLE001
        return False


# ---------- helpers ----------

def _normalizar_zap(numero: str) -> str:
    d = "".join(ch for ch in (numero or "") if ch.isdigit())
    if not d.startswith("55"):
        d = "55" + d
    if len(d) == 12:                      # sem o nono digito -> insere
        d = d[:4] + "9" + d[4:]
    return "+" + d


def conta_logada(request: Request):
    cid = request.session.get("conta_id")
    if not cid:
        return None
    pool = get_pool()
    with pool.connection() as c:
        row = c.execute(
            "select id, tipo, nome, email, plano, status, vencimento from contas where id = %s",
            (cid,),
        ).fetchone()
    return row


def _planos():
    pool = get_pool()
    with pool.connection() as c:
        return c.execute(
            """select codigo, nome, tipo_conta, preco_base_centavos,
                      membros_inclusos, preco_assento_centavos
               from planos where ativo order by preco_base_centavos"""
        ).fetchall()


def _limite_membros(conta_row) -> tuple[int, int, bool]:
    """(ativos, inclusos_no_plano, pode_passar_do_limite).

    PF: teto rigido = membros_inclusos. PJ: pode passar (assento extra cobrado).
    """
    pool = get_pool()
    with pool.connection() as c:
        ativos = c.execute(
            "select count(*) from membros where conta_id=%s and ativo", (conta_row[0],)
        ).fetchone()[0]
        plano = c.execute(
            "select membros_inclusos, tipo_conta from planos where codigo=%s",
            (conta_row[4],),
        ).fetchone()
    inclusos = plano[0] if plano else 1
    pode_extra = (conta_row[1] == "pj")
    return ativos, inclusos, pode_extra


def brl(centavos: int) -> str:
    return f"R$ {centavos/100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


# ---------- paginas (templates embutidos: 1 arquivo so') ----------

_BASE = """<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ titulo }} - OpenClaw</title>
<style>
:root { color-scheme: dark; }
body{margin:0;min-height:100vh;font-family:system-ui,-apple-system,sans-serif;
 background:#0e0e0f;color:#ececec;display:flex;flex-direction:column;align-items:center}
.topo{width:100%;max-width:960px;display:flex;justify-content:space-between;
 align-items:center;padding:1.2rem 1rem;box-sizing:border-box}
.topo a{color:#5dcaa5;text-decoration:none;margin-left:1rem}
.logo{font-weight:600;color:#ececec;font-size:1.1rem}
.card{width:100%;max-width:430px;background:#161617;border:1px solid #2a2a2b;
 border-radius:14px;padding:2rem;margin:1.5rem 1rem;box-sizing:border-box}
.card.larga{max-width:720px}
h1{font-size:1.35rem;font-weight:500;margin:0 0 1.2rem}
label{display:block;font-size:.85rem;color:#a8a8a3;margin:.9rem 0 .3rem}
input,select{width:100%;padding:.65rem .8rem;border-radius:8px;border:1px solid #333;
 background:#0e0e0f;color:#ececec;box-sizing:border-box;font-size:.95rem}
button{width:100%;margin-top:1.4rem;padding:.75rem;border:0;border-radius:8px;
 background:#1d9e75;color:#fff;font-size:1rem;cursor:pointer}
button:hover{background:#22b485}
.erro{background:#3a1d1d;border:1px solid #6e2b2b;color:#f0b8b8;border-radius:8px;
 padding:.6rem .8rem;font-size:.88rem;margin-bottom:.6rem}
.ok{background:#15301f;border:1px solid #1d9e75;color:#9fe8c9;border-radius:8px;
 padding:.6rem .8rem;font-size:.88rem;margin-bottom:.6rem}
.mut{color:#a8a8a3;font-size:.85rem}
table{width:100%;border-collapse:collapse;margin-top:.8rem}
td,th{padding:.5rem .4rem;border-bottom:1px solid #2a2a2b;text-align:left;font-size:.92rem}
.tag{display:inline-block;padding:.1rem .55rem;border-radius:999px;font-size:.78rem;
 border:1px solid #1d9e75;color:#5dcaa5}
</style></head><body>
<div class="topo"><span class="logo">OpenClaw</span><span>
{% if logado %}<a href="/painel">Painel</a><a href="/sair">Sair</a>
{% else %}<a href="/login">Entrar</a><a href="/cadastro">Criar conta</a>{% endif %}
</span></div>
{% block conteudo %}{% endblock %}
</body></html>"""

_CADASTRO = """{% extends "base" %}{% block conteudo %}
<div class="card"><h1>Criar sua conta</h1>
{% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
<form method="post" action="/cadastro">
<label>Plano</label><select name="plano">
{% for p in planos %}<option value="{{ p[0] }}">{{ p[1] }} — {{ brl(p[3]) }}/mês
{% if p[2]=='pj' %}(inclui {{ p[4] }} usuários; extra {{ brl(p[5]) }}){% elif p[4]>1 %}(até {{ p[4] }} pessoas){% endif %}</option>{% endfor %}
</select>
<label>Seu nome</label><input name="nome" required maxlength="80">
<label>E-mail</label><input name="email" type="email" required maxlength="120">
<label>Senha</label><input name="senha" type="password" required minlength="8" maxlength="72">
<label>CPF ou CNPJ <span class="mut">(opcional agora)</span></label><input name="documento" maxlength="20">
<label>Seu WhatsApp (com DDD)</label><input name="whatsapp" required placeholder="86 98888-7777" maxlength="20">
<button>Começar meu teste grátis de 7 dias</button>
<p class="mut">Sem cartão agora. Coletamos só o necessário (LGPD).</p>
</form></div>{% endblock %}"""

_LOGIN = """{% extends "base" %}{% block conteudo %}
<div class="card"><h1>Entrar</h1>
{% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
{% if aviso %}<div class="ok">{{ aviso }}</div>{% endif %}
<form method="post" action="/login">
<label>E-mail</label><input name="email" type="email" required>
<label>Senha</label><input name="senha" type="password" required>
<button>Entrar</button></form></div>{% endblock %}"""

_PAINEL = """{% extends "base" %}{% block conteudo %}
<div class="card larga"><h1>Olá, {{ conta[2] }}! <span class="tag">{{ conta[5] }}</span></h1>
<p class="mut">Plano: <b>{{ conta[4] or '-' }}</b>
{% if conta[6] %} · válido até <b>{{ conta[6].strftime('%d/%m/%Y') }}</b>{% endif %}
 · tipo: <b>{{ conta[1]|upper }}</b></p>
<h1 style="font-size:1.05rem;margin-top:1.4rem">Pessoas da conta</h1>
<table><tr><th>Nome</th><th>Papel</th><th>WhatsApp</th><th>Status</th><th></th></tr>
{% for m in membros %}<tr><td>{{ m[0] or '-' }}</td><td>{{ m[1] }}</td>
<td>{{ m[2] or '-' }}</td><td>{{ 'ativo' if m[3] else 'desativado' }}</td>
<td>{% if m[1] != 'dono' and m[3] %}<form method="post" action="/membros/desativar" style="margin:0">
<input type="hidden" name="membro_id" value="{{ m[4] }}">
<button style="margin:0;padding:.3rem .7rem;background:#6e2b2b;font-size:.8rem">desativar</button>
</form>{% endif %}</td></tr>{% endfor %}
</table>
<p class="mut" style="margin-top:1.2rem">Seu assistente já responde no WhatsApp cadastrado.
Mande "oi" pra ele!</p>
</div>
<div class="card larga"><h1 style="font-size:1.05rem">Adicionar pessoa</h1>
{% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
{% if aviso %}<div class="ok">{{ aviso }}</div>{% endif %}
{% if pode_adicionar %}
<form method="post" action="/membros/adicionar">
<label>Nome</label><input name="nome" required maxlength="80">
<label>WhatsApp (com DDD)</label><input name="whatsapp" required maxlength="20" placeholder="86 98888-7777">
<button>Adicionar à conta</button></form>
{% if extra_pago %}<p class="mut">Seu plano inclui {{ inclusos }} pessoas; acima disso, cada assento extra é cobrado.</p>{% endif %}
{% else %}
<p class="mut">Seu plano ({{ conta[4] }}) permite {{ inclusos }} pessoa(s) e você já usa {{ ativos }}.
Pra adicionar mais, faça upgrade pro plano Família ou PJ.</p>
{% endif %}
<p class="mut" style="margin-top:1rem"><a href="/senha" style="color:#5dcaa5">Alterar minha senha</a></p>
</div>{% endblock %}"""

_SENHA = """{% extends "base" %}{% block conteudo %}
<div class="card"><h1>Alterar senha</h1>
{% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
{% if ok %}<div class="ok">{{ ok }}</div>{% endif %}
<form method="post" action="/senha">
<label>Senha atual</label><input name="atual" type="password" required>
<label>Nova senha</label><input name="nova" type="password" required minlength="8" maxlength="72">
<button>Salvar nova senha</button></form>
<p class="mut" style="margin-top:1rem"><a href="/painel" style="color:#5dcaa5">Voltar ao painel</a></p>
</div>{% endblock %}"""

_env = Environment(loader=DictLoader({
    "base": _BASE, "cadastro": _CADASTRO, "login": _LOGIN, "painel": _PAINEL, "senha": _SENHA,
}), autoescape=select_autoescape())
_env.globals["brl"] = brl


def _render(nome: str, request: Request, **ctx) -> HTMLResponse:
    ctx.setdefault("logado", bool(request.session.get("conta_id")))
    ctx.setdefault("titulo", nome.capitalize())
    return HTMLResponse(_env.get_template(nome).render(**ctx))


# ---------- rotas ----------

@router.get("/cadastro", response_class=HTMLResponse)
def cadastro_form(request: Request):
    return _render("cadastro", request, planos=_planos(), erro=None)


@router.post("/cadastro", response_class=HTMLResponse)
def cadastro_envia(request: Request, plano: str = Form(...), nome: str = Form(...),
                   email: str = Form(...), senha: str = Form(...),
                   documento: str = Form(""), whatsapp: str = Form(...)):
    pool = get_pool()
    email = email.strip().lower()
    zap = _normalizar_zap(whatsapp)
    planos_ok = {p[0]: p for p in _planos()}
    if plano not in planos_ok:
        return _render("cadastro", request, planos=_planos(), erro="Plano invalido.")
    with pool.connection() as c:
        ja = c.execute("select 1 from contas where lower(email)=%s", (email,)).fetchone()
        zap_ja = c.execute("select 1 from membros where whatsapp_id=%s", (zap,)).fetchone()
    if ja:
        return _render("cadastro", request, planos=_planos(),
                       erro="Ja existe uma conta com esse e-mail. Tente entrar.")
    if zap_ja:
        return _render("cadastro", request, planos=_planos(),
                       erro="Esse WhatsApp ja esta cadastrado em outra conta.")

    tipo = planos_ok[plano][2]
    doc = "".join(ch for ch in documento if ch.isdigit()) or None
    conta_id = ct.criar_conta(pool, tipo, nome.strip(), plano=plano, documento=doc)
    with pool.connection() as c:
        c.execute("update contas set email=%s, senha_hash=%s where id=%s",
                  (email, hash_senha(senha), conta_id))
        c.commit()
    ct.adicionar_membro(pool, conta_id, nome=nome.strip(), papel="dono", whatsapp_id=zap)
    request.session["conta_id"] = conta_id
    return RedirectResponse("/painel", status_code=303)


@router.get("/login", response_class=HTMLResponse)
def login_form(request: Request):
    return _render("login", request, erro=None, aviso=None)


@router.post("/login", response_class=HTMLResponse)
def login_envia(request: Request, email: str = Form(...), senha: str = Form(...)):
    pool = get_pool()
    with pool.connection() as c:
        row = c.execute("select id, senha_hash from contas where lower(email)=%s",
                        (email.strip().lower(),)).fetchone()
    if not row or not verificar_senha(senha, row[1]):
        return _render("login", request, erro="E-mail ou senha incorretos.", aviso=None)
    request.session["conta_id"] = row[0]
    return RedirectResponse("/painel", status_code=303)


@router.get("/sair")
def sair(request: Request):
    request.session.clear()
    return RedirectResponse("/login", status_code=303)


@router.get("/painel", response_class=HTMLResponse)
def painel(request: Request):
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    with pool.connection() as c:
        membros = c.execute(
            "select nome, papel, whatsapp_id, ativo, id from membros where conta_id=%s order by id",
            (conta[0],),
        ).fetchall()
    ativos, inclusos, pode_extra = _limite_membros(conta)
    pode_adicionar = pode_extra or ativos < inclusos
    return _render("painel", request, conta=conta, membros=membros, titulo="Painel",
                   ativos=ativos, inclusos=inclusos, extra_pago=pode_extra,
                   pode_adicionar=pode_adicionar,
                   erro=request.session.pop("erro", None),
                   aviso=request.session.pop("aviso", None))


@router.post("/membros/adicionar")
def membros_adicionar(request: Request, nome: str = Form(...), whatsapp: str = Form(...)):
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    zap = _normalizar_zap(whatsapp)
    ativos, inclusos, pode_extra = _limite_membros(conta)
    if not pode_extra and ativos >= inclusos:
        request.session["erro"] = "Limite de pessoas do plano atingido."
        return RedirectResponse("/painel", status_code=303)
    with pool.connection() as c:
        ja = c.execute("select 1 from membros where whatsapp_id=%s", (zap,)).fetchone()
    if ja:
        request.session["erro"] = "Esse WhatsApp ja esta cadastrado."
        return RedirectResponse("/painel", status_code=303)
    ct.adicionar_membro(pool, conta[0], nome=nome.strip(), papel="membro", whatsapp_id=zap)
    request.session["aviso"] = f"{nome.strip()} adicionado(a)! Ja pode falar com o assistente."
    return RedirectResponse("/painel", status_code=303)


@router.post("/membros/desativar")
def membros_desativar(request: Request, membro_id: int = Form(...)):
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    with pool.connection() as c:
        ok = c.execute(
            "select 1 from membros where id=%s and conta_id=%s and papel <> 'dono'",
            (membro_id, conta[0]),
        ).fetchone()
    if not ok:
        request.session["erro"] = "Nao foi possivel desativar essa pessoa."
        return RedirectResponse("/painel", status_code=303)
    ct.desativar_membro(pool, membro_id)
    request.session["aviso"] = "Pessoa desativada (o historico dela fica preservado)."
    return RedirectResponse("/painel", status_code=303)


@router.get("/senha", response_class=HTMLResponse)
def senha_form(request: Request):
    if not request.session.get("conta_id"):
        return RedirectResponse("/login", status_code=303)
    return _render("senha", request, erro=None, ok=None, titulo="Alterar senha")


@router.post("/senha", response_class=HTMLResponse)
def senha_envia(request: Request, atual: str = Form(...), nova: str = Form(...)):
    cid = request.session.get("conta_id")
    if not cid:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    with pool.connection() as c:
        row = c.execute("select senha_hash from contas where id=%s", (cid,)).fetchone()
    if not row or not verificar_senha(atual, row[0]):
        return _render("senha", request, erro="Senha atual incorreta.", ok=None, titulo="Alterar senha")
    with pool.connection() as c:
        c.execute("update contas set senha_hash=%s where id=%s", (hash_senha(nova), cid))
        c.commit()
    ct.registrar_evento(pool, cid, "senha_alterada", "via portal")
    return _render("senha", request, erro=None, ok="Senha alterada com sucesso!", titulo="Alterar senha")
