"""Dispatcher de SAÍDA do WhatsApp por conta: escolhe Twilio ou Cloud API.

Cada empresa configura o WhatsApp em canais_config (canal='whatsapp') com um
`provedor`:
  * 'twilio' (padrão) → número da empresa em `identificador`, credenciais no env.
  * 'cloud'  → número PRÓPRIO na Cloud API da Meta: `wa_phone_id` + `token` (por conta).
  * 'qr'     → sessão tipo WhatsApp Web (serviço à parte; ainda não implementado aqui).

Recebe um cursor já aberto (`c`) porque quem chama (painel/agente) já está numa
transação. Sempre best-effort: retorna {"ok":False,"erro":...} em vez de estourar.
"""
from __future__ import annotations

from finance import whatsapp_cloud as _cloud
from finance import whatsapp_twilio as _twilio


def _row(c, conta_id):
    """O canal de WhatsApp desta empresa — o dela OU o do CHIP dela.

    O CHIP PODE MORAR EM OUTRA CONTA. `contas.chip_de` liga uma conta-chip à
    empresa dona: na Liberal (37) o canal está na conta 38 ("cp liberal"), e a 37
    não tem linha nenhuma em `canais_config`. Esta consulta procurava só por
    `conta_id = 37`, achava nada, e todo envio devolvia `sem_numero_empresa` —
    "está conectado mas não consigo mandar mensagem pelo canal de comunicação",
    22/09/2026.

    O #781 consertou essa mesma pergunta na TELA (o status do chip em
    `painel_prospeccao._wa_chip`) e não aqui. Consertar a tela e não a função é
    como o painel passou a dizer "conectado" enquanto o envio dizia "sem número".

    O `order by (cc.conta_id <> %s)` põe o canal da PRÓPRIA conta na frente: quem
    tem chip próprio não muda de comportamento, e empresa com chip próprio E
    conta-chip filha continua saindo pelo dela.
    """
    return c.execute(
        """select coalesce(cc.provedor,'twilio'), cc.identificador,
                  cc.wa_phone_id, cc.token, cc.conta_id
             from canais_config cc
            where cc.canal='whatsapp' and cc.ativo
              and (cc.conta_id = %s
                   or cc.conta_id in (select id from contas where chip_de = %s))
            order by (cc.conta_id <> %s), cc.conta_id
            limit 1""",
        (conta_id, conta_id, conta_id)).fetchone()


def provedor_da_conta(c, conta_id) -> str:
    """'twilio' | 'cloud' | 'qr' — ou '' se a empresa não tem canal ativo.

    Quem chama precisa disso pra saber se as regras da API OFICIAL do WhatsApp
    (janela de 24h + template aprovado) valem: elas são da Business API
    (twilio/cloud). No 'qr' é uma sessão tipo WhatsApp Web — texto livre pra
    qualquer número, sempre, sem template."""
    r = _row(c, conta_id)
    return r[0] if r else ""


def configurado_conta(c, conta_id) -> bool:
    """A empresa consegue ENVIAR WhatsApp agora? (provedor + credenciais prontos)."""
    r = _row(c, conta_id)
    if not r:
        return False
    prov = r[0]
    if prov == "cloud":
        return _cloud.configurado(r[2], r[3])
    if prov == "qr":
        from finance import whatsapp_qr as _qr
        return _qr.configurado()   # serviço ligado; a sessão em si é checada no envio
    return _twilio.configurado() and bool(r[1])


def chip_da_conversa(c, conta_id, conversa_id) -> int | None:
    """Por qual chip esta conversa entrou — ou None (= o chip da própria empresa).

    Só vale no provedor 'qr': Twilio e Cloud API têm um número por conta, e ali a
    pergunta não existe. Confere `conta_id` de propósito: sem isso um id de conversa
    de outra empresa faria a resposta sair por um chip que não é desta.
    """
    if not conversa_id:
        return None
    r = c.execute("select chip_id from conversas where id=%s and conta_id=%s",
                  (conversa_id, conta_id)).fetchone()
    return int(r[0]) if (r and r[0]) else None


