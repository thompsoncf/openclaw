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
        c.execute((base / "452_evento_stands_sinal_saldo_grupo.sql").read_text(encoding="utf-8"))
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
        # orcamentos recomeça no id 1: contrato órfão (deste ou de outro módulo) colidiria
        c.execute("do $$ begin if to_regclass('contratos') is not null then "
                  "delete from contratos; end if; end $$")
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
    campos = {c["campo"] for c in ctr.campos_disponiveis(com_estande=True)}
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


# ------------------------------------- até 2 estandes por empresa + sinal e saldo

def _reservar(pool, conta_id, monkeypatch, codigos, sinal=None, zap="86988887777",
              nome="Boutique Nova Era"):
    """Reserva pelo mesmo cano da página pública, sem storage e sem contrato real."""
    from finance import contrato as ctr
    from finance import vendas
    monkeypatch.setattr(vendas, "modo_do_orcamento", lambda p, c: "evento")
    monkeypatch.setattr(ctr, "criar_para_orcamento",
                        lambda *a, **k: {"id": 1, "token": "tok-contrato"})
    with pool.connection() as c:
        pid = c.execute(
            "insert into prospeccao (conta_id, empresa, whatsapp, status, origem) "
            "values (%s,%s,%s,'novo','pagina_stands') returning id",
            (conta_id, nome, zap)).fetchone()[0]
        c.commit()
    return es.subir_e_registrar_comprovante(
        pool, conta_id, codigos[0], b"%PDF-1.4 x", "application/pdf",
        prospeccao_id=pid, subir=lambda *a, **k: None,
        junto_com=codigos[1:], sinal_centavos=sinal)


def _config_evento(pool, conta_id):
    with pool.connection() as c:
        c.execute("""insert into evento_stands_config
                       (conta_id, slug, evento_inicio, evento_fim, evento_local,
                        edicao_label, evento_horario)
                     values (%s,'outlet-chic','2026-11-13','2026-11-15',
                             'Centro de Convenções','32ª edição',
                             '13 e 14/11: 10h às 22h; 15/11: 10h às 16h')""", (conta_id,))
        c.commit()


def test_a_reserva_de_2_estandes_trava_os_dois_num_contrato_so(pool, conta_id, monkeypatch):
    _config_evento(pool, conta_id)
    _criar_stand(pool, conta_id, "G60")
    _criar_stand(pool, conta_id, "G61")
    r = _reservar(pool, conta_id, monkeypatch, ["G60", "G61"], sinal=300000)
    assert r["ok"] and r["contrato_token"] == "tok-contrato"
    a, b = es.buscar(pool, conta_id, "G60"), es.buscar(pool, conta_id, "G61")
    assert a["status"] == b["status"] == "pre_reservado"
    assert a["grupo_id"] and a["grupo_id"] == b["grupo_id"]
    assert a["orcamento_id"] == b["orcamento_id"] and a["cliente_id"] == b["cliente_id"]
    with pool.connection() as c:
        itens, total, parcelas, sinal, evento = c.execute(
            "select itens, setup_centavos, parcelas, sinal_centavos, evento "
            "from orcamentos where id=%s", (a["orcamento_id"],)).fetchone()
    assert [i["nome"][:9] for i in itens] == ["Stand G60", "Stand G61"]
    assert total == 840000 and sinal == 300000            # sem desconto
    assert [p["valor_centavos"] for p in parcelas] == [300000, 540000]
    assert parcelas[1]["venc"] == "2026-11-13"            # saldo até o dia do evento
    assert evento["horario"].startswith("13 e 14/11")
    assert evento["tipo"] == "Outlet Chic — 32ª edição"   # a marca é a do slug, não a da assessoria


def test_reserva_tudo_ou_nada(pool, conta_id, monkeypatch):
    _config_evento(pool, conta_id)
    _criar_stand(pool, conta_id, "G60")
    _criar_stand(pool, conta_id, "G61", status="vendido")
    r = _reservar(pool, conta_id, monkeypatch, ["G60", "G61"], sinal=300000)
    assert not r["ok"]
    assert es.buscar(pool, conta_id, "G60")["status"] == "livre"


