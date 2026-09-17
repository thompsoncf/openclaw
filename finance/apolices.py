"""A carteira de apólices da corretora, e o relógio que ela dá de presente.

O QUE É. Uma corretora de seguros não vende uma vez: ela vende todo ano, pro
mesmo cliente, na mesma data. O fim da vigência é o único compromisso comercial
que já está marcado no calendário no dia em que a venda fecha — e é o que esta
tabela guarda, porque até hoje não era guardado em lugar nenhum (o perfil de
Raio-X da corretora, migração 242, deixou `fu_festa_dias` nulo justamente por
isso).

AS QUATRO DECISÕES DO DONO (17/09/2026), que este módulo implementa e não
reinterpreta:

1. **A régua é 60/30/15.** Três toques, não cinco. A escada que o mercado usa é
   90/60/45/30/15, e cinco avisos sobre a MESMA apólice viram ruído até o
   corretor ignorar todos. Os 90 dias viram LISTA ("o que vem por aí"), não
   alerta — é por isso que `HORIZONTE` e `DEGRAUS` são coisas diferentes aqui.
2. **A comissão sai de um percentual padrão por seguradora**, com sobrescrita na
   apólice. A comissão não aparece em lugar nenhum do documento da apólice (é
   papel do cliente, e o cliente não vê quanto o corretor ganha), então ela tem
   que vir de um cadastro — `seguros_comissao`.
3. **O alerta é do corretor** dono da apólice; o dono vê a carteira inteira na
   tela. Mesmo desenho da Fila do vendedor.
4. **Começa por auto.** A tabela aguenta todos os ramos pelo `bem` jsonb; o que
   muda por ramo é o formulário, e hoje só o de auto existe.

SOBRE A BASE DA COMISSÃO — e isto corrige a conta do próprio mockup. O prêmio de
uma apólice tem duas partes: o LÍQUIDO (o que a seguradora cobra pelo risco) e o
IOF (imposto, que a seguradora só repassa ao governo). A comissão do corretor
incide sobre o LÍQUIDO. O mockup somou os dois e mostrou "R$ 4.088,57 × 20% =
R$ 817,71"; o certo, com o mesmo papel, é R$ 3.807,57 × 20% = R$ 761,51. São 56
reais de diferença numa apólice só — numa carteira inteira é a comissão parecendo
maior do que é todo mês. Por isso `premio_centavos` aqui é SEMPRE o líquido, e o
IOF mora em coluna própria.
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP

_log = logging.getLogger("openclaw.apolices")

#: Os degraus da régua, em dias que faltam. Ordem decrescente importa: `degrau_de`
#: devolve o PRIMEIRO que a apólice alcança, e o alerta sai uma vez por degrau.
DEGRAUS = (60, 30, 15)

#: Até onde a LISTA enxerga. Não é alerta — é "o que vem por aí", que o corretor
#: abre quando quer, e é de propósito maior que o primeiro degrau: entre 90 e 60
#: dias a apólice aparece na tela sem incomodar ninguém.
HORIZONTE = 90

#: Os ramos. `auto` é o único com formulário hoje (decisão 4); os outros já
#: gravam, e entram na tela conforme a corretora pedir.
RAMOS = (
    ("auto", "Auto"),
    ("frota", "Frota"),
    ("vida", "Vida"),
    ("residencial", "Residencial"),
    ("empresarial", "Empresarial"),
    ("condominio", "Condomínio"),
    ("saude", "Saúde"),
    ("rc", "Responsabilidade civil"),
    ("garantia", "Garantia"),
    ("viagem", "Viagem"),
    ("outro", "Outro"),
)
_RAMOS = dict(RAMOS)

#: Os cinco estados do ciclo. São cinco porque o papel tem DOIS: o documento que
#: chega primeiro é uma PROPOSTA (a seguradora tem 25 dias pra aceitar, e há
#: cobertura provisória nesse meio-tempo), e só depois vira apólice com número.
SITUACOES = (
    ("proposta", "Proposta"),
    ("vigente", "Vigente"),
    ("renovada", "Renovada"),
    ("vencida", "Vencida"),
    ("cancelada", "Cancelada"),
)
_SITUACOES = dict(SITUACOES)

#: As que ainda contam pro relógio. As outras já acabaram a vida útil delas e não
#: entram em alerta nem em lista — é o mesmo recorte do índice parcial da 278.
VIVAS = ("proposta", "vigente")


def rotulo_ramo(chave: str | None) -> str:
    return _RAMOS.get((chave or "").strip().lower(), (chave or "").strip() or "—")


def rotulo_situacao(chave: str | None) -> str:
    return _SITUACOES.get((chave or "").strip().lower(), (chave or "").strip() or "—")


def degrau_de(dias: int | None) -> int | None:
    """Em que degrau da régua esta apólice está hoje — ou None se ainda não entrou.

    Devolve o degrau MAIS APERTADO que ela já alcançou, e a ordem importa. A
    tentação é devolver o maior ("ela passou do 60"), e isso quebra a régua
    inteira: o dedup é por (apólice, degrau), então uma apólice que já recebeu o
    aviso de 60 dias devolveria 60 pra sempre, `_primeira_vez` recusaria todas as
    vezes seguintes, e os avisos de 30 e de 15 NUNCA sairiam. Três toques viram um.

    E é por isso também que a carteira recém-carregada não fica calada: a apólice
    digitada faltando 20 dias (o caso da Liberal, que vai digitar tudo de uma vez)
    cai direto no degrau 30 e avisa hoje, em vez de esperar os 15.

    Vencida (dias < 0) não é degrau: quem já venceu não precisa de aviso de
    renovação, precisa de outra conversa.
    """
    if dias is None or dias < 0:
        return None
    for d in sorted(DEGRAUS):
        if dias <= d:
            return d
    return None


def dias_para(vigencia_fim, hoje: date | None = None) -> int | None:
    if vigencia_fim is None:
        return None
    return (vigencia_fim - (hoje or date.today())).days


# --------------------------------------------------------------- comissão

def pct_padrao(c, conta_id: int, seguradora: str, ramo: str) -> Decimal | None:
    """O percentual cadastrado pra esta seguradora, neste ramo.

    Tenta o ramo exato e cai no curinga (`ramo = ''`): a corretora cadastra
    "Allianz 20%" uma vez e só detalha onde o percentual foge do padrão. Sem
    cadastro nenhum devolve None — e None é a resposta honesta, não zero: zero
    diria "essa apólice não paga comissão", que é outra afirmação.
    """
    seg = (seguradora or "").strip()
    if not seg:
        return None
    r = c.execute(
        """select pct from seguros_comissao
            where conta_id = %s and lower(seguradora) = lower(%s)
              and ramo in (%s, '')
            order by (ramo <> '') desc
            limit 1""",
        (conta_id, seg, (ramo or "").strip().lower())).fetchone()
    return Decimal(str(r[0])) if r else None


def comissao_de(premio_centavos, pct, comissao_centavos=None) -> int | None:
    """A comissão em centavos: o valor fechado manda; senão, percentual × líquido.

    O valor digitado ganha do percentual de propósito. Quando a corretora recebe o
    extrato da seguradora e digita o número exato, esse número é FATO — e um
    percentual estimado não tem o direito de sobrescrever um fato.
    """
    if comissao_centavos is not None:
        return int(comissao_centavos)
    if pct is None:
        return None
    base = Decimal(int(premio_centavos or 0))
    v = (base * Decimal(str(pct)) / Decimal(100)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
    return int(v)


def comissoes(pool, conta_id: int) -> list[dict]:
    """O cadastro de percentuais, pra tela. Curinga primeiro, depois por ramo."""
    with pool.connection() as c:
        rows = c.execute(
            "select id, seguradora, ramo, pct from seguros_comissao "
            "where conta_id=%s order by lower(seguradora), ramo", (conta_id,)).fetchall()
    return [{"id": i, "seguradora": s, "ramo": r, "ramo_txt": rotulo_ramo(r) if r else "todos os ramos",
             "pct": Decimal(str(p))} for (i, s, r, p) in rows]


def salvar_comissao(pool, conta_id: int, seguradora: str, ramo: str, pct) -> bool:
    seg = (seguradora or "").strip()
    if not seg:
        return False
    ramo = (ramo or "").strip().lower()
    if ramo and ramo not in _RAMOS:
        return False
    try:
        pct = Decimal(str(pct).replace(",", ".").strip())
    except Exception:  # noqa: BLE001
        return False
    if pct < 0 or pct > 100:
        return False
    with pool.connection() as c:
        with c.transaction():
            c.execute(
                """insert into seguros_comissao (conta_id, seguradora, ramo, pct)
                   values (%s,%s,%s,%s)
                   on conflict (conta_id, lower(seguradora), ramo)
                   do update set pct = excluded.pct, seguradora = excluded.seguradora,
                                 atualizado_em = now()""",
                (conta_id, seg, ramo, pct))
    return True


def apagar_comissao(pool, conta_id: int, cid: int) -> None:
    with pool.connection() as c:
        with c.transaction():
            c.execute("delete from seguros_comissao where id=%s and conta_id=%s", (cid, conta_id))


# --------------------------------------------------------------- carteira

_COLS = """a.id, a.cliente_id, a.corretor_id, a.seguradora, a.ramo,
           a.numero_proposta, a.numero_apolice, a.vigencia_inicio, a.vigencia_fim,
           a.situacao, a.premio_centavos, a.iof_centavos, a.franquia_centavos,
           a.comissao_pct, a.comissao_centavos, a.classe_bonus, a.parcelas,
           a.dia_vencimento, a.bem, a.condutor, a.coberturas, a.renovacao_de, a.obs"""


def _linha(r, hoje: date, pct_fallback=None) -> dict:
    (i, cli, cor, seg, ramo, n_prop, n_apol, v_ini, v_fim, sit, premio, iof, franquia,
     pct, com, bonus, parcelas, dia_venc, bem, condutor, coberturas, renov, obs) = r[:23]
    pct = Decimal(str(pct)) if pct is not None else pct_fallback
    dias = dias_para(v_fim, hoje)
    return {
        "id": i, "cliente_id": cli, "corretor_id": cor,
        "seguradora": seg, "ramo": ramo, "ramo_txt": rotulo_ramo(ramo),
        "numero_proposta": n_prop, "numero_apolice": n_apol,
        "vigencia_inicio": v_ini, "vigencia_fim": v_fim,
        "situacao": sit, "situacao_txt": rotulo_situacao(sit),
        "premio_centavos": int(premio or 0), "iof_centavos": int(iof or 0),
        "total_centavos": int(premio or 0) + int(iof or 0),
        "franquia_centavos": int(franquia or 0),
        "comissao_pct": pct,
        "comissao_estimada": comissao_de(premio, pct, com),
        "comissao_fechada": com is not None,
        "classe_bonus": bonus, "parcelas": parcelas, "dia_vencimento": dia_venc,
        "bem": bem or {}, "condutor": condutor or {}, "coberturas": coberturas or [],
        "renovacao_de": renov, "obs": obs,
        "dias": dias, "degrau": degrau_de(dias),
    }


def a_vencer(pool, conta_id: int, *, dias: int = HORIZONTE, hoje: date | None = None,
             corretor_id: int | None = None) -> list[dict]:
    """A carteira que vence nos próximos `dias`, do mais urgente pro mais folgado.

    Traz também o que JÁ venceu e continua marcado como vivo: não é ruído, é a
    linha que ninguém tocou — e escondê-la deixaria a corretora com um cliente
    descoberto e a tela dizendo que está tudo bem.

    `corretor_id` recorta pro corretor (a fila dele). Sem ele vem a carteira
    inteira, que é o que o dono vê.
    """
    hoje = hoje or date.today()
    sql = (f"select {_COLS}, coalesce(nullif(cl.nome,''), '') "
           "  from apolices a left join clientes cl on cl.id = a.cliente_id "
           " where a.conta_id = %s and a.situacao = any(%s) "
           "   and a.vigencia_fim <= %s")
    args: list = [conta_id, list(VIVAS), hoje + timedelta(days=int(dias))]
    if corretor_id is not None:
        sql += " and a.corretor_id = %s"
        args.append(corretor_id)
    sql += " order by a.vigencia_fim, a.id"
    with pool.connection() as c:
        rows = c.execute(sql, tuple(args)).fetchall()
        padrao = {}
        saida = []
        for r in rows:
            chave = ((r[3] or "").lower(), (r[4] or "").lower())
            if chave not in padrao:
                padrao[chave] = pct_padrao(c, conta_id, r[3], r[4])
            d = _linha(r, hoje, padrao[chave])
            d["cliente"] = r[23] or "—"
            saida.append(d)
    return saida


def listar(pool, conta_id: int, *, hoje: date | None = None) -> list[dict]:
    """A carteira inteira, viva e morta, pra tela de cadastro."""
    hoje = hoje or date.today()
    with pool.connection() as c:
        rows = c.execute(
            f"select {_COLS}, coalesce(nullif(cl.nome,''), '') "
            "  from apolices a left join clientes cl on cl.id = a.cliente_id "
            " where a.conta_id = %s order by a.vigencia_fim desc, a.id desc",
            (conta_id,)).fetchall()
        padrao = {}
        saida = []
        for r in rows:
            chave = ((r[3] or "").lower(), (r[4] or "").lower())
            if chave not in padrao:
                padrao[chave] = pct_padrao(c, conta_id, r[3], r[4])
            d = _linha(r, hoje, padrao[chave])
            d["cliente"] = r[23] or "—"
            saida.append(d)
    return saida


def uma(pool, conta_id: int, apolice_id: int, *, hoje: date | None = None) -> dict | None:
    hoje = hoje or date.today()
    with pool.connection() as c:
        r = c.execute(
            f"select {_COLS}, coalesce(nullif(cl.nome,''), '') "
            "  from apolices a left join clientes cl on cl.id = a.cliente_id "
            " where a.conta_id = %s and a.id = %s", (conta_id, apolice_id)).fetchone()
        if r is None:
            return None
        d = _linha(r, hoje, pct_padrao(c, conta_id, r[3], r[4]))
    d["cliente"] = r[23] or "—"
    return d


_CAMPOS = ("cliente_id", "corretor_id", "seguradora", "ramo", "numero_proposta",
           "numero_apolice", "vigencia_inicio", "vigencia_fim", "situacao",
           "premio_centavos", "iof_centavos", "franquia_centavos", "comissao_pct",
           "comissao_centavos", "classe_bonus", "parcelas", "dia_vencimento",
           "bem", "condutor", "coberturas", "renovacao_de", "obs")


def salvar(pool, conta_id: int, dados: dict, apolice_id: int | None = None) -> int:
    """Grava a apólice e devolve o id. Levanta se faltar o que o alerta precisa.

    Seguradora e fim de vigência são obrigatórios porque sem eles a linha não
    serve pro que ela existe: uma apólice sem data de fim nunca entra em régua
    nenhuma, e ficaria pra sempre na carteira parecendo coberta.
    """
    seg = (dados.get("seguradora") or "").strip()
    if not seg:
        raise ValueError("seguradora é obrigatória")
    if not dados.get("vigencia_fim"):
        raise ValueError("o fim da vigência é obrigatório")
    ramo = (dados.get("ramo") or "auto").strip().lower()
    if ramo not in _RAMOS:
        raise ValueError(f"ramo desconhecido: {ramo!r}")
    sit = (dados.get("situacao") or "proposta").strip().lower()
    if sit not in _SITUACOES:
        raise ValueError(f"situação desconhecida: {sit!r}")
    d = {k: dados.get(k) for k in _CAMPOS}
    d["seguradora"], d["ramo"], d["situacao"] = seg, ramo, sit
    # NULL EXPLÍCITO ANULA O DEFAULT DA COLUNA. O insert lista todas as colunas,
    # então "não passei o campo" chega no banco como `null` — e `premio_centavos`,
    # `iof_centavos` e `franquia_centavos` são not-null com default 0. Sem esta
    # linha, cadastrar uma apólice sem franquia (que é o normal) levanta
    # NotNullViolation em vez de gravar zero.
    for k in ("premio_centavos", "iof_centavos", "franquia_centavos"):
        d[k] = int(d[k] or 0)
    import json as _json
    for k, vazio in (("bem", {}), ("condutor", {}), ("coberturas", [])):
        d[k] = _json.dumps(d[k] if d[k] is not None else vazio)
    with pool.connection() as c:
        with c.transaction():
            if apolice_id:
                sets = ", ".join(f"{k} = %s" for k in _CAMPOS)
                c.execute(f"update apolices set {sets}, atualizado_em = now() "
                          "where id = %s and conta_id = %s",
                          tuple(d[k] for k in _CAMPOS) + (apolice_id, conta_id))
                return apolice_id
            cols = ", ".join(_CAMPOS)
            marks = ", ".join(["%s"] * len(_CAMPOS))
            r = c.execute(f"insert into apolices (conta_id, {cols}) "
                          f"values (%s, {marks}) returning id",
                          (conta_id,) + tuple(d[k] for k in _CAMPOS)).fetchone()
            return int(r[0])


def marcar_vencidas(pool, hoje: date | None = None) -> int:
    """Passa pra 'vencida' o que já passou da data e ninguém tocou.

    É a única escrita automática deste módulo, e ela só muda uma linha que o tempo
    já mudou: a vigência acabou, e dizer "vigente" seria a tela mentindo. Não
    apaga nada, não mexe em cliente, não desfaz renovação — quem renovou já saiu
    de `VIVAS` antes de chegar aqui.
    """
    hoje = hoje or date.today()
    with pool.connection() as c:
        with c.transaction():
            cur = c.execute(
                "update apolices set situacao = 'vencida', atualizado_em = now() "
                " where situacao = any(%s) and vigencia_fim < %s", (list(VIVAS), hoje))
            return cur.rowcount or 0
