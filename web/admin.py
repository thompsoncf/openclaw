"""Painel ADM do OpenClaw: a torre de controle do dono do sistema.

SEGURANCA: toda rota exige que a conta logada tenha is_admin=true (setado por
SQL, nunca pela interface). Quem nao for admin recebe 404 - nem revela que a
area existe. Toda acao administrativa e' registrada na auditoria (eventos_conta).
"""
from datetime import date
import re
import unicodedata

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment, DictLoader, select_autoescape

from db.conexao import get_pool
from web import tema as _tema
from contas import contas as ct
from finance.estatisticas import estatisticas_funil, estatisticas_custo
from web.portal import brl

router = APIRouter()


def _slug(texto: str) -> str:
    """Gera slug a partir do nome: 'Hortifruti do Zé' -> 'hortifruti-do-ze'."""
    t = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    t = re.sub(r"[^a-zA-Z0-9]+", "-", t).strip("-").lower()
    return t or "fornecedor"


# ---------- guarda de admin ----------

def _admin(request: Request):
    """Retorna a conta logada SE for admin; senao None."""
    # Memoizacao por requisicao: _admin e' chamado varias vezes por pagina do admin.
    if hasattr(request.state, "_admin_cache"):
        return request.state._admin_cache
    cid = request.session.get("conta_id")
    if not cid:
        request.state._admin_cache = None
        return None
    pool = get_pool()
    with pool.connection() as c:
        row = c.execute(
            "select id, nome, is_admin from contas where id = %s", (cid,)
        ).fetchone()
    if not row or not row[2]:
        request.state._admin_cache = None
        return None
    request.state._admin_cache = row
    return row


_NEGADO = HTMLResponse("<h1>404</h1>", status_code=404)


# ---------- templates ----------

_ADMIN_BASE = """<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Admin - Zaq</title>
""" + _tema.FONTES + """<style>
""" + _tema.variaveis() + """
body{margin:0;font-family:var(--body);background:var(--bg);
 color:var(--txt);display:flex;flex-direction:column;align-items:center}
.topo{width:100%;max-width:1000px;display:flex;flex-wrap:wrap;gap:.6rem;justify-content:space-between;
 align-items:center;padding:1.2rem 1rem;box-sizing:border-box}
.adm-nav{display:inline-flex;flex-wrap:wrap;gap:.25rem;background:var(--card-2);padding:.25rem;border-radius:14px;border:1px solid var(--borda)}
.adm-nav a{color:var(--txt-mut);text-decoration:none;margin:0;padding:.35rem .7rem;border-radius:10px;font-size:.85rem;transition:background .18s,color .18s}
.adm-nav a:hover{color:var(--txt)}
.adm-nav a.on{background:var(--verde);color:var(--sobre-verde);font-weight:500}
.logo{font-weight:600}.logo span{color:var(--ambar)}
.wrap{width:100%;max-width:1000px;padding:0 1rem 3rem;box-sizing:border-box}
.cards{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:12px;margin:1rem 0}
.metric{background:var(--card);border:1px solid var(--borda);border-radius:10px;padding:1rem}
.metric span{display:block;font-size:.8rem;color:var(--txt-mut);margin-bottom:.3rem}
.metric b{font-size:1.5rem;font-weight:500}
.card{background:var(--card);border:1px solid var(--borda);border-radius:12px;padding:1.3rem;margin:1rem 0}
h1{font-size:1.3rem;font-weight:500}h2{font-size:1.05rem;font-weight:500;margin:0 0 .6rem}
table{width:100%;border-collapse:collapse;font-size:.9rem}
td,th{padding:.5rem .4rem;border-bottom:1px solid var(--borda);text-align:left}
input,select{padding:.5rem .6rem;border-radius:7px;border:1px solid #333;background:var(--bg);color:var(--txt);font-size:.9rem}
button{padding:.45rem .8rem;border:0;border-radius:7px;background:var(--verde);color:var(--sobre-verde);cursor:pointer;font-size:.85rem}
button:hover{background:var(--verde-hover)}
button.warn{background:#8a5a1c}button.warn:hover{background:#a86c22}
button.danger{background:#6e2b2b}button.danger:hover{background:#8a3636}
.tag{display:inline-block;padding:.1rem .5rem;border-radius:999px;font-size:.75rem;border:1px solid #444;color:#bbb}
.tag.ativa{border-color:var(--verde);color:var(--verde-claro)}.tag.trial{border-color:#3a78c2;color:#7ab0e8}
.tag.suspensa,.tag.inadimplente{border-color:#8a3636;color:#e89a9a}
.mut{color:var(--txt-mut);font-size:.85rem}
form.inline{display:inline;margin:0}
</style></head><body>
<div class="topo"><span class="logo">Zaq <span>· admin</span></span>
<nav class="adm-nav"><a href="/admin">Contas</a><a href="/admin/funil">Funil</a><a href="/admin/custos">Custos</a><a href="/admin/qr">QR notas</a><a href="/admin/precos">Preços</a><a href="/admin/categorias">Categorias</a><a href="/admin/banco">Banco</a><a href="/admin/comunicacao">Comunicacao</a><a href="/admin/pesquisas">Pesquisas</a><a href="/painel">Meu painel</a><a href="/sair">Sair</a></nav></div>
<div class="wrap">{% block conteudo %}{% endblock %}</div>
<script>(function(){var p=location.pathname;document.querySelectorAll(".adm-nav a").forEach(function(a){var h=a.getAttribute("href");if(h===p||(h!=="/admin"&&h!=="/painel"&&h!=="/sair"&&p.indexOf(h)===0))a.classList.add("on");});})();</script>
</body></html>"""

