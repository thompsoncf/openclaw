"""O agente do WhatsApp na clínica (fase 3b): diz o preço, oferece horário livre de
verdade, marca, e passa pra recepção o que não é dele.

SÓ NO PERFIL `clinica` (finance/raio_x_perfil.py). `agente._atender` desvia pra cá
logo depois de ler a conversa; o caminho das outras contas (festa, orçamento,
visita, seguros) não passa por aqui e não muda uma linha.

O QUE ELE FAZ
  - diz o preço do atendimento só se "o agente pode dizer este preço" estiver
    marcado (Configurar › Atendimentos); senão, "a recepção informa";
  - oferece até 3 horários livres de verdade (grade − bloqueios − marcados), só dos
    atendimentos com "o agente pode marcar sozinho", e MARCA quando o paciente
    escolhe. Com o Amigo fora (decisão de 25/09/2026) a agenda do Zaq é a única, e
    esse era o caminho da decisão A da parte 1: "segura até a agenda ser uma só";
  - responde o "1"/"2" do lembrete da véspera sem chamar a IA.

O QUE ELE NUNCA FAZ (trava de produto, seção 07 da parte 1): indicar tratamento,
falar de sintoma, interpretar exame ou foto, dizer se o convênio cobre, dar
desconto. Aí ele PASSA PRA RECEPÇÃO: manda um texto FIXO (escrito aqui, não pela
IA, porque é justamente o assunto em que ela não pode improvisar) e deixa o item
"O agente passou pra você" na tela Hoje (migração 363). Foto, áudio e documento
também passam: ele não vê nem ouve.

E NUNCA SE DESLIGA SOZINHO (ver agente.py): passar pra recepção não pausa a
conversa. O próximo "quanto é a consulta?" ele responde; o mesmo assunto ele não
repete enquanto a recepção não responder.
"""
from __future__ import annotations

import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone

from finance import clinica_agenda as ca
from finance import clinica_config as cc

_log = logging.getLogger("agente.clinica")

DIAS = 14                             # até onde o cardápio de horários olha
DIAS_NO_CARDAPIO = 6                  # quantos dias diferentes a IA enxerga por agenda
POR_DIA = 2                           # ...e no máximo 2 horários de cada dia (manhã e tarde, se der)
ANTECEDENCIA = timedelta(hours=1)     # o agente não marca pra daqui a 10 minutos
OFERECE = 3                           # quantos horários o paciente lê de cada vez

ACOES = ("responder", "consulta", "repassar")

#: motivo → (o que a tela Hoje diz, o texto que o paciente recebe). O texto é fixo
#: de propósito: é o assunto em que a IA não pode improvisar.
MOTIVOS = {
    "sintoma": ("pergunta de saúde",
                "Entendi! Sobre isso, quem pode te orientar é o médico, na consulta. Por aqui eu não "
                "consigo falar de sintomas nem de tratamentos. Já avisei a nossa recepção, que te "
                "responde por aqui 😊"),
    "foto": ("mandou foto ou vídeo",
             "Recebi! Por aqui eu não consigo abrir fotos e vídeos, então já passei para a nossa "
             "recepção, que te responde por aqui 😊"),
    "audio": ("mandou áudio",
              "Recebi o seu áudio! Por aqui eu não consigo ouvir, então já pedi para a nossa recepção "
              "ouvir e te responder 😊"),
    "arquivo": ("mandou documento",
                "Recebi o arquivo! Já passei para a nossa recepção, que te responde por aqui 😊"),
    "desconto": ("pediu desconto ou condição",
                 "Sobre condições e descontos, quem fala é a nossa recepção. Já avisei, e ela te "
                 "responde por aqui 😊"),
    "convenio": ("pergunta de convênio",
                 "Sobre convênio, quem confirma com você é a nossa recepção. Já avisei, e ela te "
                 "responde por aqui 😊"),
    "urgencia": ("urgência",
                 "Se for uma emergência, procure agora o pronto-socorro mais próximo ou ligue 192 "
                 "(SAMU). Já avisei a nossa recepção para te responder o quanto antes."),
    "remarcar": ("quer remarcar ou desmarcar",
                 "Certo! Já avisei a nossa recepção, que te responde por aqui para ajeitar o seu "
                 "horário 😊"),
    "marcar": ("quer marcar",
               "Anotado! A nossa recepção confirma o horário com você por aqui 😊"),
    "pessoa": ("quer falar com alguém",
               "Claro! Já avisei a nossa recepção, que te responde por aqui 😊"),
}

