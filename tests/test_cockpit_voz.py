"""O vendedor gravando áudio DENTRO do Zaq — e o portão dos três canais.

O que estes testes prendem:

* **o portão é por CANAL, não por nicho.** Só o QR manda mídia por este caminho.
  Twilio (URL pública) e Cloud API (media-id) são outros dois caminhos, e nenhum
  está construído — mostrar o microfone numa conta dessas faria o vendedor gravar
  e o envio falhar depois, que é pior que não ter o botão;

* **a mensagem nasce com `membro_id`.** É o ganho que sobrevive a tudo: hoje 98%
  do que a Prime manda ao cliente chega sem nome, porque sai do celular. Se a
  autoria se perder de novo, todo o resto perde o sentido;

* **o iPhone passa sem conversão** — e o servidor não tenta decodificar nada,
  porque a duração e a onda chegam prontas da tela;

* **a transcrição é EXTRA.** STT fora do ar não pode impedir o vendedor de mandar
  áudio no meio de uma visita.

Banco dedicado e descartável.
"""
import os
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import audio_voz as av
from finance import cockpit as ck

DADOS = Path(__file__).parent / "dados"
WEBM = (DADOS / "voz_chromium.webm").read_bytes()
MP4 = (DADOS / "voz_chromium.mp4").read_bytes()

CONTA_QR, CONTA_TW, CONTA_CLOUD = 11, 22, 33
LEAD = 101


@pytest.fixture()
def pool(monkeypatch):
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_cockpit_voz"
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
        c.execute("create table nichos (id bigserial primary key, nome text, slug text unique)")
        c.execute("""create table contas (id bigserial primary key, nome text,
                     documento text, razao_social text, nome_fantasia text, endereco text,
                     bairro text, cep text, cidade text, uf text, email_empresa text,
                     telefone text, cnae text, nicho_id bigint,
                     vende_produto boolean, vende_servico boolean, chip_de bigint)""")
        c.execute("""create table canais_config (conta_id bigint, canal text, provedor text,
                     identificador text, wa_phone_id text, token text, ativo boolean default true)""")
        # as colunas de contato existem porque `criar_orcamento` monta a proposta
        # a partir da ficha do lead — os testes da PORTA passam por ele
        c.execute("""create table prospeccao (id bigserial primary key, conta_id bigint,
                     membro_id bigint, empresa text, contato text, decisor_nome text,
                     socio text, cnpj text, segmento text, whatsapp text, telefone text,
                     email text, cidade text, uf text,
                     orcamento_id bigint, atualizado_em timestamptz default now())""")
        c.execute("""create table conversas (id bigserial primary key, conta_id bigint,
                     prospeccao_id bigint, canal text, status text, agente_ativo boolean,
                     responsavel_membro_id bigint, push_avisado_em timestamptz,
                     ultima_msg_em timestamptz, criado_em timestamptz default now(), chip_id bigint, visto_ate_id bigint)""")
        c.execute("""create table mensagens (id bigserial primary key, conversa_id bigint,
                     canal text, direcao text, autor text, membro_id bigint, texto text,
                     meta jsonb, provider_sid text, status text,
                     criado_em timestamptz default now(),
                     midia_ref jsonb, midia_tipo text, midia_meta jsonb)""")
        # `ck.orcamento` (a tela de DETALHE) junta membros pra mostrar o vendedor e
        # pra saber pra quem o cliente responde no e-mail — os testes antigos só
        # passavam por `criar_orcamento`, que não junta
        c.execute("""create table membros (id bigserial primary key, conta_id bigint,
                     nome text, email text, papel text, ativo boolean default true)""")
        c.execute("insert into contas (id, nome) values (%s,'Prime QR'),(%s,'Twilio'),(%s,'Cloud')",
                  (CONTA_QR, CONTA_TW, CONTA_CLOUD))
        c.execute("""insert into membros (id, conta_id, nome, email)
                     values (7,%s,'Vendedor','vendedor@empresa.com')""", (CONTA_QR,))
        c.execute("""insert into membros (conta_id, nome, email)
                     values (%s,'Vendedor TW','tw@empresa.com')""", (CONTA_TW,))
        c.execute("""insert into canais_config (conta_id, canal, provedor, identificador, wa_phone_id, token)
                     values (%s,'whatsapp','qr','+5586999990000',null,null),
                            (%s,'whatsapp','twilio','+5586999991111',null,null),
                            (%s,'whatsapp','cloud',null,'123','tok')""",
                  (CONTA_QR, CONTA_TW, CONTA_CLOUD))
        c.execute("""insert into prospeccao (id, conta_id, membro_id, empresa, whatsapp)
                     values (%s,%s,7,'Buffet Estrela','5586999998888')""", (LEAD, CONTA_QR))
        c.commit()
    with p.connection() as c:
        from web.painel_servicos import _garantir_tabela
        _garantir_tabela(c)
        c.commit()
    monkeypatch.setattr(ck, "_posse", lambda c, cid, mid, lid: True)
    monkeypatch.setenv("WA_QR_SERVICE_URL", "https://qr.test")
    monkeypatch.setenv("WA_QR_SHARED_SECRET", "s3gr3d0")
    yield p
    p.close()


