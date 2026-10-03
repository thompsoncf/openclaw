"""O controle de material da obra (migração 484) — onde a obra sangra.

Desenho aprovado pelo dono em 02/10/2026 (docs/mockups/obras_mapa_3d.html,
seção 3, "segue as recomendações"): TUDO que a nota tiver entra (a leitura já é
item a item — migração 016); apontar o uso é OPCIONAL (quem não aponta já ganha
a comparação de compra entre as casas irmãs); UM depósito por conta; e o CUSTO
NÃO É TOCADO — o dinheiro continua nascendo do lançamento, material é régua de
quantidade.

REAPROVEITA O MOTOR DO FORNECEDOR (catalogo_produtos + estoque_mov, migração
032), como a clínica fez, com a dimensão nova: `obra_id` no movimento (NULL = o
depósito central). Entrada de obra NÃO carrega custo unitário — o CMP do
fornecedor fica em paz.

DE ONDE VEM CADA NÚMERO:
  entrou  = entradas naquele local (nota absorvida, transferência que chegou)
  usado   = saídas e perdas naquele local ("usei 15 sacos na casa 2")
  na obra = entrou − usado (± ajustes); negativo = FURO (uso maior que entrada)

O FLUXO DA NOTA: o leitor salva os itens (registrar_itens_cupom) → o gancho
`livro.apos_itens` chama `absorver_lancamento` → cada item vira uma entrada no
local do lançamento (a obra do centro de custo, ou o depósito). Quando a pessoa
toca o botão "É de qual obra?" DEPOIS, `realocar_do_lancamento` leva o material
junto com o dinheiro. O item da nota é único no movimento (`item_id`): cupom em
lotes ou reabsorvido não duplica.

A RÉGUA MAIS HONESTA É A QUADRA: num loteamento as casas são iguais, então o
alerta compara o consumo da casa com a média das irmãs que estão na mesma
altura ou além — sem cadastrar previsto nenhum (`alerta_irmas`).
"""
from __future__ import annotations

import re
import uuid
from decimal import Decimal

from . import obras as _ob

# o que o painel e os alertas destacam (decisão 1: tudo entra; o DESTAQUE é dos
# materiais-chave). Padrões sobre o nome normalizado (_ob._norm).
MATERIAIS_CHAVE = (r"\bcimento\b", r"\bferro\b|vergalh|aco ca", r"\bareia\b",
                   r"\bbrita\b", r"tijolo|bloco", r"telha", r"argamassa|\bcal\b")

# as categorias em que nota de material de obra cai (obras._CATEGORIA_CUSTO:
# 'Compras' e 'Construcao' são do histórico da PX2, antes da persona do ramo)
CATEGORIAS_MATERIAL = ("Insumos", "Construcao", "Compras")

_UNIDADES = {"sc": "saco", "sacos": "saco", "saco": "saco", "m3": "m³", "m³": "m³",
             "mt3": "m³", "barras": "barra", "barra": "barra", "br": "barra",
             "latas": "lata", "lata": "lata", "lt": "lata", "milheiros": "milheiro",
             "milheiro": "milheiro", "mil": "milheiro", "kg": "kg", "un": "unidade",
             "und": "unidade", "unid": "unidade", "pc": "unidade", "m": "m", "m2": "m²",
             "m²": "m²", "l": "litro", "litro": "litro"}


def _tem_484(c) -> bool:
    return c.execute("""select 1 from information_schema.columns
                         where table_name='estoque_mov' and column_name='item_id'""").fetchone() is not None


def _unidade(txt: str | None) -> str:
    t = _ob._norm(txt or "").replace(".", "")
    return _UNIDADES.get(t, (txt or "").strip()[:12] or "unidade")


def eh_chave(nome: str) -> bool:
    n = _ob._norm(nome)
    return any(re.search(p, n) for p in MATERIAIS_CHAVE)


def _qtd(v) -> str:
    """'6', '5,5' — número de gente, sem zeros pendurados."""
    d = Decimal(str(v or 0)).normalize()
    s = format(d, "f")
    return s.replace(".", ",") if "." in s else s


