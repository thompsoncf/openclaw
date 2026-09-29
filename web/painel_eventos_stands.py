"""A aba Estandes: /painel/eventos/estandes — o mapa de venda por trás do
/e/<slug> público (migração 448, finance/evento_stands.py).

ONDE ENCAIXAR (decisão de projeto, 28/09/2026): painel DEDICADO, e não uma aba
dentro de web/painel_servicos.py. Comparado o padrão recente (painel_obras.py,
painel_clinica_numeros.py — tela própria por assunto, gate por nicho/feature)
contra o padrão antigo (painel_servicos.py, funil de 3 abas dentro de uma tela
só): estande de feira não é uma PROPOSTA em pipeline (sumiu_apos_proposta,
com_o_cliente...) — é uma UNIDADE FÍSICA com posição na planta, ocupação e
prazo de reserva, muito mais perto do que painel_obras já faz pra casa/reforma
(KPIs + lista de pendência de confirmação) do que do funil de vendas. Um dia,
se a Outlet Chic também vender por proposta consultiva, a proposta dela
aparece no painel_servicos normal — as duas coisas não competem.

USA O MESMO CASCO DE TODA TELA DO PAINEL (base.html via _render — o mesmo
`extends "base"` de painel_obras.py), e não uma página HTML solta: a versão
anterior desta tela desenhava `<html>` própria, sem menu nem cabeçalho — não
aparecia em lugar nenhum de dentro do produto e não tinha como chegar nela sem
colar a URL (achado do dono, 29/09/2026, revisando o PR #919 já mergeado). O
item de menu correspondente mora em web/portal.py (`tem_estandes` em
`_render()`, os dois `navi('estandes', ...)` perto de Obras).

O CONTEÚDO é o port fiel da "Visão do Gestor" da maquete aprovada
(scratchpad/outlet-chic-mockup.html, v11 — que já usava os tokens reais de
web/tema.py de propósito): linha de KPIs, barras de ocupação por espaço, o
CADASTRO COMPLETO dos stands no MESMO mapa da página pública (pedido do dono,
29/09/2026: "tem que ter o cadastro de todos os stands vinculado ao do site —
os vendedores vão saber o que tá livre") e o funil de propostas em 3 abas
(Precisa de mim / Com o cliente / Fechada) com linha expansível — badge do
stand na cor do tamanho, sub-abas de comprovante, contrato e cliente. A
diferença pra maquete é só a fonte dos dados: status vem de evento_stands, o
interessado vem de prospeccao, o contrato de finance/contrato (nasce junto com
o comprovante do sinal — ver evento_stands.garantir_orcamento_e_contrato), e
as ações (confirmar/liberar/ver comprovante) são os POSTs reais abaixo.

GATE (opt-in, igual ao resto do módulo): nicho 'eventos' E a conta ter
`evento_stands_config` — nem toda conta de eventos vende estande numerado
(Prime Eventos, conta 34, não vende).

QUEM VÊ: dono, gestor E vendedor — o vendedor precisa saber na hora o que está
livre pra oferecer na conversa (pedido do dono, 29/09/2026); a rota dele entra
na whitelist em contas/equipe.rotas_do_papel. Quem AGE (confirmar pagamento,
liberar, abrir comprovante) continua sendo dono/gestor: confirmar venda é
decisão de quem administra, mesmo corte de painel_obras.
"""
from __future__ import annotations

import json
import logging

from fastapi import APIRouter, File, Form, Request, UploadFile
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response
from starlette.concurrency import run_in_threadpool

from db.conexao import get_pool
from finance import comprovantes as comprov
from finance import contrato as ctr
from finance import evento_stands as es
from web.loja_stands import PLANTA_DEFS_JS
from web.portal import _env, _render, brl, conta_logada, nicho_da_conta

router = APIRouter()
_log = logging.getLogger("openclaw.painel_eventos_stands")

_PAPEIS_GERIR = ("dono", "gestor")
_PAPEIS_VER = ("dono", "gestor", "vendedor")

# Cores por tamanho — a MESMA paleta da planta oficial do PDF que a página
# pública usa na legenda "cor por tamanho" (e que a maquete usava no badge).
_COR_TAM = {
    "4x2": ("#FF4FA3", "#360019"), "4x3": ("#4C8DFF", "#04143B"),
    "3x2": ("#2BD4E0", "#022B2E"), "2x2": ("#B073FF", "#1D0940"),
    "3x3": ("#FF9A3C", "#3A1900"), "tenda": ("#FFDE2E", "#241C00"),
    "personalizado": ("#6B5CFF", "#0D0836"),
}
_TAM_LABEL = {"4x2": "4x2m", "4x3": "4x3m", "3x2": "3x2m", "2x2": "2x2m",
              "3x3": "3x3m", "tenda": "Tenda", "personalizado": "Custom"}
_MES = ["jan", "fev", "mar", "abr", "mai", "jun",
        "jul", "ago", "set", "out", "nov", "dez"]


def _data_curta(dt) -> str:
    return f"{dt.day} {_MES[dt.month - 1]}" if dt else "—"


def _para_centavos(txt: str) -> int | None:
    """'3.500', 'R$ 3.500,00', '3500' -> 350000. Formato BR: ponto é milhar,
    vírgula é decimal. Vazio/zero/lixo -> None (campo não mexe no preço)."""
    t = (txt or "").strip().replace("R$", "").replace(" ", "")
    if not t:
        return None
    t = t.replace(".", "").replace(",", ".")
    try:
        v = round(float(t) * 100)
    except ValueError:
        return None
    return v if v > 0 else None


def _acesso(request: Request, papeis=_PAPEIS_GERIR):
    """(conta, config) ou (None, redirect) — mesma dupla checagem de nicho +
    feature ligada que o resto do painel usa. `papeis` abre a LEITURA pro
    vendedor sem abrir as ações (cada rota escolhe o corte)."""
    conta = conta_logada(request)
    if conta is None:
        return None, RedirectResponse("/login", status_code=303)
    if request.session.get("papel", "dono") not in papeis:
        return None, RedirectResponse("/painel", status_code=303)
    if nicho_da_conta(conta) != "eventos":
        return None, RedirectResponse("/painel", status_code=303)
    pool = get_pool()
    cfg = es.obter_config(pool, conta[0])
    if cfg is None:
        return None, RedirectResponse("/painel", status_code=303)
    return conta, cfg


def _prospeccoes(pool, conta_id: int, ids: list[int]) -> dict[int, dict]:
    """empresa/whatsapp dos interessados, num SELECT só — é o nome que aparece
    na linha do funil no lugar do 'expositor' fake da maquete."""
    if not ids:
        return {}
    with pool.connection() as c:
        rows = c.execute(
            """select id, empresa, whatsapp, criado_em from prospeccao
               where conta_id=%s and id = any(%s)""", (conta_id, ids)).fetchall()
    return {r[0]: {"empresa": r[1], "whatsapp": r[2], "criado_em": r[3]} for r in rows}


