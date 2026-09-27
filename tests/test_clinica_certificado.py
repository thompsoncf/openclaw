"""Prontuário, fase 4: o certificado digital (finance/clinica_certificado.py, finance/clinica_psc.py,
web/painel_clinica_certificado.py). Seções 04, 11.5, 11.6 e 11.7 do desenho.

Os certificados são gerados aqui: uma AC de teste e um e-CPF com a política da ICP-Brasil.
O provedor da nuvem é de mentira (httpx.MockTransport), assinando com a chave de teste.
"""
import base64
import io
import re
import secrets
from datetime import datetime, timedelta, timezone
from urllib.parse import parse_qs, urlparse

import httpx
import psycopg
import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa, utils
from cryptography.hazmat.primitives.serialization import pkcs12
from cryptography.x509.oid import NameOID
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from finance import clinica_acesso_clinico as acc
from finance import clinica_certificado as cert
from finance import clinica_documentos as cdoc
from finance import clinica_prontuario as prt
from finance import clinica_psc as psc
from tests.test_clinica_agenda import BASE, CLINICA
from tests.test_clinica_ficha_link import banco as _banco_ficha  # noqa: F401
from tests.test_clinica_pacientes import banco as _banco_pacientes  # noqa: F401
from tests.test_clinica_pacotes import pool, zap  # noqa: F401
from tests.test_clinica_prontuario import MANOEL, _liberado

AGORA = datetime.now(timezone.utc)
SENHA = "senha-do-cert"


@pytest.fixture()
def banco(_banco_ficha, monkeypatch):  # noqa: F811
    monkeypatch.setenv("PRONTUARIO_CHAVE", base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
    with _banco_ficha.connection() as c:
        c.execute((BASE / "442_clinica_certificado.sql").read_text(encoding="utf-8"))
        c.commit()
    return _banco_ficha


def _ac():
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    nome = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "AC Teste do Zaq")])
    c = (x509.CertificateBuilder().subject_name(nome).issuer_name(nome).public_key(k.public_key())
         .serial_number(1).not_valid_before(AGORA - timedelta(days=2)).not_valid_after(AGORA + timedelta(days=900))
         .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
         .add_extension(x509.KeyUsage(True, False, False, False, False, True, True, False, False), critical=True)
         .sign(k, hashes.SHA256()))
    return k, c


def _ecpf(ac, *, icp=True, dias=300, nome="MANOEL TESTE:00000000000"):
    ac_k, ac_c = ac
    k = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    b = (x509.CertificateBuilder().subject_name(x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, nome)]))
         .issuer_name(ac_c.subject).public_key(k.public_key()).serial_number(secrets.randbits(60))
         .not_valid_before(AGORA - timedelta(days=400 if dias < 0 else 1)).not_valid_after(AGORA + timedelta(days=dias))
         .add_extension(x509.KeyUsage(True, True, False, False, False, False, False, False, False), critical=True))
    if icp:
        b = b.add_extension(x509.CertificatePolicies([x509.PolicyInformation(
            x509.ObjectIdentifier("2.16.76.1.2.1.51"), None)]), critical=False)
    c = b.sign(ac_k, hashes.SHA256())
    pfx = pkcs12.serialize_key_and_certificates(b"manoel", k, c, [ac_c],
                                                serialization.BestAvailableEncryption(SENHA.encode()))
    return k, c, pfx


def _confere(pdf: bytes, ac_cert) -> bool:
    """Íntegra, válida, confiável até a AC de teste e com a política AD-RB da ICP-Brasil."""
    from asn1crypto import x509 as ax
    from pyhanko.pdf_utils.reader import PdfFileReader
    from pyhanko.sign.validation import validate_pdf_signature
    from pyhanko_certvalidator import ValidationContext
    s = PdfFileReader(io.BytesIO(pdf)).embedded_signatures[0]
    raiz = ax.Certificate.load(ac_cert.public_bytes(serialization.Encoding.DER))
    st = validate_pdf_signature(s, ValidationContext(trust_roots=[raiz], allow_fetching=False))
    attrs = {a["type"].native: a for a in s.signer_info["signed_attrs"]}
    pol = attrs["signature_policy_identifier"]["values"][0]
    return (st.intact and st.valid and st.trusted
            and pol.native["sig_policy_id"] == cert.POLITICA_OID
            and "signing_certificate_v2" in attrs)


