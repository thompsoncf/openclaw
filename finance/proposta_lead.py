"""A proposta precisa de um card no funil — e quem garante isso é aqui.

O PROBLEMA
O gatilho `orcamento_enviado` procura a proposta por `prospeccao.orcamento_id`. Uma
proposta que não está ligada a lead nenhum não tem card pra mover, e aí não importa
quantos canais registrem o envio: o funil continua mentindo.

Medido na conta 34 (PRIME EVENTOS) em 19/08/2026, com o gatilho ligado desde as
14:02 — 4 propostas, ZERO ligadas a lead, e as 4 com um lead de mesmo telefone
esperando do outro lado. O único card que chegou em "Proposta" foi arrastado na mão.

AS TRÊS PORTAS, NESTA ORDEM
 1. o vendedor escolheu o cliente na busca da Base → o id vem junto e amarra ali;
 2. não escolheu, mas existe UM lead com aquele telefone ou e-mail → amarra nele;
 3. não existe nenhum → o card NASCE, com os dados da ficha que ele preencheu.

QUANDO A PORTA 3 ABRE: NO ENVIO, NUNCA NO RASCUNHO
Criar card a cada proposta salva encheria o funil de negócio que não existe — meia
proposta montada, teste, orçamento que o vendedor abandonou no meio. Proposta que
SAIU é negociação de verdade. Por isso o gatilho desta criação é o registro de
envio, e não o salvar.

E O EMPATE NÃO É ADIVINHADO
Dois leads com o mesmo telefone: não amarra em nenhum. Amarrar no errado move o card
de outra pessoa e enterra a proposta no lead trocado — um silêncio que ninguém
descobre. Ficar sem vínculo é visível, e visível se conserta.
"""
from __future__ import annotations

import logging
import re

_log = logging.getLogger(__name__)

# Etapa onde o card entra quando a proposta sai. É a chave fixa que o funil já usa;
# se a conta renomeou a coluna ("Proposta" virou "Orçamento na mão"), o rótulo muda
# e a chave continua.
ETAPA_PROPOSTA = "proposta"

# Status que a chegada da proposta NÃO pode atropelar: negócio fechado ou perdido é
# desfecho, e desfecho não volta pro meio do funil por causa de um reenvio.
_INTOCAVEIS = ("ganho", "perdido")


def _so_digitos(v: str | None) -> str:
    return re.sub(r"\D", "", v or "")


def _fim(v: str | None, n: int = 8) -> str:
    """Os últimos N dígitos do telefone.

    Comparar o número inteiro não funciona: a mesma pessoa aparece como
    "86995167171" no orçamento e "5586995167171" no lead — DDI que um lado tem e o
    outro não. E o nono dígito do celular entra e sai conforme quem digitou. O fim
    do número é a parte que todo mundo escreve igual."""
    d = _so_digitos(v)
    return d[-n:] if len(d) >= n else ""


def ligar(c, conta_id: int, lead_id: int, orcamento_id: int,
          membro_id: int | None = None) -> bool:
    """Amarra a proposta ao lead e traz o card pra etapa de proposta.

    `orcamento_id is null` no WHERE é a trava que impede roubar um lead que já tem
    outra proposta em cima. Devolve False nesse caso — quem chamou decide se avisa;
    o que não pode é sobrescrever em silêncio.

    Recebe a CONEXÃO, e não o pool: quem chama costuma já estar numa transação
    (salvar a proposta), e abrir outra aqui criaria um commit que sobrevive ao
    rollback do chamador."""
    r = c.execute(
        "select status from prospeccao where id=%s and conta_id=%s and orcamento_id is null",
        (lead_id, conta_id)).fetchone()
    if not r:
        return False
    status = r[0]
    novo = status if status in _INTOCAVEIS else ETAPA_PROPOSTA
    c.execute(
        "update prospeccao set orcamento_id=%s, status=%s, atualizado_em=now() "
        "where id=%s and conta_id=%s and orcamento_id is null",
        (orcamento_id, novo, lead_id, conta_id))
    if novo != status:
        try:
            from finance import funil_regua as _fr
            with c.transaction():   # savepoint: o histórico não derruba o vínculo
                _fr.registrar_movimento(c, conta_id, lead_id, status, novo,
                                        "orcamento", membro_id)
        except Exception:  # noqa: BLE001
            _log.warning("não registrei o movimento do lead %s", lead_id, exc_info=True)
    return True


