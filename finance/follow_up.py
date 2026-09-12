"""O FOLLOW-UP AUTOMÁTICO: a próxima ação de cada lead, e a cobrança quando vence.

Aprovado em 07/09/2026 (docs/mockups/follow_up_automatico.html), depois de duas
rodadas com o dono.

POR QUE A PRÓXIMA AÇÃO NASCE SOZINHA
O pedido original era um painel que cobrasse do vendedor o preenchimento de
"próxima ação". Medido na conta 34 antes de desenhar: `proximo_contato_em`
preenchido em 1 lead de 274 ativos, `ultimo_contato_em` em 30 de 297. Cobrar o
preenchimento abriria o dia 1 com 273 leads em "sem próxima ação" — e a meta do
dono ("sem próxima ação = zero") viraria 273 formulários pra três pessoas.

Então o sistema PROPÕE (a escada, abaixo) e o vendedor CORRIGE. Medido de novo
com a escada rodando contra a produção: "sem próxima ação" cai pra 4 — só os
leads cuja festa já passou.

O RELÓGIO LÊ A CONVERSA, NÃO O CARD
Mesma razão da régua do funil (finance/funil_regua): "registrar atividade" teve 5
usos em toda a história da conta 34, e 74 dos 81 leads "parados" já tinham
resposta nossa. Interação aqui é linha em `mensagens` — inclusive o eco fromMe,
que grava o que o vendedor responde pelo próprio celular. Abrir o card, arrastar
a coluna ou marcar como lido NÃO encerram alerta nenhum: o fato que gerou o aviso
continua de pé.

O SEGUNDO RELÓGIO É DO NICHO (CLAUDE.md §6)
Em eventos a data da festa manda: festa em até `fu_festa_dias` sem proposta já está
vencida, por mais recente que tenha sido a última conversa. O prazo dela é o
instante em que a festa ENTROU nessa janela (`_janela_da_festa`), nunca "agora" —
"agora" muda a cada passada do poller, e prazo que muda é fato novo, que fura o
dedup e cobra de novo sem parar. Quem não vende festa (perfil recorrente) não tem
esse relógio — e nunca vê a palavra. O perfil vem de finance/raio_x_perfil, os
mesmos três de sempre.

OS QUATRO DEGRAUS DA COBRANÇA
    venc  no vencimento          → o vendedor
    a24   24h depois             → o vendedor de novo
    a48   48h depois             → o vendedor + o gestor
    a72   72h depois             → destaque no painel da gestão

O dedup é pelo FATO (`ref_em` = o prazo que venceu), no mesmo índice único que a
cobrança da régua já usa. Consequência que é o pedido do dono: qualquer coisa que
mude o prazo — uma mensagem enviada, uma proposta, um reagendamento — muda o
fato e ZERA a escada de avisos. Abrir o card não muda nada, logo não zera nada.

NASCE DESLIGADO. `follow_up_modo` é 'off' em toda conta. Em 'observando' o motor
calcula tudo e grava com `simulado=true`, sem mandar um push sequer.
"""
from __future__ import annotations

import logging
from datetime import datetime, time, timedelta, timezone

from finance import funil_regua as fr

_log = logging.getLogger("openclaw.follow_up")

#: Brasília é -3 fixo no produto inteiro (o país não tem horário de verão desde
#: 2019). Mesma convenção de finance/funil_regua e do painel.
_UTC_BR = timedelta(hours=-3)

#: Perfis que já ganharam a tela. O motor é do perfil (o vocabulário e o relógio
#: da festa saem dele), mas a ENTREGA foi combinada com o dono em duas etapas:
#: eventos primeiro, na Prime, e o recorrente quando a régua provar que funciona
#: com gente usando. Acrescentar "recorrente" aqui é o que liga a segunda etapa.
PERFIS_COM_TELA = ("eventos",)

#: os seis estados do mockup, na ordem da urgência (o primeiro é o mais urgente)
ESTADOS = ("critico", "atrasado", "hoje", "agendado", "andamento", "sem_acao")
ROTULO = {
    "critico": "Crítico", "atrasado": "Atrasado", "hoje": "Follow-up hoje",
    "agendado": "Agendado", "andamento": "Em andamento", "sem_acao": "Sem próxima ação",
}
EMOJI = {"critico": "🚨", "atrasado": "🔴", "hoje": "🟡", "agendado": "🔵",
         "andamento": "🟢", "sem_acao": "⚠️"}

#: (chave, horas de atraso, quem recebe). A ordem é do mais brando pro mais grave.
DEGRAUS = (("venc", 0, "vendedor"), ("a24", 24, "vendedor"),
           ("a48", 48, "gestor"), ("a72", 72, "gestor"))
_DEGRAU_TXT = {
    "venc": "tem follow-up pendente. Fazer contato agora.",
    "a24": "está com follow-up atrasado há 24h.",
    "a48": "está há 48h sem acompanhamento.",
    "a72": "está há 72h sem acompanhamento.",
}

#: quantos adiamentos SEGUIDOS sem falar com o cliente antes de exigir motivo
ADIAMENTOS_ATE_MOTIVO = 3

_PADRAO = {"follow_up_modo": "off", "fu_proposta_dias": 3, "fu_toques": (2, 4, 7, 15),
           "fu_festa_dias": 30, "fu_teto_dia": 15, "fila_modo": "prazo"}


# ------------------------------------------------------------------ config

