"""O EVENTO no lead: tipo, data e convidados — o eixo de tempo do funil de eventos.

POR QUE EXISTE. Medido na Prime Eventos (conta 34) em 04/09/2026: 274 leads no
funil, 224 numa coluna só ("Contatado"), sem eixo de tempo nenhum. E o sistema
sabia a data da festa de só 15 deles — não por falta de pergunta: o agente pergunta
a data em toda conversa que chega em preço (`_PRECISA_SABER`), mas guardava a
resposta SÓ em `orcamentos.evento`, e o funil nunca lia de lá.

No nicho de eventos o mês que importa é o da festa, não o mês em que o cliente
escreveu. Então a data passa a morar NO LEAD (migração 197), com quatro portas:

  * o agente, quando extrai da conversa (só preenche o que está vazio — nunca
    passa por cima do que o vendedor pôs à mão);
  * o orçamento, ao ser amarrado ao lead ou salvo (esse sobrescreve: é o
    documento formal);
  * a ficha do painel e a ficha do Cockpit (à mão);
  * o botão "perguntar" do card, que abre a conversa com a pergunta pronta.

E o funil usa isso pra separar a coluna em meses (`agrupar`) e pra oferecer o
trilho de meses em cima do quadro (`trilho`). Quem está parado há 15+ dias sem
mensagem vai pra uma dobra no pé da coluna — sem sair da etapa, sem mudar nada no
banco (regra 0 desta base: nada do cliente se perde).
"""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, timedelta, timezone

_log = logging.getLogger("evento_lead")

# Dias sem mensagem (em nenhum sentido) pra um lead ir pra dobra "Parados". Decisão
# do dono em 04/09/2026: 15. Com 7 dobraria 157 dos 224 da Prime; com 30, nenhum.
PARADO_DIAS = 15

# A pergunta que o botão do card põe pronta na conversa — a mesma que o agente faz.
PERGUNTA_DATA = "Pra qual data você está pensando? E mais ou menos quantos convidados? 😊"

_MESES = ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"]
_MESES_ROT = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]

_ISO = re.compile(r"^(\d{4})-(\d{1,2})-(\d{1,2})")
_BR = re.compile(r"^(\d{1,2})[/.-](\d{1,2})[/.-](\d{2,4})")
_RE_MES = re.compile(r"^\d{4}-(0[1-9]|1[0-2])$")


# ------------------------------------------------------------------ parse
def parse_data(v) -> date | None:
    """'2026-12-31', '31/12/2026', '31/12/26' → date. Qualquer outra coisa → None.

    Aceita os dois formatos porque a IA devolve ora um, ora outro ("31/12/2026" é o
    que o teste do agente já usava), e a ficha manda ISO do <input type=date>."""
    if v is None:
        return None
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v).strip()
    m = _ISO.match(s)
    if m:
        y, mo, d = int(m[1]), int(m[2]), int(m[3])
    else:
        m = _BR.match(s)
        if not m:
            return None
        d, mo, y = int(m[1]), int(m[2]), int(m[3])
        if y < 100:
            y += 2000
    try:
        return date(y, mo, d)
    except ValueError:
        return None


def parse_convidados(v) -> int | None:
    """150, '150', '150 pessoas', '1.500' → int. Zero, vazio ou absurdo → None."""
    s = str(v or "").strip()
    if not s:
        return None
    m = re.match(r"^\s*(\d{1,3}(?:\.\d{3})*|\d+)", s)
    if not m:
        return None
    n = int(m[1].replace(".", ""))
    return n if 0 < n < 100000 else None


def parse_tipo(v) -> str | None:
    s = " ".join(str(v or "").split())[:40]
    return s or None


def normalizar(evento: dict | None) -> dict:
    """Só o que veio VÁLIDO: {'data': date, 'tipo': str, 'convidados': int} (chaves
    ausentes = não veio). É o formato de `orcamentos.evento` e do JSON do agente."""
    ev = evento if isinstance(evento, dict) else {}
    out: dict = {}
    d = parse_data(ev.get("data"))
    if d:
        out["data"] = d
    t = parse_tipo(ev.get("tipo"))
    if t:
        out["tipo"] = t
    n = parse_convidados(ev.get("convidados"))
    if n:
        out["convidados"] = n
    return out


