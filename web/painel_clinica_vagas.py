"""Vagas liberadas da clínica: /painel/clinica/vagas (finance/clinica_vagas.py).

O horário que abre por cancelamento, quem cabe nele e em que ordem, o convite na
rua e quem ficou com ele. A recepção aprova cada horário no modo 'aprova' (o
padrão do 1º mês); dono e gestor mudam o modo.

SÓ A CLÍNICA (CLAUDE.md §6), e a mesma porta da agenda da clínica: dono, gestor e
vendedor (a recepção entra como vendedor).
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse

from db.conexao import get_pool
from finance import clinica_vagas as cvg
from finance import funil_regua as fr
from web.painel_clinica_agenda import _acesso, _ir
from web.portal import _env, _render

router = APIRouter()
_log = logging.getLogger("openclaw.painel_clinica_vagas")

_AVISOS_VAGA = {
    "mandada": "Convite enviado para a 1ª rodada. Fica com quem responder 1 primeiro.",
    "aprovada_fora": "Aprovado. A clínica está fora do horário de atendimento: o convite sai quando abrir.",
    "ninguem": "Ninguém cabe nesse horário agora. Ele ficou livre na agenda.",
    "ocupada": "Esse horário já foi ocupado na agenda.",
    "balcao": "Falta menos de 1 hora: ofereça no balcão.",
    "ja_tratada": "Essa vaga já tinha sido tratada.",
    "parada": "Pronto: o horário fica livre na agenda, sem convite.",
    "salvo": "Configuração salva.",
}
_ESTADO_D = {"aguardando": "esperando aprovar", "oferta": "em oferta", "preenchida": "preenchida",
             "livre": "ficou livre", "balcao": "ofereça no balcão", "ocupada": "ocupada na agenda"}
_OFERTA_D = {"enviada": "✓ enviada", "ganhou": "ficou com a vaga", "perdeu": "chegou depois",
             "recusou": "respondeu 2", "parar": "pediu PARAR", "falhou": "não saiu", "expirou": "sem resposta"}

URL = "/painel/clinica/vagas"


@router.get(URL, response_class=HTMLResponse)
def vagas(request: Request):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    conta_id = conta[0]
    agora = datetime.now(timezone.utc)
    with get_pool().connection() as c:
        cfg = cvg.config(c, conta_id)
        if cfg["modo"] != "off":
            # a tela não espera o poller: o cancelamento de um minuto atrás já aparece
            try:
                cvg.detectar(c, conta_id, agora)
                cvg.fechar(c, conta_id, agora)
                c.commit()
            except Exception:  # noqa: BLE001
                c.rollback()
                _log.info("vagas: detectar na tela falhou (conta %s)", conta_id, exc_info=True)
        lista = cvg.listar(c, conta_id, agora)
        for v in lista:
            v["estado_d"] = _ESTADO_D.get(v["estado"], v["estado"])
            v["ofertas"] = cvg.ofertas(c, conta_id, v["id"])
            for o in v["ofertas"]:
                o["estado_d"] = _OFERTA_D.get(o["estado"], o["estado"])
            v["ate"] = cvg.ca.hora_txt(v["oferta_ate"]) if v["oferta_ate"] else ""
            if v["estado"] == "aguardando":
                chamar, fora = cvg.candidatos(c, conta_id, v, agora)
                v["rodada1"], v["rodada2"], v["fora"] = chamar[:3], chamar[3:8], fora
        janela = fr.config(c, conta_id)
        c.commit()
    aberta = fr.dentro_da_janela(agora, janela)
    q = request.query_params
    return _render("clinica_vagas.html", request, titulo="Vagas liberadas", secao_ativa="agenda",
                   aviso=_AVISOS_VAGA.get(q.get("aviso") or "", ""), erro=request.session.pop("agenda_erro", ""),
                   cfg=cfg, gerencia=gerencia, lista=lista, aberta=aberta, MODOS=cvg.MODOS,
                   abertas=[v for v in lista if v["estado"] in ("aguardando", "oferta")],
                   antigas=[v for v in lista if v["estado"] not in ("aguardando", "oferta")])


def _ir_vagas(request: Request, aviso: str = "", erro: str = ""):
    resp = _ir(request, URL, erro=erro)
    if aviso in _AVISOS_VAGA:
        resp.headers["location"] = URL + f"?aviso={aviso}"
    return resp


@router.post(URL + "/{vaga_id}/aprovar")
def aprovar(request: Request, vaga_id: int):
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return redir
    agora = datetime.now(timezone.utc)
    with get_pool().connection() as c:
        janela = fr.config(c, conta[0])
        c.commit()
        aviso = cvg.aprovar(c, conta[0], vaga_id, request.session.get("membro_id"), agora, janela)
        c.commit()
    return _ir_vagas(request, aviso)


@router.post(URL + "/{vaga_id}/parar")
def parar(request: Request, vaga_id: int):
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return redir
    with get_pool().connection() as c:
        ok = cvg.parar(c, conta[0], vaga_id)
        c.commit()
    return _ir_vagas(request, "parada" if ok else "ja_tratada")


@router.post(URL + "/config")
def salvar_config(request: Request, modo: str = Form("aprova"), teto_dia: str = Form("")):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    if not gerencia:
        return _ir_vagas(request, erro="Só o dono ou o gestor muda o modo das vagas.")
    try:
        teto = int(teto_dia) if teto_dia.strip() else None
    except ValueError:
        teto = None
    with get_pool().connection() as c:
        erro = cvg.salvar_config(c, conta[0], modo, teto)
        (c.rollback if erro else c.commit)()
    return _ir_vagas(request, "" if erro else "salvo", erro or "")


_TPL = r"""{% extends "base" %}{% block conteudo %}
<style>
.vg-pag{width:100%;max-width:var(--pag,1180px);margin:0 auto;padding:1.2rem 1rem 2.5rem;box-sizing:border-box}
.vg-topo{display:flex;align-items:flex-end;justify-content:space-between;gap:.8rem;flex-wrap:wrap}
.vg-topo h2{margin:0;font-size:1.5rem}
.vg-topo .sub{color:var(--txt-mut);font-size:.86rem;margin-top:.2rem;max-width:62ch}
.vg-estado{font-size:.8rem;color:var(--txt-mut);margin:.7rem 0 1rem;display:flex;gap:.6rem;flex-wrap:wrap}
.vg-card{background:var(--card);border:1px solid var(--borda);border-left:3px solid var(--borda);border-radius:11px;
  padding:.7rem .85rem;margin:.5rem 0}