def _escada(txt: str | None) -> tuple[int, ...]:
    """"2,4,7,15" → (2, 4, 7, 15). Lista vazia ou torta volta pro padrão: uma
    config ilegível não pode virar prazo zero, que cobraria tudo de todo mundo."""
    out = []
    for p in (txt or "").split(","):
        p = p.strip()
        if p.lstrip("-").isdigit() and int(p) > 0:
            out.append(int(p))
    return tuple(out) or _PADRAO["fu_toques"]


def config(c, conta_id: int) -> dict:
    """A config do follow-up da conta, semeando a linha da régua na 1ª vez.

    Devolve junto os prazos de conversa da régua (`sem_resposta_min`,
    `bola_nossa_min`) e a janela de atendimento — são os mesmos números, e ter
    duas configurações de "quanto tempo o cliente pode esperar" no mesmo produto
    seria pior que o erro de uma.
    """
    base = fr.config(c, conta_id)
    r = c.execute(
        """select follow_up_modo, fu_proposta_dias, fu_toques_dias, fu_festa_dias, fu_teto_dia,
                  coalesce(fila_modo, 'prazo')
             from funil_regua where conta_id=%s""", (conta_id,)).fetchone()
    if not r:
        return dict(base, **_PADRAO)
    # COLUNA VAZIA = HERDA (migração 228). `base` já traz o padrão do nicho resolvido
    # pra estas quatro; aqui só entra o que a CONTA escolheu de verdade.
    escolhidas = set(base.get("_escolhidas") or ())
    vals = {"fu_proposta_dias": r[1], "fu_toques_dias": r[2],
            "fu_festa_dias": r[3], "fu_teto_dia": r[4]}
    escolhidas |= {k for k, v in vals.items() if v is not None}
    out = dict(base, follow_up_modo=r[0],
               **{k: v for k, v in vals.items() if v is not None})
    out["_escolhidas"] = escolhidas
    # `fu_festa_dias` é None no perfil recorrente — quem não vende festa não tem o
    # segundo relógio, e o None é a declaração disso (não um valor faltando).
    out.setdefault("fu_proposta_dias", _PADRAO["fu_proposta_dias"])
    out.setdefault("fu_teto_dia", _PADRAO["fu_teto_dia"])
    out["fu_festa_dias"] = out.get("fu_festa_dias")
    out["fu_toques"] = _escada(out.get("fu_toques_dias"))
    # a ordem da fila (migração 245). Não entra em `_escolhidas`: modo não se herda
    # do ramo — 'prazo' é o que a conta já faz, não "ainda não escolheu".
    out["fila_modo"] = r[5] if r[5] in ("prazo", "temperatura") else "prazo"
    return out


# ------------------------------------------------------------------ a escada

def toques_da_etapa(*, desde, toques: tuple, feitos: int, agora: datetime) -> list[dict]:
    """As tentativas como TAREFAS, ancoradas na entrada na etapa (migração 233).

    O pedido do dono era "as três tentativas devem nascer automaticamente como
    tarefas em D1, D3 e D7, sem depender da lembrança do vendedor". Nascer é isto:
    a lista existe assim que o lead entra na etapa, com data em cada linha, e a
    tela só mostra o que já está calculado.

    `feitos` são as mensagens que saíram DEPOIS da entrada — o mesmo relógio do
    resto da régua, que lê a conversa e não "atividade registrada" (5 usos em toda
    a história da conta 34).
    """
    out = []
    for i, d in enumerate(toques):
        prazo = desde + timedelta(days=int(d))
        feito = i < feitos
        out.append({"n": i + 1, "dia": int(d), "prazo": prazo, "feito": feito,
                    "atrasado": (not feito and prazo <= agora)})
    return out


