"""A aba Obras: /painel/obras — cada casa e cada reforma da construtora.

PR 2 de 4 do desenho aprovado pelo dono em 25/09/2026
(docs/mockups/nicho_construcao.html, seções 05 e 06). A primeira conta é a PX2
(conta 33), que constrói casa popular pelo Minha Casa Minha Vida e faz reforma.

SÓ APARECE PRA CONSTRUÇÃO (regra 6): o perfil do nicho tem que ser `obras`, como
Renovações só abre pra `seguros`. Quem barra o nicho é a rota; a whitelist de
papel (contas.equipe.rotas_do_papel) não conhece nicho.

QUEM VÊ: dono, gestor e financeiro — obra aqui é custo, e custo é financeiro. O
vendedor não tem a aba.

A OBRA NASCE AQUI e em nenhum outro lugar (decisão 1 do dono): o agente do
WhatsApp manda o link desta tela quando pedem obra nova, porque criar obra cria
centro de custo, e o agente não cria centro (regra do dono de 23/09).

Tudo que escreve é ação explícita de quem está logado na conta dele: criar,
editar, marcar etapa e pôr um gasto na obra. Nada apaga lançamento.
"""
from __future__ import annotations

import logging
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from jinja2 import Environment

from db.conexao import get_pool
from finance import obra_reforma as orf
from finance import obra_venda as ov
from finance import obras as ob
from finance import raio_x_perfil as rxp
from web.portal import _env, _render, conta_logada, nicho_da_conta

router = APIRouter()
_log = logging.getLogger("openclaw.painel_obras")

_PAPEIS_OK = ("dono", "gestor", "financeiro")


def _acesso(request: Request):
    """Devolve (conta, redirect)."""
    conta = conta_logada(request)
    if conta is None:
        return None, RedirectResponse("/login", status_code=303)
    if request.session.get("papel", "dono") not in _PAPEIS_OK:
        return None, RedirectResponse("/painel", status_code=303)
    # Regra 6: a tela segue o nicho. `obras` e mais nenhum.
    if rxp.perfil_por_nicho(nicho_da_conta(conta)) != "obras":
        return None, RedirectResponse("/painel", status_code=303)
    return conta, None


def _brl(centavos) -> str:
    return ob._brl(centavos)


def _cent(txt: str):
    """'82.000,00' / '82000' / '' → centavos. Vazio é None: previsto e valor
    podem não existir ainda, e 0 diria que existem e são zero."""
    t = (txt or "").strip().replace("R$", "").replace(" ", "")
    if not t:
        return None
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        v = int(round(float(t) * 100))
    except ValueError:
        return None
    return v if v >= 0 else None


def _num(txt: str):
    t = (txt or "").strip().replace(",", ".")
    try:
        v = float(t) if t else None
    except ValueError:
        return None
    return v if v and v > 0 else None


def _data(txt: str):
    t = (txt or "").strip()
    try:
        return datetime.strptime(t, "%Y-%m-%d").date() if t else None
    except ValueError:
        return None


def _volta(url: str, erro: str = "") -> RedirectResponse:
    if erro:
        from urllib.parse import quote
        url += ("&" if "?" in url else "?") + "erro=" + quote(erro)
    return RedirectResponse(url, status_code=303)


# ─────────────────────────────────────────────────────────────── a lista
@router.get("/painel/obras", response_class=HTMLResponse)
def painel_obras(request: Request):
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    obras = ob.listar_obras(pool, conta[0], incluir_arquivadas=True)
    abertas = [o for o in obras if o["status"] not in ("arquivada",)]
    andamento = [o for o in abertas if o["status"] in ("em_obra", "pronta")]
    # o chip de cada casa: o que trava (casa pronta) ou o primeiro alerta de prazo
    avisos = {}
    for o in abertas:
        if o["tipo"] != "casa":
            continue
        sit = ov.situacao_da_casa(pool, conta[0], o)
        if o["pct"] == 100 and sit["trava"]:
            avisos[o["id"]] = "trava: " + sit["trava"]["nome"].lower()
        elif sit["alertas"]:
            avisos[o["id"]] = sit["alertas"][0]
    return _render(
        "obras", request, titulo="Obras", secao_ativa="obras", brl=_brl,
        obras=abertas, arquivadas=[o for o in obras if o["status"] == "arquivada"],
        n_andamento=len(andamento),
        # o botão Dividir reparte entre as EM OBRA (a mesma regra de `lancamento_na_obra`)
        n_em_obra=sum(1 for o in abertas if o["status"] == "em_obra"),
        gasto_andamento=sum(o["custos"]["total"] for o in andamento),
        parado=ov.parado_em_casas(pool, conta[0], abertas), avisos=avisos,
        sem=ob.sem_obra(pool, conta[0]),
        escolhas=[o for o in abertas if o["status"] != "entregue"],
        tipos=ob.ROTULO_TIPO, hoje=date.today(),
        erro=(request.query_params.get("erro") or "").strip())


@router.post("/painel/obras/nova")
def criar(request: Request, nome: str = Form(""), tipo: str = Form("casa"),
          endereco: str = Form(""), area_m2: str = Form(""),
          custo_previsto: str = Form(""), valor: str = Form(""),
          inicio_em: str = Form(""), previsao_em: str = Form("")):
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    try:
        o = ob.criar_obra(get_pool(), conta[0], nome, tipo, endereco=endereco,
                          area_m2=_num(area_m2), custo_previsto_centavos=_cent(custo_previsto),
                          valor_centavos=_cent(valor), inicio_em=_data(inicio_em),
                          previsao_em=_data(previsao_em),
                          criado_por=request.session.get("membro_id"))
    except ValueError as e:
        return _volta("/painel/obras", str(e))
    return RedirectResponse(f"/painel/obras/{o['id']}", status_code=303)


@router.post("/painel/obras/lancamento")
def lancamento_na_obra(request: Request, lancamento_id: int = Form(...),
                       obra_id: str = Form(""), acao: str = Form("por")):
    """Da lista "Sem obra": o gasto inteiro numa obra, ou dividido entre as obras
    em andamento. `voltar` é a própria lista, que é de onde o botão vem."""
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    try:
        if acao == "dividir":
            alvo = [o["id"] for o in ob.listar_obras(pool, conta[0], com_custos=False)
                    if o["status"] == "em_obra"]
            ob.dividir(pool, conta[0], lancamento_id, alvo)
        else:
            if not obra_id.strip().isdigit():
                raise ValueError("Escolha a obra.")
            ob.por_na_obra(pool, conta[0], lancamento_id, int(obra_id))
    except ValueError as e:
        return _volta("/painel/obras#sem-obra", str(e))
    return RedirectResponse("/painel/obras#sem-obra", status_code=303)


# ─────────────────────────────────────────────────────────────── a ficha
@router.get("/painel/obras/{obra_id}", response_class=HTMLResponse)
def ficha(request: Request, obra_id: int):
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    o = ob.obter_obra(get_pool(), conta[0], obra_id)
    if not o:
        return RedirectResponse("/painel/obras", status_code=303)
    sit = ov.situacao_da_casa(get_pool(), conta[0], o) if o["tipo"] == "casa" else None
    orc = _orcamento_da_reforma(get_pool(), conta[0], o) if o["tipo"] == "reforma" else None
    return _render("obra", request, titulo=o["nome"], secao_ativa="obras", brl=_brl,
                   o=o, tipos=ob.ROTULO_TIPO, status=ob.ROTULO_STATUS,
                   rotulo_custo=ob.ROTULO_CUSTO, sit=sit, orc=orc,
                   tipos_item=orf.TIPOS_ITEM, unidades=orf.UNIDADES, modelos=orf.MODELOS,
                   status_doc=ov.STATUS_DOC, modalidades=ov.MODALIDADES,
                   situacoes=[(k, ov.ROTULO_SITUACAO[k]) for k in ov.SITUACOES],
                   erro=(request.query_params.get("erro") or "").strip())


