"""Módulo Vendas de Serviços — a ponte pipeline → financeiro.

Motor GENÉRICO de venda consultiva de serviço (o nicho 'tecnologia'/Aladdin é o
primeiro caso; o mesmo fluxo serve advocacia, agência, etc., trocando só o
catálogo). Aqui mora a ação que fecha o negócio:

    fechar_orcamento() — o orçamento cai no financeiro que já existe (módulo
    Empresa), como TÍTULOS A RECEBER:
      • setup  (valor único)      -> título a receber não-recorrente
      • mensal (recorrente)       -> título a receber RECORRENTE, que na baixa
                                     se auto-renova (dar_baixa_titulo do Empresa).

    No modo EVENTO (nicho eventos, migração 147) não existe mensalidade: cada
    parcela do plano de pagamento vira um título no vencimento combinado —
    sinal no Pix hoje, 12x no cartão a partir do mês que vem.

    E no modo EVENTO quem CHAMA fechar_orcamento não é botão nenhum: é a
    assinatura do contrato pelo cliente. O dinheiro se divide em dois momentos —
    o sinal entra no caixa quando cai (lancar_sinal_recebido), porque é dinheiro
    que já entrou; o resto do plano vira conta a receber só quando o cliente
    assina. Nos nichos recorrentes não existe contrato e nada mudou: quem fecha
    continua sendo o botão do funil.

    Assim a receita entra pelo caminho de sempre (livro-caixa, fonte única) e o
    relatório de vendas continua unificado — sem PDV novo pra serviço.

Atômico: o status vira num único UPDATE ... WHERE status<>'fechado' RETURNING e
os dois títulos entram na MESMA transação. Fecha a corrida do duplo-clique (não
gera títulos em dobro) e, se algo falhar no meio, faz rollback total.
"""
from __future__ import annotations

import json
import logging
from datetime import date, datetime, timedelta

from .empresa import _mes_seguinte

_log = logging.getLogger("openclaw.vendas")

CAT_SERVICOS = "Serviços"


# Nichos que emitem orçamento de EVENTO (migração 147). O modo é do NICHO da
# conta, não de quem está criando — e orçamento nasce em quatro portas: painel,
# cockpit do vendedor, "gerar orçamento" da prospecção e o agente. Todas passam
# por aqui; senão a empresa de eventos manda pro cliente uma folha de
# mensalidade dependendo de onde o vendedor clicou.
NICHOS_EVENTO = {"eventos"}


def modo_por_nicho(slug: str | None) -> str:
    return "evento" if (slug or "").strip() in NICHOS_EVENTO else "recorrente"


def modo_do_orcamento(pool, conta_id: int) -> str:
    """O modo do orçamento dessa conta: 'evento' ou 'recorrente'.

    Sem try/except de propósito: se a leitura do nicho falhar, é melhor o
    salvamento falhar do que gravar calado um orçamento no modo errado — o
    cliente receberia uma folha de mensalidade por uma festa. Conta que não
    existe volta sem nicho e cai em 'recorrente', que é o padrão da coluna.
    """
    from finance import empresa as emp
    return modo_por_nicho((emp.obter_dados_empresa(pool, conta_id) or {}).get("nicho"))


def vende_data(pool, conta_id: int) -> bool:
    """Esta conta VENDE DATA? (nicho de eventos)

    É o que decide se a tela mostra o vocabulário de data segurada — barra de
    fixado/segurado no calendário, "Só segurar a data" no formulário, a legenda que
    fala de sinal. Clínica, loja e escritório não seguram data esperando sinal:
    pra eles isso é ruído numa tela que já estava certa.

    TOLERANTE, ao contrário de modo_do_orcamento: lá, falhar a leitura do nicho tem
    que derrubar o salvamento (gravar no modo errado manda uma folha de mensalidade
    por uma festa). Aqui o pior caso é a Agenda deixar de mostrar um enfeite — e ela
    precisa abrir de qualquer jeito.
    """
    try:
        return modo_do_orcamento(pool, conta_id) == "evento"
    except Exception:  # noqa: BLE001
        return False


# SQL do título a receber (reusa a tabela titulos do módulo Empresa).
# orcamento_id/parcela_idx (migração 162) só vêm preenchidos no modo evento: são
# eles que deixam voltar do título pra parcela sem casar por texto de descrição.
_SQL_TITULO = """insert into titulos
    (conta_id, tipo, descricao, contraparte, valor_centavos, vencimento,
     categoria, recorrente, criado_por, orcamento_id, parcela_idx)
  values (%s, 'receber', %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id"""


# ---------------------------------------------------------------- o SINAL
# Ponto único da regra "qual parcela é o sinal". O gerador de parcelas do
# orçamento de evento escreve, na PRIMEIRA linha, "Sinal — confirma a reserva da
# data"; é dali que sai o valor e é essa parcela que recebe baixa quando o dono
# confirma o recebimento. web/proposta.sinal_do_orcamento delega pra cá — duas
# leituras diferentes de "o que é o sinal" seria o começo de dois números.

