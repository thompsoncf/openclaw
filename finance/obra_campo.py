"""O campo: o que o mestre de obras faz pelo app /obra (migração 550).

Desenho aprovado pelo dono em 02/10/2026 (docs/mockups/obras_mapa_3d.html,
seção 4, "segue as recomendações"):
  3. o mestre MARCA DIRETO — com "desfazer" e aviso ao dono. Aprovação a cada
     gesto mata o uso no canteiro;
  4. o app não mostra NENHUM valor em dinheiro: só quantidade, etapa, foto e o
     quadro. Dinheiro é do painel.

CADA GESTO VIRA UMA LINHA EM `obra_campo`: é o "aviso ao dono" (a lista "Do
campo" na tela de Obras) e o que o "desfazer" desfaz. O gesto em si usa os
mesmos motores do painel e do WhatsApp — `obras.marcar_etapa`,
`obra_material.mover`, `obra_fotos.guardar` —, então a etapa marcada pelo
mestre conta no andamento, no mapa e no lembrete de segunda como qualquer outra.

QUEM VÊ O QUÊ: o mestre vê só as obras em que ele é o `mestre_id`. Dono e
gestor (que também têm a capacidade `campo`) veem todas — servem pra conferir
o app e pra cobrir o mestre no dia em que ele faltar.
"""
from __future__ import annotations

import json
from decimal import Decimal

from . import obras as _ob

#: obra que já saiu do canteiro não aparece no app
_FORA_DO_CANTEIRO = ("vendida", "entregue", "arquivada")


# ── quem vê o quê ─────────────────────────────────────────────────────────
def obras_do(pool, conta_id: int, papel: str, membro_id: int | None) -> list[dict]:
    """As obras que esta pessoa vê no app, sem custo (`com_custos=False`: o app
    não mostra dinheiro, e nem calcula)."""
    todas = [o for o in _ob.listar_obras(pool, conta_id, com_custos=False)
             if o["status"] not in _FORA_DO_CANTEIRO]
    if papel != "mestre":
        return todas
    if not membro_id:
        return []
    with pool.connection() as c:
        minhas = {r[0] for r in c.execute(
            "select id from obras where conta_id=%s and mestre_id=%s",
            (conta_id, membro_id)).fetchall()}
    return [o for o in todas if o["id"] in minhas]


def pode(pool, conta_id: int, papel: str, membro_id: int | None, obra_id: int) -> bool:
    return any(o["id"] == obra_id for o in obras_do(pool, conta_id, papel, membro_id))


def definir_mestre(pool, conta_id: int, obra_id: int, membro_id: int | None) -> None:
    """O painel escolhe o mestre da obra. Só membro ATIVO, desta conta, com papel
    'mestre' — senão o app ficaria mostrando a obra pra quem não devia."""
    with pool.connection() as c:
        if membro_id and not c.execute(
                """select 1 from membros where id=%s and conta_id=%s and papel='mestre'
                     and coalesce(ativo, true)""", (membro_id, conta_id)).fetchone():
            raise ValueError("Escolha um mestre de obras da sua equipe.")
        c.execute("update obras set mestre_id=%s where id=%s and conta_id=%s",
                  (membro_id or None, obra_id, conta_id))
        c.commit()


def mestres(pool, conta_id: int) -> list[dict]:
    try:
        with pool.connection() as c:
            rows = c.execute("""select id, nome from membros
                                 where conta_id=%s and papel='mestre' and coalesce(ativo, true)
                                 order by lower(nome)""", (conta_id,)).fetchall()
    except Exception:  # noqa: BLE001
        return []
    return [{"id": r[0], "nome": r[1]} for r in rows]


def mestre_da_obra(pool, conta_id: int, obra_id: int) -> int | None:
    try:
        with pool.connection() as c:
            r = c.execute("select mestre_id from obras where id=%s and conta_id=%s",
                          (obra_id, conta_id)).fetchone()
        return r[0] if r else None
    except Exception:  # noqa: BLE001 — sem a 550
        return None


# ── o registro do campo ───────────────────────────────────────────────────
def _registrar(pool, conta_id: int, obra_id: int, membro_id, tipo: str, ref: dict,
               descricao: str) -> int:
    with pool.connection() as c:
        eid = c.execute("""insert into obra_campo (conta_id, obra_id, membro_id, tipo, ref, descricao)
                           values (%s,%s,%s,%s,%s,%s) returning id""",
                        (conta_id, obra_id, membro_id, tipo, json.dumps(ref),
                         descricao[:200])).fetchone()[0]
        c.commit()
    return eid


def recentes(pool, conta_id: int, *, membro_id: int | None = None, limite: int = 20) -> list[dict]:
    """O que veio do campo, o mais novo primeiro (a lista "Do campo" do dono, e o
    "o que eu fiz hoje" do mestre quando `membro_id` vem)."""
    try:
        with pool.connection() as c:
            rows = c.execute(
                f"""select e.id, e.obra_id, o.nome, e.tipo, e.descricao, e.criado_em,
                           e.desfeito_em, coalesce(m.nome, 'Dono'), e.membro_id
                      from obra_campo e
                      join obras o on o.id = e.obra_id and o.conta_id = e.conta_id
                      left join membros m on m.id = e.membro_id and m.conta_id = e.conta_id
                     where e.conta_id=%s {"and e.membro_id=%s" if membro_id else ""}
                     order by e.criado_em desc, e.id desc limit %s""",
                (conta_id, membro_id, limite) if membro_id else (conta_id, limite)).fetchall()
    except Exception:  # noqa: BLE001 — sem a 550
        return []
    from .relogio import para_br       # o banco fala UTC; a tela, Brasília (regra 7)
    return [{"id": r[0], "obra_id": r[1], "obra": r[2], "tipo": r[3], "descricao": r[4],
             "quando": para_br(r[5]) if r[5] else None, "desfeito": r[6] is not None,
             "quem": r[7], "membro_id": r[8]}
            for r in rows]


