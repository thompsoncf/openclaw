"""finance/wa_silencio.py::deve_avisar — o alarme de "conectado e sem receber".

O caso real (Confeitaria Doce Mell, 17/08/2026): a sessão foi repareada do zero, o
cofre de chaves do Signal ficou horas se reconstruindo, e as mensagens chegavam como
frame sem virar conversa. O painel dizia CONECTADO o tempo todo. A empresa passou
mais de duas horas sem receber nada e ninguém soube — a faixa do painel avisava, mas
aviso em tela só serve pra quem está com a tela aberta.

Este teste guarda os três freios que impedem o alarme de virar spam. Eles importam
tanto quanto o alarme: alarme que toca à toa é alarme que o dono aprende a ignorar,
e aí ele não serve nem no dia que importa.

Teste puro — deve_avisar não toca banco nem rede.
"""
import pytest

from finance import wa_silencio as ws

EP = "2026-08-17T12:39:41+00:00"        # carimbo da última recebida (o "episódio")
OUTRO_EP = "2026-08-17T15:02:00+00:00"
HORA_OK = 14                             # dentro do horário comercial


def test_o_caso_da_doce_mell_dispara():
    """Os números reais de 17/08: 163min mudos, 468 recebidas em 7 dias (~11min entre
    mensagens). Quinze vezes o ritmo dela — é exatamente o alarme."""
    assert ws.deve_avisar(163, 468, HORA_OK, None, EP) is True


def test_a_conta_zaq_no_MESMO_silencio_nao_dispara():
    """O contra-exemplo que calibrou a regra, com números reais do mesmo instante: a
    ZAQ estava 165min sem receber — MAIS que a Doce Mell — e não é incidente, porque
    ela recebe 108 vezes em 7 dias (~47min entre mensagens). Mesma duração, ritmos
    diferentes: limiar fixo confundiria as duas."""
    assert ws.deve_avisar(165, 108, HORA_OK, None, EP) is False
    # e a mesma conta, parada tempo demais até pro ritmo dela, dispara
    assert ws.deve_avisar(600, 108, HORA_OK, None, EP) is True


def test_silencio_curto_nao_dispara():
    """Uma hora quieta é almoço, não incidente. E o vigia do wa-qr ainda tenta
    religar sessão muda sozinho antes disso."""
    assert ws.deve_avisar(45, 468, HORA_OK, None, EP) is False
    assert ws.deve_avisar(ws._SILENCIO_MIN - 1, 5000, HORA_OK, None, EP) is False


def test_piso_protege_a_conta_muito_movimentada():
    """Ritmo de 4min entre mensagens daria um limiar de 24min — alarme a cada respiro
    do cliente. O piso segura isso."""
    assert ws.limiar_de_silencio(1219) == ws._SILENCIO_MIN
    assert ws.deve_avisar(30, 1219, HORA_OK, None, EP) is False
    assert ws.deve_avisar(120, 1219, HORA_OK, None, EP) is True


def test_conta_parada_nao_vira_alarme():
    """Empresa que recebe pouco fica horas quieta sem nada de errado — pra ela o
    silêncio é o estado normal, e alarme no normal ninguém escuta."""
    assert ws.deve_avisar(300, 3, HORA_OK, None, EP) is False
    assert ws.deve_avisar(3000, 0, HORA_OK, None, EP) is False


@pytest.mark.parametrize("hora", [0, 3, 7, 20, 23])
def test_fora_do_horario_comercial_espera(hora):
    """Silêncio às 3h da manhã é o esperado. Acordar o dono pra dizer isso queima o
    alarme pra quando ele importar."""
    assert ws.deve_avisar(300, 468, hora, None, EP) is False


@pytest.mark.parametrize("hora", [8, 12, 19])
def test_dentro_do_horario_dispara(hora):
    assert ws.deve_avisar(300, 468, hora, None, EP) is True


def test_um_aviso_por_episodio():
    """O ticker roda a cada 2 minutos: sem dedup, o mesmo silêncio viraria dezenas de
    mensagens. O episódio é a última recebida — enquanto for a mesma, é o mesmo
    silêncio."""
    assert ws.deve_avisar(300, 468, HORA_OK, EP, EP) is False


def test_novo_silencio_depois_de_voltar_dispara_de_novo():
    """A conta voltou a receber (episódio mudou) e emudeceu outra vez: é incidente
    novo e tem que avisar. É o que faz o dedup se resetar sozinho, sem faxina."""
    assert ws.deve_avisar(300, 468, HORA_OK, EP, OUTRO_EP) is True


