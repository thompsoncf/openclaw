"""A foto que chega vira ponteiro, não arquivo.

O CASO REAL (28/08/2026, Prime Eventos). Em 48 horas, 598 mensagens de um-para-um
sem texto foram descartadas — 299 por dia, contra 124 medidos uma semana antes. Não
são grupos nem status: esse filtro já sai da conta. É o cliente mandando foto de
referência de decoração, print do orçamento do concorrente, vídeo do salão,
comprovante do sinal. O vendedor via tudo — no celular dele, que é a razão de ele
não largar o aparelho.

A ESCOLHA QUE ESTE ARQUIVO TRANCA
O arquivo não é guardado. O WhatsApp já mantém a mídia cifrada no CDN dele, e a
mensagem traz `directPath` e `mediaKey` — ~200 bytes que dizem onde está e como
decifrar. Guardar o arquivo seriam ~110 GB por ano só nesta conta, contra 22 MB de
ponteiro, e ainda pediria política de retenção, disco e rotina de limpeza. Quem
busca é o serviço web, sob demanda, quando alguém abre a conversa.

E POR QUE A PENEIRA EXISTE
`_midia_do_payload` recebe entrada de REDE que vai direto pra uma coluna jsonb. Sem
teto de tamanho, um `directPath` forjado de megabytes viraria linha de megabytes no
banco — e o banco inteiro tem 87 MB hoje. Sem `mediaKey`, a bolha prometeria uma
imagem que nunca vai carregar.
"""
import json
import os

import pytest
from psycopg_pool import ConnectionPool

from web import painel_prospeccao as pp

CONTA = 34
CAMINHO = "/v/t62.7118-24/12345_67890_112233_n.enc"
CHAVE = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="


def _midia(**kw):
    base = {"tipo": "imagem",
            "ref": {"directPath": CAMINHO, "mediaKey": CHAVE, "mimetype": "image/jpeg"},
            "meta": {"bytes": 184320, "largura": 1280, "altura": 960}}
    base.update(kw)
    return base


# ------------------------------------------------------------------ a peneira

def test_o_ponteiro_completo_passa():
    m = pp._midia_do_payload(_midia())
    assert m["tipo"] == "imagem"
    assert m["ref"]["directPath"] == CAMINHO
    assert m["ref"]["mediaKey"] == CHAVE
    assert m["meta"]["bytes"] == 184320


@pytest.mark.parametrize("tipo", ["imagem", "video", "documento", "figurinha"])
def test_os_quatro_tipos_que_a_tela_sabe_desenhar(tipo):
    assert pp._midia_do_payload(_midia(tipo=tipo))["tipo"] == tipo


def test_tipo_desconhecido_nao_vira_ponteiro():
    """A bolha prometeria uma coisa que a tela não sabe mostrar. O texto da marca já
    conta o que chegou — é melhor a linha sem imagem do que a imagem quebrada."""
    assert pp._midia_do_payload(_midia(tipo="holograma")) is None
    assert pp._midia_do_payload(_midia(tipo="")) is None


def test_sem_endereco_ou_sem_chave_nao_passa():
    assert pp._midia_do_payload(_midia(ref={"mediaKey": CHAVE})) is None
    assert pp._midia_do_payload(_midia(ref={"directPath": CAMINHO})) is None
    assert pp._midia_do_payload(_midia(ref={})) is None


def test_campo_gigante_e_recusado():
    """Entrada de rede indo pra coluna jsonb: sem teto, uma linha de megabytes."""
    assert pp._midia_do_payload(
        _midia(ref={"directPath": "/v/" + "x" * 600, "mediaKey": CHAVE})) is None
    assert pp._midia_do_payload(
        _midia(ref={"directPath": CAMINHO, "mediaKey": "k" * 300})) is None


def test_lixo_nao_estoura():
    for v in (None, "", 42, [], {"tipo": "imagem"}, {"ref": "nao é dict"}):
        assert pp._midia_do_payload(v) is None


def test_meta_so_aceita_numero_que_faz_sentido():
    m = pp._midia_do_payload(_midia(meta={"bytes": "abc", "segundos": -5,
                                          "largura": 0, "altura": 720}))
    assert "bytes" not in m["meta"] and "segundos" not in m["meta"]
    assert "largura" not in m["meta"] and m["meta"]["altura"] == 720


def test_nome_de_arquivo_e_cortado():
    m = pp._midia_do_payload(_midia(tipo="documento", meta={"nome": "a" * 400}))
    assert len(m["meta"]["nome"]) == 160


# --------------------------------------------------------------- a gravação

