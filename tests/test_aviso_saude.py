"""O VIGIA DO CANAL MORTO (18/09/2026).

POR QUE ELE EXISTE. O dono da conta 34 recebia todo dia a cópia de gestor dos 30
leads mais atrasados da casa — e não recebia nada: sem e-mail, sem aparelho com
push e, quando o WhatsApp entrou, um número que aceita o envio e nunca devolve
recibo. Ficou dois dias assim e só apareceu porque alguém foi procurar. Medir o
envio (`aviso_log`) sem avisar quando ele para é meio caminho.

O QUE ESTE ARQUIVO PROTEGE, e cada item é um jeito de o vigia virar inútil ou
injusto:

  1. as três regras acusam o que devem acusar;
  2. quem tem UM canal funcionando não é acusado — senão o alerta vira paisagem;
  3. o número só é acusado depois de DOIS envios e passadas as horas de espera:
     acusar cedo transforma "ele está almoçando" em "o número está errado";
  4. A GUARDA: se a conta inteira está sem recibo, ninguém é acusado. Culpar as
     pessoas por um defeito do sistema é o pior erro que este módulo pode cometer;
  5. base sem a tabela devolve vazio, porque isto alimenta um selo — e selo que
     derruba tela não serve.
"""
import os
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import aviso_saude as sa

CONTA = 81
MIG = Path(__file__).resolve().parent.parent / "db" / "migracoes"

