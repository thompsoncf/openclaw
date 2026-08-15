"""Fila de números por alvo: a campanha não pode morrer no primeiro número.

Antes, o disparo usava só o ⭐ que a Credify marca como `provavel`. Se ele
falhasse, o lead levava `wa_status='erro'` e saía da fila pra sempre — enquanto a
base guardava dezenas de outros números, cada um já marcado com `whatsapp:
true/false`. Medido na produção: 37 alvos parados guardavam 214 números, 129 deles
com WhatsApp confirmado e nunca tentados.

Os três casos reais que desenharam a ordenação estão aqui como teste:

* **GiOlaser** — 45 números, 1 tentado. A fila precisa andar sozinha.
* **The Beauty** — o ⭐ é (83), da Paraíba; os outros 18 são (86), do Piauí. O DDD
  majoritário do próprio lead desempata sem tabela UF→DDD.
* **Evanilda Basilio** — guarda o MESMO número duas vezes com `tipo` diferente.
  Sem dedup por dígitos, a fila paga de novo pelo número que acabou de falhar.

Banco dedicado e descartável com o schema MÍNIMO que o motor usa.
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import campanhas_motor as cm

_BASE_SQL = """
create table contas (id bigserial primary key, tipo text, nome text);
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text,
  cnpj text, whatsapp text, telefone text, decisor_telefones jsonb, estagio text);
create table campanhas (id bigserial primary key, conta_id bigint, nome text,
  status text default 'ativa', wa_ativo boolean default true, limite_wa_dia int default 30,
  wa_enviados_hoje int default 0, wa_dia_contagem date, wa_template_sid text,
  wa_mmlite boolean default false, teto_wa numeric(10,2),
  wa_bloqueio text, wa_bloqueio_em timestamptz);
create table campanha_alvos (id bigserial primary key, campanha_id bigint, prospeccao_id bigint,
  status text default 'fila', wa_status text, wa_em timestamptz, wa_sid text,
  wa_erro_codigo text, wa_erro_msg text, wa_categoria text, wa_custo numeric(10,4),
  alvo_telefone text, wa_tentados jsonb not null default '[]'::jsonb,
  wa_tentativas int not null default 0);
create table campanha_eventos (id bigserial primary key, campanha_id bigint, prospeccao_id bigint,
  canal text, evento text, detalhe text, quando timestamptz default now());
create table canais_config (
  id bigserial primary key, conta_id bigint, canal text, identificador text,
  ativo boolean not null default true, token text, provedor text not null default 'twilio',
  wa_phone_id text);