# ------------------------------------------------------------------ gravar
def gravar(c, conta_id: int, lead_id, evento: dict | None, *, so_vazios: bool = False,
           origem: str | None = None, trecho: str | None = None) -> bool:
    """Grava no lead o que veio preenchido. NUNCA apaga: campo ausente fica como está.

    `so_vazios=True` é o modo do agente e do leitor da conversa: só preenche o que
    ainda está vazio, pra não passar por cima do que o vendedor pôs à mão. O
    orçamento e a ficha usam o modo normal (o que veio sobrescreve o que estava).

    `origem` (conversa · agente · orcamento · mao) e `trecho` (a prova, migração
    198) só são gravados quando ALGUMA das três colunas muda — o selo do card diz
    de onde veio o que está lá, não quem passou por último sem mudar nada.
    Devolve True só quando mudou algo.

    Savepoint por dentro: base sem a migração (ou qualquer erro aqui) não pode
    derrubar quem chamou — o atendimento do agente, o salvamento da proposta."""
    ev = normalizar(evento)
    if not ev or not lead_id:
        return False
    try:
        with c.transaction():
            atual = c.execute(
                "select evento_em, evento_tipo, evento_convidados from prospeccao "
                "where id=%s and conta_id=%s", (int(lead_id), conta_id)).fetchone()
            if not atual:
                return False
            sets, vals = [], []
            for col, chave, cur in (("evento_em", "data", atual[0]),
                                    ("evento_tipo", "tipo", atual[1]),
                                    ("evento_convidados", "convidados", atual[2])):
                novo = ev.get(chave)
                if novo is None or novo == cur or (so_vazios and cur is not None):
                    continue
                sets.append(f"{col}=%s")
                vals.append(novo)
            if not sets:
                return False
            if origem:
                sets.append("evento_origem=%s")
                vals.append(origem)
            if trecho is not None:
                sets.append("evento_trecho=%s")
                vals.append(trecho[:300] or None)
            c.execute(
                f"update prospeccao set {', '.join(sets)}, atualizado_em=now() "  # noqa: S608
                "where id=%s and conta_id=%s", (*vals, int(lead_id), conta_id))
        return True
    except Exception:  # noqa: BLE001
        _log.warning("não gravei o evento no lead %s", lead_id, exc_info=True)
        return False


def sincronizar_do_orcamento(c, conta_id: int, orcamento_id) -> int:
    """Copia `orcamentos.evento` (data, tipo, convidados) pros leads amarrados a essa
    proposta. Chamado quando a proposta é amarrada ao lead e quando é salva/editada.
    Devolve quantos leads receberam. Tolerante: nunca derruba quem chamou."""
    if not orcamento_id:
        return 0
    try:
        with c.transaction():
            r = c.execute("select evento from orcamentos where id=%s and conta_id=%s",
                          (int(orcamento_id), conta_id)).fetchone()
            if not r or not r[0]:
                return 0
            ev = r[0]
            if isinstance(ev, str):
                import json
                ev = json.loads(ev)
            if not normalizar(ev):
                return 0
            leads = c.execute(
                "select id from prospeccao where orcamento_id=%s and conta_id=%s",
                (int(orcamento_id), conta_id)).fetchall()
            n = 0
            for (lid,) in leads:
                if gravar(c, conta_id, lid, ev, origem="orcamento"):
                    n += 1
            return n
    except Exception:  # noqa: BLE001
        _log.warning("não sincronizei o evento do orçamento %s", orcamento_id, exc_info=True)
        return 0


# ------------------------------------------------------------------ rótulos
def mes_chave(d: date) -> str:
    return f"{d.year:04d}-{d.month:02d}"


def mes_valido(chave: str) -> bool:
    return bool(_RE_MES.match(chave or ""))


