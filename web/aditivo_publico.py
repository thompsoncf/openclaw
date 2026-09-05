"""Termo aditivo — link próprio, assinatura própria.

/aditivo/<token>  (SEM login): o cliente abre, lê o que muda, imprime e ASSINA.

POR QUE PÁGINA SEPARADA DO CONTRATO

Pelo mesmo motivo que o contrato saiu de dentro da proposta: são dois aceites e
dois momentos. O contrato foi assinado em setembro; o aditivo é uma decisão nova,
tomada meses depois, sobre pontos específicos. Empilhado no fim do contrato, o
aditivo leria como anexo — e é justamente ele que muda a data do casamento.

O DOCUMENTO SE QUALIFICA SOZINHO, e reaproveitando a mesma fonte

Cabeçalho da empresa, as duas partes com documento e endereço: tudo sai de
`contrato_publico.qualificacao`, a MESMA função que monta o contrato. Não é
economia de linha — é que contrato e aditivo dizendo nomes diferentes das mesmas
partes, no mesmo negócio, é defeito jurídico. Quando o CPF do cadastro entra no
contrato, entra aqui junto.

O QUE ESTA PÁGINA MOSTRA A MAIS QUE O CONTRATO

O quadro "antes → depois". O papel do dono escreve isso dentro do texto ("passa a
ser 140, em substituição à quantidade originalmente estabelecida de 115"), e o
texto continua escrevendo. Mas quem abre no celular decide em três segundos se
está certo — e pra isso o de→para tem que estar visível sem ler cláusula.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db.conexao import get_pool
from finance import aditivo as ad
from finance import contrato as ctr
from web.contrato_publico import _ip, qualificacao
from web.portal import _env

router = APIRouter()
_log = logging.getLogger("aditivo.publico")

_TPL_NOME = "aditivo_publico.html"


def _linhas_do_quadro(aditivo: dict, est: dict) -> list[dict]:
    """O 'antes → depois' que a página mostra em cima das cláusulas."""
    from finance.contrato import data_br, reais
    linhas = []
    for a in (aditivo.get("alteracoes") or []):
        campo = a.get("campo")
        if campo == "data":
            linhas.append({"o_que": "Data do evento",
                           "de": data_br(a.get("de")), "para": data_br(a.get("para"))})
        elif campo == "horario":
            de, para = a.get("de") or {}, a.get("para") or {}
            linhas.append({
                "o_que": "Horário",
                "de": f"{de.get('inicio') or '—'} às {de.get('fim') or '—'}",
                "para": f"{para.get('inicio') or '—'} às {para.get('fim') or '—'}"})
        elif campo == "convidados":
            linhas.append({"o_que": "Convidados",
                           "de": str(a.get("de") or "—"), "para": str(a.get("para") or "—")})
        elif campo == "servicos":
            linhas.append({"o_que": "Serviços",
                           "de": ", ".join(str(x) for x in (a.get("saem") or [])) or "—",
                           "para": (a.get("entram") or "—")})
    dif = int(aditivo.get("diferenca_centavos") or 0)
    taxa = int(aditivo.get("taxa_centavos") or 0)
    if dif or taxa:
        linhas.append({"o_que": "Valor do contrato",
                       "de": reais(int(aditivo.get("valor_antes_centavos") or 0)),
                       "para": reais(int(aditivo.get("valor_novo_centavos") or 0))})
    return linhas


def carregar(token: str, pool=None) -> dict | None:
    """Tudo que o aditivo precisa. None quando o token não existe ou foi cancelado
    (cancelar apaga o token, então cai no mesmo 404 de gente)."""
    pool = pool or get_pool()
    a = ad.por_token(pool, token)
    if not a:
        return None
    est = ad.estado_atual(pool, a["conta_id"], a["contrato_id"])
    if not est:
        return None
    q = qualificacao(pool, a["conta_id"], est.get("orcamento_id"))
    if not q:
        return None

    assinado = bool(a["assinado_em"])
    # congelado quando assinado, montado ao vivo antes — mesma regra do contrato
    clausulas = a["texto"] if (assinado and a.get("texto")) else ad.clausulas(a, est)
    return {
        "aditivo": a, "clausulas": clausulas, "assinado": assinado,
        "rotulo": ad.ordinal(a["ordem"]), "token": a["token"],
        "contrato_numero": est.get("contrato_numero"),
        "contrato_em": (est["contrato_em"].strftime("%d/%m/%Y")
                        if est.get("contrato_em") else ""),
        "criado_em": a["criado_em"].strftime("%d/%m/%Y") if a["criado_em"] else "",
        "assinado_por": a["assinado_por"],
        "assinado_em": (a["assinado_em"].strftime("%d/%m/%Y às %H:%M")
                        if a["assinado_em"] else ""),
        "contratada": q["contratada"], "contratante": q["contratante"],
        "evento": q["evento"], "orcamento_numero": q["orcamento_numero"],
        "quadro": _linhas_do_quadro(a, est),
        "disposicoes": ad.DISPOSICOES, "fecho": ad.FECHO,
        "cidade_uf": ", ".join(x for x in [q["empresa"].get("cidade"),
                                           q["empresa"].get("uf")] if x),
        "valor_novo": ctr.reais(int(a.get("valor_novo_centavos") or 0)),
        # cancelado nunca chega aqui (o token é apagado); sobra assinado ou não
        "pode_assinar": not assinado,
    }


@router.get("/aditivo/{token}", response_class=HTMLResponse)
def aditivo_publico(request: Request, token: str, erro: str = ""):
    try:
        d = carregar(token)
    except Exception:  # noqa: BLE001
        _log.warning("não deu pra montar o aditivo do token %s", token, exc_info=True)
        d = None
    html = _env.get_template(_TPL_NOME).render(d=d, token=token, erro=erro)
    return HTMLResponse(html, status_code=200 if d else 404)


@router.post("/aditivo/{token}/assinar")
def aditivo_assinar(request: Request, token: str, nome: str = Form(""),
                    doc: str = Form(""), aceite: str = Form("")):
    """O aceite das alterações.

    Congela o texto no ato e só então grava de volta — ver `finance.aditivo`. As
    travas são revalidadas AQUI, e não só na tela: esconder o formulário não
    impede ninguém de montar a chamada."""
    if not (nome or "").strip() or aceite != "on":
        return RedirectResponse(
            f"/aditivo/{token}?erro=Preencha+seu+nome+e+marque+o+aceite.",
            status_code=303)
    pool = get_pool()
    d = carregar(token, pool)
    if not d or not d["pode_assinar"]:
        return RedirectResponse(f"/aditivo/{token}", status_code=303)
    ad.assinar(pool, d["aditivo"]["id"], nome, doc, _ip(request), d["clausulas"])
    return RedirectResponse(f"/aditivo/{token}", status_code=303)


_TPL = r"""{% if not d %}
<!doctype html><meta charset=utf-8><title>Termo aditivo</title>
<div style="font-family:system-ui;max-width:520px;margin:12vh auto;text-align:center;color:#14213D">
  <h1 style="font-size:22px">Termo aditivo não encontrado</h1>
  <p style="color:#8A8475">Esse link pode ter sido cancelado ou estar incorreto.
  Peça um novo à empresa que enviou.</p>
