"""A página que o PACIENTE abre pelo link "complete sua ficha" (sem login).

/ficha/{token}            abre com a data de nascimento; depois, os passos
/ficha/{token}/entrar     confere a data (5 erros travam por 30 minutos)
/ficha/{token}/cadastro   passo 1: nome, nascimento, CPF, cidade, endereço (e o responsável do menor)
/ficha/{token}/preconsulta passo 2: o que o profissional precisa saber (só ele lê)
/ficha/{token}/termos     passo 3: uso de dados (LGPD) e de imagem

O token É a chave (finance/clinica_ficha_link.por_token). A data de nascimento é a
segunda: sem ela o link aberto por outra pessoa não mostra nada do paciente. Quem ainda
não tem a data na ficha (a recepção marcou só com o nome) entra direto no passo 1 e dá a
data ali. Esse celular fica no nível "novo": segue pra pré-consulta e termos, mas NUNCA
vê o que a recepção guardou (CPF, endereço), e o cadastro dele só preenche o que está
vazio (clinica_ficha_link.salvar_cadastro). Só quem provou a data (nível "data") vê a
ficha preenchida.

Erro e aviso moram na sessão, nunca na URL. Com o link desligado pela clínica, a
página não abre.

Desenho: docs/mockups/clinica_prontuario.html, seção 12 (o celular do paciente).
"""
from __future__ import annotations

import logging
import re
import time as _time
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db.conexao import get_pool
from finance import clinica_agenda as ca
from finance import clinica_ficha_link as cfl
from finance import clinica_preconsulta as cpc
from web.portal import _env

_log = logging.getLogger("clinica.ficha_publica")

router = APIRouter()
_VALIDADE_S = 2 * 60 * 60          # a data vale 2 horas neste celular
_PASSOS = ((1, "Cadastro"), (2, "Pré-consulta"), (3, "Termos"), (4, "Pronto"))


def _ip(request: Request) -> str:
    """O IP do aceite dos termos: o último do X-Forwarded-For, o que o proxy do Render
    acrescentou (o primeiro quem escreve é o próprio celular)."""
    xf = request.headers.get("x-forwarded-for", "")
    if xf:
        return xf.split(",")[-1].strip()[:60]
    return (request.client.host if request.client else "")[:60]


def _nivel(request: Request, tok: str) -> str | None:
    """'data' (provou a data de nascimento), 'novo' (a ficha não tinha data e ele deu
    agora) ou None."""
    ok = request.session.get("fichas_ok") or {}
    v = ok.get(tok) if isinstance(ok, dict) else None
    if not isinstance(v, list) or len(v) != 2 or _time.time() - float(v[0]) >= _VALIDADE_S:
        return None
    return v[1] if v[1] in ("data", "novo") else None


def _liberar(request: Request, tok: str, nivel: str) -> None:
    ok = request.session.get("fichas_ok") or {}
    ok = {k: v for k, v in (ok.items() if isinstance(ok, dict) else [])
          if isinstance(v, list) and len(v) == 2 and _time.time() - float(v[0]) < _VALIDADE_S}
    ok[tok] = [_time.time(), nivel]
    request.session["fichas_ok"] = dict(sorted(ok.items(), key=lambda kv: kv[1][0])[-5:])


def _aberta(c, tok: str) -> dict | None:
    """A ficha do token, se o link da clínica estiver ligado."""
    f = cfl.por_token(c, tok)
    return f if f and cfl.ligado(c, f["conta_id"]) else None


def _empresa(c, conta_id: int) -> str:
    r = c.execute("select coalesce(nome,'') from contas where id=%s", (conta_id,)).fetchone()
    return r[0] if r else ""


def _nao_achou() -> HTMLResponse:
    return HTMLResponse(_env.get_template("ficha_404.html").render(), status_code=404)


def _ir(request: Request, tok: str, passo: int | None = None, erro: str = "", aviso: str = "") -> RedirectResponse:
    if erro:
        request.session["ficha_erro"] = erro[:200]
    if aviso:
        request.session["ficha_aviso"] = aviso[:200]
    return RedirectResponse(f"/ficha/{tok}" + (f"?passo={passo}" if passo else ""), status_code=303)