_SQL = """
create table membros (id bigserial primary key, conta_id bigint, nome text,
  email text, papel text default 'vendedor', ativo boolean default true,
  whatsapp text, whatsapp_id text);
create table push_assinaturas (id bigserial primary key, conta_id bigint,
  membro_id bigint, endpoint text, p256dh text, auth text);
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_aviso_saude_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.execute((MIG / "276_aviso_envios.sql").read_text(encoding="utf-8"))
        c.execute((MIG / "284_aviso_envios_recibo.sql").read_text(encoding="utf-8"))
        # a equipe da Prime, com os casos reais: o dono sem e-mail e sem push, dois
        # vendedores completos, e uma pessoa sem nada cadastrado
        c.execute("""insert into membros (id, conta_id, nome, email, papel, whatsapp) values
                        (1,%s,'THIAGO','t@x.com','vendedor','86988614189'),
                        (2,%s,'PEDRO','p@x.com','vendedor','86995454554'),
                        (3,%s,'MANOEL',null,'dono','5599984996253'),
                        (4,%s,'IRIS',null,'vendedor',null),
                        (5,%s,'FINANCEIRO',null,'financeiro',null)""",
                  (CONTA, CONTA, CONTA, CONTA, CONTA))
        c.execute("insert into push_assinaturas (conta_id, membro_id, endpoint) "
                  "values (%s,1,'https://push/1'), (%s,2,'https://push/2')", (CONTA, CONTA))
        c.commit()
    yield p
    p.close()


def _envio(pool, membro_id, canal="whatsapp", ok=True, *, horas=24, recibo=False,
           motivo="", destino="86988614189"):
    """Uma linha de envio com idade controlada — a idade É a regra em duas delas."""
    with pool.connection() as c:
        c.execute(
            """insert into aviso_envios (conta_id, membro_id, canal, origem, ok, motivo,
                                         destino, criado_em, entregue_em)
               values (%s,%s,%s,'follow_up',%s,%s,%s,
                       now() - make_interval(hours => %s),
                       case when %s then now() - make_interval(hours => %s) end)""",
            (CONTA, membro_id, canal, ok, motivo or None, destino, horas, recibo, horas))
        c.commit()


def _tipos(pool):
    return {(a["quem"], a["tipo"]) for a in sa.alertas(pool, CONTA)}


# ───────────────────────────────────────────── 1. sem canal cadastrado
#
# É o único alerta que não espera nada se perder: ele fala antes do primeiro aviso.

def test_sem_cadastro_pega_quem_nao_tem_email_numero_nem_aparelho(pool):
    assert ("IRIS", "sem_cadastro") in _tipos(pool)


def test_sem_cadastro_nao_acusa_quem_tem_pelo_menos_um_canal(pool):
    """O Manoel tem número (mesmo que ruim); o Thiago tem tudo. Acusar quem tem um
    canal faria o alerta virar paisagem, e alerta que vira paisagem não é lido."""
    tipos = _tipos(pool)
    assert ("MANOEL", "sem_cadastro") not in tipos
    assert ("THIAGO", "sem_cadastro") not in tipos


def test_sem_cadastro_ignora_quem_nao_participa_do_aviso(pool):
    """O financeiro não entra na régua de follow-up: cobrar cadastro de quem não é
    avisado seria pedir pra pessoa arrumar algo que não usa."""
    assert ("FINANCEIRO", "sem_cadastro") not in _tipos(pool)


# ───────────────────────────────────────────── 2. silêncio com rastro
def test_sem_canal_pega_quem_teve_tentativa_e_nada_saiu(pool):
    _envio(pool, 3, canal="push", ok=False, motivo="nenhum aparelho com push")
    _envio(pool, 3, canal="email", ok=False, motivo="membro sem e-mail cadastrado")
    alertas = [a for a in sa.alertas(pool, CONTA) if a["tipo"] == "sem_canal"]
    assert [a["quem"] for a in alertas] == ["MANOEL"]
    assert "sem e-mail" in alertas[0]["detalhe"] and "push" in alertas[0]["detalhe"]


def test_UM_canal_que_funciona_tira_a_pessoa_do_alerta(pool):
    """É o que separa "não recebe nada" de "um canal ruim". O segundo não é
    urgente — a pessoa está sendo avisada."""
    _envio(pool, 1, canal="push", ok=False, motivo="nenhum aparelho com push")
    _envio(pool, 1, canal="email", ok=True)
    assert ("THIAGO", "sem_canal") not in _tipos(pool)


def test_sem_canal_olha_so_a_janela_recente(pool):
    """Falha de cinco dias atrás já pode ter sido corrigida; alerta velho é ruído."""
    _envio(pool, 1, canal="email", ok=False, horas=24 * 5, motivo="caixa cheia")
    assert ("THIAGO", "sem_canal") not in _tipos(pool)


# ───────────────────────────────────────────── 3. o número que não confirma
def test_numero_sem_recibo_pega_o_caso_do_dono(pool):
    """O caso traiçoeiro: o registro diz "enviado ✓" e o aparelho nunca confirma."""
    _envio(pool, 2, recibo=True)                      # a prova de que o caminho funciona
    _envio(pool, 3, destino="5599984996253")
    _envio(pool, 3, destino="5599984996253", horas=48)
    alertas = [a for a in sa.alertas(pool, CONTA) if a["tipo"] == "numero_sem_recibo"]
    assert [a["quem"] for a in alertas] == ["MANOEL"]
    assert "5599984996253" in alertas[0]["detalhe"]


def test_um_envio_sozinho_nao_acusa_o_numero(pool):
    """Um sem recibo é aparelho desligado numa tarde. Dois dias seguidos é o número."""
    _envio(pool, 2, recibo=True)
    _envio(pool, 3, destino="5599984996253")
    assert ("MANOEL", "numero_sem_recibo") not in _tipos(pool)


def test_envio_recente_demais_nao_acusa_o_numero(pool):
    """Recibo demora quando o telefone está no bolso. Acusar em minutos
    transformaria almoço em número errado."""
    _envio(pool, 2, recibo=True)
    _envio(pool, 3, destino="5599984996253", horas=1)
    _envio(pool, 3, destino="5599984996253", horas=2)
    assert ("MANOEL", "numero_sem_recibo") not in _tipos(pool)


def test_quem_teve_recibo_nao_e_acusado(pool):
    _envio(pool, 1, recibo=True)
    _envio(pool, 1)
    assert ("THIAGO", "numero_sem_recibo") not in _tipos(pool)


def test_A_GUARDA_conta_inteira_sem_recibo_nao_acusa_NINGUEM(pool):
    """O erro mais caro que este módulo poderia cometer: o caminho do recibo parou
    (chip religando, webhook fora do ar, deploy pela metade) e a tela marca a
    equipe inteira como "número errado". Culpar as pessoas por defeito do sistema
    destrói a confiança no alerta — e ele só vale enquanto é acreditado."""
    for membro in (1, 2, 3):
        _envio(pool, membro)
        _envio(pool, membro, horas=48)
    assert not [a for a in sa.alertas(pool, CONTA) if a["tipo"] == "numero_sem_recibo"]


# ───────────────────────────────────────────── ordem, escopo e tolerância
def test_o_mais_grave_vem_primeiro(pool):
    """Quem não tem canal nenhum antes de quem tem um canal ruim: a tela mostra
    uma lista, e a primeira linha é a que será lida."""
    _envio(pool, 2, recibo=True)
    _envio(pool, 3, canal="email", ok=False, motivo="membro sem e-mail cadastrado")
    tipos = [a["tipo"] for a in sa.alertas(pool, CONTA)]
    assert tipos[0] == "sem_cadastro"


def test_nao_vaza_alerta_de_outra_conta(pool):
    with pool.connection() as c:
        c.execute("insert into membros (id, conta_id, nome, papel) values (90, 999, 'DE FORA', 'vendedor')")
        c.commit()
    assert all(a["quem"] != "DE FORA" for a in sa.alertas(pool, CONTA))


def test_membro_desativado_nao_gera_alerta(pool):
    with pool.connection() as c:
        c.execute("update membros set ativo=false where id=4")
        c.commit()
    assert ("IRIS", "sem_cadastro") not in _tipos(pool)


def test_por_membro_indexa_e_guarda_o_mais_grave(pool):
    _envio(pool, 2, recibo=True)
    _envio(pool, 4, canal="email", ok=False, motivo="membro sem e-mail cadastrado")
    por = sa.por_membro(pool, CONTA)
    assert por[4]["tipo"] == "sem_cadastro", "o alerta mais grave da pessoa é o que fica"


def test_base_sem_a_tabela_devolve_vazio_em_vez_de_derrubar(pool):
    with pool.connection() as c:
        c.execute("drop table aviso_envios")
        c.commit()
    assert sa.alertas(pool, CONTA) == []
    assert sa.por_membro(pool, CONTA) == {}


# ───────────────────────────────────────────────────── as duas telas
#
# O alerta aparece em DOIS lugares, e não é repetição: no Follow-up porque é lá
# que o dono olha o aviso todo dia, e na Equipe porque é lá que ele CORRIGE — o
# campo do e-mail e do WhatsApp estão na mesma linha do selo.

def _alerta(quem="MANOEL", tipo="numero_sem_recibo",
            detalhe="2 avisos para 5599984996253 sem nenhum ✓✓ — o número pode estar errado"):
    return {"membro_id": 3, "quem": quem, "tipo": tipo, "detalhe": detalhe}


def test_a_tela_de_follow_up_mostra_quem_nao_esta_recebendo():
    import web.painel_follow_up as pfu  # noqa: F401 — registra o template
    from finance import follow_up as fu
    from finance import raio_x_perfil as rxp
    from web.portal import _env
    base = dict(perfil=rxp.perfil("eventos"), papel="dono", topo=fu.resumo([]), fila=[],
                sobrando=0, estado="critico", vend_f=None, etapa_f="", vendedores=[],
                etapas=[], gestao=[], modo="ligado", rotulo=fu.ROTULO, emoji=fu.EMOJI,
                cfg=dict(fu._PADRAO), tem_follow_up=True, raio_x_perfil=rxp.perfil("eventos"),
                caps={"vendas": True, "origens": True, "financeiro": True, "gerir": True},
                br=pfu._br, tempo=pfu._tempo, adia_max=fu.ADIAMENTOS_ATE_MOTIVO, erro="",
                resumo_msg=pfu._resumo_msg, quando_curto=pfu._quando_curto)
    t = _env.get_template("follow_up")
    com = "".join(t.blocks["conteudo"](t.new_context(dict(base, sem_aviso=[_alerta()]))))
    sem = "".join(t.blocks["conteudo"](t.new_context(dict(base, sem_aviso=[]))))
    assert "não" in com and "recebendo o aviso" in com and "MANOEL" in com
    assert "o número pode estar errado" in com
    assert "recebendo o aviso" not in sem, "faixa vermelha em tela sem problema nenhum"
    # e o caminho do conserto vai junto: alerta sem saída vira culpa
    assert "/painel/equipe" in com


def test_a_tela_de_equipe_marca_a_linha_de_quem_nao_recebe():
    import web.painel_equipe  # noqa: F401 — registra o template
    from finance import raio_x_perfil as rxp
    from web.portal import _env
    membro = {"id": 3, "nome": "MANOEL", "email": "", "papel": "dono", "rotulo": "Dono",
              "ativo": True, "pendente": False, "whatsapp": "5599984996253"}
    base = dict(tem_pj=True, membros=[membro], papeis=[("dono", "Dono")], convites=[],
                comissoes={}, request=None, raiox=None, grupos=[], raiox_qr=False,
                raio_x_perfil=rxp.perfil("eventos"))
    t = _env.get_template("equipe")
    com = "".join(t.blocks["conteudo"](t.new_context(dict(base, sem_aviso={3: _alerta()}))))
    sem = "".join(t.blocks["conteudo"](t.new_context(dict(base, sem_aviso={}))))
    # o SELO, pela marcação: procurar o texto solto casaria com o comentário do CSS,
    # que está na página sempre — e o teste passaria sem o selo existir
    assert 'class="mtag mudo"' in com and "o número pode estar errado" in com
    assert 'class="mtag mudo"' not in sem
