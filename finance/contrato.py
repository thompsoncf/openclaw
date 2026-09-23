"""Contrato de locação de espaço — o modelo que a empresa escreve e o documento
que sai dele.

O PROBLEMA QUE ESTE MÓDULO EXISTE PARA MATAR

Contrato e catálogo guardavam cópias próprias dos mesmos números, mantidas por
mãos diferentes. Medido no contrato vigente da Prime Eventos contra o catálogo
dela, em 16/08/2026:

    hora extra       contrato R$ 600,00/h    catálogo R$ 620,00
    taxa de limpeza  contrato R$ 600,00      catálogo R$ 400,00

E o estrago é real: em 15/08 o agente cotou "R$ 620 por hora" a um cliente que,
fechando, assinaria um contrato dizendo R$ 600. Na limpeza a proposta promete
R$ 400 e o contrato cobra R$ 600 — R$ 200 de discussão na entrega.

A saída não é "conferir com cuidado", é o contrato PARAR de ter números próprios.
As cláusulas guardam CAMPOS e o texto final é montado na hora:

    "Taxa de Utilização Excedente de {preco.hora-extra} por hora"
                          ↓ preencher()
    "Taxa de Utilização Excedente de R$ 620,00 por hora"

Corrigir o preço no catálogo corrige no contrato. A divergência não tem por onde
voltar.

CAMPO QUE FALTA NÃO SOME

`preencher` devolve (texto, faltas) e deixa o campo desconhecido VISÍVEL no
texto. Num contrato, valor que sumiu em silêncio é pior que valor errado: some a
cláusula de multa e ninguém percebe até precisar dela. Quem chama decide o que
fazer com as faltas — a tela do dono avisa, e a geração do documento assinável
se recusa a seguir.

DOIS CONTRATOS, UM MOTOR

Contrato de locação de espaço é do nicho de eventos, e lá ele existe por NICHO: a
porta é a mesma que decide o modo do orçamento (vendas.modo_por_nicho).

Desde 23/09/2026 existe o segundo: PRESTAÇÃO DE SERVIÇOS, pro recorrente (setup
e mensalidade). Pedido do dono pra ZAQ, conta 3 — "o mesmo modelo que já roda na
Prime ... com orçamento e contrato, sem o aditivo". O motor é este mesmo (campos,
`preencher`, `montar`, assinatura congelada); mudam o texto padrão, os "números da
casa" e a PORTA: no recorrente o contrato é uma chave por conta
(`contrato_modelo.pedir_assinatura`, migração 311), que nasce desligada. Oito
contas são recorrentes, e ligar pelo nicho poria um contrato que ninguém escreveu
na frente de clínica, seguros e construção de uma vez.
"""
from __future__ import annotations

import json
import logging
import re
import secrets

_log = logging.getLogger("finance.contrato")

# Um campo é {grupo.nome}. O ponto separa DE ONDE vem o valor, e isso é
# proposital: quem lê a cláusula sabe se aquele número veio do catálogo, do
# orçamento ou de uma regra da casa sem precisar consultar tabela nenhuma.
_CAMPO = re.compile(r"\{([a-z]+)\.([a-z0-9_-]+)\}")

# Os grupos, e o que cada um significa pra quem escreve a cláusula.
GRUPOS = {
    "preco":   "preço de um item do catálogo, pelo slug",
    "evento":  "o que o cliente informou: data, horário, convidados, tipo",
    "cliente": "quem assina: nome e documento",
    "valor":   "dinheiro do orçamento: total, entrada, saldo, parcelas",
    "regra":   "números da casa: sinal, multas, duração, tolerância",
    "empresa": "quem loca: razão social, CNPJ, endereço",
}

# As regras da casa, com os valores do contrato vigente da Prime como padrão de
# quem ainda não configurou. Cada uma é (chave, rótulo na tela, valor inicial).
REGRAS_PADRAO = {
    "sinal_pct":          30,     # % da entrada que confirma a reserva (cláusula 3.2)
    "multa_cancelamento": 30,     # % sobre o total (cláusula 10.2)
    "taxa_reagendamento": 10,     # % sobre o valor atualizado (cláusula 11.3)
    "duracao_horas":       5,     # horas de evento incluídas (cláusula 2.2)
    "tolerancia_min":     30,     # minutos de cortesia (cláusula 2.3)
    "quitacao_dias":       7,     # dias antes do evento (cláusula 3.3)
    "reagenda_dias":      30,     # antecedência mínima (cláusula 11.1)
    "reagenda_prazo":    180,     # prazo da nova data (cláusula 11.5)
    "retirada_horas":     48,     # retirada de materiais (cláusula 13.3)
    "acesso_montagem":  "10h00",  # entrada de fornecedores (cláusula 2.9)
    "multa_atraso_pct":    2,     # % sobre a parcela vencida, no boleto (cláusula 3.4)
    "juros_mora_pct_mes":  1,     # % ao mês, proporcional aos dias de atraso (cláusula 3.4)
}


# Os dois documentos. É o NICHO que escolhe o texto padrão e os números da casa;
# a chave por conta (`pedir_assinatura`) só decide se o de serviço está ligado.
MODO_LOCACAO = "locacao"
MODO_SERVICO = "servico"

# Os números da casa do contrato de SERVIÇO. Em branco de propósito, e é o oposto
# do de locação: lá os padrões são os do contrato vigente da Prime; aqui não existe
# contrato vigente nenhum ("ainda não", disse o dono da ZAQ em 23/09/2026). Um
# número inventado — fidelidade de 12 meses, multa de 30% — iria pro cliente como
# se fosse da empresa. Em branco, ele fica à vista no texto e a tela recusa ligar
# o contrato até alguém preencher. Multa e juros de atraso têm padrão porque 2% e
# 1% ao mês são o teto que o CDC e a prática bancária já dão.
REGRAS_SERVICO_PADRAO = {
    "fidelidade_meses":  "",   # prazo mínimo (cláusula 5.1)
    "dia_vencimento":    "",   # vencimento da mensalidade (cláusula 3.2)
    "indice_reajuste":   "",   # IPCA, IGP-M... (cláusula 4.1)
    "aviso_previo_dias": "",   # antecedência pra cancelar (cláusula 9.1)
    "multa_rescisao":    "",   # % das mensalidades restantes da fidelidade (9.2)
    "implantacao_dias":  "",   # prazo da implantação, dias úteis (cláusula 2.2)
    "suporte_horario":   "",   # "de segunda a sexta, das 8h às 18h" (cláusula 6.1)
    "setup_parcelas":    "",   # "parcela única", "3 parcelas mensais" (cláusula 2.1)
    "multa_atraso_pct":   2,   # % sobre o valor em atraso (cláusula 3.4)
    "juros_mora_pct_mes": 1,   # % ao mês (cláusula 3.4)
}

# o que vira porcentagem na hora de preencher — o resto entra como está escrito
_REGRAS_PCT = {"sinal_pct", "multa_cancelamento", "taxa_reagendamento",
               "multa_atraso_pct", "juros_mora_pct_mes", "multa_rescisao"}


def regras_padrao(modo: str = MODO_LOCACAO) -> dict:
    return dict(REGRAS_SERVICO_PADRAO if modo == MODO_SERVICO else REGRAS_PADRAO)