@pytest.fixture()
def tela(banco, monkeypatch):  # noqa: F811
    from web import painel_clinica_agenda as pa
    from web import painel_clinica_certificado as wc
    from web import painel_clinica_pacientes as pw
    from web import painel_clinica_prontuario as pp
    for m in (pa, pw, pp, wc):
        monkeypatch.setattr(m, "get_pool", lambda: banco)
    monkeypatch.setattr(pa, "conta_logada", lambda request: (CLINICA,))
    monkeypatch.setattr(pa, "nicho_da_conta", lambda conta: "clinica")
    from finance import clinica_planos
    monkeypatch.setattr(clinica_planos, "_app_url", lambda: "https://zaq.teste")
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste")
    for r in (pa.router, pw.router, pp.router, wc.router, wc.router_publico):
        app.include_router(r)

    @app.get("/_login/{papel}/{membro}")
    def _login(request: Request, papel: str, membro: int):
        request.session.clear()
        request.session["papel"] = papel
        if membro:
            request.session["membro_id"] = membro
        return {}
    return TestClient(app, follow_redirects=False)


def _evolucao(banco, kid, eid, texto="melasma malar"):
    with banco.connection() as c:
        q = acc.leitor(c, CLINICA, MANOEL)
        evo = prt.nova(c, CLINICA, kid, q, "livre", None)
        assert prt.salvar(c, CLINICA, kid, evo, q, {"texto": texto}) is None
        c.commit()
    return evo


def _aviso(cli, url):
    html = cli.get(url).text
    m = re.search(r'<div class="(?:ok|erro)"[^>]*>(.*?)</div>', html, re.S)
    return m.group(1).strip() if m else ""