_ADMIN_HOME = """{% extends "abase" %}{% block conteudo %}
<h1>Torre de controle</h1>
{% if leads_forn and leads_forn > 0 %}<div class="metric" style="border-color:#b8860b;color:#e8cf9f">👨‍🌾 {{ leads_forn }} conta(s) marcaram interesse no <b>módulo Fornecedor</b> no cadastro — veja a tag "quer Fornecedor" na lista.</div>{% endif %}
{% if aviso %}<div class="metric" style="border-color:var(--verde);color:#9fe8c9">{{ aviso }}</div>{% endif %}
<div class="cards">
<div class="metric"><span>Contas</span><b>{{ resumo.total }}</b></div>
<div class="metric"><span>Em trial</span><b>{{ resumo.trial }}</b></div>
<div class="metric"><span>Ativas</span><b>{{ resumo.ativa }}</b></div>
<div class="metric"><span>Vencendo em 7 dias</span><b>{{ resumo.vencendo }}</b></div>
<div class="metric"><span>Receita mensal estimada</span><b>{{ brl(resumo.mrr) }}</b></div>
</div>

<div class="card"><h2>Planos e preços</h2>
<form method="post" action="/admin/beta" style="display:flex;justify-content:space-between;align-items:center;gap:1rem;background:{% if beta_gratis %}#14251c{% else %}var(--card){% endif %};border:1px solid {% if beta_gratis %}var(--verde)33{% else %}var(--borda){% endif %};border-radius:10px;padding:.8rem 1rem;margin-bottom:1rem">
  <div><div style="font-weight:600;color:{% if beta_gratis %}var(--verde-claro){% else %}#b4b2a9{% endif %}">Modo beta — tudo grátis {% if beta_gratis %}(LIGADO){% else %}(desligado){% endif %}</div>
  <div class="mut" style="font-size:.78rem;margin-top:.2rem">{% if beta_gratis %}Quem se cadastra vê "Grátis (beta)" e não é cobrado. Os preços abaixo ficam guardados.{% else %}Os preços abaixo estão VALENDO — clientes veem o valor no cadastro.{% endif %}</div></div>
  <button name="acao" value="{% if beta_gratis %}off{% else %}on{% endif %}" style="padding:.5rem 1rem;background:{% if beta_gratis %}#333{% else %}var(--verde){% endif %};border:0;color:#fff;border-radius:6px;font-weight:600;cursor:pointer">{% if beta_gratis %}Desligar beta{% else %}Ligar beta{% endif %}</button>
</form>
<table style="width:100%;font-size:.86rem">
  <tr class="mut" style="font-size:.74rem;text-align:left"><td style="padding:.3rem 0">Plano</td><td>Tipo</td><td style="text-align:right">Preço/mês</td><td style="text-align:center">Ativo</td><td></td></tr>
  {% for p in planos_admin %}
  <form method="post" action="/admin/plano/{{ p.codigo }}">
  <tr style="border-top:1px solid var(--card-2)">
    <td style="padding:.5rem 0;color:var(--txt)">{{ p.nome }}</td>
    <td class="mut">{{ p.tipo_conta }}</td>
    <td style="text-align:right">
      {% if p.codigo == 'zaq_cesta' %}<span style="color:var(--verde-claro);font-size:.8rem">sempre grátis</span>
      {% else %}<span style="display:inline-flex;align-items:center;gap:3px;justify-content:flex-end">
        <span class="mut" style="font-size:.75rem">R$</span>
        <input name="preco" value="{{ (p.preco_base_centavos/100)|n2 }}" style="width:64px;text-align:right;padding:.25rem .4rem;background:var(--bg);border:1px solid var(--borda);color:#f4f4f4;border-radius:5px">
      </span>{% endif %}
    </td>
    <td style="text-align:center">
      {% if p.codigo != 'zaq_cesta' %}<input type="checkbox" name="ativo" value="1" {% if p.ativo %}checked{% endif %}>{% else %}<span style="color:var(--verde-claro)">●</span>{% endif %}
    </td>
    <td style="text-align:right">{% if p.codigo != 'zaq_cesta' %}<button style="padding:.25rem .7rem;background:var(--verde);border:0;color:var(--sobre-verde);border-radius:5px;font-size:.76rem;cursor:pointer">salvar</button>{% else %}<span class="mut">—</span>{% endif %}</td>
  </tr>
  </form>
  {% endfor %}
</table>

<div style="font-weight:600;color:#b4b2a9;font-size:.82rem;margin:1rem 0 .4rem">Módulos (add-ons do plano PJ)</div>
<table style="width:100%;font-size:.86rem">
  <tr class="mut" style="font-size:.74rem;text-align:left"><td style="padding:.3rem 0">Módulo</td><td style="text-align:right">Mensalidade</td><td style="text-align:center">Ativo</td><td></td></tr>
  {% for m in modulos_admin %}
  <form method="post" action="/admin/modulo/{{ m.codigo }}">
  <tr style="border-top:1px solid var(--card-2)">
    <td style="padding:.5rem 0;color:var(--txt)">{{ m.nome }}{% if m.codigo == 'fornecedor' %} <span class="mut" style="font-size:.72rem">+ comissão % das vendas</span>{% endif %}</td>
    <td style="text-align:right"><span style="display:inline-flex;align-items:center;gap:3px;justify-content:flex-end">
      <span class="mut" style="font-size:.75rem">R$</span>
      <input name="preco" value="{{ (m.preco_centavos/100)|n2 }}" style="width:64px;text-align:right;padding:.25rem .4rem;background:var(--bg);border:1px solid var(--borda);color:#f4f4f4;border-radius:5px"></span></td>
    <td style="text-align:center"><input type="checkbox" name="ativo" value="1" {% if m.ativo %}checked{% endif %}></td>
    <td style="text-align:right"><button style="padding:.25rem .7rem;background:var(--verde);border:0;color:var(--sobre-verde);border-radius:5px;font-size:.76rem;cursor:pointer">salvar</button></td>
  </tr>
  </form>
  {% endfor %}
</table>
<div class="mut" style="font-size:.72rem;margin-top:.5rem">Os módulos são ligados por conta no detalhe de cada conta (Empresa = plano PJ; Fornecedor = ativar fornecedor). Aqui você só define o preço.</div>
<div class="mut" style="font-size:.76rem;margin-top:.7rem">{% if beta_gratis %}Com o beta ligado, o preço nunca aparece pro cliente no cadastro. Ao desligar, estes preços valem na hora — sem mexer em código.{% else %}⚠️ Beta desligado: estes preços estão ativos no cadastro.{% endif %}</div>
</div>

<div class="card"><h2>Contas</h2>
<form method="post" action="/admin/precos/importar" style="margin-bottom:.8rem; display:inline">
<button title="Lê os cupons de mercado e atualiza o banco de preços do comparador">↻ Atualizar banco de preços</button></form>
<form method="get" action="/admin" style="margin-bottom:.8rem">
<input name="busca" placeholder="buscar nome, e-mail ou documento" value="{{ busca or '' }}" style="width:60%">
<button>Buscar</button></form>
<div style="max-height:65vh;overflow-y:auto;padding-right:.3rem;margin-top:.4rem">
{% for c in contas %}
<div style="background:var(--bg);border:1px solid var(--borda);border-radius:10px;padding:.7rem .9rem;margin-bottom:.6rem">
  <div style="display:flex;justify-content:space-between;align-items:center;gap:.6rem;flex-wrap:wrap">
    <div style="min-width:0"><span class="mut">#{{ c.id }}</span> <b>{{ c.nome }}</b> <span class="mut" style="font-size:.8rem">{{ c.email or '-' }}</span></div>
    <div style="display:flex;gap:.35rem;align-items:center;flex-wrap:wrap">
      <span class="tag {{ c.status }}">{{ c.status }}</span>
      <span class="tag">{{ c.plano or '-' }}</span>
      {% if c.interesse_modulos and 'fornecedor' in c.interesse_modulos %}<span class="tag" style="background:#3a2c08;color:#e8cf9f" title="marcou interesse no cadastro">quer Fornecedor</span>{% endif %}
      {% if c.qtd_cestas and c.qtd_cestas > 0 %}<span class="tag ativa" title="assinatura de cesta">cesta {{ c.qtd_cestas }}</span>{% endif %}
      <span class="mut" style="font-size:.78rem">vence {{ c.vencimento.strftime('%d/%m/%y') if c.vencimento else '-' }}</span>
      <button type="button" onclick="admToggle({{ c.id }})" id="tgl-{{ c.id }}" style="padding:.3rem .7rem;font-size:.78rem;background:transparent;border:1px solid #333;color:var(--verde-claro)">detalhes</button>
    </div>
  </div>
  <div id="det-{{ c.id }}" style="display:none;margin-top:.8rem">
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.6rem">
      <div style="background:var(--card);border-radius:8px;padding:.6rem .7rem">
        <div class="mut" style="font-size:.72rem;text-transform:uppercase;margin-bottom:.4rem">Limites / dia</div>
        <form class="inline" method="post" action="/admin/conta/{{ c.id }}/limites" style="display:flex;gap:4px;align-items:center">
          <input type="number" name="msg" value="{{ c.limite_mensagens_dia }}" style="width:52px" title="mensagens/dia"><span>/</span>
          <input type="number" name="cup" value="{{ c.limite_cupons_dia }}" style="width:48px" title="cupons/dia">
          <button style="padding:.35rem .55rem">ok</button>
        </form>
      </div>
      <div style="background:var(--card);border-radius:8px;padding:.6rem .7rem">
        <div class="mut" style="font-size:.72rem;text-transform:uppercase;margin-bottom:.4rem">Uso hoje</div>
        <div style="font-size:.88rem">{{ c.msg_hoje }}/{{ c.limite_mensagens_dia }} msg<br>{{ c.cup_hoje }}/{{ c.limite_cupons_dia }} cup</div>
      </div>
      <div style="background:var(--card);border-radius:8px;padding:.6rem .7rem">
        <div class="mut" style="font-size:.72rem;text-transform:uppercase;margin-bottom:.4rem">Uso mes</div>
        <div style="font-size:.88rem">{{ c.msg_mes }} msg<br>{{ c.cup_mes }} cup</div>
      </div>
    </div>
    {% if c.documento or c.razao_social or c.nome_fantasia or c.email_empresa or c.telefone %}
    <div style="background:var(--card);border-radius:8px;padding:.7rem .8rem;margin-top:.6rem">
      <div class="mut" style="font-size:.72rem;text-transform:uppercase;margin-bottom:.5rem">{% if c.tipo == 'pj' %}🏢 Dados da empresa{% else %}Dados cadastrais{% endif %}</div>
      <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:.5rem .9rem;font-size:.82rem">
        {% if c.documento %}<div><div class="mut" style="font-size:.68rem">{% if c.tipo == 'pj' %}CNPJ{% else %}CPF/CNPJ{% endif %}</div>{{ c.documento }}</div>{% endif %}
        {% if c.razao_social %}<div><div class="mut" style="font-size:.68rem">Razao social</div>{{ c.razao_social }}</div>{% endif %}
        {% if c.nome_fantasia %}<div><div class="mut" style="font-size:.68rem">Nome fantasia</div>{{ c.nome_fantasia }}</div>{% endif %}
        {% if c.cidade or c.uf %}<div><div class="mut" style="font-size:.68rem">Cidade / UF</div>{{ c.cidade }}{% if c.uf %} / {{ c.uf }}{% endif %}</div>{% endif %}
        {% if c.bairro %}<div><div class="mut" style="font-size:.68rem">Bairro</div>{{ c.bairro }}</div>{% endif %}
        {% if c.email_empresa %}<div><div class="mut" style="font-size:.68rem">E-mail</div>{{ c.email_empresa }}</div>{% endif %}
        {% if c.telefone %}<div><div class="mut" style="font-size:.68rem">Telefone</div>{{ c.telefone }}</div>{% endif %}
      </div>
      {% if c.cep or c.endereco %}
      <div style="border-top:1px solid #262628;margin-top:.6rem;padding-top:.6rem;display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:.5rem .9rem;font-size:.82rem">
        {% if c.cep %}<div><div class="mut" style="font-size:.68rem">CEP</div>{{ c.cep }}</div>{% endif %}
        {% if c.endereco %}<div style="grid-column:span 2"><div class="mut" style="font-size:.68rem">Endereco</div>{{ c.endereco }}</div>{% endif %}
      </div>
      {% endif %}
    </div>
    {% endif %}
    <div style="display:flex;gap:.4rem;align-items:center;flex-wrap:wrap;margin-top:.7rem">
      <form class="inline" method="post" action="/admin/conta/{{ c.id }}/ativar"><button>+30 dias</button></form>
      <form class="inline" method="post" action="/admin/conta/{{ c.id }}/suspender"><button class="warn">Suspender</button></form>
      {% if c.tipo == 'pj' %}
        {% if c.eh_fornecedor %}
          <span class="tag ativa" title="slug: {{ c.fornecedor_slug }}">fornecedor ok</span>
          <form class="inline" method="post" action="/admin/conta/{{ c.id }}/remover-fornecedor"><button class="warn" style="padding:.35rem .6rem;font-size:.78rem">remover forn.</button></form>
          <form class="inline" method="post" action="/admin/conta/{{ c.id }}/repasse" style="display:flex;gap:4px;align-items:center;flex-wrap:wrap"><input type="number" name="valor" step="0.01" placeholder="R$ repasse" style="width:92px" required><input name="obs" placeholder="obs (ex: sem 01-07)" style="width:126px"><button style="padding:.35rem .6rem;font-size:.78rem">registrar repasse</button></form>
        {% else %}
          <form class="inline" method="post" action="/admin/conta/{{ c.id }}/fornecedor" style="display:flex;gap:4px;align-items:center;flex-wrap:wrap">
            <select name="nicho_id" style="width:100px" required title="nicho">{% for n in nichos %}<option value="{{ n.id }}">{{ n.nome }}</option>{% endfor %}</select>
            <input type="number" name="comissao" step="0.01" placeholder="%" style="width:52px" title="comissao %" required>
            <input name="wallet" placeholder="wallet Asaas" style="width:120px" title="Asaas wallet id">
            <button style="padding:.4rem .7rem;font-size:.8rem">tornar fornecedor</button>
          </form>
        {% endif %}
      {% endif %}
    </div>
  </div>
</div>
{% endfor %}
</div>
<script>
function admToggle(id){var d=document.getElementById('det-'+id),b=document.getElementById('tgl-'+id);if(!d)return;var open=(d.style.display!=='none');d.style.display=open?'none':'block';if(b)b.textContent=open?'detalhes':'fechar';}
function admCep(id){var c=document.getElementById('cep-'+id);var d=(c.value||'').replace(/[^0-9]/g,'');if(d.length!==8)return;fetch('/api/cep/'+d).then(function(r){return r.json();}).then(function(j){if(!j||!j.ok)return;var e=document.getElementById('end-'+id);if(e&&!e.value.trim()){var s=j.rua||'';if(j.bairro)s+=(s?', ':'')+j.bairro;if(j.cidade)s+=(s?' - ':'')+j.cidade+(j.uf?'/'+j.uf:'');e.value=s;}}).catch(function(){});}
</script></div>

<div class="card"><h2>Auditoria recente</h2>
<div style="max-height:320px;overflow:auto"><table><tr><th>Quando</th><th>Conta</th><th>Evento</th><th>Detalhe</th></tr>
{% for e in eventos %}<tr>
<td class="mut">{{ e.criado_em.strftime('%d/%m %H:%M') }}</td>
<td>{{ e.conta_id }}</td><td>{{ e.tipo }}</td><td class="mut">{{ e.detalhe }}</td></tr>{% endfor %}
</table></div></div>
{% endblock %}"""

