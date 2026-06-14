"""Testes da leitura de QR code de NFC-e."""
from finance.nfce_qr import extrair_chave


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
