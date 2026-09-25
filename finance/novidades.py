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
from datetime import date, datetime, timedelta, timezone

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


def _seguros(slug) -> bool:
    """Corretora de seguros — e' o unico portao de UM nicho so'.

    Os outros portoes descrevem familias ('produto', 'servico', 'recorrente') ou um
    modo de operar ('eventos' pergunta a `tem_contrato`, e nao ao slug). Aqui a
    mira e' mesmo o ramo: apolice, seguradora e comissao nao existem em nenhum
    outro nicho, e um aviso sobre eles em qualquer outra conta e' prometer tela que
    nao abre. Se um dia entrar corretora de imoveis ou consorcio, isto vira uma
    familia — hoje seria inventar alcance que ninguem tem.
    """
    return (slug or "") == "seguros"


def _suplementos(slug) -> bool:
    """Loja de suplemento com cozinha — o segundo portão de UM nicho só.

    Mesma justificativa do `_seguros` logo acima: whey, marmita e ficha técnica não
    existem em nenhum outro nicho da base. Os portões de família erram os dois
    lados aqui — `produto` alcançaria o hortifrúti do Zé do Arroz (conta 9) e a
    mercearia, `servico` alcançaria advocacia e contabilidade, e `recorrente`
    alcançaria as quatro consultorias. Nenhuma delas ganha tela nenhuma com isto.
    """
    return (slug or "") == "suplementos"


def _construcao(slug) -> bool:
    """Construção e reforma — o terceiro portão de UM nicho só.

    Obra, casa de Minha Casa Minha Vida, visita técnica e crédito da Caixa não
    existem em nenhum outro ramo. `servico` alcançaria advocacia e contabilidade,
    e `recorrente` alcançaria as consultorias — o perfil `obras` e o que vier
    depois dele (a aba Obras, o caminho do dinheiro da casa) não abrem pra
    nenhuma delas.
    """
    return (slug or "") == "construcao"


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


def _empresa(pool, conta_id: int) -> bool:
    """A conta tem a aba Empresa (o módulo PJ).

    Nasceu com o aviso da aba reorganizada (24/09/2026, migração 338). Nenhum
    portão de nicho descrevia o alcance: a aba é a mesma pros três perfis, mas
    só existe pra quem tem o módulo — 'todos' avisaria de uma tela que parte da
    base nem tem. O portão é o MESMO da rota (`empresa.modulo_pj_ativo`), pra
    que o aviso e a tela nunca discordem sobre quem vê o quê.

    Falha fechada, como o do canal: sem saber, o aviso não sai.
    """
    try:
        from finance import empresa as emp
        return bool(emp.modulo_pj_ativo(pool, conta_id))
    except Exception as e:  # noqa: BLE001
        _log.warning("não deu pra ver o módulo Empresa da conta %s: %s: %s",
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
    "seguros": _seguros,
    "suplementos": _suplementos,
    "construcao": _construcao,
}

