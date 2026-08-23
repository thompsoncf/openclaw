"""Distribuição de leads por rodízio (finance/distribuicao):

- proximo_vendedor gira em círculo e avança o ponteiro; pula membro inativo;
- atribuir_se_sem_dono atribui só quando o lead não tem dono (nunca rouba);
- salvar grava a fila na ordem e zera o ponteiro; desligado não distribui.

Banco dedicado e descartável com o schema mínimo (mesmo padrão do teste de blindagem).
"""
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import distribuicao as dist

_BASE_SQL = """
create table contas (id bigserial primary key, nome text, nome_fantasia text, chip_de bigint);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  ativo boolean default true, whatsapp text, cockpit_pausado boolean default false,
  aviso_zap_em timestamptz);
create table prospeccao (id bigserial primary key, conta_id bigint, empresa text,
  vendedor_id bigint, atualizado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  responsavel_membro_id bigint, chip_id bigint);
create table distribuicao (conta_id bigint primary key, ativo boolean not null default false,
  ponteiro int not null default 0, avisar boolean not null default true,
  aviso_template_sid text, atualizado_em timestamptz not null default now(),
  aviso_zap boolean not null default false, aviso_zap_chip_id bigint, aviso_zap_texto text);
create table distribuicao_fila (conta_id bigint, membro_id bigint, ordem int not null default 0,
  primary key (conta_id, membro_id));
"""


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_distribuicao_test"
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


def _setup(c, nome_conta, n=3, ativo=True):
    conta = c.execute("insert into contas (nome) values (%s) returning id", (nome_conta,)).fetchone()[0]
    ids = []
    for i in range(n):
        ids.append(c.execute("insert into membros (conta_id, nome, email) values (%s,%s,%s) returning id",
                             (conta, f"V{i+1}", f"v{i+1}-{conta}@x.com")).fetchone()[0])
    dist.salvar(c, conta, ativo, True, ids)
    c.commit()
    return conta, ids


def _lead(c, conta):
    return c.execute("insert into prospeccao (conta_id, empresa) values (%s,'Lead') returning id",
                     (conta,)).fetchone()[0]


def test_rodizio_circular_e_ponteiro(pool):
    with pool.connection() as c:
        conta, ids = _setup(c, "Rod", 3)
        seq = [dist.proximo_vendedor(c, conta) for _ in range(7)]
        c.commit()
    assert seq == [ids[0], ids[1], ids[2], ids[0], ids[1], ids[2], ids[0]]   # gira em círculo


def test_pula_inativo(pool):
    with pool.connection() as c:
        conta, ids = _setup(c, "Inativo", 3)
        c.execute("update membros set ativo=false where id=%s", (ids[1],))   # V2 sai
        c.commit()
        seq = [dist.proximo_vendedor(c, conta) for _ in range(4)]
        c.commit()
    assert set(seq) == {ids[0], ids[2]}          # V2 nunca é escolhido
    assert ids[1] not in seq


def test_desligado_nao_distribui(pool):
    with pool.connection() as c:
        conta, ids = _setup(c, "Off", 3, ativo=False)
        assert dist.proximo_vendedor(c, conta) is None
        pid = _lead(c, conta)
        assert dist.atribuir_se_sem_dono(c, conta, pid) is None
        c.commit()
        assert c.execute("select vendedor_id from prospeccao where id=%s", (pid,)).fetchone()[0] is None


def test_atribui_lead_sem_dono_e_marca_conversa(pool):
    with pool.connection() as c:
        conta, ids = _setup(c, "Atrib", 2)
        pid = _lead(c, conta)
        conv = c.execute("insert into conversas (conta_id, prospeccao_id) values (%s,%s) returning id",
                         (conta, pid)).fetchone()[0]
        mid = dist.atribuir_se_sem_dono(c, conta, pid)
        c.commit()
    assert mid == ids[0]
    with pool.connection() as c:
        assert c.execute("select vendedor_id from prospeccao where id=%s", (pid,)).fetchone()[0] == ids[0]
        assert c.execute("select responsavel_membro_id from conversas where id=%s", (conv,)).fetchone()[0] == ids[0]


