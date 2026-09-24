"""Cockpit do Dono — métricas e ações pra o dono/gestor acompanhar a equipe.

Mesmo app (/cockpit), mas quem entra como dono/gestor cai numa visão de EQUIPE:
Visão (KPIs + funil do time + precisa de atenção), Placar (ranking dos vendedores)
e Atividade (feed). Ações: reatribuir lead e pausar/reativar vendedor no rodízio.

Tudo escopado por conta_id, lê do dado que já existe (prospeccao/conversas/mensagens/
orcamentos/eventos_agenda). Sem tabela nova.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timedelta, timezone

from finance import funil_regua as _fr

_log_cd = logging.getLogger("finance.cockpit_dono")

# O painel do dono conta venda pela FASE da etapa, não pelo literal 'ganho' — senão
# o lead que anda pra uma etapa de pós-venda sai do "ganhos do mês" como se a venda
# tivesse sido desfeita (ver finance/funil_regua.sql_fechadas).
_ABERTO_P = "p.status not in " + _fr.sql_encerradas("p")
_ABERTO_T = "status not in " + _fr.sql_encerradas("prospeccao")
_ENCERRADO_P = "p.status in " + _fr.sql_encerradas("p")


def _brt():
    from finance import agenda as ag
    return ag.BRT


#: As pílulas da Visão. 'periodo' é a quarta, com as datas que o gestor escolher —
#: o nome é do dono (24/09/2026), entre "Escolher", "Período" e "Personalizado".
PERIODOS = ("hoje", "semana", "mes", "periodo")

#: Até onde o período escolhido pode ir. Um ano cobre "o que aconteceu no ano
#: passado"; mais que isso é relatório, não painel de celular.
PERIODO_MAX_DIAS = 366


def periodo_escolhido(de, ate) -> tuple[date, date] | None:
    """As duas datas do período escolhido, validadas. None se não dá pra usar.

    Aceita 'AAAA-MM-DD' (o <input type=date> manda assim) ou `date`. Invertidas são
    DESINVERTIDAS em vez de recusadas: quem pôs 30 no "de" e 1 no "até" quis o mês
    inteiro, e uma tela de erro no celular por isso é pior que adivinhar certo."""
    def _d(v):
        if isinstance(v, date):
            return v
        try:
            return date.fromisoformat(str(v or "").strip()[:10])
        except ValueError:
            return None
    a, b = _d(de), _d(ate)
    if not a or not b:
        return None
    if a > b:
        a, b = b, a
    if (b - a).days > PERIODO_MAX_DIAS:
        a = b - timedelta(days=PERIODO_MAX_DIAS)
    return a, b


_MESES_CURTOS = ("jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez")
_DIAS_CURTOS = ("seg", "ter", "qua", "qui", "sex", "sáb", "dom")


def rotulo_periodo(de: date, ate: date) -> str:
    """'10–23 set', '28 ago – 3 set', '15 dez 2025 – 10 jan'. É o que a pílula
    'Período' mostra depois de escolhida: o nome some e vira a resposta."""
    if de == ate:
        return f"{de.day} {_MESES_CURTOS[de.month - 1]}"
    ano_de = f" {de.year}" if de.year != ate.year else ""
    if de.month == ate.month and de.year == ate.year:
        return f"{de.day}–{ate.day} {_MESES_CURTOS[ate.month - 1]}"
    return f"{de.day} {_MESES_CURTOS[de.month - 1]}{ano_de} – {ate.day} {_MESES_CURTOS[ate.month - 1]}"


def _range(periodo: str, de=None, ate=None):
    """(início, fim) do período em BRT. hoje / semana / mês / período escolhido."""
    now = datetime.now(_brt())
    if periodo == "periodo":
        esc = periodo_escolhido(de, ate)
        if esc:
            ini = datetime(esc[0].year, esc[0].month, esc[0].day, tzinfo=_brt())
            fim = datetime(esc[1].year, esc[1].month, esc[1].day, tzinfo=_brt()) + timedelta(days=1)
            return ini, min(fim, now + timedelta(minutes=1))
        periodo = "semana"     # datas ilegíveis: a semana, que é o padrão da tela
    if periodo == "hoje":
        ini = now.replace(hour=0, minute=0, second=0, microsecond=0)
    elif periodo == "mes":
        ini = now.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
    else:  # semana (default)
        ini = now - timedelta(days=7)
    return ini, now + timedelta(minutes=1)


def _primeiro(nome: str) -> str:
    return (nome or "—").strip().split(" ")[0]


def _reais(centavos) -> str:
    v = int(centavos or 0) // 100
    if v >= 1000:
        return f"R$ {v/1000:.0f} mil" if v % 1000 == 0 or v >= 10000 else f"R$ {v/1000:.1f} mil".replace(".0", "")
    return f"R$ {v}"


#: O último recurso, quando a conta não tem funil lido do banco. NÃO é a lista do
#: funil de ninguém: as etapas de verdade saem de `_etapas_conta`.
_ETAPAS_FUNIL = [("novo", "Novo"), ("contatado", "Contatado"),
                 ("qualificado", "Qualificado"), ("proposta", "Proposta")]

#: QUANTO VALE UM LEAD FECHADO. `valor_estimado_centavos` é o palpite que o
#: vendedor digita na ficha — e está ZERADO na base inteira (ver a nota do
#: relatório de leads em web/painel_relatorios e a do portal). Somando só ele, o
#: cockpit do gestor da Prime mostrava "Fechado no período: R$ 0" com oito
#: contratos assinados na semana, cada um com orçamento e títulos gerados.
#:
#: O valor certo é o do ORÇAMENTO, pela mesma fórmula que gera os títulos
#: (`coalesce(primeiro_ano_centavos, setup_centavos)` — ver finance/cockpit ao
#: fechar contrato e finance/agenda). Assim a tela do gestor diz o mesmo que o
#: contas a receber. O palpite fica como último recurso, pra conta que trabalha
#: sem orçamento.
_VALOR_FECHADO = """coalesce(
        (select coalesce(o.primeiro_ano_centavos, o.setup_centavos)
           from orcamentos o where o.id = p.orcamento_id and o.conta_id = p.conta_id),
        p.valor_estimado_centavos, 0)"""

#: QUANDO FALAMOS COM ESTE LEAD PELA ÚLTIMA VEZ. `ultimo_contato_em` só é escrito
#: por quem move o lead na mão no painel: na Prime ele está preenchido em 34 dos
#: 423 leads abertos, e por isso "parados há +3 dias" virava "cadastrados há mais
#: de 3 dias" — 363 leads, dos quais 118 tinham recebido mensagem nossa nos
#: últimos três dias. A mensagem que saiu é o fato; o campo é o complemento.
_FALOU = """greatest(
        p.ultimo_contato_em,
        (select max(m.criado_em) from conversas cv join mensagens m on m.conversa_id = cv.id
          where cv.prospeccao_id = p.id and cv.conta_id = p.conta_id and m.direcao = 'out'))"""

#: "Parado" cai na ENTRADA do lead quando nunca falamos com ele: quem entrou hoje
#: e ainda não foi atendido não é um lead parado há três dias.
_FALAMOS_EM = "coalesce(" + _FALOU + ", p.criado_em)"

#: "Sem contato hoje" NÃO usa a entrada: lead que chegou hoje e ninguém falou é
#: exatamente o caso que o alerta existe pra pegar. Sem contato nenhum, a data é o
#: começo dos tempos — sempre menor que hoje.
_FALAMOS_OU_NUNCA = "coalesce(" + _FALOU + ", timestamptz 'epoch')"


def _e_gerencia(papel: str) -> bool:
    return (papel or "") in ("dono", "gestor")


# ------------------------------------------------------------------ VISÃO
def visao(pool, conta_id: int, periodo: str = "semana", de=None, ate=None) -> dict:
    ini, fim = _range(periodo, de, ate)
    with pool.connection() as c:
        novos = c.execute("select count(*) from prospeccao where conta_id=%s and coalesce(estagio,'lead')='lead' "
                          "and criado_em>=%s and criado_em<%s", (conta_id, ini, fim)).fetchone()[0]
        # em atendimento agora (snapshot): leads abertos, split IA x vendedor
        atend = c.execute(
            """select count(*) filter (where coalesce(cv.agente_ativo,true)),
                      count(*) filter (where not coalesce(cv.agente_ativo,true))
                 from prospeccao p
                 left join conversas cv on cv.prospeccao_id=p.id and cv.conta_id=p.conta_id
                where p.conta_id=%s and coalesce(p.estagio,'lead')='lead' and """ + _ABERTO_P + """
                  and p.vendedor_id is not null""", (conta_id,)).fetchone()
        com_ia, com_vend = int(atend[0] or 0), int(atend[1] or 0)
        g = c.execute("select count(*), coalesce(sum(" + _VALOR_FECHADO + "),0) from prospeccao p "
                      "where p.conta_id=%s and p.status in " + _fr.sql_fechadas("p")
                      + " and p.atualizado_em>=%s and p.atualizado_em<%s",
                      (conta_id, ini, fim)).fetchone()
        perd = c.execute("select count(*) from prospeccao where conta_id=%s and status='perdido' "
                         "and atualizado_em>=%s and atualizado_em<%s", (conta_id, ini, fim)).fetchone()[0]
        ganhos, ganhos_c = int(g[0] or 0), int(g[1] or 0)
        conv = round(100 * ganhos / (ganhos + perd)) if (ganhos + perd) else None
        funil = _funil(c, conta_id)
        maxn = max([f["n"] for f in funil] + [1])
        for f in funil:
            f["pct"] = round(100 * f["n"] / maxn)
        # precisa de atenção
        agora = datetime.now(_brt())
        parados = c.execute(
            "select count(*) from prospeccao p where p.conta_id=%s and coalesce(p.estagio,'lead')='lead' "
            "and " + _ABERTO_P + " and " + _FALAMOS_EM + " < %s",
            (conta_id, agora - timedelta(days=3))).fetchone()[0]
        quentes = c.execute(
            "select count(*) from prospeccao p where p.conta_id=%s and coalesce(p.estagio,'lead')='lead' "
            "and " + _ABERTO_P + " and p.temperatura='quente' and " + _FALAMOS_OU_NUNCA + " < %s",
            (conta_id, agora.replace(hour=0, minute=0, second=0, microsecond=0))).fetchone()[0]
        propostas = c.execute("select count(*) from orcamentos where conta_id=%s and status='enviado'",
                              (conta_id,)).fetchone()[0]
        visitas = c.execute(
            "select count(*) from eventos_agenda where conta_id=%s and status='ativo' and prospeccao_id is not null "
            "and inicio >= %s and inicio < %s",
            (conta_id, agora.replace(hour=0, minute=0, second=0, microsecond=0),
             agora.replace(hour=0, minute=0, second=0, microsecond=0) + timedelta(days=1))).fetchone()[0]
    return {
        "kpis": {"novos": novos, "com_ia": com_ia, "com_vend": com_vend,
                 "ganhos": ganhos, "ganhos_rs": _reais(ganhos_c), "conversao": conv},
        "funil": funil,
        "atencao": {"parados": parados, "quentes": quentes, "propostas": propostas, "visitas": visitas},
    }


def _funil(c, conta_id: int) -> list[dict]:
    """O funil do time, com as MESMAS colunas do quadro do painel.

    Pedido do dono em 24/09/2026: "tem uma coluna que tem na web e não tem no
    cockpit". O conserto do dia anterior tinha trazido os nomes da conta, mas ainda
    tirava ganho e perdido — e na Prime isso é o CONTRATO ASSINADO (9) e o Perdido
    (38), justamente as duas pontas do fim. Agora a regra é a do quadro: todas as
    etapas da conta, na ordem dela, menos as marcadas "sai do quadro". O Perdido
    vem marcado pra tela desenhar apagado — quem olha o funil precisa ver o
    vazamento, mas ele não compete com o que está vivo.

    O R$ SÓ ONDE HÁ ORÇAMENTO OU CONTRATO (mesmo pedido): "valores só precisam
    aparecer em orçamentos e contratos, fora isso não — os dados dos leads mesmo,
    com quantidade". A regra é pelo DADO e não pelo nome da etapa: a etapa mostra
    R$ quando os leads dela têm orçamento, e quantos têm. Assim vale pra qualquer
    conta, com qualquer nome. No Perdido não mostra — dinheiro que não entrou não é
    número de funil.
    """
    try:
        with c.transaction():
            etapas = c.execute(
                """select chave, coalesce(nullif(rotulo,''), chave), coalesce(fase,'venda')
                     from funil_etapas where conta_id=%s and not coalesce(sai_do_quadro,false)
                    order by ordem, id""", (conta_id,)).fetchall()
    except Exception:  # noqa: BLE001 — base sem 238/fase: o jeito antigo
        etapas = []
    if not etapas:
        etapas = [(ch, rot, "venda") for ch, rot in _ETAPAS_FUNIL]
    linhas = c.execute(
        """select p.status, count(*),
                  count(*) filter (where o.id is not null),
                  coalesce(sum(coalesce(o.primeiro_ano_centavos, o.setup_centavos, 0)), 0)
             from prospeccao p
             left join orcamentos o on o.id = p.orcamento_id and o.conta_id = p.conta_id
            where p.conta_id=%s and p.estagio = 'lead'
            group by p.status""", (conta_id,)).fetchall()
    por = {r[0]: (int(r[1] or 0), int(r[2] or 0), int(r[3] or 0)) for r in linhas}
    out = []
    for chave, rot, fase in etapas:
        n, n_orc, cent = por.get(chave, (0, 0, 0))
        perdido = chave == "perdido"
        fechado = (fase in ("fechamento", "pos") and not perdido) or chave == "ganho"
        mostra_rs = bool(n_orc and cent and not perdido)
        out.append({"chave": chave, "rotulo": rot, "n": n,
                    "tipo": "perd" if perdido else ("fech" if fechado else ""),
                    "valor": _reais_cheio(cent) if mostra_rs else "",
                    # "18 prop." só onde nem todo lead tem orçamento — no contrato
                    # assinado todos têm, e repetir o número só enche a linha
                    "n_orc": n_orc if (mostra_rs and n_orc < n) else 0})
    return out


def _reais_cheio(centavos) -> str:
    """'R$ 148.050' — o valor inteiro, com ponto de milhar. No funil o número
    exato importa (é a proposta na mesa); o 'R$ 148 mil' do KPI é pra bater o olho."""
    v = int(centavos or 0) // 100
    return "R$ " + f"{v:,}".replace(",", ".")


# ------------------------------------------------------------------ O MOVIMENTO
# Os quatro blocos abaixo nasceram do mesmo pedido (24/09/2026): o dono vai dar
# acesso à gestão de tráfego, e a Visão tinha o placar do vendedor mas nada do que a
# agência pergunta — o anúncio está trazendo gente? a gente atende? o que pedem? por
# que não fecha? Tudo só leitura, e escopado pela conta.

#: Janela mínima dos blocos de análise. "Quando chegam" num período de hoje, com
#: três leads distribuídos por dia da semana, é ruído com cara de dado.
JANELA_MINIMA_DIAS = 30
#: Quantas barras o "leads por dia" tem no mínimo e no máximo.
DIAS_MIN, DIAS_MAX = 14, 31


def _janela_minima(ini, fim, dias: int):
    return min(ini, fim - timedelta(days=dias)), fim


def _dia_br(dt) -> date:
    return dt.astimezone(_brt()).date()


def _cfg_janela(c, conta_id: int) -> dict:
    """A janela de atendimento da conta, SÓ LENDO. `funil_regua.config` semeia a
    linha na primeira leitura — aqui não: uma tela de consulta não escreve."""
    cfg = dict(_fr._PADRAO)
    try:
        with c.transaction():
            r = c.execute("select janela_dias, janela_abre, janela_fecha from funil_regua where conta_id=%s",
                          (conta_id,)).fetchone()
        if r:
            for k, v in zip(("janela_dias", "janela_abre", "janela_fecha"), r):
                if v is not None:
                    cfg[k] = v
    except Exception:  # noqa: BLE001
        pass
    return cfg


def _entradas(c, conta_id: int, ini, fim) -> list[tuple]:
    """(criado_em, primeira mensagem nossa) de cada lead que entrou no período."""
    return c.execute(
        """select p.criado_em,
                  (select min(m.criado_em) from conversas cv join mensagens m on m.conversa_id = cv.id
                    where cv.prospeccao_id = p.id and cv.conta_id = p.conta_id and m.direcao = 'out')
             from prospeccao p
            where p.conta_id=%s and p.criado_em >= %s and p.criado_em < %s""",
        (conta_id, ini, fim)).fetchall()


def _mediana(xs):
    xs = sorted(xs)
    if not xs:
        return None
    m = len(xs) // 2
    return xs[m] if len(xs) % 2 else (xs[m - 1] + xs[m]) / 2


def movimento(pool, conta_id: int, periodo: str = "semana", de=None, ate=None) -> dict:
    """Leads por dia (com o dia da semana e a data em cada barra), a 1ª resposta e
    quem ficou sem resposta nenhuma.

    As barras cobrem o período escolhido, com piso de 14 dias e teto de 31: "hoje"
    com uma barra só não mostra nada, e um ano inteiro em barras de 3 pixels também
    não. O fim de semana vem marcado — é ele que explica os buracos (na Prime:
    sábado 19 teve 4 leads, domingo 20 teve 1, segunda voltou pra 16).
    """
    ini, fim = _range(periodo, de, ate)
    ultimo = _dia_br(fim - timedelta(minutes=2))
    primeiro = max(_dia_br(ini), ultimo - timedelta(days=DIAS_MAX - 1))
    if (ultimo - primeiro).days + 1 < DIAS_MIN:
        primeiro = ultimo - timedelta(days=DIAS_MIN - 1)
    a = datetime(primeiro.year, primeiro.month, primeiro.day, tzinfo=_brt())
    with pool.connection() as c:
        linhas = _entradas(c, conta_id, a, fim)
    cont: dict = {}
    esperas, sem = [], 0
    for criado, prim in linhas:
        d = _dia_br(criado)
        cont[d] = cont.get(d, 0) + 1
        if prim:
            esperas.append((prim - criado).total_seconds() / 60)
        else:
            sem += 1
    dias = []
    d = primeiro
    while d <= ultimo:
        dias.append({"data": d, "n": cont.get(d, 0), "dia": _DIAS_CURTOS[d.weekday()],
                     "num": d.day, "fds": d.weekday() >= 5, "ultimo": d == ultimo})
        d += timedelta(days=1)
    maxn = max([x["n"] for x in dias] + [1])
    for x in dias:
        x["pct"] = max(2, round(100 * x["n"] / maxn)) if x["n"] else 2
    total = sum(x["n"] for x in dias)
    med = _mediana(esperas)
    return {"dias": dias, "total": total, "media": round(total / len(dias)) if dias else 0,
            "resposta_min": round(med) if med is not None else None, "sem_resposta": sem,
            "de": primeiro, "ate": ultimo}


def quando_chegam(pool, conta_id: int, periodo: str = "semana", de=None, ate=None) -> dict:
    """Por dia da semana e por turno — e o achado mais caro: quem chega FORA do
    expediente da conta espera quanto pela primeira resposta.

    Medido na Prime em 24/09/2026: 78 de 310 leads (25%) chegaram à noite ou no
    domingo e esperaram 7h40 (mediana) pela 1ª resposta; dentro do horário, 37 min.
    É anúncio pago esperando a noite inteira. O expediente é o da CONTA (a mesma
    janela da régua), não um 8h–18h fixo no código.
    """
    ini, fim = _janela_minima(*_range(periodo, de, ate), JANELA_MINIMA_DIAS)
    with pool.connection() as c:
        cfg = _cfg_janela(c, conta_id)
        linhas = _entradas(c, conta_id, ini, fim)
    por_dia = [0] * 7
    turnos = {"manha": 0, "tarde": 0, "noite": 0, "madrugada": 0}
    fora, esp_fora, esp_dentro = 0, [], []
    for criado, prim in linhas:
        loc = criado.astimezone(_brt())
        por_dia[loc.weekday()] += 1
        h = loc.hour
        turnos["madrugada" if h < 6 else "manha" if h < 12 else "tarde" if h < 18 else "noite"] += 1
        # `dentro_da_janela` recebe o instante em UTC e converte ele mesmo — é a
        # mesma função que decide se a régua pode cobrar alguém agora
        dentro = _fr.dentro_da_janela(criado.astimezone(timezone.utc), cfg)
        espera = (prim - criado).total_seconds() / 60 if prim else None
        if not dentro:
            fora += 1
            if espera is not None:
                esp_fora.append(espera)
        elif espera is not None:
            esp_dentro.append(espera)
    total = len(linhas)
    maxd = max(por_dia + [1])
    mf, md = _mediana(esp_fora), _mediana(esp_dentro)
    return {
        "total": total, "dias_janela": (fim - ini).days,
        "por_dia": [{"dia": nome, "rotulo": nome, "n": n, "pct": round(100 * n / maxd), "fds": i >= 5}
                    for i, (nome, n) in enumerate(zip(("Segunda", "Terça", "Quarta", "Quinta",
                                                       "Sexta", "Sábado", "Domingo"), por_dia))],
        "turnos": turnos,
        "fora": fora, "fora_pct": round(100 * fora / total) if total else 0,
        "espera_fora_min": round(mf) if mf is not None else None,
        "espera_dentro_min": round(md) if md is not None else None,
    }


def _faixa_convidados(n) -> str | None:
    if not n:
        return None
    return "até 50" if n < 50 else "50–99" if n < 100 else "100–199" if n < 200 else "200+"


def o_que_pedem(pool, conta_id: int, periodo: str = "semana", de=None, ate=None) -> dict | None:
    """O que os leads do período pedem, NO VOCABULÁRIO DO NICHO (regra 6).

    Quem vende festa: tipo de festa, mês do evento e tamanho. Quem vende serviço por
    mensalidade: segmento e porte da empresa. Produto não tem funil — sem bloco.
    Nenhum nicho vê o do outro: a ZAQ não vê "casamento", a Prime não vê "porte".
    Devolve None quando não há nada a dizer (o bloco some).
    """
    ini, fim = _janela_minima(*_range(periodo, de, ate), JANELA_MINIMA_DIAS)
    with pool.connection() as c:
        perfil = _fr.perfil_da_conta(c, conta_id)
        if perfil == "produto":
            return None
        if perfil == "eventos":
            linhas = c.execute(
                """select lower(nullif(trim(evento_tipo),'')), evento_em, evento_convidados
                     from prospeccao where conta_id=%s and criado_em>=%s and criado_em<%s""",
                (conta_id, ini, fim)).fetchall()
        else:
            linhas = c.execute(
                """select nullif(trim(segmento),''), null, nullif(trim(porte),'')
                     from prospeccao where conta_id=%s and criado_em>=%s and criado_em<%s""",
                (conta_id, ini, fim)).fetchall()
    total = len(linhas)
    if not total:
        return None
    tipos: dict = {}
    sem_tipo = 0
    for t, _em, _cv in linhas:
        if t:
            tipos[t] = tipos.get(t, 0) + 1
        else:
            sem_tipo += 1
    if not tipos:
        return None
    ordem = sorted(tipos.items(), key=lambda x: -x[1])
    top, resto = ordem[:4], sum(n for _t, n in ordem[4:])
    maxn = max(n for _t, n in top)
    itens = [{"rotulo": t[:1].upper() + t[1:], "n": n, "pct": round(100 * n / maxn)} for t, n in top]
    if resto:
        itens.append({"rotulo": "Outros", "n": resto, "pct": round(100 * resto / maxn), "resto": True})
    linhas_txt = []
    hoje = datetime.now(_brt()).date()
    if perfil == "eventos":
        meses: dict = {}
        faixas: dict = {}
        for _t, em, cv in linhas:
            if em and em >= hoje.replace(day=1):
                k = (em.year, em.month)
                meses[k] = meses.get(k, 0) + 1
            f = _faixa_convidados(cv)
            if f:
                faixas[f] = faixas.get(f, 0) + 1
        if meses:
            m3 = sorted(meses.items(), key=lambda x: -x[1])[:3]
            linhas_txt.append(("Meses mais pedidos",
                               " · ".join(f"{_MESES_CURTOS[m - 1]} {n}" for (_y, m), n in m3)))
        if faixas:
            f2 = sorted(faixas.items(), key=lambda x: -x[1])[:2]
            linhas_txt.append(("Convidados", " · ".join(f"{f}: {n}" for f, n in f2)))
        titulo_sem = "Sem tipo de festa"
    else:
        portes: dict = {}
        for _s, _em, po in linhas:
            if po:
                portes[po] = portes.get(po, 0) + 1
        if portes:
            p3 = sorted(portes.items(), key=lambda x: -x[1])[:3]
            linhas_txt.append(("Porte", " · ".join(f"{p} {n}" for p, n in p3)))
        titulo_sem = "Sem segmento"
    return {"perfil": perfil, "total": total, "itens": itens, "linhas": linhas_txt,
            "sem": sem_tipo, "sem_rotulo": titulo_sem, "dias_janela": (fim - ini).days}


#: Motivos que o vendedor marca e que NÃO dizem nada: aí a leitura da conversa vale.
_MOTIVO_VAZIO = ("", "outro")


def por_que_perdemos(pool, conta_id: int, periodo: str = "semana", de=None, ate=None) -> dict | None:
    """Por que os leads do período foram perdidos — o vendedor primeiro, a conversa
    onde ele não disse nada.

    O motivo de cada lead é o que o VENDEDOR marcou; quando ele não marcou (ou
    marcou "Outro"), vale o que `finance.motivo_lido` leu na conversa (selo 💬 lido).
    Os dois usam a MESMA lista de motivos da conta, então somam na mesma linha.
    Lead ainda não lido fica em "Ainda não lido" — nunca some da conta.
    """
    from finance import motivo_lido as _ml
    ini, fim = _janela_minima(*_range(periodo, de, ate), JANELA_MINIMA_DIAS)
    with pool.connection() as c:
        perfil = _fr.perfil_da_conta(c, conta_id)
        if perfil == "produto":
            return None
        rot = _ml.rotulos(c, conta_id, perfil)
        try:
            with c.transaction():
                linhas = c.execute(
                    """select coalesce(perda_motivo,''), perda_lida
                         from prospeccao
                        where conta_id=%s and status='perdido'
                          and coalesce(perda_em, atualizado_em) >= %s
                          and coalesce(perda_em, atualizado_em) < %s""",
                    (conta_id, ini, fim)).fetchall()
        except Exception:  # noqa: BLE001 — base sem a 328: só o que o vendedor marcou
            linhas = [(r[0], None) for r in c.execute(
                """select coalesce(perda_motivo,'') from prospeccao
                    where conta_id=%s and status='perdido'
                      and atualizado_em >= %s and atualizado_em < %s""",
                (conta_id, ini, fim)).fetchall()]
    if not linhas:
        return None
    cont: dict = {}
    lidos = 0
    for marcado, lido in linhas:
        if marcado not in _MOTIVO_VAZIO:
            chave = marcado
        elif lido:
            chave, lidos = lido, lidos + 1
        else:
            chave = marcado or "_nao_lido"
        cont[chave] = cont.get(chave, 0) + 1
    ordem = sorted(cont.items(), key=lambda x: (x[0] in ("_nao_lido", "outro", "sem_conversa"), -x[1]))
    maxn = max(cont.values())
    itens = [{"chave": ch, "rotulo": ("Ainda não lido" if ch == "_nao_lido" else rot.get(ch)
                                      or ch.replace("_", " ").capitalize()),
              "n": n, "pct": round(100 * n / maxn),
              "tom": ("apagado" if ch in ("_nao_lido", "outro", "sem_conversa", "nao_era_cliente")
                      else "alerta" if ch in ("achou_caro", "nao_respondeu", "sumiu_apos_proposta") else "")}
             for ch, n in ordem]
    return {"total": len(linhas), "itens": itens, "lidos": lidos,
            "nao_cliente": cont.get("nao_era_cliente", 0), "perfil": perfil,
            "dias_janela": (fim - ini).days}


#: O motivo que vale pra cada lead perdido, na MESMA precedência do
#: `por_que_perdemos`: o do vendedor; onde ele não disse nada (ou disse "Outro"),
#: o que a conversa disse; e, sem nenhum dos dois, "Ainda não lido".
_MOTIVO_EFETIVO = """(case when coalesce(p.perda_motivo,'') not in ('','outro') then p.perda_motivo
                          when p.perda_lida is not null then p.perda_lida
                          when coalesce(p.perda_motivo,'') <> '' then p.perda_motivo
                          else '_nao_lido' end)"""


def _sub_do_lead(perfil: str, tipo, convidados, segmento, porte) -> str:
    """A linha de baixo do nome, no vocabulário do nicho (regra 6)."""
    if perfil == "eventos":
        partes = [(tipo or "").strip().capitalize() or "Evento"]
        if convidados:
            partes.append(f"{int(convidados)} convidados")
        return " · ".join(partes)
    return " · ".join(x for x in ((segmento or "").strip(), (porte or "").strip()) if x)


def perdidos(pool, conta_id: int, motivo: str, periodo: str = "semana", de=None, ate=None,
             codigo: str | None = None) -> dict | None:
    """Os leads perdidos por UM motivo — o que abre ao tocar numa linha do "Por que
    perdemos". Pedido do dono em 24/09/2026: "colocar o link com por que perdemos
    o lead".

    Duas portas, duas janelas:
      * da Visão (`codigo` None): a MESMA janela do bloco, pela data da perda — o
        número da linha e o tamanho da lista batem;
      * da aba Anúncios (`codigo` = o código, ou "" pro "sem código"): os leads que
        AQUELE anúncio trouxe no período, pela data de entrada.

    Cada lead vem com a frase do cliente (quando a leitura achou uma), quem decidiu
    o motivo (a equipe ou 💬 a leitura), em que ponto a conversa parou e, pra quem
    não era cliente, o que a pessoa queria. Motivo "data indisponível" em conta de
    festa traz também as DATAS que pediram — a agenda de procura."""
    from finance import motivo_lido as _ml
    motivo = (motivo or "").strip()
    if not motivo:
        return None
    if codigo is None:
        ini, fim = _janela_minima(*_range(periodo, de, ate), JANELA_MINIMA_DIAS)
        filtro = "and coalesce(p.perda_em, p.atualizado_em) >= %s and coalesce(p.perda_em, p.atualizado_em) < %s"
        args: tuple = (ini, fim)
    else:
        # o MESMO universo da aba Anúncios (`origens.detalhes_por_codigo`): lead com
        # conversa de WhatsApp aberta no período, em dia de Brasília. Assim a barra
        # "Não era cliente 3" abre uma lista de 3, e não de 2 ou 4.
        ini, fim = _range(periodo, de, ate)
        filtro = ("and exists (select 1 from conversas cv where cv.prospeccao_id = p.id "
                  "and cv.conta_id = p.conta_id and cv.canal = 'whatsapp' "
                  "and (cv.criado_em at time zone 'America/Sao_Paulo')::date between %s and %s) "
                  "and coalesce(nullif(btrim(p.origem_codigo), ''), '') = %s")
        args = (_dia_br(ini), _dia_br(fim - timedelta(minutes=1)), (codigo or "").strip())
    with pool.connection() as c:
        perfil = _fr.perfil_da_conta(c, conta_id)
        if perfil == "produto":
            return None
        rot = _ml.rotulos(c, conta_id, perfil)
        opcoes = _ml.motivos_da_conta(c, conta_id, perfil)
        try:
            with c.transaction():
                linhas = c.execute(
                    f"""select p.id, coalesce(nullif(p.contato,''), nullif(p.empresa,''), 'lead'),
                               p.evento_tipo, p.evento_convidados, p.segmento, p.porte, p.evento_em,
                               p.perda_lida_trecho,
                               coalesce(p.perda_motivo,'') not in ('','outro') as da_equipe,
                               p.perda_corrigida_em is not null as corrigido,
                               p.perda_lida_parou, p.perda_lida_quem,
                               coalesce(p.perda_em, p.atualizado_em),
                               coalesce(nullif(m.nome,''), '')
                          from prospeccao p
                          left join membros m on m.id = p.vendedor_id and m.conta_id = p.conta_id
                         where p.conta_id=%s and p.status='perdido' {filtro}
                           and {_MOTIVO_EFETIVO} = %s
                         order by coalesce(p.perda_em, p.atualizado_em) desc
                         limit 200""", (conta_id, *args, motivo)).fetchall()
        except Exception:  # noqa: BLE001 — base sem a 328/331: lista vazia, tela de pé
            _log_cd.warning("perdidos: consulta falhou na conta %s", conta_id, exc_info=True)
            linhas = []
    itens = []
    for (lid, nome, tipo, conv, seg, porte, em, trecho, da_equipe, corrigido,
         parou, quem, perdido, vend) in linhas:
        itens.append({
            "id": lid, "nome": nome, "sub": _sub_do_lead(perfil, tipo, conv, seg, porte),
            "evento_em": em, "dia": _DIAS_CURTOS[em.weekday()] if em else "",
            "trecho": trecho or "", "lido": not da_equipe, "corrigido": bool(corrigido),
            "parou": _ml.PAROU.get(parou or "", ""), "quem": _ml.QUEM.get(quem or "", ""),
            "perdido_em": _dia_br(perdido) if perdido else None, "vendedor": vend,
        })
    rotulo = ("Ainda não lido" if motivo == "_nao_lido" else rot.get(motivo)
              or motivo.replace("_", " ").capitalize())
    out = {"motivo": motivo, "rotulo": rotulo, "total": len(itens), "itens": itens,
           "perfil": perfil, "opcoes": opcoes, "datas": None,
           "de": _dia_br(ini), "ate": _dia_br(fim - timedelta(minutes=1))}
    # o resumo do detalhe: em que ponto pararam e, de quem não era cliente, o que queria
    out["parou"] = _contagem(i["parou"] for i in itens)
    out["quem"] = _contagem(i["quem"] for i in itens)
    if motivo == "data_indisponivel" and perfil == "eventos":
        out["datas"] = _datas_procuradas(pool, conta_id, itens)
    return out


def _contagem(rotulos) -> list[tuple[str, int]]:
    cont: dict = {}
    for r in rotulos:
        if r:
            cont[r] = cont.get(r, 0) + 1
    return sorted(cont.items(), key=lambda x: -x[1])


def _datas_procuradas(pool, conta_id: int, itens: list[dict]) -> dict | None:
    """As datas que pediram e a casa não tinha — a agenda de procura.

    Medido na Prime (1 a 23/09/2026): data indisponível foi o motivo nº 1, 7 de 23,
    e os 7 disseram a data; 4 eram sábado e 3 caíam entre 28/11 e 19/12. É demanda
    que existe e a casa não atendeu: serve pro preço, pra oferecer segunda data e pro
    tráfego não puxar datas já tomadas.

    Quando a conta usa a lista de espera (`contas.festas_por_dia`), cada data diz se
    continua tomada ou se ABRIU — e quem esperava por ela é avisado pela própria lista.
    """
    from finance import lista_espera as _le
    datas = sorted({i["evento_em"] for i in itens if i["evento_em"]})
    if not datas:
        return None
    hoje = datetime.now(_brt()).date()
    limite = _le.festas_por_dia(pool, conta_id)
    ocup: dict = {}
    if limite is not None:
        futuras = [d for d in datas if d >= hoje]
        if futuras:
            try:
                with pool.connection() as c:
                    ocup = _le._ocupacao(c, conta_id, min(futuras), max(futuras))
            except Exception:  # noqa: BLE001
                ocup = {}
    chips = []
    for d in datas:
        passou = d < hoje
        situacao = None
        if limite is not None and not passou:
            situacao = "tomada" if ocup.get(d, 0) >= limite else "abriu"
        chips.append({"data": d, "dia": _DIAS_CURTOS[d.weekday()], "passou": passou,
                      "situacao": situacao})
    por_dia: dict = {}
    por_mes: dict = {}
    for d in datas:
        por_dia[d.weekday()] = por_dia.get(d.weekday(), 0) + 1
        por_mes[(d.year, d.month)] = por_mes.get((d.year, d.month), 0) + 1
    wd, n_wd = max(por_dia.items(), key=lambda x: (x[1], x[0]))
    (ano, mes), n_mes = max(por_mes.items(), key=lambda x: (x[1], -x[0][0], -x[0][1]))
    resumo = []
    if n_wd > 1:
        resumo.append(f"{n_wd} das {len(datas)} eram {_DIAS_LONGOS[wd]}")
    if n_mes > 1:
        resumo.append(f"{_MESES_CURTOS[mes - 1]}: {n_mes} pedidos")
    return {"chips": chips, "resumo": " · ".join(resumo), "usa_lista": limite is not None,
            "abriram": sum(1 for x in chips if x["situacao"] == "abriu")}


_DIAS_LONGOS = ("segunda", "terça", "quarta", "quinta", "sexta", "sábado", "domingo")


def corrigir_motivo(pool, conta_id: int, lead_id: int, motivo: str,
                    membro_id: int | None = None) -> bool:
    """O gestor corrige o motivo que a leitura da conversa deu. Aprovado pelo dono
    em 24/09/2026 ("o gestor pode corrigir o motivo? — sim").

    A correção grava em `perda_motivo`, e daí vale como se a equipe tivesse
    marcado: o número da tela deixa de depender da leitura errada. `perda_lida`
    fica como estava — é o registro do que a leitura disse. Só aceita motivo da
    lista da conta, e só em lead que continua perdido."""
    motivo = (motivo or "").strip()
    with pool.connection() as c:
        perfil = _fr.perfil_da_conta(c, conta_id)
        from finance import funil_perda as _fp
        validos = {m["chave"] for m in _fp.motivos(c, conta_id, perfil)}
        if motivo not in validos:
            return False
        r = c.execute(
            """update prospeccao set perda_motivo=%s, perda_corrigida_por=%s,
                      perda_corrigida_em=now()
                where id=%s and conta_id=%s and status='perdido'""",
            (motivo, membro_id, lead_id, conta_id))
        ok = bool(r.rowcount)
        c.commit()
    return ok


# ------------------------------------------------------------------ PLACAR
def _nome_por_id(c, conta_id, mid):
    if not mid:
        return "—"
    try:
        mid_i = int(str(mid).strip())
    except (TypeError, ValueError):
        return "—"
    r = c.execute("select coalesce(nullif(nome,''), email) from membros where id=%s and conta_id=%s",
                  (mid_i, conta_id)).fetchone()
    return r[0] if r else "—"


def placar(pool, conta_id: int, periodo: str = "mes") -> list[dict]:
    ini, fim = _range(periodo)
    d30 = datetime.now(_brt()) - timedelta(days=30)
    out = []
    with pool.connection() as c:
        membros = c.execute(
            "select id, coalesce(nullif(nome,''), email), coalesce(cockpit_pausado,false) "
            "from membros where conta_id=%s and ativo and papel in ('vendedor','gestor','dono') order by id",
            (conta_id,)).fetchall()
        for mid, nome, pausado in membros:
            fila = c.execute("select count(*) from prospeccao where conta_id=%s and vendedor_id=%s "
                             "and coalesce(estagio,'lead')='lead' and " + _ABERTO_T,
                             (conta_id, mid)).fetchone()[0]
            atend = c.execute(
                "select count(*) from conversas cv join prospeccao p on p.id=cv.prospeccao_id "
                "where cv.conta_id=%s and cv.responsavel_membro_id=%s and not coalesce(cv.agente_ativo,true) "
                "and " + _ABERTO_P, (conta_id, mid)).fetchone()[0]
            g = c.execute("select count(*), coalesce(sum(" + _VALOR_FECHADO + "),0) from prospeccao p "
                          "where p.conta_id=%s and p.vendedor_id=%s and p.status in " + _fr.sql_fechadas("p")
                          + " and p.atualizado_em>=%s and p.atualizado_em<%s",
                          (conta_id, mid, ini, fim)).fetchone()
            perd = c.execute("select count(*) from prospeccao where conta_id=%s and vendedor_id=%s and status='perdido' "
                             "and atualizado_em>=%s and atualizado_em<%s", (conta_id, mid, ini, fim)).fetchone()[0]
            ganhos, rs_c = int(g[0] or 0), int(g[1] or 0)
            conv = round(100 * ganhos / (ganhos + perd)) if (ganhos + perd) else None
            resp = c.execute(
                """select avg(extract(epoch from (fo.po - cv.criado_em))/60) from conversas cv
                     join lateral (select min(criado_em) po from mensagens
                                    where conversa_id=cv.id and direcao='out' and autor='humano') fo on true
                    where cv.conta_id=%s and cv.responsavel_membro_id=%s and fo.po is not null and cv.criado_em>=%s""",
                (conta_id, mid, d30)).fetchone()[0]
            out.append({
                "id": mid, "nome": nome, "pausado": bool(pausado),
                "fila": fila, "atendendo": atend, "ganhos": ganhos, "rs_centavos": rs_c, "rs": _reais(rs_c),
                "conversao": (f"{conv}%" if conv is not None else "—"),
                "resp": (f"{round(resp)} min" if resp else "—"),
            })
    out.sort(key=lambda x: (x["rs_centavos"], x["ganhos"], x["fila"]), reverse=True)
    return out


# ------------------------------------------------------------------ VENDEDOR (drill)
def vendedor(pool, conta_id: int, membro_id: int) -> dict | None:
    with pool.connection() as c:
        m = c.execute("select coalesce(nullif(nome,''), email), coalesce(cockpit_pausado,false), papel "
                      "from membros where id=%s and conta_id=%s and ativo", (membro_id, conta_id)).fetchone()
        if not m:
            return None
    base = next((p for p in placar(pool, conta_id) if p["id"] == membro_id), None) or {}
    with pool.connection() as c:
        rows = c.execute(
            """select p.id, p.empresa, p.temperatura, coalesce(cv.agente_ativo, true)
                 from prospeccao p
                 left join conversas cv on cv.prospeccao_id=p.id and cv.conta_id=p.conta_id
                where p.conta_id=%s and p.vendedor_id=%s and coalesce(p.estagio,'lead')='lead'
                  and """ + _ABERTO_P + """
                order by p.atualizado_em desc limit 50""", (conta_id, membro_id)).fetchall()
    from web.painel_prospeccao import TEMP_COR
    leads = [{"id": r[0], "empresa": r[1] or "Lead", "temp_cor": TEMP_COR.get(r[2] or "frio", "#5b9bd5"),
              "ia": bool(r[3])} for r in rows]
    return {"id": membro_id, "nome": m[0], "pausado": bool(m[1]), "papel": m[2],
            "fila": base.get("fila", len(leads)), "ganhos": base.get("ganhos", 0),
            "rs": base.get("rs", "R$ 0"), "conversao": base.get("conversao", "—"),
            "resp": base.get("resp", "—"), "leads": leads}


# ------------------------------------------------------------------ ATIVIDADE
def atividade(pool, conta_id: int, limite: int = 25) -> list[dict]:
    itens = []
    with pool.connection() as c:
        for emp, status, val, quando, nome in c.execute(
                """select p.empresa, p.status, """ + _VALOR_FECHADO + """, p.atualizado_em,
                          coalesce(nullif(m.nome,''), m.email, '—')
                     from prospeccao p left join membros m on m.id=p.vendedor_id
                    where p.conta_id=%s and """ + _ENCERRADO_P + """
                    order by p.atualizado_em desc limit 15""", (conta_id,)).fetchall():
            # com fase, "ganhou" é tudo que encerrou e não foi perdido — inclusive
            # uma etapa de pós-venda, que é fechamento com o evento já entregue
            g = status != "perdido"
            txt = f"{_primeiro(nome)} {'ganhou' if g else 'perdeu'} — {emp or 'lead'}"
            if g and val:
                txt += f" · {_reais(val)}"
            itens.append({"tipo": "ganho" if g else "perdido", "quando": quando, "txt": txt})
        for titulo, quando, nome in c.execute(
                """select e.titulo, e.criado_em, coalesce(nullif(m.nome,''), m.email, '—')
                     from eventos_agenda e left join membros m on m.id=e.membro_id
                    where e.conta_id=%s and e.prospeccao_id is not null and e.status='ativo'
                    order by e.criado_em desc limit 10""", (conta_id,)).fetchall():
            alvo = (titulo or "").replace("Visita — ", "")
            itens.append({"tipo": "visita", "quando": quando, "txt": f"{_primeiro(nome)} marcou visita — {alvo}"})
        for emp, quando, criador in c.execute(
                """select empresa, criado_em, criado_por from orcamentos
                    where conta_id=%s and coalesce(canal,'')='cockpit'
                    order by criado_em desc limit 10""", (conta_id,)).fetchall():
            nome = _nome_por_id(c, conta_id, criador)
            itens.append({"tipo": "prop", "quando": quando, "txt": f"{_primeiro(nome)} enviou proposta — {emp or 'cliente'}"})
    itens = [i for i in itens if i.get("quando")]
    itens.sort(key=lambda x: x["quando"], reverse=True)
    return itens[:limite]


# ------------------------------------------------------------------ AÇÕES
def reatribuir(pool, conta_id: int, lead_id: int, para_membro_id: int,
               *, por_id=None, papel: str = "dono", motivo: str = "") -> dict:
    """Move o lead pra outra pessoa, na visão de equipe do dono/gestor.

    DELEGA pro `finance.repasse`: a regra de quem pode passar e o REGISTRO da
    troca têm um dono só. Antes esta função escrevia o `update` na mão e não
    anotava nada — e um histórico que só guarda metade das trocas mente por
    omissão justamente quando alguém questiona a decisão.
    """
    from finance import repasse as _rp
    return _rp.passar(pool, conta_id, lead_id, para_membro_id,
                      por_id=por_id, papel=papel, motivo=motivo)


def pausar(pool, conta_id: int, membro_id: int, on: bool) -> dict:
    from finance import cockpit as ck
    ck.set_pausado(pool, conta_id, membro_id, on)
    return {"ok": True, "pausado": bool(on)}


def leads(pool, conta_id: int, vend: int | None = None, etapa: str = "", temp: str = "") -> list[dict]:
    """TODOS os leads abertos da equipe (fora de ganho/perdido), com o vendedor dono e
    se está com IA ou com o vendedor. Filtra por vendedor / etapa / temperatura."""
    from web.painel_prospeccao import TEMP_COR
    where = ["p.conta_id=%s", "coalesce(p.estagio,'lead')='lead'", _ABERTO_P]
    args = [conta_id]
    if vend:
        where.append("p.vendedor_id=%s")
        args.append(vend)
    if etapa:
        where.append("p.status=%s")
        args.append(etapa)
    if temp:
        where.append("p.temperatura=%s")
        args.append(temp)
    sql = ("select p.id, p.empresa, p.status, coalesce(p.temperatura,'frio'), "
           "coalesce(nullif(m.nome,''), m.email, 'Sem dono'), coalesce(cv.agente_ativo, true) "
           "from prospeccao p left join membros m on m.id=p.vendedor_id "
           "left join conversas cv on cv.prospeccao_id=p.id and cv.conta_id=p.conta_id "
           "where " + " and ".join(where) + " order by p.atualizado_em desc limit 200")
    with pool.connection() as c:
        rows = c.execute(sql, tuple(args)).fetchall()
    return [{"id": r[0], "empresa": r[1] or "Lead", "status": r[2] or "novo",
             "temp_cor": TEMP_COR.get(r[3], "#5b9bd5"), "vendedor": r[4], "ia": bool(r[5])} for r in rows]


def filtros_leads(pool, conta_id: int) -> dict:
    """Opções pros filtros da aba Leads: vendedores (com lead) + etapas do funil.

    Devolve também `rotulos` — {chave: nome que a CONTA deu} —, e é ele que a tela
    usa pra escrever etapa, no filtro e em cada linha da lista.

    POR QUE O RÓTULO PASSOU A VIR DAQUI (17/09/2026). A tela montava os nomes de uma
    tabela fixa de quatro (`_ETAPA_ROT` em web/painel_cockpit.py): novo, contatado,
    qualificado, proposta. Quem não estivesse nela virava `.title()` da chave. Na
    Prime, onde o dono renomeou as etapas, o gestor lia no app "Qualificado" onde o
    painel dizia "Agendado Visita", "Proposta" onde dizia "Negociação" e —
    literalmente — "Evento_Realizado", com sublinhado no meio da palavra. Dois
    vocabulários pro mesmo lead, e a regra 6 furada por uma constante esquecida.

    `rotulos` traz TODAS as etapas, ganho e perdido inclusive: a lista de leads
    mostra a etapa de cada um, e um lead pode estar numa que o FILTRO não oferece.
    """
    with pool.connection() as c:
        vends = c.execute(
            "select distinct m.id, coalesce(nullif(m.nome,''), m.email) from prospeccao p "
            "join membros m on m.id=p.vendedor_id where p.conta_id=%s and coalesce(p.estagio,'lead')='lead' "
            "and " + _ABERTO_P + " order by 2", (conta_id,)).fetchall()
        todas = _etapas_conta(c, conta_id)
        etapas = [e for e, _r in todas if e not in ("ganho", "perdido")]
        rotulos = {e: r for e, r in todas}
    return {"vendedores": [{"id": r[0], "nome": r[1]} for r in vends],
            "etapas": etapas, "rotulos": rotulos}


def _etapas_conta(c, conta_id: int) -> list[tuple[str, str]]:
    """(chave, rótulo) de todas as etapas da conta, na ordem do funil."""
    try:
        rows = c.execute("select chave, rotulo from funil_etapas where conta_id=%s order by ordem, id",
                         (conta_id,)).fetchall()
        if rows:
            return [(r[0], r[1] or r[0].replace("_", " ").title()) for r in rows]
    except Exception:  # noqa: BLE001
        pass
    return [("novo", "Novo"), ("contatado", "Contatado"),
            ("qualificado", "Qualificado"), ("proposta", "Proposta")]


def vendedores_para_reatribuir(pool, conta_id: int, exceto_id: int) -> list[dict]:
    with pool.connection() as c:
        rows = c.execute(
            "select id, coalesce(nullif(nome,''), email) from membros where conta_id=%s and ativo "
            "and papel in ('vendedor','gestor','dono') and id<>%s order by nome", (conta_id, exceto_id)).fetchall()
    return [{"id": r[0], "nome": r[1]} for r in rows]
