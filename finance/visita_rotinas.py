"""AS ROTINAS DA VISITA — o funil novo de eventos, parte 2a
(docs/mockups/funil_novo_rotinas.html, aprovado pelo dono em 27/09/2026 "com as
recomendações").

POR QUE EXISTE. Medido na Prime (só leitura, 27/09/2026): nos últimos 60 dias,
24% das visitas com desfecho foram FALTA (6 de 25), e nenhuma das 29 recebeu
confirmação — porque as 29 foram marcadas pela EQUIPE, e a confirmação que existia
(`finance/ia_visita.py`) só vale pra visita que a IA marca. E quem recebeu a visita
só respondia "veio?" se abrisse a agenda do app.

O QUE ESTE MÓDULO FAZ, por visita, num relógio só (o poller de web/app.py):

  A VISITA DA EQUIPE (a conversa não é da IA):
    ao marcar    "sua visita está marcada: <quando>, com <quem recebe>"
    véspera 18h  "Confirma? Responda 1 para confirmar ou 2 se precisar remarcar."
    2h antes     "Daqui a pouco, às <h>, te esperamos"
    1h30 antes   sem confirmação → quem recebe é avisado ("continua marcada")
    a resposta   1 → confirmada; 2 → quem atende é avisado e REMARCA: a IA não
                 entra na conversa do vendedor (decisão 4 do mockup)
  TODA VISITA (da equipe e da IA):
    1h depois    "📍 A visita da Hana foi às 10h. Ela veio?" pra quem recebeu (na
                 visita da IA, a anfitriã); sem resposta, de novo às 18h
  DEPOIS:
    veio         equipe: 2h depois, se ninguém escreveu ao cliente, o vendedor é
                 lembrado. IA: a IA agradece e oferece o orçamento.
    faltou       o card já volta pra Qualificado (parte 1, `card_pela_visita`); o
                 dono do card é avisado se não foi ele quem recebeu. Na visita da
                 IA, o "sentimos sua falta" continua sendo do `ia_visita`.

O QUE NÃO FAZ. Não mexe no card (os movimentos são os gatilhos da parte 1), não
marca desfecho (quem marca é gente, no app), e não confirma a visita que a IA marcou
(o `ia_visita` já confirma; dois relógios na mesma visita seriam duas perguntas).

AS TRAVAS
  * só conta de eventos que LIGOU as rotinas (`visita_rotinas_config`, uma chave por
    rotina); a Prime nasce ligada (migração 414, com a autorização do dono), as outras
    desligadas;
  * mensagem ao cliente só pelo chip da conversa dele, pelo envio de sempre
    (`agente._mandar`), e só com a conversa FORA da IA — a conversa da IA é da IA;
  * visita com a hora "chutada" pelo sistema (`hora_sugerida`) não é confirmada:
    confirmar um horário que ninguém escolheu é prometer o que não foi combinado;
  * "ao marcar" só pra visita criada DEPOIS de a rotina ligar — ligar não pode virar
    uma rajada de "sua visita está marcada" pras visitas de semanas atrás;
  * cliente: das 8h às 20h; equipe: das 8h às 21h (Brasília). Fora disso, espera;
  * reivindica antes de mandar (a coluna nula vira agora): dois workers nunca mandam
    a mesma coisa duas vezes; o envio que falha devolve a reivindicação e tenta de
    novo em 15 min, até 5 vezes (igual ao `ia_visita`);
  * no máximo `TETO_CICLO` mensagens a clientes por ciclo, em toda a base.

Não toca conexão, chip, `canais_config` nem pareamento (CLAUDE.md §0/§1): só LÊ a
conversa e manda pelo caminho de envio que já existe.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from finance import agenda as ag

_log = logging.getLogger(__name__)

#: vizinho da trava do `ia_visita` (771171/771172)
_LOCK = 771182          # era 771173, a mesma do relógio do sinal (ia_orcamento) — revisão de 27/09/2026

#: as três chaves da conta (migração 414). Cada uma liga uma família de rotinas.
CHAVES = ("confirmar", "perguntar_veio", "depois_visita")

TETO_CICLO = 6
MAX_FALHAS = 5
INTERVALO_FALHA = timedelta(minutes=15)

#: a visita da equipe: "ao marcar" só nas 2 primeiras horas depois de criada, e só
#: se ainda faltar pelo menos isto pra ela
AO_MARCAR_JANELA = timedelta(hours=2)
AO_MARCAR_ANTES = timedelta(hours=3)
#: e só depois de 3 min: quem marca pelo app já avisa o cliente, e a mensagem dele
#: leva alguns segundos pra sair e ser gravada — sem a espera, o relógio podia passar
#: nesse meio tempo e mandar a mesma notícia de novo (revisão de 27/09/2026)
AO_MARCAR_ESPERA = timedelta(minutes=3)
#: o "veio?": 1h depois do horário; a 2ª vez às 18h do mesmo dia
VEIO_DEPOIS = timedelta(hours=1)
VEIO_HORA_2 = 18
#: depois da visita: 3h depois do horário (= ~2h depois de ela acabar)
DEPOIS_DEPOIS = timedelta(hours=3)
#: nada disso vale pra visita mais velha que isto (ligar não reabre o passado)
JANELA_PASSADO = timedelta(hours=30)

HORAS_CLIENTE = (8, 20)
#: a resposta ao "1"/"2" que o cliente acabou de mandar sai fora da janela, se ele
#: escreveu há até isto — é conversa, não disparo
RESPOSTA_NA_HORA = timedelta(minutes=30)
HORAS_EQUIPE = (8, 21)


# ------------------------------------------------------------------ config

def config(c, conta_id: int) -> dict:
    """As chaves da conta. Tolerante: sem a 414, tudo desligado."""
    base = {k: False for k in CHAVES} | {"ligado_em": None}
    try:
        with c.transaction():
            r = c.execute("""select confirmar, perguntar_veio, depois_visita, ligado_em
                               from visita_rotinas_config where conta_id=%s""",
                          (conta_id,)).fetchone()
    except Exception:  # noqa: BLE001
        return base
    if not r:
        return base
    return {"confirmar": bool(r[0]), "perguntar_veio": bool(r[1]),
            "depois_visita": bool(r[2]), "ligado_em": r[3]}


def salvar_config(c, conta_id: int, valores: dict) -> None:
    """Grava as três chaves. `ligado_em` anda quando alguma chave LIGA (de desligada
    pra ligada): é o marco do "ao marcar" — só visita criada depois dele."""
    novo = {k: bool(valores.get(k)) for k in CHAVES}
    antes = config(c, conta_id)
    ligou = any(novo[k] and not antes[k] for k in CHAVES)
    c.execute("""insert into visita_rotinas_config (conta_id, confirmar, perguntar_veio,
                                                    depois_visita, ligado_em, atualizado_em)
                 values (%s,%s,%s,%s, case when %s then now() end, now())
                 on conflict (conta_id) do update
                 set confirmar=excluded.confirmar, perguntar_veio=excluded.perguntar_veio,
                     depois_visita=excluded.depois_visita,
                     ligado_em=case when %s then now() else visita_rotinas_config.ligado_em end,
                     atualizado_em=now()""",
              (conta_id, novo["confirmar"], novo["perguntar_veio"], novo["depois_visita"],
               ligou, ligou))


# ------------------------------------------------------------------ textos

def _hora(ini: datetime) -> str:
    loc = ini.astimezone(ag.BRT)
    return f"{loc.hour}h" + (f"{loc.minute:02d}" if loc.minute else "")


def texto_ao_marcar(ini: datetime, nome: str, empresa: str, quem_recebe: str, local: str,
                    agora: datetime) -> str:
    from finance.ia_visita import _momento_vespera, fmt
    com = f", com {quem_recebe}" if quem_recebe else ""
    onde = f" 📍 {local}." if local else ""
    depois = (" Na véspera eu te chamo pra confirmar 😊"
              if _momento_vespera(ini, False) > agora else "")
    return (f"Oi{', ' + nome if nome else ''}! Sua visita ao {empresa} está marcada: "
            f"{fmt(ini)}{com}.{onde}{depois}")


def texto_remarcar(quem_atende: str) -> str:
    return (f"Tudo bem! {quem_atende or 'A nossa equipe'} já vai falar com você pra achar "
            "outro horário 😊")


def texto_agradecimento(nome: str, ini: datetime, agora: datetime, tipo: str,
                        convidados) -> str:
    """A IA, depois da visita que ela marcou (modelo 2 do mockup)."""
    quando = "hoje" if ini.astimezone(ag.BRT).date() == agora.astimezone(ag.BRT).date() \
        else "na visita"
    festa = "da sua festa"
    detalhe = ", ".join(x for x in ((tipo or "").strip().lower(),
                                    f"{convidados} convidados" if convidados else "") if x)
    if detalhe:
        festa += f" ({detalhe})"
    return (f"Oi{', ' + nome if nome else ''}! Foi um prazer te receber {quando} 😊 "
            f"Gostou do espaço? Já posso montar o orçamento {festa}?")


# ------------------------------------------------------------------ o aviso à equipe

def avisar(pool, conta_id: int, membro_id: int | None, titulo: str, corpo: str,
           url: str, origem: str = "visita") -> bool:
    """WhatsApp de avisos da empresa + push, como a esteira (finance/esteira.py): o
    WhatsApp primeiro, porque é o único canal com recibo; cada envio vai pro
    `aviso_log`. Best-effort inteiro — nunca levanta. True se algum canal aceitou."""
    if not membro_id:
        return False
    from finance import aviso_log as _al
    from finance import follow_up as _fu
    ok = False
    try:
        from finance.email_sender import _app_url
        link = _app_url().rstrip("/") + url
    except Exception:  # noqa: BLE001
        link = ""
    try:
        with pool.connection() as c:
            numero = _fu._zap_do_membro(c, conta_id, membro_id)
        if numero:
            texto = "\n\n".join(x for x in (titulo, corpo, f"Abrir: {link}" if link else "") if x)
            r = _fu._mandar_zap(pool, conta_id, numero, texto)
            ok = ok or bool(r.get("ok"))
            _al.registrar(pool, conta_id, origem=origem, canal="whatsapp", membro_id=membro_id,
                          destino=numero, assunto=titulo, n_leads=1, ok=bool(r.get("ok")),
                          motivo=r.get("erro", ""), sid=r.get("sid", ""))
    except Exception as e:  # noqa: BLE001
        _log.warning("visita_rotinas.avisar: whatsapp falhou (membro %s): %s", membro_id, e)
    try:
        from finance import cockpit as _ck
        tok = _al.novo_token()
        n = _ck.enviar_push(pool, conta_id, membro_id, titulo, corpo, url, token=tok)
        ok = ok or bool(n)
        _al.registrar(pool, conta_id, origem=origem, canal="push", membro_id=membro_id,
                      assunto=titulo, n_leads=1, ok=bool(n),
                      motivo="" if n else "nenhum aparelho com push", token=tok if n else "")
    except Exception as e:  # noqa: BLE001
        _log.warning("visita_rotinas.avisar: push falhou (membro %s): %s", membro_id, e)
    return ok


# ------------------------------------------------------------------ as visitas

def _sql_visitas() -> str:
    """As visitas que as rotinas acompanham, numa conta: a régua única de visita pra
    quem vende festa (`finance.visita.sql_e_visita(festa=True)` — o título diz
    "Visita"), com card, ativa, de 30h atrás até 15 dias pra frente."""
    from finance.visita import sql_e_visita
    return f"""
        select e.id from eventos_agenda e
         where e.conta_id=%(conta)s and e.prospeccao_id is not null and e.status='ativo'
           and {sql_e_visita('e', festa=True)}
           and e.inicio between %(de)s and %(ate)s"""


#: os passos que valem pra UM horário: remarcou, eles recomeçam
_PASSOS_DO_HORARIO = ("vespera_em", "duas_horas_em", "confirmado_em", "pede_remarcar_em",
                      "sem_resposta_em", "veio_1_em", "veio_2_em", "depois_em", "depois_acao",
                      "falta_aviso_em")


def acompanhar(c, conta_id: int, agora: datetime) -> int:
    """Põe no acompanhamento as visitas novas da conta. `da_ia` é a visita que a IA
    marcou (tem linha em `ia_visitas`): a confirmação dela é do `ia_visita`.

    REMARCAR MOVE A MESMA VISITA (`cockpit.remarcar_visita`): o evento é o mesmo e o
    horário é outro. Sem zerar os passos, a véspera já mandada pro horário velho
    calaria a do horário novo — e o "confirmada" de terça valeria pra quinta."""
    n = c.execute(
        """insert into visita_rotinas (evento_id, conta_id, prospeccao_id, da_ia, inicio_visto)
           select e.id, e.conta_id, e.prospeccao_id,
                  exists (select 1 from ia_visitas iv where iv.evento_id = e.id), e.inicio
             from eventos_agenda e
            where e.id in (""" + _sql_visitas() + """)
           on conflict (evento_id) do nothing""",
        {"conta": conta_id, "de": agora - JANELA_PASSADO,
         "ate": agora + timedelta(days=15)}).rowcount
    zera = ", ".join(f"{col}=null" for col in _PASSOS_DO_HORARIO)
    # `remarcado_em`: a véspera do horário novo não sai logo depois de remarcar (o
    # remarcar já avisou o cliente: "sua visita mudou de data")
    c.execute(f"""update visita_rotinas v set inicio_visto=e.inicio, {zera},
                                          envio_falhas=0, envio_falhou_em=null,
                                          remarcado_em=%s
                    from eventos_agenda e
                   where e.id = v.evento_id and e.conta_id = v.conta_id and v.conta_id=%s
                     and v.inicio_visto is not null and v.inicio_visto <> e.inicio""",
              (agora, conta_id))
    # a linha que nasceu da marcação manual ("o cliente já confirmou") não tinha horário
    c.execute("""update visita_rotinas v set inicio_visto=e.inicio
                   from eventos_agenda e
                  where e.id = v.evento_id and e.conta_id = v.conta_id and v.conta_id=%s
                    and v.inicio_visto is null""", (conta_id,))
    return n


_COLS = ("evento_id", "conta_id", "lead", "da_ia", "ao_marcar_em", "vespera_em",
         "duas_horas_em", "confirmado_em", "pede_remarcar_em", "sem_resposta_em",
         "veio_1_em", "veio_2_em", "depois_em", "depois_acao", "falta_aviso_em",
         "inicio", "criado_em", "desfecho", "local", "membro_id", "hora_sugerida",
         "vendedor_id", "quem", "tipo", "convidados", "fechado", "remarcado_em")


def _visitas(c, conta_id: int, agora: datetime) -> list[dict]:
    from finance import funil_regua as fr
    rows = c.execute(
        """select v.evento_id, v.conta_id, v.prospeccao_id,
                  -- CALCULADO A CADA CICLO: a IA pode remarcar uma visita da equipe, e
                  -- a partir daí a confirmação é dela (`ia_visita`), não desta rotina
                  exists (select 1 from ia_visitas iv where iv.evento_id = v.evento_id),
                  v.ao_marcar_em,
                  v.vespera_em, v.duas_horas_em, v.confirmado_em, v.pede_remarcar_em,
                  v.sem_resposta_em, v.veio_1_em, v.veio_2_em, v.depois_em, v.depois_acao,
                  v.falta_aviso_em,
                  e.inicio, e.criado_em, e.desfecho, e.local, e.membro_id,
                  coalesce(e.hora_sugerida, false), p.vendedor_id,
                  coalesce(nullif(p.contato,''), nullif(p.empresa,''), ''),
                  coalesce(p.evento_tipo, ''), p.evento_convidados,
                  -- O CARD SAIU DO JOGO (perdido, ganho, pós) OU ESPERA A DATA: nada
                  -- mais sai pro cliente sobre esta visita (revisão de 27/09/2026 — a
                  -- véspera ia pra lead perdido)
                  coalesce(p.status in """ + fr.sql_encerradas("p") + """
                           or p.status = 'lista_espera', false),
                  v.remarcado_em
             from visita_rotinas v
             join eventos_agenda e on e.id = v.evento_id and e.conta_id = v.conta_id
             left join prospeccao p on p.id = v.prospeccao_id and p.conta_id = v.conta_id
            where v.conta_id=%s and e.status='ativo'
              and v.envio_falhas < %s
              and (v.envio_falhou_em is null or v.envio_falhou_em < %s)
              and e.inicio between %s and %s
            order by e.inicio""",
        (conta_id, MAX_FALHAS, agora - INTERVALO_FALHA, agora - JANELA_PASSADO,
         agora + timedelta(days=15))).fetchall()
    return [dict(zip(_COLS, r)) for r in rows]


def _conversa(c, conta_id: int, lead: int) -> tuple[int | None, bool]:
    """(a conversa de WhatsApp do lead, a IA está nela?). A mais recente."""
    r = c.execute("""select id, coalesce(agente_ativo, false) from conversas
                      where conta_id=%s and prospeccao_id=%s and canal='whatsapp'
                      order by coalesce(ultima_msg_em, criado_em) desc nulls last, id desc
                      limit 1""", (conta_id, lead)).fetchone()
    return (r[0], bool(r[1])) if r else (None, False)


def _nome(m_id, c, conta_id) -> str:
    if not m_id:
        return ""
    r = c.execute("select coalesce(nullif(nome,''), '') from membros where id=%s and conta_id=%s",
                  (m_id, conta_id)).fetchone()
    return ((r[0] if r else "") or "").split(" ")[0]


def _anfitria(c, conta_id: int, conversa_id: int | None) -> int | None:
    """Quem recebe a visita que a IA marcou: a anfitriã da regra do chip."""
    if not conversa_id:
        return None
    try:
        from finance import chip_regra as _cr
        from finance import ia_visita as _iv
        with c.transaction():
            reg = _cr.regra_da_conversa(c, conta_id, conversa_id)
            return (_iv.config(c, reg) or {}).get("anfitria_id") if reg else None
    except Exception:  # noqa: BLE001
        return None


def _dentro(agora: datetime, horas: tuple[int, int]) -> bool:
    h = agora.astimezone(ag.BRT).hour
    return horas[0] <= h < horas[1]


def _escreveu_depois(c, conversa_id: int | None, desde: datetime) -> bool:
    """Alguém da empresa (gente ou IA) escreveu ao cliente depois de `desde`?"""
    if not conversa_id:
        return False
    return bool(c.execute("""select 1 from mensagens where conversa_id=%s and direcao='out'
                                and criado_em > %s limit 1""", (conversa_id, desde)).fetchone())


def _reivindicar(pool, evento_id: int, coluna: str, quando: datetime) -> bool:
    """Carimba o passo com o `agora` do CICLO, não com o `now()` do banco. A resposta
    do cliente é lida "depois da pergunta" (`ler_resposta_em`): com dois relógios, o
    ciclo decidia por um e carimbava pelo outro — e o teste que roda o ciclo num dia
    fixo quebrou no dia em que o relógio real passou dele (30/09/2026)."""
    with pool.connection() as c:
        r = c.execute(f"update visita_rotinas set {coluna}=%s where evento_id=%s "
                      f"and {coluna} is null returning evento_id", (quando, evento_id)).fetchone()
        c.commit()
    return bool(r)


def _acao(pool, evento_id: int, acao: str) -> None:
    """O que o passo "depois da visita" fez: 'lembrete', 'agradecimento' ou
    'ja_falou' (e 'respondido', quando alguém escreve depois do lembrete). É o que o
    selo do card lê."""
    with pool.connection() as c:
        c.execute("update visita_rotinas set depois_acao=%s where evento_id=%s",
                  (acao, evento_id))
        c.commit()


def _ao_cliente(pool, conta_id: int, v: dict, conversa_id: int, coluna: str | None,
                texto: str, agora: datetime | None = None) -> bool:
    """Reivindica (se `coluna`) e manda pelo chip da conversa — o `_passo` do
    `ia_visita`, na tabela desta rotina.

    Passa pelo TETO DO CHIP (`finance.teto_chip`) antes: cheio, não reivindica e tenta
    no próximo ciclo. E o PRAZO ESTOURADO conta como enviado — o provedor pode ter
    aceitado e só demorado a responder; mandar a véspera duas vezes é pior que uma."""
    from finance import agente
    from finance import festa_rotinas as _frt
    from finance import ia_visita as _iv
    from finance import teto_chip as _tc
    agora = agora or datetime.now(timezone.utc)
    with pool.connection() as c:
        pode = _tc.pode(c, conta_id, conversa_id, agora)
        c.commit()
    if not pode:
        return False
    if coluna and not _reivindicar(pool, v["evento_id"], coluna, agora):
        return False
    with pool.connection() as c:
        try:
            res = _iv._mandar(c, conta_id, conversa_id, texto)
        except Exception as e:  # noqa: BLE001
            _log.warning("visita_rotinas: envio falhou (evento %s): %s", v["evento_id"], e)
            c.rollback()
            res = {"ok": False, "erro": str(e)}
        if res.get("ok") or _frt.talvez_saiu(res):
            _tc.registrar(c, conta_id, conversa_id, "visita", agora)
            try:
                if res.get("ok"):
                    agente._add_bot_msg(c, conversa_id, "whatsapp", texto, res.get("sid"))
                c.execute("update visita_rotinas set envio_falhas=0, envio_falhou_em=null "
                          "where evento_id=%s", (v["evento_id"],))
                c.commit()
            except Exception:  # noqa: BLE001 — saiu; só não ficou gravado
                c.rollback()
                _log.warning("visita_rotinas: saiu mas não gravou (evento %s)",
                             v["evento_id"], exc_info=True)
            return True
        sets = f"{coluna}=null, " if coluna else ""
        c.execute(f"""update visita_rotinas set {sets}envio_falhas=envio_falhas+1,
                                               envio_falhou_em=now()
                       where evento_id=%s""", (v["evento_id"],))
        c.commit()
        return False


# ------------------------------------------------------------------ a resposta 1/2

def ler_resposta(c, conta_id: int, v: dict, conversa_id: int) -> str | None:
    """Só o que o cliente respondeu (`ler_resposta_em` diz também QUANDO)."""
    return ler_resposta_em(c, conta_id, v, conversa_id)[0]


def ler_resposta_em(c, conta_id: int, v: dict, conversa_id: int) -> tuple[str | None, datetime | None]:
    """O cliente respondeu ao "1 confirma, 2 remarca"? Olha as mensagens dele desde a
    PRIMEIRA pergunta, na ordem: a primeira que for resposta decide. Mesmas regras da
    confirmação da clínica (`clinica_agenda.ler_respostas`): "15h" não é "1".

    DEPOIS QUE A EQUIPE FALA, A LEITURA PARA. O "1" que vem depois de "prefere o
    salão 1 ou 2?" responde o vendedor, não a véspera — antes, o número puro ainda
    valia e confirmava (ou pedia remarcar) a visita sozinho (revisão de 27/09/2026).
    Com a conversa nas mãos da equipe, quem confirma é ela, pelo app.
    Devolve (o que: 'confirmou', 'confirmou_e_mais', 'remarcar' ou None, quando o
    cliente escreveu)."""
    from finance.clinica_agenda import _RE_REMARCAR, _RE_SIM, _SO_NUMERO
    perguntas = [x for x in (v["vespera_em"], v["duas_horas_em"]) if x]
    if not perguntas or v["confirmado_em"] or v["pede_remarcar_em"]:
        return None, None
    desde = min(perguntas)
    for texto, depois_de_outra, quando in c.execute(
            """select m.texto,
                      exists (select 1 from mensagens o
                               where o.conversa_id = m.conversa_id and o.direcao = 'out'
                                 and coalesce(o.autor, '') <> 'bot'
                                 and o.criado_em > %s + interval '2 minutes'
                                 and o.criado_em < m.criado_em),
                      m.criado_em
                 from mensagens m join conversas cv on cv.id = m.conversa_id
                where m.conversa_id=%s and cv.conta_id=%s and m.direcao='in' and m.criado_em > %s
                order by m.criado_em, m.id limit 20""",
            (desde, conversa_id, conta_id, desde)).fetchall():
        t = (texto or "").strip()
        if depois_de_outra:
            break
        if _RE_SIM.search(t):
            curto = bool(_SO_NUMERO.match(t)) or (len(t) <= 25 and "?" not in t)
            return ("confirmou" if curto else "confirmou_e_mais"), quando
        if _RE_REMARCAR.search(t):
            return "remarcar", quando
    return None, None


# ------------------------------------------------------------------ o relógio

def _uma_visita(pool, conta_id: int, cfg: dict, v: dict, agora: datetime,
                esp: dict, out: dict) -> None:
    from finance.ia_visita import fmt, texto_duas_horas, texto_vespera, _momento_vespera
    from finance.voltar_a_chamar import primeiro_nome
    from finance import chip_regra as _cr
    ini = v["inicio"]
    with pool.connection() as c:
        conversa_id, ia_na_conversa = _conversa(c, conta_id, v["lead"])
        # O MEMBRO DA IA NÃO RECEBE AVISO: o WhatsApp dele é o do dono da conta (o
        # "ZAQ SDR"), e "a visita das 15h veio?" ia pro celular errado. Quem responde
        # por ela é gente: a anfitriã, quem está na agenda ou o vendedor — o primeiro
        # que não for a IA; nenhum, e o aviso não sai (revisão de 27/09/2026).
        ia = _cr.membros_ia(c, conta_id)
        gente = [m for m in ((_anfitria(c, conta_id, conversa_id) if v["da_ia"] else None),
                             v["membro_id"], v["vendedor_id"]) if m and m not in ia]
        # quem RECEBE a visita: na da IA, a anfitriã; na da equipe, quem está na agenda
        recebe = gente[0] if gente else None
        dono = next((m for m in (v["vendedor_id"], v["membro_id"]) if m and m not in ia),
                    None) or recebe
        nome_recebe = _nome(recebe, c, conta_id)
        nome_dono = _nome(dono, c, conta_id)
        # PEDIU PRA PARAR: a última mensagem dele diz "não quero mais", "pare"… — a
        # rotina não escreve mais pra ele (a equipe continua sabendo da visita)
        parou = False
        if conversa_id:
            r = c.execute("""select texto from mensagens where conversa_id=%s and direcao='in'
                              order by criado_em desc, id desc limit 1""",
                          (conversa_id,)).fetchone()
            from finance.resgate import RE_PARAR
            parou = bool(r and RE_PARAR.search(r[0] or ""))
        c.commit()
    if v["fechado"] and ini > agora:
        return                      # perdido, ganho ou na espera: a visita não se cobra

    def _mandar_cli(coluna, texto):
        return _ao_cliente(pool, conta_id, v, conversa_id, coluna, texto, agora)
    nome = primeiro_nome(v["quem"])
    nome = nome[:1].upper() + nome[1:].lower() if nome else ""
    quem = nome or "O cliente"
    link = f"/cockpit/lead/{v['lead']}"
    # a conversa da equipe: a confirmação é desta rotina; a da IA é do ia_visita
    da_equipe = (not v["da_ia"]) and conversa_id and not ia_na_conversa
    cliente_ok = (_dentro(agora, HORAS_CLIENTE) and out["enviadas"] < TETO_CICLO
                  and not parou and not v["fechado"])
    equipe_ok = _dentro(agora, HORAS_EQUIPE)

    # ---- antes da visita: confirmar (só a da equipe) --------------------------
    if cfg["confirmar"] and da_equipe and ini > agora and not v["hora_sugerida"]:
        with pool.connection() as c:
            resp, resp_em = ler_resposta_em(c, conta_id, v, conversa_id)
            c.commit()
        # A RESPOSTA A QUEM ACABOU DE ESCREVER NÃO ESPERA A JANELA (27/09/2026): a
        # janela das 8h às 20h é da mensagem que o sistema INICIA. A cliente que
        # respondeu "1" às 20h01 ficou sem o "Confirmadíssimo" — a confirmação foi
        # gravada, e a resposta nunca mais saía. Respondida há pouco, sai na hora.
        resposta_ok = (out["enviadas"] < TETO_CICLO and not parou and not v["fechado"]
                       and (cliente_ok or bool(resp_em and agora - resp_em <= RESPOSTA_NA_HORA)))
        if resp in ("confirmou", "confirmou_e_mais"):
            if _reivindicar(pool, v["evento_id"], "confirmado_em", agora):
                out["confirmadas"] += 1
                # o "sim, qual o endereço?" fica pro vendedor: responder só o "sim"
                # por cima de uma pergunta seria a empresa ignorando o cliente
                if resp == "confirmou" and resposta_ok:
                    if _mandar_cli(None,
                                   f"Confirmadíssimo! 🎉 Te esperamos {fmt(ini)}."):
                        out["enviadas"] += 1
            return
        if resp == "remarcar":
            if _reivindicar(pool, v["evento_id"], "pede_remarcar_em", agora):
                out["remarcar"] += 1
                if resposta_ok and _mandar_cli(None,
                                               texto_remarcar(nome_dono)):
                    out["enviadas"] += 1
                avisar(pool, conta_id, dono, f"🔁 {quem} pediu pra remarcar a visita",
                       f"A visita era {fmt(ini)}. Chame pra achar outro horário.", link)
            return
        if v["pede_remarcar_em"]:
            return
        marcada_em = v["criado_em"] or ini
        # o horário ATUAL foi combinado quando? na criação ou na última remarcação —
        # é o que diz se a véspera e o "2h antes" ainda fazem sentido
        combinada_em = max(marcada_em, v["remarcado_em"] or marcada_em)
        if (not v["ao_marcar_em"] and cliente_ok and cfg["ligado_em"]
                and marcada_em >= cfg["ligado_em"] and agora - marcada_em <= AO_MARCAR_JANELA
                and ini - agora > AO_MARCAR_ANTES):
            if agora - marcada_em < AO_MARCAR_ESPERA:
                return                  # o aviso de quem marcou pode estar saindo agora
            # MARCAR PELO APP JÁ AVISA O CLIENTE ("Sua visita ao … está marcada", com o
            # convite .ics — `cockpit.agendar_visita`). Se alguém já escreveu desde que a
            # visita nasceu, o "ao marcar" fica feito sem sair: seria a mesma notícia duas
            # vezes.
            with pool.connection() as c:
                ja_avisado = _escreveu_depois(c, conversa_id, marcada_em - timedelta(minutes=1))
                c.commit()
            if ja_avisado:
                _reivindicar(pool, v["evento_id"], "ao_marcar_em", agora)
                return
            if _mandar_cli("ao_marcar_em",
                           texto_ao_marcar(ini, nome, esp["nome"], nome_recebe,
                                           v["local"] or esp["endereco"] or "", agora)):
                out["ao_marcar"] += 1
                out["enviadas"] += 1
            return
        momento = _momento_vespera(ini, False)
        if (not v["vespera_em"] and not v["confirmado_em"] and agora >= momento
                and combinada_em < momento and ini - agora > timedelta(hours=2, minutes=30)
                and cliente_ok):
            if _mandar_cli("vespera_em",
                           texto_vespera(ini, nome, agora, esp["nome"])):
                out["vesperas"] += 1
                out["enviadas"] += 1
            return
        if (not v["duas_horas_em"] and ini - agora <= timedelta(hours=2)
                and combinada_em < ini - timedelta(hours=2) and cliente_ok):
            if _mandar_cli("duas_horas_em",
                           texto_duas_horas(ini, esp["nome"],
                                            v["local"] or esp["endereco"] or esp["nome"],
                                            bool(v["confirmado_em"]))):
                out["duas_horas"] += 1
                out["enviadas"] += 1
            return
        if ((v["vespera_em"] or v["duas_horas_em"]) and not v["confirmado_em"]
                and not v["sem_resposta_em"] and ini - agora <= timedelta(minutes=90)
                and equipe_ok):
            if _reivindicar(pool, v["evento_id"], "sem_resposta_em", agora):
                avisar(pool, conta_id, recebe, "⏰ Visita sem confirmação",
                       f"{quem} não confirmou a visita das {_hora(ini)}. Continua marcada.", link)
                out["sem_resposta"] += 1
        return

    if ini > agora:
        return

    # ---- depois do horário: "veio?" (toda visita) ------------------------------
    if v["desfecho"] is None:
        if not cfg["perguntar_veio"] or not equipe_ok or not recebe:
            return
        pergunta = f"📍 Visita das {_hora(ini)}: {nome or 'o cliente'} veio?"
        corpo = "Responda no app: ✅ Apareceu ou ❌ Não apareceu."
        loc = agora.astimezone(ag.BRT)
        mesmo_dia = ini.astimezone(ag.BRT).date() == loc.date()
        if not v["veio_1_em"] and agora >= ini + VEIO_DEPOIS:
            if _reivindicar(pool, v["evento_id"], "veio_1_em", agora):
                avisar(pool, conta_id, recebe, pergunta, corpo, "/cockpit/agenda")
                out["veio"] += 1
            return
        segunda = datetime(loc.year, loc.month, loc.day, VEIO_HORA_2, tzinfo=ag.BRT)
        if (v["veio_1_em"] and not v["veio_2_em"] and mesmo_dia and agora >= segunda
                and v["veio_1_em"] < segunda - timedelta(minutes=30)):
            if _reivindicar(pool, v["evento_id"], "veio_2_em", agora):
                avisar(pool, conta_id, recebe, pergunta, corpo, "/cockpit/agenda")
                out["veio"] += 1
        return

    if not cfg["depois_visita"]:
        return

    # ---- faltou: o dono do card fica sabendo (se não foi ele quem recebeu) -----
    if v["desfecho"] == "nao_realizado":
        if v["da_ia"] or v["falta_aviso_em"] or not equipe_ok:
            return
        if _reivindicar(pool, v["evento_id"], "falta_aviso_em", agora) and dono and dono != recebe:
            avisar(pool, conta_id, dono, f"❌ {quem} faltou à visita",
                   "O card voltou pra Qualificado. Chame pra remarcar.", link)
            out["faltou"] += 1
        return

    # ---- veio: falar com o cliente enquanto a festa está fresca ----------------
    if v["desfecho"] == "realizado" and v["depois_acao"] == "lembrete":
        # o lembrete saiu; alguém escreveu depois? o selo "falar com o cliente" sai
        with pool.connection() as c:
            if _escreveu_depois(c, conversa_id, ini):
                c.execute("update visita_rotinas set depois_acao='respondido' "
                          "where evento_id=%s and conta_id=%s", (v["evento_id"], conta_id))
            c.commit()
        return
    if v["desfecho"] != "realizado" or v["depois_em"] or agora < ini + DEPOIS_DEPOIS:
        return
    with pool.connection() as c:
        ja_falou = _escreveu_depois(c, conversa_id, ini)
        c.commit()
    if ja_falou:
        # nada a fazer, e fica feito — sem selo: o selo é só do lembrete que saiu
        if _reivindicar(pool, v["evento_id"], "depois_em", agora):
            _acao(pool, v["evento_id"], "ja_falou")
        return
    if v["da_ia"]:
        if conversa_id and ia_na_conversa and cliente_ok:
            if _mandar_cli("depois_em",
                           texto_agradecimento(nome, ini, agora, v["tipo"], v["convidados"])):
                _acao(pool, v["evento_id"], "agradecimento")
                out["agradecimentos"] += 1
                out["enviadas"] += 1
        return
    if equipe_ok and _reivindicar(pool, v["evento_id"], "depois_em", agora):
        _acao(pool, v["evento_id"], "lembrete")
        avisar(pool, conta_id, dono, f"💬 Fale com {nome or 'o cliente'}",
               f"A visita foi às {_hora(ini)} e ninguém escreveu depois. Mande a proposta "
               "enquanto a festa está fresca.", link)
        out["lembretes"] += 1


def rodar(pool, agora: datetime | None = None) -> dict:
    """Um ciclo do relógio, em toda conta que ligou alguma rotina. Janelas, e não
    minutos exatos: o poller anda de ~2 em ~2 minutos e atrasa."""
    agora = agora or datetime.now(timezone.utc)
    out = {"ao_marcar": 0, "vesperas": 0, "duas_horas": 0, "confirmadas": 0, "remarcar": 0,
           "sem_resposta": 0, "veio": 0, "faltou": 0, "lembretes": 0, "agradecimentos": 0,
           "enviadas": 0}
    with pool.connection() as lk:
        try:
            if not lk.execute("select pg_try_advisory_lock(%s)", (_LOCK,)).fetchone()[0]:
                return out
        except Exception:  # noqa: BLE001
            return out
        try:
            try:
                with pool.connection() as c:
                    contas = [r[0] for r in c.execute(
                        """select conta_id from visita_rotinas_config
                            where confirmar or perguntar_veio or depois_visita
                            order by conta_id""").fetchall()]
            except Exception:  # noqa: BLE001 — banco sem a 414
                return out
            from finance.cockpit import endereco_empresa
            for conta_id in contas:
                try:
                    with pool.connection() as c:
                        cfg = config(c, conta_id)
                        acompanhar(c, conta_id, agora)
                        c.commit()
                        visitas = _visitas(c, conta_id, agora)
                    esp = endereco_empresa(pool, conta_id)
                except Exception as e:  # noqa: BLE001
                    _log.warning("visita_rotinas.rodar: conta %s: %s", conta_id, e)
                    continue
                for v in visitas:
                    try:
                        _uma_visita(pool, conta_id, cfg, v, agora, esp, out)
                    except Exception as e:  # noqa: BLE001
                        _log.warning("visita_rotinas.rodar: evento %s: %s", v["evento_id"], e)
        finally:
            lk.execute("select pg_advisory_unlock(%s)", (_LOCK,))
    return out


# ------------------------------------------------------------------ o card

def selos(c, conta_id: int, lead_ids: list[int], agora: datetime | None = None) -> dict:
    """O selo da visita no card, numa consulta só: {lead: (texto, classe)}.

        confirmada ✓        o cliente respondeu 1 (a da IA também, pelo `ia_visitas`)
        pediu pra remarcar  respondeu 2
        não confirmou       perguntamos e ninguém respondeu até 1h30 antes
        falar com o cliente veio, o lembrete saiu, e ainda ninguém escreveu
        agradecida ✓        veio, e a IA agradeceu (visita da IA)
    Tolerante: sem as tabelas, nenhum selo. `agora` é o do quadro (o relógio real);
    o teste passa o do cenário — um relógio só pro SQL e pro Python."""
    if not lead_ids:
        return {}
    agora = agora or datetime.now(timezone.utc)
    try:
        with c.transaction():
            rows = c.execute(
                """select distinct on (v.prospeccao_id) v.prospeccao_id,
                          coalesce(v.confirmado_em, iv.confirmado_em) is not null,
                          coalesce(v.pede_remarcar_em, iv.pede_remarcar_em) is not null,
                          coalesce(v.sem_resposta_em, iv.sem_resposta_em) is not null,
                          e.desfecho,
                          -- o lembrete saiu e ainda ninguém escreveu: quem apaga é o relógio
                          -- (`_uma_visita` vira 'lembrete' em 'respondido'). Aqui não se lê
                          -- conversa: o quadro faz UMA consulta de conversas pro board
                          -- inteiro (tests/test_kanban_chat.py), e o selo não é motivo pra
                          -- uma segunda
                          v.depois_acao = 'lembrete' and e.inicio > %s - interval '2 days',
                          v.depois_acao = 'agradecimento', e.inicio
                     from visita_rotinas v
                     join eventos_agenda e on e.id = v.evento_id and e.conta_id = v.conta_id
                     left join ia_visitas iv on iv.evento_id = v.evento_id
                    where v.conta_id=%s and v.prospeccao_id = any(%s) and e.status='ativo'
                    order by v.prospeccao_id, e.inicio desc""",
                (agora, conta_id, list(lead_ids))).fetchall()
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for lead, conf, remarcar, sem_resp, desfecho, falar, agradeceu, ini in rows:
        # o desfecho manda: com ele marcado, a visita aconteceu (ou não), seja qual for
        # o relógio
        if desfecho == "realizado":
            if falar:
                out[lead] = ("falar com o cliente", "at")
            elif agradeceu:
                out[lead] = ("agradecida ✓", "ia")
        elif desfecho is None and ini and ini > agora:
            if remarcar:
                out[lead] = ("pediu pra remarcar", "bad")
            elif conf:
                out[lead] = ("confirmada ✓", "ok")
            elif sem_resp:
                out[lead] = ("não confirmou", "at")
    return out


def marcar_confirmada(c, conta_id: int, evento_id: int, membro_id: int | None = None,
                      gestao: bool = False) -> bool:
    """O vendedor já confirmou pelo celular dele: a visita fica confirmada e as
    mensagens de confirmação param. Só visita futura, da conta, e — sem ser gestão
    — de quem recebe ou do dono do card."""
    from finance.visita import sql_e_visita
    r = c.execute(
        f"""select e.id, e.prospeccao_id, e.membro_id, p.vendedor_id
              from eventos_agenda e
              left join prospeccao p on p.id = e.prospeccao_id and p.conta_id = e.conta_id
             where e.id=%s and e.conta_id=%s and e.status='ativo' and e.inicio > now()
               and e.prospeccao_id is not null and {sql_e_visita('e', festa=True)}""",
        (evento_id, conta_id)).fetchone()
    if not r:
        return False
    if not gestao and membro_id not in (r[2], r[3]):
        return False
    c.execute("""insert into visita_rotinas (evento_id, conta_id, prospeccao_id, confirmado_em)
                 values (%s,%s,%s,now())
                 on conflict (evento_id) do update
                 set confirmado_em = coalesce(visita_rotinas.confirmado_em, now())""",
              (evento_id, conta_id, r[1]))
    return True
