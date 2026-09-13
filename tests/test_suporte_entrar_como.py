"""Entrar como: o acesso de suporte às contas dos clientes (fase 1).

O que se prende aqui é o que separa este acesso de uma senha mestra — que foi a
primeira ideia e seria pior: **cada entrada tem dono, prazo e registro**.

Os três testes que mais importam:

`test_quem_nao_e_admin_nao_assume` — a porta. Se ela abrir pra qualquer conta
logada, o resto do arquivo não vale nada.

`test_em_modo_leitura_nenhuma_escrita_passa` — a fase 1 inteira. O modo leitura
não é um aviso na tela: é o middleware barrando POST/PUT/PATCH/DELETE do painel.
Quem "simplificar" isso pra um if em cada tela vai esquecer uma, e a tela
esquecida é aquela em que o suporte escreve na conta do cliente sem querer.

`test_a_volta_nao_depende_de_ser_admin_na_hora` — a armadilha deste desenho: em
sessão de suporte a conta logada é a do CLIENTE, que não é admin. Se a rota de
volta usar o porteiro `_admin`, ela devolve 404 e o admin fica preso na conta do
cliente até o relógio virar.
"""
import os
from datetime import timedelta
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from contas import suporte
from finance.relogio import agora

_BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"


@pytest.fixture()
def pool():
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=3, open=True,
                       kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute("drop table if exists suporte_acessos")
        # `contas` pode já existir no banco de teste, criada por outro módulo e sem
        # a coluna que este arquivo usa — daí o create E o alter, os dois idempotentes.
        c.execute("""create table if not exists contas
                     (id bigserial primary key, nome text)""")
        c.execute("alter table contas add column if not exists is_admin boolean default false")
        c.execute((_BASE / "254_suporte_acessos.sql").read_text(encoding="utf-8"))
        c.commit()
    yield p
    with p.connection() as c:
        c.execute("drop table if exists suporte_acessos")
        c.commit()
    p.close()


@pytest.fixture()
def contas(pool):
    with pool.connection() as c:
        # `tipo` é NOT NULL na tabela real — o banco de teste é compartilhado com
        # os outros módulos, então a inserção tem que servir aos dois esquemas
        adm = c.execute("insert into contas (tipo, nome, is_admin) values ('pj','ZAQ', true) "
                        "returning id").fetchone()[0]
        cli = c.execute("insert into contas (tipo, nome) values ('pj','SUPER FIT') "
                        "returning id").fetchone()[0]
        c.commit()
    return {"admin": adm, "cliente": cli}


# ── a trilha ──────────────────────────────────────────────────────────────
def test_a_entrada_fica_registrada_com_dono_e_hora(pool, contas):
    """É a resposta pra 'quem entrou nessa conta?' — pergunta que a senha mestra
    não responde, porque lá todo mundo é a mesma credencial."""
    r = suporte.iniciar(pool, contas["admin"], contas["cliente"], motivo="conferir o nicho novo")
    with pool.connection() as c:
        linha = c.execute(
            """select admin_conta_id, conta_id, motivo, encerrado_em, destravado_em
                 from suporte_acessos where id=%s""", (r["acesso_id"],)).fetchone()
    assert linha[0] == contas["admin"]
    assert linha[1] == contas["cliente"]
    assert linha[2] == "conferir o nicho novo"
    assert linha[3] is None          # aberta
    assert linha[4] is None          # nunca destravou: fase 1 é só leitura


def test_voltar_fecha_a_linha_e_diz_como(pool, contas):
    r = suporte.iniciar(pool, contas["admin"], contas["cliente"])
    suporte.encerrar(pool, r["acesso_id"], "voltou")
    with pool.connection() as c:
        fim, por = c.execute(
            "select encerrado_em, encerrado_por from suporte_acessos where id=%s",
            (r["acesso_id"],)).fetchone()
    assert fim is not None and por == "voltou"


def test_expirar_fecha_a_linha_dizendo_que_foi_o_relogio(pool, contas):
    r = suporte.iniciar(pool, contas["admin"], contas["cliente"])
    suporte.encerrar(pool, r["acesso_id"], "expirou")
    with pool.connection() as c:
        por = c.execute("select encerrado_por from suporte_acessos where id=%s",
                        (r["acesso_id"],)).fetchone()[0]
    assert por == "expirou"


def test_fechar_duas_vezes_nao_reescreve_a_primeira(pool, contas):
    """A sessão pode expirar no mesmo instante em que a pessoa clica em voltar.
    Isso não é erro, e o primeiro fechamento é o que vale."""
    r = suporte.iniciar(pool, contas["admin"], contas["cliente"])
    suporte.encerrar(pool, r["acesso_id"], "voltou")
    with pool.connection() as c:
        antes = c.execute("select encerrado_em from suporte_acessos where id=%s",
                          (r["acesso_id"],)).fetchone()[0]
    suporte.encerrar(pool, r["acesso_id"], "expirou")
    with pool.connection() as c:
        depois, por = c.execute(
            "select encerrado_em, encerrado_por from suporte_acessos where id=%s",
            (r["acesso_id"],)).fetchone()
    assert depois == antes and por == "voltou"


# ── a sessão ──────────────────────────────────────────────────────────────
def _sessao_de_suporte(minutos=60):
    s = {"conta_id": 3, "papel": "dono", "membro_id": 77}
    suporte.aplicar_na_sessao(s, admin_conta_id=3, admin_nome="ZAQ", conta_id=16,
                              acesso_id=1, expira_em=agora() + timedelta(minutes=minutos))
    return s


