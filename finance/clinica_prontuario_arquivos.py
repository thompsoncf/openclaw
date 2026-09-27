"""Fotos clínicas e anexos do prontuário (fase 3), cifrados pelo Zaq.

Desenho aprovado: docs/mockups/clinica_prontuario.html, seção 05. Migração 430.

  - O arquivo sobe pro bucket PRIVADO do Storage (o mesmo cano dos comprovantes,
    finance/comprovantes.subir_em) já CIFRADO: AES-256-GCM, formato `ZQP2` + o número
    da chave + nonce + cifrado. A chave atual é PRONTUARIO_CHAVE (número
    PRONTUARIO_CHAVE_ID, padrão 1); as antigas ficam em PRONTUARIO_CHAVES_ANTIGAS
    ("2:base64,…") — trocar a chave não perde nada. Sem chave, não se guarda nada
    (`configurado()` falso, a tela esconde o botão). PERDER A CHAVE PERDE AS FOTOS: ela
    precisa de cópia fora do Render.
  - FOTO: só JPEG, PNG ou WEBP, com teto de pixels (bomba de descompressão); o Zaq
    regrava a imagem — orientação certa, sem metadados (GPS, aparelho, comentário),
    mantendo o perfil de cor — em até 3000 px.
  - TERMO DE IMAGEM: foto só com o termo aceito (uso clínico ou divulgação) ou com a
    autorização em papel declarada. "Não autorizo" barra; e anexo que é IMAGEM, sem o
    termo, só com a declaração de que é documento/exame (não o paciente). Erro ao
    conferir o termo = não guarda.
  - O trabalho pesado (Pillow, cifra, Storage) roda FORA da conexão do banco: a tela
    confere o acesso numa conexão curta, prepara o arquivo, e grava noutra.
  - Quem vê passa pelo portão da fase 1 (finance/clinica_acesso_clinico.ler).
  - O agente do WhatsApp nunca importa este módulo.
"""
from __future__ import annotations

import base64
import hashlib
import io
import logging
import os
import secrets
import uuid

_log = logging.getLogger("clinica.prontuario_arquivos")

TETO = {"foto": 15 * 1024 * 1024, "anexo": 16 * 1024 * 1024}
TETO_CORPO = 17 * 1024 * 1024               # o pedido inteiro (arquivo + campos)
_TIPOS = {"foto": ("image/jpeg", "image/png", "image/webp"),
          "anexo": ("application/pdf", "image/jpeg", "image/png", "image/webp")}
_FORMATOS = ["JPEG", "PNG", "WEBP"]
_LADO_MAX = 3000
_PIXELS_MAX = 40_000_000


# ------------------------------------------------------------------ a chave

def _decodificar(txt: str) -> bytes | None:
    txt = (txt or "").strip()
    try:
        k = base64.urlsafe_b64decode(txt + "=" * (-len(txt) % 4))
    except Exception:  # noqa: BLE001
        return None
    return k if len(k) == 32 else None


def _chaves() -> tuple[int | None, dict[int, bytes]]:
    """(número da chave atual, {número: chave}) — a atual e as antigas."""
    todas: dict[int, bytes] = {}
    for item in (os.environ.get("PRONTUARIO_CHAVES_ANTIGAS") or "").split(","):
        if ":" in item:
            n, v = item.split(":", 1)
            k = _decodificar(v)
            if n.strip().isdigit() and k:
                todas[int(n)] = k
    atual_txt = os.environ.get("PRONTUARIO_CHAVE") or ""
    atual = _decodificar(atual_txt)
    try:
        n_atual = int(os.environ.get("PRONTUARIO_CHAVE_ID") or "1")
    except ValueError:
        n_atual = 1
    if atual_txt.strip() and not atual:
        _log.warning("PRONTUARIO_CHAVE existe mas não é uma chave de 32 bytes em base64: o cofre fica desligado")
    if atual and 0 < n_atual < 256:
        todas[n_atual] = atual
        return n_atual, todas
    return None, todas


