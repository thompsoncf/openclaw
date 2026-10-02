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
        c.execute((base / "457_evento_stands_aviso_vence.sql").read_text(encoding="utf-8"))
        c.execute((base / "461_clientes_vendedor.sql").read_text(encoding="utf-8"))
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


def _limpa(pool):
    with pool.connection() as c:
        c.execute("truncate table evento_stands, evento_stands_config, orcamentos, "
                  "titulos, clientes, pessoas restart identity cascade")
        # orcamentos recomeça no id 1: contrato órfão (deste ou de outro módulo) colidiria
        c.execute("do $$ begin if to_regclass('contratos') is not null then "
                  "delete from contratos; end if; end $$")
        c.commit()


@pytest.fixture(autouse=True)
def _isola(pool):
    _limpa(pool)
    yield
    # e limpa DEPOIS também: a última proposta numerada deste arquivo ficava no banco e
    # batia em uq_orcamentos_conta_numero no módulo seguinte
    _limpa(pool)


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


def test_com_o_agente_atendendo_o_assumir_leva_a_mensagem_do_link(pool, monkeypatch):
    """O "Link de stands" tocado com o agente atendendo: o Assumir leva a mensagem e
    avisa que ela já entra pronta na caixa. Só no app de estandes."""
    from web import painel_cockpit as pc
    monkeypatch.setattr(pc, "get_pool", lambda: pool)
    for n in ("_bloco_aconteceu", "_bloco_resgate", "_bloco_visita", "_bloco_espera"):
        monkeypatch.setattr(pc, n, lambda *a, **k: "")
    monkeypatch.setattr(pc, "_link_stands_texto", lambda c, m: "MSG")
    d = {"empresa": "Loja X", "mensagens": [], "etapas": [], "ia": True}

    def pagina(em_stands):
        monkeypatch.setattr(pc, "_perfil_stands", lambda cid: em_stands)
        req = _Req(conta_id=40, membro_id=2)
        req.query_params = {"texto": "Oi! link de vendas"}
        for _ in range(80):                      # o lead fake ganha as chaves que a tela lê
            try:
                return pc._lead_vendedor(req, 28, d).body.decode("utf-8")
            except KeyError as e:
                d[e.args[0]] = None
        raise AssertionError("a tela do lead não montou")

    stands, prime = pagina(True), pagina(False)
    assert "action='/cockpit/lead/28/assumir?texto=Oi%21+link+de+vendas'" in stands
    assert "<div class=assdica>" in stands and "já entra pronta na caixa" in stands
    assert "action='/cockpit/lead/28/assumir'" in prime and "<div class=assdica>" not in prime


def test_assumir_a_conversa_devolve_a_mensagem_pra_caixa(monkeypatch):
    """Assumida a conversa, a mensagem do "Link de stands" volta no `?texto=` da página
    do lead — que já a põe na caixa. Sem mensagem, ou fora do app de estandes, como era."""
    from urllib.parse import parse_qs, urlsplit
    from web import painel_cockpit as pc
    destinos = []
    monkeypatch.setattr(pc, "_agir", lambda req, lid, fn, destino: destinos.append(destino))
    msg = "Oi! Escolha o seu stand: https://app.zaq-ia.com/e/outlet-chic?v=2-abc"
    monkeypatch.setattr(pc, "_perfil_stands", lambda cid: True)
    pc.cockpit_assumir(_Req(conta_id=40, membro_id=2), 28, texto=msg)
    alvo = urlsplit(destinos[-1])
    assert alvo.path == "/cockpit/lead/28" and parse_qs(alvo.query)["texto"] == [msg]
    pc.cockpit_assumir(_Req(conta_id=40, membro_id=2), 28)
    assert destinos[-1] == "/cockpit/lead/28"
    monkeypatch.setattr(pc, "_perfil_stands", lambda cid: False)    # a Prime: como era
    pc.cockpit_assumir(_Req(conta_id=34, membro_id=5), 28, texto=msg)
    assert destinos[-1] == "/cockpit/lead/28"


def test_o_mapa_do_app_de_estandes_se_atualiza_sozinho(pool, conta_id, monkeypatch):
    """A tela do mapa pede /cockpit/stands/estado (só no app de estandes) e redesenha,
    com o mesmo filtro do que cada um pode ver. Fora do app, nem script nem rota."""
    import json
    carla, rui = _membro(pool, conta_id, "Carla"), _membro(pool, conta_id, "Rui")
    _config_evento(pool, conta_id)
    _criar_stand(pool, conta_id, "G60")
    _criar_stand(pool, conta_id, "G61")
    pc, req = _cockpit(pool, conta_id, monkeypatch, carla)
    monkeypatch.setattr(pc, "_perfil_stands", lambda cid: True)
    assert "BASE_STANDS+'/estado'" in pc.cockpit_stands(req).body.decode("utf-8")
    j = json.loads(pc.cockpit_stands_estado(req).body)
    assert j["ok"] and j["stands"]["G60"]["status"] == "livre"
    assert j["sub"] == "2 livres · 0 reservados · 0 vendidos"

    # um cliente reservou pelo link da Carla: o pedido seguinte já traz
    _reservar(pool, conta_id, monkeypatch, ["G60"], sinal=150000)
    _dono_da_venda(pool, conta_id, "G60", carla)
    es.salvar_cadastro_stand(pool, conta_id, "G60", _dados())
    j = json.loads(pc.cockpit_stands_estado(req).body)
    assert j["stands"]["G60"]["status"] == "reservado" and j["stands"]["G60"]["minha"]
    assert j["stands"]["G60"]["cad"]["doc"] and j["sub"] == "1 livres · 1 reservados · 0 vendidos"
    # o Rui vê o stand reservado, mas não o cadastro do cliente da Carla
    pc, req_rui = _cockpit(pool, conta_id, monkeypatch, rui)
    alheia = json.loads(pc.cockpit_stands_estado(req_rui).body)["stands"]["G60"]
    assert alheia["status"] == "reservado" and "cad" not in alheia and "minha" not in alheia

    # conta sem o app de estandes (a Prime): nem o script, nem a rota
    monkeypatch.setattr(pc, "_perfil_stands", lambda cid: False)
    assert "/estado" not in pc.cockpit_stands(req).body.decode("utf-8")
    assert pc.cockpit_stands_estado(req).status_code == 404


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


# ------------------------- avisos, venda registrada pelo app e app da gestão

def _aplica_454(pool):
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    with pool.connection() as c:
        c.execute((base / "457_evento_stands_aviso_vence.sql").read_text(encoding="utf-8"))
        c.commit()


def _pushes(monkeypatch):
    from finance import cockpit as ck
    enviados = []
    monkeypatch.setattr(ck, "enviar_push",
                        lambda pool, cid, mid, titulo, corpo, url="/cockpit", **k:
                        enviados.append((mid, titulo, corpo, url)) or 1)
    return enviados


def test_o_vendedor_e_avisado_da_reserva_do_sinal_e_do_prazo(pool, conta_id, monkeypatch):
    from datetime import datetime, timedelta, timezone
    _aplica_454(pool)
    carla = _membro(pool, conta_id, "Carla")
    _config_evento(pool, conta_id)
    _criar_stand(pool, conta_id, "G60")
    _criar_stand(pool, conta_id, "G61")
    enviados = _pushes(monkeypatch)
    _reservar(pool, conta_id, monkeypatch, ["G60", "G61"], sinal=300000)
    _dono_da_venda(pool, conta_id, "G60", carla)
    pid = es.buscar(pool, conta_id, "G60")["prospeccao_id"]
    # reserva nova pelo link
    assert es.avisar_reserva_nova(pool, conta_id, pid, ["G60", "G61"], "Boutique", 300000) == 1
    assert enviados[-1][0] == carla and "G60 + G61" in enviados[-1][2]
    # perto de vencer: avisa UMA vez
    agora = datetime.now(timezone.utc)
    with pool.connection() as c:
        c.execute("update evento_stands set pre_reserva_ate=%s where conta_id=%s",
                  (agora + timedelta(hours=5), conta_id))
        c.commit()
    assert es.avisar_reservas_vencendo(pool, agora) == 1          # uma venda, 2 stands
    assert "perto de vencer" in enviados[-1][1]
    assert es.avisar_reservas_vencendo(pool, agora) == 0          # não repete
    # sinal confirmado
    es.confirmar_pagamento(pool, conta_id, "G60", sinal_centavos=300000)
    assert enviados[-1][1] == "Sinal confirmado ✓" and "R$ 5.400,00" in enviados[-1][2]


def test_venda_sem_vendedor_e_reserva_vencida(pool, conta_id, monkeypatch):
    _aplica_454(pool)
    carla = _membro(pool, conta_id, "Carla")
    _config_evento(pool, conta_id)
    _criar_stand(pool, conta_id, "G60")
    enviados = _pushes(monkeypatch)
    _reservar(pool, conta_id, monkeypatch, ["G60"], sinal=150000)   # pelo link neutro
    pid = es.buscar(pool, conta_id, "G60")["prospeccao_id"]
    assert es.avisar_reserva_nova(pool, conta_id, pid, ["G60"], "X", 150000) == 0
    assert enviados == []                                          # ninguém pra avisar
    _dono_da_venda(pool, conta_id, "G60", carla)
    es.avisar_expiradas(pool, [{"conta_id": conta_id, "codigo": "G60", "prospeccao_id": pid}])
    assert enviados[-1][0] == carla and "voltou pro mapa" in enviados[-1][2]


def test_o_vendedor_registra_pelo_app_a_venda_fechada_no_whatsapp(pool, conta_id, monkeypatch):
    from finance import comprovantes as comprov
    from finance import contrato as ctr
    from finance import vendas
    _aplica_454(pool)
    carla = _membro(pool, conta_id, "Carla")
    _config_evento(pool, conta_id)
    _criar_stand(pool, conta_id, "G60")
    _criar_stand(pool, conta_id, "G61")
    _pushes(monkeypatch)
    monkeypatch.setattr(comprov, "subir_em", lambda *a, **k: None)
    monkeypatch.setattr(vendas, "modo_do_orcamento", lambda p, c: "evento")
    monkeypatch.setattr(ctr, "criar_para_orcamento", lambda *a, **k: {"id": 1, "token": "t"})
    pc, req = _cockpit(pool, conta_id, monkeypatch, carla)
    pdf = b"%PDF-1.4 x"
    # sinal abaixo do mínimo pra 2 stands: recusa e nada trava
    r = pc._registrar_venda_sync(req, "G60", "Loja Zap", "(86) 9 9111-2222", "1.500",
                                 "g61", pdf, "application/pdf")
    assert r.status_code == 303 and "mínimo" in req.session["ck_err"]
    assert es.buscar(pool, conta_id, "G60")["status"] == "livre"
    r = pc._registrar_venda_sync(req, "G60", "Loja Zap", "(86) 9 9111-2222", "3.000",
                                 "g61", pdf, "application/pdf")
    assert "G60 + G61" in req.session["ck_ok"]
    a, b = es.buscar(pool, conta_id, "G60"), es.buscar(pool, conta_id, "G61")
    assert a["status"] == b["status"] == "pre_reservado" and a["grupo_id"] == b["grupo_id"]
    with pool.connection() as c:
        vid = c.execute("select vendedor_id from prospeccao where id=%s",
                        (a["prospeccao_id"],)).fetchone()[0]
    assert vid == carla                                            # a venda é dela


