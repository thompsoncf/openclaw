"""Web do OpenClaw: pagina de status + webhook do WhatsApp (Twilio).

O Twilio entrega as mensagens do WhatsApp em POST /webhook/whatsapp.
A gente identifica o usuario pelo numero, usa o MESMO nucleo (agente
financeiro) e responde de volta pelo Twilio. Texto, foto e audio.

Variaveis de ambiente do WhatsApp:
    TWILIO_ACCOUNT_SID, TWILIO_AUTH_TOKEN, TWILIO_WHATSAPP_FROM (ex: whatsapp:+1415...)
"""
import base64
import logging
import os
import re as _re
import threading

import httpx
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, Response
from fastapi.middleware.cors import CORSMiddleware

from db.conexao import get_pool, init_schema
from contas import contas as ct
from contas.contas import URL_CADASTRO
from core.brain import Brain
from core.memory import MemoriaPersistente
from core.transcribe import transcritor_se_configurado
from finance.livro_caixa import LivroCaixa
from finance.lista_compras import ListaCompras
from finance.banco_precos import BancoPrecos
from finance.agente_financeiro import criar_agente_financeiro

logging.basicConfig(level=logging.INFO)
app = FastAPI(title="OpenClaw")
log = logging.getLogger("openclaw.web")

# Compressao gzip: HTML/JSON acima de 1KB saem comprimidos (~75% menor no 4G)
from starlette.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1024)

# Portal (cadastro/login/painel) + sessao assinada por cookie
from starlette.middleware.sessions import SessionMiddleware
from web.portal import router as portal_router
from web.admin import router as admin_router
from web.admin_precos import router as precos_router
from web.admin_orcamento import router as orcamento_router
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("PORTAL_SECRET", "troque-isto-em-producao"),
    same_site="lax",
    https_only=os.environ.get("PORTAL_COOKIE_SECURE", "1") == "1",
    max_age=60 * 60 * 24 * 7,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://zaq-landing.onrender.com"],
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type"],
)
app.include_router(portal_router)
app.include_router(admin_router)
app.include_router(precos_router)
app.include_router(orcamento_router)

_FAVICON_SVG = (
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64" fill="none">'
    '<rect width="64" height="64" rx="14" fill="#0f7d5c"/>'
    '<path d="M18 20 H42 L20 44 H44" stroke="#ffffff" stroke-width="6" '
    'stroke-linecap="round" stroke-linejoin="round" fill="none"/>'
    '<path d="M47 12 L49 17 L54 19 L49 21 L47 26 L45 21 L40 19 L45 17 Z" fill="#f48b22"/>'
    "</svg>"
)


@app.get("/favicon.ico", include_in_schema=False)
@app.get("/favicon.svg", include_in_schema=False)
def favicon():
    from fastapi.responses import Response
    return Response(content=_FAVICON_SVG, media_type="image/svg+xml",
                    headers={"Cache-Control": "public, max-age=604800"})


_pool = None
_brain = None
_transcritor = None

# dica curta anexada quando o cliente manda FOTO de cupom no WhatsApp:
DICA_QR_WPP = ("\n\n📸 Dica: deixe o QR code do cupom bem visível na foto — "
               "assim eu identifico a nota certinho e melhoro os preços pra você.")

# reconhece codigo de convite (ex: THO-4632)
_RE_CONVITE = _re.compile(r"\b([A-Z0-9]{2,4}-[A-F0-9]{4})\b")

# Detector de rajada (Camada 0): contador de mídia por número WhatsApp em janela ~30s
# Se mesmo cliente manda 2+ mídias rápido, avisa UMA VEZ na 2ª
_rajada_midia_wpp: dict[str, list[float]] = {}  # numero -> lista de timestamps (segundos)
_JANELA_RAJADA_SEG = 30


def _processar_rajada_midia_wpp(numero: str) -> bool:
    """Retorna True se é a 2ª+ mídia na janela (deve avisar 'Pode mandar todos')."""
    import time
    agora = time.time()
    if numero not in _rajada_midia_wpp:
        _rajada_midia_wpp[numero] = [agora]
        return False
    # limpa o histórico: mantem apenas eventos dentro da janela
    _rajada_midia_wpp[numero] = [t for t in _rajada_midia_wpp[numero]
                                 if agora - t < _JANELA_RAJADA_SEG]
    eh_rajada = len(_rajada_midia_wpp[numero]) >= 1  # ja tem 1+ evento: isso é a 2ª
    _rajada_midia_wpp[numero].append(agora)
    return eh_rajada


def _codigo_convite(texto: str) -> str | None:
    m = _RE_CONVITE.search((texto or "").strip().upper())
    return m.group(1) if m else None


def _setup():
    global _pool, _brain, _transcritor
    if _pool is None:
        _pool = get_pool()
        init_schema(_pool)
        _brain = Brain(model=os.environ.get("OPENCLAW_MODEL", "claude-sonnet-4-6"))
        _transcritor = transcritor_se_configurado()
    return _pool


