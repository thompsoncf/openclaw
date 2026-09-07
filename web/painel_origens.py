"""A tela de Origens: /painel/origens — de onde vieram os leads e o que virou.

É a metade de baixo do funil da AGÊNCIA de tráfego, dentro do Zaq. O painel dela
termina em "conversas iniciadas"; daí pra baixo — atendimento, compromisso,
comparecimento, venda, faturamento — só o Zaq sabe, e hoje isso é digitado à mão
numa planilha uma vez por mês.

O QUE NÃO TEM AQUI, DE PROPÓSITO: investimento, CPL, CAC, ROAS, ROI. Esses
números são medida da agência. O Zaq publicar um que divergisse do painel dela
faria o Zaq parecer errado — e a ponte entre os dois lados não precisa disso: é o
CÓDIGO do criativo, que aparece dos dois lados e junta as duas planilhas.

Quem vê: dono, gestor e o **convidado** — o papel da agência, que entra por
convite do dono e não alcança mais nada do sistema.

O QUE O CONVIDADO NÃO VÊ, decidido pelo dono em 07/09/2026: o dinheiro da faixa
"sem código". Ele precisa da CONTAGEM dela (parte é anúncio que perdeu o texto, e
é assim que a agência percebe a atribuição vazando), mas o faturamento que veio de
fora do anúncio não é assunto dela. O faturamento das linhas COM código continua
em reais — é o que deixa a agência calcular o retorno do lado dela, e sem isso o
painel volta a ser contagem de lead.

O corte é de APRESENTAÇÃO: `finance/origens.py` devolve tudo pra todo mundo, e é
esta tela que decide o que desenha. Assim a regra fica num lugar que se lê.
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db.conexao import get_pool
from finance import origens as og
from finance import periodo as per
from finance import raio_x_perfil as rxp
# a barra de abas de Prospecção: Origens virou uma delas em 07/09/2026 — pra quem
# alcança Prospecção. O convidado da agência não alcança, e não vê barra nenhuma.
from web.painel_prospeccao import NAVBAR_CSS, _navbar
from web.portal import _env, _render, conta_logada, nicho_da_conta

router = APIRouter()

#: quem abre esta tela. O `convidado` é a agência de tráfego: entra por convite do
#: dono, vê isto e mais nada — o gate de web/app.py devolve todo o resto.
_PAPEIS_OK = ("dono", "gestor", "convidado")


def _pode_ver(request: Request):
    conta = conta_logada(request)
    if conta is None:
        return None, RedirectResponse("/login", status_code=303)
    if request.session.get("papel", "dono") not in _PAPEIS_OK:
        return None, RedirectResponse("/painel", status_code=303)
    return conta, None


def _brl(centavos) -> str:
    v = (int(centavos or 0)) / 100
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _min_txt(m) -> str:
    """Minutos em algo que se lê: 12 min, 3h20, 2 dias."""
    if m is None:
        return "—"
    m = int(m)
    if m < 60:
        return f"{m} min"
    if m < 60 * 24:
        h, r = divmod(m, 60)
        return f"{h}h{r:02d}" if r else f"{h}h"
    d = round(m / (60 * 24))
    return "1 dia" if d == 1 else f"{d} dias"


def _pct(n, de) -> int:
    return 0 if not de else round(100 * n / de)


@router.get("/painel/origens", response_class=HTMLResponse)
def painel_origens(request: Request):
    conta, redir = _pode_ver(request)
    if redir is not None:
        return redir
    pool = get_pool()
    # Regra 6: o vocabulário é do nicho. Quem vende festa marca VISITA; quem vende
    # serviço recorrente marca REUNIÃO; quem só vende produto não tem funil, e a
    # tela não se aplica.
    #
    # O slug sai de `nicho_da_conta`, não de um índice contado na mão: foi contando
    # posição que o `cidade` virou nicho e três telas sumiram sem erro nenhum
    # (#647). Também não vale reler do banco aqui — a tupla da sessão já traz o
    # slug, e uma segunda fonte é uma segunda chance de divergir.
    perfil = rxp.perfil(nicho_da_conta(conta))
    if not perfil["aplica"]:
        return RedirectResponse("/painel", status_code=303)
    q = request.query_params
    periodo = (q.get("periodo") or "7d").strip()
    if periodo not in dict(per.PERIODOS_ORIGENS):
        periodo = "7d"
    de, ate = q.get("de", ""), q.get("ate", "")
    ini, fim = per.intervalo(periodo, de, ate)
    d = og.dados_origens(pool, conta[0], ini, fim,
                         compromisso=perfil["vocab"]["compromisso"])
    convidado = request.session.get("papel", "dono") == "convidado"
    # A tela virou aba de Prospecção — pra quem entra em Prospecção. O convidado
    # tem `vendas: False`, cai aqui como página inicial e não alcança mais nada:
    # pra ele o menu lateral continua acendendo Origens, e a barra de abas nem
    # aparece (ver o `{% if caps.vendas %}` no template). Sem isso, a agência
    # ficaria com uma barra de abas onde não pode clicar em nada.
    return _render("origens", request, titulo="Origens",
                   secao_ativa=("origens" if convidado else "prospeccao"),
                   nav_ativo="origens", convidado=convidado,
                   d=d, periodo=periodo, periodos=per.PERIODOS_ORIGENS,
                   # o menu lateral lê `raio_x_perfil` pra decidir o que mostrar
                   raio_x_perfil=perfil,
                   de=de, ate=ate, ini=ini, fim=fim, perfil=perfil,
                   brl=_brl, min_txt=_min_txt, pct=_pct)


_ORIGENS_TPL = r"""{% extends "base" %}{% block conteudo %}
<style>""" + NAVBAR_CSS + r"""</style>
{% if caps.vendas %}""" + _navbar("origens") + r"""{% endif %}
<style>
.og-topo{display:flex;align-items:baseline;justify-content:space-between;gap:.8rem;flex-wrap:wrap}
.og-pills{display:flex;gap:.3rem;overflow-x:auto;padding:.7rem 0 .4rem;-webkit-overflow-scrolling:touch}
.og-pill{white-space:nowrap;padding:.35rem .7rem;border-radius:999px;font-size:.8rem;
  border:1px solid var(--borda);color:var(--txt-mut);text-decoration:none;flex:none}
