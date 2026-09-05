"""Termo aditivo ao contrato de locação — a emenda que o papel do dono já era.

O QUE ESTE MÓDULO EXISTE PARA DESTRAVAR

O sistema manda fazer aditivo em três lugares — `web/painel_servicos` (409 ao
salvar orçamento com contrato assinado), `finance/cockpit` (mesma trava pelo
WhatsApp) e a cláusula 9.1 do próprio modelo de contrato ("o Orçamento nº X e
seus aditivos integram este contrato"). Nenhum deles tinha para onde mandar: até
aqui o aditivo não existia, e a saída real da Prime era refazer no Word.

O MODELO VEIO DO DONO, COM QUATRO CASOS REAIS JUNTO

Lidos em 04/09/2026: um modelo em branco e quatro aditivos já assinados
(Gardênia, Grazielly, Bárbara, Ana Aline). Os reais ensinaram três coisas que o
modelo em branco não ensina:

 1. NUNCA saem todas as cláusulas. Saiu 1, 2, 2 e 2 — só o que mudou, renumerado
    de 1 em diante. Por isso `clausulas()` numera o que sobrou, e não existe
    cláusula fixa com "não se aplica".
 2. O TÍTULO muda com o sentido. "ACRÉSCIMO DE CONVIDADOS" quando sobe (os dois
    casos reais eram aumento), "REDUÇÃO" quando desce; "ALTERAÇÃO NO HORÁRIO" —
    e não "na data e/ou horário" — quando a data ficou igual. Documento que
    anuncia mudança de data sem mudar data confunde quem lê depois.
 3. O VALOR NÃO SE CALCULA. Bárbara foi de 50 para 73 convidados por R$ 5.576,43
    (R$ 242,45/pessoa) enquanto o total dela dá R$ 185/pessoa — houve
    renegociação, não regra de três. E o contrato vivo da Prime é locação pura,
    onde convidado a mais pode não mudar valor nenhum. Então a diferença é
    DIGITADA; o módulo só soma e mostra o total resultante.

DATA NÃO É HORÁRIO, E É A CLÁUSULA 7 QUE DIZ ISSO

O modelo em papel junta as duas num "ALTERAÇÃO NA DATA E/OU HORÁRIO". O contrato
do dono não junta: a cláusula 7 trata alteração de DATA e impõe quatro coisas —
uma alteração por contrato (7.1), 30 dias de antecedência (7.1), taxa sobre o
valor atualizado (7.2, com disponibilidade), e nova data em até 180 dias (7.3).
Nenhuma vale pra horário. `conferir_data` roda as quatro.

E as quatro AVISAM, não travam. É a regra que o dono deu em contas a pagar
("só avisa não trava") aplicada aqui pelo mesmo motivo: quem manda no contrato é
ele, não a tela. Um aditivo com 20 dias de antecedência é decisão dele com a
regra na frente dos olhos — barrar só empurraria de volta pro Word, que é
exatamente o que este módulo veio tirar do caminho.

A ASSINATURA GRAVA DE VOLTA

Decisão do dono em 05/09/2026, perguntado explicitamente: assinou, o sistema
muda os dados. Sem isso o aditivo seria um gerador de PDF — o documento diria
140 convidados às 19h e o sistema continuaria com 115 às 18h, a equipe montaria
no horário velho e a diferença combinada não viraria cobrança. `assinar` escreve
no orçamento, na agenda e cria o título a receber, tudo depois de congelar o
texto.

Só na assinatura, nunca antes: enquanto o cliente não assinou, o aditivo é
proposta, e se ele nunca assinar não há nada pra desfazer.
"""
from __future__ import annotations

import json
import logging
import secrets
from datetime import date, datetime, timedelta

_log = logging.getLogger("finance.aditivo")

STATUS = ("enviado", "assinado", "cancelado")

_COLS = ("id, conta_id, contrato_id, ordem, status, alteracoes, texto, "
         "valor_antes_centavos, valor_novo_centavos, diferenca_centavos, "
         "taxa_centavos, vencimento, forma_pagamento, titulo_id, token, "
         "assinado_em, assinado_por, assinado_doc, assinado_ip, cancelado_em, "
         "criado_em, criado_por")


# ------------------------------------------------------------------ por extenso
# Documento jurídico escreve o número duas vezes: em algarismo e por extenso. Os
# quatro aditivos reais fazem isso em todo valor e em toda contagem de convidados
# ("73 (setenta e três) convidados"), então não dá pra pular.

