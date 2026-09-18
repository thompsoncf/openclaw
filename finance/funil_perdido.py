"""O PERDIDO AUTOMÁTICO: o lead que ninguém respondeu sai do quadro sozinho.

Pedido do dono em 18/09/2026, ao remontar o funil da Prime em sete colunas. A
regra dele, com estas palavras:

    "perdido - nao respondeu durante os 7 dias ou nao tem mais interassao ou nao
    respondeu o follow"

e, quando perguntado quantos toques são um follow-up:

    "sao 3 tocs e o 4 fica como perdido"

POR QUE SÃO DOIS PORTÕES, E NÃO SÓ O CALENDÁRIO
Medido na conta 34 em 18/09, nos 334 leads de Contatado: 240 passaram do teto de
7 dias com a bola conosco. Mas desses 240, **122 levaram um único toque** e 51
levaram dois — só 67 foram tocados três vezes ou mais. Fechar por calendário puro
chamaria de "não respondeu" o lead que ninguém chamou: a falha seria nossa e o
carimbo, dele. Por isso o relógio da etapa diz que a janela acabou, e a contagem
de toques diz que a empresa de fato tentou. Os dois, nunca um só.

O TERCEIRO PORTÃO É A BOLA, E ELE NÃO TEM EXCEÇÃO
Dos leads parados além do teto, 28 estão parados porque **o CLIENTE escreveu e
ninguém respondeu**. Marcar esses de perdido é carimbar como desinteresse dele um
silêncio que é nosso. É a mesma medida que deu forma à trava da insistência
(finance/funil_trava), e aqui ela pesa ainda mais: a trava só atrasava uma
mensagem, isto tira o lead do quadro. Bola com o cliente NUNCA fecha.

O CORTE POR DATA EXISTE PORQUE O DONO PEDIU UM
"fazer desse mês de setembro pra frente" — `perdido_desde` guarda esse corte. Sem
ele, ligar a regra numa conta com fila velha fecharia anos de histórico numa
passada só. O corte olha quando o lead ENTROU na etapa, que é o mesmo relógio do
teto (`funil_teto.na_etapa_desde`), nunca `atualizado_em`, que qualquer automação
encosta.

NADA AQUI É IRREVERSÍVEL
Perder é mudar `status` e gravar o motivo — a ficha, a conversa e o histórico
continuam inteiros, e `funil_perda.reativar` devolve o lead a Contatado num
clique. Toda saída deixa linha em `funil_movimentos` com autor (o motor), então
dá pra dizer depois exatamente quem foi fechado por regra e quem foi na mão.

NASCE DESLIGADO. `perdido_modo` é 'off' em toda conta; 'observando' conta o que
teria fechado sem fechar nada.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone

from finance import funil_perda as _perda
from finance import funil_regua as fr
from finance import funil_teto as _teto

_log = logging.getLogger("openclaw.funil_perdido")

#: vizinho dos locks da régua (771147), do follow-up (771148), do teto (771149)
#: e da agenda (771150)
_LOCK = 771151

MODOS = ("off", "observando", "ligado")

#: o motivo de perda que a ficha guarda. Existe em toda conta de eventos e de
#: serviço desde a semente de `raio_x_perfil` — é o primeiro da lista, com o
#: rótulo "Não respondeu — após as tentativas de follow-up".
MOTIVO_PERDA = "nao_respondeu"

#: o motivo do movimento, que é o que separa no histórico o que a regra fechou do
#: que uma pessoa fechou ('manual')
MOTIVO_MOV = "sem_resposta"

#: o padrão dos toques vem da resposta do dono, e é o mesmo número que a
#: temperatura usa pra esfriar um lead (finance/temperatura). Se os dois contassem
#: diferente, o card diria "frio" e a regra pensaria outra coisa.
TOQUES_PADRAO = 3


def config(c, conta_id: int) -> dict:
    """A config do perdido automático, junto da régua — mesma janela, mesma conta.

    A base é a do TETO, e não a da régua, porque é ela que esta config alimenta:
    `elegiveis` entrega o resultado a `funil_teto.leads`, que lê `teto_avisar_antes`
    de dentro dele. Montar em cima da régua deixaria a chave faltando e o motor
    morreria na primeira passada, sem nunca ter fechado nada.
    """
    base = _teto.config(c, conta_id)
    r = c.execute(
        """select perdido_modo, perdido_toques_min, perdido_desde
             from funil_regua where conta_id=%s""", (conta_id,)).fetchone()
    if not r:
        return dict(base, perdido_modo="off", perdido_toques_min=TOQUES_PADRAO,
                    perdido_desde=None)
    return dict(base, perdido_modo=(r[0] or "off"),
                perdido_toques_min=(r[1] if r[1] is not None else TOQUES_PADRAO),
                perdido_desde=r[2])


def toques_de(c, lead_id: int) -> int:
    """Quantas mensagens NOSSAS saíram depois da última do cliente.

    Conta o eco do celular junto (`membro_id` nulo): pro cliente não existe
    diferença entre a mensagem que saiu do app e a que o vendedor digitou no
    telefone — as duas são um toque que ele não respondeu.
    """
    r = c.execute(
        """with ult as (
             select coalesce(max(m.criado_em) filter (where m.direcao='in'),
                             '-infinity'::timestamptz) as ult_in
               from conversas cv join mensagens m on m.conversa_id = cv.id
              where cv.prospeccao_id=%(lead)s)
           select count(*) from conversas cv
             join mensagens m on m.conversa_id = cv.id, ult
            where cv.prospeccao_id=%(lead)s
              and m.direcao='out' and m.criado_em > ult.ult_in""",
        {"lead": lead_id}).fetchone()
    return int(r[0] or 0)


def bola_de(c, lead_id: int) -> str:
    """'cliente' se ele falou por último, senão 'nossa'. Compara IDS, não datas:
    `criado_em` de mensagens quase simultâneas chega fora de ordem, e empatar pro
    lado errado aqui significa fechar quem está esperando resposta."""
    r = c.execute(
        """select coalesce(max(m.id) filter (where m.direcao='in'), 0),
                  coalesce(max(m.id) filter (where m.direcao='out'), 0)
             from conversas cv join mensagens m on m.conversa_id = cv.id
            where cv.prospeccao_id=%s""", (lead_id,)).fetchone()
    ult_in, ult_out = (r[0], r[1]) if r else (0, 0)
    return "cliente" if ult_in > ult_out else "nossa"


def decidir(*, bola: str, estado: str, toques: int, toques_min: int,
            entrou: datetime | None, desde: date | None) -> str:
    """A regra, pura — sem banco, pra caber num teste.

    Devolve 'fecha' ou o nome do portão que barrou. A ORDEM importa: a bola é
    consultada antes de tudo, pelo mesmo motivo da trava.
    """
    if bola == "cliente":
        return "cliente_esperando"
    if estado not in ("vencido", "esgotado"):
        return "no_prazo"
    if toques < toques_min:
        return "sem_follow_up"
    if desde and entrou and entrou.date() < desde:
        return "antes_do_corte"
    return "fecha"


def conversa_por_lead(c, leads: list[int]) -> dict:
    """{lead: {"bola", "toques"}} pra muitos leads numa consulta só.

    A versão um-lead-por-vez custaria quatro consultas por card — 1.300 por ciclo
    na Prime, a cada dois minutos, num poller que ainda tem campanha, lembrete,
    régua, teto e follow-up na mesma passada. É o mesmo cuidado (e o mesmo
    tamanho de erro) que `funil_teto.leads` documenta.
    """
    if not leads:
        return {}
    linhas = c.execute(
        """with ult as (
             select cv.prospeccao_id as lead,
                    coalesce(max(m.id) filter (where m.direcao='in'), 0) as ult_in,
                    coalesce(max(m.id) filter (where m.direcao='out'), 0) as ult_out,
                    coalesce(max(m.criado_em) filter (where m.direcao='in'),
                             '-infinity'::timestamptz) as em_in
               from conversas cv join mensagens m on m.conversa_id = cv.id
              where cv.prospeccao_id = any(%s)
              group by 1)
           select u.lead, u.ult_in, u.ult_out,
                  (select count(*) from conversas cv2
                     join mensagens m2 on m2.conversa_id = cv2.id
                    where cv2.prospeccao_id = u.lead and m2.direcao='out'
                      and m2.criado_em > u.em_in)
             from ult u""", (leads,)).fetchall()
    return {r[0]: {"bola": ("cliente" if r[1] > r[2] else "nossa"), "toques": int(r[3] or 0)}
            for r in linhas}


def elegiveis(c, conta_id: int, agora: datetime | None = None,
              cfg: dict | None = None) -> list[dict]:
    """Os leads que a regra fecharia agora, com o porquê de cada um.

    Só percorre etapa COM teto: etapa sem prazo não tem do que o lead ter passado.
    O estado do prazo vem de `funil_teto.leads`, que já resolve a conta inteira
    numa consulta — e as renovações junto, que é o que impede fechar o lead que o
    vendedor disse estar trabalhando.
    """
    agora = agora or datetime.now(timezone.utc)
    cfg = cfg or config(c, conta_id)
    do_teto = _teto.leads(c, conta_id, agora, cfg)
    if not do_teto:
        return []
    toques_min = max(0, int(cfg.get("perdido_toques_min") or 0))
    desde = cfg.get("perdido_desde")
    conversa = conversa_por_lead(c, [l["id"] for l in do_teto])
    fora = []
    for l in do_teto:
        # lead sem conversa nenhuma: bola nossa e zero toques — ninguém chamou,
        # então os toques barram, que é o resultado certo
        cv = conversa.get(l["id"], {"bola": "nossa", "toques": 0})
        veredito = decidir(bola=cv["bola"], estado=l["estado"], toques=cv["toques"],
                           toques_min=toques_min, entrou=l["desde"], desde=desde)
        fora.append({"id": l["id"], "etapa": l["status"], "nome": l["quem"],
                     "vendedor_id": l["vendedor_id"], "veredito": veredito,
                     "bola": cv["bola"], "toques": cv["toques"],
                     "dias": round(l["dias"], 2), "estado": l["estado"],
                     "entrou": l["desde"]})
    return fora


def fechar(c, conta_id: int, lead: dict, agora: datetime | None = None) -> bool:
    """Move UM lead para Perdido, com motivo e histórico. Idempotente pelo status:
    se alguém mexeu no lead entre a leitura e agora, o update não acha linha e a
    regra se cala — quem mexeu por último foi uma pessoa, e pessoa ganha."""
    agora = agora or datetime.now(timezone.utc)
    etapa = lead["etapa"]
    movido = c.execute(
        """update prospeccao set status='perdido', atualizado_em=now()
            where id=%s and conta_id=%s and status=%s""",
        (lead["id"], conta_id, etapa)).rowcount
    if not movido:
        return False
    fr.registrar_movimento(c, conta_id, lead["id"], etapa, "perdido", MOTIVO_MOV)
    _perda.registrar(c, conta_id, lead["id"], motivo=MOTIVO_PERDA,
                     etapa_origem=etapa, agora=agora)
    return True


def avaliar(c, conta_id: int, agora: datetime | None = None) -> dict:
    """Uma passada nesta conta. Em 'observando' conta e não fecha; em 'ligado' fecha."""
    agora = agora or datetime.now(timezone.utc)
    cfg = config(c, conta_id)
    modo = cfg.get("perdido_modo") or "off"
    vazio = {"modo": modo, "fechados": 0, "simulados": 0, "poupados": 0, "leads": []}
    if modo == "off":
        return vazio
    todos = elegiveis(c, conta_id, agora, cfg)
    alvos = [l for l in todos if l["veredito"] == "fecha"]
    poupados = sum(1 for l in todos if l["veredito"] == "cliente_esperando")
    if modo == "observando":
        return {"modo": modo, "fechados": 0, "simulados": len(alvos),
                "poupados": poupados, "leads": alvos}
    fechados = [l for l in alvos if fechar(c, conta_id, l, agora)]
    return {"modo": modo, "fechados": len(fechados), "simulados": 0,
            "poupados": poupados, "leads": fechados}


def rodar(pool, agora: datetime | None = None) -> dict:
    """Uma passada em todas as contas que ligaram. Chamada pelo poller, junto da
    régua, do teto e do follow-up — sem cron novo no Render."""
    total = {"contas": 0, "fechados": 0, "simulados": 0}
    with pool.connection() as lockc:
        if not lockc.execute("select pg_try_advisory_lock(%s)", (_LOCK,)).fetchone()[0]:
            return total
        try:
            with pool.connection() as c:
                contas = [r[0] for r in c.execute(
                    "select conta_id from funil_regua where perdido_modo <> 'off'").fetchall()]
            for conta_id in contas:
                try:
                    with pool.connection() as c:
                        r = avaliar(c, conta_id, agora)
                        c.commit()
                    total["contas"] += 1
                    total["fechados"] += r["fechados"]
                    total["simulados"] += r["simulados"]
                except Exception:  # noqa: BLE001
                    _log.warning("perdido automático falhou na conta %s", conta_id,
                                 exc_info=True)
        finally:
            lockc.execute("select pg_advisory_unlock(%s)", (_LOCK,))
    return total
