"""Aba "Agenda" do painel — calendário do mês, próximos compromissos, novo
compromisso, lembrete (opt-in) e sincronização com outras agendas via .ics.

Agenda PRÓPRIA do Zaq (a mesma que o chat usa em finance/agenda.py). Escopo
multi-tenant sagrado: toda query filtra por conta[0]. O feed .ics é público por
token (rota /agenda/<token>.ics, fora do gate do painel) — o segredo é o token.

Reusa o motor do portal: _render/_env (base, nav) + conta_logada.
"""
import calendar as _cal
import logging
from datetime import date, datetime, timedelta
from urllib.parse import quote

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse, Response

from db.conexao import get_pool
from web import estaticos as _estaticos
from web import tema as _tema
from finance import agenda as ag
from finance import convites as cv
from web.portal import _env, _render, conta_logada

router = APIRouter()
_log_ag = logging.getLogger("painel.agenda")

MESES = ["", "janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
         "agosto", "setembro", "outubro", "novembro", "dezembro"]
DIAS_SEM = ["Dom", "Seg", "Ter", "Qua", "Qui", "Sex", "Sáb"]
DIAS_SEM_EXT = ["Domingo", "Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado"]
TIPO_ROT = {"pessoal": "Pessoal", "empresa": "Empresa", "fornecedor": "Fornecedor"}


def _acesso(request: Request):
    """Qualquer membro logado vê a agenda da sua conta."""
    conta = conta_logada(request)
    if conta is None:
        return None, RedirectResponse("/login", status_code=303)
    ctx = {"conta": conta, "conta_id": conta[0],
           "membro_id": request.session.get("membro_id")}
    return ctx, None


def _mes_ref(m: str) -> tuple[int, int]:
    """'2026-07' -> (2026, 7). Inválido/vazio -> mês atual (Brasília)."""
    hoje = ag.agora_brt()
    try:
        ano, mes = (m or "").split("-")
        ano, mes = int(ano), int(mes)
        if 1 <= mes <= 12 and 2000 <= ano <= 2100:
            return ano, mes
    except (ValueError, AttributeError):
        pass
    return hoje.year, hoje.month


def _vizinho(ano: int, mes: int, delta: int) -> str:
    """Mês anterior/seguinte no formato YYYY-MM."""
    idx = (ano * 12 + (mes - 1)) + delta
    return f"{idx // 12:04d}-{idx % 12 + 1:02d}"


def _prazo(ev: dict, agora) -> dict:
    """Quanto falta pro prazo da data segurada vencer, em texto curto e em horas.

    O texto vai na LINHA do calendário (é o que faz o prazo aparecer sem abrir
    nada) e as horas decidem a cor: abaixo de 24h a data vira urgência, não aviso.
    Compromisso firme devolve vazio — não tem prazo correndo."""
    ate = ev.get("pre_reserva_ate")
    if ev.get("status") != ag.PRE_RESERVADO or not ate:
        return {"rot": "", "horas": None, "urgente": False}
    horas = (ate - agora).total_seconds() / 3600
    if horas <= 0:
        rot = "vencido"
    elif horas < 24:
        rot = f"{max(1, int(horas))}h"
    else:
        rot = f"{int(horas // 24)}d"
    return {"rot": rot, "horas": horas, "urgente": horas < 24}


def _ficha_rot(f: dict | None) -> dict | None:
    """A ficha do evento já formatada pro JS — valores em texto, nada de centavos
    crus na tela. None quando o compromisso não veio de orçamento nenhum (marcado
    na mão, ou data segurada por telefonema): aí não há ficha pra mostrar."""
    if not f:
        return None
    prox = f.get("proxima") or None
    conv = f.get("convidados")
    try:
        conv = int(conv) if conv not in (None, "") else None
    except (TypeError, ValueError):
        conv = None
    return {
        "orcamento_id": f["orcamento_id"], "numero": f.get("numero"),
        "status": f.get("status") or "",
        "fechado": (f.get("status") or "") == "fechado",
        "cliente": f.get("cliente") or "", "contato": f.get("contato") or "",
        "tipo": f.get("tipo") or "", "convidados": conv,
        "itens": f.get("itens") or [],
        "total": _brl(f.get("total_centavos")),
        "tem_titulos": bool(f.get("tem_titulos")),
        "pct": f.get("pct"),
        "pago": _brl(f.get("pago_centavos")),
        "cobrado": _brl(f.get("titulos_centavos")),
        "plano": _brl(f.get("plano_centavos")),
        "vencidas": int(f.get("vencidas") or 0),
        "vencidas_valor": _brl(f.get("vencidas_centavos")),
        "prox_valor": _brl(prox["valor_centavos"]) if prox else "",
        "prox_venc": prox["vencimento"].strftime("%d/%m") if prox and prox.get("vencimento") else "",
        "prox_vencida": bool(prox and prox.get("vencida")),
        "prox_dias": ((prox["vencimento"] - date.today()).days
                      if prox and prox.get("vencimento") else None),
    }


def _pg_estado(ficha: dict | None) -> dict:
    """Como o pagamento daquela festa aparece NA LINHA do calendário.

    Num buffet, "quanto já entrou" é tão parte da data quanto o horário — duas
    festas no mesmo mês, uma quitada e outra sem um centavo, hoje parecem idênticas.
    Rótulo curto e uma cor:
      • verde  — quitado;
      • coral  — tem parcela vencida (é o que exige ação);
      • âmbar  — recebido em parte, nada vencido.
    Sem título nenhum (contrato não fechado) não mostra número: não há o que medir.
    """
    if not ficha or ficha.get("pct") is None:
        return {"rot": "", "classe": ""}
    pct = int(ficha["pct"])
    if ficha.get("vencidas"):
        classe = "bad"
    elif pct >= 100:
        classe = "ok"
    else:
        classe = "mid"
    return {"rot": f"{pct}%", "classe": classe}


def _monta_semanas(ano: int, mes: int, eventos: list[dict], hoje: date,
                   agora=None, fichas: dict | None = None) -> list[list[dict]]:
    """Grade do mês (semanas de Dom a Sáb). Cada célula traz seus eventos."""
    agora = agora or ag.agora_brt()
    fichas = fichas or {}
    por_dia: dict[date, list[dict]] = {}
    for ev in eventos:
        d = ev["inicio"].astimezone(ag.BRT).date()
        por_dia.setdefault(d, []).append(ev)
    cal = _cal.Calendar(firstweekday=6)   # 6 = domingo
    semanas = []
    for semana in cal.monthdatescalendar(ano, mes):
        linha = []
        for d in semana:
            evs = por_dia.get(d, [])
            # `pre` = data SEGURADA esperando o sinal, não compromisso. Aparece no
            # calendário (é ela que impede vender a data duas vezes) com a marca de
            # estado própria, e fica fora dos "Próximos" — ag.proximos só traz 'ativo'.
            linhas_ev = []
            for e in evs:
                pz = _prazo(e, agora)
                pg = _pg_estado(fichas.get(e["id"]))
                linhas_ev.append({
                    "id": e["id"], "titulo": e["titulo"], "tipo": e["tipo"],
                    "hora": e["inicio"].astimezone(ag.BRT).strftime("%H:%M"),
                    "pre": e.get("status") == ag.PRE_RESERVADO,
                    "prazo": pz["rot"], "urgente": pz["urgente"],
                    "pg": pg["rot"], "pg_classe": pg["classe"],
                    # quem marcou: é por ele que o filtro por pessoa esconde e mostra
                    # sem voltar ao servidor (ver o JS do .ag-pessoas).
                    "membro_id": e.get("membro_id") or "",
                })
            # a célula inteira se pinta: é o que se enxerga do mês sem ler linha
            # nenhuma — âmbar quando tem data segurada, coral quando alguma aperta.
            linha.append({
                "dia": d.day, "fora": d.month != mes, "hoje": d == hoje,
                "iso": d.isoformat(), "eventos": linhas_ev,
                "tem_seg": any(x["pre"] for x in linhas_ev),
                "urg": any(x["pre"] and x["urgente"] for x in linhas_ev),
            })
        semanas.append(linha)
    return semanas


def _titulo_dia(d: date) -> str:
    """'Terça, 4 de agosto' — pro cabeçalho da caixa do dia."""
    idx = (d.weekday() + 1) % 7   # Calendar usa firstweekday=6 (domingo) -> 0=Dom
    return f"{DIAS_SEM_EXT[idx]}, {d.day} de {MESES[d.month]}"


def _eventos_por_dia(eventos: list[dict], convidados: dict[int, list[dict]] | None = None,
                     agora=None, orcamentos: dict[int, dict] | None = None,
                     fichas: dict[int, dict] | None = None,
                     nomes: dict[int, str] | None = None) -> dict[str, dict]:
    """{iso_do_dia: {titulo, eventos:[...]}} com os detalhes completos (local,
    descrição, convidados) — alimenta a caixa do dia no JS sem precisar de outra
    requisição (os eventos do mês já vieram pro calendário)."""
    convidados = convidados or {}
    orcamentos = orcamentos or {}
    fichas = fichas or {}
    nomes = nomes or {}
    agora = agora or ag.agora_brt()
    out: dict[str, dict] = {}
    for e in sorted(eventos, key=lambda ev: ev["inicio"]):
        d = e["inicio"].astimezone(ag.BRT).date()
        iso = d.isoformat()
        bucket = out.setdefault(iso, {"titulo": _titulo_dia(d), "eventos": []})
        conv_lista = []
        for g in convidados.get(e["id"], []):
            texto = f"Oi{(' ' + g['nome']) if g.get('nome') else ''}! Sobre o compromisso \"{e['titulo']}\"…"
            conv_lista.append({"nome": g.get("nome") or "", "contato": g.get("contato") or "",
                               "status": g["status"], "status_rot": g["status_rot"],
                               "wa": _wa_share(g["contato"], texto) if g.get("contato") else ""})
        bucket["eventos"].append({
            "id": e["id"], "hora": e["inicio"].astimezone(ag.BRT).strftime("%H:%M"),
            "titulo": e["titulo"], "tipo": e["tipo"], "tipo_rot": TIPO_ROT.get(e["tipo"], "Pessoal"),
            "local": e.get("local") or "", "descricao": e.get("descricao") or "",
            "convidados": conv_lista, "inicio_iso": e["inicio"].isoformat(),
            "data_iso": iso, "desfecho": e.get("desfecho"),
            "link_online": e.get("link_online") or "",
            "pre": e.get("status") == ag.PRE_RESERVADO,
            "pre_ate": (e["pre_reserva_ate"].astimezone(ag.BRT).strftime("%d/%m %H:%M")
                        if e.get("pre_reserva_ate") else ""),
            "prazo": _prazo(e, agora)["rot"],
            "urgente": _prazo(e, agora)["urgente"],
            # sinal e orçamento só existem na data segurada — são eles que ligam os
            # botões "Sinal recebido" e "Ver orçamento" da caixa do dia.
            "sinal": _brl(orcamentos.get(e["id"], {}).get("sinal_centavos")
                          or e.get("sinal_centavos")),
            "orcamento_id": orcamentos.get(e["id"], {}).get("orcamento_id"),
            "orcamento_numero": orcamentos.get(e["id"], {}).get("orcamento_numero"),
            # a FICHA: o que o orçamento vinculado já sabe sobre essa festa
            "ficha": _ficha_rot(fichas.get(e["id"])),
            # quem marcou — a agenda agora é de todos, e esta é a primeira pergunta
            # de quem olha um compromisso que não marcou. Vazio = o dono titular.
            "autor": nomes.get(e.get("membro_id")) or "",
            "membro_id": e.get("membro_id") or "",
        })
    return out


def _feed_url(request: Request, token: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/agenda/{token}.ics"


def _convite_url(request: Request, token: str) -> str:
    base = str(request.base_url).rstrip("/")
    return f"{base}/convite/{token}"


def _so_digitos(s: str) -> str:
    return "".join(ch for ch in (s or "") if ch.isdigit())


# VOCABULÁRIO DA TELA, por nicho. Quem vende data não marca "compromisso" — marca
# EVENTO, e as palavras dos campos são outras: "O que é / Ex: reunião com o
# contador" não é a pergunta que um buffet faz. Mesmo formulário, mesma rota, mesmo
# dado no banco; muda só o que a pessoa lê. Fora do nicho, nada disso aparece.
_ROT_PADRAO = {
    "novo": "Novo compromisso", "novo_btn": "＋ Novo compromisso",
    "titulo": "O que é", "titulo_ph": "Ex: reunião com o contador",
    "data": "Data", "hora": "Hora", "fim": "",
    "desc": "Descrição", "desc_ph": "Detalhes do compromisso…",
    "local": "Local", "tipo": "Tipo",
    "t_pessoal": "Pessoal", "t_empresa": "Empresa", "t_fornecedor": "Fornecedor",
    "conv_t": "👥 Envolvidos (opcional)",
    "conv_d": "Um ou vários. Cada um recebe o próprio link — e a mensagem já cita quem mais vem.",
    "conv_add": "+ adicionar envolvido",
    "salvar": "Marcar", "salvando": "⏳ Marcando…",
    "proximos": "Próximos compromissos",
    "cta_dia": "＋ Marcar novo compromisso nesse dia",
}
_ROT_EVENTO = dict(_ROT_PADRAO, **{
    "novo": "Novo evento", "novo_btn": "＋ Novo evento",
    # "O que é" vira o nome que o buffet dá pra festa — é assim que ela é chamada
    # no grupo, na cozinha e no dia.
    "titulo": "Evento", "titulo_ph": "Ex: Casamento — Ana e Pedro",
    "data": "Data da festa", "hora": "Começa", "fim": "Encerra",
    "desc": "Observações", "desc_ph": "O que a equipe precisa saber…",
    "local": "Onde vai ser", "tipo": "O que é",
    # os VALORES no banco seguem pessoal/empresa/fornecedor (a coluna tem check e o
    # resto do sistema lê assim). Só as palavras mudam: num buffet toda festa cairia
    # em "Empresa", que não diz nada.
    "t_pessoal": "Interno", "t_empresa": "Festa", "t_fornecedor": "Fornecedor",
    "conv_t": "👥 Quem participa (opcional)",
    "conv_d": "Cliente, equipe, fornecedor — cada um recebe o próprio link de confirmação.",
    "conv_add": "+ adicionar pessoa",
    "salvar": "Marcar evento", "salvando": "⏳ Marcando…",
    "proximos": "Próximos eventos",
    "cta_dia": "＋ Marcar evento nesse dia",
})


def _vendas():
    """Import tardio: web/painel_agenda não depende do módulo de vendas pra abrir —
    só pra saber se esta conta vende data (nicho de eventos)."""
    from finance import vendas
    return vendas


def _centavos(txt: str) -> int | None:
    """"1.810,00", "1810", "R$ 1.810" -> centavos. O dono digita como fala."""
    d = "".join(ch for ch in (txt or "") if ch.isdigit() or ch in ",.")
    if not d:
        return None
    d = d.replace(".", "").replace(",", ".")
    try:
        v = float(d)
    except ValueError:
        return None
    return int(round(v * 100)) or None


def _brl(centavos) -> str:
    """R$ 1.810,00 — vazio quando não há valor. O sinal esperado aparece na caixa
    do dia e no card das datas seguradas; sem valor, a linha simplesmente não fala
    de dinheiro."""
    if not centavos:
        return ""
    v = int(centavos) / 100
    return "R$ " + f"{v:,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def _wa_share(contato: str, texto: str) -> str:
    """Link que abre o WhatsApp DA PRÓPRIA pessoa (dono) já com a mensagem pronta
    pro cliente. Se tiver número, manda direto pra ele; senão, abre pra escolher."""
    d = _so_digitos(contato)
    if d and not d.startswith("55") and len(d) <= 11:
        d = "55" + d
    alvo = d if d else ""
    return f"https://wa.me/{alvo}?text={quote(texto)}"


def _fmt_quando_hist(dt, hoje: date) -> str:
    """'Hoje 12:01' / 'Ontem 09:15' / '07/08 08:50' — pro Histórico de envios."""
    local = dt.astimezone(ag.BRT)
    hhmm = local.strftime("%H:%M")
    d = local.date()
    if d == hoje:
        return f"Hoje {hhmm}"
    if d == hoje - timedelta(days=1):
        return f"Ontem {hhmm}"
    return local.strftime("%d/%m") + f" {hhmm}"


def _preparar_historico(itens: list[dict], hoje: date) -> list[dict]:
    for it in itens:
        it["quando_rot"] = _fmt_quando_hist(it["quando"], hoje)
        it["tipo_rot"] = _TIPO_HIST_ROT.get(it["tipo"], it["tipo"])
        it["canal_rot"] = _CANAL_HIST_ROT.get(it["canal"], it["canal"])
        it["motivo_rot"] = _MOTIVO_HIST.get(it["motivo"], it["motivo"]) if it.get("motivo") else None
        it["convidado_rot"] = it.get("convidado_nome") or "— (você)"
        it["pode_reenviar"] = (not it["ok"]) and it["tipo"] in ("convite", "lembrete")
        it["quando"] = it["quando"].isoformat()
    return itens


def _fmt_eta(dt, agora) -> dict:
    """'~1h10' / '~40min' / 'agora' + a hora prevista — pra fila do Histórico."""
    mins = int((dt - agora).total_seconds() // 60)
    if mins <= 0:
        rel = "agora"
    elif mins < 60:
        rel = f"~{mins}min"
    else:
        h, m = divmod(mins, 60)
        rel = f"~{h}h{m:02d}" if m else f"~{h}h"
    return {"rel": rel, "hora": dt.astimezone(ag.BRT).strftime("%H:%M"), "passou": mins <= 0}


def _preparar_fila(itens: list[dict], agora) -> list[dict]:
    for it in itens:
        eta = _fmt_eta(it["sai_em"], agora)
        it["eta_rel"] = eta["rel"]
        it["eta_hora"] = eta["hora"]
        it["tipo_rot"] = _TIPO_HIST_ROT.get(it["tipo"], it["tipo"])
        it["convidado_rot"] = it.get("convidado_nome") or "— (você)"
        it["status_rot"] = "⏳ Tentando agora" if eta["passou"] else "🕓 Aguardando janela"
    return itens


def _montar_share(request: Request, pool, conta_id: int, convite_ev: str, convite: str):
    """Card de compartilhar os convites de UM evento (todos os convidados dele)."""
    ev_id = None
    if convite_ev and convite_ev.isdigit():
        ev_id = int(convite_ev)
    elif convite:                                  # retrocompat: token -> evento dele
        c = cv.por_token(pool, convite)
        if c and c["conta_id"] == conta_id:
            ev_id = c["evento"]["id"]
    if not ev_id:
        return None
    ev = ag.evento_por_id(pool, conta_id, ev_id)
    if not ev:
        return None
    guests = cv.por_evento(pool, conta_id, [ev_id]).get(ev_id, [])
    if not guests:
        return None
    quando = ag.fmt_hora(ev)
    local = ev.get("local")
    nomes = [g["nome"] for g in guests if (g.get("nome") or "").strip()]
    com = f" Com: {ag.frase_nomes(nomes)}." if len(nomes) > 1 else ""
    lista = []
    link_online = ev.get("link_online")
    for g in guests:
        url = _convite_url(request, g["token"])
        msg = (f"Oi{(' ' + g['nome']) if g['nome'] else ''}! Quero marcar uma reunião: "
               f"{ev['titulo']} — {quando}{(' (' + local + ')') if local else ''}.{com} "
               f"Confirma pra mim aqui: {url}")
        if link_online:
            msg += f"\n🎥 Chamada: {link_online}"
        lista.append({"nome": g["nome"], "contato": g["contato"], "url": url,
                      "wa": _wa_share(g["contato"] or "", msg), "token": g["token"],
                      "status": g["status"], "status_rot": g["status_rot"]})
    return {"titulo": ev["titulo"], "quando": quando, "guests": lista, "ev_id": ev_id,
            "total": len(lista), "resumo": cv.resumo(guests),
            "auto_on": cv.template_configurado(pool, conta_id)}


# ================================================================ CALENDÁRIO
@router.get("/painel/agenda", response_class=HTMLResponse)
def agenda_home(request: Request, m: str = "", novo: str = "", convite: str = "",
                convite_ev: str = "", p: str = ""):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    pool = get_pool()
    conta_id = ctx["conta_id"]
    ano, mes = _mes_ref(m)
    agora = ag.agora_brt()
    hoje = agora.date()
    # FILTRO POR PESSOA (?p=<membro_id>). Não é permissão — a agenda é da conta e
    # todos veem tudo; é foco: "me mostra só o do Rafael". O padrão é o time.
    p_id = int(p) if (p or "").isdigit() else None
    # NICHO: só quem vende data (eventos) vê o vocabulário de data segurada e a
    # ficha do evento. Pra clínica, loja e escritório a Agenda continua exatamente
    # como estava — a pré-reserva nasce só de orçamento de evento, então a marca não
    # teria o que marcar e a legenda falaria de um sinal que nunca existe.
    vende_data = _vendas().vende_data(pool, conta_id)
    eventos = ag.eventos_mes(pool, conta_id, ano, mes)
    proximos = ag.proximos(pool, conta_id, limite=8)
    # nomes de quem marcou + as pessoas do filtro. As pessoas vêm dos AUTORES do
    # mês (não da equipe inteira): chip de quem não tem evento é botão que filtra
    # pro vazio. O mapa de nomes cobre eventos e próximos.
    with pool.connection() as c:
        # só `nome`: a coluna `email` de membros nasce em runtime (garantir_tabela)
        # e nem todo banco passou por ela — a agenda não pode quebrar por isso.
        nomes = dict(c.execute(
            "select id, coalesce(nullif(nome,''), '') from membros where conta_id=%s",
            (conta_id,)).fetchall())
    autores_mes = sorted({e["membro_id"] for e in eventos if e.get("membro_id")})
    pessoas = [{"id": mid, "nome": nomes.get(mid) or f"#{mid}", "on": (mid == p_id)}
               for mid in autores_mes]
    # O SERVIDOR NÃO FILTRA MAIS. Filtrava aqui — depois de fazer as treze
    # consultas e montar a página inteira — e por isso cada clique num nome era um
    # recarregamento completo pra rodar duas linhas sobre dados que já estavam na
    # mão. O filtro virou coisa da tela (ver o JS do .ag-pessoas): `p_id` só diz
    # qual chip nasce ligado, e o JS aplica antes do primeiro toque.
    #
    # Mandar tudo é o que torna "Todos" possível sem voltar ao servidor: filtrado
    # na origem, o navegador não teria de onde trazer os outros de volta.
    ids_com_convidados = {e["id"] for e in eventos} | {e["id"] for e in proximos}
    convidados = cv.por_evento(pool, conta_id, list(ids_com_convidados))
    # FICHA DO EVENTO: o orçamento vinculado e o pagamento de cada festa do mês.
    # Só pra quem vende data — e só quando há eventos, pra não gastar consulta à toa.
    fichas = {}
    if vende_data and eventos:
        try:
            fichas = _vendas().fichas_de_eventos(pool, conta_id, [e["id"] for e in eventos])
        except Exception:  # noqa: BLE001 — a ficha é leitura extra; a agenda abre sem ela
            _log_ag.warning("agenda: não deu pra montar as fichas dos eventos", exc_info=True)
    semanas = _monta_semanas(ano, mes, eventos, hoje, agora, fichas)
    # DATAS SEGURADAS: lista própria, da que vence primeiro pra última. `proximos`
    # não pode mostrá-las (é a fonte do lembrete e do resumo do dia), então sem
    # este card uma data que vence amanhã só aparecia pra quem abrisse o mês certo.
    seguradas = []
    for ev in ag.pre_reservas(pool, conta_id):
        pz = _prazo(ev, agora)
        seguradas.append({
            "id": ev["id"], "titulo": ev["titulo"],
            "quando": ag.fmt_hora(ev), "prazo": pz["rot"], "urgente": pz["urgente"],
            "ate": (ev["pre_reserva_ate"].astimezone(ag.BRT).strftime("%d/%m %H:%M")
                    if ev.get("pre_reserva_ate") else ""),
            "sinal": _brl(ev.get("sinal_centavos")),
            "orcamento_id": ev.get("orcamento_id"),
            "orcamento_numero": ev.get("orcamento_numero"),
            "mes": f"{ev['inicio'].astimezone(ag.BRT):%Y-%m}",
        })
    # DATAS CONFIRMADAS: a irmã do card acima, e a que não existia em lugar
    # nenhum. Quem quisesse saber quantas datas tinha vendidas contava no
    # calendário, mês a mês. Mesma consulta única do lado das seguradas.
    confirmadas = [{
        "id": ev["id"], "titulo": ev["titulo"],
        "quando": ag.fmt_hora(ev),
        "dia_rot": ev["inicio"].astimezone(ag.BRT).strftime("%d/%m"),
        "total": _brl(ev.get("total_centavos")),
        "sinal_pago": bool(ev.get("sinal_centavos")),
        "orcamento_id": ev.get("orcamento_id"),
        "orcamento_numero": ev.get("orcamento_numero"),
        "mes": f"{ev['inicio'].astimezone(ag.BRT):%Y-%m}",
    } for ev in ag.confirmadas(pool, conta_id, agora)] if vende_data else []
    orcs = {s["id"]: s for s in seguradas}
    eventos_dia = _eventos_por_dia(eventos, convidados, agora, orcs, fichas, nomes)
    reaproveitar = [{
        "id": e["id"], "titulo": e["titulo"], "hora_rot": ag.fmt_hora(e),
        "hora": e["inicio"].astimezone(ag.BRT).strftime("%H:%M"),
        "n_convidados": e["n_convidados"],
    } for e in ag.eventos_para_reaproveitar(pool, conta_id, agora)]
    for ev in proximos:
        ev["dia_rot"] = ev["inicio"].astimezone(ag.BRT).strftime("%d/%m")
        ev["hora_rot"] = ev["inicio"].astimezone(ag.BRT).strftime("%H:%M")
        ev["data_iso"] = ev["inicio"].astimezone(ag.BRT).date().isoformat()
        ev["tipo_rot"] = TIPO_ROT.get(ev["tipo"], "Pessoal")
        ev["convidados"] = convidados.get(ev["id"], [])
        ev["conv_resumo"] = cv.resumo(ev["convidados"]) if ev["convidados"] else None
        ev["autor"] = nomes.get(ev.get("membro_id")) or ""
    cfg = ag.get_config(pool, conta_id)
    feed_url = _feed_url(request, cfg["feed_token"]) if cfg.get("feed_token") else ""
    # Card de compartilhar os convites de um evento (?convite_ev=<id>; aceita também
    # ?convite=<token> por retrocompat, resolvendo pro evento dele).
    share = _montar_share(request, pool, conta_id, convite_ev, convite)
    hist = cv.listar_historico(pool, conta_id, dias=7)
    historico = _preparar_historico(hist["itens"], hoje)
    fila = _preparar_fila(cv.listar_fila(pool, conta_id, agora), agora)
    return _render("agenda", request, titulo="Agenda", secao_ativa="agenda",
                   historico=historico, historico_total=hist["total"], fila=fila,
                   ano=ano, mes=mes, mes_nome=MESES[mes], dias_sem=DIAS_SEM,
                   semanas=semanas, proximos=proximos, tipo_rot=TIPO_ROT,
                   eventos_dia=eventos_dia, status_rot=cv.STATUS_ROT,
                   reaproveitar=reaproveitar, meses_js=MESES, dias_sem_ext_js=DIAS_SEM_EXT,
                   agora_iso=agora.isoformat(),
                   mes_prev=_vizinho(ano, mes, -1), mes_next=_vizinho(ano, mes, +1),
                   mes_hoje=f"{hoje.year:04d}-{hoje.month:02d}",
                   hoje_iso=hoje.isoformat(), abrir_novo=(novo == "1"),
                   cfg=cfg, feed_url=feed_url, share=share, seguradas=seguradas,
                   confirmadas=confirmadas,
                   vende_data=vende_data, pessoas=pessoas, p_id=p_id,
                   rot=(_ROT_EVENTO if vende_data else _ROT_PADRAO),
                   aviso=request.session.pop("agenda_aviso", None))


# ================================================================ HISTÓRICO DE ENVIOS
@router.get("/painel/agenda/historico")
def agenda_historico(request: Request, dias: int = 7, falhas: str = "", q: str = "",
                     offset: int = 0):
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "auth"}, status_code=401)
    pool = get_pool()
    hoje = ag.agora_brt().date()
    hist = cv.listar_historico(pool, ctx["conta_id"], dias=dias, somente_falhas=(falhas == "1"),
                               busca=q, offset=offset)
    itens = _preparar_historico(hist["itens"], hoje)
    return JSONResponse({"ok": True, "itens": itens, "total": hist["total"]})