_SQL = """
create table contas (id bigserial primary key, nome text, chip_de bigint);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, contato text, whatsapp text, telefone text, email text, cidade text,
  uf text, origem text, status text default 'novo', estagio text default 'lead',
  temperatura text, criado_em timestamptz default now(),
  atualizado_em timestamptz default now(), tipo text);
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text default 'whatsapp', contato_ref text, contato_nome text,
  status text default 'aberta', agente_ativo boolean default false,
  janela_expira_em timestamptz, ultima_msg_em timestamptz default now(),
  criado_em timestamptz default now(), chip_id bigint, responsavel_membro_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, canal text,
  direcao text, autor text, texto text, membro_id bigint, provider_sid text,
  status text, meta jsonb, criado_em timestamptz default now(),
  midia_ref jsonb, midia_tipo text, midia_meta jsonb);
create unique index on mensagens (conversa_id, provider_sid) where provider_sid is not null;
create table wa_contatos (conta_id bigint, numero8 text, nome text);
create table membros (id bigserial primary key, conta_id bigint, nome text, papel text,
  ativo boolean default true);
"""


@pytest.fixture()
def pool():
    dbname = "zaq_wa_midia"
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1,
                           open=True, kwargs={"autocommit": True, "prepare_threshold": None})
    with admin.connection() as c:
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True,
                       kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.execute("insert into contas (id, nome) values (%s,'PRIME')", (CONTA,))
        c.commit()
    yield p
    p.close()


def _entrar(pool, texto, midia=None, sid="M1"):
    with pool.connection() as c:
        conv, nova = pp._wa_inbound_conversa(c, CONTA, "558699495501", texto, sid,
                                             "Layane Castro", False, midia=midia)
        c.commit()
    with pool.connection() as c:
        return c.execute(
            """select texto, midia_tipo, midia_ref, midia_meta from mensagens
                where conversa_id=%s order by id desc limit 1""", (conv,)).fetchone()


def test_a_foto_sem_legenda_vira_mensagem(pool):
    """Era esta que sumia: sem texto, o wa-qr dava return e ninguém ficava sabendo.
    Agora a marca é o texto e o ponteiro vai junto."""
    texto, tipo, ref, meta = _entrar(pool, "📷 Foto", pp._midia_do_payload(_midia()))
    assert texto == "📷 Foto"
    assert tipo == "imagem"
    assert ref["directPath"] == CAMINHO and ref["mediaKey"] == CHAVE
    assert meta["bytes"] == 184320


def test_a_foto_com_legenda_guarda_as_duas_coisas(pool):
    """Esta já chegava hoje — como texto, perdendo a foto."""
    texto, tipo, ref, _ = _entrar(pool, "queria uma decoração assim",
                                  pp._midia_do_payload(_midia()))
    assert texto == "queria uma decoração assim"
    assert tipo == "imagem" and ref["directPath"] == CAMINHO


def test_mensagem_de_texto_nao_ganha_coluna_de_midia(pool):
    """O caminho de sempre não pode mudar: é a esmagadora maioria das linhas."""
    texto, tipo, ref, meta = _entrar(pool, "Olá, bom dia!")
    assert texto == "Olá, bom dia!"
    assert tipo is None and ref is None and meta is None


def test_o_arquivo_nao_entra_no_banco(pool):
    """A linha inteira tem que caber em centenas de bytes — não em megabytes. É a
    diferença entre 22 MB por ano e 110 GB."""
    _entrar(pool, "📷 Foto", pp._midia_do_payload(_midia()))
    with pool.connection() as c:
        n = c.execute(
            """select length(coalesce(midia_ref::text,'')) + length(coalesce(midia_meta::text,''))
                 from mensagens order by id desc limit 1""").fetchone()[0]
    assert n < 600, f"o ponteiro cresceu pra {n} bytes — tem arquivo entrando aqui"


def test_reentrega_nao_duplica(pool):
    """O wa-qr remanda a mesma mensagem quando a conexão oscila."""
    _entrar(pool, "📷 Foto", pp._midia_do_payload(_midia()), sid="MESMO")
    _entrar(pool, "📷 Foto", pp._midia_do_payload(_midia()), sid="MESMO")
    with pool.connection() as c:
        assert c.execute("select count(*) from mensagens").fetchone()[0] == 1


def test_midia_invalida_nao_impede_a_mensagem(pool):
    """Ponteiro quebrado é motivo pra não ter bolha de imagem, nunca pra perder a
    mensagem — que é exatamente o defeito que este passo veio consertar."""
    texto, tipo, ref, _ = _entrar(pool, "📷 Foto", pp._midia_do_payload({"tipo": "imagem"}))
    assert texto == "📷 Foto"
    assert tipo is None and ref is None