def prazo_automatico(*, status: str, ult_in, ult_out, criado_em, tentativas: int,
                     evento_em, cfg: dict, tem_data: bool, agora: datetime,
                     toques_fixos: tuple = (), desde=None,
                     feitos_na_etapa: int = 0) -> tuple[datetime, str]:
    """(prazo, ação) que o sistema propõe pra este lead. Puro — sem banco.

    A ordem é a do mockup: a bola vem antes de tudo (cliente esperando é mais
    urgente e mais acionável que card parado), a proposta tem prazo próprio, e o
    resto sobe a escada de toques. Por último, e só pra quem vende festa, a data
    aperta o que estiver frouxo.

    `toques_fixos` é a etapa que declarou D1/D3/D7 (migração 233): ali a escada
    deixa de ser relativa à última conversa e passa a ser contada da ENTRADA na
    etapa. A bola continua vindo antes — cliente esperando resposta é mais urgente
    que a próxima tentativa agendada, e essa ordem não muda por etapa nenhuma.
    """
    if ult_out is None:
        # nunca falamos nada — nem pelo painel, nem pelo celular
        base = ult_in or criado_em or agora
        prazo, acao = base + timedelta(minutes=cfg["sem_resposta_min"]), "responder — ninguém falou com ele ainda"
    elif ult_in is not None and ult_in > ult_out:
        prazo, acao = ult_in + timedelta(minutes=cfg["bola_nossa_min"]), "responder — o cliente está esperando"
    elif status == "proposta":
        prazo, acao = ult_out + timedelta(days=cfg["fu_proposta_dias"]), "cobrar retorno da proposta"
    elif toques_fixos and desde:
        # a etapa declarou as tentativas (D1/D3/D7). A próxima é a primeira ainda
        # não feita; feitas todas, o prazo é o fim do período — e o lead fica
        # esperando DECISÃO, não virando Perdido sozinho: perder exige motivo, e só
        # quem falou com o cliente sabe qual (mesma trava do teto da etapa).
        n = min(max(0, int(feitos_na_etapa or 0)), len(toques_fixos))
        if n >= len(toques_fixos):
            prazo = desde + timedelta(days=int(toques_fixos[-1]))
            acao = "as tentativas acabaram — encerrar ou reativar?"
        else:
            prazo = desde + timedelta(days=int(toques_fixos[n]))
            acao = f"{n + 1}ª tentativa de {len(toques_fixos)}"
    else:
        esc = cfg["fu_toques"]
        n = min(max(int(tentativas or 1), 1), len(esc))
        prazo = ult_out + timedelta(days=esc[n - 1])
        acao = ("segundo toque" if n == 1 else "terceiro toque" if n == 2
                else "último toque desta rodada" if n == 3
                else "insistiu demais — muda de canal ou encerra?")
        if n >= len(esc):
            acao = "insistiu demais — muda de canal ou encerra?"
    # O segundo relógio: só existe pra quem vende festa. Duas portas pra mesma
    # pergunta, e ambas contam: `tem_data` vem do vocabulário do perfil, e
    # `fu_festa_dias` é None no perfil que não tem esse relógio (raio_x_perfil).
    # Quem declara não ter data nunca deve cair aqui por um cfg mal montado.
    if tem_data and evento_em and status != "proposta" and cfg.get("fu_festa_dias"):
        faltam = (evento_em - agora.date()).days
        if 0 <= faltam <= cfg["fu_festa_dias"] and prazo > agora:
            prazo, acao = _janela_da_festa(evento_em, criado_em, cfg), "mandar proposta — a data está chegando"
    return prazo, acao


def _janela_da_festa(evento_em, criado_em, cfg: dict) -> datetime:
    """Quando a festa ENTROU na janela que aperta o prazo — um fato do lead, não do
    relógio de quem está perguntando.

    ISTO ERA `agora`, E `agora` NÃO É UM FATO. O dedup do aviso é por `ref_em`, que
    é o prazo; com o prazo valendo "agora", cada passada do poller inventava um fato
    novo e o aviso saía DE NOVO. Medido no ensaio da conta 34 em 11/09/2026: o lead
    977 (festa em 26/09) acumulou 442 avisos em quatro dias, 441 com `ref_em`
    distinto — um por ciclo, dentro da janela de atendimento. Ligado, esse lead
    sozinho comeria a cota diária do vendedor (`fu_teto_dia`) em meia hora e
    represaria todo o follow-up de verdade dele, todo dia.

    A âncora é `evento_em - fu_festa_dias`, às 9h de Brasília — determinística a
    partir do cadastro, então duas passadas seguidas devolvem o mesmo instante e o
    dedup volta a funcionar. Nunca antes de o lead existir: com a festa já dentro da
    janela no dia do cadastro, a abertura ficaria no passado e o lead nasceria
    "atrasado há 20 dias", número que nunca foi verdade.

    9h e não meia-noite porque prazo de madrugada só serve pra vencer antes de
    alguém acordar — é a mesma hora que o reagendamento de um toque já usa.
    """
    dia = evento_em - timedelta(days=int(cfg["fu_festa_dias"]))
    abertura = datetime.combine(dia, time(9, 0)).replace(tzinfo=timezone.utc) - _UTC_BR
    return max(abertura, criado_em) if criado_em else abertura


def estado_de(prazo: datetime | None, ult: datetime | None, agora: datetime,
              sem_acao: bool = False) -> str:
    """Um dos seis. `sem_acao` é o lead que o sistema não tem o que propor."""
    if sem_acao or prazo is None:
        return "sem_acao"
    atraso = (agora - prazo).total_seconds() / 3600.0
    if atraso < 0:
        return "andamento" if ult and (agora - ult).total_seconds() < 86400 else "agendado"
    if atraso < 24:
        return "hoje"
    if atraso < 72:
        return "atrasado"
    return "critico"


# ------------------------------------------------------------------ os leads