def reais(centavos) -> str:
    """R$ 8.900,00 — com centavos, porque é documento e não conversa de WhatsApp."""
    v = int(centavos or 0) / 100
    return "R$ " + f"{v:,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def data_br(v) -> str:
    """31/12/2026 — a data como brasileiro lê.

    O orçamento grava a data do evento em ISO ('2026-10-10'; conferido nos dois
    orçamentos de evento em produção em 16/08/2026). Sem passar por aqui, um
    contrato de locação daqui imprime '2026-10-10' na qualificação do objeto e em
    toda cláusula que cite {evento.data} — data ao contrário em documento que se
    assina e se arquiva.

    Tolerante de propósito: o que não for data reconhecível volta como veio. O
    campo é texto livre e o dono pode ter escrito 'a combinar' — trocar isso por
    vazio apagaria informação que ele quis dar."""
    from finance.agenda import parse_data      # só datetime lá dentro, sem banco
    d = parse_data(v)
    return d.strftime("%d/%m/%Y") if d else str(v or "")


def pct(n) -> str:
    """30% — inteiro quando é inteiro, que é como um contrato escreve."""
    try:
        v = float(n)
    except (TypeError, ValueError):
        return "0%"
    return (f"{int(v)}%" if v == int(v) else f"{v}%".replace(".", ","))


def _regras(modelo, modo: str = MODO_LOCACAO) -> dict:
    """As regras da conta por cima dos padrões — conta que nunca configurou usa
    os números do contrato vigente em vez de zeros, que num contrato seriam
    piores que a falta. No de serviço os padrões são brancos (ver
    REGRAS_SERVICO_PADRAO): a falta fica à vista em vez de um número inventado."""
    r = regras_padrao(modo)
    for k, v in ((modelo or {}).get("regras") or {}).items():
        if v not in (None, ""):
            r[k] = v
    return r


def completar_do_cadastro(pool, conta_id: int, orcamento: dict, cliente_id) -> dict:
    """Completa o cliente do orçamento com o que a aba Clientes já sabe.

    POR QUE ISTO PRECISA EXISTIR
    O contrato lia SÓ a linha do orçamento. Quando o vendedor não digitou o CPF
    ali, a folha do cliente saía com "⚠️ Campos sem valor neste contrato:
    cliente.doc" — mesmo com o CPF cadastrado e o orçamento apontando pro
    cadastro. Medido na conta 34 em 02/09/2026: 12 orçamentos sem documento, e em
    4 deles o documento estava no cadastro; dois já tinham virado contrato
    emitido, com o aviso na cara do cliente.

    O vínculo já era pra ser lido — `web/painel_servicos` grava `cliente_id` ao
    salvar e diz o motivo em comentário: "o VÍNCULO é o que faz a folha reler o
    cadastro depois: sem ele, o texto copiado aqui congelaria pra sempre e
    corrigir na aba Clientes não mudaria nada". Faltava alguém consumir.

    O ORÇAMENTO VENCE, sempre. Só buraco é preenchido. Quem digitou um documento
    diferente no orçamento tinha razão pra isso — contrato no nome do cônjuge, do
    pai da noiva, da empresa que paga — e o cadastro não pode desautorizar.

    O ENDEREÇO VEM EM BLOCO, não campo a campo. Rua de um endereço com cidade de
    outro é um endereço que não existe, e num contrato isso é pior que a falta.
    Então: se o orçamento tem logradouro, o endereço é o dele, inteiro; se não
    tem, é o do cadastro, inteiro.

    Tolerante: sem `cliente_id`, sem cadastro ou com o banco fora, devolve o
    orçamento como veio. O contrato continua saindo — com o aviso de sempre, que
    é exatamente o que ele já fazia antes desta função existir.
    """
    o = dict(orcamento or {})
    if not cliente_id:
        return o
    try:
        with pool.connection() as c:
            r = c.execute(
                """select coalesce(nullif(p.cpf,''), nullif(p.cnpj,'')),
                          cl.endereco, cl.cep, cl.cidade, cl.uf
                     from clientes cl
                     left join pessoas p on p.id = cl.pessoa_id
                    where cl.id=%s and cl.dono_id=%s""",
                (int(cliente_id), int(conta_id))).fetchone()
    except Exception as e:  # noqa: BLE001
        _log.warning("não deu pra ler o cadastro do cliente %s: %s: %s",
                     cliente_id, type(e).__name__, e)
        return o
    if not r:
        return o
    doc, endereco, cep, cidade, uf = r
    if not (o.get("cnpj") or "").strip():
        o["cnpj"] = doc or ""
    # o logradouro é a CHAVE do bloco: é ele que diz se existe endereço no
    # orçamento. Cidade/UF sozinhas costumam vir do lead, não de um endereço.
    if not (o.get("endereco") or "").strip():
        o["endereco"] = endereco or ""
        o["cep"] = cep or ""
        o["cidade"] = cidade or o.get("cidade") or ""
        o["uf"] = uf or o.get("uf") or ""
    return o


def contexto(*, catalogo=None, orcamento=None, modelo=None, empresa=None,
             modo: str = MODO_LOCACAO) -> dict:
    """Monta o que `preencher` vai consultar, um dicionário por grupo.

    Recebe o que já existe no sistema — a lista do catálogo, a linha do orçamento
    e os dados da empresa — em vez de ir buscar sozinho: assim a função é pura,
    o teste não precisa de banco, e a pré-visualização da tela do dono pode
    montar um contexto de mentira sem tocar em produção."""
    o = orcamento or {}
    ev = o.get("evento") or {}
    reg = _regras(modelo, modo)
    emp = empresa or {}

    total = int(o.get("setup_centavos") or 0)
    # entrada/saldo são do contrato de locação; o de serviço não tem sinal
    entrada = (round(total * float(reg["sinal_pct"]) / 100)
               if reg.get("sinal_pct") not in (None, "") else 0)

    return {
        # preço vem por SLUG: a cláusula cita o item, não uma cópia do número
        "preco": {s["slug"]: reais(s.get("setup_centavos"))
                  for s in (catalogo or []) if s.get("slug")},
        "evento": {
            "data": data_br(ev.get("data")),
            "inicio": ev.get("inicio") or "",
            "fim": ev.get("fim") or "",
            "tipo": ev.get("tipo") or "",
            "convidados": str(ev.get("convidados") or ""),
            "local": ev.get("local") or "",
        },
        # o CLIENTE inteiro, não só o nome. O orçamento já guarda endereço, cidade,
        # e-mail e telefone — um contrato que qualifica as partes precisa disso, e
        # deixar de fora obrigava a empresa a completar na mão depois de imprimir.
        #
        # NOME: `empresa` primeiro, `cliente` depois — mesma regra de
        # `_espelhar_cliente` (web/painel_servicos.py). O formulário troca o
        # RÓTULO do campo `empresa` pra "Nome completo" quando o cliente é
        # pessoa física, mas grava na mesma coluna de sempre; `cliente` vira
        # "Contato/responsável" (pra PJ) e passa a maior parte do tempo vazio —
        # ou, pior, com um telefone que o agente de IA capturou antes do nome.
        # Relato em produção: contrato saiu com "86998192489" como nome do
        # LOCATÁRIO porque a ordem antiga preferia `cliente` (o telefone).
        "cliente": {
            "nome": o.get("empresa") or o.get("cliente") or "",
            "doc": o.get("cnpj") or "",
            "whatsapp": o.get("whatsapp") or "",
            "email": o.get("email") or "",
            "telefone": o.get("telefone") or o.get("whatsapp") or "",
            "endereco": o.get("endereco") or "",
            "cep": o.get("cep") or "",
            "cidade": o.get("cidade") or "",
            "uf": (o.get("uf") or "").upper(),
        },
        "valor": {
            "total": reais(total),
            "entrada": reais(entrada),
            "saldo": reais(total - entrada),
            "numero": str(o.get("numero") or ""),
            **(_valor_servico(o.get("recorrente") or {}) if modo == MODO_SERVICO else {}),
        },
        # SÓ AS REGRAS DESTE DOCUMENTO. Número em branco sai como "" — e `preencher`
        # deixa o campo à vista no texto, que é o que impede de ir pro cliente.
        "regra": {k: _regra_txt(k, v) for k, v in reg.items()
                  if k in regras_padrao(modo)},
        "empresa": {
            "razao": emp.get("razao_social") or emp.get("nome_fantasia") or "",
            # `obter_dados_empresa` devolve a chave `documento`; ler "cnpj" fazia
            # {empresa.cnpj} sair vazio SEMPRE — falta silenciosa num campo que a
            # paleta oferece. O fallback mantém quem passar o dicionário na mão.
            "cnpj": emp.get("documento") or emp.get("cnpj") or "",
            "endereco": emp.get("endereco") or "",
            "bairro": emp.get("bairro") or "",
            "cep": emp.get("cep") or "",
            "cidade": emp.get("cidade") or "",
            "uf": (emp.get("uf") or "").upper(),
            "telefone": emp.get("telefone") or "",
            "email": emp.get("email_empresa") or "",
        },
    }


