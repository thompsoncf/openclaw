"""Módulo "Relatórios" — Vendas, Contas a pagar, Contas a receber, Contas
pagas, Comissão e Contas recebidas, com resultado na tela e exportação em PDF
(impressão do navegador, mesmo padrão já usado no holerite e na separação de
pedidos).

Dados reais, reaproveitando o que já existe:
  - Vendas          -> lancamentos (tipo='receita', natureza='empresa')
  - Contas a pagar   -> titulos (tipo='pagar',   status='aberto')  [finance.empresa]
  - Contas a receber -> titulos (tipo='receber', status='aberto')  [finance.empresa]
  - Contas pagas     -> lancamentos (tipo='despesa', natureza='empresa')
  - Contas recebidas -> lancamentos (tipo='receita', natureza='empresa')

    Estas duas liam `titulos` baixados e por isso viviam VAZIAS: em 01/09/2026 a
    produção inteira tinha 2 títulos pagos, os dois baixados pelo sistema, e
    nenhum baixado por gente. Quem responde "quanto eu paguei" é o caixa, não o
    compromisso — ver o docstring de `_dados_caixa`. As duas abas de compromisso
    (a pagar / a receber) continuam lendo `titulos`, que é onde elas estão certas.
  - Comissão         -> lancamentos de vendas agrupados por membro_id, aplicando o
                        membros.comissao_pct de cada um (migração 137). Vendedor sem
                        % configurada aparece com comissão R$ 0,00 e um aviso na tela
                        apontando pra Equipe.
"""
import logging
from datetime import date, timedelta

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from contas import equipe as eq
from db.conexao import get_pool
from finance import agenda as _ag
from finance import empresa as emp
from finance import models as mod
from finance import vendas
from web.portal import _render, _env, conta_logada, brl as _brl, _mascara_cnpj

_log = logging.getLogger("painel.relatorios")

router = APIRouter()

PERIODOS = [
    ("mes", "Este mês"),
    ("mes_passado", "Mês passado"),
    ("90d", "Últimos 90 dias"),
    ("ano", "Este ano"),
    ("todos", "Todo o período"),
    # "Período específico" NÃO entra aqui, e não é esquecimento: as outras oito
    # abas chamam `_intervalo(periodo)` sem `de`/`ate`, então escolher datas ali
    # cairia calado no mês corrente — filtro que mente é pior que filtro que não
    # existe. Quando alguma delas precisar, é só passar os dois argumentos.
]

# A pílula Agenda tem a sua própria lista, e o motivo é que ela olha pro outro
# lado do tempo. Os presets acima terminam todos em HOJE porque nasceram pra
# Vendas e Contas pagas, que são histórico. A agenda de um salão é o contrário:
# medido na Prime em 31/08/2026, 38 dos 60 compromissos estavam no FUTURO, 13
# deles em 2027 — e "Este mês" e "Este ano" mostravam os mesmos 22. Dezembro
# tinha 8 festas e não existia jeito de pedir dezembro: ou 22, ou os 60 de
# "Todo o período".
PERIODOS_AGENDA = [
    ("mes", "Este mês"),
    ("mes_passado", "Mês passado"),
    ("prox30", "Próximos 30 dias"),
    ("prox90", "Próximos 90 dias"),
    ("ano", "Este ano"),
    ("todos", "Todo o período"),
    ("personalizado", "Período específico…"),
]
_PERIODO_ROTULO = dict(PERIODOS) | dict(PERIODOS_AGENDA)


def periodos_da_aba(tipo: str) -> list[tuple[str, str]]:
    """As opções de período que a aba oferece. Só a Agenda difere."""
    return PERIODOS_AGENDA if tipo == "agenda" else PERIODOS


def _pode_ver(request: Request):
    """Só quem tem a capacidade financeiro (dono ou membro com o papel) entra —
    mesmo gate usado pra Empresa/DRE, já que relatório financeiro é módulo PJ."""
    conta = conta_logada(request)
    if conta is None:
        return None, RedirectResponse("/login", status_code=303)
    if not conta[11]:  # tem_pj
        return None, RedirectResponse("/painel", status_code=303)
    caps = eq.caps_do_papel(request.session.get("papel", "dono"))
    if not caps["financeiro"]:
        return None, RedirectResponse("/painel", status_code=303)
    return conta, None


def _pode_liberar(request: Request) -> bool:
    """Quem libera conta a pagar: a capacidade `gerir`, exclusiva do dono.

    É a MESMA régua de `_so_o_dono` no portal, e é de propósito que seja a mesma
    função de capacidades e não uma cópia da conclusão: a pergunta "quem pode
    liberar" não pode ter duas respostas em duas telas."""
    return bool(eq.caps_do_papel(request.session.get("papel", "dono")).get("gerir"))


def _dia(s) -> date | None:
    """A data que vem do `<input type="date">`: sempre AAAA-MM-DD, nunca o texto
    que o usuário vê. O navegador mostra dd/mm/aaaa em aparelho brasileiro e
    manda ISO no formulário — quem formata é ele, não nós."""
    if isinstance(s, date):
        return s
    try:
        return date.fromisoformat((s or "").strip())
    except ValueError:
        return None


def _fim_do_mes(d: date) -> date:
    return (d.replace(day=28) + timedelta(days=4)).replace(day=1) - timedelta(days=1)


def _intervalo(periodo: str, de=None, ate=None,
               ate_o_fim: bool = False) -> tuple[date, date]:
    """O par (início, fim) do período pedido.

    `ate_o_fim` estica "este mês"/"este ano" até o ÚLTIMO dia em vez de parar
    hoje. Só a pílula Agenda liga isso (decisão do dono em 31/08/2026): num
    relatório de histórico, "este mês" que vai além de hoje mostraria linha
    nenhuma; numa agenda, parar em hoje esconde justamente a festa que ainda vai
    acontecer.

    `de`/`ate` só valem com `periodo='personalizado'`. Data faltando ou torta cai
    no mês corrente — filtro quebrado não pode virar tela vazia sem explicação.
    Invertidas (de > ate), são trocadas: é engano de digitação, não pedido.
    """
    hoje = date.today()
    if periodo == "personalizado":
        d, a = _dia(de), _dia(ate)
        if d and a:
            return (a, d) if d > a else (d, a)
        if d:
            return d, _fim_do_mes(d)
        if a:
            return a.replace(day=1), a
        return hoje.replace(day=1), _fim_do_mes(hoje) if ate_o_fim else hoje
    if periodo == "todos":
        return date(2000, 1, 1), hoje
    if periodo == "mes_passado":
        fim = hoje.replace(day=1) - timedelta(days=1)
        return fim.replace(day=1), fim
    if periodo == "90d":
        return hoje - timedelta(days=90), hoje
    if periodo == "prox30":
        return hoje, hoje + timedelta(days=30)
    if periodo == "prox90":
        return hoje, hoje + timedelta(days=90)
    if periodo == "ano":
        return date(hoje.year, 1, 1), date(hoje.year, 12, 31) if ate_o_fim else hoje
    return hoje.replace(day=1), _fim_do_mes(hoje) if ate_o_fim else hoje  # "mes"


def _fmt(d) -> str:
    return d.strftime("%d/%m/%Y") if d else "—"


def _letterhead(pool, conta) -> dict:
    """Marca (logo/cor) + dados cadastrais da empresa pro timbre do PDF — mesmo
    kit que holerite e recibo do PDV já usam (finance.marca/empresa.marca_empresa)."""
    d = emp.obter_dados_empresa(pool, conta[0])
    endereco = ", ".join(p for p in (
        d["endereco"], d["bairro"],
        f"{d['cidade']}/{d['uf']}" if d["cidade"] else d["uf"],
        f"CEP {d['cep']}" if d["cep"] else "",
    ) if p)
    return {
        "marca": emp.marca_empresa(pool, conta[0]),
        "empresa_nome": d["razao_social"] or d["nome_fantasia"] or conta[2] or "",
        "cnpj_fmt": _mascara_cnpj(d["documento"]),
        "endereco_fmt": endereco,
    }


def _soma(linhas, chave):
    return sum(int(r[chave]) for r in linhas)


def _col(chave, rotulo, num=False, brl=False, tag=False, flex=False, cli=False,
         extra=None, venc=False, parte=None, zero=None):
    """`flex=True` marca uma coluna ELÁSTICA da tabela — as que podem encolher
    quando falta largura. São sempre as de nome livre (descrição, cliente,
    contraparte), e todo relatório tem ao menos uma.

    Existe porque o oposto quebrava a tela. A tabela tinha `nowrap` em TODA célula
    e `min-width:640px`: com seis colunas de nome longo nada cabia nos 720px do
    cartão, a tabela rolava pro lado e o que aparecia era o MEIO dela. No print de
    26/08 o cliente lia "ço Pelle Clínica" e "erson Venici" — não era truncagem, era
    a coluna Data e o começo do nome fora da área visível. Uma coluna elástica, que
    corta no FIM com reticências, mantém todas as outras no lugar e o começo do nome
    sempre visível — que é a parte que identifica o cliente.

    **Duas elásticas na mesma tabela é permitido, e é o caso de Contas a
    pagar/receber**, que mostram Descrição E Fornecedor — dois nomes livres, os
    dois longos. O que continua proibido é ZERO — aí volta a rolagem lateral do
    print — e marcar como elástica uma coluna de tamanho previsível (valor, data),
    que encolheria justo o que já cabia.

    **`parte` é obrigatório quando há duas, e a razão é um erro meu.** Eu tinha
    escrito aqui que duas elásticas dividiriam a sobra em partes iguais sozinhas,
    "repartimento do algoritmo da tabela, não sorte". É falso, e o print do dono
    em 04/09/2026 mostrou: numa tabela de largura automática, dois pedidos iguais
    que não cabem NÃO viram duas fatias iguais — **a primeira leva tudo e a
    segunda desce pro piso dela**. Medido no Chromium com o CSS real: o Fornecedor
    recebia 107px numa janela de 900 e os mesmos 107px numa de 1500, cortado em 8
    de 8 linhas. `parte=55` / `parte=45` declara a divisão, e aí ela acontece.
    Medido também que declarar no `<th>` não resolve: a largura precisa estar na
    célula que carrega o `max-width:0`, ou seja no `<td>`."""
    return {"chave": chave, "rotulo": rotulo, "num": num, "brl": brl, "tag": tag,
            "flex": flex, "parte": parte,
            # `zero` é o que a célula de dinheiro escreve NO LUGAR de "R$ 0,00".
            # Nasceu da conta de valor variável (196): a água repete a data e não
            # o valor, então ela fica sem valor até o boleto chegar. "R$ 0,00" ali
            # mentiria duas vezes — diz que a conta é de graça e não pede nada de
            # quem olha. Só vale onde a coluna declara; zero legítimo (um total
            # que deu zero) continua saindo como zero.
            "zero": zero,
            # `extra` pendura uma SEGUNDA coisa na mesma célula, opcional — pílula
            # ao lado, numa coluna de tag; linha de baixo, menor, numa elástica.
            # Nasceu da coluna "Conferir", que ninguém entendia: ela nomeava a ação
            # ("vá conferir") em vez do fato ("esta conta talvez já esteja paga"),
            # e ficava vazia em 25 das 30 linhas da Prime. Conteúdo que aparece em
            # poucas linhas não paga uma coluna, que cobra largura de todas.
            "extra": extra,
            # `venc=True` é a célula de vencimento de Contas a pagar/receber: a
            # data MAIS o prazo ("15/08/2026 · há 20 dias"), pintada. Ela substitui
            # a coluna Status inteira, e substitui dizendo mais: "Vencida" era
            # `vencimento < hoje` — fato nenhum que a data já não tivesse —, e
            # achatava numa palavra só a diferença entre dever há 4 dias e dever há
            # 20. A coluna cobrava 278px por isso, medidos.
            "venc": venc,
            # `cli=True` é a coluna Cliente da Agenda, que precisa de marcação
            # própria: nome lido do TÍTULO sai apagado e com selo, e a célula vira
            # link pra ligar ao cadastro. Um flag e não `|safe`: a escapagem
            # continua valendo, o template é que sabe montar esta célula.
            "cli": cli}


# Grupo do plano de contas que NÃO é operação: 7 é onde mora "Aporte de Sócios"
# (7.1.05, migração 186) e a distribuição de lucros. Dinheiro que atravessa a
# empresa sem ser negócio dela.
_GRUPO_NAO_OPERACIONAL = 7

# O que denuncia aporte/empréstimo quando NEM a categoria NEM o plano de contas
# pegam. Não é firula: em produção o aporte de R$ 2.500 da Prime está com
# categoria "Outros" (que é operacional) e plano "1.2.03 Outras Receitas" (grupo
# 1, também operacional) — os dois portões anteriores passam batido nele. Casar
# no texto é o último recurso, e por isso a linha vai pro bloco "fora de Vendas"
# EXPLICANDO o motivo, nunca some calada: se o casamento estiver errado, o dono
# vê e corrige o lançamento na origem.
_TEXTO_NAO_VENDA = ("aporte", "emprestimo", "empréstimo", "transferencia entre",
                    "transferência entre")

# Como a venda chegou. Só três portas existem, e a tela diz qual foi.
_CANAL_VENDA = {"titulo": ("Funil", "ok"), "balcao": ("Balcão", "ok")}


