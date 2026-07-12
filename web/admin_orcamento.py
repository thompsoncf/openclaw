"""Tela ADM: Gerador de Orcamentos (Aladdin).

Padrao IGUAL ao web/admin_precos.py: router proprio, template Jinja que estende
"abase", endpoints JSON pros fetch do front. Reaproveita o que ja existe:
  - login do admin .......... web.admin._admin / _NEGADO
  - IA (Claude) ............. core.brain.Brain  (le ANTHROPIC_API_KEY do ambiente)
  - banco ................... db.conexao.get_pool  (psycopg3)
  - moeda ................... web.portal.brl  (recebe CENTAVOS)
  - tema/menu ............... web.admin._ADMIN_BASE

Plugar no web/app.py:
    from web.admin_orcamento import router as orcamento_router
    app.include_router(orcamento_router)

Conteudo desta tela e' self-contained - subir VERBATIM, nao reescrever.
"""
import json
import re

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse
from jinja2 import Environment, DictLoader, select_autoescape
from pydantic import BaseModel

from core.brain import Brain
from db.conexao import get_pool
from web.portal import brl
from web.admin import _admin, _NEGADO, _ADMIN_BASE

router = APIRouter()

# ---------------------------------------------------------------- catalogo
# Valores em REAIS (referencia de mercado). Tudo editavel na tela.
_MODULOS = [
    {"id": "atendimento", "nome": "Agente de Atendimento", "desc": "Atendimento 24/7 multicanal",      "setup": 4500,  "mensal": 1200, "custo": 430},
    {"id": "automacao",   "nome": "Automacao de Processos", "desc": "Workflows e RPA",                  "setup": 6000,  "mensal": 1500, "custo": 520},
    {"id": "crm",         "nome": "CRM / Leads",            "desc": "Pipeline e gestao de leads",       "setup": 3500,  "mensal": 900,  "custo": 300},
    {"id": "sdr",         "nome": "SDR Digital",            "desc": "Prospeccao e qualificacao",        "setup": 5000,  "mensal": 1400, "custo": 480},
    {"id": "docs",        "nome": "Analise de Documentos",  "desc": "Extracao e leitura com IA",        "setup": 4000,  "mensal": 1100, "custo": 360},
    {"id": "bi",          "nome": "BI / Dashboard",         "desc": "Indicadores em tempo real",        "setup": 5500,  "mensal": 1300, "custo": 410},
    {"id": "voz",         "nome": "Agente de Voz",          "desc": "Ligacoes com IA",                  "setup": 7000,  "mensal": 1800, "custo": 640},
    {"id": "financeiro",  "nome": "Automacao Financeira",   "desc": "Conciliacao e cobranca",           "setup": 6500,  "mensal": 1600, "custo": 560},
    {"id": "custom",      "nome": "Sistema Sob Medida",     "desc": "Desenvolvimento dedicado",         "setup": 12000, "mensal": 2500, "custo": 1100},
]
_DEFAULT_ON = {"atendimento", "automacao", "crm", "sdr", "bi"}  # plano "pro"


def _garantir_tabela(c):
    c.execute("""
        create table if not exists orcamentos (
            id                     bigserial primary key,
            cliente                text,
            empresa                text,
            segmento               text,
            setup_centavos         bigint default 0,
            mensal_centavos        bigint default 0,
            primeiro_ano_centavos  bigint default 0,
            n_modulos              int default 0,
            criado_em              timestamptz default now()
        )""")
    c.commit()


# ---------------------------------------------------------------- rotas
@router.get("/admin/orcamento", response_class=HTMLResponse)
def admin_orcamento(request: Request):
    if _admin(request) is None:
        return _NEGADO
    with get_pool().connection() as c:
        _garantir_tabela(c)
    modulos = [{**m, "on": m["id"] in _DEFAULT_ON} for m in _MODULOS]
    return HTMLResponse(_env.get_template("aorcamento").render(modulos=modulos))


class SugerirIn(BaseModel):
    descricao: str = ""


