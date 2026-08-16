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

SÓ NICHO EVENTO

Contrato de locação de espaço é do nicho de eventos. Uma conta recorrente teria
um contrato de serviço, que é outro documento — então a porta é a mesma que
decide o modo do orçamento (vendas.modo_por_nicho), e não uma regra nova que
pudesse divergir dela.
"""
from __future__ import annotations

import json
import re

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
}


def reais(centavos) -> str:
    """R$ 8.900,00 — com centavos, porque é documento e não conversa de WhatsApp."""
    v = int(centavos or 0) / 100
    return "R$ " + f"{v:,.2f}".replace(",", "\x00").replace(".", ",").replace("\x00", ".")


def pct(n) -> str:
    """30% — inteiro quando é inteiro, que é como um contrato escreve."""
    try:
        v = float(n)
    except (TypeError, ValueError):
        return "0%"
    return (f"{int(v)}%" if v == int(v) else f"{v}%".replace(".", ","))


def _regras(modelo) -> dict:
    """As regras da conta por cima dos padrões — conta que nunca configurou usa
    os números do contrato vigente em vez de zeros, que num contrato seriam
    piores que a falta."""
    r = dict(REGRAS_PADRAO)
    for k, v in ((modelo or {}).get("regras") or {}).items():
        if v not in (None, ""):
            r[k] = v
    return r


def contexto(*, catalogo=None, orcamento=None, modelo=None, empresa=None) -> dict:
    """Monta o que `preencher` vai consultar, um dicionário por grupo.

    Recebe o que já existe no sistema — a lista do catálogo, a linha do orçamento
    e os dados da empresa — em vez de ir buscar sozinho: assim a função é pura,
    o teste não precisa de banco, e a pré-visualização da tela do dono pode
    montar um contexto de mentira sem tocar em produção."""
    o = orcamento or {}
    ev = o.get("evento") or {}
    reg = _regras(modelo)
    emp = empresa or {}

    total = int(o.get("setup_centavos") or 0)
    entrada = round(total * float(reg["sinal_pct"]) / 100)

    return {
        # preço vem por SLUG: a cláusula cita o item, não uma cópia do número
        "preco": {s["slug"]: reais(s.get("setup_centavos"))
                  for s in (catalogo or []) if s.get("slug")},
        "evento": {
            "data": ev.get("data") or "",
            "inicio": ev.get("inicio") or "",
            "fim": ev.get("fim") or "",
            "tipo": ev.get("tipo") or "",
            "convidados": str(ev.get("convidados") or ""),
            "local": ev.get("local") or "",
        },
        "cliente": {
            "nome": o.get("cliente") or o.get("empresa") or "",
            "doc": o.get("cnpj") or "",
            "whatsapp": o.get("whatsapp") or "",
        },
        "valor": {
            "total": reais(total),
            "entrada": reais(entrada),
            "saldo": reais(total - entrada),
            "numero": str(o.get("numero") or ""),
        },
        "regra": {
            "sinal_pct": pct(reg["sinal_pct"]),
            "multa_cancelamento": pct(reg["multa_cancelamento"]),
            "taxa_reagendamento": pct(reg["taxa_reagendamento"]),
            "duracao_horas": str(reg["duracao_horas"]),
            "tolerancia_min": str(reg["tolerancia_min"]),
            "quitacao_dias": str(reg["quitacao_dias"]),
            "reagenda_dias": str(reg["reagenda_dias"]),
            "reagenda_prazo": str(reg["reagenda_prazo"]),
            "retirada_horas": str(reg["retirada_horas"]),
            "acesso_montagem": str(reg["acesso_montagem"]),
        },
        "empresa": {
            "razao": emp.get("razao_social") or emp.get("nome_fantasia") or "",
            "cnpj": emp.get("cnpj") or "",
            "endereco": emp.get("endereco") or "",
            "cidade": emp.get("cidade") or "",
            "uf": emp.get("uf") or "",
        },
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


def campos_disponiveis(catalogo=None) -> list[dict]:
    """A paleta de campos que a tela do dono mostra, na ordem em que ele pensa.

    Os {preco.*} são gerados a partir do catálogo REAL da conta — é assim que ele
    descobre que pode citar qualquer item, e com o slug certo. Escrever o slug de
    cabeça é a forma mais fácil de criar uma falta silenciosa."""
    fixos = [
        ("cliente.nome", "nome de quem assina"), ("cliente.doc", "CPF/CNPJ"),
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
        ("empresa.razao", "razão social"), ("empresa.cnpj", "CNPJ"),
        ("empresa.endereco", "endereço"),
    ]
    saida = [{"campo": c, "rotulo": r, "grupo": c.split(".")[0]} for c, r in fixos]
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


# ---------------------------------------------------------------- persistência

def carregar_modelo(pool, conta_id: int) -> dict:
    """O modelo da conta. Quem nunca editou recebe o modelo padrão — assim a
    tela abre com um contrato inteiro pra editar em vez de uma página em branco,
    que é o que faz o dono desistir na primeira visita."""
    with pool.connection() as c:
        r = c.execute("select clausulas, regras from contrato_modelo where conta_id=%s",
                      (conta_id,)).fetchone()
    if not r or not r[0]:
        return {"clausulas": modelo_padrao(), "regras": dict(REGRAS_PADRAO), "novo": True}
    return {"clausulas": r[0], "regras": _regras({"regras": r[1]}), "novo": False}


def salvar_modelo(pool, conta_id: int, clausulas, regras, por: str = "") -> dict:
    """Grava o modelo inteiro. Não versiona de propósito: o histórico que importa
    é o dos contratos ASSINADOS, e esse mora congelado em cada orçamento."""
    limpas = [{"titulo": str((c or {}).get("titulo") or "")[:200],
               "corpo": str((c or {}).get("corpo") or "")[:20000]}
              for c in (clausulas or []) if (c or {}).get("titulo") or (c or {}).get("corpo")]
    with pool.connection() as c:
        c.execute(
            """insert into contrato_modelo (conta_id, clausulas, regras, atualizado_por)
               values (%s,%s::jsonb,%s::jsonb,%s)
               on conflict (conta_id) do update
                  set clausulas=excluded.clausulas, regras=excluded.regras,
                      atualizado_em=now(), atualizado_por=excluded.atualizado_por""",
            (conta_id, json.dumps(limpas), json.dumps(regras or {}), (por or "")[:120]))
        c.commit()
    return {"ok": True, "clausulas": len(limpas)}


def modelo_padrao() -> list[dict]:
    """Contrato de locação de espaço, genérico, já com os campos no lugar.

    É o ponto de partida de toda conta de eventos — inclusive da Prime, cujo
    contrato vigente foi a base deste texto. Quem tem o contrato próprio
    substitui; quem não tem sai daqui com algo utilizável."""
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
                  "{regra.quitacao_dias} dias corridos antes da realização do evento."},
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
