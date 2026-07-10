"""Base de clientes PROPRIA do lojista (Zaq Vendas).

O ativo de pos-venda: cada venda pode capturar um contato que depois vira
recompra, aviso de promocao, mensagem de aniversario. Como o Zaq ja fala no
WhatsApp, essa base destrava contato direto.

DESENHO (importante):
- A base e' do LOJISTA (dono_id -> conta do lojista). Multi-tenant: um lojista
  nunca ve o cliente de outro.
- O cliente NAO precisa ter conta no Zaq — e' a agenda do lojista, gente do balcao.
- Se o cliente TAMBEM for uma conta Zaq, da' pra vincular (conta_zaq_id), opcional.
- "Consumidor" (avulso) NAO fica aqui — venda avulsa referencia cliente_id nulo.

Tudo recebe o pool (padrao do projeto). Datas em date; telefone guardamos so
digitos (pra casar buscas) mas exibimos como o lojista digitou nao e' necessario.
"""
from __future__ import annotations

from datetime import date, datetime


def _so_digitos(s: str | None) -> str | None:
    if not s:
        return None
    d = "".join(c for c in s if c.isdigit())
    return d or None


def _parse_data(s):
    """Aceita 'AAAA-MM-DD' ou date; devolve date ou None."""
    if not s:
        return None
    if isinstance(s, date):
        return s
    try:
        return datetime.strptime(str(s).strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def criar_cliente(pool, dono_id: int, nome: str, *, telefone: str | None = None,
                  email: str | None = None, aniversario=None,
                  conta_zaq_id: int | None = None, obs: str | None = None) -> int:
    """Cadastra um cliente na base do lojista. Retorna o id. nome obrigatorio."""
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("nome do cliente e' obrigatorio")
    tel = _so_digitos(telefone)
    mail = (email or "").strip().lower() or None
    agg = _parse_data(aniversario)
    ob = (obs or "").strip() or None
    with pool.connection() as c:
        row = c.execute(
            """insert into clientes
                 (dono_id, nome, telefone, email, aniversario, conta_zaq_id, obs)
               values (%s, %s, %s, %s, %s, %s, %s) returning id""",
            (dono_id, nome, tel, mail, agg, conta_zaq_id, ob),
        ).fetchone()
        c.commit()
    return int(row[0])


def listar_clientes(pool, dono_id: int, busca: str | None = None,
                    limite: int = 200) -> list[dict]:
    """Lista os clientes do lojista (mais recentes primeiro). Se `busca`, filtra
    por nome ou telefone (parcial)."""
    sql = """select id, nome, telefone, email, aniversario, conta_zaq_id, obs
               from clientes where dono_id=%s and ativo"""
    params: list = [dono_id]
    if busca and busca.strip():
        termo = f"%{busca.strip()}%"
        termo_tel = f"%{_so_digitos(busca) or ''}%"
        sql += " and (nome ilike %s or coalesce(telefone,'') like %s)"
        params += [termo, termo_tel]
    sql += " order by nome limit %s"
    params.append(limite)
    with pool.connection() as c:
        rows = c.execute(sql, params).fetchall()
    return [_row_para_dict(r) for r in rows]


def obter_cliente(pool, dono_id: int, cliente_id: int) -> dict | None:
    """Um cliente da base do lojista (isolado por dono_id). None se nao for dele."""
    with pool.connection() as c:
        r = c.execute(
            """select id, nome, telefone, email, aniversario, conta_zaq_id, obs
                 from clientes where id=%s and dono_id=%s and ativo""",
            (cliente_id, dono_id),
        ).fetchone()
    return _row_para_dict(r) if r else None


def buscar_por_telefone(pool, dono_id: int, telefone: str) -> dict | None:
    """Acha um cliente do lojista pelo telefone (so digitos). None se nao achar.
    Util pra nao duplicar na hora de vender."""
    tel = _so_digitos(telefone)
    if not tel:
        return None
    with pool.connection() as c:
        r = c.execute(
            """select id, nome, telefone, email, aniversario, conta_zaq_id, obs
                 from clientes where dono_id=%s and telefone=%s and ativo
                 limit 1""",
            (dono_id, tel),
        ).fetchone()
    return _row_para_dict(r) if r else None


def atualizar_cliente(pool, dono_id: int, cliente_id: int, **campos) -> bool:
    """Atualiza campos do cliente (nome, telefone, email, aniversario, obs,
    conta_zaq_id). So altera se o cliente for do lojista. Retorna True se mudou."""
    permitidos = {"nome", "telefone", "email", "aniversario", "obs", "conta_zaq_id"}
    sets, vals = [], []
    for k, v in campos.items():
        if k not in permitidos:
            continue
        if k == "telefone":
            v = _so_digitos(v)
        elif k == "email":
            v = (v or "").strip().lower() or None
        elif k == "aniversario":
            v = _parse_data(v)
        elif k in ("nome", "obs"):
            v = (v or "").strip() or None
        sets.append(f"{k}=%s")
        vals.append(v)
    if not sets:
        return False
    sets.append("atualizado_em=now()")
    vals += [cliente_id, dono_id]
    with pool.connection() as c:
        cur = c.execute(
            f"update clientes set {', '.join(sets)} where id=%s and dono_id=%s and ativo",
            vals,
        )
        c.commit()
        return cur.rowcount > 0


def arquivar_cliente(pool, dono_id: int, cliente_id: int) -> bool:
    """Soft delete: marca ativo=false. So se for do lojista."""
    with pool.connection() as c:
        cur = c.execute(
            "update clientes set ativo=false, atualizado_em=now() "
            "where id=%s and dono_id=%s and ativo",
            (cliente_id, dono_id),
        )
        c.commit()
        return cur.rowcount > 0


def achar_ou_criar(pool, dono_id: int, nome: str,
                   telefone: str | None = None) -> int:
    """Fluxo do PDV: na venda, o lojista digita nome (+ telefone). Se ja existe
    pelo telefone, reusa; senao cria. Retorna o id do cliente. Se nao houver
    telefone, sempre cria um novo (nao da' pra deduplicar so pelo nome)."""
    if telefone:
        existente = buscar_por_telefone(pool, dono_id, telefone)
        if existente:
            return existente["id"]
    return criar_cliente(pool, dono_id, nome, telefone=telefone)


def contar_clientes(pool, dono_id: int) -> int:
    """Quantos clientes ativos o lojista tem na base."""
    with pool.connection() as c:
        r = c.execute(
            "select count(*) from clientes where dono_id=%s and ativo",
            (dono_id,),
        ).fetchone()
    return int(r[0]) if r else 0


def _row_para_dict(r) -> dict:
    return {
        "id": r[0],
        "nome": r[1],
        "telefone": r[2],
        "email": r[3],
        "aniversario": r[4],
        "conta_zaq_id": r[5],
        "obs": r[6],
    }