def test_sem_episodio_nao_avisa():
    """Conta que nunca recebeu nada não tem carimbo pra deduplicar — avisar aqui
    viraria repetição a cada volta do ticker."""
    assert ws.deve_avisar(300, 468, HORA_OK, None, None) is False


def test_nunca_recebeu_nao_e_silencio():
    """minutos=None é conta nova, não conta muda."""
    assert ws.deve_avisar(None, 468, HORA_OK, None, EP) is False


def test_estreia_em_ensaio_ninguem_recebe():
    """Alarme novo não estreia mandando mensagem pra cliente. O padrão é ensaio: ele
    avalia e escreve no log o que TERIA mandado, e quem viu os disparos decide."""
    assert ws.envia_de_verdade(35, "ensaio", "") is False
    assert ws.envia_de_verdade(35, "", "") is False
    assert ws.envia_de_verdade(35, None, None) is False


def test_ligar_uma_conta_de_cada_vez():
    """O jeito pretendido de ligar: conta por conta, depois de ver o disparo dela."""
    assert ws.envia_de_verdade(35, "ensaio", "35") is True
    assert ws.envia_de_verdade(34, "ensaio", "35") is False
    assert ws.envia_de_verdade(34, "ensaio", "35,34") is True
    # tolerante ao jeito que a pessoa digita na mão
    assert ws.envia_de_verdade(34, "ensaio", " 35 , 34 ") is True


def test_ligar_geral():
    assert ws.envia_de_verdade(99, "ligado", "") is True
    assert ws.envia_de_verdade(99, "LIGADO", "") is True


def test_texto_diz_o_que_fazer():
    """Aviso que só assusta não ajuda: tem que ter o teste (mandar mensagem de outro
    celular) e o que NÃO fazer (Desconectar apaga o cofre e custa horas)."""
    tg, email = ws._texto("Confeitaria Doce Mell", 133)
    for t in (tg, email):
        assert "Confeitaria Doce Mell" in t
        assert "outro celular" in t
        assert "Desconectar" in t
    assert "2 horas" in email and "2 horas" in tg
    assert "45 minutos" in ws._texto("X", 45)[1]


# ===================================================================== 02/09/2026
# A UNIDADE É O CHIP, NÃO A EMPRESA
#
# O caso real: a Prime Eventos tem dois chips. O 1 (CP Zarb, número principal, que
# recebia ~100 mensagens de cliente por dia) parou de receber ao vivo em 31/08 às
# 17:24. O 2 (CP Thiago) seguiu recebendo o tempo todo. Somando os dois, a empresa
# nunca ficou 100 minutos calada — e o alarme, que agrupava por `conta_id` e
# ignorava o `chip_id`, passou 47 HORAS sem tocar. Quem descobriu foi o dono,
# mandando "oi teste" pros dois números e vendo chegar em um só.
#
# Estes testes tocam banco porque é ali que o defeito morava: a régua pura
# (`deve_avisar`) sempre esteve certa — quem mentia era a CONSULTA que a alimentava.

import os
from datetime import datetime, timedelta, timezone

from psycopg_pool import ConnectionPool

EMPRESA = 34
CHIP2 = 36

_SQL_CHIP = """
create table app_config (chave text primary key, valor text not null,
  atualizado_em timestamptz not null default now());
create table contas (id bigserial primary key, nome text, nome_fantasia text,
  email text, chip_de bigint);
create table canais_config (id bigserial primary key, conta_id bigint,
  canal text, rotulo text, ativo boolean default true);
create table conversas (id bigserial primary key, conta_id bigint, chip_id bigint,
  canal text);
create table mensagens (id bigserial primary key, conversa_id bigint,
  direcao text, texto text default '', criado_em timestamptz default now());
"""


@pytest.fixture
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_wa_silencio_chip"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s",
                  (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True,
                       kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL_CHIP)
        c.commit()
    yield p
    p.close()


def _empresa(pool, *, dois_chips=True):
    """A Prime como ela é: principal batizado no `canais_config.rotulo`, secundário
    batizado no `contas.nome` — os dois lugares que o Inbox lê."""
    with pool.connection() as c:
        c.execute("insert into contas (id, nome, nome_fantasia, email) "
                  "values (%s,'MANOEL SOARES','PRIME EVENTOS','dono@prime.com')", (EMPRESA,))
        c.execute("insert into canais_config (conta_id, canal, rotulo) "
                  "values (%s,'whatsapp','CP Zarb')", (EMPRESA,))
        if dois_chips:
            c.execute("insert into contas (id, nome, chip_de) values (%s,'CP Thiago',%s)",
                      (CHIP2, EMPRESA))
            c.execute("insert into canais_config (conta_id, canal, rotulo) "
                      "values (%s,'whatsapp',null)", (CHIP2,))
        c.commit()


