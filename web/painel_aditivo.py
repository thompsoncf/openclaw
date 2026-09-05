"""A tela que faz o termo aditivo — cinco blocos, marca o que mudou.

MORA SOB /painel/servicos/ DE PROPÓSITO

O dono decidiu em 05/09/2026 que dono, gestor E vendedor fazem aditivo. Pendurar
esta tela em `/painel/empresa` (onde mora o financeiro) a deixaria invisível pro
vendedor, que não tem `caps.financeiro` — foi exatamente assim que a aprovação de
contas a pagar subiu no ar sem ninguém ver. Sob `/painel/servicos/`, o gate de
`web/app.py` libera pelos mesmos `caps.vendas` que já deixam o vendedor montar
orçamento, e `_conta_servico` revalida no POST.

OS CINCO BLOCOS, NA ORDEM QUE O DONO USA
data · horário · convidados · serviços · valor. E o valor "dependendo da
alteração", como ele disse — por isso ele se marca sozinho quando outro bloco
mexe em dinheiro, e a taxa da cláusula 7.2 já vem calculada com um botão de
zerar.

O QUE ESTA TELA NÃO FAZ
Não barra nada. As conferências da cláusula 7 aparecem escritas, com o número de
dias e o nome de quem já ocupa a data, e o botão continua clicável — "só avisa,
não trava", a regra que o dono deu em contas a pagar. Quem manda no contrato é
ele.
"""
from __future__ import annotations

import logging
from datetime import datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse

from db.conexao import get_pool
from finance import aditivo as ad
from finance import contrato as ctr
from web.painel_servicos import _conta_servico
from web.portal import _render, _env

router = APIRouter()
_log = logging.getLogger("painel.aditivo")

_TPL_NOME = "painel_aditivo.html"


def _centavos(v) -> int:
    """'R$ 1.250,00' / '1250,00' / '1250.00' -> 125000.

    Aceita o que o dono digitar. Vale a pena ser tolerante aqui: é campo de
    dinheiro numa tela que ele usa com o cliente no telefone."""
    s = str(v or "").strip()
    if not s:
        return 0
    s = s.replace("R$", "").replace(" ", "").replace("\xa0", "")
    neg = s.startswith("-")
    s = s.lstrip("-")
    if "," in s:                       # 1.250,00 -> 1250.00
        s = s.replace(".", "").replace(",", ".")
    try:
        return int(round(float(s) * 100)) * (-1 if neg else 1)
    except ValueError:
        return 0


def _data_iso(v) -> str:
    """'22/01/2027' ou '2027-01-22' -> '2027-01-22'. Vazio quando não dá."""
    s = str(v or "").strip()
    if not s:
        return ""
    for f in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, f).date().isoformat()
        except ValueError:
            continue
    return ""


@router.get("/painel/servicos/aditivo/{contrato_id}")
def aditivo_form(request: Request, contrato_id: int, erro: str = ""):
    conta, redir = _conta_servico(request)
    if redir:
        return redir
    pool = get_pool()
    est = ad.estado_atual(pool, conta[0], contrato_id)
    if not est:
        return RedirectResponse("/painel/servicos", status_code=303)
    aberto = ad.aberto_do_contrato(pool, conta[0], contrato_id)
    regras = (ctr.carregar_modelo(pool, conta[0]) or {}).get("regras") or {}
    return _render(
        _TPL_NOME, request, titulo="Termo aditivo", secao_ativa="servicos",
        empresa_nome=conta[2], est=est, aberto=aberto,
        anteriores=ad.assinados_do_contrato(pool, conta[0], contrato_id),
        pct_taxa=regras.get("taxa_reagendamento") or ctr.REGRAS_PADRAO["taxa_reagendamento"],
        valor_atual=ctr.reais(int(est["valor_centavos"] or 0)),
        itens=[i.get("nome") or "" for i in (est.get("itens") or [])],
        erro=erro)


@router.post("/painel/servicos/aditivo/{contrato_id}/conferir")
def aditivo_conferir(request: Request, contrato_id: int, nova_data: str = Form("")):
    """As quatro conferências da cláusula 7, sob demanda, sem sair da tela.

    Rota separada porque a tela precisa mostrar o resultado ANTES de o dono
    mandar pro cliente — e porque conferir não pode criar nada."""
    conta, redir = _conta_servico(request)
    if redir:
        return redir
    pool = get_pool()
    regras = (ctr.carregar_modelo(pool, conta[0]) or {}).get("regras") or {}
    avisos = ad.conferir_data(pool, conta[0], contrato_id,
                              _data_iso(nova_data), regras)
    from fastapi.responses import JSONResponse
    return JSONResponse({"avisos": avisos})