def indice_do_sinal(parcelas) -> int | None:
    """A posição da parcela do sinal em `orcamentos.parcelas`, ou None.

    Só a primeira conta: parcela de sinal no meio do plano não é sinal, é parcela
    com observação parecida. Orçamento montado na mão, sem essa linha, não tem
    sinal — e a data é reservada direto (ver web/proposta._reservar_na_agenda).

    SEMPRE sobre a lista normalizada por `_parcelas` — nunca sobre o jsonb cru,
    mesmo quando ele já vem como list. É `_parcelas` que fechar_orcamento percorre
    pra numerar `parcela_idx`, e ela DESCARTA linha de valor zero. Contando no cru,
    um plano com uma primeira linha vazia daria índices diferentes nos dois lados —
    e o índice aqui é o que escolhe qual título recebe baixa. Errar isso é dar por
    recebida uma parcela que ninguém pagou."""
    itens = _parcelas(parcelas)
    if not itens:
        return None
    return 0 if "sinal" in str(itens[0].get("obs") or "").lower() else None


def valor_do_sinal(parcelas) -> int:
    """Quanto é o sinal, em centavos, ou 0 se o orçamento não pede sinal."""
    i = indice_do_sinal(parcelas)
    return int(_parcelas(parcelas)[i]["valor_centavos"]) if i is not None else 0


def _itens_curtos(bruto, limite: int = 8) -> list[dict]:
    """Só o que a ficha da agenda mostra de cada item: nome e quantidade."""
    if isinstance(bruto, str):
        try:
            bruto = json.loads(bruto)
        except ValueError:
            return []
    out = []
    for it in (bruto or [])[:limite]:
        if not isinstance(it, dict):
            continue
        nome = str(it.get("nome") or "").strip()
        if nome:
            try:
                qtd = int(it.get("qtd") or 1)
            except (TypeError, ValueError):
                qtd = 1
            out.append({"nome": nome, "qtd": max(1, qtd)})
    return out


# ------------------------------------------------------- os PAGAMENTOS do orçamento
#
# Um orçamento não tem UM pagamento: tem o sinal e mais N parcelas. Até 19/08/2026
# isso só se enxergava em dois lugares separados — o botão "Sinal recebido" no
# funil e a lista de títulos no módulo Empresa —, e nenhum dos dois respondia a
# pergunta que a empresa faz: "quanto desta festa já entrou?".
#
# ANTES DO CONTRATO FECHAR NÃO EXISTE TÍTULO. O plano é um jsonb, e o único
# pagamento que pode ter acontecido é o sinal (`orcamentos.sinal_pago_em`). Depois
# do fechamento cada parcela vira título, e é o título que manda — é nele que a
# baixa acontece e é dele que sai o livro-caixa. Esta função lê os dois mundos e
# devolve UMA lista, porque a tela não tem por que saber em qual deles está.


def pagamentos_do_orcamento(pool, conta_id: int, orcamento_id: int) -> dict:
    """{parcelas: [...], total, recebido, falta} do orçamento.

    Cada parcela traz `idx` (o mesmo `parcela_idx` dos títulos e do comprovante),
    rótulo, valor, vencimento, se está paga e quando."""
    from datetime import date as _date
    with pool.connection() as c:
        r = c.execute("select parcelas, sinal_pago_em, coalesce(status,'') "
                      "  from orcamentos where id=%s and conta_id=%s",
                      (orcamento_id, conta_id)).fetchone()
        if not r:
            return {}
        parcelas_cru, sinal_pago_em, _status = r
        try:
            titulos = {t[0]: {"pago": t[1] == "pago", "pago_em": t[2], "id": t[3]}
                       for t in c.execute(
                           """select parcela_idx, status, pago_em, id
                                from titulos
                               where conta_id=%s and orcamento_id=%s
                                 and parcela_idx is not null
                                 and status <> 'cancelado'""",
                           (conta_id, orcamento_id)).fetchall()}
        except Exception:  # noqa: BLE001 — conta sem o módulo financeiro
            titulos = {}

    itens = _parcelas(parcelas_cru)
    i_sinal = indice_do_sinal(parcelas_cru)
    n = len(itens)
    n_normais = n - (1 if i_sinal is not None else 0)
    saida, recebido, total = [], 0, 0
    ordem = 0
    hoje = _date.today()
    for idx, p in enumerate(itens):
        valor = int(p["valor_centavos"])
        total += valor
        eh_sinal = (idx == i_sinal)
        if eh_sinal:
            rotulo = "Sinal"
        else:
            ordem += 1
            rotulo = f"Parcela {ordem} de {n_normais}" if n_normais > 1 else "Pagamento"
        t = titulos.get(idx)
        # o sinal tem caminho próprio: ele é confirmado ANTES de o contrato fechar,
        # quando título nenhum existe ainda. Depois de fechar, o título manda — é
        # nele que a baixa acontece e é dele que sai o livro-caixa.
        pago = t["pago"] if t else (eh_sinal and bool(sinal_pago_em))
        if not pago and eh_sinal and sinal_pago_em:
            pago = True          # sinal confirmado antes de o título existir
        quando = None
        if t and t["pago"]:
            quando = t["pago_em"]
        elif pago and eh_sinal and sinal_pago_em:
            quando = sinal_pago_em.date()
        if pago:
            recebido += valor
        venc = p.get("venc")
        saida.append({
            "idx": idx, "rotulo": rotulo, "valor_centavos": valor,
            "venc": venc, "forma": p.get("forma") or "", "obs": p.get("obs") or "",
            "pago": pago, "pago_em": quando, "titulo_id": (t["id"] if t else None),
            "vence_hoje": bool(not pago and venc and str(venc)[:10] == hoje.isoformat()),
        })
    return {"parcelas": saida, "total": total, "recebido": recebido,
            "falta": max(0, total - recebido)}


