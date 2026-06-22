"""Assinaturas do cliente (Fase 3, loja).

O cliente assina uma cesta de um fornecedor: tamanho (preço fixo) + frequência +
restrições (produtos que nunca quer). Reusa a tabela vinculos (Fase 0, modelo
cliente-cêntrico) como o elo cliente–fornecedor.

Fluxo da loja (guest checkout — best practice):
1. cliente monta a escolha SEM conta (guardada na sessão pela camada web).
2. cria a conta no final — a camada web chama criar_assinatura com a escolha.
3. assinatura nasce 'aguardando_pagamento' (Asaas entra na Fase 5; aqui não cobra).

Tudo monetário em CENTAVOS. Funções validam o dono (multi-tenant): o cliente só
mexe nas próprias assinaturas; o fornecedor só vê as que são dele.
"""
from __future__ import annotations


def criar_assinatura(
    pool,
    cliente_id: int,
    fornecedor_id: int,
    tamanho_id: int,
    frequencia: str = "semanal",
    restricoes_produto_ids: list[int] | None = None,
) -> dict:
    """Cria a assinatura + o vínculo cliente-cêntrico + as restrições. Transação única.

    Valida que o tamanho pertence ao fornecedor. Pega o preço do tamanho como snapshot
    (pra histórico, mesmo que o tamanho mude de preço depois).
    Status inicial: 'aguardando_pagamento' (a Fase 5 ativa via Asaas).
    Retorna {assinatura_id, vinculo_id, preco_centavos, status}.
    """
    if frequencia not in ("semanal", "quinzenal", "mensal"):
        frequencia = "semanal"
    restricoes_produto_ids = restricoes_produto_ids or []

    with pool.connection() as c:
        # valida o tamanho e pega o preço (snapshot) + confere o fornecedor
        tam = c.execute(
            """select preco_centavos from cesta_tamanhos
               where id = %s and fornecedor_id = %s and ativo""",
            (tamanho_id, fornecedor_id),
        ).fetchone()
        if tam is None:
            raise ValueError("tamanho de cesta inválido para este fornecedor")
        preco = int(tam[0] or 0)

        # cria/reusa o vínculo cliente-cêntrico (Fase 0)
        vinc = c.execute(
            """select id from vinculos
               where cliente_id = %s and fornecedor_id = %s and status = 'ativo'""",
            (cliente_id, fornecedor_id),
        ).fetchone()
        if vinc:
            vinculo_id = int(vinc[0])
        else:
            # nicho do fornecedor (pro vínculo)
            nrow = c.execute(
                "select nicho_id from contas where id = %s", (fornecedor_id,)
            ).fetchone()
            nicho_id = nrow[0] if nrow else None
            vrow = c.execute(
                """insert into vinculos
                     (cliente_id, fornecedor_id, nicho_id, tipo, status, frequencia)
                   values (%s, %s, %s, 'assinatura', 'ativo', %s) returning id""",
                (cliente_id, fornecedor_id, nicho_id, frequencia),
            ).fetchone()
            vinculo_id = int(vrow[0])

        # cria a assinatura
        arow = c.execute(
            """insert into assinaturas
                 (cliente_id, fornecedor_id, tamanho_id, vinculo_id,
                  frequencia, preco_centavos, status)
               values (%s, %s, %s, %s, %s, %s, 'aguardando_pagamento')
               returning id""",
            (cliente_id, fornecedor_id, tamanho_id, vinculo_id, frequencia, preco),
        ).fetchone()
        assinatura_id = int(arow[0])

        # restrições (produtos que nunca quer) — valida que são do fornecedor
        for pid in restricoes_produto_ids:
            ok = c.execute(
                "select 1 from catalogo_produtos where id=%s and fornecedor_id=%s",
                (pid, fornecedor_id),
            ).fetchone()
            if ok:
                c.execute(
                    """insert into assinatura_restricoes (assinatura_id, produto_id)
                       values (%s, %s) on conflict do nothing""",
                    (assinatura_id, pid),
                )
        c.commit()

    return {
        "assinatura_id": assinatura_id,
        "vinculo_id": vinculo_id,
        "preco_centavos": preco,
        "status": "aguardando_pagamento",
    }


def listar_assinaturas_cliente(pool, cliente_id: int) -> list[dict]:
    """Assinaturas do cliente (pra ele ver/gerenciar)."""
    with pool.connection() as c:
        rows = c.execute(
            """select a.id, a.fornecedor_id, ct.nome, a.frequencia, a.status,
                      a.preco_centavos, co.nome, a.tamanho_id
               from assinaturas a
               join cesta_tamanhos ct on ct.id = a.tamanho_id
               join contas co on co.id = a.fornecedor_id
               where a.cliente_id = %s
               order by a.criado_em desc""",
            (cliente_id,),
        ).fetchall()
    return [
        {
            "id": r[0], "fornecedor_id": r[1], "tamanho_nome": r[2],
            "frequencia": r[3], "status": r[4], "preco_centavos": int(r[5] or 0),
            "fornecedor_nome": r[6], "tamanho_id_atual": r[7],
        }
        for r in rows
    ]


