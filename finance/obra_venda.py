"""A venda da casa e os papéis dela: o CAMINHO DO DINHEIRO.

PR 3 de 4 do desenho aprovado pelo dono em 25/09/2026
(docs/mockups/nicho_construcao.html, seções 06 e 10). A primeira conta é a PX2
(conta 33), que constrói casa popular pra vender pelo Minha Casa Minha Vida.

Na casa pronta vendida com financiamento da Caixa, o dinheiro — financiamento,
FGTS e subsídio — só cai pra construtora DEPOIS que o contrato é registrado no
cartório. Antes disso a casa precisa de habite-se, CND da obra (aferição no SERO;
a Lei 8.212, art. 47, exige a CND pra averbar) e averbação na matrícula. Da
documentação ao crédito o mercado fala em 60 a 90 dias, e a greve da Caixa de
setembro/2026 esticou isso. Até aqui nada disso era guardado.

AS QUATRO ESCOLHAS:

1. **O caminho é uma lista fixa de passos, na ordem em que o dinheiro anda**: obra
   pronta → habite-se → CND da obra → averbação → comprador aprovado → avaliação
   da Caixa → assinatura → registro → crédito. O primeiro que falta é o que TRAVA
   a casa, e é ele que a tela acende e o agente lembra. Venda à vista pula a
   aprovação e a avaliação.

2. **Quem paga o repasse é a CAIXA, não o comprador.** O título do repasse nasce
   com a Caixa na contraparte e sem cliente; só o da entrada fica na ficha do
   comprador — senão a ficha dele diria que ele deve R$ 145 mil que quem vai pagar
   é o banco. É a mesma lição da corretora, onde quem paga é a seguradora.

3. **Os títulos nascem na ASSINATURA, não antes.** Antes disso a venda ainda cai
   (crédito, avaliação), e título a receber de venda que caiu é cobrança errada. A
   entrada vence na assinatura; o repasse, 30 dias depois (o prazo de registro).
   Os dois vão no centro de custo da obra e na conta 1.1.04, então o dinheiro que
   cai aparece como "recebido" na ficha da casa (finance/obras.py).

4. **Alerta se calcula na leitura, nunca se grava**: CNO (30 dias do início da obra,
   IN RFB 2.061/2021), alvará vencendo, certidões da empresa (180 dias), avaliação
   da Caixa (12 meses), registro atrasado (30 dias da assinatura), avaliação abaixo
   do preço e casa pronta parada esperando papel. Prazo e regra ficam aqui, e
   imposto nenhum: o que se lembra é que o papel existe, não quanto se paga.

Multi-tenant sagrado: toda consulta é escopada por `conta_id`.
"""
from __future__ import annotations

from datetime import date, timedelta

from . import obras as _ob

# ─────────────────────────────────────────────────────────────── os papéis
DOCUMENTOS = (
    ("alvara", "Alvará de construção"),
    ("art_rrt", "ART/RRT de execução"),
    ("cno", "CNO (Receita Federal)"),
    ("habite_se", "Habite-se"),
    ("cnd_obra", "CND da obra (SERO)"),
    ("averbacao", "Averbação na matrícula"),
    ("matricula", "Matrícula individualizada"),
    ("certidoes", "Certidões da empresa"),
)
ROTULO_DOC = dict(DOCUMENTOS)
STATUS_DOC = {"pendente": "Pendente", "ok": "Em dia", "nao_se_aplica": "Não se aplica"}
#: como se fala no canteiro -> o tipo. Casa por palavra, sem acento e sem caixa.
_SINONIMOS_DOC = {
    "alvara": "alvara", "licenca": "alvara",
    "art": "art_rrt", "rrt": "art_rrt",
    "cno": "cno",
    "habite": "habite_se", "habitese": "habite_se",
    "cnd": "cnd_obra", "sero": "cnd_obra", "afericao": "cnd_obra", "inss": "cnd_obra",
    "averbacao": "averbacao", "averbou": "averbacao", "averbar": "averbacao",
    "averbada": "averbacao",
    "matricula": "matricula", "individualizacao": "matricula",
    "certidao": "certidoes", "certidoes": "certidoes", "junta": "certidoes",
}

