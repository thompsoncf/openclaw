"""O prontuário do paciente (fase 2): a ficha clínica e as evoluções.

/painel/clinica/prontuario/{paciente}                     a tela (seção 02 do desenho)
/painel/clinica/prontuario/{paciente}/ficha               salva a ficha clínica (versão nova)
/painel/clinica/prontuario/{paciente}/evolucao/nova       rascunho novo (do atendimento, se veio da agenda)
/painel/clinica/prontuario/{paciente}/evolucao/{id}       o rascunho (só o autor)
/painel/clinica/prontuario/{paciente}/evolucao/{id}/salvar   salva (o autosave também)
/painel/clinica/prontuario/{paciente}/evolucao/{id}/assinar  assina (vira imutável)
/painel/clinica/prontuario/{paciente}/evolucao/{id}/adendo   a correção, assinada

Só o profissional liberado (finance/clinica_acesso_clinico): todo o resto volta pra ficha
do paciente. Toda abertura e toda assinatura vão pro registro de acesso.
"""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from db.conexao import get_pool
from finance import clinica_acesso_clinico as acc
from finance import clinica_agenda as ca
from finance import clinica_certificado as cert
from finance import clinica_pacientes as cpa
from finance import clinica_preconsulta as cpc
from finance import clinica_prontuario as prt
from finance import clinica_documentos as cdoc
from finance import clinica_prontuario_arquivos as parq
from web import painel_clinica_certificado as wcert
from web.painel_clinica_agenda import _acesso, _int
from web.portal import _env, _render

router = APIRouter()
URL = "/painel/clinica/prontuario"


def _ip(request: Request) -> str:
    xf = request.headers.get("x-forwarded-for", "")
    return (xf.split(",")[-1].strip() if xf else (request.client.host if request.client else ""))[:60]


def _volta_ficha(cliente_id: int) -> RedirectResponse:
    return RedirectResponse(f"/painel/clinica/pacientes/{cliente_id}", status_code=303)


def _ir(request: Request, cliente_id: int, aviso: str = "", erro: str = "", evo: int | None = None) -> RedirectResponse:
    if erro:
        request.session["prontuario_erro"] = erro
    if aviso:
        request.session["prontuario_aviso"] = aviso
    return RedirectResponse(f"{URL}/{cliente_id}" + (f"/evolucao/{evo}" if evo else ""), status_code=303)


def cpa_do_evento(c, conta_id: int, evento_id: int) -> int | None:
    r = c.execute("select cliente_id from eventos_agenda where id=%s and conta_id=%s", (evento_id, conta_id)).fetchone()
    return r[0] if r else None


def _pode(request: Request, c, cliente_id: int):
    """(conta, leitor) ou (None, resposta): só profissional liberado, e o paciente da conta."""
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return None, redir
    q = acc.leitor(c, conta[0], request.session)
    if not q:
        return None, _volta_ficha(cliente_id)
    if not c.execute("select 1 from clientes where id=%s and dono_id=%s", (cliente_id, conta[0])).fetchone():
        return None, RedirectResponse("/painel/clinica/pacientes", status_code=303)
    return (conta, q), None


@router.get(URL + "/{cliente_id}", response_class=HTMLResponse)
def prontuario(request: Request, cliente_id: int):
    agora = datetime.now(timezone.utc)
    evento = _int(request.query_params.get("evento"))
    with get_pool().connection() as c:
        ok, resp = _pode(request, c, cliente_id)
        if resp is not None:
            return resp
        conta, q = ok
        try:
            acc.ler(c, conta[0], request.session, cliente_id, "prontuário", _ip(request))
        except acc.SemRegistro:
            request.session["pacientes_erro"] = "Não foi possível abrir o prontuário agora (a abertura precisa ficar no registro)."
            return _volta_ficha(cliente_id)
        p = cpa.ficha(c, conta[0], cliente_id, agora)
        if not p:
            return RedirectResponse("/painel/clinica/pacientes", status_code=303)
        fc = prt.ficha_clinica(c, conta[0], cliente_id)
        pre = cpc.ultima(c, conta[0], cliente_id)
        evos = prt.historia(c, conta[0], cliente_id, q)
        arquivos = parq.listar(c, conta[0], cliente_id)
        docs = cdoc.listar(c, conta[0], cliente_id, q)
        for d in docs:
            d["integro"] = cdoc.integro(d, cliente_id, conta[0]) if d["status"] == "assinado" else None
        cx = wcert.contexto(request, c, conta[0], q, agora)
        _marcar_icp(c, conta[0], evos, docs, cx)
        imagem_op, imagem_txt = parq.autorizacao_de_imagem(c, conta[0], cliente_id)
        ev = ca.evento(c, conta[0], evento) if evento else None
        if ev and cpa_do_evento(c, conta[0], evento) != cliente_id:
            ev = None                             # atendimento de outro paciente: nada
        c.commit()
    return _render("clinica_prontuario.html", request, titulo=f"Prontuário · {p['nome']}", secao_ativa="pacientes",
                   p=p, fc=fc, pre=pre, evos=evos, q=q, ev=ev, MODELOS=prt.MODELOS,
                   arquivos=arquivos, cofre=parq.configurado(), imagem_op=imagem_op, imagem_txt=imagem_txt,
                   docs=docs, TIPOS_DOC=cdoc.TIPOS, cx=cx,
                   aviso=request.session.pop("prontuario_aviso", ""), erro=request.session.pop("prontuario_erro", ""))


def _marcar_icp(c, conta_id: int, evos: list[dict], docs: list[dict], cx: dict) -> None:
    """Quem tem a assinatura com certificado (fase 4), e quem está esperando por ela."""
    todas = [e for x in evos for e in (x, *x.get("adendos", []))]
    feitas = cert.assinados(c, conta_id, "evolucao", [e["id"] for e in todas])
    feitos = cert.assinados(c, conta_id, "documento", [d["id"] for d in docs])
    esperando = {(p["alvo"], p["id"]) for p in cx["pendentes"]}
    for e in todas:
        e["icp"] = feitas.get(e["id"])
        e["icp_pendente"] = ("evolucao", e["id"]) in esperando
    for d in docs:
        d["icp"] = feitos.get(d["id"])
        d["icp_pendente"] = ("documento", d["id"]) in esperando


