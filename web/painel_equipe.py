"""Aba "Equipe" do painel — login por usuário + permissões (só o dono gere).

O dono convida membros por LINK: escolhe nome, e-mail e papel
(gestor/vendedor/financeiro). O membro abre o link, cria a senha e passa a logar
no painel com as permissões do papel. Reusa contas.equipe (motor) + _render/_env
do portal (gate/nav/base). Escopo por conta[0].
"""
from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from db.conexao import get_pool
from contas import equipe as eq
from web.portal import _render, _env, conta_logada

router = APIRouter()


def _dono(request: Request):
    """Só o titular (dono) gere a equipe. Devolve (conta, None) ou (None, redirect)."""
    conta = conta_logada(request)
    if conta is None:
        return None, RedirectResponse("/login", status_code=303)
    if request.session.get("papel", "dono") != "dono":
        return None, RedirectResponse("/painel", status_code=303)
    return conta, None


def _link(token: str) -> str:
    """Link público do convite. Usa APP_URL (mesma fonte dos outros e-mails) em vez de
    request.base_url — atrás do proxy do Render o base_url sai errado (host/esquema
    internos), gerando link que não abre. O APP_URL é a URL pública de verdade."""
    from finance.email_sender import _app_url
    return f"{_app_url()}/equipe/convite/{token}"


def _enviar_email_convite(conta, nome: str, email: str, papel: str, link: str) -> bool:
    """Dispara o e-mail de convite pro convidado (com o link). Tolerante: se o SMTP
    não estiver configurado ou falhar, devolve False e o link segue na tela como plano B."""
    try:
        from finance.email_sender import enviar_convite_equipe
        return bool(enviar_convite_equipe(email, nome or None, conta[2] or "sua empresa",
                                          eq.rotulo(papel), link))
    except Exception:  # noqa: BLE001
        return False


@router.get("/painel/equipe", response_class=HTMLResponse)
def painel_equipe(request: Request):
    conta, redir = _dono(request)
    if redir is not None:
        return redir
    pool = get_pool()
    eq.garantir_tabela(pool)
    return _render("equipe", request, tem_pj=True,
                   membros=eq.listar_equipe(pool, conta[0]),
                   papeis=[(p, eq.rotulo(p)) for p in eq.PAPEIS_PJ],
                   novo_link=request.session.pop("equipe_link", None),
                   aviso=request.session.pop("equipe_aviso", None),
                   erro=request.session.pop("equipe_erro", None))


@router.post("/painel/equipe/convidar")
def painel_equipe_convidar(request: Request, nome: str = Form(""),
                           email: str = Form(...), papel: str = Form("vendedor")):
    conta, redir = _dono(request)
    if redir is not None:
        return redir
    r = eq.convidar(get_pool(), conta[0], nome, email, papel)
    if r.get("ok") and r.get("ja_tem_login"):
        request.session["equipe_aviso"] = ("Essa pessoa já tem login no Zaq — foi adicionada "
                                            "à equipe. Ela acessa esta empresa com a senha que "
                                            "já usa, pelo menu “Trocar empresa”.")
    elif r.get("ok"):
        link = _link(r["token"])
        request.session["equipe_link"] = link
        enviado = _enviar_email_convite(conta, nome, email, papel, link)
        request.session["equipe_aviso"] = (
            f"Convite enviado por e-mail para {email} ✓ — o link também está aqui embaixo, "
            "caso queira mandar por WhatsApp."
            if enviado else
            "Gerei o link abaixo (copie e mande pra pessoa). Não consegui enviar o e-mail "
            "automático agora — confira o SMTP nas configurações.")
    else:
        request.session["equipe_erro"] = r.get("erro", "Não consegui convidar.")
    return RedirectResponse("/painel/equipe", status_code=303)


@router.post("/painel/equipe/papel")
def painel_equipe_papel(request: Request, membro_id: int = Form(...), papel: str = Form(...)):
    conta, redir = _dono(request)
    if redir is not None:
        return redir
    eq.atualizar_papel(get_pool(), conta[0], membro_id, papel)
    return RedirectResponse("/painel/equipe", status_code=303)


@router.post("/painel/equipe/ativo")
def painel_equipe_ativo(request: Request, membro_id: int = Form(...), ativo: str = Form("1")):
    conta, redir = _dono(request)
    if redir is not None:
        return redir
    eq.definir_ativo(get_pool(), conta[0], membro_id, ativo == "1")
    return RedirectResponse("/painel/equipe", status_code=303)


