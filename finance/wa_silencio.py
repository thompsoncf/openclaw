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

A UNIDADE É O CHIP, NÃO A EMPRESA (02/09/2026)

Até aqui a medida era por empresa: as conversas eram agrupadas por `conta_id` e o
`chip_id` era ignorado. Numa empresa de um chip só dá no mesmo; em duas, esconde
exatamente o caso que este módulo existe pra pegar.

A Prime Eventos tem dois. O chip 1 (CP Zarb, o número principal, que recebia ~100
mensagens de cliente por dia) parou de receber ao vivo em 31/08 às 17:24. O chip 2
(CP Thiago) continuou recebendo normalmente o tempo todo — e, somando os dois, a
empresa nunca ficou 100 minutos calada. **47 horas de entrada morta no número
principal, e o alarme não tocou uma vez.** Quem descobriu foi o dono, mandando um
"oi teste" pros dois números e vendo chegar em um só.

Então cada chip tem seu próprio relógio, seu próprio ritmo (o chip 2 recebe 2 a 17
por dia e o 1 recebia 100 — o mesmo silêncio não quer dizer a mesma coisa nos
dois) e seu próprio dedup. O aviso diz QUAL chip, senão o dono recebe "seu
WhatsApp parou" com dois números na mesa.

O que este arquivo continua NÃO resolvendo, e vale saber: a fonte é a tabela
`mensagens`, não o `wa_qr_log`. O sinal que não mente sobre entrada viva é o
`entrada repassada ao webhook ✓`, e há casos em que os dois discordam — o serviço
descarta de propósito grupo, canal e status, então uma conta pode estar recebendo
tráfego o tempo todo e não gravar uma linha em `mensagens` (foi o erro de
diagnóstico de 22/08/2026, que está no CLAUDE.md). Trocar a fonte é uma mudança
maior que esta, e não era o que estava quebrado aqui.
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


def _texto(nome_empresa: str, sem_receber_min: int,
           chip: str = "") -> tuple[str, str]:
    """(texto do Telegram, texto do e-mail). O aviso tem que dizer o que fazer —
    'está estranho' sem próximo passo só gera aflição.

    `chip` é o apelido do número, e só vem preenchido quando a empresa tem MAIS DE
    UM. Numa empresa de um chip só, dizer o nome dele não acrescenta nada e ainda
    confunde ("qual chip? só tenho um"). Em duas, é a informação mais importante da
    mensagem: sem ela o dono lê "seu WhatsApp parou" com dois números na mesa e
    testa o que está funcionando — foi o que aconteceu na Prime em 02/09/2026."""
    h = sem_receber_min / 60
    quanto = f"{int(sem_receber_min)} minutos" if sem_receber_min < 120 else f"{h:.0f} horas"
    quem = f"O chip *{chip}* da {nome_empresa}" if chip else f"O WhatsApp da {nome_empresa}"
    quem_txt = f"O chip {chip} da {nome_empresa}" if chip else f"O WhatsApp da {nome_empresa}"
    alvo = f"pro número do chip {chip}" if chip else "pro número da empresa"
    corpo = (f"{quem_txt} está sem receber mensagem há {quanto}.\n\n"
             "O painel pode estar dizendo CONECTADO mesmo assim — é o sintoma de "
             "sessão que emudece sem cair, e o cliente que manda mensagem não recebe "
             "resposta.\n\n"
             f"O que fazer: mande uma mensagem de outro celular {alvo}. "
             "Se ela NÃO aparecer no painel, abra Comunicação › Canais e reconecte o "
             "chip.\n\n"
             "Cuidado: não clique em Desconectar — ele apaga a sessão e deixa a conta "
             "horas sem receber. Reconectar o sistema faz sozinho.")
    tg = (f"⚠️ {quem} está sem receber WhatsApp há {quanto}.\n\n"
          "O painel pode dizer CONECTADO mesmo assim — é sessão muda.\n"
          f"Mande uma mensagem de outro celular {alvo}: se não aparecer no painel, "
          "reconecte o chip em Comunicação › Canais.\n"
          "_Não clique em Desconectar_ — ele apaga a sessão.")
    return tg, corpo


