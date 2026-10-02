"""As obras agrupadas por quadra — ou setor, bloco: a empresa escolhe (migração 478).

Desenho aprovado pelo dono em 01/10/2026 (docs/mockups/obras_por_quadra.html,
"segue as recomendações"):
  1. o nome do grupo é da empresa, com "Quadra" como padrão, e pode mudar;
  2. o empreendimento (o loteamento) é um texto na quadra, sem nível próprio;
  3. o custo comum da quadra entra no custo da casa pelo m² (PR 2);
  4. "terminei a fundação da quadra 5" marca na hora, diz quem ficou de fora,
     e "desfaz" volta a última marcação em lote;
  5. o mapa é uma grade pela numeração dos lotes (PR 2).

A CASA CONTINUA SENDO A OBRA. A quadra só junta: casa sem quadra não muda nada, e
a quadra e o lote são lidos à parte (`por_obra`) — a lista de colunas da obra
(`obras._COLS`) é lida em todo canto e não pode depender da 478.

QUEM ENTRA NA MARCAÇÃO EM LOTE: as casas da quadra que já começaram (têm data de
início até hoje, ou alguma etapa feita). A casa que nem começou fica de fora e é
dita na resposta — marcar fundação num lote vazio seria mentir o andamento. Quem
escolhe as casas na mão (o quadro do painel, ou "lotes 1 a 6") marca nelas.
"""
from __future__ import annotations

import json
import re
from datetime import date

from . import obras as _ob

ROTULO_PADRAO = "Quadra"


def _tem_tabela(c) -> bool:
    return c.execute("select to_regclass('public.obra_grupos')").fetchone()[0] is not None


# ── o nome do grupo ───────────────────────────────────────────────────────
def rotulo(pool, conta_id: int) -> str:
    try:
        with pool.connection() as c:
            r = c.execute("select obras_rotulo_grupo from contas where id=%s", (conta_id,)).fetchone()
        return ((r[0] if r else "") or "").strip() or ROTULO_PADRAO
    except Exception:  # noqa: BLE001 — sem a 478
        return ROTULO_PADRAO


def salvar_rotulo(pool, conta_id: int, txt: str) -> str:
    t = " ".join((txt or "").split())[:20]
    with pool.connection() as c:
        c.execute("update contas set obras_rotulo_grupo=%s where id=%s",
                  (t if t and t != ROTULO_PADRAO else None, conta_id))
        c.commit()
    return t or ROTULO_PADRAO


# ── as quadras ────────────────────────────────────────────────────────────
def criar_grupo(pool, conta_id: int, nome: str, empreendimento: str = "") -> dict:
    nome = " ".join((nome or "").split())[:60]
    if not nome:
        raise ValueError("Dê um nome — por exemplo, Quadra 4.")
    with pool.connection() as c:
        if c.execute("select 1 from obra_grupos where conta_id=%s and lower(nome)=lower(%s)",
                     (conta_id, nome)).fetchone():
            raise ValueError(f"Já existe “{nome}”.")
        gid = c.execute("""insert into obra_grupos (conta_id, nome, empreendimento)
                           values (%s,%s,%s) returning id""",
                        (conta_id, nome, " ".join((empreendimento or "").split())[:80])).fetchone()[0]
        c.commit()
    garantir_centro(pool, conta_id, gid)        # o custo comum já nasce com a quadra
    return {"id": gid, "nome": nome}


def editar_grupo(pool, conta_id: int, grupo_id: int, nome: str, empreendimento: str = "") -> None:
    nome = " ".join((nome or "").split())[:60]
    if not nome:
        raise ValueError("O grupo precisa de um nome.")
    with pool.connection() as c:
        if c.execute("""select 1 from obra_grupos where conta_id=%s and lower(nome)=lower(%s)
                          and id<>%s""", (conta_id, nome, grupo_id)).fetchone():
            raise ValueError(f"Já existe “{nome}”.")
        c.execute("update obra_grupos set nome=%s, empreendimento=%s where id=%s and conta_id=%s",
                  (nome, " ".join((empreendimento or "").split())[:80], grupo_id, conta_id))
        c.execute("""update centros_custo set nome=%s where conta_id=%s
                       and id = (select centro_custo_id from obra_grupos where id=%s and conta_id=%s)""",
                  (_nome_centro(nome), conta_id, grupo_id, conta_id))
        c.commit()


