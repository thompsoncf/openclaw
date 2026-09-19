"""A CARTEIRA DE APÓLICES e a régua de renovação (migração 278).

As quatro decisões do dono em 17/09/2026 — "60/30/15, percentual por seguradora,
alerta pro corretor, começa por auto" — são regras de negócio, não preferências de
tela. Cada uma tem teste aqui, e é isto que este arquivo existe pra impedir de
voltar:

* `test_a_carteira_recem_carregada_nao_fica_calada` — o caso REAL da Liberal, que
  vai digitar a carteira inteira de uma vez, com apólices já vencendo em 20 dias.
  Um `degrau_de` que devolvesse só o degrau mais próximo deixaria essa carga
  inteira em silêncio.
* `test_a_comissao_sai_do_LIQUIDO_e_nao_do_total_com_IOF` — o erro de conta do
  meu próprio mockup, que mostrava R$ 817,71 onde o certo é R$ 761,51. O IOF é
  imposto repassado ao governo; comissão sobre ele é comissão inventada.
* `test_lembretes_enviados_aceita_o_tipo_renovacao` — o incidente das migrações
  128 e 171, pela terceira vez: o CHECK que não conhece o tipo novo derruba o
  tick INTEIRO do ticker, não só o aviso da corretora.
"""
import json
import os
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import pytest
from psycopg.errors import UniqueViolation
from psycopg_pool import ConnectionPool

from finance import apolices as ap

MIG = Path(__file__).resolve().parent.parent / "db" / "migracoes"
CONTA = 37          # a Liberal Seguros, que deu origem a tudo isto
OUTRA = 38

_BASE = """
create table contas (id bigint primary key, nome text);
create table membros (id bigserial primary key, conta_id bigint references contas(id),
  nome text, email text, papel text not null default 'membro', ativo boolean not null default true);
create table pessoas (id bigserial primary key, cpf text, cnpj text, tipo text, celular text,
  nome text, email text);
create table clientes (id bigserial primary key, dono_id bigint references contas(id),
  pessoa_id bigint references pessoas(id), nome text not null, telefone text, email text,
  endereco text, cidade text, uf text, cep text, obs text,
  ativo boolean not null default true, criado_em timestamptz not null default now());
create table conversas (id bigserial primary key, conta_id bigint, contato_ref text,
  contato_nome text, ultima_msg_em timestamptz);
create table mensagens (id bigserial primary key, conversa_id bigint, direcao text, texto text,
  criado_em timestamptz not null default now(),
  -- o ponteiro da mídia (migração 187): é por ele que o PDF que chegou no
  -- WhatsApp vira candidato a apólice, sem o arquivo encostar no banco
  midia_ref jsonb, midia_tipo text, midia_meta jsonb, midia_arquivo text);
create table lembretes_enviados (id bigserial primary key,
  conta_id bigint not null references contas(id) on delete cascade,
  tipo text not null check (tipo in ('resumo','aviso')),
  chave text not null, enviado_em timestamptz not null default now(),
  unique (conta_id, tipo, chave));
insert into contas (id, nome) values (37,'Liberal Seguros'), (38,'Outra Corretora');
"""