def _candidatos(c, conta_id: int, telefone: str, email: str) -> list[int]:
    """Leads da conta que batem com este cliente. Mais de um = empate, e empate a
    gente não resolve no chute."""
    achados: list[int] = []
    fim = _fim(telefone)
    if fim:
        achados += [r[0] for r in c.execute(
            "select id from prospeccao where conta_id=%s and estagio='lead' "
            " and right(regexp_replace(coalesce(whatsapp,''),'\\D','','g'),8)=%s "
            "union "
            "select id from prospeccao where conta_id=%s and estagio='lead' "
            " and right(regexp_replace(coalesce(telefone,''),'\\D','','g'),8)=%s",
            (conta_id, fim, conta_id, fim)).fetchall()]
    mail = (email or "").strip().lower()
    if mail:
        achados += [r[0] for r in c.execute(
            "select id from prospeccao where conta_id=%s and estagio='lead' "
            " and lower(coalesce(email,''))=%s", (conta_id, mail)).fetchall()]
    return sorted(set(achados))


def _criar(c, conta_id: int, orc: dict, membro_id: int | None) -> int:
    """O card nasce já em "Proposta" — que é onde ele de fato está.

    Nascer em "Novo" e esperar o gatilho mover seria mais "correto" no papel e pior
    na prática: o card apareceria na primeira coluna com uma proposta já enviada em
    cima, e alguém ia trabalhar um lead que não precisa de primeiro contato."""
    nome = (orc.get("empresa") or orc.get("cliente") or "Cliente sem nome").strip()
    return c.execute(
        """insert into prospeccao (conta_id, vendedor_id, empresa, contato, cnpj,
              whatsapp, telefone, email, cidade, uf, segmento, origem, status,
              estagio, orcamento_id, criado_por, atualizado_em)
           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,'proposta',%s,'lead',%s,%s,now())
           returning id""",
        (conta_id, membro_id, nome, orc.get("cliente") or "", orc.get("cnpj") or "",
         orc.get("whatsapp") or "", orc.get("telefone") or "", orc.get("email") or "",
         orc.get("cidade") or "", orc.get("uf") or "", orc.get("segmento") or "",
         ETAPA_PROPOSTA, orc.get("id"), membro_id)).fetchone()[0]


def garantir(pool, conta_id: int, orcamento_id: int,
             membro_id: int | None = None) -> dict:
    """Garante que esta proposta tem card no funil. Chamado NO ENVIO.

    Devolve {"lead_id": int|None, "como": str} — `como` é um de:
      'ja_tinha'  o orçamento já estava ligado;
      'ligado'    achou um lead só com aquele telefone/e-mail e amarrou;
      'criado'    não havia nenhum, o card nasceu;
      'empate'    mais de um lead bate — não amarrou nada, de propósito;
      'sem_dados' a proposta não tem telefone, e-mail nem nome pra trabalhar.

    Nunca levanta: é chamado no caminho do envio, e proposta entregue não pode virar
    erro na tela porque o funil não conseguiu se organizar."""
    try:
        with pool.connection() as c:
            r = c.execute(
                """select o.id, o.cliente, o.empresa, o.cnpj, o.whatsapp, o.telefone,
                          o.email, o.cidade, o.uf, o.segmento,
                          (select p.id from prospeccao p
                            where p.conta_id=o.conta_id and p.orcamento_id=o.id limit 1)
                     from orcamentos o where o.id=%s and o.conta_id=%s""",
                (orcamento_id, conta_id)).fetchone()
            if not r:
                return {"lead_id": None, "como": "sem_dados"}
            orc = {"id": r[0], "cliente": r[1], "empresa": r[2], "cnpj": r[3],
                   "whatsapp": r[4], "telefone": r[5], "email": r[6], "cidade": r[7],
                   "uf": r[8], "segmento": r[9]}
            if r[10]:
                return {"lead_id": r[10], "como": "ja_tinha"}

            achados = _candidatos(c, conta_id, orc["whatsapp"] or orc["telefone"],
                                  orc["email"])
            if len(achados) > 1:
                _log.info("proposta %s: %d leads batem — não amarrei nenhum",
                          orcamento_id, len(achados))
                return {"lead_id": None, "como": "empate"}
            if len(achados) == 1:
                if ligar(c, conta_id, achados[0], orcamento_id, membro_id):
                    c.commit()
                    return {"lead_id": achados[0], "como": "ligado"}
                # o lead achado já tem outra proposta: não rouba, e também não cria
                # um card duplicado da mesma pessoa.
                return {"lead_id": None, "como": "empate"}

            if not (orc["empresa"] or orc["cliente"]):
                return {"lead_id": None, "como": "sem_dados"}
            novo = _criar(c, conta_id, orc, membro_id)
            c.commit()
            return {"lead_id": novo, "como": "criado"}
    except Exception as ex:  # noqa: BLE001 — o funil não derruba o envio
        _log.warning("proposta %s: não consegui garantir o card: %s: %s",
                     orcamento_id, type(ex).__name__, ex)
        return {"lead_id": None, "como": "sem_dados"}
