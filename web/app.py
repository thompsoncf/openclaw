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


# Gate de permissão por papel do membro logado. IMPORTANTE: registrado ANTES do
# SessionMiddleware pra ficar INTERNO a ele — assim request.session já está
# carregada quando o gate roda. O titular (dono) e visitantes passam direto;
# só barra membro de equipe fora do que o papel dele acessa (defesa central,
# além de esconder do menu).
from contas.equipe import caps_do_papel as _caps_do_papel


@app.middleware("http")
async def _gate_permissoes(request: Request, call_next):
    try:
        papel = request.session.get("papel")
    except Exception:  # rota sem sessão
        papel = None
    if papel and papel != "dono":
        # MEMBRO de equipe: whitelist. Só acessa a(s) área(s) do papel dele — nunca
        # o /painel do dono (Pessoas da conta, plano) nem áreas de outro papel.
        caps = _caps_do_papel(papel)
        p = request.url.path
        home = ("/painel/servicos" if caps["vendas"]
                else "/painel/empresa" if caps["financeiro"] else "/trocar")
        permitido = ["/trocar", "/sair"]
        if caps["vendas"]:
            permitido.append("/painel/servicos")
            permitido.append("/painel/prospeccao")
        if caps["financeiro"]:
            permitido.append("/painel/empresa")   # financeiro da EMPRESA (não o pessoal do dono)
            permitido.append("/painel/relatorios")
        if caps["gerir"]:
            permitido += ["/painel/equipe", "/membros"]
        # só barra rotas do painel/membros; público, loja e webhooks passam livres.
        guardado = p == "/painel" or p.startswith("/painel/") or p.startswith("/membros")
        if guardado and not any(p == a or p.startswith(a + "/") for a in permitido):
            from fastapi.responses import RedirectResponse as _RR
            return _RR(home if p != home else "/trocar", status_code=303)
    return await call_next(request)


# Portal (cadastro/login/painel) + sessao assinada por cookie
from starlette.middleware.sessions import SessionMiddleware
from web.portal import router as portal_router
from web.admin import router as admin_router
from web.admin_precos import router as precos_router
from web.painel_servicos import router as servicos_router
from web.painel_equipe import router as equipe_router
from web.painel_prospeccao import router as prospeccao_router
from web.painel_conteudo import router as conteudo_router
from web.painel_agenda import router as agenda_router
from web.painel_cockpit import router as cockpit_router
from web.painel_relatorios import router as relatorios_router
from web.proposta import router as proposta_router
app.add_middleware(
    SessionMiddleware,
    secret_key=os.environ.get("PORTAL_SECRET", "troque-isto-em-producao"),
    same_site="lax",
    https_only=os.environ.get("PORTAL_COOKIE_SECURE", "1") == "1",
    max_age=60 * 60 * 24 * 7,
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["https://zaq-landing.onrender.com", "https://zaq-ia.com"],
    allow_methods=["POST", "GET"],
    allow_headers=["Content-Type"],
)
app.include_router(portal_router)
app.include_router(admin_router)
app.include_router(precos_router)
app.include_router(servicos_router)
app.include_router(equipe_router)
app.include_router(conteudo_router)  # antes do prospeccao_router: aquele tem um
                                      # catch-all GET /painel/prospeccao/{alvo_id}
                                      # (ficha do lead) que engoliria /ia-insta*
                                      # se viesse primeiro (mesmo formato de 1 segmento).
app.include_router(prospeccao_router)
app.include_router(agenda_router)
app.include_router(cockpit_router)
app.include_router(relatorios_router)
app.include_router(proposta_router)