def _regra_txt(chave, valor) -> str:
    if valor in (None, ""):
        return ""
    return pct(valor) if chave in _REGRAS_PCT else str(valor)


def _valor_servico(r: dict) -> dict:
    """O dinheiro do contrato de SERVIÇO, lido do orçamento recorrente.

    `mensal_centavos` já vem com o desconto do anual aplicado (é assim que a tela
    grava e que `fechar_orcamento` gera o título recorrente), então o contrato diz
    o mesmo número que o financeiro vai cobrar."""
    anual = bool(r.get("anual"))
    nomes = [str((i or {}).get("nome") or "").strip() for i in (r.get("itens") or [])]
    nomes = [n for n in nomes if n]
    return {
        "setup": reais(r.get("setup_centavos")),
        "mensal": reais(r.get("mensal_centavos")),
        "ano1": reais(r.get("ano1_centavos")),
        "forma": ("anual, com 15% de desconto na mensalidade, vinculado ao período de "
                  "fidelidade" if anual else "mensal"),
        # a lista vira texto corrido: "A; B e C" é como um contrato enumera
        "itens": ("; ".join(nomes[:-1]) + " e " + nomes[-1]) if len(nomes) > 1
                 else (nomes[0] if nomes else ""),
    }


def preencher(texto: str, ctx: dict) -> tuple[str, list[str]]:
    """Troca os {grupo.nome} pelos valores. Devolve (texto, faltas).

    O campo que não resolve FICA NO TEXTO, visível. É o oposto do que se faria
    numa mensagem de chat, e de propósito: num contrato, o valor que evapora em
    silêncio é o perigoso — a cláusula continua lá, gramaticalmente inteira, sem
    o número que lhe dava sentido. Deixando o `{preco.hora-extra}` à vista, quem
    revisa vê; e `faltas` deixa quem chama recusar a assinatura."""
    faltas: list[str] = []

    def _troca(m):
        grupo, nome = m.group(1), m.group(2)
        valor = (ctx.get(grupo) or {}).get(nome)
        if valor in (None, ""):
            faltas.append(f"{grupo}.{nome}")
            return m.group(0)
        return str(valor)

    return _CAMPO.sub(_troca, texto or ""), faltas


def montar(clausulas, ctx: dict) -> tuple[list[dict], list[str]]:
    """O contrato inteiro: cada cláusula com título e corpo preenchidos.

    As faltas vêm juntas e sem repetição, na ordem em que aparecem — é a lista
    que a tela mostra como "o que falta preencher antes de mandar pro cliente"."""
    saida, faltas = [], []
    for c in (clausulas or []):
        titulo, f1 = preencher((c or {}).get("titulo") or "", ctx)
        corpo, f2 = preencher((c or {}).get("corpo") or "", ctx)
        saida.append({"titulo": titulo, "corpo": corpo})
        for f in f1 + f2:
            if f not in faltas:
                faltas.append(f)
    return saida, faltas


# ------------------------------------------------------------------ diagnóstico

# Os grupos que vêm de CADA orçamento. O valor não mora na configuração: chega
# junto com a proposta, e é diferente em cada uma.
GRUPOS_DA_PROPOSTA = ("cliente", "evento", "valor")

# Onde o dono conserta o que é dele. Endereço, não nome de campo: "{empresa.cnpj}"
# não diz a ninguém o que fazer; "preencha na aba Empresa" diz.
_ONDE = {
    "preco": "adicione o item ao catálogo, logo abaixo, ou corrija o nome na cláusula",
    "empresa": "preencha nos dados da empresa, na aba Empresa",
    "regra": "preencha em “Números da casa”, dentro deste card",
}


def diagnostico(faltas, ctx, catalogo=None) -> dict:
    """Separa as faltas em CONSERTÁVEIS e as que só dependem da proposta.

    POR QUE ISSO EXISTE. A tela de configuração passou a avisar quais campos
    ficaram sem valor, e o primeiro aviso que a Prime viu foi `{cliente.nome}` —
    porque o orçamento usado de exemplo (o nº 2) não tinha o nome do cliente
    preenchido. Não havia nada errado no contrato dela. Alarme que dispara sem
    ter o que consertar treina o dono a ignorar o próximo, que vai ser de
    verdade.

    A régua é uma só: SÓ é alarme o que não vai se resolver sozinho.

        preco.X     slug fora do catálogo  → nunca preenche, em proposta nenhuma
        empresa.X   cadastro em branco     → nunca preenche até o dono preencher
        regra.X     número da casa vazio   → idem
        cliente/evento/valor com campo INEXISTENTE (erro de digitação na
                    cláusula) → nunca preenche, em proposta nenhuma
        cliente/evento/valor com campo VÁLIDO e vazio → é dado daquela proposta;
                    a próxima que tiver o dado preenche. Informação, não defeito.

    Devolve {"ajustes": [...], "da_proposta": [campo, ...]}. Cada ajuste traz
    `titulo` (o campo em português) e `detalhe` (o que fazer, e onde) — a tela só
    imprime, e o texto que o dono lê fica testável aqui em vez de dentro do JS.

    Não substitui `montar`: o contrato do cliente de verdade (contrato_publico)
    continua olhando TODAS as faltas, porque lá o {cliente.nome} vazio é o nome
    de quem vai assinar."""
    ajustes, da_proposta = [], []

    for f in (faltas or []):
        grupo, _, nome = f.partition(".")
        existe = nome in (ctx.get(grupo) or {})
        rotulo = _ROTULO.get(f) or nome

        if grupo not in GRUPOS:
            ajustes.append({
                "campo": f, "titulo": f"{{{f}}}",
                "detalhe": "esse campo não existe — veja a lista de campos ao abrir o contrato"})
        elif grupo == "preco":
            # o slug não está no catálogo (se estivesse, teria valor e não seria
            # falta), então não há nome bonito pra mostrar: vale mais o campo do
            # jeito que ele aparece na cláusula, que é o que o dono procura.
            ajustes.append({
                "campo": f, "titulo": f"{{{f}}}",
                "detalhe": f"esse item não existe no seu catálogo — {_ONDE['preco']}"})
        elif not existe:
            # digitou errado o nome do campo: {cliente.nomee} não preenche nunca
            ajustes.append({
                "campo": f, "titulo": f"{{{f}}}",
                "detalhe": "esse campo não existe — o contrato vai imprimir isso como está"})
        elif grupo in GRUPOS_DA_PROPOSTA:
            da_proposta.append(f)
        else:
            ajustes.append({
                "campo": f, "titulo": rotulo,
                "detalhe": f"está em branco — {_ONDE[grupo]}"})

    return {"ajustes": ajustes, "da_proposta": da_proposta}


