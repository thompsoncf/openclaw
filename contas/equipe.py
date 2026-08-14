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


def rotas_do_papel(papel: str | None) -> list[str]:
    """As rotas do painel que um MEMBRO de equipe pode abrir (o gate de web/app.py
    é whitelist; o dono passa em tudo e não usa isto).

    Vive aqui, junto do CAPS, pra ser a mesma fonte da verdade que o gate usa e
    que os testes conferem: quando uma tela precisa desviar um membro, o destino
    tem que estar nesta lista — senão o gate devolve, a tela desvia de novo e o
    membro fica preso num laço de redirect sem conseguir entrar.
    """
    caps = caps_do_papel(papel)
    permitido = ["/trocar", "/sair"]
    if caps["vendas"]:
        permitido += ["/painel/servicos", "/painel/prospeccao"]
    if caps["financeiro"]:
        permitido += ["/painel/empresa", "/painel/relatorios"]
    if caps["gerir"]:
        permitido += ["/painel/equipe", "/membros"]
    return permitido


def rotulo(papel: str | None) -> str:
    return _ROTULOS.get(papel or "", papel or "")


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def garantir_tabela(pool):
    """Colunas de login web em membros (idempotente). Relaxa o check de papel pra
    aceitar os papéis de equipe."""
    with pool.connection() as c:
        c.execute("alter table contas  add column if not exists senha_hash     text")
        c.execute("alter table membros add column if not exists email          text")
        c.execute("alter table membros add column if not exists senha_hash     text")
        c.execute("alter table membros add column if not exists convite_token  text")
        c.execute("alter table membros add column if not exists convite_expira timestamptz")
        c.execute("alter table membros add column if not exists whatsapp       text")
        c.execute("alter table membros add column if not exists comissao_pct   numeric(5,2)")
        c.execute("alter table membros drop constraint if exists membros_papel_check")
        # e-mail é único POR CONTA (não global): a mesma pessoa pode ser membro de
        # várias empresas com o mesmo e-mail. Troca o índice global antigo, se houver.
        c.execute("drop index if exists idx_membros_email")
        c.execute("create unique index if not exists idx_membros_email_conta "
                  "on membros (conta_id, lower(email)) where email is not null")
        c.execute("create unique index if not exists idx_membros_convite "
                  "on membros (convite_token) where convite_token is not null")
        c.commit()


def _tem_login(c, email: str) -> bool:
    """A pessoa já tem uma senha no Zaq? (conta própria OU membro que já aceitou).
    Se sim, o convite só a VINCULA — ela entra com a senha que já tem."""
    if c.execute("select 1 from contas where lower(email)=%s and senha_hash is not null",
                 (email,)).fetchone():
        return True
    return c.execute("select 1 from membros where lower(email)=%s and senha_hash is not null",
                     (email,)).fetchone() is not None


def _so_fone(s) -> str | None:
    """Normaliza um número: só dígitos (aceita + no começo). Vazio → None."""
    s = (s or "").strip()
    dig = "".join(ch for ch in s if ch.isdigit())
    return ("+" + dig if s.startswith("+") else dig)[:20] or None


def convidar(pool, conta_id: int, nome: str, email: str, papel: str, whatsapp: str = "") -> dict:
    """Convida um membro pra ESTA conta.

    - E-mail é único POR conta (a pessoa pode ser membro de várias empresas).
    - Se a pessoa já tem login Zaq (conta própria ou membro de outra empresa),
      já entra ativa e usa a senha que tem — devolve {ja_tem_login: True}, sem link.
    - Senão, cria inativa com token e devolve o link pra ela criar a senha.
    - whatsapp: número pro aviso de rodízio (opcional).
    """
    papel = (papel or "vendedor").strip()
    if papel not in PAPEIS_PJ:
        return {"ok": False, "erro": "Papel inválido."}
    email = (email or "").strip().lower()
    if "@" not in email or "." not in email:
        return {"ok": False, "erro": "E-mail inválido."}
    nome = (nome or "").strip() or email.split("@")[0]
    wa = _so_fone(whatsapp)
    with pool.connection() as c:
        if c.execute("select 1 from membros where conta_id=%s and lower(email)=%s",
                     (conta_id, email)).fetchone():
            return {"ok": False, "erro": "Essa pessoa já está na equipe."}
        if _tem_login(c, email):
            row = c.execute(
                """insert into membros (conta_id, nome, papel, email, ativo, whatsapp)
                   values (%s,%s,%s,%s,true,%s) returning id""",
                (conta_id, nome, papel, email, wa)).fetchone()
            c.commit()
            return {"ok": True, "membro_id": row[0], "ja_tem_login": True}
        token = secrets.token_urlsafe(24)
        row = c.execute(
            """insert into membros (conta_id, nome, papel, email, ativo,
                                    convite_token, convite_expira, whatsapp)
               values (%s,%s,%s,%s,false,%s,%s,%s) returning id""",
            (conta_id, nome, papel, email, token, _agora() + timedelta(days=7), wa),
        ).fetchone()
        c.commit()
    return {"ok": True, "membro_id": row[0], "token": token, "ja_tem_login": False}


