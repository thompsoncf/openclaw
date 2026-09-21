"""A tela Cotações: /painel/cotacoes — o preço antes de a apólice existir.

É a porta de DENTRO das três que o dono pediu em 21/09/2026 (as outras são
`web/api_cotacao.py`, pro site da corretora, e a ferramenta do WhatsApp em
`finance/tools_pj.py`). As três chamam `finance/cotacao.py`; nenhuma tem regra
própria — duas implementações seriam dois preços pro mesmo risco.

SÓ APARECE PRA CORRETORA (regra 6 do CLAUDE.md): o perfil do nicho tem que ser
`seguros`, exatamente como em Renovações. Uma tela de cotação de seguro numa conta
de festa é o erro que a regra 6 nasceu pra impedir.

O CAMINHO DA TELA, que é o caminho do trabalho dela:

    risco → ofertas → escolher → proposta na carteira

E ele funciona INTEIRO sem provedor nenhum contratado: no modo 'manual' (o
padrão) o corretor digita as ofertas que levantou e o resto é idêntico. Foi a
decisão de desenho que fez este recurso nascer útil no dia 1 em vez de esperar
contrato de terceiro.

QUEM VÊ O QUÊ: o corretor vê as cotações dele; dono e gestor veem as da conta —
mesmo recorte da carteira de apólices e da Fila do vendedor. As CHAVES da API são
só da gerência: chave de API é credencial da empresa, não ferramenta de venda.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db.conexao import get_pool
from finance import apolices as ap
from finance import cotacao as ct
from finance import cotacao_provedores as cp
from finance import raio_x_perfil as rxp
from web.portal import _env, _render, conta_logada, nicho_da_conta

router = APIRouter()
_log = logging.getLogger("openclaw.painel_cotacao")

_PAPEIS_OK = ("dono", "gestor", "vendedor")


def _acesso(request: Request):
    """(conta, gerencia, redirect) — o mesmo gate de Renovações, pelo mesmo motivo."""
    conta = conta_logada(request)
    if conta is None:
        return None, False, RedirectResponse("/login", status_code=303)
    papel = request.session.get("papel", "dono")
    if papel not in _PAPEIS_OK:
        return None, False, RedirectResponse("/painel", status_code=303)
    if rxp.perfil_por_nicho(nicho_da_conta(conta)) != "seguros":
        return None, False, RedirectResponse("/painel", status_code=303)
    return conta, papel in ("dono", "gestor"), None


def _brl(centavos) -> str:
    if centavos is None:
        return "—"
    v = (int(centavos or 0)) / 100
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _cent(txt: str) -> int:
    """'4.088,57' / '4088.57' / '' → centavos. Mesmo conversor de Renovações."""
    t = (txt or "").strip().replace("R$", "").replace(" ", "")
    if not t:
        return 0
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return int(round(float(t) * 100))
    except ValueError:
        return 0


def _membro(request: Request) -> int | None:
    return request.session.get("membro_id")


def _volta(cotacao_id: int, erro: str = "", aviso: str = "") -> RedirectResponse:
    from urllib.parse import urlencode
    q = {k: v for k, v in (("erro", erro), ("aviso", aviso)) if v}
    destino = f"/painel/cotacoes/{cotacao_id}" + (f"?{urlencode(q)}" if q else "")
    return RedirectResponse(destino, status_code=303)


# ──────────────────────────────────────────────────────────────── a lista

@router.get("/painel/cotacoes", response_class=HTMLResponse)
def painel_cotacoes(request: Request):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    pool, conta_id = get_pool(), conta[0]
    so_meu = None if gerencia else _membro(request)
    prov = cp.provedor_ativo(pool, conta_id)
    return _render("cotacoes", request, titulo="Cotações", secao_ativa="cotacoes",
                   gerencia=gerencia,
                   cotacoes=ct.listar(pool, conta_id, corretor_id=so_meu),
                   usos=ct.USOS_AUTO, garagens=ct.GARAGENS,
                   provedor=prov, manual=(prov.chave == "manual"),
                   chaves=(ct.chaves(pool, conta_id) if gerencia else []),
                   nova_chave=(request.query_params.get("chave") or "").strip(),
                   erro=(request.query_params.get("erro") or "").strip(),
                   aviso=(request.query_params.get("aviso") or "").strip())


@router.post("/painel/cotacoes")
def nova_cotacao(request: Request,
                 nome: str = Form(""), cpf: str = Form(""), nascimento: str = Form(""),
                 telefone: str = Form(""), cep: str = Form(""),
                 placa: str = Form(""), fipe: str = Form(""),
                 marca_modelo: str = Form(""), ano_modelo: str = Form(""),
                 ano_fabricacao: str = Form(""), uso: str = Form("particular"),
                 garagem: str = Form("nao"), bonus: str = Form("0"),
                 zero_km: str = Form(""), jovem_em_casa: str = Form(""),
                 vigencia_inicio: str = Form(""), obs: str = Form("")):
    """Cria a cotação e JÁ MANDA COTAR. Um clique, não dois: 'salvar e depois
    cotar' obrigaria o corretor a lembrar do segundo passo com o cliente no
    telefone. No modo manual, cotar não faz nada e a tela abre pronta pra digitar
    as ofertas — que é exatamente o que ele ia fazer a seguir."""
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    pool, conta_id = get_pool(), conta[0]
    try:
        risco = ct.normalizar_risco({
            "nome": nome, "cpf": cpf, "nascimento": nascimento, "telefone": telefone,
            "cep": cep, "placa": placa, "fipe": fipe, "marca_modelo": marca_modelo,
            "ano_modelo": ano_modelo, "ano_fabricacao": ano_fabricacao, "uso": uso,
            "garagem": garagem, "bonus": bonus, "zero_km": bool(zero_km),
            "jovem_em_casa": bool(jovem_em_casa),
            "vigencia_inicio": vigencia_inicio, "obs": obs})
    except ct.CotacaoErro as e:
        from urllib.parse import urlencode
        return RedirectResponse("/painel/cotacoes?" + urlencode({"erro": str(e)}),
                                status_code=303)
    cid = ct.criar(pool, conta_id, risco, corretor_id=_membro(request), origem="painel")
    try:
        ct.cotar(pool, conta_id, cid)
    except Exception as e:  # noqa: BLE001 — a cotação já existe; cotar de novo é um botão
        _log.warning("cotação %s criada mas não cotou: %s: %s", cid, type(e).__name__, e)
    return RedirectResponse(f"/painel/cotacoes/{cid}", status_code=303)


# ─────────────────────────────────────────────────────────────── o detalhe

@router.get("/painel/cotacoes/{cotacao_id}", response_class=HTMLResponse)
def ver_cotacao(request: Request, cotacao_id: int):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    pool, conta_id = get_pool(), conta[0]
    cot = ct.ler(pool, conta_id, cotacao_id)
    if not cot:
        return RedirectResponse("/painel/cotacoes", status_code=303)
    prov = cp.provedor_ativo(pool, conta_id)
    escolhida = next((o for o in cot["ofertas"] if o["escolhida"]), None)
    return _render("cotacao", request, titulo="Cotação", secao_ativa="cotacoes",
                   gerencia=gerencia, cot=cot, escolhida=escolhida,
                   provedor=prov, manual=(prov.chave == "manual"),
                   ramos=ap.RAMOS, brl=_brl,
                   # o roteiro é montado aqui e não no template: é texto pra
                   # COPIAR inteiro, e template que monta texto pra copiar acaba
                   # colando indentação de HTML no meio do CPF
                   roteiro=(ct.roteiro_do_portal(cot, escolhida)
                            if request.query_params.get("roteiro") and escolhida else ""),
                   erro=(request.query_params.get("erro") or "").strip(),
                   aviso=(request.query_params.get("aviso") or "").strip())


@router.post("/painel/cotacoes/{cotacao_id}/cotar")
def recotar(request: Request, cotacao_id: int):
    """Pede o preço de HOJE. Substitui as ofertas — preço de seguro vence, e
    comparar o de ontem com o de hoje lado a lado sem dizer seria mentir na tela."""
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    ct.cotar(get_pool(), conta[0], cotacao_id)
    return _volta(cotacao_id)


@router.post("/painel/cotacoes/{cotacao_id}/oferta")
def oferta_manual(request: Request, cotacao_id: int,
                  seguradora: str = Form(""), produto: str = Form(""),
                  premio_total: str = Form(""), premio_liquido: str = Form(""),
                  iof: str = Form(""), franquia: str = Form(""),
                  parcelas: str = Form(""), ref_externa: str = Form("")):
    """A oferta que o corretor levantou por fora. É o que faz a tela servir hoje.

    ACRESCENTA, não substitui: aqui quem cotou foi a pessoa, uma seguradora de
    cada vez — apagar as anteriores a cada linha nova tornaria o comparativo
    impossível de montar à mão.
    """
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    pool, conta_id = get_pool(), conta[0]
    total, liquido = _cent(premio_total), _cent(premio_liquido)
    if not (seguradora or "").strip() or total <= 0:
        return _volta(cotacao_id, erro="Informe a seguradora e o prêmio total.")
    try:
        nova = ct.Oferta(
            seguradora=seguradora.strip(), produto=produto.strip(),
            premio_total_centavos=total,
            premio_liquido_centavos=(liquido or None),
            iof_centavos=(_cent(iof) or None),
            franquia_centavos=(_cent(franquia) or None),
            parcelas=(int(parcelas) if (parcelas or "").strip().isdigit() else None),
            ref_externa=(ref_externa.strip() or None))
        ct.acrescentar_oferta(pool, conta_id, cotacao_id, nova)
    except (ValueError, ct.CotacaoErro) as e:
        return _volta(cotacao_id, erro=str(e))
    return _volta(cotacao_id)


@router.post("/painel/cotacoes/{cotacao_id}/escolher")
def escolher_oferta(request: Request, cotacao_id: int, oferta_id: str = Form("")):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    try:
        ct.escolher(get_pool(), conta[0], cotacao_id, int(oferta_id))
    except (ValueError, ct.CotacaoErro) as e:
        return _volta(cotacao_id, erro=str(e))
    return _volta(cotacao_id)


@router.post("/painel/cotacoes/{cotacao_id}/emitir")
def emitir(request: Request, cotacao_id: int):
    """O "envio de dados pra apólice" do pedido — e o fallback quando não há API.

    Automático: a proposta nasce com número e a apólice entra na carteira.
    Manual: devolve o roteiro pro portal da seguradora, e a proposta entra na
    carteira do mesmo jeito — a corretora tem UMA carteira, não uma das
    automáticas e outra das manuais.
    """
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    pool, conta_id = get_pool(), conta[0]
    try:
        r = ct.enviar_para_emissao(pool, conta_id, cotacao_id)
    except ct.CotacaoErro as e:
        return _volta(cotacao_id, erro=str(e))
    if r.get("automatico"):
        return _volta(cotacao_id, aviso=f"Proposta {r.get('numero_proposta') or ''} "
                                        "enviada — já está na carteira.")
    from urllib.parse import urlencode
    return RedirectResponse(f"/painel/cotacoes/{cotacao_id}?" + urlencode({"roteiro": "1"}),
                            status_code=303)


@router.post("/painel/cotacoes/{cotacao_id}/proposta")
def gerar_proposta(request: Request, cotacao_id: int,
                   vigencia_inicio: str = Form(""), numero_proposta: str = Form("")):
    """Digitei no portal, a seguradora aceitou: põe na carteira com o número."""
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    pool, conta_id = get_pool(), conta[0]
    try:
        apolice_id = ct.virar_proposta(pool, conta_id, cotacao_id,
                                       vigencia_inicio=(vigencia_inicio or None),
                                       numero_proposta=(numero_proposta.strip() or None))
    except ct.CotacaoErro as e:
        return _volta(cotacao_id, erro=str(e))
    _log.info("cotação %s virou a apólice %s", cotacao_id, apolice_id)
    return RedirectResponse("/painel/renovacoes?aba=carteira", status_code=303)


@router.post("/painel/cotacoes/{cotacao_id}/cliente")
def virar_cliente(request: Request, cotacao_id: int):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    try:
        ct.vincular_cliente(get_pool(), conta[0], cotacao_id)
    except ct.CotacaoErro as e:
        return _volta(cotacao_id, erro=str(e))
    return _volta(cotacao_id, aviso="Segurado na carteira de clientes.")


@router.post("/painel/cotacoes/{cotacao_id}/perder")
def perder(request: Request, cotacao_id: int, motivo: str = Form("")):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    ct.perder(get_pool(), conta[0], cotacao_id, motivo)
    return RedirectResponse("/painel/cotacoes", status_code=303)


# ──────────────────────────────────────────────────────── as chaves da API

@router.post("/painel/cotacoes/chaves/nova")
def nova_chave(request: Request, rotulo: str = Form("")):
    """Emite a chave e devolve o token UMA vez, na querystring da volta.

    Na querystring e não no banco: o token não é guardado em lugar nenhum (só o
    sha256), então ou ele aparece agora ou não aparece nunca. A tela diz isso em
    letra grande — é a troca que todo provedor sério faz.
    """
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    if not gerencia:
        return RedirectResponse("/painel/cotacoes", status_code=303)
    token = ct.criar_chave(get_pool(), conta[0], rotulo, criado_por=_membro(request))
    from urllib.parse import urlencode
    return RedirectResponse("/painel/cotacoes?" + urlencode({"chave": token}),
                            status_code=303)


@router.post("/painel/cotacoes/chaves/{chave_id}/revogar")
def revogar_chave(request: Request, chave_id: int):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    if not gerencia:
        return RedirectResponse("/painel/cotacoes", status_code=303)
    ct.revogar_chave(get_pool(), conta[0], chave_id)
    return RedirectResponse("/painel/cotacoes?aviso=Chave+revogada.", status_code=303)


# ───────────────────────────────────────────────────────────────── a tela

_CSS = r"""
<style>
.cot-topo{display:flex;align-items:baseline;justify-content:space-between;gap:.8rem;flex-wrap:wrap}
.cot-prov{font-size:.74rem;color:var(--txt-mut);border:1px solid var(--borda);
  border-radius:999px;padding:.2rem .6rem;white-space:nowrap}
