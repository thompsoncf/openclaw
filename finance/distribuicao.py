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


#: O texto que a empresa recebe pronto ao ligar o aviso por WhatsApp. Existe pra que
#: ligar o interruptor já sirva pra alguma coisa — campo vazio obrigaria a escrever
#: antes de ver o recurso funcionar, e quem liga quer ver funcionar.
TEXTO_ZAP_PADRAO = (
    "🔥 {vendedor}, caiu um lead pra você!\n\n"
    "*{lead}* acabou de chamar no WhatsApp da {empresa}.\n"
    "\"{primeira_mensagem}\"\n\n"
    "Atenda por aqui: {link}"
)


def config(c, conta_id: int) -> dict:
    _garantir(c, conta_id)
    r = c.execute("select ativo, ponteiro, avisar, coalesce(aviso_template_sid,''), "
                  "coalesce(aviso_zap,false), aviso_zap_chip_id, coalesce(aviso_zap_texto,'') "
                  "from distribuicao where conta_id=%s", (conta_id,)).fetchone()
    return {"ativo": bool(r[0]), "ponteiro": int(r[1] or 0), "avisar": bool(r[2]),
            "aviso_template_sid": r[3],
            "aviso_zap": bool(r[4]), "aviso_zap_chip_id": r[5],
            # o padrão entra na LEITURA, não na coluna: assim melhorar o texto de
            # fábrica alcança quem nunca editou, sem migração de dados.
            "aviso_zap_texto": r[6] or TEXTO_ZAP_PADRAO}


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
           aviso_template_sid: str | None = None, *,
           aviso_zap: bool | None = None, aviso_zap_chip_id=None,
           aviso_zap_texto: str | None = None) -> None:
    """Grava a config + a fila (ordem = posição na lista). Zera o ponteiro.
    aviso_template_sid=None mantém o atual; string (mesmo vazia) sobrescreve.

    Os três `aviso_zap_*` seguem a mesma regra do template — None mantém — porque
    esta função é chamada de dois lugares: o formulário da tela, que manda tudo, e
    os testes/rotinas que só querem mexer na fila. Sobrescrever com o default
    apagaria a configuração de quem não mandou o campo."""
    _garantir(c, conta_id)
    c.execute("update distribuicao set ativo=%s, avisar=%s, ponteiro=0, atualizado_em=now() where conta_id=%s",
              (bool(ativo), bool(avisar), conta_id))
    if aviso_template_sid is not None:
        c.execute("update distribuicao set aviso_template_sid=%s where conta_id=%s",
                  ((aviso_template_sid or "").strip()[:120] or None, conta_id))
    if aviso_zap is not None:
        c.execute("update distribuicao set aviso_zap=%s where conta_id=%s",
                  (bool(aviso_zap), conta_id))
    if aviso_zap_chip_id is not None:
        # string vazia da tela = "chip principal", que é nulo na coluna
        try:
            chip = int(aviso_zap_chip_id) or None
        except (TypeError, ValueError):
            chip = None
        c.execute("update distribuicao set aviso_zap_chip_id=%s where conta_id=%s",
                  (chip, conta_id))
    if aviso_zap_texto is not None:
        c.execute("update distribuicao set aviso_zap_texto=%s where conta_id=%s",
                  ((aviso_zap_texto or "").strip()[:900] or None, conta_id))
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


#: Quanto o texto do cliente pode ocupar no aviso. É o que faz o vendedor decidir se
#: corre ou não; inteiro viraria um paredão no WhatsApp de quem só quer o resumo.
PRIMEIRA_MAX = 120

#: Intervalo mínimo entre dois avisos por WhatsApp pro MESMO vendedor. O que passar
#: disso continua indo por e-mail e push — o lead não fica sem aviso, só não toca o
#: celular de novo. Ver o comentário do freio na migração 185.
ZAP_INTERVALO_MIN = 2


def _texto_zap(modelo: str, *, lead: str, vendedor: str, empresa: str,
               link: str, primeira: str) -> str:
    """Troca as variáveis do modelo que a empresa escreveu.

    `str.format` está fora de questão: o texto é digitado por gente, e uma chave
    solta ("desconto de 50% {" ou um emoji com chave) levantaria KeyError/ValueError
    dentro da thread do aviso. Substituição literal não tem como falhar.

    Variável desconhecida fica como está, à vista — some no meio do texto seria pior:
    quem escreveu `{nome}` em vez de `{lead}` precisa ver que não funcionou."""
    valores = {
        "{lead}": lead or "Um lead",
        "{vendedor}": (vendedor or "").split()[0] if vendedor else "você",
        "{empresa}": empresa or "sua empresa",
        "{link}": link or "",
        "{primeira_mensagem}": primeira or "",
    }
    for chave, valor in valores.items():
        modelo = modelo.replace(chave, valor)
    return modelo.strip()