def resumo_pagamentos(parcelas, sinal_pago_em, titulos_pagos=(), com_comprovante=()) -> dict:
    """{pagas, total, sem_comprovante} de UMA linha do funil.

    Pura, e separada de `pagamentos_do_orcamento`, por causa do N+1: a lista do
    funil traz até 50 orçamentos, e chamar a função completa por linha seriam 100
    consultas. Quem chama busca os títulos e os comprovantes de TODAS as linhas de
    uma vez e passa por aqui — dois SELECTs no total.

    `sem_comprovante` conta só parcela PAGA. Cobrar comprovante do que ainda nem
    venceu encheria a tela de âmbar que ninguém pode resolver — a mesma armadilha
    do aviso do contrato."""
    itens = _parcelas(parcelas)
    if not itens:
        return {"pagas": 0, "total": 0, "sem_comprovante": 0}
    i_sinal = indice_do_sinal(parcelas)
    pagos, faltando = 0, 0
    for idx in range(len(itens)):
        pago = idx in set(titulos_pagos) or (idx == i_sinal and bool(sinal_pago_em))
        if not pago:
            continue
        pagos += 1
        if idx not in set(com_comprovante):
            faltando += 1
    return {"pagas": pagos, "total": len(itens), "sem_comprovante": faltando}


# ------------------------------------------------- o ESTADO DA DATA no funil
#
# POR QUE ISSO EXISTE. A data de um evento aprovado tem quatro estados, e até
# 19/08/2026 a linha do funil só sabia desenhar UM deles — a pré-reserva
# correndo. Os outros três ficavam com a mesma cara:
#
#     reservada  compromisso firme na agenda           tudo certo
#     segurada   pré-reserva correndo, esperando sinal  já aparecia
#     fora       nunca entrou na agenda                 PARECIA tudo certo
#     liberada   entrou e o prazo do sinal venceu       PARECIA tudo certo
#
# E "fora" não é hipótese: a reserva nasce num lugar só (o cliente assinando o
# link público) e sai calada por quatro portas — orçamento sem hora de início,
# exceção engolida, processo reiniciado antes da tarefa de segundo plano rodar,
# ou modo diferente de evento. Nenhuma delas deixava rastro na tela.
#
# NADA DISSO PEDE CONFIGURAÇÃO NOVA. Os quatro estados são lidos do que já
# existe; o único ajuste da empresa continua sendo "Segurar por N dias", na aba
# Agenda.

DATA_RESERVADA = "reservada"
DATA_SEGURADA = "segurada"
DATA_FORA = "fora"
DATA_LIBERADA = "liberada"

# Depois do aceite a data virou promessa ao cliente. Antes disso não há o que
# cobrar: rascunho e proposta enviada não reservam nada, por definição.
_APROVADOS = ("aprovada", "fechado")


def estado_da_data(*, status, modo, evento, evento_status, pre_reserva_ate,
                   hoje=None) -> dict | None:
    """O que a linha do funil diz sobre a data — ou None quando não diz nada.

    Devolve `estado`, o `texto` do selo, a `dica` que explica sem jargão e a
    `acao` que conserta (None quando não há o que fazer).

    DEVOLVE None QUANDO A DATA JÁ PASSOU. Um alarme vermelho eterno num evento de
    julho não é informação: é a mesma armadilha do aviso do contrato, que
    disparava sem ter o que consertar e ensinava o dono a ignorar o próximo. Data
    vencida não tem conserto — some do funil em silêncio.

    Pura de propósito: recebe as colunas que a consulta do funil já traz e não
    volta ao banco, então a REDAÇÃO — que é o que o dono lê — dá pra testar sem
    subir tela nenhuma."""
    from finance import agenda as ag

    if (modo or "") != "evento" or (status or "") not in _APROVADOS:
        return None

    ev = evento or {}
    dia = ag.parse_data(ev.get("data"))
    hoje = hoje or ag.agora_brt().date()
    # sem data e sem compromisso, nada foi prometido
    if dia is None and not evento_status:
        return None
    if dia is not None and dia < hoje:
        return None

    if evento_status == ag.PRE_RESERVADO:
        ate = (pre_reserva_ate.astimezone(ag.BRT).strftime("%d/%m %H:%M")
               if pre_reserva_ate else "")
        return {"estado": DATA_SEGURADA,
                "texto": f"Data segurada até {ate}" if ate else "Data segurada",
                "dica": "O cliente aprovou, mas o sinal ainda não foi confirmado. "
                        "Passando o prazo, a data libera sozinha.",
                "acao": "sinal"}

    if evento_status == "ativo":
        return {"estado": DATA_RESERVADA, "texto": "Data reservada",
                "dica": "A data está firme na agenda da empresa.", "acao": None}

    if evento_status == "cancelado":
        return {"estado": DATA_LIBERADA, "texto": "Data liberada",
                "dica": "O prazo do sinal venceu (ou alguém soltou a data) e ela "
                        "voltou a ficar livre na agenda. Se o cliente ainda quer, "
                        "dá pra segurar de novo.",
                "acao": "resegurar"}

    # nenhum compromisso: a aprovação não virou data. O motivo mais comum tem
    # conserto próprio, então ele é dito em vez de um "deu erro" genérico.
    sem_hora = not (ev.get("inicio") or "").strip()
    return {"estado": DATA_FORA, "texto": "Fora da agenda",
            "dica": ("Falta a hora de início do evento — sem ela a data não entra "
                     "na agenda. Preencha em “O evento” e marque."
                     if sem_hora else
                     "O cliente aprovou, mas a data não entrou na agenda. "
                     "Toque em Marcar agora."),
            "acao": "marcar"}


