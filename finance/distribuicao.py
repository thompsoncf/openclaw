"""Distribuição automática de leads por rodízio (fila) — nível EMPRESA.

Todo lead novo que entra SEM dono (contato novo no chip da empresa, resposta de
campanha, tráfego pago) é atribuído ao PRÓXIMO vendedor da fila (round-robin). O
agente IA segue dando o 1º toque; o vendedor é avisado, observa e assume quando
quiser. Nunca rouba um lead que já tem dono.

Config e fila ficam em `distribuicao` / `distribuicao_fila` (migração 132). O aviso
usa o e-mail do membro (sempre) e o WhatsApp dele (best-effort — fora da janela de
24h o Cloud API exige template, então pode não sair até haver um aprovado).
"""
from __future__ import annotations

import logging

_log = logging.getLogger(__name__)


def _garantir(c, conta_id: int) -> None:
    c.execute("insert into distribuicao (conta_id) values (%s) on conflict (conta_id) do nothing",
              (conta_id,))


def config(c, conta_id: int) -> dict:
    _garantir(c, conta_id)
    r = c.execute("select ativo, ponteiro, avisar, coalesce(aviso_template_sid,'') "
                  "from distribuicao where conta_id=%s", (conta_id,)).fetchone()
    return {"ativo": bool(r[0]), "ponteiro": int(r[1] or 0), "avisar": bool(r[2]),
            "aviso_template_sid": r[3]}


def fila_ids(c, conta_id: int) -> list[int]:
    """membro_ids elegíveis (na fila, ativos E não pausados), na ordem definida.
    `cockpit_pausado` deixa o vendedor pausar o rodízio pelo app sem sair da fila
    que o gestor montou — some da distribuição enquanto estiver pausado e volta
    na mesma posição quando despausa."""
    rows = c.execute(
        """select f.membro_id from distribuicao_fila f
             join membros m on m.id=f.membro_id and m.conta_id=f.conta_id
            where f.conta_id=%s and m.ativo and not coalesce(m.cockpit_pausado, false)
            order by f.ordem, f.membro_id""", (conta_id,)).fetchall()
    return [r[0] for r in rows]


def membros_fila_ui(c, conta_id: int) -> list[dict]:
    """Pro editor: membros ativos com login, os que estão na fila primeiro (na ordem),
    depois o resto. Cada um com {id, nome, whatsapp, na_fila}."""
    rows = c.execute(
        """select m.id, coalesce(nullif(m.nome,''), m.email), coalesce(m.whatsapp,''),
                  f.ordem
             from membros m
             left join distribuicao_fila f on f.membro_id=m.id and f.conta_id=m.conta_id
            where m.conta_id=%s and m.ativo and m.email is not null
            order by (f.ordem is null), f.ordem, m.id""", (conta_id,)).fetchall()
    return [{"id": r[0], "nome": r[1], "whatsapp": r[2], "na_fila": r[3] is not None} for r in rows]


def salvar(c, conta_id: int, ativo: bool, avisar: bool, membro_ids: list[int],
           aviso_template_sid: str | None = None) -> None:
    """Grava a config + a fila (ordem = posição na lista). Zera o ponteiro.
    aviso_template_sid=None mantém o atual; string (mesmo vazia) sobrescreve."""
    _garantir(c, conta_id)
    c.execute("update distribuicao set ativo=%s, avisar=%s, ponteiro=0, atualizado_em=now() where conta_id=%s",
              (bool(ativo), bool(avisar), conta_id))
    if aviso_template_sid is not None:
        c.execute("update distribuicao set aviso_template_sid=%s where conta_id=%s",
                  ((aviso_template_sid or "").strip()[:120] or None, conta_id))
    c.execute("delete from distribuicao_fila where conta_id=%s", (conta_id,))
    for i, mid in enumerate(membro_ids):
        c.execute("""insert into distribuicao_fila (conta_id, membro_id, ordem) values (%s,%s,%s)
                     on conflict (conta_id, membro_id) do update set ordem=excluded.ordem""",
                  (conta_id, mid, i))


