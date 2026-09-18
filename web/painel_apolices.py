"""A tela Renovações: /painel/renovacoes — a carteira de apólices da corretora.

É a tela do segundo relógio. Quem vende festa tem a data da festa; quem vende
seguro tem o fim da vigência — e até esta entrega o fim da vigência não era
guardado em lugar nenhum, o que deixou `fu_festa_dias` nulo no perfil de Raio-X
da corretora (migração 242).

SÓ APARECE PRA CORRETORA (regra 6): o perfil do nicho tem que ser `seguros`. Uma
tela de apólice numa conta de festa seria exatamente o erro que a regra 6 nasceu
pra impedir.

QUEM VÊ O QUÊ, e é a decisão do dono de 17/09/2026 ("alerta pro corretor"):
o dono e o gestor veem a carteira inteira; o corretor vê a fila dele. O ALERTA —
o push de 60/30/15, em `finance/lembretes._renovacoes` — vai pro corretor da
apólice. A tela é onde se olha; o push é o que chega sem pedir.

O CADASTRO COMEÇA POR AUTO (mesma decisão). O formulário pergunta placa, modelo,
ano e classe de bônus porque é isso que a corretora tem hoje; a tabela aguenta
vida e residencial pelo `bem` jsonb desde o primeiro dia, e o que falta pra eles
é formulário, não banco.
"""
from __future__ import annotations

import logging
from datetime import date, datetime

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db.conexao import get_pool
from finance import apolices as ap
from finance import raio_x_perfil as rxp
from web.portal import _env, _render, conta_logada, nicho_da_conta

router = APIRouter()
_log = logging.getLogger("openclaw.painel_apolices")

_PAPEIS_OK = ("dono", "gestor", "vendedor")


def _acesso(request: Request):
    """Devolve (conta, gerencia, redirect). `gerencia` é quem vê a carteira inteira."""
    conta = conta_logada(request)
    if conta is None:
        return None, False, RedirectResponse("/login", status_code=303)
    papel = request.session.get("papel", "dono")
    if papel not in _PAPEIS_OK:
        return None, False, RedirectResponse("/painel", status_code=303)
    # Regra 6: a tela segue o nicho. `seguros` e mais nenhum.
    if rxp.perfil_por_nicho(nicho_da_conta(conta)) != "seguros":
        return None, False, RedirectResponse("/painel", status_code=303)
    return conta, papel in ("dono", "gestor"), None


def _brl(centavos) -> str:
    v = (int(centavos or 0)) / 100
    return f"R$ {v:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")


def _cent(txt: str) -> int:
    """'4.088,57' / '4088.57' / '' → centavos. Vazio é 0, não erro: a corretora
    cadastra a apólice antes de saber o prêmio mais vezes do que se imagina."""
    t = (txt or "").strip().replace("R$", "").replace(" ", "")
    if not t:
        return 0
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return int(round(float(t) * 100))
    except ValueError:
        return 0


def _data(txt: str):
    t = (txt or "").strip()
    if not t:
        return None
    try:
        return datetime.strptime(t, "%Y-%m-%d").date()
    except ValueError:
        return None


def _int(txt: str):
    t = (txt or "").strip()
    try:
        return int(t) if t else None
    except ValueError:
        return None


def _membro_logado(request: Request) -> int | None:
    return request.session.get("membro_id")


def _corretores(pool, conta_id: int) -> list[tuple]:
    with pool.connection() as c:
        return c.execute(
            "select id, coalesce(nullif(nome,''), email, 'sem nome') from membros "
            " where conta_id=%s and coalesce(ativo,true) "
            "   and papel in ('dono','gestor','vendedor') order by 2", (conta_id,)).fetchall()


def _clientes(pool, conta_id: int) -> list[tuple]:
    with pool.connection() as c:
        return c.execute(
            "select id, nome from clientes where dono_id=%s and ativo "
            " order by nome limit 500", (conta_id,)).fetchall()


#: As três abas. Existem porque a tela fazia TRÊS TRABALHOS de frequências
#: diferentes na mesma rolagem — ver a fila é diário, cadastrar é eventual,
#: configurar percentual é uma vez na vida — e dava o mesmo peso aos três. Com uma
#: apólice cadastrada, a página mostrava três contadores zerados, 120 palavras de
#: lei e um formulário de 21 campos ANTES da única linha que interessava.
ABAS = (("fila", "Renovações"), ("carteira", "Carteira"), ("percentuais", "Percentuais"))