def test_a_gestao_do_outlet_chic_abre_no_mapa_e_ve_o_ranking(pool, conta_id, monkeypatch):
    from web import painel_cockpit as pc
    _aplica_454(pool)
    carla = _membro(pool, conta_id, "Carla")
    _config_evento(pool, conta_id)
    for cod in ("G60", "G61", "G62"):
        _criar_stand(pool, conta_id, cod)
    _reservar(pool, conta_id, monkeypatch, ["G60", "G61"], sinal=300000)
    _dono_da_venda(pool, conta_id, "G60", carla)
    es.confirmar_pagamento(pool, conta_id, "G60", sinal_centavos=300000)
    _reservar(pool, conta_id, monkeypatch, ["G62"], sinal=150000, zap="86977776666",
              nome="Sem Vendedor Ltda")
    monkeypatch.setattr(pc, "get_pool", lambda: pool)
    monkeypatch.setattr(pc, "_ligar_voc", lambda cid: None)
    monkeypatch.setattr(pc, "_perfil_stands", lambda cid: True)
    monkeypatch.setattr(pc, "_gerencia", lambda r: (conta_id, None))
    monkeypatch.setattr(pc, "_sessao", lambda r: None)
    req = _Req(conta_id=conta_id, papel="dono")
    r = pc.cockpit_inicio(req)
    assert r.status_code == 303 and r.headers["location"].endswith("/cockpit/stands")
    html = pc.cockpit_stands_ranking(req).body.decode("utf-8")
    assert html.index("Carla") < html.index("Sem vendedor")        # quem vendeu mais primeiro
    assert "R$ 8.400" in html and "R$ 3.000" in html                # vendido e recebido dela
    assert "Ranking" in html and "Vendas" in html                   # abas da gestão
    vendas = pc.cockpit_stands_vendas(req).body.decode("utf-8")
    assert "Vendedor: <b>Carla</b>" in vendas and "sem vendedor" in vendas


# ------------- o cliente completa os dados no link do contrato (01/10/2026)

def _contrato_de_verdade(pool, conta_id, monkeypatch, vendedor=None, codigos=("G60", "G61")):
    """Uma reserva pelo cano da página pública, com o contrato REAL (link público)."""
    from finance import contrato as ctr
    from finance import vendas
    from web import contrato_publico as cp
    base = Path(__file__).resolve().parent.parent / "db" / "migracoes"
    with pool.connection() as c:
        for m in ("160_contrato_modelo.sql", "164_contratos.sql", "165_contrato_token.sql",
                  "189_contrato_enviado_em.sql", "194_assinar_antes_do_sinal.sql",
                  "201_contrato_aditivos.sql", "311_contrato_servico.sql"):
            c.execute((base / m).read_text(encoding="utf-8"))
        for col in ("razao_social", "nome_fantasia", "endereco", "bairro", "cep", "cidade",
                    "uf", "telefone", "email_empresa", "logo_url"):
            c.execute(f"alter table contas add column if not exists {col} text")
        c.execute("delete from contratos")
        c.execute("delete from contrato_modelo where conta_id=%s", (conta_id,))
        c.execute("insert into contrato_modelo (conta_id, clausulas) values (%s, %s::jsonb)",
                  (conta_id, '[{"titulo":"I","corpo":"Cláusula de teste."}]'))
        pid = c.execute(
            "insert into prospeccao (conta_id, empresa, whatsapp, status, origem, vendedor_id) "
            "values (%s,'Boutique Nova Era','86988887777','novo','pagina_stands',%s) "
            "returning id", (conta_id, vendedor)).fetchone()[0]
        c.commit()
    _config_evento(pool, conta_id)
    for cod in codigos:
        _criar_stand(pool, conta_id, cod)
    monkeypatch.setattr(vendas, "modo_do_orcamento", lambda p, c: "evento")
    monkeypatch.setattr(ctr, "conta_tem_contrato", lambda p, c: True)
    monkeypatch.setattr(cp.scat, "listar", lambda *a, **k: [])
    monkeypatch.setattr(cp, "get_pool", lambda: pool)
    r = es.subir_e_registrar_comprovante(
        pool, conta_id, codigos[0], b"%PDF-1.4 x", "application/pdf", prospeccao_id=pid,
        subir=lambda *a, **k: None, junto_com=list(codigos[1:]), sinal_centavos=300000)
    return r["contrato_token"], pid


def _sem_contratos(pool, conta_id):
    with pool.connection() as c:
        c.execute("delete from contratos where conta_id=%s", (conta_id,))
        c.commit()


def _dados_do_link(**kw):
    base = {"fantasia": "Boutique Nova Era", "whats": "(86) 9 8888-7777",
            "razao": "Boutique Nova Era Comércio Ltda", "doc": CNPJ_VALIDO,
            "rep": "Ana Paula Souza", "email": "", "end": "Av. Frei Serafim, 1200",
            "cep": "64001-020", "cidade": "Teresina", "uf": "PI"}
    base.update(kw)
    return base


def test_o_cliente_completa_os_dados_no_link_do_contrato_e_so_entao_assina(
        pool, conta_id, monkeypatch):
    from finance import contrato as ctr
    from web import contrato_publico as cp
    carla = _membro(pool, conta_id, "Carla")
    enviados = _pushes(monkeypatch)
    monkeypatch.setattr(es, "app_de_stands", lambda p, c: True)
    token, _pid = _contrato_de_verdade(pool, conta_id, monkeypatch, vendedor=carla)
    # a observação da equipe nunca passa pela mão do cliente
    es.salvar_cadastro_stand(pool, conta_id, "G60", {"fantasia": "Boutique Nova Era",
                                                     "whats": "(86) 9 8888-7777",
                                                     "obs": "cliente VIP"})
    req = _Req()
    req.client = None
    d = cp.carregar(token, pool)
    assert d["cadastro"]["codigos"] == ["G60", "G61"] and d["cadastro"]["faltam"]
    assert d["pode_assinar"] is False
    html = cp.contrato_publico(req, token).body.decode("utf-8")
    assert "Complete os dados da sua empresa" in html and f"/contrato/{token}/dados" in html
    assert "✓ Assinar contrato" not in html and "complete no topo da página" in html

    # assinar antes de completar: o SERVIDOR recusa, não só a tela
    cp.contrato_assinar(req, token, nome="Ana Paula Souza", doc="", aceite="on", dia="")
    assert not ctr.por_token(pool, token)["assinado_em"]

    # documento inválido: volta com o erro e com o que foi digitado
    r = cp.contrato_dados(req, token, **_dados_do_link(doc="123"))
    corpo = r.body.decode("utf-8")
    assert r.status_code == 200 and "11 (CPF) ou 14 (CNPJ)" in corpo
    assert "Boutique Nova Era Comércio Ltda" in corpo and enviados == []

    # tudo certo: salva, avisa a vendedora UMA vez e libera a assinatura
    r = cp.contrato_dados(req, token, **_dados_do_link())
    assert r.status_code == 303 and r.headers["location"].endswith("?ok=dados")
    assert [(m, t) for m, t, *_ in enviados] == [(carla, "Cadastro completo ✓")]
    assert "G60 + G61" in enviados[0][2]
    d = cp.carregar(token, pool)
    assert d["cadastro"]["faltam"] == [] and d["pode_assinar"] is True
    assert d["contratante"]["nome"] == "Boutique Nova Era Comércio Ltda"
    assert d["contratante"]["representante"] == "Ana Paula Souza"
    g60, g61 = es.buscar(pool, conta_id, "G60"), es.buscar(pool, conta_id, "G61")
    assert g60["cliente_id"] and g60["cliente_id"] == g61["cliente_id"]
    assert cli.obter_clientes(pool, conta_id, [g60["cliente_id"]])[g60["cliente_id"]]["obs"] \
        == "cliente VIP"
    html = cp.contrato_publico(req, token, ok="dados").body.decode("utf-8")
    assert "Dados salvos." in html and "✓ Assinar contrato" in html
    assert "editar os dados da empresa" in html and "Complete os dados" not in html
    assert "Os dados da sua empresa" in cp.contrato_publico(req, token, editar="1").body.decode()

    # salvar de novo, já completo, não avisa outra vez
    cp.contrato_dados(req, token, **_dados_do_link())
    assert len(enviados) == 1
    _sem_contratos(pool, conta_id)


def test_o_contrato_de_quem_nao_tem_o_perfil_de_estandes_segue_como_era(
        pool, conta_id, monkeypatch):
    from web import contrato_publico as cp
    monkeypatch.setattr(es, "app_de_stands", lambda p, c: False)
    token, _pid = _contrato_de_verdade(pool, conta_id, monkeypatch)
    d = cp.carregar(token, pool)
    assert d["cadastro"] is None and d["pode_assinar"] is True
    req = _Req()
    req.client = None
    html = cp.contrato_publico(req, token).body.decode("utf-8")
    assert "Complete os dados da sua empresa" not in html and "✓ Assinar contrato" in html
    r = cp.contrato_dados(req, token, **_dados_do_link())
    assert r.status_code == 303 and r.headers["location"] == f"/contrato/{token}"
    g60 = es.buscar(pool, conta_id, "G60")
    assert es.cadastros_dos_stands(pool, conta_id, [g60])["G60"]["razao"] == ""
    _sem_contratos(pool, conta_id)


# ------------- a vendedora cadastra o cliente ANTES da reserva (01/10/2026)

def _moda_encanto(**kw):
    base = _dados(fantasia="Moda Encanto", whats="(86) 9 8111-2233",
                  razao="Moda Encanto Confecções Ltda", rep="Juliana Costa")
    base.update(kw)
    return base