@router.post("/painel/obras/{obra_id}/documento")
def documento(request: Request, obra_id: int, tipo: str = Form(...),
              status: str = Form("pendente"), numero: str = Form(""),
              emitido_em: str = Form(""), vence_em: str = Form("")):
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    try:
        ov.marcar_documento(get_pool(), conta[0], obra_id, tipo, status=status,
                            numero=numero, emitido_em=_data(emitido_em),
                            vence_em=_data(vence_em), substituir=True)
    except ValueError as e:
        return _volta(f"/painel/obras/{obra_id}", str(e))
    return RedirectResponse(f"/painel/obras/{obra_id}#papeis", status_code=303)


@router.post("/painel/obras/{obra_id}/venda")
def salvar_venda(request: Request, obra_id: int, comprador: str = Form(""),
                 telefone: str = Form(""), faixa: str = Form(""),
                 modalidade: str = Form("financiada"), valor_venda: str = Form(""),
                 valor_avaliacao: str = Form(""), avaliacao_em: str = Form(""),
                 aprovado_em: str = Form(""), financiamento: str = Form(""),
                 subsidio: str = Form(""), fgts: str = Form(""), entrada: str = Form(""),
                 obs: str = Form("")):
    """O cadastro da venda. As datas de assinatura, registro e crédito NÃO vêm
    daqui: elas andam pelo botão do passo, que é quem cria as contas a receber —
    uma data digitada aqui seria uma venda assinada sem título."""
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    try:
        ov.salvar_venda(get_pool(), conta[0], obra_id, comprador=comprador,
                        telefone=telefone,
                        faixa=int(faixa) if faixa.strip().isdigit() else None,
                        modalidade=modalidade, valor_venda_centavos=_cent(valor_venda),
                        valor_avaliacao_centavos=_cent(valor_avaliacao),
                        avaliacao_em=_data(avaliacao_em), aprovado_em=_data(aprovado_em),
                        financiamento_centavos=_cent(financiamento),
                        subsidio_centavos=_cent(subsidio), fgts_centavos=_cent(fgts),
                        entrada_centavos=_cent(entrada), obs=obs)
    except ValueError as e:
        return _volta(f"/painel/obras/{obra_id}", str(e))
    return RedirectResponse(f"/painel/obras/{obra_id}#venda", status_code=303)


@router.post("/painel/obras/{obra_id}/venda/passo")
def passo_da_venda(request: Request, obra_id: int, situacao: str = Form(...),
                   data: str = Form("")):
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    try:
        ov.andar_venda(get_pool(), conta[0], obra_id, situacao, _data(data))
    except ValueError as e:
        return _volta(f"/painel/obras/{obra_id}", str(e))
    return RedirectResponse(f"/painel/obras/{obra_id}#venda", status_code=303)


@router.post("/painel/obras/{obra_id}/editar")
def editar(request: Request, obra_id: int, nome: str = Form(""), tipo: str = Form("casa"),
           endereco: str = Form(""), area_m2: str = Form(""),
           custo_previsto: str = Form(""), valor: str = Form(""),
           status: str = Form("em_obra"), inicio_em: str = Form(""),
           previsao_em: str = Form(""), obs: str = Form("")):
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    try:
        ob.editar_obra(get_pool(), conta[0], obra_id, nome=nome, tipo=tipo,
                       endereco=endereco.strip(), area_m2=_num(area_m2),
                       custo_previsto_centavos=_cent(custo_previsto),
                       valor_centavos=_cent(valor), status=status,
                       inicio_em=_data(inicio_em), previsao_em=_data(previsao_em),
                       obs=obs.strip())
    except ValueError as e:
        return _volta(f"/painel/obras/{obra_id}", str(e))
    return RedirectResponse(f"/painel/obras/{obra_id}", status_code=303)


# ─────────────────────────────────────────────────────────────── o orçamento da reforma
_TZ = ZoneInfo("America/Sao_Paulo")


def _app_url() -> str:
    """O endereço público, pro link do cliente: a mesma fonte dos e-mails."""
    return os.environ.get("APP_URL", "https://app.zaq-ia.com").rstrip("/")


def _orcamento_da_reforma(pool, conta_id: int, o: dict) -> dict:
    """As versões (orçamento e aditivos), as parcelas que já podem ser cobradas e o
    que o editor mostra: a versão em aberto, ou uma nova se não houver nenhuma."""
    versoes = orf.orcamentos(pool, conta_id, o["id"])
    liberadas = {(x["versao"], x["titulo_id"]) for x in orf.parcelas_liberadas(pool, conta_id, o)}
    for v in versoes:
        pagaveis = [p for p in v["parcelas"] if int(p.get("valor_centavos") or 0) > 0]
        for p in v["parcelas"]:
            p["liberada"] = False
        for p, tid in zip(pagaveis, v["titulos"]):
            p["liberada"] = (v["versao"], tid) in liberadas
        if v["aceito_em"]:
            v["aceito_em"] = v["aceito_em"].astimezone(_TZ)
    aberta = versoes[-1] if versoes and versoes[-1]["status"] != "aceito" else None
    editor = None
    if aberta or not versoes:
        base = aberta or {"id": None, "versao": 1, "status": "rascunho", "itens": [],
                          "material_incluso": True, "prazo_dias": None,
                          "modelo_pagamento": "etapas", "parcelas": [], "escopo": "",
                          "garantia": orf.GARANTIA_PADRAO}
        vazia = {"servico": "", "tipo": "mao_de_obra", "unidade": "m2", "quantidade": 0,
                 "valor_unit_centavos": 0}
        linhas = list(base["itens"]) + [dict(vazia) for _ in range(max(3, 6 - len(base["itens"])))]
        parcelas = list(base["parcelas"]) or orf.parcelas_do_modelo(base["modelo_pagamento"],
                                                                   o["etapas"])
        editor = dict(base, linhas=linhas,
                      parcelas_linhas=parcelas + [{"rotulo": "", "pct": 0, "etapa": None}])
    return {"versoes": versoes, "editor": editor, "base": _app_url(),
            "pode_aditivo": bool(versoes) and versoes[-1]["status"] == "aceito",
            "nome_etapa": {e["chave"]: e["nome"] for e in o["etapas"]}}


def _pct(txt: str) -> float:
    try:
        return float((txt or "0").replace(",", ".").replace("%", "").strip() or 0)
    except ValueError:
        return 0.0


@router.post("/painel/obras/{obra_id}/orcamento")
def salvar_orcamento(request: Request, obra_id: int, servico: list[str] = Form([]),
                     tipo: list[str] = Form([]), unidade: list[str] = Form([]),
                     quantidade: list[str] = Form([]), valor_unit: list[str] = Form([]),
                     material_incluso: str = Form("1"), prazo_dias: str = Form(""),
                     modelo_pagamento: str = Form("etapas"), usar_modelo: str = Form(""),
                     p_rotulo: list[str] = Form([]), p_pct: list[str] = Form([]),
                     p_etapa: list[str] = Form([]), escopo: str = Form(""),
                     garantia: str = Form("")):
    """`def` e não `async`, com as listas vindo do Form (test_event_loop_nao_trava)."""
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    itens = [{"servico": sv, "tipo": tp, "unidade": un, "quantidade": qt,
              "valor_unit_centavos": _cent(vl) or 0}
             for sv, tp, un, qt, vl in zip(servico, tipo, unidade, quantidade, valor_unit)]
    parcelas = None if usar_modelo else [
        {"rotulo": r, "pct": _pct(pc), "etapa": et or None}
        for r, pc, et in zip(p_rotulo, p_pct, p_etapa) if r.strip() and _pct(pc) > 0]
    prazo = int(prazo_dias) if prazo_dias.strip().isdigit() and int(prazo_dias) > 0 else None
    try:
        orf.salvar_rascunho(get_pool(), conta[0], obra_id, itens=itens,
                            material_incluso=material_incluso == "1", prazo_dias=prazo,
                            garantia=garantia, escopo=escopo,
                            modelo_pagamento=modelo_pagamento, parcelas=parcelas or None)
    except ValueError as e:
        return _volta(f"/painel/obras/{obra_id}", str(e))
    return RedirectResponse(f"/painel/obras/{obra_id}#orcamento", status_code=303)