#: o que chega pelo WhatsApp quando o paciente manda mídia: o agente só lê TEXTO,
#: e o anexo vira um marcador (finance/cockpit.py, o espelho do wa-qr).
_MIDIA = (("📷", "foto"), ("🎥", "foto"), ("🖼", "foto"), ("🎤", "audio"), ("🎵", "audio"),
          ("📄", "arquivo"), ("📎", "arquivo"))

#: rede de segurança da urgência, sem depender da IA. Lista curta e forte: "preciso
#: de um horário urgente" não é emergência (a IA oferece o primeiro horário).
_RE_URGENTE = re.compile(
    r"\b(emergencia|sangr\w*|hemorrag\w*|falta de ar|nao consigo respirar|desmai\w*|anafila\w*|"
    r"convuls\w*|pronto.?socorro|samu|garganta fechando|reacao alergica)\b")

#: conselho de saúde na resposta da IA, apesar da instrução: não sai; vira repasse.
#: Estreita de propósito: "recomendo chegar 10 minutos antes" e "você pode usar o
#: estacionamento" são recepção, não conselho — só pega remédio, creme, tratamento
#: e a IA opinando sobre gravidade ou causa.
_PRODUTO = (r"(creme|pomada|remedio|medicamento|protetor|hidratante|sabonete|acido|vitamina|"
            r"antibiotic\w*|corticoide|anti.?inflamat\w*)")
_RE_CONSELHO = re.compile(
    rf"\b((usar|passar|tomar|aplicar)( (um|uma|o|a|esse|essa|este|esta))? {_PRODUTO}"
    rf"|(recomendo|aconselho|indico|sugiro)( (o|a|um|uma))? (tratamento|procedimento|{_PRODUTO})"
    r"|antibiotic\w*|corticoide|nao (e|parece) (nada )?grave|parece (ser )?grave"
    r"|pode ser (alergia|infeccao|micose|fungo|cancer|melanoma|dermatite|herpes))\b")

#: a IA dizendo que marcou sem ter devolvido acao=consulta: quem marca é o código.
_RE_MARQUEI = re.compile(r"\b(marquei|agendei|reservei|deixei marcad\w*|ja marquei)\b")


def _sem_acento(txt: str | None) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", txt or "")
                   if unicodedata.category(ch) != "Mn").lower()


def midia(texto: str | None) -> str | None:
    """O motivo do repasse quando a mensagem é mídia (foto, áudio, documento)."""
    t = (texto or "").lstrip()
    for pre, motivo in _MIDIA:
        if t.startswith(pre):
            return motivo
    return None


def urgente(texto: str | None) -> bool:
    return bool(_RE_URGENTE.search(_sem_acento(texto)))


def parece_conselho(texto: str | None) -> bool:
    return bool(_RE_CONSELHO.search(_sem_acento(texto)))


def diz_que_marcou(texto: str | None) -> bool:
    return bool(_RE_MARQUEI.search(_sem_acento(texto)))


# ------------------------------------------------------------------ quem é a conta

def e_clinica(c, conta_id: int) -> bool:
    from finance import raio_x_perfil as rxp
    r = c.execute("""select coalesce(n.slug,'') from contas ct
                       left join nichos n on n.id = ct.nicho_id
                      where ct.id=%s""", (conta_id,)).fetchone()
    return rxp.perfil_por_nicho(r[0] if r else "") == "clinica"


# ------------------------------------------------------------------ o cardápio de horários

def _ja_e_paciente(c, conta_id: int, lead: int | None) -> bool:
    """Retorno e sessão de pacote só pra quem já foi atendido aqui: sem isso, qualquer
    um que dissesse "é retorno" marcava de graça no lugar da consulta."""
    if not lead:
        return False
    return c.execute("""select 1 from eventos_agenda where conta_id=%s and prospeccao_id=%s
                         and situacao='finalizado' limit 1""", (conta_id, lead)).fetchone() is not None


def codigo(prof_id: int, tipo_id: int, inicio: datetime) -> str:
    """"3-7-2609300830": profissional, atendimento e o horário de Brasília. Estável
    entre uma mensagem e outra (a lista é recalculada a cada volta, e o paciente
    escolhe em cima do que leu antes)."""
    return f"{prof_id}-{tipo_id}-{ca.local(inicio):%y%m%d%H%M}"


def ler_codigo(txt: str | None) -> tuple[int, int, datetime] | None:
    m = re.fullmatch(r"\s*(\d{1,9})-(\d{1,9})-(\d{10})\s*", str(txt or ""))
    if not m:
        return None
    try:
        loc = datetime.strptime(m.group(3), "%y%m%d%H%M")
    except ValueError:
        return None
    return int(m.group(1)), int(m.group(2)), ca.utc(loc.date(), loc.time())