.cot-cx{background:var(--card);border:1px solid var(--borda);border-radius:14px;
  padding:.9rem 1rem;margin:.9rem 0}
.cot-cx h3{margin:0 0 .6rem;font-size:.95rem}
.cot-grade{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.6rem}
.cot-campo{display:flex;flex-direction:column;gap:.2rem}
.cot-campo label{font-size:.7rem;text-transform:uppercase;letter-spacing:.05em;color:var(--txt-mut)}
.cot-campo input,.cot-campo select,.cot-campo textarea{background:var(--bg);color:var(--txt);
  border:1px solid var(--borda);border-radius:8px;padding:.4rem .55rem;font-size:.86rem;width:100%}
.cot-bt{background:var(--verde);color:var(--sobre-verde);border:0;border-radius:8px;
  padding:.45rem .9rem;font-size:.84rem;font-weight:700;cursor:pointer}
.cot-bt.fraco{background:transparent;color:var(--txt-mut);border:1px solid var(--borda);font-weight:500}
.cot-lin{display:flex;align-items:center;justify-content:space-between;gap:.7rem;
  padding:.6rem .2rem;border-top:1px solid var(--borda);text-decoration:none;color:inherit}
.cot-lin:first-of-type{border-top:0}
.cot-lin .q{font-size:.9rem;font-weight:600}
.cot-lin .s{font-size:.76rem;color:var(--txt-mut)}
.cot-selo{font-size:.7rem;border-radius:999px;padding:.12rem .5rem;white-space:nowrap;
  border:1px solid var(--borda);color:var(--txt-mut)}
