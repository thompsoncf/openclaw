"""De qual anúncio veio o lead — lido da própria mensagem que ele mandou.

A Prime atende num número só, por QR (Baileys). Anúncio, orgânico, bio do Instagram
e indicação caem todos no mesmo WhatsApp, e até 07/09/2026 não havia como separar:
`prospeccao.origem` tinha quatro valores grossos ('whatsapp_inbound' pra tudo que
chega) e `chip_id` estava nulo em 383 das 429 conversas da conta.

O caminho exato seria o clique-para-WhatsApp da API oficial, em que a Meta manda
`referral.source_id` junto da mensagem. Ele **não existe no QR** — e migrar o número
pra API oficial tira ele do celular e mexe numa sessão que entrega mensagem todo dia
(regra 1 da casa). Então a origem viaja onde ela consegue viajar: no texto.

O ANÚNCIO ESCREVE, O CLIENTE ENVIA. A mensagem pronta do anúncio já vem com o código
("Olá! Quero saber sobre o espaço. [#A3]"); a pessoa aperta enviar sem pensar no
assunto. Vale pra qualquer link de WhatsApp, não só anúncio da Meta: bio do
Instagram, Google Meu Negócio, site, QR de panfleto — tudo que monta um
`wa.me/...?text=` entra na mesma conta.

POR QUE A CERQUILHA É OBRIGATÓRIA. A primeira versão casava qualquer `[...]`, e isso
apagaria texto de gente: `[risos]`, `[foto]`, `[audio]` são coisas que cliente
escreve. Exigir `#` derruba o falso positivo a praticamente zero e não custa nada
pra quem monta o anúncio — o código é digitado uma vez, na criação do criativo.

É FORMATO, NÃO LISTA. Nada aqui sabe quais códigos existem: a agência sobe dez
criativos numa terça e na quarta os dez já aparecem no painel. Guardar uma lista
significaria que todo criativo novo nasce sem origem até alguém avisar a gente — uma
falha silenciosa, e a culpa cairia no Zaq.
"""
from __future__ import annotations

import re

#: `#` obrigatório; começa com letra ou dígito; até 24 no total. Ponto, hífen e
#: sublinhado passam porque nome de criativo costuma usá-los ("FORM-SET-01").
_CODIGO = re.compile(r"\[#([A-Za-z0-9][A-Za-z0-9._-]{0,23})\]")
_ESPACOS = re.compile(r"[ \t]{2,}")

TAMANHO_MAX = 24


def extrair(texto: str | None) -> tuple[str | None, str]:
    """Devolve `(código, texto sem o código)`. Sem código, devolve `(None, texto)`.

    Só o PRIMEIRO `[#...]` conta, e só ele sai do texto. Se o cliente escreveu
    colchetes por conta própria mais adiante, aquilo é mensagem dele e fica.

    O código volta em MAIÚSCULAS: `[#a3]` e `[#A3]` são o mesmo criativo, e sem
    normalizar um deslize de digitação no anúncio viraria duas linhas no painel.

    TEXTO VAZIO NUNCA SAI DAQUI. Se a mensagem era só o código, limpar deixaria
    string vazia — e mensagem sem texto é DESCARTADA lá no wa-qr
    (`services/wa-qr/server.js`, "entrada ignorada (sem texto...)"). A entrada do
    cliente se perderia pra sempre por causa de um enfeite de relatório, o que a
    regra 0 proíbe. Nesse caso o código é lido e o texto volta como veio.
    """
    original = texto or ""
    m = _CODIGO.search(original)
    if not m:
        return None, original
    limpo = original[:m.start()] + original[m.end():]
    limpo = _ESPACOS.sub(" ", limpo).strip()
    if not limpo:
        return m.group(1).upper(), original
    return m.group(1).upper(), limpo
