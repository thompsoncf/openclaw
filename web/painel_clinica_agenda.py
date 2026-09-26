"""A agenda da clínica: /painel/clinica/agenda (dia e semana), o novo agendamento e
o agendamento aberto. A lógica mora em finance/clinica_agenda.py.

SÓ A CLÍNICA (CLAUDE.md §6). Dono, gestor e a recepção (papel vendedor) usam; só
dono e gestor mexem na confirmação da véspera.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone
from urllib.parse import urlencode

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db.conexao import get_pool
from finance import clinica_agenda as ca
from finance import clinica_config as cc
from finance import raio_x_perfil as rxp
from web.portal import _env, _render, conta_logada, nicho_da_conta

router = APIRouter()
_log = logging.getLogger("openclaw.painel_clinica_agenda")

_AVISOS = {
    "marcado": "Agendado.",
    "marcado_msg": "Agendado, e a confirmação foi pro WhatsApp do paciente.",
    "marcado_sem_msg": "Agendado. A mensagem não saiu (sem WhatsApp conectado ou número inválido).",
    "situacao": "Status atualizado.",
    "remarcado": "Remarcado.",
    "mensagem": "Mensagem enviada.",
    "sem_mensagem": "A mensagem não saiu (sem WhatsApp conectado ou número inválido).",
    "salvo": "Configuração salva.",
}


def _acesso(request: Request):
    conta = conta_logada(request)
    if conta is None:
        return None, False, RedirectResponse("/login", status_code=303)
    papel = request.session.get("papel", "dono")
    if papel not in ("dono", "gestor", "vendedor"):
        return None, False, RedirectResponse("/painel", status_code=303)
    if rxp.perfil_por_nicho(nicho_da_conta(conta)) != "clinica":
        return None, False, RedirectResponse("/painel", status_code=303)
    return conta, papel in ("dono", "gestor"), None


def _int(txt) -> int | None:
    try:
        return int(str(txt).strip()) if str(txt or "").strip() else None
    except ValueError:
        return None


def _data(txt, padrao: date) -> date:
    try:
        d = datetime.strptime((txt or "").strip(), "%Y-%m-%d").date()
    except ValueError:
        return padrao
    return d if 2000 <= d.year <= 2100 else padrao


def _instante(txt: str) -> datetime | None:
    """'2026-10-01T11:00:00+00:00' (o valor do rádio de horário) → datetime UTC."""
    try:
        dt = datetime.fromisoformat((txt or "").strip())
    except ValueError:
        return None
    return dt if (dt.tzinfo and 2000 <= dt.year <= 2100) else None


def _ir(request: Request, url: str, aviso: str = "", erro: str = "") -> RedirectResponse:
    if erro:
        request.session["agenda_erro"] = erro[:300]
    if aviso in _AVISOS:
        url += ("&" if "?" in url else "?") + f"aviso={aviso}"
    return RedirectResponse(url, status_code=303)


def _ctx_base(request: Request) -> dict:
    q = request.query_params
    return {"aviso": _AVISOS.get(q.get("aviso") or "", ""),
            "erro": request.session.pop("agenda_erro", ""), "secao_ativa": "agenda",
            "SIT": ca.SITUACOES, "SIT_D": ca.SIT_D}


# ------------------------------------------------------------------ dia e semana

@router.get("/painel/clinica/agenda", response_class=HTMLResponse)
def agenda(request: Request):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    conta_id = conta[0]
    q = request.query_params
    agora = datetime.now(timezone.utc)
    hoje = ca.hoje_br(agora)
    data = _data(q.get("data"), hoje)
    vista = "semana" if q.get("vista") == "semana" else "dia"
    local_id = _int(q.get("local"))
    with get_pool().connection() as c:
        profs = [p for p in cc.listar_profissionais(c, conta_id) if p["funcao"] != "Recepção, não atende"]
        locais = cc.listar_locais(c, conta_id)
        cfg = ca.config(c, conta_id)
        from finance import clinica_vagas as cvg
        vagas_esperando = cvg.esperando(c, conta_id)
        prof_id = _int(q.get("prof")) or (profs[0]["id"] if profs else None)
        if vista == "semana" and prof_id:
            segunda = data - timedelta(days=data.isoweekday() - 1)
            dados = ca.semana(c, conta_id, prof_id, segunda, agora)
            ant, prox = segunda - timedelta(days=7), segunda + timedelta(days=7)
            titulo_data = f"Semana de {segunda:%d/%m} a {segunda + timedelta(days=6):%d/%m}"
        else:
            vista = "dia"
            dados = ca.dia(c, conta_id, data, agora, local_id)
            ant, prox = data - timedelta(days=1), data + timedelta(days=1)
            titulo_data = f"{ca._SEMANA[data.isoweekday()].capitalize()}, {data:%d/%m/%Y}"
            if data == hoje:
                titulo_data += " · hoje"
    base = {"vista": vista, "local": local_id or "", "prof": prof_id or ""}

    def link(**kw):
        return "/painel/clinica/agenda?" + urlencode({k: v for k, v in dict(base, **kw).items() if v not in ("", None)})

    return _render("clinica_agenda.html", request, titulo="Agenda", **_ctx_base(request),
                   vista=vista, d=dados, profs=profs, locais=locais, local_id=local_id,
                   prof_id=prof_id, titulo_data=titulo_data, gerencia=gerencia, cfg=cfg,
                   vagas_esperando=vagas_esperando,
                   data_iso=data.isoformat(), link_ant=link(data=ant.isoformat()),
                   link_prox=link(data=prox.isoformat()), link_hoje=link(data=hoje.isoformat()),
                   link_dia=link(vista="dia", data=data.isoformat()),
                   link_semana=link(vista="semana", data=data.isoformat()),
                   prof_nome={p["id"]: p["nome"] for p in profs},
                   prof_marca=any(p["id"] == prof_id and p["tipos"] for p in profs))


# ------------------------------------------------------------------ novo agendamento

@router.get("/painel/clinica/agenda/novo", response_class=HTMLResponse)
def novo(request: Request):
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return redir
    conta_id = conta[0]
    q = request.query_params
    agora = datetime.now(timezone.utc)
    hoje = ca.hoje_br(agora)
    # o que a recepção já tinha preenchido (buscar paciente, ou um erro) volta pela
    # sessão — nome, celular e busca nunca vão na URL
    form = request.session.pop("agenda_form", None) or {}
    with get_pool().connection() as c:
        if not form and _int(q.get("lead")):
            r = c.execute("""select id, coalesce(nullif(contato,''), empresa, ''),
                                    coalesce(nullif(whatsapp,''), telefone, '')
                               from prospeccao where id=%s and conta_id=%s""", (_int(q.get("lead")), conta_id)).fetchone()
            if r:
                form = {"lead_id": str(r[0]), "nome": r[1], "fone": r[2], "busca": r[1]}
        profs = [p for p in cc.listar_profissionais(c, conta_id)
                 if p["funcao"] != "Recepção, não atende" and p["tipos"]]
        tipos = cc.listar_tipos(c, conta_id)
        prof_id = _int(q.get("prof")) or (profs[0]["id"] if profs else None)
        prof = next((p for p in profs if p["id"] == prof_id), None)
        tipos_prof = [t for t in tipos if prof and t["id"] in prof["tipos"]]
        tipo_id = _int(q.get("tipo"))
        if not any(t["id"] == tipo_id for t in tipos_prof):
            tipo_id = tipos_prof[0]["id"] if tipos_prof else None
        data = _data(q.get("data"), hoje)
        livres = (ca.livres(c, conta_id, prof_id, tipo_id, max(data, hoje), dias=14, agora=agora, limite=24)
                  if prof_id and tipo_id else [])
        pedido = None
        if q.get("hora") and prof_id:
            try:
                h = datetime.strptime(q.get("hora"), "%H:%M").time()
                pedido = ca.utc(data, h)
            except ValueError:
                pedido = None
        ja = _instante((form.get("inicio") or "").replace("enc|", ""))
        if ja and any(x["inicio"] == ja for x in livres):
            pedido = ja
        escolhido = pedido if pedido and any(x["inicio"] == pedido for x in livres) else None
        pode_encaixe = bool(pedido and not escolhido and pedido > agora)
        achados = ca.buscar_pacientes(c, conta_id, form.get("busca") or "")
    opcoes = [{"valor": x["inicio"].isoformat(), "txt": f"{ca.dia_txt(x['inicio'])} {ca.hora_txt(x['inicio'])}",
               "on": x["inicio"] == (escolhido or (livres[0]["inicio"] if livres else None))}
              for x in livres]
    tipo = next((t for t in tipos if t["id"] == tipo_id), None)
    return _render("clinica_agenda_novo.html", request, titulo="Novo agendamento", **_ctx_base(request),
                   profs=profs, prof=prof, tipos_prof=tipos_prof, tipo=tipo, opcoes=opcoes,
                   pedido=pedido.isoformat() if pode_encaixe else "",
                   pedido_txt=(f"{ca.dia_txt(pedido)} {ca.hora_txt(pedido)}" if pode_encaixe else ""),
                   achados=achados, busca=form.get("busca") or "", data_iso=data.isoformat(),
                   hora=q.get("hora") or "", ORIGENS=ca.ORIGENS, form=form,
                   lead_escolhido=_int(form.get("lead_id")))


@router.post("/painel/clinica/agenda/novo")
def novo_salvar(request: Request, prof: str = Form(""), tipo: str = Form(""), inicio: str = Form(""),
                lead_id: str = Form(""), nome: str = Form(""), fone: str = Form(""),
                origem: str = Form(""), observacao: str = Form(""), busca: str = Form(""),
                data: str = Form(""), acao: str = Form("agendar")):
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return redir
    conta_id, membro = conta[0], request.session.get("membro_id")
    # o rádio do encaixe carrega a marca no próprio valor: escolher outro horário
    # (livre) nunca vira encaixe por um campo escondido que valia pra todos
    encaixe = inicio.startswith("enc|")
    quando = _instante(inicio.replace("enc|", ""))
    dia_volta = ca.local(quando).date().isoformat() if quando else data
    volta = "/painel/clinica/agenda/novo?" + urlencode({"prof": prof, "tipo": tipo, "data": dia_volta})
    guardar = {"inicio": inicio, "lead_id": lead_id, "nome": nome[:120], "fone": fone[:30],
               "origem": origem, "observacao": observacao[:500], "busca": busca[:60]}
    if acao == "buscar":
        request.session["agenda_form"] = guardar
        return _ir(request, volta)
    if not quando:
        request.session["agenda_form"] = guardar
        return _ir(request, volta, erro="Escolha um horário.")
    with get_pool().connection() as c:
        eid, erro = ca.agendar(c, conta_id, profissional_id=_int(prof) or 0, servico_id=_int(tipo) or 0,
                               inicio=quando, lead_id=_int(lead_id), nome=nome, fone=fone, origem=origem,
                               observacao=observacao, encaixe=encaixe, membro_id=membro)
        if erro:
            c.rollback()
            request.session["agenda_form"] = guardar
            return _ir(request, volta, erro=erro)
        c.commit()
        aviso = "marcado"
        if acao == "confirmar":
            ev = ca.evento(c, conta_id, eid)
            promete = ca.config(c, conta_id)["confirmacao_modo"] == "ligado"
            ok = ev and ca.enviar(c, conta_id, ev, ca.texto_marcado(c, conta_id, ev, promete),
                                  autor="humano", membro_id=membro).get("ok")
            c.commit()
            aviso = "marcado_msg" if ok else "marcado_sem_msg"
    dia_ = ca.local(quando).date().isoformat()
    return _ir(request, f"/painel/clinica/agenda?data={dia_}", aviso)


# ------------------------------------------------------------------ o agendamento aberto

@router.get("/painel/clinica/agenda/evento/{evento_id}", response_class=HTMLResponse)
def ver_evento(request: Request, evento_id: int):
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return redir
    conta_id = conta[0]
    agora = datetime.now(timezone.utc)
    with get_pool().connection() as c:
        ev = ca.evento(c, conta_id, evento_id)
        if not ev:
            return RedirectResponse("/painel/clinica/agenda", status_code=303)
        prof = next((p for p in cc.listar_profissionais(c, conta_id, so_ativos=False)
                     if p["id"] == ev["profissional_id"]), None)
        remarcar = []
        if ev["situacao"] in ("agendado", "confirmado", "faltou") and ev["servico_id"] and prof:
            remarcar = [{"valor": x["inicio"].isoformat(), "txt": f"{ca.dia_txt(x['inicio'])} {ca.hora_txt(x['inicio'])}"}
                        for x in ca.livres(c, conta_id, prof["id"], ev["servico_id"], ca.hoje_br(agora),
                                           dias=14, agora=agora, limite=13, ignorar=evento_id)
                        if x["inicio"] != ev["inicio"]][:12]      # o horário de agora não é opção
        conversa = ca._conversa(c, conta_id, ev)
        promete = ca.config(c, conta_id)["confirmacao_modo"] == "ligado"
        msg_marcado = ca.texto_marcado(c, conta_id, ev, promete)
        msg_vespera = ca.texto_vespera(c, conta_id, ev, agora)
        from finance import clinica_pacotes as ckp
        hoje = ca.hoje_br(agora)
        pacote_feito = ckp.do_evento(c, conta_id, evento_id, hoje) if ev["situacao"] == "finalizado" else None
        pacote_vai = ckp.para_o_evento(c, conta_id, ev) if ev["situacao"] != "finalizado" else None
        from finance import clinica_assinaturas as cas
        assin_vai = cas.cobre(c, conta_id, ev) if ev["situacao"] != "finalizado" else None
        assin_feito = cas.do_evento(c, conta_id, evento_id) if ev["situacao"] == "finalizado" else None
        if assin_vai:
            pacote_vai = None                      # a sessão do mês da assinatura cobre
        tipo_ev = next((t for t in cc.listar_tipos(c, conta_id, so_ativos=False) if t["id"] == ev["servico_id"]), None)
        volta_padrao = (tipo_ev or {}).get("volta_dias") or ""
        try:
            with c.transaction():
                r = c.execute("select vence_em, estado from clinica_retornos where conta_id=%s and evento_id=%s",
                              (conta_id, evento_id)).fetchone()
        except Exception:  # noqa: BLE001
            r = None
        retorno = {"vence": r[0], "estado": r[1]} if r else None
        pac_cfg = ckp.config(c, conta_id)
        # produto no fim do atendimento (fase 7c): a reposição do paciente e o que vence logo
        prod = None
        if ev["situacao"] in ("presente", "atendimento", "finalizado"):
            from finance import clinica_produtos as cpr
            prod = {"lista": [p for p in cpr.produtos(c, conta_id, hoje) if p["saldo"] > 0 and p["preco_centavos"]],
                    "sugestoes": cpr.sugestoes(c, conta_id, ev, hoje),
                    "vendas": cpr.vendas_do_evento(c, conta_id, evento_id), "pagamentos": cpr.PAGAMENTOS}
    return _render("clinica_agenda_evento.html", request, titulo="Agendamento", **_ctx_base(request),
                   pacote_feito=pacote_feito, pacote_vai=pacote_vai, assin_vai=assin_vai, assin_feito=assin_feito, volta_padrao=volta_padrao, retorno=retorno,
                   pac_cfg=pac_cfg, prod=prod,
                   ev=ev, prof=prof, proximos=ca.PROXIMOS.get(ev["situacao"], ()), remarcar=remarcar,
                   conversa=conversa, quando=f"{ca.dia_txt(ev['inicio'])} {ev['hora']}–{ev['fim_txt']}",
                   msg_marcado=msg_marcado, msg_vespera=msg_vespera,
                   data_iso=ca.local(ev["inicio"]).date().isoformat())


@router.post("/painel/clinica/agenda/evento/{evento_id}/situacao")
def evento_situacao(request: Request, evento_id: int, nova: str = Form(""),
                    tratamento: str = Form(""), valor: str = Form(""), retorno: str = Form("")):
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return redir
    from finance.clinica_config import centavos
    valor_c = centavos(valor) if valor.strip() else None
    if valor.strip() and valor_c is None:
        return _ir(request, f"/painel/clinica/agenda/evento/{evento_id}",
                   erro="Valor inválido. Use o formato 1.500,00.")
    with get_pool().connection() as c:
        erro = ca.mudar_situacao(c, conta[0], evento_id, nova, tratamento=(tratamento or None),
                                 valor_centavos=valor_c, membro_id=request.session.get("membro_id"),
                                 retorno_dias=_int(retorno))
        (c.rollback if erro else c.commit)()
    if not erro and nova == "finalizado" and tratamento == "sim":
        # o médico propôs tratamento: a recepção monta o plano agora, com o paciente na frente
        return RedirectResponse(f"/painel/clinica/planos/novo?evento={evento_id}", status_code=303)
    return _ir(request, f"/painel/clinica/agenda/evento/{evento_id}", "" if erro else "situacao", erro or "")


@router.post("/painel/clinica/agenda/evento/{evento_id}/remarcar")
def evento_remarcar(request: Request, evento_id: int, inicio: str = Form("")):
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return redir
    quando = _instante(inicio)
    with get_pool().connection() as c:
        erro = ca.remarcar(c, conta[0], evento_id, quando, membro_id=request.session.get("membro_id"))             if quando else "Escolha o novo horário."
        (c.rollback if erro else c.commit)()
    return _ir(request, f"/painel/clinica/agenda/evento/{evento_id}", "" if erro else "remarcado", erro or "")


@router.post("/painel/clinica/agenda/evento/{evento_id}/mensagem")
def evento_mensagem(request: Request, evento_id: int, qual: str = Form("marcado")):
    conta, _g, redir = _acesso(request)
    if redir is not None:
        return redir
    conta_id = conta[0]
    with get_pool().connection() as c:
        ev = ca.evento(c, conta_id, evento_id)
        if not ev or ev["situacao"] in ("finalizado", "cancelou", "faltou"):
            return _ir(request, f"/painel/clinica/agenda/evento/{evento_id}", erro="Esse agendamento não recebe mensagem.")
        agora = datetime.now(timezone.utc)
        if qual == "vespera" and ev["confirmacao_enviada_em"] and \
                agora - ev["confirmacao_enviada_em"] < timedelta(minutes=5):
            # duplo clique (ou duas abas): o lembrete acabou de sair
            return _ir(request, f"/painel/clinica/agenda/evento/{evento_id}", "mensagem")
        promete = ca.config(c, conta_id)["confirmacao_modo"] == "ligado"
        texto = (ca.texto_vespera(c, conta_id, ev, agora) if qual == "vespera"
                 else ca.texto_marcado(c, conta_id, ev, promete))
        ok = ca.enviar(c, conta_id, ev, texto, autor="humano", membro_id=request.session.get("membro_id")).get("ok")
        if ok and qual == "vespera":
            c.execute("update eventos_agenda set confirmacao_enviada_em=now() where id=%s and conta_id=%s",
                      (evento_id, conta_id))
        c.commit()
    return _ir(request, f"/painel/clinica/agenda/evento/{evento_id}", "mensagem" if ok else "sem_mensagem")


@router.post("/painel/clinica/agenda/config")
def agenda_config(request: Request, modo: str = Form("off"), hora: str = Form("10")):
    conta, gerencia, redir = _acesso(request)
    if redir is not None:
        return redir
    if not gerencia:
        return _ir(request, "/painel/clinica/agenda", erro="Só o dono ou o gestor muda a confirmação.")
    with get_pool().connection() as c:
        erro = ca.salvar_config(c, conta[0], modo, _int(hora))
        (c.rollback if erro else c.commit)()
    return _ir(request, "/painel/clinica/agenda", "" if erro else "salvo", erro or "")


_CSS = r"""<style>
.ag-pag{width:100%;max-width:var(--pag,1180px);margin:0 auto;padding:1.2rem 1rem 2.5rem;box-sizing:border-box}
.ag-topo{display:flex;align-items:flex-end;justify-content:space-between;gap:.8rem;flex-wrap:wrap}
.ag-topo h2{margin:0;font-size:1.5rem}
.ag-topo .sub{color:var(--txt-mut);font-size:.86rem;margin-top:.2rem}
.ag-bt{display:inline-flex;align-items:center;min-height:40px;padding:0 .9rem;border-radius:8px;text-decoration:none;font-size:.9rem;background:var(--verde);color:var(--sobre-verde)}
.ag-bt.sec{background:transparent;border:1px solid var(--borda);color:var(--txt)}
.ag-nav{display:flex;gap:.4rem;align-items:center;flex-wrap:wrap;margin:1rem 0 .6rem}
.ag-nav .dia{font-weight:600;margin:0 .3rem}
.ag-seg{display:inline-flex;border:1px solid var(--borda);border-radius:8px;overflow:hidden}
.ag-seg a{padding:.4rem .8rem;font-size:.84rem;color:var(--txt-mut);text-decoration:none}
.ag-seg a.on{background:var(--neon-fundo);color:var(--txt)}
.ag-nav select{width:auto;min-height:38px;padding:.2rem .5rem;font-size:.86rem}
.ag-kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:.5rem;margin-bottom:.8rem}
.ag-kpi{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.5rem .7rem}
.ag-kpi b{display:block;font-size:1.3rem}.ag-kpi span{font-size:.74rem;color:var(--txt-mut)}
.ag-rolo{overflow-x:auto;border:1px solid var(--borda);border-radius:11px}
table.ag{border-collapse:collapse;width:100%;min-width:560px;font-size:.82rem}
table.ag th{position:sticky;top:0;background:var(--card);padding:.45rem .4rem;text-align:left;border-bottom:1px solid var(--borda);font-weight:600;white-space:nowrap}
table.ag th small{display:block;font-weight:400;color:var(--txt-mut);font-size:.7rem}
table.ag td{border-top:1px solid var(--borda);padding:2px 4px;vertical-align:top;height:34px}
table.ag td.h{width:52px;color:var(--txt-mut);font-variant-numeric:tabular-nums;white-space:nowrap}
td.fora{background:repeating-linear-gradient(135deg,transparent,transparent 5px,rgba(255,255,255,.02) 5px,rgba(255,255,255,.02) 10px)}
td.continua{background:rgba(255,255,255,.02)}
a.livre{display:block;height:100%;min-height:28px;border-radius:6px;color:var(--txt-mut);text-decoration:none;font-size:.74rem;padding:.25rem .4rem;border:1px dashed transparent}
a.livre:hover{border-color:var(--neon-borda);color:var(--verde-claro)}
a.ev{display:block;border-radius:7px;padding:.25rem .45rem;margin:1px 0;text-decoration:none;color:var(--txt);border:1px solid var(--borda);border-left:4px solid var(--borda)}
a.ev b{font-size:.8rem}a.ev span{display:block;font-size:.7rem;color:var(--txt-mut)}
.s-agendado{background:#122019}.s-confirmado{background:#10241A;border-color:#25D366!important}
.s-presente{background:#0D1B23;border-color:#229ED9!important}.s-atendimento{background:#241C0F;border-color:#E0A32E!important}
.s-finalizado{background:#121614;color:#8b9a92!important}.s-faltou{background:#241313;border-color:#E0574F!important}
.s-cancelou{background:#1a1414;text-decoration:line-through;color:#a07a77!important}
.ag-leg{display:flex;gap:.5rem;flex-wrap:wrap;font-size:.72rem;margin:.6rem 0;color:var(--txt-mut)}
.ag-leg i{display:inline-block;width:10px;height:10px;border-radius:3px;margin-right:.25rem;vertical-align:-1px;border:1px solid var(--borda)}
.ag-caixa{background:var(--card);border:1px solid var(--borda);border-radius:11px;padding:.7rem .9rem;margin-top:.9rem}
.ag-caixa.alerta{background:var(--ambar-fundo);border-color:var(--ambar-borda)}
.ag-form{display:grid;grid-template-columns:1fr 1fr;gap:.6rem .8rem}
.ag-form label{display:flex;flex-direction:column;gap:.2rem;font-size:.76rem;color:var(--txt-mut)}
.ag-form .inteira{grid-column:1/-1}
.ag-form input[type=radio],.ag-form input[type=checkbox]{width:auto;min-height:0;margin:0}
.ag-ops{display:flex;flex-wrap:wrap;gap:.35rem}
.ag-ops label{flex-direction:row;align-items:center;gap:.35rem;border:1px solid var(--borda);border-radius:8px;padding:.35rem .6rem;color:var(--txt);font-size:.84rem}
.ag-form button,.ag-acoes button{width:auto;margin:0;min-height:42px;padding:.45rem 1rem;font-size:.9rem}
.ag-acoes{display:flex;gap:.4rem;flex-wrap:wrap;align-items:center;margin-top:.6rem}
.ag-acoes form{margin:0}
button.sec{background:transparent;border:1px solid var(--borda);color:var(--txt)}
.ag-msg{font-size:.84rem;padding:.45rem .6rem;border-radius:9px;background:var(--neon-fundo);border:1px solid var(--neon-borda);margin-top:.35rem}
.mut{color:var(--txt-mut);font-size:.8rem}
@media (max-width:620px){.ag-form{grid-template-columns:1fr}}
</style>
<script>
/* o segundo clique não manda de novo: o botão trava assim que o formulário sai */
document.addEventListener('submit', function(ev){
  var b = ev.submitter; if(!b || b.value === 'buscar') return;
  setTimeout(function(){ ev.target.querySelectorAll('button').forEach(function(x){ x.disabled = true; }); }, 0);
});
</script>"""

_TPL = r"""{% extends "base" %}{% block conteudo %}""" + _CSS + r"""
<div class="ag-pag">
  <div class="ag-topo"><div><h2>Agenda</h2>
    <div class="sub">{% if vista == 'dia' %}Uma coluna por profissional. Clique num horário livre para agendar e num agendamento para mudar o status.{% else %}A semana de um profissional, dia a dia.{% endif %}</div></div>
    <div style="display:flex;gap:.4rem;flex-wrap:wrap">
      <a class="ag-bt sec" href="/painel/clinica/vagas">⚡ Vagas liberadas{% if vagas_esperando %} ({{ vagas_esperando }}){% endif %}</a>
      <a class="ag-bt sec" href="/painel/clinica/pacotes">Pacotes e retornos</a>
      <a class="ag-bt" href="/painel/clinica/agenda/novo?data={{ data_iso }}">+ Agendar</a></div></div>
  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}
  {% if erro %}<div class="erro" style="margin-top:.8rem">{{ erro }}</div>{% endif %}

  <div class="ag-nav">
    <a class="ag-bt sec" href="{{ link_ant }}" aria-label="Anterior">‹</a>
    <span class="dia">{{ titulo_data }}</span>
    <a class="ag-bt sec" href="{{ link_prox }}" aria-label="Próximo">›</a>
    <a class="ag-bt sec" href="{{ link_hoje }}">Hoje</a>
    <span class="ag-seg"><a href="{{ link_dia }}" class="{% if vista == 'dia' %}on{% endif %}">Dia</a><a href="{{ link_semana }}" class="{% if vista == 'semana' %}on{% endif %}">Semana</a></span>
    {% if vista == 'dia' and locais|length > 1 %}
    <form method="get" action="/painel/clinica/agenda" style="margin:0"><input type="hidden" name="data" value="{{ data_iso }}">
      <select name="local" onchange="this.form.submit()" aria-label="Local"><option value="">Todos os locais</option>{% for l in locais %}<option value="{{ l.id }}" {% if local_id == l.id %}selected{% endif %}>{{ l.nome }}</option>{% endfor %}</select></form>
    {% endif %}
    {% if vista == 'semana' %}
    <form method="get" action="/painel/clinica/agenda" style="margin:0"><input type="hidden" name="vista" value="semana"><input type="hidden" name="data" value="{{ data_iso }}">
      <select name="prof" onchange="this.form.submit()" aria-label="Profissional">{% for p in profs %}<option value="{{ p.id }}" {% if prof_id == p.id %}selected{% endif %}>{{ p.nome }}</option>{% endfor %}</select></form>
    {% endif %}
  </div>

  <div class="ag-kpis">
    <div class="ag-kpi"><b>{{ d.ocupacao if d.ocupacao is not none else '—' }}{% if d.ocupacao is not none %}%{% endif %}</b><span>ocupação {{ 'do dia' if vista == 'dia' else 'da semana' }}</span></div>
    {% if vista == 'dia' %}<div class="ag-kpi"><b>{{ d.confirmados }} de {{ d.a_confirmar }}</b><span>confirmados (ou já chegaram)</span></div>
    {% else %}<div class="ag-kpi"><b>{{ d.livres }}</b><span>horários livres de meia hora</span></div>{% endif %}
    <div class="ag-kpi"><b>{{ d.faltas }}</b><span>faltas</span></div>
  </div>

  {% if vista == 'dia' and d.remarcar %}
  <div class="ag-caixa alerta"><b>Pediram para remarcar</b> (responderam 2 no lembrete)
    {% for e in d.remarcar %}<div><a href="/painel/clinica/agenda/evento/{{ e.id }}">{{ e.paciente }} · {{ e.dia }} {{ e.hora }} · {{ prof_nome.get(e.profissional_id, '') }}</a></div>{% endfor %}</div>
  {% endif %}

  {% if not d.colunas %}
  <div class="ag-caixa">Ninguém atende {{ 'neste dia' if vista == 'dia' else 'nesta semana' }}. {% if gerencia %}A grade de cada profissional fica em <a href="/painel/clinica/configurar?aba=grade">Clínica › Grade</a>.{% endif %}</div>
  {% else %}
  <div class="ag-rolo"><table class="ag">
    <thead><tr><th></th>{% for col in d.colunas %}<th>{% if vista == 'dia' %}<span style="color:{{ col.prof.cor }}">●</span> {{ col.prof.nome }}<small>{{ col.prof.funcao }}</small>{% else %}{{ col.rotulo }}{% if col.hoje %} · hoje{% endif %}<small>{% if col.ocupacao is not none %}{{ col.ocupacao }}% ocupado{% else %}não atende{% endif %}</small>{% endif %}</th>{% endfor %}</tr></thead>
    <tbody>{% for h in d.linhas %}{% set i = loop.index0 %}<tr><td class="h">{{ '%02d:%02d'|format(h.hour, h.minute) }}</td>
      {% for col in d.colunas %}{% set cel = col.celulas[i] %}
        {% if cel.tipo == 'ev' %}<td>{% for e in cel.evs %}<a class="ev s-{{ e.situacao }}" style="border-left-color:{{ e.cor }}" href="/painel/clinica/agenda/evento/{{ e.id }}"><b>{{ e.paciente }}</b><span>{{ e.tipo }} · {{ e.hora }}–{{ e.fim_txt }}{% if e.encaixe %} · encaixe{% endif %}</span><span>{{ SIT_D[e.situacao] }}{% if e.pede_remarcar_em %} · quer remarcar{% endif %}</span></a>{% endfor %}</td>
        {% elif cel.tipo == 'livre' and ((vista == 'dia' and col.marca) or (vista == 'semana' and prof_marca)) %}<td><a class="livre" href="/painel/clinica/agenda/novo?prof={{ col.prof.id if vista == 'dia' else prof_id }}&data={{ (col.dia if vista == 'semana' else d.data).isoformat() }}&hora={{ '%02d:%02d'|format(h.hour, h.minute) }}">+ livre</a></td>
        {% elif cel.tipo == 'livre' %}<td class="continua"></td>
        {% elif cel.tipo == 'continua' %}<td class="continua"></td>
        {% elif cel.tipo == 'passou' %}<td class="continua"></td>
        {% else %}<td class="fora"></td>{% endif %}
      {% endfor %}</tr>{% endfor %}</tbody></table></div>
  <div class="ag-leg">{% for k, rot in SIT %}<span><i class="s-{{ k }}"></i>{{ rot }}</span>{% endfor %}</div>
  {% endif %}

  {% if gerencia %}
  <form class="ag-caixa" method="post" action="/painel/clinica/agenda/config">
    <b>Confirmação na véspera</b>
    <div class="mut" style="margin:.2rem 0 .5rem">Na véspera, quem tem horário recebe no WhatsApp: "Amanhã você tem consulta às 09:00 com o Dr. Manoel… Responda 1 para confirmar ou 2 se precisar remarcar." Quem responde 1 fica Confirmado sozinho; quem responde 2 aparece aqui em cima. A mensagem nunca diz o procedimento.</div>
    <div class="ag-form">
      <label>Modo<select name="modo"><option value="off" {% if cfg.confirmacao_modo == 'off' %}selected{% endif %}>Desligada</option><option value="ligado" {% if cfg.confirmacao_modo == 'ligado' %}selected{% endif %}>Ligada — o Zaq manda sozinho</option></select></label>
      <label>A partir de que hora (no horário de atendimento)<select name="hora">{% for hh in range(7, 19) %}<option value="{{ hh }}" {% if cfg.confirmacao_hora == hh %}selected{% endif %}>{{ hh }}h</option>{% endfor %}</select></label>
    </div>
    <div class="ag-acoes"><button>Salvar</button></div>
  </form>
  {% endif %}
</div>
{% endblock %}"""

_TPL_NOVO = r"""{% extends "base" %}{% block conteudo %}""" + _CSS + r"""
<div class="ag-pag">
  <div class="ag-topo"><div><h2>Novo agendamento</h2><div class="sub">Só o que a recepção preenche. O fim, o valor e o local se calculam.</div></div>
    <a class="ag-bt sec" href="/painel/clinica/agenda?data={{ data_iso }}">Voltar à agenda</a></div>
  {% if erro %}<div class="erro" style="margin-top:.8rem">{{ erro }}</div>{% endif %}
  {% if not profs %}
  <div class="ag-caixa">Ninguém com atendimento cadastrado ainda. Cadastre em Clínica › Profissionais.</div>
  {% else %}
  <form class="ag-caixa ag-form" method="get" action="/painel/clinica/agenda/novo">
    <input type="hidden" name="data" value="{{ data_iso }}"><input type="hidden" name="hora" value="{{ hora }}">
    <label>Profissional<select name="prof" onchange="this.form.submit()">{% for p in profs %}<option value="{{ p.id }}" {% if prof and prof.id == p.id %}selected{% endif %}>{{ p.nome }}</option>{% endfor %}</select></label>
    <label>Atendimento<select name="tipo" onchange="this.form.submit()">{% for t in tipos_prof %}<option value="{{ t.id }}" {% if tipo and tipo.id == t.id %}selected{% endif %}>{{ t.nome }} · {{ t.duracao_min }} min · {{ t.preco }}</option>{% endfor %}</select></label>
    <noscript><button class="sec">Ver horários</button></noscript>
  </form>

  <form class="ag-caixa ag-form" method="post" action="/painel/clinica/agenda/novo">
    <input type="hidden" name="prof" value="{{ prof.id if prof else '' }}"><input type="hidden" name="tipo" value="{{ tipo.id if tipo else '' }}">
    <input type="hidden" name="data" value="{{ data_iso }}">
    <div class="inteira"><span class="mut">Horário{% if tipo %} ({{ tipo.duracao_min }} min){% endif %}</span>
      <div class="ag-ops" style="margin-top:.3rem">
        {% if pedido %}<label><input type="radio" name="inicio" value="enc|{{ pedido }}" {% if not form.inicio or form.inicio.startswith('enc|') %}checked{% endif %}> {{ pedido_txt }} · <b>encaixe</b></label>{% endif %}
        {% for o in opcoes %}<label><input type="radio" name="inicio" value="{{ o.valor }}" {% if form.inicio == o.valor or (o.on and not pedido and not form.inicio) %}checked{% endif %}> {{ o.txt }}</label>{% else %}{% if not pedido %}<span class="mut">Nenhum horário livre nos próximos 14 dias. Confira a grade em Clínica › Grade.</span>{% endif %}{% endfor %}
      </div>
      {% if pedido %}<div class="mut" style="margin-top:.3rem">O horário que você clicou está ocupado: só vai como encaixe se você deixar esse marcado, e se ainda houver encaixe no dia.</div>{% endif %}
    </div>
    <label class="inteira">Paciente: buscar por nome ou telefone<input name="busca" value="{{ busca }}" placeholder="Maria, 99 98888-7777" autocomplete="off"></label>
    <div class="ag-acoes inteira" style="margin-top:0"><button class="sec" name="acao" value="buscar" formnovalidate>Buscar</button></div>
    <div class="inteira"><span class="mut">Paciente{% if achados %} (escolha um, ou "Paciente novo"){% endif %}</span>
      <div class="ag-ops" style="margin-top:.3rem">
        {% for a in achados %}<label><input type="radio" name="lead_id" value="{{ a.id }}" {% if lead_escolhido == a.id %}checked{% endif %}> {{ a.nome }}{% if a.fone %} · {{ a.fone }}{% endif %}</label>{% endfor %}
        <label><input type="radio" name="lead_id" value="" {% if not achados or (not lead_escolhido and form.nome) %}checked{% endif %}> Paciente novo</label>
      </div></div>
    <label>Nome (paciente novo)<input name="nome" maxlength="120" autocomplete="off" value="{{ form.nome or '' }}"></label>
    <label>Celular com DDD (paciente novo)<input name="fone" inputmode="tel" maxlength="20" autocomplete="off" placeholder="(99) 9 8888-7777" value="{{ form.fone or '' }}"></label>
    <div class="inteira"><span class="mut">Como conheceu</span><div class="ag-ops" style="margin-top:.3rem">{% for o in ORIGENS %}<label><input type="radio" name="origem" value="{{ o }}" {% if form.origem == o %}checked{% endif %}> {{ o }}</label>{% endfor %}</div></div>
    <label class="inteira">Observação para a recepção (nunca vai pro paciente)<input name="observacao" maxlength="500" value="{{ form.observacao or '' }}"></label>
    <div class="ag-acoes inteira"><button name="acao" value="agendar">Agendar</button><button class="sec" name="acao" value="confirmar">Agendar e mandar confirmação</button></div>
    <div class="mut inteira">O Zaq guarda só nome, celular, atendimento, horário e origem. Nada de queixa ou diagnóstico.</div>
  </form>
  {% endif %}
</div>
{% endblock %}"""

_TPL_EVENTO = r"""{% extends "base" %}{% block conteudo %}""" + _CSS + r"""
<div class="ag-pag">
  <div class="ag-topo"><div><h2>{{ ev.paciente }}</h2><div class="sub">{{ ev.tipo }} · {{ quando }}{% if prof %} · {{ prof.nome }}{% endif %}{% if ev.encaixe %} · encaixe{% endif %}</div></div>
    <a class="ag-bt sec" href="/painel/clinica/agenda?data={{ data_iso }}">Voltar à agenda</a></div>
  {% if aviso %}<div class="ok" style="margin-top:.8rem">{{ aviso }}</div>{% endif %}
  {% if erro %}<div class="erro" style="margin-top:.8rem">{{ erro }}</div>{% endif %}
  <div class="ag-caixa">
    <div><b>Status:</b> <span class="ev s-{{ ev.situacao }}" style="display:inline-block;padding:.1rem .5rem;border-radius:6px;border:1px solid var(--borda)">{{ SIT_D[ev.situacao] }}</span>
      {% if ev.pede_remarcar_em %} · <b>pediu para remarcar</b>{% endif %}
      {% if ev.confirmado_em %} · confirmou{% elif ev.confirmacao_enviada_em %} · lembrete da véspera enviado{% endif %}</div>
    <div class="mut" style="margin-top:.3rem">{% if ev.fone %}Celular {{ ev.fone }}{% endif %}{% if ev.origem %} · veio por {{ ev.origem }}{% endif %}{% if ev.marcado_por == 'ia' %} · marcado pelo agente no WhatsApp{% elif ev.marcado_por == 'vaga' %} · veio de vaga liberada{% endif %}{% if ev.observacao %} · {{ ev.observacao }}{% endif %}</div>
    {% if proximos %}<div class="ag-acoes">{% for s in proximos if s != 'finalizado' %}
      <form method="post" action="/painel/clinica/agenda/evento/{{ ev.id }}/situacao"><input type="hidden" name="nova" value="{{ s }}"><button class="{% if s in ('faltou','cancelou') %}sec{% endif %}">{{ {'agendado':'Reabrir','confirmado':'Confirmar','presente':'Chegou','atendimento':'Entrou no atendimento','faltou':'Faltou','cancelou':'Cancelar'}[s] }}</button></form>
    {% endfor %}</div>
    {% if 'finalizado' in proximos %}
    <form class="ag-form" method="post" action="/painel/clinica/agenda/evento/{{ ev.id }}/situacao" style="margin-top:.7rem">
      <input type="hidden" name="nova" value="finalizado">
      {% if assin_vai %}
      <div class="inteira"><span class="mut">Assinante {{ assin_vai.plano }}: finalizar usa a sessão inclusa do mês ({{ assin_vai.usadas + 1 }} de {{ assin_vai.sessoes }}); o pacote não baixa.</span></div>
      {% endif %}
      {% if pacote_vai %}
      <div class="inteira"><span class="mut">Sessão do pacote: finalizar baixa 1 do saldo ({{ pacote_vai.nome }}, sessão {{ pacote_vai.usadas + 1 }} de {{ pacote_vai.total }}).</span></div>
      {% else %}
      <div class="inteira"><span class="mut">O médico propôs tratamento? (o card do paciente anda no funil com a resposta)</span>
        <div class="ag-ops" style="margin-top:.3rem">
          <label><input type="radio" name="tratamento" value="nao" required> Não — {{ 'Fechado' }}</label>
          <label><input type="radio" name="tratamento" value="sim" required> Sim — Plano de tratamento</label></div></div>
      <label>Valor proposto (se souber)<input name="valor" inputmode="decimal" placeholder="1.500,00"></label>
      {% endif %}
      <label>O médico pediu retorno em quantos dias? (vazio: não pediu)<input name="retorno" inputmode="numeric" value="{{ '' if pacote_vai else volta_padrao }}"></label>
      <div class="ag-acoes inteira"><button>Finalizar</button></div>
    </form>
    {% endif %}{% endif %}
    {% if ev.situacao == 'finalizado' %}
    {% if pacote_feito %}<div class="ok" style="margin-top:.7rem">Sessão {{ pacote_feito.usadas }} de {{ pacote_feito.total }} baixada ({{ pacote_feito.nome }}).{% if pacote_feito.saldo %} Faltam {{ pacote_feito.saldo }}; a próxima fica boa a partir de {{ pacote_feito.proxima.strftime('%d/%m') }} (intervalo de {{ pacote_feito.intervalo }} dias).{% else %} Pacote concluído.{% endif %}</div>{% endif %}
    {% if assin_feito %}<div class="ok" style="margin-top:.7rem">Sessão inclusa na assinatura {{ assin_feito }} usada; o pacote não baixou.</div>{% endif %}
    {% if retorno %}<div class="mut" style="margin-top:.4rem">Retorno pedido até {{ retorno.vence.strftime('%d/%m/%Y') }}{% if retorno.estado == 'marcado' %} · já marcado{% elif pac_cfg.lembretes == 'ligado' %} · o Zaq chama o paciente {{ pac_cfg.retorno_aviso_dias }} dias antes{% else %} · os lembretes estão desligados: a recepção chama{% endif %}.</div>{% endif %}
    <div class="ag-acoes" style="margin-top:.7rem">
      {% if pacote_feito and pacote_feito.saldo %}<a class="ag-bt" href="/painel/clinica/agenda/novo?prof={{ ev.profissional_id }}&tipo={{ ev.servico_id }}&data={{ pacote_feito.proxima.isoformat() }}&lead={{ ev.lead or '' }}">Marcar a {{ pacote_feito.proxima_n }}ª sessão</a>{% endif %}
      <a class="ag-bt sec" href="/painel/clinica/planos/novo?evento={{ ev.id }}">Plano de tratamento</a></div>
    {% endif %}
  </div>

  {% if remarcar %}
  <form class="ag-caixa ag-form" method="post" action="/painel/clinica/agenda/evento/{{ ev.id }}/remarcar">
    <div class="inteira"><b>Remarcar</b><div class="ag-ops" style="margin-top:.4rem">{% for o in remarcar %}<label><input type="radio" name="inicio" value="{{ o.valor }}" {% if loop.first %}checked{% endif %}> {{ o.txt }}</label>{% endfor %}</div></div>
    <div class="ag-acoes inteira"><button>Remarcar para este horário</button></div>
  </form>
  {% endif %}

  {% if prod %}
  <div class="ag-caixa"><b>Produto</b>
    {% for v in prod.vendas %}<div class="ok" style="margin-top:.4rem">Vendido: {{ v.nome }} × {{ v.qtd }} · {{ v.valor }}{% if v.recompra_em %} · reposição lembrada em {{ v.recompra_em.strftime('%d/%m') }}{% endif %}</div>{% endfor %}
    {% for r in prod.sugestoes.recompra %}<div class="mut" style="margin-top:.4rem">Reposição: {{ r.produto }} (levou em {{ r.comprado_em.strftime('%d/%m') }}, deve acabar {{ r.recompra_em.strftime('%d/%m') }}).</div>{% endfor %}
    {% for v in prod.sugestoes.vencendo %}<div class="mut" style="margin-top:.2rem">Na prateleira, vence logo: {{ v.nome }} ({{ v.validade.strftime('%d/%m') }}, {{ v.quantidade }} no lote).</div>{% endfor %}
    {% if prod.lista %}
    <form class="ag-form" method="post" action="/painel/clinica/produtos/vender" style="margin-top:.6rem">
      <input type="hidden" name="evento_id" value="{{ ev.id }}">
      <label>Produto<select name="produto_id">{% for p in prod.lista %}<option value="{{ p.id }}">{{ p.nome }} · {{ p.preco }}</option>{% endfor %}</select></label>
      <label>Quantidade<input name="quantidade" value="1" inputmode="decimal"></label>
      <label>Pagamento<select name="pagamento">{% for k, v in prod.pagamentos.items() %}<option value="{{ k }}">{{ v }}</option>{% endfor %}</select></label>
      <div class="ag-acoes inteira"><button class="sec">Vender</button><span class="mut">assinante leva o desconto do plano sozinho</span></div>
    </form>
    {% else %}<div class="mut" style="margin-top:.4rem">Nenhum produto com estoque e preço. <a href="/painel/clinica/produtos">Produtos da clínica</a></div>{% endif %}
  </div>
  {% endif %}

  {% if ev.situacao in ('agendado','confirmado') %}
  <div class="ag-caixa"><b>Mensagens pro paciente</b>
    <div class="ag-msg">{{ msg_marcado }}</div>
    <div class="ag-acoes"><form method="post" action="/painel/clinica/agenda/evento/{{ ev.id }}/mensagem"><input type="hidden" name="qual" value="marcado"><button class="sec">Mandar esta</button></form></div>
    <div class="ag-msg" style="margin-top:.7rem">{{ msg_vespera }}</div>
    <div class="ag-acoes"><form method="post" action="/painel/clinica/agenda/evento/{{ ev.id }}/mensagem"><input type="hidden" name="qual" value="vespera"><button class="sec">Mandar o lembrete agora</button></form>
      {% if conversa %}<a class="ag-bt sec" href="/painel/prospeccao/comunicacao?abrir={{ conversa }}">Abrir conversa</a>{% endif %}</div>
  </div>
  {% endif %}
</div>
{% endblock %}"""

# ".html": o `_env` liga o autoescape pela extensão (nome e mensagem vêm do WhatsApp)
_env.loader.mapping["clinica_agenda.html"] = _TPL
_env.loader.mapping["clinica_agenda_novo.html"] = _TPL_NOVO
_env.loader.mapping["clinica_agenda_evento.html"] = _TPL_EVENTO
