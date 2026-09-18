"""O VIGIA DO CANAL MORTO — quem está deixando de ser avisado, e por quê.

NASCEU DE UM CASO QUE FICOU DOIS DIAS ESCONDIDO (18/09/2026). O dono da conta 34
recebia a cópia de gestor dos 30 leads mais atrasados da casa, todo dia, e não
recebia nada: sem e-mail cadastrado, sem aparelho com push, e — depois que o
WhatsApp entrou — um número que aceita o envio e nunca devolve recibo. Nada disso
aparecia em lugar nenhum. Só apareceu porque alguém foi procurar.

Medir foi o degrau anterior (`aviso_log`, migrações 276 e 284). Este módulo é o
degrau que faltava: **a tela pergunta sozinha**, em vez de esperar alguém
desconfiar.

AS TRÊS PERGUNTAS, e cada uma existe por um jeito diferente de o aviso sumir:

1. `sem_cadastro` — a pessoa não tem POR ONDE ser avisada: nenhum e-mail, nenhum
   número, nenhum aparelho. É estático, não depende de histórico, e é o único que
   avisa ANTES do primeiro aviso perdido.
2. `sem_canal` — houve tentativa nos últimos dias e NENHUMA saiu, em canal nenhum.
   É o silêncio total, com rastro.
3. `numero_sem_recibo` — o WhatsApp aceitou o envio e o aparelho nunca confirmou.
   É o caso mais traiçoeiro, porque o registro diz "enviado ✓" e a régua parece
   saudável.

O QUE ESTE MÓDULO NÃO FAZ: nada. Só lê. Não manda aviso sobre a falta de aviso —
isso empilharia um canal em cima do canal quebrado — e não mexe em cadastro de
ninguém. Ele existe pra a tela poder dizer "olha isto aqui" pra quem decide.
"""
from __future__ import annotations

import logging

_log = logging.getLogger("openclaw.aviso_saude")

#: A janela do silêncio. Três dias porque o aviso é diário: um dia é feriado, dois
#: é fim de semana, três já é padrão.
DIAS_SEM_CANAL = 3

#: A janela do número suspeito. Sete dias dão amostra sem virar história antiga —
#: número trocado semana passada já se corrigiu.
DIAS_NUMERO = 7

#: Quantos envios sem recibo antes de acusar o número. DOIS, e não um: um envio
#: sem recibo é o aparelho desligado numa tarde; dois dias seguidos é o número.
MIN_ENVIOS_NUMERO = 2

#: Quanto tempo esperar antes de chamar um envio de "sem recibo". O recibo leva
#: segundos quando o aparelho está ligado e horas quando está no bolso — acusar
#: cedo demais transformaria "ele está almoçando" em "o número está errado".
HORAS_ESPERA_RECIBO = 6

#: Quem participa do aviso de follow-up. Quem não participa não pode ser cobrado
#: por não receber — `membro` e `financeiro` não entram na régua.
PAPEIS_AVISADOS = ("dono", "gestor", "vendedor")

TEXTO = {
    "sem_cadastro": "não tem por onde ser avisado",
    "sem_canal": "não recebeu nenhum aviso",
    "numero_sem_recibo": "o WhatsApp não confirma entrega",
}


def alertas(pool, conta_id: int) -> list[dict]:
    """Quem não está sendo avisado nesta conta.

    Devolve [{membro_id, quem, tipo, detalhe}], do mais grave pro menos: primeiro
    quem não tem canal nenhum, depois o silêncio com rastro, por fim o número que
    não confirma.

    Tolerante de ponta a ponta: base sem `aviso_envios` (ou sem as colunas da 284)
    devolve lista vazia. Isto alimenta um selo; selo que derruba tela não serve.
    """
    try:
        with pool.connection() as c:
            with c.transaction():
                return _sem_cadastro(c, conta_id) + _sem_canal(c, conta_id) \
                    + _numero_sem_recibo(c, conta_id)
    except Exception as e:  # noqa: BLE001
        _log.info("aviso_saude: leitura falhou (ok) conta=%s: %s: %s",
                  conta_id, type(e).__name__, e)
        return []


def por_membro(pool, conta_id: int) -> dict:
    """Os mesmos alertas, indexados por membro — o formato que a lista da Equipe
    precisa pra marcar cada linha sem varrer a lista inteira por pessoa."""
    out: dict = {}
    for a in alertas(pool, conta_id):
        out.setdefault(a["membro_id"], a)     # o primeiro é o mais grave
    return out


