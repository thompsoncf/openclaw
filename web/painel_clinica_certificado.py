"""O certificado digital do profissional (prontuário, fase 4). finance/clinica_certificado.py.

/painel/clinica/certificado                 a página do profissional logado (seção 11.6)
/painel/clinica/certificado/a1              envia o .pfx (a senha confere e é esquecida)
/painel/clinica/certificado/nuvem           escolhe o provedor da nuvem
/painel/clinica/certificado/nenhum          volta pra assinatura simples
/painel/clinica/certificado/liberar         A1: a senha do dia (seção 11.5)
/painel/clinica/certificado/encerrar        A1: tira a liberação antes do fim do dia
/painel/clinica/certificado/assinar         assina as pendentes (A1 liberado, ou vai pro provedor)
/painel/clinica/certificado/volta           a volta do provedor da nuvem (OAuth)
/doc/{codigo}                               o QR do documento: a farmácia e o validador do ITI

Só o profissional liberado (finance/clinica_acesso_clinico.leitor) mexe no PRÓPRIO
certificado: o dono não sobe o certificado de ninguém nem digita a senha de ninguém.
"""
from __future__ import annotations

import logging
import re
import secrets
from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from db.conexao import get_pool
from finance import clinica_acesso_clinico as acc
from finance import clinica_certificado as cert
from finance import clinica_psc as psc
from web.painel_clinica_agenda import _acesso, _ip_req
from web.portal import _env, _render

router = APIRouter()
router_publico = APIRouter()
URL = "/painel/clinica/certificado"
_log = logging.getLogger("clinica.certificado")
_PUBLICO = re.compile(r"[A-Za-z0-9_-]{8,40}")
_SESSAO = "certificado_dia"          # {"p": profissional, "k": a chave do A1 liberado}
_NUVEM = "certificado_nuvem"         # {"state", "verifier", "p", "volta"} enquanto o provedor autoriza


def _volta(v) -> str:
    v = str(v or "")
    return v if v.startswith("/painel/clinica/") and "//" not in v and "\\" not in v else URL


def _ir(request: Request, volta: str, aviso: str = "", erro: str = "") -> RedirectResponse:
    chave = "prontuario" if volta.startswith("/painel/clinica/prontuario/") else "certificado"
    if aviso:
        request.session[f"{chave}_aviso"] = aviso
    if erro:
        request.session[f"{chave}_erro"] = erro
    return RedirectResponse(volta, status_code=303)


def _quem(request: Request, c):
    """(conta, leitor) ou (None, resposta)."""
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return None, redir
    q = acc.leitor(c, conta[0], request.session)
    if not q:
        request.session["pacientes_erro"] = ("O certificado digital é de quem pode ler o prontuário "
                                            "(o dono libera em Configurar › Profissionais).")
        return None, RedirectResponse("/painel/clinica/pacientes", status_code=303)
    return (conta, q), None


def _chave_sessao(request: Request, q: dict) -> str | None:
    s = request.session.get(_SESSAO) or {}
    return s.get("k") if s.get("p") == q["profissional_id"] else None


def contexto(request: Request, c, conta_id: int, q: dict, agora: datetime) -> dict:
    """O que a faixa do certificado no prontuário mostra."""
    e = cert.estado(c, conta_id, q["profissional_id"], agora)
    e["provedor_nome"] = psc.nome(e["provedor"])
    e["provedor_ligado"] = bool(e["provedor"] and e["provedor"] in psc.ligados())
    if e["liberado_ate"] and not _chave_sessao(request, q):
        e["liberado_ate"] = None                   # liberado em outro aparelho: aqui não vale
    e["pendentes"] = cert.pendentes(c, conta_id, q) if e["tipo"] != "nenhum" else []
    e["lote"] = min(len(e["pendentes"]), cert.LOTE_MAX)       # o que uma rodada assina
    return e


def _assinar(request: Request, c, conta_id: int, q: dict, assinador, itens: list[dict], agora: datetime):
    """Assina, registra (um por paciente, sem o conteúdo) e grava. (quantos, erro)."""
    feitos, erro = cert.assinar_itens(c, conta_id, assinador, itens, agora)
    for f in feitos:
        acc.registrar(c, conta_id, q, f["cliente_id"],
                      f"assinou com o certificado {'a evolução' if f['alvo'] == 'evolucao' else 'o documento'} #{f['id']}",
                      _ip_req(request))
    c.commit()
    return len(feitos), erro


