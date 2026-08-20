"""Comprovante de pagamento: sobe pra bucket PRIVADO, sai por rota nossa.

POR QUE NÃO REUSA `upload_foto`

`finance/upload_foto.py` sobe pro Supabase num bucket PÚBLICO e devolve uma URL
aberta com cache de um ano. Para foto de produto está certo — a foto existe pra
ser vista, e quanto mais barato servir, melhor.

Comprovante bancário é o oposto. Tem o nome do cliente, o banco, o valor e às
vezes o CPF. Numa URL pública, qualquer pessoa com o link lê o documento — e link
se encaminha, se cola em grupo de WhatsApp, se indexa. Não existe "despublicar"
depois: o cache é de um ano e a URL já saiu.

Então aqui:

    o arquivo vai pra um bucket PRIVADO (SUPABASE_BUCKET_DOCS)
    o banco guarda o CAMINHO, nunca uma URL
    quem entrega é uma rota do Zaq, que confere sessão e conta antes de ler

O link que aparece na tela é `/painel/servicos/comprovante/12`. Sem sessão na
empresa certa, não passa.

SE O BUCKET NÃO EXISTIR, DIZ. Falhar em silêncio aqui seria o pior desfecho: o
dono anexaria, veria "ok", e o comprovante não estaria em lugar nenhum no dia da
auditoria. `configurado()` responde antes de a tela oferecer o botão.
"""
from __future__ import annotations

import logging
import os
import time
import uuid

import httpx

_log = logging.getLogger("openclaw.comprovantes")

# O que sai de app de banco e de print de celular.
TIPOS_OK = {"application/pdf", "image/jpeg", "image/jpg", "image/png", "image/webp"}
MAX_BYTES = 10 * 1024 * 1024
_EXT = {"application/pdf": "pdf", "image/jpeg": "jpg", "image/jpg": "jpg",
        "image/png": "png", "image/webp": "webp"}
_TIMEOUT = 30.0


def _config() -> tuple[str, str, str]:
    url = (os.environ.get("SUPABASE_URL") or "").rstrip("/")
    key = os.environ.get("SUPABASE_SERVICE_KEY") or ""
    # DEFAULT PRÓPRIO, e nunca o bucket das fotos: um erro de env não pode fazer
    # comprovante cair no bucket público sem ninguém perceber.
    bucket = (os.environ.get("SUPABASE_BUCKET_DOCS") or "documentos").strip()
    if not url or not key:
        raise ValueError("Upload não configurado (faltam SUPABASE_URL/SUPABASE_SERVICE_KEY).")
    return url, key, bucket


def configurado() -> bool:
    """Dá pra guardar comprovante nesta instalação? A tela pergunta antes de
    oferecer o botão — melhor não ter botão que ter botão que engole arquivo."""
    try:
        _config()
        return True
    except ValueError:
        return False


def validar(conteudo: bytes, content_type: str) -> str:
    """Devolve o content-type normalizado, ou levanta ValueError com o motivo em
    português — a mensagem vai direto pra tela."""
    if not conteudo:
        raise ValueError("Arquivo vazio.")
    if len(conteudo) > MAX_BYTES:
        raise ValueError("Arquivo muito grande (máximo 10 MB).")
    ct = (content_type or "").lower().split(";")[0].strip()
    if ct not in TIPOS_OK:
        raise ValueError("Aceitamos PDF ou imagem (JPG, PNG, WEBP).")
    return ct


def subir(conteudo: bytes, content_type: str, *, conta_id: int,
          orcamento_id: int, parcela_idx: int) -> str:
    """Sobe e devolve o CAMINHO no bucket (não uma URL). Levanta ValueError.

    O caminho leva a conta no começo pra dois fins: a leitura confere que o objeto
    é da conta que pediu (defesa em profundidade, além do WHERE do banco), e um
    dia dá pra apagar tudo de uma conta por prefixo."""
    ct = validar(conteudo, content_type)
    url, key, bucket = _config()
    ext = _EXT.get(ct, "bin")
    caminho = (f"comprovantes/{conta_id}/{orcamento_id}-{parcela_idx}"
               f"-{int(time.time())}-{uuid.uuid4().hex[:8]}.{ext}")
    try:
        with httpx.Client(timeout=_TIMEOUT) as c:
            r = c.post(f"{url}/storage/v1/object/{bucket}/{caminho}",
                       content=conteudo,
                       headers={"Authorization": f"Bearer {key}",
                                "Content-Type": ct, "x-upsert": "true"})
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Falha no upload: {e}")
    if r.status_code >= 300:
        from core.falhas import avaliar_falha_provedor
        avaliar_falha_provedor(f"HTTP {r.status_code}: {r.text[:200]}",
                               servico="Supabase Storage")
        # o 404 do bucket é o erro de instalação, e vale dizer qual é
        if r.status_code == 404:
            raise ValueError(f"O bucket privado '{bucket}' não existe no Supabase.")
        raise ValueError(f"Falha no upload (HTTP {r.status_code}).")
    return caminho


