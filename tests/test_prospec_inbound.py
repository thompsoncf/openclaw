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


def test_negacao_nao_pode_virar_aceite():
    """O defeito: "não quero" contém "quero", e o aceite era testado ANTES da recusa —
    o Zaq mandava o material justamente pra quem tinha acabado de recusar, e ainda
    esquentava o lead. Foi encontrado numa varredura de respostas de campanha."""
    for t in ("não quero", "nao quero", "não quero nada", "não quero mais receber",
              "não tenho interesse", "sem interesse", "não obrigado"):
        assert pi.classificar(t) == "nao", t


def test_pedidos_de_parada_sao_recusa():
    for t in ("pare de mandar", "parar", "quero sair", "me remove", "descadastrar",
              "cancelar"):
        assert pi.classificar(t) == "nao", t


def test_aceite_continua_valendo_depois_do_conserto():
    """A recusa vir primeiro não pode ter comido os aceites."""
    for t in ("quero o material", "quero sim", "sim", "aceito", "bora",
              "pode mandar", "manda aí"):
        assert pi.classificar(t) == "material", t
    assert pi.classificar("quero te conhecer") == "conhecer"


def test_resposta_automatica_de_empresa_nao_e_classificada():
    """Os bots que apareceram na base real: nenhum é aceite nem recusa — seguem pro
    fluxo normal, onde a trava de continuidade decide se vira lead."""
    for t in ("O Dom agradece seu contato, no momento não estamos disponíveis. "
              "Nosso horário de atendimento é de 8h às 18h.",
              "✨ Seja bem-vinda(o)! É um prazer falar com você.",
              "🌸 Olá, seja bem-vinda(o) ao atendimento online do Espaço da Mulher"):
        assert pi.classificar(t) is None, t


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