# ── os quatro gestos (o quadro é só leitura: obra_grupos.quadro) ──────────
def marcar_etapa(pool, conta_id: int, obra_id: int, etapa_id: int, membro_id) -> dict:
    o = _ob.obter_obra(pool, conta_id, obra_id)
    e = next((x for x in (o or {}).get("etapas", []) if x["id"] == etapa_id), None)
    if not o or not e:
        raise ValueError("Etapa não encontrada.")
    if e["concluida_em"]:
        raise ValueError(f"{e['nome']} já estava marcada.")
    _ob.marcar_etapa(pool, conta_id, obra_id, etapa_id, concluida=True)
    pct = _ob.obter_obra(pool, conta_id, obra_id)["pct"]
    eid = _registrar(pool, conta_id, obra_id, membro_id, "etapa", {"etapa_id": etapa_id},
                     f"{e['nome']} pronta")
    return {"evento": eid, "frase": f"{e['nome']} marcada na {o['nome']}. A obra está em {pct}%."}


def foto(pool, conta_id: int, obra_id: int, conteudo: bytes, content_type: str, *,
         etapa: str | None, membro_id, subir=None) -> dict:
    from . import obra_fotos as _of
    f = _of.guardar(pool, conta_id, obra_id, conteudo, content_type, etapa=etapa or None,
                    origem="campo", membro_id=membro_id, subir=subir)
    desc = "Foto" + (f" da etapa {f['etapa'].lower()}" if f.get("etapa") else "")
    eid = _registrar(pool, conta_id, obra_id, membro_id, "foto", {"foto_id": f["id"]}, desc)
    return {"evento": eid, "frase": f"{desc} guardada na {f['obra']}."}


def material(pool, conta_id: int, obra_id: int, *, acao: str, material: str, quantidade,
             unidade: str = "", membro_id=None) -> dict:
    """usei (consumiu aqui), levei (do depósito pra cá) ou chegou (entrou direto
    aqui, sem nota). Material ambíguo pergunta, sem gravar."""
    from . import obra_material as _om
    if acao not in ("usei", "levei", "chegou"):
        raise ValueError("Escolha: usei, levei do depósito ou chegou.")
    try:
        q = Decimal(str(quantidade).replace(",", "."))
    except Exception:  # noqa: BLE001
        raise ValueError("Quantidade inválida.")
    p = _om.achar_produto(pool, conta_id, material)
    if p is not None and "ambiguo" in p:
        raise ValueError("Qual deles? " + " · ".join(p["ambiguo"]))
    if p is None:
        if acao != "chegou" or not (material or "").strip():
            raise ValueError(f"Não conheço “{material}”. Escolha da lista.")
        with pool.connection() as c:
            pid, nome, un = _om._achar_ou_criar(c, conta_id, material, unidade)
            c.commit()
        p = {"id": pid, "nome": nome, "unidade": un}
    r = _om.mover(pool, conta_id, acao=acao, produto_id=p["id"], quantidade=q, obra_id=obra_id,
                  por="app do mestre")
    verbo = {"usei": "usado", "levei": "levado do depósito", "chegou": "chegou"}[acao]
    desc = f"{_om.rotulo(q, p['unidade'])} de {p['nome']} — {verbo}"
    eid = _registrar(pool, conta_id, obra_id, membro_id, "material", {"mov_ids": r["mov_ids"]}, desc)
    frase = f"Apontado: {desc}. Na obra: {_om.rotulo(r.get('na_obra', 0), p['unidade'])}."
    if r.get("furo"):
        frase += " ⚠️ Uso maior que entrada — avise se faltou nota."
    return {"evento": eid, "frase": frase}


# ── desfazer ──────────────────────────────────────────────────────────────
def desfazer(pool, conta_id: int, evento_id: int, *, membro_id: int | None = None) -> str:
    """Volta o gesto. O mestre desfaz só os DELE (`membro_id`); o dono, qualquer um."""
    with pool.connection() as c:
        r = c.execute("""select obra_id, tipo, ref, descricao, desfeito_em, membro_id
                           from obra_campo where id=%s and conta_id=%s""",
                      (evento_id, conta_id)).fetchone()
    if not r:
        raise ValueError("Não achei esse registro.")
    obra_id, tipo, ref, desc, desfeito, dono_do_gesto = r
    if desfeito:
        raise ValueError("Isso já foi desfeito.")
    if membro_id is not None and dono_do_gesto != membro_id:
        raise ValueError("Só dá pra desfazer o que você mesmo fez.")
    ref = ref if isinstance(ref, dict) else json.loads(ref or "{}")
    if tipo == "etapa":
        _ob.marcar_etapa(pool, conta_id, obra_id, int(ref["etapa_id"]), concluida=False)
    elif tipo == "material":
        with pool.connection() as c:
            c.execute("delete from estoque_mov where fornecedor_id=%s and id = any(%s)",
                      (conta_id, [int(i) for i in ref.get("mov_ids", [])]))
            c.commit()
    elif tipo == "foto":
        from . import obra_fotos as _of
        _of.apagar(pool, conta_id, obra_id, int(ref["foto_id"]))
    with pool.connection() as c:
        c.execute("update obra_campo set desfeito_em=now() where id=%s and conta_id=%s",
                  (evento_id, conta_id))
        c.commit()
    return f"Desfeito: {desc}."