def configurado() -> bool:
    from finance import comprovantes
    return _chaves()[0] is not None and comprovantes.configurado()


def cifrar(dados: bytes, contexto: str) -> bytes:
    """ZQP2 + número da chave (1 byte) + nonce(12) + AES-GCM(dados). O `contexto` (o
    caminho, com conta e paciente) entra como dado autenticado."""
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    n, todas = _chaves()
    if n is None:
        raise ValueError("A chave do prontuário não está configurada.")
    nonce = secrets.token_bytes(12)
    return b"ZQP2" + bytes([n]) + nonce + AESGCM(todas[n]).encrypt(nonce, dados, contexto.encode("utf-8"))


def decifrar(blob: bytes, contexto: str) -> bytes:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    _n, todas = _chaves()
    if not blob.startswith(b"ZQP2") or len(blob) < 5 + 12 + 16:
        raise ValueError("Arquivo do prontuário inválido.")
    k = todas.get(blob[4])
    if not k:
        raise ValueError("A chave que cifrou este arquivo não está configurada.")
    try:
        return AESGCM(k).decrypt(blob[5:17], blob[17:], contexto.encode("utf-8"))
    except Exception as e:  # noqa: BLE001 — chave errada ou arquivo mexido
        raise ValueError("O arquivo não confere (foi alterado ou a chave mudou).") from e


# ------------------------------------------------------------------ a imagem

def _limpar_imagem(dados: bytes) -> tuple[bytes, str]:
    """Só JPEG/PNG/WEBP, até 40 Mpx; orientação certa; sem metadados (EXIF, GPS,
    comentário), com o perfil de cor; fundo transparente vira branco. JPEG 90."""
    from PIL import Image, ImageOps
    img = Image.open(io.BytesIO(dados), formats=_FORMATOS)
    w, h = img.size
    if w * h > _PIXELS_MAX:
        raise ValueError("Imagem grande demais.")
    icc = img.info.get("icc_profile")
    if img.format == "JPEG":
        img.draft("RGB", (_LADO_MAX, _LADO_MAX))       # reduz já na leitura
    img.thumbnail((_LADO_MAX, _LADO_MAX))
    try:
        img = ImageOps.exif_transpose(img)
    except Exception:  # noqa: BLE001
        pass
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        fundo = Image.new("RGB", rgba.size, (255, 255, 255))
        fundo.paste(rgba, mask=rgba.getchannel("A"))
        img = fundo
    elif img.mode != "RGB":
        img = img.convert("RGB")
    img.info.pop("comment", None)
    out = io.BytesIO()
    kw = {"icc_profile": icc} if icc else {}
    img.save(out, format="JPEG", quality=90, **kw)     # sem exif=: os metadados ficam pra trás
    return out.getvalue(), "image/jpeg"


# ------------------------------------------------------------------ o termo

def autorizacao_de_imagem(c, conta_id: int, cliente_id: int) -> tuple[str, str]:
    """('clinico'|'divulgacao'|'nao'|'', texto) do último termo de imagem. Pra TELA:
    erro vira "sem termo". Pra guardar, use `_termo_ou_erro` (que falha fechado)."""
    try:
        return _termo_ou_erro(c, conta_id, cliente_id)
    except Exception:  # noqa: BLE001
        return "", ""


def _termo_ou_erro(c, conta_id: int, cliente_id: int) -> tuple[str, str]:
    with c.transaction():
        r = c.execute("""select opcao, aceito_em from clinica_termos_aceites
                          where conta_id=%s and cliente_id=%s and termo='imagem'
                          order by aceito_em desc limit 1""", (conta_id, cliente_id)).fetchone()
    if not r:
        return "", ""
    from finance.clinica_agenda import local
    rot = {"clinico": "só uso clínico", "divulgacao": "uso clínico e divulgação", "nao": "não autoriza fotos"}
    return r[0] or "", f"termo de imagem de {local(r[1]):%d/%m/%Y}: {rot.get(r[0], r[0])}"


