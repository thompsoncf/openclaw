"""O lembrete de segunda das obras: o que trava cada casa e o que já dá pra cobrar.

Desenho aprovado pelo dono em 25/09/2026 (docs/mockups/nicho_construcao.html,
seção 06): "o agente lembra o Pablo toda segunda até o passo andar". Até aqui a
lista das pendências só existia no prompt (finance/obras.py, `_bloco_das_casas`)
— ou seja, só aparecia se o Pablo puxasse conversa. Casa pronta parada esperando
habite-se é dinheiro parado, e ninguém puxa conversa pra ouvir isso.

O QUE VAI NA MENSAGEM, e só se tiver pelo menos uma linha:
- casa pronta: o passo que trava o dinheiro (finance/obra_venda.py, `caminho`);
- qualquer casa: os alertas de prazo (CNO, alvará, certidões, avaliação, registro);
- reforma: as parcelas que a etapa feita já liberou e continuam em aberto;
- no fim, quanto está parado em casa que a Caixa ainda não pagou.
Semana sem pendência não manda nada: lembrete que diz "tudo certo" ensina a
ignorar o próximo.

POR ONDE SAI, pra cada dono ou gestor da conta (nesta ordem):
1. WhatsApp com texto livre, se ele falou com o Zaq nas últimas 23 h (a janela de
   24 h da Meta, com uma hora de folga). A janela é lida da memória da conversa
   (`memoria_conversa`, 'wa:<membro>'), que anda a cada mensagem dele.
2. WhatsApp por TEMPLATE, fora da janela — precisa do template aprovado
   (scripts/criar_template_obras.py) e da env TWILIO_TMPL_OBRAS_SID. Sem ela,
   pula pro próximo.
3. Telegram (só o dono tem o canal de aviso por Telegram).
4. E-mail.
Medido em produção em 26/09/2026: o dono da PX2 não tem Telegram e falou com o
Zaq pela última vez 5 dias antes — sem o template, o lembrete dele sai por e-mail.

UMA VEZ POR SEMANA POR PESSOA. A trava é a linha em `obras_lembretes` (migração
365), gravada ANTES de mandar: com 2 workers só um consegue inserir, e se o envio
falhar é melhor perder um lembrete do que repetir o mesmo a cada 2 minutos (o
mesmo critério do aprovacao_aviso e do wa_silencio).

Roda no ticker de fundo do web (~2 min), e só faz alguma coisa na segunda, das 8h
às 20h de Brasília.
"""
from __future__ import annotations

import logging
import os

_log = logging.getLogger("openclaw.obras_lembrete")

_DIA = 0            # segunda (date.weekday)
_HORA_INICIO = 8
_HORA_FIM = 20
_JANELA_HORAS = 23  # a janela de 24 h da Meta, com folga
_MAX_VARIAVEL = 700  # a variável do template não pode ser um romance


def _brl(centavos: int) -> str:
    return f"R$ {centavos / 100:,.2f}".replace(",", "~").replace(".", ",").replace("~", ".")


def pendencias(pool, conta_id: int, hoje=None) -> dict:
    """{'itens': [frases], 'parado': centavos}. Itens vazios = nada a lembrar."""
    from . import obras as ob
    from . import obra_venda as ov
    obras = [o for o in ob.listar_obras(pool, conta_id)
             if o["status"] not in ("arquivada", "entregue")]
    itens = []
    for o in obras:
        if o["tipo"] == "casa":
            sit = ov.situacao_da_casa(pool, conta_id, o, hoje)
            partes = []
            t = sit["trava"]
            if o["pct"] == 100 and t:
                partes.append("falta cair o crédito da Caixa" if t["chave"] == "creditado"
                              else f"o que trava é {t['nome'].lower()}")
            partes += sit["alertas"][:2]
            if partes:
                itens.append(f"{o['nome']}: " + "; ".join(p.rstrip(".") for p in partes) + ".")
        elif o["tipo"] == "reforma":
            try:
                from . import obra_reforma as orf
                completa = ob.obter_obra(pool, conta_id, o["id"])
                for p in orf.parcelas_liberadas(pool, conta_id, completa):
                    itens.append(f"{o['nome']}: a parcela \"{p['rotulo']}\" "
                                 f"({_brl(p['valor_centavos'])}) já pode ser cobrada — "
                                 f"{p['etapa'].lower()} ficou pronta.")
            except Exception as e:  # noqa: BLE001 — sem a 355, sem parcela
                _log.info("obras_lembrete: parcelas da obra %s: %s", o["id"], e)
    parado = ov.parado_em_casas(pool, conta_id, obras) if itens else 0
    return {"itens": itens, "parado": parado}


def texto(nome: str, pend: dict) -> str:
    """A mensagem inteira (WhatsApp dentro da janela, Telegram e e-mail)."""
    primeiro = (nome or "").split()[0].title() if (nome or "").strip() else ""
    linhas = [f"Bom dia{', ' + primeiro if primeiro else ''}! 🏗️ O que está parado nas obras "
              "nesta semana:", ""]
    linhas += [f"• {i}" for i in pend["itens"]]
    if pend["parado"]:
        linhas += ["", f"Tem {_brl(pend['parado'])} gastos em casa que a Caixa ainda não pagou."]
    linhas += ["", "Quando um papel sair ou a parcela for paga, me conta aqui "
               "(\"saiu o habite-se da casa 2\") que eu atualizo."]
    return "\n".join(linhas)


