"""Módulo "IA Insta" — conteúdo gerado por IA pro Instagram/Facebook.

Aba de Prospecção que escreve o post com IA, renderiza as artes, deixa o humano
conferir e publica na Graph API. Mesma divisão das Campanhas: a tela grava
intenção, o motor executa (no clique de "Publicar agora" e no poller do
web/app.py pros agendados).

Arquivo único de propósito — o módulo inteiro (Graph API + motor + telas) mora
aqui pra ser fácil de revisar, aplicar e reverter. Está dividido em 3 partes:

  PARTE 1 — Graph API (publicação no Instagram e no Facebook)
  PARTE 2 — Motor (geração por IA, renderização das artes, publicação)
  PARTE 3 — Rotas e templates da aba (dashboard, wizard de criação, conferência)

Reusa o motor do portal (_render/_env), o gate de acesso da Prospecção
(_acesso) e o CSS dela (_CSS). Escopo multi-tenant: toda query filtra conta_id.

Caminho de publicação: Facebook Login (token EAA, graph.facebook.com). É o que
abre Reels/Stories no futuro e publica na Página no mesmo fluxo. O caminho
Instagram Login (IGAA, graph.instagram.com) NÃO publica Story/Reels — e é o
mesmo token que finance/meta_msg.py usa pra DM, um canal separado
(canais_config.canal='instagram') do que este módulo lê (canal='messenger').

Pré-requisitos do lado da Meta (fora do código):
  - conta do Instagram Profissional ligada a uma Página do Facebook
  - app com instagram_business_basic + instagram_business_content_publish
    aprovados em app review (antes disso só roda com usuários de teste)
  - Página conectada em Comunicação › Canais (canais_config, canal='messenger')

Escopo desta 1ª versão: imagem única e carrossel no feed. Story, Reels, Vídeo,
Criação Livre (geração de imagem por IA) e Foto Profissional (retoque por IA)
aparecem na tela como preview do que vem depois — cada um pede um fornecedor de
IA/processamento diferente do que já está aqui (Pillow + Graph API) e fica pra
uma rodada seguinte, com decisão de custo/fornecedor em separado.
"""
from __future__ import annotations

import io
import json
import logging
import os
import re
import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
import uuid
from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse, JSONResponse

from db.conexao import get_pool
from web.portal import _render, _env
from web.painel_prospeccao import _acesso, _CSS, _navbar

router = APIRouter()
_log = logging.getLogger("openclaw.ia_insta")


# ==================================================================== PARTE 1
# Graph API — publicação no Instagram e no Facebook.
#
# Modelo de publicação no Instagram (sempre 2 passos):
#   1) POST /<ig_id>/media          -> cria container, devolve creation_id
#   2) POST /<ig_id>/media_publish  -> publica o container
#
# Carrossel: um container por imagem com is_carousel_item=true, depois um
# container pai com media_type=CAROUSEL e children=<ids>, e só então o publish.
#
# Limite: 100 posts publicados por API em 24h por conta (carrossel conta como 1).
#
# Nenhuma função aqui levanta exceção de rede: todas devolvem {"ok": bool, ...}.

_GRAPH = "https://graph.facebook.com/v21.0"
_TIMEOUT = 60

# Máximo de imagens num carrossel do Instagram.
MAX_CARROSSEL = 10
# Teto de publicações por API em 24h (regra da Meta). O motor checa antes.
LIMITE_24H = 100


# ------------------------------------------------------------------ HTTP cru

def _req(metodo: str, caminho: str, params: dict) -> dict:
    """GET/POST na Graph API. Devolve {"ok":True, **json} ou {"ok":False,"erro":...}."""
    url = f"{_GRAPH}/{caminho.lstrip('/')}"
    dados = {k: v for k, v in params.items() if v is not None and v != ""}
    corpo = urllib.parse.urlencode(dados).encode()
    if metodo.upper() == "GET":
        url = f"{url}?{urllib.parse.urlencode(dados)}"
        corpo = None
    req = urllib.request.Request(url, data=corpo, method=metodo.upper())
    try:
        with urllib.request.urlopen(req, timeout=_TIMEOUT) as r:
            d = json.loads(r.read().decode() or "{}")
        if isinstance(d, dict) and d.get("error"):
            return {"ok": False, "erro": _msg_erro(d["error"])}
        return {"ok": True, **(d if isinstance(d, dict) else {"dados": d})}
    except urllib.error.HTTPError as e:
        try:
            d = json.loads(e.read().decode() or "{}")
            return {"ok": False, "erro": _msg_erro(d.get("error") or {}) or f"HTTP {e.code}"}
        except Exception:  # noqa: BLE001
            return {"ok": False, "erro": f"HTTP {e.code}"}
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "erro": f"{type(e).__name__}: {e}"}


def _msg_erro(err: dict) -> str:
    """Mensagem curta e legível do erro da Graph (o payload dela é verborrágico)."""
    if not isinstance(err, dict):
        return "erro desconhecido"
    partes = [err.get("error_user_msg"), err.get("message")]
    txt = next((p for p in partes if p), "erro desconhecido")
    cod = err.get("code")
    sub = err.get("error_subcode")
    marca = f" (code={cod}{'/' + str(sub) if sub else ''})" if cod else ""
    return f"{txt}{marca}"[:400]


# ------------------------------------------------------------------ descoberta

def conta_ig_da_pagina(page_id: str, token: str) -> dict:
    """Descobre o ID da conta do Instagram Profissional ligada à Página.

    É esse ID (e não o @usuario nem o page_id) que vai em todas as chamadas de
    publicação. Sem a conta ligada à Página, a Graph devolve o campo vazio."""
    r = _req("GET", page_id, {"fields": "instagram_business_account,name", "access_token": token})
    if not r.get("ok"):
        return r
    ig = (r.get("instagram_business_account") or {}).get("id")
    if not ig:
        return {"ok": False, "erro": "a Página não tem conta do Instagram Profissional ligada"}
    return {"ok": True, "ig_id": ig, "pagina": r.get("name") or ""}


def limite_publicacao(ig_id: str, token: str) -> dict:
    """Quantas publicações já foram feitas por API nas últimas 24h."""
    r = _req("GET", f"{ig_id}/content_publishing_limit",
             {"fields": "config,quota_usage", "access_token": token})
    if not r.get("ok"):
        return r
    linha = (r.get("data") or [{}])[0]
    usado = int(linha.get("quota_usage") or 0)
    total = int(((linha.get("config") or {}).get("quota_total")) or LIMITE_24H)
    return {"ok": True, "usado": usado, "total": total, "restante": max(0, total - usado)}


# ------------------------------------------------------------------ Instagram

def container_imagem(ig_id: str, token: str, url_imagem: str,
                     legenda: str | None = None, item_carrossel: bool = False) -> dict:
    """Cria o container de uma imagem. `item_carrossel=True` pros filhos do carrossel
    (filho não leva legenda — a legenda vai no container pai)."""
    p = {"image_url": url_imagem, "access_token": token}
    if item_carrossel:
        p["is_carousel_item"] = "true"
    elif legenda:
        p["caption"] = legenda
    r = _req("POST", f"{ig_id}/media", p)
    if not r.get("ok"):
        return r
    if not r.get("id"):
        return {"ok": False, "erro": "a Meta não devolveu id do container"}
    return {"ok": True, "container_id": r["id"]}


def container_carrossel(ig_id: str, token: str, filhos: list[str],
                        legenda: str | None = None) -> dict:
    """Container pai do carrossel. ATENÇÃO: todas as imagens são cortadas pro
    aspecto da PRIMEIRA — mande as imagens já no mesmo aspecto."""
    filhos = [f for f in filhos if f]
    if len(filhos) < 2:
        return {"ok": False, "erro": "carrossel precisa de pelo menos 2 imagens"}
    if len(filhos) > MAX_CARROSSEL:
        return {"ok": False, "erro": f"carrossel aceita no máximo {MAX_CARROSSEL} imagens"}
    r = _req("POST", f"{ig_id}/media", {
        "media_type": "CAROUSEL", "children": ",".join(filhos),
        "caption": legenda or "", "access_token": token})
    if not r.get("ok"):
        return r
    if not r.get("id"):
        return {"ok": False, "erro": "a Meta não devolveu id do container do carrossel"}
    return {"ok": True, "container_id": r["id"]}


def status_container(container_id: str, token: str) -> dict:
    """FINISHED (pronto pra publicar) / IN_PROGRESS / ERROR / EXPIRED / PUBLISHED."""
    r = _req("GET", container_id, {"fields": "status_code,status", "access_token": token})
    if not r.get("ok"):
        return r
    return {"ok": True, "status": r.get("status_code") or "", "detalhe": r.get("status") or ""}


def publicar(ig_id: str, token: str, container_id: str) -> dict:
    """Publica o container. Só chame com status FINISHED (ou logo após criar, pra
    imagem simples — imagem costuma ficar pronta na hora; carrossel nem sempre)."""
    r = _req("POST", f"{ig_id}/media_publish",
             {"creation_id": container_id, "access_token": token})
    if not r.get("ok"):
        return r
    if not r.get("id"):
        return {"ok": False, "erro": "a Meta não devolveu id da mídia publicada"}
    return {"ok": True, "media_id": r["id"]}


def permalink(media_id: str, token: str) -> str | None:
    """URL pública do post publicado (pra mostrar na lista de publicados)."""
    r = _req("GET", media_id, {"fields": "permalink", "access_token": token})
    return r.get("permalink") if r.get("ok") else None


# ------------------------------------------------------------------ Facebook

def publicar_facebook(page_id: str, page_token: str, mensagem: str,
                      url_imagem: str | None = None, link: str | None = None) -> dict:
    """Publica na Página. Com imagem vai em /photos (published=true); sem imagem,
    /feed. O retorno da Meta muda de campo entre os dois (post_id vs id)."""
    if url_imagem:
        r = _req("POST", f"{page_id}/photos", {
            "url": url_imagem, "caption": mensagem or "",
            "published": "true", "access_token": page_token})
        if not r.get("ok"):
            return r
        return {"ok": True, "post_id": r.get("post_id") or r.get("id") or ""}
    r = _req("POST", f"{page_id}/feed", {
        "message": mensagem or "", "link": link, "access_token": page_token})
    if not r.get("ok"):
        return r
    return {"ok": True, "post_id": r.get("id") or ""}