def mes_rotulo(chave: str) -> str:
    """'2027-01' → 'Jan 27'. Sempre com o ano: as festas da Prime vão de set/26 a
    fev/28, e 'Jan' sozinho não diz qual."""
    y, m = int(chave[:4]), int(chave[5:7])
    return f"{_MESES_ROT[m - 1]} {y % 100:02d}"


def mes_intervalo(chave: str) -> tuple[date, date]:
    """[primeiro dia, primeiro dia do mês seguinte) — pro filtro em SQL."""
    y, m = int(chave[:4]), int(chave[5:7])
    ini = date(y, m, 1)
    fim = date(y + 1, 1, 1) if m == 12 else date(y, m + 1, 1)
    return ini, fim


def data_curta(d: date | None, hoje: date | None = None) -> str:
    """'28 nov' no ano corrente, '16 jan 27' fora dele — cabe numa linha do card."""
    if not d:
        return ""
    hoje = hoje or date.today()
    s = f"{d.day:02d} {_MESES[d.month - 1]}"
    return s if d.year == hoje.year else f"{s} {d.year % 100:02d}"


def linha_evento(tipo, d, convidados) -> str:
    """'💍 Casamento · 14/11/2026 · 150 convidados' — a linha da ficha e do balão."""
    partes = []
    if tipo:
        partes.append(str(tipo))
    if d and hasattr(d, "strftime"):
        partes.append(d.strftime("%d/%m/%Y"))
    if convidados:
        partes.append(f"{int(convidados)} convidados")
    return (icone_tipo(tipo) + " " + " · ".join(partes)) if partes else ""


def icone_tipo(tipo: str | None) -> str:
    t = (tipo or "").lower()
    if "casa" in t or "noiv" in t or "bodas" in t:
        return "💍"
    if "anivers" in t or "15" in t or "debut" in t or "infantil" in t:
        return "🎂"
    if "format" in t:
        return "🎓"
    if "corpor" in t or "empres" in t or "confrat" in t:
        return "🏢"
    if "batiz" in t or "chá" in t or "cha de" in t:
        return "🎀"
    return "🎉"


# ------------------------------------------------------------------ agrupar
def _aware(dt):
    if dt is None:
        return None
    if isinstance(dt, datetime):
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    if isinstance(dt, date):
        return datetime(dt.year, dt.month, dt.day, tzinfo=timezone.utc)
    return None


def ultima_atividade(card: dict, agora: datetime):
    """A última vez que algo aconteceu com o lead: mensagem (qualquer sentido),
    contato registrado ou, sem nada disso, a entrada no funil."""
    cands = [_aware(card.get("ult_em")), _aware(card.get("ultimo_contato_em")),
             _aware(card.get("criado_em"))]
    cands = [x for x in cands if x]
    return max(cands) if cands else agora


def parado(card: dict, agora: datetime | None = None) -> bool:
    agora = agora or datetime.now(timezone.utc)
    return ultima_atividade(card, agora) < agora - timedelta(days=PARADO_DIAS)


def esperando_resposta(card: dict) -> bool:
    """O cliente falou por último e ninguém respondeu (resposta do agente conta como
    resposta, como no Cockpit). É o que pede ação — vai pro topo da coluna."""
    ult = card.get("ult")
    return bool(ult) and not ult.get("minha")


def _semana_chave(d: date) -> str:
    seg = d - timedelta(days=d.weekday())
    return seg.isoformat()