def test_regras_da_reserva(pool, conta_id):
    _config_evento(pool, conta_id)
    for cod in ("G60", "G61", "G62"):
        _criar_stand(pool, conta_id, cod)
    v = es.validar_reserva
    assert v(pool, conta_id, ["G60"], "86988887777", 150000)["ok"]
    assert not v(pool, conta_id, ["G60"], "86988887777", 149999)["ok"]        # abaixo do mínimo
    assert not v(pool, conta_id, ["G60", "G61"], "86988887777", 200000)["ok"]  # 1.500 por estande
    assert v(pool, conta_id, ["G60", "G61"], "86988887777", 300000)["ok"]
    assert "máximo" in v(pool, conta_id, ["G60", "G61", "G62"], "86988887777")["erro"]
    assert not v(pool, conta_id, ["G60", "G60"], "86988887777")["ok"]
    assert not v(pool, conta_id, ["G60"], "86988887777", 99999999)["ok"]       # passa do total


def test_a_mesma_empresa_nao_reserva_de_novo_por_outra_reserva(pool, conta_id, monkeypatch):
    _config_evento(pool, conta_id)
    for cod in ("G60", "G61"):
        _criar_stand(pool, conta_id, cod)
    assert _reservar(pool, conta_id, monkeypatch, ["G60"], sinal=150000)["ok"]
    r = es.validar_reserva(pool, conta_id, ["G61"], "(86) 9 8888-7777", 150000)
    assert not r["ok"] and "G60" in r["erro"] and "um contrato só" in r["erro"]
    assert es.validar_reserva(pool, conta_id, ["G61"], "86977776666", 150000)["ok"]


def test_confirmar_o_sinal_baixa_o_sinal_e_deixa_o_saldo_em_aberto(pool, conta_id, monkeypatch):
    _config_evento(pool, conta_id)
    _criar_stand(pool, conta_id, "G60")
    _criar_stand(pool, conta_id, "G61")
    _reservar(pool, conta_id, monkeypatch, ["G60", "G61"], sinal=300000)
    # abaixo do mínimo: recusa e nada muda
    r = es.confirmar_pagamento(pool, conta_id, "G60", sinal_centavos=200000)
    assert not r["ok"] and es.buscar(pool, conta_id, "G61")["status"] == "pre_reservado"
    # o gestor conferiu R$ 3.500 no comprovante (mais que o informado)
    r = es.confirmar_pagamento(pool, conta_id, "G61", sinal_centavos=350000)
    assert r["ok"] and set(r["codigos"]) == {"G60", "G61"}
    assert es.buscar(pool, conta_id, "G60")["status"] == "vendido"
    oid = es.buscar(pool, conta_id, "G60")["orcamento_id"]
    with pool.connection() as c:
        tits = c.execute("select valor_centavos, status, vencimento from titulos "
                         "where orcamento_id=%s order by parcela_idx", (oid,)).fetchall()
    assert [(t[0], t[1]) for t in tits] == [(350000, "pago"), (490000, "aberto")]
    assert str(tits[1][2]) == "2026-11-13"
    sit = es.situacao_financeira(pool, conta_id, [oid])[oid]
    assert sit["pago"] == 350000 and sit["aberto"] == 490000
    # confirmar de novo não duplica
    assert es.confirmar_pagamento(pool, conta_id, "G60")["ja_confirmado"]


