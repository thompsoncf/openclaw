"""Persona e ferramentas PJ do bot (Módulo Empresa) — Leva 2C.

Plugado SÓ quando a conta é PJ com o módulo ativo. Não altera a persona PF
nem o núcleo do agente: apenas ADICIONA um bloco ao prompt e ferramentas à
lista. As ferramentas executam de verdade (o dono autorizou execução direta).
"""
from __future__ import annotations

import re
from datetime import date, datetime

from finance import relogio

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
    hoje = relogio.hoje().strftime("%d/%m/%Y")
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
        return relogio.hoje()
    for fmt in ("%d/%m/%Y", "%d/%m/%y", "%Y-%m-%d"):
        try:
            return datetime.strptime(s, fmt).date()
        except ValueError:
            continue
    # "dia 15" / "15"
    dig = "".join(ch for ch in s if ch.isdigit())
    if dig:
        hoje = relogio.hoje()
        dia = min(int(dig[:2]), 28)
        venc = date(hoje.year, hoje.month, dia)
        if venc < hoje:  # "dia 15" que já passou → rola pro mês seguinte
            ano, mes = (hoje.year + 1, 1) if hoje.month == 12 else (hoje.year, hoje.month + 1)
            venc = date(ano, mes, dia)
        return venc
    return relogio.hoje()


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
        hoje = relogio.hoje()
        folha = emp.folha_do_mes(pool, conta_id, hoje.year, hoje.month)
        item = next((i for i in folha["itens"] if i["id"] == f["id"]), None)
        resta = formatar_brl(item["a_pagar_centavos"]) if item else "?"
        return (f"Vale de {formatar_brl(cent)} pro {f['nome']} registrado "
                f"(saiu do caixa como Pessoal). Ele fica com {resta} a receber "
                f"na folha de {hoje.month:02d}/{hoje.year}.")

    def consultar_empresa(e: dict) -> str:
        hoje = relogio.hoje()
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
        from .livro_caixa import LivroCaixa
        hoje = relogio.hoje()
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
    # As ferramentas de ESTANDE são só de 'eventos' E com a feature LIGADA
    # (evento_stands_config, migração 448) — nem toda conta de eventos vende
    # espaço numerado (Prime Eventos, conta 34, não vende), e sem essa segunda
    # checagem o agente ofereceria "confirmar comprovante de estande" pra quem
    # nunca ouviu falar de estande.
    if _nicho_da_conta(pool, conta_id) == "eventos":
        try:
            from . import evento_stands as _es
            if _es.obter_config(pool, conta_id):
                ferramentas += construir_ferramentas_evento_stands(
                    pool, conta_id, livro=livro, membro_id=membro_id)
        except Exception:
            pass  # feature nova: nunca derruba o agente de quem não usa
    return ferramentas