def _conversa(pool, chip_id):
    with pool.connection() as c:
        cid = c.execute("insert into conversas (conta_id, chip_id, canal) "
                        "values (%s,%s,'whatsapp') returning id",
                        (EMPRESA, chip_id)).fetchone()[0]
        c.commit()
    return cid


def _entradas(pool, conversa_id, quantas, *, ate_min_atras):
    """`quantas` mensagens de entrada, a última `ate_min_atras` minutos atrás e as
    outras de 10 em 10 minutos pra trás — todas dentro dos 7 dias, que é a janela
    de onde sai o ritmo da conta (e, dele, o limiar de silêncio)."""
    agora = datetime.now(timezone.utc)
    with pool.connection() as c:
        for i in range(quantas):
            quando = (agora - timedelta(minutes=ate_min_atras) if i == 0
                      else agora - timedelta(minutes=ate_min_atras + 10 * (i + 1)))
            c.execute("insert into mensagens (conversa_id, direcao, criado_em) "
                      "values (%s,'in',%s)", (conversa_id, quando))
        c.commit()


def _prime_com_o_chip_1_morto(pool):
    """O retrato de 02/09, com as proporções reais: o chip 1 recebia ~100 por dia
    (480 em 7 dias, ritmo de ~10min) e está mudo há 4h; o chip 2 recebe bem menos
    (120 em 7 dias, ritmo de ~42min) e acabou de receber."""
    _empresa(pool)
    _entradas(pool, _conversa(pool, None), 480, ate_min_atras=240)
    _entradas(pool, _conversa(pool, CHIP2), 120, ate_min_atras=3)


def test_a_consulta_devolve_uma_linha_por_chip(pool):
    _prime_com_o_chip_1_morto(pool)
    with pool.connection() as c:
        linhas = ws._chips_com_whatsapp(c)
    por_chip = {r[3]: r for r in linhas}
    assert set(por_chip) == {EMPRESA, CHIP2}, "faltou (ou sobrou) chip"
    assert por_chip[EMPRESA][4] is True and por_chip[EMPRESA][5] == "CP Zarb"
    assert por_chip[CHIP2][4] is False and por_chip[CHIP2][5] == "CP Thiago"


def test_o_silencio_de_cada_chip_e_o_dele(pool):
    """O coração da correção: o chip 1 está mudo há 4h mesmo com o 2 recebendo."""
    _prime_com_o_chip_1_morto(pool)
    with pool.connection() as c:
        por_chip = {r[3]: r for r in ws._chips_com_whatsapp(c)}
    assert por_chip[EMPRESA][6] > 200, "o chip mudo devia acusar horas de silêncio"
    assert por_chip[CHIP2][6] < 30, "o chip que recebe não pode aparecer calado"
    # e as contagens não se misturam — é o ritmo de cada um que define o limiar
    assert por_chip[EMPRESA][7] == 480 and por_chip[CHIP2][7] == 120


def test_o_chip_que_recebe_nao_esconde_mais_o_que_morreu(pool, monkeypatch):
    """A REGRESSÃO, com os papéis do caso real. Antes de 02/09 esta passada não
    avisava nada: somando os dois chips a empresa tinha recebido há 3 minutos."""
    _prime_com_o_chip_1_morto(pool)
    ws.config_app.set_config(pool, "wa_silencio_contas", str(EMPRESA))
    avisos = []
    monkeypatch.setattr(ws.notificar, "enviar_para_dono",
                        lambda p, cid, txt: avisos.append((cid, txt)) or True)
    assert ws.rodar(pool, hora_brt=HORA_OK) == 1
    assert len(avisos) == 1
    conta_avisada, texto = avisos[0]
    assert conta_avisada == EMPRESA, "o aviso vai pro dono da EMPRESA, não pro chip"
    assert "CP Zarb" in texto, "o aviso tem que dizer QUAL chip parou"
    assert "CP Thiago" not in texto


