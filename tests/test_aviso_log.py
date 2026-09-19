"""O REGISTRO DE AVISO ENVIADO (17/09/2026).

Nasceu de uma pergunta do dono que eu não conseguia responder. No primeiro dia com
o follow-up cobrando de verdade, ele pediu: "já mandou os e-mails pros vendedores?
me traz os logs dos clientes e e-mails enviados".

Os clientes eu tinha — `funil_avisos`, 129 linhas às 08:58, 33 leads, 4 pessoas. Os
E-MAILS não: o `email_sender` escreve no log da aplicação, o `enviar_push` devolve
um número que ninguém guardava, e o `notificar` tinha dois `except: pass` que
engoliam a falha inteira. Quando um vendedor dissesse "não recebi", a discussão
seria sem prova.

O que este arquivo garante, e cada item é um jeito pelo qual o buraco voltaria:

  1. sucesso grava linha, com o canal, o destino e quantos leads o aviso agrupou;
  2. FALHA grava linha, com o erro dentro — é a linha mais valiosa da tabela;
  3. push que não alcança aparelho nenhum não é erro, mas também não é silêncio;
  4. membro SEM e-mail gera linha explicando — é o caso do dono da conta 34, cuja
     cópia de gestor caía no vazio sem ninguém saber;
  5. registrar NUNCA derruba o envio: banco fora do ar não pode calar o aviso.
"""
import os
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import aviso_log as al

CONTA = 77
MIG = Path(__file__).resolve().parent.parent / "db" / "migracoes"

