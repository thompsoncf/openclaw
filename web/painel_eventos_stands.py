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


def _wa_num(txt) -> str:
    """Só dígitos, com o 55 na frente quando vier sem país (o cliente digita
    '(86) 9 8888-7777' na página): wa.me sem DDI abre a conversa errada."""
    d = "".join(ch for ch in (txt or "") if ch.isdigit())
    return "55" + d if 10 <= len(d) <= 11 else d


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
            """select id, empresa, whatsapp, criado_em, vendedor_id from prospeccao
               where conta_id=%s and id = any(%s)""", (conta_id, ids)).fetchall()
    return {r[0]: {"empresa": r[1], "whatsapp": r[2], "criado_em": r[3],
                   "vendedor_id": r[4]} for r in rows}


def _vendedores(pool, conta_id: int) -> list[dict]:
    """Os membros que podem receber uma venda — vendedor/gestor ativos. Popula
    o seletor de atribuição no painel e valida a troca."""
    with pool.connection() as c:
        rows = c.execute(
            "select id, coalesce(nullif(nome,''), email, 'Membro '||id) "
            "from membros where conta_id=%s and ativo and papel in ('vendedor','gestor') "
            "order by 2", (conta_id,)).fetchall()
    return [{"id": r[0], "nome": r[1]} for r in rows]


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
    # o cadastro de verdade do cliente de cada stand ocupado (clientes/pessoas):
    # é o que o formulário "Dados do cliente" edita e o contrato lê
    cadastros = es.cadastros_dos_stands(pool, conta[0], stands)

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
    # RESERVA DE 2 ESTANDES (um contrato só): a lista completa mostra uma linha por
    # estande, e o funil UM cartão por reserva (o primeiro do grupo)
    grupos: dict = {}
    for s in stands:
        if s.get("grupo_id") and s["status"] != "livre":
            grupos.setdefault(s["grupo_id"], []).append(s)
    orc_ids = [s["orcamento_id"] for s in stands if s["orcamento_id"]]
    orcs = es.orcamentos_do_estande(pool, conta[0], orc_ids)
    fin = es.situacao_financeira(pool, conta[0], orc_ids)
    reg = es.regras_de_pagamento(cfg)
    saldo_ate_txt = reg["saldo_ate"].strftime("%d/%m") if reg["saldo_ate"] else ""
    todos_itens: dict = {}
    for s in stands:
        if s["status"] == "livre":
            continue
        cli = interessados.get(s["prospeccao_id"]) or {}
        item = dict(s)
        item["cliente"] = cli
        cad = cadastros.get(s["codigo"])
        item["cad"] = cad
        g = grupos.get(s.get("grupo_id")) or [s]
        item["g_codigos"] = [x["codigo"] for x in g]
        item["g_n"] = len(g)
        item["g_total"] = sum(int(x["preco_centavos"] or 0) for x in g)
        item["objeto"] = " + ".join(es.descricao_objeto(x) for x in g)
        o = orcs.get(s["orcamento_id"]) or {}
        f = fin.get(s["orcamento_id"]) or {}
        item["minimo"] = reg["sinal_minimo_centavos"] * len(g)
        item["sinal_inf"] = int(o.get("sinal_centavos") or item["minimo"])
        item["pago"], item["aberto"] = int(f.get("pago", 0)), int(f.get("aberto", 0))
        item["confirmado_fin"] = bool(f)
        item["saldo_ate"] = saldo_ate_txt
        # selo "Cadastro 2/7" enquanto faltar dado do contrato
        cad_badge = ([(f"Cadastro {cad['n_ok']}/{cad['n_total']}", "ambar")]
                     if cad and cad["faltam"] else [])
        # o contrato VIVO da proposta do estande (nasce junto com o comprovante
        # do sinal — evento_stands.garantir_orcamento_e_contrato); tolerante:
        # sem a migração 164, a aba abre sem contrato.
        item["contrato"] = (ctr.por_orcamento(pool, conta[0], s["orcamento_id"])
                            if s["orcamento_id"] else None)
        lider = s["codigo"] == g[0]["codigo"]
        destino = None
        if s["status"] == "pre_reservado":
            item["resumo"] = ("Comprovante recebido · sinal de " + brl(item["sinal_inf"])
                              + " aguardando confirmação")
            item["pend"] = [("Confirmar sinal", "coral")] + cad_badge
            if item["contrato"] and not item["contrato"]["assinado_em"]:
                item["pend"].append(("Contrato na mão do lojista", "azul"))
            destino = "precisa_de_mim"
        elif s["orcamento_id"] and item["aberto"] > 0:
            item["resumo"] = ("Sinal confirmado · saldo de " + brl(item["aberto"])
                              + (f" até {saldo_ate_txt}" if saldo_ate_txt else ""))
            item["pend"] = [("Saldo em aberto", "azul")] + cad_badge
            destino = "com_o_cliente"
        elif s["orcamento_id"] and item["pago"] > 0:
            item["resumo"] = "Sinal e saldo pagos · quitado"
            item["pend"] = list(cad_badge)
            destino = "fechada"
        elif s["orcamento_id"]:
            item["resumo"] = "Sinal confirmado · parcelas e contrato na proposta"
            item["pend"] = [("Acompanhar proposta", "azul")] + cad_badge
            destino = "com_o_cliente"
        else:
            item["resumo"] = "Pagamento confirmado · stand vendido"
            item["pend"] = list(cad_badge)
            destino = "fechada"
        todos_itens[s["codigo"]] = item
        if lider:
            funil[destino].append(item)
    funil["precisa_de_mim"].sort(key=lambda s: s["pre_reserva_ate"] or "")

    # o vínculo (interessado/proposta/contrato) POR CÓDIGO, pra lista completa
    # não repetir as consultas do funil
    vinculos = todos_itens

    # vendedores da conta (pro seletor de atribuição) + nome por id (pra mostrar
    # o vendedor atual de cada venda)
    vendedores = _vendedores(pool, conta[0])
    vend_nomes = {v["id"]: v["nome"] for v in vendedores}

    # O LINK DE VENDAS de cada vendedor (pedido do dono, 30/09/2026): o
    # vendedor manda pro CLIENTE DELE, o cliente escolhe qualquer stand na
    # página pública e a venda cai na conta do vendedor. NÃO é login no app.
    # URL absoluta (é pra colar no WhatsApp); o código é assinado.
    from finance.email_sender import _app_url
    base = f"{_app_url().rstrip('/')}/e/{cfg['slug']}"
    links_vendas = [{"nome": v["nome"], "link": f"{base}?v={es.codigo_vendedor(v['id'])}"}
                    for v in vendedores]
    # quem está logado como MEMBRO que vende (gestor que também vende): os
    # "Copiar link" do mapa já levam a marca dele
    membro_logado = request.session.get("membro_id")
    meu_cod = (es.codigo_vendedor(membro_logado)
               if membro_logado and any(v["id"] == membro_logado for v in vendedores)
               else None)

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
        vendedores=vendedores, vend_nomes=vend_nomes, wa_num=_wa_num,
        links_vendas=links_vendas, meu_cod=meu_cod,
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
def confirmar(request: Request, codigo: str, sinal: str = Form("")):
    """Confirma o SINAL depois de conferir o comprovante. `sinal` é o valor que
    caiu (o mínimo é R$ 1.500 por estande); vazio = o que o cliente informou.
    Numa reserva de 2 estandes confirma os dois."""
    from web.loja_stands import _centavos
    conta, cfg_ou_redir = _acesso(request)
    if conta is None:
        return cfg_ou_redir
    membro_id = request.session.get("membro_id")
    r = es.confirmar_pagamento(get_pool(), conta[0], codigo, membro_id=membro_id,
                               sinal_centavos=_centavos(sinal))
    if not r["ok"]:
        return RedirectResponse(
            f"/painel/eventos/estandes?erro={r['erro']}&abrir={codigo}&aba=comprovante",
            status_code=303)
    cods = " + ".join(r.get("codigos") or [codigo])
    return RedirectResponse(
        f"/painel/eventos/estandes?ok=Sinal confirmado: {cods}. O saldo ficou em aberto no Financeiro.&abrir={codigo}&aba=comprovante",
        status_code=303)


