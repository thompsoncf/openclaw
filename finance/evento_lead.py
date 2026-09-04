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
def gravar(c, conta_id: int, lead_id, evento: dict | None, *, so_vazios: bool = False) -> bool:
    """Grava no lead o que veio preenchido. NUNCA apaga: campo ausente fica como está.

    `so_vazios=True` é o modo do agente: só preenche o que ainda está vazio, pra não
    passar por cima do que o vendedor pôs à mão. O orçamento e a ficha usam o modo
    normal (o que veio sobrescreve o que estava).

    Savepoint por dentro: base sem a migração 197 (ou qualquer erro aqui) não pode
    derrubar quem chamou — o atendimento do agente, o salvamento da proposta."""
    ev = normalizar(evento)
    if not ev or not lead_id:
        return False
    if so_vazios:
        sets = ["evento_em=coalesce(evento_em,%s)", "evento_tipo=coalesce(evento_tipo,%s)",
                "evento_convidados=coalesce(evento_convidados,%s)"]
    else:
        sets = ["evento_em=coalesce(%s,evento_em)", "evento_tipo=coalesce(%s,evento_tipo)",
                "evento_convidados=coalesce(%s,evento_convidados)"]
    try:
        with c.transaction():
            c.execute(
                f"update prospeccao set {', '.join(sets)}, atualizado_em=now() "  # noqa: S608
                "where id=%s and conta_id=%s",
                (ev.get("data"), ev.get("tipo"), ev.get("convidados"), int(lead_id), conta_id))
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
                if gravar(c, conta_id, lid, ev):
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


def agrupar(cards: list[dict], agora: datetime | None = None) -> list[dict]:
    """Separa os cards de UMA coluna em grupos, na ordem em que aparecem:

      1. por mês do EVENTO, do mais próximo pro mais distante;
      2. sem data, por mês de ENTRADA no funil, do mais novo pro mais velho;
      3. parados há PARADO_DIAS+ (qualquer data), numa dobra fechada no pé.

    Cada grupo: {tipo: 'evento'|'entrada'|'parado', chave, rotulo, cards, n}.
    Uma coluna que só tem um grupo de entrada volta com rotulo vazio — não há o
    que separar, e um cabeçalho ali seria enfeite."""
    agora = agora or datetime.now(timezone.utc)
    limite = agora - timedelta(days=PARADO_DIAS)
    evento: dict[str, list] = {}
    entrada: dict[str, list] = {}
    parados: list = []
    for c in cards:
        if ultima_atividade(c, agora) < limite:
            parados.append(c)
        elif c.get("evento_em"):
            evento.setdefault(mes_chave(c["evento_em"]), []).append(c)
        else:
            base = _aware(c.get("criado_em")) or agora
            entrada.setdefault(mes_chave(base.date()), []).append(c)
    grupos = []
    for k in sorted(evento):
        lst = sorted(evento[k], key=lambda x: x["evento_em"])
        grupos.append({"tipo": "evento", "chave": k, "rotulo": mes_rotulo(k),
                       "cards": lst, "n": len(lst)})
    for k in sorted(entrada, reverse=True):
        grupos.append({"tipo": "entrada", "chave": k,
                       "rotulo": "Sem data · entrou em " + _MESES[int(k[5:7]) - 1],
                       "cards": entrada[k], "n": len(entrada[k])})
    if parados:
        grupos.append({"tipo": "parado", "chave": "parado",
                       "rotulo": f"Parados {PARADO_DIAS}+ dias",
                       "cards": parados, "n": len(parados)})
    if len(grupos) == 1 and grupos[0]["tipo"] == "entrada":
        grupos[0]["rotulo"] = ""
    return grupos


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
