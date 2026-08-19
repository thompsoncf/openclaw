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
    """Dispara o e-mail de convite pro convidado (com o link).

    Sai pelo REMETENTE DO ZAQ, com a caixa da empresa como reserva — mesma ordem, e
    pelo mesmo motivo, do link de acesso do Cockpit (ver web/painel_cockpit.py). O
    convite é a OUTRA porta de entrada da mesma pessoa: deixar um caminho pela caixa
    do cliente e outro pelo Zaq faria o acesso funcionar ou não conforme a porta.

    Convite é e-mail de SISTEMA — ninguém responde, só precisa chegar. Mensagem pra
    lead é que sai pela caixa da empresa, pra resposta cair no inbox dela.

    Tolerante: se nada enviar, devolve False e o link segue na tela como plano B."""
    try:
        from finance import email_sender as es
        from finance import email_inbound as ein
        empresa = conta[2] or "sua empresa"
        assunto, html, texto = es.conteudo_convite_equipe(nome or None, empresa,
                                                          eq.rotulo(papel), link)
        # 1) remetente do Zaq, com o nome da empresa visível pra quem recebe
        if es.enviar_email(email, assunto, html, texto, from_nome=empresa):
            return True
        # 2) reserva: a caixa da empresa, quando o Zaq não pôde mandar
        return bool(ein.enviar_conta(get_pool(), conta[0], email, assunto, html, texto,
                                     from_nome=empresa))
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
                   novo_link_cap=request.session.pop("equipe_link_cap", None),
                   aviso=request.session.pop("equipe_aviso", None),
                   erro=request.session.pop("equipe_erro", None))


@router.post("/painel/equipe/convidar")
def painel_equipe_convidar(request: Request, nome: str = Form(""),
                           email: str = Form(...), papel: str = Form("vendedor"),
                           whatsapp: str = Form("")):
    conta, redir = _dono(request)
    if redir is not None:
        return redir
    r = eq.convidar(get_pool(), conta[0], nome, email, papel, whatsapp)
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


@router.post("/painel/equipe/renomear")
def painel_equipe_renomear(request: Request, membro_id: int = Form(...), nome: str = Form(""),
                           whatsapp: str = Form("")):
    conta, redir = _dono(request)
    if redir is not None:
        return redir
    r = eq.renomear_membro(get_pool(), conta[0], membro_id, nome, whatsapp)
    if r.get("ok"):
        request.session["equipe_aviso"] = "Nome atualizado ✓"
    else:
        request.session["equipe_erro"] = r.get("erro") or "Não consegui renomear (o dono não muda por aqui)."
    return RedirectResponse("/painel/equipe", status_code=303)


@router.post("/painel/equipe/comissao")
def painel_equipe_comissao(request: Request, membro_id: int = Form(...),
                           comissao_pct: str = Form("")):
    conta, redir = _dono(request)
    if redir is not None:
        return redir
    txt = (comissao_pct or "").strip().replace(",", ".")
    try:
        pct = float(txt) if txt else None
    except ValueError:
        pct = -1  # força o erro "entre 0 e 100%" abaixo, em vez de 500
    r = eq.definir_comissao(get_pool(), conta[0], membro_id, pct)
    if r.get("ok"):
        request.session["equipe_aviso"] = "Comissão atualizada ✓"
    else:
        request.session["equipe_erro"] = r.get("erro") or "Não consegui salvar a comissão."
    return RedirectResponse("/painel/equipe", status_code=303)


@router.post("/painel/equipe/excluir")
def painel_equipe_excluir(request: Request, membro_id: int = Form(...)):
    conta, redir = _dono(request)
    if redir is not None:
        return redir
    r = eq.remover_membro(get_pool(), conta[0], membro_id)
    if r.get("ok"):
        request.session["equipe_aviso"] = "Colaborador removido da equipe."
    elif r.get("erro") == "vinculado":
        request.session["equipe_erro"] = ("Esse colaborador tem leads ou registros vinculados — "
                                          "desative-o (botão “desativar”) ou reatribua os leads antes de excluir.")
    else:
        request.session["equipe_erro"] = "Não consegui excluir (o dono não pode ser removido)."
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
        # cada empresa pode ter um papel diferente: manda pra casa DAQUELE papel
        return RedirectResponse(
            eq.home_do_papel(ctxs[i]["papel"], ctxs[i].get("membro_id")), status_code=303)
    return RedirectResponse("/trocar", status_code=303)


