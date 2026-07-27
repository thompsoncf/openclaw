"""Ferramentas de AGENDA do agente (escopadas por conta). Agenda própria do Zaq.

marcar_evento / ver_agenda / remarcar_evento / cancelar_evento. Cada resposta de
"marcar" já traz o link "adicionar ao Google" (1 toque, sem login). O lembrete
proativo (resumo do dia) e o feed .ics assinável vêm nas etapas 2 e 3.
"""
from __future__ import annotations

from datetime import timedelta

from core.agent import Ferramenta
from . import agenda as ag


def construir_ferramentas_agenda(pool, conta_id: int,
                                 membro_id: int | None = None) -> list[Ferramenta]:

    def _linha(ev: dict) -> str:
        base = f"• {ag.fmt_hora(ev)} — {ev['titulo']}"
        if ev.get("local"):
            base += f" ({ev['local']})"
        return base

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
        return (f"📅 Marquei: {titulo} — {ag.fmt_hora(ev)}.{aviso_passado}\n"
                f"Adicionar ao seu calendário (Google/Apple/Outlook): {ag.link_google(ev)}")

    def ver_agenda(e: dict) -> str:
        periodo = (e.get("periodo") or "").strip().lower()
        agora = ag.agora_brt()
        inicio_dia = agora.replace(hour=0, minute=0, second=0, microsecond=0)
        if periodo in ("hoje", "hj"):
            eventos = ag.listar_eventos(pool, conta_id, inicio_dia, inicio_dia + timedelta(days=1))
            titulo = "hoje"
        elif periodo in ("amanha", "amanhã"):
            d = inicio_dia + timedelta(days=1)
            eventos = ag.listar_eventos(pool, conta_id, d, d + timedelta(days=1))
            titulo = "amanhã"
        elif periodo in ("semana", "essa semana"):
            eventos = ag.listar_eventos(pool, conta_id, agora, inicio_dia + timedelta(days=7))
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
        cand = ag.achar_por_titulo(pool, conta_id, e.get("titulo") or "")
        if len(cand) == 1:
            return cand[0], None
        if len(cand) > 1:
            return None, ("Tem mais de um compromisso parecido: "
                          + "; ".join(f"{ag.fmt_hora(c)} {c['titulo']}" for c in cand[:5])
                          + ". Qual deles?")
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
            return (f"✅ Remarquei '{ev['titulo']}' pra {ag.fmt_hora(ev2)}.\n"
                    f"Atualizar no seu calendário: {ag.link_google(ev2)}")
        return "Não consegui remarcar esse compromisso."

    def cancelar_evento(e: dict) -> str:
        ev, erro = _resolver(e)
        if erro:
            return erro
        if ag.cancelar_evento(pool, conta_id, ev["id"]):
            return f"🗑️ Cancelei: {ev['titulo']} ({ag.fmt_hora(ev)})."
        return "Não consegui cancelar esse compromisso."

    _quando = {"type": "string", "description": "data/hora, ex: 'amanhã 15h', '28/07 14:30', 'sexta 10h'"}

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
                },
                "required": ["titulo", "inicio"],
            },
            executar=marcar_evento,
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
    ]
