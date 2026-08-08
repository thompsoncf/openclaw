"""Persona e ferramentas PJ do bot (Módulo Empresa) — Leva 2C.

Plugado SÓ quando a conta é PJ com o módulo ativo. Não altera a persona PF
nem o núcleo do agente: apenas ADICIONA um bloco ao prompt e ferramentas à
lista. As ferramentas executam de verdade (o dono autorizou execução direta).
"""
from __future__ import annotations

from datetime import date, datetime

from core.agent import Ferramenta
from . import empresa as emp
from .models import formatar_brl


# ─────────────────────────────────────────────────────────────────────────
# Persona: bloco anexado ao prompt quando a conta é PJ
# ─────────────────────────────────────────────────────────────────────────
def _nicho_da_conta(pool, conta_id: int) -> str:
    """Slug do nicho da conta (ou '' se não tiver). Usado pra moldar a persona."""
    try:
        with pool.connection() as c:
            r = c.execute(
                "select coalesce(n.slug,'') from contas ct "
                "left join nichos n on n.id = ct.nicho_id where ct.id=%s",
                (conta_id,)).fetchone()
        return r[0] if r else ""
    except Exception:
        return ""


def bloco_persona_pj(pool, conta_id: int, empresa_nome: str = "") -> str:
    """Contexto empresarial pro prompt: quem é a empresa + o que está aberto.
    Molda ao NICHO/CNAE da empresa: anexa o bloco de persona do ramo (o
    'sentimento') quando houver, pra o bot falar/ajudar como aquele negócio."""
    hoje = date.today().strftime("%d/%m/%Y")
    try:
        res = emp.resumo_titulos(pool, conta_id)
        funcs = emp.listar_funcionarios(pool, conta_id, so_ativos=True)
    except Exception:
        res, funcs = {}, []
    nomes = ", ".join(f["nome"] for f in funcs[:8]) or "nenhum cadastrado"
    a_pagar = formatar_brl(res.get("a_pagar_centavos", 0))
    a_receber = formatar_brl(res.get("a_receber_centavos", 0))
    atrasados = res.get("n_atrasados", 0)
    nome = empresa_nome or "a empresa"

    from .nichos import persona_do_nicho
    slug = _nicho_da_conta(pool, conta_id)
    bloco_nicho = persona_do_nicho(slug)
    bloco_nicho = f"\n{bloco_nicho}\n" if bloco_nicho else ""
    # a linha "a folha oficial é do contador" não faz sentido pra um contador:
    # pro ramo contabilidade, o próprio molde do nicho já reenquadra a folha.
    linha_folha = ("" if slug == "contabilidade" else
                   "- Folha é controle GERENCIAL; a folha oficial (eSocial/guias) "
                   "segue com a contabilidade. Não prometa cálculo de eSocial.\n")

    return f"""

──────────────────────────────────────────────────────────
MODO EMPRESA (esta conta é PJ). Hoje é {hoje}.

Você também é o braço financeiro de {nome}. Além do controle pessoal, cuida
das CONTAS DA EMPRESA: títulos a pagar/receber, funcionários e folha.
{bloco_nicho}
Situação atual: a pagar {a_pagar} · a receber {a_receber} · {atrasados} título(s) atrasado(s).
Funcionários: {nomes}.

O QUE FAZER (use as ferramentas de empresa):
- "boleto/conta da luz R$ X vence dia Y", "tenho que pagar Z" -> criar_titulo (tipo=pagar).
- "fulano me deve X", "vou receber Y de cliente" -> criar_titulo (tipo=receber).
- "paguei o boleto X", "quita o título Y", "deu baixa no aluguel" -> dar_baixa_titulo.
- "dei um vale de X pro fulano", "adiantei Y pro João" -> registrar_vale.
- "como está a folha?", "quanto devo de salário?", "qual meu saldo/fluxo?",
  "o que tenho a pagar?" -> consultar_empresa.

PESSOAL x EMPRESA (esta conta mistura os dois — ajude a separar):
- Ao registrar um GASTO ou RECEITA do dia-a-dia (não título/folha): se a pessoa JÁ
  disse se foi pessoal ou da empresa, passe natureza="pessoal" ou "empresa" DIRETO no
  lancar_despesa/lancar_receita (num passo só). Se ela NÃO disse, pergunte de forma
  leve "esse foi pessoal ou da empresa?" e, quando responder, use marcar_natureza com
  o id. Se ela não responder ou mudar de assunto, tudo bem — fica "a definir" (NÃO insista).
- CONTA CONTÁBIL (DRE): quando o gasto/receita for de EMPRESA, escolha também a
  conta contábil do plano e passe o código em plano_conta (ex: "5.1.03") no MESMO
  lancar — sugira pela categoria/descrição. Se não souber os códigos habilitados,
  chame plano_de_contas antes. Se a pessoa disser a unidade/projeto, passe
  centro_custo (nome). É opcional: na dúvida deixe vazio (classifica depois no
  painel). Aceite tanto o código quanto o nome que a pessoa escrever.
- "marca aquele gasto como empresa", "o do posto foi pessoal" -> marcar_natureza.
- "como foi o mês da empresa?", "relatório da empresa", "quanto gastei de
  pessoal?", "me separa pessoal e empresa" -> relatorio_separado.
- Título e folha JÁ entram como empresa sozinhos — não pergunte a natureza deles.

REGRAS:
- Valores em reais (ex: 1500 = R$ 1.500,00). Datas em dd/mm/aaaa; se a pessoa
  disser "dia 15", assuma o dia 15 do mês atual (ou do próximo se já passou).
- Ao dar um vale, confirme de forma humana quanto o funcionário fica a receber.
- Se faltar informação essencial (valor, ou de quem é o vale), PERGUNTE antes.
{linha_folha}- NUNCA invente funcionário que não está na lista acima; se não achar, pergunte.
──────────────────────────────────────────────────────────
"""


