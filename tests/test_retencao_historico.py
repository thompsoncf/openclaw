"""Retenção do histórico de WhatsApp: o botão de apagar e a regra dos 30 dias.

CONTEXTO. Até o PR #404, desconectar o WhatsApp apagava o histórico na hora. Foi
removido de propósito — desconectar acontece sem querer e o histórico com os leads
é o ativo comercial da empresa. Mas o outro lado ficou faltando: quem QUERIA
apagar não tinha como, e canal desconectado pra sempre acumulava dado pessoal sem
prazo nenhum. `finance.retencao` é esse outro lado.

O QUE ESTES TESTES TRAVAM, e por que cada um existe:

  • apagar leva conversa e mensagem de WhatsApp, e SÓ isso — lead, orçamento e
    conversa de outro canal ficam. Apagar o lead junto seria destruir o cliente
    em vez do histórico dele;
  • o escopo é por conta. Num sistema multi-inquilino, um `delete` sem `conta_id`
    apaga a empresa do vizinho;
  • os TRÊS filtros da elegibilidade automática. O prazo sozinho não basta: este
    expurgo não tem volta, e o serviço de QR reconecta sozinho ao reiniciar, sem
    passar pelo painel. Uma mensagem trafegada depois do marco é prova de que o
    canal voltou — e tem que cancelar a elegibilidade, senão a faxina apaga o
    histórico de um WhatsApp que está funcionando.

Roda com banco de TESTE separado (ver tests/conftest.py).
"""
import os
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from db.conexao import init_schema
from finance import retencao

BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"


