"""Voltar a chamar depois do preço — a clínica que passa o preço e nunca mais fala.

O QUE FOI MEDIDO. Espaço Pelle (conta 39, nicho clínica), 24/08 a 23/09/2026, só
leitura na produção: 11 pacientes escreveram, receberam o preço da consulta
(R$ 500) e 1 marcou. Quem não marcou não recebeu mais nada — a recepção atende
rápido (mediana de 2 minutos), mas depois do preço a conversa morre calada.

O FATO. Uma mensagem NOSSA (`out`, e nunca do agente) que casa `RE_PRECO` e o
contexto do perfil (consulta/avaliação) NA MESMA MENSAGEM, numa conversa em que o
paciente escreveu ANTES. As duas travas são o que separa paciente de fornecedor:
"segue o boleto de R$ 1.200" não fala de consulta, e a conversa que a clínica
abriu com o contador não tem mensagem do contador antes do preço.
A primeira medição usava "a primeira mensagem da conversa é do paciente" e dava
13 → 3; o histórico do WhatsApp vem truncado, e a regra certa é "o paciente
escreveu antes do preço" (11 → 1).

A SEQUÊNCIA É DA CONVERSA, NÃO DO CARD. A conta 39 conectou o WhatsApp em
23/09/2026 e trouxe 84 conversas de histórico; só 15 ganharam card, e 8 dos 11
pacientes que receberam o preço estão entre as que ficaram sem. Exigir card
deixaria de fora justamente a repescagem — o dinheiro medido. Quando o card existe
(toda conversa nova ganha um), ele é lido de `conversas.prospeccao_id` na hora:
card em Consulta agendada encerra a sequência, "Já marcou" move o card. Sem card,
o toque sai igual e "Já marcou" só encerra. Este módulo não cria card.

OS TOQUES. +3h, +1 dia, +3 dias, +7 dias depois do preço (`raio_x_perfil`). O de
3h só sai no mesmo dia e com a clínica aberta; os outros caem na próxima abertura
da janela da régua (`funil_regua.config`). Nunca dois no mesmo dia. Quem ficou
parado ANTES da conta ligar recebe um toque só — a repescagem (toque 0) — e ele
sempre espera a recepção apertar Mandar, mesmo com o modo 'ligado'.

RESPONDER NÃO ENCERRA — A CONVERSA PARADA É QUE CHAMA. Medido na conta 39: dos 11
que receberam o preço, 10 responderam alguma coisa depois ("obrigada", "vou ver")
e a conversa morreu ali, sem pergunta pendente. Parar de chamar quem respondeu
deixaria o recurso com 1 paciente em 11. Então o toque vencido só aparece (e só
sai) com a conversa PARADA há `_PARADA_H` horas, e nunca quando a última mensagem
é uma pergunta do paciente sem resposta — essa é da recepção, e está em
"Esperando resposta".

OS TRÊS MODOS (voltar_a_chamar_config.modo):
    off      nasce assim. Nada é registrado e nada sai.
    sugere   a tela Hoje mostra o toque pronto; a recepção aperta Mandar.
    ligado   o Zaq manda sozinho, pelo MESMO chip da conversa, dentro da janela,
             1 por ciclo, até `teto_dia` por conta e 1 por paciente por dia.

O QUE ENCERRA OS TOQUES DE UM PACIENTE:
    marcou       saiu "Prontinho"/"agendamento foi realizado", o card foi pra
                 Consulta agendada ou além, ou a recepção clicou "Já marcou"
    dispensado   a recepção clicou "Não chamar", ou o lead foi dado como perdido
    nao_paciente / saiu   o número entra em `voltar_a_chamar_bloqueios` e nunca
                 mais recebe toque, nem num lead novo
    pulado       alguém da equipe escreveu nas últimas 20h, ou um toque mais novo
                 venceu antes deste ser mandado

SÓ A CLÍNICA. `rodar` confere `modo <> 'off'` E perfil 'clinica' — as duas, de
propósito: uma linha de config numa conta de festa (restaurada de backup, criada
por teste) não pode fazer o Zaq escrever pro cliente de outra conta.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, time, timedelta, timezone

from finance import funil_regua as fr
from finance import raio_x_perfil as rxp

_log = logging.getLogger("openclaw.voltar_a_chamar")

MODOS = ("off", "sugere", "ligado")
_BR = fr._UTC_BR   # Brasília -3 fixo, a mesma convenção do resto do painel

#: vizinho livre dos locks do funil (771147 régua; 771151 perdido e temperatura).
_LOCK = 771160

#: o toque 1 (+3h) só vale no mesmo dia; toque em atraso no registro vira 'pulado',
#: menos o último — ninguém recebe três mensagens de uma vez depois de um deploy.
_JANELA_FATO_DIAS = 8          # +7 dias é o último toque; fato mais velho não entra
_REPESCAGEM_DIAS = 30          # quanto antes de ligar a repescagem alcança
_UMA_SEQUENCIA_DIAS = 30       # uma sequência por conversa neste intervalo
_HUMANO_RECENTE_H = 20         # alguém da equipe escreveu nesse tempo → pula o toque
_RAJADA_MIN = 30               # o que a recepção manda logo depois do preço é o preço
_PARADA_H = 3                  # conversa sem mensagem há isso: dá pra chamar de novo
_REPESCAGEM_PARADA_H = 24      # e pra repescagem, parada há um dia

RE_MARCOU = r"(prontinho|agendamento foi realizado)"
# A MENSAGEM INTEIRA tem que ser o pedido de saída: "Não quero esperar muito, tem
# horário?" e "Sair do trabalho às 18h, tem 19h?" começam igual e querem marcar.
RE_SAIR = r"^\s*(sair|parar|pare|n[ãaÃA]o quero( mais)?)\s*[.!,]*\s*(obrigad[oa])?\s*[.!]*\s*$"
# A RECUSA EM FRASE ("Não, obrigada", "Não tenho interesse", "Não quero mais receber
# mensagens") encerra a SEQUÊNCIA — não bloqueia o número. Mais estreita que a
# `prospec_inbound._RECUSA`: aqui "cancelar" é desmarcar consulta e "não tenho" sozinho
# é "não tenho horário essa semana". Só vale em mensagem SEM '?'. Errar pra este lado
# deixa de chamar alguém; nunca chama a mais.
RE_RECUSA = (r"(\y(n[ãaÃA]o|nem|sem)\s+(quero|queria|tenho interesse|desejo|preciso|interesse)\y"
             r"|\yagora\s+n[ãaÃA]o\y|\ysem\s+interesse\y"
             r"|\y(pare|parar|para\s+de|sair|remov\w*|descadastr\w*)\y"
             r"|^\s*n[ãaÃA]o[\s,.!]*(obrigad[oa])?[\s.!]*$)")

_RE_MARCOU_PY = re.compile(RE_MARCOU, re.I)
_RE_SAIR_PY = re.compile(RE_SAIR, re.I)


def _py(rx: str) -> str:
    """Regex do Postgres → Python. `\\y` é a borda de palavra do Postgres."""
    return rx.replace(r"\y", r"\b")


# ------------------------------------------------------------------ regras puras

def eh_preco_de_consulta(texto: str | None, re_contexto: str) -> bool:
    """A mesma pergunta do SQL, em Python — pros testes e pra quem quiser conferir
    uma frase na mão. Preço E contexto na mesma mensagem."""
    t = texto or ""
    return bool(re.search(_py(fr.RE_PRECO), t, re.I) and re.search(_py(re_contexto), t, re.I))


def marcou(texto: str | None) -> bool:
    return bool(_RE_MARCOU_PY.search(texto or ""))


def recusou(texto: str | None) -> bool:
    t = texto or ""
    return "?" not in t and bool(re.search(_py(RE_RECUSA), t, re.I))


def quer_sair(texto: str | None) -> bool:
    return bool(_RE_SAIR_PY.search(texto or ""))


# Palavra de saúde num texto automático é dado sensível saindo sem ninguém olhar
# (LGPD, art. 11). O toque fala de consulta e de horário, e de mais nada. A lista é
# de dermatologia porque é a clínica que existe; cresce quando entrar outra.
_SAUDE = (
    "acne", "espinha", "laser", "botox", "toxina", "preenchimento", "mancha",
    "melasma", "verruga", "cancer", "tumor", "lesao", "pinta", "cicatriz",
    "psoriase", "dermatite", "rosacea", "micose", "alopecia", "calvicie",
    "queda de cabelo", "herpes", "alergia", "coceira", "ferida", "biopsia",
    "sintoma", "dor", "remedio", "doenca", "diagnostico",
)
_RE_SAUDE = re.compile(r"\b(" + "|".join(re.escape(p) for p in _SAUDE) + r")\b")


def _sem_acento(txt: str) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", txt or "")
                   if unicodedata.category(ch) != "Mn").lower()


def tem_palavra_de_saude(texto: str | None) -> list[str]:
    """As palavras de saúde que o texto tem (lista vazia = pode sair)."""
    return sorted(set(_RE_SAUDE.findall(_sem_acento(texto or ""))))


def primeiro_nome(nome: str | None) -> str:
    """Só o primeiro nome, e só se parecer nome. "~Maria Clara 🌸" → "Maria";
    "5586999..." → "". Nome errado é pior que nome nenhum."""
    for parte in (nome or "").split():
        p = re.sub(r"^[^\w]+|[^\w]+$", "", parte)
        if len(p) >= 2 and p.isalpha():
            return p[:1].upper() + p[1:].lower()
        return ""
    return ""


def texto_do_toque(modelo: str, nome: str | None) -> str:
    """O texto pronto. Sem nome, "Oi, {nome}!" vira "Oi!"."""
    n = primeiro_nome(nome)
    if n:
        return modelo.replace("{nome}", n)
    return modelo.replace(", {nome}", "").replace(" {nome}", "").replace("{nome}", "")


def _local(quando: datetime) -> datetime:
    return (quando + _BR).replace(tzinfo=None)


def _utc(local: datetime) -> datetime:
    return (local - _BR).replace(tzinfo=timezone.utc)


def _proxima_abertura(quando: datetime, janela: dict) -> datetime | None:
    """O primeiro instante >= `quando` em que a clínica está aberta."""
    dias = fr._dias(janela)
    abre, fecha = janela["janela_abre"], janela["janela_fecha"]
    if not dias or abre >= fecha:
        return None
    loc = _local(quando)
    for i in range(15):
        d = loc.date() + timedelta(days=i)
        if d.isoweekday() not in dias:
            continue
        a, b = datetime.combine(d, abre), datetime.combine(d, fecha)
        if i == 0:
            if loc < a:
                return _utc(a)
            if loc < b:
                return quando
            continue
        return _utc(a)
    return None


def agenda_de_toques(preco_em: datetime, janela: dict, toques_min) -> list[tuple[int, datetime]]:
    """[(toque, devido_em)] a partir do instante do preço (UTC).

    - toque 1 (o de horas): só no MESMO dia e com a clínica aberta; senão some —
      empurrar pro dia seguinte o encavalaria com o de +1 dia.
    - os outros: fora da janela, vão pra próxima abertura.
    - nunca dois no mesmo dia: o de baixo anda pra próxima abertura depois.
    """
    out: list[tuple[int, datetime]] = []
    ult_dia = None
    for i, minutos in enumerate(toques_min, start=1):
        alvo = preco_em + timedelta(minutes=int(minutos))
        if i == 1:
            if not fr.dentro_da_janela(alvo, janela) or _local(alvo).date() != _local(preco_em).date():
                continue
            quando = alvo
        else:
            quando = _proxima_abertura(alvo, janela)
        while quando is not None and ult_dia is not None and _local(quando).date() <= ult_dia:
            quando = _proxima_abertura(_utc(datetime.combine(ult_dia + timedelta(days=1), time(0, 0))),
                                       janela)
        if quando is None:
            continue
        out.append((i, quando))
        ult_dia = _local(quando).date()
    return out


def _toques_min(txt) -> tuple[int, ...] | None:
    try:
        v = tuple(int(p) for p in str(txt or "").split(",") if p.strip())
    except ValueError:
        return None
    return v if len(v) == 4 and all(x > 0 for x in v) else None


# ------------------------------------------------------------------ config

def config(c, conta_id: int, perfil: str | None = None) -> dict | None:
    """A config da conta, ou None se o perfil não tem o recurso.

    Sem linha = 'off'. `textos` junta o padrão do perfil com o que o dono mudou —
    o que ele não mexeu continua acompanhando o padrão.
    """
    perfil = perfil or fr.perfil_da_conta(c, conta_id)
    base = rxp.voltar_padrao(perfil)
    if base is None:
        return None
    cfg = dict(base, perfil=perfil, modo="off", ligado_em=None, textos_da_conta={})
    try:
        with c.transaction():
            r = c.execute("""select modo, toques_min, textos, teto_dia, ligado_em
                               from voltar_a_chamar_config where conta_id=%s""",
                          (conta_id,)).fetchone()
    except Exception:  # noqa: BLE001 — tabela ainda não existe num deploy pela metade
        r = None
    if r:
        cfg["modo"] = r[0] if r[0] in MODOS else "off"
        cfg["toques_min"] = _toques_min(r[1]) or base["toques_min"]
        da_conta = {k: v for k, v in (r[2] or {}).items()
                    if k in base["textos"] and isinstance(v, str) and v.strip()}
        cfg["textos"] = dict(base["textos"], **da_conta)
        cfg["textos_da_conta"] = da_conta
        cfg["teto_dia"] = r[3] if (r[3] or 0) > 0 else base["teto_dia"]
        cfg["ligado_em"] = r[4]
    return cfg


def validar_texto(texto: str) -> str | None:
    """O erro do texto, ou None. Vale pra todo texto que o dono salvar."""
    t = (texto or "").strip()
    if not t:
        return "O texto não pode ficar vazio."
    if len(t) > 500:
        return "O texto passou de 500 letras."
    saude = tem_palavra_de_saude(t)
    if saude:
        return ("O toque não pode falar de saúde (" + ", ".join(saude) + "). "
                "Fale só da consulta e do horário.")
    if re.search(_py(fr.RE_PRECO), t, re.I):
        return "O toque não repete preço: quem quiser saber o valor pergunta, e a recepção responde."
    return None


def salvar_config(c, conta_id: int, modo: str, textos: dict | None = None,
                  teto_dia: int | None = None) -> str | None:
    """Grava o modo e os textos. Devolve o erro pra tela, ou None.

    `ligado_em` é a PRIMEIRA vez que a conta saiu do 'off' e não muda mais: é a
    linha que separa repescagem (preço de antes) de toque (preço de depois).
    Desligar e ligar de novo não pode transformar o mês inteiro em repescagem.
    """
    if modo not in MODOS:
        return "Modo inválido."
    base = rxp.voltar_padrao(fr.perfil_da_conta(c, conta_id))
    if base is None:
        return "Este recurso é só da clínica."
    guardar = {}
    for k, v in (textos or {}).items():
        if k not in base["textos"]:
            continue
        v = (v or "").strip()
        if not v or v == base["textos"][k]:
            continue
        erro = validar_texto(v)
        if erro:
            return f"Toque {k}: {erro}"
        guardar[k] = v
    teto = teto_dia if (teto_dia and 0 < teto_dia <= 50) else None
    from psycopg.types.json import Jsonb
    # `textos=None` é "não mexi nos textos" (só o modo mudou): mantém o que está.
    c.execute(
        """insert into voltar_a_chamar_config (conta_id, modo, textos, teto_dia, ligado_em, atualizado_em)
           values (%(c)s, %(m)s, %(t)s, %(teto)s, case when %(m)s <> 'off' then now() end, now())
           on conflict (conta_id) do update
              set modo = excluded.modo,
                  textos = case when %(mexe)s then excluded.textos else voltar_a_chamar_config.textos end,
                  teto_dia = case when %(mexe)s then excluded.teto_dia else voltar_a_chamar_config.teto_dia end,
                  ligado_em = coalesce(voltar_a_chamar_config.ligado_em, excluded.ligado_em),
                  atualizado_em = now()""",
        {"c": conta_id, "m": modo, "t": Jsonb(guardar) if guardar else None, "teto": teto,
         "mexe": textos is not None})
    return None


# ------------------------------------------------------------------ SQL
# ANTES/DEPOIS É PELA DATA, NÃO PELO ID. O histórico que o WhatsApp importou ao
# conectar (conta 39, 23/09/2026) chegou em ondas e fora de ordem: medido em
# 25/09, 1.379 de 2.046 mensagens têm id menor que a anterior na conversa, em 71
# de 104 conversas, com recuo de até 22 dias. Pelo id, um "Prontinho" ou um "Sair"
# de depois do preço podia parecer de antes. O id só desempata o mesmo instante.
def _depois(alias: str, mid: str) -> str:
    """`alias` veio depois da mensagem `mid`, pela data (desempate pelo id)."""
    return (f"({alias}.criado_em, {alias}.id) > "
            f"(select px.criado_em, px.id from mensagens px where px.id = {mid})")


def _n8_da_conversa(conv: str) -> str:
    """O número (8 dígitos) da conversa `conv`, pela mesma regra do `_N8`."""
    return (r"(select right(regexp_replace(coalesce(nullif(px.whatsapp,''), nullif(px.telefone,''),"
            r" cx.contato_ref, ''), '\D', '', 'g'), 8)"
            f" from conversas cx left join prospeccao px on px.id = cx.prospeccao_id"
            f" where cx.id = {conv})")

# O número do paciente, na mesma ordem em que o agente escolhe o destino
# (`agente._atender`): o WhatsApp do lead, o telefone, e o contato da conversa.
_DESTINO = "coalesce(nullif(p.whatsapp,''), nullif(p.telefone,''), cv.contato_ref, '')"
_N8 = r"right(regexp_replace(" + _DESTINO + r", '\D', '', 'g'), 8)"

_FORA_DA_EQUIPE = r"""
   not exists (select 1 from membros mb
                where mb.conta_id = cv.conta_id and coalesce(mb.ativo, true)
                  and (right(regexp_replace(coalesce(mb.whatsapp,''), '\D', '', 'g'), 8) = """ + _N8 + r"""
                    or right(regexp_replace(coalesce(mb.whatsapp_id,''), '\D', '', 'g'), 8) = """ + _N8 + """))"""

def _nao_bloqueado(so_nao_paciente: bool = False) -> str:
    """O bloqueio vale pra TOQUE. Pra "Esperando resposta" e pro placar, só o 'não é
    paciente' (fornecedor, contador) sai: quem pediu pra sair e depois volta a
    escrever continua sendo paciente esperando atendimento, e continua tendo
    recebido o preço."""
    return ("""
   not exists (select 1 from voltar_a_chamar_bloqueios b
                where b.conta_id = cv.conta_id and b.numero8 = """ + _N8
            + (" and b.motivo = 'nao_paciente'" if so_nao_paciente else "") + ")")


_NAO_BLOQUEADO = _nao_bloqueado()

# A mensagem do toque não é fato: nem pela id que ele gravou, nem pelo eco do
# celular (mesmo texto, outra linha). Os textos não falam de preço, e isto é a
# segunda trava.
_NAO_E_TOQUE = """
   not exists (select 1 from voltar_a_chamar_toques x
                where x.conversa_id = m.conversa_id and (x.mensagem_id = m.id or x.texto = m.texto))"""

# Por conversa, a PRIMEIRA mensagem de preço de consulta no intervalo, com o
# paciente tendo escrito antes dela. Base do registro e do placar — a mesma, de
# propósito: gatilho e métrica que lessem "preço" diferente dariam um placar que
# ninguém conseguiria conferir.
_SQL_FATOS_BASE = """
  select pr.conversa_id, cv.prospeccao_id, pr.mid, pr.criado_em
    from (select distinct on (m.conversa_id) m.conversa_id, m.id as mid, m.criado_em
            from mensagens m join conversas cv on cv.id = m.conversa_id
           where cv.conta_id = %(conta)s and cv.canal = 'whatsapp'
             and m.direcao = 'out' and m.autor <> 'bot'
             and m.criado_em >= %(desde)s and m.criado_em < %(ate)s
             and m.texto ~* %(re_preco)s and m.texto ~* %(re_ctx)s
             and exists (select 1 from mensagens f
                          where f.conversa_id = m.conversa_id and f.direcao = 'in'
                            and (f.criado_em, f.id) < (m.criado_em, m.id))
             and """ + _NAO_E_TOQUE + """
           order by m.conversa_id, m.criado_em, m.id) pr
    join conversas cv on cv.id = pr.conversa_id
    left join prospeccao p on p.id = cv.prospeccao_id and p.conta_id = cv.conta_id
   where length(""" + _N8 + """) = 8
     and """ + _FORA_DA_EQUIPE
_SQL_FATOS = _SQL_FATOS_BASE + " and " + _NAO_BLOQUEADO

# "Marcou" pelo que está na conversa ou no card. As chaves `qualificado` e
# `proposta` são "Consulta agendada" e "Plano de tratamento" no funil da clínica.
def _marcou_sql(conv: str, mid: str, lead: str) -> str:
    return ("""(
   exists (select 1 from mensagens mm where mm.conversa_id = """ + conv + """ and mm.direcao = 'out'
            and """ + _depois("mm", mid) + """ and mm.texto ~* %(re_marcou)s)
   or exists (select 1 from prospeccao pp where pp.id = """ + lead + """
               and (pp.status in ('qualificado','proposta') or pp.status in """
            + fr.sql_fechadas("pp") + ")))")


# Card em aberto — ou nenhum card. Conversa sem card é paciente que ninguém pôs no
# funil (o histórico importado ao conectar o WhatsApp), não paciente resolvido.
_EM_ABERTO = ("(p.id is null or (p.status not in ('qualificado','proposta') and "
              + fr.sql_encerradas_nao("p") + "))")


def _params(conta_id: int, cfg: dict, **extra) -> dict:
    return dict(conta=conta_id, re_preco=fr.RE_PRECO, re_ctx=cfg["re_contexto"],
                re_marcou=RE_MARCOU, re_sair=RE_SAIR, re_recusa=RE_RECUSA, **extra)


# ------------------------------------------------------------------ registro

def registrar_fatos(c, conta_id: int, agora: datetime, cfg: dict, janela: dict) -> int:
    """Grava os toques dos fatos novos. Devolve quantos toques entraram."""
    ligado_em = cfg["ligado_em"] or agora
    novos = 0
    # 1) preço depois de ligar: a sequência inteira, de uma vez
    rows = c.execute(
        _SQL_FATOS + """
     and """ + _EM_ABERTO + """
     and not exists (select 1 from voltar_a_chamar_toques t
                      where t.conta_id = cv.conta_id and t.conversa_id = cv.id and t.toque >= 1
                        and t.criado_em > %(agora)s - interval '""" + str(_UMA_SEQUENCIA_DIAS) + """ days')""",
        _params(conta_id, cfg, agora=agora,
                desde=max(ligado_em, agora - timedelta(days=_JANELA_FATO_DIAS)), ate=agora)).fetchall()
    for conversa_id, lead, mid, preco_em in rows:
        agenda = agenda_de_toques(preco_em, janela, cfg["toques_min"])
        vencidos = [t for t, d in agenda if d <= agora]
        for toque, devido in agenda:
            estado = "pulado" if (toque in vencidos and toque != vencidos[-1]) else "pendente"
            r = c.execute(
                """insert into voltar_a_chamar_toques
                     (conta_id, prospeccao_id, conversa_id, preco_msg_id, toque, estado, devido_em)
                   values (%s,%s,%s,%s,%s,%s,%s)
                   on conflict (conta_id, conversa_id, preco_msg_id, toque) do nothing
                   returning id""", (conta_id, lead, conversa_id, mid, toque, estado, devido)).fetchone()
            novos += 1 if r else 0
    # 2) parados de antes de ligar: um toque só, e sempre na mão
    rows = c.execute(
        _SQL_FATOS + """
     and """ + _EM_ABERTO + """
     and (select max(x.criado_em) from mensagens x where x.conversa_id = cv.id)
         < %(agora)s - interval '""" + str(_REPESCAGEM_PARADA_H) + """ hours'
     and not exists (select 1 from mensagens r where r.conversa_id = cv.id
                      and r.direcao = 'in' and """ + _depois("r", "pr.mid") + """
                      and r.texto ~* %(re_sair)s)
     and not """ + _marcou_sql("cv.id", "pr.mid", "cv.prospeccao_id") + """
     and not exists (select 1 from voltar_a_chamar_toques t
                      where t.conta_id = cv.conta_id and t.conversa_id = cv.id)""",
        _params(conta_id, cfg, agora=agora, desde=ligado_em - timedelta(days=_REPESCAGEM_DIAS),
                ate=ligado_em)).fetchall()
    for conversa_id, lead, mid, _preco_em in rows:
        r = c.execute(
            """insert into voltar_a_chamar_toques
                 (conta_id, prospeccao_id, conversa_id, preco_msg_id, toque, estado, devido_em)
               values (%s,%s,%s,%s,0,'pendente',%s)
               on conflict (conta_id, conversa_id, preco_msg_id, toque) do nothing
               returning id""", (conta_id, lead, conversa_id, mid, agora)).fetchone()
        novos += 1 if r else 0
    return novos


def atualizar_estados(c, conta_id: int, agora: datetime, cfg: dict) -> None:
    """Encerra os toques pendentes que não fazem mais sentido. Só mexe em
    'pendente': o que já foi mandado fica como está, é histórico."""
    p = _params(conta_id, cfg, agora=agora)
    base = """ from conversas cv left join prospeccao p on p.id = cv.prospeccao_id
               where t.conta_id = %(conta)s and t.estado = 'pendente'
                 and cv.id = t.conversa_id """
    # pediu pra sair: o número entra na lista e não recebe mais nada, nunca. De
    # QUALQUER toque, não só dos pendentes: o "Pare" em resposta ao último toque (ou à
    # repescagem, que é um só) é justamente o mais provável.
    c.execute("""insert into voltar_a_chamar_bloqueios (conta_id, numero8, motivo)
                 select distinct t.conta_id, """ + _N8 + """, 'saiu'
                   from voltar_a_chamar_toques t
                   join conversas cv on cv.id = t.conversa_id
                   left join prospeccao p on p.id = cv.prospeccao_id
                  where t.conta_id = %(conta)s
                    and length(""" + _N8 + """) = 8
                    and exists (select 1 from mensagens m where m.conversa_id = t.conversa_id
                                 and m.direcao = 'in' and """ + _depois("m", "t.preco_msg_id") + """
                                 and m.texto ~* %(re_sair)s)
                 on conflict (conta_id, numero8) do nothing""", p)
    c.execute("""update voltar_a_chamar_toques t set estado = b.motivo
                   from conversas cv left join prospeccao p on p.id = cv.prospeccao_id,
                        voltar_a_chamar_bloqueios b
                  where t.conta_id = %(conta)s and t.estado = 'pendente'
                    and cv.id = t.conversa_id
                    and b.conta_id = t.conta_id and b.numero8 = """ + _N8, p)
    c.execute("update voltar_a_chamar_toques t set estado = 'marcou'" + base + " and "
              + _marcou_sql("t.conversa_id", "t.preco_msg_id", "cv.prospeccao_id"), p)
    c.execute("update voltar_a_chamar_toques t set estado = 'dispensado'" + base
              + " and p.status = 'perdido'", p)
    # "Não, obrigada" / "Não tenho interesse": encerra esta sequência (não bloqueia)
    c.execute("update voltar_a_chamar_toques t set estado = 'dispensado'" + base + """
                and exists (select 1 from mensagens m where m.conversa_id = t.conversa_id
                             and m.direcao = 'in' and """ + _depois("m", "t.preco_msg_id") + """
                             and m.texto ~* %(re_recusa)s and m.texto not like '%%?%%')""", p)
    # alguém da equipe já voltou a falar com ele: o toque de hoje seria repetição.
    # A rajada logo depois do preço ("a consulta dura 40 minutos...") não conta.
    c.execute("update voltar_a_chamar_toques t set estado = 'pulado'" + base + """
                and t.devido_em <= %(agora)s
                and exists (select 1 from mensagens m, mensagens pm
                             where pm.id = t.preco_msg_id and m.conversa_id = t.conversa_id
                               and m.direcao = 'out' and m.autor <> 'bot'
                               and m.criado_em > pm.criado_em + interval '""" + str(_RAJADA_MIN) + """ minutes'
                               and m.criado_em > %(agora)s - interval '""" + str(_HUMANO_RECENTE_H) + """ hours'
                               and """ + _NAO_E_TOQUE + ")", p)
    # o de +3h é do mesmo dia: se o dia virou sem ele sair (conversa viva à tarde,
    # clínica fechou), ele não aparece de manhã empurrando os outros pra dias seguidos
    c.execute("""update voltar_a_chamar_toques t set estado = 'pulado'
                  where t.conta_id = %(conta)s and t.estado = 'pendente' and t.toque = 1
                    and t.devido_em < %(hoje)s""", dict(p, hoje=_inicio_do_dia(agora)))
    # toque encalhado não sai semanas depois: 2 dias pros toques, 14 pra repescagem.
    # Sem isso, passar de 'sugere' pra 'ligado' mandaria primeiro os mais velhos.
    c.execute("""update voltar_a_chamar_toques t set estado = 'pulado'
                  where t.conta_id = %(conta)s and t.estado = 'pendente'
                    and t.devido_em < %(agora)s - case when t.toque = 0 then interval '14 days'
                                                       else interval '2 days' end""", p)
    # venceu um toque mais novo antes deste sair: fica só o mais novo
    c.execute("""update voltar_a_chamar_toques t set estado = 'pulado'
                  where t.conta_id = %(conta)s and t.estado = 'pendente' and t.devido_em <= %(agora)s
                    and exists (select 1 from voltar_a_chamar_toques t2
                                 where t2.conta_id = t.conta_id and t2.conversa_id = t.conversa_id
                                   and t2.preco_msg_id = t.preco_msg_id and t2.estado = 'pendente'
                                   and t2.devido_em <= %(agora)s and t2.toque > t.toque)""", p)


# ------------------------------------------------------------------ envio

# A conversa parou: a última mensagem tem `_PARADA_H` horas, e nada do que o
# paciente mandou depois da nossa última resposta é pergunta, foto ou áudio — isso
# é da recepção, e ele está em "Esperando resposta". Vale pra mostrar e pra mandar.
_PARADA = """(
   (select max(m.criado_em) from mensagens m where m.conversa_id = t.conversa_id)
       < %(agora)s - interval '""" + str(_PARADA_H) + """ hours'
   and not exists (
       select 1 from mensagens q
        where q.conversa_id = t.conversa_id and q.direcao = 'in'
          and q.criado_em > coalesce((select max(o.criado_em) from mensagens o
                                       where o.conversa_id = t.conversa_id and o.direcao = 'out'),
                                      '-infinity'::timestamptz)
          and (q.texto like '%%?%%' or q.midia_tipo is not null
               or q.texto ~ '^(🎤|🎵|📷|🎥|📎)')))"""

# SOZINHO (modo 'ligado'), só com a última mensagem NOSSA. "Tem horário amanhã de
# manhã", sem ponto de interrogação, passa pelo `_PARADA`; o Zaq respondendo isso
# com "conseguiu ver as informações?" seria pior que não mandar. Com a última
# palavra do paciente, o toque fica na tela e a recepção lê antes de mandar.
_ULTIMA_E_NOSSA = """(
   (select m.direcao from mensagens m where m.conversa_id = t.conversa_id
     order by m.criado_em desc, m.id desc limit 1) = 'out')"""

# 1 por paciente por dia — na tela, no clique e no automático, a mesma regra. Por
# NÚMERO, não por conversa: o mesmo telefone pode ter duas conversas (dois chips).
_NAO_MANDADO_HOJE = """
   not exists (select 1 from voltar_a_chamar_toques o
                where o.conta_id = t.conta_id and o.estado = 'enviado' and o.enviado_em >= %(hoje)s
                  and (o.conversa_id = t.conversa_id
                       or """ + _n8_da_conversa("o.conversa_id") + " = " + _n8_da_conversa("t.conversa_id") + "))"

def _inicio_do_dia(agora: datetime) -> datetime:
    return _utc(datetime.combine(_local(agora).date(), time(0, 0)))


def enviar(c, conta_id: int, toque_id: int, cfg: dict, *, por: str,
           membro_id: int | None = None, agora: datetime | None = None) -> dict:
    """Manda UM toque. O `update ... where estado='pendente' returning` é a trava:
    dois cliques, dois workers ou clique e poller juntos — só um passa.

    O estado vira 'enviado' e é COMMITADO antes de chamar o WhatsApp: se o processo
    cair no meio, o paciente fica sem o toque, e nunca com dois.
    """
    agora = agora or datetime.now(timezone.utc)
    r = c.execute(
        """update voltar_a_chamar_toques t
              set estado = 'enviado', enviado_em = %(agora)s, enviado_por = %(por)s,
                  membro_id = %(membro)s
            where t.id = %(id)s and t.conta_id = %(conta)s and t.estado = 'pendente'
              and """ + _NAO_MANDADO_HOJE + """
        returning t.conversa_id, t.toque""",
        {"agora": agora, "por": por, "membro": membro_id, "id": toque_id, "conta": conta_id,
         "hoje": _inicio_do_dia(agora)}).fetchone()
    if not r:
        ainda = c.execute("select estado from voltar_a_chamar_toques where id=%s and conta_id=%s",
                          (toque_id, conta_id)).fetchone()
        c.rollback()
        return {"ok": False, "erro": "hoje_ja" if (ainda and ainda[0] == "pendente") else "ja_tratado"}
    conversa_id, toque = r
    info = c.execute(
        "select " + _DESTINO + """,
                coalesce(nullif(p.contato,''), nullif(cv.contato_nome,''), p.empresa, ''),
                cv.prospeccao_id
           from conversas cv left join prospeccao p on p.id = cv.prospeccao_id
          where cv.id = %s""", (conversa_id,)).fetchone()
    destino, nome, lead = (info or ("", "", None))
    texto = texto_do_toque(cfg["textos"][str(toque)], nome)
    if not destino or tem_palavra_de_saude(texto):
        c.execute("update voltar_a_chamar_toques set estado='falhou', texto=%s where id=%s",
                  (texto, toque_id))
        c.commit()
        return {"ok": False, "erro": "sem_numero" if not destino else "saude"}
    c.execute("update voltar_a_chamar_toques set texto=%s where id=%s", (texto, toque_id))
    c.commit()
    from finance import agente
    try:
        res = agente._mandar(c, conta_id, "whatsapp", destino, texto, conversa_id) or {}
    except Exception as e:  # noqa: BLE001
        _log.warning("voltar a chamar: envio falhou (toque %s): %s", toque_id, e)
        res = {"ok": False, "erro": type(e).__name__}
    if not res.get("ok"):
        c.execute("update voltar_a_chamar_toques set estado='falhou' where id=%s", (toque_id,))
        c.commit()
        return {"ok": False, "erro": res.get("erro") or "envio"}
    mid = c.execute(
        """insert into mensagens (conversa_id, canal, direcao, autor, texto, membro_id, provider_sid,
                                   criado_em)
           values (%s, 'whatsapp', 'out', %s, %s, %s, %s, %s) returning id""",
        (conversa_id, "bot" if por == "agente" else "humano", texto, membro_id,
         res.get("sid"), agora)).fetchone()[0]
    c.execute("update conversas set ultima_msg_em=now() where id=%s", (conversa_id,))
    c.execute("update voltar_a_chamar_toques set mensagem_id=%s where id=%s", (mid, toque_id))
    try:
        if lead is None:
            raise LookupError("conversa sem card")
        with c.transaction():
            c.execute("""insert into prospeccao_atividades (prospeccao_id, membro_id, tipo, descricao)
                         values (%s, %s, 'whatsapp', %s)""",
                      (lead, membro_id, (f"Voltar a chamar — "
                                         f"{'repescagem' if toque == 0 else f'toque {toque}'}, "
                                         f"{'pelo Zaq' if por == 'agente' else 'pela recepção'}")))
    except LookupError:
        pass
    except Exception:  # noqa: BLE001 — histórico é bônus; a mensagem já saiu
        _log.info("voltar a chamar: atividade não gravada (lead %s)", lead, exc_info=True)
    c.commit()
    return {"ok": True, "mensagem_id": mid}


def _enviar_um(c, conta_id: int, agora: datetime, cfg: dict, janela: dict) -> dict:
    """No modo 'ligado': no máximo UM por ciclo, dentro da janela, até o teto do dia
    da conta e 1 por paciente por dia. A repescagem (toque 0) nunca sai sozinha."""
    if not fr.dentro_da_janela(agora, janela):
        return {"enviados": 0, "falhas": 0}
    hoje = _inicio_do_dia(agora)
    feitos = c.execute(
        """select count(*) from voltar_a_chamar_toques
            where conta_id=%s and enviado_por='agente' and enviado_em >= %s""",
        (conta_id, hoje)).fetchone()[0]
    if feitos >= cfg["teto_dia"]:
        return {"enviados": 0, "falhas": 0}
    r = c.execute(
        """select t.id from voltar_a_chamar_toques t
            where t.conta_id=%(conta)s and t.estado='pendente' and t.toque >= 1
              and t.devido_em <= %(agora)s and """ + _PARADA + " and " + _ULTIMA_E_NOSSA + """
              and """ + _NAO_MANDADO_HOJE + """
            order by t.devido_em, t.id limit 1""",
        {"conta": conta_id, "agora": agora, "hoje": hoje}).fetchone()
    c.commit()
    if not r:
        return {"enviados": 0, "falhas": 0}
    # o mesmo relógio do teto e do "1 por paciente por dia" logo acima
    res = enviar(c, conta_id, r[0], cfg, por="agente", agora=agora)
    return {"enviados": 1 if res["ok"] else 0, "falhas": 0 if res["ok"] else 1}


# ------------------------------------------------------------------ o motor

def passar_conta(c, conta_id: int, agora: datetime) -> dict:
    zero = {"novos": 0, "enviados": 0, "falhas": 0}
    perfil = fr.perfil_da_conta(c, conta_id)
    if perfil != "clinica":        # a verificação dupla do docstring
        return zero
    cfg = config(c, conta_id, perfil)
    if not cfg or cfg["modo"] == "off":
        return zero
    if cfg["ligado_em"] is None:
        c.execute("update voltar_a_chamar_config set ligado_em=%s where conta_id=%s and ligado_em is null",
                  (agora, conta_id))
        cfg["ligado_em"] = agora
    janela = fr.config(c, conta_id)
    novos = registrar_fatos(c, conta_id, agora, cfg, janela)
    atualizar_estados(c, conta_id, agora, cfg)
    c.commit()
    out = dict(zero, novos=novos)
    if cfg["modo"] == "ligado":
        out.update(_enviar_um(c, conta_id, agora, cfg, janela))
    return out


def rodar(pool, agora: datetime | None = None) -> dict:
    """Uma passada em todas as contas que saíram do 'off'. Chamada pelo poller
    (web/app.py), depois do follow-up. Best-effort por conta."""
    agora = agora or datetime.now(timezone.utc)
    total = {"contas": 0, "novos": 0, "enviados": 0, "falhas": 0}
    with pool.connection() as lockc:
        # dois workers no Render: sem o lock, os dois mandam o mesmo toque
        if not lockc.execute("select pg_try_advisory_lock(%s)", (_LOCK,)).fetchone()[0]:
            return total
        try:
            with pool.connection() as c:
                try:
                    with c.transaction():
                        contas = [r[0] for r in c.execute(
                            "select conta_id from voltar_a_chamar_config where modo <> 'off'"
                        ).fetchall()]
                except Exception:  # noqa: BLE001 — migração 343 ainda não rodou
                    contas = []
            for conta_id in contas:
                try:
                    with pool.connection() as c:
                        r = passar_conta(c, conta_id, agora)
                        c.commit()
                    total["contas"] += 1
                    for k in ("novos", "enviados", "falhas"):
                        total[k] += r[k]
                except Exception:  # noqa: BLE001
                    _log.warning("voltar a chamar falhou na conta %s", conta_id, exc_info=True)
        finally:
            lockc.execute("select pg_advisory_unlock(%s)", (_LOCK,))
    return total


# ------------------------------------------------------------------ ações da tela

def _encerrar(c, conta_id: int, conversa_id: int, preco_msg_id: int, estado: str) -> None:
    """Encerra a sequência com a decisão da recepção. Sem toque pendente (todos já
    saíram), a decisão fica na última linha — `enviado_em` e `mensagem_id` continuam
    lá, então o histórico de que o toque saiu não se perde."""
    n = c.execute("""update voltar_a_chamar_toques set estado=%s
                      where conta_id=%s and conversa_id=%s and preco_msg_id=%s
                        and estado='pendente'""", (estado, conta_id, conversa_id, preco_msg_id)).rowcount
    if not n:
        c.execute("""update voltar_a_chamar_toques set estado=%s
                      where id = (select id from voltar_a_chamar_toques
                                   where conta_id=%s and conversa_id=%s and preco_msg_id=%s
                                   order by toque desc limit 1)""",
                  (estado, conta_id, conversa_id, preco_msg_id))


def _sequencia(c, conta_id: int, toque_id: int):
    return c.execute(
        """select t.conversa_id, t.preco_msg_id, cv.prospeccao_id
             from voltar_a_chamar_toques t join conversas cv on cv.id = t.conversa_id
            where t.id=%s and t.conta_id=%s""", (toque_id, conta_id)).fetchone()


def mandar_sugerido(pool, conta_id: int, toque_id: int, membro_id: int | None,
                    agora: datetime | None = None) -> dict:
    """O botão Mandar. Sai como mensagem da recepção (autor 'humano', com o membro).

    A tela pode estar aberta há uma hora: antes de mandar, os estados são
    recalculados (sair, marcou, bloqueio, recepção que já falou) e o toque tem que
    continuar na tela — conversa parada e ninguém mandou nada pra ele hoje. Com o
    modo 'off', nada sai, nem clicando."""
    agora = agora or datetime.now(timezone.utc)
    with pool.connection() as c:
        cfg = config(c, conta_id)
        if not cfg:
            return {"ok": False, "erro": "perfil"}
        if cfg["modo"] == "off":
            return {"ok": False, "erro": "desligado"}
        atualizar_estados(c, conta_id, agora, cfg)
        c.commit()
        ainda = c.execute(
            """select 1 from voltar_a_chamar_toques t
                where t.id = %(id)s and t.conta_id = %(conta)s and t.estado = 'pendente'
                  and """ + _PARADA,
            {"id": toque_id, "conta": conta_id, "agora": agora}).fetchone()
        c.rollback()
        if not ainda:
            return {"ok": False, "erro": "ja_tratado"}
        return enviar(c, conta_id, toque_id, cfg, por="recepcao", membro_id=membro_id, agora=agora)


def dispensar(pool, conta_id: int, toque_id: int) -> bool:
    """"Não chamar": encerra a sequência deste paciente (inclusive na lista de quem
    respondeu). O número continua podendo receber toque de um preço futuro."""
    with pool.connection() as c:
        s = _sequencia(c, conta_id, toque_id)
        if not s:
            return False
        _encerrar(c, conta_id, s[0], s[1], "dispensado")
        c.commit()
        return True


def nao_e_paciente(pool, conta_id: int, toque_id: int, membro_id: int | None) -> bool:
    """"Não é paciente": o número sai pra sempre (fornecedor, banco, contador)."""
    with pool.connection() as c:
        r = c.execute(
            "select " + _N8 + """ from voltar_a_chamar_toques t
               join conversas cv on cv.id = t.conversa_id
               left join prospeccao p on p.id = cv.prospeccao_id
              where t.id=%s and t.conta_id=%s""", (toque_id, conta_id)).fetchone()
        if not r or len(r[0] or "") != 8:
            return False
        c.execute("""insert into voltar_a_chamar_bloqueios (conta_id, numero8, motivo, membro_id)
                     values (%s,%s,'nao_paciente',%s)
                     on conflict (conta_id, numero8) do update set motivo='nao_paciente'""",
                  (conta_id, r[0], membro_id))
        c.execute("""update voltar_a_chamar_toques t set estado='nao_paciente'
                       from conversas cv left join prospeccao p on p.id = cv.prospeccao_id
                      where t.conta_id=%s and t.estado = 'pendente'
                        and cv.id = t.conversa_id
                        and """ + _N8 + " = %s", (conta_id, r[0]))
        c.commit()
        return True


def ja_marcou(pool, conta_id: int, toque_id: int, membro_id: int | None) -> bool:
    """"Já marcou" (por telefone, no balcão, no Amigo): encerra a sequência e leva o
    card pra Consulta agendada — é a mão da recepção, e conta no placar."""
    with pool.connection() as c:
        s = _sequencia(c, conta_id, toque_id)
        if not s:
            return False
        _encerrar(c, conta_id, s[0], s[1], "marcou")
        lead = s[2]     # sem card, só a sequência se encerra
        antes = lead and c.execute("select status from prospeccao where id=%s and conta_id=%s for update",
                                   (lead, conta_id)).fetchone()
        if antes and antes[0] in ("novo", "contatado", "follow_up"):
            c.execute("update prospeccao set status='qualificado', atualizado_em=now() where id=%s",
                      (lead,))
            fr.registrar_movimento(c, conta_id, lead, antes[0], "qualificado", "manual", membro_id)
        c.commit()
        return True


# ------------------------------------------------------------------ a tela e o placar

def placar(c, conta_id: int, desde: datetime, ate: datetime, cfg: dict) -> dict:
    """Receberam o preço, marcaram, e nunca foram chamados de novo — no intervalo.

    É a SQL do registro sem os filtros de "lead em aberto": o placar olha pra
    trás, e o paciente que marcou já saiu do aberto. "Marcou" é o mesmo da tela:
    "Prontinho" na conversa depois do preço, card em Consulta agendada ou além, ou
    o "Já marcou" da recepção — sem prazo, senão quem marca depois do toque de +7
    dias nunca entraria. Quem pediu pra sair continua em "receberam"; só o 'não é
    paciente' sai da conta. "Nunca chamado" é quem não marcou,
    não recebeu mais nada nosso depois da rajada do preço, e o preço já tem 1 dia
    — antes disso é cedo, e o placar acusaria quem a recepção ainda vai chamar.
    """
    r = c.execute(
        "with fatos as (" + _SQL_FATOS_BASE + " and " + _nao_bloqueado(True) + """)
         select count(*),
                count(*) filter (where f.marcou),
                count(*) filter (where not f.marcou and not f.chamado
                                   and f.criado_em < %(ate)s - interval '1 day')
           from (select fa.*,
                        (""" + _marcou_sql("fa.conversa_id", "fa.mid", "fa.prospeccao_id") + """
                         or exists (select 1 from voltar_a_chamar_toques t
                                     where t.conversa_id = fa.conversa_id and t.preco_msg_id = fa.mid
                                       and t.estado = 'marcou')) as marcou,
                        exists (select 1 from mensagens mm where mm.conversa_id = fa.conversa_id
                                 and mm.direcao = 'out'
                                 and mm.criado_em > fa.criado_em + interval '""" + str(_RAJADA_MIN) + """ minutes') as chamado
                   from fatos fa) f""",
        _params(conta_id, cfg, desde=desde, ate=ate)).fetchone()
    receberam, marcaram, nunca = r or (0, 0, 0)
    return {"receberam": receberam, "marcaram": marcaram, "nunca_chamados": nunca,
            "taxa": round(100 * marcaram / receberam) if receberam else None}


_ROTULO_TOQUE = {0: "repescagem", 1: "+3 horas", 2: "+1 dia", 3: "+3 dias", 4: "+7 dias"}


def _fmt(quando: datetime | None) -> str:
    if not quando:
        return ""
    return _local(quando).strftime("%d/%m %H:%M")


def hoje(c, conta_id: int, agora: datetime, cfg: dict) -> dict:
    """Tudo que a tela Hoje mostra, numa conexão."""
    p = _params(conta_id, cfg, agora=agora, hoje=_inicio_do_dia(agora))
    esperando = []
    for r in c.execute(
            """select cv.id, cv.prospeccao_id,
                      coalesce(nullif(p.contato,''), nullif(cv.contato_nome,''), p.empresa, ''),
                      ult.texto, ult.criado_em
                 from conversas cv
                 left join prospeccao p on p.id = cv.prospeccao_id and p.conta_id = cv.conta_id
                 join lateral (select m.direcao, m.texto, m.criado_em from mensagens m
                                where m.conversa_id = cv.id
                                order by m.criado_em desc, m.id desc limit 1) ult on true
                where cv.conta_id = %(conta)s and cv.canal = 'whatsapp'
                  and ult.direcao = 'in' and ult.criado_em > %(agora)s - interval '3 days'
                  and """ + _FORA_DA_EQUIPE + " and " + _nao_bloqueado(True) + """
                order by ult.criado_em limit 40""", p).fetchall():
        esperando.append({"conversa_id": r[0], "lead": r[1], "nome": r[2] or "Sem nome",
                          "texto": (r[3] or "")[:160], "quando": _fmt(r[4]), "em": r[4]})

    def _toques(where: str) -> list[dict]:
        out = []
        for r in c.execute(
                """select t.id, t.toque, t.devido_em, t.conversa_id, cv.prospeccao_id,
                          coalesce(nullif(p.contato,''), nullif(cv.contato_nome,''), p.empresa, ''),
                          pm.criado_em,
                          (select m.texto from mensagens m where m.conversa_id = t.conversa_id
                            and m.direcao = 'in' and """ + _depois("m", "t.preco_msg_id") + """
                            order by m.criado_em desc, m.id desc limit 1)
                     from voltar_a_chamar_toques t
                     join conversas cv on cv.id = t.conversa_id
                     left join prospeccao p on p.id = cv.prospeccao_id
                     join mensagens pm on pm.id = t.preco_msg_id
                    where t.conta_id = %(conta)s and t.estado = 'pendente' and """ + where + """
                    order by t.devido_em, t.id limit 60""", p).fetchall():
            out.append({"id": r[0], "toque": r[1], "rotulo": _ROTULO_TOQUE.get(r[1], ""),
                        "devido": _fmt(r[2]), "conversa_id": r[3], "lead": r[4],
                        "nome": r[5] or "Sem nome", "preco_em": _fmt(r[6]),
                        # o que o paciente disse depois do preço: a recepção lê antes
                        # de apertar Mandar (uma recusa em frase passaria despercebida)
                        "ultima": (r[7] or "")[:160],
                        "texto": texto_do_toque(cfg["textos"][str(r[1])], r[5])})
        return out

    responderam = []
    for r in c.execute(
            """select distinct on (t.conversa_id, t.preco_msg_id)
                      t.id, t.conversa_id, cv.prospeccao_id,
                      coalesce(nullif(p.contato,''), nullif(cv.contato_nome,''), p.empresa, ''),
                      (select m.texto from mensagens m where m.conversa_id = t.conversa_id
                        and m.direcao = 'in' order by m.criado_em desc, m.id desc limit 1),
                      (select max(m.criado_em) from mensagens m where m.conversa_id = t.conversa_id
                        and m.direcao = 'in')
                 from voltar_a_chamar_toques t
                 join conversas cv on cv.id = t.conversa_id
                 left join prospeccao p on p.id = cv.prospeccao_id
                where t.conta_id = %(conta)s and t.criado_em > %(agora)s - interval '30 days'
                  and exists (select 1 from mensagens m where m.conversa_id = t.conversa_id
                               and m.direcao = 'in' and """ + _depois("m", "t.preco_msg_id") + """)
                  and not exists (select 1 from voltar_a_chamar_toques o
                                   where o.conta_id = t.conta_id and o.conversa_id = t.conversa_id
                                     and o.preco_msg_id = t.preco_msg_id
                                     and o.estado in ('marcou','dispensado','nao_paciente','saiu'))
                  and not """ + _marcou_sql("t.conversa_id", "t.preco_msg_id", "cv.prospeccao_id") + """
                  and coalesce(p.status, '') <> 'perdido'
                order by t.conversa_id, t.preco_msg_id, t.toque""", p).fetchall():
        responderam.append({"id": r[0], "conversa_id": r[1], "lead": r[2],
                            "nome": r[3] or "Sem nome", "frase": (r[4] or "")[:160],
                            "quando": _fmt(r[5]), "em": r[5]})
    _velho = datetime(1970, 1, 1, tzinfo=timezone.utc)
    responderam.sort(key=lambda x: x["em"] or _velho, reverse=True)
    voltar = _toques("t.toque >= 1 and t.devido_em <= %(agora)s and " + _PARADA
                     + " and " + _NAO_MANDADO_HOJE)
    repescagem = _toques("t.toque = 0 and " + _PARADA + " and " + _NAO_MANDADO_HOJE)
    # quem já tem a mensagem pronta lá em cima não aparece duas vezes
    na_fila = {t["conversa_id"] for t in voltar + repescagem}
    responderam = [r for r in responderam if r["conversa_id"] not in na_fila]

    inicio_mes = _utc(datetime.combine(_local(agora).date().replace(day=1), time(0, 0)))
    proximos = c.execute(
        """select count(*) from voltar_a_chamar_toques
            where conta_id=%(conta)s and estado='pendente' and toque >= 1 and devido_em > %(agora)s""",
        p).fetchone()[0]
    return {
        "esperando": esperando,
        "voltar": voltar,
        "repescagem": repescagem,
        "responderam": responderam[:40],
        "proximos": proximos,
        "placar": placar(c, conta_id, inicio_mes, agora, cfg),
    }