def campos_usados(clausulas) -> list[str]:
    """Todos os campos citados no modelo, sem repetição.

    Serve à tela do dono: é como ela sabe quais preços do catálogo aquele
    contrato depende, e avisa quando um deles some do catálogo."""
    vistos = []
    for c in (clausulas or []):
        for texto in ((c or {}).get("titulo") or "", (c or {}).get("corpo") or ""):
            for g, n in _CAMPO.findall(texto):
                if f"{g}.{n}" not in vistos:
                    vistos.append(f"{g}.{n}")
    return vistos


# A paleta fixa: campo e como se chama pra quem não escreveu o sistema. Vive aqui
# fora e não dentro de `campos_disponiveis` porque o diagnóstico usa os mesmos
# rótulos — duas listas iguais viravam duas listas diferentes na primeira edição.
_CAMPOS_FIXOS = [
    ("cliente.nome", "nome de quem assina"), ("cliente.doc", "CPF/CNPJ"),
    ("cliente.endereco", "endereço do cliente"), ("cliente.cidade", "cidade do cliente"),
    ("cliente.uf", "UF do cliente"), ("cliente.cep", "CEP do cliente"),
    ("cliente.telefone", "telefone do cliente"), ("cliente.email", "e-mail do cliente"),
    ("evento.data", "data do evento"), ("evento.inicio", "horário de início"),
    ("evento.fim", "horário de término"), ("evento.tipo", "tipo de evento"),
    ("evento.convidados", "nº de convidados"),
    ("valor.total", "valor total"), ("valor.entrada", "valor da entrada"),
    ("valor.saldo", "saldo a pagar"), ("valor.numero", "nº do orçamento"),
    ("regra.sinal_pct", "% da entrada"), ("regra.multa_cancelamento", "% da multa"),
    ("regra.taxa_reagendamento", "% do reagendamento"),
    ("regra.duracao_horas", "horas de evento"), ("regra.tolerancia_min", "min. de tolerância"),
    ("regra.quitacao_dias", "dias p/ quitar"), ("regra.reagenda_dias", "antecedência p/ remarcar"),
    ("regra.reagenda_prazo", "prazo da nova data"), ("regra.retirada_horas", "horas p/ retirar"),
    ("regra.acesso_montagem", "horário de montagem"),
    ("evento.local", "local do evento"),
    ("empresa.razao", "razão social"), ("empresa.cnpj", "CNPJ"),
    ("empresa.endereco", "endereço"), ("empresa.bairro", "bairro"),
    ("empresa.cidade", "cidade"), ("empresa.uf", "UF"),
    ("empresa.telefone", "telefone"), ("empresa.email", "e-mail"),
]

# A paleta do contrato de SERVIÇO: nada de evento, entrada ou saldo (seção 6 do
# CLAUDE.md — "festa" não aparece pra quem vende mensalidade).
_CAMPOS_SERVICO = [
    ("cliente.nome", "nome de quem assina"), ("cliente.doc", "CPF/CNPJ"),
    ("cliente.endereco", "endereço do cliente"), ("cliente.cidade", "cidade do cliente"),
    ("cliente.uf", "UF do cliente"), ("cliente.cep", "CEP do cliente"),
    ("cliente.telefone", "telefone do cliente"), ("cliente.email", "e-mail do cliente"),
    ("valor.itens", "serviços contratados"), ("valor.setup", "valor da implantação"),
    ("valor.mensal", "mensalidade"), ("valor.forma", "forma de pagamento"),
    ("valor.ano1", "total do 1º ano"), ("valor.numero", "nº do orçamento"),
    ("regra.fidelidade_meses", "meses de fidelidade"),
    ("regra.dia_vencimento", "dia de vencimento"),
    ("regra.indice_reajuste", "índice de reajuste"),
    ("regra.aviso_previo_dias", "dias de aviso prévio"),
    ("regra.multa_rescisao", "% da multa rescisória"),
    ("regra.implantacao_dias", "dias úteis de implantação"),
    ("regra.suporte_horario", "horário do suporte"),
    ("regra.setup_parcelas", "parcelas da implantação"),
    ("regra.multa_atraso_pct", "% da multa por atraso"),
    ("regra.juros_mora_pct_mes", "% de juros ao mês"),
    ("empresa.razao", "razão social"), ("empresa.cnpj", "CNPJ"),
    ("empresa.endereco", "endereço"), ("empresa.bairro", "bairro"),
    ("empresa.cidade", "cidade"), ("empresa.uf", "UF"),
    ("empresa.telefone", "telefone"), ("empresa.email", "e-mail"),
]

_ROTULO = {**dict(_CAMPOS_SERVICO), **dict(_CAMPOS_FIXOS)}


def campos_disponiveis(catalogo=None, modo: str = MODO_LOCACAO) -> list[dict]:
    """A paleta de campos que a tela do dono mostra, na ordem em que ele pensa.

    Os {preco.*} são gerados a partir do catálogo REAL da conta — é assim que ele
    descobre que pode citar qualquer item, e com o slug certo. Escrever o slug de
    cabeça é a forma mais fácil de criar uma falta silenciosa.

    No de SERVIÇO não há {preco.*}: o preço de cada serviço entra pelo orçamento
    ({valor.itens}, {valor.setup}, {valor.mensal}), que é o que o cliente aprovou."""
    if modo == MODO_SERVICO:
        return [{"campo": c, "rotulo": r, "grupo": c.split(".")[0]} for c, r in _CAMPOS_SERVICO]
    saida = [{"campo": c, "rotulo": r, "grupo": c.split(".")[0]} for c, r in _CAMPOS_FIXOS]
    for s in (catalogo or []):
        if s.get("slug"):
            saida.append({"campo": f"preco.{s['slug']}",
                          "rotulo": (s.get("nome") or s["slug"]).lower(), "grupo": "preco"})
    return saida


def tem_contrato(nicho: str | None) -> bool:
    """Esta conta tem contrato de locação?

    Mesma porta que decide o modo do orçamento — de propósito. Uma regra nova e
    paralela poderia divergir da primeira, e aí a conta emitiria orçamento de
    evento com contrato de serviço, ou o contrário."""
    from finance.vendas import modo_por_nicho
    return modo_por_nicho(nicho) == "evento"


def modo_do_nicho(nicho: str | None) -> str:
    """Qual dos dois documentos esta conta escreve: locação (eventos) ou serviço.
    A mesma porta de `tem_contrato`, pra os dois nunca discordarem."""
    return MODO_LOCACAO if tem_contrato(nicho) else MODO_SERVICO


def modo_da_conta(pool, conta_id: int) -> str:
    from finance import empresa as emp
    return modo_do_nicho((emp.obter_dados_empresa(pool, conta_id) or {}).get("nicho"))