@router.get("/painel/renovacoes", response_class=HTMLResponse)
def painel_renovacoes(request: Request):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    conta_id = conta[0]
    hoje = date.today()
    q = request.query_params
    aba = (q.get("aba") or "fila").strip()
    if aba not in dict(ABAS):
        aba = "fila"
    # o vendedor não configura o que a conta inteira usa, nem vê a carteira toda
    if not gerencia and aba == "percentuais":
        aba = "fila"
    busca = (q.get("busca") or "").strip()
    # O corretor vê a fila dele; o dono e o gestor veem a carteira inteira. Mesmo
    # desenho da Fila do vendedor — e é o que o dono pediu.
    so_meu = None if gerencia else _membro_logado(request)
    fila = ap.a_vencer(pool, conta_id, dias=ap.HORIZONTE, hoje=hoje, corretor_id=so_meu)
    carteira = ap.listar(pool, conta_id, hoje=hoje, busca=busca) if gerencia else []
    # os três degraus, pra faixa do topo. A lista vai até 90 dias e o alerta só
    # sai em 60 — a diferença entre as duas coisas é o ponto do desenho.
    em_alerta = [a for a in fila if a["degrau"] is not None]
    vencidas = [a for a in fila if (a["dias"] or 0) < 0]
    # o que vem DEPOIS do horizonte: é o que transforma "nenhuma apólice vencendo"
    # de mentira útil em informação. Só custa uma consulta quando a fila está vazia.
    proxima = ap.proxima(pool, conta_id, hoje=hoje,
                         corretor_id=so_meu) if not fila else None
    return _render("renovacoes", request, titulo="Renovações",
                   secao_ativa="renovacoes", gerencia=gerencia,
                   aba=aba, abas=ABAS, busca=busca, proxima=proxima,
                   fila=fila, carteira=carteira, em_alerta=em_alerta, vencidas=vencidas,
                   n_carteira=ap.total_da_carteira(pool, conta_id) if gerencia else 0,
                   comissoes=ap.comissoes(pool, conta_id),
                   corretores=_corretores(pool, conta_id),
                   clientes=_clientes(pool, conta_id),
                   ramos=ap.RAMOS, situacoes=ap.SITUACOES, degraus=ap.DEGRAUS,
                   horizonte=ap.HORIZONTE, hoje=hoje, brl=_brl,
                   erro=(request.query_params.get("erro") or "").strip())


@router.post("/painel/renovacoes/apolice")
def salvar_apolice(request: Request,
                   apolice_id: str = Form(""), cliente_id: str = Form(""),
                   corretor_id: str = Form(""), seguradora: str = Form(""),
                   ramo: str = Form("auto"), numero_proposta: str = Form(""),
                   numero_apolice: str = Form(""), vigencia_inicio: str = Form(""),
                   vigencia_fim: str = Form(""), situacao: str = Form("proposta"),
                   premio: str = Form(""), iof: str = Form(""), franquia: str = Form(""),
                   comissao_pct: str = Form(""), comissao: str = Form(""),
                   classe_bonus: str = Form(""), parcelas: str = Form(""),
                   dia_vencimento: str = Form(""),
                   placa: str = Form(""), modelo: str = Form(""), ano: str = Form(""),
                   chassi: str = Form(""), obs: str = Form("")):
    """Cadastra ou edita uma apólice.

    `def` e não `async def`: o handler escreve no banco de forma síncrona, e
    handler async fazendo isso trava o event loop do processo inteiro
    (`tests/test_event_loop_nao_trava.py` cobra isso de todo handler novo).

    O CAMPO DE AUTO vira `bem` jsonb — placa, modelo, ano, chassi. É o único
    formulário por ramo que existe hoje (decisão do dono: "começa por auto"), e a
    coluna já aguenta os outros sem migração nova.
    """
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    bem = {k: v.strip() for k, v in (("placa", placa), ("modelo", modelo),
                                     ("ano", ano), ("chassi", chassi)) if v.strip()}
    pct = (comissao_pct or "").strip().replace(",", ".")
    dados = {
        "cliente_id": _int(cliente_id), "corretor_id": _int(corretor_id),
        "seguradora": seguradora, "ramo": ramo,
        "numero_proposta": (numero_proposta or "").strip() or None,
        "numero_apolice": (numero_apolice or "").strip() or None,
        "vigencia_inicio": _data(vigencia_inicio), "vigencia_fim": _data(vigencia_fim),
        "situacao": situacao,
        "premio_centavos": _cent(premio), "iof_centavos": _cent(iof),
        "franquia_centavos": _cent(franquia),
        "comissao_pct": (pct or None),
        # comissão VAZIA é None, não zero: zero diria "esta apólice não paga
        # comissão" e apagaria o percentual padrão da seguradora em silêncio.
        "comissao_centavos": (_cent(comissao) if (comissao or "").strip() else None),
        "classe_bonus": (classe_bonus or "").strip() or None,
        "parcelas": _int(parcelas), "dia_vencimento": _int(dia_vencimento),
        "bem": bem, "condutor": {}, "coberturas": [],
        "renovacao_de": None, "obs": (obs or "").strip() or None,
    }
    try:
        ap.salvar(pool, conta[0], dados, _int(apolice_id))
    except ValueError as e:
        return RedirectResponse(f"/painel/renovacoes?erro={e}", status_code=303)
    except Exception as e:  # noqa: BLE001
        _log.warning("apólice não salvou (conta %s): %s: %s", conta[0], type(e).__name__, e)
        return RedirectResponse("/painel/renovacoes?erro=não deu pra salvar", status_code=303)
    return RedirectResponse("/painel/renovacoes", status_code=303)