def apagar_grupo(pool, conta_id: int, grupo_id: int) -> None:
    """Só a quadra vazia sai: a casa não fica órfã sem a pessoa decidir."""
    with pool.connection() as c:
        if c.execute("select 1 from obras where conta_id=%s and grupo_id=%s", (conta_id, grupo_id)).fetchone():
            raise ValueError("Tire as casas dela antes de apagar.")
        centro = c.execute("select centro_custo_id from obra_grupos where id=%s and conta_id=%s",
                           (grupo_id, conta_id)).fetchone()
        if centro and centro[0] and c.execute(
                "select 1 from lancamentos where conta_id=%s and centro_custo_id=%s limit 1",
                (conta_id, centro[0])).fetchone():
            raise ValueError("Ela tem custo comum lançado: tire os lançamentos dela antes de apagar.")
        c.execute("delete from obra_grupos where id=%s and conta_id=%s", (grupo_id, conta_id))
        if centro and centro[0]:
            # o centro vazio sai dos selects (inativo), sem sumir do histórico
            c.execute("update centros_custo set ativo=false where id=%s and conta_id=%s",
                      (centro[0], conta_id))
        c.commit()


def listar_grupos(pool, conta_id: int) -> list[dict]:
    try:
        with pool.connection() as c:
            if not _tem_tabela(c):
                return []
            rows = c.execute("""select id, nome, empreendimento from obra_grupos
                                 where conta_id=%s order by ordem, lower(nome), id""", (conta_id,)).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [{"id": r[0], "nome": r[1], "empreendimento": r[2]} for r in rows]


def por_obra(pool, conta_id: int) -> dict[int, dict]:
    """{obra_id: {grupo_id, lote}} — vazio sem a 478."""
    try:
        with pool.connection() as c:
            if not _tem_tabela(c):
                return {}
            rows = c.execute("""select id, grupo_id, lote from obras
                                 where conta_id=%s and (grupo_id is not null or coalesce(lote,'')<>'')""",
                             (conta_id,)).fetchall()
    except Exception:  # noqa: BLE001
        return {}
    return {r[0]: {"grupo_id": r[1], "lote": r[2] or ""} for r in rows}


def definir(pool, conta_id: int, obra_id: int, grupo_id: int | None, lote: str = "") -> None:
    """Põe a casa na quadra (ou tira, com None) e guarda o lote."""
    with pool.connection() as c:
        if grupo_id and not c.execute("select 1 from obra_grupos where id=%s and conta_id=%s",
                                      (grupo_id, conta_id)).fetchone():
            raise ValueError("Essa quadra não é desta conta.")
        c.execute("update obras set grupo_id=%s, lote=%s where id=%s and conta_id=%s",
                  (grupo_id or None, " ".join((lote or "").split())[:20] or None, obra_id, conta_id))
        c.commit()


def _numero(txt: str) -> str:
    m = re.search(r"\d+", txt or "")
    if not m:
        return ""
    return m.group(0).lstrip("0") or "0"


def grupo_por_nome(pool, conta_id: int, ref: str | None) -> dict | None:
    """A quadra pelo jeito que a pessoa fala: "quadra 5", "Q5", "5", "setor norte".
    Exato primeiro; depois pelo número; depois o único que contém. Ambíguo: None."""
    grupos = listar_grupos(pool, conta_id)
    alvo = _ob._norm(ref)
    if not alvo or not grupos:
        return None
    for g in grupos:
        if _ob._norm(g["nome"]) == alvo:
            return g
    num = _numero(alvo)
    if num:
        achou = [g for g in grupos if _numero(g["nome"]) == num]
        if len(achou) == 1:
            return achou[0]
    rot = _ob._norm(rotulo(pool, conta_id))
    sem_rotulo = alvo.replace(rot, "").strip() if rot else alvo
    achou = [g for g in grupos if sem_rotulo and sem_rotulo in _ob._norm(g["nome"])]
    return achou[0] if len(achou) == 1 else None


def _ordem_lote(o: dict):
    n = _numero(o.get("lote") or "")
    return (0, int(n)) if n else (1, _ob._norm(o.get("lote") or o["nome"]))


def casas(pool, conta_id: int, grupo_id: int) -> list[dict]:
    """As obras da quadra, completas (etapas e custos), na ordem do lote."""
    mapa = por_obra(pool, conta_id)
    ids = [oid for oid, g in mapa.items() if g["grupo_id"] == grupo_id]
    out = []
    for o in _ob.listar_obras(pool, conta_id):
        if o["id"] in ids:
            o["lote"] = mapa[o["id"]]["lote"]
            out.append(o)
    return sorted(out, key=_ordem_lote)


