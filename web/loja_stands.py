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
    # ?c=<código> é o link que a vendedora manda pro cliente que ela cadastrou ANTES
    # ("+ Novo cliente", 01/10/2026): a página abre com o nome fantasia e o WhatsApp
    # dele, e a reserva vai pro cadastro — a venda nasce com o contrato completo. O
    # código é assinado (es.codigo_cliente); inválido = a página de sempre.
    c_raw = "".join(ch for ch in (request.query_params.get("c") or "")[:40]
                    if ch.isalnum() or ch == "-")
    cli_link = es.cliente_do_codigo(pool, conta_id, c_raw) if c_raw else None
    # dentro de <script>: o nome é digitado por gente, então nada de < > & crus
    if cli_link:
        from web.contrato_publico import _fone
        cli_link["whatsapp"] = _fone(cli_link["whatsapp"])
    cliente_json = (json.dumps({"codigo": c_raw, "nome": cli_link["nome"],
                                "whatsapp": cli_link["whatsapp"], "vendedora": vendedor_nome})
                    if cli_link else "null")
    cliente_json = (cliente_json.replace("<", "\\u003c").replace(">", "\\u003e")
                    .replace("&", "\\u0026"))
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
        fachada=_FACHADAS.get(cfg.get("slug") or ""),
        data_br=_data_br, msg=msg, msg_codigo=msg_codigo, ct=ct,
        vendedor_link=vendedor_link, vendedor_nome=vendedor_nome, cliente_json=cliente_json,
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
                                  cliente: str = Form(""),
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
        conteudo, arquivo.content_type or "", codigo2, sinal, cliente)


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
                                  codigo2: str = "", sinal: str = "", cliente: str = ""):
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
    # cliente cadastrado antes pela vendedora (link com `?c=`): a reserva vai pro
    # cadastro dele. Código assinado e revalidado aqui; inválido = o caminho de sempre.
    # Vem ANTES da checagem: o limite por empresa conta também os stands desse cadastro.
    cli = es.cliente_do_codigo(pool, conta_id, cliente) if (cliente or "").strip() else None
    v = es.validar_reserva(pool, conta_id, codigos, whatsapp,
                           sinal_c if sinal_c is not None else None,
                           cliente_id=(cli or {}).get("id"))
    if not v["ok"]:
        return RedirectResponse(
            f"/e/{slug}?msg=erro_{v.get('cod') or 'reserva'}&codigo={codigo}", status_code=303)

    prospeccao_id = _criar_prospeccao_simples(pool, conta_id, nome, whatsapp, vendedor)
    r = es.subir_e_registrar_comprovante(pool, conta_id, codigo, conteudo, content_type,
                                         prospeccao_id=prospeccao_id,
                                         junto_com=codigos[1:], sinal_centavos=v["sinal"],
                                         cliente_id=(cli or {}).get("id"))
    if not r["ok"]:
        _log.info("loja_stands: comprovante recusado (%s/%s): %s", conta_id, codigo,
                  r.get("erro"))
        return RedirectResponse(f"/e/{slug}?msg=erro&codigo={codigo}", status_code=303)
    es.avisar_reserva_nova(pool, conta_id, prospeccao_id, codigos, nome, v["sinal"])
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
_FOTOS_OK = {"2x2", "3x2", "3x3", "4x2", "4x3",
             "fachada-outlet-chic", "fachada-outlet-chic-800"}
# A foto do topo da página (01/10/2026, pedido do dono): a fachada do local com o
# pórtico do evento, pág. 2 do PDF oficial. É arte DESTE evento — entra pelo slug
# da página, nunca pra todas as contas que vendem stand.
_FACHADAS = {
    "outlet-chic": {"arquivo": "fachada-outlet-chic",
                    "alt": "Fachada do Centro de Convenções de Teresina com o pórtico do Outlet Chic"},
}
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
# A PLANTA DESENHADA. Posição e tamanho de cada stand foram MEDIDOS na planta
# oficial do evento (PDF v2, págs. 10–12 — conferido stand a stand em 01/10/2026):
# o stand fica onde a planta põe, na proporção real (2x4, 3x4, 3x3, 2x3…). Antes
# era uma transcrição em grade de 24 colunas e alguns blocos saíam deslocados
# (S143–S154, S127–S132). Unidade: largura do pavilhão = 1000; o Outlet Car entra
# nivelado (no PDF o terreno sobe ~5° pra direita).
# Vive em constantes próprias porque o PAINEL do gestor (web/painel_eventos_stands)
# e o APP da vendedora (web/painel_cockpit) desenham o MESMO mapa com a MESMA
# função (plantaMontar) — três cópias divergiriam na primeira mudança de planta.
# ─────────────────────────────────────────────────────────────────────────
PLANTA_CSS = """
/* ---- cenário da planta (paredes, rótulos, faixas, ícones): o MESMO nas 3 telas
   (página pública, painel do gestor, app da vendedora). Cada tela só informa as
   cores no elemento do mapa: --pl-dim (texto), --pl-line (traço), --pl-surf (fundo
   de rótulo). Nada aqui recebe clique: o toque passa pro stand. Classes com prefixo
   plc- pra não trombar com as do app (que já tem .pl, .ic…). --plk (posto pelo
   plantaMontar) encolhe a letra dos rótulos quando a planta tem menos de 800px. */
.plc{position:absolute;box-sizing:border-box;display:flex;align-items:center;justify-content:center;
  text-align:center;white-space:nowrap;line-height:1;font-size:calc(8px*var(--plk,1));font-weight:700;letter-spacing:.03em;
  color:var(--pl-dim,#9FA087);pointer-events:none}
.plc-linha{background:var(--pl-line,rgba(245,243,230,.16))}
.plc-muro{border:1px solid var(--pl-line,rgba(245,243,230,.16))}
.plc-zona{font-size:calc(7.5px*var(--plk,1));font-weight:800;text-transform:uppercase;border-radius:4px;
  background:var(--pl-surf,#181A10);border:1px solid var(--pl-line,rgba(245,243,230,.16))}
.plc-faixa{background:#CFC8B8;color:#3A362C;font-size:calc(9px*var(--plk,1));font-weight:800;letter-spacing:.07em;
  text-transform:uppercase;border-radius:4px}
.plc-avenue{font-size:calc(8.5px*var(--plk,1));font-weight:600;letter-spacing:.1em;text-transform:uppercase;opacity:.75}
.plc-txt{font-size:calc(7px*var(--plk,1));text-transform:uppercase;letter-spacing:.05em}
.plc-tenda{border:1.5px dashed var(--pl-dim,#9FA087);border-radius:6px;background:rgba(245,243,230,.05)}
/* texto em pé: vt lê de cima pra baixo, vb de baixo pra cima (como na planta) */
.plc-vt,.plc-vb{writing-mode:vertical-rl;text-orientation:mixed}
.plc-vb{transform:rotate(180deg)}
.plc-faixa.plc-vt,.plc-faixa.plc-vb{font-size:calc(7px*var(--plk,1));letter-spacing:.02em}
.plc-ic svg{display:block;width:100%;height:100%}
"""