@router.get("/painel/eventos/estandes", response_class=HTMLResponse)
def painel_eventos_stands(request: Request):
    conta, cfg_ou_redir = _acesso(request, papeis=_PAPEIS_VER)
    if conta is None:
        return cfg_ou_redir
    cfg = cfg_ou_redir
    pode_gerir = request.session.get("papel", "dono") in _PAPEIS_GERIR
    pool = get_pool()
    stands = es.listar(pool, conta[0])

    kpis = {"total": len(stands), "livre": 0, "pre_reservado": 0, "vendido": 0,
            "faturamento_centavos": 0, "a_confirmar_centavos": 0}
    por_pavilhao: dict = {}
    for s in stands:
        kpis[s["status"]] = kpis.get(s["status"], 0) + 1
        if s["status"] == "vendido":
            kpis["faturamento_centavos"] += int(s["preco_centavos"] or 0)
        elif s["status"] == "pre_reservado":
            kpis["a_confirmar_centavos"] += int(s["preco_centavos"] or 0)
        pav = por_pavilhao.setdefault(s["pavilhao"], {"total": 0, "livre": 0,
                                                       "pre_reservado": 0, "vendido": 0})
        pav["total"] += 1
        pav[s["status"]] += 1

    interessados = _prospeccoes(pool, conta[0],
                                [s["prospeccao_id"] for s in stands if s["prospeccao_id"]])

    # O CADASTRO COMPLETO pro mapa (mesma planta da página pública): status já
    # traduzido pras classes .st-*, e o nome do interessado só onde não é livre
    # — é o que o vendedor precisa ver antes de oferecer.
    mapa_json = json.dumps({
        s["codigo"]: {
            "pavilhao": s["pavilhao"], "zona": s["zona"] or "",
            "tamanho": s["tamanho"],
            "status": "reservado" if s["status"] == "pre_reservado" else s["status"],
            "preco": brl(s["preco_centavos"]) if s["preco_centavos"] else None,
            "cliente": ((interessados.get(s["prospeccao_id"]) or {}).get("empresa")
                        if s["status"] != "livre" else None),
        }
        for s in stands
    })

    # O funil de 3 abas da maquete, com o mapeamento REAL de cada grupo:
    # - precisa_de_mim: pré-reservado (comprovante chegou, sinal esperando o
    #   dono conferir — é a fila de trabalho)
    # - com_o_cliente: vendido COM proposta vinculada (sinal confirmado, o
    #   resto do plano — parcelas, contrato — mora na proposta)
    # - fechada: vendido sem pendência de proposta (venda direta pela página)
    funil = {"precisa_de_mim": [], "com_o_cliente": [], "fechada": []}
    for s in stands:
        if s["status"] == "livre":
            continue
        cli = interessados.get(s["prospeccao_id"]) or {}
        item = dict(s)
        item["cliente"] = cli
        # o contrato VIVO da proposta do estande (nasce junto com o comprovante
        # do sinal — evento_stands.garantir_orcamento_e_contrato); tolerante:
        # sem a migração 164, a aba abre sem contrato.
        item["contrato"] = (ctr.por_orcamento(pool, conta[0], s["orcamento_id"])
                            if s["orcamento_id"] else None)
        if s["status"] == "pre_reservado":
            item["resumo"] = "Comprovante recebido · sinal aguardando confirmação"
            item["pend"] = [("Confirmar sinal", "coral")]
            if item["contrato"] and not item["contrato"]["assinado_em"]:
                item["pend"].append(("Contrato na mão do lojista", "azul"))
            funil["precisa_de_mim"].append(item)
        elif s["orcamento_id"]:
            item["resumo"] = "Sinal confirmado · parcelas e contrato na proposta"
            item["pend"] = [("Acompanhar proposta", "azul")]
            funil["com_o_cliente"].append(item)
        else:
            item["resumo"] = "Pagamento confirmado · stand vendido"
            item["pend"] = []
            funil["fechada"].append(item)
    funil["precisa_de_mim"].sort(key=lambda s: s["pre_reserva_ate"] or "")

    # o vínculo (interessado/proposta/contrato) POR CÓDIGO, pra lista completa
    # não repetir as consultas do funil
    vinculos = {d["codigo"]: d for grupo in funil.values() for d in grupo}

    # VALORES POR TAMANHO (achado do dono, 29/09/2026: "onde eu cadastro o
    # valor?? por tamanho, alterável"): o valor mais comum de cada tamanho
    # pré-preenche o formulário; misturado = aviso, salvar iguala os livres.
    from collections import Counter
    precos_tam = []
    for tam in ("2x2", "3x2", "3x3", "4x2", "4x3", "tenda", "personalizado"):
        do_tam = [s for s in stands if s["tamanho"] == tam]
        if not do_tam:
            continue
        cont = Counter(int(s["preco_centavos"] or 0) for s in do_tam)
        comum = cont.most_common(1)[0][0]
        precos_tam.append({
            "tamanho": tam, "rotulo": _TAM_LABEL.get(tam, tam),
            "valor": (brl(comum)[3:] if comum else ""),
            "n_livres": sum(1 for s in do_tam if s["status"] == "livre"),
            "n_total": len(do_tam), "misto": len(cont) > 1})

    return _render(
        "estandes", request, titulo="Estandes", secao_ativa="estandes", brl=brl,
        cfg=cfg, kpis=kpis, por_pavilhao=por_pavilhao, funil=funil,
        cor_tam=_COR_TAM, tam_label=_TAM_LABEL, data_curta=_data_curta,
        mapa_json=mapa_json, pode_gerir=pode_gerir, stands=stands,
        vinculos=vinculos, precos_tam=precos_tam,
        sem_storage=not comprov.configurado(),
        erro=(request.query_params.get("erro") or "").strip(),
        ok=(request.query_params.get("ok") or "").strip())


@router.post("/painel/eventos/estandes/precos")
def salvar_precos(request: Request,
                  preco_2x2: str = Form(""), preco_3x2: str = Form(""),
                  preco_3x3: str = Form(""), preco_4x2: str = Form(""),
                  preco_4x3: str = Form(""), preco_tenda: str = Form(""),
                  preco_personalizado: str = Form("")):
    """O valor por TAMANHO — o cadastro de preço que faltava (o seed inicial
    era SQL na mão; achado do dono, 29/09/2026). Só mexe em quem está LIVRE:
    stand reservado/vendido mantém o valor do negócio que o comprovante
    fechou — reprecificar por baixo de um Pix já enviado mudaria o combinado
    sem ninguém avisar. Campo em branco/ilegível não mexe naquele tamanho."""
    conta, cfg_ou_redir = _acesso(request)
    if conta is None:
        return cfg_ou_redir
    valores = {"2x2": preco_2x2, "3x2": preco_3x2, "3x3": preco_3x3,
               "4x2": preco_4x2, "4x3": preco_4x3, "tenda": preco_tenda,
               "personalizado": preco_personalizado}
    pool = get_pool()
    mexidos = 0
    tams = 0
    with pool.connection() as c:
        for tam, bruto in valores.items():
            cent = _para_centavos(bruto)
            if cent is None:
                continue
            cur = c.execute(
                """update evento_stands set preco_centavos=%s, atualizado_em=now()
                    where conta_id=%s and tamanho=%s and status='livre'
                      and preco_centavos is distinct from %s""",
                (cent, conta[0], tam, cent))
            if cur.rowcount:
                tams += 1
                mexidos += cur.rowcount
        c.commit()
    if not mexidos:
        return RedirectResponse("/painel/eventos/estandes?ok=Nenhum valor mudou.",
                                status_code=303)
    return RedirectResponse(
        f"/painel/eventos/estandes?ok=Valores atualizados: {mexidos} stand(s) "
        f"livre(s) em {tams} tamanho(s). Reservados e vendidos mantêm o valor do negócio.",
        status_code=303)


@router.post("/painel/eventos/estandes/{codigo}/confirmar")
def confirmar(request: Request, codigo: str):
    conta, cfg_ou_redir = _acesso(request)
    if conta is None:
        return cfg_ou_redir
    membro_id = request.session.get("membro_id")
    r = es.confirmar_pagamento(get_pool(), conta[0], codigo, membro_id=membro_id)
    if not r["ok"]:
        return RedirectResponse(f"/painel/eventos/estandes?erro={r['erro']}", status_code=303)
    return RedirectResponse(f"/painel/eventos/estandes?ok=Estande {codigo} confirmado.",
                            status_code=303)


@router.post("/painel/eventos/estandes/{codigo}/liberar")
def liberar(request: Request, codigo: str):
    """Devolve pro mapa público — desistência do interessado, ou pra corrigir
    um clique errado. Ação de negócio simples o bastante pra não precisar de
    tela própria (mesmo corte de excluir/cancelar em painel_obras)."""
    conta, cfg_ou_redir = _acesso(request)
    if conta is None:
        return cfg_ou_redir
    ok = es.liberar(get_pool(), conta[0], codigo)
    msg = f"Estande {codigo} liberado." if ok else f"Estande {codigo} já estava livre."
    return RedirectResponse(f"/painel/eventos/estandes?ok={msg}", status_code=303)


@router.post("/painel/eventos/estandes/{codigo}/comprovante")
async def anexar_comprovante(request: Request, codigo: str, nome: str = Form(""),
                             whatsapp: str = Form(""), arquivo: UploadFile = File(...)):
    """O gestor ANEXA o comprovante pelo painel — a venda fechada por fora
    (WhatsApp, presencial) entra pelo MESMO cano da página pública
    (subir_e_registrar_comprovante): trava o stand, nasce prospecção, proposta
    e contrato iguais. Sub-aba "Anexar comprovante" da maquete, agora real
    (achado do dono, 29/09/2026: em produção tudo é livre e as sub-abas não
    apareciam — faltava o caminho manual).

    Só o READ do arquivo fica no event loop; o resto roda na threadpool —
    mesma regra de web/loja_stands (tests/test_event_loop_nao_trava.py)."""
    conteudo = await arquivo.read()
    return await run_in_threadpool(_anexar_comprovante_sync, request, codigo,
                                   nome, whatsapp, conteudo,
                                   arquivo.content_type or "")