def test_a_vendedora_cadastra_antes_e_a_reserva_pelo_link_do_cliente_nasce_completa(
        pool, conta_id, monkeypatch):
    from finance import comprovantes as comprov
    from finance import contrato as ctr
    from finance import vendas
    from web import loja_stands as ls
    carla, rui = _membro(pool, conta_id, "Carla"), _membro(pool, conta_id, "Rui")
    enviados = _pushes(monkeypatch)
    r = es.cadastrar_cliente_do_vendedor(pool, conta_id, carla, _moda_encanto())
    assert r["ok"] and r["acao"] == "criado"
    cid = r["cliente_id"]
    carteira = es.clientes_do_vendedor_sem_stand(pool, conta_id, carla)
    assert [c["cliente_id"] for c in carteira] == [cid] and carteira[0]["faltam"] == []
    assert es.clientes_do_vendedor_sem_stand(pool, conta_id, rui) == []
    # o mesmo lojista não troca de vendedora; e só fantasia + WhatsApp são obrigatórios
    r2 = es.cadastrar_cliente_do_vendedor(pool, conta_id, rui, _moda_encanto())
    assert not r2["ok"] and "outra vendedora" in r2["erro"]
    assert not es.cadastrar_cliente_do_vendedor(pool, conta_id, carla,
                                                {"fantasia": "Loja", "whats": "8699"})["ok"]
    # o código do cliente é assinado: forjado, ou de outra conta, não vale
    codigo = es.codigo_cliente(cid)
    assert es.cliente_do_codigo(pool, conta_id, codigo)["nome"] == "Moda Encanto"
    assert es.cliente_do_codigo(pool, conta_id, f"{cid}-0000000000") is None
    assert es.cliente_do_codigo(pool, conta_id + 999, codigo) is None

    monkeypatch.setattr(ls, "get_pool", lambda: pool)
    monkeypatch.setattr(comprov, "subir_em", lambda *a, **k: None)
    monkeypatch.setattr(vendas, "modo_do_orcamento", lambda p, c: "evento")
    monkeypatch.setattr(ctr, "criar_para_orcamento",
                        lambda *a, **k: {"id": 1, "token": "tok-contrato"})
    _config_evento(pool, conta_id)
    _criar_stand(pool, conta_id, "G60")
    r = ls._loja_stands_comprovante_sync(
        "outlet-chic", "G60", "Moda Encanto", "(86) 9 8111-2233", es.codigo_vendedor(carla),
        b"%PDF-1.4 x", "application/pdf", "", "1500", codigo)
    assert r.status_code == 303 and "msg=ok" in r.headers["location"]
    g60 = es.buscar(pool, conta_id, "G60")
    assert g60["cliente_id"] == cid
    assert es.cadastros_dos_stands(pool, conta_id, [g60])["G60"]["faltam"] == []
    with pool.connection() as c:
        assert c.execute("select count(*) from clientes").fetchone()[0] == 1  # não nasceu outro
        empresa, socio = c.execute(
            "select empresa, to_jsonb(o)->>'socio' from orcamentos o where id=%s",
            (g60["orcamento_id"],)).fetchone()
    assert empresa == "Moda Encanto Confecções Ltda" and socio == "Juliana Costa"
    assert es.clientes_do_vendedor_sem_stand(pool, conta_id, carla) == []  # virou venda
    assert "Cadastro completo: já pode mandar o contrato" in enviados[-1][2]
    r3 = es.cadastrar_cliente_do_vendedor(pool, conta_id, carla, _moda_encanto())
    assert not r3["ok"] and "já tem stand" in r3["erro"]


def test_a_aba_clientes_do_app_tem_novo_cliente_e_o_link_que_leva_o_cadastro(
        pool, conta_id, monkeypatch):
    carla = _membro(pool, conta_id, "Carla")
    _config_evento(pool, conta_id)
    pc, req = _cockpit(pool, conta_id, monkeypatch, carla)
    monkeypatch.setattr(pc, "_perfil_stands", lambda cid: True)
    assert "action='/cockpit/stands/clientes/novo'" in \
        pc.cockpit_stands_cliente_novo(req).body.decode("utf-8")
    r = pc.cockpit_stands_cliente_novo_salvar(req, fantasia="", whats="")
    assert r.status_code == 200 and "Preencha o nome fantasia" in r.body.decode("utf-8")
    r = pc.cockpit_stands_cliente_novo_salvar(
        req, fantasia="Moda Encanto", whats="(86) 9 8111-2233", doc="", razao="", rep="",
        email="", end="", cep="", cidade="", uf="")
    assert r.status_code == 303 and r.headers["location"] == "/cockpit/stands/clientes"
    cid = es.clientes_do_vendedor_sem_stand(pool, conta_id, carla)[0]["cliente_id"]
    html = pc.cockpit_stands_clientes(req).body.decode("utf-8")
    assert "href='/cockpit/stands/clientes/novo'" in html and "Ainda sem stand" in html
    assert "Cadastro 2/7" in html and "Cliente salvo" in html
    assert "wa.me/5586981112233?text=" in html and "Mandar link de vendas" in html
    assert "%26c%3D" + es.codigo_cliente(cid) in html               # o link leva o cadastro
    assert "v%3D" + es.codigo_vendedor(carla) in html                # e a marca da vendedora
    # conta sem o app de estandes: nem o botão, nem a rota
    monkeypatch.setattr(pc, "_perfil_stands", lambda cid: False)
    assert "clientes/novo" not in pc.cockpit_stands_clientes(req).body.decode("utf-8")
    assert pc.cockpit_stands_cliente_novo(req).status_code == 303


def test_a_pagina_publica_abre_com_o_cadastro_do_cliente_do_link(pool, conta_id, monkeypatch):
    from web import loja_stands as ls
    monkeypatch.setattr(ls, "get_pool", lambda: pool)
    carla = _membro(pool, conta_id, "Carla")
    _config_evento(pool, conta_id)
    _criar_stand(pool, conta_id, "G60")
    cid = es.cadastrar_cliente_do_vendedor(
        pool, conta_id, carla, {"fantasia": "Moda </script><b>x",
                                "whats": "(86) 9 8111-2233"})["cliente_id"]

    def pagina(**q):
        req = _Req()
        req.query_params = q
        return ls.loja_stands(req, "outlet-chic").body.decode("utf-8")

    html = pagina(c=es.codigo_cliente(cid), v=es.codigo_vendedor(carla))
    assert 'var CLIENTE_LINK = {"codigo": "' in html and '"vendedora": "Carla"' in html
    # dentro do <script>, o nome digitado não fecha a tag
    assert "Moda \\u003c/script\\u003e\\u003cb\\u003ex" in html and "</script><b>x" not in html
    assert "var CLIENTE_LINK = null" in pagina(c=f"{cid}-0000000000")
    assert "var CLIENTE_LINK = null" in pagina()


def test_o_app_manda_o_contrato_avisando_que_o_cliente_completa_os_dados():
    from web import painel_cockpit as pc
    assert "complete os dados da empresa e assine pelo celular" in pc._STANDS_JS
    assert "o cliente completa esses dados no próprio link" in pc._STANDS_JS


# ------- reserva nova nasce limpa; reserva da lista, sem prazo nem comprovante (01/10/2026)

def test_reserva_nova_depois_de_liberar_nasce_com_proposta_propria(pool, conta_id, monkeypatch):
    """O stand liberado (ou vencido) ainda aponta pra proposta de quem estava lá. A
    reserva SEGUINTE não pode herdar esse contrato — foi o que aconteceu no G56 em
    produção ("Maria store" com a proposta do "THOMPSON TESTE")."""
    _config_evento(pool, conta_id)
    for cod in ("G57", "G58", "G59"):
        _criar_stand(pool, conta_id, cod)
    _reservar(pool, conta_id, monkeypatch, ["G57"], sinal=150000, nome="CAMPANHA TESTE 2",
              zap="86999250575")
    antes = es.buscar(pool, conta_id, "G57")
    assert antes["orcamento_id"] and antes["cliente_id"]
    # reenvio do comprovante na MESMA reserva: continua a mesma proposta
    es.registrar_comprovante(pool, conta_id, "G57", "stands/x/outro.pdf",
                             prospeccao_id=antes["prospeccao_id"])
    assert es.buscar(pool, conta_id, "G57")["orcamento_id"] == antes["orcamento_id"]
    assert es.liberar(pool, conta_id, "G57")
    _reservar(pool, conta_id, monkeypatch, ["G57"], sinal=150000, nome="MOOD FOR MAN",
              zap="86988880000")
    depois = es.buscar(pool, conta_id, "G57")
    assert depois["orcamento_id"] != antes["orcamento_id"]
    assert depois["cliente_id"] != antes["cliente_id"]
    with pool.connection() as c:
        assert c.execute("select empresa from orcamentos where id=%s",
                         (depois["orcamento_id"],)).fetchone()[0] == "MOOD FOR MAN"
    # o mesmo vale pra reserva de 2 stands (o cano do grupo)
    _reservar(pool, conta_id, monkeypatch, ["G58", "G59"], sinal=300000, nome="LOJA A",
              zap="86911112222")
    par_antes = es.buscar(pool, conta_id, "G58")["orcamento_id"]
    assert es.liberar(pool, conta_id, "G58")
    _reservar(pool, conta_id, monkeypatch, ["G58", "G59"], sinal=300000, nome="LOJA B",
              zap="86933334444")
    g58, g59 = es.buscar(pool, conta_id, "G58"), es.buscar(pool, conta_id, "G59")
    assert g58["orcamento_id"] == g59["orcamento_id"] != par_antes


def _reserva_da_lista(pool, conta_id, codigo, loja, vendedor=None):
    """Como a lista da gestão entra: reservado, sem comprovante e sem prazo."""
    with pool.connection() as c:
        pid = c.execute(
            "insert into prospeccao (conta_id, empresa, status, origem, vendedor_id) "
            "values (%s,%s,'novo','pagina_stands',%s) returning id",
            (conta_id, loja, vendedor)).fetchone()[0]
        c.execute("update evento_stands set status='pre_reservado', pre_reserva_ate=null, "
                  "comprovante_url=null, prospeccao_id=%s where conta_id=%s and codigo=%s",
                  (pid, conta_id, codigo))
        c.commit()
    return pid


def test_o_funil_do_painel_aceita_reserva_sem_prazo_e_diz_que_veio_da_lista(
        pool, conta_id, monkeypatch):
    from web import painel_eventos_stands as pes
    _config_evento(pool, conta_id)
    for cod in ("G60", "G61", "G62"):
        _criar_stand(pool, conta_id, cod)
    _reservar(pool, conta_id, monkeypatch, ["G60"], sinal=150000)        # página: com prazo
    _reserva_da_lista(pool, conta_id, "G61", "MOOD FOR MAN")             # lista: sem prazo
    _reservar(pool, conta_id, monkeypatch, ["G62"], sinal=150000, nome="Outra Loja",
              zap="86977776666")                                          # página de novo
    monkeypatch.setattr(pes, "get_pool", lambda: pool)
    monkeypatch.setattr(pes, "conta_logada", lambda r: (
        conta_id, "pj", "OUTLET CHIC", "x@x.com", "pj_pro", "ativa", None, "Teresina", False,
        None, False, True, True, False, True, False, "eventos"))
    monkeypatch.setattr(pes, "nicho_da_conta", lambda conta: "eventos")
    req = _Req(papel="dono")
    req.query_params = {}
    r = pes.painel_eventos_stands(req)
    html = r.body.decode("utf-8")
    assert r.status_code == 200                       # antes: TypeError ao ordenar
    assert "Reservado pela lista · aguardando o sinal de R$ 1.500" in html
    assert "Comprovante recebido · sinal de" in html
    # "Precisa de mim": quem vence primeiro vem antes; a da lista (sem prazo) vai pro fim
    import re
    ordem = re.findall(r'data-grupo="precisa_de_mim" data-st="[a-z_]+" data-cod="([a-z0-9]+)"',
                       html)
    assert ordem == ["g60", "g62", "g61"]