def pede_assinatura_servico(pool, conta_id: int) -> bool:
    """A chave do recorrente (migração 311): esta conta ligou o contrato de serviço?

    FALHA FECHADA NO COMPORTAMENTO DE HOJE: sem a coluna, sem linha ou com o banco
    fora, devolve False — a proposta aprovada fecha pelo botão, como sempre fechou.
    O contrario (assumir ligado) travaria o financeiro de uma conta que nunca pediu
    contrato, esperando uma assinatura que ninguém vai mandar."""
    try:
        with pool.connection() as c:
            r = c.execute("select pedir_assinatura from contrato_modelo where conta_id=%s",
                          (conta_id,)).fetchone()
    except Exception as e:  # noqa: BLE001 — base sem a 311 ainda
        _log.warning("não deu pra ler pedir_assinatura da conta %s: %s: %s",
                     conta_id, type(e).__name__, e)
        return False
    return bool(r and r[0])


def conta_tem_contrato(pool, conta_id: int) -> bool:
    """Nesta conta, a proposta aprovada vira contrato?

    Eventos: sempre (é do nicho). Recorrente: só com a chave ligada."""
    if modo_da_conta(pool, conta_id) == MODO_LOCACAO:
        return True
    return pede_assinatura_servico(pool, conta_id)


# ---------------------------------------------------------------- persistência

def carregar_modelo(pool, conta_id: int, modo: str | None = None) -> dict:
    """O modelo da conta. Quem nunca editou recebe o modelo padrão — assim a
    tela abre com um contrato inteiro pra editar em vez de uma página em branco,
    que é o que faz o dono desistir na primeira visita.

    `atualizado_em`/`atualizado_por` alimentam o resumo do card recolhido: o
    contrato se escreve uma vez e some da frente, e é esse resumo que responde
    "está no ar e é o meu?" sem obrigar a abrir. O nome sai do membro; 'dono'
    (quem abriu a conta) não tem linha em `membros` e vira o nome da conta.

    `modo` escolhe o modelo padrão e os números da casa (locação × serviço); sem
    ele, sai do nicho da conta. `pedir_assinatura` é a chave do recorrente (311),
    lida à parte pra uma base sem a coluna não derrubar o contrato da Prime."""
    if not modo:
        # TOLERANTE aqui, e só aqui: até a 311 esta função nem olhava o nicho, e
        # quem chama (a folha do contrato, o aditivo) não pode cair porque o
        # nicho não pôde ser lido. Sem nicho, o de sempre — o de locação. As
        # portas que decidem se NASCE contrato (`conta_tem_contrato`) continuam
        # sem esta tolerância.
        try:
            modo = modo_da_conta(pool, conta_id)
        except Exception as e:  # noqa: BLE001
            _log.warning("modelo da conta %s: não deu pra ler o nicho (%s: %s) — "
                         "segue o de locação", conta_id, type(e).__name__, e)
            modo = MODO_LOCACAO
    pedir = pede_assinatura_servico(pool, conta_id) if modo == MODO_SERVICO else True
    with pool.connection() as c:
        r = c.execute(
            """select m.clausulas, m.regras, m.atualizado_em,
                      coalesce((select mb.nome from membros mb
                                 where mb.id = case when m.atualizado_por ~ '^[0-9]+$'
                                                    then m.atualizado_por::bigint end),
                               (select ct.nome from contas ct where ct.id = m.conta_id), '')
                      , m.assinar_antes_do_sinal
                 from contrato_modelo m where m.conta_id=%s""", (conta_id,)).fetchone()
    # o PARÂMETRO sobrevive ao modelo em branco: quem ligou a ordem nova e ainda não
    # escreveu cláusula nenhuma continua com a ordem que escolheu.
    antes = bool(r[4]) if r else False
    if not r or not r[0]:
        return {"clausulas": modelo_padrao(modo), "regras": regras_padrao(modo), "novo": True,
                "atualizado_em": None, "atualizado_por": "",
                "assinar_antes_do_sinal": antes, "modo": modo, "pedir_assinatura": pedir}
    return {"clausulas": r[0], "regras": _regras({"regras": r[1]}, modo), "novo": False,
            "atualizado_em": r[2], "atualizado_por": r[3] or "",
            "assinar_antes_do_sinal": antes, "modo": modo, "pedir_assinatura": pedir}


def assina_antes_do_sinal(pool, conta_id: int) -> bool:
    """Esta conta pede a assinatura do contrato ANTES do sinal? (migração 194)

    POR QUE UMA FUNÇÃO SÓ PRA ISSO. O funil precisa do parâmetro a cada renderização
    da lista e não quer o modelo inteiro — cláusulas e regras — só pra saber a
    ordem de dois botões. E a tela do contrato precisa dele junto do modelo. Uma
    leitura, dois chamadores, nenhuma cópia da regra.

    FALHA FECHADA: sem a coluna, sem linha ou com o banco fora, devolve False — a
    ordem de hoje. Um parâmetro que não pôde ser lido não pode mudar o fluxo de
    ninguém, e menos ainda inverter a ordem em que a empresa cobra o cliente.
    """
    try:
        with pool.connection() as c:
            r = c.execute("select assinar_antes_do_sinal from contrato_modelo "
                          "where conta_id=%s", (conta_id,)).fetchone()
    except Exception as e:  # noqa: BLE001 — base sem a 194 ainda
        _log.warning("não deu pra ler assinar_antes_do_sinal da conta %s: %s: %s",
                     conta_id, type(e).__name__, e)
        return False
    return bool(r and r[0])


def salvar_modelo(pool, conta_id: int, clausulas, regras, por: str = "",
                  assinar_antes_do_sinal: bool = False,
                  pedir_assinatura: bool | None = None) -> dict:
    """Grava o modelo inteiro. Não versiona de propósito: o histórico que importa
    é o dos contratos ASSINADOS, e esse mora congelado em cada orçamento.

    `assinar_antes_do_sinal` é a ORDEM que a empresa escolheu (194) e vem junto
    porque é o mesmo botão Salvar da mesma tela — pedir um segundo clique pra ela
    faria o dono ligar a ordem nova e sair achando que ligou, quando não salvou.

    `pedir_assinatura` (311) é a chave do recorrente, pelo mesmo motivo. None =
    não mexe (a tela de eventos não manda o campo, e não pode desligar nada).
    QUEM LIGA A CHAVE CONFERE ANTES — ver `pendencias_pra_ligar` —, porque ligada
    com número em branco o contrato iria pro cliente com o campo cru no texto."""
    limpas = [{"titulo": str((c or {}).get("titulo") or "")[:200],
               "corpo": str((c or {}).get("corpo") or "")[:20000]}
              for c in (clausulas or []) if (c or {}).get("titulo") or (c or {}).get("corpo")]
    with pool.connection() as c:
        c.execute(
            """insert into contrato_modelo (conta_id, clausulas, regras, atualizado_por,
                                            assinar_antes_do_sinal)
               values (%s,%s::jsonb,%s::jsonb,%s,%s)
               on conflict (conta_id) do update
                  set clausulas=excluded.clausulas, regras=excluded.regras,
                      atualizado_em=now(), atualizado_por=excluded.atualizado_por,
                      assinar_antes_do_sinal=excluded.assinar_antes_do_sinal""",
            (conta_id, json.dumps(limpas), json.dumps(regras or {}), (por or "")[:120],
             bool(assinar_antes_do_sinal)))
        if pedir_assinatura is not None:
            c.execute("update contrato_modelo set pedir_assinatura=%s where conta_id=%s",
                      (bool(pedir_assinatura), conta_id))
        c.commit()
    return {"ok": True, "clausulas": len(limpas)}