.og-pill.on{background:var(--verde);border-color:var(--verde);color:var(--sobre-verde);font-weight:600}
.og-datas{display:flex;gap:.4rem;align-items:center;flex-wrap:wrap;margin:.2rem 0 1rem}
.og-datas input{background:var(--bg);border:1px solid var(--borda);border-radius:8px;
  padding:.35rem .55rem;color:var(--txt);font-size:.82rem}
.og-datas button{background:var(--verde);color:var(--sobre-verde);border:0;border-radius:8px;
  padding:.4rem .8rem;font-size:.8rem;font-weight:700;cursor:pointer}
.og-faixas{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.6rem;margin-bottom:1rem}
.og-cx{background:var(--card);border:1px solid var(--borda);border-radius:12px;padding:.7rem .85rem}
.og-cx .r{font-size:.7rem;text-transform:uppercase;letter-spacing:.06em;color:var(--txt-mut);display:block}
.og-cx .v{font-size:1.5rem;font-weight:700;line-height:1.2}
.og-cx .n{font-size:.76rem;color:var(--txt-mut)}
.og-cx.destaque{background:var(--neon-fundo);border-color:var(--neon-borda)}
.og-cx.destaque .v{color:var(--verde-claro)}
.og-funil{background:var(--card);border:1px solid var(--borda);border-radius:14px;padding:.9rem 1rem;margin-bottom:1rem}
.og-lin{display:grid;grid-template-columns:minmax(120px,1.1fr) minmax(0,2fr) auto;gap:.7rem;
  align-items:center;padding:.45rem 0;border-top:1px solid var(--borda)}
.og-lin:first-of-type{border-top:0}
.og-lin .et{font-size:.85rem;font-weight:600}
.og-trilho{height:22px;background:var(--bg);border:1px solid var(--borda);border-radius:6px;overflow:hidden}
.og-pre{height:100%;min-width:2.6rem;background:var(--verde);display:flex;align-items:center;padding-left:.5rem}
.og-pre b{font-size:.72rem;color:var(--sobre-verde)}
.og-taxa{font-size:.78rem;color:var(--txt-mut);text-align:right;white-space:nowrap}
.og-rol{overflow-x:auto;border:1px solid var(--borda);border-radius:12px}
.og-tab{border-collapse:collapse;width:100%;min-width:640px;font-size:.85rem}
.og-tab th{text-align:right;padding:.55rem .6rem;font-size:.68rem;text-transform:uppercase;
  letter-spacing:.05em;color:var(--txt-mut);font-weight:500;background:var(--card-2);
  border-bottom:1px solid var(--borda);white-space:nowrap}