# ------------------------------------------------------------------ fotos e anexos (fase 3)

@router.post(URL + "/{cliente_id}/arquivos")
async def arquivos(request: Request, cliente_id: int):
    """Upload: primeiro o tamanho (antes de ler), depois o acesso (antes de ler), só
    então o arquivo — e lido com teto."""
    try:
        tam = int(request.headers.get("content-length") or 0)
    except ValueError:
        tam = 0
    if tam > parq.TETO_CORPO:
        return Response("Arquivo grande demais.", status_code=413)
    negado = await run_in_threadpool(_arquivos_pode, request, cliente_id)
    if negado is not None:
        return negado
    form = await request.form()
    up = form.get("arquivo")
    campos = {k: str(v) for k, v in form.items() if k != "arquivo"}
    tipo = "foto" if campos.get("tipo") == "foto" else "anexo"
    dados = await up.read(parq.TETO[tipo] + 1) if hasattr(up, "read") else b""
    tipo_mime = getattr(up, "content_type", "") or ""
    return await run_in_threadpool(_arquivos, request, cliente_id, campos, dados, tipo_mime, tipo)


def _arquivos_pode(request: Request, cliente_id: int):
    with get_pool().connection() as c:
        _ok, resp = _pode(request, c, cliente_id)
    return resp


def _arquivos(request: Request, cliente_id: int, form: dict, dados: bytes, tipo_mime: str, tipo: str):
    # 1) o acesso e o termo, numa conexão curta
    with get_pool().connection() as c:
        ok, resp = _pode(request, c, cliente_id)
        if resp is not None:
            return resp
        conta, q = ok
        autorizacao, erro = parq.conferir(c, conta[0], cliente_id, tipo=tipo, mimetype=tipo_mime,
                                          papel_autorizado=bool(form.get("papel")),
                                          e_documento=bool(form.get("documento")))
        c.commit()
    if erro:
        return _ir(request, cliente_id, erro=erro)
    # 2) limpar, cifrar e subir, FORA da conexão
    pronto, erro = parq.preparar(conta[0], cliente_id, tipo=tipo, dados=dados, mimetype=tipo_mime)
    if erro:
        return _ir(request, cliente_id, erro=erro)
    # 3) o registro no banco
    with get_pool().connection() as c:
        aid = parq.registrar_arquivo(c, conta[0], cliente_id, q, pronto, tipo=tipo, autorizacao=autorizacao,
                                     regiao=form.get("regiao", ""), legenda=form.get("legenda", ""),
                                     evento_id=_int(form.get("evento")))
        acc.registrar(c, conta[0], q, cliente_id, f"guardou {'a foto' if tipo == 'foto' else 'o anexo'} #{aid}",
                      _ip(request))
        c.commit()
    return _ir(request, cliente_id, "Foto guardada." if tipo == "foto" else "Anexo guardado.")


@router.get(URL + "/{cliente_id}/arquivo/{arquivo_id}")
def arquivo(request: Request, cliente_id: int, arquivo_id: int):
    """A foto ou o anexo, decifrado na hora, só pra quem pode ler (e registrado com o
    número do arquivo). Nunca fica no cache do aparelho."""
    with get_pool().connection() as c:
        ok, resp = _pode(request, c, cliente_id)
        if resp is not None:
            return resp
        conta, _q = ok
        info = parq.localizar(c, conta[0], cliente_id, arquivo_id)
        if not info:
            return Response("Arquivo não encontrado.", status_code=404)
        try:
            if not acc.ler(c, conta[0], request.session, cliente_id,
                           f"abriu {'a foto' if info['tipo'] == 'foto' else 'o anexo'} #{arquivo_id}"
                           + (f" ({info['regiao']})" if info["regiao"] else ""), _ip(request)):
                return _volta_ficha(cliente_id)
        except acc.SemRegistro:
            return Response("Não foi possível abrir agora.", status_code=503)
        c.commit()
    try:
        dados = parq.baixar(info)                      # fora da conexão do banco
    except ValueError as e:
        return Response(str(e), status_code=409)
    return Response(dados, media_type=info["mimetype"],
                    headers={"Cache-Control": "no-store, max-age=0", "Pragma": "no-cache",
                             "Content-Security-Policy": "sandbox",
                             "Content-Disposition": f'inline; filename="prontuario-{arquivo_id}"'})


# ------------------------------------------------------------------ documentos (fase 5)

@router.post(URL + "/{cliente_id}/documento/novo")
async def documento_novo(request: Request, cliente_id: int):
    form = dict(await request.form())
    return await run_in_threadpool(_documento_novo, request, cliente_id, form)


def _documento_novo(request: Request, cliente_id: int, form: dict):
    with get_pool().connection() as c:
        ok, resp = _pode(request, c, cliente_id)
        if resp is not None:
            return resp
        conta, q = ok
        agora = datetime.now(timezone.utc)
        p = cpa.ficha(c, conta[0], cliente_id, agora)
        did = cdoc.novo(c, conta[0], cliente_id, q, str(form.get("tipo") or ""), p["nome"] if p else "", agora,
                        _int(form.get("evento")))
        c.commit()
    if not did:
        return _ir(request, cliente_id, erro="Tipo de documento inválido.")
    return RedirectResponse(f"{URL}/{cliente_id}/documento/{did}", status_code=303)


@router.get(URL + "/{cliente_id}/documento/{doc_id}", response_class=HTMLResponse)
def documento(request: Request, cliente_id: int, doc_id: int):
    with get_pool().connection() as c:
        ok, resp = _pode(request, c, cliente_id)
        if resp is not None:
            return resp
        conta, q = ok
        d = cdoc.documento(c, conta[0], cliente_id, doc_id)
        if not d or d["profissional_id"] != q["profissional_id"] or d["status"] != "rascunho":
            return _ir(request, cliente_id)
        try:
            acc.ler(c, conta[0], request.session, cliente_id, f"abriu o rascunho do documento #{doc_id}", _ip(request))
        except acc.SemRegistro:
            return _ir(request, cliente_id, erro="Não foi possível abrir agora.")
        p = cpa.ficha(c, conta[0], cliente_id, datetime.now(timezone.utc))
        c.commit()
    return _render("clinica_documento.html", request, titulo=f"{d['titulo']} · {p['nome']}", secao_ativa="pacientes",
                   p=p, d=d, erro=request.session.pop("prontuario_erro", ""))


