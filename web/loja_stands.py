"""Página pública de venda de estandes: /e/<slug> (migração 448, finance/evento_stands.py).

SLUG SEM ANO (pedido do dono, 28/09/2026): o link é da EMPRESA, não da edição
— ver o comentário de evento_stands_config em db/migracoes/448_evento_stands.sql.

Resolve a conta pelo slug do MESMO jeito que web/portal.loja_fornecedor (/f/{slug})
já faz — mas o "catálogo" aqui é outra coisa (estande numerado com posição na
planta, não produto de prateleira), então nada do MODELO de dados da loja de
fornecedor é reaproveitado, só o padrão de rota pública sem login.

O template é o PORT FIEL da maquete aprovada pelas sócias (visão do expositor
de scratchpad/outlet-chic-mockup.html, 28/09/2026): abas de pavilhão, mapa 3D
com perspectiva + planta técnica, zoom, cor por status OU por tamanho (a mesma
legenda de cores da planta oficial do PDF), chips de zona e painel lateral com
a foto do modelo de stand. A ÚNICA diferença é a fonte dos dados: status/preço
vêm do banco, e o upload de comprovante é um formulário de verdade (multipart
pro POST abaixo) em vez do clique fake da maquete.

As POSIÇÕES no grid (col/row de cada bloco da planta) moram no JS do template,
não no banco: são a transcrição manual da planta oficial do evento, feita na
maquete e aprovada — o banco guarda o que MUDA (status, preço), a planta é
desenho. Estande no banco que não esteja na planta desenhada (ou pavilhão de
outra conta) cai num bloco corrido no fim do pavilhão/numa aba própria, pra
página continuar multi-tenant sem exigir coordenadas.
"""
from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timezone

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from db.conexao import get_pool
from finance import comprovantes as comprov
from finance import evento_stands as es
from web.portal import _env, brl

router = APIRouter()
_log = logging.getLogger("openclaw.loja_stands")

_TPL_NOME = "evento_stands_publico.html"


def _data_br(d) -> str:
    return d.strftime("%d/%m/%Y") if d else ""


def _dias_restantes(ate) -> int | None:
    """Dias que faltam pra pré-reserva expirar — arredondado pra CIMA (o
    expositor lê '3 dias' no dia em que enviou, como a config promete)."""
    if not ate:
        return None
    try:
        # `ate` ingênuo vem de coluna sem fuso, gravada em UTC (o banco fala UTC)
        agora = datetime.now(timezone.utc)
        if not ate.tzinfo:
            agora = agora.replace(tzinfo=None)
        seg = (ate - agora).total_seconds()
        return max(0, math.ceil(seg / 86400))
    except Exception:  # noqa: BLE001
        return None


def _criar_prospeccao_simples(pool, conta_id: int, nome: str, whatsapp: str,
                              vendedor: str = ""):
    """Registro MÍNIMO do interessado (nome/whatsapp) na tabela de CRM
    (prospeccao, migração 075) — pra que `evento_stands.prospeccao_id` aponte
    pra algo navegável no Funil, em vez de ficar solto. Não é o fluxo de
    inbound completo (aquele trata mensagem dentro de uma conversa que já
    existe); aqui o visitante da página ainda não conversou com ninguém.

    `vendedor` (o `v` do link de vendas) vincula a venda a quem mandou o link —
    é o código ASSINADO (es.codigo_vendedor), validado contra os membros da
    conta antes de gravar (es.vendedor_do_codigo), pra o `v` da URL não virar
    porta de fraude de comissão.

    Best-effort e SILENCIOSO: o upload do comprovante — a parte que importa —
    não pode falhar por causa de um cadastro de lead que é só um bônus."""
    nome = (nome or "").strip()
    if not nome:
        return None
    try:
        vend = es.vendedor_do_codigo(pool, conta_id, vendedor)
        vid = vend["id"] if vend else None
        with pool.connection() as c:
            pid = c.execute(
                """insert into prospeccao (conta_id, empresa, whatsapp, status,
                                           origem, vendedor_id)
                   values (%s,%s,%s,'novo','pagina_stands',%s) returning id""",
                (conta_id, nome[:200], (whatsapp or "").strip()[:40] or None, vid)
            ).fetchone()[0]
            c.commit()
        return pid
    except Exception as e:  # noqa: BLE001
        _log.info("loja_stands: não deu pra registrar o interessado: %s: %s",
                  type(e).__name__, e)
        return None


@router.get("/e/{slug}", response_class=HTMLResponse)
def loja_stands(request: Request, slug: str):
    pool = get_pool()
    cfg = es.buscar_config_por_slug(pool, slug)
    if cfg is None:
        return HTMLResponse("<h1>Página não encontrada</h1>", status_code=404)
    conta_id = cfg["conta_id"]
    stands = es.listar(pool, conta_id)

    # A marca no hero vem do SLUG ("outlet-chic" -> "OUTLET CHIC"), não de
    # contas.nome: o cadastro guarda a razão social ("M.R. ROCHA AURELIO
    # ASSESSORIA..."), e o slug é o nome público que o dono escolheu.
    marca = cfg["slug"].replace("-", " ").upper()

    # SÓ o que é público vai pro JS (nunca comprovante_url/prospeccao_id/etc):
    # é este objeto que abastece mapa e painel de detalhe. Status traduzido pro
    # vocabulário da maquete (pre_reservado -> 'reservado', classes .st-*).
    # o nome da empresa só aparece pra stand vendido COM contrato assinado
    try:
        expositores = es.expositores_publicos(pool, conta_id, stands)
    except Exception as e:  # noqa: BLE001 — sem o nome a página continua servindo
        _log.info("loja_stands: sem os nomes dos expositores: %s: %s", type(e).__name__, e)
        expositores = {}
    stands_json = json.dumps({
        s["codigo"]: {
            "pavilhao": s["pavilhao"], "zona": s["zona"] or "",
            "tamanho": s["tamanho"],
            "status": "reservado" if s["status"] == "pre_reservado" else s["status"],
            "preco": brl(s["preco_centavos"]) if s["preco_centavos"] else None,
            "dias": _dias_restantes(s["pre_reserva_ate"]) if s["status"] == "pre_reservado" else None,
            "expositor": expositores.get(s["codigo"]),
            "preco_centavos": s["preco_centavos"],
        }
        for s in stands
    })

    msg = request.query_params.get("msg") or ""
    msg_codigo = (request.query_params.get("codigo") or "")[:20]
    ct = (request.query_params.get("ct") or "")[:64] if msg == "ok" else ""
    # ?stand=G58 abre a página já com o stand selecionado — é o link que o
    # vendedor manda pro interessado a partir do mapa do painel/cockpit.
    # ?v=<código> é o LINK DE VENDAS de um vendedor: o cliente escolhe qualquer
    # stand e a venda que sair por aqui fica na conta dele. O código é
    # assinado (es.codigo_vendedor); só um código VÁLIDO chega ao form, e o
    # POST revalida — a URL sozinha não é confiável.
    stand_link = (request.query_params.get("stand") or "")[:20]
    v_raw = "".join(ch for ch in (request.query_params.get("v") or "")[:40]
                    if ch.isalnum() or ch == "-")
    vend = es.vendedor_do_codigo(pool, conta_id, v_raw) if v_raw else None
    vendedor_link = v_raw if vend else ""
    vendedor_nome = (vend or {}).get("nome") or ""
    reg = es.regras_de_pagamento(cfg)
    regras_json = json.dumps({
        "minimo": reg["sinal_minimo_centavos"], "max": reg["max_por_empresa"],
        "saldoAte": reg["saldo_ate"].strftime("%d/%m") if reg["saldo_ate"] else ""})
    c2_raw = (request.query_params.get("c2") or "")[:20] if msg == "ok" else ""
    html = _env.get_template(_TPL_NOME).render(
        regras_json=regras_json,
        just_sent_codes_json=json.dumps([x for x in (msg_codigo, c2_raw) if x]
                                        if msg == "ok" else []),
        cfg=cfg, marca=marca, n_total=len(stands), stands_json=stands_json,
        data_br=_data_br, msg=msg, msg_codigo=msg_codigo, ct=ct,
        vendedor_link=vendedor_link, vendedor_nome=vendedor_nome,
        ct_json=json.dumps(ct or None), stand_link_json=json.dumps(stand_link or None),
        # injeção segura no JS: sempre via json.dumps, nunca string crua
        pix_json=json.dumps({"chave": cfg["pix_chave"], "titular": cfg["pix_titular"]}),
        wa_json=json.dumps(cfg["whatsapp_numero"]),
        just_sent_json=json.dumps(msg_codigo if msg == "ok" else None),
        erro_codigo_json=json.dumps(msg_codigo if msg == "erro" else None),
        sem_storage=not comprov.configurado())
    return HTMLResponse(html)


@router.post("/e/{slug}/comprovante")
async def loja_stands_comprovante(request: Request, slug: str,
                                  codigo: str = Form(...), nome: str = Form(""),
                                  whatsapp: str = Form(""), vendedor: str = Form(""),
                                  codigo2: str = Form(""), sinal: str = Form(""),
                                  arquivo: UploadFile = File(...)):
    """Recebe o comprovante do sinal — é ESTE POST que, no modo 'pagamento',
    trava o estande (livre -> pre_reservado). Sem login: qualquer visitante da
    página pode mandar, pro modo mais rápido possível de reservar (é o pedido
    do dono: concorrência real entre interessados até o Pix cair).

    Só o READ do arquivo fica no event loop (é I/O assíncrono de verdade); o
    resto — resolver slug, criar prospecção, subir e registrar — é psycopg
    SÍNCRONO, e por isso roda na THREADPOOL. Mesmo motivo dos webhooks do
    wa-qr (web/painel_prospeccao.py): handler async fazendo banco síncrono
    trava o worker inteiro a cada requisição, painel incluso — pego pelo
    tests/test_event_loop_nao_trava.py antes de chegar em produção."""
    conteudo = await arquivo.read()
    return await run_in_threadpool(
        _loja_stands_comprovante_sync, slug, codigo, nome, whatsapp, vendedor,
        conteudo, arquivo.content_type or "", codigo2, sinal)


