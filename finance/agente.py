"""Motor do Agente IA de atendimento (Fase D).

Ao chegar uma mensagem do lead, lê a config + base de conhecimento + catálogo +
histórico e decide: responder, gerar orçamento prévio, ou escalar pro humano
(handoff). Respeita confiança/horário/máx-trocas. Tudo best-effort: qualquer
falha → não responde (seguro), nunca estoura pro webhook.

Reusa core.brain.Brain (mesma IA do "sugerir escopo"), servicos_catalogo e
whatsapp_twilio. Só WhatsApp por enquanto (único canal ao vivo).
"""
from __future__ import annotations

import json
import logging
import re
import secrets
from datetime import datetime, timedelta, timezone

from finance import agente_visita as _av
from finance import raio_x_perfil as _rxp
from finance import servicos_catalogo as scat

_log = logging.getLogger("agente")


def _cfg(c, conta_id):
    r = c.execute(
        """select ativo, limiar_confianca, horario, tom, max_trocas, escalar_para,
                  pode_responder, pode_qualificar, pode_agendar, pode_orcamento,
                  orcamento_proativo, agendar_modo
             from agente_config where conta_id=%s""", (conta_id,)).fetchone()
    if not r:
        return None
    ks = ["ativo", "limiar", "horario", "tom", "max_trocas", "escalar_para",
          "pode_responder", "pode_qualificar", "pode_agendar", "pode_orcamento", "proativo",
          # o modo da visita (migração 259): 'off' | 'propoe' | 'marca'. O
          # `pode_agendar` acima continua sendo lido e continua não mandando em
          # nada — está aqui só pra não quebrar quem grava por ele.
          "agendar_modo"]
    return dict(zip(ks, r))


def _horario_ok(cfg) -> bool:
    if cfg["horario"] != "comercial":
        return True
    agora = datetime.now(timezone.utc) - timedelta(hours=3)   # Brasil (UTC-3)
    return agora.weekday() <= 5 and 8 <= agora.hour < 18       # seg–sáb, 8–18h


def _pode_falar_agora(cfg) -> bool:
    """O relógio deixa o agente responder agora?

    MODO TESTE tem passe livre: com o agente-mestre DESLIGADO, quem chegou até aqui
    veio de uma conversa que um humano ligou à mão, uma por vez, no botão do chat. É
    o caminho que o painel oferece pra experimentar o agente antes de soltá-lo na
    caixa inteira — e esse teste quase sempre acontece fora do expediente (à noite,
    no fim de semana). Um agente mudo justamente na hora do teste se parece com um
    agente quebrado, e foi assim que ele pareceu.

    Com o mestre LIGADO, o horário da empresa vale pra todo mundo: aí não é teste, é
    atendimento, e a conta decidiu quando quer atender."""
    return (not cfg["ativo"]) or _horario_ok(cfg)


def _reais(centavos: int) -> str:
    """R$ 8.800 — sem centavos, com ponto de milhar. É texto que vai pro cliente."""
    return "R$ " + f"{(centavos or 0) // 100:,}".replace(",", ".")


def _precos_escondidos(c, conta_id: int, *, todos: bool = False) -> set[str] | None:
    """Slugs de atendimento da CLÍNICA cujo preço o agente não pode dizer
    (Configurar › Atendimentos, "o agente pode dizer este preço" desmarcado).

    Só a linha criada pelo cadastro da clínica (tem `duracao_min`) entra: as linhas
    das outras contas nasceram com a coluna em false e continuam como sempre foram.
    Tolerante: banco sem as colunas (348) não esconde nada.

    `todos` é o modo da REGRA POR NÚMERO (migração 388): ali a chave vale pra TODO
    item, não só pro da clínica — decisão do dono em 26/09, "a IA respeita o que eu
    liberar no catálogo". E o lado seguro se inverte: sem a coluna, nada foi
    liberado, então a volta é None ("esconda todos")."""
    try:
        with c.transaction():
            return {r[0] for r in c.execute(
                """select slug from servicos_catalogo
                    where conta_id=%s and ativo and not agente_diz_preco"""
                + ("" if todos else " and duracao_min is not null"), (conta_id,)).fetchall()}
    except Exception:  # noqa: BLE001
        return None if todos else set()


def _linha_catalogo(s, preco_escondido: bool = False, *, aproximado: bool = False,
                    eventos: bool = True) -> str:
    """Uma linha do catálogo do jeito que a IA deve LER — e, por tabela, falar.

    O catálogo nasceu pra serviço recorrente (setup + mensalidade), e a linha era
    sempre "setup R$X, mensal R$Y". Isso quebra em dois casos reais:

    1. LOCAÇÃO. A Prime Eventos tem 26 itens com mensal ZERO — pacote de espaço,
       DJ, hora extra, taxa de limpeza. A linha antiga mandava "mensal R$0" pra IA,
       e "mensal R$ 0" numa conversa de aluguel de salão não quer dizer nada; na
       melhor das hipóteses confunde, na pior o cliente entende que tem uma
       mensalidade zerada que vai virar cobrança depois.

    2. PREÇO A COMBINAR. Serviço cadastrado com os dois valores em zero (é o caso
       de uma assessoria de consórcio na base) virava "setup R$0, mensal R$0" — a
       IA lendo isso pode dizer ao cliente que é DE GRAÇA. Zero aqui nunca
       significa gratuito, significa que ninguém preencheu.

    Então: mostra só o que tem valor, e o que não tem vira "valor sob consulta",
    que é a verdade e ainda deixa a IA saber que precisa perguntar."""
    setup = s.get("setup_centavos") or 0
    mensal = s.get("mensal_centavos") or 0
    if preco_escondido and aproximado:
        # a regra por número: o dono ainda não liberou este valor pra IA
        passo = "convide pra visita" if eventos else "convide pra uma reunião"
        return (f"- {s['nome']} (slug {s['slug']}): valor NÃO liberado — não diga nem "
                f"estime; {passo} e diga que a equipe manda o orçamento conferido")
    if preco_escondido:
        return (f"- {s['nome']} (slug {s['slug']}): valor sob consulta "
                "(a recepção informa — não diga nem estime o preço)")
    if setup and mensal:
        preco = f"{_reais(setup)} de entrada + {_reais(mensal)} por mês"
    elif setup:
        preco = _reais(setup)
    elif mensal:
        preco = f"{_reais(mensal)} por mês"
    else:
        preco = "valor sob consulta (não cadastrado — pergunte, não invente)"
    if aproximado and (setup or mensal):
        # até a IA estar treinada, o preço dela é de referência (decisão 15 do dono)
        preco = f"a partir de {preco} (valor de referência)"
    return f"- {s['nome']} (slug {s['slug']}): {preco}"


