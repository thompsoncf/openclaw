"""POR QUE O LEAD FOI PERDIDO: a lista da conta, a exigência e o que fica gravado.

Regra 5 do FLUXO_FINAL_FUNIL_PRIME_EVENTOS_DESENVOLVEDOR_V3, aprovada em 11/09/2026.

    "A coluna PERDIDO deve receber somente leads que encerraram efetivamente o ciclo
     comercial, evitando que o vendedor use essa etapa apenas para 'limpar' o funil.
     Ao marcar como PERDIDO, o sistema deve exigir obrigatoriamente o motivo da
     perda, com opções padronizadas. (...) Além do motivo, o CRM deve salvar
     automaticamente vendedor responsável, data da perda, etapa de origem, tempo
     total no funil e histórico completo."

A LISTA É DA CONTA, NÃO DO CÓDIGO (migração 235)
Eram sete chaves fixas com um CHECK no banco. Os dez do documento são os DA PRIME —
a próxima empresa de eventos vai querer "buffet próprio", "estacionamento", "não
aceitou o regulamento", e nenhum desses pode esperar um deploy. Cada conta recebe a
semente do PERFIL dela na primeira vez (`raio_x_perfil.semente_motivos`) e dali em
diante a lista é dela.

DUAS RECUSAS, E AS DUAS SÃO O PEDIDO VIRANDO CÓDIGO
    motivo_obrigatorio     a etapa exige motivo e não veio nenhum
    descricao_obrigatoria  o motivo escolhido exige texto e o texto está vazio

Recusar é a trava. Um campo marcado como obrigatório na tela se contorna pelo POST,
e o pedido é que o lead NÃO ENTRE em Perdido sem que alguém diga por quê.

O QUE NÃO VIRA COLUNA: "tempo total no funil". É `perda_em - criado_em`, e guardar
derivado é guardar uma segunda verdade que sai de sincronia no primeiro backfill.
`tempo_no_funil` calcula na hora, de quem perguntar.

NASCE DESLIGADO. `funil_etapas.exige_motivo` começa false em toda etapa: quem não
ligar continua podendo fechar sem motivo, como o produto sempre funcionou. O dono
liga no Perdido quando quiser.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

_log = logging.getLogger("openclaw.funil_perda")

#: o mesmo vazio de `funil_modelo.DO_DONO` — "o nome é do dono, não da semente"
_DO_DONO = ""


# ------------------------------------------------------------------ a lista

def motivos(c, conta_id: int, perfil_chave: str = "recorrente",
            so_ativos: bool = True) -> list[dict]:
    """Os motivos desta conta, semeando do perfil na primeira vez.

    Semear na LEITURA, e não numa migração, é o mesmo caminho de `_etapas`: a
    migração não sabe o nicho de cada conta (ele muda depois), e uma conta criada
    amanhã precisa da lista sem ninguém rodar nada.
    """
    from finance import raio_x_perfil as _rxp
    achou = c.execute("select 1 from funil_motivos_perda where conta_id=%s limit 1",
                      (conta_id,)).fetchone()
    if not achou:
        semente = _rxp.semente_motivos(perfil_chave)
        for i, (chave, rotulo, exige) in enumerate(semente):
            c.execute("""insert into funil_motivos_perda
                           (conta_id, chave, rotulo, ordem, exige_descricao, semeado_de)
                         values (%s,%s,%s,%s,%s,%s)
                         on conflict (conta_id, chave) do nothing""",
                      (conta_id, chave, rotulo, i * 10, exige, perfil_chave))
    linhas = c.execute(
        "select chave, rotulo, ordem, ativo, exige_descricao, id from funil_motivos_perda "
        " where conta_id=%s " + (" and ativo " if so_ativos else "") +
        " order by ordem, id", (conta_id,)).fetchall()
    return [{"chave": r[0], "rotulo": r[1], "ordem": r[2], "ativo": r[3],
             "exige_descricao": r[4], "id": r[5]} for r in linhas]


# ------------------------------------------------- o modelo do ramo, pros motivos
#
# As ETAPAS já tinham isto desde 11/09 (`finance.funil_modelo.plano`). Os motivos
# não tinham nada: quem trocasse de ramo — ou quem tivesse aberto a tela antes de
# escolher o ramo — ficava com a lista errada e sem caminho de volta. Medido em
# 14/09/2026: 4 das 8 contas com funil tinham ZERO motivo, e a Liberal (37, seguros)
# tinha os 8 do recorrente, sem "Renovou direto com a seguradora".
#
# Mesma forma do plano das etapas, e pelos mesmos motivos: propõe, não aplica;
# nunca apaga (motivo fora do modelo é proposto pra DESLIGAR, e o histórico de quem
# foi perdido por ele continua lendo o rótulo); e o que o dono renomeou vem
# desmarcado.

def plano_motivos(c, conta_id: int, perfil_chave: str) -> list[dict]:
    """O que o modelo do ramo mudaria na lista de motivos. Vazia = já está igual."""
    from finance import raio_x_perfil as _rxp
    from finance.funil_modelo import DO_DONO
    atuais = {r[0]: {"rotulo": r[1], "ativo": r[2], "semeado_de": r[3], "id": r[4]}
              for r in c.execute(
                  """select chave, rotulo, ativo, semeado_de, id
                       from funil_motivos_perda where conta_id=%s""",
                  (conta_id,)).fetchall()}
    modelo = _rxp.semente_motivos(perfil_chave)
    do_modelo = {m[0] for m in modelo}
    itens = []
    for chave, rotulo, _exige in modelo:
        cur = atuais.get(chave)
        if cur is None:
            itens.append({"id": f"criar:{chave}", "acao": "criar", "chave": chave,
                          "para": rotulo, "marcado": True,
                          "texto": f"acrescentar “{rotulo}”", "nota": ""})
            continue
        if not cur["ativo"]:
            itens.append({"id": f"ligar:{chave}", "acao": "ligar", "chave": chave,
                          "para": True, "marcado": False,
                          "texto": f"reativar “{cur['rotulo']}”",
                          "nota": "você desligou este motivo — marque só se quiser de volta"})
        if (cur["rotulo"] or "") != rotulo:
            a_mao = (cur["semeado_de"] or DO_DONO) == DO_DONO
            itens.append({"id": f"rotulo:{chave}", "acao": "rotulo", "chave": chave,
                          "para": rotulo, "marcado": not a_mao,
                          "texto": f"chamar “{cur['rotulo']}” de “{rotulo}”",
                          "nota": ("você já renomeou este motivo — marque só se quiser "
                                   "o nome do ramo" if a_mao else "")})
    for chave, cur in atuais.items():
        if chave in do_modelo or not cur["ativo"]:
            continue
        itens.append({"id": f"desligar:{chave}", "acao": "desligar", "chave": chave,
                      "para": False, "marcado": False,
                      "texto": f"desligar “{cur['rotulo']}” (não existe no modelo do ramo)",
                      "nota": "some da lista de escolha; quem já foi perdido por ele "
                              "continua mostrando o motivo na ficha"})
    return itens


def aplicar_motivos(c, conta_id: int, perfil_chave: str, aceitas) -> dict:
    """Aplica os itens marcados. O plano é RECALCULADO aqui, não recebido da tela —
    mesma razão de `funil_modelo.aplicar`: formulário velho manda id que já não vale."""
    from finance import raio_x_perfil as _rxp
    aceitas = set(aceitas or ())
    feito = {"criar": 0, "rotulo": 0, "ligar": 0, "desligar": 0}
    modelo = {m[0]: m for m in _rxp.semente_motivos(perfil_chave)}
    ordens = {m[0]: i * 10 for i, m in enumerate(_rxp.semente_motivos(perfil_chave))}
    for it in plano_motivos(c, conta_id, perfil_chave):
        if it["id"] not in aceitas:
            continue
        acao, chave = it["acao"], it["chave"]
        if acao == "criar":
            m = modelo[chave]
            c.execute("""insert into funil_motivos_perda
                           (conta_id, chave, rotulo, ordem, exige_descricao, semeado_de)
                         values (%s,%s,%s,%s,%s,%s)
                         on conflict (conta_id, chave) do nothing""",
                      (conta_id, chave, m[1], ordens[chave], m[2], perfil_chave))
        elif acao == "rotulo":
            c.execute("""update funil_motivos_perda set rotulo=%s, semeado_de=%s
                          where conta_id=%s and chave=%s""",
                      (it["para"], perfil_chave, conta_id, chave))
        elif acao in ("ligar", "desligar"):
            c.execute("update funil_motivos_perda set ativo=%s where conta_id=%s and chave=%s",
                      (acao == "ligar", conta_id, chave))
        feito[acao] += 1
    return feito


def carimbar_motivos(c, conta_id: int) -> int:
    """O carimbo das linhas anteriores à 254 — irmão de `funil_modelo.carimbar`,
    com a mesma regra: rótulo que bate com alguma semente conhecida é 'semente'; o
    que não bate com nenhuma foi o dono que escreveu."""
    from finance import raio_x_perfil as _rxp
    from finance.funil_modelo import DO_DONO
    linhas = c.execute(
        """select id, chave, rotulo from funil_motivos_perda
            where conta_id=%s and semeado_de is null""", (conta_id,)).fetchall()
    for mid, chave, rotulo in linhas:
        conhecidos = set()
        for perfil in _rxp.PERFIS:
            conhecidos |= {r for ch, r, _e in _rxp.semente_motivos(perfil) if ch == chave}
        conhecidos |= {r for ch, r in _rxp.MOTIVOS_TODOS if ch == chave}
        c.execute("update funil_motivos_perda set semeado_de=%s where id=%s",
                  ("semente" if (rotulo or "") in conhecidos else DO_DONO, mid))
    return len(linhas)


def exige_motivo(c, conta_id: int, etapa: str) -> bool:
    """Esta etapa exige motivo pra receber um lead? False é o padrão de toda conta."""
    r = c.execute("""select coalesce(exige_motivo, false) from funil_etapas
                      where conta_id=%s and chave=%s""", (conta_id, etapa)).fetchone()
    return bool(r[0]) if r else False


def tempo_no_funil(criado_em, perda_em) -> int | None:
    """Dias entre a entrada do lead e a perda — o "tempo total no funil" do pedido,
    calculado e não guardado (ver o docstring do módulo)."""
    if not criado_em or not perda_em:
        return None
    return max(0, int((perda_em - criado_em).total_seconds() // 86400))


# ------------------------------------------------------------------ a perda

def validar(c, conta_id: int, *, etapa_destino: str, motivo: str,
            descricao: str, perfil_chave: str = "recorrente") -> dict:
    """Pode perder assim? Devolve {ok} ou {ok: False, erro, ...}.

    Separado de `registrar` de propósito: os dois caminhos que levam a Perdido (o
    painel e o app do vendedor) precisam PERGUNTAR antes de mexer no lead, pra não
    mover o card e só então descobrir que falta o motivo.
    """
    if not exige_motivo(c, conta_id, etapa_destino):
        return {"ok": True}
    lista = {m["chave"]: m for m in motivos(c, conta_id, perfil_chave)}
    escolhido = (motivo or "").strip()
    if not escolhido or escolhido not in lista:
        return {"ok": False, "erro": "motivo_obrigatorio", "motivos": list(lista.values())}
    if lista[escolhido]["exige_descricao"] and not (descricao or "").strip():
        return {"ok": False, "erro": "descricao_obrigatoria", "motivo": escolhido}
    return {"ok": True}


def registrar(c, conta_id: int, lead_id: int, *, motivo: str = "", descricao: str = "",
              etapa_origem: str | None = None, agora: datetime | None = None) -> None:
    """Grava o que o documento pede que fique guardado.

    O VENDEDOR RESPONSÁVEL e o HISTÓRICO não entram aqui porque já estão: o dono do
    lead é `prospeccao.vendedor_id` (e mexer nele ao perder faria o placar do mês
    mudar de dono), e o histórico é `mensagens` + `funil_movimentos` +
    `funil_renovacoes`, que ninguém apaga. Guardar cópia dos dois seria criar uma
    segunda verdade pra sair de sincronia depois.

    `etapa_origem` é passada por quem chama porque só ele sabe de ONDE o lead saiu —
    na hora que esta função roda, o status já é o novo.
    """
    agora = agora or datetime.now(timezone.utc)
    m = (motivo or "").strip() or None
    d = (descricao or "").strip()[:1000] or None
    try:
        # SAVEPOINT: as colunas nasceram na 235, e perder um lead não pode depender
        # delas. O mesmo motivo do savepoint que já protege o histórico do funil.
        with c.transaction():
            c.execute("""update prospeccao
                            set perda_motivo = coalesce(%s, perda_motivo),
                                perda_descricao = coalesce(%s, perda_descricao),
                                perda_em = %s, perda_etapa = coalesce(%s, perda_etapa)
                          where id=%s and conta_id=%s""",
                      (m, d, agora, etapa_origem, lead_id, conta_id))
    except Exception:  # noqa: BLE001
        _log.warning("perda do lead %s não registrada", lead_id, exc_info=True)


def ficha(c, conta_id: int, lead_id: int) -> dict | None:
    """O que a tela mostra sobre a perda: motivo, texto, quando, de onde e quanto
    tempo ficou no funil. None quando o lead não foi perdido."""
    r = c.execute(
        """select p.perda_motivo, p.perda_descricao, p.perda_em, p.perda_etapa,
                  p.criado_em, coalesce(nullif(mb.nome,''), mb.email)
             from prospeccao p
             left join membros mb on mb.id = p.vendedor_id
            where p.id=%s and p.conta_id=%s""", (lead_id, conta_id)).fetchone()
    if not r or not (r[0] or r[2]):
        return None
    rot = {m["chave"]: m["rotulo"] for m in motivos(c, conta_id, so_ativos=False)}
    return {"motivo": r[0], "rotulo": rot.get(r[0] or "", r[0] or "sem motivo"),
            "descricao": r[1], "em": r[2], "etapa": r[3], "vendedor": r[5],
            "dias_no_funil": tempo_no_funil(r[4], r[2])}


# ------------------------------------------------------------------ a lista, editável

def salvar_motivo(c, conta_id: int, *, chave: str = "", rotulo: str = "",
                  ordem: int = 0, ativo: bool = True, exige_descricao: bool = False,
                  motivo_id: int | None = None) -> dict:
    """Cria ou atualiza um motivo da conta. Devolve {ok, chave} ou {ok: False, erro}.

    A CHAVE NUNCA MUDA depois de criada: ela é o que está gravado em
    `prospeccao.perda_motivo` de todo lead já perdido, e trocá-la apagaria o motivo
    do histórico de quem já foi. Renomear mexe só no rótulo, que é o que a tela lê.
    """
    rot = (rotulo or "").strip()[:80]
    if motivo_id:
        # RENOMEAR LIMPA O CARIMBO (migração 254): a partir daqui o nome é do dono,
        # e o bloco "o modelo do seu ramo" para de propor trocá-lo sem ele marcar.
        # Só o RÓTULO carimba — mudar ordem, ativo ou exigência não é dar nome.
        cur = c.execute("""update funil_motivos_perda
                              set rotulo = coalesce(nullif(%s,''), rotulo), ordem=%s,
                                  ativo=%s, exige_descricao=%s,
                                  semeado_de = case when nullif(%s,'') is null
                                                     or rotulo = %s then semeado_de
                                                    else %s end
                            where id=%s and conta_id=%s returning chave""",
                        (rot, ordem, ativo, exige_descricao,
                         rot, rot, _DO_DONO, motivo_id, conta_id)).fetchone()
        return {"ok": bool(cur), "chave": cur[0] if cur else None} if cur else {
            "ok": False, "erro": "nao_encontrado"}
    if not rot:
        return {"ok": False, "erro": "sem_rotulo"}
    ch = (chave or "").strip().lower() or _chave_de(rot)
    existe = c.execute("select 1 from funil_motivos_perda where conta_id=%s and chave=%s",
                       (conta_id, ch)).fetchone()
    if existe:
        return {"ok": False, "erro": "repetido"}
    c.execute("""insert into funil_motivos_perda (conta_id, chave, rotulo, ordem, exige_descricao)
                 values (%s,%s,%s,%s,%s)""", (conta_id, ch, rot, ordem, exige_descricao))
    return {"ok": True, "chave": ch}


def _chave_de(rotulo: str) -> str:
    """"Fechou com concorrente" -> "fechou_com_concorrente". Sem acento, sem espaço:
    a chave viaja em URL, em POST e no CHECK de relatório."""
    import re
    import unicodedata
    s = unicodedata.normalize("NFKD", rotulo).encode("ascii", "ignore").decode()
    s = re.sub(r"[^a-zA-Z0-9]+", "_", s).strip("_").lower()
    return (s or "motivo")[:40]


# ------------------------------------------------------------------ a reativação

def reativar(c, conta_id: int, lead_id: int, membro_id: int | None = None) -> dict:
    """O cliente voltou a falar: reabre o cadastro, se a etapa dele mandar (236).

    Devolve {reativado: False} quando não há o que fazer — que é o caso de toda
    conta hoje, porque `reativa_para` nasce NULL. Nunca levanta: isto roda DENTRO do
    caminho de entrada de mensagem, e uma mensagem de cliente não pode se perder
    porque a régua teve um problema (CLAUDE.md §0).

    O QUE NÃO É APAGADO: o motivo da perda, a data e a etapa de origem. "Preservando
    todo o histórico anterior" é literal — o lead volta com a história inteira,
    inclusive a de ter sido perdido uma vez, que é o que o dono quer analisar depois.
    """
    try:
        with c.transaction():
            r = c.execute(
                """select p.status, e.reativa_para, e.rotulo
                     from prospeccao p
                     join funil_etapas e on e.conta_id = p.conta_id and e.chave = p.status
                    where p.id=%s and p.conta_id=%s""", (lead_id, conta_id)).fetchone()
            if not r or not r[1]:
                return {"reativado": False}
            de, para = r[0], r[1]
            if de == para:
                return {"reativado": False}
            # o destino tem que existir: etapa renomeada ou removida deixaria o lead
            # num status que nenhuma coluna do quadro mostra, e ele sumiria da tela
            if not c.execute("select 1 from funil_etapas where conta_id=%s and chave=%s",
                             (conta_id, para)).fetchone():
                _log.warning("reativa_para '%s' da conta %s não existe", para, conta_id)
                return {"reativado": False}
            c.execute("""update prospeccao set status=%s, estagio='lead',
                             temperatura='quente', atualizado_em=now()
                          where id=%s and conta_id=%s""", (para, lead_id, conta_id))
            from finance import funil_regua as _fr
            _fr.registrar_movimento(c, conta_id, lead_id, de, para, "reativado", membro_id)
        return {"reativado": True, "de": de, "para": para}
    except Exception:  # noqa: BLE001
        _log.warning("reativação do lead %s falhou", lead_id, exc_info=True)
        return {"reativado": False}