def depois_de_assinar(request: Request, c, conta_id: int, q: dict) -> str:
    """Chamado logo depois da assinatura simples (evolução, adendo, documento): com o A1
    liberado nesta sessão, põe a assinatura ICP na hora. Devolve o complemento do aviso.
    A assinatura simples JÁ foi gravada: nada aqui pode derrubar a resposta."""
    try:
        return _depois_de_assinar(request, c, conta_id, q)
    except Exception:  # noqa: BLE001
        _log.warning("certificado: falhou depois da assinatura simples (conta %s)", conta_id, exc_info=True)
        try:
            c.rollback()
        except Exception:  # noqa: BLE001
            pass
        return " O certificado não assinou agora: fica pendente."


def _depois_de_assinar(request: Request, c, conta_id: int, q: dict) -> str:
    agora = datetime.now(timezone.utc)
    e = cert.estado(c, conta_id, q["profissional_id"], agora)
    if e["tipo"] == "nenhum":
        return ""
    pend = cert.pendentes(c, conta_id, q)
    if not pend:
        return ""
    if e["tipo"] == "a1":
        a = cert.assinador_liberado(c, conta_id, q, _chave_sessao(request, q), agora)
        if not a:
            return " Falta o certificado: libere com a senha do dia."
        n, erro = _assinar(request, c, conta_id, q, a, pend, agora)
        return " Assinado também com o certificado." if n and not erro else \
            f" O certificado não assinou agora ({erro or 'tente de novo'}): fica pendente."
    return f" {len(pend)} esperando a assinatura no aplicativo do certificado."


# ------------------------------------------------------------------ a página

@router.get(URL, response_class=HTMLResponse)
def pagina(request: Request):
    agora = datetime.now(timezone.utc)
    with get_pool().connection() as c:
        ok, resp = _quem(request, c)
        if resp is not None:
            return resp
        conta, q = ok
        e = contexto(request, c, conta[0], q, agora)
        c.commit()
    return _render("clinica_certificado.html", request, titulo="Certificado digital", secao_ativa="pacientes",
                   q=q, e=e, provedores=psc.PROVEDORES, ligados=psc.ligados(),
                   aviso=request.session.pop("certificado_aviso", ""), erro=request.session.pop("certificado_erro", ""))


@router.post(URL + "/a1")
async def a1(request: Request):
    try:
        tam = int(request.headers.get("content-length") or 0)
    except ValueError:
        tam = 0
    if tam > cert.TETO_PFX + 16 * 1024:
        return Response("Arquivo grande demais.", status_code=413)
    form = await request.form()
    up = form.get("arquivo")
    dados = await up.read(cert.TETO_PFX + 1) if hasattr(up, "read") else b""
    return await run_in_threadpool(_a1, request, dados, str(form.get("senha") or ""))


def _a1(request: Request, dados: bytes, senha: str):
    agora = datetime.now(timezone.utc)
    with get_pool().connection() as c:
        ok, resp = _quem(request, c)
        if resp is not None:
            return resp
        conta, q = ok
        erro = cert.guardar_a1(c, conta[0], q["profissional_id"], dados, senha, q["nome"], agora)
        if erro:
            c.rollback()
            return _ir(request, URL, erro=erro)
        acc.registrar(c, conta[0], q, None, "enviou o certificado A1", _ip_req(request))
        c.commit()
    request.session.pop(_SESSAO, None)
    return _ir(request, URL, "Certificado guardado. A senha não fica no Zaq: libere uma vez por dia.")


@router.post(URL + "/nuvem")
async def nuvem(request: Request):
    form = dict(await request.form())
    return await run_in_threadpool(_nuvem, request, str(form.get("provedor") or ""))


