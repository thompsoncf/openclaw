"""“Levar para o lead” leva o NOME e o TELEFONE — não o número cru.

O botão do inbox de Comunicação criava o lead com `empresa = contato_ref`, ou seja o
número como veio do WhatsApp ("558694867388"), mesmo com o nome do contato já
guardado no banco (agenda do celular em wa_contatos, ou o pushName em
conversas.contato_nome). O funil enchia de lead chamado "5586…".

Banco dedicado e descartável com o schema mínimo que as rotas usam (mesmo padrão do
teste de blindagem) — não replica migrações antigas nem toca o banco compartilhado.
"""
import os
from types import SimpleNamespace

import pytest
from psycopg_pool import ConnectionPool

from web import painel_prospeccao as pp

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text not null, contato text, cnpj text, cpf text, tipo text default 'pj',
  telefone text, whatsapp text, email text, origem text,
  status text default 'novo', temperatura text default 'frio', estagio text default 'base',
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text, contato_ref text, contato_nome text, status text default 'aberta',
  responsavel_membro_id bigint, ultima_msg_em timestamptz default now());
create table wa_contatos (conta_id bigint, numero8 text, nome text,
  da_agenda boolean default false, primary key (conta_id, numero8));
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text, ativo boolean default true, cockpit_pausado boolean default false);
create table distribuicao (conta_id bigint primary key, ativo boolean default false,
  ponteiro int default 0, avisar boolean default true, aviso_template_sid text,
  atualizado_em timestamptz default now());
create table distribuicao_fila (conta_id bigint, membro_id bigint, ordem int,
  primary key (conta_id, membro_id));
