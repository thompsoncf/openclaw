"""Lead pessoa física: o documento vai pra COLUNA certa e o CPF é validado de verdade.

A regra que importa é `_doc_lead`: quem decide se é CPF ou CNPJ é o TAMANHO do
documento, não a pílula que ficou marcada na tela. Sem isso, colar um CPF com
"Pessoa Jurídica" ligado gravaria 11 dígitos na coluna cnpj — e as duas
deduplicações (uq_prospeccao_conta_cnpj e a nova uq_prospeccao_conta_cpf) parariam
de pegar o cadastro repetido.
"""
import json
import os
from types import SimpleNamespace

import pytest
from psycopg_pool import ConnectionPool

from web import painel_prospeccao as pp
from web.painel_prospeccao import _doc_lead, _fmt_doc

CPF_OK = "12345678909"
CPF_RUIM = "12345678900"        # mesmo tamanho, dígito verificador errado
CNPJ_OK = "10320422000106"
CONTA = 5

_SQL = """
create table prospeccao (id bigserial primary key, evento_em date, evento_tipo text, evento_convidados int, evento_origem text, evento_trecho text, evento_pista text, evento_lido_em timestamptz, ultimo_contato_em timestamptz, conta_id bigint, vendedor_id bigint,
  empresa text not null, segmento text, cidade text, uf text, contato text, cargo text,
  telefone text, whatsapp text, email text, cnpj text, cpf text, tipo text default 'pj',
  temperatura text default 'frio', valor_estimado_centavos bigint default 0, origem text,
  obs text, socio text, regime_tributario text, porte text, instagram text, site_url text,
  tem_site boolean, receita jsonb, criado_por bigint, estagio text default 'base',
  atualizado_em timestamptz default now(), criado_em timestamptz default now(),
  constraint uq_prospeccao_conta_cnpj unique (conta_id, cnpj),
  constraint uq_prospeccao_conta_cpf unique (conta_id, cpf));
create table campanhas (id bigserial primary key, nome text);
create table campanha_alvos (id bigserial primary key, prospeccao_id bigint,
  campanha_id bigint, criado_em timestamptz default now());
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_lead_pf_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.commit()
    yield p
    p.close()


def _novo(req, **kw):
    """Chama a rota como o FastAPI chamaria: todo campo do formulário presente."""
    campos = dict(segmento="", cidade="", uf="", contato="", telefone="", whatsapp="",
                  email="", cnpj="", cpf="", documento="", tipo="", temperatura="frio",
                  valor="", origem="manual", vendedor_id="", obs="", socio="",
                  regime_tributario="", porte="", cargo="", instagram="", site_url="",
                  receita="", voltar="", destino="")
    campos.update(kw)
    return pp.prospeccao_novo(req, **campos)


def _logado(monkeypatch, pool):
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_acesso", lambda req: (
        {"conta_id": CONTA, "membro_id": 1, "gerencia": True, "pode_atribuir": True}, None))
    return SimpleNamespace(session={}, headers={"x-requested-with": "fetch"})


def test_cpf_valido_vai_pra_coluna_cpf_e_marca_pf():
    tipo, cnpj, cpf, erro = _doc_lead("pf", "", "123.456.789-09", "")
    assert (tipo, cnpj, cpf, erro) == ("pf", None, CPF_OK, "")


def test_cnpj_continua_indo_pra_coluna_cnpj_e_marca_pj():
    tipo, cnpj, cpf, erro = _doc_lead("pj", "10.320.422/0001-06", "", "")
    assert (tipo, cnpj, cpf, erro) == ("pj", CNPJ_OK, None, "")


def test_tamanho_manda_mesmo_com_a_pilula_errada():
    # colou um CPF com "Pessoa Jurídica" marcado: ainda assim entra como CPF/PF
    tipo, cnpj, cpf, _ = _doc_lead("pj", "", "", CPF_OK)
    assert (tipo, cnpj, cpf) == ("pf", None, CPF_OK)
    # e o contrário também: CNPJ com "Pessoa Física" marcado
    tipo, cnpj, cpf, _ = _doc_lead("pf", "", "", CNPJ_OK)
    assert (tipo, cnpj, cpf) == ("pj", CNPJ_OK, None)


def test_cpf_com_digito_errado_nao_entra():
    tipo, cnpj, cpf, erro = _doc_lead("pf", "", CPF_RUIM, "")
    assert (cnpj, cpf) == (None, None)
    assert "não existe" in erro          # e o motivo aparece pro usuário


def test_documento_de_tamanho_esquisito_e_recusado():
    _, cnpj, cpf, erro = _doc_lead("", "", "", "1234567")
    assert (cnpj, cpf) == (None, None)
    assert "CPF (11 dígitos)" in erro


def test_sem_documento_vale_o_tipo_do_formulario():
    assert _doc_lead("pf", "", "", "") == ("pf", None, None, "")
    assert _doc_lead("", "", "", "") == ("pj", None, None, "")   # padrão histórico


def test_documento_guardado_so_em_digitos():
    """Máscara é coisa de exibição. No banco vai limpo, senão '123.456.789-09' e
    '12345678909' seriam dois leads diferentes pro índice único."""
    _, _, cpf, _ = _doc_lead("pf", "", "123.456.789-09", "")
    assert cpf == CPF_OK
    assert _fmt_doc(cpf) == "123.456.789-09"
    assert _fmt_doc(CNPJ_OK) == "10.320.422/0001-06"


def test_fmt_doc_nao_estraga_documento_torto_do_passado():
    assert _fmt_doc("") == ""
    assert _fmt_doc("123") == "123"


# ------------------------------------------------------- cadastro pela rota /novo

def test_cadastra_pessoa_fisica_pelo_formulario(pool, monkeypatch):
    req = _logado(monkeypatch, pool)
    r = _novo(req, empresa="Joana Ribeiro", documento="123.456.789-09", tipo="pf",
              whatsapp="+5586994867388")
    assert json.loads(r.body)["ok"] is True
    with pool.connection() as c:
        row = c.execute("select empresa, tipo, cpf, cnpj from prospeccao where conta_id=%s",
                        (CONTA,)).fetchone()
    assert row == ("Joana Ribeiro", "pf", CPF_OK, None)


def test_pessoa_fisica_nao_guarda_socio_regime_porte(pool, monkeypatch):
    """Campos do quadro societário não existem pra pessoa — se vierem no POST
    (aba trocada com o form já preenchido), são descartados em vez de virar lixo."""
    req = _logado(monkeypatch, pool)
    _novo(req, empresa="Joana Ribeiro", documento=CPF_OK, tipo="pf",
          socio="Fulano", regime_tributario="Simples", porte="ME")
    with pool.connection() as c:
        row = c.execute("select socio, regime_tributario, porte from prospeccao where conta_id=%s",
                        (CONTA,)).fetchone()
    assert row == (None, None, None)


def test_cpf_invalido_nao_cria_lead(pool, monkeypatch):
    req = _logado(monkeypatch, pool)
    r = _novo(req, empresa="Joana Ribeiro", documento=CPF_RUIM, tipo="pf")
    assert r.status_code == 400
    assert "não existe" in json.loads(r.body)["erro"]
    with pool.connection() as c:
        assert c.execute("select count(*) from prospeccao").fetchone()[0] == 0


def test_cpf_repetido_avisa_qual_lead_ja_existe(pool, monkeypatch):
    req = _logado(monkeypatch, pool)
    _novo(req, empresa="Joana Ribeiro", documento=CPF_OK, tipo="pf")
    r = _novo(req, empresa="Joana R.", documento=CPF_OK, tipo="pf")
    assert r.status_code == 409
    erro = json.loads(r.body)["erro"]
    assert "Joana Ribeiro" in erro and "CPF 123.456.789-09" in erro
    with pool.connection() as c:
        assert c.execute("select count(*) from prospeccao").fetchone()[0] == 1


def test_empresa_continua_entrando_como_pj(pool, monkeypatch):
    req = _logado(monkeypatch, pool)
    _novo(req, empresa="Mercado Avenida", documento="10.320.422/0001-06", tipo="pj",
          socio="Carlos", porte="ME")
    with pool.connection() as c:
        row = c.execute("select tipo, cnpj, cpf, socio, porte from prospeccao").fetchone()
    assert row == ("pj", CNPJ_OK, None, "Carlos", "ME")


def test_lead_sem_documento_nasce_pj_como_sempre(pool, monkeypatch):
    req = _logado(monkeypatch, pool)
    _novo(req, empresa="Padaria do Bairro")
    with pool.connection() as c:
        assert c.execute("select tipo, cnpj, cpf from prospeccao").fetchone() == ("pj", None, None)


# ------------------------------------------------------- edição na ficha do lead

def _editar(req, monkeypatch, pool, alvo_id, **kw):
    with pool.connection() as c:
        r = c.execute("select id, empresa, vendedor_id from prospeccao where id=%s",
                      (alvo_id,)).fetchone()
    monkeypatch.setattr(pp, "_carrega_alvo", lambda p, cid, aid:
                        {"id": r[0], "empresa": r[1], "vendedor_id": r[2]})
    monkeypatch.setattr(pp, "_pode_ver", lambda alvo, ctx: True)
    campos = dict(contato="", cargo="", telefone="", whatsapp="", email="", cnpj="", cpf="",
                  documento="", tipo="", segmento="", cidade="", uf="", valor="", socio="",
                  regime_tributario="", porte="", instagram="", empresa="", tem_site="",
                  site_url="", obs="")
    campos.update(kw)
    return pp.prospeccao_editar(req, alvo_id=alvo_id, **campos)


def test_ficha_converte_empresa_em_pessoa_fisica(pool, monkeypatch):
    req = _logado(monkeypatch, pool)
    _novo(req, empresa="Joana Ribeiro ME", documento=CNPJ_OK, tipo="pj", socio="Joana")
    with pool.connection() as c:
        lead = c.execute("select id from prospeccao").fetchone()[0]
    _editar(req, monkeypatch, pool, lead, empresa="Joana Ribeiro", documento=CPF_OK, tipo="pf")
    with pool.connection() as c:
        row = c.execute("select empresa, tipo, cpf, cnpj, socio from prospeccao where id=%s",
                        (lead,)).fetchone()
    assert row == ("Joana Ribeiro", "pf", CPF_OK, None, None)   # o CNPJ sai junto com o tipo


def test_editar_sem_mexer_no_nome_nao_apaga_o_nome(pool, monkeypatch):
    req = _logado(monkeypatch, pool)
    _novo(req, empresa="Mercado Avenida")
    with pool.connection() as c:
        lead = c.execute("select id from prospeccao").fetchone()[0]
    _editar(req, monkeypatch, pool, lead, telefone="86 3333-0000")
    with pool.connection() as c:
        assert c.execute("select empresa, telefone from prospeccao where id=%s",
                         (lead,)).fetchone() == ("Mercado Avenida", "86 3333-0000")


def test_csv_importa_cpf_e_nao_perde_o_resto_por_uma_linha_repetida(pool, monkeypatch):
    """Uma linha com documento repetido derrubava a transação e a importação inteira
    voltava ZERO lead. Agora cai só a linha — e o aviso diz quantas foram."""
    import asyncio
    import io

    from starlette.datastructures import UploadFile

    req = _logado(monkeypatch, pool)
    csv = ("nome;cpf;whatsapp\n"
           "Joana Ribeiro;123.456.789-09;86994867388\n"
           "Joana de novo;123.456.789-09;86994867388\n"
           "Padaria do Bairro;;8633330000\n")
    arq = UploadFile(file=io.BytesIO(csv.encode("utf-8")), filename="leads.csv")
    r = asyncio.run(pp.captar_csv(req, arquivo=arq, vendedor_id=""))
    d = json.loads(r.body)
    assert (d["inseridos"], d["repetidos"]) == (2, 1)
    with pool.connection() as c:
        linhas = c.execute("select empresa, tipo, cpf from prospeccao order by id").fetchall()
    assert linhas == [("Joana Ribeiro", "pf", CPF_OK), ("Padaria do Bairro", "pj", None)]


def test_editar_com_cpf_de_outro_lead_avisa_e_nao_grava(pool, monkeypatch):
    req = _logado(monkeypatch, pool)
    _novo(req, empresa="Joana Ribeiro", documento=CPF_OK, tipo="pf")
    _novo(req, empresa="Outra Pessoa", tipo="pf")
    with pool.connection() as c:
        outra = c.execute("select id from prospeccao where empresa='Outra Pessoa'").fetchone()[0]
    _editar(req, monkeypatch, pool, outra, documento=CPF_OK, tipo="pf")
    assert "Joana Ribeiro" in req.session.get("prosp_aviso", "")
    with pool.connection() as c:
        assert c.execute("select cpf from prospeccao where id=%s", (outra,)).fetchone()[0] is None
