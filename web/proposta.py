"""Proposta pública — o link que o vendedor manda pro cliente.

/proposta/<token>  (SEM login): o cliente vê a proposta com a marca da empresa
que vendeu, baixa em PDF (impressão A4 do navegador) e APROVA/ASSINA (nome +
aceite; registra nome, data/hora e IP — assinatura eletrônica simples).

Assinar só marca a proposta como 'aprovada' (o vendedor fecha o contrato depois,
no funil, gerando os títulos a receber). Escopo de leitura por TOKEN, não por
conta — quem tem o link vê aquela proposta e só ela.
"""
from datetime import date, timedelta

from fastapi import APIRouter, BackgroundTasks, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from db.conexao import get_pool
from web.portal import _env

router = APIRouter()


def _notificar_assinatura(d: dict, assinante: str) -> None:
    """Avisa a empresa (Telegram + e-mail) que o cliente assinou. Roda em
    background e é tolerante a falha — nunca afeta a resposta pro cliente."""
    try:
        from finance import notificar
        notificar.avisar_proposta_assinada(
            get_pool(), d["conta_id"], d.get("empresa") or d.get("contato") or "cliente",
            assinante, d.get("ano1", ""), d.get("criado_por"))
    except Exception:  # noqa: BLE001
        pass


def _reais(v) -> str:
    return "R$ " + format(int(v or 0), ",").replace(",", ".")


def _ip(request: Request) -> str:
    xf = request.headers.get("x-forwarded-for", "")
    if xf:
        return xf.split(",")[0].strip()[:60]
    return (request.client.host if request.client else "")[:60]


def registrar_assinatura(pool, token: str, nome: str, doc: str, ip: str) -> bool:
    """Grava a assinatura (idempotente): só assina se ainda não foi aprovada/fechada.
    Devolve True se assinou agora."""
    nome = (nome or "").strip()
    if not nome:
        return False
    with pool.connection() as c:
        r = c.execute(
            """update orcamentos
                  set aprovada_por=%s, aprovada_doc=%s, aprovada_ip=%s,
                      aprovada_em=now(), status='aprovada', atualizado_em=now()
                where token=%s and status not in ('aprovada','fechado')
              returning id""",
            (nome[:120], (doc or "").strip()[:40] or None, (ip or "")[:60], token)).fetchone()
        c.commit()
    return bool(r)


def _carregar(token: str, pool=None):
    with (pool or get_pool()).connection() as c:
        r = c.execute(
            """select o.id, o.empresa, o.cliente, o.whatsapp, o.segmento, o.escopo,
                      o.itens, o.setup_centavos, o.mensal_centavos, o.primeiro_ano_centavos,
                      o.status, o.criado_em, o.aprovada_por, o.aprovada_em, c.nome,
                      o.conta_id, o.criado_por
                 from orcamentos o join contas c on c.id = o.conta_id
                where o.token=%s""", (token,)).fetchone()
    if not r:
        return None
    itens = r[6]
    if isinstance(itens, str):
        import json
        try:
            itens = json.loads(itens)
        except Exception:
            itens = []
    criado = r[11].date() if r[11] else date.today()
    return {
        "id": r[0], "empresa": r[1] or "Cliente", "contato": r[2] or "",
        "whats": r[3] or "", "segmento": r[4] or "",
        "escopo": r[5] or "", "itens": itens or [],
        "setup": _reais((r[7] or 0) / 100), "mensal": _reais((r[8] or 0) / 100),
        "ano1": _reais((r[9] or 0) / 100),
        "status": r[10] or "rascunho",
        "criado": criado, "validade": criado + timedelta(days=15),
        "aprovada_por": r[12] or "", "aprovada_em": r[13],
        "vendedor": r[14] or "Proposta",
        "conta_id": r[15], "criado_por": r[16],
        "doc_num": "PR-%s-%03d" % (criado.strftime("%Y%m"), (r[0] or 0) % 1000),
    }


@router.get("/proposta/{token}", response_class=HTMLResponse)
def proposta_publica(request: Request, token: str, erro: str = ""):
    d = _carregar(token)
    if not d:
        return HTMLResponse(_env.get_template("proposta").render(prop=None), status_code=404)
    linhas = [{"nome": (it.get("nome") or ""), "desc": (it.get("desc") or ""),
               "setup": _reais(it.get("setup")), "mensal": _reais(it.get("mensal"))}
              for it in d["itens"]]
    _MES = ["", "janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
            "agosto", "setembro", "outubro", "novembro", "dezembro"]
    d["data_str"] = f"{d['criado'].day} de {_MES[d['criado'].month]} de {d['criado'].year}"
    d["validade_str"] = d["validade"].strftime("%d/%m/%Y")
    d["aprovada_str"] = d["aprovada_em"].strftime("%d/%m/%Y às %H:%M") if d["aprovada_em"] else ""
    d["assinada"] = d["status"] in ("aprovada", "fechado")
    return HTMLResponse(_env.get_template("proposta").render(prop=d, linhas=linhas,
                                                             token=token, erro=erro))