def test_nao_rouba_lead_com_dono(pool):
    with pool.connection() as c:
        conta, ids = _setup(c, "Rouba", 2)
        pid = c.execute("insert into prospeccao (conta_id, empresa, vendedor_id) values (%s,'X',%s) returning id",
                        (conta, ids[1])).fetchone()[0]
        mid = dist.atribuir_se_sem_dono(c, conta, pid)
        c.commit()
    assert mid is None
    with pool.connection() as c:
        assert c.execute("select vendedor_id from prospeccao where id=%s", (pid,)).fetchone()[0] == ids[1]


def test_salvar_guarda_template_sid(pool):
    with pool.connection() as c:
        conta, ids = _setup(c, "Tpl", 2)
        dist.salvar(c, conta, True, True, ids, aviso_template_sid="HXabc123")
        c.commit()
        assert dist.config(c, conta)["aviso_template_sid"] == "HXabc123"
        # None mantém o atual; string vazia limpa
        dist.salvar(c, conta, True, True, ids)  # aviso_template_sid=None
        c.commit()
        assert dist.config(c, conta)["aviso_template_sid"] == "HXabc123"
        dist.salvar(c, conta, True, True, ids, aviso_template_sid="")
        c.commit()
        assert dist.config(c, conta)["aviso_template_sid"] == ""


def test_avisar_usa_template_quando_sid(pool, monkeypatch):
    """Com SID → enviar_template ({{1}}=empresa); sem SID → texto livre."""
    from finance import distribuicao as d
    from finance import whatsapp_out as wo
    from finance import email_sender as es
    monkeypatch.setattr(es, "enviar_aviso", lambda *a, **k: True)
    chamado = {}
    monkeypatch.setattr(wo, "enviar_template",
                        lambda c, cid, num, sid, var, **k: chamado.update(tipo="template", sid=sid, var=var) or {"ok": True})
    monkeypatch.setattr(wo, "enviar",
                        lambda c, cid, num, txt: chamado.update(tipo="texto") or {"ok": True})
    with pool.connection() as c:
        conta, ids = _setup(c, "Av", 1)
        c.execute("update membros set whatsapp='5586999' where id=%s", (ids[0],))
        dist.salvar(c, conta, True, True, ids, aviso_template_sid="HXzap")
        c.commit()
    d.avisar_vendedor(pool, conta, ids[0], "Padaria Estrela")
    assert chamado.get("tipo") == "template"
    assert chamado.get("sid") == "HXzap" and chamado.get("var") == {"1": "Padaria Estrela"}
    # agora sem template → texto livre
    with pool.connection() as c:
        dist.salvar(c, conta, True, True, ids, aviso_template_sid="")
        c.commit()
    chamado.clear()
    d.avisar_vendedor(pool, conta, ids[0], "Padaria Estrela")
    assert chamado.get("tipo") == "texto"


def test_membros_fila_ui_ordem(pool):
    with pool.connection() as c:
        conta, ids = _setup(c, "UI", 3)
        # só os 2 primeiros na fila, na ordem invertida
        dist.salvar(c, conta, True, True, [ids[1], ids[0]])
        c.commit()
        ui = dist.membros_fila_ui(c, conta)
    na_fila = [m["id"] for m in ui if m["na_fila"]]
    assert na_fila == [ids[1], ids[0]]           # respeita a ordem salva
    assert ids[2] in [m["id"] for m in ui if not m["na_fila"]]   # o de fora aparece depois


# ------------------------------- o texto do aviso: lead novo × cliente que voltou

@pytest.fixture()
def espiao(monkeypatch):
    """O que sairia por e-mail e por push, sem mandar nada.

    `enviar_push` é trocado de propósito: o webpush deste container quebra com
    PanicException do `pyo3`, que NÃO é Exception e escapa do try/except de dentro do
    avisar_vendedor."""
    from finance import cockpit as ck
    from finance import email_sender as es

    visto = {}
    monkeypatch.setattr(es, "enviar_aviso",
                        lambda email, titulo, corpo, **k: visto.update(
                            titulo=titulo, corpo=corpo) or True)
    monkeypatch.setattr(ck, "enviar_push",
                        lambda pool, cid, mid, t, sub, link: visto.update(
                            push_titulo=t, push_sub=sub))
    return visto


def test_lead_novo_continua_com_o_texto_de_sempre(pool, espiao):
    with pool.connection() as c:
        conta, ids = _setup(c, "TxtNovo", 1)
    dist.avisar_vendedor(pool, conta, ids[0], "Padaria Estrela")
    assert espiao["titulo"] == "🔥 Novo lead pra você: Padaria Estrela"
    assert "rodízio de leads" in espiao["corpo"]


