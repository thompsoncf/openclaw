"""A tela Hoje da clínica: /painel/hoje — o que a recepção resolve no dia.

SÓ APARECE PRA CLÍNICA (CLAUDE.md §6): o perfil do nicho tem que ser `clinica`.
Festa, visita, convidados e orçamento não existem aqui; paciente, consulta e
horário, sim.

O QUE A TELA MOSTRA, de cima pra baixo:
  0. O agente passou pra você — o que o agente do WhatsApp não resolve sozinho
     (finance/clinica_agente.py): saúde, foto, áudio, convênio, desconto,
     remarcar, urgência. Só aparece quando tem item.
  1. Esperando resposta — quem escreveu e não teve retorno (3 dias pra trás),
     marcando quem escreveu com a clínica fechada.
  2. Voltar a chamar hoje — quem recebeu o preço da consulta e está calado, com o
     toque pronto (finance/voltar_a_chamar.py).
  3. Repescagem — quem ficou parado antes da conta ligar: uma mensagem só, sempre
     na mão.
  4. Responderam e não marcaram — com a última frase do paciente.
  5. O placar do mês.
  6. O modo e os textos (só dono e gestor).

QUEM VÊ O QUÊ: dono, gestor e vendedor (a recepção entra como vendedor) veem e
mandam. Só dono e gestor mudam o modo e os textos.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db.conexao import get_pool
from finance import clinica_agente as cla
from finance import funil_regua as fr
from finance import raio_x_perfil as rxp
from finance import voltar_a_chamar as vac
from web.portal import _env, _render, conta_logada, nicho_da_conta

router = APIRouter()
_log = logging.getLogger("openclaw.painel_hoje")

_PAPEIS_OK = ("dono", "gestor", "vendedor")

_AVISOS = {
    "mandado": "Mensagem enviada.",
    "ja_tratado": "Esse toque já tinha sido tratado por outra pessoa.",
    "falhou": "Não deu para enviar agora. O toque ficou marcado como falho.",
    "dispensado": "Pronto: esse paciente não recebe mais toques deste preço.",
    "nao_paciente": "Pronto: esse número não recebe mais toque nenhum.",
    "marcou": "Anotado como marcado. O card foi para Consulta agendada.",
    "salvo": "Configuração salva.",
    "hoje_ja": "Esse paciente já recebeu uma mensagem hoje. O próximo toque fica para amanhã.",
    "desligado": "Voltar a chamar está desligado: nada sai, nem clicando.",
    "resolvido": "Pronto: saiu da lista.",
}


def _acesso(request: Request):
    """Devolve (conta, gerencia, redirect). `gerencia` é quem muda o modo."""
    conta = conta_logada(request)
    if conta is None:
        return None, False, RedirectResponse("/login", status_code=303)
    papel = request.session.get("papel", "dono")
    if papel not in _PAPEIS_OK:
        return None, False, RedirectResponse("/painel", status_code=303)
    if rxp.perfil_por_nicho(nicho_da_conta(conta)) != "clinica":
        return None, False, RedirectResponse("/painel", status_code=303)
    return conta, papel in ("dono", "gestor"), None


def _ir(request: Request, aviso: str = "", erro: str = "") -> RedirectResponse:
    """Volta pra tela. O aviso vai na URL como CÓDIGO (traduzido por `_AVISOS`); o
    erro de validação, que é texto livre, vai pela sessão — texto da URL impresso na
    tela é o link que alguém manda pra recepção com um script dentro."""
    if erro:
        request.session["hoje_erro"] = erro[:300]
    return RedirectResponse("/painel/hoje" + (f"?aviso={aviso}" if aviso in _AVISOS else ""),
                            status_code=303)


@router.get("/painel/hoje", response_class=HTMLResponse)
def painel_hoje(request: Request):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    conta_id = conta[0]
    agora = datetime.now(timezone.utc)
    pool = get_pool()
    with pool.connection() as c:
        cfg = vac.config(c, conta_id, "clinica")
        janela = fr.config(c, conta_id)
        if cfg["modo"] != "off":
            # a tela não espera o poller: quem respondeu SAIR ou marcou há um minuto
            # já não aparece com a mensagem pronta
            vac.atualizar_estados(c, conta_id, agora, cfg)
        dados = vac.hoje(c, conta_id, agora, cfg)
        repasses = cla.repasses_abertos(c, conta_id)
        from finance import clinica_vagas as cvg
        vagas_esperando = cvg.esperando(c, conta_id)
        from finance import clinica_planos as cpl
        planos = cpl.em_aberto(c, conta_id, agora)
        from finance import clinica_pacotes as ckp
        pac_marcar = ckp.precisam_marcar(c, conta_id, agora)
        pac_retornos = ckp.retornos(c, conta_id, agora, dias=7)
        c.commit()      # fr.config semeia a linha da régua na 1ª vez
    for e in dados["esperando"]:
        e["fora"] = not fr.dentro_da_janela(e["em"], janela)
    q = request.query_params
    return _render("hoje.html", request, titulo="Hoje", cfg=cfg, gerencia=gerencia,
                   d=dados, repasses=repasses, vagas_esperando=vagas_esperando, planos=planos, pac_marcar=pac_marcar, pac_retornos=pac_retornos, aviso=_AVISOS.get(q.get("aviso") or "", ""),
                   erro=request.session.pop("hoje_erro", ""),
                   rotulos=vac._ROTULO_TOQUE)


@router.post("/painel/hoje/toque/{toque_id}/{acao}")
def acao_no_toque(request: Request, toque_id: int, acao: str):
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return redir
    conta_id, membro = conta[0], request.session.get("membro_id")
    pool = get_pool()
    if acao == "mandar":
        r = vac.mandar_sugerido(pool, conta_id, toque_id, membro)
        if r.get("ok"):
            return _ir(request, "mandado")
        erro = r.get("erro")
        return _ir(request, erro if erro in ("ja_tratado", "hoje_ja", "desligado") else "falhou")
    if acao == "dispensar":
        vac.dispensar(pool, conta_id, toque_id)
        return _ir(request, "dispensado")
    if acao == "nao-paciente":
        vac.nao_e_paciente(pool, conta_id, toque_id, membro)
        return _ir(request, "nao_paciente")
    if acao == "ja-marcou":
        vac.ja_marcou(pool, conta_id, toque_id, membro)
        return _ir(request, "marcou")
    return _ir(request)


@router.post("/painel/hoje/repasse/{repasse_id}/resolvido")
def repasse_resolvido(request: Request, repasse_id: int):
    """"Resolvido" no item que o agente passou: some da lista. Responder a conversa
    já tira sozinho; o botão é pra quando a recepção resolveu por telefone."""
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return redir
    with get_pool().connection() as c:
        cla.resolver(c, conta[0], repasse_id, request.session.get("membro_id"))
        c.commit()
    return _ir(request, "resolvido")


@router.post("/painel/hoje/config")
def salvar_config(request: Request, modo: str = Form("off"), teto_dia: str = Form(""),
                  t0: str = Form(""), t1: str = Form(""), t2: str = Form(""),
                  t3: str = Form(""), t4: str = Form("")):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    if not gerencia:
        return _ir(request, erro="Só o dono ou o gestor muda o modo e os textos.")
    try:
        teto = int(teto_dia) if teto_dia.strip() else None
    except ValueError:
        teto = None
    textos = {"0": t0, "1": t1, "2": t2, "3": t3, "4": t4}
    with get_pool().connection() as c:
        erro = vac.salvar_config(c, conta[0], modo, textos, teto)
        if erro:
            c.rollback()
            return _ir(request, erro=erro)
        c.commit()
    return _ir(request, "salvo")


_TPL = r"""{% extends "base" %}{% block conteudo %}
<style>
.hj-pag{width:100%;max-width:var(--pag,1180px);margin:0 auto;padding:1.2rem 1rem 2.5rem;box-sizing:border-box}
.hj-topo h2{margin:0;font-size:1.5rem;line-height:1.15}
.hj-topo .sub{color:var(--txt-mut);font-size:.88rem;margin-top:.25rem}
.hj-placar{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:.5rem;margin:1rem 0 1.2rem}
.hj-cx{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.55rem .75rem}
.hj-cx .r{font-size:.66rem;text-transform:uppercase;letter-spacing:.06em;color:var(--txt-mut);display:block}
.hj-cx .v{display:block;font-size:1.35rem;font-weight:700;line-height:1.15}
.hj-cx .n{display:block;font-size:.72rem;color:var(--txt-mut)}
.hj-cx.alerta{background:var(--ambar-fundo);border-color:var(--ambar-borda)}
.hj-sec{margin:1.3rem 0 .5rem;display:flex;align-items:baseline;gap:.5rem;flex-wrap:wrap}
.hj-sec h3{margin:0;font-size:1.02rem}
.hj-sec .qt{font-size:.78rem;color:var(--txt-mut)}
.hj-sec .ex{font-size:.8rem;color:var(--txt-mut);flex-basis:100%}
.hj-lista{display:flex;flex-direction:column;gap:.45rem}
.hj-card{background:var(--card);border:1px solid var(--borda);border-left:3px solid var(--borda);
  border-radius:11px;padding:.6rem .8rem}