.vg-card.aguardando{border-left-color:var(--amar)} .vg-card.oferta{border-left-color:var(--amar);background:var(--ambar-fundo)}
.vg-card.preenchida{border-left-color:var(--verde)} .vg-card.balcao,.vg-card.livre,.vg-card.ocupada{opacity:.8}
.vg-card .cab{display:flex;gap:.5rem;align-items:baseline;flex-wrap:wrap}
.vg-card .quando{font-weight:700;font-size:1rem}
.vg-chip{font-size:.7rem;padding:.08rem .45rem;border-radius:999px;border:1px solid var(--borda);color:var(--txt-mut)}
.vg-ctx{font-size:.82rem;color:var(--txt-mut);margin-top:.25rem}
.vg-rod{font-size:.72rem;text-transform:uppercase;letter-spacing:.05em;color:var(--txt-mut);margin:.6rem 0 .2rem}
.vg-p{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:.1rem .6rem;padding:.35rem 0;border-top:1px solid var(--borda)}
.vg-p .nome{font-weight:600;font-size:.9rem}
.vg-p .porque{font-size:.8rem;color:var(--txt-mut);grid-column:1/2}
.vg-p .st{font-size:.78rem;color:var(--txt-mut);text-align:right;grid-row:1/3;grid-column:2/3;align-self:center}
.vg-fora{font-size:.8rem;color:var(--txt-mut);margin-top:.4rem}
.vg-fora summary{cursor:pointer}
.vg-acoes{display:flex;gap:.35rem;flex-wrap:wrap;margin-top:.6rem}
.vg-acoes form{margin:0}
.vg-acoes button,.vg-acoes a{width:auto;margin:0;min-height:40px;padding:.4rem .8rem;font-size:.85rem;border-radius:8px}
.vg-acoes button.sec,.vg-acoes a{background:transparent;border:1px solid var(--borda);color:var(--txt);text-decoration:none;
  display:inline-flex;align-items:center}