_U = ("zero", "um", "dois", "três", "quatro", "cinco", "seis", "sete", "oito",
      "nove", "dez", "onze", "doze", "treze", "quatorze", "quinze", "dezesseis",
      "dezessete", "dezoito", "dezenove")
_D = ("", "", "vinte", "trinta", "quarenta", "cinquenta", "sessenta", "setenta",
      "oitenta", "noventa")
_C = ("", "cento", "duzentos", "trezentos", "quatrocentos", "quinhentos",
      "seiscentos", "setecentos", "oitocentos", "novecentos")


def _ate_999(n: int) -> str:
    if n == 0:
        return "zero"
    if n == 100:
        return "cem"          # 'cem' exato; 101 já é 'cento e um'
    partes = []
    c, r = divmod(n, 100)
    if c:
        partes.append(_C[c])
    if r:
        if r < 20:
            partes.append(_U[r])
        else:
            d, u = divmod(r, 10)
            partes.append(_D[d] + (f" e {_U[u]}" if u else ""))
    return " e ".join(partes)


def extenso(n: int) -> str:
    """140 -> 'cento e quarenta'. Inteiro não negativo.

    A junção entre os grupos segue a regra usual: vírgula, virando 'e' quando o
    último grupo é menor que cem ou é centena redonda — 'mil e cinco',
    'mil e quinhentos', mas 'treze mil, quinhentos e cinco'. Conferido contra os
    quatro valores dos aditivos reais do dono."""
    n = int(n)
    if n < 0:
        return "menos " + extenso(-n)
    if n < 1000:
        return _ate_999(n)
    grupos = []          # (valor, sufixo) do mais significativo pro menos
    for div, sing, plur in ((1_000_000_000, "bilhão", "bilhões"),
                            (1_000_000, "milhão", "milhões"),
                            (1000, "mil", "mil")):
        q, n = divmod(n, div)
        if q:
            # 'mil' não leva 'um' na frente: 1.850 é 'mil oitocentos…'
            corpo = "" if (div == 1000 and q == 1) else _ate_999(q) + " "
            grupos.append(corpo + (sing if q == 1 else plur))
    if n:
        grupos.append(_ate_999(n))
        # o 'e' final quando o último grupo é pequeno ou redondo
        if n < 100 or n % 100 == 0:
            return ", ".join(grupos[:-1]) + " e " + grupos[-1]
    return ", ".join(grupos)


def reais_extenso(centavos: int) -> str:
    """125000 -> 'mil duzentos e cinquenta reais'."""
    centavos = int(centavos or 0)
    r, c = divmod(abs(centavos), 100)
    partes = []
    if r or not c:
        partes.append(f"{extenso(r)} {'real' if r == 1 else 'reais'}")
    if c:
        partes.append(f"{extenso(c)} {'centavo' if c == 1 else 'centavos'}")
    s = " e ".join(partes)
    return ("menos " + s) if centavos < 0 else s


def valor_por_extenso(centavos: int) -> str:
    """'R$ 9.900,00 (nove mil e novecentos reais)' — como o documento escreve."""
    from finance.contrato import reais
    return f"{reais(centavos)} ({reais_extenso(centavos)})"


def qtd_por_extenso(n) -> str:
    """'140 (cento e quarenta)' — como o papel escreve convidado."""
    try:
        i = int(n)
    except (TypeError, ValueError):
        return str(n or "")
    return f"{i} ({extenso(i)})"


# ------------------------------------------------------------------- utilidades

def _fmt(r) -> dict:
    return {"id": r[0], "conta_id": r[1], "contrato_id": r[2], "ordem": r[3],
            "status": r[4], "alteracoes": r[5] or [], "texto": r[6],
            "valor_antes_centavos": r[7], "valor_novo_centavos": r[8],
            "diferenca_centavos": r[9] or 0, "taxa_centavos": r[10] or 0,
            "vencimento": r[11], "forma_pagamento": r[12] or "",
            "titulo_id": r[13], "token": r[14], "assinado_em": r[15],
            "assinado_por": r[16] or "", "assinado_doc": r[17] or "",
            "assinado_ip": r[18] or "", "cancelado_em": r[19],
            "criado_em": r[20], "criado_por": r[21] or ""}


def ordinal(n: int) -> str:
    """1 -> '1º'. O papel se refere a si mesmo assim: '2º termo aditivo'."""
    return f"{int(n)}º"