def fichas_de_eventos(pool, conta_id: int, evento_ids) -> dict[int, dict]:
    """{id_do_compromisso: ficha} — o orçamento e o PAGAMENTO de cada festa.

    Mora aqui, e não em finance/agenda, porque é a mesma ponte que este módulo já
    é: orçamento -> título a receber. A agenda só pede a leitura.

    O QUE ISSO RESOLVE. A agenda guardava o evento e uma FRASE colada na aprovação
    ("Orçamento Nº 1 · 100 convidados · aprovado pelo cliente"). Não é dado: não dá
    pra somar nem filtrar, e envelhece — segue dizendo "aprovado" num orçamento que
    já foi fechado, com o sinal vencendo. Tudo que a ficha mostra já estava gravado;
    faltava alguém ler.

    O PAGAMENTO tem dois estados, e a diferença importa:
      • contrato FECHADO -> existem títulos a receber, e eles são a verdade do
        dinheiro (é neles que a baixa acontece, é deles que sai o livro-caixa);
      • orçamento só APROVADO -> não há título nenhum ainda. Aí a ficha mostra o
        PLANO que o cliente aceitou, dizendo que é plano — inventar "0% recebido"
        de algo que nem virou cobrança seria mentira com cara de número.

    Duas consultas no total, independentemente de quantos eventos — a tela do mês
    pede a lista inteira de uma vez.
    """
    ids = [int(i) for i in (evento_ids or [])]
    if not ids:
        return {}
    with pool.connection() as c:
        try:
            orcs = c.execute(
                """select o.evento_agenda_id, o.id, o.numero, coalesce(o.status,''),
                          coalesce(o.empresa, o.cliente, ''), coalesce(o.whatsapp, o.telefone, ''),
                          o.evento, o.itens, o.parcelas,
                          coalesce(o.primeiro_ano_centavos, o.setup_centavos, 0)
                     from orcamentos o
                    where o.conta_id=%s and o.evento_agenda_id = any(%s)""",
                (conta_id, ids)).fetchall()
        except Exception as e:  # noqa: BLE001 — conta sem o módulo de orçamentos
            _log.info("fichas_de_eventos: sem orçamentos legíveis: %s", type(e).__name__)
            return {}
        por_orc = {r[1]: r for r in orcs}
        titulos = []
        if por_orc:
            titulos = c.execute(
                """select orcamento_id, status, valor_centavos, vencimento, pago_em
                     from titulos
                    where conta_id=%s and orcamento_id = any(%s) and status <> 'cancelado'
                    order by vencimento, id""",
                (conta_id, list(por_orc))).fetchall()

    pagto: dict[int, dict] = {}
    hoje = date.today()
    for oid, status, valor, venc, _pago_em in titulos:
        p = pagto.setdefault(oid, {"total": 0, "pago": 0, "aberto": 0,
                                   "vencidas": 0, "vencidas_centavos": 0, "proxima": None})
        v = int(valor or 0)
        p["total"] += v
        if status == "pago":
            p["pago"] += v
        else:
            p["aberto"] += v
            if venc and venc < hoje:
                p["vencidas"] += 1
                p["vencidas_centavos"] += v
            # a PRÓXIMA a vencer: a primeira em aberto na ordem de vencimento (a
            # consulta já vem ordenada), inclusive se já está vencida — é ela que o
            # dono precisa resolver primeiro.
            if p["proxima"] is None:
                p["proxima"] = {"vencimento": venc, "valor_centavos": v,
                                "vencida": bool(venc and venc < hoje)}

    fichas: dict[int, dict] = {}
    for r in orcs:
        (ev_id, oid, numero, status, cliente, contato, evento, itens, parcelas, total) = r
        evento = evento if isinstance(evento, dict) else {}
        pg = pagto.get(oid)
        plano = sum(int(p.get("valor_centavos") or 0)
                    for p in (_parcelas(parcelas) or []))
        ficha = {
            "orcamento_id": oid, "numero": numero, "status": status,
            "cliente": cliente.strip(), "contato": contato.strip(),
            "tipo": str(evento.get("tipo") or "").strip(),
            "convidados": evento.get("convidados"),
            "local": str(evento.get("local") or "").strip(),
            "itens": _itens_curtos(itens),
            "total_centavos": int(total or 0),
            "tem_titulos": bool(pg),
        }
        if pg:
            ficha.update({
                "titulos_centavos": pg["total"], "pago_centavos": pg["pago"],
                "aberto_centavos": pg["aberto"],
                "pct": int(round(100 * pg["pago"] / pg["total"])) if pg["total"] else 0,
                "vencidas": pg["vencidas"], "vencidas_centavos": pg["vencidas_centavos"],
                "proxima": pg["proxima"],
            })
        else:
            # sem título: o que existe é o PLANO aceito, não cobrança. Sem `pct`
            # justamente pra tela não mostrar "0% recebido" de algo que ninguém
            # cobrou ainda.
            ficha.update({"plano_centavos": plano, "pct": None, "vencidas": 0,
                          "proxima": None})
        fichas[ev_id] = ficha
    return fichas


