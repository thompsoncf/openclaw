"""Backfill: devolve ao VENDEDOR os lançamentos que a baixa creditou errado.

Até a correção, `empresa.dar_baixa_titulo` gravava no lançamento o `membro_id` de
QUEM CLICOU em "pago" — quase sempre o dono, ou ninguém — em vez de quem originou
o título. Resultado: a comissão do relatório ia pra pessoa errada, ou o
recebimento caía em "Sem vendedor". Daqui pra frente já está certo; este script
arruma o que ficou pra trás.

A regra é a mesma do código novo: o dono do lançamento é `titulos.criado_por`.
Só mexe em lançamento que veio de título (`titulos.lancamento_id`) e cujo título
TEM origem registrada. Onde não há origem, não há o que aprender — o script não
chuta.

Duas situações, contadas separadas porque têm gravidade diferente:

  * **órfão**  — lançamento sem `membro_id`: ninguém recebia comissão por ele.
    Preencher é ganho puro.
  * **trocado** — lançamento com `membro_id` DIFERENTE do vendedor: alguém estava
    levando comissão que não era dele. Corrigir muda quanto cada um recebe, então
    confira a lista antes de aplicar.

O que este script NÃO consegue arrumar: venda de balcão antiga (PDV). Ela nunca
gravou quem vendeu e não tem título pra consultar — a informação não existe em
lugar nenhum. O relatório mostra o total, e ele aparece aqui só como aviso.

Rodar no Render Shell:
    python -m scripts.backfill_comissao_vendedor              # simula (padrão)
    python -m scripts.backfill_comissao_vendedor --conta 7    # simula uma conta só
    python -m scripts.backfill_comissao_vendedor --aplicar    # grava
"""
import sys

from db.conexao import get_pool


def _arg(nome: str):
    """Valor de --nome VALOR (ou None)."""
    if nome in sys.argv:
        i = sys.argv.index(nome)
        if i + 1 < len(sys.argv):
            return sys.argv[i + 1]
    return None


def _candidatos(c, conta_id=None):
    """Lançamentos de título cujo dono não bate com quem originou a venda."""
    filtro, args = "", []
    if conta_id:
        filtro = " and t.conta_id = %s"
        args.append(int(conta_id))
    return c.execute(
        """select l.id, l.conta_id, l.data, l.valor_centavos, l.descricao,
                  l.membro_id, t.criado_por, t.id,
                  coalesce(nullif(mv.nome,''), mv.email, '?') as vendedor,
                  coalesce(nullif(ma.nome,''), ma.email)      as atual
             from titulos t
             join lancamentos l on l.id = t.lancamento_id and l.conta_id = t.conta_id
             left join membros mv on mv.id = t.criado_por  and mv.conta_id = t.conta_id
             left join membros ma on ma.id = l.membro_id   and ma.conta_id = l.conta_id
            where t.criado_por is not null
              and l.membro_id is distinct from t.criado_por""" + filtro +
        " order by l.conta_id, l.data, l.id", tuple(args)).fetchall()


def _pdv_sem_dono(c, conta_id=None):
    """Receita órfã que NÃO veio de título — o que o backfill não alcança."""
    filtro, args = "", []
    if conta_id:
        filtro = " and l.conta_id = %s"
        args.append(int(conta_id))
    return c.execute(
        """select count(*), coalesce(sum(l.valor_centavos), 0)
             from lancamentos l
             left join titulos t on t.lancamento_id = l.id
            where l.tipo = 'receita' and l.natureza = 'empresa'
              and l.membro_id is null and t.id is null""" + filtro, tuple(args)).fetchone()


def _brl(centavos) -> str:
    s = f"{int(centavos or 0) / 100:,.2f}"
    return "R$ " + s.replace(",", "X").replace(".", ",").replace("X", ".")


def main():
    aplicar = "--aplicar" in sys.argv
    conta_id = _arg("--conta")
    pool = get_pool()

    with pool.connection() as c:
        linhas = _candidatos(c, conta_id)
        n_pdv, v_pdv = _pdv_sem_dono(c, conta_id)

        orfaos = [r for r in linhas if r[5] is None]
        trocados = [r for r in linhas if r[5] is not None]
        escopo = f" (conta {conta_id})" if conta_id else ""
        print(f"{'APLICANDO' if aplicar else 'SIMULAÇÃO'}{escopo}\n")
        print(f"{len(linhas)} lançamento(s) pra reatribuir: "
              f"{len(orfaos)} sem dono, {len(trocados)} com dono errado.\n")

        if orfaos:
            print("SEM DONO → passam a contar comissão pro vendedor:")
            for (lid, cid, data, valor, desc, _at, _cp, tid, vend, _an) in orfaos:
                print(f"  conta {cid} · lanç {lid} · título {tid} · {data} · "
                      f"{_brl(valor)} · {(desc or '')[:40]!r} → {vend}")
            print()

        if trocados:
            print("DONO ERRADO → a comissão MUDA de pessoa (confira antes):")
            for (lid, cid, data, valor, desc, _at, _cp, tid, vend, atual) in trocados:
                print(f"  conta {cid} · lanç {lid} · título {tid} · {data} · "
                      f"{_brl(valor)} · {(atual or '?')} → {vend}")
            print()

        # por vendedor, pra dar pra conferir o impacto sem abrir o relatório
        if linhas:
            por = {}
            for (_l, _c, _d, valor, _desc, _at, _cp, _t, vend, atual) in linhas:
                por.setdefault(vend, [0, 0])[0] += int(valor or 0)
                if atual:
                    por.setdefault(atual, [0, 0])[1] += int(valor or 0)
            print("Impacto no recebido de cada um:")
            for nome, (ganha, perde) in sorted(por.items(), key=lambda x: -x[1][0]):
                partes = []
                if ganha:
                    partes.append(f"+{_brl(ganha)}")
                if perde:
                    partes.append(f"-{_brl(perde)}")
                print(f"  {nome}: {' e '.join(partes)}")
            print()

        if n_pdv:
            print(f"AVISO: {n_pdv} recebimento(s) somando {_brl(v_pdv)} continuam sem "
                  "vendedor — são vendas que não vieram de título (balcão antigo). "
                  "Quem vendeu nunca foi gravado, então não há de onde recuperar.\n")

        if not linhas:
            print("Nada a reatribuir.")
            return
        if not aplicar:
            print("Nada foi gravado. Rode de novo com --aplicar pra valer.")
            return

        # um UPDATE só, e a condição repete a do SELECT: se alguém mexeu no meio
        # do caminho, a linha simplesmente não entra em vez de gravar dado velho.
        n = c.execute(
            """update lancamentos l
                  set membro_id = t.criado_por
                 from titulos t
                where t.lancamento_id = l.id and t.conta_id = l.conta_id
                  and t.criado_por is not null
                  and l.membro_id is distinct from t.criado_por"""
            + (" and l.conta_id = %s" if conta_id else ""),
            (int(conta_id),) if conta_id else ()).rowcount
        c.commit()
    print(f"{n} lançamento(s) reatribuído(s).")


if __name__ == "__main__":
    main()