def _nuvem(request: Request, provedor: str):
    with get_pool().connection() as c:
        ok, resp = _quem(request, c)
        if resp is not None:
            return resp
        conta, q = ok
        erro = cert.escolher_nuvem(c, conta[0], q["profissional_id"], provedor)
        if erro:
            c.rollback()
            return _ir(request, URL, erro=erro)
        acc.registrar(c, conta[0], q, None, f"escolheu o certificado em nuvem ({psc.nome(provedor)})", _ip_req(request))
        c.commit()
    request.session.pop(_SESSAO, None)
    return _ir(request, URL, "Certificado em nuvem escolhido." + ("" if provedor in psc.ligados() else
               " Este provedor ainda não está ligado no Zaq: as assinaturas ficam pendentes até ligar."))


@router.post(URL + "/nenhum")
def nenhum(request: Request):
    with get_pool().connection() as c:
        ok, resp = _quem(request, c)
        if resp is not None:
            return resp
        conta, q = ok
        cert.tirar(c, conta[0], q["profissional_id"])
        acc.registrar(c, conta[0], q, None, "tirou o certificado digital", _ip_req(request))
        c.commit()
    request.session.pop(_SESSAO, None)
    return _ir(request, URL, "Sem certificado: a assinatura volta a ser a simples.")


@router.post(URL + "/liberar")
async def liberar(request: Request):
    form = dict(await request.form())
    return await run_in_threadpool(_liberar, request, str(form.get("senha") or ""), _volta(form.get("volta")))


def _liberar(request: Request, senha: str, volta: str):
    agora = datetime.now(timezone.utc)
    with get_pool().connection() as c:
        ok, resp = _quem(request, c)
        if resp is not None:
            return resp
        conta, q = ok
        k, erro = cert.liberar_a1(c, conta[0], q, senha, agora)
        if erro:
            c.rollback()
            return _ir(request, volta, erro=erro)
        acc.registrar(c, conta[0], q, None, "liberou o certificado A1 no dia", _ip_req(request))
        c.commit()
        request.session[_SESSAO] = {"p": q["profissional_id"], "k": k}
        e = cert.estado(c, conta[0], q["profissional_id"], agora)
        n, erro = 0, None
        try:                                  # a liberação já valeu: as pendentes são um extra
            pend = cert.pendentes(c, conta[0], q)
            if pend:
                a = cert.assinador_liberado(c, conta[0], q, k, agora)
                n, erro = _assinar(request, c, conta[0], q, a, pend, agora) if a else (0, "tente de novo")
        except Exception:  # noqa: BLE001
            _log.warning("certificado: liberou, mas não assinou as pendentes (conta %s)", conta[0], exc_info=True)
            c.rollback()
            n, erro = 0, "tente de novo pelo botão"
    ate = e["liberado_ate"].strftime("%H:%M") if e["liberado_ate"] else ""
    aviso = f"Certificado liberado até {ate}: o Zaq assina ao finalizar, sem perguntar de novo."
    if n:
        aviso += f" {n} pendente{'s' if n > 1 else ''} assinada{'s' if n > 1 else ''} agora."
    return _ir(request, volta, aviso, f"O certificado não assinou as pendentes ({erro})." if erro else "")


@router.post(URL + "/encerrar")
async def encerrar(request: Request):
    form = dict(await request.form())
    return await run_in_threadpool(_encerrar, request, _volta(form.get("volta")))


def _encerrar(request: Request, volta: str):
    with get_pool().connection() as c:
        ok, resp = _quem(request, c)
        if resp is not None:
            return resp
        conta, q = ok
        cert.encerrar(c, conta[0], q["profissional_id"])
        acc.registrar(c, conta[0], q, None, "encerrou o certificado A1 do dia", _ip_req(request))
        c.commit()
    request.session.pop(_SESSAO, None)
    return _ir(request, volta, "Liberação do certificado encerrada.")


@router.post(URL + "/assinar")
async def assinar(request: Request):
    form = dict(await request.form())
    return await run_in_threadpool(_assinar_pendentes, request, _volta(form.get("volta")))