def _pode_zap_agora(c, conta_id: int, membro_id: int) -> bool:
    """O freio, decidido no próprio UPDATE com RETURNING.

    Dois webhooks simultâneos disputam a linha e só um sai vencedor — ler o carimbo
    e gravar depois deixaria os dois passarem, que é justamente o caso da rajada.
    Mesmo desenho do `push_avisado_em` em finance/cockpit.py."""
    return c.execute(
        """update membros set aviso_zap_em=now()
            where id=%s and conta_id=%s
              and (aviso_zap_em is null
                   or aviso_zap_em < now() - make_interval(mins => %s))
         returning id""",
        (membro_id, conta_id, ZAP_INTERVALO_MIN)).fetchone() is not None


def avisar_vendedor(pool, conta_id: int, membro_id: int, empresa: str,
                    *, retomada: bool = False, lead_id: int | None = None,
                    primeira: str = "") -> None:
    """Best-effort: avisa o vendedor que caiu um lead. E-mail sempre (o membro tem
    e-mail); WhatsApp quando houver número (best-effort). Nunca levanta exceção —
    pensado pra rodar numa thread solta, sem travar o webhook.

    `retomada` = a conversa já existia com histórico e o cliente voltou a falar; o lead
    é novo pro FUNIL, não pra empresa. Muda só o texto — quem volta também precisa de
    dono, então a distribuição é a mesma. Sem isso o vendedor lia "🔥 Novo lead" pra
    cliente que ele atende há meses: aconteceu 10 vezes numa tarde depois de um
    re-pareamento (ver o bloco da órfã em _wa_inbound_conversa). Fica com padrão False
    porque os outros dois chamadores — o botão "Levar para o lead" e o inbound de
    campanha — tratam de lead novo mesmo.

    `lead_id` monta o link direto pra ficha. Sem ele o aviso mandava pro login e o
    vendedor tinha que caçar de quem era — defeito que o próprio código já apontava
    em finance/cockpit.py ("o push do rodízio manda pra /cockpit e obriga o vendedor
    a procurar"), e que só dava pra consertar recebendo o id aqui.

    `primeira` é a mensagem que o cliente mandou, cortada em PRIMEIRA_MAX. Só vai
    pro WhatsApp, onde cabe: no assunto do e-mail viraria ruído."""
    try:
        with pool.connection() as c:
            cfg = config(c, conta_id)
            if not cfg["avisar"]:
                return
            m = c.execute("select coalesce(nullif(nome,''), email), email, coalesce(whatsapp,'') "
                          "from membros where id=%s and conta_id=%s", (membro_id, conta_id)).fetchone()
            # Tudo daqui pra baixo é do WhatsApp, e SÓ roda quando ele está ligado.
            # Não é economia de consulta: é blindagem. E-mail e push são o aviso que
            # não pode faltar; uma consulta a mais no caminho comum seria uma chance a
            # mais de o `except` lá embaixo engolir os três de uma vez.
            casa, zap_liberado = "", False
            if cfg["aviso_zap"] and not retomada and m and (m[2] or "").strip():
                # try PRÓPRIO, e não é decoração: sem ele um erro daqui sobe pro try de
                # fora e leva junto o e-mail e o push, que ainda nem tinham saído. O
                # aviso que não pode faltar é o e-mail; o WhatsApp é o extra.
                try:
                    # nome da EMPRESA (a variável {empresa} do texto), não o do lead
                    _cta = c.execute(
                        "select coalesce(nullif(nome_fantasia,''), nullif(nome,''), '') "
                        "from contas where id=%s", (conta_id,)).fetchone()
                    casa = (_cta[0] if _cta else "") or ""
                    # o freio carimba na hora que decide. Só chega aqui quem ia receber
                    # mesmo — carimbar antes calaria o próximo aviso à toa.
                    zap_liberado = _pode_zap_agora(c, conta_id, membro_id)
                except Exception as e:  # noqa: BLE001
                    _log.warning("distribuicao: preparo do aviso por WhatsApp falhou "
                                 "(conta=%s) — segue por e-mail e push: %s", conta_id, e)
                    zap_liberado = False
            c.commit()
        if not m:
            return
        nome, email, wa = m
        emp = (empresa or "").strip() or "Um lead"
        titulo = (f"💬 Cliente voltou a falar: {emp}" if retomada
                  else f"🔥 Novo lead pra você: {emp}")
        try:
            from finance.email_sender import _app_url
            # a ficha do lead, não a porta de entrada: quem recebe o aviso quer chegar
            # NAQUELE lead. Sem id (chamador antigo) volta pro login, como era.
            _cockpit = f"{_app_url()}/cockpit/lead/{lead_id}" if lead_id else f"{_app_url()}/cockpit/login"
        except Exception:  # noqa: BLE001
            _cockpit = ""
        _abertura = (f"{emp} mandou mensagem de novo e ficou com você. A conversa é "
                     "antiga e o histórico está todo lá no inbox."
                     if retomada else
                     f"{emp} caiu pra você no rodízio de leads. O agente já iniciou o "
                     "atendimento e você assume quando quiser.")
        corpo = (_abertura
                 + (f" Atenda pelo app: {_cockpit}" if _cockpit else " Abra o Zaq pra acompanhar."))
        # PUSH no app do vendedor (Cockpit/PWA) — chega na hora, mesmo com o app
        # fechado. Best-effort; respeita o toggle de push do próprio vendedor.
        try:
            from finance import cockpit as _ck
            # o push tem que dizer a MESMA coisa que o e-mail: dois avisos da mesma
            # mensagem se contradizendo é pior que um só.
            _ck.enviar_push(pool, conta_id, membro_id,
                            f"💬 Voltou a falar: {emp}" if retomada else f"🔥 Novo lead: {emp}",
                            "Conversa antiga · toque pra ver" if retomada
                            else "Caiu no rodízio · toque pra atender",
                            f"/cockpit/lead/{lead_id}" if lead_id else "/cockpit")
        except Exception:  # noqa: BLE001
            pass
        if email and "@" in email:
            try:
                from finance import email_sender as es
                es.enviar_aviso(email, titulo, corpo, nome=nome)
            except Exception:  # noqa: BLE001
                pass
        # WHATSAPP. Três portões, nesta ordem, e cada um por um motivo:
        #   1. `aviso_zap` desligado — é o padrão; mandar no WhatsApp de alguém não
        #      começa ligado.
        #   2. `retomada` — cliente que volta já está no inbox do vendedor, e é
        #      justamente o caso que virou enxurrada na reimportação de 22/08.
        #   3. o freio por vendedor (já decidido lá em cima, com o carimbo gravado).
        # Falhar qualquer um deles não cala o aviso: e-mail e push já saíram.
        if zap_liberado:
            texto = _texto_zap(
                cfg["aviso_zap_texto"], lead=emp, vendedor=nome, empresa=casa,
                link=_cockpit,
                primeira=(primeira or "").strip()[:PRIMEIRA_MAX])
            _avisar_whatsapp(pool, conta_id, wa, cfg.get("aviso_template_sid") or "",
                             emp, texto, chip_id=cfg.get("aviso_zap_chip_id"))
    except Exception as e:  # noqa: BLE001
        _log.info("distribuicao: aviso best-effort falhou (ok): %s", e)