def test_retomada_diz_que_o_cliente_voltou(pool, espiao):
    """Chamar de "novo lead" quem a empresa atende há meses foi o que motivou isto:
    depois de um re-pareamento, dez clientes antigos da Doce Mell foram anunciados
    assim no mesmo dia."""
    with pool.connection() as c:
        conta, ids = _setup(c, "TxtVolta", 1)
    dist.avisar_vendedor(pool, conta, ids[0], "Ateliê Festas", retomada=True)
    assert espiao["titulo"] == "💬 Cliente voltou a falar: Ateliê Festas"
    assert "Novo lead" not in espiao["titulo"]
    assert "rodízio" not in espiao["corpo"], "não caiu no rodízio agora, já era dela"
    assert "histórico" in espiao["corpo"]


def test_o_push_nao_contradiz_o_email(pool, espiao):
    """Dois avisos da mesma mensagem dizendo coisas diferentes é pior que um só."""
    with pool.connection() as c:
        conta, ids = _setup(c, "TxtPush", 1)
    dist.avisar_vendedor(pool, conta, ids[0], "Ateliê Festas", retomada=True)
    assert "Voltou a falar" in espiao["push_titulo"]
    assert "Novo lead" not in espiao["push_titulo"]
    espiao.clear()
    dist.avisar_vendedor(pool, conta, ids[0], "Padaria Estrela")
    assert espiao["push_titulo"] == "🔥 Novo lead: Padaria Estrela"


def test_retomada_e_parametro_so_por_nome(pool, espiao):
    """Keyword-only de propósito: `avisar_vendedor(pool, conta, membro, empresa, True)`
    posicional passaria despercebido numa revisão e mudaria o texto sem querer."""
    with pool.connection() as c:
        conta, ids = _setup(c, "TxtKw", 1)
    with pytest.raises(TypeError):
        dist.avisar_vendedor(pool, conta, ids[0], "X", True)


# ------------------------------- aviso de lead por WhatsApp (migração 185)

@pytest.fixture()
def zap(monkeypatch):
    """Espia o envio por WhatsApp sem mandar nada, e neutraliza e-mail e push.

    `enviar_push` é trocado porque o webpush deste container quebra com
    PanicException do `pyo3`, que não é Exception e escapa do try/except interno."""
    from finance import cockpit as ck
    from finance import email_sender as es
    from finance import whatsapp_out as wo

    visto = {"enviados": [], "templates": []}
    monkeypatch.setattr(es, "enviar_aviso", lambda *a, **k: True)
    monkeypatch.setattr(ck, "enviar_push", lambda *a, **k: 0)
    monkeypatch.setattr(wo, "enviar",
                        lambda c, cid, num, txt, **kw: visto["enviados"].append(
                            {"numero": num, "texto": txt, "chip": kw.get("chip_id")}) or {"ok": True})
    monkeypatch.setattr(wo, "enviar_template",
                        lambda c, cid, num, sid, var, **k: visto["templates"].append(sid) or {"ok": True})
    return visto


def _conta_com_zap(c, nome, *, texto=None, chip=None, ligado=True):
    """Uma conta com o rodízio e o aviso por WhatsApp ligados, e um vendedor com número."""
    conta, ids = _setup(c, nome, 1)
    c.execute("update contas set nome_fantasia=%s where id=%s", ("Doce Mell", conta))
    c.execute("update membros set whatsapp='5586999990001' where id=%s", (ids[0],))
    dist.salvar(c, conta, True, True, ids, aviso_zap=ligado,
                aviso_zap_chip_id=chip or 0, aviso_zap_texto=texto)
    c.commit()
    return conta, ids[0]


def test_o_aviso_sai_pelo_chip_escolhido(pool, zap):
    """O ponto do pedido: o aviso interno não precisa sair pelo número que atende
    cliente o dia inteiro. `whatsapp_out.enviar` já aceitava `chip_id` — faltava alguém
    passar."""
    with pool.connection() as c:
        conta, mid = _conta_com_zap(c, "ZapChip", chip=4242)
    dist.avisar_vendedor(pool, conta, mid, "Melry", lead_id=9)
    assert len(zap["enviados"]) == 1
    assert zap["enviados"][0]["chip"] == 4242