</div>
{% else %}
<!doctype html><html lang=pt-br><head><meta charset=utf-8>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{{ d.rotulo }} aditivo ao contrato nº {{ d.contrato_numero }} · {{ d.contratada.nome }}</title>
{% raw %}<style>
*{box-sizing:border-box}
body{margin:0;background:#EFEBE3;font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,Arial,sans-serif;color:#14213D;padding:22px 14px}
.wrapc{max-width:794px;margin:0 auto}
.bar{display:flex;justify-content:space-between;align-items:center;gap:10px;margin-bottom:12px;flex-wrap:wrap}
.bar .who{font-size:12.5px;color:#5A6678}
.bar .dl{background:#14213D;color:#F4F1EA;border:0;border-radius:9px;padding:9px 16px;font-size:13px;font-weight:600;cursor:pointer}
.pg{background:#fff;border-radius:6px;overflow:hidden;box-shadow:0 20px 50px -25px rgba(20,33,61,.4)}
.hd{background:#14213D;color:#F4F1EA;padding:28px 40px;display:flex;justify-content:space-between;align-items:flex-start;gap:14px}
.hdl{display:flex;align-items:flex-start;gap:12px;flex:1;min-width:0}
.hdl>div{min-width:0;overflow-wrap:anywhere}
.lgo{background:#fff;border-radius:8px;padding:6px 8px;display:inline-flex;align-items:center;justify-content:center;flex:0 0 auto}
.lgo img{height:44px;width:auto;max-width:160px;object-fit:contain;display:block}
.hd .lg{font-size:20px;font-weight:600;line-height:1.2}
.hd .sub{font-size:10px;letter-spacing:.2em;text-transform:uppercase;color:#9FA8BC;margin-top:5px}
.hd .mt{text-align:right;font-size:11px;color:#9FA8BC;flex:0 0 auto;white-space:nowrap}
.hd .mt b{display:block;color:#E0B458;font-size:10px;letter-spacing:.14em;text-transform:uppercase;margin-bottom:4px}
.bd{padding:26px 40px 36px}
.eb{font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:#B8862E;font-weight:600;margin:22px 0 8px}
.eb:first-child{margin-top:0}
.partes{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:620px){.partes{grid-template-columns:1fr}}
.parte{background:#FBFAF7;border:1px solid #ECE7DC;border-radius:9px;padding:12px 14px;font-size:13px;line-height:1.5}
.parte .p{font-size:9.5px;letter-spacing:.12em;text-transform:uppercase;color:#B8862E;font-weight:700;margin-bottom:4px}
.parte b{font-size:14px}
.parte small{display:block;color:#5A6678;font-size:12px;margin-top:2px}
.ev{display:grid;grid-template-columns:1fr 1fr 2fr;gap:1px;background:#ECE7DC;border:1px solid #ECE7DC;border-radius:9px;overflow:hidden}
@media(max-width:620px){.ev{grid-template-columns:1fr}}
.ev>div{background:#fff;padding:10px 12px}
.ev .k{font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;color:#8A8475;font-weight:600}
.ev .v{font-size:14px;font-weight:600;margin-top:2px}
.vig{font-size:12.5px;color:#5A6678;margin-top:9px;line-height:1.55}
.qd{width:100%;border-collapse:collapse;font-size:13px;border:1px solid #ECE7DC;border-radius:9px;overflow:hidden}
.qd th{background:#FBFAF7;text-align:left;padding:8px 12px;font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;color:#8A8475;font-weight:700;border-bottom:1px solid #ECE7DC}
.qd td{padding:9px 12px;border-bottom:1px solid #F4F1EA;vertical-align:top}
.qd tr:last-child td{border-bottom:0}
.qd .de{color:#A6A093;text-decoration:line-through}
.qd .para{font-weight:700;color:#14213D}
.ctrc{margin-bottom:13px;break-inside:avoid}
.ctrt{font-size:12.5px;font-weight:700;color:#14213D;margin-bottom:3px}
.ctrb{font-size:13px;line-height:1.6;color:#3B4757;white-space:pre-wrap}
.sign{margin-top:24px;background:#FBFAF7;border:1px solid #ECE7DC;border-radius:10px;padding:18px 20px}
.sign h3{font-size:15px;margin:0 0 6px}
.sign p{font-size:12.5px;color:#5A6678;line-height:1.55;margin:0}
.row{display:flex;gap:9px;margin:12px 0 9px;flex-wrap:wrap}
.row input{flex:1;min-width:170px;padding:10px 12px;border:1px solid #DCD5C6;border-radius:8px;font-size:14px;font-family:inherit}
.ck{display:flex;gap:8px;align-items:flex-start;font-size:12.5px;color:#3B4757;line-height:1.45}
.go{margin-top:12px;width:100%;background:#14213D;color:#F4F1EA;border:0;border-radius:9px;padding:12px;font-size:14px;font-weight:600;cursor:pointer}
.err{background:#FDECEA;border:1px solid #F5C6C0;color:#B4453C;border-radius:8px;padding:9px 11px;font-size:12.5px;margin-bottom:9px}
.ok{margin-top:22px;background:#0e2a1f;border:1px solid #1c5c40;border-radius:10px;padding:16px 18px;color:#eafff5}
.ok h3{color:#7EE7B8;font-size:15px;margin:0}.ok p{font-size:12.5px;color:#a9d9c4;margin-top:4px}
.carimbo{margin-top:22px;border:1px dashed #C9BFA8;border-radius:9px;padding:14px 16px;font-size:12.5px;color:#3B4757;line-height:1.6}
.carimbo b{color:#14213D}
.ft{margin-top:22px;padding-top:12px;border-top:1px solid #ECE7DC;font-size:10.5px;color:#8A8475;line-height:1.5}
@media print{
  body{background:#fff;padding:0}
  .bar,.sign{display:none!important}
  .pg{box-shadow:none;border-radius:0}
  .hd{background:#fff;color:#14213D;border-bottom:2px solid #14213D;padding:0 0 14px}
  .hd .sub,.hd .mt{color:#5A6678}.hd .mt b{color:#B8862E}
  .lgo{background:transparent;padding:0}
  .bd{padding:16px 0 0}
  .wrapc{max-width:none}
  @page{size:A4;margin:14mm}
}
</style>{% endraw %}
</head><body><div class="wrapc">
  <div class="bar">
    <span class="who">{{ d.rotulo }} termo aditivo ao contrato nº {{ d.contrato_numero }}</span>
    <button class="dl" onclick="window.print()">⬇ Baixar / imprimir</button>
  </div>
  <div class="pg">
    <div class="hd">
      <div class="hdl">
        {% if d.contratada.logo %}<span class="lgo"><img src="{{ d.contratada.logo }}" alt=""></span>{% endif %}
        <div>
          <div class="lg">{{ d.contratada.nome }}</div>
          <div class="sub">Termo aditivo ao contrato de locação</div>
        </div>
      </div>
      <div class="mt"><b>{{ d.rotulo }} Aditivo</b>ao contrato nº {{ d.contrato_numero }}<br>{{ d.criado_em }}</div>
    </div>
    <div class="bd">

      <div class="eb">Partes</div>
      <div class="partes">
        <div class="parte">
          <div class="p">Contratada (locadora)</div>
          <b>{{ d.contratada.nome }}</b>
          {% if d.contratada.doc %}<small>CNPJ {{ d.contratada.doc }}</small>{% endif %}
          {% if d.contratada.endereco %}<small>{{ d.contratada.endereco }}</small>{% endif %}
          {% if d.contratada.contato %}<small>{{ d.contratada.contato }}</small>{% endif %}
        </div>
        <div class="parte">
          <div class="p">Contratante (locatário)</div>
          <b>{{ d.contratante.nome }}</b>
          {% if d.contratante.doc %}<small>CPF/CNPJ {{ d.contratante.doc }}</small>{% endif %}
          {% if d.contratante.endereco %}<small>{{ d.contratante.endereco }}</small>{% endif %}
          {% if d.contratante.contato %}<small>{{ d.contratante.contato }}</small>{% endif %}
        </div>
      </div>

      <div class="eb">Contrato original</div>
      <div class="ev">
        <div><div class="k">Contrato</div><div class="v">nº {{ d.contrato_numero }}</div></div>
        <div><div class="k">Celebrado em</div><div class="v">{{ d.contrato_em or '—' }}</div></div>
        <div><div class="k">Objeto</div><div class="v">{{ d.evento.tipo }}{% if d.orcamento_numero %} · orçamento nº {{ d.orcamento_numero }}{% endif %}</div></div>
      </div>
      <div class="vig">Cujas cláusulas permanecem em vigor, exceto naquilo que forem
        alteradas pelo presente instrumento.</div>

      {% if d.quadro %}
      <div class="eb">O que muda</div>
      <table class="qd">
        <tr><th style="width:34%">&nbsp;</th><th style="width:30%">Como está</th><th>Passa a ser</th></tr>
        {% for l in d.quadro %}
        <tr><td><b>{{ l.o_que }}</b></td><td class="de">{{ l.de }}</td><td class="para">{{ l.para }}</td></tr>
        {% endfor %}
      </table>
      {% endif %}

      <div class="eb">Objeto do termo aditivo</div>
      {% for c in d.clausulas %}
      <div class="ctrc"><div class="ctrt">{{ c.titulo }}</div><div class="ctrb">{{ c.corpo }}</div></div>
      {% endfor %}

      <div class="eb">Disposições gerais</div>
      <div class="ctrb">{{ d.disposicoes }}</div>

      <div class="eb">Assinatura das partes</div>
      <div class="ctrb">{{ d.fecho }}</div>

      {% if d.assinado %}
      <div class="carimbo">✓ Assinado eletronicamente por <b>{{ d.assinado_por }}</b>
        em {{ d.assinado_em }} — registrado com nome, documento, data/hora e IP.</div>
      <div class="ok">
        <h3>✓ Termo aditivo assinado</h3>
        <p>Guarde este link: ele é o seu termo aditivo. O contrato original continua
        valendo em tudo o que não foi alterado aqui.</p>
      </div>
      {% else %}
      <form class="sign" method="post" action="/aditivo/{{ token }}/assinar">
        <h3>✍️ Assinar o termo aditivo</h3>
        <p>Ao assinar, você aceita as alterações acima. O contrato
        nº {{ d.contrato_numero }} continua valendo em tudo o mais. Fica registrado
        com nome, CPF, data/hora e IP.</p>
        {% if erro %}<div class="err" style="margin-top:.8rem">{{ erro }}</div>{% endif %}
        <div class="row">
          <input name="nome" placeholder="Seu nome completo" required
                 value="{{ d.contratante.nome }}">
          <input name="doc" placeholder="CPF ou CNPJ" value="{{ d.contratante.doc }}">
        </div>
        <label class="ck"><input type="checkbox" name="aceite" value="on" required>
          <span>Li e aceito as alterações descritas neste termo aditivo.</span></label>
        <button class="go" type="submit">Assinar o termo aditivo</button>
      </form>
      {% endif %}

      <div class="ft">{{ d.cidade_uf }}{% if d.cidade_uf %}, {% endif %}{{ d.criado_em }}
        · Documento gerado eletronicamente por {{ d.contratada.nome }}.</div>
    </div>
  </div>
</div></body></html>
{% endif %}
"""

# O `_env` do portal usa `select_autoescape()`, que decide pela EXTENSÃO do nome:
# registrar como .html é o que faz o Jinja escapar nome e documento digitados pelo
# cliente. Mesmo cuidado (e mesmo comentário) da folha do contrato.
_env.loader.mapping[_TPL_NOME] = _TPL
