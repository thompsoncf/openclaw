"""A ficha com os campos do Amigo, o check-in no balcão e as etiquetas (migração 413).

Reaproveita o banco e as telas do link da ficha (tests/test_clinica_ficha_link.py).
"""
from datetime import date, timedelta

from finance import clinica_ficha_link as cfl
from finance import clinica_pacientes as cpa
from tests.test_clinica_agenda import AGORA, CLINICA
from tests.test_clinica_ficha_link import _ligar, _marcar, banco, cli  # noqa: F401
from tests.test_clinica_pacientes import _ficha_do_evento
from tests.test_clinica_pacientes import banco as _banco_pacientes  # noqa: F401
from tests.test_clinica_pacotes import FONE, _paciente, pool, zap  # noqa: F401

BASE_CAD = {"nome": "Lúcia Ferreira", "fone": FONE, "nascimento": "1990-05-17", "cidade": "Pedreiras"}


def _ficha(banco, ligar=True):
    with banco.connection() as c:
        if ligar:
            _ligar(c)
        lead, _conv = _paciente(c)
        kid = _ficha_do_evento(c, _marcar(c, lead))
        c.commit()
    return kid


def test_a_recepcao_grava_os_campos_novos_e_as_etiquetas(banco, zap):  # noqa: F811
    kid = _ficha(banco, ligar=False)
    assert cpa.salvar_cadastro(banco, CLINICA, kid, {**BASE_CAD, "sexo": "x"}) == "Sexo inválido."
    assert cpa.salvar_cadastro(banco, CLINICA, kid, {
        **BASE_CAD, "nome_social": "Lu", "sexo": "f", "profissao": "Agricultora", "rg": "123",
        "nome_mae": "Maria Ferreira", "endereco": "Rua A", "numero": "12", "complemento": "casa 2",
        "bairro": "Centro", "contato_emergencia": "João", "fone_emergencia": "99 98888-0000",
        "etiquetas": "VIP, pós-operatório , vip"}) is None
    with banco.connection() as c:
        p = cpa.ficha(c, CLINICA, kid, AGORA)
        assert (p["nome_social"], p["sexo_txt"], p["profissao"], p["bairro"], p["numero"]) == \
            ("Lu", "Feminino", "Agricultora", "Centro", "12")
        assert p["etiquetas"] == ["VIP", "pós-operatório"]
        d = cpa.listar(c, CLINICA, AGORA, etiqueta="vip")
        assert d["total"] == 1 and d["etiquetas"] == ["pós-operatório", "VIP"]
        assert cpa.listar(c, CLINICA, AGORA, etiqueta="sensível")["total"] == 0
        assert cpa.listar(c, CLINICA, AGORA, busca="lu")["total"] == 1
    # campo apagado na tela apaga mesmo (a recepção é quem cuida do cadastro)
    assert cpa.salvar_cadastro(banco, CLINICA, kid, {**BASE_CAD, "profissao": "", "etiquetas": ""}) is None
    with banco.connection() as c:
        p = cpa.ficha(c, CLINICA, kid, AGORA)
    assert p["profissao"] == "" and p["etiquetas"] == [] and p["bairro"] == "Centro"   # o que não veio fica


def test_o_link_preenche_os_campos_e_sem_a_data_so_o_vazio(banco, zap):  # noqa: F811
    kid = _ficha(banco)
    cpa.salvar_cadastro(banco, CLINICA, kid, {"nome": "Lúcia Ferreira", "fone": FONE, "profissao": "Professora"})
    with banco.connection() as c:
        f = cfl.por_token(c, cfl.token(c, CLINICA, kid))
        c.commit()
    form = {"nome": "Lúcia Ferreira", "nascimento": "1990-05-17", "cidade": "Pedreiras", "cpf": "529.982.247-25",
            "profissao": "Outra", "bairro": "Centro", "sexo": "f", "rg": "999"}
    assert cfl.salvar_cadastro(banco, f, form, AGORA, verificado=False) == (None, None)
    with banco.connection() as c:
        p = cpa.ficha(c, CLINICA, kid, AGORA)
    assert p["profissao"] == "Professora"           # sem a data, não troca o que estava
    assert p["bairro"] == "Centro" and p["sexo"] == "f"
    assert p["rg"] == ""                            # RG e nome da mãe são da recepção, não do link
    assert cfl.salvar_cadastro(banco, f, {**form, "sexo": "z"}, AGORA)[0] == "Sexo inválido."


