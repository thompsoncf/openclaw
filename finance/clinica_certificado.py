"""O certificado digital do profissional (prontuário, fase 4). Migração 442.

Desenho aprovado: docs/mockups/clinica_prontuario.html, seções 04, 11.5, 11.6 e 11.7.

  - Cada profissional escolhe: NENHUM (a assinatura simples de hoje), ARQUIVO (A1) ou
    NUVEM (os provedores PSC, finance/clinica_psc.py). Cartão/token USB não: pede um
    programa no computador do médico; quem tem cartão costuma ter o mesmo certificado
    na nuvem.
  - A1: o .pfx fica cifrado (a mesma chave das fotos do prontuário). A SENHA NUNCA É
    GRAVADA: o profissional digita uma vez por dia ("liberar"). Aí a chave privada vai pra
    uma linha cifrada com uma chave que só existe na SESSÃO de quem liberou (o cookie),
    e vence no fim do dia. Sem o cookie, a linha não serve; sem o banco, o cookie não serve.
  - A assinatura ICP-Brasil é uma CAMADA por cima da simples: a evolução/documento já
    está assinado e imutável; o Zaq gera o PDF, assina (PAdES com a política AD-RB da
    ICP-Brasil, a que o validar.iti.gov.br confere) e guarda o PDF assinado, cifrado.
    O que foi assinado com o certificado já ligado e ainda não tem a camada ICP fica
    PENDENTE: A1 assina ao liberar; nuvem assina "as N de hoje" numa autorização.
  - Documento de saúde (receita, atestado, pedido de exame, laudo) leva os metadados do
    ITI (o tipo e o CRM) e um QR com código pra farmácia conferir no validador.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import hmac
import io
import json
import logging
import secrets
from datetime import datetime, time, timedelta

from finance import clinica_agenda as ca

_log = logging.getLogger("clinica.certificado")

#: a política de assinatura PAdES AD-RB v1.1 da ICP-Brasil (DOC-ICP-15.03). O hash é o
#: SHA-256 do arquivo da política, o mesmo da lista oficial (LPA_PAdES.der).
POLITICA_OID = "2.16.76.1.7.1.11.1.1"
POLITICA_SHA256 = "95752d26ca974d46675ae7fb787b606a71ea941f26b59f6b6a321f97d63b9cb1"
POLITICA_URI = "http://politicas.icpbrasil.gov.br/PA_PAdES_AD_RB_v1_1.der"
#: as políticas de certificado da ICP-Brasil (A1 = 2.16.76.1.2.1.x, A3 = 2.16.76.1.2.3.x)
ICP_CERTIFICADO = "2.16.76.1.2."
#: o validar.iti.gov.br reconhece o documento de saúde por estes metadados
OID_DOCUMENTO = {"receita": ("2.16.76.1.12.1.1", "Prescrição de medicamento"),
                 "receita_controle": ("2.16.76.1.12.1.1", "Prescrição de medicamento"),
                 "atestado": ("2.16.76.1.12.1.2", "Atestado médico"),
                 "pedido_exame": ("2.16.76.1.12.1.3", "Solicitação de exame"),
                 "laudo": ("2.16.76.1.12.1.4", "Laudo")}
OID_CRM, OID_CRM_UF = "2.16.76.1.4.2.2.1", "2.16.76.1.4.2.2.2"
TETO_PFX = 64 * 1024
LOTE_MAX = 60             # por autorização (o token da nuvem vale 10 minutos)
QR_DIAS = 365             # o QR da farmácia abre por um ano


# ------------------------------------------------------------------ o certificado

def _x509(der: bytes):
    from cryptography import x509
    return x509.load_der_x509_certificate(der)


def _der(cert) -> bytes:
    from cryptography.hazmat.primitives import serialization
    return cert.public_bytes(serialization.Encoding.DER)


def info(cert) -> dict:
    """Titular (sem o CPF que o e-CPF traz no nome), série, emissor e validade."""
    from cryptography.x509.oid import NameOID
    cn = cert.subject.get_attributes_for_oid(NameOID.COMMON_NAME)
    titular = (cn[0].value if cn else cert.subject.rfc4514_string()).split(":")[0].strip()
    ecn = cert.issuer.get_attributes_for_oid(NameOID.COMMON_NAME)
    return {"titular": titular[:200], "serial": format(cert.serial_number, "x"),
            "emissor": (ecn[0].value if ecn else "")[:200], "validade": cert.not_valid_after_utc}


def conferir(cert, agora: datetime) -> str | None:
    """O certificado serve pra assinar documento de saúde: ICP-Brasil, RSA, no prazo."""
    from cryptography import x509
    from cryptography.hazmat.primitives.asymmetric import rsa
    if cert.not_valid_after_utc <= agora:
        return f"O certificado venceu em {ca.local(cert.not_valid_after_utc):%d/%m/%Y}."
    if cert.not_valid_before_utc > agora:
        return "O certificado ainda não começou a valer."
    try:
        pols = cert.extensions.get_extension_for_class(x509.CertificatePolicies).value
    except x509.ExtensionNotFound:
        pols = []
    if not any(p.policy_identifier.dotted_string.startswith(ICP_CERTIFICADO) for p in pols):
        return "Este não é um certificado ICP-Brasil de pessoa (e-CPF A1 ou A3)."
    if not isinstance(cert.public_key(), rsa.RSAPublicKey):
        return "O Zaq assina com certificado RSA (o padrão da ICP-Brasil); este é de outro tipo."
    return None


def _cadeia_por_aia(cert, ja: list) -> list:
    """Completa a cadeia pelo endereço do emissor que o próprio certificado traz (AIA):
    o validador do ITI confere o caminho até a raiz."""
    from cryptography import x509
    from cryptography.x509.oid import AuthorityInformationAccessOID
    cadeia, atual = list(ja), cert
    for _ in range(5):
        if atual.issuer == atual.subject:                 # chegou na raiz
            break
        emissor = next((c for c in cadeia if c.subject == atual.issuer), None)
        if emissor is None:
            try:
                aia = atual.extensions.get_extension_for_class(x509.AuthorityInformationAccess).value
                url = next(d.access_location.value for d in aia
                           if d.access_method == AuthorityInformationAccessOID.CA_ISSUERS
                           and str(d.access_location.value).startswith("http"))
                import httpx
                r = httpx.get(url, timeout=10)
                r.raise_for_status()
                try:
                    emissor = x509.load_der_x509_certificate(r.content)
                except ValueError:
                    from cryptography.hazmat.primitives.serialization import pkcs7
                    emissor = next(c for c in pkcs7.load_der_pkcs7_certificates(r.content)
                                   if c.subject == atual.issuer)
            except Exception:  # noqa: BLE001 — sem a cadeia completa, assina assim mesmo
                break
            cadeia.append(emissor)
        atual = emissor
    return cadeia


# ------------------------------------------------------------------ quem assina

class Assinador:
    """Assina (PKCS#1 v1.5, SHA-256) os atributos de cada PDF: com a chave do A1 aqui, ou
    mandando só o hash pro provedor da nuvem."""

    def __init__(self, metodo: str, cert, cadeia: list, cru, provedor: str | None = None):
        self.metodo, self.cert, self.cadeia, self._cru, self.provedor = metodo, cert, cadeia, cru, provedor

    def cru(self, dados: list[bytes]) -> list[bytes]:
        return self._cru(dados)


def assinador_a1(chave_privada, cert, cadeia: list) -> Assinador:
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import padding
    return Assinador("a1", cert, cadeia,
                     lambda lista: [chave_privada.sign(d, padding.PKCS1v15(), hashes.SHA256()) for d in lista])


def assinador_nuvem(provedor: str, tok: str, alias: str, cert, cadeia: list) -> Assinador:
    from finance import clinica_psc as psc
    return Assinador("nuvem", cert, cadeia,
                     lambda lista: psc.assinar(provedor, tok, alias, [hashlib.sha256(d).digest() for d in lista]),
                     provedor)


# ------------------------------------------------------------------ o PDF (PAdES AD-RB)

def _metadados(pdf: bytes, pares: dict[str, str]) -> bytes:
    """Os metadados do ITI no dicionário Info do PDF (chave = o OID)."""
    if not pares:
        return pdf
    import pymupdf
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    tipo, val = doc.xref_get_key(-1, "Info")
    if tipo != "xref":
        doc.set_metadata({"producer": "Zaq"})
        tipo, val = doc.xref_get_key(-1, "Info")
    xref = int(val.split()[0])
    for k, v in pares.items():
        doc.xref_set_key(xref, k, pymupdf.get_pdf_str(v))
    return doc.tobytes(garbage=0)


def assinar_pdfs(assinador: Assinador, pdfs: list[bytes], motivo: str = "Documento de saúde") -> list[bytes]:
    """PAdES em duas fases: prepara todos, assina os atributos de todos de uma vez (uma
    autorização na nuvem), fecha todos."""
    return asyncio.run(_assinar_pdfs(assinador, pdfs, motivo))


async def _assinar_pdfs(assinador: Assinador, pdfs: list[bytes], motivo: str) -> list[bytes]:
    from asn1crypto import core
    from asn1crypto import x509 as ax
    from pyhanko.pdf_utils.incremental_writer import IncrementalPdfFileWriter
    from pyhanko.sign import fields, signers
    from pyhanko.sign.ades import cades_asn1
    from pyhanko.sign.ades.api import CAdESSignedAttrSpec
    from pyhanko.sign.signers.pdf_cms import PdfCMSSignedAttributes
    from pyhanko.sign.signers.pdf_signer import PdfTBSDocument
    from pyhanko_certvalidator.registry import SimpleCertificateStore

    cert = ax.Certificate.load(_der(assinador.cert))
    reg = SimpleCertificateStore.from_certs([ax.Certificate.load(_der(c)) for c in assinador.cadeia])
    politica = cades_asn1.SignaturePolicyIdentifier({"signature_policy_id": {
        "sig_policy_id": POLITICA_OID,
        "sig_policy_hash": {"digest_algorithm": {"algorithm": "sha256"}, "digest": bytes.fromhex(POLITICA_SHA256)},
        "sig_policy_qualifiers": [{"sig_policy_qualifier_id": "sp_uri",
                                   "sig_qualifier": core.IA5String(POLITICA_URI)}]}})
    attrs = PdfCMSSignedAttributes(cades_signed_attrs=CAdESSignedAttrSpec(signature_policy_identifier=politica))
    tamanho = assinador.cert.public_key().key_size // 8
    prontos = []
    for pdf in pdfs:
        ext = signers.ExternalSigner(signing_cert=cert, cert_registry=reg, signature_value=tamanho)
        meta = signers.PdfSignatureMetadata(field_name="AssinaturaICP", md_algorithm="sha256",
                                            subfilter=fields.SigSeedSubFilter.PADES, reason=motivo)
        w = IncrementalPdfFileWriter(io.BytesIO(pdf))
        prep, tbs, out = await signers.PdfSigner(meta, signer=ext).async_digest_doc_for_signing(
            w, bytes_reserved=32768)
        sa = await ext.signed_attrs(prep.document_digest, "sha256", attr_settings=attrs, use_pades=True)
        prontos.append((prep, tbs, out, sa))
    crus = assinador.cru([sa.dump() for _p, _t, _o, sa in prontos])
    if len(crus) != len(prontos):
        raise ValueError("faltou assinatura")
    saida = []
    for (prep, tbs, out, sa), cru in zip(prontos, crus):
        ext = signers.ExternalSigner(signing_cert=cert, cert_registry=reg, signature_value=cru)
        sig = await ext.async_sign_prescribed_attributes("sha256", signed_attrs=sa)
        await PdfTBSDocument.async_finish_signing(out, prep, sig, post_sign_instr=tbs.post_sign_instructions)
        saida.append(out.getvalue())
    raizes = [ax.Certificate.load(_der(c)) for c in assinador.cadeia if c.issuer == c.subject]
    for dados in saida:                      # a assinatura confere antes de guardar
        if not await _confere(dados, raizes):
            raise ValueError("a assinatura não conferiu")
    return saida


def assinatura_confere(pdf: bytes) -> bool:
    """O PDF não mudou depois de assinado e a assinatura bate com o certificado (a cadeia
    até a raiz da ICP-Brasil quem confere é o validar.iti.gov.br)."""
    return asyncio.run(_confere(pdf, []))


async def _confere(pdf: bytes, raizes: list) -> bool:
    try:
        from pyhanko.pdf_utils.reader import PdfFileReader
        from pyhanko.sign.validation import async_validate_pdf_signature
        from pyhanko_certvalidator import ValidationContext
        sigs = PdfFileReader(io.BytesIO(pdf)).embedded_signatures
        if len(sigs) != 1:
            return False
        st = await async_validate_pdf_signature(sigs[0], ValidationContext(trust_roots=raizes, allow_fetching=False))
        return bool(st.intact and st.valid and st.coverage.name == "ENTIRE_FILE")
    except Exception:  # noqa: BLE001
        _log.warning("certificado: a conferência do PDF assinado falhou", exc_info=True)
        return False


# ------------------------------------------------------------------ cadastro (A1 / nuvem / nenhum)

def estado(c, conta_id: int, prof_id: int, agora: datetime) -> dict:
    try:
        with c.transaction():
            r = c.execute("""select p.certificado, p.certificado_provedor, p.certificado_desde, k.titular, k.validade,
                                    k.emissor, k.tipo, l.expira_em
                               from clinica_profissionais p
                               left join clinica_certificados k on k.profissional_id = p.id
                               left join clinica_certificado_liberado l on l.profissional_id = p.id and l.expira_em > %s
                              where p.id=%s and p.conta_id=%s""", (agora, prof_id, conta_id)).fetchone()
    except Exception:  # noqa: BLE001 — base sem a 442
        r = None
    if not r:
        return {"tipo": "nenhum", "provedor": None, "desde": None, "titular": "", "validade": None, "emissor": "",
                "liberado_ate": None}
    tem = r[6] == r[0]                     # o certificado guardado é do tipo escolhido
    return {"tipo": r[0], "provedor": r[1], "desde": r[2], "titular": r[3] if tem else "",
            "validade": ca.local(r[4]) if tem and r[4] else None, "emissor": r[5] if tem else "",
            "liberado_ate": ca.local(r[7]) if r[7] and r[0] == "a1" else None}


def _ctx(conta_id: int, prof_id: int, o_que: str) -> str:
    return f"certificado:{o_que}:{conta_id}:{prof_id}"


def guardar_a1(c, conta_id: int, prof_id: int, dados: bytes, senha: str, quem: str, agora: datetime) -> str | None:
    """O .pfx do profissional: confere com a senha (que não fica) e guarda cifrado."""
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.hazmat.primitives.serialization import pkcs12

    from finance import clinica_prontuario_arquivos as parq
    if not parq.chave_configurada():
        return "A chave do prontuário não está configurada no servidor."
    if not dados or len(dados) > TETO_PFX:
        return "Envie o arquivo do certificado (.pfx ou .p12)."
    try:
        chave, cert, _extra = pkcs12.load_key_and_certificates(dados, senha.encode("utf-8"))
    except Exception:  # noqa: BLE001
        return "A senha não abre este arquivo (ou ele não é um certificado .pfx/.p12)."
    if chave is None or cert is None or not isinstance(chave, rsa.RSAPrivateKey):
        return "O arquivo não traz a chave do certificado."
    erro = conferir(cert, agora)
    if erro:
        return erro
    i = info(cert)
    c.execute("""insert into clinica_certificados (profissional_id, conta_id, tipo, arquivo, titular, serial, emissor,
                                                    validade, enviado_por, atualizado_em)
                 values (%s,%s,'a1',%s,%s,%s,%s,%s,%s,now())
                 on conflict (profissional_id) do update set tipo='a1', arquivo=excluded.arquivo,
                   titular=excluded.titular, serial=excluded.serial, emissor=excluded.emissor,
                   validade=excluded.validade, enviado_por=excluded.enviado_por, atualizado_em=now()""",
              (prof_id, conta_id, parq.cifrar(dados, _ctx(conta_id, prof_id, "a1")), i["titular"], i["serial"],
               i["emissor"], i["validade"], quem[:120]))
    _escolher(c, conta_id, prof_id, "a1", None)
    c.execute("delete from clinica_certificado_liberado where profissional_id=%s", (prof_id,))
    return None


def _escolher(c, conta_id: int, prof_id: int, tipo: str, provedor: str | None) -> None:
    # "desde" só muda quando o tipo muda: trocar o A1 vencido pelo novo não perde pendências
    c.execute("""update clinica_profissionais
                    set certificado_desde = case when certificado = %s and certificado_desde is not null
                                                 then certificado_desde else now() end,
                        certificado=%s, certificado_provedor=%s
                  where id=%s and conta_id=%s""", (tipo, tipo, provedor, prof_id, conta_id))


def escolher_nuvem(c, conta_id: int, prof_id: int, provedor: str) -> str | None:
    from finance import clinica_psc as psc
    if provedor not in psc.PROVEDORES:
        return "Escolha o provedor."
    _escolher(c, conta_id, prof_id, "nuvem", provedor)
    c.execute("delete from clinica_certificado_liberado where profissional_id=%s", (prof_id,))
    return None


def lembrar_nuvem(c, conta_id: int, prof_id: int, cert) -> None:
    """O certificado que o provedor mostrou (titular e validade na tela)."""
    i = info(cert)
    c.execute("""insert into clinica_certificados (profissional_id, conta_id, tipo, titular, serial, emissor, validade,
                                                    atualizado_em)
                 values (%s,%s,'nuvem',%s,%s,%s,%s,now())
                 on conflict (profissional_id) do update set tipo='nuvem', arquivo=null, titular=excluded.titular,
                   serial=excluded.serial, emissor=excluded.emissor, validade=excluded.validade, atualizado_em=now()""",
              (prof_id, conta_id, i["titular"], i["serial"], i["emissor"], i["validade"]))


def tirar(c, conta_id: int, prof_id: int) -> None:
    """Volta pra assinatura simples. O arquivo A1 some (a assinatura já feita fica)."""
    c.execute("update clinica_profissionais set certificado='nenhum', certificado_provedor=null, "
              "certificado_desde=null where id=%s and conta_id=%s", (prof_id, conta_id))
    c.execute("delete from clinica_certificado_liberado where profissional_id=%s and conta_id=%s", (prof_id, conta_id))
    c.execute("delete from clinica_certificados where profissional_id=%s and conta_id=%s", (prof_id, conta_id))


# ------------------------------------------------------------------ A1: liberar no dia

def fim_do_dia(agora: datetime) -> datetime:
    return min(ca.utc(ca.local(agora).date(), time(23, 59)), agora + timedelta(hours=16))


def liberar_a1(c, conta_id: int, q: dict, senha: str, agora: datetime) -> tuple[str | None, str | None]:
    """(chave da sessão, erro). A senha abre o .pfx aqui e é esquecida; a chave privada
    fica cifrada com uma chave que só a sessão de quem liberou tem."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    from cryptography.hazmat.primitives.serialization import pkcs12

    from finance import clinica_prontuario_arquivos as parq
    pid = q["profissional_id"]
    r = c.execute("""select k.arquivo from clinica_certificados k join clinica_profissionais p on p.id = k.profissional_id
                      where k.profissional_id=%s and k.conta_id=%s and k.tipo='a1' and p.certificado='a1'""",
                  (pid, conta_id)).fetchone()
    if not r or not r[0]:
        return None, "Envie o arquivo do certificado A1 antes."
    try:
        pfx = parq.decifrar(bytes(r[0]), _ctx(conta_id, pid, "a1"))
    except ValueError:
        return None, "O arquivo do certificado não abre (a chave do servidor mudou?): envie de novo."
    try:
        chave, cert, extra = pkcs12.load_key_and_certificates(pfx, senha.encode("utf-8"))
    except Exception:  # noqa: BLE001
        return None, "Senha do certificado errada."
    erro = conferir(cert, agora)
    if erro:
        return None, erro
    k = secrets.token_bytes(32)
    nonce = secrets.token_bytes(12)
    corpo = json.dumps({"chave": base64.b64encode(chave.private_bytes(
        serialization.Encoding.DER, serialization.PrivateFormat.PKCS8, serialization.NoEncryption())).decode(),
        "certs": [base64.b64encode(_der(x)).decode() for x in [cert, *_cadeia_por_aia(cert, list(extra or []))]]
    }).encode()
    blob = parq.cifrar(nonce + AESGCM(k).encrypt(nonce, corpo, _ctx(conta_id, pid, "dia").encode()),
                       _ctx(conta_id, pid, "liberado"))
    c.execute("""insert into clinica_certificado_liberado (profissional_id, conta_id, membro_id, chave, expira_em)
                 values (%s,%s,%s,%s,%s)
                 on conflict (profissional_id) do update set membro_id=excluded.membro_id, chave=excluded.chave,
                   expira_em=excluded.expira_em""", (pid, conta_id, q.get("membro_id"), blob, fim_do_dia(agora)))
    return base64.urlsafe_b64encode(k).decode(), None


def encerrar(c, conta_id: int, prof_id: int) -> None:
    c.execute("delete from clinica_certificado_liberado where profissional_id=%s and conta_id=%s", (prof_id, conta_id))


def assinador_liberado(c, conta_id: int, q: dict, chave_sessao: str | None, agora: datetime) -> Assinador | None:
    """O A1 liberado HOJE, por ESTE login, com a chave DESTA sessão; senão None."""
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from finance import clinica_prontuario_arquivos as parq
    if not chave_sessao:
        return None
    pid = q["profissional_id"]
    try:
        with c.transaction():
            r = c.execute("""select l.chave from clinica_certificado_liberado l
                               join clinica_profissionais p on p.id = l.profissional_id and p.certificado = 'a1'
                              where l.profissional_id=%s and l.conta_id=%s and l.expira_em > %s
                                and l.membro_id is not distinct from %s""",
                          (pid, conta_id, agora, q.get("membro_id"))).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not r:
        return None
    try:
        k = base64.urlsafe_b64decode(chave_sessao.encode())
        dentro = parq.decifrar(bytes(r[0]), _ctx(conta_id, pid, "liberado"))
        corpo = json.loads(AESGCM(k).decrypt(dentro[:12], dentro[12:], _ctx(conta_id, pid, "dia").encode()))
        chave = serialization.load_der_private_key(base64.b64decode(corpo["chave"]), password=None)
        certs = [_x509(base64.b64decode(x)) for x in corpo["certs"]]
    except Exception:  # noqa: BLE001 — outra sessão, chave trocada: não serve
        return None
    if conferir(certs[0], agora):
        return None
    return assinador_a1(chave, certs[0], certs[1:])


# ------------------------------------------------------------------ pendentes e a camada ICP

def pendentes(c, conta_id: int, q: dict) -> list[dict]:
    """O que ESTE profissional assinou (simples) desde que ligou o certificado e ainda não
    tem a assinatura ICP."""
    cad = list(q.get("cadastros") or [q["profissional_id"]])
    try:
        with c.transaction():
            desde = c.execute("select certificado_desde from clinica_profissionais where id=%s and conta_id=%s "
                              "and certificado <> 'nenhum'", (q["profissional_id"], conta_id)).fetchone()
            if not desde or not desde[0]:
                return []
            rows = c.execute(
                """select 'evolucao', e.id, e.cliente_id, e.assinado_em from clinica_evolucoes e
                    where e.conta_id=%s and e.profissional_id = any(%s) and e.status='assinado' and e.assinado_em >= %s
                      and not exists (select 1 from clinica_assinaturas_icp a where a.alvo='evolucao' and a.alvo_id=e.id)
                   union all
                   select 'documento', d.id, d.cliente_id, d.assinado_em from clinica_documentos d
                    where d.conta_id=%s and d.profissional_id = any(%s) and d.status='assinado' and d.assinado_em >= %s
                      and d.tipo <> 'notificacao'
                      and not exists (select 1 from clinica_assinaturas_icp a where a.alvo='documento' and a.alvo_id=d.id)
                   order by 4 limit 500""", (conta_id, cad, desde[0], conta_id, cad, desde[0])).fetchall()
    except Exception:  # noqa: BLE001 — base sem a 442
        return []
    return [{"alvo": r[0], "id": r[1], "cliente_id": r[2], "quando": r[3]} for r in rows]


def _codigo() -> str:
    alfa = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"            # sem 0/O e 1/I: é digitado
    return "".join(secrets.choice(alfa) for _ in range(10))


def _crm(conselho: str) -> dict[str, str]:
    import re
    m = re.match(r"\s*CRM\s*[-/ ]?\s*([A-Za-z]{2})\s*[-/ ]?\s*(\d+)", conselho or "")
    return {OID_CRM: m.group(2), OID_CRM_UF: m.group(1).upper()} if m else {}


def assinar_itens(c, conta_id: int, assinador: Assinador, itens: list[dict],
                  agora: datetime) -> tuple[list[dict], str | None]:
    """Gera o PDF de cada item, assina todos numa rodada e guarda. (os assinados, erro)."""
    from finance import clinica_documentos as cdoc
    from finance import clinica_planos
    from finance import clinica_prontuario as prt
    from finance import clinica_prontuario_arquivos as parq
    itens = itens[:LOTE_MAX]
    if not itens:
        return [], None
    if not parq.chave_configurada():
        return [], "A chave do prontuário não está configurada no servidor."
    titular = info(assinador.cert)["titular"]
    base = clinica_planos._app_url()
    prontos = []                                  # (item, profissional, pdf, publico, codigo)
    for it in itens:
        publico = codigo = None
        if it["alvo"] == "evolucao":
            e = prt.evolucao(c, conta_id, it["cliente_id"], it["id"])
            if not e or not prt.integra(c, conta_id, it["cliente_id"], it["id"]):
                continue                          # mexida no banco: não se assina por cima
            prof = e["profissional_id"]
            pdf = prt.pdf_evolucao(c, conta_id, it["cliente_id"], e, {"titular": titular, "quando": agora})
            meta = {}
        else:
            d = cdoc.documento(c, conta_id, it["cliente_id"], it["id"])
            if not d or not cdoc.integro(d, it["cliente_id"], conta_id):
                continue
            prof = d["profissional_id"]
            if d["tipo"] in OID_DOCUMENTO and base:
                publico, codigo = secrets.token_urlsafe(12), _codigo()
            pdf = cdoc.pdf(c, conta_id, it["cliente_id"], d, assinatura={
                "titular": titular, "quando": agora, "codigo": codigo,
                "url": f"{base}/doc/{publico}" if publico else None})
            meta = dict([OID_DOCUMENTO[d["tipo"]]]) if d["tipo"] in OID_DOCUMENTO else {}
            meta.update(_crm(d["conselho"]))
        if pdf:
            prontos.append((it, prof, _metadados(pdf, meta), publico, codigo))
    if not prontos:
        return [], None
    try:
        assinados = assinar_pdfs(assinador, [p[2] for p in prontos])
    except Exception as e:  # noqa: BLE001
        from finance import clinica_psc as psc
        _log.warning("certificado: não assinou (conta %s)", conta_id, exc_info=True)
        return [], (f"O provedor do certificado recusou: {e}" if isinstance(e, psc.ErroPSC)
                    else "Não deu pra assinar com o certificado agora.")
    i = info(assinador.cert)
    feitos = []
    for (it, prof, _pdf, publico, codigo), pdf in zip(prontos, assinados):
        r = c.execute(
            """insert into clinica_assinaturas_icp (conta_id, cliente_id, alvo, alvo_id, profissional_id, metodo, provedor,
                                                    titular, serial, pdf, pdf_sha256, publico, segredo_sha256)
               values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
               on conflict (alvo, alvo_id) do nothing returning id""",
            (conta_id, it["cliente_id"], it["alvo"], it["id"], prof, assinador.metodo, assinador.provedor, i["titular"],
             i["serial"], parq.cifrar(pdf, _ctx_pdf(conta_id, it["alvo"], it["id"])),
             hashlib.sha256(pdf).hexdigest(), publico,
             hashlib.sha256(codigo.encode()).hexdigest() if codigo else None)).fetchone()
        if r:
            feitos.append(it)
    return feitos, None


def _ctx_pdf(conta_id: int, alvo: str, alvo_id: int) -> str:
    return f"icp:{conta_id}:{alvo}:{alvo_id}"


def assinados(c, conta_id: int, alvo: str, ids: list[int]) -> dict[int, dict]:
    """{id: {'titular', 'quando', 'metodo'}} dos que têm a assinatura ICP."""
    if not ids:
        return {}
    try:
        with c.transaction():
            rows = c.execute("""select alvo_id, titular, assinado_em, metodo from clinica_assinaturas_icp
                                 where conta_id=%s and alvo=%s and alvo_id = any(%s)""",
                             (conta_id, alvo, list(ids))).fetchall()
    except Exception:  # noqa: BLE001
        return {}
    return {r[0]: {"titular": r[1], "quando": ca.local(r[2]), "metodo": r[3]} for r in rows}


def pdf_assinado(c, conta_id: int, alvo: str, alvo_id: int) -> bytes | None:
    from finance import clinica_prontuario_arquivos as parq
    try:
        with c.transaction():
            r = c.execute("select pdf, pdf_sha256 from clinica_assinaturas_icp where conta_id=%s and alvo=%s "
                          "and alvo_id=%s", (conta_id, alvo, alvo_id)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not r:
        return None
    try:
        pdf = parq.decifrar(bytes(r[0]), _ctx_pdf(conta_id, alvo, alvo_id))
    except ValueError:
        _log.warning("certificado: PDF assinado não decifra (conta %s, %s %s)", conta_id, alvo, alvo_id)
        return None
    return pdf if hashlib.sha256(pdf).hexdigest() == r[1] else None


def pelo_qr(c, publico: str, codigo: str, agora: datetime) -> tuple[bytes, int, int, int] | None:
    """O PDF que a farmácia (ou o validador do ITI) abre com o código impresso.
    (pdf, conta, paciente, documento) ou None."""
    if not publico or not codigo or len(publico) > 40 or len(codigo) > 64:
        return None
    try:
        with c.transaction():
            r = c.execute("""select conta_id, cliente_id, alvo_id, segredo_sha256, assinado_em
                               from clinica_assinaturas_icp where publico=%s and alvo='documento'""",
                          (publico,)).fetchone()
    except Exception:  # noqa: BLE001
        return None
    if not r or not r[3] or r[4] < agora - timedelta(days=QR_DIAS):
        return None
    if not hmac.compare_digest(hashlib.sha256(codigo.strip().upper().encode()).hexdigest(), r[3]):
        return None
    pdf = pdf_assinado(c, r[0], "documento", r[2])
    return (pdf, r[0], r[1], r[2]) if pdf else None
