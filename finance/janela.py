"""A janela semanal (Fase 6) — o ciclo recorrente da assinatura.

Toda semana, pra cada assinatura ativa:
1. ABRE a janela: monta a cesta (chama o montador) → status 'sugerida'.
2. AVISA o cliente (WhatsApp com link pra tela web de ajuste).
3. O cliente AJUSTA na web (tira/troca item) ou não faz nada.
4. LEMBRETE antes de fechar (2º aviso).
5. FECHA a janela: modo confiança — quem não mexeu, confirma sozinho e vai.
   Status vira 'confirmada' → (cobrança Asaas entra na Fase 5) → entrega.

Decisões (fechadas com o dono):
- Modo confiança: não responder = cesta confirmada (sem fricção; a maioria não mexe).
- Ajuste por LINK→WEB (não texto livre no WhatsApp). Seguro, sem ambiguidade.
- 2 avisos: 1 quando monta + 1 lembrete antes de fechar.
- Testar MANUAL primeiro; o Asaas (cobrança) é o último passo, ligado depois.

A janela é orquestração — reusa o montador (Fase 4) e o envio de WhatsApp existente.
"""
from __future__ import annotations

from datetime import date, timedelta


# ----------------------------------------------------------------------------
# ABRIR a janela: montar as cestas da semana das assinaturas ativas
# ----------------------------------------------------------------------------
def abrir_janela_semana(pool, data_entrega=None, apenas_assinatura_id: int | None = None) -> dict:
    """Monta a cesta da semana pra cada assinatura ATIVA (ou uma específica, pra teste).

    Chama o montador (Fase 4) pra cada uma. As cestas nascem 'sugerida'.
    Retorna {montadas, erros, cestas:[...]}.

    data_entrega: a data da entrega dessa semana (default: daqui a 7 dias).
    apenas_assinatura_id: se passado, monta só essa (útil pra testar sem afetar as outras).
    """
    from finance import montador

    if data_entrega is None:
        data_entrega = date.today() + timedelta(days=7)

    with pool.connection() as c:
        sql = """select id from assinaturas where status = 'ativa'"""
        params = ()
        if apenas_assinatura_id is not None:
            sql = """select id from assinaturas where id = %s"""
            params = (apenas_assinatura_id,)
        assinaturas = [int(r[0]) for r in c.execute(sql, params).fetchall()]

    montadas, erros, cestas = 0, [], []
    for assinatura_id in assinaturas:
        try:
            # evita montar 2x a mesma cesta pra mesma data
            if _ja_tem_cesta(pool, assinatura_id, data_entrega):
                continue
            r = montador.montar_cesta(pool, assinatura_id, data_entrega=data_entrega, persistir=True)
            montadas += 1
            cestas.append({
                "cesta_id": r.get("cesta_id"),
                "assinatura_id": assinatura_id,
                "itens": len(r["itens"]),
                "avisos": r["avisos"],
            })
        except Exception as e:
            erros.append({"assinatura_id": assinatura_id, "erro": str(e)})

    return {"montadas": montadas, "erros": erros, "cestas": cestas, "data_entrega": str(data_entrega)}


def _ja_tem_cesta(pool, assinatura_id: int, data_entrega) -> bool:
    """Evita duplicar a cesta da mesma assinatura pra mesma data de entrega."""
    with pool.connection() as c:
        r = c.execute(
            """select 1 from cesta_semana
               where assinatura_id = %s and data_entrega = %s
                 and status not in ('cancelada')""",
            (assinatura_id, data_entrega),
        ).fetchone()
    return r is not None


# ----------------------------------------------------------------------------
# A cesta da semana (pro cliente ver/ajustar na tela web)
# ----------------------------------------------------------------------------
def obter_cesta(pool, cesta_id: int, cliente_id: int | None = None) -> dict | None:
    """Retorna a cesta (cabeçalho + itens) pra mostrar na tela de ajuste.

    Se cliente_id for passado, valida que a cesta é dele (segurança).
    NUNCA inclui o custo (privado do fornecedor) — só o que o cliente pode ver.
    """
    with pool.connection() as c:
        cab = c.execute(
            """select cs.id, cs.assinatura_id, cs.status, cs.preco_centavos,
                      cs.data_entrega, cs.cliente_id, co.nome
               from cesta_semana cs
               join contas co on co.id = cs.fornecedor_id
               where cs.id = %s""",
            (cesta_id,),
        ).fetchone()
        if cab is None:
            return None
        if cliente_id is not None and int(cab[5]) != int(cliente_id):
            return None
        itens = c.execute(
            """select ci.id, p.nome, ci.grupo, ci.quantidade, p.unidade
               from cesta_itens ci
               join catalogo_produtos p on p.id = ci.produto_id
               where ci.cesta_id = %s order by ci.grupo""",
            (cesta_id,),
        ).fetchall()
    return {
        "id": cab[0], "assinatura_id": cab[1], "status": cab[2],
        "preco_centavos": int(cab[3] or 0), "data_entrega": cab[4],
        "fornecedor_nome": cab[6],
        "itens": [
            {"item_id": r[0], "nome": r[1], "grupo": r[2],
             "quantidade": float(r[3] or 0), "unidade": r[4]}
            for r in itens
        ],
    }


