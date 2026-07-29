"""Leitura de QR code de NFC-e (Nota Fiscal do Consumidor Eletrônica).

Extrai chave fiscal de foto ou PDF usando OpenCV (imagem) e PyMuPDF (PDF).
Tolerante a falha: qualquer erro retorna None e o fluxo continua.
"""
from __future__ import annotations

import threading
from datetime import datetime, date

# Cache dos detectores POR THREAD. Instanciar o WeChatQRCode carrega 2 redes
# neurais (detect + super-resolucao, ~arquivos caffemodel) - caro em CPU e,
# principalmente, em MEMORIA. A leitura do QR roda em threads separadas
# (threading.Thread / asyncio.to_thread), e cv2.dnn.Net NAO e' thread-safe,
# entao NAO da' pra compartilhar UMA instancia global entre threads. Um cache
# thread-local resolve os dois lados: cada thread constroi UMA vez e reusa
# (evita recarregar os modelos a cada foto/pagina de PDF) sem corrida entre
# threads. Antes isso vazava: um PDF de 3 paginas reinstanciava as redes 3x.
_tls = threading.local()


def deve_mandar_dica_qr(dica_qr: bool, chave_nfce, tools_usadas) -> bool:
    """A dica de QR ('deixe o QR visivel') so' faz sentido em CUPOM FISCAL - que
    tem QR. Comprovante de Pix/banco NAO tem QR, entao nao deve receber a dica
    (a mensagem tem que bater com o que a pessoa enviou).

    Sinais de que foi um cupom fiscal: a chave foi lida (tem QR) OU o agente
    registrou itens (registrar_itens_cupom). Pix/comprovante nao aciona nenhum."""
    if not dica_qr:
        return False
    if chave_nfce:
        return True
    try:
        return "registrar_itens_cupom" in (tools_usadas or ())
    except TypeError:
        return False


def extrair_chave(texto: str) -> str | None:
    """Extrai a chave de 44 digitos de uma URL de QR OU do TEXTO de um cupom.
    Aceita a chave colada a rotulo ('CHAVE DE ACESSO2226...') e em grupos de 4
    com espacos. Valida o modelo 65 (NFC-e) / 55 (NF-e) nas posicoes 20-21."""
    if not texto:
        return None
    import re
    # 1) URL do QR: a chave vem como p=<44> ou chNFe=<44> (o mais confiavel).
    m = re.search(r"[?&]p=(\d{44})", texto) or re.search(r"chNFe=(\d{44})", texto)
    if m and m.group(1)[20:22] in ("65", "55"):
        return m.group(1)
    # 2) Texto solto (cupom/DANFE): procura runs de 44+ digitos apos remover os
    # espacos e valida o modelo. NAO usa \b (rotulo grudado quebra o boundary).
    for run in re.findall(r"\d{44,}", "".join(texto.split())):
        for s in range(0, len(run) - 43):
            cand = run[s:s + 44]
            if cand[20:22] in ("65", "55"):
                return cand
    return None


def chave_dv_valida(chave: str) -> bool:
    """Valida o digito verificador (mod 11) de uma chave NFC-e/NF-e de 44 digitos.
    Pega quase todo erro de 1 digito - essencial quando a chave vem da VISAO (o
    agente leu do texto do cupom), nao do QR (que ja' e' confiavel)."""
    if not chave or len(chave) != 44 or not chave.isdigit():
        return False
    corpo, dv = chave[:43], int(chave[43])
    peso, soma = 2, 0
    for d in reversed(corpo):
        soma += int(d) * peso
        peso = 2 if peso == 9 else peso + 1
    resto = soma % 11
    calc = 0 if resto in (0, 1) else 11 - resto
    return calc == dv


