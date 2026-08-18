"""Avisos de atualização pro dono — e a MIRA deles.

O que este módulo existe pra impedir: uma mudança subir e a pessoa afetada não
saber. Aconteceu duas vezes em três dias com a mesma conta — a Doce Mell perdeu o
botão "Fechar contrato" do funil e ganhou uma agenda diferente sem ser avisada de
nenhum dos dois.

A PARTE DIFÍCIL É A MIRA, não a tela. Cada nicho recebe atualização diferente, e
avisar quem não devia é tão ruim quanto calar pra quem devia: a pessoa vai
procurar na tela uma coisa que ela não tem.

O PÚBLICO É O NOME DE UM PORTÃO QUE JÁ EXISTE
---------------------------------------------
Não é lista de nichos escrita à mão — é o MESMO portão que decide quem vê a
funcionalidade. Lista paralela acerta no primeiro dia e diverge no terceiro.

E tem um efeito colateral que vale por si: pra escrever o aviso você precisa
NOMEAR o portão. Se não existe portão que descreva quem recebeu a mudança, o
problema não é o aviso — é que a mudança foi pro ar sem ninguém saber quem ela
atinge. O aviso vira o teste da própria gatilhagem.

CONTA SEM NICHO NÃO RECEBE AVISO MIRADO
---------------------------------------
Medido em 18/08/2026: 14 das 22 contas nunca escolheram nicho. E os portões que
já existem DISCORDAM sobre elas — `vende_produto` inclui (default True, porque o
painel mostra as telas de produto no onboarding), `vende_servico` e
`tem_contrato` excluem. Herdar esse default mandaria um aviso de produto pra 17
contas quando só 3 declararam nicho de produto.

Então a regra é do aviso, não da tela: público diferente de 'todos' exige nicho
DECLARADO. Se o sistema não sabe o que aquele negócio é, não pode afirmar que a
mudança o afeta — e 'todos' continua alcançando todo mundo, então ninguém fica
invisível por acidente.
"""
from __future__ import annotations

import logging

from finance import nichos as _n

_log = logging.getLogger("finance.novidades")

TIPOS = ("novidade", "mudanca")


def _declarou(slug) -> bool:
    """Esta conta ESCOLHEU um nicho? `config_do_nicho` cai no genérico pra slug
    desconhecido e pra None, então perguntar a ele não distingue "não escolheu" de
    "escolheu algo que não existe mais" — as duas coisas precisam ficar de fora do
    aviso mirado, e é isto que separa."""
    return (slug or "") in _n.NICHOS


def _produto_declarado(slug) -> bool:
    """`vende_produto` SEM o default permissivo. O default True é correto pro
    painel (onboarding mostra produto antes de a pessoa escolher) e errado aqui."""
    return _declarou(slug) and _n.vende_produto(slug)


def _recorrente(slug) -> bool:
    from finance.vendas import modo_por_nicho
    return _n.vende_servico(slug) and modo_por_nicho(slug) != "evento"


def _eventos(slug) -> bool:
    from finance.contrato import tem_contrato
    return tem_contrato(slug)


# O REGISTRO. Cada chave aponta pro portão que já decide quem vê a funcionalidade.
# Acrescentar um público aqui EXIGE mexer no check da migração — e um teste
# compara as duas listas, pra deriva virar falha em vez de surpresa.
PUBLICOS = {
    "todos": lambda slug: True,
    "produto": _produto_declarado,
    "servico": _n.vende_servico,
    "eventos": _eventos,
    "recorrente": _recorrente,
}


def alcanca(publico: str, slug: str | None) -> bool:
    """Este aviso alcança uma conta com este nicho?

    Público desconhecido devolve False e LOGA: melhor um aviso que não sai do que
    um aviso que sai pra quem não devia — e o log é o que impede a falha de ficar
    silenciosa. (Na prática não acontece: o check da tabela recusa o valor antes.)
    """
    p = PUBLICOS.get(publico)
    if p is None:
        _log.warning("novidade com publico desconhecido: %r — ninguém será avisado", publico)
        return False
    if publico != "todos" and not _declarou(slug):
        return False
    return bool(p(slug))