_ADMIN_QR = """{% extends "abase" %}{% block conteudo %}
<h1>Leitura de QR code das notas</h1>
{% if aviso %}<div class="card" style="border-color:var(--verde)">{{ aviso }}</div>{% endif %}
<div class="cards">
<div class="metric"><span>Fotos/PDFs recebidos</span><b>{{ resumo.total }}</b></div>
<div class="metric"><span>QR lido com sucesso</span><b style="color:var(--verde-claro)">{{ resumo.leu }}</b></div>
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

_ADMIN_CATEGORIAS = """{% extends "abase" %}{% block conteudo %}
<h1>Categorias — uso real</h1>
<p class="mut">Visão de TODAS as contas. Use pra decidir o que criar ou remover na lista oficial
(finance/models.py). Muita coisa em <b>"Outros"</b> = falta categoria. Categoria com <b>0 uso</b> = candidata a remover.</p>
<div class="cards">
<div class="metric"><span>Lançamentos de despesa</span><b>{{ d.total_despesa }}</b></div>
<div class="metric" style="{% if d.pct_outros_despesa >= 15 %}border-color:#8a5a1c{% endif %}">
  <span>% em "Outros" (despesa)</span><b style="{% if d.pct_outros_despesa >= 15 %}color:var(--ambar){% endif %}">{{ d.pct_outros_despesa }}%</b></div>
<div class="metric"><span>Lançamentos de receita</span><b>{{ d.total_receita }}</b></div>
<div class="metric" style="{% if d.pct_outros_receita >= 15 %}border-color:#8a5a1c{% endif %}">
  <span>% em "Outros" (receita)</span><b style="{% if d.pct_outros_receita >= 15 %}color:var(--ambar){% endif %}">{{ d.pct_outros_receita }}%</b></div>