def test_a1_guarda_libera_no_dia_e_assina_ao_finalizar(tela, banco, zap):  # noqa: F811
    ac = _ac()
    kid, eid, _prof = _liberado(banco)
    tela.get("/_login/gestor/52")
    assert "Certificado digital" in tela.get("/painel/clinica/certificado").text
    _k, _c, sem_icp = _ecpf(ac, icp=False)
    tela.post("/painel/clinica/certificado/a1", files={"arquivo": ("c.pfx", sem_icp)}, data={"senha": SENHA})
    assert "ICP-Brasil" in _aviso(tela, "/painel/clinica/certificado")
    _k, _c, vencido = _ecpf(ac, dias=-1)
    tela.post("/painel/clinica/certificado/a1", files={"arquivo": ("c.pfx", vencido)}, data={"senha": SENHA})
    assert "venceu" in _aviso(tela, "/painel/clinica/certificado")
    k, c_, pfx = _ecpf(ac)
    tela.post("/painel/clinica/certificado/a1", files={"arquivo": ("c.pfx", pfx)}, data={"senha": "errada"})
    assert "senha" in _aviso(tela, "/painel/clinica/certificado").lower()
    tela.post("/painel/clinica/certificado/a1", files={"arquivo": ("c.pfx", pfx)}, data={"senha": SENHA})
    assert "Certificado guardado" in _aviso(tela, "/painel/clinica/certificado")
    with banco.connection() as c:
        arq, titular = c.execute("select arquivo, titular from clinica_certificados").fetchone()
        assert pfx not in bytes(arq) and bytes(arq)[:4] == b"ZQP2" and titular == "MANOEL TESTE"   # sem o CPF
        assert SENHA.encode() not in bytes(arq)

    # assinou sem liberar: fica pendente
    evo = _evolucao(banco, kid, eid)
    tela.post(f"/painel/clinica/prontuario/{kid}/evolucao/{evo}/assinar")
    assert "libere com a senha" in _aviso(tela, f"/painel/clinica/prontuario/{kid}")
    assert "falta o certificado" in tela.get(f"/painel/clinica/prontuario/{kid}").text

    # libera: a pendente é assinada na hora
    r = tela.post("/painel/clinica/certificado/liberar", data={"senha": "errada", "volta": f"/painel/clinica/prontuario/{kid}"})
    assert r.headers["location"] == f"/painel/clinica/prontuario/{kid}"
    assert "Senha do certificado errada" in _aviso(tela, f"/painel/clinica/prontuario/{kid}")
    tela.post("/painel/clinica/certificado/liberar", data={"senha": SENHA, "volta": f"/painel/clinica/prontuario/{kid}"})
    assert "1 pendente assinada" in _aviso(tela, f"/painel/clinica/prontuario/{kid}")
    with banco.connection() as c:
        pdf = cert.pdf_assinado(c, CLINICA, "evolucao", evo)
        assert pdf and _confere(pdf, ac[1])
        assert c.execute("select chave from clinica_certificado_liberado").fetchone()[0][:4] == b"ZQP2"
    r = tela.get(f"/painel/clinica/prontuario/{kid}/evolucao/{evo}/pdf")
    assert r.status_code == 200 and r.content == pdf and "no-store" in r.headers["cache-control"]
    assert "PDF assinado" in tela.get(f"/painel/clinica/prontuario/{kid}").text

    # liberado: a próxima sai assinada com o certificado sem perguntar
    evo2 = _evolucao(banco, kid, eid, "retorno: melhora")
    tela.post(f"/painel/clinica/prontuario/{kid}/evolucao/{evo2}/assinar")
    assert "Assinado também com o certificado" in _aviso(tela, f"/painel/clinica/prontuario/{kid}")

    # outro aparelho do mesmo login: a liberação não vale lá
    outro = type(tela)(tela.app, follow_redirects=False)
    outro.get("/_login/gestor/52")
    assert 'name="senha"' in outro.get(f"/painel/clinica/prontuario/{kid}").text
    with banco.connection() as c:
        q = acc.leitor(c, CLINICA, MANOEL)
        assert cert.assinador_liberado(c, CLINICA, q, base64.urlsafe_b64encode(b"x" * 32).decode(), AGORA) is None
        assert cert.assinador_liberado(c, CLINICA, {**q, "membro_id": 999}, None, AGORA) is None
        # a assinatura com certificado não muda nem some
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("update clinica_assinaturas_icp set titular='x'")
        c.rollback()
        with pytest.raises(psycopg.errors.RaiseException):
            c.execute("delete from clinica_assinaturas_icp")
        c.rollback()
        regs = [x["o_que"] for x in acc.registro(c, CLINICA, datetime.now(timezone.utc))]
    assert "liberou o certificado A1 no dia" in regs and f"assinou com o certificado a evolução #{evo}" in regs

    # encerrar: volta a pedir a senha
    tela.post("/painel/clinica/certificado/encerrar", data={"volta": f"/painel/clinica/prontuario/{kid}"})
    assert 'name="senha"' in tela.get(f"/painel/clinica/prontuario/{kid}").text