def reabrir_proposta(pool, conta_id: int, orcamento_id: int, evento_agenda_id: int,
                     sinal_pago_em, evento_novo: dict | None = None) -> dict:
    """Editar uma proposta JÁ APROVADA desfaz a assinatura — e a data na agenda tem
    que acompanhar. Este é o caminho de volta que não existia.

    Até aqui, editar uma proposta aprovada revertia o status pra 'enviado' e limpava
    a assinatura, mas NÃO tocava em `evento_agenda_id` nem no sinal: a data seguia
    ocupada por um orçamento que voltou a ser rascunho, e uma re-aprovação nem
    remarcava (o `_reservar_na_agenda` sai fora quando já existe compromisso), nem
    movia a data se o cliente tivesse escolhido outro dia.

    A REGRA é o dinheiro:

      • SINAL PAGO -> a data é do cliente e não se mexe nela. Se a edição mudou
        data/hora, o compromisso é REMARCADO pra janela nova — perder a data de quem
        pagou seria o pior erro possível aqui.
      • SINAL NÃO PAGO -> nada sustenta a reserva. A data é liberada e o vínculo se
        desfaz, pra a próxima aprovação criar do zero (com a data nova, se mudou).

    Devolve {liberou, remarcou} pra quem chamou contar na tela.
    """
    from finance import agenda as ag
    liberou = remarcou = False
    if sinal_pago_em:
        ini, fim = ag.janela_evento((evento_novo or {}).get("data"),
                                    (evento_novo or {}).get("inicio"),
                                    (evento_novo or {}).get("fim"))
        atual = ag.evento_por_id(pool, conta_id, evento_agenda_id)
        if ini and atual and (atual["inicio"] != ini or atual.get("fim") != fim):
            remarcou = ag.remarcar_evento(pool, conta_id, evento_agenda_id, ini, fim)
        return {"liberou": False, "remarcou": remarcou}

    liberou = ag.cancelar_evento(pool, conta_id, evento_agenda_id)
    with pool.connection() as c:
        # o vínculo E o valor congelado do sinal saem juntos: a próxima aprovação
        # relê as parcelas do orçamento editado e grava de novo. Deixar o
        # `sinal_centavos` velho faria a ficha da agenda mostrar o preço de antes.
        c.execute("update orcamentos set evento_agenda_id=null, sinal_centavos=null "
                  " where id=%s and conta_id=%s", (int(orcamento_id), conta_id))
        c.commit()
    return {"liberou": liberou, "remarcou": False}


