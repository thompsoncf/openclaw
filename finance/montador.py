"""O montador da cesta (Fase 4) — o "cérebro" do Zaq Fornecedor.

Dada uma assinatura, monta a cesta da semana por REGRAS:
- usa o ESQUELETO do tamanho (ex: 2 frutas + 2 legumes + 1 verdura + 1 tempero)
- escolhe produtos do ESTOQUE do fornecedor, priorizando MAIOR SALDO (gira o que
  está sobrando — reduz perda, o maior risco do hortifruti)
- respeita as RESTRIÇÕES do cliente (produtos que ele nunca quer)
- enche até o CUSTO bater a margem alvo do fornecedor (custo <= margem_alvo% do preço),
  garantindo a margem. O preço que o cliente paga É FIXO (do tamanho).

Decisões de negócio (fechadas com o dono):
- Por regras (não IA ainda) — barato, previsível, testável. IA evolui depois.
- Prioriza maior estoque (gira o que sobra).
- Margem alvo configurável por fornecedor (campo contas.margem_alvo_pct, default 60%).
- Nunca quebra: se faltar produto num grupo, pega o que dá e sinaliza.

⚠️ PRIVACIDADE: o custo é do fornecedor. Fica em cesta_itens/cesta_semana (privado).
NUNCA exposto ao cliente nem ao banco de preços.

Tudo monetário em CENTAVOS. Quantidade em numeric.
"""
from __future__ import annotations

from decimal import Decimal


# quantidade base por porção, por unidade de medida (ponto de partida)
# o montador ajusta pra caber na margem, mas começa daqui.
_QTD_BASE = {
    "kg": Decimal("0.5"),       # meio quilo por porção
    "duzia": Decimal("1"),       # uma dúzia
    "unidade": Decimal("1"),     # uma unidade
    "maco": Decimal("1"),        # um maço
    "bandeja": Decimal("1"),
    "litro": Decimal("1"),
    "pacote": Decimal("1"),
}


def _qtd_base(unidade: str) -> Decimal:
    return _QTD_BASE.get((unidade or "kg").lower(), Decimal("1"))


def montar_cesta(
    pool,
    assinatura_id: int,
    data_entrega=None,
    persistir: bool = True,
) -> dict:
    """Monta a cesta da semana de uma assinatura. Retorna o resultado.

    Se persistir=True, grava em cesta_semana + cesta_itens (status 'sugerida').
    Se persistir=False, só calcula e retorna (útil pra preview/teste).

    Retorna:
      {
        "assinatura_id", "preco_centavos", "custo_total_centavos",
        "margem_alvo_pct", "custo_limite_centavos",
        "itens": [{produto_id, nome, grupo, quantidade, unidade,
                   custo_unit_centavos, preco_unit_centavos}],
        "avisos": [...],   # ex: "grupo frutas: só achei 1 de 2"
        "cesta_id" (se persistiu)
      }
    """
    with pool.connection() as c:
        # 1. dados da assinatura + tamanho (esqueleto + preço) + fornecedor + margem
        a = c.execute(
            """select a.cliente_id, a.fornecedor_id, a.tamanho_id, a.preco_centavos,
                      t.qtd_frutas, t.qtd_legumes, t.qtd_verduras, t.qtd_temperos,
                      coalesce(co.margem_alvo_pct, 60)
               from assinaturas a
               join cesta_tamanhos t on t.id = a.tamanho_id
               join contas co on co.id = a.fornecedor_id
               where a.id = %s""",
            (assinatura_id,),
        ).fetchone()
        if a is None:
            raise ValueError("assinatura não encontrada")
        (cliente_id, fornecedor_id, tamanho_id, preco_centavos,
         q_frutas, q_legumes, q_verduras, q_temperos, margem_alvo) = a
        preco_centavos = int(preco_centavos or 0)
        margem_alvo = Decimal(str(margem_alvo or 60))
        # custo pode ir até margem_alvo% do preço
        custo_limite = int(Decimal(preco_centavos) * margem_alvo / Decimal(100))

        # 2. restrições do cliente (produtos que nunca quer)
        restritos = {
            int(r[0]) for r in c.execute(
                "select produto_id from assinatura_restricoes where assinatura_id=%s",
                (assinatura_id,),
            ).fetchall()
        }

        # 3. produtos disponíveis do fornecedor, por grupo, ordenados por maior saldo
        prods = c.execute(
            """select id, nome, categoria, unidade, saldo,
                      custo_medio_centavos, preco_venda_centavos
               from catalogo_produtos
               where fornecedor_id = %s and ativo and disponivel and saldo > 0
               order by saldo desc""",
            (fornecedor_id,),
        ).fetchall()

    # organiza por grupo (categoria), tirando os restritos
    grupos: dict[str, list] = {"fruta": [], "legume": [], "verdura": [], "tempero": []}
    for p in prods:
        pid, nome, categoria, unidade, saldo, custo, preco = p
        if pid in restritos:
            continue
        cat = _normaliza_grupo(categoria)
        if cat in grupos:
            grupos[cat].append({
                "produto_id": pid, "nome": nome, "grupo": cat,
                "unidade": unidade, "saldo": float(saldo or 0),
                "custo_unit_centavos": int(custo or 0),
                "preco_unit_centavos": int(preco or 0),
            })

    # 4. monta: pega N de cada grupo (do esqueleto), priorizando maior saldo (já ordenado)
    pedido = {"fruta": q_frutas or 0, "legume": q_legumes or 0,
              "verdura": q_verduras or 0, "tempero": q_temperos or 0}
    itens = []
    avisos = []
    for grupo, qtd_pedida in pedido.items():
        disponiveis = grupos[grupo]
        if qtd_pedida > 0 and not disponiveis:
            avisos.append(f"grupo {grupo}: sem produto disponível no estoque")
            continue
        escolhidos = disponiveis[:qtd_pedida]  # os de maior saldo
        if len(escolhidos) < qtd_pedida:
            avisos.append(
                f"grupo {grupo}: só achei {len(escolhidos)} de {qtd_pedida} pedidos"
            )
        for prod in escolhidos:
            qtd = _qtd_base(prod["unidade"])
            itens.append({**prod, "quantidade": qtd})

    # 5. ajusta quantidades pra caber no custo limite (margem garantida)
    #    se o custo passou do limite, reduz proporcionalmente; se sobrou folga,
    #    aumenta um pouco (sem estourar o saldo do estoque).
    itens, custo_total = _ajustar_ao_custo(itens, custo_limite)

    resultado = {
        "assinatura_id": assinatura_id,
        "fornecedor_id": fornecedor_id,
        "cliente_id": cliente_id,
        "preco_centavos": preco_centavos,
        "custo_total_centavos": custo_total,
        "margem_alvo_pct": float(margem_alvo),
        "custo_limite_centavos": custo_limite,
        "itens": itens,
        "avisos": avisos,
    }

    if persistir:
        resultado["cesta_id"] = _persistir_cesta(pool, resultado, data_entrega)
    return resultado


