"""O registro de aviso enviado — quem recebeu o quê, por qual canal, e se saiu.

NASCEU DE UMA PERGUNTA SEM RESPOSTA (17/09/2026). No primeiro dia do follow-up
ligado, o dono perguntou "já mandou os e-mails pros vendedores? me traz os logs".
Dava pra provar o que foi COBRADO — `funil_avisos`, 129 linhas às 08:58 — e não o
que SAIU. O `email_sender` escreve no log da aplicação, o `enviar_push` devolve um
número que ninguém guardava, e o `notificar` engole exceção pra não derrubar o
poller. Quando um vendedor diz "não recebi", não havia prova nenhuma.

AS DUAS REGRAS DESTE MÓDULO, e as duas vêm de o aviso ser best-effort:

1. **Registrar nunca pode quebrar o envio.** Um `except` largo em volta de tudo, e
   falha aqui só vira log. O aviso é o produto; o registro é a testemunha — e
   testemunha que derruba o réu não serve.
2. **Registra o RESULTADO, não a intenção.** `ok=False` com o motivo é a linha mais
   valiosa da tabela: é ela que responde "por que o Thiago não recebeu". Gravar só
   sucesso seria o mesmo buraco de antes, com mais trabalho.

Ver a migração 276 pra por que a tabela não tem chave única.
"""
from __future__ import annotations

import logging

_log = logging.getLogger("openclaw.aviso_log")

#: Os canais que existem hoje. Não é check no banco de propósito: um canal novo
#: (Telegram, SMS) não pode exigir migração pra ser registrado — o que não pode é
#: o canal ficar FORA do registro.
CANAIS = ("email", "push", "whatsapp")

#: Teto do motivo. A mensagem de erro de SMTP pode vir com o corpo inteiro do
#: e-mail dentro; o que serve pra diagnóstico são os primeiros caracteres.
MOTIVO_MAX = 300


def registrar(pool, conta_id: int, *, origem: str, canal: str, ok: bool,
              membro_id: int | None = None, destino: str = "",
              assunto: str = "", n_leads: int | None = None,
              motivo: str = "") -> None:
    """Grava uma linha de envio. Best-effort inteiro — nunca levanta.

    `ok=False` sem `motivo` é aceito (às vezes o provedor só devolve False), mas é
    a linha que menos ajuda: quem chama deve passar o erro quando tiver.
    """
    try:
        with pool.connection() as c:
            with c.transaction():
                c.execute(
                    """insert into aviso_envios
                         (conta_id, membro_id, origem, canal, destino, assunto,
                          n_leads, ok, motivo)
                       values (%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (conta_id, membro_id, origem, canal,
                     (destino or "").strip() or None, (assunto or "").strip() or None,
                     n_leads, bool(ok), (motivo or "").strip()[:MOTIVO_MAX] or None))
    except Exception as e:  # noqa: BLE001 — testemunha não derruba o réu
        _log.warning("aviso_log: não deu pra registrar o envio (conta=%s, canal=%s): %s: %s",
                     conta_id, canal, type(e).__name__, e)


def ultimos(pool, conta_id: int, *, limite: int = 200,
            membro_id: int | None = None, so_falha: bool = False) -> list[dict]:
    """O que saiu nesta conta, do mais novo pro mais velho.

    Tolerante: base sem a tabela devolve lista vazia em vez de derrubar a tela que
    for mostrar isto.
    """
    onde = ["e.conta_id = %s"]
    args: list = [conta_id]
    if membro_id:
        onde.append("e.membro_id = %s")
        args.append(membro_id)
    if so_falha:
        onde.append("not e.ok")
    args.append(max(1, min(int(limite or 200), 1000)))
    try:
        with pool.connection() as c:
            with c.transaction():
                rows = c.execute(
                    f"""select e.criado_em, e.origem, e.canal, e.destino, e.assunto,
                               e.n_leads, e.ok, e.motivo,
                               coalesce(nullif(m.nome,''), m.email, '(sem membro)')
                          from aviso_envios e
                          left join membros m on m.id = e.membro_id
                         where {' and '.join(onde)}
                         order by e.criado_em desc, e.id desc limit %s""",
                    tuple(args)).fetchall()
    except Exception as e:  # noqa: BLE001
        _log.warning("aviso_log: leitura falhou (conta=%s): %s: %s",
                     conta_id, type(e).__name__, e)
        return []
    return [{"quando": r[0], "origem": r[1], "canal": r[2], "destino": r[3],
             "assunto": r[4], "n_leads": r[5], "ok": r[6], "motivo": r[7],
             "quem": r[8]} for r in rows]