@router.post("/painel/servicos/aditivo/{contrato_id}/criar")
def aditivo_criar(request: Request, contrato_id: int,
                  mudar_data: str = Form(""), nova_data: str = Form(""),
                  mudar_horario: str = Form(""), novo_inicio: str = Form(""),
                  novo_fim: str = Form(""),
                  mudar_convidados: str = Form(""), novos_convidados: str = Form(""),
                  mudar_servicos: str = Form(""), itens_saem: list[str] = Form(None),
                  itens_entram: str = Form(""),
                  mudar_valor: str = Form(""), diferenca: str = Form(""),
                  taxa: str = Form(""), vencimento: str = Form(""),
                  forma_pagamento: str = Form("")):
    conta, redir = _conta_servico(request)
    if redir:
        return redir
    pool = get_pool()
    est = ad.estado_atual(pool, conta[0], contrato_id)
    if not est:
        return RedirectResponse("/painel/servicos", status_code=303)

    alteracoes = []
    if mudar_data == "on" and _data_iso(nova_data):
        alteracoes.append({"campo": "data", "de": est.get("data"),
                           "para": _data_iso(nova_data)})
    if mudar_horario == "on" and (novo_inicio or novo_fim):
        alteracoes.append({
            "campo": "horario",
            "de": {"inicio": est.get("inicio"), "fim": est.get("fim")},
            "para": {"inicio": ad._hora_limpa(novo_inicio) or est.get("inicio"),
                     "fim": ad._hora_limpa(novo_fim) or est.get("fim")}})
    if mudar_convidados == "on" and str(novos_convidados).strip():
        try:
            alteracoes.append({"campo": "convidados", "de": est.get("convidados"),
                               "para": int(str(novos_convidados).strip())})
        except ValueError:
            pass
    if mudar_servicos == "on" and (itens_saem or itens_entram.strip()):
        alteracoes.append({"campo": "servicos",
                           "saem": [s for s in (itens_saem or []) if s.strip()],
                           "entram": itens_entram.strip()})

    dif = _centavos(diferenca) if mudar_valor == "on" else 0
    tx = _centavos(taxa) if mudar_valor == "on" else 0
    try:
        ad.criar(pool, conta[0], contrato_id, alteracoes,
                 diferenca_centavos=dif, taxa_centavos=tx,
                 vencimento=_data_iso(vencimento) or None,
                 forma_pagamento=forma_pagamento,
                 criado_por=(request.session.get("nome")
                             or request.session.get("email") or ""))
    except ValueError as e:
        return RedirectResponse(
            f"/painel/servicos/aditivo/{contrato_id}?erro={e}", status_code=303)
    return RedirectResponse(f"/painel/servicos/aditivo/{contrato_id}", status_code=303)


@router.post("/painel/servicos/aditivo/{contrato_id}/cancelar/{aditivo_id}")
def aditivo_cancelar(request: Request, contrato_id: int, aditivo_id: int):
    conta, redir = _conta_servico(request)
    if redir:
        return redir
    ad.cancelar(get_pool(), conta[0], aditivo_id)
    return RedirectResponse(f"/painel/servicos/aditivo/{contrato_id}", status_code=303)


