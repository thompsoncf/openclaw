"""A entrada do mestre de obras no app /obra: cadastro sem e-mail e link mágico
pelo WhatsApp (pedido do dono em 03/10/2026: "faz o link mágico do mestre pelo
WhatsApp e cria o perfil na equipe pra quando for cadastrar").

POR QUE SEM E-MAIL E SEM SENHA: mestre de obras não lê e-mail nem guarda senha.
O dono cadastra só NOME + WHATSAPP na Equipe, toca em "Mandar o link pelo
WhatsApp" e o WhatsApp DELE abre com a mensagem pronta (wa.me). O mestre toca no
link, entra, e o celular fica lembrado.

O ENVIO SAI DO WHATSAPP DO DONO, não do número do Zaq: nenhum canal, sessão ou
template é tocado (CLAUDE.md, seção 1), e a mensagem chega de alguém que o
mestre conhece — que é o que faz ele tocar no link.

AS TABELAS SÃO AS DO COCKPIT (migrações 134 e 173): `cockpit_acesso` (o token
do link) e `cockpit_lembrete` (o aparelho lembrado, só o sha256 no banco). Elas
são genéricas por conta e membro. O que é daqui é a VALIDAÇÃO, que só aceita o
papel 'mestre' — e a do Cockpit (finance/cockpit.py) não aceita o mestre. Um
link de mestre não abre o Cockpit, e um do Cockpit não abre o /obra.

O LINK VALE 48 H E É REUSÁVEL DENTRO DO PRAZO: o WhatsApp abre o link sozinho
pra montar a prévia — se fosse de um uso, o mestre receberia um link já
queimado. Gerar um novo apaga os anteriores do mesmo mestre. E o lembrete
RELÊ O MEMBRO a cada uso: desativar o mestre na Equipe corta o acesso na hora.
"""
from __future__ import annotations

import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

TTL_HORAS = 48
COOKIE = "zaq_obra"          # o "manter conectado" do app, só no caminho /obra


def _agora():
    return datetime.now(timezone.utc)


def _app_url() -> str:
    from finance.email_sender import _app_url as _u
    return _u()


def fone_br(txt: str | None) -> str:
    """Só dígitos, com o 55 na frente — o formato que o wa.me exige. "(86)
    99999-8888" e "+55 86 99999-8888" viram "5586999998888". Vazio: ""."""
    dig = "".join(ch for ch in (txt or "") if ch.isdigit())
    if len(dig) in (10, 11):            # DDD + número, sem o país
        dig = "55" + dig
    return dig[:15]


# ── o cadastro ────────────────────────────────────────────────────────────
def cadastrar_mestre(pool, conta_id: int, nome: str, whatsapp: str) -> int:
    """O mestre na equipe, sem e-mail nem senha: entra pelo link mágico."""
    from contas import equipe as eq
    nome = " ".join((nome or "").split())[:80]
    fone = fone_br(whatsapp)
    if not nome:
        raise ValueError("Diga o nome do mestre.")
    if len(fone) < 12:
        raise ValueError("Diga o WhatsApp do mestre com DDD — é por ele que vai o link.")
    eq.garantir_tabela(pool)              # as colunas de login (whatsapp etc.) em runtime
    with pool.connection() as c:
        if c.execute("""select 1 from membros where conta_id=%s and papel='mestre'
                          and coalesce(whatsapp,'')=%s""", (conta_id, fone)).fetchone():
            raise ValueError("Já tem um mestre com esse WhatsApp na equipe.")
        mid = c.execute("""insert into membros (conta_id, nome, papel, ativo, whatsapp)
                           values (%s,%s,'mestre',true,%s) returning id""",
                        (conta_id, nome, fone)).fetchone()[0]
        c.commit()
    return mid


# ── o link ────────────────────────────────────────────────────────────────
def _mestre(c, conta_id: int, membro_id: int):
    return c.execute("""select nome, coalesce(whatsapp,''), ativo from membros
                         where id=%s and conta_id=%s and papel='mestre'""",
                     (membro_id, conta_id)).fetchone()


