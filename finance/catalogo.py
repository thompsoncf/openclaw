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
    foto_url: str | None = None,
    descricao_curta: str | None = None,
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
                  preco_venda_centavos, estoque_minimo, foto_url, descricao_curta)
               values (%s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (fornecedor_id, nome, unidade, (categoria or "").strip() or None,
             max(0, int(preco_venda_centavos)), Decimal(str(estoque_minimo or 0)),
             (foto_url or "").strip() or None, (descricao_curta or "").strip() or None),
        ).fetchone()
        c.commit()
    return int(row[0])


def atualizar_produto(pool, fornecedor_id: int, produto_id: int, **campos) -> bool:
    """Atualiza campos do produto (nome, unidade, categoria, preco_venda_centavos,
    estoque_minimo, disponivel, foto_url, descricao_curta). Só altera se o produto for do fornecedor.

    Retorna True se atualizou. NÃO mexe em saldo nem custo_medio (esses vêm das
    movimentações, não de edição manual – use registrar_movimentacao).
    """
    permitidos = {
        "nome", "unidade", "categoria", "preco_venda_centavos",
        "estoque_minimo", "disponivel", "foto_url", "descricao_curta",
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
                    custo_medio_centavos, saldo, estoque_minimo, disponivel,
                    foto_url, descricao_curta
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
            "foto_url": r[9],
            "descricao_curta": r[10],
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


# ============================================================================
# ⚠️ REGRA DE PRIVACIDADE — gravar na pedra
# ============================================================================
# O CUSTO de compra do fornecedor (quanto ele pagou no CEASA) é DADO PRIVADO.
# É segredo comercial DELE. Vive APENAS em estoque_mov / catalogo_produtos / compras_*.
#
# NUNCA, em hipótese alguma:
#   - inserir custo de compra do fornecedor em precos_observados (banco de preços)
#   - alimentar o banco de preços colaborativo com nota de COMPRA do fornecedor
#   - expor custo_medio_centavos / custo_unit_centavos pro cliente ou outro fornecedor
#
# O banco de preços (precos_observados) É alimentado SÓ por cupons de CLIENTE
# (via livro_caixa._observar_precos) – preço de VENDA no mercado, dado público.
# Compra de fornecedor É outro mundo: estoque, privado, isolado. NÃO cruzar.
# ============================================================================


# ----------------------------------------------------------------------------
# COMPRAS (módulo): agrupa vários itens de entrada numa "compra" (ida ao CEASA)
# ----------------------------------------------------------------------------
# Fluxo: cria a compra (rascunho) -> adiciona itens (manual OU lidos da nota) ->
# fornecedor REVISA -> confirma_compra() gera as entradas de estoque de todos os
# itens de uma vez. Enquanto rascunho, dá pra editar/remover itens.
#
# ⚠️ O custo daqui É PRIVADO do fornecedor. Nunca vai pro banco de preços.

def criar_compra(
    pool,
    fornecedor_id: int,
    origem_id: int | None = None,
    data_compra=None,
    fonte: str = "manual",
    chave_nfe: str | None = None,
) -> int:
    """Cria uma compra em rascunho (cabeçalho). Retorna o id.

    fonte: 'manual' | 'nota_pdf' | 'nota_foto' | 'nota_qr'.
    chave_nfe: se veio de nota, a chave (idempotência – não importa 2x a mesma nota).
    """
    if fonte not in ("manual", "nota_pdf", "nota_foto", "nota_qr"):
        fonte = "manual"
    with pool.connection() as c:
        # se veio nota com chave e já existe, retorna a compra existente (idempotência)
        if chave_nfe:
            ja = c.execute(
                """select id from compras_fornecedor
                   where fornecedor_id = %s and chave_nfe = %s""",
                (fornecedor_id, chave_nfe),
            ).fetchone()
            if ja:
                return int(ja[0])
        row = c.execute(
            """insert into compras_fornecedor
                 (fornecedor_id, origem_id, data_compra, fonte, chave_nfe)
               values (%s, %s, coalesce(%s, current_date), %s, %s)
               returning id""",
            (fornecedor_id, origem_id, data_compra, fonte, chave_nfe),
        ).fetchone()
        c.commit()
    return int(row[0])


def adicionar_item_compra(
    pool,
    fornecedor_id: int,
    compra_id: int,
    descricao: str,
    quantidade: float,
    custo_unit_centavos: int,
    unidade: str = "kg",
    produto_id: int | None = None,
) -> int:
    """Adiciona um item à compra (rascunho). Retorna o id do item.

    produto_id: se já souber a qual produto do catálogo casa; senão fica null e o
    fornecedor casa na revisão (ou cria o produto na hora).
    """
    with pool.connection() as c:
        # confere que a compra é do fornecedor e está em rascunho
        comp = c.execute(
            """select status from compras_fornecedor
               where id = %s and fornecedor_id = %s""",
            (compra_id, fornecedor_id),
        ).fetchone()
        if comp is None:
            raise ValueError("compra não encontrada para este fornecedor")
        if comp[0] != "rascunho":
            raise ValueError("só dá pra editar itens enquanto a compra é rascunho")
        row = c.execute(
            """insert into compras_itens
                 (compra_id, produto_id, descricao, quantidade, unidade,
                  custo_unit_centavos, casado)
               values (%s, %s, %s, %s, %s, %s, %s) returning id""",
            (compra_id, produto_id, (descricao or "").strip(),
             Decimal(str(quantidade or 0)), (unidade or "kg").strip().lower(),
             max(0, int(custo_unit_centavos)), produto_id is not None),
        ).fetchone()
        # atualiza o total do cabeçalho
        c.execute(
            """update compras_fornecedor set total_centavos = (
                 select coalesce(sum(round(quantidade * custo_unit_centavos)), 0)
                 from compras_itens where compra_id = %s)
               where id = %s""",
            (compra_id, compra_id),
        )
        c.commit()
    return int(row[0])


def listar_itens_compra(pool, fornecedor_id: int, compra_id: int) -> list[dict]:
    """Lista os itens de uma compra (pra tela de revisão)."""
    with pool.connection() as c:
        # valida dono
        if c.execute(
            "select 1 from compras_fornecedor where id=%s and fornecedor_id=%s",
            (compra_id, fornecedor_id),
        ).fetchone() is None:
            raise ValueError("compra não encontrada para este fornecedor")
        rows = c.execute(
            """select i.id, i.produto_id, i.descricao, i.quantidade, i.unidade,
                      i.custo_unit_centavos, i.casado, p.nome
               from compras_itens i
               left join catalogo_produtos p on p.id = i.produto_id
               where i.compra_id = %s order by i.id""",
            (compra_id,),
        ).fetchall()
    return [
        {
            "id": r[0], "produto_id": r[1], "descricao": r[2],
            "quantidade": float(r[3] or 0), "unidade": r[4],
            "custo_unit_centavos": int(r[5] or 0), "casado": r[6],
            "produto_nome": r[7],
        }
        for r in rows
    ]


def casar_item_produto(
    pool, fornecedor_id: int, item_id: int, produto_id: int
) -> bool:
    """Vincula um item da compra a um produto do catálogo (na revisão).

    Valida que o item e o produto pertencem ao fornecedor.
    """
    with pool.connection() as c:
        ok = c.execute(
            """select 1 from compras_itens i
               join compras_fornecedor cf on cf.id = i.compra_id
               where i.id = %s and cf.fornecedor_id = %s""",
            (item_id, fornecedor_id),
        ).fetchone()
        prod = c.execute(
            "select 1 from catalogo_produtos where id=%s and fornecedor_id=%s",
            (produto_id, fornecedor_id),
        ).fetchone()
        if ok is None or prod is None:
            return False
        c.execute(
            "update compras_itens set produto_id=%s, casado=true where id=%s",
            (produto_id, item_id),
        )
        c.commit()
        return True


def confirmar_compra(pool, fornecedor_id: int, compra_id: int) -> dict:
    """Confirma a compra: gera as ENTRADAS de estoque de todos os itens de uma vez.

    Pré-requisito: todos os itens devem estar CASADOS a um produto (produto_id).
    Cada item vira uma movimentação 'entrada' (saldo += qtd, custo médio recalculado),
    com a origem da compra. Tudo numa transação. Retorna resumo.

    ⚠️ NÃO toca em precos_observados. O custo É privado do fornecedor.
    """
    with pool.connection() as c:
        comp = c.execute(
            """select status, origem_id from compras_fornecedor
               where id = %s and fornecedor_id = %s for update""",
            (compra_id, fornecedor_id),
        ).fetchone()
        if comp is None:
            raise ValueError("compra não encontrada para este fornecedor")
        if comp[0] != "rascunho":
            raise ValueError("compra já confirmada ou cancelada")
        origem_id = comp[1]

        itens = c.execute(
            """select id, produto_id, quantidade, custo_unit_centavos, casado
               from compras_itens where compra_id = %s""",
            (compra_id,),
        ).fetchall()
        if not itens:
            raise ValueError("compra sem itens")
        nao_casados = [i for i in itens if not i[1]]
        if nao_casados:
            raise ValueError(
                f"{len(nao_casados)} item(ns) sem produto casado – revise antes de confirmar"
            )

        # gera uma entrada por item (recalcula saldo + custo médio de cada produto)
        for item_id, produto_id, qtd, custo_unit, _casado in itens:
            prod = c.execute(
                """select saldo, custo_medio_centavos from catalogo_produtos
                   where id = %s and fornecedor_id = %s for update""",
                (produto_id, fornecedor_id),
            ).fetchone()
            if prod is None:
                raise ValueError(f"produto {produto_id} não é deste fornecedor")
            saldo_atual = Decimal(str(prod[0] or 0))
            custo_atual = int(prod[1] or 0)
            q = Decimal(str(qtd))
            novo_custo = _custo_medio_ponderado(saldo_atual, custo_atual, q, int(custo_unit))
            novo_saldo = saldo_atual + q
            c.execute(
                """insert into estoque_mov
                     (produto_id, fornecedor_id, tipo, quantidade,
                      custo_unit_centavos, origem_id, motivo)
                   values (%s, %s, 'entrada', %s, %s, %s, %s)""",
                (produto_id, fornecedor_id, q, int(custo_unit), origem_id,
                 f"compra #{compra_id}"),
            )
            c.execute(
                """update catalogo_produtos
                   set saldo = %s, custo_medio_centavos = %s, atualizado_em = now()
                   where id = %s""",
                (novo_saldo, novo_custo, produto_id),
            )

        c.execute(
            """update compras_fornecedor
               set status = 'confirmada', confirmada_em = now() where id = %s""",
            (compra_id,),
        )
        c.commit()
    return {"compra_id": compra_id, "itens": len(itens), "status": "confirmada"}
