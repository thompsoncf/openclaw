"""Ferramentas do agente financeiro.

Cada ferramenta e' ligada ao livro-caixa de UM usuario. O agente chama elas
quando voce pede ("lanca 50 de mercado") ou quando le uma nota por foto.
"""
from datetime import date, datetime

from core.agent import Ferramenta
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


def construir_ferramentas(livro: LivroCaixa, lista=None) -> list[Ferramenta]:
    def lancar(tipo: Tipo, entrada: dict) -> str:
        lanc = Lancamento.criar(
            tipo=tipo,
            valor_reais=entrada["valor"],
            categoria=entrada.get("categoria", "Outros"),
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
        linhas = [f"- lancamento_id={a['id']}: {a['descricao'] or 'sem descricao'} "
                  f"(registrado em {a['criado_em'].strftime('%d/%m %H:%M')})" for a in achados]
        return (f"ATENCAO: ja existe {len(achados)} lancamento(s) de {formatar_brl(cents)} "
                f"na data {data.strftime('%d/%m/%Y')}:\n" + "\n".join(linhas)
                + "\nProvavelmente e' o MESMO cupom. Avise o usuario e so' registre se ele confirmar. "
                  "Se ele quiser anexar ITENS ao cupom existente, use o lancamento_id acima.")

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
            })
        n = livro.registrar_itens(int(lanc_id), itens)
        if n == 0:
            return "Nao consegui salvar os itens (lancamento nao encontrado)."
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
            "quantidade": {"type": "number", "description": "qtd (pode ser kg, ex: 1.99)"},
            "valor_unitario": {"type": "number", "description": "preco unitario em reais"},
            "valor_total": {"type": "number", "description": "preco total do item em reais"},
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
        return "🛒 Lista de compras:\n" + "\n".join(linhas) + rodape

    def marcar_lista(entrada: dict) -> str:
        if lista is None:
            return "Lista de compras nao disponivel."
        termo = (entrada.get("descricao") or "").strip().lower()
        if not termo:
            return "Qual item voce comprou?"
        for i in lista.listar(incluir_comprados=False):
            if termo in i["descricao"].lower():
                lista.marcar_comprado(i["id"], True)
                return f"Marquei '{i['descricao']}' como comprado. ✅"
        return f"Nao achei '{termo}' entre os itens pendentes da lista."

    return [
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
    ] + ([
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
    ] if lista is not None else [])