def _chips_com_whatsapp(c) -> list[tuple]:
    """Um retrato de silêncio por CHIP, numa consulta só (o ticker roda de 2 em 2
    min; N+1 aqui seria desperdício puro).

    Devolve, por chip ativo:
        (empresa_id, empresa_nome, empresa_email, chip_conta, principal, rotulo,
         minutos_sem_receber, recebidas_7d, ultima_in)

    COMO UM CHIP É IDENTIFICADO. Cada chip é uma linha de `contas` com canal de
    WhatsApp ativo; o principal é o que não tem `chip_de`, e os secundários apontam
    pra ele. As CONVERSAS, porém, ficam todas na empresa: `conversas.conta_id` é
    sempre o principal e quem diz de qual chip veio é `conversas.chip_id` — nulo
    pro principal. Conferido no banco de produção em 02/09/2026: nenhuma conversa
    de WhatsApp pertence a uma conta-chip. Por isso o join é pela empresa MAIS o
    `chip_id`, e não por `conta_id` do chip — que não casaria com nada.

    O rótulo segue o mesmo par que o Inbox lê: `canais_config.rotulo` pro principal,
    `contas.nome` pro secundário. Duas fontes porque é assim que os dois foram
    batizados; ler só uma deixaria metade dos chips sem nome."""
    return c.execute(
        """with chip as (
             select ct.id                       as chip_conta,
                    coalesce(ct.chip_de, ct.id) as empresa_id,
                    (ct.chip_de is null)        as principal,
                    coalesce(nullif(btrim(cc.rotulo),''),
                             nullif(btrim(ct.nome),''), '') as rotulo
               from contas ct
               join canais_config cc
                 on cc.conta_id = ct.id and cc.canal='whatsapp' and cc.ativo)
           select ch.empresa_id,
                  coalesce(nullif(emp.nome_fantasia,''), emp.nome, 'sua empresa'),
                  emp.email, ch.chip_conta, ch.principal, ch.rotulo,
                  extract(epoch from now() - max(m.criado_em)
                          filter (where m.direcao='in'))/60,
                  count(*) filter (where m.direcao='in'
                                     and m.criado_em > now() - interval '7 days'),
                  max(m.criado_em) filter (where m.direcao='in')
             from chip ch
             join contas emp on emp.id = ch.empresa_id
             left join conversas cv
               on cv.conta_id = ch.empresa_id and cv.canal='whatsapp'
              and cv.chip_id is not distinct from
                  (case when ch.principal then null else ch.chip_conta end)
             left join mensagens m on m.conversa_id = cv.id
            group by ch.empresa_id, emp.nome_fantasia, emp.nome, emp.email,
                     ch.chip_conta, ch.principal, ch.rotulo""").fetchall()


def chave_dedup(empresa_id: int, chip_conta: int, principal: bool, real: bool) -> str:
    """A chave do dedup daquele chip.

    O PRINCIPAL FICA COM A CHAVE ANTIGA (`wa_silencio_aviso_<empresa>`), de
    propósito: das quatro empresas com WhatsApp em produção, três têm um chip só, e
    mudar o formato pra todo mundo zeraria o dedup delas — o alarme reavisaria um
    episódio já avisado no primeiro ticker depois do deploy. Só o chip secundário
    ganha chave nova, porque ele nunca teve uma.

    O ensaio tem chave própria (ver o laço do `rodar`): sem isso ele gastaria a
    marca do episódio e, no dia em que o alarme fosse ligado, o aviso de verdade
    não sairia — o episódio já constaria como avisado."""
    base = "wa_silencio_aviso" if real else "wa_silencio_ensaio"
    return f"{base}_{empresa_id}" if principal else f"{base}_{empresa_id}_c{chip_conta}"


def envia_de_verdade(conta_id: int, modo: str, contas_ligadas: str) -> bool:
    """Este aviso SAI, ou só é registrado?

    Alarme novo estreia em ENSAIO: ele avalia tudo e só escreve no log o que teria
    mandado. Assim dá pra ver os disparos reais antes de qualquer cliente receber
    mensagem — se a calibragem estiver errada, o erro fica no log e não no bolso de
    alguém. Ligar é decisão de quem viu os disparos:

        app_config['wa_silencio_modo']   = 'ligado'   → manda pra todo mundo
        app_config['wa_silencio_contas'] = '35,34'    → manda só pra essas
                                                        (vale mesmo em ensaio)
    """
    if (modo or "").strip().lower() == "ligado":
        return True
    escolhidas = {p.strip() for p in (contas_ligadas or "").split(",") if p.strip()}
    return str(conta_id) in escolhidas


