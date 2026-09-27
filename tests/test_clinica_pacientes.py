"""Os pacientes da clínica (finance/clinica_pacientes.py e /painel/clinica/pacientes).

Reaproveita o banco do test_clinica_pacotes (a semente da Espaço Pelle, o Dr. Manoel)
e põe por cima a ficha de clientes do Zaq (064, 066, 149, 182) e a 403.
"""
from datetime import date, datetime, time, timedelta, timezone

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from starlette.middleware.sessions import SessionMiddleware

from finance import clinica_agenda as ca
from finance import clinica_pacientes as cpa
from tests.test_clinica_agenda import AGORA, BASE, CLINICA
from tests.test_clinica_pacotes import (FONE, _manoel, _paciente, _plano_aceito, _sessao, _tipo,  # noqa: F401
                                        pool, zap)


@pytest.fixture()
def banco(pool):  # noqa: F811
    with pool.connection() as c:
        for m in ("064_clientes_lojista.sql", "149_cliente_cidade_uf.sql", "182_clientes_papel.sql"):
            c.execute((BASE / m).read_text(encoding="utf-8"))
        # da 066, só o que é de pessoas e clientes (ela também altera lançamentos)
        c.execute("""create table pessoas (id bigserial primary key, cpf text, celular text, nome text not null,
                                           email text, conta_zaq_id bigint, cnpj text, tipo text,
                                           criado_em timestamptz not null default now(),
                                           atualizado_em timestamptz not null default now());
                     create unique index ux_pessoas_cpf on pessoas (cpf) where cpf is not null;
                     alter table clientes add column if not exists pessoa_id bigint references pessoas(id)""")
        c.execute("""alter table clientes add column if not exists endereco text;
                     alter table clientes add column if not exists cep text;
                     alter table prospeccao add column if not exists cidade text;
                     alter table titulos add column if not exists descricao text;
                     alter table titulos add column if not exists tipo text default 'receber';
                     alter table titulos add column if not exists cliente_id bigint""")
        c.execute((BASE / "403_clinica_pacientes.sql").read_text(encoding="utf-8"))
        c.commit()
    return pool


def _ficha_do_evento(c, eid):
    return c.execute("select cliente_id from eventos_agenda where id=%s", (eid,)).fetchone()[0]


def test_agendar_cria_a_ficha_e_o_filho_no_mesmo_whatsapp_ganha_a_dele(banco, zap):  # noqa: F811
    with banco.connection() as c:
        lead, _conv = _paciente(c)                                   # Lúcia Ferreira
        e1 = _sessao(c, lead, date(2026, 9, 28), tipo="Consulta")
        e2 = _sessao(c, lead, date(2026, 9, 29), tipo="Consulta")
        eid, erro = ca.agendar(c, CLINICA, profissional_id=_manoel(c), servico_id=_tipo(c, "Consulta")["id"],
                               inicio=ca.utc(date(2026, 9, 30), time(9)), lead_id=lead, paciente="Pedro Ferreira",
                               agora=AGORA)
        assert erro is None
        c.commit()
        lucia, pedro = _ficha_do_evento(c, e1), _ficha_do_evento(c, eid)
        assert lucia and lucia == _ficha_do_evento(c, e2)            # a mesma ficha nas duas
        assert pedro and pedro != lucia                              # o filho tem a dele
        f = cpa.ficha(c, CLINICA, pedro, AGORA)
    assert f["nome"] == "Pedro Ferreira" and [o["nome"] for o in f["mesmo_card"]] == ["Lúcia Ferreira"]
    assert len(f["agenda"]) == 1 and set(f["falta"]) == {"data de nascimento", "CPF", "cidade"}


def test_a_lista_filtra_e_mostra_o_novo_contato(banco, zap):  # noqa: F811
    with banco.connection() as c:
        lead, _conv = _paciente(c)
        _sessao(c, lead, date(2026, 9, 28), tipo="Consulta")
        _paciente(c, nome="Rita Souza", fone="+5599977770000")        # escreveu e não marcou
        c.commit()
        d = cpa.listar(c, CLINICA, AGORA)
        nomes = {p["nome"]: p for p in d["pacientes"]}
        assert nomes["Rita Souza"]["tipo"] == "contato" and nomes["Lúcia Ferreira"]["proximo"]
        assert d["contagem"]["novos"] == 1 and d["contagem"]["com_horario"] == 1
        assert [p["nome"] for p in cpa.listar(c, CLINICA, AGORA, filtro="novos")["pacientes"]] == ["Rita Souza"]
        assert [p["nome"] for p in cpa.listar(c, CLINICA, AGORA, busca="luc")["pacientes"]] == ["Lúcia Ferreira"]
        assert cpa.listar(c, CLINICA, AGORA, busca="0000")["pacientes"][0]["nome"] == "Rita Souza"