def test_receita_assinada_leva_metadados_do_iti_e_o_qr_da_farmacia(tela, banco, zap):  # noqa: F811
    import pymupdf
    ac = _ac()
    kid, _eid, prof = _liberado(banco)
    _k, _c, pfx = _ecpf(ac)
    with banco.connection() as c:
        q = acc.leitor(c, CLINICA, MANOEL)
        assert cert.guardar_a1(c, CLINICA, prof, pfx, SENHA, "Dr. Manoel", AGORA) is None
        c.commit()
        chave, erro = cert.liberar_a1(c, CLINICA, q, SENHA, AGORA)
        assert erro is None
        c.commit()
        did = cdoc.novo(c, CLINICA, kid, q, "receita", "Lúcia Ferreira", AGORA)
        assert cdoc.salvar(c, CLINICA, kid, did, q, {"titulo": "Receita", "corpo": "1. Hidratante labial, 3x/dia"}) is None
        assert cdoc.emitir(c, CLINICA, kid, did, q) is None
        c.commit()
        a = cert.assinador_liberado(c, CLINICA, q, chave, AGORA)
        feitos, erro = cert.assinar_itens(c, CLINICA, a, cert.pendentes(c, CLINICA, q), datetime.now(timezone.utc))
        assert erro is None and [f["id"] for f in feitos] == [did]
        c.commit()
        pdf = cdoc.pdf(c, CLINICA, kid, cdoc.documento(c, CLINICA, kid, did))      # o PDF é o assinado
        publico = c.execute("select publico from clinica_assinaturas_icp where alvo='documento'").fetchone()[0]
    assert b"/ByteRange" in pdf and _confere(pdf, ac[1])
    doc = pymupdf.open(stream=pdf, filetype="pdf")
    info = int(doc.xref_get_key(-1, "Info")[1].split()[0])
    assert "Prescri" in doc.xref_get_key(info, "2.16.76.1.12.1.1")[1]
    assert doc.xref_get_key(info, cert.OID_CRM)[1] == "1234" and doc.xref_get_key(info, cert.OID_CRM_UF)[1] == "MA"
    texto = doc[0].get_text()
    assert "validar.iti.gov.br" in texto and "imprima e assine" not in texto and "Hidratante" in texto
    codigo = re.search(r"código\s+([A-Z2-9]{10})", texto).group(1)

    # o validador do ITI (e a farmácia): só com o código impresso
    farmacia = type(tela)(tela.app, follow_redirects=False)
    fmt = "application/validador-iti+json"
    assert farmacia.get(f"/doc/{publico}", params={"_format": fmt, "_secretCode": "ERRADO1234"}).status_code == 404
    j = farmacia.get(f"/doc/{publico}", params={"_format": fmt, "_secretCode": codigo}).json()
    url = j["prescription"]["signatureFiles"][0]["url"]
    assert url.startswith(f"https://zaq.teste/doc/{publico}/documento.pdf")
    r = farmacia.get(url.replace("https://zaq.teste", ""))
    assert r.status_code == 200 and r.content == pdf and "no-store" in r.headers["cache-control"]
    assert farmacia.get(f"/doc/{publico}/documento.pdf", params={"_secretCode": "ERRADO1234"}).status_code == 404
    assert "código impresso" in farmacia.get(f"/doc/{publico}").text
    with banco.connection() as c:
        assert any(f"abriu o documento #{did} pelo QR" == x["o_que"]
                   for x in acc.registro(c, CLINICA, datetime.now(timezone.utc)))


def test_nuvem_autoriza_no_aplicativo_e_assina_o_lote(tela, banco, zap, monkeypatch):  # noqa: F811
    ac = _ac()
    k, c_, _pfx = _ecpf(ac)
    monkeypatch.setenv("PSC_BIRDID_CLIENT_ID", "zaq")
    monkeypatch.setenv("PSC_BIRDID_CLIENT_SECRET", "segredo")
    visto = {}

    def _psc(req: httpx.Request) -> httpx.Response:
        if req.url.path.endswith("/oauth/token"):
            f = parse_qs(req.content.decode())
            visto["verifier"] = f["code_verifier"][0]
            return httpx.Response(200 if f["code"] == ["bom"] else 400,
                                  json={"access_token": "tok"} if f["code"] == ["bom"] else {"error": "invalid_grant"})
        assert req.headers["authorization"] == "Bearer tok"
        if req.url.path.endswith("/certificate-discovery"):
            return httpx.Response(200, json={"status": "S", "certificates": [{
                "alias": "MANOEL", "certificate": base64.b64encode(c_.public_bytes(serialization.Encoding.DER)).decode(),
                "certificate_chain": [base64.b64encode(ac[1].public_bytes(serialization.Encoding.DER)).decode()]}]})
        import json
        corpo = json.loads(req.content)
        visto["lote"] = len(corpo["hashes"])
        return httpx.Response(200, json={"signatures": [
            {"id": h["id"], "raw_signature": base64.b64encode(k.sign(
                base64.b64decode(h["hash"]), padding.PKCS1v15(), utils.Prehashed(hashes.SHA256()))).decode()}
            for h in corpo["hashes"]]})
    monkeypatch.setattr(psc, "_http", lambda: httpx.Client(transport=httpx.MockTransport(_psc)))

    kid, eid, _prof = _liberado(banco)
    tela.get("/_login/gestor/52")
    tela.post("/painel/clinica/certificado/nuvem", data={"provedor": "birdid"})
    e1, e2 = _evolucao(banco, kid, eid), _evolucao(banco, kid, eid, "segunda")
    for e in (e1, e2):
        tela.post(f"/painel/clinica/prontuario/{kid}/evolucao/{e}/assinar")
    assert "Assinar as 2 no aplicativo" in tela.get(f"/painel/clinica/prontuario/{kid}").text

    def _pedir():
        r = tela.post("/painel/clinica/certificado/assinar", data={"volta": f"/painel/clinica/prontuario/{kid}"})
        u = urlparse(r.headers["location"])
        assert u.netloc == "api.birdid.com.br" and u.path == "/v0/oauth/authorize"
        return parse_qs(u.query)
    p = _pedir()
    assert p["redirect_uri"] == ["https://zaq.teste/painel/clinica/certificado/volta"] and p["code_challenge_method"] == ["S256"]
    tela.get("/painel/clinica/certificado/volta", params={"code": "bom", "state": "outro"})
    assert "não confere" in _aviso(tela, f"/painel/clinica/prontuario/{kid}")
    p = _pedir()
    tela.get("/painel/clinica/certificado/volta", params={"code": "ruim", "state": p["state"][0]})
    assert "recusou" in _aviso(tela, f"/painel/clinica/prontuario/{kid}")
    p = _pedir()
    tela.get("/painel/clinica/certificado/volta", params={"code": "bom", "state": p["state"][0]})
    assert "2 assinadas com o certificado" in _aviso(tela, f"/painel/clinica/prontuario/{kid}")
    import hashlib
    ch = base64.urlsafe_b64encode(hashlib.sha256(visto["verifier"].encode()).digest()).rstrip(b"=").decode()
    assert ch == p["code_challenge"][0] and visto["lote"] == 2             # PKCE, e uma autorização pras duas
    with banco.connection() as c:
        for e in (e1, e2):
            assert _confere(cert.pdf_assinado(c, CLINICA, "evolucao", e), ac[1])
        assert c.execute("select metodo, provedor from clinica_assinaturas_icp limit 1").fetchone() == ("nuvem", "birdid")
        assert c.execute("select titular from clinica_certificados").fetchone()[0] == "MANOEL TESTE"