def quantidade_br(txt) -> Decimal:
    """A quantidade como se digita no Brasil: "1,5" é um e meio, "1.000" é mil
    (ponto de milhar), "1.250,5" também vale. Sem vírgula o ponto é decimal
    ("1.5"), menos no milhar óbvio ("1.000", "12.500"). Recusa com ValueError o
    que não é quantidade (vazio, texto, nan/inf, negativo, grande demais pro
    numeric(12,3)) e arredonda em 3 casas — a precisão do banco, pra comparar
    igual ao que fica gravado ("10.0004" é 10)."""
    s = str(txt if txt is not None else "").strip().replace(" ", "")
    if "," in s:
        s = s.replace(".", "").replace(",", ".")
    elif re.fullmatch(r"[1-9]\d{0,2}(\.\d{3})+", s):
        s = s.replace(".", "")
    try:
        d = Decimal(s)
    except Exception:  # noqa: BLE001
        raise ValueError("Quantidade inválida.")
    if not d.is_finite():
        raise ValueError("Quantidade inválida.")
    if d < 0:
        raise ValueError("Quantidade não pode ser negativa.")
    if d >= Decimal("1e9"):
        raise ValueError("Quantidade grande demais.")
    d = d.quantize(Decimal("0.001"))
    if d >= Decimal("1e9"):                   # 999999999,9996 arredonda pra 1 bilhão
        raise ValueError("Quantidade grande demais.")
    return d


def rotulo(qtd, unidade: str) -> str:
    u = unidade or "unidade"
    q = Decimal(str(qtd or 0))
    if u in ("saco", "barra", "lata", "milheiro", "unidade", "litro") and q != 1:
        u += "s"
    return f"{_qtd(qtd)} {u}"


# ── o produto (o mesmo catálogo do fornecedor, categoria 'material') ──────
def _palavras(txt: str) -> list[str]:
    return [w for w in re.split(r"[^0-9a-z]+", _ob._norm(txt)) if w]


def _contem_palavras(curto: str, longo: str) -> bool:
    """Todas as palavras de `curto` estão em `longo`, como palavra INTEIRA —
    'cal' não pode casar com 'calha', nem 'areia' com 'areial'."""
    pc, pl = _palavras(curto), set(_palavras(longo))
    return bool(pc) and all(w in pl for w in pc)


def _materiais(c, conta_id: int) -> list[tuple]:
    """Só o catálogo de MATERIAL: o catálogo de venda da mesma conta (se ela
    também vende) nunca recebe quantidade de nota de obra."""
    return c.execute("""select id, nome, unidade from catalogo_produtos
                         where fornecedor_id=%s and ativo and categoria='material'
                         order by id""", (conta_id,)).fetchall()


def _achar_ou_criar(c, conta_id: int, descricao: str, unidade: str) -> tuple[int, str, str]:
    """(produto_id, nome, unidade). A nota repete o mesmo texto ('CIMENTO CP II
    50KG' toda vez), então casa pelo NOME EXATO na MESMA UNIDADE; depois, pelo
    produto cujo nome inteiro está no item (palavra a palavra) e na mesma
    unidade. Nunca o contrário: 'cimento' (falado) não pode engolir 'COLA
    CIMENTO PVC', e areia em saco não é areia em m³. Não achou: nasce, sem
    preço (material de obra não é venda)."""
    alvo, u = _ob._norm(descricao), _unidade(unidade)
    rows = _materiais(c, conta_id)
    for pid, nome, un in rows:
        if _ob._norm(nome) == alvo and (un or "unidade") == u:
            return pid, nome, un
    parcial = [(pid, nome, un) for pid, nome, un in rows
               if (un or "unidade") == u and len(_ob._norm(nome)) > 3
               and _contem_palavras(nome, descricao)]
    if len(parcial) == 1:
        return parcial[0]
    nome = " ".join((descricao or "Material").split())[:80]
    pid = c.execute("""insert into catalogo_produtos (fornecedor_id, nome, unidade,
                           categoria, disponivel)
                       values (%s,%s,%s,'material',false) returning id""",
                    (conta_id, nome, u)).fetchone()[0]
    return pid, nome, u