"""

CONTA = 7
NUM = "558694867388"          # como o WhatsApp entrega: DDI+DDD+número, sem máscara


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_virar_lead_test"
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


def _conversa(c, *, ref=NUM, nome=None, canal="whatsapp"):
    return c.execute(
        """insert into conversas (conta_id, prospeccao_id, canal, contato_ref, contato_nome)
           values (%s,null,%s,%s,%s) returning id""", (CONTA, canal, ref, nome)).fetchone()[0]


def _logado(monkeypatch, pool, *, gerencia=True, pode_atribuir=True, membro=1):
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_acesso", lambda req: (
        {"conta_id": CONTA, "membro_id": membro, "gerencia": gerencia,
         "pode_atribuir": pode_atribuir}, None))
    return SimpleNamespace(session={}, headers={})


def _lead(c, lead_id):
    return c.execute(
        """select empresa, contato, whatsapp, telefone, email, tipo, temperatura, vendedor_id
             from prospeccao where id=%s""", (lead_id,)).fetchone()


# ------------------------------------------------------------------ pré-preenchimento

def test_prefill_usa_o_nome_da_agenda_do_celular(pool, monkeypatch):
    with pool.connection() as c:
        conv = _conversa(c, nome="Mercado da Esquina")   # pushName, mais fraco
        c.execute("insert into wa_contatos (conta_id, numero8, nome, da_agenda) values (%s,%s,%s,true)",
                  (CONTA, NUM[-8:], "Mercado Avenida"))
        c.commit()
    req = _logado(monkeypatch, pool)
    d = pp.comunicacao_virar_lead_dados(req, conversa_id=conv).body
    import json
    d = json.loads(d)
    assert d["nome"] == "Mercado Avenida"      # agenda ganha do pushName
    assert d["nome_fonte"] == "agenda"
    assert d["telefone"] == "+55 86 9486-7388"  # formatado pra conferir, não os 12 dígitos
    assert d["tipo"] == "pf"                    # quem manda mensagem é uma pessoa


def test_prefill_cai_no_nome_do_perfil_quando_nao_tem_agenda(pool, monkeypatch):
    import json
    with pool.connection() as c:
        conv = _conversa(c, nome="Joana Ribeiro")
        c.commit()
    req = _logado(monkeypatch, pool)
    d = json.loads(pp.comunicacao_virar_lead_dados(req, conversa_id=conv).body)
    assert (d["nome"], d["nome_fonte"]) == ("Joana Ribeiro", "perfil")


def test_prefill_avisa_do_lead_que_ja_usa_o_telefone(pool, monkeypatch):
    import json
    with pool.connection() as c:
        ja = c.execute("insert into prospeccao (conta_id, empresa, whatsapp) "
                       "values (%s,'Mercado Avenida ME','+5586994867388') returning id",
                       (CONTA,)).fetchone()[0]
        conv = _conversa(c, nome="Mercado Avenida")
        c.commit()
    req = _logado(monkeypatch, pool)
    d = json.loads(pp.comunicacao_virar_lead_dados(req, conversa_id=conv).body)
    # casa pelos últimos 8 dígitos: o 9 extra não atrapalha
    assert d["duplicado"] == {"id": ja, "empresa": "Mercado Avenida ME"}


def test_prefill_de_email_traz_o_endereco_e_entra_como_empresa(pool, monkeypatch):
    import json
    with pool.connection() as c:
        conv = _conversa(c, ref="compras@mercadoavenida.com.br", nome="Setor de Compras", canal="email")
        c.commit()
    req = _logado(monkeypatch, pool)
    d = json.loads(pp.comunicacao_virar_lead_dados(req, conversa_id=conv).body)
    assert d["email"] == "compras@mercadoavenida.com.br"
    assert (d["telefone"], d["tipo"], d["nome_fonte"]) == ("", "pj", "email")


# ------------------------------------------------------------------ criação do lead

def test_cria_lead_com_o_nome_e_o_telefone_conferidos(pool, monkeypatch):
    with pool.connection() as c:
        conv = _conversa(c, nome="Mercado Avenida")
        c.commit()
    req = _logado(monkeypatch, pool)
    r = pp.comunicacao_virar_lead(req, conversa_id=conv, nome="Mercado Avenida",
                                  empresa="", telefone="+55 86 9486-7388", email="",
                                  tipo="pf", vendedor_id="", temperatura="quente")
    import json
    lead_id = json.loads(r.body)["lead_id"]
    with pool.connection() as c:
        empresa, contato, whats, tel, _mail, tipo, temp, _v = _lead(c, lead_id)
        vinculo = c.execute("select prospeccao_id from conversas where id=%s", (conv,)).fetchone()[0]
    assert empresa == "Mercado Avenida"          # e NÃO "558694867388"
    assert contato == "Mercado Avenida"
    assert whats == "+" + NUM                    # o número de verdade, pro envio
    assert tel == "+55 86 9486-7388"             # legível na ficha
    assert (tipo, temp) == ("pf", "quente")
    assert vinculo == lead_id                    # a conversa passa a ser do lead


def test_em_pj_a_empresa_e_o_contato_sao_campos_diferentes(pool, monkeypatch):
    import json
    with pool.connection() as c:
        conv = _conversa(c, nome="Joana Ribeiro")
        c.commit()
    req = _logado(monkeypatch, pool)
    lead_id = json.loads(pp.comunicacao_virar_lead(
        req, conversa_id=conv, nome="Joana Ribeiro", empresa="Mercado Avenida",
        telefone="", email="", tipo="pj", vendedor_id="", temperatura="morno").body)["lead_id"]
    with pool.connection() as c:
        empresa, contato, *_ = _lead(c, lead_id)
    assert (empresa, contato) == ("Mercado Avenida", "Joana Ribeiro")


def _virar_vazio(req, conv):
    """Como o FastAPI chama a rota quando o formulário vem sem nenhum campo preenchido
    (o botão antigo, que só mandava o conversa_id)."""
    return pp.comunicacao_virar_lead(req, conversa_id=conv, nome="", empresa="",
                                     telefone="", email="", tipo="", vendedor_id="",
                                     temperatura="morno")


def test_sem_nome_digitado_ainda_assim_nao_grava_o_numero_cru(pool, monkeypatch):
    """Formulário vazio: cai na agenda/perfil, e no pior caso usa o telefone
    FORMATADO — nunca os 12 dígitos colados que ninguém reconhece."""
    import json
    with pool.connection() as c:
        conv_com_nome = _conversa(c, nome="Mercado Avenida")
        conv_sem_nome = _conversa(c, ref="5586988887777")
        c.commit()
    req = _logado(monkeypatch, pool)
    a = json.loads(_virar_vazio(req, conv_com_nome).body)["lead_id"]
    b = json.loads(_virar_vazio(req, conv_sem_nome).body)["lead_id"]
    with pool.connection() as c:
        assert _lead(c, a)[0] == "Mercado Avenida"
        assert _lead(c, b)[0] == "+55 86 98888-7777"


def test_conversa_que_ja_e_lead_so_devolve_o_lead(pool, monkeypatch):
    import json
    with pool.connection() as c:
        lead = c.execute("insert into prospeccao (conta_id, empresa) values (%s,'Já Existe') returning id",
                         (CONTA,)).fetchone()[0]
        conv = c.execute("""insert into conversas (conta_id, prospeccao_id, canal, contato_ref)
                            values (%s,%s,'whatsapp',%s) returning id""",
                         (CONTA, lead, NUM)).fetchone()[0]
        c.commit()
    req = _logado(monkeypatch, pool)
    assert json.loads(pp.comunicacao_virar_lead(req, conversa_id=conv).body)["lead_id"] == lead
    with pool.connection() as c:
        assert c.execute("select count(*) from prospeccao where conta_id=%s", (CONTA,)).fetchone()[0] == 1


def test_conversa_de_outra_conta_nao_vira_lead(pool, monkeypatch):
    import json
    with pool.connection() as c:
        conv = c.execute("""insert into conversas (conta_id, canal, contato_ref)
                            values (999,'whatsapp',%s) returning id""", (NUM,)).fetchone()[0]
        c.commit()
    req = _logado(monkeypatch, pool)
    r = pp.comunicacao_virar_lead(req, conversa_id=conv, nome="Invasor")
    assert r.status_code == 404
    assert json.loads(r.body)["erro"] == "escopo"


def test_vendedor_comum_fica_com_o_proprio_lead(pool, monkeypatch):
    import json
    with pool.connection() as c:
        conv = _conversa(c, nome="Mercado Avenida")
        c.commit()
    req = _logado(monkeypatch, pool, gerencia=False, pode_atribuir=False, membro=42)
    lead_id = json.loads(pp.comunicacao_virar_lead(
        req, conversa_id=conv, nome="Mercado Avenida", empresa="", telefone="", email="",
        tipo="pf", vendedor_id="", temperatura="morno").body)["lead_id"]
    with pool.connection() as c:
        assert _lead(c, lead_id)[7] == 42


# ------------------------------------------------------------------ rodízio
# O caso real: conta com a fila montada, leads entrando e NENHUM chegando ao vendedor.
# Eram dois problemas somados — a chave do rodízio desligada (config, resolvida no
# painel) e este caminho, que criava o lead sem nunca consultar a fila. Mesmo com a
# chave ligada, "Levar para o lead" sem escolher responsável deixava o lead órfão.

def _rodizio(c, *, ativo, membro_ids=(9,)):
    """Liga/desliga a distribuição e monta a fila com os membros dados."""
    c.execute("insert into distribuicao (conta_id, ativo) values (%s,%s) "
              "on conflict (conta_id) do update set ativo=excluded.ativo", (CONTA, ativo))
    for i, mid in enumerate(membro_ids):
        c.execute("insert into membros (id, conta_id, nome, email, papel) "
                  "values (%s,%s,%s,%s,'vendedor') on conflict do nothing",
                  (mid, CONTA, f"Vendedor {mid}", f"v{mid}@x.com"))
        c.execute("insert into distribuicao_fila (conta_id, membro_id, ordem) values (%s,%s,%s) "
                  "on conflict do nothing", (CONTA, mid, i))


def _sem_avisar(monkeypatch):
    """O aviso ao vendedor sai numa thread e manda e-mail/WhatsApp de verdade —
    fora do escopo do teste, e não é pra sair de dentro da suíte."""
    from finance import distribuicao as dist
    monkeypatch.setattr(dist, "avisar_vendedor", lambda *a, **k: None)


def _virar(req, conv, **over):
    import json
    campos = dict(nome="Mercado Avenida", empresa="", telefone="", email="",
                  tipo="pf", vendedor_id="", temperatura="morno")
    campos.update(over)
    return json.loads(pp.comunicacao_virar_lead(req, conversa_id=conv, **campos).body)["lead_id"]


def test_sem_responsavel_escolhido_o_rodizio_decide(pool, monkeypatch):
    """Era o buraco: só o inbound automático chamava a distribuição."""
    _sem_avisar(monkeypatch)
    with pool.connection() as c:
        _rodizio(c, ativo=True, membro_ids=(9,))
        conv = _conversa(c, nome="Mercado Avenida")
        c.commit()
    req = _logado(monkeypatch, pool)
    lead_id = _virar(req, conv)
    with pool.connection() as c:
        assert _lead(c, lead_id)[7] == 9


def test_o_rodizio_nao_rouba_lead_com_dono_escolhido(pool, monkeypatch):
    """Escolher na tela vence a fila — senão o dono não conseguiria direcionar."""
    _sem_avisar(monkeypatch)
    with pool.connection() as c:
        _rodizio(c, ativo=True, membro_ids=(9,))
        c.execute("insert into membros (id, conta_id, nome, papel) values (11,%s,'Bia','vendedor') "
                  "on conflict do nothing", (CONTA,))
        conv = _conversa(c, nome="Mercado Avenida")
        c.commit()
    req = _logado(monkeypatch, pool)
    lead_id = _virar(req, conv, vendedor_id="11")
    with pool.connection() as c:
        assert _lead(c, lead_id)[7] == 11


def test_com_o_rodizio_desligado_o_lead_fica_sem_dono(pool, monkeypatch):
    """Documenta o outro lado: a fila montada NÃO basta, a chave precisa estar ligada.
    É exatamente o estado em que a conta do chamado estava — e por isso o painel agora
    avisa quando a fila existe e a distribuição está desligada."""
    _sem_avisar(monkeypatch)
    with pool.connection() as c:
        _rodizio(c, ativo=False, membro_ids=(9,))
        conv = _conversa(c, nome="Mercado Avenida")
        c.commit()
    req = _logado(monkeypatch, pool)
    lead_id = _virar(req, conv)
    with pool.connection() as c:
        assert _lead(c, lead_id)[7] is None


def test_o_rodizio_tambem_marca_a_conversa(pool, monkeypatch):
    """Atribuir sem marcar a conversa deixaria a caixa de entrada dizendo 'sem
    responsável' pro lead que já tem dono."""
    _sem_avisar(monkeypatch)
    with pool.connection() as c:
        _rodizio(c, ativo=True, membro_ids=(9,))
        conv = _conversa(c, nome="Mercado Avenida")
        c.commit()
    req = _logado(monkeypatch, pool)
    _virar(req, conv)
    with pool.connection() as c:
        assert c.execute("select responsavel_membro_id from conversas where id=%s",
                         (conv,)).fetchone()[0] == 9