def _detectores():
    """Monta os detectores de QR disponiveis (cacheados). O WeChat detector e'
    muito superior pra QR degradado (foto comprimida de WhatsApp/Telegram), mas
    SO' funciona de verdade com os arquivos de modelo - por isso os embarcamos
    em finance/wechat_models/ e passamos explicitamente (senao, dependendo do
    build do opencv, ele instancia mas nao decodifica - foi o bug do Render).

    Cacheado POR THREAD (_tls): constroi UMA vez por thread e reusa. Sem isso,
    cada leitura reinstanciava as redes neurais (detect + super-resolucao),
    estourando a memoria do servico sob carga (o alerta de OOM do Render)."""
    cached = getattr(_tls, "dets", None)
    if cached is not None:
        return cached
    import cv2
    import os
    dets = []
    try:
        base = os.path.join(os.path.dirname(__file__), "wechat_models")
        det_proto = os.path.join(base, "detect.prototxt")
        det_model = os.path.join(base, "detect.caffemodel")
        sr_proto = os.path.join(base, "sr.prototxt")
        sr_model = os.path.join(base, "sr.caffemodel")
        if all(os.path.exists(p) for p in (det_proto, det_model, sr_proto, sr_model)):
            dets.append(("wechat", cv2.wechat_qrcode.WeChatQRCode(
                det_proto, det_model, sr_proto, sr_model)))
        else:
            dets.append(("wechat", cv2.wechat_qrcode.WeChatQRCode()))
    except Exception:  # noqa: BLE001
        pass
    dets.append(("padrao", cv2.QRCodeDetector()))
    _tls.dets = dets
    return dets


def medir_imagem(dados: bytes, media_type: str = "image/jpeg") -> dict:
    """Mede a imagem que chegou: largura, altura, tamanho em bytes. Pra auditar
    a QUALIDADE real da foto que o app de mensagens entrega. Tolerante a falha."""
    info = {"bytes": len(dados) if dados else 0, "largura": None, "altura": None}
    if media_type == "application/pdf":
        return info
    try:
        import cv2
        import numpy as np
        arr = cv2.imdecode(np.frombuffer(dados, np.uint8), cv2.IMREAD_COLOR)
        if arr is not None:
            info["altura"], info["largura"] = arr.shape[0], arr.shape[1]
    except Exception:  # noqa: BLE001
        pass
    return info


