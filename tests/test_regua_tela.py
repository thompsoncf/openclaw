"""A tela da Régua: salvar a config e ligar UM gatilho de cada vez.

O que estes testes protegem:
  * o prazo é regulável em minutos/horas/dias e vira uma unidade só no banco;
  * "sem prazo" existe de verdade (campo vazio não vira zero, que cobraria sempre);
  * ligar o gatilho sem escolher evento não liga nada — senão a etapa ficaria
    "ativa" apontando pro vazio, e o motor rodaria em falso;
  * etapa de resultado (ganho/perdido) não aceita prazo: "está em Perdido há 30
    dias" não é cobrança, é o fim da história;
  * vendedor não configura a régua da empresa.
"""
import asyncio
import os
from pathlib import Path
from types import SimpleNamespace

import pytest
from psycopg_pool import ConnectionPool

from web import painel_prospeccao as pp

CONTA = 11
MIG = Path(__file__).resolve().parent.parent / "db" / "migracoes"

_SQL = """
create table prospeccao (id bigserial primary key, conta_id bigint, status text default 'novo',
  estagio text default 'lead', vendedor_id bigint, criado_em timestamptz default now());
create table funil_motivos_perda (id bigserial primary key, conta_id bigint,
  chave text, rotulo text, ordem int default 0, ativo boolean default true,
  exige_descricao boolean default false, criado_em timestamptz default now(),
  constraint uq_fmp unique (conta_id, chave));
create table funil_etapas (id bigserial primary key,
  -- 254: de onde veio o rótulo — a semente do ramo, ou o dono
  semeado_de text, conta_id bigint, chave text, rotulo text,
  ordem int default 0, fixa boolean default false, fase text default 'venda',
  prazo_min integer, gatilho text, gatilho_ativo boolean default false,
  teto_dias integer, renovacoes_max integer not null default 0,
  exige_justificativa boolean not null default true, renova_sozinho_h integer,
  saidas_permitidas text, toques_dias text,
  exige_motivo boolean not null default false, reativa_para text,
  sai_do_quadro boolean not null default false,
  agenda_ao_entrar boolean not null default false,
  criado_em timestamptz default now(), constraint uq_fe unique (conta_id, chave));
create table funil_regua (conta_id bigint primary key,
  gatilhos_modo text default 'off', cobranca_modo text default 'off',
  janela_dias text default '1,2,3,4,5,6', janela_abre time default '08:00',
  janela_fecha time default '19:00', sem_resposta_min int default 120,
  bola_nossa_min int default 240, bola_cliente_min int default 4320,
  escala_min int default 240, teto_avisos_dia int default 5,
  -- sem NOT NULL e SEM DEFAULT, como a migração 228 deixou a tabela de verdade:
  -- coluna vazia quer dizer "herda o padrão do nicho", e um default aqui esconderia
  -- justamente o caso que a tela precisa saber mostrar.
  follow_up_modo text default 'off', fu_proposta_dias int, fu_toques_dias text,
  -- `fu_zap` (migração 280): o aviso também no WhatsApp do vendedor, desligado por padrão
  fu_zap boolean not null default false,
  fu_festa_dias int, fu_teto_dia int,
  -- o quarto modo (migração 230): um interruptor POR REGRA, não um geral
  teto_modo text not null default 'off', teto_avisar_antes int,
  -- a ordem da fila do vendedor (migração 245)
  fila_modo text not null default 'prazo',
  -- a temperatura pelos fatos (migração 247): modo + três limiares que herdam do ramo
  temperatura_modo text, temp_quente_h int, temp_morno_dias int, temp_frio_tentativas int,
  atualizado_em timestamptz default now());
create table funil_movimentos (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  de text, para text, motivo text, membro_id bigint, criado_em timestamptz default now());
create table funil_avisos (id bigserial primary key, conta_id bigint, prospeccao_id bigint,
  estado text, nivel text, etapa text default '', ref_em timestamptz,
  simulado boolean default false, membro_id bigint, criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint, prospeccao_id bigint, chip_id bigint, visto_ate_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, direcao text,
  criado_em timestamptz,
  midia_ref jsonb, midia_tipo text, midia_meta jsonb, midia_arquivo text, midia_guardada_em timestamptz, midia_guardada_por bigint);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text);
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_regua_tela_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        # a 218 acrescenta o follow-up à mesma linha de config, e é a tela da Régua
        # que liga os três motores — aplicar a migração de verdade é o que faz este
        # teste perceber quando uma coluna nova não chegou ao POST
        c.execute((MIG / "218_follow_up.sql").read_text(encoding="utf-8"))
        # a 282 traz o Perdido automático pro mesmo bloco Estado — e foi ESTE teste
        # que pegou a coluna faltando no POST, que é pra isso que o replay existe
        c.execute((MIG / "282_perdido_automatico.sql").read_text(encoding="utf-8"))
        # a 292 traz a esteira pro mesmo bloco Estado
        c.execute((MIG / "292_esteira_da_cobranca.sql").read_text(encoding="utf-8"))
        for chave, rot, ordem, fixa, fase in [("novo", "Novo", 0, True, "venda"),
                                              ("contatado", "Contatado", 10, False, "venda"),
                                              ("ganho", "Sinal Pago", 900, True, "fechamento"),
                                              ("perdido", "Perdido", 910, True, "fechamento")]:
            c.execute("""insert into funil_etapas (conta_id, chave, rotulo, ordem, fixa, fase)
                         values (%s,%s,%s,%s,%s,%s)""", (CONTA, chave, rot, ordem, fixa, fase))
        c.commit()
    yield p
    p.close()


class _Form(dict):
    def getlist(self, k):
        v = self.get(k)
        return v if isinstance(v, list) else ([v] if v is not None else [])


class _Req:
    def __init__(self, campos):
        self._f = _Form(campos)
        self.session = {}

    async def form(self):
        return self._f


def _logado(monkeypatch, pool, gerencia=True):
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_acesso", lambda req: (
        {"conta_id": CONTA, "membro_id": 1, "gerencia": gerencia, "pode_atribuir": gerencia,
         "conta": None, "papel": "dono" if gerencia else "vendedor"}, None))


def _eid(pool, chave):
    with pool.connection() as c:
        return c.execute("select id from funil_etapas where conta_id=%s and chave=%s",
                         (CONTA, chave)).fetchone()[0]


def _etapa(pool, chave):
    with pool.connection() as c:
        return c.execute("""select prazo_min, gatilho, gatilho_ativo, rotulo
                              from funil_etapas where conta_id=%s and chave=%s""",
                         (CONTA, chave)).fetchone()


# ----------------------------------------------------------------- unidades

def test_prazo_vai_e_volta_na_maior_unidade_que_serve():
    assert pp._par_min("4", "h") == 240
    assert pp._min_par(240) == (4, "h")
    assert pp._min_par(4320) == (3, "d")
    assert pp._min_par(90) == (90, "min")     # 1h30 não é hora inteira nem dia


def test_campo_vazio_e_sem_prazo_nao_e_zero():
    """Zero significaria 'vence na hora' — a etapa cobraria todo lead o tempo todo."""
    assert pp._par_min("", "h") is None
    assert pp._par_min("0", "h") is None
    assert pp._par_min("-3", "d") is None
    assert pp._min_par(None) == ("", "h")


# ----------------------------------------------------------------- config

def test_salvar_config_grava_modos_janela_e_prazos(monkeypatch, pool):
    _logado(monkeypatch, pool)
    req = _Req({"gatilhos_modo": "observando", "cobranca_modo": "off",
                "dias": ["1", "2", "3", "4", "5", "6"], "abre": "07:30", "fecha": "20:00",
                "sem_resposta_n": "90", "sem_resposta_u": "min",
                "bola_nossa_n": "4", "bola_nossa_u": "h",
                "bola_cliente_n": "5", "bola_cliente_u": "d",
                "escala_n": "6", "escala_u": "h", "teto": "3"})
    asyncio.run(pp.regua_config(req))
    with pool.connection() as c:
        r = c.execute("""select gatilhos_modo, cobranca_modo, janela_dias, sem_resposta_min,
                                bola_nossa_min, bola_cliente_min, escala_min, teto_avisos_dia
                           from funil_regua where conta_id=%s""", (CONTA,)).fetchone()
    assert r == ("observando", "off", "1,2,3,4,5,6", 90, 240, 7200, 360, 3)


def test_modo_invalido_cai_pra_desligado(monkeypatch, pool):
    """Ninguém liga a régua por acidente de digitação num POST."""
    _logado(monkeypatch, pool)
    asyncio.run(pp.regua_config(_Req({"gatilhos_modo": "LIGADO!!", "cobranca_modo": "sim"})))
    with pool.connection() as c:
        r = c.execute("select gatilhos_modo, cobranca_modo from funil_regua where conta_id=%s",
                      (CONTA,)).fetchone()
    assert r == ("off", "off")


def test_teto_nunca_fica_zero(monkeypatch, pool):
    """Teto zero calaria a régua inteira sem ninguém perceber — a trava é antiga,
    o que mudou foi onde ela mora.

    Até a migração 228 o "0" caía no `coalesce` do SQL e o valor ANTERIOR ficava.
    Agora campo vazio quer dizer HERDA, então "0" deixou de ser indistinguível de
    "não mexi": é um número que o dono digitou, e a resposta certa pra um número
    inválido é o menor válido (1), não ressuscitar em silêncio o que estava lá.
    """
    _logado(monkeypatch, pool)
    asyncio.run(pp.regua_config(_Req({"teto": "0"})))
    with pool.connection() as c:
        assert c.execute("select teto_avisos_dia from funil_regua where conta_id=%s",
                         (CONTA,)).fetchone()[0] == 1


def test_campo_vazio_volta_a_herdar_o_padrao_do_ramo(monkeypatch, pool):
    """O pedido do dono de 11/09/2026: "deixa uma forma de parametrizar, porque
    serve pra outras empresas do mesmo nicho ou outras".

    Antes da 228 não existia caminho de volta: `coalesce(%s, coluna)` fazia campo
    vazio significar "mantém o que estava", então quem digitasse um número uma vez
    ficava com ele pra sempre e o padrão do ramo nunca mais alcançava a conta.
    """
    _logado(monkeypatch, pool)
    asyncio.run(pp.regua_config(_Req({"sem_resposta_n": "90", "sem_resposta_u": "min",
                                      "fu_toques_dias": "1, 3,7 "})))
    with pool.connection() as c:
        r = c.execute("select sem_resposta_min, fu_toques_dias from funil_regua where conta_id=%s",
                      (CONTA,)).fetchone()
    assert r == (90, "1,3,7"), "escolha do dono não gravou (ou não limpou o espaço)"

    asyncio.run(pp.regua_config(_Req({"sem_resposta_n": "", "fu_toques_dias": ""})))
    with pool.connection() as c:
        r = c.execute("select sem_resposta_min, fu_toques_dias from funil_regua where conta_id=%s",
                      (CONTA,)).fetchone()
    assert r == (None, None), "apagar o campo tinha que devolver a conta pro padrão do ramo"


# ----------------------------------------------------------------- etapa

def test_liga_um_gatilho_de_cada_vez(monkeypatch, pool):
    _logado(monkeypatch, pool)
    r = asyncio.run(pp.regua_etapa(_Req({"rotulo": "Contatado", "gatilho": "resposta_nossa",
                                         "gatilho_ativo": "1", "prazo_n": "5", "prazo_u": "d"}),
                                   _eid(pool, "contatado")))
    assert r.status_code == 200
    assert _etapa(pool, "contatado") == (7200, "resposta_nossa", True, "Contatado")
    # e a etapa vizinha continua intocada — é uma linha por vez, não um salvar-tudo
    assert _etapa(pool, "novo")[2] is False


def test_marcar_ativo_sem_escolher_evento_nao_liga_nada(monkeypatch, pool):
    """Etapa 'ativa' apontando pro vazio faria o motor rodar em falso todo ciclo."""
    _logado(monkeypatch, pool)
    asyncio.run(pp.regua_etapa(_Req({"gatilho": "", "gatilho_ativo": "1"}), _eid(pool, "contatado")))
    prazo, gat, ativo, _rot = _etapa(pool, "contatado")
    assert (gat, ativo) == (None, False)


def test_evento_desconhecido_e_recusado(monkeypatch, pool):
    _logado(monkeypatch, pool)
    asyncio.run(pp.regua_etapa(_Req({"gatilho": "lua_cheia", "gatilho_ativo": "1"}),
                               _eid(pool, "contatado")))
    assert _etapa(pool, "contatado")[1] is None


def test_etapa_de_resultado_nao_aceita_prazo(monkeypatch, pool):
    _logado(monkeypatch, pool)
    asyncio.run(pp.regua_etapa(_Req({"prazo_n": "3", "prazo_u": "d"}), _eid(pool, "perdido")))
    assert _etapa(pool, "perdido")[0] is None


def _regras(pool, chave):
    with pool.connection() as c:
        return c.execute("""select exige_motivo, reativa_para, sai_do_quadro, agenda_ao_entrar,
                                   exige_justificativa, renovacoes_max
                              from funil_etapas where conta_id=%s and chave=%s""",
                         (CONTA, chave)).fetchone()


def _como_na_prime(pool):
    """O Perdido e o Fechado como a Prime (conta 34) tem hoje: o Perdido pede motivo e
    reabre em Contatado; o Fechado sai do quadro e cria o compromisso na Agenda."""
    with pool.connection() as c:
        c.execute("""update funil_etapas set exige_motivo=true, reativa_para='contatado'
                      where conta_id=%s and chave='perdido'""", (CONTA,))
        c.execute("""update funil_etapas set sai_do_quadro=true, agenda_ao_entrar=true
                      where conta_id=%s and chave='ganho'""", (CONTA,))
        c.commit()


def test_renomear_o_perdido_nao_apaga_as_regras_dele(monkeypatch, pool):
    """O defeito de 27/09/2026, achado lendo o PDF da régua da Prime.

    A linha do Perdido só desenha rótulo e gatilho. O resto não vem no formulário,
    e o servidor gravava "desligado" em tudo que não veio: renomear o Perdido
    desligava, sem aviso, o "reabre em Contatado se o cliente voltar a falar" e o
    "pede motivo" — duas regras que a tela nem mostrava pra alguém religar.
    """
    _logado(monkeypatch, pool)
    _como_na_prime(pool)
    r = asyncio.run(pp.regua_etapa(_Req({"rotulo": "Perdidos"}), _eid(pool, "perdido")))
    assert r.status_code == 200
    assert _etapa(pool, "perdido")[3] == "Perdidos", "o que a tela mostra tinha que gravar"
    assert _regras(pool, "perdido")[:2] == (True, "contatado")


def test_salvar_o_fechado_nao_devolve_os_fechados_pro_quadro(monkeypatch, pool):
    """Na Prime, isto punha os 12 fechados de volta no quadro e parava de criar o
    compromisso na Agenda."""
    _logado(monkeypatch, pool)
    _como_na_prime(pool)
    asyncio.run(pp.regua_etapa(_Req({"rotulo": "Fechado", "gatilho": "contrato_assinado",
                                     "gatilho_ativo": "1"}), _eid(pool, "ganho")))
    assert _etapa(pool, "ganho")[1:] == ("contrato_assinado", True, "Fechado")
    assert _regras(pool, "ganho")[2:4] == (True, True)


def test_na_etapa_comum_desmarcar_continua_desligando(monkeypatch, pool):
    """O conserto é só das etapas de resultado. Nas outras, a caixinha está na tela,
    e caixinha desmarcada não vem no POST: ali, ausente TEM que ser desligado, senão
    ninguém consegue mais desmarcar nada."""
    _logado(monkeypatch, pool)
    with pool.connection() as c:
        c.execute("""update funil_etapas set exige_motivo=true, reativa_para='novo',
                            sai_do_quadro=true, agenda_ao_entrar=true
                      where conta_id=%s and chave='contatado'""", (CONTA,))
        c.commit()
    asyncio.run(pp.regua_etapa(_Req({"rotulo": "Contatado"}), _eid(pool, "contatado")))
    assert _regras(pool, "contatado")[:4] == (False, None, False, False)


def test_rotulo_vazio_nao_apaga_o_nome_da_etapa(monkeypatch, pool):
    _logado(monkeypatch, pool)
    asyncio.run(pp.regua_etapa(_Req({"rotulo": "  ", "gatilho": "sinal_pago"}), _eid(pool, "ganho")))
    assert _etapa(pool, "ganho")[3] == "Sinal Pago"


def test_vendedor_nao_configura_a_regua_da_empresa(monkeypatch, pool):
    _logado(monkeypatch, pool, gerencia=False)
    r = asyncio.run(pp.regua_etapa(_Req({"gatilho": "sinal_pago", "gatilho_ativo": "1"}),
                                   _eid(pool, "ganho")))
    assert r.status_code == 403
    assert _etapa(pool, "ganho")[2] is False


# ----------------------------------------------------------------- ritmo

def test_duracao_em_minutos_nao_vira_zero_hora():
    """A mediana desta equipe é 12 minutos. Arredondar pra hora apagaria justamente
    o número que mostra que os vendedores são rápidos."""
    assert pp._dur(12) == "12 min"
    assert pp._dur(59) == "59 min"
    assert pp._dur(90) == "1h30"
    assert pp._dur(60 * 30) == "1d 6h"


def test_medir_conta_as_mudas_e_os_cortes(monkeypatch, pool):
    """Os três cortes (2h/4h/8h) são o que deixa escolher o prazo com evidência:
    quando o número mal muda entre eles, a cauda é gente esquecida, não atrasada."""
    from finance import funil_regua as fr
    from datetime import datetime, timedelta, timezone
    agora = datetime.now(timezone.utc)
    with pool.connection() as c:
        for nome, atraso_h in [("RAPIDO", 0.2), ("LENTO", 6.0), ("MUDO", None)]:
            lead = c.execute("insert into prospeccao (conta_id, status) values (%s,'novo') returning id",
                             (CONTA,)).fetchone()[0]
            conv = c.execute("insert into conversas (conta_id, prospeccao_id) values (%s,%s) returning id",
                             (CONTA, lead)).fetchone()[0]
            entrada = agora - timedelta(days=1)
            c.execute("insert into mensagens (conversa_id, direcao, criado_em) values (%s,'in',%s)",
                      (conv, entrada))
            if atraso_h is not None:
                c.execute("insert into mensagens (conversa_id, direcao, criado_em) values (%s,'out',%s)",
                          (conv, entrada + timedelta(hours=atraso_h)))
        c.commit()
        d = fr.medir(c, CONTA, 21)
    assert d["mensagens"] == 3
    assert d["mudas"] == 1
    cortes = dict(d["cortes"])
    assert cortes["2 horas"] == 1 and cortes["8 horas"] == 0
    assert any(v["n"] for v in d["vendedores"])


def test_tempo_por_etapa_fica_vazio_sem_historico(monkeypatch, pool):
    """Honestidade: sem movimento gravado a tela diz "aguardando" em vez de inventar."""
    from finance import funil_regua as fr
    with pool.connection() as c:
        assert fr.medir(c, CONTA, 21)["etapas"] == []


# ------------------------------------------------------------ o modelo do ramo
# O bloco novo da Régua (11/09/2026). Jinja quebrado num `{% if %}` só apareceria
# em produção, na tela onde o dono conserta as coisas — então ele é renderizado
# aqui, nos dois estados que existem: com plano e sem plano.

def _html_modelo(modelo):
    """Renderiza SÓ o bloco do modelo, com o resto da tela no mínimo que ela pede.

    Desde 27/09/2026 o modelo mora no cabeçalho das etapas: igual ao modelo, vira um
    selo no título; com diferença, o formulário aparece antes da primeira linha."""
    from web.portal import _env
    tpl = pp._REGUA_TPL
    ini = tpl.index('<div class="fsec" id="etapas"')
    fim = tpl.index('<div class="rg-cab">')
    return _env.from_string(tpl[ini:fim] + "</div>").render(modelo=modelo, rot_ramo="eventos")


def test_bloco_do_modelo_diz_quando_nao_ha_o_que_mudar():
    html = _html_modelo({"itens": [], "colunas": ["Novo", "Contatado"], "fora": ["Fechado"]})
    assert "igual ao modelo de eventos ✓" in html
    assert 'title="Novo · Contatado"' in html, "as colunas do modelo ficam na dica do selo"
    assert "Adotar o que marquei" not in html, "botão de aplicar sem nada a aplicar"


def test_bloco_do_modelo_lista_os_itens_e_a_nota():
    from finance import funil_modelo as fm
    item = fm._item("quadro", "evento_realizado", de=False, para=True, leads=5,
                    texto="tirar “Evento A Realizar” do quadro", nota="5 leads continuam no cadastro")
    html = _html_modelo({"itens": [item], "colunas": ["Novo"], "fora": []})
    assert 'value="quadro:evento_realizado"' in html
    assert "5 leads" in html and "continuam no cadastro" in html
    assert "Adotar o que marquei" in html


def test_item_desmarcado_nao_vem_checked():
    from finance import funil_modelo as fm
    a = fm._item("rotulo", "perdido", de="Entregue", para="Perdido", marcado=False, texto="x")
    b = fm._item("rotulo", "ganho", de="Ganho", para="Fechado", texto="y")
    html = _html_modelo({"itens": [a, b], "colunas": [], "fora": []})
    assert html.count("checked") == 1, "a caixa do rótulo renomeado à mão veio marcada"


# ------------------------------------------------------------ a fase da etapa
# Editável desde 12/09/2026. Medido na conta 34: "Evento A Realizar" — festa já
# contratada, esperando acontecer — estava presa em fase 'venda', então as 5 festas
# dela não entravam nos ganhos do mês, e não havia botão na tela pra arrumar.

def _fase(pool, chave):
    with pool.connection() as c:
        return c.execute("""select fase, ordem from funil_etapas
                             where conta_id=%s and chave=%s""", (CONTA, chave)).fetchone()


def _nova(pool, chave, rotulo, ordem, fase="venda"):
    with pool.connection() as c:
        c.execute("""insert into funil_etapas (conta_id, chave, rotulo, ordem, fase)
                     values (%s,%s,%s,%s,%s) on conflict (conta_id, chave) do nothing""",
                  (CONTA, chave, rotulo, ordem, fase))
        c.commit()
    return _eid(pool, chave)


def test_a_etapa_de_venda_vira_pos_venda_e_a_tela_recarrega(monkeypatch, pool):
    """O caso da Prime: a coluna da festa contratada passa a contar como vendida."""
    eid = _nova(pool, "evento_a_realizar", "Evento A Realizar", 60)
    _logado(monkeypatch, pool)
    r = asyncio.run(pp.regua_etapa(_Req({"fase": "pos"}), eid))
    assert r.status_code == 200
    import json
    assert json.loads(bytes(r.body))["recarrega"] is True, "a coluna mudou de lugar e a tela não avisa"
    assert _fase(pool, "evento_a_realizar")[0] == "pos"


def test_a_pos_venda_vai_pra_depois_do_perdido(monkeypatch, pool):
    """A ORDEM anda junto com a fase, e esta é a razão: `escolher_etapa` decide o que
    está "à frente" pela ordem. Pós-venda em ordem 60 seria alcançável por um gatilho
    lá de Contatado — o lead pularia a venda inteira e cairia em pós-venda."""
    eid = _nova(pool, "entregue", "Entregue", 60)
    _logado(monkeypatch, pool)
    asyncio.run(pp.regua_etapa(_Req({"fase": "pos"}), eid))
    fase, ordem = _fase(pool, "entregue")
    assert fase == "pos"
    assert ordem > pp._ORDEM_PERDIDO, f"pós-venda ficou em {ordem}, antes de Perdido"


def test_voltar_pra_venda_traz_a_ordem_de_volta_pro_miolo(monkeypatch, pool):
    eid = _nova(pool, "montagem", "Montagem", 930, fase="pos")
    _logado(monkeypatch, pool)
    asyncio.run(pp.regua_etapa(_Req({"fase": "venda"}), eid))
    fase, ordem = _fase(pool, "montagem")
    assert fase == "venda"
    assert ordem < pp._ORDEM_GANHO, f"etapa de venda ficou em {ordem}, depois de Ganho"


def test_a_fase_das_fixas_nao_muda(monkeypatch, pool):
    """'novo' é a entrada; 'ganho' e 'perdido' são o resultado e estão fixos nas
    consultas de fechamento. Trocar a fase delas quebraria o relatório de todo mundo."""
    _logado(monkeypatch, pool)
    for chave in ("novo", "ganho", "perdido"):
        antes = _fase(pool, chave)
        asyncio.run(pp.regua_etapa(_Req({"fase": "pos" if antes[0] != "pos" else "venda"}),
                                   _eid(pool, chave)))
        assert _fase(pool, chave) == antes, f"a fase de {chave} mudou"


def test_fase_inventada_e_ignorada(monkeypatch, pool):
    eid = _nova(pool, "limbo", "Limbo", 70)
    _logado(monkeypatch, pool)
    for lixo in ("fechamento", "qualquer", ""):
        asyncio.run(pp.regua_etapa(_Req({"fase": lixo}), eid))
        assert _fase(pool, "limbo") == ("venda", 70), f"aceitou fase {lixo!r}"


def test_salvar_a_etapa_sem_mexer_na_fase_nao_reordena(monkeypatch, pool):
    """O formulário salva a linha inteira. Mandar a mesma fase de volta — que é o que
    acontece em todo salvamento normal — não pode empurrar a coluna de lugar."""
    eid = _nova(pool, "visita_feita", "Visita feita", 70)
    _logado(monkeypatch, pool)
    r = asyncio.run(pp.regua_etapa(_Req({"fase": "venda", "rotulo": "Visita feita"}), eid))
    import json
    assert json.loads(bytes(r.body))["recarrega"] is False
    assert _fase(pool, "visita_feita") == ("venda", 70), "reordenou sem a fase ter mudado"


def test_a_fase_so_aparece_pra_quem_pode_mudar():
    """O seletor não existe nas fixas. Desde 27/09/2026 a linha de cada etapa é um
    template próprio (`regua_etapa_linha`) e a fase mora em "mais regras"."""
    tpl = pp._REGUA_ETAPA_TPL
    assert 'name="fase"' in tpl and "já vendido · pós-venda" in tpl
    ini = tpl.index("{% if e.fixa %}")
    trecho = tpl[ini:ini + 900]
    assert "{% if e.fixa %}" in trecho and "{% else %}" in trecho, \
        "o seletor de fase não está atrás do portão das fixas"


# ----------------------------------------------------------------- a tela (27/09/2026)
#
# Os bugs que o PDF da régua da Prime mostrou (mockup
# docs/mockups/regua_funil_reorganizada.html, itens 2 a 6).

def _tela(**extra):
    """A Régua renderizada, com o mínimo de contexto que o template pede."""
    from datetime import time

    from finance import follow_up as fu
    from finance import raio_x_perfil as rxp
    from web.portal import _env
    t = _env.get_template("prospeccao_regua")
    etapa = {"id": 1, "chave": "qualificado", "rotulo": "Visita marcada", "fase": "venda",
             "fixa": False, "prazo_n": "", "prazo_u": "h", "n": 213, "gatilho": None,
             "gatilho_ativo": False, "teto_dias": None, "renovacoes_max": 0,
             "teto_total": 0, "exige_justificativa": True, "saidas_lista": [],
             "toques_dias": None, "exige_motivo": False, "sai_do_quadro": False,
             "agenda_ao_entrar": False, "reativa_para": None}
    base = dict(conta=None, aviso=None, etapas=[etapa], conv=[], eventos=[], unidades=[],
                dias_on={1, 2, 3, 4, 5, 6}, n_mov=0, gerencia=True, request=None,
                caps={"vendas": True, "origens": True}, raio_x_perfil=rxp.perfil("eventos"),
                tem_follow_up=True, modelo={"itens": [], "colunas": ["Novo"], "fora": []},
                escolhidas_tpl=set(), padrao_tpl={}, rot_ramo="eventos", janela_herda=True,
                esc={"n": "", "u": "h", "ph": "4", "herda": True},
                teto={"v": "", "ph": "5", "herda": True},
                fup={"proposta": {"v": "", "ph": "3", "herda": True},
                     "toques": {"v": "", "ph": "1,3,7", "herda": True},
                     "festa": {"v": "", "ph": "30", "herda": True, "tem": True},
                     "teto": {"v": "", "ph": "10", "herda": True}},
                rotinas_festa={"confirmar": True, "perguntar_veio": True, "depois_visita": True},
                festa_cfg={"proposta_validade_dias": 7, "reserva_disputada_h": 48,
                           "pos_festa": True, "avaliacao_link": ""},
                espelho={"de": None, "para": None}, equipe_espelho=[],
                motivos_conta=[], modelo_motivos=[],
                cfg=dict(fu._PADRAO, gatilhos_modo="off", cobranca_modo="off",
                         esteira_modo="ligado", temperatura_modo="ligado",
                         janela_abre=time(8), janela_fecha=time(19)))
    base.update(extra)
    return "".join(t.blocks["conteudo"](t.new_context(base)))


def test_as_caixinhas_das_rotinas_nao_esticam():
    """O CSS geral do app (web/portal.py) põe todo input com 100% de largura e 48px
    de altura. As caixinhas das rotinas não zeravam isso: cada uma ocupava a linha
    inteira e o texto ficava numa tira de uma palavra por linha (páginas 1 a 3 do
    PDF). As outras caixinhas da Régua sempre zeraram."""
    tpl = pp._REGUA_TPL
    for campo in ('id="rf_{{ campo }}"', 'id="rf_pos_festa"'):
        trecho = tpl[tpl.index(campo):]
        trecho = trecho[:trecho.index(">")]
        assert "width:auto" in trecho and "min-height:0" in trecho, campo


def test_a_contagem_tem_nome_e_a_chave_sai_da_vista():
    html = _tela()
    # "213" sozinho ao lado de "horas" parecia prazo
    assert ">213<span" in html and "leads</span>" in html
    # "Visita marcada · qualificado" contradizia o nome; a chave fica só na dica
    assert '<code class="mut" style="font-size:.68rem">qualificado</code>' not in html
    assert 'title="chave interna: qualificado"' in html


def test_o_texto_da_esteira_le_os_numeros_da_conta():
    """Era fixo: "10 leads ... cobra no dia 1, 3 e 7", qualquer que fosse a escada."""
    html = _tela(est_por_dia=8, est_dias_txt="2, 4 e 9")
    assert "8 leads por vendedor por dia" in html and "cobra no dia 2, 4 e 9" in html
    assert "10 leads por vendedor" not in html
    assert pp._dias_br((1, 3, 7)) == "1, 3 e 7" and pp._dias_br((5,)) == "5"


def test_o_aviso_da_temperatura_so_com_ela_desligada():
    """"Hoje todo lead é carimbado quente e nada esfria" aparecia com a temperatura
    LIGADA, que é o caso da Prime."""
    from datetime import time

    from finance import follow_up as fu
    ligada = _tela()
    assert "carimbado quente" not in ligada
    desligada = _tela(cfg=dict(fu._PADRAO, gatilhos_modo="off", cobranca_modo="off",
                               temperatura_modo="off", janela_abre=time(8),
                               janela_fecha=time(19)))
    assert "carimbado quente" in desligada


# ----------------------------------------------------------------- a régua reorganizada
#
# Mockup docs/mockups/regua_funil_reorganizada.html, aprovado em 27/09/2026. A tela
# mudou de ordem e recolheu o que quase ninguém usa. O risco de uma reorganização
# assim é um campo sair do formulário: aí salvar grava "desligado" nele, que é o
# mesmo defeito do Perdido e do Fechado. Estes testes fixam que tudo continua indo.

def _etapa_tela(chave, rotulo, **k):
    e = {"id": abs(hash(chave)) % 1000, "chave": chave, "rotulo": rotulo, "fase": "venda",
         "fixa": False, "prazo_n": "", "prazo_u": "h", "n": 0, "gatilho": None,
         "gatilho_ativo": False, "teto_dias": None, "renovacoes_max": 0, "teto_total": 0,
         "exige_justificativa": True, "saidas_lista": [], "toques_dias": None,
         "exige_motivo": False, "sai_do_quadro": False, "agenda_ao_entrar": False,
         "reativa_para": None}
    e.update(k)
    return e


def _etapas_prime():
    return [_etapa_tela("contatado", "Contatado", n=213, teto_dias=7, renovacoes_max=2,
                        teto_total=21, gatilho_ativo=True, gatilho="resposta_nossa"),
            _etapa_tela("follow_up", "Follow-up", sai_do_quadro=True),
            _etapa_tela("ganho", "Fechado", fase="fechamento", fixa=True, n=12,
                        sai_do_quadro=True, agenda_ao_entrar=True),
            _etapa_tela("perdido", "Perdido", fase="fechamento", fixa=True, n=70,
                        exige_motivo=True, reativa_para="contatado")]


def _forms(html):
    """{action: [trecho de cada <form> com essa action]} — a régua não aninha form."""
    out = {}
    for pedaco in html.split("<form")[1:]:
        corpo = pedaco[:pedaco.index("</form>")]
        acao = corpo.split('action="', 1)[1].split('"', 1)[0]
        out.setdefault(acao, []).append(corpo)
    return out


def test_recolher_nao_tira_campo_de_nenhum_formulario():
    html = _tela(etapas=_etapas_prime(), conv=[
        {"chave": "sem_resposta", "rotulo": "Sem resposta", "n": "", "u": "h", "ph": "2", "herda": True}],
        unidades=[("min", "min"), ("h", "horas"), ("d", "dias")],
        est_por_dia=10, est_dias_txt="1, 3 e 7", fu_ligado=True)
    f = _forms(html)
    config = f["/painel/prospeccao/regua/config"][0]
    for campo in ("gatilhos_modo", "cobranca_modo", "teto_modo", "perdido_modo", "esteira_modo",
                  "temperatura_modo", "fila_modo", "sem_resposta_n", "sem_resposta_u",
                  "escala_n", "escala_u", 'name="teto"', "fu_proposta_dias", "fu_toques_dias",
                  "fu_festa_dias", "fu_teto_dia", "temp_quente_h", "temp_morno_dias",
                  "temp_frio_tentativas", 'name="dias"', 'name="abre"', 'name="fecha"'):
        assert campo in config, f"{campo} saiu do formulário das automações"
    assert "follow_up_modo" not in config, "a chave do follow-up voltou pra Régua"
    # o espelho mora no cartão da Esteira, mas salva no formulário dele
    assert f["/painel/prospeccao/regua/espelho"] == [' method="post" action="/painel/prospeccao/regua/espelho" id="f-espelho">']
    assert 'name="espelho_de" form="f-espelho"' in html and 'name="espelho_para" form="f-espelho"' in html
    # a etapa comum: tudo o que "mais regras" recolhe continua no formulário dela
    contatado = [x for x in f.values() for x in x if 'value="Contatado"' in x][0]
    for campo in ("rotulo", "gatilho_ativo", "gatilho", "teto_dias", "renovacoes_max",
                  "exige_justificativa", "toques_dias", "exige_motivo", "agenda_ao_entrar",
                  "sai_do_quadro", "reativa_para", "saidas", "fase", "prazo_n", "prazo_u"):
        assert f'name="{campo}"' in contatado, f"{campo} saiu do formulário da etapa"


def test_perdido_e_fechado_mostram_as_regras_e_o_fechado_fica_fora_do_quadro():
    html = _tela(etapas=_etapas_prime(), unidades=[("h", "horas")])
    assert "pede motivo" in html and "reabre em Contatado se voltar a falar" in html
    assert "cria compromisso" in html
    fora = html[html.index('<details class="rg-fora">'):]
    assert "Fechado (12)" in fora and "Follow-up (0)" in fora
    # e o Perdido, de resultado, não ganha "mais regras" (o servidor mantém as dele)
    perdido = [x for x in _forms(html).values() for x in x if 'value="Perdido"' in x][0]
    assert "rg-mais" not in perdido and 'name="reativa_para"' not in perdido


def test_numeros_de_motor_desligado_ficam_recolhidos_mas_no_formulario():
    html = _tela(conv=[{"chave": "bola_nossa", "rotulo": "Bola com você", "n": "", "u": "h",
                        "ph": "4", "herda": True}], unidades=[("h", "horas")])
    cobr = html[html.index("<h5>Cobrança por prazo</h5>"):]
    cobr = cobr[:cobr.index("</details>")]
    assert '<details class="rg-num" >' in cobr, "cobrança desligada tinha que vir recolhida"
    assert 'name="bola_nossa_n"' in cobr and "bola com você 4 horas" in cobr


def test_quem_nao_vende_festa_nao_ve_festa():
    """§6: a ZAQ (recorrente) vê a mesma régua sem nenhum bloco ou palavra de festa."""
    from finance import raio_x_perfil as rxp
    html = _tela(rotinas_festa=None, festa_cfg=None, raio_x_perfil=rxp.perfil("recorrente"),
                 fup={"proposta": {"v": "", "ph": "3", "herda": True},
                      "toques": {"v": "", "ph": "1,3,7", "herda": True},
                      "festa": {"v": "", "ph": "—", "herda": True, "tem": False},
                      "teto": {"v": "", "ph": "10", "herda": True}},
                 rot_ramo="serviço recorrente")
    assert "Rotinas de festa" not in html and "#rotinas" not in html
    import re
    visivel = re.sub(r"<!--.*?-->", "", html, flags=re.S).lower()
    assert "festa" not in visivel
