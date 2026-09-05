"""Cockpit do Vendedor (finance/cockpit): motor do app do vendedor.

- token mágico: gera, valida 1x, expira / não reusa;
- membro_por_email acha o vendedor ativo;
- leads_do_vendedor traz só os leads DELE, abertos, marcando IA vs sua vez;
- posse: não deixa mexer no lead de outro (mudar_etapa/fechar/assumir/mensagem);
- ações: mudar etapa, assumir (pausa o bot), fechar (sai da fila), enviar mensagem;
- pausar rodízio reflete no perfil.

Banco dedicado e descartável com o schema mínimo (mesmo padrão do teste de blindagem).
"""
import os
from datetime import date, datetime, timedelta, timezone

import pytest
from psycopg_pool import ConnectionPool

from finance import cockpit as ck

_BASE_SQL = """
create table nichos (id bigserial primary key, nome text, slug text unique, tipo text);
create table contas (id bigserial primary key, nome text, documento text, razao_social text,
  nome_fantasia text, endereco text, bairro text, cep text, cidade text, uf text,
  email_empresa text, telefone text, nicho text, cnae text,
  -- o nicho decide o MODO do orçamento (evento × recorrente), inclusive quando a
  -- proposta nasce aqui no cockpit do vendedor
  nicho_id bigint references nichos(id), chip_de bigint);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text default 'vendedor', ativo boolean default true, whatsapp text,
  cockpit_push_ativo boolean default true, cockpit_pausado boolean default false);
create table prospeccao (id bigserial primary key, evento_em date, evento_tipo text, evento_convidados int, evento_origem text, evento_trecho text, evento_pista text, evento_lido_em timestamptz, conta_id bigint, vendedor_id bigint,
  empresa text, cnpj text, segmento text, cidade text, uf text, contato text, cargo text,
  telefone text, whatsapp text, email text, status text default 'novo', temperatura text default 'frio',
  valor_estimado_centavos bigint default 0, origem text, obs text, instagram text, socio text,
  regime_tributario text, porte text, ultimo_contato_em timestamptz, proximo_contato_em timestamptz,
  orcamento_id bigint, tem_site boolean, maps_url text, receita text, site_url text,
  decisor_nome text, decisor_cargo text, decisor_telefone text, decisor_whatsapp boolean,
  decisor_em timestamptz, decisor_telefones jsonb, estagio text default 'lead',
  tipo text default 'pj', cpf text,
  -- endereço + aniversário (migração 171): a ficha do vendedor preenche, e o
  -- cep é o que puxa rua/bairro/cidade/uf pela BrasilAPI na tela
  cep text, endereco text, numero text, bairro text, nascimento date,
  criado_por bigint,
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text, status text default 'aberta', agente_ativo boolean default true,
  -- `contato_ref` entrou aqui pro aviso "este número tem outra conversa" rodar de
  -- verdade nestes testes: sem a coluna ele cai no `except` e a tela do vendedor
  -- passaria sem nunca exercitar a consulta.
  contato_ref text,
  responsavel_membro_id bigint, ultima_msg_em timestamptz default now(),
  push_avisado_em timestamptz, criado_em timestamptz default now(), chip_id bigint, visto_ate_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, canal text, direcao text,
  autor text default 'humano', membro_id bigint, texto text default '', provider_sid text,
  criado_em timestamptz default now(),
  midia_ref jsonb, midia_tipo text, midia_meta jsonb, midia_arquivo text, midia_guardada_em timestamptz, midia_guardada_por bigint);
create table funil_etapas (id bigserial primary key, conta_id bigint, chave text, rotulo text,
  -- `fase` (migração 177) é o que os painéis leem pra saber o que conta como venda
  -- ganha; sem a coluna aqui, toda consulta do cockpit estoura com UndefinedColumn
  ordem int default 0, fixa boolean default false, fase text not null default 'venda',
  prazo_min integer, gatilho text, gatilho_ativo boolean not null default false,
  unique (conta_id, chave));
create table prospeccao_atividades (id bigserial primary key, prospeccao_id bigint, membro_id bigint,
  tipo text, resultado text, descricao text, criado_em timestamptz default now());
create table cockpit_acesso (token text primary key, conta_id bigint, membro_id bigint,
  expira_em timestamptz not null, usado_em timestamptz, criado_em timestamptz default now());
create table push_assinaturas (id bigserial primary key, conta_id bigint, membro_id bigint,
  endpoint text unique, p256dh text, auth text, criado_em timestamptz default now());
create table servicos_catalogo (id bigserial primary key, conta_id bigint, slug text, nome text,
  descricao text, setup_centavos bigint default 0, mensal_centavos bigint default 0,
  custo_centavos bigint default 0, ordem int default 0, ativo boolean default true,
  categoria text, foto_url text, icone text);
create table eventos_agenda (id bigserial primary key, conta_id bigint, membro_id bigint,
  titulo text, inicio timestamptz, fim timestamptz, local text, descricao text,
  lembrete_min int, tipo text default 'pessoal', link_online text, desfecho text,
  status text default 'ativo', criado_em timestamptz default now(), prospeccao_id bigint, ics_token text,
  pre_reserva_ate timestamptz, sinal_centavos int,
  tipo_evento text, convidados int, hora_sugerida boolean default false);
  -- 160/163/179: agenda._COLS lê todas essas
-- o Raio-X (finance/raio_x) lê estas três (e `orcamentos`, criada no fixture pelo
-- DDL do próprio app) além das de cima; sem elas cada bloco da tela cairia no
-- `except` e o teste passaria sem exercitar a consulta
create table contratos (id bigserial primary key, conta_id bigint, orcamento_id bigint,
  status text default 'enviado', valor_centavos bigint, assinado_em timestamptz, enviado_em timestamptz);
create table wa_qr_log (id bigserial primary key, conta_id bigint, nivel text default 'warn',
  msg text not null default '', dados jsonb, criado_em timestamptz not null default now());
create table wa_decifra_diario (dia date not null, conta_id bigint not null, from_me boolean not null,
  ocorrencias int not null default 0, ids_distintos int not null default 0,
  chegaram int, nunca_chegaram int, correlacionado_em timestamptz,
  apurado_em timestamptz not null default now(), primary key (dia, conta_id, from_me));
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_cockpit_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_BASE_SQL)
        # as Novidades no app (migração 199): a tabela nasce na 174, o check de
        # público na 184, pra_quem/resumo/link na 199. `listar` corta pelo
        # `criado_em` da conta, que este schema não tinha. Os avisos são GLOBAIS
        # e o banco é de módulo: cada teste cria a conta (criado_em = now(), o
        # início da transação) e grava o aviso com clock_timestamp(), que é
        # depois — assim a conta só enxerga os avisos do próprio teste.
        from tests.test_novidades import BASE as _MIG
        c.execute("alter table contas add column criado_em timestamptz not null default now()")
        for m in ("174_novidades.sql", "184_novidade_voz_e_porta_fechada.sql",
                  "199_novidades_pra_quem.sql", "207_raio_x.sql"):
            c.execute((_MIG / m).read_text(encoding="utf-8"))
        # `orcamentos` com TODAS as colunas do app (o Raio-X lê status, aprovada_em,
        # sinal_pago_em, primeiro_ano_centavos; criar_orcamento grava dezenas)
        from web.painel_servicos import _criar_orcamentos
        _criar_orcamentos(c)
        c.commit()
    yield p
    p.close()


def _conta(c, nome="Emp"):
    return c.execute("insert into contas (nome) values (%s) returning id", (nome,)).fetchone()[0]


def _membro(c, conta, nome="Rob", email=None, papel="vendedor", ativo=True):
    email = email or f"{nome.lower()}-{conta}@x.com"
    return c.execute("insert into membros (conta_id, nome, email, papel, ativo) values (%s,%s,%s,%s,%s) returning id",
                     (conta, nome, email, papel, ativo)).fetchone()[0]


def _lead(c, conta, vend, empresa="Padaria", status="novo", estagio="lead", wa="5586999990000"):
    return c.execute("""insert into prospeccao (conta_id, vendedor_id, empresa, status, estagio, whatsapp)
                        values (%s,%s,%s,%s,%s,%s) returning id""",
                     (conta, vend, empresa, status, estagio, wa)).fetchone()[0]


# ------------------------------------------------------------------ token mágico
def test_token_valida_e_reusa_na_janela(pool):
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta); c.commit()
    tok = ck.gerar_token(pool, conta, vend)
    d = ck.validar_token(pool, tok)
    assert d and d["conta_id"] == conta and d["membro_id"] == vend and d["papel"] == "vendedor"
    # reusável dentro dos 15 min (scanner de e-mail não queima o link do vendedor)
    assert ck.validar_token(pool, tok) is not None
    with pool.connection() as c:
        assert c.execute("select usado_em from cockpit_acesso where token=%s", (tok,)).fetchone()[0] is not None


def test_token_expirado_nao_valida(pool):
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta)
        c.execute("""insert into cockpit_acesso (token, conta_id, membro_id, expira_em)
                     values ('velho',%s,%s,%s)""",
                  (conta, vend, datetime.now(timezone.utc) - timedelta(minutes=1)))
        c.commit()
    assert ck.validar_token(pool, "velho") is None
    assert ck.validar_token(pool, "nao-existe") is None


def test_membro_por_email(pool):
    with pool.connection() as c:
        conta = _conta(c)
        vend = _membro(c, conta, email="vend@x.com")
        _membro(c, conta, nome="Fin", email="fin@x.com", papel="financeiro")
        c.commit()
    assert ck.membro_por_email(pool, "VEND@x.com") == {"conta_id": conta, "membro_id": vend}
    assert ck.membro_por_email(pool, "fin@x.com") is None      # financeiro não usa cockpit
    assert ck.membro_por_email(pool, "ninguem@x.com") is None


# ------------------------------------------------------------------ inbox / escopo
def test_leads_do_vendedor_escopo_e_abertos(pool):
    with pool.connection() as c:
        conta = _conta(c)
        v1 = _membro(c, conta, nome="V1", email="v1@x.com")
        v2 = _membro(c, conta, nome="V2", email="v2@x.com")
        aberto = _lead(c, conta, v1, "Aberto")
        _lead(c, conta, v1, "Ganho", status="ganho")           # fechado: fora
        _lead(c, conta, v1, "Base", estagio="base")            # ainda base: fora
        _lead(c, conta, v2, "DoOutro")                          # de outro vendedor: fora
        # conversa com bot ativo → IA
        c.execute("insert into conversas (conta_id, prospeccao_id, canal, agente_ativo) values (%s,%s,'whatsapp',true)",
                  (conta, aberto))
        c.commit()
    leads = ck.leads_do_vendedor(pool, conta, v1)
    assert [l["empresa"] for l in leads] == ["Aberto"]
    assert leads[0]["ia"] is True


def test_lead_do_vendedor_posse(pool):
    with pool.connection() as c:
        conta = _conta(c)
        v1 = _membro(c, conta, nome="V1", email="v1b@x.com")
        v2 = _membro(c, conta, nome="V2", email="v2b@x.com")
        meu = _lead(c, conta, v1, "Meu")
        alheio = _lead(c, conta, v2, "Alheio")
        c.commit()
    d = ck.lead_do_vendedor(pool, conta, v1, meu)
    assert d and d["empresa"] == "Meu"
    assert all(e["chave"] not in ("ganho", "perdido") for e in d["etapas"])   # etapas sem fechados
    assert ck.lead_do_vendedor(pool, conta, v1, alheio) is None               # não é dele


def test_conversa_longa_traz_as_ultimas_mensagens(pool):
    """O corte de 200 tem que pegar as ÚLTIMAS, não as primeiras. Com 'asc limit 200'
    o vendedor abria uma conversa de 250 mensagens e via só o começo dela — nunca o
    pedido que acabou de chegar, que é justamente pra isso que ele abre a tela."""
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="longa@x.com")
        lead = _lead(c, conta, vend, "Conversa longa")
        conv = c.execute("insert into conversas (conta_id, prospeccao_id, canal, agente_ativo) "
                         "values (%s,%s,'whatsapp',false) returning id", (conta, lead)).fetchone()[0]
        base = datetime.now(timezone.utc) - timedelta(days=1)
        for i in range(250):
            c.execute("insert into mensagens (conversa_id, canal, direcao, autor, texto, criado_em) "
                      "values (%s,'whatsapp','in','lead',%s,%s)",
                      (conv, f"msg {i}", base + timedelta(minutes=i)))
        c.commit()

    d = ck.lead_do_vendedor(pool, conta, vend, lead)
    msgs = d["mensagens"]
    assert len(msgs) == 200
    assert msgs[-1]["texto"] == "msg 249"          # a última é a mais recente
    assert msgs[0]["texto"] == "msg 50"            # cortou pelo começo, não pelo fim
    assert [m["texto"] for m in msgs] == [f"msg {i}" for i in range(50, 250)]   # ordem de leitura


# ------------------------------------------------------------------ ficha do cliente
def test_salvar_ficha_preenche_e_nao_apaga(pool):
    """O lead entra pelo WhatsApp só com número; quem conversa é quem descobre nome,
    CPF e e-mail. Salvar duas vezes não pode zerar o que foi digitado antes."""
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="fic@x.com")
        lead = _lead(c, conta, vend, "5586999990001")
        c.commit()

    r = ck.salvar_ficha(pool, conta, vend, lead, {
        "empresa": "Bruna", "contato": "Bruna Silva", "cargo": "Mãe da debutante",
        "email": "BRUNA@Exemplo.com ", "cidade": "Teresina", "uf": "pi",
        "documento": "529.982.247-25", "obs": "Festa 08/05/27"})
    assert r["ok"] is True

    with pool.connection() as c:
        got = c.execute("""select empresa, contato, cargo, email, cidade, uf, tipo, cpf, cnpj, obs
                             from prospeccao where id=%s""", (lead,)).fetchone()
    assert got == ("Bruna", "Bruna Silva", "Mãe da debutante", "bruna@exemplo.com",
                   "Teresina", "PI", "pf", "52998224725", None, "Festa 08/05/27")

    # segunda passada só com o telefone: o resto continua lá (inclusive o CPF)
    assert ck.salvar_ficha(pool, conta, vend, lead, {"telefone": "86 99999-0001"})["ok"] is True
    with pool.connection() as c:
        got = c.execute("select contato, telefone, cpf, obs from prospeccao where id=%s",
                        (lead,)).fetchone()
    assert got == ("Bruna Silva", "86 99999-0001", "52998224725", "Festa 08/05/27")


def test_ficha_traz_o_whatsapp_do_lead_que_entrou_conversando(pool):
    """O buraco que motivou o campo: quem chega por `whatsapp_inbound` nasce com o
    número em `whatsapp` e `telefone` NULL. A ficha do vendedor mostrava só `telefone`,
    então o campo aparecia EM BRANCO justamente no lead que veio conversando."""
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="wa@x.com")
        lead = _lead(c, conta, vend, "Cliente do zap", wa="+5586999990001")
        c.commit()
    d = ck.lead_do_vendedor(pool, conta, vend, lead)
    assert d["whatsapp"] == "+5586999990001"
    assert not d["telefone"], "o lead de WhatsApp não tem telefone — é esse o ponto"


def test_salvar_ficha_grava_whatsapp_sem_encostar_no_telefone(pool):
    """São COLUNAS diferentes e continuam separadas: o zap é por onde a conversa corre,
    o telefone é o fixo/comercial que o vendedor descobre depois. Salvar um não pode
    escrever no outro."""
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="wa2@x.com")
        lead = _lead(c, conta, vend, "Zap", wa="+5586999990002")
        c.commit()

    assert ck.salvar_ficha(pool, conta, vend, lead, {"telefone": "86 3221-0000"})["ok"] is True
    with pool.connection() as c:
        assert c.execute("select whatsapp, telefone from prospeccao where id=%s",
                         (lead,)).fetchone() == ("+5586999990002", "86 3221-0000")

    # trocar o zap (cliente passou outro número) não encosta no telefone
    assert ck.salvar_ficha(pool, conta, vend, lead, {"whatsapp": "+5511988887777"})["ok"] is True
    with pool.connection() as c:
        assert c.execute("select whatsapp, telefone from prospeccao where id=%s",
                         (lead,)).fetchone() == ("+5511988887777", "86 3221-0000")


def test_ficha_em_branco_nao_apaga_o_whatsapp(pool):
    """O número é a IDENTIDADE da conversa. Salvar a ficha inteira só pra corrigir um
    cargo não pode esvaziá-lo — o `coalesce` do salvar_ficha é o que garante isso, e
    aqui é onde ele mais importa."""
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="wa3@x.com")
        lead = _lead(c, conta, vend, "Zap", wa="+5586999990003")
        c.commit()
    assert ck.salvar_ficha(pool, conta, vend, lead,
                           {"cargo": "Comprador", "whatsapp": ""})["ok"] is True
    with pool.connection() as c:
        assert c.execute("select whatsapp, cargo from prospeccao where id=%s",
                         (lead,)).fetchone() == ("+5586999990003", "Comprador")


def test_ficha_guarda_endereco_e_aniversario(pool):
    """Campos da migração 171: o vendedor que vai VISITAR precisa de rua e número, e
    o aniversário é o que alimenta o aviso do dia (finance/lembretes._aniversarios)."""
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="end@x.com")
        lead = _lead(c, conta, vend, "Visitar"); c.commit()

    assert ck.salvar_ficha(pool, conta, vend, lead, {
        "cep": "64.000-000", "endereco": "Rua São Pedro", "numero": "120",
        "bairro": "Centro", "cidade": "Teresina", "uf": "pi",
        "nascimento": "1990-05-08"})["ok"] is True
    with pool.connection() as c:
        got = c.execute("""select cep, endereco, numero, bairro, cidade, uf, nascimento
                             from prospeccao where id=%s""", (lead,)).fetchone()
    # o CEP entra só com dígitos: veio mascarado da tela, e é assim que ele é comparável
    assert got == ("64000000", "Rua São Pedro", "120", "Centro", "Teresina", "PI",
                   date(1990, 5, 8))

    # branco não apaga, igual ao resto da ficha
    assert ck.salvar_ficha(pool, conta, vend, lead, {"numero": "121"})["ok"] is True
    with pool.connection() as c:
        got = c.execute("select numero, endereco, nascimento from prospeccao where id=%s",
                        (lead,)).fetchone()
    assert got == ("121", "Rua São Pedro", date(1990, 5, 8))


def test_data_de_nascimento_ruim_e_erro_e_nao_salva_nada(pool):
    """Engolir a data inválida e responder "Ficha salva ✓" seria mentir pro vendedor
    que acabou de digitá-la. E a recusa não pode gravar os outros campos pela metade."""
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="dt@x.com")
        lead = _lead(c, conta, vend, "Data"); c.commit()

    r = ck.salvar_ficha(pool, conta, vend, lead, {"cargo": "Chefe", "nascimento": "31/02/1990"})
    assert r["ok"] is False and "nascimento" in r["erro"].lower()
    with pool.connection() as c:
        assert c.execute("select cargo from prospeccao where id=%s", (lead,)).fetchone()[0] is None

    assert ck.salvar_ficha(pool, conta, vend, lead, {"nascimento": "2099-01-01"})["ok"] is False
    assert ck.salvar_ficha(pool, conta, vend, lead, {"nascimento": ""})["ok"] is True


# ------------------------------------------------------------------ lead manual
def test_criar_lead_cai_na_fila_do_proprio_vendedor(pool):
    """O app não tinha NENHUMA rota que criasse lead — o vendedor só trabalhava o que
    o rodízio entregava. Criado por ele, o lead nasce dele e já aberto."""
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="novo@x.com"); c.commit()

    r = ck.criar_lead(pool, conta, vend, "Dona Bruna", "(86) 99999-1234")
    assert r["ok"] is True and r["existia"] is False

    with pool.connection() as c:
        got = c.execute("""select vendedor_id, empresa, contato, whatsapp, tipo, origem,
                                  status, estagio, criado_por
                             from prospeccao where id=%s""", (r["lead_id"],)).fetchone()
    assert got == (vend, "Dona Bruna", "Dona Bruna", "+5586999991234", "pf",
                   "manual_vendedor", "novo", "lead", vend)
    assert r["lead_id"] in [l["id"] for l in ck.leads_do_vendedor(pool, conta, vend)]


def test_criar_lead_com_numero_repetido_abre_o_que_existe(pool):
    """Número repetido é o modo normal de errar aqui — ninguém sabe de cor quem já
    está na base. Dois cadastros partiriam a conversa em duas fichas."""
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="dup@x.com")
        antigo = _lead(c, conta, vend, "Já existe", wa="+5586999995555"); c.commit()

    r = ck.criar_lead(pool, conta, vend, "Outro nome", "86 99999-5555")
    assert r["ok"] is True and r["existia"] is True and r["lead_id"] == antigo

    # a grafia sem o nono dígito é o MESMO celular (ver _wa_equivalentes)
    r2 = ck.criar_lead(pool, conta, vend, "Terceiro", "8699995555")
    assert r2["existia"] is True and r2["lead_id"] == antigo
    with pool.connection() as c:
        assert c.execute("select count(*) from prospeccao where conta_id=%s",
                         (conta,)).fetchone()[0] == 1


def test_criar_lead_recusa_entrada_vazia_ou_curta(pool):
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="vaz@x.com"); c.commit()
    assert ck.criar_lead(pool, conta, vend, "", "86999991111")["ok"] is False
    assert ck.criar_lead(pool, conta, vend, "Sem número", "999")["ok"] is False
    with pool.connection() as c:
        assert c.execute("select count(*) from prospeccao where conta_id=%s",
                         (conta,)).fetchone()[0] == 0


def test_lead_de_outra_conta_com_o_mesmo_numero_nao_atrapalha(pool):
    """O dedup é por CONTA: o mesmo celular pode ser lead de duas empresas clientes."""
    with pool.connection() as c:
        a = _conta(c, "A"); b = _conta(c, "B")
        va = _membro(c, a, email="a@x.com"); vb = _membro(c, b, email="b@x.com")
        _lead(c, a, va, "Da A", wa="+5586999997777"); c.commit()
    r = ck.criar_lead(pool, b, vb, "Da B", "86999997777")
    assert r["existia"] is False


def test_salvar_ficha_posse_e_documento_invalido(pool):
    with pool.connection() as c:
        conta = _conta(c)
        v1 = _membro(c, conta, nome="F1", email="f1@x.com")
        v2 = _membro(c, conta, nome="F2", email="f2@x.com")
        meu = _lead(c, conta, v1, "Meu")
        alheio = _lead(c, conta, v2, "Alheio")
        c.commit()
    assert ck.salvar_ficha(pool, conta, v1, alheio, {"contato": "X"})["erro"] == "escopo"
    ruim = ck.salvar_ficha(pool, conta, v1, meu, {"documento": "111.111.111-11"})
    assert ruim["ok"] is False and "CPF" in ruim["erro"]
    with pool.connection() as c:      # nada gravado quando o documento é recusado
        assert c.execute("select cpf from prospeccao where id=%s", (meu,)).fetchone()[0] is None


# ------------------------------------------------------------------ ações
def test_mudar_etapa(pool):
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="et@x.com")
        lead = _lead(c, conta, vend)
        c.execute("insert into funil_etapas (conta_id, chave, rotulo) values (%s,'qualificado','Qualificado')", (conta,))
        c.commit()
    assert ck.mudar_etapa(pool, conta, vend, lead, "qualificado")["ok"] is True
    assert ck.mudar_etapa(pool, conta, vend, lead, "ganho")["ok"] is False    # ganho = usar fechar
    with pool.connection() as c:
        assert c.execute("select status from prospeccao where id=%s", (lead,)).fetchone()[0] == "qualificado"


def test_fechar_sai_da_fila(pool):
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="fe@x.com")
        lead = _lead(c, conta, vend)
        c.commit()
    assert ck.fechar(pool, conta, vend, lead, "ganho")["ok"] is True
    assert ck.leads_do_vendedor(pool, conta, vend) == []                       # fechado sai do inbox
    with pool.connection() as c:
        assert c.execute("select status from prospeccao where id=%s", (lead,)).fetchone()[0] == "ganho"


def test_assumir_pausa_o_bot(pool):
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="as@x.com")
        lead = _lead(c, conta, vend)
        c.execute("insert into conversas (conta_id, prospeccao_id, canal, agente_ativo) values (%s,%s,'whatsapp',true)",
                  (conta, lead))
        c.commit()
    assert ck.assumir(pool, conta, vend, lead)["ok"] is True
    with pool.connection() as c:
        r = c.execute("select agente_ativo, responsavel_membro_id from conversas where prospeccao_id=%s",
                      (lead,)).fetchone()
    assert r[0] is False and r[1] == vend


def test_enviar_mensagem_grava_e_pausa(pool, monkeypatch):
    from finance import whatsapp_out as wo
    # a assinatura do dublê segue a real: enviar(c, conta_id, numero, texto, *, chip_id).
    # Sem o `chip_id` aqui o dublê não bate com o que finance/cockpit.py chama, e o
    # teste morre de TypeError em vez de testar o cockpit.
    monkeypatch.setattr(wo, "enviar",
                        lambda c, cid, num, txt, *, chip_id=None: {"ok": True, "sid": "SM1"})
    with pool.connection() as c:
        conta = _conta(c)
        v1 = _membro(c, conta, nome="V1", email="m1@x.com")
        v2 = _membro(c, conta, nome="V2", email="m2@x.com")
        meu = _lead(c, conta, v1, "Meu")
        alheio = _lead(c, conta, v2, "Alheio")
        c.commit()
    assert ck.enviar_mensagem(pool, conta, v1, alheio, "oi")["ok"] is False    # não é dele
    assert ck.enviar_mensagem(pool, conta, v1, meu, "  ")["ok"] is False       # vazio
    assert ck.enviar_mensagem(pool, conta, v1, meu, "Olá!")["ok"] is True
    with pool.connection() as c:
        msg = c.execute("""select m.texto, m.direcao, cv.agente_ativo from mensagens m
                           join conversas cv on cv.id=m.conversa_id where cv.prospeccao_id=%s""", (meu,)).fetchone()
    assert msg[0] == "Olá!" and msg[1] == "out" and msg[2] is False


def test_pausar_rodizio_reflete_no_perfil(pool):
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="pa@x.com")
        c.commit()
    p0 = ck.perfil(pool, conta, vend)
    assert p0["pausado"] is False
    ck.set_pausado(pool, conta, vend, True)
    assert ck.perfil(pool, conta, vend)["pausado"] is True


def test_devolver_ia(pool):
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="dv@x.com")
        lead = _lead(c, conta, vend)
        c.execute("insert into conversas (conta_id, prospeccao_id, canal, agente_ativo) values (%s,%s,'whatsapp',false)",
                  (conta, lead))
        c.commit()
    assert ck.devolver_ia(pool, conta, vend, lead)["ok"] is True
    with pool.connection() as c:
        assert c.execute("select agente_ativo from conversas where prospeccao_id=%s", (lead,)).fetchone()[0] is True


def test_assinatura_push_salva_e_remove(pool):
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="ps@x.com"); c.commit()
    sub = {"endpoint": "https://push/abc", "keys": {"p256dh": "PPP", "auth": "AAA"}}
    assert ck.salvar_assinatura(pool, conta, vend, sub) is True
    assert ck.salvar_assinatura(pool, conta, vend, {"endpoint": "x"}) is False   # sem chaves
    with pool.connection() as c:
        assert c.execute("select count(*) from push_assinaturas where membro_id=%s", (vend,)).fetchone()[0] == 1
    ck.remover_assinatura(pool, "https://push/abc")
    with pool.connection() as c:
        assert c.execute("select count(*) from push_assinaturas where membro_id=%s", (vend,)).fetchone()[0] == 0


def test_enviar_push_respeita_toggle_e_limpa_morto(pool, monkeypatch):
    from finance import webpush
    monkeypatch.setattr(webpush, "configurado", lambda: True)
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="ep@x.com")
        for ep in ("https://push/1", "https://push/2"):
            c.execute("insert into push_assinaturas (conta_id, membro_id, endpoint, p256dh, auth) values (%s,%s,%s,'p','a')",
                      (conta, vend, ep))
        c.commit()
    # 1ª assinatura ok, 2ª expirada → deve ser removida
    def fake_enviar(sub, dados, ttl=3600):
        if sub["endpoint"].endswith("/2"):
            raise webpush.PushExpirado(sub["endpoint"])
        return True
    monkeypatch.setattr(webpush, "enviar", fake_enviar)
    n = ck.enviar_push(pool, conta, vend, "t", "c")
    assert n == 1
    with pool.connection() as c:
        eps = [r[0] for r in c.execute("select endpoint from push_assinaturas where membro_id=%s", (vend,)).fetchall()]
    assert eps == ["https://push/1"]           # a morta saiu
    # com push desligado no membro, não envia
    ck.set_push(pool, conta, vend, False)
    assert ck.enviar_push(pool, conta, vend, "t", "c") == 0


def test_pendentes_conta_so_o_que_ficou_sem_resposta(pool):
    """A bolinha vermelha do card: mensagens do cliente depois da última resposta.
    O push é aviso que passa; isto é o que fica pro vendedor que não viu."""
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="pend@x.com")
        lead = _lead(c, conta, vend, "Bruna")
        conv = c.execute("insert into conversas (conta_id, prospeccao_id, canal) "
                         "values (%s,%s,'whatsapp') returning id", (conta, lead)).fetchone()[0]

        def msg(direcao, autor, texto):
            c.execute("insert into mensagens (conversa_id, canal, direcao, autor, texto) "
                      "values (%s,'whatsapp',%s,%s,%s)", (conv, direcao, autor, texto))
        msg("in", "lead", "Boa tarde")
        msg("out", "vendedor", "Oi! Como posso ajudar?")   # respondeu: zera aqui
        msg("in", "lead", "Quero orçamento")
        msg("in", "lead", "pra 80 pessoas")
        c.commit()

    def pend():
        return {l["id"]: l["pend"] for l in ck.leads_do_vendedor(pool, conta, vend)}[lead]

    assert pend() == 2                    # as duas depois da resposta

    with pool.connection() as c:          # a IA responde → também zera
        c.execute("insert into mensagens (conversa_id, canal, direcao, autor, texto) "
                  "values (%s,'whatsapp','out','bot','Claro, vou verificar')", (conv,))
        c.commit()
    assert pend() == 0

    with pool.connection() as c:          # cliente volta a falar
        c.execute("insert into mensagens (conversa_id, canal, direcao, autor, texto) "
                  "values (%s,'whatsapp','in','lead','e tem data em maio?')", (conv,))
        c.commit()
    assert pend() == 1


def test_pendentes_conversa_nunca_respondida_conta_tudo(pool):
    """Lead que chegou e ninguém respondeu nunca: sem `out` na conversa, o corte
    não pode zerar por falta de referência — tem que contar todas."""
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="pend2@x.com")
        mudo = _lead(c, conta, vend, "Ninguém respondeu")
        conv = c.execute("insert into conversas (conta_id, prospeccao_id, canal) "
                         "values (%s,%s,'whatsapp') returning id", (conta, mudo)).fetchone()[0]
        for t in ("oi", "tem espaço dia 12?", "?"):
            c.execute("insert into mensagens (conversa_id, canal, direcao, autor, texto) "
                      "values (%s,'whatsapp','in','lead',%s)", (conv, t))
        sem_conversa = _lead(c, conta, vend, "Sem conversa")
        c.commit()
    por_id = {l["id"]: l["pend"] for l in ck.leads_do_vendedor(pool, conta, vend)}
    assert por_id[mudo] == 3
    assert por_id[sem_conversa] == 0      # lead sem conversa não inventa pendência


def test_sua_vez_e_bot_pausado_MAIS_mensagem_nova_nao_so_bot_pausado(pool):
    """Relato em produção (conta Prime, Thiago): o selo "sua vez" ficava preso
    pra sempre depois da 1ª resposta manual — pelo app OU direto no WhatsApp do
    celular —, porque `agente_ativo` só volta a `true` com um clique explícito
    em "Devolver ao agente", e ninguém clica nisso só pra tirar o selo da tela.

    "sua vez" tem que significar "bot pausado E tem mensagem nova pra
    responder" — não só "bot pausado". Assumiu e respondeu = sua_vez some,
    mesmo com o bot ainda desligado; chega mensagem nova do cliente = volta.

    Mockup aprovado (opção A): quando não é "sua vez" nem "IA", o selo não
    fica mudo — vira "respondido" (verde), contando que alguém já cuidou."""
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="sv@x.com")
        lead = _lead(c, conta, vend, "Sapataria Central")
        conv = c.execute("insert into conversas (conta_id, prospeccao_id, canal, agente_ativo) "
                         "values (%s,%s,'whatsapp',true) returning id", (conta, lead)).fetchone()[0]

        def msg(direcao, autor, texto):
            c.execute("insert into mensagens (conversa_id, canal, direcao, autor, texto) "
                      "values (%s,'whatsapp',%s,%s,%s)", (conv, direcao, autor, texto))
        c.commit()

    def estado():
        l = {x["id"]: x for x in ck.leads_do_vendedor(pool, conta, vend)}[lead]
        return l["ia"], l["sua_vez"], l["respondido"]

    assert estado() == (True, False, False)   # bot ligado: nem "sua vez" nem "respondido"

    with pool.connection() as c:
        msg("in", "lead", "Oi, tem sapato 42?")
        c.commit()
    assert estado() == (True, False, False)   # cliente escreveu, mas o bot ainda tá no comando

    with pool.connection() as c:          # vendedor assume (pelo app OU pelo celular — mesmo efeito)
        c.execute("update conversas set agente_ativo=false where id=%s", (conv,))
        c.commit()
    assert estado() == (False, True, False)   # bot pausado + mensagem sem resposta = sua vez de verdade

    with pool.connection() as c:          # vendedor responde — bot CONTINUA pausado (não volta sozinho)
        msg("out", "humano", "Temos sim! Qual cor?")
        c.commit()
    assert estado() == (False, False, True)   # respondeu: vira "respondido", não fica mudo

    with pool.connection() as c:          # cliente escreve de novo: volta a ser a vez dele
        msg("in", "lead", "Prefiro preto")
        c.commit()
    assert estado() == (False, True, False)


def test_respondido_nao_confunde_lead_assumido_sem_conversa_nenhuma(pool):
    """Um vendedor pode clicar "Assumir" num lead que ainda não trocou mensagem
    nenhuma (`agente_ativo=false`, zero linhas em `mensagens`). Isso não é
    "respondido" — não tem o que comemorar, ninguém respondeu nada ainda —
    nem "sua vez", porque não há mensagem pendente pra responder."""
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="assumido-vazio@x.com")
        lead = _lead(c, conta, vend, "Lead Assumido Vazio")
        c.execute("insert into conversas (conta_id, prospeccao_id, canal, agente_ativo) "
                  "values (%s,%s,'whatsapp',false)", (conta, lead))
        c.commit()
    l = {x["id"]: x for x in ck.leads_do_vendedor(pool, conta, vend)}[lead]
    assert (l["sua_vez"], l["respondido"]) == (False, False)


def test_respondido_nao_depende_de_qual_mensagem_e_a_ultima_por_criado_em(pool):
    """Achado ao verificar o mockup na tela: com a pergunta cliente + resposta
    gravadas na MESMA transação (mesmo instante — `now()` no Postgres é o
    início da transação, não o momento de cada INSERT), a mensagem do cliente
    podia "ganhar" o desempate de `order by criado_em desc limit 1` e aparecer
    como se fosse a última — fazendo "respondido" nunca ligar. `n_pend` já
    evitava exatamente essa armadilha usando `id`, não `criado_em`; a checagem
    de "respondido" tinha que evitar a mesma coisa."""
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="empate@x.com")
        lead = _lead(c, conta, vend, "Empate de Timestamp")
        conv = c.execute("insert into conversas (conta_id, prospeccao_id, canal, agente_ativo) "
                         "values (%s,%s,'whatsapp',false) returning id", (conta, lead)).fetchone()[0]
        # as DUAS na MESMA transação/commit — mesmo criado_em, de propósito
        c.execute("insert into mensagens (conversa_id, canal, direcao, autor, texto) "
                  "values (%s,'whatsapp','in','lead','oi, tudo bem?')", (conv,))
        c.execute("insert into mensagens (conversa_id, canal, direcao, autor, texto) "
                  "values (%s,'whatsapp','out','humano','tudo sim, e você?')", (conv,))
        c.commit()
    l = {x["id"]: x for x in ck.leads_do_vendedor(pool, conta, vend)}[lead]
    assert (l["sua_vez"], l["respondido"]) == (False, True)


def test_shell_leva_o_retorno_visual_de_espera():
    """O app é form + redirect: todo toque é uma navegação inteira. O que faltava era
    ele CONTAR isso. Estas peças precisam existir no shell de toda tela."""
    from web import painel_cockpit as pc
    html = pc._page("x", "<div>y</div>").body.decode()
    assert "id=zprog" in html                    # o Z que se desenha enquanto carrega
    assert "form.composer" in html               # o enviar otimista
    # o valor tem que migrar pro hidden ANTES de esvaziar o visível, senão o POST
    # vai com texto vazio — foi o erro que quase passou
    assert "hid.value=txt" in html and "campo.removeAttribute('name')" in html
    assert ".tabs a:active" in pc._CSS           # resposta ao toque, sem rede e sem JS


def test_aba_fila_mostra_o_numero_em_todas_as_telas():
    """O selo na aba é o que dá a contagem no ANDROID, onde setAppBadge não existe.
    Ele tem que estar em toda tela do vendedor — o vendedor está na Agenda e vê que
    a fila tem gente esperando, sem entrar."""
    from web import painel_cockpit as pc
    for tela in ("fila", "agenda", "orcamentos", "resultado", "perfil"):
        assert "class=tsel" in pc._abas_vend(tela, 3), tela
        assert ">3<" in pc._abas_vend(tela, 3)
    assert "class=tsel" not in pc._abas_vend("fila", 0)    # zerado: sem selo
    assert ">9+<" in pc._abas_vend("fila", 40)             # não estica a barra
    assert "class=tsel" not in pc._abas_dono("orcamentos")  # gestor não tem fila


def test_gestor_tem_como_sair_pela_barra():
    """O Sair do gestor existe em /cockpit/perfil desde sempre — mas só se chegava
    nele tocando nas INICIAIS DA EMPRESA num círculo sem rótulo. Na prática, quem
    entrava no app de gestão não achava como sair."""
    from web import painel_cockpit as pc
    barra = pc._abas_dono("visao")
    assert ">Perfil<" in barra
    assert f"href='{pc._BASE}/perfil'" in barra

    # e a aba certa acende: antes `_abas_dono("perfil")` não casava com nada da
    # lista, então a barra inteira ficava apagada
    assert pc._abas_dono("perfil").count("<a class=on") == 1
    for tela in ("visao", "placar", "leads", "orcamentos", "ativ", "perfil"):
        assert pc._abas_dono(tela).count("<a class=on") == 1, tela

    # o vendedor não herda a aba do gestor: são barras diferentes
    assert pc._abas_vend("fila").count("<a") == 5
    # 7 desde a agenda compartilhada: a aba Agenda entrou pro dono/gestor (medido
    # em 390px: ~55px por aba e o maior rótulo ocupa ~46px — cabe numa linha).
    assert pc._abas_dono("visao").count("<a") == 7


def test_total_pendentes_soma_a_carteira_e_ignora_fechados(pool):
    """O número da bolinha do ícone: soma de todas as conversas do vendedor."""
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="tot@x.com")
        outro = _membro(c, conta, nome="Outro", email="tot2@x.com")

        def conversa_com(dono, entradas, respondida=False, status="novo"):
            lead = _lead(c, conta, dono)
            if status != "novo":
                c.execute("update prospeccao set status=%s where id=%s", (status, lead))
            cv = c.execute("insert into conversas (conta_id, prospeccao_id, canal) "
                           "values (%s,%s,'whatsapp') returning id", (conta, lead)).fetchone()[0]
            for i in range(entradas):
                c.execute("insert into mensagens (conversa_id, canal, direcao, autor, texto) "
                          "values (%s,'whatsapp','in','lead',%s)", (cv, f"m{i}"))
            if respondida:
                c.execute("insert into mensagens (conversa_id, canal, direcao, autor, texto) "
                          "values (%s,'whatsapp','out','humano','ja respondi')", (cv,))
            return lead

        conversa_com(vend, 2)                       # +2
        conversa_com(vend, 3)                       # +3
        conversa_com(vend, 4, respondida=True)      # respondida: não soma
        conversa_com(vend, 9, status="ganho")       # fechado: sai da carteira
        conversa_com(outro, 7)                      # de outro vendedor
        c.commit()

    assert ck.total_pendentes(pool, conta, vend) == 5
    assert ck.total_pendentes(pool, conta, outro) == 7


def test_push_leva_o_numero_da_bolinha_por_vendedor(pool, monkeypatch):
    """Lead sem dono vai pra vários, e cada um tem a SUA carteira — o badge não pode
    ser um número só repetido pra todo mundo."""
    from finance import webpush
    monkeypatch.setattr(webpush, "configurado", lambda: True)
    with pool.connection() as c:
        conta = _conta(c)
        a = _membro(c, conta, nome="A", email="bdg1@x.com")
        b = _membro(c, conta, nome="B", email="bdg2@x.com")
        # endpoint é UNIQUE na tabela: cada teste precisa dos seus, senão um rouba a
        # assinatura do outro e a falha aparece longe daqui, na suíte inteira.
        _assina(c, conta, a, "https://push/bdg-a")
        _assina(c, conta, b, "https://push/bdg-b")
        # A já tem 3 sem resposta numa outra conversa; B não tem nada
        outro_lead = _lead(c, conta, a)
        cv2 = c.execute("insert into conversas (conta_id, prospeccao_id, canal) "
                        "values (%s,%s,'whatsapp') returning id", (conta, outro_lead)).fetchone()[0]
        for i in range(3):
            c.execute("insert into mensagens (conversa_id, canal, direcao, autor, texto) "
                      "values (%s,'whatsapp','in','lead',%s)", (cv2, f"x{i}"))
        # e o lead SEM DONO que acabou de falar
        orfao = _lead(c, conta, None, "Órfão")
        conv = _conversa(c, conta, orfao)
        c.execute("insert into mensagens (conversa_id, canal, direcao, autor, texto) "
                  "values (%s,'whatsapp','in','lead','oi')", (conv,))
        c.commit()
    por_endpoint = {}
    monkeypatch.setattr(webpush, "enviar",
                        lambda sub, dados, ttl=3600: (por_endpoint.__setitem__(
                            sub["endpoint"], dados.get("badge_n")), True)[1])

    assert ck.avisar_mensagem(pool, conta, orfao, conv, "oi") == 2
    assert por_endpoint["https://push/bdg-a"] == 3   # a carteira dele
    assert por_endpoint["https://push/bdg-b"] == 0   # a dele está limpa


def _conversa(c, conta, lead):
    return c.execute("insert into conversas (conta_id, prospeccao_id, canal) "
                     "values (%s,%s,'whatsapp') returning id", (conta, lead)).fetchone()[0]


def _assina(c, conta, membro, ep):
    c.execute("insert into push_assinaturas (conta_id, membro_id, endpoint, p256dh, auth) "
              "values (%s,%s,%s,'p','a')", (conta, membro, ep))


def test_avisar_mensagem_vai_pro_dono_com_link_da_conversa(pool, monkeypatch):
    """Antes deste push, a resposta do cliente era silêncio: o único gatilho no código
    era o rodízio atribuindo um lead novo."""
    from finance import webpush
    monkeypatch.setattr(webpush, "configurado", lambda: True)
    with pool.connection() as c:
        conta = _conta(c)
        dono = _membro(c, conta, nome="Dono", email="am1@x.com")
        outro = _membro(c, conta, nome="Outro", email="am2@x.com")
        lead = _lead(c, conta, dono, "Bruna")
        conv = _conversa(c, conta, lead)
        _assina(c, conta, dono, "https://push/dono")
        _assina(c, conta, outro, "https://push/outro")
        c.commit()
    saiu = []
    monkeypatch.setattr(webpush, "enviar",
                        lambda sub, dados, ttl=3600: (saiu.append((sub["endpoint"], dados)), True)[1])

    assert ck.avisar_mensagem(pool, conta, lead, conv, "Quais os valores") == 1
    assert [e for e, _ in saiu] == ["https://push/dono"]         # só o dono, não a conta toda
    d = saiu[0][1]
    assert d["title"] == "Bruna" and d["body"] == "Quais os valores"
    assert d["url"] == f"/cockpit/lead/{lead}"                    # abre a CONVERSA, não a fila


def test_avisar_mensagem_sem_dono_vai_pra_quem_assinou(pool, monkeypatch):
    """O caso da Prime Eventos: rodízio desligado, lead sem dono. Antes, ninguém era
    avisado — agora avisa quem instalou o app e aceitou notificação."""
    from finance import webpush
    monkeypatch.setattr(webpush, "configurado", lambda: True)
    with pool.connection() as c:
        conta = _conta(c)
        a = _membro(c, conta, nome="A", email="sd1@x.com")
        b = _membro(c, conta, nome="B", email="sd2@x.com")
        pausado = _membro(c, conta, nome="P", email="sd3@x.com")
        _membro(c, conta, nome="SemApp", email="sd4@x.com")       # sem assinatura: fica fora
        lead = _lead(c, conta, None, "Valeria")
        conv = _conversa(c, conta, lead)
        for m, ep in ((a, "https://push/a"), (b, "https://push/b"), (pausado, "https://push/p")):
            _assina(c, conta, m, ep)
        c.commit()
    ck.set_pausado(pool, conta, pausado, True)
    saiu = []
    monkeypatch.setattr(webpush, "enviar",
                        lambda sub, dados, ttl=3600: (saiu.append(sub["endpoint"]), True)[1])

    assert ck.avisar_mensagem(pool, conta, lead, conv, "Olá, quero informações") == 2
    assert sorted(saiu) == ["https://push/a", "https://push/b"]   # o pausado fica de fora


def test_avisar_mensagem_rajada_gera_um_aviso_so(pool, monkeypatch):
    """'Boa tarde' / '08/05/27' / 'Debutante' / '19 h' em 40 segundos é o padrão real.
    Quatro notificações por isso e o vendedor desliga o push."""
    from finance import webpush
    monkeypatch.setattr(webpush, "configurado", lambda: True)
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="raj@x.com")
        lead = _lead(c, conta, vend, "Sonja")
        conv = _conversa(c, conta, lead)
        _assina(c, conta, vend, "https://push/raj")
        c.commit()
    n = []
    monkeypatch.setattr(webpush, "enviar", lambda sub, dados, ttl=3600: (n.append(1), True)[1])

    saidas = [ck.avisar_mensagem(pool, conta, lead, conv, t)
              for t in ("Boa tarde", "08/05/27", "Debutante", "19 h")]
    assert saidas == [1, 0, 0, 0] and len(n) == 1

    # passado o cooldown, volta a avisar — não é um silêncio permanente
    with pool.connection() as c:
        c.execute("update conversas set push_avisado_em = now() - interval '%s minutes' "
                  "where id=%s" % (ck.PUSH_COOLDOWN_MIN + 1, conv))
        c.commit()
    assert ck.avisar_mensagem(pool, conta, lead, conv, "Ainda tem vaga?") == 1


def test_cooldown_zera_quando_o_vendedor_abre_a_conversa(pool, monkeypatch):
    """O caso real: cliente manda, push toca, vendedor ABRE e lê. Cliente escreve de
    novo 2 min depois — tem que tocar. Antes ficava mudo até fechar os 10 min, ou
    seja, quem atendia rápido tinha MAIS chance de perder a mensagem seguinte."""
    from finance import webpush
    monkeypatch.setattr(webpush, "configurado", lambda: True)
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="visto@x.com")
        lead = _lead(c, conta, vend, "Bruna")
        conv = _conversa(c, conta, lead)
        _assina(c, conta, vend, "https://push/visto")
        c.commit()
    n = []
    monkeypatch.setattr(webpush, "enviar", lambda sub, dados, ttl=3600: (n.append(1), True)[1])

    assert ck.avisar_mensagem(pool, conta, lead, conv, "Boa tarde") == 1
    assert ck.avisar_mensagem(pool, conta, lead, conv, "e aí?") == 0      # cooldown

    ck.lead_do_vendedor(pool, conta, vend, lead, pos_visto=True)          # abriu e leu
    with pool.connection() as c:
        assert c.execute("select push_avisado_em from conversas where id=%s",
                         (conv,)).fetchone()[0] is None
    assert ck.avisar_mensagem(pool, conta, lead, conv, "manda por favor") == 1
    assert len(n) == 2


def test_polling_nao_zera_o_cooldown(pool, monkeypatch):
    """O polling de 8s da conversa aberta chama o mesmo lead_do_vendedor. Se ele
    zerasse o cooldown, a janela nunca fecharia e a rajada voltaria a virar rajada
    de notificação — que é justamente o que o cooldown existe pra impedir."""
    from finance import webpush
    monkeypatch.setattr(webpush, "configurado", lambda: True)
    monkeypatch.setattr(webpush, "enviar", lambda sub, dados, ttl=3600: True)
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="poll@x.com")
        lead = _lead(c, conta, vend, "Sonja")
        conv = _conversa(c, conta, lead)
        _assina(c, conta, vend, "https://push/poll")
        c.commit()

    assert ck.avisar_mensagem(pool, conta, lead, conv, "oi") == 1
    ck.lead_do_vendedor(pool, conta, vend, lead)          # sem pos_visto = é o polling
    with pool.connection() as c:
        assert c.execute("select push_avisado_em from conversas where id=%s",
                         (conv,)).fetchone()[0] is not None
    assert ck.avisar_mensagem(pool, conta, lead, conv, "?") == 0          # segue calado


def test_cooldown_zera_quando_o_vendedor_responde(pool, monkeypatch):
    """Responder é a forma mais forte de estar em dia."""
    from finance import webpush, whatsapp_out
    from web import painel_prospeccao as pp
    monkeypatch.setattr(webpush, "configurado", lambda: True)
    monkeypatch.setattr(webpush, "enviar", lambda sub, dados, ttl=3600: True)
    monkeypatch.setattr(whatsapp_out, "enviar",
                        lambda c, ci, num, txt, *, chip_id=None: {"ok": True, "sid": "SM1"})
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="resp@x.com")
        lead = _lead(c, conta, vend, "Valeria")
        c.execute("update prospeccao set whatsapp='558611112222' where id=%s", (lead,))
        conv = _conversa(c, conta, lead)
        _assina(c, conta, vend, "https://push/resp")
        c.commit()
    monkeypatch.setattr(pp, "_conversa_id", lambda c, ci, li, canal: conv)

    assert ck.avisar_mensagem(pool, conta, lead, conv, "Quero orçamento") == 1
    assert ck.enviar_mensagem(pool, conta, vend, lead, "Já te mando")["ok"] is True
    with pool.connection() as c:
        assert c.execute("select push_avisado_em from conversas where id=%s",
                         (conv,)).fetchone()[0] is None
    assert ck.avisar_mensagem(pool, conta, lead, conv, "manda por favor") == 1


def test_avisar_mensagem_texto_vazio_e_sem_vapid(pool, monkeypatch):
    from finance import webpush
    with pool.connection() as c:
        conta = _conta(c); vend = _membro(c, conta, email="vz@x.com")
        lead = _lead(c, conta, vend); conv = _conversa(c, conta, lead)
        _assina(c, conta, vend, "https://push/vz")
        c.commit()
    monkeypatch.setattr(webpush, "configurado", lambda: True)
    assert ck.avisar_mensagem(pool, conta, lead, conv, "   ") == 0    # áudio/mídia sem texto
    monkeypatch.setattr(webpush, "configurado", lambda: False)
    assert ck.avisar_mensagem(pool, conta, lead, conv, "oi") == 0     # ambiente sem VAPID
    with pool.connection() as c:   # e não queimou o cooldown à toa
        assert c.execute("select push_avisado_em from conversas where id=%s", (conv,)).fetchone()[0] is None


def test_catalogo_servicos_em_reais(pool):
    with pool.connection() as c:
        conta = _conta(c)
        c.execute("insert into servicos_catalogo (conta_id, slug, nome, setup_centavos, mensal_centavos) "
                  "values (%s,'a','Consultoria',0,49000)", (conta,))
        c.execute("insert into servicos_catalogo (conta_id, slug, nome, setup_centavos, mensal_centavos, ativo) "
                  "values (%s,'b','Inativo',10000,0,false)", (conta,))
        c.commit()
    cat = ck.catalogo_servicos(pool, conta)
    assert [s["nome"] for s in cat] == ["Consultoria"]           # só ativo
    assert cat[0]["mensal"] == 490 and cat[0]["setup"] == 0      # centavos → reais


def test_criar_orcamento_gera_proposta(pool, monkeypatch):
    monkeypatch.setenv("APP_URL", "https://app.zaq-ia.com")
    with pool.connection() as c:
        conta = _conta(c)
        v1 = _membro(c, conta, email="o1@x.com")
        v2 = _membro(c, conta, nome="V2", email="o2@x.com")
        meu = _lead(c, conta, v1, "Salão Alfa", wa="5586991112222")
        alheio = _lead(c, conta, v2, "Outro")
        c.commit()
    itens = [{"nome": "Locação do espaço", "setup": 3000, "mensal": 0},
             {"nome": "Buffet", "setup": 5000, "mensal": 0}]
    assert ck.criar_orcamento(pool, conta, v1, alheio, itens)["ok"] is False   # não é dele
    assert ck.criar_orcamento(pool, conta, v1, meu, [])["ok"] is False          # sem itens
    r = ck.criar_orcamento(pool, conta, v1, meu, itens)
    assert r["ok"] and r["setup_centavos"] == 800000 and r["token"]
    assert r["link"] == f"https://app.zaq-ia.com/proposta/{r['token']}"
    assert "wa.me" in r["zap"]                                                  # link pronto pro WhatsApp
    with pool.connection() as c:
        row = c.execute("select conta_id, empresa, token, setup_centavos, status, canal from orcamentos where id=%s",
                        (r["id"],)).fetchone()
        assert row[0] == conta and row[1] == "Salão Alfa" and row[3] == 800000 and row[5] == "cockpit"
        assert c.execute("select orcamento_id from prospeccao where id=%s", (meu,)).fetchone()[0] == r["id"]


def test_endereco_empresa_do_cadastro(pool):
    with pool.connection() as c:
        conta = c.execute("insert into contas (nome, nome_fantasia, endereco, bairro, cidade, uf) "
                          "values ('C','Prime Eventus','Av. Fátima, 1200','Jóquei','Teresina','PI') returning id").fetchone()[0]
        c.commit()
    e = ck.endereco_empresa(pool, conta)
    assert e["nome"] == "Prime Eventus"
    assert e["endereco"] == "Av. Fátima, 1200 — Jóquei — Teresina PI"
    assert "google.com/maps" in e["maps"]


def test_agendar_visita(pool):
    with pool.connection() as c:
        conta = c.execute("insert into contas (nome, nome_fantasia, endereco, cidade, uf) "
                          "values ('C','Prime Eventus','Av. Fátima, 1200','Teresina','PI') returning id").fetchone()[0]
        v1 = _membro(c, conta, email="vi1@x.com")
        v2 = _membro(c, conta, nome="V2", email="vi2@x.com")
        meu = _lead(c, conta, v1, "Ana & Léo")
        c.execute("update prospeccao set contato='Ana' where id=%s", (meu,))
        alheio = _lead(c, conta, v2, "Outro")
        c.commit()
    # posse + data inválida
    assert ck.agendar_visita(pool, conta, v1, alheio, data="2026-09-01", hora="10:00")["ok"] is False
    assert ck.agendar_visita(pool, conta, v1, meu, data="xx", hora="10:00")["ok"] is False
    r = ck.agendar_visita(pool, conta, v1, meu, data="2026-09-01", hora="10:00", dur_min=60,
                          lembrete_min=60, avisar_cliente=False)
    assert r["ok"] and "01/09 às 10:00" in r["quando"] and r["empresa"] == "Prime Eventus"
    assert r["ics_url"].endswith(".ics")
    with pool.connection() as c:
        ev = c.execute("select titulo, local, prospeccao_id, ics_token, tipo from eventos_agenda where id=%s",
                       (r["evento_id"],)).fetchone()
        assert ev[0] == "Visita — Ana" and ev[2] == meu and ev[3] and ev[4] == "empresa"
        assert "Av. Fátima" in (ev[1] or "")
        # lead avançou pra qualificado + atividade na timeline
        assert c.execute("select status from prospeccao where id=%s", (meu,)).fetchone()[0] == "qualificado"
        assert c.execute("select count(*) from prospeccao_atividades where prospeccao_id=%s and tipo='visita'",
                         (meu,)).fetchone()[0] == 1
    # .ics público com VALARM (lembrete do cliente)
    ics = ck.visita_ics(pool, r["ics_url"].rsplit("/", 1)[1].replace(".ics", ""))
    assert ics and "BEGIN:VEVENT" in ics and "BEGIN:VALARM" in ics and "Visita — Ana" in ics


# ------------------------------------------------------------------ a Fila no mês atual
# (mockup cockpit_mes_atual): o período, as pílulas de fora, os grupos por o que o lead
# pede, a linha do evento no card, e "perguntar"/"confirmar" num toque.
def _conv_msg(c, conta, lead, *, ia=False, direcao="in", texto="oi", ha_dias=0):
    conv = c.execute("insert into conversas (conta_id, prospeccao_id, canal, agente_ativo) "
                     "values (%s,%s,'whatsapp',%s) returning id", (conta, lead, ia)).fetchone()[0]
    c.execute("insert into mensagens (conversa_id, direcao, texto, criado_em) "
              "values (%s,%s,%s, now() - %s * interval '1 day')", (conv, direcao, texto, ha_dias))
    c.execute("update conversas set ultima_msg_em = now() - %s * interval '1 day' where id=%s", (ha_dias, conv))
    return conv


def test_a_fila_traz_o_evento_e_o_periodo_de_cada_lead(pool):
    from datetime import date
    with pool.connection() as c:
        conta = _conta(c)
        vend = _membro(c, conta, nome="Jaque", email="jaque@x.com")
        lid = _lead(c, conta, vend, "Larissa")
        c.execute("update prospeccao set evento_em=%s, evento_tipo='Casamento', evento_convidados=70, "
                  "evento_origem='conversa', evento_pista=null where id=%s", (date(2027, 2, 13), lid))
        _conv_msg(c, conta, lid, texto="Média de 70 pessoas")
        c.commit()
    l = ck.leads_do_vendedor(pool, conta, vend)[0]
    assert (l["evento_em"], l["evento_tipo"], l["evento_convidados"], l["evento_origem"]) == (
        date(2027, 2, 13), "Casamento", 70, "conversa")
    assert l["ev_ic"] == "💍" and l["ev_data"] == "13 fev 27"
    assert l["criado_em"] is not None and l["esperando"] == 1 and l["sua_vez"] is True


def test_fila_agrupada_por_o_que_o_lead_pede():
    from datetime import date, datetime, timedelta, timezone
    ag = datetime(2026, 9, 5, 12, tzinfo=timezone.utc)

    def L(i, *, dias=1, ia=False, esperando=0, evento=None, ult=None):
        return {"id": i, "ia": ia, "esperando": esperando, "evento_em": evento,
                "criado_em": ag - timedelta(days=dias), "ult_em": ult}

    leads = [L(1, esperando=2),                                   # sua vez, deste mês
             L(2, evento=date(2026, 12, 19)),                       # festa marcada
             L(3, evento=date(2026, 11, 14)),                       # festa marcada, antes
             L(4),                                                  # sem data
             L(5, dias=40, ult=ag - timedelta(days=20)),            # julho, parado — fora
             L(6, dias=40, esperando=1, ult=ag),                    # julho, sua vez — fora
             L(7, dias=40, evento=ag.date() + timedelta(days=10)),  # julho, festa em 30d — fora
             L(8, esperando=3, ia=True)]                            # o agente atende: não é "sua vez"
    f = ck.fila_agrupada(leads, entrou="2026-09", fora_on=[], agora=ag)
    assert [(g["chave"], [l["id"] for l in g["leads"]]) for g in f["grupos"]] == [
        ("vez", [1]), ("festa", [3, 2]), ("sem", [4, 8])]
    assert f["fora_cont"] == {"suavez": 1, "festa30": 1} and f["n_quadro"] == 5 and f["total"] == 8
    assert [(m["chave"], m["n"], m["on"], m["curto"]) for m in f["meses"]] == [
        ("2026-09", 5, True, "Set"), ("2026-07", 3, False, "Jul"), ("tudo", 8, False, "Tudo")]
    # liga "sua vez" e "30 dias": os de julho entram, marcados
    f2 = ck.fila_agrupada(leads, entrou="2026-09", fora_on=["suavez", "festa30"], agora=ag)
    assert [(g["chave"], [l["id"] for l in g["leads"]]) for g in f2["grupos"]] == [
        ("vez", [1, 6]), ("festa", [7, 3, 2]), ("sem", [4, 8])]
    assert all(l["fora"] for l in leads if l["id"] in (6, 7)) and leads[5]["entrou_rot"] == "jul"
    # tudo: julho inteiro, com o parado na dobra
    f3 = ck.fila_agrupada(leads, entrou="tudo", fora_on=[], agora=ag)
    assert f3["grupos"][-1]["chave"] == "parados" and f3["grupos"][-1]["dobra"] is True
    assert [l["id"] for l in f3["grupos"][-1]["leads"]] == [5]


def _fila_html(monkeypatch, pool, conta, vend, req=None, **kw):
    from types import SimpleNamespace
    from starlette.datastructures import QueryParams
    from web import painel_cockpit as pc
    import finance.vendas as v
    from finance import webpush
    monkeypatch.setattr(pc, "get_pool", lambda: pool)
    monkeypatch.setattr(pc, "_selo", lambda conta_id: "")
    monkeypatch.setattr(v, "vende_data", lambda pool, conta_id: True)
    monkeypatch.setattr(webpush, "chave_publica", lambda: None)
    req = req or SimpleNamespace(session={}, query_params=QueryParams(""))
    r = pc._fila(req, conta, vend, **kw)
    return bytes(r.body).decode("utf-8"), req


def test_a_fila_abre_no_mes_atual_com_pilulas_grupos_e_a_linha_do_evento(pool, monkeypatch):
    from datetime import date
    with pool.connection() as c:
        conta = _conta(c)
        vend = _membro(c, conta, nome="Jaque", email="jaque2@x.com")
        a = _lead(c, conta, vend, "Deste Mes")
        c.execute("update prospeccao set evento_em=%s, evento_tipo='Casamento', evento_convidados=70, "
                  "evento_origem='conversa' where id=%s", (date(2027, 2, 13), a))
        _conv_msg(c, conta, a, texto="Média de 70 pessoas")                      # sua vez
        b = _lead(c, conta, vend, "Do Mes Passado")
        c.execute("update prospeccao set criado_em = now() - interval '40 days' where id=%s", (b,))
        _conv_msg(c, conta, b, texto="Vai dar certo?")                           # sua vez, fora
        s_ = _lead(c, conta, vend, "Sem Data")
        p_ = _lead(c, conta, vend, "So Pista")
        c.execute("update prospeccao set evento_pista='falou de março' where id=%s", (p_,))
        c.commit()
    html, req = _fila_html(monkeypatch, pool, conta, vend)
    assert "Deste Mes" in html and "Sem Data" in html and "Do Mes Passado" not in html
    assert "3 de 4 · " in html and "🟢 sua vez <b>1</b>" in html          # o subtítulo e a pílula de fora
    assert "🟢 Sua vez <b>1</b>" in html and "📅 Sem data <b>2</b>" in html
    assert "💍 <b>Casamento</b> · <i class=d>13 fev 27</i> · 70 conv. <em class=src>💬 lido</em>" in html
    assert f"data-href='/cockpit/lead/{s_}?texto=Pra%20qual%20data" in html and ">perguntar</span>" in html
    assert f"data-href='/cockpit/lead/{p_}?pista=1'>confirmar</span>" in html and "📅 falou de março" in html
    assert "class='pil on' href='/cockpit?entrou=" in html and "Tudo <b>4</b>" in html
    # liga a pílula: o de agosto entra, marcado com o mês; e a escolha fica na sessão
    html2, _ = _fila_html(monkeypatch, pool, conta, vend, req=req, fora="suavez")
    assert "Do Mes Passado" in html2 and "<span class='chip entrou'>📥 " in html2 and "class='lead front fora'" in html2
    assert "🟢 Sua vez <b>2</b>" in html2 and req.session["ck_fora"] == "suavez"
    html3, _ = _fila_html(monkeypatch, pool, conta, vend, req=req)
    assert "Do Mes Passado" in html3                                            # sem parâmetro: vale a sessão
    html4, _ = _fila_html(monkeypatch, pool, conta, vend, req=req, entrou="tudo")
    assert "4 abertos · " in html4 and "sua vez <b>" not in html4.split("class=scroll")[0].split("class=foco")[1]


def test_a_tela_do_lead_preenche_a_caixa_e_avisa_a_pista(pool, monkeypatch):
    from types import SimpleNamespace
    from starlette.datastructures import QueryParams
    from web import painel_cockpit as pc
    with pool.connection() as c:
        conta = _conta(c)
        vend = _membro(c, conta, nome="Jaque", email="jaque3@x.com")
        lid = _lead(c, conta, vend, "Turma Enfermagem")
        c.execute("update prospeccao set evento_tipo='Formatura', evento_pista='falou de março' where id=%s", (lid,))
        _conv_msg(c, conta, lid, texto="a data seria mais ou menos março")
        c.commit()
    monkeypatch.setattr(pc, "get_pool", lambda: pool)
    d = ck.lead_do_vendedor(pool, conta, vend, lid)
    req = SimpleNamespace(session={}, query_params=QueryParams("texto=Pra%20qual%20data%20voc%C3%AA%20est%C3%A1%20pensando%3F"))
    html = bytes(pc._lead_vendedor(req, lid, d).body).decode("utf-8")
    assert "value='Pra qual data você está pensando?' autofocus" in html
    assert "O cliente <b>falou de março</b> na conversa. Confirmar a data?" in html
    assert f"href='/cockpit/lead/{lid}/ficha'>Abrir a ficha</a>" in html
    assert f"href='/cockpit/lead/{lid}?texto=Pra%20qual%20data" in html


# ═══════════════════════════════════════ as Novidades no app (migração 199)

def _aviso_vend(c, chave, *, pra_quem=("vendedor",), tipo="novidade", resumo="Uma linha.",
                link="/cockpit"):
    c.execute("""insert into novidades (chave, tipo, publico, pra_quem, titulo, corpo, resumo, link,
                                        publicado_em)
                 values (%s,%s,'todos',%s,%s,'O corpo do aviso, com detalhes.',%s,%s,
                         clock_timestamp())
                 on conflict (chave) do nothing""",
              (chave, tipo, list(pra_quem), "T " + chave, resumo, link))
    return c.execute("select id from novidades where chave=%s", (chave,)).fetchone()[0]


def _req_vend(conta, vend):
    from types import SimpleNamespace
    from starlette.datastructures import QueryParams
    return SimpleNamespace(session={"conta_id": conta, "membro_id": vend, "papel": "vendedor"},
                           query_params=QueryParams(""), cookies={})


def _lidas_de(pool, vend):
    with pool.connection() as c:
        return {r[0] for r in c.execute("""select n.chave from novidade_lida l
                                            join novidades n on n.id = l.novidade_id
                                           where l.membro_id=%s""", (vend,)).fetchall()}


def test_a_faixa_na_fila_e_so_do_aviso_do_vendedor_por_ler(pool, monkeypatch):
    """A faixa mostra UM aviso, o mais novo que ele não leu, e só se for pra
    vendedor. O do dono não aparece na Fila dele. O selo verde vai na aba Perfil."""
    with pool.connection() as c:
        conta = _conta(c, "Nov1")
        vend = _membro(c, conta, nome="Jaque", email="nv1@x.com")
        _lead(c, conta, vend, "Alguem")
        _aviso_vend(c, "nv1-vend", resumo="A Fila abre no mês.")
        _aviso_vend(c, "nv1-dono", pra_quem=("dono", "gestor"))
        c.commit()
    html, _ = _fila_html(monkeypatch, pool, conta, vend)
    assert "class=faixa" in html and "T nv1-vend" in html and "A Fila abre no mês." in html
    assert "T nv1-dono" not in html
    assert "class='tsel ok' aria-label='1 por ler'" in html
    # ninguém marcou nada só por ver a faixa
    assert _lidas_de(pool, vend) == set()


def test_abrir_o_aviso_marca_lida_e_a_faixa_some(pool, monkeypatch):
    from web import painel_cockpit as pc
    with pool.connection() as c:
        conta = _conta(c, "Nov2")
        vend = _membro(c, conta, nome="Jaque", email="nv2@x.com")
        _lead(c, conta, vend, "Alguem")
        nid = _aviso_vend(c, "nv2-vend")
        c.commit()
    monkeypatch.setattr(pc, "get_pool", lambda: pool)
    r = pc.cockpit_novidade(_req_vend(conta, vend), nid)
    html = bytes(r.body).decode("utf-8")
    assert "O corpo do aviso, com detalhes." in html and "Ver a Fila" in html
    assert "Entendi" not in html            # novidade: abrir já basta
    assert _lidas_de(pool, vend) == {"nv2-vend"}
    fila, _ = _fila_html(monkeypatch, pool, conta, vend)
    assert "class=faixa" not in fila and "tsel ok" not in fila


def test_o_x_da_faixa_marca_lida_sem_abrir_e_volta_pra_fila(pool, monkeypatch):
    from web import painel_cockpit as pc
    with pool.connection() as c:
        conta = _conta(c, "Nov3")
        vend = _membro(c, conta, nome="Jaque", email="nv3@x.com")
        nid = _aviso_vend(c, "nv3-vend")
        c.commit()
    monkeypatch.setattr(pc, "get_pool", lambda: pool)
    r = pc.cockpit_novidade_lida(_req_vend(conta, vend), nid, volta="fila")
    assert r.status_code == 303 and r.headers["location"] == "/cockpit"
    assert _lidas_de(pool, vend) == {"nv3-vend"}
    r = pc.cockpit_novidade_lida(_req_vend(conta, vend), nid, volta="perfil")
    assert r.headers["location"] == "/cockpit/perfil"


def test_a_mudanca_espera_o_entendi(pool, monkeypatch):
    from web import painel_cockpit as pc
    with pool.connection() as c:
        conta = _conta(c, "Nov4")
        vend = _membro(c, conta, nome="Jaque", email="nv4@x.com")
        nid = _aviso_vend(c, "nv4-mud", tipo="mudanca", link="/cockpit/agenda")
        c.commit()
    monkeypatch.setattr(pc, "get_pool", lambda: pool)
    html = bytes(pc.cockpit_novidade(_req_vend(conta, vend), nid).body).decode("utf-8")
    assert ">Entendi</button>" in html and "mudança ·" in html
    assert "href='/cockpit/agenda'>Ver como ficou" in html
    assert _lidas_de(pool, vend) == set()
    pc.cockpit_novidade_lida(_req_vend(conta, vend), nid, volta="perfil")
    assert _lidas_de(pool, vend) == {"nv4-mud"}
    html = bytes(pc.cockpit_novidade(_req_vend(conta, vend), nid).body).decode("utf-8")
    assert ">Entendi</button>" not in html


def test_o_perfil_lista_os_avisos_com_o_lido_apagado(pool, monkeypatch):
    from web import painel_cockpit as pc
    from finance import novidades as nv
    with pool.connection() as c:
        conta = _conta(c, "Nov5")
        vend = _membro(c, conta, nome="Jaque", email="nv5@x.com")
        a = _aviso_vend(c, "nv5-a", resumo="Primeiro.")
        _aviso_vend(c, "nv5-b", resumo="Segundo.")
        c.commit()
    nv.marcar_lida(pool, a, conta, vend)
    monkeypatch.setattr(pc, "get_pool", lambda: pool)
    html = bytes(pc._perfil_vendedor(_req_vend(conta, vend), conta, vend).body).decode("utf-8")
    assert "<div class=eyebrow>Novidades</div>" in html
    assert f"class='nvc lida' href='/cockpit/novidades/{a}'" in html
    assert "class='nvc nova'" in html and "Segundo." in html
    assert "class='tsel ok' aria-label='1 por ler'" in html


def test_sem_aviso_o_perfil_nao_tem_a_secao(pool, monkeypatch):
    from web import painel_cockpit as pc
    with pool.connection() as c:
        conta = _conta(c, "Nov6")
        vend = _membro(c, conta, nome="Jaque", email="nv6@x.com")
        c.commit()
    monkeypatch.setattr(pc, "get_pool", lambda: pool)
    html = bytes(pc._perfil_vendedor(_req_vend(conta, vend), conta, vend).body).decode("utf-8")
    assert "<div class=eyebrow>Novidades</div>" not in html and "tsel ok" not in html


def test_aviso_de_outro_publico_nao_abre_nem_marca(pool, monkeypatch):
    from web import painel_cockpit as pc
    with pool.connection() as c:
        conta = _conta(c, "Nov7")
        vend = _membro(c, conta, nome="Jaque", email="nv7@x.com")
        nid = _aviso_vend(c, "nv7-dono", pra_quem=("dono",))
        c.commit()
    monkeypatch.setattr(pc, "get_pool", lambda: pool)
    html = bytes(pc.cockpit_novidade(_req_vend(conta, vend), nid).body).decode("utf-8")
    assert "não é seu" in html and "O corpo do aviso" not in html
    pc.cockpit_novidade_lida(_req_vend(conta, vend), nid, volta="perfil")
    assert _lidas_de(pool, vend) == set()


def test_o_gestor_vendo_a_equipe_nao_ganha_a_faixa_do_vendedor(pool, monkeypatch):
    with pool.connection() as c:
        conta = _conta(c, "Nov8")
        vend = _membro(c, conta, nome="Jaque", email="nv8@x.com")
        _aviso_vend(c, "nv8-vend")
        c.commit()
    html, _ = _fila_html(monkeypatch, pool, conta, vend, gestor=True)
    assert "class=faixa" not in html


def test_a_faixa_nao_derruba_a_fila_sem_a_tabela(pool, monkeypatch):
    from web import painel_cockpit as pc
    from finance import novidades as nv
    with pool.connection() as c:
        conta = _conta(c, "Nov9")
        vend = _membro(c, conta, nome="Jaque", email="nv9@x.com")
        c.commit()

    def _boom(*a, **k):
        raise RuntimeError("sem tabela")
    monkeypatch.setattr(nv, "listar", _boom)
    html, _ = _fila_html(monkeypatch, pool, conta, vend)
    assert "Meus leads" in html and "class=faixa" not in html


# ------------------------------------------------------------------ Raio-X (a aba Resultado virou Raio-X)
def _msg_rx(c, conv, direcao, texto, horas_atras, autor=None):
    c.execute("""insert into mensagens (conversa_id, direcao, autor, texto, criado_em)
                 values (%s,%s,%s,%s, now() - make_interval(hours => %s))""",
              (conv, direcao, autor or ("lead" if direcao == "in" else "humano"), texto, horas_atras))


def test_resultado_redireciona_pro_raio_x_com_o_mesmo_periodo():
    """Quem tem o app aberto na aba antiga, ou um link guardado, cai na tela nova."""
    from types import SimpleNamespace
    from starlette.datastructures import QueryParams
    from web import painel_cockpit as pc
    r = pc.cockpit_resultado(SimpleNamespace(query_params=QueryParams("p=mes")))
    assert r.status_code == 303 and r.headers["location"] == "/cockpit/raio-x?p=mes"
    r = pc.cockpit_resultado(SimpleNamespace(query_params=QueryParams("p=xyz")))
    assert r.headers["location"] == "/cockpit/raio-x"


def test_aba_resultado_se_chama_raio_x_e_leva_o_selo_do_responda_hoje():
    from web import painel_cockpit as pc
    html = pc._abas_vend("fila", 0, 0, 3)
    assert "Raio-X" in html and "href='/cockpit/raio-x'" in html and "Resultado" not in html
    assert "class='tsel ok' aria-label='3 por ler'" in html      # verde: é lista de trabalho, não alerta
    assert "tsel" not in pc._abas_vend("fila", 0, 0, 0)


def test_raio_x_mostra_os_quatro_blocos_e_a_fila_de_hoje_do_vendedor(pool, monkeypatch):
    """Um lead perguntou há 2h e não foi respondido: aparece em "Responda hoje",
    com o texto e o link da conversa; a semana, os fechamentos e a confiança do
    dado estão na tela. O bloco do dinheiro continua no fim."""
    from web import painel_cockpit as pc
    with pool.connection() as c:
        conta = _conta(c, "RX1")
        vend = _membro(c, conta, nome="Jaque", email="rx1@x.com")
        outro = _membro(c, conta, nome="Pedro", email="rx1b@x.com")
        lid = _lead(c, conta, vend, "Doceria Flor")
        conv = c.execute("insert into conversas (conta_id, prospeccao_id, canal) values (%s,%s,'whatsapp') returning id",
                         (conta, lid)).fetchone()[0]
        _msg_rx(c, conv, "out", "Oi! Posso ajudar?", 30)
        _msg_rx(c, conv, "in", "Quanto fica pra 80 pessoas?", 2)
        # o lead do OUTRO vendedor não entra na tela deste
        lid2 = _lead(c, conta, outro, "Buffet Alheio")
        conv2 = c.execute("insert into conversas (conta_id, prospeccao_id, canal) values (%s,%s,'whatsapp') returning id",
                          (conta, lid2)).fetchone()[0]
        _msg_rx(c, conv2, "in", "e aí?", 1)
        c.commit()
    monkeypatch.setattr(pc, "get_pool", lambda: pool)
    monkeypatch.setattr(pc, "_selo", lambda conta_id: "")
    # o bloco do dinheiro é o de sempre (finance.cockpit.remuneracao lê títulos e
    # lançamentos, que este schema não tem): entra pronto
    monkeypatch.setattr(pc.ck, "remuneracao", lambda pool, conta_id, membro_id, periodo: {
        "comissao_centavos": 12000, "comissao_pct": 10, "recebido_centavos": 120000, "n_vendas": 1,
        "fechado_centavos": 500000, "ganhos": 2, "conversao": "50%", "fila": 1, "resp": "2h",
        "posicao": 1, "total_equipe": 2})
    html = bytes(pc.cockpit_raio_x(_req_vend(conta, vend)).body).decode("utf-8")
    assert "Sua semana" in html and "Responda hoje" in html and "Fechamentos, a um passo" in html
    assert "Confiança do dado" in html and "dias medidos" in html
    assert "🔴 Pergunta sem resposta" in html
    assert "Doceria Flor" in html and "Quanto fica pra 80 pessoas?" in html and f"href='/cockpit/lead/{lid}'" in html
    assert "Buffet Alheio" not in html
    assert "Seu dinheiro" in html                 # o que a aba Resultado mostrava, no fim
    assert "class='tsel ok' aria-label='1 por ler'" in html      # a aba Raio-X com o 1 de hoje
    assert "href='/cockpit/raio-x?p=mes'" in html # o seletor de período
    # ninguém esperando: a fila diz isso, sem quebrar
    html2 = bytes(pc.cockpit_raio_x(_req_vend(conta, outro)).body).decode("utf-8")
    assert "Buffet Alheio" in html2 and "Doceria Flor" not in html2


def test_raio_x_sem_sessao_manda_pro_login(monkeypatch):
    from types import SimpleNamespace
    from starlette.datastructures import QueryParams
    from web import painel_cockpit as pc
    r = pc.cockpit_raio_x(SimpleNamespace(session={}, query_params=QueryParams(""), cookies={}))
    assert r.status_code == 303 and r.headers["location"] == "/cockpit/login"


def test_fila_leva_o_selo_do_raio_x_so_pro_vendedor(pool, monkeypatch):
    """O selo verde da aba Raio-X é o "responda hoje" do vendedor. O gestor não
    tem Raio-X próprio (não tem leads): a aba dele vai sem número."""
    with pool.connection() as c:
        conta = _conta(c, "RX2")
        vend = _membro(c, conta, nome="Jaque", email="rx2@x.com")
        lid = _lead(c, conta, vend, "Padoca")
        conv = c.execute("insert into conversas (conta_id, prospeccao_id, canal) values (%s,%s,'whatsapp') returning id",
                         (conta, lid)).fetchone()[0]
        _msg_rx(c, conv, "in", "tem pra sábado?", 3)
        c.commit()
    html, _ = _fila_html(monkeypatch, pool, conta, vend)
    assert "href='/cockpit/raio-x'" in html and "aria-label='1 por ler'" in html