# ─────────────────────────────────────────────────────────────────────────
# Ferramentas de empresa (executam de verdade)
# ─────────────────────────────────────────────────────────────────────────
def _parse_data_pj(s: str | None) -> date:
    s = (s or "").strip()
    if not s:
        return date.today()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # "dia 15" / "15"
    dig = "".join(ch for ch in s if ch.isdigit())
    if dig:
        hoje = date.today()
        dia = min(int(dig[:2]), 28)
        venc = date(hoje.year, hoje.month, dia)
        if venc < hoje:  # "dia 15" que já passou → rola pro mês seguinte
            ano, mes = (hoje.year + 1, 1) if hoje.month == 12 else (hoje.year, hoje.month + 1)
            venc = date(ano, mes, dia)
        return venc
    return date.today()


def _acha_funcionario(pool, conta_id: int, nome: str):
    nome_n = (nome or "").strip().casefold()
    if not nome_n:
        return None
    funcs = emp.listar_funcionarios(pool, conta_id, so_ativos=True)
    # match exato, depois "começa com", depois "contém"
    for f in funcs:
        if f["nome"].casefold() == nome_n:
            return f
    for f in funcs:
        if f["nome"].casefold().startswith(nome_n) or nome_n in f["nome"].casefold():
            return f
    return None