@pytest.fixture()
def envios(monkeypatch):
    """Intercepta o cliente do QR: guarda o que teria ido pro WhatsApp."""
    caixa = []

    def _falso(conta_id, numero, dados, mimetype, segundos, onda=None):
        caixa.append({"conta": conta_id, "numero": numero, "bytes": dados,
                      "mimetype": mimetype, "seg": segundos, "onda": onda})
        return {"ok": True, "sid": "SID%d" % len(caixa)}

    from finance import whatsapp_qr as qr
    monkeypatch.setattr(qr, "enviar_audio", _falso)
    return caixa


@pytest.fixture()
def sem_stt(monkeypatch):
    import core.transcribe as tr
    monkeypatch.setattr(tr, "transcritor_se_configurado", lambda: None)


def _msgs(pool):
    with pool.connection() as c:
        return c.execute("""select direcao, autor, membro_id, texto, provider_sid
                              from mensagens order by id""").fetchall()


# ══════════════════════════════════════════════ o portão dos três canais

def test_so_o_canal_qr_manda_audio(pool):
    """Twilio manda mídia por URL pública; a Cloud API por media-id. São três
    funções distintas e só uma está construída."""
    assert ck.pode_gravar_audio(pool, CONTA_QR) is True
    assert ck.pode_gravar_audio(pool, CONTA_TW) is False
    assert ck.pode_gravar_audio(pool, CONTA_CLOUD) is False


def test_sem_o_servico_de_qr_configurado_nao_tem_microfone(pool, monkeypatch):
    monkeypatch.delenv("WA_QR_SERVICE_URL", raising=False)
    assert ck.pode_gravar_audio(pool, CONTA_QR) is False


