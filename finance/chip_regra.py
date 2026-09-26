"""A regra por número (migração 388): quem recebe o lead NOVO que entra por um chip,
se a IA atende esse dono, em que horário, e quem ela avisa quando precisa de gente.

POR QUE EXISTE (26/09/2026). A Prime (conta 34) tem dois chips e um rodízio só. O dono
pôs um tráfego pago apontando para o chip CP Thiago (conta 36) e quer que todo contato
novo desse número vá para o "zaq teste", atendido pela IA, pra medir a IA contra a
equipe. Pelo ANÚNCIO não dá pra separar: o QR não traz de qual anúncio a mensagem veio
(finance/origem_anuncio.py). Pelo NÚMERO dá — o chip vem do servidor em toda mensagem
(`_resolver_chip`) e o cliente não consegue mudar.

O QUE A REGRA NUNCA FAZ
  * Não mexe na conexão do chip nem em `canais_config` (CLAUDE.md §1). O chip é só a
    chave de leitura.
  * Não tira lead de ninguém. Vale para o contato NOVO depois de `vale_desde`; quem já
    era lead segue com quem tem, e a conversa antiga que volta a falar (a "retomada")
    segue no rodízio — foi a decisão do dono ("só contatos novos").
  * Não liga a IA na conversa de um lead que não é do dono da regra.

Tudo aqui é tolerante: banco sem a 388 devolve "sem regra", e o webhook segue como
sempre foi. Nada daqui pode derrubar a entrada de uma mensagem.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

_log = logging.getLogger(__name__)

_BRT = timezone(timedelta(hours=-3))

HORARIOS = ("24h", "proprio")

#: Motivos com que a IA chama gente. `agenda` vai para quem cuida do espaço (a
#: Jacqueline, na Prime); `dono` vai para quem decide dinheiro (o Manoel).
MOTIVOS = {
    "visita": ("agenda", "Visita"),
    "agenda": ("agenda", "Agenda"),
    "pessoa": ("agenda", "Pediu uma pessoa"),
    "fora_da_base": ("agenda", "Pergunta fora da base"),
    "reclamacao": ("agenda", "Reclamação"),
    "desconto": ("dono", "Pediu desconto"),
    "sinal": ("dono", "Sinal"),
}

_COLS = ("id, conta_id, chip_id, ativa, membro_id, ia_ligada, ia_horario, ia_dias, "
         "ia_hora_ini, ia_hora_fim, coalesce(ia_fora_texto,''), coalesce(ia_apresentacao,''), "
         "aviso_agenda_membro_id, aviso_dono_membro_id, vale_desde")
_CHAVES = ("id", "conta_id", "chip_id", "ativa", "membro_id", "ia_ligada", "ia_horario",
           "ia_dias", "ia_hora_ini", "ia_hora_fim", "ia_fora_texto", "ia_apresentacao",
           "aviso_agenda_membro_id", "aviso_dono_membro_id", "vale_desde")

#: A marca, em `mensagens.status`, do recado de fora do horário. Pela marca e não pelo
#: texto: o dono pode reescrever o recado com conversas esperando, e o texto antigo
#: deixaria de casar — elas nunca seriam respondidas.
STATUS_FORA = "ia_fora"

#: O texto de fábrica quando a IA está fora do horário próprio. Entra na LEITURA,
#: não na coluna: melhorar o texto alcança quem nunca editou.
FORA_PADRAO = ("Oi! Recebi sua mensagem 😊 Nosso atendimento volta às {hora}h, "
               "e eu te respondo assim que abrir.")


def _dict(r) -> dict:
    d = dict(zip(_CHAVES, r))
    d["ia_dias"] = [int(x) for x in (d["ia_dias"] or [])]
    return d


def regra(c, conta_id: int, chip_id) -> dict | None:
    """A regra ATIVA do chip, ou None. `chip_id` nulo = o chip principal (a empresa).

    Savepoint próprio: sem a tabela (banco sem a 388), a consulta falha e a
    transação do webhook não pode ir junto — ela carrega a mensagem do cliente."""
    chip = int(chip_id) if chip_id else int(conta_id)
    try:
        with c.transaction():
            r = c.execute(f"select {_COLS} from chip_regra "
                          "where conta_id=%s and chip_id=%s and ativa", (conta_id, chip)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    return _dict(r) if r else None


def regra_da_conversa(c, conta_id: int, conversa_id: int) -> dict | None:
    """A regra do chip por onde esta conversa entrou, SE o lead da conversa é do dono
    da regra. É o que decide se as travas e o jeito de falar da regra valem aqui: a
    conversa de um lead que era do Pedro antes da regra continua sendo do Pedro, e a
    IA não mexe nela."""
    try:
        with c.transaction():
            cv = c.execute("""select cv.chip_id, p.vendedor_id,
                                     exists (select 1 from chip_regra_leads l
                                              where l.prospeccao_id = p.id and l.conta_id = cv.conta_id)
                                from conversas cv left join prospeccao p on p.id = cv.prospeccao_id
                               where cv.id=%s and cv.conta_id=%s""",
                           (conversa_id, conta_id)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    # só o lead que a REGRA deu (chip_regra_leads): o lead antigo do mesmo dono, ou um
    # que o gestor moveu pra ele, segue o atendimento de sempre
    if not cv or not cv[2]:
        return None
    r = regra(c, conta_id, cv[0])
    if not r or not r["membro_id"] or cv[1] != r["membro_id"]:
        return None
    return r


def tem_mais_de_um_chip(pool, conta_id: int) -> bool:
    """A empresa tem um segundo chip? É o portão da regra por número — na tela, no
    catálogo ("a IA pode dizer este preço") e no aviso da novidade. Falha fechada."""
    try:
        with pool.connection() as c:
            return bool(c.execute("select 1 from contas where chip_de=%s limit 1",
                                  (conta_id,)).fetchone())
    except Exception:  # noqa: BLE001
        return False


def ia_pode_falar(r: dict, agora: datetime | None = None) -> bool:
    """A IA da regra pode responder agora? '24h' sempre; 'proprio' só nos dias e
    horas escolhidos (hora de Brasília)."""
    if not r or r.get("ia_horario") != "proprio":
        return True
    agora = (agora or datetime.now(timezone.utc)).astimezone(_BRT)
    if agora.weekday() not in (r.get("ia_dias") or []):
        return False
    return int(r.get("ia_hora_ini") or 0) <= agora.hour < int(r.get("ia_hora_fim") or 24)


def texto_fora(r: dict) -> str:
    return (r.get("ia_fora_texto") or "").strip() or FORA_PADRAO.format(
        hora=int(r.get("ia_hora_ini") or 8))


def dono_do_contato_novo(c, conta_id: int, chip_id, *, contato_novo: bool) -> dict | None:
    """Se o lead que ACABOU de nascer por este chip tem dono pela regra, a regra; senão
    None (e o rodízio decide). Só contato novo: retomada e lead que já existia ficam
    de fora (decisão do dono, 26/09). Membro inativo também: lead não cai em quem saiu."""
    if not contato_novo:
        return None
    r = regra(c, conta_id, chip_id)
    if not r or not r["membro_id"]:
        return None
    try:
        with c.transaction():
            ok = c.execute("select 1 from membros where id=%s and conta_id=%s and ativo",
                           (r["membro_id"], conta_id)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not ok:
        return None
    if r.get("vale_desde") and r["vale_desde"] > datetime.now(timezone.utc):
        return None
    return r


def dono_do_gemeo(c, conta_id: int, prospeccao_id: int) -> int | None:
    """O MESMO NÚMERO JÁ É LEAD DE ALGUÉM (pelo outro chip)? Devolve esse vendedor.

    O chip novo cria uma ficha própria pra quem já falava pelo principal (é o que faz
    a resposta sair pelo número certo — ver `_wa_inbound_conversa`). Mas o cliente é
    o mesmo, e a decisão do dono (nota 2 do mockup) é que ele fica com quem já
    atende: a ficha nova vai pro mesmo vendedor, a IA não entra, e ele é avisado."""
    try:
        with c.transaction():
            r = c.execute(
                r"""select g.vendedor_id from prospeccao p
                      join prospeccao g on g.conta_id = p.conta_id and g.id <> p.id
                       and right(regexp_replace(coalesce(g.whatsapp, g.telefone, ''), '\D', '', 'g'), 8)
                         = right(regexp_replace(coalesce(p.whatsapp, p.telefone, ''), '\D', '', 'g'), 8)
                      join membros m on m.id = g.vendedor_id and m.conta_id = g.conta_id and m.ativo
                     where p.id=%s and p.conta_id=%s
                       and length(regexp_replace(coalesce(p.whatsapp, p.telefone, ''), '\D', '', 'g')) >= 8
                     order by g.atualizado_em desc limit 1""",
                (prospeccao_id, conta_id)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    return int(r[0]) if r and r[0] else None


def atribuir(c, conta_id: int, prospeccao_id: int, conversa_id: int | None, r: dict) -> int | None:
    """Dá o lead SEM DONO ao dono da regra e, se a IA está ligada, liga a IA na
    conversa dele — uma vez, aqui, na atribuição. Não religa depois: quem desligou
    a IA numa conversa (ou assumiu) decidiu, e mensagem que chega não desfaz ato de
    gente (ver o update do inbound em web/painel_prospeccao.py)."""
    mid = r["membro_id"]
    n = c.execute("update prospeccao set vendedor_id=%s, atualizado_em=now() "
                  "where id=%s and conta_id=%s and vendedor_id is null",
                  (mid, prospeccao_id, conta_id)).rowcount
    if not n:
        return None
    c.execute("""insert into chip_regra_leads (prospeccao_id, conta_id, chip_id, membro_id)
                 values (%s,%s,%s,%s) on conflict (prospeccao_id) do nothing""",
              (prospeccao_id, conta_id, r["chip_id"], mid))
    if conversa_id:
        c.execute("""update conversas set responsavel_membro_id=%s,
                            agente_ativo = case when %s and status <> 'pendente'
                                                then true else agente_ativo end
                      where id=%s and conta_id=%s""",
                  (mid, bool(r.get("ia_ligada")), conversa_id, conta_id))
    return mid


# ------------------------------------------------------------------ o agente

def _depois_de(c, conversa_id: int, r: dict):
    """A partir de quando a regra olha esta conversa: a última fala da IA, ou o
    início da regra. O que veio antes (histórico do celular, conversa antiga) não
    é "alguém respondeu"."""
    x = c.execute("select max(criado_em) from mensagens where conversa_id=%s and autor='bot'",
                  (conversa_id,)).fetchone()
    return max([t for t in ((x[0] if x else None), r.get("vale_desde")) if t is not None],
               default=None)


def pausar_se_humano(c, conta_id: int, conversa_id: int, r: dict) -> bool:
    """ALGUÉM DA EQUIPE RESPONDEU → a IA sai da conversa. Devolve se pausou.

    Pelo celular (o eco chega como autor 'humano' sem membro) ou pelo painel, dá no
    mesmo: gente assumiu, e a IA falando por cima faria o cliente ouvir duas vozes.
    Fica como o botão "Assumir" deixa: status 'pendente' e IA desligada, pra ninguém
    religar sem querer. O eco da PRÓPRIA IA não conta — `_add_bot_msg` marca a linha
    como bot, e o texto igual a uma fala recente dela é o seguro da corrida."""
    desde = _depois_de(c, conversa_id, r)
    h = c.execute(
        """select 1 from mensagens m
            where m.conversa_id=%s and m.direcao='out' and m.autor='humano'
              and m.criado_em > coalesce(%s::timestamptz, '-infinity')
              and not exists (select 1 from mensagens b
                               where b.conversa_id=m.conversa_id and b.autor='bot'
                                 and b.texto = m.texto
                                 and b.criado_em > now() - interval '1 hour')
            limit 1""", (conversa_id, desde)).fetchone()
    if not h:
        return False
    c.execute("update conversas set status='pendente', agente_ativo=false "
              "where id=%s and conta_id=%s", (conversa_id, conta_id))
    _log.info("chip_regra: IA pausada, alguém da equipe respondeu (conta=%s conversa=%s)",
              conta_id, conversa_id)
    return True


def tem_o_que_responder(c, conversa_id: int, r: dict) -> bool:
    """A última mensagem é do cliente? Numa rajada, cada mensagem acorda o agente;
    a volta que responde lê todas, e as outras não podem responder de novo. O
    recado de fora do horário não conta como resposta: quando a IA abre, ela
    responde o que ficou (ver `pendentes_da_abertura`)."""
    u = c.execute("""select autor, coalesce(texto,''), coalesce(status,'') from mensagens
                      where conversa_id=%s
                      order by criado_em desc, id desc limit 1""", (conversa_id,)).fetchone()
    if not u:
        return False
    return u[0] == "lead" or (u[0] == "bot" and u[2] == STATUS_FORA)


def ja_mandou_fora(c, conversa_id: int, r: dict) -> bool:
    """O recado de fora do horário sai uma vez por noite, não uma por mensagem."""
    x = c.execute("""select 1 from mensagens where conversa_id=%s and autor='bot' and status=%s
                      and criado_em > now() - interval '12 hours' limit 1""",
                  (conversa_id, STATUS_FORA)).fetchone()
    return bool(x)


def pendentes_da_abertura(c, conta_id: int, limite: int = 5) -> list[int]:
    """Conversas que receberam o recado de fora do horário e ainda esperam a IA,
    agora que ela pode falar. Chamado pelo webhook a cada mensagem que entra na
    empresa: não há relógio que acorde a IA na abertura, então a primeira mensagem
    do dia (de qualquer cliente) acorda as que ficaram. Tolerante: sem a 388, []."""
    try:
        with c.transaction():
            regras = [_dict(x) for x in c.execute(
                f"select {_COLS} from chip_regra where conta_id=%s and ativa and ia_ligada "
                "and ia_horario='proprio' and membro_id is not null", (conta_id,)).fetchall()]
            saida = []
            for r in regras:
                if not ia_pode_falar(r):
                    continue
                saida += [x[0] for x in c.execute(
                    """select cv.id from conversas cv
                         join prospeccao p on p.id = cv.prospeccao_id
                        where cv.conta_id=%s and coalesce(cv.chip_id, cv.conta_id)=%s
                          and p.vendedor_id=%s and cv.agente_ativo
                          and cv.status <> 'pendente'
                          and cv.ultima_msg_em > now() - interval '3 days'
                          and exists (select 1 from chip_regra_leads l where l.prospeccao_id = p.id)
                          -- recebeu o recado de fora do horário…
                          and exists (select 1 from mensagens f where f.conversa_id=cv.id
                                         and f.autor='bot' and f.status=%s
                                         and f.criado_em > now() - interval '24 hours')
                          -- …e ninguém respondeu depois: a última fala é o recado ou o
                          -- cliente (quem escreveu de novo durante a noite também espera)
                          and (select case when m.autor='lead' then true
                                           else m.autor='bot' and m.status=%s end
                                 from mensagens m where m.conversa_id=cv.id
                                order by m.criado_em desc, m.id desc limit 1)
                        order by cv.ultima_msg_em limit %s""",
                    (conta_id, r["chip_id"], r["membro_id"], STATUS_FORA, STATUS_FORA,
                     limite)).fetchall()]
            return saida[:limite]
    except Exception:  # noqa: BLE001
        return []


# ------------------------------------------------------------------ tela

def listar(c, conta_id: int, chips: list[dict]) -> list[dict]:
    """Cada chip da conta com a sua regra (ou None), pra tela Regras por número."""
    regras = {}
    try:
        with c.transaction():
            for r in c.execute(f"select {_COLS} from chip_regra where conta_id=%s",
                               (conta_id,)).fetchall():
                d = _dict(r)
                d["vale_desde_txt"] = (d["vale_desde"].astimezone(_BRT).strftime("%d/%m às %H:%M")
                                       if d.get("vale_desde") else "")
                regras[d["chip_id"]] = d
    except Exception:  # noqa: BLE001
        regras = {}
    from finance import ia_visita as _iv
    for d in regras.values():
        d["visita"] = _iv.config_tela(c, d["id"])
    saida = []
    for ch in chips:
        cid = int(ch.get("id") or conta_id)
        saida.append({**ch, "regra": regras.get(cid), "celular_pct": _pct_celular(c, conta_id, cid)})
    return saida


def catalogo_liberado(c, conta_id: int) -> dict | None:
    """Quantos itens ativos do catálogo a IA pode dizer o preço — o aviso da tela
    quando é zero (o caso da Prime em 26/09: 0 de 42)."""
    try:
        with c.transaction():
            r = c.execute("""select count(*), count(*) filter (where agente_diz_preco)
                               from servicos_catalogo where conta_id=%s and ativo""",
                          (conta_id,)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    return {"total": int(r[0] or 0), "liberados": int(r[1] or 0)}


def _pct_celular(c, conta_id: int, chip_id: int) -> int | None:
    """Das mensagens que SAÍRAM por este chip em 30 dias, quantas % foram mandadas
    pelo celular (humano sem membro). Com a IA ligada, é quem responder pelo celular
    que pausa a IA — a tela mostra o tamanho disso antes de ligar."""
    try:
        with c.transaction():
            r = c.execute(
                """select count(*), count(*) filter (where m.autor='humano' and m.membro_id is null)
                     from mensagens m join conversas cv on cv.id = m.conversa_id
                    where cv.conta_id=%s and coalesce(cv.chip_id, cv.conta_id)=%s
                      and cv.canal='whatsapp' and m.direcao='out'
                      and m.criado_em > now() - interval '30 days'""",
                (conta_id, chip_id)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not r or not r[0]:
        return None
    return round(100 * int(r[1]) / int(r[0]))


def salvar(c, conta_id: int, chip_id: int, f: dict) -> dict:
    """Grava a regra do chip. Devolve {ok, erro?}.

    Tudo que vem da tela é conferido contra a conta: o chip tem que ser desta empresa
    (a própria, ou uma conta-chip dela) e todo membro tem que ser dela e ativo. Um id
    trocado à mão não pode pôr o lead de uma empresa no vendedor de outra."""
    chip_ok = c.execute("select 1 from contas where id=%s and (id=%s or chip_de=%s)",
                        (chip_id, conta_id, conta_id)).fetchone()
    if not chip_ok:
        return {"ok": False, "erro": "Esse número não é desta empresa."}

    def _membro(v):
        try:
            mid = int(v or 0)
        except (TypeError, ValueError):
            return None
        if not mid:
            return None
        r = c.execute("select 1 from membros where id=%s and conta_id=%s and ativo",
                      (mid, conta_id)).fetchone()
        return mid if r else False

    membro = _membro(f.get("membro_id"))
    agenda = _membro(f.get("aviso_agenda_membro_id"))
    dono = _membro(f.get("aviso_dono_membro_id"))
    if False in (membro, agenda, dono):
        return {"ok": False, "erro": "Escolha pessoas da equipe desta empresa."}
    ativa = bool(f.get("ativa"))
    ia = bool(f.get("ia_ligada"))
    if ativa and not membro:
        return {"ok": False, "erro": "Escolha quem recebe os leads deste número."}
    horario = f.get("ia_horario") if f.get("ia_horario") in HORARIOS else "24h"
    dias = sorted({int(d) for d in (f.get("ia_dias") or []) if str(d).isdigit() and 0 <= int(d) <= 6})
    try:
        ini = max(0, min(23, int(f.get("ia_hora_ini") or 8)))
        fim = max(1, min(24, int(f.get("ia_hora_fim") or 22)))
    except (TypeError, ValueError):
        ini, fim = 8, 22
    if horario == "proprio" and (not dias or ini >= fim):
        return {"ok": False, "erro": "No horário próprio, escolha os dias e um início antes do fim."}
    if ini >= fim:
        ini, fim = 8, 22
    fora = (f.get("ia_fora_texto") or "").strip()[:500] or None
    apres = (f.get("ia_apresentacao") or "").strip()[:160] or None
    antes = c.execute("select ativa from chip_regra where conta_id=%s and chip_id=%s",
                      (conta_id, chip_id)).fetchone()
    # "Vale para contatos novos a partir de": a data em que a regra LIGOU. Religar
    # depois de desligada recomeça a contar; salvar de novo com ela ligada não mexe.
    religou = ativa and not (antes and antes[0])
    c.execute(
        """insert into chip_regra (conta_id, chip_id, ativa, membro_id, ia_ligada, ia_horario,
                                   ia_dias, ia_hora_ini, ia_hora_fim, ia_fora_texto,
                                   ia_apresentacao, aviso_agenda_membro_id, aviso_dono_membro_id)
           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
           on conflict (conta_id, chip_id) do update set
             ativa=excluded.ativa, membro_id=excluded.membro_id, ia_ligada=excluded.ia_ligada,
             ia_horario=excluded.ia_horario, ia_dias=excluded.ia_dias,
             ia_hora_ini=excluded.ia_hora_ini, ia_hora_fim=excluded.ia_hora_fim,
             ia_fora_texto=excluded.ia_fora_texto, ia_apresentacao=excluded.ia_apresentacao,
             aviso_agenda_membro_id=excluded.aviso_agenda_membro_id,
             aviso_dono_membro_id=excluded.aviso_dono_membro_id,
             atualizado_em=now()""",
        (conta_id, chip_id, ativa, membro or None, ia, horario, dias or [0, 1, 2, 3, 4, 5],
         ini, fim, fora, apres, agenda or None, dono or None))
    if religou:
        c.execute("update chip_regra set vale_desde=now() where conta_id=%s and chip_id=%s",
                  (conta_id, chip_id))
    return {"ok": True}


# ------------------------------------------------------------------ o aviso

def avisar(pool, conta_id: int, r: dict, motivo: str, *, prospeccao_id=None,
           conversa_id=None, resumo: str = "", lead: str = "") -> int | None:
    """A IA precisa de gente: registra e avisa quem a regra manda (push, e-mail e
    WhatsApp). Devolve o membro avisado, ou None.

    Registra PRIMEIRO, e em transação própria: o aviso que não sai (sem push, sem
    e-mail) ainda fica contado no painel do desafio — "por que a IA avisou gente" não
    pode depender de o celular de alguém estar ligado. Nunca levanta."""
    papel, rotulo = MOTIVOS.get(motivo, ("agenda", "A IA precisa de você"))
    mid = (r or {}).get("aviso_dono_membro_id" if papel == "dono" else "aviso_agenda_membro_id")
    try:
        with pool.connection() as c:
            # UM AVISO POR ASSUNTO: a IA devolve o motivo a cada mensagem enquanto o
            # assunto está aberto, e a Jacqueline receberia um push por "ok" do cliente
            if conversa_id and c.execute(
                    """select 1 from ia_avisos where conversa_id=%s and motivo=%s
                        and criado_em > now() - interval '6 hours' limit 1""",
                    (conversa_id, motivo[:40])).fetchone():
                c.commit()
                return None
            with c.transaction():
                c.execute("""insert into ia_avisos (conta_id, conversa_id, prospeccao_id, membro_id,
                                                    motivo, resumo)
                             values (%s,%s,%s,%s,%s,%s)""",
                          (conta_id, conversa_id, prospeccao_id, mid, motivo[:40],
                           (resumo or "")[:600] or None))
            m = None
            if mid:
                m = c.execute("select coalesce(nullif(nome,''), email), email, coalesce(whatsapp,'') "
                              "from membros where id=%s and conta_id=%s and ativo",
                              (mid, conta_id)).fetchone()
            c.commit()
    except Exception as e:  # noqa: BLE001
        _log.warning("chip_regra.avisar: não registrou o aviso (conta=%s, motivo=%s): %s",
                     conta_id, motivo, e)
        return None
    if not m:
        return None
    quem = (lead or "").strip() or "Um cliente"
    titulo = f"🤖 A IA precisa de você · {rotulo}"
    corpo = f"{quem}: {(resumo or '').strip() or rotulo}"
    url = f"/cockpit/lead/{prospeccao_id}" if prospeccao_id else "/cockpit"
    _enviar_aviso(pool, conta_id, mid, m, titulo, corpo, url)
    return mid


def notificar(pool, conta_id: int, membro_id: int, titulo: str, corpo: str, url: str) -> bool:
    """Avisa uma pessoa da equipe de algo que a IA FEZ (marcou, remarcou uma visita) —
    sem entrar em `ia_avisos`, que é a conta de quando a IA PRECISOU de gente. Nunca
    levanta."""
    try:
        with pool.connection() as c:
            m = c.execute("select coalesce(nullif(nome,''), email), email, coalesce(whatsapp,'') "
                          "from membros where id=%s and conta_id=%s and ativo",
                          (membro_id, conta_id)).fetchone()
    except Exception:  # noqa: BLE001
        return False
    if not m:
        return False
    _enviar_aviso(pool, conta_id, membro_id, m, titulo, corpo, url)
    return True


def _enviar_aviso(pool, conta_id: int, mid: int, m, titulo: str, corpo: str, url: str) -> None:
    """Push, e-mail e WhatsApp — cada um no seu try: um canal caído não cala os outros."""
    nome, email, wa = m
    try:
        from finance import cockpit as _ck
        _ck.enviar_push(pool, conta_id, mid, titulo, corpo[:140], url)
    except Exception:  # noqa: BLE001
        pass
    try:
        from finance.email_sender import _app_url
        link = f"{_app_url()}{url}"
    except Exception:  # noqa: BLE001
        link = ""
    if email and "@" in email:
        try:
            from finance import email_sender as es
            es.enviar_aviso(email, titulo, corpo, nome=nome, link=link, link_texto="Abrir a conversa")
        except Exception:  # noqa: BLE001
            pass
    if (wa or "").strip():
        try:
            from finance import whatsapp_out as wo
            with pool.connection() as c2:
                wo.enviar(c2, conta_id, wa, f"{titulo}\n\n{corpo}" + (f"\n\n{link}" if link else ""))
        except Exception as e:  # noqa: BLE001
            _log.info("chip_regra.avisar: WhatsApp não saiu (conta=%s): %s", conta_id, e)