PRAZO_CNO_DIAS = 30
PRAZO_REGISTRO_DIAS = 30
VALIDADE_CERTIDOES_DIAS = 180
VALIDADE_AVALIACAO_DIAS = 365

# ─────────────────────────────────────────────────────────────── a venda
SITUACOES = ("documentacao", "analise", "aprovado", "avaliacao", "assinatura",
             "registro", "creditado", "desistiu")
ROTULO_SITUACAO = {
    "documentacao": "Juntando documentos", "analise": "Crédito em análise",
    "aprovado": "Crédito aprovado", "avaliacao": "Avaliação da Caixa feita",
    "assinatura": "Contrato assinado", "registro": "Registrado no cartório",
    "creditado": "Dinheiro na conta", "desistiu": "Venda desfeita",
}
_DATA_DO_PASSO = {"aprovado": "aprovado_em", "avaliacao": "avaliacao_em",
                  "assinatura": "assinatura_em", "registro": "registro_em",
                  "creditado": "creditado_em"}
_SINONIMOS_PASSO = {
    "documentacao": "documentacao", "documentos": "documentacao",
    "analise": "analise",
    "aprovou": "aprovado", "aprovado": "aprovado", "aprovada": "aprovado",
    "avaliacao": "avaliacao", "avaliou": "avaliacao", "vistoria": "avaliacao",
    "assinou": "assinatura", "assinatura": "assinatura", "assinaram": "assinatura",
    "contrato": "assinatura",
    "registrou": "registro", "registro": "registro", "cartorio": "registro",
    "registrado": "registro",
    "caiu": "creditado", "creditado": "creditado", "creditou": "creditado",
    "dinheiro": "creditado", "repasse": "creditado",
    "desistiu": "desistiu", "desistencia": "desistiu", "desfez": "desistiu",
}
MODALIDADES = {"financiada": "Financiada pela Caixa", "avista": "À vista",
               "outro": "Outra"}

_COLS_VENDA = ("comprador", "telefone", "cliente_id", "faixa", "modalidade",
               "valor_venda_centavos", "valor_avaliacao_centavos",
               "financiamento_centavos", "subsidio_centavos", "fgts_centavos",
               "entrada_centavos", "situacao", "aprovado_em", "avaliacao_em",
               "assinatura_em", "registro_em", "creditado_em", "titulo_entrada_id",
               "titulo_repasse_id", "obs")
_EDITAVEIS = ("comprador", "telefone", "faixa", "modalidade", "valor_venda_centavos",
              "valor_avaliacao_centavos", "financiamento_centavos", "subsidio_centavos",
              "fgts_centavos", "entrada_centavos", "aprovado_em", "avaliacao_em",
              "assinatura_em", "registro_em", "creditado_em", "obs")


def achar_documento(ref: str | None) -> str | None:
    alvo = _ob._norm(ref).replace("-", " ").replace("_", " ")
    if not alvo:
        return None
    for tipo, nome in DOCUMENTOS:
        if alvo in (tipo.replace("_", " "), _ob._norm(nome)):
            return tipo
    for palavra in alvo.split():
        if palavra in _SINONIMOS_DOC:
            return _SINONIMOS_DOC[palavra]
    achou = [t for t, n in DOCUMENTOS if alvo in _ob._norm(n)]
    return achou[0] if len(achou) == 1 else None


def achar_passo(ref: str | None) -> str | None:
    alvo = _ob._norm(ref)
    if alvo in SITUACOES:
        return alvo
    for palavra in alvo.replace("_", " ").split():
        if palavra in _SINONIMOS_PASSO:
            return _SINONIMOS_PASSO[palavra]
    return None


def _obra_da_conta(c, conta_id: int, obra_id: int):
    return c.execute("select id, tipo, nome, centro_custo_id, status from obras "
                     "where id=%s and conta_id=%s", (obra_id, conta_id)).fetchone()