def agrupar(cards: list[dict], agora: datetime | None = None, *,
            por_semana: bool = False) -> list[dict]:
    """Separa os cards de UMA coluna em grupos, na ordem em que aparecem:

      0. esperando resposta (o cliente falou por último) — é o que pede ação;
      1. por mês do EVENTO, do mais próximo pro mais distante;
      2. sem data, por mês de ENTRADA no funil, do mais novo pro mais velho
         (ou por SEMANA de entrada, quando o quadro já está num mês só);
      3. parados há PARADO_DIAS+ (qualquer data), numa dobra fechada no pé.

    Cada grupo: {tipo: 'esperando'|'evento'|'entrada'|'parado', chave, rotulo,
    cards, n}. Uma coluna que só tem um grupo de entrada volta com rotulo vazio —
    não há o que separar, e um cabeçalho ali seria enfeite."""
    agora = agora or datetime.now(timezone.utc)
    limite = agora - timedelta(days=PARADO_DIAS)
    esperando: list = []
    evento: dict[str, list] = {}
    entrada: dict[str, list] = {}
    parados: list = []
    for c in cards:
        # quem entrou pela pílula de fora (esperando resposta, festa em 30 dias)
        # entrou porque é urgente: nunca se esconde na dobra
        if (ultima_atividade(c, agora) < limite and not c.get("fora")
                and not festa_em_30_dias(c, agora.date())):
            parados.append(c)
        elif esperando_resposta(c):
            esperando.append(c)
        elif c.get("evento_em"):
            evento.setdefault(mes_chave(c["evento_em"]), []).append(c)
        else:
            base = (_aware(c.get("criado_em")) or agora).date()
            entrada.setdefault(_semana_chave(base) if por_semana else mes_chave(base), []).append(c)
    grupos = []
    if esperando:
        # a mensagem mais nova primeiro: é a que está esperando há menos e dói mais
        esperando.sort(key=lambda x: _aware(x.get("ult_em")) or agora, reverse=True)
        grupos.append({"tipo": "esperando", "chave": "esperando", "rotulo": "🟢 Esperando resposta",
                       "cards": esperando, "n": len(esperando)})
    for k in sorted(evento):
        lst = sorted(evento[k], key=lambda x: x["evento_em"])
        grupos.append({"tipo": "evento", "chave": k, "rotulo": mes_rotulo(k),
                       "cards": lst, "n": len(lst)})
    for k in sorted(entrada, reverse=True):
        if por_semana:
            seg = date.fromisoformat(k)
            rot = f"Sem data · semana de {seg.day:02d}/{seg.month:02d}"
        else:
            rot = "Sem data · entrou em " + _MESES[int(k[5:7]) - 1]
        grupos.append({"tipo": "entrada", "chave": k, "rotulo": rot,
                       "cards": entrada[k], "n": len(entrada[k])})
    if parados:
        grupos.append({"tipo": "parado", "chave": "parado",
                       "rotulo": f"Parados {PARADO_DIAS}+ dias",
                       "cards": parados, "n": len(parados)})
    if len(grupos) == 1 and grupos[0]["tipo"] == "entrada":
        grupos[0]["rotulo"] = ""
    return grupos


# ------------------------------------------------------------------ o período do quadro
def periodo_atual(hoje: date | None = None) -> str:
    return mes_chave(hoje or date.today())


def no_periodo(card: dict, entrou: str) -> bool:
    """O card entrou no mês `entrou` ('AAAA-MM')? 'tudo' aceita todo mundo."""
    if entrou == "tudo":
        return True
    base = _aware(card.get("criado_em"))
    return bool(base) and mes_chave(base.date()) == entrou


def festa_em_30_dias(card: dict, hoje: date | None = None) -> bool:
    hoje = hoje or date.today()
    d = card.get("evento_em")
    return bool(d) and hoje <= d <= hoje + timedelta(days=30)


def meses_entrada(cards: list[dict], hoje: date | None = None) -> list[dict]:
    """As pílulas "Entraram em": um mês por leva, do mais novo pro mais velho, com o
    mês corrente sempre presente (mesmo com zero — é o padrão), e "Tudo" no fim.
    [{chave, rotulo, n}]."""
    hoje = hoje or date.today()
    cont: dict[str, int] = {periodo_atual(hoje): 0}
    for c in cards:
        base = _aware(c.get("criado_em"))
        if base:
            k = mes_chave(base.date())
            cont[k] = cont.get(k, 0) + 1
    itens = [{"chave": k, "rotulo": _NOME_MES_CHEIO[int(k[5:7]) - 1].capitalize()
              + ("" if k[:4] == str(hoje.year) else f" {k[2:4]}"), "n": n}
             for k, n in sorted(cont.items(), reverse=True)]
    itens.append({"chave": "tudo", "rotulo": "Tudo", "n": len(cards)})
    return itens


_NOME_MES_CHEIO = ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho",
                   "agosto", "setembro", "outubro", "novembro", "dezembro"]



