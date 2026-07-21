"""Funções puras do cliente Credify (sem rede): parsing tolerante, formatação
de telefone, detecção de celular e a escolha do decisor (sócio-administrador)."""
from finance import credify as cf


def test_pega_case_insensitive_e_pula_vazio():
    d = {"Nome": "", "NOME_SOCIO": "Thompson"}
    assert cf._pega(d, "nome", "nome_socio") == "Thompson"


def test_so_digitos():
    assert cf._so_digitos("(86) 98888-7777") == "86988887777"


def test_fmt_tel_fixo_e_celular():
    assert cf._fmt_tel("86", "32220000") == "(86) 3222-0000"
    assert cf._fmt_tel("86", "988887777") == "(86) 98888-7777"


def test_eh_movel():
    assert cf._eh_movel("86", "988887777") is True   # 11 dígitos, 9 na frente
    assert cf._eh_movel("86", "32220000") is False   # fixo


def test_escolher_decisor_prefere_administrador():
    socios = [
        {"nome": "Fulano", "cpf": "11111111111", "qualificacao": "Sócio"},
        {"nome": "Beltrano", "cpf": "22222222222", "qualificacao": "Sócio-Administrador"},
    ]
    assert cf._escolher_decisor(socios)["nome"] == "Beltrano"


def test_escolher_decisor_prefere_quem_tem_cpf():
    socios = [
        {"nome": "SemCpf", "cpf": None, "qualificacao": "Sócio"},
        {"nome": "ComCpf", "cpf": "22222222222", "qualificacao": "Sócio"},
    ]
    assert cf._escolher_decisor(socios)["nome"] == "ComCpf"


def test_escolher_decisor_vazio():
    assert cf._escolher_decisor([]) is None


def test_extrai_token_do_envelope_credify():
    # sucesso: token dentro de Dados (dict)
    assert cf._extrai_token({"Sucess": True, "Dados": {"token": "abc123def456ghi789xy"}}) == "abc123def456ghi789xy"
    # sucesso: Dados é a própria string do token
    assert cf._extrai_token({"Sucess": True, "Dados": "x" * 30}) == "x" * 30
    # falha típica: sem token
    assert cf._extrai_token({"Sucess": False, "Message": "LOGON OU SENHA INVALIDOS", "Dados": False}) is None


def test_decisor_sem_credenciais(monkeypatch):
    monkeypatch.delenv("CREDIFY_CLIENT_ID", raising=False)
    monkeypatch.delenv("CREDIFY_CLIENT_SECRET", raising=False)
    r = cf.decisor_com_telefone("12345678000199")
    assert r["ok"] is False and r["erro"] == "sem_credenciais"