# O campo `escopo` do orçamento é impresso na folha da proposta sob o título
# "Condições" — texto de documento, que o cliente lê depois de assinar. O agente
# gravava ali a RESPOSTA DE CHAT que tinha acabado de mandar, então a proposta saía
# com "Que festa incrível vai ser essa! 🎉🥂", emoji de bolo e "quer que eu chame um
# consultor?" no lugar das condições comerciais. Condição de documento é isto:
_CONDICOES = ("Valores de referência para a data, o horário e o número de convidados "
              "informados pelo cliente. Sujeito à confirmação de disponibilidade da data.")


def _linha_orcamento(it) -> str:
    """Uma linha do orçamento que vai pro WhatsApp do cliente.

    Mesma regra do _linha_catalogo (zero é campo em branco, nunca desconto), mais a
    QUANTIDADE — que faltava e mudava o preço. O cliente pediu 6 horas de festa e a
    linha da hora extra saía "R$ 620", como se fosse uma só; o número que ele leu não
    era o que ele pediu."""
    q = max(1, int(it.get("qtd") or 1))
    us = int(it.get("setup_centavos") or 0)
    um = int(it.get("mensal_centavos") or 0)
    nome = it.get("nome") or ""
    rot = f"{nome} ({q}×)" if q > 1 else nome
    if not us and not um:
        return f"• {rot}: valor sob consulta"
    if us and um:
        txt = f"{_reais(us * q)} de entrada + {_reais(um * q)} por mês"
    elif us:
        txt = _reais(us * q)
    else:
        txt = f"{_reais(um * q)} por mês"
    if q > 1:
        txt += f" ({_reais(us or um)} cada)"
    return f"• {rot}: {txt}"


def _bloco_orcamento(itens, link: str) -> str:
    """O orçamento prévio inteiro, do jeito que chega no WhatsApp.

    Nasceu de um orçamento real que saiu assim pra um cliente:

        • PACOTE ESSENCIAL - SEGUNDA A QUINTA - 2027: R$ 0/mês
        • DJ: R$ 0/mês
        Setup: R$ 11920
        Total mensal: R$ 0

    Três erros numa tela só. O "R$ 0/mês" é o mesmo zero que o _linha_catalogo já
    tinha aprendido a não falar — só que este texto era montado à parte e ficou pra
    trás. O "R$ 11920" é dinheiro sem ponto de milhar. E "Total mensal: R$ 0" num
    aluguel de salão não quer dizer nada: não existe mensalidade aqui.

    Função pura, e de propósito: é o texto que chega no cliente."""
    linhas = [_linha_orcamento(i) for i in itens]
    setup = sum(int(i.get("setup_centavos") or 0) * max(1, int(i.get("qtd") or 1)) for i in itens)
    mensal = sum(int(i.get("mensal_centavos") or 0) * max(1, int(i.get("qtd") or 1)) for i in itens)
    if setup and mensal:
        total = f"Entrada: {_reais(setup)}\nMensal: {_reais(mensal)}"
    elif setup:
        total = f"Total: {_reais(setup)}"
    elif mensal:
        total = f"Total: {_reais(mensal)} por mês"
    else:
        # nenhum item tem preço cadastrado: melhor não fingir um total do que somar zeros
        total = "Os valores desses itens ainda não estão cadastrados — já confirmo com a equipe."
    return ("📄 Montei um orçamento prévio pra você:\n" + "\n".join(linhas) + "\n" + total
            + f"\n\nVer a proposta: {link}\n\nÉ um valor de referência, sem compromisso.")


def _itens_escolhidos(d, slugs_ok) -> list[dict]:
    """Os itens que a IA escolheu, validados contra o catálogo e com quantidade.

    Aceita as duas formas em `servicos`: o slug solto (como sempre foi) ou
    {"slug": ..., "qtd": N}. Slug que não existe no catálogo é descartado sem dó — é
    o que impede a IA de inventar item, e por tabela inventar preço. Slug repetido
    entra uma vez só: a IA às vezes lista o mesmo pacote duas vezes e o orçamento
    saía com o valor dobrado."""
    fora, vistos = [], set()
    for x in (d.get("servicos") or []):
        slug = (x.get("slug") if isinstance(x, dict) else x) or ""
        s = slugs_ok.get(slug)
        if not s or slug in vistos:
            continue
        vistos.add(slug)
        try:
            q = int(x.get("qtd") or 1) if isinstance(x, dict) else 1
        except (TypeError, ValueError):
            q = 1
        fora.append({**s, "qtd": max(1, min(q, 999))})
    return fora


# O convite que o agente faz ANTES de montar qualquer orçamento. É frase fixa de
# propósito: é ela que o código procura no histórico pra saber se já ofereceu (ver
# _ja_ofereceu_orcamento). Se a IA reescrevesse o convite a cada vez, a trava
# dependeria de a IA se comportar — e o ponto da trava é justamente não depender.
_CONVITE_ORCAMENTO = "Quer que eu monte um orçamento com isso?"

# O que o agente PRECISA saber antes de pôr preço num documento. Sem isto ele cotava
# no escuro: numa conversa real respondeu "quanto custa pra 150 pessoas?" montando um
# orçamento sem data nenhuma — e, sem data, juntou o pacote de segunda-a-quinta COM o
# de sexta-a-domingo e somou os dois, R$ 13.360 por um salão que custa 5.760 ou 7.200.
# São alternativas; a data é que decide qual. Ordem = ordem em que se pergunta.
_PRECISA_SABER = (("data", "a data do evento"),
                  ("inicio", "o horário de início"),
                  ("convidados", "quantos convidados"))


def _falta_pro_orcamento(evento) -> list[str]:
    """O que ainda falta perguntar antes de poder orçar."""
    return [rotulo for chave, rotulo in _PRECISA_SABER if not (evento or {}).get(chave)]


def _ja_ofereceu_orcamento(msgs) -> bool:
    """O agente já perguntou se o cliente QUER um orçamento?

    `msgs` são as linhas (direcao, autor, texto) do histórico recente."""
    return any(autor == "bot" and _CONVITE_ORCAMENTO in (texto or "")
               for (_d, autor, texto) in (msgs or []))


def _lista_br(itens) -> str:
    """"a data do evento, o horário de início e quantos convidados"."""
    itens = list(itens)
    if len(itens) <= 1:
        return itens[0] if itens else ""
    return ", ".join(itens[:-1]) + " e " + itens[-1]


def _texto_perguntando(resposta: str, falta) -> str:
    """Responde o que dá pra responder e pede o que falta — em vez de orçar no escuro."""
    return ((resposta + "\n\n" if resposta else "")
            + f"Pra montar um orçamento certinho eu preciso saber {_lista_br(falta)}. "
              "Me conta? 😊")


