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
#: status de lead que ainda estão em jogo (espelha finance.raio_x.ABERTOS)
ABERTOS = ("novo", "contatado", "qualificado", "proposta")
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
    não faz nada; se tinha saído, volta."""
    try:
        with pool.connection() as c:
            c.execute("""
                insert into lista_espera_data (conta_id, prospeccao_id, data)
                values (%s, %s, %s)
                on conflict (prospeccao_id, data) do update
                   set saiu_em = null, saiu_motivo = null, entrou_em = coalesce(lista_espera_data.entrou_em, now())
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
                ocup = _ocupacao(c, conta_id, de, ate)
            else:
                ocup = {}
            na_lista = {(r[0], r[1]) for r in c.execute(
                "select prospeccao_id, data from lista_espera_data where conta_id = %s and saiu_em is null",
                (conta_id,)).fetchall()}
        for lid, dia, status in leads:
            tomada = ocup.get(dia, 0) >= limite
            em_jogo = status in ABERTOS
            # perdido por "data indisponível" continua esperando: o cliente ainda
            # quer aquele dia, e é justamente quem avisar quando abrir
            if not em_jogo and status == "perdido":
                with pool.connection() as c:
                    r = c.execute("select perda_motivo from prospeccao where id = %s", (lid,)).fetchone()
                em_jogo = bool(r and r[0] == "data_indisponivel")
            if tomada and em_jogo and (lid, dia) not in na_lista:
                if entrar(pool, conta_id, lid, dia):
                    entraram += 1
            elif (lid, dia) in na_lista and not (tomada and em_jogo):
                motivo = ("fechou" if status == "ganho" else "atendido" if not tomada else "desistiu")
                saíram += sair(pool, conta_id, lid, motivo, dia)
        # lead que MUDOU de data: a linha antiga não aparece mais no laço acima
        atuais = {(l[0], l[1]) for l in leads}
        for lid, dia in na_lista - atuais:
            saíram += sair(pool, conta_id, lid, "mudou_data", dia)
    except Exception as e:  # noqa: BLE001
        _log.info("lista_espera: sincronizar falhou (conta %s): %s: %s", conta_id, type(e).__name__, e)
    return {"entraram": entraram, "sairam": saíram}


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
                       coalesce(nullif(p.evento_tipo, ''), '')
                  from lista_espera_data l join prospeccao p on p.id = l.prospeccao_id
                 where l.conta_id = %s and l.saiu_em is null and l.avisado_em is null
                   and l.data >= %s
                 order by l.data, l.entrou_em""", (conta_id, hoje)).fetchall()
            if not rows:
                return []
            ocup = _ocupacao(c, conta_id, min(r[1] for r in rows), max(r[1] for r in rows))
    except Exception as e:  # noqa: BLE001
        _log.info("lista_espera: datas_que_abriram falhou: %s: %s", type(e).__name__, e)
        return []
    return [{"id": r[0], "data": r[1], "lead_id": r[2], "entrou_em": r[3], "vendedor_id": r[4],
             "nome": r[5], "tipo": r[6],
             "dias_esperando": (datetime.now(r[3].tzinfo) - r[3]).days if r[3] else 0}
            for r in rows if ocup.get(r[1], 0) < limite]


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
        s = sincronizar(pool, conta_id, hoje)
        out["entraram"] += s["entraram"]
        out["sairam"] += s["sairam"]
        try:
            out["avisados"] += avisar(pool, conta_id, hoje)
        except Exception as e:  # noqa: BLE001
            _log.info("lista_espera.rodar: avisar falhou (conta %s): %s: %s", conta_id, type(e).__name__, e)
    return out
