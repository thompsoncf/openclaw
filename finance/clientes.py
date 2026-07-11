"""Base de clientes do lojista — modelo PESSOA + RELACAO (Zaq Vendas).

DESENHO (pos-migracao 066):
- IDENTIDADE vive em `pessoas` (cpf/celular/nome/email/conta_zaq_id) e e' UNICA no
  sistema. CPF e' chave forte (funde na igualdade); celular SUGERE (familia divide
  numero) e NUNCA funde sozinho.
- RELACAO loja<->pessoa vive em `clientes` (dono_id + pessoa_id + obs/aniversario/ativo).
  Multi-tenant: um lojista so' ve a SUA relacao. `clientes.nome/telefone/email` sao
  mantidos como CACHE (a leitura sempre prefere `pessoas` via coalesce).
- Portabilidade: se a pessoa virar conta Zaq, `pessoas.conta_zaq_id` liga — e a loja
  nunca ve isso, nem o cliente ve a base da loja.

Compatibilidade: todas as funcoes publicas antigas seguem existindo com a mesma
assinatura (criar_cliente, listar_clientes, obter_cliente, buscar_por_telefone,
atualizar_cliente, arquivar_cliente, achar_ou_criar, contar_clientes). Novas:
resolver_pessoa, puxar_ou_criar_cliente, sugerir_pessoas_por_celular.
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


# ---------------------------------------------------------------------------
# IDENTIDADE (pessoas)
# ---------------------------------------------------------------------------
def resolver_pessoa(pool, *, cpf: str | None = None, celular: str | None = None,
                    nome: str | None = None, email: str | None = None,
                    conta_zaq_id: int | None = None) -> int:
    """Acha/cria a IDENTIDADE e retorna pessoa_id.

    Regra de dedup: CPF FUNDE (se ja existe pessoa com esse cpf, reusa). Celular NAO
    funde sozinho (so' sugere — ver sugerir_pessoas_por_celular). Se nao achou por
    cpf, cria uma pessoa nova (nome obrigatorio).
    """
    cpf_d = _so_digitos(cpf)
    with pool.connection() as c:
        if cpf_d:
            r = c.execute("select id from pessoas where cpf=%s", (cpf_d,)).fetchone()
            if r:
                return int(r[0])
        nome_l = (nome or "").strip()
        if not nome_l:
            raise ValueError("nome e' obrigatorio pra criar a pessoa")
        row = c.execute(
            """insert into pessoas (cpf, celular, nome, email, conta_zaq_id)
               values (%s, %s, %s, %s, %s) returning id""",
            (cpf_d, _so_digitos(celular), nome_l,
             (email or "").strip().lower() or None, conta_zaq_id),
        ).fetchone()
        c.commit()
        return int(row[0])


def sugerir_pessoas_por_celular(pool, celular: str | None) -> list[dict]:
    """Celular SUGERE (nao funde): pode haver mais de uma pessoa no mesmo numero
    (familia). Retorna candidatos pra o lojista escolher/confirmar."""
    tel = _so_digitos(celular)
    if not tel:
        return []
    with pool.connection() as c:
        rows = c.execute(
            "select id, nome, cpf, celular from pessoas where celular=%s order by nome limit 10",
            (tel,),
        ).fetchall()
    return [{"id": r[0], "nome": r[1], "cpf": r[2], "celular": r[3]} for r in rows]


# ---------------------------------------------------------------------------
# RELACAO (clientes)
# ---------------------------------------------------------------------------
def puxar_ou_criar_cliente(pool, dono_id: int, *, cpf: str | None = None,
                           celular: str | None = None, nome: str | None = None,
                           email: str | None = None, pessoa_id: int | None = None,
                           aniversario=None, obs: str | None = None) -> int:
    """Fluxo de cadastro/venda: resolve a PESSOA (por cpf, ou pessoa_id dado) e
    garante a RELACAO (clientes) deste lojista com ela. Retorna cliente_id.

    Se o lojista ja tem uma relacao ativa com essa pessoa, reusa (nao duplica).
    """
    if pessoa_id is None:
        pessoa_id = resolver_pessoa(pool, cpf=cpf, celular=celular, nome=nome, email=email)
    with pool.connection() as c:
        r = c.execute(
            "select id from clientes where dono_id=%s and pessoa_id=%s and ativo limit 1",
            (dono_id, pessoa_id),
        ).fetchone()
        if r:
            return int(r[0])
        p = c.execute(
            "select nome, celular, email from pessoas where id=%s", (pessoa_id,)
        ).fetchone()
        pn = p[0] if p else (nome or "").strip()
        pc = p[1] if p else _so_digitos(celular)
        pe = p[2] if p else ((email or "").strip().lower() or None)
        row = c.execute(
            """insert into clientes
                 (dono_id, pessoa_id, nome, telefone, email, aniversario, obs)
               values (%s, %s, %s, %s, %s, %s, %s) returning id""",
            (dono_id, pessoa_id, pn, pc, pe, _parse_data(aniversario),
             (obs or "").strip() or None),
        ).fetchone()
        c.commit()
        return int(row[0])


def criar_cliente(pool, dono_id: int, nome: str, *, telefone: str | None = None,
                  email: str | None = None, aniversario=None,
                  conta_zaq_id: int | None = None, obs: str | None = None,
                  cpf: str | None = None) -> int:
    """Cadastra um cliente (identidade + relacao). Retorna o cliente_id. nome
    obrigatorio. Compat: mesma assinatura de antes, agora com `cpf` opcional; o
    telefone entra como `celular` da pessoa."""
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("nome do cliente e' obrigatorio")
    pessoa_id = resolver_pessoa(pool, cpf=cpf, celular=telefone, nome=nome,
                                email=email, conta_zaq_id=conta_zaq_id)
    return puxar_ou_criar_cliente(pool, dono_id, pessoa_id=pessoa_id, nome=nome,
                                  email=email, aniversario=aniversario, obs=obs)


_SEL = """select c.id,
                 coalesce(p.nome, c.nome)        as nome,
                 coalesce(p.celular, c.telefone) as telefone,
                 coalesce(p.email, c.email)      as email,
                 c.aniversario,
                 p.conta_zaq_id,
                 c.obs,
                 p.cpf,
                 c.pessoa_id
            from clientes c
            left join pessoas p on p.id = c.pessoa_id"""


def listar_clientes(pool, dono_id: int, busca: str | None = None,
                    limite: int = 200) -> list[dict]:
    """Lista os clientes do lojista. Se `busca`, filtra por nome, telefone ou cpf."""
    sql = _SEL + " where c.dono_id=%s and c.ativo"
    params: list = [dono_id]
    if busca and busca.strip():
        termo = f"%{busca.strip()}%"
        dig = _so_digitos(busca) or ""
        termo_dig = f"%{dig}%"
        sql += (" and (coalesce(p.nome,c.nome) ilike %s"
                " or coalesce(p.celular,c.telefone,'') like %s"
                " or coalesce(p.cpf,'') like %s)")
        params += [termo, termo_dig, termo_dig]
    sql += " order by coalesce(p.nome, c.nome) limit %s"
    params.append(limite)
    with pool.connection() as c:
        rows = c.execute(sql, params).fetchall()
    return [_row_para_dict(r) for r in rows]


def obter_cliente(pool, dono_id: int, cliente_id: int) -> dict | None:
    """Um cliente da base do lojista (isolado por dono_id). None se nao for dele."""
    with pool.connection() as c:
        r = c.execute(_SEL + " where c.id=%s and c.dono_id=%s and c.ativo",
                      (cliente_id, dono_id)).fetchone()
    return _row_para_dict(r) if r else None


def buscar_por_telefone(pool, dono_id: int, telefone: str) -> dict | None:
    """Acha um cliente do lojista pelo telefone (so digitos). None se nao achar.
    Util pra nao duplicar na hora de vender (dedup dentro da propria loja)."""
    tel = _so_digitos(telefone)
    if not tel:
        return None
    with pool.connection() as c:
        r = c.execute(
            _SEL + " where c.dono_id=%s and coalesce(p.celular, c.telefone)=%s"
                   " and c.ativo limit 1",
            (dono_id, tel),
        ).fetchone()
    return _row_para_dict(r) if r else None


def atualizar_cliente(pool, dono_id: int, cliente_id: int, **campos) -> bool:
    """Atualiza campos do cliente. Identidade (nome/telefone/email/cpf/conta_zaq_id)
    vai pra `pessoas`; relacao (aniversario/obs) vai pra `clientes`. So altera se o
    cliente for do lojista. Retorna True se algo mudou."""
    id_map = {"nome": "nome", "telefone": "celular", "email": "email",
              "cpf": "cpf", "conta_zaq_id": "conta_zaq_id"}
    rel_permit = {"aniversario", "obs"}
    mudou = False
    with pool.connection() as c:
        row = c.execute(
            "select pessoa_id from clientes where id=%s and dono_id=%s and ativo",
            (cliente_id, dono_id),
        ).fetchone()
        if not row:
            return False
        pessoa_id = row[0]

        isets, ivals = [], []
        for k, col in id_map.items():
            if k not in campos:
                continue
            v = campos[k]
            if k in ("telefone", "cpf"):
                v = _so_digitos(v)
            elif k == "email":
                v = (v or "").strip().lower() or None
            elif k == "nome":
                v = (v or "").strip() or None
            isets.append(f"{col}=%s")
            ivals.append(v)
        if isets and pessoa_id is not None:
            isets.append("atualizado_em=now()")
            ivals.append(pessoa_id)
            cur = c.execute(f"update pessoas set {', '.join(isets)} where id=%s", ivals)
            mudou = mudou or cur.rowcount > 0
            csets, cvals = [], []
            for k, col in (("nome", "nome"), ("telefone", "telefone"), ("email", "email")):
                if k in campos:
                    v = campos[k]
                    if k == "telefone":
                        v = _so_digitos(v)
                    elif k == "email":
                        v = (v or "").strip().lower() or None
                    else:
                        v = (v or "").strip() or None
                    csets.append(f"{col}=%s")
                    cvals.append(v)
            if csets:
                cvals += [cliente_id, dono_id]
                c.execute(
                    f"update clientes set {', '.join(csets)} where id=%s and dono_id=%s",
                    cvals,
                )

        rsets, rvals = [], []
        for k in rel_permit:
            if k not in campos:
                continue
            v = campos[k]
            if k == "aniversario":
                v = _parse_data(v)
            elif k == "obs":
                v = (v or "").strip() or None
            rsets.append(f"{k}=%s")
            rvals.append(v)
        if rsets:
            rsets.append("atualizado_em=now()")
            rvals += [cliente_id, dono_id]
            cur = c.execute(
                f"update clientes set {', '.join(rsets)} where id=%s and dono_id=%s and ativo",
                rvals,
            )
            mudou = mudou or cur.rowcount > 0

        c.commit()
    return mudou


def arquivar_cliente(pool, dono_id: int, cliente_id: int) -> bool:
    """Soft delete: marca ativo=false. So se for do lojista. (A pessoa/identidade
    permanece — outras lojas podem ter relacao com ela.)"""
    with pool.connection() as c:
        cur = c.execute(
            "update clientes set ativo=false, atualizado_em=now() "
            "where id=%s and dono_id=%s and ativo",
            (cliente_id, dono_id),
        )
        c.commit()
        return cur.rowcount > 0


def achar_ou_criar(pool, dono_id: int, nome: str,
                   telefone: str | None = None, cpf: str | None = None) -> int:
    """Fluxo do PDV: na venda, o lojista informa nome (+ telefone e/ou cpf). Reusa
    se ja existe (por cpf na identidade, ou por telefone na propria loja); senao
    cria. Retorna o cliente_id."""
    if cpf and _so_digitos(cpf):
        return puxar_ou_criar_cliente(pool, dono_id, cpf=cpf, celular=telefone, nome=nome)
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
        "cpf": r[7],
        "pessoa_id": r[8],
    }
