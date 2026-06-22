"""Portal do OpenClaw: cadastro, login e painel (Bloco A+B+C).

Vive dentro do openclaw-web. Regra sagrada: toda pagina logada enxerga
APENAS a conta da sessao (isolamento multi-tenant na camada web).
Senhas: hash scrypt (stdlib) com sal aleatorio - nunca em texto puro.
"""
import hashlib
import logging
import os
import secrets

from fastapi import APIRouter, Request, Form, BackgroundTasks
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse
from jinja2 import Environment, DictLoader, select_autoescape

from datetime import date

log = logging.getLogger("zaq.portal")

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
            "select id, tipo, nome, email, plano, status, vencimento, cidade, "
            "eh_fornecedor, fornecedor_slug, eh_assinante_cesta from contas where id = %s",
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
<title>{{ titulo }} - Zaq</title>
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
.lk-tg,.lk-copy,.lk-wpp{padding:.35rem .6rem;border-radius:6px;font-size:.75rem;border:1px solid #243049;text-decoration:none;display:inline-block;line-height:1.2}
.lk-tg{background:#2AABEE;color:#fff;border-color:#2AABEE}
.lk-copy{background:#1a2233;color:#e7ecf3;cursor:pointer;border-color:#2a2a2b}
.lk-wpp{background:#25D366;color:#fff;border-color:#25D366}
.conv-invite{margin:.3rem 0;font-size:.82rem}
.conv-links{display:flex;gap:4px;flex-wrap:wrap;margin-top:.3rem}
.abas{display:flex;gap:.4rem;margin:.6rem 0 .9rem}
.aba{background:#2a2a2b;color:#a8a8a3;border:none;padding:.45rem 1rem;border-radius:8px;font-size:14px;cursor:pointer;transition:background .2s, color .2s}
.aba.on{background:#1d9e75;color:#fff;font-weight:500}
.dica-toque{font-size:.78rem;color:#7a7a78;margin:0 0 .6rem}
.dephead{font-size:.72rem;font-weight:600;color:#cfcfca;margin:.6rem 0 .3rem .2rem}
.litem{display:flex;align-items:center;gap:.6rem;padding:.7rem .5rem .7rem .6rem;margin-bottom:.4rem;background:#1a1a1c;border-radius:10px;transition:transform .15s,background .2s,opacity .35s,max-height .35s;position:relative;overflow:hidden}
.litem .toque{display:flex;align-items:center;gap:.7rem;flex:1;cursor:pointer}
.litem:active{transform:scale(.985)}
.litem .bol{width:26px;height:26px;border:2px solid #5dcaa5;border-radius:50%;flex-shrink:0;display:flex;align-items:center;justify-content:center;color:#fff;font-size:15px;transition:background .2s,border-color .2s}
.litem .nome{flex:1;color:#ececec;font-size:15px;transition:color .2s;word-break:break-word}
.litem .rem{width:34px;height:34px;flex-shrink:0;display:flex;align-items:center;justify-content:center;color:#7a4a4a;font-size:1.1rem;border-radius:8px;cursor:pointer;border:0;background:transparent}
.litem .rem:hover{background:rgba(180,60,60,.15);color:#c66}
.litem.done{background:#15241d}
.litem.done .bol{background:#1d9e75;border-color:#1d9e75}
.litem.done .nome{color:#7a8a82;text-decoration:line-through}
.litem.flash-v::after{content:'';position:absolute;inset:0;background:#1d9e75;opacity:.35;animation:cfade .5s ease-out forwards;pointer-events:none}
.litem.flash-r::after{content:'';position:absolute;inset:0;background:#a33;opacity:.3;animation:cfade .4s ease-out forwards;pointer-events:none}
@keyframes cfade{to{opacity:0}}
.litem.saindo{opacity:0;transform:translateX(40px);max-height:0;padding-top:0;padding-bottom:0;margin-bottom:0}
.cart-head{margin:1rem 0 .3rem;font-size:.88rem;color:#5dcaa5;cursor:pointer;user-select:none;font-weight:500}
.btn-finalizar{width:100%;margin-top:.8rem;background:#1d9e75;color:#fff;border:none;padding:.7rem;border-radius:10px;font-size:15px;font-weight:500;cursor:pointer;transition:background .2s}
.btn-finalizar:hover{background:#22b485}
.btn-finalizar:active{transform:scale(.99)}
.hist-dia{border-bottom:1px solid #2a2a2b}
.hist-head{display:flex;align-items:center;justify-content:space-between;padding:.6rem .2rem;cursor:pointer;color:#ececec;font-size:14px;user-select:none}
.hist-head:hover{background:#1a1a1c;border-radius:8px}
.hist-itens{padding:.2rem .2rem .6rem 1rem;font-size:13px;color:#a8a8a3;line-height:1.7}
</style></head><body>
<div class="topo"><span class="logo">Zaq</span><span>
{% if logado %}
  {% if conta and conta[10] %}
    <!-- Assinante de cesta: menu com cestas + plano -->
    <a href="/painel/assinaturas">🧺 Minhas cestas</a><a href="/painel/meu-plano">Meu plano</a><a href="/sair">Sair</a>
  {% else %}
    <!-- Cliente do app / Fornecedor: menu completo -->
    <a href="/painel">Painel</a><a href="/painel/financeiro">Financeiro</a><a href="/painel/compras">Compras</a>{% if tem_cesta %}<a href="/painel/assinaturas">🧺 Minhas cestas</a>{% endif %}{% if conta and conta[8] %}<a href="/painel/fornecedor">👨‍🌾 Fornecedor</a>{% endif %}<a href="/sair">Sair</a>
  {% endif %}
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
<label>Seu CEP <span class="mut">(pra comparar preços perto de você)</span></label>
<input name="cep" required placeholder="64000-000" maxlength="9" inputmode="numeric">
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
{% if conta[5] == 'trial' %}
<form method="post" action="/assinar" style="display:inline">
<button style="background:#1d9e75;color:#fff;padding:.5rem 1rem;border:0;border-radius:6px;cursor:pointer;font-size:.95rem">💳 Assinar plano</button>
</form>
{% endif %}
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
<div class="conv-invite">
  <div>🔑 <code>{{ m[5] }}</code></div>
  <div class="conv-links">
    <a class="lk-tg" href="https://t.me/clawaladdin_bot?text={{ m[5]|urlencode }}" target="_blank" rel="noopener">📨 Telegram</a>
    <button type="button" class="lk-copy" onclick="copiarConvite(this, 'https://t.me/clawaladdin_bot?text={{ m[5]|urlencode }}')">🔗 Copiar</button>
    {% if whatsapp_bot_num %}
    <a class="lk-wpp" href="https://wa.me/{{ whatsapp_bot_num }}?text={{ m[5]|urlencode }}" target="_blank" rel="noopener">🟢 WhatsApp</a>
    <button type="button" class="lk-copy" onclick="copiarConvite(this, 'https://wa.me/{{ whatsapp_bot_num }}?text={{ m[5]|urlencode }}')">🔗 Copiar</button>
    {% endif %}
  </div>
</div>
{% else %}
{% if m[6] %}<div class="conv mut">✅ Telegram conectado</div>{% endif %}
{% if m[2] %}<div class="conv mut">✅ WhatsApp conectado</div>{% endif %}
{% if not m[2] and not m[6] %}<span class="mut">sem contato</span>{% endif %}
{% endif %}
</div>
<div class="membro-acoes">
<form method="post" action="/membros/reconectar"><input type="hidden" name="membro_id" value="{{ m[4] }}">
<button class="btn-conv">↻ Reconectar</button></form>
{% if m[1] != 'dono' %}
{% if m[3] %}
<form method="post" action="/membros/desativar"><input type="hidden" name="membro_id" value="{{ m[4] }}">
<button class="btn-off">desativar</button></form>
{% else %}
<form method="post" action="/membros/reativar"><input type="hidden" name="membro_id" value="{{ m[4] }}">
<button class="btn-on">reativar</button></form>
{% endif %}
{% else %}<span class="mut" style="font-size:.85rem">titular</span>{% endif %}
</div>
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

_FORNECEDOR = """{% extends "base" %}{% block conteudo %}
<div class="card larga"><h2>👨‍🌾 Fornecedor</h2>
{% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
{% if aviso %}<div class="ok">{{ aviso }}</div>{% endif %}

<!-- MENU DE CARDS (visível ao entrar) -->
<div id="forn-menu" class="forn-cards">
  <button type="button" class="forn-card" onclick="fornAbrir('dados')">
    <span class="fc-ic">🏢</span>
    <span class="fc-tit">Meus dados</span>
    <span class="fc-leg">Os dados da sua empresa: razão social, CNPJ e endereço.</span>
  </button>
  <button type="button" class="forn-card" onclick="fornAbrir('catalogo')">
    <span class="fc-ic">📦</span>
    <span class="fc-tit">Catálogo</span>
    <span class="fc-leg">Seus produtos e preços — o que os clientes podem pedir de você.</span>
  </button>
  <button type="button" class="forn-card" onclick="fornAbrir('compras')">
    <span class="fc-ic">🛒</span>
    <span class="fc-tit">Compras</span>
    <span class="fc-leg">O que você compra no CEASA — notas e entradas de estoque.</span>
  </button>
  <button type="button" class="forn-card" onclick="fornAbrir('cestas')">
    <span class="fc-ic">🧺</span>
    <span class="fc-tit">Cestas</span>
    <span class="fc-leg">Os tamanhos de cesta que seus clientes podem assinar.</span>
  </button>
  <button type="button" class="forn-card" onclick="fornAbrir('pedidos')">
    <span class="fc-ic">📋</span>
    <span class="fc-tit">Pedidos</span>
    <span class="fc-leg">Os pedidos dos seus clientes, prontos pra separar e entregar.</span>
  </button>
  <button type="button" class="forn-card" onclick="fornAbrir('financeiro')">
    <span class="fc-ic">💰</span>
    <span class="fc-tit">Financeiro</span>
    <span class="fc-leg">Quanto você ganhou, os repasses e as comissões.</span>
  </button>
</div>

<!-- SEÇÃO: Meus dados -->
<div id="forn-dados" class="forn-secao" style="display:none">
  <button type="button" class="forn-voltar" onclick="fornVoltar()">← voltar</button>
  <h3>Meus dados</h3>
  <form method="post" action="/painel/fornecedor/dados">
    <label>Razão social</label>
    <input name="razao_social" value="{{ fiscal.razao_social or '' }}" placeholder="ex: Hortifruti do Zé LTDA" maxlength="200">
    <label>CNPJ</label>
    <input name="cnpj" value="{{ fiscal.cnpj or '' }}" placeholder="14224053000103" maxlength="14">
    <label>Endereço</label>
    <input name="endereco" value="{{ fiscal.endereco or '' }}" placeholder="ex: Rua A, 123, Teresina - PI, 64000-000" maxlength="200">
    <button>Salvar dados</button>
  </form>
  <p class="mut">Dados fiscais completos (inscrição estadual, regime, certificado) serão pedidos quando ativarmos a emissão de nota.</p>

  <hr style="margin:1.5rem 0;border:none;border-top:1px solid #2a2a2b">
  <h4 style="margin-top:0">⚙️ Configuração de margem</h4>
  <p class="mut">Define a folga de custo para que você tenha margem pra perda, comissão e lucro. A cesta sempre respeita esse limite.</p>
  <form method="post" action="/painel/fornecedor/margem-alvo" style="display:flex;gap:.5rem;align-items:flex-end">
    <div style="flex:1">
      <label>Margem alvo (%)</label>
      <input name="margem_alvo" type="number" min="0" max="100" value="{{ margem_alvo }}" placeholder="60" style="width:100%">
      <small class="mut" style="display:block;margin-top:.3rem">Até {{ margem_alvo }}% do preço da cesta pode ser custo. Ex: 60% = você garante 40% de folga.</small>
    </div>
    <button style="background:#1d9e75;color:#fff;padding:.5rem 1rem;border:0;border-radius:6px;cursor:pointer;font-weight:500">Salvar</button>
  </form>
</div>

<!-- SEÇÃO: Catálogo -->
<div id="forn-catalogo" class="forn-secao" style="display:none">
  <button type="button" class="forn-voltar" onclick="fornVoltar()">← voltar</button>
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem">
    <h3 style="margin:0">Catálogo</h3>
    <div style="display:flex;gap:.5rem">
      <button type="button" onclick="fornShowNovoProduct()" style="padding:.5rem 1rem;background:#1d9e75;color:#fff;border:0;border-radius:6px;cursor:pointer;font-weight:500;font-size:.9rem">+ novo produto</button>
      <button type="button" onclick="fornShowImportarPlanilha()" style="padding:.5rem 1rem;background:transparent;border:1px solid #1d9e75;color:#1d9e75;border-radius:6px;cursor:pointer;font-weight:500;font-size:.9rem">📊 importar planilha</button>
    </div>
  </div>

  <!-- LISTA DE PRODUTOS EM CARDS -->
  <div id="forn-cat-lista" style="display:flex;flex-direction:column;gap:.8rem">
    {% if produtos %}
      {% for p in produtos %}
      <div style="background:#1c1c1f;border:1px solid #2a2a2b;border-radius:8px;padding:1rem">
        <div style="display:flex;justify-content:space-between;align-items:flex-start;margin-bottom:.6rem">
          <div>
            <strong style="font-size:1rem">{{ p.nome }}</strong> · <span class="mut">{{ p.unidade }}</span>
            <div class="mut" style="font-size:.85rem;margin-top:.3rem">
              saldo: <strong>{{ p.saldo }} {{ p.unidade }}</strong> · custo médio <strong>R$ {{ "%.2f" | format(p.custo_medio_centavos / 100) }}</strong> · vende <strong>R$ {{ "%.2f" | format(p.preco_venda_centavos / 100) }}</strong> · margem {% if p.margem_pct %}<strong>{{ p.margem_pct }}%</strong>{% else %}-{% endif %}
              {% if p.abaixo_minimo %}<br><span style="color:#ff6b6b">⚠ abaixo do mínimo</span>{% endif %}
            </div>
          </div>
        </div>
        <div style="display:flex;gap:.5rem;flex-wrap:wrap">
          <button type="button" onclick="fornShowEntrada({{ p.id }})" style="background:#1d9e75;color:#fff;border:0;border-radius:4px;padding:.4rem .8rem;cursor:pointer;font-size:.85rem;font-weight:500">dar entrada</button>
          <button type="button" onclick="fornShowPerda({{ p.id }})" style="background:#8a3636;color:#fff;border:0;border-radius:4px;padding:.4rem .8rem;cursor:pointer;font-size:.85rem;font-weight:500">registrar perda</button>
          <button type="button" onclick="fornEditProduct({{ p.id }})" style="background:transparent;border:1px solid #5dcaa5;color:#5dcaa5;border-radius:4px;padding:.4rem .8rem;cursor:pointer;font-size:.85rem">editar</button>
        </div>
      </div>
      {% endfor %}
    {% else %}
    <p class="mut">Nenhum produto ainda. Clique em "+ novo produto" pra começar.</p>
    {% endif %}
  </div>

  <!-- MODAL: Novo/Editar Produto -->
  <div id="forn-novo-prod" style="display:none;margin-top:2rem;padding:1.2rem;background:#1c1c1f;border:1px solid #2a2a2b;border-radius:8px">
    <h4 style="margin-top:0">Novo produto</h4>
    <form method="post" action="/painel/fornecedor/catalogo/produto">
      <label>Nome</label><input name="nome" required placeholder="ex: Tomate" style="width:100%">
      <label>Unidade</label>
      <select name="unidade" required style="width:100%">
        <option value="kg">kg</option>
        <option value="unidade">unidade</option>
        <option value="duzia">dúzia</option>
        <option value="maco">maço</option>
        <option value="bandeja">bandeja</option>
        <option value="litro">litro</option>
        <option value="pacote">pacote</option>
      </select>
      <label>Categoria (opcional)</label><input name="categoria" placeholder="ex: fruta" style="width:100%">
      <label>Preço de venda (R$)</label><input name="preco_venda" type="number" step="0.01" placeholder="ex: 6.90" style="width:100%">
      <label>Estoque mínimo para alerta</label><input name="estoque_minimo" type="number" step="0.1" placeholder="ex: 5" style="width:100%">
      <button style="background:#1d9e75;color:#fff;padding:.5rem 1rem;border:0;border-radius:6px;cursor:pointer;margin-top:.8rem;width:100%;font-weight:500">Criar produto</button>
    </form>
    <button type="button" onclick="fornHideNovoProduct()" style="background:transparent;border:none;color:#5dcaa5;cursor:pointer;margin-top:.6rem;font-size:.9rem">Cancelar</button>
  </div>

  <!-- MODAL: Dar Entrada -->
  <div id="forn-entrada" style="display:none;margin-top:2rem;padding:1.2rem;background:#1c1c1f;border:1px solid #2a2a2b;border-radius:8px">
    <h4 style="margin-top:0">ao clicar "dar entrada" abre isto:</h4>
    <form method="post" action="/painel/fornecedor/catalogo/entrada">
      <input type="hidden" id="forn-prod-id" name="produto_id">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:.8rem;margin-bottom:.8rem">
        <div>
          <label>Quantidade:</label><input name="quantidade" type="number" step="0.1" required placeholder="ex: 50 kg" style="width:100%">
        </div>
        <div>
          <label>Custo: R$ <input name="custo_unit" type="number" step="0.01" required placeholder="4,00" style="width:100%;display:inline;width:auto">/kg</label>
        </div>
      </div>
      <label>Origem (de quem comprou):</label>
      <div style="display:flex;gap:.4rem">
        <select name="origem_id" id="forn-origem-sel" required style="flex:1">
          <option value="">Selecione uma origem</option>
          {% for o in origens %}<option value="{{ o.id }}">{{ o.nome }}</option>{% endfor %}
        </select>
        <button type="button" onclick="fornShowNovaOrigem()" style="background:transparent;border:none;color:#5dcaa5;cursor:pointer;font-size:.9rem;padding:0;text-decoration:underline">+ nova</button>
      </div>
      <button style="background:#1d9e75;color:#fff;padding:.6rem 1rem;border:0;border-radius:6px;cursor:pointer;margin-top:1rem;width:100%;font-weight:500;font-size:.95rem">Confirmar entrada</button>
    </form>
    <button type="button" onclick="fornHideEntrada()" style="background:transparent;border:none;color:#5dcaa5;cursor:pointer;margin-top:.6rem;font-size:.9rem">Cancelar</button>
  </div>

  <!-- MODAL: Nova Origem -->
  <div id="forn-nova-origem" style="display:none;margin-top:1rem;padding:1rem;background:#2a2a2b;border-radius:6px">
    <h4 style="margin-top:0;margin-bottom:.6rem">Nova origem</h4>
    <form method="post" action="/painel/fornecedor/catalogo/origem">
      <label>Nome</label><input name="nome" required placeholder="ex: CEASA, Sítio do João" style="width:100%">
      <label>Contato (opcional)</label><input name="contato" placeholder="tel, email, whatsapp" style="width:100%">
      <button style="background:#1d9e75;color:#fff;padding:.4rem .8rem;border:0;border-radius:4px;cursor:pointer;font-size:.85rem;margin-top:.5rem;font-weight:500">Criar origem</button>
    </form>
    <button type="button" onclick="fornHideNovaOrigem()" style="background:transparent;border:none;color:#5dcaa5;cursor:pointer;font-size:.85rem;margin-top:.5rem">Cancelar</button>
  </div>

  <!-- MODAL: Importar Planilha -->
  <div id="forn-importar-planilha" style="display:none;margin-top:2rem;padding:1.2rem;background:#1c1c1f;border:1px solid #2a2a2b;border-radius:8px">
    <h4 style="margin-top:0">Importar planilha de produtos</h4>
    <p class="mut" style="font-size:.9rem;margin-bottom:.8rem">Baixe o <a href="/static/modelo_planilha_produtos.csv" style="color:#5dcaa5">modelo de planilha</a>, preencha com seus produtos e suba o arquivo CSV.</p>
    <form id="forn-form-import" style="display:flex;flex-direction:column;gap:.8rem">
      <input type="file" id="forn-import-file" name="arquivo" accept=".csv" required style="padding:.5rem;border:1px solid #2a2a2b;border-radius:4px;background:#0a0a0a;color:#fff">
      <button type="button" onclick="fornProcessarImportacao()" style="background:#1d9e75;color:#fff;padding:.6rem 1rem;border:0;border-radius:6px;cursor:pointer;font-weight:500;width:100%">Ler planilha</button>
    </form>
    <button type="button" onclick="fornHideImportarPlanilha()" style="background:transparent;border:none;color:#5dcaa5;cursor:pointer;margin-top:.6rem;font-size:.9rem">Cancelar</button>
  </div>

  <!-- MODAL: Preview de Importação -->
  <div id="forn-import-preview" style="display:none;margin-top:2rem;padding:1.2rem;background:#1c1c1f;border:1px solid #2a2a2b;border-radius:8px">
    <h4 style="margin-top:0">Preview dos produtos</h4>
    <div id="forn-import-info" style="margin-bottom:1rem;padding:.8rem;background:#2a2a2b;border-radius:4px;font-size:.9rem">
      <strong id="forn-import-total">0</strong> produtos encontrados ·
      <span style="color:#1d9e75"><strong id="forn-import-validos">0</strong> OK</span> ·
      <span style="color:#ff9800"><strong id="forn-import-problema">0</strong> com ⚠</span>
    </div>
    <table id="forn-import-tabela" style="width:100%;border-collapse:collapse;font-size:.85rem;margin-bottom:1rem">
      <thead><tr style="border-bottom:1px solid #2a2a2b">
        <th style="text-align:left;padding:.4rem">Produto</th>
        <th style="text-align:center;padding:.4rem">Un</th>
        <th style="text-align:right;padding:.4rem">Preço</th>
        <th style="text-align:left;padding:.4rem">Categoria</th>
        <th style="text-align:center;padding:.4rem">Status</th>
      </tr></thead>
      <tbody id="forn-import-tbody"></tbody>
    </table>
    <button type="button" onclick="fornConfirmarImportacao()" style="background:#1d9e75;color:#fff;padding:.6rem 1rem;border:0;border-radius:6px;cursor:pointer;font-weight:500;width:100%;margin-bottom:.5rem">Importar produtos</button>
    <button type="button" onclick="fornHideImportarPlanilha()" style="background:transparent;border:none;color:#5dcaa5;cursor:pointer;font-size:.9rem">Cancelar</button>
  </div>

  <!-- MODAL: Registrar Perda -->
  <div id="forn-perda" style="display:none;margin-top:2rem;padding:1.2rem;background:#1c1c1f;border:1px solid #2a2a2b;border-radius:8px">
    <h4 style="margin-top:0">Registrar perda</h4>
    <form method="post" action="/painel/fornecedor/catalogo/perda">
      <input type="hidden" id="forn-perda-prod-id" name="produto_id">
      <label>Quantidade perdida</label><input name="quantidade" type="number" step="0.1" required placeholder="ex: 2" style="width:100%">
      <label>Motivo</label><input name="motivo" placeholder="ex: estragou, sobrou do dia" style="width:100%">
      <button style="background:#8a3636;color:#fff;padding:.6rem 1rem;border:0;border-radius:6px;cursor:pointer;margin-top:1rem;width:100%;font-weight:500;font-size:.95rem">Registrar perda</button>
    </form>
    <button type="button" onclick="fornHidePerda()" style="background:transparent;border:none;color:#5dcaa5;cursor:pointer;margin-top:.6rem;font-size:.9rem">Cancelar</button>
  </div>

  <!-- MODAL: Editar Produto -->
  <div id="forn-editar-produto" style="display:none;margin-top:1rem;padding:1rem;background:#2a2a2b;border-radius:6px">
    <h4 style="margin-top:0;margin-bottom:.8rem">Editar produto</h4>
    <form method="post" action="/painel/fornecedor/catalogo/editar">
      <input type="hidden" name="produto_id" id="forn-edit-id">
      <label>Nome</label>
      <input name="nome" id="forn-edit-nome" required style="width:100%">
      <label>Unidade</label>
      <select name="unidade" id="forn-edit-unidade" style="width:100%">
        <option value="kg">kg</option><option value="duzia">dúzia</option>
        <option value="unidade">unidade</option><option value="maco">maço</option>
        <option value="bandeja">bandeja</option><option value="litro">litro</option>
        <option value="pacote">pacote</option>
      </select>
      <label>Categoria</label>
      <input name="categoria" id="forn-edit-categoria" placeholder="fruta, verdura..." style="width:100%">
      <label>Preço de venda (R$)</label>
      <input name="preco_venda" id="forn-edit-preco" placeholder="6,90" required style="width:100%">
      <label>Estoque mínimo</label>
      <input name="estoque_minimo" id="forn-edit-minimo" type="number" step="0.001" style="width:100%">
      <button style="background:#1d9e75;color:#fff;padding:.5rem 1rem;border:0;border-radius:6px;cursor:pointer;margin-top:.8rem;width:100%;font-weight:500">Salvar alterações</button>
    </form>
    <button type="button" onclick="fornHideEditar()" style="background:transparent;border:none;color:#5dcaa5;cursor:pointer;margin-top:.5rem;font-size:.85rem">Cancelar</button>
  </div>

</div>

<script>
window.PRODUTOS = {
  {% for p in produtos %}
  "{{ p.id }}": {
    nome: {{ p.nome|tojson }},
    unidade: {{ p.unidade|tojson }},
    categoria: {{ (p.categoria or '')|tojson }},
    preco: {{ "%.2f"|format(p.preco_venda_centavos/100) }},
    minimo: {{ p.estoque_minimo }}
  }{% if not loop.last %},{% endif %}
  {% endfor %}
};
</script>

<!-- SEÇÃO: Compras -->
<div id="forn-compras" class="forn-secao" style="display:none">
  <button type="button" class="forn-voltar" onclick="fornVoltar()">← voltar</button>
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem">
    <h3 style="margin:0">Compras</h3>
    <button type="button" onclick="fornShowNovaCompra()" style="padding:.5rem 1rem;background:#1d9e75;color:#fff;border:0;border-radius:6px;cursor:pointer;font-weight:500;font-size:.9rem">+ nova compra</button>
  </div>

  <!-- LISTA DE COMPRAS -->
  <div id="forn-compras-lista" style="display:flex;flex-direction:column;gap:.8rem">
    {% if compras %}
      {% for c in compras %}
      <div style="background:#1c1c1f;border:1px solid #2a2a2b;border-radius:8px;padding:1rem">
        <div style="display:flex;justify-content:space-between;align-items:start">
          <div>
            <strong>#{{ c.id }}</strong> · <span class="mut">{{ c.data_compra }}</span> · <span style="{% if c.status == 'confirmada' %}color:#1d9e75{% else %}color:#ffa500{% endif %}">{{ c.status }}</span>
            <div class="mut" style="font-size:.85rem;margin-top:.3rem">Total: <strong>R$ {{ "%.2f" | format(c.total_centavos / 100) }}</strong> | Origem: {{ c.origem_nome or '-' }} | Fonte: {{ c.fonte }}</div>
          </div>
          <div style="display:flex;gap:.4rem">
            <a href="/painel/fornecedor/compras/{{ c.id }}" style="background:transparent;border:1px solid #5dcaa5;color:#5dcaa5;border-radius:4px;padding:.3rem .6rem;cursor:pointer;font-size:.85rem;text-decoration:none;display:inline-block">revisar</a>
          </div>
        </div>
      </div>
      {% endfor %}
    {% else %}
    <p class="mut">Nenhuma compra ainda. Clique em "+ nova compra" pra começar.</p>
    {% endif %}
  </div>

  <!-- MODAL: Nova Compra -->
  <div id="forn-nova-compra" style="display:none;margin-top:2rem;padding:1.2rem;background:#1c1c1f;border:1px solid #2a2a2b;border-radius:8px">
    <h4 style="margin-top:0">Nova compra</h4>
    <div style="display:flex;gap:.6rem;margin-bottom:1rem">
      <button type="button" onclick="fornShowCompraManual()" style="flex:1;background:#1d9e75;color:#fff;padding:.5rem;border:0;border-radius:6px;cursor:pointer;font-weight:500">Manual (digitar itens)</button>
      <button type="button" onclick="fornShowCompraNota()" style="flex:1;background:transparent;border:1px solid #5dcaa5;color:#5dcaa5;padding:.5rem;border-radius:6px;cursor:pointer;font-weight:500">Com nota (em breve)</button>
    </div>
    <button type="button" onclick="fornHideNovaCompra()" style="background:transparent;border:none;color:#5dcaa5;cursor:pointer;font-size:.85rem">Cancelar</button>
  </div>

  <!-- MODAL: Compra Manual -->
  <div id="forn-compra-manual" style="display:none;margin-top:1rem;padding:1rem;background:#2a2a2b;border-radius:6px">
    <h4 style="margin-top:0;margin-bottom:.8rem">Nova compra manual</h4>
    <form method="post" action="/painel/fornecedor/compras/criar">
      <label>Data da compra</label><input type="date" name="data_compra" style="width:100%">
      <label>De onde comprou?</label>
      <select name="origem_id" id="forn-compra-origem-sel" style="width:100%">
        <option value="">Selecione uma origem</option>
        {% for o in origens %}<option value="{{ o.id }}">{{ o.nome }}</option>{% endfor %}
      </select>
      <button type="button" onclick="fornShowNovaOrigemCompra()" style="background:transparent;border:1px solid #5dcaa5;color:#5dcaa5;padding:.3rem .6rem;cursor:pointer;font-size:.85rem;margin-top:.3rem">+ Nova origem</button>
      <button style="background:#1d9e75;color:#fff;padding:.5rem 1rem;border:0;border-radius:6px;cursor:pointer;margin-top:.8rem;width:100%;font-weight:500">Criar compra</button>
    </form>
    <button type="button" onclick="fornHideCompraManual()" style="background:transparent;border:none;color:#5dcaa5;cursor:pointer;margin-top:.5rem;font-size:.85rem">Cancelar</button>
  </div>

  <!-- MODAL: Nova Origem (Compras) -->
  <div id="forn-nova-origem-compra" style="display:none;margin-top:1rem;padding:1rem;background:#1c1c1f;border-radius:6px">
    <h4 style="margin-top:0;margin-bottom:.6rem">Nova origem</h4>
    <form method="post" action="/painel/fornecedor/compras/origem">
      <label>Nome</label><input name="nome" required placeholder="ex: CEASA, Sítio do João" style="width:100%">
      <label>Contato (opcional)</label><input name="contato" placeholder="tel, email, whatsapp" style="width:100%">
      <button style="background:#1d9e75;color:#fff;padding:.4rem .8rem;border:0;border-radius:4px;cursor:pointer;margin-top:.5rem;font-weight:500">Criar origem</button>
    </form>
    <button type="button" onclick="fornHideNovaOrigemCompra()" style="background:transparent;border:none;color:#5dcaa5;cursor:pointer;font-size:.85rem;margin-top:.5rem">Cancelar</button>
  </div>

</div>

<!-- SEÇÃO: Cestas -->
<div id="forn-cestas" class="forn-secao" style="display:none">
  <button type="button" class="forn-voltar" onclick="fornVoltar()">← voltar</button>
  <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:1rem">
    <h3 style="margin:0">🧺 Tamanhos de cesta</h3>
  </div>
  <p class="mut">Defina os tamanhos que seus clientes podem assinar. O preço é FIXO — o que vai dentro varia conforme a estação e o estoque.</p>

  <!-- lista dos tamanhos -->
  {% if tamanhos %}
  {% for t in tamanhos %}
  <div style="background:#1c1c1f;border:1px solid #2a2a2b;border-radius:8px;padding:1rem;margin-bottom:.6rem">
    <strong>{{ t.nome }}</strong> · <strong>R$ {{ "%.2f"|format(t.preco_centavos/100) }}</strong>
    <div class="mut" style="font-size:.85rem;margin-top:.3rem">
      {{ t.qtd_frutas }} frutas · {{ t.qtd_legumes }} legumes · {{ t.qtd_verduras }} verduras · {{ t.qtd_temperos }} temperos ({{ t.total_porcoes }} porções)
      {% if t.descricao %}<br>{{ t.descricao }}{% endif %}
    </div>
  </div>
  {% endfor %}
  {% else %}
  <p class="mut">Nenhum tamanho ainda. Crie o primeiro (ex: Pequena, Média, Grande).</p>
  {% endif %}

  <!-- form criar tamanho -->
  <div style="background:#2a2a2b;border-radius:6px;padding:1rem;margin-top:1rem">
    <h4 style="margin-top:0">Novo tamanho</h4>
    <form method="post" action="/painel/fornecedor/cestas/criar">
      <label>Nome</label>
      <input name="nome" placeholder="Pequena / Média / Grande" required style="width:100%">
      <label>Preço fixo (R$)</label>
      <input name="preco" placeholder="90,00" required style="width:100%">
      <label>Porções de frutas</label>
      <input name="qtd_frutas" type="number" value="0" style="width:100%">
      <label>Porções de legumes</label>
      <input name="qtd_legumes" type="number" value="0" style="width:100%">
      <label>Porções de verduras</label>
      <input name="qtd_verduras" type="number" value="0" style="width:100%">
      <label>Porções de temperos</label>
      <input name="qtd_temperos" type="number" value="0" style="width:100%">
      <label>Descrição (opcional)</label>
      <input name="descricao" placeholder="ideal pra 2 pessoas por semana" style="width:100%">
      <button style="background:#1d9e75;color:#fff;padding:.5rem 1rem;border:0;border-radius:6px;cursor:pointer;margin-top:.8rem;width:100%;font-weight:500">Criar tamanho</button>
    </form>
  </div>
</div>

</div>

<!-- SEÇÃO: Pedidos -->
<div id="forn-pedidos" class="forn-secao" style="display:none">
  <button type="button" class="forn-voltar" onclick="fornVoltar()">← voltar</button>
  <h3>Pedidos</h3>
  <p class="mut">Em breve: os pedidos do dia, organizados por bairro, com a lista de separação.</p>
</div>

<!-- SEÇÃO: Financeiro -->
<div id="forn-financeiro" class="forn-secao" style="display:none">
  <button type="button" class="forn-voltar" onclick="fornVoltar()">← voltar</button>
  <h3>Financeiro</h3>
  <p class="mut">Em breve: seus ganhos, repasses e comissões.</p>
</div>

</div>

<style>
.forn-cards{display:grid; grid-template-columns:repeat(auto-fit,minmax(160px,1fr)); gap:.7rem; margin-top:1rem}
.forn-card{display:flex; flex-direction:column; align-items:flex-start; text-align:left;
  background:#1c1c1f; border:1px solid #2a2a2b; border-radius:12px; padding:1rem; cursor:pointer; transition:border-color .15s; color:inherit; font:inherit}
.forn-card:hover{border-color:#1d9e75}
.fc-ic{font-size:24px; margin-bottom:.4rem; line-height:1}
.fc-tit{font-size:.95rem; font-weight:500; color:#f0f0ee; margin-bottom:.2rem}
.fc-leg{font-size:.8rem; color:#a8a8a3; line-height:1.4}
.forn-secao{margin-top:1rem}
.forn-voltar{background:transparent; border:none; color:#5dcaa5; cursor:pointer; font-size:.9rem; padding:0 0 .8rem 0; text-decoration:none}
.forn-voltar:hover{color:#7ad4b4}
</style>

<script>
function fornAbrir(secao){
  document.getElementById('forn-menu').style.display = 'none';
  ['dados','catalogo','compras','cestas','pedidos','financeiro'].forEach(function(s){
    document.getElementById('forn-'+s).style.display = (s===secao ? 'block' : 'none');
  });
}
function fornVoltar(){
  document.getElementById('forn-menu').style.display = 'grid';
  ['dados','catalogo','compras','cestas','pedidos','financeiro'].forEach(function(s){
    document.getElementById('forn-'+s).style.display = 'none';
  });
}
// CATÁLOGO
function fornShowNovoProduct(){
  document.getElementById('forn-novo-prod').style.display = 'block';
}
function fornHideNovoProduct(){
  document.getElementById('forn-novo-prod').style.display = 'none';
}
function fornEditProduct(prod_id){
  var p = window.PRODUTOS[prod_id];
  if(!p){ return; }
  document.getElementById('forn-edit-id').value = prod_id;
  document.getElementById('forn-edit-nome').value = p.nome;
  document.getElementById('forn-edit-unidade').value = p.unidade;
  document.getElementById('forn-edit-categoria').value = p.categoria;
  document.getElementById('forn-edit-preco').value = String(p.preco).replace('.', ',');
  document.getElementById('forn-edit-minimo').value = p.minimo;
  document.getElementById('forn-editar-produto').style.display = 'block';
  document.getElementById('forn-editar-produto').scrollIntoView({behavior:'smooth'});
}
function fornHideEditar(){
  document.getElementById('forn-editar-produto').style.display = 'none';
}
function fornShowEntrada(prod_id){
  document.getElementById('forn-prod-id').value = prod_id;
  document.getElementById('forn-entrada').style.display = 'block';
}
function fornHideEntrada(){
  document.getElementById('forn-entrada').style.display = 'none';
}
function fornShowPerda(prod_id){
  document.getElementById('forn-perda-prod-id').value = prod_id;
  document.getElementById('forn-perda').style.display = 'block';
}
function fornHidePerda(){
  document.getElementById('forn-perda').style.display = 'none';
}
function fornShowNovaOrigem(){
  document.getElementById('forn-nova-origem').style.display = 'block';
}
function fornHideNovaOrigem(){
  document.getElementById('forn-nova-origem').style.display = 'none';
}
// COMPRAS
function fornShowNovaCompra(){
  document.getElementById('forn-nova-compra').style.display = 'block';
}
function fornHideNovaCompra(){
  document.getElementById('forn-nova-compra').style.display = 'none';
}
function fornShowCompraManual(){
  document.getElementById('forn-compra-manual').style.display = 'block';
  document.getElementById('forn-nova-compra').style.display = 'none';
}
function fornHideCompraManual(){
  document.getElementById('forn-compra-manual').style.display = 'none';
  document.getElementById('forn-nova-compra').style.display = 'block';
}
function fornShowCompraNota(){
  alert('Leitura de nota — em breve. Por enquanto use compra manual.');
}
function fornShowNovaOrigemCompra(){
  document.getElementById('forn-nova-origem-compra').style.display = 'block';
}
function fornHideNovaOrigemCompra(){
  document.getElementById('forn-nova-origem-compra').style.display = 'none';
}
// IMPORTAÇÃO
function fornShowImportarPlanilha(){
  document.getElementById('forn-importar-planilha').style.display = 'block';
  document.getElementById('forn-import-preview').style.display = 'none';
}
function fornHideImportarPlanilha(){
  document.getElementById('forn-importar-planilha').style.display = 'none';
  document.getElementById('forn-import-preview').style.display = 'none';
  document.getElementById('forn-import-file').value = '';
}
function fornProcessarImportacao(){
  var file = document.getElementById('forn-import-file').files[0];
  if (!file) { alert('Selecione um arquivo'); return; }
  var reader = new FileReader();
  reader.onload = function(e) {
    var formData = new FormData();
    formData.append('arquivo', file);
    fetch('/painel/fornecedor/catalogo/ler-planilha', {method: 'POST', body: formData})
      .then(r => r.json())
      .then(data => {
        if (!data.ok) { alert('Erro: ' + data.erro); return; }
        window.IMPORT_DATA = data;
        document.getElementById('forn-importar-planilha').style.display = 'none';
        document.getElementById('forn-import-preview').style.display = 'block';
        document.getElementById('forn-import-total').textContent = data.total;
        document.getElementById('forn-import-validos').textContent = data.validos;
        document.getElementById('forn-import-problema').textContent = data.com_problema;
        var tbody = document.getElementById('forn-import-tbody');
        tbody.innerHTML = '';
        data.itens.forEach(function(it) {
          var tr = document.createElement('tr');
          tr.style.borderBottom = '1px solid #2a2a2b';
          if (it.problemas.length > 0) tr.style.backgroundColor = '#3a2a1a';
          var tds = [
            it.nome || '(sem nome)',
            it.unidade,
            (it.preco_venda_centavos / 100).toFixed(2),
            it.categoria || '-',
            it.problemas.length > 0 ? '⚠' : '✓'
          ];
          tds.forEach(function(txt) {
            var td = document.createElement('td');
            td.textContent = txt;
            td.style.padding = '.4rem';
            td.style.textAlign = txt === it.unidade ? 'center' : 'left';
            tr.appendChild(td);
          });
          tbody.appendChild(tr);
        });
      }).catch(e => alert('Erro ao ler: ' + e.message));
  };
  reader.readAsArrayBuffer(file);
}
function fornConfirmarImportacao(){
  if (!window.IMPORT_DATA) { alert('Nenhum preview'); return; }
  fetch('/painel/fornecedor/catalogo/importar-planilha', {
    method: 'POST',
    headers: {'Content-Type': 'application/json'},
    body: JSON.stringify({itens: window.IMPORT_DATA.itens})
  })
  .then(r => r.json())
  .then(data => {
    alert('✓ Importados: ' + data.criados + ' | Pulados: ' + data.pulados);
    location.reload();
  }).catch(e => alert('Erro: ' + e.message));
}
</script>
{% endblock %}"""

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
<div class="cat-linha" onclick="abrirCat(this)" data-cat="{{ cat }}" data-tipo="despesa" style="cursor:pointer">
  <div style="display:flex; justify-content:space-between; font-size:.9rem; margin:.4rem 0 .2rem">
    <span><span class="seta">▸</span> {{ cat }}</span><b>{{ brl(val) }}</b>
  </div>
  <div class="barra"><div class="barra-fill" style="width:{{ (val*100//maior_cat) if maior_cat else 0 }}%"></div></div>
</div>
<div class="cat-lancamentos" style="display:none; padding:.2rem 0 .6rem 1.2rem"></div>
{% endfor %}{% else %}<p class="mut">Sem despesas neste mês.</p>{% endif %}

<h1 style="font-size:1.05rem; margin-top:1.6rem">Receitas por categoria</h1>
{% if receitas_cat %}{% for cat,val in receitas_cat %}
<div class="cat-linha" onclick="abrirCat(this)" data-cat="{{ cat }}" data-tipo="receita" style="cursor:pointer">
  <div style="display:flex; justify-content:space-between; font-size:.9rem; margin:.4rem 0 .2rem">
    <span><span class="seta">▸</span> {{ cat }}</span><b>{{ brl(val) }}</b>
  </div>
  <div class="barra"><div class="barra-fill" style="width:{{ (val*100//maior_rec) if maior_rec else 0 }}%; background:#5dcaa5"></div></div>
</div>
<div class="cat-lancamentos" style="display:none; padding:.2rem 0 .6rem 1.2rem"></div>
{% endfor %}{% else %}<p class="mut">Sem receitas neste mês.</p>{% endif %}

<h1 style="font-size:1.05rem; margin-top:1.6rem">Lançamentos</h1>
<form method="get" action="/painel/financeiro" style="margin:.5rem 0 1rem">
<input type="search" name="q" value="{{ q_search or '' }}" placeholder="🔎 Buscar lançamento..."
       style="width:100%;padding:.6rem .8rem;border:1px solid #2a3a33;border-radius:8px;background:#161617;color:#ececec;font-size:.95rem">
{% if q_search %}<input type="hidden" name="mes" value="{{ mes_sel }}">{% endif %}
</form>
{% if q_search %}
<p style="margin:.5rem 0; color:#8a8a85">{{ n_resultados }} {{ 'resultado' if n_resultados == 1 else 'resultados' }} para "{{ q_search }}"
{% if n_resultados > 0 %}<a href="/painel/financeiro?mes={{ mes_sel }}" style="color:#5dcaa5;text-decoration:none"> — limpar busca</a>{% endif %}
</p>
{% endif %}
<div class="abas" {% if q_search %}style="display:none"{% endif %}>
<button type="button" class="aba ativa" data-f="todos" onclick="filtrarTipo(this)">Todos</button>
<button type="button" class="aba" data-f="despesa" onclick="filtrarTipo(this)">Despesas</button>
<button type="button" class="aba" data-f="receita" onclick="filtrarTipo(this)">Receitas</button>
</div>
{% if q_search %}
<table style="width:100%;border-collapse:collapse;margin:1rem 0">
<tbody>
{% if n_resultados == 0 %}
<tr><td colspan="5" style="padding:1.2rem;text-align:center;color:#8a8a85">Nenhum lançamento encontrado pra "{{ q_search }}"</td></tr>
{% else %}
{% for l in lancamentos %}
<tr data-tipo="{{ l.tipo }}" style="border-bottom:1px solid #2a2a2b">
<td style="padding:.7rem .8rem;font-size:.9rem;white-space:nowrap">{{ l.data.strftime('%d/%m/%Y') }}</td>
<td style="padding:.7rem .8rem">{{ l.descricao }}{% if l.origem=='foto' %} 📷{% endif %}</td>
<td style="padding:.7rem .8rem;font-size:.85rem;color:#a8a8a3">{{ l.categoria }}</td>
<td style="padding:.7rem .8rem;text-align:right;font-weight:500;color:{{ '#5dcaa5' if l.tipo=='receita' else '#f0b8b8' }}">
{{ '+' if l.tipo=='receita' else '−' }} {{ brl(l.valor).replace('R$ ','') }}</td>
<td style="padding:.7rem .4rem;text-align:right">
<button type="button" class="lanc-rm" data-id="{{ l.id }}" onclick="apagarLanc(this)" title="apagar lançamento" style="margin:0;padding:.15rem .45rem;width:auto;background:transparent;color:#8a3636;border:0;cursor:pointer;font-size:.95rem">✕</button>
</td>
</tr>
{% endfor %}
{% endif %}
</tbody>
</table>
{% else %}
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
{% for l in dia.itens %}<tr data-tipo="{{ l.tipo }}" data-cat="{{ canon(l.categoria, l.tipo) }}" data-desc="{{ l.descricao }}" data-valor="{{ brl(l.valor) }}">
<td>{{ l.descricao }}{% if l.origem=='foto' %} 📷{% endif %}</td>
<td style="white-space:nowrap">
<span style="display:inline-flex;align-items:center;gap:.4rem">
<select class="cat-edit" data-id="{{ l.id }}" data-orig="{{ canon(l.categoria, l.tipo) }}" onchange="catMudou(this)"
   style="background:#0e0e0f;border:1px solid #2a3a33;border-radius:6px;color:#ececec;font-size:.78rem;padding:.2rem .45rem;max-width:130px">
{% for c in categorias_de(l.tipo) %}<option value="{{ c }}" style="background:#161617;color:#ececec" {% if canon(l.categoria, l.tipo)==c %}selected{% endif %}>{{ c }}</option>{% endfor %}
</select>
<button type="button" class="cat-ok" onclick="salvarCat(this)" style="display:none;padding:.24rem .65rem;width:auto;font-size:.72rem;font-weight:600;background:#1d9e75;color:#fff;border:0;border-radius:6px;cursor:pointer;line-height:1.1">OK</button>
</span>
</td>
{% if pessoas|length > 1 %}<td class="mut">{{ l.quem }}</td>{% endif %}
<td style="text-align:right; font-weight:500; color:{{ '#5dcaa5' if l.tipo=='receita' else '#f0b8b8' }}">
{{ '+' if l.tipo=='receita' else '−' }} {{ brl(l.valor).replace('R$ ','') }}</td>
<td style="text-align:right"><button type="button" class="lanc-rm" data-id="{{ l.id }}" onclick="apagarLanc(this)" title="apagar lançamento" style="margin:0;padding:.15rem .45rem;width:auto;background:transparent;color:#8a3636;border:0;cursor:pointer;font-size:.95rem">✕</button></td></tr>{% endfor %}
</table>
</div>
</div>
{% else %}<p class="mut">Nenhum lançamento neste período.</p>{% endfor %}
</div>
{% endif %}
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
function abrirCat(el){
  var box = el.nextElementSibling;            // .cat-lancamentos
  var seta = el.querySelector('.seta');
  var jaAberto = box.style.display !== 'none';
  // fecha todas
  document.querySelectorAll('.cat-lancamentos').forEach(function(b){ b.style.display='none'; });
  document.querySelectorAll('.cat-linha .seta').forEach(function(s){ s.textContent='▸'; });
  if(jaAberto){ return; }
  var cat = el.getAttribute('data-cat');       // categoria canônica
  var tipo = el.getAttribute('data-tipo') || 'despesa';  // tipo: despesa ou receita
  // pega TODAS as linhas de lançamento da página com a mesma categoria e tipo
  var linhas = document.querySelectorAll('#lista-dias tr[data-cat="'+cat+'"][data-tipo="'+tipo+'"]');
  var html = '';
  linhas.forEach(function(tr){
    html += '<div style="display:flex;justify-content:space-between;font-size:.85rem;margin:.25rem 0">'
          + '<span>'+ tr.getAttribute('data-desc') +'</span>'
          + '<span class="mut">'+ tr.getAttribute('data-valor') +'</span></div>';
  });
  box.innerHTML = html || '<span class="mut" style="font-size:.85rem">Sem lançamentos detalhados deste mês.</span>';
  box.style.display = 'block';
  seta.textContent = '▾';
}
function catMudou(sel){
  // mostra o botao OK so' quando o valor muda em relacao ao salvo
  var btn = sel.parentElement.querySelector('.cat-ok');
  btn.style.display = (sel.value !== sel.getAttribute('data-orig')) ? 'inline-block' : 'none';
}
function salvarCat(btn){
  var sel = btn.parentElement.querySelector('.cat-edit');
  var fd = new FormData();
  fd.append('lancamento_id', sel.getAttribute('data-id'));
  fd.append('categoria', sel.value);
  btn.disabled = true; btn.textContent = '...';
  fetch('/painel/lancamento/categoria', {method:'POST', body: fd})
    .then(function(r){ return r.json(); })
    .then(function(d){
      if(d.ok){
        // atualiza no lugar, SEM recarregar a pagina
        sel.setAttribute('data-orig', sel.value);
        var tr = sel.closest('tr'); if(tr){ tr.setAttribute('data-cat', sel.value); }
        sel.style.borderColor = '#5dcaa5';
        btn.textContent = '✓';
        setTimeout(function(){
          btn.style.display = 'none'; btn.disabled = false;
          btn.textContent = 'OK'; sel.style.borderColor = '#2a3a33';
        }, 1100);
      } else {
        btn.disabled = false; btn.textContent = 'OK'; sel.style.borderColor = '#f0b8b8';
      }
    })
    .catch(function(){ btn.disabled = false; btn.textContent = 'OK'; sel.style.borderColor = '#f0b8b8'; });
}
function apagarLanc(btn){
  if(!confirm('Apagar este lançamento? Essa ação não pode ser desfeita.')) return;
  var fd = new FormData();
  fd.append('lancamento_id', btn.getAttribute('data-id'));
  btn.disabled = true;
  fetch('/painel/lancamento/apagar', {method:'POST', body: fd})
    .then(function(r){ return r.json(); })
    .then(function(d){
      if(d.ok){ var tr = btn.closest('tr'); if(tr){ tr.remove(); } }
      else { btn.disabled = false; alert('Não consegui apagar.'); }
    })
    .catch(function(){ btn.disabled = false; alert('Erro ao apagar.'); });
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
<div style="display:flex; gap:.5rem; align-items:center">
<h1 style="margin:0" id="titulo-abas">Lista de compras</h1>
</div>
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

<div class="abas">
  <button id="aba-lista" class="aba on" onclick="setAba('lista')" style="margin:0">Lista</button>
  <button id="aba-hist" class="aba" onclick="setAba('historico')" style="margin:0">Histórico</button>
</div>

<button id="btn-comparar" onclick="carregarPrecos()" style="margin:.5rem 0;padding:.4rem 1rem;background:#6366f1;color:#fff;border:0;border-radius:8px;cursor:pointer;font-size:.9rem">📊 Comparar preços</button>

<div id="comparador" style="margin:1rem 0"></div>

<div id="toggle-visao" style="display:none; gap:.5rem; margin:.5rem 0">
  <button id="bt-geral" onclick="setVisao('geral')"
    style="margin:0;padding:.4rem .9rem;border-radius:8px;border:0;cursor:pointer;background:#1d9e75;color:#fff">Geral</button>
  <button id="bt-pessoa" onclick="setVisao('pessoa')"
    style="margin:0;padding:.4rem .9rem;border-radius:8px;border:1px solid #2a2a2b;cursor:pointer;background:#1a2233;color:#e7ecf3">Por pessoa</button>
</div>

<div id="lista-itens"></div>
<div id="btn-finalizar-box"></div>
<p id="resumo-lista" class="mut" style="margin-top:1rem"></p>
</div>

<script>
var DEPARTAMENTOS = [
  ["Hortifruti", ["alface","tomate","cebola","batata","banana","maca","maçã","laranja","limao","limão",
    "cenoura","alho","mamao","mamã","manga","uva","melancia","abacaxi","abacate","pera","morango",
    "couve","brocolis","brócolis","pimentao","pimentã","pepino","abobora","abóbora","chuchu","mandioca",
    "verdura","legume","fruta","salsa","cebolinha","coentro","rucula","rúcula","espinafre","beterraba",
    "manjericao","manjericã","gengibre","milho","vagem","quiabo","berinjela"]],
  ["Açougue", ["carne","frango","boi","porco","linguica","linguiça","bacon","bife","costela","file","filé",
    "coxa","sobrecoxa","peito de frango","picanha","alcatra","patinho","moida","moída","peixe","tilapia",
    "tilá","camarao","camarã","salsicha","peito","pernil","cupim","fraldinha","maminha","acem","acém"]],
  ["Laticínios", ["leite","queijo","iogurte","manteiga","margarina","requeijao","requeijã","creme de leite",
    "leite condensado","nata","mussarela","muçarela","mozzarela","prato","minas","coalho","ricota",
    "cream cheese","danone","ovos","ovo","parmesao","parmesã"]],
  ["Padaria", ["pao","pã","paes","pã","bolo","torrada","biscoito","bolacha","croissant","baguete",
    "rosca","frances","francês","forma","bisnaga","panettone","pao de queijo","pã de queijo"]],
  ["Bebidas", ["agua","água","refrigerante","suco","cerveja","vinho","cafe","café","cha","chá","achocolatado",
    "energetico","energético","coca","guarana","guaraná","fanta","sprite","whisky","vodka","gin","leite de coco",
    "isotonico","isotônico","gatorade","red bull","tonica","tônica","champagne","espumante"]],
  ["Limpeza", ["detergente","sabao","sabã","amaciante","desinfetante","agua sanitaria","água sanitária","candida",
    "câ","alvejante","limpa","multiuso","esponja","saco de lixo","vassoura","rodo","pano","cloro","veja",
    "ype","ypê","omo","comfort","pinho","desengordurante","lustra","cera"]],
  ["Higiene", ["sabonete","shampoo","xampu","condicionador","pasta de dente","creme dental","escova",
    "papel higienico","papel higiênico","absorvente","fralda","desodorante","cotonete","algodao","algodã",
    "lenço","lenco","aparelho de barbear","gilete","hidratante","fio dental","enxaguante","listerine"]],
  ["Mercearia", ["arroz","feijao","feijã","macarrao","macarrã","oleo","óleo","azeite","sal","acucar","açúcar",
    "farinha","molho","extrato","atum","sardinha","milho","ervilha","seleta","fermento","amido","tempero",
    "vinagre","cafe","café","achocolatado","cereal","aveia","granola","mel","geleia","amendoim","castanha",
    "lentilha","grao","grã","trigo","fuba","fubá","tapioca","leite em po","leite em pó","sopa","catchup",
    "ketchup","mostarda","maionese","shoyu","caldo","macarrao instantaneo","miojo","nescau","leite ninho",
    "biscoito","bolacha","chocolate","bala","doce","pipoca","salgadinho","chips","gelatina","pudim"]],
  ["Congelados", ["congelado","sorvete","polpa","hamburguer","hambúrguer","nuggets","pizza","lasanha",
    "pao de queijo congelado","empanado","petit pois","ervilha congelada"]],
];
var ORDEM_DEP = ["Hortifruti","Açougue","Laticínios","Padaria","Congelados","Mercearia","Bebidas","Limpeza","Higiene","Outros"];

function departamentoDe(nome){ var n=(nome||"").toLowerCase(); for(var d=0;d<DEPARTAMENTOS.length;d++){ var palavras=DEPARTAMENTOS[d][1]; for(var p=0;p<palavras.length;p++){ if(n.indexOf(palavras[p])!==-1) return DEPARTAMENTOS[d][0]; }} return "Outros"; }

(function(){
  var listaEl = document.getElementById('lista-itens');
  var resumoEl = document.getElementById('resumo-lista');
  var compEl = document.getElementById('comparador');
  var btnApagar = document.getElementById('btn-apagar');
  var ajustes = {};
  var pendentesAtuais = [];
  var _ultimoDados = null;
  var _aba = 'lista';

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

  function setAba(a){
    _aba = a;
    document.getElementById('aba-lista').className = 'aba' + (a==='lista'?' on':'');
    document.getElementById('aba-hist').className = 'aba' + (a==='historico'?' on':'');
    if(a==='lista'){ render(_ultimoDados || {itens:[]}); }
    else { carregarHistorico(); }
  }

  function renderItens(itens){
    var pendentes = itens.filter(function(i){return !i.comprado;});
    var comprados = itens.filter(function(i){return i.comprado;});

    if(!pendentes.length && !comprados.length){
      listaEl.innerHTML = '<p class="mut">A lista está vazia. Adicione itens acima — ou peça pelo WhatsApp/Telegram: <i>"acabou o arroz, bota na lista"</i>.</p>';
      return;
    }

    var dica = pendentes.length ? '<div class="dica-toque">👆 toque no item = comprei · toque no ✕ = tirar da lista</div>' : '';

    var grupos={};
    pendentes.forEach(function(i){ var d=departamentoDe(i.descricao); (grupos[d]=grupos[d]||[]).push(i); });
    var html = dica;
    ORDEM_DEP.forEach(function(dep){
      if(!grupos[dep]) return;
      html += '<div class="dephead">'+dep+'</div>';
      ordenarAlf(grupos[dep]).forEach(function(i){ html += linhaItemAtivo(i); });
    });
    if(!pendentes.length) html += '<div class="mut" style="text-align:center;padding:1rem">Tudo no carrinho! 🛒</div>';

    if(comprados.length){
      html += '<div class="cart-head" onclick="toggleCarrinho()"><span id="cart-arrow">▸</span> No carrinho ('+comprados.length+')</div>';
      html += '<div id="cart-body" style="display:none">';
      ordenarAlf(comprados).forEach(function(i){ html += linhaItemComprado(i); });
      html += '</div>';
      html += '<button class="btn-finalizar" onclick="finalizarCompra('+comprados.length+')">✓ Finalizar compra ('+comprados.length+' itens)</button>';
    }
    listaEl.innerHTML = html;
  }

  function ordenarAlf(itens){
    return itens.slice().sort(function(a,b){ return (a.descricao||'').localeCompare((b.descricao||''),'pt',{sensitivity:'base'}); });
  }

  function linhaItemAtivo(i){
    var preco = i.preco ? '<span class="mut"> · ~'+i.preco+'</span>' : '';
    var quem = (_visao==='geral' && i.quem) ? '<span class="mut" style="font-size:.78rem"> · '+esc(i.quem)+'</span>' : '';
    return '<div class="litem" data-id="'+i.id+'">'
         + '<div class="toque" onclick="marcarItem('+i.id+')">'
         + '<span class="bol"></span>'
         + '<span class="nome">'+esc(i.descricao)+preco+quem+'</span></div>'
         + '<button class="rem" onclick="removerItem(event,'+i.id+')" title="tirar da lista">✕</button>'
         + '</div>';
  }

  function linhaItemComprado(i){
    return '<div style="display:flex;align-items:center;gap:.6rem;padding:.45rem .6rem;opacity:.7">'
         + '<span style="color:#1d9e75;flex-shrink:0">✓</span>'
         + '<span style="color:#7a8a82;text-decoration:line-through;font-size:14px;flex:1">'+esc(i.descricao)+'</span>'
         + '<button onclick="desmarcarItem('+i.id+')" title="voltar pra lista" '
         + 'style="flex-shrink:0;width:32px;height:32px;display:flex;align-items:center;justify-content:center;'
         + 'background:transparent;border:0;color:#5dcaa5;cursor:pointer;font-size:1rem;border-radius:8px">↩</button>'
         + '</div>';
  }

  function marcarItem(id){
    var el = listaEl.querySelector('.litem[data-id="'+id+'"]');
    if(el){ el.classList.add('flash-v','done'); el.querySelector('.bol').textContent='✓';
            setTimeout(function(){ el.classList.add('saindo'); },280); }
    setTimeout(function(){ acao({acao:'marcar', item_id:id, comprado:1}); }, 600);
  }

  function removerItem(ev,id){
    ev.stopPropagation();
    var el = listaEl.querySelector('.litem[data-id="'+id+'"]');
    if(el){ el.classList.add('flash-r');
            setTimeout(function(){ el.classList.add('saindo'); },180); }
    setTimeout(function(){ acao({acao:'remover', item_id:id}); }, 480);
  }

  function desmarcarItem(id){ acao({acao:'marcar', item_id:id, comprado:0}); }

  function toggleCarrinho(){
    var b=document.getElementById('cart-body'), a=document.getElementById('cart-arrow');
    if(!b) return; var ab=b.style.display!=='none';
    b.style.display=ab?'none':'block'; a.textContent=ab?'▸':'▾';
  }

  function finalizarCompra(n){
    if(!confirm('Finalizar a compra? Os '+n+' itens comprados vão pro histórico. Os que faltam continuam na lista.')) return;
    acao({acao:'finalizar'}).then(function(){
      resumoEl.textContent = 'Compra finalizada! ✓';
      setTimeout(function(){ resumoEl.textContent = ''; }, 3000);
    });
  }

  function carregarHistorico(){
    listaEl.innerHTML = '<p class="mut">Carregando histórico...</p>';
    fetch('/painel/compras/historico').then(function(r){return r.json();}).then(function(d){
      var h = d.historico || [];
      if(!h.length){ listaEl.innerHTML = '<p class="mut">Nenhuma compra finalizada ainda. Quando você finalizar uma compra, ela aparece aqui.</p>'; return; }
      var html = '';
      h.forEach(function(c, idx){
        var itensTxt = (c.itens||[]).join(' · ');
        html += '<div class="hist-dia">'
              + '<div class="hist-head" onclick="toggleDia('+idx+')">'
              + '<span><span id="harr'+idx+'">▸</span> '+esc(c.data)+'</span>'
              + '<span class="mut" style="font-size:.8rem">'+c.total_itens+' itens</span></div>'
              + '<div id="hday'+idx+'" class="hist-itens" style="display:none">'+esc(itensTxt)+'</div>'
              + '</div>';
      });
      listaEl.innerHTML = html;
    });
  }

  function toggleDia(idx){
    var b=document.getElementById('hday'+idx), a=document.getElementById('harr'+idx);
    if(!b) return; var ab=b.style.display!=='none';
    b.style.display=ab?'none':'block'; a.textContent=ab?'▸':'▾';
  }

  function render(d){
    _ultimoDados = d;
    if(_aba !== 'lista') return;

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

  // Exportar funcoes pra escopo global
  window.setVisao = setVisao;
  window.renderItens = renderItens;
  window.ligarBotoesLinha = ligarBotoesLinha;
  window.carregarPrecos = carregarPrecos;
  window.setAba = setAba;
  window.toggleCarrinho = toggleCarrinho;
  window.finalizarCompra = finalizarCompra;
  window.carregarHistorico = carregarHistorico;
  window.toggleDia = toggleDia;
  window.marcarItem = marcarItem;
  window.removerItem = removerItem;
  window.desmarcarItem = desmarcarItem;
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

_COMPRA_REVISAR = """{% extends "base" %}{% block conteudo %}
<div class="card larga"><a href="/painel/fornecedor" style="color:#5dcaa5;display:inline-block;margin-bottom:1rem">← voltar</a>
<h2 style="margin-top:0">🛒 Compra #{{ compra.id }}</h2>
<p class="mut">Origem: <strong>{{ compra.origem_nome or '-' }}</strong> · {{ compra.data }} · status: <strong>{{ compra.status }}</strong></p>
{% if aviso %}<div class="ok">{{ aviso }}</div>{% endif %}
{% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
{% if compra.status == 'rascunho' %}
<div style="background:#1c1c1f;border:1px solid #2a2a2b;border-radius:8px;padding:1.2rem;margin:1.5rem 0">
<h4 style="margin-top:0">Adicionar item</h4>
{% if produtos %}
<form method="post" action="/painel/fornecedor/compras/{{ compra.id }}/item" style="display:grid;gap:1rem">
<div style="display:grid;grid-template-columns:1fr 1fr;gap:1rem">
<div><label>Produto</label><select name="produto_id" required style="width:100%"><option value="">Selecione um produto</option>
{% for p in produtos %}<option value="{{ p.id }}">{{ p.nome }} ({{ p.unidade }})</option>{% endfor %}
</select></div>
<div><label>Quantidade</label><input name="quantidade" type="number" step="0.001" required placeholder="50"></div>
</div>
<div><label>Custo unitário (R$)</label><input name="custo_unit" placeholder="4,00" required></div>
<button style="background:#1d9e75;color:#fff;padding:.6rem 1rem;border:0;border-radius:6px;cursor:pointer;width:100%;font-weight:500">Adicionar item</button>
</form>
{% else %}
<p class="mut">Você ainda não tem produtos no catálogo. <a href="/painel/fornecedor" style="color:#5dcaa5">Crie um produto</a> primeiro.</p>
{% endif %}
</div>
{% endif %}
<h4>Itens da compra</h4>
{% if itens %}
<table style="width:100%;border-collapse:collapse;font-size:.9rem;margin-bottom:1.5rem">
<tr style="border-bottom:2px solid #2a2a2b;color:#888"><th style="text-align:left;padding:.6rem;font-weight:500">Produto</th><th style="text-align:center;padding:.6rem;font-weight:500">Qtd</th><th style="text-align:right;padding:.6rem;font-weight:500">Custo un.</th><th style="text-align:right;padding:.6rem;font-weight:500">Subtotal</th></tr>
{% for i in itens %}
<tr style="border-top:1px solid #2a2a2b"><td style="padding:.6rem">{{ i.produto_nome or i.descricao }}</td><td style="text-align:center;padding:.6rem">{{ i.quantidade }} {{ i.unidade }}</td><td style="text-align:right;padding:.6rem">R$ {{ "%.2f"|format(i.custo_unit_centavos/100) }}</td><td style="text-align:right;padding:.6rem;font-weight:500">R$ {{ "%.2f"|format(i.quantidade * i.custo_unit_centavos/100) }}</td></tr>
{% endfor %}
<tr style="border-top:2px solid #2a2a2b;background:#2a2a2b"><td colspan="3" style="text-align:right;padding:.6rem;font-weight:600">Total:</td><td style="text-align:right;padding:.6rem;font-weight:600">R$ {{ "%.2f"|format(compra.total_centavos/100) }}</td></tr>
</table>
{% if compra.status == 'rascunho' %}
<form method="post" action="/painel/fornecedor/compras/{{ compra.id }}/confirmar">
<button style="background:#1d9e75;color:#fff;padding:.7rem 1rem;border:0;border-radius:8px;cursor:pointer;width:100%;font-weight:600;font-size:1rem;margin-bottom:1rem">✓ Confirmar compra (dar entrada no estoque)</button>
</form>
<p class="mut" style="font-size:.85rem;margin:0">Ao confirmar, os itens dão entrada no estoque e o custo médio é recalculado. Não dá pra editar depois.</p>
{% else %}
<div style="background:#2a3a2a;border:1px solid #1d9e75;color:#5dcaa5;padding:.8rem;border-radius:6px;text-align:center">✓ Compra confirmada em {{ compra.data }}</div>
{% endif %}
{% else %}
<p class="mut" style="background:#2a2a2b;padding:1rem;border-radius:6px">Nenhum item ainda. Adicione acima.</p>
{% endif %}
</div>
{% endblock %}"""

_LOJA = """{% extends "base" %}{% block conteudo %}
<div class="card larga"><h2>{{ fornecedor.nome }}</h2>
<p class="mut">Escolha o tamanho e a frequência de sua cesta. Sem fidelidade — cancele quando quiser.</p>
<form method="post" action="/f/{{ fornecedor.slug }}/assinar" style="background:#1c1c1f;border:1px solid #2a2a2b;border-radius:8px;padding:1.5rem">
<h4>Tamanhos disponíveis</h4>
{% for t in tamanhos %}
<div style="background:#2a2a2b;padding:.8rem;margin-bottom:.6rem;border-radius:6px;cursor:pointer" onclick="document.getElementById('tam-{{ t.id }}').checked=true">
  <label style="cursor:pointer;display:flex;align-items:center;gap:.5rem">
    <input type="radio" name="tamanho_id" id="tam-{{ t.id }}" value="{{ t.id }}" required {% if escolha.tamanho_id and escolha.tamanho_id == t.id %}checked{% endif %}>
    <strong>{{ t.nome }}</strong> — R$ {{ "%.2f"|format(t.preco_centavos/100) }}
  </label>
  <div class="mut" style="font-size:.85rem;margin-top:.3rem">{{ t.qtd_frutas }}🍓 {{ t.qtd_legumes }}🥕 {{ t.qtd_verduras }}🥬 {{ t.qtd_temperos }}🌿 ({{ t.total_porcoes }} porções)</div>
  {% if t.descricao %}<div class="mut" style="font-size:.85rem">{{ t.descricao }}</div>{% endif %}
</div>
{% endfor %}
<h4 style="margin-top:1.5rem">Frequência</h4>
<select name="frequencia" style="width:100%">
  <option value="semanal" {% if (escolha.frequencia or 'semanal') == 'semanal' %}selected{% endif %}>Semanal (a cada 7 dias)</option>
  <option value="quinzenal" {% if (escolha.frequencia or 'semanal') == 'quinzenal' %}selected{% endif %}>Quinzenal (a cada 14 dias)</option>
  <option value="mensal" {% if (escolha.frequencia or 'semanal') == 'mensal' %}selected{% endif %}>Mensal (a cada 30 dias)</option>
</select>
<h4 style="margin-top:1.5rem">Produtos que você NÃO quer receber</h4>
<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.5rem;max-height:200px;overflow-y:auto">
{% for p in produtos %}
<label style="display:flex;gap:.3rem;font-size:.9rem">
  <input type="checkbox" name="restricoes" value="{{ p.id }}" {% if p.id in (escolha.restricoes or []) %}checked{% endif %}> {{ p.nome }}
</label>
{% endfor %}
</div>
<button style="background:#1d9e75;color:#fff;padding:.7rem 1rem;border:0;border-radius:6px;cursor:pointer;width:100%;font-weight:600;font-size:1rem;margin-top:1.5rem">✓ Assinar</button>
<p class="mut" style="font-size:.85rem;margin-top:.5rem;text-align:center">Você só paga 4 dias antes de cada entrega.</p>
</form>
</div>
{% endblock %}"""

_LOJA_CONFIRMAR_NOVO = """{% extends "base" %}{% block conteudo %}
<div class="card" style="max-width:420px;margin:0 auto">
  <h2 style="margin-bottom:.3rem">Confirme sua assinatura 🧺</h2>
  <p class="mut" style="margin-bottom:1.2rem;font-size:.9rem">
    Crie sua conta grátis pra receber sua cesta.
    Já tem conta? <a href="/login" style="color:#5dcaa5">Entrar</a>
  </p>
  {% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
  <form method="post" action="/cadastro">
    <input type="hidden" name="next" value="/f/{{ slug }}/confirmar">
    <input type="hidden" name="plano" value="cesta">
    <label>Email</label>
    <input name="email" type="email" required placeholder="seu@email.com" style="width:100%;margin-bottom:.6rem">
    <label>Senha</label>
    <input name="senha" type="password" required placeholder="mínimo 8 caracteres" style="width:100%;margin-bottom:.6rem">
    <label>📱 WhatsApp (com DDD) <span class="mut" style="font-size:.8rem">— pra avisar da sua cesta</span></label>
    <input name="whatsapp" required placeholder="86 98888-7777" maxlength="20" style="width:100%;margin-bottom:.6rem">
    <label>CEP</label>
    <input name="cep" required placeholder="64000-000" style="width:100%;margin-bottom:.6rem">
    <label>Endereço</label>
    <input name="endereco" required placeholder="Rua X, nº 123, bairro" style="width:100%;margin-bottom:1rem">
    <button style="background:#1d9e75;color:#fff;padding:.7rem 1rem;border:0;border-radius:6px;cursor:pointer;width:100%;font-weight:600;font-size:.95rem">
      Criar conta e assinar 🧺
    </button>
  </form>
  <p class="mut" style="font-size:.8rem;text-align:center;margin-top:.8rem">
    Conta grátis. Você só paga a cesta, 4 dias antes de cada entrega. Sem fidelidade.
  </p>
</div>
{% endblock %}"""

_PAINEL_ASSINATURAS = """{% extends "base" %}{% block conteudo %}
<div class="card larga"><h2>Minhas assinaturas</h2>
{% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
{% if aviso %}<div class="ok">{{ aviso }}</div>{% endif %}
{% if assinaturas %}
{% for a in assinaturas %}
<div style="background:#1c1c1f;border:1px solid #2a2a2b;border-radius:8px;padding:1rem;margin-bottom:.6rem">
  <div style="display:flex;justify-content:space-between;align-items:start">
    <div style="flex:1">
      <strong>{{ a.tamanho_nome }}</strong> — <strong>R$ {{ "%.2f"|format(a.preco_centavos/100) }}</strong>
      <div class="mut" style="font-size:.85rem;margin-top:.3rem">De: {{ a.fornecedor_nome }} | Frequência: {{ a.frequencia }} | Status: <strong>{{ a.status }}</strong></div>
      <!-- Trocar tamanho (apenas se tem tamanhos disponíveis) -->
      {% if tamanhos_por_fornecedor.get(a.fornecedor_id) %}
      <form method="post" action="/painel/assinaturas/{{ a.id }}/trocar-tamanho" style="display:flex;gap:.5rem;margin-top:.6rem;align-items:end">
        <div style="flex:1">
          <label style="font-size:.8rem;color:#888">Mudar tamanho:</label>
          <select name="novo_tamanho_id" style="width:100%;padding:.4rem;border:1px solid #2a2a2b;border-radius:4px;background:#0a0a0a;color:#fff;font-size:.85rem">
            <option value="">Escolher novo tamanho...</option>
            {% for t in tamanhos_por_fornecedor[a.fornecedor_id] %}
              {% if t.id != a.tamanho_id_atual %}
              <option value="{{ t.id }}">{{ t.nome }} — R$ {{ "%.2f"|format(t.preco_centavos/100) }}</option>
              {% endif %}
            {% endfor %}
          </select>
        </div>
        <button type="submit" style="background:#5dcaa5;color:#0a0a0a;border:0;padding:.4rem .8rem;border-radius:4px;cursor:pointer;font-weight:600;font-size:.85rem;white-space:nowrap">Trocar</button>
      </form>
      <p class="mut" style="font-size:.75rem;margin-top:.3rem">Muda a partir da próxima entrega.</p>
      {% endif %}
    </div>
    <form method="post" action="/painel/assinaturas/{{ a.id }}/status" style="display:inline">
      <select name="novo_status" onchange="this.form.submit()" style="padding:.3rem;border:1px solid #2a2a2b;border-radius:4px;background:#0a0a0a;color:#fff;font-size:.85rem">
        <option value="">Ações</option>
        {% if a.status != 'ativa' %}<option value="ativa">Reativar</option>{% endif %}
        {% if a.status == 'ativa' %}<option value="pausada">Pausar</option>{% endif %}
        {% if a.status != 'cancelada' %}<option value="cancelada">Cancelar</option>{% endif %}
      </select>
    </form>
  </div>
</div>
{% endfor %}
{% else %}
<p class="mut">Você ainda não tem assinaturas. <a href="/" style="color:#5dcaa5">Explore os fornecedores</a></p>
{% endif %}
</div>
{% endblock %}"""

_MEU_PLANO = """{% extends "base" %}{% block conteudo %}
<div class="card larga"><h2>Meu plano</h2>
{% if aviso %}<div class="ok">{{ aviso }}</div>{% endif %}
{% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
{% if assinaturas %}
{% for a in assinaturas %}
<div style="background:#1c1c1f;border:1px solid #2a2a2b;border-radius:8px;padding:1rem;margin-bottom:.8rem">
  <strong>{{ a.tamanho_nome }}</strong> — R$ {{ "%.2f"|format(a.preco_centavos/100) }}
  <div class="mut" style="font-size:.85rem">De: {{ a.fornecedor_nome }} · {{ a.frequencia }} · {{ a.status }}</div>
  <!-- Trocar de tamanho -->
  <form method="post" action="/painel/meu-plano/trocar" style="margin-top:.6rem">
    <input type="hidden" name="assinatura_id" value="{{ a.id }}">
    <label style="font-size:.85rem">Trocar tamanho (vale na próxima entrega):</label>
    <select name="novo_tamanho_id" style="width:100%;margin:.3rem 0;padding:.4rem;border:1px solid #2a2a2b;border-radius:4px;background:#0a0a0a;color:#fff">
      <option value="">Escolher novo tamanho...</option>
      {% for t in tamanhos_por_forn[a.fornecedor_id] %}
        {% if t.id != a.tamanho_id_atual %}
        <option value="{{ t.id }}">{{ t.nome }} — R$ {{ "%.2f"|format(t.preco_centavos/100) }}</option>
        {% endif %}
      {% endfor %}
    </select>
    <button type="submit" style="background:#1d9e75;color:#fff;padding:.4rem .8rem;border:0;border-radius:6px;cursor:pointer;font-size:.85rem;margin-top:.3rem">Trocar tamanho</button>
  </form>
</div>
{% endfor %}
{% else %}
<p class="mut">Você ainda não tem uma assinatura. <a href="/painel/assinaturas" style="color:#5dcaa5">Ver fornecedores</a></p>
{% endif %}
<!-- UPSELL discreto do app financeiro -->
<div style="background:#161617;border:1px solid #2a2a2b;border-radius:8px;padding:1rem;margin-top:1.5rem">
  <p style="margin:0;font-size:.88rem;color:#a8a8a3">
    💡 Sabia que o Zaq também controla seus gastos, lê cupom fiscal e organiza sua lista
    de compras? <a href="/cadastro" style="color:#5dcaa5">Conheça o app financeiro →</a>
  </p>
</div>
</div>
{% endblock %}"""

_CESTA_AJUSTE = """{% extends "base" %}{% block conteudo %}
<div class="card larga"><h2>🧺 Sua cesta da semana</h2>
{% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
{% if aviso %}<div class="ok">{{ aviso }}</div>{% endif %}

<div style="background:#1c1c1f;border:1px solid #2a2a2b;border-radius:8px;padding:1.2rem;margin-bottom:1.5rem">
  <div style="display:flex;justify-content:space-between;align-items:start;margin-bottom:1rem">
    <div>
      <h3 style="margin:0;margin-bottom:.5rem">{{ cesta.fornecedor_nome }}</h3>
      <strong>R$ {{ "%.2f"|format(cesta.preco_centavos/100) }}</strong> · Entrega: <strong>{{ cesta.data_entrega }}</strong>
    </div>
    <div style="text-align:right;font-size:.9rem;color:#5dcaa5">
      {% if cesta.status == 'confirmada' %}✓ Confirmada{% elif cesta.status == 'em_ajuste' %}Ajustando...{% else %}Sugerida{% endif %}
    </div>
  </div>
  <p class="mut" style="margin:0;font-size:.85rem">Está tudo certo? Não precisa fazer nada — sua cesta vai automático.<br>Quer tirar algo? É só remover abaixo.</p>
</div>

{% if cesta.itens %}
{% set grupos = {} %}
{% for item in cesta.itens %}
  {% if item.grupo not in grupos %}{% set _ = grupos.update({item.grupo: []}) %}{% endif %}
  {% set _ = grupos[item.grupo].append(item) %}
{% endfor %}

{% for grupo in ['fruta', 'legume', 'verdura', 'tempero'] %}
{% if grupo in grupos %}
<div style="margin-bottom:1.5rem">
  <h4 style="margin:0 0 .8rem 0;color:#5dcaa5;text-transform:capitalize">🥗 {{ grupo }}s</h4>
  <div style="display:grid;gap:.6rem">
  {% for item in grupos[grupo] %}
    <div style="background:#0a0a0a;border:1px solid #2a2a2b;border-radius:6px;padding:.8rem;display:flex;justify-content:space-between;align-items:center">
      <div>
        <strong>{{ item.nome }}</strong><br>
        <span class="mut" style="font-size:.85rem">{{ item.quantidade }} {{ item.unidade }}</span>
      </div>
      {% if cesta.status != 'confirmada' %}
      <form method="post" action="/cesta/{{ cesta.id }}/remover" style="display:inline">
        <input type="hidden" name="item_id" value="{{ item.item_id }}">
        <button style="background:#8a3636;color:#fff;border:0;border-radius:4px;padding:.4rem .8rem;cursor:pointer;font-size:.85rem">tirar</button>
      </form>
      {% endif %}
    </div>
  {% endfor %}
  </div>
</div>
{% endif %}
{% endfor %}

{% if cesta.status != 'confirmada' %}
<form method="post" action="/cesta/{{ cesta.id }}/confirmar" style="margin-top:1.5rem">
  <button style="background:#1d9e75;color:#fff;padding:.7rem 1rem;border:0;border-radius:6px;cursor:pointer;width:100%;font-weight:600;font-size:1rem">✓ Confirmar cesta</button>
  <p class="mut" style="font-size:.85rem;text-align:center;margin-top:.5rem">Ou deixe como está — vai automático. 👍</p>
</form>
{% else %}
<div style="background:#1d4620;border:1px solid #2d8659;border-radius:6px;padding:1rem;text-align:center;margin-top:1.5rem">
  <p style="margin:0;color:#5dcaa5;font-weight:500">✓ Cesta confirmada</p>
  <small class="mut">Ela será entregue em {{ cesta.data_entrega }}</small>
</div>
{% endif %}

{% else %}
<p class="mut">Nenhum item na sua cesta. Algo deu errado — avisa pra gente!</p>
{% endif %}
</div>
{% endblock %}"""

_env = Environment(loader=DictLoader({
    "base": _BASE, "cadastro": _CADASTRO, "login": _LOGIN, "bemvindo": _BEMVINDO, "painel": _PAINEL, "senha": _SENHA, "dash": _DASH, "compras": _COMPRAS, "fornecedor": _FORNECEDOR, "compra_revisar": _COMPRA_REVISAR, "loja": _LOJA, "loja_confirmar_novo": _LOJA_CONFIRMAR_NOVO, "painel_assinaturas": _PAINEL_ASSINATURAS, "meu_plano": _MEU_PLANO, "cesta_ajuste": _CESTA_AJUSTE,
}), autoescape=select_autoescape())
_env.globals["brl"] = brl
from finance.models import canonizar_categoria, categorias_de
_env.globals["canon"] = lambda c, t="despesa": canonizar_categoria(c, t)
_env.globals["categorias_de"] = categorias_de
from finance import cidades as _cidades_mod
_env.globals["cidades"] = _cidades_mod.opcoes()


def _tem_assinatura_cesta(conta_id: int) -> bool:
    """Verifica se a conta tem assinatura de cesta ativa (pra mostrar link no menu)."""
    try:
        with get_pool().connection() as c:
            r = c.execute(
                """select 1 from assinaturas
                   where cliente_id = %s and status != 'cancelada' limit 1""",
                (conta_id,),
            ).fetchone()
        return bool(r)
    except Exception:
        return False


def _render(nome: str, request: Request, **ctx) -> HTMLResponse:
    ctx.setdefault("logado", bool(request.session.get("conta_id")))
    ctx.setdefault("titulo", nome.capitalize())
    # Injeta tem_cesta pro menu decidir mostrar "Minhas cestas"
    if "tem_cesta" not in ctx and request.session.get("conta_id"):
        ctx["tem_cesta"] = _tem_assinatura_cesta(request.session["conta_id"])
    return HTMLResponse(_env.get_template(nome).render(**ctx))


# ---------- rotas ----------

@router.get("/cadastro", response_class=HTMLResponse)
def cadastro_form(request: Request):
    return _render("cadastro", request, planos=_planos(), erro=None)


@router.post("/cadastro", response_class=HTMLResponse)
def cadastro_envia(request: Request, background: BackgroundTasks,
                   plano: str = Form("cesta"), nome: str = Form(""),
                   email: str = Form(...), senha: str = Form(...),
                   documento: str = Form(""), whatsapp: str = Form(...),
                   cep: str = Form(""), endereco: str = Form(""),
                   next: str = Form("")):
    pool = get_pool()
    email = email.strip().lower()
    # Se nome vazio, usar parte do email como nome provisório
    if not nome or not nome.strip():
        nome = email.split("@")[0].capitalize()
    zap = _normalizar_zap(whatsapp)

    # Validar plano: "cesta" é a opção grátis (assinante de cesta),
    # outros planos vêm de _planos()
    eh_cesta = (plano == "cesta")
    if not eh_cesta:
        planos_ok = {p[0]: p for p in _planos()}
        if plano not in planos_ok:
            return _render("cadastro", request, planos=_planos(), erro="Plano inválido.")
        tipo = planos_ok[plano][2]
        plano_gravar = plano
    else:
        tipo = "pf"  # assinante de cesta é pessoa física; a flag eh_assinante_cesta marca o papel
        plano_gravar = None

    with pool.connection() as c:
        ja = c.execute("select 1 from contas where lower(email)=%s", (email,)).fetchone()
        zap_ja = c.execute("select 1 from membros where whatsapp_id=%s", (zap,)).fetchone()
    if ja:
        return _render("cadastro", request, planos=_planos(),
                       erro="Ja existe uma conta com esse e-mail. Tente entrar.")
    if zap_ja:
        return _render("cadastro", request, planos=_planos(),
                       erro="Esse WhatsApp ja esta cadastrado em outra conta.")

    doc = "".join(ch for ch in documento if ch.isdigit()) or None
    from finance import cidades as _cid
    from finance import cep as _cep
    _info = _cep.consultar(cep)
    regiao = _info["regiao"] if _info else None

    # FIX 1: trava de email único (captura unique violation)
    try:
        conta_id = ct.criar_conta(pool, tipo, nome.strip(), plano=plano_gravar, documento=doc,
                                  cidade=_cid.valida(regiao))  # trial 7d; CEP->regiao
        with pool.connection() as c:
            # Salvar email, senha, flag assinante_cesta, endereço e CEP (se fornecidos)
            endereco_val = (endereco or "").strip() or None
            cep_val = (cep or "").strip() or None
            c.execute("update contas set email=%s, senha_hash=%s, eh_assinante_cesta=%s, endereco=%s, cep=%s where id=%s",
                      (email, hash_senha(senha), eh_cesta, endereco_val, cep_val, conta_id))
            c.commit()
    except Exception as e:  # captura violação de unique constraint
        if "idx_contas_email_unico" in str(e) or "unique" in str(e).lower():
            return _render("cadastro", request, planos=_planos(),
                           erro="Ja existe uma conta com esse e-mail. Tente entrar.")
        raise

    ct.adicionar_membro(pool, conta_id, nome=nome.strip(), papel="dono", whatsapp_id=zap)

    # FIX 2 (Opção B): registra/atualiza lead pra o funil contar TODOS os cadastros.
    # Se a pessoa testou (lead existe), só marca virou_conta. Se cadastrou direto,
    # cria lead já como cadastro com gastos_usados=0 (não infla "testaram").
    try:
        with pool.connection() as c:
            c.execute(
                """insert into leads (canal, identificador, virou_conta, conta_id, gastos_usados)
                   values ('whatsapp', %s, true, %s, 0)
                   on conflict (canal, identificador) do update
                     set virou_conta = true, conta_id = excluded.conta_id,
                         ultimo_em = now()""",
                (zap, conta_id),
            )
            c.commit()
    except Exception:  # noqa: BLE001
        pass  # nunca quebra o cadastro por causa do funil
    codigo_dono = ct.gerar_convite_dono(pool, conta_id)  # codigo pro dono conectar Telegram
    request.session["conta_id"] = conta_id
    # email de boas-vindas (tolerante a falha - nunca quebra o cadastro)
    try:
        from finance.email_sender import enviar_boas_vindas
        background.add_task(enviar_boas_vindas, email, nome.strip(), codigo_dono)
    except Exception:  # noqa: BLE001
        pass
    # Se veio da loja (/f/{slug}/confirmar), segue pra lá pra criar a assinatura
    # Senão, vai pro /bem-vindo (fluxo normal do app)
    if next and next.startswith("/f/"):
        return RedirectResponse(next, status_code=303)
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
    # Assinante de cesta: home dele é a cesta, não o painel financeiro
    if conta[10]:  # eh_assinante_cesta
        return RedirectResponse("/painel/assinaturas", status_code=303)
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


# ========== LOJA PÚBLICA (FASE 3) ==========

@router.get("/f/{slug}", response_class=HTMLResponse)
def loja_fornecedor(request: Request, slug: str):
    from finance import cestas as cestas_mod, catalogo as cat_mod
    pool = get_pool()
    with pool.connection() as c:
        forn = c.execute(
            """select id, nome, fornecedor_slug from contas
               where fornecedor_slug = %s and eh_fornecedor""",
            (slug,),
        ).fetchone()
    if forn is None:
        return HTMLResponse("<h1>Loja não encontrada</h1>", status_code=404)
    fornecedor = {"id": forn[0], "nome": forn[1], "slug": forn[2]}
    tamanhos = cestas_mod.listar_tamanhos(pool, forn[0], so_ativos=True)
    produtos = cat_mod.listar_produtos(pool, forn[0], so_disponiveis=True)
    escolha = request.session.get("loja_escolha", {})
    # garante chaves pra não quebrar o template na primeira visita
    escolha = {
        "tamanho_id": escolha.get("tamanho_id"),
        "frequencia": escolha.get("frequencia", "semanal"),
        "restricoes": escolha.get("restricoes", []) or [],
    }
    return _render("loja", request, fornecedor=fornecedor, tamanhos=tamanhos,
                   produtos=produtos, escolha=escolha)


@router.post("/f/{slug}/assinar")
def loja_assinar(request: Request, slug: str,
                tamanho_id: int = Form(...),
                frequencia: str = Form("semanal"),
                restricoes: list[int] = Form(default=[])):
    request.session["loja_escolha"] = {
        "slug": slug, "tamanho_id": tamanho_id,
        "frequencia": frequencia, "restricoes": restricoes or [],
    }
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse(f"/f/{slug}/confirmar", status_code=303)
    # já logado: cria direto
    return _criar_assinatura_da_sessao(request, conta, slug)


@router.get("/f/{slug}/confirmar", response_class=HTMLResponse)
def loja_confirmar(request: Request, slug: str):
    conta = conta_logada(request)
    esc = request.session.get("loja_escolha", {})
    if esc.get("slug") != slug:
        return RedirectResponse(f"/f/{slug}", status_code=303)
    if conta is None:
        # sem conta: mostra cadastro/login
        return _render("loja_confirmar_novo", request, slug=slug, erro=None)
    # logado: cria a assinatura
    return _criar_assinatura_da_sessao(request, conta, slug)


def _criar_assinatura_da_sessao(request, conta, slug):
    from finance import assinaturas as assin_mod
    esc = request.session.get("loja_escolha")
    if not esc or esc.get("slug") != slug:
        return RedirectResponse("/painel", status_code=303)
    pool = get_pool()
    with pool.connection() as c:
        forn = c.execute(
            "select id from contas where fornecedor_slug=%s and eh_fornecedor",
            (slug,)).fetchone()
    if forn is None:
        return RedirectResponse("/painel", status_code=303)
    try:
        r = assin_mod.criar_assinatura(
            pool, cliente_id=conta[0], fornecedor_id=forn[0],
            tamanho_id=esc["tamanho_id"], frequencia=esc["frequencia"],
            restricoes_produto_ids=esc.get("restricoes", []),
        )
        request.session.pop("loja_escolha", None)
        request.session["aviso"] = "✓ Assinatura criada! Em breve o pagamento."
    except Exception as e:
        request.session["erro"] = f"Erro: {str(e)}"
    return RedirectResponse("/painel/assinaturas", status_code=303)


@router.get("/painel/assinaturas", response_class=HTMLResponse)
def painel_assinaturas(request: Request):
    from finance import assinaturas as assin_mod
    from finance import cestas as cestas_mod
    pool = get_pool()
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    assinaturas = assin_mod.listar_assinaturas_cliente(pool, conta[0])
    # Monta dict de tamanhos disponíveis por fornecedor
    tamanhos_por_fornecedor = {}
    for a in assinaturas:
        forn_id = a["fornecedor_id"]
        if forn_id not in tamanhos_por_fornecedor:
            tamanhos_por_fornecedor[forn_id] = cestas_mod.listar_tamanhos(pool, forn_id)
    return _render("painel_assinaturas", request, conta=conta, assinaturas=assinaturas,
                   tamanhos_por_fornecedor=tamanhos_por_fornecedor,
                   erro=request.session.pop("erro", None),
                   aviso=request.session.pop("aviso", None))


@router.post("/painel/assinaturas/{assinatura_id}/trocar-tamanho", response_class=HTMLResponse)
def trocar_tamanho_assinatura(request: Request, assinatura_id: int,
                              novo_tamanho_id: int = Form(...)):
    from finance import assinaturas as assin_mod
    pool = get_pool()
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    try:
        resultado = assin_mod.trocar_tamanho(pool, conta[0], assinatura_id, novo_tamanho_id)
        if resultado["ok"]:
            preco = resultado["preco_centavos"] / 100
            request.session["aviso"] = f"✓ Tamanho alterado! Nova cesta será R$ {preco:.2f} (a partir da próxima entrega)."
        else:
            request.session["erro"] = f"Erro: {resultado.get('erro', 'tamanho inválido')}"
    except Exception as e:
        request.session["erro"] = f"Erro: {str(e)}"
    return RedirectResponse("/painel/assinaturas", status_code=303)


@router.get("/painel/meu-plano", response_class=HTMLResponse)
def painel_meu_plano(request: Request):
    from finance import assinaturas as assin_mod
    from finance import cestas as cestas_mod
    pool = get_pool()
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    assinaturas = assin_mod.listar_assinaturas_cliente(pool, conta[0])
    # Monta dict de tamanhos disponíveis por fornecedor
    tamanhos_por_forn = {}
    for a in assinaturas:
        fid = a["fornecedor_id"]
        if fid not in tamanhos_por_forn:
            tamanhos_por_forn[fid] = cestas_mod.listar_tamanhos(pool, fid, so_ativos=True)
    return _render("meu_plano", request, conta=conta, assinaturas=assinaturas,
                   tamanhos_por_forn=tamanhos_por_forn,
                   aviso=request.session.pop("aviso", None),
                   erro=request.session.pop("erro", None))


@router.post("/painel/meu-plano/trocar", response_class=HTMLResponse)
def painel_meu_plano_trocar(request: Request,
                            assinatura_id: int = Form(...),
                            novo_tamanho_id: int = Form(...)):
    from finance import assinaturas as assin_mod
    pool = get_pool()
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    try:
        r = assin_mod.trocar_tamanho(pool, conta[0], assinatura_id, novo_tamanho_id)
        if r["ok"]:
            request.session["aviso"] = "Plano alterado! Vale a partir da próxima entrega."
        else:
            request.session["erro"] = r.get("erro", "Não foi possível trocar.")
    except Exception as e:
        request.session["erro"] = f"Erro: {str(e)}"
    return RedirectResponse("/painel/meu-plano", status_code=303)


@router.post("/painel/assinaturas/{assinatura_id}/status")
def painel_assinatura_status(request: Request, assinatura_id: int,
                            novo_status: str = Form(...)):
    from finance import assinaturas as assin_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    try:
        assin_mod.alterar_status(get_pool(), conta[0], assinatura_id, novo_status)
        request.session["aviso"] = f"Assinatura {novo_status}."
    except Exception as e:
        request.session["erro"] = f"Erro: {str(e)}"
    return RedirectResponse("/painel/assinaturas", status_code=303)


# ===== FASE 6: Janela semanal (ajuste da cesta) =====
@router.get("/cesta/{cesta_id}", response_class=HTMLResponse)
def ver_cesta(request: Request, cesta_id: int):
    from finance import janela
    conta = conta_logada(request)
    if conta is None:
        request.session["next"] = f"/cesta/{cesta_id}"
        return RedirectResponse("/login", status_code=303)
    cesta = janela.obter_cesta(get_pool(), cesta_id, cliente_id=conta[0])
    if cesta is None:
        return HTMLResponse("<h1>Cesta não encontrada</h1>", status_code=404)
    return _render("cesta_ajuste", request, cesta=cesta,
                   aviso=request.session.pop("aviso", None),
                   erro=request.session.pop("erro", None))


@router.post("/cesta/{cesta_id}/remover")
def cesta_remover_item(request: Request, cesta_id: int, item_id: int = Form(...)):
    from finance import janela
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    janela.remover_item(get_pool(), cesta_id, item_id, conta[0])
    request.session["aviso"] = "Item removido da sua cesta."
    return RedirectResponse(f"/cesta/{cesta_id}", status_code=303)


@router.post("/cesta/{cesta_id}/confirmar")
def cesta_confirmar(request: Request, cesta_id: int):
    from finance import janela
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    janela.confirmar_cesta(get_pool(), cesta_id, conta[0])
    request.session["aviso"] = "Cesta confirmada! 🧺"
    return RedirectResponse(f"/cesta/{cesta_id}", status_code=303)


# ===== Botões de teste (manual) — abrir/fechar janela =====
@router.post("/painel/teste/abrir-janela")
def teste_abrir_janela(request: Request, assinatura_id: int = Form(...)):
    from finance import janela
    conta = conta_logada(request)
    if conta is None or not conta[8]:  # eh_fornecedor
        return RedirectResponse("/painel/fornecedor", status_code=303)
    try:
        r = janela.abrir_janela_semana(get_pool(), apenas_assinatura_id=assinatura_id)
        msg = f"✓ Montadas {r['montadas']} cestas"
        if r['erros']:
            msg += f" | ⚠️ {len(r['erros'])} erros"
        request.session["aviso"] = msg
    except Exception as e:
        request.session["erro"] = f"Erro: {str(e)}"
    return RedirectResponse("/painel/fornecedor", status_code=303)


@router.post("/painel/teste/fechar-janela")
def teste_fechar_janela(request: Request, data_entrega: str = Form(...)):
    from finance import janela
    from datetime import datetime
    conta = conta_logada(request)
    if conta is None or not conta[8]:
        return RedirectResponse("/painel/fornecedor", status_code=303)
    try:
        data = datetime.strptime(data_entrega, "%Y-%m-%d").date()
        r = janela.fechar_janela(get_pool(), data)
        msg = f"✓ Confirmadas {r['confirmadas']} cestas (modo confiança)"
        request.session["aviso"] = msg
    except Exception as e:
        request.session["erro"] = f"Erro: {str(e)}"
    return RedirectResponse("/painel/fornecedor", status_code=303)


@router.get("/painel/fornecedor", response_class=HTMLResponse)
def painel_fornecedor(request: Request):
    from finance import catalogo as cat_mod
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    if not conta[8]:  # eh_fornecedor
        return RedirectResponse("/painel", status_code=303)
    pool = get_pool()
    fiscal = None
    with pool.connection() as c:
        row = c.execute(
            "select razao_social, cnpj, endereco from fornecedor_fiscal where conta_id=%s",
            (conta[0],),
        ).fetchone()
        fiscal = {
            "razao_social": row[0] if row else None,
            "cnpj": row[1] if row else None,
            "endereco": row[2] if row else None
        } if row else {"razao_social": None, "cnpj": None, "endereco": None}
    # Carrega produtos do catálogo
    produtos = cat_mod.listar_produtos(pool, conta[0])
    # Carrega origens de compra (de quem o fornecedor compra)
    origens = cat_mod.listar_origens(pool, conta[0])
    # Carrega compras
    compras_raw = []
    with pool.connection() as c:
        rows = c.execute(
            """select id, data_compra, total_centavos, fonte, status,
                      (select nome from origem_compra where id = compras_fornecedor.origem_id) as origem_nome
               from compras_fornecedor where fornecedor_id=%s order by criado_em desc limit 20""",
            (conta[0],),
        ).fetchall()
        compras_raw = [{"id": r[0], "data_compra": r[1], "total_centavos": r[2], "fonte": r[3], "status": r[4], "origem_nome": r[5]} for r in rows]
    # Carrega tamanhos de cesta
    from finance import cestas as cestas_mod
    tamanhos = cestas_mod.listar_tamanhos(pool, conta[0], so_ativos=False)
    # Carrega margem alvo do fornecedor
    margem_alvo = None
    with pool.connection() as c:
        row = c.execute(
            "select margem_alvo_pct from contas where id=%s",
            (conta[0],),
        ).fetchone()
        margem_alvo = float(row[0]) if row and row[0] else 60.0
    return _render("fornecedor", request, conta=conta, fiscal=fiscal, produtos=produtos, origens=origens, compras=compras_raw, tamanhos=tamanhos, margem_alvo=margem_alvo,
                   erro=request.session.pop("erro", None),
                   aviso=request.session.pop("aviso", None))


@router.post("/painel/fornecedor/dados")
def painel_fornecedor_dados(request: Request,
                           razao_social: str = Form(""),
                           cnpj: str = Form(""),
                           endereco: str = Form("")):
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    if not conta[8]:  # eh_fornecedor
        return RedirectResponse("/painel", status_code=303)

    pool = get_pool()
    with pool.connection() as c:
        # upsert: cria a linha fiscal se não existe, atualiza se existe
        c.execute("""
            insert into fornecedor_fiscal (conta_id, razao_social, cnpj, endereco)
            values (%s, %s, %s, %s)
            on conflict (conta_id) do update
              set razao_social=excluded.razao_social,
                  cnpj=excluded.cnpj,
                  endereco=excluded.endereco,
                  atualizado_em=now()
        """, (conta[0], razao_social or None, cnpj or None, endereco or None))
        c.commit()
    request.session["aviso"] = "Dados do fornecedor salvos."
    return RedirectResponse("/painel/fornecedor", status_code=303)


@router.post("/painel/fornecedor/margem-alvo")
def salvar_margem_alvo(request: Request, margem_alvo: str = Form("60")):
    conta = conta_logada(request)
    if conta is None or not conta[8]:
        return RedirectResponse("/painel/fornecedor", status_code=303)
    try:
        s = margem_alvo.replace("%", "").strip()
        valor = float(s)
        if valor < 0 or valor > 100:
            valor = 60.0
    except ValueError:
        valor = 60.0
    with get_pool().connection() as c:
        c.execute(
            "update contas set margem_alvo_pct = %s where id = %s",
            (valor, conta[0]),
        )
        c.commit()
    request.session["aviso"] = f"Margem alvo salva: {valor}%"
    return RedirectResponse("/painel/fornecedor", status_code=303)


@router.post("/painel/fornecedor/montar-cesta")
def montar_cesta_teste(request: Request, assinatura_id: int = Form(...)):
    from finance import montador
    conta = conta_logada(request)
    if conta is None or not conta[8]:
        return RedirectResponse("/painel/fornecedor", status_code=303)
    try:
        r = montador.montar_cesta(get_pool(), assinatura_id, persistir=True)
        msg = f"✓ Cesta montada: {len(r['itens'])} itens, custo R$ {r['custo_total_centavos']/100:.2f}"
        if r['avisos']:
            msg += " | ⚠️ " + "; ".join(r['avisos'])
        request.session["aviso"] = msg
    except Exception as e:
        request.session["erro"] = f"Erro ao montar cesta: {str(e)}"
    return RedirectResponse("/painel/fornecedor", status_code=303)


@router.post("/painel/fornecedor/catalogo/produto")
def painel_catalogo_produto(request: Request,
                           nome: str = Form(""),
                           unidade: str = Form("kg"),
                           categoria: str = Form(""),
                           preco_venda: str = Form("0"),
                           estoque_minimo: str = Form("0")):
    from finance import catalogo as cat_mod
    conta = conta_logada(request)
    if conta is None or not conta[8]:
        return RedirectResponse("/painel/fornecedor", status_code=303)
    try:
        preco_centavos = int(float(preco_venda or 0) * 100)
        produto_id = cat_mod.criar_produto(
            get_pool(), conta[0], nome, unidade, categoria,
            preco_centavos, float(estoque_minimo or 0)
        )
        request.session["aviso"] = f"Produto '{nome}' criado com sucesso!"
    except Exception as e:
        request.session["erro"] = f"Erro: {str(e)}"
    return RedirectResponse("/painel/fornecedor", status_code=303)


@router.post("/painel/fornecedor/catalogo/editar")
def painel_catalogo_editar(request: Request,
                          produto_id: int = Form(...),
                          nome: str = Form(""),
                          unidade: str = Form("kg"),
                          categoria: str = Form(""),
                          preco_venda: str = Form("0"),
                          estoque_minimo: str = Form("0")):
    from finance import catalogo as cat_mod
    conta = conta_logada(request)
    if conta is None or not conta[8]:
        return RedirectResponse("/painel/fornecedor", status_code=303)
    s = preco_venda.replace("R$", "").strip()
    s = s.replace(".", "").replace(",", ".") if "," in s else s
    try:
        preco_centavos = int(round(float(s) * 100))
    except ValueError:
        preco_centavos = 0
    try:
        cat_mod.atualizar_produto(
            get_pool(), conta[0], produto_id,
            nome=nome, unidade=unidade,
            categoria=(categoria.strip() or None),
            preco_venda_centavos=preco_centavos,
            estoque_minimo=float(estoque_minimo or 0),
        )
        request.session["aviso"] = "Produto atualizado."
    except Exception as e:
        request.session["erro"] = f"Erro: {str(e)}"
    return RedirectResponse("/painel/fornecedor", status_code=303)


@router.post("/painel/fornecedor/catalogo/entrada")
def painel_catalogo_entrada(request: Request,
                           produto_id: int = Form(...),
                           quantidade: str = Form(""),
                           custo_unit: str = Form(""),
                           origem_id: int = Form(...)):
    from finance import catalogo as cat_mod
    conta = conta_logada(request)
    if conta is None or not conta[8]:
        return RedirectResponse("/painel/fornecedor", status_code=303)
    try:
        custo_centavos = int(float(custo_unit or 0) * 100)
        result = cat_mod.registrar_movimentacao(
            get_pool(), conta[0], produto_id, "entrada",
            float(quantidade or 0), custo_centavos, origem_id
        )
        aviso = f"✓ Entrada registrada! Novo saldo: {result['saldo_novo']} " \
                f"| Custo médio: €{result['custo_medio_centavos']/100:.2f}"
        if result["abaixo_minimo"]:
            aviso += " ⚠ Abaixo do mínimo!"
        request.session["aviso"] = aviso
    except Exception as e:
        request.session["erro"] = f"Erro: {str(e)}"
    return RedirectResponse("/painel/fornecedor", status_code=303)


@router.post("/painel/fornecedor/catalogo/perda")
def painel_catalogo_perda(request: Request,
                         produto_id: int = Form(...),
                         quantidade: str = Form(""),
                         motivo: str = Form("")):
    from finance import catalogo as cat_mod
    conta = conta_logada(request)
    if conta is None or not conta[8]:
        return RedirectResponse("/painel/fornecedor", status_code=303)
    try:
        result = cat_mod.registrar_movimentacao(
            get_pool(), conta[0], produto_id, "perda",
            float(quantidade or 0), motivo=motivo
        )
        request.session["aviso"] = f"✓ Perda registrada. Novo saldo: {result['saldo_novo']}"
    except Exception as e:
        request.session["erro"] = f"Erro: {str(e)}"
    return RedirectResponse("/painel/fornecedor", status_code=303)


@router.post("/painel/fornecedor/catalogo/origem")
def painel_catalogo_origem(request: Request,
                          nome: str = Form(""),
                          contato: str = Form("")):
    from finance import catalogo as cat_mod
    conta = conta_logada(request)
    if conta is None or not conta[8]:
        return RedirectResponse("/painel/fornecedor", status_code=303)
    try:
        cat_mod.criar_origem(get_pool(), conta[0], nome, contato)
        request.session["aviso"] = f"Origem '{nome}' criada!"
    except Exception as e:
        request.session["erro"] = f"Erro: {str(e)}"
    return RedirectResponse("/painel/fornecedor", status_code=303)


@router.post("/painel/fornecedor/compras/criar")
def painel_compras_criar(request: Request,
                        data_compra: str = Form(""),
                        origem_id: int = Form(None)):
    from finance import catalogo as cat_mod
    conta = conta_logada(request)
    if conta is None or not conta[8]:
        return RedirectResponse("/painel/fornecedor", status_code=303)
    try:
        compra_id = cat_mod.criar_compra(
            get_pool(), conta[0], origem_id, data_compra, fonte="manual"
        )
        request.session["compra_id_atual"] = compra_id
        request.session["aviso"] = f"Compra #{compra_id} criada em rascunho. Adicione itens."
    except Exception as e:
        request.session["erro"] = f"Erro: {str(e)}"
    return RedirectResponse("/painel/fornecedor", status_code=303)


@router.post("/painel/fornecedor/compras/origem")
def painel_compras_origem(request: Request,
                         nome: str = Form(""),
                         contato: str = Form("")):
    from finance import catalogo as cat_mod
    conta = conta_logada(request)
    if conta is None or not conta[8]:
        return RedirectResponse("/painel/fornecedor", status_code=303)
    try:
        cat_mod.criar_origem(get_pool(), conta[0], nome, contato)
        request.session["aviso"] = f"Origem '{nome}' criada!"
    except Exception as e:
        request.session["erro"] = f"Erro: {str(e)}"
    return RedirectResponse("/painel/fornecedor", status_code=303)


@router.post("/painel/fornecedor/cestas/criar")
def painel_cestas_criar(request: Request,
                       nome: str = Form(...),
                       preco: str = Form("0"),
                       qtd_frutas: int = Form(0),
                       qtd_legumes: int = Form(0),
                       qtd_verduras: int = Form(0),
                       qtd_temperos: int = Form(0),
                       descricao: str = Form("")):
    from finance import cestas as cestas_mod
    conta = conta_logada(request)
    if conta is None or not conta[8]:
        return RedirectResponse("/painel/fornecedor", status_code=303)
    s = preco.replace("R$", "").strip()
    s = s.replace(".", "").replace(",", ".") if "," in s else s
    try:
        preco_cent = int(round(float(s) * 100))
    except ValueError:
        preco_cent = 0
    try:
        cestas_mod.criar_tamanho(get_pool(), conta[0], nome, preco_cent,
                                 qtd_frutas, qtd_legumes, qtd_verduras, qtd_temperos,
                                 descricao or None)
        request.session["aviso"] = "Tamanho de cesta criado."
    except Exception as e:
        request.session["erro"] = f"Erro: {str(e)}"
    return RedirectResponse("/painel/fornecedor", status_code=303)


@router.get("/painel/fornecedor/compras/{compra_id}", response_class=HTMLResponse)
def painel_compra_revisar(request: Request, compra_id: int):
    from finance import catalogo as cat_mod
    conta = conta_logada(request)
    if conta is None or not conta[8]:
        return RedirectResponse("/painel/fornecedor", status_code=303)
    pool = get_pool()
    try:
        itens = cat_mod.listar_itens_compra(pool, conta[0], compra_id)
    except Exception:
        request.session["erro"] = "Compra não encontrada."
        return RedirectResponse("/painel/fornecedor", status_code=303)
    produtos = cat_mod.listar_produtos(pool, conta[0])
    with pool.connection() as c:
        cab = c.execute(
            """select id, data_compra, total_centavos, status,
                      (select nome from origem_compra where id = origem_id)
               from compras_fornecedor where id=%s and fornecedor_id=%s""",
            (compra_id, conta[0]),
        ).fetchone()
    if cab is None:
        request.session["erro"] = "Compra não encontrada."
        return RedirectResponse("/painel/fornecedor", status_code=303)
    compra = {"id": cab[0], "data": cab[1], "total_centavos": cab[2],
              "status": cab[3], "origem_nome": cab[4]}
    return _render("compra_revisar", request, conta=conta, compra=compra,
                   itens=itens, produtos=produtos,
                   erro=request.session.pop("erro", None),
                   aviso=request.session.pop("aviso", None))


@router.post("/painel/fornecedor/compras/{compra_id}/item")
def painel_compra_add_item(request: Request, compra_id: int,
                          produto_id: int = Form(...),
                          quantidade: float = Form(...),
                          custo_unit: str = Form(...)):
    from finance import catalogo as cat_mod
    conta = conta_logada(request)
    if conta is None or not conta[8]:
        return RedirectResponse("/painel/fornecedor", status_code=303)
    s = custo_unit.replace("R$", "").strip().replace(".", "").replace(",", ".") \
        if "," in custo_unit else custo_unit.replace("R$", "").strip()
    try:
        custo_centavos = int(round(float(s) * 100))
    except ValueError:
        custo_centavos = 0
    try:
        prods = {p["id"]: p["nome"] for p in cat_mod.listar_produtos(get_pool(), conta[0])}
        desc = prods.get(produto_id, "produto")
        with get_pool().connection() as c:
            urow = c.execute("select unidade from catalogo_produtos where id=%s", (produto_id,)).fetchone()
        unidade = urow[0] if urow else "kg"
        cat_mod.adicionar_item_compra(
            get_pool(), conta[0], compra_id, desc, quantidade,
            custo_centavos, unidade=unidade, produto_id=produto_id,
        )
        request.session["aviso"] = "Item adicionado."
    except Exception as e:
        request.session["erro"] = f"Erro: {str(e)}"
    return RedirectResponse(f"/painel/fornecedor/compras/{compra_id}", status_code=303)


@router.post("/painel/fornecedor/compras/{compra_id}/confirmar")
def painel_compra_confirmar(request: Request, compra_id: int):
    from finance import catalogo as cat_mod
    conta = conta_logada(request)
    if conta is None or not conta[8]:
        return RedirectResponse("/painel/fornecedor", status_code=303)
    try:
        r = cat_mod.confirmar_compra(get_pool(), conta[0], compra_id)
        request.session["aviso"] = f"✓ Compra confirmada! {r['itens']} item(ns) deram entrada no estoque."
    except Exception as e:
        request.session["erro"] = f"Erro: {str(e)}"
    return RedirectResponse("/painel/fornecedor", status_code=303)


@router.get("/static/modelo_planilha_produtos.csv")
def modelo_planilha():
    from fastapi.responses import FileResponse
    import os
    arquivo = os.path.join(os.path.dirname(__file__), "..", "db", "modelos", "modelo_planilha_produtos.csv")
    return FileResponse(arquivo, media_type="text/csv", filename="modelo_planilha_produtos.csv")


@router.post("/painel/fornecedor/catalogo/ler-planilha")
async def ler_planilha(request: Request):
    from finance.catalogo_import import ler_planilha_csv
    conta = conta_logada(request)
    if conta is None or not conta[8]:
        return {"ok": False, "erro": "não autorizado"}
    try:
        form = await request.form()
        arquivo = form.get("arquivo")
        if not arquivo:
            return {"ok": False, "erro": "arquivo não enviado"}
        conteudo = await arquivo.read()
        resultado = ler_planilha_csv(conteudo)
        return resultado
    except Exception as e:
        return {"ok": False, "erro": str(e)}


@router.post("/painel/fornecedor/catalogo/importar-planilha")
async def importar_planilha(request: Request):
    from finance.catalogo_import import importar_itens
    from finance import catalogo as cat_mod
    import json
    conta = conta_logada(request)
    if conta is None or not conta[8]:
        return {"ok": False, "erro": "não autorizado"}
    try:
        body = await request.json()
        itens = body.get("itens", [])
        resultado = importar_itens(get_pool(), conta[0], itens)
        request.session["aviso"] = f"✓ Importados: {resultado['criados']} produtos"
        return resultado
    except Exception as e:
        return {"ok": False, "erro": str(e)}


@router.post("/assinar")
def assinar(request: Request):
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    pool = get_pool()
    with pool.connection() as c:
        plano_row = c.execute(
            "select codigo, nome, preco_base_centavos from planos where codigo=%s",
            (conta[4],),
        ).fetchone()
    if not plano_row:
        request.session["erro"] = "Plano não encontrado"
        return RedirectResponse("/painel", status_code=303)
    try:
        from finance.asaas import criar_link_pagamento
        valor_reais = plano_row[2] / 100.0
        link_data = criar_link_pagamento(conta_id=conta[0], nome_plano=plano_row[1],
                                         valor_reais=valor_reais)
        return RedirectResponse(link_data["url"], status_code=303)
    except Exception as e:
        log.error(f"Erro ao criar link Asaas: {e}")
        request.session["erro"] = "Erro ao gerar link de pagamento. Tente novamente."
        return RedirectResponse("/painel", status_code=303)


@router.get("/painel/financeiro", response_class=HTMLResponse)
def painel_financeiro(request: Request, mes: str = "", membro: str = "", tipo: str = "", q: str = ""):
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

    # Se há busca, usa buscar_lancamentos; senão fluxo normal
    if q:
        lancamentos = livro.buscar_lancamentos(q, limite=100)
        dias = []  # plano, não agrupado
        raiox = {}
        resumo = {"saldo": 0, "receitas": 0, "despesas": 0}
        categorias = []
        receitas_cat = []
        maior_cat = 0
        maior_rec = 0
    else:
        resumo = livro.resumo_mes(ano_sel, mes_num, membro_sel)
        categorias = livro.despesas_por_categoria(ano_sel, mes_num, membro_sel)
        maior_cat = max((v for _, v in categorias), default=0)
        receitas_cat = livro.receitas_por_categoria(ano_sel, mes_num, membro_sel)
        maior_rec = max((v for _, v in receitas_cat), default=0)
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

    from finance.models import CATEGORIAS_DESPESA, CATEGORIAS_RECEITA
    categorias_lista = CATEGORIAS_DESPESA + [c for c in CATEGORIAS_RECEITA if c not in CATEGORIAS_DESPESA]

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
                   meses=meses, mes_sel=mes_sel, membro_sel=membro_sel, tipo_sel=tipo,
                   receitas_cat=receitas_cat, maior_rec=maior_rec, categorias_lista=categorias_lista,
                   q_search=q, n_resultados=len(lancamentos) if q else 0)


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
    elif acao == "finalizar":
        resultado = lista.finalizar_compra()
    # NAO estima preco aqui (sob demanda no botao "Comparar precos" ->
    # /painel/compras/precos). Adicionar item fica leve, sem buscar preco.
    itens = lista.listar(incluir_comprados=True)  # frontend separa pendentes/comprados
    resumo = lista.resumo()
    return JSONResponse({
        "itens": [{"id": i["id"], "descricao": i["descricao"], "comprado": i["comprado"],
                   "quem": i["quem"],
                   "preco": brl(i["preco_estimado_centavos"]) if i["preco_estimado_centavos"] else None}
                  for i in itens],
        "pendentes": resumo["pendentes"], "comprados": resumo["comprados"],
        "tem_pendentes": resumo["pendentes"] > 0,
    })


@router.get("/painel/compras/historico", response_class=JSONResponse)
def compras_historico(request: Request):
    """Lista compras finalizadas desta conta."""
    conta, lista = _lista_logada(request)
    if conta is None:
        return JSONResponse({"erro": "nao logado"}, status_code=401)
    hist = lista.listar_historico()
    # formata data pra exibir
    for h in hist:
        h["data"] = h["criado_em"].strftime("%d/%m/%Y") if h.get("criado_em") else "-"
        h.pop("criado_em", None)
    return JSONResponse({"historico": hist})


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


@router.post("/painel/lancamento/categoria")
def mudar_categoria_lancamento(request: Request,
                               lancamento_id: int = Form(...),
                               categoria: str = Form(...)):
    conta = conta_logada(request)
    if not conta:
        return JSONResponse({"ok": False}, status_code=401)
    from finance.livro_caixa import LivroCaixa
    livro = LivroCaixa(get_pool(), conta[0])
    ok = livro.mudar_categoria(lancamento_id, categoria)
    return JSONResponse({"ok": bool(ok)})


@router.post("/painel/lancamento/apagar")
def apagar_lancamento_endpoint(request: Request, lancamento_id: int = Form(...)):
    conta = conta_logada(request)
    if not conta:
        return JSONResponse({"ok": False}, status_code=401)
    from finance.livro_caixa import LivroCaixa
    livro = LivroCaixa(get_pool(), conta[0])
    ok = livro.apagar_lancamento(lancamento_id)
    return JSONResponse({"ok": bool(ok)})


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
    # criar_convite foi removido; agora é adicionar_membro + gerar_convite_para
    membro_id = ct.adicionar_membro(pool, conta[0], nome=nome, papel=papel, whatsapp_id=zap)
    codigo = ct.gerar_convite_para(pool, membro_id, conta[0])
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


@router.post("/membros/reconectar")
def membros_reconectar(request: Request, membro_id: int = Form(...)):
    conta = conta_logada(request)
    if conta is None:
        return RedirectResponse("/login", status_code=303)
    codigo = ct.regerar_acesso(get_pool(), membro_id, conta[0])
    if codigo:
        request.session["aviso"] = (
            f"Código de reconexão gerado: {codigo} — mande este código pro bot "
            f"no Telegram ou WhatsApp do número novo. Pronto, você conecta!")
    else:
        request.session["erro"] = "Não foi possível gerar o código de reconexão."
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