def test_saldo_em_mais_de_uma_vez_ate_quitar(pool, conta_id, monkeypatch):
    _config_evento(pool, conta_id)
    _criar_stand(pool, conta_id, "G60")
    _reservar(pool, conta_id, monkeypatch, ["G60"], sinal=150000)
    es.confirmar_pagamento(pool, conta_id, "G60")
    oid = es.buscar(pool, conta_id, "G60")["orcamento_id"]
    assert es.situacao_financeira(pool, conta_id, [oid])[oid]["aberto"] == 270000
    r = es.registrar_pagamento_saldo(pool, conta_id, "G60", 100000)
    assert r["ok"] and r["aberto"] == 170000 and not r["quitado"]
    assert not es.registrar_pagamento_saldo(pool, conta_id, "G60", 999999999)["ok"]
    r = es.registrar_pagamento_saldo(pool, conta_id, "G60", 170000)
    assert r["ok"] and r["quitado"]
    sit = es.situacao_financeira(pool, conta_id, [oid])[oid]
    assert sit["pago"] == 420000 and sit["aberto"] == 0
    assert not es.registrar_pagamento_saldo(pool, conta_id, "G60", 100)["ok"]


def test_liberar_um_estande_da_reserva_libera_os_dois(pool, conta_id, monkeypatch):
    _config_evento(pool, conta_id)
    _criar_stand(pool, conta_id, "G60")
    _criar_stand(pool, conta_id, "G61")
    _reservar(pool, conta_id, monkeypatch, ["G60", "G61"], sinal=300000)
    assert es.liberar(pool, conta_id, "G61")
    for cod in ("G60", "G61"):
        st = es.buscar(pool, conta_id, cod)
        assert st["status"] == "livre" and st["grupo_id"] is None


def test_o_contrato_publico_da_reserva_mostra_espaco_horario_sinal_e_saldo(
        pool, conta_id, monkeypatch):
    """O quadro do contrato do estande: espaço(s) locado(s), horário do evento,
    sinal e saldo com data — e as cláusulas leem os mesmos campos."""
    from finance import contrato as ctr
    from finance import vendas
    from web import contrato_publico as cp
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    with pool.connection() as c:
        for m in ("160_contrato_modelo.sql", "164_contratos.sql", "165_contrato_token.sql",
                  "189_contrato_enviado_em.sql", "194_assinar_antes_do_sinal.sql",
                  "201_contrato_aditivos.sql", "311_contrato_servico.sql"):
            c.execute((base / m).read_text(encoding="utf-8"))
        # o cabeçalho lê a empresa (contas): num banco novo essas colunas vêm de outras migrações
        for col in ("razao_social", "nome_fantasia", "endereco", "bairro", "cep", "cidade",
                    "uf", "telefone", "email_empresa", "logo_url"):
            c.execute(f"alter table contas add column if not exists {col} text")
        c.commit()
    _config_evento(pool, conta_id)
    _criar_stand(pool, conta_id, "G60")
    _criar_stand(pool, conta_id, "G61")
    monkeypatch.setattr(vendas, "modo_do_orcamento", lambda p, c: "evento")
    monkeypatch.setattr(ctr, "conta_tem_contrato", lambda p, c: True)
    monkeypatch.setattr(cp.scat, "listar", lambda *a, **k: [])
    with pool.connection() as c:
        # orcamentos volta ao id 1 a cada teste: contrato órfão de outro módulo colidiria
        c.execute("delete from contratos")
        c.execute("delete from contrato_modelo where conta_id=%s", (conta_id,))
        c.execute(
            "insert into contrato_modelo (conta_id, clausulas) values (%s, %s::jsonb)",
            (conta_id, '[{"titulo":"II","corpo":"Horário: {evento.horario}. Espaços: '
                       '{objeto.descricao}. Sinal {valor.entrada}, saldo {valor.saldo} '
                       'até {valor.saldo_ate}."}]'))
        pid = c.execute(
            "insert into prospeccao (conta_id, empresa, whatsapp, status, origem) "
            "values (%s,'Boutique Nova Era','86988887777','novo','pagina_stands') "
            "returning id", (conta_id,)).fetchone()[0]
        c.commit()
    r = es.subir_e_registrar_comprovante(
        pool, conta_id, "G60", b"%PDF-1.4 x", "application/pdf", prospeccao_id=pid,
        subir=lambda *a, **k: None, junto_com=["G61"], sinal_centavos=300000)
    d = cp.carregar(r["contrato_token"], pool=pool)
    assert d["espaco"] is True and "G60" in d["objeto"] and "G61" in d["objeto"]
    assert d["evento"]["horario"].startswith("13 e 14/11")
    assert d["pagamento"] == {"sinal": "R$ 3.000,00", "saldo": "R$ 5.400,00",
                              "saldo_ate": "13/11/2026"}
    corpo = d["clausulas"][0]["corpo"]
    assert "13 e 14/11: 10h às 22h" in corpo and "Sinal R$ 3.000,00" in corpo
    assert "saldo R$ 5.400,00 até 13/11/2026" in corpo
    with pool.connection() as c:
        c.execute("delete from contratos where conta_id=%s", (conta_id,))
        c.commit()