def _assinar_pendentes(request: Request, volta: str):
    agora = datetime.now(timezone.utc)
    with get_pool().connection() as c:
        ok, resp = _quem(request, c)
        if resp is not None:
            return resp
        conta, q = ok
        e = cert.estado(c, conta[0], q["profissional_id"], agora)
        pend = cert.pendentes(c, conta[0], q)
        if not pend:
            return _ir(request, volta, "Nada esperando o certificado.")
        if e["tipo"] == "a1":
            a = cert.assinador_liberado(c, conta[0], q, _chave_sessao(request, q), agora)
            if not a:
                return _ir(request, volta, erro="Libere o certificado com a senha do dia.")
            n, erro = _assinar(request, c, conta[0], q, a, pend, agora)
            return _ir(request, volta, f"{n} assinada{'s' if n != 1 else ''} com o certificado." if n else "",
                       erro or "")
        if e["tipo"] != "nuvem" or e["provedor"] not in psc.ligados():
            return _ir(request, volta, erro="O provedor do certificado ainda não está ligado no Zaq.")
        c.commit()
    state = secrets.token_urlsafe(24)
    verifier, challenge = psc.pkce()
    url = psc.url_autorizar(e["provedor"], _endereco_volta(), state, challenge)
    request.session[_NUVEM] = {"state": state, "verifier": verifier, "p": q["profissional_id"], "volta": volta,
                               "provedor": e["provedor"]}
    return RedirectResponse(url, status_code=303)


def _endereco_volta() -> str:
    from finance import clinica_planos
    return f"{clinica_planos._app_url()}{URL}/volta"


@router.get(URL + "/volta")
def volta(request: Request):
    agora = datetime.now(timezone.utc)
    pedido = request.session.pop(_NUVEM, None) or {}
    destino = _volta(pedido.get("volta"))
    state = request.query_params.get("state") or ""
    if not pedido or not secrets.compare_digest(state, str(pedido.get("state") or "")):
        return _ir(request, destino, erro="A autorização do certificado não confere: comece de novo.")
    code = request.query_params.get("code") or ""
    if not code:
        return _ir(request, destino, erro="A assinatura não foi autorizada no aplicativo do certificado.")
    with get_pool().connection() as c:
        ok, resp = _quem(request, c)
        if resp is not None:
            return resp
        conta, q = ok
        e = cert.estado(c, conta[0], q["profissional_id"], agora)
        if q["profissional_id"] != pedido.get("p") or e["tipo"] != "nuvem" or e["provedor"] != pedido.get("provedor"):
            return _ir(request, destino, erro="O certificado mudou no meio: comece de novo.")
        c.commit()
        prov = pedido["provedor"]
        try:
            tok = psc.token(prov, code, _endereco_volta(), pedido["verifier"])
            opcoes = psc.certificados(prov, tok)
        except psc.ErroPSC as ex:
            return _ir(request, destino, erro=f"O provedor do certificado recusou: {ex}")
        escolhido, erro = None, None
        for alias, der, cadeia_der in opcoes:        # o primeiro e-CPF ICP-Brasil válido
            try:
                x, extras = cert._x509(der), [cert._x509(d) for d in cadeia_der]
            except ValueError:
                continue
            erro = cert.conferir(x, agora, extras)
            if not erro:
                escolhido = (alias, x, cert.cadeia_icp(x, extras, agora) or [])
                break
        if not escolhido:
            return _ir(request, destino, erro=erro or "O provedor não mostrou um certificado válido.")
        alias, x, cadeia = escolhido
        cert.lembrar_nuvem(c, conta[0], q["profissional_id"], x)
        n, erro = _assinar(request, c, conta[0], q, cert.assinador_nuvem(prov, tok, alias, x, cadeia),
                           cert.pendentes(c, conta[0], q), agora)
    return _ir(request, destino, f"{n} assinada{'s' if n != 1 else ''} com o certificado." if n else "", erro or "")


# ------------------------------------------------------------------ o QR (farmácia e validador do ITI)

_FORMATO_ITI = "application/validador-iti+json"