def test_sem_chip_escolhido_sai_pelo_principal(pool, zap):
    """Nulo = chip da própria empresa, que é o comportamento de sempre."""
    with pool.connection() as c:
        conta, mid = _conta_com_zap(c, "ZapPrinc")
    dist.avisar_vendedor(pool, conta, mid, "Melry", lead_id=9)
    assert zap["enviados"][0]["chip"] is None


def test_as_variaveis_viram_o_texto_de_verdade(pool, zap):
    with pool.connection() as c:
        conta, mid = _conta_com_zap(
            c, "ZapVars",
            texto="{vendedor}: {lead} chamou a {empresa}. \"{primeira_mensagem}\" -> {link}")
    dist.avisar_vendedor(pool, conta, mid, "Melry", lead_id=9,
                         primeira="Boa tarde, faz bolo de pote?")
    t = zap["enviados"][0]["texto"]
    assert "V1" in t and "Melry" in t and "Doce Mell" in t
    assert "Boa tarde, faz bolo de pote?" in t
    assert "/cockpit/lead/9" in t
    assert "{" not in t, f"sobrou variável sem trocar: {t}"


def test_a_mensagem_do_cliente_entra_cortada(pool, zap):
    """120 caracteres: o suficiente pra decidir se corre, sem virar paredão no
    WhatsApp de quem só quer o resumo."""
    with pool.connection() as c:
        conta, mid = _conta_com_zap(c, "ZapCorte", texto="{primeira_mensagem}")
    dist.avisar_vendedor(pool, conta, mid, "Melry", lead_id=9, primeira="x" * 500)
    assert zap["enviados"][0]["texto"] == "x" * dist.PRIMEIRA_MAX


def test_chave_solta_no_texto_nao_derruba_o_aviso(pool, zap):
    """O texto é digitado por gente. `str.format` levantaria KeyError num '{' solto e
    o vendedor ficaria sem aviso nenhum — dentro de uma thread, calado."""
    with pool.connection() as c:
        conta, mid = _conta_com_zap(c, "ZapChave", texto="50% de desconto { hoje {lead}")
    dist.avisar_vendedor(pool, conta, mid, "Melry", lead_id=9)
    assert zap["enviados"][0]["texto"] == "50% de desconto { hoje Melry"


def test_variavel_desconhecida_fica_a_vista(pool, zap):
    """Sumir com ela seria pior: quem escreveu {nome} em vez de {lead} precisa ver
    que não funcionou."""
    with pool.connection() as c:
        conta, mid = _conta_com_zap(c, "ZapDesc", texto="oi {nome}")
    dist.avisar_vendedor(pool, conta, mid, "Melry", lead_id=9)
    assert zap["enviados"][0]["texto"] == "oi {nome}"


def test_desligado_nao_manda_whatsapp(pool, zap):
    """Padrão da migração: mandar no WhatsApp de alguém não começa ligado."""
    with pool.connection() as c:
        conta, mid = _conta_com_zap(c, "ZapOff", ligado=False)
    dist.avisar_vendedor(pool, conta, mid, "Melry", lead_id=9)
    assert zap["enviados"] == []


def test_retomada_nao_manda_whatsapp(pool, zap):
    """Decidido com o dono: quem volta a falar já está no inbox do vendedor — e é
    justamente o caso que virou enxurrada na reimportação de 22/08."""
    with pool.connection() as c:
        conta, mid = _conta_com_zap(c, "ZapVolta")
    dist.avisar_vendedor(pool, conta, mid, "Ateliê Festas", lead_id=9, retomada=True)
    assert zap["enviados"] == []


def test_o_freio_segura_o_segundo_aviso_do_mesmo_vendedor(pool, zap):
    with pool.connection() as c:
        conta, mid = _conta_com_zap(c, "ZapFreio")
    dist.avisar_vendedor(pool, conta, mid, "Lead 1", lead_id=1)
    dist.avisar_vendedor(pool, conta, mid, "Lead 2", lead_id=2)
    assert len(zap["enviados"]) == 1, "dois WhatsApps seguidos no celular do vendedor"


def test_o_freio_e_por_vendedor_nao_por_conta(pool, zap):
    """Teto por conta calaria o aviso de quem ainda não tinha recebido nada — e a fila
    do rodízio existe justamente pra repartir entre vários."""
    with pool.connection() as c:
        conta, ids = _setup(c, "ZapDois", 2)
        c.execute("update contas set nome_fantasia='X' where id=%s", (conta,))
        c.execute("update membros set whatsapp='5586999990002' where conta_id=%s", (conta,))
        dist.salvar(c, conta, True, True, ids, aviso_zap=True, aviso_zap_texto="oi")
        c.commit()
    dist.avisar_vendedor(pool, conta, ids[0], "Lead 1", lead_id=1)
    dist.avisar_vendedor(pool, conta, ids[1], "Lead 2", lead_id=2)
    assert len(zap["enviados"]) == 2