# ─────────────────────────────────────────────────────────────── documentos
def documentos(pool, conta_id: int, obra_id: int) -> list[dict]:
    """Os oito papéis da casa, na ordem do caminho. O que nunca foi preenchido
    aparece PENDENTE — ausência de linha não é 'em dia'."""
    with pool.connection() as c:
        rows = c.execute(
            """select tipo, status, numero, emitido_em, vence_em, obs from obra_documentos
                where conta_id=%s and obra_id=%s""", (conta_id, obra_id)).fetchall()
    guardado = {r[0]: r for r in rows}
    out = []
    for tipo, nome in DOCUMENTOS:
        r = guardado.get(tipo)
        out.append({"tipo": tipo, "nome": nome,
                    "status": r[1] if r else "pendente", "numero": r[2] if r else "",
                    "emitido_em": r[3] if r else None, "vence_em": r[4] if r else None,
                    "obs": r[5] if r else ""})
    return out


def marcar_documento(pool, conta_id: int, obra_id: int, tipo: str, *,
                     status: str = "ok", numero: str | None = None,
                     emitido_em: date | None = None, vence_em: date | None = None,
                     obs: str | None = None, substituir: bool = False) -> dict:
    """Grava a situação de um papel. Pelo WhatsApp, campo que não veio fica como
    estava; pela tela (`substituir`), vale o formulário inteiro — é assim que se
    apaga uma validade digitada errado. Papel em dia sem data de emissão ganha a
    de hoje ("saiu o habite-se")."""
    if tipo not in ROTULO_DOC:
        raise ValueError("Documento desconhecido.")
    if status not in STATUS_DOC:
        raise ValueError("Situação de documento desconhecida.")
    atual = {d["tipo"]: d for d in documentos(pool, conta_id, obra_id)}[tipo]
    if substituir:
        novo = {"status": status, "numero": (numero or "").strip(), "emitido_em": emitido_em,
                "vence_em": vence_em, "obs": (obs or "").strip()}
    else:
        novo = {
            "status": status,
            "numero": atual["numero"] if numero is None else numero.strip(),
            "emitido_em": emitido_em or atual["emitido_em"],
            "vence_em": vence_em if vence_em is not None else atual["vence_em"],
            "obs": atual["obs"] if obs is None else obs.strip(),
        }
    if status == "ok" and not novo["emitido_em"]:
        novo["emitido_em"] = date.today()
    with pool.connection() as c:
        if not _obra_da_conta(c, conta_id, obra_id):
            raise ValueError("Obra não encontrada.")
        c.execute(
            """insert into obra_documentos (conta_id, obra_id, tipo, status, numero,
                                             emitido_em, vence_em, obs)
                    values (%s,%s,%s,%s,%s,%s,%s,%s)
               on conflict (obra_id, tipo) do update
                     set status=excluded.status, numero=excluded.numero,
                         emitido_em=excluded.emitido_em, vence_em=excluded.vence_em,
                         obs=excluded.obs, atualizado_em=now()""",
            (conta_id, obra_id, tipo, novo["status"], novo["numero"], novo["emitido_em"],
             novo["vence_em"], novo["obs"]))
        c.commit()
    return dict(novo, tipo=tipo, nome=ROTULO_DOC[tipo])


# ─────────────────────────────────────────────────────────────── venda
def venda(pool, conta_id: int, obra_id: int) -> dict | None:
    with pool.connection() as c:
        r = c.execute(f"select {', '.join(_COLS_VENDA)} from obra_vendas "
                      "where conta_id=%s and obra_id=%s", (conta_id, obra_id)).fetchone()
    if not r:
        return None
    v = dict(zip(_COLS_VENDA, r))
    v["repasse_centavos"] = repasse_centavos(v)
    v["rotulo_situacao"] = ROTULO_SITUACAO[v["situacao"]]
    v["rotulo_modalidade"] = MODALIDADES[v["modalidade"]]
    return v


def repasse_centavos(v: dict) -> int:
    """O que a Caixa paga: financiamento + subsídio + FGTS."""
    return sum(int(v.get(k) or 0) for k in ("financiamento_centavos", "subsidio_centavos",
                                            "fgts_centavos"))