# ==================================================================== PARTE 2
# Motor — geração por IA, renderização das artes e publicação.
#
# Por que renderizar a arte aqui e não pedir imagem pra uma API de geração:
# custo, previsibilidade e marca. Card de texto sobre fundo da marca resolve o
# carrossel educativo, que é o formato que traz lead. Foto de produto e imagem
# gerada entram depois sem mexer neste fluxo (basta gravar outra URL em
# conteudo_midias).

_LOCK = 771146          # advisory lock próprio (não briga com e-mail/WhatsApp)
_MAX_PASS = 3           # posts publicados por passada do poller
_MAX_TENTATIVAS = 3     # depois disso o post fica em 'erro' e espera o humano

VERDE = (29, 158, 117)          # #1d9e75 — verde da marca (var(--verde) do portal)
FUNDO = (14, 14, 15)             # #0e0e0f — var(--bg) do portal
BRANCO = (236, 236, 236)         # #ececec — var(--txt) do portal
LADO = 1080                     # 1:1 — aspecto mais seguro pro carrossel

OBJETIVOS = {
    "atrair":   "expor uma dor concreta do dia a dia de quem lê e terminar com uma pergunta leve",
    "provar":   "mostrar um resultado concreto (número, antes/depois) e terminar com convite discreto",
    "ofertar":  "apresentar a oferta com preço/condição clara e terminar com chamada direta pra ação",
    "bastidor": "mostrar rotina/pessoa por trás do negócio, tom humano, SEM chamada pra ação",
}
FORMATOS = {"carrossel", "post"}
DESTINOS = {"whatsapp", "link", "nenhum"}

# Ideias da semana: prompts curados por objetivo, sem custo de IA — a tela
# escolhe algumas pra mostrar como chips; clicar preenche o tema e já marca o
# objetivo certo. Ver `ideias_semana()`.
_IDEIAS = [
    ("atrair", "Um erro comum que seus clientes cometem sem perceber"),
    ("atrair", "Aquilo que todo cliente novo pergunta primeiro"),
    ("provar", "Um resultado real de um cliente satisfeito"),
    ("provar", "Antes e depois de quem resolveu o problema com você"),
    ("ofertar", "Condição especial pra quem fechar essa semana"),
    ("ofertar", "O que está incluso no seu serviço e o cliente não sabe"),
    ("bastidor", "Um dia normal de trabalho, sem filtro"),
    ("bastidor", "Quem está por trás do negócio"),
]


def ideias_semana(n: int = 5) -> list[dict]:
    """5 sugestões de tema, rotativas por semana do ano (determinístico — sem
    chamar IA a cada carregamento da tela). Cada uma já vem com o objetivo."""
    semana = datetime.now(timezone.utc).isocalendar()[1]
    ordem = _IDEIAS[semana % len(_IDEIAS):] + _IDEIAS[:semana % len(_IDEIAS)]
    return [{"objetivo": o, "texto": t} for o, t in ordem[:n]]


def _agora():
    return datetime.now(timezone.utc)


# ------------------------------------------------------------------ geração (IA)

def gerar(pool, conta_id: int, formato: str, objetivo: str, tema: str,
          n_cards: int = 5) -> dict:
    """Claude escreve o post. Devolve {"legenda": str, "cards": [str, ...]}.

    Fallback simples se a IA falhar: o usuário ainda consegue editar na tela de
    conferência em vez de ficar travado."""
    objetivo = objetivo if objetivo in OBJETIVOS else "atrair"
    n = max(2, min(int(n_cards or 5), 10)) if formato == "carrossel" else 1
    tema = (tema or "").strip() or "organização financeira de pequeno negócio"

    fallback = {
        "legenda": f"{tema.capitalize()}. Chama no WhatsApp que eu te mostro como resolver.",
        "cards": [tema.capitalize()] + [f"Ponto {i}" for i in range(2, n + 1)],
    }
    try:
        from finance.agente import _conhecimento
        with pool.connection() as c:
            instr, _faqs = _conhecimento(c, conta_id)
            idn = _identidade(c, conta_id)
        empresa = idn.get("empresa") or "a empresa"

        if formato == "carrossel":
            regra_cards = (
                f"- 'cards': exatamente {n} textos curtos, um por card do carrossel.\n"
                "- Card 1 é a CAPA: uma frase de impacto, no máximo 9 palavras.\n"
                "- Cards do meio: uma ideia por card, no máximo 16 palavras cada.\n"
                f"- Card {n} é o fechamento: chama pra conversar (a não ser que o objetivo seja bastidor).\n")
        else:
            regra_cards = ("- 'cards': exatamente 1 texto — a frase que vai NA IMAGEM, "
                           "no máximo 9 palavras.\n")

        system = (
            "Você escreve conteúdo para Instagram/Facebook de pequenas empresas, em "
            "português do Brasil. Linguagem simples, concreta, sem jargão de marketing, "
            "sem hipérbole ('revolucionário', 'incrível'), sem emoji nos cards. "
            f"O objetivo deste post é: {OBJETIVOS[objetivo]}.\n"
            "REGRAS:\n"
            f"{regra_cards}"
            "- 'legenda': 2 a 4 linhas pra descrição do post. Pode ter no máximo 2 emojis.\n"
            "- Não invente preço, prazo nem promessa que não esteja na base abaixo.\n"
            "- Não escreva hashtag na legenda (elas entram depois, se o usuário quiser).\n"
            'Responda SÓ o JSON: {"legenda":"...","cards":["...","..."]}\n\n'
            f"QUEM PUBLICA (empresa '{empresa}'):\n{instr or '(sem base cadastrada)'}")
        pedir = f"Tema do post: {tema}\n\nSó o JSON."

        from core.brain import Brain
        resp = Brain().chamar(system=system, mensagens=[{"role": "user", "content": pedir}])
        txt = "".join(getattr(b, "text", "") for b in resp.content
                      if getattr(b, "type", None) == "text").strip()
        txt = re.sub(r"^```json|^```|```$", "", txt, flags=re.M).strip()
        d = json.loads(txt)
        legenda = (d.get("legenda") or "").strip()
        cards = [str(x).strip() for x in (d.get("cards") or []) if str(x).strip()]
        if legenda and cards:
            return {"legenda": legenda[:2000], "cards": cards[:10]}
    except Exception as e:  # noqa: BLE001
        _log.info("IA Insta: geração falhou conta=%s: %s", conta_id, e)
    return fallback


def _identidade(c, conta_id: int) -> dict:
    """Nome que aparece na arte. Usa razão social/nome da conta."""
    r = c.execute("select coalesce(nullif(razao_social,''), nome) from contas where id=%s",
                  (conta_id,)).fetchone()
    return {"empresa": (r[0] if r else "") or ""}


# ------------------------------------------------------------------ arte (Pillow)

def _fonte(tamanho: int, negrito: bool = False):
    """Fonte do sistema. Cai na default do Pillow se nenhuma existir (a arte fica
    feia mas o fluxo não quebra)."""
    from PIL import ImageFont
    caminhos = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf" if negrito
        else "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf" if negrito
        else "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
    ]
    for p in caminhos:
        try:
            if os.path.exists(p):
                return ImageFont.truetype(p, tamanho)
        except Exception:  # noqa: BLE001
            continue
    try:
        return ImageFont.load_default(tamanho)
    except Exception:  # noqa: BLE001
        return ImageFont.load_default()


def _quebrar(desenho, texto: str, fonte, largura_max: int) -> list[str]:
    """Quebra por palavra medindo de verdade (textlength), não por contagem de
    caractere — fonte proporcional engana."""
    linhas, atual = [], ""
    for palavra in (texto or "").split():
        teste = f"{atual} {palavra}".strip()
        if desenho.textlength(teste, font=fonte) <= largura_max or not atual:
            atual = teste
        else:
            linhas.append(atual)
            atual = palavra
    if atual:
        linhas.append(atual)
    return linhas


def arte_card(texto: str, indice: int, total: int, empresa: str = "",
              capa: bool = False) -> bytes:
    """Renderiza um card 1080x1080 e devolve JPEG. Capa usa fonte maior.

    O tamanho da fonte cai automaticamente até o texto caber na área útil — texto
    estourando o card é o defeito mais comum desse tipo de gerador."""
    from PIL import Image, ImageDraw

    img = Image.new("RGB", (LADO, LADO), FUNDO)
    d = ImageDraw.Draw(img)

    # faixa da marca no topo
    d.rectangle([0, 0, LADO, 14], fill=VERDE)

    margem = 96
    largura = LADO - 2 * margem
    tamanho = 86 if capa else 60
    linhas: list[str] = []
    while tamanho >= 30:
        fonte = _fonte(tamanho, negrito=capa)
        linhas = _quebrar(d, texto, fonte, largura)
        altura = len(linhas) * int(tamanho * 1.32)
        if altura <= LADO - 2 * margem - 120:
            break
        tamanho -= 6
    fonte = _fonte(tamanho, negrito=capa)

    alt_linha = int(tamanho * 1.32)
    y = (LADO - len(linhas) * alt_linha) // 2 - 20
    for ln in linhas:
        d.text((margem, y), ln, font=fonte, fill=BRANCO)
        y += alt_linha

    rodape = _fonte(30)
    if empresa:
        d.text((margem, LADO - margem - 14), empresa[:40], font=rodape, fill=VERDE)
    if total > 1 and not capa:
        marcador = f"{indice}/{total}"
        larg = d.textlength(marcador, font=rodape)
        d.text((LADO - margem - larg, LADO - margem - 14), marcador, font=rodape, fill=VERDE)

    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=88, optimize=True)
    return buf.getvalue()


