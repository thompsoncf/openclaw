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
    # OS CENTROS DE CUSTO DA CONTA, com os nomes que o dono deu — o agente
    # passou a conhecê-los em 23/09/2026. Só LEITURA: nenhum centro é criado nem
    # mexido daqui ("não mexer em centro de custos", regra do dono no mesmo dia).
    try:
        from . import plano_contas as _pc
        centros = [c["nome"] for c in _pc.listar_centros(pool, conta_id)]
    except Exception:
        centros = []
    linha_centros = (
        f"CENTROS DE CUSTO desta empresa (a ÁREA do negócio): {', '.join(centros)}.\n"
        "  Ao registrar gasto/receita de EMPRESA, passe centro_custo com o nome EXATO\n"
        "  de um deles QUANDO A PESSOA DISSER de qual área foi. Não escolha por conta\n"
        "  própria.\n"
        if centros else "")
    # O TIPO DE DESPESA (325) é pergunta PRÓPRIA, separada do centro — correção
    # do dono em 24/09/2026: "fixa, eventual, investimento não é centro de custo".
    # Mesmo que exista um centro chamado INVESTIMENTO, "foi investimento" vai no
    # tipo, nunca no centro.
    linha_centros += (
        "TIPO DE DESPESA (fixa, eventual, investimento) é SEPARADO do centro:\n"
        "  'foi investimento' -> tipo_despesa=investimento; 'é conta fixa' -> fixa;\n"
        "  'foi eventual' -> eventual. NUNCA ponha isso em centro_custo, mesmo que\n"
        "  exista um centro com esse nome. Só quando a pessoa disser.\n")
    a_pagar = formatar_brl(res.get("a_pagar_centavos", 0))
    a_receber = formatar_brl(res.get("a_receber_centavos", 0))
    atrasados = res.get("n_atrasados", 0)
    nome = empresa_nome or "a empresa"

    from .nichos import persona_do_nicho
    slug = _nicho_da_conta(pool, conta_id)
    bloco_nicho = persona_do_nicho(slug)
    # A CONSTRUTORA fala das obras DELA: a lista e o que fazer com cada pedido
    # (finance/obras.py). É o que liga a persona do ramo ("de qual obra?") às
    # ferramentas que só esta conta recebe.
    if slug == "construcao":
        from . import obras as _obras
        bloco_obras = _obras.bloco_persona(pool, conta_id)
        if bloco_obras:
            bloco_nicho = f"{bloco_nicho}\n{bloco_obras}" if bloco_nicho else bloco_obras
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
  EXCETO se o pagamento JÁ foi registrado (comprovante, foto, extrato): aí o
  dinheiro já está no caixa, e dar_baixa_titulo o lançaria DUAS vezes.

COMPROVANTE QUE QUITA CONTA:
- Quando o lancar_despesa/lancar_receita devolver "CONTA EM ABERTO QUE ESTE
  PAGAMENTO PODE QUITAR", faça a pergunta que ele pede NA MESMA resposta em que
  confirma o registro. Ex.: "Tem uma conta aberta que bate: Águas de Teresina,
  R$ 86,22, venceu 21/09 — tipo fixa. Esse pagamento quita ela?"
- SÓ com o "sim" dele chame quitar_conta_com_pagamento (titulo_id e
  lancamento_id da pergunta). Com duas ou mais contas, ele escolhe QUAL.