def _hora_limpa(v) -> str:
    """'18:00' a partir do que o orçamento tiver.

    Medido na produção em 04/09/2026, o campo `evento.inicio`/`fim` guarda coisas
    como '20h30', '01h', '22', '17', '18:00' e '23:40' — texto livre. Documento
    que se assina não pode imprimir 'término às 22', então o que dá pra
    normalizar é normalizado, e o que não dá volta como veio (é melhor sair o
    texto do dono do que sair vazio)."""
    s = str(v or "").strip().lower().replace("h", ":")
    if not s:
        return ""
    s = s.rstrip(":")
    if ":" in s:
        hh, _, mm = s.partition(":")
    else:
        hh, mm = s, "0"
    try:
        h, m = int(hh), int(mm or 0)
    except ValueError:
        return str(v or "").strip()
    if not (0 <= h <= 24 and 0 <= m <= 59):
        return str(v or "").strip()
    # "encerramento 24" é como orçamento de festa escreve meia-noite, e
    # `agenda._minutos` já aceita isso (1440). Aqui vira 00:00 porque quem
    # imprime a frase é o documento: "término às 00:00h do dia 23" se lê, "às
    # 24:00h" trava o leitor. O dia certo sai de `janela_evento`, que rola o fim
    # pro dia seguinte sozinho — o instante é o mesmo.
    if h == 24:
        h, m = 0, 0
    return f"{h:02d}:{m:02d}"


# --------------------------------------------------------------- o estado ANTES

def estado_atual(pool, conta_id: int, contrato_id: int) -> dict | None:
    """O que vale HOJE naquele contrato — o 'de' de todo de→para.

    Lê o orçamento por baixo do contrato, e não o texto congelado: o congelado é
    o que o cliente assinou, mas aditivo anterior já pode ter mudado o mundo. O
    'de' correto é sempre o estado corrente."""
    with pool.connection() as c:
        r = c.execute(
            """select ct.id, ct.numero, ct.criado_em, ct.assinado_em,
                      ct.orcamento_id, ct.valor_centavos,
                      o.numero, o.cliente, o.evento, o.itens, o.cliente_id,
                      o.evento_agenda_id,
                      coalesce(o.primeiro_ano_centavos, o.setup_centavos, 0)
                 from contratos ct
                 left join orcamentos o
                        on o.id = ct.orcamento_id and o.conta_id = ct.conta_id
                where ct.id=%s and ct.conta_id=%s""",
            (int(contrato_id), conta_id)).fetchone()
    if not r:
        return None
    ev = r[8] or {}
    return {
        "contrato_id": r[0], "contrato_numero": r[1], "contrato_em": r[2],
        "contrato_assinado_em": r[3], "orcamento_id": r[4],
        "contrato_valor_centavos": r[5],
        "orcamento_numero": r[6], "cliente": r[7] or "",
        "evento": ev, "itens": r[9] or [], "cliente_id": r[10],
        "evento_agenda_id": r[11],
        "valor_centavos": int(r[12] or 0),
        "data": (ev.get("data") or ""),
        "inicio": _hora_limpa(ev.get("inicio")),
        "fim": _hora_limpa(ev.get("fim")),
        "convidados": ev.get("convidados"),
        "tipo": ev.get("tipo") or "",
    }


# ------------------------------------------------------- as regras da cláusula 7