def _eh_assinante_cesta(pool, conta_id: int) -> bool:
    """Verifica se a conta é um assinante de cesta (FASE 7)."""
    with pool.connection() as c:
        r = c.execute(
            "select eh_assinante_cesta from contas where id=%s", (conta_id,)
        ).fetchone()
    return bool(r and r[0])


def _agente_do(membro, conta):
    memoria = MemoriaPersistente(_pool, f"wa:{membro.id}")
    lista = ListaCompras(_pool, conta.id, membro.id)
    banco = BancoPrecos(_pool)
    cidade = getattr(conta, 'cidade', None)
    ag = criar_agente_financeiro(_brain, LivroCaixa(_pool, conta.id, membro.id), memoria, lista, membro.papel, banco, cidade)
    ag.canal_atual = "whatsapp"
    return ag


def _responder_whatsapp(to: str, texto: str):
    from twilio.rest import Client
    cli = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
    cli.messages.create(
        from_=os.environ["TWILIO_WHATSAPP_FROM"], to=to, body=texto or "(sem resposta)",
    )


def _baixar_midia(url: str) -> bytes:
    auth = (os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
    with httpx.Client(timeout=30) as c:
        r = c.get(url, auth=auth, follow_redirects=True)
        r.raise_for_status()
        return r.content


def _ler_qr_e_auditar_whatsapp(numero: str, dados: bytes, media_type: str) -> None:
    """Roda POR TRAS (thread separada): le o QR com calma (todas as tecnicas) e
    registra a auditoria pra enriquecer nosso banco de dados. NUNCA bloqueia o
    fluxo do cliente - a mensagem ja' foi processada normalmente. Tolerante a tudo."""
    chave = None
    try:
        from finance.nfce_qr import ler_chave
        chave = ler_chave(dados, media_type)
    except Exception:  # noqa: BLE001
        chave = None
    try:
        from finance.nfce_qr import registrar_leitura
        pool = _setup()
        achado = ct.membro_por_whatsapp(pool, numero)
        conta_id = achado[1].id if achado else None
        registrar_leitura(pool, conta_id, chave, media_type)
    except Exception:  # noqa: BLE001
        pass


def _ler_qr_whatsapp(numero: str, dados: bytes, media_type: str) -> None:
    """Roda numa thread: le o QR da NFC-e (com calma, todas as tecnicas) e
    registra a auditoria pra enriquecer nosso banco. NUNCA bloqueia a resposta
    ao cliente. Tolerante a qualquer falha."""
    chave = None
    try:
        from finance.nfce_qr import ler_chave
        chave = ler_chave(dados, media_type)
    except Exception:  # noqa: BLE001
        chave = None
    try:
        from finance.nfce_qr import registrar_leitura, medir_imagem
        achado = ct.membro_por_whatsapp(_setup(), numero)
        conta_id = achado[1].id if achado else None
        info = medir_imagem(dados, media_type)
        registrar_leitura(_setup(), conta_id, chave, media_type, info)
    except Exception:  # noqa: BLE001
        pass


def _disparar_qr_whatsapp(numero: str, dados: bytes, media_type: str) -> None:
    """Dispara a leitura do QR em thread separada (nao espera o resultado)."""
    try:
        threading.Thread(target=_ler_qr_whatsapp,
                         args=(numero, bytes(dados), media_type), daemon=True).start()
    except Exception:  # noqa: BLE001
        pass


def processar_whatsapp(numero: str, nome: str | None, body: str,
                       media_url: str | None, media_ctype: str):
    """Roda em background: identifica o MEMBRO, checa a CONTA, agente, responde."""
    pool = _setup()
    to = f"whatsapp:{numero}"
    try:
        achado = ct.membro_por_whatsapp(pool, numero)
        if achado is not None and _codigo_convite((body or "").strip()) == (body or "").strip().upper():
            # membro JA conectado mandou so' o codigo (1o acesso com o numero
            # certo): confirma em vez de mandar pra IA interpretar.
            _responder_whatsapp(to, "Voce ja esta conectado! – Manda um 'oi' que eu te ajudo 👋")
            return
        if achado is None:
            # talvez a pessoa tenha mandado um CODIGO de convite
            cod = _codigo_convite(body)
            if cod:
                ok, msg = ct.resgatar_convite_canal(pool, cod, "whatsapp", numero)
                _responder_whatsapp(to, msg + ("Manda um 'oi' que eu te ajudo!" if ok else ""))
                return
            # --- LEAD (cliente novo em test-drive) ---
            # cupom/foto é só pós-cadastro (protege custo ~5x)
            if media_url:
                _responder_whatsapp(to,
                    "📸 O leitor de cupom é exclusivo pra quem tem conta! "
                    "Cria a sua grátis e ganha 7 dias pra testar tudo: https://app.zaq-ia.com/cadastro")
                return
            est = ct.lead_estado(pool, "whatsapp", numero)
            if not est["pode_testar"]:
                _responder_whatsapp(to,
                    "Você já testou seus gastos grátis 😊 Curtiu? "
                    "Cria sua conta e ganha 7 dias grátis pra usar tudo "
                    "(incluindo leitura de cupom): https://app.zaq-ia.com/cadastro")
                return
            # sem texto = saudação inicial
            if not (body or "").strip():
                _responder_whatsapp(to,
                    "Opa! 👋 Eu sou o Zaq, seu assistente financeiro no WhatsApp. "
                    "Quer ver como funciona? Me diz um gasto, tipo *almoço 35* "
                    "ou *uber 22*, que eu organizo pra você. "
                    f"(Você tem {est['restantes']} testes grátis.)")
                return
            # processa o gasto de teste DE VERDADE
            from finance.degustacao import responder_degustacao
            resp = responder_degustacao(pool, _brain, body)
            novo = ct.lead_registrar_gasto(pool, "whatsapp", numero)
            extra = ""
            if novo >= ct.LEAD_LIMITE_GASTOS:
                extra = ("\n\n🎉 Curtiu? Esse foi seu último teste grátis. "
                         "Cria sua conta e ganha 7 dias grátis pra usar tudo, "
                         "incluindo a leitura automática de cupom: https://app.zaq-ia.com/cadastro")
            elif novo == ct.LEAD_LIMITE_GASTOS - 1:
                extra = "\n\n_(resta 1 teste grátis — depois é só criar a conta em https://app.zaq-ia.com/cadastro)_"
            _responder_whatsapp(to, resp + extra)
            return
        membro, conta = achado
        if not ct.acesso_liberado(conta):
            _responder_whatsapp(to, "Seu acesso esta suspenso (pagamento pendente). "
                                    "Assim que o pagamento for confirmado, voce volta a usar.")
            return

        # FASE 7: Assinante de cesta? Usar agente da cesta (Camada 1, read-only)
        if _eh_assinante_cesta(pool, conta.id):
            from finance.agente_cesta import criar_agente_cesta
            from core.memoria import MemoriaPersistente
            memoria = MemoriaPersistente(pool, f"cesta:{conta.id}")
            ag = criar_agente_cesta(pool, conta.id, _brain, memoria)
            resp = ag.responder(body)
            _responder_whatsapp(to, resp)
            return

        # check para comandos de pagamento
        cmd = (body or "").strip().lower()
        if cmd in ("assinar", "pagar", "upgrade", "plano", "assinar plano"):
            try:
                from finance.asaas import criar_link_pagamento
                with pool.connection() as c:
                    plano_row = c.execute(
                        "select nome, preco_base_centavos from planos where codigo=%s",
                        (conta.plano,),
                    ).fetchone()
                if plano_row:
                    valor_reais = plano_row[1] / 100.0
                    link_data = criar_link_pagamento(conta_id=conta.id, nome_plano=plano_row[0],
                                                     valor_reais=valor_reais)
                    _responder_whatsapp(to, f"Pra ativar seu plano {plano_row[0]}, é só pagar aqui 👇\n"
                                           f"{link_data['url']}\n\n"
                                           f"Aceita Pix, boleto ou cartão. Assim que cair, eu libero na hora! 🎉")
                else:
                    _responder_whatsapp(to, "Erro ao gerar link de pagamento. Tente novamente.")
            except Exception as e:
                log.error(f"Erro Asaas WhatsApp: {e}")
                _responder_whatsapp(to, "Erro ao gerar link de pagamento. Tente novamente.")
            return
        # tem_midia=True (foto/PDF) custa 5x; tipo (cupom vs comprovante) é decidido pelo agente
        tem_midia = bool(media_url)

        # Detector de rajada (Camada 0): se 2ª+ mídia rápido, avisa (UMA VEZ)
        if tem_midia and _processar_rajada_midia_wpp(numero):
            _responder_whatsapp(to, "Pode mandar todos! 👍 Vou registrando um a um e te confirmo cada um.")

        ok, _restante = ct.checar_e_registrar_uso(pool, conta, tem_midia=tem_midia)
        if not ok:
            _responder_whatsapp(to,
                "Voce atingiu o limite de CUPONS de hoje 📷. Pode seguir mandando texto!"
                if tem_midia else
                "Voce atingiu o limite de mensagens de hoje. A gente se fala amanha!")
            return

        texto = body or ""
        imagem_b64 = None
        media_type = "image/jpeg"
        dica_qr = False
        chave_nfce = None
        if media_url:
            dados = _baixar_midia(media_url)
            ctype = (media_ctype or "")
            if ctype.startswith("image/"):
                imagem_b64 = base64.b64encode(dados).decode("ascii")
                media_type = ctype
                texto = body or "Segue o cupom para registrar."
                _disparar_qr_whatsapp(numero, dados, ctype)
                dica_qr = True
                # TRAVA DUPLICIDADE + REPLICA: ler chave de forma síncrona ANTES do agente
                try:
                    from finance.nfce_qr import ler_chave_da_imagem
                    chave_nfce = ler_chave_da_imagem(dados)
                    if chave_nfce:
                        from finance.livro_caixa import LivroCaixa
                        liv = LivroCaixa(pool, conta.id, membro.id)
                        dup = liv.lancamento_por_chave(chave_nfce, global_=True)
                        if dup:
                            if dup["conta_id"] == conta.id:
                                # mesma conta -> ja' registrado
                                data_str = dup["data"].strftime("%d/%m/%Y")
                                desc = dup["descricao"][:50]
                                _responder_whatsapp(to,
                                    f"✓ Esse cupom ja foi registrado em {data_str} ({desc}...)")
                                return
                            # outra conta -> tenta replicar (zero API)
                            rep = liv.replicar_cupom(chave_nfce)
                            if rep:
                                _responder_whatsapp(to,
                                    f"✓ Esse cupom ja foi lido no sistema. Salvei uma copia na sua "
                                    f"conta: {rep['descricao']} - R$ {rep['valor']/100:.2f} "
                                    f"({rep['n_itens']} itens). Nao precisei reler a foto.")
                                return
                            # dup existe mas SEM itens (parse falhou) -> cai pro fluxo normal
                except Exception:  # noqa: BLE001
                    pass  # trava falha gracefully, segue o fluxo normal
            elif "pdf" in ctype:
                imagem_b64 = base64.b64encode(dados).decode("ascii")
                media_type = "application/pdf"
                texto = body or "Segue o comprovante para registrar."
                _disparar_qr_whatsapp(numero, dados, "application/pdf")
                # TRAVA DUPLICIDADE + REPLICA: mesmo pra PDF
                try:
                    from finance.nfce_qr import ler_chave_da_imagem
                    chave_nfce = ler_chave_da_imagem(dados)
                    if chave_nfce:
                        from finance.livro_caixa import LivroCaixa
                        liv = LivroCaixa(pool, conta.id, membro.id)
                        dup = liv.lancamento_por_chave(chave_nfce, global_=True)
                        if dup:
                            if dup["conta_id"] == conta.id:
                                data_str = dup["data"].strftime("%d/%m/%Y")
                                desc = dup["descricao"][:50]
                                _responder_whatsapp(to,
                                    f"✓ Esse cupom ja foi registrado em {data_str} ({desc}...)")
                                return
                            rep = liv.replicar_cupom(chave_nfce)
                            if rep:
                                _responder_whatsapp(to,
                                    f"✓ Esse cupom ja foi lido no sistema. Salvei uma copia na sua "
                                    f"conta: {rep['descricao']} - R$ {rep['valor']/100:.2f} "
                                    f"({rep['n_itens']} itens). Nao precisei reler a foto.")
                                return
                except Exception:  # noqa: BLE001
                    pass
            elif ctype.startswith("audio/") and _transcritor:
                texto = _transcritor.transcrever(dados, "audio.ogg")

        agente = _agente_do(membro, conta)
        # Se lemos uma chave, passa pro livro pra que tools a usem ao gravar lançamento
        if chave_nfce:
            agente.livro.chave_nfce_atual = chave_nfce
        resposta = agente.responder(texto, imagem_b64, media_type)
        if dica_qr:
            resposta = (resposta or "") + DICA_QR_WPP
        _responder_whatsapp(to, resposta)
    except Exception as e:  # noqa: BLE001
        log.exception("erro no whatsapp")
        try:
            _responder_whatsapp(to, f"Ops, deu um erro aqui: {e}")
        except Exception:
            pass


def _normalizar_br(numero: str) -> str:
    """Corrige o 'nono digito' do WhatsApp BR.

    No Brasil, o numero pode chegar SEM o 9 (formato antigo, 12 digitos:
    55 + DDD + 8 digitos). Pra ENVIAR de volta, o WhatsApp exige o 9.
    Ex: +5586 81885930  ->  +5586 9 81885930
    """
    n = numero.replace("whatsapp:", "").strip()
    digitos = n.lstrip("+")
    if digitos.startswith("55") and len(digitos) == 12:
        digitos = digitos[:4] + "9" + digitos[4:]
    return "+" + digitos


@app.post("/webhook/asaas")
async def webhook_asaas(request: Request):
    """Webhook do Asaas: ativa CESTA (externalReference 'cesta:<id>') ou CONTA do app
    (externalReference '<conta_id>'). Parsing robusto (form-urlencoded OU json),
    confirmação na API e idempotência. Sempre responde 200 pra não penalizar no Asaas.
    """
    import logging
    _logw = logging.getLogger("openclaw.asaas")
    # VALIDAÇÃO: só rejeita se há token configurado E ele não bate
    _tok_recv = (request.headers.get("asaas-access-token", "") or "").strip()
    _tok_esp = (os.environ.get("ASAAS_WEBHOOK_TOKEN", "") or "").strip()
    if _tok_esp and _tok_recv != _tok_esp:
        _logw.warning("ASAAS token invalido (configurado mas nao bate) — ignorando webhook")
        return Response(status_code=200)

    # corpo pode vir VAZIO (ping/teste) -> responde 200
    try:
        _raw = await request.body()
        if not _raw or not _raw.strip():
            return Response(status_code=200)
        _ct = (request.headers.get("content-type", "") or "").lower()
        import json as _json
        if "application/x-www-form-urlencoded" in _ct:
            from urllib.parse import parse_qs
            _campos = parse_qs(_raw.decode("utf-8", "replace"))
            _payload = (_campos.get("data") or _campos.get("payload") or [""])[0]
            if not _payload.strip():
                return Response(status_code=200)
            body = _json.loads(_payload)
        else:
            body = _json.loads(_raw.decode("utf-8-sig"))
    except Exception as _e:  # noqa: BLE001
        _logw.warning("ASAAS webhook corpo invalido (%s) - respondendo 200", _e)
        return Response(status_code=200)

    evento = body.get("event", "")
    pay = body.get("payment", {}) or {}
    ref = pay.get("externalReference")
    if not ref:
        return Response(status_code=200)
    ref = str(ref)

    # prefixos: "cesta:<id>" = assinatura de cesta; "pedido:<id>" = pedido da loja;
    # senão = conta do app
    if ref.startswith("pedido:"):
        if evento in ("PAYMENT_RECEIVED", "PAYMENT_CONFIRMED"):
            try:
                from finance import carrinho as _car
                _car.marcar_pago(_setup(), int(ref.split(":", 1)[1]))
                _logw.warning("ASAAS pedido pago: %s", ref)
            except Exception as _e:  # noqa: BLE001
                _logw.warning("ASAAS pedido %s erro: %s", ref, _e)
        return Response(status_code=200)
    is_cesta = ref.startswith("cesta:")
    assinatura_id = conta_id = None
    try:
        if is_cesta:
            assinatura_id = int(ref.split(":", 1)[1])
        else:
            conta_id = int(ref)
    except (TypeError, ValueError, IndexError):
        _logw.warning("ASAAS externalReference invalido: %r", ref)
        return Response(status_code=200)

    _logw.warning("ASAAS >> evento=%s pay=%s ref=%s valor=%s",
                  evento, pay.get("id", ""), ref, pay.get("value", ""))

    try:
        pool = _setup()
        chave_idem = f"asaas:{pay.get('id','')}:{evento}"
        if not ct.reivindicar_mensagem(pool, chave_idem):
            return Response(status_code=200)

        if evento in ("PAYMENT_RECEIVED", "PAYMENT_CONFIRMED"):
            # HARDENING: confirma o status direto na API (protege contra webhook forjado)
            from starlette.concurrency import run_in_threadpool
            from finance import asaas
            try:
                api_pay = await run_in_threadpool(asaas.consultar_pagamento, pay.get("id", ""))
                if api_pay.get("status") not in ("RECEIVED", "CONFIRMED"):
                    _logw.warning("ASAAS pagamento %s status=%s — ignorando",
                                  pay.get("id", ""), api_pay.get("status"))
                    return Response(status_code=200)
            except asaas.AsaasErro as _e:
                _logw.warning("ASAAS nao confirmou pagamento %s (%s) — nao ativando",
                              pay.get("id", ""), _e)
                return Response(status_code=200)

            if is_cesta:
                from finance import assinaturas as assin_mod
                assin_mod.ativar_pos_pagamento(pool, assinatura_id)
                # cliente_id da assinatura, pra registrar o evento sem FK nula
                cli_id = None
                with pool.connection() as c:
                    row = c.execute("select cliente_id from assinaturas where id=%s",
                                    (assinatura_id,)).fetchone()
                    cli_id = row[0] if row else None
                ct.registrar_evento(pool, cli_id, "assinatura_ativada_asaas",
                                    f"assinatura_id={assinatura_id} valor {pay.get('value','')}")
                _logw.warning("ASAAS cesta assinatura %s ATIVADA (pay %s)",
                              assinatura_id, pay.get("id", ""))
            else:
                # ativa a conta do app (lê o plano gravado no banco, não o description do Asaas)
                with pool.connection() as c:
                    row = c.execute("select plano from contas where id=%s", (conta_id,)).fetchone()
                    plano_atual = row[0] if row else None
                ct.ativar(pool, conta_id, dias=30, plano=plano_atual)
                ct.registrar_evento(pool, conta_id, "pagamento_confirmado",
                                    f"asaas {pay.get('id','')} valor {pay.get('value','')}")
                _logw.warning("ASAAS conta %s ATIVADA (pagamento %s)", conta_id, pay.get("id", ""))

        elif evento == "PAYMENT_OVERDUE":
            if not is_cesta:
                ct.marcar_inadimplente(pool, conta_id)
        elif evento in ("PAYMENT_REFUNDED", "PAYMENT_DELETED", "PAYMENT_CHARGEBACK"):
            if not is_cesta:
                ct.suspender(pool, conta_id, motivo=f"asaas {evento}")
    except Exception as _e:  # noqa: BLE001
        import traceback
        _logw.error("ASAAS erro ao processar evento %s ref %s: %s\n%s",
                    evento, ref, _e, traceback.format_exc())
        return Response(status_code=200)

    return Response(status_code=200)


@app.post("/webhook/whatsapp")
async def whatsapp(request: Request, background: BackgroundTasks):
    form = await request.form()
    numero = _normalizar_br(form.get("From", "") or "")
    nome = form.get("ProfileName") or None
    body = form.get("Body", "") or ""
    media_url = None
    media_ctype = ""
    if int(form.get("NumMedia", "0") or 0) > 0:
        media_url = form.get("MediaUrl0")
        media_ctype = form.get("MediaContentType0", "")
    # nunca responder pro proprio numero do bot (evita From==To)
    bot = os.environ.get("TWILIO_WHATSAPP_FROM", "").replace("whatsapp:", "").lstrip("+")
    if numero and numero.lstrip("+") != bot:
        # Idempotencia: reivindicar a mensagem ANTES de processar (evita reentrega/duplicacao)
        msg_sid = form.get("MessageSid") or ""
        if ct.reivindicar_mensagem(_setup(), f"wa:{msg_sid}"):
            background.add_task(processar_whatsapp, numero, nome, body, media_url, media_ctype)
    # responde rapido (200) pra nao estourar o timeout do Twilio
    return Response(content="<Response></Response>", media_type="application/xml")


@app.get("/", response_class=HTMLResponse)
def home():
    return _PAGINA


@app.get("/health")
def health():
    try:
        with get_pool().connection() as conn:
            conn.execute("select 1")
        return {"status": "ok", "db": "ok"}
    except Exception as e:  # noqa: BLE001
        return JSONResponse({"status": "degraded", "db": str(e)}, status_code=503)


@app.get("/pesquisa/{segmento}", response_class=HTMLResponse)
def pesquisa_form(segmento: str):
    """Formulário de pesquisa para fornecedores (Zaq Fornecedor fase 1)."""
    return _HTML_PESQUISA


@app.post("/api/pesquisa-fornecedor")
async def pesquisa_salvar(request: Request):
    """Salva respostas da pesquisa de fornecedor (anônima, jsonb)."""
    try:
        dados = await request.json()
        seg = (dados.get("segmento") or "hortifruti")[:40]
        resp = dados.get("respostas") or {}
        import json as _json
        pool = _setup()
        with pool.connection() as c:
            c.execute("insert into pesquisa_fornecedor (segmento, respostas) values (%s, %s)",
                      (seg, _json.dumps(resp)))
            c.commit()
        return JSONResponse({"ok": True})
    except Exception as e:  # noqa: BLE001
        log.error("erro ao salvar pesquisa: %s", e)
        return JSONResponse({"erro": str(e)}, status_code=400)


_PAGINA = """<!doctype html>
<html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zaq</title>
<style>
  :root { color-scheme: light dark; }
  body { margin:0; min-height:100vh; display:flex; align-items:center;
    justify-content:center; font-family: system-ui, -apple-system, sans-serif;
    background:#0e0e0f; color:#ececec; }
  .card { max-width:440px; padding:2.5rem 2rem; text-align:center; }
  h1 { font-size:1.6rem; font-weight:500; margin:0 0 .5rem; }
  p { color:#a8a8a3; line-height:1.6; margin:.4rem 0; }
  .dot { display:inline-block; width:9px; height:9px; border-radius:50%;
    background:#1d9e75; margin-right:7px; vertical-align:middle; }
  .status { font-size:.95rem; color:#5dcaa5; margin-top:1rem; }
  .soon { margin-top:1.8rem; font-size:.85rem; color:#737370;
    border-top:1px solid #2a2a2b; padding-top:1.2rem; }
</style></head>
<body><div class="card">
  <h1>Zaq</h1>
  <p>Seu assistente financeiro pessoal.</p>
  <p class="status"><span class="dot"></span>no ar</p>
  <p class="soon">Fale com o assistente no Telegram ou no WhatsApp.<br><br>
  <a href="/cadastro" style="color:#5dcaa5">Criar conta</a> &nbsp;·&nbsp;
  <a href="/login" style="color:#5dcaa5">Entrar</a></p>
</div></body></html>"""


_HTML_PESQUISA = """<!doctype html>
<html lang="pt-BR">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Zaq — Como funciona sua distribuidora</title>
<style>
  :root{ --verde:#1d9e75; --verde2:#5dcaa5; --bg:#0f0f10; --card:#161617; --bord:#2a2a2b; --txt:#ececec; --mut:#a8a8a3; }
  *{ box-sizing:border-box; }
  body{ margin:0; background:var(--bg); color:var(--txt); font-family:-apple-system,Segoe UI,Roboto,sans-serif; line-height:1.5; }
  .wrap{ max-width:640px; margin:0 auto; padding:1.5rem 1.1rem 4rem; }
  .logo{ font-size:1.6rem; font-weight:700; color:var(--verde2); letter-spacing:-.5px; }
  .sub{ color:var(--mut); font-size:.95rem; margin:.3rem 0 1.6rem; }
  .q{ background:var(--card); border:1px solid var(--bord); border-radius:14px; padding:1.1rem 1.1rem .9rem; margin-bottom:1rem; }
  .q h3{ font-size:1rem; font-weight:600; margin:0 0 .8rem; }
  .q .hint{ font-size:.8rem; color:var(--mut); font-weight:400; }
  .opt{ display:flex; align-items:center; gap:.7rem; padding:.6rem .7rem; border:1px solid var(--bord); border-radius:10px; margin-bottom:.45rem; cursor:pointer; transition:background .15s, border-color .15s; }
  .opt:hover{ background:#1d1d1f; }
  .opt input{ width:18px; height:18px; accent-color:var(--verde); flex-shrink:0; }
  .opt.sel{ border-color:var(--verde); background:rgba(29,158,117,.08); }
  .opt span{ font-size:.92rem; }
  textarea{ width:100%; background:#0f0f10; border:1px solid var(--bord); border-radius:10px; color:var(--txt); padding:.7rem; font-size:.92rem; font-family:inherit; resize:vertical; min-height:70px; }
  .btn{ width:100%; background:var(--verde); color:#fff; border:none; padding:.95rem; border-radius:12px; font-size:1.05rem; font-weight:600; cursor:pointer; margin-top:1rem; }
  .btn:active{ transform:scale(.99); }
  .btn:disabled{ opacity:.5; }
  .ok{ text-align:center; padding:3rem 1rem; }
  .ok .big{ font-size:3rem; }
  .ok h2{ color:var(--verde2); }
  .prog{ position:sticky; top:0; background:var(--bg); padding:.6rem 0; margin:-1.5rem 0 1rem; z-index:5; }
  .prog .bar{ height:5px; background:var(--bord); border-radius:3px; overflow:hidden; }
  .prog .fill{ height:100%; width:0; background:var(--verde); transition:width .3s; }
  .prog .txt{ font-size:.78rem; color:var(--mut); margin-top:.35rem; }
</style>
</head>
<body>
<div class="wrap" id="wrap">

  <div class="logo">Zaq</div>
  <div class="sub">Poucas perguntas rápidas pra entender como sua distribuidora funciona. É só marcar — leva 3 minutos. 🚀</div>

  <div class="prog"><div class="bar"><div class="fill" id="fill"></div></div><div class="txt" id="ptxt">0 de 11 respondidas</div></div>

  <form id="form">
    <!-- as perguntas são injetadas pelo JS -->
  </form>

  <button class="btn" id="enviar" disabled>Responder depois (preencha pra enviar)</button>
</div>

<div class="wrap ok" id="obrigado" style="display:none">
  <div class="big">✓</div>
  <h2>Recebido, valeu!</h2>
  <p class="sub">Suas respostas chegaram. Em breve a gente conversa sobre como o Zaq pode ajudar sua distribuidora.</p>
</div>

<script>
var SEGMENTO = "hortifruti";

var PERGUNTAS = [
  {id:"q1", tipo:"radio", t:"Quantos produtos diferentes você vende?",
   opts:["Até 30","30 a 100","100 a 300","Mais de 300"]},
  {id:"q2", tipo:"radio", t:"Como você controla seus produtos hoje?",
   opts:["Na cabeça / de memória","Caderno ou papel","Planilha (Excel/Google)","Algum sistema/app","WhatsApp"]},
  {id:"q3", tipo:"radio", t:"Com que frequência seus preços mudam?",
   opts:["Todo dia","Toda semana","De vez em quando","Preço fixo, quase não muda"]},
  {id:"q4", tipo:"check", t:"Você vende seus produtos por:", hint:"(pode marcar vários)",
   opts:["Quilo (kg)","Caixa","Dúzia","Unidade","Sacola / saco"]},
  {id:"q5", tipo:"check", t:"Como seus clientes fazem o pedido hoje?", hint:"(pode marcar vários)",
   opts:["Ligação (telefone)","WhatsApp","Pessoalmente","Você que passa e oferece"]},
  {id:"q6", tipo:"radio", t:"Quantos pedidos você recebe por dia?",
   opts:["Até 10","10 a 30","30 a 50","Mais de 50"]},
  {id:"q7", tipo:"check", t:"O que mais te dá trabalho nos pedidos hoje?", hint:"(pode marcar vários)",
   opts:["Anotar tudo certo","Cliente esquece itens","Preço desatualizado","Organizar a rota de entrega","Confusão de quem pediu o quê"]},
  {id:"q8", tipo:"radio", t:"Como funciona sua entrega?",
   opts:["Dias fixos por bairro","Todo dia, conforme o pedido","Só quando junta bastante pedido","Outro"]},
  {id:"q9", tipo:"radio", t:"Se seu cliente montasse o pedido sozinho, com seus preços, e caísse pronto e organizado no seu WhatsApp — quanto valeria por mês pra você?",
   opts:["Não pagaria","Até R$ 50/mês","R$ 50 a R$ 150/mês","R$ 150 a R$ 300/mês","Mais de R$ 300/mês"]},
  {id:"q10", tipo:"radio", t:"Na sua opinião, quem deveria pagar por esse sistema?",
   opts:["Eu (o fornecedor)","O cliente que faz o pedido","Os dois dividem","Não sei"]},
  {id:"q11", tipo:"texto", t:"Quer comentar mais alguma coisa?", hint:"(opcional)"},
];

var respostas = {};
var formEl = document.getElementById('form');

PERGUNTAS.forEach(function(p){
  var div = document.createElement('div');
  div.className = 'q';
  var html = '<h3>'+p.t+(p.hint?' <span class="hint">'+p.hint+'</span>':'')+'</h3>';
  if (p.tipo === 'texto'){
    html += '<textarea data-q="'+p.id+'" placeholder="Escreva aqui (opcional)"></textarea>';
  } else {
    p.opts.forEach(function(o){
      var inputType = p.tipo === 'check' ? 'checkbox' : 'radio';
      html += '<label class="opt" data-q="'+p.id+'" data-o="'+o+'">'
            + '<input type="'+inputType+'" name="'+p.id+'" value="'+o+'">'
            + '<span>'+o+'</span></label>';
    });
  }
  div.innerHTML = html;
  formEl.appendChild(div);
});

formEl.addEventListener('change', function(e){
  var lbl = e.target.closest('.opt');
  if (lbl){
    var q = lbl.getAttribute('data-q');
    var p = PERGUNTAS.find(function(x){return x.id===q;});
    if (p.tipo === 'radio'){
      Array.prototype.forEach.call(formEl.querySelectorAll('.opt[data-q="'+q+'"]'), function(l){ l.classList.remove('sel'); });
      lbl.classList.add('sel');
      respostas[q] = lbl.getAttribute('data-o');
    } else {
      lbl.classList.toggle('sel', e.target.checked);
      respostas[q] = respostas[q] || [];
      var v = lbl.getAttribute('data-o');
      if (e.target.checked){ if (respostas[q].indexOf(v)===-1) respostas[q].push(v); }
      else { respostas[q] = respostas[q].filter(function(x){return x!==v;}); }
    }
    atualizar();
  }
});
formEl.addEventListener('input', function(e){
  if (e.target.tagName === 'TEXTAREA'){
    respostas[e.target.getAttribute('data-q')] = e.target.value;
  }
});

function atualizar(){
  var obrig = PERGUNTAS.filter(function(p){return p.id!=='q11';});
  var feitas = obrig.filter(function(p){
    var r = respostas[p.id];
    return r && (Array.isArray(r) ? r.length>0 : true);
  }).length;
  var pct = Math.round(feitas / obrig.length * 100);
  document.getElementById('fill').style.width = pct + '%';
  document.getElementById('ptxt').textContent = feitas + ' de ' + obrig.length + ' respondidas';
  var btn = document.getElementById('enviar');
  if (feitas === obrig.length){
    btn.disabled = false; btn.textContent = 'Enviar respostas';
  } else {
    btn.disabled = true; btn.textContent = 'Preencha tudo pra enviar ('+(obrig.length-feitas)+' faltando)';
  }
}

document.getElementById('enviar').onclick = function(){
  this.disabled = true; this.textContent = 'Enviando...';
  fetch('/api/pesquisa-fornecedor', {
    method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ segmento: SEGMENTO, respostas: respostas })
  }).then(function(r){
    if (!r.ok) throw new Error('falha');
    document.getElementById('wrap').style.display = 'none';
    document.getElementById('obrigado').style.display = 'block';
    window.scrollTo(0,0);
  }).catch(function(){
    var b = document.getElementById('enviar');
    b.disabled = false; b.textContent = 'Erro ao enviar — tente de novo';
  });
};
</script>
</body>
</html>"""