- "Não", silêncio ou outro assunto: não faça nada. A conta continua aberta e
  aparece na tela da Empresa pra ele fechar depois.
{linha_centros}
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
                             membro_id: int | None = None, livro=None) -> list[Ferramenta]:
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
        # LIGA ao cadastro da base pelo nome (contraparte), sem criar duplicado.
        # Assim o honorário/venda a prazo (a receber) ou a dívida (a pagar) aparece
        # na ficha certa. O papel filtra a busca — "a pagar" não deve casar com
        # alguém marcado só como cliente, e vice-versa (mesmo cadastro, papéis
        # diferentes: ver finance/clientes.eh_cliente/eh_fornecedor).
        cli_id = None
        if contraparte:
            try:
                from . import clientes as _cli
                papel = "cliente" if tipo == "receber" else "fornecedor"
                cli_id = _cli.achar_cliente_por_nome(pool, conta_id, contraparte, papel=papel)
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

    def quitar_conta_com_pagamento(e: dict) -> str:
        """Liga um pagamento QUE JÁ ESTÁ NO CAIXA a uma conta aberta, e a fecha.

        É a metade que faltava do comprovante (entrega 3, 23/09/2026): o
        comprovante vira lançamento sozinho, e a conta ficava aberta. A irmã
        `dar_baixa_titulo` NÃO serve aqui — ela lança o dinheiro de novo.

        Quem garante que o par é válido é `emp.conciliar_titulo`, com a mesma
        régua da tela: um id trocado pelo modelo esbarra lá, e a resposta diz por
        quê em vez de fechar a conta errada."""
        try:
            tid, lid = int(e.get("titulo_id")), int(e.get("lancamento_id"))
        except (TypeError, ValueError):
            return "Preciso do titulo_id e do lancamento_id que vieram na pergunta."
        r = emp.conciliar_titulo(pool, conta_id, tid, lid)
        if not r.get("ok"):
            return f"NÃO quitei: {r.get('erro')} A conta continua aberta."
        # O CENTRO que foi proposto na pergunta e o dono confirmou. Só preenche o
        # que o lançamento não tem — a mesma regra da conciliação pela tela.
        cen_txt = ""
        nome_centro = (e.get("centro_custo") or "").strip()
        if nome_centro:
            from . import plano_contas as _pc
            cid = _pc.centro_por_nome(pool, conta_id, nome_centro)
            if cid:
                with pool.connection() as c:
                    feito = c.execute(
                        """update lancamentos set centro_custo_id=%s
                            where id=%s and conta_id=%s and centro_custo_id is null
                        returning id""", (cid, lid, conta_id)).fetchone()
                    c.commit()
                if feito:
                    cen_txt = f" Centro de custo: {nome_centro}."
        # e o TIPO (325), pela mesma regra do vazio
        from .tipo_despesa import normalizar as _norm_tipo
        tipo_d = _norm_tipo(e.get("tipo_despesa"))
        if tipo_d:
            with pool.connection() as c:
                feito = c.execute(
                    """update lancamentos set tipo_despesa=%s
                        where id=%s and conta_id=%s and tipo_despesa is null
                    returning id""", (tipo_d, lid, conta_id)).fetchone()
                c.commit()
            if feito:
                cen_txt += f" Tipo: {tipo_d}."
        quando = r["pago_em"].strftime("%d/%m/%Y") if r.get("pago_em") else ""
        return (f"Conta quitada ✅ '{r['descricao']}' "
                f"({formatar_brl(r['valor_centavos'])}), paga em {quando}. O "
                "pagamento que já estava no caixa foi ligado a ela — nenhum "
                f"dinheiro novo foi lançado.{cen_txt}")

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
    ferramentas = [
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
            nome="quitar_conta_com_pagamento",
            descricao=("Fecha uma conta aberta LIGANDO a ela um pagamento que JÁ ESTÁ "
                       "no caixa (o comprovante que acabou de ser registrado). Use SÓ "
                       "depois de o lancar ter perguntado 'CONTA EM ABERTO QUE ESTE "
                       "PAGAMENTO PODE QUITAR' e de a pessoa CONFIRMAR. Não lança "
                       "dinheiro novo — ao contrário do dar_baixa_titulo."),
            parametros={
                "type": "object",
                "properties": {
                    "titulo_id": {"type": "integer", "description": "o titulo_id da pergunta"},
                    "lancamento_id": {"type": "integer", "description": "o lancamento_id da pergunta"},
                    "centro_custo": {"type": "string", "description": "o centro proposto na pergunta, ou outro que a pessoa disser; vazio se nenhum"},
                    "tipo_despesa": {"type": "string", "enum": ["fixa", "eventual", "investimento"], "description": "o tipo proposto na pergunta, ou outro que a pessoa disser; vazio se nenhum"},
                },
                "required": ["titulo_id", "lancamento_id"],
            },
            executar=quitar_conta_com_pagamento,
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
    # As ferramentas de OBRA são só da construtora (§6: o vocabulário de um ramo
    # nunca vaza pro outro). Numa clínica, "dividir_entre_obras" seria uma porta
    # aberta pra um erro que não tem como acontecer.
    if _nicho_da_conta(pool, conta_id) == "construcao":
        ferramentas += construir_ferramentas_obras(pool, conta_id, livro=livro,
                                                   membro_id=membro_id)
    return ferramentas


def construir_ferramentas_obras(pool, conta_id: int, livro=None,
                                membro_id: int | None = None) -> list[Ferramenta]:
    """consultar_obra, dividir_entre_obras, por_na_obra, marcar_etapa e
    gastos_sem_obra — o dia do encarregado (docs/mockups/nicho_construcao.html,
    seção 07). Nenhuma cria obra: obra nasce no painel (decisão 1 do dono)."""
    from . import obras as ob

    def _obra(ref) -> tuple[dict | None, str]:
        o = ob.obra_por_nome(pool, conta_id, ref)
        if o:
            return o, ""
        nomes = ", ".join(x["nome"] for x in ob.listar_obras(pool, conta_id, com_custos=False))
        if not nomes:
            return None, (f"Ainda não tem obra cadastrada. Quem cadastra é a empresa, "
                          f"no painel: {ob.LINK_OBRAS}")
        return None, f"Não achei a obra “{ref}”. As obras são: {nomes}. Qual delas?"

    def _com_caminho(o: dict) -> str:
        """O resumo da obra e, se for casa, o caminho do dinheiro dela."""
        sit = None
        if o["tipo"] == "casa":
            try:
                from . import obra_venda as ov
                sit = ov.situacao_da_casa(pool, conta_id, o)
            except Exception:  # noqa: BLE001 — sem a 353, só o custo
                sit = None
        txt = ob.resumo_da_obra(o, venda=sit["venda"] if sit else None)
        if sit:
            txt += " " + ov.resumo_caminho(o, sit)
        try:
            from . import obra_empreita as oe
            emp = oe.resumo(o, oe.situacao(pool, conta_id, o))
            if emp:
                txt += " " + emp
        except Exception:  # noqa: BLE001 — sem a 371
            pass
        return txt

    def consultar_obra(e: dict) -> str:
        ref = (e.get("obra") or "").strip()
        if ref:
            o, erro = _obra(ref)
            return _com_caminho(ob.obter_obra(pool, conta_id, o["id"])) if o else erro
        obras = ob.listar_obras(pool, conta_id)
        if not obras:
            return f"Ainda não tem obra cadastrada. Cadastro no painel: {ob.LINK_OBRAS}"
        partes = [_com_caminho(o) for o in obras]
        falta = ob.sem_obra(pool, conta_id, limite=0)
        if falta["n"]:
            partes.append(f"Sem obra: {falta['n']} despesa(s) de obra, "
                          f"{ob._brl(falta['total_centavos'])}.")
        return "\n".join(partes)

    def dividir_entre_obras(e: dict) -> str:
        try:
            lid = int(e.get("lancamento_id"))
        except (TypeError, ValueError):
            return "Preciso do id do lançamento que vai ser dividido."
        nomes = [n for n in (e.get("obras") or []) if str(n).strip()]
        if e.get("todas") or not nomes:
            alvo = [o for o in ob.listar_obras(pool, conta_id, com_custos=False)
                    if o["status"] == "em_obra"]
        else:
            alvo = []
            for n in nomes:
                o, erro = _obra(n)
                if not o:
                    return erro
                alvo.append(o)
        if len(alvo) < 2:
            return ("Pra dividir preciso de pelo menos duas obras em andamento. "
                    "De qual obra é esse gasto?")
        try:
            partes = ob.dividir(pool, conta_id, lid, [o["id"] for o in alvo])
        except ValueError as err:
            return f"Não dividi: {err}"
        return "Dividi: " + "; ".join(
            f"{ob._brl(p['valor_centavos'])} em {p['obra']}" for p in partes) + ". ✅"

    def por_na_obra(e: dict) -> str:
        try:
            lid = int(e.get("lancamento_id"))
        except (TypeError, ValueError):
            return "Preciso do id do lançamento."
        o, erro = _obra(e.get("obra"))
        if not o:
            return erro
        try:
            r = ob.por_na_obra(pool, conta_id, lid, o["id"])
        except ValueError as err:
            return f"Não mudei: {err}"
        return f"Pus {ob._brl(r['valor_centavos'])} na {r['obra']}. ✅"

    def marcar_etapa(e: dict) -> str:
        o, erro = _obra(e.get("obra"))
        if not o:
            return erro
        concluida = e.get("concluida")
        try:
            r = ob.marcar_etapa(pool, conta_id, o["id"], (e.get("etapa") or "").strip(),
                                concluida=True if concluida is None else bool(concluida))
        except ValueError as err:
            return str(err)
        verbo = "concluída" if r["concluida"] else "desmarcada"
        txt = f"{r['etapa']} {verbo} em {r['obra']}: a obra está em {r['pct']}%."
        if r["status"] == "pronta":
            txt += " Todas as etapas feitas — marquei a obra como PRONTA."
        # REFORMA: a etapa concluída libera a parcela ligada a ela (finance/obra_reforma)
        if r["concluida"] and o["tipo"] == "reforma":
            try:
                from . import obra_reforma as orf
                for p in orf.parcelas_liberadas(pool, conta_id, ob.obter_obra(pool, conta_id, o["id"])):
                    if p["etapa"] == r["etapa"]:
                        txt += (f" A parcela \"{p['rotulo']}\" ({ob._brl(p['valor_centavos'])}) "
                                "já pode ser cobrada do cliente — ofereça montar a cobrança "
                                "com o Pix (cobrar_parcela).")
            except Exception:  # noqa: BLE001 — sem a 355, sem parcela
                pass
        return txt

    def cobrar_parcela(e: dict) -> str:
        """A cobrança pronta das parcelas liberadas da reforma, pro dono encaminhar
        do WhatsApp dele (decisão do dono em 26/09/2026: o cliente recebe de um
        número que conhece, e o Pix é da própria empresa)."""
        o, erro = _obra(e.get("obra"))
        if not o:
            return erro
        if o["tipo"] != "reforma":
            return f"{o['nome']} é casa: a cobrança da casa é a entrada e o repasse da venda."
        from . import obra_reforma as orf
        try:
            cbs = orf.cobrancas(pool, conta_id, ob.obter_obra(pool, conta_id, o["id"]))
        except Exception:  # noqa: BLE001 — sem a 355/367
            cbs = []
        if not cbs:
            return (f"Nenhuma parcela liberada em aberto em {o['nome']}: ou a etapa ainda não "
                    "foi concluída, ou já foi paga.")
        sep = "\n---\n"
        aviso = "" if all(cb["pix"] for cb in cbs) else (
            " (sem Pix: a chave da empresa não está cadastrada — o dono cadastra na ficha da "
            f"obra, {ob.LINK_OBRAS})")
        return ("MENSAGEM PRONTA PRA ELE ENCAMINHAR AO CLIENTE — mande exatamente o texto "
                "entre as linhas, sem mudar nada, e diga que quando o cliente pagar é só "
                f"avisar que você dá baixa{aviso}:" + sep
                + sep.join(cb["mensagem"] for cb in cbs) + "\n---")

    def pagar_etapa(e: dict) -> str:
        """O pagamento do empreiteiro por etapa (finance/obra_empreita.py): o
        lançamento de mão de obra da obra passa a dizer QUE etapas ele fechou."""
        o, erro = _obra(e.get("obra"))
        if not o:
            return erro
        from . import obra_empreita as oe
        etapas = e.get("etapas") or []
        if isinstance(etapas, str):
            etapas = [x.strip() for x in etapas.replace(" e ", ",").split(",") if x.strip()]
        try:
            r = oe.pagar_etapas(pool, conta_id, o["id"], etapas,
                                lancamento_id=int(e.get("lancamento_id") or 0),
                                obs=(e.get("obs") or "").strip())
        except (ValueError, TypeError) as err:
            return str(err)
        partes = ", ".join(f"{x['nome'].lower()} ({ob._brl(x['valor_centavos'])})" for x in r["etapas"])
        txt = f"Marquei como PAGAS na {r['obra']}: {partes}."
        if r["adiantadas"]:
            txt += (" ⚠️ Ainda não estão concluídas: " + ", ".join(n.lower() for n in r["adiantadas"])
                    + " — foi adiantamento? Avise, uma vez, sem sermão.")
        if r["ja_pagas"]:
            txt += " ⚠️ Já tinha pagamento antes: " + "; ".join(r["ja_pagas"]) + \
                   ". Confirme se é parcela combinada ou pagamento em dobro."
        return txt

    def guardar_foto_da_obra(e: dict) -> str:
        """A foto que NÃO é nota (telhado, parede, piso pronto): guarda na obra e na
        etapa (finance/obra_fotos.py). A imagem é a da mensagem atual, que o webhook
        deixou em `livro.midia_atual`."""
        midia = getattr(livro, "midia_atual", None) if livro is not None else None
        if not midia:
            return ("Não chegou foto nesta mensagem. Peça pra ele mandar a foto de novo, "
                    "junto com a obra e a etapa.")
        o, erro = _obra(e.get("obra"))
        if not o:
            return erro
        from . import obra_fotos as of
        try:
            r = of.guardar(pool, conta_id, o["id"], midia[0], midia[1],
                           etapa=(e.get("etapa") or "").strip() or None,
                           legenda=(e.get("legenda") or "").strip(), origem="whatsapp",
                           membro_id=membro_id)
        except ValueError as err:
            return str(err)
        livro.midia_atual = None          # a mesma foto não entra duas vezes
        n = of.contagem(pool, conta_id, o["id"])
        onde = f"na etapa {r['etapa'].lower()}" if r["etapa"] else "sem etapa"
        return (f"Foto guardada na {r['obra']}, {onde} ({n} foto{'s' if n != 1 else ''} "
                "nessa obra). Ela aparece na ficha da obra, no painel. Se ele disse que a "
                "etapa terminou, marque com marcar_etapa também.")

    def marcar_documento(e: dict) -> str:
        from . import obra_venda as ov
        o, erro = _obra(e.get("obra"))
        if not o:
            return erro
        tipo = ov.achar_documento(e.get("documento"))
        if not tipo:
            nomes = ", ".join(n for _t, n in ov.DOCUMENTOS)
            return f"Não entendi qual papel. Os da casa são: {nomes}."
        status = (e.get("situacao") or "ok").strip()
        if status not in ov.STATUS_DOC:
            status = "ok"
        try:
            d = ov.marcar_documento(pool, conta_id, o["id"], tipo, status=status,
                                    emitido_em=_parse_data_pj(e["data"]) if e.get("data") else None)
        except ValueError as err:
            return str(err)
        sit = ov.situacao_da_casa(pool, conta_id, ob.obter_obra(pool, conta_id, o["id"]))
        txt = f"{d['nome']} da {o['nome']}: {ov.STATUS_DOC[d['status']].lower()}. ✅"
        if sit["trava"]:
            txt += f" Agora o que trava é {sit['trava']['nome'].lower()}."
        return txt

    def andar_venda(e: dict) -> str:
        from . import obra_venda as ov
        o, erro = _obra(e.get("obra"))
        if not o:
            return erro
        passo = ov.achar_passo(e.get("passo"))
        if not passo:
            return ("Não entendi o passo da venda. Pode ser: análise, aprovado, avaliação, "
                    "assinatura, registro, creditado ou desistiu.")
        try:
            r = ov.andar_venda(pool, conta_id, o["id"], passo,
                               _parse_data_pj(e["data"]) if e.get("data") else None)
        except ValueError as err:
            return str(err)
        txt = f"{r['obra']}: {r['rotulo'].lower()}. ✅"
        if r["titulos"]:
            txt += " Criei as contas a receber: " + " e ".join(r["titulos"]) + "."
        return txt

    def gastos_sem_obra(_e: dict) -> str:
        f = ob.sem_obra(pool, conta_id, limite=10)
        if not f["n"]:
            return "Nenhuma despesa de obra sem obra. ✅"
        linhas = [f"id={i['id']} · {i['data'].strftime('%d/%m')} · "
                  f"{ob._brl(i['valor_centavos'])} · {i['descricao'][:70]}"
                  for i in f["itens"]]
        mais = f" (mostrando {len(f['itens'])})" if f["n"] > len(f["itens"]) else ""
        return (f"{f['n']} despesa(s) de obra sem obra, {ob._brl(f['total_centavos'])}"
                f"{mais}:\n" + "\n".join(linhas))

    obra_s = {"type": "string", "description": "o nome da obra, como a pessoa falou (ex: Casa 2)"}
    return [
        Ferramenta(
            nome="consultar_obra",
            descricao=("Quanto já foi gasto numa obra (material, mão de obra, outros), "
                       "contra o previsto, e em que etapa ela está. Sem 'obra', resume "
                       "todas as obras abertas."),
            parametros={"type": "object", "properties": {"obra": obra_s}},
            executar=consultar_obra,
        ),
        Ferramenta(
            nome="dividir_entre_obras",
            descricao=("Divide um lançamento JÁ REGISTRADO em partes iguais entre obras "
                       "(a nota de material que é de várias casas). Use o lancamento_id "
                       "que o registro devolveu. 'todas' = todas as obras em andamento."),
            parametros={
                "type": "object",
                "properties": {
                    "lancamento_id": {"type": "integer"},
                    "obras": {"type": "array", "items": {"type": "string"},
                              "description": "os nomes das obras; vazio com todas=true"},
                    "todas": {"type": "boolean"},
                },
                "required": ["lancamento_id"],
            },
            executar=dividir_entre_obras,
        ),
        Ferramenta(
            nome="por_na_obra",
            descricao=("Põe um lançamento já registrado INTEIRO numa obra (desfaz divisão "
                       "anterior). Serve pra distribuir os gastos sem obra."),
            parametros={
                "type": "object",
                "properties": {"lancamento_id": {"type": "integer"}, "obra": obra_s},
                "required": ["lancamento_id", "obra"],
            },
            executar=por_na_obra,
        ),
        Ferramenta(
            nome="marcar_etapa",
            descricao=("Marca uma etapa da obra como concluída (ou desmarca, com "
                       "concluida=false). 'etapa' pode vir do jeito que a pessoa falou: "
                       "telhado, laje, reboco, piso, pintura."),
            parametros={
                "type": "object",
                "properties": {"obra": obra_s,
                               "etapa": {"type": "string"},
                               "concluida": {"type": "boolean"}},
                "required": ["obra", "etapa"],
            },
            executar=marcar_etapa,
        ),
        Ferramenta(
            nome="marcar_documento",
            descricao=("Marca um papel da CASA: alvará, ART/RRT, CNO, habite-se, CND da "
                       "obra, averbação, matrícula ou certidões da empresa. 'documento' "
                       "pode vir do jeito que a pessoa falou (\"saiu o habite-se\")."),
            parametros={
                "type": "object",
                "properties": {"obra": obra_s,
                               "documento": {"type": "string"},
                               "situacao": {"type": "string",
                                            "enum": ["ok", "pendente", "nao_se_aplica"]},
                               "data": {"type": "string", "description": "dd/mm/aaaa; vazio = hoje"}},
                "required": ["obra", "documento"],
            },
            executar=marcar_documento,
        ),
        Ferramenta(
            nome="andar_venda",
            descricao=("Anda a venda da CASA um passo: análise, aprovado, avaliação, "
                       "assinatura, registro, creditado (o dinheiro caiu) ou desistiu. Na "
                       "assinatura nascem as contas a receber da entrada e do repasse da "
                       "Caixa. A venda (comprador e valores) tem que estar cadastrada."),
            parametros={
                "type": "object",
                "properties": {"obra": obra_s,
                               "passo": {"type": "string"},
                               "data": {"type": "string", "description": "dd/mm/aaaa; vazio = hoje"}},
                "required": ["obra", "passo"],
            },
            executar=andar_venda,
        ),
        Ferramenta(
            nome="cobrar_parcela",
            descricao=("Monta a mensagem de cobrança das parcelas da REFORMA que a etapa "
                       "concluída liberou (com o Pix copia e cola da própria empresa), "
                       "pra pessoa encaminhar ao cliente pelo WhatsApp dela. Quando o "
                       "cliente pagar, a baixa é pelo dar_baixa_titulo."),
            parametros={"type": "object", "properties": {"obra": obra_s},
                        "required": ["obra"]},
            executar=cobrar_parcela,
        ),
        Ferramenta(
            nome="pagar_etapa",
            descricao=("Marca que um pagamento de MÃO DE OBRA (empreiteiro, pedreiro) fechou "
                       "certas etapas da obra. Primeiro o pagamento tem que estar LANÇADO na "
                       "obra (lancar_despesa com centro_custo = a obra, ou por_na_obra); aqui "
                       "vai o id desse lançamento e as etapas como ele falou (\"fundação e "
                       "estrutura\", \"até a laje\"). Avisa etapa paga antes de ficar pronta "
                       "e etapa paga duas vezes."),
            parametros={"type": "object",
                        "properties": {"obra": obra_s,
                                       "etapas": {"type": "array", "items": {"type": "string"}},
                                       "lancamento_id": {"type": "integer"},
                                       "obs": {"type": "string"}},
                        "required": ["obra", "etapas", "lancamento_id"]},
            executar=pagar_etapa,
        ),
        Ferramenta(
            nome="guardar_foto_da_obra",
            descricao=("Guarda a FOTO DA OBRA que veio nesta mensagem (telhado, parede, piso, "
                       "obra pronta — foto que NÃO é nota nem comprovante) na obra e, se "
                       "souber, na etapa. Não lança nada no caixa. Pergunte de qual obra se "
                       "ele não disse."),
            parametros={"type": "object",
                        "properties": {"obra": obra_s,
                                       "etapa": {"type": "string",
                                                 "description": "a etapa como ele falou (ex: telhado); vazio = sem etapa"},
                                       "legenda": {"type": "string"}},
                        "required": ["obra"]},
            executar=guardar_foto_da_obra,
        ),
        Ferramenta(
            nome="gastos_sem_obra",
            descricao=("Lista as despesas de obra da empresa que ainda não têm obra "
                       "(com o id de cada uma), pra distribuir com por_na_obra ou "
                       "dividir_entre_obras."),
            parametros={"type": "object", "properties": {}},
            executar=gastos_sem_obra,
        ),
    ]
