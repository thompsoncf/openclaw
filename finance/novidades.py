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

# PRA QUEM o aviso é, por papel (migração 199). O check de `pra_quem` no banco
# espelha esta tupla, e um teste compara as duas — a mesma trava dos públicos.
#
# O vendedor é a novidade aqui: até 05/09/2026 nenhum aviso chegava nele, e três
# dos sete de agosto falavam do app DELE. Dono e gestor continuam sendo o padrão
# (é o `default '{dono,gestor}'` da coluna), então aviso antigo não muda de mão.
PAPEIS = ("dono", "gestor", "vendedor")


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


def _canal_proprio(pool, conta_id: int) -> bool:
    """A conta fala pelo WhatsApp DELA, conectado por QR code.

    POR QUE ISTO NÃO É UM NICHO. Duas mudanças de agosto — o microfone no app do
    vendedor e o sumiço do atalho "Mandar no WhatsApp" — não valem por ramo de
    negócio: valem por CANAL. Quem está na API oficial (Twilio, Cloud) só fala com o
    cliente dentro da janela de 24h, então lá o atalho continua e o microfone não
    aparece.

    Mandar esses avisos por nicho seria errar dos dois lados: prometer microfone pra
    quem não tem, e calar sobre um botão que sumiu pra quem perdeu. Na base de hoje,
    'eventos' até acertaria — mas por coincidência, não por regra: a conta 34 tem
    canal QR e Twilio configurados ao mesmo tempo.

    Falha fechada: sem saber o canal, o aviso não sai. Melhor um aviso que não sai
    do que um que sai pra quem não devia — a mesma escolha do público desconhecido.
    """
    try:
        from finance import whatsapp_out as wo
        with pool.connection() as c:
            return wo.provedor_da_conta(c, conta_id) == "qr"
    except Exception as e:  # noqa: BLE001
        _log.warning("não deu pra ver o canal da conta %s: %s: %s",
                     conta_id, type(e).__name__, e)
        return False


# O REGISTRO. Cada chave aponta pro portão que já decide quem vê a funcionalidade.
# Acrescentar um público aqui EXIGE mexer no check da migração — e um teste
# compara as duas listas, pra deriva virar falha em vez de surpresa.
#
# São DOIS registros porque são duas perguntas diferentes. A esmagadora maioria dos
# portões é sobre o RAMO, e ramo é o slug: a regra vale pra qualquer conta daquele
# nicho, hoje e no ano que vem. Uns poucos são sobre a CONTA — o canal de WhatsApp
# que ela usa, por exemplo — e esses precisam ir ao banco perguntar.
#
# Manter os dois separados é o que preserva `nichos_alcancados`, que é como se
# testa a mira sem depender de quem está cadastrado hoje.
PUBLICOS_NICHO = {
    "todos": lambda slug: True,
    "produto": _produto_declarado,
    "servico": _n.vende_servico,
    "eventos": _eventos,
    "recorrente": _recorrente,
}

PUBLICOS_CONTA = {
    "canal_proprio": _canal_proprio,
}

# A lista completa — é ela que o check da migração espelha.
PUBLICOS = {**PUBLICOS_NICHO, **PUBLICOS_CONTA}


def alcanca(publico: str, slug: str | None, pool=None, conta_id: int | None = None) -> bool:
    """Este aviso alcança esta conta?

    `pool`/`conta_id` só são usados pelos portões de CONTA. Sem eles, um público de
    conta devolve False — e isso é de propósito: quem chamar sem o banco não pode
    receber um "sim" por engano. `nichos_alcancados` depende disso pra continuar
    respondendo sobre slugs sem inventar contas.

    Público desconhecido devolve False e LOGA: melhor um aviso que não sai do que
    um aviso que sai pra quem não devia — e o log é o que impede a falha de ficar
    silenciosa. (Na prática não acontece: o check da tabela recusa o valor antes.)
    """
    if publico in PUBLICOS_CONTA:
        if pool is None or conta_id is None:
            return False
        return bool(PUBLICOS_CONTA[publico](pool, conta_id))
    p = PUBLICOS_NICHO.get(publico)
    if p is None:
        _log.warning("novidade com publico desconhecido: %r — ninguém será avisado", publico)
        return False
    if publico != "todos" and not _declarou(slug):
        return False
    return bool(p(slug))


