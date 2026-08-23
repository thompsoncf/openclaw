"""O "Esqueci minha senha" que não servia pra vendedor.

O DEFEITO
`/esqueci-senha` consultava só `contas`. Quem entra como MEMBRO — vendedor,
gestor, financeiro — digitava o e-mail, lia "enviamos o link" e ficava esperando
um link que nunca nasceu: a consulta não achava a linha, nenhum token era criado,
e a tela respondia "enviado" do mesmo jeito. `/redefinir-senha` tinha o outro lado
do mesmo buraco — `update contas set senha_hash`, então nem com token na mão a
senha de um membro mudaria.

Caso real: `docemellvendas@gmail.com` (Maze, vendedora da conta 35) ficou fora do
app sem caminho de volta. Em 22/08/2026 eram 5 membros no mesmo beco.

O QUE ESTE ARQUIVO FIXA
Menos a tela e mais a REGRA que a tela obedece — quem o e-mail reseta, e onde a
senha nova é escrita. Em especial a parte que é fácil de errar de novo: quando o
e-mail é de conta E de membro, a autoridade é a CONTA. Resetar a linha de membro
ali criaria uma segunda senha válida, e trocar uma não mexeria na outra — é o bug
que `finance.cockpit.definir_senha` já se recusava a criar.
"""
import os
import re
import shutil
import subprocess

import pytest
from psycopg_pool import ConnectionPool

from contas import equipe as eq
from contas import senha as _senha

_SQL = """
create table contas (id bigserial primary key, nome text, email text, senha_hash text);
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text default 'vendedor', ativo boolean default true, senha_hash text,
  convite_token text, convite_expira timestamptz);
create table tokens_reset_senha (
  token text primary key,
  conta_id bigint references contas(id),
  membro_id bigint references membros(id) on delete cascade,
  criado_em timestamptz not null default now(),
  expira_em timestamptz not null,
  usado boolean not null default false,
  constraint tokens_reset_senha_alvo_check check (num_nonnulls(conta_id, membro_id) = 1)
);
"""