"""


def _t(fmt, provavel=False, whatsapp=True, tipo="RESIDENCIAL"):
    return {"formatado": fmt, "provavel": provavel, "whatsapp": whatsapp, "tipo": tipo}


# ============================================================ ordenação (sem banco)

def test_estrela_vem_primeiro():
    fila = cm.fila_numeros([_t("(86) 98800-0002"), _t("(86) 99900-0001", provavel=True)])
    assert fila[0] == "(86) 99900-0001"


def test_comercial_com_whatsapp_antes_de_residencial():
    fila = cm.fila_numeros([
        _t("(86) 99900-0001", provavel=True),
        _t("(86) 98800-0002", tipo="RESIDENCIAL"),
        _t("(86) 98800-0003", tipo="COMERCIAL"),
    ])
    assert fila == ["(86) 99900-0001", "(86) 98800-0003", "(86) 98800-0002"]


def test_numero_sem_whatsapp_fica_de_fora():
    """A base já diz quem não tem WhatsApp — não se paga marketing por ele."""
    fila = cm.fila_numeros([
        _t("(86) 99900-0001", provavel=True),
        _t("(86) 3222-0000", whatsapp=False, tipo="COMERCIAL"),
    ])
    assert fila == ["(86) 99900-0001"]


def test_ddd_majoritario_do_lead_desempata():
    """Caso The Beauty: 18 números (86) e o ⭐ em (83). O forasteiro é a estrela —
    ela continua sendo a 1ª tentativa, mas o resto da fila prefere o DDD de casa."""
    tels = ([_t("(83) 99844-9595", provavel=True)]
            + [_t(f"(11) 9{i}000-0000") for i in range(1, 3)]
            + [_t(f"(86) 9{i}400-0000") for i in range(1, 5)])
    fila = cm.fila_numeros(tels)
    assert fila[0] == "(83) 99844-9595", "a aposta da Credify continua sendo a 1ª"
    assert fila[1].startswith("(86)"), "depois dela, o DDD de casa vem antes"


def test_numero_repetido_entra_uma_vez_so():
    """Caso Evanilda: o mesmo número guardado 2x com `tipo` diferente."""
    fila = cm.fila_numeros([
        _t("(86) 98815-7064", provavel=True),
        _t("(86) 98815-7064", tipo="COMERCIAL"),
        _t("(86) 99492-2843"),
    ])
    assert fila == ["(86) 98815-7064", "(86) 99492-2843"]


def test_ja_tentado_nao_volta():
    fila = cm.fila_numeros([_t("(86) 99900-0001", provavel=True), _t("(86) 98800-0002")],
                           ja_tentados=["86999000001"])
    assert fila == ["(86) 98800-0002"]


def test_geral_da_empresa_fecha_a_fila():
    fila = cm.fila_numeros([], whatsapp="(86) 3200-1234", telefone="(86) 98888-7777")
    assert fila == ["(86) 3200-1234", "(86) 98888-7777"]


def test_lixo_e_numero_curto_sao_descartados():
    fila = cm.fila_numeros([_t("123"), _t(""), "não é dict", _t("(86) 99900-0001")])
    assert fila == ["(86) 99900-0001"]


# ============================================================ o motor (com banco)

@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_campanha_fila_numeros_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_BASE_SQL)
        c.commit()
    yield p
    p.close()


def _cenario(pool, nome, tels, n_alvos=1):
    """Uma conta Twilio com uma campanha e `n_alvos` leads, cada um com `tels`."""
    import json
    with pool.connection() as c:
        conta = c.execute("insert into contas (tipo, nome) values ('pj',%s) returning id",
                          (nome,)).fetchone()[0]
        c.execute("""insert into canais_config (conta_id, canal, identificador, provedor)
                      values (%s,'whatsapp','whatsapp:+5586990001111','twilio')""", (conta,))
        camp = c.execute("""insert into campanhas (conta_id, nome, wa_template_sid)
                             values (%s,'C','HXabc') returning id""", (conta,)).fetchone()[0]
        for i in range(n_alvos):
            pid = c.execute("""insert into prospeccao (conta_id, empresa, decisor_telefones, estagio)
                                values (%s,%s,%s::jsonb,'prospecto') returning id""",
                            (conta, f"{nome} {i}", json.dumps(tels))).fetchone()[0]
            c.execute("insert into campanha_alvos (campanha_id, prospeccao_id) values (%s,%s)",
                      (camp, pid))
        c.commit()
    return conta, camp


def _estado(pool, camp):
    with pool.connection() as c:
        return c.execute("""select wa_status, wa_tentativas, wa_tentados
                              from campanha_alvos where campanha_id=%s order by id""",
                         (camp,)).fetchall()


class _Provedor:
    """Recusa por `codigo`, guardando pra quais números tentou."""

    def __init__(self, codigo=63024):
        self.codigo, self.numeros = codigo, []

    def enviar_template(self, c, conta_id, numero, sid, variaveis, mmlite=False):
        self.numeros.append(numero)
        return {"ok": False, "erro": "numero sem whatsapp", "codigo": self.codigo,
                "msg": "numero sem whatsapp"}


@pytest.fixture
def motor_isolado(monkeypatch):
    monkeypatch.setattr(cm, "_respondeu", lambda pool, conta_id, pid: False)
    monkeypatch.setattr(cm, "_conta_identidade", lambda c, conta_id: {"empresa": "Zaq"})


_TRES = [_t("(86) 99900-0001", provavel=True), _t("(86) 98800-0002"), _t("(86) 98800-0003")]


def test_erro_do_numero_nao_marca_o_alvo_e_a_fila_anda(pool, motor_isolado):
    """O coração: com número sobrando, wa_status fica NULL de propósito — é o que
    faz o alvo voltar na próxima passada pro motor pegar o próximo."""
    conta, camp = _cenario(pool, "Anda sozinha", _TRES)
    prov = _Provedor()
    cm._disparar_wa_campanha(pool, camp, conta, "HXabc", 10, prov)
    (status, tentativas, tentados), = _estado(pool, camp)
    assert status is None, "alvo com número sobrando não pode sair da fila"
    assert tentativas == 1 and tentados == ["86999000001"]
    assert prov.numeros == ["(86) 99900-0001"], "gasta uma mensagem por passada"

    # 2ª passada: pega o PRÓXIMO, não repete o que falhou
    cm._disparar_wa_campanha(pool, camp, conta, "HXabc", 10, prov)
    (status, tentativas, tentados), = _estado(pool, camp)
    assert prov.numeros[-1] == "(86) 98800-0002"
    assert status is None and tentativas == 2


def test_no_teto_de_tres_o_alvo_finalmente_para(pool, motor_isolado):
    conta, camp = _cenario(pool, "Teto de tres", _TRES)
    prov = _Provedor()
    for _ in range(5):                       # roda mais vezes que o teto de propósito
        cm._disparar_wa_campanha(pool, camp, conta, "HXabc", 10, prov)
    (status, tentativas, _tentados), = _estado(pool, camp)
    assert status == "erro"
    assert tentativas == cm._WA_TENTATIVAS == 3
    assert len(prov.numeros) == 3, "não pode gastar mais que o teto, nem com passada extra"
    assert len(set(prov.numeros)) == 3, "cada tentativa é um número diferente"


def test_fila_curta_para_antes_do_teto(pool, motor_isolado):
    """Dois números só: para no 2º, sem ficar rodando à toa."""
    conta, camp = _cenario(pool, "Fila curta",
                           [_t("(86) 99900-0001", provavel=True), _t("(86) 98800-0002")])
    prov = _Provedor()
    for _ in range(4):
        cm._disparar_wa_campanha(pool, camp, conta, "HXabc", 10, prov)
    (status, tentativas, _t2), = _estado(pool, camp)
    assert status == "erro" and tentativas == 2
    assert len(prov.numeros) == 2


def test_custo_nao_estoura_o_teto_com_varios_alvos(pool, motor_isolado):
    """3 alvos × teto 3 = no máximo 9 mensagens, por mais que o motor rode."""
    conta, camp = _cenario(pool, "Custo", _TRES, n_alvos=3)
    prov = _Provedor()
    for _ in range(8):
        cm._disparar_wa_campanha(pool, camp, conta, "HXabc", 10, prov)
    assert len(prov.numeros) == 9
    assert all(s == "erro" for (s, _t1, _t2) in _estado(pool, camp))


def test_erro_da_conta_nao_gasta_tentativa(pool, motor_isolado):
    """Não regride o #404: 2xxxx é da conta, trava a campanha e não queima nada."""
    conta, camp = _cenario(pool, "Erro de conta", _TRES)
    prov = _Provedor(codigo=20003)
    cm._disparar_wa_campanha(pool, camp, conta, "HXabc", 10, prov)
    (status, tentativas, tentados), = _estado(pool, camp)
    assert status is None and tentativas == 0 and tentados == []
    with pool.connection() as c:
        assert c.execute("select wa_bloqueio from campanhas where id=%s",
                         (camp,)).fetchone()[0] == "twilio_20003"


