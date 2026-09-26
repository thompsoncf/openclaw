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

import json
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
#: e o anexo vira um marcador (o espelho do wa-qr, services/wa-qr/server.js MIDIA_DE)
#: e o tipo em `mensagens.midia_tipo`. Foto COM legenda chega só com a legenda, sem
#: marcador: por isso o tipo da coluna manda, e o marcador é a segunda porta.
_MIDIA = (("📷", "foto"), ("🎬", "foto"), ("🎥", "foto"), ("🖼", "foto"), ("🎤", "audio"),
          ("🎵", "audio"), ("📄", "arquivo"), ("📎", "arquivo"))
_MIDIA_TIPO = {"imagem": "foto", "video": "foto", "documento": "arquivo", "audio": "audio"}

#: rede de segurança da urgência, sem depender da IA, no português do WhatsApp (sem
#: acento, "n consigo", "ta saindo sangue"). "preciso de um horário urgente" não é
#: emergência (a IA oferece o primeiro horário), e "exame de sangue" também não.
_PARTE = r"(rosto|boca|labio\w*|lingua|olho\w*|garganta|cara|pescoco)"
_RE_URGENTE = re.compile(
    r"\b(emergencia|sangr\w*|(?<!exame de )sangue|hemorrag\w*|falta de ar|sem ar|"
    r"(nao|n|nem) (to |tou |estou |esta |ta )?(consig\w*|conseguindo) respirar|"
    r"dificuldade (pra|para|de) respirar|desmai\w*|anafila\w*|convuls\w*|samu|"
    r"garganta (\w+ )?fechand\w*|reacao alergica|"
    rf"inch\w* .{{0,25}}{_PARTE}|{_PARTE} .{{0,25}}inch\w*)\b")

#: conselho de saúde na resposta da IA, apesar da instrução: não sai; vira repasse.
#: Estreita de propósito: "recomendo chegar 10 minutos antes", "você pode usar o
#: estacionamento" e "a aplicação custa R$ 1.500" são recepção, não conselho — só
#: pega remédio, creme e cuidado (no infinitivo ou no imperativo), tratamento
#: indicado e a IA opinando sobre gravidade, causa ou o que é normal sentir.
_PRODUTO = (r"(creme|pomada|remedio|medicamento|protetor|hidratante|sabonete|acido|vitamina|"
            r"antibiotic\w*|antialergic\w*|corticoide|anti.?inflamat\w*|compressa|gelo)")
_CAUSA = r"(alergia|infeccao|micose|fungo|cancer|melanoma|dermatite|herpes|acne|reacao|inflamacao)"
_RE_CONSELHO = re.compile(
    rf"\b((us[ae]r?|pass[ae]r?|tom[ae]r?|evit[ae]r?|lav[ae]r?|coloc[ae]r?|ponh[ae])"
    rf"( (um|uma|o|a|esse|essa|este|esta|bastante))? {_PRODUTO}"
    rf"|(recomendo|aconselho|indico|sugiro)( (o|a|um|uma))? (tratamento|procedimento|{_PRODUTO})"
    r"|antibiotic\w*|antialergic\w*|corticoide|nao (e|parece) (nada )?grave|parece (ser )?grave"
    rf"|pode ser (um |uma )?{_CAUSA}|parece (ser )?(um |uma )?{_CAUSA}"
    r"|(e|eh) (normal|comum) (ficar|ter|arder|cocar|inchar|descascar|doer|sair|sentir|avermelhar))\b")

#: o que a clínica não deixa a IA prometer (seção 07): cobertura de convênio,
#: desconto, gratuidade, resultado. A IA pode ter lido "a moça disse que a Unimed
#: cobre" do próprio paciente — pedido de confirmação é justamente o ataque.
_RE_CONVENIO = re.compile(r"\b(convenio|plano de saude|unimed|hapvida|bradesco saude|sulamerica|amil|"
                          r"cassi|geap|ipasgo|cobertura|cobre)\b")
