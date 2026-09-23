"""O recibo — link próprio, sem login: /recibo/<token>.

O cliente abre no celular, lê e imprime (ou salva em PDF pelo "imprimir" do
navegador). O texto vem CONGELADO de `recibos.dados` (ver `finance/recibo.py`):
o que o cliente vê é o que foi emitido, e editar o orçamento depois não muda o
papel. O visual segue a família do contrato e do aditivo, que é o que o cliente
da Prime já conhece.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse

from db.conexao import get_pool
from finance import recibo as rb
from web.portal import _env

router = APIRouter()
_log = logging.getLogger("recibo.publico")

_TPL_NOME = "recibo_publico.html"


@router.get("/recibo/{token}", response_class=HTMLResponse)
def recibo_publico(request: Request, token: str):
    try:
        r = rb.por_token(get_pool(), token)
    except Exception:  # noqa: BLE001
        # só o PREFIXO: o token cru é a credencial do link público
        _log.warning("não deu pra abrir o recibo do token %s…", (token or "")[:6],
                     exc_info=True)
        r = None
    html = _env.get_template(_TPL_NOME).render(r=r, d=(r or {}).get("dados") or {})
    return HTMLResponse(html, status_code=200 if r else 404)


_TPL = """<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>{% if r %}Recibo nº {{ r.rotulo }} — {{ d.empresa.nome }}{% else %}Recibo não encontrado{% endif %}</title>
{% raw %}<style>
*{box-sizing:border-box}
body{margin:0;background:#EFEBE2;font-family:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;color:#1F2937;padding:18px 12px}
.wrapc{max-width:720px;margin:0 auto}
.bar{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap}
.bar .who{font-size:13px;color:#5A6678}
.dl{background:#14213D;color:#F4F1EA;border:0;border-radius:9px;padding:10px 14px;font-size:13px;font-weight:600;cursor:pointer}
.pg{background:#fff;border-radius:14px;box-shadow:0 10px 30px rgba(20,33,61,.12);overflow:hidden}
.hd{display:flex;justify-content:space-between;align-items:flex-start;gap:16px;padding:22px 24px;border-bottom:2px solid #14213D}
.hdl{display:flex;gap:12px;align-items:center;min-width:0}
.lgo img{max-height:46px;max-width:120px;display:block}
.lg{font-size:17px;font-weight:700;color:#14213D;overflow-wrap:anywhere}
.sub{font-size:12px;color:#5A6678;margin-top:2px}
.mt{text-align:right;font-size:12px;color:#5A6678;white-space:nowrap}
.mt b{display:block;font-size:19px;color:#14213D;letter-spacing:.06em}
.mt i{display:block;font-style:normal;font-size:14px;font-weight:700;color:#B8862E}
.bd{padding:24px}
.txt{font-size:15px;line-height:1.75;color:#1F2937}
.txt b{color:#14213D}
.val{margin:14px 0 6px;font-size:26px;font-weight:700;color:#14213D;font-variant-numeric:tabular-nums}
.ext{font-size:13px;color:#5A6678;margin-bottom:14px}
.det{font-size:12.5px;color:#5A6678;margin-top:6px}
.fecho{margin-top:22px;font-size:14px}
.ass{margin:46px auto 0;max-width:320px;border-top:1px solid #14213D;padding-top:6px;text-align:center;font-size:13px;font-weight:600;color:#14213D}
.ass small{display:block;font-weight:400;color:#5A6678;font-size:11.5px}
.ft{margin-top:26px;padding-top:12px;border-top:1px solid #ECE7DC;font-size:10.5px;color:#8A8475;line-height:1.5;overflow-wrap:anywhere}
.nada{background:#fff;border-radius:14px;padding:28px;text-align:center;color:#5A6678}
@media(max-width:560px){.hd{flex-direction:column}.mt{text-align:left}.bd{padding:18px}.txt{font-size:14.5px}}
@media print{
  body{background:#fff;padding:0}
  .bar{display:none!important}
  .pg{box-shadow:none;border-radius:0}
  .wrapc{max-width:none}
  @page{size:A4;margin:16mm}
}
</style>{% endraw %}
</head><body><div class="wrapc">
{% if not r %}
  <div class="nada"><h2 style="margin-top:0;color:#14213D">Recibo não encontrado</h2>
    <p>Confira o link com quem te mandou.</p></div>
{% else %}
  <div class="bar">
    <span class="who">Recibo nº {{ r.rotulo }} · {{ d.empresa.nome }}</span>
    <button class="dl" onclick="window.print()">⬇ Baixar / imprimir</button>
  </div>
  <div class="pg">
    <div class="hd">
      <div class="hdl">
        {% if d.empresa.logo %}<span class="lgo"><img src="{{ d.empresa.logo }}" alt=""></span>{% endif %}
        <div>
          <div class="lg">{{ d.empresa.nome }}</div>
          <div class="sub">{% if d.empresa.doc %}CNPJ {{ d.empresa.doc }}{% endif %}{% if d.empresa.doc and d.empresa.cidade %} · {% endif %}{% if d.empresa.cidade %}{{ d.empresa.cidade }}{% if d.empresa.uf %}/{{ d.empresa.uf }}{% endif %}{% endif %}</div>
        </div>
      </div>
      <div class="mt"><b>RECIBO</b><i>nº {{ r.rotulo }}</i>{{ d.data_br }}</div>
    </div>
    <div class="bd">
      <div class="txt">
        Recebemos de <b>{{ d.pagador.nome }}</b>{% if d.pagador.doc %}, {{ 'CNPJ' if d.pagador.doc|length > 14 else 'CPF' }} {{ d.pagador.doc }}{% endif %}, a importância de
      </div>
      <div class="val">{{ d.valor }}</div>
      <div class="ext">({{ d.extenso }})</div>
      <div class="txt">
        referente {{ d.referente }}.{% if d.forma %} Forma de pagamento: {{ d.forma }}.{% endif %}
        {% if d.detalhe %}<div class="det">Valor composto por {{ d.detalhe }}.</div>{% endif %}
      </div>
      <div class="txt" style="margin-top:12px">
        Damos plena quitação <b>desta parcela</b>.{% if d.restante %} {{ d.restante }}{% endif %}
      </div>
      <div class="txt fecho">{{ d.fecho }}</div>
      <div class="ass">{{ d.empresa.nome }}{% if d.empresa.doc %}<small>CNPJ {{ d.empresa.doc }}</small>{% endif %}</div>
      <div class="ft">Emitido pelo Zaq em {{ r.emitido_em.strftime('%d/%m/%Y %H:%M') if r.emitido_em else d.data_br }}
        · {{ r.link }}</div>
    </div>
  </div>
{% endif %}
</div></body></html>
"""

# O `_env` do portal usa `select_autoescape()`, que decide pela EXTENSÃO do nome:
# registrar como .html é o que faz o Jinja escapar o nome do cliente e a
# descrição digitada da conta. Mesmo cuidado do contrato e do aditivo.
_env.loader.mapping[_TPL_NOME] = _TPL