</div>

<div class="card"><h2>Despesa</h2>
<table><tr><th>Categoria</th><th>Lançamentos</th><th>%</th><th>Total</th></tr>
{% for l in d.despesa %}<tr>
<td>{{ l.categoria }}{% if l.qtd == 0 %} <span class="tag suspensa">sem uso</span>{% elif l.categoria == 'Outros' and l.pct >= 15 %} <span class="tag" style="border-color:#8a5a1c;color:var(--ambar)">revisar</span>{% endif %}</td>
<td>{{ l.qtd }}</td><td class="mut">{{ l.pct }}%</td><td>{{ brl(l.total) }}</td>
</tr>{% endfor %}
</table></div>

<div class="card"><h2>Receita</h2>
<table><tr><th>Categoria</th><th>Lançamentos</th><th>%</th><th>Total</th></tr>
{% for l in d.receita %}<tr>
<td>{{ l.categoria }}{% if l.qtd == 0 %} <span class="tag suspensa">sem uso</span>{% elif l.categoria == 'Outros' and l.pct >= 15 %} <span class="tag" style="border-color:#8a5a1c;color:var(--ambar)">revisar</span>{% endif %}</td>
<td>{{ l.qtd }}</td><td class="mut">{{ l.pct }}%</td><td>{{ brl(l.total) }}</td>
</tr>{% endfor %}
</table></div>

<div class="card"><h2>Raio-x do consumo — itens de cupom (departamentos)</h2>
<p class="mut">Nível de <b>item de cupom</b> (alimenta o banco de preços), não de lançamento.
Mostra <b>apenas a lista branca</b> (Mercado, Saúde, Restaurante, Pet) que aparece no raio-x do cliente.
Editar a lista em <b>finance/models.py: DEPARTAMENTOS_RAIOX</b>.</p>
<table><tr><th>Departamento</th><th>Itens</th><th>Cupons</th><th>Total</th></tr>
{% for l in raiox %}<tr>
<td>{{ l.departamento }}</td>
<td>{{ l.itens }}</td><td class="mut">{{ l.cupons }}</td><td>{{ brl(l.total) }}</td>
</tr>{% endfor %}
</table></div>
{% endblock %}"""

_ADMIN_BANCO = """{% extends "abase" %}{% block conteudo %}
<h1>Banco de preços</h1>
<p class="mut">Coleta colaborativa dos cupons. O mesmo preço no mesmo dia/loja não
vira várias linhas pro cliente — aqui aparece agregado, com <b>"visto N vezes"</b>
(quanto mais vezes, mais confiável a mediana). Só leitura.</p>
<div class="cards">
<div class="metric"><span>Cupons com QR lido</span><b>{{ d.tot_cupons }}</b></div>
<div class="metric"><span>Produtos distintos</span><b>{{ d.tot_produtos }}</b></div>
<div class="metric"><span>Lojas</span><b>{{ d.tot_lojas }}</b></div>
<div class="metric"><span>Observações de preço</span><b>{{ d.tot_observacoes }}</b></div>
</div>

<div class="card"><h2>Preços mais confirmados</h2>
<table><tr><th>Loja</th><th>Produto</th><th>Preço</th><th>Dia</th><th>Visto</th></tr>
{% for m in d.mais_confirmados %}<tr>
<td>{{ m.loja }}</td><td>{{ m.produto }}</td><td>{{ brl(m.preco) }}</td>
<td class="mut">{{ m.data.strftime('%d/%m/%y') if m.data else '-' }}</td>
<td>{{ m.vezes }}×</td>
</tr>{% endfor %}
{% if not d.mais_confirmados %}<tr><td colspan="5" class="mut">Sem preços coletados ainda.</td></tr>{% endif %}
</table></div>

<div class="card"><h2>Top produtos (mais coletados)</h2>
<table><tr><th>Produto</th><th>Observações</th></tr>
{% for p in d.top_produtos %}<tr><td>{{ p.produto }}</td><td>{{ p.obs }}</td></tr>{% endfor %}
</table></div>

<div class="card"><h2>Top lojas</h2>
<table><tr><th>Loja</th><th>Observações</th><th>Produtos</th></tr>
{% for l in d.top_lojas %}<tr><td>{{ l.loja }}</td><td>{{ l.obs }}</td><td class="mut">{{ l.produtos }}</td></tr>{% endfor %}
</table></div>
{% endblock %}"""

_ADMIN_FUNIL = """{% extends "abase" %}{% block conteudo %}
<h2>Funil de aquisição</h2>
{% if not f.tem_tabela_leads %}
<div class="metric" style="border-color:var(--ambar)">A tabela de leads ainda não existe (migração 023 não aplicada).
Os números do funil aparecem assim que o fluxo de cliente novo entrar no ar. Abaixo, a saúde das contas já funciona.</div>
{% else %}
<div class="cards">
  <div class="metric"><span>Visitantes</span><b style="color:var(--verde-claro)">{{ f.geral.visitantes }}</b></div>
  <div class="metric"><span>Testaram</span><b style="color:#7ab0e8">{{ f.geral.testaram }} ({{ f.geral.pct_testou }}%)</b></div>
  <div class="metric"><span>Cadastraram</span><b style="color:#a8d96f">{{ f.geral.cadastraram }} ({{ f.geral.pct_cadastrou }}%)</b></div>
  <div class="metric"><span>Pagaram</span><b style="color:var(--ambar)">{{ f.geral.pagaram }} ({{ f.geral.pct_pagou }}%)</b></div>
</div>

<div class="card"><h2>Por canal</h2>
<table><tr><th>Canal</th><th>Visitantes</th><th>Testaram</th><th>Cadastraram</th><th>Pagaram</th></tr>
{% for c in f.por_canal %}<tr><td>{{ c.canal }}</td><td>{{ c.visitantes }}</td><td>{{ c.testaram }}</td><td>{{ c.cadastraram }}</td><td>{{ c.pagaram }}</td></tr>
{% else %}<tr><td colspan="5" class="mut">Sem leads ainda.</td></tr>
{% endfor %}</table></div>
{% endif %}

<div class="card"><h2>Saúde das contas</h2>
<table><tr><th>Status</th><th>Qtd</th></tr>
{% for s in f.contas_status %}<tr><td>{{ s.status }}</td><td>{{ s.qtd }}</td></tr>
{% endfor %}</table></div>

<div class="card"><h2>Trials vencendo (próximos 3 dias)</h2>
<table><tr><th>#</th><th>Nome</th><th>Plano</th><th>Vence</th></tr>
{% for t in f.trials_vencendo %}<tr><td>{{ t.id }}</td><td>{{ t.nome }}</td><td>{{ t.plano or '-' }}</td><td>{{ t.vencimento }}</td></tr>
{% else %}<tr><td colspan="4" class="mut">Nenhum trial vencendo nos próximos 3 dias.</td></tr>
{% endfor %}</table></div>
{% endblock %}"""

_ADMIN_CUSTOS = """{% extends "abase" %}{% block conteudo %}
<h2>Custos de API</h2>
{% if not k.tem_tabela %}
<div class="metric" style="border-color:var(--ambar)">A tabela uso_api ainda não existe (migração 024 não aplicada).</div>
{% endif %}

<form method="get" action="/admin/custos" style="margin:1rem 0;display:flex;gap:.5rem;align-items:center;flex-wrap:wrap">
<label>De <input type="date" name="desde" value="{{ desde or '' }}" style="padding:.4rem .5rem;border-radius:6px;border:1px solid #333;background:var(--bg);color:var(--txt)"></label>
<label>Até <input type="date" name="ate" value="{{ ate or '' }}" style="padding:.4rem .5rem;border-radius:6px;border:1px solid #333;background:var(--bg);color:var(--txt)"></label>
<button style="padding:.45rem .8rem;border:0;border-radius:7px;background:var(--verde);color:var(--sobre-verde);cursor:pointer">Filtrar</button>
<span class="mut">(vazio = mês corrente)</span>
</form>