def nichos_alcancados(publico: str) -> set[str]:
    """Quais SLUGS este público alcança. É sobre slugs e não sobre contas de
    propósito: a base muda toda semana, a regra não. Teste que conta contas passa
    por sorte e quebra quando alguém se cadastra."""
    return {s for s in _n.NICHOS if alcanca(publico, s)}


# ------------------------------------------------------------------ persistência

_COLS = "id, chave, tipo, publico, titulo, corpo, publicado_em"


def _fmt(r, lida=None) -> dict:
    return {"id": r[0], "chave": r[1], "tipo": r[2], "publico": r[3],
            "titulo": r[4], "corpo": r[5], "publicado_em": r[6],
            "lida": bool(lida)}


def listar(pool, conta_id: int, membro_id=None) -> list[dict]:
    """Os avisos que ESTA conta deve ver, mais novos primeiro, já com o estado de
    lida de QUEM está olhando.

    Duas exclusões que não são detalhe:

    • o corte por `criado_em` da conta — quem se cadastrar mês que vem não pode
      abrir o painel com 23 avisos não lidos de mudanças que nunca viveu;
    • a mira, aplicada em Python e não em SQL, porque os portões são funções
      (`vende_servico`, `tem_contrato`) e duplicá-los em SQL seria a segunda
      leitura que este módulo inteiro existe pra evitar.
    """
    with pool.connection() as c:
        nicho = c.execute(
            """select n.slug, ct.criado_em from contas ct
                 left join nichos n on n.id = ct.nicho_id
                where ct.id=%s""", (conta_id,)).fetchone()
        if not nicho:
            return []
        slug, criada_em = nicho
        rows = c.execute(
            f"""select {_COLS},
                       (select 1 from novidade_lida l
                         where l.novidade_id = novidades.id and l.conta_id = %s
                           and coalesce(l.membro_id, 0) = coalesce(%s::bigint, 0))
                  from novidades
                 where publicado_em > %s
                 order by publicado_em desc, id desc""",
            (conta_id, membro_id, criada_em)).fetchall()
    return [_fmt(r, r[7]) for r in rows if alcanca(r[3], slug)]


def nao_lidas(pool, conta_id: int, membro_id=None) -> int:
    """Quantas faltam ler — é o número da bolinha no menu."""
    try:
        return sum(1 for n in listar(pool, conta_id, membro_id) if not n["lida"])
    except Exception as e:  # noqa: BLE001
        # TOLERANTE: a bolinha é enfeite, e o painel inteiro não pode deixar de
        # abrir porque a contagem de avisos falhou. Mesma escolha do `vende_data`.
        _log.warning("não deu pra contar novidades da conta %s: %s: %s",
                     conta_id, type(e).__name__, e)
        return 0


def marcar_lida(pool, novidade_id: int, conta_id: int, membro_id=None) -> bool:
    """Marca lida pra QUEM leu — não pra conta.

    Numa conta com dono e gerente cada um lê o seu; marcar por conta faria o
    segundo nunca ver o aviso que o primeiro abriu, e é o segundo que costuma
    operar a tela. O `coalesce(membro_id, 0)` no índice é porque a sessão do dono
    carrega membro_id NULO (ver web/painel_servicos._ator)."""
    with pool.connection() as c:
        cur = c.execute(
            """insert into novidade_lida (novidade_id, conta_id, membro_id)
               values (%s,%s,%s) on conflict do nothing""",
            (int(novidade_id), conta_id, membro_id))
        c.commit()
    return cur.rowcount > 0


# ----------------------------------------------------------------- prévia (CI)

def contas_alcancadas(pool, publico: str) -> list[dict]:
    """QUEM vai receber, por nome. É a trava que o humano opera: as outras pegam
    erro de código, esta pega erro de julgamento — quando o portão está certo e
    mesmo assim não é quem se queria avisar.

    Sai no corpo do PR pra a lista ser lida ANTES de o aviso alcançar alguém."""
    with pool.connection() as c:
        rows = c.execute(
            """select ct.id, ct.nome, n.slug from contas ct
                 left join nichos n on n.id = ct.nicho_id
                order by ct.id""").fetchall()
    return [{"id": r[0], "nome": r[1], "nicho": r[2] or "(sem nicho)"}
            for r in rows if alcanca(publico, r[2])]
