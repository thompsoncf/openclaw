"""As ferramentas e equipamentos do CD: código, empréstimo e devolução.

PR 2 de 3 do CD das obras (docs/mockups/obras_cd_almoxarifado.html, aba
"Ferramentas"), aprovado pelo dono em 03/10/2026. Decisão 5: a ferramenta entra
no MESMO catálogo do material (`catalogo_produtos`, categoria 'ferramenta') —
um cadastro só —, mas NÃO é estoque: sai e volta. Por isso cada unidade é um
patrimônio com código (FER-01) e o controle é por empréstimo
(`obra_ferramenta_movs`), nunca por `estoque_mov`.

ONDE ESTÁ: a saída sem volta (`voltou_em` vazio) diz a obra e com quem; sem
nenhuma aberta, está no CD. Mandar pra outra obra fecha a saída anterior e abre
a nova — a ferramenta nunca está em dois lugares (índice único parcial).

O AVISO (o que o mockup aprovou): a ferramenta que ficou numa obra PRONTA, ou
que está fora há mais de `DIAS_FORA` dias. Sem cadastro de "quanto tempo cada
uma pode ficar": a régua é uma só, e o que ela pede é conferir.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone

from . import obras as _ob

DIAS_FORA = 7
_PRONTA = ("pronta", "vendida", "entregue", "arquivada")


def _agora():
    return datetime.now(timezone.utc)


def _tipo(c, conta_id: int, nome: str) -> int:
    """O tipo da ferramenta no catálogo (categoria 'ferramenta'): o mesmo nome
    reaproveita, sem se misturar com o material de mesmo nome."""
    alvo = _ob._norm(nome)
    for pid, n in c.execute("""select id, nome from catalogo_produtos
                                where fornecedor_id=%s and categoria='ferramenta' and ativo""",
                            (conta_id,)).fetchall():
        if _ob._norm(n) == alvo:
            return pid
    return c.execute("""insert into catalogo_produtos (fornecedor_id, nome, unidade, categoria, disponivel)
                        values (%s,%s,'unidade','ferramenta',false) returning id""",
                     (conta_id, nome)).fetchone()[0]


def _proximo_numero(c, conta_id: int) -> int:
    maior = 0
    for (cod,) in c.execute("select codigo from obra_ferramentas where conta_id=%s",
                            (conta_id,)).fetchall():
        m = re.fullmatch(r"FER-(\d+)", (cod or "").strip().upper())
        if m:
            maior = max(maior, int(m.group(1)))
    return maior + 1


def cadastrar(pool, conta_id: int, nome: str, quantidade=1, obs: str = "") -> list[str]:
    """Cada unidade ganha o seu código (FER-01, FER-02…). Devolve os códigos."""
    nome = " ".join((nome or "").split())[:80]
    if not nome:
        raise ValueError("Diga o nome da ferramenta — por exemplo, Betoneira 400 L.")
    try:
        n = int(str(quantidade or 1).strip())
    except ValueError:
        raise ValueError("Quantidade inválida.")
    if not 1 <= n <= 50:
        raise ValueError("Cadastre de 1 a 50 de uma vez.")
    with pool.connection() as c:
        pid = _tipo(c, conta_id, nome)
        prox = _proximo_numero(c, conta_id)
        codigos = [f"FER-{prox + i:02d}" for i in range(n)]
        for cod in codigos:
            c.execute("""insert into obra_ferramentas (conta_id, produto_id, codigo, obs)
                         values (%s,%s,%s,%s)""", (conta_id, pid, cod, " ".join((obs or "").split())[:120]))
        c.commit()
    return codigos


# ── onde está cada uma ────────────────────────────────────────────────────
def listar(pool, conta_id: int, *, obra_id: int | None = None) -> list[dict]:
    """As ferramentas ativas, com onde estão, com quem, há quantos dias e o
    aviso. `obra_id`: só as que estão naquela obra (o app do mestre)."""
    try:
        with pool.connection() as c:
            rows = c.execute(
                """select f.id, f.codigo, p.nome, f.obs, m.id, m.obra_id, o.nome, o.status,
                          m.com_quem, m.saiu_em
                     from obra_ferramentas f
                     join catalogo_produtos p on p.id = f.produto_id and p.fornecedor_id = f.conta_id
                     left join obra_ferramenta_movs m on m.ferramenta_id = f.id and m.voltou_em is null
                          and m.conta_id = f.conta_id
                     left join obras o on o.id = m.obra_id and o.conta_id = f.conta_id
                    where f.conta_id=%s and f.ativa
                    order by lower(p.nome), f.codigo""", (conta_id,)).fetchall()
    except Exception:  # noqa: BLE001 — sem a migração
        return []
    pct = {o["id"]: o["pct"] for o in _ob.listar_obras(pool, conta_id, com_custos=False,
                                                        incluir_arquivadas=True)}
    from .relogio import para_br
    agora, out = _agora(), []
    for fid, cod, nome, obs, mov, oid, onome, ostatus, quem, saiu in rows:
        fora = mov is not None
        dias = (agora - saiu).days if fora else 0
        pronta = bool(fora and oid and (ostatus in _PRONTA or pct.get(oid) == 100))
        alerta = ""
        if fora and not oid:
            alerta = "a obra dela foi apagada — onde está?"
        elif pronta:
            alerta = "a obra está pronta — recolher"
        elif fora and dias > DIAS_FORA:
            alerta = f"fora há {dias} dias — confira"
        d = {"id": fid, "codigo": cod, "nome": nome, "obs": obs, "fora": fora,
             "obra_id": oid, "obra": onome or ("obra apagada" if fora else ""),
             "com_quem": quem or "", "desde": para_br(saiu) if fora else None,
             "dias": dias, "obra_pronta": pronta, "alerta": alerta,
             # o "desde" falado: hoje / 1 dia / 12 dias
             "ha": ("hoje" if dias == 0 else f"{dias} dia{'s' if dias != 1 else ''}") if fora else ""}
        if obra_id is None or oid == obra_id:
            out.append(d)
    return out


def resumo(lista: list[dict]) -> dict:
    return {"total": len(lista), "fora": sum(1 for f in lista if f["fora"]),
            "alertas": [f for f in lista if f["alerta"]]}


# ── sair, voltar, dar baixa ───────────────────────────────────────────────
def _ferramenta(c, conta_id: int, ferramenta_id: int):
    r = c.execute("""select f.codigo, p.nome from obra_ferramentas f
                       join catalogo_produtos p on p.id = f.produto_id and p.fornecedor_id = f.conta_id
                      where f.id=%s and f.conta_id=%s and f.ativa""",
                  (ferramenta_id, conta_id)).fetchone()
    if not r:
        raise ValueError("Ferramenta não encontrada.")
    return r


def _aberta(c, conta_id: int, ferramenta_id: int):
    return c.execute("""select id, obra_id from obra_ferramenta_movs
                         where ferramenta_id=%s and conta_id=%s and voltou_em is null""",
                     (ferramenta_id, conta_id)).fetchone()


def emprestar(pool, conta_id: int, ferramenta_id: int, obra_id: int, *, com_quem: str = "",
              por=None) -> str:
    """Manda pra obra. Se estava em outra obra, fecha aquela saída antes (vai
    direto de uma casa pra outra, sem passar pelo CD)."""
    with pool.connection() as c:
        cod, nome = _ferramenta(c, conta_id, ferramenta_id)
        obra = c.execute("select nome from obras where id=%s and conta_id=%s",
                         (obra_id, conta_id)).fetchone()
        if not obra:
            raise ValueError("Obra não encontrada.")
        ab = _aberta(c, conta_id, ferramenta_id)
        if ab and ab[1] == obra_id:
            raise ValueError(f"{nome} ({cod}) já está na {obra[0]}.")
        if ab:
            c.execute("""update obra_ferramenta_movs set voltou_em=now(), voltou_por=%s
                          where id=%s and conta_id=%s""", (por, ab[0], conta_id))
        c.execute("""insert into obra_ferramenta_movs (conta_id, ferramenta_id, obra_id, com_quem, saiu_por)
                     values (%s,%s,%s,%s,%s)""",
                  (conta_id, ferramenta_id, obra_id, " ".join((com_quem or "").split())[:60], por))
        c.commit()
    return f"{nome} ({cod}) foi pra {obra[0]}" + (f", com {com_quem.strip()}" if (com_quem or "").strip() else "") + "."


def devolver(pool, conta_id: int, ferramenta_id: int, *, por=None) -> str:
    with pool.connection() as c:
        cod, nome = _ferramenta(c, conta_id, ferramenta_id)
        ab = _aberta(c, conta_id, ferramenta_id)
        if not ab:
            raise ValueError(f"{nome} ({cod}) já está no CD.")
        c.execute("""update obra_ferramenta_movs set voltou_em=now(), voltou_por=%s
                      where id=%s and conta_id=%s""", (por, ab[0], conta_id))
        c.commit()
    return f"{nome} ({cod}) voltou pro CD."


def baixar(pool, conta_id: int, ferramenta_id: int, motivo: str) -> str:
    """Quebrou, sumiu, foi vendida: sai da lista e o histórico fica."""
    motivo = " ".join((motivo or "").split())[:120]
    if not motivo:
        raise ValueError("Diga o motivo da baixa (quebrou, sumiu, vendeu…).")
    with pool.connection() as c:
        cod, nome = _ferramenta(c, conta_id, ferramenta_id)
        c.execute("""update obra_ferramenta_movs set voltou_em=now()
                      where ferramenta_id=%s and conta_id=%s and voltou_em is null""",
                  (ferramenta_id, conta_id))
        c.execute("""update obra_ferramentas set ativa=false, baixa_motivo=%s, baixa_em=now()
                      where id=%s and conta_id=%s""", (motivo, ferramenta_id, conta_id))
        c.commit()
    return f"{nome} ({cod}) baixada: {motivo}."


def devolver_da_obra(pool, conta_id: int, obra_id: int, *, por=None) -> list[str]:
    """Todas as ferramentas que estão na obra voltam pro CD. Devolve os nomes."""
    voltaram = []
    for f in listar(pool, conta_id, obra_id=obra_id):
        devolver(pool, conta_id, f["id"], por=por)
        voltaram.append(f"{f['nome']} ({f['codigo']})")
    return voltaram


def devolver_tudo(pool, conta_id: int, obra_id: int, *, por=None) -> str:
    """A casa ficou pronta: o material E as ferramentas voltam pro CD."""
    from . import obra_pedidos as op
    partes = []
    try:
        partes.append(op.devolver(pool, conta_id, obra_id, por))
    except ValueError:
        pass                                   # sem material: só as ferramentas
    ferr = devolver_da_obra(pool, conta_id, obra_id, por=por)
    if ferr:
        partes.append("Ferramentas de volta: " + ", ".join(ferr) + ".")
    if not partes:
        raise ValueError("Essa obra não tem material nem ferramenta pra devolver.")
    return " ".join(partes)
