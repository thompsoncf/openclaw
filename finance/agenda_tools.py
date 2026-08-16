"""Ferramentas de AGENDA do agente (escopadas por conta). Agenda própria do Zaq.

marcar_evento / ver_agenda / remarcar_evento / cancelar_evento. Cada resposta de
"marcar" já traz o link "adicionar ao Google" (1 toque, sem login). O lembrete
proativo (resumo do dia) e o feed .ics assinável vêm nas etapas 2 e 3.
"""
from __future__ import annotations

from datetime import timedelta

from core.agent import Ferramenta
from . import agenda as ag
from . import convites as cv


def construir_ferramentas_agenda(pool, conta_id: int,
                                 membro_id: int | None = None) -> list[Ferramenta]:

    def _linha(ev: dict) -> str:
        base = f"• {ag.fmt_hora(ev)} — {ev['titulo']}"
        if ev.get("local"):
            base += f" ({ev['local']})"
        # data SEGURADA esperando o sinal: entra na lista de propósito (dizer que o
        # dia está livre quando alguém já segurou é o erro caro), mas dita como o
        # que é — não é compromisso fechado.
        if ev.get("status") == ag.PRE_RESERVADO:
            base += " ⏳ data segurada, sinal pendente"
        return base

    def _convidar_e_enviar(ev: dict, convidados: list) -> list[str]:
        """Cria os convites do evento e TENTA disparar o template no WhatsApp de
        cada um (pelo número da empresa). Devolve linhas de status pro dono."""
        linhas = []
        for g in convidados or []:
            if not isinstance(g, dict):
                continue
            nome = (g.get("nome") or "").strip()
            contato = (g.get("contato") or "").strip()
            if not (nome or contato):
                continue
            conv = cv.criar_convidado(pool, conta_id, ev["id"], nome or None, contato or None)
            quem = nome or contato or "convidado"
            if not contato:
                linhas.append(f"• {quem}: sem número — me passa o WhatsApp (com DDD) "
                              f"que eu envio, ou compartilhe: {cv.url_convite(conv['token'])}")
                continue
            r = cv.enviar_convite_whatsapp(pool, conv["token"])
            if r.get("ok"):
                linhas.append(f"• {quem}: convite enviado no WhatsApp ✅")
            else:
                linhas.append(f"• {quem}: convite criado, mas não enviei automático "
                              f"({cv.motivo_erro(r.get('erro'))}). Link pra mandar: "
                              f"{cv.url_convite(conv['token'])}")
        return linhas

    def marcar_evento(e: dict) -> str:
        titulo = (e.get("titulo") or "").strip()
        inicio = ag.parse_datahora(e.get("inicio"))
        if not titulo:
            return "O que é o compromisso? Me diz o título (ex: 'reunião com o contador')."
        if not inicio:
            return ("Não entendi a data/hora. Me diz quando, tipo 'amanhã 15h' ou "
                    "'28/07 14:30'.")
        fim = ag.parse_datahora(e.get("fim")) if e.get("fim") else None
        try:
            lembrete = int(e["lembrete_min"]) if e.get("lembrete_min") is not None else None
        except (TypeError, ValueError):
            lembrete = None
        ev = ag.criar_evento(pool, conta_id, titulo, inicio, membro_id=membro_id,
                             fim=fim, local=(e.get("local") or None),
                             descricao=(e.get("descricao") or None), lembrete_min=lembrete)
        aviso_passado = " (repara que essa data já passou, hein!)" if inicio < ag.agora_brt() else ""
        resp = (f"📅 Marquei: {titulo} — {ag.fmt_hora(ev)}.{aviso_passado}\n"
                f"Adicionar ao seu calendário (Google/Apple/Outlook): {ag.link_google(ev)}")
        mapa = ag.link_maps(ev.get("local"))
        if mapa:
            resp += f"\n📍 Ver o local no mapa: {mapa}"
        convidados = e.get("convidados") or []
        nomes = [(g.get("nome") or "").strip() for g in convidados
                if isinstance(g, dict) and (g.get("nome") or "").strip()]
        if len(nomes) > 1:
            resp += f"\n\n👥 Com: {ag.frase_nomes(nomes)}"
        linhas = _convidar_e_enviar(ev, convidados)
        if linhas:
            resp += "\n\n👥 Convites:\n" + "\n".join(linhas)
        return resp

    def convidar_reuniao(e: dict) -> str:
        convidados = e.get("convidados") or []
        if not convidados:
            return "Quem você quer convidar? Me diz o nome e o WhatsApp (com DDD)."
        ev, erro = _resolver(e)
        if erro:
            return erro
        linhas = _convidar_e_enviar(ev, convidados)
        if not linhas:
            return "Não entendi os convidados. Me passa nome + WhatsApp (com DDD)."
        return (f"👥 Convites de *{ev['titulo']}* ({ag.fmt_hora(ev)}):\n"
                + "\n".join(linhas))

    def ver_agenda(e: dict) -> str:
        periodo = (e.get("periodo") or "").strip().lower()
        agora = ag.agora_brt()
        inicio_dia = agora.replace(hour=0, minute=0, second=0, microsecond=0)
        if periodo in ("hoje", "hj"):
            eventos = ag.listar_eventos(pool, conta_id, inicio_dia, inicio_dia + timedelta(days=1),
                                        incluir_pre_reserva=True)
            titulo = "hoje"
        elif periodo in ("amanha", "amanhã"):
            d = inicio_dia + timedelta(days=1)
            eventos = ag.listar_eventos(pool, conta_id, d, d + timedelta(days=1),
                                        incluir_pre_reserva=True)
            titulo = "amanhã"
        elif periodo in ("semana", "essa semana"):
            eventos = ag.listar_eventos(pool, conta_id, agora, inicio_dia + timedelta(days=7),
                                        incluir_pre_reserva=True)
            titulo = "os próximos 7 dias"
        else:
            eventos = ag.proximos(pool, conta_id, limite=15)
            titulo = "próximos compromissos"
        if not eventos:
            return f"Sem nada na agenda pra {titulo}. 🎉"
        return f"Sua agenda ({titulo}):\n" + "\n".join(_linha(ev) for ev in eventos)

    def _resolver(e: dict) -> tuple[dict | None, str | None]:
        """Acha o evento por id ou por título. Devolve (evento, mensagem_de_erro)."""
        eid = e.get("evento_id")
        if eid:
            try:
                alvo = int(eid)
            except (TypeError, ValueError):
                alvo = None
            if alvo:
                for ev in ag.proximos(pool, conta_id, limite=100):
                    if ev["id"] == alvo:
                        return ev, None
        termo = e.get("titulo") or ""
        cand = ag.achar_por_titulo(pool, conta_id, termo)
        if len(cand) == 1:
            return cand[0], None
        if len(cand) > 1:
            return None, ("Tem mais de um compromisso parecido: "
                          + "; ".join(f"{ag.fmt_hora(c)} {c['titulo']}" for c in cand[:5])
                          + ". Qual deles?")
        # Não bateu exato (a voz costuma trocar o nome) -> SUGERE o que tem na agenda.
        sug = ag.sugerir_por_titulo(pool, conta_id, termo)
        if sug:
            lista = "; ".join(f"{ag.fmt_hora(s)} {s['titulo']}" for s in sug)
            alvo = f" '{termo}'" if termo else ""
            return None, (f"Não achei{alvo} com esse nome exato. Na sua agenda tem: {lista}. "
                          f"É algum desses? Me diz o nome certinho (ou o horário).")
        return None, "Não achei esse compromisso na agenda. Confere o nome ou a data?"

    def remarcar_evento(e: dict) -> str:
        novo = ag.parse_datahora(e.get("novo_inicio") or e.get("inicio"))
        if not novo:
            return "Pra quando remarco? Me diz a nova data/hora (ex: 'sexta 10h')."
        ev, erro = _resolver(e)
        if erro:
            return erro
        novo_fim = ag.parse_datahora(e.get("novo_fim")) if e.get("novo_fim") else None
        if ag.remarcar_evento(pool, conta_id, ev["id"], novo, novo_fim):
            ev2 = {**ev, "inicio": novo, "fim": novo_fim}
            resp = (f"✅ Remarquei '{ev['titulo']}' pra {ag.fmt_hora(ev2)}.\n"
                    f"Atualizar no seu calendário: {ag.link_google(ev2)}")
            mapa = ag.link_maps(ev2.get("local"))
            if mapa:
                resp += f"\n📍 Ver o local no mapa: {mapa}"
            return resp
        return "Não consegui remarcar esse compromisso."

    def cancelar_evento(e: dict) -> str:
        ev, erro = _resolver(e)
        if erro:
            return erro
        if ag.cancelar_evento(pool, conta_id, ev["id"]):
            return f"🗑️ Cancelei: {ev['titulo']} ({ag.fmt_hora(ev)})."
        return "Não consegui cancelar esse compromisso."

    def _resolver_passado(e: dict) -> tuple[dict | None, str | None]:
        """Igual _resolver, mas pra compromissos que JÁ PASSARAM (marcar desfecho
        só faz sentido pro passado — achar_por_titulo normal só olha o futuro)."""
        eid = e.get("evento_id")
        if eid:
            try:
                alvo = int(eid)
            except (TypeError, ValueError):
                alvo = None
            if alvo:
                ev = ag.evento_por_id(pool, conta_id, alvo)
                if ev:
                    return ev, None
        termo = e.get("titulo") or ""
        cand = ag.achar_por_titulo_passado(pool, conta_id, termo, ag.agora_brt())
        if len(cand) == 1:
            return cand[0], None
        if len(cand) > 1:
            return None, ("Tem mais de um compromisso parecido nos últimos 30 dias: "
                          + "; ".join(f"{ag.fmt_hora(c)} {c['titulo']}" for c in cand[:5])
                          + ". Qual deles?")
        return None, "Não achei esse compromisso nos últimos 30 dias. Confere o nome?"

    def marcar_desfecho_evento(e: dict) -> str:
        aconteceu = (e.get("aconteceu") or "").strip().lower()
        if aconteceu in ("sim", "true", "1", "realizado", "aconteceu"):
            desfecho = "realizado"
        elif aconteceu in ("nao", "não", "false", "0", "nao_realizado", "nao aconteceu", "não aconteceu"):
            desfecho = "nao_realizado"
        else:
            return "Aconteceu ou não aconteceu? Me diz um dos dois."
        ev, erro = _resolver_passado(e)
        if erro:
            return erro
        if ag.marcar_desfecho(pool, conta_id, ev["id"], desfecho, ag.agora_brt()):
            if desfecho == "realizado":
                return f"✅ Anotado: '{ev['titulo']}' aconteceu."
            return f"❌ Anotado: '{ev['titulo']}' não aconteceu. Se quiser remarcar, é só falar."
        return "Não consegui marcar esse compromisso (só dá pra marcar um que já passou)."

    _quando = {"type": "string", "description": "data/hora, ex: 'amanhã 15h', '28/07 14:30', 'sexta 10h'"}
    _convidados = {
        "type": "array",
        "description": ("opcional; pessoas a convidar pra reunião. O Zaq cria o convite "
                        "e DISPARA no WhatsApp de cada uma (ela confirma num botão). "
                        "Só inclua se a pessoa pediu pra convidar alguém."),
        "items": {
            "type": "object",
            "properties": {
                "nome": {"type": "string", "description": "nome do convidado"},
                "contato": {"type": "string", "description": "WhatsApp com DDD, ex: '86 98888-7777'"},
            },
        },
    }

    return [
        Ferramenta(
            nome="marcar_evento",
            descricao=("Marca um compromisso na agenda da pessoa (reunião, consulta, "
                       "pagamento, lembrete). Converta a data/hora que a pessoa falar "
                       "('amanhã 15h') pro formato 'dd/mm/aaaa HH:MM' (horário de Brasília)."),
            parametros={
                "type": "object",
                "properties": {
                    "titulo": {"type": "string", "description": "o que é o compromisso"},
                    "inicio": _quando,
                    "fim": {"type": "string", "description": "opcional; fim do compromisso"},
                    "local": {"type": "string", "description": "opcional; onde"},
                    "descricao": {"type": "string", "description": "opcional; detalhes"},
                    "lembrete_min": {"type": "integer", "description": "opcional; min antes pra avisar (ex: 30)"},
                    "convidados": _convidados,
                },
                "required": ["titulo", "inicio"],
            },
            executar=marcar_evento,
        ),
        Ferramenta(
            nome="convidar_reuniao",
            descricao=("Convida pessoa(s) pra uma reunião JÁ marcada e dispara o convite "
                       "no WhatsApp de cada uma. Identifique a reunião por titulo (ou "
                       "evento_id). Use pra 'convida o Paulo pra reunião de amanhã'."),
            parametros={
                "type": "object",
                "properties": {
                    "titulo": {"type": "string", "description": "título da reunião já marcada"},
                    "evento_id": {"type": "integer", "description": "opcional; id do evento"},
                    "convidados": _convidados,
                },
                "required": ["convidados"],
            },
            executar=convidar_reuniao,
        ),
        Ferramenta(
            nome="ver_agenda",
            descricao="Mostra os compromissos da agenda. Use pra 'o que tenho hoje?', 'minha agenda', 'o que vem essa semana?'.",
            parametros={
                "type": "object",
                "properties": {
                    "periodo": {"type": "string", "description": "hoje, amanha, semana; vazio = próximos"},
                },
            },
            executar=ver_agenda,
        ),
        Ferramenta(
            nome="remarcar_evento",
            descricao="Muda a data/hora de um compromisso já marcado. Identifique por titulo (ou evento_id, se souber).",
            parametros={
                "type": "object",
                "properties": {
                    "titulo": {"type": "string", "description": "título do compromisso a remarcar"},
                    "evento_id": {"type": "integer", "description": "opcional; id do evento"},
                    "novo_inicio": _quando,
                    "novo_fim": {"type": "string", "description": "opcional; novo fim"},
                },
                "required": ["novo_inicio"],
            },
            executar=remarcar_evento,
        ),
        Ferramenta(
            nome="cancelar_evento",
            descricao="Cancela/apaga um compromisso da agenda. Identifique por titulo (ou evento_id).",
            parametros={
                "type": "object",
                "properties": {
                    "titulo": {"type": "string", "description": "título do compromisso a cancelar"},
                    "evento_id": {"type": "integer", "description": "opcional; id do evento"},
                },
            },
            executar=cancelar_evento,
        ),
        Ferramenta(
            nome="marcar_desfecho_evento",
            descricao=("Marca se um compromisso JÁ PASSADO aconteceu ou não. Use quando a "
                      "pessoa disser algo como 'a reunião com o Paulo aconteceu' ou 'não "
                      "rolou o compromisso de ontem'. Identifique por titulo (ou evento_id)."),
            parametros={
                "type": "object",
                "properties": {
                    "titulo": {"type": "string", "description": "título do compromisso já passado"},
                    "evento_id": {"type": "integer", "description": "opcional; id do evento"},
                    "aconteceu": {"type": "string", "description": "'sim' se aconteceu, 'nao' se não aconteceu"},
                },
                "required": ["aconteceu"],
            },
            executar=marcar_desfecho_evento,
        ),
    ]