@router.post("/painel/obras/{obra_id}/orcamento/{orcamento_id}/enviar")
def enviar_orcamento(request: Request, obra_id: int, orcamento_id: int):
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    try:
        orf.enviar(get_pool(), conta[0], orcamento_id)
    except ValueError as e:
        return _volta(f"/painel/obras/{obra_id}", str(e))
    return RedirectResponse(f"/painel/obras/{obra_id}#orcamento", status_code=303)


@router.post("/painel/obras/{obra_id}/aditivo")
def abrir_aditivo(request: Request, obra_id: int):
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    try:
        orf.abrir_aditivo(get_pool(), conta[0], obra_id)
    except ValueError as e:
        return _volta(f"/painel/obras/{obra_id}", str(e))
    return RedirectResponse(f"/painel/obras/{obra_id}#orcamento", status_code=303)


@router.post("/painel/obras/{obra_id}/etapa")
def etapa(request: Request, obra_id: int, etapa_id: int = Form(...),
          concluida: str = Form("1")):
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    try:
        ob.marcar_etapa(get_pool(), conta[0], obra_id, etapa_id, concluida=concluida == "1")
    except ValueError as e:
        return _volta(f"/painel/obras/{obra_id}", str(e))
    return RedirectResponse(f"/painel/obras/{obra_id}#etapas", status_code=303)


@router.post("/painel/obras/{obra_id}/etapas")
def etapas(request: Request, obra_id: int, chave: list[str] = Form([]),
           nome: list[str] = Form([]), peso: list[str] = Form([]),
           tirar: list[str] = Form([])):
    """A lista inteira de etapas, editada: nome e peso de cada uma, a linha vazia do
    fim pra acrescentar, e a marca de tirar. Chave vazia é etapa nova.

    `def` e não `async def`, com as listas vindo do Form: o salvamento é banco
    síncrono, e num handler async ele congelaria o worker inteiro
    (tests/test_event_loop_nao_trava.py)."""
    conta, redir = _acesso(request)
    if redir is not None:
        return redir
    fora = set(tirar)
    linhas = [(ch or None, nm, _num(ps) or 0)
              for ch, nm, ps in zip(chave, nome, peso) if not (ch and ch in fora)]
    try:
        ob.salvar_etapas(get_pool(), conta[0], obra_id, linhas)
    except ValueError as e:
        return _volta(f"/painel/obras/{obra_id}", str(e))
    return RedirectResponse(f"/painel/obras/{obra_id}#etapas", status_code=303)


# ─────────────────────────────────────────────────────────────── as telas
_CSS = r"""<style>
/* mesma medida das outras telas de trabalho (Renovações, Raio-X): `--pag` vem de
   web/tema.py */
.ob-pag{width:100%;max-width:var(--pag,1180px);margin:0 auto;padding:1.2rem 1rem 2.5rem;box-sizing:border-box}
.ob-topo{display:flex;align-items:flex-end;justify-content:space-between;gap:.8rem;flex-wrap:wrap}
.ob-topo h2{margin:0;font-size:1.5rem;line-height:1.15}
.ob-sub{color:var(--txt-mut);font-size:.88rem;margin-top:.25rem}
.ob-volta{font-size:.85rem;color:var(--txt-mut);text-decoration:none}
.ob-faixas{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.5rem;margin:1rem 0}
.ob-cx{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.55rem .75rem;text-decoration:none;color:inherit}
.ob-cx .r{font-size:.66rem;text-transform:uppercase;letter-spacing:.06em;color:var(--txt-mut);display:block}
.ob-cx .v{font-size:1.3rem;font-weight:700;line-height:1.2;display:block}
.ob-cx .n{font-size:.72rem;color:var(--txt-mut)}
.ob-cx.alerta{background:var(--ambar-fundo);border-color:var(--ambar-borda)}
.ob-lista{display:flex;flex-direction:column;gap:.45rem}
.ob-card{display:grid;grid-template-columns:1.3fr 1.3fr 1fr 1fr;gap:.8rem;align-items:center;background:var(--card);
  border:1px solid var(--borda);border-radius:11px;padding:.65rem .85rem;text-decoration:none;color:inherit}
.ob-card:hover{border-color:var(--verde)}
.ob-card .nm{font-weight:700}
.ob-mut{color:var(--txt-mut);font-size:.78rem}
.ob-bar{height:7px;background:var(--borda);border-radius:4px;overflow:hidden;margin:.25rem 0 .1rem}
.ob-bar i{display:block;height:100%;background:var(--verde)}
.ob-bar i.alto{background:#D9932B}
.ob-pill{display:inline-block;border-radius:14px;padding:2px 9px;font-size:.72rem;border:1px solid var(--borda);color:var(--txt-mut);white-space:nowrap}
.ob-pill.pronta,.ob-pill.vendida{border-color:var(--verde);color:var(--verde-claro)}
.ob-sec{margin:1.6rem 0 .6rem;font-size:1.05rem}
.ob-box{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.8rem .9rem;margin-bottom:.6rem}
.ob-box summary{cursor:pointer;font-weight:600}
.ob-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:.6rem;margin-top:.6rem}
.ob-box label{display:block;font-size:.7rem;color:var(--txt-mut);text-transform:uppercase;letter-spacing:.05em;margin-bottom:.2rem}
.ob-box input,.ob-box select,.ob-box textarea{width:100%;box-sizing:border-box}
.ob-erro{background:var(--neon-fundo);border:1px solid var(--neon-borda);border-radius:9px;padding:.55rem .75rem;margin:.8rem 0}
.ob-tab{width:100%;border-collapse:collapse;font-size:.85rem}
.ob-tab td,.ob-tab th{padding:.45rem .5rem;border-bottom:1px solid var(--borda);text-align:left;vertical-align:middle}
.ob-tab th{font-size:.66rem;text-transform:uppercase;letter-spacing:.06em;color:var(--txt-mut)}
.ob-tab td.v{text-align:right;font-variant-numeric:tabular-nums;white-space:nowrap}
.ob-rolo{overflow-x:auto}
.ob-acoes{display:flex;gap:.35rem;flex-wrap:wrap;align-items:center}
.ob-acoes select{width:auto;min-width:9rem}
.ob-bt{padding:.35rem .7rem;border-radius:7px;border:1px solid var(--borda);background:transparent;color:inherit;cursor:pointer;font-size:.8rem}
.ob-bt.prim{background:var(--verde);border-color:var(--verde);color:#fff}
.ob-etapas{display:flex;flex-direction:column;gap:.3rem}
.ob-et{display:flex;justify-content:space-between;align-items:center;gap:.6rem;padding:.35rem .5rem;border:1px solid var(--borda);border-radius:8px}
.ob-et.feita{border-color:var(--verde)}
.ob-et form{margin:0}
.ob-custo{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.5rem}
/* o caminho do dinheiro: um passo por caixinha, o que trava em âmbar */
.ob-passos{display:flex;flex-wrap:wrap;gap:.35rem;margin:.4rem 0 .6rem}
.ob-passo{flex:1 1 92px;border:1px solid var(--borda);border-radius:9px;padding:.4rem .45rem;font-size:.74rem;text-align:center;color:var(--txt-mut);line-height:1.35}
.ob-passo small{display:block;font-size:.66rem;opacity:.85}
.ob-passo.ok{border-color:var(--verde);color:var(--verde-claro)}
.ob-passo.trava{border-color:var(--ambar-borda);background:var(--ambar-fundo);color:#F0DCA6;font-weight:700}
.ob-alertas{background:var(--ambar-fundo);border:1px solid var(--ambar-borda);border-radius:9px;padding:.5rem .7rem;margin:.4rem 0 .8rem;font-size:.84rem}
.ob-doc{display:flex;flex-wrap:wrap;gap:.4rem;align-items:center;padding:.4rem 0;border-bottom:1px solid var(--borda)}
.ob-doc .nm{flex:1 1 180px;font-size:.86rem}
.ob-doc select,.ob-doc input{width:auto}
.ob-doc input[type=date]{max-width:9.5rem}
.ob-doc input[name=numero]{max-width:8rem}
.ob-chip{display:inline-block;margin-top:.25rem;font-size:.7rem;color:#F0DCA6;background:var(--ambar-fundo);border:1px solid var(--ambar-borda);border-radius:12px;padding:1px 8px}
@media (max-width:760px){.ob-card{grid-template-columns:1fr 1fr}}
</style>"""

