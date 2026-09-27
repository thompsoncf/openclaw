"""O RESGATE DA IA: o lead parado há 7 dias passa, no 8º, pro membro IA (migração 396).

Aprovado pelo dono em 27/09/2026 (docs/mockups/resgate_ia.html). As palavras dele:

    "so puxa apos o 7 dias ou seja no oitavo dia ele so fica com lead com uma
     condicao abrir a ficha e justificar por qualquer motivo no historico de
     anotacoes que ja existe"

    "se ele marcou a visita a IA nao se entromete ai tem que fazer o follow do
     vendedor"

O RELÓGIO. O prazo corre da última coisa que a EMPRESA fez pelo lead: a última
mensagem nossa (painel, celular ou IA), o registro que o PRÓPRIO vendedor fez no
histórico da ficha (`prospeccao_atividades`, com texto — é a justificativa), ou o
nascimento do lead. Registro automático do sistema (membro nulo) e registro de outra
pessoa não seguram.

O LEAD QUE O VENDEDOR ESQUENTOU. Visita marcada pra frente: a IA não se mete, nunca.
Visita que já aconteceu ou orçamento ligado ao lead: o prazo é `aquecido_dias` (14,
por padrão) em vez de 7, e conta também da visita e do orçamento. Nulo = nunca vai.
Medido em 26/09 na Prime (só leitura): dos 309 da fila, 26 tinham visita ou
orçamento — e 16 desses estavam em silêncio total havia mais de 14 dias, mediana de
19. "Nunca" deixaria justamente os leads mais quentes morrerem parados; a escolha
fica no cartão.

O CHIP PRINCIPAL É QR (CLAUDE.md §0). 317 dos 326 parados estão nele, e disparo em
massa é o jeito mais rápido de o WhatsApp banir o número de onde vem a receita. Por
isso: um envio por passada do poller, no máximo `teto_dia` por dia (contando tudo que
o resgate manda pra cliente e as prévias do Ensaio, que andam no mesmo ritmo), um a
cada 20–30 minutos, só no horário e nos dias do cartão, e sempre pelo chip onde a
conversa JÁ está. O freio: chip fora do ar segura o envio (sem pausar); 3 clientes
pedindo pra parar no mesmo dia, ou 3 envios que falharam, PAUSAM o resgate e avisam o
supervisor.

OS TRÊS MODOS
    off      nada roda.
    ensaio   nada vai pra cliente e nenhum lead muda de dono: a IA escreve a retomada
             e manda a PRÉVIA pro supervisor, no mesmo ritmo do ligado.
    ligado   o lead passa pro membro IA (a ficha ganha uma nota do sistema dizendo de
             quem era) e a retomada sai pro cliente. Daí em diante quem responde é a
             IA da regra do membro — preço liberado, visita, orçamento com conferência.

O AVISO AO VENDEDOR (só no ligado). Dois dias antes do prazo, um aviso por dia por
vendedor, com a lista dos leads dele que vão pro resgate. E o lead só passa depois de
48h do aviso: é o que dá ao vendedor a chance de segurar — inclusive no dia em que o
resgate liga, quando a fila inteira já está vencida.

NADA AQUI É IRREVERSÍVEL. Passar o lead é trocar o `vendedor_id` e anotar; a ficha, a
conversa e o histórico ficam inteiros, e o gestor devolve o lead a quem quiser.
Tolerante: banco sem a 396 → o resgate não existe, e nada em volta quebra.
"""
from __future__ import annotations

from finance.raio_x_perfil import palavra as _palavra  # o nome de quem compra ("paciente" na clínica)
import json
import logging
import re
from datetime import datetime, timedelta, timezone

_log = logging.getLogger("openclaw.resgate")

_BRT = timezone(timedelta(hours=-3))

#: vizinho das travas do relógio da visita (771171) e do sinal (771173)
_LOCK = 771180

MODOS = ("off", "ensaio", "ligado")

PADRAO = {"modo": "off", "membro_id": None, "dias": 7, "aquecido_dias": 14, "teto_dia": 20,
          "hora_ini": 9, "hora_fim": 19, "dias_semana": [0, 1, 2, 3, 4, 5],
          "supervisor_whatsapp": "", "incluir_perdidos": True, "aviso_vendedor": True,
          "pausado_em": None, "pausado_motivo": "", "ligado_em": None}

_COLS = ("modo", "membro_id", "dias", "aquecido_dias", "teto_dia", "hora_ini", "hora_fim",
         "dias_semana", "supervisor_whatsapp", "incluir_perdidos", "aviso_vendedor",
         "pausado_em", "pausado_motivo", "ligado_em")

#: o espaçamento entre dois envios do resgate: 20 minutos + até 10 de variação por
#: lead (o mesmo lead sempre dá o mesmo número — sem acaso, o teste repete)
ESPACO_MIN = 20
#: o aviso ao vendedor sai `AVISO_DIAS` antes do prazo, e o lead só passa `AVISO_H`
#: depois do aviso
AVISO_DIAS = 2
AVISO_H = 48
#: o freio: quantos pedidos de parar / envios falhos no mesmo dia pausam o resgate
FREIO_PARAR = 3
FREIO_FALHAS = 3
#: festa em menos de tantos dias não é resgatada (é do supervisor decidir)
FESTA_MIN_DIAS = 3
#: o lead cujo envio falhou fica de fora por tantos dias
FALHA_DIAS = 7
#: OS TOQUES (etapa 2, a regra de sempre do dono: "3 toques e o 4º fica como
#: perdido"). Contados da retomada: o 2º toque no dia 3, o 3º no dia 7 (e nunca
#: menos de 3 dias depois do 2º), e sem resposta no dia 10 o lead vira perdido.
TOQUE_2_DIAS = 3
TOQUE_3_DIAS = 7
TOQUE_INTERVALO_DIAS = 3
PERDIDO_DEPOIS_DIAS = 3
#: tudo que o resgate manda pra CLIENTE (e a prévia, que anda no mesmo ritmo): é o
#: que conta no teto do dia e no espaçamento
ENVIOS = ("previa", "retomada", "toque")

#: "pare", "não quero mais", "me tira da lista"… — o cliente que pediu pra parar
#: nunca mais é chamado pelo resgate. Palavra INTEIRA e frase inteira: "para" (a
#: preposição), "parece", "parabéns", "quando chega" e "não quero sair tarde" são
#: conversa, não pedido de parar — e cada falso "pare" gasta um degrau do freio.
RE_PARAR = re.compile(
    r"(?<!\w)(pare|parem|parar de|para de (me )?(mandar|chamar|enviar)|"
    r"n[aã]o quero mais|n[aã]o tenho (mais )?interesse|sem interesse|"
    r"n[aã]o me (mande|mandem|chame|chamem|ligue|procure)|"
    r"me (tira|tire|remove|remova|exclui|exclua) d[aeo]|sai[r]? da lista|"
    r"desisti|j[aá] (fechei|contratei)|vou bloquear|bloqueado)(?!\w)",
    re.I)

FAIXAS = {1: "Cliente esperando resposta", 2: "Festa com data por vir",
          3: "Aberto", 4: "Perdido"}


def _so_digitos(v) -> str:
    return re.sub(r"\D", "", str(v or ""))


def numero_do_supervisor(v) -> str:
    """O número como o WhatsApp quer: só dígitos, com o 55 na frente quando veio sem.
    Vazio quando não parece telefone."""
    d = _so_digitos(v)
    if len(d) in (10, 11):
        d = "55" + d
    return d if 12 <= len(d) <= 13 else ""


def _fim8(v) -> str:
    return _so_digitos(v)[-8:]


def chave_do_numero(v) -> str:
    """DDD + os 8 últimos dígitos: o mesmo número com ou sem o nono dígito, e com ou
    sem o 55 — mas nunca o de outro DDD (8 dígitos sozinhos colidem entre cidades)."""
    d = _so_digitos(v)
    if d.startswith("55") and len(d) in (12, 13):
        d = d[2:]
    if len(d) in (10, 11):
        return d[:2] + d[-8:]
    return ""


# ══════════════════════════════════════════════════════════════════ a config

def config(c, conta_id: int) -> dict:
    """A config do resgate desta empresa (o padrão, se não tem linha ou a tabela)."""
    try:
        with c.transaction():
            r = c.execute(f"select {', '.join(_COLS)} from resgate_config where conta_id=%s",
                          (conta_id,)).fetchone()
    except Exception:  # noqa: BLE001 — banco sem a 396
        r = None
    if not r:
        return dict(PADRAO, dias_semana=list(PADRAO["dias_semana"]))
    d = dict(zip(_COLS, r))
    d["dias_semana"] = [int(x) for x in (d["dias_semana"] or [])]
    d["supervisor_whatsapp"] = d["supervisor_whatsapp"] or ""
    d["pausado_motivo"] = d["pausado_motivo"] or ""
    return d


def _membros_ia(c, conta_id: int) -> list[int]:
    """Os membros que são dono de uma regra por número com a IA ligada — os únicos que
    podem receber o resgate (a IA que responde o cliente é a da regra deles)."""
    try:
        with c.transaction():
            return [int(x[0]) for x in c.execute(
                """select distinct membro_id from chip_regra
                    where conta_id=%s and membro_id is not null""", (conta_id,)).fetchall()]
    except Exception:  # noqa: BLE001
        return []