def entrada_calculada(v: dict) -> int | None:
    """entrada = venda − financiamento − subsídio − FGTS (nunca negativa)."""
    if not v.get("valor_venda_centavos"):
        return None
    return max(0, int(v["valor_venda_centavos"]) - repasse_centavos(v))


def _cliente(pool, conta_id: int, nome: str, telefone: str) -> int | None:
    """O comprador na base de clientes: acha pelo nome ou cadastra. Tolerante —
    sem a base de clientes, a venda segue sem o vínculo."""
    if not nome:
        return None
    try:
        from . import clientes as _cli
        return _cli.achar_cliente_por_nome(pool, conta_id, nome, papel="cliente") or \
            _cli.criar_cliente(pool, conta_id, nome, telefone=telefone or None)
    except Exception:  # noqa: BLE001
        return None


def salvar_venda(pool, conta_id: int, obra_id: int, **campos) -> dict:
    """Cria ou atualiza a venda da casa com os campos que vierem. Entrada vazia é
    calculada; comprador com nome vai pra base de clientes."""
    novos = {k: v for k, v in campos.items() if k in _EDITAVEIS}
    if "modalidade" in novos and novos["modalidade"] not in MODALIDADES:
        raise ValueError("Modalidade desconhecida.")
    if novos.get("faixa") is not None and novos["faixa"] not in (1, 2, 3, 4):
        raise ValueError("A faixa do Minha Casa Minha Vida vai de 1 a 4.")
    with pool.connection() as c:
        obra = _obra_da_conta(c, conta_id, obra_id)
    if not obra:
        raise ValueError("Obra não encontrada.")
    if obra[1] != "casa":
        raise ValueError("Venda é de casa; reforma tem contrato e etapas.")
    atual = venda(pool, conta_id, obra_id) or {}
    junto = {k: atual.get(k) for k in _EDITAVEIS}
    junto.update(novos)
    if "entrada_centavos" not in novos or novos["entrada_centavos"] is None:
        junto["entrada_centavos"] = entrada_calculada(junto)
    junto["comprador"] = " ".join((junto.get("comprador") or "").split())
    junto["telefone"] = (junto.get("telefone") or "").strip()
    junto["modalidade"] = junto.get("modalidade") or "financiada"
    junto["obs"] = (junto.get("obs") or "").strip()
    cliente_id = atual.get("cliente_id")
    if junto["comprador"] and junto["comprador"] != atual.get("comprador"):
        cliente_id = _cliente(pool, conta_id, junto["comprador"], junto["telefone"])
    cols = list(_EDITAVEIS) + ["cliente_id"]
    vals = [junto[k] for k in _EDITAVEIS] + [cliente_id]
    with pool.connection() as c:
        c.execute(
            f"""insert into obra_vendas (obra_id, conta_id, {', '.join(cols)})
                     values (%s, %s, {', '.join(['%s'] * len(cols))})
                on conflict (obra_id) do update
                      set {', '.join(f'{k}=excluded.{k}' for k in cols)}, atualizado_em=now()""",
            (obra_id, conta_id, *vals))
        c.commit()
    return venda(pool, conta_id, obra_id)


def _plano_venda_de_imovel(pool) -> int | None:
    try:
        with pool.connection() as c:
            r = c.execute("select id from plano_contas where codigo='1.1.04'").fetchone()
        return r[0] if r else None
    except Exception:  # noqa: BLE001 — base sem a 349
        return None