def _texto_oferecendo(resposta: str) -> str:
    """O passo que faltava: perguntar antes de mandar o documento.

    O agente montava orçamento em qualquer pergunta de preço. Quem só queria uma ideia
    de valor recebia proposta formal com link — e cada uma dessas vira lixo no funil."""
    return (resposta + "\n\n" if resposta else "") + _CONVITE_ORCAMENTO


def _evento_do_json(d) -> dict:
    """Data, convidados, horário e tipo — o que o CLIENTE disse, guardado no orçamento.

    A folha da proposta tem um bloco "O evento" (data, convidados, início,
    encerramento) e ele saía todo vazio nos orçamentos do agente, porque o agente
    nunca gravava nada ali. O cliente dizia "31/12, casamento, 21h, 50 convidados" e
    recebia um papel que não repetia nenhuma dessas quatro coisas."""
    ev = d.get("evento") if isinstance(d.get("evento"), dict) else {}
    out = {}
    for k in ("data", "inicio", "fim", "tipo", "local"):
        v = str(ev.get(k) or "").strip()[:60]
        if v:
            out[k] = v
    try:
        n = int(str(ev.get("convidados") or "").strip() or 0)
        if n > 0:
            out["convidados"] = n
    except (TypeError, ValueError):
        pass
    return out


def _conhecimento(c, conta_id):
    rows = c.execute(
        "select tipo, pergunta, resposta from agente_conhecimento where conta_id=%s order by ordem, id",
        (conta_id,)).fetchall()
    instr, faqs = "", []
    for (tipo, perg, resp) in rows:
        if tipo == "instrucoes":
            instr = resp or ""
        elif perg and resp:
            faqs.append(f"P: {perg}\nR: {resp}")
    return instr, "\n\n".join(faqs)


def _add_bot_msg(c, conversa_id, canal, texto, sid=None, status=None):
    """Grava o que o agente mandou.

    O ECO CHEGA ANTES, às vezes. No QR, a mensagem que o agente manda volta pelo
    webhook de saída (`_wa_saida_conversa`) como se fosse do celular — e se o eco
    ganha a corrida, a linha já existe com autor 'humano', o insert daqui batia no
    único (conversa_id, provider_sid) e a transação caía. Agora a linha é do bot
    de qualquer jeito: sem isto a regra por número leria a resposta da própria IA
    como "alguém respondeu pelo celular" e pausaria a IA na primeira mensagem."""
    if status:
        # só o recado de fora do horário da regra leva marca (chip_regra.STATUS_FORA)
        c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto,
                                            provider_sid, status)
                     values (%s,%s,'out','bot',%s,%s,%s)
                     on conflict (conversa_id, provider_sid) where provider_sid is not null
                     do update set autor='bot', status=excluded.status""",
                  (conversa_id, canal, (texto or "")[:8000], sid or None, status))
    else:
        c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, texto, provider_sid)
                     values (%s,%s,'out','bot',%s,%s)
                     on conflict (conversa_id, provider_sid) where provider_sid is not null
                     do update set autor='bot'""",
                  (conversa_id, canal, (texto or "")[:8000], sid or None))
    c.execute("update conversas set ultima_msg_em=now() where id=%s", (conversa_id,))


def _mandar(c, conta_id, canal, destino, texto, conversa_id=None) -> dict:
    """Envia pelo canal certo: WhatsApp (Twilio/Cloud API) ou Messenger/Instagram (Meta).

    `conversa_id` serve pra sair pelo MESMO chip que recebeu, quando a empresa tem
    mais de um. Sem ele o agente responderia pelo chip principal — e o lead que
    escreveu pro segundo número receberia resposta de um número que ele não conhece.
    """
    if canal in ("messenger", "instagram"):
        from finance import meta_msg
        r = c.execute("select token from canais_config where conta_id=%s and canal=%s and ativo",
                      (conta_id, canal)).fetchone()
        return meta_msg.enviar(r[0] if r else None, destino, texto, canal)
    from finance import whatsapp_out
    return whatsapp_out.enviar(
        c, conta_id, destino, texto,
        chip_id=whatsapp_out.chip_da_conversa(c, conta_id, conversa_id))


def _extrair_json(txt: str) -> dict:
    txt = re.sub(r"^```json|^```|```$", "", (txt or "").strip(), flags=re.M).strip()
    return json.loads(txt)


def atender(pool, conta_id: int, conversa_id: int) -> None:
    """Decide e age numa conversa recém-recebida. Nunca estoura (best-effort)."""
    try:
        # a clínica roda de novo quando chegou mensagem do paciente durante a volta
        # (a outra volta achou a conversa travada e saiu: ver clinica_agente.tentar_travar)
        for _ in range(3):
            if not _atender(pool, conta_id, conversa_id):
                break
    except Exception as e:  # noqa: BLE001
        _log.info("agente.atender falhou conta=%s conversa=%s: %s: %s",
                  conta_id, conversa_id, type(e).__name__, e)
        _socorro_da_regra(pool, conta_id, conversa_id, f"{type(e).__name__}")


def _socorro_da_regra(pool, conta_id: int, conversa_id: int, porque: str) -> None:
    """A IA DA REGRA NÃO CONSEGUIU RESPONDER: chama gente.

    Na regra por número o lead nasce da IA — ninguém da equipe recebe o "lead novo
    pra você" nem o "Retornar contato". Se a IA cai (a IA de fora fora do ar, JSON
    torto, conta sem config), sem isto o cliente ficaria sem resposta e ninguém
    saberia. Vai como 'pessoa' pra quem cuida da agenda; o `avisar` não repete o
    mesmo assunto em 6h. Nunca levanta."""
    try:
        from finance import chip_regra as _cr
        with pool.connection() as c:
            r = _cr.regra_da_conversa(c, conta_id, conversa_id)
            if not (r and r.get("ia_ligada")):
                return
            p = c.execute("""select cv.prospeccao_id, coalesce(p.empresa, cv.contato_ref, '')
                               from conversas cv left join prospeccao p
                                    on p.id = cv.prospeccao_id and p.conta_id = cv.conta_id
                              where cv.id=%s and cv.conta_id=%s""",
                          (conversa_id, conta_id)).fetchone()
            c.commit()
        _cr.avisar(pool, conta_id, r, "pessoa", prospeccao_id=p[0] if p else None,
                   conversa_id=conversa_id, lead=p[1] if p else "",
                   resumo=f"A IA não conseguiu responder ({porque}). Responda o cliente por ela.")
    except Exception:  # noqa: BLE001
        _log.warning("agente: socorro da regra falhou conta=%s conversa=%s",
                     conta_id, conversa_id, exc_info=True)


