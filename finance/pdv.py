"""PDV / venda de balcao.

Uma venda de balcao amarra tres coisas que ja' existem no sistema, sem tabela nova:
  1) baixa de ESTOQUE por item      -> catalogo.registrar_movimentacao('saida')
  2) RECEITA no livro-caixa          -> LivroCaixa.adicionar (categoria 'Vendas',
                                        origem 'balcao' = marcador do canal)
  3) ITENS da venda                  -> LivroCaixa.registrar_itens (linha a linha)
  4) CLIENTE opcional (agenda)       -> clientes.achar_ou_criar

Modelo leve de proposito: a "venda" e' um lancamento de receita com itens. Se um dia
precisar de relatorio de vendas dedicado, cria-se uma tabela vendas_balcao depois.

Atomicidade: as funcoes de estoque e de caixa fazem commit proprio (nao da' pra abrir
UMA transacao unica sem reescreve-las). Por isso a venda:
  - PRE-VALIDA o saldo de todos os itens ANTES de mexer em qualquer coisa (evita a
    falha comum: vender mais do que tem);
  - so' entao baixa estoque e grava a receita.
Se algo muito raro falhar no meio (ex.: erro de banco entre um item e outro), o
chamador recebe a excecao e o estado parcial fica visivel no extrato/estoque para
correcao manual. Para o balcao (poucos itens por venda) isso e' aceitavel no v1.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from decimal import Decimal

from .livro_caixa import LivroCaixa
from .models import Lancamento, Tipo
from . import catalogo as _cat
from . import clientes as _cli
from . import empresa as _emp


def _parse_venc(s):
    """Aceita 'AAAA-MM-DD' ou date; devolve date ou None."""
    if not s:
        return None
    if isinstance(s, date):
        return s
    try:
        return datetime.strptime(str(s).strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


def _saldo_produto(pool, dono_id: int, produto_id: int):
    """Retorna (saldo, nome, unidade, preco_venda_centavos) ou None se nao for do dono."""
    with pool.connection() as c:
        r = c.execute(
            """select saldo, nome, unidade, preco_venda_centavos
                 from catalogo_produtos
                where id = %s and fornecedor_id = %s""",
            (produto_id, dono_id),
        ).fetchone()
    if r is None:
        return None
    return (Decimal(str(r[0] or 0)), r[1], r[2] or "un", int(r[3] or 0))


def registrar_venda_balcao(
    pool,
    dono_id: int,
    itens: list[dict],
    *,
    cliente_id: int | None = None,
    cliente_nome: str | None = None,
    cliente_telefone: str | None = None,
    cliente_cpf: str | None = None,
    cliente_cnpj: str | None = None,
    pagamento: str = "dinheiro",
    vencimento: str | None = None,
    desconto_centavos: int = 0,
    permitir_estoque_negativo: bool = False,
    membro_id: int | None = None,
) -> dict:
    """Registra uma venda de balcao.

    itens: lista de dicts, cada um:
        {produto_id: int, quantidade: float|Decimal, preco_unit_centavos: int}
      (descricao/unidade sao buscados do proprio produto; se vierem no dict, usa-se
       o que veio).

    Retorna:
        {lancamento_id, total_centavos, subtotal_centavos, desconto_centavos,
         cliente_id, itens: <qtd de itens>}

    Levanta ValueError com mensagem amigavel em: carrinho vazio, produto inexistente,
    quantidade invalida ou estoque insuficiente (a menos de permitir_estoque_negativo).
    """
    if not itens:
        raise ValueError("carrinho vazio")

    # ---- 1) normaliza + pre-valida TODOS os itens (nada e' gravado ainda) ----
    linhas = []          # itens prontos pro livro-caixa + estoque
    subtotal = 0
    for it in itens:
        pid = int(it["produto_id"])
        qtd = Decimal(str(it["quantidade"]))
        if qtd <= 0:
            raise ValueError("quantidade deve ser positiva")

        info = _saldo_produto(pool, dono_id, pid)
        if info is None:
            raise ValueError(f"produto {pid} nao encontrado")
        saldo, nome, unidade, preco_padrao = info

        # preco unitario: usa o enviado (editavel no balcao) ou o do cadastro
        vu = int(it.get("preco_unit_centavos") if it.get("preco_unit_centavos") is not None
                 else preco_padrao)
        if vu < 0:
            vu = 0

        if not permitir_estoque_negativo and qtd > saldo:
            raise ValueError(
                f"estoque insuficiente de {nome}: tem {saldo} {unidade}, "
                f"tentou vender {qtd}"
            )

        # total da linha (centavos, arredonda no fim)
        vt = int((Decimal(vu) * qtd).quantize(Decimal("1")))
        subtotal += vt
        linhas.append({
            "produto_id": pid,
            "descricao": it.get("descricao") or nome,
            "quantidade": qtd,
            "valor_unitario_centavos": vu,
            "valor_total_centavos": vt,
            "unidade": it.get("unidade") or unidade,
        })

    desconto = max(0, int(desconto_centavos or 0))
    if desconto > subtotal:
        desconto = subtotal
    total = subtotal - desconto

    # ---- 2) cliente (agenda) opcional ----
    # Um cliente_id ja escolhido (puxado da base pelo lojista) tem prioridade;
    # senao acha/cria por cpf (forte), telefone ou nome.
    nome_lbl = (cliente_nome or "").strip()
    if cliente_id is None:
        cpf_lbl = (cliente_cpf or "").strip() or None
        cnpj_lbl = (cliente_cnpj or "").strip() or None
        if nome_lbl or cpf_lbl or cnpj_lbl:
            cliente_id = _cli.achar_ou_criar(
                pool, dono_id, nome_lbl or "cliente",
                telefone=(cliente_telefone or "").strip() or None,
                cpf=cpf_lbl,
                cnpj=cnpj_lbl,
            )

    # ---- 3) baixa de estoque por item (saida) ----
    for ln in linhas:
        _cat.registrar_movimentacao(
            pool, dono_id, ln["produto_id"], "saida", ln["quantidade"],
            motivo="venda balcao",
        )

    # ---- 3.5) FIADO: nao gera receita agora; cria um titulo A RECEBER do cliente ----
    if (pagamento or "").strip().lower() == "fiado":
        if not cliente_id:
            raise ValueError("fiado exige um cliente identificado")
        venc = _parse_venc(vencimento) or (date.today() + timedelta(days=30))
        desc_f = "Venda de balcao (fiado)"
        if nome_lbl:
            desc_f += f" - {nome_lbl}"
        t = _emp.criar_titulo(
            pool, dono_id, "receber", desc_f, total, venc,
            contraparte=nome_lbl, criado_por=membro_id,
        )
        with pool.connection() as _c:
            _c.execute(
                "update titulos set cliente_id=%s where id=%s and conta_id=%s",
                (cliente_id, t["id"], dono_id),
            )
            _c.commit()
        return {
            "fiado": True,
            "titulo_id": t["id"],
            "subtotal_centavos": subtotal,
            "desconto_centavos": desconto,
            "total_centavos": total,
            "vencimento": str(venc),
            "cliente_id": cliente_id,
            "itens": len(linhas),
        }

    # ---- 4) receita no livro-caixa (canal 'balcao') ----
    desc = "Venda de balcao"
    if nome_lbl:
        desc += f" - {nome_lbl}"
    if desconto > 0:
        desc += f" (desc R$ {desconto/100:.2f})"

    lc = LivroCaixa(pool, dono_id, membro_id=membro_id)
    lanc = Lancamento(
        tipo=Tipo.RECEITA,
        valor_centavos=total,
        categoria="Vendas",
        descricao=desc,
        pagamento=(pagamento or "dinheiro").strip() or "dinheiro",
        origem="balcao",
        natureza="empresa",
    )
    # forcar=True: duas vendas de mesmo valor no mesmo dia sao legitimas no balcao,
    # a trava anti-duplicidade por tipo+valor+data nao deve barrar.
    lanc = lc.adicionar(lanc, forcar=True)

    # liga a venda ao cliente (migracao 066: lancamentos.cliente_id)
    if cliente_id:
        with pool.connection() as _c:
            _c.execute(
                "update lancamentos set cliente_id=%s where id=%s and conta_id=%s",
                (cliente_id, lanc.id, dono_id),
            )
            _c.commit()

    # ---- 5) itens da venda ligados ao lancamento ----
    lc.registrar_itens(
        lanc.id,
        [{
            "descricao": ln["descricao"],
            "quantidade": float(ln["quantidade"]),
            "valor_unitario_centavos": ln["valor_unitario_centavos"],
            "valor_total_centavos": ln["valor_total_centavos"],
            "unidade": ln["unidade"],
        } for ln in linhas],
    )

    return {
        "lancamento_id": lanc.id,
        "subtotal_centavos": subtotal,
        "desconto_centavos": desconto,
        "total_centavos": total,
        "cliente_id": cliente_id,
        "itens": len(linhas),
    }