def _centavos(txt: str) -> int | None:
    """'3000', '3.000,00', '1500,50' -> centavos; ilegível -> None."""
    t = (txt or "").strip().replace("R$", "").replace(" ", "")
    if not t:
        return None
    t = t.replace(".", "").replace(",", ".")
    try:
        return round(float(t) * 100)
    except ValueError:
        return None


def _loja_stands_comprovante_sync(slug: str, codigo: str, nome: str, whatsapp: str,
                                  vendedor: str, conteudo: bytes, content_type: str,
                                  codigo2: str = "", sinal: str = ""):
    pool = get_pool()
    cfg = es.buscar_config_por_slug(pool, slug)
    if cfg is None:
        return HTMLResponse("<h1>Página não encontrada</h1>", status_code=404)
    conta_id = cfg["conta_id"]
    codigo = (codigo or "").strip()
    if not codigo:
        return RedirectResponse(f"/e/{slug}?msg=erro_generico", status_code=303)

    # nome fantasia + WhatsApp são o cadastro que nasce da reserva: sem os dois a
    # equipe não teria como chamar o cliente pra fechar o contrato (a checagem do
    # navegador é só conforto — quem posta direto cai aqui)
    if not (nome or "").strip() or len("".join(ch for ch in (whatsapp or "") if ch.isdigit())) < 10:
        return RedirectResponse(f"/e/{slug}?msg=erro_dados&codigo={codigo}", status_code=303)

    # 1 ou 2 estandes da mesma empresa, com o sinal mínimo por estande. A checagem
    # vem ANTES de criar a prospecção e de subir o arquivo: reserva recusada não
    # deixa lead órfão nem comprovante solto no bucket.
    codigos = [codigo] + ([codigo2.strip()] if (codigo2 or "").strip() else [])
    sinal_c = _centavos(sinal)
    v = es.validar_reserva(pool, conta_id, codigos, whatsapp,
                           sinal_c if sinal_c is not None else None)
    if not v["ok"]:
        return RedirectResponse(
            f"/e/{slug}?msg=erro_{v.get('cod') or 'reserva'}&codigo={codigo}", status_code=303)

    prospeccao_id = _criar_prospeccao_simples(pool, conta_id, nome, whatsapp, vendedor)
    r = es.subir_e_registrar_comprovante(pool, conta_id, codigo, conteudo, content_type,
                                         prospeccao_id=prospeccao_id,
                                         junto_com=codigos[1:], sinal_centavos=v["sinal"])
    if not r["ok"]:
        _log.info("loja_stands: comprovante recusado (%s/%s): %s", conta_id, codigo,
                  r.get("erro"))
        return RedirectResponse(f"/e/{slug}?msg=erro&codigo={codigo}", status_code=303)
    destino = f"/e/{slug}?msg=ok&codigo={codigo}"
    if len(codigos) > 1:
        destino += f"&c2={codigos[1]}"
    # o contrato nasceu junto com o comprovante (evento_stands.garantir_orcamento_
    # e_contrato) — o token vai na URL pra página oferecer "assinar agora".
    if r.get("contrato_token"):
        destino += f"&ct={r['contrato_token']}"
    return RedirectResponse(destino, status_code=303)


# ─────────────────────────────────────────────────────────────────────────
# Fotos do modelo de stand (renders do PDF oficial, recortadas) — mesmo padrão
# da rota de fontes em web/app.py: NOME FIXO em whitelist (join direto com
# caminho livre deixaria `../../` chegar em qualquer arquivo), cache em
# memória, imutável (se a arte mudar, muda o nome).
# ─────────────────────────────────────────────────────────────────────────
_FOTOS_OK = {"2x2", "3x2", "3x3", "4x2", "4x3"}
_FOTOS_DIR = os.path.join(os.path.dirname(__file__), "estatico", "stands")
_fotos_cache: dict[str, bytes] = {}


@router.get("/estatico/stands/{nome}.jpg", include_in_schema=False)
def foto_stand(nome: str):
    if nome not in _FOTOS_OK:
        return Response(status_code=404)
    if nome not in _fotos_cache:
        try:
            with open(os.path.join(_FOTOS_DIR, f"{nome}.jpg"), "rb") as fh:
                _fotos_cache[nome] = fh.read()
        except OSError:
            return Response(status_code=404)
    return Response(content=_fotos_cache[nome], media_type="image/jpeg",
                    headers={"Cache-Control": "public, max-age=31536000, immutable"})


# ─────────────────────────────────────────────────────────────────────────
# A PLANTA DESENHADA (posições col/row de cada bloco no grid de 24 colunas):
# transcrição manual da planta oficial do evento, aprovada na maquete. Vive
# numa constante própria porque o PAINEL do gestor (web/painel_eventos_stands)
# desenha o MESMO mapa — duas cópias divergiriam na primeira mudança de planta.
# ─────────────────────────────────────────────────────────────────────────
PLANTA_DEFS_JS = r"""
  // grid coords are (col, colSpan, row, rowSpan) on each pavilion's own 24-col grid.
  // TRANSCRIÇÃO FIEL da planta oficial do PDF v2 (aprovada pelo dono, 29/09/2026):
  // stand a stand na posição da planta. `w`/`h` no def sobrescrevem sizeDims
  // (os 2x3 do Superior ficam "em pé", o 3x2 do G67/68 fica "deitado").
  var inferiorDefs = [
    {prefix:'i', from:1, to:3, col:1, cspan:3, row:1, rspan:2, label:'Outlet Acessórios'},
    {prefix:'i', from:4, to:15, col:4, cspan:9, row:1, rspan:2, label:'Outlet Make'},
    {prefix:'G', from:43, to:54, col:14, cspan:9, row:1, rspan:2, label:'Outlet Grifes'},
    {prefix:'i', from:16, to:19, col:1, cspan:1, row:4, rspan:5},
    {prefix:'i', from:20, to:27, col:4, cspan:4, row:4, rspan:4, label:'Home Decor'},
    {prefix:'i', from:28, to:31, col:11, cspan:1, row:4, rspan:4},
    {prefix:'G', from:55, to:62, col:14, cspan:4, row:4, rspan:4},
    {prefix:'G', from:63, to:66, col:21, cspan:1, row:4, rspan:5},
    {prefix:'i', from:32, to:35, col:2, cspan:4, row:10, rspan:2, label:'Outlet Fitness'},
    {prefix:'i', from:36, to:39, col:6, cspan:4, row:10, rspan:2, label:'Outlet Kids'},
    {prefix:'i', from:40, to:41, col:11, cspan:2, row:10, rspan:2, label:'Multimarcas'},
    {prefix:'i', from:42, to:42, col:11, cspan:1, row:12, rspan:1},
    {prefix:'G', from:67, to:68, col:14, cspan:2, row:10, rspan:2},
    {prefix:'G', from:69, to:74, col:17, cspan:5, row:10, rspan:2}
  ];
  var inferiorDecor = [
    {label:'Outlet Acessórios', col:3, cspan:1, row:4, rspan:5, kind:'corridor'},
    {label:'Corredor Outlet Grifes', col:12, cspan:1, row:3, rspan:7, kind:'corridor'},
    {label:'WC', col:14, cspan:2, row:12, rspan:1, kind:'wc'},
    {label:'Entrada única →', col:23, cspan:1, row:5, rspan:4, kind:'gate'},
    {label:'Saída', col:1, cspan:1, row:12, rspan:1, kind:'gate'},
    {label:'Saída', col:9, cspan:1, row:12, rspan:1, kind:'gate'},
    {label:'Av. Marechal Castelo Branco', col:24, cspan:1, row:1, rspan:12, kind:'avenue'}
  ];

  var superiorDefs = [
    {prefix:'S', from:75, to:83, col:3, cspan:8, row:1, rspan:2, w:24, h:22},
    {prefix:'S', from:84, to:96, col:12, cspan:12, row:1, rspan:2, w:24, h:22},
    {prefix:'S', from:97, to:97, col:2, cspan:2, row:3, rspan:2},
    {prefix:'S', from:98, to:102, col:2, cspan:1, row:5, rspan:5, w:24, h:22},
    {prefix:'S', from:103, to:106, col:5, cspan:5, row:3, rspan:1},
    {prefix:'S', from:115, to:118, col:5, cspan:5, row:4, rspan:1},
    {prefix:'S', from:107, to:110, col:11, cspan:5, row:3, rspan:1},
    {prefix:'S', from:119, to:122, col:11, cspan:5, row:4, rspan:1},
    {prefix:'S', from:111, to:114, col:17, cspan:5, row:3, rspan:1},
    {prefix:'S', from:123, to:126, col:17, cspan:5, row:4, rspan:1},
    {prefix:'S', from:130, to:132, col:11, cspan:3, row:6, rspan:1, w:24, h:22},
    {prefix:'S', from:133, to:136, col:17, cspan:4, row:6, rspan:1, w:24, h:22},
    {prefix:'S', from:127, to:129, col:6, cspan:3, row:7, rspan:1, w:24, h:22},
    {prefix:'S', from:137, to:142, col:22, cspan:1, row:2, rspan:6, w:24, h:22},
    {prefix:'S', from:143, to:150, col:8, cspan:6, row:9, rspan:2},
    {prefix:'S', from:151, to:154, col:18, cspan:3, row:9, rspan:2}
  ];
  var superiorDecor = [
    {label:'Entrada →', col:1, cspan:1, row:1, rspan:2, kind:'gate'},
    {label:'WC', col:5, cspan:1, row:9, rspan:1, kind:'wc'},
    {label:'Saída', col:15, cspan:2, row:10, rspan:1, kind:'gate'},
    {label:'Saída →', col:23, cspan:1, row:7, rspan:1, kind:'gate'},
    {label:'Av. Marechal Castelo Branco', col:24, cspan:1, row:1, rspan:10, kind:'avenue'}
  ];

  var carDefs = [
    {prefix:'C', from:155, to:158, col:8, cspan:5, row:3, rspan:2, label:'Espaço em Tenda', w:34, h:30},
    {prefix:'C', from:159, to:162, col:15, cspan:5, row:3, rspan:2, label:'Stand Personalizado', w:34, h:30}
  ];
  var carDecor = [
    {label:'Av. Marechal Castelo Branco', col:4, cspan:18, row:1, rspan:1, kind:'avenueh'},
    {label:'Entrada ↓', col:13, cspan:2, row:2, rspan:1, kind:'gate'},
    {label:'Palco · Shows', col:1, cspan:2, row:2, rspan:3, kind:'stage'},
    {label:'Praça de Alimentação · Tenda 20x10', col:4, cspan:4, row:2, rspan:3, kind:'food'},
    {label:'OUTLET CAR', col:9, cspan:10, row:5, rspan:1, kind:'faixa'},
    {label:'OUTLET CHIC ↓', col:10, cspan:8, row:6, rspan:1, kind:'faixa'}
  ];

  var pavilions = [
    {key:'inferior', label:'Pavilhão Inferior', sub:'i01–i42 · G43–G74', defs:inferiorDefs, decor:inferiorDecor, rows:12},
    {key:'superior', label:'Pavilhão Superior', sub:'S75–S154', defs:superiorDefs, decor:superiorDecor, rows:10},
    {key:'outlet_car', label:'Outlet Car', sub:'C155–C162', defs:carDefs, decor:carDecor, rows:6}
  ];
"""