def regra_do_membro(c, conta_id: int, membro_id) -> dict | None:
    """A regra por número (ativa, com IA) do membro IA — é dela que o resgate tira o
    jeito de falar, e é ela que responde o cliente quando ele volta."""
    if not membro_id:
        return None
    from finance import chip_regra as _cr
    try:
        with c.transaction():
            r = c.execute(f"select {_cr._COLS} from chip_regra where conta_id=%s and membro_id=%s "
                          "order by (ativa and ia_ligada) desc, id limit 1",
                          (conta_id, membro_id)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    return _cr._dict(r) if r else None


def salvar(c, conta_id: int, f: dict) -> dict:
    """Grava a config vinda do cartão. Devolve {ok, erro?}.

    Conferido contra a conta: o membro IA tem que ser desta empresa, ativo e dono de
    uma regra por número. Pra LIGAR, a regra dele tem que estar ativa com a IA ligada
    (é ela que responde o cliente) e o supervisor tem que ter número. O Ensaio só
    precisa do supervisor — nada vai pra cliente."""
    modo = f.get("modo") if f.get("modo") in MODOS else "off"
    try:
        mid = int(f.get("membro_id") or 0) or None
    except (TypeError, ValueError):
        mid = None
    if mid and not c.execute("select 1 from membros where id=%s and conta_id=%s and ativo",
                             (mid, conta_id)).fetchone():
        return {"ok": False, "erro": "Escolha um membro desta empresa."}
    if mid and mid not in _membros_ia(c, conta_id):
        return {"ok": False, "erro": "O resgate vai pra quem é dono de uma regra por número."}

    def _int(k, lo, hi, padrao):
        try:
            return max(lo, min(hi, int(f.get(k))))
        except (TypeError, ValueError):
            return padrao

    dias = _int("dias", 3, 60, 7)
    aq = f.get("aquecido_dias")
    aquecido = None if str(aq or "").strip().lower() in ("", "nunca", "0") else _int(
        "aquecido_dias", dias, 90, 14)
    teto = _int("teto_dia", 1, 60, 20)
    ini, fim = _int("hora_ini", 0, 23, 9), _int("hora_fim", 1, 24, 19)
    if ini >= fim:
        return {"ok": False, "erro": "O horário do resgate tem que começar antes de terminar."}
    semana = sorted({int(d) for d in (f.get("dias_semana") or [])
                     if str(d).isdigit() and 0 <= int(d) <= 6})
    if not semana:
        return {"ok": False, "erro": "Escolha ao menos um dia da semana."}
    sup = numero_do_supervisor(f.get("supervisor_whatsapp"))
    if (f.get("supervisor_whatsapp") or "").strip() and not sup:
        return {"ok": False, "erro": "O número do supervisor não parece um WhatsApp (DDD + número)."}
    if modo != "off" and not mid:
        return {"ok": False, "erro": f"Escolha pra quem vão os {_palavra('leads', conta_id)} do resgate."}
    if modo != "off" and not sup:
        return {"ok": False, "erro": "Ponha o WhatsApp do supervisor: é pra ele que vão as prévias e os avisos."}
    if modo == "ligado":
        r = regra_do_membro(c, conta_id, mid)
        if not (r and r.get("ativa") and r.get("ia_ligada")):
            return {"ok": False, "erro": "Pra ligar, a regra por número desse membro tem que estar "
                                         "ligada e com a IA atendendo — é ela que responde o cliente."}
    antes = config(c, conta_id)
    c.execute(
        """insert into resgate_config (conta_id, modo, membro_id, dias, aquecido_dias, teto_dia,
                                       hora_ini, hora_fim, dias_semana, supervisor_whatsapp,
                                       incluir_perdidos, aviso_vendedor, atualizado_em)
           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,now())
           on conflict (conta_id) do update set
             modo=excluded.modo, membro_id=excluded.membro_id, dias=excluded.dias,
             aquecido_dias=excluded.aquecido_dias, teto_dia=excluded.teto_dia,
             hora_ini=excluded.hora_ini, hora_fim=excluded.hora_fim,
             dias_semana=excluded.dias_semana, supervisor_whatsapp=excluded.supervisor_whatsapp,
             incluir_perdidos=excluded.incluir_perdidos, aviso_vendedor=excluded.aviso_vendedor,
             atualizado_em=now()""",
        (conta_id, modo, mid, dias, aquecido, teto, ini, fim, semana, sup or None,
         bool(f.get("incluir_perdidos")), bool(f.get("aviso_vendedor"))))
    if modo == "ligado" and antes.get("modo") != "ligado":
        c.execute("update resgate_config set ligado_em=now() where conta_id=%s", (conta_id,))
    if f.get("retomar") or modo != antes.get("modo"):
        # tirar o freio é decisão de gente: salvar com "retomar" (ou mudar de modo)
        c.execute("update resgate_config set pausado_em=null, pausado_motivo=null where conta_id=%s",
                  (conta_id,))
    return {"ok": True}


def pode_rodar_agora(cfg: dict, agora: datetime | None = None) -> bool:
    """Dentro dos dias e do horário do resgate (hora de Brasília)? Vale pra PUXAR
    conversa — quem escreve pra IA é respondido na hora, com o horário da regra."""
    agora = (agora or datetime.now(timezone.utc)).astimezone(_BRT)
    if agora.weekday() not in (cfg.get("dias_semana") or []):
        return False
    return int(cfg.get("hora_ini") or 0) <= agora.hour < int(cfg.get("hora_fim") or 24)


# ══════════════════════════════════════════════════════════════════ a fila

def _perfil_eventos(c, conta_id: int) -> bool:
    try:
        from finance import raio_x_perfil as _rxp
        with c.transaction():
            r = c.execute("""select coalesce(n.slug,'') from contas ct
                               left join nichos n on n.id = ct.nicho_id where ct.id=%s""",
                          (conta_id,)).fetchone()
        return _rxp.perfil_por_nicho(r[0] if r else "") == "eventos"
    except Exception:  # noqa: BLE001
        return False


def _sql_leads(festa: bool) -> str:
    from finance import funil_regua as fr
    from finance import visita as vis
    return f"""
    with l as (
      select p.id, p.status, p.vendedor_id, p.evento_em, p.criado_em, p.orcamento_id,
             coalesce(nullif(p.contato,''), nullif(p.empresa,''), 'Cliente') quem,
             nullif(p.whatsapp,'') numero, nullif(p.telefone,'') telefone
        from prospeccao p
       where p.conta_id = %(conta)s and p.estagio = 'lead'
         and p.vendedor_id is distinct from %(ia)s
         and (p.vendedor_id is null or p.vendedor_id not in (
               select cr.membro_id from chip_regra cr
                where cr.conta_id = %(conta)s and cr.membro_id is not null))
         and not exists (select 1 from chip_regra_leads cl where cl.prospeccao_id = p.id)
         and p.status not in {fr.sql_fechadas('p')}
         and (%(perdidos)s or p.status <> 'perdido')
         and (%(lead)s::bigint is null or p.id = %(lead)s::bigint)
         and not exists (select 1 from resgate_leads r
                          where r.prospeccao_id = p.id and (r.ativo or r.opt_out))),
    msg as (
      select cv.prospeccao_id lead,
             max(m.criado_em) filter (where m.direcao = 'out') ult_out,
             max(m.criado_em) filter (where m.direcao = 'in')  ult_in
        from conversas cv join l on l.id = cv.prospeccao_id
        join mensagens m on m.conversa_id = cv.id
       where cv.conta_id = %(conta)s
       group by cv.prospeccao_id),
    cvw as (
      select distinct on (cv.prospeccao_id) cv.prospeccao_id lead, cv.id, cv.chip_id, cv.contato_ref
        from conversas cv join l on l.id = cv.prospeccao_id
       where cv.conta_id = %(conta)s and cv.canal = 'whatsapp'
       order by cv.prospeccao_id, cv.ultima_msg_em desc nulls last, cv.id desc),
    nota as (
      select a.prospeccao_id lead, max(a.criado_em) em
        from prospeccao_atividades a join l on l.id = a.prospeccao_id
       where a.membro_id = l.vendedor_id and length(trim(coalesce(a.descricao,''))) > 0
       group by a.prospeccao_id),
    vis as (
      select e.prospeccao_id lead, max(e.inicio) ultima,
             bool_or(e.inicio >= now()) futura
        from eventos_agenda e join l on l.id = e.prospeccao_id
       where e.conta_id = %(conta)s and {vis.sql_conta('e', festa=festa)}
       group by e.prospeccao_id)
    select l.id, l.status, l.vendedor_id, l.evento_em, l.criado_em, l.orcamento_id, l.quem,
           coalesce(l.numero, cvw.contato_ref, l.telefone), msg.ult_out, msg.ult_in, nota.em,
           vis.ultima, coalesce(vis.futura, false),
           (select o.criado_em from orcamentos o where o.id = l.orcamento_id),
           cvw.id, cvw.chip_id
      from l left join msg on msg.lead = l.id left join cvw on cvw.lead = l.id
      left join nota on nota.lead = l.id left join vis on vis.lead = l.id"""


def leads(c, conta_id: int, cfg: dict | None = None, agora: datetime | None = None,
          *, lead_id: int | None = None) -> list[dict]:
    """Todo lead da empresa com o relógio do resgate: quando começou a contar, o
    prazo, a faixa, e se já venceu. Uma consulta pra conta inteira.

    Devolve também os que ainda não venceram (`vence_em` no futuro) — o aviso ao
    vendedor e a tela da fila precisam deles."""
    cfg = cfg or config(c, conta_id)
    agora = agora or datetime.now(timezone.utc)
    hoje = agora.astimezone(_BRT).date()
    festa = _perfil_eventos(c, conta_id)
    try:
        with c.transaction():
            rows = c.execute(_sql_leads(festa), {"conta": conta_id, "ia": cfg.get("membro_id"),
                                                 "perdidos": bool(cfg.get("incluir_perdidos")),
                                                 "lead": lead_id}).fetchall()
    except Exception as e:  # noqa: BLE001 — banco sem a 396
        _log.info("resgate.leads: sem fila (conta=%s): %s", conta_id, e)
        return []
    out = []
    for (lid, status, vend, evento_em, criado, orc_id, quem, numero, ult_out, ult_in, nota,
         vis_ult, vis_futura, orc_em, conversa_id, chip_id) in rows:
        if vis_futura:
            continue                      # visita marcada: a IA não se mete
        if evento_em and evento_em < hoje + timedelta(days=FESTA_MIN_DIAS):
            continue                      # festa passou ou é já: não é resgate
        if not conversa_id or not _so_digitos(numero):
            continue                      # sem conversa de WhatsApp não há por onde chamar
        aquecido = bool(orc_id or vis_ult)
        marcos = [t for t in (ult_out, criado, nota) if t]
        prazo = int(cfg.get("dias") or 7)
        if aquecido:
            if cfg.get("aquecido_dias") is None:
                continue                  # "nunca": o lead esquentado fica com o vendedor
            prazo = int(cfg["aquecido_dias"])
            marcos += [t for t in (orc_em, vis_ult) if t]
        desde = max(marcos)
        if ult_in and (not ult_out or ult_in > ult_out):
            faixa = 1
        elif festa and evento_em:
            faixa = 2
        elif status == "perdido":
            faixa = 4
        else:
            faixa = 3
        out.append({"id": lid, "status": status, "vendedor_id": vend, "quem": quem,
                    "numero": _so_digitos(numero), "conversa_id": conversa_id,
                    "chip_id": chip_id, "evento_em": evento_em, "desde": desde,
                    "prazo_dias": prazo, "vence_em": desde + timedelta(days=prazo),
                    "aquecido": aquecido, "faixa": faixa, "ult_in": ult_in,
                    "segurado": bool(nota and nota == desde)})
    return out


def fila(c, conta_id: int, cfg: dict | None = None, agora: datetime | None = None) -> list[dict]:
    """Os leads VENCIDOS, na ordem em que o resgate chama: 1º o cliente que ficou
    esperando resposta, 2º a festa mais próxima, 3º os abertos mais recentes, 4º os
    perdidos mais recentes."""
    agora = agora or datetime.now(timezone.utc)
    return fila_de([x for x in leads(c, conta_id, cfg, agora) if x["vence_em"] <= agora])


# ══════════════════════════════════════════════════════════════════ a IA escreve

def _base_da_empresa(pool, c, conta_id: int, festa: bool) -> str:
    """O que o atendente da empresa sabe: instruções, perguntas frequentes e o
    catálogo com SÓ os preços liberados — o mesmo material do agente da regra."""
    from finance import agente as ag
    from finance import servicos_catalogo as scat
    instr, faqs = ag._conhecimento(c, conta_id)
    escondidos = ag._precos_escondidos(c, conta_id, todos=True)
    catalogo = scat.listar(pool, conta_id)
    cat_txt = "\n".join(
        ag._linha_catalogo(s, escondidos is None or s["slug"] in escondidos,
                           aproximado=True, eventos=festa) for s in catalogo) or "(sem catálogo)"
    return (f"INSTRUÇÕES DA EMPRESA:\n{instr or '(nenhuma)'}\n\n"
            f"PERGUNTAS FREQUENTES:\n{faqs or '(nenhuma)'}\n\n"
            f"CATÁLOGO DE SERVIÇOS (valor só se estiver escrito; sempre \"a partir de\"):\n{cat_txt}")


def _historico(c, conversa_id: int, n: int = 16) -> str:
    msgs = c.execute("""select autor, texto from mensagens where conversa_id=%s
                         order by criado_em desc, id desc limit %s""", (conversa_id, n)).fetchall()
    return "\n".join(("Cliente: " if a == "lead" else "Empresa: ") + (t or "")
                     for (a, t) in reversed(msgs)) or "(conversa vazia)"


def _system(pool, c, conta_id: int, festa: bool) -> str:
    return ("Você é o atendente virtual da empresa, no WhatsApp. Fala em português do "
            "Brasil, tom próximo e educado, mensagens curtas. Use SÓ o que está na base "
            "abaixo — NUNCA invente preço, prazo, disponibilidade de data ou promessa. "
            f"Hoje é {datetime.now(_BRT).date().isoformat()}. "
            "Responda SEMPRE só com JSON válido, sem markdown.\n\n"
            + _base_da_empresa(pool, c, conta_id, festa))


def _pedido_retomada(lead: dict, regra: dict | None, festa: bool, historico: str,
                     dias_parado: int) -> str:
    apres = ((regra or {}).get("ia_apresentacao") or "").strip()
    passo = ("convidar pra conhecer o espaço (a visita)" if festa
             else "convidar pra uma conversa rápida com a equipe")
    faixa = lead["faixa"]
    situacao = {
        1: "O CLIENTE FEZ A ÚLTIMA PERGUNTA E FICOU SEM RESPOSTA. Peça desculpa pela demora "
           "em uma frase e responda o que ele perguntou (só com o que está na base; o que não "
           "estiver, diga que confirma).",
        2: "O cliente tem uma data em vista. Lembre o que ele queria (a data, o tipo, quantas "
           "pessoas, se estiver na conversa) sem afirmar que a data está livre.",
        3: "A conversa esfriou. Retome lembrando o que ele procurava.",
        4: "A conversa foi encerrada sem resposta dele. Retome com leveza, sem pressão, "
           "perguntando se ainda faz sentido.",
    }[faixa]
    from finance import calendario as _cal
    return (
        f"Conversa com {lead['quem']} (a última fala da empresa foi há {dias_parado} dias):\n"
        f"{historico}{_cal.bloco(historico)}\n\n"
        "RETOMADA: você vai escrever a PRIMEIRA mensagem sua pra este cliente, voltando a "
        "falar depois do silêncio.\n"
        + (f"- Apresente-se assim: \"{apres}\".\n" if apres else
           "- Apresente-se como da equipe de atendimento da empresa.\n")
        + f"- {situacao}\n"
        f"- Termine com UMA pergunta que facilite a resposta — o próximo passo é {passo}.\n"
        "- Até 4 linhas, sem lista, sem link, no máximo 1 emoji. Use o primeiro nome dele "
        "se souber.\n"
        "- Nunca cite o vendedor anterior nem diga que alguém esqueceu ou saiu.\n"
        'Retorne APENAS JSON: {"mensagem":"texto pra mandar ao cliente"}')


def redigir(pool, conta_id: int, lead: dict, regra: dict | None,
            agora: datetime | None = None) -> str | None:
    """A IA escreve a retomada deste lead. None se não deu (a IA fora do ar, JSON
    torto) — quem chama tenta outro lead e este fica pra depois.

    Não segura conexão enquanto a IA pensa: lê o que precisa, devolve a conexão, e
    só pega outra pra anotar o custo (o app tem poucas por processo)."""
    from core.brain import Brain
    from finance import agente as ag
    from finance import ia_uso as _iu
    agora = agora or datetime.now(timezone.utc)
    dias_parado = max(1, (agora - lead["desde"]).days)
    try:
        with pool.connection() as c:
            festa = _perfil_eventos(c, conta_id)
            system = _system(pool, c, conta_id, festa)
            pedido = _pedido_retomada(lead, regra, festa, _historico(c, lead["conversa_id"]),
                                      dias_parado)
            c.commit()
        brain = Brain()
        resp = brain.chamar(system=system, mensagens=[{"role": "user", "content": pedido}])
        with pool.connection() as c:
            _iu.registrar(c, conta_id, lead["conversa_id"], lead["id"],
                          getattr(brain, "model", None), resp)
            c.commit()
        txt = "".join(getattr(b, "text", "") for b in resp.content
                      if getattr(b, "type", None) == "text").strip()
        msg = (ag._extrair_json(txt).get("mensagem") or "").strip()
        from finance import calendario as _cal
        msg = _cal.corrigir(msg)
    except Exception as e:  # noqa: BLE001
        _log.info("resgate.redigir: a IA não escreveu (conta=%s lead=%s): %s", conta_id, lead["id"], e)
        return None
    return msg[:1200] or None


# ══════════════════════════════════════════════════════════════════ os envios

def _registrar(c, conta_id: int, tipo: str, *, lead=None, membro=None, ref_em=None, texto="",
               ok=True, erro=None) -> None:
    c.execute("""insert into resgate_envios (conta_id, prospeccao_id, membro_id, tipo, ref_em,
                                             texto, ok, erro)
                 values (%s,%s,%s,%s,%s,%s,%s,%s)""",
              (conta_id, lead, membro, tipo, ref_em, (texto or "")[:4000], ok, (erro or None)))


def _inicio_do_dia(agora: datetime) -> datetime:
    d = agora.astimezone(_BRT)
    return d.replace(hour=0, minute=0, second=0, microsecond=0)


def _contagem_hoje(c, conta_id: int, tipos: tuple, agora: datetime, *, ok=None) -> int:
    sql = ("select count(*) from resgate_envios where conta_id=%s and tipo = any(%s) "
           "and criado_em >= %s")
    args = [conta_id, list(tipos), _inicio_do_dia(agora)]
    if ok is not None:
        sql += " and ok = %s"
        args.append(ok)
    return int(c.execute(sql, args).fetchone()[0] or 0)


def _pode_mandar_agora(c, conta_id: int, cfg: dict, lead_id: int, agora: datetime) -> bool:
    """O teto do dia e o espaçamento. Prévia conta igual à retomada: o Ensaio anda no
    ritmo do ligado."""
    if _contagem_hoje(c, conta_id, ENVIOS, agora) >= int(cfg.get("teto_dia") or 20):
        return False
    u = c.execute("""select max(criado_em) from resgate_envios
                      where conta_id=%s and tipo = any(%s)""", (conta_id, list(ENVIOS))).fetchone()
    espaco = timedelta(minutes=ESPACO_MIN + (int(lead_id) * 7) % 11)
    return not (u and u[0] and agora - u[0] < espaco)


def _chip_de_pe(c, conta_id: int, chip_id) -> bool:
    """O chip por onde a conversa sai está conectado? Só LEITURA de `canais_config`
    (CLAUDE.md §1): o resgate nunca mexe na conexão, só espera ela voltar."""
    alvo = int(chip_id) if chip_id else int(conta_id)
    try:
        with c.transaction():
            r = c.execute("""select coalesce(ativo,false), desconectado_em from canais_config
                              where conta_id=%s and canal='whatsapp'""", (alvo,)).fetchone()
    except Exception:  # noqa: BLE001
        return False
    return bool(r and r[0] and r[1] is None)


def supervisor(pool, conta_id: int, texto: str, *, tipo: str = "supervisor", lead=None,
               ref_em=None, cfg: dict | None = None) -> bool:
    """Manda uma mensagem pro WhatsApp do supervisor, pelo número principal da
    empresa, e registra. Nunca levanta."""
    try:
        with pool.connection() as c:
            cfg = cfg or config(c, conta_id)
            num = cfg.get("supervisor_whatsapp") or ""
            if not num:
                return False
            from finance import whatsapp_out as wo
            destino = wo.preparar(c, conta_id)
            c.commit()
        res = wo.enviar_pronto(destino, num, texto)
        with pool.connection() as c:
            _registrar(c, conta_id, tipo, lead=lead, ref_em=ref_em, texto=texto,
                       ok=bool(res.get("ok")), erro=None if res.get("ok") else str(res.get("erro"))[:200])
            c.commit()
        return bool(res.get("ok"))
    except Exception as e:  # noqa: BLE001
        _log.info("resgate.supervisor: não saiu (conta=%s): %s", conta_id, e)
        return False


def e_lead_do_resgate(c, conta_id: int, prospeccao_id) -> bool:
    if not prospeccao_id:
        return False
    try:
        with c.transaction():
            return bool(c.execute(
                """select 1 from resgate_leads r join prospeccao p on p.id = r.prospeccao_id
                    where r.prospeccao_id=%s and r.conta_id=%s and r.ativo
                      and p.vendedor_id = r.membro_id""", (prospeccao_id, conta_id)).fetchone())
    except Exception:  # noqa: BLE001
        return False


def leads_da_ia(c, conta_id: int) -> set[int]:
    """Os leads que estão com a IA pelo resgate — o follow-up e a cobrança do vendedor
    pulam estes (o dono deles é o membro IA, que não tem celular). Tolerante."""
    try:
        with c.transaction():
            # só enquanto o lead é do membro IA: devolvido pelo gestor (com o resgate
            # ligado ou não), volta pra cobrança do vendedor na hora
            return {int(r[0]) for r in c.execute(
                """select r.prospeccao_id from resgate_leads r
                     join prospeccao p on p.id = r.prospeccao_id
                    where r.conta_id=%s and r.ativo and p.vendedor_id = r.membro_id""",
                (conta_id,)).fetchall()}
    except Exception:  # noqa: BLE001
        return set()


def regra_do_lead(c, conta_id: int, prospeccao_id) -> dict | None:
    """Se o lead está com a IA pelo resgate, a regra que responde por ele — com
    `vale_desde` puxado pra quando ele entrou no resgate: o que o vendedor falou
    antes não é "alguém da equipe respondeu" (ver `chip_regra.pausar_se_humano`)."""
    try:
        with c.transaction():
            r = c.execute("""select r.membro_id, r.entrou_em, p.vendedor_id
                               from resgate_leads r join prospeccao p on p.id = r.prospeccao_id
                              where r.prospeccao_id=%s and r.conta_id=%s and r.ativo""",
                          (prospeccao_id, conta_id)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not r or r[2] != r[0]:
        return None
    regra = regra_do_membro(c, conta_id, r[0])
    if not (regra and regra.get("ativa") and regra.get("ia_ligada")):
        return None
    vd = regra.get("vale_desde")
    regra["vale_desde"] = max(vd, r[1]) if vd else r[1]
    regra["resgate"] = True
    return regra


# ══════════════════════════════════════════════════════════════════ passar o lead

def _passar(c, conta_id: int, lead: dict, membro_ia: int) -> bool:
    """O lead vai pro membro IA — se ninguém mexeu nele desde que a fila foi lida
    (mensagem nova, nota nova, outro dono). Não faz commit."""
    ok = c.execute(
        """update prospeccao set vendedor_id=%s, atualizado_em=now()
            where id=%s and conta_id=%s and vendedor_id is not distinct from %s
              and not exists (select 1 from conversas cv join mensagens m on m.conversa_id = cv.id
                               where cv.prospeccao_id = %s and cv.conta_id = %s
                                 and m.direcao = 'out' and m.criado_em > %s)
              and not exists (select 1 from prospeccao_atividades a
                               where a.prospeccao_id = %s and a.criado_em > %s
                                 and a.membro_id = %s
                                 and length(trim(coalesce(a.descricao,''))) > 0)""",
        (membro_ia, lead["id"], conta_id, lead["vendedor_id"], lead["id"], conta_id, lead["desde"],
         lead["id"], lead["desde"], lead["vendedor_id"])).rowcount
    if not ok:
        return False
    # como a conversa estava: se o envio falhar, ela volta EXATAMENTE assim — a IA
    # ligada numa conversa que voltou pro vendedor responderia o cliente dele
    cv = c.execute("select status, agente_ativo, responsavel_membro_id from conversas "
                   "where id=%s and conta_id=%s", (lead["conversa_id"], conta_id)).fetchone()
    c.execute("""insert into resgate_leads (prospeccao_id, conta_id, membro_id, vendedor_antes,
                                            conversa_id, faixa, status_antes, entrou_em, ativo,
                                            estado, ultimo_envio_em, respondeu_em, saiu_em,
                                            conv_status_antes, conv_agente_antes, conv_resp_antes)
                 values (%s,%s,%s,%s,%s,%s,%s,now(),true,'chamado',null,null,null,%s,%s,%s)
                 on conflict (prospeccao_id) do update set
                   membro_id=excluded.membro_id, vendedor_antes=excluded.vendedor_antes,
                   conversa_id=excluded.conversa_id, faixa=excluded.faixa,
                   status_antes=excluded.status_antes, entrou_em=now(), ativo=true,
                   estado='chamado', ultimo_envio_em=null, respondeu_em=null, saiu_em=null,
                   conv_status_antes=excluded.conv_status_antes,
                   conv_agente_antes=excluded.conv_agente_antes,
                   conv_resp_antes=excluded.conv_resp_antes""",
              (lead["id"], conta_id, membro_ia, lead["vendedor_id"], lead["conversa_id"],
               lead["faixa"], lead["status"], cv[0] if cv else None, cv[1] if cv else None,
               cv[2] if cv else None))
    c.execute("""update conversas set responsavel_membro_id=%s, agente_ativo=true,
                        status = 'aberta'
                  where id=%s and conta_id=%s""", (membro_ia, lead["conversa_id"], conta_id))
    antes = ""
    if lead["vendedor_id"]:
        r = c.execute("select coalesce(nullif(nome,''), email) from membros "
                      "where id=%s and conta_id=%s", (lead["vendedor_id"], conta_id)).fetchone()
        antes = f" Era de {r[0]}." if r else ""
    dias = max(1, (datetime.now(timezone.utc) - lead["desde"]).days)
    c.execute("""insert into prospeccao_atividades (prospeccao_id, membro_id, tipo, descricao)
                 values (%s, null, 'nota', %s)""",
              (lead["id"], f"Passou pro resgate da IA: {dias} dias sem mensagem nossa nem "
                           f"justificativa no histórico.{antes}"[:400]))
    return True


def _devolver(c, conta_id: int, lead: dict) -> None:
    """O envio falhou: o lead volta pra quem era, e a conversa volta como estava."""
    c.execute("update prospeccao set vendedor_id=%s, atualizado_em=now() where id=%s and conta_id=%s",
              (lead["vendedor_id"], lead["id"], conta_id))
    c.execute("""update conversas cv set status = coalesce(r.conv_status_antes, cv.status),
                        agente_ativo = coalesce(r.conv_agente_antes, false),
                        responsavel_membro_id = r.conv_resp_antes
                   from resgate_leads r
                  where r.prospeccao_id=%s and r.conta_id=%s and cv.id = r.conversa_id
                    and cv.conta_id = r.conta_id""", (lead["id"], conta_id))
    c.execute("delete from resgate_leads where prospeccao_id=%s and conta_id=%s", (lead["id"], conta_id))
    c.execute("""delete from prospeccao_atividades
                  where prospeccao_id=%s and membro_id is null
                    and descricao like 'Passou pro resgate da IA:%%'
                    and criado_em > now() - interval '1 hour'""", (lead["id"],))


def _mandar_retomada(pool, conta_id: int, cfg: dict, lead: dict, texto: str) -> bool:
    """Passa o lead e manda a retomada pelo chip da conversa. Se o envio falha, o
    lead volta pra quem era e a falha entra na conta do freio."""
    from finance import agente as ag
    from finance import whatsapp_out as wo
    with pool.connection() as c:
        if not _passar(c, conta_id, lead, cfg["membro_id"]):
            c.rollback()
            return False
        destino = wo.preparar(c, conta_id)
        c.commit()
    res = wo.enviar_pronto(destino, lead["numero"], texto, chip_id=lead["chip_id"])
    with pool.connection() as c:
        if res.get("ok"):
            ag._add_bot_msg(c, lead["conversa_id"], "whatsapp", texto, res.get("sid"))
            c.execute("update resgate_leads set ultimo_envio_em=now() where prospeccao_id=%s",
                      (lead["id"],))
            _registrar(c, conta_id, "retomada", lead=lead["id"], ref_em=lead["desde"], texto=texto)
        else:
            _devolver(c, conta_id, lead)
            _registrar(c, conta_id, "retomada", lead=lead["id"], ref_em=lead["desde"], texto=texto,
                       ok=False, erro=str(res.get("erro") or "falhou")[:200])
        c.commit()
    return bool(res.get("ok"))


def _previa(lead: dict, vendedor: str, texto: str, n: int, teto: int, dias: int) -> str:
    festa = (f"\nFesta {lead['evento_em'].strftime('%d/%m')}" if lead.get("evento_em") else "")
    return (f"🧪 Ensaio do resgate · {n} de {teto} hoje\n"
            f"{_primeiro(lead['quem'])} · lead #{lead['id']} · era de {vendedor} · "
            f"parado há {dias} dias{festa}\n"
            f"Por quê: {FAIXAS[lead['faixa']].lower()}.\n\n"
            f"Eu mandaria:\n“{texto}”\n\nNada foi enviado pra cliente.")


def _primeiro(nome: str) -> str:
    return (str(nome or "Cliente").strip().split() or ["Cliente"])[0].title()


def _nome(c, conta_id: int, membro_id) -> str:
    if not membro_id:
        return "ninguém"
    r = c.execute("select coalesce(nullif(nome,''), email) from membros where id=%s and conta_id=%s",
                  (membro_id, conta_id)).fetchone()
    return _primeiro(r[0]) if r and r[0] else "ninguém"


# ══════════════════════════════════════════════════════════════════ o que já está com a IA

def _devolvido(c, conta_id: int, lead: int, conv, vend) -> None:
    """O gestor deu o lead pra alguém: sai do resgate, e a IA sai da conversa (quem
    responde agora é gente — a do novo dono)."""
    c.execute("""update resgate_leads set ativo=false, estado='devolvido', saiu_em=now()
                  where prospeccao_id=%s and conta_id=%s""", (lead, conta_id))
    if conv:
        c.execute("""update conversas set agente_ativo=false, responsavel_membro_id=%s
                      where id=%s and conta_id=%s""", (vend, conv, conta_id))


def varrer_devolvidos(pool, conta_id: int) -> int:
    """Com o resgate em qualquer modo: lead que o gestor tirou do membro IA sai do
    resgate. Sem isto, desligar o resgate e devolver os leads deixaria a cobrança do
    vendedor pulando esses leads pra sempre."""
    n = 0
    with pool.connection() as c:
        for lead, conv, vend in c.execute(
                """select r.prospeccao_id, r.conversa_id, p.vendedor_id
                     from resgate_leads r join prospeccao p on p.id = r.prospeccao_id
                    where r.conta_id=%s and r.ativo
                      and p.vendedor_id is distinct from r.membro_id""", (conta_id,)).fetchall():
            _devolvido(c, conta_id, lead, conv, vend)
            n += 1
        c.commit()
    return n


def _acompanhar(pool, conta_id: int, cfg: dict, agora: datetime) -> dict:
    """Os leads que já estão com a IA: o cliente respondeu? pediu pra parar? alguém
    da equipe falou com ele (a IA sai)? o gestor deu o lead pra outra pessoa?"""
    from finance import chip_regra as _cr
    out = {"responderam": 0, "pararam": 0, "pausados": 0, "devolvidos": 0}
    avisar = []
    with pool.connection() as c:
        rows = c.execute(
            """select r.prospeccao_id, r.conversa_id, r.entrou_em, r.estado, r.membro_id,
                      p.vendedor_id, coalesce(nullif(p.contato,''), nullif(p.empresa,''), 'Cliente')
                 from resgate_leads r join prospeccao p on p.id = r.prospeccao_id
                where r.conta_id=%s and r.ativo""", (conta_id,)).fetchall()
        for lead, conv, entrou, estado, mid, vend, quem in rows:
            if vend != mid:
                _devolvido(c, conta_id, lead, conv, vend)
                out["devolvidos"] += 1
                continue
            if estado in ("chamado", "perdido"):
                # depois do perdido do resgate, a resposta reabre: a entrada de mensagem
                # já devolve o lead à etapa que a conta manda (funil_perda.reativar), e a
                # IA — ainda dona do lead — segue a conversa
                resp = c.execute("""select texto from mensagens where conversa_id=%s
                                      and direcao='in' and criado_em > %s
                                    order by criado_em""", (conv, entrou)).fetchall()
                if resp:
                    parou = any(RE_PARAR.search(t or "") for (t,) in resp)
                    c.execute("""update resgate_leads set estado=%s, respondeu_em=now(), opt_out=%s
                                  where prospeccao_id=%s""",
                              ("parou" if parou else "respondeu", parou, lead))
                    out["pararam" if parou else "responderam"] += 1
                    if parou:
                        _registrar(c, conta_id, "parou", lead=lead)
            if estado != "pausado":
                r = {"vale_desde": entrou}
                if _cr.pausar_se_humano(c, conta_id, conv, r):
                    c.execute("update resgate_leads set estado='pausado' where prospeccao_id=%s",
                              (lead,))
                    out["pausados"] += 1
                    avisar.append((lead, quem))
        c.commit()
    for lead, quem in avisar:
        supervisor(pool, conta_id, f"⏸ Resgate · lead #{lead} ({_primeiro(quem)})\n"
                                   "Alguém da equipe falou com o cliente, então eu saí da "
                                   "conversa. O lead continua com a IA.", lead=lead, cfg=cfg)
    return out


def _freio(pool, conta_id: int, cfg: dict, agora: datetime) -> bool:
    """Pausa o resgate se hoje já teve pedidos de parar ou falhas demais. Devolve se
    está pausado (agora ou antes)."""
    if cfg.get("pausado_em"):
        return True
    with pool.connection() as c:
        parou = _contagem_hoje(c, conta_id, ("parou",), agora)
        falhas = _contagem_hoje(c, conta_id, ("retomada", "toque"), agora, ok=False)
        motivo = None
        if parou >= FREIO_PARAR:
            motivo = f"{parou} clientes pediram pra parar hoje"
        elif falhas >= FREIO_FALHAS:
            motivo = f"{falhas} envios falharam hoje"
        if not motivo:
            return False
        c.execute("update resgate_config set pausado_em=now(), pausado_motivo=%s where conta_id=%s",
                  (motivo, conta_id))
        c.commit()
    supervisor(pool, conta_id, f"🛑 Resgate pausado: {motivo}.\nNada mais sai até alguém "
                               "retomar no cartão do Resgate (Comunicação › Agente).", cfg=cfg)
    return True


# ══════════════════════════════════════════════════════════════════ o aviso ao vendedor

def _avisos_vendedor(pool, conta_id: int, cfg: dict, todos: list[dict], agora: datetime) -> int:
    """Dois dias antes do prazo: um aviso por dia por vendedor, com os leads dele que
    vão pro resgate. Registra UM envio por lead (o dedup é pelo relógio do lead: nota
    ou mensagem nova recomeçam o relógio, e aí vale um aviso novo)."""
    from finance import chip_regra as _cr
    janela = timedelta(days=AVISO_DIAS)
    por_vend: dict[int, list[dict]] = {}
    with pool.connection() as c:
        ja = {(r[0], r[1]) for r in c.execute(
            """select prospeccao_id, ref_em from resgate_envios
                where conta_id=%s and tipo='aviso_vendedor'""", (conta_id,)).fetchall()}
        hoje_ja = {r[0] for r in c.execute(
            """select distinct membro_id from resgate_envios where conta_id=%s
                and tipo='aviso_vendedor' and criado_em >= %s""",
            (conta_id, _inicio_do_dia(agora))).fetchall()}
        ativos = {r[0] for r in c.execute("select id from membros where conta_id=%s and ativo",
                                          (conta_id,)).fetchall()}
        for x in todos:
            v = x["vendedor_id"]
            if not v or v not in ativos or v in hoje_ja or (x["id"], x["desde"]) in ja:
                continue
            if x["vence_em"] - janela <= agora:
                por_vend.setdefault(v, []).append(x)
        c.commit()
    n = 0
    for v, xs in por_vend.items():
        xs.sort(key=lambda x: x["vence_em"])
        nomes = ", ".join(_primeiro(x["quem"]) for x in xs[:8]) + (
            f" e mais {len(xs) - 8}" if len(xs) > 8 else "")
        um = len(xs) == 1
        titulo = (f"🤖 Um {_palavra('lead', conta_id)} seu vai pro resgate da IA" if um
                  else f"🤖 {len(xs)} leads seus vão pro resgate da IA")
        corpo = (f"{nomes}: sem mensagem sua há dias. Em 2 dias {'ele passa' if um else 'eles passam'} "
                 "pra IA. Pra ficar, mande uma mensagem ou abra a ficha e escreva o motivo "
                 "em \"Segurar este lead\".")
        _cr.notificar(pool, conta_id, v, titulo, corpo, f"/cockpit/lead/{xs[0]['id']}")
        with pool.connection() as c:
            for x in xs:
                _registrar(c, conta_id, "aviso_vendedor", lead=x["id"], membro=v, ref_em=x["desde"])
            c.commit()
        n += len(xs)
    return n


def _avisado_a_tempo(c, conta_id: int, cfg: dict, lead: dict, agora: datetime) -> bool:
    """O lead só passa 48h depois do aviso ao vendedor (quando o aviso está ligado e
    o lead tem um vendedor ativo)."""
    if not cfg.get("aviso_vendedor") or not lead["vendedor_id"]:
        return True
    ativo = c.execute("select ativo from membros where id=%s and conta_id=%s",
                      (lead["vendedor_id"], conta_id)).fetchone()
    if not (ativo and ativo[0]):
        return True
    r = c.execute("""select min(criado_em) from resgate_envios
                      where conta_id=%s and tipo='aviso_vendedor' and prospeccao_id=%s
                        and ref_em=%s""", (conta_id, lead["id"], lead["desde"])).fetchone()
    return bool(r and r[0] and agora - r[0] >= timedelta(hours=AVISO_H))


# ══════════════════════════════════════════════════════════════════ o resumo das 19h

def resumo(c, conta_id: int, cfg: dict, agora: datetime) -> str:
    ini = _inicio_do_dia(agora)
    n_fila = len(fila(c, conta_id, cfg, agora))
    teto = max(1, int(cfg.get("teto_dia") or 20))
    dias_uteis = -(-n_fila // teto)
    rodape = f"Fila: {n_fila}" + (f" · ~{dias_uteis} dias úteis" if n_fila else "")
    if cfg.get("modo") == "ensaio":
        n = _contagem_hoje(c, conta_id, ("previa",), agora)
        return (f"📋 Ensaio do resgate · hoje\n{n} prévia{'s' if n != 1 else ''} pra você. "
                "Nada foi enviado pra cliente.\n" + rodape)
    cham = _contagem_hoje(c, conta_id, ("retomada",), agora, ok=True)
    toques = _contagem_hoje(c, conta_id, ("toque",), agora, ok=True)
    resp = c.execute("""select count(*) filter (where estado in ('respondeu','parou')),
                               count(*) filter (where estado = 'parou'),
                               count(*) filter (where estado = 'pausado')
                          from resgate_leads where conta_id=%s and respondeu_em >= %s""",
                     (conta_id, ini)).fetchone()
    linhas = [f"📋 Resgate · hoje", f"{cham} chamado{'s' if cham != 1 else ''} · "
              f"{resp[0]} respondeu" + ("ram" if resp[0] != 1 else "")]
    try:
        with c.transaction():
            vis = c.execute("""select count(*) from ia_visitas v join resgate_leads r
                                 on r.prospeccao_id = v.prospeccao_id and r.ativo
                                where v.conta_id=%s and v.marcado_em >= %s""", (conta_id, ini)).fetchone()[0]
            orc = c.execute("""select count(*) from ia_orcamentos o join resgate_leads r
                                 on r.prospeccao_id = o.prospeccao_id and r.ativo
                                where o.conta_id=%s and o.estado='conferir'""", (conta_id,)).fetchone()[0]
    except Exception:  # noqa: BLE001 — banco sem a 390/392
        vis, orc = 0, 0
    if vis:
        linhas.append(f"{vis} visita{'s' if vis != 1 else ''} marcada{'s' if vis != 1 else ''}")
    if orc:
        linhas.append(f"{orc} orçamento{'s' if orc != 1 else ''} esperando conferência")
    if toques:
        linhas.append(f"{toques} toque{'s' if toques != 1 else ''} em quem não respondeu")
    perd = c.execute("""select count(*) from resgate_leads where conta_id=%s and perdido_em >= %s""",
                     (conta_id, ini)).fetchone()[0]
    if perd:
        linhas.append(f"{perd} virou perdido (3 toques sem resposta)" if perd == 1
                      else f"{perd} viraram perdidos (3 toques sem resposta)")
    if resp[1]:
        linhas.append(f"{resp[1]} pediu pra parar (não chamo mais)" if resp[1] == 1
                      else f"{resp[1]} pediram pra parar (não chamo mais)")
    pausados = c.execute("""select count(*) from resgate_leads where conta_id=%s and ativo
                             and estado='pausado'""", (conta_id,)).fetchone()[0]
    if pausados:
        linhas.append(f"{pausados} com alguém da equipe falando com o cliente (eu saí)")
    # as justificativas do dia: nota do próprio vendedor num lead que tinha sido avisado
    just = c.execute(
        """select coalesce(nullif(m.nome,''), m.email), a.descricao
             from prospeccao_atividades a
             join prospeccao p on p.id = a.prospeccao_id and p.conta_id = %s
             join membros m on m.id = a.membro_id and m.conta_id = p.conta_id
            where a.criado_em >= %s and a.membro_id = p.vendedor_id
              and length(trim(coalesce(a.descricao,''))) > 0
              and exists (select 1 from resgate_envios e where e.prospeccao_id = a.prospeccao_id
                             and e.tipo = 'aviso_vendedor' and e.criado_em > a.criado_em - interval '3 days')
            order by a.criado_em limit 20""", (conta_id, ini)).fetchall()
    if just:
        exemplos = "; ".join(f"{_primeiro(n)}: “{(d or '').strip()[:60]}”" for n, d in just[:3])
        linhas.append(f"Segurados com justificativa: {len(just)} ({exemplos})")
    if cfg.get("pausado_em"):
        linhas.append(f"⚠️ Pausado: {cfg.get('pausado_motivo') or 'freio'}")
    linhas.append(rodape)
    return "\n".join(linhas)


# ══════════════════════════════════════════════════════════════════ o ciclo

def _uma_conta(pool, conta_id: int, agora: datetime) -> dict:
    out = {"previas": 0, "retomadas": 0, "avisos": 0, "toques": 0, "perdidos": 0}
    with pool.connection() as c:
        cfg = config(c, conta_id)
        c.commit()
    modo = cfg.get("modo")
    if modo not in ("ensaio", "ligado") or not cfg.get("membro_id"):
        return out
    ligado = modo == "ligado"
    if ligado:
        out.update(_acompanhar(pool, conta_id, cfg, agora))
        out["perdidos"] = _perder_sem_resposta(pool, conta_id, agora)
    pausado = ligado and _freio(pool, conta_id, cfg, agora)
    dentro = pode_rodar_agora(cfg, agora)
    todos, regra = [], None
    if dentro and not pausado:
        with pool.connection() as c:
            todos = leads(c, conta_id, cfg, agora)
            regra = regra_do_membro(c, conta_id, cfg["membro_id"])
            c.commit()
    if ligado and dentro and not pausado and cfg.get("aviso_vendedor"):
        out["avisos"] = _avisos_vendedor(pool, conta_id, cfg, todos, agora)
    regra_ok = bool(regra and regra.get("ativa") and regra.get("ia_ligada"))
    if dentro and not pausado and (not ligado or regra_ok):
        # o toque de quem já foi chamado vem antes do lead novo da fila: é conversa
        # começada, e o dia dele passa
        if not (ligado and _um_toque(pool, conta_id, cfg, regra, agora, out)):
            _um_envio(pool, conta_id, cfg, regra, todos, agora, out)
    _resumo_do_dia(pool, conta_id, cfg, agora)
    return out


def _pedido_toque(lead: dict, n: int, festa: bool, historico: str, dias: int) -> str:
    passo = "conhecer o espaço" if festa else "uma conversa rápida com a equipe"
    if n == 2:
        o_que = ("É o 2º TOQUE: o cliente não respondeu a sua mensagem. Escreva uma mensagem CURTA "
                 "(até 2 linhas) trazendo UMA coisa nova e útil tirada da base (um item do "
                 f"catálogo que combina com o que ele queria, ou o convite pra {passo}) — sem "
                 "cobrar a resposta e sem repetir a mensagem anterior.")
    else:
        o_que = ("É o 3º e ÚLTIMO TOQUE: o cliente não respondeu às duas mensagens. Pergunte com "
                 "gentileza, em 1 ou 2 linhas, se ainda faz sentido continuar ou se pode encerrar o "
                 "atendimento — deixando a porta aberta pra quando ele quiser.")
    from finance import calendario as _cal
    return (f"Conversa com {lead['quem']} (a sua última mensagem foi há {dias} dias):\n{historico}"
            f"{_cal.bloco(historico)}\n\n"
            f"{o_que}\n- Sem lista, sem link, no máximo 1 emoji. Não se apresente de novo.\n"
            'Retorne APENAS JSON: {"mensagem":"texto pra mandar ao cliente"}')


def redigir_toque(pool, conta_id: int, lead: dict, n: int, agora: datetime | None = None) -> str | None:
    """A IA escreve o 2º ou o 3º toque. None se não deu."""
    from core.brain import Brain
    from finance import agente as ag
    from finance import ia_uso as _iu
    agora = agora or datetime.now(timezone.utc)
    try:
        with pool.connection() as c:
            festa = _perfil_eventos(c, conta_id)
            system = _system(pool, c, conta_id, festa)
            pedido = _pedido_toque(lead, n, festa, _historico(c, lead["conversa_id"]),
                                   max(1, (agora - lead["ultimo_envio_em"]).days))
            c.commit()
        brain = Brain()
        resp = brain.chamar(system=system, mensagens=[{"role": "user", "content": pedido}])
        with pool.connection() as c:
            _iu.registrar(c, conta_id, lead["conversa_id"], lead["id"],
                          getattr(brain, "model", None), resp)
            c.commit()
        txt = "".join(getattr(b, "text", "") for b in resp.content
                      if getattr(b, "type", None) == "text").strip()
        msg = (ag._extrair_json(txt).get("mensagem") or "").strip()
        from finance import calendario as _cal
        msg = _cal.corrigir(msg)
    except Exception as e:  # noqa: BLE001
        _log.info("resgate.redigir_toque: a IA não escreveu (conta=%s lead=%s): %s",
                  conta_id, lead["id"], e)
        return None
    return msg[:800] or None


def _sql_toque_ok() -> str:
    """As travas de um toque, as MESMAS da fila: o lead ainda do membro IA, a conversa
    ainda da IA, sem resposta do cliente desde a retomada, card não fechado, festa que
    não passou nem é já, e sem visita marcada pra frente ("se ele marcou a visita a IA
    não se entromete"). Vale na escolha E de novo logo antes de mandar — entre as duas
    a IA pensou uns segundos, e o cliente pode ter respondido nesse meio."""
    from finance import funil_regua as fr
    from finance import visita as vis
    return f"""
              and p.vendedor_id = r.membro_id and cv.agente_ativo and cv.status <> 'pendente'
              and p.status not in {fr.sql_fechadas('p')}
              and (p.evento_em is null or p.evento_em >= current_date + {int(FESTA_MIN_DIAS)})
              and not exists (select 1 from mensagens mi where mi.conversa_id = r.conversa_id
                                 and mi.direcao = 'in' and mi.criado_em > r.entrou_em)
              and not exists (select 1 from eventos_agenda e
                               where e.conta_id = r.conta_id and e.prospeccao_id = r.prospeccao_id
                                 and e.inicio >= now() and {vis.sql_conta('e', festa=False)})"""


def _toques_devidos(c, conta_id: int, agora: datetime, membro_id=None) -> list[dict]:
    """Quem já foi chamado, não respondeu, e está no dia do próximo toque (só os do
    membro IA da config: é a regra dele que responde se o cliente voltar)."""
    rows = c.execute(
        """select r.prospeccao_id, r.conversa_id, r.toques, r.entrou_em, r.ultimo_envio_em,
                  cv.chip_id,
                  coalesce(nullif(p.whatsapp,''), cv.contato_ref, nullif(p.telefone,'')),
                  coalesce(nullif(p.contato,''), nullif(p.empresa,''), 'Cliente')
             from resgate_leads r
             join prospeccao p on p.id = r.prospeccao_id and p.conta_id = r.conta_id
             join conversas cv on cv.id = r.conversa_id and cv.conta_id = r.conta_id
            where r.conta_id=%s and r.ativo and r.estado='chamado' and not r.opt_out
              and r.membro_id = %s
              and r.ultimo_envio_em is not null""" + _sql_toque_ok() + """
              and ((r.toques = 1 and r.entrou_em <= %s and r.ultimo_envio_em <= %s)
                or (r.toques = 2 and r.entrou_em <= %s and r.ultimo_envio_em <= %s))
              and not exists (select 1 from resgate_envios e
                               where e.prospeccao_id = r.prospeccao_id and not e.ok
                                 and ((e.tipo = 'toque' and e.criado_em > %s)
                                   or (e.tipo = 'erro_texto' and e.criado_em > %s)))
            order by r.ultimo_envio_em""",
        (conta_id, membro_id, agora - timedelta(days=TOQUE_2_DIAS), agora - timedelta(days=TOQUE_2_DIAS),
         agora - timedelta(days=TOQUE_3_DIAS), agora - timedelta(days=TOQUE_INTERVALO_DIAS),
         agora - timedelta(days=1), agora - timedelta(hours=6))).fetchall()
    return [{"id": r[0], "conversa_id": r[1], "toques": r[2], "entrou_em": r[3],
             "ultimo_envio_em": r[4], "chip_id": r[5], "numero": _so_digitos(r[6]), "quem": r[7]}
            for r in rows if _so_digitos(r[6])]


def _um_toque(pool, conta_id: int, cfg: dict, regra, agora: datetime, out: dict) -> bool:
    """Manda um toque, se algum está devido. Devolve True quando havia toque devido
    que podia sair (saiu, ou o teto/espaçamento segurou) — aí o lead novo da fila
    espera. Toque de chip fora do ar não segura a fila."""
    from finance import agente as ag
    from finance import whatsapp_out as wo
    with pool.connection() as c:
        devidos = [x for x in _toques_devidos(c, conta_id, agora, cfg.get("membro_id"))
                   if _chip_de_pe(c, conta_id, x["chip_id"])]
        if not devidos:
            c.commit()
            return False
        lead = devidos[0]
        pode = _pode_mandar_agora(c, conta_id, cfg, lead["id"], agora)
        c.commit()
    if not pode:
        return True
    n = lead["toques"] + 1
    texto = redigir_toque(pool, conta_id, lead, n, agora)
    if not texto:
        # a IA não escreveu: não é falha de envio (não entra no freio); o lead espera 6h
        with pool.connection() as c:
            _registrar(c, conta_id, "erro_texto", lead=lead["id"], ok=False)
            c.commit()
        return True
    with pool.connection() as c:
        # RECONFERE E REIVINDICA, numa transação, antes de mandar: o cliente pode ter
        # respondido enquanto a IA escrevia (aí a IA já está conversando com ele), e o
        # toque não pode sair duas vezes se a gravação depois do envio cair — por isso
        # o `toques` sobe AGORA, e só volta se o envio falhar
        ok = c.execute(
            """update resgate_leads r set toques=%s, ultimo_envio_em=now()
                 from prospeccao p, conversas cv
                where r.prospeccao_id=%s and r.conta_id=%s and r.ativo and r.estado='chamado'
                  and not r.opt_out and r.toques=%s
                  and p.id = r.prospeccao_id and p.conta_id = r.conta_id
                  and cv.id = r.conversa_id and cv.conta_id = r.conta_id""" + _sql_toque_ok(),
            (n, lead["id"], conta_id, lead["toques"])).rowcount
        if not ok:
            c.rollback()
            return True
        _registrar(c, conta_id, "toque", lead=lead["id"], texto=texto)
        envio_id = c.execute("select max(id) from resgate_envios where prospeccao_id=%s "
                             "and tipo='toque'", (lead["id"],)).fetchone()[0]
        destino = wo.preparar(c, conta_id)
        c.commit()
    res = wo.enviar_pronto(destino, lead["numero"], texto, chip_id=lead["chip_id"])
    with pool.connection() as c:
        if res.get("ok"):
            ag._add_bot_msg(c, lead["conversa_id"], "whatsapp", texto, res.get("sid"))
            out["toques"] = out.get("toques", 0) + 1
        else:
            c.execute("""update resgate_leads set toques=%s, ultimo_envio_em=%s
                          where prospeccao_id=%s and conta_id=%s""",
                      (lead["toques"], lead["ultimo_envio_em"], lead["id"], conta_id))
            c.execute("update resgate_envios set ok=false, erro=%s where id=%s",
                      (str(res.get("erro") or "falhou")[:200], envio_id))
        c.commit()
    return True


def _perder_sem_resposta(pool, conta_id: int, agora: datetime) -> int:
    """3 toques sem resposta e mais 3 dias: o lead vira perdido, motivo "não
    respondeu" — pela mesma porta do perdido automático (histórico, motivo, etapa de
    origem). Nada é apagado, e o lead continua da IA: se o cliente escrever, a
    entrada reabre o card e a IA responde. Quem já estava perdido só muda de estado
    no resgate."""
    from finance import funil_perdido as _fp
    from finance import funil_regua as fr
    n = 0
    with pool.connection() as c:
        rows = c.execute(
            f"""select r.prospeccao_id, p.status, p.status in {fr.sql_fechadas('p')}
                  from resgate_leads r
                  join prospeccao p on p.id = r.prospeccao_id and p.conta_id = r.conta_id
                 where r.conta_id=%s and r.ativo and r.estado='chamado' and r.toques >= 3
                   and p.vendedor_id = r.membro_id
                   and r.ultimo_envio_em <= %s""",
            (conta_id, agora - timedelta(days=PERDIDO_DEPOIS_DIAS))).fetchall()
        for lead, status, fechado in rows:
            if fechado:
                continue            # alguém fechou a venda: isso não é "não respondeu"
            if status != "perdido":
                try:
                    with c.transaction():
                        if not _fp.fechar(c, conta_id, {"id": lead, "etapa": status}, agora):
                            continue        # o card mudou entre a leitura e agora
                except Exception as e:  # noqa: BLE001
                    _log.warning("resgate: não fechei o lead %s como perdido: %s", lead, e)
                    continue
            c.execute("""update resgate_leads set estado='perdido', perdido_em=now()
                          where prospeccao_id=%s and conta_id=%s""", (lead, conta_id))
            n += 1
        c.commit()
    return n


def _um_envio(pool, conta_id: int, cfg: dict, regra, todos: list[dict], agora: datetime,
              out: dict) -> None:
    ligado = cfg["modo"] == "ligado"
    vencidos = [x for x in todos if x["vence_em"] <= agora]
    if not vencidos:
        return
    chip_caido = False
    with pool.connection() as c:
        ja_previa = {(r[0], r[1]) for r in c.execute(
            "select prospeccao_id, ref_em from resgate_envios where conta_id=%s and tipo='previa'",
            (conta_id,)).fetchall()}
        sem_texto = {r[0] for r in c.execute(
            """select prospeccao_id from resgate_envios where conta_id=%s and tipo='erro_texto'
                and criado_em > %s""", (conta_id, agora - timedelta(hours=6))).fetchall()}
        # o número que não recebeu (fora do WhatsApp, bloqueado) não é tentado de novo
        # por FALHA_DIAS: sem isto o mesmo lead seria o 1º da fila a cada passada, e
        # três falhas dele puxariam o freio da empresa inteira
        falhou = {r[0] for r in c.execute(
            """select prospeccao_id from resgate_envios where conta_id=%s and tipo='retomada'
                and not ok and criado_em > %s""",
            (conta_id, agora - timedelta(days=FALHA_DIAS))).fetchall()}
        candidato = None
        for x in fila_de(vencidos):
            if x["id"] in sem_texto or x["id"] in falhou:
                continue
            if not ligado and (x["id"], x["desde"]) in ja_previa:
                continue
            if ligado and not _avisado_a_tempo(c, conta_id, cfg, x, agora):
                continue
            if ligado and not _chip_de_pe(c, conta_id, x["chip_id"]):
                chip_caido = True        # este espera o chip voltar; o próximo pode ir
                continue
            candidato = x
            break
        pode = bool(candidato) and _pode_mandar_agora(c, conta_id, cfg, candidato["id"], agora)
        avisar_chip = False
        if chip_caido and not candidato:
            avisar_chip = not c.execute(
                """select 1 from resgate_envios where conta_id=%s and tipo='freio_chip'
                    and criado_em > %s""", (conta_id, agora - timedelta(hours=6))).fetchone()
            if avisar_chip:
                _registrar(c, conta_id, "freio_chip")
        n_hoje = _contagem_hoje(c, conta_id, ENVIOS, agora) + 1
        vendedor = _nome(c, conta_id, candidato["vendedor_id"]) if candidato else ""
        c.commit()
    if avisar_chip:
        supervisor(pool, conta_id, "📵 Resgate esperando: o número por onde as conversas da fila "
                                   "saem está fora do ar. Volto a chamar quando ele voltar.", cfg=cfg)
    if not pode:
        return
    texto = redigir(pool, conta_id, candidato, regra, agora)
    if not texto:
        with pool.connection() as c:
            _registrar(c, conta_id, "erro_texto", lead=candidato["id"], ref_em=candidato["desde"],
                       ok=False)
            c.commit()
        return
    dias = max(1, (agora - candidato["desde"]).days)
    if ligado:
        if _mandar_retomada(pool, conta_id, cfg, candidato, texto):
            out["retomadas"] += 1
    elif supervisor(pool, conta_id, _previa(candidato, vendedor, texto, n_hoje,
                                            int(cfg.get("teto_dia") or 20), dias),
                    tipo="previa", lead=candidato["id"], ref_em=candidato["desde"], cfg=cfg):
        out["previas"] += 1


def fila_de(vencidos: list[dict]) -> list[dict]:
    def _ordem(x):
        if x["faixa"] == 2 and x.get("evento_em"):
            return (2, x["evento_em"].toordinal(), 0)
        return (x["faixa"], 0, -x["desde"].timestamp())
    return sorted(vencidos, key=_ordem)


def _resumo_do_dia(pool, conta_id: int, cfg: dict, agora: datetime) -> None:
    """Uma vez por dia, a partir do fim do horário do resgate, nos dias dele."""
    hora = agora.astimezone(_BRT)
    if hora.weekday() not in (cfg.get("dias_semana") or []) or hora.hour < int(cfg.get("hora_fim") or 19):
        return
    with pool.connection() as c:
        if _contagem_hoje(c, conta_id, ("resumo",), agora):
            c.commit()
            return
        texto = resumo(c, conta_id, cfg, agora)
        c.commit()
    supervisor(pool, conta_id, texto, tipo="resumo", cfg=cfg)


def rodar(pool, agora: datetime | None = None) -> dict:
    """Um ciclo do resgate em toda empresa com ele em Ensaio ou ligado. Uma trava só
    pro processo inteiro: dois workers do Render não mandam a mesma retomada.

    A TRAVA FICA FORA DE TRANSAÇÃO. O pool abre toda conexão com
    `idle_in_transaction_session_timeout=60s` (db/conexao.py), e um ciclo daqui pode
    passar disso (a IA escrevendo, o envio, os avisos). Numa transação aberta, a
    sessão da trava seria derrubada no meio do ciclo, a trava soltaria e o outro
    worker mandaria uma segunda retomada dentro do espaçamento. Em autocommit a
    trava de sessão vale sem transação nenhuma aberta."""
    agora = agora or datetime.now(timezone.utc)
    total = {"previas": 0, "retomadas": 0, "avisos": 0, "toques": 0, "perdidos": 0}
    with pool.connection() as lk:
        lk.commit()
        lk.autocommit = True
        try:
            try:
                if not lk.execute("select pg_try_advisory_lock(%s)", (_LOCK,)).fetchone()[0]:
                    return total
            except Exception:  # noqa: BLE001
                return total
            try:
                try:
                    with pool.connection() as c:
                        contas = [r[0] for r in c.execute(
                            "select conta_id from resgate_config where modo in ('ensaio','ligado')"
                        ).fetchall()]
                        com_leads = [r[0] for r in c.execute(
                            "select distinct conta_id from resgate_leads where ativo").fetchall()]
                except Exception:  # noqa: BLE001 — banco sem a 396
                    return total
                for conta_id in sorted(set(com_leads) - set(contas)):
                    try:
                        varrer_devolvidos(pool, conta_id)
                    except Exception as e:  # noqa: BLE001
                        _log.warning("resgate.rodar: devolvidos da conta %s: %s", conta_id, e)
                for conta_id in contas:
                    try:
                        r = _uma_conta(pool, conta_id, agora)
                        for k in total:
                            total[k] += r.get(k, 0)
                    except Exception as e:  # noqa: BLE001
                        _log.warning("resgate.rodar: conta %s: %s: %s", conta_id, type(e).__name__, e)
            finally:
                lk.execute("select pg_advisory_unlock(%s)", (_LOCK,))
        finally:
            lk.autocommit = False
    return total


# ══════════════════════════════════════════════════════════════════ o supervisor fala

def e_do_supervisor(c, conta_id: int, numero) -> bool:
    """Esta mensagem que chegou é do supervisor do resgate? Aí ela não vira lead nem
    conversa da empresa: ou é o "Testar comigo", ou é ele respondendo um aviso."""
    chave = chave_do_numero(numero)
    if not chave:
        return False
    cfg = config(c, conta_id)
    return (cfg.get("modo") in ("ensaio", "ligado")
            and chave_do_numero(cfg.get("supervisor_whatsapp")) == chave)


def testar(pool, conta_id: int) -> dict:
    """O "Testar comigo": pega o primeiro da fila, a IA escreve a retomada e manda pro
    supervisor como se ele fosse o cliente. Nada vira lead, nada vai pro cliente."""
    agora = datetime.now(timezone.utc)
    with pool.connection() as c:
        cfg = config(c, conta_id)
        if cfg.get("modo") not in ("ensaio", "ligado"):
            # desligado, a resposta do supervisor não é interceptada e viraria lead
            return {"ok": False, "erro": "Ponha o resgate em Ensaio (ou Ligado) e salve antes de testar."}
        if not cfg.get("supervisor_whatsapp"):
            return {"ok": False, "erro": "Ponha o WhatsApp do supervisor e salve antes de testar."}
        if not cfg.get("membro_id"):
            return {"ok": False, "erro": f"Escolha pra quem vão os {_palavra('leads', conta_id)} do resgate e salve."}
        todos = leads(c, conta_id, cfg, agora)
        vencidos = fila_de([x for x in todos if x["vence_em"] <= agora])
        if not vencidos:
            return {"ok": False, "erro": "A fila está vazia: não há lead parado pra servir de exemplo."}
        lead = vencidos[0]
        regra = regra_do_membro(c, conta_id, cfg["membro_id"])
        c.commit()
    texto = redigir(pool, conta_id, lead, regra, agora)
    if not texto:
        return {"ok": False, "erro": "A IA não conseguiu escrever agora. Tente de novo em instantes."}
    with pool.connection() as c:
        c.execute("delete from resgate_teste where conta_id=%s", (conta_id,))
        c.execute("""insert into resgate_teste (conta_id, numero8, prospeccao_id, historico)
                     values (%s,%s,%s,%s)""",
                  (conta_id, chave_do_numero(cfg["supervisor_whatsapp"]), lead["id"],
                   json.dumps([{"quem": "ia", "texto": texto}], ensure_ascii=False)))
        c.commit()
    supervisor(pool, conta_id, f"🧪 Testar comigo · você é {_primeiro(lead['quem'])} (lead #{lead['id']}).\n"
                               "Responda como o cliente responderia. Nada disso vai pro cliente "
                               "nem vira lead.", tipo="teste", cfg=cfg)
    supervisor(pool, conta_id, texto, tipo="teste", lead=lead["id"], cfg=cfg)
    return {"ok": True}


def responder_supervisor(pool, conta_id: int, texto: str, sid: str | None = None) -> None:
    """O supervisor escreveu. Com um teste aberto, a IA responde como responderia ao
    cliente (sem marcar visita nem mandar orçamento: é só a conversa). Sem teste, um
    lembrete de como testar — no máximo um a cada 6 horas. Nunca levanta.

    A mensagem fica GUARDADA (`resgate_envios`, tipo 'do_supervisor'): não vira
    conversa da empresa, mas também não some. E é por ela que a reentrega do wa-qr (a
    mesma mensagem de novo, quando a conexão oscila) não ganha segunda resposta."""
    try:
        from core.brain import Brain
        from finance import agente as ag
        with pool.connection() as c:
            if sid and c.execute("""select 1 from resgate_envios where conta_id=%s
                                      and tipo='do_supervisor' and erro=%s""",
                                 (conta_id, str(sid)[:200])).fetchone():
                c.commit()
                return
            _registrar(c, conta_id, "do_supervisor", texto=texto, erro=(str(sid)[:200] if sid else None))
            cfg = config(c, conta_id)
            # o histórico cresce por APPEND no banco: duas mensagens seguidas não se
            # atropelam reescrevendo o mesmo jsonb
            t = c.execute("""update resgate_teste set historico = historico || %s::jsonb,
                                    expira_em = now() + interval '2 hours'
                              where id = (select id from resgate_teste
                                           where conta_id=%s and expira_em > now()
                                           order by id desc limit 1)
                          returning id, prospeccao_id, historico""",
                          (json.dumps([{"quem": "cliente", "texto": (texto or "")[:1000]}],
                                      ensure_ascii=False), conta_id)).fetchone()
            if not t:
                lembrou = c.execute("""select 1 from resgate_envios where conta_id=%s and tipo='lembrete'
                                        and criado_em > now() - interval '6 hours'""",
                                    (conta_id,)).fetchone()
                c.commit()
                if not lembrou:
                    supervisor(pool, conta_id, "Este é o número do supervisor do resgate. Pra conversar "
                                               "com a IA como se fosse um cliente, use o botão \"Testar "
                                               "comigo\" no cartão do Resgate.", tipo="lembrete", cfg=cfg)
                return
            hist = list(t[2] or [])
            festa = _perfil_eventos(c, conta_id)
            conv = c.execute("""select id from conversas where prospeccao_id=%s and conta_id=%s
                                 order by ultima_msg_em desc nulls last limit 1""",
                             (t[1], conta_id)).fetchone()
            antes = _historico(c, conv[0]) if conv else "(conversa vazia)"
            system = _system(pool, c, conta_id, festa)
            c.commit()
        agora_txt = "\n".join(("Cliente: " if h["quem"] == "cliente" else "Você: ") + h["texto"]
                              for h in hist)
        passo = "a visita ao espaço" if festa else "uma conversa com a equipe"
        from finance import calendario as _cal
        pedido = (f"Conversa antiga com o cliente:\n{antes}\n\nA RETOMADA (você voltou a falar):\n"
                  f"{agora_txt}{_cal.bloco(antes, agora_txt)}\n\nResponda a última mensagem do cliente. Preço só o liberado, "
                  f"sempre \"a partir de\". O próximo passo é {passo}: pergunte o dia e o "
                  "horário de preferência e diga que a equipe confirma. Mensagem curta.\n"
                  'Retorne APENAS JSON: {"resposta":"texto"}')
        brain = Brain()
        resp = brain.chamar(system=system, mensagens=[{"role": "user", "content": pedido}])
        txt = "".join(getattr(b, "text", "") for b in resp.content
                      if getattr(b, "type", None) == "text").strip()
        resposta = _cal.corrigir((ag._extrair_json(txt).get("resposta") or "").strip()[:1200])
        if not resposta:
            return
        with pool.connection() as c:
            from finance import ia_uso as _iu
            _iu.registrar(c, conta_id, None, t[1], getattr(brain, "model", None), resp)
            c.execute("update resgate_teste set historico = historico || %s::jsonb where id=%s",
                      (json.dumps([{"quem": "ia", "texto": resposta}], ensure_ascii=False), t[0]))
            c.commit()
        supervisor(pool, conta_id, resposta, tipo="teste", lead=t[1], cfg=cfg)
    except Exception as e:  # noqa: BLE001
        _log.info("resgate.responder_supervisor falhou (conta=%s): %s", conta_id, e)


def avisar_supervisor(pool, conta_id: int, prospeccao_id, titulo: str, corpo: str) -> None:
    """A IA precisou de gente num lead do resgate: o supervisor fica sabendo também."""
    try:
        with pool.connection() as c:
            if not e_lead_do_resgate(c, conta_id, prospeccao_id):
                c.commit()
                return
            cfg = config(c, conta_id)
            c.commit()
        supervisor(pool, conta_id, f"🙋 Resgate · lead #{prospeccao_id}\n{titulo}\n{corpo}",
                   lead=prospeccao_id, cfg=cfg)
    except Exception:  # noqa: BLE001
        pass


# ══════════════════════════════════════════════════════════════════ a justificativa

def justificar(c, conta_id: int, lead_id: int, membro_id: int, texto: str) -> dict:
    """"Segurar este lead": o vendedor escreve o motivo e ele vai pro histórico da
    ficha (`prospeccao_atividades`, como nota dele). Só o vendedor do lead segura —
    a nota de outra pessoa não conta pro relógio, então aceitá-la seria mentir."""
    texto = (texto or "").strip()
    if len(texto) < 5:
        return {"ok": False, "erro": "Escreva o motivo (ao menos umas palavras)."}
    r = c.execute("select vendedor_id from prospeccao where id=%s and conta_id=%s",
                  (lead_id, conta_id)).fetchone()
    if not r:
        return {"ok": False, "erro": "Lead não encontrado."}
    if r[0] != membro_id:
        return {"ok": False, "erro": "Só o vendedor do lead pode segurá-lo."}
    c.execute("""insert into prospeccao_atividades (prospeccao_id, membro_id, tipo, descricao)
                 values (%s,%s,'nota',%s)""", (lead_id, membro_id, texto[:400]))
    return {"ok": True}


def situacao_do_lead(c, conta_id: int, lead_id: int, agora: datetime | None = None) -> dict | None:
    """Pro app do vendedor: o lead está a caminho do resgate? {dias, vence_em} quando
    faltam 2 dias ou menos (ou já venceu e espera a vez); None quando não. Só com o
    resgate ligado — no Ensaio nada muda de dono, e avisar seria mentir."""
    cfg = config(c, conta_id)
    if cfg.get("modo") != "ligado":
        return None
    agora = agora or datetime.now(timezone.utc)
    for x in leads(c, conta_id, cfg, agora, lead_id=lead_id):
        if x["id"] == lead_id:
            falta = x["vence_em"] - agora
            if falta > timedelta(days=AVISO_DIAS):
                return None
            return {"dias": max(0, falta.days + (1 if falta.seconds else 0)),
                    "vencido": falta <= timedelta(0)}
    return None


def tela(c, conta_id: int, agora: datetime | None = None) -> dict:
    """O que o cartão do Resgate mostra: a config, o tamanho da fila por faixa, quantos
    estão segurados, o que saiu hoje e o estado dos que estão com a IA."""
    agora = agora or datetime.now(timezone.utc)
    cfg = config(c, conta_id)
    membros = []
    try:
        with c.transaction():
            membros = [{"id": r[0], "nome": r[1]} for r in c.execute(
                """select distinct m.id, coalesce(nullif(m.nome,''), m.email)
                     from chip_regra r join membros m on m.id = r.membro_id and m.ativo
                                           and m.conta_id = r.conta_id
                    where r.conta_id=%s""", (conta_id,)).fetchall()]
    except Exception:  # noqa: BLE001
        pass
    if not cfg.get("membro_id") and len(membros) == 1:
        cfg["membro_id"] = membros[0]["id"]
    todos = leads(c, conta_id, cfg, agora) if cfg.get("membro_id") else []
    vencidos = [x for x in todos if x["vence_em"] <= agora]
    por_faixa = {k: 0 for k in FAIXAS}
    for x in vencidos:
        por_faixa[x["faixa"]] += 1
    hoje = {"envios": 0, "retomadas": 0}
    com_ia = {"total": 0, "responderam": 0, "pararam": 0, "pausados": 0}
    try:
        with c.transaction():
            hoje["envios"] = _contagem_hoje(c, conta_id, ENVIOS, agora)
            hoje["retomadas"] = _contagem_hoje(c, conta_id, ("retomada",), agora, ok=True)
            r = c.execute("""select count(*), count(*) filter (where estado='respondeu'),
                                    count(*) filter (where estado='parou'),
                                    count(*) filter (where estado='pausado')
                               from resgate_leads where conta_id=%s and ativo""", (conta_id,)).fetchone()
            com_ia = {"total": r[0], "responderam": r[1], "pararam": r[2], "pausados": r[3]}
    except Exception:  # noqa: BLE001
        pass
    regra = regra_do_membro(c, conta_id, cfg.get("membro_id")) if cfg.get("membro_id") else None
    sup = cfg.get("supervisor_whatsapp") or ""
    return {"cfg": cfg, "membros": membros, "fila": len(vencidos), "por_faixa": por_faixa,
            "segurados": sum(1 for x in todos if x["segurado"] and x["vence_em"] > agora),
            "a_caminho": sum(1 for x in todos
                             if agora < x["vence_em"] <= agora + timedelta(days=AVISO_DIAS)),
            "hoje": hoje, "com_ia": com_ia,
            "regra_ok": bool(regra and regra.get("ativa") and regra.get("ia_ligada")),
            "supervisor_mascara": (f"({sup[2:4]}) {sup[4:5]}····-{sup[-4:]}" if len(sup) >= 12 else ""),
            "faixas": FAIXAS}