<div class="cards">
<div class="metric"><span>Custo total</span><b style="color:var(--ambar)">R$ {{ k.geral.custo_total_reais }}</b></div>
<div class="metric"><span>Contas ativas</span><b style="color:#7ab0e8">{{ k.geral.contas_ativas }}</b></div>
<div class="metric"><span>Custo médio/cliente</span><b style="color:#a8d96f">R$ {{ k.geral.custo_medio_por_conta_reais }}</b></div>
<div class="metric"><span>Chamadas</span><b style="color:var(--verde-claro)">{{ k.geral.chamadas }}</b></div>
</div>

<div class="card"><h3>Custos por tipo</h3>
<table><tr><th>Tipo</th><th>Qtd</th><th>Custo R$</th><th>Médio R$</th></tr>
<tr><td>📸 Fotos (cupom)</td><td>{{ k.geral.fotos }}</td><td>{{ k.geral.custo_fotos_reais }}</td><td>{{ k.geral.custo_medio_foto_reais }}</td></tr>
<tr><td>💬 Textos</td><td>{{ k.geral.textos }}</td><td>{{ k.geral.custo_textos_reais }}</td><td>{{ k.geral.custo_medio_texto_reais }}</td></tr>
</table></div>

<div class="card"><h3>Por conta (top 30)</h3>
<table><tr><th>#</th><th>Nome</th><th>Chamadas</th><th>Fotos</th><th>Custo R$</th></tr>
{% for c in k.por_conta %}<tr><td>{{ c.conta_id }}</td><td>{{ c.nome }}</td><td>{{ c.chamadas }}</td><td>{{ c.fotos }}</td><td>{{ c.custo_reais }}</td></tr>
{% else %}<tr><td colspan="5" class="mut">Sem uso no período.</td></tr>
{% endfor %}</table></div>
<p class="mut" style="margin-top:1rem">Custo estimado (Sonnet 4.6, câmbio ~5.40). A conta-degustação entra no total mas não conta como cliente na média.</p>
{% endblock %}"""

_ADMIN_PESQUISAS = """{% extends "abase" %}{% block conteudo %}
<h1>Pesquisas de fornecedores</h1>
{% if aviso %}<div class="metric" style="border-color:var(--verde);color:#9fe8c9">{{ aviso }}</div>{% endif %}

<div class="card"><h2>Respostas (últimas 200)</h2>
<table><tr><th>ID</th><th>Segmento</th><th>Criado em</th><th>Respostas resumidas</th></tr>
{% for p in pesquisas %}<tr>
<td>{{ p.id }}</td>
<td>{{ p.segmento }}</td>
<td class="mut">{{ p.criado_em.strftime('%d/%m/%Y %H:%M') }}</td>
<td class="mut" style="max-width:400px;word-break:break-word">
  {% for k, v in p.respostas.items() %}
  {{ k }}: {{ v if not v is string else v[:50] }}{% if not loop.last %}<br>{% endif %}
  {% endfor %}
</td></tr>
{% else %}<tr><td colspan="4" class="mut">Sem pesquisas ainda.</td></tr>
{% endfor %}</table></div>
{% endblock %}"""

_ADMIN_COMUNICACAO = """{% extends "abase" %}{% block conteudo %}
<h1>Comunicacao</h1>
{% if aviso %}<div class="card" style="border-color:var(--verde)">{{ aviso }}</div>{% endif %}

<div class="card"><h2>Alertas do sistema — pra onde vao</h2>
<p class="mut">Avisos de provedor pago quebrado (Anthropic, Twilio, Asaas...) e do monitor diario de saldo. O cliente nunca ve isso: ele so' recebe "instabilidade tecnica". O que valer aqui vale pra <b>todos</b> os servicos (web, cron e worker), sem redeploy.</p>
<form method="post" action="/admin/alertas" style="display:grid;gap:.7rem;margin-top:1rem">
  <label style="display:grid;gap:.25rem"><span class="mut">E-mail do admin</span>
    <input type="email" name="email" value="{{ alertas.email_banco }}" placeholder="{{ alertas.email_env or 'seu@email.com' }}" style="max-width:340px"></label>
  <label style="display:grid;gap:.25rem"><span class="mut">Telegram do admin (chat id numerico)</span>
    <input name="telegram_id" value="{{ alertas.telegram_banco }}" placeholder="{{ alertas.telegram_env or '(opcional)' }}" style="max-width:340px"></label>
  <div><button>Salvar destino dos alertas</button></div>
</form>
<form method="post" action="/admin/alertas/teste" style="margin-top:.6rem">
  <button style="background:var(--borda);border:1px solid var(--borda)">Enviar alerta de teste</button>
  <span class="mut" style="margin-left:.5rem">dispara pelo caminho real e diz, canal a canal, se saiu</span>
</form>
<table style="margin-top:1rem">
<tr><td>E-mail em uso agora</td><td>{% if alertas.email_efetivo %}<b>{{ alertas.email_efetivo }}</b> <span class="mut">({{ alertas.email_origem }})</span>{% else %}<span class="tag suspensa">nenhum</span> <span class="mut">nenhum alerta por e-mail sai daqui</span>{% endif %}</td></tr>
<tr><td>Telegram em uso agora</td><td>{% if alertas.telegram_efetivo %}<b>{{ alertas.telegram_efetivo }}</b> <span class="mut">({{ alertas.telegram_origem }})</span>{% else %}<span class="mut">nao configurado (opcional)</span>{% endif %}</td></tr>
<tr><td>Remetente SMTP</td><td>{% if alertas.remetente %}{{ alertas.remetente }}{% else %}<span class="tag suspensa">SMTP nao configurado</span> <span class="mut">sem SMTP_USER/SMTP_SENHA nao sai e-mail nenhum</span>{% endif %}</td></tr>
</table>
<p class="mut">Campo vazio = vale a variavel de ambiente do Render (ADMIN_EMAIL / ADMIN_TELEGRAM_ID), mostrada como placeholder.</p>
</div>

<h2>O que chega: cupom x comprovante (ultimos {{ split.dias }} dias)</h2>
<div class="cards">
<div class="metric"><span>Imagens recebidas</span><b>{{ split.total_imagens }}</b></div>
<div class="metric"><span>Cupom fiscal</span><b style="color:var(--verde-claro)">{{ split.cupom_fiscal }} ({{ split.pct_cupom }}%)</b></div>
<div class="metric"><span>Comprovante/outro</span><b style="color:#7ab0e8">{{ split.comprovante_ou_outro }} ({{ split.pct_outro }}%)</b></div>
</div>
<p class="mut">Cupom = o agente usou tool de cupom (checar_duplicata / registrar_itens_cupom). O resto, tratou como comprovante. Sinal pela acao, nao por chute.</p>

<div class="card"><h2>Banco de ouro - Fase B: {{ faseb.veredito }}</h2>
<table>
{% for m in faseb.metricas %}<tr>
<td>{{ m.nome }}</td>
<td class="mut">{{ m.valor }}{{ m.unidade }} / {{ m.gatilho }}{{ m.unidade }}</td>
<td style="width:38%"><div style="background:var(--borda);border-radius:6px;height:8px">
<div style="width:{{ m.pct }}%;height:8px;border-radius:6px;background:{% if m.ok %}var(--verde){% else %}#8a5a1c{% endif %}"></div></div></td>
<td style="white-space:nowrap">{% if m.ok %}<span class="tag ativa">ok</span>{% else %}<span class="mut">{{ m.pct }}%</span>{% endif %}</td>
</tr>{% endfor %}
</table>
<p class="mut">Multi-loja (ouro): {{ faseb.contexto.produtos_multiloja }} &middot; Confirmados: {{ faseb.contexto.produtos_confirmados }} &middot; Cupons com QR: {{ faseb.contexto.cupons_com_qr }} &middot; Faltam (est.): {{ faseb.contexto.cupons_faltando_estimado }}</p>
</div>

