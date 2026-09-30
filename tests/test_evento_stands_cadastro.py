"""O cadastro do cliente do stand (finance/evento_stands.py, migração 451).

Aprovado na maquete (30/09/2026): quem reserva vira cliente na hora; o gestor
completa os dados que o CONTRATO pede; salvar cria/atualiza o cliente na aba
Clientes (sem duplicar) e leva os dados pro contrato — que recebe também o
OBJETO (o stand) e o período do evento.

Banco de TESTE separado (ver tests/conftest.py).
"""
import os
from datetime import date
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import clientes as cli
from finance import evento_stands as es

CNPJ_VALIDO = "11.444.777/0001-61"

# o mesmo baseline de test_cliente_nao_duplica (init_schema não cria `clientes`)
_MIGRACOES = ("064_clientes_lojista.sql", "066_pessoas_identidade.sql",
              "131_pessoa_cnpj.sql", "182_clientes_papel.sql")


@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4,
                       open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    with p.connection() as c:
        # orcamentos ANTES de tudo: 075 (prospeccao) já nasce com FK pra ela.
        c.execute("""create table if not exists orcamentos (
            id bigserial primary key, conta_id bigint, cliente text, empresa text,
            segmento text, status text default 'enviado',
            setup_centavos bigint default 0, mensal_centavos bigint default 0,
            primeiro_ano_centavos bigint default 0, n_modulos int default 0,
            evento_agenda_id bigint, criado_por text,
            criado_em timestamptz default now(), atualizado_em timestamptz default now())""")
        c.execute((base / "075_modulo_prospeccao.sql").read_text(encoding="utf-8"))
        c.execute((base / "053_modulo_pj.sql").read_text(encoding="utf-8"))
        c.execute((base / "162_titulo_parcela_do_orcamento.sql").read_text(encoding="utf-8"))
        c.execute((base / "147_orcamento_evento.sql").read_text(encoding="utf-8"))
        c.execute((base / "161_orcamento_sinal.sql").read_text(encoding="utf-8"))
        # em produção `criado_por` é texto; o banco de teste é COMPARTILHADO e a
        # tabela pode ter nascido bigint em outro módulo. Alinha pra este módulo e
        # DEVOLVE o tipo original no fim (abaixo), pra não afetar os outros.
        tipo_original = c.execute(
            "select data_type from information_schema.columns "
            "where table_name='orcamentos' and column_name='criado_por'").fetchone()[0]
        c.execute("alter table orcamentos alter column criado_por type text "
                  "using criado_por::text")
        for m in _MIGRACOES:
            c.execute((base / m).read_text(encoding="utf-8"))
        c.execute((base / "448_evento_stands.sql").read_text(encoding="utf-8"))
        c.execute((base / "451_evento_stands_cadastro_cliente.sql").read_text(encoding="utf-8"))
        c.commit()
    cli._garantir_cols(p)
    yield p
    if tipo_original != "text":
        with p.connection() as c:
            c.execute("truncate table orcamentos restart identity cascade")
            c.execute(f"alter table orcamentos alter column criado_por type "
                      f"{tipo_original} using criado_por::{tipo_original}")
            c.commit()
    p.close()


@pytest.fixture(autouse=True)
def _isola(pool):
    with pool.connection() as c:
        c.execute("truncate table evento_stands, evento_stands_config, orcamentos, "
                  "titulos, clientes, pessoas restart identity cascade")
        c.commit()
    yield


@pytest.fixture()
def conta_id(pool):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome) values ('pj','Outlet Chic') "
                        "returning id").fetchone()[0]
        c.commit()
    return cid


def _criar_stand(pool, conta_id, codigo, **kw):
    with pool.connection() as c:
        c.execute(
            """insert into evento_stands (conta_id, codigo, pavilhao, zona, tamanho,
                   preco_centavos, status)
               values (%s,%s,%s,%s,%s,%s,%s)""",
            (conta_id, codigo, kw.get("pavilhao", "inferior"), kw.get("zona", "Outlet Grifes"),
             kw.get("tamanho", "4x3"), kw.get("preco_centavos", 420000),
             kw.get("status", "livre")))
        c.commit()