def test_o_app_mostra_a_reserva_da_lista_como_aguardando_o_sinal(pool, conta_id, monkeypatch):
    import json
    import re
    roberta = _membro(pool, conta_id, "Roberta")
    _config_evento(pool, conta_id)
    _criar_stand(pool, conta_id, "S113")
    _reserva_da_lista(pool, conta_id, "S113", "SÓ SPORT", vendedor=roberta)
    pc, req = _cockpit(pool, conta_id, monkeypatch, roberta)
    html = pc.cockpit_stands(req).body.decode("utf-8")
    dados = json.loads(re.search(r"var STANDS=(\{.*?\});var PUB", html, re.S).group(1))
    assert dados["S113"]["status"] == "reservado" and dados["S113"]["lista"] is True
    assert "Reservado pela lista — aguardando o sinal do cliente" in pc._STANDS_JS
    vendas = pc.cockpit_stands_vendas(req).body.decode("utf-8")
    assert "Reservado pela lista · aguardando o sinal do cliente." in vendas
    assert "Comprovante recebido" not in vendas


# ------------------------------------- mesma empresa (um CNPJ), dois stands, duas marcas
# 02/10/2026: a vendedora pôs no S97 (OCEAN BEACH) o CNPJ que já estava no i04 (EM
# ESSENCE) — mesma dona, duas lojas — e o sistema respondia "já está cadastrado em
# outro cliente", sem saída.

OUTRO_CNPJ = "11.222.333/0001-81"


def _duas_lojas(pool, conta_id, vendedora=None):
    """i04 com o cadastro completo (EM ESSENCE, com CNPJ) e S97 só com o cadastro
    provisório da reserva (OCEAN BEACH, sem documento) — como a lista do dono deixou."""
    o1 = _venda(pool, conta_id, "i04", nome="EM ESSENCE", zap="86911110001")
    o2 = _venda(pool, conta_id, "S97", nome="OCEAN BEACH", zap="86922220002")
    a = es.salvar_cadastro_stand(pool, conta_id, "i04", _dados(
        fantasia="EM ESSENCE", razao="EM ESSENCE COMERCIO LTDA", whats="86911110001",
        rep="Eduarda Santiago", end="Rua A, 10", email=""))
    b = es.salvar_cadastro_stand(pool, conta_id, "S97", _dados(
        fantasia="OCEAN BEACH", razao="", doc="", whats="86922220002", rep="", end="",
        cep="", cidade="", uf="", email=""))
    assert a["ok"] and b["ok"] and a["cliente_id"] != b["cliente_id"]
    if vendedora:
        _dono_da_venda(pool, conta_id, "i04", vendedora)
        _dono_da_venda(pool, conta_id, "S97", vendedora)
    return a["cliente_id"], b["cliente_id"], o1, o2


def _cads(pool, conta_id, *codigos):
    return es.cadastros_dos_stands(pool, conta_id, [es.buscar(pool, conta_id, c) for c in codigos])


def test_o_mesmo_cnpj_em_outro_stand_entra_no_cadastro_da_empresa(pool, conta_id):
    cass = _membro(pool, conta_id, "Cassandra")
    empresa, provisorio, o1, o2 = _duas_lojas(pool, conta_id, cass)
    r = es.salvar_cadastro_stand(pool, conta_id, "S97", _dados(
        fantasia="OCEAN BEACHWEAR", razao="EM ESSENCE COMERCIO LTDA", whats="86911110001",
        rep="Eduarda Santiago", end="Rua A, 10", email=""),
        juntar="do_vendedor", vendedor_id=cass)
    assert r["ok"] is True, r
    assert r["cliente_id"] == empresa and r["acao"] == "juntado"
    assert r["juntou"] == {"cliente": "EM ESSENCE", "stands": ["i04"], "doc": "CNPJ"}
    assert not r["faltam"]
    # o cadastro da empresa NÃO foi rebatizado; o provisório do S97 saiu da lista
    assert cli.obter_cliente(pool, conta_id, empresa)["nome"] == "EM ESSENCE"
    assert cli.obter_cliente(pool, conta_id, provisorio) is None            # arquivado
    # cada stand com a sua marca, o mesmo CNPJ nos dois
    cads = _cads(pool, conta_id, "i04", "S97")
    assert cads["i04"]["fantasia"] == "EM ESSENCE" and cads["S97"]["fantasia"] == "OCEAN BEACHWEAR"
    assert cads["i04"]["doc"] == cads["S97"]["doc"] == CNPJ_VALIDO
    assert cads["i04"]["dividido_com"] == ["S97"] and cads["S97"]["dividido_com"] == ["i04"]
    # a proposta do S97 (o que o contrato lê) saiu no nome e no CNPJ da empresa
    with pool.connection() as c:
        emp, cnpj, cid = c.execute("select empresa, cnpj, cliente_id from orcamentos where id=%s",
                                   (o2,)).fetchone()
        outro = c.execute("select empresa from orcamentos where id=%s", (o1,)).fetchone()[0]
        marca = c.execute("select empresa from prospeccao where id=(select prospeccao_id from "
                          "evento_stands where conta_id=%s and codigo='S97')", (conta_id,)).fetchone()[0]
    assert emp == "EM ESSENCE COMERCIO LTDA" and cnpj == CNPJ_VALIDO and cid == empresa
    assert outro == "EM ESSENCE COMERCIO LTDA"            # a proposta do i04 não mudou
    assert marca == "OCEAN BEACHWEAR"


def test_salvar_de_novo_no_stand_juntado_nao_rebatiza_a_empresa(pool, conta_id):
    empresa, _prov, _o1, _o2 = _duas_lojas(pool, conta_id)
    base = dict(razao="EM ESSENCE COMERCIO LTDA", whats="86911110001", rep="Eduarda Santiago",
                end="Rua A, 10", email="")
    assert es.salvar_cadastro_stand(pool, conta_id, "S97", _dados(fantasia="OCEAN BEACH", **base))["ok"]
    # edita a empresa pelo S97: o representante vale pros dois; a marca, só pro S97
    r = es.salvar_cadastro_stand(pool, conta_id, "S97", _dados(
        fantasia="OCEAN BEACHWEAR", **dict(base, rep="Matheus Oliveira")))
    assert r["ok"] and r["cliente_id"] == empresa and r["acao"] == "atualizado" and r["juntou"] is None
    cads = _cads(pool, conta_id, "i04", "S97")
    assert cads["i04"]["fantasia"] == "EM ESSENCE" and cads["S97"]["fantasia"] == "OCEAN BEACHWEAR"
    assert cads["i04"]["rep"] == cads["S97"]["rep"] == "Matheus Oliveira"
    # e pelo i04 também: a marca dele muda, a do S97 fica
    assert es.salvar_cadastro_stand(pool, conta_id, "i04", _dados(fantasia="EM ESSENCE PERFUMARIA", **base))["ok"]
    cads = _cads(pool, conta_id, "i04", "S97")
    assert cads["i04"]["fantasia"] == "EM ESSENCE PERFUMARIA" and cads["S97"]["fantasia"] == "OCEAN BEACHWEAR"
    with pool.connection() as c:
        assert c.execute("select count(*) from clientes where ativo").fetchone()[0] == 1


def test_a_vendedora_nao_entra_no_cadastro_da_venda_de_outra(pool, conta_id):
    cass, rob = _membro(pool, conta_id, "Cassandra"), _membro(pool, conta_id, "Roberta")
    empresa, provisorio, _o1, o2 = _duas_lojas(pool, conta_id)
    _dono_da_venda(pool, conta_id, "i04", cass)
    _dono_da_venda(pool, conta_id, "S97", rob)
    r = es.salvar_cadastro_stand(pool, conta_id, "S97", _dados(fantasia="OCEAN BEACH"),
                                 juntar="do_vendedor", vendedor_id=rob)
    assert r["ok"] is False
    assert "EM ESSENCE (i04)" in r["erro"] and "outra vendedora" in r["erro"] and "gestão" in r["erro"]
    # nada mudou: o S97 continua no provisório, sem CNPJ
    assert es.buscar(pool, conta_id, "S97")["cliente_id"] == provisorio
    assert _cads(pool, conta_id, "S97")["S97"]["doc"] == ""
    # a gestão junta
    r = es.salvar_cadastro_stand(pool, conta_id, "S97", _dados(fantasia="OCEAN BEACH"), juntar="sempre")
    assert r["ok"] and r["cliente_id"] == empresa


def test_o_link_publico_do_contrato_nunca_entra_no_cadastro_de_outra_loja(pool, conta_id):
    empresa, provisorio, _o1, o2 = _duas_lojas(pool, conta_id)
    r = es.salvar_cadastro_do_contrato(pool, conta_id, o2, _dados(fantasia="OCEAN BEACH"))
    assert r["ok"] is False and "fale com o seu vendedor" in r["erro"]
    assert "EM ESSENCE" not in r["erro"] and "i04" not in r["erro"]       # não conta de quem é
    assert es.buscar(pool, conta_id, "S97")["cliente_id"] == provisorio
    cad = _cads(pool, conta_id, "S97")["S97"]
    assert cad["doc"] == "" and cad["rep"] == "" and cad["end"] == ""      # nada da outra loja vazou
    assert cli.obter_cliente(pool, conta_id, empresa)["representante"] == "Eduarda Santiago"


def test_trocar_o_cnpj_de_um_dos_stands_separa_as_empresas_de_novo(pool, conta_id):
    empresa, _prov, _o1, o2 = _duas_lojas(pool, conta_id)
    assert es.salvar_cadastro_stand(pool, conta_id, "S97", _dados(fantasia="OCEAN BEACH"))["ok"]
    # era engano: a OCEAN BEACH tem o CNPJ dela
    r = es.salvar_cadastro_stand(pool, conta_id, "S97", _dados(
        fantasia="OCEAN BEACH", razao="OCEAN BEACH MODA PRAIA LTDA", doc=OUTRO_CNPJ,
        whats="86922220002", rep="Ana Lima", email=""))
    assert r["ok"] and r["cliente_id"] != empresa and r["acao"] == "criado"
    cads = _cads(pool, conta_id, "i04", "S97")
    # o i04 fica com o CNPJ e com a razão que a empresa sempre teve (entrar não trocou nada)
    assert cads["i04"]["doc"] == CNPJ_VALIDO and cads["i04"]["razao"] == "EM ESSENCE COMERCIO LTDA"
    assert cads["i04"]["fantasia"] == "EM ESSENCE" and "dividido_com" not in cads["i04"]
    assert cads["S97"]["doc"] == OUTRO_CNPJ and cads["S97"]["razao"] == "OCEAN BEACH MODA PRAIA LTDA"
    assert cads["S97"]["rep"] == "Ana Lima" and "dividido_com" not in cads["S97"]
    assert cli.obter_cliente(pool, conta_id, empresa)["representante"] != "Ana Lima"


