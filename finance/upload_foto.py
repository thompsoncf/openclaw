"""Upload de foto de produto pro Supabase Storage.

O fornecedor envia a foto real do produto (tira no celular ou escolhe do aparelho).
A foto é redimensionada (pra não pesar) e subida pro bucket do Supabase Storage, que
devolve uma URL pública – essa URL vai pro foto_url do produto.

Usa httpx (já presente no projeto, mesmo padrão do asaas.py) + Pillow (redimensionar).

Envs necessárias (Render):
  SUPABASE_URL          -> ex https://xxxx.supabase.co
  SUPABASE_SERVICE_KEY  -> a "service_role key" (NÃO a anon) – tem permissão de upload
  SUPABASE_BUCKET_FOTOS -> nome do bucket (ex "produtos") – opcional, default "produtos"

Função principal:
  subir_foto(conteudo_bytes, nome_arquivo, content_type) -> URL pública (str)
                                                          | levanta ValueError se inválido
"""
from __future__ import annotations

import io
import os
import time
import uuid

import httpx


# Limites de segurança
_MAX_BYTES = 6 * 1024 * 1024          # 6 MB de entrada (antes de redimensionar)
_TIPOS_OK = {"image/jpeg", "image/jpg", "image/png", "image/webp"}
_LADO_MAX = 700                        # Redimensiona pra no máximo 700px (card/loja)


def _config() -> tuple[str, str, str]:
    url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or ""
    bucket = os.environ.get("SUPABASE_BUCKET_FOTOS") or "produtos"
    if not url or not key:
        raise ValueError("Upload não configurado (faltam SUPABASE_URL/SUPABASE_SERVICE_KEY).")
    return url, key, bucket


def _cortar_aspecto(img, aspecto: float):
    """Corta no CENTRO pra deixar a imagem na proporção 'aspecto' (largura/altura).
    Ex: 1.0 = quadrado (logo), 3.0 = bem largo (capa). Sem distorcer, só corta."""
    w, h = img.size
    if not h:
        return img
    atual = w / h
    if atual > aspecto:            # larga demais -> corta as laterais
        nova = int(round(h * aspecto))
        x = max(0, (w - nova) // 2)
        return img.crop((x, 0, x + nova, h))
    if atual < aspecto:            # alta demais -> corta topo e base
        nova = int(round(w / aspecto))
        y = max(0, (h - nova) // 2)
        return img.crop((0, y, w, y + nova))
    return img


def _redimensionar(conteudo: bytes, content_type: str,
                   aspecto: float | None = None) -> tuple[bytes, str]:
    """Reduz a imagem pra no máximo _LADO_MAX no maior lado, mantendo proporção.
    Se 'aspecto' for dado (ex: 1.0 logo, 3.0 capa), corta no centro pra essa proporção
    ANTES de reduzir (fica sempre alinhada, sem distorcer).
    Converte pra JPEG (mais leve). Retorna (bytes, content_type)."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return conteudo, content_type
    img = Image.open(io.BytesIO(conteudo))
    try:
        img = ImageOps.exif_transpose(img)   # Corrige orientação de fotos de celular
    except Exception:
        pass
    if img.mode != "RGB":
        img = img.convert("RGB")
    if aspecto:
        img = _cortar_aspecto(img, aspecto)
    img.thumbnail((_LADO_MAX, _LADO_MAX), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=82, optimize=True)
    return out.getvalue(), "image/jpeg"


def subir_foto(conteudo: bytes, nome_arquivo: str = "",
               content_type: str = "image/jpeg",
               aspecto: float | None = None) -> str:
    """Valida, redimensiona e sobe a foto pro Supabase Storage. Retorna a URL pública.
    'aspecto' opcional corta no centro pra alinhar (1.0 = logo quadrada, 3.0 = capa larga).
    Levanta ValueError se a foto for inválida ou o upload falhar."""
    if not conteudo:
        raise ValueError("Arquivo vazio.")
    if len(conteudo) > _MAX_BYTES:
        raise ValueError("Foto muito grande (max 6 MB).")
    ct = (content_type or "").lower().split(";")[0].strip()
    if ct not in _TIPOS_OK:
        raise ValueError("Só aceitamos imagem (JPEG, PNG ou WEBP).")

    conteudo, ct = _redimensionar(conteudo, ct, aspecto)

    url, key, bucket = _config()
    nome = f"{int(time.time())}_{uuid.uuid4().hex[:8]}.jpg"
    destino = f"{url}/storage/v1/object/{bucket}/{nome}"

    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": ct,
        "x-upsert": "true",
        "Cache-Control": "max-age=31536000",
    }
    try:
        with httpx.Client(timeout=20.0) as c:
            r = c.post(destino, content=conteudo, headers=headers)
    except Exception as e:
        raise ValueError(f"Falha no upload: {e}")
    if r.status_code >= 300:
        # avalia com o corpo cru (so' pro admin: pode conter auth/quota do Supabase)
        from core.falhas import avaliar_falha_provedor
        avaliar_falha_provedor(f"HTTP {r.status_code}: {r.text[:200]}",
                               servico="Supabase Storage")
        # excecao SEM o corpo cru: se vazar pra um tool/painel, nao expoe interno.
        raise ValueError(f"Falha no upload da foto (HTTP {r.status_code}).")

    # URL pública (o bucket precisa ser público – ver guia de setup)
    return f"{url}/storage/v1/object/public/{bucket}/{nome}"


# ---- Material de campanha (PDF ou imagem) --------------------------------
# Reaproveita o mesmo bucket do Supabase, numa pasta "materiais/". Imagem passa
# pelo redimensionamento normal (subir_foto); PDF sobe cru (até 10 MB).
_MAX_PDF = 10 * 1024 * 1024


def subir_material(conteudo: bytes, nome_arquivo: str = "",
                   content_type: str = "") -> str:
    """Sobe o material da campanha (PDF ou imagem) pro Supabase e devolve a URL
    pública. Imagem é redimensionada; PDF sobe como está. Levanta ValueError se o
    tipo não for suportado ou o upload falhar."""
    if not conteudo:
        raise ValueError("Arquivo vazio.")
    ct = (content_type or "").lower().split(";")[0].strip()
    # imagem → mesma rota das fotos (valida + redimensiona)
    if ct in _TIPOS_OK:
        return subir_foto(conteudo, nome_arquivo, ct)
    if ct != "application/pdf":
        raise ValueError("Aceitamos PDF ou imagem (JPG, PNG, WEBP).")
    if len(conteudo) > _MAX_PDF:
        raise ValueError("PDF muito grande (max 10 MB).")

    url, key, bucket = _config()
    nome = f"materiais/{int(time.time())}_{uuid.uuid4().hex[:8]}.pdf"
    destino = f"{url}/storage/v1/object/{bucket}/{nome}"
    headers = {
        "Authorization": f"Bearer {key}",
        "Content-Type": "application/pdf",
        "x-upsert": "true",
        "Cache-Control": "max-age=31536000",
    }
    try:
        with httpx.Client(timeout=30.0) as c:
            r = c.post(destino, content=conteudo, headers=headers)
    except Exception as e:
        raise ValueError(f"Falha no upload: {e}")
    if r.status_code >= 300:
        from core.falhas import avaliar_falha_provedor
        avaliar_falha_provedor(f"HTTP {r.status_code}: {r.text[:200]}",
                               servico="Supabase Storage")
        raise ValueError(f"Falha no upload do material (HTTP {r.status_code}).")
    return f"{url}/storage/v1/object/public/{bucket}/{nome}"