def _venda(pool, conta_id, codigo="G60", nome="Boutique Nova Era", zap="86988887777",
           status="pre_reservado"):
    """Um stand com a venda já registrada: prospecção + orçamento + vínculos."""
    with pool.connection() as c:
        pid = c.execute(
            "insert into prospeccao (conta_id, empresa, whatsapp, status, origem) "
            "values (%s,%s,%s,'novo','pagina_stands') returning id",
            (conta_id, nome, zap)).fetchone()[0]
        c.commit()
    _criar_stand(pool, conta_id, codigo, status=status)
    with pool.connection() as c:
        oid = c.execute(
            "insert into orcamentos (conta_id, cliente, empresa, status) "
            "values (%s,%s,%s,'aprovada') returning id", (conta_id, nome, nome)).fetchone()[0]
        c.execute("update evento_stands set prospeccao_id=%s, orcamento_id=%s "
                  "where conta_id=%s and codigo=%s", (pid, oid, conta_id, codigo))
        c.commit()
    return oid


def _dados(**kw):
    base = {"fantasia": "Boutique Nova Era", "whats": "(86) 9 8888-7777",
            "razao": "Boutique Nova Era Comércio Ltda", "doc": CNPJ_VALIDO,
            "rep": "Ana Paula Souza", "email": "contato@boutique.com.br",
            "end": "Av. Frei Serafim, 1200 — Centro", "cep": "64001-020",
            "cidade": "Teresina", "uf": "PI", "obs": ""}
    base.update(kw)
    return base


# ---------------------------------------------------------------- puras

def test_o_cnpj_dos_testes_e_valido():
    from finance import validadoc
    assert validadoc.valida_cnpj(CNPJ_VALIDO)


def test_descricao_do_objeto_e_periodo_do_evento():
    stand = {"codigo": "G60", "tamanho": "4x3", "zona": "Outlet Grifes",
             "pavilhao": "inferior"}
    assert es.descricao_objeto(stand) == "Stand G60 — 4x3m — Outlet Grifes (Pavilhão Inferior)"
    assert es.descricao_objeto({"codigo": "C155", "tamanho": "tenda", "zona": None,
                                "pavilhao": "outlet_car"}) == \
        "Stand C155 — Espaço em tenda (Outlet Car)"
    assert es._periodo(date(2026, 11, 13), date(2026, 11, 15)) == "13 a 15/11/2026"
    assert es._periodo(date(2026, 11, 30), date(2026, 12, 2)) == "30/11 a 02/12/2026"
    assert es._periodo(date(2026, 11, 13), None) == "13/11/2026"


def test_o_contrato_recebe_o_objeto_o_periodo_e_o_representante():
    from finance import contrato as ctr
    ctx = ctr.contexto(orcamento={
        "empresa": "Boutique Nova Era Comércio Ltda", "representante": "Ana Paula Souza",
        "objeto": "Stand G60 — 4x3m — Outlet Grifes (Pavilhão Inferior)",
        "setup_centavos": 420000,
        "evento": {"data": "2026-11-13", "periodo": "13 a 15/11/2026"}})
    texto, faltas = ctr.preencher(
        "O estande {objeto.descricao} no evento de {evento.periodo}, por {valor.total}, "
        "representado por {cliente.representante}.", ctx)
    assert texto == ("O estande Stand G60 — 4x3m — Outlet Grifes (Pavilhão Inferior) no evento "
                     "de 13 a 15/11/2026, por R$ 4.200,00, representado por Ana Paula Souza.")
    assert faltas == []
    # sem período gravado, cai na data única de sempre (quem já usa {evento.data} não muda)
    assert ctr.contexto(orcamento={"evento": {"data": "2026-11-13"}})["evento"]["periodo"] \
        == "13/11/2026"
    # e os campos aparecem na paleta que o dono vê
    campos = {c["campo"] for c in ctr.campos_disponiveis()}
    assert {"objeto.descricao", "evento.periodo", "cliente.representante"} <= campos