def test_o_provisorio_com_vinculo_ou_documento_nao_e_arquivado(pool, conta_id):
    cass = _membro(pool, conta_id, "Cassandra")
    empresa, provisorio, _o1, _o2 = _duas_lojas(pool, conta_id, cass)
    with pool.connection() as c:          # o provisório está na carteira de uma vendedora
        c.execute("update clientes set vendedor_id=%s where id=%s", (cass, provisorio))
        c.commit()
    assert es.salvar_cadastro_stand(pool, conta_id, "S97", _dados(fantasia="OCEAN BEACH"))["ok"]
    assert cli.obter_cliente(pool, conta_id, provisorio) is not None       # ficou


def test_cnpj_de_cliente_de_outra_conta_nao_mexe_na_identidade_dele(pool, conta_id):
    with pool.connection() as c:
        outra = c.execute("insert into contas (tipo, nome) values ('pj','Prime') returning id").fetchone()[0]
        c.commit()
    la = cli.salvar_cliente(pool, outra, "Loja na Prime", telefone="86955550005", cnpj=CNPJ_VALIDO,
                            email="financeiro@loja.com")
    _venda(pool, conta_id, "S97", nome="OCEAN BEACH", zap="86922220002")
    assert es.salvar_cadastro_stand(pool, conta_id, "S97", _dados(
        fantasia="OCEAN BEACH", doc="", razao="", rep="", end="", cep="", cidade="", uf="",
        email="", whats="86922220002"))["ok"]
    r = es.salvar_cadastro_stand(pool, conta_id, "S97", _dados(
        fantasia="OCEAN BEACH", whats="86922220002", email="outro@email.com"))
    assert r["ok"] is True, r
    # o cliente da outra conta continua com o nome, o telefone e o e-mail dele
    dele = cli.obter_cliente(pool, outra, la["id"])
    assert dele["nome"] == "Loja na Prime" and dele["telefone"] == "86955550005"
    assert dele["email"] == "financeiro@loja.com"
    # e o stand ficou com o CNPJ e os dados que o contrato pede
    cad = _cads(pool, conta_id, "S97")["S97"]
    assert cad["doc"] == CNPJ_VALIDO and cad["razao"] == "Boutique Nova Era Comércio Ltda"
    assert cad["fantasia"] == "OCEAN BEACH"        # a marca do stand, não o nome que a outra conta deu
    # o link público do contrato não faz isso
    _venda(pool, conta_id, "G61", nome="Terceira", zap="86933330003")
    assert es.salvar_cadastro_stand(pool, conta_id, "G61", _dados(fantasia="Terceira", doc="", whats="86933330003"))["ok"]
    with pool.connection() as c:
        o3 = c.execute("select orcamento_id from evento_stands where conta_id=%s and codigo='G61'",
                       (conta_id,)).fetchone()[0]
    assert es.salvar_cadastro_do_contrato(pool, conta_id, o3, _dados(fantasia="Terceira", whats="86933330003"))["ok"] is False


def test_o_app_diz_que_juntou_e_o_formulario_avisa_que_e_a_mesma_empresa(pool, conta_id, monkeypatch):
    import json
    cass = _membro(pool, conta_id, "Cassandra")
    _config_evento(pool, conta_id)
    empresa, _prov, _o1, _o2 = _duas_lojas(pool, conta_id, cass)
    pc, req = _cockpit(pool, conta_id, monkeypatch, cass)
    d = _dados(fantasia="OCEAN BEACHWEAR", razao="EM ESSENCE COMERCIO LTDA", whats="86911110001",
               rep="Eduarda Santiago", end="Rua A, 10", email="")
    resp = pc.cockpit_stand_salvar_cliente(
        req, "S97", fantasia=d["fantasia"], whats=d["whats"], razao=d["razao"], doc=d["doc"],
        rep=d["rep"], email=d["email"], end=d["end"], cep=d["cep"], cidade=d["cidade"], uf=d["uf"])
    j = json.loads(resp.body)
    assert j["ok"] is True and j["juntou"] == {"cliente": "EM ESSENCE", "stands": ["i04"], "doc": "CNPJ"}
    assert j["cad"]["fantasia"] == "OCEAN BEACHWEAR" and j["cad"]["dividido_com"] == ["i04"]
    assert "do cadastro '+j.juntou.cliente" in pc._STANDS_JS and "Mesma empresa do stand" in pc._STANDS_JS
    assert "window.stAtualizar()" in pc._STANDS_JS and "window.stAtualizar=function" in pc._STANDS_AUTO_JS
    from web import painel_eventos_stands as painel
    assert "Mesma empresa do stand" in painel._TPL

def test_entrar_num_cadastro_sem_stand_mostra_a_marca_que_a_vendedora_digitou(pool, conta_id):
    # o CNPJ já estava num cadastro da carteira (sem stand nenhum): o stand entra nele
    # e, como não há outra marca em jogo, o nome fantasia é o que ela acabou de digitar
    cass = _membro(pool, conta_id, "Cassandra")
    antes = es.cadastrar_cliente_do_vendedor(pool, conta_id, cass, _dados(fantasia="Nome Antigo", whats="86955556666"))
    assert antes["ok"]
    _venda(pool, conta_id, "S97", nome="OCEAN BEACH", zap="86922220002")
    _dono_da_venda(pool, conta_id, "S97", cass)
    assert es.salvar_cadastro_stand(pool, conta_id, "S97", _dados(fantasia="OCEAN BEACH", doc="", whats="86922220002"))["ok"]
    r = es.salvar_cadastro_stand(pool, conta_id, "S97", _dados(fantasia="OCEAN BEACHWEAR", whats="86955556666"),
                                 juntar="do_vendedor", vendedor_id=cass)
    assert r["ok"] and r["cliente_id"] == antes["cliente_id"] and r["juntou"]["stands"] == []
    cad = _cads(pool, conta_id, "S97")["S97"]
    assert cad["fantasia"] == "OCEAN BEACHWEAR" and "dividido_com" not in cad
    # entrar nunca rebatiza o cadastro (podia ser o de um fornecedor da casa): a marca
    # do stand mora na reserva
    assert cli.obter_cliente(pool, conta_id, antes["cliente_id"])["nome"] == "Nome Antigo"


def test_a_mesma_empresa_tem_no_maximo_2_stands_tambem_quando_junta_pelo_cnpj(pool, conta_id):
    # o dono: "lembrando que a mesma empresa pode ter até 2 stands"
    _config_evento(pool, conta_id)
    empresa, _prov, _o1, _o2 = _duas_lojas(pool, conta_id)
    assert es.salvar_cadastro_stand(pool, conta_id, "S97", _dados(fantasia="OCEAN BEACH"))["ok"]   # 2 de 2
    _venda(pool, conta_id, "G61", nome="TERCEIRA MARCA", zap="86933330003")
    assert es.salvar_cadastro_stand(pool, conta_id, "G61", _dados(
        fantasia="TERCEIRA MARCA", doc="", whats="86933330003", razao="", rep="", end="",
        cep="", cidade="", uf="", email=""))["ok"]
    terceiro = es.buscar(pool, conta_id, "G61")["cliente_id"]
    for quem in ("sempre", "do_vendedor"):
        r = es.salvar_cadastro_stand(pool, conta_id, "G61", _dados(fantasia="TERCEIRA MARCA"),
                                     juntar=quem, vendedor_id=None)
        assert r["ok"] is False, quem
    r = es.salvar_cadastro_stand(pool, conta_id, "G61", _dados(fantasia="TERCEIRA MARCA"), juntar="sempre")
    assert "já tem 2 stands (" in r["erro"] and "i04" in r["erro"] and "S97" in r["erro"]
    assert "máximo é 2 por empresa" in r["erro"]
    assert es.buscar(pool, conta_id, "G61")["cliente_id"] == terceiro        # nada mudou
    assert _cads(pool, conta_id, "G61")["G61"]["doc"] == ""
    assert sorted(_cads(pool, conta_id, "i04")["i04"]["dividido_com"]) == ["S97"]


def test_reserva_de_2_stands_nao_entra_em_empresa_que_ja_tem_1(pool, conta_id, monkeypatch):
    _config_evento(pool, conta_id)
    _venda(pool, conta_id, "i04", nome="EM ESSENCE", zap="86911110001")
    assert es.salvar_cadastro_stand(pool, conta_id, "i04", _dados(fantasia="EM ESSENCE", whats="86911110001"))["ok"]
    for cod in ("G60", "G61"):
        _criar_stand(pool, conta_id, cod)
    _reservar(pool, conta_id, monkeypatch, ["G60", "G61"], zap="86944440004")       # 1 reserva, 2 stands
    r = es.salvar_cadastro_stand(pool, conta_id, "G60", _dados(fantasia="DUPLA", whats="86944440004"))
    assert r["ok"] is False and "já tem 1 stand (i04)" in r["erro"] and "máximo é 2" in r["erro"]


# ---- versão 2 (02/10/2026): o que a verificação independente pegou no #970 (desfeito no #971)

def _so_cnpj(fantasia="OCEAN BEACH"):
    """O formulário do provisório como ele chega: só o nome e o CNPJ, o resto vazio."""
    return _dados(fantasia=fantasia, razao="", rep="", whats="", email="", end="", cep="",
                  cidade="", uf="", obs="")


def test_entrar_na_empresa_so_com_o_cnpj_nao_apaga_nada_dela(pool, conta_id):
    empresa, prov, o1, o2 = _duas_lojas(pool, conta_id)
    cli.atualizar_cliente(pool, conta_id, empresa, email="financeiro@emessence.com", obs="cliente VIP")
    antes = cli.obter_cliente(pool, conta_id, empresa)
    r = es.salvar_cadastro_stand(pool, conta_id, "S97", _so_cnpj())
    assert r["ok"] and r["acao"] == "juntado" and r["cliente_id"] == empresa and not r["faltam"]
    depois = cli.obter_cliente(pool, conta_id, empresa)
    for k in ("nome", "telefone", "email", "razao_social", "representante", "endereco",
              "cep", "cidade", "uf", "obs", "documento"):
        assert depois[k] == antes[k], k
    cads = _cads(pool, conta_id, "i04", "S97")
    assert cads["i04"]["n_ok"] == 7 and cads["S97"]["n_ok"] == 7
    assert cads["S97"]["fantasia"] == "OCEAN BEACH" and cads["i04"]["fantasia"] == "EM ESSENCE"
    with pool.connection() as c:
        emp, socio, zap, cnpj = c.execute(
            "select empresa, socio, whatsapp, cnpj from orcamentos where id=%s", (o2,)).fetchone()
    # o contrato do S97 sai com os dados da empresa
    assert (emp, socio, zap, cnpj) == ("EM ESSENCE COMERCIO LTDA", "Eduarda Santiago",
                                       antes["telefone"], CNPJ_VALIDO)