def test_envio_que_da_certo_nao_gasta_a_fila(pool, motor_isolado):
    conta, camp = _cenario(pool, "Deu certo", _TRES)

    class _Ok:
        def enviar_template(self, *a, **k):
            return {"ok": True, "sid": "SM1"}

    cm._disparar_wa_campanha(pool, camp, conta, "HXabc", 10, _Ok())
    (status, tentativas, _t3), = _estado(pool, camp)
    assert status == "enviado" and tentativas == 0


def test_numero_travado_na_base_nao_ganha_fila(pool, motor_isolado):
    """Escolha explícita do dono (checkbox na Base) não se adivinha em cima: se o
    número que ELE escolheu falha, o alvo para — não sai tentando outros."""
    conta, camp = _cenario(pool, "Travado", _TRES)
    with pool.connection() as c:
        c.execute("update campanha_alvos set alvo_telefone='(86) 97777-0000' where campanha_id=%s",
                  (camp,))
        c.commit()
    prov = _Provedor()
    for _ in range(3):
        cm._disparar_wa_campanha(pool, camp, conta, "HXabc", 10, prov)
    (status, tentativas, _t4), = _estado(pool, camp)
    assert prov.numeros == ["(86) 97777-0000"]
    assert status == "erro" and tentativas == 1


def test_alvo_sem_numero_nenhum_continua_sem_numero(pool, motor_isolado):
    conta, camp = _cenario(pool, "Sem numero", [])
    cm._disparar_wa_campanha(pool, camp, conta, "HXabc", 10, _Provedor())
    (status, _t5, _t6), = _estado(pool, camp)
    assert status == "sem_numero"