def _normaliza_grupo(categoria: str | None) -> str:
    """Mapeia a categoria do produto pro grupo da cesta."""
    cat = (categoria or "").strip().lower()
    if cat in ("fruta", "frutas"):
        return "fruta"
    if cat in ("legume", "legumes"):
        return "legume"
    if cat in ("verdura", "verduras", "folha", "folhas"):
        return "verdura"
    if cat in ("tempero", "temperos", "erva", "ervas"):
        return "tempero"
    return "outro"


def _ajustar_ao_custo(itens: list[dict], custo_limite: int) -> tuple[list[dict], int]:
    """Ajusta as quantidades pra o custo total ficar <= custo_limite.

    Começa com a qtd base de cada item. Calcula o custo. Se passou do limite,
    reduz proporcionalmente. (Aumentar pra preencher folga fica pra evolução —
    por ora, garantir a margem é o que importa: nunca passar do limite.)
    """
    if not itens:
        return itens, 0

    def custo_de(its):
        return sum(
            int(Decimal(str(i["quantidade"])) * Decimal(i["custo_unit_centavos"]))
            for i in its
        )

    custo = custo_de(itens)
    if custo_limite > 0 and custo > custo_limite:
        # reduz tudo proporcionalmente pra caber
        fator = Decimal(custo_limite) / Decimal(custo)
        for i in itens:
            nova = (Decimal(str(i["quantidade"])) * fator).quantize(Decimal("0.001"))
            # não deixa zerar nem passar do saldo
            nova = max(Decimal("0.001"), min(nova, Decimal(str(i["saldo"]))))
            i["quantidade"] = float(nova)
        custo = custo_de(itens)

    return itens, int(custo)


def _persistir_cesta(pool, resultado: dict, data_entrega) -> int:
    """Grava a cesta montada em cesta_semana + cesta_itens (status 'sugerida')."""
    with pool.connection() as c:
        row = c.execute(
            """insert into cesta_semana
                 (assinatura_id, fornecedor_id, cliente_id, data_entrega,
                  status, preco_centavos, custo_total_centavos)
               values (%s, %s, %s, %s, 'sugerida', %s, %s) returning id""",
            (resultado["assinatura_id"], resultado["fornecedor_id"],
             resultado["cliente_id"], data_entrega,
             resultado["preco_centavos"], resultado["custo_total_centavos"]),
        ).fetchone()
        cesta_id = int(row[0])
        for it in resultado["itens"]:
            c.execute(
                """insert into cesta_itens
                     (cesta_id, produto_id, grupo, quantidade,
                      custo_unit_centavos, preco_unit_centavos)
                   values (%s, %s, %s, %s, %s, %s)""",
                (cesta_id, it["produto_id"], it["grupo"],
                 Decimal(str(it["quantidade"])),
                 it["custo_unit_centavos"], it["preco_unit_centavos"]),
            )
        c.commit()
    return cesta_id


def listar_itens_cesta(pool, cesta_id: int) -> list[dict]:
    """Lista os itens de uma cesta montada (pro fornecedor/cliente ver)."""
    with pool.connection() as c:
        rows = c.execute(
            """select ci.produto_id, p.nome, ci.grupo, ci.quantidade, p.unidade,
                      ci.preco_unit_centavos
               from cesta_itens ci
               join catalogo_produtos p on p.id = ci.produto_id
               where ci.cesta_id = %s order by ci.grupo""",
            (cesta_id,),
        ).fetchall()
    return [
        {
            "produto_id": r[0], "nome": r[1], "grupo": r[2],
            "quantidade": float(r[3] or 0), "unidade": r[4],
            "preco_unit_centavos": int(r[5] or 0),
        }
        for r in rows
    ]