PLANTA_DEFS_JS = r"""
  // [x, y, largura, altura] de cada stand, na unidade da planta (largura = 1000).
  // decor = cenário: k é o tipo (classe .plc-<k> de PLANTA_CSS), r o retângulo, t o texto,
  // ic o ícone (plantaIcone), g o giro em graus.
  var PLANTA = {
    inferior:{w:1000,h:448.8,
      stands:{"i01":[76.8,14.3,29.5,58.7],"i02":[106.3,14.3,29.1,58.7],"i03":[135.4,14.3,29.6,58.7],"i04":[180.5,14.3,29.5,58.7],"i05":[209.9,14.3,29.2,58.7],"i06":[239.1,14.3,29.2,58.7],"i07":[268.3,14.3,29.2,58.7],"i08":[297.5,14.3,29.2,58.7],"i09":[326.7,14.3,29.3,58.7],"i10":[356.0,14.3,29.1,58.7],"i11":[385.1,14.3,29.2,58.7],"i12":[414.3,14.3,29.2,58.7],"i13":[443.5,14.3,29.3,58.7],"i14":[472.7,14.3,29.3,58.7],"i15":[502.0,14.3,29.5,58.7],"i16":[81.6,124.0,59.0,29.3],"i17":[81.6,153.2,59.0,29.1],"i18":[81.6,182.3,59.0,29.1],"i19":[81.6,211.4,59.0,29.2],"i20":[191.3,138.5,58.5,43.8],"i21":[249.8,138.5,58.5,43.8],"i22":[308.3,138.5,58.4,43.8],"i23":[366.7,138.5,58.7,43.8],"i24":[191.3,182.3,58.5,43.9],"i25":[249.8,182.3,58.5,43.9],"i26":[308.3,182.3,58.4,43.9],"i27":[366.7,182.3,58.7,43.9],"i28":[468.6,124.1,29.6,29.1],"i29":[468.6,153.2,29.6,29.1],"i30":[468.6,182.3,29.6,29.0],"i31":[468.6,211.3,29.6,29.1],"i32":[160.3,292.9,29.6,58.8],"i33":[189.9,292.9,29.2,58.8],"i34":[219.0,292.9,29.2,58.8],"i35":[248.2,292.9,29.3,58.8],"i36":[277.5,292.9,29.2,58.8],"i37":[306.7,292.9,29.3,58.8],"i38":[335.9,292.9,29.3,58.8],"i39":[365.2,292.9,29.5,58.8],"i40":[472.5,292.9,29.6,58.5],"i41":[502.0,292.9,29.5,58.8],"i42":[472.5,351.4,29.6,58.3],"G43":[541.2,14.3,29.5,58.7],"G44":[570.7,14.3,29.2,58.7],"G45":[599.9,14.3,29.2,58.7],"G46":[629.2,14.3,29.2,58.7],"G47":[658.4,14.3,29.3,58.7],"G48":[687.7,14.3,29.2,58.7],"G49":[716.8,14.3,29.2,58.7],"G50":[746.0,14.3,29.1,58.7],"G51":[775.2,14.3,29.2,58.7],"G52":[804.4,14.3,29.3,58.7],"G53":[833.7,14.3,29.1,58.7],"G54":[862.8,14.3,29.7,58.7],"G55":[541.4,138.5,58.5,43.8],"G56":[599.9,138.5,58.5,43.8],"G57":[658.4,138.5,58.4,43.8],"G58":[716.8,138.5,58.6,43.8],"G59":[541.4,182.3,58.5,43.8],"G60":[599.9,182.3,58.5,43.8],"G61":[658.4,182.3,58.4,43.8],"G62":[716.8,182.3,58.6,43.8],"G63":[820.5,122.9,59.0,29.4],"G64":[820.5,152.3,59.0,29.0],"G65":[820.5,181.3,59.0,29.1],"G66":[820.5,210.4,59.0,29.3],"G67":[583.8,291.6,44.2,29.7],"G68":[628.1,291.6,44.0,29.7],"G69":[714.0,291.6,29.5,58.6],"G70":[743.5,291.6,29.2,58.6],"G71":[772.7,291.6,29.2,58.6],"G72":[801.9,291.6,29.2,58.6],"G73":[831.0,291.6,29.2,58.6],"G74":[860.3,291.6,29.5,58.6]},
      decor:[{"k":"linha","r":[0.0,4.8,75.8,0.0]},{"k":"linha","r":[0.0,64.1,75.8,0.0]},{"k":"linha","r":[0.0,355.9,108.9,0.0]},{"k":"linha","r":[75.8,4.8,0.0,351.2]},{"k":"linha","r":[75.8,7.8,818.8,0.0]},{"k":"linha","r":[312.3,7.8,0.0,6.9]},{"k":"linha","r":[130.5,355.9,94.5,0.0]},{"k":"linha","r":[894.6,4.8,0.0,235.0]},{"k":"linha","r":[894.6,282.5,0.0,72.6]},{"k":"linha","r":[894.6,4.5,53.8,0.0]},{"k":"linha","r":[948.4,4.5,0.0,350.6]},{"k":"linha","r":[865.0,354.9,83.5,0.0]},{"k":"linha","r":[501.9,409.9,0.0,19.4]},{"k":"linha","r":[473.0,429.3,28.9,0.0]},{"k":"muro","r":[225.0,353.6,204.1,75.6]},{"k":"muro","r":[501.9,362.4,276.2,67.0]},{"k":"muro","r":[570.4,340.5,101.4,21.9]},{"k":"muro","r":[778.2,353.7,86.8,75.6]},{"k":"muro","r":[865.0,354.9,135.0,91.3]},{"k":"muro","r":[190.9,120.1,8.6,9.1]},{"k":"muro","r":[307.1,121.0,8.6,9.1]},{"k":"muro","r":[421.2,118.4,8.2,8.6]},{"k":"muro","r":[428.1,118.4,8.2,8.6]},{"k":"muro","r":[540.8,118.4,9.1,8.6]},{"k":"muro","r":[658.3,118.4,9.1,8.6]},{"k":"muro","r":[774.5,118.4,8.6,8.6]},{"k":"muro","r":[188.3,235.4,9.1,9.1]},{"k":"muro","r":[305.4,236.7,9.1,9.1]},{"k":"muro","r":[419.4,233.7,8.2,8.2]},{"k":"muro","r":[425.9,233.7,8.2,8.2]},{"k":"muro","r":[539.1,232.8,9.1,8.6]},{"k":"muro","r":[657.0,232.8,8.6,8.6]},{"k":"muro","r":[773.2,232.8,8.6,8.6]},{"k":"muro","r":[539.1,349.0,8.6,9.1]},{"k":"muro","r":[772.4,349.5,8.6,8.6]},{"k":"zona","r":[88.6,75.2,141.7,14.3],"t":"Outlet Acessórios"},{"k":"zona","r":[305.0,75.2,90.3,14.3],"t":"Outlet Make"},{"k":"zona","r":[644.9,95.0,101.9,14.3],"t":"Outlet Grifes"},{"k":"zona vb","r":[141.7,111.9,15.1,137.4],"t":"Outlet Acessórios"},{"k":"zona","r":[263.5,122.2,88.6,14.3],"t":"Home Decor"},{"k":"zona","r":[167.2,272.6,108.0,14.3],"t":"Outlet Fitness"},{"k":"zona","r":[297.2,272.6,84.2,14.3],"t":"Outlet Kids"},{"k":"faixa vt","r":[522.2,70.8,16.8,221.6],"t":"Outlet Grifes"},{"k":"ic","ic":"seta","r":[14.7,193.5,13.4,17.7]},{"k":"zona","r":[0.9,217.3,40.5,11.7],"t":"Sobe"},{"k":"ic","ic":"seta","r":[880.8,256.6,17.7,13.8],"g":270},{"k":"faixa vb","r":[907.6,213.8,12.1,99.4],"t":"Entrada única"},{"k":"ic","ic":"saida","r":[108.9,345.6,21.6,21.2]},{"k":"ic","ic":"saida","r":[431.1,411.2,21.6,20.7]},{"k":"ic","ic":"wc","r":[625.9,386.6,21.2,21.2]},{"k":"avenue vb","r":[986.2,79.9,9.5,212.5],"t":"Av. Marechal Castelo Branco"}]},
    superior:{w:1000,h:368.0,
      stands:{"S75":[151.4,16.6,29.2,43.5],"S76":[180.6,16.6,29.3,43.5],"S77":[209.8,16.6,29.0,43.5],"S78":[238.8,16.6,29.0,43.5],"S79":[267.9,16.6,29.2,43.5],"S80":[297.1,16.6,29.0,43.5],"S81":[326.1,16.6,29.2,43.5],"S82":[355.3,16.6,29.2,43.5],"S83":[384.5,16.6,29.2,43.5],"S84":[428.2,16.6,29.2,43.5],"S85":[457.4,16.6,29.2,43.5],"S86":[486.6,16.6,29.2,43.5],"S87":[515.7,16.6,28.9,43.5],"S88":[544.7,16.6,29.2,43.5],"S89":[573.9,16.6,29.2,43.5],"S90":[603.0,16.6,29.0,43.5],"S91":[632.0,16.6,29.2,43.5],"S92":[661.3,16.6,29.1,43.5],"S93":[690.3,16.6,29.0,43.5],"S94":[719.3,16.6,29.3,43.5],"S95":[748.6,16.6,29.0,43.5],"S96":[777.6,16.6,29.2,43.5],"S97":[64.7,75.8,43.8,43.6],"S98":[64.7,133.8,43.8,29.0],"S99":[64.7,162.9,43.8,28.9],"S100":[64.7,191.7,43.8,29.2],"S101":[64.7,220.9,43.8,28.9],"S102":[64.7,249.8,43.8,29.1],"S103":[152.1,96.3,43.7,43.5],"S104":[195.8,96.3,43.8,43.5],"S105":[239.5,96.3,43.8,43.5],"S106":[283.3,96.3,43.7,43.5],"S107":[370.5,96.3,43.7,43.5],"S108":[414.3,96.3,43.8,43.5],"S109":[458.1,96.3,43.5,43.5],"S110":[501.6,96.3,43.9,43.5],"S111":[588.9,96.3,43.8,43.5],"S112":[632.7,96.3,43.6,43.5],"S113":[676.4,96.3,43.7,43.5],"S114":[720.1,96.3,43.7,43.5],"S115":[152.1,139.8,43.7,43.6],"S116":[195.8,139.8,43.8,43.6],"S117":[239.5,139.8,43.8,43.6],"S118":[283.3,139.8,43.7,43.6],"S119":[370.5,139.8,43.7,43.6],"S120":[414.3,139.8,43.8,43.6],"S121":[458.1,139.8,43.5,43.6],"S122":[501.6,139.8,43.9,43.6],"S123":[588.9,139.8,43.8,43.6],"S124":[632.7,139.8,43.6,43.6],"S125":[676.4,139.8,43.7,43.6],"S126":[720.1,139.8,43.7,43.6],"S127":[269.5,240.9,29.3,43.6],"S128":[298.9,240.9,29.0,43.6],"S129":[327.9,240.9,29.2,43.6],"S130":[414.2,219.5,43.8,29.2],"S131":[458.1,219.5,43.5,29.2],"S132":[501.6,219.5,43.9,29.2],"S133":[588.9,219.5,43.8,29.2],"S134":[632.7,219.5,43.5,29.2],"S135":[676.2,219.5,43.8,29.2],"S136":[720.1,219.5,43.7,29.2],"S137":[806.8,60.2,43.8,28.9],"S138":[806.8,89.1,43.8,29.2],"S139":[806.8,118.3,43.8,28.9],"S140":[806.8,147.2,43.8,28.8],"S141":[806.8,176.0,43.8,29.2],"S142":[806.8,205.2,43.8,28.9],"S143":[406.4,284.3,29.2,58.2],"S144":[435.6,284.3,29.2,58.2],"S145":[464.8,284.3,29.0,58.2],"S146":[493.8,284.3,29.2,58.2],"S147":[523.0,284.3,29.0,58.2],"S148":[552.1,284.3,29.2,58.2],"S149":[581.3,284.3,29.1,58.2],"S150":[610.4,284.3,29.3,58.2],"S151":[757.9,284.3,29.2,58.2],"S152":[787.1,284.3,29.1,58.2],"S153":[816.2,284.3,29.2,58.2],"S154":[845.4,284.3,29.2,58.2]},
      decor:[{"k":"linha","r":[0.0,8.4,874.7,0.0]},{"k":"linha","r":[53.6,8.4,0.0,8.2]},{"k":"linha","r":[53.6,16.6,97.8,0.0]},{"k":"linha","r":[0.0,63.9,59.4,0.0]},{"k":"linha","r":[59.4,64.4,0.0,286.8]},{"k":"linha","r":[59.4,284.5,346.7,0.0]},{"k":"muro","r":[166.3,262.6,103.2,22.0]},{"k":"linha","r":[0.0,355.5,647.1,0.0]},{"k":"linha","r":[709.7,354.2,165.0,0.0]},{"k":"linha","r":[874.7,4.8,0.0,236.7]},{"k":"linha","r":[874.7,284.7,0.0,70.4]},{"k":"linha","r":[874.7,4.8,106.3,0.0]},{"k":"linha","r":[981.0,4.8,0.0,350.3]},{"k":"linha","r":[874.7,295.0,106.3,0.0]},{"k":"linha","r":[874.7,355.1,106.3,0.0]},{"k":"faixa vt","r":[18.1,10.8,14.7,61.8],"t":"Entrada"},{"k":"ic","ic":"seta","r":[42.3,35.0,17.7,13.4],"g":90},{"k":"faixa vb","r":[901.5,244.5,14.7,38.9],"t":"Saída"},{"k":"ic","ic":"seta","r":[873.9,257.0,17.7,13.4],"g":90},{"k":"ic","ic":"wc","r":[214.3,309.3,21.6,21.2]},{"k":"ic","ic":"saida","r":[687.7,342.1,21.6,21.6]},{"k":"avenue vb","r":[984.0,80.8,9.5,216.0],"t":"Av. Marechal Castelo Branco"}]},
    outlet_car:{w:1000,h:328.1,
      stands:{"C155":[511.0,87.9,38.8,37.8],"C156":[564.5,87.9,38.8,37.8],"C157":[618.0,87.9,38.8,37.8],"C158":[671.6,87.9,38.8,37.8],"C159":[769.6,87.9,38.8,37.8],"C160":[823.1,87.9,38.8,37.8],"C161":[876.7,87.9,38.8,37.8],"C162":[930.5,87.9,38.8,37.8]},
      decor:[{"k":"linha","r":[0.0,32.4,1000.0,0.0]},{"k":"linha","r":[0.0,77.8,1000.0,0.0]},{"k":"linha","r":[366.6,260.9,631.9,0.0]},{"k":"muro","r":[31.4,108.7,71.3,72.3]},{"k":"tenda","r":[234.3,109.7,238.2,118.9]},{"k":"ic","ic":"carro","cor":"#26272B","r":[512.5,133.4,30.1,56.8],"g":180},{"k":"ic","ic":"carro","cor":"#F1EDEE","r":[581.0,136.0,29.6,56.3],"g":180},{"k":"ic","ic":"carro","cor":"#DDE6EA","r":[640.3,134.8,29.1,62.1],"g":180},{"k":"ic","ic":"carro","cor":"#E0504A","r":[674.9,136.4,31.1,56.8],"g":180},{"k":"ic","ic":"carro","cor":"#39456A","r":[771.9,129.8,30.6,56.8],"g":180},{"k":"ic","ic":"carro","cor":"#F1EDEE","r":[842.4,132.0,29.6,56.3],"g":180},{"k":"ic","ic":"carro","cor":"#DDE6EA","r":[883.7,130.7,28.6,62.1],"g":180},{"k":"ic","ic":"carro","cor":"#26272B","r":[946.3,130.5,30.6,55.8],"g":180},{"k":"ic","ic":"moto","cor":"#3A65A8","r":[551.1,138.2,18.4,43.7],"g":180},{"k":"ic","ic":"moto","cor":"#F7B717","r":[617.4,137.6,18.0,43.7],"g":180},{"k":"ic","ic":"moto","cor":"#F7B717","r":[708.5,137.3,18.0,43.2],"g":180},{"k":"ic","ic":"moto","cor":"#3A65A8","r":[804.1,131.1,18.0,43.2],"g":180},{"k":"ic","ic":"moto","cor":"#F7B717","r":[824.5,131.4,18.9,43.2],"g":180},{"k":"ic","ic":"moto","cor":"#3A65A8","r":[919.8,132.9,18.4,43.7],"g":180},{"k":"ic","ic":"palco","r":[25.9,90.4,107.2,113.1]},{"k":"ic","ic":"musica","r":[140.0,146.8,35.9,38.3]},{"k":"ic","ic":"mesa","r":[240.3,118.8,31.1,28.6]},{"k":"ic","ic":"mesa","r":[269.8,148.7,31.5,29.1]},{"k":"ic","ic":"mesa","r":[302.9,116.5,31.1,28.6]},{"k":"ic","ic":"mesa","r":[336.5,149.6,31.1,28.6]},{"k":"ic","ic":"mesa","r":[361.9,114.9,31.1,29.1]},{"k":"ic","ic":"mesa","r":[400.6,146.0,30.6,28.6]},{"k":"ic","ic":"mesa","r":[428.5,117.3,31.1,28.6]},{"k":"ic","ic":"barraca","r":[234.4,195.0,35.4,34.9]},{"k":"ic","ic":"barraca","r":[268.5,194.6,35.4,35.4]},{"k":"ic","ic":"barraca","r":[302.1,194.1,35.9,35.4]},{"k":"ic","ic":"barraca","r":[336.2,194.7,35.9,34.9]},{"k":"ic","ic":"barraca","r":[370.3,193.7,35.9,35.4]},{"k":"ic","ic":"barraca","r":[404.4,193.8,35.4,35.4]},{"k":"ic","ic":"barraca","r":[438.1,193.8,35.4,34.9]},{"k":"txt","t":"Tenda 20x10","r":[311.4,178.5,72.8,11.6]},{"k":"faixa","t":"Praça de Alimentação","r":[242.9,233.6,225.8,19.7]},{"k":"txt vb","t":"Palco 6x6","r":[11.6,109.4,9.7,63.1]},{"k":"avenue","t":"Av. Marechal Castelo Branco","r":[434.2,6.8,271.7,13.6]},{"k":"faixa","t":"Entrada","r":[705.4,49.4,74.2,18.6]},{"k":"ic","ic":"seta","g":180,"r":[733.6,79.1,18.0,21.8]},{"k":"faixa","t":"Outlet Car","r":[514.6,199.1,466.0,24.4]},{"k":"faixa","t":"Outlet Chic","r":[615.9,264.0,265.3,29.5]},{"k":"ic","ic":"seta","g":180,"r":[747.4,299.0,18.4,22.3]},{"k":"linha","r":[726.5,77.8,0.0,54.7]},{"k":"linha","r":[726.5,132.6,32.3,0.0]},{"k":"linha","r":[758.8,77.8,0.0,54.7]},{"k":"zona","t":"Espaço em tenda","r":[545.2,61.4,131.0,12.6]},{"k":"zona","t":"Stand personalizado","r":[791.9,61.4,155.3,12.6]}]}
  };

  var pavilions = [
    {key:'inferior', label:'Pavilhão Inferior', sub:'i01–i42 · G43–G74', planta:PLANTA.inferior},
    {key:'superior', label:'Pavilhão Superior', sub:'S75–S154', planta:PLANTA.superior},
    {key:'outlet_car', label:'Outlet Car', sub:'C155–C162', planta:PLANTA.outlet_car}
  ];

  // ---- ícones da planta (Outlet Car, praça de alimentação, palco). SVG FIXO, escrito
  // aqui: nada vem de fora nem do banco. `cor` só é usada nos carros/motos.
  function plantaIcone(nome, cor){
    var S = '<svg xmlns="http://www.w3.org/2000/svg" width="100%" height="100%" preserveAspectRatio="xMidYMid meet" aria-hidden="true" viewBox="';
    cor = cor || '#D9D6C8';
    if (nome === 'carro') return S + '0 0 40 84">' +
      '<rect x="1.5" y="14" width="5" height="13" rx="2" fill="#0B0C08"/><rect x="33.5" y="14" width="5" height="13" rx="2" fill="#0B0C08"/>' +
      '<rect x="1.5" y="57" width="5" height="13" rx="2" fill="#0B0C08"/><rect x="33.5" y="57" width="5" height="13" rx="2" fill="#0B0C08"/>' +
      '<rect x="4" y="2" width="32" height="80" rx="12" fill="' + cor + '" stroke="#8D8F86" stroke-width="1.2"/>' +
      '<path d="M8.5 27 Q20 20.5 31.5 27 L29.5 37 Q20 34 10.5 37 Z" fill="#141A22" opacity=".9"/>' +
      '<rect x="10" y="38.5" width="20" height="17" rx="3.5" fill="rgba(255,255,255,.16)"/>' +
      '<path d="M10.5 57.5 Q20 60.5 29.5 57.5 L31 66 Q20 70.5 9 66 Z" fill="#141A22" opacity=".9"/>' +
      '<rect x="8" y="4.5" width="7" height="3" rx="1.5" fill="#FFF4C2"/><rect x="25" y="4.5" width="7" height="3" rx="1.5" fill="#FFF4C2"/>' +
      '<rect x="8" y="76.5" width="7" height="2.6" rx="1.3" fill="#E0574F"/><rect x="25" y="76.5" width="7" height="2.6" rx="1.3" fill="#E0574F"/>' +
      '</svg>';
    if (nome === 'moto') return S + '0 0 20 60">' +
      '<rect x="7.5" y="2" width="5" height="13" rx="2.5" fill="#0B0C08"/><rect x="7.5" y="45" width="5" height="13" rx="2.5" fill="#0B0C08"/>' +
      '<rect x="2" y="13" width="16" height="2.6" rx="1.3" fill="#8D8F86"/>' +
      '<path d="M10 12 Q15 17 14 28 L13 44 Q10 47 7 44 L6 28 Q5 17 10 12 Z" fill="' + cor + '" stroke="#8D8F86" stroke-width="1"/>' +
      '<ellipse cx="10" cy="33" rx="3.4" ry="6.5" fill="#14161A"/>' +
      '</svg>';
    if (nome === 'mesa') return S + '0 0 40 40">' +
      '<g fill="none" stroke="#C48A63" stroke-width="2.4" stroke-linecap="round">' +
      '<path d="M14 5.5 Q20 3 26 5.5"/><path d="M14 34.5 Q20 37 26 34.5"/><path d="M5.5 14 Q3 20 5.5 26"/><path d="M34.5 14 Q37 20 34.5 26"/></g>' +
      '<circle cx="20" cy="20" r="10.5" fill="#B5774F" stroke="#8A5636" stroke-width="1.2"/>' +
      '<circle cx="20" cy="20" r="6" fill="none" stroke="rgba(255,255,255,.22)" stroke-width="1"/>' +
      '</svg>';
    if (nome === 'barraca') return S + '0 0 40 44">' +
      '<path d="M3 20 L20 4 L37 20 Z" fill="#F5F3E6" stroke="#6E6C5E" stroke-width="1.2" stroke-linejoin="round"/>' +
      '<path d="M20 4 L14 20 M20 4 L26 20 M20 4 L20 20" stroke="#6E6C5E" stroke-width="1" fill="none"/>' +
      '<path d="M3 20 q2.85 5 5.7 0 q2.85 5 5.7 0 q2.85 5 5.7 0 q2.85 5 5.7 0 q2.85 5 5.7 0 q2.85 5 5.5 0" fill="#F5F3E6" stroke="#6E6C5E" stroke-width="1.1"/>' +
      '<rect x="6" y="24" width="28" height="16" fill="#E9E6D6" stroke="#6E6C5E" stroke-width="1.2"/>' +
      '<rect x="10" y="27.5" width="20" height="7" fill="#2A2C1E" stroke="#6E6C5E" stroke-width=".8"/>' +
      '<path d="M4 40 H36" stroke="#6E6C5E" stroke-width="1.4"/>' +
      '</svg>';
    if (nome === 'palco') return S + '0 0 110 116">' +
      '<rect x="28" y="22" width="38" height="72" fill="#23241A"/>' +
      '<path d="M30 8 Q4 58 30 108" fill="none" stroke="#C4B7A9" stroke-width="6"/>' +
      '<path d="M30 8 Q4 58 30 108" fill="none" stroke="#6E6456" stroke-width="6" stroke-dasharray="1.4 4.2"/>' +
      '<circle cx="22.5" cy="24.0" r="2.6" fill="#FFDE2E"/><circle cx="16.5" cy="38.0" r="2.6" fill="#FF4FA3"/><circle cx="14.0" cy="52.0" r="2.6" fill="#FF9A3C"/><circle cx="14.0" cy="66.0" r="2.6" fill="#FFDE2E"/><circle cx="16.5" cy="80.0" r="2.6" fill="#FF4FA3"/><circle cx="22.5" cy="94.0" r="2.6" fill="#FF9A3C"/>' +
      '<rect x="32" y="3" width="15" height="17" rx="2" fill="#2B2C2E" stroke="#6E6C5E" stroke-width="1"/><circle cx="39.5" cy="11.5" r="4.6" fill="#101112" stroke="#55574C" stroke-width="1"/><rect x="48" y="3" width="15" height="17" rx="2" fill="#2B2C2E" stroke="#6E6C5E" stroke-width="1"/><circle cx="55.5" cy="11.5" r="4.6" fill="#101112" stroke="#55574C" stroke-width="1"/><rect x="32" y="96" width="15" height="17" rx="2" fill="#2B2C2E" stroke="#6E6C5E" stroke-width="1"/><circle cx="39.5" cy="104.5" r="4.6" fill="#101112" stroke="#55574C" stroke-width="1"/><rect x="48" y="96" width="15" height="17" rx="2" fill="#2B2C2E" stroke="#6E6C5E" stroke-width="1"/><circle cx="55.5" cy="104.5" r="4.6" fill="#101112" stroke="#55574C" stroke-width="1"/>' +
      '<rect x="40" y="31.5" width="17" height="9" rx="4" fill="#FF4FA3"/><circle cx="38" cy="36.0" r="3.6" fill="#2A1D16"/><rect x="44" y="46.5" width="17" height="9" rx="4" fill="#FFDE2E"/><circle cx="42" cy="51.0" r="3.6" fill="#5A3A26"/><rect x="40" y="61.5" width="17" height="9" rx="4" fill="#16E3AE"/><circle cx="38" cy="66.0" r="3.6" fill="#1B1B1B"/><rect x="44" y="76.5" width="17" height="9" rx="4" fill="#FF9A3C"/><circle cx="42" cy="81.0" r="3.6" fill="#2A1D16"/>' +
      '<rect x="66" y="2" width="6" height="112" fill="#67584A"/>' +
      '<rect x="77.0" y="7.5" width="9" height="11" rx="3.5" fill="#4C8DFF"/><circle cx="78.5" cy="13.0" r="3.1" fill="#2A1D16"/><rect x="77.0" y="22.5" width="9" height="11" rx="3.5" fill="#FF4FA3"/><circle cx="78.5" cy="28.0" r="3.1" fill="#5A3A26"/><rect x="77.0" y="37.5" width="9" height="11" rx="3.5" fill="#FF9A3C"/><circle cx="78.5" cy="43.0" r="3.1" fill="#1B1B1B"/><rect x="77.0" y="52.5" width="9" height="11" rx="3.5" fill="#FFDE2E"/><circle cx="78.5" cy="58.0" r="3.1" fill="#2A1D16"/><rect x="77.0" y="67.5" width="9" height="11" rx="3.5" fill="#F1E9D2"/><circle cx="78.5" cy="73.0" r="3.1" fill="#5A3A26"/><rect x="77.0" y="82.5" width="9" height="11" rx="3.5" fill="#16E3AE"/><circle cx="78.5" cy="88.0" r="3.1" fill="#1B1B1B"/><rect x="77.0" y="97.5" width="9" height="11" rx="3.5" fill="#4C8DFF"/><circle cx="78.5" cy="103.0" r="3.1" fill="#2A1D16"/><rect x="88.5" y="14.5" width="9" height="11" rx="3.5" fill="#FF4FA3"/><circle cx="90.0" cy="20.0" r="3.1" fill="#5A3A26"/><rect x="88.5" y="29.5" width="9" height="11" rx="3.5" fill="#FF9A3C"/><circle cx="90.0" cy="35.0" r="3.1" fill="#1B1B1B"/><rect x="88.5" y="44.5" width="9" height="11" rx="3.5" fill="#FFDE2E"/><circle cx="90.0" cy="50.0" r="3.1" fill="#2A1D16"/><rect x="88.5" y="59.5" width="9" height="11" rx="3.5" fill="#F1E9D2"/><circle cx="90.0" cy="65.0" r="3.1" fill="#5A3A26"/><rect x="88.5" y="74.5" width="9" height="11" rx="3.5" fill="#16E3AE"/><circle cx="90.0" cy="80.0" r="3.1" fill="#1B1B1B"/><rect x="88.5" y="89.5" width="9" height="11" rx="3.5" fill="#4C8DFF"/><circle cx="90.0" cy="95.0" r="3.1" fill="#2A1D16"/><rect x="100.0" y="7.5" width="9" height="11" rx="3.5" fill="#FF4FA3"/><circle cx="101.5" cy="13.0" r="3.1" fill="#5A3A26"/><rect x="100.0" y="22.5" width="9" height="11" rx="3.5" fill="#FF9A3C"/><circle cx="101.5" cy="28.0" r="3.1" fill="#1B1B1B"/><rect x="100.0" y="37.5" width="9" height="11" rx="3.5" fill="#FFDE2E"/><circle cx="101.5" cy="43.0" r="3.1" fill="#2A1D16"/><rect x="100.0" y="52.5" width="9" height="11" rx="3.5" fill="#F1E9D2"/><circle cx="101.5" cy="58.0" r="3.1" fill="#5A3A26"/><rect x="100.0" y="67.5" width="9" height="11" rx="3.5" fill="#16E3AE"/><circle cx="101.5" cy="73.0" r="3.1" fill="#1B1B1B"/><rect x="100.0" y="82.5" width="9" height="11" rx="3.5" fill="#4C8DFF"/><circle cx="101.5" cy="88.0" r="3.1" fill="#2A1D16"/><rect x="100.0" y="97.5" width="9" height="11" rx="3.5" fill="#FF4FA3"/><circle cx="101.5" cy="103.0" r="3.1" fill="#5A3A26"/>' +
      '</svg>';
    if (nome === 'musica') return S + '0 0 24 24">' +
      '<path d="M9 17.5 V5.5 L19 3.5 V15.5" fill="none" stroke="#C9C7B4" stroke-width="1.8" stroke-linejoin="round"/>' +
      '<ellipse cx="6.6" cy="17.6" rx="2.9" ry="2.3" fill="#C9C7B4"/><ellipse cx="16.6" cy="15.6" rx="2.9" ry="2.3" fill="#C9C7B4"/>' +
      '</svg>';
    if (nome === 'seta') return S + '0 0 24 24">' +
      '<path d="M12 21 V5 M5.5 11 L12 4 L18.5 11" fill="none" stroke="#7DD957" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"/>' +
      '</svg>';
    if (nome === 'saida') return S + '0 0 24 24">' +
      '<rect x="3" y="3" width="18" height="18" rx="3" fill="none" stroke="#9FA087" stroke-width="1.4"/>' +
      '<circle cx="12.5" cy="7" r="1.7" fill="#9FA087"/>' +
      '<path d="M8 13 L11 10 L14 11.5 L16.5 10.5 M11 10 L10.5 14.5 L13.5 16.5 L13 20 M10.5 14.5 L7.5 18" fill="none" stroke="#9FA087" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>' +
      '</svg>';
    if (nome === 'wc') return S + '0 0 24 24">' +
      '<rect x="2.5" y="2.5" width="19" height="19" rx="3" fill="none" stroke="#9FA087" stroke-width="1.4"/>' +
      '<circle cx="8.5" cy="7.2" r="1.7" fill="#9FA087"/><path d="M6.4 10 h4.2 l1 5.5 h-1.8 v4 h-2.6 v-4 h-1.8 Z" fill="#9FA087"/>' +
      '<circle cx="15.8" cy="7.2" r="1.7" fill="#9FA087"/><rect x="14.2" y="10" width="3.2" height="9.5" rx="1" fill="#9FA087"/>' +
      '<path d="M12.1 5.5 V18.5" stroke="#9FA087" stroke-width=".9"/>' +
      '</svg>';
    return '';
  }

  // ---- o desenho da planta. As 3 telas (página pública, painel do gestor, app da
  // vendedora) chamam ESTA função; cada uma só entrega o botão do stand (o.tile) e
  // os nomes das suas classes. Posição e tamanho vêm de PLANTA (medidos no PDF).
  //   o.larg  largura da planta em px     o.pad    respiro interno do quadro
  //   o.vao   folga entre stands vizinhos   o.miolo  borda + respiro do botão (px)
  // O cenário (paredes, rótulos, ícones) usa as classes .plc de PLANTA_CSS.
  function plantaMontar(grid, pav, o){
    var P = pav.planta || {w:1000, h:0, stands:{}, decor:[]};
    var k = o.larg / P.w, pad = o.pad || 0, vao = o.vao == null ? 2 : o.vao, miolo = o.miolo || 4;
    function px(v){ return Math.round(v * k * 10) / 10; }
    function poe(el, r, folga){
      el.style.position = 'absolute';
      el.style.boxSizing = 'border-box';
      el.style.left = (pad + px(r[0]) + folga / 2) + 'px';
      el.style.top = (pad + px(r[1]) + folga / 2) + 'px';
      el.style.width = Math.max(1, px(r[2]) - folga) + 'px';
      el.style.height = Math.max(1, px(r[3]) - folga) + 'px';
    }
    grid.innerHTML = '';
    (P.decor || []).forEach(function(d){
      var el = document.createElement('div');
      el.className = 'plc plc-' + d.k.split(' ').join(' plc-');
      poe(el, d.r, 0);
      if (d.ic) el.innerHTML = plantaIcone(d.ic, d.cor);   // SVG fixo do código, nunca dado de fora
      else if (d.t) el.textContent = d.t;
      if (d.g) el.style.transform = 'rotate(' + d.g + 'deg)';
      grid.appendChild(el);
    });
    // a fila (mesmo topo, mesma altura, mesmo nº de letras) usa a largura do stand mais
    // estreito dela: a letra e a quebra em duas linhas saem iguais pros vizinhos
    var fila = {};
    function chave(code){ var r = P.stands[code]; return r[1] + '|' + r[3] + '|' + code.length; }
    Object.keys(P.stands).forEach(function(code){
      var k2 = chave(code), w = P.stands[code][2];
      if (fila[k2] == null || w < fila[k2]) fila[k2] = w;
    });
    Object.keys(P.stands).forEach(function(code){
      var el = o.tile(code);
      if (!el) return;
      var r = P.stands[code];
      poe(el, r, vao);
      // a letra cabe no stand: o código (3 ou 4 letras) nunca encosta na borda
      var lw = px(fila[chave(code)]) - vao, lh = px(r[3]) - vao;
      var f = Math.min(10, (lw - miolo) / (0.62 * code.length), lh * 0.45);
      if (f < 8 && code.length > 3 && lh > lw * 1.3){
        // estreito e alto (S127–S129, S143–S154): a letra em cima, o número embaixo —
        // como a própria planta do PDF escreve. O texto do botão continua "S145".
        el.textContent = '';
        el.appendChild(document.createTextNode(code.slice(0, 1)));
        el.appendChild(document.createElement('br'));
        el.appendChild(document.createTextNode(code.slice(1)));
        el.style.flexDirection = 'column';
        el.style.lineHeight = '1.05';
        el.setAttribute('aria-label', code);      // leitor de tela lê "S145", não "S 145"
        f = Math.min(10, (lw - miolo) / (0.62 * (code.length - 1)), (lh - miolo) / 2.1);
      }
      el.style.fontSize = Math.max(6.5, f).toFixed(1) + 'px';
      grid.appendChild(el);
    });
    // stand do banco que não está na planta desenhada: fila corrida embaixo —
    // a tela nunca esconde stand que existe
    var alt = px(P.h), ex = pav.extraCodes || [], x = 0, y = alt ? alt + 14 : 0;
    ex.forEach(function(code){
      var el = o.tile(code);
      if (!el) return;
      if (x + 36 > o.larg){ x = 0; y += 30; }
      el.style.position = 'absolute'; el.style.boxSizing = 'border-box';
      el.style.left = (pad + x) + 'px'; el.style.top = (pad + y) + 'px';
      el.style.width = '36px'; el.style.height = '26px';
      grid.appendChild(el);
      x += 40; alt = y + 26;
    });
    grid.style.setProperty('--plk', Math.min(1, o.larg / 800).toFixed(3));
    grid.style.display = 'block';
    grid.style.boxSizing = 'content-box';
    grid.style.width = o.larg + 'px';
    grid.style.height = Math.ceil(alt) + 'px';
  }
  // os códigos de stand que a planta desenha, na ordem do pavilhão
  function plantaCodigos(pav){ return Object.keys((pav.planta || {}).stands || {}); }
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
  /* com a foto da fachada: texto à esquerda, foto à direita; no celular a foto sobe
     pro topo, de ponta a ponta */
  .hero-inner.com-foto{display:grid;grid-template-columns:minmax(0,1fr) minmax(0,clamp(320px,42vw,500px));gap:32px;align-items:center;}
  .hero-foto{margin:0;border-radius:14px;overflow:hidden;border:1px solid var(--line);box-shadow:var(--shadow);background:#0e0f0a;aspect-ratio:16/9;}
  .hero-foto img{display:block;width:100%;height:100%;object-fit:cover;}
  @media (max-width:860px){
    .hero-inner.com-foto{grid-template-columns:minmax(0,1fr);gap:18px;}
    /* tablet em pé e celular deitado: de ponta a ponta a foto ficava maior que a tela
       e empurrava título e mapa pra fora — limita pela altura da janela */
    .hero-foto{order:-1;width:100%;max-width:min(100%,560px,calc(55vh*16/9));justify-self:center;}
  }
  .brand{font-size:clamp(34px,7vw,58px);line-height:0.95;margin:0;}
  .brand span{color:var(--mint);}
  .hero-meta{display:flex;flex-wrap:wrap;gap:14px 22px;margin-top:14px;font-size:14px;color:#C9C7B4;}
  .hero-meta b{color:#F3F1E3;font-weight:700;}
  .hero-pitch{max-width:62ch;margin-top:14px;color:#C9C7B4;font-size:14.5px;line-height:1.55;}
  .info-strip{margin-top:18px;padding:10px 14px;border-radius:10px;background:rgba(22,227,174,0.12);border:1px solid rgba(22,227,174,0.35);font-size:13px;color:#D8F5E9;line-height:1.5;}
  .cli-box{margin-top:12px;padding:12px 14px;border:1.5px solid var(--mint);border-radius:12px;background:var(--surface);}
  .cli-nota{margin:0;font-size:12.5px;color:#CFEFE3;line-height:1.45;}
  .cli-nota b{color:var(--fg);}

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

  .legend{display:flex;flex-wrap:wrap;align-items:center;gap:16px;margin:4px 0 16px;font-size:12.5px;color:var(--fg-dim);}
  .legend-item{display:flex;align-items:center;gap:6px;}
  .dot{width:10px;height:10px;border-radius:3px;display:inline-block;}
  /* a amostra de status é um mini-tile: recebe as MESMAS declarações do tile (mais abaixo) */
  .legend .dot{width:20px;height:14px;border-radius:4px;box-sizing:border-box;flex:0 0 auto;}
  /* display:flex de autor vencia o atributo hidden: as duas legendas apareciam juntas */
  .legend[hidden],.size-legend[hidden]{display:none;}

  /* ---- floor stage ---- */
  /* Sem margem negativa: o mapa agora vive DENTRO da coluna esquerda do
     .layout, com o painel de reserva colado do lado direito (pedido do dono,
     29/09/2026: "a caixa da reserva embaixo não ficou legal"). */
  .floor-outer{overflow:auto;margin:0;padding:36px 12px 54px;max-height:78vh;}
  .floor-stage{display:flex;justify-content:center;min-width:min-content;perspective:2000px;}
  /* pointer-events: o .floor-zoom é só a moldura do zoom. No Mapa 3D ele divide o
     espaço 3D com os stands (que agora são filhos diretos do chão, cada um com seu
     translateZ) e o navegador entregava o clique PRA ELE, não pro stand — no
     computador nenhum stand selecionava no 3D (01/10/2026). Quem recebe o clique é
     o chão (.floor-grid) e o que está em cima dele. */
  .floor-zoom{transform-style:preserve-3d;transition:transform .25s ease;transform-origin:50% 0;pointer-events:none;}
  .floor-grid{
    position:relative;padding:20px;border-radius:18px;pointer-events:auto;
    --pl-dim:var(--fg-dim);--pl-line:rgba(245,243,230,0.2);--pl-surf:var(--surface);
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


  .stand{
    appearance:none;cursor:pointer;border:none;border-radius:5px;
    font-family:'IBM Plex Mono',monospace;font-size:8.6px;font-weight:700;line-height:1;
    display:flex;align-items:center;justify-content:center;text-align:center;padding:2px;
    transform:translateZ(4px);transition:transform .15s ease, box-shadow .15s ease;
    flex:0 0 auto;
  }
  /* status = luz + forma, igual nas 3 telas (página pública, painel, app):
     LIVRE = cheio e liso · RESERVADO = cheio com hachura diagonal · VENDIDO = escuro com contorno.
     A mesma regra pinta o tile (3D e planta) e a amostra da legenda. */
  .stand.st-livre,.legend .dot.livre{background:linear-gradient(155deg, color-mix(in srgb, var(--mint) 88%, white 16%), var(--mint));color:var(--mint-fg);}
  .stand.st-reservado,.legend .dot.reservado{background:repeating-linear-gradient(135deg, rgba(36,24,0,.28) 0 calc(2px*var(--inv,1)), rgba(36,24,0,0) calc(2px*var(--inv,1)) calc(6px*var(--inv,1))), linear-gradient(155deg, color-mix(in srgb, var(--gold) 85%, white 18%), var(--gold));color:var(--gold-fg);}
  .stand.st-vendido,.legend .dot.vendido{background:color-mix(in srgb, var(--coral) 30%, var(--bg));color:color-mix(in srgb, var(--coral-fg) 70%, var(--coral));border:calc(1px*var(--inv,1)) solid var(--coral-strong);}
  /* volume do 3D: livre e reservado como na maquete; vendido vira laje baixa, sem opacidade */
  .stand.st-livre{box-shadow:0 3px 0 var(--mint-strong), 0 5px 8px rgba(0,0,0,0.16);}
  .stand.st-reservado{box-shadow:0 3px 0 var(--gold-strong), 0 5px 8px rgba(0,0,0,0.16);}
  .stand.st-vendido{box-shadow:0 2px 0 rgba(0,0,0,0.5);}
  .stand:hover{transform:translateZ(8px);}
  .stand.is-selected{transform:translateZ(16px);outline:2px solid var(--fg);outline-offset:1px;z-index:3;}
  .stand:focus-visible{outline:2px solid var(--fg);outline-offset:1px;z-index:3;}
  /* planta técnica: os MESMOS preenchimentos do 3D, só sem volume */
  .floor-grid:not(.is-3d) .stand{box-shadow:none;transform:none;}
  .floor-grid:not(.is-3d) .stand:hover{transform:none;outline:1px solid var(--fg);outline-offset:1px;}
  .floor-grid:not(.is-3d) .stand.is-selected{outline:2px solid var(--fg);transform:none;}

  /* cor por tamanho — mesma legenda de cores da planta original do PDF.
     --sz guarda a cor do tamanho pra regra de status logo abaixo. */
  .floor-grid.by-size .stand.sz-4x2{--sz:#FF4FA3;background:linear-gradient(155deg, color-mix(in srgb, #FF4FA3 88%, white 16%), #FF4FA3);color:#360019;box-shadow:0 3px 0 #C23378, 0 5px 8px rgba(0,0,0,0.16);}
  .floor-grid.by-size .stand.sz-4x3{--sz:#4C8DFF;background:linear-gradient(155deg, color-mix(in srgb, #4C8DFF 88%, white 16%), #4C8DFF);color:#04143B;box-shadow:0 3px 0 #2F63C2, 0 5px 8px rgba(0,0,0,0.16);}
  .floor-grid.by-size .stand.sz-3x2{--sz:#2BD4E0;background:linear-gradient(155deg, color-mix(in srgb, #2BD4E0 88%, white 16%), #2BD4E0);color:#022B2E;box-shadow:0 3px 0 #1DA3AD, 0 5px 8px rgba(0,0,0,0.16);}
  .floor-grid.by-size .stand.sz-2x2{--sz:#B073FF;background:linear-gradient(155deg, color-mix(in srgb, #B073FF 88%, white 16%), #B073FF);color:#1D0940;box-shadow:0 3px 0 #8850D6, 0 5px 8px rgba(0,0,0,0.16);}
  .floor-grid.by-size .stand.sz-3x3{--sz:#FF9A3C;background:linear-gradient(155deg, color-mix(in srgb, #FF9A3C 88%, white 16%), #FF9A3C);color:#3A1900;box-shadow:0 3px 0 #C97323, 0 5px 8px rgba(0,0,0,0.16);}
  .floor-grid.by-size .stand.sz-tenda{--sz:#FFDE2E;background:linear-gradient(155deg, color-mix(in srgb, var(--yellow) 88%, white 16%), var(--yellow));color:var(--yellow-fg);box-shadow:0 3px 0 #C9A800, 0 5px 8px rgba(0,0,0,0.16);}
  .floor-grid.by-size .stand.sz-personalizado{--sz:#6B5CFF;background:linear-gradient(155deg, color-mix(in srgb, #6B5CFF 88%, white 16%), #6B5CFF);color:#000;box-shadow:0 3px 0 #4B3FC9, 0 5px 8px rgba(0,0,0,0.16);}
  .floor-grid.by-size.is-3d .stand:hover{transform:translateZ(8px);}
  .floor-grid.by-size.is-3d .stand.is-selected{transform:translateZ(16px);outline:2px solid var(--fg);}
  .floor-grid.by-size:not(.is-3d) .stand{box-shadow:none !important;}
  /* STATUS POR CIMA DA COR DO TAMANHO. A cor continua sendo do tamanho; o status vem da luz e da forma:
     livre = cor cheia · reservado = apagado com hachura · vendido = apagado, só contorno.
     O [class*="sz-"] sobe a especificidade pra (0,5,0): vence as regras .sz-* e a da planta
     (0,4,0) em qualquer ordem — mesmo assim, manter este bloco DEPOIS delas. */
  .legend.by-size .dot{--sz:var(--fg-dim);}
  .legend.by-size .dot.livre{background:var(--sz);}
  .floor-grid.by-size .stand.st-reservado[class*="sz-"],
  .legend.by-size .dot.reservado{background:repeating-linear-gradient(135deg, color-mix(in srgb, var(--sz, var(--gold)) 38%, var(--floor)) 0 calc(2px*var(--inv,1)), color-mix(in srgb, var(--sz, var(--gold)) 13%, var(--floor)) calc(2px*var(--inv,1)) calc(6px*var(--inv,1)));color:var(--fg);border:calc(1px*var(--inv,1)) solid var(--sz, var(--gold));}
  .floor-grid.by-size .stand.st-vendido[class*="sz-"],
  .legend.by-size .dot.vendido{background:color-mix(in srgb, var(--sz, var(--coral)) 8%, var(--floor));color:var(--fg-dim);border:calc(1px*var(--inv,1)) solid color-mix(in srgb, var(--sz, var(--coral-strong)) 85%, var(--floor));}
  .floor-grid.by-size .stand.st-reservado[class*="sz-"]{box-shadow:0 3px 0 color-mix(in srgb, var(--sz, var(--gold)) 28%, #000), 0 5px 8px rgba(0,0,0,0.16);}
  .floor-grid.by-size .stand.st-vendido[class*="sz-"]{box-shadow:none;}

  .size-legend{display:flex;flex-wrap:wrap;gap:12px;margin:4px 0 16px;font-size:11.5px;color:var(--fg-dim);}
  .size-legend .dot{width:10px;height:10px;border-radius:3px;display:inline-block;}

""" + PLANTA_CSS + """
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
  <div class="hero-inner{% if fachada %} com-foto{% endif %}">
    <div class="hero-texto">
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
    {% if fachada %}<figure class="hero-foto"><img src="/estatico/stands/{{ fachada.arquivo }}.jpg" srcset="/estatico/stands/{{ fachada.arquivo }}-800.jpg 800w, /estatico/stands/{{ fachada.arquivo }}.jpg 1600w" sizes="(max-width:860px) calc(100vw - 34px), 500px" width="1600" height="900" alt="{{ fachada.alt }}" decoding="async"></figure>{% endif %}
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
    <span class="legend-item"><span class="dot reservado"></span>Reservado</span>
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
var CLIENTE_LINK = {{ cliente_json|safe }};  // o cadastro feito antes pela vendedora (`c` do link)
var SEM_STORAGE = {{ 'true' if sem_storage else 'false' }};
</script>
{% raw %}<script>
(function(){
  var sizeLabel = {'4x2':'4x2m','4x3':'4x3m','3x2':'3x2m','2x2':'2x2m','3x3':'3x3m','tenda':'Espaço em tenda','personalizado':'Stand personalizado'};
  var sizePhoto = {'4x2':'/estatico/stands/4x2.jpg','4x3':'/estatico/stands/4x3.jpg','3x2':'/estatico/stands/3x2.jpg','2x2':'/estatico/stands/2x2.jpg','3x3':'/estatico/stands/3x3.jpg'};

""" + PLANTA_DEFS_JS + r"""
  // A planta desenhada acima + o BANCO: cada def só vira stand se o código
  // existir no servidor (STANDS); status/zona/tamanho/preço vêm de lá.
  var usados = {};
  var stands = [];
  pavilions.forEach(function(p){
    plantaCodigos(p).forEach(function(code){
      var sv = STANDS[code];
      if (!sv) return;
      usados[code] = true;
      stands.push({ code:code, zone:sv.zona, pavilion:p.key, size:sv.tamanho,
                    preco:sv.preco, precoC:sv.preco_centavos || 0, status:sv.status, dias:sv.dias, expositor:sv.expositor });
    });
  });
  // Estandes do banco fora da planta desenhada (código novo, ou outra conta):
  // caem numa fila corrida embaixo do pavilhão — e pavilhão desconhecido vira
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
      pav = { key:pk, label:pk.replace(/_/g,' ').replace(/\\b\\w/g,function(c){return c.toUpperCase();}), sub:'' };
      pavilions.push(pav);
    }
    pav.extraCodes = extras[pk].sort();
  });

  var currentPavilion = pavilions[0] ? pavilions[0].key : 'inferior';
  var selectedCode = null;
  // A RESERVA (até 2 stands da mesma empresa, um contrato só): `cart` são os stands
  // juntados; `draft` guarda o que a pessoa já digitou, porque a caixa é refeita a
  // cada stand adicionado.
  var cart = [], adicionando = false,
      draft = {nome: CLIENTE_LINK ? CLIENTE_LINK.nome : '',
               whatsapp: CLIENTE_LINK ? CLIENTE_LINK.whatsapp : '', sinal:null};
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
    // a legenda de status fica SEMPRE: o status aparece também em "Cor por tamanho"
    // (amostras neutras — só a forma: cheio, hachurado, contorno)
    document.getElementById('legend-status').classList.toggle('by-size', mode === 'tamanho');
    document.getElementById('legend-size').hidden = mode !== 'tamanho';
    var grid = document.getElementById('floor-grid');
    if (mode === 'tamanho') grid.classList.add('by-size'); else grid.classList.remove('by-size');
  }
  window.setColorMode = setColorMode;

  function aplicarZoom(){
    document.getElementById('floor-zoom').style.transform = 'scale(' + (baseScale * zoomLevel) + ')';
    // mapa encolhido (celular): a hachura e o contorno engrossam na mesma medida,
    // senão o reservado e o vendido perdem a forma que diz o status
    document.getElementById('floor-grid').style.setProperty('--inv', Math.min(2.6, 1 / (baseScale * zoomLevel)).toFixed(2));
    document.getElementById('zoom-level').textContent = Math.round(zoomLevel * 100) + '%';
  }
  function zoomFloor(dir){
    zoomLevel = Math.min(1.8, Math.max(0.7, Math.round((zoomLevel + dir * 0.15) * 100) / 100));
    aplicarZoom();
  }
  window.zoomFloor = zoomFloor;

  // No celular a planta (930px de largura) não cabe na tela: em vez de scroll
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
  // Largura da planta em px. No computador ela ocupa a coluna do mapa inteira, sem
  // rolar pro lado (o Outlet Car vai até a borda direita); no celular fica em 930 e
  // o mapa todo encolhe (ajustarEscala).
  var largPlanta = 930;
  function larguraPlanta(){
    if (window.innerWidth < 860) return 930;
    var outer = document.querySelector('.floor-outer');
    return Math.max(620, Math.min(930, (outer ? outer.clientWidth : 994) - 64));
  }
  window.addEventListener('resize', function(){
    if (larguraPlanta() !== largPlanta) renderFloor(); else ajustarEscala();
  });

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

  // só o botão: posição e tamanho quem dá é a planta medida (plantaMontar)
  function standButton(s){
    var btn = document.createElement('button');
    btn.className = 'stand st-' + s.status + ' sz-' + s.size + (s.code === selectedCode || cart.indexOf(s.code) >= 0 ? ' is-selected' : '');
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
    if (floorView === '3d') grid.classList.add('is-3d'); else grid.classList.remove('is-3d');
    largPlanta = larguraPlanta();
    plantaMontar(grid, pav, {larg: largPlanta, pad: 20, tile: function(code){
      var s = stOf(code);
      return s ? standButton(s) : null;
    }});
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
        if (CLIENTE_LINK) html += '  <input type="hidden" name="cliente" value="' + esc(CLIENTE_LINK.codigo) + '">';
        html += '<div class="sinal-box"><div class="sinal-tit">Sinal e saldo</div>';
        html += '<div class="panel-row"><span>Sinal mínimo</span><b>R$ 1.500 por stand' + (n > 1 ? ' · ' + reais(minimo) : '') + '</b></div>';
        html += '<label class="fld"><span>Valor do sinal enviado <i>*</i></span><input class="up-input" type="text" name="sinal" inputmode="decimal" value="' + esc(sinalV) + '" oninput="atualizaSaldo(this.value)"></label>';
        html += '<div class="panel-row"><span>Saldo</span><b id="v-saldo">' + reais(Math.max(0, totalR - Math.round(numBR(sinalV) * 100))) + '</b></div>';
        if (REGRAS.saldoAte) html += '<div class="panel-row"><span>Saldo até</span><b>' + esc(REGRAS.saldoAte) + ' (dia do evento)</b></div>';
        html += '<p class="upload-sub" style="margin-top:6px;text-align:left">Pode mandar mais que o mínimo agora. O saldo é pago em uma ou mais vezes, por Pix ou cartão' + (REGRAS.saldoAte ? ', até ' + esc(REGRAS.saldoAte) : '') + ' — a organização registra cada pagamento.</p></div>';
        // cliente cadastrado antes pela vendedora: os dados dele já vêm preenchidos
        if (CLIENTE_LINK) html += '<div class="cli-box"><p class="cli-nota">Seus dados já vieram do cadastro feito por ' + (CLIENTE_LINK.vendedora ? '<b>' + esc(CLIENTE_LINK.vendedora) + '</b>' : 'quem te atendeu') + '. Confira:</p>';
        html += '  <label class="fld"><span>Nome fantasia</span><input class="up-input" type="text" name="nome" placeholder="Ex.: Boutique Nova Era" required maxlength="200" autocomplete="organization" value="' + esc(draft.nome) + '" oninput="draftCampo(this.name, this.value)"></label>';
        html += '  <label class="fld"><span>WhatsApp <i>*</i></span><input class="up-input" type="tel" name="whatsapp" placeholder="(86) 9 9999-9999" required maxlength="40" autocomplete="tel" value="' + esc(draft.whatsapp) + '" oninput="draftCampo(this.name, this.value)"></label>';
        if (CLIENTE_LINK) html += '</div>';
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