def test_a_regua_antiga_teria_deixado_passar(pool):
    """Prova que a correção era necessária, e não gosto: a consulta ANTIGA — a que
    agrupava por `conta_id` sem olhar o `chip_id` — é reproduzida aqui e dá a
    empresa como viva. Sem este teste, "por chip" seria uma afirmação sem
    contraprova, e o próximo refactor poderia desfazer sem ninguém notar."""
    _prime_com_o_chip_1_morto(pool)
    with pool.connection() as c:
        antiga = c.execute(
            """select extract(epoch from now() - max(m.criado_em)
                              filter (where m.direcao='in'))/60
                 from contas ct
                 join canais_config cc
                   on cc.conta_id = ct.id and cc.canal='whatsapp' and cc.ativo
                 left join conversas cv on cv.conta_id = ct.id and cv.canal='whatsapp'
                 left join mensagens m on m.conversa_id = cv.id
                where ct.id=%s group by ct.id""", (EMPRESA,)).fetchone()[0]
        por_chip = {r[3]: r for r in ws._chips_com_whatsapp(c)}
    assert antiga < 30, "a régua antiga precisa mesmo dar a empresa como viva"
    assert not ws.deve_avisar(antiga, 600, HORA_OK, None, "ep"), (
        "era este o buraco: pela empresa, o silêncio do chip 1 nem existia")
    assert ws.deve_avisar(por_chip[EMPRESA][6], int(por_chip[EMPRESA][7]),
                          HORA_OK, None, "ep"), "e pelo chip ele tem que aparecer"


def test_avisar_um_chip_nao_gasta_o_dedup_do_outro(pool, monkeypatch):
    """Cada chip tem seu episódio. Se a chave fosse compartilhada, o segundo chip a
    emudecer ficaria calado pra sempre — o defeito de novo, por outra porta."""
    _prime_com_o_chip_1_morto(pool)
    ws.config_app.set_config(pool, "wa_silencio_contas", str(EMPRESA))
    monkeypatch.setattr(ws.notificar, "enviar_para_dono", lambda p, cid, txt: True)
    assert ws.rodar(pool, hora_brt=HORA_OK) == 1        # avisa o chip 1
    assert ws.rodar(pool, hora_brt=HORA_OK) == 0        # mesmo episódio, não repete
    # agora o chip 2 também emudece
    with pool.connection() as c:
        c.execute("update mensagens set criado_em = criado_em - interval '5 hours' "
                  " where conversa_id in (select id from conversas where chip_id=%s)",
                  (CHIP2,))
        c.commit()
    assert ws.rodar(pool, hora_brt=HORA_OK) == 1, "o chip 2 tinha que avisar por conta própria"


def test_empresa_de_um_chip_so_nao_muda_de_comportamento(pool, monkeypatch):
    """Três das quatro empresas com WhatsApp em produção têm um chip só. Pra elas o
    aviso continua idêntico — sem nome de chip, que ali não quer dizer nada."""
    _empresa(pool, dois_chips=False)
    _entradas(pool, _conversa(pool, None), 480, ate_min_atras=240)
    ws.config_app.set_config(pool, "wa_silencio_contas", str(EMPRESA))
    avisos = []
    monkeypatch.setattr(ws.notificar, "enviar_para_dono",
                        lambda p, cid, txt: avisos.append(txt) or True)
    assert ws.rodar(pool, hora_brt=HORA_OK) == 1
    assert "PRIME EVENTOS" in avisos[0]
    assert "chip *" not in avisos[0], "empresa de um chip só não deve nomear chip"


def test_o_principal_fica_com_a_chave_antiga_do_dedup():
    """Mudar o formato pra todo mundo zeraria o dedup das empresas de um chip só, e
    elas reavisariam um episódio já avisado no primeiro ticker depois do deploy."""
    assert ws.chave_dedup(34, 34, True, True) == "wa_silencio_aviso_34"
    assert ws.chave_dedup(34, 34, True, False) == "wa_silencio_ensaio_34"
    assert ws.chave_dedup(34, 36, False, True) == "wa_silencio_aviso_34_c36"
    assert ws.chave_dedup(34, 36, False, False) == "wa_silencio_ensaio_34_c36"


def test_o_texto_do_chip_diz_o_numero_certo():
    tg, email = ws._texto("PRIME EVENTOS", 240, "CP Zarb")
    for t in (tg, email):
        assert "CP Zarb" in t and "PRIME EVENTOS" in t
        assert "outro celular" in t and "Desconectar" in t
    assert "chip CP Zarb" in email


def test_chip_desativado_sai_da_conta(pool):
    """`canais_config.ativo` false = chip que não está no ar. Alarmar por ele seria
    avisar sobre silêncio de um número que ninguém espera que receba."""
    _prime_com_o_chip_1_morto(pool)
    with pool.connection() as c:
        c.execute("update canais_config set ativo=false where conta_id=%s", (CHIP2,))
        c.commit()
    with pool.connection() as c:
        assert {r[3] for r in ws._chips_com_whatsapp(c)} == {EMPRESA}
