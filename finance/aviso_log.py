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


def novo_token() -> str:
    """Um token de push, aleatório e sem significado.

    É ele que autentica o clique de volta: o service worker roda sem sessão e sem
    cookie garantido, então o que prova "este clique é deste aviso" é conhecer o
    token. Por isso `token_urlsafe` e não o id da linha — id é sequencial, e
    sequencial se chuta.
    """
    import secrets
    return secrets.token_urlsafe(18)


def registrar(pool, conta_id: int, *, origem: str, canal: str, ok: bool,
              membro_id: int | None = None, destino: str = "",
              assunto: str = "", n_leads: int | None = None,
              motivo: str = "", sid: str = "", token: str = "") -> None:
    """Grava uma linha de envio. Best-effort inteiro — nunca levanta.

    `ok=False` sem `motivo` é aceito (às vezes o provedor só devolve False), mas é
    a linha que menos ajuda: quem chama deve passar o erro quando tiver.

    `sid` é o id da mensagem no WhatsApp e `token` é o segredo do push (migração
    282): são as duas pontas por onde o recibo e o clique voltam. Quem não tem,
    passa vazio — e-mail não tem nenhum dos dois, e a linha continua válida.
    """
    try:
        with pool.connection() as c:
            with c.transaction():
                c.execute(
                    """insert into aviso_envios
                         (conta_id, membro_id, origem, canal, destino, assunto,
                          n_leads, ok, motivo, sid, token)
                       values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                    (conta_id, membro_id, origem, canal,
                     (destino or "").strip() or None, (assunto or "").strip() or None,
                     n_leads, bool(ok), (motivo or "").strip()[:MOTIVO_MAX] or None,
                     (sid or "").strip() or None, (token or "").strip() or None))
    except Exception as e:  # noqa: BLE001 — testemunha não derruba o réu
        _log.warning("aviso_log: não deu pra registrar o envio (conta=%s, canal=%s): %s: %s",
                     conta_id, canal, type(e).__name__, e)


def marcar_recibo(c, sid: str, novo: str) -> None:
    """O recibo do WhatsApp chegou pra este aviso: carimba entrega e leitura.

    NUNCA REGRIDE, e a razão é a mesma de `aplicar_status_wa`: os recibos chegam
    fora de ordem, e sem a trava um 'lido' voltaria pra 'entregue' na tela meia
    hora depois. Aqui a trava sai de graça do `is null` — carimba uma vez e a
    marca fica.

    'lido' carimba a entrega junto quando ela falta: ler sem ter recebido não
    existe, e o recibo de entrega às vezes se perde numa reconexão do chip.

    Não faz commit — quem chama é dono da transação, como o resto do caminho do
    webhook."""
    if not sid or novo not in ("entregue", "lido"):
        return
    if novo == "entregue":
        c.execute("update aviso_envios set entregue_em=now() "
                  " where sid=%s and entregue_em is null", (sid,))
        return
    c.execute("""update aviso_envios
                    set lido_em = coalesce(lido_em, now()),
                        entregue_em = coalesce(entregue_em, now())
                  where sid=%s and lido_em is null""", (sid,))


def marcar_clique(pool, token: str) -> bool:
    """O vendedor TOCOU na notificação. Devolve se carimbou algo.

    Só o primeiro clique conta (`clicado_em is null`): a mesma notificação pode ser
    tocada duas vezes, e "clicou de novo" não é informação nova.

    Best-effort: token que não existe devolve False sem reclamar — é o que chega
    quando uma notificação velha sobrevive a um banco restaurado."""
    t = (token or "").strip()
    if not t or len(t) > 100:
        return False
    try:
        with pool.connection() as c:
            with c.transaction():
                cur = c.execute("update aviso_envios set clicado_em=now() "
                                " where token=%s and clicado_em is null", (t,))
                return bool(cur.rowcount)
    except Exception as e:  # noqa: BLE001
        _log.warning("aviso_log: não deu pra marcar o clique: %s: %s", type(e).__name__, e)
        return False


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


#: Quantos dias o card da tela olha pra trás. Trinta, escolhido pelo dono em
#: 18/09/2026: sete responde "esta semana funcionou" e trinta responde "dá pra
#: confiar nisso" — e a pergunta dele é a segunda.
DIAS_CARD = 30

#: O zero de cada canal. Existe pra a tela nunca precisar de `if canal in resumo`:
#: conta sem nenhum envio recebe a mesma forma, com tudo em zero.
_VAZIO = {"tentativas": 0, "ok": 0, "falhas": 0, "entregues": 0, "lidos": 0,
          "sem_recibo": 0, "clicados": 0}


def resumo(pool, conta_id: int, *, dias: int = DIAS_CARD,
           origem: str = "follow_up") -> dict:
    """Como os avisos chegaram, por canal, nos últimos `dias`.

    A ORIGEM FILTRA DE PROPÓSITO. O botão "Testar agora" grava com
    `origem='follow_up_teste'`, e um teste de canal não pode entrar na estatística
    de cobrança — foi pra isso que a origem nasceu separada.

    O QUE CADA CANAL SABE DIZER é diferente, e este resumo não finge o contrário:

    * WhatsApp tem `entregues` e `lidos` de verdade (recibo do aparelho).
    * Push tem `clicados` — não existe "viu" no navegador; existe o toque, e quem
      toca abriu o painel.
    * E-mail tem só `ok`: o SMTP aceitou. Abertura de e-mail só se mede com pixel,
      e o proxy do Gmail pré-carrega imagem — o número seria alto e falso, o que é
      pior que número nenhum. A tela mostra travessão, não zero.

    `sem_recibo` é coluna própria, e não some dentro de "não entregue": quem desliga
    a confirmação de leitura no WhatsApp nunca gera o 👀, e o aviso chegou do mesmo
    jeito.

    Tolerante: base sem a tabela (ou sem as colunas da 282) devolve a forma vazia,
    porque isto alimenta uma tela que não pode cair por causa de um card.
    """
    d = max(1, min(int(dias or DIAS_CARD), 365))
    fora = {"dias": d, "origem": origem, "por_vendedor": [],
            **{ch: dict(_VAZIO) for ch in CANAIS}}
    try:
        with pool.connection() as c:
            with c.transaction():
                linhas = c.execute(
                    """select canal,
                              count(*),
                              count(*) filter (where ok),
                              count(*) filter (where not ok),
                              count(*) filter (where entregue_em is not null),
                              count(*) filter (where lido_em is not null),
                              count(*) filter (where ok and entregue_em is null
                                                   and lido_em is null),
                              count(*) filter (where clicado_em is not null)
                         from aviso_envios
                        where conta_id=%s and origem=%s
                          and criado_em >= now() - make_interval(days => %s)
                        group by canal""", (conta_id, origem, d)).fetchall()
                vend = c.execute(
                    """select coalesce(nullif(m.nome,''), m.email, '(sem membro)'),
                              count(*),
                              count(*) filter (where e.lido_em is not null),
                              count(*) filter (where e.entregue_em is not null
                                                 and e.lido_em is null),
                              count(*) filter (where e.entregue_em is null
                                                 and e.lido_em is null)
                         from aviso_envios e
                         left join membros m on m.id = e.membro_id
                        where e.conta_id=%s and e.origem=%s and e.canal='whatsapp'
                          and e.ok
                          and e.criado_em >= now() - make_interval(days => %s)
                        group by 1 order by 2 desc, 1""",
                    (conta_id, origem, d)).fetchall()
    except Exception as e:  # noqa: BLE001
        _log.warning("aviso_log: resumo falhou (conta=%s): %s: %s",
                     conta_id, type(e).__name__, e)
        return fora
    for r in linhas:
        fora[r[0]] = {"tentativas": r[1], "ok": r[2], "falhas": r[3],
                      "entregues": r[4], "lidos": r[5], "sem_recibo": r[6],
                      "clicados": r[7]}
    fora["por_vendedor"] = [{"quem": v[0], "total": v[1], "lidos": v[2],
                             "entregues": v[3], "sem_recibo": v[4]} for v in vend]
    return fora
