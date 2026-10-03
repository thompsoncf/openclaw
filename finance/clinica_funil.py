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

PERFIL = "clinica"
#: as regras automáticas da tela, na ordem em que aparecem
REGRAS = ("resposta", "prazo", "reabre")
MODOS = ("off", "observando", "ligado")
MODO_D = {"off": "desligado", "observando": "ensaio", "ligado": "ligado"}
PRAZO_DIAS, PRAZO_RENOVACOES = 3, 2
ENSAIO_DIAS = 7


def _plano(c, conta_id: int) -> list[dict]:
    """O plano do modelo com o que o desenho aprovado pede já marcado. Tirar do quadro
    uma coluna que a conta criou não é do desenho: vem desmarcado."""
    itens = fm.plano(c, conta_id, PERFIL)
    for it in itens:
        it["marcado"] = not (it["acao"] == "quadro" and it.get("para") is True)
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


def modos(c, conta_id: int) -> dict:
    """O modo de cada regra, como está hoje na conta."""
    reg = _regua(c, conta_id)
    contatado = _etapa(c, conta_id, "contatado") or {}
    perdido = _etapa(c, conta_id, "perdido") or {}
    resp = (reg["gatilhos_modo"] if contatado.get("gatilho") == "resposta_nossa" and contatado.get("gatilho_ativo")
            else "off")
    prazo = reg["teto_modo"] if contatado.get("teto_dias") else "off"
    return {"resposta": resp, "prazo": prazo,
            "reabre": "ligado" if perdido.get("reativa_para") == "contatado" else "off"}


def ensaio(c, conta_id: int, agora: datetime | None = None) -> dict:
    """O que as regras em ensaio teriam feito nos últimos 7 dias."""
    agora = agora or datetime.now(timezone.utc)
    desde = agora - timedelta(days=ENSAIO_DIAS)
    out = {"resposta": 0, "prazo": 0}
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
                """select count(*) from funil_avisos
                    where conta_id=%s and simulado and estado='teto' and criado_em >= %s""",
                (conta_id, desde)).fetchone()[0]
    except Exception:  # noqa: BLE001 — banco sem o teto (230)
        pass
    return out


def estado(c, conta_id: int, agora: datetime | None = None) -> dict:
    """Tudo o que a tela mostra."""
    itens = _plano(c, conta_id)
    cards = c.execute("select status, count(*) from prospeccao where conta_id=%s group by status",
                      (conta_id,)).fetchall()
    return {"aplicado": aplicado(c, conta_id), "itens": itens, "modos": modos(c, conta_id),
            "ensaio": ensaio(c, conta_id, agora), "cards": dict(cards)}


def _garantir_regua(c, conta_id: int) -> None:
    """A linha da régua da conta, tudo desligado, como `funil_regua.config` semeia."""
    c.execute("insert into funil_regua (conta_id) values (%s) on conflict (conta_id) do nothing", (conta_id,))


def salvar_regras(c, conta_id: int, escolhas: dict) -> str | None:
    """Liga, põe em ensaio ou desliga cada regra. Só depois de aplicar as colunas: as
    regras moram nas etapas do funil novo."""
    if not aplicado(c, conta_id):
        return "Aplique as colunas do funil da clínica primeiro."
    resp, prazo, reabre = (escolhas.get(k) or "off" for k in REGRAS)
    if resp not in MODOS or prazo not in MODOS or reabre not in ("off", "ligado"):
        return "Escolha desligado, ensaio ou ligado em cada regra."
    _garantir_regua(c, conta_id)
    # o gatilho da primeira resposta: a etapa Em conversa recebe o evento; o modo da
    # régua (desligado, ensaio, ligado) é o da conta, e só ela usa gatilho na clínica
    c.execute("""update funil_etapas set gatilho = case when %s = 'off' then gatilho else 'resposta_nossa' end,
                        gatilho_ativo = (%s <> 'off')
                  where conta_id=%s and chave='contatado'""", (resp, resp, conta_id))
    c.execute("update funil_regua set gatilhos_modo=%s, atualizado_em=now() where conta_id=%s", (resp, conta_id))
    # o prazo de Em conversa: 3 dias, 2 renovações, com justificativa
    if prazo == "off":
        c.execute("update funil_etapas set teto_dias=null where conta_id=%s and chave='contatado'", (conta_id,))
    else:
        c.execute("""update funil_etapas set teto_dias=%s, renovacoes_max=%s, exige_justificativa=true
                      where conta_id=%s and chave='contatado'""", (PRAZO_DIAS, PRAZO_RENOVACOES, conta_id))
    c.execute("update funil_regua set teto_modo=%s, atualizado_em=now() where conta_id=%s", (prazo, conta_id))
    # o Perdido que volta a falar reabre em Em conversa
    c.execute("update funil_etapas set reativa_para=%s where conta_id=%s and chave='perdido'",
              ("contatado" if reabre == "ligado" else None, conta_id))
    return None


def aplicar(c, conta_id: int, aceitas, escolhas: dict) -> tuple[dict, str | None]:
    """Aplica as colunas marcadas e, em seguida, as regras. Devolve (contagem, erro)."""
    validas = {it["id"] for it in _plano(c, conta_id)}
    feito = fm.aplicar(c, conta_id, PERFIL, [a for a in (aceitas or ()) if a in validas])
    if not aplicado(c, conta_id):
        return feito, ("As colunas Consulta, Em tratamento e Retorno são o funil da clínica: "
                       "marque as três para aplicar.")
    return feito, salvar_regras(c, conta_id, escolhas)