def _anexar_comprovante_sync(request: Request, codigo: str, nome: str,
                             whatsapp: str, conteudo: bytes, content_type: str):
    conta, cfg_ou_redir = _acesso(request)
    if conta is None:
        return cfg_ou_redir
    pool = get_pool()
    pid = None
    if (nome or "").strip():
        from web.loja_stands import _criar_prospeccao_simples
        pid = _criar_prospeccao_simples(pool, conta[0], nome, whatsapp)
    r = es.subir_e_registrar_comprovante(pool, conta[0], codigo, conteudo,
                                         content_type, prospeccao_id=pid)
    if not r.get("ok"):
        return RedirectResponse(
            f"/painel/eventos/estandes?erro={r.get('erro') or 'Não deu pra anexar.'}",
            status_code=303)
    msg = f"Comprovante anexado — estande {codigo} reservado."
    if r.get("contrato_token"):
        msg += " Proposta e contrato criados."
    return RedirectResponse(f"/painel/eventos/estandes?ok={msg}", status_code=303)


@router.get("/painel/eventos/estandes/{codigo}/comprovante")
def ver_comprovante(request: Request, codigo: str):
    """Entrega o arquivo do bucket PRIVADO — mesmo desenho de
    painel_servicos_comprovante_ver: é esta rota (com a sessão da conta
    conferida) que permite o bucket continuar privado. O `conta_id` no WHERE
    (dentro de `es.buscar`) é o que impede ler o comprovante de outra conta
    trocando o código na URL."""
    conta, cfg_ou_redir = _acesso(request)
    if conta is None:
        return cfg_ou_redir
    s = es.buscar(get_pool(), conta[0], codigo)
    if not s or not s["comprovante_url"]:
        return JSONResponse({"erro": "sem comprovante"}, status_code=404)
    try:
        conteudo, tipo = comprov.ler(s["comprovante_url"])
    except ValueError as e:
        return JSONResponse({"erro": str(e)}, status_code=502)
    return Response(conteudo, media_type=tipo, headers={
        "Content-Disposition": f'inline; filename="comprovante-{codigo}"',
        "Cache-Control": "no-store"})


