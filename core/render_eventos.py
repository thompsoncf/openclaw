"""Observabilidade dos deploys do Render.

Recebe os webhooks que o Render dispara (deploy começou, deploy terminou,
serviço caiu), ENRIQUECE com a API do Render, grava em `render_evento` e avisa
o admin quando um deploy quebra.

POR QUE ISSO EXISTE
-------------------
O `scripts/render_cli.py` já consulta a API do Render — mas só funciona de onde
a API é alcançável. O ambiente do agente (Claude Code na web) bloqueia
`api.render.com` na política de egresso (403 no CONNECT), e o mesmo vale pra
qualquer runner sem a chave. Resultado: hoje só se descobre que um deploy
falhou abrindo o dashboard no navegador.

O webhook inverte a direção. Em vez do agente PERGUNTAR ao Render (bloqueado),
o Render AVISA a gente. O receptor roda dentro do openclaw-web — que está
hospedado no próprio Render e portanto alcança `api.render.com` sem problema —
então é ele quem faz o enriquecimento e deixa o resultado mastigado no
Postgres, onde qualquer um lê sem depender de rede externa.

Ganho prático: falha de deploy vira alerta no Telegram/e-mail na hora, com o
commit e a cauda do log de build junto. Não é mais preciso saber que quebrou
pra só então ir caçar o motivo.

CONFIGURAÇÃO
------------
    RENDER_WEBHOOK_SECRET   segredo de assinatura do webhook (começa com whsec_)
    RENDER_API_KEY          chave da API, pro enriquecimento (opcional)

Sem `RENDER_WEBHOOK_SECRET` o receptor fica INERTE: responde 200 e ignora, sem
gravar nada. É proposital — assim dá pra subir o código antes de criar o
webhook no dashboard, sem quebrar nada e sem aceitar corpo não assinado.

Sem `RENDER_API_KEY` o evento ainda é gravado, só que sem enriquecer (fica o
tipo, o serviço e o corpo cru). Degrada, não morre.

TUDO É BEST-EFFORT: nada aqui levanta exceção pra fora. Um webhook que falha em
gravar não pode derrubar a rota — o Render reentregaria em loop.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import time

_log = logging.getLogger("openclaw.render")

API = "https://api.render.com/v1"

# Tolerância do timestamp do webhook, em segundos. É o valor do padrão Standard
# Webhooks: recusa entrega velha demais (replay) ou com relógio muito à frente.
_TOLERANCIA_S = 5 * 60

# Cauda de log capturada quando o deploy falha. 80 linhas costuma pegar o
# traceback inteiro do build sem transformar a linha do banco num monstro.
_LINHAS_LOG_FALHA = 80

# `details.status` do evento deploy_ended. O exemplo oficial do Render
# (render-examples/webhook-github-action) confirma que 2 = sucesso e trata
# qualquer outro valor como fracasso. Como o resto do enum não está documentado
# de forma estável, a gente NÃO chuta rótulo: guarda o número cru em
# `status_num` e prefere o status em TEXTO, que já vem legível.
_STATUS_SUCESSO = 2

# Existem DOIS vocabulários de status em texto, e eles não são o mesmo:
#
#   - o do PRÓPRIO WEBHOOK (`data.status`): grosso — "succeeded" / "failed".
#     Vem de graça no corpo, sem chamar a API. Observado em entrega real:
#       {"type":"deploy_ended","data":{...,"serviceName":"openclaw-web-bcu3",
#        "status":"succeeded"}}
#     (a definição de tipos do exemplo oficial do Render está desatualizada:
#      lá o `data` só tem `id` e `serviceId`.)
#
#   - o do DEPLOY na API (`/services/{id}/deploys/{id}` → `status`): fino —
#     "live", "build_failed", "update_failed", "pre_deploy_failed".
#     Diz TAMBÉM em que etapa quebrou, o que o do webhook não diz.
#
# A gente aceita os dois e prefere o fino quando ele existe. Aceitar o grosso é
# o que faz o alerta continuar funcionando sem RENDER_API_KEY.
_TEXTO_SUCESSO = frozenset({"live", "deactivated", "succeeded"})
# Status textuais que significam "quebrou" (merecem alerta).
_TEXTO_FALHA = frozenset({"build_failed", "update_failed", "pre_deploy_failed",
                          "failed"})


# --------------------------------------------------------------------------
# 1. Assinatura (padrão Standard Webhooks)
# --------------------------------------------------------------------------

def _chave_secreta(segredo: str) -> bytes:
    """Converte o segredo do webhook em bytes de chave HMAC.

    O Render entrega o segredo no formato do Standard Webhooks: o prefixo
    `whsec_` seguido da chave em base64. Quem esquece de tirar o prefixo (ou de
    decodificar) gera assinatura que nunca bate — e o sintoma é um 400 mudo,
    difícil de diagnosticar. Por segurança, aceita também um segredo cru, pra
    não travar caso o Render mude o formato.
    """
    s = (segredo or "").strip()
    if s.startswith("whsec_"):
        s = s[len("whsec_"):]
    try:
        return base64.b64decode(s)
    except Exception:  # noqa: BLE001 - segredo fora do padrão: usa como veio
        return s.encode("utf-8")


def verificar_assinatura(corpo: bytes, headers, segredo: str | None = None) -> bool:
    """True se o corpo veio mesmo do Render.

    `corpo` precisa ser o byte-a-byte CRU da requisição. Se for reserializado
    (json.loads + json.dumps), a assinatura não bate: qualquer diferença de
    espaço ou de ordem de chave muda o HMAC.

    `headers` é qualquer mapping case-insensitive (o `request.headers` do
    Starlette serve).
    """
    segredo = segredo if segredo is not None else os.environ.get("RENDER_WEBHOOK_SECRET", "")
    if not segredo:
        return False

    wid = (headers.get("webhook-id") or "").strip()
    wts = (headers.get("webhook-timestamp") or "").strip()
    wsig = (headers.get("webhook-signature") or "").strip()
    if not (wid and wts and wsig):
        _log.warning("webhook do Render sem os headers de assinatura")
        return False

    # Janela de tempo: barra replay de entrega antiga.
    try:
        idade = abs(time.time() - int(wts))
    except (TypeError, ValueError):
        _log.warning("webhook-timestamp invalido: %r", wts)
        return False
    if idade > _TOLERANCIA_S:
        _log.warning("webhook do Render fora da janela (%.0fs de diferenca)", idade)
        return False

    assinado = b"%s.%s." % (wid.encode(), wts.encode()) + corpo
    esperado = base64.b64encode(
        hmac.new(_chave_secreta(segredo), assinado, hashlib.sha256).digest()
    ).decode()

    # O header pode trazer VÁRIAS assinaturas separadas por espaço (o padrão
    # prevê isso pra rotação de segredo). Basta uma bater.
    for parte in wsig.split():
        _, _, sig = parte.partition(",")   # formato "v1,<base64>"
        if sig and hmac.compare_digest(sig, esperado):
            return True

    _log.warning("assinatura do webhook do Render nao confere")
    return False


# --------------------------------------------------------------------------
# 2. API do Render (enriquecimento)
# --------------------------------------------------------------------------

def _get(caminho: str, params: dict | None = None):
    """GET na API do Render. Devolve o JSON ou None (nunca levanta).

    Roda de dentro do Render, então a API é alcançável. Em qualquer outro lugar
    isso pode dar 403 de política de rede — daí o None e o log, sem estourar.
    """
    chave = os.environ.get("RENDER_API_KEY")
    if not chave:
        return None
    try:
        import httpx
        r = httpx.get(
            f"{API}{caminho}",
            params=params or {},
            headers={"Authorization": f"Bearer {chave}", "Accept": "application/json"},
            timeout=15,
        )
        if r.status_code >= 400:
            _log.warning("API do Render devolveu %s em %s", r.status_code, caminho)
            return None
        return r.json()
    except Exception as e:  # noqa: BLE001 - enriquecer e' opcional
        _log.warning("falha ao consultar a API do Render (%s): %s", caminho, e)
        return None


def _detalhes_evento(evento_id: str) -> dict:
    """Detalhes do evento. É de onde sai o `deployId` — o corpo do webhook NÃO
    traz o deploy, só o id do evento."""
    return _get(f"/events/{evento_id}") or {}


def _servico(servico_id: str) -> dict:
    return _get(f"/services/{servico_id}") or {}


def _deploy(servico_id: str, deploy_id: str) -> dict:
    return _get(f"/services/{servico_id}/deploys/{deploy_id}") or {}


def _log_da_falha(servico_id: str, owner_id: str) -> str:
    """Cauda do log do serviço, pra anexar no alerta de falha.

    É o que transforma "o deploy quebrou" em "o deploy quebrou POR ISTO". Sem
    isso o alerta obriga a abrir o dashboard, que é justamente o que a gente
    está tentando evitar.
    """
    if not owner_id:
        return ""
    dados = _get("/logs", {
        "ownerId": owner_id,
        "resource": servico_id,
        "limit": _LINHAS_LOG_FALHA,
        "direction": "backward",
    })
    if not dados:
        return ""
    linhas = dados.get("logs", dados) if isinstance(dados, dict) else dados
    saida = []
    for ln in (linhas or []):
        if isinstance(ln, dict):
            saida.append(f"{ln.get('timestamp', '')}  {ln.get('message', '')}".strip())
    # A API devolve do mais novo pro mais velho; inverte pra ler na ordem natural.
    return "\n".join(reversed(saida))


# --------------------------------------------------------------------------
# 3. Persistência
# --------------------------------------------------------------------------

def _gravar(pool, linha: dict) -> bool:
    """Grava o evento. Devolve True se gravou, False se era duplicata.

    O `on conflict do nothing` no `webhook_id` é o que segura a reentrega: o
    Render remanda o webhook quando não recebe 200 a tempo, e a gente não pode
    alertar duas vezes pelo mesmo deploy.
    """
    sql = """
        insert into render_evento
            (webhook_id, evento_id, tipo, servico_id, servico_nome, deploy_id,
             status, status_num, sucesso, commit_id, commit_msg, log_trecho,
             ocorrido_em, payload, detalhes)
        values (%(webhook_id)s, %(evento_id)s, %(tipo)s, %(servico_id)s,
                %(servico_nome)s, %(deploy_id)s, %(status)s, %(status_num)s,
                %(sucesso)s, %(commit_id)s, %(commit_msg)s, %(log_trecho)s,
                %(ocorrido_em)s, %(payload)s::jsonb, %(detalhes)s::jsonb)
        on conflict (webhook_id) do nothing
        returning id
    """
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, linha)
            novo = cur.fetchone()
        conn.commit()
    return novo is not None


# --------------------------------------------------------------------------
# 4. Alerta
# --------------------------------------------------------------------------

def _avisar_falha(linha: dict) -> None:
    """Avisa o admin que um deploy quebrou, pelo canal que já existe
    (/admin/comunicacao -> Telegram + e-mail)."""
    nome = linha.get("servico_nome") or linha.get("servico_id") or "servico"
    status = linha.get("status") or f"status {linha.get('status_num')}"
    commit = (linha.get("commit_id") or "")[:8]
    msg = (linha.get("commit_msg") or "").splitlines()
    msg = msg[0] if msg else ""

    corpo = [f"Deploy de {nome} terminou como {status}."]
    if commit:
        corpo.append(f"Commit: {commit} {msg}".rstrip())
    if linha.get("deploy_id"):
        corpo.append(f"Deploy: {linha['deploy_id']}")
    trecho = linha.get("log_trecho")
    if trecho:
        corpo.append("\nFinal do log:\n" + trecho)

    try:
        from finance.notificar import avisar_admin
        avisar_admin(f"[Render] deploy falhou: {nome}", "\n".join(corpo))
    except Exception as e:  # noqa: BLE001 - alerta nunca quebra o fluxo
        _log.warning("nao consegui avisar o admin da falha de deploy: %s", e)


# --------------------------------------------------------------------------
# 5. Orquestração
# --------------------------------------------------------------------------

def _classificar(status_txt: str, status_num) -> bool | None:
    """True = deu certo, False = quebrou, None = não dá pra afirmar.

    Prefere o status em TEXTO (autoritativo e legível, vem do deploy na API) e
    só cai no número quando a API não respondeu. Devolver None em vez de chutar
    False evita alerta falso quando o enriquecimento não rolou.
    """
    t = (status_txt or "").strip().lower()
    if t in _TEXTO_SUCESSO:
        return True
    if t in _TEXTO_FALHA:
        return False
    if t in ("canceled", "cancelled"):
        return None            # cancelado por gente: não é falha, não alerta
    if isinstance(status_num, int):
        return status_num == _STATUS_SUCESSO
    return None


def processar(corpo: bytes, headers, pool=None) -> dict:
    """Processa um webhook JÁ VERIFICADO. Devolve um resumo (pra log/teste).

    Chame isto FORA do ciclo da resposta HTTP (BackgroundTasks): o
    enriquecimento faz até 4 chamadas na API do Render e o Render espera 200
    rápido — senão considera falha de entrega e reenvia.
    """
    resumo = {"ok": False, "gravado": False, "tipo": None}
    try:
        payload = json.loads(corpo.decode("utf-8", "replace"))
    except Exception as e:  # noqa: BLE001
        _log.warning("corpo do webhook do Render nao e' JSON: %s", e)
        return resumo

    dados = payload.get("data") or {}
    tipo = payload.get("type") or "desconhecido"
    servico_id = dados.get("serviceId") or ""
    evento_id = dados.get("id") or ""
    resumo["tipo"] = tipo

    linha = {
        "webhook_id": (headers.get("webhook-id") or "").strip() or evento_id,
        "evento_id": evento_id or None,
        "tipo": tipo,
        "servico_id": servico_id or None,
        # O próprio corpo já traz nome e status; o enriquecimento abaixo só
        # REFINA. Sem isto, um webhook chegando com RENDER_API_KEY ausente (ou
        # com a API fora do ar) viraria uma linha anônima e sem veredito, mesmo
        # o Render tendo mandado as duas coisas de graça.
        "servico_nome": dados.get("serviceName") or None,
        "deploy_id": None,
        "status": dados.get("status") or None,
        "status_num": None,
        "sucesso": None,
        "commit_id": None,
        "commit_msg": None,
        "log_trecho": None,
        "ocorrido_em": payload.get("timestamp"),
        "payload": json.dumps(payload),
        "detalhes": None,
    }

    # --- enriquecimento (best-effort; sem RENDER_API_KEY tudo isso vira no-op) ---
    detalhes = _detalhes_evento(evento_id) if evento_id else {}
    if detalhes:
        linha["detalhes"] = json.dumps(detalhes)
        d = detalhes.get("details") or {}
        linha["deploy_id"] = d.get("deployId")
        if isinstance(d.get("status"), int):
            linha["status_num"] = d["status"]

    servico = _servico(servico_id) if servico_id else {}
    if servico:
        linha["servico_nome"] = servico.get("name") or linha["servico_nome"]

    if servico_id and linha["deploy_id"]:
        dep = _deploy(servico_id, linha["deploy_id"])
        if dep:
            # o status do deploy e' mais fino que o do webhook ("build_failed"
            # em vez de "failed"): diz em que etapa quebrou. So' substitui se
            # veio mesmo — `or` pra nao apagar o que o corpo ja' informou.
            linha["status"] = dep.get("status") or linha["status"]
            commit = dep.get("commit") or {}
            linha["commit_id"] = commit.get("id")
            linha["commit_msg"] = commit.get("message")

    linha["sucesso"] = _classificar(linha["status"], linha["status_num"])

    # Log só na falha: em deploy que deu certo seria só peso no banco.
    if linha["sucesso"] is False and servico_id:
        owner = servico.get("ownerId") or (servico.get("owner") or {}).get("id") or ""
        linha["log_trecho"] = _log_da_falha(servico_id, owner) or None

    # --- grava ---
    try:
        from db.conexao import get_pool
        pool = pool or get_pool()
        gravou = _gravar(pool, linha)
    except Exception as e:  # noqa: BLE001 - nunca deixa o webhook estourar
        _log.exception("falha ao gravar evento do Render: %s", e)
        return resumo

    resumo["ok"] = True
    resumo["gravado"] = gravou

    # Alerta SÓ na primeira vez que este webhook chega (gravou == True) e só se
    # a gente tem certeza de que quebrou. Reentrega não realerta.
    if gravou and linha["sucesso"] is False:
        _avisar_falha(linha)

    if not gravou:
        _log.info("webhook do Render repetido (%s), ignorado", linha["webhook_id"])
    else:
        _log.info("evento do Render gravado: %s %s status=%s",
                  tipo, linha.get("servico_nome") or servico_id, linha.get("status"))
    return resumo


# --------------------------------------------------------------------------
# 6. Leitura (é o que o agente/CLI usa)
# --------------------------------------------------------------------------

def historico(pool=None, servico: str = "", limite: int = 20,
              so_falhas: bool = False) -> list[dict]:
    """Últimos eventos gravados, do mais novo pro mais velho.

    Lê só do Postgres de propósito: funciona mesmo onde `api.render.com` está
    bloqueada, que é exatamente o caso do ambiente do agente.
    """
    from db.conexao import get_pool
    pool = pool or get_pool()

    onde, args = [], []
    if servico:
        onde.append("(servico_nome = %s or servico_id = %s)")
        args += [servico, servico]
    if so_falhas:
        onde.append("sucesso is false")
    filtro = ("where " + " and ".join(onde)) if onde else ""
    args.append(max(1, min(limite, 200)))

    sql = f"""
        select recebido_em, tipo, servico_nome, servico_id, deploy_id, status,
               sucesso, commit_id, commit_msg, log_trecho
          from render_evento
          {filtro}
         order by recebido_em desc
         limit %s
    """
    with pool.connection() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, args)
            cols = [c.name for c in cur.description]
            return [dict(zip(cols, r)) for r in cur.fetchall()]