def _avisar_whatsapp(pool, conta_id: int, numero: str, template_sid: str, empresa: str,
                     texto: str, *, chip_id=None) -> None:
    """WhatsApp do vendedor: se houver TEMPLATE configurado, dispara o template (funciona
    fora da janela de 24h) — a variável {{1}} é a empresa do lead. Sem template, tenta
    texto livre (só sai se o vendedor já falou com o número nas últimas 24h). Silencioso.

    `chip_id` diz POR QUAL número o aviso sai, e só o provedor 'qr' lê (ver
    whatsapp_out.enviar). Nulo = chip principal, que era o comportamento fixo até
    aqui. Numa empresa com chip de campanha, mandar o aviso interno por ele deixa o
    número que atende cliente fora disso.

    O template ignora o chip de propósito: ele só existe em Twilio/Cloud, onde não há
    chip nenhum pra escolher — e é lá que a janela de 24h obriga a usar template. No
    QR, texto livre chega a qualquer hora, que é o que faz esta tela poder existir."""
    try:
        from finance import whatsapp_out as wo
        with pool.connection() as c2:
            if template_sid:
                r = wo.enviar_template(c2, conta_id, numero, template_sid, {"1": empresa})
            else:
                r = wo.enviar(c2, conta_id, numero, texto, chip_id=chip_id)
        # fora da janela 24h e sem template → a Meta bloqueia; segue só o e-mail.
        # Mas isso PRECISA aparecer no log: um `pass` aqui escondia o vendedor não
        # estar recebendo aviso nenhum de lead novo.
        if not (r or {}).get("ok"):
            _log.warning("distribuicao: aviso por WhatsApp não saiu (conta=%s, template=%s): %s",
                         conta_id, bool(template_sid),
                         (r or {}).get("erro") or (r or {}).get("msg") or "?")
    except Exception as e:  # noqa: BLE001
        _log.warning("distribuicao: aviso por WhatsApp falhou (conta=%s): %s", conta_id, e)