_SQL_LEADS = """
with msg as (
  select cv.prospeccao_id as lead,
         max(m.criado_em) filter (where m.direcao='in')  as ult_in,
         max(m.criado_em) filter (where m.direcao='out') as ult_out
    from conversas cv join mensagens m on m.conversa_id = cv.id
   where cv.conta_id = %(conta)s and cv.prospeccao_id is not null
   group by cv.prospeccao_id),
tent as (
  select cv.prospeccao_id as lead, count(*) as n
    from conversas cv join mensagens m on m.conversa_id = cv.id
    join msg on msg.lead = cv.prospeccao_id
   where cv.conta_id = %(conta)s and m.direcao='out'
     and (msg.ult_in is null or m.criado_em > msg.ult_in)
   group by cv.prospeccao_id),
marc as (
  select distinct on (prospeccao_id)
         prospeccao_id as lead, prazo_em, acao, membro_id, automatico, criado_em, motivo
    from follow_up_marcacoes where conta_id = %(conta)s
   -- o desempate por id não é zelo: `now()` no Postgres é a hora da TRANSAÇÃO,
   -- então duas marcações da mesma transação nascem com o mesmo instante e a
   -- "última" seria sorteada a cada consulta
   order by prospeccao_id, criado_em desc, id desc),
ultmsg as (
  -- a última mensagem de cada lead, pro balão do card. É a MESMA leitura do
  -- funil (painel_prospeccao): lateral limit 1 por conversa, uma consulta pro
  -- board inteiro. `visto_ate_id` nulo é "nunca abriu" — aí toda entrada conta
  -- como não vista, que é o estado de quem nunca usou o Inbox.
  select distinct on (cv.prospeccao_id)
         cv.prospeccao_id as lead, cv.visto_ate_id, cv.id as conversa_id,
         coalesce(cv.canal, 'whatsapp') as canal, m.id as mid,
         m.direcao as dir, m.texto, m.criado_em as em
    from conversas cv
    join lateral (select id, direcao, texto, criado_em from mensagens
                   where conversa_id = cv.id order by criado_em desc, id desc limit 1) m on true
   where cv.conta_id = %(conta)s and cv.prospeccao_id is not null
     and coalesce(cv.canal, 'whatsapp') in ('whatsapp', 'email', 'instagram')
   order by cv.prospeccao_id, m.criado_em desc, m.id desc),
adiam as (
  select fm.prospeccao_id as lead, count(*) as n
    from follow_up_marcacoes fm
    left join msg on msg.lead = fm.prospeccao_id
   where fm.conta_id = %(conta)s and not fm.automatico
     and fm.criado_em > coalesce(greatest(msg.ult_in, msg.ult_out), '-infinity'::timestamptz)
   group by fm.prospeccao_id)
select p.id, p.status, p.vendedor_id,
       coalesce(nullif(p.contato,''), nullif(p.empresa,''), 'Lead'),
       coalesce(nullif(p.temperatura,''), 'frio') as temperatura,
       p.evento_em, p.evento_tipo, p.evento_convidados, p.criado_em,
       -- desde quando está NESTA etapa, e quantas mensagens nossas saíram depois
       -- disso: é o par que a escada ancorada (migração 233) precisa, e vem do
       -- histórico que roda desde 19/08 com tudo desligado
       coalesce((select max(fm.criado_em) from funil_movimentos fm
                  where fm.prospeccao_id = p.id and fm.para = p.status), p.criado_em) as na_etapa,
       (select count(*) from conversas cv2 join mensagens m2 on m2.conversa_id = cv2.id
         where cv2.prospeccao_id = p.id and m2.direcao = 'out'
           and m2.criado_em > coalesce((select max(fm2.criado_em) from funil_movimentos fm2
                                         where fm2.prospeccao_id = p.id and fm2.para = p.status),
                                       p.criado_em)) as saiu_na_etapa,
       msg.ult_in, msg.ult_out, coalesce(tent.n, 0),
       marc.prazo_em, marc.acao, marc.criado_em, coalesce(adiam.n, 0),
       coalesce(nullif(mb.nome,''), mb.email), marc.membro_id,
       ultmsg.texto, ultmsg.em, ultmsg.dir, ultmsg.mid, ultmsg.visto_ate_id,
       ultmsg.conversa_id, ultmsg.canal
  from prospeccao p
  left join msg    on msg.lead    = p.id
  left join ultmsg on ultmsg.lead = p.id
  left join tent  on tent.lead  = p.id
  left join marc  on marc.lead  = p.id
  left join adiam on adiam.lead = p.id
  left join membros mb on mb.id = p.vendedor_id
 where p.conta_id = %(conta)s and p.estagio = 'lead' and """ + fr.sql_encerradas_nao("p")


