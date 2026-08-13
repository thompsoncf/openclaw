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
"""
import re
from pathlib import Path

import pytest

_SERVER_JS = Path(__file__).resolve().parent.parent / "services" / "wa-qr" / "server.js"


@pytest.fixture(scope="module")
def bloco_logout() -> str:
    """O trecho do handler que roda quando o WhatsApp desloga (401)."""
    fonte = _SERVER_JS.read_text(encoding="utf-8")
    ini = fonte.index("      if (deslogado) {")
    fim = fonte.index("sessoes.delete(contaId)", ini)
    return fonte[ini:fim]


def test_logout_fecha_o_socket(bloco_logout):
    assert ".end(" in bloco_logout, (
        "o logout não fecha o socket — ele vira zumbi e continua mandando "
        "contatos/histórico pro webhook depois da sessão ter sido descartada"
    )


def test_logout_marca_descartado_antes_de_fechar(bloco_logout):
    """O próprio end() emite um último 'close'. Sem marcar _descartado antes,
    esse evento reentra no handler e refaz a limpeza."""
    pos_flag = bloco_logout.index("_descartado")
    pos_end = bloco_logout.index(".end(")
    assert pos_flag < pos_end, "_descartado precisa ser marcado ANTES do end()"


def test_fecha_socket_antes_de_apagar_credencial(bloco_logout):
    """Ordem importa: enquanto o socket estiver vivo ele pode regravar estado,
    então limpar primeiro deixaria lixo pra trás."""
    pos_end = bloco_logout.index(".end(")
    pos_limpar = bloco_logout.index("limparTudo()")
    assert pos_end < pos_limpar, "fechar o socket tem que vir antes do limparTudo()"


def test_fechamento_do_socket_nao_derruba_o_logout(bloco_logout):
    """Se o end() explodir, a credencial ainda tem que ser limpa — senão um erro
    ao fechar deixa a conta sem conseguir parear de novo."""
    trecho = bloco_logout[bloco_logout.index("_descartado"):]
    antes_do_limpar = trecho[:trecho.index("limparTudo()")]
    assert re.search(r"catch\s*\(", antes_do_limpar), (
        "o fechamento do socket precisa estar em try/catch pra não abortar a limpeza"
    )