def test_a_pagina_publica_recusa_sinal_baixo_e_aceita_2_estandes(pool, conta_id, monkeypatch):
    from web import loja_stands as ls
    from finance import comprovantes as comprov
    from finance import contrato as ctr
    from finance import vendas
    monkeypatch.setattr(ls, "get_pool", lambda: pool)
    monkeypatch.setattr(comprov, "subir_em", lambda *a, **k: None)
    monkeypatch.setattr(vendas, "modo_do_orcamento", lambda p, c: "evento")
    monkeypatch.setattr(ctr, "criar_para_orcamento",
                        lambda *a, **k: {"id": 1, "token": "tok-contrato"})
    _config_evento(pool, conta_id)
    _criar_stand(pool, conta_id, "G60")
    _criar_stand(pool, conta_id, "G61")
    pdf = b"%PDF-1.4 x"

    def enviar(sinal, cod2="G61"):
        return ls._loja_stands_comprovante_sync(
            "outlet-chic", "G60", "Boutique Nova Era", "(86) 9 8888-7777", "", pdf,
            "application/pdf", cod2, sinal)

    r = enviar("2.000,00")                        # 1.500 por stand = 3.000 no mínimo
    assert r.status_code == 303 and "msg=erro_sinal" in r.headers["location"]
    assert es.buscar(pool, conta_id, "G60")["status"] == "livre"
    r = enviar("3.000,00")
    assert r.status_code == 303 and "msg=ok" in r.headers["location"]
    assert "c2=G61" in r.headers["location"] and "ct=tok-contrato" in r.headers["location"]
    assert es.buscar(pool, conta_id, "G61")["status"] == "pre_reservado"
    # a mesma empresa não reserva de novo por fora do contrato
    _criar_stand(pool, conta_id, "G62")
    r = ls._loja_stands_comprovante_sync("outlet-chic", "G62", "Boutique Nova Era",
                                         "86988887777", "", pdf, "application/pdf", "",
                                         "1500")
    assert "msg=erro_empresa" in r.headers["location"]


# ------------------------- o app do Outlet Chic é separado do app da Prime

def _aplica_453(pool):
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    with pool.connection() as c:
        c.execute("alter table contas add column if not exists nome_fantasia text")
        c.execute("alter table contas add column if not exists documento text")
        c.execute((base / "453_conta_app_perfil.sql").read_text(encoding="utf-8"))
        c.commit()


def _conta_com_cnpj(pool, nome, cnpj):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome, documento) values ('pj',%s,%s) "
                        "returning id", (nome, cnpj)).fetchone()[0]
        c.commit()
    return cid


def test_so_a_conta_do_cnpj_do_outlet_chic_ganha_o_perfil_de_estandes(pool):
    _aplica_453(pool)
    outlet = _conta_com_cnpj(pool, "M.R. Rocha Aurélio", "30.961.685/0001-01")
    prime = _conta_com_cnpj(pool, "Prime Eventos", "11.222.333/0001-81")
    # a migração roda de novo no deploy seguinte: não muda quem já tem o perfil
    _aplica_453(pool)
    assert es.app_de_stands(pool, outlet) is True
    assert es.app_de_stands(pool, prime) is False
    with pool.connection() as c:
        fant = dict(c.execute("select id, nome_fantasia from contas where id = any(%s)",
                              ([outlet, prime],)).fetchall())
    assert fant[outlet] == "OUTLET CHIC" and fant[prime] is None


