"""Equipe: login por usuário (membro) no painel web + permissões por papel.

Hoje o membro entrava só por chat (telegram/whatsapp). Aqui ele ganha login web
próprio — e-mail + senha — com um PAPEL que define o que acessa. O dono convida
por LINK; o membro abre o link e cria a própria senha (o dono nunca vê a senha).

Papéis = presets de 3 capacidades (vendas · financeiro · gerir):
    dono       -> vendas ✓  financeiro ✓  gerir ✓   (o titular da conta; login por conta)
    gestor     -> vendas ✓  financeiro ✓  gerir ✗
    vendedor   -> vendas ✓  financeiro ✗  gerir ✗
    financeiro -> vendas ✗  financeiro ✓  gerir ✗

Reusa a tabela `membros` (multi-tenant por conta_id). As colunas de login web
(email/senha_hash/convite_token) são garantidas em runtime — o deploy não roda
migração sozinho.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone

from . import senha as _senha

# capacidade por papel — fonte única da verdade do que cada um acessa.
CAPS = {
    "dono":       {"vendas": True,  "financeiro": True,  "gerir": True},
    "gestor":     {"vendas": True,  "financeiro": True,  "gerir": False},
    "vendedor":   {"vendas": True,  "financeiro": False, "gerir": False},
    "financeiro": {"vendas": False, "financeiro": True,  "gerir": False},
    # compat com o modelo família (chat): nunca acessam Vendas nem gerem a conta.
    "membro":     {"vendas": False, "financeiro": True,  "gerir": False},
    "restrito":   {"vendas": False, "financeiro": False, "gerir": False},
}
# papéis que o dono pode atribuir a um membro de equipe (o dono é o titular).
PAPEIS_PJ = ("gestor", "vendedor", "financeiro")
_ROTULOS = {"dono": "Dono", "gestor": "Gestor", "vendedor": "Vendedor",
            "financeiro": "Financeiro", "membro": "Membro", "restrito": "Restrito"}
_SEM_ACESSO = {"vendas": False, "financeiro": False, "gerir": False}


def caps_do_papel(papel: str | None) -> dict:
    return dict(CAPS.get(papel or "", _SEM_ACESSO))


def rotulo(papel: str | None) -> str:
    return _ROTULOS.get(papel or "", papel or "")


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def garantir_tabela(pool):
    """Colunas de login web em membros (idempotente). Relaxa o check de papel pra
    aceitar os papéis de equipe."""
    with pool.connection() as c:
        c.execute("alter table membros add column if not exists email          text")
        c.execute("alter table membros add column if not exists senha_hash     text")
        c.execute("alter table membros add column if not exists convite_token  text")
        c.execute("alter table membros add column if not exists convite_expira timestamptz")
        c.execute("alter table membros drop constraint if exists membros_papel_check")
        c.execute("create unique index if not exists idx_membros_email "
                  "on membros (lower(email)) where email is not null")
        c.execute("create unique index if not exists idx_membros_convite "
                  "on membros (convite_token) where convite_token is not null")
        c.commit()


def _email_livre(c, email: str, ignorar_membro: int | None = None) -> bool:
    """E-mail não pode colidir com nenhuma conta (login do dono) nem outro membro."""
    if c.execute("select 1 from contas where lower(email)=%s", (email,)).fetchone():
        return False
    q = "select 1 from membros where lower(email)=%s"
    args: list = [email]
    if ignorar_membro:
        q += " and id<>%s"
        args.append(ignorar_membro)
    return c.execute(q, tuple(args)).fetchone() is None


def convidar(pool, conta_id: int, nome: str, email: str, papel: str) -> dict:
    """Cria o membro (inativo até aceitar) e devolve o token do link de convite."""
    papel = (papel or "vendedor").strip()
    if papel not in PAPEIS_PJ:
        return {"ok": False, "erro": "Papel inválido."}
    email = (email or "").strip().lower()
    if "@" not in email or "." not in email:
        return {"ok": False, "erro": "E-mail inválido."}
    nome = (nome or "").strip() or email.split("@")[0]
    token = secrets.token_urlsafe(24)
    with pool.connection() as c:
        if not _email_livre(c, email):
            return {"ok": False, "erro": "Esse e-mail já está em uso."}
        row = c.execute(
            """insert into membros (conta_id, nome, papel, email, ativo,
                                    convite_token, convite_expira)
               values (%s,%s,%s,%s,false,%s,%s) returning id""",
            (conta_id, nome, papel, email, token, _agora() + timedelta(days=7)),
        ).fetchone()
        c.commit()
    return {"ok": True, "membro_id": row[0], "token": token}


def info_convite(pool, token: str) -> dict | None:
    """Dados pra tela de aceitar convite (ou None se inválido/expirado)."""
    with pool.connection() as c:
        r = c.execute(
            """select m.id, m.nome, m.email, m.papel, m.convite_expira, c.nome
                 from membros m join contas c on c.id = m.conta_id
                where m.convite_token=%s""", (token,)).fetchone()
    if not r or (r[4] and r[4] < _agora()):
        return None
    return {"membro_id": r[0], "nome": r[1], "email": r[2], "papel": r[3],
            "empresa": r[5], "rotulo": rotulo(r[3])}


def aceitar_convite(pool, token: str, senha_txt: str) -> dict:
    """Membro define a própria senha e ativa o login web."""
    if len(senha_txt or "") < 8:
        return {"ok": False, "erro": "Senha de no mínimo 8 caracteres."}
    with pool.connection() as c:
        r = c.execute(
            "select id, conta_id, papel, convite_expira from membros where convite_token=%s",
            (token,)).fetchone()
        if not r:
            return {"ok": False, "erro": "Convite inválido ou já usado."}
        if r[3] and r[3] < _agora():
            return {"ok": False, "erro": "Convite expirado. Peça um novo ao gestor."}
        c.execute(
            """update membros set senha_hash=%s, ativo=true,
                   convite_token=null, convite_expira=null where id=%s""",
            (_senha.hash_senha(senha_txt), r[0]))
        c.commit()
    return {"ok": True, "conta_id": r[1], "membro_id": r[0], "papel": r[2]}


def autenticar(pool, email: str, senha_txt: str) -> dict | None:
    """Login web do membro. Devolve o contexto pra sessão, ou None."""
    email = (email or "").strip().lower()
    with pool.connection() as c:
        r = c.execute(
            """select id, conta_id, nome, papel, senha_hash, ativo
                 from membros where lower(email)=%s""", (email,)).fetchone()
    if not r or not r[5] or not r[4]:            # inativo ou sem senha definida
        return None
    if not _senha.verificar_senha(senha_txt, r[4]):
        return None
    return {"membro_id": r[0], "conta_id": r[1], "nome": r[2], "papel": r[3]}


def listar_equipe(pool, conta_id: int) -> list[dict]:
    """Membros com login web (os que têm e-mail). Não inclui o dono (login por conta)."""
    with pool.connection() as c:
        rows = c.execute(
            """select id, nome, email, papel, ativo, (senha_hash is not null)
                 from membros where conta_id=%s and email is not null
                order by id""", (conta_id,)).fetchall()
    return [{"id": r[0], "nome": r[1], "email": r[2], "papel": r[3], "ativo": r[4],
             "aceitou": r[5], "rotulo": rotulo(r[3])} for r in rows]


def atualizar_papel(pool, conta_id: int, membro_id: int, papel: str) -> dict:
    if papel not in PAPEIS_PJ:
        return {"ok": False, "erro": "Papel inválido."}
    with pool.connection() as c:
        r = c.execute(
            "update membros set papel=%s where id=%s and conta_id=%s and papel<>'dono' returning id",
            (papel, membro_id, conta_id)).fetchone()
        c.commit()
    return {"ok": bool(r)}


def definir_ativo(pool, conta_id: int, membro_id: int, ativo: bool) -> dict:
    with pool.connection() as c:
        r = c.execute(
            "update membros set ativo=%s where id=%s and conta_id=%s and papel<>'dono' returning id",
            (ativo, membro_id, conta_id)).fetchone()
        c.commit()
    return {"ok": bool(r)}


def regerar_convite(pool, conta_id: int, membro_id: int) -> dict:
    """Novo link de convite (membro esqueceu a senha ou o convite venceu)."""
    token = secrets.token_urlsafe(24)
    with pool.connection() as c:
        r = c.execute(
            """update membros set convite_token=%s, convite_expira=%s, senha_hash=null, ativo=false
                where id=%s and conta_id=%s and papel<>'dono' returning id""",
            (token, _agora() + timedelta(days=7), membro_id, conta_id)).fetchone()
        c.commit()
    return {"ok": True, "token": token} if r else {"ok": False}