# ─────────────────────────────────────────────────────────────────────────
# Template — registrado como string no MESMO `_env` do portal (o padrão de
# web/recibo_publico.py e outras páginas públicas): a extensão .html no NOME
# é o que faz o Jinja escapar por padrão (select_autoescape olha a extensão),
# e nome/observação de interessado digitados na página passam por aqui.
#
# CSS e JS abaixo são o mockup aprovado QUASE VERBATIM (mesmos seletores,
# mesmos valores) — mudou só: dados fake -> STANDS do servidor, clique fake de
# comprovante -> <form> multipart de verdade, e telefone/Pix vêm da config.
# ─────────────────────────────────────────────────────────────────────────
_TPL = """<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>{{ marca }} — Stands</title>
<link rel="preconnect" href="https://fonts.googleapis.com">
<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Anton&family=Manrope:wght@400;500;600;700;800&family=IBM+Plex+Mono:wght@400;500;600&display=swap" rel="stylesheet">
{% raw %}<style>
  /* Tema único, deliberadamente escuro — é a identidade visual real da Outlet
     Chic (preto, mint, amarelo), não um dark-mode automático. */
  :root{
    --bg:#0F100A;
    --surface:#181A10;
    --surface-2:#212314;
    --floor:#15160D;
    --fg:#F5F3E6;
    --fg-dim:#9FA087;
    --line:rgba(245,243,230,0.12);
    --mint:#16E3AE;
    --mint-strong:#0CAF87;
    --mint-fg:#052A1C;
    --yellow:#FFDE2E;
    --yellow-fg:#241C00;
    --gold:#E9B44E;
    --gold-strong:#F4CD82;
    --gold-fg:#241800;
    --coral:#8C6D62;
    --coral-strong:#A98A7E;
    --coral-fg:#F3E9E4;
    --shadow:0 1px 2px rgba(0,0,0,0.35), 0 10px 26px rgba(0,0,0,0.4);
    color-scheme:dark;
  }

  *{box-sizing:border-box;}
  body{margin:0;background:var(--bg);color:var(--fg);font-family:'Manrope',system-ui,sans-serif;padding-inline:16px;}
  a{color:inherit;}
  .wrap{max-width:1180px;margin:0 auto;}
  .display{font-family:'Anton',sans-serif;font-weight:400;letter-spacing:0.01em;text-wrap:balance;}
  .mono{font-family:'IBM Plex Mono',monospace;font-variant-numeric:tabular-nums;}

  .hero{background:#12130C;color:#F3F1E3;margin-inline:-16px;padding:28px 16px 24px;border-bottom:4px solid var(--mint);}
  .hero-inner{max-width:1180px;margin:0 auto;}
  .brand{font-size:clamp(34px,7vw,58px);line-height:0.95;margin:0;}
  .brand span{color:var(--mint);}
  .hero-meta{display:flex;flex-wrap:wrap;gap:14px 22px;margin-top:14px;font-size:14px;color:#C9C7B4;}
  .hero-meta b{color:#F3F1E3;font-weight:700;}
  .hero-pitch{max-width:62ch;margin-top:14px;color:#C9C7B4;font-size:14.5px;line-height:1.55;}
  .info-strip{margin-top:18px;padding:10px 14px;border-radius:10px;background:rgba(22,227,174,0.12);border:1px solid rgba(22,227,174,0.35);font-size:13px;color:#D8F5E9;line-height:1.5;}

  .msg{max-width:1180px;margin:16px auto 0;padding:12px 14px;border-radius:10px;font-size:13.5px;font-weight:600;line-height:1.5;}
  .msg.ok{background:rgba(22,227,174,0.12);border:1px solid rgba(22,227,174,0.45);color:#D8F5E9;}
  .msg.erro{background:rgba(255,222,46,0.12);border:1px solid rgba(255,222,46,0.4);color:#F5ECC0;}
  .msg-link{color:var(--mint);font-weight:800;text-decoration:underline;margin-left:6px;white-space:nowrap;}

  h2.section-title{font-family:'Anton',sans-serif;font-weight:400;font-size:22px;letter-spacing:0.01em;margin:22px 0 4px;}
  p.section-sub{margin:0 0 18px;color:var(--fg-dim);font-size:13.5px;}

  .controls-row{display:flex;flex-wrap:wrap;justify-content:space-between;gap:10px;margin-bottom:12px;}
  .tabs{display:flex;gap:8px;flex-wrap:wrap;}
  .tab{appearance:none;cursor:pointer;font-family:inherit;font-weight:700;font-size:13px;padding:9px 14px;border-radius:10px;border:1px solid var(--line);background:var(--surface);color:var(--fg);}
  .tab[aria-selected="true"]{background:var(--mint);border-color:var(--mint);color:var(--mint-fg);}
  .tab small{display:block;font-weight:500;font-size:10.5px;opacity:0.75;margin-top:1px;}

  .view-toggle{display:inline-flex;background:var(--surface-2);border-radius:10px;padding:3px;gap:3px;border:1px solid var(--line);height:fit-content;}
  .view-btn{appearance:none;border:none;background:transparent;color:var(--fg-dim);font-family:inherit;font-weight:700;font-size:12px;padding:7px 12px;border-radius:8px;cursor:pointer;}
  .view-btn[aria-pressed="true"]{background:var(--surface);color:var(--fg);box-shadow:var(--shadow);}

  .zoom-controls{display:inline-flex;align-items:center;gap:2px;background:var(--surface-2);border-radius:10px;padding:3px;border:1px solid var(--line);height:fit-content;}
  .zoom-btn{appearance:none;border:none;background:var(--surface);color:var(--fg);font-family:'IBM Plex Mono',monospace;font-weight:700;font-size:14px;width:28px;height:28px;border-radius:7px;cursor:pointer;box-shadow:var(--shadow);}
  .zoom-btn:hover{background:var(--mint);color:var(--mint-fg);}
  .zoom-level{font-family:'IBM Plex Mono',monospace;font-size:11px;font-weight:600;color:var(--fg-dim);width:42px;text-align:center;}

  .legend{display:flex;flex-wrap:wrap;gap:14px;margin:4px 0 16px;font-size:12.5px;color:var(--fg-dim);}
  .legend-item{display:flex;align-items:center;gap:6px;}
  .dot{width:10px;height:10px;border-radius:3px;display:inline-block;}
  .dot.livre{background:var(--mint);} .dot.reservado{background:var(--gold);} .dot.vendido{background:var(--coral);}

  /* ---- floor stage ---- */
  /* Sem margem negativa: o mapa agora vive DENTRO da coluna esquerda do
     .layout, com o painel de reserva colado do lado direito (pedido do dono,
     29/09/2026: "a caixa da reserva embaixo não ficou legal"). */
  .floor-outer{overflow:auto;margin:0;padding:36px 12px 54px;max-height:78vh;}
  .floor-stage{display:flex;justify-content:center;min-width:min-content;perspective:2000px;}
  .floor-zoom{transform-style:preserve-3d;transition:transform .25s ease;transform-origin:50% 0;}
  .floor-grid{
    position:relative;display:grid;gap:5px;padding:20px;border-radius:18px;
    background:
      linear-gradient(160deg, color-mix(in srgb, var(--floor) 78%, var(--surface-2) 22%), var(--floor));
    background-size:100% 100%;
    box-shadow:inset 0 0 0 1px var(--line);
    transform-style:preserve-3d;transition:transform .5s cubic-bezier(.2,.7,.3,1), box-shadow .5s;
    transform-origin:50% 85%;
  }
  .floor-grid.is-3d{
    transform:rotateX(50deg) rotateZ(0deg) scale(0.96);
    box-shadow:inset 0 0 0 1px var(--line), 0 70px 55px -35px rgba(0,0,0,0.6);
  }

  .map-block{display:flex;flex-direction:column;gap:4px;transform-style:preserve-3d;}
  .block-label{
    font-size:7.5px;font-weight:800;text-transform:uppercase;letter-spacing:0.03em;color:var(--fg-dim);
    background:var(--surface);border:1px solid var(--line);border-radius:4px;padding:2px 5px;white-space:nowrap;
    width:fit-content;transform:translateZ(3px);box-shadow:0 2px 4px rgba(0,0,0,0.12);
  }
  .cells{display:flex;flex-wrap:wrap;align-content:flex-start;gap:4px;}

  .stand{
    appearance:none;cursor:pointer;border:none;border-radius:5px;
    font-family:'IBM Plex Mono',monospace;font-size:8.6px;font-weight:700;line-height:1;
    display:flex;align-items:center;justify-content:center;text-align:center;padding:2px;
    transform:translateZ(4px);transition:transform .15s ease, box-shadow .15s ease;
    flex:0 0 auto;
  }
  .stand.st-livre{background:linear-gradient(155deg, color-mix(in srgb, var(--mint) 88%, white 16%), var(--mint));color:var(--mint-fg);box-shadow:0 3px 0 var(--mint-strong), 0 5px 8px rgba(0,0,0,0.16);}
  .stand.st-reservado{background:linear-gradient(155deg, color-mix(in srgb, var(--gold) 85%, white 18%), var(--gold));color:var(--gold-fg);box-shadow:0 3px 0 var(--gold-strong), 0 5px 8px rgba(0,0,0,0.16);}
  .stand.st-vendido{background:var(--coral);color:var(--coral-fg);box-shadow:0 2px 0 var(--coral-strong);opacity:0.82;}
  .stand:hover{transform:translateZ(8px);}
  .stand.is-selected{transform:translateZ(16px);outline:2px solid var(--fg);outline-offset:1px;}
  .floor-grid:not(.is-3d) .stand{box-shadow:none;transform:none;border:1px solid var(--line);}
  .floor-grid:not(.is-3d) .stand.st-livre{background:color-mix(in srgb, var(--mint) 20%, var(--surface));color:var(--fg);}
  .floor-grid:not(.is-3d) .stand.st-reservado{background:color-mix(in srgb, var(--gold) 26%, var(--surface));color:var(--fg);}
  .floor-grid:not(.is-3d) .stand.st-vendido{background:color-mix(in srgb, var(--coral) 30%, var(--surface));color:var(--fg-dim);opacity:1;}
  .floor-grid:not(.is-3d) .stand:hover{transform:none;box-shadow:0 0 0 2px var(--fg) inset;}
  .floor-grid:not(.is-3d) .stand.is-selected{outline:2px solid var(--fg);transform:none;}
  .floor-grid:not(.is-3d) .block-label{box-shadow:none;transform:none;}

  /* cor por tamanho — mesma legenda de cores da planta original do PDF */
  .floor-grid.by-size .stand.sz-4x2{background:linear-gradient(155deg, color-mix(in srgb, #FF4FA3 88%, white 16%), #FF4FA3);color:#360019;box-shadow:0 3px 0 #C23378, 0 5px 8px rgba(0,0,0,0.16);}
  .floor-grid.by-size .stand.sz-4x3{background:linear-gradient(155deg, color-mix(in srgb, #4C8DFF 88%, white 16%), #4C8DFF);color:#04143B;box-shadow:0 3px 0 #2F63C2, 0 5px 8px rgba(0,0,0,0.16);}
  .floor-grid.by-size .stand.sz-3x2{background:linear-gradient(155deg, color-mix(in srgb, #2BD4E0 88%, white 16%), #2BD4E0);color:#022B2E;box-shadow:0 3px 0 #1DA3AD, 0 5px 8px rgba(0,0,0,0.16);}
  .floor-grid.by-size .stand.sz-2x2{background:linear-gradient(155deg, color-mix(in srgb, #B073FF 88%, white 16%), #B073FF);color:#1D0940;box-shadow:0 3px 0 #8850D6, 0 5px 8px rgba(0,0,0,0.16);}
  .floor-grid.by-size .stand.sz-3x3{background:linear-gradient(155deg, color-mix(in srgb, #FF9A3C 88%, white 16%), #FF9A3C);color:#3A1900;box-shadow:0 3px 0 #C97323, 0 5px 8px rgba(0,0,0,0.16);}
  .floor-grid.by-size .stand.sz-tenda{background:linear-gradient(155deg, color-mix(in srgb, var(--yellow) 88%, white 16%), var(--yellow));color:var(--yellow-fg);box-shadow:0 3px 0 #C9A800, 0 5px 8px rgba(0,0,0,0.16);}
  .floor-grid.by-size .stand.sz-personalizado{background:linear-gradient(155deg, color-mix(in srgb, #6B5CFF 88%, white 16%), #6B5CFF);color:#0D0836;box-shadow:0 3px 0 #4B3FC9, 0 5px 8px rgba(0,0,0,0.16);}
  .floor-grid.by-size.is-3d .stand:hover{transform:translateZ(8px);}
  .floor-grid.by-size.is-3d .stand.is-selected{transform:translateZ(16px);outline:2px solid var(--fg);}
  .floor-grid.by-size:not(.is-3d) .stand{box-shadow:none !important;border:1px solid var(--line);}

  .size-legend{display:flex;flex-wrap:wrap;gap:12px;margin:4px 0 16px;font-size:11.5px;color:var(--fg-dim);}
  .size-legend .dot{width:10px;height:10px;border-radius:3px;display:inline-block;}

  .decor{
    display:flex;align-items:center;justify-content:center;text-align:center;border-radius:10px;
    font-size:9.5px;font-weight:700;color:var(--fg-dim);letter-spacing:0.02em;
    border:1.5px dashed var(--line);background:color-mix(in srgb, var(--surface) 60%, transparent);
    padding:6px;transform:translateZ(1px);
  }
  .decor.gate{border-style:solid;background:transparent;color:var(--fg-dim);font-size:9px;}
  .decor.corridor{
    writing-mode:vertical-rl;text-orientation:mixed;border-style:dashed;
    font-size:8px;letter-spacing:0.06em;text-transform:uppercase;padding:8px 3px;
  }
  .decor.wc{border-style:dotted;font-size:8.5px;}
  /* avenida horizontal e faixas beges do Outlet Car (planta oficial) */
  .decor.avenueh{border:none;background:none;color:var(--fg-dim);opacity:0.6;font-size:9px;font-weight:600;letter-spacing:0.12em;text-transform:uppercase;}
  .decor.faixa{border:none;background:#CFC8B8;color:#3A362C;font-weight:800;letter-spacing:0.08em;font-size:10px;border-radius:6px;}
  .decor.avenue{
    writing-mode:vertical-rl;text-orientation:mixed;border:none;background:none;
    color:var(--fg-dim);font-size:8.5px;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;
    opacity:0.6;justify-content:flex-start;padding-top:6px;
  }
  .decor.stage, .decor.food{
    flex-direction:column;gap:4px;font-size:8.5px;color:var(--fg-dim);
    background:color-mix(in srgb, var(--surface) 45%, transparent);
  }

  /* ---- side panel ---- */
  .layout{display:grid;grid-template-columns:1fr 300px;gap:22px;align-items:start;}
  @media (max-width:860px){
    .layout{grid-template-columns:1fr;}
    /* celular: o mapa encolhe pra caber (ver ajustarEscala no JS) — sem
       scroll lateral e sem o vão que a escala deixaria embaixo. O stage
       ancora à ESQUERDA e a escala parte do canto 0,0: centralizado, o
       conteúdo encolhido ficava no meio de um stage de 970px e o overflow
       hidden mostrava só o vazio da borda. */
    .floor-outer{overflow:hidden;padding:16px 4px 10px;max-height:none;}
    .floor-stage{min-width:0;justify-content:flex-start;}
    .floor-zoom{transform-origin:0 0;}
  }
  .panel{position:sticky;top:16px;background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:18px;box-shadow:var(--shadow);}
  @media (max-width:860px){ .panel{position:static;} }
  .panel-empty{color:var(--fg-dim);font-size:13.5px;line-height:1.6;}
  .panel-expositor{margin:10px 0 4px;padding:10px 14px;border-radius:10px;background:color-mix(in srgb, var(--coral) 14%, var(--surface));border:1px solid color-mix(in srgb, var(--coral) 45%, var(--line));}
  .panel-expositor span{display:block;font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.05em;color:var(--fg-dim);margin-bottom:2px;}
  .panel-expositor b{font-size:17px;}
  .panel-photo-wrap{position:relative;border-radius:10px 10px 0 0;overflow:hidden;margin:-18px -18px 14px;background:#0e0f0a;}
  .panel-photo{width:100%;display:block;aspect-ratio:900/616;object-fit:cover;}
  .panel-photo-cap{font-size:10px;color:var(--fg-dim);text-align:center;margin:6px 0 4px;}
  .panel-code{font-family:'Anton',sans-serif;font-size:30px;line-height:1;}
  .status-badge{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;font-weight:700;padding:4px 10px;border-radius:999px;margin:10px 0 14px;letter-spacing:0.02em;}
  .status-badge.st-livre{background:var(--mint);color:var(--mint-fg);}
  .status-badge.st-reservado{background:var(--gold);color:var(--gold-fg);}
  .status-badge.st-vendido{background:var(--coral);color:var(--coral-fg);}
  .panel-row{display:flex;justify-content:space-between;padding:8px 0;border-top:1px solid var(--line);font-size:13px;gap:10px;}
  .panel-row:first-of-type{border-top:none;}
  .panel-row b{font-weight:700;text-align:right;}
  .panel-price{font-family:'IBM Plex Mono',monospace;font-weight:600;}
  .btn{display:block;width:100%;text-align:center;appearance:none;cursor:pointer;text-decoration:none;font-family:inherit;font-weight:700;font-size:13.5px;padding:12px 14px;border-radius:10px;border:none;margin-top:14px;}
  .btn-primary{background:var(--mint);color:var(--mint-fg);}

  .pix-box{background:var(--surface-2);border-radius:10px;padding:12px;margin-top:14px;}
  .pix-label{font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:0.03em;color:var(--fg-dim);margin-bottom:6px;}
  .pix-key-row{display:flex;align-items:center;gap:8px;background:var(--surface);border:1px solid var(--line);border-radius:8px;padding:8px 10px;}
  .pix-key{font-family:'IBM Plex Mono',monospace;font-size:11.5px;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
  .pix-copy{appearance:none;cursor:pointer;border:none;background:var(--mint);color:var(--mint-fg);font-family:inherit;font-weight:700;font-size:11px;padding:6px 10px;border-radius:6px;flex:0 0 auto;}
  .pix-titular{font-size:11px;color:var(--fg-dim);margin-top:6px;}
  .up-input{width:100%;background:var(--surface-2);border:1px solid var(--line);border-radius:8px;padding:10px 12px;color:var(--fg);font-family:inherit;font-size:13px;margin-top:8px;}
  .up-input::placeholder{color:var(--fg-dim);}
  .fld{display:block;margin-top:10px;}
  .fld > span{display:block;font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.03em;color:var(--fg-dim);margin-bottom:4px;}
  .fld > span i{font-style:normal;color:var(--gold-strong);}
  .fld .up-input{margin-top:0;}
  .fld.falta .up-input{border-color:var(--gold);}
  .fld-erro{color:var(--gold-strong);font-size:11px;margin-top:6px;}
  .reserva-box,.sinal-box{background:var(--surface-2);border:1px solid var(--line);border-radius:12px;padding:12px 14px;margin-top:14px;}
  .reserva-tit,.sinal-tit{font-size:10.5px;font-weight:800;text-transform:uppercase;letter-spacing:.05em;color:var(--fg-dim);margin-bottom:6px;}
  .reserva-lin{display:flex;align-items:center;gap:10px;padding:7px 0;border-top:1px solid var(--line);font-size:13px;}
  .reserva-lin:first-of-type{border-top:none;}
  .reserva-lin b{font-family:var(--mono,monospace);font-size:15px;min-width:42px;}
  .reserva-lin span{flex:1;color:var(--fg-dim);font-size:12px;}
  .reserva-lin em{font-style:normal;font-weight:700;}
  .reserva-x{appearance:none;border:1px solid var(--line);background:var(--surface);color:var(--fg-dim);border-radius:6px;width:24px;height:24px;line-height:1;cursor:pointer;font-size:14px;padding:0;}
  .reserva-add{display:block;width:100%;margin-top:8px;appearance:none;cursor:pointer;font-family:inherit;font-weight:700;font-size:13px;padding:10px;border-radius:9px;border:1.5px dashed var(--mint);background:transparent;color:var(--mint);}
  .reserva-add small{display:block;font-weight:500;font-size:10.5px;color:var(--fg-dim);margin-top:2px;}
  .reserva-dica{margin-top:8px;font-size:12px;color:var(--gold-strong);line-height:1.5;}
  .reserva-dica button{appearance:none;cursor:pointer;font-family:inherit;font-size:11.5px;margin-left:8px;border:1px solid var(--line);background:var(--surface);color:var(--fg);border-radius:6px;padding:3px 9px;}
  .reserva-tot{display:flex;justify-content:space-between;align-items:baseline;margin-top:8px;padding-top:8px;border-top:1px solid var(--line);font-size:13px;}
  .reserva-tot b{font-size:18px;}
  .sinal-box .panel-row{padding:6px 0;}
  .upload-box{
    display:block;margin-top:10px;border:1.5px dashed var(--line);border-radius:10px;padding:14px;text-align:center;
    cursor:pointer;background:var(--surface);
  }
  .upload-box:hover{border-color:var(--mint);}
  .upload-box input{display:none;}
  .upload-box.falta{border-color:var(--gold);}
  .upload-label{font-size:12.5px;font-weight:700;}
  .upload-sub{font-size:10.5px;color:var(--fg-dim);margin-top:2px;}
  .upload-file{font-size:11px;color:var(--mint-strong);font-weight:700;margin-top:6px;word-break:break-all;}
  .whatsapp-secondary{
    display:flex;align-items:center;justify-content:center;gap:6px;margin-top:12px;
    font-size:11.5px;color:var(--fg-dim);text-decoration:none;border-top:1px solid var(--line);padding-top:12px;
  }
  .whatsapp-secondary:hover{color:var(--mint-strong);}
  .sent-state{background:color-mix(in srgb, var(--gold) 16%, var(--surface));border:1px solid var(--gold);border-radius:10px;padding:12px;margin-top:14px;font-size:12px;line-height:1.6;}
  .sent-state b{display:block;font-size:13px;margin-bottom:3px;}

  .zone-summary{display:flex;flex-wrap:wrap;gap:6px;margin-bottom:16px;}
  .zone-chip{display:flex;align-items:center;gap:6px;font-size:11.5px;font-weight:600;padding:5px 10px;border-radius:999px;background:var(--surface);border:1px solid var(--line);}
  .zone-chip b{font-family:'IBM Plex Mono',monospace;font-weight:700;}

  footer{text-align:center;color:var(--fg-dim);font-size:12px;padding:24px 0 32px;}
</style>{% endraw %}
</head><body>

<div class="hero">
  <div class="hero-inner">
    <h1 class="brand display"><span>{{ marca[:3] }}</span>{{ marca[3:] }} — STANDS</h1>
    <div class="hero-meta">
      {% if cfg.edicao_label %}<span><b>{{ cfg.edicao_label }}</b></span>{% endif %}
      {% if cfg.evento_inicio %}<span><b>{{ data_br(cfg.evento_inicio) }}{% if cfg.evento_fim and cfg.evento_fim != cfg.evento_inicio %} a {{ data_br(cfg.evento_fim) }}{% endif %}</b></span>{% endif %}
      {% if cfg.evento_local %}<span>{{ cfg.evento_local }}</span>{% endif %}
    </div>
    <p class="hero-pitch">Escolha seu stand direto no mapa da planta oficial do evento: toque em um stand livre, veja tamanho e valor, pague o sinal no Pix e envie o comprovante — a reserva é sua enquanto a equipe confirma.</p>
    {% if vendedor_link %}<div class="info-strip">Atendimento de <b>{{ vendedor_nome or 'seu vendedor' }}</b> — escolha o stand que quiser; a reserva feita aqui fica registrada neste atendimento.</div>{% endif %}
    {% if cfg.whatsapp_numero %}<div class="info-strip">Cotas de patrocínio (Ouro, Prata e Bronze) são negociadas direto com a equipe — chama no WhatsApp.</div>{% endif %}
  </div>
</div>

{% if msg == 'ok' %}<div class="msg ok">✓ Comprovante recebido{% if msg_codigo %} — o stand {{ msg_codigo }} está reservado pra você{% endif %}! A equipe confere o pagamento e confirma em breve.{% if ct %} <a class="msg-link" href="/contrato/{{ ct }}">Assinar o contrato agora →</a>{% endif %}</div>{% endif %}
{% if msg == 'erro' %}<div class="msg erro">Não deu pra registrar o comprovante{% if msg_codigo %} do stand {{ msg_codigo }}{% endif %}. Ele pode já ter sido vendido — dá uma olhada no mapa e tenta outro{% if cfg.whatsapp_numero %}, ou chama no WhatsApp{% endif %}.</div>{% endif %}
{% if msg == 'erro_sinal' %}<div class="msg erro">O sinal mínimo é de R$ 1.500 por stand e não pode passar do valor total. Confira o valor do sinal e envie de novo.</div>{% endif %}
{% if msg == 'erro_empresa' %}<div class="msg erro">Esta empresa já tem um stand reservado. Cada empresa fecha um contrato só, com no máximo 2 stands — fale com a organização pelo WhatsApp para incluir mais um.</div>{% endif %}
{% if msg == 'erro_indisponivel' %}<div class="msg erro">Um dos stands escolhidos acabou de ser reservado por outra pessoa. Dê uma olhada no mapa e escolha de novo.</div>{% endif %}
{% if msg == 'erro_max' %}<div class="msg erro">O máximo é 2 stands por empresa.</div>{% endif %}
{% if msg == 'erro_dados' %}<div class="msg erro">Faltou o nome fantasia ou o WhatsApp (com DDD). Preencha os dois e envie o comprovante de novo.</div>{% endif %}
{% if msg == 'erro_generico' %}<div class="msg erro">Não deu pra processar. Tenta de novo.</div>{% endif %}
{% if sem_storage %}<div class="msg erro">⚠ Envio de comprovante temporariamente indisponível{% if cfg.whatsapp_numero %} — manda pelo WhatsApp{% endif %}.</div>{% endif %}

<div class="wrap">

  <h2 class="section-title">Escolha seu stand</h2>
  <p class="section-sub">Mapa fiel à planta oficial do evento, com o tamanho real de cada stand. Toque em um stand livre para ver valor e reservar.</p>

  {% if n_total == 0 %}<p class="panel-empty">Nenhum stand cadastrado ainda.</p>{% endif %}

  <div class="controls-row">
    <div class="tabs" role="tablist" id="pavilion-tabs"></div>
    <div style="display:flex;gap:8px;flex-wrap:wrap;">
      <div class="view-toggle">
        <button class="view-btn" id="btn-color-status" aria-pressed="true" onclick="setColorMode('status')">Cor por status</button>
        <button class="view-btn" id="btn-color-size" aria-pressed="false" onclick="setColorMode('tamanho')">Cor por tamanho</button>
      </div>
      <div class="view-toggle">
        <button class="view-btn" id="btn-view-3d" aria-pressed="true" onclick="setFloorView('3d')">Mapa 3D</button>
        <button class="view-btn" id="btn-view-2d" aria-pressed="false" onclick="setFloorView('2d')">Planta técnica</button>
      </div>
      <div class="zoom-controls">
        <button class="zoom-btn" onclick="zoomFloor(-1)" aria-label="Diminuir zoom">−</button>
        <span class="zoom-level" id="zoom-level">100%</span>
        <button class="zoom-btn" onclick="zoomFloor(1)" aria-label="Aumentar zoom">+</button>
      </div>
    </div>
  </div>

  <div class="legend" id="legend-status">
    <span class="legend-item"><span class="dot livre"></span>Livre</span>
    <span class="legend-item"><span class="dot reservado"></span>Reservado (comprovante em confirmação)</span>
    <span class="legend-item"><span class="dot vendido"></span>Vendido</span>
  </div>
  <div class="size-legend" id="legend-size" hidden>
    <span class="legend-item"><span class="dot" style="background:#FF4FA3"></span>4x2m</span>
    <span class="legend-item"><span class="dot" style="background:#4C8DFF"></span>4x3m</span>
    <span class="legend-item"><span class="dot" style="background:#2BD4E0"></span>3x2m</span>
    <span class="legend-item"><span class="dot" style="background:#B073FF"></span>2x2m</span>
    <span class="legend-item"><span class="dot" style="background:#FF9A3C"></span>3x3m</span>
    <span class="legend-item"><span class="dot" style="background:var(--yellow)"></span>Espaço em tenda</span>
    <span class="legend-item"><span class="dot" style="background:#6B5CFF"></span>Stand personalizado</span>
  </div>

  <div class="zone-summary" id="zone-summary"></div>

  <div class="layout">
    <div class="floor-outer">
      <div class="floor-stage">
        <div class="floor-zoom" id="floor-zoom">
          <div class="floor-grid is-3d" id="floor-grid"></div>
        </div>
      </div>
    </div>
    <div class="panel" id="panel"></div>
  </div>

</div>

<footer>{{ marca }}{% if cfg.edicao_label %} · {{ cfg.edicao_label }}{% endif %} — mapa oficial de stands</footer>

<script>
var STANDS = {{ stands_json|safe }};
var PIX = {{ pix_json|safe }};
var WA = {{ wa_json|safe }};
var ACTION = '/e/{{ cfg.slug }}/comprovante';
var JUST_SENT = {{ just_sent_json|safe }};
var ERRO_CODIGO = {{ erro_codigo_json|safe }};
var CT = {{ ct_json|safe }};
var STAND_LINK = {{ stand_link_json|safe }};
var REGRAS = {{ regras_json|safe }};   // sinal mínimo por stand (centavos), máximo por empresa, data do saldo
var JUST_SENT_CODES = {{ just_sent_codes_json|safe }};
var VENDEDOR_LINK = "{{ vendedor_link }}";  // o `v` do link — vai no form do comprovante
var SEM_STORAGE = {{ 'true' if sem_storage else 'false' }};
</script>
{% raw %}<script>
(function(){
  var sizeLabel = {'4x2':'4x2m','4x3':'4x3m','3x2':'3x2m','2x2':'2x2m','3x3':'3x3m','tenda':'Espaço em tenda','personalizado':'Stand personalizado'};
  var sizePhoto = {'4x2':'/estatico/stands/4x2.jpg','4x3':'/estatico/stands/4x3.jpg','3x2':'/estatico/stands/3x2.jpg','2x2':'/estatico/stands/2x2.jpg','3x3':'/estatico/stands/3x3.jpg'};
  // proportional footprint per real stand size (bigger stands render as bigger tiles)
  // pegada proporcional REAL: largura = FRENTE do stand, altura = FUNDO
  // (planta oficial v2). `w`/`h` no def sobrescrevem estes valores.
  var sizeDims = {'2x2':{w:24,h:16},'3x2':{w:34,h:16},'3x3':{w:34,h:22},'4x2':{w:24,h:28},'4x3':{w:34,h:28},'tenda':{w:24,h:28},'personalizado':{w:28,h:28}};

""" + PLANTA_DEFS_JS + r"""
  // A planta desenhada acima + o BANCO: cada def só vira stand se o código
  // existir no servidor (STANDS); status/zona/tamanho/preço vêm de lá.
  var usados = {};
  var stands = [];
  pavilions.forEach(function(p){
    p.defs.forEach(function(d){
      for (var n=d.from; n<=d.to; n++){
        var num = d.prefix === 'i' ? String(n).padStart(2,'0') : String(n);
        var code = d.prefix + num;
        var sv = STANDS[code];
        if (!sv) continue;
        usados[code] = true;
        stands.push({ code:code, zone:sv.zona, pavilion:p.key, size:sv.tamanho,
                      preco:sv.preco, precoC:sv.preco_centavos || 0, status:sv.status, dias:sv.dias, expositor:sv.expositor });
      }
    });
  });
  // Estandes do banco fora da planta desenhada (código novo, ou outra conta):
  // caem num bloco corrido no fim do pavilhão — e pavilhão desconhecido vira
  // aba própria. A página nunca esconde estande que existe no banco.
  var extras = {};
  Object.keys(STANDS).forEach(function(code){
    if (usados[code]) return;
    var sv = STANDS[code];
    var pk = sv.pavilhao || 'outros';
    (extras[pk] = extras[pk] || []).push(code);
    stands.push({ code:code, zone:sv.zona, pavilion:pk, size:sv.tamanho,
                  preco:sv.preco, precoC:sv.preco_centavos || 0, status:sv.status, dias:sv.dias, expositor:sv.expositor });
  });
  Object.keys(extras).forEach(function(pk){
    var pav = pavilions.filter(function(p){ return p.key === pk; })[0];
    if (!pav){
      pav = { key:pk, label:pk.replace(/_/g,' ').replace(/\\b\\w/g,function(c){return c.toUpperCase();}),
              sub:'', defs:[], decor:[], rows:1, extraRow:1 };
      pavilions.push(pav);
    } else {
      pav.extraRow = pav.rows + 1;
    }
    pav.extraCodes = extras[pk].sort();
  });

  var currentPavilion = pavilions[0] ? pavilions[0].key : 'inferior';
  var selectedCode = null;
  // A RESERVA (até 2 stands da mesma empresa, um contrato só): `cart` são os stands
  // juntados; `draft` guarda o que a pessoa já digitou, porque a caixa é refeita a
  // cada stand adicionado.
  var cart = [], adicionando = false, draft = {nome:'', whatsapp:'', sinal:null};
  function stOf(code){ return stands.filter(function(x){ return x.code === code; })[0]; }
  function reservaCodes(){ if (cart.length) return cart.slice(); return selectedCode ? [selectedCode] : []; }
  function totalC(codes){ return codes.reduce(function(a, c){ return a + ((stOf(c) || {}).precoC || 0); }, 0); }
  function reais(centavos){ return (centavos / 100).toLocaleString('pt-BR', {style:'currency', currency:'BRL'}); }
  function numBR(txt){ var v = parseFloat(String(txt || '').replace(/[^0-9,.]/g, '').replace(/[.]/g, '').replace(',', '.')); return isNaN(v) ? 0 : v; }
  function adicionarStand(){ cart = reservaCodes(); adicionando = true; renderPanel(); }
  function cancelarAdd(){ adicionando = false; renderPanel(); }
  function tirarDaReserva(code){
    cart = cart.filter(function(c){ return c !== code; });
    selectedCode = cart[0] || code; adicionando = false; draft.sinal = null; renderFloor(); renderPanel();
  }
  function draftCampo(k, v){ draft[k] = v; }
  function atualizaSaldo(v){
    draft.sinal = v;
    var el = document.getElementById('v-saldo');
    if (el) el.textContent = reais(Math.max(0, totalC(reservaCodes()) - Math.round(numBR(v) * 100)));
  }
  window.adicionarStand = adicionarStand; window.cancelarAdd = cancelarAdd;
  window.tirarDaReserva = tirarDaReserva; window.draftCampo = draftCampo; window.atualizaSaldo = atualizaSaldo;
  var floorView = '3d';
  var colorMode = 'status';
  var zoomLevel = 1;
  var baseScale = 1;

  function setColorMode(mode){
    colorMode = mode;
    document.getElementById('btn-color-status').setAttribute('aria-pressed', mode === 'status' ? 'true':'false');
    document.getElementById('btn-color-size').setAttribute('aria-pressed', mode === 'tamanho' ? 'true':'false');
    document.getElementById('legend-status').hidden = mode !== 'status';
    document.getElementById('legend-size').hidden = mode !== 'tamanho';
    var grid = document.getElementById('floor-grid');
    if (mode === 'tamanho') grid.classList.add('by-size'); else grid.classList.remove('by-size');
  }
  window.setColorMode = setColorMode;

  function aplicarZoom(){
    document.getElementById('floor-zoom').style.transform = 'scale(' + (baseScale * zoomLevel) + ')';
    document.getElementById('zoom-level').textContent = Math.round(zoomLevel * 100) + '%';
  }
  function zoomFloor(dir){
    zoomLevel = Math.min(1.8, Math.max(0.7, Math.round((zoomLevel + dir * 0.15) * 100) / 100));
    aplicarZoom();
  }
  window.zoomFloor = zoomFloor;

  // No celular a planta (24 colunas × 34px) não cabe na tela: em vez de scroll
  // lateral, o mapa inteiro encolhe pra caber (escala-base); o zoom manual
  // continua funcionando por cima dela.
  function ajustarEscala(){
    var outer = document.querySelector('.floor-outer');
    var stage = document.querySelector('.floor-stage');
    var grid = document.getElementById('floor-grid');
    if (!outer || !stage || !grid) return;
    if (window.innerWidth >= 860){
      baseScale = 1; stage.style.height = ''; aplicarZoom(); return;
    }
    var w = grid.offsetWidth || 1;
    baseScale = Math.min(1, (outer.clientWidth - 12) / w);
    // com rotateX(50°) a altura projetada é menor que a do layout — sem o
    // fator, sobrava um vão vazio embaixo do mapa 3D no celular.
    var fator3d = grid.classList.contains('is-3d') ? 0.82 : 1;
    stage.style.height = Math.ceil(grid.offsetHeight * baseScale * fator3d + 30) + 'px';
    aplicarZoom();
  }
  window.addEventListener('resize', ajustarEscala);

  var tabsEl = document.getElementById('pavilion-tabs');
  pavilions.forEach(function(p){
    var b = document.createElement('button');
    b.className = 'tab';
    b.setAttribute('role','tab');
    b.setAttribute('aria-selected', p.key === currentPavilion ? 'true' : 'false');
    b.id = 'tab-' + p.key;
    b.innerHTML = p.label + (p.sub ? '<small>' + p.sub + '</small>' : '');
    b.onclick = function(){ selectPavilion(p.key); };
    tabsEl.appendChild(b);
  });

  function selectPavilion(key){
    currentPavilion = key;
    selectedCode = null;
    pavilions.forEach(function(p){ document.getElementById('tab-' + p.key).setAttribute('aria-selected', p.key === key ? 'true' : 'false'); });
    renderFloor();
    renderZoneSummary();
    renderPanel();
  }

  function setFloorView(v){
    floorView = v;
    document.getElementById('btn-view-3d').setAttribute('aria-pressed', v === '3d' ? 'true':'false');
    document.getElementById('btn-view-2d').setAttribute('aria-pressed', v === '2d' ? 'true':'false');
    var grid = document.getElementById('floor-grid');
    if (v === '3d') grid.classList.add('is-3d'); else grid.classList.remove('is-3d');
    ajustarEscala();
  }
  window.setFloorView = setFloorView;

  function standsFor(from, to, prefix){
    var out = [];
    for (var n=from;n<=to;n++){
      var num = prefix === 'i' ? String(n).padStart(2,'0') : String(n);
      out.push(prefix+num);
    }
    return out;
  }

  function standButton(s, d){
    var btn = document.createElement('button');
    btn.className = 'stand st-' + s.status + ' sz-' + s.size + (s.code === selectedCode || cart.indexOf(s.code) >= 0 ? ' is-selected' : '');
    // o def (d) pode sobrescrever a pegada padrão do tamanho — é o que deixa
    // o stand "em pé" ou "deitado" fiel à planta oficial
    var base = sizeDims[s.size] || {w:32,h:22};
    var dims = {w: (d && d.w) || base.w, h: (d && d.h) || base.h};
    btn.style.width = dims.w + 'px';
    btn.style.height = dims.h + 'px';
    btn.textContent = s.code;
    btn.title = s.code + ' · ' + (sizeLabel[s.size] || s.size);
    btn.onclick = function(){
      if (adicionando && s.status === 'livre' && cart.length && cart.indexOf(s.code) < 0 && cart.length < REGRAS.max){
        cart.push(s.code); adicionando = false; selectedCode = s.code; draft.sinal = null;
      } else {
        selectedCode = s.code;
        if (cart.indexOf(s.code) < 0){ cart = []; adicionando = false; draft.sinal = null; }
      }
      renderFloor(); renderPanel();
      // no celular o painel fica ABAIXO do mapa (o grid vira 1 coluna) — sem
      // este scroll o toque parecia não fazer nada.
      if (window.innerWidth < 860) document.getElementById('panel').scrollIntoView({behavior:'smooth', block:'nearest'});
    };
    return btn;
  }

  function renderFloor(){
    var grid = document.getElementById('floor-grid');
    var pav = pavilions.filter(function(p){ return p.key === currentPavilion; })[0];
    grid.style.gridTemplateColumns = 'repeat(24, 34px)';
    grid.style.gridTemplateRows = 'repeat(' + pav.rows + ', 30px)';
    if (floorView === '3d') grid.classList.add('is-3d'); else grid.classList.remove('is-3d');
    grid.innerHTML = '';

    pav.decor.forEach(function(d){
      var el = document.createElement('div');
      el.className = 'decor' + (d.kind ? ' ' + d.kind : '');
      el.style.gridColumn = d.col + ' / span ' + d.cspan;
      el.style.gridRow = d.row + ' / span ' + d.rspan;
      el.textContent = d.label;
      grid.appendChild(el);
    });

    pav.defs.forEach(function(d){
      var block = document.createElement('div');
      block.className = 'map-block';
      block.style.gridColumn = d.col + ' / span ' + d.cspan;
      block.style.gridRow = d.row + ' / span ' + d.rspan;

      if (d.label){
        var lab = document.createElement('div');
        lab.className = 'block-label';
        lab.textContent = d.label;
        block.appendChild(lab);
      }
      var cells = document.createElement('div');
      cells.className = 'cells';
      var codes = standsFor(d.from, d.to, d.prefix);
      codes.forEach(function(code){
        var s = stands.filter(function(x){ return x.code === code && x.pavilion === currentPavilion; })[0];
        if (!s) return;
        cells.appendChild(standButton(s, d));
      });
      block.appendChild(cells);
      grid.appendChild(block);
    });

    if (pav.extraCodes && pav.extraCodes.length){
      var extraBlock = document.createElement('div');
      extraBlock.className = 'map-block';
      extraBlock.style.gridColumn = '1 / span 23';
      extraBlock.style.gridRow = String(pav.extraRow || 1);
      if (pav.defs.length){
        var lab2 = document.createElement('div');
        lab2.className = 'block-label';
        lab2.textContent = 'Outros espaços';
        extraBlock.appendChild(lab2);
      }
      var cells2 = document.createElement('div');
      cells2.className = 'cells';
      pav.extraCodes.forEach(function(code){
        var s = stands.filter(function(x){ return x.code === code; })[0];
        if (s) cells2.appendChild(standButton(s));
      });
      extraBlock.appendChild(cells2);
      grid.appendChild(extraBlock);
    }
    ajustarEscala();
  }

  function renderZoneSummary(){
    var el = document.getElementById('zone-summary');
    el.innerHTML = '';
    var names = [];
    stands.forEach(function(s){ if (s.pavilion === currentPavilion && names.indexOf(s.zone) === -1) names.push(s.zone); });
    names.forEach(function(zoneName){
      var zs = stands.filter(function(s){ return s.pavilion === currentPavilion && s.zone === zoneName; });
      var livres = zs.filter(function(s){ return s.status === 'livre'; }).length;
      var chip = document.createElement('span');
      chip.className = 'zone-chip';
      var nome = document.createTextNode(zoneName || 'Sem zona');
      chip.appendChild(nome);
      var b = document.createElement('b');
      b.textContent = ' ' + livres + '/' + zs.length;
      chip.appendChild(b);
      el.appendChild(chip);
    });
  }

  function waLink(text){ return 'https://wa.me/' + WA + '?text=' + encodeURIComponent(text); }

  function esc(t){
    return String(t).replace(/[&<>"']/g, function(c){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }

  function copyPix(){
    var btn = event.currentTarget;
    var restore = function(){ btn.textContent = 'Copiar'; };
    navigator.clipboard.writeText(PIX.chave).then(function(){
      btn.textContent = 'Copiado!';
      setTimeout(restore, 1600);
    }).catch(function(){
      var sel = document.getElementById('pix-key-text');
      if (sel){
        var range = document.createRange();
        range.selectNodeContents(sel);
        var s = window.getSelection();
        s.removeAllRanges(); s.addRange(range);
      }
      btn.textContent = 'Selecionado';
      setTimeout(restore, 1600);
    });
  }
  window.copyPix = copyPix;

  function mostrarArquivo(input){
    var box = input.closest('.upload-box');
    var nomeEl = box && box.querySelector('.upload-file');
    if (!nomeEl) return;
    if (input.files && input.files.length){
      nomeEl.textContent = '✓ ' + input.files[0].name;
      nomeEl.hidden = false;
      box.classList.remove('falta');
    } else {
      nomeEl.hidden = true;
    }
  }
  window.mostrarArquivo = mostrarArquivo;

  function validarEnvio(form){
    // input[type=file] fica escondido dentro do .upload-box (label), então a
    // validação nativa de `required` não consegue focar nele — checa na mão.
    var erro = form.querySelector('[data-erro]');
    var nome = form.elements['nome'], zap = form.elements['whatsapp'];
    var zapOk = zap && zap.value.replace(/[^0-9]/g, '').length >= 10;
    if (!nome.value.trim() || !zapOk){
      form.querySelectorAll('.fld').forEach(function(f){
        var inp = f.querySelector('input');
        f.classList.toggle('falta', inp === nome ? !nome.value.trim() : !zapOk);
      });
      if (erro){ erro.hidden = false; erro.textContent = 'Preencha o nome fantasia e o WhatsApp (com DDD) pra reservar.'; }
      return false;
    }
    var codes = reservaCodes(), minimo = REGRAS.minimo * codes.length;
    var sinalC = Math.round(numBR(form.elements['sinal'].value) * 100);
    if (sinalC < minimo || sinalC > totalC(codes)){
      if (erro){ erro.hidden = false; erro.textContent = sinalC < minimo
        ? 'O sinal mínimo é ' + reais(minimo) + (codes.length > 1 ? ' (R$ 1.500 por stand).' : '.')
        : 'O sinal não pode passar do valor total da reserva.'; }
      return false;
    }
    if (erro) erro.hidden = true;
    var arq = form.querySelector('input[type=file]');
    if (!arq || !arq.files || !arq.files.length){
      var box = form.querySelector('.upload-box');
      if (box){ box.classList.add('falta'); box.scrollIntoView({behavior:'smooth', block:'center'}); }
      return false;
    }
    return true;
  }
  window.validarEnvio = validarEnvio;

  function renderPanel(){
    var panel = document.getElementById('panel');
    if (!selectedCode){
      panel.innerHTML = '<p class="panel-empty">Selecione um stand no mapa acima para ver tamanho, valor e disponibilidade.</p>';
      return;
    }
    var s = stands.filter(function(x){ return x.code === selectedCode; })[0];
    var statusText = {livre:'Livre', reservado:'Reservado', vendido:'Vendido'}[s.status];
    var pav = pavilions.filter(function(p){return p.key===s.pavilion;})[0];
    var html = '';
    var photo = sizePhoto[s.size];
    if (photo){
      html += '<div class="panel-photo-wrap">';
      html += '  <img class="panel-photo" src="' + photo + '" alt="Modelo do stand ' + esc(sizeLabel[s.size] || s.size) + '">';
      html += '</div>';
      html += '<p class="panel-photo-cap">Render ilustrativo do padrão de montagem</p>';
    }
    html += '<div class="panel-code">' + esc(s.code) + '</div>';
    html += '<span class="status-badge st-' + s.status + '">' + statusText + '</span>';
    if (s.status === 'vendido' && s.expositor) html += '<div class="panel-expositor"><span>Expositor</span><b>' + esc(s.expositor) + '</b></div>';
    if (s.zone) html += '<div class="panel-row"><span>Zona</span><b>' + esc(s.zone) + '</b></div>';
    html += '<div class="panel-row"><span>Pavilhão</span><b>' + esc(pav ? pav.label : s.pavilion) + '</b></div>';
    html += '<div class="panel-row"><span>Tamanho</span><b>' + esc(sizeLabel[s.size] || s.size) + '</b></div>';
    html += '<div class="panel-row"><span>Valor</span><b class="panel-price">' + esc(s.preco || 'Consultar') + '</b></div>';

    if (s.status === 'livre'){
      var codes = reservaCodes(), n = codes.length, totalR = totalC(codes), minimo = REGRAS.minimo * n;
      var sinalV = draft.sinal != null ? draft.sinal : String(minimo / 100);
      html += '<div class="reserva-box"><div class="reserva-tit">Sua reserva</div>';
      codes.forEach(function(c){
        var x = stOf(c);
        html += '<div class="reserva-lin"><b>' + esc(c) + '</b><span>' + esc(sizeLabel[x.size] || x.size) + ' · ' + esc(x.zone || '') + '</span><em>' + esc(x.preco || '') + '</em>' +
                (n > 1 ? '<button type="button" class="reserva-x" title="Tirar da reserva" data-c="' + esc(c) + '" onclick="tirarDaReserva(this.dataset.c)">×</button>' : '') + '</div>';
      });
      if (n < REGRAS.max){
        if (adicionando){
          html += '<div class="reserva-dica">Toque num stand <b>livre</b> no mapa pra juntar a esta reserva.<button type="button" onclick="cancelarAdd()">Cancelar</button></div>';
        } else {
          html += '<button type="button" class="reserva-add" onclick="adicionarStand()">+ Adicionar mais 1 stand<small>máximo ' + REGRAS.max + ' por empresa · os dois entram num contrato só</small></button>';
        }
      } else {
        html += '<div class="reserva-dica">Máximo de ' + REGRAS.max + ' stands por empresa. Os dois entram no mesmo contrato.</div>';
      }
      html += '<div class="reserva-tot"><span>Total' + (n > 1 ? ' (sem desconto)' : '') + '</span><b>' + reais(totalR) + '</b></div></div>';
      if (PIX.chave){
        html += '<div class="pix-box">';
        html += '  <div class="pix-label">Pagar o sinal com Pix e garantir ' + (n > 1 ? 'os stands' : 'o stand') + '</div>';
        html += '  <div class="pix-key-row"><span class="pix-key" id="pix-key-text">' + esc(PIX.chave) + '</span><button class="pix-copy" onclick="copyPix()">Copiar</button></div>';
        if (PIX.titular) html += '  <div class="pix-titular">Titular: ' + esc(PIX.titular) + '</div>';
        html += '</div>';
      }
      if (!SEM_STORAGE){
        html += '<form method="post" action="' + ACTION + '" enctype="multipart/form-data" onsubmit="return validarEnvio(this)">';
        html += '  <input type="hidden" name="codigo" value="' + esc(codes[0]) + '">';
        if (codes[1]) html += '  <input type="hidden" name="codigo2" value="' + esc(codes[1]) + '">';
        if (VENDEDOR_LINK) html += '  <input type="hidden" name="vendedor" value="' + esc(VENDEDOR_LINK) + '">';
        html += '<div class="sinal-box"><div class="sinal-tit">Sinal e saldo</div>';
        html += '<div class="panel-row"><span>Sinal mínimo</span><b>R$ 1.500 por stand' + (n > 1 ? ' · ' + reais(minimo) : '') + '</b></div>';
        html += '<label class="fld"><span>Valor do sinal enviado <i>*</i></span><input class="up-input" type="text" name="sinal" inputmode="decimal" value="' + esc(sinalV) + '" oninput="atualizaSaldo(this.value)"></label>';
        html += '<div class="panel-row"><span>Saldo</span><b id="v-saldo">' + reais(Math.max(0, totalR - Math.round(numBR(sinalV) * 100))) + '</b></div>';
        if (REGRAS.saldoAte) html += '<div class="panel-row"><span>Saldo até</span><b>' + esc(REGRAS.saldoAte) + ' (dia do evento)</b></div>';
        html += '<p class="upload-sub" style="margin-top:6px;text-align:left">Pode mandar mais que o mínimo agora. O saldo é pago em uma ou mais vezes, por Pix ou cartão' + (REGRAS.saldoAte ? ', até ' + esc(REGRAS.saldoAte) : '') + ' — a organização registra cada pagamento.</p></div>';
        html += '  <label class="fld"><span>Nome fantasia</span><input class="up-input" type="text" name="nome" placeholder="Ex.: Boutique Nova Era" required maxlength="200" autocomplete="organization" value="' + esc(draft.nome) + '" oninput="draftCampo(this.name, this.value)"></label>';
        html += '  <label class="fld"><span>WhatsApp <i>*</i></span><input class="up-input" type="tel" name="whatsapp" placeholder="(86) 9 9999-9999" required maxlength="40" autocomplete="tel" value="' + esc(draft.whatsapp) + '" oninput="draftCampo(this.name, this.value)"></label>';
        html += '  <div class="fld-erro" data-erro hidden></div>';
        html += '  <label class="upload-box">';
        html += '    <div class="upload-label">Comprovante do sinal (Pix)</div>';
        html += '    <div class="upload-sub">Toque para escolher a foto ou o PDF do comprovante</div>';
        html += '    <input type="file" name="arquivo" accept="image/*,application/pdf" onchange="mostrarArquivo(this)">';
        html += '    <div class="upload-file" hidden></div>';
        html += '  </label>';
        html += '  <button class="btn btn-primary" type="submit">Enviar comprovante e reservar</button>';
        html += '</form>';
        html += '<p class="upload-sub" style="text-align:center;margin-top:8px;">Assim que o comprovante chegar, ' + (n > 1 ? 'os stands ficam reservados' : 'o stand fica reservado') + ' pra você enquanto a equipe confirma</p>';
      } else {
        html += '<div class="sent-state"><b>Envio temporariamente indisponível</b>' + (WA ? 'Manda o comprovante pelo WhatsApp que a equipe registra pra você.' : 'Tenta de novo daqui a pouco.') + '</div>';
      }
      if (WA){
        var msg = 'Olá! Tenho uma dúvida sobre o stand ' + s.code + ' (' + (sizeLabel[s.size] || s.size) + ').';
        html += '<a class="whatsapp-secondary" href="' + waLink(msg) + '" target="_blank" rel="noopener">Prefere tirar dúvida no WhatsApp?</a>';
      }
    } else if (s.status === 'reservado'){
      if (JUST_SENT_CODES.indexOf(s.code) >= 0){
        html += '<div class="sent-state"><b>Comprovante do sinal recebido</b>' +
          (JUST_SENT_CODES.length > 1 ? 'Reserva dos stands <b style="display:inline">' + esc(JUST_SENT_CODES.join(' + ')) + '</b> num contrato só. ' : '') +
          'Assim que a equipe confirmar o sinal, ' + (JUST_SENT_CODES.length > 1 ? 'eles são seus' : 'ele é seu') + ' — normalmente em algumas horas. O saldo' + (REGRAS.saldoAte ? ' vence em ' + esc(REGRAS.saldoAte) : '') + ' e fica no contrato. Seu cadastro já foi criado; a equipe vai pedir os dados do contrato.</div>';
        if (CT){
          html += '<a class="btn btn-primary" style="text-decoration:none" href="/contrato/' + encodeURIComponent(CT) + '">Assinar o contrato agora</a>';
          html += '<p class="upload-sub" style="text-align:center;margin-top:6px;">O contrato de locação do stand já está pronto com seus dados — assina online em 1 minuto</p>';
        }
      }
      if (s.dias != null){
        html += '<div class="panel-row"><span>Prazo de confirmação</span><b>' + s.dias + ' dia(s)</b></div>';
      }
      if (WA){
        var msg2 = 'Olá! O stand ' + s.code + ' está reservado — fico na fila caso libere?';
        html += '<a class="whatsapp-secondary" href="' + waLink(msg2) + '" target="_blank" rel="noopener">Entrar na fila de espera</a>';
      }
    } else {
      html += '<p class="panel-empty" style="margin-top:14px;">' + (s.expositor ? 'Stand confirmado: pagamento feito e contrato assinado. Você encontra a marca aqui na feira.' : 'Este stand já foi confirmado e não está mais disponível.') + '</p>';
    }
    panel.innerHTML = html;
  }

  // depois do POST, volta com ?msg=ok|erro&codigo=X: abre o pavilhão certo e
  // já seleciona o stand — o expositor vê o próprio comprovante refletido.
  var initCode = JUST_SENT || ERRO_CODIGO || STAND_LINK;
  var initPav = currentPavilion;
  if (initCode){
    var s0 = stands.filter(function(x){ return x.code === initCode; })[0];
    if (s0) initPav = s0.pavilion;
  }
  if (pavilions.length){
    selectPavilion(initPav);
    if (initCode){
      var s1 = stands.filter(function(x){ return x.code === initCode; })[0];
      if (s1){ selectedCode = initCode; renderFloor(); renderPanel(); }
    }
  }
})();
</script>{% endraw %}
</body></html>
"""

_env.loader.mapping[_TPL_NOME] = _TPL
