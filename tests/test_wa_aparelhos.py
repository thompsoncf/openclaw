"""Contar aparelhos ligados SEM pôr a conexão em risco.

A REGRA QUE MANDA AQUI, e ela veio direta do dono do negócio:
"nunca mexa em conexão ativa, canais ativos ou número que já está conectado; em
hipótese alguma a gente pode deixar cair a conexão dos aparelhos."

Contar aparelhos exige perguntar ao WhatsApp. A pergunta em si é inofensiva (é a
mesma que o Baileys faz pra saber pra quem encriptar cada mensagem) — o que NÃO é
inofensivo é repetir. Cliente não oficial que gera tráfego de robô é como se queima
um número, e número queimado é a pior forma de derrubar a conexão.

Eu escrevi esse defeito e ele durou minutos: pendurei a consulta no `qrPoll`, que
roda de 3 em 3 segundos com a tela de Canais aberta. Estes testes existem pra que
ninguém — inclusive eu — repita isso.

Três travas:
 1. a tela só pergunta com o dedo (botão), nunca em intervalo;
 2. a rota só consulta o WhatsApp com `perguntar=1` — sem isso devolve só o número
    do banco, que não toca em nada;
 3. o serviço Node recusa mais de uma pergunta por minuto por conta, porque a tela
    vem do navegador e navegador não é fonte confiável.
"""
import re
from pathlib import Path

import pytest

SERVER = (Path(__file__).parent.parent / "services" / "wa-qr" / "server.js").read_text(encoding="utf-8")


def _rota_aparelhos() -> str:
    """O bloco da rota, do `if` que a abre até o próximo — pra as asserções
    olharem SÓ ela e não o arquivo inteiro."""
    ini = SERVER.index("if (req.method === 'GET' && acao === 'aparelhos')")
    fim = SERVER.index("if (req.method === 'POST' && acao === 'sair')", ini)
    return SERVER[ini:fim]


def _painel() -> str:
    from web import painel_prospeccao as pp
    import inspect
    return inspect.getsource(pp)


# ══════════════════════════════ trava 1: a tela não pergunta sozinha

def test_a_consulta_de_aparelhos_nao_esta_no_polling():
    """O `qrPoll` roda a cada 3s enquanto a tela de Canais está aberta. Foi ali que
    eu pendurei a consulta por engano — e daria ~1200 perguntas ao WhatsApp por
    hora, por conta, sem ninguém pedir."""
    fonte = _painel()
    poll = re.search(r"function qrPoll\(\)\{(.*?)\n        function ", fonte, re.S)
    assert poll, "não achei o qrPoll — o teste precisa ser reapontado"
    assert "apsPuxa" not in poll.group(1), \
        "a consulta de aparelhos voltou pro polling de 3s: isso queima o número"


def test_o_botao_de_conferir_existe_e_tem_trava_de_clique():
    """Sem a trava, dedo nervoso vira rajada."""
    fonte = _painel()
    assert 'onclick="apsPuxa()"' in fonte, "sumiu o botão: a consulta virou automática?"
    assert "_apsOcupado" in fonte, "o botão perdeu a trava de clique repetido"


def test_o_numero_que_carrega_sozinho_vem_do_BANCO():
    """A saída-por-fora pode aparecer sem pedir porque é uma consulta ao Postgres —
    não encosta no WhatsApp. É ela que fica sempre à vista; o aparelho é sob
    demanda."""
    fonte = _painel()
    auto = re.search(r"DOMContentLoaded'?,function\(\)\{(.*?)\}\);", fonte, re.S)
    assert auto, "não achei o carregamento automático"
    assert "perguntar=1" not in auto.group(1), \
        "o carregamento automático está perguntando ao WhatsApp"


# ══════════════════════════════ trava 2: a rota só consulta se pedirem

def test_a_rota_so_pergunta_ao_whatsapp_com_o_sinal_explicito():
    fonte = _painel()
    rota = fonte[fonte.index("def comunicacao_whatsapp_aparelhos"):]
    rota = rota[:rota.index("@router.post")]
    assert 'request.query_params.get("perguntar") != "1"' in rota
    # e o retorno curto vem ANTES de importar/chamar o cliente do QR
    corte = rota.index('request.query_params.get("perguntar")')
    assert "whatsapp_qr" not in rota[:corte], \
        "a rota consulta o WhatsApp antes de checar se alguém pediu"


# ══════════════════════════════ trava 3: o serviço se defende sozinho

def test_o_servico_recusa_mais_de_uma_pergunta_por_minuto():
    """A tela vem do navegador. Uma aba com laço, um F5 insistente ou outro cliente
    qualquer não podem virar rajada — a trava tem que estar do lado de cá."""
    bloco = _rota_aparelhos()
    # a GUARDA, não a constante: "60000" sozinho sobrevive na linha que calcula
    # quantos segundos faltam, e aí o teste passa com a trava desligada
    assert "agora - ultimo < 60000" in bloco, "sumiu a guarda do intervalo mínimo"
    assert "'espere'" in bloco, "sem resposta de espera, o chamador insiste"
    # e a marca do tempo tem que ser gravada, senão a guarda nunca fecha
    assert "__apsQuando[contaId] = agora" in bloco, "não registra quando perguntou"


def test_a_consulta_usa_o_cache_do_baileys():
    """`useCache=false` força ida à rede a cada clique. O Baileys já mantém essa
    lista pra encriptar mensagem — reusar não custa tráfego nenhum."""
    bloco = _rota_aparelhos()
    assert "getUSyncDevices([meu], true, false)" in bloco, \
        "a consulta voltou a forçar ida à rede (useCache=false)"


def test_a_rota_de_aparelhos_e_de_LEITURA_so():
    """Ela não pode iniciar, reiniciar nem derrubar sessão. Sessão de pé responde;
    sessão fora do ar devolve 'desconectado' e pronto — RELIGAR aqui seria mexer
    numa conexão que ninguém pediu pra mexer."""
    bloco = _rota_aparelhos()
    for proibido in ("iniciarSessao", "descartarSocket", "logout", "sendMessage",
                     "ws.close", "end("):
        assert proibido not in bloco, f"a consulta de aparelhos chama {proibido}"
    assert "req.method === 'GET'" in bloco, "tem que ser GET: é leitura"


def test_a_sessao_fora_do_ar_nao_e_religada_pra_contar_aparelho():
    """O `/enviar` religa a sessão porque tem uma mensagem pra entregar. Contar
    aparelho não justifica acordar nada."""
    bloco = _rota_aparelhos()
    assert "'desconectado'" in bloco
    assert "aguardando_qr" not in bloco, "está esperando reconexão — é o laço do /enviar"


# ══════════════════════════════ o que a leitura devolve

def test_o_numero_separa_o_celular_e_o_zaq_do_resto():
    """"3 aparelhos" não diz nada sozinho: um é o celular dono, outro é o próprio
    Zaq. O que interessa é o RESTO, e é ele que a tela destaca."""
    bloco = _rota_aparelhos()
    for campo in ("total:", "celular:", "zaq:", "outros:"):
        assert campo in bloco, f"faltou {campo} na resposta"