def pendencias_pra_ligar(clausulas, regras, empresa=None) -> list[dict]:
    """O que impede de ligar o contrato de SERVIÇO: número da casa ou dado da
    empresa em branco que alguma cláusula cita.

    Só olha o que é do DONO (regra.*, empresa.*) e campo inexistente — dado de
    cada proposta (cliente, valor) chega com a proposta. É a mesma régua do
    `diagnostico`, aplicada a um contexto sem proposta nenhuma."""
    ctx = contexto(modelo={"regras": regras or {}}, empresa=empresa, modo=MODO_SERVICO)
    _doc, faltas = montar(clausulas, ctx)
    return diagnostico(faltas, ctx)["ajustes"]


def modelo_padrao(modo: str = MODO_LOCACAO) -> list[dict]:
    """Contrato de locação de espaço, genérico, já com os campos no lugar.

    É o ponto de partida de toda conta de eventos — inclusive da Prime, cujo
    contrato vigente foi a base deste texto. Quem tem o contrato próprio
    substitui; quem não tem sai daqui com algo utilizável.

    `modo=servico` devolve o de prestação de serviços (ver `modelo_padrao_servico`)."""
    if modo == MODO_SERVICO:
        return modelo_padrao_servico()
    return [
        {"titulo": "Cláusula 1 — Do objeto",
         "corpo": "1.1. O presente contrato tem por objeto a locação temporária do espaço da "
                  "{empresa.razao} para a realização do evento do tipo {evento.tipo}, de "
                  "{cliente.nome}, CPF/CNPJ {cliente.doc}, no dia {evento.data}, com início às "
                  "{evento.inicio}, para {evento.convidados} convidados.\n"
                  "1.2. O Orçamento nº {valor.numero}, aprovado pelo(a) LOCATÁRIO(A), integra "
                  "este contrato e contém as condições específicas da contratação."},
        {"titulo": "Cláusula 2 — Da duração e da utilização excedente",
         "corpo": "2.1. A duração contratada será de {regra.duracao_horas} horas de evento.\n"
                  "2.2. Será concedida tolerância de {regra.tolerancia_min} minutos após o "
                  "término, sem cobrança adicional.\n"
                  "2.3. Ultrapassada a tolerância, será cobrada Taxa de Utilização Excedente de "
                  "{preco.hora-extra} por hora.\n"
                  "2.4. O acesso para montagem e entrada de fornecedores ocorrerá a partir das "
                  "{regra.acesso_montagem} do dia do evento."},
        {"titulo": "Cláusula 3 — Do valor e do pagamento",
         "corpo": "3.1. O valor total da contratação é de {valor.total}.\n"
                  "3.2. Para confirmação da reserva será exigida entrada de {regra.sinal_pct} "
                  "do valor total, correspondente a {valor.entrada}.\n"
                  "3.3. O saldo de {valor.saldo} deverá estar integralmente quitado até "
                  "{regra.quitacao_dias} dias corridos antes da realização do evento.\n"
                  "3.4. Na opção do plano para pagamento no boleto, o atraso no pagamento de "
                  "qualquer parcela sujeitará o(a) LOCATÁRIO(A) à multa de "
                  "{regra.multa_atraso_pct} sobre a parcela vencida, acrescida de juros de "
                  "mora de {regra.juros_mora_pct_mes} ao mês."},
        {"titulo": "Cláusula 4 — Da reserva da data",
         "corpo": "4.1. A data somente será considerada definitivamente reservada após a "
                  "confirmação do pagamento da entrada prevista na Cláusula 3.\n"
                  "4.2. Bloqueio provisório durante negociação não constitui reserva definitiva."},
        {"titulo": "Cláusula 5 — Da limpeza e dos danos",
         "corpo": "5.1. Caso o(a) LOCATÁRIO(A) opte por não realizar a limpeza pós-evento, esta "
                  "poderá ser contratada por {preco.taxa-de-limpeza}.\n"
                  "5.2. O(A) LOCATÁRIO(A) responderá pelos danos comprovadamente causados por si, "
                  "seus convidados ou fornecedores.\n"
                  "5.3. Os materiais do(a) LOCATÁRIO(A) deverão ser retirados em até "
                  "{regra.retirada_horas} horas após o evento."},
        {"titulo": "Cláusula 6 — Do cancelamento",
         "corpo": "6.1. O cancelamento deverá ser solicitado por escrito.\n"
                  "6.2. Em caso de cancelamento pelo(a) LOCATÁRIO(A), será aplicada multa de "
                  "{regra.multa_cancelamento} sobre o valor total do contrato.\n"
                  "6.3. Caso os valores já pagos superem a multa, a diferença será restituída."},
        {"titulo": "Cláusula 7 — Da alteração de data",
         "corpo": "7.1. Será permitida 1 (uma) alteração de data, solicitada por escrito com "
                  "antecedência mínima de {regra.reagenda_dias} dias corridos.\n"
                  "7.2. A alteração depende de disponibilidade e será cobrada taxa de "
                  "{regra.taxa_reagendamento} sobre o valor atualizado do contrato.\n"
                  "7.3. A nova data deverá ocorrer em até {regra.reagenda_prazo} dias corridos "
                  "contados da data originalmente contratada."},
        {"titulo": "Cláusula 8 — Dos fornecedores",
         "corpo": "8.1. O(A) LOCATÁRIO(A) poderá contratar fornecedores de sua escolha, desde que "
                  "previamente informados à {empresa.razao}.\n"
                  "8.2. Quando o fornecedor for contratado diretamente pelo(a) LOCATÁRIO(A), "
                  "caberão a ele as responsabilidades pela contratação, pagamento e execução."},
        {"titulo": "Cláusula 9 — Das disposições gerais e do foro",
         "corpo": "9.1. O Orçamento nº {valor.numero} e seus aditivos integram este contrato.\n"
                  "9.2. Alterações, descontos e condições especiais somente serão válidos quando "
                  "formalizados por escrito.\n"
                  "9.3. Aplica-se a legislação brasileira, especialmente o Código Civil, o Código "
                  "de Defesa do Consumidor e a Lei Geral de Proteção de Dados."},
    ]