def andamento(obras: list[dict]) -> int:
    """O % da quadra: média das casas pesada pela área (sem área, peso 1)."""
    if not obras:
        return 0
    pesos = [float(o.get("area_m2") or 0) or 1.0 for o in obras]
    return int(round(sum(o["pct"] * p for o, p in zip(obras, pesos)) / sum(pesos)))


def comecou(o: dict, hoje: date | None = None) -> bool:
    if hoje is None:
        from .relogio import hoje as _hoje_br   # o dia de Brasília, não o do servidor
        hoje = _hoje_br()
    return bool((o.get("inicio_em") and o["inicio_em"] <= hoje)
                or any(e["concluida_em"] for e in o.get("etapas", [])))


def resumo(o_list: list[dict]) -> dict:
    return {"n": len(o_list), "pct": andamento(o_list),
            "prontas": sum(1 for o in o_list if o["pct"] == 100 or o["status"] in ("pronta", "vendida", "entregue")),
            "gasto": sum(o["custos"]["total"] for o in o_list if o.get("custos"))}


# ── o quadro casas × etapas ───────────────────────────────────────────────
def quadro(pool, conta_id: int, grupo_id: int) -> dict:
    """As colunas (a união das etapas, na ordem da 1ª casa) e cada célula:
    'paga' (feita e paga), 'feita', 'adiantada' (paga e não feita), 'falta' ou
    None (a casa não tem essa etapa)."""
    lista = casas(pool, conta_id, grupo_id)
    colunas, vistas = [], set()
    for o in lista:
        for e in o["etapas"]:
            if e["chave"] not in vistas:
                vistas.add(e["chave"])
                colunas.append({"chave": e["chave"], "nome": e["nome"]})
    pagos: dict = {}
    try:
        from . import obra_empreita as _oe
        for o in lista:
            pagos[o["id"]] = {e["id"]: e["pago_centavos"] for e in _oe.situacao(pool, conta_id, o)["etapas"]}
    except Exception:  # noqa: BLE001 — sem a 371, sem pagamento
        pagos = {}
    celulas = {}
    for o in lista:
        por_chave = {e["chave"]: e for e in o["etapas"]}
        linha = {}
        for col in colunas:
            e = por_chave.get(col["chave"])
            if not e:
                linha[col["chave"]] = None
                continue
            pago = (pagos.get(o["id"]) or {}).get(e["id"], 0)
            if e["concluida_em"]:
                linha[col["chave"]] = "paga" if pago else "feita"
            else:
                linha[col["chave"]] = "adiantada" if pago else "falta"
        celulas[o["id"]] = linha
    return {"casas": lista, "colunas": colunas, "celulas": celulas, "resumo": resumo(lista)}


# ── a marcação em lote e o "desfaz" ───────────────────────────────────────
def marcar_etapa_grupo(pool, conta_id: int, grupo_id: int, etapa_ref, *,
                       obra_ids=None, quando: date | None = None) -> dict:
    """Marca a etapa nas casas da quadra (as que começaram, ou as escolhidas).
    Devolve {etapa, marcadas, ja_feitas, fora, sem_etapa, pct}. ValueError se a
    quadra não existe, está vazia ou ninguém tem a etapa."""
    lista = casas(pool, conta_id, grupo_id)
    if not lista:
        raise ValueError("Essa quadra ainda não tem casas.")
    escolhidas = {int(i) for i in obra_ids} if obra_ids else None
    marcadas, ja, fora, sem, nome_etapa, chave = [], [], [], [], None, None
    for o in lista:
        rot = f"Lote {o['lote']}" if o.get("lote") else o["nome"]
        if escolhidas is not None and o["id"] not in escolhidas:
            continue
        e = _ob._achar_etapa(o["etapas"], etapa_ref) if not isinstance(etapa_ref, int) else None
        if not e:
            sem.append(rot)
            continue
        nome_etapa, chave = e["nome"], e["chave"]
        if e["concluida_em"]:
            ja.append(rot)
            continue
        if escolhidas is None and not comecou(o):
            fora.append(rot)
            continue
        _ob.marcar_etapa(pool, conta_id, o["id"], e["id"], concluida=True, quando=quando)
        marcadas.append((o["id"], rot))
    if not nome_etapa:
        raise ValueError(f"Nenhuma casa dessa quadra tem a etapa “{etapa_ref}”.")
    if marcadas:
        with pool.connection() as c:
            c.execute("""insert into obra_grupo_marcacoes (conta_id, grupo_id, etapa_chave, obra_ids)
                         values (%s,%s,%s,%s)""",
                      (conta_id, grupo_id, chave, json.dumps([i for i, _ in marcadas])))
            c.commit()
    return {"etapa": nome_etapa, "marcadas": [r for _, r in marcadas], "ja_feitas": ja,
            "fora": fora, "sem_etapa": sem, "pct": andamento(casas(pool, conta_id, grupo_id))}