# ─────────────────────────────────────────────────────────────────────────
# Mesmo casco de toda tela do painel (`{% extends "base" %}` + o `_render` de
# web/portal.py) — não uma página solta.
#
# O CSS dos componentes (kpi, occ-*, fn-tab, oc-*, mapa) é o da maquete
# aprovada QUASE VERBATIM, escopado em .es-pag. As variáveis --mint/--gold/
# --coral são redefinidas AQUI com os valores reais de web/tema.py (os mesmos
# que a maquete v11 já usava no bloco #view-gestor) — redefinir em vez de
# apontar pra var(--card) evita ciclo de custom property (--card já é
# var(--surface) no :root do tema).
# ─────────────────────────────────────────────────────────────────────────
_CSS = r"""<style>
.es-pag{
  width:100%;max-width:var(--pag,1180px);margin:0 auto;padding:1.2rem 1rem 2.5rem;box-sizing:border-box;
  --surface:#121A16; --surface-2:#16201B; --line:#1E2A23;
  --fg:#EAF2ED; --fg-dim:#8FA197;
  --mint:#25D366; --mint-strong:#1FB859; --mint-fg:#04150C;
  --gold:#E0A32E; --gold-strong:#F0C070; --gold-fg:#2B1D00;
  --coral:#E0574F; --coral-strong:#B84440; --coral-fg:#2B0F09;
  --shadow:0 1px 2px rgba(0,0,0,0.3), 0 8px 24px rgba(0,0,0,0.35);
}
/* O atributo `hidden` perde pra display:flex/grid de regra de autor — sem
   isto, trocar de aba do funil não escondia linha nenhuma. E o base do painel
   alarga <button> pra 100%; os componentes da maquete são pílulas inline. */
.es-pag [hidden]{display:none !important}
.es-pag .fn-tab,.es-pag .oc-subtab,.es-pag .oc-expand-btn,.es-pag .oc-ghost-btn{width:auto;min-height:0;margin:0}
.es-pag .oc-expand-btn{padding:0}
.es-pag .oc-expand-btn svg{display:block;flex:0 0 auto}
.es-topo{display:flex;align-items:flex-end;justify-content:space-between;gap:.8rem;flex-wrap:wrap}
.es-topo h2{margin:0;font-size:1.5rem;line-height:1.15;font-family:"Bricolage Grotesque",sans-serif;font-weight:800}
.es-sub{color:var(--fg-dim);font-size:.88rem;margin-top:.25rem}
.es-sub a{color:var(--mint)}
.es-msg{border-radius:9px;padding:.55rem .75rem;margin:.8rem 0;font-size:.86rem}
.es-msg.ok{background:var(--neon-fundo,rgba(37,211,102,.1));border:1px solid var(--mint)}
.es-msg.erro{background:var(--coral-fundo,rgba(224,87,79,.12));border:1px solid var(--coral)}
.es-sec{margin:1.6rem 0 4px;font-size:1.02rem;font-family:"Bricolage Grotesque",sans-serif;font-weight:800}
.es-sec-sub{margin:0 0 14px;color:var(--fg-dim);font-size:.84rem}

/* ---- KPIs (maquete: .kpi-row/.kpi) ---- */
.es-pag .kpi-row{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:12px;margin:1rem 0 18px}
.es-pag .kpi{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:14px 16px;box-shadow:var(--shadow)}
.es-pag .kpi span{display:block;font-size:11.5px;color:var(--fg-dim);font-weight:600;text-transform:uppercase;letter-spacing:0.03em}
.es-pag .kpi b{display:block;font-family:var(--mono,monospace);font-size:22px;margin-top:4px}

/* ---- ocupação (maquete: .occ-block/.occ-row/.occ-bar) ---- */
.es-pag .occ-block{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:18px;box-shadow:var(--shadow)}
.es-pag .occ-block h3{margin:0 0 12px;font-size:14px}
.es-pag .occ-row{margin-bottom:12px}
.es-pag .occ-row:last-child{margin-bottom:0}
.es-pag .occ-label{display:flex;justify-content:space-between;font-size:12.5px;font-weight:700;margin-bottom:6px;gap:10px;text-transform:capitalize}
.es-pag .occ-label span:last-child{color:var(--fg-dim);font-weight:400;text-transform:none}
.es-pag .occ-bar{display:flex;height:10px;border-radius:6px;overflow:hidden;background:var(--surface-2)}
.es-pag .occ-bar span{height:100%}

/* ---- o mapa (cadastro completo — a MESMA planta da página pública, na
        variação "planta técnica": tiles chapados por status) ---- */
.es-pag .mapa-outer{overflow:auto;background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:16px;box-shadow:var(--shadow);margin-bottom:12px}
.es-pag .mapa-grid{position:relative;display:grid;gap:4px;width:max-content}
.es-pag .mapa-grid .map-block{display:flex;flex-direction:column;gap:3px}
.es-pag .mapa-grid .block-label{font-size:7.5px;font-weight:800;text-transform:uppercase;letter-spacing:0.03em;color:var(--fg-dim);background:var(--surface-2);border:1px solid var(--line);border-radius:4px;padding:2px 5px;white-space:nowrap;width:fit-content}
.es-pag .mapa-grid .cells{display:flex;flex-wrap:wrap;align-content:flex-start;gap:3px}
.es-pag .mapa-grid .stand{
  appearance:none;cursor:pointer;border:1px solid var(--line);border-radius:5px;width:auto;min-height:0;margin:0;
  font-family:var(--mono,monospace);font-size:8.6px;font-weight:700;line-height:1;
  display:flex;align-items:center;justify-content:center;text-align:center;padding:2px;flex:0 0 auto;
}
.es-pag .mapa-grid .stand.st-livre{background:color-mix(in srgb, var(--mint) 20%, var(--surface));color:var(--fg)}
.es-pag .mapa-grid .stand.st-reservado{background:color-mix(in srgb, var(--gold) 30%, var(--surface));color:var(--fg)}
.es-pag .mapa-grid .stand.st-vendido{background:color-mix(in srgb, var(--coral) 32%, var(--surface));color:var(--fg-dim)}
.es-pag .mapa-grid .stand:hover{box-shadow:0 0 0 2px var(--fg) inset}
.es-pag .mapa-grid .stand.is-selected{outline:2px solid var(--mint)}
.es-pag .mapa-grid .decor{
  display:flex;align-items:center;justify-content:center;text-align:center;border-radius:8px;
  font-size:8.5px;font-weight:700;color:var(--fg-dim);letter-spacing:0.02em;
  border:1.5px dashed var(--line);padding:4px;
}
.es-pag .mapa-grid .decor.gate{border-style:solid}
.es-pag .mapa-grid .decor.corridor{writing-mode:vertical-rl;text-orientation:mixed;font-size:7.5px;letter-spacing:0.06em;text-transform:uppercase;padding:6px 2px}
.es-pag .mapa-grid .decor.wc{border-style:dotted}
.es-pag .mapa-grid .decor.avenue{writing-mode:vertical-rl;text-orientation:mixed;border:none;font-size:8px;font-weight:600;letter-spacing:0.08em;text-transform:uppercase;opacity:0.6;justify-content:flex-start;padding-top:6px}
.es-pag .legend-mapa{display:flex;flex-wrap:wrap;gap:14px;margin:0 0 10px;font-size:12px;color:var(--fg-dim)}
.es-pag .legend-mapa i{width:10px;height:10px;border-radius:3px;display:inline-block;margin-right:5px;vertical-align:-1px}
.es-pag .legend-mapa b{color:var(--fg);font-family:var(--mono,monospace)}
.es-pag .mapa-detalhe{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:14px 16px;box-shadow:var(--shadow);margin-bottom:18px}
.es-pag .mapa-detalhe .md-topo{display:flex;align-items:center;gap:12px;flex-wrap:wrap}
.es-pag .mapa-detalhe .md-cod{font-family:var(--mono,monospace);font-weight:800;font-size:18px}
.es-pag .mapa-detalhe .md-sub{color:var(--fg-dim);font-size:12.5px;flex:1}
.es-pag .mapa-detalhe .md-badge{font-size:10.5px;font-weight:700;padding:4px 10px;border-radius:999px}
.es-pag .mapa-detalhe .md-badge.st-livre{background:var(--mint);color:var(--mint-fg)}
.es-pag .mapa-detalhe .md-badge.st-reservado{background:var(--gold);color:var(--gold-fg)}
.es-pag .mapa-detalhe .md-badge.st-vendido{background:var(--coral);color:#fff}
.es-pag .mapa-detalhe .md-acoes{display:flex;gap:8px;flex-wrap:wrap;margin-top:10px}

/* ---- valores por tamanho (o cadastro de preço) ---- */
.es-pag .preco-card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:16px;margin-bottom:18px;box-shadow:var(--shadow)}
.es-pag .preco-card h3{margin:0 0 4px;font-size:14px}
.es-pag .preco-obs{margin:0 0 12px;color:var(--fg-dim);font-size:11.5px}
.es-pag .preco-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px 14px;margin-bottom:12px}
.es-pag .preco-item span{display:block;font-size:10px;color:var(--fg-dim);text-transform:uppercase;letter-spacing:.03em;margin-bottom:3px}
.es-pag .preco-item .pin{display:flex;align-items:center;gap:6px;background:var(--surface-2);border:1px solid var(--line);border-radius:8px;padding:8px 10px}
.es-pag .preco-item .pin b{font-size:11px;color:var(--fg-dim)}
.es-pag .preco-item input{background:none;border:0;color:var(--fg);font-family:var(--mono,monospace);font-size:13px;width:100%;min-height:0;margin:0;padding:0}
.es-pag .preco-item input:disabled{color:var(--fg-dim)}
.es-pag .preco-item small{display:block;font-size:9.5px;color:var(--fg-dim);margin-top:3px}
.es-pag .preco-item small.misto{color:var(--gold-strong)}

/* ---- lista completa (o cadastro, stand a stand) ---- */
.es-pag .lst-filtros{display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-bottom:10px}
.es-pag .lst-filtros input[type=text]{background:var(--surface-2);border:1px solid var(--line);border-radius:999px;color:var(--fg);font-family:inherit;font-size:12px;padding:7px 13px;width:150px;min-height:0;margin:0}
.es-pag .tbl-wrap{overflow:auto;background:var(--surface);border:1px solid var(--line);border-radius:14px;box-shadow:var(--shadow);padding:4px 10px 8px;margin-bottom:18px;max-height:60vh}
.es-pag table.es-tbl{width:100%;border-collapse:collapse;font-size:12.5px}
.es-pag .es-tbl th{text-align:left;font-size:10px;text-transform:uppercase;letter-spacing:.03em;color:var(--fg-dim);padding:9px 10px 6px;position:sticky;top:0;background:var(--surface)}
.es-pag .es-tbl td{padding:7px 10px;border-top:1px solid var(--line);white-space:nowrap}
.es-pag .es-tbl .cod{font-family:var(--mono,monospace);font-weight:700}
.es-pag .es-tbl .num{font-family:var(--mono,monospace)}
.es-pag .es-tbl .stb{font-size:10px;font-weight:700;padding:3px 9px;border-radius:999px}
.es-pag .es-tbl .stb.livre{background:color-mix(in srgb, var(--mint) 25%, var(--surface-2));color:var(--mint)}
.es-pag .es-tbl .stb.pre_reservado{background:color-mix(in srgb, var(--gold) 25%, var(--surface-2));color:var(--gold-strong)}
.es-pag .es-tbl .stb.vendido{background:color-mix(in srgb, var(--coral) 30%, var(--surface-2));color:var(--coral-strong)}
.es-pag .es-tbl a{color:var(--mint);text-decoration:none;font-weight:600}
.es-pag .es-tbl .mini{appearance:none;cursor:pointer;font-family:inherit;font-weight:700;font-size:10.5px;padding:4px 9px;border-radius:7px;border:1px solid var(--line);background:var(--surface-2);color:var(--fg);width:auto;min-height:0;margin:0}
.es-pag .es-tbl .mini:hover{border-color:var(--mint);color:var(--mint)}
.es-pag .es-tbl .dim{color:var(--fg-dim)}

/* ---- funil de propostas (maquete: .fn-tabs/.oc-*) ---- */
.es-pag .fn-tabs{display:flex;gap:8px;flex-wrap:wrap;margin-bottom:12px}
.es-pag .fn-tab{
  appearance:none;cursor:pointer;font-family:inherit;font-weight:700;font-size:12.5px;
  display:inline-flex;align-items:center;gap:7px;padding:8px 13px;border-radius:999px;
  border:1px solid var(--line);background:var(--surface);color:var(--fg-dim);
}
.es-pag .fn-tab .pt{width:8px;height:8px;border-radius:50%;flex:0 0 auto}
.es-pag .fn-tab .n{font-family:var(--mono,monospace);color:var(--fg-dim)}
.es-pag .fn-tab.on{background:var(--surface-2);color:var(--fg);border-color:var(--fg-dim)}

.es-pag .oc-list{display:flex;flex-direction:column;gap:8px}
.es-pag .oc-hist{
  display:flex;align-items:center;gap:12px;background:var(--surface);border:1px solid var(--line);
  border-radius:12px;padding:11px 13px;box-shadow:var(--shadow);flex-wrap:wrap;
}
.es-pag .oc-open{display:flex;align-items:center;gap:12px;flex:1 1 260px;min-width:0;cursor:pointer}
.es-pag .oc-stand-badge{
  flex:0 0 auto;width:44px;height:36px;border-radius:8px;display:flex;flex-direction:column;
  align-items:center;justify-content:center;font-family:var(--mono,monospace);
}
.es-pag .oc-stand-badge .c{font-size:11px;font-weight:800;line-height:1.1}
.es-pag .oc-stand-badge .z{font-size:7px;font-weight:600;opacity:0.85}
.es-pag .oc-body{min-width:0}
.es-pag .oc-body b{font-size:13.5px}
.es-pag .oc-sub{font-size:11.5px;color:var(--fg-dim);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.es-pag .oc-criada{flex:0 0 auto;text-align:center;padding:0 6px}
.es-pag .oc-criada .rot{font-size:9px;color:var(--fg-dim);text-transform:uppercase;letter-spacing:0.03em}
.es-pag .oc-criada .dt{font-size:12px;font-weight:700;font-family:var(--mono,monospace)}
.es-pag .oc-zap{
  flex:0 0 auto;width:32px;height:32px;border-radius:8px;display:flex;align-items:center;justify-content:center;
  background:var(--surface-2);text-decoration:none;
}
.es-pag .oc-zap svg{width:16px;height:16px;color:var(--mint-strong)}
.es-pag .oc-acoes{display:flex;align-items:center;gap:8px;flex:0 0 auto;margin-left:auto}
.es-pag .oc-badge{font-size:10.5px;font-weight:700;padding:4px 9px;border-radius:999px;white-space:nowrap}
.es-pag .oc-badge.coral{background:color-mix(in srgb, var(--coral) 35%, var(--surface-2));color:var(--coral-strong)}
.es-pag .oc-badge.azul{background:color-mix(in srgb, #229ED9 25%, var(--surface-2));color:#8FD1F0}
.es-pag .oc-ok{font-size:11px;color:var(--mint-strong);font-weight:600}
.es-pag .oc-expand-btn{
  flex:0 0 auto;width:28px;height:28px;border-radius:8px;border:1px solid var(--line);background:var(--surface-2);
  color:var(--fg-dim);cursor:pointer;display:flex;align-items:center;justify-content:center;transition:transform .2s;
}
.es-pag .oc-expand-btn.on{transform:rotate(180deg);background:var(--mint);color:var(--mint-fg);border-color:var(--mint)}
.es-pag .oc-expand-btn svg{width:13px;height:13px}
.es-pag .oc-hist.expanded{flex-direction:column;align-items:stretch}
.es-pag .oc-hist-top{display:flex;align-items:center;gap:12px;width:100%;flex-wrap:wrap}

.es-pag .oc-detail{width:100%;margin-top:12px;padding-top:12px;border-top:1px solid var(--line)}
.es-pag .oc-subtabs{display:flex;gap:6px;margin-bottom:12px;flex-wrap:wrap}
.es-pag .oc-subtab{
  appearance:none;cursor:pointer;font-family:inherit;font-weight:700;font-size:11.5px;
  padding:6px 12px;border-radius:8px;border:1px solid var(--line);background:var(--surface-2);color:var(--fg-dim);
}
.es-pag .oc-subtab.on{background:var(--mint);color:var(--mint-fg);border-color:var(--mint)}
.es-pag .oc-detail-body{font-size:12.5px;line-height:1.6}

.es-pag .oc-comprovante-item{display:flex;align-items:center;gap:10px;background:var(--surface-2);border-radius:8px;padding:9px 11px;margin-bottom:10px}
.es-pag .oc-comprovante-item .ic{width:30px;height:30px;border-radius:7px;background:var(--mint);color:var(--mint-fg);display:flex;align-items:center;justify-content:center;flex:0 0 auto;font-size:13px}
.es-pag .oc-comprovante-item .txt{flex:1;min-width:0}
.es-pag .oc-comprovante-item .txt b{display:block;font-size:12.5px}
.es-pag .oc-comprovante-item .txt span{font-size:10.5px;color:var(--fg-dim)}
.es-pag .oc-contract-status{display:inline-flex;align-items:center;gap:6px;font-size:11.5px;font-weight:700;padding:4px 10px;border-radius:999px;margin-bottom:12px}
.es-pag .oc-contract-status.pendente{background:color-mix(in srgb, var(--gold) 25%, var(--surface-2));color:var(--gold-strong)}
.es-pag .oc-contract-status.assinado{background:var(--mint);color:var(--mint-fg)}
.es-pag .oc-field-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(160px,1fr));gap:10px 16px;margin-bottom:12px}
.es-pag .oc-field span{display:block;font-size:10px;color:var(--fg-dim);text-transform:uppercase;letter-spacing:0.03em;margin-bottom:2px}
.es-pag .oc-field b{font-size:13px}
.es-pag .oc-ghost-btn{
  display:inline-flex;align-items:center;gap:6px;appearance:none;cursor:pointer;font-family:inherit;font-weight:700;
  font-size:12px;padding:8px 13px;border-radius:8px;border:1px solid var(--line);background:var(--surface-2);color:var(--fg);
  text-decoration:none;
}
.es-pag .oc-ghost-btn:hover{border-color:var(--mint);color:var(--mint-strong)}
.es-pag .oc-ghost-btn.prim{background:var(--mint);border-color:var(--mint);color:var(--mint-fg)}
.es-pag .oc-ghost-btn.prim:hover{background:var(--mint-strong);color:var(--mint-fg)}
.es-pag .oc-acoes-detail{display:flex;gap:8px;flex-wrap:wrap;margin-top:4px}
.es-pag .oc-upload{margin-top:10px;border:1.5px dashed var(--line);border-radius:10px;padding:12px;background:var(--surface-2)}
.es-pag .oc-upload b{display:block;font-size:12.5px;margin-bottom:2px}
.es-pag .oc-upload .sub{display:block;font-size:10.5px;color:var(--fg-dim);margin-bottom:8px}
.es-pag .oc-upload .campos{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:8px;margin-bottom:8px}
.es-pag .oc-upload input[type=text]{background:var(--surface);border:1px solid var(--line);border-radius:8px;color:var(--fg);font-family:inherit;font-size:12px;padding:8px 10px;width:100%;min-height:0;margin:0;box-sizing:border-box}
.es-pag .oc-upload input[type=file]{color:var(--fg-dim);font-size:11.5px;width:100%;min-height:0;margin:0 0 8px;padding:0}
.es-pag .oc-vazio{color:var(--fg-dim);font-size:13px;margin:.4rem 0}
</style>"""