def test_abas_e_inicio_do_app_do_vendedor_so_mudam_no_outlet_chic(pool, monkeypatch):
    from web import painel_cockpit as pc
    _aplica_453(pool)
    outlet = _conta_com_cnpj(pool, "Outlet", "30961685000101")
    prime = _conta_com_cnpj(pool, "Prime", "99.888.777/0001-66")
    _aplica_453(pool)                                      # o deploy marca quem já existe
    monkeypatch.setattr(pc, "get_pool", lambda: pool)
    monkeypatch.setattr(pc, "_ligar_voc", lambda cid: None)

    def rotulos(html):
        import re
        return re.findall(r"<span>([^<]+)</span></a>", html)

    assert rotulos(pc._abas_vend("fila", 0, 0, 0, conta_id=outlet)) == \
        ["Stands", "Leads", "Vendas", "Clientes", "Perfil"]
    assert rotulos(pc._abas_vend("fila", 0, 0, 0, conta_id=prime)) == \
        ["Fila", "Agenda", "Propostas", "Raio-X", "Perfil"]
    assert rotulos(pc._abas_vend("fila", 0, 0, 0)) == \
        ["Fila", "Agenda", "Propostas", "Raio-X", "Perfil"]

    # o início: o vendedor do Outlet Chic cai no mapa; o da Prime, na fila de sempre
    monkeypatch.setattr(pc, "_gerencia", lambda r: None)
    chamou = []
    monkeypatch.setattr(pc, "_fila", lambda *a, **k: chamou.append(a[1]) or "FILA")
    monkeypatch.setattr(pc, "_sessao", lambda r: (outlet, 1))
    r = pc.cockpit_inicio(_Req())
    assert r.status_code == 303 and r.headers["location"].endswith("/cockpit/stands")
    assert chamou == []
    monkeypatch.setattr(pc, "_sessao", lambda r: (prime, 1))
    assert pc.cockpit_inicio(_Req()) == "FILA" and chamou == [prime]


def test_os_campos_do_contrato_de_estande_so_aparecem_pra_quem_tem_o_perfil():
    from finance import contrato as ctr
    de_estande = {"objeto.descricao", "evento.horario", "valor.saldo_ate",
                  "cliente.representante", "evento.periodo"}
    normal = {c["campo"] for c in ctr.campos_disponiveis()}
    assert not (de_estande & normal)                       # a paleta da Prime não muda
    assert de_estande <= {c["campo"] for c in ctr.campos_disponiveis(com_estande=True)}


def test_minhas_vendas_mostra_so_as_do_vendedor_agrupadas(pool, conta_id, monkeypatch):
    import re
    carla, rui = _membro(pool, conta_id, "Carla"), _membro(pool, conta_id, "Rui")
    _config_evento(pool, conta_id)
    for cod in ("G60", "G61", "G62"):
        _criar_stand(pool, conta_id, cod)
    _reservar(pool, conta_id, monkeypatch, ["G60", "G61"], sinal=300000)
    _dono_da_venda(pool, conta_id, "G60", carla)
    _reservar(pool, conta_id, monkeypatch, ["G62"], sinal=150000, zap="86977776666",
              nome="Casa Bela")
    _dono_da_venda(pool, conta_id, "G62", rui)
    pc, req = _cockpit(pool, conta_id, monkeypatch, carla)
    html = pc.cockpit_stands_vendas(req).body.decode("utf-8")
    assert html.count("class=cvd") == 1                    # os 2 stands dela = 1 cartão
    assert "G60 + G61" in html and "Boutique Nova Era" in html
    assert "num contrato só" in html and "Aguardando a gestão confirmar o sinal" in html
    assert "Casa Bela" not in html                         # a venda do colega não aparece