def subir_arte(conteudo: bytes) -> str:
    """Sobe a arte pro Supabase Storage em 1080px e devolve a URL pública.

    Não reusa upload_foto.subir_foto de propósito: aquele reduz pra 700px (bom pra
    card de produto, ruim pro Instagram, que quer 1080)."""
    import httpx
    from finance.upload_foto import _config

    url, key, bucket = _config()
    nome = f"conteudo/{int(time.time())}_{uuid.uuid4().hex[:8]}.jpg"
    destino = f"{url}/storage/v1/object/{bucket}/{nome}"
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "image/jpeg",
               "x-upsert": "true", "Cache-Control": "max-age=31536000"}
    with httpx.Client(timeout=30.0) as c:
        r = c.post(destino, content=conteudo, headers=headers)
    if r.status_code >= 300:
        raise ValueError(f"Falha ao subir a arte (HTTP {r.status_code}).")
    return f"{url}/storage/v1/object/public/{bucket}/{nome}"


# ------------------------------------------------------------------ criar post

def criar_post(pool, conta_id: int, membro_id: int | None, formato: str,
               objetivo: str, tema: str, destino: str = "whatsapp",
               canais: str = "instagram", n_cards: int = 5) -> dict:
    """Gera o texto, renderiza as artes, sobe e grava o post em `revisar`.

    Devolve {"ok":True,"post_id":N} ou {"ok":False,"erro":...}. Nada é publicado
    aqui — publicar é decisão explícita do humano na tela de conferência."""
    formato = formato if formato in FORMATOS else "carrossel"
    destino = destino if destino in DESTINOS else "whatsapp"
    try:
        conteudo = gerar(pool, conta_id, formato, objetivo, tema, n_cards)
        with pool.connection() as c:
            empresa = _identidade(c, conta_id).get("empresa") or ""

        cards = conteudo["cards"] if formato == "carrossel" else conteudo["cards"][:1]
        total = len(cards)
        urls = []
        for i, texto in enumerate(cards, start=1):
            png = arte_card(texto, i, total, empresa=empresa, capa=(i == 1))
            urls.append((i, texto, subir_arte(png)))

        with pool.connection() as c:
            post_id = c.execute(
                """insert into conteudo_posts
                     (conta_id, criado_por, formato, objetivo, tema, legenda,
                      destino, canais, status, token)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,'revisar',%s) returning id""",
                (conta_id, membro_id, formato, objetivo, tema, conteudo["legenda"],
                 destino, canais, secrets.token_urlsafe(9))).fetchone()[0]
            for ordem, texto, url in urls:
                c.execute("""insert into conteudo_midias (post_id, ordem, url, texto)
                             values (%s,%s,%s,%s)""", (post_id, ordem, url, texto))
            c.commit()
        return {"ok": True, "post_id": post_id}
    except Exception as e:  # noqa: BLE001
        _log.warning("IA Insta: criar_post falhou conta=%s: %s: %s", conta_id, type(e).__name__, e)
        return {"ok": False, "erro": f"Não consegui gerar o post ({type(e).__name__})."}


# ------------------------------------------------------------------ credenciais

def credenciais(c, conta_id: int) -> dict:
    """Página do Facebook + token da conta. É o mesmo registro que a aba
    Comunicação já grava em canais_config (canal='messenger')."""
    r = c.execute("""select identificador, token from canais_config
                      where conta_id=%s and canal='messenger' and ativo""",
                  (conta_id,)).fetchone()
    if not r or not r[0] or not r[1]:
        return {"ok": False, "erro": "Conecte a Página do Facebook em Comunicação › Canais."}
    return {"ok": True, "page_id": r[0], "token": r[1]}


def diagnostico(pool, conta_id: int) -> dict:
    """Checagem que a tela de conferência mostra antes de publicar: token válido,
    conta do Instagram ligada e quota restante nas 24h."""
    with pool.connection() as c:
        cred = credenciais(c, conta_id)
    if not cred.get("ok"):
        return {"ok": False, "erro": cred["erro"]}
    ig = conta_ig_da_pagina(cred["page_id"], cred["token"])
    if not ig.get("ok"):
        return {"ok": False, "erro": ig["erro"]}
    lim = limite_publicacao(ig["ig_id"], cred["token"])
    return {"ok": True, "pagina": ig.get("pagina") or "", "ig_id": ig["ig_id"],
            "restante": lim.get("restante") if lim.get("ok") else None}


# ------------------------------------------------------------------ publicação

def publicar_post(pool, post_id: int) -> dict:
    """Publica um post. Idempotente no bom senso: se já está publicado, não repete."""

    with pool.connection() as c:
        p = c.execute(
            """select conta_id, formato, legenda, canais, status, coalesce(tentativas,0)
                 from conteudo_posts where id=%s""", (post_id,)).fetchone()
        if not p:
            return {"ok": False, "erro": "post não encontrado"}
        conta_id, formato, legenda, canais, status, tentativas = p
        if status == "publicado":
            return {"ok": True, "ja": True}
        midias = c.execute(
            """select id, ordem, url from conteudo_midias
                where post_id=%s order by ordem""", (post_id,)).fetchall()
        cred = credenciais(c, conta_id)
        c.execute("""update conteudo_posts set status='publicando',
                        tentativas=coalesce(tentativas,0)+1, atualizado_em=now()
                      where id=%s""", (post_id,))
        c.commit()

    def falhar(msg: str) -> dict:
        with pool.connection() as c:
            novo = "erro" if (tentativas + 1) >= _MAX_TENTATIVAS else "agendado"
            c.execute("""update conteudo_posts set status=%s, erro_msg=%s, atualizado_em=now()
                          where id=%s""", (novo, msg[:400], post_id))
            c.commit()
        _log.info("IA Insta: post %s falhou — %s", post_id, msg)
        return {"ok": False, "erro": msg}

    if not cred.get("ok"):
        return falhar(cred["erro"])
    if not midias:
        return falhar("o post não tem imagem")

    ig = conta_ig_da_pagina(cred["page_id"], cred["token"])
    if not ig.get("ok"):
        return falhar(ig["erro"])
    ig_id, token = ig["ig_id"], cred["token"]

    lim = limite_publicacao(ig_id, token)
    if lim.get("ok") and lim.get("restante", 1) <= 0:
        return falhar("cota de 100 publicações em 24h atingida — tente mais tarde")

    media_id = None
    canais_l = [x.strip() for x in (canais or "").split(",") if x.strip()]

    if "instagram" in canais_l:
        if formato == "carrossel" and len(midias) >= 2:
            filhos = []
            for (mid, _ordem, url) in midias[:MAX_CARROSSEL]:
                r = container_imagem(ig_id, token, url, item_carrossel=True)
                if not r.get("ok"):
                    return falhar(f"card {_ordem}: {r['erro']}")
                filhos.append(r["container_id"])
                with pool.connection() as c:
                    c.execute("update conteudo_midias set container_id=%s where id=%s",
                              (r["container_id"], mid))
                    c.commit()
            pai = container_carrossel(ig_id, token, filhos, legenda)
            if not pai.get("ok"):
                return falhar(pai["erro"])
            cont = pai["container_id"]
        else:
            r = container_imagem(ig_id, token, midias[0][2], legenda=legenda)
            if not r.get("ok"):
                return falhar(r["erro"])
            cont = r["container_id"]

        # carrossel costuma levar alguns segundos pra ficar FINISHED
        for _ in range(12):
            st = status_container(cont, token)
            if not st.get("ok"):
                break
            if st.get("status") == "FINISHED":
                break
            if st.get("status") in ("ERROR", "EXPIRED"):
                return falhar(f"a Meta rejeitou o container ({st.get('detalhe') or st.get('status')})")
            time.sleep(5)

        pub = publicar(ig_id, token, cont)
        if not pub.get("ok"):
            return falhar(pub["erro"])
        media_id = pub["media_id"]

    fb_id = None
    if "facebook" in canais_l:
        fb = publicar_facebook(cred["page_id"], token, legenda or "", url_imagem=midias[0][2])
        # Facebook falhar não derruba o post: o Instagram é o canal principal.
        fb_id = fb.get("post_id") if fb.get("ok") else None
        if not fb.get("ok"):
            _log.info("IA Insta: post %s — Facebook falhou: %s", post_id, fb.get("erro"))

    with pool.connection() as c:
        c.execute("""update conteudo_posts
                        set status='publicado', publicado_em=now(), ig_media_id=%s,
                            fb_post_id=%s, erro_msg=null, atualizado_em=now()
                      where id=%s""", (media_id, fb_id, post_id))
        c.commit()
    return {"ok": True, "media_id": media_id, "fb_post_id": fb_id}


def publicar_pendentes(pool) -> int:
    """Entrada do poller. Publica o que está agendado e vencido. Devolve quantos
    posts foram publicados nesta passada."""
    n = 0
    try:
        with pool.connection() as lockc:
            if not lockc.execute("select pg_try_advisory_lock(%s)", (_LOCK,)).fetchone()[0]:
                return 0
            try:
                with pool.connection() as c:
                    fila = c.execute(
                        """select id from conteudo_posts
                            where status='agendado' and agendado_para is not null
                              and agendado_para <= now()
                              and coalesce(tentativas,0) < %s
                            order by agendado_para limit %s""",
                        (_MAX_TENTATIVAS, _MAX_PASS)).fetchall()
                for (pid,) in fila:
                    r = publicar_post(pool, pid)
                    if r.get("ok"):
                        n += 1
            finally:
                lockc.execute("select pg_advisory_unlock(%s)", (_LOCK,))
                lockc.commit()
    except Exception as e:  # noqa: BLE001
        _log.info("IA Insta: publicar_pendentes falhou: %s: %s", type(e).__name__, e)
    return n