@router_publico.get("/doc/{publico}")
def doc_qr(request: Request, publico: str):
    """O validador do ITI chama com ?_format=application/validador-iti+json&_secretCode=…
    e recebe onde baixar o PDF. Uma pessoa (a farmácia pelo celular) vê o pedido do código."""
    if not _PUBLICO.fullmatch(publico):         # só o formato que o Zaq cria (vai pra dentro da página)
        return Response("Documento não encontrado.", status_code=404, headers={"Cache-Control": "no-store"})
    codigo = request.query_params.get("_secretCode") or ""
    if request.query_params.get("_format") == _FORMATO_ITI:
        achou = _qr(publico, codigo)
        if not achou:
            return JSONResponse({"erro": "documento não encontrado"}, status_code=404,
                                headers={"Cache-Control": "no-store"})
        from urllib.parse import urlencode

        from finance import clinica_planos
        return JSONResponse({"version": "1.0.0", "prescription": {"signatureFiles": [
            {"url": f"{clinica_planos._app_url()}/doc/{publico}/documento.pdf?{urlencode({'_secretCode': codigo})}"}]}},
            headers={"Cache-Control": "no-store"})
    html = ("<!doctype html><html lang=pt-BR><meta charset=utf-8><meta name=viewport content='width=device-width'>"
            "<title>Documento de saúde</title><body style='font-family:sans-serif;max-width:28rem;margin:2rem auto;"
            "padding:0 1rem'><h2>Documento de saúde assinado digitalmente</h2><p>Digite o código impresso ao lado do "
            "QR para abrir o PDF. A assinatura se confere em <b>validar.iti.gov.br</b>.</p>"
            f"<form method=get action='/doc/{publico}/documento.pdf'><input name=_secretCode maxlength=64 "
            "autocomplete=off style='font-size:1.2rem;padding:.4rem;width:100%;box-sizing:border-box'>"
            "<button style='margin-top:.6rem;font-size:1rem;padding:.5rem 1rem'>Abrir</button></form></body></html>")
    return HTMLResponse(html, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer",
                                       "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; "
                                                                  "form-action 'self'"})


def _qr(publico: str, codigo: str):
    agora = datetime.now(timezone.utc)
    with get_pool().connection() as c:
        achou = cert.pelo_qr(c, publico, codigo, agora)
        c.commit()
    return achou


@router_publico.get("/doc/{publico}/documento.pdf")
def doc_qr_pdf(request: Request, publico: str):
    if not _PUBLICO.fullmatch(publico):
        return Response("Código não confere.", status_code=404, headers={"Cache-Control": "no-store"})
    codigo = request.query_params.get("_secretCode") or ""
    agora = datetime.now(timezone.utc)
    with get_pool().connection() as c:
        achou = cert.pelo_qr(c, publico, codigo, agora)
        if achou:
            pdf, conta_id, cliente_id, doc_id = achou
            try:
                acc.registrar(c, conta_id, {"nome": "farmácia/validador (pelo QR)"}, cliente_id,
                              f"abriu o documento #{doc_id} pelo QR", _ip_req(request))
                c.commit()
            except Exception:  # noqa: BLE001 — sem registro, não abre
                c.rollback()
                achou = None
    if not achou:
        return Response("Código não confere.", status_code=404, headers={"Cache-Control": "no-store"})
    return Response(pdf, media_type="application/pdf",
                    headers={"Cache-Control": "no-store, max-age=0", "Content-Security-Policy": "sandbox",
                             "Referrer-Policy": "no-referrer",
                             "Content-Disposition": f'inline; filename="documento-{doc_id}.pdf"'})