.hj-card.quente{border-left-color:var(--verde)}
.hj-card.fora{border-left-color:var(--amar)}
.hj-card.urgente{border-left-color:#e5484d;background:#3a1d1d}
.hj-card .chip.urgente{border-color:#6e2b2b;background:#3a1d1d;color:#f0b8b8;font-weight:700}
.hj-card .cab{display:flex;gap:.45rem;align-items:baseline;flex-wrap:wrap}
.hj-card .quem{font-weight:700;font-size:.95rem;color:var(--txt);text-decoration:none}
.hj-card .meta{font-size:.78rem;color:var(--txt-mut)}
.hj-card .chip{font-size:.68rem;padding:.08rem .45rem;border-radius:999px;border:1px solid var(--borda);color:var(--txt-mut)}
.hj-card .chip.fora{border-color:var(--ambar-borda);background:var(--ambar-fundo);color:#F0DCA6}
.hj-card .frase{font-size:.86rem;margin-top:.3rem;color:var(--txt);opacity:.9;overflow-wrap:anywhere}
.hj-card .msg{font-size:.86rem;margin:.4rem 0 .1rem;padding:.45rem .6rem;border-radius:9px;
  background:var(--neon-fundo);border:1px solid var(--neon-borda);overflow-wrap:anywhere}
.hj-acoes{display:flex;gap:.35rem;flex-wrap:wrap;margin-top:.45rem}
.hj-acoes form{margin:0}
.hj-acoes button{width:auto;margin:0;min-height:40px;padding:.4rem .8rem;font-size:.85rem;border-radius:8px}
.hj-acoes button.sec{background:transparent;border:1px solid var(--borda);color:var(--txt)}
.hj-acoes button.sec:hover{background:var(--card-2)}
.hj-acoes a.abre{display:inline-flex;align-items:center;min-height:40px;padding:0 .7rem;font-size:.85rem;
  color:var(--verde-claro);text-decoration:none}
.hj-vazio{font-size:.86rem;color:var(--txt-mut);padding:.5rem .1rem}
.hj-aviso{margin:.8rem 0}
.hj-modo{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.8rem .9rem;margin-top:1.6rem}
.hj-modo h3{margin:0 0 .3rem;font-size:1.02rem}
.hj-modo .ex{font-size:.82rem;color:var(--txt-mut);margin:.15rem 0 .6rem}
.hj-modo label{display:block;font-size:.8rem;color:var(--txt-mut);margin:.6rem 0 .2rem}
.hj-modo textarea{width:100%;min-height:64px;padding:.55rem .7rem;border-radius:8px;border:1px solid #333;
  background:var(--bg);color:var(--txt);font-size:1rem;font-family:inherit;box-sizing:border-box}
.hj-modo .linha{display:grid;grid-template-columns:1fr 1fr;gap:.6rem}
@media (max-width:560px){.hj-modo .linha{grid-template-columns:1fr}}
.hj-desligado{background:var(--ambar-fundo);border:1px solid var(--ambar-borda);border-radius:11px;
  padding:.6rem .8rem;font-size:.86rem;margin-top:1rem}
</style>
<div class="hj-pag">
  <div class="hj-topo">
    <h2>Hoje</h2>
    <div class="sub">Quem está esperando resposta e quem recebeu o preço da consulta e sumiu.</div>
  </div>
  {% if aviso %}<div class="ok hj-aviso">{{ aviso }}</div>{% endif %}
  {% if erro %}<div class="erro hj-aviso">{{ erro }}</div>{% endif %}

  {% if vagas_esperando %}
  <div class="hj-desligado" style="margin-top:1rem"><b>⚡ {{ vagas_esperando }} horário{% if vagas_esperando > 1 %}s{% endif %} liberado{% if vagas_esperando > 1 %}s{% endif %} por cancelamento</b> esperando você aprovar o convite. <a href="/painel/clinica/vagas">Abrir vagas liberadas</a></div>
  {% endif %}

  {% if planos.aprovar or planos.vencendo or planos.responderam %}
  <div class="hj-sec"><h3>Planos de tratamento</h3>
    <span class="ex">O Zaq cobra a decisão sozinho em D+1 e D+3. Aqui fica o que precisa de você.</span></div>
  <div class="hj-lista">
    {% if planos.aprovar and gerencia %}<div class="hj-card fora"><div class="cab"><a class="quem" href="/painel/clinica/planos">{{ planos.aprovar }} plano{% if planos.aprovar > 1 %}s{% endif %} com desconto acima do teto</a><span class="chip fora">esperando você aprovar</span></div></div>{% endif %}
    {% for p in planos.responderam %}<div class="hj-card quente"><div class="cab"><a class="quem" href="/painel/clinica/planos/{{ p.id }}">{{ p.paciente }}</a><span class="chip">respondeu depois do plano</span></div>
      <div class="hj-acoes"><a class="abre" href="/painel/clinica/planos/{{ p.id }}">Abrir plano</a></div></div>{% endfor %}
    {% for p in planos.vencendo if p not in planos.responderam %}<div class="hj-card fora"><div class="cab"><a class="quem" href="/painel/clinica/planos/{{ p.id }}">{{ p.paciente }}</a><span class="chip fora">vence {{ p.validade_ate.strftime('%d/%m') }}</span></div></div>{% endfor %}
  </div>
  {% endif %}

  {% if pac_marcar or pac_retornos %}
  <div class="hj-sec"><h3>Pacotes e retornos</h3><span class="qt">{{ pac_marcar|length + pac_retornos|length }}</span>
    <span class="ex">Sessão liberada sem marcar e retorno chegando. O Zaq lembra o paciente; aqui é pra quem quiser ligar antes. <a href="/painel/clinica/pacotes">Abrir pacotes</a></span></div>
  <div class="hj-lista">
    {% for k in pac_marcar %}<div class="hj-card quente"><div class="cab"><a class="quem" href="/painel/clinica/pacotes/{{ k.id }}">{{ k.paciente }}</a><span class="chip">{{ k.proxima_n }}ª sessão liberada</span></div></div>{% endfor %}
    {% for r in pac_retornos %}<div class="hj-card {% if r.vencido %}fora{% else %}quente{% endif %}"><div class="cab"><span class="quem">{{ r.paciente }}</span><span class="chip {% if r.vencido %}fora{% endif %}">retorno até {{ r.vence_em.strftime('%d/%m') }}</span></div></div>{% endfor %}
  </div>
  {% endif %}

  {% if repasses %}
  <div class="hj-sec"><h3>O agente passou pra você</h3><span class="qt">{{ repasses|length }}</span>
    <span class="ex">Assuntos que o agente do WhatsApp não resolve sozinho: saúde, foto, áudio, convênio, desconto, remarcar. Some da lista quando alguém responde a conversa.</span></div>
  <div class="hj-lista">
  {% for r in repasses %}
    <div class="hj-card {% if r.urgente %}urgente{% else %}quente{% endif %}">
      <div class="cab"><a class="quem" href="/painel/prospeccao/comunicacao?abrir={{ r.conversa_id }}">{{ r.nome }}</a>
        <span class="chip {% if r.urgente %}urgente{% endif %}">{{ r.rotulo }}</span>
        <span class="meta">{{ r.quando }}</span></div>
      <div class="hj-acoes">
        <a class="abre" href="/painel/prospeccao/comunicacao?abrir={{ r.conversa_id }}">Abrir conversa</a>
        <form method="post" action="/painel/hoje/repasse/{{ r.id }}/resolvido"><button class="sec">Resolvido</button></form>
      </div>
    </div>
  {% endfor %}
  </div>
  {% endif %}

  {% set p = d.placar %}
  <div class="hj-placar">
    <div class="hj-cx"><span class="r">Receberam o preço</span><span class="v">{{ p.receberam }}</span><span class="n">neste mês</span></div>
    <div class="hj-cx"><span class="r">Marcaram</span><span class="v">{{ p.marcaram }}</span><span class="n">{% if p.taxa is not none %}{{ p.taxa }}% de quem recebeu{% else %}&nbsp;{% endif %}</span></div>
    <div class="hj-cx {% if p.nunca_chamados %}alerta{% endif %}"><span class="r">Nunca chamados de novo</span><span class="v">{{ p.nunca_chamados }}</span><span class="n">passou 1 dia do preço, não marcaram e ninguém voltou a falar</span></div>
    <div class="hj-cx"><span class="r">Esperando resposta</span><span class="v">{{ d.esperando|length }}</span><span class="n">escreveram e estão sem retorno</span></div>
  </div>

  <div class="hj-sec"><h3>Esperando resposta</h3><span class="qt">{{ d.esperando|length }}</span>
    <span class="ex">Do mais antigo para o mais novo, dos últimos 3 dias.</span></div>
  <div class="hj-lista">
  {% for e in d.esperando %}
    <div class="hj-card {% if e.fora %}fora{% else %}quente{% endif %}">
      <div class="cab"><a class="quem" href="/painel/prospeccao/comunicacao?abrir={{ e.conversa_id }}">{{ e.nome }}</a>
        <span class="meta">{{ e.quando }}</span>
        {% if e.fora %}<span class="chip fora">chegou com a clínica fechada</span>{% endif %}</div>
      <div class="frase">{{ e.texto }}</div>
    </div>
  {% else %}<div class="hj-vazio">Ninguém esperando. 🎉</div>{% endfor %}
  </div>

  {% if cfg.modo == 'off' %}
  <div class="hj-desligado"><b>Voltar a chamar está desligado.</b> Quando a recepção passa o preço da consulta e o paciente
    some, o Zaq pode preparar a mensagem de retorno. {% if gerencia %}Escolha <b>Sugere</b> no fim da página para começar: nada sai sem alguém apertar Mandar.{% else %}Peça ao dono para ligar.{% endif %}</div>
  {% else %}
  <div class="hj-sec"><h3>Voltar a chamar hoje</h3><span class="qt">{{ d.voltar|length }}</span>
    <span class="ex">Receberam o preço da consulta, não marcaram e a conversa parou.{% if cfg.modo == 'ligado' %} O Zaq manda sozinho no horário de atendimento; você pode mandar antes.{% endif %}{% if d.proximos %} Mais {{ d.proximos }} programado(s) para os próximos dias.{% endif %}</span></div>
  <div class="hj-lista">
  {% for t in d.voltar %}{% set tipo = 'toque' %}{% include "hoje_toque.html" %}
  {% else %}<div class="hj-vazio">Nada para hoje.</div>{% endfor %}
  </div>

  {% if d.repescagem %}
  <div class="hj-sec"><h3>Repescagem</h3><span class="qt">{{ d.repescagem|length }}</span>
    <span class="ex">Receberam o preço antes de o recurso ser ligado e não voltaram. Uma mensagem só, e ela nunca sai sozinha.</span></div>
  <div class="hj-lista">
  {% for t in d.repescagem %}{% set tipo = 'repescagem' %}{% include "hoje_toque.html" %}{% endfor %}
  </div>
  {% endif %}

  <div class="hj-sec"><h3>Responderam e não marcaram</h3><span class="qt">{{ d.responderam|length }}</span>
    <span class="ex">Escreveram depois do preço e ainda não marcaram, dos últimos 30 dias. A última frase do paciente.</span></div>
  <div class="hj-lista">
  {% for r in d.responderam %}
    <div class="hj-card">
      <div class="cab"><a class="quem" href="/painel/prospeccao/comunicacao?abrir={{ r.conversa_id }}">{{ r.nome }}</a>
        <span class="meta">{{ r.quando }}</span></div>
      <div class="frase">“{{ r.frase }}”</div>
      <div class="hj-acoes">
        <a class="abre" href="/painel/prospeccao/comunicacao?abrir={{ r.conversa_id }}">Abrir conversa</a>
        <form method="post" action="/painel/hoje/toque/{{ r.id }}/ja-marcou"><button class="sec">Já marcou</button></form>
        <form method="post" action="/painel/hoje/toque/{{ r.id }}/dispensar"><button class="sec">Não chamar</button></form>
      </div>
    </div>
  {% else %}<div class="hj-vazio">Ninguém nessa situação.</div>{% endfor %}
  </div>
  {% endif %}

  {% if gerencia %}
  <form class="hj-modo" method="post" action="/painel/hoje/config">
    <h3>Voltar a chamar depois do preço</h3>
    <div class="ex">+3 horas (só no mesmo dia), +1 dia, +3 dias e +7 dias depois do preço, sempre no horário de atendimento da Régua, com a conversa parada há 3 horas e no máximo 1 por dia para cada paciente. Quem marca ou pede para sair não recebe mais nada; pergunta do paciente sem resposta fica para a recepção.</div>
    <div class="linha">
      <div><label for="hj-modo">Modo</label>
        <select id="hj-modo" name="modo">
          <option value="off" {% if cfg.modo == 'off' %}selected{% endif %}>Desligado</option>
          <option value="sugere" {% if cfg.modo == 'sugere' %}selected{% endif %}>Sugere — a recepção aperta Mandar</option>
          <option value="ligado" {% if cfg.modo == 'ligado' %}selected{% endif %}>Ligado — o Zaq manda sozinho</option>
        </select></div>
      <div><label for="hj-teto">Máximo por dia (a clínica toda)</label>
        <input id="hj-teto" name="teto_dia" inputmode="numeric" value="{{ cfg.teto_dia }}"></div>
    </div>
    {% for k in ['1','2','3','4','0'] %}
      <label for="hj-t{{ k }}">{% if k == '0' %}Repescagem{% else %}Toque {{ rotulos[k|int] }}{% endif %}{% if k in cfg.textos_da_conta %} · alterado por você{% endif %}</label>
      <textarea id="hj-t{{ k }}" name="t{{ k }}" maxlength="500">{{ cfg.textos[k] }}</textarea>
    {% endfor %}
    <div class="ex">Use {nome} para o primeiro nome. O texto não pode falar de preço nem de saúde.</div>
    <div class="hj-acoes"><button>Salvar</button></div>
  </form>
  {% endif %}
</div>
<script>
/* o segundo clique no Mandar não manda de novo — quem garante é o banco
   (update ... where estado='pendente'); isto só evita a pessoa achar que falhou */
document.querySelectorAll('.hj-acoes form').forEach(function(f){
  f.addEventListener('submit', function(){ var b = f.querySelector('button'); if(b){ b.disabled = true; } });
});
</script>
{% endblock %}"""

_TPL_TOQUE = r"""<div class="hj-card quente">
  <div class="cab"><a class="quem" href="/painel/prospeccao/comunicacao?abrir={{ t.conversa_id }}">{{ t.nome }}</a>
    <span class="chip">{{ t.rotulo }}</span>
    <span class="meta">preço em {{ t.preco_em }}</span></div>
  {% if t.ultima %}<div class="frase">Paciente: “{{ t.ultima }}”</div>{% endif %}
  <div class="msg">{{ t.texto }}</div>
  <div class="hj-acoes">
    <form method="post" action="/painel/hoje/toque/{{ t.id }}/mandar"><button>Mandar</button></form>
    <form method="post" action="/painel/hoje/toque/{{ t.id }}/ja-marcou"><button class="sec">Já marcou</button></form>
    <form method="post" action="/painel/hoje/toque/{{ t.id }}/dispensar"><button class="sec">Não chamar</button></form>
    <form method="post" action="/painel/hoje/toque/{{ t.id }}/nao-paciente"><button class="sec">Não é paciente</button></form>
  </div>
</div>"""

# ".html" NÃO É ENFEITE: o `_env` liga o autoescape pela extensão
# (select_autoescape). Sem ela, o nome e a mensagem que chegam pelo WhatsApp —
# qualquer número, até desconhecido — iam crus pra tela, com a sessão da recepção.
_env.loader.mapping["hoje.html"] = _TPL
_env.loader.mapping["hoje_toque.html"] = _TPL_TOQUE