def test_o_lead_sobrevive_quando_a_distribuicao_explode(pool, monkeypatch):
    """O defeito que o CI pegou: sem SAVEPOINT, um erro dentro da distribuição abortava
    a transação inteira. O `except` engolia a exceção, o commit seguinte virava rollback
    e a rota respondia {"ok": true, "lead_id": N} com o lead que não existia.

    Distribuir é acessório; criar o lead é o que foi pedido. Se o rodízio cair, o lead
    fica — sem dono, e com o motivo no log."""
    from finance import distribuicao as dist

    def _explode(*a, **k):
        raise RuntimeError("banco tossiu no meio da distribuição")

    monkeypatch.setattr(dist, "atribuir_se_sem_dono", _explode)
    with pool.connection() as c:
        _rodizio(c, ativo=True, membro_ids=(9,))
        conv = _conversa(c, nome="Mercado Avenida")
        c.commit()
    req = _logado(monkeypatch, pool)
    lead_id = _virar(req, conv)
    with pool.connection() as c:
        lead = _lead(c, lead_id)
    assert lead is not None, "o lead foi prometido na resposta e precisa existir"
    assert lead[0] == "Mercado Avenida"
    assert lead[7] is None                      # sem dono, que é o aceitável aqui


def test_o_modal_avisa_quando_o_rodizio_esta_desligado(pool, monkeypatch):
    """O rótulo do campo Responsável muda conforme o rodízio: com ele ligado, deixar em
    branco é 'a fila escolhe'; desligado, é 'ninguém'."""
    import json
    with pool.connection() as c:
        _rodizio(c, ativo=False, membro_ids=(9,))
        conv = _conversa(c, nome="Mercado Avenida")
        c.commit()
    req = _logado(monkeypatch, pool)
    assert json.loads(pp.comunicacao_virar_lead_dados(req, conversa_id=conv).body)["rodizio"] is False
    with pool.connection() as c:
        c.execute("update distribuicao set ativo=true where conta_id=%s", (CONTA,))
        c.commit()
    assert json.loads(pp.comunicacao_virar_lead_dados(req, conversa_id=conv).body)["rodizio"] is True