<div class="card"><h2>Leitura de QR (auditoria por classe)</h2>
<table>
<tr><td>Cupom lido (QR ok)</td><td><b style="color:var(--verde-claro)">{{ qr.classes.cupom_lido }}</b></td></tr>
<tr><td>Imagem sem QR</td><td>{{ qr.classes.cupom_sem_qr }}</td></tr>
<tr><td>PDF indefinido</td><td>{{ qr.classes.pdf_indefinido }}</td></tr>
<tr><td>Outro</td><td>{{ qr.classes.outro }}</td></tr>
</table>
<p class="mut">Assertividade de cupom: <b>{{ qr.assertividade_cupom_pct }}%</b> sobre {{ qr.cupons_fiscais }} cupons fiscais. Total auditado: {{ qr.total_leituras }}. (comprovante/outro fora da conta)</p>
</div>

<div class="card"><h2>Qualidade da foto (Foto x PDF)</h2>
<table><tr><th>Tipo</th><th>Total</th><th>QR lido</th><th>Sem QR</th><th>KB lido</th><th>KB sem</th></tr>
{% for t in qr.tipos %}<tr><td>{{ t.tipo }}</td><td>{{ t.total }}</td><td>{{ t.acertos }}</td><td>{{ t.falhas }}</td><td class="mut">{{ t.kb_medio_acerto }}</td><td class="mut">{{ t.kb_medio_falha }}</td></tr>{% endfor %}
{% if not qr.tipos %}<tr><td colspan="6" class="mut">sem leituras</td></tr>{% endif %}
</table>
<p class="mut">KB do "lido" &gt; KB do "sem" = foto pequena/comprimida derrubando o QR.</p>
</div>

<div class="card"><h2>Uso (ultimos {{ uso.dias }} dias)</h2>
<div class="cards">
<div class="metric"><span>Interacoes</span><b>{{ uso.total }}</b></div>
<div class="metric"><span>Sucesso</span><b>{{ uso.taxa_sucesso_pct }}%</b></div>
<div class="metric"><span>Custo (R$)</span><b>{{ uso.custo_reais }}</b></div>
</div>
<p class="mut">Por canal: {% for x in uso.por_canal %}{{ x.canal|e }}: {{ x.qtd }}{% if not loop.last %}, {% endif %}{% endfor %} &middot; Por tipo: {% for x in uso.por_tipo %}{{ x.tipo|e }}: {{ x.qtd }}{% if not loop.last %}, {% endif %}{% endfor %}</p>
</div>

<div class="card"><h2>Atrito (falhas e repeticoes)</h2>
<table><tr><th>id</th><th>canal</th><th>tipo</th><th>texto</th><th>sucesso</th><th>repetiu</th></tr>
{% for a in atrito %}<tr><td>{{ a.id }}</td><td class="mut">{{ a.canal|e or '-' }}</td><td class="mut">{{ a.tipo_midia|e or '-' }}</td><td class="mut">{{ (a.texto_usuario or '')[:50]|e }}</td><td>{% if a.sucesso %}sim{% else %}<span class="tag suspensa">nao</span>{% endif %}</td><td>{% if a.repetiu %}sim{% else %}-{% endif %}</td></tr>{% endfor %}
{% if not atrito %}<tr><td colspan="6" class="mut">nenhum atrito recente</td></tr>{% endif %}
</table></div>
{% endblock %}"""


_env = Environment(loader=DictLoader({"abase": _ADMIN_BASE, "ahome": _ADMIN_HOME, "aqr": _ADMIN_QR, "acat": _ADMIN_CATEGORIAS, "abanco": _ADMIN_BANCO, "afunil": _ADMIN_FUNIL, "acustos": _ADMIN_CUSTOS, "apesquisas": _ADMIN_PESQUISAS, "acomunic": _ADMIN_COMUNICACAO}),
                   autoescape=select_autoescape())
_env.globals["brl"] = brl


def _n2(v) -> str:
    """Numero em formato BR com 2 casas, SEM 'R$'. Ex.: 2020.5 -> '2.020,50'."""
    try:
        return f"{float(v):,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    except (ValueError, TypeError):
        return "0,00"


_env.filters["n2"] = _n2


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
                        ct.limite_mensagens_dia, ct.limite_cupons_dia, ct.eh_fornecedor,
                        coalesce(ct.interesse_modulos,'') as interesse_modulos,
                        (select count(*) from membros m where m.conta_id = ct.id and m.ativo) as membros,
                        coalesce((select mensagens from uso_diario u
                                  where u.conta_id = ct.id and u.dia = current_date),0) as msg_hoje,
                        coalesce((select cupons from uso_diario u
                                  where u.conta_id = ct.id and u.dia = current_date),0) as cup_hoje,
                        coalesce((select sum(mensagens) from uso_diario u
                                  where u.conta_id = ct.id
                                    and u.dia >= date_trunc('month', current_date)),0) as msg_mes,
                        coalesce((select sum(cupons) from uso_diario u
                                  where u.conta_id = ct.id
                                    and u.dia >= date_trunc('month', current_date)),0) as cup_mes,
                        coalesce((select count(*) from assinaturas a
                                  where a.cliente_id = ct.id
                                    and a.status != 'cancelada'),0) as qtd_cestas,
                        ct.endereco, ct.cep,
                        coalesce(ct.documento,'') as documento,
                        coalesce(ct.razao_social,'') as razao_social,
                        coalesce(ct.nome_fantasia,'') as nome_fantasia,
                        coalesce(ct.bairro,'') as bairro,
                        coalesce(ct.cidade,'') as cidade,
                        coalesce(ct.uf,'') as uf,
                        coalesce(ct.email_empresa,'') as email_empresa,
                        coalesce(ct.telefone,'') as telefone
                 from contas ct"""
        params: list = []
        if busca.strip():
            sql += """ where ct.nome ilike %s or ct.email ilike %s or ct.documento ilike %s"""
            termo = f"%{busca.strip()}%"; params = [termo, termo, termo]
        sql += " order by ct.id desc limit 200"
        cols = ["id", "nome", "email", "tipo", "plano", "status", "vencimento",
                "limite_mensagens_dia", "limite_cupons_dia", "eh_fornecedor",
                "interesse_modulos", "membros",
                "msg_hoje", "cup_hoje", "msg_mes", "cup_mes", "qtd_cestas", "endereco", "cep",
                "documento", "razao_social", "nome_fantasia", "bairro", "cidade", "uf",
                "email_empresa", "telefone"]
        contas = [dict(zip(cols, r)) for r in c.execute(sql, params).fetchall()]

        ecols = ["conta_id", "tipo", "detalhe", "criado_em"]
        eventos = [dict(zip(ecols, r)) for r in c.execute(
            """select conta_id, tipo, detalhe, criado_em from eventos_conta
               order by id desc limit 30""").fetchall()]

        # Busca nichos pra select de fornecedor
        ncols = ["id", "nome"]
        nichos = [dict(zip(ncols, r)) for r in c.execute(
            """select id, nome from nichos where ativo order by nome""").fetchall()]

        _pl = c.execute(
            """select codigo, nome, tipo_conta, preco_base_centavos, ativo
                 from planos order by tipo_conta, preco_base_centavos""").fetchall()
        _md = c.execute(
            """select codigo, nome, preco_centavos, ativo
                 from modulos where codigo in ('pj','fornecedor')
                 order by codigo""").fetchall()

    resumo = {"total": total, "trial": tot.get("trial", 0), "ativa": tot.get("ativa", 0),
              "vencendo": vencendo, "mrr": mrr}
    from types import SimpleNamespace
    contas = [SimpleNamespace(**c) for c in contas]
    eventos = [SimpleNamespace(**e) for e in eventos]
    nichos = [SimpleNamespace(**n) for n in nichos]
    from finance import config_app as _cfg
    from types import SimpleNamespace as _SN
    planos_admin = [_SN(codigo=r[0], nome=r[1], tipo_conta=r[2],
                        preco_base_centavos=r[3], ativo=r[4]) for r in _pl]
    modulos_admin = [_SN(codigo=r[0], nome=r[1], preco_centavos=r[2], ativo=r[3])
                     for r in _md]
    beta_gratis = _cfg.beta_gratis_ativo(get_pool())
    try:
        with get_pool().connection() as _cx:
            leads_forn = _cx.execute(
                "select count(*) from contas where interesse_modulos ilike %s",
                ("%fornecedor%",)).fetchone()[0]
    except Exception:
        leads_forn = 0
    return HTMLResponse(_env.get_template("ahome").render(
        resumo=resumo, contas=contas, eventos=eventos, nichos=nichos, busca=busca,
        planos_admin=planos_admin, modulos_admin=modulos_admin,
        beta_gratis=beta_gratis, leads_forn=leads_forn,
        aviso=request.session.pop("admin_aviso", None)))


