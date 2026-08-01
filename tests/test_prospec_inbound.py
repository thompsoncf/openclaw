"""Botões do template de 1º contato da prospecção (finance/prospec_inbound.py).

Testa a classificação do clique e a montagem da resposta automática — lógica pura,
sem banco.
"""
from finance import prospec_inbound as pi


def test_classificar_botoes_do_template():
    assert pi.classificar("Quero te conhecer") == "conhecer"
    assert pi.classificar("Quero o material") == "material"
    assert pi.classificar("Agora não") == "nao"


def test_classificar_texto_livre():
    assert pi.classificar("quero sim") == "material"
    assert pi.classificar("manda o material aí") == "material"
    assert pi.classificar("agora não, obrigado") == "nao"
    # não reconhecível → deixa a IA seguir
    assert pi.classificar("oi tudo bem?") is None
    assert pi.classificar("") is None
    assert pi.classificar(None) is None


def test_normalizar_instagram():
    assert pi.normalizar_instagram("@thompsoncf") == "https://www.instagram.com/thompsoncf"
    assert pi.normalizar_instagram("thompsoncf") == "https://www.instagram.com/thompsoncf"
    assert pi.normalizar_instagram("https://instagram.com/x") == "https://instagram.com/x"
    assert pi.normalizar_instagram("") == ""


def test_resposta_conhecer_usa_instagram():
    txt = pi.resposta("conhecer", {"instagram": "@thompsoncf"})
    assert "https://www.instagram.com/thompsoncf" in txt


def test_resposta_material_com_e_sem_material():
    com = pi.resposta("material", {"instagram": "@x"}, material="https://ex.com/pdf")
    assert "https://ex.com/pdf" in com
    sem = pi.resposta("material", {}, material="")
    assert "material" in sem.lower()


def test_resposta_nao_nao_promete_material():
    txt = pi.resposta("nao", {"instagram": "@x"})
    assert "disposição" in txt.lower() or "mudar de ideia" in txt.lower()
