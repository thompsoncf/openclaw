"""O arquivo que a empresa decidiu GUARDAR (passo 5 da mídia).

O QUE ISTO É, E O QUE NÃO É

Os passos 1 a 4 nunca guardam arquivo: a mensagem traz o endereço no CDN do
WhatsApp e a chave, e quem abre a conversa busca lá na hora. Continua sendo a
escolha certa — medido em 28/08/2026, guardar tudo daria ~110 GB por ano numa
conta só contra 22 MB de ponteiro, e a maioria das fotos ninguém abre duas vezes.

Só que o CDN EXPIRA. Para uma foto de referência de decoração isso é aceitável:
pede de novo ao cliente. Para o comprovante do sinal e o contrato assinado não é —
são o registro do negócio, e o dia em que se precisa deles é justamente o dia da
discussão, meses depois.

Então: SELETIVO. O vendedor olha o comprovante, sabe que aquilo é o sinal do
evento, e aperta Guardar. Uma regra automática ou guardaria demais (o custo que já
foi recusado) ou de menos — e aí não daria pra confiar nela.

ONDE FICA
No mesmo bucket PRIVADO dos comprovantes de pagamento, pela mesma razão que aquele
é privado: comprovante tem nome, banco, valor e às vezes CPF. O banco guarda o
CAMINHO, nunca uma URL; quem entrega é a rota de mídia, que já confere sessão,
conta e dono do lead.

O CAMINHO ATÉ O STORAGE É O DE `comprovantes.subir_em` de propósito. As regras de
tamanho e formato mudam conforme o assunto — comprovante de banco não é vídeo de
salão —, mas a chave de serviço, o bucket privado e o tratamento de erro são os
mesmos. Dois canos até o Storage é como um deles vira público sem ninguém notar.
"""
from __future__ import annotations

import logging
import time
import uuid

_log = logging.getLogger("openclaw.midia_cofre")

# Teto do que se guarda, por tipo. MENOR que o teto de ENVIO (32 MB pro vídeo) e
# isso é de propósito: enviar é passageiro — o arquivo atravessa a memória e vai
# embora —, guardar é para sempre e ocupa disco que alguém paga todo mês. O que
# vira registro do negócio é comprovante, contrato e orçamento: PDF e foto, coisa
# de centenas de KB. Vídeo cabe, mas com teto curto pra não virar arquivo morto.
TETO = {"imagem": 8 * 1024 * 1024, "documento": 16 * 1024 * 1024,
        "video": 16 * 1024 * 1024, "figurinha": 2 * 1024 * 1024}

_EXT = {"image/jpeg": "jpg", "image/png": "png", "image/webp": "webp",
        "application/pdf": "pdf", "video/mp4": "mp4", "video/quicktime": "mov"}


def configurado() -> bool:
    """Dá pra guardar nesta instalação? A tela pergunta antes de mostrar o botão —
    melhor não ter botão do que ter botão que engole o comprovante do cliente."""
    from finance import comprovantes
    return comprovantes.configurado()


def _extensao(mimetype: str, nome: str) -> str:
    """A extensão do objeto no bucket.

    Importa mais do que parece: quem abrir o arquivo meses depois vai baixar por
    uma rota nossa, e é a extensão que faz o sistema operacional saber abrir. Um
    contrato salvo como `.bin` é um contrato que ninguém abre no dia da discussão.
    """
    ct = (mimetype or "").split(";")[0].strip().lower()
    if ct in _EXT:
        return _EXT[ct]
    fim = (nome or "").rsplit(".", 1)
    if len(fim) == 2 and 1 <= len(fim[1]) <= 5 and fim[1].isalnum():
        return fim[1].lower()
    return "bin"


def juntar(pedacos, teto: int) -> bytes:
    """Junta o fluxo do CDN em bytes, parando no teto.

    PARA no teto em vez de conferir depois: um arquivo de 300 MB baixado inteiro
    pra só então ser recusado gastaria a banda e a memória do servidor à toa — e o
    ponto do desenho em fluxo era exatamente não segurar arquivo grande.
    """
    partes, total = [], 0
    for p in pedacos:
        total += len(p)
        if total > teto:
            raise ValueError(f"Arquivo passa de {teto // (1024 * 1024)} MB.")
        partes.append(p)
    if not total:
        raise ValueError("Arquivo vazio.")
    return b"".join(partes)


def guardar(pedacos, *, conta_id: int, mensagem_id: int, tipo: str,
            mimetype: str, nome: str = "") -> tuple[str, int]:
    """Busca o fluxo, sobe pro bucket privado e devolve (caminho, bytes).

    O caminho leva a CONTA no começo, igual ao dos comprovantes: a leitura confere
    que o objeto é da conta que pediu (defesa em profundidade, além do WHERE do
    banco), e um dia dá pra apagar tudo de uma conta por prefixo.
    """
    from finance import comprovantes
    teto = TETO.get(tipo)
    if not teto:
        raise ValueError(f"Não sei guardar mídia do tipo {tipo!r}.")
    conteudo = juntar(pedacos, teto)
    ext = _extensao(mimetype, nome)
    caminho = (f"conversa/{conta_id}/{mensagem_id}"
               f"-{int(time.time())}-{uuid.uuid4().hex[:8]}.{ext}")
    ct = (mimetype or "").split(";")[0].strip() or "application/octet-stream"
    comprovantes.subir_em(caminho, conteudo, ct)
    _log.info("mídia %s guardada (conta %s, %d bytes)", mensagem_id, conta_id, len(conteudo))
    return caminho, len(conteudo)


def ler(caminho: str) -> tuple[bytes, str]:
    """(bytes, content-type) do arquivo guardado — pela chave de serviço.

    Quem checa PERMISSÃO é a rota, antes de chamar aqui. Este módulo não conhece
    sessão, e misturar as duas coisas é como um bucket privado volta a ser público
    por acidente (a mesma nota está em `comprovantes.ler`, e vale igual)."""
    from finance import comprovantes
    return comprovantes.ler(caminho)