# ==================================================================== PARTE 3
# Rotas e templates da aba.
#
# GET  /painel/prospeccao/ia-insta                    dashboard (Lista)
# GET  /painel/prospeccao/ia-insta/diagnostico         AJAX de conexão
# GET  /painel/prospeccao/ia-insta/criar               wizard de criação (3 passos)
# GET  /painel/prospeccao/ia-insta/video-reels         preview — em breve
# GET  /painel/prospeccao/ia-insta/criacao-livre       preview — em breve
# GET  /painel/prospeccao/ia-insta/foto-profissional   preview — em breve
# POST /painel/prospeccao/ia-insta/gerar               gera post (chamado pelo wizard)
# GET  /painel/prospeccao/ia-insta/{post_id}           Conferir (passo 4 do wizard)
# POST /painel/prospeccao/ia-insta/{post_id}/legenda
# POST /painel/prospeccao/ia-insta/{post_id}/publicar
# POST /painel/prospeccao/ia-insta/{post_id}/agendar
# POST /painel/prospeccao/ia-insta/{post_id}/excluir
# GET  /p/{token}                                      link público (clique -> lead)
#
# As rotas estáticas (/criar, /video-reels, /criacao-livre, /foto-profissional)
# são registradas ANTES de /{post_id}: o path param não tem conversor de tipo na
# URL (só na assinatura Python), então o FastAPI casa a rota por posição — se
# /{post_id} viesse primeiro, "criar" tentaria virar int e voltaria 422 em vez
# de cair na rota certa.

OBJETIVOS_UI = [("atrair", "Atrair", "dor do dia a dia"),
                ("provar", "Provar", "caso real, número"),
                ("ofertar", "Ofertar", "preço e condição"),
                ("bastidor", "Bastidor", "rosto e rotina")]
STATUS_ROT = {"rascunho": "Rascunho", "revisar": "Falta conferir", "agendado": "Agendado",
              "publicando": "Publicando", "publicado": "Publicado", "erro": "Erro"}

# Copy das 3 telas de preview — mesma estrutura, texto diferente. Um dict só
# evita 3 templates quase idênticos (a diferença real é só o conteúdo).
_PREVIEWS = {
    "video-reels": {
        "titulo": "Editar Vídeo/Reels",
        "chave": "video",
        "headline": "Transforme qualquer vídeo num Reel que prende atenção",
        "sub": "A IA transcreve a fala, corta silêncio e hesitação, adiciona legenda "
               "animada e deixa pronto pra postar.",
        "upload_ico": "⬆️", "upload_h": "Arraste seu vídeo aqui",
        "upload_cap": "MP4, MOV ou WebM · máximo 500MB",
        "upload_meta": "Processamento leva de 1 a 3 minutos, dependendo da duração.",
        "upload_btn": "⬆️ Selecionar arquivo",
        "feats": [
            ("💬", "Legenda animada palavra por palavra", "Transcrição automática sincronizada com a fala."),
            ("⭐", "Frase de abertura nos 3 primeiros segundos", "Chamada inicial pra parar o scroll."),
            ("⚡", "Palavras de destaque em tela cheia", "Termos-chave em fonte grande e centralizada."),
            ("✂️", "Corte automático de hesitação", 'Remove pausas, "é", "hmm" e silêncios.'),
            ("🎞️", "Cor cinematográfica e zoom sutil", "Grading de cor e leve movimento no quadro."),
            ("📐", "Formato 9:16 pronto pro Reels", "Ajusta o vídeo pro vertical automaticamente."),
        ],
        "motivo": ("Precisa de transcrição de fala, corte automático de vídeo e "
                    "renderização de legenda animada — um pipeline de vídeo (ex.: ffmpeg + "
                    "transcrição), bem diferente do gerador de card em Pillow que já existe. "
                    "Processamento de 1 a 3 min por vídeo também pede fila assíncrona própria, "
                    "não só o poller atual."),
    },
    "criacao-livre": {
        "titulo": "Criação Livre",
        "chave": "livre",
        "headline": "Descreva sua imagem",
        "sub": "A IA gera a imagem a partir do que você descrever — sem precisar de card de texto.",
        "feats": [],
        "motivo": ("Pede um modelo de geração de imagem de verdade (tipo DALL·E, Imagen "
                    "ou Stable Diffusion). Hoje o motor só desenha card de texto sobre fundo "
                    "da marca; gerar imagem a partir de descrição livre é uma API paga à "
                    "parte, com custo por imagem."),
    },
    "foto-profissional": {
        "titulo": "Foto Profissional",
        "chave": "foto",
        "headline": "Foto Profissional",
        "sub": "Suba uma foto de pessoa ou produto e a IA transforma numa imagem "
               "profissional, com iluminação de estúdio e fundo limpo.",
        "upload_ico": "📷", "upload_h": "Selecionar foto",
        "upload_cap": "Foto de produto, vitrine ou equipe — o que for.",
        "upload_meta": "",
        "upload_btn": "⬆️ Selecionar foto",
        "feats": [],
        "motivo": ("É edição de imagem por IA (relighting + troca de fundo), outro tipo "
                    "de modelo, também de API paga à parte — diferente da publicação via "
                    "Graph API, que já está especificada."),
    },
}


# ------------------------------------------------------------------ helpers

def _post_da_conta(c, conta_id: int, post_id: int):
    return c.execute(
        """select id, formato, objetivo, tema, legenda, destino, canais, status,
                  agendado_para, publicado_em, ig_media_id, erro_msg, token, cliques, leads
             from conteudo_posts where id=%s and conta_id=%s""",
        (post_id, conta_id)).fetchone()


def _linha(r) -> dict:
    return {"id": r[0], "formato": r[1], "objetivo": r[2], "tema": r[3] or "",
            "legenda": r[4] or "", "destino": r[5], "canais": r[6] or "",
            "status": r[7], "status_rot": STATUS_ROT.get(r[7], r[7]),
            "agendado_para": r[8], "publicado_em": r[9], "ig_media_id": r[10],
            "erro_msg": r[11], "token": r[12], "cliques": r[13] or 0, "leads": r[14] or 0}


def _midias(c, post_id: int) -> list[dict]:
    rows = c.execute("""select ordem, url, texto from conteudo_midias
                         where post_id=%s order by ordem""", (post_id,)).fetchall()
    return [{"ordem": r[0], "url": r[1], "texto": r[2] or ""} for r in rows]


# ------------------------------------------------------------------ lista (dashboard)

@router.get("/painel/prospeccao/ia-insta", response_class=HTMLResponse)
def ia_insta(request: Request):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    conta_id = ctx["conta_id"]
    with get_pool().connection() as c:
        rows = c.execute(
            """select id, formato, objetivo, tema, legenda, destino, canais, status,
                      agendado_para, publicado_em, ig_media_id, erro_msg, token, cliques, leads
                 from conteudo_posts where conta_id=%s
                order by coalesce(publicado_em, agendado_para, criado_em) desc limit 40""",
            (conta_id,)).fetchall()
        tot = c.execute(
            """select count(*) filter (where status='publicado'),
                      coalesce(sum(cliques),0), coalesce(sum(leads),0)
                 from conteudo_posts
                where conta_id=%s and criado_em > now() - interval '30 days'""",
            (conta_id,)).fetchone()
        pessoa = None
        if ctx.get("membro_id"):
            r = c.execute("select coalesce(nullif(nome,''), email) from membros where id=%s",
                          (ctx["membro_id"],)).fetchone()
            pessoa = r[0] if r else None
    posts = [_linha(r) for r in rows]
    # a lista geral vem ordenada por atividade recente; pro widget de "próximo
    # agendamento" precisa da data mais PRÓXIMA (asc), não a mais recente.
    agendados = sorted([p for p in posts if p["status"] == "agendado"],
                        key=lambda p: p["agendado_para"] or datetime.max.replace(tzinfo=timezone.utc))
    handle = "".join(ch for ch in (ctx["conta"][2] or "").lower() if ch.isalnum()) or "suaempresa"
    return _render("ia_insta", request, titulo="IA Insta", secao_ativa="prospeccao",
                   posts=posts, pendentes=[p for p in posts if p["status"] in ("revisar", "erro")],
                   agendados=agendados, proximo_agendado=agendados[0] if agendados else None,
                   publicados=[p for p in posts if p["status"] == "publicado"][:10],
                   ideias=ideias_semana(), handle=handle,
                   pessoa_nome=(pessoa or "").split(" ")[0] if pessoa else "",
                   tot_pub=tot[0], tot_cliques=tot[1], tot_leads=tot[2],
                   gerencia=ctx["gerencia"], aviso=request.session.pop("insta_aviso", None))


@router.get("/painel/prospeccao/ia-insta/diagnostico")
def ia_insta_diagnostico(request: Request):
    """Checagem de conexão (token, conta do IG, cota de 24h) — chamada por AJAX
    pra não travar o carregamento da página quando a Meta está lenta."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    return JSONResponse(diagnostico(get_pool(), ctx["conta_id"]))


# ------------------------------------------------------------------ wizard: criar

@router.get("/painel/prospeccao/ia-insta/criar", response_class=HTMLResponse)
def ia_insta_criar(request: Request):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    return _render("ia_insta_criar", request, titulo="Criar post", secao_ativa="prospeccao",
                   ideias=ideias_semana(), objetivos=OBJETIVOS_UI,
                   handle="".join(ch for ch in (ctx["conta"][2] or "").lower() if ch.isalnum()) or "suaempresa")


# ------------------------------------------------------------------ previews (em breve)

@router.get("/painel/prospeccao/ia-insta/video-reels", response_class=HTMLResponse)
@router.get("/painel/prospeccao/ia-insta/criacao-livre", response_class=HTMLResponse)
@router.get("/painel/prospeccao/ia-insta/foto-profissional", response_class=HTMLResponse)
def ia_insta_preview(request: Request):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    chave = request.url.path.rsplit("/", 1)[-1]
    p = _PREVIEWS[chave]
    return _render("ia_insta_preview", request, titulo=p["titulo"], secao_ativa="prospeccao", p=p)


# ------------------------------------------------------------------ gerar

@router.post("/painel/prospeccao/ia-insta/gerar")
def ia_insta_gerar(request: Request, formato: str = Form("carrossel"),
                   objetivo: str = Form("atrair"), tema: str = Form(""),
                   destino: str = Form("whatsapp"), n_cards: int = Form(5),
                   no_instagram: str = Form(""), no_facebook: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    canais = ",".join([x for x, on in (("instagram", no_instagram), ("facebook", no_facebook)) if on])
    if not canais:
        canais = "instagram"
    r = criar_post(get_pool(), ctx["conta_id"], ctx.get("membro_id"), formato,
                      objetivo, tema, destino=destino, canais=canais, n_cards=n_cards)
    if not r.get("ok"):
        request.session["insta_aviso"] = r.get("erro") or "Não consegui gerar."
        return RedirectResponse("/painel/prospeccao/ia-insta/criar", status_code=303)
    return RedirectResponse(f"/painel/prospeccao/ia-insta/{r['post_id']}", status_code=303)


# ------------------------------------------------------------------ conferir (passo 4)

@router.get("/painel/prospeccao/ia-insta/{post_id}", response_class=HTMLResponse)
def ia_insta_post(request: Request, post_id: int):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    with get_pool().connection() as c:
        r = _post_da_conta(c, ctx["conta_id"], post_id)
        if not r:
            return RedirectResponse("/painel/prospeccao/ia-insta", status_code=303)
        midias = _midias(c, post_id)
    return _render("ia_insta_post", request, titulo="Conferir post", secao_ativa="prospeccao",
                   post=_linha(r), midias=midias,
                   aviso=request.session.pop("insta_aviso", None))


@router.post("/painel/prospeccao/ia-insta/{post_id}/legenda")
def ia_insta_legenda(request: Request, post_id: int, legenda: str = Form("")):
    """Salva a legenda editada sem recarregar a página (fetch no template)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    with get_pool().connection() as c:
        if not _post_da_conta(c, ctx["conta_id"], post_id):
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=404)
        c.execute("""update conteudo_posts set legenda=%s, atualizado_em=now()
                      where id=%s and conta_id=%s""",
                  (legenda[:2000], post_id, ctx["conta_id"]))
        c.commit()
    return JSONResponse({"ok": True})


