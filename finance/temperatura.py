"""A TEMPERATURA DO LEAD, sugerida pelos fatos — § 3 e § 5 do Projeto Adaptado.

    "Sempre que possível, o CRM deverá analisar as informações existentes na conversa
     e sugerir automaticamente a temperatura do lead CONTACTADO. O vendedor poderá
     ajustar a classificação quando necessário."

O QUE ESTAVA ERRADO, MEDIDO EM 12/09/2026
Na conta 34, 281 dos 284 leads em "Contatado" estavam marcados QUENTE. Três frios,
zero mornos. E 15 dos 21 PERDIDOS também estavam quentes.

A causa não é descuido de vendedor: é o código. Todo lead que vira lead é carimbado
`temperatura='quente'` na promoção — em `painel_prospeccao._promover_para_lead`,
`campanhas_motor`, `email_inbound` e na reativação. O esquentador roda sempre; o
único esfriador é o agente de IA (`agente.py`, `pode_qualificar`), e na Prime o
agente está DESLIGADO. Resultado: o campo satura em quente e para de informar.

Isso não é um detalhe: sem temperatura que distinga, a fila de prioridade do § 7
(`follow_up.prioridade`) é quase um no-op — todo mundo cai no 1º nível.

POR QUE FATO E NÃO TEXTO
Dá pra ler a conversa com IA — o agente já faz isso. Mas ele só roda quando está
ligado ATENDENDO cliente, que é uma decisão de outra ordem. E os fatos que separam
quente de frio já estão em `mensagens`, de graça: quem falou por último, há quanto
tempo, e quantas vezes falamos sem resposta. É o mesmo material que a régua e o
follow-up já leem, e dá pra testar sem depender de modelo nenhum.

É A MESMA RECEITA DO `follow_up.py`, e está escrita no topo dele:

    "Cobrar o preenchimento abriria o dia 1 com 273 leads em 'sem próxima ação'.
     Então o sistema PROPÕE (a escada) e o vendedor CORRIGE."

Aqui idem: o motor sugere, o vendedor ajusta na ficha, e o ajuste MANUAL VENCE
enquanto for a última palavra — exatamente como a marcação de próxima ação.

NASCE DESLIGADO, E TEM O MODO 'observando'
`funil_regua.temperatura_modo` é 'off'. Em 'observando' o motor calcula e conta o
que MUDARIA, sem escrever uma linha — é o ensaio que deixa o dono ver os 281 antes
de qualquer coisa acontecer. Só 'ligado' escreve.
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

from finance import funil_regua as _fr

_log = logging.getLogger("openclaw.temperatura")

#: vizinho dos locks da régua (771147), follow-up (771148), teto (771149) e agenda (771150)
_LOCK = 771151

TEMPERATURAS = ("frio", "morno", "quente")

#: o que o motor escreve como motivo no histórico — é por ele que se reconhece
MOTIVO = "temperatura"

_PADRAO = {"temp_quente_h": 48, "temp_morno_dias": 7, "temp_frio_tentativas": 3}


def config(c, conta_id: int) -> dict:
    """Os limiares da conta, com o padrão do ramo no vazio (migração 228)."""
    base = _fr.config(c, conta_id)
    r = c.execute(
        """select coalesce(temperatura_modo, 'off'),
                  temp_quente_h, temp_morno_dias, temp_frio_tentativas
             from funil_regua where conta_id=%s""", (conta_id,)).fetchone()
    out = dict(_PADRAO)
    for k in _PADRAO:
        v = base.get(k)
        if v is not None:
            out[k] = v
    modo = "off"
    # quais limiares a CONTA gravou de verdade — é o que a Régua usa pra dizer
    # "definido por você" em vez de "padrão do ramo". Sem isso o dono não tem como
    # saber se o número que ele lê é escolha dele ou herança (migração 228).
    escolhidas = set()
    if r:
        modo = r[0] if r[0] in ("off", "observando", "ligado") else "off"
        for k, v in zip(("temp_quente_h", "temp_morno_dias", "temp_frio_tentativas"), r[1:]):
            if v is not None:
                out[k] = v
                escolhidas.add(k)
    out["temperatura_modo"] = modo
    out["_escolhidas_temp"] = escolhidas
    return out


def sugerir(*, ult_in, ult_out, tentativas: int, agora: datetime, cfg: dict) -> tuple[str, str]:
    """(temperatura, por quê) a partir dos fatos da conversa. Pura, sem banco.

    A ordem dos testes é a regra, e ela responde ao § 3 do documento:

      QUENTE  o cliente falou por último, e faz pouco. Responder é o sinal comercial
              mais barato e mais honesto que existe — quem pede orçamento, informa
              data ou pergunta disponibilidade está, antes de tudo, RESPONDENDO.
      FRIO    falamos N vezes e ele não voltou; ou nunca falou nada. É o "pouca
              interação / informações insuficientes" do documento.
      MORNO   o resto: a conversa existe dos dois lados, mas a última palavra é
              nossa, ou a dele já envelheceu.

    Nunca devolve None: todo lead tem uma temperatura, e "não sei" viraria um quarto
    estado que a tela não sabe pintar.
    """
    quente_h = int(cfg.get("temp_quente_h") or _PADRAO["temp_quente_h"])
    morno_d = int(cfg.get("temp_morno_dias") or _PADRAO["temp_morno_dias"])
    frio_t = int(cfg.get("temp_frio_tentativas") or _PADRAO["temp_frio_tentativas"])

    if ult_in and (agora - ult_in) <= timedelta(hours=quente_h):
        return "quente", f"o cliente falou há menos de {quente_h}h"
    if ult_in is None:
        return "frio", ("nunca respondeu" if ult_out else "nenhuma conversa ainda")
    if int(tentativas or 0) >= frio_t:
        return "frio", f"{int(tentativas)} tentativas nossas sem resposta"
    if (agora - ult_in) > timedelta(days=morno_d):
        return "frio", f"o cliente não fala há mais de {morno_d} dias"
    return "morno", "conversa dos dois lados, a bola está com ele"


_SQL = """
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
    left join msg on msg.lead = cv.prospeccao_id
   where cv.conta_id = %(conta)s and m.direcao='out'
     and (msg.ult_in is null or m.criado_em > msg.ult_in)
   group by cv.prospeccao_id)