@router.post("/painel/renovacoes/apolice/{apolice_id}/situacao")
def mudar_situacao(request: Request, apolice_id: int, situacao: str = Form("")):
    """Move a apólice de estado num toque — é o caminho de "proposta → vigente" e
    de "vigente → renovada", que é o que acontece o tempo todo."""
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return redir
    sit = (situacao or "").strip().lower()
    if sit not in dict(ap.SITUACOES):
        return RedirectResponse("/painel/renovacoes", status_code=303)
    with get_pool().connection() as c:
        with c.transaction():
            c.execute("update apolices set situacao=%s, atualizado_em=now() "
                      " where id=%s and conta_id=%s", (sit, apolice_id, conta[0]))
    return RedirectResponse("/painel/renovacoes", status_code=303)


@router.post("/painel/renovacoes/comissao")
def salvar_comissao(request: Request, seguradora: str = Form(""),
                    ramo: str = Form(""), pct: str = Form("")):
    """O percentual padrão da seguradora. Só dono e gestor: é número da casa, não
    do corretor — e é ele que alimenta o "comissão proposta × fechada" do Raio-X."""
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    if not gerencia:
        return RedirectResponse("/painel/renovacoes", status_code=303)
    if not ap.salvar_comissao(get_pool(), conta[0], seguradora, ramo, pct):
        return RedirectResponse("/painel/renovacoes?aba=percentuais&erro=percentual inválido",
                                status_code=303)
    return RedirectResponse("/painel/renovacoes?aba=percentuais", status_code=303)


@router.post("/painel/renovacoes/comissao/{cid}/apagar")
def apagar_comissao(request: Request, cid: int):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    if gerencia:
        ap.apagar_comissao(get_pool(), conta[0], cid)
    return RedirectResponse("/painel/renovacoes?aba=percentuais", status_code=303)


