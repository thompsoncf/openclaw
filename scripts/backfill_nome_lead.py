"""Backfill: troca o número cru pelo NOME nos leads que nasceram antes do ajuste.

O botão "Levar para o lead" gravava `empresa = contato_ref` — o número como o
WhatsApp entrega ("558694867388") — mesmo com o nome do contato já guardado no
banco. Quem foi criado assim continua no funil chamado "5586…". Este script
renomeia esses leads usando a MESMA escada do painel:

    1. agenda do celular conectado (wa_contatos.nome, casando pelos 8 últimos
       dígitos — ignora o 9 extra e variações de DDI)
    2. nome guardado na conversa (conversas.contato_nome: pushName do WhatsApp
       ou o remetente do e-mail)

Só mexe em lead cujo `empresa` é SÓ dígitos (ou o número com +): quem tem nome
de verdade não é tocado, nem que o nome pareça estranho. Sem nome nas duas
fontes, o lead fica como está — o script não inventa nome nem apaga o que tem.
`contato` (nome da pessoa) é preenchido junto quando está vazio.

Rodar no Render Shell:
    python -m scripts.backfill_nome_lead            # mostra o que faria
    python -m scripts.backfill_nome_lead --aplicar  # grava
"""
import sys

from db.conexao import get_pool

# lead que nunca foi batizado: o nome é o próprio número (com ou sem +/espaços)
_SO_NUMERO = r"^\+?[0-9 ()\-]+$"

# O outro jeito de um lead nascer sem nome: quando nem a agenda nem o pushName
# tinham chegado, a escada do inbound grava este texto fixo. Ele fica pra sempre,
# mesmo com o nome aparecendo na conversa minutos depois — foi o que aconteceu na
# Doce Mell. A correção contínua está em `_batiza_lead_pendente`; aqui é o
# retroativo, pras contas que já acumularam.
_PROVISORIO = "Contato WhatsApp"


def _candidatos(c):
    """(id, conta_id, empresa, nome_novo, fonte) dos leads que dá pra renomear."""
    return c.execute(
        r"""select p.id, p.conta_id, p.empresa,
                   coalesce(nullif(wa.nome, ''), nullif(cv.contato_nome, '')) as nome_novo,
                   case when nullif(wa.nome, '') is not null then 'agenda' else 'conversa' end as fonte,
                   p.contato
              from prospeccao p
              left join conversas cv
                     on cv.prospeccao_id = p.id and coalesce(cv.contato_nome, '') <> ''
              left join wa_contatos wa
                     on wa.conta_id = p.conta_id
                    and wa.numero8 = right(regexp_replace(coalesce(p.whatsapp, p.telefone, ''),
                                                          '\D', '', 'g'), 8)
             where ((p.empresa ~ %s
                     and length(regexp_replace(p.empresa, '\D', '', 'g')) >= 8)
                    or p.empresa = %s)
               and coalesce(nullif(wa.nome, ''), nullif(cv.contato_nome, '')) is not null
             order by p.conta_id, p.id""", (_SO_NUMERO, _PROVISORIO)).fetchall()


def main():
    aplicar = "--aplicar" in sys.argv
    pool = get_pool()
    with pool.connection() as c:
        linhas = _candidatos(c)

        print(f"{len(linhas)} lead(s) com número no lugar do nome e nome disponível.")
        for (lead_id, conta_id, empresa, nome_novo, fonte, contato) in linhas:
            print(f"  conta {conta_id} · lead {lead_id}: {empresa!r} -> {nome_novo!r} ({fonte})")
        if not linhas:
            print("Nada a fazer.")
            return
        if not aplicar:
            print("\nNada foi gravado. Rode de novo com --aplicar pra valer.")
            return

        for (lead_id, _conta, _empresa, nome_novo, _fonte, contato) in linhas:
            c.execute(
                """update prospeccao
                      set empresa = %s,
                          contato = coalesce(nullif(contato, ''), %s),
                          atualizado_em = now()
                    where id = %s""", (nome_novo[:250], nome_novo[:250], lead_id))
        c.commit()
    print(f"\n{len(linhas)} lead(s) renomeado(s).")


if __name__ == "__main__":
    main()
