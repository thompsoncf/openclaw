"""O follow-up no painel: /painel/follow-up (mockup docs/mockups/follow_up_automatico.html,
aprovado em 07/09/2026).

Os quatro indicadores do topo — hoje, atrasados, críticos, sem próxima ação —,
cada um abrindo a lista respectiva, com filtro por vendedor e por etapa. Cada
lead mostra o que o pedido do dono pediu: a próxima ação e quando vence, o
responsável, o tempo sem interação, as tentativas, e de quem é a bola. E os dois
selos que a régua acrescenta: quantas vezes já foi avisado, e quantas vezes foi
adiado sem ninguém falar com o cliente.

Quem vê: dono e gestor veem a conta inteira e a tabela por vendedor; o vendedor
vê a fila dele e mais nada — o painel da gestão é do gestor.

O motor está em finance/follow_up; aqui é só a tela.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db.conexao import get_pool
from finance import follow_up as fu
from finance import raio_x_perfil as rxp
from web.portal import _env, _render, conta_logada

router = APIRouter()

_UTC_BR = timedelta(hours=-3)


def _acesso(request: Request):
    conta = conta_logada(request)
    if conta is None:
        return None, None, RedirectResponse("/login", status_code=303)
    papel = request.session.get("papel", "dono")
    if papel not in ("dono", "gestor", "vendedor"):
        return None, None, RedirectResponse("/painel", status_code=303)
    perfil = rxp.perfil(conta[7] if len(conta) > 7 else None)
    if perfil["chave"] not in fu.PERFIS_COM_TELA:
        return None, None, RedirectResponse("/painel", status_code=303)
    return conta, perfil, None


def _br(dt) -> str:
    """Instante em hora de Brasília, curto. O painel inteiro trata -3 fixo (o país
    não tem horário de verão desde 2019) — duas convenções de fuso no mesmo
    produto seria pior que o erro de uma."""
    if not dt:
        return ""
    loc = dt + _UTC_BR
    hoje = (datetime.now(timezone.utc) + _UTC_BR).date()
    if loc.date() == hoje:
        return f"hoje {loc:%H:%M}"
    if loc.date() == hoje - timedelta(days=1):
        return f"ontem {loc:%H:%M}"
    if loc.date() == hoje + timedelta(days=1):
        return f"amanhã {loc:%H:%M}"
    return f"{loc:%d/%m} {loc:%H:%M}"


def _tempo(horas) -> str:
    if horas is None:
        return "—"
    h = int(horas)
    return f"{h}h" if h < 48 else f"{h // 24}d"


@router.get("/painel/follow-up", response_class=HTMLResponse)
def painel_follow_up(request: Request):
    conta, perfil, redir = _acesso(request)
    if redir is not None:
        return redir
    pool, conta_id = get_pool(), conta[0]
    papel = request.session.get("papel", "dono")
    q = request.query_params
    estado = q.get("estado") or "critico"
    if estado not in fu.ESTADOS and estado != "todos":
        estado = "critico"
    try:
        vend_f = int(q.get("vendedor") or 0) or None
    except ValueError:
        vend_f = None
    # o vendedor só enxerga a fila dele — o painel da gestão é do gestor
    if papel == "vendedor":
        vend_f = request.session.get("membro_id")
    etapa_f = (q.get("etapa") or "").strip()

    linhas, cfg, vendedores, etapas = [], dict(fu._PADRAO), [], []
    try:
        with pool.connection() as c:
            cfg = fu.config(c, conta_id)
            linhas = fu.leads(c, conta_id, perfil, cfg=cfg)
            if papel != "vendedor":
                vendedores = c.execute(
                    """select id, coalesce(nullif(nome,''), email) from membros
                        where conta_id=%s and coalesce(ativo,true) and papel='vendedor'
                        order by 2""", (conta_id,)).fetchall()
            etapas = [r[0] for r in c.execute(
                """select chave from funil_etapas where conta_id=%s
                    and fase='venda' order by ordem, id""", (conta_id,)).fetchall()]
    except Exception:  # noqa: BLE001 — tela que não abre é pior que tela incompleta
        linhas = []

    minhas = [x for x in linhas if (not vend_f or x["vendedor_id"] == vend_f)]
    topo = fu.resumo(minhas)
    fila = [x for x in minhas
            if (estado == "todos" or x["estado"] == estado)
            and (not etapa_f or x["status"] == etapa_f)]
    fila = fu.ordenar(fila)
    return _render("follow_up", request, titulo="Follow-up", secao_ativa="follow_up",
                   perfil=perfil, papel=papel, topo=topo, fila=fila[:200],
                   sobrando=max(0, len(fila) - 200), estado=estado, vend_f=vend_f,
                   etapa_f=etapa_f, vendedores=vendedores, etapas=etapas,
                   gestao=(fu.por_vendedor(linhas) if papel != "vendedor" else []),
                   modo=cfg["follow_up_modo"], rotulo=fu.ROTULO, emoji=fu.EMOJI,
                   br=_br, tempo=_tempo, adia_max=fu.ADIAMENTOS_ATE_MOTIVO,
                   erro=q.get("erro") or "")


@router.post("/painel/follow-up/reagendar")
def follow_up_reagendar(request: Request, lead_id: int = Form(...),
                        quando: str = Form(""), hora: str = Form(""),
                        acao: str = Form(""), motivo: str = Form(""),
                        volta: str = Form("")):
    """Remarca a próxima ação. O motivo passa a ser obrigatório do 3º adiamento
    seguido sem nenhuma mensagem no meio — a trava que o dono pediu pra que
    reagendar não vire um jeito de silenciar o lead."""
    conta, _perfil, redir = _acesso(request)
    if redir is not None:
        return redir
    conta_id = conta[0]
    d = (quando or "").strip() if isinstance(quando, str) else ""
    h = (hora or "").strip() if isinstance(hora, str) else ""
    try:
        dia = datetime.strptime(d, "%Y-%m-%d").date()
    except ValueError:
        return RedirectResponse(_volta(volta, "data_invalida"), status_code=303)
    try:
        hh, mm = (int(x) for x in h.split(":")[:2])
    except (ValueError, TypeError):
        hh, mm = 9, 0
    # a tela fala em hora de Brasília; o banco guarda UTC
    prazo = datetime.combine(dia, time(hh, mm)).replace(tzinfo=timezone.utc) - _UTC_BR
    try:
        with get_pool().connection() as c:
            r = fu.marcar(c, conta_id, lead_id, prazo,
                          acao=(acao if isinstance(acao, str) else ""),
                          membro_id=request.session.get("membro_id"),
                          motivo=(motivo if isinstance(motivo, str) else ""))
            c.commit()
    except Exception:  # noqa: BLE001
        return RedirectResponse(_volta(volta, "falhou"), status_code=303)
    if not r.get("ok"):
        return RedirectResponse(_volta(volta, r.get("erro", "falhou")), status_code=303)
    return RedirectResponse(_volta(volta, ""), status_code=303)


def _volta(volta: str, erro: str) -> str:
    base = volta if (isinstance(volta, str) and volta.startswith("/painel/follow-up")) else "/painel/follow-up"
    if not erro:
        return base
    return base + ("&" if "?" in base else "?") + "erro=" + erro


_TPL = r"""{% extends "base" %}{% block conteudo %}
<style>
.fu{display:flex;flex-direction:column;gap:1rem}
.fu h1{font-size:1.5rem;margin:0}
.fu .lede{color:var(--text-dim);font-size:.88rem;margin:.2rem 0 0;max-width:70ch}
.fu-tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:.6rem}
@media (max-width:820px){.fu-tiles{grid-template-columns:repeat(2,1fr)}}
.fu-tile{border:1px solid var(--line);border-radius:12px;background:var(--surface);padding:.7rem .8rem;display:flex;flex-direction:column;gap:.1rem;text-decoration:none;color:inherit}
.fu-tile b{font:600 1.7rem/1 var(--display);font-variant-numeric:tabular-nums}
.fu-tile span{font-size:.66rem;color:var(--text-faint);letter-spacing:.02em}
.fu-tile em{font:500 .6rem var(--mono);font-style:normal;color:var(--text-dim);margin-top:.15rem}
.fu-tile.hoje b{color:var(--ambar)}.fu-tile.atrasado b{color:var(--coral)}
.fu-tile.critico b{color:var(--coral)}.fu-tile.sem_acao b{color:var(--text-dim)}
.fu-tile.on{border-color:var(--neon-borda);background:var(--neon-fundo)}
.fu-bar{display:flex;flex-wrap:wrap;gap:.4rem;padding:.6rem .8rem;border:1px solid var(--line);border-radius:12px;background:var(--bg-2);align-items:center}
.fu-bar label{display:inline-flex;align-items:center;gap:.35rem;border:1px solid var(--line);border-radius:999px;padding:.2rem .35rem .2rem .7rem;font-size:.74rem;color:var(--text-dim);background:var(--surface);margin:0}
.fu-bar label.on{border-color:var(--neon-borda);background:var(--neon-fundo);color:var(--text)}
.fu-bar select{width:auto;margin:0;padding:.15rem .3rem;font-size:.76rem;background:transparent;border:0;color:var(--text);font-weight:500}
.fu-bar select:focus{outline:none}
.fu-lista{border:1px solid var(--line);border-radius:12px;background:var(--surface);overflow:hidden}
.fu-lead{display:grid;grid-template-columns:1fr auto;gap:.6rem;padding:.7rem .85rem;border-bottom:1px solid var(--line);align-items:start}
.fu-lead:last-child{border-bottom:none}
.fu-lead .nome{display:flex;align-items:center;gap:.45rem;flex-wrap:wrap;margin-bottom:.3rem}
.fu-lead .nome a{font:600 .95rem var(--display);color:var(--text);text-decoration:none}
.fu-lead .meta{display:flex;flex-wrap:wrap;gap:.2rem .8rem;font-size:.72rem;color:var(--text-dim)}
.fu-lead .meta i{font-style:normal;color:var(--text-faint);margin-right:.2rem}
.fu-lead .meta b{color:var(--text);font-weight:500}
.fu-acao{margin-top:.35rem;font-size:.76rem;color:#F0DCA6;background:var(--ambar-fundo);border:1px solid var(--ambar-borda);border-radius:8px;padding:.35rem .55rem;display:inline-block}
.fu-acao b{color:#fff}
.fu-acao.calma{color:var(--text-dim);background:var(--surface-2);border-color:var(--line)}
.fu-acao.calma b{color:var(--text)}
.fu-pill{display:inline-flex;align-items:center;gap:.25rem;font:600 .64rem var(--mono);border-radius:999px;padding:.1rem .45rem;border:1px solid var(--line);color:var(--text-dim);white-space:nowrap}
.fu-pill.critico,.fu-pill.atrasado{color:var(--coral);border-color:var(--coral-borda);background:var(--coral-fundo)}
.fu-pill.hoje{color:var(--ambar);border-color:var(--ambar-borda);background:var(--ambar-fundo)}
.fu-pill.agendado,.fu-pill.andamento{color:var(--neon-bright);border-color:var(--neon-borda);background:var(--neon-fundo)}
.fu-dir{display:flex;flex-direction:column;gap:.3rem;align-items:flex-end}
.fu-dir a.bt,.fu-dir summary{font:500 .7rem var(--body);border:1px solid var(--line);background:var(--bg-2);color:var(--text-dim);border-radius:8px;padding:.28rem .55rem;white-space:nowrap;cursor:pointer;list-style:none;text-decoration:none}
.fu-dir a.bt:hover,.fu-dir summary:hover{border-color:var(--neon-borda);color:var(--neon-bright)}
.fu-dir details[open] summary{border-color:var(--neon-borda);color:var(--neon-bright)}
.fu-form{margin-top:.35rem;display:flex;flex-direction:column;gap:.3rem;border:1px solid var(--line);border-radius:10px;padding:.5rem;background:var(--bg-2);min-width:230px}
.fu-form input,.fu-form select{margin:0;font-size:.76rem;padding:.25rem .35rem}
.fu-form button{font-size:.74rem;padding:.3rem .5rem;margin:0}
.fu-form .dica{font-size:.64rem;color:var(--text-faint)}
.fu-gest{width:100%;border-collapse:collapse;font-size:.8rem}
.fu-gest th{font:500 .6rem var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--text-faint);text-align:left;padding:.4rem .5rem;border-bottom:1px solid var(--line)}
.fu-gest td{padding:.45rem .5rem;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}
.fu-gest td.n{text-align:right}
.fu-gest .ruim{color:var(--coral)}.fu-gest .amb{color:var(--ambar)}
.fu-nota{font-size:.74rem;color:var(--text-faint);border-left:2px solid var(--line);padding-left:.6rem}
.fu-erro{font-size:.78rem;color:#F2BDB9;background:var(--coral-fundo);border:1px solid var(--coral-borda);border-radius:8px;padding:.45rem .6rem}
.fu-vazio{padding:1.4rem .9rem;text-align:center;color:var(--text-faint);font-size:.85rem}
</style>
<div class="fu">
  <div>
    <h1>Follow-up</h1>
    <p class="lede">A próxima ação de cada lead em jogo, proposta pelo sistema e corrigida por você.
    O relógio lê a conversa — mensagem enviada pelo celular também conta. Abrir o card não encerra nada.</p>
    {% if modo == 'off' %}<p class="lede" style="color:var(--ambar)">Os avisos automáticos estão <b>desligados</b>: a tela mostra o quadro, mas ninguém recebe push nem e-mail. Ligue em <a href="/painel/prospeccao/funil">Funil</a> quando quiser.</p>
    {% elif modo == 'observando' %}<p class="lede" style="color:var(--azul)">Os avisos estão em <b>ensaio</b>: o sistema calcula e grava o que mandaria, sem mandar nada a ninguém.</p>{% endif %}
  </div>

  {% if erro == 'motivo_obrigatorio' %}
    <div class="fu-erro">Este lead já foi adiado {{ adia_max - 1 }} vezes sem ninguém falar com o cliente. Pra adiar de novo, escreva o motivo.</div>
  {% elif erro == 'data_invalida' %}<div class="fu-erro">Escolha uma data pra próxima ação.</div>
  {% elif erro %}<div class="fu-erro">Não deu pra remarcar agora. Tente de novo.</div>{% endif %}

  {% macro qs(e) %}?estado={{ e }}{% if vend_f %}&vendedor={{ vend_f }}{% endif %}{% if etapa_f %}&etapa={{ etapa_f }}{% endif %}{% endmacro %}
  <div class="fu-tiles">
    <a class="fu-tile hoje {% if estado=='hoje' %}on{% endif %}" href="/painel/follow-up{{ qs('hoje') }}">
      <b>{{ topo.hoje }}</b><span>FOLLOW-UPS HOJE</span><em>{{ topo.andamento }} em andamento</em></a>
    <a class="fu-tile atrasado {% if estado=='atrasado' %}on{% endif %}" href="/painel/follow-up{{ qs('atrasado') }}">
      <b>{{ topo.atrasado }}</b><span>ATRASADOS</span><em>até 72h do prazo</em></a>
    <a class="fu-tile critico {% if estado=='critico' %}on{% endif %}" href="/painel/follow-up{{ qs('critico') }}">
      <b>{{ topo.critico }}</b><span>CRÍTICOS +72H</span><em>{{ topo.com_festa }} com data marcada</em></a>
    <a class="fu-tile sem_acao {% if estado=='sem_acao' %}on{% endif %}" href="/painel/follow-up{{ qs('sem_acao') }}">
      <b>{{ topo.sem_acao }}</b><span>SEM PRÓXIMA AÇÃO</span><em>a data já passou</em></a>
  </div>

  <form method="get" action="/painel/follow-up" class="fu-bar">
    <input type="hidden" name="estado" value="{{ estado }}">
    {% if vendedores %}
    <label class="{{ 'on' if vend_f }}">Vendedor
      <select name="vendedor" onchange="this.form.submit()"><option value="">todos</option>
        {% for vid, vnome in vendedores %}<option value="{{ vid }}" {% if vend_f==vid %}selected{% endif %}>{{ vnome }}</option>{% endfor %}
      </select></label>
    {% endif %}
    {% if etapas %}
    <label class="{{ 'on' if etapa_f }}">Etapa
      <select name="etapa" onchange="this.form.submit()"><option value="">todas</option>
        {% for e in etapas %}<option value="{{ e }}" {% if etapa_f==e %}selected{% endif %}>{{ e }}</option>{% endfor %}
      </select></label>
    {% endif %}
    <label class="{{ 'on' if estado=='todos' }}" style="cursor:pointer" onclick="location.href='/painel/follow-up{{ qs('todos') }}'">Ver todos os {{ topo.ativos }}</label>
  </form>

  <div class="fu-lista">
    {% for x in fila %}
    <div class="fu-lead">
      <div>
        <div class="nome">
          <a href="/painel/prospeccao/lead/{{ x.id }}">{{ x.quem }}</a>
          <span class="fu-pill {{ x.estado }}">{{ emoji[x.estado] }} {{ rotulo[x.estado] }}</span>
          <span class="fu-pill">{{ x.bola }}</span>
          {% if x.adiados >= adia_max %}<span class="fu-pill hoje">🔁 adiado {{ x.adiados }}×</span>{% endif %}
        </div>
        <div class="meta">
          {% if perfil.vocab.data %}
            {% if x.evento_em %}<span><i>🎉</i>{{ x.evento_tipo or 'Festa' }} <b>{{ x.evento_em.strftime('%d/%m') }}</b>{% if x.faltam is not none %} · {% if x.faltam < 0 %}<b style="color:var(--coral)">a data passou</b>{% else %}em {{ x.faltam }} dias{% endif %}{% endif %}</span>
            {% else %}<span><i>🎉</i>sem data definida</span>{% endif %}
            {% if x.convidados %}<span><i>👥</i>{{ x.convidados }} convidados</span>{% endif %}
          {% endif %}
          <span><i>⏱️</i><b>{{ tempo(x.parado_h) }}</b> sem interação</span>
          <span><i>🔄</i>{{ x.tentativas }} tentativa{{ '' if x.tentativas == 1 else 's' }}</span>
          <span><i>📍</i>{{ x.status }}</span>
          <span><i>👤</i>{{ x.vendedor }}</span>
          {% if x.adiados %}<span><i>🔁</i>adiado <b>{{ x.adiados }}×</b>{% if x.adiado_por %} sem mensagem no meio{% endif %}</span>{% endif %}
        </div>
        {% if x.estado == 'sem_acao' %}
          <div class="fu-acao calma">A data já passou. <b>Encerre com motivo, ou remarque.</b></div>
        {% else %}
          <div class="fu-acao"><span>📅 Próxima ação:</span> <b>{{ x.acao }}</b>
            {% if x.atraso_h > 0 %}— venceu há {{ tempo(x.atraso_h) }}{% else %}— {{ br(x.prazo) }}{% endif %}
            {% if x.na_mao %} · marcada na mão{% endif %}</div>
        {% endif %}
      </div>
      <div class="fu-dir">
        <a class="bt" href="/painel/prospeccao/lead/{{ x.id }}">Abrir</a>
        <details>
          <summary>Remarcar</summary>
          <form method="post" action="/painel/follow-up/reagendar" class="fu-form">
            <input type="hidden" name="lead_id" value="{{ x.id }}">
            <input type="hidden" name="volta" value="/painel/follow-up{{ qs(estado) }}">
            <input type="date" name="quando" required>
            <input type="time" name="hora" value="09:00">
            <input type="text" name="acao" maxlength="120" placeholder="o que fazer (opcional)">
            <input type="text" name="motivo" maxlength="300" placeholder="motivo{% if x.adiados >= adia_max - 1 %} (obrigatório){% endif %}">
            <span class="dica">Do {{ adia_max }}º adiamento seguido sem falar com o cliente, o motivo é obrigatório.</span>
            <button type="submit">Remarcar</button>
          </form>
        </details>
      </div>
    </div>
    {% else %}
    <div class="fu-vazio">Nada em <b>{{ rotulo.get(estado, estado) }}</b> por aqui. Fila limpa.</div>
    {% endfor %}
  </div>
  {% if sobrando %}<p class="fu-nota">Mais {{ sobrando }} nesta fila. Filtre por vendedor ou etapa pra chegar neles.</p>{% endif %}

  {% if gestao %}
  <div>
    <h2 style="font-size:1.05rem;margin:.6rem 0 .4rem">Por vendedor</h2>
    <div style="overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--surface)">
      <table class="fu-gest">
        <thead><tr><th>Vendedor</th><th class="n">Ativos</th><th class="n">🟡 Hoje</th><th class="n">🔴 Atrasados</th><th class="n">🚨 Críticos</th><th class="n">⚠️ Sem ação</th><th class="n">Adiados {{ adia_max }}×+</th></tr></thead>
        <tbody>
          {% for v in gestao %}
          <tr>
            <td>{{ v.nome }}</td><td class="n">{{ v.ativos }}</td>
            <td class="n {{ 'amb' if v.hoje }}">{{ v.hoje }}</td>
            <td class="n {{ 'ruim' if v.atrasado }}">{{ v.atrasado }}</td>
            <td class="n {{ 'ruim' if v.critico }}">{{ v.critico }}</td>
            <td class="n">{{ v.sem_acao }}</td>
            <td class="n {{ 'amb' if v.adiados }}">{{ v.adiados or '—' }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
    <p class="fu-nota" style="margin-top:.5rem">A coluna de adiamentos conta quem foi empurrado pra frente {{ adia_max }} vezes ou mais <b>sem nenhuma mensagem no meio</b>. Adiar depois de falar com o cliente é trabalho; adiar sem falar é adiar.</p>
  </div>
  {% endif %}
</div>
{% endblock %}"""

_env.loader.mapping["follow_up"] = _TPL