# ------------------------------------------------------------------ trocar o dono no chat
# O dono atendia pelo inbox e não sabia de quem era a conversa: o responsável só
# aparecia (e só mudava) na ficha do lead. A mesma rota /atribuir agora serve os dois
# — form + redirect na ficha, JSON quando vem por fetch do chat.

def _req_ajax(monkeypatch, pool, **over):
    req = _logado(monkeypatch, pool, **over)
    req.headers = {"x-requested-with": "fetch"}
    return req


def _lead_com_conversa(c, *, vendedor=None):
    lead = c.execute("insert into prospeccao (conta_id, empresa, vendedor_id) "
                     "values (%s,'Mercado Avenida',%s) returning id",
                     (CONTA, vendedor)).fetchone()[0]
    conv = c.execute("""insert into conversas (conta_id, prospeccao_id, canal, contato_ref,
                          responsavel_membro_id) values (%s,%s,'whatsapp',%s,%s) returning id""",
                     (CONTA, lead, NUM, vendedor)).fetchone()[0]
    c.execute("insert into membros (id, conta_id, nome, papel) values (9,%s,'Rafael','vendedor') "
              "on conflict do nothing", (CONTA,))
    return lead, conv


def test_dono_troca_o_responsavel_pelo_chat(pool, monkeypatch):
    import json
    with pool.connection() as c:
        lead, _conv = _lead_com_conversa(c)
        c.commit()
    req = _req_ajax(monkeypatch, pool)
    r = pp.prospeccao_atribuir(req, alvo_id=lead, vendedor_id="9")
    d = json.loads(r.body)
    assert d["ok"] is True and d["vendedor_id"] == 9
    assert d["vendedor"] == "Rafael"             # a tela precisa do nome, não do id
    with pool.connection() as c:
        assert c.execute("select vendedor_id from prospeccao where id=%s",
                         (lead,)).fetchone()[0] == 9