def remover_item(pool, cesta_id: int, item_id: int, cliente_id: int) -> bool:
    """Cliente tira um item da cesta da semana (ajuste). Só se a cesta for dele e
    ainda estiver aberta (sugerida/em_ajuste). Marca a cesta como 'em_ajuste'.
    """
    with pool.connection() as c:
        # valida dono + status aberto
        ok = c.execute(
            """select 1 from cesta_semana
               where id = %s and cliente_id = %s and status in ('sugerida','em_ajuste')""",
            (cesta_id, cliente_id),
        ).fetchone()
        if ok is None:
            return False
        cur = c.execute(
            "delete from cesta_itens where id = %s and cesta_id = %s",
            (item_id, cesta_id),
        )
        c.execute(
            "update cesta_semana set status = 'em_ajuste' where id = %s",
            (cesta_id,),
        )
        c.commit()
        return cur.rowcount > 0


def confirmar_cesta(pool, cesta_id: int, cliente_id: int | None = None) -> bool:
    """Confirma a cesta (cliente clica confirmar, ou modo confiança fecha sozinha).

    Status sugerida/em_ajuste → confirmada. Se cliente_id passado, valida dono.
    A cobrança (Asaas) será disparada na Fase 5, a partir de 'confirmada'.
    """
    with pool.connection() as c:
        if cliente_id is not None:
            ok = c.execute(
                "select 1 from cesta_semana where id=%s and cliente_id=%s",
                (cesta_id, cliente_id),
            ).fetchone()
            if ok is None:
                return False
        cur = c.execute(
            """update cesta_semana set status = 'confirmada'
               where id = %s and status in ('sugerida','em_ajuste')""",
            (cesta_id,),
        )
        c.commit()
        return cur.rowcount > 0


# ----------------------------------------------------------------------------
# FECHAR a janela: modo confiança (quem não mexeu, confirma sozinho)
# ----------------------------------------------------------------------------
def fechar_janela(pool, data_entrega) -> dict:
    """Fecha a janela da semana: todas as cestas 'sugerida'/'em_ajuste' daquela data
    viram 'confirmada' (modo confiança — quem não confirmou, vai assim mesmo).

    Roda quando a janela de ajuste termina. Retorna {confirmadas}.
    A cobrança (Asaas) será disparada depois, a partir das 'confirmada' (Fase 5).
    """
    with pool.connection() as c:
        cur = c.execute(
            """update cesta_semana set status = 'confirmada'
               where data_entrega = %s and status in ('sugerida','em_ajuste')""",
            (data_entrega,),
        )
        c.commit()
        confirmadas = cur.rowcount
    return {"confirmadas": confirmadas, "data_entrega": str(data_entrega)}


# ----------------------------------------------------------------------------
# AVISOS (monta o texto; o envio reusa a função de WhatsApp existente)
# ----------------------------------------------------------------------------
def montar_aviso_cesta(cesta: dict, link_ajuste: str, eh_lembrete: bool = False) -> str:
    """Monta o texto do aviso de WhatsApp (1º aviso ou lembrete).

    O envio em si é feito pela camada web (reusa _responder_whatsapp do app.py),
    passando este texto. Aqui só montamos a mensagem.
    """
    nome_forn = cesta.get("fornecedor_nome", "seu fornecedor")
    n_itens = len(cesta.get("itens", []))
    preco = cesta.get("preco_centavos", 0) / 100
    if eh_lembrete:
        return (
            f"⏰ Última chance de ajustar sua cesta de {nome_forn}!\n"
            f"{n_itens} itens · R$ {preco:.2f}\n"
            f"Se estiver tudo certo, não precisa fazer nada — sua cesta vai automático. 🧺\n"
            f"Quer ajustar? {link_ajuste}"
        )
    return (
        f"🧺 Sua cesta da semana de {nome_forn} está pronta!\n"
        f"{n_itens} itens · R$ {preco:.2f}\n"
        f"Dê uma olhada e ajuste se quiser: {link_ajuste}\n"
        f"Não precisa fazer nada se estiver tudo certo — ela vai automático. 👍"
    )


def cestas_pendentes_aviso(pool, data_entrega, eh_lembrete: bool = False) -> list[dict]:
    """Lista as cestas que precisam de aviso (pra a camada web enviar o WhatsApp).

    1º aviso: cestas recém-montadas ('sugerida').
    Lembrete: cestas ainda abertas ('sugerida' ou 'em_ajuste') perto de fechar.
    Retorna dados pra montar e enviar a mensagem (inclui o whatsapp do cliente).
    """
    status_alvo = ("sugerida", "em_ajuste") if eh_lembrete else ("sugerida",)
    placeholders = ",".join(["%s"] * len(status_alvo))
    with pool.connection() as c:
        rows = c.execute(
            f"""select cs.id, cs.cliente_id, cs.preco_centavos, co.nome,
                       cl.whatsapp
                from cesta_semana cs
                join contas co on co.id = cs.fornecedor_id
                join contas cl on cl.id = cs.cliente_id
                where cs.data_entrega = %s and cs.status in ({placeholders})""",
            (data_entrega, *status_alvo),
        ).fetchall()
    return [
        {
            "cesta_id": r[0], "cliente_id": r[1],
            "preco_centavos": int(r[2] or 0), "fornecedor_nome": r[3],
            "whatsapp": r[4],
        }
        for r in rows
    ]