def _atender(pool, conta_id, conversa_id):
    with pool.connection() as c:
        cfg = _cfg(c, conta_id)
        if not cfg:
            _socorro_da_regra(pool, conta_id, conversa_id, "a empresa não tem o agente configurado")
            return
        conv = c.execute(
            """select cv.agente_ativo, cv.prospeccao_id, cv.contato_ref, p.empresa,
                      p.whatsapp, p.telefone, p.segmento, p.cidade, p.uf, cv.canal
                 from conversas cv left join prospeccao p on p.id = cv.prospeccao_id
                where cv.id=%s and cv.conta_id=%s""", (conversa_id, conta_id)).fetchone()
        if not conv or not conv[0]:      # conversa não existe, humano assumiu, ou desligada
            return
        _slug_n = c.execute("""select coalesce(n.slug,'') from contas ct
                                 left join nichos n on n.id = ct.nicho_id
                                where ct.id=%s""", (conta_id,)).fetchone()
        _perfil = _rxp.perfil_por_nicho(_slug_n[0] if _slug_n else "")
        _visto = 0
        # A REGRA POR NÚMERO (migração 388, finance/chip_regra.py): a conversa é de um
        # lead que caiu no dono da regra e a IA da regra está ligada. Muda o jeito de
        # atender (ver `_regra_antes`), não o motor. A clínica tem o agente dela.
        regra = None
        if _perfil != "clinica":
            from finance import chip_regra as _cr
            regra = _cr.regra_da_conversa(c, conta_id, conversa_id)
            if not (regra and regra.get("ia_ligada")):
                regra = None
        if regra:
            from finance import clinica_agente as _cla
            _canal = conv[9] or "whatsapp"
            _dest = conv[2] if _canal in ("messenger", "instagram") else (conv[4] or conv[5] or conv[2])
            segue = _regra_antes(c, conta_id, conversa_id, regra,
                                 lambda texto, status=None: _enviar(c, conta_id, conversa_id,
                                                                   _canal, _dest, texto, status))
            if not segue:
                return False
            _visto = _cla.ultimo_do_paciente(c, conta_id, conversa_id)
        if _perfil == "clinica":
            # UMA VOLTA POR CONVERSA, travada ANTES de ler o histórico: a volta que
            # espera não pode mandar pra IA uma conversa sem a resposta da anterior
            from finance import clinica_agente as _cla
            if not _cla.tentar_travar(c, conversa_id):
                return False
            _visto = _cla.ultimo_do_paciente(c, conta_id, conversa_id)
        if not regra and not _pode_falar_agora(cfg):
            if _perfil == "clinica":
                # fora do horário o agente da clínica fica quieto, MENOS na urgência
                # (pronto-socorro/192 e o item vermelho na tela Hoje não esperam)
                _canal = conv[9] or "whatsapp"
                _dest = conv[2] if _canal in ("messenger", "instagram") else (conv[4] or conv[5] or conv[2])
                _cla.so_urgencia(pool, c, conta_id, conversa_id, conv[1],
                                 lambda texto: _enviar(c, conta_id, conversa_id, _canal, _dest, texto))
                c.commit()
                return _cla.ultimo_do_paciente(c, conta_id, conversa_id) > _visto
            return
        # histórico das últimas mensagens (contexto pro Brain)
        msgs = c.execute(
            """select direcao, autor, texto from mensagens
                where conversa_id=%s order by criado_em desc limit 12""", (conversa_id,)).fetchall()
        catalogo = scat.listar(pool, conta_id)
        instr, faqs = _conhecimento(c, conta_id)
        lead_empresa = conv[3] or conv[2] or "cliente"
        # canal + destino: WhatsApp usa nº do lead; Messenger/Instagram usam o PSID/IGSID (contato_ref)
        canal = conv[9] or "whatsapp"
        destino = conv[2] if canal in ("messenger", "instagram") else (conv[4] or conv[5] or conv[2])

        historico = "\n".join(
            ("Cliente: " if a == "lead" else ("Agente: " if a == "bot" else "Vendedor: ")) + (t or "")
            for (_d, a, t) in reversed(msgs))

        escondidos = _precos_escondidos(c, conta_id, todos=bool(regra))
        cat_txt = "\n".join(
            _linha_catalogo(s, escondidos is None or s["slug"] in escondidos,
                            aproximado=bool(regra), eventos=_perfil == "eventos")
            for s in catalogo) \
            or "(sem catálogo)"
        # A CLÍNICA TEM O AGENTE DELA (fase 3b, finance/clinica_agente.py): preço,
        # horário livre de verdade, marcar e passar pra recepção. Desvia AQUI, antes
        # de tudo que é de festa (visita ao espaço, orçamento, evento no lead); o
        # caminho das outras contas segue daqui pra baixo sem mudar nada.
        if _perfil == "clinica":
            _cla.atender(pool, c, conta_id, conversa_id, cfg, conv, msgs, historico=historico,
                         gemeo_nota=_nota_gemeo(c, conta_id, conv), instr=instr, faqs=faqs,
                         cat_txt=cat_txt, canal=canal, destino=destino,
                         enviar=lambda texto: _enviar(c, conta_id, conversa_id, canal, destino, texto))
            c.commit()      # o silêncio (recado já com a recepção) também grava a temperatura
            return _cla.ultimo_do_paciente(c, conta_id, conversa_id) > _visto
        # A VISITA (migração 259). O bloco só entra quando a conta ligou a chave E
        # estamos na janela comercial — fora dela quem resolve é gente, e instruir a
        # IA sobre visita que ela não pode marcar é convidá-la a prometer horário.
        visita_txt, visita_livres = "", []
        _agora = _av.ag.agora_brt()
        if regra:
            # etapa 1 da regra: a IA ainda não marca — pega a preferência do cliente
            # e chama quem cuida da agenda (a etapa 2 é a IA marcando sozinha)
            visita_txt = _regra_prompt(regra, msgs, _perfil)
        elif _av.pode_agora(cfg, _agora):
            visita_livres = _av.sugestoes(pool, conta_id, _agora, quantas=2)
            if visita_livres:
                _op = " ou ".join(d.strftime("%d/%m às %H:%M") for d in visita_livres)
                visita_txt = (
                    "\n\nVISITA AO ESPAÇO: a empresa recebe visita, e você pode combinar. "
                    f"Horários LIVRES na agenda: {_op}. Ofereça só esses — nunca invente "
                    "outro horário nem confirme um que o cliente propuser sem estar na "
                    "lista (diga que confere com a equipe). Quando ele ACEITAR um deles, "
                    "devolva acao=visita com visita.data e visita.hora exatamente do "
                    "horário aceito.")
        elif _av.modo(cfg) != "off":
            # a chave está ligada, mas estamos fora da janela (ou não há horário
            # livre nos próximos 14 dias). Nos dois casos a IA não pode combinar
            # nada — e sem esta linha ela inventaria um horário pra agradar.
            visita_txt = ("\n\nVISITA AO ESPAÇO: a empresa recebe visita, mas você NÃO pode "
                          "marcar agora. Se o cliente pedir, diga que vai passar pra equipe "
                          "confirmar o horário — nunca ofereça nem confirme dia e hora.")
        # O NICHO MUDA O QUE UM DOCUMENTO SIGNIFICA (regra 6). O agente só enxerga
        # TEXTO: um anexo chega pra ele como a palavra "Documento", e sem instrução
        # ele responde em cima dela como se fosse uma frase do cliente. Numa
        # corretora isso é pior que inútil — o PDF quase sempre é apólice, proposta
        # ou endosso, e o pior desfecho possível é a IA falar de cobertura, prêmio
        # ou vencimento de um papel que ela NÃO leu.
        #
        # O leitor automático (migração 304) é quem lê o documento de verdade, e
        # ele não conversa: deixa o pré-cadastro pronto pra corretora conferir.
        seguros_txt = ""
        if _perfil == "seguros":
            seguros_txt = (
                "\n\nESTA EMPRESA É UMA CORRETORA DE SEGUROS. Nunca fale de festa, "
                "evento, convidados ou data de festa — esse vocabulário não existe aqui.\n"
                "DOCUMENTO ANEXADO: quando a mensagem do cliente for só '📄 Documento' "
                "(ou parecida), ele MANDOU UM ARQUIVO e você NÃO consegue lê-lo. "
                "Confirme que chegou, diga que a equipe vai conferir e retornar, e siga "
                "a conversa. NUNCA diga que leu, nem cite seguradora, cobertura, prêmio, "
                "franquia, placa ou data de vencimento a partir de um anexo — nada disso "
                "está na sua frente, e errar esse número custa a renovação do cliente.\n"
                "RENOVAÇÃO: se o cliente perguntar quando vence a apólice dele, não "
                "arrisque a data. Diga que vai confirmar com a equipe.")
        tom = "informal e próximo" if cfg["tom"] == "informal" else "formal e profissional"
        system = (
            "Você é o atendente virtual da empresa, no WhatsApp. Fala em português do "
            f"Brasil, tom {tom}, mensagens curtas. Use SÓ o que está na base abaixo — "
            "NUNCA invente preço, prazo ou promessa (se não tiver a info na base, diga "
            "que vai confirmar e siga a conversa). Seu objetivo é atender, tirar dúvidas "
            "e qualificar sozinho — NÃO ofereça 'chamar um consultor' a não ser que o "
            "CLIENTE peça explicitamente falar com uma pessoa. "
            "Responda SEMPRE só com JSON válido, sem markdown.\n\n"
            # O nome do item do catálogo costuma CARREGAR a condição (o ano, o dia da
            # semana, a faixa). Um agente que ignora isso cotou o pacote de 2027 pra
            # uma festa de 31/12/2026 e ainda escreveu "2026" ao lado do preço de
            # 2027 — R$ 7.800 no lugar de R$ 5.760, com o nome do ano certo.
            f"Hoje é {datetime.now(timezone.utc).date().isoformat()}. O nome de cada "
            "item do catálogo diz a que ele se aplica (ano, dia da semana, faixa). "
            "Escolha o item cujo nome bate com o que o cliente pediu — se a festa é "
            "em 2026, o item tem que ser o de 2026. Se não existir item pro que ele "
            "pediu (uma data especial, um ano que não está no catálogo), NÃO use o "
            "parecido: diga que vai confirmar o valor dessa data com a equipe. "
            # ela juntou "SEGUNDA A QUINTA" com "SEXTA A DOMINGO" no mesmo orçamento e
            # somou os dois — R$ 13.360 por um salão que custa 5.760 OU 7.200
            "Itens que são ALTERNATIVAS entre si (dias diferentes, anos diferentes do "
            "mesmo pacote) nunca entram juntos: escolha um. "
            # "dá pra atender bem os seus 150 convidados" — capacidade não está em
            # lugar nenhum da base; foi invenção
            "E capacidade (quantas pessoas cabem) só se estiver escrita na base: se "
            "não estiver, diga que confirma com a equipe.\n\n"
            f"INSTRUÇÕES DA EMPRESA:\n{instr or '(nenhuma)'}\n\n"
            f"PERGUNTAS FREQUENTES:\n{faqs or '(nenhuma)'}\n\n"
            f"CATÁLOGO DE SERVIÇOS:\n{cat_txt}")
        gemeo_nota = _nota_gemeo(c, conta_id, conv)

        pedir = (
            f"Conversa com {lead_empresa}:\n{historico}{gemeo_nota}{visita_txt}{seguros_txt}\n\n"
            "Responda a última mensagem do cliente. Retorne APENAS JSON:\n"
            + ('{"acao":"responder","resposta":"texto pra mandar ao cliente",' if regra else
               '{"acao":"responder|orcamento|visita","resposta":"texto pra mandar ao cliente",')
            + '"visita":{"data":"AAAA-MM-DD","hora":"HH:MM"},'
            '"servicos":[{"slug":"...","qtd":1}],"temperatura":"frio|morno|quente",'
            '"evento":{"data":"AAAA-MM-DD","convidados":0,"inicio":"","fim":"","tipo":""}'
            + (',"avisar_equipe":{"motivo":"","resumo":""}' if regra else "") + '}\n'
            # na regra (etapa 1) a IA não manda orçamento: sem o convite a montar um
            + ("" if regra else
               "- acao=orcamento só quando o cliente ACEITOU receber um orçamento (você "
               "ofereceu antes e ele disse que sim) E você já sabe a data, o horário de "
               "início e quantos convidados. Pergunta de preço não é pedido de orçamento: "
               "responda o valor e ofereça montar. Liste em servicos os slugs do catálogo.\n")
            + "- qtd é a QUANTIDADE que o cliente pediu, não 1 por padrão: 6 horas de "
            "festa num pacote de 4 são 2 horas extras, qtd=2. O preço que ele vai ler "
            "é qtd × valor do item.\n"
            "- evento: repita o que o CLIENTE disse (data, quantos convidados, que "
            "horas começa e termina, que tipo de festa). Deixe em branco o que ele não "
            "disse — não complete com número redondo nem com o padrão da casa.\n"
            "- Sempre preencha resposta com um texto útil pra mandar ao cliente.")

        from core.brain import Brain
        resp = Brain().chamar(system=system, mensagens=[{"role": "user", "content": pedir}])
        txt = "".join(getattr(b, "text", "") for b in resp.content
                      if getattr(b, "type", None) == "text").strip()
        d = _extrair_json(txt)
        acao = d.get("acao") if d.get("acao") in ("responder", "orcamento", "visita") else "responder"
        resposta = (d.get("resposta") or "").strip()
        if regra:
            # etapa 1: nem orçamento formal nem visita marcada pela IA — o que ela
            # precisa de gente vai pelo aviso, e a conversa segue com ela
            acao = "responder"
            _regra_avisar(pool, c, conta_id, conversa_id, conv, regra, d, lead_empresa)

        # qualificação: atualiza a temperatura do lead (se ligado e veio no JSON)
        if cfg["pode_qualificar"] and conv[1] and d.get("temperatura") in ("frio", "morno", "quente"):
            c.execute("update prospeccao set temperatura=%s, atualizado_em=now() where id=%s and conta_id=%s",
                      (d["temperatura"], conv[1], conta_id))

        # O EVENTO NO LEAD (migração 197). O que o cliente disse de data, tipo e
        # convidados vai pro lead NA HORA — e não só pro orçamento, que nem sempre
        # nasce: na Prime, 224 leads em "Contatado" e a data da festa conhecida em
        # 15, com o agente perguntando a data em toda conversa de preço. Só preenche
        # o que está vazio: o que o vendedor pôs à mão fica.
        if conv[1]:
            from finance import evento_lead as _evl
            _evl.gravar(c, conta_id, conv[1], _evento_do_json(d), so_vazios=True, origem="agente")

        # o agente fica SEMPRE ativo e segue o painel: NUNCA se desliga sozinho (nem por
        # confiança, nem por trocas, nem por 'achar' que precisa de humano). Quem assume
        # é um humano — botão "Assumir" ou responder pelo chat. Se o dono desligou o
        # "responder dúvidas" no painel, o agente fica quieto (mas continua ativo).
        # na regra, quem manda é a chave "a IA atende" dela: o dono ligou ali, e uma
        # IA muda por causa de outra chave, em outra tela, pareceria quebrada
        if not cfg["pode_responder"] and not regra:
            return

        # ---------------------------------------------------------------- a visita
        # FORA DA JANELA a IA não marca nem propõe: chama o vendedor dono do lead
        # (ou a gestão). Decisão do dono em 14/09/2026 — e é a metade que impede
        # "só em horário comercial" de virar "o pedido morreu no domingo".
        # Só quando a conta ligou a chave: conta em 'off' não tem visita nenhuma
        # pra avisar, e o vendedor não recebe push de função que não existe.
        _ult_cliente = next((t for (_dd, a, t) in msgs if a == "lead"), "")
        if (not regra and _av.modo(cfg) != "off" and conv[1]
                and not _av.na_janela(_agora) and _av.pediu_visita(_ult_cliente)):
            _av.fora_de_hora(pool, conta_id, conv[1], lead_empresa)

        if acao == "visita" and _av.pode_agora(cfg, _agora) and conv[1]:
            # O HORÁRIO TEM QUE SER UM DOS QUE OFERECEMOS. A lista saiu da agenda
            # segundos atrás, já sem conflito; aceitar outro seria deixar a IA
            # marcar em cima de festa — no nicho eventos, vender a mesma data
            # duas vezes. Se não bater, não marca: responde e a conversa segue.
            quando = _av.ag.parse_datahora(
                f"{(d.get('visita') or {}).get('data','')} {(d.get('visita') or {}).get('hora','')}".strip())
            if _av.foi_oferecido(quando, visita_livres):
                if _av.modo(cfg) == "marca":
                    _av.marcar(pool, conta_id, conv[1], quando, quem=lead_empresa)
                else:
                    _av.propor(pool, conta_id, conv[1], quando,
                               conversa_id=conversa_id, quem=lead_empresa)

        if acao == "orcamento" and cfg["pode_orcamento"]:
            # Duas travas ANTES do documento, as duas de negócio e as duas em código —
            # não em instrução, porque instrução a IA às vezes ignora e aqui o que sai
            # é preço com o nome da empresa em cima.
            #
            # 1) SEM DATA, HORÁRIO E CONVIDADOS não se orça. Foi assim que ela cotou
            #    "150 pessoas" sem data e somou dois pacotes que são alternativas.
            # 2) PERGUNTA ANTES. Quem faz uma pergunta de preço quer um preço, não um
            #    documento formal com link de proposta — e cada um desses vira lixo
            #    no funil.
            evento = _evento_do_json(d)
            falta = _falta_pro_orcamento(evento)
            if falta:
                return _enviar(c, conta_id, conversa_id, canal, destino,
                               _texto_perguntando(resposta, falta))
            if not _ja_ofereceu_orcamento(msgs):
                return _enviar(c, conta_id, conversa_id, canal, destino,
                               _texto_oferecendo(resposta))
            return _orcamento(pool, c, conta_id, conversa_id, conv, catalogo, d, canal,
                              destino, resposta, evento)

        # responde tudo (nunca escala/desliga automático)
        _enviar(c, conta_id, conversa_id, canal, destino, resposta or
                "Boa! Me conta um pouquinho mais que já te ajudo 😊")
        if regra:
            # chegou mensagem do cliente enquanto a IA pensava? roda de novo
            return _cla.ultimo_do_paciente(c, conta_id, conversa_id) > _visto