@router.post("/painel/agenda/historico/reenviar")
def agenda_historico_reenviar(request: Request, log_id: int = Form(...)):
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "auth"}, status_code=401)
    r = cv.reenviar_historico(get_pool(), ctx["conta_id"], log_id)
    return JSONResponse(r)


# ================================================================ NOVO
@router.post("/painel/agenda/novo")
def agenda_novo(request: Request, titulo: str = Form(...), data: str = Form(""),
                hora: str = Form(""), hora_fim: str = Form(""),
                local: str = Form(""), descricao: str = Form(""),
                tipo: str = Form("pessoal"), link_online: str = Form(""),
                convidado_nome: list[str] = Form(default=[]),
                convidado_contato: list[str] = Form(default=[]),
                segurar: str = Form(""), segurar_ate: str = Form(""),
                sinal_esperado: str = Form(""),
                m: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    titulo = (titulo or "").strip()
    quando = f"{(data or '').strip()} {(hora or '').strip()}".strip()
    inicio = ag.parse_datahora(quando)
    voltar = f"/painel/agenda?m={m}" if m else "/painel/agenda"
    if not titulo:
        request.session["agenda_aviso"] = "Dá um nome pro compromisso."
        return RedirectResponse(voltar + ("&" if "?" in voltar else "?") + "novo=1", status_code=303)
    if not inicio:
        request.session["agenda_aviso"] = "Não entendi a data/hora. Confere aí."
        return RedirectResponse(voltar + ("&" if "?" in voltar else "?") + "novo=1", status_code=303)
    pool = get_pool()
    local = (local or "").strip() or None
    # HORA DE ENCERRAMENTO (só o formulário de evento pergunta). Passa por
    # janela_evento pra herdar a regra do ramo: festa que "encerra às 24" acaba
    # 00:00 do dia seguinte, e virar a noite (19h→02h) rola o dia. Sem fim, o
    # compromisso segue pontual, como sempre foi.
    fim = None
    if (hora_fim or "").strip():
        _ini, fim = ag.janela_evento(data, hora or "09:00", hora_fim)
        if fim is not None and fim <= inicio:
            fim = None
    # SÓ SEGURAR A DATA: pré-reserva nascida na própria agenda, sem orçamento
    # nenhum — é o telefonema "segura o dia 20 pra mim até sexta". Sem isso, a
    # única saída do dono era marcar firme (mentira) ou não marcar (e vender duas
    # vezes). O prazo padrão vem do card ⏳ Data segurada da conta.
    ate = None
    sinal_cent = None
    # o gate também no SERVIDOR: o form vem do navegador, e navegador não é fonte
    # confiável. Conta que não vende data não segura data nem por requisição forjada.
    if segurar == "1" and _vendas().vende_data(pool, ctx["conta_id"]):
        ate = ag.parse_datahora(segurar_ate) if segurar_ate else None
        if ate is None:
            dias = ag.get_config(pool, ctx["conta_id"]).get("pre_reserva_dias") or ag.PRE_RESERVA_DIAS
            ate = ag.agora_brt() + timedelta(days=int(dias))
        else:
            ate = ate.replace(hour=23, minute=59)   # a data digitada vale até o fim do dia
        sinal_cent = _centavos(sinal_esperado)
    ev = ag.criar_evento(pool=pool, conta_id=ctx["conta_id"], titulo=titulo,
                         inicio=inicio, fim=fim, membro_id=ctx["membro_id"],
                         local=local,
                         descricao=(descricao or "").strip() or None,
                         tipo=tipo if tipo in ag.TIPOS else "pessoal",
                         link_online=(link_online or "").strip() or None if ag.eh_online(local) else None,
                         pre_reserva_ate=ate, sinal_centavos=sinal_cent)
    destino = f"/painel/agenda?m={inicio.year:04d}-{inicio.month:02d}"
    if ate is not None:
        request.session["agenda_aviso"] = (
            f"Data segurada: “{titulo}” em {inicio.strftime('%d/%m às %H:%M')}, "
            f"até {ate.strftime('%d/%m %H:%M')}. Ela ocupa o dia, mas não vira lembrete.")
        return RedirectResponse(destino, status_code=303)
    # Convidados (um ou vários): cria um convite por linha preenchida.
    pares = []
    for i in range(max(len(convidado_nome), len(convidado_contato))):
        nm = convidado_nome[i] if i < len(convidado_nome) else ""
        ct = convidado_contato[i] if i < len(convidado_contato) else ""
        if (nm or "").strip() or (ct or "").strip():
            pares.append((nm, ct))
    if pares:
        for nm, ct in pares:
            cv.criar_convidado(pool, ctx["conta_id"], ev["id"], nm, ct)
        n = len(pares)
        request.session["agenda_aviso"] = (
            f"“{titulo}” marcado. Agora é só enviar {'o convite' if n == 1 else f'os {n} convites'}.")
        return RedirectResponse(destino + f"&convite_ev={ev['id']}", status_code=303)
    request.session["agenda_aviso"] = f"“{titulo}” marcado pra {inicio.strftime('%d/%m às %H:%M')}."
    return RedirectResponse(destino, status_code=303)


# ================================================================ BUSCAR LOCAL
@router.get("/painel/agenda/buscar-local")
def agenda_buscar_local(request: Request, q: str = "", lat: str = "", lng: str = ""):
    """Sugestões de endereço/lugar pro campo Local (autocomplete no form de novo
    compromisso). Reusa a mesma Google Places API já usada na prospecção — aqui
    sem cidade/segmento, é busca livre. Devolve endereço FORMATADO pelo Google
    (o que faz o link do mapa depois ser exato, não uma busca por texto solto).

    `lat`/`lng` (opcionais): posição do navegador, quando o usuário permite —
    enviesa o ranking pro entorno, não restringe (long-distance ainda aparece
    se o texto bater)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "auth", "itens": []}, status_code=401)
    from finance import prospeccao_fontes as pf
    if not pf.tem_chave_places():
        return JSONResponse({"ok": False, "erro": "sem_chave", "itens": []})
    termo = (q or "").strip()
    if len(termo) < 2:
        return JSONResponse({"ok": True, "itens": []})
    lat_f = lng_f = None
    try:
        if lat and lng:
            lat_f, lng_f = float(lat), float(lng)
    except ValueError:
        pass
    r = pf.buscar_places(termo, cidade="", max_resultados=6, lat=lat_f, lng=lng_f)
    if not r.get("ok"):
        return JSONResponse({"ok": False, "erro": r.get("erro"), "itens": []})
    itens = [{"nome": i["empresa"], "endereco": i["endereco"]}
             for i in r["itens"] if i.get("endereco")]
    return JSONResponse({"ok": True, "itens": itens})


# ================================================================ CANCELAR
@router.post("/painel/agenda/cancelar")
def agenda_cancelar(request: Request, evento_id: int = Form(...), m: str = Form("")):
    ajax = bool(request.headers.get("x-zaq-ajax"))
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "auth"}, status_code=401) if ajax else redir
    ok = ag.cancelar_evento(get_pool(), ctx["conta_id"], evento_id)
    if ajax:
        return JSONResponse({"ok": bool(ok)})
    if ok:
        request.session["agenda_aviso"] = "Compromisso cancelado."
    voltar = f"/painel/agenda?m={m}" if m else "/painel/agenda"
    return RedirectResponse(voltar, status_code=303)


# ================================================================ REMARCAR
@router.post("/painel/agenda/remarcar")
def agenda_remarcar(request: Request, evento_id: int = Form(...), data: str = Form(""),
                    hora: str = Form(""), avisar: str = Form(""), m: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    voltar = f"/painel/agenda?m={m}" if m else "/painel/agenda"
    novo_inicio = ag.parse_datahora(f"{(data or '').strip()} {(hora or '').strip()}".strip())
    if not novo_inicio:
        request.session["agenda_aviso"] = "Não entendi a nova data/hora. Confere aí."
        return RedirectResponse(voltar, status_code=303)
    r = cv.remarcar_e_avisar(get_pool(), ctx["conta_id"], evento_id, novo_inicio, None,
                             avisar=(avisar == "1"), agora=ag.agora_brt())
    if not r.get("ok"):
        request.session["agenda_aviso"] = "Não consegui remarcar esse compromisso."
    else:
        msg = f"Remarcado para {ag.fmt_hora({'inicio': novo_inicio, 'fim': None})}."
        if r.get("total_convidados"):
            msg += (f" {r['avisados']} de {r['total_convidados']} convidados avisados."
                    if avisar == "1" else f" {r['total_convidados']} convidados voltaram a aguardar confirmação.")
        request.session["agenda_aviso"] = msg
    destino = f"/painel/agenda?m={novo_inicio.year:04d}-{novo_inicio.month:02d}"
    return RedirectResponse(destino, status_code=303)


# ================================================================ DESFECHO
@router.post("/painel/agenda/desfecho")
def agenda_desfecho(request: Request, evento_id: int = Form(...), desfecho: str = Form(...),
                    m: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "auth"}, status_code=401)
    ok = ag.marcar_desfecho(get_pool(), ctx["conta_id"], evento_id, desfecho, ag.agora_brt())
    return JSONResponse({"ok": ok})


# ================================================================ CONVIDADO (adicionar depois)
@router.post("/painel/agenda/convidado/adicionar")
def agenda_convidado_adicionar(request: Request, evento_id: int = Form(...),
                               nome: str = Form(""), contato: str = Form(""),
                               m: str = Form("")):
    """Inclui mais um convidado num compromisso já marcado (antes só dava na
    criação). Só em compromisso ativo — evento_por_id já filtra cancelado."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    voltar = f"/painel/agenda?m={m}" if m else "/painel/agenda"
    ev = ag.evento_por_id(get_pool(), ctx["conta_id"], evento_id)
    nome = (nome or "").strip()
    contato = (contato or "").strip()
    if not ev:
        request.session["agenda_aviso"] = "Não achei esse compromisso."
    elif not nome and not contato:
        request.session["agenda_aviso"] = "Preencha nome ou contato do convidado."
    else:
        cv.criar_convidado(get_pool(), ctx["conta_id"], evento_id, nome, contato)
        request.session["agenda_aviso"] = f"{nome or 'Convidado'} adicionado. Agora é só mandar o convite pra ele."
        voltar += ("&" if "?" in voltar else "?") + f"convite_ev={evento_id}"
    return RedirectResponse(voltar, status_code=303)


_ERRO_ENVIO = {
    "sem_numero": "esse convidado não tem número de WhatsApp",
    "sem_template": "o template ainda não está configurado (falta a aprovação / o SID)",
    "nao_configurado": "o WhatsApp (Twilio) não está configurado",
    "sem_numero_empresa": "esta empresa ainda não tem número de WhatsApp configurado (aba Canais)",
    "provedor_sem_template": "o número desta empresa não é Twilio — o template só sai por número Twilio",
    "numero_invalido": "o número do convidado parece inválido",
    "convite_nao_encontrado": "convite não encontrado",
}

# Rótulos curtos pro Histórico de envios (tabela) — mesmos códigos de erro que
# _ERRO_ENVIO já mapeia (em frase), só que aqui em rótulo curto pra caber na linha.
_MOTIVO_HIST = {
    "sem_numero": "sem número",
    "sem_template": "sem template configurado",
    "fora_da_janela_sem_template": "fora da janela de 24h, sem template",
    "nao_configurado": "WhatsApp não configurado",
    "sem_numero_empresa": "empresa sem número de WhatsApp",
    "provedor_sem_template": "número não é Twilio",
    "numero_invalido": "número inválido",
    "falha_envio": "falha no envio (rede/API)",
}
_TIPO_HIST_ROT = {"convite": "✉️ Convite", "lembrete": "⏰ Lembrete", "remarcado": "🔁 Remarcado"}
_CANAL_HIST_ROT = {"whatsapp_livre": "💬 Livre", "whatsapp_template": "📩 Template", "telegram": "Telegram"}


@router.post("/painel/agenda/convite/enviar")
def agenda_convite_enviar(request: Request, token: str = Form(...),
                          ev_id: str = Form(""), m: str = Form("")):
    """Dispara o convite pelo WhatsApp (template) pro número do convidado."""
    ajax = bool(request.headers.get("x-zaq-ajax"))
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "msg": "Faça login."}, status_code=401) if ajax else redir
    pool = get_pool()
    c = cv.por_token(pool, token)
    if not c or c["conta_id"] != ctx["conta_id"]:      # ownership sagrado
        ok, msg = False, "Convite não encontrado."
    elif c["status"] == "confirmado":                  # já confirmou: não reenvia
        ok, msg = False, f"{c['nome'] or 'O convidado'} já confirmou — não precisa reenviar. ✅"
    else:
        r = cv.enviar_convite_whatsapp(pool, token)
        quem = c["nome"] or "convidado"
        if r.get("ok"):
            ok, msg = True, f"Convite enviado pelo WhatsApp pra {quem}! ✅"
        else:
            motivo = _ERRO_ENVIO.get(r.get("erro"), r.get("erro") or "erro desconhecido")
            ok, msg = False, f"Não consegui enviar pra {quem}: {motivo}."
    if ajax:
        return JSONResponse({"ok": ok, "msg": msg})
    request.session["agenda_aviso"] = msg
    destino = f"/painel/agenda?m={m}" if m else "/painel/agenda"
    if ev_id:
        destino += ("&" if "?" in destino else "?") + f"convite_ev={ev_id}"
    return RedirectResponse(destino, status_code=303)


