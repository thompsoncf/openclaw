"""Avisa o DONO quando o WhatsApp da empresa para de receber.

O painel já mostrava "conectado · sem receber há 2h · conferir o chip" na faixa do
chip. Só que aviso em tela só serve pra quem está com a tela aberta: em 17/08/2026 a
Confeitaria Doce Mell passou mais de duas horas exatamente assim — dizendo
"conectado", sem uma mensagem entrando — e ninguém soube. A sessão tinha sido
repareada do zero e o cofre de chaves do Signal estava se reconstruindo devagar; as
mensagens chegavam como frame e não viravam conversa. Nenhum alarme tocou.

Este módulo é a outra metade: quem não está olhando a tela também precisa saber.
Roda no ticker de fundo do web (~2 min), junto dos lembretes.

TRÊS CUIDADOS, porque isto manda mensagem pra cliente de verdade:

1. Só conta ATIVA. Empresa que recebe pouco fica horas quieta sem nada de errado —
   o alarme só vale pra quem tem movimento (ver _CONTA_ATIVA_MIN).
2. Só em horário comercial. Silêncio às 3h da manhã é o esperado, e acordar o dono
   pra dizer isso queimaria o alarme pra quando ele importar.
3. Um aviso por EPISÓDIO. O dedup guarda QUAL era a última mensagem recebida na
   hora do aviso: enquanto for a mesma, o episódio é o mesmo e ninguém repete. Se
   a conta voltar a receber e emudecer de novo, a última mensagem mudou — episódio
   novo, aviso novo. Sem tarefa de limpeza e sem contagem de tempo pra errar.
"""
from __future__ import annotations

import logging

from . import config_app, notificar

_log = logging.getLogger("openclaw.wa_silencio")

# A partir de quantos minutos sem receber o silêncio vira alarme. A faixa do painel
# acende com 60min (ver _WA_SILENCIO_MIN em web/painel_prospeccao.py); aqui é mais
# alto de propósito — a tela é passiva e pode exagerar um pouco, o aviso que chega
# no bolso do dono não pode. 100min também dá tempo do vigia do wa-qr religar uma
# sessão muda sozinho (ele tenta em até ~45min) antes de a gente incomodar alguém.
_SILENCIO_MIN = 100

# Quantas recebidas nos últimos 7 dias pra conta valer alarme. Abaixo disso o
# silêncio é o estado NORMAL dela, e alarme que toca no normal ninguém escuta.
_CONTA_ATIVA_MIN = 20

# Janela em que o aviso pode sair (hora de Brasília). Fora daqui ele espera.
_HORA_INICIO = 8
_HORA_FIM = 20

# Minutos ÚTEIS em 7 dias (12h por dia): cliente escreve em horário comercial, então
# dividir pelas 168h cheias faria toda conta parecer mais parada do que é.
_MIN_UTEIS_7D = 7 * 12 * 60

# Quantas vezes o intervalo NORMAL da conta o silêncio precisa passar. O limiar fixo
# sozinho não serve: medido em 17/08, com 100min fixos a conta ZAQ (108 recebidas em
# 7 dias, ~47min entre mensagens) dispararia alarme depois de 165min parada — que é o
# ritmo dela. Já a Doce Mell (468 recebidas, ~11min entre mensagens) ficou 163min
# muda, quinze vezes o normal dela. É a MESMA duração e só uma das duas é incidente:
# quem decide isso é o ritmo de cada conta, não o relógio.
_FATOR_RITMO = 6


def limiar_de_silencio(recebidas_7d: int) -> float:
    """Quantos minutos de silêncio já são anormais PRA ESTA CONTA. Nunca abaixo do
    piso: numa conta muito movimentada o ritmo daria poucos minutos, e alarme a cada
    respiro do cliente é ruído."""
    if recebidas_7d <= 0:
        return float("inf")
    return max(_SILENCIO_MIN, _FATOR_RITMO * (_MIN_UTEIS_7D / recebidas_7d))


def deve_avisar(minutos_sem_receber, recebidas_7d: int, hora_brt: int,
                episodio_avisado: str | None, episodio_atual: str | None) -> bool:
    """A decisão inteira, sem banco e sem rede — é o que dá pra testar de verdade.

    `episodio_*` é o carimbo da última mensagem recebida: o que ficou guardado no
    aviso anterior e o de agora. Iguais = mesmo silêncio, já avisado.
    """
    if minutos_sem_receber is None:
        return False
    if recebidas_7d < _CONTA_ATIVA_MIN:
        return False
    if minutos_sem_receber < limiar_de_silencio(recebidas_7d):
        return False
    if not (_HORA_INICIO <= hora_brt < _HORA_FIM):
        return False
    # sem episódio atual não dá pra deduplicar; melhor não avisar do que avisar em loop
    if not episodio_atual:
        return False
    return episodio_avisado != episodio_atual