# ---------------------------------------------------- a reserva vira cliente

def test_a_reserva_ja_cria_o_cliente_e_o_objeto(pool, conta_id, monkeypatch):
    from finance import contrato as ctr
    from finance import vendas
    monkeypatch.setattr(vendas, "modo_do_orcamento", lambda p, c: "evento")
    monkeypatch.setattr(ctr, "criar_para_orcamento",
                        lambda *a, **k: {"id": 1, "token": "tok-contrato"})
    with pool.connection() as c:
        c.execute("""insert into evento_stands_config
                       (conta_id, slug, evento_inicio, evento_fim, evento_local, edicao_label)
                     values (%s,'outlet-chic','2026-11-13','2026-11-15',
                             'Centro de Convenções','32ª edição')""", (conta_id,))
        pid = c.execute(
            "insert into prospeccao (conta_id, empresa, whatsapp, status, origem) "
            "values (%s,'Boutique Nova Era','86988887777','novo','pagina_stands') "
            "returning id", (conta_id,)).fetchone()[0]
        c.commit()
    _criar_stand(pool, conta_id, "G60", status="pre_reservado")
    with pool.connection() as c:
        c.execute("update evento_stands set prospeccao_id=%s where conta_id=%s "
                  "and codigo='G60'", (pid, conta_id))
        c.commit()
    r = es.garantir_orcamento_e_contrato(pool, conta_id, es.buscar(pool, conta_id, "G60"))
    assert r["contrato_token"] == "tok-contrato"
    stand = es.buscar(pool, conta_id, "G60")
    # o CLIENTE nasceu com o que a página coletou, e o stand e a proposta apontam pra ele
    assert stand["cliente_id"]
    with pool.connection() as c:
        nome, tel = c.execute("select nome, telefone from clientes where id=%s",
                              (stand["cliente_id"],)).fetchone()
        oc, itens, evento = c.execute(
            "select cliente_id, itens, evento from orcamentos where id=%s",
            (stand["orcamento_id"],)).fetchone()
    assert nome == "Boutique Nova Era" and tel.endswith("988887777")
    assert oc == stand["cliente_id"]
    # o OBJETO e o EVENTO que vão pro contrato
    assert itens[0]["nome"] == "Stand G60 — 4x3m — Outlet Grifes (Pavilhão Inferior)"
    assert evento["periodo"] == "13 a 15/11/2026" and evento["data"] == "2026-11-13"
    assert evento["local"] == "Centro de Convenções"
    assert "32ª edição" in evento["tipo"]


# ------------------------------------------------ salvar o cadastro completo

def test_salvar_o_cadastro_cria_o_cliente_e_leva_pro_orcamento(pool, conta_id):
    oid = _venda(pool, conta_id)
    r = es.salvar_cadastro_stand(pool, conta_id, "G60", _dados())
    assert r["ok"] and r["faltam"] == [] and r["congelado"] is False
    stand = es.buscar(pool, conta_id, "G60")
    assert stand["cliente_id"] == r["cliente_id"]
    c = cli.obter_cliente(pool, conta_id, r["cliente_id"])
    assert c["nome"] == "Boutique Nova Era"
    assert c["razao_social"] == "Boutique Nova Era Comércio Ltda"
    assert c["representante"] == "Ana Paula Souza"
    assert c["cnpj"] == "11444777000161" and c["cidade"] == "Teresina"
    # a proposta (o que o contrato lê) recebeu os dados: razão social no lugar do
    # nome, representante em `socio`, documento formatado
    with pool.connection() as cx:
        emp, cliente, socio, cnpj, end, ocid = cx.execute(
            "select empresa, cliente, socio, cnpj, endereco, cliente_id "
            "from orcamentos where id=%s", (oid,)).fetchone()
    assert emp == "Boutique Nova Era Comércio Ltda"
    assert socio == "Ana Paula Souza" and cliente == "Ana Paula Souza"
    assert cnpj == CNPJ_VALIDO and end.startswith("Av. Frei Serafim")
    assert ocid == r["cliente_id"]


