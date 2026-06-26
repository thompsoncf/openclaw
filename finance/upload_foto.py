"""Upload de foto de produto pro Supabase Storage.

O fornecedor envia a foto real do produto (tira no celular ou escolhe do aparelho).
A foto é redimensionada (pra não pesar) e subida pro bucket do Supabase Storage, que
devolve uma URL pública – essa URL vai pro foto_url do produto.

Não usa o SDK do Supabase (mais uma dependência); sobe via HTTP direto na API REST do
Storage, usando urllib (stdlib). Precisa de Pillow pra redimensionar.

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
import urllib.request
import uuid


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


def _redimensionar(conteudo: bytes, content_type: str) -> tuple[bytes, str]:
    """Reduz a imagem pra no máximo _LADO_MAX no maior lado, mantendo proporção.
    Converte pra JPEG (mais leve). Retorna (bytes, content_type)."""
    try:
        from PIL import Image
    except ImportError:
        # Sem Pillow: sobe original (mas avisa no log). Melhor ter Pillow.
        return conteudo, content_type
    img = Image.open(io.BytesIO(conteudo))
    # Corrige orientação de fotos de celular (EXIF)
    try:
        from PIL import ImageOps
        img = ImageOps.exif_transpose(img)
    except Exception:
        pass
    # Converte pra RGB (descarta alpha/paleta, pra salvar JPEG)
    if img.mode not in ("RGB",):
        img = img.convert("RGB")
    # Redimensiona mantendo proporção
    img.thumbnail((_LADO_MAX, _LADO_MAX), Image.LANCZOS)
    out = io.BytesIO()
    img.save(out, format="JPEG", quality=82, optimize=True)
    return out.getvalue(), "image/jpeg"


def subir_foto(conteudo: bytes, nome_arquivo: str = "",
               content_type: str = "image/jpeg") -> str:
    """Valida, redimensiona e sobe a foto pro Supabase Storage. Retorna a URL pública.
    Levanta ValueError se a foto for inválida ou o upload falhar."""
    if not conteudo:
        raise ValueError("Arquivo vazio.")
    if len(conteudo) > _MAX_BYTES:
        raise ValueError("Foto muito grande (max 6 MB).")
    ct = (content_type or "").lower().split(";")[0].strip()
    if ct not in _TIPOS_OK:
        raise ValueError("Só aceitamos imagem (JPEG, PNG ou WEBP).")

    # Redimensiona (vira JPEG leve)
    conteudo, ct = _redimensionar(conteudo, ct)

    url, key, bucket = _config()
    # Nome único: timestamp + uuid curto, sempre .jpg após redimensionar
    nome = f"{int(time.time())}_{uuid.uuid4().hex[:8]}.jpg"
    destino = f"{url}/storage/v1/object/{bucket}/{nome}"

    req = urllib.request.Request(
        destino, data=conteudo, method="POST",
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": ct,
            "x-upsert": "true",
            "Cache-Control": "max-age=31536000",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            if resp.status not in (200, 201):
                raise ValueError(f"Falha no upload (HTTP {resp.status}).")
    except urllib.error.HTTPError as e:
        corpo = ""
        try:
            corpo = e.read().decode("utf-8")[:200]
        except Exception:
            pass
        raise ValueError(f"Falha no upload (HTTP {e.code}): {corpo}")
    except Exception as e:
        raise ValueError(f"Falha no upload: {e}")

    # URL pública (o bucket precisa ser público – ver guia de setup)
    return f"{url}/storage/v1/object/public/{bucket}/{nome}"