@router.post(URL + "/{cliente_id}/documento/{doc_id}/salvar")
async def documento_salvar(request: Request, cliente_id: int, doc_id: int):
    form = dict(await request.form())
    return await run_in_threadpool(_documento_salvar, request, cliente_id, doc_id, form)


def _documento_salvar(request: Request, cliente_id: int, doc_id: int, form: dict):
    emitir = form.get("acao") == "emitir"
    with get_pool().connection() as c:
        ok, resp = _pode(request, c, cliente_id)
        if resp is not None:
            return resp
        conta, q = ok
        erro = cdoc.salvar(c, conta[0], cliente_id, doc_id, q, form)
        if not erro and emitir:
            erro = cdoc.emitir(c, conta[0], cliente_id, doc_id, q)
        if erro:
            c.rollback()
            request.session["prontuario_erro"] = erro
            return RedirectResponse(f"{URL}/{cliente_id}/documento/{doc_id}", status_code=303)
        if emitir:
            acc.registrar(c, conta[0], q, cliente_id, f"emitiu o documento #{doc_id}", _ip(request))
        c.commit()
        extra = wcert.depois_de_assinar(request, c, conta[0], q) if emitir else ""
    if emitir:
        return _ir(request, cliente_id, "Documento emitido." + extra)
    return RedirectResponse(f"{URL}/{cliente_id}/documento/{doc_id}", status_code=303)


@router.get(URL + "/{cliente_id}/documento/{doc_id}/pdf")
def documento_pdf(request: Request, cliente_id: int, doc_id: int):
    with get_pool().connection() as c:
        ok, resp = _pode(request, c, cliente_id)
        if resp is not None:
            return resp
        conta, _q = ok
        d = cdoc.documento(c, conta[0], cliente_id, doc_id)
        if not d or d["status"] != "assinado":
            return Response("Documento não encontrado.", status_code=404)
        try:
            if not acc.ler(c, conta[0], request.session, cliente_id, f"abriu o documento #{doc_id}", _ip(request)):
                return _volta_ficha(cliente_id)
        except acc.SemRegistro:
            return Response("Não foi possível abrir agora.", status_code=503)
        doc = cdoc.pdf(c, conta[0], cliente_id, d)
        c.commit()
    if not doc:
        return Response("Este documento não tem PDF (fica no talão).", status_code=404)
    return Response(doc, media_type="application/pdf",
                    headers={"Cache-Control": "no-store, max-age=0", "Content-Security-Policy": "sandbox",
                             "Content-Disposition": f'inline; filename="documento-{doc_id}.pdf"'})


@router.post(URL + "/{cliente_id}/documentos/enviar")
async def documentos_enviar(request: Request, cliente_id: int):
    form = await request.form()
    ids = [x for x in (_int(v) for v in form.getlist("doc")) if x]
    return await run_in_threadpool(_documentos_enviar, request, cliente_id, ids)


def _documentos_enviar(request: Request, cliente_id: int, ids: list[int]):
    with get_pool().connection() as c:
        ok, resp = _pode(request, c, cliente_id)
        if resp is not None:
            return resp
        conta, q = ok
        erro = cdoc.enviar(c, conta[0], cliente_id, ids, request.session.get("membro_id"), quem_manda=q["nome"])
        if erro:
            c.rollback()
            return _ir(request, cliente_id, erro=erro)
        c.commit()
    return _ir(request, cliente_id, "Link dos documentos enviado no WhatsApp.")


@router.get(URL + "/{cliente_id}/comparar", response_class=HTMLResponse)
def comparar(request: Request, cliente_id: int):
    ids = [x for x in (_int(v) for v in request.query_params.getlist("f")) if x][:2]
    with get_pool().connection() as c:
        ok, resp = _pode(request, c, cliente_id)
        if resp is not None:
            return resp
        conta, _q = ok
        todos = {a["id"]: a for a in parq.listar(c, conta[0], cliente_id) if a["tipo"] == "foto"}
        p = cpa.ficha(c, conta[0], cliente_id, datetime.now(timezone.utc))
        c.commit()
    fotos = [todos[i] for i in ids if i in todos]
    if len(fotos) != 2:
        return _ir(request, cliente_id, erro="Escolha duas fotos para comparar.")
    return _render("clinica_comparar.html", request, titulo=f"Comparar · {p['nome']}", secao_ativa="pacientes",
                   p=p, fotos=sorted(fotos, key=lambda a: a["quando"]))


@router.post(URL + "/{cliente_id}/ficha")
async def ficha(request: Request, cliente_id: int):
    form = dict(await request.form())
    return await run_in_threadpool(_ficha, request, cliente_id, form)


def _ficha(request: Request, cliente_id: int, form: dict):
    with get_pool().connection() as c:
        ok, resp = _pode(request, c, cliente_id)
        if resp is not None:
            return resp
        conta, q = ok
        if form.get("levar_alergia"):
            # "um clique leva pra ficha" (seção 12): a alergia que o paciente contou
            try:
                acc.ler(c, conta[0], request.session, cliente_id, "levou a alergia da pré-consulta", _ip(request))
            except acc.SemRegistro:
                return _ir(request, cliente_id, erro="Não foi possível agora. Tente de novo.")
            pre = cpc.ultima(c, conta[0], cliente_id)
            txt = next((v for k, v in (pre or {}).get("linhas", []) if "alergia" in k.lower()), "")
            atual = prt.ficha_clinica(c, conta[0], cliente_id)
            form = dict(atual)
            if txt.startswith("Sim"):
                al = txt[4:].lstrip(": ").strip() or "alergia informada na pré-consulta (sem dizer qual)"
                ja = {x.strip().lower() for x in atual["alergias"].replace(",", "\n").split("\n") if x.strip()}
                if al.lower() not in ja:
                    form["alergias"] = (atual["alergias"] + "\n" + al).strip()
            else:
                c.commit()
                return _ir(request, cliente_id, "A pré-consulta não informou alergia.")
        mudou = prt.salvar_ficha_clinica(c, conta[0], cliente_id, q, form)
        if mudou:
            acc.registrar(c, conta[0], q, cliente_id, "atualizou a ficha clínica", _ip(request))
        c.commit()
    return _ir(request, cliente_id, "Ficha clínica salva." if mudou else "Nada mudou na ficha clínica.")