def desfazer_ultima(pool, conta_id: int, grupo_id: int) -> dict | None:
    """Volta a última marcação em lote da quadra que ainda não foi desfeita."""
    with pool.connection() as c:
        r = c.execute("""select id, etapa_chave, obra_ids from obra_grupo_marcacoes
                          where conta_id=%s and grupo_id=%s and desfeita_em is null
                          order by criado_em desc, id desc limit 1""", (conta_id, grupo_id)).fetchone()
        if not r:
            return None
        c.execute("update obra_grupo_marcacoes set desfeita_em=now() where id=%s", (r[0],))
        c.commit()
    ids = r[2] if isinstance(r[2], list) else json.loads(r[2] or "[]")
    voltaram, nome = [], None
    for oid in ids:
        o = _ob.obter_obra(pool, conta_id, int(oid))
        e = next((x for x in (o or {}).get("etapas", []) if x["chave"] == r[1]), None)
        if o and e and e["concluida_em"]:
            _ob.marcar_etapa(pool, conta_id, o["id"], e["id"], concluida=False)
            voltaram.append(o["nome"])
            nome = e["nome"]
    return {"etapa": nome or r[1], "voltaram": voltaram}


# ── o custo comum da quadra, pelo m² (PR 2; decisão 3 do dono) ────────────
#
# Terraplanagem, rede de água e esgoto, poste, muro da quadra: não são de casa
# nenhuma, mas são de todas. Ficam no CENTRO DE CUSTO da quadra ("Quadra 4 ·
# comum") e entram no custo de cada casa na proporção da área — a conta é feita
# na hora de mostrar, como a divisão de nota: o lançamento não é quebrado, e o
# extrato e a conciliação continuam batendo.
#
# O centro nasce no PAINEL (criar a quadra, ou abrir a página dela): o agente não
# cria centro de custo (regra do dono de 23/09).

def _nome_centro(nome: str) -> str:
    return f"{nome} · comum"[:80]


def centro_da_quadra(pool, conta_id: int, grupo_id: int) -> int | None:
    with pool.connection() as c:
        r = c.execute("select centro_custo_id from obra_grupos where id=%s and conta_id=%s",
                      (grupo_id, conta_id)).fetchone()
    return r[0] if r else None


def garantir_centro(pool, conta_id: int, grupo_id: int) -> int:
    """O centro de custo da quadra; cria se ainda não tem. Só o painel chama."""
    with pool.connection() as c:
        r = c.execute("select centro_custo_id, nome from obra_grupos where id=%s and conta_id=%s",
                      (grupo_id, conta_id)).fetchone()
        if not r:
            raise ValueError("Quadra não encontrada.")
        if r[0]:
            return r[0]
        cid = c.execute("""insert into centros_custo (conta_id, nome, descricao)
                           values (%s,%s,'custo comum da quadra') returning id""",
                        (conta_id, _nome_centro(r[1]))).fetchone()[0]
        c.execute("update obra_grupos set centro_custo_id=%s where id=%s and conta_id=%s",
                  (cid, grupo_id, conta_id))
        c.commit()
    return cid


def custo_comum(pool, conta_id: int, grupo_id: int) -> int:
    """O total lançado no centro da quadra (com a divisão de nota aplicada)."""
    centro = centro_da_quadra(pool, conta_id, grupo_id)
    if not centro:
        return 0
    with pool.connection() as c:
        return int(_ob._custos(c, conta_id, [centro])[centro]["total"])