def test_entrar_so_completa_o_que_a_empresa_nao_tem(pool, conta_id):
    empresa, _prov, _o1, _o2 = _duas_lojas(pool, conta_id)
    cli.atualizar_cliente(pool, conta_id, empresa, email="", cep="")
    r = es.salvar_cadastro_stand(pool, conta_id, "S97", _dados(
        fantasia="OCEAN BEACH", razao="OUTRA RAZAO", rep="Outra Pessoa", whats="86900000000",
        email="novo@ocean.com", end="Rua Nova", cep="64000-999", cidade="Timon", uf="MA"))
    assert r["ok"] and r["acao"] == "juntado"
    d = cli.obter_cliente(pool, conta_id, empresa)
    assert d["razao_social"] == "EM ESSENCE COMERCIO LTDA" and d["representante"] == "Eduarda Santiago"
    assert d["endereco"] == "Rua A, 10" and d["cidade"] == "Teresina"
    assert d["email"] == "novo@ocean.com" and (d["cep"] or "").replace("-", "") == "64000999"


def test_editar_a_empresa_por_um_stand_leva_pro_contrato_do_outro(pool, conta_id, monkeypatch):
    from finance import contrato as ctr
    empresa, _prov, o1, o2 = _duas_lojas(pool, conta_id)
    assert es.salvar_cadastro_stand(pool, conta_id, "S97", _so_cnpj())["ok"]
    r = es.salvar_cadastro_stand(pool, conta_id, "S97", _dados(
        fantasia="OCEAN BEACH", razao="EM ESSENCE NOVA RAZAO LTDA", rep="Matheus Oliveira",
        end="Rua NOVA, 99", whats="", email="", cep="", cidade="", uf=""))
    assert r["ok"] and r["acao"] == "atualizado"
    with pool.connection() as c:
        for oid in (o1, o2):
            emp, socio, endr = c.execute("select empresa, socio, endereco from orcamentos where id=%s",
                                         (oid,)).fetchone()
            assert (emp, socio, endr) == ("EM ESSENCE NOVA RAZAO LTDA", "Matheus Oliveira", "Rua NOVA, 99"), oid
    d = cli.obter_cliente(pool, conta_id, empresa)
    assert d["telefone"] and d["cidade"] == "Teresina"          # vazio no formulário não apagou
    # contrato ASSINADO de um dos stands não muda
    monkeypatch.setattr(ctr, "assinado_do_orcamento", lambda p, conta, oid: oid == o1)
    assert es.salvar_cadastro_stand(pool, conta_id, "S97", _dados(
        fantasia="OCEAN BEACH", razao="RAZAO DEPOIS DA ASSINATURA", rep="", whats="", email="",
        end="", cep="", cidade="", uf=""))["ok"]
    with pool.connection() as c:
        e1 = c.execute("select empresa from orcamentos where id=%s", (o1,)).fetchone()[0]
        e2 = c.execute("select empresa from orcamentos where id=%s", (o2,)).fetchone()[0]
    assert e1 == "EM ESSENCE NOVA RAZAO LTDA" and e2 == "RAZAO DEPOIS DA ASSINATURA"


def test_a_marca_de_cada_stand_fica_depois_de_liberar_ou_separar(pool, conta_id):
    empresa, _prov, _o1, _o2 = _duas_lojas(pool, conta_id)
    assert es.salvar_cadastro_stand(pool, conta_id, "S97", _so_cnpj())["ok"]
    # renomeia a marca do i04 enquanto dividido
    assert es.salvar_cadastro_stand(pool, conta_id, "i04", _dados(
        fantasia="EM ESSENCE PERFUMARIA", razao="", rep="", whats="", email="", end="",
        cep="", cidade="", uf=""))["ok"]
    assert cli.obter_cliente(pool, conta_id, empresa)["nome"] == "EM ESSENCE"     # cadastro não muda
    # separa pelo stand ORIGINAL (o i04 troca de CNPJ)
    r = es.salvar_cadastro_stand(pool, conta_id, "i04", _dados(
        fantasia="EM ESSENCE PERFUMARIA", doc=OUTRO_CNPJ, razao="EM ESSENCE PERFUMARIA LTDA"))
    assert r["ok"] and r["cliente_id"] != empresa
    cads = _cads(pool, conta_id, "i04", "S97")
    assert cads["i04"]["fantasia"] == "EM ESSENCE PERFUMARIA" and cads["S97"]["fantasia"] == "OCEAN BEACH"
    assert "dividido_com" not in cads["i04"] and "dividido_com" not in cads["S97"]
    # e liberar: o que ficou continua com a marca dele
    _venda(pool, conta_id, "G61", nome="MARCA G61", zap="86933330003")
    assert es.salvar_cadastro_stand(pool, conta_id, "G61", _so_cnpj("MARCA G61"))["ok"]   # entra no S97
    assert es.liberar(pool, conta_id, "S97")
    assert _cads(pool, conta_id, "G61")["G61"]["fantasia"] == "MARCA G61"


def test_cliente_de_outra_conta_nao_tem_a_identidade_trocada_nem_lida(pool, conta_id, monkeypatch):
    import json
    with pool.connection() as c:
        outra = c.execute("insert into contas (tipo, nome) values ('pj','Prime') returning id").fetchone()[0]
        c.commit()
    la = cli.salvar_cliente(pool, outra, "Loja na Prime", telefone="86955550005", cnpj=CNPJ_VALIDO,
                            email="financeiro@loja.com")
    cass = _membro(pool, conta_id, "Cassandra")
    _venda(pool, conta_id, "S97", nome="OCEAN BEACH", zap="86922220002")
    _dono_da_venda(pool, conta_id, "S97", cass)
    for i, d in enumerate((
            _dados(fantasia="OCEAN BEACH", whats="86922220002", email="ocean@x.com"),
            _dados(fantasia="OCEAN BEACH 2", whats="86900009999", email="outro@x.com", rep="Fulano"))):
        r = es.salvar_cadastro_stand(pool, conta_id, "S97", d, juntar="do_vendedor", vendedor_id=cass)
        assert r["ok"], (i, r)
        dele = cli.obter_cliente(pool, outra, la["id"])
        assert (dele["nome"], dele["telefone"], dele["email"]) == (
            "Loja na Prime", "86955550005", "financeiro@loja.com"), i
    # o link público também não troca
    oid = es.buscar(pool, conta_id, "S97")["orcamento_id"]
    assert es.salvar_cadastro_do_contrato(pool, conta_id, oid, _dados(
        fantasia="PELO LINK", whats="86911112222", email="link@x.com"))["ok"]
    dele = cli.obter_cliente(pool, outra, la["id"])
    assert (dele["nome"], dele["telefone"], dele["email"]) == ("Loja na Prime", "86955550005",
                                                               "financeiro@loja.com")
    # e a vendedora vê o contato DESTA reserva, não o da outra conta
    pc, req = _cockpit(pool, conta_id, monkeypatch, cass)
    resp = pc.cockpit_stand_salvar_cliente(
        req, "S97", fantasia="OCEAN BEACH", whats="86922220002", razao="X LTDA", doc=CNPJ_VALIDO,
        rep="Fulano", email="ocean@x.com", end="Rua", cep="", cidade="Teresina", uf="PI")
    cad = json.loads(resp.body)["cad"]
    assert cad["whats"] == "86922220002" and cad["email"] == "ocean@x.com"
    assert "86955550005" not in json.dumps(cad) and "financeiro@loja.com" not in json.dumps(cad)


def test_primeiro_cnpj_em_cadastro_dividido_pelo_whatsapp_completa_os_dois(pool, conta_id):
    _venda(pool, conta_id, "G60", zap="86988887777")
    _venda(pool, conta_id, "G61", zap="86988887777")
    a = es.salvar_cadastro_stand(pool, conta_id, "G60", _dados(doc=""))
    b = es.salvar_cadastro_stand(pool, conta_id, "G61", _dados(doc=""))
    assert a["cliente_id"] == b["cliente_id"]
    r = es.salvar_cadastro_stand(pool, conta_id, "G60", _dados())          # o primeiro CNPJ
    assert r["ok"] and r["cliente_id"] == a["cliente_id"] and r["acao"] == "atualizado"
    cads = _cads(pool, conta_id, "G60", "G61")
    assert cads["G60"]["doc"] == cads["G61"]["doc"] == CNPJ_VALIDO


def test_apagar_o_cnpj_nao_tira_o_documento_do_contrato(pool, conta_id):
    oid = _venda(pool, conta_id)
    assert es.salvar_cadastro_stand(pool, conta_id, "G60", _dados())["ok"]
    assert es.salvar_cadastro_stand(pool, conta_id, "G60", _dados(doc=""))["ok"]
    with pool.connection() as c:
        assert c.execute("select cnpj from orcamentos where id=%s", (oid,)).fetchone()[0] == CNPJ_VALIDO
    assert _cads(pool, conta_id, "G60")["G60"]["doc"] == CNPJ_VALIDO


def test_o_provisorio_com_qualquer_vinculo_nao_e_arquivado(pool, conta_id):
    empresa, prov, _o1, _o2 = _duas_lojas(pool, conta_id)
    with pool.connection() as c:     # uma tabela qualquer com cliente_id (como a agenda)
        c.execute("create table if not exists _tmp_compromissos (id serial primary key, "
                  "conta_id bigint, cliente_id bigint)")
        c.execute("insert into _tmp_compromissos (conta_id, cliente_id) values (%s,%s)",
                  (conta_id, prov))
        c.commit()
    try:
        assert es.salvar_cadastro_stand(pool, conta_id, "S97", _so_cnpj())["ok"]
        assert cli.obter_cliente(pool, conta_id, prov) is not None              # ficou
    finally:
        with pool.connection() as c:
            c.execute("drop table if exists _tmp_compromissos")
            c.commit()


def test_cair_no_meio_e_salvar_de_novo_arquiva_o_provisorio(pool, conta_id, monkeypatch):
    empresa, prov, _o1, o2 = _duas_lojas(pool, conta_id)
    real = es._espelhar_no_orcamento

    def cai(*a, **k):
        raise RuntimeError("queda simulada")
    monkeypatch.setattr(es, "_espelhar_no_orcamento", cai)
    with pytest.raises(RuntimeError):
        es.salvar_cadastro_stand(pool, conta_id, "S97", _so_cnpj())
    monkeypatch.setattr(es, "_espelhar_no_orcamento", real)
    # o stand e a proposta já apontam pra empresa e o provisório já saiu da lista
    assert es.buscar(pool, conta_id, "S97")["cliente_id"] == empresa
    assert cli.obter_cliente(pool, conta_id, prov) is None
    r = es.salvar_cadastro_stand(pool, conta_id, "S97", _so_cnpj())
    assert r["ok"] and r["cliente_id"] == empresa
    with pool.connection() as c:
        assert c.execute("select cnpj from orcamentos where id=%s", (o2,)).fetchone()[0] == CNPJ_VALIDO