@router.post(URL + "/{cliente_id}/evolucao/nova")
async def evolucao_nova(request: Request, cliente_id: int):
    form = dict(await request.form())
    return await run_in_threadpool(_evolucao_nova, request, cliente_id, form)


def _evolucao_nova(request: Request, cliente_id: int, form: dict):
    with get_pool().connection() as c:
        ok, resp = _pode(request, c, cliente_id)
        if resp is not None:
            return resp
        conta, q = ok
        eid = prt.nova(c, conta[0], cliente_id, q, str(form.get("modelo") or "livre"), _int(form.get("evento")))
        c.commit()
    if not eid:
        return _ir(request, cliente_id, erro="Modelo inválido.")
    return _ir(request, cliente_id, evo=eid)


@router.get(URL + "/{cliente_id}/evolucao/{evo_id}", response_class=HTMLResponse)
def evolucao(request: Request, cliente_id: int, evo_id: int):
    with get_pool().connection() as c:
        ok, resp = _pode(request, c, cliente_id)
        if resp is not None:
            return resp
        conta, q = ok
        e = prt.evolucao(c, conta[0], cliente_id, evo_id)
        if not e or e["profissional_id"] != q["profissional_id"] or e["status"] != "rascunho":
            return _ir(request, cliente_id)
        try:
            acc.ler(c, conta[0], request.session, cliente_id, "evolução (rascunho)", _ip(request))
        except acc.SemRegistro:
            return _ir(request, cliente_id, erro="Não foi possível abrir agora (a abertura precisa ficar no registro).")
        p = cpa.ficha(c, conta[0], cliente_id, datetime.now(timezone.utc))
        fc = prt.ficha_clinica(c, conta[0], cliente_id)
        ev = ca.evento(c, conta[0], e["evento_id"]) if e["evento_id"] else None
        c.commit()
    return _render("clinica_evolucao.html", request, titulo=f"Evolução · {p['nome']}", secao_ativa="pacientes",
                   p=p, e=e, fc=fc, ev=ev, MODELOS=prt.MODELOS, erro=request.session.pop("prontuario_erro", ""))


@router.post(URL + "/{cliente_id}/evolucao/{evo_id}/salvar")
async def salvar(request: Request, cliente_id: int, evo_id: int):
    form = dict(await request.form())
    return await run_in_threadpool(_salvar, request, cliente_id, evo_id, form)


def _salvar(request: Request, cliente_id: int, evo_id: int, form: dict):
    auto = form.get("auto") == "1"
    with get_pool().connection() as c:
        ok, resp = _pode(request, c, cliente_id)
        if resp is not None:
            return JSONResponse({"ok": False}, status_code=403) if auto else resp
        conta, q = ok
        erro = prt.salvar(c, conta[0], cliente_id, evo_id, q, form)
        (c.rollback if erro else c.commit)()
    if auto:
        return JSONResponse({"ok": not erro, "erro": erro or "",
                             "salvo": ca.local(datetime.now(timezone.utc)).strftime("%H:%M:%S")})
    if form.get("trocar_modelo"):
        return _ir(request, cliente_id, evo=evo_id)
    return _ir(request, cliente_id, "Rascunho salvo." if not erro else "", erro or "", evo=None if not erro else evo_id)


@router.post(URL + "/{cliente_id}/evolucao/{evo_id}/assinar")
async def assinar(request: Request, cliente_id: int, evo_id: int):
    form = dict(await request.form())
    return await run_in_threadpool(_assinar, request, cliente_id, evo_id, form)


def _assinar(request: Request, cliente_id: int, evo_id: int, form: dict):
    agora = datetime.now(timezone.utc)
    with get_pool().connection() as c:
        ok, resp = _pode(request, c, cliente_id)
        if resp is not None:
            return resp
        conta, q = ok
        erro = prt.salvar(c, conta[0], cliente_id, evo_id, q, form) if form else None
        erro = erro or prt.assinar(c, conta[0], cliente_id, evo_id, q, agora)
        if erro:
            c.rollback()
            return _ir(request, cliente_id, erro=erro, evo=evo_id)
        acc.registrar(c, conta[0], q, cliente_id, "assinou uma evolução", _ip(request))
        prt.retorno_depois_de_finalizar(c, conta[0], cliente_id, evo_id)
        c.commit()
        extra = wcert.depois_de_assinar(request, c, conta[0], q)
    return _ir(request, cliente_id, "Evolução assinada." + extra)


@router.post(URL + "/{cliente_id}/evolucao/{evo_id}/descartar")
def descartar(request: Request, cliente_id: int, evo_id: int):
    with get_pool().connection() as c:
        ok, resp = _pode(request, c, cliente_id)
        if resp is not None:
            return resp
        conta, q = ok
        foi = prt.descartar(c, conta[0], cliente_id, evo_id, q)
        c.commit()
    return _ir(request, cliente_id, "Rascunho descartado." if foi else "")


@router.post(URL + "/{cliente_id}/evolucao/{evo_id}/adendo")
async def adendo(request: Request, cliente_id: int, evo_id: int):
    form = dict(await request.form())
    return await run_in_threadpool(_adendo, request, cliente_id, evo_id, form)


def _adendo(request: Request, cliente_id: int, evo_id: int, form: dict):
    agora = datetime.now(timezone.utc)
    with get_pool().connection() as c:
        ok, resp = _pode(request, c, cliente_id)
        if resp is not None:
            return resp
        conta, q = ok
        erro = prt.adendo(c, conta[0], cliente_id, evo_id, q, str(form.get("texto") or ""), agora)
        if erro:
            c.rollback()
            return _ir(request, cliente_id, erro=erro)
        acc.registrar(c, conta[0], q, cliente_id, "escreveu um adendo", _ip(request))
        c.commit()
        extra = wcert.depois_de_assinar(request, c, conta[0], q)
    return _ir(request, cliente_id, "Adendo assinado." + extra)


