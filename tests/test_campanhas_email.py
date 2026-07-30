"""Testes puros (sem banco) da montagem do e-mail de campanha: saudação pela
empresa, assinatura com nome da empresa + responsável + telefone, e remoção da
despedida que a IA às vezes cola no fim."""
from finance import campanhas_motor as m

IDN = {"empresa": "OPAPAY", "responsavel": "Thompson Cavalcante Fernandes",
       "telefone": "(86) 9525-5525"}


def test_assinatura_texto_usa_empresa_responsavel_telefone():
    txt = m._assinatura_texto(IDN)
    assert "Equipe OPAPAY" in txt          # a "equipe" chama o nome da EMPRESA
    assert "Thompson Cavalcante Fernandes" in txt  # responsável embaixo
    assert "(86) 9525-5525" in txt         # telefone cadastrado no ZAQ


def test_assinatura_sem_duplicar_quando_empresa_igual_responsavel():
    idn = {"empresa": "ACME", "responsavel": "ACME", "telefone": ""}
    txt = m._assinatura_texto(idn)
    assert txt.count("ACME") == 1          # não repete a linha do responsável


def test_tira_assinatura_da_ia():
    corpo = ("Olá, equipe Sorrir Odonto!\n\nA gente ajuda clínicas.\n\n"
             "Faz sentido conversar?\n\nAtenciosamente,\nEquipe Thompson Cavalcante Fernandes")
    limpo = m._tira_assinatura(corpo)
    assert "Atenciosamente" not in limpo
    assert "Thompson" not in limpo
    assert "Faz sentido conversar?" in limpo


def test_html_tem_assinatura_unica_e_com_empresa():
    corpo = ("Olá, equipe Sorrir Odonto!\n\nA gente ajuda.\n\nConversamos?\n\n"
             "Abraços,\nEquipe Fulano")
    html = m._html(corpo, {"whatsapp": None}, IDN, "http://x/descad", "http://x/int")
    assert "Equipe OPAPAY" in html
    assert "Thompson Cavalcante Fernandes" in html
    assert "(86) 9525-5525" in html
    assert html.count("Atenciosamente") == 1   # não duplica a assinatura


def test_html_aceita_string_por_compatibilidade():
    html = m._html("corpo", {"whatsapp": None}, "OPAPAY", "http://x/d")
    assert "Equipe OPAPAY" in html
