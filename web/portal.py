"""Portal do OpenClaw: cadastro, login e painel (Bloco A+B+C).

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

from datetime import date

from db.conexao import get_pool
from contas import contas as ct
from contas.permissoes import pode_financas
from finance.livro_caixa import LivroCaixa
from finance.lista_compras import ListaCompras

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


def _papel_logado(request: Request, conta_id: int) -> str:
    """Papel do operador logado no portal. O login do portal e' por conta
    (titular = dono), entao por padrao 'dono'. Centraliza o gate de permissoes
    e ja' deixa pronto um futuro login por membro."""
    return request.session.get("papel", "dono")


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
.metric{background:#0e0e0f;border:1px solid #2a2a2b;border-radius:8px;padding:1rem}
.metric span{display:block;font-size:.8rem;color:#a8a8a3;margin-bottom:.3rem}
.metric b{font-size:1.4rem;font-weight:500}
.barra{height:8px;background:#0e0e0f;border-radius:4px;overflow:hidden}
.barra-fill{height:8px;background:#1d9e75;border-radius:4px}
.chip{border:1px solid #2a2a2b;padding:.25rem .6rem;border-radius:999px;font-size:.8rem;color:#ccc}
.abas{display:inline-flex;gap:4px;background:#0e0e0f;padding:3px;border-radius:8px;margin:.3rem 0 .6rem}
.aba{width:auto;margin:0;padding:.4rem .9rem;border-radius:6px;background:transparent;color:#a8a8a3;font-size:.85rem}
.aba:hover{background:#1a1a1b}
.aba.ativa{background:#1d9e75;color:#fff}
.dep{border:1px solid #2a2a2b;border-radius:8px;margin-bottom:8px;overflow:hidden}
.dep-cab{display:flex;justify-content:space-between;align-items:center;padding:.7rem .9rem;background:#161617;cursor:pointer;font-size:.92rem}
.dep-cab:hover{background:#1c1c1d}
.seta{color:#5dcaa5;margin-right:.3rem}
.dep-corpo{display:none;padding:.7rem .9rem;flex-wrap:wrap;gap:8px}
.dep.aberto .dep-corpo{display:flex}
.subdia{border-top:1px solid #232324}
.subdia-cab{display:flex;justify-content:space-between;align-items:center;padding:.55rem .2rem;cursor:pointer;font-size:.88rem}
.subdia-cab:hover{color:#fff}
.seta2{color:#5dcaa5;margin-right:.3rem;font-size:.8rem}
.subdia-corpo{display:none;flex-wrap:wrap;gap:8px;padding:.2rem 0 .7rem}
.subdia.aberto .subdia-corpo{display:flex}
</style></head><body>
<div class="topo"><span class="logo">OpenClaw</span><span>
{% if logado %}<a href="/painel">Painel</a><a href="/painel/financeiro">Financeiro</a><a href="/painel/compras">Compras</a><a href="/sair">Sair</a>
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
<label>Tipo de acesso</label>
<select name="papel">
<option value="membro">Membro — vê finanças e usa a lista de compras</option>
<option value="restrito">Restrito — só a lista de compras (ex: empregada)</option>
</select>
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

_DASH = """{% extends "base" %}{% block conteudo %}
<div class="card larga">
<div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:.5rem">
<h1 style="margin:0">Financeiro</h1>
<form method="get" action="/painel/financeiro" style="margin:0; display:flex; gap:.5rem; align-items:center">
<select name="mes" onchange="this.form.submit()">
{% for v,rotulo in meses %}<option value="{{ v }}" {% if v==mes_sel %}selected{% endif %}>{{ rotulo }}</option>{% endfor %}
</select>
{% if pessoas|length > 1 %}<select name="membro" onchange="this.form.submit()">
<option value="">Todos</option>
{% for mid,nome in pessoas %}<option value="{{ mid }}" {% if mid==membro_sel %}selected{% endif %}>{{ nome }}</option>{% endfor %}
</select>{% endif %}
</form></div>

<div style="display:grid; grid-template-columns:repeat(auto-fit,minmax(150px,1fr)); gap:12px; margin:1.2rem 0">
<div class="metric"><span>Saldo atual</span><b style="color:#5dcaa5">{{ brl(resumo.saldo) }}</b></div>
<div class="metric"><span>Receitas do mês</span><b>{{ brl(resumo.receitas) }}</b></div>
<div class="metric"><span>Despesas do mês</span><b>{{ brl(resumo.despesas) }}</b></div>
</div>

<h1 style="font-size:1.05rem">Despesas por categoria</h1>
{% if categorias %}{% for cat,val in categorias %}
<div style="display:flex; justify-content:space-between; font-size:.9rem; margin:.4rem 0 .2rem"><span>{{ cat }}</span><b>{{ brl(val) }}</b></div>
<div class="barra"><div class="barra-fill" style="width:{{ (val*100//maior_cat) if maior_cat else 0 }}%"></div></div>
{% endfor %}{% else %}<p class="mut">Sem despesas neste mês.</p>{% endif %}

<h1 style="font-size:1.05rem; margin-top:1.6rem">Lançamentos</h1>
<div class="abas">
<button type="button" class="aba ativa" data-f="todos" onclick="filtrarTipo(this)">Todos</button>
<button type="button" class="aba" data-f="despesa" onclick="filtrarTipo(this)">Despesas</button>
<button type="button" class="aba" data-f="receita" onclick="filtrarTipo(this)">Receitas</button>
</div>
<div id="lista-dias">
{% for dia in dias %}
<div class="dep" data-tipos="{% for it in dia.itens %}{{ it.tipo }} {% endfor %}">
<div class="dep-cab" onclick="abrirDep(this)">
<span><span class="seta">▸</span> {{ dia.data.strftime('%d/%m') }}
<span class="mut">· {{ dia.itens|length }} {{ 'lançamento' if dia.itens|length == 1 else 'lançamentos' }}</span></span>
<b style="color:{{ '#5dcaa5' if dia.saldo >= 0 else '#f0b8b8' }}">{{ '+' if dia.saldo >= 0 else '−' }} {{ brl(dia.saldo|abs).replace('R$ ','') }}</b>
</div>
<div class="dep-corpo" style="flex-direction:column; gap:0">
<table style="margin:0">
{% for l in dia.itens %}<tr data-tipo="{{ l.tipo }}">
<td>{{ l.descricao }}{% if l.origem=='foto' %} 📷{% endif %}</td>
<td><span class="tag">{{ l.categoria }}</span></td>
{% if pessoas|length > 1 %}<td class="mut">{{ l.quem }}</td>{% endif %}
<td style="text-align:right; font-weight:500; color:{{ '#5dcaa5' if l.tipo=='receita' else '#f0b8b8' }}">
{{ '+' if l.tipo=='receita' else '−' }} {{ brl(l.valor).replace('R$ ','') }}</td></tr>{% endfor %}
</table>
</div>
</div>
{% else %}<p class="mut">Nenhum lançamento neste período.</p>{% endfor %}
</div>
<p id="lanc-vazio" class="mut" style="display:none">Nenhum lançamento desse tipo neste período.</p>

<h1 style="font-size:1.05rem; margin-top:1.6rem">Raio-x do consumo por departamento</h1>
{% if raiox %}{% for dep, dados in raiox.items() %}
<div class="dep">
<div class="dep-cab" onclick="abrirDep(this)">
<span><span class="seta">▸</span> {{ dep }}</span>
<b>{{ brl(dados.total) }}</b>
</div>
<div class="dep-corpo" style="flex-direction:column; gap:0; padding-top:0">
{% for d in dados.dias %}
<div class="subdia">
<div class="subdia-cab" onclick="abrirSub(event, this)">
<span><span class="seta2">▸</span> {{ d.data.strftime('%d/%m/%Y') }}
<span class="mut">· {{ d.itens|length }} {{ 'item' if d.itens|length == 1 else 'itens' }}</span></span>
<span class="mut">{{ brl(d.subtotal) }}</span>
</div>
<div class="subdia-corpo">
{% for it in d.itens %}<span class="chip">{{ it.descricao }} · {{ brl(it.valor) }}</span>{% endfor %}
</div>
</div>
{% endfor %}
</div>
</div>{% endfor %}
{% else %}<p class="mut">Os itens aparecem aqui quando você fotografa um cupom de mercado.</p>{% endif %}
</div>

<script>
function filtrarTipo(btn){
  document.querySelectorAll('.aba').forEach(function(a){a.classList.remove('ativa')});
  btn.classList.add('ativa');
  var f = btn.dataset.f, visiveis = 0;
  document.querySelectorAll('#lista-dias .dep').forEach(function(dia){
    var linhas = dia.querySelectorAll('tr[data-tipo]'), comTipo = 0;
    linhas.forEach(function(tr){
      var ok = (f === 'todos' || tr.dataset.tipo === f);
      tr.style.display = ok ? '' : 'none';
      if (ok) comTipo++;
    });
    dia.style.display = comTipo ? '' : 'none';
    if (comTipo) visiveis++;
  });
  document.getElementById('lanc-vazio').style.display = visiveis ? 'none' : 'block';
}
function abrirSub(ev, cab){
  ev.stopPropagation();                 // nao fecha o departamento
  var sub = cab.parentElement;
  sub.classList.toggle('aberto');
  cab.querySelector('.seta2').textContent = sub.classList.contains('aberto') ? '▾' : '▸';
}
function abrirDep(cab){
  var dep = cab.parentElement;
  dep.classList.toggle('aberto');
  cab.querySelector('.seta').textContent = dep.classList.contains('aberto') ? '▾' : '▸';
}
</script>
{% endblock %}"""

_COMPRAS = """{% extends "base" %}{% block conteudo %}
<div class="card larga">
<div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:.5rem">
<h1 style="margin:0">Lista de compras</h1>
{% if itens %}<form method="post" action="/painel/compras/limpar" style="margin:0"
 onsubmit="return confirm('Remover os itens já comprados da lista?')">
<button style="margin:0;padding:.4rem .8rem;background:#2a2a2b;font-size:.82rem">Limpar comprados</button></form>{% endif %}
</div>

<form method="post" action="/painel/compras/add" style="display:flex; gap:.5rem; margin:1rem 0">
<input name="descricao" placeholder="Adicionar item (ex: arroz, café...)" required maxlength="80" style="flex:1">
<button style="margin:0; width:auto; padding:.65rem 1.2rem">Adicionar</button>
</form>

{% if resumo.estimado_centavos %}
<p class="mut">Estimativa dos pendentes: <b style="color:#5dcaa5">{{ brl(resumo.estimado_centavos) }}</b>
<span style="font-size:.78rem">(baseada no histórico de preços)</span></p>
{% endif %}

{% if itens %}
<table style="margin-top:.5rem">
{% for i in itens %}
<tr style="{{ 'opacity:.5' if i.comprado else '' }}">
<td style="width:40px">
<form method="post" action="/painel/compras/marcar" style="margin:0">
<input type="hidden" name="item_id" value="{{ i.id }}">
<input type="hidden" name="comprado" value="{{ 0 if i.comprado else 1 }}">
<button title="marcar" style="margin:0;padding:.25rem .55rem;background:{{ '#1d9e75' if i.comprado else '#2a2a2b' }};font-size:.9rem">✓</button>
</form></td>
<td style="{{ 'text-decoration:line-through' if i.comprado else '' }}">{{ i.descricao }}
{% if i.quantidade and i.quantidade != 1 %}<span class="mut">({{ '%g'|format(i.quantidade) }}{{ i.unidade or '' }})</span>{% endif %}
{% if i.preco_estimado_centavos %}<span class="mut"> · ~{{ brl(i.preco_estimado_centavos) }}</span>{% endif %}</td>
<td class="mut" style="font-size:.8rem">{{ i.quem }}</td>
<td style="width:40px; text-align:right">
<form method="post" action="/painel/compras/remover" style="margin:0">
<input type="hidden" name="item_id" value="{{ i.id }}">
<button title="remover" style="margin:0;padding:.25rem .5rem;background:transparent;color:#8a3636;font-size:.95rem">✕</button>
</form></td></tr>
{% endfor %}
</table>
<p class="mut" style="margin-top:1rem">{{ resumo.pendentes }} pendente(s)
{% if resumo.comprados %}· {{ resumo.comprados }} comprado(s){% endif %}</p>
{% else %}
<p class="mut">A lista está vazia. Adicione itens acima — ou peça pelo WhatsApp/Telegram:
<i>"acabou o arroz, bota na lista"</i>.</p>
{% endif %}
</div>{% endblock %}"""

_env = Environment(loader=DictLoader({
    "base": _BASE, "cadastro": _CADASTRO, "login": _LOGIN, "painel": _PAINEL, "senha": _SENHA, "dash": _DASH, "compras": _COMPRAS,
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


@router.get("/painel/financeiro", response_class=HTMLResponse)
def painel_financeiro(request: Request, mes: str = "", membro: str = "", tipo: str = ""):
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    if not pode_financas(_papel_logado(request, conta[0])):
        return RedirectResponse("/painel/compras", status_code=303)
    pool = get_pool()
    hoje = date.today()
    try:
        ano_sel, mes_num = (int(x) for x in mes.split("-")) if mes else (hoje.year, hoje.month)
    except ValueError:
        ano_sel, mes_num = hoje.year, hoje.month
    mes_sel = f"{ano_sel:04d}-{mes_num:02d}"
    membro_sel = int(membro) if membro.isdigit() else None

    with pool.connection() as c:
        pessoas = c.execute(
            "select id, coalesce(nome,'-') from membros where conta_id=%s and ativo order by id",
            (conta[0],)).fetchall()
    if membro_sel is not None and membro_sel not in {p[0] for p in pessoas}:
        membro_sel = None

    livro = LivroCaixa(pool, conta[0])
    resumo = livro.resumo_mes(ano_sel, mes_num, membro_sel)
    categorias = livro.despesas_por_categoria(ano_sel, mes_num, membro_sel)
    maior_cat = max((v for _, v in categorias), default=0)
    lancamentos = livro.lancamentos_recentes(ano_sel, mes_num, membro_sel,
                                             tipo if tipo in ("despesa", "receita") else None)
    # agrupa por DIA (pro accordion): cada dia com seu saldo e seus lancamentos
    from collections import OrderedDict
    por_dia = OrderedDict()
    for l in lancamentos:
        d = l["data"]
        if d not in por_dia:
            por_dia[d] = {"itens": [], "saldo": 0}
        por_dia[d]["itens"].append(l)
        por_dia[d]["saldo"] += l["valor"] if l["tipo"] == "receita" else -l["valor"]
    dias = [{"data": d, "itens": g["itens"], "saldo": g["saldo"]} for d, g in por_dia.items()]
    raiox_bruto = livro.raiox_por_departamento(membro_id=membro_sel)
    # monta {dep: {total, dias:[{data, itens, subtotal}]}} - itens divididos por dia
    from collections import OrderedDict
    raiox = {}
    for dep, itens in raiox_bruto.items():
        por_dia = OrderedDict()
        for it in itens:
            por_dia.setdefault(it["data"], []).append(it)
        dias_dep = [{"data": d, "itens": its, "subtotal": sum(i["valor"] for i in its)}
                    for d, its in por_dia.items()]
        raiox[dep] = {"total": sum(i["valor"] for i in itens), "dias": dias_dep}

    meses = []
    y, m = hoje.year, hoje.month
    nomes = ["jan","fev","mar","abr","mai","jun","jul","ago","set","out","nov","dez"]
    for _ in range(6):
        meses.append((f"{y:04d}-{m:02d}", f"{nomes[m-1]}/{y}"))
        m -= 1
        if m == 0:
            m = 12; y -= 1

    return _render("dash", request, titulo="Financeiro", conta=conta,
                   resumo=resumo, categorias=categorias, maior_cat=maior_cat,
                   lancamentos=lancamentos, dias=dias, raiox=raiox, pessoas=pessoas,
                   meses=meses, mes_sel=mes_sel, membro_sel=membro_sel, tipo_sel=tipo)


# ---------- lista de compras ----------

def _lista_logada(request: Request):
    conta = conta_logada(request)
    if conta is None:
        return None, None
    pool = get_pool()
    with pool.connection() as c:
        m = c.execute("select id from membros where conta_id=%s order by id limit 1",
                      (conta[0],)).fetchone()
    membro_id = m[0] if m else None
    return conta, ListaCompras(pool, conta[0], membro_id)


@router.get("/painel/compras", response_class=HTMLResponse)
def compras(request: Request):
    conta, lista = _lista_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    livro = LivroCaixa(get_pool(), conta[0])
    def _estimador(desc):
        try:
            itens_achados, _total = livro.buscar_itens(desc, dias=180)
            precos = [it["valor_total_centavos"] for it in itens_achados
                      if it.get("valor_total_centavos")]
            if precos:
                return int(sum(precos) / len(precos)), "historico"
        except Exception:  # noqa: BLE001
            pass
        return None, None
    try:
        lista.estimar_precos(_estimador)
    except Exception:  # noqa: BLE001
        pass
    itens = lista.listar(incluir_comprados=True)
    return _render("compras", request, titulo="Compras", itens=itens, resumo=lista.resumo())


@router.post("/painel/compras/add")
def compras_add(request: Request, descricao: str = Form(...)):
    conta, lista = _lista_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    lista.adicionar(descricao.strip())
    return RedirectResponse("/painel/compras", status_code=303)


@router.post("/painel/compras/marcar")
def compras_marcar(request: Request, item_id: int = Form(...), comprado: int = Form(1)):
    conta, lista = _lista_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    lista.marcar_comprado(item_id, bool(comprado))
    return RedirectResponse("/painel/compras", status_code=303)


@router.post("/painel/compras/remover")
def compras_remover(request: Request, item_id: int = Form(...)):
    conta, lista = _lista_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    lista.remover(item_id)
    return RedirectResponse("/painel/compras", status_code=303)


@router.post("/painel/compras/limpar")
def compras_limpar(request: Request):
    conta, lista = _lista_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    lista.limpar_comprados()
    return RedirectResponse("/painel/compras", status_code=303)


@router.post("/membros/adicionar")
def membros_adicionar(request: Request, nome: str = Form(...), whatsapp: str = Form(...),
                      papel: str = Form("membro")):
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
    papel = papel if papel in ("membro", "restrito") else "membro"
    ct.adicionar_membro(pool, conta[0], nome=nome.strip(), papel=papel, whatsapp_id=zap)
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
