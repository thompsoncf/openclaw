"""GUARDAR o arquivo de vez — o passo 5 da mídia.

POR QUE ISTO EXISTE
Os passos 1 a 4 nunca guardam arquivo: a mensagem traz o endereço no CDN do
WhatsApp e a chave, e quem abre a conversa busca lá na hora. Foi a escolha certa —
guardar tudo daria ~110 GB por ano numa conta só contra 22 MB de ponteiro.

Só que o CDN EXPIRA. Foto de referência de decoração pode expirar: pede de novo ao
cliente. O COMPROVANTE DO SINAL e o CONTRATO ASSINADO não podem — são o registro
do negócio, e o dia em que se precisa deles é o dia da discussão, meses depois.

A ESCOLHA: SELETIVO
Quem sabe o que vira registro é o vendedor, no momento em que recebe. Um botão que
ele aperta guarda o que importa; uma regra automática ou guarda demais (o custo já
recusado) ou de menos — e aí não dá pra confiar nela.

O QUE ESTE ARQUIVO PRENDE
 1. o portão é o MESMO da mídia (mensagem do lead + lead do vendedor);
 2. guardar é IDEMPOTENTE — dois toques não viram dois arquivos;
 3. o teto para o download ANTES de baixar tudo;
 4. o caminho vai pro banco, a URL nunca;
 5. depois de guardado, a rota serve do NOSSO bucket e o CDN vira plano B.
"""
import inspect
import os

import pytest
from psycopg_pool import ConnectionPool

from finance import cockpit as ck
from finance import midia_cofre as mc
from tests.test_cockpit import _BASE_SQL as _SQL
from web import painel_cockpit as pc

CAMINHO = "/v/t62.7118-24/abc_n.enc"
CHAVE = "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY="


@pytest.fixture()
def pool():
    dbname = "zaq_midia_cofre"
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


@pytest.fixture()
def cena(pool, monkeypatch):
    """Um lead do vendedor, com uma foto recebida (ponteiro, sem arquivo)."""
    with pool.connection() as c:
        conta = c.execute("insert into contas (nome) values ('Prime') returning id").fetchone()[0]
        vend = c.execute("insert into membros (conta_id, nome) values (%s,'Rob') returning id",
                         (conta,)).fetchone()[0]
        lead = c.execute("""insert into prospeccao (conta_id, vendedor_id, empresa, whatsapp)
                            values (%s,%s,'Buffet','5586999990000') returning id""",
                         (conta, vend)).fetchone()[0]
        conv = c.execute("""insert into conversas (conta_id, prospeccao_id, canal, agente_ativo)
                            values (%s,%s,'whatsapp',false) returning id""",
                         (conta, lead)).fetchone()[0]
        msg = c.execute(
            """insert into mensagens (conversa_id, canal, direcao, autor, texto,
                                      midia_ref, midia_tipo, midia_meta)
               values (%s,'whatsapp','in','lead','📄 Documento',
                       %s,'documento',%s) returning id""",
            (conv, '{"directPath": "%s", "mediaKey": "%s", "mimetype": "application/pdf"}'
             % (CAMINHO, CHAVE), '{"bytes": 8626, "nome": "RECIBO PIX.pdf"}')).fetchone()[0]
        c.commit()
    monkeypatch.setattr(mc, "configurado", lambda: True)
    return {"pool": pool, "conta": conta, "vend": vend, "lead": lead,
            "conv": conv, "msg": msg}


@pytest.fixture()
def cofre(monkeypatch):
    """Intercepta o Storage: guarda o que teria subido."""
    caixa = []

    def _sobe(caminho, conteudo, ct):
        caixa.append({"caminho": caminho, "bytes": conteudo, "ct": ct})
        return caminho

    from finance import comprovantes
    monkeypatch.setattr(comprovantes, "subir_em", _sobe)
    return caixa


@pytest.fixture()
def cdn(monkeypatch):
    """O CDN devolvendo um PDF pequeno, em pedaços — como o de verdade."""
    from finance import wa_midia
    monkeypatch.setattr(wa_midia, "buscar",
                        lambda ref, tipo, **kw: iter([b"%PDF-1.7", b" conteudo", b" do recibo"]))


def _guardado(cena):
    with cena["pool"].connection() as c:
        return c.execute("""select midia_arquivo, midia_guardada_por, midia_guardada_em
                              from mensagens where id=%s""", (cena["msg"],)).fetchone()


# ------------------------------------------------------------------ o caminho feliz