_RE_DESCONTO = re.compile(r"\b(desconto|\d+ ?% ?(off|de desconto)?|gratis|gratuit\w*|de graca|"
                          r"sem custo|cortesia|resultado garantido|garant\w* (o )?resultado)\b")

#: a IA dizendo que marcou sem ter devolvido acao=consulta: quem marca é o código.
_RE_MARQUEI = re.compile(r"\b(marquei|agendei|reservei|deixei marcad\w*|ja marquei)\b")


def _sem_acento(txt: str | None) -> str:
    return "".join(ch for ch in unicodedata.normalize("NFD", txt or "")
                   if unicodedata.category(ch) != "Mn").lower()


def midia(texto: str | None, tipo: str | None = None) -> str | None:
    """O motivo do repasse quando a mensagem é mídia (foto, vídeo, documento, áudio).

    ÁUDIO TRANSCRITO NÃO É MÍDIA: o wa-qr grava "🎤 Áudio (0:23)" e a transcrição
    na linha de baixo, e aí o agente lê como texto. Só o áudio sem transcrição passa
    pra recepção. Figurinha vai pra IA (é um "ok", não uma pergunta)."""
    t = (texto or "").strip()
    motivo = _MIDIA_TIPO.get((tipo or "").strip())
    if motivo is None:
        motivo = next((m for pre, m in _MIDIA if t.startswith(pre)), None)
    if motivo == "audio" and "\n" in t and t.split("\n", 1)[1].strip():
        return None
    return motivo


def falou_de(texto: str | None) -> str | None:
    """Convênio ou desconto na resposta da IA: vira repasse com texto fixo."""
    t = _sem_acento(texto)
    if _RE_CONVENIO.search(t):
        return "convenio"
    if _RE_DESCONTO.search(t):
        return "desconto"
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

def _atendidos(c, conta_id: int, lead: int | None) -> set[str]:
    """O primeiro nome de quem deste card já foi atendido aqui (consulta finalizada).

    Retorno e sessão de pacote só pra quem já foi atendido: sem isso, qualquer um que
    dissesse "é retorno" marcava de graça no lugar da consulta. E é pela PESSOA, não
    pelo card: a mãe atendida não libera retorno pro filho que nunca veio."""
    if not lead:
        return set()
    from finance.voltar_a_chamar import primeiro_nome
    return {_sem_acento(primeiro_nome(r[0])) for r in c.execute(
        """select coalesce(paciente_nome, '') from eventos_agenda
            where conta_id=%s and prospeccao_id=%s and situacao='finalizado'""",
        (conta_id, lead)).fetchall()} - {""}


def _ja_e_paciente(c, conta_id: int, lead: int | None) -> bool:
    return bool(_atendidos(c, conta_id, lead))


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
    # o mesmo horário do mesmo profissional aparece uma vez só, mesmo quando ele serve
    # pra dois atendimentos (consulta e retorno às 08:00 são a mesma linha pro paciente)
    unicos, chaves = [], set()
    for s in slots:
        k = (s["inicio"], s["prof_id"])
        if k not in chaves:
            chaves.add(k)
            unicos.append(s)
    vistos, escolha = set(), []
    for s in unicos:
        d = ca.local(s["inicio"]).date()
        if d not in vistos:
            vistos.add(d)
            escolha.append(s)
        if len(escolha) >= OFERECE:
            break
    for s in unicos:
        if len(escolha) >= OFERECE:
            break
        if s not in escolha:
            escolha.append(s)
    if not escolha:
        return ""
    itens = "\n".join(f"• {s['quando']} com {s['prof']}{', ' + s['onde'] if s['onde'] else ''}"
                      for s in escolha)
    return f"{abertura}\n{itens}\nQual fica melhor?"