_TPL = r"""{% extends "base" %}{% block conteudo %}
<style>
.rn-topo{display:flex;align-items:center;justify-content:space-between;gap:.8rem;flex-wrap:wrap;margin-bottom:.2rem}
.rn-topo h2{margin:0;font-size:1.25rem}
.rn-abas{display:flex;gap:.15rem;border-bottom:1px solid var(--borda);margin:.7rem 0 1rem;overflow-x:auto}
.rn-abas a{padding:.45rem .8rem;font-size:.85rem;color:var(--txt-mut);text-decoration:none;
  border-bottom:2px solid transparent;white-space:nowrap}
.rn-abas a.on{color:var(--verde-claro);border-bottom-color:var(--verde);font-weight:600}
.rn-faixas{display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:.5rem;margin-bottom:1rem}
.rn-cx{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.55rem .75rem}
.rn-cx .r{font-size:.66rem;text-transform:uppercase;letter-spacing:.06em;color:var(--txt-mut);display:block}
.rn-cx .v{font-size:1.35rem;font-weight:700;line-height:1.15}
.rn-cx .n{font-size:.72rem;color:var(--txt-mut)}
.rn-cx.alerta{background:var(--ambar-fundo);border-color:var(--ambar-borda)}
.rn-cx.alerta .v{color:#F0DCA6}
.rn-cx.venc{background:var(--neon-fundo);border-color:var(--neon-borda)}
.rn-lista{display:flex;flex-direction:column;gap:.45rem;margin-bottom:1rem}
.rn-card{background:var(--card);border:1px solid var(--borda);border-left:3px solid var(--borda);
  border-radius:11px;padding:.6rem .8rem}
.rn-card.d15{border-left-color:#E06C6C}
.rn-card.d30{border-left-color:var(--amar)}
.rn-card.d60{border-left-color:#8FC9E6}
.rn-card.passou{border-left-color:#E06C6C;background:var(--ambar-fundo)}
.rn-card .cab{display:flex;gap:.45rem;align-items:baseline;flex-wrap:wrap}
.rn-card .quem{font-weight:700;font-size:.95rem}
.rn-card .meta{font-size:.79rem;color:var(--txt-mut);margin-top:.1rem}
.rn-card .dinheiro{font-size:.79rem;color:var(--txt);opacity:.85;margin-top:.15rem}
.rn-acoes{display:flex;gap:.35rem;align-items:center;flex-wrap:wrap;margin-top:.5rem}
.rn-acoes form{display:inline-flex;gap:.3rem;align-items:center;margin:0}
.rn-bt{background:var(--verde);color:var(--sobre-verde);border:0;border-radius:8px;
  padding:.35rem .8rem;font-size:.8rem;font-weight:700;cursor:pointer}
.rn-bt.fraco{background:transparent;border:1px solid var(--borda);color:var(--txt-mut);font-weight:500}
.rn-tag{font-size:.67rem;font-weight:700;text-transform:uppercase;letter-spacing:.05em;
  padding:.1rem .42rem;border-radius:999px;border:1px solid var(--borda);color:var(--txt-mut)}
.rn-tag.d15{background:rgba(224,108,108,.15);border-color:#E06C6C;color:#E9A0A0}
.rn-tag.d30{background:var(--ambar-fundo);border-color:var(--ambar-borda);color:#F0DCA6}
.rn-tag.d60{background:var(--azul-fundo);border-color:var(--azul-borda);color:#8FC9E6}
.rn-pill{font-size:.72rem;color:var(--txt-mut);background:var(--card-2);border:1px solid var(--borda);
  border-radius:999px;padding:.08rem .45rem}
.rn-bloco{background:var(--card);border:1px solid var(--borda);border-radius:13px;padding:.85rem 1rem;margin-bottom:1rem}
.rn-bloco h3{margin:0 0 .2rem;font-size:.95rem}
.rn-bloco .sub{font-size:.79rem;color:var(--txt-mut);margin-bottom:.8rem;line-height:1.55}
.rn-grupo{border:1px solid var(--borda);border-radius:10px;padding:.6rem .7rem;margin-bottom:.6rem}
.rn-grupo > .t{font-size:.67rem;text-transform:uppercase;letter-spacing:.08em;color:var(--txt-mut);
  font-weight:700;margin-bottom:.45rem}
.rn-grade{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.55rem}
.rn-campo{display:flex;flex-direction:column;gap:.18rem}
.rn-campo label{font-size:.68rem;text-transform:uppercase;letter-spacing:.05em;color:var(--txt-mut)}
.rn-campo.chave label{color:var(--verde-claro)}
.rn-campo input,.rn-campo select{background:var(--bg);border:1px solid var(--borda);
  border-radius:8px;padding:.38rem .5rem;color:var(--txt);font-size:.85rem;width:100%}
.rn-campo.chave input{border-color:var(--neon-borda)}
.rn-busca{display:flex;gap:.4rem;margin-bottom:.8rem;flex-wrap:wrap}
.rn-busca input{flex:1;min-width:180px;background:var(--bg);border:1px solid var(--borda);
  border-radius:8px;padding:.42rem .6rem;color:var(--txt);font-size:.85rem}
.rn-rol{overflow-x:auto;border:1px solid var(--borda);border-radius:11px}
.rn-tab{border-collapse:collapse;width:100%;min-width:620px;font-size:.84rem}
.rn-tab th{text-align:right;padding:.5rem .6rem;font-size:.66rem;text-transform:uppercase;
  letter-spacing:.05em;color:var(--txt-mut);font-weight:500;background:var(--card-2);
  border-bottom:1px solid var(--borda);white-space:nowrap}
.rn-tab th:first-child,.rn-tab td:first-child{text-align:left}
.rn-tab td{text-align:right;padding:.45rem .6rem;border-bottom:1px solid var(--borda);white-space:nowrap}
.rn-tab tr:last-child td{border-bottom:0}
.rn-aviso{border-radius:10px;padding:.7rem .85rem;font-size:.84rem;line-height:1.55;margin-bottom:.9rem}
.rn-aviso.azul{background:var(--azul-fundo);border:1px solid var(--azul-borda);color:#8FC9E6}
.rn-aviso.ambar{background:var(--ambar-fundo);border:1px solid var(--ambar-borda);color:#F0DCA6}
.rn-vazio{background:var(--card);border:1px solid var(--borda);border-radius:13px;padding:1.1rem 1rem;margin-bottom:1rem}
.rn-vazio .t{font-weight:600;font-size:.95rem;margin-bottom:.15rem}
.rn-vazio .s{font-size:.82rem;color:var(--txt-mut);line-height:1.6}
details.rn-det{margin-bottom:1rem}
details.rn-det > summary{cursor:pointer;font-size:.8rem;color:var(--txt-mut);list-style:none;
  display:inline-flex;gap:.3rem;align-items:center;padding:.3rem .65rem;border:1px solid var(--borda);
  border-radius:999px;background:var(--card)}
details.rn-det > summary::-webkit-details-marker{display:none}
details.rn-det[open] > summary{margin-bottom:.6rem}
</style>

<div class="rn-topo">
  <h2>Renovações</h2>
  {% if gerencia %}
  {# âncora não abre <details>; o onclick abre e a âncora leva até lá #}
  <a class="rn-bt" href="#nova" style="text-decoration:none"
     onclick="var d=document.getElementById('nova');if(d)d.open=true">+ Nova apólice</a>
  {% endif %}
</div>

<nav class="rn-abas">
  {% for chave, rot in abas %}
    {% if chave != 'percentuais' or gerencia %}
    <a href="/painel/renovacoes?aba={{ chave }}" class="{% if aba == chave %}on{% endif %}">{{ rot }}{% if chave == 'carteira' and n_carteira %} ({{ n_carteira }}){% endif %}</a>
    {% endif %}
  {% endfor %}
</nav>

{% if erro %}<div class="rn-aviso ambar"><b>Não salvou:</b> {{ erro }}</div>{% endif %}

{# ─────────────────────────── ABA: a fila ─────────────────────────── #}
{% if aba == 'fila' %}

  {# os contadores só existem quando há o que contar. Três caixas dizendo 0 na
     faixa mais nobre da página é o que a tela fazia antes. #}
  {% if fila %}
  <div class="rn-faixas">
    {% if em_alerta %}<div class="rn-cx alerta"><span class="r">Na régua</span>
      <span class="v">{{ em_alerta|length }}</span><span class="n">60 dias ou menos</span></div>{% endif %}
    {% set na_fila = fila|length - em_alerta|length - vencidas|length %}
    {% if na_fila > 0 %}<div class="rn-cx"><span class="r">Na fila</span>
      <span class="v">{{ na_fila }}</span><span class="n">entre 60 e {{ horizonte }} dias</span></div>{% endif %}
    {% if vencidas %}<div class="rn-cx venc"><span class="r">Já venceu</span>
      <span class="v">{{ vencidas|length }}</span><span class="n">e ninguém marcou</span></div>{% endif %}
  </div>

  <div class="rn-lista">
    {% for a in fila %}
    {% set passou = (a.dias is not none and a.dias < 0) %}
    <div class="rn-card {% if passou %}passou{% elif a.degrau %}d{{ a.degrau }}{% endif %}" id="a{{ a.id }}">
      <div class="cab">
        <span class="quem">{{ a.cliente }}</span>
        <span class="rn-pill">{{ a.seguradora }}</span>
        <span class="rn-pill">{{ a.ramo_txt|lower }}</span>
        {% if passou %}<span class="rn-tag d15">venceu</span>
        {% elif a.degrau %}<span class="rn-tag d{{ a.degrau }}">faltam {{ a.dias }} dias</span>{% endif %}
      </div>
      <div class="meta">
        {%- if a.bem.modelo %}{{ a.bem.modelo }}{% if a.bem.placa %} · {{ a.bem.placa }}{% endif %} · {% endif -%}
        vence {{ a.vigencia_fim.strftime('%d/%m/%Y') }}
        {%- if a.dias == 0 %} · <b>é hoje</b>{% elif a.dias == 1 %} · <b>é amanhã</b>
        {%- elif a.dias < 0 %} · <b>há {{ -a.dias }} dias</b>{% endif %}
        {%- if a.classe_bonus %} · classe de bônus {{ a.classe_bonus }}{% endif %}
      </div>
      <div class="dinheiro">
        Prêmio {{ brl(a.premio_centavos) }}
        {%- if a.comissao_estimada is not none %} · comissão {{ brl(a.comissao_estimada) }}
          {%- if not a.comissao_fechada %} (estimada{% if a.comissao_pct %}, {{ a.comissao_pct }}%{% endif %}){% endif %}
        {%- else %} · <a href="/painel/renovacoes?aba=percentuais" style="color:var(--amar)">cadastre o percentual da {{ a.seguradora }}</a>{% endif %}
      </div>
      <div class="rn-acoes">
        <form method="post" action="/painel/renovacoes/apolice/{{ a.id }}/situacao">
          <select name="situacao" onchange="this.form.submit()"
                  style="background:var(--bg);border:1px solid var(--borda);border-radius:8px;padding:.28rem .45rem;color:var(--txt);font-size:.78rem">
            {% for chave, rot in situacoes %}
            <option value="{{ chave }}" {% if chave == a.situacao %}selected{% endif %}>{{ rot }}</option>
            {% endfor %}
          </select>
          <noscript><button class="rn-bt fraco">ok</button></noscript>
        </form>
      </div>
    </div>
    {% endfor %}
  </div>

  {% else %}
  {# O VAZIO INFORMA. "Nenhuma apólice vencendo" é verdade e é inútil: a corretora
     TEM carteira, e a tela dizendo que não há nada convida a cadastrar duplicado. #}
  <div class="rn-vazio">
    <div class="t">Nada vence nos próximos {{ horizonte }} dias.</div>
    {% if proxima %}
    <div class="s">A próxima é <b>{{ proxima.cliente }}</b> — {{ proxima.seguradora }}
      {{ proxima.ramo_txt|lower }}{% if proxima.bem.modelo %}, {{ proxima.bem.modelo }}{% endif %},
      que vence em <b>{{ proxima.vigencia_fim.strftime('%d/%m/%Y') }}</b>
      (faltam {{ proxima.dias }} dias).<br>
      Entra nesta lista em <b>{{ proxima.entra_em.strftime('%d/%m/%Y') }}</b>, e o primeiro
      aviso no celular sai no mesmo dia.</div>
    {% elif n_carteira %}
    <div class="s">A carteira tem {{ n_carteira }} apólice{{ 's' if n_carteira > 1 }}, e nenhuma
      delas está perto de vencer. Veja tudo na aba <a href="/painel/renovacoes?aba=carteira">Carteira</a>.</div>
    {% else %}
    <div class="s">A carteira está vazia. Cadastre a primeira apólice abaixo — o aviso
      começa a valer no mesmo dia.</div>
    {% endif %}
  </div>
  {% endif %}

  <details class="rn-det">
    <summary>por que 60, 30 e 15?</summary>
    <div class="rn-aviso azul" style="margin:0">
      O aviso no celular sai três vezes por apólice — faltando 60, 30 e 15 dias — e vai
      pro corretor responsável. A lista vai até {{ horizonte }} dias porque "o que vem por
      aí" é consulta, não interrupção: entre 90 e 60 dias a apólice aparece e não avisa ninguém.
      <br><br>
      Ninguém é obrigado a avisar o cliente do vencimento — nem a corretora, nem a
      seguradora. Quem tem prazo é a seguradora que <b>não</b> quer renovar, e são 30 dias
      (Lei 15.040/2024). É por isso que avisar antes retém: quem liga faltando dois dias
      não está fazendo consultoria, está emitindo boleto.
    </div>
  </details>

  {% if gerencia %}
  <details class="rn-det" id="nova" {% if not n_carteira %}open{% endif %}>
    <summary>+ cadastrar uma apólice</summary>
    <div class="rn-bloco" style="margin:0">
      <h3>Cadastrar apólice</h3>
      <div class="sub">Auto por enquanto — é o que a carteira tem hoje. Os outros ramos já
        gravam; o que falta pra eles é o formulário, não o cadastro.</div>
      <form method="post" action="/painel/renovacoes/apolice">
        <div class="rn-grupo"><div class="t">Quem</div><div class="rn-grade">
          <div class="rn-campo"><label>Cliente</label>
            <select name="cliente_id"><option value="">— sem cliente na carteira —</option>
              {% for cid, nome in clientes %}<option value="{{ cid }}">{{ nome }}</option>{% endfor %}
            </select></div>
          <div class="rn-campo"><label>Corretor (recebe o aviso)</label>
            <select name="corretor_id"><option value="">— dono e gestores —</option>
              {% for mid, nome in corretores %}<option value="{{ mid }}">{{ nome }}</option>{% endfor %}
            </select></div>
        </div></div>

        <div class="rn-grupo"><div class="t">O que, e até quando</div><div class="rn-grade">
          <div class="rn-campo chave"><label>Seguradora *</label><input name="seguradora" required placeholder="Allianz"></div>
          <div class="rn-campo"><label>Ramo</label>
            <select name="ramo">{% for chave, rot in ramos %}<option value="{{ chave }}">{{ rot }}</option>{% endfor %}</select></div>
          <div class="rn-campo"><label>Situação</label>
            <select name="situacao">{% for chave, rot in situacoes %}<option value="{{ chave }}">{{ rot }}</option>{% endfor %}</select></div>
          <div class="rn-campo"><label>Início da vigência</label><input type="date" name="vigencia_inicio"></div>
          <div class="rn-campo chave"><label>Fim da vigência *</label><input type="date" name="vigencia_fim" required></div>
          <div class="rn-campo"><label>Nº da proposta</label><input name="numero_proposta"></div>
          <div class="rn-campo"><label>Nº da apólice</label><input name="numero_apolice"></div>
        </div></div>

        <div class="rn-grupo"><div class="t">Quanto</div><div class="rn-grade">
          <div class="rn-campo"><label>Prêmio líquido</label><input name="premio" placeholder="3.807,57"></div>
          <div class="rn-campo"><label>IOF</label><input name="iof" placeholder="281,00"></div>
          <div class="rn-campo"><label>Franquia</label><input name="franquia" placeholder="4.088,88"></div>
          <div class="rn-campo"><label>Comissão %</label><input name="comissao_pct" placeholder="vem da seguradora"></div>
          <div class="rn-campo"><label>Comissão R$ (se souber)</label><input name="comissao"></div>
          <div class="rn-campo"><label>Parcelas</label><input name="parcelas" placeholder="4"></div>
          <div class="rn-campo"><label>Dia do vencimento</label><input name="dia_vencimento" placeholder="5"></div>
        </div></div>

        <div class="rn-grupo"><div class="t">O bem segurado</div><div class="rn-grade">
          <div class="rn-campo"><label>Placa</label><input name="placa"></div>
          <div class="rn-campo"><label>Marca / modelo</label><input name="modelo" placeholder="GEELY EX2 MAX"></div>
          <div class="rn-campo"><label>Ano</label><input name="ano" placeholder="2026"></div>
          <div class="rn-campo"><label>Chassi</label><input name="chassi"></div>
          <div class="rn-campo"><label>Classe de bônus</label><input name="classe_bonus" placeholder="00"></div>
        </div></div>

        <button class="rn-bt">Cadastrar apólice</button>
      </form>
    </div>
  </details>
  {% endif %}

{# ────────────────────────── ABA: a carteira ────────────────────────── #}
{% elif aba == 'carteira' %}
  <form class="rn-busca" method="get" action="/painel/renovacoes">
    <input type="hidden" name="aba" value="carteira">
    <input name="busca" value="{{ busca }}" placeholder="nome, seguradora, placa, modelo ou número…">
    <button class="rn-bt">Buscar</button>
    {% if busca %}<a class="rn-bt fraco" style="text-decoration:none;padding:.35rem .8rem"
       href="/painel/renovacoes?aba=carteira">limpar</a>{% endif %}
  </form>
  {% if carteira %}
  <div class="rn-rol"><table class="rn-tab">
    <tr><th>Cliente</th><th>Seguradora</th><th>Ramo</th><th>Vence</th><th>Situação</th>
        <th>Prêmio</th><th>Comissão</th></tr>
    {% for a in carteira %}
    <tr>
      <td>{{ a.cliente }}{% if a.bem.placa %} <span class="rn-pill">{{ a.bem.placa }}</span>{% endif %}</td>
      <td>{{ a.seguradora }}</td><td>{{ a.ramo_txt }}</td>
      <td>{{ a.vigencia_fim.strftime('%d/%m/%Y') }}{% if a.dias is not none and a.dias >= 0 %}
          <span class="rn-pill">{{ a.dias }}d</span>{% endif %}</td>
      <td>{{ a.situacao_txt }}</td>
      <td>{{ brl(a.premio_centavos) }}</td>
      <td>{% if a.comissao_estimada is not none %}{{ brl(a.comissao_estimada) }}{% else %}—{% endif %}</td>
    </tr>
    {% endfor %}
  </table></div>
  {% else %}
  <div class="rn-vazio"><div class="t">
    {% if busca %}Nada encontrado para “{{ busca }}”.{% else %}A carteira está vazia.{% endif %}</div>
    <div class="s">{% if busca %}A busca olha nome do cliente, seguradora, placa, modelo e número da
      apólice.{% else %}Cadastre a primeira em <a href="/painel/renovacoes#nova">Renovações</a>.{% endif %}</div>
  </div>
  {% endif %}

{# ───────────────────────── ABA: os percentuais ───────────────────────── #}
{% else %}
  <div class="rn-bloco">
    <h3>Comissão por seguradora</h3>
    <div class="sub">A comissão não vem escrita na apólice — é papel do cliente, e o cliente
      não vê quanto o corretor ganha. Cadastre o percentual uma vez e toda apólice daquela
      seguradora passa a mostrar a comissão estimada. Deixe o ramo em “todos” pra valer no
      geral, e cadastre por ramo só onde o percentual foge.
      <br>O percentual incide sobre o <b>prêmio líquido</b>, sem o IOF — o IOF é imposto
      repassado ao governo e não entra em comissão.</div>
    {% if comissoes %}
    <div class="rn-rol" style="margin-bottom:.8rem"><table class="rn-tab" style="min-width:420px">
      <tr><th>Seguradora</th><th>Ramo</th><th>%</th><th></th></tr>
      {% for cm in comissoes %}
      <tr><td>{{ cm.seguradora }}</td><td>{{ cm.ramo_txt }}</td><td>{{ cm.pct }}%</td>
        <td><form method="post" action="/painel/renovacoes/comissao/{{ cm.id }}/apagar">
          <button class="rn-bt fraco" style="padding:.18rem .5rem">apagar</button></form></td></tr>
      {% endfor %}
    </table></div>
    {% endif %}
    <form method="post" action="/painel/renovacoes/comissao">
      <div class="rn-grade">
        <div class="rn-campo"><label>Seguradora</label><input name="seguradora" required placeholder="Allianz"></div>
        <div class="rn-campo"><label>Ramo</label>
          <select name="ramo"><option value="">todos os ramos</option>
            {% for chave, rot in ramos %}<option value="{{ chave }}">{{ rot }}</option>{% endfor %}</select></div>
        <div class="rn-campo"><label>Percentual</label><input name="pct" required placeholder="20"></div>
      </div>
      <button class="rn-bt" style="margin-top:.6rem">Salvar percentual</button>
    </form>
  </div>
{% endif %}
{% endblock %}"""

_env.loader.mapping["renovacoes"] = _TPL