def _escolher(livres: list[dict]) -> list[dict]:
    """Até POR_DIA por dia (o primeiro do dia e o primeiro 3h depois, pra ter manhã e
    tarde), em até DIAS_NO_CARDAPIO dias."""
    por_dia: dict = {}
    for x in livres:
        por_dia.setdefault(ca.local(x["inicio"]).date(), []).append(x)
    out = []
    for dia in sorted(por_dia)[:DIAS_NO_CARDAPIO]:
        xs = por_dia[dia]
        pegos = [xs[0]]
        depois = next((x for x in xs[1:] if x["inicio"] >= xs[0]["inicio"] + timedelta(hours=3)), None)
        if depois is None and len(xs) > 1:
            depois = xs[1]
        if depois is not None:
            pegos.append(depois)
        out.extend(pegos[:POR_DIA])
    return out


def cardapio(c, conta_id: int, lead: int | None, agora: datetime) -> dict:
    """Os atendimentos que o agente pode marcar pra ESTE contato e os horários livres
    de cada um, com o código que a IA devolve."""
    paciente = _ja_e_paciente(c, conta_id, lead)
    tipos = [t for t in cc.listar_tipos(c, conta_id)
             if t["agente_marca"] and (paciente or t["categoria"] not in ("retorno", "sessao"))]
    profs = ca._profs_que_atendem(c, conta_id)
    locais = {x["id"]: x for x in cc.listar_locais(c, conta_id, so_ativos=False)}
    desde = agora + ANTECEDENCIA
    hoje = ca.hoje_br(agora)
    slots = []
    for t in tipos:
        for p in profs:
            if t["id"] not in p["tipos"]:
                continue
            for x in _escolher(ca.livres(c, conta_id, p["id"], t["id"], hoje, DIAS, desde)):
                loc = locais.get(x["local_id"])
                onde = ""
                if loc:
                    onde = (f"em {loc['nome']}" if loc["tipo"] == "viagem" else f"no {loc['nome']}") + \
                        (f" ({loc['cidade']})" if loc["cidade"] and loc["tipo"] != "viagem" else "")
                slots.append({"codigo": codigo(p["id"], t["id"], x["inicio"]), "tipo_id": t["id"],
                              "tipo": t["nome"], "prof_id": p["id"], "prof": p["nome"],
                              "inicio": x["inicio"], "local_id": x["local_id"],
                              "quando": f"{ca.dia_txt(x['inicio'])} às {ca.hora_txt(x['inicio'])}",
                              "onde": onde})
    return {"tipos": tipos, "slots": slots, "paciente": paciente}


def marcadas(c, conta_id: int, lead: int | None, fone: str | None, agora: datetime) -> list[dict]:
    """As consultas pela frente deste contato (pelo card ou pelo celular)."""
    dig = ca._digitos(fone)
    rows = c.execute(
        r"""select e.id from eventos_agenda e
             where e.conta_id=%s and e.situacao in ('agendado','confirmado') and e.status='ativo'
               and e.inicio > %s
               and (e.prospeccao_id = %s
                    or (length(%s) >= 8 and right(regexp_replace(coalesce(e.paciente_fone,''), '\D', '', 'g'), 8) = %s))
             order by e.inicio limit 5""", (conta_id, agora, lead, dig, dig[-8:])).fetchall()
    out = []
    for (eid,) in rows:
        ev = ca.evento(c, conta_id, eid)
        if ev:
            out.append(ev)
    return out


# ------------------------------------------------------------------ o prompt

def _linha_marcada(c, conta_id: int, ev: dict) -> str:
    return (f"- {ev['paciente']} · {ca._palavra(ev)} com {ca._prof_nome(c, conta_id, ev)} · "
            f"{ca.dia_txt(ev['inicio'])} às {ev['hora']}{ca._onde(c, conta_id, ev)} ({ev['sit_txt'].lower()})")


def bloco_agenda(c, conta_id: int, menu: dict, ja: list[dict]) -> str:
    linhas = []
    if ja:
        linhas.append("CONSULTAS JÁ MARCADAS DESTE CONTATO:")
        linhas += [_linha_marcada(c, conta_id, ev) for ev in ja]
        linhas.append("")
    if menu["slots"]:
        linhas.append("HORÁRIOS LIVRES QUE VOCÊ PODE OFERECER E MARCAR (código = horário):")
        atual = None
        for s in menu["slots"]:
            if (s["tipo_id"], s["prof_id"]) != atual:
                atual = (s["tipo_id"], s["prof_id"])
                linhas.append(f"[{s['tipo']} com {s['prof']}]")
            linhas.append(f"  {s['codigo']} = {s['quando']}{', ' + s['onde'] if s['onde'] else ''}")
        linhas.append("Só estes atendimentos você marca. Se o paciente quer outro (um procedimento, "
                      "uma cirurgia, um exame), acao=repassar com motivo marcar.")
    else:
        linhas.append("AGENDA: você não tem horário para oferecer agora. Se o paciente quiser marcar, "
                      "pergunte o melhor dia e período e devolva acao=repassar com motivo marcar "
                      "(a recepção confirma o horário). Nunca invente horário.")
    return "\n".join(linhas)