# ---------------------------------------------------------------- a regra por número
#
# A IA de um chip com regra (migração 388, finance/chip_regra.py). O motor é o mesmo;
# o que muda é o que vem ANTES dele (rajada, pausa, horário) e o que ele pode fazer
# (etapa 1: responder e chamar gente — nada de orçamento formal nem visita marcada).

#: Quanto a IA espera o cliente parar de digitar. Na Prime o cliente manda "oi",
#: "tudo bem?", "queria saber do espaço" em três mensagens seguidas; sem espera, a IA
#: respondia a primeira e já chegava atrasada pra segunda.
_RAJADA_S = 6


def _regra_antes(c, conta_id: int, conversa_id: int, regra: dict, enviar) -> bool:
    """O que roda antes da IA da regra falar. Devolve se ela segue.

    Na ordem: espera a rajada; trava a conversa (uma volta por vez); pausa se
    alguém da equipe respondeu; não responde duas vezes a mesma coisa; e, fora do
    horário próprio, manda o recado de fora do horário uma vez só."""
    import time
    from finance import chip_regra as _cr
    from finance import clinica_agente as _cla
    c.commit()          # nada da leitura anterior fica aberto enquanto espera
    for _ in range(3):
        r = c.execute("""select extract(epoch from now() - max(criado_em)) from mensagens
                          where conversa_id=%s and autor='lead'""", (conversa_id,)).fetchone()
        c.commit()
        idade = float(r[0]) if r and r[0] is not None else _RAJADA_S
        if idade >= _RAJADA_S:
            break
        # teto: o horário da mensagem pode vir do provedor alguns minutos "no futuro"
        # (`_wa_recebido_em` aceita até 5 min), e aí a espera viraria minutos
        time.sleep(max(0.0, min(float(_RAJADA_S), _RAJADA_S - idade)))
    if not _cla.tentar_travar(c, conversa_id):
        return False
    # alguém apertou "Assumir" (ou desligou a IA) enquanto ela esperava a rajada?
    st = c.execute("select coalesce(agente_ativo,false), coalesce(status,'') from conversas "
                   "where id=%s and conta_id=%s", (conversa_id, conta_id)).fetchone()
    if not st or not st[0] or st[1] == "pendente":
        return False
    if _cr.pausar_se_humano(c, conta_id, conversa_id, regra):
        c.commit()
        return False
    if not _cr.tem_o_que_responder(c, conversa_id, regra):
        return False
    if not _cr.ia_pode_falar(regra):
        if not _cr.ja_mandou_fora(c, conversa_id, regra):
            enviar(_cr.texto_fora(regra), _cr.STATUS_FORA)
        return False
    return True


