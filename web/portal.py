"""Portal do OpenClaw: cadastro, login e painel (Bloco A+B+C).

Vive dentro do openclaw-web. Regra sagrada: toda pagina logada enxerga
APENAS a conta da sessao (isolamento multi-tenant na camada web).
Senhas: hash scrypt (stdlib) com sal aleatorio - nunca em texto puro.
"""
import hashlib
import os
import secrets

from fastapi import APIRouter, Request, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
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
            "select id, tipo, nome, email, plano, status, vencimento, cidade from contas where id = %s",
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
.membros{display:flex;flex-direction:column;gap:6px;margin-top:.6rem}
.membro-row{display:flex;align-items:center;gap:12px;flex-wrap:wrap;padding:.7rem .8rem;background:#0e0e0f;border:1px solid #2a2a2b;border-radius:10px}
.membro-id{display:flex;align-items:center;gap:10px;flex:1 1 180px;min-width:0}
.avatar{width:34px;height:34px;border-radius:50%;background:#1d3a30;color:#5dcaa5;display:flex;align-items:center;justify-content:center;font-weight:600;flex-shrink:0}
.membro-nome{font-size:.95rem}
.membro-papel{font-size:.78rem;color:#a8a8a3}
.membro-contato{flex:1 1 200px;font-size:.84rem;color:#c5c5c0;min-width:0}
.membro-contato .conv code{background:#161617;border:1px solid #2a2a2b;border-radius:6px;padding:.1rem .4rem;font-size:.82rem;color:#5dcaa5}
.membro-acoes{display:flex;gap:6px;flex:0 0 auto}
.membro-acoes form{margin:0}
.membro-acoes button{margin:0;padding:.35rem .7rem;font-size:.78rem;width:auto}
.btn-conv{background:#1d6e9e}.btn-conv:hover{background:#2480b5}
.btn-off{background:#6e2b2b}.btn-off:hover{background:#8a3636}
.btn-on{background:#1d9e75}
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
.conv-links{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.lk-tg,.lk-copy,.lk-wa{padding:.4rem .7rem;border-radius:8px;font-size:.8rem;border:1px solid #243049;text-decoration:none;display:inline-block;line-height:1}
.lk-tg{background:#2AABEE;color:#fff;border-color:#2AABEE}
.lk-copy{background:#1a2233;color:#e7ecf3;cursor:pointer}
.lk-wa{background:#11201a;color:#5a6b62;border-color:#1e3a2e;cursor:not-allowed}
.lk-wpp{background:#25D366;color:#fff;border-color:#25D366}
.conv-canal{display:flex;flex-direction:column;gap:8px;margin-top:6px}
.conv-canal .conv-links{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
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
<label>Sua cidade <span class="mut">(pra comparar preços perto de você)</span></label>
<select name="cidade">{% for cod, nome in cidades %}<option value="{{ cod }}">{{ nome }}</option>{% endfor %}</select>
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

_BEMVINDO = """{% extends "base" %}{% block conteudo %}
<div style="max-width:420px;margin:2rem auto;text-align:center">
  <div style="width:56px;height:56px;border-radius:50%;background:#11402e;display:flex;align-items:center;justify-content:center;margin:0 auto 1rem;font-size:28px">✅</div>
  <h1 style="margin:0 0 .4rem">Bem-vindo, {{ nome_pessoa }}!</h1>
  <p class="mut" style="margin:0 0 1.5rem">Sua conta está pronta. Falta só conectar seu Telegram pra começar.</p>
  {% if ja_conectado %}
  <div class="card"><p>Seu Telegram já está conectado! 🎉</p>
  <a class="btn" href="/painel">Ir pro painel →</a></div>
  {% else %}
  <div class="card" style="text-align:left">
    <p class="mut" style="margin:0 0 .85rem">Conecte seu Telegram pra conversar com seu assistente — registrar gastos, ver saldo, montar a lista de compras.</p>
    <a href="https://t.me/{{ bot }}?text={{ codigo|urlencode }}" target="_blank" rel="noopener"
       style="display:flex;align-items:center;justify-content:center;gap:8px;padding:.8rem;border-radius:8px;text-decoration:none;background:#2AABEE;color:#fff;font-weight:600;margin-bottom:8px">
       📨 Conectar meu Telegram</a>
    {% if whatsapp_bot_num %}
    <a href="https://wa.me/{{ whatsapp_bot_num }}?text={{ codigo|urlencode }}" target="_blank" rel="noopener"
       style="display:flex;align-items:center;justify-content:center;gap:8px;padding:.8rem;border-radius:8px;text-decoration:none;background:#25D366;color:#fff;font-weight:600">
       🟢 Conectar meu WhatsApp</a>
    {% else %}
    <span title="WhatsApp não configurado"
       style="display:flex;align-items:center;justify-content:center;gap:8px;padding:.8rem;border-radius:8px;background:#11201a;color:#5a6b62;cursor:not-allowed">
       🟢 WhatsApp (não disponível)</span>
    {% endif %}
    <p class="mut" style="font-size:.8rem;margin:.85rem 0 0">Use seu código <code>{{ codigo }}</code> em qualquer canal (Telegram ou WhatsApp) — é só enviar.</p>
  </div>
  <a href="/painel" class="mut" style="font-size:.85rem;display:inline-block;margin-top:1rem">Pular e ir pro painel →</a>
  {% endif %}
</div>
{% endblock %}"""

_PAINEL = """{% extends "base" %}{% block conteudo %}
<div class="card larga"><h1>Olá, {{ conta[2] }}! <span class="tag">{{ conta[5] }}</span></h1>
<p class="mut">Plano: <b>{{ conta[4] or '-' }}</b>
{% if conta[6] %} · válido até <b>{{ conta[6].strftime('%d/%m/%Y') }}</b>{% endif %}
 · tipo: <b>{{ conta[1]|upper }}</b></p>
<h1 style="font-size:1.05rem;margin-top:1.4rem">Pessoas da conta</h1>
<div class="membros">
{% for m in membros %}
<div class="membro-row">
<div class="membro-id">
<div class="avatar">{{ (m[0] or '?')[0]|upper }}</div>
<div><div class="membro-nome">{{ m[0] or '-' }}</div>
<div class="membro-papel"><span class="tag">{{ m[1] }}</span> {{ '' if m[3] else '· desativado' }}</div></div>
</div>
<div class="membro-contato">
{% if m[2] %}<div class="zap">📱 {{ m[2] }}</div>{% endif %}
{% if m[5] %}
  <div class="conv">🔑 <code>{{ m[5] }}</code> <span class="mut">— convite pendente</span></div>
  <div class="conv-canal">
    <div class="conv-links">
      <a class="lk-tg" href="https://t.me/clawaladdin_bot?text={{ m[5]|urlencode }}" target="_blank" rel="noopener">📨 Convidar no Telegram</a>
      <button type="button" class="lk-copy" onclick="copiarConvite(this, 'https://t.me/clawaladdin_bot?text={{ m[5]|urlencode }}')">🔗 Copiar link</button>
    </div>
    {% if whatsapp_bot_num %}
    <div class="conv-links">
      <a class="lk-wpp" href="https://wa.me/{{ whatsapp_bot_num }}?text={{ m[5]|urlencode }}" target="_blank" rel="noopener">🟢 Convidar no WhatsApp</a>
      <button type="button" class="lk-copy" onclick="copiarConvite(this, 'https://wa.me/{{ whatsapp_bot_num }}?text={{ m[5]|urlencode }}')">🔗 Copiar link</button>
    </div>
    {% endif %}
  </div>
{% else %}
  {% if m[6] %}<div class="conv mut">✅ Telegram conectado</div>{% endif %}
  {% if m[2] %}<div class="conv mut">✅ WhatsApp conectado</div>{% endif %}
  {% if not m[2] and not m[6] %}<span class="mut">sem contato vinculado</span>{% endif %}
{% endif %}
</div>
{% if m[1] != 'dono' %}
<div class="membro-acoes">
<form method="post" action="/membros/convite"><input type="hidden" name="membro_id" value="{{ m[4] }}">
<button class="btn-conv">↻ convite</button></form>
{% if m[3] %}
<form method="post" action="/membros/desativar"><input type="hidden" name="membro_id" value="{{ m[4] }}">
<button class="btn-off">desativar</button></form>
{% else %}
<form method="post" action="/membros/reativar"><input type="hidden" name="membro_id" value="{{ m[4] }}">
<button class="btn-on">reativar</button></form>
{% endif %}
</div>
{% else %}<div class="membro-acoes mut" style="align-self:center">titular</div>{% endif %}
</div>
{% endfor %}
</div>
<p class="mut" style="margin-top:1rem">O código 🔑 serve pro Telegram (a pessoa envia no bot ClawIAOpen).
Quem tem WhatsApp 📱 cadastrado também já usa por lá.</p>
</div>
<div class="card larga"><h1 style="font-size:1.05rem">Adicionar pessoa</h1>
{% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
{% if aviso %}<div class="ok">{{ aviso }}</div>{% endif %}
{% if pode_adicionar %}
<form method="post" action="/membros/adicionar">
<label>Nome</label><input name="nome" required maxlength="80">
<label>WhatsApp (com DDD) <span class="mut">— opcional</span></label><input name="whatsapp" maxlength="20" placeholder="86 98888-7777">
<label>Tipo de acesso</label>
<select name="papel">
<option value="membro">Membro — vê finanças e usa a lista de compras</option>
<option value="restrito">Restrito — só a lista de compras (ex: empregada)</option>
</select>
<button>Adicionar à conta</button></form>
<p class="mut" style="margin-top:.6rem">Ao adicionar, você recebe um <b>código de convite</b>.
Peça pra pessoa abrir o bot <b>ClawIAOpen</b> no Telegram e enviar o código — pronto, ela é vinculada.</p>
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
function copiarConvite(btn, url){
  navigator.clipboard.writeText(url).then(function(){
    var txt = btn.textContent;
    btn.textContent = '✅ Copiado!';
    setTimeout(function(){ btn.textContent = txt; }, 1500);
  }).catch(function(){ window.prompt('Copie o link:', url); });
}
</script>
{% endblock %}"""

_COMPRAS = """{% extends "base" %}{% block conteudo %}
<div class="card larga">
<div style="display:flex; justify-content:space-between; align-items:center; flex-wrap:wrap; gap:.5rem">
<h1 style="margin:0">Lista de compras</h1>
<div style="display:flex; gap:.5rem">
{% if papel != 'dono' %}
<button id="btn-avisar" onclick="avisarTerminei()" style="margin:0;padding:.4rem .8rem;background:#2AABEE;color:#fff;font-size:.82rem;width:auto;border-radius:8px;border:0;cursor:pointer">✅ Avisar que terminei</button>
<span id="aviso-msg" class="mut" style="font-size:.85rem;margin-left:.5rem"></span>
{% endif %}
<button id="btn-apagar" style="margin:0;padding:.4rem .8rem;background:#2a2a2b;font-size:.82rem;width:auto;display:none;color:#d98a8a">Apagar lista</button>
</div>
</div>

<form id="form-add" style="display:flex; gap:.5rem; margin:1rem 0">
<input id="inp-add" placeholder="Adicionar item (ex: arroz, café...)" required maxlength="80" style="flex:1" autocomplete="off">
<button style="margin:0; width:auto; padding:.65rem 1.2rem">Adicionar</button>
</form>

<button id="btn-comparar" onclick="carregarPrecos()" style="margin:.5rem 0;padding:.4rem 1rem;background:#6366f1;color:#fff;border:0;border-radius:8px;cursor:pointer;font-size:.9rem">📊 Comparar preços</button>

<div id="comparador" style="margin:1rem 0"></div>

<div id="toggle-visao" style="display:none; gap:.5rem; margin:.5rem 0">
  <button id="bt-geral" onclick="setVisao('geral')"
    style="margin:0;padding:.4rem .9rem;border-radius:8px;border:0;cursor:pointer;background:#1d9e75;color:#fff">Geral</button>
  <button id="bt-pessoa" onclick="setVisao('pessoa')"
    style="margin:0;padding:.4rem .9rem;border-radius:8px;border:1px solid #2a2a2b;cursor:pointer;background:#1a2233;color:#e7ecf3">Por pessoa</button>
</div>

<div id="lista-itens"></div>
<p id="resumo-lista" class="mut" style="margin-top:1rem"></p>
</div>

<script>
(function(){
  var listaEl = document.getElementById('lista-itens');
  var resumoEl = document.getElementById('resumo-lista');
  var compEl = document.getElementById('comparador');
  var btnApagar = document.getElementById('btn-apagar');
  var ajustes = {};
  var pendentesAtuais = [];

  function esc(s){ var d=document.createElement('div'); d.textContent=s; return d.innerHTML; }

  var _visao = 'geral';
  var _itensCache = [];

  function setVisao(v){
    _visao = v;
    document.getElementById('bt-geral').style.background = v==='geral' ? '#1d9e75' : '#1a2233';
    document.getElementById('bt-geral').style.color = '#fff';
    document.getElementById('bt-pessoa').style.background = v==='pessoa' ? '#1d9e75' : '#1a2233';
    document.getElementById('bt-pessoa').style.color = '#fff';
    renderItens(_itensCache);
  }

  function linhaItem(i){
    var preco = i.preco ? '<span class="mut"> · ~'+i.preco+'</span>' : '';
    var h = '<tr style="'+(i.comprado?'opacity:.5':'')+'">';
    h += '<td style="width:40px"><button class="mk" data-id="'+i.id+'" data-c="'+(i.comprado?0:1)+'" title="marcar" style="margin:0;padding:.25rem .55rem;background:'+(i.comprado?'#1d9e75':'#2a2a2b')+';font-size:.9rem">✓</button></td>';
    h += '<td style="'+(i.comprado?'text-decoration:line-through':'')+'">'+esc(i.descricao)+preco+'</td>';
    if (_visao === 'geral') h += '<td class="mut" style="font-size:.8rem">'+esc(i.quem||'')+'</td>';
    h += '<td style="width:40px;text-align:right"><button class="rm" data-id="'+i.id+'" title="remover" style="margin:0;padding:.25rem .5rem;background:transparent;color:#8a3636;font-size:.95rem">✕</button></td>';
    h += '</tr>';
    return h;
  }

  function renderItens(itens){
    if (!itens.length){ listaEl.innerHTML = '<p class="mut">A lista está vazia. Adicione itens acima — ou peça pelo WhatsApp/Telegram: <i>"acabou o arroz, bota na lista"</i>.</p>'; return; }
    var nomes = {}; itens.forEach(function(i){ nomes[i.quem||'-'] = 1; });
    var temVarias = Object.keys(nomes).length > 1;
    document.getElementById('toggle-visao').style.display = temVarias ? 'flex' : 'none';

    var html = '';
    if (_visao === 'pessoa' && temVarias){
      Object.keys(nomes).sort().forEach(function(nome){
        var doNome = itens.filter(function(i){ return (i.quem||'-') === nome; });
        html += '<div style="margin:.6rem 0 .2rem;font-weight:600">'+esc(nome)+' <span class="mut" style="font-weight:400;font-size:.8rem">('+doNome.length+')</span></div>';
        html += '<table style="margin:0">' + doNome.map(linhaItem).join('') + '</table>';
      });
    } else {
      html = '<table style="margin-top:.5rem">' + itens.map(linhaItem).join('') + '</table>';
    }
    listaEl.innerHTML = html;
    ligarBotoesLinha();
  }

  function ligarBotoesLinha(){
    Array.prototype.forEach.call(listaEl.querySelectorAll('.mk'), function(b){
      b.onclick = function(){ acao({acao:'marcar', item_id:+b.getAttribute('data-id'), comprado:+b.getAttribute('data-c')}); };
    });
    Array.prototype.forEach.call(listaEl.querySelectorAll('.rm'), function(b){
      b.onclick = function(){ acao({acao:'remover', item_id:+b.getAttribute('data-id')}); };
    });
  }

  function acao(payload){
    return fetch('/painel/compras/api', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(payload)
    }).then(function(r){ return r.json(); }).then(function(d){ render(d); return d; });
  }

  function render(d){
    var itens = d.itens || [];
    _itensCache = itens;
    pendentesAtuais = itens.filter(function(i){return !i.comprado;}).map(function(i){return i.descricao;});
    // limpa ajustes orfaos (item removido/comprado nao deve manter ajuste)
    Object.keys(ajustes).forEach(function(k){
      if (pendentesAtuais.indexOf(k) === -1) delete ajustes[k];
    });
    if (!itens.length){
      listaEl.innerHTML = '<p class="mut">A lista está vazia. Adicione itens acima — ou peça pelo WhatsApp/Telegram: <i>"acabou o arroz, bota na lista"</i>.</p>';
      resumoEl.textContent = '';
      btnApagar.style.display = 'none';
      compEl.innerHTML = '';
      return;
    }
    renderItens(itens);
    resumoEl.textContent = d.pendentes + ' item(ns) na lista';
    btnApagar.style.display = d.pendentes ? '' : 'none';
  }

  document.getElementById('form-add').onsubmit = function(e){
    e.preventDefault();
    var inp = document.getElementById('inp-add');
    var v = inp.value.trim();
    if (!v) return;
    inp.value = '';                 // limpa na hora, sutil
    // NAO zera os ajustes ja' feitos - so' remove ajuste de item que nao existe
    // mais (limpeza acontece no render, comparando com pendentesAtuais)
    acao({acao:'add', descricao:v});
  };
  btnApagar.onclick = function(){
    if (!confirm('Apagar a lista inteira? Essa acao nao pode ser desfeita.')) return;
    acao({acao:'apagar_tudo'});
  };

  var prog = null;
  function barraProgresso(){
    var n = pendentesAtuais.length || 1, i = 0;
    compEl.innerHTML = '<div style="margin:.4rem 0"><div class="mut" style="font-size:.82rem" id="prog-txt">Buscando os melhores preços perto de você...</div>'
      + '<div style="height:6px;background:#1d1d1f;border-radius:4px;margin-top:.4rem;overflow:hidden">'
      + '<div id="prog-bar" style="height:100%;width:8%;background:#5dcaa5;transition:width .4s"></div></div></div>';
    var bar = document.getElementById('prog-bar'), txt = document.getElementById('prog-txt');
    if (prog) clearInterval(prog);
    prog = setInterval(function(){
      i = Math.min(i+1, n);
      if (bar) bar.style.width = Math.min(8 + (i/n)*84, 92) + '%';
      if (txt) txt.textContent = 'Buscando preços... ' + Math.min(i, n) + ' de ' + n + ' itens';
    }, 500);
  }

  var reqToken = 0;
  function carregarPrecos(){
    if (!pendentesAtuais.length){ compEl.innerHTML=''; if(prog) clearInterval(prog); return; }
    barraProgresso();
    var meuToken = ++reqToken;   // so' a chamada mais recente pode escrever
    var params = Object.keys(ajustes).map(function(it){
      return 'ajuste=' + encodeURIComponent(it + '||' + ajustes[it]);
    }).join('&');
    fetch('/painel/compras/precos' + (params ? ('?'+params) : ''))
      .then(function(r){ return r.json(); })
      .then(function(d){
        if (meuToken !== reqToken) return;   // chegou atrasada: ignora
        if (prog) clearInterval(prog);
        var grupos = d.grupos || {}, nomes = Object.keys(grupos), html = '';
        if (!nomes.length){
          html = '<p class="mut" style="font-size:.82rem">📈 Ainda não tenho preços suficientes pra comparar essa lista aqui na sua região.</p>';
        } else {
          nomes.forEach(function(grupo){
            var linhas = grupos[grupo];
            html += '<div style="background:#0e0e0f;border:1px solid #2a2a2b;border-radius:10px;padding:1rem;margin:1rem 0">';
            html += '<div style="font-weight:600;margin-bottom:.6rem">'+grupo+' — onde sua cesta sai mais barata</div>';
            linhas.forEach(function(m, i){
              var falta = m.faltando ? (' · faltam '+m.faltando) : ' · completa';
              var endTxt = m.endereco || '';
              if (m.data) endTxt += (endTxt ? ' · ' : '') + 'preço de ' + m.data;
              var end = endTxt ? ('<div class="mut" style="font-size:.72rem">'+esc(endTxt)+'</div>') : '';
              html += '<div style="padding:.45rem 0;'+(i<linhas.length-1?'border-bottom:1px solid #1d1d1f':'')+'">';
              html += '<div style="display:flex;justify-content:space-between">';
              html += '<div>'+(i+1)+'. <b>'+esc(m.mercado)+'</b> <span class="mut" style="font-size:.76rem">· '+m.cobertos+' itens'+falta+'</span>'+end+'</div>';
              html += '<b style="color:#5dcaa5">'+m.total+'</b></div>';
              if (m.produtos && m.produtos.length){
                html += '<div style="margin:.3rem 0 0 1.1rem">';
                m.produtos.forEach(function(pr){
                  html += '<div class="mut" style="font-size:.72rem;display:flex;justify-content:space-between"><span>'+esc(pr.descricao)+'</span><span>'+pr.preco+'</span></div>';
                });
                html += '</div>';
              }
              html += '</div>';
            });
            html += '</div>';
          });
          var fonte = d.fonte === 'sefaz'
            ? 'Preços oficiais de nota fiscal (SEFAZ), atualizados há pouco.'
            : 'Baseado em '+d.observacoes+' preços dos seus cupons — melhora a cada compra.';
          html += '<p class="mut" style="font-size:.72rem">'+fonte+'</p>';
        }
        html += '<div style="margin-top:.6rem"><div class="mut" style="font-size:.78rem;margin-bottom:.3rem">Não bateu? Ajuste o produto certo:</div><div style="display:flex;flex-wrap:wrap;gap:6px">';
        pendentesAtuais.forEach(function(it){
          var marca = ajustes[it] ? ' ✓' : '';
          html += '<button type="button" class="aj" data-item="'+encodeURIComponent(it)+'" style="margin:0;padding:.25rem .6rem;background:#2a2a2b;font-size:.74rem;width:auto">'+esc(it)+marca+'</button>';
        });
        html += '</div><div id="aj-opcoes"></div></div>';
        compEl.innerHTML = html;
        Array.prototype.forEach.call(compEl.querySelectorAll('.aj'), function(b){
          b.onclick = function(){ abrirOpcoes(decodeURIComponent(b.getAttribute('data-item'))); };
        });
      }).catch(function(){
        if (prog) clearInterval(prog);
        compEl.innerHTML = '<p class="mut" style="font-size:.8rem">Não consegui buscar os preços agora. Tente recarregar.</p>';
      });
  }

  function abrirOpcoes(item){
    var cx = document.getElementById('aj-opcoes');
    cx.innerHTML = '<div class="mut" style="font-size:.78rem;margin-top:.5rem">🔄 Buscando opções de "'+esc(item)+'"...</div>';
    fetch('/painel/compras/opcoes?item='+encodeURIComponent(item))
      .then(function(r){ return r.json(); })
      .then(function(d){
        var ops = d.opcoes || [];
        if (!ops.length){ cx.innerHTML = '<div class="mut" style="font-size:.76rem;margin-top:.5rem">Sem catálogo de produtos pra essa região (a comparação usa seus cupons).</div>'; return; }
        var html = '<div style="background:#0e0e0f;border:1px solid #2a2a2b;border-radius:10px;padding:.8rem;margin-top:.5rem">';
        html += '<div style="font-size:.8rem;margin-bottom:.4rem">Qual "'+esc(item)+'" você quer?</div>';
        ops.forEach(function(o){
          html += '<button type="button" class="op" data-termo="'+encodeURIComponent(o.descricao)+'" style="display:flex;justify-content:space-between;width:100%;margin:.15rem 0;padding:.4rem .6rem;background:#161617;font-size:.78rem;text-align:left"><span>'+esc(o.descricao)+'</span><span class="mut">'+o.faixa+'</span></button>';
        });
        html += '</div>';
        cx.innerHTML = html;
        Array.prototype.forEach.call(cx.querySelectorAll('.op'), function(b){
          b.onclick = function(){ ajustes[item] = decodeURIComponent(b.getAttribute('data-termo')); carregarPrecos(); };
        });
      }).catch(function(){ cx.innerHTML=''; });
  }

  acao({acao:'noop'});

  // Exportar funcoes pra escopo global (botoes do toggle precisam acessar)
  window.setVisao = setVisao;
  window.renderItens = renderItens;
  window.ligarBotoesLinha = ligarBotoesLinha;
  window.carregarPrecos = carregarPrecos;
})();

function avisarTerminei(){
  var btn = document.getElementById('btn-avisar');
  btn.disabled = true;
  fetch('/painel/compras/avisar', {method:'POST'})
    .then(function(r){ return r.json(); })
    .then(function(d){
      document.getElementById('aviso-msg').textContent = d.msg || 'Avisado!';
      setTimeout(function(){ btn.disabled = false; }, 3000);
    })
    .catch(function(){ btn.disabled = false; });
}
</script>
{% endblock %}"""

_env = Environment(loader=DictLoader({
    "base": _BASE, "cadastro": _CADASTRO, "login": _LOGIN, "bemvindo": _BEMVINDO, "painel": _PAINEL, "senha": _SENHA, "dash": _DASH, "compras": _COMPRAS,
}), autoescape=select_autoescape())
_env.globals["brl"] = brl
from finance import cidades as _cidades_mod
_env.globals["cidades"] = _cidades_mod.opcoes()


def _render(nome: str, request: Request, **ctx) -> HTMLResponse:
    ctx.setdefault("logado", bool(request.session.get("conta_id")))
    ctx.setdefault("titulo", nome.capitalize())
    return HTMLResponse(_env.get_template(nome).render(**ctx))


# ---------- rotas ----------

@router.get("/cadastro", response_class=HTMLResponse)
def cadastro_form(request: Request):
    return _render("cadastro", request, planos=_planos(), erro=None)


@router.post("/cadastro", response_class=HTMLResponse)
def cadastro_envia(request: Request, background: BackgroundTasks,
                   plano: str = Form(...), nome: str = Form(...),
                   email: str = Form(...), senha: str = Form(...),
                   documento: str = Form(""), whatsapp: str = Form(...),
                   cidade: str = Form("")):
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
    from finance import cidades as _cid
    conta_id = ct.criar_conta(pool, tipo, nome.strip(), plano=plano, documento=doc,
                              cidade=_cid.valida(cidade))  # trial 7d
    with pool.connection() as c:
        c.execute("update contas set email=%s, senha_hash=%s where id=%s",
                  (email, hash_senha(senha), conta_id))
        c.commit()
    ct.adicionar_membro(pool, conta_id, nome=nome.strip(), papel="dono", whatsapp_id=zap)
    codigo_dono = ct.gerar_convite_dono(pool, conta_id)  # codigo pro dono conectar Telegram
    request.session["conta_id"] = conta_id
    # email de boas-vindas (tolerante a falha - nunca quebra o cadastro)
    try:
        from finance.email_sender import enviar_boas_vindas
        background.add_task(enviar_boas_vindas, email, nome.strip(), codigo_dono)
    except Exception:  # noqa: BLE001
        pass
    return RedirectResponse("/bem-vindo", status_code=303)


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


@router.get("/bem-vindo", response_class=HTMLResponse)
def bem_vindo(request: Request):
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    with pool.connection() as c:
        row = c.execute(
            "select nome, codigo_convite, telegram_id from membros "
            "where conta_id=%s and papel='dono'", (conta[0],)).fetchone()
    nome = (row[0] if row else None) or "tudo certo"
    codigo = row[1] if row else None
    ja_conectado = bool(row and row[2])
    whatsapp_bot_num = (os.environ.get("TWILIO_WHATSAPP_FROM") or "").replace("whatsapp:+", "")
    return _render("bemvindo", request, nome_pessoa=nome, codigo=codigo,
                   ja_conectado=ja_conectado, bot="clawaladdin_bot",
                   whatsapp_bot_num=whatsapp_bot_num)


@router.get("/painel", response_class=HTMLResponse)
def painel(request: Request):
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    with pool.connection() as c:
        membros = c.execute(
            "select nome, papel, whatsapp_id, ativo, id, codigo_convite, telegram_id from membros where conta_id=%s order by id",
            (conta[0],),
        ).fetchall()
    ativos, inclusos, pode_extra = _limite_membros(conta)
    pode_adicionar = pode_extra or ativos < inclusos
    # numero do WhatsApp bot (sem "whatsapp:+")
    whatsapp_from = (os.environ.get("TWILIO_WHATSAPP_FROM") or "").replace("whatsapp:+", "")
    return _render("painel", request, conta=conta, membros=membros, titulo="Painel",
                   ativos=ativos, inclusos=inclusos, extra_pago=pode_extra,
                   pode_adicionar=pode_adicionar,
                   whatsapp_bot_num=whatsapp_from,
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
                                             tipo if tipo in ("despesa", "receita") else None,
                                             limite=1000)
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
    raiox_bruto = livro.raiox_por_departamento(ano=ano_sel, mes=mes_num, membro_id=membro_sel)
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
    # busca o papel do usuario logado (dono, membro, restrito)
    pool = get_pool()
    with pool.connection() as c:
        m = c.execute(
            "select papel from membros where conta_id=%s order by id limit 1",
            (conta[0],)).fetchone()
    papel = m[0] if m else "membro"
    # a pagina carrega "vazia" e o JS busca a lista e os precos via AJAX
    # (sem reload a cada acao). Ver endpoint /painel/compras/api.
    return _render("compras", request, titulo="Compras", papel=papel)


@router.get("/painel/compras/precos")
def compras_precos(request: Request):
    """Endpoint chamado pelo JS em segundo plano: calcula o comparador separado
    por departamento e devolve JSON. A pagina ja' carregou; isso preenche depois."""
    conta = conta_logada(request)
    if conta is None:
        return JSONResponse({"erro": "nao logado"}, status_code=401)
    from finance.banco_precos import comparar_separado
    from finance.lista_compras import ListaCompras
    lista = ListaCompras(get_pool(), conta[0])
    pendentes = [i["descricao"] for i in lista.listar(incluir_comprados=False)]
    if not pendentes:
        return JSONResponse({"grupos": {}, "observacoes": 0, "fonte": "vazio"})
    # escolhas de ajuste: parametros ajuste=item||termo
    escolhas: dict[str, str] = {}
    for a in request.query_params.getlist("ajuste"):
        if "||" in a:
            item, termo = a.split("||", 1)
            if item.strip() and termo.strip():
                escolhas[item.strip()] = termo.strip()
    with get_pool().connection() as c:
        row = c.execute("select cidade from contas where id=%s", (conta[0],)).fetchone()
    cidade = row[0] if row else None
    try:
        r = comparar_separado(get_pool(), pendentes, cidade, escolhas)
    except Exception:  # noqa: BLE001
        return JSONResponse({"grupos": {}, "observacoes": 0, "fonte": "erro"})
    return JSONResponse(_fmt_comparacao(r))


def _fmt_comparacao(r: dict) -> dict:
    """Formata o resultado do comparador pra JSON amigavel ao front (com BRL).
    Popula endereco (da loja com melhor preco) e produtos (nomes completos de cada item)."""
    rotulos = {"mercado": "🏪 Mercado", "farmacia": "💊 Farmácia", "outro": "🏪 Mercado"}
    grupos = {}
    itens_detalhe = {item["descricao"]: item for item in r.get("itens", [])}

    for tipo, g in r.get("grupos", {}).items():
        linhas = []
        for m in g["mercados"]:
            nome_merc = m["mercado"]
            # monta lista de produtos com nome completo e preco pra este mercado
            produtos = []
            endereco_merc = None
            for item_desc, item_info in itens_detalhe.items():
                # procura se tem preco deste item neste mercado
                for preco in item_info.get("precos", []):
                    if preco["mercado"] == nome_merc:
                        produtos.append({
                            "descricao": preco["descricao"],  # nome completo (ex "Arroz Camil 5kg")
                            "preco": brl(preco["valor_centavos"])
                        })
                        # pega endereco do 1o preco (mais barato deste mercado)
                        if endereco_merc is None and preco.get("endereco"):
                            endereco_merc = preco["endereco"]
                        break
            linhas.append({
                "mercado": nome_merc,
                "total": brl(m["total_centavos"]),
                "cobertos": m["itens_cobertos"],
                "faltando": len(m["itens_faltando"]),
                "endereco": endereco_merc or "",
                "produtos": produtos,
            })
        if linhas:
            grupos[rotulos.get(tipo, tipo)] = linhas
    return {"grupos": grupos, "observacoes": r.get("observacoes", 0), "fonte": r.get("fonte", "vazio")}


@router.get("/painel/compras/opcoes")
def compras_opcoes(request: Request, item: str = ""):
    """Lista as variantes reais de produto pra um item (pro 'ajustar')."""
    conta = conta_logada(request)
    if conta is None:
        return JSONResponse({"erro": "nao logado"}, status_code=401)
    item = (item or "").strip()
    if not item:
        return JSONResponse({"opcoes": []})
    with get_pool().connection() as c:
        row = c.execute("select cidade from contas where id=%s", (conta[0],)).fetchone()
    cidade = row[0] if row else None
    from finance import cidades as cid
    coord = cid.coordenada(cidade)
    if not coord or cid.fonte(cidade) != "sefaz":
        return JSONResponse({"opcoes": [], "motivo": "regiao sem catalogo SEFAZ"})
    try:
        from finance.sefaz_precos import SefazMenorPreco
        lat, lon, raio = coord
        ops = SefazMenorPreco().opcoes_produto(item, lat, lon, raio)
    except Exception:  # noqa: BLE001
        return JSONResponse({"opcoes": []})
    return JSONResponse({"opcoes": [
        {"descricao": o["descricao"],
         "faixa": (brl(o["min"]) if o["min"] == o["max"] else f"{brl(o['min'])}–{brl(o['max'])}")}
        for o in ops]})


@router.post("/painel/compras/api")
async def compras_api(request: Request):
    """Acao na lista SEM reload: recebe {acao, ...} e devolve a lista atualizada
    em JSON. O front redesenha so' a lista, sem recarregar a pagina."""
    conta, lista = _lista_logada(request)
    if conta is None:
        return JSONResponse({"erro": "nao logado"}, status_code=401)
    dados = await request.json()
    acao = dados.get("acao")
    if acao == "add":
        import re as _re
        partes = [p.strip() for p in _re.split(r"[,;\n]+", dados.get("descricao", "")) if p.strip()]
        if len(partes) > 1:
            lista.adicionar_varios(partes)
        elif partes:
            lista.adicionar(partes[0])
    elif acao == "marcar":
        lista.marcar_comprado(int(dados["item_id"]), bool(dados.get("comprado", 1)))
    elif acao == "remover":
        lista.remover(int(dados["item_id"]))
    elif acao == "limpar":
        lista.limpar_comprados()
    elif acao == "apagar_tudo":
        lista.limpar_tudo()
    # NAO estima preco aqui (sob demanda no botao "Comparar precos" ->
    # /painel/compras/precos). Adicionar item fica leve, sem buscar preco.
    itens = lista.listar(incluir_comprados=False)
    resumo = lista.resumo()
    return JSONResponse({
        "itens": [{"id": i["id"], "descricao": i["descricao"], "comprado": i["comprado"],
                   "quem": i["quem"],
                   "preco": brl(i["preco_estimado_centavos"]) if i["preco_estimado_centavos"] else None}
                  for i in itens],
        "pendentes": resumo["pendentes"], "comprados": resumo["comprados"],
        "tem_pendentes": resumo["pendentes"] > 0,
    })


@router.post("/painel/compras/add")
def compras_add(request: Request, descricao: str = Form(...)):
    conta, lista = _lista_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    # permite adicionar varios de uma vez: "arroz, cafe, leite" -> 3 itens
    import re as _re
    partes = [p.strip() for p in _re.split(r"[,;\n]+", descricao) if p.strip()]
    if len(partes) > 1:
        lista.adicionar_varios(partes)
    elif partes:
        lista.adicionar(partes[0])
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


@router.post("/painel/compras/avisar")
def compras_avisar(request: Request):
    """Pessoa (qualquer uma menos o dono) avisa que terminou a lista.
    Manda Telegram pro dono. Nao trava a lista."""
    conta, lista = _lista_logada(request)
    if conta is None:
        return JSONResponse({"erro": "nao logado"}, status_code=401)
    pool = get_pool()
    with pool.connection() as c:
        m = c.execute(
            "select nome, papel from membros where conta_id=%s and id = "
            "(select id from membros where conta_id=%s order by id limit 1)",
            (conta[0], conta[0])).fetchone()
    quem_nome = (m[0] if m else None) or conta[2] or "Alguem"
    papel = m[1] if m else "membro"
    if papel == "dono":
        return JSONResponse({"ok": False, "msg": "Voce e' o dono - o aviso e' pra voce mesmo."})
    n = lista.resumo().get("pendentes", 0)
    from finance.notificar import avisar_dono_lista_fechada
    ok = avisar_dono_lista_fechada(pool, conta[0], quem_nome, n_itens=n)
    if ok:
        return JSONResponse({"ok": True, "msg": "Pronto! Avisei o responsavel. ✅"})
    return JSONResponse({"ok": True, "msg": "Lista marcada como pronta! (o aviso por Telegram sai quando o responsavel conectar)"})


@router.post("/membros/adicionar")
def membros_adicionar(request: Request, nome: str = Form(...), whatsapp: str = Form(""),
                      papel: str = Form("membro")):
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    ativos, inclusos, pode_extra = _limite_membros(conta)
    if not pode_extra and ativos >= inclusos:
        request.session["erro"] = "Limite de pessoas do plano atingido."
        return RedirectResponse("/painel", status_code=303)
    papel = papel if papel in ("membro", "restrito") else "membro"
    nome = nome.strip()

    zap = _normalizar_zap(whatsapp) if whatsapp.strip() else None
    if zap:
        with pool.connection() as c:
            ja = c.execute("select 1 from membros where whatsapp_id=%s", (zap,)).fetchone()
        if ja:
            request.session["erro"] = "Esse WhatsApp ja esta cadastrado."
            return RedirectResponse("/painel", status_code=303)

    # cria UM membro com codigo de convite (e whatsapp, se informado)
    codigo = ct.criar_convite(pool, conta[0], nome=nome, papel=papel, whatsapp_id=zap)
    request.session["aviso"] = (
        f"{nome} adicionado(a)! Código de convite do Telegram: {codigo} — "
        f"peça pra essa pessoa abrir o bot ClawIAOpen e enviar esse código."
        + (" (ou já pode usar pelo WhatsApp cadastrado)" if zap else ""))
    return RedirectResponse("/painel", status_code=303)


@router.post("/membros/convite")
def membros_convite(request: Request, membro_id: int = Form(...)):
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    codigo = ct.gerar_convite_para(get_pool(), membro_id, conta[0])
    if codigo:
        request.session["aviso"] = (
            f"Código de convite gerado: {codigo} — peça pra pessoa abrir o bot "
            f"ClawIAOpen no Telegram e enviar esse código.")
    else:
        request.session["erro"] = "Não foi possível gerar o convite para essa pessoa."
    return RedirectResponse("/painel", status_code=303)


@router.post("/membros/reativar")
def membros_reativar(request: Request, membro_id: int = Form(...)):
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    if ct.reativar_membro(get_pool(), membro_id, conta[0]):
        request.session["aviso"] = "Pessoa reativada."
    else:
        request.session["erro"] = "Não foi possível reativar."
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