def conferir_data(pool, conta_id: int, contrato_id: int, nova_data,
                  regras: dict | None = None) -> list[dict]:
    """As quatro conferências da cláusula 7, cada uma como um aviso.

    Devolve lista de {"regra", "ok", "texto"}. NUNCA levanta e NUNCA impede: quem
    chama mostra na tela e o dono decide. Ver o docstring do módulo.
    """
    from finance.agenda import parse_data, janela_evento
    from finance import contrato as ctr

    est = estado_atual(pool, conta_id, contrato_id) or {}
    regras = regras or {}
    dias_min = int(regras.get("reagenda_dias") or ctr.REGRAS_PADRAO["reagenda_dias"])
    prazo = int(regras.get("reagenda_prazo") or ctr.REGRAS_PADRAO["reagenda_prazo"])
    nova = parse_data(nova_data)
    velha = parse_data(est.get("data"))
    avisos: list[dict] = []

    # 7.1 — uma alteração de data por contrato
    ja = quantas_mudaram_data(pool, conta_id, contrato_id)
    avisos.append({
        "regra": "7.1",
        "ok": ja == 0,
        "texto": ("primeira alteração de data deste contrato (a cláusula 7.1 prevê 1)"
                  if ja == 0 else
                  f"este contrato já teve {ja} alteração(ões) de data — a cláusula 7.1 prevê 1"),
    })

    # 7.1 — antecedência mínima, contada da data ORIGINALMENTE contratada
    if velha:
        faltam = (velha - date.today()).days
        avisos.append({
            "regra": "7.1",
            "ok": faltam >= dias_min,
            "texto": (f"{faltam} dias de antecedência — o mínimo da cláusula é {dias_min}"
                      if faltam >= dias_min else
                      f"faltam só {faltam} dias para a data contratada — a cláusula 7.1 "
                      f"pede {dias_min} de antecedência"),
        })

    # 7.2 — disponibilidade da nova data
    if nova:
        ini, fim = janela_evento(nova, est.get("inicio"), est.get("fim"))
        chocam = [e for e in _conflitos(pool, conta_id, ini, fim,
                                        est.get("evento_agenda_id"))]
        avisos.append({
            "regra": "7.2",
            "ok": not chocam,
            "texto": (f"{nova.strftime('%d/%m/%Y')} está livre na agenda"
                      if not chocam else
                      "já tem compromisso nesse dia: " +
                      ", ".join((e.get("titulo") or "sem título") for e in chocam[:3])),
        })

    # 7.3 — a nova data dentro do prazo contado da original
    if nova and velha:
        dist = (nova - velha).days
        avisos.append({
            "regra": "7.3",
            "ok": 0 <= dist <= prazo,
            "texto": (f"nova data {dist} dias após a original — o limite é {prazo}"
                      if 0 <= dist <= prazo else
                      f"nova data {abs(dist)} dias {'antes' if dist < 0 else 'depois'} "
                      f"da original — a cláusula 7.3 prevê até {prazo} dias depois"),
        })
    return avisos


def _conflitos(pool, conta_id, ini, fim, ignorar_id):
    """Isola a leitura da agenda: base sem a tabela não pode derrubar a tela."""
    try:
        from finance.agenda import conflitos
        return conflitos(pool, conta_id, ini, fim, ignorar_id)
    except Exception as e:  # noqa: BLE001
        _log.warning("conferir_data: agenda indisponível (conta=%s): %s: %s",
                     conta_id, type(e).__name__, e)
        return []


def quantas_mudaram_data(pool, conta_id: int, contrato_id: int) -> int:
    """Quantos aditivos ASSINADOS já mudaram a data deste contrato (cláusula 7.1).

    Só assinados contam: um aditivo em aberto ainda não alterou nada, e cancelado
    nunca alterou."""
    try:
        with pool.connection() as c:
            r = c.execute(
                """select count(*) from contrato_aditivos
                    where conta_id=%s and contrato_id=%s and status='assinado'
                      and alteracoes @> '[{"campo":"data"}]'::jsonb""",
                (conta_id, int(contrato_id))).fetchone()
        return int(r[0] or 0)
    except Exception:  # noqa: BLE001 — base sem a 196 ainda
        return 0


def taxa_reagendamento(valor_atualizado_centavos: int, regras: dict | None = None) -> int:
    """A taxa da cláusula 7.2, em centavos, sobre o valor ATUALIZADO do contrato.

    "Atualizado" é a palavra da cláusula: se o mesmo aditivo também acrescenta
    convidados, a taxa incide sobre o total já com o acréscimo. Nasce calculada e
    o dono zera se quiser — decisão dele em 05/09/2026, pra que a taxa nunca suma
    na pressa nem seja cobrada sem ele ver."""
    from finance import contrato as ctr
    pct = float((regras or {}).get("taxa_reagendamento")
                or ctr.REGRAS_PADRAO["taxa_reagendamento"])
    return int(round(int(valor_atualizado_centavos or 0) * pct / 100.0))


# ------------------------------------------------------------------ persistência

def do_contrato(pool, conta_id: int, contrato_id: int) -> list[dict]:
    """Todos os aditivos daquele contrato, do mais novo pro mais velho."""
    try:
        with pool.connection() as c:
            rows = c.execute(
                "select " + _COLS + " from contrato_aditivos "
                " where conta_id=%s and contrato_id=%s order by ordem desc",
                (conta_id, int(contrato_id))).fetchall()
    except Exception:  # noqa: BLE001 — base sem a 196: a tela abre sem aditivo
        return []
    return [_fmt(r) for r in rows]