def texto_ja_marcada(c, conta_id: int, ev: dict) -> str:
    palavra = ca._palavra(ev)
    art, fim = ca._genero(palavra)
    return (f"{art} {palavra} já está marcad{fim} para {ca.dia_txt(ev['inicio'])} às {ev['hora']} "
            f"com {ca._prof_nome(c, conta_id, ev)}{ca._onde(c, conta_id, ev)} 😊")


def resposta_da_vespera(c, conta_id: int, conversa_id: int, lead: int | None, fone: str | None,
                        texto: str | None, agora: datetime) -> str | None:
    """O paciente respondeu "1" ou "2" ao lembrete (da véspera ou mandado na mão).

    Só vale se o lembrete foi a ÚLTIMA coisa que a clínica disse na conversa ("sim"
    pra uma pergunta do agente não é confirmação de consulta) e só pra consulta que
    ainda espera a resposta (o mesmo critério de `ler_respostas`). Grava na hora com
    a própria `ler_respostas` (idempotente) e RESPONDE PELO QUE FOI GRAVADO, não pelo
    texto: se nada mudou, devolve None e a IA atende. Mensagem com mais coisa junto
    ("sim, e o estacionamento?") grava e também fica com a IA.

    Quem chama roda a urgência ANTES disto: "não consigo respirar" começa igual a um
    "não consigo ir" e não pode virar "vamos remarcar"."""
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
             where conta_id=%s and situacao='agendado' and status='ativo' and pede_remarcar_em is null
               and confirmacao_enviada_em is not null and inicio > %s
               and confirmacao_enviada_em >= coalesce(%s, confirmacao_enviada_em) - interval '2 minutes'
               and (prospeccao_id = %s
                    or (length(%s) >= 8 and right(regexp_replace(coalesce(paciente_fone,''), '\D', '', 'g'), 8) = %s))
             order by inicio limit 1""",
        (conta_id, agora, ultima_nossa, lead, dig, dig[-8:])).fetchone()
    if not r:
        return None
    ca.ler_respostas(c, conta_id, agora)
    depois = c.execute("select situacao, pede_remarcar_em from eventos_agenda where id=%s and conta_id=%s",
                       (r[0], conta_id)).fetchone()
    if len(t) > 30 or not depois:
        return None
    if depois[0] == "confirmado":
        return "Obrigado! Está confirmado ✅ Até lá 😊"
    if depois[1] is not None:
        return "Tudo bem! A nossa recepção vai te chamar por aqui para remarcar 😊"
    return None


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

    O MESMO ASSUNTO NÃO SE REPETE: com um repasse aberto do mesmo motivo (a segunda
    foto do álbum, o segundo áudio), o agente fica quieto — a recepção já foi chamada
    e o paciente já leu o recado. Assunto NOVO fala (a foto e depois "é grave?"), no
    mesmo item da tela Hoje, sem outro aviso. A urgência sempre fala da primeira vez
    e sobe o item pra urgência, com aviso."""
    motivo = motivo if motivo in MOTIVOS else "pessoa"
    aberto = _aberto(c, conta_id, conversa_id)
    if aberto and aberto[1] == motivo:
        return None
    novo = aberto is None or (motivo == "urgencia" and aberto[1] != "urgencia")
    try:
        with c.transaction():
            if aberto is None:
                c.execute("""insert into clinica_repasses (conta_id, conversa_id, prospeccao_id, motivo)
                             values (%s,%s,%s,%s)""", (conta_id, conversa_id, lead, motivo))
            elif aberto[1] != "urgencia":
                # o item mostra o assunto mais recente; urgência não desce
                c.execute("update clinica_repasses set motivo=%s where id=%s and conta_id=%s",
                          (motivo, aberto[0], conta_id))
    except Exception:  # noqa: BLE001 — sem a 363 o paciente ainda recebe a resposta
        _log.warning("agente da clínica: repasse não gravado (conversa %s)", conversa_id, exc_info=True)
    texto = MOTIVOS[motivo][1]
    enviar(texto)
    if not novo:
        return texto
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