def test_provedor_sem_credencial_nao_manda_ninguem_pra_fora(tela, banco, zap, monkeypatch):  # noqa: F811
    monkeypatch.delenv("PSC_VIDAAS_CLIENT_ID", raising=False)
    kid, eid, _prof = _liberado(banco)
    tela.get("/_login/gestor/52")
    tela.post("/painel/clinica/certificado/nuvem", data={"provedor": "vidaas"})
    assert "ainda não está ligado" in _aviso(tela, "/painel/clinica/certificado")
    evo = _evolucao(banco, kid, eid)
    tela.post(f"/painel/clinica/prontuario/{kid}/evolucao/{evo}/assinar")
    r = tela.post("/painel/clinica/certificado/assinar", data={"volta": "https://fora.com"})
    assert r.headers["location"] == "/painel/clinica/certificado"                  # só volta pra dentro
    tela.post("/painel/clinica/certificado/nenhum")
    with banco.connection() as c:
        assert c.execute("select certificado from clinica_profissionais where id=%s", (_prof,)).fetchone()[0] == "nenhum"


def test_a_recepcao_nao_mexe_no_certificado(tela, banco, zap):  # noqa: F811
    _liberado(banco)
    tela.get("/_login/vendedor/51")
    assert tela.get("/painel/clinica/certificado").headers["location"] == "/painel/clinica/pacientes"
    assert tela.post("/painel/clinica/certificado/liberar", data={"senha": "x"}).headers["location"] == \
        "/painel/clinica/pacientes"


def test_antes_da_442(pool, zap):  # noqa: F811
    q = {"profissional_id": 1, "cadastros": [1], "membro_id": None}
    with pool.connection() as c:
        assert cert.estado(c, CLINICA, 1, AGORA)["tipo"] == "nenhum"
        assert cert.pendentes(c, CLINICA, q) == [] and cert.assinados(c, CLINICA, "evolucao", [1]) == {}
        assert cert.pdf_assinado(c, CLINICA, "documento", 1) is None
        assert cert.pelo_qr(c, "x", "y", AGORA) is None
        assert cert.assinador_liberado(c, CLINICA, q, "abc", AGORA) is None