HOJE = date(2026, 9, 17)


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_apolices_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_BASE)
        c.execute((MIG / "278_apolices.sql").read_text(encoding="utf-8"))
        c.execute((MIG / "286_apolices_pdf.sql").read_text(encoding="utf-8"))
        c.execute((MIG / "287_apolice_perdida.sql").read_text(encoding="utf-8"))
        c.execute((MIG / "289_apolice_remetentes.sql").read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


@pytest.fixture
def limpo(pool):
    with pool.connection() as c:
        c.execute("delete from apolice_remetentes")
        c.execute("delete from apolices")
        c.execute("delete from seguros_comissao")
        c.execute("delete from lembretes_enviados")
        c.execute("delete from clientes")
        c.execute("delete from pessoas")
        c.execute("delete from membros")
        c.execute("delete from mensagens")
        c.execute("delete from conversas")
        c.commit()
    return pool


def _apolice(pool, **kw):
    d = {"seguradora": "Allianz", "ramo": "auto", "situacao": "vigente",
         "vigencia_fim": HOJE + timedelta(days=45), "premio_centavos": 380757,
         "iof_centavos": 28100, "bem": {}, "condutor": {}, "coberturas": []}
    d.update(kw)
    return ap.salvar(pool, d.pop("conta_id", CONTA), d)


# ------------------------------------------------------------------ a régua

def test_a_regua_tem_tres_degraus_e_nao_cinco():
    """"60/30/15" — decisão do dono. A escada de mercado é 90/60/45/30/15, e cinco
    avisos sobre a mesma apólice viram ruído até o corretor ignorar todos."""
    assert ap.DEGRAUS == (60, 30, 15)


def test_os_90_dias_sao_LISTA_e_nao_alerta():
    """O horizonte da tela é maior que o primeiro degrau de propósito: entre 90 e
    60 dias a apólice aparece e não incomoda ninguém."""
    assert ap.HORIZONTE == 90
    assert ap.HORIZONTE > ap.DEGRAUS[0]
    assert ap.degrau_de(75) is None


@pytest.mark.parametrize("dias, esperado", [
    (91, None), (61, None), (60, 60), (59, 60), (31, 60),
    (30, 30), (16, 30), (15, 15), (1, 15), (0, 15),
])
def test_cada_faixa_cai_no_seu_degrau(dias, esperado):
    assert ap.degrau_de(dias) == esperado


def test_a_carteira_recem_carregada_nao_fica_calada():
    """O caso REAL da Liberal: ela vai digitar a carteira inteira de uma vez, e
    parte dela já vence em menos de 60 dias.

    `degrau_de` devolve o degrau mais APERTADO já alcançado: faltando 20 dias ela
    cai no 30 e avisa hoje. Devolvendo o MAIOR ("ela passou do 60") a régua
    quebraria em dois lugares de uma vez — a carga nova só avisaria daqui a cinco
    dias, e, pior, o dedup por (apólice, degrau) travaria em 60 pra sempre e os
    avisos de 30 e de 15 nunca sairiam. Três toques virariam um.
    """
    assert ap.degrau_de(20) == 30
    assert ap.degrau_de(50) == 60


def test_quem_ja_venceu_nao_entra_na_regua():
    """Não é aviso de renovação — é outra conversa, e ela não se resolve com push."""
    assert ap.degrau_de(-1) is None
    assert ap.degrau_de(-400) is None
    assert ap.degrau_de(None) is None


# --------------------------------------------------------------- a comissão

def test_a_comissao_sai_do_LIQUIDO_e_nao_do_total_com_IOF():
    """O papel da Allianz: líquido R$ 3.807,57 + IOF R$ 281,00 = R$ 4.088,57.

    A comissão do corretor incide sobre o LÍQUIDO. O mockup somou os dois e
    anunciou R$ 817,71; o certo, com o mesmo papel, é R$ 761,51. São 56 reais de
    diferença numa apólice só — numa carteira inteira é a comissão parecendo maior
    do que é, todo mês, no número em que o Raio-X da corretora se apoia.
    """
    assert ap.comissao_de(380757, 20) == 76151
    assert ap.comissao_de(380757 + 28100, 20) == 81771   # a conta ERRADA, pra fixar a diferença


def test_sem_percentual_cadastrado_a_comissao_e_None_e_nao_zero():
    """Zero diria "esta apólice não paga comissão", que é outra afirmação — e é a
    que faria a tela mentir com cara de dado."""
    assert ap.comissao_de(380757, None) is None
    assert ap.comissao_de(0, None) is None


def test_o_valor_digitado_ganha_do_percentual():
    """Quando a corretora recebe o extrato da seguradora e digita o número exato,
    esse número é FATO. Um percentual estimado não sobrescreve um fato."""
    assert ap.comissao_de(380757, 20, comissao_centavos=70000) == 70000
    # inclusive quando o fato é zero: comissão zerada existe (apólice cortesia,
    # repasse, acerto) e não pode virar 20% de novo na próxima leitura.
    assert ap.comissao_de(380757, 20, comissao_centavos=0) == 0


def test_o_ramo_exato_ganha_do_curinga(limpo):
    """"Allianz 20%" vale pra tudo; "Allianz vida 12%" manda em vida. É assim que a
    corretora cadastra uma linha por seguradora e só detalha onde foge."""
    ap.salvar_comissao(limpo, CONTA, "Allianz", "", 20)
    ap.salvar_comissao(limpo, CONTA, "Allianz", "vida", "12,5")
    with limpo.connection() as c:
        assert float(ap.pct_padrao(c, CONTA, "Allianz", "auto")) == 20.0
        assert float(ap.pct_padrao(c, CONTA, "Allianz", "vida")) == 12.5
        assert float(ap.pct_padrao(c, CONTA, "allianz", "auto")) == 20.0   # não é sensível a caixa
        assert ap.pct_padrao(c, CONTA, "Porto", "auto") is None


def test_o_percentual_de_uma_corretora_nao_vaza_pra_outra(limpo):
    ap.salvar_comissao(limpo, CONTA, "Allianz", "", 20)
    with limpo.connection() as c:
        assert ap.pct_padrao(c, OUTRA, "Allianz", "auto") is None


def test_percentual_fora_da_faixa_nao_grava(limpo):
    assert ap.salvar_comissao(limpo, CONTA, "Allianz", "", 101) is False
    assert ap.salvar_comissao(limpo, CONTA, "Allianz", "", -1) is False
    assert ap.salvar_comissao(limpo, CONTA, "", "", 20) is False
    assert ap.salvar_comissao(limpo, CONTA, "Allianz", "nao_existe", 20) is False
    assert ap.comissoes(limpo, CONTA) == []


def test_salvar_o_mesmo_par_atualiza_em_vez_de_duplicar(limpo):
    ap.salvar_comissao(limpo, CONTA, "Allianz", "auto", 20)
    ap.salvar_comissao(limpo, CONTA, "ALLIANZ", "auto", 22)
    linhas = ap.comissoes(limpo, CONTA)
    assert len(linhas) == 1 and float(linhas[0]["pct"]) == 22.0


# ---------------------------------------------------------------- a apólice

def test_apolice_sem_fim_de_vigencia_nao_grava(limpo):
    """Ela ficaria pra sempre na carteira parecendo coberta e nunca avisaria."""
    with pytest.raises(ValueError):
        ap.salvar(limpo, CONTA, {"seguradora": "Allianz", "vigencia_fim": None})
    with pytest.raises(ValueError):
        ap.salvar(limpo, CONTA, {"seguradora": "", "vigencia_fim": HOJE})
    with pytest.raises(ValueError):
        ap.salvar(limpo, CONTA, {"seguradora": "A", "vigencia_fim": HOJE, "ramo": "foguete"})


def test_o_bem_de_auto_vai_e_volta_inteiro(limpo):
    """O `bem` é jsonb porque auto tem placa e chassi, residencial tem metragem e
    vida tem beneficiário — e uma coluna por campo de cada ramo seria uma tabela
    com sessenta colunas nulas em toda linha."""
    i = _apolice(limpo, bem={"placa": "ABC1D23", "modelo": "GEELY EX2 MAX", "ano": "2026"})
    a = ap.uma(limpo, CONTA, i, hoje=HOJE)
    assert a["bem"]["placa"] == "ABC1D23"
    assert a["bem"]["modelo"] == "GEELY EX2 MAX"


def test_a_mesma_apolice_nao_entra_duas_vezes(limpo):
    """Cadastro duplicado conta o dobro de prêmio E dispara o alerta duplicado."""
    _apolice(limpo, numero_apolice="139041981")
    with pytest.raises(UniqueViolation):
        _apolice(limpo, numero_apolice="139041981")


def test_a_mesma_PROPOSTA_nao_entra_duas_vezes(limpo):
    """Migração 286. O índice da 278 protege pelo nº da APÓLICE, que uma proposta
    ainda não tem (NULL não colide). Reimportar o mesmo PDF cadastraria a mesma
    proposta de novo — a Maria de Fátima em produção é esse caso."""
    _apolice(limpo, situacao="proposta", numero_proposta="139041981")
    with pytest.raises(UniqueViolation):
        _apolice(limpo, situacao="proposta", numero_proposta="139041981")
    # seguradora diferente com o mesmo número é OUTRA proposta (numeração é da seguradora)
    _apolice(limpo, situacao="proposta", numero_proposta="139041981", seguradora="Porto")
    # e caixa não separa: "allianz" e "Allianz" são a mesma casa
    with pytest.raises(UniqueViolation):
        _apolice(limpo, situacao="proposta", numero_proposta="139041981", seguradora="allianz")


def test_duas_PROPOSTAS_sem_numero_convivem(limpo):
    """Na fase de proposta o número da apólice ainda não foi emitido — e a trava de
    duplicidade não pode impedir a corretora de cadastrar duas propostas."""
    _apolice(limpo, situacao="proposta", numero_proposta="1")
    _apolice(limpo, situacao="proposta", numero_proposta="2")
    assert len(ap.listar(limpo, CONTA, hoje=HOJE)) == 2


def test_a_comissao_da_apolice_sobrescreve_o_padrao_da_seguradora(limpo):
    ap.salvar_comissao(limpo, CONTA, "Allianz", "", 20)
    padrao = _apolice(limpo)
    especial = _apolice(limpo, comissao_pct="25", numero_apolice="X")
    por_id = {a["id"]: a for a in ap.listar(limpo, CONTA, hoje=HOJE)}
    assert por_id[padrao]["comissao_estimada"] == 76151      # 20% do líquido
    assert por_id[especial]["comissao_estimada"] == 95189    # 25% do líquido


# ----------------------------------------------------------------- a fila

def test_a_fila_vem_do_mais_urgente_pro_mais_folgado(limpo):
    _apolice(limpo, vigencia_fim=HOJE + timedelta(days=80), numero_apolice="C")
    _apolice(limpo, vigencia_fim=HOJE + timedelta(days=10), numero_apolice="A")
    _apolice(limpo, vigencia_fim=HOJE + timedelta(days=40), numero_apolice="B")
    fila = ap.a_vencer(limpo, CONTA, hoje=HOJE)
    assert [a["numero_apolice"] for a in fila] == ["A", "B", "C"]
    assert [a["degrau"] for a in fila] == [15, 60, None]


def test_o_que_vence_depois_do_horizonte_fica_de_fora(limpo):
    _apolice(limpo, vigencia_fim=HOJE + timedelta(days=200))
    assert ap.a_vencer(limpo, CONTA, hoje=HOJE) == []


def test_renovada_e_cancelada_saem_da_fila(limpo):
    """Elas já acabaram a vida útil. Continuar avisando sobre uma apólice renovada
    é o sistema pedindo pra ser ignorado."""
    _apolice(limpo, situacao="renovada", numero_apolice="R")
    _apolice(limpo, situacao="cancelada", numero_apolice="C")
    _apolice(limpo, situacao="vigente", numero_apolice="V")
    assert [a["numero_apolice"] for a in ap.a_vencer(limpo, CONTA, hoje=HOJE)] == ["V"]


def test_a_que_ja_venceu_e_ninguem_tocou_CONTINUA_aparecendo(limpo):
    """Escondê-la deixaria a corretora com um cliente descoberto e a tela dizendo
    que está tudo bem. Ela aparece, e com `degrau` None: aparece sem avisar."""
    _apolice(limpo, vigencia_fim=HOJE - timedelta(days=5))
    fila = ap.a_vencer(limpo, CONTA, hoje=HOJE)
    assert len(fila) == 1 and fila[0]["dias"] == -5 and fila[0]["degrau"] is None


def test_a_fila_do_corretor_e_so_dele(limpo):
    """Decisão do dono: o corretor vê a fila dele, o dono vê a carteira inteira."""
    with limpo.connection() as c:
        ana = c.execute("insert into membros (conta_id, nome, papel) "
                        "values (%s,'Ana','vendedor') returning id", (CONTA,)).fetchone()[0]
        bruno = c.execute("insert into membros (conta_id, nome, papel) "
                          "values (%s,'Bruno','vendedor') returning id", (CONTA,)).fetchone()[0]
        c.commit()
    _apolice(limpo, corretor_id=ana, numero_apolice="A")
    _apolice(limpo, corretor_id=bruno, numero_apolice="B")
    assert [a["numero_apolice"] for a in ap.a_vencer(limpo, CONTA, hoje=HOJE, corretor_id=ana)] == ["A"]
    assert len(ap.a_vencer(limpo, CONTA, hoje=HOJE)) == 2


def test_a_carteira_de_uma_corretora_nao_aparece_na_outra(limpo):
    _apolice(limpo, conta_id=CONTA)
    assert ap.a_vencer(limpo, OUTRA, hoje=HOJE) == []
    assert ap.listar(limpo, OUTRA, hoje=HOJE) == []


def test_o_nome_do_cliente_vem_junto(limpo):
    with limpo.connection() as c:
        cli = c.execute("insert into clientes (dono_id, nome) values (%s,'Maria Fernanda') "
                        "returning id", (CONTA,)).fetchone()[0]
        c.commit()
    _apolice(limpo, cliente_id=cli)
    assert ap.a_vencer(limpo, CONTA, hoje=HOJE)[0]["cliente"] == "Maria Fernanda"


def test_apolice_sem_cliente_na_carteira_ainda_assim_grava(limpo):
    """A corretora digita a apólice antes de o cliente existir na carteira mais
    vezes do que o contrário. Recusar seria o sistema escolhendo a ordem do
    trabalho dela."""
    _apolice(limpo, cliente_id=None)
    assert ap.a_vencer(limpo, CONTA, hoje=HOJE)[0]["cliente"] == "—"


# ------------------------------------------------- o vazio que informa (tela)

def test_a_proxima_e_a_que_vem_DEPOIS_do_horizonte(limpo):
    """O caso real da Liberal: uma apólice na carteira, vencendo em 23/07/2027 —
    309 dias a partir do HOJE deste arquivo (17/09). A fila fica vazia e a tela dizia "nenhuma apólice
    vencendo", o que é verdade e é inútil: convida a cadastrar de novo o que já
    está lá. `proxima` é o que transforma o vazio em informação."""
    _apolice(limpo, vigencia_fim=date(2027, 7, 23), numero_apolice="A")
    assert ap.a_vencer(limpo, CONTA, hoje=HOJE) == []
    p = ap.proxima(limpo, CONTA, hoje=HOJE)
    assert p is not None and p["dias"] == 309
    # e responde a pergunta seguinte sem obrigar ninguém a fazer a conta de cabeça
    assert p["entra_em"] == date(2027, 5, 24)      # 60 dias antes de vencer


def test_a_proxima_ignora_o_que_ja_esta_na_fila(limpo):
    """Ela é o que vem DEPOIS do horizonte. Devolver quem já está na lista faria a
    tela mostrar a mesma apólice duas vezes."""
    _apolice(limpo, vigencia_fim=HOJE + timedelta(days=30), numero_apolice="perto")
    assert ap.proxima(limpo, CONTA, hoje=HOJE) is None


def test_a_proxima_nao_traz_renovada_nem_vencida(limpo):
    _apolice(limpo, vigencia_fim=date(2027, 1, 1), situacao="renovada", numero_apolice="R")
    _apolice(limpo, vigencia_fim=date(2027, 6, 1), situacao="cancelada", numero_apolice="C")
    assert ap.proxima(limpo, CONTA, hoje=HOJE) is None


def test_a_proxima_respeita_o_corretor(limpo):
    with limpo.connection() as c:
        ana = c.execute("insert into membros (conta_id, nome, papel) "
                        "values (%s,'Ana','vendedor') returning id", (CONTA,)).fetchone()[0]
        c.commit()
    _apolice(limpo, corretor_id=ana, vigencia_fim=date(2027, 7, 23), numero_apolice="A")
    assert ap.proxima(limpo, CONTA, hoje=HOJE, corretor_id=ana) is not None
    assert ap.proxima(limpo, CONTA, hoje=HOJE, corretor_id=ana + 999) is None


# --------------------------------------------------------- a busca da carteira

def test_a_busca_acha_por_nome_seguradora_placa_e_numero(limpo):
    """Com trezentas apólices a tabela sem busca é ilegível — e trezentas é o
    tamanho de uma corretora pequena."""
    with limpo.connection() as c:
        cli = c.execute("insert into clientes (dono_id, nome) values (%s,'Maria Fernanda') "
                        "returning id", (CONTA,)).fetchone()[0]
        c.commit()
    _apolice(limpo, cliente_id=cli, numero_apolice="139041981",
             bem={"placa": "ABC1D23", "modelo": "GEELY EX2 MAX"})
    _apolice(limpo, seguradora="Porto", numero_apolice="999", bem={"placa": "XYZ9Z99"})
    def acha(termo):
        return [a["numero_apolice"] for a in ap.listar(limpo, CONTA, hoje=HOJE, busca=termo)]
    assert acha("maria") == ["139041981"]          # nome do cliente
    assert acha("porto") == ["999"]                # seguradora
    assert acha("ABC1D23") == ["139041981"]        # placa, que está no jsonb
    assert acha("geely") == ["139041981"]          # modelo, idem
    assert acha("13904") == ["139041981"]          # número
    assert acha("nao existe nada assim") == []


def test_a_busca_vazia_traz_tudo(limpo):
    _apolice(limpo, numero_apolice="A")
    _apolice(limpo, numero_apolice="B")
    assert len(ap.listar(limpo, CONTA, hoje=HOJE, busca="")) == 2
    assert len(ap.listar(limpo, CONTA, hoje=HOJE, busca=None)) == 2
    assert len(ap.listar(limpo, CONTA, hoje=HOJE)) == 2


def test_a_busca_nao_atravessa_a_conta(limpo):
    _apolice(limpo, conta_id=CONTA, seguradora="Allianz", numero_apolice="A")
    assert ap.listar(limpo, OUTRA, hoje=HOJE, busca="allianz") == []


def test_o_total_da_carteira_ignora_a_busca(limpo):
    """A aba mostra o número de tudo mesmo com filtro ligado: "Carteira (1)" com uma
    busca ativa faria a corretora achar que perdeu as outras 299."""
    _apolice(limpo, seguradora="Allianz", numero_apolice="A")
    _apolice(limpo, seguradora="Porto", numero_apolice="B")
    assert ap.total_da_carteira(limpo, CONTA) == 2
    assert len(ap.listar(limpo, CONTA, hoje=HOJE, busca="porto")) == 1
    assert ap.total_da_carteira(limpo, CONTA) == 2
    assert ap.total_da_carteira(limpo, OUTRA) == 0


# --------------------------------------------------------- o PDF guardado junto

def test_o_pdf_vai_e_volta_e_quem_foi_digitada_nao_tem(limpo):
    """Migração 286: a apólice importada guarda o caminho no cofre e o que o leitor
    leu; a digitada à mão fica com tudo NULO — é a resposta honesta, não '{}'."""
    com = _apolice(limpo, numero_apolice="PDF", pdf_caminho="apolice/37/x.pdf",
                   pdf_nome="allianz.pdf", pdf_bytes=61234,
                   pdf_lido={"reconhecida": True, "campos": {"vigencia_fim": "2027-07-23"}},
                   pdf_lido_em=datetime(2026, 9, 18, 14, 0, tzinfo=timezone.utc))
    sem = _apolice(limpo, numero_apolice="MAO")
    por_id = {a["id"]: a for a in ap.listar(limpo, CONTA, hoje=HOJE)}
    assert por_id[com]["tem_pdf"] is True and por_id[com]["pdf_nome"] == "allianz.pdf"
    assert por_id[sem]["tem_pdf"] is False and por_id[sem]["pdf_caminho"] is None
    with limpo.connection() as c:
        lido, lido_em = c.execute("select pdf_lido, pdf_lido_em from apolices where id=%s",
                                  (com,)).fetchone()
        nulo = c.execute("select pdf_lido from apolices where id=%s", (sem,)).fetchone()[0]
    assert lido["campos"]["vigencia_fim"] == "2027-07-23" and lido_em is not None
    assert nulo is None


# ------------------------------------------------------------ perdida, com motivo

MOTIVOS = [{"chave": "renovou_direto", "rotulo": "Renovou direto", "exige_descricao": False},
           {"chave": "outro", "rotulo": "Outro", "exige_descricao": True}]


def test_perdida_e_um_estado_proprio_e_sai_da_fila(limpo):
    """Migração 287. 'vencida' é o calendário; 'cancelada' é encerrar no meio.
    'perdida' é "o cliente NÃO renovou comigo" — e é o que o Raio-X conta."""
    i = _apolice(limpo, vigencia_fim=HOJE + timedelta(days=20))
    ap.perder(limpo, CONTA, i, motivo="renovou_direto", motivos_validos=MOTIVOS)
    a = ap.uma(limpo, CONTA, i, hoje=HOJE)
    assert a["situacao"] == "perdida" and a["perda_motivo"] == "renovou_direto"
    assert a["perdida_em"] is not None
    assert ap.a_vencer(limpo, CONTA, hoje=HOJE) == []          # saiu da fila
    assert "perdida" not in ap.VIVAS and "perdida" in dict(ap.SITUACOES)


def test_perdida_sem_motivo_e_recusada(limpo):
    """É o motivo que faz o estado valer alguma coisa."""
    i = _apolice(limpo)
    with pytest.raises(ValueError):
        ap.perder(limpo, CONTA, i, motivo="", motivos_validos=MOTIVOS)
    with pytest.raises(ValueError, match="desconhecido"):
        ap.perder(limpo, CONTA, i, motivo="inventado", motivos_validos=MOTIVOS)
    with pytest.raises(ValueError, match="descricao_obrigatoria"):
        ap.perder(limpo, CONTA, i, motivo="outro", descricao="", motivos_validos=MOTIVOS)
    assert ap.uma(limpo, CONTA, i, hoje=HOJE)["situacao"] == "vigente"   # nada mudou
    ap.perder(limpo, CONTA, i, motivo="outro", descricao="mudou de cidade", motivos_validos=MOTIVOS)
    assert ap.uma(limpo, CONTA, i, hoje=HOJE)["situacao"] == "perdida"


def test_as_decisoes_nao_incluem_vencida():
    """'vencida' não é decisão de ninguém — é o calendário, e `marcar_vencidas`
    grava sozinho. Chip de 'vencida' na janela seria a pessoa fingindo ser o tempo."""
    assert "vencida" not in ap.DECISOES
    assert set(ap.DECISOES) == {"proposta", "vigente", "renovada", "perdida"}


# ------------------------------------------------------------ a ficha na janela

def _cliente(pool, nome="Maria Fernanda", telefone="86994800000"):
    with pool.connection() as c:
        cid = c.execute("insert into clientes (dono_id, nome, telefone) values (%s,%s,%s) returning id",
                        (CONTA, nome, telefone)).fetchone()[0]
        c.commit()
    return cid


def test_a_ficha_junta_identidade_apolices_e_conversa(limpo):
    cid = _cliente(limpo)
    viva = _apolice(limpo, cliente_id=cid, vigencia_fim=HOJE + timedelta(days=300), numero_apolice="V")
    _apolice(limpo, cliente_id=cid, vigencia_fim=HOJE - timedelta(days=400), situacao="renovada", numero_apolice="R")
    with limpo.connection() as c:
        cv = c.execute("insert into conversas (conta_id, contato_ref, ultima_msg_em) values (%s,'5586994800000', now()) "
                       "returning id", (CONTA,)).fetchone()[0]
        for i, (d, t) in enumerate([("in", "oi"), ("out", "olá"), ("in", "meu seguro vence?"),
                                    ("out", "vence em julho"), ("in", "ok"), ("out", "te aviso antes")]):
            c.execute("insert into mensagens (conversa_id, direcao, texto, criado_em) "
                      "values (%s,%s,%s, now() + (%s || ' minutes')::interval)", (cv, d, t, i))
        c.commit()
    f = ap.ficha_do_cliente(limpo, CONTA, cid, hoje=HOJE)
    assert f["nome"] == "Maria Fernanda" and f["tipo"] == "pf"
    assert [a["id"] for a in f["apolices"]] == [viva, f["apolices"][1]["id"]]   # a viva primeiro (vence depois)
    assert f["n_vivas"] == 1 and f["proxima"]["id"] == viva
    assert f["premio_ano_centavos"] == 380757                  # só as vivas somam
    # a conversa: as ÚLTIMAS 5, em ordem cronológica, e quem falou
    assert f["conversa_id"] == cv
    assert [m["texto"] for m in f["conversa"]] == ["olá", "meu seguro vence?", "vence em julho", "ok", "te aviso antes"]
    assert [m["de"] for m in f["conversa"]][:2] == ["você", "ele"]
    assert any("entrou na carteira" in t for _, t in f["linha_do_tempo"])


def test_a_conversa_casa_pelos_ultimos_11_digitos_e_nao_por_8(limpo):
    """Medido na conta 37 em 18/09: 39/39 leads casam por 11 dígitos. Por 8 também
    casariam — e casariam gente errada. O `contato_ref` tem o 55 na frente; o
    telefone do cliente costuma não ter."""
    cid = _cliente(limpo, telefone="(86) 99480-0000")           # com máscara, sem 55
    with limpo.connection() as c:
        c.execute("insert into conversas (conta_id, contato_ref) values (%s,'5586994800000')", (CONTA,))
        # outro número que compartilha os 8 finais mas não os 11
        c.execute("insert into conversas (conta_id, contato_ref) values (%s,'5511994800000')", (CONTA,))
        c.execute("insert into mensagens (conversa_id, direcao, texto) select id, 'in', 'errado' "
                  "  from conversas where contato_ref='5511994800000'")
        c.commit()
    f = ap.ficha_do_cliente(limpo, CONTA, cid, hoje=HOJE)
    assert f["conversa"] == []                                  # não pegou a do 11
    assert f["conversa_id"] is not None                         # pegou a certa (sem mensagens)


def test_a_conversa_nao_atravessa_a_conta(limpo):
    cid = _cliente(limpo)
    with limpo.connection() as c:
        c.execute("insert into conversas (conta_id, contato_ref) values (%s,'5586994800000')", (OUTRA,))
        c.commit()
    assert ap.ficha_do_cliente(limpo, CONTA, cid, hoje=HOJE)["conversa_id"] is None


def test_cliente_sem_telefone_nao_quebra_a_ficha(limpo):
    cid = _cliente(limpo, telefone=None)
    f = ap.ficha_do_cliente(limpo, CONTA, cid, hoje=HOJE)
    assert f is not None and f["conversa"] == [] and f["apolices"] == []


def test_ficha_de_cliente_de_outra_conta_e_None(limpo):
    cid = _cliente(limpo)
    assert ap.ficha_do_cliente(limpo, OUTRA, cid, hoje=HOJE) is None


# ------------------------------------------------------------ marcar vencidas

def test_marcar_vencidas_so_mexe_no_que_o_tempo_ja_mudou(limpo):
    viva = _apolice(limpo, vigencia_fim=HOJE + timedelta(days=10), numero_apolice="V")
    velha = _apolice(limpo, vigencia_fim=HOJE - timedelta(days=1), numero_apolice="X")
    renov = _apolice(limpo, vigencia_fim=HOJE - timedelta(days=90),
                     situacao="renovada", numero_apolice="R")
    assert ap.marcar_vencidas(limpo, HOJE) == 1
    por_id = {a["id"]: a for a in ap.listar(limpo, CONTA, hoje=HOJE)}
    assert por_id[velha]["situacao"] == "vencida"
    assert por_id[viva]["situacao"] == "vigente"
    assert por_id[renov]["situacao"] == "renovada"   # renovar não é vencer


def test_marcar_vencidas_e_idempotente(limpo):
    _apolice(limpo, vigencia_fim=HOJE - timedelta(days=1))
    assert ap.marcar_vencidas(limpo, HOJE) == 1
    assert ap.marcar_vencidas(limpo, HOJE) == 0


# ------------------------------------------------------------------ o alerta

def test_lembretes_enviados_aceita_o_tipo_renovacao(limpo):
    """PELA TERCEIRA VEZ (migrações 128 e 171): o CHECK que não conhece o tipo novo
    derruba o tick INTEIRO do ticker — `_primeira_vez` insere numa conexão própria
    e a CheckViolation sobe até `_rodar()`, matando o resumo do dia de todas as
    contas seguintes. Não é um aviso que se perde; são todos."""
    with limpo.connection() as c:
        c.execute("insert into lembretes_enviados (conta_id, tipo, chave) "
                  "values (%s,'renovacao','1:60')", (CONTA,))
        c.commit()


def _agora(h=9):
    return datetime(2026, 9, 17, h, 0, tzinfo=timezone.utc)


def _espiao(monkeypatch):
    enviados = []
    monkeypatch.setattr("finance.cockpit.enviar_push",
                        lambda pool, conta_id, membro_id, titulo, corpo, url=None, badge=None:
                        (enviados.append((conta_id, membro_id, titulo)), 1)[1])
    monkeypatch.setattr("finance.aviso_log.registrar", lambda *a, **k: None)
    return enviados


def test_o_alerta_vai_pro_corretor_da_apolice(limpo, monkeypatch):
    """Decisão do dono, verbatim: "alerta pro corretor"."""
    from finance import lembretes as lb
    with limpo.connection() as c:
        ana = c.execute("insert into membros (conta_id, nome, papel) "
                        "values (%s,'Ana','vendedor') returning id", (CONTA,)).fetchone()[0]
        c.execute("insert into membros (conta_id, nome, papel) values (%s,'Dono','dono')", (CONTA,))
        c.commit()
    _apolice(limpo, corretor_id=ana, vigencia_fim=HOJE + timedelta(days=30))
    enviados = _espiao(monkeypatch)
    assert lb._renovacoes(limpo, _agora()) == 1
    assert [m for (_c, m, _t) in enviados] == [ana]


def test_apolice_sem_corretor_cai_no_dono_e_nos_gestores(limpo, monkeypatch):
    """Aviso que não chega a ninguém é pior que aviso nenhum: some calado."""
    from finance import lembretes as lb
    with limpo.connection() as c:
        dono = c.execute("insert into membros (conta_id, nome, papel) "
                         "values (%s,'Dono','dono') returning id", (CONTA,)).fetchone()[0]
        c.execute("insert into membros (conta_id, nome, papel) values (%s,'Ana','vendedor')", (CONTA,))
        c.commit()
    _apolice(limpo, corretor_id=None, vigencia_fim=HOJE + timedelta(days=30))
    enviados = _espiao(monkeypatch)
    lb._renovacoes(limpo, _agora())
    assert [m for (_c, m, _t) in enviados] == [dono]


def test_o_mesmo_degrau_nao_avisa_duas_vezes(limpo, monkeypatch):
    """O ticker roda a cada 2 minutos. Sem o dedup seriam 30 pushes por hora, sobre
    a mesma apólice, pro mesmo corretor."""
    from finance import lembretes as lb
    with limpo.connection() as c:
        c.execute("insert into membros (conta_id, nome, papel) values (%s,'Dono','dono')", (CONTA,))
        c.commit()
    _apolice(limpo, vigencia_fim=HOJE + timedelta(days=30))
    enviados = _espiao(monkeypatch)
    assert lb._renovacoes(limpo, _agora()) == 1
    assert lb._renovacoes(limpo, _agora()) == 0
    assert len(enviados) == 1


def test_degrau_novo_avisa_de_novo(limpo, monkeypatch):
    """Três toques por apólice, não um: o dedup é por (apólice, degrau)."""
    from finance import lembretes as lb
    with limpo.connection() as c:
        c.execute("insert into membros (conta_id, nome, papel) values (%s,'Dono','dono')", (CONTA,))
        c.commit()
    _apolice(limpo, vigencia_fim=date(2026, 11, 16))    # 60 dias a partir de 17/09
    enviados = _espiao(monkeypatch)
    lb._renovacoes(limpo, _agora())
    # trinta dias depois, a MESMA apólice alcança o degrau 30
    lb._renovacoes(limpo, datetime(2026, 10, 17, 9, 0, tzinfo=timezone.utc))
    assert len(enviados) == 2


def test_fora_da_hora_o_alerta_nao_sai(limpo, monkeypatch):
    from finance import lembretes as lb
    _apolice(limpo, vigencia_fim=HOJE + timedelta(days=30))
    enviados = _espiao(monkeypatch)
    assert lb._renovacoes(limpo, _agora(h=14)) == 0
    assert enviados == []


def test_o_que_esta_alem_de_60_dias_nao_vira_push(limpo, monkeypatch):
    """Os 90 dias são a lista; o alerta começa em 60. É a diferença entre consulta
    e interrupção — e é por isso que o dono pediu três toques e não cinco."""
    from finance import lembretes as lb
    with limpo.connection() as c:
        c.execute("insert into membros (conta_id, nome, papel) values (%s,'Dono','dono')", (CONTA,))
        c.commit()
    _apolice(limpo, vigencia_fim=HOJE + timedelta(days=80))
    enviados = _espiao(monkeypatch)
    assert lb._renovacoes(limpo, _agora()) == 0
    assert enviados == []


# ───────────────── OS PDFs QUE CHEGAM PELO WHATSAPP VINCULADO ─────────────────
#
# O que estes testes seguram: o escopo (o id da mensagem é adivinhável) e a
# recusa em cadastrar sozinho. Na conta da Liberal, medido em 18/09/2026, a
# maioria dos PDFs que chegam não é apólice — é boleto, extrato e petição.


def _msg_com_pdf(limpo, conta_id, *, nome, de="Corretor", ref="5586999990000",
                 direcao="in", tipo="documento", mimetype="application/pdf", dias=1,
                 liberado=True):
    """Um PDF chegando no WhatsApp da conta. `liberado` é o padrão porque quase
    todo teste quer olhar a lista, e a lista só mostra quem foi liberado."""
    with limpo.connection() as c:
        cv = c.execute(
            "insert into conversas (conta_id, contato_ref, contato_nome, ultima_msg_em) "
            "values (%s, %s, %s, now()) returning id",
            (conta_id, ref, de)).fetchone()[0]
        mid = c.execute(
            "insert into mensagens (conversa_id, direcao, texto, criado_em, midia_ref, "
            "  midia_tipo, midia_meta) "
            "values (%s, %s, %s, now() - make_interval(days => %s), %s, %s, %s) returning id",
            (cv, direcao, "documento", dias,
             json.dumps({"directPath": "/x", "mediaKey": "aaaa", "mimetype": mimetype}),
             tipo, json.dumps({"nome": nome, "bytes": 120000}))).fetchone()[0]
        c.commit()
    if liberado:
        ap.liberar_remetente(limpo, conta_id, ref, rotulo=de)
    return mid


def test_lista_os_pdfs_que_chegaram(limpo):
    _msg_com_pdf(limpo, CONTA, nome="PROPOSTA DENISE.pdf")
    itens = ap.pdfs_do_whatsapp(limpo, CONTA)
    assert [i["nome"] for i in itens] == ["PROPOSTA DENISE.pdf"]
    assert itens[0]["parece_apolice"] is True
    assert itens[0]["ja_cadastrada"] is False


def test_boleto_tambem_aparece_mas_sem_a_marca(limpo):
    """Marcar é palpite; esconder seria decidir pela pessoa."""
    _msg_com_pdf(limpo, CONTA, nome="invoice-2000017755025792.pdf")
    itens = ap.pdfs_do_whatsapp(limpo, CONTA)
    assert len(itens) == 1 and itens[0]["parece_apolice"] is False


def test_nao_vaza_pdf_de_outra_conta(limpo):
    outra = _msg_com_pdf(limpo, 38, nome="APOLICE DE OUTRA CORRETORA.pdf")
    assert ap.pdfs_do_whatsapp(limpo, CONTA) == []
    assert ap.ref_do_pdf(limpo, CONTA, outra) is None
    assert ap.ref_do_pdf(limpo, 38, outra) is not None


def test_so_entrada_e_so_documento(limpo):
    _msg_com_pdf(limpo, CONTA, nome="mandei eu.pdf", direcao="out")
    _msg_com_pdf(limpo, CONTA, nome="foto.jpg", tipo="imagem", mimetype="image/jpeg")
    assert ap.pdfs_do_whatsapp(limpo, CONTA) == []


def test_o_que_ja_virou_apolice_sai_da_lista(limpo):
    mid = _msg_com_pdf(limpo, CONTA, nome="PROPOSTA JOSE.pdf")
    assert ap.pdfs_do_whatsapp(limpo, CONTA)[0]["ja_cadastrada"] is False
    _apolice(limpo, pdf_caminho="apolice/37/x.pdf",
             pdf_lido={"origem": {"whatsapp_msg": mid}})
    assert ap.pdfs_do_whatsapp(limpo, CONTA)[0]["ja_cadastrada"] is True


def test_pdf_velho_demais_nao_entra(limpo):
    _msg_com_pdf(limpo, CONTA, nome="PROPOSTA ANTIGA.pdf", dias=200)
    assert ap.pdfs_do_whatsapp(limpo, CONTA) == []
    assert len(ap.pdfs_do_whatsapp(limpo, CONTA, dias=365)) == 1


# ─────────────── SÓ CORRETOR E SEGURADORA ENTRAM NA LISTA ───────────────
#
# Pedido do dono em 19/09/2026: "limita a lista só pros numeros de corretor e
# seguradora". Prender ao número da EMPRESA já tirava o WhatsApp pessoal de
# todo mundo; isto tira o fornecedor de boleto, que escreve pro mesmo número.


def test_so_entra_pdf_de_quem_foi_liberado(limpo):
    _msg_com_pdf(limpo, CONTA, nome="PROPOSTA DO CORRETOR.pdf", de="Cássio",
                 ref="5586911111111")
    _msg_com_pdf(limpo, CONTA, nome="invoice-20000177.pdf", de="Fornecedor",
                 ref="5586922222222", liberado=False)
    assert [i["nome"] for i in ap.pdfs_do_whatsapp(limpo, CONTA)] == ["PROPOSTA DO CORRETOR.pdf"]


def test_sem_ninguem_liberado_a_lista_vem_vazia(limpo):
    _msg_com_pdf(limpo, CONTA, nome="PROPOSTA.pdf", liberado=False)
    assert ap.pdfs_do_whatsapp(limpo, CONTA) == []
    # mas a tela ainda sabe quem anda mandando, pra oferecer a liberação
    quem = ap.quem_mandou_pdf(limpo, CONTA)
    assert len(quem) == 1 and quem[0]["liberado"] is False and quem[0]["quantos"] == 1


def test_tirar_o_remetente_esconde_os_pdfs_dele(limpo):
    _msg_com_pdf(limpo, CONTA, nome="PROPOSTA.pdf", ref="5586911111111")
    assert len(ap.pdfs_do_whatsapp(limpo, CONTA)) == 1
    ap.tirar_remetente(limpo, CONTA, "5586911111111")
    assert ap.pdfs_do_whatsapp(limpo, CONTA) == []
    assert ap.remetentes(limpo, CONTA) == []


def test_nao_libera_numero_que_nao_tem_conversa_na_conta(limpo):
    """Senão daria pra liberar um número qualquer mandando outro id na requisição."""
    with pytest.raises(ValueError):
        ap.liberar_remetente(limpo, CONTA, "5511999998888", rotulo="Estranho")


def test_nao_libera_numero_de_conversa_de_outra_conta(limpo):
    _msg_com_pdf(limpo, 38, nome="PROPOSTA DA OUTRA.pdf", ref="5586933333333",
                 liberado=False)
    with pytest.raises(ValueError):
        ap.liberar_remetente(limpo, CONTA, "5586933333333")


def test_liberar_de_novo_atualiza_em_vez_de_duplicar(limpo):
    _msg_com_pdf(limpo, CONTA, nome="PROPOSTA.pdf", de="Cássio", ref="5586911111111")
    ap.liberar_remetente(limpo, CONTA, "5586911111111", rotulo="Cássio Liberal",
                         tipo="seguradora")
    libs = ap.remetentes(limpo, CONTA)
    assert len(libs) == 1
    assert libs[0]["rotulo"] == "Cássio Liberal" and libs[0]["tipo"] == "seguradora"


def test_tipo_desconhecido_nao_entra(limpo):
    _msg_com_pdf(limpo, CONTA, nome="PROPOSTA.pdf", ref="5586911111111", liberado=False)
    with pytest.raises(ValueError):
        ap.liberar_remetente(limpo, CONTA, "5586911111111", tipo="amigo")


def test_quem_mandou_conta_por_numero_e_poe_o_liberado_na_frente(limpo):
    _msg_com_pdf(limpo, CONTA, nome="a.pdf", de="Fornecedor", ref="5586922222222",
                 liberado=False)
    _msg_com_pdf(limpo, CONTA, nome="b.pdf", de="Fornecedor", ref="5586922222222",
                 liberado=False)
    _msg_com_pdf(limpo, CONTA, nome="PROPOSTA.pdf", de="Cássio", ref="5586911111111")
    quem = ap.quem_mandou_pdf(limpo, CONTA)
    assert [q["nome"] for q in quem] == ["Cássio", "Fornecedor"]
    assert quem[0]["liberado"] is True and quem[0]["quantos"] == 1
    assert quem[1]["liberado"] is False and quem[1]["quantos"] == 2


def test_o_remetente_nao_vaza_entre_contas(limpo):
    _msg_com_pdf(limpo, 38, nome="PROPOSTA DA OUTRA.pdf", ref="5586933333333")
    assert ap.remetentes(limpo, CONTA) == []
    assert len(ap.remetentes(limpo, 38)) == 1
    assert ap.quem_mandou_pdf(limpo, CONTA) == []