def prompt(c, conta_id: int, cfg: dict, *, nome: str, historico: str, gemeo_nota: str,
           instr: str, faqs: str, cat_txt: str, menu: dict, ja: list[dict],
           agora: datetime) -> tuple[str, str]:
    tom = "informal e acolhedor" if cfg.get("tom") == "informal" else "formal e acolhedor"
    loc = ca.local(agora)
    system = (
        "Você é a recepção virtual de uma clínica, no WhatsApp. Fala em português do Brasil, "
        f"tom {tom}, mensagens curtas. Use SÓ o que está na base abaixo: NUNCA invente preço, "
        "horário, profissional, endereço ou prazo. Responda SEMPRE só com JSON válido, sem markdown.\n\n"
        "O QUE VOCÊ NUNCA FAZ (regra da clínica, sem exceção):\n"
        "- indicar tratamento ou procedimento, falar de sintoma, dizer se algo é grave, interpretar "
        "exame, foto ou resultado, recomendar remédio, creme ou cuidado;\n"
        "- dizer se o convênio cobre, dar desconto ou condição especial;\n"
        "- prometer resultado.\n"
        "Nesses casos devolva acao=repassar com o motivo: a recepção assume, e o texto que o "
        "paciente recebe é da clínica, não seu. Se o paciente só CITA o motivo da consulta (\"tenho "
        "acne, quero marcar\"), NÃO repasse e não comente o motivo: siga oferecendo o horário.\n\n"
        f"Agora são {loc:%H:%M} de {ca._DIA_LONGO[loc.isoweekday()]}, {loc:%d/%m/%Y}, em Brasília.\n\n"
        f"INSTRUÇÕES DA CLÍNICA:\n{instr or '(nenhuma)'}\n\n"
        f"PERGUNTAS FREQUENTES:\n{faqs or '(nenhuma)'}\n\n"
        f"ATENDIMENTOS E PREÇOS:\n{cat_txt}")
    pedir = (
        f"Conversa com {nome}:\n{historico}{gemeo_nota}\n\n{bloco_agenda(c, conta_id, menu, ja)}\n\n"
        "Responda a última mensagem do paciente. Retorne APENAS JSON:\n"
        '{"acao":"responder|consulta|repassar","resposta":"texto pro paciente",'
        '"consulta":{"codigo":"","nome":""},'
        '"repasse":{"motivo":"sintoma|desconto|convenio|urgencia|remarcar|marcar|pessoa"},'
        '"temperatura":"frio|morno|quente"}\n'
        f"- Para marcar, ofereça no máximo {OFERECE} horários da lista (de dias diferentes quando "
        "der), escritos como estão lá, com o nome do profissional e o lugar. Quando o paciente "
        "ESCOLHER um deles, devolva acao=consulta com consulta.codigo = o código exato desse "
        "horário e consulta.nome = o nome completo de quem vai ser atendido, se ele disse (a mãe "
        "pode marcar pro filho). Se pedir outro dia ou hora, ofereça os mais próximos da lista.\n"
        "- Nunca diga \"marquei\" nem confirme horário: quem confirma é o sistema, depois de gravar.\n"
        "- Quem já tem consulta marcada (lista acima) não ganha outra igual. Para remarcar ou "
        "desmarcar, acao=repassar com motivo remarcar.\n"
        "- Se ele pedir pra falar com uma pessoa, acao=repassar com motivo pessoa.\n"
        "- Sempre preencha resposta com um texto útil.")
    return system, pedir


# ------------------------------------------------------------------ respostas fixas

def texto_opcoes(slots: list[dict], abertura: str) -> str:
    """"<abertura> Tenho: • qua 30/09 às 08:00 com Dr. Manoel, no Espaço Pelle ... Qual fica melhor?"
    Um horário por dia primeiro (dá escolha de dia), até OFERECE."""
    vistos, escolha = set(), []
    for s in slots:
        d = ca.local(s["inicio"]).date()
        if d not in vistos:
            vistos.add(d)
            escolha.append(s)
        if len(escolha) >= OFERECE:
            break
    for s in slots:
        if len(escolha) >= OFERECE:
            break
        if s not in escolha:
            escolha.append(s)
    if not escolha:
        return ""
    itens = "\n".join(f"• {s['quando']} com {s['prof']}{', ' + s['onde'] if s['onde'] else ''}"
                      for s in escolha)
    return f"{abertura}\n{itens}\nQual fica melhor?"


