"""A ESTEIRA DA COBRANÇA: 10 por dia, 3 cobranças em 7 dias, e o vendedor decide.

Desenho do dono em 19/09/2026. As palavras dele: "cobrar os 10 leads do dia e todo
dia manda o resumo do que ele fez ou não fez e no outro dia manda mais 10. Ele é
que tem que escrever o histórico e colocar como perdido ... tem 7 dias de prazo
final".

POR QUE A COBRANÇA ANTIGA NÃO DAVA CHANCE NENHUMA
Ela contava os degraus a partir do prazo VENCIDO da etapa, e os leads da Prime
estavam vencidos havia um mês. Medido em 19/09: a Layane recebeu venc, 24h, 48h e
72h **no mesmo minuto** — o prazo dela era de 14/08. O lead nascia com as três
chances gastas. Aqui o relógio é `entrou_em`: a hora em que ESTE lead entrou na
esteira, hoje, e não um prazo que venceu antes de o motor existir.

O QUE CONTA COMO "O VENDEDOR AGIU" (`resolver`)
Qualquer mensagem nossa para o cliente depois da entrada — **inclusive a que saiu
do WhatsApp Web**, sem passar pelo sistema. Medido em setembro na conta 34: 1.191
das 1.505 mensagens saíram de lá, e o app do vendedor teve 19 acessos em 19 dias
entre quatro pessoas. Uma esteira que só enxergasse ação feita dentro do produto
cobraria gente que trabalhou o dia inteiro.

Também contam: mover o card (a mão do vendedor manda), e o cliente voltar a falar
— quem respondeu deixou de ser lead parado, e cobrar o vendedor por ele seria
cobrá-lo do sucesso.

O DIA 7 (a escolha "C" do dono)
No sétimo dia o aviso muda de tom: "último dia, resolva ou fecha hoje às 19h". Se
ninguém tratar até o fim da janela, a esteira fecha o lead — com motivo que diz a
verdade (`sem_tratativa`), separado do `nao_respondeu` que culpa o cliente. O
vendedor teve três cobranças e um aviso final; o fechamento é consequência de uma
escolha dele, não uma surpresa.

NASCE DESLIGADA. `esteira_modo` é 'off'; em 'observando' ela entra, cobra e
resume, mas NÃO fecha ninguém no dia 7.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from finance import funil_perda as _perda
from finance import funil_regua as fr

_log = logging.getLogger("openclaw.esteira")

#: vizinho dos locks da régua (771147), follow-up (771148), teto (771149),
#: agenda (771150) e perdido (771151)
_LOCK = 771152

MODOS = ("off", "observando", "ligado")

#: o motivo do movimento — é por ele que o relatório separa o que a esteira fechou
#: do que uma pessoa fechou ('manual') e do que o cliente matou ('sem_resposta')
MOTIVO_MOV = "sem_tratativa"

#: na ficha do lead. NÃO é 'nao_respondeu': o cliente não fez nada de errado aqui.
MOTIVO_PERDA = "outro"
TEXTO_PERDA = ("Prazo de 7 dias vencido sem tratativa — o vendedor foi cobrado "
               "três vezes e avisado no último dia.")

#: quantos entram por vendedor por dia, e em que dias desde a entrada ele é cobrado
POR_DIA_PADRAO = 10
DIAS_PADRAO = (1, 3, 7)


def config(c, conta_id: int) -> dict:
    """Modo, quantos entram por dia e os dias de cobrança.

    Reaproveita `fu_teto_dia` e `fu_toques_dias` da régua de propósito: são os
    mesmos números que o dono já configurou na tela do Follow-up (10 e "1,3,7" na
    Prime). Um segundo par de campos dizendo a mesma coisa seria duas verdades pra
    sair de sincronia no primeiro ajuste.
    """
    base = fr.config(c, conta_id)
    r = c.execute(
        """select coalesce(esteira_modo,'off'), fu_teto_dia, fu_toques_dias
             from funil_regua where conta_id=%s""", (conta_id,)).fetchone()
    if not r:
        return dict(base, esteira_modo="off", por_dia=POR_DIA_PADRAO, dias=DIAS_PADRAO)
    dias = _dias(r[2])
    return dict(base, esteira_modo=(r[0] or "off"),
                por_dia=int(r[1] or POR_DIA_PADRAO), dias=dias)


def _dias(txt: str | None) -> tuple[int, ...]:
    """"1,3,7" → (1, 3, 7). Lista torta cai no padrão — a esteira não pode parar
    de cobrar porque alguém digitou vírgula a mais na tela."""
    try:
        v = tuple(sorted({int(x) for x in (txt or "").split(",") if x.strip()}))
        return v if v else DIAS_PADRAO
    except Exception:  # noqa: BLE001
        return DIAS_PADRAO


def prazo_final(dias: tuple[int, ...]) -> int:
    """O último dia da escada é o prazo final. Na Prime, 7."""
    return max(dias) if dias else max(DIAS_PADRAO)


# ------------------------------------------------------------------ quem já agiu

def resolver(c, conta_id: int, agora: datetime | None = None) -> int:
    """Fecha a linha de quem teve ação depois de entrar. Devolve quantos saíram.

    Roda ANTES de cobrar e ANTES de fechar, sempre: cobrar quem já respondeu é o
    jeito mais rápido de o vendedor parar de ler o aviso.
    """
    agora = agora or datetime.now(timezone.utc)
    n = c.execute(
        """update follow_up_esteira e
              set resolvido_em = %s,
                  resolucao = case
                    when exists (select 1 from conversas cv join mensagens m on m.conversa_id=cv.id
                                  where cv.prospeccao_id = e.prospeccao_id
                                    and m.direcao='in' and m.criado_em > e.entrou_em) then 'cliente_voltou'
                    when exists (select 1 from funil_movimentos fm
                                  where fm.prospeccao_id = e.prospeccao_id
                                    and fm.criado_em > e.entrou_em
                                    and fm.para in ('perdido','ganho')) then 'fechou'
                    when exists (select 1 from funil_movimentos fm
                                  where fm.prospeccao_id = e.prospeccao_id
                                    and fm.criado_em > e.entrou_em
                                    and fm.motivo not like 'temperatura%%') then 'moveu'
                    else 'falou' end
            where e.conta_id = %s and e.resolvido_em is null and e.fechado_em is null
              and (exists (select 1 from conversas cv join mensagens m on m.conversa_id=cv.id
                            where cv.prospeccao_id = e.prospeccao_id and m.direcao='out'
                              and m.criado_em > e.entrou_em)
                or exists (select 1 from conversas cv join mensagens m on m.conversa_id=cv.id
                            where cv.prospeccao_id = e.prospeccao_id and m.direcao='in'
                              and m.criado_em > e.entrou_em)
                or exists (select 1 from funil_movimentos fm
                            where fm.prospeccao_id = e.prospeccao_id
                              and fm.criado_em > e.entrou_em
                              and fm.motivo not like 'temperatura%%'))""",
        (agora, conta_id)).rowcount
    return int(n or 0)


# ------------------------------------------------------------------ quem entra hoje

def entrar(c, conta_id: int, agora: datetime | None = None, cfg: dict | None = None) -> list[dict]:
    """Põe na esteira os `por_dia` leads mais antigos de CADA vendedor.

    Só entra quem está numa etapa com prazo, com a bola CONOSCO e fora da esteira.
    A bola é o mesmo portão de sempre: cliente esperando resposta nunca é cobrança
    do vendedor — é dívida nossa, e ele vê isso na Fila, não aqui.

    Idempotente pelo índice único: rodar de novo no mesmo dia não duplica ninguém,
    e a contagem do dia sai de quem JÁ entrou hoje, não de um contador à parte.
    """
    agora = agora or datetime.now(timezone.utc)
    cfg = cfg or config(c, conta_id)
    por_dia = max(0, int(cfg.get("por_dia") or POR_DIA_PADRAO))
    if not por_dia:
        return []
    etapas = [r[0] for r in c.execute(
        "select chave from funil_etapas where conta_id=%s and teto_dias is not null and teto_dias > 0",
        (conta_id,)).fetchall()]
    if not etapas:
        return []
    linhas = c.execute(
        """with ja_hoje as (
             select membro_id, count(*) as n from follow_up_esteira
              where conta_id = %(conta)s and entrou_em >= %(inicio)s group by membro_id),
           fila as (
             select p.id, p.vendedor_id, p.status,
                    coalesce(nullif(p.contato,''), nullif(p.empresa,''), 'Lead') as quem,
                    coalesce((select max(fm.criado_em) from funil_movimentos fm
                               where fm.prospeccao_id = p.id and fm.para = p.status),
                             p.criado_em) as desde,
                    row_number() over (partition by p.vendedor_id
                                       order by coalesce((select max(fm.criado_em) from funil_movimentos fm
                                                           where fm.prospeccao_id = p.id and fm.para = p.status),
                                                         p.criado_em)) as pos
               from prospeccao p
              where p.conta_id = %(conta)s and p.estagio = 'lead' and p.status = any(%(etapas)s)
                and not exists (select 1 from follow_up_esteira e
                                 where e.conta_id = %(conta)s and e.prospeccao_id = p.id
                                   and e.resolvido_em is null and e.fechado_em is null)
                -- a bola tem que ser NOSSA: compara ids, não datas
                and coalesce((select max(m.id) filter (where m.direcao='out')
                                from conversas cv join mensagens m on m.conversa_id = cv.id
                               where cv.prospeccao_id = p.id), 0)
                 >= coalesce((select max(m.id) filter (where m.direcao='in')
                                from conversas cv join mensagens m on m.conversa_id = cv.id
                               where cv.prospeccao_id = p.id), 0))
           select f.id, f.vendedor_id, f.status, f.quem
             from fila f left join ja_hoje j on j.membro_id is not distinct from f.vendedor_id
            where f.pos <= (%(por_dia)s - coalesce(j.n, 0))
            order by f.vendedor_id, f.pos""",
        {"conta": conta_id, "etapas": etapas, "por_dia": por_dia,
         "inicio": _inicio_do_dia(agora)}).fetchall()
    novos = []
    for lead_id, vend, etapa, quem in linhas:
        n = c.execute(
            """insert into follow_up_esteira (conta_id, prospeccao_id, membro_id, etapa, entrou_em)
               values (%s,%s,%s,%s,%s)
               on conflict do nothing""",
            (conta_id, lead_id, vend, etapa or "", agora)).rowcount
        if n:
            novos.append({"id": lead_id, "membro_id": vend, "etapa": etapa, "quem": quem})
    return novos


def _inicio_do_dia(agora: datetime) -> datetime:
    """Meia-noite em Brasília, em UTC. O dia da esteira é o dia de quem trabalha,
    não o do banco — às 21h de Fortaleza já é amanhã em UTC, e a conta do dia
    viraria no meio do expediente."""
    br = agora + timedelta(hours=-3)
    return datetime(br.year, br.month, br.day, tzinfo=timezone.utc) + timedelta(hours=3)


# ------------------------------------------------------------------ o que cobrar hoje

def cobrancas(c, conta_id: int, agora: datetime | None = None, cfg: dict | None = None) -> list[dict]:
    """Quem está na esteira e hoje é dia de cobrar. `ultimo_dia` marca o dia 7.

    O dia é contado por DATA, não por 24h corridas: quem entrou às 18h de segunda
    é cobrado na terça de manhã como "dia 1", e não às 18h de terça. O aviso é da
    manhã; a conta tem que ser do calendário de quem lê.
    """
    agora = agora or datetime.now(timezone.utc)
    cfg = cfg or config(c, conta_id)
    dias, final = cfg["dias"], prazo_final(cfg["dias"])
    linhas = c.execute(
        """select e.id, e.prospeccao_id, e.membro_id, e.entrou_em, e.etapa,
                  coalesce(nullif(p.contato,''), nullif(p.empresa,''), 'Lead') as quem,
                  p.evento_em
             from follow_up_esteira e join prospeccao p on p.id = e.prospeccao_id
            where e.conta_id=%s and e.resolvido_em is null and e.fechado_em is null
            order by e.membro_id, e.entrou_em""", (conta_id,)).fetchall()
    hoje = _dia_br(agora)
    fora = []
    for eid, lead, membro, entrou, etapa, quem, festa in linhas:
        d = (hoje - _dia_br(entrou)).days
        if d in dias:
            fora.append({"esteira_id": eid, "id": lead, "membro_id": membro, "quem": quem,
                         "dia": d, "ultimo_dia": d >= final, "etapa": etapa,
                         "evento_em": festa, "entrou_em": entrou})
    return fora


def _dia_br(dt: datetime) -> date:
    return (dt + timedelta(hours=-3)).date()


# ------------------------------------------------------------------ o dia 7

def fechar_vencidos(c, conta_id: int, agora: datetime | None = None,
                    cfg: dict | None = None) -> list[dict]:
    """Fecha quem passou do prazo final sem tratativa. Só em 'ligado'.

    Roda no FIM da janela de atendimento, não de manhã: o aviso do último dia diz
    "resolva ou fecha hoje às 19h", e fechar antes disso faria do aviso uma mentira.
    """
    agora = agora or datetime.now(timezone.utc)
    cfg = cfg or config(c, conta_id)
    if cfg.get("esteira_modo") != "ligado" or not _fim_da_janela(agora, cfg):
        return []
    final = prazo_final(cfg["dias"])
    hoje = _dia_br(agora)
    linhas = c.execute(
        """select e.id, e.prospeccao_id, e.membro_id, e.entrou_em, e.etapa, p.status,
                  coalesce(nullif(p.contato,''), nullif(p.empresa,''), 'Lead')
             from follow_up_esteira e join prospeccao p on p.id = e.prospeccao_id
            where e.conta_id=%s and e.resolvido_em is null and e.fechado_em is null
              and p.status <> 'perdido'""", (conta_id,)).fetchall()
    fechados = []
    for eid, lead, membro, entrou, etapa, status_hoje, quem in linhas:
        if (hoje - _dia_br(entrou)).days < final:
            continue
        # A MÃO DE QUEM MEXEU MANDA. A etapa comparada é a de quando o lead ENTROU
        # na esteira, não a de agora: ler o status atual e compará-lo consigo mesmo
        # é uma guarda que nunca dispara — foi o defeito que o teste pegou. Card em
        # outra coluna significa que alguém decidiu algo, e essa decisão vale mais
        # que o prazo.
        if (status_hoje or "") != (etapa or ""):
            continue
        movido = c.execute(
            """update prospeccao set status='perdido', atualizado_em=now()
                where id=%s and conta_id=%s and status=%s""", (lead, conta_id, etapa)).rowcount
        if not movido:      # alguém mexeu no lead — pessoa ganha da esteira
            continue
        fr.registrar_movimento(c, conta_id, lead, etapa, "perdido", MOTIVO_MOV, membro)
        _perda.registrar(c, conta_id, lead, motivo=MOTIVO_PERDA, descricao=TEXTO_PERDA,
                         etapa_origem=etapa, agora=agora)
        c.execute("update follow_up_esteira set fechado_em=%s where id=%s", (agora, eid))
        fechados.append({"id": lead, "quem": quem, "membro_id": membro, "etapa": etapa})
    return fechados


def _fim_da_janela(agora: datetime, cfg: dict) -> bool:
    """Passou da hora de fechar o expediente? Usa a MESMA janela da régua."""
    fecha = cfg.get("janela_fecha")
    if not fecha:
        return False
    br = agora + timedelta(hours=-3)
    return (br.hour, br.minute) >= (fecha.hour, fecha.minute)


# ------------------------------------------------------------------ o resumo do dia

def resumo(c, conta_id: int, membro_id: int | None, agora: datetime | None = None) -> dict:
    """O que este vendedor fez e não fez — é o "resumo do dia" que o dono pediu.

    A janela é o dia de ontem pra frente: o aviso da manhã fala do que ficou de
    ontem, e o que entrou hoje ainda não teve tempo de ser feito.
    """
    agora = agora or datetime.now(timezone.utc)
    desde = _inicio_do_dia(agora) - timedelta(days=1)
    r = c.execute(
        """select count(*) filter (where e.resolvido_em >= %(desde)s and e.resolucao='falou'),
                  count(*) filter (where e.resolvido_em >= %(desde)s and e.resolucao='moveu'),
                  count(*) filter (where e.resolvido_em >= %(desde)s and e.resolucao='fechou'),
                  count(*) filter (where e.resolvido_em >= %(desde)s and e.resolucao='cliente_voltou'),
                  count(*) filter (where e.fechado_em >= %(desde)s),
                  count(*) filter (where e.resolvido_em is null and e.fechado_em is null)
             from follow_up_esteira e
            where e.conta_id=%(conta)s
              and (%(membro)s::bigint is null or e.membro_id = %(membro)s)""",
        {"conta": conta_id, "membro": membro_id, "desde": desde}).fetchone()
    falou, moveu, fechou, voltou, vencidos, abertos = [int(x or 0) for x in (r or (0,) * 6)]
    return {"falou": falou, "moveu": moveu, "fechou": fechou, "cliente_voltou": voltou,
            "fechados_sem_tratativa": vencidos, "na_esteira": abertos,
            "tratou": falou + moveu + fechou}


# ------------------------------------------------------------------ o motor

def avaliar(c, conta_id: int, agora: datetime | None = None) -> dict:
    """Uma passada da esteira nesta conta: resolve, faz entrar, lista o que cobrar
    e fecha o que venceu. Quem manda a mensagem é `notificar`, depois do commit."""
    agora = agora or datetime.now(timezone.utc)
    cfg = config(c, conta_id)
    vazio = {"modo": cfg.get("esteira_modo"), "resolvidos": 0, "entraram": 0,
             "cobrancas": [], "fechados": []}
    if cfg.get("esteira_modo") == "off":
        return vazio
    resolvidos = resolver(c, conta_id, agora)
    novos = entrar(c, conta_id, agora, cfg)
    return {"modo": cfg["esteira_modo"], "resolvidos": resolvidos, "entraram": len(novos),
            "cobrancas": cobrancas(c, conta_id, agora, cfg),
            "fechados": fechar_vencidos(c, conta_id, agora, cfg)}


def _e_hora_do_aviso(agora: datetime | None, conta_id: int, pool) -> bool:
    """Um aviso por dia, na abertura da janela — não um por ciclo do poller.

    A guarda é o próprio registro do envio: se já saiu aviso da esteira pra esta
    conta hoje, não sai outro. Contador à parte seria um segundo estado pra sair
    de sincronia com o que de fato foi mandado.
    """
    agora = agora or datetime.now(timezone.utc)
    try:
        with pool.connection() as c:
            r = c.execute(
                """select count(*) from aviso_envios
                    where conta_id=%s and origem='esteira' and criado_em >= %s""",
                (conta_id, _inicio_do_dia(agora))).fetchone()
        return not int(r[0] if r else 0)
    except Exception:  # noqa: BLE001 — sem certeza de que já saiu, não manda de novo
        return False


def rodar(pool, agora: datetime | None = None) -> dict:
    """Uma passada em todas as contas que ligaram. Chamada pelo poller."""
    total = {"contas": 0, "entraram": 0, "resolvidos": 0, "fechados": 0}
    with pool.connection() as lockc:
        if not lockc.execute("select pg_try_advisory_lock(%s)", (_LOCK,)).fetchone()[0]:
            return total
        try:
            with pool.connection() as c:
                contas = [r[0] for r in c.execute(
                    "select conta_id from funil_regua where coalesce(esteira_modo,'off') <> 'off'").fetchall()]
            for conta_id in contas:
                try:
                    with pool.connection() as c:
                        r = avaliar(c, conta_id, agora)
                        c.commit()
                    total["contas"] += 1
                    total["entraram"] += r["entraram"]
                    total["resolvidos"] += r["resolvidos"]
                    total["fechados"] += len(r["fechados"])
                    # o aviso sai DEPOIS do commit: mensagem não tem como ser desfeita
                    if r["cobrancas"] and _e_hora_do_aviso(agora, conta_id, pool):
                        notificar(pool, conta_id, r["cobrancas"])
                except Exception:  # noqa: BLE001
                    _log.warning("esteira falhou na conta %s", conta_id, exc_info=True)
        finally:
            lockc.execute("select pg_advisory_unlock(%s)", (_LOCK,))
    return total


# ------------------------------------------------------------------ o aviso

def texto(nome: str, itens: list[dict], resumo_ontem: dict) -> tuple[str, str]:
    """O aviso da manhã: o que ficou de ontem, o que vence hoje, e os nomes.

    OS NOMES SÃO O AVISO. A versão antiga dizia "⏱️ 10 leads esperando follow-up" e
    mandava um link pro app — que a Prime abriu 19 vezes em 19 dias, entre quatro
    pessoas, enquanto mandava 1.191 mensagens pelo WhatsApp Web. Um aviso sem nome
    obriga a abrir uma tela; com nome, o vendedor procura a pessoa no WhatsApp que
    ele já tem aberto e responde. O canal é o celular dele; a ferramenta é a dele.
    """
    ultimos = [i for i in itens if i["ultimo_dia"]]
    resto = [i for i in itens if not i["ultimo_dia"]]
    titulo = (f"🚨 {nome}: {len(ultimos)} fecham hoje" if ultimos
              else f"⏱️ {nome}, {len(itens)} para hoje")
    linhas = []
    tratou, total = resumo_ontem.get("tratou", 0), resumo_ontem.get("na_esteira", 0)
    if tratou or total:
        linhas.append(f"Ontem você tratou {tratou}. Na sua esteira: {total}.")
    if ultimos:
        linhas.append("")
        linhas.append("ÚLTIMO DIA — resolvem hoje ou fecham às 19h:")
        linhas += [f"• {i['quem']}" for i in ultimos[:10]]
    if resto:
        linhas.append("")
        linhas.append("Cobrança de hoje:")
        linhas += [f"• {i['quem']} (dia {i['dia']})" for i in resto[:10]]
    linhas.append("")
    linhas.append("Responda pelo WhatsApp da empresa, ou marque como perdido "
                  "escrevendo o motivo.")
    return titulo, "\n".join(linhas)


def notificar(pool, conta_id: int, cobrancas_hoje: list[dict]) -> None:
    """Um aviso por pessoa, por dia. Best-effort inteiro — cobrança não derruba
    poller, e um aviso perdido custa menos que um ciclo que não roda.

    O WhatsApp vem PRIMEIRO aqui, ao contrário do follow-up antigo, onde ele era o
    último a ser tentado. Não é preferência de canal: é o único que dá recibo de
    entrega e de leitura, e a pergunta que o dono fez em 19/09 — "eles estão vendo
    os avisos?" — não tinha resposta possível por push nem por e-mail.
    """
    if not cobrancas_hoje:
        return
    from finance import aviso_log as _al
    from finance import follow_up as _fu
    por_membro: dict = {}
    for it in cobrancas_hoje:
        if it.get("membro_id"):
            por_membro.setdefault(it["membro_id"], []).append(it)
    for membro_id, itens in por_membro.items():
        try:
            with pool.connection() as c:
                m = c.execute("select coalesce(nullif(nome,''), email), email from membros "
                              " where id=%s and conta_id=%s", (membro_id, conta_id)).fetchone()
                r_ontem = resumo(c, conta_id, membro_id)
                numero = _fu._zap_do_membro(c, conta_id, membro_id)
            if not m:
                continue
            nome, email = m[0] or "você", m[1]
            titulo, corpo = texto(nome, itens, r_ontem)
            if numero:
                r = _fu._mandar_zap(pool, conta_id, numero, _fu._texto_zap(titulo, corpo))
                _al.registrar(pool, conta_id, origem="esteira", canal="whatsapp",
                              membro_id=membro_id, destino=numero, assunto=titulo,
                              n_leads=len(itens), ok=bool(r.get("ok")),
                              motivo=r.get("erro", ""), sid=r.get("sid", ""))
            else:
                _al.registrar(pool, conta_id, origem="esteira", canal="whatsapp",
                              membro_id=membro_id, assunto=titulo, n_leads=len(itens),
                              ok=False, motivo="membro sem WhatsApp cadastrado")
            if email and "@" in email:
                try:
                    from finance import email_sender as es_mail
                    ok = es_mail.enviar_aviso(email, titulo, corpo, nome=nome)
                    _al.registrar(pool, conta_id, origem="esteira", canal="email",
                                  membro_id=membro_id, destino=email, assunto=titulo,
                                  n_leads=len(itens), ok=bool(ok),
                                  motivo="" if ok else "o envio devolveu falso")
                except Exception as e:  # noqa: BLE001
                    _al.registrar(pool, conta_id, origem="esteira", canal="email",
                                  membro_id=membro_id, destino=email, assunto=titulo,
                                  n_leads=len(itens), ok=False,
                                  motivo=f"{type(e).__name__}: {e}")
        except Exception:  # noqa: BLE001
            _log.warning("aviso da esteira falhou (membro %s)", membro_id, exc_info=True)
