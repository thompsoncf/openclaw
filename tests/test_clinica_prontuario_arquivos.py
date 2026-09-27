"""Prontuário, fase 3: fotos clínicas e anexos, cifrados (finance/clinica_prontuario_arquivos.py).

O Storage é trocado por um dicionário: o que importa aqui é o que SAI do Zaq (cifrado),
quem abre (só o profissional liberado, registrado) e o termo de imagem.
"""
import base64
import io
import secrets
from datetime import datetime, timezone

import pytest

from finance import clinica_acesso_clinico as acc
from finance import clinica_prontuario_arquivos as parq
from finance import clinica_termos as ct
from tests.test_clinica_agenda import CLINICA
from tests.test_clinica_ficha_link import _completar, _ligar, _marcar, banco  # noqa: F401
from tests.test_clinica_pacientes import banco as _banco_pacientes  # noqa: F401
from tests.test_clinica_pacotes import pool, zap  # noqa: F401
from tests.test_clinica_prontuario import MANOEL, _liberado, tela  # noqa: F401


@pytest.fixture()
def cofre(monkeypatch):
    from finance import comprovantes
    guardado = {}
    monkeypatch.setenv("PRONTUARIO_CHAVE", base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
    monkeypatch.setattr(comprovantes, "configurado", lambda: True)
    monkeypatch.setattr(comprovantes, "subir_em", lambda caminho, dados, ct_: guardado.__setitem__(caminho, dados) or caminho)
    monkeypatch.setattr(comprovantes, "ler", lambda caminho: (guardado[caminho], "application/octet-stream"))
    return guardado


def _jpeg_com_gps() -> bytes:
    from PIL import Image
    img = Image.new("RGB", (40, 30), (200, 150, 120))
    exif = Image.Exif()
    exif[0x010F] = "Aparelho do Dr"            # fabricante
    exif[0x0110] = "Modelo X"                  # modelo (a localização vai no mesmo bloco de metadados)
    out = io.BytesIO()
    img.save(out, format="JPEG", exif=exif)
    return out.getvalue()


def test_a_foto_sai_cifrada_sem_gps_e_so_com_o_termo(cofre, banco, zap):  # noqa: F811
    kid, eid, _prof = _liberado(banco)                 # _completar aceitou imagem 'clinico'
    with banco.connection() as c:
        q = acc.leitor(c, CLINICA, MANOEL)
        foto = _jpeg_com_gps()
        aid, erro = parq.guardar(c, CLINICA, kid, q, tipo="foto", dados=foto, mimetype="image/jpeg",
                                 regiao="face", legenda="antes", evento_id=eid)
        assert erro is None
        c.commit()
        (caminho, blob), = cofre.items()
        assert caminho.startswith(f"prontuario/{CLINICA}/{kid}/") and blob.startswith(b"ZQP2")
        assert b"Aparelho" not in blob and b"JFIF" not in blob and b"\xff\xd8" not in blob[:20]   # cifrado
        dados, mime, _i = parq.abrir(c, CLINICA, kid, aid)
        assert mime == "image/jpeg" and dados[:2] == b"\xff\xd8"
        assert b"Aparelho do Dr" not in dados                      # sem os metadados do aparelho
        from PIL import Image
        assert not Image.open(io.BytesIO(dados)).getexif()
        lista = parq.listar(c, CLINICA, kid)
        assert lista[0]["regiao"] == "face" and "só uso clínico" in lista[0]["autorizacao"]
        # mexeram no arquivo guardado: não abre
        cofre[caminho] = blob[:-1] + bytes([blob[-1] ^ 1])
        with pytest.raises(ValueError):
            parq.abrir(c, CLINICA, kid, aid)
        # o arquivo de um paciente não abre como se fosse de outro
        assert parq.abrir(c, CLINICA, kid + 999, aid) is None


def test_sem_termo_so_com_papel_e_nao_autorizo_barra(cofre, banco, zap):  # noqa: F811
    kid, _eid, _prof = _liberado(banco)
    with banco.connection() as c:
        q = acc.leitor(c, CLINICA, MANOEL)
        c.execute("alter table clinica_termos_aceites disable trigger all")
        c.execute("delete from clinica_termos_aceites where cliente_id=%s", (kid,))
        c.execute("alter table clinica_termos_aceites enable trigger all")
        c.commit()
        _a, erro = parq.guardar(c, CLINICA, kid, q, tipo="foto", dados=_jpeg_com_gps(), mimetype="image/jpeg")
        assert "ainda não aceitou o termo de imagem" in erro
        aid, erro = parq.guardar(c, CLINICA, kid, q, tipo="foto", dados=_jpeg_com_gps(), mimetype="image/jpeg",
                                 papel_autorizado=True)
        assert erro is None and "papel" in parq.listar(c, CLINICA, kid)[0]["autorizacao"]
        t = ct.textos(c, CLINICA, "E", "L", False)
        ct.gravar_aceite(c, CLINICA, kid, "imagem", t["imagem"], opcao="nao", quem="Lúcia Ferreira",
                         papel="paciente", ip="", user_agent="")
        _a, erro = parq.guardar(c, CLINICA, kid, q, tipo="foto", dados=_jpeg_com_gps(), mimetype="image/jpeg",
                                papel_autorizado=True)
        assert erro == "O paciente não autorizou fotos (termo de imagem)."
        # anexo (o exame) continua valendo; foto como "anexo" não fura o "não autorizo"
        _a, erro = parq.guardar(c, CLINICA, kid, q, tipo="anexo", dados=b"%PDF-1.4 exame", mimetype="application/pdf")
        assert erro is None
        _a, erro = parq.guardar(c, CLINICA, kid, q, tipo="anexo", dados=_jpeg_com_gps(), mimetype="image/jpeg")
        assert "só se for foto de documento ou exame" in erro
        _a, erro = parq.guardar(c, CLINICA, kid, q, tipo="anexo", dados=_jpeg_com_gps(), mimetype="image/jpeg",
                                e_documento=True)
        assert erro is None and "documento" in parq.listar(c, CLINICA, kid)[0]["autorizacao"]
        _a, erro = parq.guardar(c, CLINICA, kid, q, tipo="foto", dados=b"x", mimetype="image/heic")
        assert "HEIC" in erro
        _a, erro = parq.guardar(c, CLINICA, kid, q, tipo="anexo", dados=b"MZ\x90 virus", mimetype="application/pdf")
        assert erro == "O arquivo não é um PDF."


def test_sem_a_chave_nao_guarda_nada(monkeypatch, banco, zap):  # noqa: F811
    monkeypatch.delenv("PRONTUARIO_CHAVE", raising=False)
    assert not parq.configurado()
    with banco.connection() as c:
        _a, erro = parq.guardar(c, CLINICA, 1, {"profissional_id": 1}, tipo="anexo", dados=b"%PDF", mimetype="application/pdf")
    assert "não está configurado" in erro


def test_quem_abre_a_foto_e_o_registro(cofre, tela, banco, zap):  # noqa: F811
    kid, _eid, _prof = _liberado(banco)
    with banco.connection() as c:
        q = acc.leitor(c, CLINICA, MANOEL)
        aid, _e = parq.guardar(c, CLINICA, kid, q, tipo="foto", dados=_jpeg_com_gps(), mimetype="image/jpeg")
        aid2, _e = parq.guardar(c, CLINICA, kid, q, tipo="foto", dados=_jpeg_com_gps(), mimetype="image/jpeg")
        c.commit()
    tela.get("/_login/vendedor/51")                               # a recepção não abre
    r = tela.get(f"/painel/clinica/prontuario/{kid}/arquivo/{aid}")
    assert r.status_code == 303
    tela.get("/_login/gestor/52")
    r = tela.get(f"/painel/clinica/prontuario/{kid}/arquivo/{aid}")
    assert r.status_code == 200 and r.content[:2] == b"\xff\xd8" and "no-store" in r.headers["cache-control"]
    assert tela.get(f"/painel/clinica/prontuario/{kid + 999}/arquivo/{aid}").status_code in (303, 404)
    html = tela.get(f"/painel/clinica/prontuario/{kid}/comparar?f={aid}&f={aid2}").text
    assert html.count("/arquivo/") == 2
    with banco.connection() as c:
        o_que = [x["o_que"] for x in acc.registro(c, CLINICA, datetime.now(timezone.utc))]
    assert f"abriu a foto #{aid}" in o_que


def test_as_fotos_nunca_mudam_nem_somem(cofre, banco, zap):  # noqa: F811
    import psycopg
    kid, _eid, _prof = _liberado(banco)
    with banco.connection() as c:
        q = acc.leitor(c, CLINICA, MANOEL)
        aid, _e = parq.guardar(c, CLINICA, kid, q, tipo="anexo", dados=b"%PDF-1.4", mimetype="application/pdf")
        c.commit()
        for sql in ("update clinica_prontuario_arquivos set legenda='x'", "delete from clinica_prontuario_arquivos"):
            with pytest.raises(psycopg.errors.RaiseException):
                c.execute(sql)
            c.rollback()


def test_o_agente_nao_importa_as_fotos():
    from pathlib import Path
    raiz = Path(__file__).resolve().parents[1]
    for arq in ("finance/agente.py", "finance/clinica_agente.py", "finance/clinica_ficha_link.py", "web/ficha_publica.py"):
        assert "clinica_prontuario_arquivos" not in (raiz / arq).read_text(encoding="utf-8"), arq
    assert _completar  # noqa: B018
    assert _ligar and _marcar  # noqa: B018


def test_upload_grande_e_recusado_antes_de_ler(cofre, tela, banco, zap):  # noqa: F811
    kid, _eid, _prof = _liberado(banco)
    tela.get("/_login/gestor/52")
    r = tela.post(f"/painel/clinica/prontuario/{kid}/arquivos", headers={"content-length": str(parq.TETO_CORPO + 1)},
                  content=b"x")
    assert r.status_code == 413
    tela.get("/_login/vendedor/51")                               # sem acesso: nem lê o arquivo
    r = tela.post(f"/painel/clinica/prontuario/{kid}/arquivos", files={"arquivo": ("a.pdf", b"%PDF-1", "application/pdf")},
                  data={"tipo": "anexo"})
    assert r.status_code == 303 and r.headers["location"] == f"/painel/clinica/pacientes/{kid}"


def test_a_chave_antiga_continua_abrindo(cofre, monkeypatch, banco, zap):  # noqa: F811
    kid, _eid, _prof = _liberado(banco)
    with banco.connection() as c:
        q = acc.leitor(c, CLINICA, MANOEL)
        aid, _e = parq.guardar(c, CLINICA, kid, q, tipo="anexo", dados=b"%PDF-1.4 antigo", mimetype="application/pdf")
        c.commit()
        import os
        antiga = os.environ["PRONTUARIO_CHAVE"]
        monkeypatch.setenv("PRONTUARIO_CHAVES_ANTIGAS", f"1:{antiga}")
        monkeypatch.setenv("PRONTUARIO_CHAVE", base64.urlsafe_b64encode(secrets.token_bytes(32)).decode())
        monkeypatch.setenv("PRONTUARIO_CHAVE_ID", "2")
        assert parq.abrir(c, CLINICA, kid, aid)[0] == b"%PDF-1.4 antigo"