_TPL = r"""{% extends "base" %}{% block conteudo %}
{% raw %}<style>
.ad-wrap{max-width:900px}
.ad-bloco{background:var(--cartao,#fff);border:1px solid var(--linha,#E3DDD0);border-radius:10px;padding:.85rem 1rem;margin-bottom:.6rem}
.ad-bloco.off{opacity:.55}
.ad-bh{display:flex;align-items:center;gap:.55rem;font-size:.95rem;font-weight:700;cursor:pointer;user-select:none}
.ad-bh input[type=checkbox]{width:17px;height:17px;flex:0 0 auto}
.ad-bh .marca{margin-left:auto;font-size:.68rem;letter-spacing:.06em;text-transform:uppercase;font-weight:700;padding:.15rem .5rem;border-radius:99px;border:1px solid}
.ad-bc{margin-top:.7rem;padding-top:.7rem;border-top:1px solid var(--linha,#F0ECE3);display:grid;grid-template-columns:repeat(3,1fr);gap:.6rem}
@media(max-width:720px){.ad-bc{grid-template-columns:1fr}}
.ad-c label{display:block;font-size:.66rem;letter-spacing:.08em;text-transform:uppercase;color:var(--mut,#8A8475);font-weight:700;margin-bottom:.2rem}
.ad-c input,.ad-c select,.ad-c textarea{width:100%;padding:.5rem .6rem;border:1px solid var(--linha,#DCD5C6);border-radius:7px;font-size:.9rem;font-family:inherit}
.ad-c .de{font-size:.72rem;color:var(--mut,#8A8475);margin-top:.2rem}
.ad-nota{grid-column:1/-1;background:var(--fundo2,#FBFAF7);border:1px dashed var(--linha,#C9BFA8);border-radius:8px;padding:.55rem .7rem;font-size:.78rem;line-height:1.55}
.ad-av{grid-column:1/-1;border-radius:8px;padding:.55rem .7rem;font-size:.78rem;line-height:1.6;background:#FFF6E8;border:1px solid #F0DDBA;color:#7A4E0C}
.ad-av .ok{color:#1c7a4f}.ad-av .nao{color:#B4453C;font-weight:700}
.ad-hist{background:var(--fundo2,#FBFAF7);border:1px solid var(--linha,#ECE7DC);border-radius:9px;padding:.6rem .8rem;font-size:.82rem;margin-bottom:.8rem}
.ad-erro{background:#FDECEA;border:1px solid #F5C6C0;color:#B4453C;border-radius:8px;padding:.55rem .7rem;font-size:.85rem;margin-bottom:.8rem}
</style>{% endraw %}

<div class="ad-wrap">
  <h2 style="margin:0 0 .2rem">Termo aditivo</h2>
  <p class="mut" style="margin:0 0 1rem;font-size:.85rem">
    Contrato nº {{ est.contrato_numero }} · {{ est.cliente }}
    {% if est.orcamento_numero %} · orçamento nº {{ est.orcamento_numero }}{% endif %}
    {% if est.data %} · {{ est.tipo or 'Evento' }} em {{ est.data }}{% endif %}
  </p>

  {% if erro %}<div class="ad-erro">{{ erro }}</div>{% endif %}

  {% if anteriores %}
  <div class="ad-hist"><b>Já houve {{ anteriores|length }} aditivo(s) assinado(s)
    neste contrato.</b>
    {% for a in anteriores %}<br>· {{ a.ordem }}º — assinado por {{ a.assinado_por }}
    {% if a.assinado_em %}em {{ a.assinado_em.strftime('%d/%m/%Y') }}{% endif %}
    {% if a.token %}(<a href="/aditivo/{{ a.token }}" target="_blank">ver</a>){% endif %}
    {% endfor %}
  </div>
  {% endif %}

  {% if aberto %}
  {# UM link vivo por contrato: dois é o cliente assinando o errado, e como o
     aditivo muda data, assinar o errado é o evento no dia errado. #}
  <div class="ad-hist" style="border-color:#F0DDBA;background:#FFF6E8;color:#7A4E0C">
    <b>O {{ aberto.ordem }}º aditivo está esperando a assinatura do cliente.</b><br>
    Link: <a href="/aditivo/{{ aberto.token }}" target="_blank">/aditivo/{{ aberto.token }}</a><br>
    Pra fazer outro, cancele este primeiro — enquanto ninguém assinou, nada mudou
    no sistema.
    <form method="post" style="margin-top:.5rem"
          action="/painel/servicos/aditivo/{{ est.contrato_id }}/cancelar/{{ aberto.id }}"
          onsubmit="return confirm('Cancelar o {{ aberto.ordem }}º aditivo? O link enviado ao cliente para de funcionar.')">
      <button class="btn" type="submit">Cancelar este aditivo</button>
    </form>
  </div>
  {% else %}

  <form method="post" action="/painel/servicos/aditivo/{{ est.contrato_id }}/criar">

    <!-- 1 DATA -->
    <div class="ad-bloco">
      <label class="ad-bh"><input type="checkbox" name="mudar_data" id="k-data">
        <span>1 · Alterar a <b>data</b></span>
        <span class="marca" style="border-color:#F0DDBA;background:#FFF6E8;color:#7A4E0C">cai na cláusula 7</span></label>
      <div class="ad-bc">
        <div class="ad-c"><label>Data contratada</label>
          <input value="{{ est.data or '—' }}" disabled></div>
        <div class="ad-c"><label>Nova data</label>
          <input type="date" name="nova_data" id="f-data"></div>
        <div class="ad-c"></div>
        <div class="ad-av" id="av-7" style="display:none"></div>
      </div>
    </div>

    <!-- 2 HORÁRIO -->
    <div class="ad-bloco">
      <label class="ad-bh"><input type="checkbox" name="mudar_horario">
        <span>2 · Alterar o <b>horário</b></span>
        <span class="marca" style="border-color:#BFE3D0;background:#E8F5EE;color:#1c7a4f">sem taxa</span></label>
      <div class="ad-bc">
        <div class="ad-c"><label>Início</label>
          <input type="time" name="novo_inicio" value="{{ est.inicio }}">
          <div class="de">era {{ est.inicio or '—' }}</div></div>
        <div class="ad-c"><label>Término</label>
          <input type="time" name="novo_fim" value="{{ est.fim }}">
          <div class="de">era {{ est.fim or '—' }}</div></div>
        <div class="ad-c"></div>
        <div class="ad-nota">Se o término for menor que o início, o documento
          entende que a festa vira a noite e escreve o dia seguinte — foi assim
          nos dois aditivos de horário que a empresa já assinou.</div>
      </div>
    </div>

    <!-- 3 CONVIDADOS -->
    <div class="ad-bloco">
      <label class="ad-bh"><input type="checkbox" name="mudar_convidados">
        <span>3 · Alterar a <b>quantidade de convidados</b></span></label>
      <div class="ad-bc">
        <div class="ad-c"><label>Era</label>
          <input value="{{ est.convidados or '—' }}" disabled>
          <div class="de">do orçamento nº {{ est.orcamento_numero or '—' }}</div></div>
        <div class="ad-c"><label>Passa a ser</label>
          <input type="number" name="novos_convidados" min="0"></div>
        <div class="ad-c"></div>
      </div>
    </div>

    <!-- 4 SERVIÇOS -->
    <div class="ad-bloco">
      <label class="ad-bh"><input type="checkbox" name="mudar_servicos">
        <span>4 · Alterar os <b>serviços contratados</b></span></label>
      <div class="ad-bc" style="grid-template-columns:1fr 1fr">
        <div class="ad-c"><label>Itens que saem</label>
          <select name="itens_saem" multiple size="5">
            {% for i in itens %}<option value="{{ i }}">{{ i }}</option>{% endfor %}
          </select>
          <div class="de">os itens do orçamento nº {{ est.orcamento_numero or '—' }}</div></div>
        <div class="ad-c"><label>Passam a ser</label>
          <textarea name="itens_entram" rows="4"
            placeholder="01 (um) monitor para área kids, e louças limitado a atender até 100 pessoas"></textarea></div>
      </div>
    </div>

    <!-- 5 VALOR -->
    <div class="ad-bloco">
      <label class="ad-bh"><input type="checkbox" name="mudar_valor" id="k-valor">
        <span>5 · Ajustar o <b>valor</b></span>
        <span class="marca" id="m-valor" style="display:none;border-color:#BFE3D0;background:#E8F5EE;color:#1c7a4f">marcou sozinho</span></label>
      <div class="ad-bc">
        <div class="ad-c"><label>Total de hoje</label>
          <input value="{{ valor_atual }}" disabled></div>
        <div class="ad-c"><label>Diferença a cobrar</label>
          <input name="diferenca" id="f-dif" placeholder="R$ 0,00" inputmode="decimal">
          <div class="de">você digita — o sistema não calcula por convidado</div></div>
        <div class="ad-c"><label>Taxa de reagendamento ({{ pct_taxa }}%)</label>
          <input name="taxa" id="f-taxa" placeholder="R$ 0,00" inputmode="decimal">
          <div class="de">cláusula 7.2 · <a href="#" id="zerar-taxa">zerar</a></div></div>
        <div class="ad-c"><label>Vencimento</label>
          <input type="date" name="vencimento"></div>
        <div class="ad-c"><label>Forma de pagamento</label>
          <input name="forma_pagamento" placeholder="chave PIX: ...">
          </div>
        <div class="ad-c"></div>
        <div class="ad-nota" id="resumo-valor"></div>
      </div>
    </div>

    <div style="display:flex;gap:.5rem;flex-wrap:wrap;margin-top:.8rem">
      <button class="btn primario" type="submit">Gerar aditivo e mandar pro cliente</button>
      <a class="btn" href="/painel/servicos">Voltar</a>
    </div>
  </form>
  {% endif %}
</div>

{% raw %}<script>
(function(){
  var TOTAL = {% endraw %}{{ est.valor_centavos or 0 }}{% raw %};
  var PCT   = {% endraw %}{{ pct_taxa }}{% raw %};
  var CT    = {% endraw %}{{ est.contrato_id }}{% raw %};
  function brl(c){ return (c/100).toLocaleString('pt-BR',{style:'currency',currency:'BRL'}); }
  function cent(v){
    v = (v||'').toString().replace(/R\$/g,'').replace(/\s/g,'');
    if(v.indexOf(',')>=0) v = v.replace(/\./g,'').replace(',','.');
    var n = parseFloat(v); return isNaN(n) ? 0 : Math.round(n*100);
  }
  var kData=document.getElementById('k-data'), fData=document.getElementById('f-data'),
      kValor=document.getElementById('k-valor'), mValor=document.getElementById('m-valor'),
      fDif=document.getElementById('f-dif'), fTaxa=document.getElementById('f-taxa'),
      av=document.getElementById('av-7'), resumo=document.getElementById('resumo-valor');
  if(!kData) return;

  // A TAXA NASCE MARCADA, e o dono zera. Decisão dele em 05/09/2026: entrar
  // zerada faria a taxa sumir na pressa; entrar travada seria cobrar sem ele ver.
  function recalcTaxa(){
    if(kData.checked){
      var atualizado = TOTAL + cent(fDif.value);
      fTaxa.value = brl(Math.round(atualizado * PCT / 100));
      if(!kValor.checked){ kValor.checked = true; mValor.style.display=''; }
    }
    resumoValor();
  }
  function resumoValor(){
    var d = cent(fDif.value), t = cent(fTaxa.value);
    resumo.innerHTML = (d||t)
      ? 'A cobrar agora: <b>'+brl(d+t)+'</b> · novo total do contrato: <b>'+brl(TOTAL+d+t)+'</b>'
      : 'Sem mudança de valor neste aditivo.';
  }
  // As conferências da cláusula 7 — informam, nunca barram.
  function conferir(){
    if(!kData.checked || !fData.value){ av.style.display='none'; return; }
    var fd = new FormData(); fd.append('nova_data', fData.value);
    fetch('/painel/servicos/aditivo/'+CT+'/conferir', {method:'POST', body:fd})
      .then(function(r){ return r.json(); })
      .then(function(j){
        av.style.display='';
        av.innerHTML = '<b>Cláusula 7 — o que o contrato pede:</b><br>' +
          (j.avisos||[]).map(function(a){
            return (a.ok ? '<span class="ok">✓</span> ' : '<span class="nao">⚠</span> ')
                   + '<b>'+a.regra+'</b> ' + a.texto;
          }).join('<br>') +
          '<br><span style="opacity:.8">Isto é aviso, não trava: você decide e segue.</span>';
      }).catch(function(){ av.style.display='none'; });
  }
  kData.addEventListener('change', function(){ recalcTaxa(); conferir(); });
  fData.addEventListener('change', conferir);
  fDif.addEventListener('input', recalcTaxa);
  fTaxa.addEventListener('input', resumoValor);
  document.getElementById('zerar-taxa').addEventListener('click', function(e){
    e.preventDefault(); fTaxa.value=''; resumoValor();
  });
  kValor.addEventListener('change', function(){ mValor.style.display='none'; });
  resumoValor();
})();
</script>{% endraw %}
{% endblock %}
"""

# Registrado com .html DE PROPÓSITO: o `select_autoescape()` do portal decide
# pela extensão, e esta tela imprime nome de cliente e nome de item vindos do
# orçamento. Os painéis antigos usam nome sem extensão (e saem crus); aqui não
# há motivo pra repetir isso.
_env.loader.mapping[_TPL_NOME] = _TPL