_TPL = r"""{% extends "base" %}{% block conteudo %}
<style>.ce-pag{max-width:760px;margin:0 auto;padding:1.2rem 1rem 2.5rem;box-sizing:border-box}
.ce-cx{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.8rem .9rem;margin:.8rem 0}
.ce-m{font-size:.84rem;color:var(--txt-mut)}.ce-cx button{width:auto;min-height:38px;padding:.3rem .9rem}
.ce-on{font-weight:700}</style>
<div class="ce-pag">
  <h2 style="margin:0">Certificado digital · {{ q.nome }}</h2>
  <div class="ce-m">Assina as evoluções e os documentos com o seu e-CPF (ICP-Brasil). A senha nunca fica gravada no Zaq.</div>
  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}
  {% if erro %}<div class="erro" style="margin-top:.8rem">{{ erro }}</div>{% endif %}

  <div class="ce-cx"><b>Agora:</b>
    {% if e.tipo == 'nenhum' %}sem certificado: assinatura simples (login e a impressão do conteúdo); os documentos saem para imprimir e assinar à mão.
    {% elif e.tipo == 'a1' %}<span class="ce-on">arquivo (A1)</span>{% if e.titular %} · {{ e.titular }}{% endif %}{% if e.validade %} · válido até {{ e.validade.strftime('%d/%m/%Y') }}{% endif %}
      <div class="ce-m">{% if e.liberado_ate %}Liberado neste aparelho até {{ e.liberado_ate.strftime('%H:%M') }}.{% else %}Não liberado hoje.{% endif %}</div>
    {% else %}<span class="ce-on">nuvem · {{ e.provedor_nome }}</span>{% if e.titular %} · {{ e.titular }}{% endif %}{% if e.validade %} · válido até {{ e.validade.strftime('%d/%m/%Y') }}{% endif %}
      {% if not e.provedor_ligado %}<div class="erro" style="margin-top:.4rem">Este provedor ainda não está ligado no Zaq (a clínica precisa se cadastrar nele como aplicação). Até lá, o que você assina fica pendente.</div>{% endif %}
    {% endif %}
    {% if e.pendentes %}<div style="margin-top:.5rem">{{ e.pendentes|length }} esperando o certificado.</div>
      {% if e.tipo == 'a1' and not e.liberado_ate %}
      {% else %}<form method="post" action="/painel/clinica/certificado/assinar" style="margin-top:.4rem"><button>{% if e.tipo == 'nuvem' %}Assinar {{ e.lote }} no aplicativo{% else %}Assinar {{ e.lote }} agora{% endif %}</button>{% if e.pendentes|length > e.lote %} <span class="ce-m">(as outras {{ e.pendentes|length - e.lote }} na próxima rodada)</span>{% endif %}</form>{% endif %}
    {% endif %}
    {% if e.tipo == 'a1' %}
      {% if e.liberado_ate %}<form method="post" action="/painel/clinica/certificado/encerrar" style="margin-top:.5rem"><button class="sec">Encerrar a liberação agora</button></form>
      {% else %}<form method="post" action="/painel/clinica/certificado/liberar" style="margin-top:.5rem">
        <label>Senha do certificado<input type="password" name="senha" autocomplete="off" required></label>
        <button>Liberar a assinatura até o fim do dia</button>
        <div class="ce-m">A senha abre o certificado agora e é esquecida. Ele fica liberado só neste aparelho, até 23:59 de hoje.</div></form>{% endif %}
    {% endif %}
  </div>

  <div class="ce-cx"><b>Certificado em arquivo (A1)</b>
    <div class="ce-m">O arquivo .pfx ou .p12 do seu e-CPF. Ele fica guardado cifrado; a senha só confere o arquivo e não é gravada.</div>
    <form method="post" action="/painel/clinica/certificado/a1" enctype="multipart/form-data" style="margin-top:.5rem">
      <label>Arquivo do certificado<input type="file" name="arquivo" accept=".pfx,.p12,application/x-pkcs12" required></label>
      <label>Senha do certificado<input type="password" name="senha" autocomplete="off" required></label>
      <button onclick="this.disabled=true;this.form.submit()">{% if e.tipo == 'a1' %}Trocar o certificado A1{% else %}Usar o certificado A1{% endif %}</button></form>
  </div>

  <div class="ce-cx"><b>Certificado em nuvem</b>
    <div class="ce-m">Você autoriza no aplicativo do provedor (no fim do atendimento, ou "as N de hoje" de uma vez). Cartão ou token na USB não: peça ao seu provedor a versão em nuvem do mesmo certificado.</div>
    <form method="post" action="/painel/clinica/certificado/nuvem" style="margin-top:.5rem">
      <label>Provedor<select name="provedor">{% for k, v in provedores.items() %}<option value="{{ k }}" {% if e.provedor == k %}selected{% endif %}>{{ v[0] }}{% if k not in ligados %} (ainda não ligado){% endif %}</option>{% endfor %}</select></label>
      <button class="sec">Usar o certificado em nuvem</button></form>
  </div>

  {% if e.tipo != 'nenhum' %}<div class="ce-cx"><form method="post" action="/painel/clinica/certificado/nenhum"
      onsubmit="return confirm('Voltar para a assinatura simples? O que já foi assinado com o certificado continua assinado.')">
    <button class="sec">Não usar certificado</button> <span class="ce-m">o arquivo A1 guardado é apagado</span></form></div>{% endif %}
</div>
{% endblock %}"""

_env.loader.mapping["clinica_certificado.html"] = _TPL