def conferir(c, conta_id: int, cliente_id: int, *, tipo: str, mimetype: str, papel_autorizado: bool = False,
             e_documento: bool = False) -> tuple[str, str | None]:
    """(autorização que fica gravada, erro). Na conexão curta, antes do trabalho pesado."""
    if tipo not in TETO:
        return "", "Tipo inválido."
    if not configurado():
        return "", "O cofre das fotos não está configurado nesta instalação."
    mimetype = (mimetype or "").split(";")[0].strip().lower()
    if mimetype in ("image/heic", "image/heif"):
        return "", ("Foto em HEIC (iPhone) não é aceita: em Ajustes › Câmera › Formatos, escolha "
                    "\"Mais compatível\", ou tire a foto pelo Zaq.")
    if mimetype not in _TIPOS[tipo]:
        return "", "Formato não aceito (foto: JPEG, PNG ou WEBP; anexo: PDF ou foto)."
    if tipo == "anexo" and not mimetype.startswith("image/"):
        return "anexo em PDF", None
    try:
        op, txt = _termo_ou_erro(c, conta_id, cliente_id)
    except Exception:  # noqa: BLE001 — sem conferir o termo, não guarda imagem
        return "", "Não consegui conferir o termo de imagem agora. Tente de novo."
    if tipo == "anexo":
        # imagem como anexo: se não é foto autorizada do paciente, tem que ser documento
        if op in ("clinico", "divulgacao"):
            return txt, None
        if not e_documento:
            return "", ("Anexo em imagem só se for foto de documento ou exame (não do paciente): marque a "
                        "caixa. Foto do paciente precisa do termo de imagem.")
        return "imagem de documento ou exame (declarado pelo profissional)", None
    if op == "nao":
        return "", "O paciente não autorizou fotos (termo de imagem)."
    if not op and not papel_autorizado:
        return "", ("O paciente ainda não aceitou o termo de imagem. Se autorizou em papel, marque a caixa e "
                    "anexe o termo.")
    return (txt or "autorização em papel, declarada pelo profissional"), None


def preparar(conta_id: int, cliente_id: int, *, tipo: str, dados: bytes, mimetype: str) -> tuple[dict | None, str | None]:
    """Limpa, cifra e sobe (FORA da conexão do banco). ({caminho, mimetype, tamanho, sha256}, erro)."""
    mimetype = (mimetype or "").split(";")[0].strip().lower()
    if not dados:
        return None, "Arquivo vazio."
    if len(dados) > TETO[tipo]:
        return None, f"Arquivo grande demais (até {TETO[tipo] // (1024 * 1024)} MB)."
    if mimetype.startswith("image/"):
        try:
            dados, mimetype = _limpar_imagem(dados)
        except Exception:  # noqa: BLE001
            return None, "Não consegui abrir essa imagem (JPEG, PNG ou WEBP)."
    elif not dados.startswith(b"%PDF"):
        return None, "O arquivo não é um PDF."
    caminho = f"prontuario/{conta_id}/{cliente_id}/{uuid.uuid4().hex}.zqp"
    try:
        blob = cifrar(dados, caminho)
        from finance import comprovantes
        comprovantes.subir_em(caminho, blob, "application/octet-stream")
    except ValueError as e:
        return None, str(e)
    return {"caminho": caminho, "mimetype": mimetype, "tamanho": len(dados),
            "sha256": hashlib.sha256(dados).hexdigest()}, None