@router.get("/ficha/{tok}", response_class=HTMLResponse)
def ficha(request: Request, tok: str):
    agora = datetime.now(timezone.utc)
    q = request.query_params
    erro = request.session.pop("ficha_erro", "")
    aviso = request.session.pop("ficha_aviso", "")
    with get_pool().connection() as c:
        f = _aberta(c, tok)
        if not f:
            return _nao_achou()
        empresa = _empresa(c, f["conta_id"])
        nivel = _nivel(request, tok)
        liberado = bool(nivel)
        if not liberado and f["nascimento"]:
            travada = bool(f["travada_ate"] and f["travada_ate"] > agora)
            return HTMLResponse(_env.get_template("ficha_entrar.html").render(
                empresa=empresa, tok=tok, primeiro=cfl.cpa._primeiro(f["nome"]).capitalize(),
                travada=travada, erro=erro))
        s = cfl.situacao(c, f["conta_id"], f["id"], agora) or {}
        # só quem provou a data vê o que já está guardado
        dados = cfl.cpa.ficha(c, f["conta_id"], f["id"], agora) if nivel == "data" else None
        eid = cfl.proximo_evento(c, f["conta_id"], f["id"], agora)
        ev = ca.evento(c, f["conta_id"], eid) if eid else None
        prof = ca._prof_nome(c, f["conta_id"], ev) if ev else ""
        c.commit()
    passo = int(q["passo"]) if re.fullmatch(r"[1-4]", q.get("passo") or "") else None
    if not liberado:
        passo = 1                     # sem a data na ficha: primeiro o cadastro, com ela
    elif passo is None:
        passo = 1 if not s.get("cadastro_ok") else 2 if not s.get("pre_ok") else 3 if not s.get("termos_ok") else 4
    curta = bool(s.get("retorno"))
    return HTMLResponse(_env.get_template("ficha_passos.html").render(
        empresa=empresa, tok=tok, passo=passo, PASSOS=_PASSOS, erro=erro, aviso=aviso, s=s, d=dados,
        nome=f["nome"], liberado=liberado, menor=bool(s.get("menor")), curta=curta,
        perguntas=cpc.perguntas(curta), GRAVIDEZ=cpc.GRAVIDEZ, COMO=cfl.COMO_CONHECEU, IMAGEM=cfl.IMAGEM,
        termos=cfl.textos_dos_termos(empresa, f["nome"], bool(s.get("menor"))),
        ev=ev, quando=(f"{ca.dia_txt(ev['inicio'])} às {ev['hora']}" if ev else ""),
        prof=prof))


@router.post("/ficha/{tok}/entrar")
async def entrar(request: Request, tok: str):
    form = dict(await request.form())
    agora = datetime.now(timezone.utc)
    with get_pool().connection() as c:
        f = _aberta(c, tok)
        if not f:
            return _nao_achou()
        r = cfl.entrar(c, f, str(form.get("nascimento") or ""), agora)
        c.commit()
    if r == "ok":
        _liberar(request, tok, "data")
        return _ir(request, tok)
    if r == "travada":
        return _ir(request, tok, erro="Muitas tentativas. Tente de novo mais tarde ou fale com a clínica pelo WhatsApp.")
    return _ir(request, tok, erro="A data não confere. Confira dia, mês e ano.")


@router.post("/ficha/{tok}/cadastro")
async def cadastro(request: Request, tok: str):
    form = dict(await request.form())
    agora = datetime.now(timezone.utc)
    with get_pool().connection() as c:
        f = _aberta(c, tok)
    if not f:
        return _nao_achou()
    nivel = _nivel(request, tok)
    if f["nascimento"] and not nivel:
        return _ir(request, tok)
    from starlette.concurrency import run_in_threadpool
    erro, aviso = await run_in_threadpool(cfl.salvar_cadastro, get_pool(), f, form, agora, nivel == "data")
    if erro:
        return _ir(request, tok, 1, erro)
    # quem acabou de dar a data segue, mas não vê o que a recepção guardou
    _liberar(request, tok, nivel or "novo")
    return _ir(request, tok, 2, aviso=aviso or "")