def test_leads_do_app_de_estandes_usam_a_fila_numa_rota_propria(pool, monkeypatch):
    from web import painel_cockpit as pc
    _aplica_453(pool)
    outlet = _conta_com_cnpj(pool, "Outlet", "30961685000101")
    prime = _conta_com_cnpj(pool, "Prime", "77.666.555/0001-44")
    _aplica_453(pool)
    monkeypatch.setattr(pc, "get_pool", lambda: pool)
    monkeypatch.setattr(pc, "_ligar_voc", lambda cid: None)
    chamadas = []
    monkeypatch.setattr(pc, "_fila", lambda req, cid, mid, **k: chamadas.append(k) or "FILA")
    monkeypatch.setattr(pc, "_sessao", lambda r: (outlet, 1))
    assert pc.cockpit_leads(_Req()) == "FILA"
    assert chamadas[0]["base"].endswith("/cockpit/leads")      # filtros e busca voltam pra ela
    monkeypatch.setattr(pc, "_sessao", lambda r: (prime, 1))
    r = pc.cockpit_leads(_Req())                               # a Prime não ganha a rota
    assert r.status_code == 303 and r.headers["location"] == "/cockpit"
    assert len(chamadas) == 1


def test_vendas_trazem_o_resumo_e_clientes_agrupa_por_empresa(pool, conta_id, monkeypatch):
    carla = _membro(pool, conta_id, "Carla")
    _config_evento(pool, conta_id)
    for cod in ("G60", "G61", "G62"):
        _criar_stand(pool, conta_id, cod)
    _reservar(pool, conta_id, monkeypatch, ["G60", "G61"], sinal=300000)
    _dono_da_venda(pool, conta_id, "G60", carla)
    es.salvar_cadastro_stand(pool, conta_id, "G60", _dados())
    es.confirmar_pagamento(pool, conta_id, "G60", sinal_centavos=300000)
    pc, req = _cockpit(pool, conta_id, monkeypatch, carla)
    vendas = pc.cockpit_stands_vendas(req).body.decode("utf-8")
    assert "Vendido (sinal confirmado)" in vendas and "R$ 8.400" in vendas   # 2 stands vendidos
    assert "Já recebido" in vendas and "R$ 3.000" in vendas
    assert "Saldo a receber" in vendas and "R$ 5.400" in vendas
    clientes = pc.cockpit_stands_clientes(req).body.decode("utf-8")
    assert clientes.count("class=cvd") == 1                     # 2 stands, 1 empresa
    assert "G60 · G61" in clientes and "Boutique Nova Era" in clientes
    assert "wa.me/5586988887777" in clientes and "Cadastro completo" in clientes


# --------------------- app do Outlet Chic: Leads, página do lead, ações da venda

def test_o_fragmento_da_lista_de_leads_mantem_as_abas_e_os_links_dela(monkeypatch):
    """O tique de 8 s e a busca pedem o fragmento: na lista de Leads do app de
    estandes ele tem que voltar com a base /cockpit/leads (abas e links dela). A
    Fila da Prime não manda `lista` e não paga consulta nenhuma a mais."""
    from web import painel_cockpit as pc
    chamadas, perguntou = [], []
    monkeypatch.setattr(pc, "_sessao", lambda r: (40, 2))
    monkeypatch.setattr(pc, "_gerencia", lambda r: None)
    monkeypatch.setattr(pc, "_perfil_stands", lambda cid: perguntou.append(cid) or True)
    monkeypatch.setattr(pc, "_fila", lambda req, cid, mid, **k: chamadas.append(k) or "OK")
    pc.cockpit_fila_fragmento(_Req(), lista="leads")
    assert chamadas[-1]["base"] == "/cockpit/leads" and chamadas[-1]["fragmento"] is True
    pc.cockpit_fila_fragmento(_Req())
    assert chamadas[-1]["base"] == "" and perguntou == [40]   # sem `lista`, nem pergunta
    # os dois scripts pedem o fragmento com o extra e a busca fica na URL da lista
    assert "window.CKLISTA" in pc._busca_js() and "fq(qs)" in pc._busca_js()
    assert "fq(location.search)" in pc._sinal_js("x")


