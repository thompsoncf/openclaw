"""A IA combinando a visita ao espaço — o que ela pode fazer, e quando.

POR QUE ESTE MÓDULO EXISTE. Medido na Prime em 14/09/2026, antes de desenhar:
46 leads pediram pra visitar com todas as letras e 10 têm visita na agenda. 36
pessoas pediram e ninguém marcou — não por desleixo, mas porque marcar mora no
Cockpit, em outra tela, enquanto a conversa corre no WhatsApp. E o funil mostra
o mesmo de outro ângulo: 298 dos 341 leads abertos parados em "Contatado",
2 em "Qualificado", que é a etapa que a visita abre.

O MOTOR JÁ EXISTIA: `cockpit.agendar_visita` cria o compromisso, liga no lead e
no cadastro, move pra 'qualificado' e manda o WhatsApp com o .ics. Aqui não se
reimplementa nada disso — dá-se à IA uma porta pra ele, com as travas que uma
IA marcando compromisso com cliente de verdade exige.

OS TRÊS MODOS (`agente_config.agendar_modo`, migração 259), escolha da conta:
    off     nem oferece — responde e passa pro time
    propoe  combina com o cliente e deixa PRONTA; o vendedor confirma num toque
    marca   marca direto e manda a confirmação

O HORÁRIO É DO DONO, e contraria o que eu recomendei — fica registrado porque
quem lê o código merece saber que foi decisão, não descuido. Eu propus que
marcar visita ignorasse a janela, já que as 27 visitas da Prime acontecem em
sábado, domingo e às 17h e 18h. O dono decidiu em 14/09/2026:

> "tem que ser horário comercial e fora esse horário falar com vendedor dono do
>  lead ou gestor"

Então fora da janela a IA NÃO marca e NÃO propõe: ela avisa o vendedor dono do
lead (ou a gestão, quando o lead não tem dono) e responde ao cliente sem
prometer horário. O pedido não se perde — muda de mãos.

E a janela vale SEMPRE, mesmo na conta que pôs o agente em '24h': a decisão foi
sobre marcar visita, não sobre responder. Falar 24h continua valendo.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta

from finance import agenda as ag

_log = logging.getLogger("agente.visita")

#: os três estados da chave, na ordem de quanto a IA pode agir
MODOS = ("off", "propoe", "marca")

#: a janela em que a IA pode combinar visita: seg–sáb, 8h–18h (Brasília).
#: É a mesma régua do `agente._horario_ok` — duas noções de "horário comercial"
#: no mesmo produto seria pior que o erro de uma.
DIAS_UTEIS = range(0, 6)      # segunda(0) a sábado(5)
ABRE_H, FECHA_H = 8, 18

#: quanto dura a visita quando ninguém diz outra coisa (o mesmo do Cockpit)
DUR_PADRAO_MIN = 60

#: proposta que ninguém decidiu vira poeira: some da fila do vendedor depois
#: disto, e a IA pode combinar de novo. Dois dias porque a visita costuma ser
#: pra semana que vem — expirar em horas apagaria combinação boa.
VALIDADE_H = 48


def modo(cfg: dict | None) -> str:
    """O modo da conta, sempre um dos três. Config ausente ou torta = 'off',
    que é o lado seguro de errar: a IA fica quieta como sempre esteve."""
    m = ((cfg or {}).get("agendar_modo") or "off").strip()
    return m if m in MODOS else "off"


def na_janela(quando: datetime) -> bool:
    """Este instante está na janela comercial (Brasília)?

    Recebe o instante de propósito — assim o teste não depende do relógio, que é
    como as fixtures deste repositório já viraram bomba-relógio antes."""
    loc = quando.astimezone(ag.BRT)
    return loc.weekday() in DIAS_UTEIS and ABRE_H <= loc.hour < FECHA_H


def pode_agora(cfg: dict | None, agora: datetime) -> bool:
    """A IA pode mexer com visita neste instante?"""
    return modo(cfg) != "off" and na_janela(agora)


def horario_util(quando: datetime) -> bool:
    """O HORÁRIO DA VISITA cabe na janela? Diferente de `na_janela`, que olha o
    instante da conversa. A visita marcada pra domingo de manhã seria um
    compromisso que a empresa disse não receber."""
    return na_janela(quando)


def foi_oferecido(quando: datetime | None, livres: list[datetime]) -> bool:
    """Este horário é um dos que a IA ofereceu nesta mesma volta?

    É a trava que separa "a IA agendou" de "a IA inventou". A lista saiu da agenda
    segundos antes, já sem conflito; aceitar um horário fora dela seria deixar a
    IA marcar em cima de festa — no nicho eventos, vender a mesma data duas vezes.
    E modelo de linguagem erra data com facilidade: pede-se 16/09 e volta 16/10.

    Tolerância de um minuto porque o que volta é texto reconstruído ("2026-09-16"
    + "17:00"), não o mesmo objeto — comparar por igualdade exata quebraria por
    causa de segundos que ninguém escreveu."""
    if not quando or not livres:
        return False
    return any(abs((quando - x).total_seconds()) < 60 for x in livres)


def livre(pool, conta_id: int, inicio: datetime, dur_min: int = DUR_PADRAO_MIN) -> bool:
    """Não há nada na agenda batendo com esta janela.

    Usa `agenda.conflitos`, o mesmo que a tela de remarcar usa — inclusive
    pré-reservas, que no nicho eventos são data segurada e valem como ocupado.
    Falha de banco responde False: oferecer horário sem saber se está livre é
    como se vende a mesma data duas vezes."""
    try:
        fim = inicio + timedelta(minutes=max(15, int(dur_min or DUR_PADRAO_MIN)))
        return not ag.conflitos(pool, conta_id, inicio, fim)
    except Exception:  # noqa: BLE001
        _log.warning("conflito não pôde ser conferido (conta %s)", conta_id, exc_info=True)
        return False


def sugestoes(pool, conta_id: int, agora: datetime, quantas: int = 2,
              dur_min: int = DUR_PADRAO_MIN) -> list[datetime]:
    """Os próximos horários LIVRES que a IA pode oferecer.

    Varre de hora em hora, a partir da próxima hora cheia, só dentro da janela, e
    pula o que está ocupado. Teto de 14 dias: oferecer visita pra daqui a três
    semanas não é oferta, é adiamento."""
    out: list[datetime] = []
    t = agora.astimezone(ag.BRT).replace(minute=0, second=0, microsecond=0) + timedelta(hours=1)
    limite = t + timedelta(days=14)
    while t < limite and len(out) < max(1, quantas):
        if horario_util(t) and livre(pool, conta_id, t, dur_min):
            out.append(t)
            t += timedelta(hours=2)     # não oferece dois horários colados
        else:
            t += timedelta(hours=1)
    return out


def _vendedor_do_lead(c, conta_id: int, lead_id: int) -> int | None:
    r = c.execute("select vendedor_id from prospeccao where id=%s and conta_id=%s",
                  (lead_id, conta_id)).fetchone()
    return r[0] if r and r[0] else None


def _gestao(c, conta_id: int) -> list[int]:
    """Dono e gestores ativos — pra quem o lead sem vendedor escala."""
    try:
        return [r[0] for r in c.execute(
            "select id from membros where conta_id=%s and coalesce(ativo,true) "
            "  and papel in ('dono','gestor')", (conta_id,)).fetchall()]
    except Exception:  # noqa: BLE001
        return []


def quem_avisar(c, conta_id: int, lead_id: int) -> list[int]:
    """A quem esta visita interessa: o vendedor dono do lead; sem ele, a gestão.

    É o que o dono pediu ("falar com vendedor dono do lead ou gestor"). Na Prime,
    0 dos 341 leads abertos está sem vendedor — a segunda porta é pro caso raro,
    e existe pra que o pedido do cliente nunca caia no vazio."""
    v = _vendedor_do_lead(c, conta_id, lead_id)
    return [v] if v else _gestao(c, conta_id)


def avisar(pool, conta_id: int, membros: list[int], titulo: str, corpo: str,
           url: str = "/cockpit") -> None:
    """Push no app + e-mail, best-effort inteiro — igual ao aviso do follow-up.

    Um aviso perdido custa menos que uma conversa derrubada: isto roda dentro do
    atendimento, e o atendimento nunca pode cair por causa de uma notificação."""
    for membro_id in {m for m in membros if m}:
        try:
            from finance import cockpit as _ck
            _ck.enviar_push(pool, conta_id, membro_id, titulo, corpo, url)
        except Exception:  # noqa: BLE001
            pass
        try:
            with pool.connection() as c:
                m = c.execute("select coalesce(nullif(nome,''), email), email from membros "
                              " where id=%s and conta_id=%s", (membro_id, conta_id)).fetchone()
            if m and m[1] and "@" in m[1]:
                from finance import email_sender as es
                es.enviar_aviso(m[1], titulo, corpo + " Abra o Zaq pra responder.", nome=m[0])
        except Exception:  # noqa: BLE001
            pass


#: o cliente pedindo pra conhecer o espaço, em texto. É a MESMA régua com que o
#: tamanho do problema foi medido (46 leads em 510 conversas na Prime) — o que a
#: medição contou é o que o código reconhece.
PEDIU_VISITA = re.compile(
    r"(visita|visitar|conhecer o espa|conhecer o local|conhecer a casa|"
    r"dar uma olhada no espa|ver o espa|ver o local)", re.I)


def pediu_visita(texto: str | None) -> bool:
    return bool(PEDIU_VISITA.search(texto or ""))


def _ja_avisou(c, lead_id: int, horas: int = 12) -> bool:
    """Já chamamos alguém por este lead há pouco?

    Sem isto, um cliente que escreve três mensagens seguidas às 22h vira três
    pushes pro mesmo vendedor, sobre a mesma coisa — e notificação repetida é a
    que se aprende a ignorar."""
    try:
        r = c.execute(
            """select 1 from prospeccao_atividades
                where prospeccao_id=%s and tipo='visita' and resultado='fora_de_hora'
                  and criado_em > now() - make_interval(hours => %s) limit 1""",
            (lead_id, horas)).fetchone()
        return bool(r)
    except Exception:  # noqa: BLE001
        return False        # na dúvida avisa: aviso a mais custa menos que pedido perdido


def fora_de_hora(pool, conta_id: int, lead_id: int, quem: str) -> bool:
    """Fora da janela: a IA não marca — chama gente. Devolve se avisou.

    É a decisão do dono em 14/09/2026, e é o que impede a regra do horário de
    virar silêncio: o cliente pediu às 19h de sábado e alguém fica sabendo. Sem
    esta metade, "só em horário comercial" seria o pedido morrendo no domingo."""
    try:
        with pool.connection() as c:
            if _ja_avisou(c, lead_id):
                return False
            membros = quem_avisar(c, conta_id, lead_id)
            c.execute(
                """insert into prospeccao_atividades (prospeccao_id, tipo, resultado, descricao)
                        values (%s,'visita','fora_de_hora',%s)""",
                (lead_id, f"{quem} pediu visita fora do horário — a IA avisou a equipe"[:400]))
            c.commit()
    except Exception:  # noqa: BLE001
        _log.warning("aviso de visita fora de hora falhou (lead %s)", lead_id, exc_info=True)
        return False
    avisar(pool, conta_id, membros,
           f"📅 {quem} quer marcar uma visita",
           "Pediu fora do horário comercial — a IA não marcou.",
           f"/cockpit/lead/{lead_id}")
    return True


def propor(pool, conta_id: int, lead_id: int, inicio: datetime, *,
           conversa_id: int | None = None, dur_min: int = DUR_PADRAO_MIN,
           quem: str = "O cliente") -> dict:
    """Guarda a visita combinada e entrega o cartão ao vendedor. NÃO toca na agenda.

    Uma proposta viva por lead (índice único da 259): a IA que voltar a combinar
    horário atualiza a que existe, em vez de encher a fila com três cartões do
    mesmo cliente."""
    dur = max(15, int(dur_min or DUR_PADRAO_MIN))
    with pool.connection() as c:
        membros = quem_avisar(c, conta_id, lead_id)
        c.execute(
            """insert into agente_visitas (conta_id, prospeccao_id, conversa_id, inicio,
                                           dur_min, membro_id)
                    values (%s,%s,%s,%s,%s,%s)
               on conflict (conta_id, prospeccao_id) where estado='proposta'
               do update set inicio=excluded.inicio, dur_min=excluded.dur_min,
                             conversa_id=excluded.conversa_id, criado_em=now()""",
            (conta_id, lead_id, conversa_id, inicio, dur, membros[0] if membros else None))
        c.commit()
    quando = inicio.astimezone(ag.BRT).strftime("%d/%m às %H:%M")
    avisar(pool, conta_id, membros,
           f"📅 Visita pra confirmar — {quem}",
           f"{quando}. A IA combinou; falta o seu OK.",
           f"/cockpit/lead/{lead_id}")
    return {"ok": True, "quando": quando}


def marcar(pool, conta_id: int, lead_id: int, inicio: datetime, *,
           dur_min: int = DUR_PADRAO_MIN, quem: str = "O cliente") -> dict:
    """Marca de verdade, pelo MESMO caminho do botão do Cockpit.

    `agendar_visita` revalida a posse (`_posse`: o lead tem que ser do membro), e
    é por isso que se passa o vendedor DONO do lead — a IA não tem membro próprio
    e não deve ganhar um: a visita é do vendedor, aparece na agenda dele e é ele
    quem recebe o cliente."""
    from finance import cockpit as ck
    with pool.connection() as c:
        membros = quem_avisar(c, conta_id, lead_id)
        dono = _vendedor_do_lead(c, conta_id, lead_id)
    if not dono:
        # sem dono não há posse a revalidar, e inventar um seria pôr a visita na
        # agenda de quem não vai receber o cliente. Vira proposta pra gestão.
        return propor(pool, conta_id, lead_id, inicio, dur_min=dur_min, quem=quem)
    loc = inicio.astimezone(ag.BRT)
    r = ck.agendar_visita(pool, conta_id, dono, lead_id,
                          data=loc.strftime("%Y-%m-%d"), hora=loc.strftime("%H:%M"),
                          dur_min=dur_min, avisar_cliente=True)
    if not r.get("ok"):
        return r
    if r.get("evento_id"):
        try:
            with pool.connection() as c:
                c.execute("update eventos_agenda set marcado_por='ia' where id=%s and conta_id=%s",
                          (r["evento_id"], conta_id))
                c.commit()
        except Exception:  # noqa: BLE001
            pass
    quando = loc.strftime("%d/%m às %H:%M")
    avisar(pool, conta_id, membros, f"✅ Visita marcada pela IA — {quem}",
           f"{quando}. O cliente já recebeu a confirmação.", f"/cockpit/lead/{lead_id}")
    return dict(r, quando=quando)


# ------------------------------------------------------------ o lado do vendedor

def pendentes(pool, conta_id: int, membro_id: int | None = None) -> list[dict]:
    """As propostas vivas — a fila de cartões do vendedor. Expiradas ficam de fora
    sem precisar de faxina: a validade é lida na consulta."""
    corte = ag.agora_brt() - timedelta(hours=VALIDADE_H)
    sql = ("""select v.id, v.prospeccao_id, v.inicio, v.dur_min, v.membro_id,
                     coalesce(nullif(p.contato,''), nullif(p.empresa,''), 'Cliente'),
                     p.evento_tipo, p.evento_convidados
                from agente_visitas v join prospeccao p on p.id = v.prospeccao_id
               where v.conta_id=%s and v.estado='proposta' and v.criado_em > %s """
           + ("and v.membro_id=%s " if membro_id else "")
           + " order by v.inicio")
    args = (conta_id, corte) + ((membro_id,) if membro_id else ())
    try:
        with pool.connection() as c:
            rows = c.execute(sql, args).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [{"id": r[0], "lead_id": r[1], "inicio": r[2], "dur_min": r[3], "membro_id": r[4],
             "quem": r[5], "tipo": r[6], "convidados": r[7]} for r in rows]


def confirmar(pool, conta_id: int, membro_id: int, proposta_id: int) -> dict:
    """O vendedor deu o OK: vira compromisso pelo caminho de sempre."""
    from finance import cockpit as ck
    with pool.connection() as c:
        r = c.execute("""select prospeccao_id, inicio, dur_min from agente_visitas
                          where id=%s and conta_id=%s and estado='proposta'""",
                      (proposta_id, conta_id)).fetchone()
    if not r:
        return {"ok": False, "erro": "Esta proposta não está mais valendo."}
    lead_id, inicio, dur = r
    loc = inicio.astimezone(ag.BRT)
    out = ck.agendar_visita(pool, conta_id, membro_id, lead_id,
                            data=loc.strftime("%Y-%m-%d"), hora=loc.strftime("%H:%M"),
                            dur_min=dur, avisar_cliente=True)
    if not out.get("ok"):
        return out
    with pool.connection() as c:
        # 'vendedor' porque QUEM MARCOU foi ele: a IA propôs, o dedo que
        # confirmou é humano. É o que faz o placar significar alguma coisa.
        if out.get("evento_id"):
            c.execute("update eventos_agenda set marcado_por='vendedor' where id=%s and conta_id=%s",
                      (out["evento_id"], conta_id))
        c.execute("""update agente_visitas set estado='confirmada', evento_id=%s,
                            decidido_em=now(), decidido_por=%s
                      where id=%s and conta_id=%s""",
                  (out.get("evento_id"), membro_id, proposta_id, conta_id))
        c.commit()
    return out


def descartar(pool, conta_id: int, membro_id: int, proposta_id: int) -> dict:
    with pool.connection() as c:
        n = c.execute("""update agente_visitas set estado='descartada', decidido_em=now(),
                                decidido_por=%s
                          where id=%s and conta_id=%s and estado='proposta'""",
                      (membro_id, proposta_id, conta_id)).rowcount
        c.commit()
    return {"ok": bool(n)}
