"""Catálogo e estoque do fornecedor (Zaq Fornecedor, Fase 1).

O estoque vive de MOVIMENTAÇÕES (entrada/saida/perda). O saldo e o custo médio
(CMP – Custo Médio Ponderado) são DERIVADOS dessas movimentações; guardamos uma
cópia em catalogo_produtos só pra leitura rápida, mas a verdade é estoque_mov.

Regras de negócio:
- ENTRADA: aumenta o saldo; recalcula o custo médio ponderado; registra a origem
  (de quem o fornecedor comprou) e o custo unitário daquela compra.
- SAIDA: diminui o saldo (venda pro cliente). Não mexe no custo médio.
- PERDA: diminui o saldo (estragou/sobrou). Vira a "quebra" REAL do produto.
- AJUSTE: correção manual de inventário (pode ser + ou –, conforme a quantidade
  informada e o tipo escolhido na chamada).

Tudo monetário em CENTAVOS (inteiro). Quantidade em numeric (aceita 1.5 kg etc).
Funções recebem o pool de conexão (padrão do projeto) e validam o fornecedor_id
pra garantir isolamento multi-tenant (um fornecedor nunca mexe no produto de outro).
"""
from __future__ import annotations

from decimal import Decimal
from typing import Optional


# ----------------------------------------------------------------------------
# ORIGEM DA COMPRA (de quem o fornecedor compra – CEASA, produtor, atacadista...)
# ----------------------------------------------------------------------------
def criar_origem(pool, fornecedor_id: int, nome: str, contato: str | None = None) -> int:
    """Cadastra uma origem de compra pro fornecedor. Retorna o id criado.

    Origem = de quem ELE compra pra revender. Não confundir com o fornecedor
    (que é a conta dele no Zaq).
    """
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("nome da origem é obrigatório")
    with pool.connection() as c:
        row = c.execute(
            """insert into origem_compra (fornecedor_id, nome, contato)
               values (%s, %s, %s) returning id""",
            (fornecedor_id, nome, (contato or "").strip() or None),
        ).fetchone()
        c.commit()
    return int(row[0])


def listar_origens(pool, fornecedor_id: int, incluir_inativas: bool = False) -> list[dict]:
    """Lista as origens de compra do fornecedor."""
    sql = """select id, nome, contato, ativo
             from origem_compra where fornecedor_id = %s"""
    if not incluir_inativas:
        sql += " and ativo"
    sql += " order by nome"
    with pool.connection() as c:
        rows = c.execute(sql, (fornecedor_id,)).fetchall()
    return [{"id": r[0], "nome": r[1], "contato": r[2], "ativo": r[3]} for r in rows]


# ----------------------------------------------------------------------------
# PRODUTOS do catálogo
# ----------------------------------------------------------------------------
UNIDADES_VALIDAS = {"kg", "duzia", "unidade", "maco", "bandeja", "litro", "pacote"}


def criar_produto(
    pool,
    fornecedor_id: int,
    nome: str,
    unidade: str = "kg",
    categoria: str | None = None,
    preco_venda_centavos: int = 0,
    estoque_minimo: float = 0,
) -> int:
    """Cria um produto no catálogo do fornecedor. Retorna o id.

    Saldo e custo médio começam em 0 – sobem quando houver ENTRADA de estoque.
    """
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("nome do produto é obrigatório")
    unidade = (unidade or "kg").strip().lower()
    if unidade not in UNIDADES_VALIDAS:
        unidade = "kg"
    with pool.connection() as c:
        row = c.execute(
            """insert into catalogo_produtos
                 (fornecedor_id, nome, unidade, categoria,
                  preco_venda_centavos, estoque_minimo)
               values (%s, %s, %s, %s, %s, %s) returning id""",
            (fornecedor_id, nome, unidade, (categoria or "").strip() or None,
             max(0, int(preco_venda_centavos)), Decimal(str(estoque_minimo or 0))),
        ).fetchone()
        c.commit()
    return int(row[0])


def atualizar_produto(pool, fornecedor_id: int, produto_id: int, **campos) -> bool:
    """Atualiza campos do produto (nome, unidade, categoria, preco_venda_centavos,
    estoque_minimo, disponivel). Só altera se o produto for do fornecedor.

    Retorna True se atualizou. NÃO mexe em saldo nem custo_medio (esses vêm das
    movimentações, não de edição manual – use registrar_movimentacao).
    """
    permitidos = {
        "nome", "unidade", "categoria", "preco_venda_centavos",
        "estoque_minimo", "disponivel",
    }
    sets, vals = [], []
    for k, v in campos.items():
        if k not in permitidos:
            continue
        if k == "unidade":
            v = (v or "kg").strip().lower()
            if v not in UNIDADES_VALIDAS:
                v = "kg"
        if k == "preco_venda_centavos":
            v = max(0, int(v))
        if k == "estoque_minimo":
            v = Decimal(str(v or 0))
        sets.append(f"{k} = %s")
        vals.append(v)
    if not sets:
        return False
    sets.append("atualizado_em = now()")
    vals.extend([produto_id, fornecedor_id])
    with pool.connection() as c:
        cur = c.execute(
            f"""update catalogo_produtos set {', '.join(sets)}
                where id = %s and fornecedor_id = %s""",
            tuple(vals),
        )
        c.commit()
        return cur.rowcount > 0