def aberto_do_contrato(pool, conta_id: int, contrato_id: int) -> dict | None:
    """O aditivo esperando assinatura, se houver. É o que impede criar outro."""
    for a in do_contrato(pool, conta_id, contrato_id):
        if a["status"] == "enviado":
            return a
    return None


def assinados_do_contrato(pool, conta_id: int, contrato_id: int) -> list[dict]:
    return [a for a in do_contrato(pool, conta_id, contrato_id)
            if a["status"] == "assinado"]


def por_id(pool, conta_id: int, aditivo_id: int) -> dict | None:
    with pool.connection() as c:
        r = c.execute("select " + _COLS + " from contrato_aditivos "
                      " where id=%s and conta_id=%s",
                      (int(aditivo_id), conta_id)).fetchone()
    return _fmt(r) if r else None


def por_token(pool, token: str) -> dict | None:
    """Pelo link público. Sem conta_id, mesmo desenho do contrato e da proposta."""
    if not (token or "").strip():
        return None
    with pool.connection() as c:
        r = c.execute("select " + _COLS + " from contrato_aditivos where token=%s",
                      (token.strip(),)).fetchone()
    return _fmt(r) if r else None


def criar(pool, conta_id: int, contrato_id: int, alteracoes: list[dict], *,
          diferenca_centavos: int = 0, taxa_centavos: int = 0,
          vencimento=None, forma_pagamento: str = "",
          criado_por: str = "") -> dict:
    """Cria o aditivo em aberto e devolve ele. NÃO altera nada do evento — isso é
    da assinatura.

    Levanta ValueError com mensagem de gente em: contrato inexistente, contrato
    não assinado (não se emenda o que não foi firmado), nenhuma alteração
    marcada, ou já existir um aditivo esperando assinatura."""
    est = estado_atual(pool, conta_id, contrato_id)
    if not est:
        raise ValueError("contrato não encontrado")
    if not est.get("contrato_assinado_em"):
        raise ValueError("este contrato ainda não foi assinado — não há o que aditar")
    alteracoes = [a for a in (alteracoes or []) if a and a.get("campo")]
    dif = int(diferenca_centavos or 0)
    taxa = int(taxa_centavos or 0)
    if not alteracoes and not dif and not taxa:
        raise ValueError("marque ao menos uma alteração")
    ja = aberto_do_contrato(pool, conta_id, contrato_id)
    if ja:
        raise ValueError(
            f"o {ordinal(ja['ordem'])} aditivo deste contrato está esperando "
            "assinatura — cancele ele antes de fazer outro")

    antes = int(est["valor_centavos"] or 0)
    novo = antes + dif + taxa
    with pool.connection() as c:
        r = c.execute(
            """insert into contrato_aditivos
                 (conta_id, contrato_id, ordem, status, alteracoes,
                  valor_antes_centavos, valor_novo_centavos, diferenca_centavos,
                  taxa_centavos, vencimento, forma_pagamento, token, criado_por)
               values (%s, %s,
                       (select coalesce(max(ordem),0)+1 from contrato_aditivos
                         where contrato_id=%s),
                       'enviado', %s::jsonb, %s, %s, %s, %s, %s, %s, %s, %s)
               returning """ + _COLS,
            (conta_id, int(contrato_id), int(contrato_id),
             json.dumps(alteracoes), antes, novo, dif, taxa,
             vencimento or None, (forma_pagamento or "")[:80],
             secrets.token_urlsafe(16), (criado_por or "")[:120])).fetchone()
        c.commit()
    return _fmt(r)


def cancelar(pool, conta_id: int, aditivo_id: int) -> bool:
    """Cancela um aditivo que ninguém assinou.

    Existe porque uma hora sai um valor errado e o link já foi pro cliente.
    Assinado NÃO cancela — aí é outro aditivo, mesma regra do contrato."""
    with pool.connection() as c:
        cur = c.execute(
            "update contrato_aditivos set status='cancelado', cancelado_em=now(), "
            "       token=null "
            " where id=%s and conta_id=%s and status='enviado'",
            (int(aditivo_id), conta_id))
        c.commit()
        return cur.rowcount > 0


# -------------------------------------------------------------------- o TEXTO

def _de_para(alteracoes, campo) -> dict | None:
    for a in alteracoes or []:
        if a.get("campo") == campo:
            return a
    return None


