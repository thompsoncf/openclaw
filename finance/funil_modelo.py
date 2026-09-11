"""O MODELO DE FUNIL DO RAMO: as colunas que a conta recebe, e como adotá-las depois.

Aprovado pelo dono em 11/09/2026, junto com as seis colunas de eventos:

    Novo · Contatado · Follow-up · Agendado Visita · Proposta · Perdido

("Fechado" é a sétima etapa e não é coluna — sai do quadro e vira compromisso na
Agenda. Regra 6 do FLUXO_FINAL_FUNIL_PRIME_EVENTOS_DESENVOLVEDOR_V3.)

O QUE ESTE MÓDULO RESOLVE
Medido em produção em 11/09/2026: a Prime (34, eventos) levou meses reconstruindo o
funil dela na mão até chegar nas oito colunas que usa hoje — com "Follow-up" e
"Agendado Visita", que nenhuma outra conta tem. A Doce Mell (35) é a SEGUNDA conta de
eventos, nasceu DEPOIS disso, e mesmo assim recebeu as seis colunas genéricas
(Novo · Contatado · Qualificado · Proposta · Ganho · Perdido). O que o dono de uma
conta aprendeu não alcançava a próxima conta do mesmo ramo, porque a semente era uma
lista fixa em `web.painel_prospeccao._ETAPAS_PADRAO`, igual pra todo mundo.

Agora a semente vem do perfil (`raio_x_perfil.etapas_padrao`) e este módulo faz as
duas coisas que faltavam: semear quem está nascendo, e propor a adoção a quem já
existe.

AS DUAS REGRAS QUE ESTE MÓDULO NÃO QUEBRA (CLAUDE.md §0)

1. **Nunca apaga etapa.** Uma etapa da conta que não está no modelo não é removida:
   é proposta pra SAIR DO QUADRO (migração 238). Os leads dela continuam no banco,
   na busca, nos relatórios e na ficha — some a coluna, não o cadastro. Apagar a
   etapa deixaria 5 leads da Prime ("Evento A Realizar") apontando pra uma etapa
   inexistente.

2. **Nunca mexe em lead.** Nenhum `update prospeccao` sai daqui. Adotar o modelo é
   mudança de TELA; se mudasse o status dos leads, o histórico do funil passaria a
   contar um movimento que ninguém fez.

E UMA TERCEIRA, QUE É SOBRE CONFIANÇA
O plano é uma PROPOSTA com caixas, não um botão que reescreve o funil. O rótulo que
o dono já trocou à mão vem DESMARCADO: a conta 3 chama 'perdido' de "Entregue" e a
conta 34 chama 'ganho' de "Evento Realizado" — renomear isso por conta própria seria
apagar uma decisão de quem usa a tela todo dia, em nome de um padrão que ele nunca
pediu.
"""
from __future__ import annotations

from finance import raio_x_perfil as _rxp

#: o que o modelo pode mudar numa etapa que já existe. Ordem = ordem na tela.
ACOES = ("criar", "rotulo", "ordem", "quadro", "agenda")

_COLS = ("id", "chave", "rotulo", "ordem", "fixa", "sai_do_quadro", "agenda_ao_entrar")


def _atuais(c, conta_id: int) -> dict:
    linhas = c.execute(
        """select id, chave, rotulo, ordem, fixa,
                  coalesce(sai_do_quadro,false), coalesce(agenda_ao_entrar,false)
             from funil_etapas where conta_id=%s order by ordem, id""",
        (conta_id,)).fetchall()
    return {r[1]: dict(zip(_COLS, r)) for r in linhas}


def _leads(c, conta_id: int) -> dict:
    return dict(c.execute(
        """select status, count(*) from prospeccao
            where conta_id=%s and estagio='lead' group by status""", (conta_id,)).fetchall())


