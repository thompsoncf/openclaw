"""Quem lê o conteúdo clínico, e o registro de cada leitura (prontuário, fase 1).

Desenho aprovado: docs/mockups/clinica_prontuario.html, seção 01 ("a regra que muda"),
11.8 (o registro de acesso) e 15 (parte 1). Migração 421.

  - O acesso NÃO vem do papel no sistema (dono, gestor, recepção). Vem de ser
    PROFISSIONAL DE SAÚDE da clínica: o cadastro dele em Configurar › Profissionais, com
    o conselho preenchido, o login ligado e o acesso clínico LIBERADO PELO DONO
    (`acesso_clinico`). O gestor não se libera; trocar o login ou o conselho desliga.
  - O médico que é o DONO da conta entra com o login do dono (que não é membro da
    equipe): o cadastro dele marca `e_dono`.
  - O SUPORTE do Zaq ("entrar como") nunca lê: a sessão de suporte não tem leitor.
  - Toda leitura fica no REGISTRO DE ACESSO (`clinica_acessos`, só insere): quem, quando,
    de qual paciente e o quê — nunca o conteúdo. O dono e o gestor veem o registro
    inteiro; o profissional, o dos pacientes que ele atendeu.
  - O agente do WhatsApp nunca importa este módulo (tests/test_clinica_acesso_clinico.py).
"""
from __future__ import annotations

import logging
from datetime import datetime, timedelta

from finance import clinica_agenda as ca

_log = logging.getLogger("clinica.acesso_clinico")


def _suporte(session) -> bool:
    return bool(session.get("suporte_acesso_id") or session.get("suporte_de"))


def leitor(c, conta_id: int, session) -> dict | None:
    """{'profissional_id', 'nome', 'membro_id'} de quem está logado, se pode ler o
    conteúdo clínico; senão None."""
    if _suporte(session):
        return None
    membro = session.get("membro_id")
    papel = session.get("papel") or "dono"
    try:
        with c.transaction():
            if membro:
                # o login é o MESMO que o dono liberou, o membro está ativo, e o cadastro
                # não é o do dono (o dono lê pelo login de dono)
                r = c.execute(
                    """select p.id, p.nome from clinica_profissionais p
                         join membros m on m.id = p.membro_id and m.conta_id = p.conta_id
                                       and coalesce(m.ativo, true)
                        where p.conta_id=%s and p.membro_id=%s and p.ativo and p.acesso <> 'sem_login'
                          and p.acesso_clinico and p.acesso_clinico_membro_id = p.membro_id
                          and not p.e_dono and coalesce(p.conselho, '') <> ''
                        order by p.id limit 1""",
                    (conta_id, membro)).fetchone()
            elif papel == "dono":
                r = c.execute(
                    """select id, nome from clinica_profissionais
                        where conta_id=%s and e_dono and ativo and acesso_clinico
                          and coalesce(conselho, '') <> '' limit 1""", (conta_id,)).fetchone()
            else:
                r = None
    except Exception:  # noqa: BLE001 — base sem a 421: ninguém lê
        return None
    if not r:
        return None
    cad = [r[0]]
    if membro:
        cad = [x[0] for x in c.execute(
            """select p.id from clinica_profissionais p where p.conta_id=%s and p.membro_id=%s and p.ativo
                 and p.acesso_clinico and p.acesso_clinico_membro_id = p.membro_id and not p.e_dono""",
            (conta_id, membro)).fetchall()] or cad
    return {"profissional_id": r[0], "nome": r[1], "membro_id": membro, "cadastros": cad}


def _papel_atual(c, conta_id: int, session) -> str | None:
    """O papel de AGORA (o gestor rebaixado, ou o membro desativado, não fica com o da
    sessão antiga). O dono entra sem membro."""
    membro = session.get("membro_id")
    if not membro:
        return session.get("papel") or "dono"
    r = c.execute("select papel, coalesce(ativo, true) from membros where id=%s and conta_id=%s",
                  (membro, conta_id)).fetchone()
    return r[0] if r and r[1] else None


def registrar(c, conta_id: int, quem: dict, cliente_id: int | None, o_que: str, ip: str = "") -> None:
    """Uma linha no registro de acesso. Na transação de quem chama (a leitura e o registro
    vão juntos)."""
    c.execute("""insert into clinica_acessos (conta_id, cliente_id, profissional_id, membro_id, quem, o_que, ip)
                 values (%s,%s,%s,%s,%s,%s,%s)""",
              (conta_id, cliente_id, quem.get("profissional_id"), quem.get("membro_id"),
               (quem.get("nome") or "")[:120], o_que[:120], (ip or "")[:60]))


class SemRegistro(Exception):
    """A leitura não pôde ser registrada: nada é mostrado (e a tela diz isso)."""


def ler(c, conta_id: int, session, cliente_id: int, o_que: str, ip: str = "") -> dict | None:
    """O portão de toda leitura clínica: devolve o leitor (e registra a leitura), None se
    não pode ler, ou levanta `SemRegistro` se pode mas o registro falhou."""
    q = leitor(c, conta_id, session)
    if not q:
        return None
    try:
        with c.transaction():
            registrar(c, conta_id, q, cliente_id, o_que, ip)
    except Exception as e:  # noqa: BLE001 — sem registro, não lê
        _log.warning("acesso clínico: não registrou a leitura (conta %s)", conta_id, exc_info=True)
        raise SemRegistro() from e
    return q


# ------------------------------------------------------------------ quem libera