def modelo_padrao_servico() -> list[dict]:
    """Contrato de PRESTAÇÃO DE SERVIÇOS de tecnologia, genérico, com os campos no
    lugar. É o ponto de partida das contas recorrentes que ligam o contrato — a
    primeira foi a ZAQ (conta 3), em 23/09/2026, sem contrato próprio ainda ("você
    tenta fazer da melhor forma", disse o dono).

    As mesmas regras do modelo de locação: nenhum número escrito no texto — setup,
    mensalidade, fidelidade e multa saem do orçamento e dos "números da casa" — e
    nada de festa (seção 6 do CLAUDE.md)."""
    return [
        {"titulo": "Cláusula 1 — Do objeto",
         "corpo": "1.1. O presente contrato tem por objeto a prestação, pela {empresa.razao}, "
                  "CNPJ {empresa.cnpj}, dos seguintes serviços ao(à) CONTRATANTE "
                  "{cliente.nome}, CPF/CNPJ {cliente.doc}: {valor.itens}.\n"
                  "1.2. Os serviços são prestados na modalidade de software como serviço "
                  "(SaaS), com acesso pela internet, sem cessão de código-fonte ou licença de "
                  "uso perpétua.\n"
                  "1.3. O Orçamento nº {valor.numero}, aprovado pelo(a) CONTRATANTE, integra "
                  "este contrato e contém o escopo detalhado da contratação."},
        {"titulo": "Cláusula 2 — Da implantação",
         "corpo": "2.1. Pela implantação, configuração e treinamento inicial, o(a) CONTRATANTE "
                  "pagará o valor de {valor.setup}, em {regra.setup_parcelas}.\n"
                  "2.2. A implantação será concluída em até {regra.implantacao_dias} dias úteis "
                  "contados da assinatura deste contrato e do envio, pelo(a) CONTRATANTE, das "
                  "informações e acessos necessários.\n"
                  "2.3. Atrasos causados pela falta de informações, acessos ou aprovações do(a) "
                  "CONTRATANTE suspendem o prazo acima pelo mesmo período."},
        {"titulo": "Cláusula 3 — Da mensalidade e do pagamento",
         "corpo": "3.1. Pela disponibilidade e manutenção dos serviços, o(a) CONTRATANTE pagará "
                  "a mensalidade de {valor.mensal}, na forma de pagamento {valor.forma}.\n"
                  "3.2. A mensalidade vence todo dia {regra.dia_vencimento} de cada mês, a "
                  "partir do mês seguinte à assinatura.\n"
                  "3.3. O valor total do primeiro ano, somando implantação e mensalidades, é de "
                  "{valor.ano1}.\n"
                  "3.4. O atraso no pagamento sujeitará o(a) CONTRATANTE à multa de "
                  "{regra.multa_atraso_pct} sobre o valor em atraso, acrescida de juros de mora "
                  "de {regra.juros_mora_pct_mes} ao mês, proporcionais aos dias de atraso.\n"
                  "3.5. Atraso superior a 30 (trinta) dias autoriza a CONTRATADA a suspender o "
                  "acesso aos serviços, mediante aviso prévio de 5 (cinco) dias, até a "
                  "regularização."},
        {"titulo": "Cláusula 4 — Do reajuste",
         "corpo": "4.1. Os valores deste contrato serão reajustados a cada 12 (doze) meses, "
                  "contados da assinatura, pela variação acumulada do {regra.indice_reajuste} "
                  "no período.\n"
                  "4.2. Na falta ou extinção do índice, será adotado o índice oficial que o "
                  "substituir."},
        {"titulo": "Cláusula 5 — Da vigência e da fidelidade",
         "corpo": "5.1. Este contrato vigora por prazo mínimo de {regra.fidelidade_meses} meses "
                  "a partir da assinatura (período de fidelidade), renovando-se "
                  "automaticamente por prazo indeterminado ao final desse período.\n"
                  "5.2. No pagamento anual, o desconto concedido sobre as mensalidades está "
                  "vinculado ao cumprimento integral do período de fidelidade."},
        {"titulo": "Cláusula 6 — Do suporte e do nível de serviço",
         "corpo": "6.1. O suporte técnico será prestado {regra.suporte_horario}, pelos canais "
                  "informados pela CONTRATADA.\n"
                  "6.2. A CONTRATADA empregará os melhores esforços para manter os serviços "
                  "disponíveis, ressalvadas as manutenções programadas, comunicadas com "
                  "antecedência, e as indisponibilidades de serviços de terceiros "
                  "(provedores de nuvem, operadoras e plataformas de mensagem).\n"
                  "6.3. A CONTRATADA não responde por resultados comerciais do(a) CONTRATANTE "
                  "nem pelo conteúdo das mensagens e dados que ele(a) inserir nos serviços."},
        {"titulo": "Cláusula 7 — Das obrigações do(a) CONTRATANTE",
         "corpo": "7.1. Fornecer as informações e acessos necessários à implantação e mantê-los "
                  "atualizados.\n"
                  "7.2. Utilizar os serviços de acordo com a lei e com as políticas das "
                  "plataformas integradas, inclusive as regras de envio de mensagens.\n"
                  "7.3. Manter em sigilo as senhas e acessos sob sua responsabilidade."},
        {"titulo": "Cláusula 8 — Da proteção de dados (LGPD)",
         "corpo": "8.1. Em relação aos dados pessoais tratados por meio dos serviços, o(a) "
                  "CONTRATANTE atua como controlador e a CONTRATADA como operadora, nos termos "
                  "da Lei nº 13.709/2018.\n"
                  "8.2. A CONTRATADA tratará esses dados apenas para executar este contrato, "
                  "adotará medidas de segurança adequadas e comunicará ao(à) CONTRATANTE "
                  "qualquer incidente relevante.\n"
                  "8.3. Encerrado o contrato, os dados serão disponibilizados ao(à) CONTRATANTE "
                  "por 30 (trinta) dias e depois eliminados, salvo obrigação legal de guarda."},
        {"titulo": "Cláusula 9 — Do cancelamento",
         "corpo": "9.1. Qualquer das partes poderá cancelar este contrato mediante aviso por "
                  "escrito com antecedência mínima de {regra.aviso_previo_dias} dias.\n"
                  "9.2. O cancelamento pelo(a) CONTRATANTE durante o período de fidelidade "
                  "sujeita-o(a) à multa de {regra.multa_rescisao} do valor das mensalidades "
                  "restantes até o fim desse período.\n"
                  "9.3. No pagamento anual, o cancelamento durante a fidelidade implica também "
                  "a devolução do desconto concedido sobre as mensalidades já pagas.\n"
                  "9.4. O valor da implantação não é restituído depois de concluída a "
                  "implantação."},
        {"titulo": "Cláusula 10 — Das disposições gerais e do foro",
         "corpo": "10.1. O Orçamento nº {valor.numero} integra este contrato. Alterações, "
                  "descontos e condições especiais só valem quando formalizados por escrito.\n"
                  "10.2. Este contrato é assinado eletronicamente, com validade jurídica "
                  "conforme a MP nº 2.200-2/2001.\n"
                  "10.3. Fica eleito o foro da comarca de {empresa.cidade}/{empresa.uf} para "
                  "dirimir as questões oriundas deste contrato."},
    ]


# ---------------------------------------------------- o CONTRATO de cada venda
# Até a 164 o contrato era cinco colunas em `orcamentos`. Virou documento: nasce
# de um FATO (o sinal caiu), tem número próprio, estado próprio, e pode ser
# rescindido ou substituído por aditivo. Ver db/migracoes/164_contratos.sql.

STATUS = ("rascunho", "enviado", "assinado", "rescindido", "cumprido")

_COLS_CT = ("id, conta_id, numero, orcamento_id, status, texto, valor_centavos, "
            "assinado_em, assinado_por, assinado_doc, assinado_ip, "
            "rescindido_em, rescisao_motivo, substitui_id, criado_em, token")


def _fmt_contrato(r) -> dict:
    return {"id": r[0], "conta_id": r[1], "numero": r[2], "orcamento_id": r[3],
            "status": r[4], "texto": r[5], "valor_centavos": r[6],
            "assinado_em": r[7], "assinado_por": r[8] or "", "assinado_doc": r[9] or "",
            "assinado_ip": r[10] or "", "rescindido_em": r[11],
            "rescisao_motivo": r[12] or "", "substitui_id": r[13], "criado_em": r[14],
            "token": r[15] if len(r) > 15 else None}