@app.on_event("startup")
def _iniciar_poller_email() -> None:
    """Poller de e-mail recebido (IMAP) + motor de campanhas: a cada ~2min puxa o
    que chegou na caixa e dispara o que tá pendente. Só o poller de e-mail
    depende de credencial (SMTP/IMAP); campanhas rodam mesmo sem. Com 2 workers,
    o advisory lock de cada canal (_LOCK/_LOCK_WA/_LOCK_REENG) garante que só um
    processa por vez.

    AUTORRECUPERÁVEL: o setup (pool/import) fica DENTRO do loop, então um
    soluço passageiro no boot (ex.: Postgres ainda não aceita conexão logo
    depois de um restart do Render) não mata a thread pra sempre — antes disso
    acontecia, e o motor de campanhas ficava mudo até o PRÓXIMO restart manual,
    mesmo com tudo saudável de novo minutos depois.

    LOG DE DIAGNÓSTICO (pra descobrir se/onde trava num restart): loga quando o
    evento de startup dispara, quando a thread é criada, quando ela de fato
    começa a rodar (prova que o SO rodou o código, não só que .start() não
    lançou exceção) e, a cada ciclo, quantos itens enviar_pendentes() processou
    — 0 repetido com leads elegíveis é pista de outro bug; nenhuma linha de
    ciclo nenhuma é pista de que a thread não chegou a rodar."""
    log.info("startup: registrando thread do poller (e-mail + motor de campanhas)")

    def _loop():
        import time as _time
        log.info("poller: thread iniciada — 1º ciclo em ~2min")
        pool = None
        _ein = _cm = None
        ciclo = 0
        while True:
            try:
                _time.sleep(120)          # sleep-first: não roda em app efêmero (testes)
                ciclo += 1
                if pool is None:
                    pool = _setup()
                if _ein is None:
                    from finance import email_inbound as _ein
                if _cm is None:
                    from finance import campanhas_motor as _cm
            except Exception as e:  # noqa: BLE001 — setup falhou: loga e tenta de novo no próximo ciclo
                log.warning("poller: ciclo #%d — setup falhou (%s: %s) — tenta de novo em 2min",
                           ciclo, type(e).__name__, e)
                continue
            log.info("poller: ciclo #%d iniciado", ciclo)
            try:
                _ein.poll_uma_vez(pool)          # recebe e-mail
            except Exception as e:  # noqa: BLE001
                log.info("poller: ciclo #%d — e-mail falhou: %s: %s", ciclo, type(e).__name__, e)
            n_camp = 0
            try:
                n_camp = _cm.enviar_pendentes(pool)   # dispara campanhas ativas
                log.info("poller: ciclo #%d — campanhas: %d processado(s)", ciclo, n_camp)
            except Exception as e:  # noqa: BLE001
                log.info("poller: ciclo #%d — campanhas falhou: %s: %s", ciclo, type(e).__name__, e)
            try:
                # sinal de vida do motor pra Campanhas mostrar "ativo/parado" na
                # tela (config_app.atualizado_em vira o "último ciclo há Xmin") —
                # grava mesmo se enviar_pendentes falhou (n_camp fica 0): o que
                # importa aqui é que o CICLO rodou, não que tenha achado o que
                # mandar.
                from finance import config_app as _cfgapp
                _cfgapp.set_config(pool, "prospec_motor_ultimo_ciclo", str(n_camp))
            except Exception as e:  # noqa: BLE001
                log.info("poller: ciclo #%d — sinal de vida falhou: %s: %s", ciclo, type(e).__name__, e)
            try:
                _cm.renovar_tokens_ig(pool)      # renova token do Instagram (60 dias)
            except Exception as e:  # noqa: BLE001
                log.info("poller: ciclo #%d — renovar_tokens_ig falhou: %s: %s", ciclo, type(e).__name__, e)
            try:
                from web.painel_conteudo import publicar_pendentes as _ia_insta
                n_post = _ia_insta(pool)         # IA Insta: publica agendados vencidos
                if n_post:
                    log.info("poller: ciclo #%d — IA Insta: %d post(s) publicado(s)", ciclo, n_post)
            except Exception as e:  # noqa: BLE001
                log.info("poller: ciclo #%d — IA Insta falhou: %s: %s", ciclo, type(e).__name__, e)
            try:
                from finance import lembretes as _lb
                _lb.rodar(pool)                  # resumo do dia + aviso antes (agenda)
            except Exception as e:  # noqa: BLE001
                log.info("poller: ciclo #%d — lembretes falhou: %s: %s", ciclo, type(e).__name__, e)

    try:
        threading.Thread(target=_loop, daemon=True, name="email-poller").start()
        log.info("startup: thread do poller criada")
    except Exception as e:  # noqa: BLE001 — não deveria falhar, mas se falhar loga em vez de sumir
        log.error("startup: falha ao criar a thread do poller: %s: %s", type(e).__name__, e)


# ---------------------------------------------------------------------------
# TELEGRAM POR WEBHOOK (unifica os servidores)
# ---------------------------------------------------------------------------
# O bot do Telegram historicamente rodava num WORKER separado so' pra ficar
# fazendo long polling (getUpdates em loop). Como o nucleo (agente, memoria,
# banco) ja' e' o mesmo do WhatsApp, da' pra atender o Telegram AQUI, por
# webhook, e aposentar o worker - 1 servico em vez de 2 (corta ~metade do
# compute fixo). Ativa so' se TELEGRAM_TOKEN estiver setado neste servico.
_tg_app = None   # Application do python-telegram-bot (modo webhook), por processo