@router.post("/admin/beta")
def admin_beta(request: Request, acao: str = Form("")):
    if _admin(request) is None:
        return _NEGADO
    from finance import config_app as _cfg
    _cfg.set_beta_gratis(get_pool(), acao == "on")
    request.session["admin_aviso"] = (
        "Modo beta LIGADO — clientes veem gratis." if acao == "on"
        else "Modo beta DESLIGADO — precos valendo no cadastro.")
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/plano/{codigo}")
def admin_plano_salvar(request: Request, codigo: str,
                       preco: str = Form(""), ativo: str = Form("")):
    if _admin(request) is None:
        return _NEGADO
    t = (preco or "").strip().replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".")
    try:
        cent = int(round(float(t) * 100))
    except (ValueError, TypeError):
        cent = 0
    with get_pool().connection() as c:
        c.execute("update planos set preco_base_centavos=%s, ativo=%s where codigo=%s",
                  (cent, bool(ativo), codigo))
        c.commit()
    request.session["admin_aviso"] = f"Plano {codigo} atualizado."
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/modulo/{codigo}")
def admin_modulo_salvar(request: Request, codigo: str,
                        preco: str = Form(""), ativo: str = Form("")):
    if _admin(request) is None:
        return _NEGADO
    t = (preco or "").strip().replace("R$", "").replace(" ", "").replace(".", "").replace(",", ".")
    try:
        cent = int(round(float(t) * 100))
    except (ValueError, TypeError):
        cent = 0
    with get_pool().connection() as c:
        c.execute("update modulos set preco_centavos=%s, ativo=%s where codigo=%s",
                  (cent, bool(ativo), codigo))
        c.commit()
    request.session["admin_aviso"] = f"Modulo {codigo} atualizado."
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/conta/{conta_id}/limites")
def admin_conta_limites(request: Request, conta_id: int,
                        msg: int = Form(...), cup: int = Form(...)):
    if _admin(request) is None:
        return _NEGADO
    msg = max(0, min(int(msg), 100000))
    cup = max(0, min(int(cup), 100000))
    pool = get_pool()
    with pool.connection() as c:
        c.execute("update contas set limite_mensagens_dia=%s, limite_cupons_dia=%s where id=%s",
                  (msg, cup, conta_id))
        c.commit()
    request.session["admin_aviso"] = f"Limites da conta {conta_id} atualizados: {msg} msg/dia, {cup} cupons/dia."
    return RedirectResponse("/admin", status_code=303)


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


@router.post("/admin/conta/{conta_id}/fornecedor")
def admin_fornecedor(request: Request, conta_id: int,
                     nicho_id: int = Form(...),
                     comissao: float = Form(...),
                     wallet: str = Form("")):
    adm = _admin(request)
    if adm is None:
        return _NEGADO
    comissao = max(0.0, min(float(comissao), 100.0))
    pool = get_pool()
    with pool.connection() as c:
        # Pega o nome da conta pra gerar o slug
        conta = c.execute("select nome, tipo from contas where id=%s", (conta_id,)).fetchone()
        if not conta:
            request.session["admin_aviso"] = f"Conta {conta_id} não encontrada."
            return RedirectResponse("/admin", status_code=303)
        # Verifica se é PJ
        if conta[1] != 'pj':
            request.session["admin_aviso"] = f"Apenas contas PJ podem ser fornecedores."
            return RedirectResponse("/admin", status_code=303)
        # Gera slug a partir do nome usando _slug()
        base = _slug(conta[0])
        # Garante slug único (se já existe, sufixa com id)
        existe = c.execute("select 1 from contas where fornecedor_slug=%s and id<>%s",
                          (base, conta_id)).fetchone()
        slug = base if not existe else f"{base}-{conta_id}"
        # Atualiza a conta
        c.execute("""update contas set eh_fornecedor=true, nicho_id=%s, comissao_pct=%s,
                     asaas_wallet_id=%s, fornecedor_slug=%s where id=%s""",
                  (nicho_id, comissao, wallet or None, slug, conta_id))
        c.commit()
    ct.registrar_evento(pool, conta_id, "admin_tornou_fornecedor",
                       f"nicho={nicho_id}, comissao={comissao}%, wallet={wallet or 'vazio'}, slug={slug}")
    request.session["admin_aviso"] = f"Conta {conta_id} agora é fornecedor (slug: {slug})."
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/conta/{conta_id}/remover-fornecedor")
def admin_remover_fornecedor(request: Request, conta_id: int):
    adm = _admin(request)
    if adm is None:
        return _NEGADO
    pool = get_pool()
    with pool.connection() as c:
        c.execute("""update contas set eh_fornecedor=false where id=%s""", (conta_id,))
        c.commit()
    ct.registrar_evento(pool, conta_id, "admin_removeu_fornecedor", f"por admin {adm[0]}")
    request.session["admin_aviso"] = f"Conta {conta_id} não é mais fornecedor."
    return RedirectResponse("/admin", status_code=303)


@router.post("/admin/conta/{conta_id}/repasse")
def admin_registrar_repasse(request: Request, conta_id: int,
                            valor: float = Form(...), obs: str = Form("")):
    adm = _admin(request)
    if adm is None:
        return _NEGADO
    from finance import financeiro_forn as fin
    try:
        cent = int(round(float(valor) * 100))
    except Exception:
        cent = 0
    ok = fin.registrar_repasse(get_pool(), conta_id, cent, obs, adm[0])
    if ok:
        ct.registrar_evento(get_pool(), conta_id, "admin_repasse",
                            f"valor={valor}, obs={obs}, por admin {adm[0]}")
        request.session["admin_aviso"] = f"Repasse de R$ {valor} registrado (conta {conta_id})."
    else:
        request.session["admin_aviso"] = "Valor de repasse invalido."
    return RedirectResponse("/admin", status_code=303)


@router.get("/admin/banco", response_class=HTMLResponse)
def admin_banco(request: Request):
    if not _admin(request):
        return _NEGADO
    from finance.estatisticas import estatisticas_precos
    d = estatisticas_precos(get_pool())
    return HTMLResponse(_env.get_template("abanco").render(
        d=d, aviso=request.session.pop("admin_aviso", None)))


