"""A lista de espera por data: quem quer um dia que a empresa já vendeu, e o
aviso no instante em que esse dia abre.

POR QUE EXISTE (medido na Prime Eventos, 06/09/2026, mockup
docs/mockups/lista_de_espera_por_data.html)
25 leads em jogo pediam datas que a Prime já tinha vendido — 18 datas — e
NENHUM dos 25 tinha recebido proposta: o vendedor via o dia ocupado na agenda e
parava ali, sem oferecer outra data. Ao mesmo tempo, 3 leads esperavam por
16/01, um sábado que tinha aberto por cancelamento quatro dias antes, e ninguém
foi avisado. Só 6 dos 17 sábados seguintes estavam livres.

O QUE ESTE MÓDULO FAZ
1. `data_tomada` / `datas_livres_perto` — a régua, lida da agenda. A conta diz
   quantas festas faz no mesmo dia (`contas.festas_por_dia`); com 1, uma festa
   toma o dia. Conta SEM esse número não usa lista de espera nenhuma, e é assim
   que só a Prime entra nesta rodada (decisão do dono, 06/09).
2. `sincronizar` — no ticker: todo lead em jogo com data tomada entra na lista
   sozinho; quem mudou de data, fechou ou foi perdido sai. Ninguém digita nada.
3. `datas_que_abriram` + `avisar` — quando a festa de um dia é cancelada ou a
   pré-reserva vence sem sinal, o dia volta a ter vaga: cada lead que esperava
   vira push pro SEU vendedor e linha no "responda hoje"; o dono recebe o
   resumo no Telegram (o mesmo caminho que a pré-reserva vencida já usa).

O QUE ELE NÃO FAZ, DE PROPÓSITO
Não reserva, não enfileira por ordem de chegada com prazo, não bloqueia
proposta pra data tomada. O sistema avisa; quem vende é o vendedor — foi o que
o dono decidiu em 06/09. E não encosta em `eventos_agenda`: só lê.

PERFIL: só `eventos` (finance/raio_x_perfil). Quem vende mensalidade não tem
data pra disputar — a regra 6 do CLAUDE.md em ação.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta

_log = logging.getLogger("finance.lista_espera")

#: quantas datas livres sugerir no card do lead
SUGESTOES = 3
#: até onde procurar data livre, pra frente e pra trás (dias)
JANELA_SUGESTAO = 45
#: status de lead que ainda estão em jogo: as etapas de venda do funil de eventos
#: (o funil novo de 27/09/2026 — Qualificado é `ficha_completa`, Visita feita é
#: `visita_feita`) e a própria coluna da lista de espera
ABERTOS = ("novo", "contatado", "ficha_completa", "qualificado", "visita_feita", "proposta",
           "lista_espera")
#: a coluna do funil (funil novo de eventos, parte 2b — docs/mockups/
#: funil_novo_rotinas.html): o card vai pra ela quando o cliente ACEITA esperar
COLUNA = "lista_espera"
#: por que saiu da lista
MOTIVOS_SAIDA = ("fechou", "mudou_data", "desistiu", "atendido")


def festas_por_dia(pool, conta_id: int) -> int | None:
    """Quantas festas a conta faz no mesmo dia — ou None se ela não usa a lista.

    None é o padrão e significa "esta conta não tem lista de espera": nenhuma
    tela aparece, nada é sincronizado, nenhum aviso sai. É o portão que mantém
    fora quem não pediu."""
    try:
        with pool.connection() as c:
            r = c.execute("select festas_por_dia from contas where id = %s", (conta_id,)).fetchone()
        return int(r[0]) if (r and r[0]) else None
    except Exception as e:  # noqa: BLE001
        _log.info("lista_espera: festas_por_dia falhou: %s: %s", type(e).__name__, e)
        return None


def _nicho_slug(pool, conta_id: int) -> str | None:
    """O slug do nicho da conta, numa consulta só.

    Não passa por `empresa.obter_dados_empresa` de propósito: aquilo lê a ficha
    inteira da empresa (uma dúzia de colunas) pra devolver um campo, e roda no
    ticker a cada dois minutos, em toda conta."""
    try:
        with pool.connection() as c:
            r = c.execute("""select n.slug from contas co
                             left join nichos n on n.id = co.nicho_id where co.id = %s""",
                          (conta_id,)).fetchone()
        return (r[0] if r else None) or None
    except Exception:  # noqa: BLE001
        return None


def usa_lista(pool, conta_id: int) -> bool:
    """A conta usa lista de espera? Precisa do número E do perfil de eventos.

    O número sozinho não basta: a regra 6 do CLAUDE.md diz que tela de festa é
    de quem vende festa, e uma consultoria com o campo preenchido por engano não
    ganha uma lista de datas que ela não disputa."""
    if festas_por_dia(pool, conta_id) is None:
        return False
    try:
        from finance.raio_x_perfil import perfil
        return perfil(_nicho_slug(pool, conta_id))["chave"] == "eventos"
    except Exception:  # noqa: BLE001
        return False


# ---------------------------------------------------------------- a régua

def _ocupacao(c, conta_id: int, de: date, ate: date) -> dict[date, int]:
    """Quantas festas por dia, no intervalo. Só festa (tipo_evento preenchido):
    visita e reunião não tomam o salão."""
    rows = c.execute("""
        select (e.inicio at time zone 'America/Sao_Paulo')::date, count(*)
          from eventos_agenda e
         where e.conta_id = %s and e.tipo = 'empresa' and coalesce(e.tipo_evento, '') <> ''
           and coalesce(e.status, 'ativo') in ('ativo', 'pre_reservado')
           and (e.inicio at time zone 'America/Sao_Paulo')::date between %s and %s
         group by 1""", (conta_id, de, ate)).fetchall()
    return {r[0]: int(r[1]) for r in rows}


def _festas_por_data(c, conta_id: int, de: date, ate: date) -> dict[date, list]:
    """As festas de cada dia, com o lead de cada uma (None = festa sem card): é o
    que separa "a data está tomada POR OUTRO" de "a festa é deste lead". Sem isso, o
    cliente que acabou de segurar a própria data entraria na lista de espera dela."""
    out: dict[date, list] = {}
    for d, lead, status, ate_quando in c.execute("""
        select (e.inicio at time zone 'America/Sao_Paulo')::date, e.prospeccao_id,
               coalesce(e.status, 'ativo'), e.pre_reserva_ate
          from eventos_agenda e
         where e.conta_id = %s and e.tipo = 'empresa' and coalesce(e.tipo_evento, '') <> ''
           and coalesce(e.status, 'ativo') in ('ativo', 'pre_reservado')
           and (e.inicio at time zone 'America/Sao_Paulo')::date between %s and %s""",
            (conta_id, de, ate)).fetchall():
        out.setdefault(d, []).append({"lead": lead, "pre": status == "pre_reservado",
                                      "ate": ate_quando})
    return out


def _de_outros(festas: list, lead_id: int) -> list:
    return [f for f in festas if f["lead"] != lead_id]


def data_tomada(pool, conta_id: int, dia: date | None) -> dict | None:
    """A data está tomada? Devolve {tomada, festas, limite, o_que} ou None quando
    a conta não usa lista (ou não há data)."""
    if not dia:
        return None
    limite = festas_por_dia(pool, conta_id)
    if limite is None:
        return None
    try:
        with pool.connection() as c:
            ocup = _ocupacao(c, conta_id, dia, dia)
            n = ocup.get(dia, 0)
            o_que = ""
            if n:
                r = c.execute("""
                    select coalesce(nullif(e.tipo_evento, ''), 'festa') from eventos_agenda e
                     where e.conta_id = %s and e.tipo = 'empresa' and coalesce(e.tipo_evento,'') <> ''
                       and coalesce(e.status,'ativo') in ('ativo','pre_reservado')
                       and (e.inicio at time zone 'America/Sao_Paulo')::date = %s
                     order by e.inicio limit 1""", (conta_id, dia)).fetchone()
                o_que = (r[0] if r else "festa")
    except Exception as e:  # noqa: BLE001
        _log.info("lista_espera: data_tomada falhou: %s: %s", type(e).__name__, e)
        return None
    return {"tomada": n >= limite, "festas": n, "limite": limite, "o_que": o_que, "data": dia}


def tomada_para(pool, conta_id: int, lead_id: int, dia: date | None) -> dict | None:
    """`data_tomada` do ponto de vista DESTE lead: a festa ou a pré-reserva dele não
    conta — quem segurou a própria data não está "com a data tomada"."""
    if not dia:
        return None
    limite = festas_por_dia(pool, conta_id)
    if limite is None:
        return None
    try:
        with pool.connection() as c:
            outros = _de_outros(_festas_por_data(c, conta_id, dia, dia).get(dia, []), lead_id)
    except Exception as e:  # noqa: BLE001
        _log.info("lista_espera: tomada_para falhou: %s: %s", type(e).__name__, e)
        return None
    pre = [f["ate"] for f in outros if f["pre"] and f["ate"]]
    return {"tomada": len(outros) >= limite, "festas": len(outros), "limite": limite,
            "o_que": "pré-reserva" if outros and len(pre) == len(outros) else "festa",
            "reserva_vence": min(pre) if pre and len(pre) == len(outros) else None, "data": dia}


def datas_livres_perto(pool, conta_id: int, dia: date | None, quantas: int = SUGESTOES,
                       hoje: date | None = None) -> list[dict]:
    """As datas livres mais próximas da que o cliente pediu — a resposta pronta
    pra "e se não der 10/10?".

    ORDEM: o mesmo dia da semana primeiro (quem pede sábado quer sábado), depois
    a distância. Nunca sugere data no passado."""
    limite = festas_por_dia(pool, conta_id)
    if not dia or limite is None:
        return []
    hoje = hoje or date.today()
    de, ate = max(hoje, dia - timedelta(days=JANELA_SUGESTAO)), dia + timedelta(days=JANELA_SUGESTAO)
    if de > ate:
        return []
    try:
        with pool.connection() as c:
            ocup = _ocupacao(c, conta_id, de, ate)
    except Exception as e:  # noqa: BLE001
        _log.info("lista_espera: datas livres falhou: %s: %s", type(e).__name__, e)
        return []
    cand = []
    d = de
    while d <= ate:
        if d != dia and ocup.get(d, 0) < limite:
            cand.append(d)
        d += timedelta(days=1)
    # empate na distância: a data DEPOIS ganha. Quem marcou uma festa pra outubro
    # não costuma poder antecipar pra setembro — mudar pra frente é o que o cliente
    # aceita, e é o que o vendedor consegue oferecer.
    cand.sort(key=lambda x: (x.weekday() != dia.weekday(), abs((x - dia).days), x < dia, x))
    return [{"data": x, "dias": (x - dia).days, "mesmo_dia_semana": x.weekday() == dia.weekday()}
            for x in cand[:quantas]]


# ---------------------------------------------------------------- entrar e sair

def entrar(pool, conta_id: int, lead_id: int, dia: date) -> bool:
    """Põe o lead na lista daquela data. Idempotente: se já está (e não saiu),
    não faz nada; se tinha saído, volta — e volta como espera NOVA (`avisado_em`
    zera): a data que abriu e fechou de novo tem que avisar de novo quando reabrir."""
    try:
        with pool.connection() as c:
            c.execute("""
                insert into lista_espera_data (conta_id, prospeccao_id, data)
                values (%s, %s, %s)
                on conflict (prospeccao_id, data) do update
                   set saiu_em = null, saiu_motivo = null, avisado_em = null,
                       entrou_em = coalesce(lista_espera_data.entrou_em, now())
                 where lista_espera_data.saiu_em is not null""", (conta_id, lead_id, dia))
            c.commit()
        return True
    except Exception as e:  # noqa: BLE001
        _log.info("lista_espera: entrar falhou (lead %s): %s: %s", lead_id, type(e).__name__, e)
        return False


def sair(pool, conta_id: int, lead_id: int, motivo: str, dia: date | None = None) -> int:
    """Tira o lead da lista (de uma data, ou de todas). A linha vira histórico —
    nunca é apagada: "quantos desistiram desta data" é o que orienta o preço."""
    if motivo not in MOTIVOS_SAIDA:
        motivo = "desistiu"
    try:
        with pool.connection() as c:
            r = c.execute(f"""
                update lista_espera_data set saiu_em = now(), saiu_motivo = %s
                 where conta_id = %s and prospeccao_id = %s and saiu_em is null
                   {"and data = %s" if dia else ""}""",
                ((motivo, conta_id, lead_id, dia) if dia else (motivo, conta_id, lead_id)))
            n = r.rowcount
            c.commit()
        return int(n or 0)
    except Exception as e:  # noqa: BLE001
        _log.info("lista_espera: sair falhou (lead %s): %s: %s", lead_id, type(e).__name__, e)
        return 0


def sincronizar(pool, conta_id: int, hoje: date | None = None) -> dict:
    """Põe na lista todo lead em jogo com data tomada, e tira quem não cabe mais.

    Roda no ticker. É o que faz a lista existir sem ninguém digitar: no primeiro
    ciclo depois do deploy, os 25 leads da Prime entram sozinhos.

    SAI quem: fechou (ganho), mudou a data, ou a data que pedia deixou de estar
    tomada por outro motivo que não a abertura (a festa foi remarcada pra outro
    dia, por exemplo — aí ele já pode ter a data e não é mais espera)."""
    if festas_por_dia(pool, conta_id) is None:
        return {"entraram": 0, "sairam": 0}
    hoje = hoje or date.today()
    limite = festas_por_dia(pool, conta_id)
    entraram = saíram = 0
    try:
        with pool.connection() as c:
            leads = c.execute("""
                select id, evento_em, status from prospeccao
                 where conta_id = %s and evento_em is not null and evento_em >= %s""",
                (conta_id, hoje)).fetchall()
            if leads:
                de = min(l[1] for l in leads)
                ate = max(l[1] for l in leads)
                festas = _festas_por_data(c, conta_id, de, ate)
            else:
                festas = {}
            linhas = c.execute(
                "select prospeccao_id, data, avisado_em from lista_espera_data "
                "where conta_id = %s and saiu_em is null", (conta_id,)).fetchall()
            na_lista = {(r[0], r[1]) for r in linhas}
            # A DATA ABRIU E NINGUÉM FOI AVISADO AINDA: a linha fica até o aviso sair.
            # Antes, a sincronização (que roda antes do aviso) tirava esse lead da
            # lista como "atendido" no mesmo ciclo em que a data abria — e o aviso,
            # que só olha quem ainda está na lista, nunca saía.
            nao_avisados = {(r[0], r[1]) for r in linhas if r[2] is None}
        for lid, dia, status in leads:
            # tomada POR OUTRO: a festa (ou pré-reserva) do próprio lead não conta
            tomada = len(_de_outros(festas.get(dia, []), lid)) >= limite
            em_jogo = status in ABERTOS
            # perdido por "data indisponível" continua esperando: o cliente ainda
            # quer aquele dia, e é justamente quem avisar quando abrir
            if not em_jogo and status == "perdido":
                em_jogo = _perdido_pela_data(pool, conta_id, lid)
            if tomada and em_jogo and (lid, dia) not in na_lista:
                if entrar(pool, conta_id, lid, dia):
                    entraram += 1
            elif (lid, dia) in na_lista and not (tomada and em_jogo):
                if em_jogo and not tomada and (lid, dia) in nao_avisados:
                    continue            # abriu: o aviso sai primeiro (`avisar`)
                if status == COLUNA and not tomada:
                    # quem ACEITOU esperar continua na fila com a data aberta: o 1º
                    # voltou pra Proposta (`avisar`), e o 2º é o próximo se ela fechar
                    # de novo sem ele
                    continue
                motivo = ("fechou" if status == "ganho" else "atendido" if not tomada else "desistiu")
                saíram += sair(pool, conta_id, lid, motivo, dia)
        # lead que MUDOU de data: a linha antiga não aparece mais no laço acima
        atuais = {(l[0], l[1]) for l in leads}
        for lid, dia in na_lista - atuais:
            saíram += sair(pool, conta_id, lid, "mudou_data", dia)
    except Exception as e:  # noqa: BLE001
        _log.info("lista_espera: sincronizar falhou (conta %s): %s: %s", conta_id, type(e).__name__, e)
    return {"entraram": entraram, "sairam": saíram}


def _perdido_pela_data(pool, conta_id: int, lead_id: int) -> bool:
    """O lead foi perdido porque a data não estava livre?

    Vale o que o VENDEDOR marcou; quando ele não marcou nada (ou marcou "Outro"),
    vale o que a leitura da conversa encontrou (`perda_lida`, migração 328) — a
    mesma precedência da tela "Por que perdemos". Sem isso, o cliente que disse
    "😔 só falta a data" e ficou sem motivo marcado nunca seria avisado."""
    try:
        with pool.connection() as c:
            try:
                with c.transaction():
                    r = c.execute("select perda_motivo, perda_lida from prospeccao "
                                  "where id = %s and conta_id = %s", (lead_id, conta_id)).fetchone()
            except Exception:  # noqa: BLE001 — base sem a 328: só o do vendedor
                r = c.execute("select perda_motivo, null from prospeccao where id = %s and conta_id = %s",
                              (lead_id, conta_id)).fetchone()
    except Exception:  # noqa: BLE001
        return False
    if not r:
        return False
    marcado = (r[0] or "").strip()
    if marcado and marcado != "outro":
        return marcado == "data_indisponivel"
    return r[1] == "data_indisponivel"


# ---------------------------------------------------------------- a lista

def por_data(pool, conta_id: int, hoje: date | None = None, limite_datas: int = 60) -> list[dict]:
    """A lista agrupada por data, pro painel: quem espera, desde quando, com
    quem, se a data está tomada ou já abriu, e as livres perto."""
    if festas_por_dia(pool, conta_id) is None:
        return []
    hoje = hoje or date.today()
    limite = festas_por_dia(pool, conta_id)
    try:
        with pool.connection() as c:
            rows = c.execute("""
                select l.data, l.prospeccao_id, l.entrou_em, l.avisado_em,
                       coalesce(nullif(p.contato, ''), nullif(p.empresa, ''), 'lead'),
                       coalesce(nullif(p.evento_tipo, ''), ''), coalesce(nullif(m.nome, ''), '—'),
                       p.orcamento_id, p.status
                  from lista_espera_data l
                  join prospeccao p on p.id = l.prospeccao_id
                  left join membros m on m.id = p.vendedor_id
                 where l.conta_id = %s and l.saiu_em is null and l.data >= %s
                 order by l.data, l.entrou_em""", (conta_id, hoje)).fetchall()
            if not rows:
                return []
            ocup = _ocupacao(c, conta_id, min(r[0] for r in rows), max(r[0] for r in rows))
    except Exception as e:  # noqa: BLE001
        _log.info("lista_espera: por_data falhou: %s: %s", type(e).__name__, e)
        return []
    datas: dict[date, dict] = {}
    for dia, lid, entrou, avisado, nome, tipo, vend, orc, status in rows:
        d = datas.setdefault(dia, {"data": dia, "tomada": ocup.get(dia, 0) >= limite,
                                   "festas": ocup.get(dia, 0), "limite": limite, "quem": []})
        d["quem"].append({"lead_id": lid, "nome": nome, "tipo": tipo, "vendedor": vend,
                          "dias": (datetime.now(entrou.tzinfo) - entrou).days if entrou else 0,
                          "com_proposta": bool(orc), "status": status, "avisado": bool(avisado)})
    out = []
    for dia, d in sorted(datas.items())[:limite_datas]:
        d["n"] = len(d["quem"])
        d["abriu"] = not d["tomada"]
        d["livres"] = [] if d["abriu"] else datas_livres_perto(pool, conta_id, dia, hoje=hoje)
        out.append(d)
    # o que abriu primeiro: é onde a ação está
    out.sort(key=lambda x: (not x["abriu"], x["data"]))
    return out


def esperando_por(pool, conta_id: int, lead_id: int) -> list[date]:
    """As datas pelas quais este lead espera (pro card do app)."""
    try:
        with pool.connection() as c:
            return [r[0] for r in c.execute(
                """select data from lista_espera_data
                    where conta_id = %s and prospeccao_id = %s and saiu_em is null order by data""",
                (conta_id, lead_id)).fetchall()]
    except Exception:  # noqa: BLE001
        return []


# ---------------------------------------------------------------- a data abriu

def datas_que_abriram(pool, conta_id: int, hoje: date | None = None) -> list[dict]:
    """Datas com gente esperando que VOLTARAM a ter vaga e ninguém foi avisado.

    Não pergunta "o que aconteceu": pergunta se hoje há vaga. Assim o
    cancelamento à mão, a pré-reserva vencida e a festa remarcada pra outro dia
    caem todos no mesmo caminho, sem três gatilhos pra manter."""
    if festas_por_dia(pool, conta_id) is None:
        return []
    hoje = hoje or date.today()
    limite = festas_por_dia(pool, conta_id)
    try:
        with pool.connection() as c:
            rows = c.execute("""
                select l.id, l.data, l.prospeccao_id, l.entrou_em, p.vendedor_id,
                       coalesce(nullif(p.contato, ''), nullif(p.empresa, ''), 'lead'),
                       coalesce(nullif(p.evento_tipo, ''), ''), p.status
                  from lista_espera_data l join prospeccao p on p.id = l.prospeccao_id
                 where l.conta_id = %s and l.saiu_em is null and l.avisado_em is null
                   and l.data >= %s
                 order by l.data, l.entrou_em""", (conta_id, hoje)).fetchall()
            if not rows:
                return []
            festas = _festas_por_data(c, conta_id, min(r[1] for r in rows), max(r[1] for r in rows))
    except Exception as e:  # noqa: BLE001
        _log.info("lista_espera: datas_que_abriram falhou: %s: %s", type(e).__name__, e)
        return []
    return [{"id": r[0], "data": r[1], "lead_id": r[2], "entrou_em": r[3], "vendedor_id": r[4],
             "nome": r[5], "tipo": r[6], "status": r[7],
             "dias_esperando": (datetime.now(r[3].tzinfo) - r[3]).days if r[3] else 0}
            for r in rows if len(_de_outros(festas.get(r[1], []), r[2])) < limite]


def _marcar_avisado(pool, ids: list[int]) -> int:
    """Marca as linhas como avisadas — e é a TRAVA contra aviso em dobro: o web
    roda com dois workers e o ticker roda nos dois; quem consegue o update avisa."""
    if not ids:
        return 0
    try:
        with pool.connection() as c:
            r = c.execute("""update lista_espera_data set avisado_em = now()
                              where id = any(%s) and avisado_em is null returning id""", (ids,)).fetchall()
            c.commit()
        return len(r)
    except Exception as e:  # noqa: BLE001
        _log.info("lista_espera: marcar avisado falhou: %s: %s", type(e).__name__, e)
        return 0


def _texto_push(x: dict) -> tuple[str, str]:
    tipo = f"{x['tipo']} · " if x["tipo"] else ""
    return ("📅 A data " + x["data"].strftime("%d/%m") + " abriu",
            f"{x['nome']} ({tipo}esperava há {x['dias_esperando']} dia(s)). "
            "Avise antes que feche em outro lugar.")


def avisar(pool, conta_id: int, hoje: date | None = None, push=None, telegram=None) -> int:
    """Avisa quem esperava por uma data que abriu. Devolve quantos foram avisados.

    Cada lead vira push pro SEU vendedor; o dono recebe uma linha só, com todos,
    no Telegram. `push`/`telegram` existem pro teste — em produção são os
    caminhos que já existem (cockpit.enviar_push e notificar.enviar_para_dono)."""
    abertas = datas_que_abriram(pool, conta_id, hoje)
    if not abertas:
        return 0
    # a trava primeiro: só avisa quem esta instância conseguiu marcar
    marcados = set()
    try:
        with pool.connection() as c:
            r = c.execute("""update lista_espera_data set avisado_em = now()
                              where id = any(%s) and avisado_em is null returning id""",
                          ([x["id"] for x in abertas],)).fetchall()
            c.commit()
        marcados = {x[0] for x in r}
    except Exception as e:  # noqa: BLE001
        _log.info("lista_espera: trava do aviso falhou: %s: %s", type(e).__name__, e)
        return 0
    meus = [x for x in abertas if x["id"] in marcados]
    if not meus:
        return 0
    if push is None:
        from finance import cockpit as _ck
        def push(conta, membro, titulo, corpo):  # noqa: E306
            return _ck.enviar_push(pool, conta, membro, titulo, corpo)
    if telegram is None:
        from finance import notificar as _nt
        def telegram(conta, texto):  # noqa: E306
            return _nt.enviar_para_dono(pool, conta, texto)
    for x in meus:
        if not x["vendedor_id"]:
            continue
        try:
            titulo, corpo = _texto_push(x)
            push(conta_id, x["vendedor_id"], titulo, corpo)
        except Exception as e:  # noqa: BLE001 — aviso que falha não desfaz a marca
            _log.info("lista_espera: push falhou (lead %s): %s: %s", x["lead_id"], type(e).__name__, e)
    # O 1º DA FILA (quem ACEITOU esperar e está na coluna) volta pra Proposta: a data
    # é dele até alguém dizer o contrário. Os outros da coluna continuam esperando.
    primeiros: dict[date, dict] = {}
    for x in sorted(meus, key=lambda y: (y["data"], y["entrou_em"] or datetime.max)):
        if x.get("status") == COLUNA and x["data"] not in primeiros:
            primeiros[x["data"]] = x
    for x in primeiros.values():
        try:
            voltar_pra_proposta(pool, conta_id, x)
        except Exception as e:  # noqa: BLE001
            _log.info("lista_espera: 1º da fila falhou (lead %s): %s: %s",
                      x["lead_id"], type(e).__name__, e)
    try:
        por_dia: dict[date, list[str]] = {}
        for x in meus:
            por_dia.setdefault(x["data"], []).append(x["nome"])
        linhas = [f"📅 *{d:%d/%m}* abriu — esperando: " + ", ".join(nomes)
                  for d, nomes in sorted(por_dia.items())]
        telegram(conta_id, "A data abriu na lista de espera:\n" + "\n".join(linhas)
                 + "\n\nOs vendedores foram avisados no app.")
    except Exception as e:  # noqa: BLE001
        _log.info("lista_espera: telegram falhou: %s: %s", type(e).__name__, e)
    return len(meus)


def rodar(pool, hoje: date | None = None) -> dict:
    """Chamado pelo ticker do web: sincroniza e avisa, em toda conta que usa a
    lista. Best-effort por conta — uma que falhe não segura as outras."""
    out = {"contas": 0, "entraram": 0, "sairam": 0, "avisados": 0}
    try:
        with pool.connection() as c:
            contas = [r[0] for r in c.execute(
                "select id from contas where festas_por_dia is not null").fetchall()]
    except Exception as e:  # noqa: BLE001
        _log.info("lista_espera.rodar: sem contas: %s: %s", type(e).__name__, e)
        return out
    for conta_id in contas:
        if not usa_lista(pool, conta_id):
            continue
        out["contas"] += 1
        # o aviso ANTES da sincronização: a data que abriu é avisada no mesmo ciclo
        try:
            out["avisados"] += avisar(pool, conta_id, hoje)
        except Exception as e:  # noqa: BLE001
            _log.info("lista_espera.rodar: avisar falhou (conta %s): %s: %s", conta_id, type(e).__name__, e)
        s = sincronizar(pool, conta_id, hoje)
        out["entraram"] += s["entraram"]
        out["sairam"] += s["sairam"]
        out["passaram"] = out.get("passaram", 0) + data_passou(pool, conta_id, hoje)
        out["voltaram"] = out.get("voltaram", 0) + fora_da_espera(pool, conta_id, hoje)
    return out


def fora_da_espera(pool, conta_id: int, hoje: date | None = None) -> int:
    """O card na coluna que NÃO espera mais nada: a data da festa mudou (ou foi
    apagada) e não há fila dele pra data nova. Sem isto ele ficaria preso na coluna
    pra sempre — nenhum gatilho anda pra trás. Volta pra Proposta, com a nota."""
    from finance import funil_regua as fr
    hoje = hoje or date.today()
    n = 0
    try:
        with pool.connection() as c:
            rows = c.execute(
                """select p.id, p.evento_em from prospeccao p
                    where p.conta_id=%s and p.status=%s
                      and (p.evento_em is null or p.evento_em >= %s)
                      and not exists (select 1 from lista_espera_data l
                                       where l.conta_id = p.conta_id and l.prospeccao_id = p.id
                                         and l.data = p.evento_em and l.saiu_em is null)""",
                (conta_id, COLUNA, hoje)).fetchall()
            for lid, dia in rows:
                if c.execute("""update prospeccao set status='proposta', atualizado_em=now()
                                 where id=%s and conta_id=%s and status=%s""",
                             (lid, conta_id, COLUNA)).rowcount:
                    fr.registrar_movimento(c, conta_id, lid, COLUNA, "proposta", "lista_espera")
                    _nota(c, lid, None, (f"A data mudou pra {dia:%d/%m}: saiu da lista de espera."
                                         if dia else "A data da festa foi apagada: saiu da lista de espera."))
                    n += 1
            c.commit()
    except Exception as e:  # noqa: BLE001
        _log.info("lista_espera: fora_da_espera falhou (conta %s): %s: %s", conta_id, type(e).__name__, e)
    return n


# ---------------------------------------------------------------- a coluna

def tem_coluna(c, conta_id: int) -> bool:
    return bool(c.execute("select 1 from funil_etapas where conta_id=%s and chave=%s",
                          (conta_id, COLUNA)).fetchone())


def _nota(c, lead_id: int, membro_id, texto: str) -> None:
    try:
        with c.transaction():
            c.execute("""insert into prospeccao_atividades (prospeccao_id, membro_id, tipo, descricao)
                         values (%s,%s,'nota',%s)""", (lead_id, membro_id, texto))
    except Exception:  # noqa: BLE001 — a nota é o enfeite; o card anda sem ela
        pass


def aceitar(pool, conta_id: int, lead_id: int, membro_id: int | None = None) -> dict:
    """O cliente ACEITOU esperar a data que pediu (mockup, seção "A proposta e a
    data"): o card vai pra coluna Lista de espera e entra na fila daquela data. Só
    com a data tomada POR OUTRO — esperar a própria reserva não é espera."""
    from finance import funil_regua as fr
    if not usa_lista(pool, conta_id):
        return {"ok": False, "erro": "A conta não usa lista de espera (Empresa › festas por dia)."}
    with pool.connection() as c:
        r = c.execute("""select status, evento_em, estagio from prospeccao
                          where id=%s and conta_id=%s""", (lead_id, conta_id)).fetchone()
        if not r or r[2] != "lead" or r[0] not in ABERTOS:
            return {"ok": False, "erro": "Esse card não está em jogo."}
        status, dia = r[0], r[1]
        if not dia:
            return {"ok": False, "erro": "O card não tem a data da festa."}
        limite = festas_por_dia(pool, conta_id)
        if len(_de_outros(_festas_por_data(c, conta_id, dia, dia).get(dia, []), lead_id)) < limite:
            return {"ok": False, "erro": f"A data {dia:%d/%m} está livre: dá pra seguir a venda."}
        c.commit()
    entrar(pool, conta_id, lead_id, dia)
    with pool.connection() as c:
        if status != COLUNA and tem_coluna(c, conta_id):
            if c.execute("""update prospeccao set status=%s, atualizado_em=now()
                             where id=%s and conta_id=%s and status=%s""",
                         (COLUNA, lead_id, conta_id, status)).rowcount:
                fr.registrar_movimento(c, conta_id, lead_id, status, COLUNA, "manual", membro_id)
        _nota(c, lead_id, membro_id, f"Aceitou esperar a data {dia:%d/%m} (lista de espera).")
        c.commit()
    return {"ok": True, "data": dia}


TEXTO_ABRIU = ("Oi{nome}! A data que você queria, {dia}, abriu 🎉 Ainda quer? "
               "Se quiser, já seguro pra você.")
_SEMANA = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")


def voltar_pra_proposta(pool, conta_id: int, x: dict) -> bool:
    """O 1º da fila, quando a data abre: volta pra Proposta, com a nota. Se o lead é
    da IA do número (modelo 2), a IA chama o cliente — é a conversa dela; o do
    vendedor recebe o push do `avisar`, e quem chama é ele."""
    from finance import funil_regua as fr
    lead = x["lead_id"]
    with pool.connection() as c:
        movido = c.execute("""update prospeccao set status='proposta', atualizado_em=now()
                               where id=%s and conta_id=%s and status=%s""",
                           (lead, conta_id, COLUNA)).rowcount
        if not movido:
            c.rollback()
            return False
        fr.registrar_movimento(c, conta_id, lead, COLUNA, "proposta", "lista_espera")
        _nota(c, lead, None, f"A data {x['data']:%d/%m} abriu: era o 1º da fila de espera. "
                             "Chame antes que feche em outro lugar.")
        c.commit()
    try:
        from finance import chip_regra as _cr
        with pool.connection() as c:
            da_ia = x.get("vendedor_id") in _cr.membros_ia(c, conta_id)
            cv = c.execute("""select id from conversas where conta_id=%s and prospeccao_id=%s
                                and canal='whatsapp' and coalesce(agente_ativo,false)
                              order by id desc limit 1""", (conta_id, lead)).fetchone()
            c.commit()
        if da_ia and cv:
            from finance import agente
            from finance import ia_visita as _iv
            from finance.voltar_a_chamar import primeiro_nome
            nome = primeiro_nome(x.get("nome"))
            d = x["data"]
            texto = TEXTO_ABRIU.format(nome=(", " + nome) if nome else "",
                                       dia=f"{_SEMANA[d.weekday()]} {d:%d/%m}")
            with pool.connection() as c:
                res = _iv._mandar(c, conta_id, cv[0], texto)
                if res.get("ok"):
                    agente._add_bot_msg(c, cv[0], "whatsapp", texto, res.get("sid"))
                c.commit()
    except Exception as e:  # noqa: BLE001 — o card já voltou; a mensagem é o extra
        _log.info("lista_espera: a IA chamar falhou (lead %s): %s: %s", lead, type(e).__name__, e)
    return True


def data_passou(pool, conta_id: int, hoje: date | None = None) -> int:
    """Quem esperava uma data que PASSOU vai pra Perdido, "data indisponível" — a
    mesma porta e o mesmo histórico do perdido de sempre."""
    from finance import funil_perda as _perda
    from finance import funil_regua as fr
    hoje = hoje or date.today()
    n = 0
    try:
        with pool.connection() as c:
            ids = [r[0] for r in c.execute(
                """select id from prospeccao where conta_id=%s and status=%s
                     and evento_em is not null and evento_em < %s""",
                (conta_id, COLUNA, hoje)).fetchall()]
            for lid in ids:
                if c.execute("""update prospeccao set status='perdido', atualizado_em=now()
                                 where id=%s and conta_id=%s and status=%s""",
                             (lid, conta_id, COLUNA)).rowcount:
                    fr.registrar_movimento(c, conta_id, lid, COLUNA, "perdido", "lista_espera")
                    _perda.registrar(c, conta_id, lid, motivo="data_indisponivel",
                                     etapa_origem=COLUNA)
                    n += 1
            c.commit()
    except Exception as e:  # noqa: BLE001
        _log.info("lista_espera: data_passou falhou (conta %s): %s: %s", conta_id, type(e).__name__, e)
    return n


def selos(c, conta_id: int, cards: list[dict], hoje: date | None = None) -> dict:
    """O selo da data no card do funil, numa consulta pro quadro inteiro:

        📅 data ocupada · 13/02            o card em jogo pede uma data tomada por outro
        ⏳ 1º da fila · 13/02              na coluna, a posição na fila daquela data
           · a reserva do outro vence 20/10  quando quem segura é uma pré-reserva

    `cards` são os do quadro ({id, status, evento_em}). Sem a conta usar a lista, ou
    sem as tabelas, nenhum selo."""
    hoje = hoje or date.today()
    try:
        with c.transaction():
            r = c.execute("select festas_por_dia from contas where id=%s", (conta_id,)).fetchone()
            limite = int(r[0]) if (r and r[0]) else None
            if not limite:
                return {}
            alvo = [x for x in cards if x.get("evento_em") and x["evento_em"] >= hoje
                    and x.get("status") in ABERTOS]
            if not alvo:
                return {}
            festas = _festas_por_data(c, conta_id, min(x["evento_em"] for x in alvo),
                                      max(x["evento_em"] for x in alvo))
            fila: dict[date, list[int]] = {}
            for lid, d in c.execute(
                    """select l.prospeccao_id, l.data from lista_espera_data l
                         join prospeccao p on p.id = l.prospeccao_id and p.conta_id = l.conta_id
                        where l.conta_id=%s and l.saiu_em is null and p.status=%s
                        order by l.data, l.entrou_em""", (conta_id, COLUNA)).fetchall():
                fila.setdefault(d, []).append(lid)
    except Exception:  # noqa: BLE001
        return {}
    out = {}
    for x in alvo:
        d = x["evento_em"]
        outros = _de_outros(festas.get(d, []), x["id"])
        if x["status"] == COLUNA:
            pos = fila.get(d, []).index(x["id"]) + 1 if x["id"] in fila.get(d, []) else None
            txt = (f"⏳ {pos}º da fila · {d:%d/%m}" if pos else f"⏳ esperando {d:%d/%m}")
            pre = [f for f in outros if f["pre"] and f["ate"]]
            if pre and len(outros) == len(pre):
                txt += f" · a reserva do outro vence {min(f['ate'] for f in pre):%d/%m}"
            if len(outros) < limite:
                txt = f"📅 a data {d:%d/%m} abriu"
            out[x["id"]] = (txt, "nt")
        elif len(outros) >= limite:
            out[x["id"]] = (f"📅 data ocupada · {d:%d/%m}", "bad")
    return out