@router.post("/ficha/{tok}/preconsulta")
async def preconsulta(request: Request, tok: str):
    form = dict(await request.form())
    agora = datetime.now(timezone.utc)
    if not _nivel(request, tok):
        return _ir(request, tok)
    with get_pool().connection() as c:
        f = _aberta(c, tok)
        if not f:
            return _nao_achou()
        s = cfl.situacao(c, f["conta_id"], f["id"], agora) or {}
        curta = bool(s.get("retorno"))
        resp, erro = cpc.validar(form, curta)
        if erro:
            return _ir(request, tok, 2, erro)
        cpc.salvar(c, f["conta_id"], f["id"], resp, curta=curta,
                   evento_id=cfl.proximo_evento(c, f["conta_id"], f["id"], agora),
                   papel="responsavel" if s.get("menor") else "paciente")
        c.commit()
    return _ir(request, tok, 3)


@router.post("/ficha/{tok}/termos")
async def termos(request: Request, tok: str):
    form = dict(await request.form())
    agora = datetime.now(timezone.utc)
    if not _nivel(request, tok):
        return _ir(request, tok)
    with get_pool().connection() as c:
        f = _aberta(c, tok)
        if not f:
            return _nao_achou()
        erro = cfl.salvar_termos(c, f, form, empresa=_empresa(c, f["conta_id"]), ip=_ip(request),
                                 user_agent=request.headers.get("user-agent", ""), agora=agora)
        if erro:
            c.rollback()
            return _ir(request, tok, 3, erro)
        c.commit()
    return _ir(request, tok, 4)


_CSS = r"""<style>
body{margin:0;background:#f4f2ee;font:16px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:#1d2433}
.pg{max-width:560px;margin:0 auto;padding:1.2rem 1rem 3rem}h1{font-size:1.25rem;margin:.2rem 0}.mut{color:#667085;font-size:.88rem}
.cx{background:#fff;border:1px solid #e4e0d8;border-radius:12px;padding:1rem 1.1rem;margin:.9rem 0}
.ok{background:#e8f6ee;border:1px solid #a6d9b8;border-radius:10px;padding:.7rem .9rem;margin:.8rem 0}
.erro{background:#fdecec;border:1px solid #f1b5b5;border-radius:10px;padding:.7rem .9rem;margin:.8rem 0}
label{display:block;margin:.7rem 0 .2rem;font-size:.92rem;font-weight:600}
input[type=text],input[type=date],input[type=email],select,textarea{width:100%;padding:.65rem;border:1px solid #d0d5dd;border-radius:8px;font-size:1rem;box-sizing:border-box;background:#fff}
textarea{min-height:5rem}
.op{display:flex;gap:.5rem;align-items:flex-start;margin:.35rem 0;font-weight:400}.op input{margin-top:.3rem}
.duas{display:grid;grid-template-columns:1fr 1fr;gap:.6rem}
button{background:#1d6f42;color:#fff;border:0;border-radius:9px;padding:.8rem 1.1rem;font-size:1rem;margin-top:1rem;width:100%}
.passos{display:flex;gap:.3rem;margin:.6rem 0}.passos span{flex:1;height:5px;border-radius:3px;background:#e4e0d8}.passos span.on{background:#1d6f42}
.termo{white-space:pre-wrap;font-size:.9rem;background:#faf9f6;border:1px solid #eee;border-radius:8px;padding:.6rem .7rem;max-height:14rem;overflow:auto}
a{color:#1d6f42}
</style>"""

_TPL_404 = r"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Ficha</title>""" + _CSS + r"""</head>
<body><div class="pg"><div class="cx">Este link não vale mais. Fale com a clínica pelo WhatsApp que mandamos outro.</div></div></body></html>"""