def test_trocar_o_dono_leva_a_conversa_junto(pool, monkeypatch):
    """Sem isso o inbox seguiria mostrando o dono ANTIGO — a conversa guarda o próprio
    responsável, e é ele que a caixa de entrada lê pra filtrar por vendedor."""
    with pool.connection() as c:
        lead, conv = _lead_com_conversa(c)
        c.commit()
    req = _req_ajax(monkeypatch, pool)
    pp.prospeccao_atribuir(req, alvo_id=lead, vendedor_id="9")
    with pool.connection() as c:
        assert c.execute("select responsavel_membro_id from conversas where id=%s",
                         (conv,)).fetchone()[0] == 9


def test_tirar_o_responsavel_limpa_os_dois(pool, monkeypatch):
    with pool.connection() as c:
        lead, conv = _lead_com_conversa(c, vendedor=9)
        c.commit()
    req = _req_ajax(monkeypatch, pool)
    pp.prospeccao_atribuir(req, alvo_id=lead, vendedor_id="")
    with pool.connection() as c:
        assert c.execute("select vendedor_id from prospeccao where id=%s",
                         (lead,)).fetchone()[0] is None
        assert c.execute("select responsavel_membro_id from conversas where id=%s",
                         (conv,)).fetchone()[0] is None


def test_quem_nao_e_dono_nao_troca_o_responsavel(pool, monkeypatch):
    """Gestor e vendedor veem o dono, mas não mexem — a regra é a mesma da ficha."""
    import json
    with pool.connection() as c:
        lead, _conv = _lead_com_conversa(c, vendedor=9)
        c.commit()
    req = _req_ajax(monkeypatch, pool, pode_atribuir=False, membro=42)
    r = pp.prospeccao_atribuir(req, alvo_id=lead, vendedor_id="42")
    assert r.status_code == 403
    assert json.loads(r.body)["ok"] is False
    with pool.connection() as c:
        assert c.execute("select vendedor_id from prospeccao where id=%s",
                         (lead,)).fetchone()[0] == 9      # intacto


def test_a_ficha_continua_redirecionando(pool, monkeypatch):
    """A rota serve os dois: sem o cabeçalho de fetch, segue o comportamento antigo."""
    with pool.connection() as c:
        lead, _conv = _lead_com_conversa(c)
        c.commit()
    req = _logado(monkeypatch, pool)          # sem x-requested-with
    r = pp.prospeccao_atribuir(req, alvo_id=lead, vendedor_id="9")
    assert r.status_code == 303
    assert r.headers["location"] == f"/painel/prospeccao/{lead}"


# ------------------------------------------------------------------ formatação do número

@pytest.mark.parametrize("cru,esperado", [
    ("558694867388", "+55 86 9486-7388"),
    ("5586994867388", "+55 86 99486-7388"),
    ("+55 (86) 99486-7388", "+55 86 99486-7388"),
    ("351912345678", "+351912345678"),          # fora do BR: não inventa máscara
    ("", ""),
])
def test_telefone_formatado_pra_conferencia(cru, esperado):
    assert pp._tel_fmt_br(cru) == esperado