select p.id, coalesce(nullif(p.temperatura,''), 'frio'),
       msg.ult_in, msg.ult_out, coalesce(tent.n, 0), p.atualizado_em
  from prospeccao p
  left join msg  on msg.lead  = p.id
  left join tent on tent.lead = p.id
 where p.conta_id = %(conta)s and p.estagio = 'lead'
   and p.status not in (select fe.chave from funil_etapas fe
                         where fe.conta_id = p.conta_id
                           and fe.fase in ('fechamento','pos')
                        union all select 'ganho' union all select 'perdido')
 order by p.id
"""


def avaliar(c, conta_id: int, agora: datetime | None = None, cfg: dict | None = None) -> list[dict]:
    """O que o motor sugere pra cada lead EM JOGO. Não escreve nada.

    Lead fechado ou perdido fica de fora: a temperatura é sobre quem ainda pode
    comprar. Foi por não excluí-los que 15 dos 21 perdidos da Prime estão quentes.
    """
    agora = agora or datetime.now(timezone.utc)
    cfg = cfg or config(c, conta_id)
    out = []
    for lid, atual, ult_in, ult_out, tent, _at in c.execute(_SQL, {"conta": conta_id}).fetchall():
        nova, porque = sugerir(ult_in=ult_in, ult_out=ult_out, tentativas=tent,
                               agora=agora, cfg=cfg)
        out.append({"id": lid, "de": atual, "para": nova, "porque": porque,
                    "muda": nova != atual})
    return out


def resumo(linhas: list[dict]) -> dict:
    """Quantos ficariam em cada temperatura, e quantos mudam. É o que o ensaio mostra."""
    r = {t: 0 for t in TEMPERATURAS}
    for x in linhas:
        r[x["para"]] = r.get(x["para"], 0) + 1
    r["mudam"] = sum(1 for x in linhas if x["muda"])
    r["total"] = len(linhas)
    return r


def aplicar(c, conta_id: int, linhas: list[dict]) -> int:
    """Grava as mudanças. Devolve quantos leads mudaram de temperatura.

    Uma linha por lead em `funil_movimentos`, com `de`/`para` carregando a
    temperatura e motivo 'temperatura'. É informação do cliente mudando de valor
    (CLAUDE.md §0): sem registro ninguém consegue dizer que o lead era quente ontem,
    nem desfazer.
    """
    n = 0
    for x in linhas:
        if not x["muda"]:
            continue
        c.execute("""update prospeccao set temperatura=%s, atualizado_em=now()
                      where id=%s and conta_id=%s""", (x["para"], x["id"], conta_id))
        _fr.registrar_movimento(c, conta_id, x["id"], x["de"], x["para"], MOTIVO, None)
        n += 1
    return n


def rodar(pool, agora: datetime | None = None) -> dict:
    """Uma passada nas contas que ligaram. Chamada pelo poller.

    Best-effort por conta: um lead torto numa conta não pode travar as outras.
    """
    total = {"contas": 0, "avaliados": 0, "mudados": 0, "ensaios": 0}
    with pool.connection() as lockc:
        if not lockc.execute("select pg_try_advisory_lock(%s)", (_LOCK,)).fetchone()[0]:
            return total
        try:
            with pool.connection() as c:
                contas = [r[0] for r in c.execute(
                    """select conta_id from funil_regua
                        where coalesce(temperatura_modo,'off') <> 'off'""").fetchall()]
            for cid in contas:
                try:
                    with pool.connection() as c:
                        cfg = config(c, cid)
                        linhas = avaliar(c, cid, agora, cfg)
                        total["contas"] += 1
                        total["avaliados"] += len(linhas)
                        if cfg["temperatura_modo"] == "ligado":
                            total["mudados"] += aplicar(c, cid, linhas)
                            c.commit()
                        else:
                            total["ensaios"] += sum(1 for x in linhas if x["muda"])
                except Exception:  # noqa: BLE001
                    _log.warning("temperatura falhou na conta %s", cid, exc_info=True)
        finally:
            lockc.execute("select pg_advisory_unlock(%s)", (_LOCK,))
    return total