@app.on_event("startup")
async def _iniciar_telegram_webhook() -> None:
    global _tg_app
    token = os.environ.get("TELEGRAM_TOKEN")
    if not token:
        return   # Telegram nao configurado neste servico: rota fica inativa
    try:
        import telegram_bot as _tg
        _setup()   # garante _pool/_brain/_transcritor (publica _pool por ultimo)
        if _pool is None or _brain is None:
            # nao deve acontecer (com o _setup corrigido), mas se acontecer NAO
            # ligamos o Telegram com um brain None (agente sem cerebro) - melhor a
            # rota ficar inativa e logar do que responder erro a cada mensagem.
            log.error("Telegram nao iniciado: nucleo incompleto (pool=%s brain=%s)",
                      _pool is not None, _brain is not None)
            _tg_app = None
            return
        _tg_app = _tg.construir_application(token, _pool, _brain, _transcritor,
                                            com_updater=False)
        await _tg_app.initialize()
        await _tg_app.start()   # sobe o consumidor da update_queue (sem poller)
    except Exception:  # noqa: BLE001 - Application nao subiu: rota fica inativa
        log.exception("falha ao iniciar a Application do Telegram")
        _tg_app = None
        return
    # Registrar o webhook e' um efeito GLOBAL (do bot, nao do processo) e o
    # INTERRUPTOR que troca a entrega do polling pra ca'. So' faz se
    # TELEGRAM_WEBHOOK_URL estiver setada. CRITICO: com 2 workers do uvicorn, os
    # dois chamam setWebhook quase juntos e o Telegram responde 429 (flood) pra
    # um deles - isso NAO pode derrubar o _tg_app (basta UM worker registrar; os
    # dois processam updates igual). Entao a falha aqui e' tolerada.
    url = os.environ.get("TELEGRAM_WEBHOOK_URL")
    if not url:
        log.info("Telegram pronto (webhook), aguardando TELEGRAM_WEBHOOK_URL "
                 "pra registrar a entrega.")
        return
    secret = os.environ.get("TELEGRAM_WEBHOOK_SECRET") or None
    await _registrar_webhook_telegram(_tg_app.bot, url, secret)


async def _registrar_webhook_telegram(bot, url: str, secret: str | None,
                                      tentativas: int = 2) -> bool:
    """Registra o webhook, tolerando flood (429). Retorna True se registrou.

    NUNCA propaga excecao: registrar o webhook e' efeito GLOBAL do bot; com 2
    workers, um pega 429 - basta o outro registrar. A falha aqui nao pode
    derrubar quem chamou (o _tg_app tem que seguir vivo pra processar updates)."""
    from telegram.error import RetryAfter
    import asyncio as _aio
    for _ in range(max(1, tentativas)):
        try:
            await bot.set_webhook(
                url=url, secret_token=secret,
                allowed_updates=["message", "edited_message", "callback_query"],
                drop_pending_updates=False)
            log.info("Telegram webhook registrado em %s", url)
            return True
        except RetryAfter as e:                       # 429 flood: espera e tenta
            await _aio.sleep(getattr(e, "retry_after", 1) or 1)
        except Exception:  # noqa: BLE001 - outro worker pode ter registrado; segue
            log.warning("nao registrei o webhook agora (outro worker pode ter "
                        "feito); a rota /webhook/telegram segue ATIVA", exc_info=True)
            return False
    log.warning("webhook nao registrado apos %s tentativas (flood); a rota segue "
                "ATIVA e outro worker/deploy registra", tentativas)
    return False


@app.on_event("shutdown")
async def _parar_telegram_webhook() -> None:
    global _tg_app
    if _tg_app is not None:
        try:
            await _tg_app.stop()
            await _tg_app.shutdown()
        except Exception:  # noqa: BLE001
            pass
        _tg_app = None

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


_setup_lock = threading.Lock()