@router.get(URL + "/{cliente_id}/evolucao/{evo_id}/pdf")
def evolucao_pdf(request: Request, cliente_id: int, evo_id: int):
    """O PDF da evolução assinada com o certificado (fase 4): o que se entrega ou se
    confere em validar.iti.gov.br."""
    with get_pool().connection() as c:
        ok, resp = _pode(request, c, cliente_id)
        if resp is not None:
            return resp
        conta, _q = ok
        e = prt.evolucao(c, conta[0], cliente_id, evo_id)
        doc = cert.pdf_assinado(c, conta[0], "evolucao", evo_id) if e else None
        if not doc:
            return Response("Esta evolução não tem assinatura com certificado.", status_code=404)
        try:
            if not acc.ler(c, conta[0], request.session, cliente_id, f"abriu o PDF assinado da evolução #{evo_id}",
                           _ip(request)):
                return _volta_ficha(cliente_id)
        except acc.SemRegistro:
            return Response("Não foi possível abrir agora.", status_code=503)
        c.commit()
    return Response(doc, media_type="application/pdf",
                    headers={"Cache-Control": "no-store, max-age=0", "Content-Security-Policy": "sandbox",
                             "Content-Disposition": f'inline; filename="evolucao-{evo_id}.pdf"'})


_CSS = r"""<style>
.pr-pag{width:100%;max-width:var(--pag,1180px);margin:0 auto;padding:1.2rem 1rem 2.5rem;box-sizing:border-box}
.pr-topo{display:flex;align-items:flex-end;justify-content:space-between;gap:.8rem;flex-wrap:wrap}
.pr-topo h2{margin:0;font-size:1.45rem}.pr-m{font-size:.84rem;color:var(--txt-mut)}
.pr-cx{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.8rem .9rem;margin:.8rem 0}
.pr-alerta{background:rgba(224,87,79,.12);border:1px solid #E0574F;border-radius:9px;padding:.5rem .7rem;margin-top:.6rem;font-weight:600}
.pr-3{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:.6rem}
.pr-3 div b{display:block;font-size:.8rem;color:var(--txt-mut);font-weight:600}
.pr-txt{white-space:pre-wrap}
.pr-evo{border-top:1px solid var(--borda);padding:.7rem 0}
.pr-evo h4{margin:0 0 .3rem;font-size:.95rem}
.pr-ad{margin:.5rem 0 0 1rem;padding-left:.7rem;border-left:3px solid var(--borda)}
.pr-tag{font-size:.72rem;padding:.08rem .45rem;border-radius:999px;border:1px solid var(--borda);color:var(--txt-mut)}
.pr-tag.g{border-color:var(--verde);color:var(--verde-claro)}.pr-tag.y{border-color:var(--ambar-borda);color:#F0DCA6}.pr-tag.r{border-color:#E0574F;color:#F4A9A4}
.pr-acoes{display:flex;gap:.4rem;flex-wrap:wrap;margin-top:.5rem}.pr-acoes form{margin:0}
.pr-acoes button,.pr-acoes a{width:auto;margin:0;min-height:38px;padding:.3rem .8rem;font-size:.86rem;border-radius:8px}
.pr-acoes .sec,.pr-acoes a{background:transparent;border:1px solid var(--borda);color:var(--txt);text-decoration:none;display:inline-flex;align-items:center}
.pr-cx textarea{min-height:5.5rem}
</style>"""