def contextos_de_login(pool, email: str, senha_txt: str) -> list[dict]:
    """Todos os contextos que essa pessoa (e-mail+senha) pode acessar:
    a própria conta (se tiver) + cada empresa onde é membro ativo.

    A senha é validada UMA vez, contra a autoridade da identidade: a conta
    própria manda; se não tiver conta, a senha do membro que ela definiu.
    Devolve [] se a senha não confere. Cada contexto:
        {tipo, conta_id, membro_id, papel, nome}
    """
    email = (email or "").strip().lower()
    with pool.connection() as c:
        conta = c.execute(
            "select id, nome, senha_hash from contas where lower(email)=%s", (email,)).fetchone()
        own_ok = bool(conta and _senha.verificar_senha(senha_txt, conta[2]))
        autoridade = own_ok
        if not autoridade:
            m = c.execute(
                "select senha_hash from membros where lower(email)=%s and senha_hash is not null limit 1",
                (email,)).fetchone()
            autoridade = bool(m and _senha.verificar_senha(senha_txt, m[0]))
        if not autoridade:
            return []
        ctxs: list[dict] = []
        if own_ok:
            ctxs.append({"tipo": "conta", "conta_id": conta[0], "membro_id": None,
                         "papel": "dono", "nome": conta[1]})
        membs = c.execute(
            """select m.id, m.conta_id, m.papel, co.nome
                 from membros m join contas co on co.id = m.conta_id
                where lower(m.email)=%s and m.ativo
                order by co.nome""", (email,)).fetchall()
    for mm in membs:
        ctxs.append({"tipo": "membro", "conta_id": mm[1], "membro_id": mm[0],
                     "papel": mm[2], "nome": mm[3]})
    return ctxs


def aplicar_contexto(session, ctx: dict) -> None:
    """Grava na sessão o contexto ativo escolhido (conta própria ou empresa)."""
    session["conta_id"] = ctx["conta_id"]
    session["papel"] = ctx["papel"]
    if ctx.get("membro_id"):
        session["membro_id"] = ctx["membro_id"]
    else:
        session.pop("membro_id", None)


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
            """select id, nome, email, papel, ativo, (convite_token is not null),
                      coalesce(whatsapp,''), comissao_pct
                 from membros where conta_id=%s and email is not null
                order by id""", (conta_id,)).fetchall()
    # 'pendente' = convite por LINK ainda não aceito (inativo + com token). Quem já
    # tinha login Zaq entra ativo sem senha/token — NÃO é pendente.
    return [{"id": r[0], "nome": r[1], "email": r[2], "papel": r[3], "ativo": r[4],
             "pendente": (not r[4]) and r[5], "rotulo": rotulo(r[3]), "whatsapp": r[6],
             "comissao_pct": float(r[7]) if r[7] is not None else None} for r in rows]


def definir_comissao(pool, conta_id: int, membro_id: int, comissao_pct: float | None) -> dict:
    """% de comissão do membro sobre as vendas que ele lançou (relatório de
    Comissão). None/negativo limpa a configuração; acima de 100 é rejeitado."""
    if comissao_pct is not None and (comissao_pct < 0 or comissao_pct > 100):
        return {"ok": False, "erro": "Comissão deve estar entre 0 e 100%."}
    with pool.connection() as c:
        r = c.execute(
            "update membros set comissao_pct=%s where id=%s and conta_id=%s and papel<>'dono' returning id",
            (comissao_pct, membro_id, conta_id)).fetchone()
        c.commit()
    return {"ok": bool(r)}


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


def renomear_membro(pool, conta_id: int, membro_id: int, nome: str, whatsapp=None) -> dict:
    """Corrige nome (e o WhatsApp, se vier) de um membro. Nunca mexe no dono.
    whatsapp=None mantém o atual; string (mesmo vazia) sobrescreve."""
    nome = (nome or "").strip()[:80]
    if not nome:
        return {"ok": False, "erro": "Informe um nome."}
    with pool.connection() as c:
        if whatsapp is None:
            r = c.execute(
                "update membros set nome=%s where id=%s and conta_id=%s and papel<>'dono' returning id",
                (nome, membro_id, conta_id)).fetchone()
        else:
            r = c.execute(
                "update membros set nome=%s, whatsapp=%s where id=%s and conta_id=%s and papel<>'dono' returning id",
                (nome, _so_fone(whatsapp), membro_id, conta_id)).fetchone()
        c.commit()
    return {"ok": bool(r)}


def remover_membro(pool, conta_id: int, membro_id: int) -> dict:
    """Exclui o vínculo de um membro com ESTA empresa (não apaga a pessoa do Zaq).
    Nunca remove o dono. Se o membro tiver leads/registros vinculados (FK), não
    exclui — devolve {erro:'vinculado'} pra orientar a desativar/reatribuir antes."""
    from psycopg.errors import ForeignKeyViolation
    try:
        with pool.connection() as c:
            r = c.execute(
                "delete from membros where id=%s and conta_id=%s and papel<>'dono' returning id",
                (membro_id, conta_id)).fetchone()
            c.commit()
        return {"ok": bool(r)}
    except ForeignKeyViolation:
        return {"ok": False, "erro": "vinculado"}


def regerar_convite(pool, conta_id: int, membro_id: int) -> dict:
    """Novo link de convite (membro esqueceu a senha ou o convite venceu)."""
    token = secrets.token_urlsafe(24)
    with pool.connection() as c:
        r = c.execute(
            """update membros set convite_token=%s, convite_expira=%s, senha_hash=null, ativo=false
                where id=%s and conta_id=%s and papel<>'dono' returning id, email, nome, papel""",
            (token, _agora() + timedelta(days=7), membro_id, conta_id)).fetchone()
        c.commit()
    if not r:
        return {"ok": False}
    return {"ok": True, "token": token, "email": r[1], "nome": r[2], "papel": r[3]}
