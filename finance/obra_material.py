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


def rotulo(qtd, unidade: str) -> str:
    u = unidade or "unidade"
    q = Decimal(str(qtd or 0))
    if u in ("saco", "barra", "lata", "milheiro", "unidade", "litro") and q != 1:
        u += "s"
    return f"{_qtd(qtd)} {u}"


# ── o produto (o mesmo catálogo do fornecedor, categoria 'material') ──────
def _achar_ou_criar(c, conta_id: int, descricao: str, unidade: str) -> tuple[int, str, str]:
    """(produto_id, nome, unidade). Casa pelo nome normalizado — nota escreve
    'CIMENTO CP II 50KG' e gente escreve 'cimento'; os dois têm que cair no
    mesmo produto. Não achou: nasce, sem preço (material de obra não é venda)."""
    alvo = _ob._norm(descricao)
    rows = c.execute("select id, nome, unidade from catalogo_produtos "
                     "where fornecedor_id=%s and ativo", (conta_id,)).fetchall()
    for pid, nome, un in rows:
        n = _ob._norm(nome)
        if n == alvo or (len(n) > 3 and (n in alvo or alvo in n)):
            return pid, nome, un
    nome = " ".join((descricao or "Material").split())[:80]
    pid = c.execute("""insert into catalogo_produtos (fornecedor_id, nome, unidade,
                           categoria, disponivel)
                       values (%s,%s,%s,'material',false) returning id""",
                    (conta_id, nome, _unidade(unidade))).fetchone()[0]
    return pid, nome, _unidade(unidade)


def _mov(c, conta_id: int, produto_id: int, tipo: str, qtd, *, obra_id=None,
         transf_id=None, lancamento_id=None, item_id=None, motivo=None) -> None:
    c.execute("""insert into estoque_mov (produto_id, fornecedor_id, tipo, quantidade,
                     obra_id, transf_id, lancamento_id, item_id, motivo)
                 values (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
              (produto_id, conta_id, tipo, qtd, obra_id, transf_id,
               lancamento_id, item_id, motivo))
    delta = qtd if tipo in ("entrada", "ajuste") else -qtd
    c.execute("update catalogo_produtos set saldo = saldo + %s, atualizado_em = now() "
              "where id=%s", (delta, produto_id))


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
            itens = c.execute("""select i.id, i.descricao, i.quantidade, i.unidade
                                   from itens_lancamento i
                                   join lancamentos l on l.id = i.lancamento_id
                                  where i.lancamento_id=%s and l.conta_id=%s
                                    and not exists (select 1 from estoque_mov m
                                                     where m.item_id = i.id)""",
                              (lancamento_id, conta_id)).fetchall()
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


def realocar_do_lancamento(pool, conta_id: int, lancamento_id: int) -> None:
    """A nota mudou de obra (o toque no botão, o painel): o material vai junto.
    Só mexe nas entradas QUE VIERAM DESSA NOTA — uso e transferência ficam onde
    aconteceram. Nunca levanta."""
    try:
        with pool.connection() as c:
            if not _tem_484(c):
                return
            obra_id = _obra_do_lancamento(c, conta_id, lancamento_id)
            c.execute("""update estoque_mov set obra_id=%s
                          where lancamento_id=%s and fornecedor_id=%s and tipo='entrada'""",
                      (obra_id, lancamento_id, conta_id))
            c.commit()
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger("openclaw.obra_material").exception(
            "falha ao realocar material do lançamento %s", lancamento_id)


# ── o dia a dia falado: usei / levei / chegou ─────────────────────────────
def achar_produto(pool, conta_id: int, ref: str) -> dict | None:
    alvo = _ob._norm(ref)
    if not alvo:
        return None
    with pool.connection() as c:
        rows = c.execute("select id, nome, unidade from catalogo_produtos "
                         "where fornecedor_id=%s and ativo", (conta_id,)).fetchall()
    exato = [r for r in rows if _ob._norm(r[1]) == alvo]
    parcial = [r for r in rows if alvo in _ob._norm(r[1])]
    r = (exato or parcial or [None])[0]
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
    with pool.connection() as c:
        if acao == "usei":
            _mov(c, conta_id, produto_id, "saida", q, obra_id=obra_id,
                 motivo=f"uso ({por})" if por else "uso")
        elif acao == "levei":
            t = uuid.uuid4().hex[:12]
            _mov(c, conta_id, produto_id, "saida", q, transf_id=t,
                 motivo=f"transferência ({por})" if por else "transferência")
            _mov(c, conta_id, produto_id, "entrada", q, obra_id=obra_id, transf_id=t,
                 motivo="transferência")
        elif acao == "chegou":
            _mov(c, conta_id, produto_id, "entrada", q, obra_id=obra_id,
                 motivo=f"chegada ({por})" if por else "chegada")
        else:
            raise ValueError("Ação de material desconhecida.")
        c.commit()
    out = {"deposito": saldo_local(pool, conta_id, produto_id, None)}
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


def alerta_irmas(pool, conta_id: int, obra: dict, *, quadros=None, obras=None) -> str:
    """O consumo da casa contra a média das irmãs da quadra que estão NA MESMA
    ALTURA OU ALÉM (pct >=), material-chave por material-chave. Usa o USO quando
    a casa aponta; sem apontamento, compara a COMPRA (o que entrou) — o desvio
    grosso aparece sem esforço nenhum (decisão 2 do dono)."""
    try:
        from . import obra_grupos as og
        gid = (og.por_obra(pool, conta_id).get(obra["id"]) or {}).get("grupo_id")
        if not gid:
            return ""
        irmas = [o for o in (obras if obras is not None else og.casas(pool, conta_id, gid))
                 if o["id"] != obra["id"] and o["pct"] >= obra["pct"]]
        if len(irmas) < 2:
            return ""
        qs = quadros if quadros is not None else por_obra(pool, conta_id)

        def consumo(oid: int) -> dict[str, Decimal]:
            out: dict[str, Decimal] = {}
            for r in qs.get(oid, []):
                if r["chave"]:
                    k = _ob._norm(r["nome"])
                    out[k] = out.get(k, Decimal(0)) + (r["usado"] if r["usado"] > 0 else r["entrou"])
            return out

        meu = consumo(obra["id"])
        das_irmas = [consumo(o["id"]) for o in irmas]
        for r in _ordenado(qs.get(obra["id"], [])):
            if not r["chave"]:
                continue
            k = _ob._norm(r["nome"])
            valores = [c[k] for c in das_irmas if c.get(k, Decimal(0)) > 0]
            if len(valores) < 2 or meu.get(k, Decimal(0)) <= 0:
                continue
            media = sum(valores) / len(valores)
            if media > 0 and meu[k] > media * Decimal("1.2"):
                acima = int(round(100 * (meu[k] - media) / media))
                return (f"{r['nome']} {acima}% acima das irmãs: já foram "
                        f"{rotulo(meu[k], r['unidade'])} nesta casa; as {len(valores)} irmãs "
                        f"na mesma altura ou além usaram {_qtd(media.quantize(Decimal('0.1')))} em média.")
        return ""
    except Exception:  # noqa: BLE001
        return ""