# ------------------------------------------------------------------ publicar

@router.post("/painel/prospeccao/ia-insta/{post_id}/publicar")
def ia_insta_publicar(request: Request, post_id: int):
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    pool = get_pool()
    with pool.connection() as c:
        if not _post_da_conta(c, ctx["conta_id"], post_id):
            return JSONResponse({"ok": False, "erro": "escopo"}, status_code=404)
    r = publicar_post(pool, post_id)
    return JSONResponse(r)


@router.post("/painel/prospeccao/ia-insta/{post_id}/agendar")
def ia_insta_agendar(request: Request, post_id: int, quando: str = Form("")):
    """Agenda pro poller pegar. `quando` vem do input datetime-local (hora local do
    navegador); sem valor, agenda pra daqui a 1h."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    try:
        alvo = datetime.fromisoformat(quando).replace(tzinfo=timezone.utc) if quando \
            else datetime.now(timezone.utc) + timedelta(hours=1)
    except ValueError:
        alvo = datetime.now(timezone.utc) + timedelta(hours=1)
    with get_pool().connection() as c:
        if not _post_da_conta(c, ctx["conta_id"], post_id):
            return RedirectResponse("/painel/prospeccao/ia-insta", status_code=303)
        c.execute("""update conteudo_posts set status='agendado', agendado_para=%s,
                        tentativas=0, erro_msg=null, atualizado_em=now()
                      where id=%s and conta_id=%s""", (alvo, post_id, ctx["conta_id"]))
        c.commit()
    request.session["insta_aviso"] = "Post agendado."
    return RedirectResponse("/painel/prospeccao/ia-insta", status_code=303)


@router.post("/painel/prospeccao/ia-insta/{post_id}/excluir")
def ia_insta_excluir(request: Request, post_id: int):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    with get_pool().connection() as c:
        c.execute("""delete from conteudo_posts where id=%s and conta_id=%s
                      and status <> 'publicado'""", (post_id, ctx["conta_id"]))
        c.commit()
    request.session["insta_aviso"] = "Post removido."
    return RedirectResponse("/painel/prospeccao/ia-insta", status_code=303)


# ------------------------------------------------------------------ link público

@router.get("/p/{token}")
def ia_insta_clique(token: str):
    """Link que vai no post: conta o clique e manda pro WhatsApp da conta.

    Sem sessão e sem login — é rota pública. O incremento é best-effort: se a
    conta não tiver WhatsApp configurado, cai em /tenho-interesse."""
    pool = get_pool()
    with pool.connection() as c:
        r = c.execute("""select id, conta_id, destino from conteudo_posts
                          where token=%s""", (token,)).fetchone()
        if not r:
            return RedirectResponse("/", status_code=303)
        post_id, conta_id, destino = r
        c.execute("update conteudo_posts set cliques=coalesce(cliques,0)+1 where id=%s",
                  (post_id,))
        num = c.execute("""select identificador from canais_config
                            where conta_id=%s and canal='whatsapp' and ativo limit 1""",
                        (conta_id,)).fetchone()
        c.commit()
    if destino == "whatsapp" and num and num[0]:
        digitos = "".join(ch for ch in str(num[0]) if ch.isdigit())
        if digitos:
            return RedirectResponse(f"https://wa.me/{digitos}", status_code=303)
    return RedirectResponse("/tenho-interesse", status_code=303)


# ================================================================== templates

_NAV = _navbar("ia-insta")

# Cores locais dos pills/UI — hardcoded, mesmo padrão que o resto da Prospecção
# usa quando precisa de uma cor semântica que não está nas variáveis globais.
_EXTRA_CSS = """<style>
.ia-eyebrow{display:flex;align-items:center;gap:.5rem;flex-wrap:wrap}
.ia-badge-novo{font-size:.6rem;font-weight:800;letter-spacing:.04em;color:#b39bef;
  background:#221c38;padding:.1rem .4rem;border-radius:5px;margin-left:.4rem;vertical-align:1px}
.ia-greet{font-size:1.4rem;font-weight:700;margin:.3rem 0 0;letter-spacing:-.01em}
.ia-greet .ac{color:var(--verde-claro)}

.ia-ideas-bar{display:flex;gap:.55rem;overflow-x:auto;padding:.15rem .05rem .5rem;margin-top:.5rem}
.ia-idea-chip{flex:0 0 auto;display:flex;align-items:center;gap:.4rem;white-space:nowrap;font-size:.8rem;
  padding:.5rem .85rem;border-radius:999px;border:1px solid var(--borda);background:var(--card-2);
  color:var(--txt);cursor:pointer;font:inherit}
.ia-idea-chip:hover{border-color:var(--verde)}

.ia-stats{display:grid;grid-template-columns:repeat(3,1fr);gap:.7rem;margin:1.2rem 0 1.6rem}
.ia-stat{background:var(--card-2);border:1px solid var(--borda);border-radius:12px;padding:.85rem 1rem}
.ia-stat .n{font-size:1.6rem;font-weight:700;margin-top:.15rem;letter-spacing:-.02em}
.ia-stat.ac .n{color:var(--verde-claro)}

.ia-launch-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.7rem;margin-top:.6rem}
.ia-launch{display:block;border:1px solid var(--borda);border-radius:13px;padding:1rem;background:var(--card-2);
  cursor:pointer;text-align:left;text-decoration:none;color:inherit;font:inherit}
.ia-launch:hover{border-color:var(--verde)}
.ia-launch.soon{opacity:.5;cursor:not-allowed}
.ia-launch .ico{font-size:1.5rem}
.ia-launch .nm{font-weight:700;font-size:.92rem;margin-top:.5rem}
.ia-launch .cap{font-size:.76rem;color:var(--txt-mut);margin-top:.15rem}

.ia-tool-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:.6rem;margin-top:.6rem}
.ia-tool{border:1px dashed var(--borda);border-radius:12px;padding:.8rem .85rem;background:var(--card-2);
  opacity:.6;display:flex;align-items:center;gap:.6rem}
.ia-tool .ico{font-size:1.15rem}
.ia-tool .nm{font-size:.8rem;font-weight:600}
.ia-tool .cap{font-size:.66rem;color:var(--txt-mut);margin-top:.1rem}

.ia-agenda{border:1px solid var(--borda);border-radius:14px;padding:1.1rem 1.2rem;margin-top:1rem;background:var(--card-2)}
.ia-agenda-row{display:flex;align-items:center;justify-content:space-between;gap:1rem;padding:.4rem 0}
.ia-agenda-empty{text-align:center;color:var(--txt-mut);font-size:.84rem;padding:.4rem 0}

.ia-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.7rem}
.ia-card{background:var(--card-2);border:1px solid var(--borda);border-radius:12px;padding:.8rem}
.ia-card.on{border-color:var(--verde)}
.ia-row{display:flex;align-items:center;justify-content:space-between;gap:1rem;
        padding:.75rem 0;border-bottom:1px solid var(--borda)}
.ia-pill{font-size:.72rem;padding:.15rem .55rem;border-radius:999px;
         background:var(--card-2);color:var(--txt-mut)}