def test_o_mesmo_whatsapp_em_dois_stands_e_um_cliente_so(pool, conta_id):
    _venda(pool, conta_id, "G60", zap="86988887777")
    _venda(pool, conta_id, "G61", zap="86988887777")
    a = es.salvar_cadastro_stand(pool, conta_id, "G60", _dados(doc=""))
    b = es.salvar_cadastro_stand(pool, conta_id, "G61", _dados(doc=""))
    assert a["ok"] and b["ok"] and a["cliente_id"] == b["cliente_id"]
    with pool.connection() as c:
        assert c.execute("select count(*) from clientes").fetchone()[0] == 1


def test_editar_de_novo_atualiza_o_mesmo_cliente(pool, conta_id):
    _venda(pool, conta_id)
    a = es.salvar_cadastro_stand(pool, conta_id, "G60", _dados())
    b = es.salvar_cadastro_stand(pool, conta_id, "G60", _dados(rep="Carlos Lima"))
    assert a["cliente_id"] == b["cliente_id"]
    assert cli.obter_cliente(pool, conta_id, b["cliente_id"])["representante"] == "Carlos Lima"


def test_contrato_assinado_nao_muda_so_o_vinculo(pool, conta_id, monkeypatch):
    from finance import contrato as ctr
    oid = _venda(pool, conta_id)
    monkeypatch.setattr(ctr, "assinado_do_orcamento", lambda *a, **k: True)
    r = es.salvar_cadastro_stand(pool, conta_id, "G60", _dados())
    assert r["ok"] and r["congelado"] is True
    with pool.connection() as c:
        emp, cid = c.execute("select empresa, cliente_id from orcamentos where id=%s",
                             (oid,)).fetchone()
    assert emp == "Boutique Nova Era"          # o nome do orçamento NÃO foi trocado
    assert cid == r["cliente_id"]              # mas o vínculo com o cadastro entrou


def test_cadastro_recusa_documento_ruim_livre_e_sem_nome(pool, conta_id):
    _venda(pool, conta_id)
    assert not es.salvar_cadastro_stand(pool, conta_id, "G60", _dados(doc="123"))["ok"]
    assert not es.salvar_cadastro_stand(pool, conta_id, "G60",
                                        _dados(doc="11.111.111/1111-11"))["ok"]
    assert not es.salvar_cadastro_stand(pool, conta_id, "G60", _dados(fantasia=""))["ok"]
    _criar_stand(pool, conta_id, "S90", status="livre")
    assert not es.salvar_cadastro_stand(pool, conta_id, "S90", _dados())["ok"]
    assert not es.salvar_cadastro_stand(pool, conta_id, "NAO-EXISTE", _dados())["ok"]


def test_documento_de_outro_cliente_avisa_em_vez_de_quebrar(pool, conta_id):
    _venda(pool, conta_id, "G60", zap="86988887777")
    _venda(pool, conta_id, "G61", nome="Outra Loja", zap="86977776666")
    assert es.salvar_cadastro_stand(pool, conta_id, "G60", _dados())["ok"]
    # o mesmo CNPJ num cliente que ainda não é o dono dele: o Zaq junta os dois
    # (é o dedup por documento) em vez de estourar violação de unicidade
    r = es.salvar_cadastro_stand(pool, conta_id, "G61", _dados(fantasia="Outra Loja"))
    assert r["ok"] is True
    with pool.connection() as c:
        assert c.execute("select count(*) from clientes").fetchone()[0] == 1


# ------------------------------------------------------ o que falta pro contrato

def test_o_que_falta_pro_contrato_sai_da_prospeccao_enquanto_nao_ha_cliente(pool, conta_id):
    _venda(pool, conta_id)
    cad = es.cadastros_dos_stands(pool, conta_id, es.listar(pool, conta_id))["G60"]
    assert cad["fantasia"] == "Boutique Nova Era" and cad["n_ok"] == 2
    assert [f["k"] for f in cad["faltam"]] == ["razao", "doc", "rep", "end", "cidade"]
    es.salvar_cadastro_stand(pool, conta_id, "G60", _dados())
    cad = es.cadastros_dos_stands(pool, conta_id, es.listar(pool, conta_id))["G60"]
    assert cad["n_ok"] == 7 and cad["faltam"] == []
    assert cad["doc"] == CNPJ_VALIDO           # o documento volta formatado