def listar_assinaturas_fornecedor(pool, fornecedor_id: int) -> list[dict]:
    """Assinaturas que o fornecedor recebe (pro painel dele)."""
    with pool.connection() as c:
        rows = c.execute(
            """select a.id, a.cliente_id, ct.nome, a.frequencia, a.status,
                      a.preco_centavos, co.nome
               from assinaturas a
               join cesta_tamanhos ct on ct.id = a.tamanho_id
               join contas co on co.id = a.cliente_id
               where a.fornecedor_id = %s
               order by a.criado_em desc""",
            (fornecedor_id,),
        ).fetchall()
    return [
        {
            "id": r[0], "cliente_id": r[1], "tamanho_nome": r[2],
            "frequencia": r[3], "status": r[4], "preco_centavos": int(r[5] or 0),
            "cliente_nome": r[6],
        }
        for r in rows
    ]


def obter_restricoes(pool, assinatura_id: int) -> list[int]:
    """IDs dos produtos restritos de uma assinatura."""
    with pool.connection() as c:
        rows = c.execute(
            "select produto_id from assinatura_restricoes where assinatura_id = %s",
            (assinatura_id,),
        ).fetchall()
    return [int(r[0]) for r in rows]


def alterar_status(pool, cliente_id: int, assinatura_id: int, novo_status: str) -> bool:
    """Cliente pausa/cancela/reativa a própria assinatura.

    novo_status: 'ativa' | 'pausada' | 'cancelada'. Valida que é do cliente.
    """
    if novo_status not in ("ativa", "pausada", "cancelada"):
        raise ValueError("status inválido")
    extra = ""
    if novo_status == "ativa":
        extra = ", ativada_em = now()"
    elif novo_status == "cancelada":
        extra = ", cancelada_em = now()"
    with pool.connection() as c:
        cur = c.execute(
            f"""update assinaturas set status = %s {extra}
                where id = %s and cliente_id = %s""",
            (novo_status, assinatura_id, cliente_id),
        )
        c.commit()
        return cur.rowcount > 0


def ativar_pos_pagamento(pool, assinatura_id: int) -> bool:
    """Marca a assinatura como ativa após o pagamento confirmar (Fase 5, Asaas).

    Chamada pelo webhook do Asaas quando a autorização do Pix Automático confirma.
    """
    with pool.connection() as c:
        cur = c.execute(
            """update assinaturas set status = 'ativa', ativada_em = now()
               where id = %s and status = 'aguardando_pagamento'""",
            (assinatura_id,),
        )
        c.commit()
        return cur.rowcount > 0


def trocar_tamanho(pool, cliente_id: int, assinatura_id: int, novo_tamanho_id: int) -> dict:
    """Troca o tamanho da assinatura (ex: Pequena -> Grande).

    Vale a partir da PRÓXIMA entrega — não mexe em cesta já montada desta semana.
    Valida que a assinatura é do cliente e que o tamanho é do mesmo fornecedor.
    Atualiza o preço (snapshot) pro novo tamanho. Retorna {ok, preco_centavos} ou {ok: False, erro}.
    """
    with pool.connection() as c:
        # confere que a assinatura é do cliente e pega o fornecedor
        a = c.execute(
            """select fornecedor_id from assinaturas
               where id = %s and cliente_id = %s""",
            (assinatura_id, cliente_id),
        ).fetchone()
        if a is None:
            return {"ok": False, "erro": "assinatura não encontrada"}
        fornecedor_id = a[0]
        # confere que o novo tamanho é do mesmo fornecedor e pega o preço
        t = c.execute(
            """select preco_centavos from cesta_tamanhos
               where id = %s and fornecedor_id = %s and ativo""",
            (novo_tamanho_id, fornecedor_id),
        ).fetchone()
        if t is None:
            return {"ok": False, "erro": "tamanho inválido para este fornecedor"}
        novo_preco = int(t[0] or 0)
        # atualiza o tamanho e o preço (vale na próxima; cesta_semana já montada não muda)
        c.execute(
            """update assinaturas set tamanho_id = %s, preco_centavos = %s
               where id = %s and cliente_id = %s""",
            (novo_tamanho_id, novo_preco, assinatura_id, cliente_id),
        )
        c.commit()
    return {"ok": True, "preco_centavos": novo_preco}
