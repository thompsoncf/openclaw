"""O follow-up no painel: /painel/follow-up (mockup docs/mockups/follow_up_automatico.html,
aprovado em 07/09/2026).

Os quatro indicadores do topo — hoje, atrasados, críticos, sem próxima ação —,
cada um abrindo a lista respectiva, com filtro por vendedor e por etapa. Cada
lead mostra o que o pedido do dono pediu: a próxima ação e quando vence, o
responsável, o tempo sem interação, as tentativas, e de quem é a bola. E os dois
selos que a régua acrescenta: quantas vezes já foi avisado, e quantas vezes foi
adiado sem ninguém falar com o cliente.

Quem vê: dono e gestor veem a conta inteira e a tabela por vendedor; o vendedor
vê a fila dele e mais nada — o painel da gestão é do gestor.

O motor está em finance/follow_up; aqui é só a tela.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse

from db.conexao import get_pool
from finance import follow_up as fu
from finance import funil_regua as fr
from finance import raio_x_perfil as rxp
# a barra de abas de Prospecção: o Follow-up virou uma delas em 07/09/2026, e a
# barra tem que ser a MESMA — `_navbar` é fonte única desde que a cópia escrita à
# mão no Funil divergiu e escondeu a aba "Quem atacar" de quem estava lá.
from web import janela_lead as _jl
from web.painel_prospeccao import NAVBAR_CSS, _navbar
from web.portal import _env, _render, conta_logada, nicho_da_conta

import logging

_log = logging.getLogger("openclaw.painel_follow_up")

router = APIRouter()

_UTC_BR = timedelta(hours=-3)


def _acesso(request: Request):
    conta = conta_logada(request)
    if conta is None:
        return None, None, RedirectResponse("/login", status_code=303)
    papel = request.session.get("papel", "dono")
    if papel not in ("dono", "gestor", "vendedor"):
        return None, None, RedirectResponse("/painel", status_code=303)
    perfil = rxp.perfil(nicho_da_conta(conta))
    if perfil["chave"] not in fu.PERFIS_COM_TELA:
        return None, None, RedirectResponse("/painel", status_code=303)
    return conta, perfil, None


def _br(dt) -> str:
    """Instante em hora de Brasília, curto. O painel inteiro trata -3 fixo (o país
    não tem horário de verão desde 2019) — duas convenções de fuso no mesmo
    produto seria pior que o erro de uma."""
    if not dt:
        return ""
    loc = dt + _UTC_BR
    hoje = (datetime.now(timezone.utc) + _UTC_BR).date()
    if loc.date() == hoje:
        return f"hoje {loc:%H:%M}"
    if loc.date() == hoje - timedelta(days=1):
        return f"ontem {loc:%H:%M}"
    if loc.date() == hoje + timedelta(days=1):
        return f"amanhã {loc:%H:%M}"
    return f"{loc:%d/%m} {loc:%H:%M}"


def _resumo_msg(texto: str, limite: int = 62) -> str:
    """Uma linha só da última mensagem — os mesmos do funil, reaproveitados pra
    que o balão daqui e o do card sejam a mesma coisa, e não duas parecidas."""
    from web.painel_prospeccao import _resumo_msg as _r
    return _r(texto, limite)


def _quando_curto(quando) -> str:
    from web.painel_prospeccao import _quando_curto as _q
    return _q(quando)


def _tempo(horas) -> str:
    from web.painel_prospeccao import _tempo_curto as _t
    return _t(horas)


def _entrega(pool, conta_id: int) -> dict | None:
    """O resumo de entrega e leitura dos avisos, pronto pro card.

    Devolve `None` quando NÃO HÁ NADA que ele possa dizer — conta que nunca mandou
    aviso, ou base antes da migração 284. Card de zeros numa conta que acabou de
    ligar o follow-up não informa nada e só ocupa o lugar da fila.

    As porcentagens saem daqui e não do template: `width:{{ a / b * 100 }}%` em
    Jinja com b=0 derruba a tela inteira por divisão por zero — e uma conta sem
    envio nenhum é exatamente o caso b=0.
    """
    try:
        from finance import aviso_log as _al
        r = _al.resumo(pool, conta_id)
    except Exception:  # noqa: BLE001 — o card é acessório; a fila é o produto
        _log.info("follow-up: resumo de entrega falhou (ok)", exc_info=True)
        return None
    r["tem"] = any(r[ch]["tentativas"] for ch in _al.CANAIS)
    for v in r["por_vendedor"]:
        t = max(1, v["total"])
        v["pct_lido"] = round(100 * v["lidos"] / t)
        v["pct_entregue"] = round(100 * v["entregues"] / t)
    r["hist"] = _historico(pool, conta_id)
    return r


#: O selo da linha fechada, por tipo de alerta do vigia. Um só vocabulário nas duas
#: telas: o que a Equipe chama de "não recebe aviso" não pode virar outra palavra
#: aqui — a pessoa que lê as duas é a mesma.
_SELO = {"sem_cadastro": ("ruim", "não recebe"),
         "sem_canal": ("ruim", "não recebe"),
         "numero_sem_recibo": ("alerta", "sem recibo")}


def _sinal(evs: list[dict]) -> tuple[str, str]:
    """O sinal MAIS FORTE que voltou, pra caber na linha fechada.

    A ordem é a da confiança, não a do tempo: leu > entregue > abriu (o toque no
    push) > saiu > não saiu. É o que responde "chegou nele?" sem abrir nada — e é
    por isso que a linha fechada já serve pro dono bater o olho na equipe inteira.
    """
    if not evs:
        return ("", "sem aviso ainda")
    if any(e["lido_em"] for e in evs):
        return ("g", "👀 leu")
    if any(e["entregue_em"] for e in evs):
        return ("g", "✓✓ entregue")
    if any(e["clicado_em"] for e in evs):
        return ("g", "abriu o push")
    if any(e["ok"] for e in evs):
        return ("a", "enviado, sem recibo")
    return ("r", "não saiu")


def _historico(pool, conta_id: int) -> list[dict]:
    """A lista de pessoas do card, cada uma com o histórico pronto pra abrir.

    Junta três leituras que já existem: o histórico (`aviso_log.historico`), o
    alerta do vigia (`aviso_saude.por_membro`, o MESMO da tela de Equipe) e o
    último sinal. Ordena por problema primeiro — a lista existe pra mostrar quem
    precisa de ação, e quem precisa de ação não pode estar no fim.
    """
    try:
        from finance import aviso_log as _al
        from finance import aviso_saude as _as
        pessoas = _al.historico(pool, conta_id)
        alertas = _as.por_membro(pool, conta_id)
    except Exception:  # noqa: BLE001 — o histórico é enfeite; a fila é o produto
        _log.info("follow-up: histórico do aviso falhou (ok)", exc_info=True)
        return []
    for p in pessoas:
        evs = [e for ch in p["canais"].values() for e in ch["recentes"] + ch["antigos"]]
        al = alertas.get(p["membro_id"])
        p["alerta"] = al
        p["selo_cls"], p["selo"] = _SELO.get((al or {}).get("tipo"), ("ok", "recebendo"))
        p["sinal_cls"], p["sinal"] = _sinal(evs)
        ult = max(evs, key=lambda e: e["quando"]) if evs else None
        p["n_leads"] = (ult or {}).get("n_leads")
        p["quando"] = (ult or {}).get("quando")
        p["tem_antigos"] = any(ch["antigos"] for ch in p["canais"].values())
    pessoas.sort(key=lambda x: (x["selo"] == "recebendo", x["quem"]))
    return pessoas


@router.get("/painel/follow-up", response_class=HTMLResponse)
def painel_follow_up(request: Request):
    conta, perfil, redir = _acesso(request)
    if redir is not None:
        return redir
    pool, conta_id = get_pool(), conta[0]
    papel = request.session.get("papel", "dono")
    q = request.query_params
    estado = q.get("estado") or "critico"
    if estado not in fu.ESTADOS and estado != "todos":
        estado = "critico"
    try:
        vend_f = int(q.get("vendedor") or 0) or None
    except ValueError:
        vend_f = None
    # o vendedor só enxerga a fila dele — o painel da gestão é do gestor
    if papel == "vendedor":
        vend_f = request.session.get("membro_id")
    etapa_f = (q.get("etapa") or "").strip()

    linhas, cfg, vendedores, etapas = [], dict(fu._PADRAO), [], []
    status_tpl: list = []
    try:
        with pool.connection() as c:
            cfg = fu.config(c, conta_id)
            linhas = fu.leads(c, conta_id, perfil, cfg=cfg)
            if papel != "vendedor":
                vendedores = c.execute(
                    """select id, coalesce(nullif(nome,''), email) from membros
                        where conta_id=%s and coalesce(ativo,true) and papel='vendedor'
                        order by 2""", (conta_id,)).fetchall()
            etapas = [r[0] for r in c.execute(
                """select chave from funil_etapas where conta_id=%s
                    and fase='venda' order by ordem, id""", (conta_id,)).fetchall()]
            # TODAS as etapas, pro seletor da janela do lead. Difere do `etapas`
            # acima de propósito: aquele é o FILTRO da tela, que só lista etapa de
            # venda; este é pra onde o lead pode ir, e ganho e perdido têm que
            # estar lá — "já fechou mas está como contato" é justamente a troca que
            # o dono pediu. Mesma fonte do funil (`_etapas`), pra conta que
            # renomeou etapa não ver dois vocabulários.
            #
            # E passa pela MESMA função que o funil usa (`lista_de_status`) desde
            # 17/09/2026: as duas telas montavam a lista cada uma do seu jeito, e
            # foi assim que o funil acabou oferecendo 6 situações onde esta oferece
            # 9, na mesma janela e no mesmo lead.
            from web.painel_prospeccao import _etapas as _et
            status_tpl = _jl.lista_de_status(_et(c, conta_id))
    except Exception:  # noqa: BLE001 — tela que não abre é pior que tela incompleta
        linhas = []

    # COMO OS AVISOS CHEGARAM (migração 284). Só pra quem decide: o vendedor não vê
    # o card. Fora do `with` de cima de propósito — é leitura independente, e uma
    # falha dela não pode levar junto a fila, que é o produto da tela.
    entrega = _entrega(pool, conta_id) if papel in ("dono", "gestor") else None
    # QUEM NÃO ESTÁ SENDO AVISADO (18/09/2026). Vem junto do card e pelo mesmo
    # motivo dele: medir o envio só serve se alguém for avisado quando o envio
    # parar. Também só pra quem decide — é ele que corrige cadastro.
    sem_aviso = []
    if papel in ("dono", "gestor"):
        try:
            from finance import aviso_saude as _as
            sem_aviso = _as.alertas(pool, conta_id)
        except Exception:  # noqa: BLE001 — a fila é o produto; o vigia é acessório
            _log.info("follow-up: vigia do canal falhou (ok)", exc_info=True)

    minhas = [x for x in linhas if (not vend_f or x["vendedor_id"] == vend_f)]
    topo = fu.resumo(minhas)
    fila = [x for x in minhas
            if (estado == "todos" or x["estado"] == estado)
            and (not etapa_f or x["status"] == etapa_f)]
    # a ordem é a de sempre, salvo se o dono ligou a fila por temperatura na Régua
    # (migração 245): aí os seis níveis do § 7 vêm na frente, e a ordem antiga vira
    # o desempate dentro de cada nível.
    por_temp = cfg.get("fila_modo") == "temperatura"
    fila = fu.ordenar(fila, por_temperatura=por_temp)
    # `secao_ativa='prospeccao'` e `nav_ativo='follow_up'`: a tela virou ABA de
    # Prospecção (07/09/2026) — o menu lateral acende Prospecção, e a barra de
    # abas acende Follow-up.
    return _render("follow_up", request, titulo="Follow-up", secao_ativa="prospeccao",
                   nav_ativo="follow_up", cfg=cfg,
                   perfil=perfil, papel=papel, topo=topo, fila=fila[:200],
                   sobrando=max(0, len(fila) - 200), estado=estado, vend_f=vend_f,
                   etapa_f=etapa_f, vendedores=vendedores, etapas=etapas,
                   status=status_tpl,
                   # O NOME da etapa, e não a chave (25/09/2026): o cartão mostrava
                   # "📍 negociacao" e o filtro "agendado_visita" — a chave interna,
                   # que ninguém da equipe digitou. A MESMA fonte da janela do lead
                   # (`status_tpl`), pra a conta que renomeou etapa ver um nome só.
                   rot_etapa={x["c"]: x["r"] for x in status_tpl},
                   gestao=(fu.por_vendedor(linhas) if papel != "vendedor" else []),
                   modo=cfg["follow_up_modo"], zap=bool(cfg.get("fu_zap")),
                   entrega=entrega, sem_aviso=sem_aviso,
                   rotulo=fu.ROTULO, emoji=fu.EMOJI,
                   por_temp=por_temp, rot_prio=fu.ROTULO_PRIORIDADE,
                   br=_br, tempo=_tempo, adia_max=fu.ADIAMENTOS_ATE_MOTIVO,
                   resumo_msg=_resumo_msg, quando_curto=_quando_curto,
                   erro=q.get("erro") or "")


@router.post("/painel/follow-up/modo")
def follow_up_modo(request: Request, modo: str = Form("")):
    """Liga, ensaia ou desliga o follow-up automático desta conta.

    Era da Régua do funil até 07/09/2026 — e a tela de lá dizia "ligue na Régua",
    mandando a pessoa embora pra ligar o que ela estava olhando. Grava na hora, no
    clique: um formulário com "salvar" no topo de uma tela de trabalho é botão
    pra esquecer de apertar.

    Só dono e gestor. O vendedor não decide o que a conta inteira recebe — a
    mesma regra da Régua, que já barrava por `gerencia`.

    `def`, e não `async def`: o handler escreve no banco de forma síncrona, e
    handler async fazendo isso trava o event loop do processo inteiro. Sem o
    `async`, o FastAPI joga a função na threadpool sozinho — é o que o
    `follow_up_reagendar` aqui do lado já faz, e o que
    `tests/test_event_loop_nao_trava.py` cobra de todo handler novo.
    """
    conta, _perfil, redir = _acesso(request)
    if redir is not None:
        return redir
    if request.session.get("papel", "dono") not in ("dono", "gestor"):
        return RedirectResponse("/painel/follow-up", status_code=303)
    modo = (modo or "").strip()
    if modo not in fr.MODOS:
        return RedirectResponse("/painel/follow-up", status_code=303)
    with get_pool().connection() as c:
        fu.config(c, conta[0])          # garante a linha da régua
        c.execute("update funil_regua set follow_up_modo=%s, atualizado_em=now() "
                  " where conta_id=%s", (modo, conta[0]))
        c.commit()
    return RedirectResponse("/painel/follow-up", status_code=303)


@router.post("/painel/follow-up/zap")
def follow_up_zap(request: Request, zap: str = Form("")):
    """Liga ou desliga o WhatsApp do aviso de follow-up (migração 280).

    Rota PRÓPRIA, e não um campo do interruptor de modo, pelo mesmo motivo que o
    modo saiu da Régua em 07/09: são duas decisões diferentes, e juntá-las num
    formulário faria ligar uma mexer na outra sem querer.

    Só dono e gestor — o vendedor não decide o que chega no celular da equipe. E
    `def`, não `async def`: escreve no banco de forma síncrona, e handler async
    fazendo isso trava o event loop (ver `follow_up_modo` aqui em cima).
    """
    conta, _perfil, redir = _acesso(request)
    if redir is not None:
        return redir
    if request.session.get("papel", "dono") not in ("dono", "gestor"):
        return RedirectResponse("/painel/follow-up", status_code=303)
    ligar = (zap or "").strip() == "1"
    with get_pool().connection() as c:
        fu.config(c, conta[0])          # garante a linha da régua
        c.execute("update funil_regua set fu_zap=%s, atualizado_em=now() where conta_id=%s",
                  (ligar, conta[0]))
        c.commit()
    return RedirectResponse("/painel/follow-up", status_code=303)


@router.post("/painel/follow-up/zap-testar")
def follow_up_zap_testar(request: Request):
    """Manda AGORA o aviso no WhatsApp de cada vendedor, e relata pessoa a pessoa.

    Relato SEPARADO por pessoa, e não um "deu certo" agregado, pelo mesmo motivo do
    `enviar_alerta_teste` do admin: um vendedor sem número ou um chip que recusou
    ficariam escondidos atrás dos que funcionaram — exatamente o que um teste existe
    pra revelar.

    Só dono e gestor: o botão manda mensagem no celular da equipe.
    """
    conta, perfil, redir = _acesso(request)
    if redir is not None:
        return JSONResponse({"ok": False, "erro": "login"}, status_code=401)
    if request.session.get("papel", "dono") not in ("dono", "gestor"):
        return JSONResponse({"ok": False, "erro": "escopo"}, status_code=403)
    try:
        r = fu.testar_zap(get_pool(), conta[0], perfil)
    except Exception:  # noqa: BLE001 — botão de teste não derruba a tela
        _log.warning("teste do WhatsApp falhou na conta %s", conta[0], exc_info=True)
        return JSONResponse({"ok": False, "erro": "Falha no teste."})
    return JSONResponse({"ok": True, "envios": [
        {"nome": x["nome"], "n_leads": x["n_leads"], "ok": x["ok"], "erro": x["erro"]}
        for x in r]})


@router.post("/painel/follow-up/reagendar")
def follow_up_reagendar(request: Request, lead_id: int = Form(...),
                        quando: str = Form(""), hora: str = Form(""),
                        dias: str = Form(""), acao: str = Form(""),
                        motivo: str = Form(""), volta: str = Form("")):
    """Remarca a próxima ação — por data escolhida (`quando`) ou em um toque
    (`dias`: os botões Amanhã / +3d / +7d).

    O motivo passa a ser obrigatório do 3º adiamento seguido sem nenhuma mensagem
    no meio — a trava que o dono pediu pra que reagendar não vire um jeito de
    silenciar o lead. É por isso que os botões rápidos somem quando a trava está
    a um adiamento de distância: um toque não tem onde escrever motivo."""
    conta, _perfil, redir = _acesso(request)
    if redir is not None:
        return redir
    conta_id = conta[0]
    d = (quando or "").strip() if isinstance(quando, str) else ""
    n = (dias or "").strip() if isinstance(dias, str) else ""
    hh, mm = 9, 0
    if n.isdigit() and 1 <= int(n) <= 90:
        # o toque rápido marca pras 9h do dia, no fuso de Brasília: prazo de
        # madrugada só serviria pra vencer antes de alguém acordar
        dia = (datetime.now(timezone.utc) + _UTC_BR).date() + timedelta(days=int(n))
    else:
        try:
            dia = datetime.strptime(d, "%Y-%m-%d").date()
        except ValueError:
            return RedirectResponse(_volta(volta, "data_invalida"), status_code=303)
        h = (hora or "").strip() if isinstance(hora, str) else ""
        try:
            hh, mm = (int(x) for x in h.split(":")[:2])
        except (ValueError, TypeError):
            hh, mm = 9, 0
    # a tela fala em hora de Brasília; o banco guarda UTC
    prazo = datetime.combine(dia, time(hh, mm)).replace(tzinfo=timezone.utc) - _UTC_BR
    try:
        with get_pool().connection() as c:
            r = fu.marcar(c, conta_id, lead_id, prazo,
                          acao=(acao if isinstance(acao, str) else ""),
                          membro_id=request.session.get("membro_id"),
                          motivo=(motivo if isinstance(motivo, str) else ""))
            c.commit()
    except Exception:  # noqa: BLE001
        return RedirectResponse(_volta(volta, "falhou"), status_code=303)
    if not r.get("ok"):
        return RedirectResponse(_volta(volta, r.get("erro", "falhou")), status_code=303)
    return RedirectResponse(_volta(volta, ""), status_code=303)


def _volta(volta: str, erro: str) -> str:
    base = volta if (isinstance(volta, str) and volta.startswith("/painel/follow-up")) else "/painel/follow-up"
    if not erro:
        return base
    return base + ("&" if "?" in base else "?") + "erro=" + erro


_TPL = r"""{% extends "base" %}{% block conteudo %}
<style>""" + NAVBAR_CSS + r"""</style>
{# o balão de conversa é o MESMO do funil e do Raio-X (web/balao_conversa.py):
   abre ancorado no botão, sem sair da tela e sem perder a fila aberta #}
<style>{{ balao_css }}</style>
{# e a janela do lead é a MESMA do funil (web/janela_lead.py): é nela que a
   situação no funil se muda daqui, sem sair da fila #}
<style>{{ janela_css }}</style>
<style>
.fu{display:flex;flex-direction:column;gap:1rem}
.fu h1{font-size:1.5rem;margin:0}
.fu .lede{color:var(--text-dim);font-size:.88rem;margin:.2rem 0 0;max-width:70ch}
.fu-tiles{display:grid;grid-template-columns:repeat(4,1fr);gap:.6rem}
@media (max-width:820px){.fu-tiles{grid-template-columns:repeat(2,1fr)}}
.fu-tile{border:1px solid var(--line);border-radius:12px;background:var(--surface);padding:.7rem .8rem;display:flex;flex-direction:column;gap:.1rem;text-decoration:none;color:inherit}
.fu-tile b{font:600 1.7rem/1 var(--display);font-variant-numeric:tabular-nums}
.fu-tile span{font-size:.66rem;color:var(--text-faint);letter-spacing:.02em}
.fu-tile em{font:500 .6rem var(--mono);font-style:normal;color:var(--text-dim);margin-top:.15rem}
.fu-tile.hoje b{color:var(--ambar)}.fu-tile.atrasado b{color:var(--coral)}
.fu-tile.critico b{color:var(--coral)}.fu-tile.sem_acao b{color:var(--text-dim)}
.fu-tile.on{border-color:var(--neon-borda);background:var(--neon-fundo)}
.fu-bar{display:flex;flex-wrap:wrap;gap:.4rem;padding:.6rem .8rem;border:1px solid var(--line);border-radius:12px;background:var(--bg-2);align-items:center}
.fu-bar label{display:inline-flex;align-items:center;gap:.35rem;border:1px solid var(--line);border-radius:999px;padding:.2rem .35rem .2rem .7rem;font-size:.74rem;color:var(--text-dim);background:var(--surface);margin:0}
.fu-bar label.on{border-color:var(--neon-borda);background:var(--neon-fundo);color:var(--text)}
.fu-bar select{width:auto;margin:0;padding:.15rem .3rem;font-size:.76rem;background:transparent;border:0;color:var(--text);font-weight:500}
.fu-bar select:focus{outline:none}
.fu-lista{border:1px solid var(--line);border-radius:12px;background:var(--surface);overflow:hidden}
.fu-lead{display:grid;grid-template-columns:1fr auto;gap:.6rem;padding:.7rem .85rem;border-bottom:1px solid var(--line);align-items:start}
.fu-lead:last-child{border-bottom:none}
.fu-lead .nome{display:flex;align-items:center;gap:.45rem;flex-wrap:wrap;margin-bottom:.3rem}
.fu-lead .nome a{font:600 .95rem var(--display);color:var(--text);text-decoration:none}
.fu-lead .meta{display:flex;flex-wrap:wrap;gap:.2rem .8rem;font-size:.72rem;color:var(--text-dim)}
.fu-lead .meta i{font-style:normal;color:var(--text-faint);margin-right:.2rem}
.fu-lead .meta b{color:var(--text);font-weight:500}
/* as tentativas como tarefas: feito, pendente, atrasado — a cor é o estado, não
   enfeite, e é o que se lê antes de ler o texto */
/* a temperatura: as mesmas cores do card do funil (painel_prospeccao.TEMP_PILL) —
   duas paletas pro mesmo conceito faria o vendedor achar que são coisas diferentes */
.fu-temp{font-size:.66rem;font-weight:700;letter-spacing:.04em;padding:.1rem .4rem;
  border-radius:5px;text-transform:uppercase}
.fu-temp.quente{background:#3a1a1a;color:#f0917f}
.fu-temp.morno{background:#2e2713;color:#e0b25a}
.fu-temp.frio{background:#14273a;color:#7bb8e6}
/* o nível da fila: só aparece quando o dono ligou a ordem por temperatura */
.fu-nivel{font-size:.64rem;color:var(--txt-mut);font-family:var(--mono,ui-monospace);
  border:1px solid var(--borda);border-radius:5px;padding:.1rem .35rem}
.fu-toques{display:flex;align-items:center;gap:.35rem;flex-wrap:wrap;margin-top:.35rem}
.fu-toque{font:600 .66rem/1 ui-monospace,monospace;padding:.3rem .4rem;border-radius:6px;
  border:1px solid var(--borda);color:var(--txt-mut);background:var(--bg)}
.fu-toque.feito{border-color:var(--verde);color:var(--verde)}
.fu-toque.atrasado{border-color:var(--coral);color:var(--coral)}
.fu-acao{margin-top:.35rem;font-size:.76rem;color:#F0DCA6;background:var(--ambar-fundo);border:1px solid var(--ambar-borda);border-radius:8px;padding:.35rem .55rem;display:inline-block}
.fu-acao b{color:#fff}
.fu-acao.calma{color:var(--text-dim);background:var(--surface-2);border-color:var(--line)}
.fu-acao.calma b{color:var(--text)}
.fu-pill{display:inline-flex;align-items:center;gap:.25rem;font:600 .64rem var(--mono);border-radius:999px;padding:.1rem .45rem;border:1px solid var(--line);color:var(--text-dim);white-space:nowrap}
.fu-pill.critico,.fu-pill.atrasado{color:var(--coral);border-color:var(--coral-borda);background:var(--coral-fundo)}
.fu-pill.hoje{color:var(--ambar);border-color:var(--ambar-borda);background:var(--ambar-fundo)}
.fu-pill.agendado,.fu-pill.andamento{color:var(--neon-bright);border-color:var(--neon-borda);background:var(--neon-fundo)}
.fu-dir{display:flex;flex-direction:column;gap:.3rem;align-items:flex-end}
/* `button.bt` entrou junto do <a> quando "Abrir ficha" virou janela: o botão
   herdava o `button{width:100%;margin-top:1.4rem}` global (que é dos formulários
   de login) e nascia esticado e caído — daí o `width:auto;margin:0` explícito. */
.fu-dir a.bt,.fu-dir button.bt,.fu-dir summary{font:500 .7rem var(--body);border:1px solid var(--line);background:var(--bg-2);color:var(--text-dim);border-radius:8px;padding:.28rem .55rem;white-space:nowrap;cursor:pointer;list-style:none;text-decoration:none}
.fu-dir button.bt{width:auto;margin:0;font-family:var(--body)}
.fu-dir a.bt:hover,.fu-dir button.bt:hover,.fu-dir summary:hover{border-color:var(--neon-borda);color:var(--neon-bright)}
.fu-dir details[open] summary{border-color:var(--neon-borda);color:var(--neon-bright)}
.fu-form{margin-top:.35rem;display:flex;flex-direction:column;gap:.3rem;border:1px solid var(--line);border-radius:10px;padding:.5rem;background:var(--bg-2);min-width:230px}
.fu-form input,.fu-form select{margin:0;font-size:.76rem;padding:.25rem .35rem}
.fu-form button{font-size:.74rem;padding:.3rem .5rem;margin:0}
.fu-form .dica{font-size:.64rem;color:var(--text-faint)}
.fu-rapido{display:flex;gap:.25rem;margin:0}
.fu-rapido button{font:500 .68rem var(--body);border:1px solid var(--line);background:var(--bg-2);
  color:var(--text-dim);border-radius:8px;padding:.28rem .45rem;margin:0;width:auto;cursor:pointer}
.fu-rapido button:hover,.fu-rapido button:focus-visible{border-color:var(--neon-borda);color:var(--neon-bright);outline:none}
/* o balão da conversa: a mesma marcação do card do funil (kbmsg), pro vendedor
   ver onde parou sem abrir o lead */
.fu-msg{display:flex;gap:.4rem;align-items:flex-start;margin-top:.4rem;padding-top:.4rem;
  border-top:1px solid var(--line);font-size:.74rem;color:var(--text-dim);line-height:1.35}
.fu-msg .bolha{flex:0 0 auto;width:.5rem;height:.5rem;border-radius:50%;background:var(--neon);margin-top:.3rem}
.fu-msg .eu{flex:0 0 auto;color:var(--text-faint);font-size:.8em;margin-top:.1rem}
.fu-msg .txt{min-width:0;flex:1;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.fu-msg .qdo{flex:0 0 auto;color:var(--text-faint);font-size:.68rem}
.fu-msg.nova .txt{color:var(--text)}
/* a prévia vira BOTÃO quando existe conversa: é ela que abre o balão. Largura
   cheia e fundo transparente pra continuar parecendo a linha de texto que era —
   o `width:100%;margin-top:1.4rem` global dos formulários é vencido aqui. */
button.fu-msg{width:100%;margin:.4rem 0 0;padding:.4rem 0 0;background:none;text-align:left;
  cursor:pointer;font-family:inherit;font-size:.74rem;border:0;border-top:1px solid var(--line)}
button.fu-msg .zap{flex:0 0 auto;opacity:.85;font-size:.9rem}
button.fu-msg:hover .txt,button.fu-msg:focus-visible .txt{color:var(--neon-bright)}
button.fu-msg:hover .zap,button.fu-msg:focus-visible .zap{opacity:1}
button.fu-msg:focus-visible{outline:1px solid var(--neon-borda);outline-offset:2px}
.fu-gest{width:100%;border-collapse:collapse;font-size:.8rem}
.fu-gest th{font:500 .6rem var(--mono);letter-spacing:.06em;text-transform:uppercase;color:var(--text-faint);text-align:left;padding:.4rem .5rem;border-bottom:1px solid var(--line)}
.fu-gest td{padding:.45rem .5rem;border-bottom:1px solid var(--line);font-variant-numeric:tabular-nums}
.fu-gest td.n{text-align:right}
.fu-gest .ruim{color:var(--coral)}.fu-gest .amb{color:var(--ambar)}
.fu-nota{font-size:.74rem;color:var(--text-faint);border-left:2px solid var(--line);padding-left:.6rem}
.fu-erro{font-size:.78rem;color:#F2BDB9;background:var(--coral-fundo);border:1px solid var(--coral-borda);border-radius:8px;padding:.45rem .6rem}
.fu-vazio{padding:1.4rem .9rem;text-align:center;color:var(--text-faint);font-size:.85rem}
/* ---- o interruptor que veio da Régua (07/09/2026) ---- */
.fu-modo{display:flex;gap:1rem;align-items:center;flex-wrap:wrap;
  border:1px solid var(--line);border-radius:10px;padding:.7rem .85rem;margin:.9rem 0 .2rem}
.fu-modo .txt{flex:1;min-width:230px}
.fu-modo .txt b{font-size:.9rem}
.fu-modo .txt small{display:block;color:var(--text-dim);font-size:.79rem;margin-top:.1rem}
/* o segmentado: três botões de submit colados, o ativo pintado. São BOTÕES e não
   rádios porque a escolha grava na hora — sem "salvar" pra esquecer de apertar. */
.fu-seg{display:inline-flex;border:1px solid var(--line);border-radius:9px;overflow:hidden;
  flex:none;margin:0;width:auto}
.fu-seg button{font:inherit;font-size:.78rem;padding:.34rem .68rem;margin:0;width:auto;
  border:0;border-right:1px solid var(--line);border-radius:0;background:transparent;
  color:var(--text-dim);cursor:pointer;line-height:1.3}
.fu-seg button:last-child{border-right:0}
.fu-seg button:hover{color:var(--text)}
.fu-seg button.on{background:var(--neon);color:var(--sobre-verde);font-weight:700}
.fu-seg button.observando.on{background:var(--azul);color:#04131B}
.fu-seg button.off.on{background:var(--line);color:var(--text)}
.fu-selo{font-size:.75rem;border:1px solid var(--line);border-radius:20px;padding:.15rem .55rem;color:var(--text-dim)}
.fu-selo.ligado{color:var(--neon);border-color:var(--neon-borda);background:var(--neon-fundo)}
.fu-selo.observando{color:var(--azul);border-color:var(--azul-borda);background:var(--azul-fundo)}
/* ---- quem não está sendo avisado ----
   Fica ACIMA do card de entrega de propósito: o card responde "como foi", e isto
   responde "tem alguém fora?" — que é a pergunta que muda o que a pessoa faz agora. */
.fu-mudo{border:1px solid var(--coral-borda);background:var(--coral-fundo);border-radius:10px;
  padding:.6rem .8rem;margin:.9rem 0 .2rem}
.fu-mudo .tit{font-size:.82rem;font-weight:600;color:#F2BDB9;margin-bottom:.3rem}
.fu-mudo ul{margin:0;padding:0;list-style:none}
.fu-mudo li{font-size:.8rem;color:var(--text-dim);padding:.16rem 0}
.fu-mudo b{color:var(--text)}
.fu-mudo .comof{font-size:.73rem;color:var(--text-faint);margin:.35rem 0 0}
/* ---- como os avisos chegaram (migração 284) ----
   Um canal por LINHA, e não três colunas lado a lado, porque os três não medem a
   mesma coisa: forçar a mesma grade faria o e-mail parecer que só vai mal. */
.fu-ent{border:1px solid var(--line);border-radius:10px;background:var(--bg-2);margin:.7rem 0 .2rem}
.fu-ent .cab{display:flex;align-items:baseline;gap:.5rem;flex-wrap:wrap;
  padding:.6rem .85rem;border-bottom:1px solid var(--line)}
.fu-ent .cab b{font-size:.88rem}
.fu-ent .cab span{font-size:.75rem;color:var(--text-faint)}
.fu-ent .linha{display:flex;gap:.7rem;align-items:center;padding:.6rem .85rem;
  border-bottom:1px solid var(--line)}
.fu-ent .linha:last-of-type{border-bottom:0}
.fu-ent .ic{flex:0 0 auto;font-size:1.05rem;line-height:1}
.fu-ent .cn{flex:1;min-width:0}
.fu-ent .ct{font-size:.78rem;color:var(--text-dim);margin-bottom:.3rem}
.fu-kpis{display:flex;gap:.4rem;flex-wrap:wrap}
.fu-kpi{border:1px solid var(--line);border-radius:8px;padding:.3rem .55rem;min-width:4.6rem}
.fu-kpi b{display:block;font:600 1.05rem var(--mono);font-variant-numeric:tabular-nums}
.fu-kpi span{display:block;font-size:.62rem;letter-spacing:.06em;text-transform:uppercase;
  color:var(--text-faint);margin-top:.1rem}
.fu-kpi.g b{color:var(--neon)}
.fu-kpi.a b{color:var(--ambar)}
.fu-kpi.r b{color:var(--coral)}
/* o travessão do que NÃO SE MEDE. Zero aqui se leria como "ninguém abriu" — e o
   que existe é ausência de medição, não ausência de leitura. */
.fu-kpi.na b{color:var(--text-faint)}
.fu-ent .nota{font-size:.73rem;color:var(--text-faint);padding:.5rem .85rem;border-top:1px solid var(--line)}
/* ---- o histórico do aviso, pessoa por pessoa (19/09/2026) ----
   Pedido do dono, mostrando o card do lead na campanha: "quero que lá no
   follow-up fique assim". Mesma ideia — hora e estado de cada passo — com o DIA
   no lugar do passo da régua e três canais em vez de dois.
   `details/summary` e não JS: a linha abre sem script, funciona com a página
   ainda carregando, e o estado aberto não se perde num refresh acidental. */
.fu-hist{border-top:1px solid var(--line)}
.fu-hist>.ct{padding:.6rem .85rem .2rem}
.fu-p{border-top:1px solid var(--line)}
.fu-p:first-of-type{border-top:0}
.fu-p>summary{display:flex;align-items:center;gap:.6rem;flex-wrap:wrap;cursor:pointer;
  padding:.55rem .85rem;list-style:none}
.fu-p>summary::-webkit-details-marker{display:none}
.fu-p>summary::before{content:"▸";color:var(--text-faint);font-size:.7rem;flex:none}
.fu-p[open]>summary::before{content:"▾";color:var(--neon)}
.fu-p .nome{flex:1;min-width:11rem}
.fu-p .nome b{display:block;font-size:.84rem}
.fu-p .nome span{display:block;font-size:.71rem;color:var(--text-faint)}
.fu-selo2{font:500 .68rem var(--mono);border-radius:999px;padding:.1rem .5rem;
  border:1px solid var(--line);color:var(--text-dim);white-space:nowrap}
.fu-selo2.ok{color:var(--verde-claro);border-color:var(--neon-borda);background:var(--neon-fundo)}
.fu-selo2.alerta{color:var(--ambar);border-color:var(--ambar-borda);background:var(--ambar-fundo)}
.fu-selo2.ruim{color:#F2BDB9;border-color:var(--coral-borda);background:var(--coral-fundo)}
.fu-p .ult{font:500 .73rem var(--mono);color:var(--text-dim);white-space:nowrap;
  font-variant-numeric:tabular-nums}
.fu-p .ult .g{color:var(--neon)}.fu-p .ult .a{color:var(--ambar)}.fu-p .ult .r{color:var(--coral)}
.fu-cols{display:grid;grid-template-columns:repeat(3,1fr);gap:1.1rem;padding:.2rem .85rem .9rem 1.9rem;
  background:var(--bg)}
@media (max-width:760px){.fu-cols{grid-template-columns:1fr;gap:.8rem}}
.fu-col h5{font:600 .74rem var(--display);color:var(--text-dim);margin:0 0 .3rem}
.fu-ev{display:flex;gap:.6rem;font-size:.76rem;padding:.2rem 0}
.fu-ev .h{font-family:var(--mono);font-size:.7rem;color:var(--text-faint);white-space:nowrap;
  font-variant-numeric:tabular-nums;flex:none;width:5.6rem}
.fu-ev .t{color:var(--text-dim)}
.fu-ev .t b{color:var(--text)}
.fu-ev.g .t b{color:var(--neon)}
.fu-ev.a .t b{color:var(--ambar)}
.fu-ev.r .t b{color:var(--coral)}
.fu-ev .nota{display:block;font-size:.69rem;color:var(--text-faint)}
.fu-ev .tag{font:500 .61rem var(--mono);border:1px solid var(--line);border-radius:4px;
  padding:0 .25rem;color:var(--text-faint);margin-left:.25rem}
.fu-mais{font-size:.72rem;color:var(--text-faint)}
.fu-mais>summary{cursor:pointer;list-style:none;color:var(--azul)}
.fu-mais>summary::-webkit-details-marker{display:none}
.fu-nada{font-size:.73rem;color:var(--text-faint);padding:.2rem 0}
/* ---- como funciona ---- */
.fu-ajuda{border:1px solid var(--line);border-radius:10px;margin-top:1.4rem;background:var(--bg-2)}
.fu-ajuda>summary{cursor:pointer;padding:.7rem .85rem;font-size:.86rem;font-weight:600;list-style:none}
.fu-ajuda>summary::-webkit-details-marker{display:none}
.fu-ajuda>summary::before{content:"▸ ";color:var(--text-faint)}
.fu-ajuda[open]>summary::before{content:"▾ "}
.fu-ajuda .corpo{padding:0 .85rem .9rem;font-size:.85rem;color:var(--text-dim);line-height:1.6}
.fu-ajuda h4{font-size:.7rem;letter-spacing:.08em;text-transform:uppercase;color:var(--text-faint);
  margin:1rem 0 .4rem;font-weight:600}
.fu-ajuda b{color:var(--text)}
.fu-passos{margin:0;padding:0;list-style:none;counter-reset:fp}
.fu-passos li{counter-increment:fp;position:relative;padding:.45rem 0 .45rem 1.9rem;border-top:1px solid var(--line)}
.fu-passos li::before{content:counter(fp);position:absolute;left:0;top:.45rem;font:500 .7rem var(--mono);
  color:var(--neon);border:1px solid var(--neon-borda);border-radius:4px;padding:0 .3rem;line-height:1.5}
.fu-chips{display:flex;gap:.35rem;flex-wrap:wrap;margin-top:.45rem}
.fu-chip{font:500 .74rem var(--mono);border:1px solid var(--line);border-radius:7px;
  padding:.24rem .5rem;color:var(--text-dim);background:var(--bg)}
.fu-chip b{color:var(--text)}
.fu-chip.g{border-color:var(--coral-borda);background:var(--coral-fundo);color:var(--coral)}
.fu-chip.g b{color:var(--coral)}
</style>
<div class="fu">
""" + _navbar("follow_up") + r"""
  <div>
    <h1>Follow-up</h1>
    <p class="lede">A próxima ação de cada lead em jogo, proposta pelo sistema e corrigida por você.
    O relógio lê a conversa — mensagem enviada pelo celular também conta. Abrir o card não encerra nada.</p>
  </div>

  {#- O INTERRUPTOR MORA AQUI desde 07/09/2026. Ele era da Régua do funil, e esta
      tela dizia "ligue na Régua" — mandava a pessoa pra outra tela pra ligar o
      que ela estava olhando. Quem liga é dono ou gestor; o vendedor lê o estado
      e não vê botão, como no resto da régua. -#}
  <div class="fu-modo">
    <div class="txt">
      <b>Follow-up automático</b>
      <small>marca a próxima ação de cada lead e cobra quando ela vence</small>
    </div>
    {% if papel in ('dono','gestor') %}
    <form method="post" action="/painel/follow-up/modo" class="fu-seg">
      {% for v, r in [('off','Desligado'),('observando','Observando'),('ligado','Ligado')] %}
      <button type="submit" name="modo" value="{{ v }}" class="{{ v }}{% if modo==v %} on{% endif %}">{{ r }}</button>
      {% endfor %}
    </form>
    {% else %}
    <span class="fu-selo {{ modo }}">{{ {'off':'desligado','observando':'em ensaio','ligado':'ligado'}[modo] }}</span>
    {% endif %}
  </div>
  {% if modo == 'off' %}<p class="lede" style="color:var(--ambar)">Está <b>desligado</b>: a tela mostra o quadro, mas ninguém recebe push nem e-mail.</p>
  {% elif modo == 'observando' %}<p class="lede" style="color:var(--azul)">Está em <b>ensaio</b>: o sistema calcula e grava o que mandaria, sem mandar nada a ninguém.</p>{% endif %}

  {#- O TERCEIRO CANAL (migração 280). Só aparece com o motor LIGADO: oferecer
      "mandar também no WhatsApp" com a cobrança desligada seria oferecer um canal
      pra um aviso que não existe. E só pra dono/gestor, como o interruptor de cima —
      o vendedor não decide o que chega no celular da equipe inteira. -#}
  {% if modo == 'ligado' and papel in ('dono','gestor') %}
  <div class="fu-modo">
    <div class="txt">
      <b>Avisar também no WhatsApp</b>
      <small>a mesma mensagem do e-mail, no número de cada vendedor, uma vez por dia</small>
    </div>
    <form method="post" action="/painel/follow-up/zap" class="fu-seg">
      {% for v, r in [(0,'Não'),(1,'Sim')] %}
      <button type="submit" name="zap" value="{{ v }}" class="{{ 'ligado' if v else 'off' }}{% if (1 if zap else 0)==v %} on{% endif %}">{{ r }}</button>
      {% endfor %}
    </form>
  </div>
  {% if zap %}<p class="lede" style="color:var(--verde-claro)">Sai pelo chip que a empresa usa nos recados internos. Quem não tem número cadastrado continua recebendo só por e-mail e push.
    <button type="button" id="zaptest" onclick="fuTestarZap(this)" style="width:auto;margin:0 0 0 .5rem;font-size:.76rem;padding:.2rem .6rem;background:none;border:1px solid var(--borda);color:var(--txt-mut);border-radius:7px;cursor:pointer">Testar agora</button>
    <span id="zaptest-r" class="mut" style="font-size:.76rem"></span></p>
  <script>
  // Manda o aviso REAL de cada vendedor, agora. Não gasta o teto do dia nem marca
  // nada como cobrado — ver `follow_up.testar_zap`. O relato vem pessoa a pessoa
  // porque um "deu certo" agregado esconderia quem ficou de fora.
  function fuTestarZap(b){
    var t=b.textContent, r=document.getElementById('zaptest-r');
    b.disabled=true; b.textContent='Mandando…'; r.textContent='';
    zapFetch('/painel/follow-up/zap-testar',{method:'POST',headers:{'X-Requested-With':'fetch'}}).then(function(d){if(!d){b.disabled=false;b.textContent=t;return;}
        b.disabled=false; b.textContent=t;
        if(!d.ok){r.textContent=' '+(d.erro||'não deu');return;}
        if(!(d.envios||[]).length){r.textContent=' ninguém com lead vencido agora';return;}
        r.textContent=' ' + d.envios.map(function(e){
          return e.nome+': '+(e.ok?('enviado ('+e.n_leads+' leads)'):('NÃO saiu — '+(e.erro||'?')));
        }).join(' · ');
      });
  }
  </script>{% endif %}

  {#- QUEM NÃO ESTÁ SENDO AVISADO. Três motivos, e o terceiro é o que ficou dois
      dias escondido na Prime: o WhatsApp aceita o envio e o aparelho nunca
      confirma, então o registro diz "enviado ✓" e ninguém desconfia. -#}
  {% if sem_aviso %}
  <div class="fu-mudo">
    <div class="tit">⚠️ {{ sem_aviso|length }} pessoa{{ 's' if sem_aviso|length > 1 }} não
      {{ 'estão' if sem_aviso|length > 1 else 'está' }} recebendo o aviso</div>
    <ul>
      {% for a in sem_aviso %}
      <li><b>{{ a.quem }}</b> — {{ a.detalhe }}</li>
      {% endfor %}
    </ul>
    <p class="comof">Corrige em <a href="/painel/equipe" style="color:var(--azul)">Empresa → Equipe</a>:
      e-mail e WhatsApp da pessoa. O push depende de ela abrir o app e aceitar as notificações.</p>
  </div>
  {% endif %}

  {#- COMO OS AVISOS CHEGARAM (migração 284). Só dono e gestor, por decisão do dono
      em 18/09/2026: o vendedor ver a própria taxa é justo, ver a dos colegas vira
      placar — e a régua tem o cuidado de não virar fofoca sobre ninguém.
      Cada canal sabe dizer uma coisa diferente, e o card não finge o contrário.

      "ENVIADOS" QUER DIZER A MESMA COISA NAS TRÊS LINHAS: quantos SAÍRAM. Nasceu
      torto — o WhatsApp contava só o que saiu, o push contava as tentativas
      (somando o que falhou) e o e-mail escondia as falhas — e o dono pegou no
      mesmo dia: "acho que não tá batendo o número". Três contas diferentes na
      mesma tabela não dá pra conferir de cabeça.

      A conta que fecha agora, em qualquer linha:
          enviados + a última coluna (vermelha) = tudo que o sistema tentou.
      O "aceitos" do push saiu: era o mesmo número de "enviados", porque push que
      não alcança aparelho nenhum já conta como falha. -#}
  {% if entrega and entrega.tem %}
  <div class="fu-ent">
    <div class="cab"><b>Como os avisos chegaram</b><span>últimos {{ entrega.dias }} dias</span></div>

    <div class="linha">
      <div class="ic">💬</div>
      <div class="cn">
        <div class="ct">WhatsApp</div>
        <div class="fu-kpis">
          <div class="fu-kpi"><b>{{ entrega.whatsapp.ok }}</b><span>enviados</span></div>
          <div class="fu-kpi g"><b>{{ entrega.whatsapp.entregues }}</b><span>entregues ✓✓</span></div>
          <div class="fu-kpi g"><b>{{ entrega.whatsapp.lidos }}</b><span>lidos 👀</span></div>
          <div class="fu-kpi a"><b>{{ entrega.whatsapp.sem_recibo }}</b><span>sem recibo</span></div>
          <div class="fu-kpi r"><b>{{ entrega.whatsapp.falhas }}</b><span>não saíram</span></div>
        </div>
      </div>
    </div>

    <div class="linha">
      <div class="ic">🔔</div>
      <div class="cn">
        <div class="ct">Push no app</div>
        <div class="fu-kpis">
          <div class="fu-kpi"><b>{{ entrega.push.ok }}</b><span>enviados</span></div>
          <div class="fu-kpi na" title="o serviço de push aceitar não é o aparelho mostrar"><b>—</b><span>não se sabe</span></div>
          <div class="fu-kpi g" title="tocou na notificação — quem toca abriu o painel"><b>{{ entrega.push.clicados }}</b><span>abriram</span></div>
          <div class="fu-kpi r"><b>{{ entrega.push.falhas }}</b><span>sem aparelho</span></div>
        </div>
      </div>
    </div>

    <div class="linha">
      <div class="ic">📧</div>
      <div class="cn">
        <div class="ct">E-mail</div>
        <div class="fu-kpis">
          <div class="fu-kpi"><b>{{ entrega.email.ok }}</b><span>enviados</span></div>
          <div class="fu-kpi na" title="o servidor aceitar não é a caixa receber"><b>—</b><span>não se sabe</span></div>
          <div class="fu-kpi na" title="abertura de e-mail só se mede com pixel, e o proxy do Gmail pré-carrega imagem: o número seria alto e falso"><b>—</b><span>não se mede</span></div>
          <div class="fu-kpi r"><b>{{ entrega.email.falhas }}</b><span>sem endereço</span></div>
        </div>
      </div>
    </div>

    {#- UMA LINHA DO HISTÓRICO. Cada canal diz o que SABE dizer, e nada além:
        o WhatsApp tem entrega e leitura de verdade; o push tem o toque; o e-mail
        tem só "saiu". O "Abriu 👁" que a campanha mostra no e-mail NÃO entra aqui
        de propósito — 62 das 69 "aberturas" desta base aconteceram em menos de um
        minuto, que é o proxy do Gmail buscando o pixel, não gente lendo (a conta
        está escrita na nota do `_RADAR_BALDES`, em web/painel_prospeccao). -#}
  {% macro ev(e, canal) %}
    {% if not e.ok %}
    <div class="fu-ev r"><span class="h">{{ br(e.quando) }}</span><span class="t"><b>Não saiu</b>
      {% if e.motivo %}<span class="nota">{{ e.motivo }}</span>{% endif %}</span></div>
    {% else %}
    <div class="fu-ev"><span class="h">{{ br(e.quando) }}</span><span class="t"><b>Enviado</b>{% if e.teste %}<span class="tag">teste</span>{% endif %}
      {% if e.n_leads %}<span class="nota">{{ e.n_leads }} lead{{ 's' if e.n_leads > 1 }}</span>{% endif %}</span></div>
      {% if e.entregue_em %}
      <div class="fu-ev g"><span class="h">{{ br(e.entregue_em) }}</span><span class="t"><b>Entregue ✓✓</b></span></div>
      {% endif %}
      {% if e.lido_em %}
      <div class="fu-ev g"><span class="h">{{ br(e.lido_em) }}</span><span class="t"><b>Leu 👀</b></span></div>
      {% endif %}
      {% if e.clicado_em %}
      <div class="fu-ev g"><span class="h">{{ br(e.clicado_em) }}</span><span class="t"><b>Abriu</b>
        <span class="nota">tocou na notificação</span></span></div>
      {% endif %}
      {% if canal == 'whatsapp' and not e.entregue_em and not e.lido_em %}
      <div class="fu-ev a"><span class="h">—</span><span class="t"><b>Sem recibo</b>
        <span class="nota">pode ser confirmação de leitura desligada no aparelho</span></span></div>
      {% endif %}
    {% endif %}
  {% endmacro %}

    {#- O HISTÓRICO, pessoa por pessoa. Substituiu a barra de leitura por vendedor:
        a barra dizia a taxa e escondia o caminho, e o dono pediu o caminho —
        "quero que fique assim", mostrando o card do lead na campanha.
        A linha FECHADA já responde "chegou nele?"; abrir é pra ver como. -#}
    {% if entrega.hist %}
    <div class="fu-hist">
      <div class="ct">O aviso de cada pessoa</div>
      {% for p in entrega.hist %}
      <details class="fu-p">
        <summary>
          <span class="nome"><b>{{ p.quem }}</b>
            <span>{{ p.email or 'sem e-mail' }}{% if p.numero %} · {{ p.numero }}{% endif %}</span></span>
          <span class="fu-selo2 {{ p.selo_cls }}">{{ p.selo }}</span>
          <span class="ult">{% if p.n_leads %}{{ p.n_leads }} lead{{ 's' if p.n_leads > 1 }} · {% endif %}<span class="{{ p.sinal_cls }}">{{ p.sinal }}</span></span>
        </summary>
        {% if p.alerta %}<p class="fu-nada" style="padding:0 0 .3rem 1.9rem;color:var(--ambar)">{{ p.alerta.detalhe }}</p>{% endif %}
        <div class="fu-cols">
          {% for canal, titulo in [('whatsapp','💬 WhatsApp'), ('push','🔔 Push no app'), ('email','📧 E-mail')] %}
          {% set ch = p.canais.get(canal) or {'recentes': [], 'antigos': []} %}
          <div class="fu-col">
            <h5>{{ titulo }}</h5>
            {% for e in ch.recentes %}{{ ev(e, canal) }}{% else %}
            <div class="fu-nada">nada nos últimos 7 dias</div>{% endfor %}
            {% if ch.antigos %}
            <details class="fu-mais"><summary>ver os 30 dias ({{ ch.antigos|length }} a mais)</summary>
              {% for e in ch.antigos %}{{ ev(e, canal) }}{% endfor %}
            </details>
            {% endif %}
          </div>
          {% endfor %}
        </div>
      </details>
      {% endfor %}
    </div>
    {% endif %}

    <p class="nota"><b>Enviados</b> é o que saiu de verdade, nas três linhas; a última coluna é o
    que não saiu, e por quê — somando as duas você tem tudo que o sistema tentou.
    “Sem recibo” não quer dizer que não chegou: quem desliga a confirmação de leitura no WhatsApp
    nunca gera o 👀. E o travessão é onde não há dado — em vez de zero, que se leria como
    “ninguém abriu”.</p>
  </div>
  {% endif %}
  {% endif %}

  {% if erro == 'motivo_obrigatorio' %}
    <div class="fu-erro">Este lead já foi adiado {{ adia_max - 1 }} vezes sem ninguém falar com o cliente. Pra adiar de novo, escreva o motivo.</div>
  {% elif erro == 'data_invalida' %}<div class="fu-erro">Escolha uma data pra próxima ação.</div>
  {% elif erro %}<div class="fu-erro">Não deu pra remarcar agora. Tente de novo.</div>{% endif %}

  {% macro qs(e) %}?estado={{ e }}{% if vend_f %}&vendedor={{ vend_f }}{% endif %}{% if etapa_f %}&etapa={{ etapa_f }}{% endif %}{% endmacro %}
  <div class="fu-tiles">
    <a class="fu-tile hoje {% if estado=='hoje' %}on{% endif %}" href="/painel/follow-up{{ qs('hoje') }}">
      <b>{{ topo.hoje }}</b><span>FOLLOW-UPS HOJE</span><em>{{ topo.andamento }} em andamento</em></a>
    <a class="fu-tile atrasado {% if estado=='atrasado' %}on{% endif %}" href="/painel/follow-up{{ qs('atrasado') }}">
      <b>{{ topo.atrasado }}</b><span>ATRASADOS</span><em>até 72h do prazo</em></a>
    <a class="fu-tile critico {% if estado=='critico' %}on{% endif %}" href="/painel/follow-up{{ qs('critico') }}">
      <b>{{ topo.critico }}</b><span>CRÍTICOS +72H</span><em>{{ topo.com_festa }} com data marcada</em></a>
    <a class="fu-tile sem_acao {% if estado=='sem_acao' %}on{% endif %}" href="/painel/follow-up{{ qs('sem_acao') }}">
      <b>{{ topo.sem_acao }}</b><span>SEM PRÓXIMA AÇÃO</span><em>a data já passou</em></a>
  </div>

  <form method="get" action="/painel/follow-up" class="fu-bar">
    <input type="hidden" name="estado" value="{{ estado }}">
    {% if vendedores %}
    <label class="{{ 'on' if vend_f }}">Vendedor
      <select name="vendedor" onchange="this.form.submit()"><option value="">todos</option>
        {% for vid, vnome in vendedores %}<option value="{{ vid }}" {% if vend_f==vid %}selected{% endif %}>{{ vnome }}</option>{% endfor %}
      </select></label>
    {% endif %}
    {% if etapas %}
    <label class="{{ 'on' if etapa_f }}">Etapa
      <select name="etapa" onchange="this.form.submit()"><option value="">todas</option>
        {% for e in etapas %}<option value="{{ e }}" {% if etapa_f==e %}selected{% endif %}>{{ (rot_etapa or {}).get(e, e) }}</option>{% endfor %}
      </select></label>
    {% endif %}
    <label class="{{ 'on' if estado=='todos' }}" style="cursor:pointer" onclick="location.href='/painel/follow-up{{ qs('todos') }}'">Ver todos os {{ topo.ativos }}</label>
  </form>

  <div class="fu-lista">
    {% for x in fila %}
    <div class="fu-lead">
      <div>
        <div class="nome">
          <a href="/painel/prospeccao/{{ x.id }}">{{ x.quem }}</a>
          <span class="fu-temp {{ x.temperatura }}">{{ x.temperatura }}</span>
          <span class="fu-pill {{ x.estado }}">{{ emoji[x.estado] }} {{ rotulo[x.estado] }}</span>
          <span class="fu-pill">{{ x.bola }}</span>
          {% if por_temp %}<span class="fu-nivel" title="nível da fila de prioridade">{{ x.prioridade }}º {{ rot_prio[x.prioridade] }}</span>{% endif %}
          {% if x.adiados >= adia_max %}<span class="fu-pill hoje" title="sem nenhuma mensagem no meio">🔁 adiado {{ x.adiados }}× sem falar</span>{% endif %}
        </div>
        <div class="meta">
          {% if perfil.vocab.data %}
            {% if x.evento_em %}<span><i>🎉</i>{{ x.evento_tipo or 'Festa' }} <b>{{ x.evento_em.strftime('%d/%m') }}</b>{% if x.faltam is not none %} · {% if x.faltam < 0 %}<b style="color:var(--coral)">a data passou</b>{% else %}em {{ x.faltam }} dias{% endif %}{% endif %}</span>
            {% else %}<span><i>🎉</i>sem data definida</span>{% endif %}
            {% if x.convidados %}<span><i>👥</i>{{ x.convidados }} convidados</span>{% endif %}
          {% endif %}
          <span><i>⏱️</i><b>{{ tempo(x.parado_h) }}</b> sem interação</span>
          <span><i>🔄</i>{{ x.tentativas }} tentativa{{ '' if x.tentativas == 1 else 's' }}</span>
          <span><i>📍</i>{{ (rot_etapa or {}).get(x.status, x.status) }}</span>
          <span><i>👤</i>{{ x.vendedor }}</span>
          {% if x.adiados and x.adiados < adia_max %}<span><i>🔁</i>adiado <b>{{ x.adiados }}×</b> sem mensagem no meio</span>{% endif %}
        </div>
        {% if x.msg %}
        {% if x.msg.conversa_id %}
        <button type="button" class="fu-msg abre{% if x.msg.nova %} nova{% endif %}"
                onclick="kbAbrirChat(event,{{ x.msg.conversa_id }},'{{ x.msg.aba }}',this,{{ x.quem|tojson|forceescape }})"
                title="abrir a conversa aqui mesmo">
          {% if x.msg.nova %}<span class="bolha" aria-hidden="true"></span>
          {% elif x.msg.minha %}<span class="eu" aria-hidden="true">↩</span>{% endif %}
          <span class="txt">{{ resumo_msg(x.msg.texto) }}</span>
          <span class="qdo">{{ quando_curto(x.msg.em) }}</span>
          <span class="zap" aria-hidden="true">💬</span>
        </button>
        {% else %}
        <div class="fu-msg{% if x.msg.nova %} nova{% endif %}">
          {% if x.msg.nova %}<span class="bolha" aria-hidden="true"></span>
          {% elif x.msg.minha %}<span class="eu" aria-hidden="true">↩</span>{% endif %}
          <span class="txt">{{ resumo_msg(x.msg.texto) }}</span>
          <span class="qdo">{{ quando_curto(x.msg.em) }}</span>
        </div>
        {% endif %}
        {% endif %}
        {% if x.estado == 'sem_acao' %}
          <div class="fu-acao calma">A data já passou. <b>Encerre com motivo, ou remarque.</b></div>
        {% else %}
          <div class="fu-acao"><span>📅 Próxima ação:</span> <b>{{ x.acao }}</b>
            {% if x.atraso_h > 0 %}— venceu há {{ tempo(x.atraso_h) }}{% else %}— {{ br(x.prazo) }}{% endif %}
            {% if x.na_mao %} · marcada na mão{% endif %}</div>
        {% endif %}
        {% if x.toques %}
        {# AS TENTATIVAS COMO TAREFAS (migração 233). Elas já nascem com data ao
           entrar na etapa — aqui a tela só mostra o que o motor calculou, que é o
           que "sem depender da lembrança do vendedor" quer dizer na prática. #}
        <div class="fu-toques">
          {% for t in x.toques %}
          <span class="fu-toque {% if t.feito %}feito{% elif t.atrasado %}atrasado{% endif %}"
                title="{{ br(t.prazo) }}">{% if t.feito %}✓{% else %}D{{ t.dia }}{% endif %}</span>
          {% endfor %}
          <span class="mut" style="font-size:.72rem">
            {% set faltam = x.toques | rejectattr('feito') | list | length %}
            {% if faltam %}{{ faltam }} de {{ x.toques|length }} tentativa(s) pendente(s){% else %}as {{ x.toques|length }} tentativas foram feitas{% endif %}
          </span>
        </div>
        {% endif %}
      </div>
      <div class="fu-dir">
        {#- ABRE A JANELA, não navega (16/09/2026, pedido do dono: "mudar o status
            pode ser por lá e só deixar abrir uma janela igual tem no funil"). Sair
            da tela pra ver um lead custava a fila inteira de volta e o lugar onde
            a pessoa estava; e o que ela precisa ali é pequeno — "este já fechou,
            muda de contato pra ganho". A ficha completa continua a um clique,
            no rodapé da própria janela ("Ver ficha completa ↗"). -#}
        <button type="button" class="bt forte" onclick="kbAbrirLead(event,{{ x.id }},this)"
                title="ver os dados, o histórico e mudar a situação sem sair da fila">Abrir ficha</button>
        {# UM TOQUE, não um formulário. O prazo já nasce proposto; remarcar é
           quase sempre "empurra pra amanhã / pra semana que vem", e obrigar a
           escolher dia, hora e texto pra isso é o mesmo erro de pedir que o
           vendedor preencha a próxima ação. A data exata continua a um clique,
           em "Outra data" — e, batida a trava, ela é o único caminho, porque o
           motivo não pode ser pulado. #}
        {% if x.adiados < adia_max - 1 %}
        <form method="post" action="/painel/follow-up/reagendar" class="fu-rapido">
          <input type="hidden" name="lead_id" value="{{ x.id }}">
          <input type="hidden" name="volta" value="/painel/follow-up{{ qs(estado) }}">
          <button type="submit" name="dias" value="1" title="remarcar pra amanhã, 9h">Amanhã</button>
          <button type="submit" name="dias" value="3" title="remarcar pra daqui a 3 dias">+3d</button>
          <button type="submit" name="dias" value="7" title="remarcar pra daqui a 7 dias">+7d</button>
        </form>
        {% endif %}
        <details>
          <summary>{% if x.adiados >= adia_max - 1 %}Remarcar com motivo{% else %}Outra data{% endif %}</summary>
          <form method="post" action="/painel/follow-up/reagendar" class="fu-form">
            <input type="hidden" name="lead_id" value="{{ x.id }}">
            <input type="hidden" name="volta" value="/painel/follow-up{{ qs(estado) }}">
            <input type="date" name="quando" required>
            <input type="time" name="hora" value="09:00">
            <input type="text" name="acao" maxlength="120" placeholder="o que fazer (opcional)">
            <input type="text" name="motivo" maxlength="300" placeholder="motivo{% if x.adiados >= adia_max - 1 %} (obrigatório){% endif %}"{% if x.adiados >= adia_max - 1 %} required{% endif %}>
            <span class="dica">{% if x.adiados >= adia_max - 1 %}Já adiado {{ x.adiados }}× sem falar com o cliente: agora o motivo é obrigatório.{% else %}Do {{ adia_max }}º adiamento seguido sem falar com o cliente, o motivo passa a ser obrigatório.{% endif %}</span>
            <button type="submit">Remarcar</button>
          </form>
        </details>
      </div>
    </div>
    {% else %}
    <div class="fu-vazio">Nada em <b>{{ rotulo.get(estado, estado) }}</b> por aqui. Fila limpa.</div>
    {% endfor %}
  </div>
  {% if sobrando %}<p class="fu-nota">Mais {{ sobrando }} nesta fila. Filtre por vendedor ou etapa pra chegar neles.</p>{% endif %}

  {% if gestao %}
  <div>
    <h2 style="font-size:1.05rem;margin:.6rem 0 .4rem">Por vendedor</h2>
    <div style="overflow-x:auto;border:1px solid var(--line);border-radius:12px;background:var(--surface)">
      <table class="fu-gest">
        <thead><tr><th>Vendedor</th><th class="n">Ativos</th><th class="n">🟡 Hoje</th><th class="n">🔴 Atrasados</th><th class="n">🚨 Críticos</th><th class="n">⚠️ Sem ação</th><th class="n">Adiados {{ adia_max }}×+</th></tr></thead>
        <tbody>
          {% for v in gestao %}
          <tr>
            <td>{{ v.nome }}</td><td class="n">{{ v.ativos }}</td>
            <td class="n {{ 'amb' if v.hoje }}">{{ v.hoje }}</td>
            <td class="n {{ 'ruim' if v.atrasado }}">{{ v.atrasado }}</td>
            <td class="n {{ 'ruim' if v.critico }}">{{ v.critico }}</td>
            <td class="n">{{ v.sem_acao }}</td>
            <td class="n {{ 'amb' if v.adiados }}">{{ v.adiados or '—' }}</td>
          </tr>
          {% endfor %}
        </tbody>
      </table>
    </div>
    <p class="fu-nota" style="margin-top:.5rem">A coluna de adiamentos conta quem foi empurrado pra frente {{ adia_max }} vezes ou mais <b>sem nenhuma mensagem no meio</b>. Adiar depois de falar com o cliente é trabalho; adiar sem falar é adiar.</p>
  </div>
  {% endif %}
</div>
<script>{{ balao_js }}</script>
{#- `_KB_STATUS` é o que enche o seletor de situação da janela: o que
    `janela_lead.lista_de_status()` devolve pras etapas DESTA conta, ganho e perdido
    inclusive — é exatamente a troca que o dono pediu ("tem cliente que já fechou
    mais esta como contato"). Sem ela o seletor sai vazio, e é por isso que a janela
    lê `window._KB_STATUS`: uma tela que esqueça isto perde o seletor, não o clique
    inteiro. -#}
<script>var _KB_STATUS={{ (status or [])|tojson }};
// o portão do §6 dentro da janela: os campos do evento são declarados aqui, e
// só pra quem vende data — a conta de mensalidade não recebe nem a palavra.
{% if perfil.vocab.data %}{{ janela_evento_js }}{% endif %}</script>
<script>{{ janela_js }}</script>

  {#- AS REGRAS, ESCRITAS (07/09/2026, pedido do dono). Tudo aqui sai de
      finance/follow_up.py: a escada, os quatro degraus, os seis estados e os
      dois limites. Os números vêm da CONFIG da conta (`cfg`), não fixos no
      texto — conta que mexer nos prazos lê os dela. -#}
  <details class="fu-ajuda">
    <summary>Como o Zaq escolhe a próxima ação</summary>
    <div class="corpo">
      <h4>A ordem que o sistema segue</h4>
      <ol class="fu-passos">
        <li><b>Ninguém falou com ele ainda</b> → responder.</li>
        <li><b>O cliente respondeu por último</b> → responder, a bola é nossa.
          <br>Vem antes de tudo: cliente esperando é mais urgente que card parado.</li>
        <li><b>Proposta enviada</b> → cobrar retorno em <b>{{ cfg.fu_proposta_dias }} dia{{ '' if cfg.fu_proposta_dias == 1 else 's' }}</b>.</li>
        <li><b>Nenhum caso acima</b> → sobe a escada de toques.
          <div class="fu-chips">
            {% for d in cfg.fu_toques %}<span class="fu-chip{{ ' g' if loop.last }}"><b>{{ d }}d</b> {{ ['2º toque','3º toque','último desta rodada'][loop.index0] if loop.index0 < 3 else 'insistiu demais' }}</span>{% endfor %}
          </div></li>
        {% if perfil.vocab.data %}
        <li><b>A data da festa aperta o que estiver frouxo</b> → festa em até
          <b>{{ cfg.fu_festa_dias }} dias</b> sem proposta vence <b>hoje</b>, por mais
          recente que tenha sido a conversa.</li>
        {% endif %}
      </ol>
      <p style="margin:.7rem 0 0">O sistema <b>propõe</b> e você <b>corrige</b> — nada disso é
      obrigatório preencher.</p>

      <h4>O relógio lê a conversa, não o card</h4>
      <p style="margin:0">Interação é <b>mensagem trocada</b>, inclusive a que o vendedor manda
      pelo próprio celular. <b>Abrir o card, arrastar a coluna ou marcar como lido não encerram
      alerta nenhum</b>: o fato que gerou o aviso continua de pé. Em compensação, qualquer coisa
      que mude o prazo — uma mensagem enviada, uma proposta, um reagendamento — <b>zera a escada
      de avisos</b> na hora.</p>

      <h4>Quando vence, a cobrança sobe em quatro degraus</h4>
      <div class="fu-chips">
        <span class="fu-chip"><b>no vencimento</b> → o vendedor</span>
        <span class="fu-chip"><b>24h</b> → o vendedor de novo</span>
        <span class="fu-chip"><b>48h</b> → vendedor + gestor</span>
        <span class="fu-chip g"><b>72h</b> → destaque no painel da gestão</span>
      </div>

      <h4>Os seis estados</h4>
      <div class="fu-chips">
        <span class="fu-chip">🚨 Crítico · +72h</span>
        <span class="fu-chip">🔴 Atrasado · 24–72h</span>
        <span class="fu-chip">🟡 Hoje · até 24h</span>
        <span class="fu-chip">🔵 Agendado</span>
        <span class="fu-chip">🟢 Em andamento</span>
        <span class="fu-chip">⚠️ Sem próxima ação</span>
      </div>

      <h4>Duas travas, pra não virar metralhadora</h4>
      <p style="margin:0"><b>{{ adia_max }} adiamentos seguidos</b> sem falar com o cliente passam a
      exigir um motivo, e há um teto de <b>{{ cfg.fu_teto_dia }} avisos por dia</b> na conta.</p>
    </div>
  </details>
{% endblock %}"""

_env.loader.mapping["follow_up"] = _TPL