def test_stand_sem_reserva_registrada_nao_entra_em_outra_empresa(pool, conta_id):
    _duas_lojas(pool, conta_id)
    _criar_stand(pool, conta_id, "S79", status="vendido")
    with pool.connection() as c:
        cid = cli.salvar_cliente(pool, conta_id, "PITCHUKINHA LOJA II")["id"]
        c.execute("update evento_stands set cliente_id=%s where conta_id=%s and codigo='S79'",
                  (cid, conta_id))
        c.commit()
    r = es.salvar_cadastro_stand(pool, conta_id, "S79", _so_cnpj("PITCHUKINHA LOJA II"))
    assert r["ok"] is False and "não tem a reserva registrada" in r["erro"]
    assert es.buscar(pool, conta_id, "S79")["cliente_id"] == cid


def test_pelo_whatsapp_o_link_publico_tambem_nao_entra_em_outra_loja(pool, conta_id):
    empresa, _prov, _o1, _o2 = _duas_lojas(pool, conta_id)
    oid = _venda(pool, conta_id, "G61", nome="SEM CLIENTE", zap="86911110001")   # WhatsApp da EM ESSENCE
    assert es.buscar(pool, conta_id, "G61")["cliente_id"] is None
    r = es.salvar_cadastro_do_contrato(pool, conta_id, oid, {"fantasia": "SEM CLIENTE",
                                                            "whats": "86911110001", "doc": ""})
    assert r["ok"] is False and "fale com o seu vendedor" in r["erro"]
    assert es.buscar(pool, conta_id, "G61")["cliente_id"] is None
    assert cli.obter_cliente(pool, conta_id, empresa)["razao_social"] == "EM ESSENCE COMERCIO LTDA"


def test_o_link_do_cliente_conta_no_limite_da_empresa(pool, conta_id, monkeypatch):
    from finance import comprovantes as comprov
    from finance import contrato as ctr
    from finance import vendas
    from web import loja_stands as ls
    monkeypatch.setattr(ls, "get_pool", lambda: pool)
    monkeypatch.setattr(comprov, "subir_em", lambda *a, **k: None)
    monkeypatch.setattr(vendas, "modo_do_orcamento", lambda p, c: "evento")
    monkeypatch.setattr(ctr, "criar_para_orcamento", lambda *a, **k: {"id": 1, "token": "tok"})
    _config_evento(pool, conta_id)
    empresa, _prov, _o1, _o2 = _duas_lojas(pool, conta_id)
    assert es.salvar_cadastro_stand(pool, conta_id, "S97", _so_cnpj())["ok"]       # 2 stands
    _criar_stand(pool, conta_id, "G61")
    r = ls._loja_stands_comprovante_sync(
        "outlet-chic", "G61", "TERCEIRA MARCA", "86977776666", "", b"%PDF-1.4 x",
        "application/pdf", "", "1500", es.codigo_cliente(empresa))
    assert r.status_code == 303 and "msg=erro_empresa" in r.headers["location"]
    assert es.buscar(pool, conta_id, "G61")["status"] == "livre"


def test_mensagem_do_painel_com_e_comercial_no_nome_chega_inteira(pool, conta_id, monkeypatch):
    from urllib.parse import parse_qs, urlparse
    from web import painel_eventos_stands as painel
    empresa, _prov, _o1, _o2 = _duas_lojas(pool, conta_id)
    cli.atualizar_cliente(pool, conta_id, empresa, nome="DELICATA & CIA")
    monkeypatch.setattr(painel, "get_pool", lambda: pool)
    monkeypatch.setattr(painel, "_acesso", lambda request, *a, **k: ((conta_id,), {}))
    d = _so_cnpj()
    r = painel.salvar_cliente_do_stand(object(), "S97", **{k: d[k] for k in (
        "fantasia", "whats", "razao", "doc", "rep", "email", "end", "cep", "cidade", "uf", "obs")})
    ok = parse_qs(urlparse(r.headers["location"]).query)["ok"][0]
    assert "DELICATA & CIA (mesma empresa do i04)" in ok and "cada stand com a sua marca" in ok


# ---- 2ª verificação independente do #972 (02/10/2026)

def _prime(pool, nome="Loja na Prime"):
    """Uma OUTRA conta com um cliente que tem o CNPJ_VALIDO."""
    with pool.connection() as c:
        outra = c.execute("insert into contas (tipo, nome) values ('pj','Prime') returning id").fetchone()[0]
        c.commit()
    la = cli.salvar_cliente(pool, outra, nome, telefone="86955550005", cnpj=CNPJ_VALIDO,
                            email="financeiro@loja.com")
    return outra, la["id"]


def _pessoa(pool, cnpj=CNPJ_VALIDO):
    with pool.connection() as c:
        return c.execute("select nome, celular, email from pessoas where cnpj=%s",
                         ("".join(ch for ch in cnpj if ch.isdigit()),)).fetchone()


def _editar_em_clientes(pool, conta_id, monkeypatch, cliente_id, nome, estande=True):
    """O formulário da aba Clientes (web/portal.py) com o que o cliente já tem."""
    from types import SimpleNamespace
    from web import portal as pt
    monkeypatch.setattr(pt, "_guard_clientes", lambda request, **k: ((conta_id,), pool))
    monkeypatch.setattr(es, "app_de_stands", lambda p, c: estande)
    d = cli.obter_cliente(pool, conta_id, cliente_id)
    req = SimpleNamespace(session={})
    pt.painel_cliente_editar(
        req, cliente_id, nome=nome, telefone=d["telefone"] or "",
        documento=d.get("documento_fmt") or "", cpf="", email=d["email"] or "",
        aniversario="", obs=d.get("obs") or "", cidade=d["cidade"] or "", uf=d["uf"] or "",
        endereco=d["endereco"] or "", cep=d["cep"] or "", razao_social=d["razao_social"] or "",
        representante=d["representante"] or "", eh_cliente="1", eh_fornecedor="")
    assert req.session.get("aviso") == "Cliente atualizado.", req.session


def test_corrigir_o_nome_em_clientes_vale_pro_stand_e_nao_volta(pool, conta_id, monkeypatch):
    _venda(pool, conta_id, "G60", nome="BOUTIQE NOVA ERA")
    r = es.salvar_cadastro_stand(pool, conta_id, "G60", _dados(fantasia="BOUTIQE NOVA ERA"))
    assert r["ok"]
    _editar_em_clientes(pool, conta_id, monkeypatch, r["cliente_id"], "BOUTIQUE NOVA ERA")
    cad = _cads(pool, conta_id, "G60")["G60"]
    assert cad["fantasia"] == "BOUTIQUE NOVA ERA"
    # a vendedora abre o formulário (com o que a tela mostra) e só muda o CEP
    d = {k: cad[k] for k in ("fantasia", "whats", "razao", "doc", "rep", "email", "end",
                             "cep", "cidade", "uf", "obs")}
    assert es.salvar_cadastro_stand(pool, conta_id, "G60", dict(d, cep="64000-555"))["ok"]
    assert cli.obter_cliente(pool, conta_id, r["cliente_id"])["nome"] == "BOUTIQUE NOVA ERA"
    assert _cads(pool, conta_id, "G60")["G60"]["fantasia"] == "BOUTIQUE NOVA ERA"


def test_corrigir_o_nome_da_empresa_nao_troca_a_marca_do_outro_stand(pool, conta_id, monkeypatch):
    empresa, _prov, _o1, _o2 = _duas_lojas(pool, conta_id)
    assert es.salvar_cadastro_stand(pool, conta_id, "S97", _so_cnpj())["ok"]
    _editar_em_clientes(pool, conta_id, monkeypatch, empresa, "EM ESSENCE MODAS")
    cads = _cads(pool, conta_id, "i04", "S97")
    assert cads["i04"]["fantasia"] == "EM ESSENCE MODAS" and cads["S97"]["fantasia"] == "OCEAN BEACH"


def test_conta_sem_o_app_de_estandes_nao_tem_a_reserva_mexida_pela_aba_clientes(
        pool, conta_id, monkeypatch):
    _venda(pool, conta_id, "G60", nome="BOUTIQE NOVA ERA")
    r = es.salvar_cadastro_stand(pool, conta_id, "G60", _dados(fantasia="BOUTIQE NOVA ERA"))
    _editar_em_clientes(pool, conta_id, monkeypatch, r["cliente_id"], "BOUTIQUE NOVA ERA",
                        estande=False)
    with pool.connection() as c:
        assert c.execute("select empresa from prospeccao where conta_id=%s",
                         (conta_id,)).fetchone()[0] == "BOUTIQE NOVA ERA"


def test_cnpj_errado_de_cliente_de_outra_conta_se_corrige_com_o_certo(pool, conta_id):
    outra, la = _prime(pool)
    o2 = _venda(pool, conta_id, "S97", nome="OCEAN BEACH", zap="86922220002")
    assert es.salvar_cadastro_stand(pool, conta_id, "S97", _dados(
        fantasia="OCEAN BEACH", doc="", whats="86922220002"))["ok"]
    assert es.salvar_cadastro_stand(pool, conta_id, "S97", _dados(        # o engano
        fantasia="OCEAN BEACH", whats="86922220002"))["ok"]
    assert _cads(pool, conta_id, "S97")["S97"]["doc"] == CNPJ_VALIDO
    r = es.salvar_cadastro_stand(pool, conta_id, "S97", _dados(            # o certo
        fantasia="OCEAN BEACH", doc=OUTRO_CNPJ, razao="OCEAN BEACH MODA PRAIA LTDA",
        whats="86922220002"))
    assert r["ok"] and r["acao"] == "criado"
    assert _cads(pool, conta_id, "S97")["S97"]["doc"] == OUTRO_CNPJ
    with pool.connection() as c:
        emp, cnpj = c.execute("select empresa, cnpj from orcamentos where id=%s", (o2,)).fetchone()
    assert (emp, cnpj) == ("OCEAN BEACH MODA PRAIA LTDA", OUTRO_CNPJ)
    dele = cli.obter_cliente(pool, outra, la)
    assert (dele["nome"], dele["telefone"], dele["email"]) == (
        "Loja na Prime", "86955550005", "financeiro@loja.com")