def _garantir_titulos(pool, conta_id: int, obra, v: dict, assinatura: date) -> list[str]:
    """Os dois títulos a receber da venda — ver a escolha 3. Idempotente pelos ids
    guardados na venda."""
    from . import empresa as _emp
    _oid, _tipo, nome, centro, _st = obra
    plano = _plano_venda_de_imovel(pool)
    feitos, ids = [], {}
    if not v.get("titulo_entrada_id") and (v.get("entrada_centavos") or 0) > 0:
        t = _emp.criar_titulo(
            pool, conta_id, "receber", f"Entrada da {nome} — {v['comprador'] or 'comprador'}",
            int(v["entrada_centavos"]), assinatura, contraparte=v["comprador"] or "",
            categoria="Vendas", cliente_id=v.get("cliente_id"), plano_conta_id=plano,
            centro_custo_id=centro)
        ids["titulo_entrada_id"] = t["id"]
        feitos.append(f"entrada {_ob._brl(v['entrada_centavos'])}")
    repasse = repasse_centavos(v)
    if not v.get("titulo_repasse_id") and repasse > 0:
        t = _emp.criar_titulo(
            pool, conta_id, "receber",
            f"Repasse da Caixa — {nome} (financiamento, subsídio e FGTS)", repasse,
            assinatura + timedelta(days=PRAZO_REGISTRO_DIAS), contraparte="Caixa",
            categoria="Vendas", plano_conta_id=plano, centro_custo_id=centro)
        ids["titulo_repasse_id"] = t["id"]
        feitos.append(f"repasse {_ob._brl(repasse)}")
    if ids:
        with pool.connection() as c:
            c.execute(f"update obra_vendas set {', '.join(f'{k}=%s' for k in ids)} "
                      "where obra_id=%s and conta_id=%s", (*ids.values(), obra[0], conta_id))
            c.commit()
    return feitos


def andar_venda(pool, conta_id: int, obra_id: int, situacao: str,
                quando: date | None = None) -> dict:
    """Leva a venda a um passo, com a data dele. Da assinatura em diante os títulos
    existem e a obra fica VENDIDA; venda desfeita devolve a casa pra pronta (ou em
    obra) sem apagar nada."""
    if situacao not in SITUACOES:
        raise ValueError("Passo da venda desconhecido.")
    v = venda(pool, conta_id, obra_id)
    if not v:
        raise ValueError("Cadastre a venda primeiro — comprador e valores — na ficha da obra.")
    quando = quando or date.today()
    with pool.connection() as c:
        obra = _obra_da_conta(c, conta_id, obra_id)
        sets, vals = ["situacao=%s"], [situacao]
        col = _DATA_DO_PASSO.get(situacao)
        if col:
            sets.append(f"{col}=coalesce({col}, %s)")
            vals.append(quando)
        if situacao in ("registro", "creditado"):
            # pulou a assinatura na conversa: ela aconteceu, e o título precisa da data
            sets.append("assinatura_em=coalesce(assinatura_em, %s)")
            vals.append(quando)
        c.execute(f"update obra_vendas set {', '.join(sets)}, atualizado_em=now() "
                  "where obra_id=%s and conta_id=%s", (*vals, obra_id, conta_id))
        if situacao in ("assinatura", "registro", "creditado") and obra[4] in ("em_obra", "pronta"):
            c.execute("update obras set status='vendida', atualizado_em=now() "
                      "where id=%s and conta_id=%s", (obra_id, conta_id))
        elif situacao == "desistiu" and obra[4] == "vendida":
            c.execute("""update obras set status=case when concluida_em is null
                                                  then 'em_obra' else 'pronta' end,
                                  atualizado_em=now()
                          where id=%s and conta_id=%s""", (obra_id, conta_id))
        c.commit()
    v = venda(pool, conta_id, obra_id)
    titulos = []
    if situacao in ("assinatura", "registro", "creditado"):
        titulos = _garantir_titulos(pool, conta_id, obra, v, v["assinatura_em"] or quando)
    return {"obra": obra[2], "situacao": situacao, "rotulo": ROTULO_SITUACAO[situacao],
            "titulos": titulos}


# ─────────────────────────────────────────────────────────────── o caminho
def _br(d: date | None) -> str:
    return d.strftime("%d/%m") if d else ""