def confirmar_sinal(pool, conta_id: int, orcamento_id: int) -> dict:
    """O sinal caiu. Ponto único da regra, pros dois botões que a apertam: o do
    funil (Serviços) e o da caixa do dia (Agenda).

    Confirmação MANUAL de propósito. O dono recebe o Pix como já recebe hoje e
    aperta — o sistema não cobra nada nem fica esperando integração. Se um dia a
    cobrança automática existir, ela só passa a chamar esta função.

    Mexe em DUAS coisas, nesta ordem: a DATA (a pré-reserva vira compromisso firme)
    e o DINHEIRO (o título daquela parcela recebe baixa, na data em que o sinal
    caiu). O pagamento é gravado PRIMEIRO; as duas consequências vêm depois, cada
    uma podendo falhar sozinha sem desfazer o registro — apertar de novo retoma o
    que faltou.

    Devolve {ok, ja_estava, reserva_firmada, titulo_baixado} ou {ok: False, erro}.
    """
    from finance import agenda as ag
    with pool.connection() as c:
        r = c.execute("""select sinal_pago_em, evento_agenda_id, parcelas
                           from orcamentos where id=%s and conta_id=%s""",
                      (int(orcamento_id), conta_id)).fetchone()
        if not r:
            return {"ok": False, "erro": "orçamento não encontrado"}
        ja_pago, agenda_id, parcelas = r
        era_novo = not ja_pago
        if era_novo:
            ja_pago = c.execute(
                "update orcamentos set sinal_pago_em=now() where id=%s and conta_id=%s "
                "returning sinal_pago_em", (int(orcamento_id), conta_id)).fetchone()[0]
            c.commit()
    # a data vira firme. Fora da transação: confirmar o pagamento é o que não pode
    # se perder — se a agenda falhar, o registro fica e o botão retoma depois.
    firmou = False
    if agenda_id:
        try:
            firmou = ag.confirmar_pre_reserva(pool, conta_id, int(agenda_id))
        except Exception as e:  # noqa: BLE001
            _log.warning("confirmar_sinal %s: não firmou o compromisso %s: %s: %s",
                         orcamento_id, agenda_id, type(e).__name__, e)
    # e o DINHEIRO entra AGORA, na data em que caiu. O título do sinal nasce aqui
    # e já entra baixado — não espera o fechamento.
    #
    # Antes ele só nascia no "Fechar contrato", e por isso quem confirmasse o sinal
    # primeiro via a data firmar e o dinheiro não aparecer em canto nenhum. O resto
    # do plano continua esperando a assinatura: dinheiro que entrou se registra,
    # negócio que ninguém assinou não vira conta a receber.
    baixado = None
    try:
        baixado = lancar_sinal_recebido(pool, conta_id, int(orcamento_id),
                                        parcelas, ja_pago)
    except Exception as e:  # noqa: BLE001
        _log.warning("confirmar_sinal %s: lançamento do sinal falhou: %s: %s",
                     orcamento_id, type(e).__name__, e)
    # e o CONTRATO nasce aqui. Até a 164 ele era uma condição reavaliada a cada
    # carregamento da folha (tem nicho de evento? aprovada? sinal pago?); virou um
    # fato: existe uma linha de contrato, com número e estado próprios. Só no nicho
    # de eventos — `criar_para_orcamento` devolve None no resto.
    contrato_id = None
    try:
        from finance import contrato as ctr
        with pool.connection() as c:
            tot = c.execute("select coalesce(primeiro_ano_centavos, setup_centavos, 0) "
                            "from orcamentos where id=%s and conta_id=%s",
                            (int(orcamento_id), conta_id)).fetchone()
        ct = ctr.criar_para_orcamento(pool, conta_id, int(orcamento_id),
                                      valor_centavos=int(tot[0]) if tot else None)
        contrato_id = ct["id"] if ct else None
    except Exception as e:  # noqa: BLE001 — o contrato é consequência, não o registro
        _log.warning("confirmar_sinal %s: não deu pra criar o contrato: %s: %s",
                     orcamento_id, type(e).__name__, e)
    return {"ok": True, "ja_estava": not era_novo, "reserva_firmada": firmou,
            "titulo_baixado": baixado, "contrato_id": contrato_id}


def baixar_titulo_do_sinal(pool, conta_id: int, orcamento_id: int, parcelas,
                           pago_em) -> int | None:
    """Dá baixa no título da parcela do sinal, NA DATA EM QUE O SINAL CAIU.

    O dinheiro entrou quando o Pix caiu, não quando o dono apertou o botão nem
    quando fechou o contrato — e a data do lançamento decide o MÊS da receita.
    Por isso `pago_em` vem de `orcamentos.sinal_pago_em` e não de `date.today()`.

    Idempotente por baixo (dar_baixa_titulo só age em título 'aberto'), então
    pode ser chamado pelos dois caminhos sem coordenação:
      • "Sinal recebido" com o contrato JÁ fechado -> o título existe, recebe baixa;
      • "Fechar contrato" com o sinal JÁ confirmado -> o título nasce e recebe
        baixa logo em seguida.
    Devolve o id do título baixado, ou None se não havia o que baixar.
    """
    i = indice_do_sinal(parcelas)
    if i is None or not pago_em:
        return None
    with pool.connection() as c:
        r = c.execute(
            """select id from titulos
                where conta_id=%s and orcamento_id=%s and parcela_idx=%s
                  and status='aberto'
                order by id limit 1""",
            (conta_id, orcamento_id, i)).fetchone()
    if not r:
        return None
    from finance import empresa as emp
    quando = pago_em.date() if isinstance(pago_em, datetime) else pago_em
    res = emp.dar_baixa_titulo(pool, conta_id, r[0], data_pagto=quando)
    return r[0] if res.get("ok") else None


def _idx_com_titulo(c, conta_id: int, orcamento_id: int) -> set[int]:
    """Quais parcelas deste orçamento JÁ viraram título.

    Existe porque os títulos deixaram de nascer todos de uma vez: o do sinal nasce
    quando o dinheiro cai, o resto nasce na assinatura. Sem esta consulta, a
    segunda etapa duplicaria a primeira.

    Casa por `parcela_idx`, não pelo texto da descrição — foi pra isso que a
    migração 162 existiu."""
    rs = c.execute(
        """select parcela_idx from titulos
            where conta_id=%s and orcamento_id=%s and parcela_idx is not null""",
        (conta_id, int(orcamento_id))).fetchall()
    return {int(r[0]) for r in rs}