@router.post("/admin/orcamento/sugerir")
def admin_orcamento_sugerir(request: Request, dados: SugerirIn):
    """Le a descricao do cliente e devolve {modules, segmento, escopo} via Claude."""
    if _admin(request) is None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    desc = (dados.descricao or "").strip()
    if not desc:
        return JSONResponse({"erro": "descricao vazia"}, status_code=400)

    catalogo = "\n".join(f"{m['id']}: {m['nome']} - {m['desc']}" for m in _MODULOS)
    system = (
        "Voce e' consultor de pre-vendas da Aladdin Consultoria e Tecnologia, "
        "que implanta sistemas de IA sob medida para empresas brasileiras. "
        "Responde SEMPRE em portugues do Brasil e SO' com JSON valido."
    )
    prompt = (
        f"MODULOS DISPONIVEIS (use os ids exatos):\n{catalogo}\n\n"
        f"DESCRICAO DO CLIENTE:\n\"\"\"{desc}\"\"\"\n\n"
        "Tarefa: escolha os modulos mais adequados, identifique o segmento e "
        "escreva um escopo (2 a 4 frases, tom comercial, focado em resultado).\n"
        "Responda APENAS com JSON, sem markdown:\n"
        '{"modules":["id","id"],"segmento":"...","escopo":"..."}'
    )
    try:
        resp = Brain().chamar(system=system, mensagens=[{"role": "user", "content": prompt}])
        txt = "".join(
            getattr(b, "text", "") for b in resp.content
            if getattr(b, "type", None) == "text"
        ).strip()
        txt = re.sub(r"^```json|^```|```$", "", txt).strip()
        data = json.loads(txt)
        ids_validos = {m["id"] for m in _MODULOS}
        mods = [i for i in data.get("modules", []) if i in ids_validos]
        return JSONResponse({
            "modules": mods,
            "segmento": (data.get("segmento") or "").strip(),
            "escopo": (data.get("escopo") or "").strip(),
        })
    except Exception:
        return JSONResponse({"erro": "falha ao gerar"}, status_code=500)


class SalvarIn(BaseModel):
    cliente: str = ""
    empresa: str = ""
    segmento: str = ""
    setup: int = 0          # em REAIS
    mensal: int = 0         # em REAIS
    primeiro_ano: int = 0   # em REAIS
    n_modulos: int = 0


@router.post("/admin/orcamento/salvar")
def admin_orcamento_salvar(request: Request, dados: SalvarIn):
    if _admin(request) is None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    with get_pool().connection() as c:
        _garantir_tabela(c)
        c.execute(
            """insert into orcamentos
               (cliente, empresa, segmento, setup_centavos, mensal_centavos,
                primeiro_ano_centavos, n_modulos)
               values (%s,%s,%s,%s,%s,%s,%s)""",
            (dados.cliente or None, dados.empresa or None, dados.segmento or None,
             int(dados.setup) * 100, int(dados.mensal) * 100,
             int(dados.primeiro_ano) * 100, int(dados.n_modulos)),
        )
        c.commit()
    return JSONResponse({"ok": True})


@router.get("/admin/orcamento/lista")
def admin_orcamento_lista(request: Request):
    if _admin(request) is None:
        return JSONResponse({"erro": "nao autorizado"}, status_code=403)
    with get_pool().connection() as c:
        _garantir_tabela(c)
        rows = c.execute(
            """select cliente, empresa, setup_centavos, mensal_centavos,
                      primeiro_ano_centavos, n_modulos, criado_em
               from orcamentos order by criado_em desc limit 50""").fetchall()
    itens = [{
        "cliente": r[0] or "-",
        "empresa": r[1] or "",
        "setup": brl(r[2]),
        "mensal": brl(r[3]),
        "total": brl(r[4]),
        "mods": r[5],
        "data": r[6].strftime("%d/%m") if r[6] else "",
        "inicial": (r[0] or r[1] or "?").strip()[:1].upper(),
    } for r in rows]
    return JSONResponse({"itens": itens})