def test_guardar_copia_do_cdn_pro_bucket_e_marca_a_mensagem(cena, cofre, cdn):
    r = ck.guardar_midia(cena["pool"], cena["conta"], cena["vend"],
                         cena["lead"], cena["msg"])
    assert r["ok"] is True
    assert cofre[0]["bytes"] == b"%PDF-1.7 conteudo do recibo", "subiu o arquivo inteiro"
    arquivo, por, quando = _guardado(cena)
    assert arquivo == cofre[0]["caminho"]
    assert por == cena["vend"], "quem mandou guardar fica registrado — é o que se pergunta depois"
    assert quando is not None


def test_o_caminho_leva_a_conta_no_comeco(cena, cofre, cdn):
    """Igual ao dos comprovantes: a leitura confere que o objeto é da conta que
    pediu, e um dia dá pra apagar tudo de uma conta por prefixo."""
    ck.guardar_midia(cena["pool"], cena["conta"], cena["vend"], cena["lead"], cena["msg"])
    assert cofre[0]["caminho"].startswith(f"conversa/{cena['conta']}/")


def test_o_arquivo_ganha_a_extensao_certa(cena, cofre, cdn):
    """Um contrato salvo como `.bin` é um contrato que ninguém abre no dia da
    discussão — é a extensão que faz o aparelho saber abrir."""
    ck.guardar_midia(cena["pool"], cena["conta"], cena["vend"], cena["lead"], cena["msg"])
    assert cofre[0]["caminho"].endswith(".pdf")
    assert cofre[0]["ct"] == "application/pdf"


def test_o_banco_guarda_o_CAMINHO_e_nunca_uma_url(cena, cofre, cdn):
    """URL de bucket privado é assinada e expira; e se um dia virar pública, o link
    já vazou. O caminho só serve pra quem tem a chave de serviço."""
    ck.guardar_midia(cena["pool"], cena["conta"], cena["vend"], cena["lead"], cena["msg"])
    arquivo, _, _ = _guardado(cena)
    assert not arquivo.startswith("http"), arquivo
    assert "supabase" not in arquivo.lower()


# ------------------------------------------------------------------ dois toques

def test_guardar_duas_vezes_nao_guarda_duas_vezes(cena, cofre, cdn):
    """O vendedor toca sem querer, ou dois aparelhos dele fazem a mesma coisa."""
    ck.guardar_midia(cena["pool"], cena["conta"], cena["vend"], cena["lead"], cena["msg"])
    r2 = ck.guardar_midia(cena["pool"], cena["conta"], cena["vend"], cena["lead"], cena["msg"])
    assert r2["ok"] is True and r2.get("ja_estava") is True
    assert len(cofre) == 1, "não pode ter baixado e subido de novo"


# ------------------------------------------------------------------ os portões

def test_o_lead_de_outro_vendedor_nao_guarda(cena, cofre, cdn):
    """O id da mensagem é sequencial e adivinhável: o portão é o MESMO da mídia."""
    with cena["pool"].connection() as c:
        outro = c.execute("insert into membros (conta_id, nome) values (%s,'Outro') returning id",
                          (cena["conta"],)).fetchone()[0]
        c.commit()
    r = ck.guardar_midia(cena["pool"], cena["conta"], outro, cena["lead"], cena["msg"])
    assert r["ok"] is False and cofre == []


def test_mensagem_de_outro_lead_nao_guarda(cena, cofre, cdn):
    r = ck.guardar_midia(cena["pool"], cena["conta"], cena["vend"], cena["lead"] + 999, cena["msg"])
    assert r["ok"] is False and cofre == []


def test_mensagem_sem_midia_nao_guarda(cena, cofre, cdn):
    with cena["pool"].connection() as c:
        so_texto = c.execute(
            """insert into mensagens (conversa_id, canal, direcao, autor, texto)
               values (%s,'whatsapp','in','lead','oi') returning id""",
            (cena["conv"],)).fetchone()[0]
        c.commit()
    r = ck.guardar_midia(cena["pool"], cena["conta"], cena["vend"], cena["lead"], so_texto)
    assert r["ok"] is False and cofre == []


# ------------------------------------------------------------------ quando dá errado

def test_expirado_diz_o_que_fazer(cena, cofre, monkeypatch):
    """Guardar chegou TARDE. O recado tem que dizer a saída, que é pedir de novo —
    e não um 'falhou' que deixa o vendedor tentando o mesmo botão."""
    from finance import wa_midia
    def _expirou(ref, tipo, **kw):
        raise wa_midia.Expirou(410)
        yield  # noqa: unreachable — mantém a assinatura de gerador
    monkeypatch.setattr(wa_midia, "buscar", _expirou)
    r = ck.guardar_midia(cena["pool"], cena["conta"], cena["vend"], cena["lead"], cena["msg"])
    assert r["ok"] is False
    assert "Peça de novo ao cliente" in r["erro"]
    assert _guardado(cena)[0] is None