def linha_do_template(pend: dict) -> str:
    """A variável {{2}} do template: uma linha só (a Meta recusa quebra de linha
    e sequência de espaços dentro da variável), cortada num tamanho razoável."""
    txt = " ".join(i.rstrip(".") + "." for i in pend["itens"])
    if pend["parado"]:
        txt += f" Parado em casa: {_brl(pend['parado'])}."
    txt = " ".join(txt.split())
    return txt if len(txt) <= _MAX_VARIAVEL else txt[:_MAX_VARIAVEL - 1].rstrip() + "…"


def semana(agora) -> str:
    ano, sem, _ = agora.isocalendar()
    return f"{ano}-W{sem:02d}"


def _destinatarios(c, conta_id: int) -> list[dict]:
    cols = ("id", "nome", "papel", "email", "whatsapp", "whatsapp_id")
    return [dict(zip(cols, r)) for r in c.execute(
        """select id, nome, papel, email, whatsapp, whatsapp_id from membros
            where conta_id=%s and coalesce(ativo, true) and papel in ('dono', 'gestor')
            order by (papel = 'dono') desc, id""", (conta_id,)).fetchall()]


def _janela_aberta(c, membro_id: int) -> bool:
    # a memória da conversa não tem DDL no repositório (nasceu direto no banco):
    # sem ela, a janela conta como fechada e o lembrete tenta o template.
    if c.execute("select to_regclass('public.memoria_conversa')").fetchone()[0] is None:
        return False
    r = c.execute(
        """select 1 from memoria_conversa where conversa_id = %s
              and atualizado_em > now() - make_interval(hours => %s)""",
        (f"wa:{membro_id}", _JANELA_HORAS)).fetchone()
    return r is not None


def _numero(m: dict) -> str:
    return (m.get("whatsapp") or m.get("whatsapp_id") or "").replace("whatsapp:", "").strip()


def _entregar(pool, conta_id: int, m: dict, pend: dict, empresa: str) -> str:
    """Manda pra uma pessoa e devolve o canal que funcionou ('' = nenhum)."""
    msg = texto(m["nome"], pend)
    numero = _numero(m)
    if numero:
        from . import whatsapp_twilio as wt
        remetente = (os.environ.get("TWILIO_WHATSAPP_FROM") or "").strip()
        with pool.connection() as c:
            aberta = _janela_aberta(c, m["id"])
        if aberta:
            r = wt.enviar_texto(remetente, numero, msg)
            if r.get("ok"):
                return "whatsapp"
            _log.info("obras_lembrete: WhatsApp livre falhou (membro %s): %s", m["id"], r.get("erro"))
        sid = (os.environ.get("TWILIO_TMPL_OBRAS_SID") or "").strip()
        if sid:
            r = wt.enviar_template(remetente, numero, sid,
                                   {"1": empresa or "sua empresa", "2": linha_do_template(pend)})
            if r.get("ok"):
                return "whatsapp_template"
            _log.info("obras_lembrete: template falhou (membro %s): %s", m["id"], r.get("erro"))
    if m["papel"] == "dono":
        from . import notificar
        if notificar.enviar_para_dono(pool, conta_id, msg):
            return "telegram"
    if m.get("email") and "@" in m["email"]:
        try:
            from .email_sender import enviar_aviso
            from .obras import LINK_OBRAS
            if enviar_aviso(m["email"], "O que está parado nas obras", msg, m["nome"] or "",
                            link=LINK_OBRAS, link_texto="Abrir as obras"):
                return "email"
        except Exception as e:  # noqa: BLE001
            _log.info("obras_lembrete: e-mail falhou (membro %s): %s", m["id"], e)
    return ""


def rodar(pool, agora=None) -> int:
    """Uma passada do ticker. Devolve quantos lembretes saíram. Nunca levanta."""
    try:
        if agora is None:
            from .agenda import agora_brt
            agora = agora_brt()
        if agora.weekday() != _DIA or not (_HORA_INICIO <= agora.hour < _HORA_FIM):
            return 0
        with pool.connection() as c:
            if c.execute("select to_regclass('public.obras_lembretes')").fetchone()[0] is None:
                return 0
            contas = [r for r in c.execute(
                """select distinct o.conta_id,
                          coalesce(nullif(ct.nome_fantasia, ''), ct.nome, '')
                     from obras o join contas ct on ct.id = o.conta_id
                    where o.status in ('em_obra', 'pronta', 'vendida')""").fetchall()]
    except Exception as e:  # noqa: BLE001 — banco sem a 351 não derruba o ticker
        _log.info("obras_lembrete: não deu pra listar as contas: %s", e)
        return 0
    sem = semana(agora)
    enviados = 0
    for conta_id, empresa in contas:
        try:
            pend = pendencias(pool, conta_id, agora.date())
            if not pend["itens"]:
                continue
            with pool.connection() as c:
                pessoas = _destinatarios(c, conta_id)
            for m in pessoas:
                with pool.connection() as c:
                    novo = c.execute(
                        """insert into obras_lembretes (conta_id, membro_id, semana)
                           values (%s, %s, %s) on conflict do nothing""",
                        (conta_id, m["id"], sem)).rowcount
                    c.commit()
                if not novo:
                    continue
                canal = _entregar(pool, conta_id, m, pend, empresa)
                with pool.connection() as c:
                    c.execute("""update obras_lembretes set canal=%s, itens=%s
                                  where conta_id=%s and membro_id=%s and semana=%s""",
                              (canal or "nenhum", len(pend["itens"]), conta_id, m["id"], sem))
                    c.commit()
                if canal:
                    enviados += 1
                    _log.info("obras_lembrete: conta %s, membro %s, por %s (%d item(ns))",
                              conta_id, m["id"], canal, len(pend["itens"]))
        except Exception as e:  # noqa: BLE001
            _log.info("obras_lembrete: conta %s falhou: %s: %s", conta_id, type(e).__name__, e)
    return enviados
