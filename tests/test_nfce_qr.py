"""Testes da leitura de QR code de NFC-e."""
from datetime import date
from finance.nfce_qr import extrair_chave, metadados


def test_extrai_chave_de_todos_os_estados():
    """Padrao nacional: a chave de 44 digitos vem sempre depois de p=, qualquer
    estado, qualquer versao de QR (v2, v3, contingencia com digitos extras).
    Blinda contra a regressao do 'juntar todos os digitos' que quebrava no PI."""
    casos = [
        "http://webas.sefaz.pi.gov.br/nfceweb/consultarNFCe.jsf?p=22260642591651159258650740004434901193066567|3|1",
        "https://nfce.fazenda.sp.gov.br/qrcode?p=35250712345678000191650010000000011234567890|3|1",
        "https://nfce.fazenda.sp.gov.br/qrcode?p=35250712345678000191650010000000021234567890|3|1|09|150.75|a1b2|12345678901",
        "http://www.fazenda.pr.gov.br/nfce/qrcode?p=41250712345678000191650010000000011234567890|2|1|1|ABCDEF1234",
        "http://nfce.sefaz.pe.gov.br/nfce-web/consultarNFCe?p=26250712345678000191650010000000011234567890|2|1|1|ABC",
    ]
    for url in casos:
        ch = extrair_chave(url)
        assert ch is not None and len(ch) == 44, f"falhou em: {url}"
        assert ch == url.split("p=")[1][:44]


def test_metadados_extrai_uf_cnpj_data():
    """Extrai UF (pos 0-2), AAMM->data (pos 2-6) e CNPJ (pos 6-20).
    Blinda contra reescrita que deixava UF/CNPJ/data como None/vazio."""
    # Chave PI: 22260642591651159258650740004434901193066567
    # Pos 0-2: "22" = PI
    # Pos 2-6: "2606" = junho/2026
    # Pos 6-20: "31838128002287" = CNPJ
    chave = "22260631838128002287650010000000011234567890"
    m = metadados(chave)
    assert m["uf"] == "PI", f"UF incorreta: {m['uf']}"
    assert m["data_emissao"] == date(2026, 6, 1), f"data incorreta: {m['data_emissao']}"
    assert m["cnpj_emitente"] == "31838128002287", f"CNPJ incorreto: {m['cnpj_emitente']}"

    # Testa SP também
    chave_sp = "35260131838128002287650010000000011234567890"
    m_sp = metadados(chave_sp)
    assert m_sp["uf"] == "SP", f"UF SP incorreta: {m_sp['uf']}"
    assert m_sp["data_emissao"] == date(2026, 1, 1), f"data SP incorreta: {m_sp['data_emissao']}"