_TPL = r"""{% extends "base" %}{% block conteudo %}""" + _CSS + r"""
<div class="pr-pag">
  <div class="pr-topo"><div><h2>{{ p.nome_social or p.nome }}</h2>
    <div class="pr-m">{% if p.idade is not none %}{{ p.idade }} anos · {% endif %}{% if p.desde %}paciente desde {{ p.desde.strftime('%m/%Y') }} · {% endif %}prontuário · só os profissionais de saúde da clínica leem, e cada abertura fica registrada</div></div>
    <div class="pr-acoes"><a href="/painel/clinica/pacientes/{{ p.id }}">‹ Ficha</a>{% if ev %}<a href="/painel/clinica/agenda/evento/{{ ev.id }}">Agendamento {{ ev.hora }}</a>{% endif %}</div></div>
  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}
  {% if erro %}<div class="erro" style="margin-top:.8rem">{{ erro }}</div>{% endif %}
  {% if fc.alergias %}<div class="pr-alerta">ALERGIA: {{ fc.alergias }}</div>{% endif %}
  {% if cx.tipo == 'nenhum' %}<div class="pr-m" style="margin-top:.6rem">Assinatura simples (sem certificado digital) · <a href="/painel/clinica/certificado">ligar o certificado</a></div>
  {% else %}<div class="pr-cx" style="display:flex;gap:.6rem;align-items:center;flex-wrap:wrap"><b>Certificado</b>
    <span class="pr-m">{{ 'arquivo (A1)' if cx.tipo == 'a1' else 'nuvem · ' ~ cx.provedor_nome }}{% if cx.validade %} · válido até {{ cx.validade.strftime('%d/%m/%Y') }}{% endif %}{% if cx.liberado_ate %} · liberado até {{ cx.liberado_ate.strftime('%H:%M') }}{% endif %}{% if cx.pendentes %} · {{ cx.pendentes|length }} esperando{% endif %}</span>
    {% if cx.tipo == 'a1' and not cx.liberado_ate %}<form method="post" action="/painel/clinica/certificado/liberar" class="pr-acoes" style="margin:0;display:flex;gap:.4rem;align-items:center">
      <input type="hidden" name="volta" value="/painel/clinica/prontuario/{{ p.id }}"><input type="password" name="senha" placeholder="senha do certificado" autocomplete="off" required style="width:auto;margin:0">
      <button>Liberar até o fim do dia</button></form>
    {% elif cx.pendentes and (cx.tipo == 'a1' or cx.provedor_ligado) %}<form method="post" action="/painel/clinica/certificado/assinar" class="pr-acoes" style="margin:0">
      <input type="hidden" name="volta" value="/painel/clinica/prontuario/{{ p.id }}"><button class="sec">{% if cx.tipo == 'nuvem' %}Assinar {{ cx.lote }} no aplicativo{% else %}Assinar {{ cx.lote }}{% endif %}</button></form>{% endif %}
    <a href="/painel/clinica/certificado" class="pr-m">configurar</a></div>{% endif %}

  <div class="pr-cx">
    <div class="pr-3">
      <div><b>Alergias</b><div class="pr-txt">{{ fc.alergias or 'nenhuma registrada' }}</div></div>
      <div><b>Medicamentos em uso</b><div class="pr-txt">{{ fc.medicamentos or '—' }}</div></div>
      <div><b>Problemas e antecedentes</b><div class="pr-txt">{{ fc.problemas or '—' }}</div></div>
    </div>
    {% if fc.quando %}<div class="pr-m" style="margin-top:.4rem">Atualizada por {{ fc.por }} em {{ fc.quando.strftime('%d/%m/%Y %H:%M') }}</div>{% endif %}
    <details style="margin-top:.5rem"><summary>Editar a ficha clínica</summary>
      <form method="post" action="/painel/clinica/prontuario/{{ p.id }}/ficha" style="margin-top:.5rem">
        <label>Alergias<textarea name="alergias">{{ fc.alergias }}</textarea></label>
        <label>Medicamentos em uso<textarea name="medicamentos">{{ fc.medicamentos }}</textarea></label>
        <label>Problemas e antecedentes<textarea name="problemas">{{ fc.problemas }}</textarea></label>
        <div class="pr-acoes"><button>Salvar a ficha clínica</button></div>
        <div class="pr-m">Cada salvamento é uma versão nova (quem e quando); nada se apaga.</div>
      </form></details>
  </div>

  {% if pre %}<div class="pr-cx"><b>Pré-consulta</b> <span class="pr-m">contado pelo {{ 'responsável' if pre.por == 'responsavel' else 'paciente' }} em {{ pre.quando.strftime('%d/%m/%Y') }}{% if pre.curta %} · retorno{% endif %} — confira na consulta</span>
    {% for pergunta, resposta in pre.linhas %}<div style="margin-top:.35rem"><span class="pr-m">{{ pergunta }}</span> {{ resposta }}</div>{% endfor %}
    <form method="post" action="/painel/clinica/prontuario/{{ p.id }}/ficha" class="pr-acoes"><input type="hidden" name="levar_alergia" value="1"><button class="sec">Levar a alergia contada para a ficha clínica</button></form>
  </div>{% endif %}

  <div class="pr-cx" id="fotos"><b>Fotos e anexos</b> <span class="pr-m">· {{ imagem_txt or 'sem termo de imagem aceito' }}</span>
    {% if arquivos %}<form method="get" action="/painel/clinica/prontuario/{{ p.id }}/comparar">
      {% for a in arquivos %}<div style="margin-top:.35rem">{% if a.tipo == 'foto' %}<label style="display:inline"><input type="checkbox" name="f" value="{{ a.id }}" style="width:auto"> </label>📷{% else %}📎{% endif %}
        <a href="/painel/clinica/prontuario/{{ p.id }}/arquivo/{{ a.id }}" target="_blank" rel="noopener">{{ a.quando.strftime('%d/%m/%Y') }}{% if a.regiao %} · {{ a.regiao }}{% endif %}{% if a.legenda %} · {{ a.legenda }}{% endif %}</a>
        <span class="pr-m">· {{ a.prof }}</span></div>{% endfor %}
      <div class="pr-acoes"><button class="sec">Comparar as duas fotos marcadas</button></div></form>
    {% else %}<div class="pr-m" style="margin-top:.3rem">Nenhuma foto ou anexo ainda.</div>{% endif %}
    {% if cofre %}
    <form method="post" action="/painel/clinica/prontuario/{{ p.id }}/arquivos" enctype="multipart/form-data" style="margin-top:.7rem">
      {% if ev %}<input type="hidden" name="evento" value="{{ ev.id }}">{% endif %}
      <div class="pr-3">
        <label>O quê<select name="tipo"><option value="foto">Foto clínica (câmera)</option><option value="anexo">Anexo (exame em PDF ou foto do papel)</option></select></label>
        <label>Região<input name="regiao" maxlength="80" placeholder="face, dorso, mão direita…"></label>
        <label>Legenda<input name="legenda" maxlength="200" placeholder="antes, 6ª semana, resultado do exame…"></label>
      </div>
      <label>Arquivo<input type="file" name="arquivo" id="arq" accept="image/*" capture="environment" required></label>
      <label style="display:flex;gap:.4rem;align-items:center" id="doc-cx" hidden><input type="checkbox" name="documento" value="1" style="width:auto"> É foto de documento ou exame (não do paciente)</label>
      <script>(function(){var s=document.querySelector('select[name=tipo]'),a=document.getElementById('arq'),d=document.getElementById('doc-cx');
      function ajusta(){if(s.value==='foto'){a.setAttribute('capture','environment');a.accept='image/*';d.hidden=true;}else{a.removeAttribute('capture');a.accept='image/*,application/pdf';d.hidden=false;}}
      s.addEventListener('change',ajusta);ajusta();})();</script>
      {% if imagem_op == 'nao' %}<div class="erro" style="margin-top:.4rem">O paciente não autorizou fotos (termo de imagem). Anexos continuam: PDF, ou foto de documento ou exame.</div>
      {% elif not imagem_op %}<label style="display:flex;gap:.4rem;align-items:center"><input type="checkbox" name="papel" value="1" style="width:auto"> O paciente autorizou as fotos em papel (anexe o termo)</label>{% endif %}
      <div class="pr-acoes"><button class="sec">Guardar</button></div>
      <div class="pr-m">No celular, a câmera abre direto e a foto não vai para a galeria. O Zaq tira a localização e os dados do aparelho, cifra e guarda: só quem pode ler o prontuário abre, e cada abertura fica registrada.</div>
    </form>{% else %}<div class="pr-m" style="margin-top:.5rem">O cofre das fotos ainda não está ligado nesta instalação.</div>{% endif %}
  </div>

  <div class="pr-cx" id="documentos"><b>Documentos</b> <span class="pr-m">· receita, atestado, pedido de exame, laudo…</span>
    {% if docs %}<form method="post" action="/painel/clinica/prontuario/{{ p.id }}/documentos/enviar">
    {% for d in docs %}<div style="margin-top:.35rem">
      {% if d.status == 'assinado' and d.tipo != 'notificacao' %}<input type="checkbox" name="doc" value="{{ d.id }}" style="width:auto">{% endif %}
      <b>{{ d.titulo }}</b> <span class="pr-m">· {{ d.criado_em.strftime('%d/%m/%Y') }}{% if d.tipo == 'notificacao' %} · talão nº {{ d.numero_talao }}{% endif %}</span>
      {% if d.status == 'rascunho' %}<span class="pr-tag y">rascunho</span> <a href="/painel/clinica/prontuario/{{ p.id }}/documento/{{ d.id }}">continuar</a>
      {% else %}{% if d.integro %}<span class="pr-tag g">emitido · íntegro</span>{% else %}<span class="pr-tag r">ALTERADO</span>{% endif %}
        {% if d.icp %}<span class="pr-tag g">certificado</span>{% elif d.icp_pendente %}<span class="pr-tag y">falta o certificado</span>{% endif %}
        {% if d.tipo != 'notificacao' %}<a href="/painel/clinica/prontuario/{{ p.id }}/documento/{{ d.id }}/pdf" target="_blank" rel="noopener">PDF</a>{% endif %}
        {% if d.enviado_em %}<span class="pr-m">· enviado {{ d.enviado_em.strftime('%d/%m %H:%M') }}</span>{% endif %}{% endif %}</div>{% endfor %}
      <div class="pr-acoes"><button class="sec" onclick="this.disabled=true;this.form.submit()">Mandar os marcados no WhatsApp (link com a data de nascimento)</button></div></form>{% endif %}
    <form method="post" action="/painel/clinica/prontuario/{{ p.id }}/documento/novo" class="pr-acoes">
      {% if ev %}<input type="hidden" name="evento" value="{{ ev.id }}">{% endif %}
      <select name="tipo" style="width:auto;margin:0">{% for k, t in TIPOS_DOC.items() %}<option value="{{ k }}">{{ t[0] }}</option>{% endfor %}</select>
      <button>Novo documento</button></form>
  </div>

  <div class="pr-cx"><b>Nova evolução</b>{% if ev %} <span class="pr-m">· do atendimento de {{ ev.hora }} ({{ ev.tipo }})</span>{% endif %}
    <form method="post" action="/painel/clinica/prontuario/{{ p.id }}/evolucao/nova" class="pr-acoes">
      {% if ev %}<input type="hidden" name="evento" value="{{ ev.id }}">{% endif %}
      <select name="modelo" style="width:auto;margin:0">{% for k, m in MODELOS.items() if k != 'adendo' %}<option value="{{ k }}">{{ m[0] }}</option>{% endfor %}</select>
      <button>Começar</button></form></div>

  <div class="pr-cx"><b>Atendimentos</b>
  {% for e in evos %}<div class="pr-evo">
    <h4>{{ e.quando.strftime('%d/%m/%Y') }}{% if e.tipo %} · {{ e.tipo }}{% endif %} · {{ e.prof }} <span class="pr-m">· {{ e.modelo_txt }}</span>
      {% if e.status == 'rascunho' %}<span class="pr-tag y">rascunho (só você vê)</span>{% elif e.integra %}<span class="pr-tag g">assinado · íntegro</span>{% else %}<span class="pr-tag r">assinado · ALTERADO NO BANCO</span>{% endif %}
      {% if e.icp %}<span class="pr-tag g">certificado</span> <a href="/painel/clinica/prontuario/{{ p.id }}/evolucao/{{ e.id }}/pdf" target="_blank" rel="noopener" class="pr-m">PDF assinado</a>{% elif e.icp_pendente %}<span class="pr-tag y">falta o certificado</span>{% endif %}</h4>
    {% for rot, txt in e.linhas %}<div style="margin-top:.2rem"><span class="pr-m">{{ rot }}:</span> <span class="pr-txt">{{ txt }}</span></div>{% endfor %}
    {% if e.cid %}<div class="pr-m">CID: {{ e.cid }}</div>{% endif %}{% if e.retorno_dias %}<div class="pr-m">Retorno em {{ e.retorno_dias }} dias</div>{% endif %}
    {% if e.status == 'assinado' %}<div class="pr-m">Assinado por {{ e.prof }} ({{ e.conselho }}) em {{ e.assinado_em.strftime('%d/%m/%Y %H:%M') }} · impressão {{ e.hash[:12] }}</div>{% endif %}
    {% for a in e.adendos %}<div class="pr-ad"><b>Adendo</b> de {{ a.prof }} em {{ a.assinado_em.strftime('%d/%m/%Y %H:%M') if a.assinado_em else '' }} {% if a.integra %}<span class="pr-tag g">íntegro</span>{% else %}<span class="pr-tag r">ALTERADO</span>{% endif %}{% if a.icp %} <span class="pr-tag g">certificado</span>{% elif a.icp_pendente %} <span class="pr-tag y">falta o certificado</span>{% endif %}
      <div class="pr-txt">{{ a.campos.texto }}</div></div>{% endfor %}
    <div class="pr-acoes">{% if e.meu_rascunho %}<a href="/painel/clinica/prontuario/{{ p.id }}/evolucao/{{ e.id }}">Continuar o rascunho</a>{% endif %}
      {% if e.status == 'assinado' %}<details><summary class="pr-m">Escrever adendo (a correção)</summary>
        <form method="post" action="/painel/clinica/prontuario/{{ p.id }}/evolucao/{{ e.id }}/adendo"><textarea name="texto" required></textarea><div class="pr-acoes"><button class="sec">Assinar o adendo</button></div></form></details>{% endif %}</div>
  </div>{% else %}<div class="pr-m" style="margin-top:.4rem">Nenhuma evolução ainda.</div>{% endfor %}
  </div>
</div>
{% endblock %}"""