def test_o_balcao_abre_uma_vez_e_fecha_no_fim(cli, banco, zap):  # noqa: F811
    kid = _ficha(banco)
    cpa.salvar_cadastro(banco, CLINICA, kid, {**BASE_CAD, "endereco": "Rua Guardada, 7"})
    cli.get("/_papel/vendedor/51")
    html = cli.post(f"/painel/clinica/pacientes/{kid}/balcao").text
    assert "<svg" in html and "Vale uma vez" in html
    url = html.split('readonly value="')[1].split('"')[0]
    # F5 na tela do QR: o mesmo código (o QR que o paciente está lendo continua valendo)
    assert url in cli.post(f"/painel/clinica/pacientes/{kid}/balcao").text
    rota = url.split("app.zaq-ia.com")[-1]
    tok = rota.split("/ficha/")[1].split("?")[0]
    cod = rota.split("?b=")[1]
    # num aparelho logado no painel, não abre
    assert "logado no Zaq" in cli.get(rota).text
    tablet = type(cli)(cli.app, follow_redirects=False)        # o tablet: sem login no painel
    for _ in range(2):                                         # a prévia do WhatsApp faz GET: não gasta
        html = tablet.get(rota)
        assert "Começar" in html.text and html.headers["cache-control"].startswith("no-store")
    r = tablet.post(f"/ficha/{tok}/balcao", data={"b": cod})
    assert r.status_code == 303 and r.headers["location"] == f"/ficha/{tok}"
    r = tablet.get(f"/ficha/{tok}?passo=1")
    assert "Rua Guardada" in r.text and 'autocomplete="off"' in r.text      # no balcão vê a ficha
    assert r.headers["cache-control"].startswith("no-store")                # e o Voltar não traz de volta
    outro = type(cli)(cli.app, follow_redirects=True)
    assert "já foi usado" in outro.post(f"/ficha/{tok}/balcao", data={"b": cod}).text   # uma vez só
    tablet.post(f"/ficha/{tok}/preconsulta", data={"queixa": "manchas", "alergia": "nao", "remedios": "nao",
                                                   "gravidez": "na"})
    tablet.post(f"/ficha/{tok}/termos", data={"lgpd": "1", "imagem": "clinico", "nome": "Lúcia Ferreira"})
    assert "devolver o tablet" in tablet.get(f"/ficha/{tok}?passo=4").text
    assert "confirme a data de nascimento" in tablet.get(f"/ficha/{tok}").text   # fechou


def test_o_proximo_do_balcao_derruba_a_ficha_de_quem_parou_no_meio(cli, banco, zap):  # noqa: F811
    a = _ficha(banco)
    with banco.connection() as c:
        lead, _conv = _paciente(c, nome="Rita Souza", fone="+5599977776666")
        b = _ficha_do_evento(c, _marcar(c, lead, date(2026, 9, 29), nome="Rita Souza"))
        cpa.salvar_cadastro(banco, CLINICA, a, BASE_CAD)
        tok_a, cod_a = cfl.gerar_balcao(c, CLINICA, a, AGORA.replace(year=2030))
        tok_b, cod_b = cfl.gerar_balcao(c, CLINICA, b, AGORA.replace(year=2030))
        c.commit()
    tablet = type(cli)(cli.app, follow_redirects=False)
    with banco.connection() as c:              # os códigos valendo "agora" de verdade
        c.execute("update clientes set ficha_balcao_ate = now() + interval '10 minutes' where id = any(%s)",
                  ([a, b],))
        c.commit()
    tablet.post(f"/ficha/{tok_a}/balcao", data={"b": cod_a})
    assert "Passo" in tablet.get(f"/ficha/{tok_a}?passo=1").text        # a Lúcia parou no passo 1
    tablet.post(f"/ficha/{tok_b}/balcao", data={"b": cod_b})           # a Rita pega o tablet
    assert "confirme a data de nascimento" in tablet.get(f"/ficha/{tok_a}").text


def test_o_codigo_do_balcao_vence_e_o_link_desligado_nao_gera(cli, banco, zap):  # noqa: F811
    kid = _ficha(banco)
    with banco.connection() as c:
        tok, cod = cfl.gerar_balcao(c, CLINICA, kid, AGORA - timedelta(minutes=20))
        c.commit()
        f = cfl.por_token(c, tok)
        assert not cfl.usar_balcao(c, f, cod, AGORA)            # passou dos 15 minutos
        assert cfl.salvar_ligado(c, CLINICA, "off") is None
        c.commit()
    cli.get("/_papel/vendedor/51")
    r = cli.post(f"/painel/clinica/pacientes/{kid}/balcao")
    assert r.status_code == 303 and r.headers["location"].startswith(f"/painel/clinica/pacientes/{kid}")


def test_antes_da_413_a_ficha_abre(pool, zap):  # noqa: F811
    with pool.connection() as c:
        assert cpa._extras(c, CLINICA, [1]) == {}
        assert cpa.salvar_extras(c, CLINICA, 1, {"profissao": "x"}) is None
