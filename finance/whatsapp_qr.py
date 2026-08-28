"""Cliente do serviço de WhatsApp por QR Code (services/wa-qr, Node/Baileys).

O app só conversa por HTTP com o serviço externo — nada de sessão aqui. Tolerante:
sem WA_QR_SERVICE_URL/WA_QR_SHARED_SECRET, tudo vira no-op (o painel mostra "a
configurar" e o envio devolve erro amigável). Só stdlib.

Env:
- WA_QR_SERVICE_URL   — URL pública do serviço Node (ex.: https://...onrender.com)
- WA_QR_SHARED_SECRET — segredo compartilhado (header x-wa-secret)
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request

_TIMEOUT = 20


def _base() -> str:
    return (os.environ.get("WA_QR_SERVICE_URL") or "").rstrip("/")


def _segredo() -> str:
    return os.environ.get("WA_QR_SHARED_SECRET") or ""


def configurado() -> bool:
    """O serviço de QR está ligado (env presentes)?"""
    return bool(_base() and _segredo())


def _req(metodo: str, caminho: str, corpo: dict | None = None,
         timeout: int | None = None) -> dict:
    if not configurado():
        return {"ok": False, "erro": "qr_indisponivel"}
    url = _base() + caminho
    data = json.dumps(corpo).encode("utf-8") if corpo is not None else None
    headers = {"x-wa-secret": _segredo()}
    if data is not None:
        headers["content-type"] = "application/json"
    try:
        req = urllib.request.Request(url, data=data, headers=headers, method=metodo)
        with urllib.request.urlopen(req, timeout=timeout or _TIMEOUT) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:  # noqa: BLE001
        try:
            det = e.read().decode("utf-8")[:180]
        except Exception:  # noqa: BLE001
            det = str(e)
        # sessão caída, serviço fora do ar, número banido: o QR carrega 98% do
        # volume e até agora nenhuma dessas falhas chegava ao admin.
        from core.falhas import avaliar_falha_provedor
        avaliar_falha_provedor(f"http_{e.code}: {det}", servico="WhatsApp (QR)",
                               canal="whatsapp")
        return {"ok": False, "erro": f"http_{e.code}", "det": det}
    except Exception as e:  # noqa: BLE001
        from core.falhas import avaliar_falha_provedor
        avaliar_falha_provedor(e, servico="WhatsApp (QR)", canal="whatsapp")
        return {"ok": False, "erro": str(e)[:180]}


def iniciar(conta_id: int) -> dict:
    """Liga a sessão e devolve {status, qr?} (qr = data URL pra exibir)."""
    return _req("POST", f"/session/{conta_id}/iniciar")


def status(conta_id: int) -> dict:
    """{status: conectado|aguardando_qr|reconectando|desconectado, qr?}."""
    return _req("GET", f"/session/{conta_id}/status")


def enviar_texto(conta_id: int, numero: str, texto: str) -> dict:
    """Manda um texto pelo número conectado por QR daquela empresa."""
    r = _req("POST", f"/session/{conta_id}/enviar", {"numero": numero, "texto": texto})
    # normaliza pro formato dos outros adaptadores ({ok, sid?/erro})
    if r.get("ok"):
        return {"ok": True, "sid": r.get("id") or ""}
    return {"ok": False, "erro": r.get("erro") or "falha"}


def aparelhos(conta_id: int) -> dict:
    """Quantos aparelhos estão ligados neste WhatsApp.

    É a pergunta que sobra depois de o Cockpit parar de oferecer a saída pro
    celular: o app deixou de convidar, mas quem já tem o número ligado no aparelho
    continua respondendo por fora — e o que sai por fora chega sem nome.

    {ok, total, celular, zaq, outros} — `outros` é o número que interessa, e
    desligar cada um é decisão de quem é dono da conta, no celular dele. Nenhum
    sistema faz isso por ninguém: aqui só se MOSTRA.

    Tolerante: sessão fora do ar devolve {ok: False}, e a tela some com o bloco em
    vez de afirmar zero — dizer "nenhum aparelho ligado" sem ter perguntado seria
    pior que não dizer nada."""
    return _req("GET", f"/session/{conta_id}/aparelhos")


def enviar_audio(conta_id: int, numero: str, dados: bytes, mimetype: str,
                 segundos: int, onda: bytes | None = None) -> dict:
    """Manda um áudio de voz gravado no Zaq pelo número conectado por QR.

    O corpo é BINÁRIO puro, não JSON com base64: base64 custa +33% de memória e de
    rede dos dois lados, e o serviço Node roda com --max-old-space-size=1024. Os
    metadados vão na query; a onda (64 bytes) num cabeçalho.

    `segundos` e `onda` vêm da TELA porque o Baileys só decodifica o áudio quando
    falta um deles — e o decodificador de m4a dele falha, o que quebraria o
    iPhone. Mandando prontos, nenhum aparelho precisa de conversão no servidor.
    """
    import base64
    from urllib.parse import urlencode
    if not configurado():
        return {"ok": False, "erro": "qr_indisponivel"}
    q = urlencode({"numero": numero, "mime": mimetype, "seg": max(1, int(segundos))})
    url = f"{_base()}/session/{conta_id}/enviar-audio?{q}"
    headers = {"x-wa-secret": _segredo(), "content-type": "application/octet-stream"}
    if onda and len(onda) == 64:
        headers["x-wa-onda"] = base64.b64encode(onda).decode("ascii")
    try:
        req = urllib.request.Request(url, data=dados, headers=headers, method="POST")
        # prazo maior que o padrão: o Baileys ainda cifra e SOBE a mídia pro
        # servidor do WhatsApp antes de responder — não é só um texto saindo.
        with urllib.request.urlopen(req, timeout=45) as r:
            resp = json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:  # noqa: BLE001
        from core.falhas import avaliar_falha_provedor
        avaliar_falha_provedor(f"http_{e.code}", servico="WhatsApp (QR)", canal="whatsapp")
        return {"ok": False, "erro": f"http_{e.code}"}
    except Exception as e:  # noqa: BLE001
        from core.falhas import avaliar_falha_provedor
        avaliar_falha_provedor(e, servico="WhatsApp (QR)", canal="whatsapp")
        return {"ok": False, "erro": str(e)[:180]}
    if resp.get("ok"):
        return {"ok": True, "sid": resp.get("id") or ""}
    return {"ok": False, "erro": resp.get("erro") or "falha"}


def enviar_midia(conta_id: int, numero: str, dados: bytes, tipo: str, mimetype: str,
                 nome: str = "", legenda: str = "") -> dict:
    """Manda foto, vídeo ou documento que o vendedor anexou no Zaq.

    Mesmo formato do áudio — binário puro com os metadados na query — pelo mesmo
    motivo: base64 custaria +33% de memória e de rede, e do outro lado está o
    processo que segura as sessões de WhatsApp.

    `conta_id` aqui é a SESSÃO por onde isto sai, não a empresa. Numa empresa de
    dois chips quem chama tem que passar o chip da conversa (`chip_id or conta_id`,
    como o texto já faz) — mandar pelo chip errado faz o cliente receber de um
    número que não é o daquela conversa, e o eco da mensagem volta pra ficha do
    colega. Isso não é hipótese: aconteceu com o áudio até 28/08/2026.
    """
    from urllib.parse import urlencode
    if not configurado():
        return {"ok": False, "erro": "qr_indisponivel"}
    if not dados:
        return {"ok": False, "erro": "vazio_ou_grande"}
    q = urlencode({"numero": numero, "tipo": tipo, "mime": mimetype or "",
                   "nome": (nome or "")[:160], "legenda": (legenda or "")[:1000]})
    url = f"{_base()}/session/{conta_id}/enviar-midia?{q}"
    headers = {"x-wa-secret": _segredo(), "content-type": "application/octet-stream"}
    try:
        req = urllib.request.Request(url, data=dados, headers=headers, method="POST")
        # 90s, e não os 45 do áudio: aqui pode ser um vídeo de 16 MB, e o Baileys
        # ainda cifra e SOBE o arquivo pro WhatsApp antes de responder. Prazo curto
        # faria o vendedor ver "falhou" numa mensagem que na verdade saiu.
        with urllib.request.urlopen(req, timeout=90) as r:
            resp = json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:  # noqa: BLE001
        from core.falhas import avaliar_falha_provedor
        avaliar_falha_provedor(f"http_{e.code}", servico="WhatsApp (QR)", canal="whatsapp")
        return {"ok": False, "erro": f"http_{e.code}"}
    except Exception as e:  # noqa: BLE001
        from core.falhas import avaliar_falha_provedor
        avaliar_falha_provedor(e, servico="WhatsApp (QR)", canal="whatsapp")
        return {"ok": False, "erro": str(e)[:180]}
    if resp.get("ok"):
        # `midia` é o ponteiro do arquivo que o Baileys acabou de subir — o mesmo
        # formato da entrada. É ele que faz a foto enviada APARECER na conversa.
        return {"ok": True, "sid": resp.get("id") or "", "midia": resp.get("midia")}
    return {"ok": False, "erro": resp.get("erro") or "falha"}


def sair(conta_id: int) -> dict:
    """Desconecta e apaga a sessão daquela empresa.

    Prazo maior que o padrão: o serviço só responde depois de avisar o celular,
    apagar a credencial (que são milhares de linhas em wa_qr_auth) e limpar o
    histórico de conversa. Com os 20s padrão isso estourava em conta grande, e o
    painel dizia "Desconectado" com a sessão inteira ainda de pé."""
    return _req("POST", f"/session/{conta_id}/sair", timeout=60)