def caminho(obra: dict, docs: list[dict], v: dict | None) -> list[dict]:
    """Os passos na ordem em que o dinheiro anda — ver a escolha 1."""
    feito_doc = {d["tipo"]: d["status"] in ("ok", "nao_se_aplica") for d in docs}
    data_doc = {d["tipo"]: d["emitido_em"] for d in docs}
    sit = v["situacao"] if v else None
    idx = SITUACOES.index(sit) if sit and sit != "desistiu" else -1

    def passou(s: str) -> bool:
        return idx >= SITUACOES.index(s)

    passos = [{"chave": "obra", "nome": "Obra pronta", "feito": obra["pct"] == 100,
               "detalhe": f"{obra['pct']}%"}]
    for tipo in ("habite_se", "cnd_obra", "averbacao"):
        passos.append({"chave": tipo, "nome": ROTULO_DOC[tipo].split(" (")[0],
                       "feito": feito_doc[tipo], "detalhe": _br(data_doc[tipo])})
    if not v or v["modalidade"] == "financiada":
        passos.append({"chave": "aprovado", "nome": "Comprador aprovado",
                       "feito": bool(v and (v["aprovado_em"] or passou("aprovado"))),
                       "detalhe": _br(v and v["aprovado_em"])})
        passos.append({"chave": "avaliacao", "nome": "Avaliação da Caixa",
                       "feito": bool(v and (v["avaliacao_em"] or passou("avaliacao"))),
                       "detalhe": _br(v and v["avaliacao_em"])})
    passos.append({"chave": "assinatura", "nome": "Assinatura", "feito": passou("assinatura"),
                   "detalhe": _br(v and v["assinatura_em"])})
    passos.append({"chave": "registro", "nome": "Registro", "feito": passou("registro"),
                   "detalhe": _br(v and v["registro_em"]) or f"até {PRAZO_REGISTRO_DIAS} dias"})
    passos.append({"chave": "creditado", "nome": "Crédito", "feito": passou("creditado"),
                   "detalhe": _ob._brl(v["repasse_centavos"]) if v and v["repasse_centavos"] else ""})
    trava = next((p for p in passos if not p["feito"]), None)
    for p in passos:
        p["trava"] = p is trava
    return passos


def alertas(obra: dict, docs: list[dict], v: dict | None, hoje: date | None = None) -> list[str]:
    """Os avisos de prazo da casa — ver a escolha 4. Cada um é uma frase pronta."""
    hoje = hoje or date.today()
    d = {doc["tipo"]: doc for doc in docs}
    out = []
    cno = d["cno"]
    if cno["status"] == "pendente" and obra.get("inicio_em"):
        prazo = obra["inicio_em"] + timedelta(days=PRAZO_CNO_DIAS)
        if hoje > prazo:
            out.append(f"CNO atrasado: o prazo era {_br(prazo)} (30 dias do início da obra).")
        elif (prazo - hoje).days <= 7:
            out.append(f"CNO vence em {(prazo - hoje).days} dia(s), em {_br(prazo)}.")
    al = d["alvara"]
    if al["vence_em"] and al["status"] != "nao_se_aplica":
        dias = (al["vence_em"] - hoje).days
        if dias < 0:
            out.append(f"Alvará vencido desde {_br(al['vence_em'])}.")
        elif dias <= 30:
            out.append(f"Alvará vence em {dias} dia(s).")
    ce = d["certidoes"]
    if ce["status"] == "ok" and ce["emitido_em"]:
        fim = ce["emitido_em"] + timedelta(days=VALIDADE_CERTIDOES_DIAS)
        if hoje > fim:
            out.append("Certidões da empresa vencidas (valem 180 dias).")
        elif (fim - hoje).days <= 15:
            out.append(f"Certidões da empresa vencem em {(fim - hoje).days} dia(s).")
    if v and v["situacao"] != "desistiu":
        if v["avaliacao_em"]:
            fim = v["avaliacao_em"] + timedelta(days=VALIDADE_AVALIACAO_DIAS)
            if hoje > fim:
                out.append("Avaliação da Caixa vencida (vale 12 meses).")
            elif (fim - hoje).days <= 30:
                out.append(f"Avaliação da Caixa vence em {(fim - hoje).days} dia(s).")
        aval, preco = v["valor_avaliacao_centavos"], v["valor_venda_centavos"]
        if aval and preco and aval < preco:
            out.append(f"Avaliação da Caixa ({_ob._brl(aval)}) abaixo do preço "
                       f"({_ob._brl(preco)}): o comprador cobre {_ob._brl(preco - aval)} "
                       "a mais de entrada.")
        if v["situacao"] == "assinatura" and v["assinatura_em"] and \
                (hoje - v["assinatura_em"]).days > PRAZO_REGISTRO_DIAS:
            out.append(f"Registro atrasado: o contrato foi assinado em "
                       f"{_br(v['assinatura_em'])} e o prazo de 30 dias passou.")
    if obra.get("status") == "pronta" and obra.get("concluida_em"):
        falta = [ROTULO_DOC[t].split(" (")[0].lower() for t in ("habite_se", "cnd_obra", "averbacao")
                 if d[t]["status"] == "pendente"]
        if falta:
            dias = (hoje - obra["concluida_em"]).days
            out.append(f"Pronta há {dias} dia(s) esperando {falta[0]}.")
    return out