.cot-selo.ok{background:var(--neon-fundo);border-color:var(--neon-borda);color:var(--verde-claro)}
.cot-selo.ruim{background:var(--ambar-fundo);border-color:var(--ambar-borda);color:#F0DCA6}
.cot-rol{overflow-x:auto;border:1px solid var(--borda);border-radius:12px}
.cot-tab{border-collapse:collapse;width:100%;min-width:620px;font-size:.85rem}
.cot-tab th{text-align:right;padding:.5rem .6rem;font-size:.68rem;text-transform:uppercase;
  letter-spacing:.05em;color:var(--txt-mut);font-weight:500;background:var(--card-2);
  border-bottom:1px solid var(--borda);white-space:nowrap}
.cot-tab th:first-child,.cot-tab td:first-child{text-align:left}
.cot-tab td{text-align:right;padding:.5rem .6rem;border-bottom:1px solid var(--borda);white-space:nowrap}
.cot-tab tr.escolhida td{background:var(--neon-fundo)}
.cot-aviso{border-radius:11px;padding:.7rem .9rem;font-size:.84rem;line-height:1.5;margin:.7rem 0}
.cot-aviso.azul{background:var(--azul-fundo);border:1px solid var(--azul-borda);color:#8FC9E6}
.cot-aviso.ambar{background:var(--ambar-fundo);border:1px solid var(--ambar-borda);color:#F0DCA6}
.cot-token{font-family:ui-monospace,Menlo,monospace;font-size:.8rem;word-break:break-all;
  background:var(--bg);border:1px solid var(--neon-borda);border-radius:8px;padding:.5rem .6rem;display:block}
.cot-vazio{text-align:center;color:var(--txt-mut);padding:1.6rem 1rem;font-size:.88rem}
.cot-roteiro{white-space:pre-wrap;font-family:ui-monospace,Menlo,monospace;font-size:.78rem;
  background:var(--bg);border:1px solid var(--borda);border-radius:10px;padding:.7rem .8rem;
  max-height:22rem;overflow:auto}
</style>
"""

_TPL_LISTA = r"""{% extends "base" %}{% block conteudo %}""" + _CSS + r"""
<div class="cot-topo">
  <h2 style="margin:0">Cotações</h2>
  <span class="cot-prov">{{ provedor.nome }}</span>
</div>

{% if erro %}<div class="cot-aviso ambar">{{ erro }}</div>{% endif %}
{% if aviso %}<div class="cot-aviso azul">{{ aviso }}</div>{% endif %}

{% if nova_chave %}
<div class="cot-cx" style="border-color:var(--neon-borda)">
  <h3>Sua chave de API — copie agora</h3>
  <code class="cot-token">{{ nova_chave }}</code>
  <p class="s" style="font-size:.8rem;color:var(--txt-mut);margin:.5rem 0 0">
    Ela não é guardada em lugar nenhum: se fechar esta página sem copiar, é só emitir
    outra e revogar esta. Use no cabeçalho <code>Authorization: Bearer …</code>.</p>
</div>
{% endif %}

<div class="cot-cx">
  <h3>Nova cotação — auto</h3>
  <form method="post" action="/painel/cotacoes">
    <div class="cot-grade">
      <div class="cot-campo"><label>Nome do segurado</label><input name="nome" autocomplete="off"></div>
      <div class="cot-campo"><label>CPF *</label><input name="cpf" inputmode="numeric" required></div>
      <div class="cot-campo"><label>Nascimento *</label><input type="date" name="nascimento" required></div>
      <div class="cot-campo"><label>Telefone</label><input name="telefone" inputmode="tel"></div>
      <div class="cot-campo"><label>CEP *</label><input name="cep" inputmode="numeric" required></div>
      <div class="cot-campo"><label>Placa</label><input name="placa" autocapitalize="characters"></div>
      <div class="cot-campo"><label>Código FIPE</label><input name="fipe"></div>
      <div class="cot-campo"><label>Marca / modelo</label><input name="marca_modelo"></div>
      <div class="cot-campo"><label>Ano do modelo</label><input name="ano_modelo" inputmode="numeric"></div>
      <div class="cot-campo"><label>Ano de fabricação</label><input name="ano_fabricacao" inputmode="numeric"></div>
      <div class="cot-campo"><label>Uso</label><select name="uso">
        {% for c, r in usos %}<option value="{{ c }}">{{ r }}</option>{% endfor %}</select></div>
      <div class="cot-campo"><label>Garagem</label><select name="garagem">
        {% for c, r in garagens %}<option value="{{ c }}">{{ r }}</option>{% endfor %}</select></div>
      <div class="cot-campo"><label>Classe de bônus</label><input name="bonus" inputmode="numeric" value="0"></div>
      <div class="cot-campo"><label>Início da vigência</label><input type="date" name="vigencia_inicio"></div>
    </div>
    <p style="font-size:.78rem;color:var(--txt-mut);margin:.6rem 0 .3rem">
      O veículo entra por FIPE, placa <b>ou</b> marca/modelo + ano — qualquer um serve.</p>
    <div style="display:flex;gap:1rem;align-items:center;flex-wrap:wrap;margin:.4rem 0">
      <label style="font-size:.82rem"><input type="checkbox" name="zero_km" value="1"> Zero km</label>
      <label style="font-size:.82rem"><input type="checkbox" name="jovem_em_casa" value="1"> Jovem de 17 a 25 em casa</label>
    </div>
    <div class="cot-campo" style="margin:.4rem 0 .7rem"><label>Observação</label><input name="obs"></div>
    <button class="cot-bt" type="submit">Cotar</button>
  </form>
</div>

{% if manual %}
<div class="cot-aviso azul">
  <b>Nenhum multicálculo conectado ainda.</b> Você cria a cotação aqui, digita as
  ofertas que levantou em cada seguradora e o sistema faz o resto: compara, guarda
  o histórico e transforma a escolhida em proposta na carteira — sem redigitar.
  Quando a corretora contratar uma API de cotação, as ofertas passam a chegar
  sozinhas e esta tela não muda.
</div>
{% endif %}

<div class="cot-cx">
  <h3>{% if gerencia %}Cotações da corretora{% else %}Minhas cotações{% endif %}</h3>
  {% for c in cotacoes %}
  <a class="cot-lin" href="/painel/cotacoes/{{ c.id }}">
    <span>
      <span class="q">{{ c.resumo }}</span><br>
      <span class="s">{{ c.criado_em.strftime('%d/%m/%Y') }} · {{ c.origem_txt }}
        {% if c.cliente_nome %}· {{ c.cliente_nome }}{% endif %}</span>
    </span>
    <span class="cot-selo {% if c.situacao in ('proposta','escolhida') %}ok{% elif c.situacao in ('falhou','perdida') %}ruim{% endif %}">{{ c.situacao_txt }}</span>
  </a>
  {% else %}
  <div class="cot-vazio">Nenhuma cotação ainda. A primeira nasce no formulário acima.</div>
  {% endfor %}
</div>

{% if gerencia %}
<div class="cot-cx">
  <h3>Chaves da API</h3>
  <p style="font-size:.82rem;color:var(--txt-mut);margin:0 0 .6rem">
    Pro site da corretora cotar de fora: <code>POST /api/v1/cotacoes</code> com o
    cabeçalho <code>Authorization: Bearer &lt;chave&gt;</code>. As cotações que
    entrarem por aí aparecem nesta lista com a origem <b>API</b>.</p>
  {% for k in chaves %}
  <div class="cot-lin">
    <span>
      <span class="q">{{ k.rotulo or 'sem rótulo' }}</span><br>
      <span class="s">{{ k.prefixo }}… · criada em {{ k.criado_em.strftime('%d/%m/%Y') }}
        {% if k.ultimo_uso_em %}· usada em {{ k.ultimo_uso_em.strftime('%d/%m/%Y') }}
        {% else %}· nunca usada{% endif %}</span>
    </span>
    {% if k.ativa %}
    <form method="post" action="/painel/cotacoes/chaves/{{ k.id }}/revogar">
      <button class="cot-bt fraco" type="submit">Revogar</button></form>
    {% else %}<span class="cot-selo ruim">revogada</span>{% endif %}
  </div>
  {% endfor %}
  <form method="post" action="/painel/cotacoes/chaves/nova"
        style="display:flex;gap:.5rem;align-items:flex-end;margin-top:.8rem;flex-wrap:wrap">
    <div class="cot-campo" style="flex:1;min-width:180px">
      <label>Pra que é esta chave</label><input name="rotulo" placeholder="site da corretora"></div>
    <button class="cot-bt" type="submit">Emitir chave</button>
  </form>
</div>
{% endif %}
{% endblock %}"""


_TPL_DETALHE = r"""{% extends "base" %}{% block conteudo %}""" + _CSS + r"""
<div class="cot-topo">
  <h2 style="margin:0">{{ cot.resumo }}</h2>
  <span class="cot-selo {% if cot.situacao in ('proposta','escolhida') %}ok{% elif cot.situacao in ('falhou','perdida') %}ruim{% endif %}">{{ cot.situacao_txt }}</span>
</div>
<p style="font-size:.8rem;color:var(--txt-mut);margin:.2rem 0 0">
  Cotação #{{ cot.id }} · {{ cot.origem_txt }} · {{ cot.criado_em.strftime('%d/%m/%Y %H:%M') }}
  · <a href="/painel/cotacoes">voltar</a></p>

{% if erro %}<div class="cot-aviso ambar">{{ erro }}</div>{% endif %}
{% if aviso %}<div class="cot-aviso azul">{{ aviso }}</div>{% endif %}
{% if cot.erro %}<div class="cot-aviso ambar"><b>O provedor não deu preço:</b> {{ cot.erro }}</div>{% endif %}

{% if roteiro %}
<div class="cot-cx" style="border-color:var(--neon-borda)">
  <h3>Pra digitar no portal da {{ escolhida.seguradora }}</h3>
  <p style="font-size:.8rem;color:var(--txt-mut);margin:0 0 .5rem">
    Na ordem em que os portais perguntam. Emitiu por lá? Volte aqui e gere a
    proposta com o número — ela entra na carteira e passa a avisar a renovação.</p>
  <div class="cot-roteiro">{{ roteiro }}</div>
</div>
{% endif %}

<div class="cot-cx">
  <h3>Ofertas</h3>
  {% if cot.ofertas %}
  <div class="cot-rol"><table class="cot-tab">
    <tr><th>Seguradora</th><th>Prêmio</th><th>Franquia</th><th>Parcelas</th>
        <th>Comissão</th><th></th></tr>
    {% for o in cot.ofertas %}
    <tr class="{% if o.escolhida %}escolhida{% endif %}">
      <td>{{ o.seguradora }}{% if o.produto %}<br><span class="s" style="font-size:.74rem;color:var(--txt-mut)">{{ o.produto }}</span>{% endif %}</td>
      <td>{{ brl(o.premio_total_centavos) }}
        {% if o.premio_liquido_centavos is none %}
        <br><span style="font-size:.7rem;color:var(--txt-mut)">sem IOF separado</span>{% endif %}</td>
      <td>{{ brl(o.franquia_centavos) }}</td>
      <td>{% if o.parcelas %}{{ o.parcelas }}x{% else %}—{% endif %}</td>
      <td>{% if o.comissao_centavos is none %}—{% else %}{{ brl(o.comissao_centavos) }}
          <span style="font-size:.7rem;color:var(--txt-mut)">({{ o.comissao_pct }}%)</span>{% endif %}</td>
      <td>{% if o.escolhida %}<span class="cot-selo ok">escolhida</span>{% else %}
        <form method="post" action="/painel/cotacoes/{{ cot.id }}/escolher">
          <input type="hidden" name="oferta_id" value="{{ o.id }}">
          <button class="cot-bt fraco" type="submit">Escolher</button></form>{% endif %}</td>
    </tr>
    {% endfor %}
  </table></div>
  <p style="font-size:.76rem;color:var(--txt-mut);margin:.55rem 0 0">
    A comissão é estimada sobre o prêmio <b>líquido</b> (sem IOF), com o percentual
    cadastrado em Renovações › Percentuais. Oferta que chega sem o IOF separado fica
    sem estimativa — separar por chute inventaria comissão.</p>
  {% else %}
  <div class="cot-vazio">
    {% if cot.situacao == 'falhou' %}Nenhuma oferta: o provedor não respondeu.
    {% else %}Nenhuma oferta ainda. Digite abaixo o que cada seguradora passou.{% endif %}
  </div>
  {% endif %}
</div>

{% if cot.situacao != 'proposta' %}
<div class="cot-cx">
  <h3>Lançar oferta</h3>
  <form method="post" action="/painel/cotacoes/{{ cot.id }}/oferta">
    <div class="cot-grade">
      <div class="cot-campo"><label>Seguradora *</label><input name="seguradora" required></div>
      <div class="cot-campo"><label>Produto</label><input name="produto" placeholder="Auto compreensiva"></div>
      <div class="cot-campo"><label>Prêmio total *</label><input name="premio_total" inputmode="decimal" required></div>
      <div class="cot-campo"><label>Prêmio líquido</label><input name="premio_liquido" inputmode="decimal"></div>
      <div class="cot-campo"><label>IOF</label><input name="iof" inputmode="decimal"></div>
      <div class="cot-campo"><label>Franquia</label><input name="franquia" inputmode="decimal"></div>
      <div class="cot-campo"><label>Parcelas</label><input name="parcelas" inputmode="numeric"></div>
      <div class="cot-campo"><label>Nº na seguradora</label><input name="ref_externa"></div>
    </div>
    <p style="font-size:.76rem;color:var(--txt-mut);margin:.55rem 0 .5rem">
      Preencha o líquido quando a cotação separar o IOF — é ele que dá a comissão certa.</p>
    <button class="cot-bt" type="submit">Acrescentar</button>
  </form>
</div>
{% endif %}

<div class="cot-cx">
  <h3>O que fazer agora</h3>
  <div style="display:flex;gap:.5rem;flex-wrap:wrap;align-items:center">
    {% if not manual %}
    <form method="post" action="/painel/cotacoes/{{ cot.id }}/cotar">
      <button class="cot-bt fraco" type="submit">Cotar de novo</button></form>
    {% endif %}
    {% if escolhida %}
    <form method="post" action="/painel/cotacoes/{{ cot.id }}/emitir">
      <button class="cot-bt" type="submit">Enviar pra emissão</button></form>
    {% endif %}
    {% if not cot.cliente_id %}
    <form method="post" action="/painel/cotacoes/{{ cot.id }}/cliente">
      <button class="cot-bt fraco" type="submit">Pôr o segurado na carteira</button></form>
    {% endif %}
  </div>

  {% if escolhida and cot.situacao != 'proposta' %}
  <form method="post" action="/painel/cotacoes/{{ cot.id }}/proposta"
        style="display:flex;gap:.5rem;align-items:flex-end;flex-wrap:wrap;margin-top:.9rem;
               padding-top:.9rem;border-top:1px solid var(--borda)">
    <div class="cot-campo" style="min-width:150px"><label>Nº da proposta</label>
      <input name="numero_proposta" placeholder="da seguradora"></div>
    <div class="cot-campo" style="min-width:150px"><label>Início da vigência</label>
      <input type="date" name="vigencia_inicio"></div>
    <button class="cot-bt" type="submit">Gerar proposta na carteira</button>
  </form>
  <p style="font-size:.76rem;color:var(--txt-mut);margin:.5rem 0 0">
    A apólice nasce como <b>proposta</b>, com 12 meses de vigência, e já entra na
    régua de renovação 60/30/15.</p>
  {% endif %}

  {% if cot.situacao == 'proposta' %}
  <p style="font-size:.84rem;margin:.6rem 0 0">Virou a apólice #{{ cot.apolice_id }} —
    <a href="/painel/renovacoes?aba=carteira">ver na carteira</a>.</p>
  {% endif %}

  {% if cot.situacao != 'proposta' %}
  <form method="post" action="/painel/cotacoes/{{ cot.id }}/perder"
        style="display:flex;gap:.5rem;align-items:flex-end;flex-wrap:wrap;margin-top:.9rem;
               padding-top:.9rem;border-top:1px solid var(--borda)">
    <div class="cot-campo" style="flex:1;min-width:180px"><label>Não fechou — por quê</label>
      <input name="motivo" placeholder="preço, ficou com a seguradora atual…"></div>
    <button class="cot-bt fraco" type="submit">Marcar como perdida</button>
  </form>
  {% endif %}
</div>
{% endblock %}"""

_env.loader.mapping["cotacoes"] = _TPL_LISTA
_env.loader.mapping["cotacao"] = _TPL_DETALHE