def _setup():
    global _pool, _brain, _transcritor
    if _pool is None:
        # CRITICO: _pool e' o SENTINELA ('if _pool is None'), entao tem que ser o
        # ULTIMO a ser publicado - senao outro chamador concorrente (ex: o startup
        # do Telegram vs a thread do poller de e-mail) ve _pool setado enquanto
        # _brain ainda e' None, e captura um brain None (agente sem cerebro ->
        # 'NoneType' has no attribute 'chamar'). O lock evita init duplicada; o
        # double-check garante uma so' inicializacao.
        with _setup_lock:
            if _pool is None:
                pool = get_pool()
                init_schema(pool)
                _brain = Brain(model=os.environ.get("OPENCLAW_MODEL", "claude-sonnet-4-6"))
                _transcritor = transcritor_se_configurado()
                _pool = pool          # publica o sentinela POR ULTIMO
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
    ag = criar_agente_financeiro(_brain, LivroCaixa(_pool, conta.id, membro.id), memoria, lista, membro.papel, banco, cidade, pool=_pool, conta_id=conta.id, empresa_nome=getattr(conta, "nome", ""))
    ag.canal_atual = "whatsapp"
    return ag


def _responder_whatsapp(to: str, texto: str):
    from twilio.rest import Client
    cli = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
    cli.messages.create(
        from_=os.environ["TWILIO_WHATSAPP_FROM"], to=to, body=texto or "(sem resposta)",
    )


# Avisos de progresso no WhatsApp: o Twilio (Programmable Messaging) NAO tem
# "digitando..." como o Telegram, entao pra foto/PDF (o caso lento: QR + varias
# idas ao modelo) a gente avisa por MENSAGEM que esta' trabalhando. Cada aviso e'
# uma msg Twilio (custa), entao: 1 ack imediato + no maximo 2 "ainda trabalhando"
# espacados, e para assim que a resposta final sai. Intervalos configuraveis.
def _intervalos_progresso_wpp() -> list[int]:
    try:
        brutos = os.environ.get("WPP_PROGRESSO_SEGUNDOS", "15,20")
        vals = [int(x) for x in brutos.split(",") if x.strip()]
        return vals[:2] or [15, 20]      # cap em 2 avisos
    except (TypeError, ValueError):
        return [15, 20]


