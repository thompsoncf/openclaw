"""O saldo do banco que o dono informa — e o planejamento da semana que sai dele.

Etapa A do pedido 3 do dono (23/09/2026): "open finance — saldo da conta para
planejamento de contas a pagar". Open Finance de verdade é contrato com agregador
(mensalidade e consentimento renovado a cada 12 meses), e essa decisão é dele.
Esta etapa entrega a pergunta que importa — "tenho X, devo Y até o fim da semana,
sobra ou falta Z?" — sem depender de fornecedor nenhum: ele digita o saldo.

TRÊS DECISÕES, E POR QUÊ:

* POR BANCO. A Prime tem Sicoob e Banco do Nordeste ("os 2", resposta dele). Um
  campo só obrigaria a somar de cabeça — e o erro dessa soma seria invisível.

* HISTÓRICO. Cada informe é uma linha; o atual é a mais recente de cada banco.
  Nada é sobrescrito (regra 0), e tirar um banco é uma linha `arquivado`.

* O SALDO ENVELHECE NA TELA. Saldo de ontem ainda serve; de três dias atrás
  engana — o dinheiro andou. A tela diz a idade de cada um, e a partir de
  `DIAS_VELHO` pinta de cinza e pede pra atualizar, em vez de fingir que é de hoje.

Os números da semana (atrasadas e a vencer em 7 dias) saem de
`empresa.resumo_titulos`, a MESMA fonte da pílula "⚠️ Atrasadas" da aba Empresa:
duas contas diferentes na mesma tela seriam duas verdades.
"""
from __future__ import annotations

from datetime import date, datetime, timezone

#: A partir de quantos dias o saldo informado é "velho" e a tela pede pra
#: atualizar. 3, e não 1: fim de semana sem movimento não pode transformar o
#: saldo de sexta em alarme na segunda cedo.
DIAS_VELHO = 3

#: Quantos dias à frente o planejamento olha. É a semana do mockup aprovado, e a
#: mesma janela do "A vencer em 7 dias" do relatório de Contas a pagar.
DIAS_SEMANA = 7


def _chave(banco: str) -> str:
    return " ".join((banco or "").split()).lower()


def atuais(pool, conta_id: int, agora: datetime | None = None) -> list[dict]:
    """O saldo vigente de cada banco (o informe mais recente), sem os arquivados.

    `dias` é a idade do informe em dias corridos, contada no fuso de Brasília —
    o informe das 23h de ontem é "ontem", não "hoje", pra quem está em Teresina.
    """
    from zoneinfo import ZoneInfo
    br = ZoneInfo("America/Sao_Paulo")
    hoje = (agora or datetime.now(timezone.utc)).astimezone(br).date()
    with pool.connection() as c:
        rows = c.execute(
            """select distinct on (lower(btrim(banco)))
                      banco, valor_centavos, arquivado, informado_em
                 from saldo_bancario_informado
                where conta_id = %s
                order by lower(btrim(banco)), informado_em desc, id desc""",
            (conta_id,)).fetchall()
    out = []
    for banco, valor, arquivado, quando in rows:
        if arquivado:
            continue
        local = quando.astimezone(br)
        dias = (hoje - local.date()).days
        out.append({"banco": banco, "valor_centavos": int(valor),
                    "informado_em": local, "dias": dias,
                    "velho": dias >= DIAS_VELHO})
    out.sort(key=lambda s: s["banco"].lower())
    return out


def informar(pool, conta_id: int, banco: str, valor_centavos: int,
             membro_id: int | None = None) -> dict:
    """Grava um informe novo. O nome do banco vem normalizado nos espaços, e um
    banco já existente mantém a grafia que tinha — "sicoob" e "Sicoob" são o
    mesmo banco, e a tela não pode ganhar duas linhas por causa de uma maiúscula."""
    nome = " ".join((banco or "").split())[:60]
    if not nome:
        return {"ok": False, "erro": "Diga de qual banco é o saldo."}
    with pool.connection() as c:
        r = c.execute(
            """select banco from saldo_bancario_informado
                where conta_id=%s and lower(btrim(banco)) = %s
                order by informado_em desc limit 1""",
            (conta_id, _chave(nome))).fetchone()
        if r:
            nome = r[0]
        c.execute(
            """insert into saldo_bancario_informado
                   (conta_id, banco, valor_centavos, informado_por)
               values (%s, %s, %s, %s)""",
            (conta_id, nome, int(valor_centavos), membro_id))
        c.commit()
    return {"ok": True, "banco": nome}


def arquivar(pool, conta_id: int, banco: str, membro_id: int | None = None) -> bool:
    """Tira um banco da soma SEM apagar nada: grava uma linha `arquivado` com o
    último valor dele. Informar esse banco de novo o traz de volta."""
    with pool.connection() as c:
        r = c.execute(
            """select banco, valor_centavos from saldo_bancario_informado
                where conta_id=%s and lower(btrim(banco)) = %s
                order by informado_em desc, id desc limit 1""",
            (conta_id, _chave(banco))).fetchone()
        if not r:
            return False
        c.execute(
            """insert into saldo_bancario_informado
                   (conta_id, banco, valor_centavos, arquivado, informado_por)
               values (%s, %s, %s, true, %s)""",
            (conta_id, r[0], int(r[1]), membro_id))
        c.commit()
    return True


def planejamento(pool, conta_id: int, agora: datetime | None = None) -> dict:
    """A faixa da semana: saldo − atrasadas − a vencer em 7 dias = sobra.

    `sobra_centavos` é None enquanto não houver saldo informado — sem ele não
    existe sobra, e mostrar "− R$ 21.680,49" como se o saldo fosse zero seria
    anunciar um rombo que ninguém mediu.
    """
    from . import empresa as emp
    saldos = atuais(pool, conta_id, agora)
    r = emp.resumo_titulos(pool, conta_id, dias=DIAS_SEMANA)
    atrasadas = int(r.get("atrasados_pagar_centavos") or 0)
    a_vencer = int(r.get("a_pagar_centavos") or 0)
    total = sum(s["valor_centavos"] for s in saldos) if saldos else None
    sobra = (total - atrasadas - a_vencer) if total is not None else None
    receber_vencidas = int(r.get("atrasados_receber_centavos") or 0)
    return {
        "saldos": saldos,
        "saldo_centavos": total,
        "algum_velho": any(s["velho"] for s in saldos),
        "atrasadas_centavos": atrasadas,
        "n_atrasadas": int(r.get("n_atrasados_pagar") or 0),
        "a_vencer_centavos": a_vencer,
        "n_a_vencer": int(r.get("n_pagar") or 0),
        "sobra_centavos": sobra,
        "receber_vencidas_centavos": receber_vencidas,
        "n_receber_vencidas": int(r.get("n_atrasados_receber") or 0),
        # a dica só quando ajuda: falta dinheiro E há dinheiro vencido pra
        # cobrar. Com sobra, falar de cobrança seria ruído.
        "cobrar_cobre": (sobra is not None and sobra < 0 and receber_vencidas > 0
                         and receber_vencidas >= -sobra),
        "dias": DIAS_SEMANA,
    }