def clausulas(aditivo: dict, est: dict | None = None) -> list[dict]:
    """As cláusulas do documento, numeradas de 1 em diante — só as que existem.

    A ordem é a que o dono deu como habitual: data, horário, convidados,
    serviços, valor. O título de cada uma segue os quatro aditivos reais, que
    escrevem o sentido da mudança em vez de um rótulo genérico."""
    from finance.contrato import data_br
    alt = aditivo.get("alteracoes") or []
    out: list[dict] = []

    d = _de_para(alt, "data")
    if d:
        out.append({
            "titulo": "ALTERAÇÃO NA DATA",
            "corpo": (f"A data de realização do evento será alterada para "
                      f"{data_br(d.get('para'))}, em substituição à data originalmente "
                      f"contratada de {data_br(d.get('de'))}, nos termos da Cláusula 7 "
                      f"do contrato original."),
        })

    h = _de_para(alt, "horario")
    if h:
        para = h.get("para") or {}
        dia = (d or {}).get("para") or (est or {}).get("data") or ""
        dia_ini = data_br(dia)
        # VIRAR A NOITE É A REGRA DO RAMO, não a exceção: dos dois aditivos reais
        # que mexeram em horário, os dois terminavam no dia seguinte (26→27/09 e
        # 30→31/05). Quem sabe disso é `janela_evento`, que já rola o fim pro dia
        # seguinte quando ele é menor que o início — então o documento pergunta a
        # ela em vez de ter a sua própria regra, que amanhã divergiria.
        dia_fim = dia_ini
        try:
            from finance.agenda import janela_evento
            _i, _f = janela_evento(dia, para.get("inicio"), para.get("fim"))
            if _f is not None:
                dia_fim = _f.strftime("%d/%m/%Y")
        except Exception:  # noqa: BLE001 — sem data legível, os dois dias são o mesmo
            pass
        out.append({
            "titulo": "ALTERAÇÃO NO HORÁRIO",
            "corpo": (f"O horário de início será alterado para às "
                      f"{para.get('inicio') or '—'}h do dia {dia_ini} e término às "
                      f"{para.get('fim') or '—'}h do dia {dia_fim}."),
        })

    cv = _de_para(alt, "convidados")
    if cv:
        de, para = cv.get("de"), cv.get("para")
        try:
            subiu = int(para) > int(de)
            titulo = "ACRÉSCIMO DE CONVIDADOS" if subiu else "REDUÇÃO DE CONVIDADOS"
        except (TypeError, ValueError):
            titulo = "ALTERAÇÃO NO NÚMERO DE CONVIDADOS"
        out.append({
            "titulo": titulo,
            "corpo": (f"O número de convidados para prestação de serviços passa a ser "
                      f"{qtd_por_extenso(para)} convidados, em substituição à quantidade "
                      f"originalmente estabelecida de {qtd_por_extenso(de)} convidados."),
        })

    sv = _de_para(alt, "servicos")
    if sv:
        saem = ", ".join(str(x) for x in (sv.get("saem") or []) if str(x).strip())
        num = (est or {}).get("orcamento_numero")
        onde = f", descritos no orçamento nº {num}," if num else ""
        out.append({
            "titulo": "ALTERAÇÃO NOS SERVIÇOS",
            "corpo": (f"Os itens: {saem or '—'}{onde} serão substituídos por "
                      f"{(sv.get('entram') or '—').strip()}."),
        })

    dif = int(aditivo.get("diferenca_centavos") or 0)
    taxa = int(aditivo.get("taxa_centavos") or 0)
    if dif or taxa:
        total = int(aditivo.get("valor_novo_centavos") or 0)
        a_pagar = dif + taxa
        # as duas parcelas discriminadas só quando existem as duas — com uma só,
        # repetir o mesmo número entre parênteses é ruído no documento.
        detalhe = ""
        if dif and taxa:
            detalhe = (f" — sendo {valor_por_extenso(dif)} referentes à alteração "
                       f"contratada e {valor_por_extenso(taxa)} referentes à taxa de "
                       f"reagendamento prevista na Cláusula 7.2 —")
        cond = []
        if aditivo.get("forma_pagamento"):
            cond.append(f"Pagamento via {aditivo['forma_pagamento']}")
        if aditivo.get("vencimento"):
            cond.append(f"com vencimento em {data_br(aditivo['vencimento'])}")
        linha = (", ".join(cond) + "." if cond else
                 "Condições de pagamento a combinar entre as partes.")
        out.append({
            "titulo": "AJUSTE NO VALOR",
            "corpo": (f"O valor total do contrato será alterado para "
                      f"{valor_por_extenso(total)}. Considerando as modificações "
                      f"realizadas, o valor de {valor_por_extenso(a_pagar)}{detalhe} "
                      f"será pago nas condições abaixo descritas:\n  ○ {linha}"),
        })

    for i, c in enumerate(out, 1):
        c["titulo"] = f"{i}. {c['titulo']}"
    return out