def _mov(c, conta_id: int, produto_id: int, tipo: str, qtd, *, obra_id=None,
         transf_id=None, lancamento_id=None, item_id=None, motivo=None) -> int:
    """Grava o movimento e devolve o id dele (o "desfazer" do app do mestre
    apaga pelos ids). O movimento é a verdade, e o saldo sai SEMPRE da soma por local
    (`saldo_local`, `_quadros`). O cache `catalogo_produtos.saldo` do motor do
    fornecedor NÃO é tocado: ele é da conta inteira, e o apagar-a-nota (cascade
    da 484) não teria como acertá-lo."""
    return c.execute("""insert into estoque_mov (produto_id, fornecedor_id, tipo, quantidade,
                     obra_id, transf_id, lancamento_id, item_id, motivo)
                 values (%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
              (produto_id, conta_id, tipo, qtd, obra_id, transf_id,
               lancamento_id, item_id, motivo)).fetchone()[0]


def _obra_do_lancamento(c, conta_id: int, lancamento_id: int):
    r = c.execute("""select o.id from lancamentos l
                       join obras o on o.conta_id = l.conta_id
                                   and o.centro_custo_id = l.centro_custo_id
                      where l.id=%s and l.conta_id=%s""",
                  (lancamento_id, conta_id)).fetchone()
    return r[0] if r else None


# ── a nota vira quantidade (o gancho do registrar_itens_cupom) ────────────
def absorver_lancamento(pool, conta_id: int, lancamento_id: int) -> str:
    """Os itens da nota viram ENTRADAS de material — na obra do lançamento, ou
    no depósito se ainda não tem obra. Idempotente por item (cupom em lotes
    reabsorve só o que falta). Devolve a frase pra resposta do agente ('' se
    nada novo). Nunca levanta: material não pode derrubar o registro do cupom."""
    try:
        with pool.connection() as c:
            if not _tem_484(c):
                return ""
            # SÓ NOTA DE MATERIAL DA EMPRESA: o cupom do mercado da casa do dono
            # (pessoal) e o café da equipe (Mercado) também são itemizados pela
            # regra geral — e não são material de obra. A mesma régua do
            # obras.tipo_de_custo: conta 3.1.03, ou as categorias de material.
            itens = c.execute("""select i.id, i.descricao, i.quantidade, i.unidade
                                   from itens_lancamento i
                                   join lancamentos l on l.id = i.lancamento_id
                                   left join plano_contas p on p.id = l.plano_conta_id
                                  where i.lancamento_id=%s and l.conta_id=%s
                                    and l.tipo = 'despesa'
                                    and l.natureza is distinct from 'pessoal'
                                    and (p.codigo = '3.1.03'
                                         or l.categoria = any(%s))
                                    and not exists (select 1 from estoque_mov m
                                                     where m.item_id = i.id
                                                       and m.fornecedor_id = l.conta_id)""",
                              (lancamento_id, conta_id, list(CATEGORIAS_MATERIAL))).fetchall()
            if not itens:
                return ""
            obra_id = _obra_do_lancamento(c, conta_id, lancamento_id)
            n = 0
            for iid, desc, qtd, un in itens:
                q = Decimal(str(qtd or 0))
                if q <= 0:
                    continue
                pid, _, _ = _achar_ou_criar(c, conta_id, desc, un or "")
                _mov(c, conta_id, pid, "entrada", q, obra_id=obra_id,
                     lancamento_id=lancamento_id, item_id=iid, motivo="nota")
                n += 1
            c.commit()
        if not n:
            return ""
        if obra_id:
            nome = (_ob.obter_obra(pool, conta_id, obra_id) or {}).get("nome", "a obra")
            return f"🧱 {n} materiais entraram no controle de {nome}."
        return (f"🧱 {n} materiais entraram no depósito — escolhendo a obra, "
                "o material vai junto.")
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger("openclaw.obra_material").exception(
            "falha ao absorver itens do lançamento %s", lancamento_id)
        return ""


def _saldo_em(c, conta_id: int, produto_id: int, obra_id) -> Decimal:
    r = c.execute("""select coalesce(sum(case when tipo in ('entrada','ajuste')
                                              then quantidade else -quantidade end), 0)
                       from estoque_mov
                      where fornecedor_id=%s and produto_id=%s
                        and obra_id is not distinct from %s""",
                  (conta_id, produto_id, obra_id)).fetchone()
    return Decimal(str(r[0] or 0))


def realocar_do_lancamento(pool, conta_id: int, lancamento_id: int) -> None:
    """A nota mudou de obra (o botão, o painel, dividir, a quadra): o material
    vai pra onde o dinheiro foi — a obra do centro, ou o DEPÓSITO quando a nota
    ficou dividida ou no custo comum (os itens não se rateiam sozinhos; quem
    quiser leva com "levei").

    Só move o que AINDA ESTÁ LIVRE na origem: se 10 dos 60 sacos já foram
    usados ou levados de lá, mover a entrada inteira deixaria a origem negativa
    e contaria os 10 duas vezes. A parte livre vai inteira (a entrada muda de
    lugar); se só parte está livre, essa parte vai como transferência pareada.
    Nunca levanta."""
    try:
        with pool.connection() as c:
            if not _tem_484(c):
                return
            destino = _obra_do_lancamento(c, conta_id, lancamento_id)
            entradas = c.execute("""select id, produto_id, obra_id, quantidade from estoque_mov
                                     where lancamento_id=%s and fornecedor_id=%s
                                       and tipo='entrada' order by id""",
                                 (lancamento_id, conta_id)).fetchall()
            for mid, pid, origem, qtd in entradas:
                if origem == destino:
                    continue
                qtd = Decimal(str(qtd))
                livre = min(qtd, max(_saldo_em(c, conta_id, pid, origem), Decimal(0)))
                if livre == qtd:
                    c.execute("update estoque_mov set obra_id=%s where id=%s and fornecedor_id=%s",
                              (destino, mid, conta_id))
                elif livre > 0:
                    t = uuid.uuid4().hex[:12]
                    _mov(c, conta_id, pid, "saida", livre, obra_id=origem, transf_id=t,
                         motivo="a nota mudou de obra")
                    _mov(c, conta_id, pid, "entrada", livre, obra_id=destino, transf_id=t,
                         motivo="a nota mudou de obra")
            c.commit()
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger("openclaw.obra_material").exception(
            "falha ao realocar material do lançamento %s", lancamento_id)


# ── o dia a dia falado: usei / levei / chegou ─────────────────────────────
def achar_produto(pool, conta_id: int, ref: str) -> dict | None:
    """O material pelo jeito que a pessoa falou ('cimento', 'ferro 8'). Exato;
    senão, os que têm TODAS as palavras ditas (palavra inteira). Mais de um:
    {"ambiguo": [nomes]} — quem chama pergunta qual, e não grava nada. Escolher
    um ao acaso daria baixa no cimento errado."""
    alvo = _ob._norm(ref)
    if not alvo:
        return None
    with pool.connection() as c:
        rows = _materiais(c, conta_id)
    exato = [r for r in rows if _ob._norm(r[1]) == alvo]
    parcial = exato or [r for r in rows if _contem_palavras(ref, r[1])]
    if len(parcial) > 1:
        return {"ambiguo": sorted(r[1] for r in parcial)[:6]}
    r = parcial[0] if parcial else None
    return {"id": r[0], "nome": r[1], "unidade": r[2]} if r else None


def mover(pool, conta_id: int, *, acao: str, produto_id: int, quantidade,
          obra_id: int | None = None, por: str = "") -> dict:
    """'usei' (saída na obra), 'levei' (depósito → obra, as duas pernas pareadas),
    'chegou' (entrada no depósito, ou direto na obra). Devolve os saldos do que
    mudou e os avisos (furo, mínimo do depósito)."""
    q = Decimal(str(quantidade or 0))
    if q <= 0:
        raise ValueError("Quantas unidades? Preciso de um número maior que zero.")
    if acao in ("usei", "levei") and not obra_id:
        raise ValueError("De qual obra?")
    ids = []
    with pool.connection() as c:
        if acao == "usei":
            ids.append(_mov(c, conta_id, produto_id, "saida", q, obra_id=obra_id,
                            motivo=f"uso ({por})" if por else "uso"))
        elif acao == "levei":
            t = uuid.uuid4().hex[:12]
            ids.append(_mov(c, conta_id, produto_id, "saida", q, transf_id=t,
                            motivo=f"transferência ({por})" if por else "transferência"))
            ids.append(_mov(c, conta_id, produto_id, "entrada", q, obra_id=obra_id, transf_id=t,
                            motivo="transferência"))
        elif acao == "chegou":
            ids.append(_mov(c, conta_id, produto_id, "entrada", q, obra_id=obra_id,
                            motivo=f"chegada ({por})" if por else "chegada"))
        else:
            raise ValueError("Ação de material desconhecida.")
        c.commit()
    out = {"deposito": saldo_local(pool, conta_id, produto_id, None), "mov_ids": ids}
    if obra_id:
        out["na_obra"] = saldo_local(pool, conta_id, produto_id, obra_id)
    with pool.connection() as c:
        r = c.execute("select estoque_minimo from catalogo_produtos where id=%s and fornecedor_id=%s",
                      (produto_id, conta_id)).fetchone()
    minimo = Decimal(str(r[0] or 0)) if r else Decimal(0)
    out["abaixo_minimo"] = bool(minimo > 0 and out["deposito"] <= minimo)
    out["furo"] = bool(obra_id and out.get("na_obra", Decimal(0)) < 0)
    return out


def saldo_local(pool, conta_id: int, produto_id: int, obra_id: int | None) -> Decimal:
    with pool.connection() as c:
        r = c.execute("""select coalesce(sum(case when tipo in ('entrada','ajuste')
                                                  then quantidade else -quantidade end), 0)
                           from estoque_mov
                          where fornecedor_id=%s and produto_id=%s
                            and obra_id is not distinct from %s""",
                      (conta_id, produto_id, obra_id)).fetchone()
    return Decimal(str(r[0] or 0))


# ── os quadros (a tabela do mockup: entrou / usado / na obra) ─────────────
def _quadros(pool, conta_id: int) -> list[dict]:
    try:
        with pool.connection() as c:
            if not _tem_484(c):
                return []
            rows = c.execute("""
                select m.obra_id, p.id, p.nome, p.unidade, p.estoque_minimo,
                       sum(case when m.tipo='entrada' then m.quantidade else 0 end),
                       sum(case when m.tipo in ('saida','perda') then m.quantidade else 0 end),
                       sum(case when m.tipo='ajuste' then m.quantidade else 0 end)
                  from estoque_mov m
                  join catalogo_produtos p on p.id = m.produto_id
                 where m.fornecedor_id=%s
                 group by 1, 2, 3, 4, 5""", (conta_id,)).fetchall()
    except Exception:  # noqa: BLE001 — sem a 032/484
        return []
    out = []
    for obra_id, pid, nome, un, minimo, entrou, usado, ajuste in rows:
        entrou, usado = Decimal(str(entrou)), Decimal(str(usado))
        saldo = entrou - usado + Decimal(str(ajuste))
        out.append({"obra_id": obra_id, "produto_id": pid, "nome": nome,
                    "unidade": un or "unidade", "minimo": Decimal(str(minimo or 0)),
                    "entrou": entrou, "usado": usado, "saldo": saldo,
                    "chave": eh_chave(nome)})
    return out


def _ordenado(linhas: list[dict]) -> list[dict]:
    return sorted(linhas, key=lambda r: (not r["chave"], -r["entrou"], _ob._norm(r["nome"])))


def quadro_da_obra(pool, conta_id: int, obra_id: int) -> list[dict]:
    return _ordenado([r for r in _quadros(pool, conta_id) if r["obra_id"] == obra_id])


def por_obra(pool, conta_id: int) -> dict[int, list[dict]]:
    """{obra_id: linhas} de uma vez só — o mapa pinta todos os lotes com UMA query."""
    out: dict[int, list[dict]] = {}
    for r in _quadros(pool, conta_id):
        if r["obra_id"]:
            out.setdefault(r["obra_id"], []).append(r)
    return {k: _ordenado(v) for k, v in out.items()}


def deposito(pool, conta_id: int) -> list[dict]:
    linhas = [r for r in _quadros(pool, conta_id) if not r["obra_id"]]
    for r in linhas:
        r["abaixo"] = bool(r["minimo"] > 0 and r["saldo"] <= r["minimo"])
    return _ordenado(linhas)


def salvar_minimo(pool, conta_id: int, produto_id: int, minimo) -> None:
    m = Decimal(str(minimo or 0))
    with pool.connection() as c:
        c.execute("update catalogo_produtos set estoque_minimo=%s "
                  "where id=%s and fornecedor_id=%s", (m if m > 0 else 0, produto_id, conta_id))
        c.commit()


# ── os alertas que não pedem cadastro nenhum ──────────────────────────────
def furos(linhas: list[dict]) -> list[str]:
    """Saldo negativo = uso maior que entrada: ou faltou nota, ou material saiu
    por uma porta que ninguém viu. Os dois merecem pergunta."""
    return [f"{r['nome']}: uso maior que entrada ({rotulo(abs(r['saldo']), r['unidade'])} descobertos)"
            for r in linhas if r["saldo"] < 0]


# a "mesma altura": a diferença de andamento até onde o consumo ainda é comparável
ALTURA_PCT = 10


def alerta_irmas(pool, conta_id: int, obra: dict, *, quadros=None, obras=None,
                 grupos=None) -> str:
    """O consumo da casa contra a média das irmãs da quadra NA MESMA ALTURA
    (andamento a até ALTURA_PCT pontos), material-chave por material-chave.

    NA MESMA ALTURA, e não "ou além": a casa mais adiantada já gastou mais
    cimento por estar mais adiantada, e puxaria a média pra cima — a casa
    gastona nunca seria acusada (achado na demonstração de 02/10/2026).

    NA MESMA RÉGUA: compara USO com uso quando a casa e todas as irmãs apontam
    uso daquele material; senão, COMPRA com compra (o que entrou) — sem
    apontamento nenhum, o desvio grosso aparece do mesmo jeito (decisão 2 do
    dono). Misturar as duas réguas acusaria quem aponta."""
    try:
        from . import obra_grupos as og
        # `grupos` ({obra_id: {grupo_id}}) e `obras` ({grupo_id: [obras]}) vêm
        # prontos de quem desenha vários lotes de uma vez (o mapa): sem eles,
        # cada lote refaria a lista de obras inteira, com custos
        gr = grupos if grupos is not None else og.por_obra(pool, conta_id)
        gid = (gr.get(obra["id"]) or {}).get("grupo_id")
        if not gid:
            return ""
        da_quadra = obras.get(gid, []) if obras is not None else og.casas(pool, conta_id, gid)
        irmas = [o for o in da_quadra if o["id"] != obra["id"]
                 and abs(o["pct"] - obra["pct"]) <= ALTURA_PCT]
        if not irmas:
            return ""
        qs = quadros if quadros is not None else por_obra(pool, conta_id)

        def linhas(oid: int) -> dict[str, dict]:
            out: dict[str, dict] = {}
            for r in qs.get(oid, []):
                if r["chave"]:
                    k = _ob._norm(r["nome"])
                    acc = out.setdefault(k, {"entrou": Decimal(0), "usado": Decimal(0)})
                    acc["entrou"] += r["entrou"]
                    acc["usado"] += r["usado"]
            return out

        meu = linhas(obra["id"])
        das_irmas = [linhas(o["id"]) for o in irmas]
        for r in _ordenado(qs.get(obra["id"], [])):
            if not r["chave"]:
                continue
            k = _ob._norm(r["nome"])
            eu = meu.get(k)
            elas = [c[k] for c in das_irmas if k in c and c[k]["entrou"] > 0]
            if not eu or not elas:
                continue
            uso = eu["usado"] > 0 and all(e["usado"] > 0 for e in elas)
            campo = "usado" if uso else "entrou"
            mine = eu[campo]
            valores = [e[campo] for e in elas]
            media = sum(valores) / len(valores)
            if mine <= 0 or media <= 0 or mine <= media * Decimal("1.2"):
                continue
            acima = int(round(100 * (mine - media) / media))
            verbo, verbos = ("usou", "usaram") if uso else ("comprou", "compraram")
            quem = "a irmã" if len(valores) == 1 else f"as {len(valores)} irmãs"
            return (f"{r['nome']} {acima}% acima das irmãs na mesma altura: esta casa já "
                    f"{verbo} {rotulo(mine, r['unidade'])}; {quem} "
                    f"{verbo if len(valores) == 1 else verbos} "
                    f"{_qtd(media.quantize(Decimal('0.1')))}"
                    + (" em média." if len(valores) > 1 else "."))
        return ""
    except Exception:  # noqa: BLE001
        return ""