def resposta_da_vespera(c, conta_id: int, conversa_id: int, lead: int | None, fone: str | None,
                        texto: str | None, agora: datetime) -> str | None:
    """O paciente respondeu "1" ou "2" ao lembrete (da véspera ou mandado na mão).

    Só vale se o lembrete foi a ÚLTIMA coisa que a clínica disse na conversa: "sim"
    pra uma pergunta do agente não é confirmação de consulta. Grava na hora (a mesma
    `ler_respostas` do poller, que é idempotente) e responde sem chamar a IA — se a
    mensagem for só a resposta. Com mais coisa junto, grava e deixa a IA responder."""
    t = (texto or "").strip()
    sim, rem = bool(ca._RE_SIM.search(t)), bool(ca._RE_REMARCAR.search(t))
    if not (sim or rem):
        return None
    dig = ca._digitos(fone)
    ultima_nossa = c.execute(
        """select max(m.criado_em) from mensagens m join conversas cv on cv.id = m.conversa_id
            where m.conversa_id=%s and cv.conta_id=%s and m.direcao='out'""",
        (conversa_id, conta_id)).fetchone()[0]
    r = c.execute(
        r"""select id from eventos_agenda
             where conta_id=%s and situacao in ('agendado','confirmado') and status='ativo'
               and confirmacao_enviada_em is not null and inicio > %s
               and confirmacao_enviada_em >= coalesce(%s, confirmacao_enviada_em) - interval '2 minutes'
               and (prospeccao_id = %s
                    or (length(%s) >= 8 and right(regexp_replace(coalesce(paciente_fone,''), '\D', '', 'g'), 8) = %s))
             order by inicio limit 1""",
        (conta_id, agora, ultima_nossa, lead, dig, dig[-8:])).fetchone()
    if not r:
        return None
    ca.ler_respostas(c, conta_id, agora)
    if len(t) > 30:
        # "sim, mas quanto é o botox?": a confirmação já está gravada, e a pergunta é
        # da IA (ela vê a consulta como confirmada na lista)
        return None
    if sim:
        return "Obrigado! Está confirmado ✅ Até lá 😊"
    return "Tudo bem! A nossa recepção vai te chamar por aqui para remarcar 😊"


# ------------------------------------------------------------------ passar pra recepção

def _recepcao(c, conta_id: int, lead: int | None) -> list[int]:
    """O vendedor do card (a recepção entra como vendedor); sem ele, quem atende a
    conta: vendedores, gestores e o dono. Clínica é equipe pequena, e o recado que
    cai no vazio é o paciente sem resposta."""
    if lead:
        r = c.execute("select vendedor_id from prospeccao where id=%s and conta_id=%s",
                      (lead, conta_id)).fetchone()
        if r and r[0]:
            return [r[0]]
    return [x[0] for x in c.execute(
        """select id from membros where conta_id=%s and coalesce(ativo, true)
              and papel in ('dono','gestor','vendedor') order by id limit 8""", (conta_id,)).fetchall()]


def _aviso(pool, conta_id: int, membros: list[int], titulo: str, corpo: str, url: str) -> None:
    try:
        from finance import agente_visita as av
        av.avisar(pool, conta_id, membros, titulo, corpo, url)
    except Exception:  # noqa: BLE001 — aviso é bônus; o atendimento não cai por ele
        _log.info("agente da clínica: aviso não saiu (conta %s)", conta_id, exc_info=True)


def _aberto(c, conta_id: int, conversa_id: int) -> tuple | None:
    """O repasse desta conversa que ainda espera a recepção: criado nas últimas 24h,
    sem Resolvido e sem nenhuma mensagem de gente depois dele."""
    try:
        with c.transaction():
            return c.execute(
                """select r.id, r.motivo, r.criado_em from clinica_repasses r
                    where r.conta_id=%s and r.conversa_id=%s and r.resolvido_em is null
                      and r.criado_em > now() - interval '24 hours'
                      and not exists (select 1 from mensagens m
                                       where m.conversa_id = r.conversa_id and m.direcao = 'out'
                                         and m.autor <> 'bot' and m.criado_em > r.criado_em)
                    order by r.id desc limit 1""", (conta_id, conversa_id)).fetchone()
    except Exception:  # noqa: BLE001 — migração 363 ainda não rodou
        return None