def nichos_alcancados(publico: str) -> set[str]:
    """Quais SLUGS este público alcança. É sobre slugs e não sobre contas de
    propósito: a base muda toda semana, a regra não. Teste que conta contas passa
    por sorte e quebra quando alguém se cadastra.

    PORTÃO DE CONTA devolve vazio, e isso é a resposta certa: a mesma padaria pode
    estar no QR ou na API oficial, então "que slugs `canal_proprio` alcança" é uma
    pergunta que não se responde aqui. Vazio é a verdade; um conjunto seria invenção.

    Sai de graça, sem `if`: `alcanca` sem pool recusa qualquer portão de conta, e a
    compreensão abaixo não junta nada. Um `if` explícito aqui seria código que nunca
    muda o resultado — e um ramo que nenhuma mutação consegue matar é um ramo que
    ninguém precisa ler."""
    return {s for s in _n.NICHOS if alcanca(publico, s)}


# ------------------------------------------------------------------ persistência

_COLS = "id, chave, tipo, publico, titulo, corpo, publicado_em, pra_quem, resumo, link"


def _fmt(r, lida=None) -> dict:
    return {"id": r[0], "chave": r[1], "tipo": r[2], "publico": r[3],
            "titulo": r[4], "corpo": r[5], "publicado_em": r[6],
            "pra_quem": list(r[7] or ()), "resumo": r[8] or "", "link": r[9] or "",
            "lida": bool(lida)}


def para_papel(item: dict, papel: str | None) -> bool:
    """Este aviso é pra este papel? Sem papel (chamada antiga), é pra todo mundo —
    o filtro só aperta quando quem chama diz quem está olhando."""
    if not papel:
        return True
    return papel in (item.get("pra_quem") or ())


def listar(pool, conta_id: int, membro_id=None, papel: str | None = None) -> list[dict]:
    """Os avisos que ESTA conta deve ver, mais novos primeiro, já com o estado de
    lida de QUEM está olhando.

    `papel` (migração 199) corta pelo `pra_quem` do aviso: o vendedor só recebe o
    que é marcado pra ele, e o dono não recebe o aviso da Fila que é só do app do
    vendedor. Sem papel, devolve tudo — é o comportamento das chamadas antigas.

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
    itens = [_fmt(r, r[10]) for r in rows if alcanca(r[3], slug, pool, conta_id)]
    return [n for n in itens if para_papel(n, papel)]


def nao_lidas(pool, conta_id: int, membro_id=None, papel: str | None = None) -> int:
    """Quantas faltam ler — é o número da bolinha no menu."""
    try:
        return sum(1 for n in listar(pool, conta_id, membro_id, papel) if not n["lida"])
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


# ----------------------------------------------------------------- o site

def publicas(pool) -> list[dict]:
    """O que o site (zaq-ia.com/atualizacoes) mostra: todo aviso com resumo, mais
    novos primeiro, SEM nada de conta — nem quem leu, nem quem recebeu, nem corpo.

    O corpo fica de fora de propósito: ele fala com quem já usa e cita número de
    conta, nome de cliente, o que sumiu de onde. O resumo é a versão que vende.
    Aviso sem resumo não sai: é o jeito de um aviso interno não virar público
    por esquecimento.

    `ramo` é o público do aviso, que é o que a landing usa pra filtrar. Não é
    nicho de conta nenhuma."""
    with pool.connection() as c:
        rows = c.execute(
            """select chave, tipo, publico, titulo, resumo, publicado_em, pra_quem
                 from novidades
                where resumo is not null and resumo <> ''
                order by publicado_em desc, id desc""").fetchall()
    return [{"chave": r[0], "tipo": r[1], "ramo": r[2], "titulo": r[3], "resumo": r[4],
             "dia": r[5].date().isoformat(), "pra_quem": list(r[6] or ())}
            for r in rows]


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
            for r in rows if alcanca(publico, r[2], pool, r[0])]