def preparar(c, conta_id) -> dict | None:
    """Tudo o que o envio precisa saber do BANCO, lido de uma vez. None = sem canal.

    Existe pra separar as duas metades do envio: esta lê o banco (rápido), e o
    `enviar_pronto` fala com o WhatsApp (pode demorar segundos). Quem chama pode
    então DEVOLVER A CONEXÃO antes da parte lenta — o app tem 10 por processo, e
    segurá-las durante a rede é o que trava a tela de todo mundo quando o WhatsApp
    está lento (medido em 15/09/2026: conexões presas por 89 segundos).

    O conteúdo é opaco de propósito: quem chama só repassa pro `enviar_pronto`.
    """
    r = _row(c, conta_id)
    if not r:
        return None
    # `conta_id` é a conta DO CANAL, que no provedor 'qr' é o chip por onde a
    # mensagem sai — e pode ser uma conta-chip filha, não a empresa. `empresa_id`
    # guarda quem pediu, pra quem precisar dos dois.
    return {"conta_id": r[4], "empresa_id": conta_id, "provedor": r[0],
            "identificador": r[1], "wa_phone_id": r[2], "token": r[3]}


def enviar_pronto(destino: dict | None, numero, texto, *, chip_id=None) -> dict:
    """A metade que fala com o WhatsApp. NÃO toca no banco — ver `preparar`."""
    if not destino:
        return {"ok": False, "erro": "sem_numero_empresa"}
    prov = destino["provedor"]
    if prov == "cloud":
        return _cloud.enviar_texto(destino["wa_phone_id"], destino["token"], numero, texto)
    if prov == "qr":
        from finance import whatsapp_qr as _qr
        return _qr.enviar_texto(chip_id or destino["conta_id"], numero, texto)
    return _twilio.enviar_texto(destino["identificador"], numero, texto)


def enviar(c, conta_id, numero, texto, *, chip_id=None) -> dict:
    """Manda um texto pro `numero` do lead pelo provedor configurado da empresa.

    `chip_id` só é lido no provedor 'qr' e responde "por qual número isto sai".
    Nulo — que é o padrão e o caso de toda empresa de um chip só — sai pelo chip da
    própria empresa, exatamente como sempre saiu. Quem responde a uma CONVERSA
    deveria passar o chip dela (ver `chip_da_conversa`): sem isso o lead escreve pra
    um número e é respondido por outro, que do lado dele parece outra empresa.

    Continua existindo pros chamadores que já estão numa transação curta (agente,
    campanhas, avisos). Quem segura a conexão por causa da REDE deve usar o par
    `preparar` + `enviar_pronto`.
    """
    return enviar_pronto(preparar(c, conta_id), numero, texto, chip_id=chip_id)


def enviar_template(c, conta_id, numero, content_sid, variaveis, mmlite=False) -> dict:
    """Dispara um TEMPLATE aprovado (fora da janela de 24h) pelo número da empresa.
    Twilio: `content_sid` é o Content SID (HX...). Cloud API (número próprio): o mesmo
    campo carrega o NOME do template aprovado na Meta. `mmlite=True` (só no provedor
    cloud) roteia pela Marketing Messages Lite API — mesmo preço, entrega otimizada."""
    r = _row(c, conta_id)
    prov = r[0] if r else "twilio"
    if prov == "cloud":
        # no Cloud API o "content_sid" é o NOME do template aprovado na Meta
        return _cloud.enviar_template(r[2], r[3], numero, content_sid, variaveis,
                                      mmlite=mmlite)
    if prov != "twilio":
        return {"ok": False, "erro": "provedor_sem_template"}
    # nunca cai no número global do env: senão a campanha de uma conta sem WhatsApp
    # próprio sairia pelo número (e identidade) de outra empresa conectada via Twilio.
    remetente = r[1] if r and r[1] else ""
    if not remetente:
        return {"ok": False, "erro": "sem_numero_empresa"}
    return _twilio.enviar_template(_twilio.normalizar_from(remetente), numero,
                                   content_sid, variaveis)