def semear(c, conta_id: int, chave_perfil: str) -> int:
    """Grava as etapas do modelo do ramo. Só faz sentido em conta sem etapa nenhuma;
    o `on conflict do nothing` garante que chamar de novo não reescreve nada."""
    n = 0
    for chave, rotulo, ordem, fixa, sai, agenda in _rxp.etapas_padrao(chave_perfil):
        n += c.execute(
            """insert into funil_etapas (conta_id, chave, rotulo, ordem, fixa,
                                         sai_do_quadro, agenda_ao_entrar)
                    values (%s,%s,%s,%s,%s,%s,%s)
               on conflict (conta_id, chave) do nothing""",
            (conta_id, chave, rotulo, ordem, fixa, sai, agenda)).rowcount
    return n


def _generico(chave: str) -> str | None:
    """O rótulo que esta chave teria no funil genérico — o que a conta recebeu ao
    nascer, antes de o modelo por ramo existir. Serve pra distinguir "nunca mexeu"
    de "renomeou à mão": só o primeiro caso é renomeado sem perguntar duas vezes."""
    for ch, rot, *_ in _rxp.ETAPAS_GENERICAS:
        if ch == chave:
            return rot
    return None


def _item(acao, chave, *, de, para, leads=0, marcado=True, texto="", nota=""):
    return {"id": f"{acao}:{chave}", "acao": acao, "chave": chave, "de": de,
            "para": para, "leads": leads, "marcado": marcado, "texto": texto, "nota": nota}


def plano(c, conta_id: int, chave_perfil: str) -> list[dict]:
    """O que o modelo do ramo mudaria nesta conta. Lista vazia = já está no modelo.

    Cada item é UMA mudança em UMA etapa, com `marcado` dizendo se ela vem pré-aceita
    na tela. Nada aqui escreve no banco.
    """
    modelo = _rxp.etapas_padrao(chave_perfil)
    atuais = _atuais(c, conta_id)
    nleads = _leads(c, conta_id)
    do_modelo = {m[0] for m in modelo}
    itens: list[dict] = []

    for chave, rotulo, ordem, _fixa, sai, agenda in modelo:
        cur = atuais.get(chave)
        if cur is None:
            # "etapa" e não "coluna" quando ela nasce fora do quadro: chamar de
            # coluna o que nunca vai virar coluna é prometer errado na própria tela
            itens.append(_item(
                "criar", chave, de=None, para=rotulo,
                texto=(f"criar a etapa “{rotulo}”, fora do quadro" if sai
                       else f"criar a coluna “{rotulo}”")))
            continue
        if (cur["rotulo"] or "") != rotulo:
            # rótulo que a conta já trocou à mão não é reescrito sem o dono marcar
            a_mao = (cur["rotulo"] or "") != (_generico(chave) or "")
            itens.append(_item(
                "rotulo", chave, de=cur["rotulo"], para=rotulo,
                leads=nleads.get(chave, 0), marcado=not a_mao,
                texto=f"chamar “{cur['rotulo']}” de “{rotulo}”",
                nota=("você já renomeou esta etapa — marque só se quiser o nome do ramo"
                      if a_mao else "")))
        if int(cur["ordem"] or 0) != int(ordem):
            itens.append(_item("ordem", chave, de=cur["ordem"], para=ordem,
                               leads=nleads.get(chave, 0),
                               texto=f"pôr “{cur['rotulo']}” na posição do modelo"))
        if bool(cur["sai_do_quadro"]) != bool(sai):
            itens.append(_item("quadro", chave, de=cur["sai_do_quadro"], para=sai,
                               leads=nleads.get(chave, 0),
                               texto=(f"tirar “{cur['rotulo']}” do quadro"
                                      if sai else f"trazer “{cur['rotulo']}” de volta ao quadro")))
        if bool(cur["agenda_ao_entrar"]) != bool(agenda):
            itens.append(_item("agenda", chave, de=cur["agenda_ao_entrar"], para=agenda,
                               leads=nleads.get(chave, 0),
                               texto=(f"criar o compromisso na Agenda ao entrar em “{cur['rotulo']}”"
                                      if agenda else
                                      f"parar de criar compromisso ao entrar em “{cur['rotulo']}”")))

    # etapa que a conta tem e o modelo não. NÃO se apaga (CLAUDE.md §0): sai do
    # quadro, que é o que o dono pediu pras colunas de pós-venda da Prime.
    #
    # E LEVA A AGENDA JUNTO, quando o ramo usa a ponte. Medido na Prime: "Evento A
    # Realizar" tem 5 leads com data de festa e não existe no modelo. Propor só a
    # saída do quadro tiraria as 5 festas da tela sem pôr nenhuma na agenda — que é
    # exatamente o erro que a migração 238 foi escrita pra evitar, e o que o aviso
    # dela chama de "trocar uma coluna cheia por uma agenda vazia".
    agenda_no_ramo = any(m[5] for m in modelo if m[4])
    for chave, cur in atuais.items():
        if chave in do_modelo or cur["sai_do_quadro"]:
            continue
        n = nleads.get(chave, 0)
        # a agenda vem ANTES da saída na lista: é a ordem em que o aviso da 239
        # manda ligar ("Ligue a segunda antes da primeira"), e ler a tela de cima
        # pra baixo tem que ser ler a ordem certa
        if agenda_no_ramo and not cur["agenda_ao_entrar"]:
            itens.append(_item(
                "agenda", chave, de=False, para=True, leads=n,
                texto=f"criar o compromisso na Agenda ao entrar em “{cur['rotulo']}”",
                nota="marque junto com a linha abaixo: é o que faz o evento aparecer "
                     "na Agenda em vez de sumir de vista"))
        itens.append(_item(
            "quadro", chave, de=False, para=True, leads=n,
            texto=f"tirar “{cur['rotulo']}” do quadro (não existe no modelo do ramo)",
            nota=(f"{n} lead{'s' if n != 1 else ''} continuam no cadastro, na busca e "
                  f"nos relatórios — some a coluna, não o lead" if n else
                  "nenhum lead aqui hoje")))
    return itens