def construir_ferramentas_obras(pool, conta_id: int, livro=None,
                                membro_id: int | None = None) -> list[Ferramenta]:
    """consultar_obra, dividir_entre_obras, por_na_obra, marcar_etapa e
    gastos_sem_obra — o dia do encarregado (docs/mockups/nicho_construcao.html,
    seção 07). Nenhuma cria obra: obra nasce no painel (decisão 1 do dono).

    E o MATERIAL (docs/mockups/obras_mapa_3d.html, seção 3): o gancho
    `livro.apos_itens` faz os itens da nota virarem quantidade na obra, e
    apontar_material/consultar_material são o dia a dia falado."""
    from . import obra_material as omat
    from . import obras as ob
    if livro is not None:
        livro.apos_itens = lambda lanc_id: omat.absorver_lancamento(pool, conta_id, lanc_id)

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
        try:
            from . import obra_grupos as og
            o = og.com_comum(pool, conta_id, o)        # o custo cheio: o lançado + a parte do comum
        except Exception:  # noqa: BLE001 — sem a 478
            pass
        txt = ob.resumo_da_obra(o, venda=sit["venda"] if sit else None)
        if (o.get("custos") or {}).get("comum"):
            txt += f" Inclui {ob._brl(o['custos']['comum'])} do custo comum da quadra (pelo m²)."
        if sit:
            txt += " " + ov.resumo_caminho(o, sit)
        try:
            from . import obra_empreita as oe
            emp = oe.resumo(o, oe.situacao(pool, conta_id, o))
            if emp:
                txt += " " + emp
        except Exception:  # noqa: BLE001 — sem a 371
            pass
        try:
            from . import sinapi as _sin
            ref = _sin.referencia(pool, _sin.uf_da_conta(pool, conta_id))
            cmp = _sin.comparar(o, ref)
            if ref and cmp:
                txt += (f" Referência SINAPI-{ref['uf']} {ref['rotulo_mes']}: "
                        f"{ob._brl(ref['total_centavos'])}/m² (pra comparar, não pra cobrar)")
                if cmp["base"]:
                    txt += (f"; {'a obra saiu' if cmp['base'] == 'gasto' else 'o previsto dá'} "
                            f"{ob._brl(cmp['valor'])}/m² ({cmp['pct']:+d}%)")
                txt += "."
        except Exception:  # noqa: BLE001 — sem a 377
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
        por = "m2" if (e.get("por") == "m2" or e.get("quadra")) else "igual"
        if (e.get("quadra") or "").strip():
            from . import obra_grupos as og
            g = og.grupo_por_nome(pool, conta_id, e["quadra"])
            if not g:
                return _sem_quadra(e["quadra"])
            alvo = [o for o in og.casas(pool, conta_id, g["id"]) if o["status"] != "arquivada"]
        elif e.get("todas") or not nomes:
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
            partes = ob.dividir(pool, conta_id, lid, [o["id"] for o in alvo], por=por)
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
        from . import obra_empreita as oe
        etapas = e.get("etapas") or []
        if isinstance(etapas, str):
            etapas = [x.strip() for x in etapas.replace(" e ", ",").split(",") if x.strip()]
        if (e.get("quadra") or "").strip():
            return _pagar_etapa_quadra(e, etapas)
        o, erro = _obra(e.get("obra"))
        if not o:
            return erro
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

    def _sem_quadra(ref) -> str:
        from . import obra_grupos as og
        nomes = ", ".join(g["nome"] for g in og.listar_grupos(pool, conta_id))
        if not nomes:
            return (f"Ainda não tem {og.rotulo(pool, conta_id).lower()} cadastrada. Quem cadastra "
                    f"é a empresa, no painel: {ob.LINK_OBRAS}")
        return f"Não achei “{ref}”. As que existem: {nomes}. Qual delas?"

    def _pagar_etapa_quadra(e: dict, etapas: list) -> str:
        """"Paguei 36 mil pro empreiteiro, fundação da quadra 4": divide o lançamento
        entre as casas da quadra pelo m² e marca as etapas pagas em cada uma."""
        from . import obra_empreita as oe
        from . import obra_grupos as og
        g = og.grupo_por_nome(pool, conta_id, e.get("quadra"))
        if not g:
            return _sem_quadra(e.get("quadra"))
        alvo = [o for o in og.casas(pool, conta_id, g["id"]) if o["status"] != "arquivada"]
        if not alvo:
            return f"{g['nome']} ainda não tem casas."
        try:
            lid = int(e.get("lancamento_id") or 0)
            if len(alvo) > 1:
                ob.dividir(pool, conta_id, lid, [o["id"] for o in alvo], por="m2")
            else:
                ob.por_na_obra(pool, conta_id, lid, alvo[0]["id"])
        except (ValueError, TypeError) as err:
            return str(err)
        pagas, adiant, erros = 0, [], []
        for o in alvo:
            try:
                r = oe.pagar_etapas(pool, conta_id, o["id"], etapas, lancamento_id=lid,
                                    obs=(e.get("obs") or "").strip())
                pagas += 1
                if r["adiantadas"]:
                    adiant.append(f"{o['nome']} ({', '.join(n.lower() for n in r['adiantadas'])})")
            except ValueError as err:
                erros.append(f"{o['nome']}: {err}")
        txt = (f"Dividi o pagamento entre as {len(alvo)} casas de {g['nome']} pelo m² e marquei "
               f"as etapas como PAGAS em {pagas} delas.")
        if adiant:
            txt += (" ⚠️ Ainda não estão concluídas em: " + "; ".join(adiant)
                    + " — foi adiantamento? Avise, uma vez, sem sermão.")
        if erros:
            txt += " Não marquei em: " + "; ".join(erros) + "."
        return txt

    def marcar_etapa_quadra(e: dict) -> str:
        """"Terminei a fundação da quadra 5": marca nas casas que começaram (decisão
        4 do dono: na hora, dizendo quem ficou de fora; "desfaz" volta)."""
        from . import obra_grupos as og
        g = og.grupo_por_nome(pool, conta_id, e.get("quadra"))
        if not g:
            return _sem_quadra(e.get("quadra"))
        ids = None
        lotes = [str(x).strip() for x in (e.get("lotes") or []) if str(x).strip()]
        if lotes:
            casas = og.casas(pool, conta_id, g["id"])
            querer = {og._numero(x) or ob._norm(x) for x in lotes}
            ids = [o["id"] for o in casas
                   if (og._numero(o.get("lote") or "") or ob._norm(o.get("lote") or o["nome"])) in querer]
            if not ids:
                return f"Não achei esses lotes em {g['nome']}."
        try:
            r = og.marcar_etapa_grupo(pool, conta_id, g["id"], (e.get("etapa") or "").strip(),
                                      obra_ids=ids)
        except ValueError as err:
            return str(err)
        if not r["marcadas"]:
            txt = f"Nada a marcar: {r['etapa'].lower()} já estava feita nas casas de {g['nome']}."
        else:
            txt = (f"Marquei {r['etapa'].lower()} em {len(r['marcadas'])} casa(s) de {g['nome']} "
                   f"({', '.join(r['marcadas'])}). {g['nome']} está em {r['pct']}%.")
        if r["fora"]:
            txt += f" Ficaram de fora porque ainda não começaram: {', '.join(r['fora'])}."
        if r["sem_etapa"]:
            txt += f" Sem essa etapa: {', '.join(r['sem_etapa'])}."
        return txt + ' Se errou, é só dizer "desfaz".'

    def por_na_quadra(e: dict) -> str:
        """"Paguei 8 mil da terraplanagem da quadra 4": o custo comum da quadra."""
        from . import obra_grupos as og
        g = og.grupo_por_nome(pool, conta_id, e.get("quadra"))
        if not g:
            return _sem_quadra(e.get("quadra"))
        try:
            r = og.por_na_quadra(pool, conta_id, int(e.get("lancamento_id") or 0), g["id"])
        except (ValueError, TypeError) as err:
            return str(err)
        return (f"Lancei {ob._brl(r['valor_centavos'])} como CUSTO COMUM de {r['quadra']}. Ele entra "
                "no custo de cada casa pelo m².")

    def desfazer_etapa_quadra(e: dict) -> str:
        from . import obra_grupos as og
        g = og.grupo_por_nome(pool, conta_id, e.get("quadra"))
        if not g:
            return _sem_quadra(e.get("quadra"))
        r = og.desfazer_ultima(pool, conta_id, g["id"])
        if not r:
            return f"Não tem marcação em lote pra desfazer em {g['nome']}."
        return (f"Desfeito: {r['etapa'].lower()} voltou a ficar em aberto em "
                f"{len(r['voltaram'])} casa(s) de {g['nome']}.")

    def oferecer_escolha(e: dict) -> str:
        """"De qual obra?" e "que etapa ficou pronta?" com toque (finance/escolhas.py,
        pedido do dono em 02/10/2026)."""
        from . import escolhas as esc
        if (e.get("tipo") or "obra") == "etapa":
            o, erro = _obra(e.get("obra"))
            if not o:
                return erro
            escolha = esc.de_etapa(pool, conta_id, ob.obter_obra(pool, conta_id, o["id"]))
            if not escolha:
                return f"Todas as etapas de {o['nome']} já estão feitas."
        else:
            escolha = esc.de_obra(pool, conta_id)
            if not escolha:
                return (f"Ainda não tem obra cadastrada. Quem cadastra é a empresa, no painel: "
                        f"{ob.LINK_OBRAS}")
        titulos = ", ".join(op["titulo"] for op in escolha["opcoes"])
        if livro is not None and getattr(livro, "canal_interativo", False):
            livro.escolha = escolha
            return (f"Os botões vão logo depois da sua resposta, com: {titulos}. Escreva SÓ a "
                    "pergunta curta (\"É de qual obra?\" / \"Qual etapa ficou pronta?\"), sem "
                    "listar as opções. O toque chega como o nome da opção.")
        return "Liste as opções numeradas pra pessoa responder:\n" + esc.texto_das_opcoes(escolha)

    # ── o material (docs/mockups/obras_mapa_3d.html, seção 3) ─────────────
    def _destinos_do_material(ref: str) -> tuple[list[dict], str]:
        """A obra dita — ou a QUADRA inteira, que divide entre as casas em obra
        que começaram (a mesma regra da marcação em lote).

        A quadra só entra quando a pessoa FALOU de quadra ("quadra 5", "Q5", o
        nome do grupo): "casa 5" ambígua não pode cair na Quadra 5 pelo número
        e espalhar material por casas erradas — aí a resposta é perguntar."""
        o = ob.obra_por_nome(pool, conta_id, ref)
        if o:
            return [o], ""
        try:
            from . import obra_grupos as og
            alvo = ob._norm(ref)
            rot = ob._norm(og.rotulo(pool, conta_id))
            falou_grupo = bool(re.search(r"\b(quadra|setor|bloco)\b", alvo)
                               or (rot and re.search(rf"\b{re.escape(rot)}\b", alvo))
                               or re.fullmatch(r"q\s*\d+", alvo)
                               or any(ob._norm(g["nome"]) == alvo
                                      for g in og.listar_grupos(pool, conta_id)))
            g = og.grupo_por_nome(pool, conta_id, ref) if falou_grupo else None
            if g:
                casas = og.casas(pool, conta_id, g["id"])
                if not casas:
                    return [], f"A {g['nome']} ainda não tem casas."
                em_obra = [x for x in casas if x["pct"] < 100
                           and x["status"] not in ("pronta", "vendida", "entregue", "arquivada")]
                comecaram = [x for x in em_obra if og.comecou(x)]
                if not comecaram:
                    return [], (f"Nenhuma casa da {g['nome']} está em obra agora — diga a "
                                "casa, ou deixe no depósito.")
                return comecaram, ""
        except Exception:  # noqa: BLE001 — sem a 478
            pass
        _, erro = _obra(ref)
        return [], erro

    def apontar_material(e: dict) -> str:
        """'usei 15 sacos na casa 2' / 'levei 10 do depósito pra quadra 5' /
        'chegou 60 sacos'. Apontar é OPCIONAL (decisão 2 do dono): quem aponta
        ganha o saldo fino; quem não aponta já tem a comparação entre as irmãs."""
        from decimal import Decimal
        acao = (e.get("acao") or "").strip()
        if acao not in ("usei", "levei", "chegou"):
            return "Diga a ação: usei, levei (do depósito pra obra) ou chegou."
        try:
            q = Decimal(str(e.get("quantidade") or 0).replace(",", "."))
        except Exception:  # noqa: BLE001
            q = Decimal(0)
        if q <= 0:
            return "Quantas unidades? Preciso do número."
        destinos: list[dict] = []
        if (e.get("obra") or "").strip():
            destinos, erro = _destinos_do_material(e["obra"].strip())
            if erro:
                return erro
        if acao in ("usei", "levei") and not destinos:
            return "De qual obra? (pode ser a quadra inteira também)"
        ref = (e.get("material") or "").strip()
        p = omat.achar_produto(pool, conta_id, ref)
        if p is not None and "ambiguo" in p:
            return f"Qual deles? {' · '.join(p['ambiguo'])}. Não registrei nada ainda."
        if p is None:
            if acao == "chegou" and ref:
                with pool.connection() as c:
                    pid, nome, un = omat._achar_ou_criar(c, conta_id, ref, e.get("unidade") or "")
                    c.commit()
                p = {"id": pid, "nome": nome, "unidade": un}
            else:
                tem = [r for r in omat.deposito(pool, conta_id) if r["saldo"] > 0]
                nomes = ", ".join(r["nome"] for r in tem[:8])
                return (f"Não conheço o material “{ref}”." +
                        (f" No depósito tem: {nomes}." if nomes else
                         " Ainda não entrou material — mande a foto da nota que eu guardo os itens."))
        avisos, partes = [], []
        try:
            if not destinos:                      # chegou, sem obra: o depósito
                r = omat.mover(pool, conta_id, acao="chegou", produto_id=p["id"], quantidade=q)
                frase = (f"Chegou: {omat.rotulo(q, p['unidade'])} de {p['nome']} no depósito "
                         f"(agora {omat.rotulo(r['deposito'], p['unidade'])}).")
            else:
                # a quadra divide igual; os milésimos que sobram ficam na primeira
                cota = (q / len(destinos)).quantize(Decimal("0.001"))
                quotas = [cota] * len(destinos)
                quotas[0] += q - sum(quotas)
                r = None
                for o, qi in zip(destinos, quotas):
                    r = omat.mover(pool, conta_id, acao=acao, produto_id=p["id"],
                                   quantidade=qi, obra_id=o["id"])
                    partes.append(f"{o['nome']} ({omat.rotulo(qi, p['unidade'])})")
                    if r["furo"]:
                        avisos.append(f"uso maior que entrada em {o['nome']} — confere se faltou nota")
                verbo = {"usei": "usados em", "levei": "levados do depósito pra",
                         "chegou": "recebidos em"}[acao]
                frase = (f"Apontei: {omat.rotulo(q, p['unidade'])} de {p['nome']} {verbo} "
                         + (destinos[0]["nome"] if len(destinos) == 1 else
                            f"{len(destinos)} casas — " + ", ".join(partes)) + ".")
                if len(destinos) == 1 and acao != "levei":
                    frase += f" Na obra ficam {omat.rotulo(r['na_obra'], p['unidade'])}."
                if acao == "levei":
                    frase += f" No depósito ficam {omat.rotulo(r['deposito'], p['unidade'])}."
            if r and r.get("abaixo_minimo"):
                avisos.append(f"{p['nome']} abaixo do mínimo no depósito")
        except ValueError as err:
            return str(err)
        return frase + ("".join(f" ⚠️ {a.capitalize()}." for a in avisos))

    def consultar_material(e: dict) -> str:
        """'quanto cimento tem na casa 3?' / 'como está o depósito?'. A tabela do
        mockup: entrou / usado / no local — e os alertas de graça (furo, mínimo,
        irmãs da quadra)."""
        ref_obra = (e.get("obra") or "").strip()
        ref_mat = (e.get("material") or "").strip()
        if ref_obra:
            o, erro = _obra(ref_obra)
            if not o:
                return erro
            linhas = omat.quadro_da_obra(pool, conta_id, o["id"])
            titulo = f"Material de {o['nome']}:"
        else:
            linhas = omat.deposito(pool, conta_id)
            titulo = "No depósito:"
        if ref_mat:
            alvo = ob._norm(ref_mat)
            linhas = [r for r in linhas if alvo in ob._norm(r["nome"])]
        if not linhas:
            onde = f"em {o['nome']}" if ref_obra else "no depósito"
            return (f"Não tem material registrado {onde}. A foto da nota já guarda os "
                    "itens sozinha; material que chegou SEM nota entra com 'chegou 60 "
                    "sacos de cimento'.")
        corpo = "\n".join(
            f"• {r['nome']}: entrou {omat.rotulo(r['entrou'], r['unidade'])}, "
            f"usados {omat._qtd(r['usado'])}, "
            + ("no depósito " if not ref_obra else "na obra ")
            + omat.rotulo(r["saldo"], r["unidade"])
            + (" ⚠️ abaixo do mínimo" if r.get("abaixo") else "")
            for r in linhas[:12])
        extras = [f"⚠️ {f}" for f in omat.furos(linhas)]
        if ref_obra:
            alerta = omat.alerta_irmas(pool, conta_id, o)
            if alerta:
                extras.append(f"⚠️ {alerta}")
        return titulo + "\n" + corpo + ("\n" + "\n".join(extras) if extras else "")

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
            descricao=("Divide um lançamento JÁ REGISTRADO entre obras (a nota de material "
                       "que é de várias casas). Use o lancamento_id que o registro devolveu. "
                       "'todas' = todas as obras em andamento; 'quadra' = as casas daquela "
                       "quadra, pelo m²."),
            parametros={
                "type": "object",
                "properties": {
                    "lancamento_id": {"type": "integer"},
                    "obras": {"type": "array", "items": {"type": "string"},
                              "description": "os nomes das obras; vazio com todas=true"},
                    "todas": {"type": "boolean"},
                    "quadra": {"type": "string",
                               "description": "a quadra/setor como a pessoa falou (ex: quadra 5): divide entre as casas dela pelo m²"},
                    "por": {"type": "string", "enum": ["igual", "m2"],
                            "description": "igual (padrão) ou pelo m² de cada obra"},
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
                                       "quadra": {"type": "string",
                                                  "description": "no lugar da obra: o pagamento é da quadra inteira (divide pelo m²)"},
                                       "etapas": {"type": "array", "items": {"type": "string"}},
                                       "lancamento_id": {"type": "integer"},
                                       "obs": {"type": "string"}},
                        "required": ["etapas", "lancamento_id"]},
            executar=pagar_etapa,
        ),
        Ferramenta(
            nome="marcar_etapa_quadra",
            descricao=("Marca uma etapa em TODAS as casas de uma quadra/setor de uma vez "
                       "(\"terminei a fundação da quadra 5\"). Só entram as casas que já "
                       "começaram, salvo se ele disser os lotes. Diga quem ficou de fora."),
            parametros={"type": "object",
                        "properties": {"quadra": {"type": "string"},
                                       "etapa": {"type": "string"},
                                       "lotes": {"type": "array", "items": {"type": "string"},
                                                 "description": "só estes lotes (ex: [\"1\", \"2\"]); vazio = as que começaram"}},
                        "required": ["quadra", "etapa"]},
            executar=marcar_etapa_quadra,
        ),
        Ferramenta(
            nome="por_na_quadra",
            descricao=("Põe um lançamento JÁ REGISTRADO no CUSTO COMUM de uma quadra: o que é "
                       "de todas as casas e de nenhuma (terraplanagem, rede de água e esgoto, "
                       "poste, muro da quadra). Entra no custo de cada casa pelo m². Não use pra "
                       "material que vai pra casas — isso é dividir_entre_obras com quadra."),
            parametros={"type": "object",
                        "properties": {"lancamento_id": {"type": "integer"},
                                       "quadra": {"type": "string"}},
                        "required": ["lancamento_id", "quadra"]},
            executar=por_na_quadra,
        ),
        Ferramenta(
            nome="desfazer_etapa_quadra",
            descricao="Desfaz a ÚLTIMA marcação de etapa em lote daquela quadra (\"desfaz\").",
            parametros={"type": "object", "properties": {"quadra": {"type": "string"}},
                        "required": ["quadra"]},
            executar=desfazer_etapa_quadra,
        ),
        Ferramenta(
            nome="oferecer_escolha",
            descricao=("Mostra BOTÕES pra pessoa escolher, em vez de perguntar por texto: "
                       "tipo 'obra' (de qual obra é a despesa) ou tipo 'etapa' (que etapa "
                       "ficou pronta numa obra — precisa da obra). Use sempre que for "
                       "perguntar uma dessas duas coisas."),
            parametros={"type": "object",
                        "properties": {"tipo": {"type": "string", "enum": ["obra", "etapa"]},
                                       "obra": {"type": "string", "description": "pra tipo etapa"}},
                        "required": ["tipo"]},
            executar=oferecer_escolha,
        ),
        Ferramenta(
            nome="apontar_material",
            descricao=("Registra MATERIAL em quantidade (não mexe em dinheiro): acao 'usei' "
                       "(consumiu na obra), 'levei' (do depósito pra obra) ou 'chegou' "
                       "(entrou no depósito, ou na obra se dita). 'chegou' é SÓ pra material "
                       "SEM NOTA (sobra de outra obra, doação, compra sem nota): a foto da "
                       "nota já dá entrada sozinha, e apontar as duas coisas conta em dobro. "
                       "Em 'obra' também vale a QUADRA — divide entre as casas em obra. "
                       "Apontar é opcional: use quando a pessoa disser, nunca cobre."),
            parametros={"type": "object",
                        "properties": {"acao": {"type": "string",
                                                "enum": ["usei", "levei", "chegou"]},
                                       "material": {"type": "string",
                                                    "description": "ex: cimento, ferro 8mm"},
                                       "quantidade": {"type": "number"},
                                       "unidade": {"type": "string",
                                                   "description": "saco, m³, barra… (se disse)"},
                                       "obra": {"type": "string",
                                                "description": "a obra ou a quadra, como falou"}},
                        "required": ["acao", "material", "quantidade"]},
            executar=apontar_material,
        ),
        Ferramenta(
            nome="consultar_material",
            descricao=("Quanto material tem: na obra ('quanto cimento tem na casa 3?') ou no "
                       "depósito (sem obra). Mostra entrou/usado/saldo e os alertas (uso maior "
                       "que entrada, mínimo do depósito, consumo acima das casas irmãs)."),
            parametros={"type": "object",
                        "properties": {"material": {"type": "string"},
                                       "obra": {"type": "string"}}},
            executar=consultar_material,
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


# ─────────────────────────────────────────────────────────────────────────
# Estandes de feira/evento (migração 448, finance/evento_stands.py)
# ─────────────────────────────────────────────────────────────────────────
def construir_ferramentas_evento_stands(pool, conta_id: int, livro=None,
                                        membro_id: int | None = None) -> list[Ferramenta]:
    """Ferramenta do agente pro comprovante do estande chegando PELO WHATSAPP —
    o mesmo evento que a página pública trata em web/loja_stands.py, só que o
    cliente manda a foto direto na conversa em vez de usar o formulário.

    O MECANISMO é IDÊNTICO ao de `guardar_foto_da_obra` (acima, linha ~930): o
    webhook (web/app.py) deixa a mídia da mensagem atual em `livro.midia_atual`
    — (bytes, content_type) — antes de chamar o agente; a ferramenta lê de lá
    porque não existe outro jeito de saber qual foto veio em QUAL mensagem (o
    modelo não recebe o arquivo bruto, só uma descrição em texto).

    # TODO(zaq-tool): `livro.midia_atual` só é preenchido pro WhatsApp quando
    # `media_type` começa com 'image/' (web/app.py, bloco do webhook que lê
    # `media_url`/`media_ctype` — ver a condição logo antes de `agente.responder`).
    # Comprovante em FOTO (o caso comum: print do Pix) funciona; comprovante em
    # PDF mandado pelo WhatsApp NÃO populará `midia_atual` hoje, e esta
    # ferramenta vai responder "não chegou nenhuma foto/PDF" mesmo com o PDF
    # tendo chegado. Não ampliei a condição do webhook pra incluir PDF aqui
    # porque ela também decide o fluxo de LEITURA DE APÓLICE (outro PDF, outro
    # destino — ver o comentário "TERCEIRA PORTA DO LEITOR DE APÓLICE" em
    # web/app.py) e um PDF pode servir aos dois; misturar os dois sem entender
    # a prioridade entre eles é exatamente o tipo de "integração arriscada"
    # que não dá pra inventar sem o dono decidir qual vem primeiro. Página
    # pública (web/loja_stands.py) já aceita PDF sem essa limitação — é o
    # caminho que funciona hoje pra quem tem só o PDF."""

    def confirmar_comprovante_stand(e: dict) -> str:
        from . import evento_stands as _es
        midia = getattr(livro, "midia_atual", None) if livro is not None else None
        if not midia:
            return ("Não chegou nenhuma foto/PDF nesta mensagem. Peça pro cliente "
                    "mandar o comprovante do Pix de novo, junto com o código do estande.")
        codigo = (e.get("codigo") or "").strip()
        if not codigo:
            return "Preciso do código do estande (ex: G58) pra saber qual reservar."
        stand = _es.buscar(pool, conta_id, codigo)
        if not stand:
            return (f"Não achei o estande '{codigo}'. Confere o código com o cliente — "
                    "ele está escrito na planta que ele estava olhando.")
        r = _es.subir_e_registrar_comprovante(pool, conta_id, codigo, midia[0], midia[1])
        if not r["ok"]:
            return f"Não deu pra registrar: {r['erro']}"
        livro.midia_atual = None          # o mesmo comprovante não entra duas vezes
        s = r["stand"]
        preco = f" ({formatar_brl(s['preco_centavos'])})" if s["preco_centavos"] else ""
        return (f"Comprovante do estande {codigo}{preco} registrado. ✅ Ele fica reservado "
                "esperando a equipe conferir e confirmar o pagamento no painel.")

    return [
        Ferramenta(
            nome="confirmar_comprovante_stand",
            descricao=("Registra o COMPROVANTE DE PAGAMENTO de um estande de feira que "
                       "veio nesta mensagem em FOTO (print do Pix) — reserva o estande "
                       "esperando a equipe confirmar. Use quando o cliente mandar a foto "
                       "do comprovante pelo WhatsApp em vez de usar a página do estande. "
                       "Se o cliente mandar em PDF, isso ainda não é lido por aqui — peça "
                       "pra ele usar a página pública do estande ou mandar um print/foto. "
                       "Pergunte o código do estande (ex: G58) se ele não disse."),
            parametros={"type": "object",
                        "properties": {"codigo": {"type": "string",
                                                  "description": "código do estande, ex: G58"}},
                        "required": ["codigo"]},
            executar=confirmar_comprovante_stand,
        ),
    ]