def test_freio_nao_carimba_quem_nao_ia_receber(pool, zap):
    """Com o aviso desligado o carimbo não pode ser gravado: se fosse, ligar o recurso
    calaria o primeiro aviso de verdade por 2 minutos, sem motivo nenhum."""
    with pool.connection() as c:
        conta, mid = _conta_com_zap(c, "ZapCarimbo", ligado=False)
    dist.avisar_vendedor(pool, conta, mid, "Lead 1", lead_id=1)
    with pool.connection() as c:
        marca = c.execute("select aviso_zap_em from membros where id=%s", (mid,)).fetchone()[0]
    assert marca is None


def test_vendedor_sem_numero_nao_quebra_nem_carimba(pool, zap):
    with pool.connection() as c:
        conta, ids = _setup(c, "ZapSemNum", 1)
        dist.salvar(c, conta, True, True, ids, aviso_zap=True, aviso_zap_texto="oi")
        c.commit()
    dist.avisar_vendedor(pool, conta, ids[0], "Melry", lead_id=9)
    assert zap["enviados"] == []
    with pool.connection() as c:
        assert c.execute("select aviso_zap_em from membros where id=%s",
                         (ids[0],)).fetchone()[0] is None


def test_o_texto_padrao_vale_pra_quem_nunca_editou(pool, zap):
    """O padrão entra na LEITURA, não na coluna — assim melhorar o texto de fábrica
    alcança quem nunca mexeu, sem migração de dados."""
    with pool.connection() as c:
        conta, mid = _conta_com_zap(c, "ZapPadrao", texto="")
        assert dist.config(c, conta)["aviso_zap_texto"] == dist.TEXTO_ZAP_PADRAO
    dist.avisar_vendedor(pool, conta, mid, "Melry", lead_id=9, primeira="oi")
    assert "Melry" in zap["enviados"][0]["texto"]


def test_template_ainda_manda_por_template(pool, zap):
    """Twilio/Meta não têm chip pra escolher e a janela de 24h obriga template — esse
    caminho não pode ter sido trocado pelo texto livre."""
    with pool.connection() as c:
        conta, mid = _conta_com_zap(c, "ZapTpl", texto="ignorado")
        dist.salvar(c, conta, True, True, [mid], aviso_template_sid="HXzap")
        c.commit()
    dist.avisar_vendedor(pool, conta, mid, "Melry", lead_id=9)
    assert zap["templates"] == ["HXzap"] and zap["enviados"] == []


def test_o_link_do_aviso_aponta_pra_ficha_do_lead(pool, zap):
    """O defeito que o próprio código já apontava em finance/cockpit.py: o aviso do
    rodízio mandava pro login e o vendedor tinha que caçar de quem era."""
    with pool.connection() as c:
        conta, mid = _conta_com_zap(c, "ZapLink", texto="{link}")
    dist.avisar_vendedor(pool, conta, mid, "Melry", lead_id=77)
    assert zap["enviados"][0]["texto"].endswith("/cockpit/lead/77")


def test_o_email_sai_mesmo_se_a_parte_do_whatsapp_quebrar(pool, zap, monkeypatch):
    """O aviso que não pode faltar é o e-mail. Tudo que só serve ao WhatsApp — nome da
    empresa, freio — roda dentro da condição do WhatsApp, senão uma falha ali derruba
    os três avisos de uma vez, calado, dentro da thread."""
    from finance import email_sender as es

    saiu = []
    monkeypatch.setattr(es, "enviar_aviso", lambda *a, **k: saiu.append(a[1]) or True)
    monkeypatch.setattr(dist, "_pode_zap_agora",
                        lambda *a, **k: (_ for _ in ()).throw(RuntimeError("freio quebrou")))
    with pool.connection() as c:
        conta, mid = _conta_com_zap(c, "ZapBlinda")
    dist.avisar_vendedor(pool, conta, mid, "Melry", lead_id=9)
    assert saiu == ["🔥 Novo lead pra você: Melry"], "o e-mail foi junto com o WhatsApp"