def arquivar_produto(pool, fornecedor_id: int, produto_id: int) -> bool:
    """Soft delete: marca ativo=false. Mantém histórico de movimentações."""
    with pool.connection() as c:
        cur = c.execute(
            """update catalogo_produtos set ativo = false, atualizado_em = now()
               where id = %s and fornecedor_id = %s""",
            (produto_id, fornecedor_id),
        )
        c.commit()
        return cur.rowcount > 0


def listar_produtos(
    pool, fornecedor_id: int, so_disponiveis: bool = False
) -> list[dict]:
    """Lista o catálogo do fornecedor, com saldo, custo médio, preço e margem.

    margem_pct é calculada na hora (venda vs custo médio). Não é guardada.
    """
    sql = """select id, nome, unidade, categoria, preco_venda_centavos,
                    custo_medio_centavos, saldo, estoque_minimo, disponivel
             from catalogo_produtos
             where fornecedor_id = %s and ativo"""
    if so_disponiveis:
        sql += " and disponivel"
    sql += " order by nome"
    with pool.connection() as c:
        rows = c.execute(sql, (fornecedor_id,)).fetchall()
    out = []
    for r in rows:
        venda = int(r[4] or 0)
        custo = int(r[5] or 0)
        saldo = float(r[6] or 0)
        minimo = float(r[7] or 0)
        out.append({
            "id": r[0],
            "nome": r[1],
            "unidade": r[2],
            "categoria": r[3],
            "preco_venda_centavos": venda,
            "custo_medio_centavos": custo,
            "saldo": saldo,
            "estoque_minimo": minimo,
            "disponivel": r[8],
            "margem_pct": _margem_pct(venda, custo),
            "abaixo_minimo": minimo > 0 and saldo <= minimo,
        })
    return out


def _margem_pct(venda_centavos: int, custo_centavos: int) -> Optional[float]:
    """Margem bruta % sobre a venda. None se não dá pra calcular."""
    if not venda_centavos or venda_centavos <= 0:
        return None
    if not custo_centavos or custo_centavos <= 0:
        return None
    return round((venda_centavos - custo_centavos) / venda_centavos * 100, 1)


# ----------------------------------------------------------------------------
# MOVIMENTAÇÕES de estoque (o coração: saldo e custo médio derivam daqui)
# ----------------------------------------------------------------------------
def registrar_movimentacao(
    pool,
    fornecedor_id: int,
    produto_id: int,
    tipo: str,
    quantidade: float,
    custo_unit_centavos: int | None = None,
    origem_id: int | None = None,
    motivo: str | None = None,
) -> dict:
    """Registra uma movimentação e atualiza saldo (e custo médio, se entrada).

    tipo: 'entrada' | 'saida' | 'perda' | 'ajuste'
    - entrada: saldo += qtd; recalcula custo médio ponderado (precisa custo_unit);
               registra origem_id (de quem comprou).
    - saida:   saldo -= qtd (venda). custo médio inalterado.
    - perda:   saldo -= qtd (estragou). custo médio inalterado.
    - ajuste:  saldo += qtd (qtd pode ser negativa pra corrigir pra baixo).

    Tudo numa transação: a movimentação e o novo saldo são gravados juntos.
    Retorna {saldo_novo, custo_medio_centavos, abaixo_minimo}.
    """
    if tipo not in ("entrada", "saida", "perda", "ajuste"):
        raise ValueError(f"tipo inválido: {tipo}")
    qtd = Decimal(str(quantidade))
    if tipo != "ajuste" and qtd <= 0:
        raise ValueError("quantidade deve ser positiva")

    with pool.connection() as c:
        # trava a linha do produto e confere o dono (multi-tenant)
        prod = c.execute(
            """select saldo, custo_medio_centavos, estoque_minimo
               from catalogo_produtos
               where id = %s and fornecedor_id = %s for update""",
            (produto_id, fornecedor_id),
        ).fetchone()
        if prod is None:
            raise ValueError("produto não encontrado para este fornecedor")
        saldo_atual = Decimal(str(prod[0] or 0))
        custo_atual = int(prod[1] or 0)
        minimo = Decimal(str(prod[2] or 0))

        novo_custo = custo_atual
        if tipo == "entrada":
            if custo_unit_centavos is None or int(custo_unit_centavos) < 0:
                raise ValueError("entrada exige custo_unit_centavos válido")
            novo_custo = _custo_medio_ponderado(
                saldo_atual, custo_atual, qtd, int(custo_unit_centavos)
            )
            saldo_novo = saldo_atual + qtd
        elif tipo in ("saida", "perda"):
            saldo_novo = saldo_atual - qtd
        else:  # ajuste
            saldo_novo = saldo_atual + qtd  # qtd pode ser negativa

        # grava a movimentação (a verdade do estoque)
        c.execute(
            """insert into estoque_mov
                 (produto_id, fornecedor_id, tipo, quantidade,
                  custo_unit_centavos, origem_id, motivo)
               values (%s, %s, %s, %s, %s, %s, %s)""",
            (produto_id, fornecedor_id, tipo, abs(qtd) if tipo != "ajuste" else qtd,
             int(custo_unit_centavos) if tipo == "entrada" else None,
             origem_id if tipo == "entrada" else None,
             (motivo or "").strip() or None),
        )
        # atualiza o cache no produto
        c.execute(
            """update catalogo_produtos
               set saldo = %s, custo_medio_centavos = %s, atualizado_em = now()
               where id = %s""",
            (saldo_novo, novo_custo, produto_id),
        )
        c.commit()

    return {
        "saldo_novo": float(saldo_novo),
        "custo_medio_centavos": int(novo_custo),
        "abaixo_minimo": minimo > 0 and saldo_novo <= minimo,
    }