DISPOSICOES = (
    "1. Permanecem inalteradas todas as demais cláusulas e condições estabelecidas "
    "no contrato original, que não tenham sido expressamente modificadas por este "
    "termo aditivo.\n"
    "2. Este termo aditivo entra em vigor na data de sua assinatura pelas partes e "
    "passa a integrar o contrato original para todos os fins de direito."
)

# O papel diz "02 (duas) vias de igual teor" e "na presença de 02 (duas)
# testemunhas". No link não existe via nem testemunha, e dos quatro aditivos reais
# do dono TRÊS foram assinados sem testemunha nenhuma. Manter a frase seria o
# documento afirmar o que não aconteceu; o dono escolheu, em 05/09/2026, trocar
# por uma que descreve o que de fato acontece.
FECHO = (
    "Por estarem justas e acordadas, as partes assinam eletronicamente este termo "
    "aditivo, ficando registrados o nome, o documento, a data, a hora e o endereço "
    "de IP de quem assina, para que produza seus efeitos legais."
)


# ------------------------------------------------------------------- assinatura

def assinar(pool, aditivo_id: int, nome: str, doc: str, ip: str,
            clausulas_lidas=None) -> bool:
    """O cliente aceitou. Congela o texto, e SÓ ENTÃO grava de volta.

    `assinado_em is null` na condição: duplo clique ou reenvio não sobrescreve a
    assinatura nem o texto congelado, e não gera dois títulos a receber.

    A gravação de volta é tolerante, item por item, e vem DEPOIS do commit da
    assinatura — a assinatura é o que não pode se perder. Se a agenda estiver
    fora do ar, o aditivo fica assinado e o que falhou vai pro log com nome e
    sobrenome, em vez de a página do cliente estourar depois de ele já ter
    aceitado."""
    with pool.connection() as c:
        cur = c.execute(
            """update contrato_aditivos
                  set status='assinado', assinado_em=now(), texto=%s::jsonb,
                      assinado_por=%s, assinado_doc=%s, assinado_ip=%s
                where id=%s and status='enviado' and assinado_em is null""",
            (json.dumps(clausulas_lidas or []), (nome or "").strip()[:120],
             (doc or "").strip()[:40] or None, (ip or "")[:60], int(aditivo_id)))
        c.commit()
        if not cur.rowcount:
            return False
    ad = None
    with pool.connection() as c:
        r = c.execute("select " + _COLS + " from contrato_aditivos where id=%s",
                      (int(aditivo_id),)).fetchone()
        ad = _fmt(r) if r else None
    if ad:
        try:
            aplicar(pool, ad)
        except Exception as e:  # noqa: BLE001
            _log.error("aditivo %s assinado, mas a gravação de volta falhou: %s: %s",
                       aditivo_id, type(e).__name__, e)
    return True


