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

import httpx
from fastapi import FastAPI, Request, BackgroundTasks
from fastapi.responses import HTMLResponse, JSONResponse, Response

from db.conexao import get_pool, init_schema
from usuarios import usuarios as u
from core.brain import Brain
from core.memory import MemoriaConversa
from core.transcribe import transcritor_se_configurado
from finance.livro_caixa import LivroCaixa
from finance.agente_financeiro import criar_agente_financeiro

app = FastAPI(title="OpenClaw")
log = logging.getLogger("openclaw.web")

_pool = None
_brain = None
_transcritor = None
_agentes: dict[int, object] = {}


def _setup():
    global _pool, _brain, _transcritor
    if _pool is None:
        _pool = get_pool()
        init_schema(_pool)
        _brain = Brain(model=os.environ.get("OPENCLAW_MODEL", "claude-sonnet-4-6"))
        _transcritor = transcritor_se_configurado()
    return _pool


def _agente_do(usuario):
    ag = _agentes.get(usuario.id)
    if ag is None:
        ag = criar_agente_financeiro(_brain, LivroCaixa(_pool, usuario.id), MemoriaConversa())
        _agentes[usuario.id] = ag
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


def processar_whatsapp(numero: str, nome: str | None, body: str,
                       media_url: str | None, media_ctype: str):
    """Roda em background: identifica, checa acesso, agente, responde via Twilio."""
    pool = _setup()
    to = f"whatsapp:{numero}"
    try:
        usuario = u.get_or_create_whatsapp(pool, numero, nome)
        if not u.acesso_liberado(usuario):
            _responder_whatsapp(to, "Seu acesso esta suspenso (pagamento pendente). "
                                    "Assim que o pagamento for confirmado, voce volta a usar.")
            return
        ok, _restante = u.checar_e_registrar_uso(pool, usuario)
        if not ok:
            _responder_whatsapp(to, "Voce atingiu o limite de mensagens de hoje. A gente se fala amanha!")
            return

        texto = body or ""
        imagem_b64 = None
        if media_url:
            dados = _baixar_midia(media_url)
            if (media_ctype or "").startswith("image/"):
                imagem_b64 = base64.b64encode(dados).decode("ascii")
                texto = body or "Segue o cupom para registrar."
            elif (media_ctype or "").startswith("audio/") and _transcritor:
                texto = _transcritor.transcrever(dados, "audio.ogg")

        resposta = _agente_do(usuario).responder(texto, imagem_b64)
        _responder_whatsapp(to, resposta)
    except Exception as e:  # noqa: BLE001
        log.exception("erro no whatsapp")
        try:
            _responder_whatsapp(to, f"Ops, deu um erro aqui: {e}")
        except Exception:
            pass


@app.post("/webhook/whatsapp")
async def whatsapp(request: Request, background: BackgroundTasks):
    form = await request.form()
    numero = (form.get("From", "") or "").replace("whatsapp:", "")
    nome = form.get("ProfileName") or None
    body = form.get("Body", "") or ""
    media_url = None
    media_ctype = ""
    if int(form.get("NumMedia", "0") or 0) > 0:
        media_url = form.get("MediaUrl0")
        media_ctype = form.get("MediaContentType0", "")
    if numero:
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


_PAGINA = """<!doctype html>
<html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>OpenClaw</title>
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
  <h1>OpenClaw</h1>
  <p>Seu assistente financeiro pessoal.</p>
  <p class="status"><span class="dot"></span>no ar</p>
  <p class="soon">Fale com o assistente no Telegram ou no WhatsApp.</p>
</div></body></html>"""