def _regra_prompt(regra: dict, msgs, perfil: str = "eventos") -> str:
    """O que a IA da regra sabe a mais: como se apresenta, que o preço dela é de
    referência, qual é o próximo passo, e como chamar gente.

    O PRÓXIMO PASSO SEGUE O NICHO (CLAUDE.md §6): quem vende festa convida pra
    conhecer o espaço (fechou 10,5% de quem visitou, contra 1,3%, na Prime); quem
    vende serviço convida pra uma reunião. Nunca festa pra quem não vende festa."""
    from finance import chip_regra as _cr
    apres = (regra.get("ia_apresentacao") or "").strip()
    ja_falou = any(a == "bot" for (_d, a, _t) in (msgs or []))
    linhas = ["\n\nCOMO VOCÊ ATENDE ESTE NÚMERO:"]
    if apres and not ja_falou:
        linhas.append(f"- É a sua primeira mensagem: apresente-se assim: \"{apres}\".")
    elif apres:
        linhas.append(f"- Você é: \"{apres}\" (já se apresentou, não repita).")
    eventos = perfil == "eventos"
    passo = ("a VISITA ao espaço: convide o cliente a conhecer" if eventos
             else "uma REUNIÃO com a equipe: convide o cliente a conversar")
    linhas += [
        "- PREÇO: diga só os valores liberados no catálogo, sempre como valor de "
        "referência (\"a partir de\"). Item com valor NÃO liberado: não diga nem estime.",
        ("- Quando falar de pacote, cite também 2 ou 3 adicionais do catálogo que "
         "combinam com a festa." if eventos else
         "- Quando falar de um serviço, cite também 1 ou 2 complementos do catálogo "
         "que combinam com o que o cliente precisa."),
        "- NÃO monte orçamento formal nem mande link de proposta: diga que o orçamento "
        "sai conferido pela equipe.",
        f"- O melhor caminho é {passo}. Se ele "
        "topar, pergunte o dia e o horário de preferência e diga que a equipe confirma "
        "— nunca confirme horário sozinha (motivo " + ("visita" if eventos else "agenda") + ").",
        "- CHAMAR GENTE: preencha avisar_equipe.motivo com UM destes quando precisar: "
        + ", ".join(f"{k} ({v[1].lower()})" for k, v in _cr.MOTIVOS.items()
                    if eventos or k != "visita")
        + ". Em avisar_equipe.resumo, uma linha do que o cliente quer (dia, horário, "
        "o pedido). Sem necessidade, deixe motivo vazio. Chamar gente NÃO encerra a "
        "conversa: você continua atendendo.",
        "- Desconto, negociação de valor ou sinal: não negocie — diga que o responsável "
        "retorna e use o motivo desconto ou sinal.",
    ]
    return "\n".join(linhas)