def gerar_link(pool, conta_id: int, membro_id: int, empresa: str = "") -> dict:
    """{link, whatsapp_url, nome} — o link novo (os anteriores morrem) e o
    wa.me com a mensagem pronta pro WhatsApp do mestre."""
    token = secrets.token_urlsafe(24)
    with pool.connection() as c:
        m = _mestre(c, conta_id, membro_id)
        if not m:
            raise ValueError("Esse membro não é mestre de obras desta empresa.")
        if not m[2]:
            raise ValueError("Esse mestre está desativado — reative antes de mandar o link.")
        c.execute("delete from cockpit_acesso where conta_id=%s and membro_id=%s",
                  (conta_id, membro_id))
        c.execute("""insert into cockpit_acesso (token, conta_id, membro_id, expira_em)
                     values (%s,%s,%s,%s)""",
                  (token, conta_id, membro_id, _agora() + timedelta(hours=TTL_HORAS)))
        c.commit()
    link = f"{_app_url()}/obra/entrar/{token}"
    primeiro = (m[0] or "").split(" ")[0]
    msg = (f"Oi {primeiro}! Este é o seu acesso ao app da obra"
           + (f" da {empresa}" if empresa else "") + ": é só tocar no link e já entra — "
           f"sem senha. Lá você tira a foto da etapa, marca o que ficou pronto e aponta o "
           f"material. {link}")
    fone = fone_br(m[1])
    return {"link": link, "nome": m[0], "fone": fone,
            "whatsapp_url": f"https://wa.me/{fone}?text={quote(msg)}" if fone else ""}


def validar_token(pool, token: str) -> dict | None:
    """O link ainda vale? {conta_id, membro_id, papel} ou None. Reusável no prazo
    (a prévia do WhatsApp abre o link antes do mestre)."""
    token = (token or "").strip()
    if not token:
        return None
    with pool.connection() as c:
        r = c.execute("""update cockpit_acesso set usado_em=coalesce(usado_em, now())
                          where token=%s and expira_em > now()
                      returning conta_id, membro_id""", (token,)).fetchone()
        if not r:
            return None
        m = c.execute("select papel, ativo from membros where id=%s and conta_id=%s",
                      (r[1], r[0])).fetchone()
        c.commit()
    if not m or m[0] != "mestre" or not m[1]:
        return None
    return {"conta_id": r[0], "membro_id": r[1], "papel": "mestre"}


# ── o celular lembrado ────────────────────────────────────────────────────
def lembrar(pool, conta_id: int, membro_id: int, aparelho: str = "") -> str | None:
    from finance import cockpit as ck        # a mesma tabela e o mesmo hash
    return ck.lembrar_criar(pool, conta_id, membro_id, aparelho)


def lembrete_valido(pool, token: str) -> dict | None:
    """O cookie do app ainda vale? Relê o membro: desativado ou com outro papel,
    não entra mais."""
    from finance import cockpit as ck
    token = (token or "").strip()
    if not token:
        return None
    try:
        with pool.connection() as c:
            r = c.execute("""update cockpit_lembrete set ultimo_uso=now()
                              where token_hash=%s and revogado_em is null
                          returning conta_id, membro_id""",
                          (ck._hash_lembrete(token),)).fetchone()
            if not r:
                return None
            m = c.execute("select papel, ativo from membros where id=%s and conta_id=%s",
                          (r[1], r[0])).fetchone()
            c.commit()
    except Exception:  # noqa: BLE001
        return None
    if not m or m[0] != "mestre" or not m[1]:
        return None
    return {"conta_id": r[0], "membro_id": r[1], "papel": "mestre"}


def esquecer(pool, token: str) -> None:
    from finance import cockpit as ck
    ck.lembrar_revogar(pool, token)
