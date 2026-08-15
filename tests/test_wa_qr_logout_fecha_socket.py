"""Regressão: no logout do WhatsApp por QR, o socket precisa ser FECHADO.

O que aconteceu em produção (conta recém-conectada): o WhatsApp mandou um
`loggedOut` (401). O serviço apagou a credencial (`limparTudo`) e tirou a
sessão do mapa (`sessoes.delete`) — mas nunca fechou o socket do Baileys. Ele
seguiu vivo, com os listeners escutando, despejando contatos e histórico no
webhook usando o mesmo conta_id. O sintoma no banco era contraditório e
confuso de diagnosticar: `wa_qr_auth` ZERADA (nenhuma credencial) enquanto
`wa_contatos` crescia centenas por minuto — e nenhuma conversa entrando.

O arquivo já tinha o antídoto pra esse caso quando TROCA de socket
(`_descartado` + `end()`), mas o caminho do logout não usava.

Este teste lê o `server.js` de VERDADE e garante a ordem: fecha o socket antes
de apagar a credencial. É um guarda estrutural — o serviço é Node e não tem
suíte própria aqui (precisaria subir o Baileys inteiro), então o que dá pra
travar de forma barata e confiável é o invariante no código.

O fechamento foi EXTRAÍDO pra `descartarSocket()` quando o serviço começou a
estourar a memória do Render: os cinco lugares que fechavam socket repetiam o
mesmo `_descartado` + `end()` em try/catch, e o ramo de reconexão nem fechava
(deixava pra 2,5s depois, no `iniciarSessao` seguinte — num flapping isso
empilhava um socket vivo por ciclo). Então o invariante agora se verifica em
duas partes: o chamador fecha na ordem certa, e o helper fecha do jeito certo.
"""
import re
from pathlib import Path

import pytest

_SERVER_JS = Path(__file__).resolve().parent.parent / "services" / "wa-qr" / "server.js"

# Como o fechamento aparece no código: a chamada do helper ou, se um dia alguém
# voltar a escrever na mão, o `end()` inline. Os dois contam como "fechou aqui" —
# o que o teste trava é a ORDEM e o cuidado, não qual das duas formas foi usada.
_FECHA = re.compile(r"descartarSocket\s*\(|\.end\s*\(")


def _pos_fechamento(bloco: str, onde: str) -> int:
    achado = _FECHA.search(bloco)
    assert achado, (
        f"{onde} não fecha o socket — ele vira zumbi e continua mandando "
        "contatos/histórico pro webhook depois da sessão ter sido descartada"
    )
    return achado.start()


@pytest.fixture(scope="module")
def fonte() -> str:
    return _SERVER_JS.read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def bloco_logout(fonte) -> str:
    """O trecho do handler que roda quando o WhatsApp desloga (401)."""
    ini = fonte.index("      if (deslogado) {")
    fim = fonte.index("sessoes.delete(contaId)", ini)
    return fonte[ini:fim]


@pytest.fixture(scope="module")
def bloco_reconexao(fonte) -> str:
    """O ramo de close que NÃO é logout: cai aqui e religa alguns segundos depois."""
    ini = fonte.index("iniciarSessao(contaId).catch((e) => log.error({ contaId, e: String(e) }, 'reconexão automática falhou'))")
    # do 'else' que abre o ramo até a chamada de religamento
    return fonte[fonte.rindex("} else {", 0, ini):ini]


@pytest.fixture(scope="module")
def helper_descartar(fonte) -> str:
    """O corpo de descartarSocket(), que é quem fecha de fato."""
    ini = fonte.index("function descartarSocket (")
    fim = fonte.index("\nfunction ", ini + 1)
    return fonte[ini:fim]


def test_logout_fecha_o_socket(bloco_logout):
    _pos_fechamento(bloco_logout, "o logout")


def test_reconexao_fecha_o_socket_antes_de_religar(bloco_reconexao):
    """Fechar só lá na frente, no iniciarSessao seguinte, deixava o socket velho
    vivo com os listeners presos por todo o intervalo de religamento — num
    flapping de conexão isso empilha um socket (e os caches do Baileys) por ciclo."""
    _pos_fechamento(bloco_reconexao, "o ramo de reconexão")


def test_descartar_marca_descartado_antes_de_fechar(helper_descartar):
    """O próprio end() emite um último 'close'. Sem marcar _descartado antes,
    esse evento reentra no handler e refaz a limpeza."""
    pos_flag = helper_descartar.index("_descartado")
    pos_end = helper_descartar.index(".end(")
    assert pos_flag < pos_end, "_descartado precisa ser marcado ANTES do end()"


def test_fecha_socket_antes_de_apagar_credencial(bloco_logout):
    """Ordem importa: enquanto o socket estiver vivo ele pode regravar estado,
    então limpar primeiro deixaria lixo pra trás."""
    pos_fecha = _pos_fechamento(bloco_logout, "o logout")
    pos_limpar = bloco_logout.index("limparTudo()")
    assert pos_fecha < pos_limpar, "fechar o socket tem que vir antes do limparTudo()"


def test_fechamento_do_socket_nao_derruba_o_logout(helper_descartar):
    """Se o end() explodir, a credencial ainda tem que ser limpa — senão um erro
    ao fechar deixa a conta sem conseguir parear de novo. Como o fechamento agora
    é do helper, é ele que precisa engolir a exceção: o try/catch aqui protege
    todos os chamadores de uma vez."""
    antes_do_fim = helper_descartar[helper_descartar.index("_descartado"):]
    assert re.search(r"catch\s*\(", antes_do_fim), (
        "o fechamento do socket precisa estar em try/catch pra não abortar a limpeza"
    )