def leads(c, conta_id: int, perfil: dict | None = None,
          agora: datetime | None = None, cfg: dict | None = None) -> list[dict]:
    """Todo lead em jogo da conta, com prazo, estado e o que fazer.

    Uma consulta só pra conta inteira: a versão "um lead por vez" seriam 274
    consultas por ciclo do poller na Prime, e o poller ainda tem campanha,
    lembrete, régua e Raio-X pra rodar na mesma passada.
    """
    agora = agora or datetime.now(timezone.utc)
    cfg = cfg or config(c, conta_id)
    if perfil is None:
        from finance import raio_x_perfil as rxp
        perfil = rxp.perfil(None)
    tem_data = bool(perfil.get("vocab", {}).get("data"))
    # as etapas que declararam as tentativas como tarefas (migração 233). Uma
    # consulta pra conta inteira, como tudo aqui: a versão por lead seriam 274 idas
    # ao banco por ciclo do poller na Prime.
    toques_por_etapa = {r[0]: tuple(int(x) for x in (r[1] or "").split(",") if x.strip().isdigit())
                        for r in c.execute(
                            """select chave, toques_dias from funil_etapas
                                where conta_id=%s and coalesce(toques_dias,'') <> ''""",
                            (conta_id,)).fetchall()}
    toques_por_etapa = {k: v for k, v in toques_por_etapa.items() if v}
    hoje = agora.date()
    out = []
    for r in c.execute(_SQL_LEADS, {"conta": conta_id}).fetchall():
        (lid, status, vend, quem, temperatura, evento_em, ev_tipo, ev_conv, criado,
         na_etapa, saiu_na_etapa,
         ult_in, ult_out, tent, m_prazo, m_acao, m_em, adiados, vend_nome, m_por,
         msg_txt, msg_em, msg_dir, msg_id, visto, conversa_id, canal) = r
        ult = max([x for x in (ult_in, ult_out) if x], default=None)
        # festa que já passou e o lead segue aberto: não há o que propor
        sem_acao = bool(tem_data and evento_em and evento_em < hoje)
        fixos = toques_por_etapa.get(status) or ()
        prazo, acao = prazo_automatico(
            status=status, ult_in=ult_in, ult_out=ult_out, criado_em=criado,
            tentativas=tent, evento_em=evento_em, cfg=cfg, tem_data=tem_data, agora=agora,
            toques_fixos=fixos, desde=na_etapa, feitos_na_etapa=saiu_na_etapa)
        na_mao = False
        # A MÃO MANDA — mas só enquanto for a última palavra. A marcação vale se
        # foi feita DEPOIS da última mensagem; se o cliente voltou a falar, o fato
        # é novo e a escada recomeça. É o que faz "ele disse que retorna terça"
        # valer sem virar um jeito de silenciar o lead pra sempre.
        if m_prazo and m_em and (ult is None or m_em > ult):
            prazo, acao, na_mao = m_prazo, (m_acao or "retorno combinado"), True
        e = estado_de(prazo, ult, agora, sem_acao)
        out.append({
            "id": lid, "status": status, "vendedor_id": vend, "vendedor": vend_nome or "sem dono",
            "quem": quem, "temperatura": temperatura,
            "evento_em": evento_em, "evento_tipo": ev_tipo, "convidados": ev_conv,
            "ult_in": ult_in, "ult_out": ult_out, "ult": ult, "tentativas": int(tent or 0),
            "prazo": None if sem_acao else prazo, "acao": acao, "na_mao": na_mao,
            "adiados": int(adiados or 0), "adiado_por": m_por,
            "estado": e, "atraso_h": (0 if (sem_acao or not prazo)
                                      else max(0, int((agora - prazo).total_seconds() // 3600))),
            "parado_h": (int((agora - ult).total_seconds() // 3600) if ult else None),
            "bola": ("aguardando vendedor" if (ult_out is None or (ult_in and ult_in > ult_out))
                     else "aguardando cliente"),
            "faltam": ((evento_em - hoje).days if evento_em else None),
            "na_etapa": na_etapa,
            # as tentativas como tarefas — lista vazia na etapa que não declarou
            "toques": (toques_da_etapa(desde=na_etapa, toques=fixos,
                                       feitos=saiu_na_etapa, agora=agora) if fixos else []),
            # o balão do card: onde a conversa parou, sem precisar abrir o lead
            "msg": ({"texto": msg_txt or "", "em": msg_em, "minha": msg_dir == "out",
                     "nova": (msg_dir == "in" and (visto is None or (msg_id or 0) > visto)),
                     # o que o balão precisa pra abrir: a conversa e em qual aba
                     # ela mora (o mesmo par que o funil e o Raio-X passam pro
                     # kbAbrirChat de web/balao_conversa.py)
                     "conversa_id": conversa_id,
                     "aba": ("emails" if canal == "email" else "conversas")}
                    if msg_em else None),
        })
        out[-1]["prioridade"] = prioridade(out[-1])
    return out


# ------------------------------------------------------------ a fila de prioridade
# "QUEM EU PRECISO ATENDER AGORA?" — § 7 do Projeto Adaptado da Prime (12/09/2026).
#
# A TEMPERATURA JÁ EXISTIA E NÃO VALIA NADA. `prospeccao.temperatura` está no banco
# desde sempre e aparece como pílula colorida no card do funil, mas até hoje este
# módulo e `web/painel_follow_up.py` tinham ZERO ocorrências da palavra: ela não
# entrava na ordem da fila do vendedor. Era enfeite.
#
# O QUE O DOCUMENTO PEDE, NA ORDEM DELE
#   1º  quentes aguardando ação do vendedor
#   2º  tarefas atrasadas
#   3º  quentes com a próxima ação vencendo
#   4º  responderam e aguardam retorno da equipe
#   5º  mornos com possibilidade de avanço
#   6º  o resto
#
# E A REGRA QUE O DOCUMENTO REPETE TRÊS VEZES: "a temperatura não altera a etapa do
# funil; ela altera a prioridade de atendimento". Por isso isto é uma função de
# ORDENAÇÃO e nada mais — nenhum lead muda de coluna por ser quente.
#
# PURA, SEM BANCO: a fila é a tela que o vendedor abre todo dia, e uma regra de
# prioridade que só dá pra conferir com o banco montado é uma regra que ninguém
# confere. Recebe o dicionário que `leads()` já monta.
PRIORIDADES = (
    (1, "quente esperando você"),
    (2, "tarefa atrasada"),
    (3, "quente vencendo"),
    (4, "respondeu e espera"),
    (5, "morno com chance"),
    (6, "no fluxo"),
)
ROTULO_PRIORIDADE = dict(PRIORIDADES)

#: estados que contam como "tarefa atrasada" (2º nível)
_ATRASADOS = ("critico", "atrasado")


def prioridade(lead: dict) -> int:
    """Em qual dos seis níveis do § 7 este lead cai. 1 é o mais urgente.

    A ordem dos `if` É a regra: quem cai no 1º não é reavaliado pro 2º. Um quente
    esperando resposta há três dias e com tarefa atrasada é UM lead, e ele aparece
    uma vez só, no topo.
    """
    quente = lead.get("temperatura") == "quente"
    espera_nos = lead.get("bola") == "aguardando vendedor"
    estado = lead.get("estado")
    if quente and espera_nos:
        return 1
    if estado in _ATRASADOS:
        return 2
    if quente and estado in ("hoje", "atrasado", "critico"):
        return 3
    if espera_nos:
        return 4
    if lead.get("temperatura") == "morno" and estado in ("hoje", "agendado"):
        return 5
    return 6


def ordenar(linhas: list[dict], por_temperatura: bool = False) -> list[dict]:
    """A ordem da fila.

    `por_temperatura=False` (o padrão) é a ordem de sempre: estado mais urgente,
    festa mais próxima, mais atrasado. `True` põe os seis níveis do § 7 na frente
    dela — e a ordem antiga vira o critério de desempate DENTRO de cada nível, que
    é o que impede a fila de virar uma lista de quentes em ordem aleatória.

    Nasce desligado de propósito: mudar a ordem da fila muda o que três pessoas
    veem primeiro todo dia de manhã. Quem liga é o dono, na Régua.
    """
    pos = {e: i for i, e in enumerate(ESTADOS)}

    def chave(x):
        antiga = (pos.get(x["estado"], 9),
                  x["faltam"] if x["faltam"] is not None else 9999,
                  -x["atraso_h"])
        return ((x.get("prioridade") or 6,) + antiga) if por_temperatura else antiga

    return sorted(linhas, key=chave)


def resumo(linhas: list[dict]) -> dict:
    """Os quatro indicadores do topo + os que não pedem nada."""
    r = {e: 0 for e in ESTADOS}
    for x in linhas:
        r[x["estado"]] = r.get(x["estado"], 0) + 1
    r["ativos"] = len(linhas)
    r["com_festa"] = sum(1 for x in linhas if x["estado"] == "critico" and x["evento_em"])
    r["adiados"] = sum(1 for x in linhas if x["adiados"] >= ADIAMENTOS_ATE_MOTIVO)
    return r


def por_vendedor(linhas: list[dict]) -> list[dict]:
    """O painel da gestão: os mesmos indicadores, uma linha por pessoa."""
    d: dict = {}
    for x in linhas:
        k = x["vendedor_id"]
        v = d.setdefault(k, {"id": k, "nome": x["vendedor"], "ativos": 0, "adiados": 0,
                             **{e: 0 for e in ESTADOS}})
        v["ativos"] += 1
        v[x["estado"]] += 1
        if x["adiados"] >= ADIAMENTOS_ATE_MOTIVO:
            v["adiados"] += 1
    return sorted(d.values(), key=lambda v: -v["ativos"])


# ------------------------------------------------------------------ a marcação

def exige_motivo(c, conta_id: int, lead_id: int) -> bool:
    """Do 3º adiamento SEGUIDO sem nenhuma mensagem no meio, o motivo passa a ser
    obrigatório. Reagendar depois de falar com o cliente é trabalho; reagendar sem
    falar é adiar, e adiar em silêncio é o furo que a régua existe pra fechar."""
    r = c.execute(
        """select count(*) from follow_up_marcacoes fm
            where fm.conta_id=%s and fm.prospeccao_id=%s and not fm.automatico
              and fm.criado_em > coalesce(
                    (select max(m.criado_em) from mensagens m
                       join conversas cv on cv.id = m.conversa_id
                      where cv.prospeccao_id = fm.prospeccao_id), '-infinity'::timestamptz)""",
        (conta_id, lead_id)).fetchone()
    return int(r[0] if r else 0) >= ADIAMENTOS_ATE_MOTIVO - 1


def marcar(c, conta_id: int, lead_id: int, prazo: datetime, acao: str = "",
           membro_id: int | None = None, motivo: str = "",
           automatico: bool = False, agora: datetime | None = None) -> dict:
    """Marca (ou remarca) a próxima ação. Nada é sobrescrito: cada marcação é uma
    linha nova, e `proximo_contato_em` só carrega a vigente pras telas antigas.

    `agora` é o instante da marcação. Ele existe porque o CRIADO_EM É COMPARADO:
    `exige_motivo` e a coluna `na_mao` das telas perguntam "esta marcação é mais
    nova que a última mensagem do cliente?" — quem responde sim adiou em silêncio,
    quem responde não adiou depois de conversar. Deixando o Postgres carimbar com
    `now()`, essa comparação passa a misturar DOIS relógios: o do banco, na hora da
    transação, e o que o chamador injeta em `leads`/`avaliar`.

    Em produção os dois coincidem e ninguém percebe. Sob relógio injetado eles se
    separam — e em 09/09/2026 se separaram de verdade: `AGORA` dos testes era uma
    data fixa (09/09 12:00 UTC) que até a véspera era futuro. Quando o relógio real
    passou dela, "mensagem mais nova que a marcação" inverteu e dois testes que
    nunca tinham falhado passaram a falhar TODO DIA, na main, sem ninguém ter
    mexido em follow-up.

    É o mesmo defeito que o teto diário teve em 07/09, noutro ponto do módulo: hora
    do banco competindo com hora injetada. Agora quem marca diz quando.
    """
    if not automatico and not (motivo or "").strip() and exige_motivo(c, conta_id, lead_id):
        return {"ok": False, "erro": "motivo_obrigatorio"}
    # coalesce e não `agora or now()` em Python: sem `agora`, o carimbo continua
    # sendo o do banco, que é o comportamento de sempre pra quem chama sem relógio
    c.execute("""insert into follow_up_marcacoes
                   (conta_id, prospeccao_id, prazo_em, acao, membro_id, automatico,
                    motivo, criado_em)
                 values (%s,%s,%s,%s,%s,%s,%s, coalesce(%s, now()))""",
              (conta_id, lead_id, prazo, (acao or "")[:200], membro_id, automatico,
               (motivo or "")[:400], agora))
    c.execute("""update prospeccao set proximo_contato_em=%s, atualizado_em=now()
                  where id=%s and conta_id=%s""", (prazo, lead_id, conta_id))
    n = c.execute("""select count(*) from follow_up_marcacoes
                      where conta_id=%s and prospeccao_id=%s and not automatico""",
                  (conta_id, lead_id)).fetchone()
    return {"ok": True, "adiamentos": int(n[0] if n else 0)}


def historico(c, conta_id: int, lead_id: int, limite: int = 20) -> list[dict]:
    """Quem adiou, pra quando e por quê — o que o banco nunca guardou."""
    linhas = c.execute(
        """select fm.prazo_em, fm.acao, fm.motivo, fm.automatico, fm.criado_em,
                  coalesce(nullif(mb.nome,''), mb.email, 'o sistema')
             from follow_up_marcacoes fm
             left join membros mb on mb.id = fm.membro_id
            where fm.conta_id=%s and fm.prospeccao_id=%s
            order by fm.criado_em desc, fm.id desc limit %s""", (conta_id, lead_id, limite)).fetchall()
    return [{"prazo": r[0], "acao": r[1], "motivo": r[2], "automatico": r[3],
             "em": r[4], "quem": r[5]} for r in linhas]


# ------------------------------------------------------------------ a cobrança

def _avisos_hoje(c, conta_id: int, agora: datetime) -> dict:
    """Quantos leads cada vendedor já foi cobrado hoje — de QUALQUER motor. O teto
    é um orçamento de atenção da pessoa, não um por funcionalidade.

    O "hoje" vem de `agora`, não do relógio do banco. Não é detalhe: com
    `date_trunc('day', now())` a virada do dia dependeria de dois relógios
    diferentes — o do Postgres na hora da consulta e o que a passada recebeu — e
    um teste que injeta o instante mediria o dia errado."""
    linhas = c.execute(
        """select membro_id, count(distinct prospeccao_id) from funil_avisos
            where conta_id=%s and not simulado and criado_em >= date_trunc('day', %s::timestamptz)
            group by membro_id""", (conta_id, agora)).fetchall()
    return {m: n for m, n in linhas}


def avaliar(c, conta_id: int, agora: datetime | None = None,
            perfil: dict | None = None) -> dict:
    """Uma passada de cobrança do follow-up. Devolve {avisos, simulados, represados, pendentes}.

    `represados` são os que venceram mas não viraram aviso porque o vendedor bateu
    o teto do dia. Não somem: amanhã o teto zera e o aviso sai, porque o dedup é
    pelo FATO (o prazo), não pela data do aviso.
    """
    fora = {"avisos": 0, "simulados": 0, "represados": 0, "pendentes": []}
    cfg = config(c, conta_id)
    modo = cfg["follow_up_modo"]
    if modo == "off":
        return fora
    agora = agora or datetime.now(timezone.utc)
    # empresa fechada não cobra ninguém — e o ensaio obedece a mesma regra, senão
    # a simulação mentiria sobre o que teria acontecido
    if not fr.dentro_da_janela(agora, cfg):
        return fora
    if perfil is None:
        # sem perfil informado, o de serviço: é o que NÃO fala de festa, e errar
        # pra esse lado nunca inventa um relógio de data pra quem não tem data
        from finance import raio_x_perfil as rxp
        perfil = rxp.perfil(None)
    simulado = modo == "observando"
    teto_usado, teto = _avisos_hoje(c, conta_id, agora), cfg["fu_teto_dia"]
    out = dict(fora, pendentes=[])
    for x in ordenar(leads(c, conta_id, perfil, agora, cfg)):
        if x["estado"] in ("andamento", "agendado", "sem_acao") or not x["prazo"]:
            continue
        atraso = x["atraso_h"]
        alcancados = [d for d in DEGRAUS if atraso >= d[1]]
        if not alcancados:
            continue
        vend = x["vendedor_id"]
        if vend and not simulado and teto_usado.get(vend, 0) >= teto:
            out["represados"] += 1
            continue
        novo = None
        for chave, _h, quem in alcancados:
            cur = c.execute(
                """insert into funil_avisos (conta_id, prospeccao_id, estado, nivel, etapa,
                                             ref_em, simulado, membro_id, criado_em)
                   values (%s,%s,'follow_up',%s,'',%s,%s,%s,%s) on conflict do nothing""",
                (conta_id, x["id"], chave, x["prazo"], simulado, vend, agora))
            if cur.rowcount > 0:
                novo = (chave, quem)
        if not novo:
            continue                      # já cobrado por este mesmo fato
        if simulado:
            out["simulados"] += 1
            continue
        if vend:
            teto_usado[vend] = teto_usado.get(vend, 0) + 1
        out["avisos"] += 1
        out["pendentes"].append({"lead_id": x["id"], "membro_id": vend, "quem": x["quem"],
                                 "degrau": novo[0], "nivel": novo[1], "acao": x["acao"],
                                 "atraso_h": atraso})
    return out


def notificar(pool, conta_id: int, pendentes: list[dict]) -> None:
    """Push no app + e-mail, agrupado por pessoa. Best-effort inteiro: a cobrança
    não pode derrubar o poller, e um aviso perdido custa menos que um ciclo que
    não roda.

    Agrupa de propósito: sete avisos separados viram sete notificações ignoradas;
    um "7 leads esperando você" é lido. Do degrau a48 em diante o gestor entra
    JUNTO — o vendedor continua sendo avisado, senão vira fofoca sobre ele.
    """
    if not pendentes:
        return
    gestores = []
    if any(p["nivel"] == "gestor" for p in pendentes):
        try:
            with pool.connection() as c:
                gestores = [r[0] for r in c.execute(
                    "select id from membros where conta_id=%s and coalesce(ativo,true) "
                    "and papel in ('dono','gestor')", (conta_id,)).fetchall()]
        except Exception:  # noqa: BLE001
            gestores = []
    por_membro: dict = {}
    for p in pendentes:
        destinos = list({p["membro_id"], *(gestores if p["nivel"] == "gestor" else [])})
        for d in destinos:
            if d:
                por_membro.setdefault(d, []).append(p)
    for membro_id, itens in por_membro.items():
        try:
            with pool.connection() as c:
                m = c.execute("select coalesce(nullif(nome,''), email), email from membros "
                              "where id=%s and conta_id=%s", (membro_id, conta_id)).fetchone()
            if not m:
                continue
            nome, email = m
            if len(itens) == 1:
                it = itens[0]
                titulo = f"{EMOJI['hoje'] if it['degrau'] == 'venc' else '🚨'} {it['quem']} {_DEGRAU_TXT[it['degrau']]}"
                corpo = f"{it['acao']} · toque pra abrir"
            else:
                titulo = f"⏱️ {len(itens)} leads esperando follow-up"
                corpo = " · ".join(i["quem"] for i in itens[:3])
            try:
                from finance import cockpit as _ck
                _ck.enviar_push(pool, conta_id, membro_id, titulo, corpo, "/cockpit")
            except Exception:  # noqa: BLE001
                pass
            if email and "@" in email:
                try:
                    from finance import email_sender as es
                    es.enviar_aviso(email, titulo, corpo + ". Abra o Zaq pra responder.", nome=nome)
                except Exception:  # noqa: BLE001
                    pass
        except Exception:  # noqa: BLE001
            _log.warning("aviso de follow-up falhou (membro %s)", membro_id, exc_info=True)


# ------------------------------------------------------------------ o motor
_LOCK = 771148   # vizinho do lock da régua (771147)


def sincronizar(c, conta_id: int, linhas: list[dict]) -> int:
    """Escreve o prazo proposto em `proximo_contato_em`, pras telas que já leem
    esse campo (a ficha, a base, o Cockpit). Só mexe em quem está diferente — um
    update por lead a cada 2 minutos encheria `atualizado_em` de ruído."""
    n = 0
    for x in linhas:
        if x["na_mao"] or not x["prazo"]:
            continue
        cur = c.execute(
            """update prospeccao set proximo_contato_em=%s
                where id=%s and conta_id=%s
                  and (proximo_contato_em is null or proximo_contato_em <> %s)""",
            (x["prazo"], x["id"], conta_id, x["prazo"]))
        n += cur.rowcount
    return n


def rodar(pool, agora: datetime | None = None) -> dict:
    """Uma passada em todas as contas que ligaram. Chamada pelo poller (web/app.py),
    junto de campanhas, lembretes e régua — sem cron novo no Render.

    Best-effort por conta: conta com dado torto não para a passada das outras.
    """
    total = {"contas": 0, "avisos": 0, "simulados": 0, "represados": 0, "sincronizados": 0}
    with pool.connection() as lockc:
        # dois workers no Render: sem o lock os dois cobram o mesmo lead no mesmo
        # segundo, e o índice único viraria erro em vez de dedup
        if not lockc.execute("select pg_try_advisory_lock(%s)", (_LOCK,)).fetchone()[0]:
            return total
        try:
            with pool.connection() as c:
                contas = [r[0] for r in c.execute(
                    "select conta_id from funil_regua where follow_up_modo <> 'off'").fetchall()]
            from finance import raio_x_perfil as rxp
            for conta_id in contas:
                try:
                    perfil = rxp.perfil_da_conta(pool, conta_id)
                    if perfil["chave"] not in PERFIS_COM_TELA:
                        continue
                    with pool.connection() as c:
                        cfg = config(c, conta_id)
                        total["sincronizados"] += sincronizar(
                            c, conta_id, leads(c, conta_id, perfil, agora, cfg))
                        r = avaliar(c, conta_id, agora, perfil)
                        c.commit()
                    total["contas"] += 1
                    for k in ("avisos", "simulados", "represados"):
                        total[k] += r[k]
                    # a mensagem sai DEPOIS do commit, nunca antes: push não tem
                    # como ser desfeito por um erro adiante
                    notificar(pool, conta_id, r["pendentes"])
                except Exception:  # noqa: BLE001
                    _log.warning("follow-up falhou na conta %s", conta_id, exc_info=True)
        finally:
            lockc.execute("select pg_advisory_unlock(%s)", (_LOCK,))
    return total