@router.post("/painel/equipe/reconvite")
def painel_equipe_reconvite(request: Request, membro_id: int = Form(...)):
    conta, redir = _dono(request)
    if redir is not None:
        return redir
    r = eq.regerar_convite(get_pool(), conta[0], membro_id)
    if r.get("ok"):
        link = _link(r["token"])
        request.session["equipe_link"] = link
        enviado = _enviar_email_convite(conta, r.get("nome") or "", r.get("email") or "",
                                        r.get("papel") or "vendedor", link)
        request.session["equipe_aviso"] = (
            f"Novo link gerado e reenviado por e-mail para {r.get('email')} ✓."
            if enviado else
            "Novo link gerado abaixo — copie e mande pra pessoa (não consegui reenviar por e-mail agora).")
    return RedirectResponse("/painel/equipe", status_code=303)


# ---------------------------------------------------------------- troca de contexto
@router.get("/trocar", response_class=HTMLResponse)
def trocar_form(request: Request):
    ctxs = request.session.get("contextos") or []
    if not ctxs:
        return RedirectResponse("/login", status_code=303)
    return _render("trocar", request, contextos=ctxs, ativa=request.session.get("conta_id"),
                   rotulo=eq.rotulo)


@router.post("/trocar")
def trocar_aplica(request: Request, i: int = Form(...)):
    ctxs = request.session.get("contextos") or []
    if 0 <= i < len(ctxs):
        eq.aplicar_contexto(request.session, ctxs[i])
        return RedirectResponse("/painel", status_code=303)
    return RedirectResponse("/trocar", status_code=303)


# ---------------------------------------------------------------- convite (público)
@router.get("/equipe/convite/{token}", response_class=HTMLResponse)
def convite_form(request: Request, token: str):
    info = eq.info_convite(get_pool(), token)
    if not info:
        return _render("convite", request, info=None, token=token, erro=None)
    return _render("convite", request, info=info, token=token, erro=None)


@router.post("/equipe/convite/{token}", response_class=HTMLResponse)
def convite_envia(request: Request, token: str, senha: str = Form(...),
                  confirma: str = Form("")):
    pool = get_pool()
    info = eq.info_convite(pool, token)
    if not info:
        return _render("convite", request, info=None, token=token, erro=None)
    if senha != confirma:
        return _render("convite", request, info=info, token=token,
                       erro="As senhas não conferem.")
    r = eq.aceitar_convite(pool, token, senha)
    if not r.get("ok"):
        return _render("convite", request, info=info, token=token, erro=r.get("erro"))
    # loga o membro direto
    request.session["conta_id"] = r["conta_id"]
    request.session["membro_id"] = r["membro_id"]
    request.session["papel"] = r["papel"]
    return RedirectResponse("/painel", status_code=303)


# ---------------------------------------------------------------- templates
_EQUIPE_TPL = """{% extends "base" %}{% block conteudo %}
<div class="card larga">
  <h2 style="margin:0">Equipe</h2>
  <div class="mut" style="font-size:.82rem">Convide sua equipe por link. Cada pessoa entra com o próprio login, com o acesso do papel.</div>

  {% if novo_link %}
  <div style="margin-top:1rem;padding:.8rem;border:1px solid var(--verde);border-radius:10px;background:#10241d">
    <div class="mut" style="font-size:.8rem">Link de convite gerado — mande pra pessoa (vale 7 dias):</div>
    <div style="display:flex;gap:.5rem;margin-top:.4rem">
      <input id="lk" value="{{ novo_link }}" readonly onclick="this.select()"
             style="flex:1;background:var(--bg);border:1px solid var(--borda);border-radius:8px;color:var(--txt);padding:.5rem .6rem;font-size:.82rem">
      <button type="button" onclick="navigator.clipboard.writeText(document.getElementById('lk').value);this.textContent='Copiado!'"
              style="background:var(--verde);color:#04140d;border:0;border-radius:8px;padding:.5rem .9rem;font-weight:600;cursor:pointer">Copiar</button>
    </div>
  </div>
  {% endif %}
  {% if aviso %}<div style="margin-top:.8rem;padding:.7rem .8rem;border:1px solid var(--verde);border-radius:10px;background:#10241d;font-size:.85rem;color:var(--verde-claro)">{{ aviso }}</div>{% endif %}
  {% if erro %}<div class="mut" style="margin-top:.8rem;color:#e07a5f">{{ erro }}</div>{% endif %}

  <form method="post" action="/painel/equipe/convidar" style="margin-top:1rem;display:grid;grid-template-columns:1.4fr 1.6fr 1fr auto;gap:.5rem;align-items:end">
    <div><label class="mut" style="font-size:.72rem">Nome</label><input name="nome" placeholder="Nome" style="width:100%"></div>
    <div><label class="mut" style="font-size:.72rem">E-mail</label><input name="email" type="email" required placeholder="pessoa@empresa.com" style="width:100%"></div>
    <div><label class="mut" style="font-size:.72rem">Papel</label>
      <select name="papel" style="width:100%">{% for v,l in papeis %}<option value="{{ v }}">{{ l }}</option>{% endfor %}</select></div>
    <button style="white-space:nowrap;margin:0">Convidar</button>
  </form>

  <div style="margin-top:1.2rem">
    {% if not membros %}<p class="mut">Ninguém na equipe ainda. Convide a primeira pessoa acima.</p>{% endif %}
    {% for m in membros %}
    <div style="display:flex;align-items:center;gap:.6rem;padding:.7rem 0;border-top:1px solid var(--borda);flex-wrap:wrap">
      <div class="avatar">{{ (m.nome or m.email or '?')[:1]|upper }}</div>
      <div style="flex:1;min-width:160px">
        <b>{{ m.nome }}</b>
        {% if m.pendente %}<span class="tag" style="background:#2a2212;color:#e0b25a">convite pendente</span>
        {% elif not m.ativo %}<span class="tag" style="background:#3a1a1a;color:#e07a5f">desativado</span>
        {% else %}<span class="tag">ativo</span>{% endif %}
        <div class="mut" style="font-size:.78rem">{{ m.email }} · {{ m.rotulo }}</div>
      </div>
      <form method="post" action="/painel/equipe/papel" style="display:flex;gap:.3rem;align-items:center;margin:0">
        <input type="hidden" name="membro_id" value="{{ m.id }}">
        <select name="papel" onchange="this.form.submit()" style="font-size:.82rem;padding:.3rem">
          {% for v,l in papeis %}<option value="{{ v }}" {% if v==m.papel %}selected{% endif %}>{{ l }}</option>{% endfor %}
        </select>
      </form>
      <form method="post" action="/painel/equipe/reconvite" style="margin:0"><input type="hidden" name="membro_id" value="{{ m.id }}">
        <button class="btn-conv" style="margin:0">↻ Novo link</button></form>
      <form method="post" action="/painel/equipe/ativo" style="margin:0">
        <input type="hidden" name="membro_id" value="{{ m.id }}">
        <input type="hidden" name="ativo" value="{{ '0' if m.ativo else '1' }}">
        <button class="{{ 'btn-off' if m.ativo else 'btn-on' }}" style="margin:0">{{ 'desativar' if m.ativo else 'reativar' }}</button></form>
    </div>
    {% endfor %}
  </div>
</div>
{% endblock %}"""