.vg-vazio{font-size:.86rem;color:var(--txt-mut);padding:.5rem .1rem}
.vg-modo{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.8rem .9rem;margin-top:1.6rem}
.vg-modo h3{margin:0 0 .3rem;font-size:1.02rem}
.vg-modo .ex{font-size:.82rem;color:var(--txt-mut);margin:.15rem 0 .6rem}
.vg-modo label{display:block;font-size:.8rem;color:var(--txt-mut);margin:.6rem 0 .2rem}
.vg-modo .linha{display:grid;grid-template-columns:2fr 1fr;gap:.6rem}
@media (max-width:560px){.vg-modo .linha{grid-template-columns:1fr}}
</style>
<div class="vg-pag">
  <div class="vg-topo"><div><h2>Vagas liberadas</h2>
    <div class="sub">Horário que abre por cancelamento vai para quem cabe nele. Fica com quem responder 1 primeiro.</div></div>
    <div class="vg-acoes" style="margin-top:0"><a href="/painel/clinica/agenda">‹ Agenda</a></div></div>
  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}
  {% if erro %}<div class="erro" style="margin-top:.8rem">{{ erro }}</div>{% endif %}
  <div class="vg-estado">
    <span>● {% if cfg.modo == 'aprova' %}A recepção aprova cada horário{% elif cfg.modo == 'auto' %}Automático{% else %}Desligado{% endif %}</span>
    <span>· {% if aberta %}clínica aberta: convite sai agora{% else %}clínica fechada: convite sai quando abrir{% endif %}</span>
    <span>· até {{ cfg.teto_dia }} convites por dia · 1 mensagem automática por paciente por dia</span>
  </div>

  {% if cfg.modo == 'off' %}
  <div class="vg-vazio">As vagas estão desligadas: cancelamento não vira convite. {% if gerencia %}Ligue no fim da página.{% else %}Peça ao dono para ligar.{% endif %}</div>
  {% endif %}

  {% for v in abertas %}
  <div class="vg-card {{ v.estado }}">
    <div class="cab"><span class="quando">{{ v.dia }} {{ v.hora }}</span><span>· {{ v.prof }} · {{ v.dur }} min{{ v.onde }}</span>
      <span class="vg-chip">{{ v.estado_d }}{% if v.estado == 'oferta' and v.ate %} · rodada {{ v.rodada }} até {{ v.ate }}{% endif %}</span></div>
    <div class="vg-ctx">{% if v.cancelou %}{{ v.cancelou }} cancelou.{% else %}Horário cancelado.{% endif %}
      {% if v.estado == 'aguardando' and v.aprovada_em %} Aprovado: o convite sai quando a clínica abrir.{% endif %}</div>
    {% if v.estado == 'aguardando' %}
      {% if v.rodada1 %}
      <div class="vg-rod">1ª rodada · sai quando você aprovar</div>
      {% for p in v.rodada1 %}<div class="vg-p"><span class="nome">{{ p.nome }} <span class="vg-chip">{{ p.grupo_d }}</span></span>
        <span class="st">{{ p.tipo }}</span><span class="porque">{{ p.porque }}</span></div>{% endfor %}
      {% if v.rodada2 %}<div class="vg-rod">2ª rodada · sai sozinha em 20 min, se ninguém responder 1</div>
      {% for p in v.rodada2 %}<div class="vg-p"><span class="nome">{{ p.nome }} <span class="vg-chip">{{ p.grupo_d }}</span></span>
        <span class="st">{{ p.tipo }}</span><span class="porque">{{ p.porque }}</span></div>{% endfor %}{% endif %}
      {% else %}<div class="vg-vazio">Ninguém cabe nesse horário agora (quem pediu horário, recebeu o preço ou está com retorno vencido).</div>{% endif %}
      {% if v.fora %}<details class="vg-fora"><summary>Não chamou {{ v.fora|length }}</summary>
        {% for p in v.fora %}<div>{{ p.nome }} · {{ p.motivo }}</div>{% endfor %}</details>{% endif %}
    {% else %}
      {% for o in v.ofertas %}<div class="vg-p"><span class="nome">{{ o.nome }} <span class="vg-chip">{{ o.grupo_d }}</span></span>
        <span class="st">{{ o.rodada }}ª rodada · {{ o.estado_d }}</span><span class="porque">{{ o.porque }} · enviada {{ o.enviada }}</span></div>{% endfor %}
    {% endif %}
    <div class="vg-acoes">
      {% if v.estado == 'aguardando' and not v.aprovada_em and v.rodada1 %}
      <form method="post" action="/painel/clinica/vagas/{{ v.id }}/aprovar"><button>Aprovar e mandar</button></form>{% endif %}
      <a href="/painel/clinica/agenda/novo?data={{ v.inicio.strftime('%Y-%m-%d') }}">Marcar à mão</a>
      <form method="post" action="/painel/clinica/vagas/{{ v.id }}/parar"><button class="sec">Parar e deixar livre</button></form>
    </div>
  </div>
  {% else %}
  {% if cfg.modo != 'off' %}<div class="vg-vazio">Nenhum horário aberto por cancelamento agora. 🎉</div>{% endif %}
  {% endfor %}

  {% if antigas %}
  <div class="vg-rod" style="margin-top:1.4rem">Últimos 7 dias</div>
  {% for v in antigas %}
  <div class="vg-card {{ v.estado }}">
    <div class="cab"><span class="quando">{{ v.dia }} {{ v.hora }}</span><span>· {{ v.prof }}</span>
      <span class="vg-chip">{{ v.estado_d }}</span></div>
    {% for o in v.ofertas if o.estado == 'ganhou' %}<div class="vg-ctx">Ficou com {{ o.nome }} ({{ o.grupo_d|lower }}).</div>{% endfor %}
  </div>
  {% endfor %}
  {% endif %}

  {% if gerencia %}
  <form class="vg-modo" method="post" action="/painel/clinica/vagas/config">
    <h3>Como a vaga sai</h3>
    <div class="ex">O convite vai para 3 pessoas ao mesmo tempo, vale 20 minutos e fica com quem responder 1 primeiro; sem resposta, mais 5. Só no horário de atendimento da Régua, nunca com menos de 1 hora de aviso, no máximo 1 mensagem automática por paciente por dia (somando o voltar a chamar) e sempre com PARAR. A mensagem diz profissional, dia, hora e lugar — nunca o procedimento.</div>
    <div class="linha">
      <div><label for="vg-modo">Modo</label>
        <select id="vg-modo" name="modo">{% for k, rot in MODOS %}<option value="{{ k }}" {% if cfg.modo == k %}selected{% endif %}>{{ rot }}</option>{% endfor %}</select></div>
      <div><label for="vg-teto">Convites por dia (a clínica toda)</label>
        <input id="vg-teto" name="teto_dia" inputmode="numeric" value="{{ cfg.teto_dia }}"></div>
    </div>
    <div class="vg-acoes"><button>Salvar</button></div>
  </form>
  {% endif %}
</div>
<script>
document.querySelectorAll('.vg-acoes form').forEach(function(f){
  f.addEventListener('submit', function(){ var b = f.querySelector('button'); if(b){ b.disabled = true; } });
});
</script>
{% endblock %}"""

# ".html" liga o autoescape (select_autoescape): nome que vem do WhatsApp nunca vira HTML
_env.loader.mapping["clinica_vagas.html"] = _TPL