#: o nome que o webhook põe no card de quem não tem nome no WhatsApp
#: (web/painel_prospeccao.NOME_PROVISORIO): não é nome de gente.
_NOMES_PROVISORIOS = {"contato whatsapp", "contato", "cliente", "paciente", "sem nome"}


def _nome_de_gente(nome: str | None) -> str:
    """O nome, se parecer nome de pessoa; senão "" (e aí o agente pergunta)."""
    from finance.voltar_a_chamar import primeiro_nome
    n = (nome or "").strip()
    if not primeiro_nome(n) or _sem_acento(n) in _NOMES_PROVISORIOS:
        return ""
    return n


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
    ja = marcadas(c, conta_id, lead, fone, agora)
    nome_dito = _nome_de_gente(nome_dito)
    # O HORÁRIO DO CÓDIGO JÁ É DESTE CONTATO: não marca de novo, nem diz que "acabou
    # de ser ocupado" (por ele mesmo). É o "obrigada!" depois do Prontinho, e é a mãe
    # que marcou no nome dela e agora diz "é pro meu filho Pedro" — aí o nome muda
    # na mesma consulta (o protótipo marca primeiro e pergunta o nome depois).
    if alvo:
        mesma = next((ev for ev in ja if ev["profissional_id"] == alvo[0] and ev["inicio"] == alvo[2]), None)
        if mesma:
            if (nome_dito and _sem_acento(nome_dito) != _sem_acento(mesma["paciente"])
                    and _corrigir_nome(c, conta_id, mesma, nome_dito)):
                mesma = ca.evento(c, conta_id, mesma["id"])
                c.commit()
                texto = ca.texto_marcado(c, conta_id, mesma, ca.config(c, conta_id)["confirmacao_modo"] == "ligado")
            else:
                texto = texto_ja_marcada(c, conta_id, mesma)
            enviar(texto)
            return {"ok": False, "texto": texto, "evento_id": mesma["id"]}
    if not alvo or alvo[1] not in tipos_ok:
        abertura = ("Esse horário não está disponível 😕 Tenho estes:" if (cod or "").strip()
                    else "Tenho estes horários:")
        return _nao_deu(menu, None, abertura, enviar, sem_horario=sem_horario)
    prof_id, tipo_id, inicio = alvo
    if inicio < agora + ANTECEDENCIA:
        return _nao_deu(menu, tipo_id, "Esse horário já não dá mais 😕 Tenho estes:", enviar,
                        sem_horario=sem_horario)
    nome = nome_dito or _nome_de_gente(_nome_do_contato(c, conta_id, conversa_id, lead))
    if not nome:
        texto = "Pra eu marcar, me diz o nome completo de quem vai ser atendido? 😊"
        enviar(texto)
        return {"ok": False, "texto": texto}
    # retorno e sessão: só pra PESSOA já atendida (a mãe atendida não libera o filho)
    if tipos_ok[tipo_id]["categoria"] in ("retorno", "sessao") and \
            _sem_acento(primeiro_nome(nome)) not in _atendidos(c, conta_id, lead):
        return {"ok": False, "texto": sem_horario()}
    # a mesma pessoa e o mesmo atendimento já marcados em outro horário
    for ev in ja:
        if ev["servico_id"] == tipo_id and _sem_acento(ev["paciente"]) == _sem_acento(nome):
            texto = texto_ja_marcada(c, conta_id, ev)
            enviar(texto)
            return {"ok": False, "texto": texto, "evento_id": ev["id"]}
    try:
        with c.transaction():
            eid, erro = ca.agendar(c, conta_id, profissional_id=prof_id, servico_id=tipo_id, inicio=inicio,
                                   lead_id=lead, nome=nome, paciente=nome, fone=fone,
                                   origem=_origem(c, conta_id, lead), marcado_por="ia", agora=agora)
            if erro:
                raise _NaoMarcou(erro)
    except _NaoMarcou as e:
        _log.info("agente da clínica: não marcou (%s)", e)
        if "não está livre" not in str(e) and "já passou" not in str(e):
            # erro de dado (profissional, atendimento, contato): quem resolve é gente
            return {"ok": False, "texto": sem_horario()}
        return _nao_deu(menu, tipo_id, "Esse horário acabou de ser ocupado 😕 Tenho estes:", enviar,
                        tirar=cod, sem_horario=sem_horario)
    # COMMITA A MARCAÇÃO ANTES DE MANDAR: solta a trava da agenda do profissional (a
    # recepção marcando no balcão não espera a ida e volta do WhatsApp) e a consulta
    # existe mesmo que o envio falhe — a recepção recebe o aviso de qualquer jeito.
    c.commit()
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


