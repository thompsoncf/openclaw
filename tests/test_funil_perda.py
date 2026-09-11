"""Por que o lead foi perdido (finance/funil_perda): a lista da conta, a exigência
e o que fica gravado. Regra 5 do fluxo V3.

O que estes testes protegem, em uma frase cada:
  * a lista é DA CONTA, semeada do nicho — não uma constante do código;
  * sem motivo, a validação RECUSA — a trava é recusar, não marcar `required`;
  * o motivo que exige descrição recusa o texto vazio;
  * a chave nunca muda ao renomear, senão o histórico de quem já foi perdido some;
  * nasce desligado: etapa sem `exige_motivo` fecha como sempre fechou;
  * tempo no funil é calculado, não guardado.
"""
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import funil_perda as fp
from finance import raio_x_perfil as rxp

MIG = Path(__file__).resolve().parent.parent / "db" / "migracoes"
CONTA = 11
AGORA = datetime(2026, 9, 11, 12, 0, tzinfo=timezone.utc)

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text, contato text,
  status text default 'novo', estagio text default 'lead', vendedor_id bigint,
  perda_motivo text, temperatura text, atualizado_em timestamptz default now(),
  criado_em timestamptz default now());
create table funil_movimentos (id bigserial primary key, conta_id bigint,
  prospeccao_id bigint, de text, para text, motivo text, membro_id bigint,
  criado_em timestamptz default now());
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text default 'vendedor', ativo boolean default true);
create table funil_etapas (id bigserial primary key, conta_id bigint, chave text,
  rotulo text, ordem int default 0);
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_funil_perda_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        # a migração de verdade: é o único jeito de o teste perceber que ela não
        # chegou em produção
        c.execute((MIG / "235_motivos_de_perda_da_conta.sql").read_text(encoding="utf-8"))
        c.execute((MIG / "236_reativar_o_lead_que_volta.sql").read_text(encoding="utf-8"))
        for ch, o in (("novo", 0), ("contatado", 10), ("perdido", 910)):
            c.execute("""insert into funil_etapas (conta_id, chave, rotulo, ordem)
                         values (%s,%s,%s,%s)""", (CONTA, ch, ch.capitalize(), o))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def c(pool):
    with pool.connection() as con:
        con.execute("delete from funil_motivos_perda")
        con.execute("delete from funil_movimentos")
        con.execute("delete from prospeccao")
        con.execute("delete from membros")
        con.execute("update funil_etapas set exige_motivo=false, reativa_para=null "
                    " where conta_id=%s", (CONTA,))
        con.commit()
        yield con
        con.rollback()


def _lead(c, status="contatado", ha_dias=10):
    return c.execute("""insert into prospeccao (conta_id, contato, status, criado_em)
                        values (%s,'Ana',%s,%s) returning id""",
                     (CONTA, status, AGORA - timedelta(days=ha_dias))).fetchone()[0]


def _exigir(c, etapa="perdido"):
    c.execute("update funil_etapas set exige_motivo=true where conta_id=%s and chave=%s",
              (CONTA, etapa))


# ------------------------------------------------------------------ a lista

def test_a_lista_nasce_do_nicho_e_vira_da_conta(c):
    """Os dez de eventos são os DA PRIME. A próxima empresa do nicho vai querer
    outros, e nenhum deles pode esperar um deploy."""
    ev = fp.motivos(c, CONTA, "eventos")
    assert len(ev) == len(rxp.semente_motivos("eventos")) == 10
    assert [m["chave"] for m in ev][:3] == ["nao_respondeu", "achou_caro", "fechou_concorrente"]
    # semeia UMA vez: a segunda leitura não duplica nem re-semeia
    assert len(fp.motivos(c, CONTA, "eventos")) == 10


def test_cada_nicho_recebe_a_lista_dele(c):
    """CLAUDE.md §6. "Data indisponível" não existe pra quem vende mensalidade;
    "ficou com o fornecedor atual" não existe pra quem vende festa."""
    ev = {k for k, _r, _d in rxp.semente_motivos("eventos")}
    rc = {k for k, _r, _d in rxp.semente_motivos("recorrente")}
    assert "data_indisponivel" in ev and "data_indisponivel" not in rc
    assert "ficou_com_atual" in rc and "ficou_com_atual" not in ev
    assert rxp.semente_motivos("produto") == (), "produto não tem funil pra perder lead"