# ---------------------------------------------------------------- convite (público)
@router.get("/equipe/convite/{token}", response_class=HTMLResponse)
def convite_form(request: Request, token: str):
    info = eq.info_convite(get_pool(), token)
    if not info:
        return _render("convite_equipe", request, info=None, token=token, erro=None)
    return _render("convite_equipe", request, info=info, token=token, erro=None)


@router.post("/equipe/convite/{token}", response_class=HTMLResponse)
def convite_envia(request: Request, token: str, senha: str = Form(...),
                  confirma: str = Form("")):
    pool = get_pool()
    info = eq.info_convite(pool, token)
    if not info:
        return _render("convite_equipe", request, info=None, token=token, erro=None)
    if senha != confirma:
        return _render("convite_equipe", request, info=info, token=token,
                       erro="As senhas não conferem.")
    r = eq.aceitar_convite(pool, token, senha)
    if not r.get("ok"):
        return _render("convite_equipe", request, info=info, token=token, erro=r.get("erro"))
    # loga o membro direto
    request.session["conta_id"] = r["conta_id"]
    request.session["membro_id"] = r["membro_id"]
    request.session["papel"] = r["papel"]
    return RedirectResponse("/painel", status_code=303)


# ---------------------------------------------------------------- templates
_EQUIPE_TPL = """{% extends "base" %}{% block conteudo %}
<style>
/* linha de cada membro — reseta o button{width:100%} global e alinha tudo */
.mrow{display:flex;align-items:center;gap:.85rem;padding:.9rem 0;border-top:1px solid var(--borda);flex-wrap:wrap}
.mrow .minfo{flex:1;min-width:210px;display:flex;flex-direction:column;gap:.4rem}
.mname{display:flex;gap:.4rem;align-items:center;flex-wrap:wrap}
.mname input{flex:1;min-width:120px;max-width:240px;background:var(--bg);border:1px solid var(--borda);
  border-radius:8px;color:var(--txt);padding:.42rem .55rem;font-weight:600;font-size:.9rem;margin:0}
.mname input:focus{outline:none;border-color:var(--verde)}
.mname input.wa{max-width:150px;font-weight:400;font-size:.84rem;color:var(--txt-mut)}
.mmeta{font-size:.76rem;color:var(--txt-mut);display:flex;align-items:center;gap:.4rem;flex-wrap:wrap}
.macts{display:flex;gap:.45rem;align-items:center;flex-wrap:wrap}
.macts select{background:var(--bg);border:1px solid var(--borda);border-radius:8px;color:var(--txt);
  padding:.44rem .5rem;font-size:.82rem}
/* todos os botões da linha: tamanho natural (o global força width:100%) */
.mrow button{width:auto;margin:0;padding:.44rem .7rem;font-size:.8rem;border-radius:8px;cursor:pointer;
  background:var(--card-2);border:1px solid var(--borda);color:var(--txt);white-space:nowrap;
  display:inline-flex;align-items:center;gap:.35rem}
.mrow button:hover{border-color:var(--verde)}
.mrow .ebtn{padding:.44rem .6rem}                 /* botão-ícone do nome (✓) */
.mrow .danger:hover{border-color:var(--coral);color:#f0917f;background:#2a1414}
.mtag{padding:.1rem .5rem;border-radius:999px;font-size:.72rem;border:1px solid var(--borda);color:var(--txt-mut)}
.mtag.on{color:var(--verde-claro);border-color:var(--neon-borda);background:var(--neon-fundo)}
.mtag.pend{color:#e0b25a;border-color:var(--ambar-borda);background:var(--ambar-fundo)}
.mtag.off{color:#e07a5f;border-color:var(--coral-borda);background:var(--coral-fundo)}
@media (max-width:640px){.macts{width:100%}}
/* legenda: o que cada papel acessa */
.papeis{margin-top:1rem;border:1px solid var(--borda);border-radius:12px;background:var(--card-2);padding:.85rem 1rem}
.papeis .ph-tt{font-size:.86rem;font-weight:600;margin-bottom:.6rem}
.ph-row{display:flex;gap:.7rem;align-items:baseline;padding:.4rem 0;border-top:1px solid var(--borda);flex-wrap:wrap}
.ph-row:first-of-type{border-top:0}
.ph-nome{flex-shrink:0;min-width:88px;font-size:.78rem;font-weight:700;padding:.16rem .55rem;border-radius:999px;
  border:1px solid var(--borda);text-align:center}
.ph-nome.vend{color:#7bb8e6;border-color:#1e3a52;background:#0f1d2b}
.ph-nome.gest{color:var(--verde-claro);border-color:var(--neon-borda);background:var(--neon-fundo)}
.ph-nome.fin{color:#e0b25a;border-color:var(--ambar-borda);background:var(--ambar-fundo)}
.ph-desc{flex:1;min-width:200px;font-size:.82rem;color:var(--txt-mut);line-height:1.5}
.ph-desc b{color:var(--txt);font-weight:600}
</style>
<div class="card larga">
  <div style="display:flex;justify-content:space-between;align-items:center;gap:.8rem;flex-wrap:wrap">
    <h2 style="margin:0">Equipe</h2>
    <a href="/cockpit" target="_blank" rel="noopener"
       style="display:inline-flex;align-items:center;gap:.4rem;background:var(--neon-fundo);border:1px solid var(--neon-borda);color:var(--verde-claro);border-radius:10px;padding:.5rem .9rem;font-weight:700;text-decoration:none;white-space:nowrap">📊 Abrir o app da equipe</a>
  </div>
  <div class="mut" style="font-size:.82rem">Convide sua equipe por link. Cada pessoa entra com o próprio login, com o acesso do papel. Você acompanha o time no <b>Cockpit</b> — sem precisar de outro cadastro.</div>

  {% if novo_link %}
  <div style="margin-top:1rem;padding:.8rem;border:1px solid var(--verde);border-radius:10px;background:#10241d">
    <div class="mut" style="font-size:.8rem">{{ novo_link_cap or "Link de convite gerado — mande pra pessoa (vale 7 dias):" }}</div>
    <div style="display:flex;gap:.5rem;margin-top:.4rem">
      <input id="lk" value="{{ novo_link }}" readonly onclick="this.select()"
             style="flex:1;min-width:0;background:var(--bg);border:1px solid var(--borda);border-radius:8px;color:var(--txt);padding:.5rem .6rem;font-size:.82rem">
      <button type="button" onclick="navigator.clipboard.writeText(document.getElementById('lk').value);this.textContent='Copiado!'"
              style="width:auto;flex:none;margin:0;background:var(--verde);color:var(--sobre-verde);border:0;border-radius:8px;padding:.5rem .9rem;font-weight:600;cursor:pointer;white-space:nowrap">Copiar</button>
    </div>
  </div>
  {% endif %}
  {% if aviso %}<div style="margin-top:.8rem;padding:.7rem .8rem;border:1px solid var(--verde);border-radius:10px;background:#10241d;font-size:.85rem;color:var(--verde-claro)">{{ aviso }}</div>{% endif %}
  {% if erro %}<div class="mut" style="margin-top:.8rem;color:#e07a5f">{{ erro }}</div>{% endif %}

  <form method="post" action="/painel/equipe/convidar" style="margin-top:1rem;display:grid;grid-template-columns:1.2fr 1.5fr 1.1fr .95fr auto;gap:.5rem;align-items:end">
    <div><label class="mut" style="font-size:.72rem">Nome</label><input name="nome" placeholder="Nome" style="width:100%"></div>
    <div><label class="mut" style="font-size:.72rem">E-mail</label><input name="email" type="email" required placeholder="pessoa@empresa.com" style="width:100%"></div>
    <div><label class="mut" style="font-size:.72rem">WhatsApp <span style="font-weight:400">(aviso)</span></label><input name="whatsapp" inputmode="tel" placeholder="55 86 9…" style="width:100%"></div>
    <div><label class="mut" style="font-size:.72rem">Papel</label>
      <select name="papel" style="width:100%">{% for v,l in papeis %}<option value="{{ v }}">{{ l }}</option>{% endfor %}</select></div>
    <button style="white-space:nowrap;margin:0">Convidar</button>
  </form>

  <div class="papeis">
    <div class="ph-tt">O que cada papel acessa <span class="mut" style="font-weight:400">— escolha sabendo o que está liberando</span></div>
    <div class="ph-row">
      <span class="ph-nome vend">Vendedor</span>
      <span class="ph-desc">Prospecção e vendas: trabalha <b>os leads dele</b> (funil e campanhas atribuídas). <b>Não</b> vê o financeiro nem gerencia a equipe.</span>
    </div>
    <div class="ph-row">
      <span class="ph-nome gest">Gestor</span>
      <span class="ph-desc">Vendas + financeiro: enxerga a <b>prospecção inteira</b> da empresa e o <b>financeiro</b>. <b>Não</b> convida/remove membros — isso fica só com você (dono).</span>
    </div>
    <div class="ph-row">
      <span class="ph-nome fin">Financeiro</span>
      <span class="ph-desc">Só o <b>financeiro</b> (contas, pagamentos, cobranças). <b>Não</b> entra na prospecção/vendas.</span>
    </div>
    <div class="mut" style="font-size:.74rem;margin-top:.5rem">🔒 Só você (dono) convida, muda papel, desativa ou exclui membros.</div>
  </div>

  <div style="margin-top:1.2rem">
    {% if not membros %}<p class="mut">Ninguém na equipe ainda. Convide a primeira pessoa acima.</p>{% endif %}
    {% for m in membros %}
    <div class="mrow">
      <div class="avatar">{{ (m.nome or m.email or '?')[:1]|upper }}</div>
      <div class="minfo">
        <form method="post" action="/painel/equipe/renomear" class="mname">
          <input type="hidden" name="membro_id" value="{{ m.id }}">
          <input name="nome" value="{{ m.nome }}" maxlength="80" title="Corrigir o nome" aria-label="Nome">
          <input class="wa" name="whatsapp" value="{{ m.whatsapp }}" inputmode="tel" maxlength="20"
                 placeholder="📱 WhatsApp" title="WhatsApp do vendedor (aviso de rodízio)" aria-label="WhatsApp">
          <button class="ebtn" title="Salvar nome e WhatsApp">✓ Salvar</button>
        </form>
        <div class="mmeta">
          {% if m.pendente %}<span class="mtag pend">convite pendente</span>
          {% elif not m.ativo %}<span class="mtag off">desativado</span>
          {% else %}<span class="mtag on">ativo</span>{% endif %}
          <span>{{ m.email }} · {{ m.rotulo }}</span>
        </div>
      </div>
      <div class="macts">
        <form method="post" action="/painel/equipe/papel" style="margin:0">
          <input type="hidden" name="membro_id" value="{{ m.id }}">
          <select name="papel" onchange="this.form.submit()" title="Papel / acesso">
            {% for v,l in papeis %}<option value="{{ v }}" {% if v==m.papel %}selected{% endif %}>{{ l }}</option>{% endfor %}
          </select>
        </form>
        {% if m.papel in ('vendedor', 'gestor') %}
        <form method="post" action="/painel/equipe/comissao" style="margin:0;display:flex;align-items:center;gap:.3rem">
          <input type="hidden" name="membro_id" value="{{ m.id }}">
          <input name="comissao_pct" type="number" step="0.1" min="0" max="100"
                 value="{{ m.comissao_pct if m.comissao_pct is not none else '' }}" placeholder="%"
                 title="% de comissão sobre as vendas dele (relatório de Comissão)" aria-label="% comissão"
                 style="width:58px;background:var(--bg);border:1px solid var(--borda);border-radius:8px;color:var(--txt);padding:.42rem .4rem;font-size:.82rem;margin:0">
          <button title="Salvar % de comissão">% comis.</button>
        </form>
        {% endif %}
        <form method="post" action="/painel/equipe/reconvite" style="margin:0"><input type="hidden" name="membro_id" value="{{ m.id }}">
          <button title="Gerar e reenviar o link de convite">↻ Novo link</button></form>
        {% if m.papel in ('vendedor','gestor','dono') and not m.pendente %}
        <form method="post" action="/painel/equipe/cockpit-link" style="margin:0"><input type="hidden" name="membro_id" value="{{ m.id }}">
          <button title="Gerar o link do app do vendedor (Cockpit) e mandar por e-mail">📱 Cockpit</button></form>
        {% endif %}
        <form method="post" action="/painel/equipe/ativo" style="margin:0">
          <input type="hidden" name="membro_id" value="{{ m.id }}">
          <input type="hidden" name="ativo" value="{{ '0' if m.ativo else '1' }}">
          <button title="{{ 'Suspender o acesso' if m.ativo else 'Reativar o acesso' }}">{{ '⏸ Desativar' if m.ativo else '▶ Reativar' }}</button></form>
        <form method="post" action="/painel/equipe/excluir" style="margin:0"
              onsubmit="return confirm('Excluir “{{ m.nome or m.email }}” da equipe? Ele perde o acesso a esta empresa.')">
          <input type="hidden" name="membro_id" value="{{ m.id }}">
          <button class="danger" title="Excluir da equipe">✕ Excluir</button></form>
      </div>
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
# ⚠️ NÃO renomeie esta chave pra "convite": o _env.loader.mapping é COMPARTILHADO com
# painel_agenda, que já usa "convite" pro convite de reunião. Como a agenda é importada
# depois, o nome genérico "convite" seria sobrescrito e o link /equipe/convite/{token}
# abriria a tela de AGENDA. Mantenha "convite_equipe" único. (ver test_convite_template_colisao)
_env.loader.mapping["convite_equipe"] = _CONVITE_TPL
_env.loader.mapping["trocar"] = _TROCAR_TPL