# ---------------------------------------------------------------- template
_ORCAMENTO_TPL = """{% extends "abase" %}{% block conteudo %}
<h1>Gerador de Orcamentos <span class="mut" style="font-size:1rem">· Aladdin</span></h1>

{% raw %}<style>
.oc-grid{display:grid; grid-template-columns:1fr 340px; gap:1rem; align-items:start}
@media(max-width:900px){.oc-grid{grid-template-columns:1fr}}
.oc-field{display:flex; flex-direction:column; gap:.35rem; margin-bottom:.6rem}
.oc-field label{font-size:.82rem; color:#8b97a8}
.oc-inp{padding:.55rem .7rem; border-radius:8px; background:#0e1525; color:#e7ecf3; border:1px solid #243049; font-size:.95rem; width:100%}
.oc-inp:focus{border-color:#3a4a6b; outline:none}
.oc-mod{display:grid; grid-template-columns:auto 1fr 96px 96px 110px; gap:.7rem; align-items:center; padding:.6rem 0; border-bottom:1px solid #1a2233}
.oc-mod.off{opacity:.45}
.oc-tog{width:42px; height:24px; border-radius:99px; border:none; cursor:pointer; position:relative; background:#2a3550; flex:none}
.oc-tog.on{background:var(--verde-claro)}
.oc-tog::after{content:""; position:absolute; top:3px; left:3px; width:18px; height:18px; border-radius:50%; background:#fff; transition:left .15s}
.oc-tog.on::after{left:21px}
.oc-num{display:flex; align-items:center; gap:3px; border:1px solid #243049; border-radius:7px; padding:.35rem .5rem; background:#0e1525}
.oc-num span{font-size:.72rem; color:#8b97a8}
.oc-num input{width:100%; border:none; background:transparent; color:#e7ecf3; text-align:right; font-size:.86rem; font-variant-numeric:tabular-nums}
.oc-num input:focus{outline:none}
.oc-custo-col{display:none}
body.oc-margin .oc-custo-col{display:flex}
.oc-head{display:grid; grid-template-columns:auto 1fr 96px 96px 110px; gap:.7rem; font-size:.72rem; text-transform:uppercase; letter-spacing:.05em; color:#5a6678; padding-bottom:.4rem; border-bottom:1px solid #243049}
.oc-pill{padding:.4rem .8rem; border-radius:99px; border:1px solid #243049; background:#0e1525; color:#cdd6e4; cursor:pointer; font-size:.85rem}
.oc-pill.on{border-color:var(--verde-claro); background:#10241d; color:#9fe7cc}
.oc-seg button{padding:.45rem .7rem; border:1px solid #243049; background:#0e1525; color:#cdd6e4; cursor:pointer; font-size:.85rem; border-radius:7px}
.oc-seg button.on{border-color:var(--verde-claro); background:#10241d; color:#9fe7cc}
.oc-step{display:inline-flex; align-items:center; gap:0}
.oc-step button{width:34px; height:34px; border:1px solid #243049; background:#0e1525; color:#e7ecf3; cursor:pointer; font-size:1.1rem}
.oc-step .v{min-width:38px; text-align:center; font-variant-numeric:tabular-nums}
.oc-ledger{position:sticky; top:1rem}
.oc-ll{display:flex; justify-content:space-between; align-items:baseline; padding:.6rem 0; border-bottom:1px solid #1a2233}
.oc-ll b{font-size:1.15rem; font-variant-numeric:tabular-nums}
.oc-total{margin-top:.8rem; padding:1rem; border-radius:12px; background:#10241d; border:1px solid #1c3a30}
.oc-total .v{font-size:1.9rem; font-weight:700; color:#9fe7cc; font-variant-numeric:tabular-nums}
.oc-btn{display:block; width:100%; padding:.8rem; border-radius:10px; border:none; cursor:pointer; font-weight:600; font-size:.95rem; margin-top:.6rem}
.oc-btn-g{background:#c99536; color:#1a1206}
.oc-btn-o{background:transparent; border:1px solid #243049; color:#cdd6e4}
.oc-hist{display:flex; align-items:center; justify-content:space-between; padding:.7rem .8rem; border:1px solid #1a2233; border-radius:10px; margin-bottom:.5rem}
.oc-av{width:32px; height:32px; border-radius:8px; background:#1a2233; color:#c99536; display:flex; align-items:center; justify-content:center; font-weight:700; flex:none; margin-right:.7rem}
</style>{% endraw %}

<div class="card">
  <h2 style="margin-top:0">Escopo automatico · IA</h2>
  <p class="mut" style="margin-top:0">Cole o site ou a descricao do cliente. A IA escolhe os modulos e escreve o escopo da proposta.</p>
  <textarea id="oc-desc" class="oc-inp" rows="3" placeholder="Ex.: clinica com 3 unidades, muito WhatsApp, quer reduzir faltas e organizar leads..."></textarea>
  <div style="display:flex; align-items:center; gap:.8rem; margin-top:.6rem">
    <button id="oc-sugerir" class="oc-btn-g" style="border:none; border-radius:8px; padding:.55rem 1rem; cursor:pointer; font-weight:600">Sugerir escopo</button>
    <span id="oc-ia-msg" class="mut" style="font-size:.85rem"></span>
  </div>
  <div id="oc-escopo-out" class="mut" style="display:none; margin-top:.8rem; padding:.8rem; background:#0e1525; border:1px solid #1a2233; border-radius:8px; line-height:1.6"></div>
</div>

<div class="card">
  <h2 style="margin-top:0">Cliente</h2>
  <div style="display:grid; grid-template-columns:1fr 1fr; gap:.8rem">
    <div class="oc-field"><label>Empresa</label><input id="oc-empresa" class="oc-inp" placeholder="Nome da empresa"></div>
    <div class="oc-field"><label>Contato</label><input id="oc-contato" class="oc-inp" placeholder="Responsavel"></div>
    <div class="oc-field"><label>WhatsApp</label><input id="oc-whats" class="oc-inp" placeholder="(86) 9 9999-9999"></div>
    <div class="oc-field"><label>Segmento</label><input id="oc-segmento" class="oc-inp" placeholder="Saude, Varejo, Logistica..."></div>
  </div>
</div>

<div class="card">
  <h2 style="margin-top:0">Planos rapidos</h2>
  <div style="display:flex; gap:.6rem; flex-wrap:wrap">
    <button class="oc-pill" data-plano="basico">Basico</button>
    <button class="oc-pill" data-plano="pro">Pro</button>
    <button class="oc-pill" data-plano="enterprise">Enterprise</button>
  </div>
</div>

<div class="oc-grid">
  <div>
    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:center">
        <h2 style="margin:0">Modulos</h2>
        <button id="oc-margin" class="oc-pill">Modo margem</button>
      </div>
      <div class="oc-head" style="margin-top:.8rem">
        <span></span><span>Modulo</span><span style="text-align:right">Setup</span><span style="text-align:right">Mensal</span><span style="text-align:right">Custo/Margem</span>
      </div>
      {% for m in modulos %}
      <div class="oc-mod{% if not m.on %} off{% endif %}" data-id="{{ m.id }}" data-on="{{ '1' if m.on else '0' }}" data-nome="{{ m.nome }}" data-desc="{{ m.desc }}">
        <button class="oc-tog{% if m.on %} on{% endif %}" type="button" title="Ativar/desativar"></button>
        <div><b>{{ m.nome }}</b><div class="mut" style="font-size:.78rem">{{ m.desc }}</div></div>
        <div class="oc-num"><span>R$</span><input class="oc-setup" inputmode="numeric" value="{{ m.setup }}"></div>
        <div class="oc-num"><span>R$</span><input class="oc-mensal" inputmode="numeric" value="{{ m.mensal }}"></div>
        <div class="oc-num oc-custo-col"><span>R$</span><input class="oc-custo" inputmode="numeric" value="{{ m.custo }}"></div>
      </div>
      {% endfor %}
    </div>

    <div class="card">
      <h2 style="margin-top:0">Parametros</h2>
      <div class="oc-field"><label>Infraestrutura</label>
        <div class="oc-seg" style="display:flex; gap:.4rem">
          <button data-grupo="infra" data-val="compartilhada" class="on">Compartilhada</button>
          <button data-grupo="infra" data-val="dedicada">Dedicada</button>
          <button data-grupo="infra" data-val="onpremise">On-premise</button>
        </div>
      </div>
      <div class="oc-field"><label>Volume mensal</label>
        <div class="oc-seg" style="display:flex; gap:.4rem">
          <button data-grupo="volume" data-val="baixo">Baixo</button>
          <button data-grupo="volume" data-val="medio" class="on">Medio</button>
          <button data-grupo="volume" data-val="alto">Alto</button>
        </div>
      </div>
      <div style="display:flex; gap:1.5rem; flex-wrap:wrap; align-items:flex-end">
        <div class="oc-field" style="margin-bottom:0"><label>Integracoes externas</label>
          <div class="oc-step"><button type="button" data-step="-1">-</button><span class="v" id="oc-integ-v">3</span><button type="button" data-step="1">+</button></div>
          <input type="hidden" id="oc-integ" value="3">
        </div>
        <div class="oc-field" style="margin-bottom:0"><label>Suporte 24h</label>
          <button id="oc-sup" class="oc-pill" data-on="0" type="button">Atendimento dedicado</button>
        </div>
      </div>
      <div class="oc-field" style="margin-top:.8rem"><label>Canais</label>
        <div style="display:flex; gap:.4rem; flex-wrap:wrap">
          <button class="oc-canal oc-pill on" data-on="1">WhatsApp</button>
          <button class="oc-canal oc-pill on" data-on="1">Site</button>
          <button class="oc-canal oc-pill on" data-on="1">Instagram</button>
          <button class="oc-canal oc-pill" data-on="0">Telegram</button>
          <button class="oc-canal oc-pill" data-on="0">E-mail</button>
          <button class="oc-canal oc-pill" data-on="0">Voz</button>
        </div>
      </div>
    </div>
  </div>

  <div class="oc-ledger">
    <div class="card" style="margin:0">
      <div class="mut" style="font-size:.78rem; letter-spacing:.1em; text-transform:uppercase; color:#c99536">Resumo · ao vivo</div>
      <div class="oc-ll"><span class="mut">Investimento inicial</span><b id="oc-r-setup">R$ 0</b></div>
      <div class="oc-ll"><span class="mut">Mensalidade</span><b id="oc-r-mensal" style="color:#c99536">R$ 0</b></div>
      <div class="oc-ll" id="oc-r-margem-l" style="display:none"><span class="mut">Margem/mes</span><b id="oc-r-margem" style="color:var(--verde-claro); font-size:.95rem">-</b></div>
      <div class="oc-total"><div class="mut" style="font-size:.78rem; text-transform:uppercase; letter-spacing:.08em; color:#c99536">Total 1o ano</div><div class="v" id="oc-r-ano">R$ 0</div><div class="mut" id="oc-r-eco" style="display:none; font-size:.8rem; color:var(--verde-claro); margin-top:.3rem"></div></div>
      <button id="oc-anual" class="oc-pill" data-on="0" type="button" style="width:100%; margin-top:.7rem; text-align:left; display:flex; justify-content:space-between; align-items:center">Pagamento anual (-15%) <span id="oc-anual-mk">↻</span></button>
      <button id="oc-gerar" class="oc-btn oc-btn-g">Gerar proposta</button>
      <button id="oc-salvar" class="oc-btn oc-btn-o">Salvar no historico</button>
    </div>
  </div>
</div>

<div class="card">
  <h2 style="margin-top:0">Historico</h2>
  <div id="oc-hist-box"><p class="mut">Carregando...</p></div>
</div>

{% raw %}<script>
(function(){
  var INFRA={compartilhada:{s:0,m:0},dedicada:{s:1500,m:800},onpremise:{s:6000,m:1500}};
  function fmt(n){return 'R$ '+Math.round(n||0).toLocaleString('pt-BR');}
  function rows(){return [].slice.call(document.querySelectorAll('.oc-mod'));}
  function num(el){return parseInt(((el&&el.value)||'0').replace(/\\D/g,''),10)||0;}
  function seg(g){var b=document.querySelector('[data-grupo="'+g+'"].on'); return b?b.getAttribute('data-val'):'';}

  function calc(){
    var setup=0,mensal=0,modMensal=0,custo=0,mods=0;
    rows().forEach(function(r){
      if(r.getAttribute('data-on')==='1'){
        mods++;
        setup+=num(r.querySelector('.oc-setup'));
        var mm=num(r.querySelector('.oc-mensal'));
        mensal+=mm; modMensal+=mm;
        custo+=num(r.querySelector('.oc-custo'));
      }
    });
    var inf=INFRA[seg('infra')]||INFRA.compartilhada;
    setup+=inf.s; mensal+=inf.m;
    if(seg('volume')==='alto') mensal+=600;
    var integ=num(document.getElementById('oc-integ'));
    setup+=integ*250; mensal+=integ*120;
    var canais=document.querySelectorAll('.oc-canal[data-on="1"]').length;
    setup+=canais*400;
    if(document.getElementById('oc-sup').getAttribute('data-on')==='1') mensal+=1500;
    var anual=document.getElementById('oc-anual').getAttribute('data-on')==='1';
    var mensalEf=anual?mensal*0.85:mensal;
    var ano1=setup+mensalEf*12;
    var margem=modMensal-custo, margemPct=modMensal>0?Math.round(margem/modMensal*100):0;
    return {setup:setup,mensal:mensalEf,mensalCheio:mensal,ano1:ano1,margem:margem,margemPct:margemPct,mods:mods,anual:anual};
  }

  function pinta(){
    var c=calc();
    document.getElementById('oc-r-setup').textContent=fmt(c.setup);
    document.getElementById('oc-r-mensal').textContent=fmt(c.mensal);
    document.getElementById('oc-r-ano').textContent=fmt(c.ano1);
    document.getElementById('oc-r-margem').textContent=fmt(c.margem)+' · '+c.margemPct+'%';
    var eco=document.getElementById('oc-r-eco');
    if(c.anual){eco.style.display='block'; eco.textContent='Economia de '+fmt(c.mensalCheio*12*0.15)+' no ano';}
    else eco.style.display='none';
  }

  // toggles de modulo
  rows().forEach(function(r){
    r.querySelector('.oc-tog').addEventListener('click',function(){
      var on=r.getAttribute('data-on')==='1';
      r.setAttribute('data-on',on?'0':'1');
      r.classList.toggle('off',on);
      this.classList.toggle('on',!on);
      pinta();
    });
  });
  document.querySelectorAll('.oc-setup,.oc-mensal,.oc-custo').forEach(function(i){i.addEventListener('input',pinta);});

  // modo margem
  document.getElementById('oc-margin').addEventListener('click',function(){
    document.body.classList.toggle('oc-margin');
    this.classList.toggle('on');
    document.getElementById('oc-r-margem-l').style.display=document.body.classList.contains('oc-margin')?'flex':'none';
    pinta();
  });

  // segmentos (infra/volume)
  document.querySelectorAll('[data-grupo]').forEach(function(b){
    b.addEventListener('click',function(){
      document.querySelectorAll('[data-grupo="'+b.getAttribute('data-grupo')+'"]').forEach(function(x){x.classList.remove('on');});
      b.classList.add('on'); pinta();
    });
  });
  // stepper integracoes
  document.querySelectorAll('[data-step]').forEach(function(b){
    b.addEventListener('click',function(){
      var h=document.getElementById('oc-integ');
      var v=Math.max(0,Math.min(20,(parseInt(h.value,10)||0)+parseInt(b.getAttribute('data-step'),10)));
      h.value=v; document.getElementById('oc-integ-v').textContent=v; pinta();
    });
  });
  // canais + suporte (toggle generico data-on)
  document.querySelectorAll('.oc-canal,#oc-sup,#oc-anual').forEach(function(b){
    b.addEventListener('click',function(){
      var on=b.getAttribute('data-on')==='1';
      b.setAttribute('data-on',on?'0':'1');
      b.classList.toggle('on',!on);
      var mk=document.getElementById('oc-anual-mk'); if(b.id==='oc-anual'&&mk) mk.textContent=on?'↻':'✓';
      pinta();
    });
  });

  // planos rapidos
  var PLANOS={basico:['atendimento','crm'],pro:['atendimento','automacao','crm','sdr','bi'],enterprise:['atendimento','automacao','crm','sdr','docs','bi','voz','financeiro','custom']};
  document.querySelectorAll('[data-plano]').forEach(function(b){
    b.addEventListener('click',function(){
      var set=PLANOS[b.getAttribute('data-plano')]||[];
      rows().forEach(function(r){
        var on=set.indexOf(r.getAttribute('data-id'))>=0;
        r.setAttribute('data-on',on?'1':'0'); r.classList.toggle('off',!on);
        r.querySelector('.oc-tog').classList.toggle('on',on);
      });
      document.querySelectorAll('[data-plano]').forEach(function(x){x.classList.remove('on');});
      b.classList.add('on'); pinta();
    });
  });

  // IA: sugerir escopo
  document.getElementById('oc-sugerir').addEventListener('click',function(){
    var desc=document.getElementById('oc-desc').value.trim();
    if(!desc){return;}
    var btn=this, msg=document.getElementById('oc-ia-msg');
    btn.disabled=true; var t0=btn.textContent; btn.textContent='Analisando...'; msg.textContent='';
    fetch('/admin/orcamento/sugerir',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify({descricao:desc})})
      .then(function(r){return r.json();})
      .then(function(d){
        if(d.erro){msg.textContent='Nao consegui gerar agora. Tente de novo.'; return;}
        var ids=d.modules||[];
        if(ids.length){rows().forEach(function(r){var on=ids.indexOf(r.getAttribute('data-id'))>=0; r.setAttribute('data-on',on?'1':'0'); r.classList.toggle('off',!on); r.querySelector('.oc-tog').classList.toggle('on',on);});}
        if(d.segmento){document.getElementById('oc-segmento').value=d.segmento;}
        var out=document.getElementById('oc-escopo-out');
        if(d.escopo){out.style.display='block'; out.textContent=d.escopo; out.setAttribute('data-escopo',d.escopo);}
        pinta();
      })
      .catch(function(){msg.textContent='Erro de conexao.';})
      .finally(function(){btn.disabled=false; btn.textContent=t0;});
  });

  // salvar
  document.getElementById('oc-salvar').addEventListener('click',function(){
    var c=calc();
    var body={cliente:document.getElementById('oc-contato').value||'',empresa:document.getElementById('oc-empresa').value||'',segmento:document.getElementById('oc-segmento').value||'',setup:Math.round(c.setup),mensal:Math.round(c.mensal),primeiro_ano:Math.round(c.ano1),n_modulos:c.mods};
    var btn=this; btn.textContent='Salvando...';
    fetch('/admin/orcamento/salvar',{method:'POST',headers:{'Content-Type':'application/json'},body:JSON.stringify(body)})
      .then(function(r){return r.json();}).then(function(){btn.textContent='Salvo!'; carregarHist(); setTimeout(function(){btn.textContent='Salvar no historico';},1500);})
      .catch(function(){btn.textContent='Erro ao salvar';});
  });

  // historico
  function esc(s){var d=document.createElement('div'); d.textContent=s==null?'':s; return d.innerHTML;}
  function carregarHist(){
    fetch('/admin/orcamento/lista').then(function(r){return r.json();}).then(function(d){
      var box=document.getElementById('oc-hist-box');
      if(!d.itens||!d.itens.length){box.innerHTML='<p class="mut">Nenhum orcamento salvo ainda.</p>'; return;}
      box.innerHTML=d.itens.map(function(it){
        return '<div class="oc-hist"><div style="display:flex;align-items:center;min-width:0">'
          +'<div class="oc-av">'+esc(it.inicial)+'</div>'
          +'<div><b>'+esc(it.cliente)+(it.empresa?' <span class="mut">· '+esc(it.empresa)+'</span>':'')+'</b>'
          +'<div class="mut" style="font-size:.78rem">'+esc(it.data)+' · '+it.mods+' modulos</div></div></div>'
          +'<b style="font-variant-numeric:tabular-nums">'+esc(it.total)+'</b></div>';
      }).join('');
    }).catch(function(){document.getElementById('oc-hist-box').innerHTML='<p class="mut">Erro ao carregar.</p>';});
  }

  // gerar proposta (documento premium em nova aba)
  document.getElementById('oc-gerar').addEventListener('click',function(){
    var c=calc();
    var empresa=document.getElementById('oc-empresa').value||'Cliente';
    var contato=document.getElementById('oc-contato').value||'';
    var whats=document.getElementById('oc-whats').value||'';
    var segmento=document.getElementById('oc-segmento').value||'';
    var escopo=(document.getElementById('oc-escopo-out').getAttribute('data-escopo'))||('A Aladdin propoe a '+empresa+' a implantacao de uma operacao de IA sob medida, integrando os modulos abaixo, com foco em automatizar o atendimento, qualificar oportunidades e dar visibilidade dos resultados.');
    var linhas=rows().filter(function(r){return r.getAttribute('data-on')==='1';}).map(function(r){
      return {nome:r.getAttribute('data-nome'),desc:r.getAttribute('data-desc'),setup:num(r.querySelector('.oc-setup')),mensal:num(r.querySelector('.oc-mensal'))};
    });
    var hoje=new Date();
    var meses=['janeiro','fevereiro','marco','abril','maio','junho','julho','agosto','setembro','outubro','novembro','dezembro'];
    var dataStr=hoje.getDate()+' de '+meses[hoje.getMonth()]+' de '+hoje.getFullYear();
    var docNum='AL-'+hoje.getFullYear()+(''+(hoje.getMonth()+1)).padStart(2,'0')+'-'+(''+(Math.floor(hoje.getTime()/1000)%1000)).padStart(3,'0');
    var trs=linhas.map(function(l){return '<tr><td><b>'+l.nome+'</b><small>'+l.desc+'</small></td><td class=r>'+fmt(l.setup)+'</td><td class=r>'+fmt(l.mensal)+'</td></tr>';}).join('');
    var html='<!doctype html><html lang=pt-br><head><meta charset=utf-8><title>Proposta '+empresa+'</title>'
      +'<style>@import url("https://fonts.googleapis.com/css2?family=Fraunces:opsz,wght@9..144,500;9..144,600&family=Inter:wght@400;500;600&family=IBM+Plex+Mono:wght@500&display=swap");'
      +'*{box-sizing:border-box;margin:0;padding:0}body{font-family:Inter,sans-serif;color:#14213D;background:#EEE9DD;padding:30px}'
      +'.pg{max-width:794px;margin:0 auto;background:#fff;border-radius:4px;overflow:hidden;box-shadow:0 20px 50px -25px rgba(20,33,61,.4)}'
      +'.hd{background:#14213D;color:#F4F1EA;padding:36px 48px;display:flex;justify-content:space-between;align-items:flex-start}'
      +'.hd .lg{font-family:Fraunces,serif;font-size:26px;font-weight:600}.hd .lg span{color:#E0B458}'
      +'.hd .sub{font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:#9FA8BC;margin-top:5px}'
      +'.hd .mt{text-align:right;font-size:11px;color:#9FA8BC}.hd .mt b{display:block;color:#E0B458;font-size:10px;letter-spacing:.14em;text-transform:uppercase;margin-bottom:4px}'
      +'.bd{padding:34px 48px 44px}.eb{font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:#B8862E;font-weight:600;margin:24px 0 10px}'
      +'.cli{background:#FBFAF7;border:1px solid #ECE7DC;border-radius:9px;padding:14px 16px;font-size:14px}'
      +'p.es{font-size:13.5px;line-height:1.7;color:#3A4254}'
      +'table{width:100%;border-collapse:collapse;margin-top:4px}th{font-size:10px;text-transform:uppercase;letter-spacing:.06em;color:#9FA8BC;background:#14213D;padding:10px 14px;text-align:left}th.r,td.r{text-align:right;font-family:"IBM Plex Mono",monospace}'
      +'td{padding:12px 14px;border-bottom:1px solid #F0EBE0;font-size:13.5px}td small{display:block;color:#A8A192;font-size:11.5px;margin-top:2px}'
      +'.tot{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:24px}.bx{border:1px solid #ECE7DC;border-radius:12px;padding:18px}.bx .l{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:#8A8475;font-weight:600}.bx .v{font-family:"IBM Plex Mono",monospace;font-size:23px;font-weight:600;margin-top:6px}'
      +'.fin{margin-top:12px;background:#14213D;border-radius:12px;padding:20px 22px;display:flex;justify-content:space-between;align-items:center}.fin .l{font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:#E0B458;font-weight:600}.fin .v{font-family:"IBM Plex Mono",monospace;font-size:30px;font-weight:600;color:#FBFAF7}'
      +'.ft{margin-top:28px;font-size:11.5px;color:#8A8475;line-height:1.6}@media print{body{background:#fff;padding:0}.pg{box-shadow:none;border-radius:0}@page{size:A4;margin:0}}'
      +'</style></head><body><div class=pg>'
      +'<div class=hd><div><div class=lg>Aladdin <span>·</span></div><div class=sub>Consultoria e Tecnologia</div></div>'
      +'<div class=mt><b>Proposta comercial</b>'+docNum+'<br>'+dataStr+'</div></div>'
      +'<div class=bd><div class=eb>Preparada para</div><div class=cli><b>'+empresa+'</b>'+(contato?' · '+contato:'')+(whats?' · '+whats:'')+(segmento?'<br><span style="color:#A8A192">Segmento: '+segmento+'</span>':'')+'</div>'
      +'<div class=eb>Escopo da solucao</div><p class=es>'+escopo+'</p>'
      +'<div class=eb>Modulos contratados</div><table><tr><th>Modulo</th><th class=r>Setup</th><th class=r>Mensal</th></tr>'+trs+'</table>'
      +'<div class=tot><div class=bx><div class=l>Investimento inicial</div><div class=v>'+fmt(c.setup)+'</div></div><div class=bx><div class=l>Mensalidade</div><div class=v>'+fmt(c.mensal)+'</div></div></div>'
      +'<div class=fin><div><div class=l>Total estimado · 1o ano</div></div><div class=v>'+fmt(c.ano1)+'</div></div>'
      +'<div class=ft>Proposta valida por 15 dias. Valores em reais (BRL). Implantacao media de 2 a 6 semanas conforme escopo.<br>Aladdin Consultoria e Tecnologia · Teresina/PI</div>'
      +'</div></div><script>window.onload=function(){setTimeout(function(){window.print();},400);}<\\/script></body></html>';
    var w=window.open('','_blank');
    if(w){w.document.write(html); w.document.close();}
  });

  pinta(); carregarHist();
})();
</script>{% endraw %}
{% endblock %}"""

_env = Environment(
    loader=DictLoader({"abase": _ADMIN_BASE, "aorcamento": _ORCAMENTO_TPL}),
    autoescape=select_autoescape())
_env.globals["brl"] = brl