def repassar(pool, c, conta_id: int, conversa_id: int, lead: int | None, motivo: str,
             nome: str, enviar) -> str | None:
    """Passa pra recepção: grava o item da tela Hoje, manda o texto fixo e avisa.
    Devolve o texto enviado (ou None, quando o mesmo recado já está esperando).

    O mesmo assunto não se repete: com um repasse aberto, o agente fica quieto nas
    mensagens que também seriam repasse — a recepção já foi chamada. A urgência é a
    exceção: ela sempre fala, e sobe o item aberto pra urgência."""
    motivo = motivo if motivo in MOTIVOS else "pessoa"
    aberto = _aberto(c, conta_id, conversa_id)
    if aberto and not (motivo == "urgencia" and aberto[1] != "urgencia"):
        return None
    try:
        with c.transaction():
            if aberto:
                c.execute("update clinica_repasses set motivo='urgencia' where id=%s and conta_id=%s",
                          (aberto[0], conta_id))
            else:
                c.execute("""insert into clinica_repasses (conta_id, conversa_id, prospeccao_id, motivo)
                             values (%s,%s,%s,%s)""", (conta_id, conversa_id, lead, motivo))
    except Exception:  # noqa: BLE001 — sem a 363 o paciente ainda recebe a resposta
        _log.warning("agente da clínica: repasse não gravado (conversa %s)", conversa_id, exc_info=True)
    texto = MOTIVOS[motivo][1]
    enviar(texto)
    membros = _recepcao(c, conta_id, lead)
    quem = nome or "Paciente"
    if motivo == "urgencia":
        _aviso(pool, conta_id, membros, "🚨 Urgência no WhatsApp",
               f"{quem} escreveu algo que parece urgente. Responda agora.", "/painel/hoje")
    else:
        _aviso(pool, conta_id, membros, "🙋 O agente passou pra você",
               f"{quem}: {MOTIVOS[motivo][0]}.", "/painel/hoje")
    return texto


def repasses_abertos(c, conta_id: int, limite: int = 30) -> list[dict]:
    """O que a tela Hoje mostra em "O agente passou pra você": urgência primeiro, depois
    do mais antigo. Some quando alguém responde a conversa ou aperta Resolvido."""
    try:
        with c.transaction():
            rows = c.execute(
                """select r.id, r.conversa_id, r.prospeccao_id, r.motivo, r.criado_em,
                          coalesce(nullif(p.contato,''), nullif(cv.contato_nome,''), p.empresa, '')
                     from clinica_repasses r
                     join conversas cv on cv.id = r.conversa_id and cv.conta_id = r.conta_id
                     left join prospeccao p on p.id = r.prospeccao_id and p.conta_id = r.conta_id
                    where r.conta_id=%s and r.resolvido_em is null
                      and r.criado_em > now() - interval '7 days'
                      and not exists (select 1 from mensagens m
                                       where m.conversa_id = r.conversa_id and m.direcao = 'out'
                                         and m.autor <> 'bot' and m.criado_em > r.criado_em)
                    order by (r.motivo = 'urgencia') desc, r.criado_em limit %s""",
                (conta_id, limite)).fetchall()
    except Exception:  # noqa: BLE001 — migração 363 ainda não rodou
        return []
    return [{"id": r[0], "conversa_id": r[1], "lead": r[2], "motivo": r[3],
             "rotulo": MOTIVOS.get(r[3], ("", ""))[0], "urgente": r[3] == "urgencia",
             "quando": f"{ca.dia_txt(r[4])} às {ca.hora_txt(r[4])}", "nome": r[5] or "Sem nome"}
            for r in rows]


def resolver(c, conta_id: int, repasse_id: int, membro_id: int | None) -> bool:
    r = c.execute("""update clinica_repasses set resolvido_em=now(), resolvido_por=%s
                      where id=%s and conta_id=%s and resolvido_em is null returning id""",
                  (membro_id, repasse_id, conta_id)).fetchone()
    return r is not None


# ------------------------------------------------------------------ marcar

def _origem(c, conta_id: int, lead: int | None) -> str:
    """"Como conheceu", quando o card sabe (anúncio do Instagram, Google)."""
    if not lead:
        return ""
    r = c.execute("select coalesce(origem,'') from prospeccao where id=%s and conta_id=%s",
                  (lead, conta_id)).fetchone()
    o = _sem_acento(r[0] if r else "")
    if "insta" in o:
        return "Instagram"
    if "google" in o:
        return "Google"
    return ""


