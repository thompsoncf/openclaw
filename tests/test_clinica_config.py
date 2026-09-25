"""O cadastro da clínica com banco (finance/clinica_config.py) e a tela Configurar
(web/painel_clinica.py). As migrações 348 (tabelas) e 350 (semente da Espaço
Pelle) rodam de verdade sobre um esquema mínimo.
"""
import os
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path

import pytest
from fastapi import FastAPI, Request
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool
from starlette.middleware.sessions import SessionMiddleware

from finance import clinica_config as cc
from web import painel_clinica as pc

BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"
CLINICA, OUTRA = 39, 34

_SQL = """
create table nichos (id bigserial primary key, slug text);
create table contas (id bigserial primary key, tipo text, nome text, nicho_id bigint);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text, ativo boolean default true, whatsapp text, whatsapp_id text);
insert into nichos (id, slug) values (1,'clinica'),(2,'eventos');
insert into contas (id, tipo, nome, nicho_id) values (39,'pj','Clínica',1),(34,'pj','Festa',2);
insert into membros (id, conta_id, nome, papel) values (51,39,'Dono',  'dono'),(60,34,'Outro','dono');
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True,
                           kwargs={"autocommit": True, "prepare_threshold": None})
    dbname = "zaq_clinica_teste"
    with admin.connection() as c:
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        for m in ("071_servicos_catalogo.sql", "148_servico_categoria_foto.sql",
                  "153_servico_icone.sql", "348_clinica_base.sql",
                  "350_clinica_semente_espaco_pelle.sql"):
            c.execute((BASE / m).read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


SEG = date(2026, 9, 28)
AGORA = datetime(2026, 9, 25, 12, tzinfo=timezone.utc)


# ------------------------------------------------------------------ a semente

def test_a_semente_traz_a_espaco_pelle(pool):
    with pool.connection() as c:
        profs = {p["nome"]: p for p in cc.listar_profissionais(c, CLINICA)}
        tipos = {t["nome"]: t for t in cc.listar_tipos(c, CLINICA)}
        locais = cc.listar_locais(c, CLINICA)
        r = cc.resumo(c, CLINICA)
    assert list(profs) == ["Dr. Manoel", "Juliana", "Brenda", "Cristiane"]
    assert len(tipos) == 14 and tipos["Consulta"]["preco"] == "R$ 500"
    assert tipos["Consulta"]["agente_diz_preco"] and tipos["Consulta"]["volta_dias"] == 30
    assert tipos["Procedimento estético"]["preco"] == "sob consulta"
    assert locais[0]["nome"] == "Espaço Pelle" and locais[0]["endereco"] == "Rua Óscar Galvão, 38"
    assert len(locais) == 10
    assert {tipos["Consulta"]["id"], tipos["Retorno"]["id"]} <= set(profs["Dr. Manoel"]["tipos"])
    # o que falta confirmar aparece
    assert set(r["sem_grade"]) == {"Juliana", "Brenda", "Cristiane"}
    assert set(r["sem_tipo"]) == {"Brenda", "Cristiane"}


def test_a_semente_nao_repete_e_nao_toca_outra_conta(pool):
    with pool.connection() as c:
        c.execute((BASE / "350_clinica_semente_espaco_pelle.sql").read_text(encoding="utf-8"))
        c.commit()
        assert len(cc.listar_profissionais(c, CLINICA)) == 4
        assert cc.listar_profissionais(c, OUTRA) == [] and cc.listar_tipos(c, OUTRA) == []


def test_livres_do_dr_manoel_para_consulta(pool):
    with pool.connection() as c:
        manoel = cc.listar_profissionais(c, CLINICA)[0]
        consulta = next(t for t in cc.listar_tipos(c, CLINICA) if t["nome"] == "Consulta")
        vacina = next(t for t in cc.listar_tipos(c, CLINICA) if t["nome"] == "Vacina")
        livres = cc.livres(c, CLINICA, manoel["id"], consulta["id"], SEG, dias=1, agora=AGORA)
        assert (livres[0]["inicio"] - timedelta(hours=3)).strftime("%H:%M") == "08:00"
        assert len(livres) == 8 + 6
        # quem não faz o atendimento não tem horário pra ele
        assert cc.livres(c, CLINICA, manoel["id"], vacina["id"], SEG, dias=1, agora=AGORA) == []
        # congresso na segunda: some a segunda inteira
        assert cc.salvar_bloqueio(c, CLINICA, profissional_id=manoel["id"], de=SEG, ate=None,
                                  motivo="congresso") is None
        assert cc.livres(c, CLINICA, manoel["id"], consulta["id"], SEG, dias=1, agora=AGORA) == []


# ------------------------------------------------------------------ validações e escopo

def test_validacoes_do_cadastro(pool):
    with pool.connection() as c:
        manoel = cc.listar_profissionais(c, CLINICA)[0]
        sede = cc.listar_locais(c, CLINICA)[0]
        assert cc.salvar_profissional(c, CLINICA, nome="") is not None
        assert "equipe" in cc.salvar_profissional(c, CLINICA, nome="Ana", acesso="propria")
        assert cc.salvar_tipo(c, CLINICA, nome="Peeling", duracao_min=2) is not None
        assert cc.salvar_tipo(c, CLINICA, nome="Peeling", categoria="xpto") is not None
        # cruza com a faixa das 08:00–12:00 do Dr. Manoel
        erro = cc.salvar_grade(c, CLINICA, profissional_id=manoel["id"], local_id=sede["id"],
                               dias=[1], inicio="11:00", fim="13:00")
        assert "cruza" in erro
        assert "semana do mês" in cc.salvar_grade(
            c, CLINICA, profissional_id=manoel["id"], local_id=sede["id"], dias=[4],
            inicio="08:00", fim="17:00", repete="mensal")
        assert cc.salvar_grade(
            c, CLINICA, profissional_id=manoel["id"], local_id=sede["id"], dias=[4],
            inicio="08:00", fim="17:00", repete="mensal", semana_do_mes=3) is None


def test_nada_cruza_de_conta(pool):
    with pool.connection() as c:
        manoel = cc.listar_profissionais(c, CLINICA)[0]
        sede = cc.listar_locais(c, CLINICA)[0]
        consulta = cc.listar_tipos(c, CLINICA)[0]
        # a festa não usa o profissional nem o local da clínica
        assert cc.salvar_grade(c, OUTRA, profissional_id=manoel["id"], local_id=sede["id"],
                               dias=[1], inicio="08:00", fim="09:00") is not None
        # nem liga membro de outra conta, nem atendimento de outra conta
        assert cc.salvar_profissional(c, CLINICA, nome="X", acesso="propria", membro_id=60) is not None
        assert cc.salvar_profissional(c, OUTRA, nome="X", tipos=[consulta["id"]]) is not None
        assert cc.desativar(c, OUTRA, "clinica_profissionais", manoel["id"]) is False
        assert cc.desativar_tipo(c, OUTRA, consulta["id"]) is False


def test_editar_profissional_troca_os_atendimentos(pool):
    with pool.connection() as c:
        brenda = cc.listar_profissionais(c, CLINICA)[2]
        vacina = next(t for t in cc.listar_tipos(c, CLINICA) if t["nome"] == "Vacina")
        assert cc.salvar_profissional(c, CLINICA, id=brenda["id"], nome="Brenda",
                                      funcao="Enfermagem", tipos=[vacina["id"]]) is None
        brenda = cc.listar_profissionais(c, CLINICA)[2]
        assert brenda["funcao"] == "Enfermagem" and brenda["tipos"] == [vacina["id"]]


def test_preco_zero_nunca_vira_preco_que_o_agente_diz(pool):
    with pool.connection() as c:
        assert cc.salvar_tipo(c, CLINICA, nome="Peeling", duracao_min=45, categoria="procedimento",
                              preco_centavos=0, agente_diz_preco=True) is None
        peeling = next(t for t in cc.listar_tipos(c, CLINICA) if t["nome"] == "Peeling")
        assert peeling["agente_diz_preco"] is False and peeling["preco"] == "sob consulta"


# ------------------------------------------------------------------ a tela

@pytest.fixture()
def cli(pool, monkeypatch):
    estado = {"conta": CLINICA, "nicho": "clinica"}
    monkeypatch.setattr(pc, "get_pool", lambda: pool)
    monkeypatch.setattr(pc, "conta_logada", lambda request: (estado["conta"],))
    monkeypatch.setattr(pc, "nicho_da_conta", lambda conta: estado["nicho"])
    app = FastAPI()
    app.add_middleware(SessionMiddleware, secret_key="teste")
    app.include_router(pc.router)

    @app.get("/_papel/{papel}")
    def _papel(request: Request, papel: str):
        request.session["papel"] = papel
        return {}

    c = TestClient(app, follow_redirects=False)
    c.get("/_papel/dono")
    c.estado = estado
    return c


@pytest.mark.parametrize("aba", ["prof", "tipos", "locais", "grade"])
def test_todas_as_abas_abrem(cli, aba):
    r = cli.get(f"/painel/clinica/configurar?aba={aba}")
    assert r.status_code == 200, r.text[:500]
    assert "Configurar clínica" in r.text


def test_so_clinica_e_so_dono_ou_gestor(cli):
    cli.estado["nicho"] = "eventos"
    assert cli.get("/painel/clinica/configurar").headers["location"] == "/painel"
    cli.estado["nicho"] = "clinica"
    cli.get("/_papel/vendedor")
    assert cli.get("/painel/clinica/configurar").headers["location"] == "/painel"
    assert cli.post("/painel/clinica/local", data={"nome": "X"}).headers["location"] == "/painel"
    cli.get("/_papel/gestor")
    assert cli.get("/painel/clinica/configurar").status_code == 200


def test_grade_mostra_os_horarios_livres(cli):
    html = cli.get("/painel/clinica/configurar?aba=grade").text
    assert "Próximos horários livres de" in html and "08:00–12:00" in html


def test_salvar_e_erro_pela_sessao(cli, pool):
    r = cli.post("/painel/clinica/local", data={"nome": "Codó 2", "tipo": "viagem"})
    assert r.headers["location"] == "/painel/clinica/configurar?aba=locais&aviso=salvo"
    r = cli.post("/painel/clinica/tipo", data={"nome": "", "duracao_min": "30"})
    assert r.headers["location"] == "/painel/clinica/configurar?aba=tipos"
    assert "Informe o nome" in cli.get(r.headers["location"]).text
    assert "Informe o nome" not in cli.get("/painel/clinica/configurar?aba=tipos").text


def test_nome_com_html_sai_escapado(cli):
    cli.post("/painel/clinica/profissional", data={"nome": "<script>alert(1)</script>"})
    html = cli.get("/painel/clinica/configurar?aba=prof").text
    assert "<script>alert(1)" not in html and "&lt;script&gt;alert(1)" in html


def test_nao_ha_festa_na_tela(cli):
    for aba in ("prof", "tipos", "locais", "grade"):
        html = cli.get(f"/painel/clinica/configurar?aba={aba}").text
        miolo = html.split('class="cl-pag"', 1)[1].lower()
        for palavra in ("festa", "convidado", "orçamento"):
            assert palavra not in miolo, (aba, palavra)


# ------------------------------------------------------------------ revisão do #848

def test_tirar_local_ou_profissional_derruba_a_grade(pool):
    with pool.connection() as c:
        manoel = cc.listar_profissionais(c, CLINICA)[0]
        consulta = next(t for t in cc.listar_tipos(c, CLINICA) if t["nome"] == "Consulta")
        sede = cc.listar_locais(c, CLINICA)[0]
        assert cc.livres(c, CLINICA, manoel["id"], consulta["id"], SEG, dias=1, agora=AGORA)
        assert cc.desativar(c, CLINICA, "clinica_locais", sede["id"])
        assert cc.listar_grade(c, CLINICA) == []
        assert cc.livres(c, CLINICA, manoel["id"], consulta["id"], SEG, dias=1, agora=AGORA) == []


def test_atendimento_tirado_nao_conta_mais(pool):
    with pool.connection() as c:
        juliana = cc.listar_profissionais(c, CLINICA)[1]
        for t in juliana["tipos"]:
            cc.desativar_tipo(c, CLINICA, t)
        assert "Juliana" in cc.resumo(c, CLINICA)["sem_tipo"]


def test_categoria_antiga_nao_vira_consulta(pool):
    with pool.connection() as c:
        c.execute("""insert into servicos_catalogo (conta_id, slug, nome, categoria)
                     values (%s, 'buffet-velho', 'Linha antiga', 'Buffet')""", (CLINICA,))
        velho = next(t for t in cc.listar_tipos(c, CLINICA) if t["slug"] == "buffet-velho")
        assert cc.salvar_tipo(c, CLINICA, id=velho["id"], nome="Linha antiga", duracao_min=30,
                              categoria="Buffet") is None
        assert next(t for t in cc.listar_tipos(c, CLINICA) if t["slug"] == "buffet-velho")["categoria"] == "Buffet"
        # e uma categoria inventada numa linha nova continua recusada
        assert cc.salvar_tipo(c, CLINICA, nome="Novo", categoria="Buffet") is not None


def test_bloqueio_de_quem_saiu_nao_vira_a_clinica_toda(cli, pool):
    with pool.connection() as c:
        brenda = cc.listar_profissionais(c, CLINICA)[2]
        cc.salvar_bloqueio(c, CLINICA, profissional_id=brenda["id"], de=date(2030, 1, 2), ate=None,
                           motivo="férias")
        cc.desativar(c, CLINICA, "clinica_profissionais", brenda["id"])
        c.commit()
    html = cli.get("/painel/clinica/configurar?aba=grade").text
    assert "Brenda (fora da agenda)" in html


def test_o_agente_respeita_o_preco_escondido(pool):
    from finance import agente
    with pool.connection() as c:
        escondidos = agente._precos_escondidos(c, CLINICA)
    # a consulta pode (semente), o resto da clínica não
    assert "consulta" not in escondidos and "procedimento-estetico" in escondidos
    linha = agente._linha_catalogo({"nome": "Procedimento estético", "slug": "procedimento-estetico",
                                    "setup_centavos": 150000}, True)
    assert "sob consulta" in linha and "1.500" not in linha
    # sem a trava, o de sempre (as outras contas não mudam)
    assert "R$" in agente._linha_catalogo({"nome": "X", "slug": "x", "setup_centavos": 150000})