_TPL_EVO = r"""{% extends "base" %}{% block conteudo %}""" + _CSS + r"""
<div class="pr-pag">
  <div class="pr-topo"><div><h2>Evolução · {{ p.nome_social or p.nome }}</h2>
    <div class="pr-m">{% if ev %}{{ ev.tipo }} de {{ ev.hora }} · {% endif %}{{ MODELOS[e.modelo][0] }} · <span id="salvo">rascunho{% if e.atualizado_em %} salvo às {{ e.atualizado_em.strftime('%H:%M:%S') }}{% endif %}</span></div></div>
    <div class="pr-acoes"><a href="/painel/clinica/prontuario/{{ p.id }}">‹ Prontuário</a></div></div>
  {% if erro %}<div class="erro" style="margin-top:.8rem">{{ erro }}</div>{% endif %}
  {% if fc.alergias %}<div class="pr-alerta">ALERGIA: {{ fc.alergias }}</div>{% endif %}
  <form class="pr-cx" id="evo" method="post" action="/painel/clinica/prontuario/{{ p.id }}/evolucao/{{ e.id }}/salvar">
    {% if not e.adendo_de %}<label>Modelo<select name="modelo" onchange="if(confirm('Trocar o modelo? O que já está escrito vai para o primeiro campo do novo.')){document.getElementById('tm').value='1';this.form.submit();}else{this.value='{{ e.modelo }}';}">{% for k, m in MODELOS.items() if k != 'adendo' %}<option value="{{ k }}" {% if k == e.modelo %}selected{% endif %}>{{ m[0] }}</option>{% endfor %}</select></label>{% endif %}
    <input type="hidden" name="trocar_modelo" id="tm" value="">
    {% for k, rot in MODELOS[e.modelo][1] %}<label>{{ rot }}<textarea name="{{ k }}">{{ e.campos.get(k, '') }}</textarea></label>{% endfor %}
    {% if e.modelo == 'dermatologia' %}<label>CID-10 (opcional)<input name="cid" maxlength="20" value="{{ e.cid }}" placeholder="L70.0"></label>{% endif %}
    <label>Retorno em quantos dias (opcional: vira o retorno da agenda)<input name="retorno_dias" inputmode="numeric" value="{{ e.retorno_dias or '' }}"></label>
    <div class="pr-acoes"><button class="sec">Salvar rascunho</button>
      <button formaction="/painel/clinica/prontuario/{{ p.id }}/evolucao/{{ e.id }}/assinar" onclick="return confirm('Assinar? Depois de assinada, a evolução não muda: a correção é um adendo.')">Assinar e finalizar</button>
      <button class="sec" formaction="/painel/clinica/prontuario/{{ p.id }}/evolucao/{{ e.id }}/descartar" formnovalidate onclick="return confirm('Descartar este rascunho? Ele ainda não é prontuário e some.')">Descartar rascunho</button></div>
    <div class="pr-m"><a href="/painel/clinica/prontuario/{{ p.id }}{% if ev %}?evento={{ ev.id }}{% endif %}#fotos">+ Foto ou anexo deste atendimento</a></div>
    <div class="pr-m">O rascunho se salva sozinho e só você vê. Assinada, fica com a hora do servidor, seu conselho e a impressão digital do texto.</div>
  </form>
</div>
<script>
(function(){var f=document.getElementById('evo'),mudou=false,s=document.getElementById('salvo');
f.addEventListener('input',function(){mudou=true;});
setInterval(function(){if(!mudou)return;mudou=false;var d=new FormData(f);d.append('auto','1');d.delete('trocar_modelo');
fetch(f.action,{method:'POST',body:d,credentials:'same-origin'}).then(function(r){return r.json();}).then(function(j){if(j.ok)s.textContent='rascunho salvo às '+j.salvo;else s.textContent='não salvou: '+(j.erro||'tente de novo');}).catch(function(){s.textContent='sem conexão: o rascunho não salvou';mudou=true;});},15000);})();
</script>
{% endblock %}"""