def _fora_de_vendas(origem, categoria, descricao, grupo_plano):
    """Esta linha de receita é uma VENDA de produto/serviço? Se não, por quê?

    Devolve None pra venda, ou a chave do motivo. Três portões, nesta ordem:

    1. **extrato** — a importação do OFX traz todo crédito que caiu na conta. Um
       Pix que entrou é o PAGAMENTO de uma venda, não uma venda nova, e a mesma
       venda costuma já estar registrada pelo funil ou pela foto do comprovante.
       Deixar entrar é contar duas vezes por construção. Na Prime esse canal
       sozinho respondia por R$ 21.470,05 — 69% do que a aba somava.
    2. **não é receita do negócio** — aporte de sócio, empréstimo, transferência.
       Usa `receita_e_operacional()`, a MESMA régua que a tela de Financeiro já
       aplica desde 24/08 ("Entrou, mas não é receita"), mais o grupo 7 do plano
       de contas, mais o texto. Três sinais porque nenhum é completo sozinho: os
       dois aportes da Prime caíram em categorias e planos diferentes, e só o
       texto pegou os dois.
    3. o terceiro motivo — a linha repetida — não mora aqui: depende de olhar o
       conjunto, e é resolvido em `_dados_vendas`.
    """
    if (origem or "") == "extrato":
        return "extrato"
    if not mod.receita_e_operacional(categoria or ""):
        return "nao_venda"
    if grupo_plano == _GRUPO_NAO_OPERACIONAL:
        return "nao_venda"
    texto = (descricao or "").lower()
    if any(t in texto for t in _TEXTO_NAO_VENDA):
        return "nao_venda"
    return None