@router.post("/painel/eventos/estandes/{codigo}/saldo")
def registrar_saldo(request: Request, codigo: str, valor: str = Form("")):
    """O cliente pagou (parte do) saldo: baixa nos títulos em aberto da reserva —
    pode ser em mais de uma vez. Só dono/gestor."""
    from web.loja_stands import _centavos
    conta, cfg_ou_redir = _acesso(request)
    if conta is None:
        return cfg_ou_redir
    r = es.registrar_pagamento_saldo(get_pool(), conta[0], codigo, _centavos(valor) or 0,
                                     membro_id=request.session.get("membro_id"))
    if not r["ok"]:
        return RedirectResponse(
            f"/painel/eventos/estandes?erro={r['erro']}&abrir={codigo}&aba=comprovante",
            status_code=303)
    msg = ("Saldo quitado — o Financeiro já recebeu tudo." if r["quitado"]
           else "Pagamento registrado. Ainda falta receber parte do saldo.")
    return RedirectResponse(f"/painel/eventos/estandes?ok={msg}&abrir={codigo}&aba=comprovante",
                            status_code=303)


@router.get("/painel/eventos/estandes/consulta-cnpj")
def consulta_cnpj(request: Request, doc: str = ""):
    """"Buscar na Receita" do formulário Dados do cliente. É a MESMA consulta da
    aba Clientes (finance.cnpj_info, BrasilAPI) — mas numa rota daqui porque o
    gate de papéis só libera ao gestor o prefixo /painel/eventos/estandes; a de
    Clientes é do dono. Devolve razão social, e-mail, cidade e UF (o que a
    consulta traz; endereço e CEP o gestor digita)."""
    from fastapi.responses import JSONResponse as _J
    from finance import cnpj_info, validadoc
    conta, cfg_ou_redir = _acesso(request)
    if conta is None:
        return _J({"ok": False, "erro": "login"}, status_code=401)
    ok, tipo, d = validadoc.valida(doc)
    if tipo != "pj" or not ok:
        return _J({"ok": False, "erro": "CNPJ inválido"})
    info = cnpj_info.consultar_cnpj(d)
    if not info:
        return _J({"ok": False, "erro": "CNPJ não encontrado na Receita"})
    return _J({"ok": True, "nome": info.get("nome"), "email": info.get("email"),
               "cidade": info.get("cidade"), "uf": info.get("uf")})