.ia-pill.rev{background:#2e2713;color:#e0b25a}
.ia-pill.pub{background:#12281e;color:#5fd39b}
.ia-pill.err{background:#3a1a1a;color:#f0917f}
.ia-thumb{width:100%;aspect-ratio:1;object-fit:cover;border-radius:9px;border:1px solid var(--borda)}
.ia-obj{cursor:pointer}
.fld{width:100%;padding:.55rem .7rem;border-radius:9px;border:1px solid var(--borda);
     background:var(--card);color:var(--txt);box-sizing:border-box}

/* ---- wizard (Criar) ---- */
.ia-stepper{display:flex;align-items:center;gap:.45rem;margin-top:1.3rem;flex-wrap:wrap}
.ia-step-pill{font-size:.78rem;font-weight:700;padding:.4rem .8rem;border-radius:999px;
  background:var(--card-2);color:var(--txt-mut);border:1px solid var(--borda)}
.ia-step-pill .n{display:inline-flex;width:1.15rem;height:1.15rem;border-radius:50%;background:var(--borda);
  color:var(--card);align-items:center;justify-content:center;font-size:.66rem;margin-right:.35rem}
.ia-step-pill.on{background:var(--verde);color:#06170f;border-color:var(--verde)}
.ia-step-pill.on .n{background:#06170f;color:var(--verde-claro)}
.ia-step-pill.muted{opacity:.55}
.ia-chev{color:var(--txt-mut);font-size:.8rem}

.ia-wiz-card{border:1px solid var(--borda);border-radius:14px;padding:1.2rem;background:var(--card-2);margin-top:.9rem}
.ia-wiz-card:first-child{margin-top:0}
.ia-wiz-h{font-size:.86rem;font-weight:700;display:flex;align-items:center;justify-content:space-between}
.ia-edit-link{font-size:.78rem;font-weight:600;color:var(--verde-claro);cursor:pointer;background:none;border:none;font-family:inherit}

.ia-fmt-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(112px,1fr));gap:.6rem;margin-top:.7rem}
.ia-fmt-tile{border:1px solid var(--borda);border-radius:12px;padding:.85rem .6rem;text-align:center;
  background:var(--card);cursor:pointer;position:relative}
.ia-fmt-tile input{position:absolute;opacity:0;inset:0;margin:0;cursor:pointer}
.ia-fmt-tile .ico{font-size:1.35rem}
.ia-fmt-tile .nm{font-size:.78rem;font-weight:600;margin-top:.4rem}
.ia-fmt-tile .cap{font-size:.66rem;color:var(--txt-mut);margin-top:.1rem}
.ia-fmt-tile.on{border-color:var(--verde);background:#12281e}
.ia-fmt-tile.on .nm{color:var(--verde-claro)}
.ia-fmt-tile.soon{opacity:.45;cursor:not-allowed}

.ia-chosen-pill{display:inline-flex;align-items:center;gap:.4rem;margin-top:.7rem;font-size:.85rem;font-weight:600;
  color:var(--verde-claro);background:#12281e;border:1px solid var(--verde);border-radius:9px;padding:.55rem .8rem}

.ia-start-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:.65rem;margin-top:.7rem}
.ia-start{border:1px solid var(--borda);border-radius:12px;padding:.9rem;background:var(--card);cursor:pointer;position:relative}
.ia-start input{position:absolute;opacity:0;inset:0;margin:0;cursor:pointer}
.ia-start .ico{font-size:1.2rem}
.ia-start b{display:block;font-size:.86rem;margin-top:.45rem}
.ia-start .cap{font-size:.74rem;color:var(--txt-mut);margin-top:.2rem;line-height:1.4}
.ia-start.on{border-color:var(--verde);box-shadow:0 0 0 1px var(--verde) inset}
.ia-start.soon{opacity:.55}
.ia-start.soon input{cursor:not-allowed}

/* ---- modal: story ---- */
.ia-modal-ov{display:none;position:fixed;inset:0;background:rgba(8,12,10,.6);z-index:50;
  align-items:center;justify-content:center;padding:1rem}
.ia-modal-ov.on{display:flex}
.ia-modal-box{background:var(--card);border-radius:16px;padding:1.3rem 1.4rem 1.5rem;max-width:420px;width:100%;
  border:1px solid var(--borda)}
.ia-modal-head{display:flex;align-items:center;justify-content:space-between}
.ia-modal-head h3{margin:0;font-size:1.02rem;font-weight:700}
.ia-modal-close{cursor:pointer;color:var(--txt-mut);font-size:1.15rem;line-height:1;padding:.1rem .3rem;
  background:none;border:none}
.ia-modal-opt{display:flex;align-items:center;gap:.8rem;border:1px solid var(--borda);border-radius:12px;
  padding:.85rem;margin-top:.8rem;background:var(--card-2);opacity:.85;cursor:not-allowed}
.ia-modal-opt .ico2{width:2.3rem;height:2.3rem;border-radius:10px;background:#12281e;color:var(--verde-claro);
  display:flex;align-items:center;justify-content:center;font-size:1.05rem;flex:0 0 auto}
.ia-modal-opt b{font-size:.86rem;display:block}
.ia-modal-opt .cap{font-size:.76rem;color:var(--txt-mut);margin-top:.15rem}
.ia-notice{font-size:.76rem;padding:.5rem .8rem;border-radius:9px;background:#2e2713;color:#e0b25a;
  display:inline-block;font-weight:600}

/* ---- previews (Vídeo/Livre/Foto) ---- */
.ia-prev-tt{font-size:1.3rem;font-weight:700;text-align:center;margin:1.2rem 0 .3rem;letter-spacing:-.01em}
.ia-prev-sub{font-size:.86rem;color:var(--txt-mut);text-align:center;max-width:520px;margin:0 auto;line-height:1.55}
.ia-upload{border:1.5px dashed var(--borda);border-radius:16px;padding:2.4rem 1.5rem;text-align:center;
  background:var(--card-2);max-width:520px;margin:1.4rem auto 0}
.ia-upload-ico{width:3.4rem;height:3.4rem;border-radius:50%;background:#12281e;color:var(--verde-claro);
  display:flex;align-items:center;justify-content:center;font-size:1.5rem;margin:0 auto}
.ia-upload h3{margin:.9rem 0 .25rem;font-size:1rem}
.ia-upload .cap2{font-size:.8rem;color:var(--txt-mut)}
.ia-upload .meta2{font-size:.72rem;color:var(--txt-mut);margin-top:.35rem}
.ia-feat-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:.7rem;margin-top:1.4rem}
.ia-feat{border:1px solid var(--borda);border-radius:12px;padding:.9rem;background:var(--card-2);display:flex;gap:.7rem}
.ia-feat b{font-size:.84rem;display:block}
.ia-feat .cap{font-size:.76rem;color:var(--txt-mut);margin-top:.2rem;line-height:1.4}
.ia-motivo{margin-top:2.2rem;padding-top:1.4rem;border-top:1px dashed var(--borda);
  font-size:.84rem;color:var(--txt-mut);line-height:1.6;max-width:640px;margin-left:auto;margin-right:auto}
.ia-motivo b{color:var(--txt)}
</style>"""

_LISTA_TPL = """{% extends "base" %}{% block conteudo %}""" + _CSS + _EXTRA_CSS + """
<div class="pw">""" + _NAV + """
  <div class="ia-eyebrow"><span class="mut" style="font-size:.68rem;letter-spacing:.09em;text-transform:uppercase">IA Insta</span>
    <span class="mut" style="font-size:.82rem" id="diag">verificando conexão…</span>
  </div>
  <h1 class="ia-greet">Olá{% if pessoa_nome %}, {{ pessoa_nome }}{% endif %} — o que vamos publicar no <span class="ac">@{{ handle }}</span> hoje?</h1>

  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}

  <div style="margin-top:1.2rem">
    <div class="mut" style="font-size:.68rem;letter-spacing:.09em;text-transform:uppercase">Ideias da semana<span class="ia-badge-novo">NOVO</span></div>
    <div class="ia-ideas-bar">
      {% for i in ideias %}
      <button type="button" class="ia-idea-chip" onclick="location.href='/painel/prospeccao/ia-insta/criar?tema='+encodeURIComponent('{{ i.texto }}')+'&objetivo={{ i.objetivo }}'">
        💡 {{ i.texto }}</button>
      {% endfor %}
    </div>
  </div>

  <div class="mut" style="font-size:.74rem;letter-spacing:.04em;margin:1.6rem 0 .3rem">CRIAR NOVO POST</div>
  <div class="ia-launch-grid">
    <a class="ia-launch" href="/painel/prospeccao/ia-insta/criar?formato=carrossel">
      <div class="ico">🎠</div><div class="nm">Carrossel</div>
      <div class="cap">educa em vários cards, o formato que mais vira lead</div>
    </a>
    <a class="ia-launch" href="/painel/prospeccao/ia-insta/criar?formato=post">
      <div class="ico">🖼️</div><div class="nm">Post</div>
      <div class="cap">imagem única, direto ao ponto</div>
    </a>
    <button type="button" class="ia-launch" onclick="document.getElementById('ia-story-modal').classList.add('on')" style="width:100%">
      <div class="ico">📱</div><div class="nm">Story</div>
      <div class="cap">preview — abre escolha de tipo</div>
    </button>
    <a class="ia-launch" href="/painel/prospeccao/ia-insta/video-reels">
      <div class="ico">🎬</div><div class="nm">Editar Vídeo/Reels</div>
      <div class="cap">preview — sobe vídeo, a IA edita</div>
    </a>
    <a class="ia-launch" href="/painel/prospeccao/ia-insta/criacao-livre">
      <div class="ico">🎨</div><div class="nm">Criação Livre</div>
      <div class="cap">preview — descreve, a IA gera a imagem</div>
    </a>
    <a class="ia-launch" href="/painel/prospeccao/ia-insta/foto-profissional">
      <div class="ico">📷</div><div class="nm">Foto Profissional</div>
      <div class="cap">preview — sobe foto, a IA retoca</div>
    </a>
    <div class="ia-launch soon"><div class="ico">📢</div><div class="nm">Anúncios</div><div class="cap">em breve</div></div>
  </div>

  <div class="mut" style="font-size:.74rem;letter-spacing:.04em;margin:1.6rem 0 .3rem">MAIS FERRAMENTAS</div>
  <div class="ia-tool-grid">
    <div class="ia-tool"><span class="ico">🎬</span><div><div class="nm">Roteiro de Reels</div><div class="cap">em breve</div></div></div>
    <div class="ia-tool"><span class="ico">📱</span><div><div class="nm">Roteiro de Stories</div><div class="cap">em breve</div></div></div>
    <div class="ia-tool"><span class="ico">✍️</span><div><div class="nm">Ideias de Bio &amp; Nome</div><div class="cap">em breve</div></div></div>
    <div class="ia-tool"><span class="ico">🔗</span><div><div class="nm">Página na Bio</div><div class="cap">em breve</div></div></div>
    <div class="ia-tool"><span class="ico">📊</span><div><div class="nm">Insights</div><div class="cap">em breve</div></div></div>
    <div class="ia-tool"><span class="ico">⚡</span><div><div class="nm">Automações</div><div class="cap">em breve</div></div></div>
  </div>

  <div class="mut" style="font-size:.74rem;letter-spacing:.04em;margin:1.6rem 0 .3rem">PRÓXIMOS AGENDAMENTOS</div>
  <div class="ia-agenda">
    {% if proximo_agendado %}
    <div class="ia-agenda-row">
      <div>
        <div style="font-weight:600;font-size:.9rem">{{ proximo_agendado.tema or 'Sem tema' }}</div>
        <div class="mut" style="font-size:.78rem">{{ proximo_agendado.canais }}</div>
      </div>
      <div class="mut" style="font-size:.82rem;white-space:nowrap">
        {{ proximo_agendado.agendado_para.strftime('%d/%m %H:%M') if proximo_agendado.agendado_para else '—' }}
      </div>
    </div>
    {% else %}
    <div class="ia-agenda-empty">Nenhum post pra agendar ainda.</div>
    {% endif %}
    <div style="text-align:center;margin-top:.8rem">
      <a class="pbtn ghost" href="/painel/prospeccao/ia-insta/criar">+ Agendar post</a>
    </div>
  </div>

  <div class="ia-stats">
    <div class="ia-stat"><div class="mut" style="font-size:.78rem">Publicados (30d)</div><div class="n">{{ tot_pub }}</div></div>
    <div class="ia-stat"><div class="mut" style="font-size:.78rem">Cliques</div><div class="n">{{ tot_cliques }}</div></div>
    <div class="ia-stat ac"><div class="mut" style="font-size:.78rem">Viraram lead</div><div class="n">{{ tot_leads }}</div></div>
  </div>

  {% if pendentes %}
  <div class="mut" style="font-size:.74rem;letter-spacing:.04em;margin:1.6rem 0 .3rem">FALTA CONFERIR</div>
  {% for p in pendentes %}
  <div class="ia-row">
    <div style="min-width:0">
      <a href="/painel/prospeccao/ia-insta/{{ p.id }}" style="color:var(--txt);font-weight:600">{{ p.tema or 'Sem tema' }}</a>
      <span class="ia-pill {% if p.status=='erro' %}err{% else %}rev{% endif %}">{{ p.status_rot }}</span>
      <div class="mut" style="font-size:.8rem">{{ p.formato }} · {{ p.objetivo }}{% if p.erro_msg %} · {{ p.erro_msg }}{% endif %}</div>
    </div>
    <a class="pbtn ghost" href="/painel/prospeccao/ia-insta/{{ p.id }}">Conferir ›</a>
  </div>
  {% endfor %}{% endif %}

  {% if publicados %}
  <div class="mut" style="font-size:.74rem;letter-spacing:.04em;margin:1.6rem 0 .3rem">PUBLICADOS — o que virou lead</div>
  {% for p in publicados %}
  <div class="ia-row">
    <div style="min-width:0">
      <b>{{ p.tema or 'Sem tema' }}</b>
      <div class="mut" style="font-size:.8rem">
        {{ p.publicado_em.strftime('%d/%m') if p.publicado_em else '' }} · {{ p.formato }} · {{ p.objetivo }}
      </div>
    </div>
    <div style="text-align:right;white-space:nowrap">
      <b>{{ p.cliques }} cliques</b>
      <div class="mut" style="font-size:.8rem">{{ p.leads }} lead(s)</div>
    </div>
  </div>
  {% endfor %}{% endif %}
</div>

<div class="ia-modal-ov" id="ia-story-modal" onclick="if(event.target===this)this.classList.remove('on')">
  <div class="ia-modal-box">
    <div class="ia-modal-head">
      <h3>Que tipo de story você quer criar?</h3>
      <button type="button" class="ia-modal-close" onclick="document.getElementById('ia-story-modal').classList.remove('on')">✕</button>
    </div>
    <div class="ia-modal-opt">
      <span class="ico2">📱</span>
      <div><b>Story Único</b><div class="cap">Um story de impacto, visual vertical e CTA direto pro WhatsApp.</div></div>
    </div>
    <div class="ia-modal-opt">
      <span class="ico2">🧩</span>
      <div><b>Sequência de Stories</b><div class="cap">3 a 5 stories seguidos pra contar uma história completa.</div></div>
    </div>
    <div style="text-align:center;margin-top:1rem">
      <span class="ia-notice">🧪 Preview — Story pede upload resumable de vídeo, fora do escopo do motor atual</span>
    </div>
  </div>
</div>

<script>
fetch('/painel/prospeccao/ia-insta/diagnostico').then(function(r){ return r.json(); }).then(function(d){
  var el = document.getElementById('diag');
  if (d.ok) {
    el.textContent = '● Conectado a ' + (d.pagina || 'sua Página')
      + (d.restante != null ? ' · restam ' + d.restante + ' publicações hoje' : '');
    el.style.color = 'var(--verde-claro)';
  } else {
    el.textContent = d.erro || 'sem conexão com a Meta';
    el.style.color = '#e0b25a';
  }
}).catch(function(){ document.getElementById('diag').textContent = ''; });
</script>
{% endblock %}"""

_CRIAR_TPL = """{% extends "base" %}{% block conteudo %}""" + _CSS + _EXTRA_CSS + """
<div class="pw">""" + _NAV + """
  <div style="display:flex;align-items:center;justify-content:space-between;gap:1rem;flex-wrap:wrap">
    <h2 class="tt">Criar novo conteúdo</h2>
    <a class="ia-edit-link" href="/painel/prospeccao/ia-insta">‹ Voltar pro painel</a>
  </div>

  <div class="ia-stepper" id="ia-stepper">
    <button type="button" class="ia-step-pill on" data-step="1" onclick="iaStepGo(1)"><span class="n">1</span>Tipo</button>
    <span class="ia-chev">›</span>
    <button type="button" class="ia-step-pill" data-step="2" onclick="iaStepGo(2)"><span class="n">2</span>Conteúdo</button>
    <span class="ia-chev">›</span>
    <button type="button" class="ia-step-pill" data-step="3" onclick="iaStepGo(3)"><span class="n">3</span>Título</button>
    <span class="ia-chev">›</span>
    <span class="ia-step-pill muted"><span class="n">4</span>Imagem</span>
  </div>

  <form method="post" action="/painel/prospeccao/ia-insta/gerar">
    <div id="ia-step-1">
      <div class="ia-wiz-card">
        <div class="ia-wiz-h">1 O que você quer criar?</div>
        <div class="ia-fmt-grid">
          <label class="ia-fmt-tile on" id="ia-fmt-carrossel">
            <input type="radio" name="formato" value="carrossel" checked onchange="iaFmtMarcar(this)">
            <div class="ico">🎠</div><div class="nm">Carrossel</div><div class="cap">vários cards</div>
          </label>
          <label class="ia-fmt-tile" id="ia-fmt-post">
            <input type="radio" name="formato" value="post" onchange="iaFmtMarcar(this)">
            <div class="ico">🖼️</div><div class="nm">Post</div><div class="cap">imagem única</div>
          </label>
          <div class="ia-fmt-tile soon"><div class="ico">📱</div><div class="nm">Story Único</div><div class="cap">em breve</div></div>
          <div class="ia-fmt-tile soon"><div class="ico">🎞️</div><div class="nm">Sequência</div><div class="cap">em breve</div></div>
        </div>
        <div style="text-align:right;margin-top:1rem">
          <button type="button" class="pbtn" onclick="iaStepGo(2)">Continuar ›</button>
        </div>
      </div>
    </div>

    <div id="ia-step-2" style="display:none">
      <div class="ia-wiz-card">
        <div class="ia-wiz-h">1 O que você quer criar? <button type="button" class="ia-edit-link" onclick="iaStepGo(1)">Editar</button></div>
        <div class="ia-chosen-pill" id="ia-chosen-fmt">✓ Carrossel</div>
      </div>
      <div class="ia-wiz-card">
        <div class="ia-wiz-h">2 Como você quer começar?</div>
        <div class="ia-start-grid">
          <label class="ia-start on">
            <input type="radio" name="modo" value="manual" checked onchange="iaModoMarcar(this)">
            <div class="ico">✍️</div><b>Já sei o que criar</b><div class="cap">Descreva o tema, a IA escreve o post</div>
          </label>
          <label class="ia-start">
            <input type="radio" name="modo" value="ideias" onchange="iaModoMarcar(this)">
            <div class="ico">✨</div><b>Gerar ideias</b><div class="cap">Escolher uma das ideias da semana</div>
          </label>
          <label class="ia-start soon">
            <input type="radio" name="modo" value="link" disabled>
            <div class="ico">🔗</div><b>A partir de um link</b><div class="cap">Cole o link de uma notícia ou artigo · em breve</div>
          </label>
        </div>
        <div style="text-align:right;margin-top:1rem">
          <button type="button" class="pbtn" onclick="iaStepGo(3)">Continuar ›</button>
        </div>
      </div>
    </div>

    <div id="ia-step-3" style="display:none">
      <div class="ia-wiz-card" id="ia-ideas-row" style="display:none">
        <div class="ia-wiz-h">Ideias da semana</div>
        <div class="ia-ideas-bar" style="margin-top:.5rem">
          {% for i in ideias %}
          <button type="button" class="ia-idea-chip" data-obj="{{ i.objetivo }}"
                  onclick="iaEscolherIdeia(this, '{{ i.texto }}')">💡 {{ i.texto }}</button>
          {% endfor %}
        </div>
      </div>

      <div class="ia-wiz-card">
        <div class="ia-wiz-h">3 Dê o tema do post</div>
        <div class="field" style="margin-top:.6rem">
          <label class="mut" style="font-size:.8rem;display:block;margin-bottom:.3rem">Sobre o quê</label>
          <input class="fld" name="tema" id="ia-tema" placeholder="Ex.: mercadinho que ainda anota fiado no caderno" required>
        </div>
        <div style="display:flex;gap:.7rem;flex-wrap:wrap;margin-top:.7rem">
          <div style="flex:1;min-width:110px" id="ia-cards-field">
            <label class="mut" style="font-size:.8rem">Cards</label>
            <input class="fld" type="number" name="n_cards" value="5" min="2" max="10">
          </div>
          <div style="flex:1;min-width:150px">
            <label class="mut" style="font-size:.8rem">Destino do clique</label>
            <select class="fld" name="destino">
              <option value="whatsapp">Botão WhatsApp — vira lead</option>
              <option value="link">Link /tenho-interesse</option>
              <option value="nenhum">Nenhum</option>
            </select>
          </div>
        </div>
        <div style="margin-top:.7rem">
          <label class="mut" style="font-size:.8rem">Objetivo</label>
          <div class="ia-grid" style="margin-top:.35rem" id="ia-obj-grid">
            {% for val, rot, sub in objetivos %}
            <label class="ia-card ia-obj {% if loop.first %}on{% endif %}">
              <input type="radio" name="objetivo" value="{{ val }}" {% if loop.first %}checked{% endif %}
                     style="margin-right:.4rem" onchange="iaObjMarcar(this)">
              <b>{{ rot }}</b><div class="mut" style="font-size:.76rem">{{ sub }}</div>
            </label>
            {% endfor %}
          </div>
        </div>
        <div style="margin-top:.7rem">
          <label class="mut" style="font-size:.8rem">Publicar em</label><br>
          <label style="margin-right:1rem"><input type="checkbox" name="no_instagram" value="1" checked> Instagram</label>
          <label><input type="checkbox" name="no_facebook" value="1"> Facebook</label>
        </div>
        <div style="text-align:right;margin-top:1rem">
          <button class="pbtn">✨ Gerar imagens ›</button>
        </div>
        <div class="mut" style="font-size:.74rem;margin-top:.4rem;text-align:right">
          Passo 4 (Imagem) é a tela de conferência — nada vai pro ar sem você revisar lá.
        </div>
      </div>
    </div>
  </form>
</div>

<script>
var IA_URL = new URLSearchParams(location.search);
if (IA_URL.get('formato') === 'post') {
  document.querySelector('input[name=formato][value=post]').checked = true;
  iaFmtMarcar(document.querySelector('input[name=formato][value=post]'));
}
if (IA_URL.get('tema')) { document.getElementById('ia-tema').value = IA_URL.get('tema'); }
if (IA_URL.get('objetivo')) {
  var r = document.querySelector('input[name=objetivo][value="' + IA_URL.get('objetivo') + '"]');
  if (r) { r.checked = true; iaObjMarcar(r); }
}

function iaStepGo(n){
  [1,2,3].forEach(function(i){
    document.getElementById('ia-step-'+i).style.display = (i===n) ? '' : 'none';
    document.querySelector('.ia-step-pill[data-step="'+i+'"]').classList.toggle('on', i===n);
  });
}
function iaFmtMarcar(el){
  document.querySelectorAll('.ia-fmt-tile').forEach(function(t){ t.classList.remove('on'); });
  el.closest('.ia-fmt-tile').classList.add('on');
  var nome = el.value === 'post' ? 'Post' : 'Carrossel';
  document.getElementById('ia-chosen-fmt').textContent = '✓ ' + nome;
  document.getElementById('ia-cards-field').style.display = el.value === 'post' ? 'none' : '';
}
function iaModoMarcar(el){
  document.querySelectorAll('.ia-start').forEach(function(c){ c.classList.remove('on'); });
  el.closest('.ia-start').classList.add('on');
  document.getElementById('ia-ideas-row').style.display = el.value === 'ideias' ? '' : 'none';
}
function iaEscolherIdeia(btn, texto){
  document.getElementById('ia-tema').value = texto;
  var obj = btn.dataset.obj;
  var r = document.querySelector('input[name=objetivo][value="'+obj+'"]');
  if (r) { r.checked = true; iaObjMarcar(r); }
}
function iaObjMarcar(el){
  document.querySelectorAll('.ia-obj').forEach(function(c){ c.classList.remove('on'); });
  el.closest('.ia-obj').classList.add('on');
}
</script>
{% endblock %}"""

_POST_TPL = """{% extends "base" %}{% block conteudo %}""" + _CSS + _EXTRA_CSS + """
<div class="pw">""" + _NAV + """
  <h2 class="tt">Conferir post <span class="mut" style="font-weight:500">— passo 4 · Imagem</span></h2>
  <div class="mut" style="font-size:.82rem;margin-top:.15rem">
    {{ post.formato|capitalize }} · {{ midias|length }} card(s) · objetivo {{ post.objetivo }}
    <span class="ia-pill {% if post.status=='publicado' %}pub{% elif post.status=='erro' %}err{% else %}rev{% endif %}">{{ post.status_rot }}</span>
  </div>

  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}
  {% if post.erro_msg %}<div class="err" style="margin-top:.8rem">{{ post.erro_msg }}</div>{% endif %}

  <div class="ia-grid" style="margin-top:1rem">
    {% for m in midias %}
    <div><img class="ia-thumb" src="{{ m.url }}" alt="Card {{ m.ordem }}"></div>
    {% endfor %}
  </div>

  <div class="fsec" style="margin-top:1.2rem">
    <label class="mut" style="font-size:.8rem">Legenda — editável</label>
    <textarea class="fld" id="legenda" rows="5">{{ post.legenda }}</textarea>
    <button type="button" class="pbtn ghost" style="margin-top:.5rem" onclick="salvarLegenda()">Salvar legenda</button>
    <span id="leg-ok" class="mut" style="font-size:.8rem;margin-left:.5rem"></span>
  </div>

  {% if post.status != 'publicado' %}
  <div style="display:flex;gap:.5rem;flex-wrap:wrap;margin-top:1.2rem;align-items:center">
    <button type="button" class="pbtn" id="btn-pub" onclick="publicar()">Publicar agora</button>

    <form method="post" action="/painel/prospeccao/ia-insta/{{ post.id }}/agendar"
          style="display:flex;gap:.4rem;align-items:center;margin:0">
      <input class="fld" type="datetime-local" name="quando" style="width:auto">
      <button class="pbtn ghost">Agendar</button>
    </form>

    <form method="post" action="/painel/prospeccao/ia-insta/{{ post.id }}/excluir" style="margin:0"
          onsubmit="return confirm('Apagar este post?')">
      <button class="pbtn ghost">Apagar</button>
    </form>
  </div>
  <div class="mut" style="font-size:.74rem;margin-top:.5rem">
    Carrossel: todas as imagens são cortadas pro aspecto da primeira. As artes já saem 1:1.
  </div>
  {% else %}
  <div class="ok" style="margin-top:1.2rem">
    Publicado em {{ post.publicado_em.strftime('%d/%m/%Y %H:%M') if post.publicado_em }}.
    {{ post.cliques }} clique(s) · {{ post.leads }} lead(s).
  </div>
  {% endif %}

  <div style="margin-top:1.2rem"><a class="pbtn ghost" href="/painel/prospeccao/ia-insta">‹ Voltar</a></div>
</div>

<script>
function salvarLegenda(){
  var fd = new FormData(); fd.append('legenda', document.getElementById('legenda').value);
  fetch('/painel/prospeccao/ia-insta/{{ post.id }}/legenda', {method:'POST', body:fd})
    .then(function(r){ return r.json(); })
    .then(function(d){ document.getElementById('leg-ok').textContent = d.ok ? 'salvo' : 'não salvou'; });
}
function publicar(){
  var b = document.getElementById('btn-pub');
  b.disabled = true; b.textContent = 'Publicando…';
  fetch('/painel/prospeccao/ia-insta/{{ post.id }}/publicar', {method:'POST'})
    .then(function(r){ return r.json(); })
    .then(function(d){
      if (d.ok) { location.href = '/painel/prospeccao/ia-insta'; }
      else { b.disabled = false; b.textContent = 'Publicar agora'; alert(d.erro || 'Não consegui publicar.'); }
    })
    .catch(function(){ b.disabled = false; b.textContent = 'Publicar agora'; });
}
</script>
{% endblock %}"""

_PREVIEW_TPL = """{% extends "base" %}{% block conteudo %}""" + _CSS + _EXTRA_CSS + """
<div class="pw">""" + _NAV + """
  <div style="display:flex;align-items:center;justify-content:space-between;gap:1rem;flex-wrap:wrap">
    <h2 class="tt">{{ p.titulo }}</h2>
    <a class="ia-edit-link" href="/painel/prospeccao/ia-insta">‹ Voltar pro painel</a>
  </div>
  <div style="text-align:center;margin-top:.6rem"><span class="ia-notice">🧪 Preview — tela de referência, motor ainda não existe</span></div>

  <div class="ia-prev-tt">{{ p.headline }}</div>
  <div class="ia-prev-sub">{{ p.sub }}</div>

  {% if p.upload_h %}
  <div class="ia-upload">
    <div class="ia-upload-ico">{{ p.upload_ico }}</div>
    <h3>{{ p.upload_h }}</h3>
    <div class="cap2">{{ p.upload_cap }}</div>
    <div style="margin-top:1rem"><span class="pbtn" style="opacity:.6;cursor:not-allowed">{{ p.upload_btn }}</span></div>
    {% if p.upload_meta %}<div class="meta2">{{ p.upload_meta }}</div>{% endif %}
  </div>
  {% endif %}

  {% if p.feats %}
  <div class="ia-prev-tt" style="font-size:1.05rem;margin-top:2rem">O que a IA faria</div>
  <div class="ia-feat-grid">
    {% for ico, tit, cap in p.feats %}
    <div class="ia-feat"><span>{{ ico }}</span><div><b>{{ tit }}</b><div class="cap">{{ cap }}</div></div></div>
    {% endfor %}
  </div>
  {% endif %}

  <div class="ia-motivo">
    <b>Por que essa tela ainda é preview:</b> {{ p.motivo }}
  </div>
</div>
{% endblock %}"""

_env.loader.mapping["ia_insta"] = _LISTA_TPL
_env.loader.mapping["ia_insta_criar"] = _CRIAR_TPL
_env.loader.mapping["ia_insta_post"] = _POST_TPL
_env.loader.mapping["ia_insta_preview"] = _PREVIEW_TPL