def estado(c, conta_id: int) -> dict[int, dict]:
    """{profissional_id: {'acesso_clinico', 'e_dono', 'desde'}} (base sem a 421: vazio)."""
    try:
        with c.transaction():
            rows = c.execute(
                """select p.id, p.acesso_clinico, p.e_dono, p.acesso_clinico_em,
                          coalesce(nullif(m.nome,''), m.email, ''), coalesce(m.email, ''),
                          p.acesso_clinico_membro_id = p.membro_id
                     from clinica_profissionais p
                     left join membros m on m.id = p.membro_id and m.conta_id = p.conta_id
                    where p.conta_id=%s""", (conta_id,)).fetchall()
    except Exception:  # noqa: BLE001
        return {}
    return {r[0]: {"acesso_clinico": bool(r[1] and (r[2] or r[6])), "e_dono": r[2],
                   "desde": ca.local(r[3]) if r[3] else None, "login": r[4], "email": r[5]}
            for r in rows}


def liberar(c, conta_id: int, profissional_id: int, *, acesso: bool, e_dono: bool, por: str) -> str | None:
    """Só o DONO chama (a rota confere). Liga/desliga a leitura e marca quem é o dono."""
    r = c.execute("""select p.nome, coalesce(p.conselho,''), p.membro_id, p.acesso,
                             coalesce(nullif(m.nome,''), m.email, '')
                        from clinica_profissionais p
                        left join membros m on m.id = p.membro_id and m.conta_id = p.conta_id
                       where p.id=%s and p.conta_id=%s and p.ativo""", (profissional_id, conta_id)).fetchone()
    if not r:
        return "Profissional não encontrado."
    nome, conselho, membro, acesso_zaq, login = r
    if acesso and not conselho.strip():
        return "Pra ler o prontuário, o profissional precisa do conselho e número (CRM, CREFITO…)."
    if acesso and not e_dono and (not membro or acesso_zaq == "sem_login"):
        return "Pra ler o prontuário, ligue o login do profissional (Acesso ao Zaq e quem da equipe é)."
    try:
        with c.transaction():
            if e_dono:
                c.execute("update clinica_profissionais set e_dono=false where conta_id=%s and id <> %s and e_dono",
                          (conta_id, profissional_id))
            # a liberação fica presa ao login de AGORA (e o do dono não é login de membro)
            c.execute("""update clinica_profissionais
                            set acesso_clinico=%s, e_dono=%s,
                                acesso_clinico_em = case when %s then now() else null end,
                                acesso_clinico_membro_id = case when %s and not %s then membro_id end
                          where id=%s and conta_id=%s""",
                      (acesso, e_dono, acesso, acesso, e_dono, profissional_id, conta_id))
    except Exception as e:  # noqa: BLE001 — dois "sou eu" ao mesmo tempo
        if "ux_clinica_profissionais_dono" in str(e):
            return "Outro profissional acabou de ser marcado como o dono. Recarregue e confira."
        raise
    quem = " (dono da conta)" if e_dono else (f" (login: {login})" if login and acesso else "")
    registrar(c, conta_id, {"nome": por}, None,
              f"{'liberou' if acesso else 'tirou'} o prontuário de {nome}{quem}")
    return None


# ------------------------------------------------------------------ o registro

def pode_ver_registro(c, conta_id: int, session) -> tuple[bool, dict | None]:
    """(vê tudo, leitor): dono e gestor veem tudo; o profissional, os pacientes dele."""
    if _suporte(session):
        return False, None
    papel = _papel_atual(c, conta_id, session)
    if papel is None:
        return False, None
    if papel in ("dono", "gestor"):
        return True, leitor(c, conta_id, session)
    return False, leitor(c, conta_id, session)


def registro(c, conta_id: int, agora: datetime, *, dias: int = 7, cliente_id: int | None = None,
             profissional_id: int | None = None, so_pacientes_de: int | None = None) -> list[dict]:
    """As leituras dos últimos `dias` (o conteúdo nunca). `so_pacientes_de`: só pacientes
    que tiveram agendamento com esse profissional."""
    cond, args = ["a.criado_em >= %s"], [agora - timedelta(days=dias)]
    if cliente_id:
        cond.append("a.cliente_id=%s")
        args.append(cliente_id)
    if profissional_id:
        cond.append("a.profissional_id=%s")
        args.append(profissional_id)
    if so_pacientes_de:
        # os pacientes de TODOS os cadastros do mesmo login (o profissional em dois cadastros)
        cond.append("""a.cliente_id in (select e.cliente_id from eventos_agenda e
                                          where e.conta_id = a.conta_id and e.profissional_id in (
                                            select p2.id from clinica_profissionais p2, clinica_profissionais p1
                                             where p1.id = %s and p1.conta_id = a.conta_id
                                               and p2.conta_id = p1.conta_id
                                               and (p2.id = p1.id or (p1.membro_id is not null
                                                                      and p2.membro_id = p1.membro_id))))""")
        args.append(so_pacientes_de)
    try:
        with c.transaction():
            rows = c.execute(
                """select a.criado_em, a.quem, a.o_que, a.cliente_id, coalesce(p.nome, k.nome), a.profissional_id
                     from clinica_acessos a
                     left join clientes k on k.id = a.cliente_id and k.dono_id = a.conta_id
                     left join pessoas p on p.id = k.pessoa_id
                    where a.conta_id=%s and """ + " and ".join(cond) + " order by a.criado_em desc limit 500",
                [conta_id] + args).fetchall()
    except Exception:  # noqa: BLE001 — base sem a 421
        return []
    return [{"quando": ca.local(r[0]), "quem": r[1], "o_que": r[2], "cliente_id": r[3], "paciente": r[4] or "",
             "profissional_id": r[5]} for r in rows]