# ================================================================ LEMBRETE (opt-in)
@router.post("/painel/agenda/lembrete")
def agenda_lembrete(request: Request, resumo_dia: str = Form(""),
                    hora_resumo: str = Form("7"), aviso: str = Form(""),
                    aviso_antes_min: str = Form("30"), avisar_convidados: str = Form(""),
                    enviar_confirmacao: str = Form(""), m: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    resumo_on = resumo_dia == "1"
    aviso_on = aviso == "1"
    try:
        hora = int(hora_resumo)
    except (TypeError, ValueError):
        hora = 7
    try:
        antes = int(aviso_antes_min) if aviso_on else None
    except (TypeError, ValueError):
        antes = 30 if aviso_on else None
    ag.salvar_config(get_pool(), ctx["conta_id"],
                     resumo_ativo=resumo_on,
                     hora_resumo=hora,
                     aviso_antes_min=antes,
                     avisar_convidados=(avisar_convidados == "1"),
                     enviar_confirmacao=(enviar_confirmacao == "1"))
    if resumo_on or aviso_on:
        request.session["agenda_aviso"] = "Lembrete ligado. 🔔"
    else:
        request.session["agenda_aviso"] = "Lembrete desligado."
    voltar = f"/painel/agenda?m={m}" if m else "/painel/agenda"
    return RedirectResponse(voltar, status_code=303)


# ============================================ AÇÕES SOBRE A DATA SEGURADA
@router.post("/painel/agenda/sinal-recebido")
def agenda_sinal_recebido(request: Request, evento_id: int = Form(...), m: str = Form("")):
    """O sinal caiu — apertado de dentro da Agenda, onde a data está.

    Antes só existia no funil (Serviços): pra firmar uma data era preciso sair da
    agenda, abrir a proposta e apertar lá. A REGRA é a mesma —
    finance.vendas.confirmar_sinal, o mesmo ponto único que o botão do funil usa,
    com a mesma baixa do título na data do pagamento.

    Pré-reserva nascida na própria agenda não tem orçamento: aí só firma a data,
    que é tudo que existe pra firmar."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    pool, conta_id = get_pool(), ctx["conta_id"]
    orc = ag.orcamento_do_evento(pool, conta_id, evento_id)
    if orc:
        from finance import vendas
        r = vendas.confirmar_sinal(pool, conta_id, orc)
        ok = bool(r.get("ok"))
        extra = " O título dessa parcela entrou como recebido." if r.get("titulo_baixado") else ""
    else:
        ok = ag.confirmar_pre_reserva(pool, conta_id, evento_id)
        extra = ""
    request.session["agenda_aviso"] = (
        f"Sinal confirmado — a data é do cliente agora. ✅{extra}" if ok
        else "Essa data já não estava segurada.")
    voltar = f"/painel/agenda?m={m}" if m else "/painel/agenda"
    return RedirectResponse(voltar, status_code=303)


@router.post("/painel/agenda/liberar")
def agenda_liberar(request: Request, evento_id: int = Form(...), m: str = Form("")):
    """Solta a data antes do prazo, porque o dono decidiu.

    Até aqui só o prazo soltava: quem recebia um "desisti" por telefone ficava
    esperando o relógio, com a data travada. Só age em data SEGURADA — compromisso
    firme se cancela pelo botão de cancelar, que pede confirmação e avisa convidado."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    ok = ag.liberar_pre_reserva(get_pool(), ctx["conta_id"], evento_id)
    request.session["agenda_aviso"] = (
        "Data liberada — voltou a ficar disponível." if ok
        else "Essa data já não estava segurada.")
    voltar = f"/painel/agenda?m={m}" if m else "/painel/agenda"
    return RedirectResponse(voltar, status_code=303)


@router.get("/painel/agenda/conflitos")
def agenda_conflitos(request: Request, data: str = "", hora: str = "", fim: str = ""):
    """O que já está marcado nessa janela — pro aviso de choque APARECER NA TELA,
    na hora de marcar.

    Hoje o choque só existe por Telegram, e só quando vem de aprovação de orçamento.
    Quem marca pelo painel não é avisado de nada. Não bloqueia: quem decide se cabe
    é a empresa (buffet com dois salões cabe, fotógrafo não)."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "itens": []}, status_code=401)
    inicio, ffim = ag.janela_evento(data, hora or "09:00", fim or None)
    if not inicio:
        return JSONResponse({"ok": True, "itens": []})
    itens = [{"titulo": e["titulo"], "quando": ag.fmt_hora(e),
              "pre": e.get("status") == ag.PRE_RESERVADO}
             for e in ag.conflitos(get_pool(), ctx["conta_id"], inicio, ffim)]
    return JSONResponse({"ok": True, "itens": itens})


# ================================================ PRAZO DA DATA SEGURADA (pré-reserva)
@router.post("/painel/agenda/pre-reserva")
def agenda_pre_reserva(request: Request, pre_reserva_dias: str = Form("3"), m: str = Form("")):
    """Quanto tempo a data fica segurada esperando o sinal. Rota própria (não é o
    form do lembrete) porque é regra de venda, não preferência de aviso."""
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    try:
        dias = int(pre_reserva_dias)
    except (TypeError, ValueError):
        dias = ag.PRE_RESERVA_DIAS
    d = ag.salvar_pre_reserva_dias(get_pool(), ctx["conta_id"], dias)
    request.session["agenda_aviso"] = (
        f"A partir de agora a data fica segurada por {d} dia{'s' if d != 1 else ''} "
        "esperando o sinal. As pré-reservas que já estão correndo mantêm o prazo delas.")
    voltar = f"/painel/agenda?m={m}" if m else "/painel/agenda"
    return RedirectResponse(voltar, status_code=303)


# ================================================================ SINCRONIZAR (.ics)
@router.post("/painel/agenda/sincronizar")
def agenda_sincronizar(request: Request, m: str = Form("")):
    ctx, redir = _acesso(request)
    if redir is not None:
        return redir
    ag.garantir_feed_token(get_pool(), ctx["conta_id"])
    voltar = f"/painel/agenda?m={m}" if m else "/painel/agenda"
    return RedirectResponse(voltar, status_code=303)


# ---------------------------------------------------------------- feed público .ics
@router.get("/agenda/{token}.ics")
def agenda_feed(token: str):
    """Feed .ics assinável (público por token). Google/Apple/Outlook leem por URL."""
    pool = get_pool()
    conta_id = ag.conta_por_feed_token(pool, token)
    if conta_id is None:
        return Response("not found", status_code=404)
    ics = ag.feed_ics(ag.eventos_para_feed(pool, conta_id))
    return Response(ics, media_type="text/calendar; charset=utf-8",
                    headers={"Content-Disposition": 'inline; filename="zaq.ics"',
                             "Cache-Control": "public, max-age=300"})


# ================================================================ CONVITE (público)
_ACAO_STATUS = {"confirmar": "confirmado", "remarcar": "remarcar", "recusar": "recusado"}


def _render_convite(c: dict, resultado: str | None) -> HTMLResponse:
    """Página pública de confirmação. `resultado` = status recém-registrado (ou None
    pra mostrar os botões). Se o convidado já respondeu antes, mostra o resultado."""
    ev = c["evento"]
    estado = resultado or (c["status"] if c["status"] != "pendente" else None)
    ctx = {
        "empresa": c["empresa"] or "A empresa", "nome": c["nome"] or "",
        "titulo": ev["titulo"], "quando": ag.fmt_hora(c["evento"]),
        "local": ev.get("local") or "", "token": c["token"],
        "estado": estado, "resposta": c.get("resposta") or "",
        "cal_link": cv.link_calendario(ev) if estado == "confirmado" else "",
        "mapa_link": (cv.link_mapa(ev) if estado == "confirmado" else "") or "",
        "link_online": (ev.get("link_online") if estado == "confirmado" else "") or "",
    }
    return HTMLResponse(_env.get_template("convite").render(**ctx))


@router.get("/convite/{token}", response_class=HTMLResponse)
def convite_ver(request: Request, token: str):
    c = cv.por_token(get_pool(), token)
    if not c:
        return HTMLResponse(_env.get_template("convite_404").render(), status_code=404)
    return _render_convite(c, resultado=None)


@router.post("/convite/{token}/responder", response_class=HTMLResponse)
def convite_responder(request: Request, token: str, acao: str = Form(...),
                      resposta: str = Form("")):
    pool = get_pool()
    status = _ACAO_STATUS.get((acao or "").strip())
    if not status:
        return RedirectResponse(f"/convite/{token}", status_code=303)
    c = cv.responder(pool, token, status, resposta)
    if not c:
        return HTMLResponse(_env.get_template("convite_404").render(), status_code=404)
    if c.get("mudou"):         # só avisa quando o status REALMENTE mudou (não repete)
        cv.pos_resposta(pool, c)
    return _render_convite(c, resultado=status)


# ================================================================ TEMPLATE
_CSS_CRU = """
/* width:100% porque .ag-wrap é item flex do body (junto do menu lateral): sem
   largura definida ele adota a do conteúdo, e uma tabela larga empurrava a
   página inteira pra fora da tela em vez de rolar dentro do próprio card. */
.ag-wrap{max-width:1080px;width:100%;margin:0 auto;min-width:0}
/* itens de grid/flex não encolhem abaixo do conteúdo por padrão (min-width:auto):
   sem isto o calendário de 7 colunas força a página inteira a passar da largura
   da tela no celular, e tudo (inclusive os cards) sai rolando pro lado. */
.ag-grid>*,.side-cards,.side-cards>.ag-card{min-width:0}
.ag-top{display:flex;align-items:center;justify-content:space-between;gap:14px;flex-wrap:wrap;margin:.2rem 0 1rem}
.ag-mes{display:flex;align-items:center;gap:12px}
.ag-mes h1{font-size:1.32rem;margin:0;text-transform:capitalize;letter-spacing:-.01em}
.ag-nav{display:inline-flex;gap:6px}
.ag-nav a,.ag-hoje{display:inline-flex;align-items:center;justify-content:center;min-width:34px;height:34px;padding:0 10px;border:1px solid var(--borda);border-radius:9px;background:var(--card-2);color:var(--txt);text-decoration:none;font-size:.9rem}
.ag-nav a:hover,.ag-hoje:hover{border-color:var(--verde)}
.ag-hoje{font-size:.82rem}
.ag-btn{width:auto;margin:0;display:inline-flex;align-items:center;gap:7px;background:var(--verde);color:var(--sobre-verde);font-weight:700;border:0;border-radius:10px;padding:.6rem 1rem;font-size:.92rem;cursor:pointer;text-decoration:none}
/* feedback instantâneo: botão "reagindo" enquanto o back processa */
.is-busy{opacity:.62;cursor:progress!important;pointer-events:none}
/* toast (aviso flutuante) — usado nas ações sem reload */
.zaq-toast{position:fixed;left:50%;bottom:22px;transform:translateX(-50%) translateY(12px);background:var(--card-2);border:1px solid var(--verde);color:var(--txt);padding:.7rem 1.05rem;border-radius:12px;font-size:.9rem;box-shadow:0 10px 34px rgba(0,0,0,.45);opacity:0;transition:transform .28s,opacity .28s;z-index:9999;max-width:92vw;text-align:center}
.zaq-toast.show{opacity:1;transform:translateX(-50%) translateY(0)}
.zaq-toast.err{border-color:var(--coral)}
.px-row.saindo{opacity:0;transform:translateX(8px);transition:.22s}
.ag-btn:hover{background:var(--verde-hover)}
/* filtro por pessoa: chips abaixo do topo. Não é permissão — todos veem tudo —,
   é foco. Só aparece quando o mês tem autor identificado (senão é um "Todos" só). */
.ag-pessoas{display:flex;gap:.4rem;flex-wrap:wrap;margin:-.4rem 0 1rem}
.agp{font-size:.76rem;color:var(--txt-mut);text-decoration:none;
  border:1px solid var(--borda);background:var(--card-2);border-radius:999px;
  padding:.22rem .7rem}
.agp.on{border-color:var(--neon-borda);background:var(--neon-fundo);color:var(--txt);font-weight:600}
.ag-grid{display:grid;grid-template-columns:1.7fr .95fr;gap:18px;align-items:start}
@media(max-width:860px){.ag-grid{grid-template-columns:1fr}}
/* histórico de envios */
.hist-card{background:var(--card);border:1px solid var(--borda);border-radius:14px;padding:16px;margin-top:18px}
.hist-hd{display:flex;align-items:flex-start;justify-content:space-between;gap:12px;flex-wrap:wrap;margin-bottom:12px}
.hist-hd h2{font-size:.95rem;margin:0;font-weight:700}
.hist-hd .sub{font-size:.74rem;color:var(--txt-mut);margin-top:2px}
.hist-filtros{display:flex;gap:6px;flex-wrap:wrap;align-items:center;flex:0 1 auto}
/* o portal define button{width:100%;margin-top:1.4rem} — sem este reset os
   filtros/abas empilham um por linha e abrem um buraco no meio do card */
.hist-filtros .hf-btn,.hist-tabs .hist-tab{width:auto;margin:0}
.hf-btn{background:var(--card-2);border:1px solid var(--borda);color:var(--txt-mut);border-radius:20px;padding:.3rem .75rem;font-size:.72rem;font-weight:600;cursor:pointer;white-space:nowrap}
.hf-btn:hover{border-color:var(--verde);color:var(--verde-claro)}
.hf-btn.on{background:rgba(29,158,117,.16);border-color:var(--verde);color:var(--verde-claro)}
.hf-search{background:var(--card-2);border:1px solid var(--borda);border-radius:8px;padding:.32rem .65rem;font-size:.76rem;color:var(--txt);width:170px;max-width:100%}
.hf-search::placeholder{color:var(--txt-mut)}
.hf-search:focus{outline:0;border-color:var(--verde)}
.hist-resumo{display:flex;gap:16px;flex-wrap:wrap;margin-bottom:14px;padding:9px 11px;background:var(--card-2);border:1px solid var(--borda);border-radius:10px;font-size:.76rem;color:var(--txt-mut)}
.hist-resumo b{font-variant-numeric:tabular-nums;color:var(--txt)}
.hist-resumo .hr-ok b{color:var(--verde-claro)}
.hist-resumo .hr-fail b{color:var(--coral)}
.hist-tbl-wrap{overflow-x:auto}
.hist-tbl{width:100%;border-collapse:collapse;font-size:.8rem}
.hist-tbl th{text-align:left;font-size:.66rem;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:var(--txt-mut);padding:0 9px 7px;border-bottom:1px solid var(--borda);white-space:nowrap}
.hist-tbl td{padding:8px 9px;border-bottom:1px solid var(--borda);vertical-align:top}
.hist-tbl tr:last-child td{border-bottom:0}
.hist-qd{color:var(--txt-mut);white-space:nowrap;font-variant-numeric:tabular-nums;font-size:.76rem}
.hist-compr{font-weight:600}
.hist-compr .loc{font-size:.7rem;color:var(--txt-mut);font-weight:400;margin-top:1px}
.hist-tipo{font-size:.66rem;font-weight:700;padding:2px 8px;border-radius:20px;white-space:nowrap;display:inline-flex}
.ht-convite{background:rgba(57,135,229,.14);color:#bcd8f6;border:1px solid rgba(57,135,229,.4)}
.ht-lembrete{background:rgba(224,163,62,.14);color:var(--ambar);border:1px solid rgba(224,163,62,.4)}
.ht-remarcado{background:rgba(93,202,165,.12);color:var(--verde-claro);border:1px solid var(--verde)}
.hist-canal{font-size:.74rem;color:var(--txt-mut);white-space:nowrap}
.hist-status{display:inline-flex;flex-direction:column;gap:1px;font-size:.76rem;font-weight:700;white-space:nowrap}
.hs-ok{color:var(--verde-claro)}
.hs-fail{color:var(--coral)}
.hs-motivo{font-size:.68rem;font-weight:400;color:var(--txt-mut);white-space:normal;max-width:220px}
.hist-retry{background:transparent;border:1px solid var(--borda);color:var(--txt-mut);border-radius:7px;padding:.26rem .55rem;font-size:.68rem;font-weight:600;cursor:pointer;white-space:nowrap}
.hist-retry:hover{border-color:var(--verde);color:var(--verde-claro)}
.hist-vazio{color:var(--txt-mut);font-size:.84rem;padding:18px 4px;text-align:center}
.hist-foot{display:flex;justify-content:center;margin-top:12px}
.hist-mais{background:transparent;border:1px dashed var(--borda);color:var(--verde-claro);border-radius:9px;padding:.45rem 1rem;font-size:.78rem;font-weight:600;cursor:pointer}
.hist-tabs{display:flex;gap:18px;margin-bottom:14px;border-bottom:1px solid var(--borda)}
.hist-tab{background:none;border:0;color:var(--txt-mut);padding:.45rem .1rem;font-size:.82rem;font-weight:700;cursor:pointer;position:relative;top:1px;border-bottom:2px solid transparent;display:inline-flex;align-items:center;gap:6px;flex:0 0 auto}
.hist-tab:hover{color:var(--txt)}
.hist-tab.on{color:var(--txt);border-bottom-color:var(--verde)}
.hist-tab .cnt{background:var(--card-2);border:1px solid var(--borda);border-radius:20px;padding:0 7px;font-size:.68rem;color:var(--txt-mut);font-variant-numeric:tabular-nums}
.hist-tab.on .cnt{background:rgba(29,158,117,.16);border-color:var(--verde);color:var(--verde-claro)}
.hist-tab .cnt.warn{background:rgba(224,163,62,.16);border-color:var(--ambar);color:var(--ambar)}
.hs-wait{color:var(--ambar)}
.eta{font-size:.68rem;color:var(--txt-mut)}
.eta b{color:var(--txt);font-variant-numeric:tabular-nums}
.hist-resumo .hr-wait b{color:var(--ambar)}
/* Mobile: tabela de 6-7 colunas não cabe — vira cartão empilhado, cada célula
   com seu rótulo (data-rot) na frente, sem scroll lateral. */
@media(max-width:720px){
  .hist-hd{gap:10px}
  .hist-filtros{width:100%}
  .hf-search{flex:1 1 100%;width:auto}
  .hist-tbl thead{display:none}
  .hist-tbl,.hist-tbl tbody,.hist-tbl tr,.hist-tbl td{display:block;width:100%;box-sizing:border-box}
  .hist-tbl tr{background:var(--card-2);border:1px solid var(--borda);border-radius:10px;padding:10px 12px;margin-bottom:8px}
  .hist-tbl td{border:0;padding:2px 0;display:flex;flex-wrap:wrap;gap:2px 8px;align-items:baseline;min-width:0}
  .hist-tbl td:empty{display:none}
  .hist-tbl td:before{content:attr(data-rot);flex:0 0 84px;font-size:.66rem;font-weight:700;letter-spacing:.03em;text-transform:uppercase;color:var(--txt-mut)}
  .hist-tbl td>*{min-width:0}
  .hist-tbl .hist-compr{font-size:.9rem;margin-bottom:2px}
  .hist-tbl .hist-compr .loc{flex:1 1 100%;margin-left:92px}
  /* só o botão vai pra direita — a última coluna da fila é o Status, que
     precisa seguir alinhado com os outros rótulos */
  .hist-retry{width:auto;margin:6px 0 0 auto}
  .hs-motivo{max-width:none}
  .hist-resumo{gap:10px 14px}
}
.ag-card{background:var(--card);border:1px solid var(--borda);border-radius:14px;padding:16px}
.ag-card h2{font-size:.76rem;letter-spacing:.09em;text-transform:uppercase;color:var(--txt-mut);margin:0 0 12px;font-weight:700}
/* calendário */
.cal{border:1px solid var(--borda);border-radius:14px;overflow:hidden;background:var(--card)}
.cal-hd,.cal-wk{display:grid;grid-template-columns:repeat(7,1fr)}
.cal-hd{background:var(--card-2);border-bottom:1px solid var(--borda)}
.cal-hd span{padding:9px 0;text-align:center;font-size:.68rem;letter-spacing:.06em;text-transform:uppercase;color:var(--txt-mut);font-weight:700}
.cal-cell{height:84px;min-width:0;overflow:hidden;border-right:1px solid var(--borda);border-bottom:1px solid var(--borda);padding:6px 7px;display:flex;flex-direction:column;gap:3px}
.cal-cell:nth-child(7n){border-right:0}
.cal-wk:last-child .cal-cell{border-bottom:0}
.cal-cell.fora{background:rgba(255,255,255,.012)}
.cal-cell.fora .cal-num{color:#4a4a4c}
.cal-head{display:flex;align-items:center;justify-content:space-between}
.cal-num{font-size:.8rem;color:var(--txt-mut);font-variant-numeric:tabular-nums;line-height:1}
.cal-cell.hoje .cal-num{background:var(--verde);color:var(--sobre-verde);font-weight:800;width:20px;height:20px;border-radius:50%;display:inline-flex;align-items:center;justify-content:center}
.cal-count{font-size:.62rem;font-weight:700;color:var(--txt-mut);background:var(--card-2);border:1px solid var(--borda);border-radius:20px;padding:0 6px;line-height:1.4}
.cal-cell.tem-evento,.cal-cell.clicavel{cursor:pointer}
.cal-cell.tem-evento:hover,.cal-cell.clicavel:hover{background:var(--card-2)}
.cal-cell.tem-evento:focus-visible,.cal-cell.clicavel:focus-visible{outline:2px solid var(--verde);outline-offset:-2px}
.evs{display:flex;flex-direction:column;gap:2px;min-width:0}
.ev-line{display:flex;align-items:center;gap:4px;min-width:0}
.ev-line .dot{width:6px;height:6px;border-radius:50%;flex:0 0 6px}
.ev-line .h{font-size:.6rem;font-weight:700;color:var(--txt-mut);flex:0 0 auto;font-variant-numeric:tabular-nums}
.ev-line .n{font-size:.62rem;color:var(--txt);flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.ev-more{font-size:.58rem;color:var(--txt-mut);padding-left:10px}
/* MARCA DE ESTADO — a barra da esquerda responde "essa data já é minha?", que é a
   pergunta que vem antes de "que tipo de compromisso é" (a bolinha, que fica).
   Forma, não cor: cheia = fixado, pontilhada = segurado. Quem não distingue verde
   de âmbar enxerga a diferença do mesmo jeito. */
.cal.marca-estado .ev-line{padding-left:6px;position:relative}
.cal.marca-estado .ev-line::before{content:"";position:absolute;left:0;top:1px;bottom:1px;width:3px;
                 border-radius:2px;background:var(--verde)}
.cal.marca-estado .ev-line.pre::before{background:repeating-linear-gradient(180deg,var(--ambar) 0 3px,transparent 3px 6px)}
.ev-line.pre .n,.ev-line.pre .h{color:var(--ambar)}
.ev-line.pre .dot{background:transparent!important;box-shadow:inset 0 0 0 1.5px var(--ambar)}
.ev-prazo{font-size:.55rem;font-weight:700;flex:0 0 auto;color:var(--ambar);
          font-variant-numeric:tabular-nums}
.ev-prazo.urg{color:var(--coral)}
/* a célula inteira se pinta: é o que se vê do mês sem ler linha nenhuma */
.cal-cell.temseg{border-color:var(--ambar-borda)}
.cal-cell.temseg.urg{border-color:var(--coral-borda)}
/* legenda das marcas */
.leg-mk{display:inline-block;width:3px;height:11px;border-radius:2px;vertical-align:-1px;margin-right:3px}
.leg-fixo{background:var(--verde)}
.leg-seg{background:repeating-linear-gradient(180deg,var(--ambar) 0 3px,transparent 3px 6px)}
/* % RECEBIDO na linha do calendário. Num buffet "quanto já entrou" é tão parte da
   data quanto o horário — duas festas, uma quitada e outra zerada, hoje pareciam
   idênticas. Coral quando tem parcela vencida: é o que exige ação. */
.ev-pg{font-size:.55rem;font-weight:700;flex:0 0 auto;font-variant-numeric:tabular-nums}
.ev-pg.ok{color:var(--verde)} .ev-pg.mid{color:var(--ambar)} .ev-pg.bad{color:var(--coral)}
/* FICHA DO EVENTO na caixa do dia: o que o orçamento vinculado já sabe da festa */
.fic{margin-top:8px;border:1px solid var(--borda);border-radius:10px;overflow:hidden}
.fic-h{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:6px 10px;
       background:var(--card-2);border-bottom:1px solid var(--borda)}
.fic-h .t{font-size:.62rem;font-weight:700;letter-spacing:.07em;text-transform:uppercase;color:var(--txt-mut)}
.fic-h a{font-size:.66rem;color:var(--verde);text-decoration:none;white-space:nowrap}
.fic-g{display:grid;grid-template-columns:repeat(3,1fr);gap:1px;background:var(--borda)}
@media(max-width:560px){.fic-g{grid-template-columns:repeat(2,1fr)}}
.fic-c{background:var(--bg);padding:7px 10px;min-width:0}
.fic-c .k{font-size:.55rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;color:var(--txt-mut)}
.fic-c .v{font-size:.8rem;font-weight:650;margin-top:1px;font-variant-numeric:tabular-nums;
          overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fic-c .v.sm{font-size:.72rem;font-weight:500}
.fic-c a.v{color:var(--verde);text-decoration:none;display:block}
.fic-b{background:var(--bg);padding:7px 10px;border-top:1px solid var(--borda)}
.fic-b .k{font-size:.55rem;font-weight:700;letter-spacing:.06em;text-transform:uppercase;
          color:var(--txt-mut);margin-bottom:5px}
.fchip{display:inline-block;font-size:.66rem;padding:.14rem .5rem;border-radius:6px;
       background:var(--card-2);border:1px solid var(--borda);margin:0 4px 4px 0}
.fchip .q{color:var(--txt-mut);font-variant-numeric:tabular-nums}
.fbar{height:6px;border-radius:99px;background:#1c2a23;overflow:hidden;margin:5px 0 4px}
.fbar i{display:block;height:100%;background:var(--verde)}
.flin{display:flex;justify-content:space-between;gap:8px;font-size:.68rem;color:var(--txt-mut);
      font-variant-numeric:tabular-nums}
.flin b{color:var(--txt)}
.falerta{display:flex;gap:8px;align-items:flex-start;font-size:.72rem;line-height:1.45;
         border-radius:8px;padding:7px 9px;margin-top:7px;
         background:var(--coral-fundo);border:1px solid var(--coral-borda);color:#f0c2be}
.falerta b{color:var(--coral)}
.falerta a{color:var(--coral);font-weight:650}
.falerta.amb{background:var(--ambar-fundo);border-color:var(--ambar-borda);color:#e6c98d}
.falerta.amb b,.falerta.amb a{color:var(--ambar)}
/* próximos */
.px{display:flex;flex-direction:column;gap:2px}
.px-row{display:flex;align-items:flex-start;gap:11px;padding:9px 4px;border-bottom:1px solid var(--borda)}
.px-row:last-child{border-bottom:0}
.px-when{flex:0 0 46px;text-align:center;line-height:1.15}
.px-when .d{font-size:.72rem;color:var(--txt-mut);font-variant-numeric:tabular-nums}
.px-when .h{font-size:.92rem;font-weight:700;font-variant-numeric:tabular-nums}
.px-body{flex:1;min-width:0}
.px-body .tt{font-size:.92rem;font-weight:600}
.px-body .mt{font-size:.74rem;color:var(--txt-mut);margin-top:1px}
.px-dot{width:8px;height:8px;border-radius:50%;margin-top:6px;flex:0 0 8px}
.d-pessoal{background:var(--verde-claro)}.d-empresa{background:#3987e5}.d-fornecedor{background:var(--ambar)}
.px-x{width:auto;margin:0;background:none;border:0;color:var(--txt-mut);cursor:pointer;font-size:1rem;line-height:1;padding:2px 4px;border-radius:6px}
.px-x:hover{color:#f0917f;background:rgba(224,87,79,.12)}
.px-actions{display:flex;gap:2px;flex:0 0 auto}
.px-rm{width:auto;margin:0;background:none;border:0;color:var(--txt-mut);cursor:pointer;font-size:1rem;line-height:1;padding:2px 4px;border-radius:6px}
.px-rm:hover{color:var(--verde-claro);background:rgba(29,158,117,.12)}
.remarcar-box{display:none;margin-top:8px;padding:10px 11px;background:var(--card-2);border:1px solid var(--borda);border-radius:10px}
.remarcar-box.show{display:block}
.remarcar-box .rlbl{font-size:.7rem;font-weight:700;letter-spacing:.03em;text-transform:uppercase;color:var(--txt-mut);margin-bottom:7px}
.remarcar-row2{display:flex;gap:8px}
.remarcar-row2 input{flex:1;min-width:0;margin:0}
.remarcar-box .tg{padding:9px 0 0;margin-top:9px;border-top:1px dashed var(--borda);border-bottom:0}
.remarcar-actions{display:flex;gap:8px;margin-top:10px}
.rbtn{border:0;border-radius:8px;padding:.5rem .8rem;font-size:.8rem;font-weight:700;cursor:pointer}
.rbtn.ok{background:var(--verde);color:var(--sobre-verde)}
.rbtn.ok:hover{background:var(--verde-hover)}
.rbtn.cc{background:transparent;border:1px solid var(--borda);color:var(--txt-mut)}
.px-add{width:auto;margin:0;background:none;border:0;color:var(--txt-mut);cursor:pointer;font-size:1rem;line-height:1;padding:2px 4px;border-radius:6px}
.px-add:hover{color:var(--verde-claro);background:rgba(29,158,117,.12)}
.add-conv-box{display:none;margin-top:8px;padding:10px 11px;background:var(--card-2);border:1px solid var(--borda);border-radius:10px}
.add-conv-box.show{display:block}
.add-conv-box .rlbl{font-size:.7rem;font-weight:700;letter-spacing:.03em;text-transform:uppercase;color:var(--txt-mut);margin-bottom:7px}
.add-conv-row{display:flex;gap:8px}
.add-conv-row input{flex:1;min-width:0;margin:0}
.add-conv-actions{display:flex;gap:8px;margin-top:10px}
.px-vazio{color:var(--txt-mut);font-size:.86rem;padding:8px 2px}
/* formulários / cards laterais */
.side-cards{display:flex;flex-direction:column;gap:18px}
.frm label{display:block;font-size:.74rem;color:var(--txt-mut);margin:10px 0 4px;font-weight:600}
.frm input,.frm select,.frm textarea{width:100%;background:var(--card-2);border:1px solid var(--borda);border-radius:9px;color:var(--txt);padding:.55rem .6rem;font-size:.92rem;font-family:inherit}
.frm textarea{resize:vertical;min-height:58px;line-height:1.4}
.frm input:focus,.frm select:focus,.frm textarea:focus{outline:0;border-color:var(--verde)}
/* busca de endereço (campo Local) */
.addr-wrap{position:relative}
.addr-input-row{position:relative}
.addr-input-row input{padding-right:34px}
.addr-ic{position:absolute;right:10px;top:50%;transform:translateY(-50%);font-size:.9rem;color:var(--txt-mut);pointer-events:none}
.addr-drop{position:absolute;left:0;right:0;top:calc(100% + 4px);background:var(--card);border:1px solid var(--borda);border-radius:10px;box-shadow:0 16px 40px rgba(0,0,0,.5);z-index:12;overflow:hidden;display:none}
.addr-drop.show{display:block}
.addr-opt{display:flex;gap:10px;align-items:flex-start;padding:9px 11px;cursor:pointer;border-bottom:1px solid var(--borda)}
.addr-opt:last-child{border-bottom:0}
.addr-opt:hover{background:var(--card-2)}
.addr-opt .pin{flex:0 0 auto;font-size:.85rem;margin-top:1px;color:var(--verde-claro)}
.addr-opt .tt{font-size:.85rem;font-weight:600}
.addr-opt .ad{font-size:.72rem;color:var(--txt-mut);margin-top:1px}
.addr-empty{padding:10px 11px;font-size:.78rem;color:var(--txt-mut)}
.addr-picked{display:flex;align-items:flex-start;gap:9px;background:rgba(29,158,117,.1);border:1px solid var(--verde);border-radius:9px;padding:9px 10px;margin-top:6px;flex-wrap:wrap}
.addr-picked .chk{flex:0 0 auto;color:var(--verde-claro);font-size:.95rem;margin-top:1px}
.addr-picked .body{flex:1;min-width:160px}
.addr-picked .nm{font-size:.86rem;font-weight:700}
.addr-picked .ed{font-size:.74rem;color:var(--txt-mut);margin-top:1px}
.addr-picked .x{flex:0 0 auto;background:transparent;border:0;color:var(--txt-mut);cursor:pointer;font-size:.9rem;padding:2px}
.addr-picked .x:hover{color:#f0917f}
.addr-actions{width:100%;display:flex;justify-content:flex-end;margin-top:2px;position:relative}
.send-btn{display:inline-flex;align-items:center;gap:6px;background:var(--verde);color:#04160e;border:0;border-radius:8px;padding:.36rem .65rem;font-size:.76rem;font-weight:700;cursor:pointer}
.send-btn:hover{background:#2ee578}
.hint-line{font-size:.7rem;color:var(--txt-mut);margin-top:6px;min-height:1em}
.manual-toggle{display:inline-flex;align-items:center;gap:5px;background:transparent;border:0;color:var(--txt-mut);cursor:pointer;font-size:.74rem;padding:5px 0;margin-top:2px;text-decoration:underline;text-decoration-color:var(--borda);text-underline-offset:3px}
.manual-toggle:hover{color:var(--verde-claro);text-decoration-color:var(--verde-claro)}
.manual-box{display:none;margin-top:6px;padding-top:10px;border-top:1px dashed var(--borda)}
.manual-box.show{display:block}
.manual-box .mlabel{font-size:.72rem;color:var(--txt-mut);margin-bottom:5px}
.manual-cancel{display:inline-flex;align-items:center;gap:4px;background:transparent;border:0;color:var(--txt-mut);cursor:pointer;font-size:.72rem;padding:6px 0 0;text-decoration:underline;text-decoration-color:var(--borda);text-underline-offset:3px}
.manual-cancel:hover{color:var(--verde-claro);text-decoration-color:var(--verde-claro)}
.local-alt-row{display:flex;flex-wrap:wrap;gap:2px 16px}
.online-toggle{display:inline-flex;align-items:center;gap:5px;background:transparent;border:0;color:var(--txt-mut);cursor:pointer;font-size:.74rem;padding:5px 0;margin-top:2px;text-decoration:underline;text-decoration-color:var(--borda);text-underline-offset:3px}
.online-toggle:hover{color:var(--verde-claro);text-decoration-color:var(--verde-claro)}
.online-box{display:none;margin-top:6px;padding-top:10px;border-top:1px dashed var(--borda)}
.online-box.show{display:block}
.online-box .omsg{font-size:.78rem;color:var(--verde-claro);background:rgba(29,158,117,.1);border:1px solid rgba(29,158,117,.3);border-radius:8px;padding:8px 10px}
.online-box .link-lbl{font-size:.72rem;color:var(--txt-mut);margin:10px 0 5px;font-weight:600}
.online-box .link-input{width:100%;margin:0}
.online-box .link-hint{font-size:.7rem;color:var(--txt-mut);margin-top:5px;line-height:1.5}
.pop{position:absolute;top:calc(100% + 8px);right:0;width:270px;background:var(--card);border:1px solid var(--verde);border-radius:12px;padding:13px;box-shadow:0 20px 50px rgba(0,0,0,.5);z-index:20;display:none}
.pop.show{display:block}
.pop:before{content:"";position:absolute;top:-6px;right:16px;width:11px;height:11px;background:var(--card);border-left:1px solid var(--verde);border-top:1px solid var(--verde);transform:rotate(45deg)}
.pop h4{margin:0 0 8px;font-size:.8rem;color:var(--verde-claro)}
.pop-msg{background:var(--card-2);border:1px solid var(--borda);border-radius:9px;padding:8px 9px;font-size:.73rem;color:var(--txt-mut);line-height:1.55;margin-bottom:10px;white-space:pre-wrap}
.pop-msg b{color:var(--txt)}
.pop-go{display:flex;align-items:center;justify-content:center;gap:7px;width:100%;background:var(--verde);color:#04160e;border:0;border-radius:9px;padding:.5rem;font-weight:700;font-size:.8rem;cursor:pointer;text-decoration:none}
.pop-go:hover{background:#2ee578}
.frm .row2{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.frm .row2>div{min-width:0}
.frm .segs{display:flex;gap:6px;margin-top:4px}
.frm .segs label{flex:1;margin:0}
.frm .segs input{position:absolute;opacity:0;pointer-events:none}
.frm .segs span{display:block;text-align:center;border:1px solid var(--borda);border-radius:8px;padding:.5rem 0;font-size:.82rem;color:var(--txt-mut);cursor:pointer}
.frm .segs input:checked+span{border-width:2px;font-weight:700;color:var(--txt)}
.frm .segs .s-pessoal input:checked+span{border-color:var(--verde-claro)}
.frm .segs .s-empresa input:checked+span{border-color:#3987e5}
.frm .segs .s-fornecedor input:checked+span{border-color:var(--ambar)}
.frm button.ok{margin-top:14px;width:100%;background:var(--verde);color:var(--sobre-verde);font-weight:700;border:0;border-radius:10px;padding:.62rem;font-size:.92rem;cursor:pointer}
.frm button.ok:hover{background:var(--verde-hover)}
/* toggles do lembrete */
.tg{display:flex;align-items:center;justify-content:space-between;gap:12px;padding:10px 0;border-bottom:1px solid var(--borda)}
.tg:last-of-type{border-bottom:0}
.tg .tg-t{font-size:.9rem}
.tg .tg-s{font-size:.72rem;color:var(--txt-mut);margin-top:1px}
.sw{position:relative;width:42px;height:24px;flex:0 0 42px}
.sw input{position:absolute;opacity:0;width:100%;height:100%;margin:0;cursor:pointer;z-index:2}
.sw .track{position:absolute;inset:0;background:var(--card-2);border:1px solid var(--borda);border-radius:12px;transition:.18s}
.sw .knob{position:absolute;top:3px;left:3px;width:18px;height:18px;background:#8a8a86;border-radius:50%;transition:.18s}
.sw input:checked~.track{background:rgba(29,158,117,.32);border-color:var(--verde)}
.sw input:checked~.knob{left:21px;background:var(--verde-claro)}
.sub-opt{padding:8px 0 2px;display:flex;align-items:center;gap:8px;font-size:.82rem;color:var(--txt-mut)}
.sub-opt select,.sub-opt input{width:auto;background:var(--card-2);border:1px solid var(--borda);border-radius:8px;color:var(--txt);padding:.35rem .5rem;font-size:.84rem;font-family:inherit}
.canal-tag{display:inline-flex;align-items:center;gap:5px;font-size:.76rem;color:var(--txt-mut);margin-top:10px}
/* sincronizar */
.feed{display:flex;align-items:center;gap:8px;margin:2px 0 10px}
.feed input{flex:1;min-width:0;margin:0;background:var(--card-2);border:1px solid var(--borda);border-radius:9px;color:var(--txt);padding:.5rem .6rem;font-size:.8rem;font-family:var(--mono)}
.feed button{width:auto;margin:0;flex:0 0 auto;background:var(--card-2);border:1px solid var(--borda);border-radius:9px;color:var(--txt);padding:.5rem 14px;cursor:pointer;font-size:.84rem}
.feed button:hover{border-color:var(--verde)}
.sync-steps{list-style:none;padding:0;margin:8px 0 0;display:flex;flex-direction:column;gap:9px}
.sync-steps li{font-size:.82rem;color:var(--txt-mut);line-height:1.4}
.sync-steps b{color:var(--txt)}
.ag-aviso{background:rgba(29,158,117,.14);border:1px solid var(--verde);color:#bfeeda;border-radius:10px;padding:.6rem .8rem;font-size:.88rem;margin-bottom:14px}
.hint{font-size:.78rem;color:var(--txt-mut);margin-top:8px;line-height:1.45}
/* card de compartilhar convite */
.share{background:linear-gradient(180deg,rgba(29,158,117,.10),var(--card));border:1px solid var(--verde);border-radius:14px;padding:16px 18px;margin-bottom:18px}
.share h2{font-size:.95rem;margin:0 0 4px;color:var(--txt)}
.share p{font-size:.86rem;color:var(--txt-mut);margin:0 0 12px}
.share p b{color:var(--txt)}
.share-list{display:flex;flex-direction:column;gap:8px}
.share-row{display:flex;align-items:center;gap:10px;background:var(--card-2);border:1px solid var(--borda);border-radius:10px;padding:8px 10px}
.sr-av{width:30px;height:30px;border-radius:50%;background:#333;display:flex;align-items:center;justify-content:center;font-size:.82rem;font-weight:700;flex:0 0 30px}
.sr-who{flex:1;min-width:0}
.sr-who b{font-size:.9rem;display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.sr-who small{font-size:.72rem;color:var(--txt-mut)}
.sr-wa{background:var(--verde);color:#04160e;border:0;border-radius:8px;padding:.42rem .7rem;font-size:.8rem;font-weight:700;cursor:pointer;text-decoration:none;white-space:nowrap}
.sr-wa:hover{background:#2ee578}
.sr-za{width:auto;margin:0;background:var(--verde);color:var(--sobre-verde);border:0;border-radius:8px;padding:.42rem .7rem;font-size:.8rem;font-weight:700;cursor:pointer;white-space:nowrap}
.sr-za:hover{background:var(--verde-hover)}
.sr-za:disabled{background:var(--card-2);color:var(--txt-mut);border:1px solid var(--borda);cursor:default;opacity:.6}
.sr-za:disabled:hover{background:var(--card-2)}
.share-row form{margin:0;flex:0 0 auto}
.sr-cp{width:auto;margin:0;background:var(--card);border:1px solid var(--borda);color:var(--txt);border-radius:8px;padding:.42rem .55rem;cursor:pointer;font-size:.86rem}
.sr-cp:hover{border-color:var(--verde)}
/* convidado / bloco no form */
.gconv{margin-top:14px;padding-top:14px;border-top:1px dashed var(--borda)}
.gconv .gt{font-size:.82rem;color:var(--verde-claro);font-weight:700}
.gconv .gd{font-size:.74rem;color:var(--txt-mut);margin:1px 0 8px}
.guest-row{display:grid;grid-template-columns:1fr 1fr 42px;gap:8px;align-items:stretch;margin-bottom:8px}
.guest-row>div{min-width:0}
.guest-row input{margin:0}
.guest-row .g-rm{margin:0;padding:0;height:auto;display:flex;align-items:center;justify-content:center;background:transparent;border:1px solid var(--borda);color:var(--txt-mut);border-radius:9px;cursor:pointer;font-size:1rem;line-height:1}
.guest-row .g-rm:hover{border-color:var(--coral);color:#f0917f}
.g-add{margin-top:2px;background:transparent;border:1px dashed var(--borda);color:var(--verde-claro);border-radius:9px;padding:.5rem;width:100%;cursor:pointer;font-weight:600;font-size:.84rem}
.g-add:hover{border-color:var(--verde)}
/* status de convidados nos próximos */
.px-conv{display:flex;flex-wrap:wrap;gap:5px;margin-top:5px}
.cgrp{font-size:.7rem;font-weight:700;padding:2px 9px;border-radius:20px;white-space:nowrap;background:rgba(57,135,229,.14);color:#bcd8f6;border:1px solid rgba(57,135,229,.4)}
.cgrp-ok{background:rgba(29,158,117,.16);color:var(--verde-claro);border-color:var(--verde)}
.cpill{font-size:.68rem;font-weight:700;padding:2px 8px;border-radius:20px;white-space:nowrap;display:inline-flex;align-items:center;gap:4px;max-width:100%;overflow:hidden;text-overflow:ellipsis}
.cp-pendente{background:rgba(224,163,62,.14);color:var(--ambar);border:1px solid rgba(224,163,62,.4)}
.cp-confirmado{background:rgba(29,158,117,.16);color:var(--verde-claro);border:1px solid var(--verde)}
.cp-remarcar{background:rgba(57,135,229,.14);color:#bcd8f6;border:1px solid rgba(57,135,229,.4)}
.cp-recusado{background:rgba(224,87,79,.14);color:#f0917f;border:1px solid rgba(224,87,79,.4)}
/* caixa do dia (clique numa célula do calendário) */
.day-overlay{position:fixed;inset:0;background:rgba(4,6,5,.6);display:flex;align-items:center;justify-content:center;padding:24px;z-index:60;opacity:0;pointer-events:none;transition:opacity .16s}
.day-overlay.show{opacity:1;pointer-events:auto}
.daybox{width:100%;max-width:420px;max-height:82vh;overflow-y:auto;background:var(--card);border:1px solid var(--borda);border-radius:16px;padding:20px;transform:translateY(8px);transition:transform .16s;box-shadow:0 24px 60px rgba(0,0,0,.5)}
.day-overlay.show .daybox{transform:translateY(0)}
.daybox-hd{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;margin-bottom:4px}
.daybox-hd h3{font-size:1.02rem;margin:0;letter-spacing:-.005em;text-transform:capitalize;font-weight:700}
.daybox-hd .x{background:transparent;border:1px solid var(--borda);color:var(--txt-mut);width:28px;height:28px;border-radius:8px;cursor:pointer;font-size:.92rem;flex:0 0 28px}
.daybox-hd .x:hover{border-color:var(--verde);color:var(--txt)}
.daybox-sub{font-size:.78rem;color:var(--txt-mut);margin:0 0 14px}
.dev{display:flex;gap:11px;padding:10px 0;border-bottom:1px solid var(--borda)}
.dev:last-of-type{border-bottom:0;padding-bottom:2px}
.dev-dot{width:8px;height:8px;border-radius:50%;margin-top:6px;flex:0 0 8px}
.dev-body{flex:1;min-width:0}
.dev-hora{font-size:.72rem;color:var(--txt-mut);font-variant-numeric:tabular-nums;font-weight:700}
.dev-tt{font-size:.92rem;font-weight:600;margin-top:1px}
.dev-meta{display:flex;flex-wrap:wrap;gap:8px;font-size:.76rem;color:var(--txt-mut);margin-top:4px}
.dev-desc{font-size:.8rem;color:var(--txt-mut);margin-top:6px;line-height:1.45;background:var(--card-2);border:1px solid var(--borda);border-radius:8px;padding:7px 9px}
.dev-pre{font-size:.78rem;color:#e6c98d;margin-top:6px;line-height:1.45;background:var(--ambar-fundo);border:1px dashed var(--ambar-borda);border-radius:8px;padding:7px 9px}
.dev-pre b{color:var(--ambar)}
/* na caixa do dia o ponto do tipo também fica vazado quando a data é segurada */
.dev-dot.pre-dot{background:transparent!important;box-shadow:inset 0 0 0 2px var(--ambar)}
.dev-pre.urg{background:var(--coral-fundo);border-color:var(--coral-borda);color:#f0c2be}
.dev-pre.urg b{color:var(--coral)}
/* ações da data segurada: firmar, soltar, ver de onde veio */
.segacts{display:flex;gap:6px;flex-wrap:wrap;margin-top:8px}
.segacts form{display:inline}
.sbtn{font-size:.7rem;font-weight:650;border-radius:7px;padding:.34rem .62rem;cursor:pointer;
      border:1px solid var(--borda);background:transparent;color:var(--txt);width:auto;margin:0}
.sbtn.ok{background:var(--verde);border-color:var(--verde);color:var(--sobre-verde)}
.sbtn.amb{border-color:var(--ambar-borda);color:var(--ambar)}
.sbtn.gh{color:var(--txt-mut);text-decoration:none;display:inline-block}
/* card das datas seguradas */
.segrow{display:grid;grid-template-columns:3px 1fr auto;gap:9px;align-items:center;
        padding:8px 0;border-top:1px dashed var(--borda)}
.segrow:first-of-type{border-top:0;padding-top:2px}
.segbar{align-self:stretch;border-radius:2px;
        background:repeating-linear-gradient(180deg,var(--ambar) 0 3px,transparent 3px 6px)}
.segrow.urg .segbar{background:repeating-linear-gradient(180deg,var(--coral) 0 3px,transparent 3px 6px)}
.segrow .stt{font-size:.82rem;font-weight:600}
.segrow .smt{font-size:.68rem;color:var(--txt-mut)}
.segrow .srt{text-align:right}
.segrow .srt .v{font-size:.74rem;font-weight:700;color:var(--ambar);font-variant-numeric:tabular-nums}
.segrow.urg .srt .v{color:var(--coral)}
.segrow .srt .s{font-size:.62rem;color:var(--txt-mut);font-variant-numeric:tabular-nums}
.segcnt{font-size:.62rem;font-weight:700;color:var(--ambar);background:var(--ambar-fundo);
        border:1px solid var(--ambar-borda);border-radius:999px;padding:.1rem .45rem}
/* CONFIRMADAS: sólido onde a segurada é tracejada. O par sólido/tracejado é o
   mesmo que separa firme de provisório no calendário e no funil — quem não
   distingue cor lê pela forma. */
.segrow.ok .segbar{background:var(--verde)}
.segrow.ok .srt .v{color:var(--verde-claro)}
/* as duas abas do card de datas */
.dt-abas{display:flex;gap:4px;background:var(--card-2);border:1px solid var(--borda);
         border-radius:10px;padding:3px;margin-bottom:10px}
.dt-aba{flex:1;display:flex;align-items:center;justify-content:center;gap:6px;
        border:0;background:transparent;border-radius:8px;padding:.4rem .5rem;cursor:pointer;
        font-family:inherit;font-size:.78rem;font-weight:600;color:var(--txt-mut);width:auto;margin:0}
.dt-aba.on{background:var(--card);color:var(--txt);box-shadow:0 1px 0 rgba(0,0,0,.35)}
.dt-aba:focus-visible{outline:2px solid var(--verde);outline-offset:1px}
.dt-aba .c{font-size:.66rem;font-weight:700;border-radius:5px;padding:.02rem .32rem;
           background:var(--borda);color:var(--txt-mut);font-variant-numeric:tabular-nums}
.dt-aba.on .c.seg{background:var(--ambar-fundo);color:var(--ambar);border:1px solid var(--ambar-borda)}
.dt-aba.on .c.con{background:var(--neon-fundo);color:var(--verde-claro);border:1px solid var(--neon-borda)}
/* choque de horário no formulário */
.choque{display:none;gap:8px;align-items:flex-start;font-size:.74rem;color:#f0c2be;
        background:var(--coral-fundo);border:1px solid var(--coral-borda);border-radius:9px;
        padding:8px 10px;line-height:1.45;margin-top:2px}
.choque.on{display:flex}
.choque b{color:var(--coral)}
/* "só segurar a data" */
.segbox{display:none;flex-direction:column;gap:8px;border:1px dashed var(--ambar-borda);
        background:var(--ambar-fundo);border-radius:9px;padding:9px 10px;margin-top:2px}
.segbox.on{display:flex}
.segbox .sl{font-size:.66rem;font-weight:700;letter-spacing:.05em;text-transform:uppercase;color:var(--ambar)}
.segbox .sh{font-size:.68rem;color:var(--txt-mut);line-height:1.45}
.dev-conv{margin-top:9px;padding-top:9px;border-top:1px dashed var(--borda)}
.dev-conv-lbl{font-size:.66rem;font-weight:700;letter-spacing:.04em;text-transform:uppercase;color:var(--txt-mut);margin-bottom:6px;display:flex;align-items:center;justify-content:space-between;gap:8px}
.dev-conv-add{font-size:.66rem;font-weight:700;letter-spacing:0;text-transform:none;color:var(--verde-claro);background:none;border:0;cursor:pointer;padding:0}
.dev-conv-add:hover{text-decoration:underline}
.guest{display:flex;align-items:center;gap:8px;padding:5px 0}
.guest-info{flex:1;min-width:0}
.guest-nome{font-size:.85rem;font-weight:600;color:var(--txt);display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.guest-fone{font-size:.74rem;color:var(--txt-mut);font-variant-numeric:tabular-nums;margin-top:1px}
.guest-wa{flex:0 0 auto;width:30px;height:30px;border-radius:8px;background:var(--verde);color:#04160e;border:0;display:flex;align-items:center;justify-content:center;font-size:.92rem;text-decoration:none;cursor:pointer}
.guest-wa:hover{background:#2ee578}
.tpill{font-size:.64rem;font-weight:700;padding:1px 7px;border-radius:20px;text-transform:uppercase;letter-spacing:.03em}
.tp-pessoal{background:rgba(29,158,117,.16);color:var(--verde-claro)}
.tp-empresa{background:rgba(57,135,229,.16);color:#bcd8f6}
.tp-fornecedor{background:rgba(224,163,62,.16);color:#f0d9a6}
.dev-top{display:flex;align-items:flex-start;justify-content:space-between;gap:8px}
.dev-desf{margin-top:9px;padding-top:9px;border-top:1px dashed var(--borda)}
.desf-ask{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:.78rem;color:var(--txt-mut)}
.desf-btn{border:1px solid var(--borda);background:var(--card-2);color:var(--txt);border-radius:7px;padding:.32rem .6rem;font-size:.76rem;font-weight:600;cursor:pointer}
.desf-btn.ok:hover{border-color:var(--verde);color:var(--verde-claro)}
.desf-btn.nao:hover{border-color:var(--coral);color:#f0917f}
.desf-badge{font-size:.76rem;font-weight:700;padding:3px 9px;border-radius:20px;display:inline-block}
.desf-ok{background:rgba(29,158,117,.16);color:var(--verde-claro)}
.desf-nao{background:rgba(224,87,79,.14);color:#f0917f}
.reuse-sec{margin-top:14px;padding-top:14px;border-top:1px dashed var(--borda)}
.reuse-lbl{font-size:.7rem;font-weight:700;letter-spacing:.03em;text-transform:uppercase;color:var(--txt-mut);margin-bottom:8px;display:flex;align-items:center;gap:6px}
.reuse-card{display:flex;align-items:center;gap:10px;background:var(--card-2);border:1px solid var(--borda);border-radius:10px;padding:9px 11px;margin-bottom:7px}
.reuse-card form{margin:0;flex:0 0 auto}
.reuse-info{flex:1;min-width:0}
.reuse-tt{font-size:.85rem;font-weight:600}
.reuse-mt{font-size:.72rem;color:var(--txt-mut);margin-top:1px;display:flex;align-items:center;gap:6px;flex-wrap:wrap}
.reuse-nr-tag{font-size:.62rem;font-weight:700;padding:1px 7px;border-radius:20px;background:rgba(217,154,58,.16);color:var(--ambar);border:1px solid rgba(217,154,58,.4)}
.reuse-btn{flex:0 0 auto;background:rgba(29,158,117,.14);border:1px solid rgba(29,158,117,.4);color:var(--verde-claro);border-radius:8px;padding:.42rem .65rem;font-size:.76rem;font-weight:700;cursor:pointer;white-space:nowrap}
.reuse-btn:hover{background:rgba(29,158,117,.24)}
.meet-btn{display:inline-flex;align-items:center;gap:6px;margin-top:9px;background:rgba(29,158,117,.14);border:1px solid rgba(29,158,117,.4);color:var(--verde-claro);border-radius:8px;padding:.4rem .7rem;font-size:.78rem;font-weight:700;text-decoration:none}
.meet-btn:hover{background:rgba(29,158,117,.24)}
.daybox-cta{display:block;width:100%;text-align:center;margin-top:14px;background:transparent;border:1px dashed var(--borda);color:var(--verde-claro);border-radius:9px;padding:.6rem;font-size:.82rem;font-weight:600;cursor:pointer}
.daybox-cta:hover{border-color:var(--verde)}
"""

# A folha sai de dentro da página e vira arquivo com cache de um ano. Eram 38 KB
# rebaixados e reinterpretados a cada clique nos nomes e nas setas do mês — ver
# web/estaticos.py.
_CSS_URL = _estaticos.registrar("agenda.css", _CSS_CRU)
_CSS = f'<link rel="stylesheet" href="{_CSS_URL}">'


# O JS da tela sai de dentro da página do mesmo jeito que a folha de estilo.
# Ficam inline só as ~13 linhas de DADOS do mês (os eventos, o mês aberto, a
# hora do servidor) — 692 bytes que mudam a cada carregamento. As outras 761
# linhas são código, não mudam entre um clique e outro, e agora o navegador
# guarda por um ano. A ordem importa: os dados primeiro, porque o código lê
# EVENTOS_DIA assim que carrega.
_JS_CRU = """
function _esc(s){ return (s||'').replace(/[&<>"']/g, function(ch){
  return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[ch]; }); }
function isoTitulo(iso){
  var p = iso.split('-');
  var d = new Date(Number(p[0]), Number(p[1])-1, Number(p[2]));
  return DIAS_EXT_JS[d.getDay()] + ', ' + d.getDate() + ' de ' + MESES_JS[d.getMonth()+1];
}
function remToggleDia(id){ var box=document.getElementById('remBoxDia-'+id); if(box) box.classList.toggle('show'); }
function addConvToggleDia(id){ var box=document.getElementById('addConvBoxDia-'+id); if(box) box.classList.toggle('show'); }
function _addConvBoxHtml(e){
  return '<div class="add-conv-box" id="addConvBoxDia-'+e.id+'">'
    + '<form method="post" action="/painel/agenda/convidado/adicionar">'
    + '<input type="hidden" name="evento_id" value="'+e.id+'">'
    + '<input type="hidden" name="m" value="'+CUR_MES+'">'
    + '<div class="rlbl">＋ Adicionar convidado</div>'
    + '<div class="add-conv-row"><input name="nome" placeholder="Nome" autocomplete="off"><input name="contato" placeholder="(86) 90000-0000" autocomplete="off"></div>'
    + '<div class="add-conv-actions"><button class="rbtn ok" type="submit">Adicionar</button>'
    + '<button class="rbtn cc" type="button" onclick="addConvToggleDia('+e.id+')">Cancelar</button></div>'
    + '</form></div>';
}
function _remarcarBoxHtml(e){
  return '<div class="remarcar-box" id="remBoxDia-'+e.id+'">'
    + '<form method="post" action="/painel/agenda/remarcar">'
    + '<input type="hidden" name="evento_id" value="'+e.id+'">'
    + '<input type="hidden" name="m" value="'+CUR_MES+'">'
    + '<div class="rlbl">🔁 Nova data</div>'
    + '<div class="remarcar-row2"><input type="date" name="data" value="'+e.data_iso+'" required><input type="time" name="hora" value="'+e.hora+'" required></div>'
    + '<div class="tg"><div><div class="tg-t">Avisar os convidados</div><div class="tg-s">Manda a nova data pro mesmo link que já têm</div></div>'
    + '<label class="sw"><input type="checkbox" name="avisar" value="1" checked><span class="track"></span><span class="knob"></span></label></div>'
    + '<div class="remarcar-actions"><button class="rbtn ok" type="submit">Salvar nova data</button>'
    + '<button class="rbtn cc" type="button" onclick="remToggleDia('+e.id+')">Cancelar</button></div>'
    + '</form></div>';
}
function _desfechoHtml(e){
  if(e.desfecho === 'realizado') return '<span class="desf-badge desf-ok">✅ Aconteceu</span>';
  if(e.desfecho === 'nao_realizado') return '<span class="desf-badge desf-nao">❌ Não rolou</span>';
  return '<div class="desf-ask" id="desfAsk-'+e.id+'"><span>Como foi?</span>'
    + '<button type="button" class="desf-btn ok" onclick="marcarDesfecho('+e.id+',\\'realizado\\')">✅ Aconteceu</button>'
    + '<button type="button" class="desf-btn nao" onclick="marcarDesfecho('+e.id+',\\'nao_realizado\\')">❌ Não rolou</button></div>';
}
function marcarDesfecho(id, valor){
  fetch('/painel/agenda/desfecho', {
    method: 'POST', headers: {'Content-Type':'application/x-www-form-urlencoded'},
    body: 'evento_id='+id+'&desfecho='+valor
  }).then(function(r){ return r.json(); }).then(function(d){
    if(!d.ok) return;
    var ask = document.getElementById('desfAsk-'+id);
    if(ask) ask.outerHTML = valor==='realizado'
      ? '<span class="desf-badge desf-ok">✅ Aconteceu</span>'
      : '<span class="desf-badge desf-nao">❌ Não rolou</span>';
    for(var iso in EVENTOS_DIA){
      EVENTOS_DIA[iso].eventos.forEach(function(e){ if(String(e.id)===String(id)) e.desfecho = valor; });
    }
  });
}
function _reaproveitarHtml(iso){
  if(!REAPROVEITAR.length) return '';
  var cards = REAPROVEITAR.map(function(r){
    return '<div class="reuse-card"><div class="reuse-info">'
      + '<div class="reuse-tt">'+_esc(r.titulo)+'</div>'
      + '<div class="reuse-mt"><span class="reuse-nr-tag">Não rolou</span>'
      + ' era '+r.hora_rot+(r.n_convidados?' · 👤 '+r.n_convidados+(r.n_convidados===1?' convidado':' convidados'):'')+'</div></div>'
      + '<form method="post" action="/painel/agenda/remarcar">'
      + '<input type="hidden" name="evento_id" value="'+r.id+'">'
      + '<input type="hidden" name="data" value="'+iso+'">'
      + '<input type="hidden" name="hora" value="'+r.hora+'">'
      + '<input type="hidden" name="avisar" value="1">'
      + '<input type="hidden" name="m" value="'+CUR_MES+'">'
      + '<button class="reuse-btn" type="submit">Usar nesse dia</button></form></div>';
  }).join('');
  return '<div class="reuse-sec"><div class="reuse-lbl">♻️ Reaproveitar um compromisso que não aconteceu</div>'+cards+'</div>';
}
// Bloco da data segurada dentro da caixa do dia: o prazo, o que se espera, e as
// três decisões possíveis. "Sinal recebido" é o MESMO botão do funil (mesma rota
// no servidor, mesma baixa do título na data do pagamento).
// confirm() nomeando o evento SEM aninhar aspas dentro de string JS: o título vem
// do data-attribute do próprio form. A versão inline precisava de três níveis de
// escape (string Python -> template -> atributo HTML -> JS) e quebrava calada.
// FICHA DO EVENTO. Tudo aqui já estava gravado no orçamento; a agenda só não lia.
// No lugar da frase colada ("Orçamento Nº 1 · 100 convidados · aprovado pelo
// cliente"), os campos de verdade — e o pagamento, que num buffet é parte da data.
function _fichaHtml(f){
  if(!f) return '';
  var cel = function(k, v, sm){
    return '<div class="fic-c"><div class="k">'+k+'</div>'
         + '<div class="v'+(sm?' sm':'')+'">'+v+'</div></div>';
  };
  var linhas = '';
  if(f.cliente) linhas += cel('cliente', _esc(f.cliente), true);
  if(f.contato){
    var so = String(f.contato).replace(/\D/g,'');
    var num = so.length>=10 && so.slice(0,2)!=='55' ? '55'+so : so;
    linhas += '<div class="fic-c"><div class="k">whatsapp</div>'
            + '<a class="v sm" href="https://wa.me/'+num+'" target="_blank" rel="noopener">'
            + _esc(f.contato)+'</a></div>';
  }
  if(f.total) linhas += cel('total', _esc(f.total));

  var itens = '';
  if(f.itens && f.itens.length){
    itens = '<div class="fic-b"><div class="k">o que foi contratado</div>'
      + f.itens.map(function(i){
          return '<span class="fchip">'+_esc(i.nome)
               + (i.qtd>1?' <span class="q">×'+i.qtd+'</span>':'')+'</span>';
        }).join('')
      + '</div>';
  }

  // PAGAMENTO. Com contrato fechado existem títulos e o número é recebido de
  // verdade. Só aprovado, o que existe é o PLANO aceito — e dizer "0% recebido" de
  // algo que ninguém cobrou seria mentira com cara de número.
  var pg = '';
  if(f.tem_titulos){
    var pct = f.pct || 0;
    var al = '';
    if(f.prox_valor){
      var urg = f.prox_vencida || (f.prox_dias !== null && f.prox_dias <= 3);
      var quando = f.prox_vencida ? 'venceu em '+_esc(f.prox_venc)
                 : (f.prox_dias === 0 ? 'vence hoje'
                 : (f.prox_dias === 1 ? 'vence amanhã' : 'vence '+_esc(f.prox_venc)));
      al = '<div class="falerta'+(urg?'':' amb')+'"><span>'+(f.prox_vencida?'⚠️':'🗓️')+'</span>'
         + '<div><b>Próxima parcela '+quando+'</b> — '+_esc(f.prox_valor)+'.'
         + (f.vencidas>1?' '+f.vencidas+' parcelas vencidas, '+_esc(f.vencidas_valor)+' no total.':'')
         + ' <a href="/painel/empresa#receber">Ver em contas a receber</a></div></div>';
    }
    pg = '<div class="fic-b"><div class="k">recebido</div>'
       + '<div class="fbar"><i style="width:'+Math.max(0,Math.min(100,pct))+'%"></i></div>'
       + '<div class="flin"><span><b>'+_esc(f.pago)+'</b> de '+_esc(f.cobrado)+'</span>'
       + '<span>'+pct+'%</span></div>' + al + '</div>';
  } else if(f.plano){
    pg = '<div class="fic-b"><div class="k">plano de pagamento</div>'
       + '<div class="flin"><span><b>'+_esc(f.plano)+'</b> combinados</span>'
       + '<span>contrato não fechado</span></div>'
       + '<div class="flin" style="margin-top:4px;font-size:.64rem">Feche o contrato no funil '
       + 'pra virar título a receber.</div></div>';
  }

  var cab = 'orçamento' + (f.numero ? ' nº '+_esc(String(f.numero)) : '')
          + (f.status ? ' · '+_esc(f.status) : '');
  return '<div class="fic"><div class="fic-h"><span class="t">'+cab+'</span>'
       + '<a href="/painel/servicos?abrir='+f.orcamento_id+'">abrir orçamento →</a></div>'
       + (linhas?'<div class="fic-g">'+linhas+'</div>':'')
       + itens + pg + '</div>';
}
function confirmarLiberar(f){
  var t = f.getAttribute('data-titulo') || 'essa data';
  return confirm('Liberar a data de “' + t + '”? Ela volta a ficar disponível.');
}
function _seguradaHtml(e){
  var urg = e.urgente ? ' urg' : '';
  var quanto = e.prazo ? 'Vence em <b>'+_esc(e.prazo)+'</b>' : 'Data segurada';
  var ate = e.pre_ate ? ' ('+_esc(e.pre_ate)+')' : '';
  var sinal = e.sinal ? ' Sinal esperado: <b>'+_esc(e.sinal)+'</b>.' : '';
  var orc = e.orcamento_numero ? ' Orçamento nº '+_esc(String(e.orcamento_numero))+'.' : '';
  var verOrc = e.orcamento_id
    ? '<a class="sbtn gh" href="/painel/servicos?abrir='+e.orcamento_id+'">Ver orçamento</a>' : '';
  return '<div class="dev-pre'+urg+'">⏳ '+quanto+ate+'.'+sinal+orc
    + ' Passando o prazo sem o sinal, a data libera sozinha.'
    + '<div class="segacts">'
    +   '<form method="post" action="/painel/agenda/sinal-recebido">'
    +     '<input type="hidden" name="evento_id" value="'+e.id+'">'
    +     '<input type="hidden" name="m" value="'+MES_ATUAL+'">'
    +     '<button class="sbtn ok" type="submit">Sinal recebido</button></form>'
    +   '<form method="post" action="/painel/agenda/liberar" '
    +      'data-titulo="'+_esc(e.titulo)+'" onsubmit="return confirmarLiberar(this)">'
    +     '<input type="hidden" name="evento_id" value="'+e.id+'">'
    +     '<input type="hidden" name="m" value="'+MES_ATUAL+'">'
    +     '<button class="sbtn amb" type="submit">Liberar a data</button></form>'
    +   verOrc
    + '</div></div>';
}
var AG_DIA_ABERTO = '';
function abrirDia(iso){
  AG_DIA_ABERTO = iso;
  var d = EVENTOS_DIA[iso];
  // mesmo filtro do calendário: abrir o dia embaixo do nome do Rafael e ver o
  // compromisso da Ana seria a tela se contradizendo.
  var evs = (d ? d.eventos : []).filter(agDoFiltro);
  var box = document.getElementById('daybox');
  var html = '<div class="daybox-hd"><h3>'+(d?d.titulo:isoTitulo(iso))+'</h3>'
    + '<button class="x" type="button" onclick="fecharDia()" aria-label="Fechar">✕</button></div>'
    + '<p class="daybox-sub">'+(evs.length?evs.length+(evs.length===1?' compromisso':' compromissos'):'Nada marcado ainda')+'</p>';
  var agora = new Date(AGORA_ISO);
  evs.forEach(function(e){
    var conv = '';
    (e.convidados||[]).forEach(function(g){
      conv += '<div class="guest"><div class="guest-info">'
        + '<div class="guest-nome">'+_esc(g.nome||'Convidado')+' <span class="cpill cp-'+g.status+'">'+_esc(g.status_rot)+'</span></div>'
        + '<div class="guest-fone">'+(g.contato?_esc(g.contato):'<i>sem número</i>')+'</div></div>'
        + (g.wa?'<a class="guest-wa" href="'+g.wa+'" target="_blank" rel="noopener" title="Chamar '+_esc(g.nome||'convidado')+' no WhatsApp">💬</a>':'')
        + '</div>';
    });
    conv = '<div class="dev-conv"><div class="dev-conv-lbl"><span>👤 Convidados</span>'
      + '<button class="dev-conv-add" type="button" onclick="addConvToggleDia('+e.id+')">＋ adicionar</button></div>'
      + conv + _addConvBoxHtml(e) + '</div>';
    var passado = new Date(e.inicio_iso) <= agora;
    var acaoTopo = passado ? '' : '<button class="px-rm" type="button" title="Remarcar" onclick="remToggleDia('+e.id+')">🔁</button>';
    html += '<div class="dev" data-ev="'+e.id+'"><div class="dev-dot d-'+e.tipo+(e.pre?' pre-dot':'')+'"></div><div class="dev-body">'
      + '<div class="dev-top"><div>'
      + '<div class="dev-hora">'+e.hora+'</div>'
      + '<div class="dev-tt"'+(e.pre?' style="color:var(--ambar)"':'')+'>'+e.titulo+'</div>'
      + '<div class="dev-meta">'
      + '<span class="tpill tp-'+e.tipo+'">'+((e.ficha&&e.ficha.tipo)?_esc(e.ficha.tipo):(TPILL[e.tipo]||e.tipo_rot))+'</span>'
      + (e.local?'<span>📍 '+e.local+'</span>':'')
      + ((e.ficha&&e.ficha.convidados)?'<span>👥 '+e.ficha.convidados+' convidados</span>':'')
      + (e.pre?'<span style="color:var(--ambar);font-weight:700">segurada</span>':'')
      + (e.autor?'<span title="quem marcou">👤 '+_esc(e.autor)+'</span>':'')+'</div>'
      + (e.link_online?'<a class="meet-btn" href="'+_esc(e.link_online)+'" target="_blank" rel="noopener">🎥 Entrar na reunião</a>':'')
      + '</div>'+acaoTopo+'</div>'
      // Data segurada: quem abre o dia precisa saber que essa data AINDA não é de
      // ninguém, até quando, e — o que faltava — decidir aqui mesmo. Antes, firmar
      // exigia sair da agenda, abrir Serviços e achar a proposta no funil.
      + (e.pre?_seguradaHtml(e):'')
      + _fichaHtml(e.ficha)
      + (e.descricao&&!e.ficha?'<div class="dev-desc">'+e.descricao+'</div>':'')
      + conv
      + (passado ? '<div class="dev-desf">'+_desfechoHtml(e)+'</div>' : _remarcarBoxHtml(e))
      + '</div></div>';
  });
  html += _reaproveitarHtml(iso);
  html += '<button class="daybox-cta" type="button" onclick="agNovoNoDia(\\''+iso+'\\')">'+CTA_DIA+'</button>';
  box.innerHTML = html;
  document.getElementById('dayOverlay').classList.add('show');
}
function fecharDia(){ AG_DIA_ABERTO=''; document.getElementById('dayOverlay').classList.remove('show'); }
// O filtro por pessoa. Vazio = time inteiro. Mora aqui em cima porque tanto a
// célula do calendário quanto a caixa do dia leem por ele — duas listas
// filtradas por critérios diferentes seriam dois calendários.
var AG_FILTRO = '';
function agDoFiltro(e){
  return AG_FILTRO === '' || String(e.membro_id || '') === AG_FILTRO;
}
// Reconstrói o conteúdo de UMA célula (linhas de compromisso + "+N mais") a partir
// de EVENTOS_DIA — mesma fonte de dados da caixa do dia, pra nunca desalinhar.
function renderizarCelula(iso){
  var cel = document.querySelector('.cal-cell[data-iso="'+iso+'"]');
  if(!cel) return;
  var todos = (EVENTOS_DIA[iso] || {}).eventos || [];
  var evs = todos.filter(agDoFiltro);
  var num = cel.querySelector('.cal-num');
  var numHtml = num ? num.outerHTML : '';
  if(!evs.length){
    cel.classList.remove('tem-evento','temseg','urg');
    // DIA VAZIO PELO FILTRO ≠ DIA VAZIO. Só o segundo perde o `data-iso` (é o
    // caminho de "cancelei o último compromisso do dia"); tirar do primeiro
    // deixaria a célula morta pra sempre — limpar o filtro não a traria de volta.
    if(REAPROVEITAR.length || todos.length){ cel.classList.add('clicavel'); }
    else { cel.removeAttribute('tabindex'); cel.removeAttribute('role'); cel.removeAttribute('data-iso'); }
    cel.innerHTML = '<div class="cal-head">'+numHtml+'</div>';
    return;
  }
  var count = evs.length > 2 ? '<span class="cal-count">'+evs.length+'</span>' : '';
  var linhas = evs.slice(0, 2).map(function(e){
    return '<div class="ev-line'+(e.pre?' pre':'')+'" data-ev="'+e.id+'"'
      + (e.pre?' title="Data segurada — esperando o sinal"':'')
      + '><span class="dot d-'+e.tipo+'"></span><span class="h">'+e.hora+'</span><span class="n">'+e.titulo+'</span>'
      + (e.prazo?'<span class="ev-prazo'+(e.urgente?' urg':'')+'">'+_esc(e.prazo)+'</span>':'')
      + (e.pg?'<span class="ev-pg '+_esc(e.pg_classe||'')+'">'+_esc(e.pg)+'</span>':'')
      + '</div>';
  }).join('');
  var mais = evs.length > 2 ? '<div class="ev-more">+'+(evs.length - 2)+' mais</div>' : '';
  cel.classList.add('tem-evento');
  cel.classList.toggle('temseg', evs.some(function(e){return e.pre;}));
  cel.classList.toggle('urg', evs.some(function(e){return e.pre && e.urgente;}));
  cel.innerHTML = '<div class="cal-head">'+numHtml+count+'</div><div class="evs">'+linhas+'</div>'+mais;
}
// Tira o evento cancelado do calendário (linha na célula + caixa do dia se estiver
// aberta) e do EVENTOS_DIA em memória, sem precisar recarregar a página.
function removerEventoDoCalendario(id){
  var isoAlvo = null;
  for(var iso in EVENTOS_DIA){
    if(EVENTOS_DIA[iso].eventos.some(function(e){ return String(e.id) === String(id); })){ isoAlvo = iso; break; }
  }
  if(isoAlvo){
    EVENTOS_DIA[isoAlvo].eventos = EVENTOS_DIA[isoAlvo].eventos.filter(function(e){ return String(e.id) !== String(id); });
    if(!EVENTOS_DIA[isoAlvo].eventos.length) delete EVENTOS_DIA[isoAlvo];
    renderizarCelula(isoAlvo);
  }
  var overlayAberto = document.getElementById('dayOverlay').classList.contains('show');
  var devRow = document.querySelector('#daybox .dev[data-ev="'+id+'"]');
  if(overlayAberto && devRow && isoAlvo){
    abrirDia(isoAlvo);   // reconstrói (mostra "nada marcado" + reaproveitar se for o caso)
  }
}
function agNovoNoDia(iso){
  fecharDia();
  agNovo(true);
  var i = document.querySelector('#novo input[name=data]');
  if(i) i.value = iso;
}
// Delegado no calendário (não em cada célula): células podem ser recriadas por
// renderizarCelula() depois de um cancelamento, então o listener direto se perderia.
document.querySelector('.cal').addEventListener('click', function(ev){
  var cel = ev.target.closest('.cal-cell[data-iso]');
  if(cel) abrirDia(cel.getAttribute('data-iso'));
});
document.querySelector('.cal').addEventListener('keydown', function(ev){
  if(ev.key!=='Enter' && ev.key!==' ') return;
  var cel = ev.target.closest('.cal-cell[data-iso]');
  if(cel){ ev.preventDefault(); abrirDia(cel.getAttribute('data-iso')); }
});
document.getElementById('dayOverlay').addEventListener('click', function(ev){ if(ev.target.id==='dayOverlay') fecharDia(); });
document.addEventListener('keydown', function(ev){ if(ev.key==='Escape') fecharDia(); });

function agNovo(v){var n=document.getElementById('novo');if(n){n.style.display='';n.scrollIntoView({behavior:'smooth',block:'center'});var i=n.querySelector('input[name=titulo]');if(i)setTimeout(function(){i.focus()},300);}return false;}
function agCopiar(){var i=document.getElementById('feedUrl');if(!i)return;i.select();try{document.execCommand('copy');}catch(e){}if(navigator.clipboard)navigator.clipboard.writeText(i.value);var b=event.target;var t=b.textContent;b.textContent='Copiado ✓';setTimeout(function(){b.textContent=t},1600);}
function cpRow(b){var row=b.closest('.share-row');if(!row)return;var txt=row.getAttribute('data-link')||'';if(navigator.clipboard)navigator.clipboard.writeText(txt);var t=b.textContent;b.textContent='✓';setTimeout(function(){b.textContent=t},1400);}
function addGuest(){var box=document.getElementById('guests');var d=document.createElement('div');d.className='guest-row';d.innerHTML='<div><input class="gnome" name="convidado_nome" placeholder="Nome" autocomplete="off"></div><div><input name="convidado_contato" placeholder="(86) 90000-0000" autocomplete="off"></div><button type="button" class="g-rm" onclick="rmGuest(this)" title="Remover" aria-label="Remover">✕</button>';box.appendChild(d);var i=d.querySelector('input');if(i)i.focus();}
function rmGuest(b){var box=document.getElementById('guests');var row=b.closest('.guest-row');if(box&&box.children.length>1){row.remove();}else{row.querySelectorAll('input').forEach(function(x){x.value='';});}}
function remToggle(id){var box=document.getElementById('remBox-'+id);if(box)box.classList.toggle('show');}

// ---------------- Só segurar a data + choque de horário ----------------
// Duas coisas que faltavam no momento de marcar: poder segurar (em vez de mentir
// marcando firme) e saber que aquele horário já tem coisa. O choque NÃO bloqueia —
// só informa, porque quem sabe se cabe é a empresa.
(function(){
  var chk = document.getElementById('fSegurar');
  var box = document.getElementById('segBox');
  var ate = document.getElementById('fSegAte');
  var dias = PRE_RESERVA_DIAS;
  if(chk && box){
    chk.addEventListener('change', function(){
      box.classList.toggle('on', chk.checked);
      if(chk.checked && ate && !ate.value){
        var d = new Date(); d.setDate(d.getDate() + dias);
        ate.value = d.toISOString().slice(0,10);   // prazo padrão da conta, já preenchido
      }
    });
  }
  var fd = document.getElementById('fData'), fh = document.getElementById('fHora');
  var ff = document.getElementById('fFim');   // só existe no formulário de evento
  var cx = document.getElementById('choqueBox'), ct = document.getElementById('choqueTxt');
  if(!fd || !cx) return;
  var timer = null;
  function checar(){
    if(!fd.value){ cx.classList.remove('on'); return; }
    // manda o FIM junto: sem ele a janela vira 1h e duas festas na mesma noite
    // nunca acusam choque — que é justamente o caso de quem vende data.
    fetch('/painel/agenda/conflitos?data='+encodeURIComponent(fd.value)
          +'&hora='+encodeURIComponent(fh ? fh.value : '')
          +'&fim='+encodeURIComponent(ff ? ff.value : ''))
      .then(function(r){ return r.json(); })
      .then(function(d){
        var itens = (d && d.itens) || [];
        if(!itens.length){ cx.classList.remove('on'); return; }
        var linhas = itens.slice(0,3).map(function(i){
          return _esc(i.titulo)+' ('+_esc(i.quando)+(i.pre?', segurada':'')+')';
        }).join(', ');
        var resto = itens.length > 3 ? ' e mais '+(itens.length-3)+'.' : '.';
        ct.innerHTML = '<b>Esse horário já tem coisa marcada:</b> '+linhas+resto
          +' Dá pra marcar assim mesmo — só confira se cabe.';
        cx.classList.add('on');
      })
      .catch(function(){ cx.classList.remove('on'); });
  }
  function agendar(){ clearTimeout(timer); timer = setTimeout(checar, 350); }
  fd.addEventListener('change', agendar);
  if(fh) fh.addEventListener('change', agendar);
  if(ff) ff.addEventListener('change', agendar);
})();
function addConvToggle(id){var box=document.getElementById('addConvBox-'+id);if(box)box.classList.toggle('show');}

// ---------------- Histórico de envios ----------------
function _histRowHtml(it){
  var motivo = it.motivo_rot ? '<span class="hs-motivo">'+_esc(it.motivo_rot)+'</span>' : '';
  var status = it.ok ? '<span class="hist-status hs-ok">✅ Enviado</span>'
    : '<span class="hist-status hs-fail">❌ Falhou'+motivo+'</span>';
  var retry = it.pode_reenviar ? '<button class="hist-retry" type="button" onclick="histReenviar('+it.id+',this)">🔁 Reenviar</button>' : '';
  var loc = it.evento_local ? '<div class="loc">'+_esc(it.evento_local)+'</div>' : '';
  return '<tr><td class="hist-qd" data-rot="Quando">'+_esc(it.quando_rot)+'</td>'
    + '<td class="hist-compr" data-rot="Compromisso">'+_esc(it.evento_titulo||'—')+loc+'</td>'
    + '<td data-rot="Convidado">'+_esc(it.convidado_rot)+'</td>'
    + '<td data-rot="Tipo"><span class="hist-tipo ht-'+it.tipo+'">'+_esc(it.tipo_rot)+'</span></td>'
    + '<td class="hist-canal" data-rot="Canal">'+_esc(it.canal_rot)+'</td>'
    + '<td data-rot="Status">'+status+'</td><td>'+retry+'</td></tr>';
}
function _histRenderBody(){
  var box = document.getElementById('histBody');
  if(!box) return;
  if(!HIST_STATE.itens.length){
    box.innerHTML = '<div class="hist-vazio" id="histVazio">Nada enviado nos últimos '+HIST_STATE.dias+' dias. 🤷</div>';
    return;
  }
  var ok = HIST_STATE.itens.filter(function(i){return i.ok;}).length;
  var fail = HIST_STATE.itens.length - ok;
  var rows = HIST_STATE.itens.map(_histRowHtml).join('');
  var mais = HIST_STATE.total > HIST_STATE.itens.length
    ? '<div class="hist-foot"><button class="hist-mais" type="button" onclick="histMais()">Ver mais</button></div>' : '';
  box.innerHTML = '<div class="hist-resumo" id="histResumo">'
    + '<span class="hr-ok">✅ <b>'+ok+'</b> enviados</span>'
    + '<span class="hr-fail">❌ <b>'+fail+'</b> falharam</span>'
    + '<span>📨 <b>'+HIST_STATE.total+'</b> no total · últimos '+HIST_STATE.dias+' dias</span></div>'
    + '<div class="hist-tbl-wrap"><table class="hist-tbl"><thead><tr><th>Quando</th><th>Compromisso</th>'
    + '<th>Convidado</th><th>Tipo</th><th>Canal</th><th>Status</th><th></th></tr></thead>'
    + '<tbody id="histTbody">'+rows+'</tbody></table></div>' + mais;
}
function _histQS(offset){
  return 'dias='+HIST_STATE.dias+'&falhas='+(HIST_STATE.falhas?'1':'0')
    +'&q='+encodeURIComponent(HIST_STATE.q)+'&offset='+offset;
}
function _histFetch(reset){
  var offset = reset ? 0 : HIST_STATE.itens.length;
  fetch('/painel/agenda/historico?'+_histQS(offset)).then(function(r){return r.json();}).then(function(d){
    if(!d.ok) return;
    HIST_STATE.itens = reset ? d.itens : HIST_STATE.itens.concat(d.itens);
    HIST_STATE.total = d.total;
    _histRenderBody();
  });
}
function histFiltro(btn){
  document.querySelectorAll('.hist-filtros .hf-btn[data-dias]').forEach(function(b){b.classList.remove('on');});
  btn.classList.add('on');
  HIST_STATE.dias = Number(btn.getAttribute('data-dias'));
  _histFetch(true);
}
function histTab(nome){
  var feito = nome === 'feito';
  document.getElementById('histTabFeito').classList.toggle('on', feito);
  document.getElementById('histTabFila').classList.toggle('on', !feito);
  document.getElementById('histBody').style.display = feito ? '' : 'none';
  document.getElementById('filaBody').style.display = feito ? 'none' : '';
  var filtros = document.querySelector('.hist-filtros');
  if(filtros) filtros.style.display = feito ? '' : 'none';
}
function histToggleFalhas(){
  HIST_STATE.falhas = !HIST_STATE.falhas;
  var b = document.getElementById('histFalhasBtn'); if(b) b.classList.toggle('on', HIST_STATE.falhas);
  _histFetch(true);
}
function histMais(){ _histFetch(false); }
function histReenviar(id, btn){
  btn.disabled = true; var orig = btn.textContent; btn.textContent = '⏳';
  fetch('/painel/agenda/historico/reenviar', {method:'POST',
    headers:{'Content-Type':'application/x-www-form-urlencoded'}, body:'log_id='+id})
    .then(function(r){return r.json();}).then(function(d){
      if(d.ok){ _histFetch(true); }
      else { btn.disabled = false; btn.textContent = orig; zaqToast('Não consegui reenviar agora.', false); }
    }).catch(function(){ btn.disabled = false; btn.textContent = orig; });
}
(function(){
  var q = document.getElementById('histQ');
  if(!q) return;
  var t;
  q.addEventListener('input', function(){
    clearTimeout(t);
    t = setTimeout(function(){ HIST_STATE.q = q.value; _histFetch(true); }, 350);
  });
})();

// ---------------- Local: busca de endereço (Google Places) + manual + enviar ----------------
(function(){
  var addrInput = document.getElementById('addrInput');
  var addrDrop = document.getElementById('addrDrop');
  var addrPicked = document.getElementById('addrPicked');
  var localHidden = document.getElementById('localHidden');
  var hintLine = document.getElementById('hintLine');
  var addrWrap = document.getElementById('addrWrap');
  var searchRow = document.querySelector('.addr-input-row');
  var altRow = document.querySelector('.local-alt-row');
  var manualToggle = document.getElementById('manualToggle');
  var manualBox = document.getElementById('manualBox');
  var manualInput = document.getElementById('manualInput');
  var manualCancel = document.getElementById('manualCancel');
  var onlineToggle = document.getElementById('onlineToggle');
  var onlineBox = document.getElementById('onlineBox');
  var onlineCancel = document.getElementById('onlineCancel');
  var linkOnline = document.getElementById('linkOnline');
  if(!addrInput) return;

  function nomesEnvolvidos(){
    var nomes = [];
    document.querySelectorAll('.gnome').forEach(function(i){ if(i.value.trim()) nomes.push(i.value.trim()); });
    return nomes;
  }
  function fraseEnvolvidos(){
    var n = nomesEnvolvidos();
    if(n.length < 2) return '';
    return n.slice(0, -1).join(', ') + ' e ' + n[n.length - 1];
  }
  function linkMapa(endereco){
    return 'https://www.google.com/maps/search/?api=1&query=' + encodeURIComponent(endereco);
  }

  // Geolocalização: só pede no primeiro foco no campo (não no load da página,
  // pra não estourar o popup de permissão sem contexto). Se negar/não suportar,
  // segue sem — a busca continua igual a antes, só sem o bias de proximidade.
  var geoCoords = null, geoPedido = false;
  addrInput.addEventListener('focus', function(){
    if(geoPedido || !navigator.geolocation) return;
    geoPedido = true;
    navigator.geolocation.getCurrentPosition(function(pos){
      geoCoords = { lat: pos.coords.latitude, lng: pos.coords.longitude };
    }, function(){ /* negado ou indisponível: segue sem bias */ }, { timeout: 6000 });
  });

  var timer = null;
  addrInput.addEventListener('input', function(){
    localHidden.value = addrInput.value;    // digitou solto -> já vale como local, igual antes
    addrPicked.innerHTML = '';
    var termo = addrInput.value.trim();
    clearTimeout(timer);
    if(termo.length < 2){ addrDrop.classList.remove('show'); return; }
    timer = setTimeout(function(){ buscar(termo); }, 320);
  });

  function buscar(termo){
    var url = '/painel/agenda/buscar-local?q=' + encodeURIComponent(termo);
    if(geoCoords) url += '&lat=' + geoCoords.lat + '&lng=' + geoCoords.lng;
    fetch(url)
      .then(function(r){ return r.json(); })
      .then(function(d){
        if(addrInput.value.trim() !== termo) return;   // resposta atrasada de uma busca antiga
        if(!d.ok && d.erro === 'sem_chave'){
          addrDrop.classList.remove('show');
          hintLine.textContent = 'Busca de endereço desligada nesta conta — digite manualmente.';
          return;
        }
        var itens = (d.itens || []);
        if(!itens.length){
          addrDrop.innerHTML = '<div class="addr-empty">Nenhum lugar encontrado pra "'+termo+'". Usa o link "digitar manualmente" abaixo.</div>';
        } else {
          addrDrop.innerHTML = itens.map(function(p, idx){
            return '<div class="addr-opt" data-idx="'+idx+'"><span class="pin">📍</span>'
              + '<div><div class="tt"></div><div class="ad"></div></div></div>';
          }).join('');
          // texto via textContent (não via template) pra não abrir brecha de HTML no nome/endereço do lugar
          Array.prototype.forEach.call(addrDrop.querySelectorAll('.addr-opt'), function(row, idx){
            row.querySelector('.tt').textContent = itens[idx].nome;
            row.querySelector('.ad').textContent = itens[idx].endereco;
            row.addEventListener('click', function(){ escolherLocal(itens[idx]); });
          });
        }
        addrDrop.classList.add('show');
      })
      .catch(function(){ addrDrop.classList.remove('show'); });
  }

  function escolherLocal(p){
    addrInput.value = p.nome;
    localHidden.value = p.nome;
    addrDrop.classList.remove('show');
    hintLine.textContent = 'Endereço confirmado pelo Google — o link do mapa vai junto nas mensagens.';
    var mapa = linkMapa(p.endereco);
    var card = document.createElement('div');
    card.className = 'addr-picked';
    var chk = document.createElement('span'); chk.className = 'chk'; chk.textContent = '✅';
    var body = document.createElement('div'); body.className = 'body';
    var nm = document.createElement('div'); nm.className = 'nm'; nm.textContent = p.nome;
    var ed = document.createElement('div'); ed.className = 'ed'; ed.textContent = p.endereco;
    var lnk = document.createElement('a'); lnk.className = 'lnk'; lnk.href = mapa; lnk.target = '_blank';
    lnk.rel = 'noopener'; lnk.textContent = '🔗 Ver no Google Maps';
    body.appendChild(nm); body.appendChild(ed); body.appendChild(lnk);
    var x = document.createElement('button'); x.type = 'button'; x.className = 'x';
    x.setAttribute('aria-label', 'Trocar endereço'); x.textContent = '✕';
    x.addEventListener('click', function(){
      addrInput.value = ''; localHidden.value = ''; addrPicked.innerHTML = '';
      hintLine.textContent = 'Digite pra buscar — ex: nome do lugar ou endereço.';
      addrInput.focus();
    });
    var actions = document.createElement('div'); actions.className = 'addr-actions';
    var send = document.createElement('button'); send.type = 'button'; send.className = 'send-btn';
    send.textContent = '💬 Enviar pro cliente';
    var pop = document.createElement('div'); pop.className = 'pop';
    send.addEventListener('click', function(){
      var titulo = (document.getElementById('fTitulo').value || 'Compromisso').trim();
      var data = document.getElementById('fData').value, hora = document.getElementById('fHora').value;
      var quando = (data ? data.split('-').reverse().join('/') : '') + (hora ? ' ' + hora : '');
      var frase = fraseEnvolvidos();
      var msgTxt = titulo + '\\n' + quando + ' — ' + p.nome + '\\n📍 ' + mapa + (frase ? '\\n👥 Com: ' + frase : '');
      pop.innerHTML = '';
      var h4 = document.createElement('h4'); h4.textContent = '📍 Enviar pro cliente';
      var box = document.createElement('div'); box.className = 'pop-msg'; box.textContent = msgTxt;
      var go = document.createElement('a'); go.className = 'pop-go'; go.target = '_blank'; go.rel = 'noopener';
      go.href = 'https://wa.me/?text=' + encodeURIComponent(msgTxt); go.textContent = '💬 Abrir WhatsApp';
      pop.appendChild(h4); pop.appendChild(box); pop.appendChild(go);
      pop.classList.toggle('show');
    });
    actions.appendChild(send); actions.appendChild(pop);
    card.appendChild(chk); card.appendChild(body); card.appendChild(x); card.appendChild(actions);
    addrPicked.innerHTML = '';
    addrPicked.appendChild(card);
  }

  function voltarBusca(){
    searchRow.style.display = ''; hintLine.style.display = ''; altRow.style.display = '';
    addrInput.value = ''; localHidden.value = ''; linkOnline.value = '';
  }

  manualToggle.addEventListener('click', function(){
    searchRow.style.display = 'none';
    addrDrop.classList.remove('show');
    addrPicked.innerHTML = '';
    hintLine.style.display = 'none';
    altRow.style.display = 'none';
    manualBox.classList.add('show');
    addrInput.value = ''; localHidden.value = ''; linkOnline.value = '';
    manualInput.focus();
  });
  manualInput.addEventListener('input', function(){ localHidden.value = manualInput.value; });
  manualCancel.addEventListener('click', function(){
    manualBox.classList.remove('show');
    manualInput.value = '';
    voltarBusca();
    addrInput.focus();
  });

  // Reunião online: sem endereço nenhum — fecha a busca e nunca manda link de
  // mapa (nem no convite, nem quando o convidado confirma presença).
  onlineToggle.addEventListener('click', function(){
    searchRow.style.display = 'none';
    addrDrop.classList.remove('show');
    addrPicked.innerHTML = '';
    hintLine.style.display = 'none';
    altRow.style.display = 'none';
    onlineBox.classList.add('show');
    addrInput.value = ''; localHidden.value = 'Online';
  });
  onlineCancel.addEventListener('click', function(){
    onlineBox.classList.remove('show');
    voltarBusca();
    addrInput.focus();
  });

  document.addEventListener('click', function(ev){
    if(!addrWrap.contains(ev.target)) addrDrop.classList.remove('show');
    if(!ev.target.closest('.addr-actions')){
      document.querySelectorAll('.pop.show').forEach(function(p){ p.classList.remove('show'); });
    }
  });
})();
// Feedback instantâneo: ao enviar QUALQUER form, o botão reage na hora (apaga +
// "⏳ …"), enquanto o back processa — pro clique nunca parecer travado. Como a
// maioria recarrega, isso só precisa durar até a navegação; o timeout destrava
// caso algo impeça o envio (ex.: erro de rede). Respeita confirm() cancelado.
document.addEventListener('submit', function(ev){
  if(ev.defaultPrevented) return;                         // ex.: Cancelar -> confirm() = não
  var f=ev.target; if(!f||f.tagName!=='FORM') return;
  if(f.dataset && f.dataset.ajax) return;                 // forms AJAX se gerenciam sozinhos
  var b=f.querySelector('button[type=submit],button:not([type])');
  if(!b||b.disabled) return;
  var busy=b.getAttribute('data-busy');
  b.dataset.orig=b.innerHTML;
  if(busy) b.innerHTML=busy;
  b.classList.add('is-busy'); b.disabled=true;
  setTimeout(function(){ if(b.dataset.orig!=null){ b.innerHTML=b.dataset.orig; b.disabled=false; b.classList.remove('is-busy'); delete b.dataset.orig; } }, 8000);
}, false);

// Toast flutuante
function zaqToast(msg, ok){
  var t=document.createElement('div');
  t.className='zaq-toast'+(ok===false?' err':'');
  t.textContent=msg; document.body.appendChild(t);
  requestAnimationFrame(function(){ t.classList.add('show'); });
  setTimeout(function(){ t.classList.remove('show'); setTimeout(function(){ t.remove(); },300); }, 3400);
}
function zaqVazioProximos(){
  var px=document.querySelector('.px'); if(!px) return;
  if(!px.querySelector('.px-row') && !px.querySelector('.px-vazio')){
    var d=document.createElement('div'); d.className='px-vazio';
    d.textContent='Nada por vir. Marque um compromisso ali em cima. 🎉'; px.appendChild(d);
  }
}
// ---------------- As abas do card de datas ----------------
// Puro DOM: as duas listas já vieram no HTML. Trocar de aba não pede nada ao
// servidor — o card inteiro custa duas consultas, e elas já foram feitas.
(function(){
  var card=document.getElementById('datas-card'); if(!card) return;
  var abas=[].slice.call(card.querySelectorAll('.dt-aba'));
  abas.forEach(function(b){
    b.addEventListener('click', function(){
      abas.forEach(function(o){
        var on=(o===b);
        o.classList.toggle('on', on);
        o.setAttribute('aria-selected', on?'true':'false');
        var pane=document.getElementById(o.dataset.alvo);
        if(pane) pane.hidden=!on;
      });
    });
  });
})();

// ---------------- Filtro por pessoa, sem recarregar ----------------
// ERA UM LINK. Cada clique num nome refazia a página inteira — treze consultas ao
// banco, 139 KB de HTML — pra só então filtrar, em Python, uma lista que o
// servidor já tinha na mão. O mês aberto, a rolagem e o dia expandido se perdiam
// junto: era isso que fazia a tela piscar a cada toque.
//
// O filtro é VISUAL, e todos os dados do mês já estão na página (EVENTOS_DIA).
// Trocar de pessoa não pede nada ao servidor — só redesenha as células pela mesma
// função que a agenda já usava depois de cancelar ou remarcar.
//
// O SERVIDOR PAROU DE FILTRAR. Tinha que parar: se ele mandasse só os eventos do
// Rafael, clicar em "Todos" não teria como trazer os outros de volta sem
// recarregar — que é justamente o que se está tirando. Ele segue lendo o ?p= pra
// marcar qual chip nasce ligado, e o JS aplica antes de a pessoa tocar em nada.
(function(){
  var barra=document.querySelector('.ag-pessoas'); if(!barra) return;
  var chips=[].slice.call(barra.querySelectorAll('.agp'));
  if(!chips.length) return;

  function aplicar(id){
    AG_FILTRO=id;
    Object.keys(EVENTOS_DIA).forEach(renderizarCelula);
    [].slice.call(document.querySelectorAll('.px-row')).forEach(function(el){
      var meu=el.getAttribute('data-membro')||'';
      el.hidden=!(id==='' || meu===id);
    });
    if(typeof zaqVazioProximos==='function') zaqVazioProximos();
    if(AG_DIA_ABERTO) abrirDia(AG_DIA_ABERTO);   // a caixa aberta acompanha
  }

  chips.forEach(function(a){
    a.addEventListener('click', function(ev){
      ev.preventDefault();
      var id=a.dataset.membro||'';
      chips.forEach(function(o){ o.classList.toggle('on', o===a); });
      aplicar(id);
      // a URL acompanha sem recarregar: F5 mantém o filtro e o link colado no
      // WhatsApp continua abrindo a agenda do jeito que a pessoa estava vendo.
      try{
        var u=new URL(window.location.href);
        if(id) u.searchParams.set('p', id); else u.searchParams.delete('p');
        history.replaceState(null, '', u.toString());
      }catch(e){}
    });
  });

  var ligado=barra.querySelector('.agp.on');
  var inicial=(ligado && ligado.dataset.membro) || '';
  if(inicial) aplicar(inicial);
})();

// Ações SEM reload (fetch): Cancelar remove a linha/chip na hora; 📲 Zaq envia e
// dá um toast. Fallback: sem JS, os <form> continuam funcionando por POST normal.
document.addEventListener('submit', function(ev){
  var f=ev.target; if(!f||!f.dataset||!f.dataset.ajax) return;
  if(ev.defaultPrevented) return;                         // confirm() cancelado
  ev.preventDefault();
  var tipo=f.dataset.ajax, btn=f.querySelector('button[type=submit]');
  var orig=btn?btn.innerHTML:'';
  if(btn){ btn.disabled=true; btn.classList.add('is-busy'); if(btn.getAttribute('data-busy')) btn.innerHTML=btn.getAttribute('data-busy'); }
  function restore(){ if(btn){ btn.disabled=false; btn.classList.remove('is-busy'); btn.innerHTML=orig; } }
  fetch(f.action, {method:'POST', headers:{'X-Zaq-Ajax':'1'}, body:new FormData(f)})
    .then(function(r){ return r.json(); })
    .then(function(d){
      if(tipo==='cancelar'){
        if(d && d.ok){
          var id=(f.querySelector('[name=evento_id]')||{}).value;
          var row=f.closest('.px-row'); if(row){ row.classList.add('saindo'); setTimeout(function(){ row.remove(); zaqVazioProximos(); },200); }
          if(id) removerEventoDoCalendario(id);
          zaqToast('Compromisso cancelado.');
        } else { restore(); zaqToast('Não consegui cancelar.', false); }
      } else {   // enviar convite
        if(d && d.ok){ zaqToast(d.msg||'Convite enviado! ✅'); if(btn){ btn.innerHTML='✓ Enviado'; btn.classList.remove('is-busy'); } }
        else { restore(); zaqToast((d && d.msg)||'Não consegui enviar.', false); }
      }
    })
    .catch(function(){ restore(); zaqToast('Erro de conexão — tenta de novo.', false); });
}, false);
"""
_JS_TAG = f'<script src="{_estaticos.registrar("agenda.js", _JS_CRU)}" defer></script>'


_AGENDA_TPL = """{% extends "base" %}{% block conteudo %}""" + _CSS + """
<div class="ag-wrap">
  {% if aviso %}<div class="ag-aviso">{{ aviso }}</div>{% endif %}
  {% if share %}
  <div class="share">
    <h2>✅ {{ share.total }} convite{{ 's' if share.total != 1 }} pronto{{ 's' if share.total != 1 }} pra enviar</h2>
    <p><b>{{ share.titulo }}</b> — {{ share.quando }}. {% if share.auto_on %}Toque em <b>📲 Zaq</b> pra ele mandar o convite sozinho pelo WhatsApp (a pessoa confirma num toque), ou use o link.{% else %}Mande o link de cada um; cada pessoa confirma o seu e você é avisado aqui.{% endif %}</p>
    <div class="share-list">
      {% for g in share.guests %}
      <div class="share-row" data-link="{{ g.url }}">
        <div class="sr-av">{{ (g.nome or '?')[0]|upper }}</div>
        <div class="sr-who"><b>{{ g.nome or 'Convidado' }}</b><small>{{ g.contato or 'sem número' }} · {{ g.status_rot }}</small></div>
        {% if share.auto_on and g.contato %}
          {% if g.status == 'confirmado' %}
          <button type="button" class="sr-za" disabled title="Já confirmou — não precisa reenviar">📲 Zaq</button>
          {% else %}
          <form method="post" action="/painel/agenda/convite/enviar" data-ajax="enviar" style="margin:0">
            <input type="hidden" name="token" value="{{ g.token }}">
            <input type="hidden" name="ev_id" value="{{ share.ev_id }}">
            <input type="hidden" name="m" value="{{ '%04d-%02d'|format(ano, mes) }}">
            <button type="submit" class="sr-za" data-busy="⏳ Enviando…" title="O Zaq envia o convite pelo WhatsApp">📲 Zaq</button>
          </form>
          {% endif %}
        {% endif %}
        <a class="sr-wa" href="{{ g.wa }}" target="_blank" rel="noopener">💬 Enviar</a>
        <button type="button" class="sr-cp" onclick="cpRow(this)" title="Copiar link">📋</button>
      </div>
      {% endfor %}
    </div>
  </div>
  {% endif %}
  <div class="ag-top">
    <div class="ag-mes">
      <div class="ag-nav">
        <a href="/painel/agenda?m={{ mes_prev }}" aria-label="Mês anterior">‹</a>
        <a href="/painel/agenda?m={{ mes_next }}" aria-label="Próximo mês">›</a>
      </div>
      <h1>{{ mes_nome }} de {{ ano }}</h1>
      <a href="/painel/agenda?m={{ mes_hoje }}" class="ag-hoje">Hoje</a>
    </div>
    <a href="#novo" class="ag-btn" onclick="agNovo(true)">{{ rot.novo_btn }}</a>
  </div>

  {% if pessoas %}
  <div class="ag-pessoas">
    {# Continuam <a> de verdade: sem JS o servidor filtra igual a antes, e o link
       é copiável. Com JS o clique é interceptado e nada vai ao servidor. #}
    <a href="/painel/agenda?m={{ '%04d-%02d'|format(ano, mes) }}" class="agp{% if p_id is none %} on{% endif %}" data-membro="">Todos</a>
    {% for pp in pessoas %}<a href="/painel/agenda?m={{ '%04d-%02d'|format(ano, mes) }}&p={{ pp.id }}" class="agp{% if pp.on %} on{% endif %}" data-membro="{{ pp.id }}">{{ pp.nome }}</a>{% endfor %}
  </div>
  {% endif %}

  <div class="ag-grid">
    <div>
      <div class="cal{% if vende_data %} marca-estado{% endif %}">
        <div class="cal-hd">{% for d in dias_sem %}<span>{{ d }}</span>{% endfor %}</div>
        {% for semana in semanas %}
        <div class="cal-wk">
          {% for c in semana %}
          <div class="cal-cell{% if c.fora %} fora{% endif %}{% if c.hoje %} hoje{% endif %}{% if c.eventos %} tem-evento{% endif %}{% if c.tem_seg %} temseg{% endif %}{% if c.urg %} urg{% endif %}{% if not c.eventos and reaproveitar %} clicavel{% endif %}"
               {% if c.eventos or reaproveitar %}data-iso="{{ c.iso }}" tabindex="0" role="button" aria-label="{% if c.eventos %}Ver os {{ c.eventos|length }} compromisso{{ 's' if c.eventos|length != 1 }} do dia {{ c.dia }}{% else %}Ver sugestões pro dia {{ c.dia }}{% endif %}"{% endif %}>
            <div class="cal-head">
              <span class="cal-num">{{ c.dia }}</span>
              {% if c.eventos|length > 2 %}<span class="cal-count">{{ c.eventos|length }}</span>{% endif %}
            </div>
            {% if c.eventos %}
            <div class="evs">
              {% for e in c.eventos[:2] %}
              <div class="ev-line{% if e.pre %} pre{% endif %}" data-ev="{{ e.id }}" data-membro="{{ e.membro_id }}"{% if e.pre %} title="Data segurada — esperando o sinal ({{ e.prazo }})"{% endif %}><span class="dot d-{{ e.tipo }}"></span><span class="h">{{ e.hora }}</span><span class="n">{{ e.titulo }}</span>{% if e.prazo %}<span class="ev-prazo{% if e.urgente %} urg{% endif %}">{{ e.prazo }}</span>{% endif %}{% if e.pg %}<span class="ev-pg {{ e.pg_classe }}">{{ e.pg }}</span>{% endif %}</div>
              {% endfor %}
            </div>
            {% if c.eventos|length > 2 %}<div class="ev-more">+{{ c.eventos|length - 2 }} mais</div>{% endif %}
            {% endif %}
          </div>
          {% endfor %}
        </div>
        {% endfor %}
      </div>
      {% if vende_data %}
      <p class="hint">A barra da esquerda diz se a data é sua: <b><span class="leg-mk leg-fixo"></span>fixado</b> ·
      <b style="color:var(--ambar)"><span class="leg-mk leg-seg"></span>segurado</b> (esperando o sinal — ocupa a data,
      mas não vira lembrete nem entra no calendário sincronizado; o número ao lado é quanto falta pro prazo).
      A bolinha diz o tipo: <b style="color:#bfeeda">pessoal</b>, <b style="color:#bcd8f6">empresa</b>,
      <b style="color:#f0d9a6">fornecedor</b>. Marque também pelo WhatsApp/Telegram — cai tudo aqui.</p>
      {% else %}
      <p class="hint">As cores separam <b style="color:#bfeeda">pessoal</b>, <b style="color:#bcd8f6">empresa</b> e <b style="color:#f0d9a6">fornecedor</b>. Marque também pelo WhatsApp/Telegram — cai tudo aqui.</p>
      {% endif %}
    </div>

    <div class="side-cards">
      <!-- AS DATAS, as duas faces do mesmo número: o que está por um fio e o que
           já é da casa. Antes existia só o lado "seguradas", e ele sumia quando
           não havia nenhuma — a empresa não tinha onde ver quantas datas tinha
           vendido sem contar no calendário, mês a mês.
           Só quem vende data vê o card; clínica e loja não seguram data. -->
      {% if seguradas or confirmadas %}
      <div class="ag-card" id="datas-card">
        <div class="dt-abas" role="tablist">
          <button type="button" class="dt-aba on" data-alvo="dt-seg" role="tab" aria-selected="true">
            ⏳ Seguradas <span class="c seg">{{ seguradas|length }}</span></button>
          <button type="button" class="dt-aba" data-alvo="dt-con" role="tab" aria-selected="false">
            ✓ Confirmadas <span class="c con">{{ confirmadas|length }}</span></button>
        </div>

        <div id="dt-seg" class="dt-pane">
          {% for s in seguradas %}
          <div class="segrow{% if s.urgente %} urg{% endif %}">
            <div class="segbar"></div>
            <div>
              <div class="stt">{{ s.titulo }}</div>
              <div class="smt">{{ s.quando }}{% if s.sinal %} · sinal {{ s.sinal }}{% endif %}{% if s.orcamento_numero %} · orçamento nº {{ s.orcamento_numero }}{% endif %}</div>
            </div>
            <div class="srt">
              <div class="v">{{ s.prazo }}</div>
              <div class="s">vence {{ s.ate }}</div>
            </div>
          </div>
          {% else %}
          <p class="hint" style="margin:2px 0">Nenhuma data segurada agora.</p>
          {% endfor %}
          {% if seguradas %}
          <p class="hint" style="margin:2px 0 0">Da que vence primeiro. Passando o prazo sem o sinal, a data
          libera sozinha e você é avisado — abra o dia no calendário pra firmar ou soltar antes disso.</p>
          {% endif %}
        </div>

        <div id="dt-con" class="dt-pane" hidden>
          {% for s in confirmadas %}
          <div class="segrow ok">
            <div class="segbar"></div>
            <div>
              <div class="stt">{{ s.titulo }}</div>
              <div class="smt">{{ s.quando }}{% if s.orcamento_numero %} · orçamento nº {{ s.orcamento_numero }}{% else %} · marcado na agenda{% endif %}</div>
            </div>
            <div class="srt">
              <div class="v">{{ s.total or '—' }}</div>
              <div class="s">{{ 'sinal recebido' if s.sinal_pago else 'data firme' }}</div>
            </div>
          </div>
          {% else %}
          <p class="hint" style="margin:2px 0">Nenhuma data firme daqui pra frente.</p>
          {% endfor %}
          {% if confirmadas %}
          <p class="hint" style="margin:2px 0 0">Da mais próxima. A festa que já aconteceu sai daqui sozinha.</p>
          {% endif %}
        </div>
      </div>
      {% endif %}

      <!-- próximos -->
      <div class="ag-card">
        <h2>{{ rot.proximos }}</h2>
        <div class="px">
          {% for e in proximos %}
          <div class="px-row" data-ev="{{ e.id }}" data-membro="{{ e.membro_id or '' }}">
            <div class="px-dot d-{{ e.tipo }}"></div>
            <div class="px-when"><div class="d">{{ e.dia_rot }}</div><div class="h">{{ e.hora_rot }}</div></div>
            <div class="px-body">
              <div class="tt">{{ e.titulo }}</div>
              <div class="mt">{{ e.tipo_rot }}{% if e.local %} · {{ e.local }}{% endif %}{% if e.autor %} · <span title="quem marcou">👤 {{ e.autor }}</span>{% endif %}</div>
              {% if e.convidados %}
              <div class="px-conv">
                {% if e.conv_resumo.total > 1 %}
                <a href="/painel/agenda?m={{ '%04d-%02d'|format(ano, mes) }}&convite_ev={{ e.id }}" class="cgrp{% if e.conv_resumo.fechado %} cgrp-ok{% endif %}" style="text-decoration:none" title="Ver e reenviar os convites">👥 {{ e.conv_resumo.confirmados }} de {{ e.conv_resumo.total }} confirmaram{% if e.conv_resumo.fechado %} 🎉{% endif %}</a>
                {% else %}
                {% for g in e.convidados %}<a href="/painel/agenda?m={{ '%04d-%02d'|format(ano, mes) }}&convite_ev={{ e.id }}" class="cpill cp-{{ g.status }}" style="text-decoration:none" title="Reenviar o link do convite de {{ g.nome or 'convidado' }}">👤 {{ g.nome or 'Convidado' }}: {{ g.status_rot }}</a>{% endfor %}
                {% endif %}
              </div>
              {% endif %}
              <div class="remarcar-box" id="remBox-{{ e.id }}">
                <form method="post" action="/painel/agenda/remarcar">
                  <div class="rlbl">🔁 Nova data</div>
                  <input type="hidden" name="evento_id" value="{{ e.id }}">
                  <input type="hidden" name="m" value="{{ '%04d-%02d'|format(ano, mes) }}">
                  <div class="remarcar-row2">
                    <input type="date" name="data" value="{{ e.data_iso }}" required>
                    <input type="time" name="hora" value="{{ e.hora_rot }}" required>
                  </div>
                  <div class="tg">
                    <div><div class="tg-t">Avisar os convidados</div><div class="tg-s">Manda a nova data pro mesmo link que já têm</div></div>
                    <label class="sw"><input type="checkbox" name="avisar" value="1" checked><span class="track"></span><span class="knob"></span></label>
                  </div>
                  <div class="remarcar-actions">
                    <button class="rbtn ok" type="submit" data-busy="⏳ Salvando…">Salvar nova data</button>
                    <button class="rbtn cc" type="button" onclick="remToggle({{ e.id }})">Cancelar</button>
                  </div>
                </form>
              </div>
              <div class="add-conv-box" id="addConvBox-{{ e.id }}">
                <form method="post" action="/painel/agenda/convidado/adicionar">
                  <div class="rlbl">＋ Adicionar convidado</div>
                  <input type="hidden" name="evento_id" value="{{ e.id }}">
                  <input type="hidden" name="m" value="{{ '%04d-%02d'|format(ano, mes) }}">
                  <div class="add-conv-row">
                    <input name="nome" placeholder="Nome" autocomplete="off">
                    <input name="contato" placeholder="(86) 90000-0000" autocomplete="off">
                  </div>
                  <div class="add-conv-actions">
                    <button class="rbtn ok" type="submit" data-busy="⏳ Adicionando…">Adicionar</button>
                    <button class="rbtn cc" type="button" onclick="addConvToggle({{ e.id }})">Cancelar</button>
                  </div>
                </form>
              </div>
            </div>
            <div class="px-actions">
              <button class="px-add" type="button" title="Adicionar convidado" onclick="addConvToggle({{ e.id }})">＋👤</button>
              <button class="px-rm" type="button" title="Remarcar" onclick="remToggle({{ e.id }})">🔁</button>
              <form method="post" action="/painel/agenda/cancelar" data-ajax="cancelar" onsubmit="return confirm('Cancelar “{{ e.titulo }}”?')">
                <input type="hidden" name="evento_id" value="{{ e.id }}">
                <input type="hidden" name="m" value="{{ '%04d-%02d'|format(ano, mes) }}">
                <button class="px-x" type="submit" title="Cancelar">✕</button>
              </form>
            </div>
          </div>
          {% else %}
          <div class="px-vazio">Nada por vir. Marque um compromisso ali em cima. 🎉</div>
          {% endfor %}
        </div>
      </div>

      <!-- novo compromisso -->
      <div class="ag-card" id="novo"{% if not abrir_novo %} style="display:none"{% endif %}>
        <h2>{{ rot.novo }}</h2>
        <form class="frm" method="post" action="/painel/agenda/novo">
          <input type="hidden" name="m" value="{{ '%04d-%02d'|format(ano, mes) }}">
          <label>{{ rot.titulo }}</label>
          <input name="titulo" id="fTitulo" placeholder="{{ rot.titulo_ph }}" required autocomplete="off">
          <div class="row2">
            <div><label>{{ rot.data }}</label><input name="data" id="fData" type="date" value="{{ hoje_iso }}" required></div>
            <div><label>{{ rot.hora }}</label><input name="hora" id="fHora" type="time" value="{% if vende_data %}19:00{% else %}09:00{% endif %}"></div>
          </div>
          {% if rot.fim %}
          {# HORA DE ENCERRAMENTO: o orçamento de evento sempre pergunta, a agenda
             nunca perguntou. Sem ela a festa entra como compromisso de 1h e a
             checagem de choque compara a janela errada — duas festas na mesma noite
             não acusavam nada. Aceita 24:00 e vira a noite (ver agenda.janela_evento). #}
          <div class="row2">
            <div><label>{{ rot.fim }}</label><input name="hora_fim" id="fFim" type="time" value="23:00"></div>
            <div></div>
          </div>
          {% endif %}
          <!-- choque de horário: aparece na TELA, na hora de marcar. Não bloqueia —
               quem decide se cabe é a empresa (buffet com dois salões cabe). -->
          <div class="choque" id="choqueBox"><span>⚠️</span><div id="choqueTxt"></div></div>
          <!-- só segurar a data: a pré-reserva que nasce de um telefonema, sem
               orçamento nenhum. Antes só existia via aprovação de proposta.
               Só pra quem vende data — clínica não segura horário esperando sinal. -->
          {% if vende_data %}
          <div class="tg" style="margin-top:2px">
            <div><div class="tg-t">Só segurar a data</div><div class="tg-s">Ocupa o dia sem virar compromisso — não vira lembrete</div></div>
            <label class="sw"><input type="checkbox" name="segurar" value="1" id="fSegurar"><span class="track"></span><span class="knob"></span></label>
          </div>
          <div class="segbox" id="segBox">
            <div class="row2">
              <div><div class="sl">Segurar até</div><input name="segurar_ate" id="fSegAte" type="date"></div>
              <div><div class="sl">Sinal esperado</div><input name="sinal_esperado" id="fSinal" type="text" placeholder="opcional" autocomplete="off" inputmode="decimal"></div>
            </div>
            <div class="sh">Vale até o fim do dia escolhido. Passando o prazo sem o sinal, a data
            libera sozinha e você é avisado — e dá pra firmar ou soltar antes disso, abrindo o dia no calendário.</div>
          </div>
          {% endif %}
          <label>{{ rot.desc }} <span style="font-weight:400">(opcional)</span></label>
          <textarea name="descricao" placeholder="{{ rot.desc_ph }}"></textarea>
          <label>{{ rot.local }} <span style="font-weight:400">(opcional)</span></label>
          <input type="hidden" name="local" id="localHidden">
          <div class="addr-wrap" id="addrWrap">
            <div class="addr-input-row">
              <input type="text" id="addrInput" placeholder="Buscar endereço ou nome do lugar…" autocomplete="off">
              <span class="addr-ic">🔍</span>
            </div>
            <div class="addr-drop" id="addrDrop"></div>
            <div id="addrPicked"></div>
          </div>
          <div class="hint-line" id="hintLine">Digite pra buscar — ex: nome do lugar ou endereço.</div>
          <div class="local-alt-row">
            <button type="button" class="manual-toggle" id="manualToggle">✍️ Não achei o lugar — digitar manualmente</button>
            <button type="button" class="online-toggle" id="onlineToggle">🌐 É uma reunião online</button>
          </div>
          <div class="manual-box" id="manualBox">
            <div class="mlabel">Local (texto livre, sem link de mapa)</div>
            <input type="text" id="manualInput" placeholder="Ex: na casa da Ana, no clube…" autocomplete="off">
            <button type="button" class="manual-cancel" id="manualCancel">← voltar pra busca de endereço</button>
          </div>
          <div class="online-box" id="onlineBox">
            <div class="omsg">🌐 Reunião online — nenhum endereço vai ser enviado aos convidados.</div>
            <div class="link-lbl">🎥 Link da chamada <span style="font-weight:400">(opcional)</span></div>
            <input type="url" name="link_online" id="linkOnline" class="link-input" placeholder="Cole aqui o link do Meet, Zoom, Teams…" autocomplete="off">
            <div class="link-hint">Se preencher, vai junto nas mensagens de convite/confirmação e aparece na caixa do dia com um botão de entrar direto.</div>
            <button type="button" class="manual-cancel" id="onlineCancel">← voltar a informar um local</button>
          </div>
          <label>{{ rot.tipo }}</label>
          <div class="segs">
            <label class="s-pessoal"><input type="radio" name="tipo" value="pessoal"{% if not vende_data %} checked{% endif %}><span>{{ rot.t_pessoal }}</span></label>
            <label class="s-empresa"><input type="radio" name="tipo" value="empresa"{% if vende_data %} checked{% endif %}><span>{{ rot.t_empresa }}</span></label>
            <label class="s-fornecedor"><input type="radio" name="tipo" value="fornecedor"><span>{{ rot.t_fornecedor }}</span></label>
          </div>
          <div class="gconv">
            <div class="gt">{{ rot.conv_t }}</div>
            <div class="gd">{{ rot.conv_d }}</div>
            <div id="guests">
              <div class="guest-row">
                <div><input class="gnome" name="convidado_nome" placeholder="Nome" autocomplete="off"></div>
                <div><input name="convidado_contato" placeholder="(86) 90000-0000" autocomplete="off"></div>
                <button type="button" class="g-rm" onclick="rmGuest(this)" title="Remover" aria-label="Remover">✕</button>
              </div>
            </div>
            <button type="button" class="g-add" onclick="addGuest()">{{ rot.conv_add }}</button>
          </div>
          <button class="ok" type="submit" data-busy="{{ rot.salvando }}">{{ rot.salvar }}</button>
        </form>
      </div>

      <!-- lembrete -->
      <div class="ag-card">
        <h2>🔔 Lembrete</h2>
        <form method="post" action="/painel/agenda/lembrete">
          <input type="hidden" name="m" value="{{ '%04d-%02d'|format(ano, mes) }}">
          <div class="tg">
            <div><div class="tg-t">Resumo do dia</div><div class="tg-s">De manhã, o que você tem no dia</div></div>
            <label class="sw"><input type="checkbox" name="resumo_dia" value="1" {% if cfg.resumo_ativo %}checked{% endif %} onchange="document.getElementById('subResumo').style.display=this.checked?'flex':'none'"><span class="track"></span><span class="knob"></span></label>
          </div>
          <div class="sub-opt" id="subResumo"{% if not cfg.resumo_ativo %} style="display:none"{% endif %}>
            <span>às</span>
            <select name="hora_resumo">
              {% for h in range(5,12) %}<option value="{{ h }}" {% if cfg.hora_resumo==h %}selected{% endif %}>{{ '%02d:00'|format(h) }}</option>{% endfor %}
            </select>
          </div>
          <div class="tg">
            <div><div class="tg-t">Aviso antes do compromisso</div><div class="tg-s">Um toque minutos antes de cada um</div></div>
            <label class="sw"><input type="checkbox" name="aviso" value="1" {% if cfg.aviso_antes_min %}checked{% endif %} onchange="document.getElementById('subAviso').style.display=this.checked?'block':'none'"><span class="track"></span><span class="knob"></span></label>
          </div>
          <div id="subAviso"{% if not cfg.aviso_antes_min %} style="display:none"{% endif %}>
            <div class="sub-opt">
              <select name="aviso_antes_min">
                {% for mn in [10,15,30,60,120] %}<option value="{{ mn }}" {% if cfg.aviso_antes_min==mn %}selected{% endif %}>{{ mn }} min antes</option>{% endfor %}
              </select>
            </div>
            <div class="tg">
              <div><div class="tg-t">Avisar os convidados</div><div class="tg-s">Quem confirmou presença recebe o mesmo aviso, pelo WhatsApp</div></div>
              <label class="sw"><input type="checkbox" name="avisar_convidados" value="1" {% if cfg.avisar_convidados %}checked{% endif %}><span class="track"></span><span class="knob"></span></label>
            </div>
          </div>
          <!-- fora do subAviso: independe do "aviso antes" estar ligado -->
          <div class="tg">
            <div><div class="tg-t">Confirmar de volta pro convidado</div><div class="tg-s">Quando ele responde, o Zaq manda o comprovante com calendário e mapa</div></div>
            <label class="sw"><input type="checkbox" name="enviar_confirmacao" value="1" {% if cfg.enviar_confirmacao %}checked{% endif %}><span class="track"></span><span class="knob"></span></label>
          </div>
          <div class="canal-tag">📲 Vai chegar no seu WhatsApp/Telegram, onde você fala com o Zaq.</div>
          <button class="ok" type="submit" data-busy="⏳ Salvando…">Salvar lembrete</button>
        </form>
      </div>

      <!-- prazo da data segurada (pré-reserva por sinal). Regra de venda de DATA:
           quem não vende data não tem o que configurar aqui. -->
      {% if vende_data %}
      <div class="ag-card">
        <h2>⏳ Data segurada</h2>
        <p class="hint" style="margin-top:0">Quando o cliente aprova um orçamento de evento <b>com sinal</b>, a data entra aqui como segurada — ocupa o dia, mas não vira compromisso nem lembrete. Ela só firma quando você confirma o sinal, na tela do orçamento.</p>
        <form method="post" action="/painel/agenda/pre-reserva">
          <input type="hidden" name="m" value="{{ '%04d-%02d'|format(ano, mes) }}">
          {# CAMPO LIVRE, não lista fechada. A lista oferecia 1, 2, 3, 5, 7, 10, 15 e
             30 — quem pratica 4 ou 20 dias não tinha como dizer, e o prazo do sinal
             é regra de venda de cada casa. O limite de 1 a 90 é o que o servidor já
             aplicava; agora ele está à vista. #}
          <div class="sub-opt">
            <span>Segurar por</span>
            <input type="number" name="pre_reserva_dias" min="1" max="90" step="1"
                   inputmode="numeric" style="width:5rem;text-align:right"
                   value="{{ cfg.pre_reserva_dias or 3 }}">
            <span>dias</span>
          </div>
          <p class="hint">Passando o prazo sem o sinal, a data libera sozinha e você é avisado. As pré-reservas que já estão correndo mantêm o prazo com que nasceram.</p>
          <button class="ok" type="submit" data-busy="⏳ Salvando…">Salvar prazo</button>
        </form>
      </div>
      {% endif %}

      <!-- sincronizar -->
      <div class="ag-card">
        <h2>🔗 Sincronizar com sua agenda</h2>
        {% if feed_url %}
        <p class="hint" style="margin-top:0">Cole este link uma vez no seu calendário — ele puxa seus compromissos sozinho.</p>
        <div class="feed">
          <input id="feedUrl" value="{{ feed_url }}" readonly onclick="this.select()">
          <button type="button" onclick="agCopiar()">Copiar</button>
        </div>
        <ul class="sync-steps">
          <li><b>Google Agenda:</b> Outras agendas › + › <b>De um URL</b> › cole o link.</li>
          <li><b>iPhone (Apple):</b> Ajustes › Calendário › Contas › Adicionar › <b>Outro</b> › Assinar calendário.</li>
          <li><b>Outlook:</b> Adicionar calendário › <b>Assinar da Web</b> › cole o link.</li>
        </ul>
        {% else %}
        <p class="hint" style="margin-top:0">Gere um link seguro pra abrir seus compromissos no Google Agenda, Apple ou Outlook — sem senha, sem login.</p>
        <form method="post" action="/painel/agenda/sincronizar">
          <input type="hidden" name="m" value="{{ '%04d-%02d'|format(ano, mes) }}">
          <button class="ag-btn" type="submit" style="margin-top:6px" data-busy="⏳ Ativando…">Ativar sincronização</button>
        </form>
        {% endif %}
      </div>
    </div>
  </div>

  <!-- histórico de envios: convites/lembretes/remarcados mandados, com status -->
  <div class="hist-card">
    <div class="hist-hd">
      <div>
        <h2>📨 Histórico de envios</h2>
        <div class="sub">Convites, avisos e lembretes mandados pros seus convidados — e pra você</div>
      </div>
      <div class="hist-filtros">
        <input class="hf-search" id="histQ" placeholder="Buscar convidado ou compromisso…">
        <button class="hf-btn on" type="button" data-dias="7" onclick="histFiltro(this)">7 dias</button>
        <button class="hf-btn" type="button" data-dias="30" onclick="histFiltro(this)">30 dias</button>
        <button class="hf-btn" id="histFalhasBtn" type="button" onclick="histToggleFalhas()">Só falhas</button>
      </div>
    </div>
    <div class="hist-tabs">
      <button class="hist-tab on" id="histTabFeito" type="button" onclick="histTab('feito')">✅ Feito <span class="cnt">{{ historico_total }}</span></button>
      <button class="hist-tab" id="histTabFila" type="button" onclick="histTab('fila')">🕓 Por fazer <span class="cnt{% if fila %} warn{% endif %}">{{ fila|length }}</span></button>
    </div>
    <div id="histBody">
      {% if historico %}
      <div class="hist-resumo" id="histResumo">
        <span class="hr-ok">✅ <b>{{ historico|selectattr("ok")|list|length }}</b> enviados</span>
        <span class="hr-fail">❌ <b>{{ historico|rejectattr("ok")|list|length }}</b> falharam</span>
        <span>📨 <b>{{ historico_total }}</b> no total · últimos 7 dias</span>
      </div>
      <div class="hist-tbl-wrap">
        <table class="hist-tbl">
          <thead><tr><th>Quando</th><th>Compromisso</th><th>Convidado</th><th>Tipo</th><th>Canal</th><th>Status</th><th></th></tr></thead>
          <tbody id="histTbody">
            {% for it in historico %}
            <tr>
              <td class="hist-qd" data-rot="Quando">{{ it.quando_rot }}</td>
              <td class="hist-compr" data-rot="Compromisso">{{ it.evento_titulo or "—" }}{% if it.evento_local %}<div class="loc">{{ it.evento_local }}</div>{% endif %}</td>
              <td data-rot="Convidado">{{ it.convidado_rot }}</td>
              <td data-rot="Tipo"><span class="hist-tipo ht-{{ it.tipo }}">{{ it.tipo_rot }}</span></td>
              <td class="hist-canal" data-rot="Canal">{{ it.canal_rot }}</td>
              <td data-rot="Status">
                {% if it.ok %}<span class="hist-status hs-ok">✅ Enviado</span>
                {% else %}<span class="hist-status hs-fail">❌ Falhou{% if it.motivo_rot %}<span class="hs-motivo">{{ it.motivo_rot }}</span>{% endif %}</span>{% endif %}
              </td>
              <td>{% if it.pode_reenviar %}<button class="hist-retry" type="button" onclick="histReenviar({{ it.id }}, this)">🔁 Reenviar</button>{% endif %}</td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
      {% if historico_total > historico|length %}
      <div class="hist-foot"><button class="hist-mais" type="button" onclick="histMais()">Ver mais</button></div>
      {% endif %}
      {% else %}
      <div class="hist-vazio" id="histVazio">Nada enviado nos últimos 7 dias. 🤷</div>
      {% endif %}
    </div>
    <div id="filaBody" style="display:none">
      {% if fila %}
      <div class="hist-resumo">
        <span class="hr-wait">🕓 <b>{{ fila|length }}</b> na fila</span>
        <span>Próximo em <b>{{ fila[0].eta_rel }}</b> · {{ fila[0].evento_titulo }}</span>
      </div>
      <div class="hist-tbl-wrap">
        <table class="hist-tbl">
          <thead><tr><th>Sai em</th><th>Compromisso</th><th>Convidado</th><th>Tipo</th><th></th></tr></thead>
          <tbody>
            {% for it in fila %}
            <tr>
              <td class="hist-qd" data-rot="Sai em"><span class="eta">{{ it.eta_rel }} · <b>{{ it.eta_hora }}</b></span></td>
              <td class="hist-compr" data-rot="Compromisso">{{ it.evento_titulo }}{% if it.evento_local %}<div class="loc">{{ it.evento_local }}</div>{% endif %}</td>
              <td data-rot="Convidado">{{ it.convidado_rot }}</td>
              <td data-rot="Tipo"><span class="hist-tipo ht-{{ it.tipo }}">{{ it.tipo_rot }}</span></td>
              <td data-rot="Status"><span class="hist-status hs-wait">{{ it.status_rot }}</span></td>
            </tr>
            {% endfor %}
          </tbody>
        </table>
      </div>
      {% else %}
      <div class="hist-vazio">Nada na fila — sem compromisso esperando aviso nos próximos 7 dias. 🎉</div>
      {% endif %}
    </div>
  </div>

  <!-- caixa do dia (abre ao clicar numa célula com compromisso) -->
  <div class="day-overlay" id="dayOverlay">
    <div class="daybox" id="daybox"></div>
  </div>
</div>
<script>
var EVENTOS_DIA = {{ eventos_dia|tojson }};
var REAPROVEITAR = {{ reaproveitar|tojson }};
var MESES_JS = {{ meses_js|tojson }};
var DIAS_EXT_JS = {{ dias_sem_ext_js|tojson }};
var AGORA_ISO = {{ agora_iso|tojson }};
var CTA_DIA = {{ rot.cta_dia|tojson }};
var MES_ATUAL = {{ ('%04d-%02d'|format(ano, mes))|tojson }};   // pra voltar pro mesmo mês depois de agir
var CUR_MES = {{ ('%04d-%02d'|format(ano, mes))|tojson }};
var TPILL = {pessoal:'Pessoal', empresa:'Empresa', fornecedor:'Fornecedor'};
var HIST_STATE = {dias: 7, falhas: false, q: '', itens: {{ historico|tojson }},
                  total: {{ historico_total }}};
var PRE_RESERVA_DIAS = {{ (cfg.pre_reserva_dias or 3)|tojson }};
</script>""" + _JS_TAG + """

{% endblock %}"""

_env.loader.mapping["agenda"] = _AGENDA_TPL


# ---------------------------------------------------------------- página pública do convite
_CONVITE_TPL = """<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<meta name="robots" content="noindex">
<title>Convite — {{ empresa }}</title>
""" + _tema.FONTES + """<style>
  """ + _tema.variaveis() + """
  *{box-sizing:border-box}
  body{margin:0;background:var(--bg);color:var(--txt);font-family:var(--body);
    line-height:1.5;-webkit-font-smoothing:antialiased;display:flex;justify-content:center;padding:24px 16px 48px}
  .wrap{width:100%;max-width:440px}
  .brand{display:flex;align-items:center;gap:8px;justify-content:center;margin-bottom:18px;color:var(--verde-cl);font-weight:800;letter-spacing:-.01em}
  .brand svg{width:22px;height:22px}
  .card{background:var(--card);border:1px solid var(--borda);border-radius:18px;padding:22px;box-shadow:0 20px 50px rgba(0,0,0,.35)}
  .emp{font-size:.78rem;color:var(--mut);text-transform:uppercase;letter-spacing:.08em;font-weight:700}
  h1{font-size:1.28rem;margin:.3rem 0 2px;letter-spacing:-.01em;text-wrap:balance}
  .greet{color:var(--mut);font-size:.95rem;margin:0 0 16px}
  .ev{background:var(--card2);border:1px solid var(--borda);border-left:4px solid var(--azul);border-radius:12px;padding:14px 16px;margin-bottom:20px}
  .ev .t{font-weight:700;font-size:1.02rem}
  .ev .row{display:flex;align-items:center;gap:8px;color:var(--mut);font-size:.9rem;margin-top:7px}
  .ev .row svg{width:16px;height:16px;flex:0 0 16px;stroke:var(--verde-cl)}
  .btn{display:block;width:100%;text-align:center;border:0;border-radius:12px;padding:.85rem;font-size:1rem;
    font-weight:700;cursor:pointer;font-family:inherit;margin-bottom:10px}
  .b-conf{background:var(--verde2);color:#04160e}
  .b-conf:hover{background:#2ee578}
  .b-sec{background:transparent;border:1px solid var(--borda);color:var(--mut);font-weight:600;font-size:.92rem}
  .b-sec:hover{border-color:var(--verde);color:var(--txt)}
  .expand{display:none;margin:-2px 0 10px;padding:12px;background:var(--card2);border:1px solid var(--borda);border-radius:12px}
  .expand.open{display:block}
  textarea{width:100%;background:var(--bg);border:1px solid var(--borda);border-radius:9px;color:var(--txt);
    padding:.6rem;font-size:.92rem;font-family:inherit;resize:vertical;min-height:64px;margin-bottom:10px}
  textarea:focus{outline:0;border-color:var(--verde)}
  .b-go{background:var(--card);border:1px solid var(--verde);color:var(--verde-cl);font-weight:700;font-size:.9rem;padding:.6rem;margin:0}
  .res{text-align:center;padding:8px 0 2px}
  .res .ic{font-size:2.6rem;line-height:1}
  .res h2{font-size:1.2rem;margin:.5rem 0 .3rem}
  .res p{color:var(--mut);font-size:.95rem;margin:0 0 4px}
  .res .quote{margin-top:10px;font-size:.88rem;color:var(--mut);font-style:italic}
  .cal{display:inline-flex;align-items:center;gap:8px;margin-top:16px;background:var(--card2);border:1px solid var(--borda);
    color:var(--txt);border-radius:10px;padding:.7rem 1.1rem;text-decoration:none;font-weight:700;font-size:.92rem}
  .cal:hover{border-color:var(--verde)}
  .foot{text-align:center;color:var(--mut);font-size:.74rem;margin-top:18px}
  .foot b{color:var(--verde-cl)}
</style></head><body>
<div class="wrap">
  <div class="brand">
    <svg viewBox="0 0 64 64" fill="none"><path d="M16 18 H44 L18 46 H46" stroke="#3ee0a6" stroke-width="6" stroke-linecap="round" stroke-linejoin="round"/><path d="M47 10 L49 16 L55 18 L49 20 L47 26 L45 20 L39 18 L45 16 Z" fill="#3ee0a6"/></svg>
    zaq
  </div>
  <div class="card">
    {% if not estado %}
    <div class="emp">{{ empresa }}</div>
    <h1>Convite de reunião</h1>
    <p class="greet">{% if nome %}Oi, {{ nome }}! {% endif %}A {{ empresa }} quer marcar com você:</p>
    <div class="ev">
      <div class="t">{{ titulo }}</div>
      <div class="row"><svg viewBox="0 0 24 24" fill="none" stroke-width="2"><rect x="3.5" y="5" width="17" height="15" rx="2"/><path d="M3.5 9.5h17M8 3v4M16 3v4"/></svg>{{ quando }}</div>
      {% if local %}<div class="row"><svg viewBox="0 0 24 24" fill="none" stroke-width="2"><path d="M12 21s7-5.5 7-11a7 7 0 10-14 0c0 5.5 7 11 7 11z"/><circle cx="12" cy="10" r="2.5"/></svg>{{ local }}</div>{% endif %}
    </div>
    <form method="post" action="/convite/{{ token }}/responder">
      <input type="hidden" name="acao" value="confirmar">
      <button class="btn b-conf" type="submit">✅ Confirmar presença</button>
    </form>
    <button class="btn b-sec" type="button" onclick="tog('rem')">🔁 Remarcar — sugerir outro horário</button>
    <div class="expand" id="rem">
      <form method="post" action="/convite/{{ token }}/responder">
        <input type="hidden" name="acao" value="remarcar">
        <textarea name="resposta" placeholder="Se quiser, sugira um horário melhor (opcional)"></textarea>
        <button class="btn b-go" type="submit">Enviar pedido de remarcar</button>
      </form>
    </div>
    <button class="btn b-sec" type="button" onclick="tog('rec')">❌ Não vou poder</button>
    <div class="expand" id="rec">
      <form method="post" action="/convite/{{ token }}/responder">
        <input type="hidden" name="acao" value="recusar">
        <textarea name="resposta" placeholder="Motivo (opcional)"></textarea>
        <button class="btn b-go" type="submit">Confirmar que não vou</button>
      </form>
    </div>
    {% else %}
    <div class="res">
      {% if estado == 'confirmado' %}
        <div class="ic">✅</div><h2>Presença confirmada!</h2>
        <p>Você confirmou <b style="color:var(--txt)">{{ titulo }}</b> — {{ quando }}.</p>
        <p>A {{ empresa }} já foi avisada. Até lá! 👋</p>
        <div style="display:flex;flex-direction:column;gap:8px;margin-top:16px">
          {% if cal_link %}<a class="cal" href="{{ cal_link }}" style="margin-top:0" target="_blank" rel="noopener">📆 Adicionar ao meu calendário</a>{% endif %}
          {% if mapa_link %}<a class="cal" href="{{ mapa_link }}" style="margin-top:0" target="_blank" rel="noopener">📍 Ver local no mapa{% if local %} — {{ local }}{% endif %}</a>{% endif %}
          {% if link_online %}<a class="cal" href="{{ link_online }}" style="margin-top:0" target="_blank" rel="noopener">🎥 Entrar na chamada</a>{% endif %}
        </div>
      {% elif estado == 'remarcar' %}
        <div class="ic">🔁</div><h2>Pedido enviado</h2>
        <p>Avisamos a {{ empresa }} que você prefere outro horário pra <b style="color:var(--txt)">{{ titulo }}</b>.</p>
        {% if resposta %}<div class="quote">“{{ resposta }}”</div>{% endif %}
        <p style="margin-top:8px">Ela vai te procurar pra ajustar. 🙂</p>
      {% else %}
        <div class="ic">👍</div><h2>Tudo certo, obrigado por avisar</h2>
        <p>Avisamos a {{ empresa }} que você não vai poder na <b style="color:var(--txt)">{{ titulo }}</b>.</p>
        {% if resposta %}<div class="quote">“{{ resposta }}”</div>{% endif %}
        <p style="margin-top:8px">Se mudar de ideia, é só falar com ela. 👋</p>
      {% endif %}
    </div>
    {% endif %}
  </div>
  <div class="foot">Agendado com <b>Zaq</b> · seu assistente financeiro</div>
</div>
<script>
function tog(id){var e=document.getElementById(id);var open=e.classList.contains('open');
  document.querySelectorAll('.expand').forEach(function(x){x.classList.remove('open')});
  if(!open){e.classList.add('open');var t=e.querySelector('textarea');if(t)setTimeout(function(){t.focus()},120);}}
</script>
</body></html>"""

_CONVITE_404_TPL = """<!doctype html><html lang="pt-br"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>Convite não encontrado</title>
<style>body{margin:0;background:#0b0f0d;color:#eaeae6;font-family:var(--body);display:flex;
  min-height:100vh;align-items:center;justify-content:center;text-align:center;padding:24px}
  .b{max-width:360px}.b h1{font-size:1.3rem;margin:.4rem 0}.b p{color:#9a9a94}</style></head><body>
<div class="b"><div style="font-size:2.4rem">🔍</div><h1>Convite não encontrado</h1>
<p>Esse link de convite não existe mais ou foi digitado errado. Peça um novo pra quem te convidou.</p></div>
</body></html>"""

# ⚠️ "convite" aqui é o convite de REUNIÃO (agenda). O _env.loader.mapping é
# COMPARTILHADO — a equipe usa "convite_equipe" pro convite de acesso. NÃO registre
# um template de outro assunto sob "convite" nem renomeie o da equipe pra "convite",
# senão um sobrescreve o outro (ver test_convite_template_colisao).
_env.loader.mapping["convite"] = _CONVITE_TPL
_env.loader.mapping["convite_404"] = _CONVITE_404_TPL