def _avisador_progresso_wpp(to: str):
    """Dispara avisos de 'ainda trabalhando' enquanto a leitura do cupom/PDF roda.
    Devolve uma funcao parar() que encerra os avisos (chame quando a resposta sair).
    Best-effort: nunca levanta, nunca bloqueia o processamento."""
    import threading
    done = threading.Event()
    avisos = ["Ainda tô lendo... quase lá! ⏳",
              "Só mais um instante, finalizando o registro pra você... 🙏"]

    def _loop():
        for espera, msg in zip(_intervalos_progresso_wpp(), avisos):
            if done.wait(espera):
                return                    # resposta ja' saiu: para
            if done.is_set():
                return
            try:
                _responder_whatsapp(to, msg)
            except Exception:  # noqa: BLE001
                return

    threading.Thread(target=_loop, daemon=True, name="wpp-progresso").start()
    return done.set


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
    parar_avisos = (lambda: None)   # vira o stopper dos avisos quando for foto/PDF
    try:
        # --- RSVP de convite de reunião pelos botões do WhatsApp (quick reply) ---
        # Intercepta ANTES de membro/lead: o convidado costuma ser número "frio"
        # (ainda não tem conta), então cairia no fluxo de lead por engano. Só age
        # se houver um convite PENDENTE pra este número — bem restrito.
        if body and not media_url:
            from finance import convites as _cv
            _st = _cv.rsvp_por_texto(body)
            if _st:
                _pend = _cv.pendentes_por_numero(pool, numero)
                if _pend:
                    _c = _cv.responder(pool, _pend[0]["token"], _st)
                    if _c:
                        if _c.get("mudou"):                 # só avisa se mudou (não repete)
                            _cv.pos_resposta(pool, _c)
                        _responder_whatsapp(to, _cv.confirmacao_texto(_c))
                        return

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
        from finance import config_app as _cfg
        if not ct.acesso_liberado(conta, _cfg.beta_gratis_ativo(pool)):
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
            # AGILIDADE: foto/PDF sao o caso lento (QR + varias idas ao modelo).
            # Avisa NA HORA que recebeu e, se demorar, manda "ainda trabalhando"
            # ate' a resposta sair - o WhatsApp nao tem "digitando..." (Twilio).
            if ctype.startswith("image/") or "pdf" in ctype:
                _responder_whatsapp(to, "👀 Recebi! Já tô lendo, um segundinho...")
                parar_avisos = _avisador_progresso_wpp(to)
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
            elif ctype.startswith("audio/"):
                # AUDIO/VOZ: transcreve. NUNCA deixa o cliente no vacuo — se nao
                # der pra transcrever (sem STT configurado, sem credito, erro ou
                # audio vazio), responde pedindo texto em vez de engolir.
                if not _transcritor:
                    _responder_whatsapp(to, "Recebi seu audio! 🎤 Por enquanto eu "
                        "ainda nao consigo ouvir aqui — me manda por texto que eu "
                        "resolvo rapidinho. 😊")
                    return
                try:
                    from finance.agenda import vocabulario_stt
                    _vocab = vocabulario_stt(pool, conta.id)
                except Exception:  # noqa: BLE001
                    _vocab = ""
                try:
                    texto = (_transcritor.transcrever(dados, "audio.ogg", vocab=_vocab) or "").strip()
                except Exception:  # noqa: BLE001
                    log.exception("falha ao transcrever audio")
                    texto = ""
                if not texto:
                    _responder_whatsapp(to, "Recebi seu audio, mas nao consegui "
                        "entender dessa vez. 🎧 Pode repetir com mais calma ou me "
                        "mandar por texto?")
                    return

        agente = _agente_do(membro, conta)
        # Se lemos uma chave, passa pro livro pra que tools a usem ao gravar lançamento
        if chave_nfce:
            agente.livro.chave_nfce_atual = chave_nfce
        resposta = agente.responder(texto, imagem_b64, media_type)
        # Dica de QR SO' pra cupom fiscal (chave lida OU itens registrados);
        # comprovante de Pix/banco nao tem QR - nada de dica de QR nesse caso.
        from finance.nfce_qr import deve_mandar_dica_qr
        if deve_mandar_dica_qr(dica_qr, chave_nfce, getattr(agente, "_obs_tools", set())):
            resposta = (resposta or "") + DICA_QR_WPP
        _responder_whatsapp(to, resposta)
    except Exception as e:  # noqa: BLE001
        log.exception("erro no whatsapp")
        try:
            # NUNCA vaza o erro tecnico pro cliente (ex: saldo da Anthropic).
            # tratar_falha_ia devolve uma mensagem amigavel e avisa o admin por
            # e-mail/Telegram se a causa for saldo/credito da IA.
            from core.falhas import tratar_falha_ia
            _responder_whatsapp(to, tratar_falha_ia(e, canal="whatsapp"))
        except Exception:
            pass
    finally:
        # encerra os avisos de progresso em QUALQUER saida (resposta, dup, erro).
        try:
            parar_avisos()
        except Exception:  # noqa: BLE001
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
    if ref.startswith("titulo:"):
        if evento in ("PAYMENT_RECEIVED", "PAYMENT_CONFIRMED"):
            try:
                from finance import empresa as _emp
                _pool = _setup()
                _tid = int(ref.split(":", 1)[1])
                with _pool.connection() as _c:
                    _r = _c.execute(
                        "select conta_id from titulos where id=%s", (_tid,)).fetchone()
                if _r:
                    _emp.marcar_titulo_recebido(_pool, _r[0], _tid)
                _logw.warning("ASAAS titulo pago: %s", ref)
            except Exception as _e:  # noqa: BLE001
                _logw.warning("ASAAS titulo %s erro: %s", ref, _e)
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
    # Body vazio + botão tocado (quick reply do template): o texto vem em ButtonText.
    body = form.get("Body", "") or form.get("ButtonText", "") or form.get("ButtonPayload", "") or ""
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


@app.post("/webhook/telegram")
async def telegram_webhook(request: Request):
    """Recebe updates do Telegram (quando o bot esta' em modo webhook).

    Responde 200 na hora e processa em background (via update_queue do PTB),
    igual ao WhatsApp - assim o Telegram nao re-entrega por timeout."""
    if _tg_app is None:
        return JSONResponse({"ok": False, "reason": "telegram nao configurado"},
                            status_code=503)
    # Autenticidade: o Telegram devolve o secret_token no header. Sem ele bater,
    # rejeita (evita update forjado por quem descobrir a URL).
    segredo = os.environ.get("TELEGRAM_WEBHOOK_SECRET")
    if segredo and request.headers.get("X-Telegram-Bot-Api-Secret-Token") != segredo:
        return JSONResponse({"ok": False}, status_code=403)
    try:
        dados = await request.json()
    except Exception:  # noqa: BLE001
        return JSONResponse({"ok": False, "reason": "json invalido"}, status_code=400)
    from telegram import Update
    update = Update.de_json(dados, _tg_app.bot)
    await _tg_app.update_queue.put(update)   # PTB processa em background
    return {"ok": True}


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