_CONVITE_TPL = """{% extends "base" %}{% block conteudo %}
<div class="card">
  {% if not info %}
    <h1>Convite inválido</h1>
    <p class="mut">Esse link de convite não existe, já foi usado ou expirou. Peça um novo ao responsável pela conta.</p>
    <p class="mut" style="margin-top:1rem"><a href="/login" style="color:var(--verde-claro)">Ir para o login</a></p>
  {% else %}
    <h1>Bem-vindo(a){% if info.nome %}, {{ info.nome }}{% endif %}!</h1>
    <p class="mut">Você foi convidado(a) para <b>{{ info.empresa }}</b> como <b>{{ info.rotulo }}</b>. Crie sua senha pra entrar.</p>
    {% if erro %}<div class="mut" style="color:#e07a5f;margin:.6rem 0">{{ erro }}</div>{% endif %}
    <form method="post" action="/equipe/convite/{{ token }}" style="margin-top:.8rem">
      <div class="mut" style="font-size:.8rem">E-mail: <b>{{ info.email }}</b></div>
      <label style="margin-top:.6rem">Senha (mínimo 8)</label>
      <input name="senha" type="password" required minlength="8" maxlength="72">
      <label>Confirmar senha</label>
      <input name="confirma" type="password" required minlength="8" maxlength="72">
      <button>Criar senha e entrar</button>
    </form>
  {% endif %}
</div>
{% endblock %}"""

_TROCAR_TPL = """{% extends "base" %}{% block conteudo %}
<div class="card larga">
  <h1 style="margin-top:0">Onde você quer trabalhar?</h1>
  <p class="mut">Você tem acesso a mais de um espaço. Escolha por qual entrar — dá pra trocar depois pelo menu.</p>
  {% for c in contextos %}
  <form method="post" action="/trocar" style="margin:.5rem 0">
    <input type="hidden" name="i" value="{{ loop.index0 }}">
    <button style="width:100%;text-align:left;display:flex;justify-content:space-between;align-items:center;margin-top:0;{% if c.conta_id==ativa %}border:1px solid var(--verde){% endif %}">
      <span><b>{{ c.nome }}</b><br><span style="font-size:.8rem;opacity:.85">{{ rotulo(c.papel) }}{% if c.tipo=='conta' %} · sua conta{% endif %}</span></span>
      <span>{% if c.conta_id==ativa %}atual{% else %}entrar ›{% endif %}</span>
    </button>
  </form>
  {% endfor %}
</div>
{% endblock %}"""

_env.loader.mapping["equipe"] = _EQUIPE_TPL
_env.loader.mapping["convite"] = _CONVITE_TPL
_env.loader.mapping["trocar"] = _TROCAR_TPL