def test_a_leitura_do_canal_falhando_esconde_o_microfone(pool, monkeypatch):
    """TOLERANTE pro lado seguro: sem microfone o vendedor digita; com microfone
    que não envia, ele grava, acha que mandou, e o cliente nunca recebe."""
    from finance import whatsapp_out as wo
    monkeypatch.setattr(wo, "provedor_da_conta",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    assert ck.pode_gravar_audio(pool, CONTA_QR) is False


def test_conta_de_twilio_nao_envia_nem_chamando_direto(pool, envios, sem_stt):
    """Esconder o botão é metade — a rota é POST e qualquer um monta a chamada."""
    r = ck.enviar_audio(pool, CONTA_TW, 7, LEAD, WEBM, "audio/webm", 8)
    assert r["ok"] is False
    assert envios == [] and _msgs(pool) == []


# ══════════════════════════════════════════════ o envio

def test_o_webm_do_android_vira_ogg_antes_de_sair(pool, envios, sem_stt):
    r = ck.enviar_audio(pool, CONTA_QR, 7, LEAD, WEBM, "audio/webm;codecs=opus", 8)
    assert r["ok"] and r["convertido"] is True
    assert envios[0]["mimetype"] == "audio/ogg; codecs=opus"
    assert envios[0]["bytes"][:4] == b"OggS"


def test_o_mp4_do_iphone_sai_como_veio(pool, envios, sem_stt):
    """Sem conversão e sem decodificação: a duração e a onda vão prontas da tela,
    e é isso que faz o Baileys nem tentar abrir o arquivo."""
    onda = bytes(range(64))
    r = ck.enviar_audio(pool, CONTA_QR, 7, LEAD, MP4, "audio/mp4", 8, onda)
    assert r["ok"] and r["convertido"] is False
    assert envios[0]["bytes"] == MP4 and envios[0]["mimetype"] == "audio/mp4"
    assert envios[0]["onda"] == onda and envios[0]["seg"] == 8


def test_a_mensagem_nasce_com_o_nome_do_vendedor(pool, envios, sem_stt):
    """O GANHO QUE SOBREVIVE A TUDO. Medido em produção: das 1.479 mensagens que a
    Prime mandou, 30 têm autor. As 519 de áudio, nenhuma — porque saem do celular."""
    ck.enviar_audio(pool, CONTA_QR, 7, LEAD, WEBM, "audio/webm", 8)
    (direcao, autor, membro, texto, sid), = _msgs(pool)
    assert direcao == "out" and autor == "humano"
    assert membro == 7, "áudio sem autor é o problema que este módulo existe pra resolver"
    assert sid == "SID1"


def test_a_marca_do_audio_e_a_mesma_do_celular(pool, envios, sem_stt):
    """`🎤 Áudio (0:08)` é o que o serviço Node escreve pro áudio que CHEGA. As
    duas origens têm que ficar iguais na conversa, senão a tela conta duas
    histórias — e o webhook de transcrição casa por esse mesmo formato."""
    ck.enviar_audio(pool, CONTA_QR, 7, LEAD, WEBM, "audio/webm", 8)
    assert _msgs(pool)[0][3] == "🎤 Áudio (0:08)"
    ck.enviar_audio(pool, CONTA_QR, 7, LEAD, WEBM, "audio/webm", 75)
    assert _msgs(pool)[1][3] == "🎤 Áudio (1:15)"


def test_a_conversa_sai_do_automatico_ao_mandar_audio(pool, envios, sem_stt):
    """Mesma regra do texto: quem respondeu assumiu, e o agente para."""
    ck.enviar_audio(pool, CONTA_QR, 7, LEAD, WEBM, "audio/webm", 8)
    with pool.connection() as c:
        est = c.execute("select status, agente_ativo, push_avisado_em from conversas").fetchone()
    assert est[0] == "pendente" and est[1] is False and est[2] is None


# ══════════════════════════════════════════════ a transcrição é extra

def test_o_texto_transcrito_entra_embaixo_da_marca(pool, envios, monkeypatch):
    class _Tr:
        def transcrever(self, dados, nome="a.ogg", **k):
            assert nome == "audio.ogg", "a extensão diz o formato pro provedor de STT"
            return "  bom dia, fechei o salão pro dia 12  "
    import core.transcribe as tr
    monkeypatch.setattr(tr, "transcritor_se_configurado", lambda: _Tr())
    r = ck.enviar_audio(pool, CONTA_QR, 7, LEAD, WEBM, "audio/webm", 8)
    assert r["texto"] == "bom dia, fechei o salão pro dia 12"
    assert _msgs(pool)[0][3] == "🎤 Áudio (0:08)\nbom dia, fechei o salão pro dia 12"


def test_o_stt_caindo_nao_impede_o_envio(pool, envios, monkeypatch):
    """O vendedor está numa visita. Transcrição é registro; áudio é a conversa."""
    class _Tr:
        def transcrever(self, *a, **k):
            raise RuntimeError("sem crédito")
    import core.transcribe as tr
    monkeypatch.setattr(tr, "transcritor_se_configurado", lambda: _Tr())
    r = ck.enviar_audio(pool, CONTA_QR, 7, LEAD, WEBM, "audio/webm", 8)
    assert r["ok"] and len(envios) == 1
    assert _msgs(pool)[0][3] == "🎤 Áudio (0:08)"


def test_a_transcricao_le_o_audio_CONVERTIDO(pool, envios, monkeypatch):
    """Detalhe que economiza uma cópia: transcreve o mesmo buffer que vai sair,
    não o original. Duas cópias de áudio na memória é o que já apertou o serviço."""
    visto = {}
    class _Tr:
        def transcrever(self, dados, nome="a.ogg", **k):
            visto["bytes"] = dados
            return "oi"
    import core.transcribe as tr
    monkeypatch.setattr(tr, "transcritor_se_configurado", lambda: _Tr())
    ck.enviar_audio(pool, CONTA_QR, 7, LEAD, WEBM, "audio/webm", 8)
    assert visto["bytes"][:4] == b"OggS" and visto["bytes"] is envios[0]["bytes"]


# ══════════════════════════════════════════════ os limites

def test_audio_vazio_nao_vira_mensagem(pool, envios, sem_stt):
    assert ck.enviar_audio(pool, CONTA_QR, 7, LEAD, b"", "audio/webm", 3)["ok"] is False
    assert envios == [] and _msgs(pool) == []


def test_audio_acima_do_teto_de_bytes_e_recusado(pool, envios, sem_stt):
    r = ck.enviar_audio(pool, CONTA_QR, 7, LEAD, b"x" * (av.LIMITE_BYTES + 1), "audio/webm", 8)
    assert r["ok"] is False and envios == []


def test_audio_mais_longo_que_o_teto_e_recusado(pool, envios, sem_stt):
    """90 s cobre 98,5% do que o vendedor da Prime gravou em 5 semanas."""
    r = ck.enviar_audio(pool, CONTA_QR, 7, LEAD, WEBM, "audio/webm", av.LIMITE_SEGUNDOS + 1)
    assert r["ok"] is False and envios == []


def test_lead_sem_numero_nao_gera_mensagem_fantasma(pool, envios, sem_stt):
    with pool.connection() as c:
        c.execute("insert into prospeccao (id, conta_id, membro_id, empresa) values (999,%s,7,'X')",
                  (CONTA_QR,))
        c.commit()
    assert ck.enviar_audio(pool, CONTA_QR, 7, 999, WEBM, "audio/webm", 8)["ok"] is False
    assert _msgs(pool) == []


def test_o_envio_falhando_nao_grava_mensagem(pool, monkeypatch, sem_stt):
    """Mensagem no histórico é promessa de que o cliente recebeu. Gravar antes de
    saber faria a conversa mentir pro vendedor."""
    from finance import whatsapp_qr as qr
    monkeypatch.setattr(qr, "enviar_audio",
                        lambda *a, **k: {"ok": False, "erro": "desconectado"})
    r = ck.enviar_audio(pool, CONTA_QR, 7, LEAD, WEBM, "audio/webm", 8)
    assert r["ok"] is False and "desconectado" in r["erro"].lower()
    assert _msgs(pool) == []


# ══════════════════════════════════════════════ a porta pro celular

def test_onde_o_zaq_entrega_sempre_a_porta_fecha(pool):
    """`entrega_sempre` é o portão da porta pro WhatsApp. No QR o Zaq fala com o
    cliente a qualquer hora; na API oficial (Twilio/Cloud) existe a janela de 24h,
    e fora dela o vendedor simplesmente não consegue responder pelo Zaq — fechar a
    porta lá o deixaria mudo num horário morto."""
    assert ck.entrega_sempre(pool, CONTA_QR) is True
    assert ck.entrega_sempre(pool, CONTA_TW) is False
    assert ck.entrega_sempre(pool, CONTA_CLOUD) is False


def test_falha_de_leitura_deixa_a_porta_ABERTA(pool, monkeypatch):
    """A tolerância aqui aponta pro outro lado da do áudio, de propósito: não saber
    o canal vira "não entrega sempre", que MANTÉM a saída. Trancar o vendedor por
    causa de uma consulta que falhou é pior que uma porta a mais."""
    from finance import whatsapp_out as wo
    monkeypatch.setattr(wo, "provedor_da_conta",
                        lambda *a: (_ for _ in ()).throw(RuntimeError("boom")))
    assert ck.entrega_sempre(pool, CONTA_QR) is False


def test_a_tela_do_vendedor_perde_o_atalho_do_whatsapp(pool):
    from web import painel_cockpit as pc

    class _Req:
        def __init__(self):
            self.session = {}

    d = {"empresa": "Buffet", "cidade": "", "uf": "", "doc_fmt": "", "mensagens": [],
         "ia": False, "status": "novo", "etapas": [], "zap_link": "https://wa.me/5586999990000",
         "tel_link": ""}
    import re as _re

    def _atalhos(h):
        return _re.search(r"<div class=grade>(.*?)</div>", h, _re.S).group(1)

    fechada = _atalhos(pc._lead_vendedor(_Req(), 7, d, saida_wa=False).body.decode())
    aberta = _atalhos(pc._lead_vendedor(_Req(), 7, d, saida_wa=True).body.decode())
    # olha os ATALHOS, não a página: "wa.me" também aparece num comentário do JS
    assert "wa.me" not in fechada, "o app não pode mais convidar o vendedor a sair dele"
    assert "WhatsApp" not in fechada, "nem o atalho apagado deve sobrar"
    assert "wa.me" in aberta, "na conta com janela de 24h a saída continua"


def test_a_proposta_nao_devolve_link_de_whatsapp_onde_o_zaq_entrega(pool, envios, sem_stt):
    """Depois de gerar a proposta havia dois botões: "Mandar no WhatsApp" (que sai
    do Zaq) e "Enviar na conversa" (que fica). Manter os dois é ensinar a sair."""
    r = ck.criar_orcamento(pool, CONTA_QR, 7, LEAD, [{"nome": "X", "setup": 100, "mensal": 0}])
    assert r["ok"] and r["zap"] == ""


def test_a_proposta_MANTEM_o_link_onde_existe_janela_de_24h(pool, envios, sem_stt):
    with pool.connection() as c:
        c.execute("""insert into prospeccao (id, conta_id, membro_id, empresa, whatsapp)
                     values (777,%s,7,'Mercadinho','5586999997777')""", (CONTA_TW,))
        c.commit()
    r = ck.criar_orcamento(pool, CONTA_TW, 7, 777, [{"nome": "X", "setup": 100, "mensal": 0}])
    assert r["ok"] and "wa.me" in r["zap"]


# ══════════════════════════════════════════════ quanto ainda sai por fora

def test_a_saida_por_fora_conta_o_que_nao_tem_autor(pool, envios, sem_stt):
    """Mensagem enviada pelo Zaq nasce com `membro_id`; a que sai do celular chega
    pelo espelho da sessão e vem sem autor. Contar `membro_id is null` mede o USO
    da porta — a lista de aparelhos diz que ela existe, este número diz se alguém
    passa por ela. E sai do BANCO: não encosta no WhatsApp."""
    ck.enviar_audio(pool, CONTA_QR, 7, LEAD, WEBM, "audio/webm", 8)     # pelo Zaq
    with pool.connection() as c:
        conv = c.execute("select id from conversas limit 1").fetchone()[0]
        # três saídas pelo celular (sem autor) e uma do agente, que não conta
        c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, membro_id, texto)
                     values (%s,'whatsapp','out','humano',null,'a'),
                            (%s,'whatsapp','out','humano',null,'b'),
                            (%s,'whatsapp','out','humano',null,'c'),
                            (%s,'whatsapp','out','agente',null,'resposta automática')""",
                  (conv, conv, conv, conv))
        c.commit()
    r = ck.saida_por_fora(pool, CONTA_QR)
    assert r["total"] == 4 and r["por_fora"] == 3, r
    assert r["pct"] == 75


def test_o_agente_nao_conta_como_saida_por_fora(pool):
    """O agente também manda sem `membro_id`, e ele não é vendedor nenhum — contar
    a IA como "saiu por fora" inflaria o número que o dono usa pra decidir."""
    with pool.connection() as c:
        c.execute("""insert into conversas (id, conta_id, prospeccao_id, canal)
                     values (900,%s,%s,'whatsapp')""", (CONTA_QR, LEAD))
        c.execute("""insert into mensagens (conversa_id, canal, direcao, autor, membro_id, texto)
                     values (900,'whatsapp','out','agente',null,'oi')""")
        c.commit()
    assert ck.saida_por_fora(pool, CONTA_QR)["total"] == 0


def test_conta_sem_mensagem_nao_vira_divisao_por_zero(pool):
    assert ck.saida_por_fora(pool, CONTA_TW) == {"dias": 7, "total": 0, "por_fora": 0, "pct": 0}

# ═══════════════════════════════════ a proposta sai por e-mail, não por fora

def _proposta(pool, conta_id, email="cliente@exemplo.com"):
    """Cria uma proposta e devolve o id. O e-mail vem do lead, como em produção."""
    with pool.connection() as c:
        lid = c.execute("""insert into prospeccao (conta_id, membro_id, empresa, whatsapp, email)
                           values (%s,7,'Buffet Estrela','5586999998888',%s) returning id""",
                        (conta_id, email)).fetchone()[0]
        c.commit()
    r = ck.criar_orcamento(pool, conta_id, 7, lid, [{"nome": "Salão", "setup": 650, "mensal": 0}])
    assert r["ok"]
    return r["id"]


def test_o_detalhe_da_proposta_nao_oferece_mais_a_saida_no_qr(pool, envios, sem_stt):
    """O portão de `entrega_sempre` já existia na CRIAÇÃO, mas a tela de detalhe
    montava o wa.me sem consultá-lo — então o botão "Mandar no WhatsApp" continuava
    aparecendo pra quem está no QR, que é justamente quem não precisa sair."""
    o = ck.orcamento(pool, CONTA_QR, _proposta(pool, CONTA_QR), membro_id=7)
    assert o["zap"] == "", "no QR o Zaq entrega sempre — o atalho pra fora não se justifica"


def test_mas_a_saida_continua_onde_o_zaq_pode_nao_entregar(pool, envios, sem_stt):
    """Twilio fora da janela de 24h só manda template aprovado. Fechar a porta aqui
    deixaria o vendedor sem NENHUMA saída num horário morto — pior que o problema."""
    o = ck.orcamento(pool, CONTA_TW, _proposta(pool, CONTA_TW), membro_id=7)
    assert "wa.me" in o["zap"]


@pytest.fixture()
def caixa_email(monkeypatch):
    """Intercepta a ÚLTIMA porta (o SMTP), não o `proposta_email`: assim o teste
    passa pelo caminho real de main — caixa da empresa primeiro, Zaq de reserva."""
    caixa = {}
    from finance import email_inbound as ein
    from finance import email_sender as es
    monkeypatch.setattr(ein, "remetente_conta", lambda *a, **k: None)   # sem caixa própria
    monkeypatch.setattr(es, "remetente_configurado", lambda: "zaq@zaq-ia.com")
    monkeypatch.setattr(es, "enviar_email",
                        lambda *a, **k: caixa.update(args=a, kw=k) or True)
    return caixa


def test_email_da_proposta_assina_com_o_nome_COMERCIAL(pool, envios, sem_stt, caixa_email):
    """Quem recebe é o cliente do salão. `contas.nome` é o nome de quem abriu a conta —
    a proposta da Prime Eventos chegaria assinada "MANOEL SOARES"."""
    with pool.connection() as c:
        c.execute("update contas set nome='MANOEL SOARES', nome_fantasia='PRIME EVENTOS' "
                  "where id=%s", (CONTA_QR,))
        c.commit()
    r = ck.enviar_proposta_email(pool, CONTA_QR, _proposta(pool, CONTA_QR), membro_id=7)
    assert r["ok"] and r["destino"] == "cliente@exemplo.com"
    assert caixa_email["kw"]["from_nome"] == "PRIME EVENTOS"
    assert "MANOEL SOARES" not in caixa_email["args"][1]      # nem no assunto
    assert "MANOEL SOARES" not in caixa_email["args"][2]      # nem no corpo


def test_email_da_proposta_sai_pelo_mesmo_caminho_do_painel(pool, envios, sem_stt, monkeypatch):
    """A caixa da EMPRESA vem primeiro — é lá que a resposta do cliente precisa cair.
    Dois caminhos de envio dariam duas respostas pra "de quem é esse e-mail"."""
    passou = {}
    from finance import email_inbound as ein
    monkeypatch.setattr(ein, "remetente_conta", lambda *a, **k: "contato@prime.com")
    monkeypatch.setattr(ein, "enviar_conta",
                        lambda *a, **k: passou.update(args=a, kw=k) or True)
    r = ck.enviar_proposta_email(pool, CONTA_QR, _proposta(pool, CONTA_QR), membro_id=7)
    assert r["ok"] and r["remetente"] == "contato@prime.com"
    assert passou, "não passou pela caixa da empresa"


def test_email_da_proposta_leva_o_link_publico(pool, envios, sem_stt, caixa_email):
    orc_id = _proposta(pool, CONTA_QR)
    link = ck.orcamento(pool, CONTA_QR, orc_id, membro_id=7)["link"]
    assert link, "sem link público não há o que mandar"
    ck.enviar_proposta_email(pool, CONTA_QR, orc_id, membro_id=7)
    assert link in caixa_email["args"][2] and link in caixa_email["args"][3]  # html e texto


def test_sem_email_no_cadastro_o_envio_explica_em_vez_de_falhar_calado(pool, envios, sem_stt):
    r = ck.enviar_proposta_email(pool, CONTA_QR, _proposta(pool, CONTA_QR, email=""),
                                 membro_id=7)
    assert r["ok"] is False and "e-mail" in r["erro"]


# ------------------------------------------------- por qual NÚMERO o áudio sai
#
# O INCIDENTE (28/08/2026, Prime Eventos)
# O dono gravou um áudio na ficha atendida pelo chip "CP Thiago" e o cliente
# recebeu pelo número do "CP Zarb". O log do wa-qr não deixa dúvida — `enviar-audio:
# tentativa conta_id=34`, que é o chip principal, numa conversa cujo `chip_id` é 36.
#
# A causa era esta linha, e só ela: `_qr.enviar_audio(conta_id, ...)`. O envio de
# TEXTO já resolvia o chip (`whatsapp_out.enviar(..., chip_id=chip_da_conversa(...))`
# → `_qr.enviar_texto(chip_id or conta_id, ...)`); o de áudio ficou pra trás e saía
# sempre pela sessão da empresa. O docstring de `whatsapp_out.enviar` já avisava do
# preço: "o lead escreve pra um número e é respondido por outro, que do lado dele
# parece outra empresa".
#
# E há um segundo estrago, menos visível: o Baileys ecoa a mensagem enviada de volta
# pela sessão que DE FATO enviou. Saindo pelo chip errado, o eco casa com a conversa
# do chip errado e a mesma mensagem fica gravada duas vezes, em duas fichas. Medido:
# 6 mensagens em 7 dias, nas contas 34 e 23.

CHIP = 36


def _com_chip(pool, chip_id):
    """Põe a conversa do LEAD num chip específico — como fica uma empresa de dois
    números depois que a mensagem entra pelo segundo."""
    from web.painel_prospeccao import _conversa_id
    with pool.connection() as c:
        conv = _conversa_id(c, CONTA_QR, LEAD, "whatsapp")
        c.execute("update conversas set chip_id=%s where id=%s", (chip_id, conv))
        c.commit()
    return conv


def test_o_audio_sai_pelo_chip_da_conversa(pool, envios, sem_stt):
    """O caso do incidente: conversa do chip 36 tem que sair pela SESSÃO 36."""
    _com_chip(pool, CHIP)
    ck.enviar_audio(pool, CONTA_QR, 7, LEAD, WEBM, "audio/webm", 8)
    assert envios[0]["conta"] == CHIP, \
        "saiu pelo chip principal — é o cliente recebendo de um número que não é o da conversa"


def test_sem_chip_na_conversa_sai_pela_empresa(pool, envios, sem_stt):
    """`chip_id` nulo é o estado de quase toda conversa (empresa de um número só).
    O conserto não pode ter mudado esse caminho."""
    _com_chip(pool, None)
    ck.enviar_audio(pool, CONTA_QR, 7, LEAD, WEBM, "audio/webm", 8)
    assert envios[0]["conta"] == CONTA_QR


def test_o_audio_escolhe_o_numero_igual_ao_texto(pool, envios, sem_stt):
    """As duas rotas têm que responder a MESMA pergunta do mesmo jeito. Enquanto
    divergirem, o vendedor manda texto por um número e áudio por outro na mesma
    conversa — e nem ele nem o cliente têm como perceber."""
    from finance import whatsapp_out
    conv = _com_chip(pool, CHIP)
    with pool.connection() as c:
        assert whatsapp_out.chip_da_conversa(c, CONTA_QR, conv) == CHIP
    ck.enviar_audio(pool, CONTA_QR, 7, LEAD, WEBM, "audio/webm", 8)
    assert envios[0]["conta"] == CHIP


def test_a_mensagem_continua_sendo_gravada_na_conversa_do_lead(pool, envios, sem_stt):
    """O chip muda por onde SAI, não onde se grava. Se a gravação tivesse migrado
    junto, o áudio sumiria da ficha em que o vendedor o mandou."""
    conv = _com_chip(pool, CHIP)
    ck.enviar_audio(pool, CONTA_QR, 7, LEAD, WEBM, "audio/webm", 8)
    with pool.connection() as c:
        r = c.execute("select conversa_id from mensagens order by id desc limit 1").fetchone()
    assert r[0] == conv


# =================================================================== PASSO 4
# O VENDEDOR MANDANDO foto, vídeo e documento de dentro do Zaq.
#
# É a metade que faltava do trabalho de mídia. Os passos 1-3 fizeram ele RECEBER
# tudo no app; pra MANDAR a foto do salão ou o PDF do orçamento ele ainda pegava o
# celular — e o que sai do celular chega sem nome, não entra no histórico e mantém
# viva a conexão paralela que o trabalho inteiro veio fechar.
#
# O QUE ESTES TESTES PRENDEM, e por quê:
#  1. SAI PELO CHIP DA CONVERSA. O mesmo defeito que o áudio teve até 28/08/2026 —
#     mandar pelo chip principal faz o cliente receber de um número que não é o
#     daquela conversa, e o eco volta pra ficha do colega;
#  2. A MENSAGEM NASCE COM AUTOR E COM O PONTEIRO. Sem `membro_id` a mensagem chega
#     anônima; sem o ponteiro devolvido pelo Baileys a bolha vira um "📷 Foto"
#     cego, e o vendedor abre o celular pra conferir o que mandou — o hábito que
#     este passo veio quebrar;
#  3. O ARQUIVO NÃO É GUARDADO. Mesma escolha da entrada: ~200 bytes de ponteiro,
#     não megabytes de arquivo.

PNG = b"\x89PNG\r\n\x1a\n" + b"x" * 400
PDF = b"%PDF-1.7" + b"y" * 900


@pytest.fixture()
def envios_midia(monkeypatch):
    """Intercepta o cliente do QR: guarda o que teria ido pro WhatsApp."""
    caixa = []

    def _falso(conta_id, numero, dados, tipo, mimetype, nome="", legenda=""):
        caixa.append({"conta": conta_id, "numero": numero, "bytes": dados, "tipo": tipo,
                      "mime": mimetype, "nome": nome, "legenda": legenda})
        return {"ok": True, "sid": "MID%d" % len(caixa),
                "midia": {"tipo": tipo,
                          "ref": {"directPath": "/v/t62.7118-24/abc_n.enc",
                                  "mediaKey": "MDEyMzQ1Njc4OWFiY2RlZjAxMjM0NTY3ODlhYmNkZWY=",
                                  "mimetype": mimetype},
                          "meta": {"bytes": len(dados)}}}

    from finance import whatsapp_qr as qr
    monkeypatch.setattr(qr, "enviar_midia", _falso)
    return caixa


def _ultima(pool):
    with pool.connection() as c:
        return c.execute(
            """select direcao, autor, membro_id, texto, provider_sid,
                      midia_tipo, midia_ref, midia_meta
                 from mensagens order by id desc limit 1""").fetchone()


# ------------------------------------------------------------- o que é cada coisa

def test_o_tipo_sai_do_mimetype():
    assert ck.tipo_do_anexo("image/jpeg") == ("imagem", "📷 Foto")
    assert ck.tipo_do_anexo("video/mp4") == ("video", "🎬 Vídeo")
    assert ck.tipo_do_anexo("application/pdf") == ("documento", "📄 Documento")


def test_o_que_nao_e_foto_nem_video_vira_DOCUMENTO_e_nao_recusa():
    """PDF de orçamento, comprovante de PIX e planilha são o que ele mais manda.
    Uma lista de mimetypes permitidos envelheceria contra o vendedor."""
    for m in ("application/vnd.ms-excel", "text/csv", "", "coisa/estranha", None):
        assert ck.tipo_do_anexo(m)[0] == "documento"


def test_a_marca_e_a_MESMA_da_entrada():
    """Mandada e recebida têm que ficar iguais na thread — senão a tela conta duas
    histórias pro mesmo tipo de arquivo."""
    from web.painel_prospeccao import _MIDIA_TIPOS
    for mime in ("image/png", "video/mp4", "application/pdf"):
        tipo, marca = ck.tipo_do_anexo(mime)
        assert tipo in _MIDIA_TIPOS
        assert marca.split()[0] in ("📷", "🎬", "📄")


# ----------------------------------------------------------- por qual número sai

def test_o_anexo_sai_pelo_chip_da_conversa(pool, envios_midia):
    """A MESMA lição do áudio, que custou 5 mensagens entregues pelo número errado
    e 6 gravadas em duas fichas."""
    _com_chip(pool, CHIP)
    r = ck.enviar_anexo(pool, CONTA_QR, 7, LEAD, PNG, "salao.png", "image/png")
    assert r["ok"] is True
    assert envios_midia[0]["conta"] == CHIP, "saiu pelo chip principal — número errado pro cliente"


def test_sem_chip_na_conversa_sai_pela_empresa(pool, envios_midia):
    _com_chip(pool, None)
    ck.enviar_anexo(pool, CONTA_QR, 7, LEAD, PNG, "salao.png", "image/png")
    assert envios_midia[0]["conta"] == CONTA_QR


# ------------------------------------------------------------- a bolha que nasce

def test_a_mensagem_nasce_com_autor_e_com_o_ponteiro(pool, envios_midia):
    """As duas coisas juntas: o autor dá nome a quem falou, o ponteiro faz a foto
    APARECER em vez de virar um '📷 Foto' cego."""
    ck.enviar_anexo(pool, CONTA_QR, 7, LEAD, PNG, "salao.png", "image/png")
    direcao, autor, membro, texto, sid, tipo, ref, meta = _ultima(pool)
    assert (direcao, autor, membro) == ("out", "humano", 7)
    assert texto == "📷 Foto" and sid == "MID1"
    assert tipo == "imagem"
    assert ref["directPath"] == "/v/t62.7118-24/abc_n.enc" and ref["mediaKey"]
    assert meta["bytes"] == len(PNG)


def test_a_legenda_vira_o_texto_da_bolha(pool, envios_midia):
    """No WhatsApp a legenda chega colada na foto — e é assim que as pessoas
    mandam. Duas mensagens separadas chegariam fora de ordem quando a rede oscila."""
    ck.enviar_anexo(pool, CONTA_QR, 7, LEAD, PNG, "salao.png", "image/png",
                    legenda="olha o salão montado")
    assert envios_midia[0]["legenda"] == "olha o salão montado"
    assert _ultima(pool)[3] == "olha o salão montado"


def test_o_documento_leva_o_nome_do_arquivo(pool, envios_midia):
    """Um PDF sem nome chega como 'arquivo' do lado do cliente — e o orçamento
    da empresa não pode chegar assim."""
    ck.enviar_anexo(pool, CONTA_QR, 7, LEAD, PDF, "Orçamento Nº 13.pdf", "application/pdf")
    assert envios_midia[0]["nome"] == "Orçamento Nº 13.pdf"
    assert envios_midia[0]["tipo"] == "documento"


def test_o_arquivo_nao_entra_no_banco(pool, envios_midia):
    """Mesma escolha da entrada: ponteiro, não arquivo."""
    ck.enviar_anexo(pool, CONTA_QR, 7, LEAD, PDF, "contrato.pdf", "application/pdf")
    with pool.connection() as c:
        n = c.execute("""select length(coalesce(midia_ref::text,''))
                              + length(coalesce(midia_meta::text,''))
                           from mensagens order by id desc limit 1""").fetchone()[0]
    assert n < 600, f"o ponteiro cresceu pra {n} bytes — tem arquivo entrando aqui"


def test_a_conversa_sai_do_automatico_ao_mandar_anexo(pool, envios_midia):
    """Quem mandou um arquivo assumiu a conversa — igual ao texto e ao áudio."""
    ck.enviar_anexo(pool, CONTA_QR, 7, LEAD, PNG, "f.png", "image/png")
    with pool.connection() as c:
        st, ia = c.execute("select status, agente_ativo from conversas where prospeccao_id=%s",
                           (LEAD,)).fetchone()
    assert st == "pendente" and ia is False


# ------------------------------------------------------------------- os tetos

def test_arquivo_grande_demais_e_recusado_com_recado(pool, envios_midia):
    """Barrado ANTES do upload: mandar 40 MB pela rede do celular pra ouvir
    'grande demais' no fim gasta o pacote de dados do vendedor à toa."""
    r = ck.enviar_anexo(pool, CONTA_QR, 7, LEAD, b"z" * (6 * 1024 * 1024), "f.png", "image/png")
    assert r["ok"] is False and "MB" in r["erro"]
    assert envios_midia == [], "nem deve ter tentado enviar"


def test_o_teto_do_video_e_maior_que_o_da_foto(pool, envios_midia):
    """5 MB derrubaria vídeo de poucos segundos; 16 MB é o teto do próprio WhatsApp."""
    assert ck._ANEXO_TETO["video"] > ck._ANEXO_TETO["imagem"]
    r = ck.enviar_anexo(pool, CONTA_QR, 7, LEAD, b"z" * (6 * 1024 * 1024), "v.mp4", "video/mp4")
    assert r["ok"] is True


def test_arquivo_vazio_nao_vira_mensagem(pool, envios_midia):
    assert ck.enviar_anexo(pool, CONTA_QR, 7, LEAD, b"", "f.png", "image/png")["ok"] is False
    assert envios_midia == []


def test_conta_que_nao_e_QR_nao_manda_anexo(pool, envios_midia):
    """Twilio manda mídia por URL pública; a Cloud API por media-id. São outros
    caminhos, e nenhum está construído — o portão é o mesmo do microfone."""
    r = ck.enviar_anexo(pool, CONTA_TW, 7, LEAD, PNG, "f.png", "image/png")
    assert r["ok"] is False
    assert envios_midia == []


def test_o_envio_falhando_nao_grava_mensagem(pool, monkeypatch):
    """Mensagem gravada sem ter saído faz o vendedor achar que mandou — e o cliente
    nunca recebeu."""
    from finance import whatsapp_qr as qr
    monkeypatch.setattr(qr, "enviar_midia",
                        lambda *a, **k: {"ok": False, "erro": "desconectado"})
    antes = _ultima(pool)
    r = ck.enviar_anexo(pool, CONTA_QR, 7, LEAD, PNG, "f.png", "image/png")
    assert r["ok"] is False
    # o recado é pra pessoa, não o código cru do serviço: diz o que fazer
    assert r["erro"] == "WhatsApp desconectado. Reconecte na aba Canais."
    assert _ultima(pool) == antes


def test_sem_ponteiro_de_volta_a_mensagem_ainda_e_gravada(pool, monkeypatch):
    """O ponteiro é o que faz a bolha mostrar a foto. Se ele não vier, o arquivo
    JÁ FOI pro cliente — perder a mensagem por causa disso seria trocar um
    problema de tela por um buraco no histórico."""
    from finance import whatsapp_qr as qr
    monkeypatch.setattr(qr, "enviar_midia",
                        lambda *a, **k: {"ok": True, "sid": "SEMPTR", "midia": None})
    r = ck.enviar_anexo(pool, CONTA_QR, 7, LEAD, PNG, "f.png", "image/png")
    assert r["ok"] is True
    _, _, membro, texto, sid, tipo, ref, _ = _ultima(pool)
    assert sid == "SEMPTR" and membro == 7 and texto == "📷 Foto"
    assert tipo is None and ref is None