def construir_ferramentas_pj(pool, conta_id: int,
                             membro_id: int | None = None) -> list[Ferramenta]:
    """Ferramentas de empresa, escopadas na conta (multi-tenant)."""

    def criar_titulo(e: dict) -> str:
        tipo = "receber" if str(e.get("tipo", "")).startswith("receb") else "pagar"
        valor = e.get("valor")
        desc = (e.get("descricao") or "").strip()
        if not valor or not desc:
            return "Preciso do valor e da descrição pra criar o título."
        cent = int(round(float(valor) * 100))
        venc = _parse_data_pj(e.get("vencimento"))
        contraparte = (e.get("contraparte") or "").strip()
        # LIGA ao cliente da base pelo nome (contraparte), sem criar duplicado.
        # Assim o honorário/venda a prazo aparece na ficha do cliente (carteira).
        cli_id = None
        if contraparte:
            try:
                from . import clientes as _cli
                cli_id = _cli.achar_cliente_por_nome(pool, conta_id, contraparte)
            except Exception:
                cli_id = None
        t = emp.criar_titulo(pool, conta_id, tipo, desc, cent, venc,
                             contraparte=contraparte,
                             recorrente=bool(e.get("recorrente")),
                             criado_por=membro_id, cliente_id=cli_id)
        lado = "a pagar" if tipo == "pagar" else "a receber"
        extra = " (ligado à ficha do cliente)" if cli_id else ""
        return (f"Título {lado} criado: {desc} — {formatar_brl(cent)}, "
                f"vence {venc.strftime('%d/%m/%Y')}.{extra} id={t['id']}")

    def dar_baixa_titulo(e: dict) -> str:
        tid = e.get("titulo_id")
        if tid:
            r = emp.dar_baixa_titulo(pool, conta_id, int(tid), membro_id=membro_id)
            return ("Baixa registrada e lançada no caixa." if r["ok"]
                    else f"Não consegui: {r.get('erro')}")
        # sem id: tenta achar pela descrição entre os abertos
        desc = (e.get("descricao") or "").strip().casefold()
        abertos = emp.listar_titulos(pool, conta_id, status="aberto")
        cand = [t for t in abertos if desc and desc in (t["descricao"] or "").casefold()]
        if len(cand) == 1:
            r = emp.dar_baixa_titulo(pool, conta_id, cand[0]["id"], membro_id=membro_id)
            return (f"Baixei '{cand[0]['descricao']}' ({formatar_brl(cand[0]['valor_centavos'])}) "
                    "e lancei no caixa." if r["ok"] else f"Não consegui: {r.get('erro')}")
        if len(cand) > 1:
            return ("Tem mais de um título parecido: "
                    + "; ".join(f"{t['descricao']} ({formatar_brl(t['valor_centavos'])})"
                                for t in cand[:5]) + ". Qual deles?")
        return "Não achei esse título em aberto. Pode dizer o valor ou a descrição exata?"

    def registrar_vale(e: dict) -> str:
        nome = (e.get("funcionario") or "").strip()
        valor = e.get("valor")
        if not nome or not valor:
            return "Preciso do nome do funcionário e do valor do vale."
        f = _acha_funcionario(pool, conta_id, nome)
        if not f:
            return (f"Não achei '{nome}' na equipe. Confere o nome? "
                    "(ou cadastre o funcionário primeiro pelo painel)")
        cent = int(round(float(valor) * 100))
        r = emp.registrar_evento_folha(pool, conta_id, f["id"], "vale", cent,
                                       membro_id=membro_id)
        if not r.get("ok"):
            return f"Não consegui: {r.get('erro')}"
        hoje = date.today()
        folha = emp.folha_do_mes(pool, conta_id, hoje.year, hoje.month)
        item = next((i for i in folha["itens"] if i["id"] == f["id"]), None)
        resta = formatar_brl(item["a_pagar_centavos"]) if item else "?"
        return (f"Vale de {formatar_brl(cent)} pro {f['nome']} registrado "
                f"(saiu do caixa como Pessoal). Ele fica com {resta} a receber "
                f"na folha de {hoje.month:02d}/{hoje.year}.")

    def consultar_empresa(e: dict) -> str:
        hoje = date.today()
        res = emp.resumo_titulos(pool, conta_id)
        folha = emp.folha_do_mes(pool, conta_id, hoje.year, hoje.month)
        fluxo = emp.fluxo_projetado(pool, conta_id)
        partes = [
            f"A pagar (7d): {formatar_brl(res['a_pagar_centavos'])} ({res['n_pagar']}).",
            f"A receber (7d): {formatar_brl(res['a_receber_centavos'])} ({res['n_receber']}).",
        ]
        if res["n_atrasados"]:
            partes.append(f"Atrasados: {res['n_atrasados']} "
                          f"({formatar_brl(res['atrasados_centavos'])}).")
        partes.append(f"Folha do mês a pagar: {formatar_brl(folha['total_a_pagar_centavos'])} "
                      f"(custo real ≈ {formatar_brl(folha['custo_real_total_centavos'])}).")
        partes.append(f"Saldo hoje {formatar_brl(fluxo['saldo_atual_centavos'])}; "
                      f"projetado {formatar_brl(fluxo['saldo_projetado_centavos'])}.")
        # Carteira: quem está devendo (atrasado) — pro bot poder cobrar proativo.
        try:
            cart = emp.resumo_carteira(pool, conta_id)
            devedores = [c for c in cart["clientes"] if c["atrasado_centavos"] > 0]
            if devedores:
                topo = "; ".join(f"{c['nome']} {formatar_brl(c['atrasado_centavos'])}"
                                 for c in devedores[:3])
                partes.append(f"Atrasados na carteira: {topo}.")
        except Exception:
            pass
        return " ".join(partes)

    def marcar_natureza_tool(e: dict) -> str:
        from .livro_caixa import LivroCaixa
        lid = e.get("lancamento_id")
        nat = e.get("natureza")
        if not lid or nat not in ("pessoal", "empresa"):
            return "Preciso do id do lançamento e se é pessoal ou empresa."
        liv = LivroCaixa(pool, conta_id)
        ok = liv.marcar_natureza(int(lid), nat)
        if not ok:
            return "Não achei esse lançamento pra marcar."
        rot = "🏢 empresa" if nat == "empresa" else "pessoal"
        return f"Marquei como {rot}. ✅"

    def relatorio_separado(e: dict) -> str:
        from datetime import date as _date
        from .livro_caixa import LivroCaixa
        hoje = _date.today()
        mes = int(e.get("mes") or hoje.month)
        ano = int(e.get("ano") or hoje.year)
        liv = LivroCaixa(pool, conta_id)
        # res_emp (não `emp`): `emp` é o módulo importado no topo (from . import
        # empresa as emp); reaproveitar o nome aqui sombreava o módulo.
        res_emp = liv.resumo_mes(ano, mes, natureza="empresa")
        pes = liv.resumo_mes(ano, mes, natureza="pessoal")
        ndef = liv.contar_a_definir(ano, mes)
        def _res(r):
            saldo = r["receitas"] - r["despesas"]
            return (f"receitas {formatar_brl(r['receitas'])}, "
                    f"despesas {formatar_brl(r['despesas'])}, "
                    f"resultado {formatar_brl(saldo)}")
        partes = [
            f"📅 {mes:02d}/{ano}",
            f"🏢 Empresa: {_res(res_emp)}.",
            f"Pessoal: {_res(pes)}.",
        ]
        if ndef:
            partes.append(f"💡 {ndef} lançamento(s) ainda a definir "
                          "(me diz quais são pessoal ou empresa pra eu incluir).")
        return " ".join(partes)

    def cadastrar_cliente(e: dict) -> str:
        nome = (e.get("nome") or "").strip()
        if not nome:
            return "Preciso do nome do cliente pra cadastrar."
        from . import clientes as _cli
        ja = _cli.achar_cliente_por_nome(pool, conta_id, nome)
        if ja:
            return f"'{nome}' já está na base de clientes. ✅"
        try:
            _cli.criar_cliente(pool, conta_id, nome,
                               telefone=(e.get("telefone") or "").strip() or None)
        except Exception:
            return "Não consegui cadastrar agora."
        return (f"Cliente '{nome}' cadastrado. ✅ Já pode lançar o honorário/título "
                "a receber dele que fica ligado à ficha.")

    def plano_de_contas(_entrada: dict) -> str:
        from . import plano_contas as _pc
        grupos = _pc.opcoes_lancamento(pool, conta_id)
        if not grupos:
            return ("Esta conta ainda não tem contas contábeis habilitadas. "
                    "O dono habilita no painel, em Empresa › Plano de Contas.")
        linhas = [f"{c['codigo']} {c['nome']}"
                  for g in grupos for c in g["contas"]]
        centros = [c["nome"] for c in _pc.listar_centros(pool, conta_id)]
        return ("Contas contábeis habilitadas (use o código no lancar):\n"
                + "\n".join(linhas)
                + "\n\nCentros de custo: "
                + (", ".join(centros) if centros else "nenhum cadastrado"))

    valor_s = {"type": "number", "description": "valor em reais, ex 1500.50"}
    return [
        Ferramenta(
            nome="plano_de_contas",
            descricao=("Lista as CONTAS CONTÁBEIS habilitadas e os CENTROS DE CUSTO "
                       "desta empresa. Use antes de registrar um gasto/receita de "
                       "empresa quando precisar do código da conta contábil (param "
                       "plano_conta do lancar), ou se a pessoa perguntar quais existem."),
            parametros={"type": "object", "properties": {}},
            executar=plano_de_contas,
        ),
        Ferramenta(
            nome="cadastrar_cliente",
            descricao=("Cadastra um CLIENTE na base (pra ligar honorário/venda a "
                       "prazo à ficha dele). Use quando for lançar um título a "
                       "receber e o cliente ainda não existir — depois de confirmar "
                       "com o dono."),
            parametros={
                "type": "object",
                "properties": {
                    "nome": {"type": "string", "description": "nome do cliente"},
                    "telefone": {"type": "string", "description": "telefone (opcional)"},
                },
                "required": ["nome"],
            },
            executar=cadastrar_cliente,
        ),
        Ferramenta(
            nome="criar_titulo",
            descricao="Cria uma conta a pagar ou a receber (título) da empresa.",
            parametros={
                "type": "object",
                "properties": {
                    "tipo": {"type": "string", "enum": ["pagar", "receber"]},
                    "descricao": {"type": "string"},
                    "valor": valor_s,
                    "vencimento": {"type": "string", "description": "dd/mm/aaaa ou 'dia 15'; vazio=hoje"},
                    "contraparte": {"type": "string", "description": "fornecedor ou cliente (opcional)"},
                    "recorrente": {"type": "boolean", "description": "mensal (opcional)"},
                },
                "required": ["tipo", "descricao", "valor"],
            },
            executar=criar_titulo,
        ),
        Ferramenta(
            nome="dar_baixa_titulo",
            descricao="Marca um título como pago/recebido e lança no caixa. Use titulo_id se souber, senão a descrição.",
            parametros={
                "type": "object",
                "properties": {
                    "titulo_id": {"type": "integer"},
                    "descricao": {"type": "string", "description": "parte da descrição do título"},
                },
            },
            executar=dar_baixa_titulo,
        ),
        Ferramenta(
            nome="registrar_vale",
            descricao="Registra um vale/adiantamento pra um funcionário (desconta da folha e sai do caixa).",
            parametros={
                "type": "object",
                "properties": {
                    "funcionario": {"type": "string", "description": "nome do funcionário"},
                    "valor": valor_s,
                },
                "required": ["funcionario", "valor"],
            },
            executar=registrar_vale,
        ),
        Ferramenta(
            nome="consultar_empresa",
            descricao="Resumo da empresa: títulos a pagar/receber, atrasados, folha do mês e saldo/fluxo.",
            parametros={"type": "object", "properties": {}},
            executar=consultar_empresa,
        ),
        Ferramenta(
            nome="marcar_natureza",
            descricao="Marca um lançamento como pessoal ou empresa. Use o id do lançamento (o registro devolve id=N).",
            parametros={
                "type": "object",
                "properties": {
                    "lancamento_id": {"type": "integer"},
                    "natureza": {"type": "string", "enum": ["pessoal", "empresa"]},
                },
                "required": ["lancamento_id", "natureza"],
            },
            executar=marcar_natureza_tool,
        ),
        Ferramenta(
            nome="relatorio_separado",
            descricao="Relatório do mês separando pessoal e empresa (receitas, despesas, resultado). Mostra também quantos estão a definir.",
            parametros={
                "type": "object",
                "properties": {
                    "mes": {"type": "integer", "description": "1-12; vazio = mês atual"},
                    "ano": {"type": "integer", "description": "vazio = ano atual"},
                },
            },
            executar=relatorio_separado,
        ),
    ]
