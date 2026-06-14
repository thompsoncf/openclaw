"""Leitura de QR code de NFC-e (Nota Fiscal do Consumidor Eletrônica).

Extrai chave fiscal de foto ou PDF usando OpenCV (imagem) e PyMuPDF (PDF).
Tolerante a falha: qualquer erro retorna None e o fluxo continua.
"""
from __future__ import annotations

from datetime import datetime, date


def ler_chave_da_imagem(imagem_bytes: bytes) -> str | None:
    """Le o QR de uma imagem JPEG/PNG e retorna a chave da NFC-e (44 digitos).
    Usa OpenCV + pyzbar. Retorna None se nao conseguir."""
    try:
        import cv2
        import numpy as np
    except ImportError:
        return None
    try:
        nparr = np.frombuffer(imagem_bytes, np.uint8)
        img = cv2.imdecode(nparr, cv2.IMREAD_COLOR)
        if img is None:
            return None
        try:
            from pyzbar.pyzbar import decode
        except ImportError:
            return None
        barcodes = decode(img)
        for barcode in barcodes:
            texto = barcode.data.decode("utf-8", "ignore").strip()
            if len(texto) == 44 and texto.isdigit():
                return texto
        return None
    except Exception:  # noqa: BLE001
        return None


def ler_chave_de_pdf(pdf_bytes: bytes, max_paginas: int = 3) -> str | None:
    """Le o QR de um PDF de NFC-e: converte as primeiras paginas em imagem
    (PyMuPDF, sem dependencia de poppler) e procura a chave. Tolerante a falha."""
    try:
        import fitz  # PyMuPDF
    except Exception:  # noqa: BLE001
        return None
    try:
        doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    except Exception:  # noqa: BLE001
        return None
    try:
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


def metadados(chave: str) -> dict:
    """Extrai metadados da chave fiscal (UF, data emissao, etc).
    Formatos: chave é 44 digitos = AABBCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCCC
    Posicoes: 0-1 = ano, 2-3 = mes, 4-7 = CNPJ emitente (primeiros 4), ...
    Simplificado: retorna UF a partir de lookup."""
    if len(chave) != 44:
        return {}
    try:
        ano = int(chave[0:2])
        mes = int(chave[2:4])
        uf_codigo = int(chave[4:6])
        ano_completo = 2000 + ano if ano < 100 else ano
        dt = date(ano_completo, mes, 1) if 1 <= mes <= 12 else None
        uf_map = {
            11: "RO", 12: "AC", 13: "AM", 14: "RR", 15: "PA", 16: "AP", 17: "TO",
            21: "MA", 22: "PI", 23: "CE", 24: "RN", 25: "PB", 26: "PE", 27: "AL",
            28: "SE", 29: "BA", 31: "MG", 32: "ES", 33: "RJ", 35: "SP", 41: "PR",
            42: "SC", 43: "RS", 50: "MS", 51: "MT", 52: "GO", 53: "DF"
        }
        return {
            "uf": uf_map.get(uf_codigo),
            "data_emissao": dt,
            "chave": chave,
        }
    except (ValueError, IndexError):
        return {}


def registrar_leitura(pool, conta_id, chave, media_type):
    """Grava a auditoria de uma foto/PDF recebido (leu QR ou nao). Tolerante a
    falha: nunca quebra o fluxo do bot se o banco estiver indisponivel."""
    try:
        m = metadados(chave) if chave else {}
        with pool.connection() as c:
            c.execute(
                """insert into qr_leituras
                   (conta_id, chave, uf, cnpj_emitente, data_emissao, media_type, leu)
                   values (%s, %s, %s, %s, %s, %s, %s)""",
                (conta_id, chave, m.get("uf"), m.get("cnpj_emitente"),
                 m.get("data_emissao"), media_type, bool(chave)),
            )
            c.commit()
    except Exception:  # noqa: BLE001
        pass