def test_o_dono_acrescenta_um_motivo_que_e_so_dele(c):
    fp.motivos(c, CONTA, "eventos")
    r = fp.salvar_motivo(c, CONTA, rotulo="Não aceitou o regulamento", ordem=999)
    assert r["ok"] and r["chave"] == "nao_aceitou_o_regulamento"
    assert r["chave"] in {m["chave"] for m in fp.motivos(c, CONTA, "eventos")}
    assert fp.salvar_motivo(c, CONTA, rotulo="Outra coisa", chave="nao_aceitou_o_regulamento") \
        == {"ok": False, "erro": "repetido"}


def test_renomear_nao_troca_a_chave(c):
    """A chave está gravada em `perda_motivo` de todo lead já perdido. Trocá-la
    apagaria o motivo do histórico de quem já foi."""
    antes = fp.motivos(c, CONTA, "eventos")
    alvo = [m for m in antes if m["chave"] == "localizacao"][0]
    fp.salvar_motivo(c, CONTA, motivo_id=alvo["id"], rotulo="Longe demais daqui")
    depois = {m["chave"]: m["rotulo"] for m in fp.motivos(c, CONTA, "eventos")}
    assert depois["localizacao"] == "Longe demais daqui"


def test_desligar_um_motivo_tira_da_tela_e_mantem_no_historico(c):
    lista = fp.motivos(c, CONTA, "eventos")
    alvo = [m for m in lista if m["chave"] == "evento_adiado"][0]
    fp.salvar_motivo(c, CONTA, motivo_id=alvo["id"], rotulo="", ativo=False)
    assert "evento_adiado" not in {m["chave"] for m in fp.motivos(c, CONTA, "eventos")}
    assert "evento_adiado" in {m["chave"] for m in fp.motivos(c, CONTA, "eventos", so_ativos=False)}


# ------------------------------------------------------------------ a trava

def test_etapa_sem_exigencia_fecha_como_sempre_fechou(c):
    """Nasce desligado: nenhuma conta é obrigada a nada no dia do deploy."""
    assert fp.validar(c, CONTA, etapa_destino="perdido", motivo="", descricao="") == {"ok": True}


def test_sem_motivo_a_validacao_RECUSA_e_devolve_a_lista(c):
    """A trava é recusar. Um `required` na tela se contorna pelo POST, e o pedido é
    que o lead NÃO ENTRE em Perdido sem alguém dizer por quê."""
    _exigir(c)
    r = fp.validar(c, CONTA, etapa_destino="perdido", motivo="", descricao="",
                   perfil_chave="eventos")
    assert r["ok"] is False and r["erro"] == "motivo_obrigatorio"
    assert len(r["motivos"]) == 10, "a tela precisa da lista pra montar as opções"


def test_motivo_que_nao_e_da_conta_tambem_e_recusado(c):
    _exigir(c)
    r = fp.validar(c, CONTA, etapa_destino="perdido", motivo="inventei_agora", descricao="",
                   perfil_chave="eventos")
    assert r["erro"] == "motivo_obrigatorio"


def test_o_motivo_que_pede_descricao_recusa_o_texto_vazio(c):
    """"Outro — exige descrição obrigatória", do documento. E é propriedade do
    MOTIVO, não um `if` com a chave 'outro' dentro."""
    _exigir(c)
    assert fp.validar(c, CONTA, etapa_destino="perdido", motivo="outro", descricao="   ",
                      perfil_chave="eventos")["erro"] == "descricao_obrigatoria"
    assert fp.validar(c, CONTA, etapa_destino="perdido", motivo="outro",
                      descricao="Fechou com o salão do primo.", perfil_chave="eventos")["ok"]
    # os outros nove não pedem texto
    assert fp.validar(c, CONTA, etapa_destino="perdido", motivo="achou_caro", descricao="",
                      perfil_chave="eventos")["ok"]


def test_a_exigencia_e_da_etapa_e_nao_da_palavra_perdido(c):
    """Outra empresa pode exigir motivo ao arquivar, ao pausar, ao que for."""
    _exigir(c, "contatado")
    assert fp.validar(c, CONTA, etapa_destino="perdido", motivo="",
                      descricao="")["ok"] is True
    assert fp.validar(c, CONTA, etapa_destino="contatado", motivo="", descricao="",
                      perfil_chave="eventos")["erro"] == "motivo_obrigatorio"


# ------------------------------------------------------------------ o que fica