def test_a_mensagem_do_link_de_stands_leva_o_link_de_vendas_do_vendedor(pool, conta_id, monkeypatch):
    from urllib.parse import unquote
    from web import painel_cockpit as pc
    _config_evento(pool, conta_id)
    monkeypatch.setattr(pc, "get_pool", lambda: pool)
    txt = unquote(pc._link_stands_texto(conta_id, 12))
    assert "/e/outlet-chic?v=" + es.codigo_vendedor(12) in txt
    assert "R$ 1.500" in txt and "por stand" in txt


def test_a_pagina_do_lead_no_app_de_estandes_nao_tem_nada_de_festa(pool, monkeypatch):
    from web import painel_cockpit as pc
    monkeypatch.setattr(pc, "get_pool", lambda: pool)
    for n in ("_bloco_aconteceu", "_bloco_resgate", "_bloco_visita", "_bloco_espera"):
        monkeypatch.setattr(pc, n, (lambda nome: (lambda *a, **k: f"[{nome}]"))(n))
    monkeypatch.setattr(pc, "_link_stands_texto", lambda c, m: "MSG")
    d = {"empresa": "Loja X", "mensagens": [], "evento_fmt": "13/11 EVENTOX",
         "evento_pista": "PISTAX", "etapas": [], "ia": {}}

    def pagina(em_stands):
        monkeypatch.setattr(pc, "_perfil_stands", lambda cid: em_stands)
        req = _Req(conta_id=40, membro_id=2)
        req.query_params = {}
        for _ in range(80):                      # o lead fake ganha as chaves que a tela lê
            try:
                return pc._lead_vendedor(req, 28, d).body.decode("utf-8")
            except KeyError as e:
                d[e.args[0]] = None
        raise AssertionError("a tela do lead não montou")

    stands, prime = pagina(True), pagina(False)
    assert "Link de stands" in stands and "?texto=MSG" in stands
    assert "/orcamento'" not in stands and "[_bloco_visita]" not in stands
    assert "[_bloco_aconteceu]" not in stands and "[_bloco_espera]" not in stands
    assert "EVENTOX" not in stands and "PISTAX" not in stands
    assert "href='/cockpit/leads'" in stands                 # a seta volta pra lista de Leads
    # a Prime continua exatamente como era
    assert "/orcamento'" in prime and "[_bloco_visita]" in prime and "EVENTOX" in prime
    assert "Link de stands" not in prime


def test_a_venda_do_vendedor_traz_contrato_e_saldo_pras_acoes(pool, conta_id, monkeypatch):
    import json
    import re
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    with pool.connection() as c:
        for m in ("164_contratos.sql", "165_contrato_token.sql", "201_contrato_aditivos.sql"):
            c.execute((base / m).read_text(encoding="utf-8"))
        c.commit()
    carla = _membro(pool, conta_id, "Carla")
    _config_evento(pool, conta_id)
    _criar_stand(pool, conta_id, "G60")
    _criar_stand(pool, conta_id, "G61")
    _reservar(pool, conta_id, monkeypatch, ["G60", "G61"], sinal=300000)
    _dono_da_venda(pool, conta_id, "G60", carla)
    es.confirmar_pagamento(pool, conta_id, "G60", sinal_centavos=300000)
    oid = es.buscar(pool, conta_id, "G60")["orcamento_id"]
    with pool.connection() as c:
        c.execute("insert into contratos (conta_id, orcamento_id, numero, token) "
                  "values (%s,%s,1,'tok-venda')", (conta_id, oid))
        c.commit()
    pc, req = _cockpit(pool, conta_id, monkeypatch, carla)
    html = pc.cockpit_stands(req).body.decode("utf-8")
    dados = json.loads(re.search(r"var STANDS=(\{.*?\});var PUB", html, re.S).group(1))
    g60 = dados["G60"]
    assert g60["ct"] == "tok-venda" and g60["ct_ok"] is False
    assert g60["aberto"] == 540000 and g60["pago"] == 300000
    assert g60["gcods"] == ["G60", "G61"]
    assert "var SALDO_ATE=\"13/11\"" in html and "id=stq" in html   # saldo e a busca
    with pool.connection() as c:
        c.execute("delete from contratos where conta_id=%s", (conta_id,))
        c.commit()
