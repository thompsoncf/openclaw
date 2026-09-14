"""A TRAVA DA INSISTÊNCIA: pedir um porquê antes do quarto "oi, tudo bem?".

Pedido do dono em 14/09/2026:

    "colocar uma trava no chat até 7 dias e avisar pros vendedores e no funil que
     eles só podem falar com o cliente se der uma justificativa"

O mockup aprovado é `docs/mockups/trava_justificativa.html`, e a aprovação veio
com uma condição que este módulo existe para cumprir: *"sim, poupa quem está
esperando. faz o ensaio primeiro"*.

A TRAVA OLHA QUEM FALOU POR ÚLTIMO, NUNCA O CALENDÁRIO SOZINHO
Medido na conta 34 em 14/09: dos 236 leads passados do teto de 7 dias em
Contatado, **210** são casos em que nós falamos por último e o cliente não voltou
— e **26** são casos em que o CLIENTE escreveu e ninguém respondeu ainda.

Uma trava por calendário calaria a empresa justamente com esses 26, que são as
pessoas que levantaram a mão. É o oposto do que o funil existe para fazer, e sai
caro: cada um deles é uma festa que pode fechar. Então:

    bola com o CLIENTE  → nunca trava, em nenhuma hipótese, nem no 30º dia
    bola CONOSCO        → e o prazo da etapa estourou? aí a regra engata

Note que "o cliente falou por último" não é o mesmo que "o cliente falou
recentemente". Um lead em que ele escreveu há 20 dias e ninguém respondeu conta
como bola conosco pelo relógio da temperatura (está frio) e como cliente
esperando aqui — e é bom que sim: é justamente o caso mais vergonhoso de travar.

POR QUE ATRITO COM PORTA, E NÃO MURO
A Prime tem um chip só, da empresa, então a trava prende o que passa pelo Zaq.
Mas cada vendedor tem o WhatsApp pessoal, e há medição registrada nesta base de
que 98% do que a Prime mandava por fora chegava sem nome — sem lead, sem
histórico, sem nada. Um muro alto demais não faz o vendedor parar de insistir;
faz a conversa mudar de telefone, e aí a empresa perde o registro inteiro.

Por isso o desenho é: a mensagem SAI, o motivo é escolhido no mesmo gesto, e a
parede de verdade só na terceira vez — quando já houve duas conversas sobre
aquele lead. Quem decide isso é `renovacoes_max` da etapa, que a Prime já tem em 2.

NASCE DESLIGADO, E POR ENQUANTO SÓ TEM DOIS MODOS
    off          não olha nada. É o padrão de toda conta.
    observando   conta o que TERIA travado, sem travar nada.

'ligado' ainda NÃO é aceito (ver `modo`). Travar de verdade depende da tela de
justificativa, e os motivos dela ainda não foram escolhidos pelo dono. Aceitar o
modo antes da tela seria uma chave que mente: o motor recusaria o envio e o
vendedor não teria onde justificar — exatamente o beco que o #680 acabou de
fechar no fluxo de perda.

O QUE O ENSAIO CONTA, E POR QUE É TENTATIVA E NÃO LEAD
"Quantos leads estão velhos" já sabemos: 236. A pergunta que decide se 7 dias é o
número certo é **quantas justificativas por dia isso vira para cada vendedor** — e
essa só se responde contando TENTATIVA de envio. Na conta 34 saem de 16 a 188
mensagens humanas por dia; o ensaio diz quantas delas teriam parado.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from finance import funil_teto as _teto

_log = logging.getLogger("openclaw.funil_trava")

#: os três modos. 'ligado' passou a ser cumprível quando a tela de justificativa
#: nasceu (o dono aprovou os quatro motivos em 14/09: "podem ser esses mesmo").
MODOS = ("off", "observando", "ligado")

#: as decisões que ENGATAM — as únicas que viram linha no ensaio. Uma tentativa
#: dentro do prazo não é registrada: seria uma cópia da tabela `mensagens`.
DECISOES = ("pediria_justificativa", "parede", "poupou_cliente_esperando")

#: o motivo que vai pro histórico do funil quando a justificativa MOVE o lead
MOTIVO_MOV = "trava_justificada"

ROTULO = {
    "pediria_justificativa": "pediria um motivo antes de enviar",
    "parede": "bloquearia — renovações esgotadas",
    "poupou_cliente_esperando": "liberou: o cliente está esperando resposta",
}

#: OS QUATRO MOTIVOS, aprovados pelo dono em 14/09 ("os quatro motivos podem ser
#: esses mesmo, pode fazer a tela"), desenhados em docs/mockups/trava_justificativa.
#:
#: (chave, rótulo, o que FAZ, pede data?, pede texto?)
#:
#: Cada um faz alguma coisa de propósito. Um motivo que só grava texto vira "." na
#: segunda semana — é o mesmo defeito que a lista de perda teria se ninguém lesse.
#: Aqui o vendedor escolhe e o sistema trabalha: marca o retorno, move o card, ou
#: empurra o prazo. A justificativa vira ação, não papelada.
MOTIVOS = (
    ("pediu_data", "Ele pediu para eu chamar nesta data", "agenda", True, False),
    ("mandando", "Estou mandando o que ele pediu", "avanca", False, False),
    ("outro_caminho", "Vou tentar por outro caminho", "renova", False, False),
    ("outro", "Outro — conte em uma linha", "renova", False, True),
)
MOTIVO_POR_CHAVE = {m[0]: {"chave": m[0], "rotulo": m[1], "faz": m[2],
                           "pede_data": m[3], "pede_texto": m[4]} for m in MOTIVOS}

#: POR QUE ESTES QUATRO SÃO DO CÓDIGO, E OS DE PERDA SÃO DA CONTA
#: A lista de perda é vocabulário: a empresa chama do jeito dela, e acrescentar um
#: motivo é digitar uma linha na Régua. Estes quatro não são vocabulário — cada um
#: dispara uma AÇÃO do sistema. Um quinto motivo só existe depois que alguém
#: escrever o que ele faz. Deixá-los editáveis ofereceria ao dono um botão que não
#: faz nada, que é pior que não ter o botão.


def motivos() -> list[dict]:
    """Os quatro, na ordem da tela."""
    return [MOTIVO_POR_CHAVE[m[0]] for m in MOTIVOS]


def modo(c, conta_id: int) -> str:
    """O modo da conta. Qualquer valor que o código não saiba cumprir vira 'off'.

    Fechar para 'off' e não para o valor gravado é de propósito: se alguém puser
    'ligado' no banco antes de a tela existir, o certo é o motor não fazer nada —
    e não recusar envios que o vendedor não tem como destravar.
    """
    # O SAVEPOINT NÃO É ZELO. Sem ele, a consulta que falha (coluna ou tabela que
    # ainda não existe) ENVENENA a transação inteira: este `except` devolveria 'off'
    # enquanto todo comando seguinte morre com "current transaction is aborted" — e
    # quem morre a seguir é o INSERT da mensagem do vendedor. Foi assim que este
    # módulo quebrou `test_enviar_mensagem_grava_e_pausa` na primeira tentativa,
    # com o savepoint um nível acima, tarde demais.
    try:
        with c.transaction():
            r = c.execute("select trava_modo from funil_regua where conta_id=%s",
                          (conta_id,)).fetchone()
    except Exception as e:  # noqa: BLE001 — coluna nova; deploy pela metade não trava envio
        _log.warning("trava_modo ilegível na conta %s (%s) — tratando como off", conta_id, e)
        return "off"
    v = (r[0] or "off") if r else "off"
    return v if v in MODOS else "off"


def bola_de(c, conta_id: int, lead_id: int) -> str:
    """Com quem está a bola: 'cliente' se ele falou por último, senão 'nossa'.

    Compara IDS, não datas. `criado_em` de duas mensagens quase simultâneas pode
    chegar fora de ordem (é o mesmo motivo pelo qual a bolinha vermelha do card
    usa id), e nesta decisão empatar para o lado errado significa calar a empresa
    com quem está esperando.

    Conversa nenhuma → 'nossa'. Lead sem nenhuma mensagem não tem cliente
    esperando; se alguém for insistir nele, a regra pode perguntar por quê.
    """
    r = c.execute(
        """select coalesce(max(m.id) filter (where m.direcao='in'), 0),
                  coalesce(max(m.id) filter (where m.direcao='out'), 0)
             from conversas cv join mensagens m on m.conversa_id = cv.id
            where cv.conta_id=%s and cv.prospeccao_id=%s""",
        (conta_id, lead_id)).fetchone()
    ult_in, ult_out = (r[0], r[1]) if r else (0, 0)
    return "cliente" if ult_in > ult_out else "nossa"


def tentativas_de(c, conta_id: int, lead_id: int) -> int:
    """Quantas mensagens NOSSAS foram depois da última do cliente.

    É o mesmo número que a temperatura usa para esfriar (finance/temperatura), de
    propósito: se as duas contassem diferente, o cartão diria "3 tentativas" e a
    trava diria outra coisa na mesma tela.
    """
    r = c.execute(
        """with ult as (
             select coalesce(max(m.criado_em) filter (where m.direcao='in'),
                             '-infinity'::timestamptz) as ult_in
               from conversas cv join mensagens m on m.conversa_id = cv.id
              where cv.conta_id=%(conta)s and cv.prospeccao_id=%(lead)s)
           select count(*) from conversas cv
             join mensagens m on m.conversa_id = cv.id, ult
            where cv.conta_id=%(conta)s and cv.prospeccao_id=%(lead)s
              and m.direcao='out' and m.criado_em > ult.ult_in""",
        {"conta": conta_id, "lead": lead_id}).fetchone()
    return int(r[0] or 0)


# ------------------------------------------------------------------ a decisão

def decidir(*, bola: str, estado: str) -> str:
    """A regra, pura. Recebe com quem está a bola e o estado do teto da etapa.

    `estado` vem de `funil_teto.estado` e vale 'ok', 'avisar', 'vencido' ou
    'esgotado'. As quatro linhas abaixo são a regra inteira:

        cliente esperando  → poupou   (antes de qualquer olhada no prazo)
        vencido            → pediria a justificativa
        esgotado           → parede
        resto              → no_prazo (não engata, não vira registro)

    A ORDEM IMPORTA e é esta: a bola é consultada ANTES do prazo. Escrito ao
    contrário — checar o prazo e só então perguntar de quem é a bola — o primeiro
    `return` já teria travado os 26 leads que este módulo existe para poupar.
    """
    if bola == "cliente":
        return "poupou_cliente_esperando"
    if estado == "esgotado":
        return "parede"
    if estado == "vencido":
        return "pediria_justificativa"
    return "no_prazo"


def avaliar(c, conta_id: int, lead_id: int, agora: datetime | None = None) -> dict:
    """O que a trava faria com uma tentativa de envio para este lead AGORA.

    Devolve {decisao, bola, etapa, estado, dias, renovacoes, tentativas}. Não
    escreve nada e não bloqueia nada — quem grava é `registrar`.

    Lead em etapa SEM teto devolve 'no_prazo': a trava é uma consequência do prazo
    da etapa, e etapa sem prazo não tem do que o vendedor se justificar.
    """
    agora = agora or datetime.now(timezone.utc)
    vazio = {"decisao": "no_prazo", "bola": "", "etapa": "", "estado": "ok",
             "dias": 0.0, "renovacoes": 0, "tentativas": 0}
    r = c.execute("select status from prospeccao where id=%s and conta_id=%s",
                  (lead_id, conta_id)).fetchone()
    if not r:
        return vazio
    etapa = r[0] or ""
    regra = _teto.etapas_com_teto(c, conta_id).get(etapa)
    if not regra:
        return dict(vazio, etapa=etapa)

    bola = bola_de(c, conta_id, lead_id)
    desde = _teto.na_etapa_desde(c, lead_id) or agora
    renov = _teto.renovacoes_de(c, lead_id, etapa)
    cfg = _teto.config(c, conta_id)
    est = _teto.estado(desde=desde, renovacoes=renov, regra=regra, agora=agora,
                       avisar_antes=int(cfg.get("teto_avisar_antes") or _teto.AVISAR_ANTES_PADRAO))
    return {"decisao": decidir(bola=bola, estado=est["estado"]),
            "bola": bola, "etapa": etapa, "estado": est["estado"],
            "dias": round(est["dias"], 2), "renovacoes": renov,
            "tentativas": tentativas_de(c, conta_id, lead_id)}


# ------------------------------------------------------------------ o ensaio

def registrar(c, conta_id: int, lead_id: int, membro_id: int | None = None,
              agora: datetime | None = None) -> dict | None:
    """Chamada ANTES de um envio humano. No ensaio, conta e deixa passar.

    Devolve o veredito quando a regra engata, e None quando não há nada a dizer
    (modo off, etapa sem prazo, lead dentro do prazo). **Nunca levanta**: um
    defeito aqui não pode impedir uma mensagem de sair para um cliente — é a
    mesma escolha do resto do produto, e neste caso ela é mais forte ainda,
    porque o módulo inteiro está em ensaio.

    O `savepoint` não é zelo: sem ele, uma consulta que falha (coluna que ainda
    não existe num deploy pela metade) envenena a transação e derruba o INSERT da
    mensagem que veio depois. Mesmo defeito que já custou um ciclo nos gatilhos.
    """
    try:
        with c.transaction():
            md = modo(c, conta_id)
            if md == "off":
                return None
            v = avaliar(c, conta_id, lead_id, agora=agora)
            if v["decisao"] not in DECISOES:
                return None
            # `simulado` diz se a mensagem SAIU assim mesmo. Em ensaio sempre saiu;
            # em 'ligado' a decisão de barrar é de quem chama, e o registro guarda o
            # que de fato aconteceu — sem isso o relatório de depois de ligar não
            # distinguiria "teria travado" de "travou".
            v["modo"] = md
            c.execute(
                """insert into funil_trava_tentativa
                     (conta_id, prospeccao_id, membro_id, etapa, decisao, bola,
                      dias_na_etapa, renovacoes, tentativas, simulado)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)""",
                (conta_id, lead_id, membro_id, v["etapa"], v["decisao"], v["bola"],
                 v["dias"], v["renovacoes"], v["tentativas"], md != "ligado"))
            return v
    except Exception as e:  # noqa: BLE001 — ver o docstring: envio nunca para por isto
        _log.warning("ensaio da trava falhou na conta %s, lead %s (%s: %s)",
                     conta_id, lead_id, type(e).__name__, e)
        return None


def resumo(c, conta_id: int, dias: int = 7) -> dict:
    """O que o ensaio viu na janela — é este o relatório que responde ao dono.

    `enviadas` é o denominador e sai de `mensagens`, não da tabela do ensaio: só
    as tentativas que engatam viram linha, então sem o denominador o número de
    justificativas por dia não teria com o que ser comparado.
    """
    linhas = c.execute(
        """select t.decisao, coalesce(nullif(mb.nome,''), mb.email, 'sem vendedor'),
                  count(*), round(avg(t.dias_na_etapa)::numeric, 1),
                  count(distinct t.prospeccao_id)
             from funil_trava_tentativa t
             left join membros mb on mb.id = t.membro_id
            where t.conta_id=%s and t.criado_em > now() - make_interval(days => %s)
            group by 1, 2 order by 3 desc""",
        (conta_id, dias)).fetchall()
    env = c.execute(
        """select count(*) from mensagens m join conversas cv on cv.id = m.conversa_id
            where cv.conta_id=%s and m.direcao='out' and m.autor='humano'
              and m.criado_em > now() - make_interval(days => %s)""",
        (conta_id, dias)).fetchone()
    por = [{"decisao": r[0], "quem": r[1], "quantas": r[2],
            "dias_medio": float(r[3]) if r[3] is not None else None, "leads": r[4]}
           for r in linhas]
    return {"dias": dias, "enviadas": int(env[0] or 0), "por_decisao_e_vendedor": por,
            "engataram": sum(x["quantas"] for x in por)}


# ------------------------------------------------------------------ a ação

def proxima_de_venda(c, conta_id: int, etapa: str) -> str | None:
    """A próxima etapa à FRENTE desta que ainda é venda e aparece no quadro.

    Usada pelo motivo 'mandando' — quem está mandando o que o cliente pediu já
    saiu do "contatado" e entrou em negociação. Na Prime isso cai em Negociação;
    em outra conta cai no que o funil dela tiver na frente, sem nome fixo no
    código (CLAUDE.md §6: o vocabulário é da conta, não meu).

    `sai_do_quadro` e fase de fechamento ficam de fora: justificar um envio nunca
    pode ganhar ou perder um lead, e mover para Ganho é decisão de quem falou com
    o cliente — a mesma razão pela qual o D7 do follow-up não move pra Perdido.
    """
    r = c.execute(
        """select prox.chave
             from funil_etapas atual
             join funil_etapas prox
               on prox.conta_id = atual.conta_id and prox.ordem > atual.ordem
              and coalesce(prox.fase,'venda') = 'venda'
              and not coalesce(prox.sai_do_quadro, false)
            where atual.conta_id=%s and atual.chave=%s
            order by prox.ordem limit 1""", (conta_id, etapa)).fetchone()
    return r[0] if r else None


def justificar(c, conta_id: int, lead_id: int, membro_id: int | None, *,
               motivo: str, descricao: str = "", data: str = "",
               agora: datetime | None = None) -> dict:
    """O vendedor escolheu um motivo. Libera o envio e FAZ o que o motivo promete.

    Devolve {ok} ou {ok: False, erro, ...}. As recusas:

        motivo_invalido        não é um dos quatro
        descricao_obrigatoria  'outro' sem a linha de texto
        data_obrigatoria       'pediu_data' sem a data
        sem_renovacao          é a PAREDE: as renovações da etapa acabaram

    TODA justificativa consome uma renovação da etapa, inclusive a mais legítima
    ("ele pediu para chamar no dia 20"). Não é rigor: é o que faz a parede existir.
    Se um dos motivos não consumisse, ele viraria o botão de nunca bater na parede,
    e no fim de duas semanas seria o único escolhido — a trava viraria enfeite.

    Em troca, a renovação compra uma SEMANA inteira com aquele lead: dentro dela a
    regra nem engata. Justificar uma vez não é justificar toda mensagem.
    """
    from finance import funil_teto as _t
    m = MOTIVO_POR_CHAVE.get((motivo or "").strip())
    if not m:
        return {"ok": False, "erro": "motivo_invalido"}
    if m["pede_texto"] and not (descricao or "").strip():
        return {"ok": False, "erro": "descricao_obrigatoria"}
    if m["pede_data"] and not (data or "").strip():
        return {"ok": False, "erro": "data_obrigatoria"}

    etapa = (c.execute("select status from prospeccao where id=%s and conta_id=%s",
                       (lead_id, conta_id)).fetchone() or [""])[0]
    regra = _t.etapas_com_teto(c, conta_id).get(etapa)
    if not regra:
        return {"ok": True, "nada_a_fazer": True}   # etapa sem prazo: não havia trava

    texto = (descricao or "").strip() or m["rotulo"]
    r = _t.renovar(c, conta_id, lead_id, etapa=etapa, regra=regra, membro_id=membro_id,
                   justificativa=texto, agora=agora)
    if not r.get("ok"):
        return {"ok": False, "erro": r.get("erro"), "renovacoes": r.get("renovacoes")}

    feito = {"motivo": m["chave"], "faz": m["faz"], "renovacoes": r.get("renovacoes")}
    if m["faz"] == "agenda":
        # O lead sai da fila até o dia que o CLIENTE pediu — e quem marca isso é
        # `follow_up.marcar`, não um update solto. Escrever `proximo_contato_em` na
        # mão daria a MESMA coluna dois donos: a marcação ficaria sem linha em
        # `follow_up_marcacoes`, sem autor e sem motivo, e a tela do follow-up
        # mostraria um prazo que ninguém sabe de onde veio.
        #
        # MEIO-DIA UTC, e não meia-noite: `'2026-10-20'::date` vira 00:00 UTC, que
        # no Brasil é 21h do dia 19 — o lead voltaria pra fila na véspera, à noite.
        # 12:00 UTC são 9h de Brasília, dentro da janela de atendimento da régua.
        try:
            with c.transaction():
                from datetime import date as _date, time as _time
                dia = _date.fromisoformat((data or "").strip())
                prazo = datetime.combine(dia, _time(12, 0), tzinfo=timezone.utc)
                from finance import follow_up as _fu
                _fu.marcar(c, conta_id, lead_id, prazo, acao=m["rotulo"],
                           membro_id=membro_id, motivo=texto, automatico=True,
                           agora=agora)
            feito["voltar_em"] = dia.isoformat()
        except Exception as e:  # noqa: BLE001 — data torta não desfaz a renovação
            _log.warning("data de retorno inválida no lead %s (%r): %s", lead_id, data, e)
            feito["data_ignorada"] = True
    elif m["faz"] == "avanca":
        prox = proxima_de_venda(c, conta_id, etapa)
        if prox:
            c.execute("update prospeccao set status=%s, atualizado_em=now() "
                      "where id=%s and conta_id=%s", (prox, lead_id, conta_id))
            from finance import funil_regua as _fr
            _fr.registrar_movimento(c, conta_id, lead_id, etapa, prox, MOTIVO_MOV, membro_id)
            feito["moveu_para"] = prox
    return {"ok": True, **feito}