def test_falha_no_upload_nao_marca_a_mensagem(cena, cdn, monkeypatch):
    """Mensagem marcada sem arquivo no bucket é o pior desfecho: a tela diria
    'guardado' e o arquivo não estaria em lugar nenhum no dia da auditoria."""
    from finance import comprovantes
    monkeypatch.setattr(comprovantes, "subir_em",
                        lambda *a, **k: (_ for _ in ()).throw(ValueError("bucket sumiu")))
    r = ck.guardar_midia(cena["pool"], cena["conta"], cena["vend"], cena["lead"], cena["msg"])
    assert r["ok"] is False
    assert _guardado(cena)[0] is None


def test_sem_bucket_configurado_o_botao_nem_aparece(pool, monkeypatch):
    monkeypatch.setattr(mc, "configurado", lambda: False)
    assert ck.pode_guardar(pool, 1) is False


# ------------------------------------------------------------------ o teto

def test_o_teto_para_o_download_antes_de_baixar_tudo():
    """Baixar 300 MB inteiros pra só então recusar gastaria banda e memória à toa —
    e o ponto do desenho em fluxo era não segurar arquivo grande."""
    lidos = []

    def fonte():
        for _ in range(100):
            lidos.append(1)
            yield b"x" * 1024 * 1024      # 1 MB por pedaço

    with pytest.raises(ValueError, match="MB"):
        mc.juntar(fonte(), 8 * 1024 * 1024)
    assert len(lidos) <= 9, f"leu {len(lidos)} MB antes de parar num teto de 8"


def test_guardar_tem_teto_MENOR_que_enviar():
    """Enviar é passageiro — o arquivo atravessa a memória e vai embora. Guardar é
    para sempre e ocupa disco que alguém paga todo mês."""
    assert mc.TETO["video"] < ck._ANEXO_TETO["video"]


def test_tipo_que_nao_sei_guardar_recusa():
    with pytest.raises(ValueError):
        mc.guardar(iter([b"x"]), conta_id=1, mensagem_id=1, tipo="holograma",
                   mimetype="application/octet-stream")


# ------------------------------------------------- depois de guardado, o CDN é plano B

def test_a_rota_serve_do_bucket_quando_ha_arquivo():
    """É a ordem que dá sentido ao botão: guardar significa 'não depende mais do
    WhatsApp'. Buscando no CDN primeiro, o arquivo guardado só estrearia no dia em
    que o CDN já tivesse expirado — ou seja, quando ninguém pode testá-lo."""
    fonte = inspect.getsource(pc.cockpit_midia)
    i, k = fonte.index("if arquivo:"), fonte.index("_wm.buscar")
    assert i < k, "o bucket vem antes do CDN"
    assert "m.midia_arquivo" in fonte


def test_bucket_ilegivel_cai_pro_cdn_em_vez_de_falhar():
    """Enquanto o WhatsApp ainda tiver o arquivo, um problema no bucket não pode
    tirar a foto da tela."""
    fonte = inspect.getsource(pc.cockpit_midia)
    i = fonte.index("if arquivo:")
    trecho = fonte[i:i + 1100]
    assert "except Exception" in trecho
    assert "tentando o CDN" in trecho


# ------------------------------------------------------------------ a tela

def test_o_botao_existe_nas_duas_copias():
    fonte = inspect.getsource(pc)
    assert "class=guardar data-msg=" in inspect.getsource(pc._guardar_html), "servidor"
    assert "class=guardar data-msg=" in fonte.split("function mid(m)")[1][:900], "polling"


def test_guardado_e_selo_e_nao_botao():
    """Desguardar não existe de propósito: o ponto de guardar é o arquivo não
    depender mais de ninguém, e um desfazer ao lado do contrato assinado é um
    acidente esperando data."""
    h = pc._guardar_html(9, {"tipo": "documento", "guardada": True})
    assert "<button" not in h and "guardado" in h


def test_o_clique_e_delegado():
    """As bolhas do polling nascem depois — handler amarrado na criação não pegaria
    as mensagens que chegam com a tela aberta."""
    fonte = inspect.getsource(pc)
    assert "closest('.guardar')" in fonte


def test_a_rota_de_guardar_nao_trava_o_event_loop():
    """Guardar BAIXA do CDN e SOBE pro bucket: dois saltos de rede em série, e são
    dois workers só."""
    fonte = inspect.getsource(pc.cockpit_guardar)
    assert "run_in_threadpool" in fonte
    assert "get_pool()" not in fonte, "o pool é pego na função _sync, fora do async"