def proximo_vendedor(c, conta_id: int) -> int | None:
    """Próximo da fila (round-robin), avançando o ponteiro. Atômico: trava a linha de
    `distribuicao` (FOR UPDATE) pra dois leads simultâneos não pegarem o mesmo. Devolve
    membro_id ou None (desligado / fila vazia). O % len absorve quem saiu da fila."""
    _garantir(c, conta_id)
    r = c.execute("select ativo, ponteiro from distribuicao where conta_id=%s for update",
                  (conta_id,)).fetchone()
    if not r or not r[0]:
        return None
    fila = fila_ids(c, conta_id)
    if not fila:
        return None
    idx = int(r[1] or 0) % len(fila)
    mid = fila[idx]
    c.execute("update distribuicao set ponteiro=%s, atualizado_em=now() where conta_id=%s",
              ((idx + 1) % len(fila), conta_id))
    return mid


def atribuir_se_sem_dono(c, conta_id: int, prospeccao_id: int) -> int | None:
    """Se o lead não tem vendedor, atribui o próximo da fila e marca a(s) conversa(s)
    dele. NUNCA rouba um lead que já tem dono. Devolve o membro_id atribuído (ou None).
    Roda dentro da transação do chamador (o commit é dele)."""
    row = c.execute("select vendedor_id from prospeccao where id=%s and conta_id=%s",
                    (prospeccao_id, conta_id)).fetchone()
    if not row or row[0] is not None:
        return None
    mid = proximo_vendedor(c, conta_id)
    if not mid:
        return None
    c.execute("update prospeccao set vendedor_id=%s, atualizado_em=now() where id=%s and conta_id=%s",
              (mid, prospeccao_id, conta_id))
    c.execute("""update conversas set responsavel_membro_id=%s
                  where conta_id=%s and prospeccao_id=%s and responsavel_membro_id is null""",
              (mid, conta_id, prospeccao_id))
    return mid


def avisar_vendedor(pool, conta_id: int, membro_id: int, empresa: str) -> None:
    """Best-effort: avisa o vendedor que caiu um lead. E-mail sempre (o membro tem
    e-mail); WhatsApp quando houver número (best-effort). Nunca levanta exceção —
    pensado pra rodar numa thread solta, sem travar o webhook."""
    try:
        with pool.connection() as c:
            cfg = config(c, conta_id)
            if not cfg["avisar"]:
                return
            m = c.execute("select coalesce(nullif(nome,''), email), email, coalesce(whatsapp,'') "
                          "from membros where id=%s and conta_id=%s", (membro_id, conta_id)).fetchone()
        if not m:
            return
        nome, email, wa = m
        emp = (empresa or "").strip() or "Um lead"
        titulo = f"🔥 Novo lead pra você: {emp}"
        try:
            from finance.email_sender import _app_url
            _cockpit = f"{_app_url()}/cockpit/login"
        except Exception:  # noqa: BLE001
            _cockpit = ""
        corpo = (f"{emp} caiu pra você no rodízio de leads. O agente já iniciou o "
                 "atendimento e você assume quando quiser."
                 + (f" Atenda pelo app: {_cockpit}" if _cockpit else " Abra o Zaq pra acompanhar."))
        if email and "@" in email:
            try:
                from finance import email_sender as es
                es.enviar_aviso(email, titulo, corpo, nome=nome)
            except Exception:  # noqa: BLE001
                pass
        if wa:
            _avisar_whatsapp(pool, conta_id, wa, cfg.get("aviso_template_sid") or "", emp, f"{titulo}\n{corpo}")
    except Exception as e:  # noqa: BLE001
        _log.info("distribuicao: aviso best-effort falhou (ok): %s", e)


def _avisar_whatsapp(pool, conta_id: int, numero: str, template_sid: str, empresa: str, texto: str) -> None:
    """WhatsApp do vendedor: se houver TEMPLATE configurado, dispara o template (funciona
    fora da janela de 24h) — a variável {{1}} é a empresa do lead. Sem template, tenta
    texto livre (só sai se o vendedor já falou com o número nas últimas 24h). Silencioso."""
    try:
        from finance import whatsapp_out as wo
        with pool.connection() as c2:
            if template_sid:
                wo.enviar_template(c2, conta_id, numero, template_sid, {"1": empresa})
            else:
                wo.enviar(c2, conta_id, numero, texto)
    except Exception:  # noqa: BLE001
        pass  # fora da janela 24h e sem template → a Meta bloqueia; segue só o e-mail