.og-tab th:first-child,.og-tab td:first-child{text-align:left}
.og-tab td{text-align:right;padding:.5rem .6rem;border-bottom:1px solid var(--borda);white-space:nowrap}
.og-tab tr:last-child td{border-bottom:0}
.og-tab tr.sem td{color:var(--amar);font-style:italic}
.og-tab td.oculto{color:var(--txt-mut);opacity:.5}
.og-cod{font-family:ui-monospace,Menlo,monospace;background:var(--neon-fundo);color:var(--verde-claro);
  border:1px solid var(--neon-borda);border-radius:5px;padding:.05rem .35rem;font-size:.78rem}
.og-aviso{border-radius:11px;padding:.75rem .9rem;font-size:.85rem;line-height:1.5;margin-top:1rem}
.og-aviso.ambar{background:var(--ambar-fundo);border:1px solid var(--ambar-borda);color:#F0DCA6}
.og-aviso.azul{background:var(--azul-fundo);border:1px solid var(--azul-borda);color:#8FC9E6}
.og-vazio{text-align:center;color:var(--txt-mut);padding:2rem 1rem;font-size:.9rem}
.og-vazio code{background:var(--card-2);border:1px solid var(--borda);border-radius:5px;padding:.1rem .35rem}
</style>

<div class="og-topo">
  <h2 style="margin:0">Origens</h2>
  <span class="mut" style="font-size:.82rem">{{ ini.strftime('%d/%m/%Y') }} a {{ fim.strftime('%d/%m/%Y') }}</span>
</div>

<div class="og-pills">
  {% for chave, rot in periodos %}
  <a class="og-pill {% if chave == periodo %}on{% endif %}"
     href="/painel/origens?periodo={{ chave }}">{{ rot }}</a>
  {% endfor %}
</div>
{% if periodo == 'personalizado' %}
<form class="og-datas" method="get" action="/painel/origens">
  <input type="hidden" name="periodo" value="personalizado">
  <input type="date" name="de" value="{{ de }}" aria-label="De">
  <span class="mut">→</span>
  <input type="date" name="ate" value="{{ ate }}" aria-label="Até">
  <button type="submit">Aplicar</button>
</form>
{% endif %}

<div class="og-faixas">
  <div class="og-cx destaque"><span class="r">com código</span>
    <span class="v">{{ d.resumo.com_codigo }}</span>
    <div class="n">conversas de anúncio</div></div>
  <div class="og-cx"><span class="r">sem código</span>
    <span class="v">{{ d.resumo.sem_codigo }}</span>
    <div class="n">orgânico e afins</div></div>
  <div class="og-cx"><span class="r">total da casa</span>
    <span class="v">{{ d.resumo.total }}</span>
    <div class="n">no período</div></div>
  {# o faturamento aqui é só o das linhas COM código — o convidado pode vê-lo #}
  <div class="og-cx"><span class="r">faturamento</span>
    <span class="v">{{ brl(d.faturamento_centavos) }}</span>
    <div class="n">de anúncio, com sinal pago</div></div>
</div>

{% if d.resumo.com_codigo == 0 %}
<div class="og-funil og-vazio">
  <b>Nenhuma conversa com código de anúncio neste período.</b><br>
  O código vai na mensagem pronta do anúncio, entre colchetes e com cerquilha —
  por exemplo <code>Olá! Quero saber sobre o espaço. [#A3]</code>.<br>
  Um por criativo; o painel descobre os novos sozinho.
</div>
{% else %}
<div class="og-funil">
  {% for f in d.funil %}
  <div class="og-lin">
    <span class="et">{{ f.rotulo }}</span>
    <div class="og-trilho">
      <div class="og-pre" style="width:{{ pct(f.n, d.resumo.com_codigo) }}%"><b>{{ f.n }}</b></div>
    </div>
    <span class="og-taxa">{% if f.taxa %}{{ f.taxa.texto }}{% else %}—{% endif %}</span>
  </div>
  {% endfor %}
</div>

<div class="og-faixas">
  <div class="og-cx"><span class="r">atendidas</span>
    <span class="v">{{ d.atendimento.atendidas }}</span>
    <div class="n">de {{ d.resumo.com_codigo }}</div></div>
  <div class="og-cx"><span class="r">1ª resposta</span>
    <span class="v">{{ min_txt(d.atendimento.mediana_min) }}</span>
    <div class="n">mediana</div></div>
  <div class="og-cx"><span class="r">em até 1h</span>
    <span class="v">{{ d.atendimento.ate_1h }}</span>
    <div class="n">das atendidas</div></div>
  <div class="og-cx"><span class="r">sem resposta</span>
    <span class="v">{{ d.atendimento.sem_resposta }}</span>
    <div class="n">ninguém falou</div></div>
</div>

<div class="og-rol">
  <table class="og-tab">
    <thead><tr>
      <th>Origem</th><th>Conversas</th><th>Atendidas</th><th>1ª resposta</th>
      <th>{{ d.compromisso|capitalize }}s</th><th>Compareceu</th><th>Vendas</th><th>Faturamento</th>
    </tr></thead>
    <tbody>
      {% for l in d.linhas %}
      <tr>
        <td><span class="og-cod">{{ l.codigo }}</span></td>
        <td>{{ l.conversas }}</td><td>{{ l.atendidas }}</td>
        <td>{{ min_txt(l.resposta_mediana_min) }}</td>
        <td>{{ l.marcaram }}</td><td>{{ l.compareceram }}</td><td>{{ l.vendas }}</td>
        <td>{% if l.faturamento_centavos %}{{ brl(l.faturamento_centavos) }}{% else %}—{% endif %}</td>
      </tr>
      {% endfor %}
      {% if d.sem_codigo %}
      {# O convidado (a agência) vê a CONTAGEM desta faixa, porque parte dela é
         anúncio que perdeu o texto e é assim que ele percebe a atribuição
         vazando. O dinheiro que veio de fora do anúncio não é assunto dele. #}
      <tr class="sem">
        <td>sem código</td>
        <td>{{ d.sem_codigo.conversas }}</td><td>{{ d.sem_codigo.atendidas }}</td>
        <td>{{ min_txt(d.sem_codigo.resposta_mediana_min) }}</td>
        <td>{{ d.sem_codigo.marcaram }}</td><td>{{ d.sem_codigo.compareceram }}</td>
        {% if convidado %}
        <td class="oculto" title="visível para a empresa">—</td>
        <td class="oculto" title="visível para a empresa">—</td>
        {% else %}
        <td>{{ d.sem_codigo.vendas }}</td>
        <td>{% if d.sem_codigo.faturamento_centavos %}{{ brl(d.sem_codigo.faturamento_centavos) }}{% else %}—{% endif %}</td>
        {% endif %}
      </tr>
      {% endif %}
    </tbody>
  </table>
</div>

{% if d.cobertura.sem_desfecho %}
<div class="og-aviso ambar">
  <b>{{ d.cobertura.sem_desfecho }} de {{ d.cobertura.ja_passou }}
  {{ d.compromisso }}s que já aconteceram estão sem resposta</b> — ninguém marcou se
  o cliente apareceu. Enquanto isso, a taxa de comparecimento acima sai de uma
  amostra menor do que parece. O vendedor responde pelo Cockpit, no bloco
  “Precisa de resposta”.
</div>
{% endif %}
{% endif %}

{% if convidado %}
<div class="og-aviso azul">
  <b>O que fica com a empresa.</b> Vendas e faturamento da faixa “sem código” não
  aparecem aqui — é o que a casa vendeu por fora do anúncio. O faturamento das
  linhas com código está completo, que é o que você precisa pra cruzar com o
  investimento do seu lado.
</div>
{% endif %}
<div class="og-aviso azul">
  <b>“Sem código” não é só orgânico.</b> Junta quem chegou por indicação ou pelo
  Google com quem veio do anúncio e apagou o texto antes de enviar. O Zaq não tem
  como separar os dois — e dizer que tem seria pior do que não medir.
</div>
{% endblock %}"""

_env.loader.mapping["origens"] = _ORIGENS_TPL
