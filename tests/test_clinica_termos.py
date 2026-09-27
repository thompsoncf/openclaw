"""Os termos da clínica (finance/clinica_termos.py, /painel/clinica/termos): o texto da
clínica no lugar do padrão, o termo por procedimento no link e a cópia em PDF.
"""
from finance import clinica_ficha_link as cfl
from finance import clinica_termos as ct
from tests.test_clinica_agenda import AGORA, CLINICA
from tests.test_clinica_ficha_link import _completar, _ligar, _marcar, banco, cli  # noqa: F401
from tests.test_clinica_pacientes import _ficha_do_evento
from tests.test_clinica_pacientes import banco as _banco_pacientes  # noqa: F401
from tests.test_clinica_pacotes import _paciente, _tipo, pool, zap  # noqa: F401

TEXTO = "Autorizo {clinica} a usar os dados de {paciente} para o atendimento e a nota fiscal, com sigilo."


def test_o_texto_da_clinica_substitui_o_padrao_e_editar_sobe_a_versao(banco, zap):  # noqa: F811
    with banco.connection() as c:
        assert ct.salvar_modelo(c, CLINICA, "lgpd", "Dados", "curto") == "O texto do termo está curto demais."
        assert ct.salvar_modelo(c, CLINICA, "lgpd", "Uso de dados", TEXTO) is None
        c.commit()
        t = ct.textos(c, CLINICA, "Espaço Pelle", "Lúcia Ferreira", False)
        assert t["lgpd"]["texto"].startswith("Autorizo Espaço Pelle a usar os dados de Lúcia Ferreira")
        assert t["lgpd"]["versao"] == "clinica-v1" and t["imagem"]["versao"] == ct.VERSAO_PADRAO
        assert ct.textos(c, CLINICA, "E", "Pedro", True)["lgpd"]["texto"].startswith("Como responsável legal por Pedro")
        assert ct.salvar_modelo(c, CLINICA, "lgpd", "Uso de dados", TEXTO + " Versão nova.") is None
        c.commit()
        assert ct.textos(c, CLINICA, "E", "P", False)["lgpd"]["versao"] == "clinica-v2"
        m = next(x for x in ct.modelos(c, CLINICA) if x["chave"] == "lgpd")
        ct.desativar(c, CLINICA, m["id"])
        c.commit()
        assert ct.textos(c, CLINICA, "E", "P", False)["lgpd"]["versao"] == ct.VERSAO_PADRAO


def test_o_termo_do_procedimento_entra_no_link_e_na_ficha_completa(banco, zap):  # noqa: F811
    with banco.connection() as c:
        _ligar(c)
        consulta = _tipo(c, "Consulta")["id"]
        lead, _conv = _paciente(c)
        kid = _ficha_do_evento(c, _marcar(c, lead))
        c.commit()
    _completar(banco, kid)                  # cadastro, pré-consulta, dados e imagem
    with banco.connection() as c:
        assert cfl.situacao(c, CLINICA, kid, AGORA)["completa"]
        # a clínica escreve o termo da consulta: agora falta o do procedimento
        assert ct.salvar_modelo(c, CLINICA, "procedimento", "Termo da consulta", TEXTO) == \
            "Escolha o atendimento deste termo."
        assert ct.salvar_modelo(c, CLINICA, "procedimento", "Termo da consulta", TEXTO,
                                servico_id=consulta) is None
        c.commit()
        s = cfl.situacao(c, CLINICA, kid, AGORA)
        assert not s["termos_ok"] and "termos" in s["falta"]
        f = cfl.por_token(c, cfl.token(c, CLINICA, kid))
        base = {"lgpd": "1", "imagem": "clinico", "nome": "Lúcia Ferreira"}
        assert cfl.salvar_termos(c, f, base, empresa="Espaço Pelle", ip="", user_agent="", agora=AGORA) == \
            "Para seguir, é preciso aceitar o termo do procedimento."
        assert cfl.salvar_termos(c, f, {**base, "procedimento": "1"}, empresa="Espaço Pelle", ip="",
                                 user_agent="", agora=AGORA) is None
        c.commit()
        assert cfl.situacao(c, CLINICA, kid, AGORA)["completa"]
        a = [x for x in ct.aceitos(c, CLINICA, kid) if x["termo"] == "procedimento"]
        assert a and a[0]["servico_id"] == consulta and a[0]["titulo"] == "Termo da consulta"