def partes_do_comum(obras: list[dict], comum: int) -> dict[int, int]:
    """{obra_id: parte} — pela área; se alguma casa não tem área, em partes iguais.
    Os centavos que sobram ficam na primeira casa."""
    if not obras or not comum:
        return {o["id"]: 0 for o in obras}
    areas = [float(o.get("area_m2") or 0) for o in obras]
    if all(a > 0 for a in areas):
        partes = [int(comum * a // sum(areas)) for a in areas]
    else:
        partes = [comum // len(obras)] * len(obras)
    partes[0] += comum - sum(partes)
    return {o["id"]: p for o, p in zip(obras, partes)}


def com_comum(pool, conta_id: int, obra: dict) -> dict:
    """A obra com o custo CHEIO: o lançado nela mais a parte do comum da quadra.
    Devolve uma cópia (`custos.comum`, `custos.total` somado, `custo_m2` e
    `pct_previsto` refeitos); sem quadra ou sem comum, `custos.comum` = 0."""
    o = dict(obra, custos=dict(obra.get("custos") or {}))
    o["custos"].setdefault("comum", 0)
    gid = (por_obra(pool, conta_id).get(obra["id"]) or {}).get("grupo_id")
    if not gid:
        return o
    comum = custo_comum(pool, conta_id, gid)
    if not comum:
        return o
    parte = partes_do_comum(casas(pool, conta_id, gid), comum).get(obra["id"], 0)
    o["custos"]["comum"] = parte
    o["custos"]["total"] = int(o["custos"].get("total") or 0) + parte
    if o.get("area_m2"):
        o["custo_m2"] = int(round(o["custos"]["total"] / float(o["area_m2"])))
    if o.get("custo_previsto_centavos"):
        o["pct_previsto"] = int(round(100 * o["custos"]["total"] / o["custo_previsto_centavos"]))
    return o


def por_na_quadra(pool, conta_id: int, lancamento_id: int, grupo_id: int) -> dict:
    """O lançamento inteiro vai pro custo comum da quadra. Desfaz divisão antiga.
    ValueError se a quadra ainda não tem centro (ele nasce no painel)."""
    centro = centro_da_quadra(pool, conta_id, grupo_id)
    if not centro:
        raise ValueError("Abra a quadra no painel uma vez pra ela ganhar o custo comum.")
    with pool.connection() as c:
        lanc = c.execute("select id, tipo, valor_centavos, natureza from lancamentos "
                         "where id=%s and conta_id=%s", (lancamento_id, conta_id)).fetchone()
        if not lanc:
            raise ValueError("Não achei esse lançamento.")
        if lanc[3] == "pessoal":
            raise ValueError("Esse lançamento está marcado como pessoal.")
        nome = c.execute("select nome from obra_grupos where id=%s", (grupo_id,)).fetchone()[0]
        c.execute("delete from lancamento_rateio where lancamento_id=%s and conta_id=%s",
                  (lancamento_id, conta_id))
        c.execute("""update lancamentos set centro_custo_id=%s, natureza=coalesce(natureza, 'empresa')
                      where id=%s and conta_id=%s""", (centro, lancamento_id, conta_id))
        c.commit()
    # custo comum não é de casa nenhuma: o material da nota volta pro depósito
    from . import obra_material as _omat
    _omat.realocar_do_lancamento(pool, conta_id, lancamento_id)
    return {"quadra": nome, "valor_centavos": int(lanc[2])}


# ── o mapa em grade (PR 2; decisão 5 do dono) ─────────────────────────────
def mapa(pool, conta_id: int, grupo_id: int) -> list[dict]:
    """Os lotes na ordem da numeração, cada um com a faixa de cor do andamento e o
    alerta (o que trava a casa pronta, ou o primeiro prazo vencendo)."""
    out = []
    try:
        from . import obra_venda as _ov
    except Exception:  # noqa: BLE001
        _ov = None
    for o in casas(pool, conta_id, grupo_id):
        alerta = ""
        if _ov is not None and o["tipo"] == "casa":
            try:
                sit = _ov.situacao_da_casa(pool, conta_id, o)
                if o["pct"] == 100 and sit["trava"] and sit["trava"]["chave"] != "creditado":
                    alerta = "trava: " + sit["trava"]["nome"].lower()
                elif sit["alertas"]:
                    alerta = sit["alertas"][0]
            except Exception:  # noqa: BLE001 — sem a 353, sem alerta
                alerta = ""
        pct = o["pct"]
        faixa = ("pronta" if pct == 100 else "f3" if pct > 60 else "f2" if pct > 30
                 else "f1" if pct > 0 else "f0")
        out.append({"id": o["id"], "lote": o.get("lote") or o["nome"], "pct": pct,
                    "faixa": faixa, "alerta": alerta})
    return out