def _regra_avisar(pool, c, conta_id, conversa_id, conv, regra, d, lead_empresa) -> None:
    """Se a IA pediu gente no JSON, avisa quem a regra manda. Nunca derruba a
    resposta ao cliente."""
    from finance import chip_regra as _cr
    av = d.get("avisar_equipe") if isinstance(d.get("avisar_equipe"), dict) else {}
    motivo = str(av.get("motivo") or "").strip().lower()
    if motivo not in _cr.MOTIVOS:
        return
    try:
        _cr.avisar(pool, conta_id, regra, motivo, prospeccao_id=conv[1],
                   conversa_id=conversa_id, resumo=str(av.get("resumo") or "")[:600],
                   lead=lead_empresa)
    except Exception:  # noqa: BLE001
        _log.warning("agente: aviso da regra falhou conta=%s conversa=%s",
                     conta_id, conversa_id, exc_info=True)


def _nota_gemeo(c, conta_id, conv) -> str:
    """MESMO NÚMERO NO OUTRO CHIP. Numa empresa de dois números o mesmo cliente
    pode estar em duas campanhas, e cada chip tem a SUA conversa — de propósito,
    pra resposta sair pelo número que recebeu. O agente só enxerga a thread
    daqui, então sem este aviso ele fala como se fosse o primeiro contato com
    alguém que já está negociando do outro lado. Ele não deve puxar o assunto
    (não é dele, e o cliente pode nem saber que são dois números da mesma casa);
    deve parar de prometer sozinho e passar pra gente.

    `_gemeos_de_outro_chip` já engole o próprio erro (num SAVEPOINT, pra não
    abortar esta transação) e devolve vazio — o aviso é enfeite, a resposta ao
    cliente não é. Por isso aqui não há try: falhar de vez seria outra coisa."""
    from web.painel_prospeccao import _gemeos_de_outro_chip, _aviso_gemeo
    _g = _gemeos_de_outro_chip(c, conta_id, [conv[1]]).get(conv[1]) if conv[1] else None
    if not _g:
        return ""
    return ("\n\nATENÇÃO: " + _aviso_gemeo(_g) + " É a mesma pessoa falando "
            "com outro número desta empresa, numa conversa que você NÃO vê. "
            "Não cite isso ao cliente e não repita oferta: se ele falar de "
            "preço, prazo ou de algo já combinado, diga que vai confirmar "
            "com a equipe e NÃO feche nada por conta própria.")


def _enviar(c, conta_id, conversa_id, canal, destino, texto, status=None):
    if not texto:
        return
    res = _mandar(c, conta_id, canal, destino, texto, conversa_id)
    _add_bot_msg(c, conversa_id, canal, texto, res.get("sid") if res.get("ok") else None,
                 status=status)
    c.commit()


