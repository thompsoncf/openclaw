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
from datetime import datetime, timedelta, timezone

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
  nicho_id bigint references nichos(id));
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text default 'vendedor', ativo boolean default true, whatsapp text,
  cockpit_push_ativo boolean default true, cockpit_pausado boolean default false);
create table prospeccao (id bigserial primary key, conta_id bigint, vendedor_id bigint,
  empresa text, cnpj text, segmento text, cidade text, uf text, contato text, cargo text,
  telefone text, whatsapp text, email text, status text default 'novo', temperatura text default 'frio',
  valor_estimado_centavos bigint default 0, origem text, obs text, instagram text, socio text,
  regime_tributario text, porte text, ultimo_contato_em timestamptz, proximo_contato_em timestamptz,
  orcamento_id bigint, tem_site boolean, maps_url text, receita text, site_url text,
  decisor_nome text, decisor_cargo text, decisor_telefone text, decisor_whatsapp boolean,
  decisor_em timestamptz, decisor_telefones jsonb, estagio text default 'lead',
  tipo text default 'pj', cpf text,
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  canal text, status text default 'aberta', agente_ativo boolean default true,
  responsavel_membro_id bigint, ultima_msg_em timestamptz default now(),
  push_avisado_em timestamptz, criado_em timestamptz default now());
create table mensagens (id bigserial primary key, conversa_id bigint, canal text, direcao text,
  autor text default 'humano', membro_id bigint, texto text default '', provider_sid text,
  criado_em timestamptz default now());
create table funil_etapas (id bigserial primary key, conta_id bigint, chave text, rotulo text,
  ordem int default 0, fixa boolean default false, unique (conta_id, chave));
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
  pre_reserva_ate timestamptz, sinal_centavos int);   -- 160/163: agenda._COLS lê essas
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
    monkeypatch.setattr(wo, "enviar", lambda c, cid, num, txt: {"ok": True, "sid": "SM1"})
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