_TPL_LISTA = r"""{% extends "base" %}{% block conteudo %}""" + _CSS + r"""
<div class="ob-pag">
<div class="ob-topo"><div><h2>Obras</h2>
  <div class="ob-sub">Cada casa e cada reforma: o que já custou, contra o previsto, e em que etapa está.</div></div></div>
{% if erro %}<div class="ob-erro">{{ erro|e }}</div>{% endif %}

<div class="ob-faixas">
  <div class="ob-cx"><span class="r">Em andamento</span><span class="v">{{ n_andamento }}</span>
    <span class="n">em obra ou prontas</span></div>
  <div class="ob-cx"><span class="r">Gasto nessas obras</span><span class="v">{{ brl(gasto_andamento) }}</span>
    <span class="n">dinheiro que ainda está na obra</span></div>
  {% if parado %}<div class="ob-cx"><span class="r">Parado em casa não paga</span><span class="v">{{ brl(parado) }}</span>
    <span class="n">gasto em casa que a Caixa ainda não pagou</span></div>{% endif %}
  {% if sem.n %}<a class="ob-cx alerta" href="#sem-obra"><span class="r">Sem obra</span>
    <span class="v">{{ sem.n }}</span><span class="n">{{ brl(sem.total_centavos) }} de material e mão de obra sem casa</span></a>{% endif %}
</div>

{% if obras %}<div class="ob-lista">
{% for o in obras %}<a class="ob-card" href="/painel/obras/{{ o.id }}">
  <div><span class="nm">{{ '🏠' if o.tipo == 'casa' else '🔨' }} {{ o.nome|e }}</span>
    <div class="ob-mut">{{ o.endereco|e or o.rotulo_tipo }}{% if o.area_m2 %} · {{ '%g'|format(o.area_m2) }} m²{% endif %}</div></div>
  <div><span class="ob-mut">Etapas · {{ o.pct }}%</span>
    <div class="ob-bar"><i style="width:{{ o.pct }}%"></i></div>
    <span class="ob-mut">{{ ('próxima: ' ~ o.proxima_etapa|lower|e) if o.proxima_etapa else 'todas feitas' }}</span></div>
  <div><b>{{ brl(o.custos.total) }}</b>
    <div class="ob-mut">{% if o.custo_previsto_centavos %}de {{ brl(o.custo_previsto_centavos) }} previstos ({{ o.pct_previsto }}%){% else %}sem previsto{% endif %}</div></div>
  <div><span class="ob-pill {{ o.status }}">{{ o.rotulo_status }}</span>
    {% if avisos[o.id] %}<div><span class="ob-chip">{{ avisos[o.id]|e }}</span></div>{% endif %}</div>
</a>{% endfor %}
</div>{% endif %}

<details class="ob-box" {% if not obras %}open{% endif %}><summary>+ Nova obra</summary>
<form method="post" action="/painel/obras/nova">
  <div class="ob-grid">
    <div><label>Nome</label><input name="nome" placeholder="Casa 4" required></div>
    <div><label>Tipo</label><select name="tipo">{% for k, r in tipos.items() %}<option value="{{ k }}">{{ r }}</option>{% endfor %}</select></div>
    <div><label>Lote / endereço</label><input name="endereco" placeholder="Qd 4 · Lt 14"></div>
    <div><label>Área (m²)</label><input name="area_m2" inputmode="decimal" placeholder="45"></div>
    <div><label>Custo previsto (R$)</label><input name="custo_previsto" inputmode="decimal" placeholder="82.000,00"></div>
    <div><label>Venda prevista ou contrato (R$)</label><input name="valor" inputmode="decimal"></div>
    <div><label>Início</label><input type="date" name="inicio_em"></div>
    <div><label>Previsão de término</label><input type="date" name="previsao_em"></div>
  </div>
  <p class="ob-mut" style="margin:.6rem 0">A obra ganha um centro de custo com o mesmo nome — é por ele que o assistente
  do WhatsApp lança cada nota na casa certa. As etapas vêm da sua última obra do mesmo tipo.</p>
  <button class="ob-bt prim">Criar obra</button>
</form></details>

<h3 class="ob-sec" id="sem-obra">Sem obra{% if sem.n %} · {{ sem.n }}{% endif %}</h3>
{% if sem.n %}
<p class="ob-mut">Material e mão de obra da empresa que ainda não dizem de qual casa são. Ponha cada um na sua obra —
ou divida entre as obras em andamento, quando for de todas.</p>
<div class="ob-rolo"><table class="ob-tab">
<tr><th>Data</th><th>Descrição</th><th style="text-align:right">Valor</th><th>Obra</th></tr>
{% for i in sem.itens %}<tr>
  <td>{{ i.data.strftime('%d/%m/%Y') }}</td><td>{{ i.descricao|e }}</td><td class="v">{{ brl(i.valor_centavos) }}</td>
  <td>{% if escolhas %}<div class="ob-acoes">
    <form method="post" action="/painel/obras/lancamento" class="ob-acoes">
      <input type="hidden" name="lancamento_id" value="{{ i.id }}">
      <select name="obra_id">{% for o in escolhas %}<option value="{{ o.id }}">{{ o.nome|e }}</option>{% endfor %}</select>
      <button class="ob-bt">Pôr</button></form>
    {% if n_em_obra > 1 %}<form method="post" action="/painel/obras/lancamento">
      <input type="hidden" name="lancamento_id" value="{{ i.id }}"><input type="hidden" name="acao" value="dividir">
      <button class="ob-bt" title="partes iguais entre as obras em andamento">Dividir</button></form>{% endif %}
  </div>{% else %}<span class="ob-mut">crie uma obra primeiro</span>{% endif %}</td>
</tr>{% endfor %}
</table></div>
{% if sem.n > sem.itens|length %}<p class="ob-mut">Mostrando os {{ sem.itens|length }} mais recentes.</p>{% endif %}
{% else %}<p class="ob-mut">Nenhum gasto de obra sem obra. ✅</p>{% endif %}

{% if arquivadas %}<details class="ob-box" style="margin-top:1.4rem"><summary>Arquivadas · {{ arquivadas|length }}</summary>
<div class="ob-lista" style="margin-top:.6rem">{% for o in arquivadas %}<a class="ob-card" href="/painel/obras/{{ o.id }}">
  <div><span class="nm">{{ o.nome|e }}</span><div class="ob-mut">{{ o.rotulo_tipo }}</div></div>
  <div class="ob-mut">Etapas · {{ o.pct }}%</div><div><b>{{ brl(o.custos.total) }}</b></div>
  <div><span class="ob-pill">Arquivada</span></div></a>{% endfor %}</div></details>{% endif %}
</div>
{% endblock %}"""

