"""Contrato de locação — DOCUMENTO PRÓPRIO, com link próprio.

/contrato/<token>  (SEM login): o cliente abre, lê, imprime e ASSINA.

Por que fora da proposta. Até aqui o contrato saía como um bloco no rodapé da
folha do orçamento: mesma URL, mesma página. São dois documentos, com dois
aceites e dois momentos — a proposta aprova valores e vai ANTES do pagamento; o
contrato aceita cláusulas de cancelamento, multa e reagendamento, e só existe
DEPOIS da entrada. Empilhados na mesma página, o segundo lê como anexo do
primeiro, e é justamente o segundo que restringe direito do cliente.

O documento se qualifica sozinho: cabeçalho da empresa, as duas partes com
documento e endereço, os dados do evento e os valores saem dos campos que o
sistema já tem — não dependem de a empresa ter citado cada um dentro de uma
cláusula. As cláusulas vêm por cima disso.

Escopo de leitura por TOKEN, não por conta: quem tem o link vê aquele contrato e
só ele — mesmo desenho da proposta.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db.conexao import get_pool
from finance import contrato as ctr, servicos_catalogo as scat
from web.portal import _env

router = APIRouter()
_log = logging.getLogger("contrato.publico")


def _ip(request: Request) -> str:
    xf = (request.headers.get("x-forwarded-for") or "") if request else ""
    if xf:
        return xf.split(",")[0].strip()[:60]
    return ((request.client.host if (request and request.client) else "") or "")[:60]


def _doc(v: str) -> str:
    """CNPJ/CPF com máscara — o documento identifica as partes, então sai formatado."""
    d = "".join(ch for ch in (v or "") if ch.isdigit())
    if len(d) == 14:
        return f"{d[:2]}.{d[2:5]}.{d[5:8]}/{d[8:12]}-{d[12:]}"
    if len(d) == 11:
        return f"{d[:3]}.{d[3:6]}.{d[6:9]}-{d[9:]}"
    return (v or "").strip()


def _fone(v: str) -> str:
    d = "".join(ch for ch in (v or "") if ch.isdigit())
    if len(d) in (12, 13) and d.startswith("55"):
        d = d[2:]
    if len(d) in (10, 11):
        return f"({d[:2]}) {d[2:-4]}-{d[-4:]}"
    return (v or "").strip()


def _linha_end(*partes) -> str:
    return " · ".join(p for p in (str(x or "").strip() for x in partes) if p)


def carregar(token: str, pool=None) -> dict | None:
    """Tudo que o documento precisa, numa consulta por token.

    Devolve None quando o token não existe — a página responde 404 com texto de
    gente, e não com stack trace."""
    pool = pool or get_pool()
    ct = ctr.por_token(pool, token)
    if not ct:
        return None
    with pool.connection() as c:
        r = c.execute(
            """select o.cliente, o.empresa, o.cnpj, o.whatsapp, o.email, o.telefone,
                      o.endereco, o.cep, o.cidade, o.uf, o.numero, o.evento,
                      coalesce(o.primeiro_ano_centavos, o.setup_centavos, 0),
                      o.parcelas, o.status, o.sinal_pago_em,
                      ct.nome, ct.razao_social, ct.nome_fantasia, ct.documento,
                      ct.endereco, ct.bairro, ct.cep, ct.cidade, ct.uf,
                      ct.telefone, ct.email_empresa, ct.logo_url
                 from orcamentos o join contas ct on ct.id = o.conta_id
                where o.id=%s and o.conta_id=%s""",
            (ct["orcamento_id"], ct["conta_id"])).fetchone()
    if not r:
        return None
    (cli, emp_nome, cli_doc, whats, cli_email, cli_tel, cli_end, cli_cep, cli_cid,
     cli_uf, numero, evento, total, parcelas, orc_status, sinal_pago_em,
     c_nome, c_razao, c_fantasia, c_doc, c_end, c_bairro, c_cep, c_cid, c_uf,
     c_tel, c_email, c_logo) = r
    evento = evento if isinstance(evento, dict) else {}
    empresa = {"razao_social": c_razao or c_fantasia or c_nome or "",
               "nome_fantasia": c_fantasia or "", "documento": c_doc or "",
               "endereco": c_end or "", "bairro": c_bairro or "", "cep": c_cep or "",
               "cidade": c_cid or "", "uf": c_uf or "", "telefone": c_tel or "",
               "email_empresa": c_email or "", "logo_url": c_logo or ""}
    # nome: `empresa` primeiro — mesma regra de `_espelhar_cliente`
    # (web/painel_servicos.py) e de `contrato.contexto()`. O formulário troca o
    # RÓTULO de `empresa` pra "Nome completo" quando o cliente é pessoa física,
    # mas grava na mesma coluna; `cliente` vira "Contato/responsável" (pensado
    # pra PJ) e às vezes chega com um telefone que o agente de IA capturou
    # antes do nome — relato em produção: o LOCATÁRIO saiu identificado como
    # "86998192489" na folha do contrato.
    orcamento = {"cliente": emp_nome or cli or "", "empresa": emp_nome or "",
                 "cnpj": cli_doc or "", "whatsapp": whats or "", "email": cli_email or "",
                 "telefone": cli_tel or "", "endereco": cli_end or "", "cep": cli_cep or "",
                 "cidade": cli_cid or "", "uf": cli_uf or "", "numero": numero,
                 "setup_centavos": int(total or 0), "evento": evento}

    # "este orçamento pede entrada?" sai do mesmo lugar que a folha do cliente usa
    # pra prometer o valor do sinal — duas leituras diferentes seriam dois números.
    from finance import vendas
    tem_sinal = vendas.valor_do_sinal(parcelas) > 0

    assinado = bool(ct["assinado_em"])
    if assinado and ct.get("texto"):
        clausulas, faltas = ct["texto"], []
    else:
        modelo = ctr.carregar_modelo(pool, ct["conta_id"])
        ctx = ctr.contexto(catalogo=scat.listar(pool, ct["conta_id"]),
                           orcamento=orcamento, modelo=modelo, empresa=empresa)
        clausulas, faltas = ctr.montar(modelo["clausulas"], ctx)
    return {
        "contrato": ct, "clausulas": clausulas, "faltas": faltas, "assinado": assinado,
        "numero": ct["numero"], "token": ct["token"],
        "assinado_por": ct["assinado_por"],
        "assinado_em": (ct["assinado_em"].strftime("%d/%m/%Y às %H:%M")
                        if ct["assinado_em"] else ""),
        "criado_em": ct["criado_em"].strftime("%d/%m/%Y") if ct["criado_em"] else "",
        # AS PARTES, qualificadas pelos campos que o sistema já tem — não dependem
        # de a empresa ter citado cada um dentro de uma cláusula.
        "contratada": {
            "nome": empresa["razao_social"], "doc": _doc(empresa["documento"]),
            "endereco": _linha_end(empresa["endereco"], empresa["bairro"],
                                   _linha_end(empresa["cidade"], empresa["uf"]),
                                   empresa["cep"]),
            "contato": _linha_end(_fone(empresa["telefone"]), empresa["email_empresa"]),
            "logo": empresa["logo_url"],
        },
        "contratante": {
            "nome": orcamento["cliente"], "doc": _doc(orcamento["cnpj"]),
            "endereco": _linha_end(orcamento["endereco"],
                                   _linha_end(orcamento["cidade"], orcamento["uf"].upper()),
                                   orcamento["cep"]),
            "contato": _linha_end(_fone(orcamento["telefone"] or orcamento["whatsapp"]),
                                  orcamento["email"]),
        },
        "evento": {
            "tipo": evento.get("tipo") or "Evento",
            # mesma função que preenche {evento.data} nas cláusulas: a data no
            # quadro do objeto e a data no texto não podem sair diferentes
            "data": ctr.data_br(evento.get("data")),
            "horario": _linha_end(evento.get("inicio"), evento.get("fim")).replace(" · ", " às "),
            "local": evento.get("local") or "",
            "convidados": evento.get("convidados") or "",
        },
        "valor": ctr.reais(int(total or 0)),
        "orcamento_numero": numero,
        "orcamento_status": orc_status or "",
        # APROVOU, ASSINA. O contrato é amarrado ao ORÇAMENTO APROVADO e a mais nada
        # (decisão do dono em 01/09/2026).
        #
        # Até aqui a assinatura esperava o sinal cair. A ideia era boa no papel — a
        # cláusula 4.1 diz que a data só fica reservada com a entrada, então assinar
        # antes seria aceitar um contrato cuja primeira obrigação ainda não foi
        # cumprida. Na prática o porteiro custava mais do que guardava: quem confirma
        # o sinal só existe no desktop (funil e agenda), enquanto quem recebe o
        # comprovante no WhatsApp é o vendedor, no celular. O contrato ficava lido e
        # parado esperando alguém sentar no computador.
        #
        # E ASSINAR CEDO NÃO GARANTE DATA. A 4.1 continua valendo, palavra por
        # palavra: a data só é reservada depois da entrada. O que a assinatura
        # antecipa é o COMPROMISSO, não a reserva — são coisas diferentes, e é o
        # próprio contrato que diz isso.
        #
        # `sinal_pago` e `tem_sinal` seguem no dicionário: a página ainda os mostra
        # (é o que explica ao cliente o que falta pra data ficar firme), só não
        # mandam mais na assinatura.
        "aprovada": (orc_status or "") in ("aprovada", "fechado"),
        "sinal_pago": bool(sinal_pago_em),
        "tem_sinal": tem_sinal,
        "pode_assinar": ((orc_status or "") in ("aprovada", "fechado")
                         and not assinado),
    }


@router.get("/contrato/{token}", response_class=HTMLResponse)
def contrato_publico(request: Request, token: str, erro: str = ""):
    try:
        d = carregar(token)
    except Exception:  # noqa: BLE001
        _log.warning("não deu pra montar o contrato do token %s", token, exc_info=True)
        d = None
    html = _env.get_template(_TPL_NOME).render(d=d, token=token, erro=erro)
    return HTMLResponse(html, status_code=200 if d else 404)


@router.post("/contrato/{token}/assinar")
def contrato_assinar(request: Request, token: str, nome: str = Form(""),
                     doc: str = Form(""), aceite: str = Form("")):
    """O aceite das cláusulas — separado do aceite da proposta, de propósito.

    Congela o texto no ato: grava o que o cliente LEU, não uma referência ao
    modelo. Editar o modelo amanhã não reescreve o que foi aceito hoje."""
    if not (nome or "").strip() or aceite != "on":
        return RedirectResponse(
            f"/contrato/{token}?erro=Preencha+seu+nome+e+marque+o+aceite.", status_code=303)
    pool = get_pool()
    d = carregar(token, pool)
    # as travas revalidadas AQUI e não só na tela
    if not d or not d["pode_assinar"]:
        return RedirectResponse(f"/contrato/{token}", status_code=303)
    ctr.assinar(pool, d["contrato"]["conta_id"], d["contrato"]["id"], d["clausulas"],
                nome, doc, _ip(request))
    return RedirectResponse(f"/contrato/{token}", status_code=303)


_TPL = r"""{% if not d %}
<!doctype html><meta charset=utf-8><title>Contrato</title>
<div style="font-family:system-ui;max-width:520px;margin:12vh auto;text-align:center;color:#14213D">
  <h1 style="font-size:22px">Contrato não encontrado</h1>
  <p style="color:#8A8475">Esse link pode ter expirado ou estar incorreto. Peça um novo à empresa que enviou.</p>