def _variantes_preproc(arr):
    """Gera versoes pre-processadas da imagem pra ajudar a leitura de QR em fotos
    DIFICEIS (escuras, baixo contraste, amassadas). Cada tecnica realca o QR de um
    jeito; tentamos varias porque cada foto ruim falha de um jeito diferente.
    Retorna lista de imagens (numpy arrays) pra tentar ler, alem da original.
    Tolerante a falha: se cv2 nao tiver algo, pula aquela variante."""
    out = []
    try:
        import cv2
        import numpy as np
    except Exception:  # noqa: BLE001
        return out
    try:
        gray = cv2.cvtColor(arr, cv2.COLOR_BGR2GRAY)
    except Exception:  # noqa: BLE001
        return out

    # 1. CLAHE: equalizacao de contraste adaptativa (resolve foto ESCURA/desigual)
    try:
        clahe = cv2.createCLAHE(clipLimit=3.0, tileGridSize=(8, 8))
        eq = clahe.apply(gray)
        out.append(cv2.cvtColor(eq, cv2.COLOR_GRAY2BGR))
    except Exception:  # noqa: BLE001
        pass

    # 2. Threshold adaptativo: vira preto-e-branco puro (o QR e' P&B por natureza)
    try:
        th = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
                                   cv2.THRESH_BINARY, 31, 5)
        out.append(cv2.cvtColor(th, cv2.COLOR_GRAY2BGR))
    except Exception:  # noqa: BLE001
        pass

    # 3. Otsu: outro threshold (bom quando a iluminacao e' mais uniforme)
    try:
        _t, oth = cv2.threshold(gray, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
        out.append(cv2.cvtColor(oth, cv2.COLOR_GRAY2BGR))
    except Exception:  # noqa: BLE001
        pass

    # 4. Sharpen: realca bordas (recupera QR levemente borrado)
    try:
        blur = cv2.GaussianBlur(gray, (0, 0), 3)
        sharp = cv2.addWeighted(gray, 1.5, blur, -0.5, 0)
        out.append(cv2.cvtColor(sharp, cv2.COLOR_GRAY2BGR))
    except Exception:  # noqa: BLE001
        pass

    return out


def ler_chave_da_imagem(imagem_bytes: bytes) -> str | None:
    """Le o QR code de uma imagem (bytes) e devolve a chave de 44 digitos.
    None se nao houver QR legivel ou nao for uma NFC-e. Tolerante a falha.

    Foto de cupom (WhatsApp/Telegram) chega COMPRIMIDA, com o QR pequeno e
    degradado. Pra maximizar a leitura combinamos: detector WeChat (otimo pra
    QR ruim) + detector padrao; na imagem inteira, ampliada, e em recortes da
    regiao central ampliados em varias escalas."""
    try:
        import cv2
        import numpy as np
    except Exception:  # noqa: BLE001
        return None
    try:
        arr = cv2.imdecode(np.frombuffer(imagem_bytes, np.uint8), cv2.IMREAD_COLOR)
        if arr is None:
            return None
    except Exception:  # noqa: BLE001
        return None

    dets = _detectores()

    def _le(img) -> str | None:
        for nome, det in dets:
            try:
                if nome == "wechat":
                    res, _pts = det.detectAndDecode(img)
                    for d in (res or []):
                        ch = extrair_chave(d)
                        if ch:
                            return ch
                else:
                    data, _pts, _ = det.detectAndDecode(img)
                    if data:
                        ch = extrair_chave(data)
                        if ch:
                            return ch
                    ok, datas, _p, _s = det.detectAndDecodeMulti(img)
                    if ok and datas:
                        for d in datas:
                            ch = extrair_chave(d)
                            if ch:
                                return ch
            except Exception:  # noqa: BLE001
                continue
        return None

    if (ch := _le(arr)):
        return ch
    try:
        if (ch := _le(cv2.resize(arr, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC))):
            return ch
    except Exception:  # noqa: BLE001
        pass
    try:
        h, w = arr.shape[:2]
        cortes = [
            arr[int(h * 0.45):int(h * 0.78), int(w * 0.18):int(w * 0.82)],
            arr[int(h * 0.35):int(h * 0.85), int(w * 0.10):int(w * 0.90)],
            arr[int(h * 0.50):, int(w * 0.15):int(w * 0.85)],
        ]
        for corte in cortes:
            if corte.size == 0:
                continue
            for fx in (3, 4, 5):
                grande = cv2.resize(corte, None, fx=fx, fy=fx, interpolation=cv2.INTER_CUBIC)
                if (ch := _le(grande)):
                    return ch
    except Exception:  # noqa: BLE001
        pass

    # ULTIMA cartada: variantes pre-processadas (contraste/threshold/sharpen),
    # pra fotos ESCURAS, de baixo contraste ou amassadas. Tenta cada variante na
    # imagem inteira, ampliada 2x, e nos recortes centrais ampliados.
    try:
        for var in _variantes_preproc(arr):
            if (ch := _le(var)):
                return ch
            try:
                if (ch := _le(cv2.resize(var, None, fx=2, fy=2,
                                         interpolation=cv2.INTER_CUBIC))):
                    return ch
            except Exception:  # noqa: BLE001
                pass
            try:
                hv, wv = var.shape[:2]
                corte_v = var[int(hv * 0.40):, int(wv * 0.10):int(wv * 0.90)]
                if corte_v.size:
                    for fx in (3, 4):
                        gv = cv2.resize(corte_v, None, fx=fx, fy=fx,
                                        interpolation=cv2.INTER_CUBIC)
                        if (ch := _le(gv)):
                            return ch
            except Exception:  # noqa: BLE001
                pass
    except Exception:  # noqa: BLE001
        pass
    return None


def ler_chave_de_pdf(pdf_bytes: bytes, max_paginas: int = 3) -> str | None:
    """Le a chave de uma NFC-e/NF-e em PDF. Tenta primeiro o TEXTO do PDF (a
    DANFE digital traz a chave de 44 digitos escrita, as vezes em grupos de 4) e,
    so' se nao achar, renderiza a pagina e le o QR. extrair_chave valida o modelo
    (65/55), entao numero solto nao vira falso positivo. Tolerante a falha."""
    try:
        import fitz  # PyMuPDF
    except Exception:  # noqa: BLE001
        return None
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:  # noqa: BLE001
        return None
    try:
        # 1) TEXTO (custo zero): a DANFE digital traz a chave de 44 digitos escrita
        # (as vezes em grupos de 4). Procura runs de 44+ digitos (apos remover os
        # espacos) e valida o modelo 65/55 nas posicoes 20-21, igual extrair_chave.
        import re as _re
        for i in range(min(max_paginas, doc.page_count)):
            try:
                txt = "".join((doc[i].get_text() or "").split())
            except Exception:  # noqa: BLE001
                txt = ""
            for run in _re.findall(r"\d{44,}", txt):
                for s in range(0, len(run) - 43):
                    cand = run[s:s + 44]
                    if cand[20:22] in ("65", "55"):
                        return cand
        # 2) QR renderizado (pega PDF escaneado/sem texto, como antes).
        for i in range(min(max_paginas, doc.page_count)):
            try:
                png = doc[i].get_pixmap(dpi=200).tobytes("png")
            except Exception:  # noqa: BLE001
                continue
            chave = ler_chave_da_imagem(png)
            if chave:
                return chave
        return None
    finally:
        doc.close()


def ler_chave(conteudo: bytes, media_type: str = "image/jpeg") -> str | None:
    """Ponto unico: le a chave de uma foto OU de um PDF, conforme o media_type."""
    if media_type == "application/pdf":
        return ler_chave_de_pdf(conteudo)
    return ler_chave_da_imagem(conteudo)


_UF = {
    "11": "RO", "12": "AC", "13": "AM", "14": "RR", "15": "PA", "16": "AP",
    "17": "TO", "21": "MA", "22": "PI", "23": "CE", "24": "RN", "25": "PB",
    "26": "PE", "27": "AL", "28": "SE", "29": "BA", "31": "MG", "32": "ES",
    "33": "RJ", "35": "SP", "41": "PR", "42": "SC", "43": "RS", "50": "MS",
    "51": "MT", "52": "GO", "53": "DF",
}


def metadados(chave: str) -> dict:
    """Extrai UF, data aproximada (AAMM->1o dia do mes) e CNPJ do emitente."""
    if not chave or len(chave) != 44:
        return {}
    uf = _UF.get(chave[0:2])
    aa, mm = chave[2:4], chave[4:6]
    try:
        data_emissao = date(2000 + int(aa), int(mm), 1)
    except (ValueError, TypeError):
        data_emissao = None
    cnpj = chave[6:20]
    return {"uf": uf, "data_emissao": data_emissao, "cnpj_emitente": cnpj}


def registrar_leitura(pool, conta_id, chave, media_type, img_info=None):
    """Grava a auditoria de uma foto/PDF recebido (leu QR ou nao), incluindo a
    qualidade da imagem (dimensoes/bytes). Tolerante a falha."""
    try:
        m = metadados(chave) if chave else {}
        info = img_info or {}
        if chave:
            classe = "cupom_lido"
        elif (media_type or "") == "application/pdf":
            classe = "pdf_indefinido"
        elif (media_type or "").startswith("image/"):
            classe = "cupom_sem_qr"
        else:
            classe = "outro"
        with pool.connection() as c:
            c.execute(
                """insert into qr_leituras
                   (conta_id, chave, uf, cnpj_emitente, data_emissao, media_type,
                    leu, img_largura, img_altura, img_bytes, classe)
                   values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)""",
                (conta_id, chave, m.get("uf"), m.get("cnpj_emitente"),
                 m.get("data_emissao"), media_type, bool(chave),
                 info.get("largura"), info.get("altura"), info.get("bytes"), classe),
            )
            c.commit()
    except Exception:  # noqa: BLE001
        pass
