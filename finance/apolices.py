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
    # o cliente NÃO renovou comigo, e disse por quê (migração 287). É outro estado
    # que 'vencida' (o tempo passou e ninguém marcou) e que 'cancelada' (encerrou
    # no meio): é o que o Raio-X conta como renovação perdida.
    ("perdida", "Perdida"),
)
#: os chips que a janela oferece — o que a pessoa DECIDE. 'vencida' não está aqui
#: porque não é decisão de ninguém: é o calendário, e `marcar_vencidas` grava sozinho.
DECISOES = ("proposta", "vigente", "renovada", "perdida")
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
           a.dia_vencimento, a.bem, a.condutor, a.coberturas, a.renovacao_de, a.obs,
           a.pdf_caminho, a.pdf_nome, a.perda_motivo, a.perdida_em"""


def _linha(r, hoje: date, pct_fallback=None) -> dict:
    (i, cli, cor, seg, ramo, n_prop, n_apol, v_ini, v_fim, sit, premio, iof, franquia,
     pct, com, bonus, parcelas, dia_venc, bem, condutor, coberturas, renov, obs,
     pdf_caminho, pdf_nome, perda_motivo, perdida_em) = r[:27]
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
        "pdf_caminho": pdf_caminho, "pdf_nome": pdf_nome, "tem_pdf": bool(pdf_caminho),
        "perda_motivo": perda_motivo, "perdida_em": perdida_em,
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
           " where a.conta_id = %s and a.excluida_em is null and a.situacao = any(%s) "
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
            d["cliente"] = r[27] or "—"
            saida.append(d)
    return saida


def proxima(pool, conta_id: int, *, hoje: date | None = None,
            corretor_id: int | None = None) -> dict | None:
    """A apólice viva mais próxima de vencer que ainda NÃO entrou no horizonte.

    Existe pro vazio da tela informar em vez de mentir. "Nenhuma apólice vencendo
    nos próximos 90 dias" é verdade e é inútil: a corretora tem uma carteira, e a
    tela dizendo "não há nada" convida a cadastrar de novo o que já está lá. Com
    isto ela diz QUAL é a próxima e QUANDO entra na régua.
    """
    hoje = hoje or date.today()
    sql = (f"select {_COLS}, coalesce(nullif(cl.nome,''), '') "
           "  from apolices a left join clientes cl on cl.id = a.cliente_id "
           " where a.conta_id = %s and a.excluida_em is null and a.situacao = any(%s) and a.vigencia_fim > %s")
    args: list = [conta_id, list(VIVAS), hoje + timedelta(days=HORIZONTE)]
    if corretor_id is not None:
        sql += " and a.corretor_id = %s"
        args.append(corretor_id)
    sql += " order by a.vigencia_fim limit 1"
    with pool.connection() as c:
        r = c.execute(sql, tuple(args)).fetchone()
        if r is None:
            return None
        d = _linha(r, hoje, pct_padrao(c, conta_id, r[3], r[4]))
    d["cliente"] = r[27] or "—"
    # quando ela entra na régua (o primeiro degrau) — é a pergunta seguinte de quem
    # lê "faltam 308 dias", e responder aqui evita a conta de cabeça
    d["entra_em"] = d["vigencia_fim"] - timedelta(days=DEGRAUS[0])
    return d


def listar(pool, conta_id: int, *, hoje: date | None = None,
           busca: str | None = None) -> list[dict]:
    """A carteira inteira, viva e morta, pra tela de cadastro.

    `busca` casa nome do cliente, seguradora, número (proposta ou apólice) e o que
    estiver no `bem` — placa e modelo, que é como a corretora procura um carro. Sem
    ela, uma carteira de trezentas linhas é uma tabela que ninguém lê.
    """
    hoje = hoje or date.today()
    termo = (busca or "").strip()
    sql = (f"select {_COLS}, coalesce(nullif(cl.nome,''), '') "
           "  from apolices a left join clientes cl on cl.id = a.cliente_id "
           " where a.conta_id = %s and a.excluida_em is null")
    args: list = [conta_id]
    if termo:
        sql += ("   and (coalesce(cl.nome,'') ilike %s or a.seguradora ilike %s "
                "     or coalesce(a.numero_proposta,'') ilike %s "
                "     or coalesce(a.numero_apolice,'') ilike %s "
                "     or a.bem::text ilike %s)")
        args += ["%" + termo + "%"] * 5
    sql += " order by a.vigencia_fim desc, a.id desc"
    with pool.connection() as c:
        rows = c.execute(sql, tuple(args)).fetchall()
        padrao = {}
        saida = []
        for r in rows:
            chave = ((r[3] or "").lower(), (r[4] or "").lower())
            if chave not in padrao:
                padrao[chave] = pct_padrao(c, conta_id, r[3], r[4])
            d = _linha(r, hoje, padrao[chave])
            d["cliente"] = r[27] or "—"
            saida.append(d)
    return saida


def excluir(pool, conta_id: int, apolice_id: int, membro_id=None) -> bool:
    """Tira uma apólice da carteira SEM apagar o registro dela.

    NÃO É `delete` (regra 0): informação de cliente não se perde. Uma apólice
    cadastrada errado precisa sair da carteira; o registro de que ela existiu, do
    PDF que a originou e de quem a criou, não precisa sumir junto — e desfazer um
    `delete` é restaurar backup, enquanto desfazer isto é um update.

    NÃO SE CONFUNDE COM `perdida`, que é desfecho de NEGÓCIO (o cliente renovou
    com outra corretora) e é o que a tela de perda mede. Esta é erro de cadastro.
    """
    with pool.connection() as c:
        r = c.execute("""update apolices set excluida_em = now(), excluida_por = %s
                          where conta_id = %s and id = %s and excluida_em is null
                          returning id""", (membro_id, conta_id, apolice_id)).fetchone()
        c.commit()
    return bool(r)


def voltar_apolice(pool, conta_id: int, apolice_id: int) -> bool:
    """Desfaz a exclusão. Excluir é um toque e errar também."""
    with pool.connection() as c:
        r = c.execute("""update apolices set excluida_em = null, excluida_por = null
                          where conta_id = %s and id = %s returning id""",
                      (conta_id, apolice_id)).fetchone()
        c.commit()
    return bool(r)


def total_da_carteira(pool, conta_id: int) -> int:
    """Quantas apólices a conta tem, sem trazer nenhuma.

    A aba mostra o número mesmo quando a busca filtrou a lista — senão "Carteira (1)"
    com um filtro ligado faria a corretora achar que perdeu as outras 299.
    """
    with pool.connection() as c:
        r = c.execute("select count(*) from apolices "
                      " where conta_id=%s and excluida_em is null", (conta_id,)).fetchone()
    return int(r[0]) if r else 0


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
    d["cliente"] = r[27] or "—"
    return d


_CAMPOS = ("cliente_id", "corretor_id", "seguradora", "ramo", "numero_proposta",
           "numero_apolice", "vigencia_inicio", "vigencia_fim", "situacao",
           "premio_centavos", "iof_centavos", "franquia_centavos", "comissao_pct",
           "comissao_centavos", "classe_bonus", "parcelas", "dia_vencimento",
           "bem", "condutor", "coberturas", "renovacao_de", "obs",
           # o PDF guardado junto (migração 286) e o que o leitor leu dele
           "pdf_caminho", "pdf_nome", "pdf_bytes", "pdf_lido", "pdf_lido_em")


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
    # `pdf_lido` é jsonb mas NULO quando não veio de PDF — sem forçar '{}': a
    # coluna vazia é a resposta honesta de "esta foi digitada à mão"
    if d.get("pdf_lido") is not None and not isinstance(d["pdf_lido"], str):
        d["pdf_lido"] = _json.dumps(d["pdf_lido"])
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


def perder(pool, conta_id: int, apolice_id: int, *, motivo: str, descricao: str = "",
           motivos_validos=None) -> None:
    """O cliente não renovou comigo. Grava a situação E o porquê, juntos.

    O motivo é obrigatório de propósito — é o que faz "perdida" valer alguma coisa
    no Raio-X. `motivos_validos` é a lista da conta (`funil_perda.motivos`): um
    motivo fora dela é digitação, não decisão, e é recusado antes do banco.
    """
    m = (motivo or "").strip()
    if not m:
        raise ValueError("motivo é obrigatório pra marcar como perdida")
    if motivos_validos is not None:
        por_chave = {x["chave"]: x for x in motivos_validos}
        if m not in por_chave:
            raise ValueError("motivo desconhecido")
        if por_chave[m].get("exige_descricao") and not (descricao or "").strip():
            raise ValueError("descricao_obrigatoria")
    with pool.connection() as c:
        with c.transaction():
            c.execute(
                """update apolices set situacao = 'perdida', perda_motivo = %s,
                          perda_descricao = %s, perdida_em = now(), atualizado_em = now()
                    where id = %s and conta_id = %s""",
                (m, (descricao or "").strip() or None, apolice_id, conta_id))


def _digitos(txt) -> str:
    return "".join(ch for ch in str(txt or "") if ch.isdigit())


def ficha_do_cliente(pool, conta_id: int, cliente_id: int, *, hoje: date | None = None,
                     n_msgs: int = 5) -> dict | None:
    """Tudo que a JANELA do segurado mostra, num JSON só.

    Medido antes de escrito (docs/mockups/ficha_do_segurado.html): o que um
    segurado TEM nesta base é identidade e contato (`clientes` + `pessoas`), as
    apólices (`apolices.cliente_id`) e a conversa pelo telefone (`conversas`).
    Compras, fiado e ticket ficam de fora — são de loja, e aqui seriam sempre zero.

    A CONVERSA CASA PELOS ÚLTIMOS 11 DÍGITOS. `contato_ref` guarda o número com o
    55 na frente (10 a 13 dígitos); `clientes.telefone` costuma vir sem. Medido em
    18/09 na conta 37: 39 de 39 leads casam por 11 dígitos. Oito dígitos casariam
    também — e casariam gente errada.
    """
    hoje = hoje or date.today()
    with pool.connection() as c:
        cl = c.execute(
            """select c.id, c.nome, c.telefone, c.email, c.endereco, c.cidade, c.uf, c.cep,
                      c.obs, c.criado_em, p.cpf, p.cnpj, coalesce(p.tipo, case when p.cnpj is not null then 'pj' else 'pf' end)
                 from clientes c left join pessoas p on p.id = c.pessoa_id
                where c.id = %s and c.dono_id = %s and c.ativo""", (cliente_id, conta_id)).fetchone()
        if cl is None:
            return None
        (cid, nome, tel, email, end_, cid_, uf, cep, obs, criado_em, cpf, cnpj, tipo) = cl
        rows = c.execute(
            f"select {_COLS}, '' from apolices a where a.conta_id = %s and a.excluida_em is null and a.cliente_id = %s "
            " order by a.vigencia_fim desc, a.id desc", (conta_id, cliente_id)).fetchall()
        padrao = {}
        apolices = []
        for r in rows:
            chave = ((r[3] or "").lower(), (r[4] or "").lower())
            if chave not in padrao:
                padrao[chave] = pct_padrao(c, conta_id, r[3], r[4])
            apolices.append(_linha(r, hoje, padrao[chave]))
        conversa, conversa_id = [], None
        d = _digitos(tel)
        if len(d) >= 10:
            n = 11 if len(d) >= 11 else 10
            cv = c.execute(
                "select id from conversas where conta_id = %s and right(contato_ref, %s) = right(%s, %s) "
                " order by ultima_msg_em desc nulls last, id desc limit 1",
                (conta_id, n, d, n)).fetchone()
            if cv:
                conversa_id = int(cv[0])
                msgs = c.execute(
                    "select direcao, texto, criado_em from mensagens where conversa_id = %s "
                    "   and coalesce(texto,'') <> '' order by criado_em desc limit %s",
                    (conversa_id, n_msgs)).fetchall()
                conversa = [{"de": ("ele" if dr == "in" else "você"), "texto": tx, "quando": q}
                            for (dr, tx, q) in reversed(msgs)]
    from finance import validadoc
    doc = cnpj or cpf
    vivas = [a for a in apolices if a["situacao"] in VIVAS]
    proxima = min((a for a in vivas if a["dias"] is not None and a["dias"] >= 0),
                  key=lambda a: a["dias"], default=None)
    linha_do_tempo = []
    for a in apolices:
        if a["vigencia_inicio"]:
            linha_do_tempo.append((a["vigencia_inicio"], f"Início da vigência {a['seguradora']} {a['ramo_txt'].lower()}"))
        if a["perdida_em"]:
            linha_do_tempo.append((a["perdida_em"].date(), f"Renovação perdida — {a['seguradora']}"
                                   + (f" ({rotulo_motivo_perda(a['perda_motivo'])})" if a["perda_motivo"] else "")))
    if criado_em:
        linha_do_tempo.append((criado_em.date(), "Cliente entrou na carteira"
                               + (" pela importação da apólice" if "apólice" in (obs or "").lower() else "")))
    linha_do_tempo.sort(key=lambda x: x[0], reverse=True)
    return {
        "id": cid, "nome": nome, "tipo": tipo, "documento": validadoc.formatar(doc) if doc else None,
        "telefone": tel, "email": email, "endereco": end_, "cidade": cid_, "uf": uf, "cep": cep,
        "obs": obs, "desde": criado_em.date() if criado_em else None,
        "apolices": apolices, "n_vivas": len(vivas), "proxima": proxima,
        "premio_ano_centavos": sum(a["premio_centavos"] for a in vivas),
        "conversa": conversa, "conversa_id": conversa_id,
        "linha_do_tempo": linha_do_tempo,
    }


def rotulo_motivo_perda(chave: str | None) -> str:
    from finance import raio_x_perfil as _rxp
    return _rxp.rotulo_motivo(chave)


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


# ─────────────────────── OS PDFs QUE JÁ CHEGARAM NO WHATSAPP ───────────────────────
#
# A corretora não precisa mudar de hábito: o documento JÁ chega no número
# vinculado. Medido na Liberal (conta 37) em 18/09/2026, nos últimos 90 dias,
# 36 PDFs — entre eles "PROPOSTA DENISE BARROS DE SOUSA SANTOS.pdf" e
# "Orçamento MARIA IRACI CORREIA DE SANTANA.pdf", mandados pelo próprio corretor.
#
# POR QUE NÃO CADASTRAR SOZINHO. Na mesma medição, a maioria dos PDFs não era
# apólice: uma dúzia de boletos, extrato bancário do mês, um agravo de
# instrumento. Ler tudo e gravar viraria lixo na carteira — e vigência lida
# errada é alerta que não dispara, o pior defeito que esta tela pode ter. Por
# isso aqui só se LISTA; quem escolhe é a pessoa, e a conferência é a mesma do
# PDF solto.
#
# O ARQUIVO NÃO ESTÁ AQUI, só o ponteiro (`mensagens.midia_ref`, migração 187):
# quem busca no CDN é `finance.wa_midia`, e o CDN expira. Escolher um PDF é,
# de quebra, o que faz ele virar cópia nossa no cofre da apólice.

_PISTAS_APOLICE = ("apolice", "apólice", "proposta", "orcamento", "orçamento",
                   "endosso", "renovacao", "renovação", "seguro", "certificado")


def _parece_apolice(nome: str) -> bool:
    return any(p in (nome or "").lower() for p in _PISTAS_APOLICE)


def pdfs_do_whatsapp(pool, conta_id: int, *, dias: int = 90,
                     limite: int = 40) -> list[dict]:
    """Os PDFs recebidos no WhatsApp vinculado, do mais novo pro mais velho.

    `ja_cadastrada` olha a marca que o cadastro deixa em `apolices.pdf_lido`
    (`origem.whatsapp_msg`): sem ela a mesma proposta apareceria pra sempre na
    lista, e cadastrar duas vezes seria o caminho mais fácil.

    Só entrada (`direcao='in'`): o que a corretora MANDOU pelo painel não volta
    pra lista como se fosse documento novo.

    E SÓ DE QUEM FOI LIBERADO (migração 289, pedido do dono em 19/09/2026:
    "limita a lista só pros numeros de corretor e seguradora"). Prender a lista ao
    número da empresa já tirava o WhatsApp pessoal de todo mundo; isto tira o
    fornecedor de boleto, que escreve pro MESMO número. Sem ninguém liberado a
    lista vem vazia de propósito — a tela então mostra quem anda mandando, pra
    liberar num toque.
    """
    sql = """
        select m.id,
               m.criado_em,
               coalesce(nullif(cv.contato_nome, ''), cv.contato_ref, '') as de,
               coalesce(m.midia_meta->>'nome', '') as nome,
               coalesce((m.midia_meta->>'bytes')::bigint, 0) as bytes,
               exists (select 1 from apolices a
                        where a.conta_id = %s
                          and a.pdf_lido->'origem'->>'whatsapp_msg' = m.id::text),
               -- o que o leitor automático já tirou do documento (migração 304):
               -- é o que faz a lista dizer "Allianz · Maria · vence 23/07/2027" em
               -- vez de repetir o nome do arquivo
               l.seguradora, l.segurado, l.vigencia_fim, l.erro, (l.id is not null)
          from mensagens m
          join conversas cv on cv.id = m.conversa_id
          left join apolice_lida l on l.mensagem_id = m.id and l.conta_id = cv.conta_id
         where cv.conta_id = %s
           and m.direcao = 'in'
           and m.midia_tipo = 'documento'
           and m.midia_ref is not null
           and (coalesce(m.midia_ref->>'mimetype', '') like 'application/pdf%%'
                or lower(coalesce(m.midia_meta->>'nome', '')) like '%%.pdf')
           and m.criado_em > now() - make_interval(days => %s)
           and exists (select 1 from apolice_remetentes r
                        where r.conta_id = cv.conta_id
                          and r.contato_ref = cv.contato_ref)
         order by m.criado_em desc
         limit %s
    """
    with pool.connection() as c:
        rows = c.execute(sql, (conta_id, conta_id, int(dias), int(limite))).fetchall()
    return [{"mensagem_id": r[0], "quando": r[1], "de": r[2] or "—",
             "nome": r[3] or "documento.pdf", "bytes": int(r[4] or 0),
             "parece_apolice": _parece_apolice(r[3] or ""),
             "ja_cadastrada": bool(r[5]),
             # o pré-cadastro, quando o leitor já passou por este documento
             "lida": bool(r[10]), "seguradora": r[6], "segurado": r[7],
             "vigencia_fim": r[8], "erro_leitura": r[9] or ""}
            for r in rows]


def ref_do_pdf(pool, conta_id: int, mensagem_id: int) -> dict | None:
    """O ponteiro de UM PDF, preso à conta.

    O id da mensagem é sequencial e adivinhável — o mesmo cuidado da mídia do
    funil. Sem o casamento `mensagens -> conversas -> conta_id`, trocar um número
    na URL leria o documento do cliente de outra corretora.
    """
    with pool.connection() as c:
        r = c.execute(
            """select m.midia_ref, m.midia_tipo, coalesce(m.midia_meta->>'nome', '')
                 from mensagens m
                 join conversas cv on cv.id = m.conversa_id
                where m.id = %s and cv.conta_id = %s and m.midia_ref is not null
                  and m.midia_tipo = 'documento'""",
            (mensagem_id, conta_id)).fetchone()
    if not r:
        return None
    return {"ref": r[0] or {}, "tipo": r[1] or "documento", "nome": r[2] or "documento.pdf"}


TIPOS_REMETENTE = (("corretor", "Corretor"), ("seguradora", "Seguradora"))


def remetentes(pool, conta_id: int) -> list[dict]:
    """Quem está liberado pra mandar apólice, do mais novo pro mais velho."""
    with pool.connection() as c:
        rows = c.execute(
            "select contato_ref, rotulo, tipo, criado_em from apolice_remetentes "
            " where conta_id = %s order by criado_em desc", (conta_id,)).fetchall()
    return [{"contato_ref": r[0], "rotulo": r[1] or r[0], "tipo": r[2],
             "tipo_txt": dict(TIPOS_REMETENTE).get(r[2], r[2]), "criado_em": r[3]}
            for r in rows]


def liberar_remetente(pool, conta_id: int, contato_ref: str, *, rotulo: str = "",
                      tipo: str = "corretor", membro_id: int | None = None) -> bool:
    """Libera um número. Liberar de novo só atualiza o rótulo e o tipo.

    O número tem que ter CONVERSA nesta conta: é o mesmo cerco da lista, e sem
    ele daria pra liberar um número qualquer digitando o id na requisição.
    """
    ref = "".join(ch for ch in (contato_ref or "") if ch.isdigit())
    if not ref:
        raise ValueError("número vazio")
    if tipo not in dict(TIPOS_REMETENTE):
        raise ValueError("tipo desconhecido")
    with pool.connection() as c:
        existe = c.execute(
            "select 1 from conversas where conta_id = %s and contato_ref = %s limit 1",
            (conta_id, ref)).fetchone()
        if not existe:
            raise ValueError("este número não tem conversa nesta conta")
        c.execute(
            """insert into apolice_remetentes (conta_id, contato_ref, rotulo, tipo, criado_por)
                    values (%s, %s, %s, %s, %s)
               on conflict (conta_id, contato_ref)
                    do update set rotulo = excluded.rotulo, tipo = excluded.tipo""",
            (conta_id, ref, (rotulo or "")[:120], tipo, membro_id))
        c.commit()
    return True


def tirar_remetente(pool, conta_id: int, contato_ref: str) -> None:
    """Tira da lista. Não apaga apólice nenhuma — só para de sugerir PDF dele."""
    with pool.connection() as c:
        c.execute("delete from apolice_remetentes where conta_id = %s and contato_ref = %s",
                  (conta_id, "".join(ch for ch in (contato_ref or "") if ch.isdigit())))
        c.commit()


def esperando_conferencia(pool, conta_id: int, *, dias: int = 30) -> int:
    """Quantos documentos o leitor já leu e ninguém conferiu ainda.

    É o número da faixa no topo de Renovações. Conta só o que dá pra CONFERIR:
    leitura sem erro, com o PDF guardado, e que ainda não virou apólice — o mesmo
    recorte de `apolice_leitor.sem_mensagem`, que é a lista pra onde a faixa leva.
    Contar diferente da lista faria a faixa prometer o que a janela não mostra.

    Nasceu em 21/09/2026: o WhatsApp avisava "está esperando você conferir em
    Renovações" e a tela não dizia nada — a lista mora dentro da janela de
    cadastrar, e quem chega pela mensagem não abre uma janela de CRIAR.
    """
    with pool.connection() as c:
        r = c.execute(
            """select count(*) from apolice_lida l
                where l.conta_id = %s and l.mensagem_id is null
                  and l.descartado_em is null
                  and coalesce(l.erro,'') = '' and l.pdf_caminho is not null
                  and l.criado_em > now() - make_interval(days => %s)
                  and not exists (select 1 from apolices a
                                   where a.conta_id = l.conta_id
                                     and a.pdf_caminho is not null
                                     and a.pdf_caminho = l.pdf_caminho)""",
            (conta_id, int(dias))).fetchone()
    return int(r[0] or 0)


def quem_mandou_pdf(pool, conta_id: int, *, dias: int = 90,
                    limite: int = 30) -> list[dict]:
    """Quem andou mandando PDF pro número da empresa, com quantos e quando.

    É o que faz a liberação ser um toque em vez de digitar telefone: a pessoa
    reconhece o nome, vê quantos documentos vieram daquele número, e libera.
    Mostra também quem JÁ está liberado, pra tela poder tirar.
    """
    sql = """
        select cv.contato_ref,
               coalesce(nullif(max(cv.contato_nome), ''), cv.contato_ref, '') as nome,
               count(*) as quantos,
               max(m.criado_em) as ultimo,
               exists (select 1 from apolice_remetentes r
                        where r.conta_id = cv.conta_id and r.contato_ref = cv.contato_ref) as liberado
          from mensagens m
          join conversas cv on cv.id = m.conversa_id
         where cv.conta_id = %s
           and m.direcao = 'in'
           and m.midia_tipo = 'documento'
           and m.midia_ref is not null
           and (coalesce(m.midia_ref->>'mimetype', '') like 'application/pdf%%'
                or lower(coalesce(m.midia_meta->>'nome', '')) like '%%.pdf')
           and m.criado_em > now() - make_interval(days => %s)
           and cv.contato_ref is not null
         group by cv.conta_id, cv.contato_ref
         order by liberado desc, quantos desc, ultimo desc
         limit %s
    """
    with pool.connection() as c:
        rows = c.execute(sql, (conta_id, int(dias), int(limite))).fetchall()
    return [{"contato_ref": r[0], "nome": r[1] or "—", "quantos": int(r[2]),
             "ultimo": r[3], "liberado": bool(r[4])} for r in rows]