_TPL_FICHA = r"""{% extends "base" %}{% block conteudo %}""" + _CSS + r"""
<div class="ob-pag">
<a class="ob-volta" href="/painel/obras">← Obras</a>
<div class="ob-topo" style="margin-top:.4rem"><div>
  <h2>{{ '🏠' if o.tipo == 'casa' else '🔨' }} {{ o.nome|e }}</h2>
  <div class="ob-sub">{{ o.rotulo_tipo }}{% if o.endereco %} · {{ o.endereco|e }}{% endif %}{% if o.area_m2 %} · {{ '%g'|format(o.area_m2) }} m²{% endif %}
    · <span class="ob-pill {{ o.status }}">{{ o.rotulo_status }}</span></div></div></div>
{% if erro %}<div class="ob-erro">{{ erro|e }}</div>{% endif %}

<div class="ob-faixas">
  <div class="ob-cx"><span class="r">Gasto</span><span class="v">{{ brl(o.custos.total) }}</span>
    <span class="n">{% if o.custo_previsto_centavos %}{{ o.pct_previsto }}% de {{ brl(o.custo_previsto_centavos) }} previstos{% else %}sem previsto cadastrado{% endif %}</span></div>
  <div class="ob-cx"><span class="r">Etapas</span><span class="v">{{ o.pct }}%</span>
    <span class="n">{{ ('próxima: ' ~ o.proxima_etapa|lower|e) if o.proxima_etapa else 'todas feitas' }}</span></div>
  {% if o.custo_m2 %}<div class="ob-cx"><span class="r">Custo por m²</span><span class="v">{{ brl(o.custo_m2) }}</span>
    <span class="n">do que já foi gasto</span></div>{% endif %}
  <div class="ob-cx"><span class="r">{{ 'Venda prevista' if o.tipo == 'casa' else 'Contrato' }}</span>
    <span class="v">{{ brl(o.valor_centavos) if o.valor_centavos else '—' }}</span>
    <span class="n">recebido nesta obra: {{ brl(o.custos.recebido) }}</span></div>
</div>

<div class="ob-box"><b>O custo</b>
  <div class="ob-custo" style="margin-top:.5rem">
    {% for k in ('material', 'mao_de_obra', 'outros') %}<div class="ob-cx"><span class="r">{{ rotulo_custo[k] }}</span>
      <span class="v" style="font-size:1.1rem">{{ brl(o.custos[k]) }}</span></div>{% endfor %}
  </div>
  <p class="ob-mut" style="margin:.5rem 0 0">Material é o que foi lançado em Insumos (conta 3.1.03); mão de obra, o de
  Serviços (conta 3.1.04). O resto cai em outros.</p>
</div>

{% if sit %}
<h3 class="ob-sec" id="caminho">O caminho do dinheiro</h3>
<div class="ob-passos">{% for p in sit.caminho %}<div class="ob-passo{{ ' ok' if p.feito }}{{ ' trava' if p.trava }}">{{ '✓ ' if p.feito }}{{ p.nome }}{% if p.detalhe %}<small>{{ p.detalhe }}</small>{% endif %}</div>{% endfor %}</div>
<p class="ob-mut">{% if sit.trava %}O que trava agora: <b>{{ sit.trava.nome }}</b>. Na casa pronta, o dinheiro da Caixa só cai depois do
registro — e o registro depende de habite-se, CND da obra e averbação.{% else %}Dinheiro na conta. ✅{% endif %}</p>
{% if sit.alertas %}<div class="ob-alertas">{% for a in sit.alertas %}<div>⚠️ {{ a|e }}</div>{% endfor %}</div>{% endif %}

<h3 class="ob-sec" id="papeis">Os papéis da casa</h3>
<div class="ob-box">{% for d in sit.documentos %}
  <form method="post" action="/painel/obras/{{ o.id }}/documento" class="ob-doc">
    <input type="hidden" name="tipo" value="{{ d.tipo }}">
    <span class="nm">{{ '✓' if d.status == 'ok' else '○' }} {{ d.nome }}</span>
    <select name="status">{% for k, r in status_doc.items() %}<option value="{{ k }}"{{ ' selected' if k == d.status }}>{{ r }}</option>{% endfor %}</select>
    <input name="numero" value="{{ d.numero|e }}" placeholder="número">
    <label class="ob-mut">emitido <input type="date" name="emitido_em" value="{{ d.emitido_em or '' }}"></label>
    <label class="ob-mut">vence <input type="date" name="vence_em" value="{{ d.vence_em or '' }}"></label>
    <button class="ob-bt">Salvar</button>
  </form>{% endfor %}
  <p class="ob-mut" style="margin:.5rem 0 0">CNO: até 30 dias do início da obra. Certidões da empresa valem 180 dias.
  O Zaq lembra o papel; o imposto (INSS da obra, RET) é com o contador.</p>
</div>

<h3 class="ob-sec" id="venda">A venda</h3>
{% set v = sit.venda %}
{% if v %}<div class="ob-faixas">
  <div class="ob-cx"><span class="r">Situação</span><span class="v" style="font-size:1rem">{{ v.rotulo_situacao }}</span>
    <span class="n">{{ v.comprador|e or 'comprador não informado' }}{% if v.faixa %} · Faixa {{ v.faixa }}{% endif %}</span></div>
  <div class="ob-cx"><span class="r">Entrada (do comprador)</span><span class="v">{{ brl(v.entrada_centavos) if v.entrada_centavos else '—' }}</span></div>
  <div class="ob-cx"><span class="r">Repasse (da Caixa)</span><span class="v">{{ brl(v.repasse_centavos) if v.repasse_centavos else '—' }}</span>
    <span class="n">financiamento + subsídio + FGTS</span></div>
  {% if v.titulo_entrada_id or v.titulo_repasse_id %}<div class="ob-cx"><span class="r">Contas a receber</span>
    <span class="v" style="font-size:1rem">criadas</span><span class="n">na assinatura, no centro desta obra</span></div>{% endif %}
</div>
<form method="post" action="/painel/obras/{{ o.id }}/venda/passo" class="ob-box ob-acoes">
  <b style="margin-right:.4rem">Andar a venda</b>
  <select name="situacao">{% for k, r in situacoes %}<option value="{{ k }}"{{ ' selected' if k == v.situacao }}>{{ r }}</option>{% endfor %}</select>
  <input type="date" name="data" title="vazio = hoje">
  <button class="ob-bt prim">Salvar passo</button>
  <span class="ob-mut">Na assinatura nascem as contas a receber da entrada e do repasse.</span>
</form>{% endif %}
<details class="ob-box"{% if not v %} open{% endif %}><summary>{{ 'Dados da venda' if v else '+ Cadastrar a venda' }}</summary>
<form method="post" action="/painel/obras/{{ o.id }}/venda">
  <div class="ob-grid">
    <div><label>Comprador</label><input name="comprador" value="{{ (v.comprador if v else '')|e }}"></div>
    <div><label>Telefone</label><input name="telefone" value="{{ (v.telefone if v else '')|e }}"></div>
    <div><label>Faixa do MCMV</label><select name="faixa"><option value="">—</option>{% for f in (1, 2, 3, 4) %}<option value="{{ f }}"{{ ' selected' if v and v.faixa == f }}>Faixa {{ f }}</option>{% endfor %}</select></div>
    <div><label>Modalidade</label><select name="modalidade">{% for k, r in modalidades.items() %}<option value="{{ k }}"{{ ' selected' if v and v.modalidade == k }}>{{ r }}</option>{% endfor %}</select></div>
    <div><label>Preço de venda (R$)</label><input name="valor_venda" inputmode="decimal" value="{{ '%.2f'|format(v.valor_venda_centavos / 100) if v and v.valor_venda_centavos is not none else '' }}"></div>
    <div><label>Financiamento (R$)</label><input name="financiamento" inputmode="decimal" value="{{ '%.2f'|format(v.financiamento_centavos / 100) if v and v.financiamento_centavos is not none else '' }}"></div>
    <div><label>Subsídio (R$)</label><input name="subsidio" inputmode="decimal" value="{{ '%.2f'|format(v.subsidio_centavos / 100) if v and v.subsidio_centavos is not none else '' }}"></div>
    <div><label>FGTS (R$)</label><input name="fgts" inputmode="decimal" value="{{ '%.2f'|format(v.fgts_centavos / 100) if v and v.fgts_centavos is not none else '' }}"></div>
    <div><label>Entrada (R$) · vazio = calcula</label><input name="entrada" inputmode="decimal" value="{{ '%.2f'|format(v.entrada_centavos / 100) if v and v.entrada_centavos is not none else '' }}"></div>
    <div><label>Avaliação da Caixa (R$)</label><input name="valor_avaliacao" inputmode="decimal" value="{{ '%.2f'|format(v.valor_avaliacao_centavos / 100) if v and v.valor_avaliacao_centavos is not none else '' }}"></div>
    <div><label>Data da avaliação</label><input type="date" name="avaliacao_em" value="{{ (v.avaliacao_em if v else '') or '' }}"></div>
    <div><label>Crédito aprovado em</label><input type="date" name="aprovado_em" value="{{ (v.aprovado_em if v else '') or '' }}"></div>
  </div>
  <div style="margin-top:.6rem"><label>Observação</label><textarea name="obs" rows="2">{{ (v.obs if v else '')|e }}</textarea></div>
  <p class="ob-mut" style="margin:.6rem 0">Entrada = preço − financiamento − subsídio − FGTS. Se a avaliação da Caixa vier abaixo do
  preço, a diferença sai do bolso do comprador — o alerta aparece aqui em cima. O Zaq não simula financiamento: os valores
  vêm da simulação da Caixa ou do correspondente.</p>
  <button class="ob-bt prim">Salvar a venda</button>
</form></details>
{% endif %}

{% if orc is not none %}
<h3 class="ob-sec" id="orcamento">O orçamento</h3>
{% for v in orc.versoes %}<div class="ob-box">
  <div class="ob-acoes" style="justify-content:space-between">
    <b>{{ 'Orçamento' if v.versao == 1 else 'Aditivo ' ~ (v.versao - 1) }} · {{ brl(v.total_centavos) }}</b>
    <span class="ob-pill{{ ' pronta' if v.status == 'aceito' }}">{{ v.rotulo_status }}</span>
  </div>
  <div class="ob-mut" style="margin:.3rem 0">mão de obra {{ brl(v.totais.mao_de_obra) }} · material {{ brl(v.totais.material) }}
    · equipamento {{ brl(v.totais.equipamento) }}{% if v.prazo_dias %} · prazo {{ v.prazo_dias }} dias{% endif %} · {{ v.rotulo_modelo }}</div>
  {% if v.token and v.status in ('enviado', 'aceito', 'recusado') %}
  <div class="ob-acoes" style="margin:.3rem 0">
    <input readonly value="{{ orc.base }}/orcamento-obra/{{ v.token }}" onclick="this.select()" style="flex:1 1 260px">
    <a class="ob-bt" target="_blank" rel="noopener" href="https://wa.me/?text={{ ('Segue o orçamento da reforma: ' ~ orc.base ~ '/orcamento-obra/' ~ v.token)|urlencode }}">Mandar no WhatsApp</a>
  </div>{% endif %}
  {% if v.status == 'aceito' %}<div class="ob-mut">Aceito por <b>{{ v.aceito_nome|e }}</b> em {{ v.aceito_em.strftime('%d/%m/%Y %H:%M') }}.
    As parcelas viraram contas a receber no centro desta obra.</div>{% endif %}
  {% if v.status == 'enviado' %}<div class="ob-mut">Enviado · vale até {{ v.validade_ate.strftime('%d/%m/%Y') if v.validade_ate }}.
    Mudar aqui muda o que o cliente vê no mesmo link.</div>{% endif %}
  <table class="ob-tab" style="margin-top:.4rem"><tr><th>Parcela</th><th>Quando</th><th style="text-align:right">Valor</th></tr>
  {% for p in v.parcelas %}<tr><td>{{ p.rotulo|e }}{% if p.liberada %} <span class="ob-chip">pode cobrar</span>{% endif %}</td>
    <td class="ob-mut">{{ ('ao concluir ' ~ (orc.nome_etapa[p.etapa] or p.etapa)|lower) if p.etapa else 'na assinatura' }}</td>
    <td class="v">{{ brl(p.valor_centavos) }}</td></tr>{% endfor %}
  </table>
  {% if v.status in ('rascunho', 'enviado', 'recusado') %}
  <form method="post" action="/painel/obras/{{ o.id }}/orcamento/{{ v.id }}/enviar" style="margin-top:.5rem">
    <button class="ob-bt prim">{{ 'Gerar o link pro cliente' if v.status != 'enviado' else 'Renovar a validade do link' }}</button></form>{% endif %}
</div>{% endfor %}

{% if orc.pode_aditivo %}<form method="post" action="/painel/obras/{{ o.id }}/aditivo" class="ob-box ob-acoes">
  <span>Serviço extra no meio da obra? Ele entra como <b>aditivo</b>, com link e aceite próprios.</span>
  <button class="ob-bt">Abrir aditivo</button></form>{% endif %}

{% if orc.editor %}{% set ed = orc.editor %}
<details class="ob-box"{% if not orc.versoes or ed.status == 'rascunho' %} open{% endif %}>
<summary>{{ ('Editar o ' ~ ('orçamento' if ed.versao == 1 else 'aditivo ' ~ (ed.versao - 1))) if ed.id else 'Montar o orçamento' }}</summary>
<form method="post" action="/painel/obras/{{ o.id }}/orcamento">
  <p class="ob-mut" style="margin:.5rem 0">Mão de obra, material e equipamento separados — é o que o Código de Defesa do
  Consumidor (art. 40) pede no orçamento. Linha sem serviço é ignorada.</p>
  <div class="ob-rolo"><table class="ob-tab">
  <tr><th>Serviço</th><th>Tipo</th><th>Unid.</th><th>Qtd</th><th>Valor unit. (R$)</th></tr>
  {% for it in ed.linhas %}<tr>
    <td><input name="servico" value="{{ it.servico|e }}" placeholder="ex.: assentamento de piso"></td>
    <td><select name="tipo">{% for k, r in tipos_item.items() %}<option value="{{ k }}"{{ ' selected' if k == it.tipo }}>{{ r }}</option>{% endfor %}</select></td>
    <td><select name="unidade">{% for k, r in unidades.items() %}<option value="{{ k }}"{{ ' selected' if k == it.unidade }}>{{ r }}</option>{% endfor %}</select></td>
    <td><input name="quantidade" value="{{ '%g'|format(it.quantidade) if it.quantidade else '' }}" inputmode="decimal" style="max-width:5rem"></td>
    <td><input name="valor_unit" value="{{ '%.2f'|format(it.valor_unit_centavos / 100) if it.valor_unit_centavos else '' }}" inputmode="decimal" style="max-width:8rem"></td>
  </tr>{% endfor %}
  </table></div>
  <div class="ob-grid">
    <div><label>Material incluso?</label><select name="material_incluso"><option value="1"{{ ' selected' if ed.material_incluso }}>Sim, está no preço</option><option value="0"{{ ' selected' if not ed.material_incluso }}>Não, o cliente compra</option></select></div>
    <div><label>Prazo (dias)</label><input name="prazo_dias" value="{{ ed.prazo_dias or '' }}" inputmode="numeric" placeholder="{{ 'Reforma Casa Brasil: 55' }}"></div>
    <div><label>Forma de pagamento</label><select name="modelo_pagamento">{% for k, r in modelos.items() %}<option value="{{ k }}"{{ ' selected' if k == ed.modelo_pagamento }}>{{ r }}</option>{% endfor %}</select></div>
  </div>
  <p style="margin:.7rem 0 .3rem"><b>Parcelas</b> <label class="ob-mut" style="display:inline"><input type="checkbox" name="usar_modelo" value="1" style="width:auto"{{ ' checked' if not ed.id }}> usar as parcelas da forma de pagamento escolhida</label></p>
  <table class="ob-tab"><tr><th>Parcela</th><th>%</th><th>Quando</th></tr>
  {% for p in ed.parcelas_linhas %}<tr>
    <td><input name="p_rotulo" value="{{ p.rotulo|e }}"></td>
    <td><input name="p_pct" value="{{ '%g'|format(p.pct) if p.pct else '' }}" inputmode="decimal" style="max-width:5rem"></td>
    <td><select name="p_etapa"><option value="">na assinatura</option>{% for e in o.etapas %}<option value="{{ e.chave|e }}"{{ ' selected' if e.chave == p.etapa }}>ao concluir {{ e.nome|e|lower }}</option>{% endfor %}</select></td>
  </tr>{% endfor %}
  </table>
  <div style="margin-top:.6rem"><label>Escopo (o que está e o que não está no serviço)</label><textarea name="escopo" rows="2">{{ ed.escopo|e }}</textarea></div>
  <div style="margin-top:.6rem"><label>Garantia</label><textarea name="garantia" rows="2">{{ ed.garantia|e }}</textarea></div>
  <p class="ob-mut" style="margin:.6rem 0">As cláusulas que vão junto (material, prazo, serviço extra, ART/RRT, garantia) são um modelo:
  leia antes de mandar. O cliente vê tudo no link e aceita por lá, com nome e data registrados.</p>
  <button class="ob-bt prim">Salvar o orçamento</button>
</form></details>{% endif %}
{% endif %}

<h3 class="ob-sec" id="etapas">Etapas · {{ o.pct }}%</h3>
<div class="ob-bar" style="height:9px;margin-bottom:.6rem"><i style="width:{{ o.pct }}%"></i></div>
<div class="ob-etapas">{% for e in o.etapas %}
  <div class="ob-et{{ ' feita' if e.concluida_em }}">
    <span>{{ '✓' if e.concluida_em else '○' }} {{ e.nome|e }} <span class="ob-mut">· {{ '%g'|format(e.peso) }}%{% if e.concluida_em %} · {{ e.concluida_em.strftime('%d/%m') }}{% endif %}</span></span>
    <form method="post" action="/painel/obras/{{ o.id }}/etapa">
      <input type="hidden" name="etapa_id" value="{{ e.id }}"><input type="hidden" name="concluida" value="{{ '0' if e.concluida_em else '1' }}">
      <button class="ob-bt">{{ 'Desmarcar' if e.concluida_em else 'Concluída' }}</button></form>
  </div>{% endfor %}</div>

<details class="ob-box" style="margin-top:.6rem"><summary>Editar etapas e pesos</summary>
<form method="post" action="/painel/obras/{{ o.id }}/etapas">
  <p class="ob-mut">O peso é quanto a etapa vale no andamento da obra. A próxima obra do mesmo tipo começa com esta lista.</p>
  <table class="ob-tab"><tr><th>Etapa</th><th>Peso (%)</th><th>Tirar</th></tr>
  {% for e in o.etapas %}<tr><td><input type="hidden" name="chave" value="{{ e.chave|e }}"><input name="nome" value="{{ e.nome|e }}"></td>
    <td><input name="peso" value="{{ '%g'|format(e.peso) }}" inputmode="decimal" style="max-width:6rem"></td>
    <td><input type="checkbox" name="tirar" value="{{ e.chave|e }}" style="width:auto"></td></tr>{% endfor %}
  <tr><td><input type="hidden" name="chave" value=""><input name="nome" placeholder="etapa nova"></td>
    <td><input name="peso" inputmode="decimal" style="max-width:6rem"></td><td></td></tr>
  </table>
  <button class="ob-bt prim" style="margin-top:.5rem">Salvar etapas</button>
</form></details>

<h3 class="ob-sec">Os lançamentos desta obra</h3>
{% if o.lancamentos %}<div class="ob-rolo"><table class="ob-tab">
<tr><th>Data</th><th>Descrição</th><th>Tipo</th><th style="text-align:right">Valor</th></tr>
{% for l in o.lancamentos %}<tr><td>{{ l.data.strftime('%d/%m/%Y') }}</td><td>{{ l.descricao|e }}</td>
  <td>{{ 'Recebido' if l.tipo_custo == 'receita' else rotulo_custo[l.tipo_custo] }}</td>
  <td class="v">{{ brl(l.valor_centavos) }}{% if l.dividido %}<div class="ob-mut">parte de {{ brl(l.valor_inteiro_centavos) }}</div>{% endif %}</td></tr>{% endfor %}
</table></div>
{% else %}<p class="ob-mut">Nada lançado nesta obra ainda. No WhatsApp, é só mandar a foto da nota e dizer que é da {{ o.nome|e }}.</p>{% endif %}

<details class="ob-box" style="margin-top:1.2rem"><summary>Dados da obra</summary>
<form method="post" action="/painel/obras/{{ o.id }}/editar">
  <div class="ob-grid">
    <div><label>Nome</label><input name="nome" value="{{ o.nome|e }}" required></div>
    <div><label>Tipo</label><select name="tipo">{% for k, r in tipos.items() %}<option value="{{ k }}"{{ ' selected' if k == o.tipo }}>{{ r }}</option>{% endfor %}</select></div>
    <div><label>Situação</label><select name="status">{% for k, r in status.items() %}<option value="{{ k }}"{{ ' selected' if k == o.status }}>{{ r }}</option>{% endfor %}</select></div>
    <div><label>Lote / endereço</label><input name="endereco" value="{{ o.endereco|e }}"></div>
    <div><label>Área (m²)</label><input name="area_m2" value="{{ '%g'|format(o.area_m2) if o.area_m2 else '' }}" inputmode="decimal"></div>
    <div><label>Custo previsto (R$)</label><input name="custo_previsto" value="{{ '%.2f'|format(o.custo_previsto_centavos / 100) if o.custo_previsto_centavos is not none else '' }}" inputmode="decimal"></div>
    <div><label>Venda prevista ou contrato (R$)</label><input name="valor" value="{{ '%.2f'|format(o.valor_centavos / 100) if o.valor_centavos is not none else '' }}" inputmode="decimal"></div>
    <div><label>Início</label><input type="date" name="inicio_em" value="{{ o.inicio_em or '' }}"></div>
    <div><label>Previsão de término</label><input type="date" name="previsao_em" value="{{ o.previsao_em or '' }}"></div>
  </div>
  <div style="margin-top:.6rem"><label>Observação</label><textarea name="obs" rows="2">{{ o.obs|e }}</textarea></div>
  <p class="ob-mut" style="margin:.6rem 0">Mudar o nome muda o centro de custo junto. Arquivar tira a obra das listas sem
  apagar nada do que foi lançado nela.</p>
  <button class="ob-bt prim">Salvar</button>
</form></details>
</div>
{% endblock %}"""