@router.post("/proposta/{token}/assinar")
def proposta_assinar(request: Request, background: BackgroundTasks, token: str,
                     nome: str = Form(""), doc: str = Form(""), aceite: str = Form("")):
    if not (nome or "").strip() or aceite != "on":
        return RedirectResponse(
            f"/proposta/{token}?erro=Preencha+seu+nome+e+marque+o+aceite.", status_code=303)
    if registrar_assinatura(get_pool(), token, nome, doc, _ip(request)):
        d = _carregar(token)
        if d:
            background.add_task(_notificar_assinatura, d, nome.strip())
    return RedirectResponse(f"/proposta/{token}", status_code=303)


# ---------------------------------------------------------------- template
_PROPOSTA_TPL = r"""{% if not prop %}
<!doctype html><meta charset=utf-8><title>Proposta</title>
<div style="font-family:system-ui;max-width:520px;margin:12vh auto;text-align:center;color:#14213D">
  <h1 style="font-size:22px">Proposta não encontrada</h1>
  <p style="color:#8A8475">Esse link pode ter expirado ou estar incorreto. Peça um novo à empresa que enviou.</p>
</div>
{% else %}
<!doctype html><html lang=pt-br><head><meta charset=utf-8>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Proposta · {{ prop.vendedor }}</title>
{% raw %}<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:#14213D;background:#EEE9DD;padding:18px}
.wrapc{max-width:794px;margin:0 auto}
.bar{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap}
.bar .who{font-size:12.5px;color:#5A6678}
.bar .dl{background:#14213D;color:#F4F1EA;border:0;border-radius:9px;padding:9px 16px;font-size:13px;font-weight:600;cursor:pointer}
.pg{background:#fff;border-radius:6px;overflow:hidden;box-shadow:0 20px 50px -25px rgba(20,33,61,.4)}
.hd{background:#14213D;color:#F4F1EA;padding:30px 40px;display:flex;justify-content:space-between;align-items:flex-start;gap:14px;flex-wrap:wrap}
.hd .lg{font-size:23px;font-weight:600}.hd .lg span{color:#E0B458}
.hd .sub{font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:#9FA8BC;margin-top:5px}
.hd .mt{text-align:right;font-size:11px;color:#9FA8BC}.hd .mt b{display:block;color:#E0B458;font-size:10px;letter-spacing:.14em;text-transform:uppercase;margin-bottom:4px}
.bd{padding:28px 40px 38px}.eb{font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:#B8862E;font-weight:600;margin:20px 0 8px}
.cli{background:#FBFAF7;border:1px solid #ECE7DC;border-radius:9px;padding:13px 15px;font-size:14px}
p.es{font-size:13.5px;line-height:1.7;color:#3A4254}
table{width:100%;border-collapse:collapse;margin-top:4px}
th{font-size:10px;text-transform:uppercase;letter-spacing:.05em;color:#9FA8BC;background:#14213D;padding:9px 13px;text-align:left}
th.r,td.r{text-align:right;font-family:ui-monospace,Menlo,monospace;white-space:nowrap}
td{padding:11px 13px;border-bottom:1px solid #F0EBE0;font-size:13.5px}td small{display:block;color:#A8A192;font-size:11.5px;margin-top:2px}
.tot{display:grid;grid-template-columns:1fr 1fr;gap:12px;margin-top:22px}
.bx{border:1px solid #ECE7DC;border-radius:12px;padding:16px}.bx .l{font-size:10px;letter-spacing:.1em;text-transform:uppercase;color:#8A8475;font-weight:600}.bx .v{font-family:ui-monospace,Menlo,monospace;font-size:21px;font-weight:600;margin-top:6px}
.fin{margin-top:12px;background:#14213D;border-radius:12px;padding:18px 20px;display:flex;justify-content:space-between;align-items:center;gap:10px;flex-wrap:wrap}.fin .l{font-size:10px;letter-spacing:.12em;text-transform:uppercase;color:#E0B458;font-weight:600}.fin .v{font-family:ui-monospace,Menlo,monospace;font-size:26px;font-weight:600;color:#FBFAF7}
.sign{margin-top:16px;background:#0e2a1f;border:1px solid #1c5c40;border-radius:12px;padding:20px 22px;color:#eafff5}
.sign h3{font-size:15px;margin-bottom:4px;color:#7EE7B8}
.sign p{font-size:12.5px;color:#a9d9c4;margin-bottom:14px;line-height:1.5}
.sign .row{display:flex;gap:10px;flex-wrap:wrap}
.sign input[type=text]{flex:1;min-width:170px;background:transparent;border:1px solid #2a6b4c;border-radius:8px;color:#fff;padding:10px 12px;font-size:14px}
.sign .ck{display:flex;align-items:flex-start;gap:8px;font-size:12.5px;color:#cdece0;margin:12px 0}
.sign .ck input{margin-top:2px}
.sign .go{background:#12b981;color:#04140d;border:0;border-radius:9px;padding:12px 22px;font-weight:700;font-size:14px;cursor:pointer}
.sign .err{background:#3a1a1a;border:1px solid #7a3a3a;color:#ffd7d7;border-radius:8px;padding:8px 12px;font-size:12.5px;margin-bottom:12px}
.ok{margin-top:16px;background:#0e2a1f;border:1px solid #1c5c40;border-radius:12px;padding:20px 22px;color:#eafff5}
.ok h3{color:#7EE7B8;font-size:16px}.ok p{font-size:13px;color:#a9d9c4;margin-top:4px}
.ft{margin-top:20px;font-size:11px;color:#8A8475;line-height:1.6}
@media print{
  body{background:#fff;padding:0}
  .bar,.sign,.ft{display:none!important}
  .pg{box-shadow:none;border-radius:0}
  @page{size:A4;margin:12mm}
}
</style>{% endraw %}
</head><body><div class="wrapc">
  <div class="bar">
    <span class="who">🔒 Proposta enviada por <b>{{ prop.vendedor }}</b> · válida até {{ prop.validade_str }}</span>
    <button class="dl" onclick="window.print()">⬇ Baixar PDF</button>
  </div>

  <div class="pg">
    <div class="hd">
      <div><div class="lg">{{ prop.vendedor }} <span>·</span></div><div class="sub">Proposta comercial</div></div>
      <div class="mt"><b>Proposta comercial</b>{{ prop.doc_num }}<br>{{ prop.data_str }}</div>
    </div>
    <div class="bd">
      <div class="eb">Preparada para</div>
      <div class="cli"><b>{{ prop.empresa }}</b>{% if prop.contato %} · {{ prop.contato }}{% endif %}{% if prop.whats %} · {{ prop.whats }}{% endif %}{% if prop.segmento %}<br><span style="color:#A8A192">Segmento: {{ prop.segmento }}</span>{% endif %}</div>
      {% if prop.escopo %}<div class="eb">Escopo da solução</div><p class="es">{{ prop.escopo }}</p>{% endif %}
      {% if linhas %}
      <div class="eb">Serviços contratados</div>
      <table><tr><th>Serviço</th><th class="r">Setup</th><th class="r">Mensal</th></tr>
        {% for l in linhas %}<tr><td><b>{{ l.nome }}</b>{% if l.desc %}<small>{{ l.desc }}</small>{% endif %}</td><td class="r">{{ l.setup }}</td><td class="r">{{ l.mensal }}</td></tr>{% endfor %}
      </table>
      {% endif %}
      <div class="tot">
        <div class="bx"><div class="l">Investimento inicial</div><div class="v">{{ prop.setup }}</div></div>
        <div class="bx"><div class="l">Mensalidade</div><div class="v">{{ prop.mensal }}</div></div>
      </div>
      <div class="fin"><div class="l">Total estimado · 1º ano</div><div class="v">{{ prop.ano1 }}</div></div>
    </div>
  </div>

  {% if prop.assinada %}
  <div class="ok">
    {% if prop.aprovada_por %}
    <h3>✓ Proposta aprovada</h3>
    <p>Aprovada por <b>{{ prop.aprovada_por }}</b>{% if prop.aprovada_str %} em {{ prop.aprovada_str }}{% endif %}. A empresa foi notificada e dará os próximos passos.</p>
    {% else %}
    <h3>✓ Proposta fechada</h3>
    <p>Esta proposta já foi contratada. Fale com a empresa para os próximos passos.</p>
    {% endif %}
  </div>
  {% else %}
  <form class="sign" method="post" action="/proposta/{{ token }}/assinar">
    <h3>✍️ Aprovar e assinar</h3>
    <p>Ao aprovar, você aceita esta proposta e autoriza o início. Fica registrado com seu nome, data/hora e IP.</p>
    {% if erro %}<div class="err">{{ erro }}</div>{% endif %}
    <div class="row">
      <input type="text" name="nome" placeholder="Seu nome completo" required>
      <input type="text" name="doc" placeholder="CPF (opcional)">
    </div>
    <label class="ck"><input type="checkbox" name="aceite"> Li e concordo com os termos e valores desta proposta.</label>
    <button class="go" type="submit">✓ Aprovar e assinar proposta</button>
  </form>
  {% endif %}

  <div class="ft">Valores em reais (BRL). Assinatura eletrônica registrada com nome, data/hora e IP — validade jurídica conforme MP 2.200-2/2001. Proposta válida até {{ prop.validade_str }}.</div>
</div></body></html>
{% endif %}"""

_env.loader.mapping["proposta"] = _PROPOSTA_TPL