def _orcamento(pool, c, conta_id, conversa_id, conv, catalogo, d, canal, destino, resposta, evento):
    slugs_ok = {s["slug"]: s for s in catalogo}
    escolhidos = _itens_escolhidos(d, slugs_ok)
    if not escolhidos:
        # sem serviços válidos → responde o texto e para
        return _enviar(c, conta_id, conversa_id, canal, destino, resposta or
                       "Me conta rapidinho o que você precisa que eu monto um orçamento 😊")
    setup = sum(s["setup_centavos"] * s["qtd"] for s in escolhidos)
    mensal = sum(s["mensal_centavos"] * s["qtd"] for s in escolhidos)
    # `itens` vai pra folha da proposta, e lá os valores são lidos em REAIS: o
    # painel grava round(setup_centavos/100) e o fechamento multiplica por 100 de
    # volta (finance/cockpit.py). Só o agente gravava CENTAVOS neste mesmo campo, e a
    # folha então multiplicava tudo por 100 outra vez — o pacote de R$ 7.800 aparecia
    # como R$ 780.000,00 na linha, enquanto o total, que vem de outro campo, dizia
    # R$ 11.920,00. O papel se contradizia sozinho na frente do cliente.
    itens = [{"nome": s["nome"], "desc": s.get("descricao") or "",
              "categoria": s.get("categoria") or "", "qtd": s["qtd"],
              "unitario": round(s["setup_centavos"] / 100),
              "setup": round(s["setup_centavos"] * s["qtd"] / 100),
              "mensal": round(s["mensal_centavos"] * s["qtd"] / 100)} for s in escolhidos]
    empresa = conv[3] or conv[2] or "Cliente"
    token = secrets.token_urlsafe(16)
    from finance import vendas as _vendas
    _n = c.execute("""select coalesce(n.slug,'') from contas ct
                        left join nichos n on n.id = ct.nicho_id
                       where ct.id=%s""", (conta_id,)).fetchone()
    # O TELEFONE DO CLIENTE VAI NA PROPOSTA.
    #
    # Sem ele o `proposta_lead` fica sem a segunda porta (casar lead por telefone),
    # e a folha sai sem o contato de quem vai assinar. O agente é o único caminho
    # que gravava a proposta com o campo vazio — o cockpit sempre copiou do lead.
    fone = (conv[4] or conv[5] or (destino if canal == "whatsapp" else "") or "")
    # `primeiro_ano_centavos` NÃO pode ficar no default.
    #
    # Quem gera os títulos lê `coalesce(primeiro_ano_centavos, setup_centavos, 0)`
    # (finance/vendas.py, finance/agenda.py) — e o coalesce só cai pro setup quando o
    # campo é NULO. O default da coluna é ZERO, que não é nulo: a proposta 32 da conta
    # 34, feita pelo agente em 20/08, foi aprovada pelo cliente valendo R$ 0,00.
    # Sem desconto (o agente não dá), o primeiro ano É o setup.
    # numerada ao nascer — o agente criava sem número até 06/09 (ver
    # finance.vendas.NUMERO_SQL). O retry usa savepoint de propósito: a conversa
    # já foi gravada nesta transação e uma colisão de número não pode apagá-la.
    _r = _vendas.com_retry_numero(c, lambda: c.execute(
        f"""insert into orcamentos (conta_id, cliente, empresa, modulos, itens, escopo,
             evento, setup_centavos, mensal_centavos, primeiro_ano_centavos, whatsapp,
             n_modulos, criado_por, token, status, modo, numero)
           values (%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s::jsonb,%s,%s,%s,%s,%s,'agente',%s,'rascunho',%s,
                   {_vendas.NUMERO_SQL})
           returning id""",
        (conta_id, empresa, empresa, json.dumps([s["slug"] for s in escolhidos]),
         json.dumps(itens), _CONDICOES, json.dumps(evento), setup, mensal, setup,
         fone[:60], len(escolhidos), token,
         _vendas.modo_por_nicho(_n[0] if _n else ""), conta_id)).fetchone())
    if not _r:
        raise RuntimeError("não consegui numerar a proposta depois de 3 tentativas")
    orc_id = _r[0]

    # O CARD DO FUNIL, amarrado AQUI, na mesma transação da proposta.
    #
    # `proposta_lead.garantir` procura o lead pelo telefone porque nas outras portas
    # (cockpit, app do vendedor) não há conversa nenhuma pra perguntar. Aqui há: a
    # conversa JÁ sabe de quem é o lead. Amarrar direto dispensa o palpite e o empate.
    #
    # Era isto que faltava. Medido na conta 34 em 20/08: a proposta 32 saiu pelo chip
    # 2 às 17:18 e o cliente aprovou às 17:19 — sem linha em `orcamento_envios`, sem
    # `prospeccao.orcamento_id` e sem movimento no funil. O gatilho `orcamento_enviado`
    # estava ligado e cego: a consulta dele é `join orcamentos on id = p.orcamento_id`,
    # e sem o vínculo não existe card pra mover. O lead ficou parado em "contatado"
    # com uma proposta aprovada do lado.
    if conv[1]:
        try:
            from finance import proposta_lead as _pl
            _pl.ligar(c, conta_id, int(conv[1]), orc_id)
        except Exception:  # noqa: BLE001 — o vínculo não pode derrubar o atendimento
            _log.warning("agente: não amarrei a proposta %s ao lead %s",
                         orc_id, conv[1], exc_info=True)

    from finance.email_sender import _app_url
    link = _app_url() + "/proposta/" + token
    corpo = (resposta + "\n\n" if resposta else "") + _bloco_orcamento(escolhidos, link)
    _enviar(c, conta_id, conversa_id, canal, destino, corpo)

    # O REGISTRO DO ENVIO, depois do commit do `_enviar`.
    #
    # `proposta_email.registrar` é o ponto único por onde "a proposta saiu" passa: ele
    # grava `orcamento_envios` (que é o que o gatilho lê quando o status ainda é
    # rascunho) e chama o `garantir` como rede. O comentário lá previa a quinta rota
    # de envio esquecendo de chamar — o agente era essa quinta rota.
    #
    # Depois do `_enviar` porque ele abre a PRÓPRIA conexão: antes do commit, a
    # proposta que acabou de nascer ainda não existe pra ela.
    try:
        from finance import proposta_email as _pe
        _pe.registrar(pool, conta_id, orc_id, destino=str(destino or "")[:200],
                      remetente_usado="", ok=True, por="agente",
                      canal=canal or "whatsapp")
    except Exception:  # noqa: BLE001
        _log.warning("agente: não registrei o envio da proposta %s", orc_id, exc_info=True)