_env.loader.mapping["obras"] = _TPL_LISTA
_env.loader.mapping["obra"] = _TPL_FICHA


# ─────────────────────────────────────────────────────────────── o link do cliente
#
# PÚBLICO, sem login: o cliente da reforma abre pelo link que a empresa mandou e
# aceita por ali, como na proposta de hoje (web/proposta.py). Fora de /painel, o
# gate de papel não se aplica; quem escopa é o TOKEN. Environment próprio com
# autoescape: tudo o que aparece aqui foi digitado por alguém.
_env_pub = Environment(autoescape=True)


def _ip(request: Request) -> str:
    xf = request.headers.get("x-forwarded-for", "")
    if xf:
        return xf.split(",")[0].strip()[:60]
    return (request.client.host if request.client else "")[:60]


@router.get("/orcamento-obra/{token}", response_class=HTMLResponse)
def orcamento_publico(request: Request, token: str):
    orc = orf.por_token(get_pool(), token)
    if not orc:
        return HTMLResponse(_env_pub.from_string(_TPL_PUB_404).render(), status_code=404)
    q = request.query_params
    return HTMLResponse(_env_pub.from_string(_TPL_PUB).render(
        o=orc, brl=_brl, tipos_item=orf.TIPOS_ITEM, unidades=orf.UNIDADES,
        erro=(q.get("erro") or "").strip(), acabou=(q.get("ok") or "").strip()))


