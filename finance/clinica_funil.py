"""A TELA "FUNIL DA CLÍNICA": aplicar o funil aprovado e ligar o que anda sozinho.

Desenho aprovado: docs/mockups/clinica_crm_telas.html (seções 01 e 02, 01/10/2026) e o
rascunho "Ligar o funil da Pelle" (decisões A a D, aprovadas em 03/10/2026). Tela:
web/painel_clinica_funil.py (/painel/clinica/funil), só pro dono e o gestor.

O QUE ELA FAZ, NUMA TELA SÓ (decisão D)
  1. As colunas: o modelo do ramo clínica (`funil_modelo.plano`), com TUDO do desenho
     aprovado já marcado, inclusive o nome que o dono tinha dado ("Agenda" →
     "Agendado"). Tirar uma coluna da conta do quadro vem desmarcado: não é do desenho.
     Nenhum cartão muda de coluna.
  2. O que anda sozinho, cada regra em desligado, ensaio ou ligado (decisão C): a
     regra em ensaio não mexe em nada e conta o que teria feito.
       resposta  primeira resposta nossa → Em conversa (gatilho `resposta_nossa` na
                 etapa `contatado`; o modo é o `gatilhos_modo` da régua)
       prazo     Em conversa: 3 dias, 2 renovações com justificativa (teto da etapa
                 `contatado`; o modo é o `teto_modo`)
       reabre    Perdido volta a Em conversa quando a pessoa escreve (`reativa_para`
                 da etapa `perdido`; não tem ensaio: é ligado ou desligado)
  3. Conferir e aplicar.

O QUE ELA NÃO LIGA: as mensagens automáticas (chamar de novo, resgate), que são das
entregas 8 e 9, pela ordem do dono (a recepção primeiro). O prazo do Plano enviado é a
validade do próprio plano (decisão B): não ganha teto aqui. Os 5 minutos do Novo são
tempo de resposta, da tela Hoje (decisão A, entrega 4).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from finance import funil_modelo as fm
from finance import raio_x_perfil as _rxp

PERFIL = "clinica"
#: as regras automáticas da tela, na ordem em que aparecem
REGRAS = ("resposta", "prazo", "reabre")
MODOS = ("off", "observando", "ligado")
#: "outro": a regra existe na conta, mas não é a desta tela (outro gatilho em Em
#: conversa, outro prazo, outro destino do Perdido). A tela mostra e não mexe.
OUTRO = "outro"
MODO_D = {"off": "desligado", "observando": "ensaio", "ligado": "ligado", OUTRO: "manter como está"}
PRAZO_DIAS, PRAZO_RENOVACOES = 3, 2
ENSAIO_DIAS = 7


def _plano(c, conta_id: int) -> list[dict]:
    """O plano do modelo com o que o desenho aprovado pede já marcado. Tirar do quadro
    uma coluna que a conta criou não é do desenho: vem desmarcado."""
    itens = fm.plano(c, conta_id, PERFIL)
    do_modelo = {ch for ch, *_ in _rxp.etapas_padrao(PERFIL)}
    for it in itens:
        it["marcado"] = not (it["acao"] == "quadro" and it["chave"] not in do_modelo)
    return itens


def aplicado(c, conta_id: int) -> bool:
    """A conta já tem as três colunas do funil da clínica (Em tratamento e Retorno como
    pós-venda)?"""
    r = c.execute("""select count(*) from funil_etapas
                      where conta_id=%s and (chave='consulta' or (chave in ('tratamento','retorno') and fase='pos'))""",
                  (conta_id,)).fetchone()
    return bool(r and r[0] == 3)


def _etapa(c, conta_id: int, chave: str) -> dict | None:
    r = c.execute("""select gatilho, gatilho_ativo, teto_dias, coalesce(renovacoes_max, 0), reativa_para
                       from funil_etapas where conta_id=%s and chave=%s""", (conta_id, chave)).fetchone()
    return ({"gatilho": r[0], "gatilho_ativo": r[1], "teto_dias": r[2], "renovacoes_max": r[3],
             "reativa_para": r[4]} if r else None)


def _regua(c, conta_id: int) -> dict:
    r = c.execute("select gatilhos_modo, teto_modo from funil_regua where conta_id=%s", (conta_id,)).fetchone()
    return {"gatilhos_modo": r[0] if r else "off", "teto_modo": r[1] if r else "off"}


def _outras(c, conta_id: int) -> dict:
    """As OUTRAS etapas que usam gatilho ou prazo. O modo da régua (desligado, ensaio,
    ligado) é um só pra conta toda: mudar aqui mudaria o delas junto."""
    linhas = c.execute("""select rotulo, gatilho_ativo, coalesce(teto_dias, 0) > 0 from funil_etapas
                           where conta_id=%s and chave <> 'contatado' order by ordem""", (conta_id,)).fetchall()
    return {"gatilho": [r[0] for r in linhas if r[1]], "prazo": [r[0] for r in linhas if r[2]]}


def modos(c, conta_id: int) -> dict:
    """O modo de cada regra, como está hoje na conta ("outro" quando a etapa tem uma
    regra que não é a desta tela)."""
    reg = _regua(c, conta_id)
    contatado = _etapa(c, conta_id, "contatado") or {}
    perdido = _etapa(c, conta_id, "perdido") or {}
    if not contatado.get("gatilho_ativo"):
        resp = "off"
    elif contatado.get("gatilho") == "resposta_nossa":
        resp = reg["gatilhos_modo"]
    else:
        resp = OUTRO
    if not contatado.get("teto_dias"):
        prazo = "off"
    elif (contatado["teto_dias"], contatado["renovacoes_max"]) == (PRAZO_DIAS, PRAZO_RENOVACOES):
        prazo = reg["teto_modo"]
    else:
        prazo = OUTRO
    destino = perdido.get("reativa_para")
    reabre = "off" if not destino else ("ligado" if destino == "contatado" else OUTRO)
    return {"resposta": resp, "prazo": prazo, "reabre": reabre}


def ensaio(c, conta_id: int, agora: datetime | None = None) -> dict:
    """O que as regras em ensaio teriam feito nos últimos 7 dias."""
    agora = agora or datetime.now(timezone.utc)
    desde = agora - timedelta(days=ENSAIO_DIAS)
    out = {"resposta": 0, "prazo": 0, "ao_ligar": 0}
    try:
        with c.transaction():
            out["resposta"] = c.execute(
                """select count(distinct prospeccao_id) from funil_movimentos
                    where conta_id=%s and motivo='simulado:resposta_nossa' and criado_em >= %s""",
                (conta_id, desde)).fetchone()[0]
    except Exception:  # noqa: BLE001 — banco sem a régua
        pass
    try:
        with c.transaction():
            out["prazo"] = c.execute(
                """select count(distinct prospeccao_id) from funil_avisos
                    where conta_id=%s and simulado and estado='teto' and etapa='contatado'
                      and criado_em >= %s""",
                (conta_id, desde)).fetchone()[0]
    except Exception:  # noqa: BLE001 — banco sem o teto (230)
        pass
    # O QUE ANDA NO DIA EM QUE LIGAR. O ensaio grava cada salto uma vez só e não
    # repete: o cartão que já tinha resposta nossa antes da semana não entra na conta
    # acima, mas anda junto na primeira passada ligada. Este é o número do susto, e
    # ele vem antes do clique (a conta 34 viu 78 cartões andarem de uma vez em 19/08).
    try:
        with c.transaction():
            out["ao_ligar"] = c.execute(
                """select count(*) from prospeccao p
                    where p.conta_id=%(c)s and p.estagio='lead'
                      -- como o motor: status sem etapa conta como antes de tudo (-1)
                      and coalesce((select e.ordem from funil_etapas e
                                     where e.conta_id=p.conta_id and e.chave=p.status), -1)
                          < (select ordem from funil_etapas where conta_id=%(c)s and chave='contatado')
                      and exists (select 1 from conversas cv join mensagens m on m.conversa_id = cv.id
                                   where cv.conta_id=p.conta_id and cv.prospeccao_id=p.id
                                     and m.direcao='out')""",
                {"c": conta_id}).fetchone()[0]
    except Exception:  # noqa: BLE001 — banco sem as conversas
        pass
    return out


def estado(c, conta_id: int, agora: datetime | None = None) -> dict:
    """Tudo o que a tela mostra."""
    itens = _plano(c, conta_id)
    cards = c.execute("""select status, count(*) from prospeccao where conta_id=%s and estagio='lead'
                          group by status""", (conta_id,)).fetchall()
    feito, atual, outras = aplicado(c, conta_id), modos(c, conta_id), _outras(c, conta_id)
    if feito:
        marcado = atual
    else:
        # antes de aplicar, o rascunho aprovado: ensaio nas duas, Perdido reabrindo. Com
        # outra etapa usando o mesmo modo da conta, vem o modo que já está lá (trocar
        # seria mexer na outra etapa, e a tela recusa)
        reg = _regua(c, conta_id)
        marcado = {"resposta": reg["gatilhos_modo"] if outras["gatilho"] else "observando",
                   "prazo": reg["teto_modo"] if outras["prazo"] else "observando",
                   "reabre": "ligado"}
        # a regra que o dono já montou na Régua fica como está, a não ser que ele troque
        marcado = {k: OUTRO if atual[k] == OUTRO else v for k, v in marcado.items()}
    return {"aplicado": feito, "itens": itens, "modos": atual, "marcado": marcado,
            "ensaio": ensaio(c, conta_id, agora), "cards": dict(cards), "outras": outras}


def _garantir_regua(c, conta_id: int) -> None:
    """A linha da régua da conta, tudo desligado, como `funil_regua.config` semeia."""
    c.execute("insert into funil_regua (conta_id) values (%s) on conflict (conta_id) do nothing", (conta_id,))


def salvar_regras(c, conta_id: int, escolhas: dict) -> str | None:
    """Liga, põe em ensaio ou desliga cada regra. Só depois de aplicar as colunas: as
    regras moram nas etapas do funil novo."""
    if not aplicado(c, conta_id):
        return "Aplique as colunas do funil da clínica primeiro."
    resp, prazo, reabre = (escolhas.get(k) or "off" for k in REGRAS)
    if resp not in MODOS + (OUTRO,) or prazo not in MODOS + (OUTRO,) or reabre not in ("off", "ligado", OUTRO):
        return "Escolha desligado, ensaio ou ligado em cada regra."
    _garantir_regua(c, conta_id)
    reg, outras = _regua(c, conta_id), _outras(c, conta_id)
    # O MODO É DA CONTA TODA. Com outra etapa usando gatilho (ou prazo), trocar o modo
    # aqui trocaria o dela sem ninguém ver: a tela recusa e manda pra Régua, onde as
    # etapas aparecem juntas. Desligar a regra desta tela não mexe no modo.
    for regra, modo, chave_modo, quem in (("primeira resposta", resp, "gatilhos_modo", outras["gatilho"]),
                                          ("prazo", prazo, "teto_modo", outras["prazo"])):
        if quem and modo not in ("off", OUTRO) and modo != reg[chave_modo]:
            return (f"O modo da regra de {regra} vale para a conta toda, e também mexe em "
                    f"{', '.join(quem)}: hoje está em {MODO_D.get(reg[chave_modo], reg[chave_modo])}. "
                    f"Mude em Funil › Régua, onde as etapas aparecem juntas.")
    # o gatilho da primeira resposta: a etapa Em conversa recebe o evento
    if resp != OUTRO:
        c.execute("""update funil_etapas set gatilho = case when %s = 'off' then gatilho else 'resposta_nossa' end,
                            gatilho_ativo = (%s <> 'off')
                      where conta_id=%s and chave='contatado'""", (resp, resp, conta_id))
        if resp != "off" or not outras["gatilho"]:
            c.execute("update funil_regua set gatilhos_modo=%s, atualizado_em=now() where conta_id=%s",
                      (resp, conta_id))
    # o prazo de Em conversa: 3 dias, 2 renovações, com justificativa
    if prazo != OUTRO:
        if prazo == "off":
            c.execute("update funil_etapas set teto_dias=null where conta_id=%s and chave='contatado'", (conta_id,))
        else:
            c.execute("""update funil_etapas set teto_dias=%s, renovacoes_max=%s, exige_justificativa=true
                          where conta_id=%s and chave='contatado'""", (PRAZO_DIAS, PRAZO_RENOVACOES, conta_id))
        if prazo != "off" or not outras["prazo"]:
            c.execute("update funil_regua set teto_modo=%s, atualizado_em=now() where conta_id=%s", (prazo, conta_id))
    # o Perdido que volta a falar reabre em Em conversa
    if reabre != OUTRO:
        c.execute("update funil_etapas set reativa_para=%s where conta_id=%s and chave='perdido'",
                  ("contatado" if reabre == "ligado" else None, conta_id))
    return None


def aplicar(c, conta_id: int, aceitas, escolhas: dict) -> tuple[dict, str | None]:
    """Aplica as colunas marcadas e, em seguida, as regras. Devolve (contagem, erro).

    Já aplicado, não faz nada: o formulário reenviado (voltar do navegador, uma aba
    antiga) traria as regras padrão por cima das que o dono escolheu depois. Daí em
    diante as regras mudam só pelo formulário delas."""
    if aplicado(c, conta_id):
        return {}, None
    validas = {it["id"] for it in _plano(c, conta_id)}
    feito = fm.aplicar(c, conta_id, PERFIL, [a for a in (aceitas or ()) if a in validas])
    if not aplicado(c, conta_id):
        return feito, ("As colunas Consulta, Em tratamento e Retorno são o funil da clínica: "
                       "marque as três para aplicar.")
    return feito, salvar_regras(c, conta_id, escolhas)