def test_plano_aceito_marca_em_tratamento(banco, zap):  # noqa: F811
    with banco.connection() as c:
        lead, _conv = _paciente(c)
        _sessao(c, lead, date(2026, 9, 28), tipo="Consulta")
        c.commit()
    _plano_aceito(banco, lead)
    with banco.connection() as c:
        d = cpa.listar(c, CLINICA, AGORA, filtro="em_tratamento")
    assert [p["nome"] for p in d["pacientes"]] == ["Lúcia Ferreira"]


def test_cadastro_valida_cpf_e_guarda_o_responsavel(banco, zap):  # noqa: F811
    with banco.connection() as c:
        lead, _conv = _paciente(c)
        e1 = _sessao(c, lead, date(2026, 9, 28), tipo="Consulta")
        e2, _ = ca.agendar(c, CLINICA, profissional_id=_manoel(c), servico_id=_tipo(c, "Consulta")["id"],
                           inicio=ca.utc(date(2026, 9, 30), time(9)), lead_id=lead, paciente="Pedro Ferreira",
                           agora=AGORA)
        c.commit()
        lucia, pedro = _ficha_do_evento(c, e1), _ficha_do_evento(c, e2)
    base = {"nome": "Pedro Ferreira", "fone": FONE, "nascimento": "2017-03-10", "cidade": "Pedreiras", "uf": "ma"}
    assert cpa.salvar_cadastro(banco, CLINICA, pedro, {**base, "cpf": "111.111.111-11"}) == "CPF inválido."
    assert cpa.salvar_cadastro(banco, CLINICA, pedro, {**base, "cpf": "529.982.247-25",
                                                       "responsavel_id": str(lucia),
                                                       "como_conheceu": "Instagram"}) is None
    with banco.connection() as c:
        f = cpa.ficha(c, CLINICA, pedro, AGORA)
    assert f["idade"] == 9 and f["uf"] == "MA" and f["cpf"] == "52998224725" and f["falta"] == []
    assert f["responsavel"]["nome"] == "Lúcia Ferreira" and f["como_conheceu"] == "Instagram"


@pytest.fixture()
def cli(banco, monkeypatch):  # noqa: F811
    from web import painel_clinica_agenda as pa
    from web import painel_clinica_pacientes as pw
    monkeypatch.setattr(pw, "get_pool", lambda: banco)
    monkeypatch.setattr(pa, "get_pool", lambda: banco)
    monkeypatch.setattr(pa, "conta_logada", lambda request: (CLINICA,))
    monkeypatch.setattr(pa, "nicho_da_conta", lambda conta: "clinica")
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste")
    app.include_router(pw.router)
    app.include_router(pa.router)

    @app.get("/_papel/{papel}")
    def _papel(request: Request, papel: str):
        request.session["papel"] = papel
        request.session["membro_id"] = 51
        return {}
    return TestClient(app, follow_redirects=False)


def test_a_recepcao_ve_a_lista_cadastra_e_abre_a_ficha(cli, banco):  # noqa: F811
    cli.get("/_papel/vendedor")
    r = cli.post("/painel/clinica/pacientes/novo", data={"nome": "Helena Batista", "fone": "(99) 98888-7777"})
    assert r.status_code == 303 and "aviso=criado" in r.headers["location"]
    ficha = r.headers["location"].split("?")[0]
    html = cli.get("/painel/clinica/pacientes").text
    assert "Helena Batista" in html and "Novos contatos" in html
    html = cli.get(ficha + "?aba=cadastro").text
    assert "Data de nascimento" in html and "Ficha incompleta" in html


def test_a_recepcao_passa_no_gate_das_telas_da_clinica():
    """O gate do app deixava a recepção só na agenda: Planos, Pacotes, Assinaturas,
    Produtos (até o "Vender" do agendamento) e Pacientes voltavam pra trás. O que é de
    quem manda (Configurar, Números) a própria rota barra pelo papel."""
    from contas import equipe as eq
    rotas = eq.rotas_do_papel("vendedor")

    def passa(r):
        return any(r == a or r.startswith(a + "/") for a in rotas)
    for r in ("/painel/clinica/pacientes", "/painel/clinica/planos/novo", "/painel/clinica/pacotes",
              "/painel/clinica/vagas", "/painel/clinica/assinaturas", "/painel/clinica/produtos/vender",
              "/painel/clinica/agenda/novo"):
        assert passa(r), r
    from web import painel_clinica
    import inspect
    assert '("dono", "gestor")' in inspect.getsource(painel_clinica._acesso)


