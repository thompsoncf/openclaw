"""Embriao da web (a interface "depois").

Por enquanto serve uma pagina simples (status) e um /health que confere a
conexao com o banco. Reaproveita o mesmo nucleo e o mesmo Postgres do bot,
entao da' pra evoluir daqui pro painel financeiro de verdade sem recomecar.

Sobe assim:
    uvicorn web.app:app --host 0.0.0.0 --port $PORT
"""
from fastapi import FastAPI
from fastapi.responses import HTMLResponse, JSONResponse

from db.conexao import get_pool

app = FastAPI(title="OpenClaw")

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
  <p class="soon">Em breve, seu painel financeiro aqui.<br>
  Por enquanto, fale com o assistente no Telegram.</p>
</div></body></html>"""


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