@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4,
                       open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    with p.connection() as c:
        # conversas referencia prospeccao(id) — entra só o alvo da FK, mesma manha
        # do test_wa_qr_contatos.
        c.execute("""create table if not exists prospeccao (
                       id bigserial primary key, conta_id bigint)""")
        for mig in ("080_comunicacao_omnichannel.sql", "081_canais_config.sql",
                    "096_whatsapp_cloud.sql", "140_conversa_contato_nome.sql",
                    "141_wa_contatos.sql", "165_canal_desconectado_em.sql"):
            c.execute((BASE / mig).read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


def _conta(pool, nome="Zaq"):
    with pool.connection() as c:
        cid = c.execute("insert into contas (tipo, nome) values ('pj',%s) returning id",
                        (nome,)).fetchone()[0]
        c.commit()
    return cid


def _canal(pool, conta_id, *, ativo=True, provedor="qr", desconectado_ha_dias=None):
    """Canal de WhatsApp da conta. `desconectado_ha_dias` carimba o marco no
    passado — é assim que se testa prazo sem esperar 30 dias."""
    # o intervalo é montado em SQL, mas o "tem ou não tem marco" é decidido aqui:
    # um `case when %s is null` deixa o Postgres sem tipo pro parâmetro.
    if desconectado_ha_dias is None:
        marco = "null"
        args = (conta_id, "qr:" + str(conta_id), ativo, provedor)
    else:
        marco = "now() - (%s || ' days')::interval"
        args = (conta_id, "qr:" + str(conta_id), ativo, provedor,
                str(int(desconectado_ha_dias)))
    with pool.connection() as c:
        c.execute("""insert into canais_config
                       (conta_id, canal, identificador, ativo, provedor, desconectado_em)
                     values (%s,'whatsapp',%s,%s,%s,""" + marco + ")", args)
        c.commit()


def _conversa(pool, conta_id, *, canal="whatsapp", n_msgs=0, msgs_ha_dias=1):
    with pool.connection() as c:
        cv = c.execute("""insert into conversas (conta_id, canal, contato_ref)
                          values (%s,%s,%s) returning id""",
                       (conta_id, canal, "5586999" + str(conta_id))).fetchone()[0]
        for i in range(n_msgs):
            c.execute("""insert into mensagens (conversa_id, canal, direcao, autor,
                                               texto, criado_em)
                         values (%s,%s,'in','lead',%s,
                                 now() - (%s || ' days')::interval)""",
                      (cv, canal, f"msg {i}", str(int(msgs_ha_dias))))
        c.commit()
    return cv


def _contatos(pool, conta_id, n=3):
    with pool.connection() as c:
        for i in range(n):
            # a PK é (conta_id, numero8) — 8 dígitos finais, que é como o WhatsApp
            # devolve o mesmo número escrito de jeitos diferentes.
            c.execute("""insert into wa_contatos (conta_id, numero8, nome, da_agenda)
                         values (%s,%s,%s,true)
                         on conflict do nothing""",
                      (conta_id, f"{conta_id:04d}{i:04d}"[-8:], f"Contato {i}"))
        c.commit()


def _limpar(pool, *contas):
    with pool.connection() as c:
        for cid in contas:
            c.execute("""delete from mensagens where conversa_id in
                           (select id from conversas where conta_id=%s)""", (cid,))
            c.execute("delete from conversas where conta_id=%s", (cid,))
            c.execute("delete from wa_contatos where conta_id=%s", (cid,))
            c.execute("delete from canais_config where conta_id=%s", (cid,))
        c.commit()


def _conta_msgs(pool, conta_id, canal="whatsapp"):
    with pool.connection() as c:
        return c.execute("""select count(*) from mensagens m join conversas cv
                              on cv.id=m.conversa_id
                             where cv.conta_id=%s and cv.canal=%s""",
                         (conta_id, canal)).fetchone()[0]


# ------------------------------------------------------------------ apagar

def test_apagar_leva_conversa_e_mensagem_de_whatsapp(pool):
    cid = _conta(pool)
    _conversa(pool, cid, n_msgs=5)
    _conversa(pool, cid, n_msgs=3)
    try:
        r = retencao.apagar_historico_whatsapp(pool, cid)
        assert r["mensagens"] == 8
        assert r["conversas"] == 2
        assert _conta_msgs(pool, cid) == 0
    finally:
        _limpar(pool, cid)


def test_apagar_nao_toca_conversa_de_outro_canal(pool):
    """O botão é do WhatsApp. E-mail tem o seu próprio canal e a sua própria
    conversa — apagar os dois porque o WhatsApp desconectou seria apagar o que o
    dono nem pediu."""
    cid = _conta(pool)
    _conversa(pool, cid, canal="whatsapp", n_msgs=4)
    _conversa(pool, cid, canal="email", n_msgs=6)
    try:
        r = retencao.apagar_historico_whatsapp(pool, cid)
        assert r["mensagens"] == 4
        assert _conta_msgs(pool, cid, "email") == 6
    finally:
        _limpar(pool, cid)


def test_apagar_e_escopado_por_conta(pool):
    """Multi-inquilino: um delete sem conta_id apaga a empresa do vizinho."""
    a, b = _conta(pool, "A"), _conta(pool, "B")
    _conversa(pool, a, n_msgs=3)
    _conversa(pool, b, n_msgs=7)
    _contatos(pool, a, 2)
    _contatos(pool, b, 4)
    try:
        retencao.apagar_historico_whatsapp(pool, a)
        assert _conta_msgs(pool, a) == 0
        assert _conta_msgs(pool, b) == 7
        with pool.connection() as c:
            assert c.execute("select count(*) from wa_contatos where conta_id=%s",
                             (b,)).fetchone()[0] == 4
    finally:
        _limpar(pool, a, b)


def test_apagar_leva_a_agenda_do_celular(pool):
    """wa_contatos entra porque é agenda de terceiros (dado pessoal de quem nunca
    foi cliente) e o próximo pareamento a reconstrói sozinha."""
    cid = _conta(pool)
    _contatos(pool, cid, 5)
    try:
        r = retencao.apagar_historico_whatsapp(pool, cid)
        assert r["contatos"] == 5
        with pool.connection() as c:
            assert c.execute("select count(*) from wa_contatos where conta_id=%s",
                             (cid,)).fetchone()[0] == 0
    finally:
        _limpar(pool, cid)


def test_apagar_conta_sem_historico_nao_estoura(pool):
    cid = _conta(pool)
    try:
        r = retencao.apagar_historico_whatsapp(pool, cid)
        assert r == {"mensagens": 0, "conversas": 0, "contatos": 0}
    finally:
        _limpar(pool, cid)


# ------------------------------------------------------------------ resumo

def test_resumo_conta_o_que_vai_apagar(pool):
    """A confirmação da tela mostra estes números. Se vierem errados, o dono
    aprova um estrago diferente do que leu."""
    cid = _conta(pool)
    _conversa(pool, cid, n_msgs=4)
    _contatos(pool, cid, 2)
    try:
        d = retencao.resumo_historico(pool, cid)
        assert d["mensagens"] == 4
        assert d["conversas"] == 1
        assert d["contatos"] == 2
        assert d["de"] and d["ate"]
    finally:
        _limpar(pool, cid)


def test_resumo_vazio_nao_inventa_datas(pool):
    cid = _conta(pool)
    try:
        d = retencao.resumo_historico(pool, cid)
        assert d["mensagens"] == 0 and d["conversas"] == 0
        assert d["de"] == "" and d["ate"] == ""
    finally:
        _limpar(pool, cid)


# ------------------------------------------------- elegibilidade automática

def test_vencido_depois_do_prazo(pool):
    cid = _conta(pool)
    _canal(pool, cid, ativo=False, desconectado_ha_dias=31)
    try:
        assert cid in retencao.canais_vencidos(pool, 30)
    finally:
        _limpar(pool, cid)


def test_dentro_do_prazo_nao_vence(pool):
    cid = _conta(pool)
    _canal(pool, cid, ativo=False, desconectado_ha_dias=29)
    try:
        assert cid not in retencao.canais_vencidos(pool, 30)
    finally:
        _limpar(pool, cid)


def test_sem_marco_nunca_vence(pool):
    """Canal desconectado antes da migração 165 tem `desconectado_em` nulo. Não
    inventamos passado pra apagar histórico com base nele."""
    cid = _conta(pool)
    _canal(pool, cid, ativo=False, desconectado_ha_dias=None)
    try:
        assert cid not in retencao.canais_vencidos(pool, 30)
    finally:
        _limpar(pool, cid)


def test_canal_ativo_nao_vence_mesmo_com_marco_velho(pool):
    """Reconectou: o marco pode ter ficado carimbado, mas `ativo` manda."""
    cid = _conta(pool)
    _canal(pool, cid, ativo=True, desconectado_ha_dias=90)
    try:
        assert cid not in retencao.canais_vencidos(pool, 30)
    finally:
        _limpar(pool, cid)


def test_provedor_nao_qr_fica_fora(pool):
    """A regra é do WhatsApp por QR. Twilio/Cloud não desconectam desse jeito."""
    cid = _conta(pool)
    _canal(pool, cid, ativo=False, provedor="twilio", desconectado_ha_dias=60)
    try:
        assert cid not in retencao.canais_vencidos(pool, 30)
    finally:
        _limpar(pool, cid)


def test_mensagem_depois_do_marco_cancela_a_elegibilidade(pool):
    """A TRAVA QUE IMPORTA. O serviço de QR reconecta sozinho ao reiniciar, sem
    passar pelo painel — então o marco pode continuar carimbado num canal que
    voltou a funcionar. Mensagem trafegada depois do marco é prova melhor que
    qualquer carimbo, e tem que tirar a conta da fila: sem isto a faxina apagaria
    o histórico de um WhatsApp em uso."""
    cid = _conta(pool)
    _canal(pool, cid, ativo=False, desconectado_ha_dias=60)
    _conversa(pool, cid, n_msgs=2, msgs_ha_dias=1)   # 59 dias DEPOIS do marco
    try:
        assert cid not in retencao.canais_vencidos(pool, 30)
    finally:
        _limpar(pool, cid)


def test_mensagem_anterior_ao_marco_nao_impede(pool):
    """O caso normal: histórico todo anterior à desconexão. Se isso barrasse, a
    regra dos 30 dias nunca apagaria nada."""
    cid = _conta(pool)
    _canal(pool, cid, ativo=False, desconectado_ha_dias=40)
    _conversa(pool, cid, n_msgs=3, msgs_ha_dias=50)
    try:
        assert cid in retencao.canais_vencidos(pool, 30)
    finally:
        _limpar(pool, cid)


# ------------------------------------------------------------------ faxina

def test_faxina_apaga_o_vencido_e_poupa_o_resto(pool):
    """As asserções falam das contas DESTE teste, não de totais globais: `faxina`
    varre o banco inteiro, e num banco de teste compartilhado qualquer resíduo de
    outro módulo faria um `== 1` falhar por motivo que não é defeito do código."""
    velho, novo = _conta(pool, "Velho"), _conta(pool, "Novo")
    _canal(pool, velho, ativo=False, desconectado_ha_dias=45)
    _canal(pool, novo, ativo=False, desconectado_ha_dias=5)
    _conversa(pool, velho, n_msgs=6, msgs_ha_dias=50)
    _conversa(pool, novo, n_msgs=4, msgs_ha_dias=10)
    try:
        assert velho in retencao.canais_vencidos(pool, 30)
        assert novo not in retencao.canais_vencidos(pool, 30)
        r = retencao.faxina(pool, 30)
        assert r["erros"] == 0
        assert _conta_msgs(pool, velho) == 0     # venceu: foi apagado
        assert _conta_msgs(pool, novo) == 4      # no prazo: intacto
    finally:
        _limpar(pool, velho, novo)


def test_faxina_sem_vencidos_nao_apaga_nada(pool):
    cid = _conta(pool)
    _canal(pool, cid, ativo=True)
    _conversa(pool, cid, n_msgs=3)
    try:
        assert cid not in retencao.canais_vencidos(pool, 30)
        retencao.faxina(pool, 30)
        assert _conta_msgs(pool, cid) == 3
    finally:
        _limpar(pool, cid)


def test_prazo_e_um_so_lugar():
    """Tela e faxina leem a mesma constante. Duas fontes divergiriam do aviso que
    o dono lê ("depois de 30 dias")."""
    assert retencao.DIAS_RETENCAO == 30