@router.post("/orcamento-obra/{token}/aceitar")
def aceitar_orcamento(request: Request, token: str, nome: str = Form(""),
                      doc: str = Form(""), concordo: str = Form("")):
    if not concordo:
        return RedirectResponse(f"/orcamento-obra/{token}?erro=Marque%20que%20leu%20e%20aceita.",
                                status_code=303)
    if not nome.strip():
        return RedirectResponse(f"/orcamento-obra/{token}?erro=Escreva%20seu%20nome%20completo.",
                                status_code=303)
    ok = orf.aceitar(get_pool(), token, nome, doc, _ip(request))
    return RedirectResponse(f"/orcamento-obra/{token}?ok={'aceito' if ok else 'nao'}",
                            status_code=303)


@router.post("/orcamento-obra/{token}/recusar")
def recusar_orcamento(request: Request, token: str):
    orf.recusar(get_pool(), token)
    return RedirectResponse(f"/orcamento-obra/{token}?ok=recusado", status_code=303)


_TPL_PUB_404 = """<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1"><title>Orçamento</title></head>
<body style="font-family:system-ui,sans-serif;max-width:640px;margin:3rem auto;padding:0 1rem;color:#1d2433">
<h2>Orçamento não encontrado</h2><p>O link pode ter sido digitado errado. Peça um novo a quem mandou.</p></body></html>"""