def _nome_do_contato(c, conta_id: int, conversa_id: int, lead: int | None) -> str:
    r = c.execute(
        """select coalesce(nullif(p.contato,''), nullif(cv.contato_nome,''), p.empresa, '')
             from conversas cv left join prospeccao p on p.id = cv.prospeccao_id and p.conta_id = cv.conta_id
            where cv.id=%s and cv.conta_id=%s""", (conversa_id, conta_id)).fetchone()
    return (r[0] if r else "") or ""


def marcar(pool, c, conta_id: int, conversa_id: int, lead: int | None, fone: str, cod: str,
           nome_dito: str, menu: dict, agora: datetime, enviar) -> dict:
    """Marca o horário do código. Devolve {"ok", "texto", "evento_id"}.

    A IA escolhe; o CÓDIGO confere tudo de novo: o atendimento tem que estar entre os
    que o agente marca pra este contato, o profissional tem que fazer esse
    atendimento, e `clinica_agenda.agendar` reconfere o horário livre com a trava da
    agenda — quem marcou no balcão um segundo antes ganha."""
    from finance.voltar_a_chamar import primeiro_nome

    def sem_horario():
        return repassar(pool, c, conta_id, conversa_id, lead, "marcar",
                        _nome_do_contato(c, conta_id, conversa_id, lead), enviar)
    alvo = ler_codigo(cod)
    tipos_ok = {t["id"]: t for t in menu["tipos"]}
    if not alvo or alvo[1] not in tipos_ok:
        abertura = ("Esse horário não está disponível 😕 Tenho estes:" if (cod or "").strip()
                    else "Tenho estes horários:")
        return _nao_deu(menu, None, abertura, enviar, sem_horario=sem_horario)
    prof_id, tipo_id, inicio = alvo
    if inicio < agora + ANTECEDENCIA:
        return _nao_deu(menu, tipo_id, "Esse horário já não dá mais 😕 Tenho estes:", enviar,
                        sem_horario=sem_horario)
    nome = (nome_dito or "").strip()
    if not primeiro_nome(nome):
        nome = _nome_do_contato(c, conta_id, conversa_id, lead)
    if not primeiro_nome(nome):
        texto = "Pra eu marcar, me diz o nome completo de quem vai ser atendido? 😊"
        enviar(texto)
        return {"ok": False, "texto": texto}
    # a mesma pessoa, o mesmo atendimento, já marcado: não marca de novo (a IA às
    # vezes devolve o código outra vez na mensagem seguinte, "obrigada!")
    for ev in marcadas(c, conta_id, lead, fone, agora):
        if ev["servico_id"] == tipo_id and _sem_acento(ev["paciente"]) == _sem_acento(nome):
            texto = (f"{ca._genero(ca._palavra(ev))[0]} {ca._palavra(ev)} já está marcad"
                     f"{ca._genero(ca._palavra(ev))[1]} para {ca.dia_txt(ev['inicio'])} às {ev['hora']} "
                     f"com {ca._prof_nome(c, conta_id, ev)}{ca._onde(c, conta_id, ev)} 😊")
            enviar(texto)
            return {"ok": False, "texto": texto, "evento_id": ev["id"]}
    try:
        with c.transaction():
            eid, erro = ca.agendar(c, conta_id, profissional_id=prof_id, servico_id=tipo_id, inicio=inicio,
                                   lead_id=lead, paciente=nome, fone=fone, origem=_origem(c, conta_id, lead),
                                   marcado_por="ia", agora=agora)
            if erro:
                raise _NaoMarcou(erro)
    except _NaoMarcou as e:
        _log.info("agente da clínica: não marcou (%s)", e)
        return _nao_deu(menu, tipo_id, "Esse horário acabou de ser ocupado 😕 Tenho estes:", enviar,
                        tirar=cod, sem_horario=sem_horario)
    ev = ca.evento(c, conta_id, eid)
    promete = ca.config(c, conta_id)["confirmacao_modo"] == "ligado"
    texto = ca.texto_marcado(c, conta_id, ev, promete)
    enviar(texto)
    _aviso(pool, conta_id, _recepcao(c, conta_id, ev.get("lead") or lead), "🗓️ O agente marcou",
           f"{ev['paciente']} · {ca._palavra(ev)} · {ca.dia_txt(ev['inicio'])} às {ev['hora']}",
           f"/painel/clinica/agenda/evento/{eid}")
    return {"ok": True, "texto": texto, "evento_id": eid}


class _NaoMarcou(Exception):
    pass