_TPL_COMPARAR = r"""{% extends "base" %}{% block conteudo %}""" + _CSS + r"""
<div class="pr-pag">
  <div class="pr-topo"><div><h2>Comparar · {{ p.nome_social or p.nome }}</h2></div>
    <div class="pr-acoes"><a href="/painel/clinica/prontuario/{{ p.id }}#fotos">‹ Prontuário</a></div></div>
  <div class="pr-3" style="margin-top:.8rem">{% for a in fotos %}<div class="pr-cx" style="margin:0">
    <b>{{ a.quando.strftime('%d/%m/%Y') }}</b>{% if a.regiao %} · {{ a.regiao }}{% endif %}{% if a.legenda %} · {{ a.legenda }}{% endif %}
    <img src="/painel/clinica/prontuario/{{ p.id }}/arquivo/{{ a.id }}" alt="foto clínica" style="width:100%;margin-top:.4rem;border-radius:8px"></div>{% endfor %}</div>
</div>
{% endblock %}"""

_TPL_DOC = r"""{% extends "base" %}{% block conteudo %}""" + _CSS + r"""
<div class="pr-pag">
  <div class="pr-topo"><div><h2>{{ d.titulo }} · {{ p.nome_social or p.nome }}</h2><div class="pr-m">rascunho · só você vê</div></div>
    <div class="pr-acoes"><a href="/painel/clinica/prontuario/{{ p.id }}#documentos">‹ Prontuário</a></div></div>
  {% if erro %}<div class="erro" style="margin-top:.8rem">{{ erro }}</div>{% endif %}
  <form class="pr-cx" method="post" action="/painel/clinica/prontuario/{{ p.id }}/documento/{{ d.id }}/salvar">
    <label>Título<input name="titulo" maxlength="120" value="{{ d.titulo }}"></label>
    {% if d.tipo == 'notificacao' %}<label>Número do talão (receita amarela ou azul)<input name="numero_talao" maxlength="40" value="{{ d.numero_talao }}"></label>
    <div class="pr-m">A notificação continua no talão de papel da vigilância: aqui fica só o registro.</div>{% endif %}
    <label>{{ 'Medicamento' if d.tipo == 'notificacao' else 'Texto' }}<textarea name="corpo" style="min-height:14rem">{{ d.corpo }}</textarea></label>
    <div class="pr-acoes"><button class="sec" name="acao" value="salvar">Salvar rascunho</button>
      <button name="acao" value="emitir" onclick="return confirm('Emitir? Depois de emitido, o documento não muda.')">Emitir</button></div>
    <div class="pr-m">Troque os ___ antes de emitir. Emitido, leva a hora, seu conselho e o código de conferência; sem certificado digital, o PDF sai pra imprimir e assinar.</div>
  </form>
</div>
{% endblock %}"""

_env.loader.mapping["clinica_prontuario.html"] = _TPL
_env.loader.mapping["clinica_documento.html"] = _TPL_DOC
_env.loader.mapping["clinica_comparar.html"] = _TPL_COMPARAR
_env.loader.mapping["clinica_evolucao.html"] = _TPL_EVO