_TPL = r"""{% extends "base" %}{% block conteudo %}
{#- A LINHA DE STAND no modelo da maquete aprovada (oc-hist): badge na cor do
    tamanho, nome do interessado, "Criada em", WhatsApp, badge de pendência e
    o expandir com as sub-abas Comprovante/Contrato/Cliente. UM macro pras
    DUAS listas — o funil e o cadastro completo — porque o dono aprovou ESTE
    visual (29/09/2026: "deixa a lista nesse modelo do mockup"). Stand livre
    usa a mesma linha, sem expandir: as ações viram "copiar link"/"ver na
    página". -#}
{% macro linha_stand(d, grupo=None, escondido=False) %}
{% set cor = cor_tam.get(d.tamanho, ('#8FA197','#0A0F0C')) %}
{% set pav_label = d.pavilhao|replace('_',' ')|title %}
{% set cli = d.get('cliente') or {} %}
{% set ct = d.get('contrato') %}
{% set pend = d.get('pend') or [] %}
{% set rotulo = {'livre':'Livre','pre_reservado':'Reservado','vendido':'Vendido'}[d.status] %}
<div class="oc-hist"{% if grupo %} data-grupo="{{ grupo }}"{% endif %} data-st="{{ d.status }}" data-cod="{{ d.codigo|lower }}"{% if escondido %} hidden{% endif %}>
  <div class="oc-hist-top">
    <div class="oc-open" title="{% if d.status == 'livre' %}Abrir opções{% else %}Ver comprovante, contrato e cliente{% endif %}" onclick="ocToggle(this)">
      <div class="oc-stand-badge" style="background:{{ cor[0] }};color:{{ cor[1] }}"><div class="c">{{ d.codigo }}</div><div class="z">{{ tam_label.get(d.tamanho, d.tamanho) }}</div></div>
      <div class="oc-body"><b>{% if d.status == 'livre' %}Livre{% else %}{{ cli.get('empresa') or 'Interessado da página' }}{% endif %}</b>
        <div class="oc-sub">{% if d.zona and d.zona != pav_label %}{{ d.zona }} · {% endif %}{{ pav_label }}</div>
        <div class="oc-sub">{% if d.preco_centavos %}{{ brl(d.preco_centavos) }} · {% endif %}{{ d.get('resumo') or ('pronto pra oferecer' if d.status == 'livre' else rotulo) }}</div></div>
    </div>
    {% if d.get('comprovante_em') or cli.get('criado_em') %}
    <div class="oc-criada"><div class="rot">Criada em</div><div class="dt">{{ data_curta(d.get('comprovante_em') or cli.get('criado_em')) }}</div></div>
    {% endif %}
    <div class="oc-acoes">
      {% if cli.get('whatsapp') %}
      <a class="oc-zap" target="_blank" rel="noopener" title="Falar no WhatsApp" href="https://wa.me/{{ cli.whatsapp|replace('+','')|replace(' ','')|replace('-','') }}?text=Olá! Sobre o stand {{ d.codigo }}...">
        <svg viewBox="0 0 24 24" fill="currentColor"><path d="M12 2a10 10 0 0 0-8.6 15.1L2 22l5.1-1.3A10 10 0 1 0 12 2Zm5.8 14.2c-.3.7-1.4 1.3-2 1.4-.5.1-1.1.2-3.6-.8-3-1.2-4.9-4.2-5.1-4.4-.1-.2-1.2-1.6-1.2-3s.7-2.1 1-2.4c.3-.3.6-.4.8-.4h.6c.2 0 .4 0 .6.5l.9 2.1c.1.2.1.4 0 .6l-.5.7c-.1.2-.2.3-.1.6.2.3.8 1.3 1.7 2.1 1.1 1 2.1 1.3 2.4 1.5.3.1.5.1.6-.1l.8-.9c.2-.3.4-.2.6-.1l1.9 1c.2.1.4.2.4.4.1.2.1.9-.2 1.6Z"/></svg>
      </a>
      {% endif %}
      {% if d.status == 'livre' %}
      <span class="oc-ok">✓ pronto pra oferecer</span>
      {% else %}
      {% for texto, tom in pend %}<span class="oc-badge {{ tom }}">{{ texto }}</span>{% endfor %}
      {% if not pend %}<span class="oc-ok">✓ nada pendente</span>{% endif %}
      {% endif %}
      <button class="oc-expand-btn" title="Abrir opções" onclick="ocToggle(this)">
        <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.5" stroke-linecap="round" stroke-linejoin="round"><path d="M6 9l6 6 6-6"/></svg>
      </button>
    </div>
  </div>
  <div class="oc-detail" hidden>
    <div class="oc-subtabs">
      <button class="oc-subtab on" onclick="ocTab(this,'comprovante')">Anexar comprovante</button>
      <button class="oc-subtab" onclick="ocTab(this,'contrato')">Abrir contrato</button>
      <button class="oc-subtab" onclick="ocTab(this,'cliente')">Dados do cliente</button>
    </div>
    <div class="oc-detail-body" data-tab="comprovante">
      {% if d.comprovante_url %}
      <div class="oc-comprovante-item"><div class="ic">✓</div><div class="txt"><b>Comprovante do sinal</b><span>enviado{% if d.comprovante_em %} em {{ data_curta(d.comprovante_em) }}{% endif %}{% if d.preco_centavos %} · {{ brl(d.preco_centavos) }}{% endif %}</span></div></div>
      {% elif d.status != 'livre' %}
      <p class="oc-vazio">Nenhum arquivo anexado a este stand.</p>
      {% endif %}
      <div class="oc-acoes-detail">
        {% if d.status == 'livre' %}
        <button class="oc-ghost-btn prim" type="button" onclick="mapaCopiarLink(this, '{{ d.codigo }}')">Copiar link pro cliente</button>
        <a class="oc-ghost-btn" href="/e/{{ cfg.slug }}?stand={{ d.codigo }}" target="_blank" rel="noopener">Ver na página →</a>
        {% endif %}
        {% if pode_gerir and d.comprovante_url %}<a class="oc-ghost-btn" href="/painel/eventos/estandes/{{ d.codigo }}/comprovante" target="_blank">Ver comprovante →</a>{% endif %}
        {% if pode_gerir and d.status == 'pre_reservado' %}
        <form method="post" action="/painel/eventos/estandes/{{ d.codigo }}/confirmar" style="display:inline">
          <button class="oc-ghost-btn prim" type="submit">Confirmar pagamento</button>
        </form>
        <form method="post" action="/painel/eventos/estandes/{{ d.codigo }}/liberar" style="display:inline"
              onsubmit="return confirm('Liberar o estande {{ d.codigo }} de volta pra livre?')">
          <button class="oc-ghost-btn" type="submit">Liberar stand</button>
        </form>
        {% endif %}
      </div>
      {% if pode_gerir and d.status != 'vendido' and not sem_storage %}
      {#- a caixa de anexar da maquete, real: venda fechada por fora entra por
          aqui e trava o stand pelo MESMO cano da página pública -#}
      <form class="oc-upload" method="post" action="/painel/eventos/estandes/{{ d.codigo }}/comprovante" enctype="multipart/form-data">
        <b>{% if d.status == 'livre' %}Anexar comprovante{% else %}Anexar novo comprovante{% endif %}</b>
        <span class="sub">{% if d.status == 'livre' %}Venda fechada por fora (WhatsApp/presencial)? Anexa o comprovante e o stand fica reservado igual ao da página — com proposta e contrato.{% else %}Substitui o arquivo atual (comprovante melhor, ou parcela seguinte) — o prazo da reserva não muda.{% endif %}</span>
        {% if d.status == 'livre' %}
        <div class="campos">
          <input type="text" name="nome" placeholder="Nome do lojista (pra nascer o contrato)" maxlength="200">
          <input type="text" name="whatsapp" placeholder="WhatsApp (opcional)" maxlength="40">
        </div>
        {% endif %}
        <input type="file" name="arquivo" accept="image/*,application/pdf" required>
        <button class="oc-ghost-btn prim" type="submit">{% if d.status == 'livre' %}Anexar e reservar{% else %}Anexar comprovante{% endif %}</button>
      </form>
      {% elif pode_gerir and d.status != 'vendido' and sem_storage %}
      <p class="oc-vazio" style="margin-top:10px">⚠ Upload temporariamente indisponível (storage fora do ar).</p>
      {% endif %}
      {% if d.status == 'pre_reservado' and d.pre_reserva_ate %}
      <p class="oc-vazio" style="margin-top:10px">Reserva vence em {{ d.pre_reserva_ate.strftime('%d/%m às %H:%M') }} — depois disso o stand volta pro mapa sozinho.</p>
      {% endif %}
    </div>
    <div class="oc-detail-body" data-tab="contrato" hidden>
      {% if ct %}
      {% if ct.assinado_em %}
      <span class="oc-contract-status assinado">✓ Assinado{% if ct.assinado_por %} por {{ ct.assinado_por }}{% endif %} em {{ data_curta(ct.assinado_em) }}</span>
      {% else %}
      <span class="oc-contract-status pendente">⏳ Aguardando assinatura do lojista</span>
      {% endif %}
      <p style="margin:0 0 10px">Contrato nº {{ '%04d'|format(ct.numero or 0) }} — nasceu junto com o comprovante do sinal, já com os dados do lojista e do stand.</p>
      <div class="oc-acoes-detail">
        <a class="oc-ghost-btn" href="/contrato/{{ ct.token }}" target="_blank">Abrir contrato (link do lojista) →</a>
        {% if pode_gerir and d.orcamento_id %}<a class="oc-ghost-btn" href="/painel/servicos?ab={{ d.orcamento_id }}">Abrir proposta →</a>{% endif %}
      </div>
      {% elif d.orcamento_id %}
      <span class="oc-contract-status pendente">⏳ Proposta sem contrato</span>
      <p style="margin:0 0 10px">A proposta existe mas o contrato ainda não nasceu — confere o modelo de contrato em Serviços.</p>
      {% if pode_gerir %}<a class="oc-ghost-btn" href="/painel/servicos?ab={{ d.orcamento_id }}">Abrir proposta →</a>{% endif %}
      {% else %}
      <span class="oc-contract-status pendente">⏳ {% if d.status == 'livre' %}Nasce com o comprovante{% else %}Sem proposta vinculada{% endif %}</span>
      <p style="margin:0">O contrato nasce sozinho quando o comprovante chega com o nome do lojista — pela página pública ou pela aba "Anexar comprovante" aqui do lado.{% if d.status != 'livre' %} Este envio veio sem nome — cria a proposta em Serviços e vincula o stand, se quiser contrato.{% endif %}</p>
      {% endif %}
    </div>
    <div class="oc-detail-body" data-tab="cliente" hidden>
      <div class="oc-field-grid">
        <div class="oc-field"><span>Empresa / expositor</span><b>{{ cli.get('empresa') or '—' }}</b></div>
        <div class="oc-field"><span>WhatsApp</span><b>{{ cli.get('whatsapp') or '—' }}</b></div>
        <div class="oc-field"><span>Origem</span><b>Página de stands</b></div>
        <div class="oc-field"><span>Stand</span><b>{{ d.codigo }} · {{ tam_label.get(d.tamanho, d.tamanho) }}</b></div>
      </div>
      {% if d.prospeccao_id %}<a class="oc-ghost-btn" href="/painel/prospeccao">Abrir no Funil →</a>
      {% elif d.status == 'livre' %}<p class="oc-vazio" style="margin:0">Sem interessado ainda — o cadastro nasce quando o comprovante chegar.</p>
      {% else %}<p class="oc-vazio" style="margin:0">Este envio veio sem nome — o interessado não preencheu o cadastro.</p>{% endif %}
    </div>
  </div>
</div>
{% endmacro %}
""" + _CSS + r"""
<div class="es-pag">
<div class="es-topo"><div><h2>Mapa de stands</h2>
  <div class="es-sub">{{ cfg.edicao_label or cfg.slug }} · página pública:
    <a href="/e/{{ cfg.slug }}" target="_blank">/e/{{ cfg.slug }}</a></div></div></div>

{% if ok %}<div class="es-msg ok">{{ ok|e }}</div>{% endif %}
{% if erro %}<div class="es-msg erro">{{ erro|e }}</div>{% endif %}

<div class="kpi-row">
  <div class="kpi"><span>Total de stands</span><b>{{ kpis.total }}</b></div>
  <div class="kpi"><span>Livres</span><b>{{ kpis.get('livre',0) }}</b></div>
  <div class="kpi"><span>Reservados</span><b>{{ kpis.get('pre_reservado',0) }}</b></div>
  <div class="kpi"><span>Vendidos</span><b>{{ kpis.get('vendido',0) }}</b></div>
  <div class="kpi"><span>Faturamento confirmado</span><b>{{ brl(kpis.faturamento_centavos) }}</b></div>
  <div class="kpi"><span>A confirmar (reservado)</span><b>{{ brl(kpis.a_confirmar_centavos) }}</b></div>
</div>

<div class="occ-block">
  <h3>Ocupação por espaço</h3>
  {% for pav, n in por_pavilhao.items() %}
  <div class="occ-row">
    <div class="occ-label"><span>{{ pav|replace('_',' ') }}</span>
      <span>{{ n.get('vendido',0) }} vendidos · {{ n.get('pre_reservado',0) }} reservados · {{ n.get('livre',0) }} livres de {{ n.total }}</span></div>
    <div class="occ-bar">
      <span style="width:{{ (n.get('vendido',0)/n.total*100) if n.total else 0 }}%;background:var(--coral)"></span>
      <span style="width:{{ (n.get('pre_reservado',0)/n.total*100) if n.total else 0 }}%;background:var(--gold)"></span>
      <span style="width:{{ (n.get('livre',0)/n.total*100) if n.total else 0 }}%;background:var(--mint)"></span>
    </div>
  </div>
  {% endfor %}
</div>

<div class="preco-card">
  <h3>Valores por tamanho</h3>
  <p class="preco-obs">O preço que a página pública mostra. Salvar aplica em quem está <b>livre</b> — stand reservado ou vendido mantém o valor do negócio fechado.{% if not pode_gerir %} <b>Só dono/gestor altera.</b>{% endif %}</p>
  <form method="post" action="/painel/eventos/estandes/precos">
    <div class="preco-grid">
      {% for p in precos_tam %}
      <div class="preco-item">
        <span>{{ p.rotulo }}</span>
        <label class="pin"><b>R$</b><input type="text" name="preco_{{ p.tamanho }}" value="{{ p.valor }}" inputmode="decimal" {% if not pode_gerir %}disabled{% endif %}></label>
        <small{% if p.misto %} class="misto"{% endif %}>{{ p.n_livres }} livres de {{ p.n_total }}{% if p.misto %} · valores diferentes — salvar iguala os livres{% endif %}</small>
      </div>
      {% endfor %}
    </div>
    {% if pode_gerir %}<button class="oc-ghost-btn prim" type="submit">Salvar valores</button>{% endif %}
  </form>
</div>

<h3 class="es-sec">Todos os stands</h3>
<p class="es-sec-sub">O cadastro completo, na MESMA planta da página pública — toca num stand pra ver a situação{% if pode_gerir %} e agir{% endif %}. Livre? Copia o link e manda pro interessado.</p>

<div class="fn-tabs" id="mapa-pavs"></div>
<div class="legend-mapa" id="mapa-legenda"></div>
<div class="mapa-outer"><div class="mapa-grid" id="mapa-grid"></div></div>
<div class="mapa-detalhe" id="mapa-detalhe" hidden></div>

<h3 class="es-sec">Lista de stands</h3>
<p class="es-sec-sub">Os {{ stands|length }} stands em lista — com o interessado e o vínculo (proposta/contrato) de cada um.</p>
<div class="lst-filtros">
  <button class="fn-tab on" data-f="" onclick="lstStatus(this)">Todos <span class="n">{{ stands|length }}</span></button>
  <button class="fn-tab" data-f="livre" onclick="lstStatus(this)"><span class="pt" style="background:var(--mint)"></span>Livres <span class="n">{{ kpis.get('livre',0) }}</span></button>
  <button class="fn-tab" data-f="pre_reservado" onclick="lstStatus(this)"><span class="pt" style="background:var(--gold)"></span>Reservados <span class="n">{{ kpis.get('pre_reservado',0) }}</span></button>
  <button class="fn-tab" data-f="vendido" onclick="lstStatus(this)"><span class="pt" style="background:var(--coral)"></span>Vendidos <span class="n">{{ kpis.get('vendido',0) }}</span></button>
  <input type="text" id="lst-busca" placeholder="Buscar código…" oninput="lstFiltra()">
</div>
<div class="oc-list" id="lista-stands">
{% for s in stands %}{{ linha_stand(vinculos.get(s.codigo, s)) }}{% endfor %}
</div>

<h3 class="es-sec">Propostas — orçamento e contrato</h3>
<p class="es-sec-sub">Sinal, parcelas e contrato moram na proposta — o mapa só mostra pra onde ela aponta.</p>

<div class="fn-tabs">
  <button class="fn-tab on" data-grupo="precisa_de_mim" onclick="fnSel(this)"><span class="pt" style="background:var(--coral-strong)"></span>Precisa de mim <span class="n">{{ funil.precisa_de_mim|length }}</span></button>
  <button class="fn-tab" data-grupo="com_o_cliente" onclick="fnSel(this)"><span class="pt" style="background:#7FA8FF"></span>Com o cliente <span class="n">{{ funil.com_o_cliente|length }}</span></button>
  <button class="fn-tab" data-grupo="fechada" onclick="fnSel(this)"><span class="pt" style="background:var(--mint)"></span>Fechada <span class="n">{{ funil.fechada|length }}</span></button>
</div>

<div class="oc-list" id="funil-list">
{% for grupo, itens in funil.items() %}
<p class="oc-vazio" data-grupo="{{ grupo }}" {% if itens or grupo != 'precisa_de_mim' %}hidden{% endif %}>✓ Nada por aqui nessa aba.</p>
{% for d in itens %}{{ linha_stand(d, grupo, escondido=(grupo != 'precisa_de_mim')) }}{% endfor %}
{% endfor %}
</div>

</div>

<script>
var MAPA = {{ mapa_json|safe }};
var PUB_URL = '/e/{{ cfg.slug }}';
var PODE_GERIR = {{ 'true' if pode_gerir else 'false' }};
</script>
<script>
(function(){
""" + PLANTA_DEFS_JS + r"""
  var tamLabel = {'4x2':'4x2m','4x3':'4x3m','3x2':'3x2m','2x2':'2x2m','3x3':'3x3m','tenda':'Espaço em tenda','personalizado':'Stand personalizado'};
  // mesma pegada de tile proporcional da página pública, um degrau menor
  var dims = {'2x2':{w:24,h:20},'3x2':{w:29,h:20},'3x3':{w:29,h:25},'4x2':{w:34,h:20},'4x3':{w:34,h:25},'tenda':{w:34,h:20},'personalizado':{w:34,h:25}};

  var pavAtual = 'inferior';
  var selecionado = null;

  // pavilhões extras (stand do banco fora da planta desenhada) viram aba própria
  var usados = {};
  pavilions.forEach(function(p){ p.defs.forEach(function(d){
    for (var n=d.from; n<=d.to; n++){
      var num = d.prefix === 'i' ? String(n).padStart(2,'0') : String(n);
      usados[d.prefix + num] = p.key;
    }
  }); });
  var extras = {};
  Object.keys(MAPA).forEach(function(code){
    if (usados[code]) return;
    var pk = MAPA[code].pavilhao || 'outros';
    (extras[pk] = extras[pk] || []).push(code);
  });
  Object.keys(extras).forEach(function(pk){
    var pav = pavilions.filter(function(p){ return p.key === pk; })[0];
    if (!pav){
      pav = { key:pk, label:pk.replace(/_/g,' ').replace(/\b\w/g,function(c){return c.toUpperCase();}),
              sub:'', defs:[], decor:[], rows:1, extraRow:1 };
      pavilions.push(pav);
    } else { pav.extraRow = pav.rows + 1; }
    pav.extraCodes = extras[pk].sort();
  });

  var pavsEl = document.getElementById('mapa-pavs');
  pavilions.forEach(function(p){
    var b = document.createElement('button');
    b.className = 'fn-tab' + (p.key === pavAtual ? ' on' : '');
    b.id = 'mapa-tab-' + p.key;
    b.innerHTML = p.label + (p.sub ? ' <span class="n">' + p.sub + '</span>' : '');
    b.onclick = function(){ pavAtual = p.key; selecionado = null;
      pavilions.forEach(function(q){ var el = document.getElementById('mapa-tab-' + q.key); if (el) el.classList.toggle('on', q.key === pavAtual); });
      renderMapa(); renderLegenda(); renderDetalhe(); };
    pavsEl.appendChild(b);
  });

  function standTile(code){
    var s = MAPA[code];
    if (!s) return null;
    var btn = document.createElement('button');
    btn.className = 'stand st-' + s.status + (code === selecionado ? ' is-selected' : '');
    var d = dims[s.tamanho] || {w:29,h:20};
    btn.style.width = d.w + 'px';
    btn.style.height = d.h + 'px';
    btn.textContent = code;
    btn.title = code + ' · ' + (tamLabel[s.tamanho] || s.tamanho) + ' · ' + s.status;
    btn.onclick = function(){ selecionado = code; renderMapa(); renderDetalhe(); };
    return btn;
  }

  function renderMapa(){
    var grid = document.getElementById('mapa-grid');
    var pav = pavilions.filter(function(p){ return p.key === pavAtual; })[0];
    grid.style.gridTemplateColumns = 'repeat(24, 30px)';
    grid.style.gridTemplateRows = 'repeat(' + pav.rows + ', 26px)';
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
      for (var n=d.from; n<=d.to; n++){
        var num = d.prefix === 'i' ? String(n).padStart(2,'0') : String(n);
        var t = standTile(d.prefix + num);
        if (t) cells.appendChild(t);
      }
      block.appendChild(cells);
      grid.appendChild(block);
    });
    if (pav.extraCodes && pav.extraCodes.length){
      var bl = document.createElement('div');
      bl.className = 'map-block';
      bl.style.gridColumn = '1 / span 23';
      bl.style.gridRow = String(pav.extraRow || 1);
      var cs = document.createElement('div'); cs.className = 'cells';
      pav.extraCodes.forEach(function(code){ var t = standTile(code); if (t) cs.appendChild(t); });
      bl.appendChild(cs);
      grid.appendChild(bl);
    }
  }

  function renderLegenda(){
    var tot = {livre:0, reservado:0, vendido:0};
    Object.keys(MAPA).forEach(function(code){
      if (MAPA[code].pavilhao !== pavAtual) return;
      tot[MAPA[code].status] = (tot[MAPA[code].status] || 0) + 1;
    });
    document.getElementById('mapa-legenda').innerHTML =
      '<span><i style="background:var(--mint)"></i>Livre <b>' + (tot.livre||0) + '</b></span>' +
      '<span><i style="background:var(--gold)"></i>Reservado <b>' + (tot.reservado||0) + '</b></span>' +
      '<span><i style="background:var(--coral)"></i>Vendido <b>' + (tot.vendido||0) + '</b></span>';
  }

  function esc(t){
    return String(t).replace(/[&<>"']/g, function(c){
      return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];
    });
  }

  function renderDetalhe(){
    var box = document.getElementById('mapa-detalhe');
    if (!selecionado){ box.hidden = true; box.innerHTML = ''; return; }
    var s = MAPA[selecionado];
    var statusTxt = {livre:'Livre', reservado:'Reservado', vendido:'Vendido'}[s.status];
    var html = '<div class="md-topo">';
    html += '<span class="md-cod">' + esc(selecionado) + '</span>';
    html += '<span class="md-badge st-' + s.status + '">' + statusTxt + '</span>';
    html += '<span class="md-sub">' + (s.zona ? esc(s.zona) + ' · ' : '') + esc((s.pavilhao||'').replace(/_/g,' ')) +
            ' · ' + esc(tamLabel[s.tamanho] || s.tamanho) + (s.preco ? ' · ' + esc(s.preco) : '') +
            (s.cliente ? ' · <b style="color:var(--fg)">' + esc(s.cliente) + '</b>' : '') + '</span>';
    html += '</div><div class="md-acoes">';
    if (s.status === 'livre'){
      html += '<button class="oc-ghost-btn prim" onclick="mapaCopiarLink(this, \'' + esc(selecionado) + '\')">Copiar link pro interessado</button>';
      html += '<a class="oc-ghost-btn" target="_blank" rel="noopener" href="' + PUB_URL + '?stand=' + encodeURIComponent(selecionado) + '">Ver na página pública →</a>';
    } else {
      html += '<button class="oc-ghost-btn" onclick="mapaAbrirFunil(\'' + esc(selecionado) + '\')">Abrir no funil ↓</button>';
    }
    html += '</div>';
    box.innerHTML = html;
    box.hidden = false;
  }

  window.mapaCopiarLink = function(btn, code){
    var url = location.origin + PUB_URL + '?stand=' + encodeURIComponent(code);
    var done = function(){ var t = btn.textContent; btn.textContent = 'Copiado ✓'; setTimeout(function(){ btn.textContent = t; }, 1600); };
    if (navigator.clipboard && navigator.clipboard.writeText){ navigator.clipboard.writeText(url).then(done); }
    else { window.prompt('Copia o link:', url); }
  };

  window.mapaAbrirFunil = function(code){
    // escopado no funil: o mesmo stand também vive na lista completa
    var row = document.querySelector('#funil-list .oc-hist[data-cod="' + String(code).toLowerCase() + '"]');
    if (!row) return;
    var grupo = row.dataset.grupo;
    var tab = document.querySelector('.fn-tab[data-grupo="' + grupo + '"]');
    if (tab) fnSel(tab);
    var detail = row.querySelector('.oc-detail');
    if (detail && detail.hidden){
      var b = row.querySelector('.oc-expand-btn');
      if (b) ocToggle(b);
    }
    row.scrollIntoView({behavior:'smooth', block:'center'});
  };

  renderMapa(); renderLegenda(); renderDetalhe();
})();

function fnSel(btn){
  var grupo = btn.dataset.grupo;
  document.querySelectorAll('.fn-tab[data-grupo]').forEach(function(b){ b.classList.toggle('on', b === btn); });
  document.querySelectorAll('.oc-hist[data-grupo], .oc-vazio[data-grupo]').forEach(function(el){
    el.hidden = el.dataset.grupo !== grupo;
  });
}
function ocToggle(el){
  // recebe o ELEMENTO clicado (não o código): o mesmo stand aparece no funil
  // E na lista completa, e buscar por data-cod abriria sempre a primeira cópia.
  var row = el.closest('.oc-hist');
  if (!row) return;
  var detail = row.querySelector('.oc-detail');
  if (!detail) return;
  var btn = row.querySelector('.oc-expand-btn');
  var aberto = !detail.hidden;
  detail.hidden = aberto;
  row.classList.toggle('expanded', !aberto);
  if (btn) btn.classList.toggle('on', !aberto);
}
function ocTab(btn, tab){
  var detail = btn.closest('.oc-detail');
  detail.querySelectorAll('.oc-subtab').forEach(function(b){ b.classList.toggle('on', b === btn); });
  detail.querySelectorAll('.oc-detail-body').forEach(function(el){ el.hidden = el.dataset.tab !== tab; });
}
var lstF = '';
function lstStatus(btn){
  lstF = btn.dataset.f;
  document.querySelectorAll('.lst-filtros .fn-tab').forEach(function(b){ b.classList.toggle('on', b === btn); });
  lstFiltra();
}
function lstFiltra(){
  var q = (document.getElementById('lst-busca').value || '').trim().toLowerCase();
  document.querySelectorAll('#lista-stands .oc-hist').forEach(function(el){
    var ok = (!lstF || el.dataset.st === lstF) && (!q || el.dataset.cod.indexOf(q) !== -1);
    el.hidden = !ok;
  });
}
</script>
{% endblock %}"""

_env.loader.mapping["estandes"] = _TPL
