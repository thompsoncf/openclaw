"""As ferramentas de COTAÇÃO no WhatsApp — a terceira porta (decisão do dono, 21/09/2026).

SÓ PRA CORRETORA. Ficam disponíveis quando o nicho da conta é `seguros`; em
qualquer outra conta o agente nem sabe que elas existem. É a regra 6 do CLAUDE.md
aplicada ao agente: ferramenta de cotação de seguro numa conta de festa é o mesmo
erro do Raio-X falando de "dia da festa" pra quem vende mensalidade.

POR QUE O WHATSAPP MERECE PORTA PRÓPRIA. É onde a corretora já trabalha — o chip
dela (conta 38) recebe apólice em PDF, boleto e pedido de cotação no mesmo fio.
Quando o corretor está no cliente e manda "cota um Argo 2022 pro Fulano", o custo
de abrir o painel, achar a tela e digitar 14 campos é justamente o que faz a
cotação não ser registrada em lugar nenhum — e ela some.

O QUE A FERRAMENTA NÃO FAZ, de propósito: não escolhe oferta e não emite. Escolher
é ato comercial, com comissão e responsabilidade no meio; acontece na tela, onde a
pessoa vê as ofertas lado a lado. Aqui o agente cota e devolve o link — é o que
economiza o trabalho sem decidir pelo corretor.
"""
from __future__ import annotations

import logging

from core.agent import Ferramenta
from . import cotacao as ct
from .models import formatar_brl

_log = logging.getLogger("openclaw.cotacao_tools")

#: Anexado à persona quando a conta é corretora. Curto de propósito: o corretor é
#: o especialista, e o que ele precisa saber é que existe a ferramenta e o que ela
#: pede — não uma aula de seguro.
BLOCO_COTACAO = """

COTAÇÃO DE SEGURO (esta conta é corretora e tem a tela de Cotações):
- "cota um Argo 2022 pro Fulano", "faz uma cotação de auto" -> cotar_seguro.
- O MÍNIMO pra cotar: CPF, data de nascimento, CEP e o veículo (código FIPE,
  placa OU marca/modelo + ano). Falta algum? Pergunte só o que falta, numa
  linha — não repita o que a pessoa já disse.
- Depois de cotar, mande o link da cotação e PARE. Escolher a oferta e emitir a
  proposta são atos da corretora, na tela — você não escolhe seguradora.
- "como ficou a cotação X", "quanto deu a do Fulano" -> ver_cotacao.
"""


def construir_ferramentas_cotacao(pool, conta_id: int,
                                  membro_id: int | None = None) -> list[Ferramenta]:

    def cotar_seguro(e: dict) -> str:
        try:
            risco = ct.normalizar_risco(e)
        except ct.CotacaoErro as erro:
            # a mensagem já é a frase em português que falta responder — devolvê-la
            # crua é o que faz o agente perguntar a coisa certa em vez de chutar
            return f"Ainda não dá pra cotar: {erro}"
        cid = ct.criar(pool, conta_id, risco, corretor_id=membro_id, origem="whatsapp")
        try:
            cot = ct.cotar(pool, conta_id, cid)
        except Exception as ex:  # noqa: BLE001 — a cotação existe; o preço tenta de novo
            _log.warning("cotação %s pelo WhatsApp não cotou: %s: %s",
                         cid, type(ex).__name__, ex)
            cot = ct.ler(pool, conta_id, cid)
        return _resposta(cot)

    def ver_cotacao(e: dict) -> str:
        try:
            cid = int(e.get("cotacao_id") or 0)
        except (TypeError, ValueError):
            cid = 0
        cot = ct.ler(pool, conta_id, cid) if cid else None
        if not cot:
            ultimas = ct.listar(pool, conta_id, limite=5)
            if not ultimas:
                return "Não há cotação nenhuma nesta conta ainda."
            linhas = [f"• #{c['id']} — {c['resumo']} ({c['situacao_txt']})" for c in ultimas]
            return "Não achei essa cotação. As últimas são:\n" + "\n".join(linhas)
        return _resposta(cot)

    def _resposta(cot: dict) -> str:
        link = f"/painel/cotacoes/{cot['id']}"
        if cot["situacao"] == "falhou":
            return (f"Cotação #{cot['id']} criada ({cot['resumo']}), mas o provedor não "
                    f"deu preço: {cot['erro']}. Dá pra lançar as ofertas na mão em {link}.")
        if not cot["ofertas"]:
            # o caminho NORMAL enquanto não há multicálculo contratado: o risco
            # ficou guardado e o comparativo é digitado na tela
            return (f"Cotação #{cot['id']} criada — {cot['resumo']}. Nenhuma seguradora "
                    f"respondeu automaticamente (não há multicálculo conectado): lance "
                    f"as ofertas em {link} pra comparar e virar proposta.")
        linhas = [f"Cotação #{cot['id']} — {cot['resumo']}:"]
        for o in cot["ofertas"][:6]:
            v = formatar_brl(o["premio_total_centavos"])
            extra = f" em {o['parcelas']}x" if o.get("parcelas") else ""
            linhas.append(f"• {o['seguradora']}: {v}{extra}")
        linhas.append(f"Escolher e gerar a proposta: {link}")
        return "\n".join(linhas)

    return [
        Ferramenta(
            nome="cotar_seguro",
            descricao=("Cria uma cotação de seguro AUTO e busca preço. Precisa de CPF, "
                       "data de nascimento, CEP e o veículo (código FIPE, placa OU "
                       "marca/modelo + ano do modelo). Devolve as ofertas e o link."),
            parametros={
                "type": "object",
                "properties": {
                    "nome": {"type": "string", "description": "nome do segurado"},
                    "cpf": {"type": "string", "description": "CPF do segurado, só dígitos"},
                    "nascimento": {"type": "string", "description": "AAAA-MM-DD ou DD/MM/AAAA"},
                    "telefone": {"type": "string"},
                    "cep": {"type": "string", "description": "CEP onde o carro dorme"},
                    "placa": {"type": "string"},
                    "fipe": {"type": "string", "description": "código FIPE, quando souber"},
                    "marca_modelo": {"type": "string", "description": "ex: FIAT ARGO 1.0"},
                    "ano_modelo": {"type": "integer"},
                    "uso": {"type": "string", "enum": [c for c, _ in ct.USOS_AUTO]},
                    "garagem": {"type": "string", "enum": [c for c, _ in ct.GARAGENS]},
                    "bonus": {"type": "integer", "description": "classe de bônus, 0 a 10"},
                },
                "required": ["cpf", "nascimento", "cep"],
            },
            executar=cotar_seguro,
        ),
        Ferramenta(
            nome="ver_cotacao",
            descricao=("Mostra as ofertas de uma cotação já feita. Sem o número, lista "
                       "as últimas cotações da corretora."),
            parametros={
                "type": "object",
                "properties": {"cotacao_id": {"type": "integer"}},
            },
            executar=ver_cotacao,
        ),
    ]