_TPL_PUB = """<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Orçamento · {{ o.obra_nome }}</title>
<style>
body{margin:0;background:#f4f2ee;font:15px/1.55 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;color:#1d2433}
.pg{max-width:760px;margin:0 auto;padding:1.4rem 1rem 3rem}
h1{font-size:1.35rem;margin:.2rem 0}.mut{color:#667085;font-size:.88rem}
.cx{background:#fff;border:1px solid #e4e0d8;border-radius:12px;padding:1rem 1.1rem;margin:.9rem 0}
table{width:100%;border-collapse:collapse;font-size:.9rem}th,td{padding:.45rem .4rem;border-bottom:1px solid #eee;text-align:left;vertical-align:top}
th{font-size:.7rem;text-transform:uppercase;letter-spacing:.06em;color:#667085}td.v{text-align:right;white-space:nowrap}
.tot{display:grid;grid-template-columns:repeat(auto-fit,minmax(140px,1fr));gap:.5rem}
.tot div{background:#faf8f4;border-radius:9px;padding:.5rem .7rem}.tot b{display:block;font-size:1.1rem}
.ok{background:#e8f5ee;border:1px solid #bfe3d0;color:#155c3b}.err{background:#fdecea;border:1px solid #f5c6c0;color:#8c332c}
label{display:block;font-size:.8rem;color:#667085;margin:.6rem 0 .2rem}input[type=text]{width:100%;box-sizing:border-box;padding:.55rem;border:1px solid #d0d5dd;border-radius:8px;font-size:1rem}
button{padding:.65rem 1.1rem;border-radius:9px;border:0;font-size:1rem;cursor:pointer}
.sim{background:#1c7a4f;color:#fff}.nao{background:transparent;color:#667085;text-decoration:underline}
</style></head><body><div class="pg">
<div class="mut">{{ o.empresa }}</div>
<h1>{{ 'Orçamento' if o.versao == 1 else 'Aditivo ' ~ (o.versao - 1) }} · {{ o.obra_nome }}</h1>
{% if o.obra_endereco %}<div class="mut">{{ o.obra_endereco }}</div>{% endif %}

{% if acabou == 'aceito' or (o.status == 'aceito' and not acabou) %}<div class="cx ok">Aceito por <b>{{ o.aceito_nome }}</b>. Obrigado!</div>
{% elif acabou == 'recusado' or o.status == 'recusado' %}<div class="cx">Você recusou este orçamento. Se mudar de ideia, fale com a empresa.</div>
{% elif acabou == 'nao' %}<div class="cx err">Não deu pra registrar o aceite: o orçamento já foi respondido ou a validade venceu.</div>{% endif %}
{% if erro %}<div class="cx err">{{ erro }}</div>{% endif %}

<div class="cx"><table>
<tr><th>Serviço</th><th>Tipo</th><th style="text-align:right">Qtd</th><th style="text-align:right">Unit.</th><th style="text-align:right">Total</th></tr>
{% for it in o.itens %}<tr><td>{{ it.servico }}</td><td class="mut">{{ tipos_item[it.tipo] }}</td>
<td class="v">{{ '%g'|format(it.quantidade) }} {{ unidades[it.unidade] }}</td><td class="v">{{ brl(it.valor_unit_centavos) }}</td>
<td class="v">{{ brl(it.subtotal_centavos) }}</td></tr>{% endfor %}
</table>
<div class="tot" style="margin-top:.8rem">
  <div><span class="mut">Mão de obra</span><b>{{ brl(o.totais.mao_de_obra) }}</b></div>
  <div><span class="mut">Material</span><b>{{ brl(o.totais.material) }}</b></div>
  <div><span class="mut">Equipamento</span><b>{{ brl(o.totais.equipamento) }}</b></div>
  <div><span class="mut">Total</span><b>{{ brl(o.total_centavos) }}</b></div>
</div></div>

<div class="cx"><b>Como paga</b><table style="margin-top:.4rem">
{% for p in o.parcelas %}<tr><td>{{ p.rotulo }}</td><td class="mut">{{ ('ao concluir ' ~ (p.etapa_nome or p.etapa)|lower) if p.etapa else 'na assinatura' }}</td><td class="v">{{ brl(p.valor_centavos) }}</td></tr>{% endfor %}
</table></div>

<div class="cx">{% for t, txt in o.clausulas %}<p><b>{{ t }}.</b> {{ txt }}</p>{% endfor %}
{% if o.validade_ate %}<p class="mut">Este orçamento vale até {{ o.validade_ate.strftime('%d/%m/%Y') }}.</p>{% endif %}</div>

{% if o.status == 'enviado' and not o.vencido and acabou not in ('aceito', 'recusado') %}
<form class="cx" method="post" action="/orcamento-obra/{{ o.token }}/aceitar">
  <b>Aceitar o orçamento</b>
  <label>Seu nome completo</label><input type="text" name="nome" required>
  <label>CPF (opcional)</label><input type="text" name="doc" inputmode="numeric">
  <label style="display:flex;gap:.5rem;align-items:center;font-size:.9rem;color:#1d2433"><input type="checkbox" name="concordo" value="1"> Li o orçamento e as condições, e aceito.</label>
  <div style="margin-top:.8rem;display:flex;gap:.6rem;align-items:center">
    <button class="sim">Aceitar</button>
    <button class="nao" formaction="/orcamento-obra/{{ o.token }}/recusar" formnovalidate>Recusar</button>
  </div>
  <p class="mut" style="margin:.6rem 0 0">O aceite fica registrado com o seu nome, a data e o endereço de internet de onde foi feito.</p>
</form>{% elif o.vencido %}<div class="cx err">A validade deste orçamento venceu. Peça um novo à empresa.</div>{% endif %}
</div></body></html>"""