def test_cnpj_de_cadastro_arquivado_so_completa_e_o_link_publico_nao_puxa(pool, conta_id):
    empresa, _prov, _o1, o2 = _duas_lojas(pool, conta_id)
    cli.atualizar_cliente(pool, conta_id, empresa, email="fin@emessence.com")
    antes = _pessoa(pool)
    assert cli.arquivar_cliente(pool, conta_id, empresa)
    # pelo link público, digitar o CNPJ não puxa a empresa arquivada
    r = es.salvar_cadastro_do_contrato(pool, conta_id, o2, _so_cnpj())
    assert r["ok"] is False and "fale com o seu vendedor" in r["erro"]
    # arquivado da carteira de OUTRA vendedora: a vendedora não puxa
    cass, rob = _membro(pool, conta_id, "Cassandra"), _membro(pool, conta_id, "Roberta")
    _dono_da_venda(pool, conta_id, "S97", cass)
    with pool.connection() as c:
        c.execute("update clientes set vendedor_id=%s where id=%s", (rob, empresa))
        c.commit()
    r = es.salvar_cadastro_stand(pool, conta_id, "S97", _so_cnpj(), juntar="do_vendedor",
                                 vendedor_id=cass)
    assert r["ok"] is False and "arquivado" in r["erro"] and "outra vendedora" in r["erro"]
    # sem dono: a vendedora puxa — o stand ganha o cadastro da empresa, e a
    # identidade não muda
    with pool.connection() as c:
        c.execute("update clientes set vendedor_id=null where id=%s", (empresa,))
        c.commit()
    r = es.salvar_cadastro_stand(pool, conta_id, "S97", _so_cnpj(), juntar="do_vendedor",
                                 vendedor_id=cass)
    assert r["ok"] and r["acao"] == "criado", r
    assert _pessoa(pool) == antes == ("EM ESSENCE", "86911110001", "fin@emessence.com")
    assert _cads(pool, conta_id, "S97")["S97"]["fantasia"] == "OCEAN BEACH"


def test_o_link_publico_nao_puxa_cliente_de_outra_conta_pelo_cnpj(pool, conta_id):
    _outra, _la = _prime(pool)
    o2 = _venda(pool, conta_id, "S97", nome="OCEAN BEACH", zap="86922220002")
    assert es.salvar_cadastro_stand(pool, conta_id, "S97", _dados(
        fantasia="OCEAN BEACH", doc="", whats="86922220002"))["ok"]
    r = es.salvar_cadastro_do_contrato(pool, conta_id, o2, _dados(fantasia="OCEAN BEACH",
                                                                 whats="86922220002"))
    assert r["ok"] is False and "fale com o seu vendedor" in r["erro"]
    assert _cads(pool, conta_id, "S97")["S97"]["doc"] == ""


def test_a_reserva_pelo_link_do_cliente_nao_troca_a_identidade_de_outra_conta(pool, conta_id):
    outra, la = _prime(pool)
    cid = cli.puxar_ou_criar_cliente(pool, conta_id, cnpj="".join(
        ch for ch in CNPJ_VALIDO if ch.isdigit()))
    o2 = _venda(pool, conta_id, "S97", nome="OCEAN BEACH", zap="86977776666")
    assert es._vincular_cliente_da_reserva(pool, conta_id, ["S97"], o2, cid,
                                           "OCEAN BEACH", "86977776666") == cid
    dele = cli.obter_cliente(pool, outra, la)
    assert (dele["nome"], dele["telefone"]) == ("Loja na Prime", "86955550005")
    assert es.buscar(pool, conta_id, "S97")["cliente_id"] == cid
    # e o contrato desta reserva leva o contato que o lojista confirmou, não o da Prime
    with pool.connection() as c:
        zap, email = c.execute("select whatsapp, email from orcamentos where id=%s",
                               (o2,)).fetchone()
    assert zap == "86977776666" and not email
    cad = _cads(pool, conta_id, "S97")["S97"]
    assert "86955550005" not in str(cad) and "financeiro@loja.com" not in str(cad)


def test_salvar_recusado_pelo_limite_nao_completa_a_empresa(pool, conta_id, monkeypatch):
    # dois salvares ao mesmo tempo: os dois passam na conferência de antes; a trava
    # do passo 3 recusa o segundo — e ele não pode ter deixado nada na empresa
    _config_evento(pool, conta_id)
    empresa, _prov, _o1, _o2 = _duas_lojas(pool, conta_id)
    cli.atualizar_cliente(pool, conta_id, empresa, email="", cep="")
    assert es.salvar_cadastro_stand(pool, conta_id, "S97", _so_cnpj())["ok"]       # 2 de 2
    _venda(pool, conta_id, "G61", nome="TERCEIRA", zap="86933330003")
    assert es.salvar_cadastro_stand(pool, conta_id, "G61", _dados(
        fantasia="TERCEIRA", doc="", whats="86933330003"))["ok"]
    monkeypatch.setattr(es, "_cabe_na_empresa", lambda *a, **k: None)
    r = es.salvar_cadastro_stand(pool, conta_id, "G61", _dados(
        fantasia="TERCEIRA", whats="", email="terceira@x.com", cep="64000-002"))
    assert r["ok"] is False and "máximo é 2" in r["erro"]
    d = cli.obter_cliente(pool, conta_id, empresa)
    assert not (d["email"] or "").strip() and not (d["cep"] or "").strip()


def test_o_whatsapp_da_loja_nao_vira_o_da_empresa_no_cadastro_dividido(pool, conta_id):
    _empresa, _prov, _o1, o2 = _duas_lojas(pool, conta_id)
    assert es.salvar_cadastro_stand(pool, conta_id, "S97", _so_cnpj())["ok"]
    # o lojista do S97 salva o link do contrato sem mexer em nada (a tela mostra o da empresa)
    assert es.salvar_cadastro_do_contrato(pool, conta_id, o2, {"fantasia": "OCEAN BEACH"})["ok"]
    with pool.connection() as c:
        zap = c.execute("select whatsapp from prospeccao where id=(select prospeccao_id from "
                        "evento_stands where conta_id=%s and codigo='S97')", (conta_id,)).fetchone()[0]
    assert zap == "86922220002"


def test_a_vendedora_entra_em_cadastro_sem_dono_mas_nao_no_de_outra(pool, conta_id):
    cass = _membro(pool, conta_id, "Cassandra")
    casa = cli.salvar_cliente(pool, conta_id, "FORNECEDOR DA CASA", telefone="86900001111",
                              cnpj=OUTRO_CNPJ)["id"]
    cli.atualizar_cliente(pool, conta_id, casa, razao_social="CASA LTDA", representante="Dono")
    _venda(pool, conta_id, "S97", nome="OCEAN BEACH", zap="86922220002")
    _dono_da_venda(pool, conta_id, "S97", cass)
    assert es.salvar_cadastro_stand(pool, conta_id, "S97", _dados(
        fantasia="OCEAN BEACH", doc="", whats="86922220002"))["ok"]
    # cadastro na carteira de OUTRA vendedora: recusa
    rob = _membro(pool, conta_id, "Roberta")
    with pool.connection() as c:
        c.execute("update clientes set vendedor_id=%s where id=%s", (rob, casa))
        c.commit()
    r = es.salvar_cadastro_stand(pool, conta_id, "S97", _so_cnpj() | {"doc": OUTRO_CNPJ},
                                 juntar="do_vendedor", vendedor_id=cass)
    assert r["ok"] is False and "FORNECEDOR DA CASA" in r["erro"] and "outra vendedora" in r["erro"]
    assert _cads(pool, conta_id, "S97")["S97"]["doc"] == ""             # nada mudou
    # sem dono: a vendedora salva (o dono: "vendedora tem que conseguir salvar"),
    # e entrar só completa — a razão social que o cadastro tinha fica
    with pool.connection() as c:
        c.execute("update clientes set vendedor_id=null where id=%s", (casa,))
        c.commit()
    r = es.salvar_cadastro_stand(pool, conta_id, "S97", _so_cnpj() | {"doc": OUTRO_CNPJ},
                                 juntar="do_vendedor", vendedor_id=cass)
    assert r["ok"] and r["cliente_id"] == casa, r
    assert cli.obter_cliente(pool, conta_id, casa)["razao_social"] == "CASA LTDA"


def test_a_vendedora_entra_na_empresa_com_stand_da_lista_sem_vendedora(pool, conta_id):
    # o i04 veio da lista do dono, sem vendedora; o S97 é venda da Cassandra
    cass = _membro(pool, conta_id, "Cassandra")
    empresa, _prov, _o1, _o2 = _duas_lojas(pool, conta_id)
    _dono_da_venda(pool, conta_id, "S97", cass)
    r = es.salvar_cadastro_stand(pool, conta_id, "S97", _so_cnpj(), juntar="do_vendedor",
                                 vendedor_id=cass)
    assert r["ok"] and r["cliente_id"] == empresa and r["acao"] == "juntado", r


def test_separar_pelo_stand_que_dava_nome_rebatiza_o_cadastro_que_ficou(pool, conta_id):
    empresa, _prov, _o1, _o2 = _duas_lojas(pool, conta_id)
    assert es.salvar_cadastro_stand(pool, conta_id, "S97", _so_cnpj())["ok"]
    r = es.salvar_cadastro_stand(pool, conta_id, "i04", _dados(
        fantasia="EM ESSENCE", doc=OUTRO_CNPJ, razao="EM ESSENCE NOVA LTDA"))
    assert r["ok"] and r["cliente_id"] != empresa
    assert cli.obter_cliente(pool, conta_id, empresa)["nome"] == "OCEAN BEACH"
    assert cli.obter_cliente(pool, conta_id, r["cliente_id"])["nome"] == "EM ESSENCE"
    cads = _cads(pool, conta_id, "i04", "S97")
    assert cads["i04"]["fantasia"] == "EM ESSENCE" and cads["S97"]["fantasia"] == "OCEAN BEACH"
    assert cads["S97"]["doc"] == CNPJ_VALIDO and cads["i04"]["doc"] == OUTRO_CNPJ


def test_o_app_puxa_o_mapa_depois_de_todo_salvar_e_mostra_a_frase():
    from web import painel_cockpit as pc
    assert "if(window.stAtualizar)window.stAtualizar();" in pc._STANDS_JS
    assert "if(j.juntou&&window.stAtualizar)" not in pc._STANDS_JS
    assert "m.scrollIntoView(" in pc._STANDS_JS
    assert "if(pedindo)denovo=true;else atualizar();" in pc._STANDS_AUTO_JS


def test_pelo_whatsapp_do_lojista_a_vendedora_salva_num_cadastro_sem_dono(pool, conta_id):
    cass = _membro(pool, conta_id, "Cassandra")
    joao = cli.salvar_cliente(pool, conta_id, "LOJA DO JOAO", telefone="86922220002")["id"]
    _venda(pool, conta_id, "S97", nome="OCEAN BEACH", zap="86922220002")
    _dono_da_venda(pool, conta_id, "S97", cass)
    assert es.buscar(pool, conta_id, "S97")["cliente_id"] is None
    r = es.salvar_cadastro_stand(pool, conta_id, "S97", _dados(
        fantasia="OCEAN BEACH", doc="", whats="86922220002"), juntar="do_vendedor", vendedor_id=cass)
    assert r["ok"] and r["cliente_id"] == joao, r
