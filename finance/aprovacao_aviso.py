"""Avisa o DONO que tem conta a pagar esperando a liberação dele.

Sem isto a fila de aprovação só existe pra quem abre a tela de Empresa — e quem
abre a tela de Empresa todo dia é justamente quem LANÇA, não quem libera. O dono
descobriria a fila quando alguém reclamasse do boleto vencido, que é tarde.

UM AVISO POR LOTE, NUNCA UM POR CONTA. O financeiro lança em rajada: numa
manhã a Prime cadastrou 30 títulos de uma vez. Trinta mensagens seguidas não são
trinta avisos — são um aviso e vinte e nove motivos pra desligar a notificação.
Então este módulo junta o que está aguardando e manda UMA mensagem com a lista.

O DEDUP É PELO CONJUNTO, e essa é a única parte com truque. Guardar "avisei às
10h" faria o aviso repetir a cada passada enquanto a fila não esvaziasse;
guardar "já avisei desta conta" calaria pra sempre a segunda que chegasse. A
marca é a ASSINATURA da fila (os ids, ordenados): enquanto for a mesma fila, é o
mesmo aviso; entrou uma conta nova, a assinatura muda e o dono é avisado de novo
— com a lista inteira, não só com a novidade, porque a pergunta que ele responde
é "o que falta eu liberar?" e não "o que chegou agora?".

Roda no ticker de fundo do web (~2 min), junto do wa_silencio e dos lembretes.
"""
from __future__ import annotations

import logging

from . import config_app, notificar

_log = logging.getLogger("openclaw.aprovacao_aviso")

# Janela em que o aviso pode sair (hora de Brasília). Fora daqui ele espera — o
# mesmo cuidado do wa_silencio: acordar o dono às 3h pra dizer que tem boleto
# esperando queima o alarme pro dia em que ele importar.
_HORA_INICIO = 8
_HORA_FIM = 20

# Quantas contas a mensagem detalha antes de virar "+N". A lista existe pra o
# dono decidir sem abrir o painel; vinte linhas não cabem em push nenhum.
_DETALHA = 5


def _brl(centavos: int) -> str:
    return f"R$ {centavos / 100:,.2f}".replace(",", "~").replace(".", ",").replace("~", ".")


def assinatura(ids) -> str:
    """A marca do dedup: o CONJUNTO da fila, não o instante do aviso."""
    return ",".join(str(i) for i in sorted(int(x) for x in ids))


def texto(nome_empresa: str, itens: list[dict]) -> tuple[str, str]:
    """(telegram, e-mail). Diz o valor total logo no começo: é o número que
    decide se o dono para o que está fazendo pra olhar agora."""
    total = sum(int(i["valor_centavos"] or 0) for i in itens)
    n = len(itens)
    cab = (f"💸 {n} conta{'s' if n != 1 else ''} a pagar "
           f"esperando sua liberação — {_brl(total)}")
    linhas = []
    for i in itens[:_DETALHA]:
        quem = f" · {i['quem']}" if i.get("quem") else ""
        venc = i["vencimento"].strftime("%d/%m") if i.get("vencimento") else "sem data"
        linhas.append(f"· {i['descricao']} — {_brl(int(i['valor_centavos'] or 0))}, "
                      f"vence {venc}{quem}")
    if n > _DETALHA:
        linhas.append(f"· +{n - _DETALHA} outra{'s' if n - _DETALHA != 1 else ''}")
    corpo = (f"{cab}\n\n" + "\n".join(linhas) +
             "\n\nAbra o painel em Empresa › Títulos pra liberar (dá pra liberar "
             "várias de uma vez).\n\nQuem lançou não fica travado: a baixa "
             "continua funcionando sem a sua liberação, e fica registrado que "
             "saiu sem autorização.")
    tg = (f"*{nome_empresa}*\n{cab}\n\n" + "\n".join(linhas) +
          "\n\n_Empresa › Títulos_ pra liberar.")
    return tg, corpo


def rodar(pool, hora_brt: int | None = None) -> int:
    """Uma passada: avisa os donos com fila nova. Devolve quantos avisos saíram.
    Best-effort — nunca levanta (roda no ticker)."""
    if hora_brt is None:
        from .agenda import agora_brt
        hora_brt = agora_brt().hour
    if not (_HORA_INICIO <= hora_brt < _HORA_FIM):
        return 0
    from . import empresa as emp
    enviados = 0
    try:
        with pool.connection() as c:
            contas = c.execute(
                """select distinct t.conta_id,
                          coalesce(nullif(ct.nome_fantasia,''), ct.nome, 'sua empresa'),
                          ct.email
                     from titulos t join contas ct on ct.id = t.conta_id
                    where t.tipo='pagar' and t.status='aberto'
                      and t.aprovacao='aguardando'""").fetchall()
    except Exception as e:  # noqa: BLE001 — banco sem a 195 não derruba o ticker
        _log.info("aprovacao_aviso: não deu pra listar as contas: %s", e)
        return 0
    for conta_id, nome, email in contas:
        try:
            itens = emp.aguardando_aprovacao(pool, conta_id)
            if not itens:
                continue
            chave = f"aprovacao_fila_{conta_id}"
            atual = assinatura(i["id"] for i in itens)
            if config_app.get_config(pool, chave) == atual:
                continue
            tg, corpo = texto(nome, itens)
            # grava ANTES de mandar, como o wa_silencio: se o envio falhar, é
            # melhor perder um aviso do que repetir o mesmo a cada 2 minutos.
            config_app.set_config(pool, chave, atual)
            ok = notificar.enviar_para_dono(pool, conta_id, tg)
            if not ok and email:
                try:
                    from .email_sender import enviar_aviso
                    ok = enviar_aviso(email, "Contas esperando sua liberação",
                                      corpo, nome)
                except Exception as e:  # noqa: BLE001
                    _log.info("aprovacao_aviso: e-mail falhou (conta %s): %s", conta_id, e)
            if ok:
                enviados += 1
                _log.info("aprovacao_aviso: avisei a conta %s (%d na fila)",
                          conta_id, len(itens))
        except Exception as e:  # noqa: BLE001
            _log.info("aprovacao_aviso: conta %s falhou: %s: %s",
                      conta_id, type(e).__name__, e)
            continue
    return enviados