def lancar_sinal_recebido(pool, conta_id: int, orcamento_id: int, parcelas,
                          pago_em, criado_por: int | None = None) -> int | None:
    """O sinal caiu: o título DELE nasce e já entra baixado, na data em que caiu.

    Só ele. O resto do plano espera a assinatura do contrato — é a regra nova:
    dinheiro que entrou é registrado quando entra, mas negócio que ninguém assinou
    não vira conta a receber.

    Antes, o único caminho do sinal até o livro-caixa passava pelo "Fechar
    contrato" (é o fechamento que criava os títulos). Quem confirmasse o sinal
    antes de fechar via a data firmar e o dinheiro não aparecer em lugar nenhum —
    foi o que aconteceu no teste de 16/08/2026, com 2min47 de buraco entre uma
    coisa e outra.

    Idempotente pelos dois lados: se o título já existe (contrato fechado antes),
    só recebe baixa; se já está pago, `dar_baixa_titulo` não age duas vezes.
    Devolve o id do título, ou None se este orçamento não tem sinal.
    """
    i = indice_do_sinal(parcelas)
    if i is None or not pago_em:
        return None
    itens = _parcelas(parcelas)
    with pool.connection() as c:
        if i not in _idx_com_titulo(c, conta_id, orcamento_id):
            r = c.execute(
                """select empresa, cliente from orcamentos
                    where id=%s and conta_id=%s""",
                (int(orcamento_id), conta_id)).fetchone()
            if not r:
                return None
            contraparte = (r[0] or r[1] or "").strip()
            p = itens[i]
            base = f"Evento — {contraparte}".strip(" —")
            rotulo = p["obs"] or f"parcela {i + 1}/{len(itens)}"
            c.execute(
                _SQL_TITULO,
                (conta_id, f"{base} · {rotulo}"[:200], contraparte,
                 p["valor_centavos"], _venc(p["venc"], date.today()),
                 CAT_SERVICOS, False, criado_por, int(orcamento_id), i))
            c.commit()
    # a baixa abre a própria conexão, então fica fora do `with` — mesmo cuidado
    # que o fechar_orcamento já tomava.
    return baixar_titulo_do_sinal(pool, conta_id, int(orcamento_id), parcelas, pago_em)


def _venc(v, padrao: date) -> date:
    """Vencimento da parcela: aceita date, ISO ('2025-11-13') e 'dd/mm/aaaa'.
    O que não der pra ler cai no padrão — parcela sem data válida vira título
    com vencimento, nunca título perdido."""
    if isinstance(v, datetime):
        return v.date()
    if isinstance(v, date):
        return v
    s = str(v or "").strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return padrao


def _parcelas(bruto) -> list[dict]:
    """As parcelas do jsonb, já filtradas: só o que tem valor > 0 vira título."""
    if isinstance(bruto, str):
        try:
            bruto = json.loads(bruto)
        except ValueError:
            bruto = []
    itens = []
    for p in (bruto or []):
        if not isinstance(p, dict):
            continue
        valor = int(p.get("valor_centavos") or 0)
        if valor > 0:
            itens.append({"valor_centavos": valor, "venc": p.get("venc"),
                          "forma": (p.get("forma") or "").strip(),
                          "obs": (p.get("obs") or "").strip()})
    return itens