def ler(caminho: str) -> tuple[bytes, str]:
    """(bytes, content-type) do objeto privado. Levanta ValueError.

    Passa pela chave de serviço — é o que faz o bucket poder ser privado. Quem
    checa PERMISSÃO é a rota, antes de chamar aqui: este módulo não conhece
    sessão, e misturar as duas coisas é como um bucket privado volta a ser
    público por acidente."""
    url, key, bucket = _config()
    try:
        with httpx.Client(timeout=_TIMEOUT) as c:
            r = c.get(f"{url}/storage/v1/object/{bucket}/{caminho}",
                      headers={"Authorization": f"Bearer {key}"})
    except Exception as e:  # noqa: BLE001
        raise ValueError(f"Não consegui ler o arquivo: {e}")
    if r.status_code >= 300:
        raise ValueError(f"Arquivo não encontrado (HTTP {r.status_code}).")
    return r.content, (r.headers.get("content-type") or "application/octet-stream")


def apagar(caminho: str) -> None:
    """Best-effort: só é chamado ao SUBSTITUIR um comprovante. Se o Supabase não
    apagar, o objeto órfão não machuca ninguém — o banco já aponta pro novo, e
    derrubar a substituição por causa da limpeza seria pior."""
    try:
        url, key, bucket = _config()
        with httpx.Client(timeout=_TIMEOUT) as c:
            c.request("DELETE", f"{url}/storage/v1/object/{bucket}/{caminho}",
                      headers={"Authorization": f"Bearer {key}"})
    except Exception as e:  # noqa: BLE001
        _log.info("comprovante antigo não apagado (%s): %s", caminho, e)


# ---------------------------------------------------------------- persistência

def registrar(pool, conta_id: int, orcamento_id: int, parcela_idx: int, *,
              caminho: str, nome: str = "", tipo: str = "", bytes_: int = 0,
              por: str = "") -> dict:
    """Grava o vínculo, substituindo o de antes. Devolve {"id", "trocou"}.

    O antigo é apagado DEPOIS do commit: perder o arquivo novo por causa da
    limpeza do velho seria trocar um problema por um pior."""
    with pool.connection() as c:
        antigo = c.execute(
            "select id, caminho from orcamento_comprovantes "
            " where orcamento_id=%s and parcela_idx=%s", (orcamento_id, parcela_idx)
        ).fetchone()
        r = c.execute(
            """insert into orcamento_comprovantes (conta_id, orcamento_id, parcela_idx,
                   caminho, nome, tipo, bytes, por)
               values (%s,%s,%s,%s,%s,%s,%s,%s)
               on conflict (orcamento_id, parcela_idx) do update set
                   caminho=excluded.caminho, nome=excluded.nome, tipo=excluded.tipo,
                   bytes=excluded.bytes, por=excluded.por, criado_em=now()
               returning id""",
            (conta_id, orcamento_id, parcela_idx, caminho, (nome or "")[:200],
             (tipo or "")[:80], int(bytes_ or 0), (por or "")[:60])).fetchone()
        c.commit()
    if antigo and antigo[1] and antigo[1] != caminho:
        apagar(antigo[1])
    return {"id": r[0], "trocou": bool(antigo)}


def por_orcamento(pool, conta_id: int, orcamento_id: int) -> dict[int, dict]:
    """{parcela_idx: {id, nome, tipo, quando}} — o que a tela precisa saber pra
    escolher entre "anexar" e "ver"."""
    try:
        with pool.connection() as c:
            rows = c.execute(
                """select parcela_idx, id, nome, tipo, criado_em
                     from orcamento_comprovantes
                    where conta_id=%s and orcamento_id=%s""",
                (conta_id, orcamento_id)).fetchall()
    except Exception:  # noqa: BLE001 — instalação sem a tabela ainda
        return {}
    return {r[0]: {"id": r[1], "nome": r[2], "tipo": r[3], "quando": r[4]} for r in rows}


def obter(pool, conta_id: int, comprovante_id: int) -> dict | None:
    """O comprovante DA CONTA, pelo id. O `conta_id` no WHERE é o que impede uma
    empresa de ler o comprovante de outra trocando o número na URL."""
    with pool.connection() as c:
        r = c.execute(
            "select caminho, nome, tipo from orcamento_comprovantes "
            " where id=%s and conta_id=%s", (comprovante_id, conta_id)).fetchone()
    return {"caminho": r[0], "nome": r[1], "tipo": r[2]} if r else None