_TPL_ENTRAR = r"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>Ficha · {{ empresa }}</title>""" + _CSS + r"""</head><body><div class="pg">
<div class="mut">{{ empresa }}</div><h1>Sua ficha</h1>
{% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
{% if travada %}<div class="cx">Muitas tentativas. Tente de novo em 30 minutos ou fale com a clínica pelo WhatsApp.</div>
{% else %}
<form class="cx" method="post" action="/ficha/{{ tok }}/entrar">
  <div>Para abrir a ficha{% if primeiro %} de <b>{{ primeiro }}</b>{% endif %}, confirme a data de nascimento.</div>
  <label for="n">Data de nascimento</label><input type="date" id="n" name="nascimento" required>
  <button>Abrir a ficha</button>
</form>{% endif %}
<p class="mut">A data protege os seus dados: sem ela, quem recebeu este link por engano não vê nada.</p>
</div></body></html>"""

_TPL_PASSOS = r"""<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><meta name="robots" content="noindex">
<title>Ficha · {{ empresa }}</title>""" + _CSS + r"""</head><body><div class="pg">
<div class="mut">{{ empresa }} · Sua ficha</div>
<div class="passos">{% for n, _r in PASSOS %}<span class="{% if n <= passo %}on{% endif %}"></span>{% endfor %}</div>
<div class="mut">Passo {{ passo }} de 4 · {{ dict(PASSOS)[passo] }}</div>
{% if erro %}<div class="erro">{{ erro }}</div>{% endif %}
{% if aviso %}<div class="ok">{{ aviso }}</div>{% endif %}

{% if passo == 1 %}
<form class="cx" method="post" action="/ficha/{{ tok }}/cadastro">
  <label for="nome">Nome completo{% if menor %} do paciente{% endif %}</label>
  <input type="text" id="nome" name="nome" required autocomplete="name" value="{{ (d.nome if d else nome) or '' }}">
  <label for="nasc">Data de nascimento</label>
  <input type="date" id="nasc" name="nascimento" required value="{{ d.nascimento.isoformat() if d and d.nascimento else '' }}">
  <label for="cpf">CPF{% if menor %} do paciente (se tiver){% endif %}</label>
  <input type="text" id="cpf" name="cpf" inputmode="numeric" placeholder="000.000.000-00" value="{{ d.cpf if d else '' }}">
  {% if not menor %}<div class="mut">Vai na nota fiscal.</div>{% endif %}
  {% if menor or not d or d.idade is none %}
  <div class="cx" style="margin:.8rem 0 0">
    <b>Responsável</b>
    <div class="mut">{% if menor %}Menor de idade{% else %}Se o paciente tiver menos de 18 anos{% endif %}: o responsável recebe as mensagens, aceita os termos e recebe a nota.</div>
    {% if d and d.responsavel %}<div style="margin-top:.4rem">{{ d.responsavel.nome }}</div>
    {% else %}<label for="rn">Nome completo do responsável</label><input type="text" id="rn" name="resp_nome">{% endif %}
    {% if not (d and d.responsavel and d.responsavel.tem_cpf) %}<label for="rc">CPF do responsável</label><input type="text" id="rc" name="resp_cpf" inputmode="numeric" placeholder="000.000.000-00">{% endif %}
  </div>
  {% endif %}
  <div class="duas"><div><label for="cid">Cidade</label><input type="text" id="cid" name="cidade" required value="{{ d.cidade if d else '' }}"></div>
    <div><label for="uf">UF</label><input type="text" id="uf" name="uf" maxlength="2" value="{{ d.uf if d else '' }}"></div></div>
  <label for="end">Endereço (opcional)</label><input type="text" id="end" name="endereco" value="{{ d.endereco if d else '' }}">
  <label for="cep">CEP (opcional)</label><input type="text" id="cep" name="cep" inputmode="numeric" value="{{ d.cep if d else '' }}">
  <label for="em">E-mail (opcional)</label><input type="email" id="em" name="email" value="{{ d.email if d else '' }}">
  {% if not (d and d.como_conheceu) %}<label for="cc">Como conheceu a clínica?</label>
  <select id="cc" name="como_conheceu"><option value="">—</option>{% for o in COMO %}<option>{{ o }}</option>{% endfor %}</select>{% endif %}
  <button>Salvar e continuar</button>
</form>

{% elif passo == 2 %}
<form class="cx" method="post" action="/ficha/{{ tok }}/preconsulta">
  {% if curta %}<div class="mut">Você já é paciente: só o que mudou desde a última consulta.</div>{% endif %}
  {% for chave, txt, tipo in perguntas %}
  <label>{{ txt }}</label>
  {% if tipo == 'texto' %}<textarea name="{{ chave }}" {% if chave == 'queixa' and not curta %}required{% endif %}></textarea>
  {% elif tipo == 'sim_qual' %}
    <div class="op"><input type="radio" name="{{ chave }}" value="nao" id="{{ chave }}n" required><label for="{{ chave }}n" style="margin:0;font-weight:400">Não</label></div>
    <div class="op"><input type="radio" name="{{ chave }}" value="sim" id="{{ chave }}s"><label for="{{ chave }}s" style="margin:0;font-weight:400">Sim. Qual?</label></div>
    <input type="text" name="{{ chave }}_qual" placeholder="Se sim, conte qual">
  {% elif tipo == 'gravidez' %}
    {% for v, r in GRAVIDEZ %}<div class="op"><input type="radio" name="{{ chave }}" value="{{ v }}" id="{{ chave }}{{ v }}" required><label for="{{ chave }}{{ v }}" style="margin:0;font-weight:400">{{ r }}</label></div>{% endfor %}
  {% endif %}
  {% endfor %}
  <p class="mut">Suas respostas vão só para o profissional que vai te atender.</p>
  <button>Salvar e continuar</button>
</form>
{% if s.pre_ok %}<p><a href="/ficha/{{ tok }}?passo=3">Já respondi: ir para os termos</a></p>{% endif %}

{% elif passo == 3 %}
<form class="cx" method="post" action="/ficha/{{ tok }}/termos">
  <b>{{ termos.lgpd[0] }}</b><div class="termo">{{ termos.lgpd[1] }}</div>
  <div class="op"><input type="checkbox" name="lgpd" value="1" id="lg" required><label for="lg" style="margin:0;font-weight:400">Li e aceito</label></div>
  <b style="display:block;margin-top:1rem">{{ termos.imagem[0] }}</b><div class="termo">{{ termos.imagem[1] }}</div>
  {% for v, r in IMAGEM %}<div class="op"><input type="radio" name="imagem" value="{{ v }}" id="im{{ v }}" required><label for="im{{ v }}" style="margin:0;font-weight:400">{{ r }}</label></div>{% endfor %}
  <label for="qn">{% if menor %}Nome completo do responsável{% else %}Seu nome completo{% endif %}</label>
  <input type="text" id="qn" name="nome" required value="{{ (d.responsavel.nome if menor and d and d.responsavel else (d.nome if d and not menor else '')) }}">
  <button>Aceitar</button>
</form>
{% if s.termos_ok %}<p><a href="/ficha/{{ tok }}?passo=4">Já aceitei</a></p>{% endif %}

{% else %}
<div class="cx ok"><b>Ficha pronta. Obrigado!</b>
  {% if ev %}<div style="margin-top:.3rem">Sua consulta: {{ quando }}{% if prof %} com {{ prof }}{% endif %}.</div>{% endif %}
  {% if s.falta %}<div class="mut" style="margin-top:.3rem">Ainda falta: {{ s.falta|join(', ') }}. <a href="/ficha/{{ tok }}?passo=1">Completar</a></div>{% endif %}
</div>
<p class="mut">Precisa mudar algo? Abra o mesmo link ou fale com a clínica pelo WhatsApp.</p>
{% endif %}
</div></body></html>"""

_env.loader.mapping["ficha_404.html"] = _TPL_404
_env.loader.mapping["ficha_entrar.html"] = _TPL_ENTRAR
_env.loader.mapping["ficha_passos.html"] = _TPL_PASSOS