def _corrigir_nome(c, conta_id: int, ev: dict, nome: str) -> bool:
    """Troca o nome do paciente numa consulta que O AGENTE marcou nas últimas 2 horas
    (a mãe marcou no nome dela e disse depois que é pro filho). Consulta da recepção,
    ou antiga, não muda por mensagem: aí é remarcar, com gente."""
    palavra = ca._palavra(ev)
    r = c.execute("""update eventos_agenda set paciente_nome=%s, titulo=%s
                      where id=%s and conta_id=%s and marcado_por='ia'
                        and situacao='agendado' and situacao_em > now() - interval '2 hours'
                      returning id""",
                  (nome[:120], f"{nome} · {palavra}"[:200], ev["id"], conta_id)).fetchone()
    return r is not None


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
    # UMA VOLTA POR CONVERSA DE CADA VEZ: três fotos do álbum são três webhooks ao
    # mesmo tempo, e sem isto as três mandavam o mesmo recado (e o "segunda 8h" e o
    # "meu nome é Ana" logo depois disputavam a mesma marcação). Solta no commit do
    # primeiro envio; a volta seguinte já enxerga o que a anterior gravou.
    _travar(c, conversa_id)
    ultima, tipo_midia = _ultima_do_paciente(c, conta_id, conversa_id, msgs)

    # 1) urgência pela regra, ANTES de tudo: nem o "não consigo respirar" que começa
    #    igual a um "não consigo ir", nem uma foto, passam na frente dela
    if urgente(ultima):
        repassar(pool, c, conta_id, conversa_id, lead, "urgencia", nome, enviar)
        return
    if not cfg["pode_responder"]:
        return
    # 2) mídia: o agente não vê — passa, sem gastar IA pra fingir que viu
    m = midia(ultima, tipo_midia)
    if m:
        repassar(pool, c, conta_id, conversa_id, lead, m, nome, enviar)
        return
    # 3) o "1"/"2" do lembrete: resposta pronta pelo que foi gravado, sem IA
    ack = resposta_da_vespera(c, conta_id, conversa_id, lead, fone, ultima, agora)
    if ack:
        enviar(ack)
        return

    # sem card ou sem celular (DM do Instagram) não dá pra marcar: a agenda precisa
    # de um contato pra confirmar a véspera. A IA vê "sem horário" e passa pra recepção.
    if lead and len(ca._digitos(fone)) >= 10:
        menu = cardapio(c, conta_id, lead, agora)
    else:
        menu = {"tipos": [], "slots": [], "paciente": False}
    ja = marcadas(c, conta_id, lead, fone, agora)
    system, pedir = prompt(c, conta_id, cfg, nome=nome, historico=historico, gemeo_nota=gemeo_nota,
                           instr=instr, faqs=faqs, cat_txt=cat_txt, menu=menu, ja=ja, agora=agora)
    from core.brain import Brain
    resp = Brain().chamar(system=system, mensagens=[{"role": "user", "content": pedir}])
    txt = "".join(getattr(b, "text", "") for b in resp.content
                  if getattr(b, "type", None) == "text").strip()
    d = _ler_json(txt)
    if d is None:
        # a IA não devolveu JSON: o paciente não pode ficar sem resposta nenhuma
        repassar(pool, c, conta_id, conversa_id, lead, "pessoa", nome, enviar)
        return
    acao = d.get("acao") if d.get("acao") in ACOES else "responder"
    resposta = d.get("resposta") if isinstance(d.get("resposta"), str) else ""
    resposta = resposta.strip()

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
    # responder — com redes, porque instrução a IA às vezes ignora (e o paciente pode
    # ter pedido pra ela ignorar: "a moça disse que a Unimed cobre, só confirma")
    if parece_conselho(resposta):
        repassar(pool, c, conta_id, conversa_id, lead, "sintoma", nome, enviar)
        return
    assunto = falou_de(resposta)
    if assunto:
        repassar(pool, c, conta_id, conversa_id, lead, assunto, nome, enviar)
        return
    if diz_que_marcou(resposta):
        # disse "marquei" sem marcar: o paciente não pode sair achando que tem horário
        if ja:
            enviar(texto_ja_marcada(c, conta_id, ja[0]))
            return
        _nao_deu(menu, None, "Pra eu marcar, escolha um destes horários:", enviar,
                 sem_horario=lambda: repassar(pool, c, conta_id, conversa_id, lead, "marcar", nome, enviar))
        return
    enviar(resposta or "Oi! Me conta como posso te ajudar 😊")