def registrar_arquivo(c, conta_id: int, cliente_id: int, quem: dict, pronto: dict, *, tipo: str,
                      autorizacao: str, regiao: str = "", legenda: str = "", evento_id: int | None = None) -> int:
    """O registro no banco (se falhar, o cifrado que subiu é apagado)."""
    try:
        with c.transaction():
            if evento_id and not c.execute("select 1 from eventos_agenda where id=%s and conta_id=%s and cliente_id=%s",
                                           (evento_id, conta_id, cliente_id)).fetchone():
                evento_id = None
            return c.execute(
                """insert into clinica_prontuario_arquivos (conta_id, cliente_id, evento_id, profissional_id, tipo,
                                                            regiao, legenda, mimetype, tamanho, sha256, caminho,
                                                            autorizacao)
                   values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
                (conta_id, cliente_id, evento_id, quem["profissional_id"], tipo, " ".join((regiao or "").split())[:80],
                 " ".join((legenda or "").split())[:200], pronto["mimetype"], pronto["tamanho"], pronto["sha256"],
                 pronto["caminho"], (autorizacao or "")[:200])).fetchone()[0]
    except Exception:
        from finance import comprovantes
        comprovantes.apagar(pronto["caminho"])
        raise


def guardar(c, conta_id: int, cliente_id: int, quem: dict, *, tipo: str, dados: bytes, mimetype: str,
            regiao: str = "", legenda: str = "", evento_id: int | None = None,
            papel_autorizado: bool = False, e_documento: bool = False) -> tuple[int | None, str | None]:
    """Tudo numa conexão (os testes e os scripts). A tela usa conferir → preparar →
    registrar_arquivo, com o trabalho pesado fora da conexão."""
    autorizacao, erro = conferir(c, conta_id, cliente_id, tipo=tipo, mimetype=mimetype,
                                 papel_autorizado=papel_autorizado, e_documento=e_documento)
    if erro:
        return None, erro
    pronto, erro = preparar(conta_id, cliente_id, tipo=tipo, dados=dados, mimetype=mimetype)
    if erro:
        return None, erro
    return registrar_arquivo(c, conta_id, cliente_id, quem, pronto, tipo=tipo, autorizacao=autorizacao,
                             regiao=regiao, legenda=legenda, evento_id=evento_id), None


# ------------------------------------------------------------------ ler

def listar(c, conta_id: int, cliente_id: int) -> list[dict]:
    try:
        with c.transaction():
            rows = c.execute(
                """select a.id, a.tipo, a.regiao, a.legenda, a.mimetype, a.tamanho, a.criado_em, a.autorizacao,
                          coalesce(p.nome, ''), a.evento_id
                     from clinica_prontuario_arquivos a
                     left join clinica_profissionais p on p.id = a.profissional_id and p.conta_id = a.conta_id
                    where a.conta_id=%s and a.cliente_id=%s order by a.criado_em desc, a.id desc""",
                (conta_id, cliente_id)).fetchall()
    except Exception:  # noqa: BLE001 — base sem a 430
        return []
    from finance.clinica_agenda import local
    return [{"id": r[0], "tipo": r[1], "regiao": r[2], "legenda": r[3], "mimetype": r[4], "tamanho": r[5],
             "quando": local(r[6]), "autorizacao": r[7], "prof": r[8], "evento_id": r[9]} for r in rows]


def localizar(c, conta_id: int, cliente_id: int, arquivo_id: int) -> dict | None:
    """A linha do arquivo DESTE paciente (sem baixar nada) — pra registrar antes de abrir."""
    r = c.execute("""select caminho, mimetype, sha256, tipo, regiao from clinica_prontuario_arquivos
                      where id=%s and conta_id=%s and cliente_id=%s""", (arquivo_id, conta_id, cliente_id)).fetchone()
    if not r or not r[0].startswith(f"prontuario/{conta_id}/{cliente_id}/"):
        return None
    return {"caminho": r[0], "mimetype": r[1], "sha256": r[2], "tipo": r[3], "regiao": r[4]}


def baixar(info: dict) -> bytes:
    """Baixa, decifra e confere (FORA da conexão do banco). Levanta ValueError."""
    from finance import comprovantes
    blob, _ct = comprovantes.ler(info["caminho"])
    dados = decifrar(blob, info["caminho"])
    if hashlib.sha256(dados).hexdigest() != info["sha256"]:
        raise ValueError("O arquivo não confere com o que foi guardado.")
    return dados


def abrir(c, conta_id: int, cliente_id: int, arquivo_id: int) -> tuple[bytes, str, dict] | None:
    info = localizar(c, conta_id, cliente_id, arquivo_id)
    if not info:
        return None
    return baixar(info), info["mimetype"], info