def fechar_orcamento(pool, conta_id: int, orcamento_id: int,
                     criado_por: int | None = None,
                     dias_setup: int = 7,
                     por_assinatura: bool = False) -> dict:
    """Fecha o orçamento e gera os títulos a receber.

    • modo 'recorrente' (o de sempre): setup (único) + mensalidade (recorrente).
    • modo 'evento': UM título por parcela do plano de pagamento, no vencimento
      combinado — evento não tem mensalidade, tem sinal + parcelas. Orçamento de
      evento sem plano de pagamento cai no título único do valor total.

    conta_id é a conta que EMITE (a empresa de serviço, ex.: Aladdin) — os títulos
    são dela. Idempotente: orçamento já fechado não gera de novo, e parcela que já
    virou título (o sinal, tipicamente) não vira de novo.

    QUEM PODE FECHAR, no nicho de eventos: só a assinatura do cliente. É o que
    `por_assinatura=True` diz — e ele vem de `contrato.assinar`, nunca da tela. Sem
    isso, dava pra gerar contas a receber e lançar receita de um negócio que ninguém
    assinou; aconteceu em 16/08/2026 e é o buraco que esta trava fecha. Nos nichos
    recorrentes não existe contrato pra assinar e nada muda: quem fecha é o botão.

    A trava mora AQUI, e não só na tela, pelo motivo de sempre: o pedido vem do
    navegador, e navegador não é fonte confiável.
    """
    hoje = date.today()
    # a porta do nicho primeiro: conta recorrente nem chega a perguntar por contrato
    if not por_assinatura:
        from finance import contrato as ctr
        if ctr.exige_assinatura(pool, conta_id) and not ctr.assinado_do_orcamento(
                pool, conta_id, int(orcamento_id)):
            return {"ok": False, "erro": "O cliente ainda não assinou o contrato. "
                                         "O financeiro abre quando ele assinar."}
    with pool.connection() as c:
        # trava atômica + escopo por conta: só o dono fecha, e só o primeiro a
        # fechar segue; os demais (duplo-clique) voltam vazios.
        orc = c.execute(
            """update orcamentos set status='fechado', atualizado_em=now()
                where id=%s and conta_id=%s and status <> 'fechado'
             returning empresa, cliente, setup_centavos, mensal_centavos,
                       coalesce(modo,'recorrente'), parcelas, primeiro_ano_centavos,
                       sinal_pago_em""",
            (orcamento_id, conta_id),
        ).fetchone()
        if not orc:
            estado = c.execute(
                "select status from orcamentos where id=%s and conta_id=%s",
                (orcamento_id, conta_id),
            ).fetchone()
            if not estado:
                return {"ok": False, "erro": "Orçamento não encontrado."}
            return {"ok": False, "erro": f"Orçamento já está '{estado[0]}'."}

        (empresa, cliente, setup_cent, mensal_cent, modo, parcelas_raw, total_cent,
         sinal_pago_em) = orc
        contraparte = (empresa or cliente or "").strip()
        setup_cent = int(setup_cent or 0)
        mensal_cent = int(mensal_cent or 0)

        if modo == "evento":
            parcelas = _parcelas(parcelas_raw)
            base = f"Evento — {contraparte}".strip(" —")
            ids = []
            # o SINAL já nasceu lá atrás, quando o dinheiro caiu — e já entrou
            # baixado. Recriar aqui duplicaria o título e a receita.
            ja_tem = _idx_com_titulo(c, conta_id, orcamento_id)
            for i, p in enumerate(parcelas, 1):
                if (i - 1) in ja_tem:
                    continue
                # a observação da parcela ("Sinal", "12x no cartão") é o que a
                # empresa escreveu pro cliente — vale mais na conciliação do que
                # um "parcela 2/13" genérico, então ela manda quando existe.
                rotulo = p["obs"] or f"parcela {i}/{len(parcelas)}"
                ids.append(c.execute(
                    _SQL_TITULO,
                    (conta_id, f"{base} · {rotulo}"[:200], contraparte,
                     p["valor_centavos"], _venc(p["venc"], hoje + timedelta(days=dias_setup)),
                     CAT_SERVICOS, False, criado_por, orcamento_id, i - 1),
                ).fetchone()[0])
            # sem plano de pagamento: um título só, com o total do evento — o
            # COM desconto (primeiro_ano_centavos), não a soma bruta dos itens.
            #
            # A condição olha `parcelas`, NÃO `ids`: desde que o sinal passou a
            # nascer antes, `ids` pode voltar vazio porque tudo já existia — e aí
            # testar `not ids` criaria um título do TOTAL por cima das parcelas
            # que já estavam lá, dobrando a receita do evento.
            total_evento = int(total_cent or 0) or setup_cent
            if not parcelas and not ja_tem and total_evento > 0:
                ids.append(c.execute(
                    _SQL_TITULO,
                    (conta_id, base, contraparte, total_evento,
                     hoje + timedelta(days=dias_setup), CAT_SERVICOS, False, criado_por,
                     orcamento_id, None),
                ).fetchone()[0])
            c.commit()
            evento_ids = ids

        else:
            evento_ids = None
            setup_id = mensal_id = None
            if setup_cent > 0:
                setup_id = c.execute(
                    _SQL_TITULO,
                    (conta_id, f"Setup — {contraparte}".strip(" —"), contraparte,
                     setup_cent, hoje + timedelta(days=dias_setup), CAT_SERVICOS,
                     False, criado_por, None, None),
                ).fetchone()[0]
            if mensal_cent > 0:
                mensal_id = c.execute(
                    _SQL_TITULO,
                    (conta_id, f"Mensalidade — {contraparte}".strip(" —"), contraparte,
                     mensal_cent, _mes_seguinte(hoje), CAT_SERVICOS, True, criado_por,
                     None, None),
                ).fetchone()[0]
            c.commit()

    if evento_ids is None:
        return {"ok": True, "modo": "recorrente", "setup_titulo_id": setup_id,
                "mensal_titulo_id": mensal_id}

    # O SINAL JÁ RECEBIDO não pode nascer como título em aberto: o dinheiro está no
    # bolso desde que o Pix caiu, e o lançamento sai na DATA DO SINAL — é ela que
    # decide o mês da receita, não o dia em que se apertou "Fechar contrato".
    #
    # Fora da transação de propósito, e depois do `with`: o contrato fechado é o
    # que não pode se perder, e dar_baixa_titulo abre a própria conexão. Se a baixa
    # falhar, o título fica aberto e o botão "Sinal recebido" (ou a baixa na mão)
    # resolve depois — nada fica duplicado, porque a baixa só age em 'aberto'.
    sinal_baixado = None
    if sinal_pago_em:
        try:
            sinal_baixado = baixar_titulo_do_sinal(pool, conta_id, orcamento_id,
                                                   parcelas_raw, sinal_pago_em)
        except Exception as e:  # noqa: BLE001
            _log.warning("fechar_orcamento %s: título do sinal não recebeu baixa: %s: %s",
                         orcamento_id, type(e).__name__, e)
    return {"ok": True, "modo": "evento", "titulos": evento_ids,
            "setup_titulo_id": None, "mensal_titulo_id": None,
            "sinal_titulo_id": sinal_baixado}