def test_a_perda_guarda_motivo_texto_data_e_etapa_de_origem(c):
    lid = _lead(c, status="perdido", ha_dias=40)
    fp.motivos(c, CONTA, "eventos")
    fp.registrar(c, CONTA, lid, motivo="outro", descricao="Fechou com o salão do primo.",
                 etapa_origem="contatado", agora=AGORA)
    f = fp.ficha(c, CONTA, lid)
    assert f["motivo"] == "outro" and f["rotulo"] == "Outro"
    assert f["descricao"] == "Fechou com o salão do primo."
    assert f["etapa"] == "contatado" and f["em"] == AGORA
    # "tempo total no funil" é calculado, não guardado: guardar derivado é guardar
    # uma segunda verdade pra sair de sincronia no primeiro backfill
    assert f["dias_no_funil"] == 40


def test_lead_que_nao_foi_perdido_nao_tem_ficha_de_perda(c):
    assert fp.ficha(c, CONTA, _lead(c)) is None


def test_o_rotulo_da_ficha_sobrevive_ao_motivo_desligado(c):
    """Desligar um motivo tira da tela de quem vai perder agora — não pode apagar o
    motivo de quem já foi perdido com ele."""
    lid = _lead(c, status="perdido")
    lista = fp.motivos(c, CONTA, "eventos")
    fp.registrar(c, CONTA, lid, motivo="localizacao", etapa_origem="contatado", agora=AGORA)
    alvo = [m for m in lista if m["chave"] == "localizacao"][0]
    fp.salvar_motivo(c, CONTA, motivo_id=alvo["id"], rotulo="", ativo=False)
    assert fp.ficha(c, CONTA, lid)["rotulo"] == "Localização / distância"


def test_tempo_no_funil_sem_data_nao_inventa_numero():
    assert fp.tempo_no_funil(None, AGORA) is None and fp.tempo_no_funil(AGORA, None) is None
    assert fp.tempo_no_funil(AGORA, AGORA - timedelta(days=3)) == 0, "nunca negativo"


# ------------------------------------------------------------------ a reativação

def _reativa(c, de="perdido", para="contatado"):
    c.execute("update funil_etapas set reativa_para=%s where conta_id=%s and chave=%s",
              (para, CONTA, de))


def test_sem_destino_declarado_nao_reativa_ninguem(c):
    """Toda conta nasce assim — é o comportamento de hoje, preservado."""
    lid = _lead(c, status="perdido")
    assert fp.reativar(c, CONTA, lid) == {"reativado": False}
    assert c.execute("select status from prospeccao where id=%s", (lid,)).fetchone()[0] == "perdido"


def test_o_cliente_que_volta_reabre_o_MESMO_cadastro(c):
    """O pedido do dono: "o sistema deve reativar o mesmo cadastro, preservando todo
    o histórico anterior"."""
    lid = _lead(c, status="perdido")
    fp.motivos(c, CONTA, "eventos")
    fp.registrar(c, CONTA, lid, motivo="nao_respondeu", etapa_origem="contatado", agora=AGORA)
    _reativa(c)
    r = fp.reativar(c, CONTA, lid)
    assert r == {"reativado": True, "de": "perdido", "para": "contatado"}
    st, temp = c.execute("select status, temperatura from prospeccao where id=%s",
                         (lid,)).fetchone()
    assert st == "contatado" and temp == "quente"
    # o motivo da perda NÃO é apagado: "preservando todo o histórico" é literal, e é
    # o que o dono quer poder analisar depois
    f = fp.ficha(c, CONTA, lid)
    assert f and f["motivo"] == "nao_respondeu" and f["etapa"] == "contatado"


def test_a_reativacao_vira_linha_no_historico(c):
    lid = _lead(c, status="perdido")
    _reativa(c)
    fp.reativar(c, CONTA, lid)
    mov = c.execute("""select de, para, motivo from funil_movimentos
                        where prospeccao_id=%s order by criado_em desc limit 1""",
                    (lid,)).fetchone()
    assert mov == ("perdido", "contatado", "reativado")


def test_destino_que_nao_existe_nao_some_com_o_lead(c):
    """Etapa renomeada ou removida deixaria o lead num status que nenhuma coluna do
    quadro mostra — e ele sumiria da tela."""
    lid = _lead(c, status="perdido")
    _reativa(c, para="etapa_que_nao_existe")
    assert fp.reativar(c, CONTA, lid) == {"reativado": False}
    assert c.execute("select status from prospeccao where id=%s", (lid,)).fetchone()[0] == "perdido"


def test_lead_que_nao_esta_na_etapa_de_reativacao_nao_e_tocado(c):
    _reativa(c)
    lid = _lead(c, status="contatado")
    assert fp.reativar(c, CONTA, lid) == {"reativado": False}