@router.post("/painel/eventos/estandes/{codigo}/cliente")
def salvar_cliente_do_stand(request: Request, codigo: str,
                            fantasia: str = Form(""), whats: str = Form(""),
                            razao: str = Form(""), doc: str = Form(""),
                            rep: str = Form(""), email: str = Form(""),
                            end: str = Form(""), cep: str = Form(""),
                            cidade: str = Form(""), uf: str = Form(""),
                            obs: str = Form("")):
    """Salva o formulário "Dados do cliente": cria/atualiza o cliente na aba
    Clientes (sem duplicar) e leva os dados pro contrato. Só dono/gestor."""
    conta, cfg_ou_redir = _acesso(request)
    if conta is None:
        return cfg_ou_redir
    r = es.salvar_cadastro_stand(get_pool(), conta[0], codigo, {
        "fantasia": fantasia, "whats": whats, "razao": razao, "doc": doc, "rep": rep,
        "email": email, "end": end, "cep": cep, "cidade": cidade, "uf": uf, "obs": obs})
    if not r["ok"]:
        return RedirectResponse(
            f"/painel/eventos/estandes?erro={r['erro']}&abrir={codigo}", status_code=303)
    msg = (f"Cliente do {codigo} salvo em Clientes"
           + (" (cadastro novo)." if r["acao"] == "criado" else " (atualizado)."))
    if r["congelado"]:
        msg += " O contrato já foi assinado — ele não muda."
    elif r["faltam"]:
        msg += " Ainda falta pro contrato: " + ", ".join(f["l"].lower() for f in r["faltam"]) + "."
    else:
        msg += " Contrato com todos os dados do contratante."
    return RedirectResponse(f"/painel/eventos/estandes?ok={msg}&abrir={codigo}",
                            status_code=303)