def trilho(contagens: dict, mes_sel: str = "") -> list[dict]:
    """As pílulas de mês em cima do quadro. `contagens` é {chave_mes|None: n}
    (None = sem data). Devolve [{chave, rotulo, n, on, sem}] com 'Todos' na frente
    e 'Sem data' no fim."""
    total = sum(contagens.values())
    itens = [{"chave": "", "rotulo": "Todos", "n": total, "on": not mes_sel, "sem": False}]
    for k in sorted(k for k in contagens if k):
        itens.append({"chave": k, "rotulo": mes_rotulo(k), "n": contagens[k],
                      "on": mes_sel == k, "sem": False})
    itens.append({"chave": "sem", "rotulo": "Sem data", "n": contagens.get(None, 0),
                  "on": mes_sel == "sem", "sem": True})
    return itens


# ------------------------------------------------------------------ vista por mês
_DIAS = ["seg", "ter", "qua", "qui", "sex", "sáb", "dom"]


def visita_curta(inicio, agora: datetime | None = None) -> str:
    """'Visita sáb 10h' na semana; 'Visita 21/11' depois disso. Cabe num selo.

    Em horário de Brasília: é o vendedor de Teresina que lê o selo, e a agenda
    guarda em UTC."""
    if not hasattr(inicio, "strftime"):
        return ""
    agora = agora or datetime.now(timezone.utc)
    dt = _aware(inicio)
    try:
        from finance import agenda as _ag
        dt_br = dt.astimezone(_ag.BRT)
    except Exception:  # noqa: BLE001
        dt_br = dt
    if 0 <= (dt - agora).days < 7:
        h = dt_br.strftime("%Hh%M").replace("h00", "h")
        return f"Visita {_DIAS[dt_br.weekday()]} {h}"
    return "Visita " + dt_br.strftime("%d/%m")


def colunas_por_mes(cards: list[dict], agora: datetime | None = None):
    """A VISTA "POR MÊS DO EVENTO": as colunas viram meses e a etapa vai pro card.

    Devolve (colunas, grupos): `colunas` é [(chave, rótulo)] — um mês por coluna,
    do mais próximo pro mais distante, e "Sem data" por último; `grupos` tem, por
    chave, a lista de grupos no formato de `agrupar`. Nos meses a coluna é lisa
    (um grupo só, ordenado pela data da festa); em "Sem data" vale o agrupamento
    de sempre (mês de entrada + dobra dos parados), porque é a fila de trabalho.

    Perdido fica de fora: é a visão de agenda de vendas — o que está em negociação
    pra cada mês — e quem já perdeu não é festa a fazer."""
    agora = agora or datetime.now(timezone.utc)
    por_mes: dict[str, list] = {}
    sem: list = []
    for c in cards:
        if c.get("status") == "perdido":
            continue
        if c.get("evento_em"):
            por_mes.setdefault(mes_chave(c["evento_em"]), []).append(c)
        else:
            sem.append(c)
    colunas = [(k, mes_rotulo(k)) for k in sorted(por_mes)]
    grupos = {k: [{"tipo": "evento", "chave": k, "rotulo": "",
                   "cards": sorted(v, key=lambda x: x["evento_em"]), "n": len(v)}]
              for k, v in por_mes.items()}
    colunas.append(("sem", "Sem data"))
    grupos["sem"] = agrupar(sem, agora)
    return colunas, grupos


def origem_apos_edicao(alvo: dict, tipo, data, convidados) -> tuple:
    """(evento_origem, evento_pista) pra gravar depois de uma edição À MÃO (ficha,
    balão): se algum dos três mudou, a origem vira 'mao' e a pista do leitor sai —
    o vendedor acabou de decidir. Senão, fica tudo como estava."""
    novo = (parse_tipo(tipo), parse_data(data), parse_convidados(convidados))
    atual = (alvo.get("evento_tipo") or None, alvo.get("evento_em") or None,
             alvo.get("evento_convidados") or None)
    if novo != atual:
        return ("mao", None)
    return (alvo.get("evento_origem"), alvo.get("evento_pista"))