def rodar(pool, hora_brt: int | None = None) -> int:
    """Uma passada: avisa quem precisa. Devolve quantos avisos saíram DE VERDADE
    (ensaio não conta). Best-effort — nunca levanta (roda no ticker dos lembretes).

    Um chip por vez, cada um com seu relógio: ver o topo do arquivo pra por que a
    unidade deixou de ser a empresa em 02/09/2026."""
    if hora_brt is None:
        from .agenda import agora_brt
        hora_brt = agora_brt().hour
    enviados = 0
    try:
        with pool.connection() as c:
            linhas = _chips_com_whatsapp(c)
    except Exception as e:  # noqa: BLE001
        _log.info("wa_silencio: não deu pra listar os chips: %s", e)
        return 0
    modo = config_app.get_config(pool, "wa_silencio_modo", "ensaio") or "ensaio"
    # A lista LIGADA é de EMPRESA, não de chip: quem liga o alarme quer saber da
    # empresa inteira, e ninguém deveria ter que lembrar o id de cada chip pra não
    # ficar cego justamente no número que quebrou.
    contas_ligadas = config_app.get_config(pool, "wa_silencio_contas", "") or ""
    # quantos chips cada empresa tem — decide se o aviso nomeia o chip (ver _texto)
    n_chips: dict = {}
    for lin in linhas:
        n_chips[lin[0]] = n_chips.get(lin[0], 0) + 1

    for (empresa_id, nome, email, chip_conta, principal, rotulo,
         minutos, recebidas_7d, ultima_in) in linhas:
        try:
            episodio = ultima_in.isoformat() if ultima_in else None
            real = envia_de_verdade(empresa_id, modo, contas_ligadas)
            chave = chave_dedup(empresa_id, chip_conta, principal, real)
            ja = config_app.get_config(pool, chave)
            if not deve_avisar(minutos, int(recebidas_7d or 0), hora_brt, ja, episodio):
                continue
            chip = (rotulo or f"#{chip_conta}") if n_chips.get(empresa_id, 1) > 1 else ""
            tg, corpo = _texto(nome, int(minutos), chip)
            # grava o dedup ANTES de mandar: se o envio falhar, é melhor perder um
            # aviso do que arriscar repetir o mesmo alarme a cada 2 minutos
            config_app.set_config(pool, chave, episodio)
            if not real:
                _log.warning(
                    "wa_silencio[ENSAIO]: TERIA avisado a empresa %s (%s), chip %s — "
                    "%d min sem receber, ritmo normal ~%d min. Pra ligar de verdade: "
                    "app_config wa_silencio_contas='%s'",
                    empresa_id, nome, rotulo or chip_conta, int(minutos),
                    int(_MIN_UTEIS_7D / max(int(recebidas_7d or 0), 1)), empresa_id)
                continue
            ok = notificar.enviar_para_dono(pool, empresa_id, tg)
            if not ok and email:
                try:
                    from .email_sender import enviar_aviso
                    ok = enviar_aviso(email, "WhatsApp sem receber", corpo, nome)
                except Exception as e:  # noqa: BLE001
                    _log.info("wa_silencio: e-mail falhou pra empresa %s: %s", empresa_id, e)
            if ok:
                enviados += 1
                _log.info("wa_silencio: avisei a empresa %s, chip %s (%d min sem receber)",
                          empresa_id, rotulo or chip_conta, int(minutos))
            else:
                _log.info("wa_silencio: empresa %s sem canal de aviso (nem telegram nem e-mail)",
                          empresa_id)
        except Exception as e:  # noqa: BLE001
            _log.info("wa_silencio: empresa %s chip %s falhou: %s: %s",
                      empresa_id, chip_conta, type(e).__name__, e)
            continue
    return enviados