@router.post("/painel/eventos/estandes/{codigo}/vendedor")
def trocar_vendedor(request: Request, codigo: str, vendedor_id: str = Form("")):
    """Atribui/corrige o vendedor da venda — grava vendedor_id na prospecção do
    stand. `vendedor_id` vazio ou '0' = tira o vendedor (venda sem dono). Só
    aceita membro válido da conta; o resto vira None (mesma trava do link)."""
    conta, cfg_ou_redir = _acesso(request)
    if conta is None:
        return cfg_ou_redir
    pool = get_pool()
    s = es.buscar(pool, conta[0], codigo)
    if not s or not s.get("prospeccao_id"):
        return RedirectResponse(
            "/painel/eventos/estandes?erro=Sem cadastro de interessado pra atribuir "
            "vendedor — anexe um comprovante com o nome do lojista primeiro.",
            status_code=303)
    with pool.connection() as c:
        vid = None
        if (vendedor_id or "").strip() not in ("", "0"):
            r = c.execute(
                "select id, coalesce(nullif(nome,''), email) from membros "
                "where id=%s and conta_id=%s and ativo and papel in ('vendedor','gestor')",
                (vendedor_id.strip(), conta[0])).fetchone()
            if not r:
                return RedirectResponse(
                    "/painel/eventos/estandes?erro=Vendedor inválido.", status_code=303)
            vid = r[0]
        c.execute("update prospeccao set vendedor_id=%s, atualizado_em=now() "
                  "where id=%s and conta_id=%s",
                  (vid, s["prospeccao_id"], conta[0]))
        c.commit()
    msg = f"Venda do {codigo} sem vendedor." if vid is None else f"Venda do {codigo} atribuída."
    return RedirectResponse(f"/painel/eventos/estandes?ok={msg}", status_code=303)


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
.es-pag .mapa-grid .decor.avenueh{border:none;font-size:8px;font-weight:600;letter-spacing:0.1em;text-transform:uppercase;opacity:0.6}
.es-pag .mapa-grid .decor.faixa{border:none;background:#CFC8B8;color:#3A362C;font-weight:800;letter-spacing:0.06em;font-size:9px}
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

/* ---- 2 stands por empresa + sinal e saldo (aprovado na maquete, 30/09/2026) ---- */
.es-pag .sinal-conf{background:var(--surface-2);border:1px solid var(--line);border-radius:10px;padding:10px 14px;margin-top:12px}
.es-pag .sinal-conf b{display:block;font-size:12.5px}
.es-pag .sinal-conf small{display:block;color:var(--fg-dim);font-size:11px;margin:2px 0 4px}
.es-pag .plano{background:var(--surface-2);border:1px solid var(--line);border-radius:10px;padding:12px 14px;margin-top:12px}
.es-pag .plano-tit{font-size:10.5px;font-weight:800;text-transform:uppercase;letter-spacing:.05em;color:var(--fg-dim);margin-bottom:6px}
.es-pag .plano-lin{display:flex;align-items:center;gap:10px;padding:7px 0;border-top:1px solid var(--line);font-size:13px;flex-wrap:wrap}
.es-pag .plano-lin:first-of-type{border-top:none}
.es-pag .plano-lin > span:first-child{flex:1;min-width:140px}
.es-pag .plano-lin small{display:block;color:var(--fg-dim);font-size:11px}
.es-pag .plano .chip{font-size:10.5px;font-weight:800;padding:3px 9px;border-radius:999px;white-space:nowrap}
.es-pag .plano .chip.ok{background:color-mix(in srgb, var(--mint) 22%, var(--surface));color:var(--mint)}
.es-pag .plano .chip.aberto{background:color-mix(in srgb, #4C8DFF 25%, var(--surface));color:#9DBFFF}
.es-pag .plano .rec{display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;margin-top:8px}
.es-pag .plano .rec .fld{margin:0;flex:1;min-width:140px}

/* ---- cadastro do cliente (aprovado na maquete, 30/09/2026) ---- */
.es-pag .oc-badge.ambar{background:color-mix(in srgb, var(--gold) 28%, var(--surface-2));color:var(--gold-strong)}
.es-pag .cad-tit{font-size:11.5px;color:var(--fg-dim);margin-bottom:10px}
.es-pag .cad-tit b{color:var(--fg)}
.es-pag .cad-prog{display:flex;align-items:center;gap:10px;background:var(--surface-2);border-radius:10px;padding:10px 12px;margin-bottom:12px}
.es-pag .cad-prog .barra{flex:1;height:8px;border-radius:5px;background:var(--surface);overflow:hidden}
.es-pag .cad-prog .barra span{display:block;height:100%;background:var(--mint)}
.es-pag .cad-prog.incompleto .barra span{background:var(--gold)}
.es-pag .cad-prog b{font-family:var(--mono,monospace);font-size:12px;white-space:nowrap}
.es-pag .cad-prog small{display:block;font-size:10.5px;color:var(--fg-dim)}
.es-pag .cad-form{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:2px 14px;margin-bottom:6px}
.es-pag .cad-form .fld.largo{grid-column:1 / -1}
.es-pag .fld{display:block;margin-top:10px}
.es-pag .fld > span{display:block;font-size:10.5px;font-weight:700;text-transform:uppercase;letter-spacing:.03em;color:var(--fg-dim);margin-bottom:4px;white-space:nowrap}
.es-pag .fld > span i{font-style:normal;color:var(--gold-strong)}
.es-pag .fld input{width:100%;background:var(--surface-2);border:1px solid var(--line);border-radius:8px;color:var(--fg);font-family:inherit;font-size:13px;padding:9px 11px;min-height:0;margin:0;box-sizing:border-box}
.es-pag .fld input:focus{outline:none;border-color:var(--mint)}
.es-pag .fld.falta input{border-color:var(--gold)}
.es-pag .fld-nota{font-size:10.5px;color:var(--fg-dim);line-height:1.5}
.es-pag .cad-linha{display:flex;gap:8px;align-items:flex-end;grid-column:span 2}
.es-pag .cad-linha .fld{flex:1}
.es-pag .cad-linha .oc-ghost-btn{height:38px;flex:0 0 auto}
.es-pag .cad-aviso{background:color-mix(in srgb, var(--gold) 14%, var(--surface));border:1px solid var(--gold);border-radius:10px;padding:11px 13px;margin-top:12px;font-size:12px;line-height:1.6}
.es-pag .cad-aviso b{display:block;font-size:13px;margin-bottom:2px}
.es-pag .cad-aviso .acoes{display:flex;gap:8px;flex-wrap:wrap;margin-top:9px}
.es-pag .partes{display:grid;grid-template-columns:repeat(auto-fit,minmax(230px,1fr));gap:10px;margin-bottom:12px}
.es-pag .parte{background:var(--surface-2);border-radius:10px;padding:10px 12px}
.es-pag .parte .p{font-size:9.5px;font-weight:800;text-transform:uppercase;letter-spacing:.06em;color:var(--fg-dim);margin-bottom:3px}
.es-pag .parte b{display:block;font-size:13px}
.es-pag .parte small{display:block;font-size:11px;color:var(--fg-dim);line-height:1.5;margin-top:2px}
.es-pag .parte .falta{color:var(--gold-strong);font-weight:700}

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
{% set cad = d.get('cad') or {} %}
{% set ct = d.get('contrato') %}
{% set zap = wa_num(cad.get('whats') or cli.get('whatsapp')) %}
{% set pend = d.get('pend') or [] %}
{% set rotulo = {'livre':'Livre','pre_reservado':'Reservado','vendido':'Vendido'}[d.status] %}
<div class="oc-hist"{% if grupo %} data-grupo="{{ grupo }}"{% endif %} data-st="{{ d.status }}" data-cod="{{ d.codigo|lower }}"{% if escondido %} hidden{% endif %}>
  <div class="oc-hist-top">
    <div class="oc-open" title="{% if d.status == 'livre' %}Abrir opções{% else %}Ver comprovante, contrato e cliente{% endif %}" onclick="ocToggle(this)">
      <div class="oc-stand-badge" style="background:{{ cor[0] }};color:{{ cor[1] }}"><div class="c">{{ d.codigo }}</div><div class="z">{% if d.get('g_n', 1) > 1 %}{{ d.g_n }} stands{% else %}{{ tam_label.get(d.tamanho, d.tamanho) }}{% endif %}</div></div>
      <div class="oc-body"><b>{% if d.status == 'livre' %}Livre{% else %}{{ cad.get('fantasia') or cli.get('empresa') or 'Interessado da página' }}{% endif %}</b>
        <div class="oc-sub">{% if d.get('g_n', 1) > 1 %}{{ d.g_codigos|join(' + ') }} · num contrato só{% else %}{% if d.zona and d.zona != pav_label %}{{ d.zona }} · {% endif %}{{ pav_label }}{% endif %}</div>
        <div class="oc-sub">{% if d.get('g_n', 1) > 1 %}{{ brl(d.g_total) }} · {% elif d.preco_centavos %}{{ brl(d.preco_centavos) }} · {% endif %}{{ d.get('resumo') or ('pronto pra oferecer' if d.status == 'livre' else rotulo) }}</div></div>
    </div>
    {% if d.get('comprovante_em') or cli.get('criado_em') %}
    <div class="oc-criada"><div class="rot">Criada em</div><div class="dt">{{ data_curta(d.get('comprovante_em') or cli.get('criado_em')) }}</div></div>
    {% endif %}
    <div class="oc-acoes">
      {% if zap %}
      <a class="oc-zap" target="_blank" rel="noopener" title="Falar no WhatsApp" href="https://wa.me/{{ zap }}?text=Olá! Sobre o stand {{ d.codigo }}...">
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
      <div class="oc-comprovante-item"><div class="ic">✓</div><div class="txt"><b>Comprovante do sinal</b><span>enviado{% if d.comprovante_em %} em {{ data_curta(d.comprovante_em) }}{% endif %}{% if d.get('sinal_inf') %} · sinal informado de {{ brl(d.sinal_inf) }}{% endif %}</span></div></div>
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
        {% endif %}
      </div>
      {% if pode_gerir and d.status == 'pre_reservado' %}
      {#- CONFIRMAR O SINAL: o gestor confere no comprovante o valor que caiu (mínimo
          de R$ 1.500 por estande); numa reserva de 2 estandes confirma os dois -#}
      <form method="post" action="/painel/eventos/estandes/{{ d.codigo }}/confirmar"
            data-faltam="{{ cad.faltam|map(attribute='l')|join(', ')|lower|replace('cnpj / cpf','CNPJ/CPF') if cad.get('faltam') else '' }}"
            onsubmit="return esConfirmar(this)">
        <div class="sinal-conf"><b>Sinal recebido</b>
          <small>Confira no comprovante o valor que caiu. Mínimo de {{ brl(d.minimo) }}{% if d.g_n > 1 %} (R$ 1.500 × {{ d.g_n }} stands){% endif %} · o saldo de {{ brl(d.g_total - d.sinal_inf) }} fica em aberto{% if d.saldo_ate %} até {{ d.saldo_ate }}{% endif %}.</small>
          <label class="fld" style="margin-top:4px"><span>Valor do sinal (R$)</span><input name="sinal" inputmode="decimal" value="{{ '%.2f'|format(d.sinal_inf / 100)|replace('.', ',') }}"></label></div>
        <div class="oc-acoes-detail" style="margin-top:10px">
          <button class="oc-ghost-btn prim" type="submit">Confirmar pagamento{% if d.g_n > 1 %} dos {{ d.g_n }} stands{% endif %}</button>
          <button class="oc-ghost-btn" type="submit" form="lib-{{ d.codigo }}">Liberar {% if d.g_n > 1 %}os {{ d.g_n }} stands{% else %}stand{% endif %}</button>
        </div>
      </form>
      <form id="lib-{{ d.codigo }}" method="post" action="/painel/eventos/estandes/{{ d.codigo }}/liberar"
            onsubmit="return confirm('Liberar {{ d.g_codigos|join(' + ') }} de volta pra livre?')"></form>
      {% endif %}
      {% if pode_gerir and d.status == 'vendido' and d.get('confirmado_fin') %}
      {#- O PLANO: o que já entrou (sinal + pagamentos do saldo) e o que falta -#}
      <div class="plano"><div class="plano-tit">Plano de pagamento</div>
        <div class="plano-lin"><span>Recebido<small>sinal + pagamentos do saldo · já lançado no Financeiro</small></span><b>{{ brl(d.pago) }}</b><span class="chip ok">pago ✓</span></div>
        <div class="plano-lin"><span>Saldo em aberto<small>{% if d.saldo_ate %}vence em {{ d.saldo_ate }} (dia do evento){% endif %}</small></span><b>{{ brl(d.aberto) }}</b>
          {% if d.aberto > 0 %}<span class="chip aberto">em aberto</span>{% else %}<span class="chip ok">quitado ✓</span>{% endif %}</div>
        {% if d.aberto > 0 %}
        <form class="rec" method="post" action="/painel/eventos/estandes/{{ d.codigo }}/saldo">
          <label class="fld"><span>Pagamento recebido (R$)</span><input name="valor" inputmode="decimal" value="{{ '%.2f'|format(d.aberto / 100)|replace('.', ',') }}"></label>
          <button class="oc-ghost-btn prim" type="submit">Registrar pagamento do saldo</button>
        </form>
        <p class="fld-nota">Pode ser em mais de uma vez (Pix ou cartão): cada baixa abate o saldo, e vira <b>quitado</b> quando zerar.</p>
        <div class="fld-nota">Se o saldo não for pago até {{ d.saldo_ate or 'a data-limite' }}: o <b>sinal fica retido</b> (Cláusula VI) e você libera {% if d.g_n > 1 %}os {{ d.g_n }} stands{% else %}o stand{% endif %} na mão — o sistema só avisa.
          <form method="post" action="/painel/eventos/estandes/{{ d.codigo }}/liberar" style="display:inline"
                onsubmit="return confirm('Liberar {{ d.g_codigos|join(' + ') }}? O sinal recebido fica retido.')">
            <button class="oc-ghost-btn" type="submit">Liberar {% if d.g_n > 1 %}os {{ d.g_n }} stands{% else %}stand{% endif %} (sinal retido)</button></form></div>
        {% endif %}
      </div>
      {% endif %}
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
      {% if d.status == 'pre_reservado' and pode_gerir and cad.get('faltam') %}
      {#- o aviso da maquete: confirmar NUNCA é bloqueado (o dinheiro já entrou e o
          stand precisa ficar garantido) — só se avisa que o contrato depende do cadastro -#}
      <div class="cad-aviso" hidden><b>⚠ O cadastro do cliente está incompleto ({{ cad.n_ok }}/{{ cad.n_total }})</b>
        Você pode confirmar o pagamento agora — o stand fica garantido — mas o contrato só sai completo depois de preencher: {{ cad.faltam|map(attribute='l')|join(', ')|lower|replace('cnpj / cpf','CNPJ/CPF') }}.
        <div class="acoes">
          <button type="button" class="oc-ghost-btn prim" onclick="esIrCadastro(this)">Completar cadastro agora</button>
          <button type="button" class="oc-ghost-btn" onclick="esConfirmarMesmoAssim(this)">Confirmar assim mesmo</button>
        </div></div>
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
      <div class="partes">
        <div class="parte"><div class="p">Contratante (lido do cadastro)</div>
          <b>{% if cad.get('razao') %}{{ cad.razao }}{% else %}<span class="falta">⚠ falta a razão social</span>{% endif %}</b>
          <small>{% if cad.get('doc') %}CNPJ/CPF {{ cad.doc }}{% else %}<span class="falta">⚠ falta o CNPJ/CPF</span>{% endif %}<br>
            {% if cad.get('end') %}{{ cad.end }}{% if cad.get('cidade') %} · {{ cad.cidade }}{% if cad.get('uf') %}/{{ cad.uf }}{% endif %}{% endif %}{% else %}<span class="falta">⚠ falta o endereço</span>{% endif %}<br>
            Representante: {% if cad.get('rep') %}{{ cad.rep }}{% else %}<span class="falta">⚠ falta</span>{% endif %}</small></div>
        <div class="parte"><div class="p">Objeto</div><b>{{ d.get('objeto') or ('Stand ' ~ d.codigo) }}</b>
          <small>{% if d.get('g_n', 1) > 1 %}{{ brl(d.g_total) }} · {{ d.g_n }} stands, sem desconto{% elif d.preco_centavos %}{{ brl(d.preco_centavos) }}{% endif %}<br>Vai no contrato como o espaço locado, com o evento e o período</small></div>
      </div>
      {% if cad.get('faltam') and not ct.assinado_em %}
      <div class="cad-aviso" style="margin-top:0;margin-bottom:12px"><b>Contrato com campos em branco</b>
        Falta preencher {{ cad.faltam|map(attribute='l')|join(', ')|lower|replace('cnpj / cpf','CNPJ/CPF') }}.
        <div class="acoes"><button type="button" class="oc-ghost-btn prim" onclick="esIrCadastro(this)">Preencher no cadastro</button></div></div>
      {% elif not ct.assinado_em %}
      <p style="margin:0 0 10px;color:var(--mint-strong);font-weight:700">✓ Cadastro completo — o contrato sai com todos os dados do contratante.</p>
      {% endif %}
      <p class="fld-nota" style="margin:0 0 10px">{% if ct.assinado_em %}Contrato assinado: fica congelado como o cliente aprovou.{% else %}Enquanto o contrato não estiver assinado, ele relê o cadastro: corrigiu aqui, o documento acompanha.{% endif %}</p>
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
      {% if d.status == 'livre' %}
      <p class="oc-vazio" style="margin:0">Sem interessado ainda — o cadastro nasce quando o comprovante chegar.</p>
      {% else %}
      <div class="cad-tit">Mesmo formulário da aba <b>Clientes</b> do Zaq{% if pode_gerir %} — salvar já cria/atualiza o cliente lá{% endif %}.</div>
      <div class="cad-prog{% if cad.get('faltam') %} incompleto{% endif %}">
        <div class="barra"><span style="width:{{ (cad.n_ok / cad.n_total * 100) if cad.get('n_total') else 0 }}%"></span></div>
        <div><b>{{ cad.get('n_ok', 0) }}/{{ cad.get('n_total', 7) }}</b>
          <small>{% if cad.get('faltam') %}faltam pro contrato: {{ cad.faltam|map(attribute='l')|join(', ')|lower|replace('cnpj / cpf','CNPJ/CPF') }}{% else %}completo — o contrato sai com todos os dados{% endif %}</small></div>
      </div>
      {% if pode_gerir %}
      {% macro falta(k) %}{% if cad.get('faltam') and k in (cad.faltam|map(attribute='k')|list) %} falta{% endif %}{% endmacro %}
      <form method="post" action="/painel/eventos/estandes/{{ d.codigo }}/cliente" oninput="esCadProg(this)">
        <div class="cad-form">
          <label class="fld{{ falta('fantasia') }}"><span>Nome fantasia <i>*</i></span><input name="fantasia" data-req="1" maxlength="200" value="{{ cad.get('fantasia','') }}" required></label>
          <label class="fld{{ falta('razao') }}"><span>Razão social <i>*</i></span><input name="razao" data-req="1" maxlength="200" value="{{ cad.get('razao','') }}" placeholder="Como sai no contrato"></label>
          <div class="cad-linha"><label class="fld{{ falta('doc') }}"><span>CNPJ / CPF <i>*</i></span><input name="doc" data-req="1" maxlength="20" value="{{ cad.get('doc','') }}" placeholder="00.000.000/0000-00"></label>
            <button type="button" class="oc-ghost-btn" onclick="esReceita(this)">Buscar na Receita</button></div>
          <label class="fld{{ falta('rep') }}"><span>Representante legal <i>*</i></span><input name="rep" data-req="1" maxlength="200" value="{{ cad.get('rep','') }}" placeholder="Quem assina pelo lojista"></label>
          <label class="fld{{ falta('whats') }}"><span>WhatsApp <i>*</i></span><input name="whats" data-req="1" maxlength="40" value="{{ cad.get('whats','') }}"></label>
          <label class="fld"><span>E-mail</span><input name="email" type="email" maxlength="200" value="{{ cad.get('email','') }}" placeholder="contato@loja.com.br"></label>
          <label class="fld largo{{ falta('end') }}"><span>Endereço <i>*</i></span><input name="end" data-req="1" maxlength="300" value="{{ cad.get('end','') }}" placeholder="Rua, número, bairro"></label>
          <label class="fld"><span>CEP</span><input name="cep" maxlength="12" value="{{ cad.get('cep','') }}" placeholder="00000-000"></label>
          <label class="fld{{ falta('cidade') }}"><span>Cidade <i>*</i></span><input name="cidade" data-req="1" maxlength="120" value="{{ cad.get('cidade','') }}" placeholder="Teresina"></label>
          <label class="fld"><span>UF</span><input name="uf" maxlength="2" value="{{ cad.get('uf','') }}" placeholder="PI" style="text-transform:uppercase"></label>
          <label class="fld largo"><span>Observações</span><input name="obs" maxlength="500" value="{{ cad.get('obs','') }}" placeholder="Anotação livre"></label>
        </div>
        <div class="cad-receita fld-nota" hidden></div>
        <div style="display:flex;gap:8px;flex-wrap:wrap;align-items:center;margin-top:6px">
          <button class="oc-ghost-btn prim" type="submit">Salvar cadastro</button>
          <span class="fld-nota" style="margin:0">Se o CNPJ/CPF ou o WhatsApp já existirem em Clientes, os dados entram no mesmo cadastro — sem duplicar.</span>
        </div>
      </form>
      {% else %}
      <div class="oc-field-grid">
        <div class="oc-field"><span>Nome fantasia</span><b>{{ cad.get('fantasia') or '—' }}</b></div>
        <div class="oc-field"><span>WhatsApp</span><b>{{ cad.get('whats') or '—' }}</b></div>
        <div class="oc-field"><span>Origem</span><b>Página de stands</b></div>
        <div class="oc-field"><span>Stand</span><b>{{ d.codigo }} · {{ tam_label.get(d.tamanho, d.tamanho) }}</b></div>
      </div>
      <p class="fld-nota" style="margin:0 0 10px">Só dono/gestor edita os dados do contrato.</p>
      {% endif %}
      {% endif %}
      {% if d.prospeccao_id and d.status != 'livre' %}
      <div style="border-top:1px solid var(--line);margin:16px 0 4px"></div>
      {#- vendedor da venda: mostra o atual e (pra gestão) deixa trocar/corrigir.
          O automático vem do link do vendedor; isto é a correção manual. -#}
      <div class="oc-field" style="margin-bottom:12px">
        <span>Vendedor</span>
        {% if pode_gerir %}
        <form method="post" action="/painel/eventos/estandes/{{ d.codigo }}/vendedor" style="display:flex;gap:6px;flex-wrap:wrap;align-items:center;margin-top:2px">
          <select name="vendedor_id" style="background:var(--surface-2);border:1px solid var(--line);border-radius:8px;color:var(--fg);font-family:inherit;font-size:12.5px;padding:7px 10px">
            <option value="0"{% if not cli.get('vendedor_id') %} selected{% endif %}>— Sem vendedor —</option>
            {% for v in vendedores %}<option value="{{ v.id }}"{% if cli.get('vendedor_id') == v.id %} selected{% endif %}>{{ v.nome }}</option>{% endfor %}
          </select>
          <button class="oc-ghost-btn" type="submit">Salvar vendedor</button>
        </form>
        {% else %}<b>{{ vend_nomes.get(cli.get('vendedor_id')) or 'Sem vendedor' }}</b>{% endif %}
      </div>
      <a class="oc-ghost-btn" href="/painel/prospeccao">Abrir no Funil →</a>
      {% elif d.status != 'livre' %}<p class="oc-vazio" style="margin:0">Este envio veio sem nome — o interessado não preencheu o cadastro.</p>{% endif %}
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

<div class="preco-card">
  <h3>Links de vendas dos vendedores</h3>
  <p class="preco-obs">Cada vendedor manda o link dele pro <b>cliente dele</b>: o cliente abre a página, escolhe <b>qualquer stand</b> e compra — a venda cai na conta do vendedor. (Não é acesso ao sistema.){% if not pode_gerir %} Seu link fica no app do vendedor.{% endif %}</p>
  {% if pode_gerir %}
  {% for l in links_vendas %}
  <div class="oc-comprovante-item" style="margin-bottom:8px">
    <div class="ic">🔗</div>
    <div class="txt"><b>{{ l.nome }}</b><span style="word-break:break-all">{{ l.link }}</span></div>
    <button class="oc-ghost-btn prim" type="button" data-link="{{ l.link }}" onclick="esCopiarLink(this)">Copiar</button>
  </div>
  {% else %}
  <p class="oc-vazio" style="margin:0">Nenhum vendedor cadastrado ainda — adicione a equipe em <a href="/painel/equipe" style="color:var(--mint)">Pessoas</a>.</p>
  {% endfor %}
  {% endif %}
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
var MEU_COD = {{ meu_cod|tojson }};
</script>
<script>
(function(){
""" + PLANTA_DEFS_JS + r"""
  var tamLabel = {'4x2':'4x2m','4x3':'4x3m','3x2':'3x2m','2x2':'2x2m','3x3':'3x3m','tenda':'Espaço em tenda','personalizado':'Stand personalizado'};
  // mesma pegada proporcional da página pública (largura=frente, altura=fundo),
  // escalada pra grid compacta do painel (colunas de 30px vs 34px da maquete)
  var sizeBase = {'2x2':{w:24,h:16},'3x2':{w:34,h:16},'3x3':{w:34,h:22},'4x2':{w:24,h:28},'4x3':{w:34,h:28},'tenda':{w:24,h:28},'personalizado':{w:28,h:28}};
  var ESCALA = 30/34;

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

  function standTile(code, def){
    var s = MAPA[code];
    if (!s) return null;
    var btn = document.createElement('button');
    btn.className = 'stand st-' + s.status + (code === selecionado ? ' is-selected' : '');
    // o def da planta pode sobrescrever a pegada padrão (stand "em pé"/"deitado")
    var base = sizeBase[s.tamanho] || {w:29,h:23};
    btn.style.width = Math.round(((def && def.w) || base.w) * ESCALA) + 'px';
    btn.style.height = Math.round(((def && def.h) || base.h) * ESCALA) + 'px';
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
        var t = standTile(d.prefix + num, d);
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
    if (MEU_COD) url += '&v=' + encodeURIComponent(MEU_COD);
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
// ---- cadastro do cliente ----
function esCadProg(form){
  // progresso e destaque de "falta" ao vivo, sem recarregar — como na maquete
  var reqs = form.querySelectorAll('[data-req]');
  var ok = 0, faltam = [];
  reqs.forEach(function(inp){
    var vazio = !inp.value.trim();
    inp.closest('.fld').classList.toggle('falta', vazio);
    if (vazio) faltam.push(inp.closest('.fld').querySelector('span').textContent.replace('*','').trim().toLowerCase().replace('cnpj / cpf','CNPJ/CPF'));
    else ok++;
  });
  var tab = form.closest('.oc-detail-body');
  var prog = tab.querySelector('.cad-prog');
  if (prog){
    prog.classList.toggle('incompleto', faltam.length > 0);
    prog.querySelector('.barra span').style.width = (reqs.length ? ok / reqs.length * 100 : 0) + '%';
    prog.querySelector('b').textContent = ok + '/' + reqs.length;
    prog.querySelector('small').textContent = faltam.length ? 'faltam pro contrato: ' + faltam.join(', ') : 'completo — o contrato sai com todos os dados';
  }
  var linha = form.closest('.oc-hist');
  linha.querySelectorAll('.oc-badge.ambar').forEach(function(b){
    if (faltam.length) b.textContent = 'Cadastro ' + ok + '/' + reqs.length; else b.remove();
  });
}
function esReceita(btn){
  var form = btn.closest('form');
  var msg = form.querySelector('.cad-receita');
  var doc = form.elements['doc'].value.trim();
  msg.hidden = false;
  if (!doc){ msg.textContent = 'Digite o CNPJ antes de buscar.'; return; }
  msg.textContent = 'Consultando a Receita…';
  fetch('/painel/eventos/estandes/consulta-cnpj?doc=' + encodeURIComponent(doc), {headers:{'x-requested-with':'fetch'}})
    .then(function(r){ return r.json(); })
    .then(function(j){
      if (!j.ok){ msg.textContent = j.erro || 'Não consegui consultar agora.'; return; }
      if (j.nome) form.elements['razao'].value = j.nome;
      if (j.email && !form.elements['email'].value.trim()) form.elements['email'].value = j.email;
      if (j.cidade) form.elements['cidade'].value = j.cidade;
      if (j.uf) form.elements['uf'].value = j.uf;
      msg.textContent = '✓ Receita: razão social, e-mail, cidade e UF preenchidos — confira. Endereço e CEP você digita.';
      esCadProg(form);
    })
    .catch(function(){ msg.textContent = 'Não consegui consultar agora — digite os dados.'; });
}
function esIrCadastro(btn){
  var detail = btn.closest('.oc-detail');
  var abas = detail.querySelectorAll('.oc-subtab');
  if (abas[2]) abas[2].click();
}
function esConfirmar(form){
  // confirmar NUNCA é bloqueado: com cadastro incompleto só avisa (o dinheiro
  // já entrou e o stand precisa ficar garantido)
  if (form.dataset.faltam && !form.dataset.ok){
    var aviso = form.closest('.oc-detail-body').querySelector('.cad-aviso');
    if (aviso){ aviso.hidden = false; return false; }
  }
  return true;
}
function esConfirmarMesmoAssim(btn){
  var form = btn.closest('.oc-detail-body').querySelector('form[data-faltam]');
  form.dataset.ok = '1';
  form.submit();
}
// volta do POST com ?abrir=G60: reabre a linha no funil e a aba "Dados do cliente"
document.addEventListener('DOMContentLoaded', function(){
  var cod = (new URLSearchParams(location.search).get('abrir') || '').toLowerCase();
  if (!cod || !window.mapaAbrirFunil) return;
  window.mapaAbrirFunil(cod);
  var linha = document.querySelector('#funil-list .oc-hist[data-cod="' + cod + '"]');
  var aba = {comprovante:0, contrato:1, cliente:2}[new URLSearchParams(location.search).get('aba') || 'cliente'];
  var abas = linha && linha.querySelectorAll('.oc-subtab');
  if (abas && abas[aba != null ? aba : 2]) abas[aba != null ? aba : 2].click();
});
function esCopiarLink(btn){
  var txt = btn.dataset.link;
  if (!txt) return;
  var done = function(){ var t = btn.textContent; btn.textContent = 'Copiado ✓'; setTimeout(function(){ btn.textContent = t; }, 1600); };
  if (navigator.clipboard && navigator.clipboard.writeText){ navigator.clipboard.writeText(txt).then(done); }
  else { window.prompt('Copia o link:', txt); }
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