_SQL = """
create table membros (id bigserial primary key, conta_id bigint, nome text,
  email text, papel text default 'vendedor', ativo boolean default true,
  -- os DOIS campos de número: `whatsapp` vem do convite, `whatsapp_id` de quem se
  -- cadastrou pelo próprio WhatsApp. O aviso lê os dois — ver `_zap_do_membro`.
  whatsapp text, whatsapp_id text);
-- a régua INTEIRA: `follow_up.config` chama `funil_regua.config`, que lê a janela
-- de atendimento e os prazos de conversa. Um stub magro aqui passava por um caminho
-- que produção não tem — e o erro sai como "column gatilhos_modo does not exist".
create table funil_regua (conta_id bigint primary key,
  gatilhos_modo text default 'off', cobranca_modo text default 'off',
  janela_dias text default '1,2,3,4,5,6', janela_abre time default '08:00',
  janela_fecha time default '19:00', sem_resposta_min int default 120,
  bola_nossa_min int default 240, bola_cliente_min int default 4320,
  escala_min int default 240, teto_avisos_dia int default 5,
  follow_up_modo text default 'off', fu_proposta_dias int, fu_toques_dias text,
  fu_festa_dias int, fu_teto_dia int, fila_modo text not null default 'prazo',
  -- a esteira (migração 292): é ela que decide se o follow-up ainda avisa
  esteira_modo text not null default 'off',
  fu_zap boolean not null default false,
  atualizado_em timestamptz not null default now());
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_aviso_log_test"
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
        # A MIGRAÇÃO DE VERDADE, não uma cópia: é o único jeito de o teste perceber
        # que a tabela nova não chegou em produção.
        c.execute((MIG / "276_aviso_envios.sql").read_text(encoding="utf-8"))
        # e o degrau do recibo: sid, token e os três carimbos
        c.execute((MIG / "284_aviso_envios_recibo.sql").read_text(encoding="utf-8"))
        c.execute("""insert into membros (id, conta_id, nome, email, whatsapp, whatsapp_id) values
                        (1,%s,'THIAGO','thiago@x.com','86988614189',null),
                        -- o MANOEL é o caso de produção: sem e-mail, sem push, e o
                        -- número só no campo do cadastro
                        (2,%s,'MANOEL',null,null,'+5599984996253'),
                        (3,%s,'IRIS',null,null,null)""", (CONTA, CONTA, CONTA))
        c.commit()
    yield p
    p.close()


def _linhas(pool):
    with pool.connection() as c:
        return c.execute(
            "select origem, canal, destino, assunto, n_leads, ok, motivo, membro_id "
            "from aviso_envios order by id").fetchall()


# ───────────────────────────────────────────────────── o registro em si
def test_sucesso_grava_o_canal_o_destino_e_quantos_leads(pool):
    al.registrar(pool, CONTA, origem="follow_up", canal="email", ok=True,
                 membro_id=1, destino="thiago@x.com",
                 assunto="⏱️ 10 leads esperando follow-up", n_leads=10)
    (r,) = _linhas(pool)
    assert r[:6] == ("follow_up", "email", "thiago@x.com",
                     "⏱️ 10 leads esperando follow-up", 10, True)
    assert r[6] is None


def test_falha_grava_o_ERRO_dentro(pool):
    """A linha mais valiosa da tabela: é ela que responde "por que não recebi"."""
    al.registrar(pool, CONTA, origem="follow_up", canal="email", ok=False,
                 membro_id=1, destino="thiago@x.com", n_leads=10,
                 motivo="SMTPAuthenticationError: 535 senha de app inválida")
    (r,) = _linhas(pool)
    assert r[5] is False
    assert "535" in r[6] and "SMTPAuth" in r[6]


def test_motivo_enorme_e_cortado_em_vez_de_estourar(pool):
    """Erro de SMTP às vezes traz o corpo inteiro do e-mail dentro. O que serve pra
    diagnóstico são os primeiros caracteres — e a linha tem que caber."""
    al.registrar(pool, CONTA, origem="follow_up", canal="email", ok=False,
                 membro_id=1, motivo="x" * 5000)
    (r,) = _linhas(pool)
    assert len(r[6]) == al.MOTIVO_MAX


def test_registrar_com_banco_quebrado_nao_levanta(pool):
    """A regra 1 do módulo: testemunha não derruba o réu. Se registrar pudesse
    levantar, uma falha de banco calaria o aviso — que é o produto."""
    with pool.connection() as c:
        c.execute("drop table aviso_envios")
        c.commit()
    al.registrar(pool, CONTA, origem="follow_up", canal="email", ok=True)  # não levanta
    assert al.ultimos(pool, CONTA) == []                                   # nem na leitura


def test_a_leitura_vem_do_mais_novo_e_filtra_falha_e_pessoa(pool):
    al.registrar(pool, CONTA, origem="follow_up", canal="email", ok=True, membro_id=1)
    al.registrar(pool, CONTA, origem="follow_up", canal="push", ok=False, membro_id=1,
                 motivo="nenhum aparelho com push")
    al.registrar(pool, CONTA, origem="follow_up", canal="email", ok=True, membro_id=2)
    al.registrar(pool, 999, origem="follow_up", canal="email", ok=True)  # outra conta

    todos = al.ultimos(pool, CONTA)
    assert len(todos) == 3, "a linha da outra conta vazou"
    # do mais NOVO pro mais velho: a última gravada é a do Manoel, a primeira da lista
    assert [x["canal"] for x in todos] == ["email", "push", "email"]
    assert todos[0]["quem"] == "MANOEL"                        # o nome vem junto
    assert todos[-1]["quem"] == "THIAGO"

    assert [x["canal"] for x in al.ultimos(pool, CONTA, so_falha=True)] == ["push"]
    assert len(al.ultimos(pool, CONTA, membro_id=1)) == 2


def test_o_limite_da_leitura_tem_teto(pool):
    """Tela não pode pedir um milhão de linhas por acidente."""
    for _ in range(5):
        al.registrar(pool, CONTA, origem="follow_up", canal="email", ok=True)
    assert len(al.ultimos(pool, CONTA, limite=2)) == 2
    assert len(al.ultimos(pool, CONTA, limite=99999)) == 5      # não estoura


# ───────────────────────────────── o motor do follow-up gravando de verdade
def test_o_notificar_grava_os_DOIS_canais_e_a_falha_do_email(pool, monkeypatch):
    """Entra pela porta da frente: chama `follow_up.notificar` com o push e o e-mail
    espionados, e confere o que ficou escrito. Sem isto, os dois `except: pass` do
    motor voltariam a engolir a falha sem ninguém notar."""
    from finance import cockpit as ck
    from finance import email_sender as es
    from finance import follow_up as fu

    monkeypatch.setattr(ck, "enviar_push", lambda *a, **k: 2)          # 2 aparelhos
    monkeypatch.setattr(es, "enviar_aviso", lambda *a, **k: False)     # e-mail recusado

    fu.notificar(pool, CONTA, [
        {"lead_id": 1, "membro_id": 1, "quem": "Talila", "degrau": "venc",
         "nivel": "vendedor", "acao": "responder", "atraso_h": 30},
        {"lead_id": 2, "membro_id": 1, "quem": "Renata", "degrau": "a24",
         "nivel": "vendedor", "acao": "responder", "atraso_h": 50},
    ])
    por_canal = {r[1]: r for r in _linhas(pool)}
    assert por_canal["push"][5] is True and por_canal["push"][4] == 2
    assert por_canal["email"][5] is False, "e-mail recusado passou como enviado"
    assert "falso" in (por_canal["email"][6] or ""), "a falha do e-mail não explicou nada"
    assert por_canal["email"][2] == "thiago@x.com"


def test_membro_sem_email_deixa_o_motivo_escrito(pool, monkeypatch):
    """O caso do dono da conta 34: sem endereço cadastrado, a cópia de gestor do
    degrau a48 caía no vazio e NADA registrava isso. Ele decidiu não cadastrar —
    o que não pode é o sistema fingir que mandou."""
    from finance import cockpit as ck
    from finance import follow_up as fu
    monkeypatch.setattr(ck, "enviar_push", lambda *a, **k: 0)

    fu.notificar(pool, CONTA, [
        {"lead_id": 1, "membro_id": 2, "quem": "Talila", "degrau": "venc",
         "nivel": "vendedor", "acao": "responder", "atraso_h": 30}])
    por_canal = {r[1]: r for r in _linhas(pool)}
    assert por_canal["email"][5] is False
    assert "sem e-mail" in por_canal["email"][6]
    # e o push sem aparelho: não é erro, mas também não é silêncio
    assert por_canal["push"][5] is False
    assert "aparelho" in por_canal["push"][6]


# ═══════════════════════════════════════════ o WhatsApp do aviso (migração 280)
#
# Pedido do dono em 17/09/2026: "vamos implementar o whatsapp pra mandar pro
# vendedor, pro número dele". O encanamento já existia (é o mesmo do aviso de lead
# novo); o que faltava era a trava do número da equipe, consertada no mesmo dia.
#
# O que este bloco fixa é o que separa "mandar no WhatsApp" de "mandar no WhatsApp
# de alguém sem ele ter pedido".

def _regua(pool, *, zap):
    with pool.connection() as c:
        c.execute("""insert into funil_regua (conta_id, follow_up_modo, fu_zap)
                     values (%s,'ligado',%s)
                     on conflict (conta_id) do update set fu_zap=excluded.fu_zap""",
                  (CONTA, zap))
        c.commit()


def _pendente(membro_id, quem="Talila"):
    return {"lead_id": 1, "membro_id": membro_id, "quem": quem, "degrau": "venc",
            "nivel": "vendedor", "acao": "responder", "atraso_h": 30}


def _espiar_zap(monkeypatch):
    """Troca o envio de verdade por um espião. Devolve a lista do que foi mandado."""
    from finance import follow_up as fu
    saiu = []

    def _falso(pool, conta_id, numero, texto):
        saiu.append({"numero": numero, "texto": texto})
        return {"ok": True, "erro": "", "sid": f"3EB0{len(saiu)}"}

    monkeypatch.setattr(fu, "_mandar_zap", _falso)
    return saiu


def test_com_o_interruptor_DESLIGADO_nao_manda_nem_registra_whatsapp(pool, monkeypatch):
    """O padrão. Mandar no WhatsApp de alguém não começa ligado — a régua inteira
    segue essa regra desde a 218, e este canal é o que mais justifica ela."""
    from finance import cockpit as ck
    from finance import email_sender as es
    from finance import follow_up as fu
    monkeypatch.setattr(ck, "enviar_push", lambda *a, **k: 1)
    monkeypatch.setattr(es, "enviar_aviso", lambda *a, **k: True)
    saiu = _espiar_zap(monkeypatch)
    _regua(pool, zap=False)

    fu.notificar(pool, CONTA, [_pendente(1)])
    assert saiu == [], "mandou WhatsApp com o interruptor desligado"
    assert [r[1] for r in _linhas(pool)] == ["push", "email"], "registrou canal que não existiu"


def test_com_o_interruptor_LIGADO_manda_e_registra_o_numero(pool, monkeypatch):
    from finance import cockpit as ck
    from finance import email_sender as es
    from finance import follow_up as fu
    monkeypatch.setattr(ck, "enviar_push", lambda *a, **k: 1)
    monkeypatch.setattr(es, "enviar_aviso", lambda *a, **k: True)
    saiu = _espiar_zap(monkeypatch)
    _regua(pool, zap=True)

    fu.notificar(pool, CONTA, [_pendente(1)])
    assert len(saiu) == 1 and saiu[0]["numero"] == "86988614189"
    zap = [r for r in _linhas(pool) if r[1] == "whatsapp"]
    assert len(zap) == 1 and zap[0][5] is True
    assert zap[0][2] == "86988614189", "o destino não ficou registrado"


def test_o_MANOEL_recebe_pelo_numero_do_CADASTRO(pool, monkeypatch):
    """O caso que motivou ler os dois campos. Na conta 34 o dono não tem e-mail nem
    push: em 18/09 o log mostrou a cópia de gestor dele — 30 leads — falhando nos
    dois canais. O número dele existe, mas só em `whatsapp_id`.

    Lendo um campo só, o WhatsApp seria o terceiro canal a cair no vazio pra ele.
    """
    from finance import cockpit as ck
    from finance import follow_up as fu
    monkeypatch.setattr(ck, "enviar_push", lambda *a, **k: 0)
    saiu = _espiar_zap(monkeypatch)
    _regua(pool, zap=True)

    fu.notificar(pool, CONTA, [_pendente(2)])
    assert [x["numero"] for x in saiu] == ["+5599984996253"]
    por_canal = {r[1]: r for r in _linhas(pool)}
    # e os outros dois continuam falhando, registrados — o WhatsApp não apaga isso
    assert por_canal["email"][5] is False and "sem e-mail" in por_canal["email"][6]
    assert por_canal["push"][5] is False
    assert por_canal["whatsapp"][5] is True


def test_membro_sem_numero_nenhum_deixa_o_motivo_escrito(pool, monkeypatch):
    """Silêncio de novo não. Quem não tem número recebe só por e-mail e push, e a
    linha diz isso — senão o dono lê "3 canais ligados" e acredita."""
    from finance import cockpit as ck
    from finance import follow_up as fu
    monkeypatch.setattr(ck, "enviar_push", lambda *a, **k: 0)
    saiu = _espiar_zap(monkeypatch)
    _regua(pool, zap=True)

    fu.notificar(pool, CONTA, [_pendente(3)])
    assert saiu == []
    por_canal = {r[1]: r for r in _linhas(pool)}
    assert por_canal["whatsapp"][5] is False
    assert "sem WhatsApp" in por_canal["whatsapp"][6]


def test_whatsapp_que_falha_nao_derruba_o_resto_e_fica_escrito(pool, monkeypatch):
    """Chip fora do ar, número inválido, janela fechada: o aviso do dia não pode se
    perder por causa do terceiro canal, e o erro tem que ficar legível."""
    from finance import cockpit as ck
    from finance import email_sender as es
    from finance import follow_up as fu
    monkeypatch.setattr(ck, "enviar_push", lambda *a, **k: 2)
    monkeypatch.setattr(es, "enviar_aviso", lambda *a, **k: True)
    monkeypatch.setattr(fu, "_mandar_zap",
                        lambda *a, **k: {"ok": False, "erro": "chip_desconectado"})
    _regua(pool, zap=True)

    fu.notificar(pool, CONTA, [_pendente(1)])      # não levanta
    por_canal = {r[1]: r for r in _linhas(pool)}
    assert por_canal["push"][5] is True and por_canal["email"][5] is True
    assert por_canal["whatsapp"][5] is False
    assert por_canal["whatsapp"][6] == "chip_desconectado"


def test_o_texto_leva_titulo_corpo_e_link(pool):
    """O link é o botão. Sem ele o aviso vira cobrança sem saída — o mesmo defeito
    que o push do rodízio já teve."""
    from finance import follow_up as fu
    t = fu._texto_zap("⏱️ 10 leads esperando follow-up", "Talila · Renata")
    assert t.startswith("⏱️ 10 leads esperando follow-up")
    assert "Talila · Renata" in t
    # o link mudou em 19/09/2026: era /cockpit, a tela inteira, e a pessoa tinha que
    # procurar o que venceu. Agora cai na fila do atrasado — e a esteira, que reusa
    # esta função, ganhou o mesmo link
    assert "/painel/follow-up?estado=atrasado" in t
    assert "*" not in t, "marcação de negrito vira lixo visível fora do WhatsApp"


def test_regua_ilegivel_deixa_o_whatsapp_DESLIGADO(pool, monkeypatch):
    """Falhar a leitura vale desligado: o canal que toca o celular de alguém não
    pode ligar por acidente de banco."""
    from finance import cockpit as ck
    from finance import follow_up as fu
    monkeypatch.setattr(ck, "enviar_push", lambda *a, **k: 1)
    saiu = _espiar_zap(monkeypatch)
    with pool.connection() as c:
        c.execute("drop table funil_regua")
        c.commit()
    fu.notificar(pool, CONTA, [_pendente(1)])
    assert saiu == [], "ligou o WhatsApp sem conseguir ler a régua"


# ───────────────────────────────────── o recibo e o clique (migração 284)
#
# O DEGRAU SEGUINTE, pedido pelo dono em 18/09/2026 depois de ver o card de
# desempenho da campanha: "se poder medir também se chegou e eles visualizaram".
# O que cada canal SABE dizer é diferente, e estes testes fixam justamente isso —
# um card que finge simetria mente em dois dos três canais.

def _sid(pool, sid="3EB0AA", canal="whatsapp", ok=True, membro_id=1):
    al.registrar(pool, CONTA, origem="follow_up", canal=canal, ok=ok,
                 membro_id=membro_id, n_leads=10, sid=sid)


def _carimbos(pool, sid="3EB0AA"):
    with pool.connection() as c:
        return c.execute("select entregue_em, lido_em, clicado_em from aviso_envios "
                         " where sid=%s", (sid,)).fetchone()


def test_o_recibo_de_entrega_e_de_leitura_carimba_o_aviso(pool):
    _sid(pool)
    with pool.connection() as c:
        al.marcar_recibo(c, "3EB0AA", "entregue")
        c.commit()
    entregue, lido, _ = _carimbos(pool)
    assert entregue is not None and lido is None
    with pool.connection() as c:
        al.marcar_recibo(c, "3EB0AA", "lido")
        c.commit()
    _, lido, _ = _carimbos(pool)
    assert lido is not None


def test_o_recibo_NUNCA_regride(pool):
    """Os recibos chegam fora de ordem. Sem a trava, um 'lido' voltaria pra
    'entregue' meia hora depois — e a tela mentiria pro dono."""
    _sid(pool)
    with pool.connection() as c:
        al.marcar_recibo(c, "3EB0AA", "lido")
        c.commit()
    _, lido_antes, _ = _carimbos(pool)
    with pool.connection() as c:
        al.marcar_recibo(c, "3EB0AA", "entregue")   # o atrasado chega depois
        al.marcar_recibo(c, "3EB0AA", "lido")       # e o repetido também
        c.commit()
    entregue, lido_depois, _ = _carimbos(pool)
    assert lido_depois == lido_antes, "o carimbo de leitura foi reescrito"
    assert entregue is not None, "ler sem ter recebido não existe"


def test_lido_carimba_a_entrega_que_faltou(pool):
    """O recibo de entrega se perde numa reconexão do chip. Quando o de leitura
    chega sozinho, a entrega é fato — e o card não pode mostrar 'lido' com zero
    entregues."""
    _sid(pool)
    with pool.connection() as c:
        al.marcar_recibo(c, "3EB0AA", "lido")
        c.commit()
    entregue, lido, _ = _carimbos(pool)
    assert entregue is not None and lido is not None


def test_recibo_de_sid_desconhecido_nao_explode_nem_carimba_outro(pool):
    _sid(pool)
    with pool.connection() as c:
        al.marcar_recibo(c, "SID-DE-OUTRA-MENSAGEM", "lido")
        al.marcar_recibo(c, "", "lido")
        al.marcar_recibo(c, "3EB0AA", "enviado")   # status que não é recibo
        c.commit()
    assert _carimbos(pool) == (None, None, None)


def test_o_clique_do_push_conta_UMA_vez(pool):
    """A mesma notificação pode ser tocada duas vezes; 'clicou de novo' não é
    informação nova."""
    tok = al.novo_token()
    al.registrar(pool, CONTA, origem="follow_up", canal="push", ok=True,
                 membro_id=1, n_leads=10, token=tok)
    assert al.marcar_clique(pool, tok) is True
    with pool.connection() as c:
        primeiro = c.execute("select clicado_em from aviso_envios where token=%s",
                             (tok,)).fetchone()[0]
    assert al.marcar_clique(pool, tok) is False
    with pool.connection() as c:
        depois = c.execute("select clicado_em from aviso_envios where token=%s",
                           (tok,)).fetchone()[0]
    assert depois == primeiro


def test_token_que_nao_existe_devolve_falso_sem_levantar(pool):
    """Chega quando uma notificação velha sobrevive a um banco restaurado. E é o
    mesmo caminho de quem tentasse chutar token: não pode virar oráculo."""
    assert al.marcar_clique(pool, "nao-existe") is False
    assert al.marcar_clique(pool, "") is False
    assert al.marcar_clique(pool, "x" * 500) is False


def test_dois_avisos_nao_podem_dividir_o_mesmo_token(pool):
    """O token AUTENTICA o clique: repetido, o clique seria de qualquer um."""
    import psycopg
    tok = al.novo_token()
    al.registrar(pool, CONTA, origem="follow_up", canal="push", ok=True, token=tok)
    with pytest.raises(psycopg.errors.UniqueViolation):
        with pool.connection() as c:
            c.execute("insert into aviso_envios (conta_id, origem, canal, ok, token) "
                      "values (%s,'follow_up','push',true,%s)", (CONTA, tok))
            c.commit()


# ───────────────────────────────────────────────── o resumo que vira card
def test_o_resumo_conta_cada_canal_pelo_que_ele_SABE_dizer(pool):
    al.registrar(pool, CONTA, origem="follow_up", canal="whatsapp", ok=True,
                 membro_id=1, sid="A1")
    al.registrar(pool, CONTA, origem="follow_up", canal="whatsapp", ok=True,
                 membro_id=1, sid="A2")
    al.registrar(pool, CONTA, origem="follow_up", canal="whatsapp", ok=False,
                 membro_id=3, motivo="membro sem WhatsApp cadastrado")
    tok = al.novo_token()
    al.registrar(pool, CONTA, origem="follow_up", canal="push", ok=True,
                 membro_id=1, token=tok)
    al.registrar(pool, CONTA, origem="follow_up", canal="push", ok=False,
                 membro_id=2, motivo="nenhum aparelho com push")
    al.registrar(pool, CONTA, origem="follow_up", canal="email", ok=True, membro_id=1)
    with pool.connection() as c:
        al.marcar_recibo(c, "A1", "lido")
        c.commit()
    al.marcar_clique(pool, tok)

    r = al.resumo(pool, CONTA)
    assert r["whatsapp"]["ok"] == 2 and r["whatsapp"]["falhas"] == 1
    assert r["whatsapp"]["lidos"] == 1 and r["whatsapp"]["entregues"] == 1
    # A2 saiu e nenhum recibo voltou: é coluna PRÓPRIA, não "não entregue". Quem
    # desliga a confirmação de leitura no WhatsApp nunca gera o 👀.
    assert r["whatsapp"]["sem_recibo"] == 1
    assert r["push"]["ok"] == 1 and r["push"]["clicados"] == 1 and r["push"]["falhas"] == 1
    # o e-mail não tem recibo nenhum, e o resumo não inventa um
    assert r["email"]["ok"] == 1
    assert r["email"]["entregues"] == 0 and r["email"]["lidos"] == 0


def test_o_teste_de_canal_NAO_entra_na_estatistica_da_cobranca(pool):
    """A origem nasceu separada pra isto: o botão "Testar agora" prova que o chip
    fala, e não pode virar "os vendedores receberam 9 avisos esta semana"."""
    al.registrar(pool, CONTA, origem="follow_up", canal="whatsapp", ok=True, membro_id=1)
    al.registrar(pool, CONTA, origem="follow_up_teste", canal="whatsapp", ok=True, membro_id=1)
    assert al.resumo(pool, CONTA)["whatsapp"]["ok"] == 1
    assert al.resumo(pool, CONTA, origem="follow_up_teste")["whatsapp"]["ok"] == 1


def test_o_resumo_separa_por_vendedor_porque_o_total_nao_diz_quem(pool):
    """"16 de 21 lidos" não diz quem não está lendo — e é essa a pergunta de quem
    cobra."""
    al.registrar(pool, CONTA, origem="follow_up", canal="whatsapp", ok=True,
                 membro_id=1, sid="T1")
    al.registrar(pool, CONTA, origem="follow_up", canal="whatsapp", ok=True,
                 membro_id=2, sid="M1")
    with pool.connection() as c:
        al.marcar_recibo(c, "T1", "lido")
        al.marcar_recibo(c, "M1", "entregue")
        c.commit()
    por = {v["quem"]: v for v in al.resumo(pool, CONTA)["por_vendedor"]}
    assert por["THIAGO"]["lidos"] == 1 and por["THIAGO"]["entregues"] == 0
    assert por["MANOEL"]["lidos"] == 0 and por["MANOEL"]["entregues"] == 1


def test_a_janela_do_resumo_corta_o_que_e_velho(pool):
    al.registrar(pool, CONTA, origem="follow_up", canal="email", ok=True, membro_id=1)
    with pool.connection() as c:
        c.execute("update aviso_envios set criado_em = now() - interval '40 days'")
        c.commit()
    assert al.resumo(pool, CONTA)["email"]["ok"] == 0, "30 dias virou 'sempre'"
    assert al.resumo(pool, CONTA, dias=90)["email"]["ok"] == 1


def test_resumo_com_a_tabela_fora_do_ar_devolve_a_forma_vazia(pool):
    """O card é acessório; a fila é o produto da tela. Um resumo que levanta
    derrubaria a tela inteira do follow-up."""
    with pool.connection() as c:
        c.execute("drop table aviso_envios")
        c.commit()
    r = al.resumo(pool, CONTA)
    assert r["whatsapp"] == r["push"] == r["email"] == {
        "tentativas": 0, "ok": 0, "falhas": 0, "entregues": 0, "lidos": 0,
        "sem_recibo": 0, "clicados": 0}
    assert r["por_vendedor"] == []


def test_o_aviso_de_whatsapp_guarda_o_id_da_mensagem(pool, monkeypatch):
    """Sem o sid, "saiu" é tudo que se saberia dizer: é por ele que o recibo
    encontra o aviso depois."""
    from finance import cockpit as ck
    from finance import email_sender as es
    from finance import follow_up as fu
    monkeypatch.setattr(ck, "enviar_push", lambda *a, **k: 1)
    monkeypatch.setattr(es, "enviar_aviso", lambda *a, **k: True)
    _espiar_zap(monkeypatch)
    _regua(pool, zap=True)

    fu.notificar(pool, CONTA, [_pendente(1)])
    with pool.connection() as c:
        sid = c.execute("select sid from aviso_envios where canal='whatsapp'").fetchone()[0]
    assert sid == "3EB01"


def test_o_push_leva_o_token_do_clique_e_o_guarda(pool, monkeypatch):
    """O token viaja NO push — gerar depois seria tarde, a notificação já saiu."""
    from finance import cockpit as ck
    from finance import email_sender as es
    from finance import follow_up as fu
    visto = {}

    def _push(pool_, conta, membro, titulo, corpo, url="/cockpit", badge=None, token=""):
        visto["token"] = token
        return 1

    monkeypatch.setattr(ck, "enviar_push", _push)
    monkeypatch.setattr(es, "enviar_aviso", lambda *a, **k: True)
    fu.notificar(pool, CONTA, [_pendente(1)])
    with pool.connection() as c:
        guardado = c.execute("select token from aviso_envios where canal='push'").fetchone()[0]
    assert visto["token"] and guardado == visto["token"]


def test_push_que_nao_alcancou_aparelho_nenhum_nao_guarda_token(pool, monkeypatch):
    """Token guardado sem push no ar seria um clique impossível esperando pra
    sempre — e uma linha a menos pro índice único cuidar."""
    from finance import cockpit as ck
    from finance import email_sender as es
    from finance import follow_up as fu
    monkeypatch.setattr(ck, "enviar_push", lambda *a, **k: 0)
    monkeypatch.setattr(es, "enviar_aviso", lambda *a, **k: True)
    fu.notificar(pool, CONTA, [_pendente(1)])
    with pool.connection() as c:
        r = c.execute("select ok, token from aviso_envios where canal='push'").fetchone()
    assert r == (False, None)


# ──────────────────────────── o histórico, pessoa por pessoa (19/09/2026)
#
# Pedido do dono, mostrando o card do lead na campanha: "quero que lá no follow-up
# fique assim, só que adapte as informações do vendedor com as notificações".
# Mockup: docs/mockups/historico_do_aviso_por_vendedor.html.

def test_o_historico_agrupa_por_pessoa_e_por_canal(pool):
    al.registrar(pool, CONTA, origem="follow_up", canal="whatsapp", ok=True,
                 membro_id=1, n_leads=10, sid="H1")
    al.registrar(pool, CONTA, origem="follow_up", canal="push", ok=True, membro_id=1)
    al.registrar(pool, CONTA, origem="follow_up", canal="email", ok=True, membro_id=1)
    al.registrar(pool, CONTA, origem="follow_up", canal="email", ok=False, membro_id=2,
                 motivo="membro sem e-mail cadastrado")
    with pool.connection() as c:
        al.marcar_recibo(c, "H1", "lido")
        c.commit()

    por = {p["quem"]: p for p in al.historico(pool, CONTA)}
    assert set(por) == {"THIAGO", "MANOEL"}
    zap = por["THIAGO"]["canais"]["whatsapp"]["recentes"]
    assert len(zap) == 1 and zap[0]["lido_em"] is not None and zap[0]["n_leads"] == 10
    assert por["THIAGO"]["canais"]["push"]["recentes"][0]["ok"] is True
    manoel = por["MANOEL"]["canais"]["email"]["recentes"][0]
    assert manoel["ok"] is False and "sem e-mail" in manoel["motivo"]
    # o contato vem junto: a linha fechada mostra pra onde o aviso vai
    assert por["THIAGO"]["email"] == "thiago@x.com"
    assert por["MANOEL"]["numero"] == "+5599984996253"


def test_o_historico_separa_os_7_dias_visiveis_do_resto(pool):
    """Sete dias abertos, o resto atrás de "ver os 30 dias": o aviso é diário, e 30
    dias escancarados viram quase 90 linhas por pessoa."""
    al.registrar(pool, CONTA, origem="follow_up", canal="email", ok=True, membro_id=1)
    al.registrar(pool, CONTA, origem="follow_up", canal="email", ok=True, membro_id=1)
    with pool.connection() as c:
        c.execute("update aviso_envios set criado_em = now() - interval '20 days' "
                  " where id = (select min(id) from aviso_envios)")
        c.commit()
    (p,) = al.historico(pool, CONTA)
    assert len(p["canais"]["email"]["recentes"]) == 1
    assert len(p["canais"]["email"]["antigos"]) == 1


def test_o_teste_de_canal_APARECE_no_historico_marcado(pool):
    """Ele fica FORA da estatística (o card só olha origem='follow_up') e DENTRO da
    linha do tempo: esconder o que chegou no celular do vendedor seria esconder
    metade da história do dia."""
    al.registrar(pool, CONTA, origem="follow_up_teste", canal="whatsapp", ok=True,
                 membro_id=1, n_leads=10)
    (p,) = al.historico(pool, CONTA)
    assert p["canais"]["whatsapp"]["recentes"][0]["teste"] is True
    assert al.resumo(pool, CONTA)["whatsapp"]["ok"] == 0, "o teste virou estatística"


def test_o_historico_nao_lista_membro_de_outra_conta_nem_desativado(pool):
    al.registrar(pool, CONTA, origem="follow_up", canal="email", ok=True, membro_id=1)
    al.registrar(pool, 999, origem="follow_up", canal="email", ok=True, membro_id=2)
    with pool.connection() as c:
        c.execute("update membros set ativo=false where id=3")
        c.execute("insert into aviso_envios (conta_id, membro_id, origem, canal, ok) "
                  "values (%s,3,'follow_up','email',true)", (CONTA,))
        c.commit()
    assert [p["quem"] for p in al.historico(pool, CONTA)] == ["THIAGO"]


def test_o_historico_tem_teto_por_canal(pool):
    """Pro dia em que alguém ligar um aviso por hora: a tela não pode virar despejo
    de log."""
    for _ in range(al.MAX_EVENTOS + 5):
        al.registrar(pool, CONTA, origem="follow_up", canal="email", ok=True, membro_id=1)
    (p,) = al.historico(pool, CONTA)
    assert len(p["canais"]["email"]["recentes"]) == al.MAX_EVENTOS


def test_historico_com_a_tabela_fora_do_ar_devolve_vazio(pool):
    with pool.connection() as c:
        c.execute("drop table aviso_envios")
        c.commit()
    assert al.historico(pool, CONTA) == []


# ──────────── a esteira manda, e o follow-up cala (19/09/2026)
#
# Em 19/09 a Prime passou o dia com os DOIS motores ligados: o follow-up cobrou às
# 08:00 em três canais e a esteira cobrou os MESMOS leads às 09:19 em dois. Cinco
# avisos por vendedor, duas mensagens dizendo a mesma coisa com meia hora de
# diferença. A esteira é o desenho mais novo e mais completo, então onde ela está
# ligada a cobrança é dela.

def _esteira(pool, modo):
    with pool.connection() as c:
        c.execute("update funil_regua set esteira_modo=%s where conta_id=%s", (modo, CONTA))
        c.commit()


def test_com_a_esteira_LIGADA_o_follow_up_nao_manda_nada(pool, monkeypatch):
    from finance import cockpit as ck
    from finance import email_sender as es
    from finance import follow_up as fu
    monkeypatch.setattr(ck, "enviar_push", lambda *a, **k: 1)
    monkeypatch.setattr(es, "enviar_aviso", lambda *a, **k: True)
    saiu = _espiar_zap(monkeypatch)
    _regua(pool, zap=True)
    _esteira(pool, "ligado")

    fu.notificar(pool, CONTA, [_pendente(1)])
    assert _linhas(pool) == [], "o follow-up avisou por cima da esteira"
    assert saiu == []


def test_com_a_esteira_em_ENSAIO_o_follow_up_tambem_cala(pool, monkeypatch):
    """'observando' já cobra e resume — só não fecha ninguém no dia 7. Duas
    cobranças continuariam sendo duas."""
    from finance import cockpit as ck
    from finance import email_sender as es
    from finance import follow_up as fu
    monkeypatch.setattr(ck, "enviar_push", lambda *a, **k: 1)
    monkeypatch.setattr(es, "enviar_aviso", lambda *a, **k: True)
    _regua(pool, zap=False)
    _esteira(pool, "observando")
    fu.notificar(pool, CONTA, [_pendente(1)])
    assert _linhas(pool) == []


def test_sem_esteira_o_follow_up_continua_avisando(pool, monkeypatch):
    """Conta que não ligou a esteira não pode ficar sem cobrança nenhuma."""
    from finance import cockpit as ck
    from finance import email_sender as es
    from finance import follow_up as fu
    monkeypatch.setattr(ck, "enviar_push", lambda *a, **k: 1)
    monkeypatch.setattr(es, "enviar_aviso", lambda *a, **k: True)
    _regua(pool, zap=False)
    _esteira(pool, "off")
    fu.notificar(pool, CONTA, [_pendente(1)])
    assert [x[1] for x in _linhas(pool)] == ["push", "email"]