def _sem_cadastro(c, conta_id: int) -> list[dict]:
    """Ninguém em casa: sem e-mail, sem número e sem aparelho com push.

    Não olha histórico de propósito — é o único alerta que aparece ANTES de o
    primeiro aviso se perder, e é o que teria pegado o dono da conta 34 no dia em
    que ele virou destinatário da cópia de gestor."""
    linhas = c.execute(
        """select m.id, coalesce(nullif(m.nome,''), m.email, '(sem nome)')
             from membros m
            where m.conta_id=%s and coalesce(m.ativo,true) and m.papel = any(%s)
              and coalesce(nullif(m.email,''), '') = ''
              and coalesce(nullif(m.whatsapp,''), nullif(m.whatsapp_id,''), '') = ''
              and not exists (select 1 from push_assinaturas s
                               where s.membro_id = m.id and s.conta_id = m.conta_id)
            order by m.id""", (conta_id, list(PAPEIS_AVISADOS))).fetchall()
    return [{"membro_id": r[0], "quem": r[1], "tipo": "sem_cadastro",
             "detalhe": "sem e-mail, sem WhatsApp e sem aparelho com push"}
            for r in linhas]


def _sem_canal(c, conta_id: int) -> list[dict]:
    """Teve tentativa e nada saiu, em canal nenhum, nos últimos dias.

    O `having` é a regra inteira: conta as tentativas e exige ZERO sucessos. Um
    canal que funciona salva a pessoa do alerta — o que se está procurando é o
    silêncio completo, não o canal ruim isolado."""
    linhas = c.execute(
        """select e.membro_id,
                  coalesce(nullif(m.nome,''), m.email, '(sem nome)'),
                  count(*),
                  string_agg(distinct coalesce(e.motivo,''), ' · ')
                    filter (where coalesce(e.motivo,'') <> ''),
                  max(e.criado_em)
             from aviso_envios e
             join membros m on m.id = e.membro_id
            where e.conta_id=%s and e.membro_id is not null
              and coalesce(m.ativo,true)
              and e.criado_em >= now() - make_interval(days => %s)
            group by 1,2
           having count(*) filter (where e.ok) = 0
            order by 3 desc, 1""", (conta_id, DIAS_SEM_CANAL)).fetchall()
    return [{"membro_id": r[0], "quem": r[1], "tipo": "sem_canal",
             "detalhe": (r[3] or "nenhum canal entregou")}
            for r in linhas]


def _numero_sem_recibo(c, conta_id: int) -> list[dict]:
    """O WhatsApp aceitou e o aparelho nunca confirmou.

    A GUARDA QUE EVITA O ALARME FALSO: só acusa se a MESMA conta teve algum aviso
    de WhatsApp com recibo no período. Sem ela, um chip cujo caminho de recibo
    parou (ou uma base que ainda não recebeu recibo nenhum) marcaria a equipe
    inteira como "número errado" — culpando as pessoas por um defeito do sistema.

    E só conta envio com mais de `HORAS_ESPERA_RECIBO`: recibo demora quando o
    telefone está no bolso, e acusar cedo transforma almoço em número errado."""
    prova = c.execute(
        """select count(*) from aviso_envios
            where conta_id=%s and canal='whatsapp'
              and (entregue_em is not null or lido_em is not null)
              and criado_em >= now() - make_interval(days => %s)""",
        (conta_id, DIAS_NUMERO)).fetchone()
    if not prova or not prova[0]:
        return []
    linhas = c.execute(
        """select e.membro_id,
                  coalesce(nullif(m.nome,''), m.email, '(sem nome)'),
                  count(*), max(e.destino)
             from aviso_envios e
             join membros m on m.id = e.membro_id
            where e.conta_id=%s and e.canal='whatsapp' and e.ok
              and coalesce(m.ativo,true)
              and e.criado_em >= now() - make_interval(days => %s)
              and e.criado_em <= now() - make_interval(hours => %s)
            group by 1,2
           having count(*) >= %s
              and count(*) filter (where e.entregue_em is not null
                                      or e.lido_em is not null) = 0
            order by 3 desc, 1""",
        (conta_id, DIAS_NUMERO, HORAS_ESPERA_RECIBO, MIN_ENVIOS_NUMERO)).fetchall()
    return [{"membro_id": r[0], "quem": r[1], "tipo": "numero_sem_recibo",
             "detalhe": f"{r[2]} avisos para {r[3] or 'o número cadastrado'} "
                        f"sem nenhum ✓✓ — o número pode estar errado"}
            for r in linhas]