def aplicar(c, conta_id: int, chave_perfil: str, aceitas) -> dict:
    """Aplica os itens do plano cujos ids o dono marcou. Devolve a contagem por ação.

    O plano é RECALCULADO aqui, e não recebido da tela: um formulário velho (outra
    aba, o botão de voltar) mandaria ids de mudanças que não existem mais, e aplicar
    o que a tela disse em vez do que o banco mostra é como se renomeia uma etapa que
    alguém acabou de renomear pra outra coisa.
    """
    aceitas = set(aceitas or ())
    feito = {a: 0 for a in ACOES}
    for it in plano(c, conta_id, chave_perfil):
        if it["id"] not in aceitas:
            continue
        acao, chave = it["acao"], it["chave"]
        if acao == "criar":
            m = next(x for x in _rxp.etapas_padrao(chave_perfil) if x[0] == chave)
            c.execute("""insert into funil_etapas (conta_id, chave, rotulo, ordem, fixa,
                                                   sai_do_quadro, agenda_ao_entrar)
                              values (%s,%s,%s,%s,%s,%s,%s)
                         on conflict (conta_id, chave) do nothing""",
                      (conta_id, m[0], m[1], m[2], m[3], m[4], m[5]))
        elif acao == "rotulo":
            c.execute("update funil_etapas set rotulo=%s where conta_id=%s and chave=%s",
                      (it["para"], conta_id, chave))
        elif acao == "ordem":
            c.execute("update funil_etapas set ordem=%s where conta_id=%s and chave=%s",
                      (int(it["para"]), conta_id, chave))
        elif acao == "quadro":
            c.execute("update funil_etapas set sai_do_quadro=%s where conta_id=%s and chave=%s",
                      (bool(it["para"]), conta_id, chave))
        elif acao == "agenda":
            c.execute("update funil_etapas set agenda_ao_entrar=%s where conta_id=%s and chave=%s",
                      (bool(it["para"]), conta_id, chave))
        feito[acao] += 1
    return feito