@pytest.fixture()
def pool():
    dbname = "zaq_reset_membro"
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1,
                           open=True, kwargs={"autocommit": True, "prepare_threshold": None})
    with admin.connection() as c:
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity "
                  "where datname=%s and pid <> pg_backend_pid()", (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True,
                       kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        c.commit()
    yield p
    p.close()


def _conta(pool, nome, email=None, senha=None):
    with pool.connection() as c:
        cid = c.execute(
            "insert into contas (nome, email, senha_hash) values (%s,%s,%s) returning id",
            (nome, email, _senha.hash_senha(senha) if senha else None)).fetchone()[0]
        c.commit()
    return cid


def _membro(pool, conta_id, nome, email, senha=None, ativo=True, papel="vendedor"):
    with pool.connection() as c:
        mid = c.execute(
            """insert into membros (conta_id, nome, email, papel, ativo, senha_hash)
               values (%s,%s,%s,%s,%s,%s) returning id""",
            (conta_id, nome, email, papel, ativo,
             _senha.hash_senha(senha) if senha else None)).fetchone()[0]
        c.commit()
    return mid


def _hash_membro(pool, mid):
    with pool.connection() as c:
        return c.execute("select senha_hash from membros where id=%s", (mid,)).fetchone()[0]


def _hash_conta(pool, cid):
    with pool.connection() as c:
        return c.execute("select senha_hash from contas where id=%s", (cid,)).fetchone()[0]


@pytest.fixture()
def doce_mell(pool):
    """A conta 35 como está na produção: a dona tem conta própria, a vendedora não.

    É essa assimetria que produz o bug — o e-mail da Louana existe em `contas`, o
    da Maze só em `membros`, e a busca antiga só enxergava a primeira.
    """
    cid = _conta(pool, "Doce Mell", "docemellsul@gmail.com", "senhaDaLouana1")
    maze = _membro(pool, cid, "Maze", "docemellvendas@gmail.com", "senhaAntiga1")
    return {"conta": cid, "maze": maze}


# ═════════════════════ quem o e-mail reseta ═════════════════════

def test_email_de_conta_reseta_a_conta(pool, doce_mell):
    alvo = eq.alvo_do_reset(pool, "docemellsul@gmail.com")
    assert alvo["tipo"] == "conta"
    assert alvo["conta_id"] == doce_mell["conta"]
    assert alvo["membro_id"] is None


def test_email_so_de_membro_acha_o_membro(pool, doce_mell):
    """O CASO DO BUG. Antes isto devolvia nada e a Maze ficava sem link."""
    alvo = eq.alvo_do_reset(pool, "docemellvendas@gmail.com")
    assert alvo is not None, "vendedora sem caminho de volta — é o defeito original"
    assert alvo["tipo"] == "membro"
    assert alvo["membro_id"] == doce_mell["maze"]
    assert alvo["conta_id"] is None


def test_quando_e_os_dois_a_conta_manda(pool):
    """Mesma pessoa com conta própria E vínculo de membro: reseta a CONTA.

    Se resetasse o membro, ela passaria a ter duas senhas válidas e trocar uma não
    mexeria na outra — exatamente o que `cockpit.definir_senha` recusa fazer.
    """
    propria = _conta(pool, "Márcio consultoria", "marcio@x.com", "aSenhaDele1")
    empresa = _conta(pool, "Doce Mell", "docemellsul@gmail.com", "outra1234")
    _membro(pool, empresa, "Márcio", "marcio@x.com", "senhaDeMembro1")

    alvo = eq.alvo_do_reset(pool, "marcio@x.com")
    assert alvo["tipo"] == "conta"
    assert alvo["conta_id"] == propria
    assert alvo["membro_id"] is None


def test_membro_inativo_nao_volta_por_formulario_publico(pool, doce_mell):
    """Suspenso não se reativa sozinho, e quem tem convite pendente
    (`regerar_convite` deixa `ativo=false`) tem o link do convite pra isso."""
    _membro(pool, doce_mell["conta"], "Márcio", "suspenso@x.com", "qualquer1", ativo=False)
    assert eq.alvo_do_reset(pool, "suspenso@x.com") is None


def test_email_desconhecido_e_vazio_nao_geram_alvo(pool, doce_mell):
    assert eq.alvo_do_reset(pool, "ninguem@lugar.com") is None
    assert eq.alvo_do_reset(pool, "") is None
    assert eq.alvo_do_reset(pool, None) is None


def test_maiuscula_e_espaco_nao_atrapalham(pool, doce_mell):
    """Quem digita no celular manda "  DoceMellVendas@Gmail.com " — e o e-mail
    guardado é minúsculo. Sem normalizar, a vendedora continuaria trancada."""
    alvo = eq.alvo_do_reset(pool, "  DoceMellVendas@Gmail.com ")
    assert alvo is not None and alvo["membro_id"] == doce_mell["maze"]


# ═════════════════════ onde a senha é escrita ═════════════════════

def test_reset_de_conta_nao_encosta_no_membro(pool, doce_mell):
    antes = _hash_membro(pool, doce_mell["maze"])
    assert eq.gravar_senha_do_reset(pool, doce_mell["conta"], None, "novaDaDona9")

    assert _senha.verificar_senha("novaDaDona9", _hash_conta(pool, doce_mell["conta"]))
    assert _hash_membro(pool, doce_mell["maze"]) == antes, "reset da dona mexeu na vendedora"


def test_reset_de_membro_muda_o_membro_e_nao_a_conta(pool, doce_mell):
    """O OUTRO LADO DO BUG: `redefinir-senha` escrevia sempre em `contas`, então a
    senha do membro não mudava nem com token válido na mão."""
    antes_conta = _hash_conta(pool, doce_mell["conta"])
    assert eq.gravar_senha_do_reset(pool, None, doce_mell["maze"], "novaDaMaze9")

    assert _senha.verificar_senha("novaDaMaze9", _hash_membro(pool, doce_mell["maze"]))
    assert _hash_conta(pool, doce_mell["conta"]) == antes_conta, "reset da vendedora mexeu na dona"


def test_a_senha_velha_para_de_valer(pool, doce_mell):
    eq.gravar_senha_do_reset(pool, None, doce_mell["maze"], "novaDaMaze9")
    assert not _senha.verificar_senha("senhaAntiga1", _hash_membro(pool, doce_mell["maze"]))


def test_senha_nunca_fica_em_texto_puro(pool, doce_mell):
    eq.gravar_senha_do_reset(pool, None, doce_mell["maze"], "novaDaMaze9")
    guardado = _hash_membro(pool, doce_mell["maze"])
    assert "novaDaMaze9" not in guardado
    assert guardado.startswith("scrypt$")


def test_pessoa_em_duas_empresas_fica_com_uma_credencial_so(pool):
    """`contextos_de_login` procura a senha de membro com `limit 1` e SEM `order by`.
    Com dois vínculos e hashes diferentes, entrar viraria sorteio. O reset escreve
    nos dois — a pessoa é uma só."""
    a = _conta(pool, "Doce Mell", "a@x.com", "x1234567")
    b = _conta(pool, "Prime", "b@x.com", "y1234567")
    m1 = _membro(pool, a, "Maze", "maze@x.com", "antiga1234")
    m2 = _membro(pool, b, "Maze", "maze@x.com", "antiga1234")

    assert eq.gravar_senha_do_reset(pool, None, m1, "novaUnica99")

    assert _senha.verificar_senha("novaUnica99", _hash_membro(pool, m1))
    assert _senha.verificar_senha("novaUnica99", _hash_membro(pool, m2)), \
        "o segundo vínculo ficou com a senha velha — o login vira sorteio"


def test_reset_de_membro_nao_mexe_em_ativo(pool, doce_mell):
    """A RAZÃO DE ISTO EXISTIR EM VEZ DO RECONVITE. `regerar_convite` faz
    `ativo=false`, e membro inativo sai de `fila_ids` — na Doce Mell a Maze era a
    ÚNICA da fila do rodízio, então destravá-la por ali esvaziaria a fila e todo
    lead novo nasceria sem dono. Este caminho só troca a senha."""
    with pool.connection() as c:
        antes = c.execute("select ativo, papel, conta_id from membros where id=%s",
                          (doce_mell["maze"],)).fetchone()

    eq.gravar_senha_do_reset(pool, None, doce_mell["maze"], "novaDaMaze9")

    with pool.connection() as c:
        depois = c.execute("select ativo, papel, conta_id from membros where id=%s",
                           (doce_mell["maze"],)).fetchone()
    assert depois == antes, "o reset mudou mais do que a senha"
    assert depois[0] is True


# ═════════════════════ ponta a ponta ═════════════════════

def test_a_maze_destravada_entra_de_verdade(pool, doce_mell):
    """O fluxo inteiro da vendedora trancada: pede o reset, escolhe a senha, entra.

    Passa por `autenticar`, que é quem o app do vendedor chama — não adianta o
    hash estar certo na tabela se o login não chega até ele.
    """
    alvo = eq.alvo_do_reset(pool, "docemellvendas@gmail.com")
    assert eq.gravar_senha_do_reset(pool, alvo["conta_id"], alvo["membro_id"], "voltei12345")

    ok = eq.autenticar(pool, "docemellvendas@gmail.com", "voltei12345")
    assert ok is not None, "senha trocada e mesmo assim não entra"
    assert ok["membro_id"] == doce_mell["maze"]
    assert ok["conta_id"] == doce_mell["conta"]
    assert ok["papel"] == "vendedor"

    assert eq.autenticar(pool, "docemellvendas@gmail.com", "senhaAntiga1") is None


def test_dona_continua_entrando_pela_conta_dela(pool, doce_mell):
    """Trilho de segurança: consertar o membro não pode ter quebrado o caminho da
    conta, que é o que já funcionava."""
    alvo = eq.alvo_do_reset(pool, "docemellsul@gmail.com")
    assert eq.gravar_senha_do_reset(pool, alvo["conta_id"], alvo["membro_id"], "novaDaDona9")

    ctxs = eq.contextos_de_login(pool, "docemellsul@gmail.com", "novaDaDona9")
    assert any(x["tipo"] == "conta" and x["conta_id"] == doce_mell["conta"] for x in ctxs)
    assert eq.contextos_de_login(pool, "docemellsul@gmail.com", "senhaDaLouana1") == []


# ═════════════════════ o token ═════════════════════

def test_token_precisa_apontar_pra_exatamente_um_alvo(pool, doce_mell):
    """O check da migração 185. Um token que não sabe quem reseta faria
    `redefinir-senha` escrever no lugar errado — ou em lugar nenhum."""
    from psycopg.errors import CheckViolation
    with pool.connection() as c:
        for conta_id, membro_id, caso in [
            (None, None, "sem alvo"),
            (doce_mell["conta"], doce_mell["maze"], "dois alvos"),
        ]:
            with pytest.raises(CheckViolation), c.transaction(force_rollback=True):
                c.execute("""insert into tokens_reset_senha (token, conta_id, membro_id, expira_em)
                             values (%s,%s,%s, now() + interval '1 hour')""",
                          (f"t-{caso}", conta_id, membro_id))


def test_token_de_membro_e_de_conta_cabem_na_tabela(pool, doce_mell):
    with pool.connection() as c:
        c.execute("""insert into tokens_reset_senha (token, conta_id, membro_id, expira_em)
                     values ('t-conta',%s,null, now() + interval '1 hour')""",
                  (doce_mell["conta"],))
        c.execute("""insert into tokens_reset_senha (token, conta_id, membro_id, expira_em)
                     values ('t-membro',null,%s, now() + interval '1 hour')""",
                  (doce_mell["maze"],))
        c.commit()
        n = c.execute("select count(*) from tokens_reset_senha").fetchone()[0]
    assert n == 2


# ═════════════════════ a senha provisória do dono ═════════════════════

def test_senha_provisoria_e_forte_o_bastante_e_sempre_diferente():
    vistas = {_senha.gerar_temporaria() for _ in range(200)}
    assert len(vistas) > 190, "gerador repetindo demais"
    for s in vistas:
        assert len(s) >= 8, "curta demais pro mínimo do formulário"
        assert not set(s) & set("0O1lI"), "caractere ambíguo pra ditar no telefone"


def test_senha_provisoria_funciona_como_senha(pool, doce_mell):
    s = _senha.gerar_temporaria()
    eq.gravar_senha_do_reset(pool, None, doce_mell["maze"], s)
    assert eq.autenticar(pool, "docemellvendas@gmail.com", s) is not None


# ═════════════════════ as rotas, com a fiação ═════════════════════
#
# O motor certo com a rota ligada errado deixaria o defeito de pé — era esse o
# formato do bug: a regra nunca foi consultada porque o endpoint fazia o SQL na
# mão. Estes exercitam `web.portal` de verdade.

class _Req:
    """O mínimo que `_render` toca."""
    def __init__(self):
        self.session = {}
        self.state = type("S", (), {})()
        self.headers = {}

    class url:  # noqa: N801
        path = "/esqueci-senha"


class _Bg:
    def __init__(self):
        self.tarefas = []

    def add_task(self, fn, *args, **kwargs):
        self.tarefas.append((fn, args, kwargs))


@pytest.fixture()
def portal_ligado(monkeypatch, pool):
    """`web.portal` apontando pro banco do teste, com SMTP fingido de configurado."""
    import web.portal as portal
    import finance.email_sender as es
    monkeypatch.setattr(portal, "_render", lambda nome, req, **ctx: ctx, raising=True)
    monkeypatch.setattr(portal, "get_pool", lambda: pool, raising=True)
    monkeypatch.setattr(es, "remetente_configurado", lambda: "contato@zaq-ia.com", raising=True)
    return portal


def _tokens(pool):
    with pool.connection() as c:
        return c.execute("select token, conta_id, membro_id from tokens_reset_senha").fetchall()


def test_a_rota_cria_token_pra_vendedora(portal_ligado, pool, doce_mell):
    """O DEFEITO EXATO: a rota olhava só `contas`, então pra Maze nenhum token
    nascia — e a tela dizia "enviamos" assim mesmo."""
    bg = _Bg()
    ctx = portal_ligado.esqueci_senha_envia(_Req(), bg, email="docemellvendas@gmail.com")

    linhas = _tokens(pool)
    assert len(linhas) == 1, "nenhum token criado — a vendedora segue sem caminho de volta"
    assert linhas[0][1] is None and linhas[0][2] == doce_mell["maze"]
    assert ctx["enviado"] is True
    assert bg.tarefas, "o e-mail com o link não foi enfileirado"
    assert linhas[0][0] in bg.tarefas[0][1][1], "o link não carrega o token criado"


def test_a_rota_continua_criando_token_pra_conta(portal_ligado, pool, doce_mell):
    portal_ligado.esqueci_senha_envia(_Req(), _Bg(), email="docemellsul@gmail.com")
    linhas = _tokens(pool)
    assert len(linhas) == 1
    assert linhas[0][1] == doce_mell["conta"] and linhas[0][2] is None


def test_email_de_ninguem_nao_cria_token_e_nao_denuncia(portal_ligado, pool, doce_mell):
    bg = _Bg()
    ctx = portal_ligado.esqueci_senha_envia(_Req(), bg, email="ninguem@lugar.com")
    assert _tokens(pool) == [] and not bg.tarefas
    assert ctx["enviado"] is True, "resposta diferente entrega quem é cliente do Zaq"
    assert not ctx["erro"]


def test_redefinir_pelo_token_da_vendedora_troca_a_senha_dela(portal_ligado, pool, doce_mell):
    """O outro lado: a rota escrevia sempre em `contas`, então nem com token
    válido a senha do membro mudava."""
    portal_ligado.esqueci_senha_envia(_Req(), _Bg(), email="docemellvendas@gmail.com")
    token = _tokens(pool)[0][0]
    antes_dona = _hash_conta(pool, doce_mell["conta"])

    portal_ligado.redefinir_senha_envia(_Req(), token=token, senha="voltei12345")

    assert eq.autenticar(pool, "docemellvendas@gmail.com", "voltei12345") is not None
    assert _hash_conta(pool, doce_mell["conta"]) == antes_dona, "escreveu na conta errada"


def test_token_so_serve_uma_vez(portal_ligado, pool, doce_mell):
    portal_ligado.esqueci_senha_envia(_Req(), _Bg(), email="docemellvendas@gmail.com")
    token = _tokens(pool)[0][0]
    portal_ligado.redefinir_senha_envia(_Req(), token=token, senha="primeira1234")

    ctx = portal_ligado.redefinir_senha_envia(_Req(), token=token, senha="segunda12345")
    assert ctx.get("valido") is False
    assert eq.autenticar(pool, "docemellvendas@gmail.com", "segunda12345") is None
    assert eq.autenticar(pool, "docemellvendas@gmail.com", "primeira1234") is not None


def test_senha_curta_nao_queima_o_token(portal_ligado, pool, doce_mell):
    """Errar o tamanho não pode custar o link — a pessoa corrige e continua."""
    portal_ligado.esqueci_senha_envia(_Req(), _Bg(), email="docemellvendas@gmail.com")
    token = _tokens(pool)[0][0]

    ctx = portal_ligado.redefinir_senha_envia(_Req(), token=token, senha="curta")
    assert ctx.get("valido") is True and ctx.get("erro")

    portal_ligado.redefinir_senha_envia(_Req(), token=token, senha="agoraVale123")
    assert eq.autenticar(pool, "docemellvendas@gmail.com", "agoraVale123") is not None


def test_token_expirado_nao_troca_senha(portal_ligado, pool, doce_mell):
    with pool.connection() as c:
        c.execute("""insert into tokens_reset_senha (token, membro_id, expira_em)
                     values ('velho',%s, now() - interval '1 hour')""", (doce_mell["maze"],))
        c.commit()
    ctx = portal_ligado.redefinir_senha_envia(_Req(), token="velho", senha="naoDeviaEntrar1")
    assert ctx.get("valido") is False
    assert eq.autenticar(pool, "docemellvendas@gmail.com", "naoDeviaEntrar1") is None


# ═════════════════════ o confirm que o navegador recebe ═════════════════════

def test_os_confirm_do_painel_de_equipe_compilam():
    """`_EQUIPE_TPL` é string Python COMUM (`\"\"\"...\"\"\"`, sem `r`), então um `\\n`
    escrito dentro do `confirm('...')` vira quebra de linha LITERAL no HTML —
    e quebra de linha crua dentro de string JS de aspas simples é erro de sintaxe.
    O handler não compila, e o form passa DIRETO, sem perguntar nada.

    Não é hipótese: os três `onsubmit` com `\\n` deste template estavam assim, e o
    de "Liberar campanhas" já ia pro ar sem confirmação nenhuma. `node --check` no
    .py fonte não pega — só depois do render, que é o que este teste faz.

    Mesma armadilha de tests/test_portal_js_sintaxe.py, num arquivo que aquele não
    alcança: lá são blocos `<script>` de `web/portal.py`, aqui é atributo inline.
    """
    node = shutil.which("node")
    if not node:
        pytest.skip("node não disponível")
    from web import painel_equipe as pe

    handlers = re.findall(r'onsubmit="(return confirm\(.*?\))"', pe._EQUIPE_TPL, re.S)
    assert len(handlers) >= 4, "sumiu handler — o teste precisa acompanhar o template"

    js = "\n".join(f"(function(){{ {h} }});" for h in handlers)
    r = subprocess.run([node, "--check", "-"], input=js, text=True, capture_output=True)
    assert r.returncode == 0, f"onsubmit não compila:\n{r.stderr}"


def test_nenhum_confirm_do_painel_tem_quebra_de_linha_crua():
    """A causa raiz, dita direto — pega mesmo sem node instalado."""
    from web import painel_equipe as pe
    for h in re.findall(r'onsubmit="(return confirm\(.*?\))"', pe._EQUIPE_TPL, re.S):
        assert "\n" not in h, (
            "quebra de linha REAL dentro do confirm — escreva \\\\n no fonte Python "
            f"pra chegar como \\n no HTML: {h[:80]!r}")