def _nao_deu(menu: dict, tipo_id: int | None, abertura: str, enviar, tirar: str = "",
             sem_horario=None) -> dict:
    """Não marcou: oferece outros horários do mesmo atendimento. Sem nenhum, passa
    pra recepção marcar (`sem_horario`), que é melhor que prometer e sumir."""
    slots = [s for s in menu["slots"] if (tipo_id is None or s["tipo_id"] == tipo_id) and s["codigo"] != tirar]
    texto = texto_opcoes(slots, abertura)
    if not texto:
        return {"ok": False, "texto": sem_horario() if sem_horario else None}
    enviar(texto)
    return {"ok": False, "texto": texto}


# ------------------------------------------------------------------ a volta inteira

def atender(pool, c, conta_id: int, conversa_id: int, cfg: dict, conv, msgs, *, historico: str,
            gemeo_nota: str, instr: str, faqs: str, cat_txt: str, canal: str, destino: str,
            enviar, agora: datetime | None = None) -> None:
    """Uma mensagem do paciente, uma resposta (ou nenhuma, se o recado já está com a
    recepção). `enviar(texto)` é o `agente._enviar` já amarrado à conversa: manda
    pelo mesmo chip e grava na conversa (e faz o commit)."""
    agora = agora or datetime.now(timezone.utc)
    lead = conv[1]
    fone = conv[4] or conv[5] or (destino if canal == "whatsapp" else "") or ""
    nome = _nome_do_contato(c, conta_id, conversa_id, lead) or conv[3] or conv[2] or "Paciente"
    ultima = next((t for (_d, a, t) in msgs if a == "lead"), "") or ""

    if not cfg["pode_responder"]:
        return
    # 1) o "1"/"2" do lembrete: resposta pronta, sem IA
    ack = resposta_da_vespera(c, conta_id, conversa_id, lead, fone, ultima, agora)
    if ack:
        enviar(ack)
        return
    # 2) mídia: o agente não vê nem ouve — passa, sem gastar IA pra fingir que viu
    m = midia(ultima)
    if m:
        repassar(pool, c, conta_id, conversa_id, lead, m, nome, enviar)
        return
    # 3) urgência pela regra, antes da IA
    if urgente(ultima):
        repassar(pool, c, conta_id, conversa_id, lead, "urgencia", nome, enviar)
        return

    menu = cardapio(c, conta_id, lead, agora)
    ja = marcadas(c, conta_id, lead, fone, agora)
    system, pedir = prompt(c, conta_id, cfg, nome=nome, historico=historico, gemeo_nota=gemeo_nota,
                           instr=instr, faqs=faqs, cat_txt=cat_txt, menu=menu, ja=ja, agora=agora)
    from core.brain import Brain
    from finance import agente as ag
    resp = Brain().chamar(system=system, mensagens=[{"role": "user", "content": pedir}])
    txt = "".join(getattr(b, "text", "") for b in resp.content
                  if getattr(b, "type", None) == "text").strip()
    d = ag._extrair_json(txt)
    if not isinstance(d, dict):
        d = {}
    acao = d.get("acao") if d.get("acao") in ACOES else "responder"
    resposta = str(d.get("resposta") or "").strip()

    if cfg.get("pode_qualificar") and lead and d.get("temperatura") in ("frio", "morno", "quente"):
        c.execute("update prospeccao set temperatura=%s, atualizado_em=now() where id=%s and conta_id=%s",
                  (d["temperatura"], lead, conta_id))

    # a IA às vezes devolve o objeto como texto ("consulta": "3-7-..."): o formato
    # errado não pode derrubar a volta, senão o paciente fica sem resposta nenhuma
    rep = d.get("repasse") if isinstance(d.get("repasse"), dict) else {"motivo": d.get("repasse")}
    cons = d.get("consulta") if isinstance(d.get("consulta"), dict) else {"codigo": d.get("consulta")}
    if acao == "repassar":
        motivo = str(rep.get("motivo") or "pessoa")
        repassar(pool, c, conta_id, conversa_id, lead, motivo, nome, enviar)
        return
    if acao == "consulta":
        marcar(pool, c, conta_id, conversa_id, lead, fone, str(cons.get("codigo") or ""),
               str(cons.get("nome") or ""), menu, agora, enviar)
        return
    # responder — com duas redes, porque instrução a IA às vezes ignora
    if parece_conselho(resposta):
        repassar(pool, c, conta_id, conversa_id, lead, "sintoma", nome, enviar)
        return
    if diz_que_marcou(resposta):
        # disse "marquei" sem marcar: o paciente não pode sair achando que tem horário
        _nao_deu(menu, None, "Pra eu marcar, escolha um destes horários:", enviar,
                 sem_horario=lambda: repassar(pool, c, conta_id, conversa_id, lead, "marcar", nome, enviar))
        return
    enviar(resposta or "Oi! Me conta como posso te ajudar 😊")