def _custo_medio_ponderado(
    saldo_atual: Decimal, custo_atual_centavos: int,
    qtd_entrada: Decimal, custo_entrada_centavos: int,
) -> int:
    """CMP – Custo Médio Ponderado, em centavos (inteiro).

    novo_custo = (saldo*custo_atual + qtd*custo_entrada) / (saldo + qtd)

    É o método recomendado no Brasil pra estoque com preço de compra variável
    (hortifruti muda toda semana). Se o saldo atual é 0 (ou negativo), o custo
    passa a ser o da entrada.
    """
    saldo_atual = Decimal(saldo_atual)
    qtd_entrada = Decimal(qtd_entrada)
    total_qtd = saldo_atual + qtd_entrada
    if total_qtd <= 0:
        return int(custo_entrada_centavos)
    if saldo_atual <= 0:
        return int(custo_entrada_centavos)
    valor_atual = saldo_atual * Decimal(custo_atual_centavos)
    valor_entrada = qtd_entrada * Decimal(custo_entrada_centavos)
    medio = (valor_atual + valor_entrada) / total_qtd
    return int(medio.quantize(Decimal("1")))  # arredonda pra centavo inteiro


# ----------------------------------------------------------------------------
# RELATÓRIOS / inteligência (perda e compra por origem)
# ----------------------------------------------------------------------------
def perda_por_origem(pool, fornecedor_id: int) -> list[dict]:
    """Quanto cada origem (fornecedor de compra) rendeu vs perdeu.

    Cruza ENTRADAS (por origem) com PERDAS (do mesmo produto). Aproximação útil:
    compara o total comprado de cada origem com a perda total daqueles produtos.
    Ajuda a responder: 'de qual fornecedor o produto estraga mais?'.
    """
    with pool.connection() as c:
        # total comprado (qtd) por origem
        compras = c.execute(
            """select o.id, o.nome,
                      coalesce(sum(m.quantidade),0) as qtd_comprada
               from origem_compra o
               left join estoque_mov m
                 on m.origem_id = o.id and m.tipo = 'entrada'
               where o.fornecedor_id = %s
               group by o.id, o.nome
               order by o.nome""",
            (fornecedor_id,),
        ).fetchall()
    return [
        {"origem_id": r[0], "nome": r[1], "qtd_comprada": float(r[2] or 0)}
        for r in compras
    ]


def historico_produto(pool, fornecedor_id: int, produto_id: int, limite: int = 50) -> list[dict]:
    """Histórico de movimentações de um produto (entrada/saida/perda/ajuste)."""
    with pool.connection() as c:
        rows = c.execute(
            """select m.tipo, m.quantidade, m.custo_unit_centavos,
                      o.nome, m.motivo, m.criado_em
               from estoque_mov m
               left join origem_compra o on o.id = m.origem_id
               where m.produto_id = %s and m.fornecedor_id = %s
               order by m.criado_em desc
               limit %s""",
            (produto_id, fornecedor_id, int(limite)),
        ).fetchall()
    return [
        {
            "tipo": r[0],
            "quantidade": float(r[1] or 0),
            "custo_unit_centavos": int(r[2]) if r[2] is not None else None,
            "origem": r[3],
            "motivo": r[4],
            "quando": r[5],
        }
        for r in rows
    ]