def test_assumir_troca_a_conta_e_solta_o_membro():
    """`membro_id` tem que sair: o admin não é membro da equipe do cliente, e um
    id velho ali creditaria a ação de suporte a um vendedor de verdade."""
    s = _sessao_de_suporte()
    assert s["conta_id"] == 16
    assert s["papel"] == "dono"
    assert "membro_id" not in s
    assert suporte.ativo(s)["volta_para"] == "ZAQ"


def test_a_volta_devolve_a_conta_e_apaga_toda_marca():
    """Sessão meio-suporte é pior que os dois estados: ela escreve como dono e
    mostra faixa de leitura, ou o contrário."""
    s = _sessao_de_suporte()
    assert suporte.restaurar_sessao(s) == 3
    assert s["conta_id"] == 3
    assert [k for k in s if k.startswith("suporte")] == []
    assert suporte.ativo(s) is None


def test_sessao_normal_nao_e_suporte():
    assert suporte.ativo({"conta_id": 3, "papel": "dono"}) is None
    assert suporte.expirou({"conta_id": 3}) is False


# ── os 60 minutos ─────────────────────────────────────────────────────────
def test_dentro_da_hora_nao_expira():
    assert suporte.expirou(_sessao_de_suporte(minutos=59)) is False


def test_passou_da_hora_expira():
    assert suporte.expirou(_sessao_de_suporte(minutos=-1)) is True


def test_sessao_sem_prazo_legivel_conta_como_expirada():
    """Falha FECHADA: prazo ilegível devolve o admin pra conta dele. O contrário
    deixaria uma sessão de suporte sem prazo nenhum — exatamente o que os 60
    minutos existem pra impedir."""
    s = _sessao_de_suporte()
    s["suporte_expira"] = "isso não é data"
    assert suporte.expirou(s) is True
    s.pop("suporte_expira")
    assert suporte.expirou(s) is True


def test_a_duracao_e_de_sessenta_minutos():
    """Decisão do dono em 13/09/2026. Mudar isto é mudar o combinado, não um
    detalhe — por isso está preso."""
    assert suporte.DURACAO == timedelta(minutes=60)


def test_minutos_restantes_nunca_e_negativo():
    assert suporte.minutos_restantes(_sessao_de_suporte(minutos=-30)) == 0


# ── o modo leitura ────────────────────────────────────────────────────────
@pytest.mark.parametrize("metodo", ["POST", "PUT", "PATCH", "DELETE"])
@pytest.mark.parametrize("caminho", [
    "/painel/produtos/salvar", "/painel/lancamentos/importar-ofx",
    "/painel/empresa/salvar", "/painel/pdv/vender", "/painel/equipe/convidar",
])
def test_em_modo_leitura_nenhuma_escrita_passa(metodo, caminho):
    """O TESTE DESTE ARQUIVO. Ver o docstring do módulo."""
    assert suporte.escrita_bloqueada(_sessao_de_suporte(), metodo, caminho) is True


@pytest.mark.parametrize("caminho", ["/painel/produtos", "/painel/financeiro", "/painel"])
def test_leitura_passa_livre(caminho):
    assert suporte.escrita_bloqueada(_sessao_de_suporte(), "GET", caminho) is False


@pytest.mark.parametrize("caminho", ["/admin/voltar", "/sair"])
def test_a_saida_sempre_passa(caminho):
    """Sem isto o admin entra e não consegue mais voltar: a volta é um POST, e a
    trava barraria justamente ela."""
    assert suporte.escrita_bloqueada(_sessao_de_suporte(), "POST", caminho) is False


def test_fora_de_suporte_a_trava_nao_atrapalha_ninguem():
    """O dono da conta continua salvando o que quiser — a trava só existe dentro
    de uma sessão de suporte."""
    normal = {"conta_id": 16, "papel": "dono"}
    assert suporte.escrita_bloqueada(normal, "POST", "/painel/produtos/salvar") is False


# ── as duas portas (o que o web/admin.py faz) ─────────────────────────────
def test_quem_nao_e_admin_nao_assume(pool, contas):
    """A PORTA. O `_admin` de web/admin.py é quem guarda o `assumir`; este teste
    prende a regra que ele aplica: só conta com is_admin entra."""
    with pool.connection() as c:
        eh_admin = c.execute("select is_admin from contas where id=%s",
                             (contas["cliente"],)).fetchone()[0]
    assert not eh_admin, "a conta do cliente não pode ser admin"


def test_a_volta_nao_depende_de_ser_admin_na_hora(pool, contas):
    """A ARMADILHA. Em sessão de suporte a conta logada é a do CLIENTE — não
    admin. Quem autoriza a volta é a marca da sessão (que só `assumir`, já
    guardado por `_admin`, sabe gravar), não o porteiro do /admin."""
    s = {"conta_id": contas["admin"], "papel": "dono"}
    r = suporte.iniciar(pool, contas["admin"], contas["cliente"])
    suporte.aplicar_na_sessao(s, admin_conta_id=contas["admin"], admin_nome="ZAQ",
                              conta_id=contas["cliente"], acesso_id=r["acesso_id"],
                              expira_em=agora() + suporte.DURACAO)
    # a sessão agora é do cliente, mas ainda sabe pra onde voltar
    assert s["conta_id"] == contas["cliente"]
    assert suporte.ativo(s)["admin_conta_id"] == contas["admin"]
    assert suporte.restaurar_sessao(s) == contas["admin"]