def por_orcamento(pool, conta_id: int, orcamento_id: int) -> dict | None:
    """O contrato VIVO daquele orçamento (o que não foi substituído por aditivo)."""
    try:
        with pool.connection() as c:
            r = c.execute(
                "select " + _COLS_CT + " from contratos "
                " where conta_id=%s and orcamento_id=%s and substitui_id is null "
                " order by id desc limit 1", (conta_id, int(orcamento_id))).fetchone()
    except Exception:  # noqa: BLE001 — base sem a 164 ainda: a folha abre sem contrato
        return None
    return _fmt_contrato(r) if r else None


def assinado_do_orcamento(pool, conta_id: int, orcamento_id: int) -> bool:
    """Este orçamento tem contrato ASSINADO?

    É a pergunta que trava a edição. Documento congelado, com aceite e IP do
    cliente, não pode ter os números de origem mudando embaixo — precisou mudar,
    é aditivo. Mesma regra do `status='fechado'`, e pelo mesmo motivo."""
    ct = por_orcamento(pool, conta_id, orcamento_id)
    return bool(ct and ct["assinado_em"])


def exige_assinatura(pool, conta_id: int) -> bool:
    """Nesta conta, o financeiro só abre com o contrato assinado?

    É a mesma porta de sempre (`tem_contrato` -> `modo_por_nicho`): onde existe
    contrato de locação, é ele que fecha o negócio. Onde não existe — consultoria,
    tecnologia, os recorrentes —, nada muda: quem fecha continua sendo o botão.

    NÃO É TOLERANTE, de propósito, e é o oposto do `vende_data`. Lá o pior caso
    era um enfeite faltando na tela; aqui é gerar contas a receber e lançar receita
    de um negócio que ninguém assinou. Se não dá pra ler o nicho, não dá pra saber
    se este negócio precisa de assinatura — e falhar abrindo a porta recriaria
    exatamente o buraco que esta função existe pra fechar.
    """
    from finance import empresa as emp
    return tem_contrato((emp.obter_dados_empresa(pool, conta_id) or {}).get("nicho"))


def exige_assinatura_do_orcamento(pool, conta_id: int, orcamento_id: int) -> bool:
    """ESTE orçamento só fecha com o contrato assinado?

    Eventos: sempre — a mesma `exige_assinatura` de antes, não tolerante.
    Recorrente: quando EXISTE contrato pra ele. É o contrato nascido que prende o
    fechamento, não a chave da conta: proposta aprovada antes de o dono ligar o
    contrato de serviço não tem contrato nenhum, e ficaria presa pra sempre
    esperando uma assinatura que não vai chegar. Essa fecha pelo botão, como
    fechava ontem."""
    if exige_assinatura(pool, conta_id):
        return True
    return por_orcamento(pool, conta_id, int(orcamento_id)) is not None


def criar_para_orcamento(pool, conta_id: int, orcamento_id: int,
                         valor_centavos: int | None = None,
                         criado_por: str = "") -> dict | None:
    """Cria o contrato daquele orçamento. IDEMPOTENTE: se já existe um vivo,
    devolve o que existe sem tocar em nada.

    Chamado quando o SINAL É CONFIRMADO — é o momento em que as três condições
    que a tela avaliava a cada carregamento (nicho de evento, proposta aprovada,
    sinal pago) deixam de ser uma pergunta e viram um fato. A partir daqui o
    contrato existe e tem estado próprio.

    Devolve None quando a conta não tem contrato: nem é de eventos, nem ligou o
    contrato de serviço (`conta_tem_contrato`) — pra não nascer contrato onde não
    existe. No recorrente ele nasce na APROVAÇÃO (`proposta._pos_assinatura`): lá
    não há sinal, e a ordem é a da Prime com a assinatura antes da entrada —
    orçamento, contrato, e só então a cobrança (dono, 23/09/2026)."""
    ja = por_orcamento(pool, conta_id, orcamento_id)
    if ja:
        return ja
    if not conta_tem_contrato(pool, conta_id):
        return None
    with pool.connection() as c:
        r = c.execute(
            """insert into contratos (conta_id, numero, orcamento_id, status,
                                      valor_centavos, criado_por, token)
               values (%s, (select coalesce(max(numero),0)+1 from contratos
                             where conta_id=%s), %s, 'enviado', %s, %s, %s)
               returning """ + _COLS_CT,
            (conta_id, conta_id, int(orcamento_id), valor_centavos,
             (criado_por or "")[:120], secrets.token_urlsafe(16))
        ).fetchone()
        c.commit()
    return _fmt_contrato(r)


def por_token(pool, token: str) -> dict | None:
    """O contrato pelo link público. Sem conta_id de propósito: quem tem o token vê
    aquele contrato e só ele — mesmo desenho da proposta."""
    if not (token or "").strip():
        return None
    with pool.connection() as c:
        r = c.execute("select " + _COLS_CT + " from contratos where token=%s",
                      (token.strip(),)).fetchone()
    return _fmt_contrato(r) if r else None


def assinar(pool, conta_id: int, contrato_id: int, clausulas,
            nome: str, doc: str, ip: str) -> bool:
    """O cliente aceitou as cláusulas. Congela o texto no ato — grava o que ele
    LEU, não uma referência ao modelo, senão editar o modelo amanhã reescreveria
    o que foi aceito ontem.

    `assinado_em is null` na condição: duplo clique ou reenvio do formulário não
    sobrescreve a assinatura nem o texto já congelado."""
    with pool.connection() as c:
        cur = c.execute(
            """update contratos
                  set texto=%s::jsonb, status='assinado', assinado_em=now(),
                      assinado_por=%s, assinado_doc=%s, assinado_ip=%s
                where id=%s and conta_id=%s and assinado_em is null""",
            (json.dumps(clausulas or []), (nome or "").strip()[:120],
             (doc or "").strip()[:40] or None, (ip or "")[:60],
             int(contrato_id), conta_id))
        c.commit()
        if not cur.rowcount:
            return False
        orcamento_id = c.execute("select orcamento_id from contratos where id=%s",
                                 (int(contrato_id),)).fetchone()[0]

    # É AQUI QUE O FINANCEIRO ABRE. A assinatura é que fecha o negócio no nicho de
    # eventos — o botão "Fechar contrato" some do funil justamente porque quem
    # fechava era ele, sem olhar se havia assinatura.
    #
    # Depois do commit e tolerante, como as consequências do `confirmar_sinal`: a
    # ASSINATURA é o que não pode se perder. Se a geração falhar, o contrato fica
    # assinado e o financeiro é retomável — a rota de fechar continua viva e a
    # trava agora deixa passar (o contrato está assinado). Botão escondido não é
    # rota removida, e foi por isso que eu não removi a rota.
    if orcamento_id:
        try:
            from finance import vendas
            vendas.fechar_orcamento(pool, conta_id, int(orcamento_id),
                                    por_assinatura=True)
        except Exception as e:  # noqa: BLE001
            _log.warning("assinar %s: financeiro não abriu no orçamento %s: %s: %s",
                         contrato_id, orcamento_id, type(e).__name__, e)
        # E O FUNIL ANDA (17/09/2026). Regra do dono: "só conta como venda quando
        # assinar contrato". O financeiro já abria aqui e o Raio-X já contava a venda
        # pelo contrato — só o CARD ficava parado. Na conta 34, dos 7 contratos
        # assinados, três apareciam em "Negociação", e uma dessas clientes estava
        # sendo cobrada pelo follow-up cinco dias depois de ter assinado.
        #
        # `funil_ganho` é tolerante por dentro e nunca anda pra trás — ver o módulo.
        from finance import funil_ganho as _fg
        _fg.marcar_por_assinatura(pool, conta_id, int(orcamento_id))
    return True