def test_cadastros_so_dos_stands_ocupados(pool, conta_id):
    _criar_stand(pool, conta_id, "S90", status="livre")
    assert es.cadastros_dos_stands(pool, conta_id, es.listar(pool, conta_id)) == {}


def test_obter_clientes_isola_por_dono(pool, conta_id):
    _venda(pool, conta_id)
    cid = es.salvar_cadastro_stand(pool, conta_id, "G60", _dados())["cliente_id"]
    with pool.connection() as c:
        outra = c.execute("insert into contas (tipo, nome) values ('pj','Outra') "
                          "returning id").fetchone()[0]
        c.commit()
    assert cid in cli.obter_clientes(pool, conta_id, [cid])
    assert cli.obter_clientes(pool, outra, [cid]) == {}
    assert cli.obter_clientes(pool, conta_id, []) == {}


# ------------------------------------------------ a página pública e o cockpit

class _Req:
    """O mínimo de Request que os guards do cockpit leem."""
    def __init__(self, **sessao):
        self.session = dict(sessao)
        self.cookies = {}
        self.headers = {}


def _cfg(pool, conta_id):
    with pool.connection() as c:
        c.execute("insert into evento_stands_config (conta_id, slug) "
                  "values (%s,'outlet-chic')", (conta_id,))
        c.commit()


def test_a_pagina_publica_exige_nome_fantasia_e_whatsapp(pool, conta_id, monkeypatch):
    from web import loja_stands as ls
    monkeypatch.setattr(ls, "get_pool", lambda: pool)
    _cfg(pool, conta_id)
    _criar_stand(pool, conta_id, "G60")
    for nome, zap in (("", "86988887777"), ("Boutique", ""), ("Boutique", "8698")):
        r = ls._loja_stands_comprovante_sync("outlet-chic", "G60", nome, zap, "",
                                             b"%PDF-1.4 x", "application/pdf")
        assert r.status_code == 303 and "msg=erro_dados" in r.headers["location"]
    # nada travou: o stand segue livre e nenhum interessado nasceu
    assert es.buscar(pool, conta_id, "G60")["status"] == "livre"
    with pool.connection() as c:
        assert c.execute("select count(*) from prospeccao where conta_id=%s",
                         (conta_id,)).fetchone()[0] == 0


def _cockpit(pool, conta_id, monkeypatch, membro, papel="vendedor"):
    from web import painel_cockpit as pc
    monkeypatch.setattr(pc, "get_pool", lambda: pool)
    monkeypatch.setattr(pc, "_ligar_voc", lambda cid: None)
    return pc, _Req(conta_id=conta_id, membro_id=membro, papel=papel)


def _membro(pool, conta_id, nome, papel="vendedor"):
    with pool.connection() as c:
        mid = c.execute(
            "insert into membros (conta_id, nome, email, papel, ativo) "
            "values (%s,%s,%s,%s,true) returning id",
            (conta_id, nome, nome.lower() + "@x.com", papel)).fetchone()[0]
        c.commit()
    return mid


def _dono_da_venda(pool, conta_id, codigo, membro_id):
    with pool.connection() as c:
        c.execute("update prospeccao set vendedor_id=%s where id=(select prospeccao_id "
                  "from evento_stands where conta_id=%s and codigo=%s)",
                  (membro_id, conta_id, codigo))
        c.commit()