_LOCK_CONVERSA = 771163   # vizinho das travas da agenda (771161/771162)


def _travar(c, conversa_id: int) -> None:
    c.execute("select pg_advisory_xact_lock(%s::int, %s::int)",
              (_LOCK_CONVERSA, int(conversa_id) % 2147483647))


def _ultima_do_paciente(c, conta_id: int, conversa_id: int, msgs) -> tuple[str, str]:
    """(texto, midia_tipo) da última mensagem do paciente. O tipo vem da coluna (foto
    com legenda não tem marcador no texto); tolerante a banco sem a coluna."""
    texto = next((t for (_d, a, t) in msgs if a == "lead"), "") or ""
    for coluna in ("coalesce(m.midia_tipo, '')", "''"):
        try:
            with c.transaction():
                r = c.execute(f"""select m.texto, {coluna} from mensagens m
                                   join conversas cv on cv.id = m.conversa_id
                                  where m.conversa_id=%s and cv.conta_id=%s and m.autor='lead'
                                  order by m.criado_em desc, m.id desc limit 1""",
                              (conversa_id, conta_id)).fetchone()
            break
        except Exception:  # noqa: BLE001 — banco sem midia_tipo: lê só o texto
            r = None
    if r:
        return r[0] or "", r[1] or ""
    return texto, ""


def _ler_json(txt: str) -> dict | None:
    """O JSON da IA, mesmo com texto em volta ("Claro! {...}") ou cercado de ```."""
    from finance import agente as ag
    try:
        d = ag._extrair_json(txt)
    except Exception:  # noqa: BLE001
        ini, fim = (txt or "").find("{"), (txt or "").rfind("}")
        if ini < 0 or fim <= ini:
            return None
        try:
            d = json.loads(txt[ini:fim + 1])
        except Exception:  # noqa: BLE001
            return None
    return d if isinstance(d, dict) else None


def so_urgencia(pool, c, conta_id: int, conversa_id: int, lead: int | None, enviar) -> bool:
    """Fora do horário do agente (ou com "responder dúvidas" desligado) ele fica
    quieto — MENOS na urgência: a orientação de pronto-socorro/192 e o item vermelho
    na tela Hoje não esperam o expediente. Só a regra, sem IA."""
    _travar(c, conversa_id)
    ultima, _tipo = _ultima_do_paciente(c, conta_id, conversa_id, [])
    if not urgente(ultima):
        return False
    repassar(pool, c, conta_id, conversa_id, lead, "urgencia",
             _nome_do_contato(c, conta_id, conversa_id, lead), enviar)
    return True