def _texto(nome_empresa: str, sem_receber_min: int) -> tuple[str, str]:
    """(texto do Telegram, texto do e-mail). O aviso tem que dizer o que fazer —
    'está estranho' sem próximo passo só gera aflição."""
    h = sem_receber_min / 60
    quanto = f"{int(sem_receber_min)} minutos" if sem_receber_min < 120 else f"{h:.0f} horas"
    corpo = (f"O WhatsApp da {nome_empresa} está sem receber mensagem há {quanto}.\n\n"
             "O painel pode estar dizendo CONECTADO mesmo assim — é o sintoma de "
             "sessão que emudece sem cair, e o cliente que manda mensagem não recebe "
             "resposta.\n\n"
             "O que fazer: mande uma mensagem de outro celular pro número da empresa. "
             "Se ela NÃO aparecer no painel, abra Comunicação › Canais e reconecte o "
             "chip.\n\n"
             "Cuidado: não clique em Desconectar — ele apaga a sessão e deixa a conta "
             "horas sem receber. Reconectar o sistema faz sozinho.")
    tg = (f"⚠️ *{nome_empresa}* está sem receber WhatsApp há {quanto}.\n\n"
          "O painel pode dizer CONECTADO mesmo assim — é sessão muda.\n"
          "Mande uma mensagem de outro celular pro número: se não aparecer no painel, "
          "reconecte o chip em Comunicação › Canais.\n"
          "_Não clique em Desconectar_ — ele apaga a sessão.")
    return tg, corpo


def _contas_com_whatsapp(c) -> list[tuple]:
    """Empresas com canal WhatsApp ativo + o retrato do silêncio de cada uma, numa
    consulta só (o ticker roda de 2 em 2 min; N+1 aqui seria desperdício puro)."""
    return c.execute(
        """select ct.id, coalesce(nullif(ct.nome_fantasia,''), ct.nome, 'sua empresa'),
                  ct.email,
                  extract(epoch from now() - max(m.criado_em) filter (where m.direcao='in'))/60,
                  count(*) filter (where m.direcao='in'
                                     and m.criado_em > now() - interval '7 days'),
                  max(m.criado_em) filter (where m.direcao='in')
             from contas ct
             join canais_config cc
               on cc.conta_id = ct.id and cc.canal='whatsapp' and cc.ativo
             left join conversas cv on cv.conta_id = ct.id and cv.canal='whatsapp'
             left join mensagens m on m.conversa_id = cv.id
            group by ct.id, ct.nome_fantasia, ct.nome, ct.email""").fetchall()


def rodar(pool, hora_brt: int | None = None) -> int:
    """Uma passada: avisa quem precisa. Devolve quantos avisos saíram.
    Best-effort — nunca levanta (roda no mesmo ticker dos lembretes)."""
    if hora_brt is None:
        from .agenda import agora_brt
        hora_brt = agora_brt().hour
    enviados = 0
    try:
        with pool.connection() as c:
            linhas = _contas_com_whatsapp(c)
    except Exception as e:  # noqa: BLE001
        _log.info("wa_silencio: não deu pra listar as contas: %s", e)
        return 0

    for conta_id, nome, email, minutos, recebidas_7d, ultima_in in linhas:
        try:
            episodio = ultima_in.isoformat() if ultima_in else None
            chave = f"wa_silencio_aviso_{conta_id}"
            ja = config_app.get_config(pool, chave)
            if not deve_avisar(minutos, int(recebidas_7d or 0), hora_brt, ja, episodio):
                continue
            tg, corpo = _texto(nome, int(minutos))
            # grava o dedup ANTES de mandar: se o envio falhar, é melhor perder um
            # aviso do que arriscar repetir o mesmo alarme a cada 2 minutos
            config_app.set_config(pool, chave, episodio)
            ok = notificar.enviar_para_dono(pool, conta_id, tg)
            if not ok and email:
                try:
                    from .email_sender import enviar_aviso
                    ok = enviar_aviso(email, "WhatsApp sem receber", corpo, nome)
                except Exception as e:  # noqa: BLE001
                    _log.info("wa_silencio: e-mail falhou pra conta %s: %s", conta_id, e)
            if ok:
                enviados += 1
                _log.info("wa_silencio: avisei a conta %s (%d min sem receber)",
                          conta_id, int(minutos))
            else:
                _log.info("wa_silencio: conta %s sem canal de aviso (nem telegram nem e-mail)",
                          conta_id)
        except Exception as e:  # noqa: BLE001
            _log.info("wa_silencio: conta %s falhou: %s: %s", conta_id, type(e).__name__, e)
            continue
    return enviados