def test_o_vendedor_salva_o_cadastro_so_das_vendas_dele(pool, conta_id, monkeypatch):
    import json
    carla, rui = _membro(pool, conta_id, "Carla"), _membro(pool, conta_id, "Rui")
    _venda(pool, conta_id, "G60")
    _dono_da_venda(pool, conta_id, "G60", carla)
    campos = {k: v for k, v in _dados().items() if k != "obs"}

    pc, req_rui = _cockpit(pool, conta_id, monkeypatch, rui)
    r = pc.cockpit_stand_salvar_cliente(req_rui, "G60", **campos)
    assert r.status_code == 403 and json.loads(r.body)["ok"] is False
    with pool.connection() as c:
        assert c.execute("select count(*) from clientes").fetchone()[0] == 0

    pc, req_carla = _cockpit(pool, conta_id, monkeypatch, carla)
    r = pc.cockpit_stand_salvar_cliente(req_carla, "G60", **campos)
    j = json.loads(r.body)
    assert r.status_code == 200 and j["ok"] and j["cad"]["faltam"] == []
    assert cli.obter_cliente(pool, conta_id, j["cad"]["cliente_id"])["representante"] \
        == "Ana Paula Souza"


def test_venda_sem_vendedor_so_a_gestao_edita(pool, conta_id, monkeypatch):
    import json
    carla = _membro(pool, conta_id, "Carla")
    gestor = _membro(pool, conta_id, "Gestora", papel="gestor")
    _venda(pool, conta_id, "G60")                       # link neutro: sem vendedor
    campos = {k: v for k, v in _dados().items() if k != "obs"}
    pc, req = _cockpit(pool, conta_id, monkeypatch, carla)
    assert pc.cockpit_stand_salvar_cliente(req, "G60", **campos).status_code == 403
    pc, req = _cockpit(pool, conta_id, monkeypatch, gestor, papel="gestor")
    assert json.loads(pc.cockpit_stand_salvar_cliente(req, "G60", **campos).body)["ok"]


def test_o_cadastro_completo_so_vai_pro_aparelho_de_quem_pode(pool, conta_id, monkeypatch):
    import json, re
    carla, rui = _membro(pool, conta_id, "Carla"), _membro(pool, conta_id, "Rui")
    _cfg(pool, conta_id)
    _venda(pool, conta_id, "G60")
    _dono_da_venda(pool, conta_id, "G60", carla)
    es.salvar_cadastro_stand(pool, conta_id, "G60", _dados())

    def stands_do(membro):
        pc, req = _cockpit(pool, conta_id, monkeypatch, membro)
        html = pc.cockpit_stands(req).body.decode("utf-8")
        return json.loads(re.search(r"var STANDS=(\{.*?\});var PUB", html, re.S).group(1))

    minha, alheia = stands_do(carla)["G60"], stands_do(rui)["G60"]
    assert minha["pode"] and minha["minha"] and minha["cad"]["doc"]
    assert "cad" not in alheia and "pode" not in alheia
    assert alheia["cliente"] == "Boutique Nova Era"      # o nome todos veem
    assert "11444777000161" not in json.dumps(alheia) and "Frei Serafim" not in json.dumps(alheia)


# ------------------------------------------- o nome da empresa na página pública

def test_o_nome_da_empresa_so_aparece_com_pagamento_confirmado_e_contrato_assinado(
        pool, conta_id):
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    with pool.connection() as c:
        for m in ("164_contratos.sql", "165_contrato_token.sql", "201_contrato_aditivos.sql"):
            c.execute((base / m).read_text(encoding="utf-8"))
        c.commit()
    oid = _venda(pool, conta_id, "G60", status="vendido")
    es.salvar_cadastro_stand(pool, conta_id, "G60", _dados(fantasia="Boutique Nova Era"))
    _venda(pool, conta_id, "G61", nome="Casa Bela", zap="86977776666", status="pre_reservado")

    def nomes():
        return es.expositores_publicos(pool, conta_id, es.listar(pool, conta_id))

    assert nomes() == {}                     # vendido, mas sem contrato assinado
    with pool.connection() as c:
        c.execute("insert into contratos (conta_id, orcamento_id, numero, token, assinado_em) "
                  "values (%s,%s,1,'tk-exp-1', now())", (conta_id, oid))
        c.commit()
    assert nomes() == {"G60": "Boutique Nova Era"}     # o reservado segue anônimo
    with pool.connection() as c:            # não deixa lixo pro banco compartilhado
        c.execute("delete from contratos where conta_id=%s", (conta_id,))
        c.commit()