@router.get("/admin/categorias", response_class=HTMLResponse)
def admin_categorias(request: Request):
    if not _admin(request):
        return _NEGADO
    from finance.estatisticas import estatisticas_categorias, estatisticas_raiox
    pool = get_pool()
    dados = estatisticas_categorias(pool)
    raiox = estatisticas_raiox(pool)
    return HTMLResponse(_env.get_template("acat").render(
        d=dados, raiox=raiox, aviso=request.session.pop("admin_aviso", None)))


@router.get("/admin/pesquisas", response_class=HTMLResponse)
def admin_pesquisas(request: Request):
    """Lista respostas de pesquisas de fornecedores (Zaq Fornecedor fase 1)."""
    if not _admin(request):
        return _NEGADO
    pool = get_pool()
    with pool.connection() as c:
        rows = c.execute("""select id, segmento, respostas, criado_em
                            from pesquisa_fornecedor order by id desc limit 200""").fetchall()
    import json
    pesquisas = []
    for r in rows:
        pesquisas.append({
            "id": r[0],
            "segmento": r[1],
            "respostas": r[2] if isinstance(r[2], dict) else (json.loads(r[2]) if r[2] else {}),
            "criado_em": r[3]
        })
    return HTMLResponse(_env.get_template("apesquisas").render(
        pesquisas=pesquisas, aviso=request.session.pop("admin_aviso", None)))


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


@router.post("/admin/conta/{conta_id}/endereco")
def admin_conta_endereco(request: Request, conta_id: int,
                         cep: str = Form(""), endereco: str = Form("")):
    """Edita endereço/CEP da conta (pra entrega da cesta)."""
    adm = _admin(request)
    if adm is None:
        return _NEGADO
    pool = get_pool()
    cep_val = (cep or "").strip() or None
    endereco_val = (endereco or "").strip() or None
    with pool.connection() as c:
        c.execute(
            "update contas set cep=%s, endereco=%s where id=%s",
            (cep_val, endereco_val, conta_id),
        )
        c.commit()
    ct.registrar_evento(pool, conta_id, "admin_atualizou_endereco",
                       f"CEP={cep_val}, endereço={endereco_val}")
    request.session["admin_aviso"] = f"Endereço da conta {conta_id} atualizado."
    return RedirectResponse("/admin", status_code=303)


@router.get("/admin/funil", response_class=HTMLResponse)
def admin_funil(request: Request):
    if not _admin(request):
        return _NEGADO
    pool = get_pool()
    f = estatisticas_funil(pool)
    return HTMLResponse(_env.get_template("afunil").render(
        f=f, aviso=request.session.pop("admin_aviso", None)))


@router.get("/admin/custos", response_class=HTMLResponse)
def admin_custos(request: Request, desde: str = "", ate: str = ""):
    if _admin(request) is None:
        return _NEGADO
    pool = get_pool()
    k = estatisticas_custo(pool, desde or None, ate or None)
    return HTMLResponse(_env.get_template("acustos").render(
        k=k, desde=desde, ate=ate, aviso=request.session.pop("admin_aviso", None)))


def _estado_alertas(pool) -> dict:
    """Pra onde vao os alertas do admin HOJE, e de onde esse valor veio (banco
    ou variavel de ambiente). Serve pro admin conferir sem abrir o Render."""
    import os
    from finance import config_app as cfg
    email_banco = (cfg.get_config(pool, cfg.ADMIN_EMAIL) or "").strip()
    tg_banco = (cfg.get_config(pool, cfg.ADMIN_TELEGRAM_ID) or "").strip()
    email_env = (os.environ.get("ADMIN_EMAIL") or "").strip()
    tg_env = (os.environ.get("ADMIN_TELEGRAM_ID") or "").strip()
    try:
        from finance.email_sender import remetente_configurado
        remetente = remetente_configurado()
    except Exception:  # noqa: BLE001
        remetente = None
    email_efetivo = email_banco or email_env or remetente
    if email_banco:
        email_origem = "definido aqui no /admin"
    elif email_env:
        email_origem = "variavel ADMIN_EMAIL do Render"
    elif remetente:
        email_origem = "fallback: o proprio remetente SMTP"
    else:
        email_origem = ""
    return {
        "email_banco": email_banco, "email_env": email_env,
        "telegram_banco": tg_banco, "telegram_env": tg_env,
        "email_efetivo": email_efetivo, "email_origem": email_origem,
        "telegram_efetivo": tg_banco or tg_env,
        "telegram_origem": ("definido aqui no /admin" if tg_banco
                            else "variavel ADMIN_TELEGRAM_ID do Render"),
        "remetente": remetente,
    }


@router.get("/admin/comunicacao", response_class=HTMLResponse)
def admin_comunicacao(request: Request):
    if _admin(request) is None:
        return _NEGADO
    pool = get_pool()
    from finance.estatisticas import pronto_para_fase_b, estatisticas_leituras_qr
    from finance.observabilidade import resumo_uso, dificuldades, split_midia
    return HTMLResponse(_env.get_template("acomunic").render(
        faseb=pronto_para_fase_b(pool),
        qr=estatisticas_leituras_qr(pool),
        uso=resumo_uso(pool, 7),
        atrito=dificuldades(pool, 30),
        split=split_midia(pool, 30),
        alertas=_estado_alertas(pool),
        aviso=request.session.pop("admin_aviso", None)))


@router.post("/admin/alertas")
def admin_alertas_salvar(request: Request, email: str = Form(""),
                         telegram_id: str = Form("")):
    """Salva pra onde vao os alertas do sistema. Campo vazio = limpa o valor do
    banco e volta a valer a variavel de ambiente."""
    if _admin(request) is None:
        return _NEGADO
    email = (email or "").strip()
    telegram_id = (telegram_id or "").strip()
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        request.session["admin_aviso"] = f"E-mail invalido: {email} — nada foi salvo."
        return RedirectResponse("/admin/comunicacao", status_code=303)
    if telegram_id and not re.fullmatch(r"-?\d+", telegram_id):
        request.session["admin_aviso"] = (
            "Telegram id tem que ser numerico (ex: 123456789) — nada foi salvo.")
        return RedirectResponse("/admin/comunicacao", status_code=303)
    pool = get_pool()
    from finance import config_app as cfg
    cfg.set_admin_email(pool, email)
    cfg.set_admin_telegram_id(pool, telegram_id)
    request.session["admin_aviso"] = (
        f"Alertas do sistema vao pra {email}." if email
        else "E-mail limpo — volta a valer a variavel ADMIN_EMAIL do Render.")
    return RedirectResponse("/admin/comunicacao", status_code=303)


@router.post("/admin/alertas/teste")
def admin_alertas_teste(request: Request):
    """Dispara um alerta de TESTE pelo caminho real e relata canal a canal.

    O relato e' separado de proposito: e-mail e Telegram falham por motivos
    diferentes (credencial SMTP x token/chat id), e um 'deu certo' agregado
    esconderia justamente o canal quebrado."""
    if _admin(request) is None:
        return _NEGADO
    from finance.notificar import enviar_alerta_teste
    r = enviar_alerta_teste()
    partes = []
    if r["email_ok"]:
        partes.append(f"E-mail ENVIADO pra {r['email_destino']} (veja tambem o spam).")
    else:
        alvo = f" ({r['email_destino']})" if r["email_destino"] else ""
        partes.append(f"E-mail NAO saiu{alvo}: {r['email_erro']}.")
    if r["telegram_ok"]:
        partes.append(f"Telegram ENVIADO pro chat {r['telegram_destino']}.")
    elif r["telegram_destino"]:
        partes.append(f"Telegram NAO saiu: {r['telegram_erro']}.")
    else:
        partes.append("Telegram nao configurado (opcional).")
    request.session["admin_aviso"] = " ".join(partes)
    return RedirectResponse("/admin/comunicacao", status_code=303)