def test_o_pdf_do_aceite_so_da_propria_ficha(cli, banco, zap):  # noqa: F811
    with banco.connection() as c:
        _ligar(c)
        lead, _conv = _paciente(c)
        kid = _ficha_do_evento(c, _marcar(c, lead))
        outro_lead, _c2 = _paciente(c, nome="Rita Souza", fone="+5599977776666")
        c.commit()
    _completar(banco, kid)
    with banco.connection() as c:
        aceite = ct.aceitos(c, CLINICA, kid)[0]["id"]
        doc = ct.pdf(c, CLINICA, kid, aceite)
        assert doc and doc[:4] == b"%PDF"
        assert ct.pdf(c, CLINICA, kid + 999, aceite) is None           # aceite de outra ficha: nada
        assert ct.pdf(c, CLINICA + 1, kid, aceite) is None             # de outra conta: nada
        tok = cfl.token(c, CLINICA, kid)
        c.commit()
    cli.get("/_papel/vendedor/51")
    r = cli.get(f"/painel/clinica/pacientes/{kid}/termos/{aceite}.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert "no-store" in r.headers["cache-control"] and f"termo-{aceite}.pdf" in r.headers["content-disposition"]
    # no link: só quem provou a data
    celular = type(cli)(cli.app, follow_redirects=False)
    assert celular.get(f"/ficha/{tok}/termo/{aceite}.pdf").status_code == 303
    celular.post(f"/ficha/{tok}/entrar", data={"nascimento": "1990-05-17"})
    assert celular.get(f"/ficha/{tok}/termo/{aceite}.pdf").content[:4] == b"%PDF"
    assert "(PDF)" in celular.get(f"/ficha/{tok}?passo=4").text


def test_a_tela_dos_termos_e_de_quem_manda(cli, banco, zap):  # noqa: F811
    cli.get("/_papel/vendedor/51")
    assert cli.get("/painel/clinica/termos").status_code == 303
    cli.get("/_papel/gestor/51")
    html = cli.get("/painel/clinica/termos").text
    assert "Termos da clínica" in html and "texto padrão do Zaq" in html
    r = cli.post("/painel/clinica/termos/salvar", data={"chave": "imagem", "titulo": "Fotos", "texto": TEXTO})
    assert r.status_code == 303 and "salvo" in r.headers["location"]
    assert "texto da clínica, versão 1" in cli.get("/painel/clinica/termos").text


def test_antes_da_417_vale_o_padrao(pool, zap):  # noqa: F811
    with pool.connection() as c:
        assert ct.modelos(c, CLINICA) == [] and ct.servicos_com_termo(c, CLINICA) == set()
        assert ct.textos(c, CLINICA, "E", "P", False)["lgpd"]["versao"] == ct.VERSAO_PADRAO


def test_no_dia_com_a_paciente_presente_o_termo_continua_valendo(banco, zap):  # noqa: F811
    from finance import clinica_agenda as ca
    with banco.connection() as c:
        _ligar(c)
        consulta = _tipo(c, "Consulta")["id"]
        lead, _conv = _paciente(c)
        eid = _marcar(c, lead)
        kid = _ficha_do_evento(c, eid)
        for st in ("confirmado", "presente"):
            assert ca.mudar_situacao(c, CLINICA, eid, st) is None
        assert ct.salvar_modelo(c, CLINICA, "procedimento", "Termo da consulta", TEXTO, servico_id=consulta) is None
        c.commit()
        f = cfl.por_token(c, cfl.token(c, CLINICA, kid))
        t = cfl.textos_dos_termos(c, CLINICA, "E", "Lúcia", False, cfl.servico_do_proximo(c, CLINICA, kid, AGORA)[1])
        assert t["procedimento"] and t["procedimento"]["servico_id"] == consulta
        assert cfl.salvar_termos(c, f, {"lgpd": "1", "imagem": "clinico", "nome": "Lúcia Ferreira"},
                                 empresa="E", ip="", user_agent="", agora=AGORA) == \
            "Para seguir, é preciso aceitar o termo do procedimento."


def test_o_texto_da_tela_e_o_texto_gravado(banco, zap):  # noqa: F811
    with banco.connection() as c:
        _ligar(c)
        lead, _conv = _paciente(c)
        kid = _ficha_do_evento(c, _marcar(c, lead))
        c.commit()
        f = cfl.por_token(c, cfl.token(c, CLINICA, kid))
        visto = cfl.versoes_vistas(cfl.textos_dos_termos(c, CLINICA, "E", "L", False))
        # a clínica edita enquanto a paciente lê
        assert ct.salvar_modelo(c, CLINICA, "lgpd", "Uso de dados", TEXTO) is None
        c.commit()
        form = {"lgpd": "1", "imagem": "clinico", "nome": "Lúcia Ferreira", "versoes": visto}
        assert cfl.salvar_termos(c, f, form, empresa="E", ip="", user_agent="", agora=AGORA) == \
            "Os termos mudaram enquanto você lia. Leia de novo e aceite."
        novo = cfl.versoes_vistas(cfl.textos_dos_termos(c, CLINICA, "E", "L", False))
        assert cfl.salvar_termos(c, f, {**form, "versoes": novo}, empresa="E", ip="", user_agent="",
                                 agora=AGORA) is None


def test_quebras_de_linha_limite_e_versao_que_nao_repete(banco, zap):  # noqa: F811
    with banco.connection() as c:
        assert ct.salvar_modelo(c, CLINICA, "imagem", "Fotos", "Cláusula 1.\r\n\r\nCláusula 2, com fotos do rosto.") is None
        m = next(x for x in ct.modelos(c, CLINICA) if x["chave"] == "imagem")
        assert "\r" not in m["texto"] and "\n\n" in m["texto"] and m["versao"] == 1
        assert "encurte" in ct.salvar_modelo(c, CLINICA, "imagem", "Fotos", "x" * (ct.LIMITE + 1))
        ct.desativar(c, CLINICA, m["id"])
        assert ct.salvar_modelo(c, CLINICA, "imagem", "Fotos", "Outro texto das fotos, depois de voltar ao padrão.") is None
        c.commit()
        assert next(x for x in ct.modelos(c, CLINICA) if x["chave"] == "imagem")["versao"] == 2


def test_a_versao_nova_do_procedimento_pede_de_novo(banco, zap):  # noqa: F811
    with banco.connection() as c:
        _ligar(c)
        consulta = _tipo(c, "Consulta")["id"]
        lead, _conv = _paciente(c)
        kid = _ficha_do_evento(c, _marcar(c, lead))
        c.commit()
    _completar(banco, kid)
    with banco.connection() as c:
        assert ct.salvar_modelo(c, CLINICA, "procedimento", "Termo", TEXTO, servico_id=consulta) is None
        c.commit()
        f = cfl.por_token(c, cfl.token(c, CLINICA, kid))
        assert cfl.salvar_termos(c, f, {"lgpd": "1", "imagem": "clinico", "nome": "Lúcia Ferreira", "procedimento": "1"},
                                 empresa="E", ip="", user_agent="", agora=AGORA) is None
        c.commit()
        assert cfl.situacao(c, CLINICA, kid, AGORA)["completa"]
        assert ct.salvar_modelo(c, CLINICA, "procedimento", "Termo", TEXTO + " Nova cláusula.",
                                servico_id=consulta) is None
        c.commit()
        assert not cfl.situacao(c, CLINICA, kid, AGORA)["termos_ok"]


def test_a_recepcao_nao_salva_termo_nem_por_post(cli, banco, zap):  # noqa: F811
    cli.get("/_papel/vendedor/51")
    r = cli.post("/painel/clinica/termos/salvar", data={"chave": "lgpd", "titulo": "X", "texto": TEXTO})
    assert r.status_code == 303 and r.headers["location"] == "/painel/clinica/agenda"
    with banco.connection() as c:
        assert ct.modelos(c, CLINICA) == []