def situacao_da_casa(pool, conta_id: int, obra: dict, hoje: date | None = None) -> dict:
    """Tudo o que a ficha e o agente precisam sobre a venda de UMA casa."""
    docs = documentos(pool, conta_id, obra["id"])
    v = venda(pool, conta_id, obra["id"])
    passos = caminho(obra, docs, v)
    return {"documentos": docs, "venda": v, "caminho": passos,
            "trava": next((p for p in passos if p["trava"]), None),
            "alertas": alertas(obra, docs, v, hoje)}


def parado_em_casas(pool, conta_id: int, obras: list[dict]) -> int:
    """Quanto já foi gasto em casa que a Caixa ainda não pagou — o capital parado."""
    casas = [o for o in obras if o["tipo"] == "casa"
             and o["status"] not in ("entregue", "arquivada")]
    if not casas:
        return 0
    with pool.connection() as c:
        creditadas = {r[0] for r in c.execute(
            "select obra_id from obra_vendas where conta_id=%s and situacao='creditado'",
            (conta_id,)).fetchall()}
    return sum(o["custos"]["total"] for o in casas if o["id"] not in creditadas)


def resumo_caminho(obra: dict, sit: dict) -> str:
    """O caminho de uma casa em uma frase de WhatsApp."""
    t = sit["trava"]
    if not t:
        txt = f"{obra['nome']}: dinheiro na conta. ✅"
    else:
        txt = f"{obra['nome']}: o que trava agora é {t['nome'].lower()}."
    v = sit["venda"]
    if v and v["situacao"] != "desistiu":
        txt += f" Venda: {v['rotulo_situacao'].lower()}"
        if v["comprador"]:
            txt += f" ({v['comprador']})"
        txt += "."
        if v["repasse_centavos"] and v["situacao"] != "creditado":
            txt += f" A Caixa paga {_ob._brl(v['repasse_centavos'])} depois do registro."
    for a in sit["alertas"][:2]:
        txt += f" ⚠️ {a}"
    return txt


def topo_financeiro(pool, conta_id: int) -> dict | None:
    """A faixa das obras no topo do Financeiro (desenho, seção 06): o dinheiro
    parado em casa que a Caixa ainda não pagou e o gasto de obra sem obra. None
    quando não há nada a dizer — a faixa só aparece com número."""
    obras = [o for o in _ob.listar_obras(pool, conta_id) if o["status"] != "arquivada"]
    parado = parado_em_casas(pool, conta_id, obras) if obras else 0
    travadas = []
    for o in obras:
        if o["tipo"] == "casa" and o["pct"] == 100 and o["status"] not in ("entregue",):
            t = situacao_da_casa(pool, conta_id, o)["trava"]
            if t and t["chave"] != "creditado":
                travadas.append(f"{o['nome']} ({t['nome'].lower()})")
            elif t:
                travadas.append(f"{o['nome']} (crédito da Caixa)")
    sem = _ob.sem_obra(pool, conta_id, limite=1)
    if not parado and not sem["n"]:
        return None
    return {"parado": parado, "travadas": travadas,
            "sem_n": sem["n"], "sem_total": sem["total_centavos"]}