def test_irmas_com_o_mesmo_primeiro_nome_tem_fichas_separadas(banco, zap):  # noqa: F811
    with banco.connection() as c:
        lead, _conv = _paciente(c, nome="Carla Souza", fone="+5599966660000")
        a = cpa.achar_ou_criar(c, CLINICA, lead, "Ana Clara Souza", "+5599966660000")
        b = cpa.achar_ou_criar(c, CLINICA, lead, "Ana Beatriz Souza", "+5599966660000")
        assert a != b
        assert cpa.achar_ou_criar(c, CLINICA, lead, "ana clara souza", "(99) 96666-0000") == a   # sem acento/caixa
        assert cpa.achar_ou_criar(c, CLINICA, None, "Ana", "99 96666-0000") == a                 # uma palavra só
        cel = c.execute("select p.celular from clientes k join pessoas p on p.id = k.pessoa_id where k.id=%s",
                        (a,)).fetchone()[0]
        c.rollback()
    assert cel == "5599966660000"                                   # só dígitos, como o resto do Zaq


def test_plano_aceito_e_venda_caem_na_ficha_do_paciente_sem_duplicar(banco, zap):  # noqa: F811
    with banco.connection() as c:
        lead, _conv = _paciente(c)
        _sessao(c, lead, date(2026, 9, 28), tipo="Consulta")
        c.commit()
    _plano_aceito(banco, lead)
    with banco.connection() as c:
        n = c.execute("select count(*) from clientes where dono_id=%s", (CLINICA,)).fetchone()[0]
    assert n == 1                                                  # o plano achou a ficha da Lúcia


def test_marcar_pela_ficha_e_cpf_repetido_e_fornecedor(banco, zap):  # noqa: F811
    with banco.connection() as c:
        lead, _conv = _paciente(c)
        e1 = _sessao(c, lead, date(2026, 9, 28), tipo="Consulta")
        c.commit()
        lucia = _ficha_do_evento(c, e1)
        pedro = cpa.achar_ou_criar(c, CLINICA, lead, "Pedro Ferreira", FONE)
        e2, erro = ca.agendar(c, CLINICA, profissional_id=_manoel(c), servico_id=_tipo(c, "Consulta")["id"],
                              inicio=ca.utc(date(2026, 9, 30), time(9)), lead_id=lead, paciente="Pedro Ferreira",
                              agora=AGORA, cliente_id=pedro)
        assert erro is None and _ficha_do_evento(c, e2) == pedro
        forn = c.execute("""insert into clientes (dono_id, nome, eh_cliente, eh_fornecedor)
                            values (%s,'Distribuidora X',false,true) returning id""", (CLINICA,)).fetchone()[0]
        c.commit()
        assert cpa.ficha(c, CLINICA, forn, AGORA) is None          # fornecedor não abre como paciente
    base = {"nome": "Lúcia Ferreira", "fone": FONE}
    assert cpa.salvar_cadastro(banco, CLINICA, lucia, {**base, "cpf": "529.982.247-25"}) is None
    msg = cpa.salvar_cadastro(banco, CLINICA, pedro, {"nome": "Pedro Ferreira", "fone": FONE, "cpf": "52998224725"})
    assert msg == "Este CPF já está na ficha de Lúcia Ferreira."
    assert cpa.salvar_cadastro(banco, CLINICA, forn, {"nome": "X"}) == "Paciente não encontrado."


def test_a_busca_nao_vai_na_url_e_agendar_pela_ficha_leva_o_paciente(cli, banco):  # noqa: F811
    cli.get("/_papel/vendedor")
    r = cli.post("/painel/clinica/pacientes/novo", data={"nome": "Helena Batista", "fone": "(99) 98888-7777"})
    kid = r.headers["location"].split("?")[0].rsplit("/", 1)[1]
    r = cli.post("/painel/clinica/pacientes/buscar", data={"q": "helena", "f": "todos"})
    assert r.status_code == 303 and "helena" not in r.headers["location"].lower()   # nome não vai na URL
    html = cli.get(r.headers["location"]).text
    assert "Helena Batista" in html and f"/painel/clinica/agenda/novo?cliente={kid}" in html
    html = cli.get(f"/painel/clinica/agenda/novo?cliente={kid}").text
    assert 'name="cliente_id"' in html and "Helena Batista" in html


def test_horario_cancelado_nao_conta_como_proximo(banco, zap):  # noqa: F811
    with banco.connection() as c:
        lead, _conv = _paciente(c)
        eid = _sessao(c, lead, date(2026, 9, 28), tipo="Consulta")
        c.commit()
        assert cpa.listar(c, CLINICA, AGORA, filtro="com_horario")["total"] == 1
        c.execute("update eventos_agenda set status='cancelado' where id=%s", (eid,))
        c.commit()
        assert cpa.listar(c, CLINICA, AGORA, filtro="com_horario")["total"] == 0