def aplicar(pool, aditivo: dict) -> dict:
    """Escreve o que o aditivo decidiu: orçamento, agenda e título a receber.

    Separada de `assinar` de propósito — assim dá pra reaplicar à mão o que
    falhou, sem mexer na assinatura. Devolve o que foi feito, item a item."""
    conta_id = aditivo["conta_id"]
    est = estado_atual(pool, conta_id, aditivo["contrato_id"]) or {}
    alt = aditivo.get("alteracoes") or []
    feito = {"orcamento": False, "agenda": False, "titulo_id": None, "erros": []}

    # ---- 1) orçamento: evento (data, horário, convidados) e itens ----
    ev = dict(est.get("evento") or {})
    mexeu = False
    d = _de_para(alt, "data")
    if d and d.get("para"):
        ev["data"] = str(d["para"])
        mexeu = True
    h = _de_para(alt, "horario")
    if h and (h.get("para") or {}):
        para = h["para"]
        if para.get("inicio"):
            ev["inicio"] = para["inicio"]
        if para.get("fim"):
            ev["fim"] = para["fim"]
        mexeu = True
    cv = _de_para(alt, "convidados")
    if cv and cv.get("para") is not None:
        try:
            ev["convidados"] = int(cv["para"])
        except (TypeError, ValueError):
            ev["convidados"] = cv["para"]
        mexeu = True

    novo_valor = int(aditivo.get("valor_novo_centavos") or 0)
    if est.get("orcamento_id") and (mexeu or novo_valor):
        try:
            with pool.connection() as c:
                c.execute(
                    """update orcamentos
                          set evento = %s::jsonb,
                              primeiro_ano_centavos = case when %s > 0
                                   then %s else primeiro_ano_centavos end,
                              atualizado_em = now()
                        where id=%s and conta_id=%s""",
                    (json.dumps(ev), novo_valor, novo_valor,
                     int(est["orcamento_id"]), conta_id))
                c.commit()
            feito["orcamento"] = True
        except Exception as e:  # noqa: BLE001
            feito["erros"].append(f"orçamento: {type(e).__name__}: {e}")
            _log.error("aditivo %s: orçamento não atualizou: %s", aditivo["id"], e)

    # ---- 2) agenda: a data/hora que a equipe olha no dia ----
    if (d or h) and est.get("evento_agenda_id"):
        try:
            from finance.agenda import parse_data, janela_evento, remarcar_evento
            dia = parse_data(ev.get("data"))
            if dia:
                ini, fim = janela_evento(dia, ev.get("inicio"), ev.get("fim"))
                remarcar_evento(pool, conta_id, int(est["evento_agenda_id"]), ini, fim)
                feito["agenda"] = True
        except Exception as e:  # noqa: BLE001
            feito["erros"].append(f"agenda: {type(e).__name__}: {e}")
            _log.error("aditivo %s: agenda não remarcou: %s", aditivo["id"], e)

    # ---- 3) o dinheiro combinado vira cobrança ----
    a_pagar = int(aditivo.get("diferenca_centavos") or 0) + \
        int(aditivo.get("taxa_centavos") or 0)
    if a_pagar > 0 and not aditivo.get("titulo_id"):
        try:
            from finance import empresa as emp
            venc = aditivo.get("vencimento") or (date.today() + timedelta(days=30))
            if isinstance(venc, datetime):
                venc = venc.date()
            desc = (f"{ordinal(aditivo['ordem'])} aditivo ao contrato "
                    f"nº {est.get('contrato_numero')}")
            t = emp.criar_titulo(
                pool, conta_id, "receber", desc, a_pagar, venc,
                contraparte=est.get("cliente") or "",
                cliente_id=est.get("cliente_id"))
            feito["titulo_id"] = t["id"]
            with pool.connection() as c:
                c.execute("update contrato_aditivos set titulo_id=%s where id=%s",
                          (t["id"], aditivo["id"]))
                c.commit()
        except Exception as e:  # noqa: BLE001
            feito["erros"].append(f"título: {type(e).__name__}: {e}")
            _log.error("aditivo %s: título a receber não nasceu: %s", aditivo["id"], e)
    return feito


# ------------------------------------------------------- o que as telas mostram

def aviso_no_contrato(pool, conta_id: int, contrato_id: int) -> dict | None:
    """A tarja do contrato original: 'alterado pelo Nº aditivo em DD/MM/AAAA'.

    O contrato assinado NÃO é reescrito — o congelamento é o que dá valor a ele.
    Mas a página dele termina dizendo "guarde este link: ele é o seu contrato", e
    sem esta tarja o cliente releria 115 convidados e 15/01 depois de ter
    assinado a mudança. Documento certo dizendo o errado é pior que documento
    faltando."""
    assinados = assinados_do_contrato(pool, conta_id, contrato_id)
    if not assinados:
        return None
    ult = assinados[0]          # do_contrato já vem do maior ordem pro menor
    return {"ordem": ult["ordem"], "rotulo": ordinal(ult["ordem"]),
            "assinado_em": ult["assinado_em"], "token": ult["token"],
            "quantos": len(assinados),
            "valor_centavos": ult["valor_novo_centavos"]}


def valor_vigente(pool, conta_id: int, contrato_id: int, padrao=None):
    """O valor que vale hoje: o do último aditivo assinado, ou o do contrato.

    É o que as telas de lista devem mostrar — contrato com aditivo assinado
    aparecendo com o valor velho é o mesmo erro da tarja, só que na tela do
    dono."""
    for a in assinados_do_contrato(pool, conta_id, contrato_id):
        if a["valor_novo_centavos"] is not None:
            return a["valor_novo_centavos"]
    return padrao
