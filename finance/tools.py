"""Ferramentas do agente financeiro.

Cada ferramenta e' ligada ao livro-caixa de UM usuario. O agente chama elas
quando voce pede ("lanca 50 de mercado") ou quando le uma nota por foto.
"""
from datetime import date, datetime

from core.agent import Ferramenta
from contas.permissoes import pode_financas, pode_lista
from .livro_caixa import LivroCaixa
from .models import (
    Lancamento, Tipo, CATEGORIAS_DESPESA, CATEGORIAS_RECEITA,
    formatar_brl, reais_para_centavos,
)


def _parse_data(s: str | None) -> date:
    if not s:
        return date.today()
    for fmt in ("%d/%m/%Y", "%Y-%m-%d", "%d/%m/%y"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    return date.today()


def construir_ferramentas(livro: LivroCaixa, lista=None, papel: str = "dono",
                          banco=None, cidade: str | None = None) -> list[Ferramenta]:
    def lancar(tipo: Tipo, entrada: dict) -> str:
        from .models import canonizar_categoria
        cat_entrada = entrada.get("categoria", "Outros")
        cat_canon = canonizar_categoria(cat_entrada, tipo.value if hasattr(tipo, "value") else str(tipo))
        lanc = Lancamento.criar(
            tipo=tipo,
            valor_reais=entrada["valor"],
            categoria=cat_canon,
            descricao=entrada.get("descricao", ""),
            data=_parse_data(entrada.get("data")),
            pagamento=entrada.get("pagamento", ""),
            origem=entrada.get("origem", "manual"),
        )
        salvo = livro.adicionar(lanc)
        rotulo = "Despesa" if tipo == Tipo.DESPESA else "Receita"
        return (f"{rotulo} registrada: {formatar_brl(salvo.valor_centavos)} em "
                f"{salvo.categoria} ({salvo.data.strftime('%d/%m/%Y')}). id={salvo.id}")

    def lancar_despesa(entrada: dict) -> str:
        return lancar(Tipo.DESPESA, entrada)

    def lancar_receita(entrada: dict) -> str:
        return lancar(Tipo.RECEITA, entrada)

    def ver_saldo(_entrada: dict) -> str:
        return f"Saldo atual: {formatar_brl(livro.saldo_centavos())}"

    def relatorio_mes(entrada: dict) -> str:
        hoje = date.today()
        ano = int(entrada.get("ano") or hoje.year)
        mes = int(entrada.get("mes") or hoje.month)
        desp = livro.total_por_categoria(Tipo.DESPESA, mes=mes, ano=ano)
        total = sum(desp.values())
        if not desp:
            return f"Sem despesas em {mes:02d}/{ano}."
        linhas = [f"- {cat}: {formatar_brl(val)}" for cat, val in desp.items()]
        return (f"Despesas de {mes:02d}/{ano} (total {formatar_brl(total)}):\n"
                + "\n".join(linhas))

    def checar_duplicata(entrada: dict) -> str:
        valor = entrada.get("valor")
        if valor is None:
            return "Informe o valor pra checar."
        data = _parse_data(entrada.get("data"))
        cents = reais_para_centavos(valor)
        achados = livro.buscar_duplicata(cents, data)
        if not achados:
            return "Sem duplicata: pode registrar normalmente."
        linhas = []
        for a in achados:
            itens_txt = (f", JA TEM {a['qtd_itens']} itens salvos" if a.get("qtd_itens")
                         else ", ainda sem itens")
            linhas.append(f"- lancamento_id={a['id']}: {a['descricao'] or 'sem descricao'} "
                          f"(registrado em {a['criado_em'].strftime('%d/%m %H:%M')}{itens_txt})")
        return (f"ATENCAO - DUPLICATA PROVAVEL: ja existe {len(achados)} lancamento(s) "
                f"de ~{formatar_brl(cents)} na data {data.strftime('%d/%m/%Y')}:\n"
                + "\n".join(linhas)
                + "\nE' quase CERTO que e' o MESMO cupom. NAO registre de novo nem salve "
                  "itens de novo (isso duplica o raio-x). Avise o usuario que o cupom ja' "
                  "esta' registrado e so' prossiga se ele confirmar EXPLICITAMENTE que e' "
                  "uma compra diferente. Se um lancamento acima JA TEM itens, NUNCA salve "
                  "os itens de novo.")

    def registrar_itens_cupom(entrada: dict) -> str:
        itens_in = entrada.get("itens") or []
        if not itens_in:
            return "Nenhum item informado."
        lanc_id = entrada.get("lancamento_id") or livro.ultimo_lancamento_id()
        if not lanc_id:
            return "Nao achei um lancamento pra anexar os itens. Registre o cupom primeiro."
        itens = []
        for it in itens_in:
            vt = it.get("valor_total")
            vu = it.get("valor_unitario")
            itens.append({
                "descricao": (it.get("descricao") or "").strip() or "Item",
                "quantidade": it.get("quantidade") or 1,
                "valor_unitario_centavos": reais_para_centavos(vu) if vu is not None else 0,
                "valor_total_centavos": reais_para_centavos(vt) if vt is not None else 0,
                "unidade": (it.get("unidade") or "").strip() or None,
            })
        loja_info = {
            "cnpj": (entrada.get("cnpj_emitente") or "").strip() or None,
            "endereco": (entrada.get("endereco") or "").strip() or None,
            "nome": (entrada.get("estabelecimento") or "").strip() or None,
        }
        n = livro.registrar_itens(int(lanc_id), itens, loja_info=loja_info)
        if n == 0:
            return "Nao consegui salvar os itens (lancamento nao encontrado)."
        if n == -1:
            return ("Esse cupom JA' tem itens salvos - nao registrei de novo pra nao duplicar. "
                    "Se quiser substituir os itens antigos pelos novos, peca explicitamente "
                    "'substituir os itens'.")
        return f"Salvei {n} itens do cupom. Agora da' pra perguntar coisas tipo 'quanto gastei em X'."

    def buscar_itens(entrada: dict) -> str:
        termo = (entrada.get("termo") or "").strip()
        if not termo:
            return "Me diga o que procurar (ex: 'banana', 'flocao')."
        dias = int(entrada.get("dias") or 60)
        itens, total = livro.buscar_itens(termo, dias=dias)
        if not itens:
            return f"Nao achei itens com '{termo}' nos ultimos {dias} dias."
        linhas = [f"- {i['descricao']}: {formatar_brl(i['valor_total_centavos'])} "
                  f"({i['data'].strftime('%d/%m')})" for i in itens[:30]]
        return (f"Itens com '{termo}' (ultimos {dias} dias) - total {formatar_brl(total)}:\n"
                + "\n".join(linhas))

    def listar_itens(entrada: dict) -> str:
        dias = int(entrada.get("dias") or 60)
        itens = livro.listar_itens(dias=dias)
        if not itens:
            return (f"Nenhum item detalhado nos ultimos {dias} dias. "
                    "Os itens so' sao salvos quando voce pede pra registrar.")
        linhas = [f"- {i['descricao']}: {formatar_brl(i['valor_total_centavos'])} "
                  f"({i['data'].strftime('%d/%m')})" for i in itens]
        return f"Itens detalhados (ultimos {dias} dias):\n" + "\n".join(linhas)

    valor_schema = {"type": "number", "description": "Valor em reais, ex: 87.40"}

    item_schema = {
        "type": "object",
        "properties": {
            "descricao": {"type": "string", "description": "nome do produto"},
            "quantidade": {"type": "number", "description": "qtd comprada (pode ser kg, ex: 1.2)"},
            "valor_unitario": {"type": "number", "description": "preco de 1 unidade ou de 1 kg/L (NUNCA o total). ex: 58.43"},
            "valor_total": {"type": "number", "description": "valor total pago no item (qtd x unitario). ex: 70.12"},
            "unidade": {"type": "string", "description": "base do preco: 'KG' (peso), 'L' (volume) ou 'UN' (unidade/pacote). default UN"},
        },
        "required": ["descricao", "valor_total"],
    }

    # ---------- lista de compras (opcional; so' se 'lista' foi passada) ----------
    def add_lista(entrada: dict) -> str:
        if lista is None:
            return "Lista de compras nao disponivel."
        itens = entrada.get("itens") or []
        if isinstance(itens, str):
            itens = [itens]
        nomes = [str(i).strip() for i in itens if str(i).strip()]
        if not nomes:
            return "Nao entendi os itens. Quais produtos adicionar a' lista?"
        n = lista.adicionar_varios(nomes)
        if n == 1:
            return f"Adicionei '{nomes[0]}' a' lista de compras. 🛒"
        return f"Adicionei {n} itens a' lista de compras: {', '.join(nomes)}. 🛒"

    def ver_lista(entrada: dict) -> str:
        if lista is None:
            return "Lista de compras nao disponivel."
        itens = lista.listar(incluir_comprados=False)
        if not itens:
            return "A lista de compras esta vazia."
        linhas = []
        for i in itens:
            q = ""
            if i["quantidade"] and float(i["quantidade"]) != 1:
                q = f" ({i['quantidade']:g}{i['unidade'] or ''})"
            preco = ""
            if i["preco_estimado_centavos"]:
                preco = f" ~{formatar_brl(i['preco_estimado_centavos'])}"
            linhas.append(f"• {i['descricao']}{q}{preco}")
        r = lista.resumo()
        rodape = f"\n\n{r['pendentes']} item(ns) pendente(s)"
        if r["estimado_centavos"]:
            rodape += f" · estimativa: {formatar_brl(r['estimado_centavos'])}"
        return "📋 Lista de compras:\n" + "\n".join(linhas) + rodape

    def marcar_lista(entrada: dict) -> str:
        if lista is None:
            return "Lista de compras nao disponivel."
        termo = (entrada.get("descricao") or "").strip().lower()
        if not termo:
            return "Qual item voce comprou?"
        # acha o item pendente cuja descricao casa com o termo
        for i in lista.listar(incluir_comprados=False):
            if termo in i["descricao"].lower():
                lista.marcar_comprado(i["id"], True)
                return f"Marquei '{i['descricao']}' como comprado. ✓"
        return f"Nao achei '{termo}' entre os itens pendentes da lista."

    def remover_lista(entrada: dict) -> str:
        if lista is None:
            return "Lista de compras nao disponivel."
        termo = (entrada.get("descricao") or "").strip().lower()
        if not termo:
            return "Qual item voce quer tirar da lista?"
        for i in lista.listar(incluir_comprados=False):
            if termo in i["descricao"].lower():
                lista.remover(i["id"])
                return f"Tirei '{i['descricao']}' da lista. 🗑️"
        return f"Nao achei '{termo}' na lista pra remover."

    def comparar_lista(entrada: dict) -> str:
        if lista is None or banco is None:
            return "Comparador de precos nao disponivel."
        itens = [i["descricao"] for i in lista.listar(incluir_comprados=False)]
        if not itens:
            return "A lista de compras esta vazia. Adicione itens antes de comparar precos."
        from finance.banco_precos import comparar_para_cidade
        r = comparar_para_cidade(banco.pool, itens, cidade)
        if r["observacoes"] == 0:
            return ("Ainda nao tenho precos suficientes pra comparar essa lista. "
                    "Conforme voce e sua familia registram cupons de mercado, eu vou "
                    "aprendendo os precos e em breve consigo dizer onde a cesta sai mais barata. 📊")
        linhas = ["📊 Onde sua cesta sai mais barata (pelos cupons registrados):\n"]
        for i, m in enumerate(r["mercados"][:3], 1):
            falta = f" · faltam {len(m['itens_faltando'])} itens" if m["itens_faltando"] else " · cesta completa"
            linhas.append(f"{i}. {m['mercado']}: {formatar_brl(m['total_centavos'])}"
                          f" ({m['itens_cobertos']} itens{falta})")
        # itens sem nenhum preco
        sem = [d["descricao"] for d in r["itens"] if not d["precos"]]
        if sem:
            linhas.append(f"\nAinda sem preco: {', '.join(sem)}")
        linhas.append(f"\n(baseado em {r['observacoes']} precos dos seus cupons - melhora a cada compra)")
        return "\n".join(linhas)

    def avisar_pronta(_entrada: dict) -> str:
        if lista is None:
            return "Lista de compras nao disponivel."
        # descobre o nome de quem esta' avisando (pelo membro_id do livro)
        quem = "Alguem"
        try:
            with livro.pool.connection() as c:
                if livro.membro_id:
                    r = c.execute("select nome from membros where id=%s",
                                  (livro.membro_id,)).fetchone()
                    if r and r[0]:
                        quem = r[0]
        except Exception:  # noqa: BLE001
            pass
        n = 0
        try:
            n = len(lista.listar(incluir_comprados=False))
        except Exception:  # noqa: BLE001
            pass
        try:
            from finance.notificar import avisar_dono_lista_fechada
            ok = avisar_dono_lista_fechada(livro.pool, livro.conta_id, quem, n_itens=n)
        except Exception:  # noqa: BLE001
            ok = False
        if ok:
            return ("Avisei o responsavel que voce terminou a lista! ✓ "
                    "Pode continuar adicionando se lembrar de mais alguma coisa.")
        return ("Anotei que voce terminou a lista! ✓ "
                "(o aviso chega quando o responsavel conectar o Telegram)")

    financeiras = [
        Ferramenta(
            nome="lancar_despesa",
            descricao="Registra uma despesa (saida de dinheiro) no livro-caixa.",
            parametros={
                "type": "object",
                "properties": {
                    "valor": valor_schema,
                    "categoria": {"type": "string", "enum": CATEGORIAS_DESPESA},
                    "descricao": {"type": "string"},
                    "data": {"type": "string", "description": "dd/mm/aaaa; vazio = hoje"},
                    "pagamento": {"type": "string", "description": "ex: Pix, cartao, dinheiro"},
                    "origem": {"type": "string", "enum": ["manual", "foto"]},
                },
                "required": ["valor", "categoria"],
            },
            executar=lancar_despesa,
        ),
        Ferramenta(
            nome="lancar_receita",
            descricao="Registra uma receita (entrada de dinheiro) no livro-caixa.",
            parametros={
                "type": "object",
                "properties": {
                    "valor": valor_schema,
                    "categoria": {"type": "string", "enum": CATEGORIAS_RECEITA},
                    "descricao": {"type": "string"},
                    "data": {"type": "string", "description": "dd/mm/aaaa; vazio = hoje"},
                    "origem": {"type": "string", "enum": ["manual", "foto"]},
                },
                "required": ["valor", "categoria"],
            },
            executar=lancar_receita,
        ),
        Ferramenta(
            nome="ver_saldo",
            descricao="Mostra o saldo atual (receitas menos despesas).",
            parametros={"type": "object", "properties": {}},
            executar=ver_saldo,
        ),
        Ferramenta(
            nome="relatorio_mes",
            descricao="Resumo de despesas por categoria de um mes.",
            parametros={
                "type": "object",
                "properties": {
                    "mes": {"type": "integer", "description": "1 a 12; vazio = mes atual"},
                    "ano": {"type": "integer", "description": "vazio = ano atual"},
                },
            },
            executar=relatorio_mes,
        ),
        Ferramenta(
            nome="checar_duplicata",
            descricao=("Verifica se ja' existe um lancamento igual (mesmo valor e data). "
                       "Use SEMPRE antes de salvar um cupom por foto, pra evitar registrar "
                       "o mesmo cupom duas vezes."),
            parametros={
                "type": "object",
                "properties": {
                    "valor": valor_schema,
                    "data": {"type": "string", "description": "dd/mm/aaaa do cupom; vazio = hoje"},
                },
                "required": ["valor"],
            },
            executar=checar_duplicata,
        ),
        Ferramenta(
            nome="registrar_itens_cupom",
            descricao=("Salva os itens individuais de um cupom (produto a produto). "
                       "Use SO' quando o usuario pedir pra registrar/detalhar os itens. "
                       "Por padrao, anexa ao ultimo lancamento registrado."),
            parametros={
                "type": "object",
                "properties": {
                    "itens": {"type": "array", "items": item_schema},
                    "lancamento_id": {"type": "integer", "description": "opcional; vazio = ultimo cupom"},
                    "cnpj_emitente": {"type": "string", "description": "CNPJ do estabelecimento (14 digitos), do cabecalho do cupom - identifica a loja exata"},
                    "estabelecimento": {"type": "string", "description": "nome da loja (ex: 'Carvalho Supershop'), do cabecalho do cupom"},
                    "endereco": {"type": "string", "description": "endereco da loja (rua, numero, bairro), do cabecalho do cupom"},
                },
                "required": ["itens"],
            },
            executar=registrar_itens_cupom,
        ),
        Ferramenta(
            nome="buscar_itens",
            descricao=("Procura itens comprados pela descricao (ex: 'banana', 'flocao') "
                       "e soma quanto foi gasto. Use pra 'quanto gastei em X'."),
            parametros={
                "type": "object",
                "properties": {
                    "termo": {"type": "string", "description": "texto a procurar na descricao"},
                    "dias": {"type": "integer", "description": "janela em dias; vazio = 60"},
                },
                "required": ["termo"],
            },
            executar=buscar_itens,
        ),
        Ferramenta(
            nome="listar_itens",
            descricao=("Lista todos os itens detalhados de um periodo. Use pra perguntas "
                       "por GRUPO (ex: 'quanto gastei em frutas') - voce le a lista e soma "
                       "os que se encaixam."),
            parametros={
                "type": "object",
                "properties": {
                    "dias": {"type": "integer", "description": "janela em dias; vazio = 60"},
                },
            },
            executar=listar_itens,
        ),
    ]

    lista_ferr = ([
        Ferramenta(
            nome="adicionar_lista_compras",
            descricao=("Adiciona um ou mais itens a' LISTA DE COMPRAS (o que ainda PRECISA "
                       "comprar - diferente de registrar um gasto ja' feito). Use quando a "
                       "pessoa disser que acabou algo, que precisa comprar, ou ditar uma lista "
                       "(ex: 'preciso de arroz, cafe e detergente', 'acabou o sabao')."),
            parametros={
                "type": "object",
                "properties": {
                    "itens": {"type": "array", "items": {"type": "string"},
                              "description": "nomes dos produtos a adicionar"},
                },
                "required": ["itens"],
            },
            executar=add_lista,
        ),
        Ferramenta(
            nome="ver_lista_compras",
            descricao="Mostra a lista de compras atual (itens que ainda faltam comprar).",
            parametros={"type": "object", "properties": {}},
            executar=ver_lista,
        ),
        Ferramenta(
            nome="marcar_comprado_lista",
            descricao=("Marca um item da lista de compras como JA' COMPRADO (sai da lista de "
                       "pendentes). Use quando a pessoa disser que comprou/pegou um item."),
            parametros={
                "type": "object",
                "properties": {
                    "descricao": {"type": "string", "description": "nome do item comprado"},
                },
                "required": ["descricao"],
            },
            executar=marcar_lista,
        ),
        Ferramenta(
            nome="remover_lista_compras",
            descricao=("Remove/tira um item da LISTA DE COMPRAS (apaga de vez, diferente de "
                       "marcar como comprado). Use quando a pessoa disser que adicionou errado, "
                       "que nao precisa mais, que quer tirar/apagar/excluir um item da lista, "
                       "ou pra corrigir um item que entrou por engano (ex: foto errada)."),
            parametros={
                "type": "object",
                "properties": {
                    "descricao": {"type": "string",
                                  "description": "nome do item a remover da lista"},
                },
                "required": ["descricao"],
            },
            executar=remover_lista,
        ),
        Ferramenta(
            nome="avisar_lista_pronta",
            descricao=("Avisa o responsavel/dono da conta que a pessoa TERMINOU de montar "
                       "a lista de compras (manda um aviso no Telegram do dono). Use quando "
                       "a pessoa disser que terminou, que fechou a lista, que acabou de "
                       "adicionar tudo, 'pronto, terminei', 'ja' coloquei tudo', 'pode comprar', "
                       "ou algo que indique que ela finalizou os pedidos por agora. A lista NAO "
                       "trava - ela ainda pode adicionar mais coisas depois."),
            parametros={"type": "object", "properties": {}},
            executar=avisar_pronta,
        ),
        Ferramenta(
            nome="comparar_precos_lista",
            descricao=("Compara onde a CESTA da lista de compras sai mais barata, usando os "
                       "precos aprendidos dos cupons registrados. Use quando perguntarem "
                       "'onde compro mais barato?', 'qual mercado e' melhor pra minha lista?', "
                       "'onde a cesta sai em conta?'."),
            parametros={"type": "object", "properties": {}},
            executar=comparar_lista,
        ),
    ] if lista is not None else [])

    # monta o conjunto conforme o PAPEL: restrito so' enxerga a lista.
    ferramentas = []
    if pode_financas(papel):
        ferramentas += financeiras
    if pode_lista(papel):
        ferramentas += lista_ferr
    return ferramentas