</div>
{% else %}
<!doctype html><html lang=pt-br><head><meta charset=utf-8>
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Contrato nº {{ d.numero }} · {{ d.contratada.nome }}</title>
{% raw %}<style>
*{box-sizing:border-box;margin:0;padding:0}
body{font-family:system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:#14213D;background:#EEE9DD;padding:18px}
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
@media screen and (max-width:560px){.hd{flex-wrap:wrap}.hdl{flex:1 1 100%;flex-wrap:wrap}
  .hdl>div{flex:1 1 100%}.hd .mt{flex:1 1 100%;text-align:left;white-space:normal;margin-top:8px}}
.bd{padding:26px 40px 36px}
.eb{font-size:10px;letter-spacing:.14em;text-transform:uppercase;color:#B8862E;font-weight:600;margin:22px 0 8px}
.eb:first-child{margin-top:0}
.partes{display:grid;grid-template-columns:1fr 1fr;gap:12px}
@media(max-width:620px){.partes{grid-template-columns:1fr}}
.parte{background:#FBFAF7;border:1px solid #ECE7DC;border-radius:9px;padding:12px 14px;font-size:13px;line-height:1.5}
.parte .p{font-size:9.5px;letter-spacing:.12em;text-transform:uppercase;color:#B8862E;font-weight:700;margin-bottom:4px}
.parte b{font-size:14px}
.parte small{display:block;color:#5A6678;font-size:12px;margin-top:2px}
.ev{display:grid;grid-template-columns:repeat(4,1fr);gap:1px;background:#ECE7DC;border:1px solid #ECE7DC;border-radius:9px;overflow:hidden}
@media(max-width:620px){.ev{grid-template-columns:repeat(2,1fr)}}
.ev>div{background:#fff;padding:10px 12px}
.ev .k{font-size:9.5px;letter-spacing:.1em;text-transform:uppercase;color:#8A8475;font-weight:600}
.ev .v{font-size:14px;font-weight:600;margin-top:2px}
.ctrc{margin-bottom:13px;break-inside:avoid}
.ctrt{font-size:12.5px;font-weight:700;color:#14213D;margin-bottom:3px}
.ctrb{font-size:13px;line-height:1.6;color:#3B4757;white-space:pre-wrap}
.falta{background:#FFF6E8;border:1px solid #F0DDBA;border-radius:8px;padding:9px 11px;font-size:12px;color:#8A5A12;margin-bottom:12px}
.sign{margin-top:24px;background:#FBFAF7;border:1px solid #ECE7DC;border-radius:10px;padding:18px 20px}
.sign h3{font-size:15px;margin-bottom:6px}
.sign p{font-size:12.5px;color:#5A6678;line-height:1.55}
.row{display:flex;gap:9px;margin:12px 0 9px;flex-wrap:wrap}
.row input{flex:1;min-width:170px;padding:10px 12px;border:1px solid #DCD5C6;border-radius:8px;font-size:14px;font-family:inherit}
.ck{display:flex;gap:8px;align-items:flex-start;font-size:12.5px;color:#3B4757;line-height:1.45}
.go{margin-top:12px;width:100%;background:#14213D;color:#F4F1EA;border:0;border-radius:9px;padding:12px;font-size:14px;font-weight:600;cursor:pointer}
.err{background:#FDECEA;border:1px solid #F5C6C0;color:#B4453C;border-radius:8px;padding:9px 11px;font-size:12.5px;margin-bottom:9px}
.ok{margin-top:22px;background:#0e2a1f;border:1px solid #1c5c40;border-radius:10px;padding:16px 18px;color:#eafff5}
.ok h3{color:#7EE7B8;font-size:15px}.ok p{font-size:12.5px;color:#a9d9c4;margin-top:4px}
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
    <span class="who">Contrato nº {{ d.numero }} · {{ d.contratada.nome }}</span>
    <button class="dl" onclick="window.print()">⬇ Baixar / imprimir</button>
  </div>
  <div class="pg">
    <div class="hd">
      <div class="hdl">
        {% if d.contratada.logo %}<span class="lgo"><img src="{{ d.contratada.logo }}" alt=""></span>{% endif %}
        <div>
          <div class="lg">{{ d.contratada.nome }}</div>
          <div class="sub">Contrato de locação de espaço</div>
        </div>
      </div>
      <div class="mt"><b>Contrato</b>nº {{ d.numero }}<br>{{ d.criado_em }}</div>
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

      <div class="eb">Objeto</div>
      <div class="ev">
        <div><div class="k">Evento</div><div class="v">{{ d.evento.tipo }}</div></div>
        <div><div class="k">Data</div><div class="v">{{ d.evento.data or '—' }}</div></div>
        <div><div class="k">Horário</div><div class="v">{{ d.evento.horario or '—' }}</div></div>
        <div><div class="k">Convidados</div><div class="v">{{ d.evento.convidados or '—' }}</div></div>
      </div>
      {% if d.evento.local or d.valor %}
      <div class="ev" style="margin-top:8px;grid-template-columns:2fr 1fr 1fr">
        <div><div class="k">Local</div><div class="v">{{ d.evento.local or '—' }}</div></div>
        <div><div class="k">Valor total</div><div class="v">{{ d.valor }}</div></div>
        <div><div class="k">Orçamento</div><div class="v">nº {{ d.orcamento_numero or '—' }}</div></div>
      </div>
      {% endif %}

      <div class="eb">Cláusulas</div>
      {% if d.faltas %}
      <div class="falta">⚠️ Campos sem valor neste contrato: {{ d.faltas|join(', ') }}.
        Avise a {{ d.contratada.nome }} antes de assinar.</div>
      {% endif %}
      {% for c in d.clausulas %}
      <div class="ctrc"><div class="ctrt">{{ c.titulo }}</div><div class="ctrb">{{ c.corpo }}</div></div>
      {% endfor %}

      {% if d.assinado %}
      <div class="carimbo">✓ Assinado eletronicamente por <b>{{ d.assinado_por }}</b>
        em {{ d.assinado_em }} — registrado com nome, documento, data/hora e IP.</div>
      <div class="ok">
        <h3>✓ Contrato assinado</h3>
        <p>Guarde este link: ele é o seu contrato. Dá pra baixar em PDF pelo botão
        "Baixar / imprimir" no topo da página.</p>
      </div>
      {% elif not d.pode_assinar %}
      {# só sobra UM motivo pra não poder assinar: o orçamento ainda não foi
         aprovado. O sinal deixou de ser porteiro em 01/09/2026. #}
      <div class="carimbo">Você já pode ler o contrato inteiro.
        <b>A assinatura é liberada</b> quando o orçamento for aprovado.</div>
      {% else %}
      <form class="sign" method="post" action="/contrato/{{ token }}/assinar">
        <h3>✍️ Assinar o contrato</h3>
        <p>Ao assinar, você aceita todas as cláusulas acima — inclusive as de
        cancelamento, reagendamento e utilização excedente. Fica registrado com
        nome, CPF, data/hora e IP.</p>
        {# A ASSINATURA NÃO É A RESERVA. Antes o sinal travava o botão, e a ordem
           dizia isso sozinha; agora dá pra assinar antes de pagar, então quem
           precisa dizer é a tela — com a mesma regra da cláusula 4.1, não com uma
           promessa nova. #}
        {% if d.tem_sinal and not d.sinal_pago %}
        <div class="carimbo" style="margin:0 0 .9rem">A data só fica <b>definitivamente
          reservada</b> depois que a entrada for confirmada pela
          {{ d.contratada.nome }} (cláusula 4.1). Assinar agora firma o
          compromisso; a reserva vem com o pagamento.</div>
        {% endif %}
        {% if erro %}<div class="err">{{ erro }}</div>{% endif %}
        <div class="row">
          <input type="text" name="nome" placeholder="Seu nome completo" required>
          <input type="text" name="doc" placeholder="CPF">
        </div>
        <label class="ck"><input type="checkbox" name="aceite">
          <span>Li e concordo com todas as cláusulas deste contrato.</span></label>
        <button class="go" type="submit">✓ Assinar contrato</button>
      </form>
      {% endif %}

      <div class="ft">Assinatura eletrônica registrada com nome, documento, data/hora e IP —
      validade jurídica conforme MP 2.200-2/2001. Este contrato acompanha o orçamento
      nº {{ d.orcamento_numero or '—' }} e com ele forma o acordo entre as partes.</div>
    </div>
  </div>
</div></body></html>
{% endif %}"""

# O NOME TERMINA EM .html DE PROPÓSITO, e é a única coisa que liga o autoescape.
#
# O `_env` do portal usa `select_autoescape()`, que decide pela EXTENSÃO do nome
# do template. Os templates deste sistema são registrados no DictLoader com nomes
# sem extensão ('proposta', 'servicos', 'base'), então caem no `default=False` e
# saem CRUS. Conferido: `from_string` escapa, `get_template('nome')` não.
#
# Esta página é pública e sem login, e joga na tela dois valores que vêm da URL:
# `{{ erro }}` (a faixa vermelha) e `{{ token }}` (dentro do action do form).
# Sem escape, `?erro="><script>…` executa no navegador de quem abrir o link — e o
# link é justamente o que o dono manda por WhatsApp, então basta o atacante
# reenviar o mesmo link com a query trocada.
#
# Escapar campo a campo resolveria os dois que eu lembrei hoje; o autoescape
# resolve também os que forem acrescentados amanhã.
_TPL_NOME = "contrato_doc.html"
_env.loader.mapping[_TPL_NOME] = _TPL