def _dados_vendas(pool, conta_id, periodo):
    """Vendas: o que o negócio VENDEU — produto ou serviço.

    Antes esta aba somava todo lançamento de receita da empresa, viesse de onde
    viesse. Na Prime (conta 34) isso fazia a tela dizer R$ 31.020,05 onde a
    empresa tinha vendido R$ 6.100,00 — cinco vezes mais. Conferido em 01/09/2026,
    o que inflava eram três coisas diferentes:

        R$ 21.470,05  12 recebimentos importados do extrato do banco
        R$  2.700,00   2 aportes de sócio
        R$    750,00   1 sinal contado duas vezes (o mesmo dinheiro entrou pela
                       baixa do título E pela foto do comprovante)

    A causa de fundo era o desenho das abas, não esta consulta: Vendas era a única
    aba que lia o caixa, então tudo que se mexia caía nela. Com Contas
    pagas/recebidas lendo `lancamentos` (ver `_dados_caixa`), o que sai daqui tem
    para onde ir — e é por isso que os dois passos andam juntos.

    Nada some calado: o que não é venda vai pro bloco `fora`, com quantidade,
    valor e motivo, apontando pra aba que agora mostra aquele dinheiro. A regra 0
    do CLAUDE.md vale pra tela — dinheiro do cliente não desaparece de uma
    contagem sem explicação.
    """
    ini, fim = _intervalo(periodo)
    with pool.connection() as c:
        rows = c.execute(
            """select l.data, l.descricao, l.categoria, l.origem,
                      coalesce(m.nome, '-') as vendedor, l.valor_centavos,
                      p.grupo, coalesce(t.contraparte, cl.nome, '') as cliente
                 from lancamentos l
                 left join membros m on m.id = l.membro_id
                 left join plano_contas p on p.id = l.plano_conta_id
                 left join clientes cl on cl.id = l.cliente_id
                 left join titulos t on t.lancamento_id = l.id and t.conta_id = l.conta_id
                where l.conta_id=%s and l.tipo='receita' and l.natureza='empresa'
                  and l.data >= %s and l.data <= %s
                order by l.data desc, l.id desc limit 300""",
            (conta_id, ini, fim),
        ).fetchall()

    # o dinheiro que entrou pela baixa do título é o registro BOM da venda (ele
    # sabe o cliente e o orçamento). Quando a mesma quantia, no mesmo dia, também
    # entrou por outra porta, a outra é eco do mesmo Pix — foi o caso da Bianca
    # Oliveira em 28/08: um sinal de R$ 750, duas linhas, R$ 1.500 na soma.
    do_titulo = {(r[0], int(r[5] or 0)) for r in rows if (r[3] or "") == "titulo"}

    linhas, fora = [], {}

    def _fora(chave, valor):
        d = fora.setdefault(chave, {"n": 0, "centavos": 0})
        d["n"] += 1
        d["centavos"] += valor

    for r in rows:
        valor = int(r[5] or 0)
        origem = r[3] or ""
        motivo = _fora_de_vendas(origem, r[2], r[1], r[6])
        if motivo is None and origem != "titulo" and (r[0], valor) in do_titulo:
            motivo = "repetida"
        if motivo:
            _fora(motivo, valor)
            continue
        canal, cor = _CANAL_VENDA.get(origem, ("Manual", "neutro"))
        linhas.append({
            "data": _fmt(r[0]), "descricao": (r[1] or "").strip() or "—",
            "cliente": (r[7] or "").strip() or "—",
            "canal": canal, "canal_cor": cor,
            "vendedor": r[4], "valor_centavos": valor,
        })

    total = _soma(linhas, "valor_centavos")
    n = len(linhas)
    hoje_str = _fmt(date.today())
    vendido_hoje = _soma([r for r in linhas if r["data"] == hoje_str], "valor_centavos")
    dados = {
        "label": "Vendas", "mock": False,
        "colunas": [_col("data", "Data"), _col("descricao", "O que foi vendido", flex=True),
                    _col("cliente", "Cliente"), _col("canal", "Origem", tag=True),
                    _col("vendedor", "Vendedor"),
                    _col("valor_centavos", "Valor", num=True, brl=True)],
        "linhas": linhas, "col_total": "valor_centavos", "total_centavos": total,
        "metricas": [("Total vendido", _brl(total)), ("Nº de vendas", str(n)),
                     ("Ticket médio", _brl(total // n if n else 0)), ("Vendido hoje", _brl(vendido_hoje))],
    }
    if fora:
        dados["fora"] = _bloco_fora(fora)
    return dados


# Cada motivo com o texto que o dono entende e a aba pra onde aquele dinheiro
# foi. O "onde está" é a parte que faz o bloco ser informação e não desculpa.
_MOTIVO_FORA = {
    "extrato": ("recebimento{s} importado{s} do extrato do banco",
                "é a perna bancária do dinheiro, não uma venda nova",
                "recebidas"),
    "nao_venda": ("aporte{s}, empréstimo{s} ou transferência{s}",
                  "entrou no caixa, mas não é faturamento do negócio",
                  "recebidas"),
    "repetida": ("linha{s} do mesmo dinheiro já contado no título",
                 "o sinal entrou pelo funil e também pela foto do comprovante",
                 "recebidas"),
}


def _bloco_fora(fora: dict) -> dict:
    """O rodapé de "entrou no caixa, mas não é venda".

    Existe porque tirar linha de uma tela de dinheiro sem dizer para onde ela foi
    é a mesma família de erro que esconder o dinheiro: o dono olha o total, não
    reconhece, e perde a confiança na tela inteira. Cada motivo sai com
    quantidade, valor, explicação e a aba onde aquele dinheiro está agora."""
    itens = []
    for chave in ("extrato", "nao_venda", "repetida"):
        d = fora.get(chave)
        if not d:
            continue
        rotulo, porque, aba = _MOTIVO_FORA[chave]
        s = "s" if d["n"] != 1 else ""
        itens.append({"n": d["n"], "texto": rotulo.format(s=s), "porque": porque,
                      "aba": aba, "centavos": d["centavos"]})
    return {"itens": itens, "centavos": sum(i["centavos"] for i in itens)}


def _prazo(vencimento, hoje) -> tuple[str, str]:
    """"há 20 dias", "amanhã", "em 6 dias" — e a cor. Devolve ("", "") sem data.

    É o que substituiu a coluna Status, e substituiu dizendo mais. A régua mora em
    `finance/empresa.py` porque a lista da aba Empresa diz a mesma coisa na mesma
    linha ("vence 15/08 · há 20 dias"): duas telas falando do mesmo vencimento não
    podem falar diferente.
    """
    return emp.prazo_do_vencimento(vencimento, hoje)


def _dados_titulos_abertos(pool, conta_id, tipo):
    """Contas a pagar/receber: SEMPRE mostra tudo que está em aberto — período não
    se aplica aqui (uma conta aberta continua aberta até ser paga, não "expira").

    O aviso de "talvez já paga" é a metade nova: em produção o dinheiro sai pelo
    extrato e pela foto do comprovante, e o título fica aberto pra sempre porque
    ninguém clica em "pago" — em 01/09/2026 a produção inteira tinha 38 títulos
    abertos, 11 vencidos (R$ 27.170,85, um deles há 58 dias) e ZERO baixas feitas
    por gente. O aviso não resolve isso; ele deixa de esconder.

    **A DESCRIÇÃO é a coluna que faz a linha ser identificável**, e ela faltava.
    Medido nos 30 títulos abertos da Prime em 04/09/2026: 16 das 30 linhas eram
    idênticas a outra linha da mesma tela — mesmo Fornecedor e mesmo Valor.
    "IMPOSTOS (FGTS/INSS/DAS)" aparecia três vezes no dia 18/09 sem dizer qual era
    o FGTS, qual o INSS e qual o DAS; "JAQUELINE DUARTE" duas vezes por R$ 1.500,00
    (2ª quinzena de agosto e 1ª de setembro); "PREFEITURA DE TERESINA" duas vezes
    por R$ 1.160,85 (IPTU 3/6 e 4/6). Com a descrição no lugar, sobram 4. As 30 já
    tinham descrição gravada — não havia campo a preencher, só a mostrar.

    **E a CATEGORIA saiu pra abrir essa largura.** Ela escrevia a mesma palavra em
    toda linha: em toda a produção, cada aba tem um único valor de categoria —
    "Fornecedores" nas 34 contas a pagar, "Serviços"/"Vendas" nas a receber. Uma
    coluna constante não informa; ocupa. (O campo continua no banco, no filtro e
    no que a aba Empresa mostra — o que saiu é a coluna desta tabela.)"""
    hoje = date.today()
    tits = emp.listar_titulos(pool, conta_id, status="aberto", tipo=tipo, limite=300)
    candidatos = emp.pagamentos_candidatos(pool, conta_id, tits, tipo)
    verbo = "pago" if tipo == "pagar" else "recebido"
    # a pílula concorda com "a conta", que é o sujeito da linha: "Talvez paga".
    paga = "paga" if tipo == "pagar" else "recebida"
    linhas = []
    for t in tits:
        if t["atrasado"]:
            status, cor = "Vencida", "erro"
        else:
            dias = (t["vencimento"] - hoje).days if t["vencimento"] else None
            status, cor = "A vencer", ("aviso" if dias is not None and dias <= 7 else "ok")
        c = candidatos.get(t["id"])
        # AQUI SÓ AVISA — quem fecha a conta é a aba Empresa. Escolha do dono em
        # 04/09/2026: "a caixa que fica no final pra marcar a conta como paga pode
        # remover, pois tudo vai ficar lá dentro da Empresa". O aviso fica: ele é
        # informação, não ação, e é o que manda a pessoa olhar a conta.
        if not c:
            talvez = ""
        elif c["n"] == 1:
            talvez = f"Talvez {paga} · {_fmt(c['data'])}"
        else:
            # dizer QUAL seria chute quando há vários do mesmo valor por perto —
            # e chute numa tela de dinheiro é o que este aviso existe pra evitar
            talvez = f"Talvez {paga} · {c['n']} iguais por perto"
        prazo, prazo_cor = _prazo(t["vencimento"], hoje)
        # `sel_id` só existe na linha que AINDA precisa de liberação: é o que a
        # tela usa pra desenhar a caixa. Marcar o que já está liberado não faria
        # nada (a régua em `decidir_aprovacao` ignora), mas ofereceria caixa que
        # não muda nada — e caixa que não faz nada foi exatamente a queixa do
        # dono no print de 04/09/2026.
        espera = (t.get("aprovacao") or "autorizado") != "autorizado"
        linhas.append({
            "vencimento": _fmt(t["vencimento"]),
            "prazo": prazo, "prazo_cor": prazo_cor,
            "aprovacao": t.get("aprovacao") or "autorizado",
            "sel_id": t["id"] if (espera and tipo == "pagar") else None,
            "descricao": (t["descricao"] or "").strip() or "—",
            "contraparte": t["cliente_nome"] or t["contraparte"] or "—",
            # `status` e `categoria` ficam na LINHA sem ter coluna: as métricas do
            # topo contam por eles, e o dado continua servindo a quem lê a linha.
            "categoria": t["categoria"] or "—", "status": status, "status_cor": cor,
            "talvez": talvez, "talvez_cor": "aviso",
            "valor_centavos": t["valor_centavos"],
        })
    total = _soma(linhas, "valor_centavos")
    vencidas = [r for r in linhas if r["status"] == "Vencida"]
    a_vencer_7d = [r for r in linhas if r["status_cor"] == "aviso"]
    a_conferir = [r for r in linhas if r["talvez"]]
    rotulo_col = "Fornecedor" if tipo == "pagar" else "Cliente"
    label = "Contas a pagar" if tipo == "pagar" else "Contas a receber"
    dados = {
        "label": label, "mock": False, "sem_periodo": True,
        # Quatro colunas, e cada uma paga o lugar dela. A descrição vem PRIMEIRO
        # porque é ela que responde "que conta é essa" — mesma ordem que a aba
        # Empresa já usa (`{{ t.descricao }} · {{ t.contraparte }}`).
        #
        # O Status saiu (vira o prazo dentro do Vencimento) e a ressalva "Talvez
        # paga" desceu pra segunda linha da descrição, onde só engorda a linha que
        # a tem. Medido no Chromium: com Status como coluna sobravam 949px pros
        # dois nomes numa janela de 1500 e o fornecedor era cortado em toda linha;
        # sem ela sobram 1.155px e não corta nenhum até 1280.
        "colunas": [_col("vencimento", "Vencimento", venc=True, extra="prazo"),
                    _col("descricao", "Descrição", flex=True, parte=55,
                         extra="talvez"),
                    _col("contraparte", rotulo_col, flex=True, parte=45),
                    _col("valor_centavos", "Valor", num=True, brl=True,
                         zero="— informar")],
        "linhas": linhas, "col_total": "valor_centavos", "total_centavos": total,
        "metricas": [("Total em aberto", _brl(total)),
                     ("Vencidas", f"{len(vencidas)} · {_brl(_soma(vencidas, 'valor_centavos'))}"),
                     ("A vencer em 7 dias", _brl(_soma(a_vencer_7d, "valor_centavos"))),
                     (f"Talvez já {verbo}", f"{len(a_conferir)} · "
                      f"{_brl(_soma(a_conferir, 'valor_centavos'))}")],
    }
    # A LIBERAÇÃO EM LOTE, e ela mora AQUI e não na aba Empresa por escolha do
    # dono em 04/09/2026 — "faz mais sentido no relatório". Ele tem razão: é esta
    # tela que mostra descrição, fornecedor, prazo e valor lado a lado, que é o
    # que decide se a conta pode ser paga. A aba Empresa é a tela de manutenção
    # (adicionar, editar, apagar), e a caixa que morava lá aparecia em 1 de 33
    # linhas — a 32ª —, o que gerou o print perguntando pra que servia.
    esperando = [r for r in linhas if r["sel_id"]]
    if esperando:
        dados["selecao"] = {
            "url": "/painel/relatorios/liberar", "campo": "ids",
            "aba": "contas_pagar", "rotulo": "liberar o pagamento",
            "n": len(esperando),
            "centavos": _soma(esperando, "valor_centavos"),
        }
    if a_conferir:
        plural = "s" if len(a_conferir) != 1 else ""
        dados["aviso_config"] = (
            f"{len(a_conferir)} conta{plural} em aberto {'têm' if plural else 'tem'} "
            f"um pagamento do mesmo valor por perto — pode ser que já "
            f"{'estejam' if plural else 'esteja'} {verbo}{plural}. Confira antes de "
            "cobrar ou pagar de novo; a baixa continua sendo sua, o Zaq só avisa."
        )
    return dados


# De onde o dinheiro veio, pra tela dizer isso em uma palavra. A cor separa o
# caminho COMPLETO (nasceu de uma venda ou de um título e fechou o ciclo) do
# caminho que só passou pelo caixa — é a diferença que a aba existe pra mostrar.
_ORIGEM = {
    "balcao":  ("Balcão", "ok"),
    "titulo":  ("Título", "ok"),
    "foto":    ("Comprovante", "neutro"),
    "manual":  ("Manual", "neutro"),
    "folha":   ("Folha", "neutro"),
    "replica": ("Cópia", "neutro"),
    "extrato": ("Extrato", "info"),
}


def _dados_caixa(pool, conta_id, tipo, periodo):
    """Contas pagas / Contas recebidas: o DINHEIRO que andou, não o título baixado.

    Antes estas duas abas liam `titulos` com status='pago'. O efeito, conferido em
    produção em 01/09/2026: **Contas pagas estava vazia em TODAS as contas, desde
    sempre**. Na Prime (conta 34) saíram R$ 34.232,86 do caixa em agosto — 53
    pagamentos — e a tela mostrava R$ 0,00.

    A causa não era bug de consulta, era desenho. Título só vira 'pago' quando
    alguém clica no botão, e o levantamento mostrou que **ninguém nunca clicou**:
    das 40 linhas de `titulos` da produção inteira, 2 estavam pagas, e as duas
    foram baixadas pelo próprio sistema (o fluxo do sinal, em finance/vendas.py).
    Enquanto isso o dinheiro real entra sozinho por dois caminhos que nascem
    direto em `lancamentos` e nunca encostam num título: a importação do extrato
    (OFX) e a foto do comprovante mandada no WhatsApp.

    Então a aba passa a responder a PERGUNTA em vez de espelhar a tabela:
    "quanto eu paguei/recebi, e do quê" é uma pergunta de caixa. O título continua
    existindo e continua certo onde ele é a resposta — Contas a pagar e Contas a
    receber, que são sobre COMPROMISSO e não mudaram de fonte.

    Nada se perde na troca: título que foi baixado gerou lançamento com
    `origem='titulo'` (dar_baixa_titulo, finance/empresa.py), então as linhas que
    a aba mostrava antes continuam aparecendo — agora com o resto junto.

    Só `natureza='empresa'`, igual à aba Vendas: relatório aqui é módulo PJ, e
    despesa pessoal do dono não é conta paga da empresa. O que está "a definir"
    (natureza nula) fica de fora da soma mas NÃO fica calado — vira aviso na tela,
    porque em produção isso é dinheiro de verdade (a conta 3 tem R$ 20.901,18 de
    despesa sem natureza definida) e sumir com ele é o erro que esta aba acabou
    de deixar de cometer.
    """
    ini, fim = _intervalo(periodo)
    lanc_tipo = "despesa" if tipo == "pagar" else "receita"
    with pool.connection() as c:
        rows = c.execute(
            """select l.data, l.descricao, l.categoria, l.origem, l.valor_centavos,
                      coalesce(cl.nome, '') as cliente, t.descricao as titulo,
                      t.id as titulo_id
                 from lancamentos l
                 left join clientes cl on cl.id = l.cliente_id
                 left join titulos t on t.lancamento_id = l.id and t.conta_id = l.conta_id
                where l.conta_id=%s and l.tipo=%s and l.natureza='empresa'
                  and l.data >= %s and l.data <= %s
                order by l.data desc, l.id desc limit 500""",
            (conta_id, lanc_tipo, ini, fim),
        ).fetchall()
        # o que ainda não foi classificado como empresa ou pessoal, no MESMO
        # período: não entra na conta, mas a tela avisa que existe
        indef = c.execute(
            """select count(*), coalesce(sum(valor_centavos), 0) from lancamentos
                where conta_id=%s and tipo=%s and natureza is null
                  and data >= %s and data <= %s""",
            (conta_id, lanc_tipo, ini, fim),
        ).fetchone()
    linhas = []
    for r in rows:
        rotulo, cor = _ORIGEM.get(r[3] or "", ((r[3] or "—").capitalize(), "neutro"))
        linhas.append({
            "data": _fmt(r[0]),
            # a descrição é onde o nome está de verdade ("Serviço de pintura —
            # Ronaldo Vaz"); o cadastro de cliente só existe em venda de balcão,
            # e é o que salva a linha quando a descrição vem vazia
            "descricao": (r[1] or "").strip() or r[5] or "—",
            "categoria": r[2] or "—",
            "origem": rotulo, "origem_cor": cor,
            # a conta que este pagamento fechou. VAZIO quando não fechou nenhuma,
            # e não "nenhum título": em produção a coluna é vazia em quase toda
            # linha, e 53 pílulas dizendo "nenhum" viram ruído. Assim a pílula só
            # aparece onde existe o elo — que é a informação rara e a que importa.
            "quitou": (r[6] or "")[:60], "quitou_cor": "ok",
            # desfazer mora AQUI, e não na aba de compromisso, porque lá só
            # aparecem títulos ABERTOS: assim que a conciliação acontece o título
            # some de vista, e o dono ficaria sem lugar pra corrigir o engano.
            # Só oferece em conciliação: baixa comum criou o lançamento dela
            # (origem='titulo'), e reabrir aquilo deixaria dinheiro sem dono.
            "acao_post": ({
                "url": "/painel/relatorios/desfazer-conciliacao",
                "campos": {"titulo_id": r[7], "tipo": tipo},
                "rotulo": "✕", "titulo": f"Desfazer o vínculo com “{(r[6] or '')[:40]}”",
                "confirmar": (f"Desligar este pagamento de “{(r[6] or '')[:60]}”?\n\n"
                              "A conta volta pra em aberto. O pagamento continua no "
                              "caixa, do jeito que está — some só o vínculo."),
            } if r[7] and (r[3] or "") != "titulo" else None),
            "valor_centavos": int(r[4] or 0),
        })
    total = _soma(linhas, "valor_centavos")
    maior = max((r["valor_centavos"] for r in linhas), default=0)
    de_titulo = sum(1 for r in linhas if r["quitou"])
    rotulo_col = "Fornecedor / descrição" if tipo == "pagar" else "Cliente / descrição"
    label = "Contas pagas" if tipo == "pagar" else "Contas recebidas"
    verbo = "pago" if tipo == "pagar" else "recebido"
    quantos = "Nº de pagamentos" if tipo == "pagar" else "Nº de entradas"
    dados = {
        "label": label, "mock": False,
        "acao": any(r["acao_post"] for r in linhas), "acao_rotulo": "Desfazer",
        "colunas": [_col("data", "Pagamento" if tipo == "pagar" else "Recebimento"),
                    _col("descricao", rotulo_col, flex=True), _col("categoria", "Categoria"),
                    _col("origem", "Entrou por", tag=True),
                    _col("quitou", "Quitou", tag=True),
                    _col("valor_centavos", f"Valor {verbo}", num=True, brl=True)],
        "linhas": linhas, "col_total": "valor_centavos", "total_centavos": total,
        "metricas": [(f"Total {verbo} no período", _brl(total)), (quantos, str(len(linhas))),
                     # o número que mostra o buraco: quantos fecharam o ciclo com
                     # o compromisso em vez de só passarem pelo caixa
                     ("Quitaram um título", str(de_titulo)),
                     ("Maior valor", _brl(maior))],
    }
    n_indef, v_indef = int(indef[0] or 0), int(indef[1] or 0)
    if n_indef:
        plural = "s" if n_indef != 1 else ""
        dados["aviso_config"] = (
            f"{n_indef} lançamento{plural} somando {_brl(v_indef)} ainda "
            f"{'estão' if n_indef != 1 else 'está'} como “a definir” e "
            f"{'ficam' if n_indef != 1 else 'fica'} fora desta conta — diga se "
            "são da empresa ou pessoais no Financeiro."
        )
    return dados


def _dados_comissao(pool, conta_id, periodo):
    """Comissão do período por vendedor.

    A conta vive em finance/comissao.py — o MESMO lugar que o Cockpit consulta.
    Antes cada tela fazia a sua e os números não batiam: aqui somava
    `lancamentos`, lá somava o valor estimado do lead."""
    from finance import comissao as com
    ini, fim = _intervalo(periodo)
    linhas = []
    sem_config = 0
    sem_vendedor = 0
    for r in com.por_vendedor(pool, conta_id, ini, fim):
        if r["sem_vendedor"]:
            sem_vendedor += 1
        elif not r["configurada"]:
            sem_config += 1
        linhas.append({
            "vendedor": r["vendedor"], "vendas_centavos": r["recebido_centavos"],
            "percentual": (f"{r['comissao_pct']:g}%" if r["configurada"]
                           else ("— sem vendedor" if r["sem_vendedor"] else "— não configurada")),
            "comissao_centavos": r["comissao_centavos"],
        })
    total = _soma(linhas, "comissao_centavos")
    vendas_totais = _soma(linhas, "vendas_centavos")
    destaque = max(linhas, key=lambda r: r["comissao_centavos"])["vendedor"] if linhas else "—"
    dados = {
        "label": "Comissão", "mock": False,
        "colunas": [_col("vendedor", "Vendedor", flex=True), _col("vendas_centavos", "Recebido no período", num=True, brl=True),
                    _col("percentual", "% comissão", num=True), _col("comissao_centavos", "Comissão a pagar", num=True, brl=True)],
        "linhas": linhas, "col_total": "comissao_centavos", "total_centavos": total,
        "metricas": [("Total de comissões", _brl(total)), ("Vendedor destaque", destaque),
                     ("Recebido pela equipe", _brl(vendas_totais))],
    }
    avisos = []
    if sem_config:
        plural = "es" if sem_config != 1 else ""
        avisos.append(
            f"{sem_config} vendedor{plural} sem % de comissão configurada (mostrando R$ 0,00 pra eles) — "
            "configure em Equipe, no botão “% comis.” de cada um.")
    if sem_vendedor:
        # não é frescura: é venda entrando sem dono, e comissão que ninguém recebe
        avisos.append(
            "Há recebimento sem vendedor atribuído. Isso acontece quando a venda "
            "entrou por fora do PDV e do funil — o valor conta no caixa, mas não "
            "gera comissão pra ninguém.")
    if avisos:
        dados["aviso_config"] = " ".join(avisos)
    return dados


# ---- Orçamentos e Contratos: uma aba por tabela, tudo visível, filtro corta ----
#
# Status em grupo ("fechados"/"abertos"/"assinados") ou específico — a mesma
# lista dá as opções do <select> e a tradução pra `status = any(%s)` na query.
# "" (todos) não entra no dict de propósito: ausência = sem filtro de status.
ORC_STATUS_TAG = {
    "rascunho": ("Rascunho", "aviso"), "enviado": ("Enviado", "aviso"),
    "negociando": ("Negociando", "aviso"), "aprovada": ("Aprovada", "ok"),
    "fechado": ("Fechado", "neutro"), "perdido": ("Perdido", "erro"),
}
ORC_STATUS_OPCOES = [
    ("", "Status: todos"), ("fechados", "— Fechados —"), ("abertos", "— Em aberto —"),
    ("rascunho", "Rascunho"), ("enviado", "Enviado"), ("negociando", "Negociando"),
    ("aprovada", "Aprovada"), ("perdido", "Perdido"),
]
ORC_STATUS_FILTROS = {
    "fechados": ["fechado"],
    # "perdido" fica de fora de propósito: é o próprio bucket "Perdidos" das
    # métricas, separado de "Em aberto" — juntar os dois faria o filtro mentir
    # sobre o que a métrica já mostra ao lado.
    "abertos": ["rascunho", "enviado", "negociando", "aprovada"],
    "rascunho": ["rascunho"], "enviado": ["enviado"], "negociando": ["negociando"],
    "aprovada": ["aprovada"], "perdido": ["perdido"],
}

CT_STATUS_TAG = {
    "rascunho": ("Rascunho", "aviso"), "enviado": ("Enviado", "aviso"),
    "assinado": ("Assinado", "ok"), "cumprido": ("Cumprido", "info"),
    "rescindido": ("Rescindido", "erro"),
}
CT_STATUS_OPCOES = [
    ("", "Status: todos"), ("assinados", "— Assinados —"),
    ("rascunho", "Rascunho"), ("enviado", "Enviado"), ("assinado", "Assinado"),
    ("cumprido", "Cumprido"), ("rescindido", "Rescindido"),
]
CT_STATUS_FILTROS = {
    "assinados": ["assinado", "cumprido"],
    "rascunho": ["rascunho"], "enviado": ["enviado"], "assinado": ["assinado"],
    "cumprido": ["cumprido"], "rescindido": ["rescindido"],
}


def _contrato_tag(numero, status, enviado_em, assinado_em) -> tuple[str, str]:
    """O selo do CONTRATO pra quem está olhando o ORÇAMENTO — os mesmos quatro
    estados que o funil usa (finance.vendas.linha_do_funil), num formato que cabe
    numa coluna de tabela em vez de selo+botão.

    `status='enviado'` nasce assim na CRIAÇÃO do contrato (ver
    finance/contrato.criar_para_orcamento) — não significa "mandado pro
    cliente". Quem decide "mandado" é `enviado_em`, pela mesma razão que já
    corrigiu o funil (PR #590): sem isso, todo contrato recém-criado apareceria
    como "esperando assinatura" antes de qualquer clique em "mandar"."""
    if not numero:
        return "—", "neutro"
    if status == "rescindido":
        return "Rescindido", "erro"
    if assinado_em or status in ("assinado", "cumprido"):
        return "Assinado", "ok"
    if enviado_em:
        return "Aguardando assinatura", "aviso"
    return "Pronto p/ enviar", "info"

_VALOR_ORC = "coalesce(o.primeiro_ano_centavos, o.setup_centavos, 0)"


def _vendedores_da_conta(pool, conta_id: int) -> list[tuple[int, str]]:
    with pool.connection() as c:
        rows = c.execute("select id, nome from membros where conta_id=%s order by nome",
                          (conta_id,)).fetchall()
    return [(r[0], r[1]) for r in rows]


def _dados_orcamentos(pool, conta_id, periodo, status_sel, vendedor_sel, busca) -> dict:
    """TODOS os orçamentos, sem fatiar por aba — Status corta em grupo (fechados/
    em aberto) ou específico, Vendedor e busca por cliente cortam junto. As
    métricas do topo ignoram o filtro de Status de propósito (mostram a
    distribuição inteira do período); o total da tabela é só do que está na tela.

    "Aprovada em" é `aprovada_em`: o instante em que o CLIENTE assinou a
    proposta pública (web/proposta.py). Não tem coluna "Fechado em" — o
    status "Fechado" já aparece na etiqueta de Status, e a data
    (`atualizado_em`) some quase toda vazia num relatório onde a maioria dos
    orçamentos ainda está em aberto (relato do dono: "não tem sentido").

    Cliente é UM nome só, não dois campos: o formulário troca o rótulo de
    `empresa` pra "Nome completo" quando o cliente é pessoa física, mas
    continua gravando na mesma coluna — então pra pessoa física o nome de
    verdade mora em `empresa`, e `cliente` (pensado como "Contato/responsável"
    de uma empresa) fica vazio a maior parte do tempo. Mesma regra que
    `_espelhar_cliente` (web/painel_servicos.py) já usa: `empresa or
    cliente`."""
    ini, fim = _intervalo(periodo)
    where = ["o.conta_id=%s"]
    params: list = [conta_id]
    if periodo != "todos":
        where.append("o.criado_em::date >= %s and o.criado_em::date <= %s")
        params += [ini, fim]
    if vendedor_sel:
        where.append("o.criado_por = %s")
        params.append(str(vendedor_sel))
    if busca:
        where.append("(o.empresa ilike %s or o.cliente ilike %s)")
        params += [f"%{busca}%", f"%{busca}%"]
    base_sql = " and ".join(where)

    with pool.connection() as c:
        por_status = c.execute(
            f"select o.status, count(*), sum({_VALOR_ORC}) from orcamentos o "
            f"where {base_sql} group by o.status", params).fetchall()

    def _grupo(quais):
        n = sum(int(r[1]) for r in por_status if r[0] in quais)
        v = sum(int(r[2] or 0) for r in por_status if r[0] in quais)
        return n, v

    n_fechado, v_fechado = _grupo({"fechado"})
    n_perdido, v_perdido = _grupo({"perdido"})
    n_aberto, v_aberto = _grupo({"rascunho", "enviado", "negociando", "aprovada"})

    where2, params2 = list(where), list(params)
    quais = ORC_STATUS_FILTROS.get(status_sel)
    if quais:
        where2.append("o.status = any(%s)")
        params2.append(quais)
    where2_sql = " and ".join(where2)

    with pool.connection() as c:
        rows = c.execute(
            f"""select o.numero, o.cliente, o.empresa, o.status, o.criado_em,
                       o.aprovada_em,
                       -- criado_por guarda o id do membro OU a palavra 'dono' (quem
                       -- abriu a conta, sem vendedor específico — mesma leitura de
                       -- web/proposta.py). Sem o 2º ramo, esses ficavam "—", como se
                       -- não tivessem dono nenhum.
                       coalesce(m.nome, case when o.criado_por = 'dono' then ct.nome end, '—'),
                       {_VALOR_ORC}, o.token,
                       -- o contrato deste orçamento, se existir — mesma trava de
                       -- `finance.contrato.por_orcamento` (o vivo, não substituído).
                       -- APELIDO em cada subconsulta pra não repetir "criado_em"/
                       -- "status" da tabela de fora e derrubar o ORDER BY.
                       (select x.numero from contratos x
                         where x.orcamento_id = o.id and x.substitui_id is null
                         order by x.id desc limit 1) as ct_numero,
                       (select x.status from contratos x
                         where x.orcamento_id = o.id and x.substitui_id is null
                         order by x.id desc limit 1) as ct_status,
                       (select x.enviado_em from contratos x
                         where x.orcamento_id = o.id and x.substitui_id is null
                         order by x.id desc limit 1) as ct_enviado_em,
                       (select x.assinado_em from contratos x
                         where x.orcamento_id = o.id and x.substitui_id is null
                         order by x.id desc limit 1) as ct_assinado_em
                  from orcamentos o
                  left join membros m on m.id::text = o.criado_por and m.conta_id = o.conta_id
                  left join contas ct on ct.id = o.conta_id
                 where {where2_sql}
                 order by o.criado_em desc limit 300""",
            params2).fetchall()

    linhas = []
    for r in rows:
        rotulo, cor = ORC_STATUS_TAG.get(r[3], (r[3] or "—", "neutro"))
        contrato_rot, contrato_cor = _contrato_tag(r[9], r[10], r[11], r[12])
        linhas.append({
            "numero": r[0], "cliente": r[2] or r[1] or "—",
            "status": rotulo, "status_cor": cor,
            "criado_em": _fmt(r[4]), "aprovada_em": _fmt(r[5]),
            "vendedor": r[6], "valor_centavos": int(r[7] or 0),
            "contrato": contrato_rot, "contrato_cor": contrato_cor,
            "acao_href": f"/proposta/{r[8]}" if r[8] else None,
        })
    return {
        "label": "Orçamentos", "mock": False, "acao": True,
        "acao_rotulo": "Ver / imprimir proposta",
        "colunas": [_col("numero", "Nº"), _col("cliente", "Cliente", flex=True),
                    _col("status", "Status", tag=True), _col("criado_em", "Criado em"),
                    _col("aprovada_em", "Aprovada em"),
                    _col("vendedor", "Vendedor"),
                    _col("valor_centavos", "Valor", num=True, brl=True),
                    _col("contrato", "Contrato", tag=True)],
        "linhas": linhas, "col_total": "valor_centavos", "total_centavos": _soma(linhas, "valor_centavos"),
        "metricas": [("Total geral", _brl(v_fechado + v_aberto + v_perdido)),
                     ("Fechados", f"{n_fechado} · {_brl(v_fechado)}"),
                     ("Em aberto", f"{n_aberto} · {_brl(v_aberto)}"),
                     ("Perdidos", f"{n_perdido} · {_brl(v_perdido)}")],
        "filtro_extra": {
            "status_opcoes": ORC_STATUS_OPCOES, "status_sel": status_sel,
            "vendedores": _vendedores_da_conta(pool, conta_id),
            "vendedor_sel": str(vendedor_sel or ""), "busca_sel": busca or "",
        },
    }


def _aditivos_vigentes(pool, conta_id, contrato_ids) -> dict:
    """{contrato_id: {"ordem": n, "valor": centavos}} do ÚLTIMO aditivo assinado.

    Numa consulta só pra lista inteira — por linha seriam 300 consultas numa tela
    que já é a mais pesada do painel. Tolerante: base sem a 196 devolve vazio e o
    relatório abre com os valores de sempre."""
    ids = [i for i in (contrato_ids or []) if i]
    if not ids:
        return {}
    try:
        with pool.connection() as c:
            rows = c.execute(
                """select contrato_id, ordem, valor_novo_centavos from (
                     select contrato_id, ordem, valor_novo_centavos,
                            row_number() over (partition by contrato_id
                                               order by ordem desc) rn
                       from contrato_aditivos
                      where conta_id=%s and status='assinado'
                        and contrato_id = any(%s)) t
                    where rn = 1""", (conta_id, ids)).fetchall()
    except Exception as e:  # noqa: BLE001
        _log.warning("relatório de contratos: sem aditivos (%s: %s)",
                     type(e).__name__, e)
        return {}
    return {r[0]: {"ordem": r[1], "valor": r[2]} for r in rows}


def _dados_contratos(pool, conta_id, periodo, status_sel, vendedor_sel, busca) -> dict:
    """TODOS os contratos vivos (sem os substituídos por aditivo — mesma trava de
    `finance/contrato.por_orcamento`). Vendedor vem do orçamento de origem: o
    contrato quase nunca grava `criado_por` (ver finance/contrato.py). Cliente
    também vem do orçamento de origem, com a mesma regra `empresa or cliente`
    de `_dados_orcamentos` — mesmo formulário, mesma confusão de campo."""
    ini, fim = _intervalo(periodo)
    where = ["c.conta_id=%s", "c.substitui_id is null"]
    params: list = [conta_id]
    if periodo != "todos":
        where.append("c.criado_em::date >= %s and c.criado_em::date <= %s")
        params += [ini, fim]
    if vendedor_sel:
        where.append("o.criado_por = %s")
        params.append(str(vendedor_sel))
    if busca:
        where.append("(o.empresa ilike %s or o.cliente ilike %s)")
        params += [f"%{busca}%", f"%{busca}%"]
    base_sql = " and ".join(where)
    join_sql = "left join orcamentos o on o.id = c.orcamento_id"

    with pool.connection() as c:
        por_status = c.execute(
            f"select c.status, count(*), sum(coalesce(c.valor_centavos,0)) "
            f"from contratos c {join_sql} where {base_sql} group by c.status",
            params).fetchall()
        # `status in (rascunho, enviado)` sozinho não diz se o contrato JÁ SAIU de
        # casa — 'enviado' nasce na criação (finance.contrato.criar_para_orcamento),
        # não no clique de "mandar" (PR #590). Quem sabe é `enviado_em`.
        por_envio = c.execute(
            f"select (c.enviado_em is not null), count(*), sum(coalesce(c.valor_centavos,0)) "
            f"from contratos c {join_sql} where {base_sql} "
            f"and c.status in ('rascunho','enviado') group by (c.enviado_em is not null)",
            params).fetchall()

    def _grupo(quais):
        n = sum(int(r[1]) for r in por_status if r[0] in quais)
        v = sum(int(r[2] or 0) for r in por_status if r[0] in quais)
        return n, v

    def _grupo_envio(enviado: bool):
        n = sum(int(r[1]) for r in por_envio if bool(r[0]) == enviado)
        v = sum(int(r[2] or 0) for r in por_envio if bool(r[0]) == enviado)
        return n, v

    n_assinado, v_assinado = _grupo({"assinado", "cumprido"})
    n_aguardando, v_aguardando = _grupo_envio(True)
    n_pronto, v_pronto = _grupo_envio(False)
    n_rescindido, v_rescindido = _grupo({"rescindido"})

    where2, params2 = list(where), list(params)
    quais = CT_STATUS_FILTROS.get(status_sel)
    if quais:
        where2.append("c.status = any(%s)")
        params2.append(quais)
    where2_sql = " and ".join(where2)

    with pool.connection() as c:
        rows = c.execute(
            f"""select c.numero, coalesce(o.empresa, o.cliente, '—'), c.status, c.criado_em,
                       c.assinado_em,
                       -- mesma leitura de _dados_orcamentos: criado_por é o id do
                       -- membro OU a palavra 'dono'.
                       coalesce(m.nome, case when o.criado_por = 'dono' then ct.nome end, '—'),
                       coalesce(c.valor_centavos, 0), c.token,
                       c.enviado_em, o.numero, c.id
                  from contratos c {join_sql}
                  left join membros m on m.id::text = o.criado_por and m.conta_id = c.conta_id
                  left join contas ct on ct.id = c.conta_id
                 where {where2_sql}
                 order by c.criado_em desc limit 300""",
            params2).fetchall()

    # O VALOR QUE VALE HOJE, não o congelado na assinatura. Contrato com aditivo
    # assinado aparecendo aqui com o valor velho é o mesmo defeito da folha do
    # cliente sem tarja — só que na tela do dono, que é quem decide com este
    # número. Numa consulta só, e tolerante: base sem a 196 mostra o de antes.
    vigentes = _aditivos_vigentes(pool, conta_id, [r[10] for r in rows])

    linhas = []
    for r in rows:
        rotulo, cor = CT_STATUS_TAG.get(r[2], (r[2] or "—", "neutro"))
        vig = vigentes.get(r[10])
        linhas.append({
            "numero": r[0], "cliente": r[1], "status": rotulo, "status_cor": cor,
            "criado_em": _fmt(r[3]), "assinado_em": _fmt(r[4]),
            "vendedor": r[5],
            "valor_centavos": int((vig or {}).get("valor") or r[6] or 0),
            "aditivo": f"{vig['ordem']}º" if vig else "—",
            "enviado_em": _fmt(r[8]),
            "orcamento": f"nº {r[9]}" if r[9] else "—",
            "acao_href": f"/contrato/{r[7]}" if r[7] else None,
        })
    return {
        "label": "Contratos", "mock": False, "acao": True,
        "acao_rotulo": "Ver / imprimir contrato",
        "colunas": [_col("numero", "Nº"), _col("orcamento", "Orçamento"),
                    _col("cliente", "Cliente", flex=True),
                    _col("status", "Status", tag=True), _col("criado_em", "Criado em"),
                    _col("enviado_em", "Enviado em"),
                    _col("assinado_em", "Assinado em"), _col("vendedor", "Vendedor"),
                    _col("aditivo", "Aditivo"),
                    _col("valor_centavos", "Valor", num=True, brl=True)],
        "linhas": linhas, "col_total": "valor_centavos", "total_centavos": _soma(linhas, "valor_centavos"),
        "metricas": [("Total geral", _brl(v_assinado + v_aguardando + v_pronto + v_rescindido)),
                     ("Assinados", f"{n_assinado} · {_brl(v_assinado)}"),
                     ("Aguardando assinatura", f"{n_aguardando} · {_brl(v_aguardando)}"),
                     ("Prontos, não enviados", f"{n_pronto} · {_brl(v_pronto)}"),
                     ("Rescindidos", f"{n_rescindido} · {_brl(v_rescindido)}")],
        "filtro_extra": {
            "status_opcoes": CT_STATUS_OPCOES, "status_sel": status_sel,
            "vendedores": _vendedores_da_conta(pool, conta_id),
            "vendedor_sel": str(vendedor_sel or ""), "busca_sel": busca or "",
        },
    }


AGENDA_STATUS_TAG = {
    "ativo": ("Confirmado", "ok"), "pre_reservado": ("Pré-reserva", "aviso"),
    "cancelado": ("Cancelado", "erro"),
}
AGENDA_STATUS_OPCOES = [
    ("", "Status: todos"), ("ativo", "Confirmado"),
    ("pre_reservado", "Pré-reserva"), ("cancelado", "Cancelado"),
]
AGENDA_DESFECHO_TAG = {
    "realizado": ("Realizado", "ok"), "nao_realizado": ("Não realizado", "erro"),
}
AGENDA_TIPO_ROTULO = {"pessoal": "Pessoal", "empresa": "Empresa", "fornecedor": "Fornecedor"}

# A régua da VISITA, escrita UMA vez. O Funil (`_SQL_VISITAS`) e o filtro de
# espécie desta aba leem daqui: duas cópias da mesma pergunta acertam no primeiro
# dia e divergem no terceiro.
#
# Título começando com "Visita" é como o Cockpit batiza ("Visita — {quem}") e
# como o time batiza na mão ("VISITA TÉCNICA - PEDRO"). `tipo_evento` vazio
# desempata: quando esse campo vem preenchido (Casamento, Locação...) o
# compromisso é a FESTA do cliente, não a visita dele ao espaço.
_E_VISITA = "(e.titulo ilike 'visita%%' and e.tipo_evento is null)"

#: EVENTO é o COMPLEMENTO da visita, e não `tipo_evento is not null`. A régua
#: óbvia apagaria festa: medido na Prime em 31/08/2026, 12 das 43 festas estavam
#: com `tipo_evento` vazio porque foram digitadas direto no título ("aniversario
#: Leda L.", "Formatura - Beatriz", "15 Anos — Fernanda"). Com a régua óbvia essas
#: 12 sumiriam dos DOIS filtros — nem visita, nem evento. Sendo o complemento,
#: visitas + eventos é sempre igual ao total, em qualquer base.
AGENDA_ESPECIES = [("", "Todos"), ("visita", "Visitas"), ("evento", "Eventos")]


def _dados_agenda(pool, conta_id, periodo, status_sel, vendedor_sel, busca,
                  especie="", de=None, ate=None) -> dict:
    """A Agenda (web/painel_agenda.py) só mostra o que vem — mês corrente e os
    próximos compromissos. Este relatório fecha o período: quantos eventos,
    quantos viraram presença, quantos não aconteceram e quantos foram
    cancelados. `status` (ativo/pré-reserva/cancelado) e `desfecho`
    (realizado/não realizado) já existem desde as migrações 098-179; nenhuma
    coluna nova.

    ESPÉCIE (31/08/2026). Visita e festa viviam na mesma lista sem como separar —
    na Prime são 17 visitas e 43 festas, perguntas de negócio diferentes. O filtro
    usa `_E_VISITA`, a mesma régua do Funil, e define EVENTO como o complemento
    (o porquê está no comentário de `AGENDA_ESPECIES`).

    A espécie muda as COLUNAS e as MÉTRICAS, não só as linhas, e é de propósito:
    visita nunca tem sinal (não segura data) nem convidados, e tem vendedor e
    comparecimento; festa tem convidados, sinal e tipo. Mostrar as oito colunas
    fixas obrigava a ler R$ 0,00 e "—" em metade da tela. Já `status` e
    `vendedor` continuam sendo só recorte: as métricas os ignoram, como sempre
    ignoraram — recorte não muda o que está sendo contado, espécie muda.

    PERÍODO. Esta é a única aba que pede `ate_o_fim`: "este mês" aqui vai até o
    último dia, não até hoje (decisão do dono em 31/08/2026). Numa agenda, parar
    em hoje escondia justamente a festa que ainda vai acontecer — eram 38 dos 60
    compromissos da Prime.

    CLIENTE tem TRÊS fontes, em ordem de firmeza. `eventos_agenda.cliente_id`
    (migração 192) é o vínculo que alguém escolheu no formulário e manda em
    todos: os outros dois são deduções. `orcamentos.evento_agenda_id` vale quando
    a festa nasceu de proposta aprovada (mesma regra `empresa or cliente` de
    `_dados_orcamentos`), e `prospeccao_id` quando a visita foi marcada pelo
    Cockpit (`agendar_visita` liga os dois na hora).

    Por que a terceira precisou existir: até 31/08/2026 só havia as deduções, e
    51 dos 60 compromissos da Prime apareciam com "—". Não era falha de leitura —
    o formulário de novo compromisso não tinha campo de cliente, e o nome acabava
    dentro do texto do título, onde é texto e não dado. Dos 43 eventos (locação,
    casamento, formatura) NENHUM tinha lead, e é esperado: locação não nasce de
    lead de WhatsApp, nasce de telefonema.

    Reunião interna e compromisso pessoal seguem sem nenhuma das três e continuam
    "—" — é o esperado, não bug.

    "Sinal" é `eventos_agenda.sinal_centavos`, o valor que segura a DATA
    (163_evento_sinal_esperado) — só é gravado no "Só segurar a data" do
    formulário de novo compromisso (web/painel_agenda.py, checkbox `segurar`) ou
    na pré-reserva por orçamento (web/proposta._reservar_na_agenda).
    """
    especie = especie if especie in ("visita", "evento") else ""
    ini, fim = _intervalo(periodo, de, ate, ate_o_fim=True)
    where = ["e.conta_id=%s"]
    params: list = [conta_id]
    if periodo != "todos":
        where.append("e.inicio::date >= %s and e.inicio::date <= %s")
        params += [ini, fim]
    if especie == "visita":
        where.append(_E_VISITA)
    elif especie == "evento":
        where.append("not " + _E_VISITA)
    if vendedor_sel:
        try:
            where.append("e.membro_id = %s")
            params.append(int(vendedor_sel))
        except (TypeError, ValueError):
            where.pop()
    # O nome do cliente tem TRÊS fontes, e a ordem importa. `cliente_id` (migração
    # 192) vem primeiro porque é o vínculo que alguém escolheu na tela — os outros
    # dois são deduções: o orçamento vale quando a festa nasceu de uma proposta
    # aprovada, e o lead quando a visita foi marcada pelo Cockpit. Antes da 192 só
    # existiam esses dois, e por isso 51 dos 60 compromissos da Prime apareciam
    # sem cliente: locação não nasce de proposta nem de lead, nasce de telefonema.
    # `convidados` (a contagem) sai do lateral pelo mesmo motivo do nome: o
    # formulário de "novo compromisso" tem um campo livre pra digitar quanta
    # gente vem, e quase ninguém preenche — mas quando a festa nasce de um
    # orçamento aprovado, a contagem JÁ EXISTE em `orcamentos.evento` (a mesma
    # que o Funil mostra no subtítulo). Sem este fallback a coluna "Convid."
    # aparecia "—" pra festa que o próprio cliente já tinha informado.
    join_orc = """left join clientes cl
                    on cl.id = e.cliente_id and cl.dono_id = e.conta_id
                  left join pessoas pe on pe.id = cl.pessoa_id
                  left join lateral (
                    select coalesce(o.empresa, o.cliente) as nome,
                           o.evento->>'convidados' as convidados
                      from orcamentos o
                     where o.evento_agenda_id = e.id
                     order by o.id desc limit 1
                  ) oc on true
                  left join prospeccao p on p.id = e.prospeccao_id
                  left join membros mb on mb.id = e.membro_id"""
    if busca:
        where.append("coalesce(pe.nome, cl.nome, oc.nome, p.contato, p.empresa) ilike %s")
        params.append(f"%{busca}%")
    base_sql = " and ".join(where)

    with pool.connection() as c:
        agg = c.execute(
            f"""select count(*),
                       count(*) filter (where e.desfecho='realizado'),
                       count(*) filter (where e.desfecho='nao_realizado'),
                       count(*) filter (where e.status='cancelado'),
                       coalesce(sum(e.sinal_centavos), 0),
                       count(*) filter (where e.status='ativo'),
                       count(*) filter (where e.status='pre_reservado'),
                       count(*) filter (where e.inicio < now() and e.desfecho is null),
                       count(*) filter (where e.prospeccao_id is not null),
                       coalesce(sum(coalesce(e.convidados, nullif(oc.convidados,'')::int)), 0),
                       count(*) filter (where e.tipo_evento is null)
                  from eventos_agenda e
                  {join_orc}
                 where {base_sql}""", params).fetchone()
    (n_total, n_realizado, n_nao_realizado, n_cancelado, sinal_total,
     n_ativo, n_pre, n_sem_resposta, n_com_lead, n_convidados,
     n_sem_tipo) = (int(x or 0) for x in agg)

    def _pct(n):
        return f"{n} · {round(n * 100 / n_total)}%" if n_total else f"{n} · 0%"

    where2, params2 = list(where), list(params)
    if status_sel in AGENDA_STATUS_TAG:
        where2.append("e.status = %s")
        params2.append(status_sel)
    where2_sql = " and ".join(where2)

    with pool.connection() as c:
        rows = c.execute(
            f"""select e.inicio, coalesce(e.tipo_evento, e.titulo),
                       coalesce(pe.nome, cl.nome, oc.nome, p.contato, p.empresa),
                       e.tipo, e.status, e.desfecho,
                       coalesce(e.convidados, nullif(oc.convidados,'')::int), e.sinal_centavos,
                       mb.nome, e.tipo_evento, e.id,
                       coalesce(e.sem_cliente, false), e.titulo,
                       coalesce(pe.nome, cl.nome, oc.nome) is not null as nome_firme,
                       coalesce(p.contato, p.empresa) as nome_lead
                  from eventos_agenda e
                  {join_orc}
                 where {where2_sql}
                 order by e.inicio asc limit 300""",
            params2).fetchall()

    # nomes da equipe: sem eles a leitura do título devolveria o VENDEDOR na
    # coluna Cliente ("VISITA TÉCNICA - PEDRO"). Uma consulta por relatório.
    equipe = [n for _i, n in _vendedores_da_conta(pool, conta_id) if n]

    linhas = []
    for r in rows:
        st_rotulo, st_cor = AGENDA_STATUS_TAG.get(r[4], (r[4] or "—", "neutro"))
        df_rotulo, df_cor = AGENDA_DESFECHO_TAG.get(r[5], ("—", "neutro"))
        # "sem resposta" só existe pra visita que JÁ passou: antes da data não há
        # o que responder, e pintar de âmbar o que ainda vai acontecer viraria um
        # alerta que não pede nada de ninguém.
        if especie == "visita" and r[5] is None and r[0] and r[0] < _ag.agora_brt():
            df_rotulo, df_cor = "Sem resposta", "aviso"
        # CLIENTE: o vínculo primeiro; sem ele, uma DEDUÇÃO — marcada como
        # palpite, porque é o que ela é. Nunca se grava daqui: dedução vira
        # `cliente_id` só quando o dono confirma na tela de ligar.
        #  r[8]=vendedor r[9]=tipo_evento r[10]=id r[11]=sem_cliente r[12]=titulo
        #  r[13]=nome_firme (veio de vínculo ou de orçamento)  r[14]=nome_lead
        #
        # O nome vindo do LEAD é dedução como o do título, e até 31/08/2026 não
        # era tratado como uma: ele preenchia a célula, e por isso a linha
        # PARECIA resolvida — sem selo e sem link, quando não há ficha nenhuma
        # atrás dela. Era o pior dos dois mundos, e é o que muda aqui.
        #
        # (Nome vindo de ORÇAMENTO segue sem selo e sem link. Também é dedução,
        # mas de outra natureza: ali existe proposta aprovada com cliente na
        # ficha, e o conserto certo é ligar o vínculo na hora de reservar a data,
        # não cobrar do dono na tela do relatório.)
        nome, do_titulo = r[2], False
        do_lead = bool(r[14]) and not r[13]
        if not nome:
            nome = _ag.nome_no_titulo(r[12], r[9], equipe)
            do_titulo = bool(nome)
        deduzido = do_titulo or do_lead
        linhas.append({
            "inicio": _fmt_hora(r[0]),
            "evento": r[1] or "—", "cliente": nome or "—",
            "cliente_do_titulo": do_titulo, "cliente_do_lead": do_lead,
            "cliente_deduzido": deduzido,
            # a célula só vira link enquanto houver pergunta a fazer: com vínculo
            # de verdade, ou com "não tem cliente" já dito, ela para de cobrar.
            "cliente_link": (None if (r[11] or (r[2] and not do_lead))
                             else f"/painel/relatorios/agenda/{r[10]}/cliente"),
            "tipo": AGENDA_TIPO_ROTULO.get(r[3], r[3] or "—"),
            "tipo_evento": r[9] or "sem tipo",
            "tipo_evento_cor": "neutro" if r[9] else "aviso",
            "status": st_rotulo, "status_cor": st_cor,
            "desfecho": df_rotulo, "desfecho_cor": df_cor,
            "vendedor": r[8] or "—",
            "convidados": r[6] if r[6] is not None else "—",
            "sinal_centavos": int(r[7] or 0),
        })

    if especie == "visita":
        # Sem Sinal (visita não segura data — era sempre R$ 0,00) e sem
        # Convidados; entram Vendedor e o comparecimento, que é o que se pergunta
        # de uma visita.
        colunas = [_col("inicio", "Data"), _col("evento", "Visita", flex=True),
                   _col("cliente", "Cliente", cli=True), _col("vendedor", "Vendedor"),
                   _col("status", "Status", tag=True),
                   _col("desfecho", "Desfecho", tag=True)]
        col_total, total_centavos = None, 0
        metricas = [("Visitas no período", str(n_total)),
                    ("Aconteceram", _pct(n_realizado)),
                    ("Não apareceram", _pct(n_nao_realizado)),
                    ("Sem resposta", _pct(n_sem_resposta)),
                    ("Vindas de lead", _pct(n_com_lead))]
    elif especie == "evento":
        # Vendedor e Desfecho (04/09/2026): nenhum dos dois é exclusivo de
        # visita — `membro_id` existe pra qualquer compromisso (uma festa também
        # foi criada por alguém da equipe), e "✅ Aconteceu / ❌ Não rolou" marca
        # qualquer evento passado, não só visita. A aba Eventos escondia os dois
        # sem motivo — mesma inconsistência que "Todos" tinha com Vendedor.
        colunas = [_col("inicio", "Data"), _col("evento", "Evento", flex=True),
                   _col("cliente", "Cliente", cli=True), _col("vendedor", "Vendedor"),
                   _col("tipo_evento", "Tipo", tag=True),
                   _col("status", "Status", tag=True),
                   _col("desfecho", "Desfecho", tag=True),
                   _col("convidados", "Convid.", num=True),
                   _col("sinal_centavos", "Sinal", num=True, brl=True)]
        col_total, total_centavos = "sinal_centavos", _soma(linhas, "sinal_centavos")
        metricas = [("Eventos no período", str(n_total)),
                    ("Confirmados", _pct(n_ativo)),
                    ("Pré-reserva", _pct(n_pre)),
                    ("Cancelados", _pct(n_cancelado)),
                    ("Convidados", str(n_convidados)),
                    ("Sinal no período", _brl(sinal_total))]
    else:
        # "Todos" continua quase EXATAMENTE como era antes da espécie existir —
        # quem abre a aba sem escolher nada tem que ver a tela de sempre. A
        # exceção é Vendedor (31/08/2026): "Todos" é a única aba que mistura
        # visita com festa, e sem esta coluna uma visita marcada por alguém da
        # equipe não tinha como mostrar quem foi — o dado (`membro_id`) sempre
        # existiu, só a aba Visita mostrava.
        colunas = [_col("inicio", "Data"), _col("evento", "Evento", flex=True),
                   _col("cliente", "Cliente", cli=True), _col("vendedor", "Vendedor"),
                   _col("tipo", "Tipo"),
                   _col("status", "Status", tag=True),
                   _col("desfecho", "Desfecho", tag=True),
                   _col("convidados", "Convid.", num=True),
                   _col("sinal_centavos", "Sinal", num=True, brl=True)]
        col_total, total_centavos = "sinal_centavos", _soma(linhas, "sinal_centavos")
        metricas = [("Eventos no período", str(n_total)),
                    ("Realizados", _pct(n_realizado)),
                    ("Não realizados", _pct(n_nao_realizado)),
                    ("Cancelados", _pct(n_cancelado)),
                    ("Sinal no período", _brl(sinal_total))]

    rotulos = {"visita": "Visitas", "evento": "Eventos"}
    return {
        "label": "Agenda" + (f" · {rotulos[especie]}" if especie else ""),
        "mock": False,
        # A elástica é o EVENTO, não o cliente: o título é digitado à mão e não
        # tem teto ("Reunião de alinhamento sobre o contrato da Prefeitura"),
        # enquanto nome de cliente tem tamanho previsível.
        "colunas": colunas, "linhas": linhas,
        "col_total": col_total, "total_centavos": total_centavos,
        "metricas": metricas,
        "filtro_extra": {
            "status_opcoes": AGENDA_STATUS_OPCOES, "status_sel": status_sel,
            "vendedores": _vendedores_da_conta(pool, conta_id),
            "vendedor_sel": str(vendedor_sel or ""), "busca_sel": busca or "",
            "especies": AGENDA_ESPECIES, "especie_sel": especie,
            # quantas festas estão sem o tipo preenchido — não bloqueia nada, só
            # avisa que essas não entram em "quantas locações tenho em dezembro".
            "sem_tipo": n_sem_tipo if especie == "evento" else 0,
        },
    }


# ------------------------------------------------------------- LEADS DO CHIP
#
# O lead que chega pelo QR não tem número pra somar — tem TEMPO DE ESPERA. Este
# relatório existe porque o painel sabia quantas mensagens chegaram e não sabia
# quanto tempo alguém ficou sem resposta: em 26/08, na conta 34, sete pessoas
# tinham chamado o chip principal e ninguém tinha respondido. Nenhuma tela dizia.
#
# A redação (o texto da espera, o rótulo do chip, a mediana) mora em
# `finance/vendas.py`, testável sem banco. Aqui fica só a consulta.

#: os valores do filtro de chip que NÃO são um id. "" é todos; "principal" é o
#: chip da própria conta, que na conversa aparece como `chip_id` NULO.
CHIP_TODOS, CHIP_PRINCIPAL = "", "principal"

#: tom da função pura → classe do selo no template (.rel-tag.ok/.aviso/.erro/.neutro)
_TOM_TAG = {"ok": "ok", "ambar": "aviso", "coral": "erro", "neutro": "neutro"}


def _chips_da_conta(pool, conta_id: int) -> tuple[str, dict[int, str]]:
    """(nome do chip principal, {id_da_conta_chip: nome}) — pra dar nome ao número.

    Duas fontes porque são duas coisas diferentes: o chip PRINCIPAL é um canal
    (`canais_config`, identificador `qr:<conta>`), e o chip SECUNDÁRIO é uma conta
    inteira, ligada por `contas.chip_de`. Uma consulta só não daria conta, e
    inventar um mapa novo daria uma terceira versão da verdade pra divergir.
    """
    with pool.connection() as c:
        prin = c.execute(
            """select rotulo from canais_config
                where conta_id=%s and canal='whatsapp' and identificador=%s
                order by id desc limit 1""",
            (conta_id, f"qr:{conta_id}")).fetchone()
        secs = c.execute("select id, nome from contas where chip_de=%s order by id",
                         (conta_id,)).fetchall()
    return (prin[0] if prin and prin[0] else ""), {r[0]: r[1] or "" for r in secs}


def _dados_leads_chip(pool, conta_id, periodo, chip_sel, vendedor_sel, busca) -> dict:
    """Os leads que entraram por um chip de WhatsApp, e quanto cada um esperou.

    UMA LINHA POR LEAD, não por conversa: o mesmo lead pode ter mais de uma
    conversa, e contá-las duas vezes inflaria "leads recebidos" e a mediana.

    Três decisões que a consulta carrega, cada uma medida em produção:

    1. **`chip_id` NULO é o chip principal**, não é dado faltando (ver
       `vendas.rotulo_do_chip`). Filtrar por `chip_id = %s` esconderia 174 dos
       186 leads da conta 34.
    2. **Resposta é `out` de HUMANO.** Havia 18 mensagens de bot na conta 34;
       contá-las zeraria a espera de quem, na prática, continuou esperando gente.
    3. **"Última msg" vem da conversa, não de `prospeccao.ultimo_contato_em`.**
       Esse campo está vazio em 158 dos 174 leads do chip principal — que têm
       2.772 mensagens trocadas. Lido dali, o relatório anunciaria que quase
       ninguém foi atendido.
    """
    ini, fim = _intervalo(periodo)
    where = ["cv.conta_id=%s", "cv.prospeccao_id is not null"]
    params: list = [conta_id]
    if periodo != "todos":
        where.append("p.criado_em::date >= %s and p.criado_em::date <= %s")
        params += [ini, fim]
    if chip_sel == CHIP_PRINCIPAL:
        where.append("cv.chip_id is null")
    elif chip_sel:
        where.append("cv.chip_id = %s")
        params.append(int(chip_sel))
    if vendedor_sel:
        where.append("cv.responsavel_membro_id = %s")
        params.append(int(vendedor_sel))
    if busca:
        where.append("p.empresa ilike %s")
        params.append(f"%{busca}%")

    sql = f"""
    with conv as (
      select cv.id, cv.prospeccao_id, cv.chip_id, cv.criado_em, cv.ultima_msg_em,
             cv.responsavel_membro_id,
             (select min(m.criado_em) from mensagens m
               where m.conversa_id=cv.id and m.direcao='in') as prim_in,
             -- só HUMANO: o bot respondendo não é a empresa respondendo
             (select min(m.criado_em) from mensagens m
               where m.conversa_id=cv.id and m.direcao='out'
                 and m.autor='humano') as prim_resp,
             (select count(*) from mensagens m where m.conversa_id=cv.id) as msgs
        from conversas cv join prospeccao p on p.id = cv.prospeccao_id
       where {" and ".join(where)}
    ), por_lead as (
      select prospeccao_id as lead_id,
             -- o chip da PRIMEIRA conversa: é por ele que o lead entrou.
             -- `array_agg` e não `min` porque o nulo aqui tem significado (chip
             -- principal) e `min` o descartaria em favor de um id qualquer.
             (array_agg(chip_id order by criado_em))[1] as chip_id,
             min(prim_in) as prim_in, min(prim_resp) as prim_resp,
             sum(msgs) as msgs, max(ultima_msg_em) as ultima_msg,
             (array_agg(responsavel_membro_id
                        order by ultima_msg_em desc nulls last))[1] as memb
        from conv group by prospeccao_id
    )
    select p.id, p.empresa, l.chip_id, l.prim_in, l.prim_resp, l.msgs,
           l.ultima_msg, coalesce(mb.nome, '—'), o.numero
      from por_lead l
      join prospeccao p on p.id = l.lead_id
      left join membros mb on mb.id = l.memb
      left join orcamentos o on o.id = p.orcamento_id
     order by l.prim_in desc nulls last, p.id desc
     limit 300"""
    with pool.connection() as c:
        rows = c.execute(sql, params).fetchall()

    nome_prin, rot_secs = _chips_da_conta(pool, conta_id)
    linhas, esperas = [], []
    n_nunca = n_orc = 0
    for r in rows:
        esp = vendas.espera_do_lead(r[3], r[4])
        if esp["minutos"] is not None:
            esperas.append(esp["minutos"])
        if esp["texto"] == "nunca respondido":
            n_nunca += 1
        if r[8]:
            n_orc += 1
        linhas.append({
            "lead": r[1] or "—",
            "chip": vendas.rotulo_do_chip(r[2], rotulos=rot_secs,
                                          nome_principal=nome_prin),
            "entrou": _fmt_hora(r[3]),
            "esperou": esp["texto"], "esperou_cor": _TOM_TAG[esp["tom"]],
            "msgs": int(r[5] or 0),
            "vendedor": r[7],
            "ultima": _fmt_hora(r[6]),
            "orcamento": f"nº {r[8]}" if r[8] else "—",
        })

    med = vendas.mediana(esperas)
    return {
        "label": "Leads do chip", "mock": False,
        # o NOME DO LEAD é a coluna elástica: é a única de texto livre aqui, e as
        # outras (chip, hora, espera, contagem) têm largura previsível. Sem uma
        # marcada, a tabela volta a rolar pro lado e engole o começo do nome.
        "colunas": [_col("lead", "Lead", flex=True), _col("chip", "Chip"),
                    _col("entrou", "Entrou em"),
                    _col("esperou", "Esperou", tag=True),
                    _col("msgs", "Msgs", num=True),
                    _col("vendedor", "Vendedor"), _col("ultima", "Última msg"),
                    _col("orcamento", "Orçamento")],
        "linhas": linhas,
        # SEM total: `valor_estimado_centavos` é zero nos 675 leads da base, e uma
        # linha "Total R$ 0,00" seria exatamente o ruído que o funil acabou de
        # tirar. O template pula a linha quando `col_total` é nulo.
        "col_total": None, "total_centavos": 0,
        "metricas": [("Leads recebidos", str(len(linhas))),
                     ("Nunca respondidos", str(n_nunca)),
                     ("Espera (mediana)", vendas.duracao_curta(med)),
                     ("Viraram orçamento", str(n_orc))],
        "filtro_extra": {
            "chips": _opcoes_de_chip(nome_prin, rot_secs),
            "chip_sel": str(chip_sel or ""),
            "vendedores": _vendedores_da_conta(pool, conta_id),
            "vendedor_sel": str(vendedor_sel or ""), "busca_sel": busca or "",
        },
    }


def _opcoes_de_chip(nome_prin: str, rot_secs: dict[int, str]) -> list[tuple[str, str]]:
    """As opções do filtro. Só oferece escolha quando há mais de um chip — numa
    conta com chip único o seletor seria uma pergunta de resposta única."""
    if not rot_secs:
        return []
    return ([(CHIP_TODOS, "Chip: todos"),
             (CHIP_PRINCIPAL, nome_prin or "Chip principal")]
            + [(str(i), n or f"Chip {i}") for i, n in sorted(rot_secs.items())])


def _fmt_hora(d) -> str:
    """dd/mm/aaaa HH:MM no fuso de Brasília — a hora importa aqui (a espera é
    medida em minutos), e `_fmt` só mostra a data.

    O ano entrou em 31/08/2026: a Prime tem compromisso até outubro/2027, e
    "16/01 19:00" não diz de qual ano é."""
    return d.astimezone(_ag.BRT).strftime("%d/%m/%Y %H:%M") if d else "—"


# ----------------------------------------------------------- FUNIL COMERCIAL
#
# Os quatro indicadores que o consultor da Prime pediu, cada um com a COBERTURA
# do dado ao lado. A régua está em `finance/vendas.py`; aqui fica só a consulta.
#
# A visita é identificada por três coisas juntas, e nenhuma delas é palpite:
#   * `titulo ilike 'visita%'` — é como o Cockpit batiza (`finance/cockpit.py`,
#     "Visita — {quem}") e como o time batiza na mão ("VISITA TÉCNICA - PEDRO");
#   * `status <> 'cancelado'` — visita cancelada não foi agendada pra valer, e
#     contá-la inflaria o degrau de cima sem inflar nenhum de baixo;
#   * `tipo_evento is null` — quando esse campo vem preenchido (Casamento,
#     Locação...) o compromisso é a FESTA do cliente, não a visita dele ao espaço.

#: o degrau "leads" do funil sai da mesma fonte da aba Leads do chip: quem chegou
#: por uma conversa de WhatsApp. Somar a base garimpada no Maps aqui misturaria
#: duas espécies de lead e faria a taxa de conversão despencar por artifício.
_SQL_VISITAS = """
    select e.id,
           coalesce(p.empresa, replace(e.titulo, 'Visita — ', '')) as lead,
           (e.prospeccao_id is not null) as ligado,
           coalesce(mb.nome, '—') as vendedor,
           e.inicio, (e.inicio < now()) as passou, e.desfecho,
           -- quanto o lead esperou da primeira mensagem dele até a visita ser
           -- marcada. Só existe pra visita ligada a um lead — nas soltas não há
           -- de quem medir.
           case when p.id is null then null else
             (select min(m.criado_em) from mensagens m
                join conversas cv on cv.id = m.conversa_id
               where cv.prospeccao_id = p.id and m.direcao='in') end as lead_chegou,
           e.criado_em
      from eventos_agenda e
      left join prospeccao p on p.id = e.prospeccao_id and p.conta_id = e.conta_id
      left join membros mb on mb.id = e.membro_id
     where e.conta_id = %s
       and """ + _E_VISITA + """
       and coalesce(e.status,'') <> 'cancelado'
"""


def _dados_funil(pool, conta_id, periodo, status_sel, vendedor_sel, busca) -> dict:
    """O funil comercial: lead → visita agendada → aconteceu → respondida → sinal.

    Cada taxa vem com a cobertura porque, sem ela, o relatório mente por omissão:
    em 26/08, na conta 34, "2 de 3 apareceram" pareceria 67% de comparecimento —
    mas 8 visitas já tinham acontecido e 5 estavam sem resposta nenhuma.
    """
    ini, fim = _intervalo(periodo)
    where, params = "", [conta_id]
    if periodo != "todos":
        where += " and e.inicio::date >= %s and e.inicio::date <= %s"
        params += [ini, fim]
    if vendedor_sel:
        where += " and e.membro_id = %s"
        params.append(int(vendedor_sel))
    if busca:
        where += " and (p.empresa ilike %s or e.titulo ilike %s)"
        params += [f"%{busca}%", f"%{busca}%"]
    if status_sel == "sem_resposta":
        where += " and e.inicio < now() and e.desfecho is null"
    elif status_sel == "respondidas":
        where += " and e.desfecho is not null"
    elif status_sel == "sem_lead":
        where += " and e.prospeccao_id is null"

    with pool.connection() as c:
        rows = c.execute(_SQL_VISITAS + where + " order by e.inicio desc limit 300",
                         params).fetchall()
        # os leads que entraram por conversa — o topo do funil. Fora do filtro de
        # vendedor de propósito: o lead chega antes de ter dono, e recortar por
        # vendedor aqui daria uma taxa de conversão sobre um universo que o
        # vendedor nunca teve a chance de atender.
        p2: list = [conta_id]
        sql_leads = ("select count(distinct cv.prospeccao_id) from conversas cv "
                     "where cv.conta_id=%s and cv.prospeccao_id is not null")
        if periodo != "todos":
            sql_leads += " and cv.criado_em::date >= %s and cv.criado_em::date <= %s"
            p2 += [ini, fim]
        n_leads = c.execute(sql_leads, p2).fetchone()[0] or 0
        n_sinal = c.execute(
            "select count(*) from orcamentos where conta_id=%s and sinal_pago_em is not null",
            (conta_id,)).fetchone()[0] or 0
        n_orc = c.execute("select count(*) from orcamentos where conta_id=%s",
                          (conta_id,)).fetchone()[0] or 0

    linhas, esperas = [], []
    n_agendadas = n_ligadas = n_passou = n_respondidas = n_apareceu = 0
    for r in rows:
        n_agendadas += 1
        if r[2]:
            n_ligadas += 1
        d = vendas.desfecho_da_visita(r[6], bool(r[5]))
        if r[5]:
            n_passou += 1
        if d["conta_no_comparecimento"]:
            n_respondidas += 1
            if r[6] == vendas.VISITA_APARECEU:
                n_apareceu += 1
        espera = None
        if r[7] and r[8]:
            espera = max(0, int((r[8] - r[7]).total_seconds() // 60))
            esperas.append(espera)
        linhas.append({
            "lead": r[1] or "—",
            "vendedor": r[3],
            "marcada": _fmt_hora(r[4]),
            "esperou": vendas.duracao_curta(espera) if espera is not None else "sem lead",
            "desfecho": d["texto"], "desfecho_cor": _TOM_TAG[d["tom"]],
        })

    # AS TAXAS. `base` é o que revela o buraco — ver vendas.taxa_com_cobertura.
    t_agendou = vendas.taxa_com_cobertura(n_agendadas, n_leads) if n_leads else \
        vendas.taxa_com_cobertura(0, 0)
    t_compareceu = vendas.taxa_com_cobertura(n_apareceu, n_respondidas, base=n_passou)
    t_sinal = vendas.taxa_com_cobertura(n_sinal, n_orc) if n_orc else \
        vendas.taxa_com_cobertura(0, 0)
    med = vendas.mediana(esperas)

    return {
        "label": "Funil", "mock": False,
        "colunas": [_col("lead", "Lead", flex=True), _col("vendedor", "Vendedor"),
                    _col("marcada", "Visita marcada"),
                    _col("esperou", "Esperou p/ agendar"),
                    _col("desfecho", "O cliente apareceu?", tag=True)],
        "linhas": linhas,
        # sem dinheiro nesta aba — mesma razão da aba Leads do chip
        "col_total": None, "total_centavos": 0,
        "metricas": [
            ("Leads → visita agendada",
             f"{t_agendou['texto']} · {n_agendadas} de {n_leads}"),
            ("Compareceram",
             f"{t_compareceu['texto']} · "
             + vendas.texto_da_cobertura(n_respondidas, n_passou, o_que="respondidas")),
            ("Viraram sinal pago",
             f"{t_sinal['texto']} · {n_sinal} de {n_orc} orçamentos"),
            ("Lead → agendamento (mediana)", vendas.duracao_curta(med)),
        ],
        # o AVISO é o achado, não rodapé: enquanto a maior parte das visitas que já
        # aconteceram estiver sem resposta, a taxa de comparecimento acima não
        # sustenta decisão nenhuma, e a tela tem que dizer isso antes da tabela.
        "aviso_config": (
            f"{n_passou - n_respondidas} das {n_passou} visitas que já aconteceram "
            "estão sem resposta — ninguém marcou se o cliente apareceu. Enquanto "
            "isso, a taxa de comparecimento sai de uma amostra pequena demais pra "
            "decidir. O vendedor responde pelo Cockpit, no bloco “Precisa de "
            "resposta”."
        ) if (n_passou - n_respondidas) > 0 else "",
        "filtro_extra": {
            "status_opcoes": FUNIL_OPCOES, "status_sel": status_sel,
            "vendedores": _vendedores_da_conta(pool, conta_id),
            "vendedor_sel": str(vendedor_sel or ""), "busca_sel": busca or "",
        },
    }


FUNIL_OPCOES = [
    ("", "Todas as visitas"), ("sem_resposta", "— Sem resposta —"),
    ("respondidas", "Respondidas"), ("sem_lead", "Sem lead ligado"),
]


TIPOS = {
    "vendas": {"label": "Vendas", "montar": lambda pool, cid, per, **f: _dados_vendas(pool, cid, per)},
    "contas_pagar": {"label": "Contas a pagar", "montar": lambda pool, cid, per, **f: _dados_titulos_abertos(pool, cid, "pagar")},
    "contas_receber": {"label": "Contas a receber", "montar": lambda pool, cid, per, **f: _dados_titulos_abertos(pool, cid, "receber")},
    "pagas": {"label": "Contas pagas", "montar": lambda pool, cid, per, **f: _dados_caixa(pool, cid, "pagar", per)},
    "comissao": {"label": "Comissão", "montar": lambda pool, cid, per, **f: _dados_comissao(pool, cid, per)},
    "recebidas": {"label": "Contas recebidas", "montar": lambda pool, cid, per, **f: _dados_caixa(pool, cid, "receber", per)},
    "orcamentos": {"label": "Orçamentos", "montar": lambda pool, cid, per, **f: _dados_orcamentos(
        pool, cid, per, f.get("status", ""), f.get("vendedor", ""), f.get("q", ""))},
    "contratos": {"label": "Contratos", "montar": lambda pool, cid, per, **f: _dados_contratos(
        pool, cid, per, f.get("status", ""), f.get("vendedor", ""), f.get("q", ""))},
    "agenda": {"label": "Agenda", "montar": lambda pool, cid, per, **f: _dados_agenda(
        pool, cid, per, f.get("status", ""), f.get("vendedor", ""), f.get("q", ""),
        especie=f.get("especie", ""), de=f.get("de"), ate=f.get("ate"))},
    # o filtro de chip viaja no MESMO parâmetro `status` das outras abas, de
    # propósito: o template já tem esse select e a rota já o repassa. Um
    # parâmetro novo obrigaria a mexer nos dois pra não ganhar nada.
    "leads_chip": {"label": "Leads do chip", "montar": lambda pool, cid, per, **f: _dados_leads_chip(
        pool, cid, per, f.get("status", ""), f.get("vendedor", ""), f.get("q", ""))},
    "funil": {"label": "Funil", "montar": lambda pool, cid, per, **f: _dados_funil(
        pool, cid, per, f.get("status", ""), f.get("vendedor", ""), f.get("q", ""))},
}


def _rotulo_periodo(tipo: str, periodo: str, de, ate) -> str:
    """O que aparece como "período: ..." no topo e no PDF. Em período específico
    o rótulo genérico não serve de nada — quem escolheu 01/12 a 31/12 quer ver
    isso escrito, não "Período específico…"."""
    if periodo == "personalizado":
        i, f = _intervalo(periodo, de, ate, ate_o_fim=(tipo == "agenda"))
        return f"{_fmt(i)} a {_fmt(f)}"
    return _PERIODO_ROTULO.get(periodo, periodo)


def _contexto(conta_id: int, tipo: str, periodo: str, status: str = "",
              vendedor: str = "", q: str = "", especie: str = "", de: str = "",
              ate: str = ""):
    tipo = tipo if tipo in TIPOS else "vendas"
    validos = {v for v, _ in periodos_da_aba(tipo)}
    periodo = periodo if periodo in validos else "mes"
    dados = TIPOS[tipo]["montar"](get_pool(), conta_id, periodo, status=status,
                                  vendedor=vendedor, q=q, especie=especie,
                                  de=de, ate=ate)
    return tipo, periodo, dados


# ---------------------------------------------------------------------------
# Ligar o compromisso a um cadastro
#
# A outra metade do conserto de 31/08/2026. A leitura do título tira 51 linhas do
# "—", mas ela é PALPITE — e palpite que fica pra sempre vira dado de mentira. Aqui
# o dono confirma, e o palpite vira `cliente_id`; ou diz que não há cliente, e a
# linha para de perguntar. É isso que faz a camada da leitura se esgotar com o uso
# em vez de virar moradia permanente.
# ---------------------------------------------------------------------------
def _evento_da_conta(pool, conta_id: int, evento_id: int):
    """(titulo, tipo_evento, inicio, cliente_id, sem_cliente, nome_lead, tel_lead)
    — ou None se o compromisso não for desta conta. O isolamento é aqui, e não na
    rota.

    O lead vem junto porque é ele que enche a tela: quando a visita foi marcada
    pelo funil, nome e número já existem em `prospeccao` e redigitá-los é o que
    faz ninguém usar o botão."""
    with pool.connection() as c:
        return c.execute(
            "select e.titulo, e.tipo_evento, e.inicio, e.cliente_id, "
            "       coalesce(e.sem_cliente,false), "
            "       coalesce(p.contato, p.empresa), "
            "       coalesce(p.whatsapp, p.telefone, '') "
            "  from eventos_agenda e "
            "  left join prospeccao p on p.id = e.prospeccao_id "
            " where e.id=%s and e.conta_id=%s",
            (evento_id, conta_id)).fetchone()


@router.get("/painel/relatorios/agenda/{evento_id}/cliente",
            response_class=HTMLResponse)
def painel_agenda_cliente(request: Request, evento_id: int):
    conta, redir = _pode_ver(request)
    if redir is not None:
        return redir
    pool = get_pool()
    ev = _evento_da_conta(pool, conta[0], evento_id)
    if ev is None:
        return RedirectResponse("/painel/relatorios?tipo=agenda", status_code=303)
    equipe = [n for _i, n in _vendedores_da_conta(pool, conta[0]) if n]
    # a caixa já vem preenchida com o palpite: quase sempre é só conferir e
    # clicar, e redigitar o que a tela acabou de mostrar é o tipo de trabalho que
    # faz ninguém usar a ferramenta. O LEAD manda no preenchimento quando existe:
    # ali o nome foi digitado por uma pessoa, enquanto o do título é leitura
    # nossa. E é o único dos dois que traz TELEFONE junto.
    return _render("agenda_cliente", request, conta=conta, evento_id=evento_id,
                   titulo_ev=ev[0], quando=_fmt_hora(ev[2]),
                   sugerido=(ev[5] or _ag.nome_no_titulo(ev[0], ev[1], equipe) or ""),
                   tel_sugerido=ev[6] or "", do_lead=bool(ev[5]),
                   erro=request.session.pop("erro", None))


@router.post("/painel/relatorios/agenda/{evento_id}/cliente")
def painel_agenda_cliente_salvar(request: Request, evento_id: int,
                                 cliente_id: str = Form(""),
                                 cliente_nome: str = Form(""),
                                 cliente_tel: str = Form(""),
                                 sem_cliente: str = Form("")):
    from finance import clientes as cli
    conta, redir = _pode_ver(request)
    if redir is not None:
        return redir
    pool = get_pool()
    volta = "/painel/relatorios?tipo=agenda"
    if _evento_da_conta(pool, conta[0], evento_id) is None:
        return RedirectResponse(volta, status_code=303)

    if sem_cliente == "1":
        _ag.marcar_sem_cliente(pool, conta[0], evento_id, True)
        request.session["aviso"] = "Marcado como sem cliente."
        return RedirectResponse(volta, status_code=303)

    # Régua de quatro respostas: escolhido na lista, achado pelo NÚMERO, achado
    # pelo nome exato, ou ficha nova. `salvar_cliente` só reaproveita por
    # documento ou telefone, e sem isto confirmar o palpite cunharia uma ficha
    # repetida a cada compromisso — que é o problema que este trabalho veio
    # fechar.
    #
    # O NÚMERO vem antes do nome de propósito: é a chave estável (a régua dos 8
    # finais, `buscar_unico_por_telefone`), enquanto o nome do lead é anotação de
    # vendedora e muda de um compromisso pro outro. Se o dono corrigiu
    # "Jacque/Elisangela 15 Anos" pra "Elisangela Moreira" mas o número já tem
    # ficha, o certo é ligar na ficha que existe, não abrir a segunda.
    alvo = None
    cid = (cliente_id or "").strip()
    tel = (cliente_tel or "").strip()
    if cid.isdigit() and cli.obter_cliente(pool, conta[0], int(cid)):
        alvo = int(cid)
    else:
        nome = (cliente_nome or "").strip()
        if not nome:
            request.session["erro"] = "Escolha um cliente ou diga que não há um."
            return RedirectResponse(f"/painel/relatorios/agenda/{evento_id}/cliente",
                                    status_code=303)
        pelo_tel = cli.buscar_unico_por_telefone(pool, conta[0], tel) if tel else None
        if pelo_tel:
            alvo = pelo_tel["id"]
        else:
            iguais = [c for c in cli.listar_clientes(pool, conta[0], busca=nome, limite=5)
                      if (c["nome"] or "").strip().casefold() == nome.casefold()]
            if len(iguais) == 1:
                alvo = iguais[0]["id"]
            elif len(iguais) > 1:
                request.session["erro"] = (
                    f"Há mais de um cadastro chamado “{nome}”. Escolha na lista qual é.")
                return RedirectResponse(f"/painel/relatorios/agenda/{evento_id}/cliente",
                                        status_code=303)
            else:
                # o TELEFONE entra na ficha nova. Sem ele, confirmar um lead
                # criaria cadastro sem número — exatamente o dado que a corrente
                # do funil estava perdendo, agora perdido no conserto dela.
                try:
                    alvo = cli.salvar_cliente(pool, conta[0], nome,
                                              telefone=(tel or None))["id"]
                except ValueError as e:
                    request.session["erro"] = str(e)
                    return RedirectResponse(
                        f"/painel/relatorios/agenda/{evento_id}/cliente", status_code=303)
    _ag.ligar_cliente(pool, conta[0], evento_id, alvo)
    request.session["aviso"] = "Compromisso ligado ao cadastro."
    return RedirectResponse(volta, status_code=303)


@router.get("/painel/relatorios", response_class=HTMLResponse)
def painel_relatorios(request: Request, tipo: str = "vendas", periodo: str = "mes",
                      status: str = "", vendedor: str = "", q: str = "",
                      especie: str = "", de: str = "", ate: str = ""):
    conta, redir = _pode_ver(request)
    if redir is not None:
        return redir
    tipo, periodo, dados = _contexto(conta[0], tipo, periodo, status, vendedor, q,
                                     especie, de, ate)
    # A caixa de liberar só existe pra QUEM LIBERA. `financeiro` (o gate desta
    # tela) deixa ver e conciliar; liberar conta a pagar é `gerir`, que no modelo
    # de papéis já é exclusivo do dono. Sem este corte, o gerente veria caixa e
    # botão que o servidor recusaria — pior que não ver.
    if dados.get("selecao") and not _pode_liberar(request):
        dados.pop("selecao")
    return _render("relatorios", request, tipos=TIPOS, tipo=tipo, periodo=periodo,
                   periodos=periodos_da_aba(tipo),
                   periodo_rotulo=_rotulo_periodo(tipo, periodo, de, ate),
                   tem_periodo_livre=any(v == "personalizado"
                                         for v, _ in periodos_da_aba(tipo)),
                   # o resultado da última gravação, uma vez só. Sem isto o dono
                   # clica em conciliar e a tela recarrega igualzinha, sem dizer se
                   # deu certo — e numa tela de dinheiro isso é pior que o erro.
                   aviso=request.session.pop("aviso", None),
                   erro=request.session.pop("erro", None),
                   de=de or "", ate=ate or "", dados=dados)


@router.get("/painel/relatorios/pdf", response_class=HTMLResponse)
def painel_relatorios_pdf(request: Request, tipo: str = "vendas", periodo: str = "mes",
                          status: str = "", vendedor: str = "", q: str = "",
                          especie: str = "", de: str = "", ate: str = ""):
    conta, redir = _pode_ver(request)
    if redir is not None:
        return redir
    pool = get_pool()
    tipo, periodo, dados = _contexto(conta[0], tipo, periodo, status, vendedor, q,
                                     especie, de, ate)
    from datetime import datetime
    return HTMLResponse(_env.get_template("relatorio_pdf").render(
        dados=dados, tipo=tipo, periodo=periodo,
        periodo_rotulo=_rotulo_periodo(tipo, periodo, de, ate),
        gerado_em=datetime.now().strftime("%d/%m/%Y %H:%M"),
        **_letterhead(pool, conta),
    ))


# ─────────────────────────────────────────────── conciliar (o passo que grava)
#
# As três rotas de leitura acima não tocam no banco. Estas duas tocam — e são as
# únicas do módulo que tocam. Por isso repetem o gate `_pode_ver` (capacidade
# financeiro) e revalidam TUDO no servidor: o pedido vem do navegador, e navegador
# não é fonte confiável. A régua da revalidação é a mesma que gerou o botão
# (`emp.pagamento_serve_pro_titulo`), então não existe botão que o servidor recuse
# por critério diferente do que a tela usou pra oferecer.

def _volta_pra(tipo: str, aba: str) -> RedirectResponse:
    return RedirectResponse(f"/painel/relatorios?tipo={aba}", status_code=303)


@router.post("/painel/relatorios/liberar")
def relatorios_liberar(request: Request, ids: list[str] = Form([])):
    """O dono libera o pagamento de várias contas de uma vez.

    LIBERAR NÃO PAGA, e a tela diz isso com todas as letras: autoriza que a conta
    seja paga. Quem paga é o "dar baixa" da aba Empresa, e continua lá — foi
    escolha do dono em 04/09/2026 manter as duas coisas separadas.

    Dois portões, e são diferentes de propósito: `_pode_ver` (capacidade
    `financeiro`) é quem entra na tela; `_pode_liberar` (capacidade `gerir`, do
    dono) é quem decide. A tela já esconde a caixa de quem não pode, mas esconder
    não é proteger: o POST vem do navegador.
    """
    conta, redir = _pode_ver(request)
    if redir is not None:
        return redir
    if not _pode_liberar(request):
        request.session["erro"] = ("Só o dono libera conta a pagar. "
                                   "Você pode ver e conciliar.")
        return _volta_pra("pagar", "contas_pagar")
    alvos = [int(i) for i in ids if str(i).strip().isdigit()]
    if not alvos:
        request.session["erro"] = "Marque as contas na lista antes."
        return _volta_pra("pagar", "contas_pagar")
    n = emp.decidir_aprovacao(get_pool(), conta[0], alvos, "autorizado",
                              membro_id=request.session.get("membro_id"))
    request.session["aviso"] = (
        f"{n} conta{'s' if n != 1 else ''} liberada{'s' if n != 1 else ''} "
        "pra pagamento. O dinheiro só sai quando alguém der baixa."
        if n else "Nada mudou — essas contas já estavam liberadas.")
    return _volta_pra("pagar", "contas_pagar")


# A rota que LIGAVA pagamento a conta saiu daqui em 04/09/2026 e foi pra aba
# Empresa (`/painel/empresa/titulo/{id}/conciliar`), junto do botão. Decisão do
# dono: "tudo vai ficar lá dentro da Empresa". O relatório ficou só com liberar e
# com o aviso "Talvez paga", que é informação, não ação.
#
# O DESFAZER continua aqui porque ele mora na aba Contas PAGAS, que é onde a
# conciliação aparece depois de feita — desfazer no lugar onde o erro se vê é o
# caminho curto.
@router.post("/painel/relatorios/desfazer-conciliacao")
def relatorios_desfazer_conciliacao(request: Request, titulo_id: int = Form(...),
                                    tipo: str = Form("pagar")):
    """Reabre a conta que foi ligada ao pagamento errado. O pagamento não se mexe."""
    conta, redir = _pode_ver(request)
    if redir is not None:
        return redir
    tipo = "pagar" if tipo != "receber" else "receber"
    aba = "pagas" if tipo == "pagar" else "recebidas"
    r = emp.desfazer_conciliacao(get_pool(), conta[0], titulo_id)
    if not r.get("ok"):
        request.session["erro"] = r.get("erro") or "Não consegui desfazer."
    else:
        request.session["aviso"] = (
            f"“{(r['descricao'] or '').strip()[:60]}” voltou pra em aberto. "
            "O pagamento continua no caixa, do jeito que estava.")
    return _volta_pra(tipo, aba)