PUBLICOS_CONTA = {
    "canal_proprio": _canal_proprio,
    "empresa": _empresa,
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


#: Quantos dias um aviso pode INTERROMPER — ou seja, virar faixa no topo da Fila.
#:
#: Passado o prazo ele continua existindo, continua não lido e continua contando na
#: bolinha do Perfil. Só para de pular na frente de quem está trabalhando.
#:
#: POR QUE ISTO EXISTE. Medido em 16/09/2026: dos OITO vendedores ativos em
#: produção, QUATRO estavam em 27 de 27 — nunca abriram nem fecharam um único
#: aviso. A faixa estava no topo da Fila deles todos os dias desde que nasceu e
#: nunca foi tocada. Aviso que ninguém toca deixou de ser aviso e virou mobília, e
#: mobília ensina a pessoa a não olhar aquele pedaço da tela — inclusive no dia em
#: que ele trouxer algo que importa.
#:
#: A causa é que a faixa nasceu depois de dezenas de avisos já publicados, e mostra
#: um por vez: ninguém vai tocar 26 vezes. O prazo faz a pilha se resolver sozinha,
#: sem apagar nada e sem depender de o dono lembrar de desligar a faixa.
DIAS_NA_FAIXA = 14


#: Fuso do Brasil, fixo. A semana de uma entrega é a semana de quem a recebeu, e
#: um aviso publicado às 21h de sexta (meia-noite de sábado em UTC) não pode cair
#: na semana seguinte só porque o servidor mora em Greenwich.
_BR = timedelta(hours=-3)


def semana_de(quando) -> str:
    """A semana ISO de um aviso, em hora de Brasília: '2026-W38'.

    Chave e não data porque é o que agrupa: duas entregas de terça e de sexta são
    a MESMA novidade da semana pra quem recebe."""
    if quando is None:
        return ""
    if getattr(quando, "tzinfo", None) is None:
        quando = quando.replace(tzinfo=timezone.utc)
    loc = quando.astimezone(timezone.utc) + _BR
    ano, num, _ = loc.isocalendar()
    return f"{ano}-W{num:02d}"


def rotulo_semana(chave: str, *, agora=None) -> str:
    """Como a semana se chama na tela: "esta semana", "semana passada", ou o
    intervalo de dias. Data crua ("2026-W38") não diz nada a ninguém."""
    agora = agora or datetime.now(timezone.utc)
    hoje = semana_de(agora)
    if chave == hoje:
        return "esta semana"
    try:
        ano, num = int(chave[:4]), int(chave[6:])
        seg = date.fromisocalendar(ano, num, 1)
        dom = date.fromisocalendar(ano, num, 7)
    except (ValueError, IndexError):
        return "antes"
    a_num = int(hoje[6:]) if hoje else 0
    if chave[:4] == hoje[:4] and a_num - num == 1:
        return "semana passada"
    if seg.month == dom.month:
        return f"{seg:%d} a {dom:%d/%m}"
    return f"{seg:%d/%m} a {dom:%d/%m}"


def por_semana(itens: list[dict], *, agora=None) -> list[dict]:
    """Os avisos agrupados por SEMANA, da mais nova pra mais velha.

    POR QUE ISTO EXISTE (17/09/2026). A regra 5 do CLAUDE.md manda todo PR que
    muda tela escrever o aviso dele, e eu vinha cumprindo ao pé da letra: 30 avisos
    em 7 dias. O efeito foi o contrário do que a regra quer — o dono abriu com 47
    por ler e ZERO lidos, e a vendedora com mais leads, 28 por ler e zero lidos. A
    faixa dizia "1 de 42": ninguém toca 42 vezes.

    O prazo de 14 dias, que nasceu dois dias antes pra segurar exatamente isso, não
    ajudou — 42 dos 47 eram dos últimos 14 dias. Ele foi desenhado contra aviso
    VELHO, e o problema era aviso DEMAIS.

    A escolha do dono: "um resumo por semana". Então a faixa passa a interromper
    uma vez por semana, com o que a semana trouxe; cada aviso continua existindo,
    inteiro, na lista e no site. Nada é agrupado no BANCO — o agrupamento é de
    leitura, e juntar no banco tiraria de cada entrega o seu próprio registro.
    """
    grupos: dict[str, dict] = {}
    for n in itens:
        ch = semana_de(n.get("publicado_em"))
        if not ch:
            continue                      # sem data não entra em semana nenhuma
        g = grupos.setdefault(ch, {"chave": ch, "itens": []})
        g["itens"].append(n)
    out = [{"chave": g["chave"], "rotulo": rotulo_semana(g["chave"], agora=agora),
            "n": len(g["itens"]), "itens": g["itens"]}
           for g in grupos.values()]
    out.sort(key=lambda g: g["chave"], reverse=True)
    return out


def semanas_vistas(pool, conta_id: int, membro_id) -> set:
    """Que semanas esta pessoa já dispensou da faixa (migração 272).

    FALHA ABERTA, como `faixa_ligada`: sem a tabela ou com o banco fora, devolve
    vazio — e vazio quer dizer "nada dispensado", ou seja, a faixa aparece. Um
    registro que não pôde ser lido não pode CALAR um aviso.
    """
    try:
        with pool.connection() as c:
            rows = c.execute(
                "select semana from novidade_semana_vista "
                " where conta_id=%s and membro_id=%s",
                (conta_id, membro_id)).fetchall()
    except Exception as e:  # noqa: BLE001 — base sem a 272 ainda
        _log.warning("semanas vistas de %s/%s: %s: %s", conta_id, membro_id,
                     type(e).__name__, e)
        return set()
    return {r[0] for r in rows}


def marcar_semana_vista(pool, conta_id: int, membro_id, semana: str) -> None:
    """O ✕ da faixa semanal: para de interromper ESTA pessoa NESTA semana.

    NÃO marca aviso nenhum como lido, e isso é o ponto — em 16/09/2026 o dono
    barrou o ✕ que dispensava tudo, porque "não lido" é o que a bolinha conta e não
    existe desmarcar. Aqui a faixa se cala e a verdade fica de pé.
    """
    if not (semana or "").strip():
        return
    try:
        with pool.connection() as c:
            c.execute("""insert into novidade_semana_vista (conta_id, membro_id, semana)
                         values (%s,%s,%s) on conflict do nothing""",
                      (conta_id, membro_id, semana.strip()))
            c.commit()
    except Exception as e:  # noqa: BLE001
        _log.warning("dispensar semana %s (%s/%s): %s: %s", semana, conta_id,
                     membro_id, type(e).__name__, e)


def para_faixa(itens: list[dict], *, agora=None) -> list[dict]:
    """Dos avisos que a pessoa ainda não leu, quais podem virar FAIXA hoje.

    Só o prazo (`DIAS_NA_FAIXA`) mora aqui — quem já filtrou "é dele" e "não leu"
    foi `listar`. A ordem que entra é a que sai: `listar` já devolve do mais novo
    pro mais velho, e reordenar aqui seria uma segunda definição de "mais novo".

    Aviso sem `publicado_em` fica DE FORA da faixa: sem data não dá pra dizer que
    está no prazo, e na dúvida a faixa não interrompe — ela é a única coisa deste
    módulo que toma a tela de alguém.
    """
    from datetime import datetime, timedelta, timezone
    agora = agora or datetime.now(timezone.utc)
    corte = agora - timedelta(days=DIAS_NA_FAIXA)
    out = []
    for n in itens:
        p = n.get("publicado_em")
        if not hasattr(p, "tzinfo"):
            continue
        if p.tzinfo is None:
            p = p.replace(tzinfo=timezone.utc)
        if p >= corte:
            out.append(n)
    return out


def faixa_ligada(pool, conta_id: int) -> bool:
    """Esta conta mostra a faixa de novidade na Fila dos vendedores?

    FALHA ABERTA, ao contrário da maioria dos portões desta base: sem a coluna, sem
    linha ou com o banco fora, devolve True — que é o comportamento de hoje. Um
    parâmetro que não pôde ser lido não pode CALAR um aviso; o pior caso aqui é a
    faixa aparecer pra quem desligou, não sumir pra quem contava com ela.
    """
    try:
        with pool.connection() as c:
            r = c.execute("select avisos_na_fila from contas where id=%s",
                          (conta_id,)).fetchone()
    except Exception as e:  # noqa: BLE001 — base sem a 266 ainda
        _log.warning("não deu pra ler avisos_na_fila da conta %s: %s: %s",
                     conta_id, type(e).__name__, e)
        return True
    return True if not r or r[0] is None else bool(r[0])


def definir_faixa(pool, conta_id: int, ligada: bool) -> None:
    """Liga ou desliga a faixa desta conta (tela Empresa)."""
    with pool.connection() as c:
        c.execute("update contas set avisos_na_fila=%s where id=%s",
                  (bool(ligada), conta_id))
        c.commit()


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
    from db.conexao import memo as _memo
    with pool.connection() as c:
        # o nicho e a data de criação da conta são cadastro, não lista: lembrados
        # por requisição (ver `db.conexao.memo`), porque a mesma tela já lê a
        # tabela `contas` noutro lugar e cada leitura custa a travessia até o banco
        nicho = _memo(("conta_nicho", conta_id), lambda: c.execute(
            """select n.slug, ct.criado_em from contas ct
                 left join nichos n on n.id = ct.nicho_id
                where ct.id=%s""", (conta_id,)).fetchone())
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
