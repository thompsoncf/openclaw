"""O cartão de clínica (entrega 3a): finance/clinica_cartao.py e a ficha do cartão.

Desenho "Cartão de clínica", aprovado em 03/10/2026. O banco imita o da Espaço Pelle
nesse dia: a sede em Pedreiras e cidades de viagem em Clínica › Locais, cartões sem
cidade nem origem.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from datetime import date, datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import psycopg
import pytest
from psycopg_pool import ConnectionPool
from starlette.datastructures import QueryParams

from finance import clinica_cartao as cc
from finance import raio_x_dono as rxd
from web import painel_prospeccao as pp

CONTA = 39
MIGRACAO = Path(__file__).resolve().parents[1] / "db" / "migracoes" / "202610031502_clinica_cartao.sql"

_SQL = """
create table membros (id bigserial primary key, conta_id bigint, nome text, email text,
  papel text, ativo boolean default true);
create table prospeccao (id bigserial primary key, evento_em date, evento_tipo text, evento_convidados int, evento_origem text, evento_trecho text, evento_pista text, evento_lido_em timestamptz, conta_id bigint, vendedor_id bigint,
  empresa text not null, cnpj text, segmento text, cidade text, uf text,
  contato text, cargo text, telefone text, whatsapp text, email text,
  status text default 'novo', temperatura text default 'frio',
  valor_estimado_centavos bigint default 0, origem text, origem_codigo text, obs text, instagram text,
  socio text, regime_tributario text, porte text,
  ultimo_contato_em timestamptz, proximo_contato_em date,
  orcamento_id bigint, tem_site boolean, maps_url text, receita jsonb, site_url text,
  decisor_nome text, decisor_cargo text, decisor_telefone text, decisor_whatsapp boolean,
  decisor_em timestamptz, decisor_telefones jsonb,
  tipo text default 'pj', cpf text,
  cep text, endereco text, numero text, bairro text, nascimento date,
  estagio text default 'lead', origem_cliente text, perda_motivo text, perda_descricao text,
  atualizado_em timestamptz default now(), criado_em timestamptz default now());
create unique index uq_prospeccao_conta_cpf on prospeccao (conta_id, cpf) where cpf is not null;
create table prospeccao_atividades (id bigserial primary key, prospeccao_id bigint,
  membro_id bigint, tipo text, resultado text, descricao text,
  agendado_para timestamptz, criado_em timestamptz default now());
create table conversas (id bigserial primary key, conta_id bigint,
  prospeccao_id bigint references prospeccao(id), canal text,
  criado_em timestamptz default now(), visto_ate_id bigint);
create table mensagens (id bigserial primary key, conversa_id bigint, texto text,
  direcao text, criado_em timestamptz default now(),
  midia_ref jsonb, midia_tipo text, midia_meta jsonb, midia_arquivo text, midia_guardada_em timestamptz, midia_guardada_por bigint);
create table funil_etapas (id bigserial primary key, semeado_de text, conta_id bigint, chave text,
  rotulo text, ordem int default 0, fixa boolean default false,
  sai_do_quadro boolean not null default false,
  agenda_ao_entrar boolean not null default false,
  criado_em timestamptz default now(), constraint uq_funil_etapa unique (conta_id, chave));
create table contas (id bigserial primary key, chip_de bigint);
create table clinica_locais (id bigserial primary key, conta_id bigint, nome text, endereco text,
  cidade text, tipo text default 'sede', ativo boolean default true, ordem int default 0);
create table campanhas (id bigserial primary key, conta_id bigint, nome text);
create table campanha_alvos (id bigserial primary key, campanha_id bigint, prospeccao_id bigint,
  criado_em timestamptz default now());
"""


@pytest.fixture()
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_clinica_cartao_teste"
    with admin.connection() as c:
        c.autocommit = True
        c.execute("select pg_terminate_backend(pid) from pg_stat_activity where datname=%s",
                  (dbname,))
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_SQL)
        # a migração de verdade, por cima da tabela com a origem da 209
        c.execute("""alter table prospeccao add constraint prospeccao_origem_cliente_check
                     check (origem_cliente is null or origem_cliente in
                            ('whatsapp','indicacao','instagram','manual','outro'))""")
        c.execute(MIGRACAO.read_text(encoding="utf-8"))
        for nome, cidade, tipo, ordem in (("Sede", "Pedreiras", "sede", 0), ("Codó", "Codó", "viagem", 1),
                                          ("Codó, sala 2", "Codó", "viagem", 2),
                                          ("Bacabal", "Bacabal", "viagem", 3)):
            c.execute("insert into clinica_locais (conta_id, nome, cidade, tipo, ordem) values (%s,%s,%s,%s,%s)",
                      (CONTA, nome, cidade, tipo, ordem))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def clinica(monkeypatch, pool):
    monkeypatch.setattr(pp, "get_pool", lambda: pool)
    monkeypatch.setattr(pp, "_acesso", lambda req: (
        {"conta_id": CONTA, "membro_id": 1, "gerencia": True, "pode_atribuir": True}, None))
    monkeypatch.setattr(pp._fr, "perfil_da_conta", lambda c, conta: "clinica")
    return pool


def _lead(pool, **kw):
    campos = dict(empresa="Marina Costa", whatsapp="86999990001", status="contatado", tipo="pf")
    campos.update(kw)
    with pool.connection() as c:
        lid = c.execute(
            f"""insert into prospeccao (conta_id, {', '.join(campos)})
                values (%s, {', '.join(['%s'] * len(campos))}) returning id""",
            (CONTA, *campos.values())).fetchone()[0]
        c.commit()
    return lid


def _linha(pool, lid, *cols):
    with pool.connection() as c:
        return c.execute(f"select {', '.join(cols)} from prospeccao where id=%s", (lid,)).fetchone()


def _req():
    return SimpleNamespace(session={}, query_params=QueryParams(""))


def _ficha(lid) -> str:
    r = pp.prospeccao_ficha(_req(), lid)
    assert r.status_code == 200
    return bytes(r.body).decode("utf-8")


def _salvar(lid, **campos):
    kw = {k: "" for k in ("empresa", "documento", "nascimento", "quem", "responsavel_nome",
                          "responsavel_parentesco", "whatsapp", "telefone", "email", "instagram",
                          "cidade", "cidade_outra", "tipo_atendimento", "origem_cliente", "obs",
                          "valor", "perda_motivo", "perda_descricao")}
    kw.update(campos)
    req = _req()
    r = pp.prospeccao_editar_clinica(req, lid, **kw)
    assert r.status_code == 303
    return req.session.get("prosp_aviso")


def test_a_migracao_aceita_as_origens_da_clinica_e_so_elas(pool):
    for chave in ("radio", "telefone", "balcao", "google", "ja_paciente", "whatsapp", "manual"):
        _lead(pool, origem_cliente=chave)
    _lead(pool, tipo_atendimento="servico")
    with pytest.raises(psycopg.errors.CheckViolation):
        _lead(pool, origem_cliente="tiktok")
    with pytest.raises(psycopg.errors.CheckViolation):
        _lead(pool, tipo_atendimento="exame")


def test_a_lista_de_origens_e_a_do_nicho():
    assert [k for k, _ in rxd.origens("clinica")] == [
        "instagram", "google", "radio", "indicacao", "ja_paciente", "whatsapp", "telefone",
        "balcao", "outro"]
    assert rxd.origens("eventos") == rxd.ORIGENS
    assert rxd.rotulo_origem("radio") == "Rádio" and rxd.rotulo_origem("manual") == "Manual"


def test_as_cidades_vem_dos_locais_sem_repetir(pool):
    with pool.connection() as c:
        assert cc.cidades(c, CONTA) == ["Pedreiras", "Codó", "Bacabal"]


def test_a_ficha_da_clinica_mostra_o_paciente_e_o_que_falta(clinica):
    lid = _lead(clinica)
    html = _ficha(lid)
    assert 'action="/painel/prospeccao/%d/editar-clinica"' % lid in html
    assert "Para sair de Em conversa" in html and "perguntar a cidade" in html
    assert ">Pedreiras</option>" in html and ">Bacabal</option>" in html
    assert "Rádio" in html and "Balcão" in html and "Serviço (vacina, teste, coleta)" in html
    # o que é de festa e de empresa não aparece na ficha da clínica
    for campo in ('name="evento_tipo"', 'name="evento_convidados"', 'name="socio"',
                  'name="porte"', 'name="segmento"', 'name="site_url"',
                  'action="/painel/prospeccao/%d/editar"' % lid):
        assert campo not in html


def test_a_ficha_das_outras_contas_continua_igual(monkeypatch, clinica):
    monkeypatch.setattr(pp._fr, "perfil_da_conta", lambda c, conta: "eventos")
    lid = _lead(clinica)
    html = _ficha(lid)
    assert 'action="/painel/prospeccao/%d/editar"' % lid in html
    assert 'name="evento_tipo"' in html and "editar-clinica" not in html
    assert "Para sair de Em conversa" not in html


def test_salvar_so_mexe_no_que_esta_na_tela(clinica):
    lid = _lead(clinica, evento_tipo="Casamento", evento_convidados=150, segmento="Estética",
                socio="Fulano", site_url="https://exemplo.com", valor_estimado_centavos=30000,
                perda_motivo="outro")
    # a tela manda o valor que mostra (300,00) e o motivo em branco
    aviso = _salvar(lid, empresa="Marina Costa Lima", whatsapp="86999990001", cidade="Codó",
                    tipo_atendimento="procedimento", origem_cliente="radio", quem="eu",
                    nascimento="1990-05-02", documento="529.982.247-25", obs="prefere à tarde",
                    valor="300,00")
    assert aviso == "Dados atualizados."
    assert _linha(clinica, lid, "empresa", "cidade", "tipo_atendimento", "origem_cliente",
                  "nascimento", "cpf", "tipo", "obs") == (
        "Marina Costa Lima", "Codó", "procedimento", "radio", date(1990, 5, 2), "52998224725",
        "pf", "prefere à tarde")
    # nada do que não está na tela mudou
    assert _linha(clinica, lid, "evento_tipo", "evento_convidados", "segmento", "socio",
                  "site_url", "valor_estimado_centavos", "perda_motivo") == (
        "Casamento", 150, "Estética", "Fulano", "https://exemplo.com", 30000, "outro")


def test_outra_pessoa_pede_o_responsavel(clinica):
    lid = _lead(clinica, empresa="Ana Souza")
    aviso = _salvar(lid, empresa="Pedro Souza", quem="outro", responsavel_nome="")
    assert "responsável" in aviso
    assert _linha(clinica, lid, "empresa", "responsavel_nome") == ("Ana Souza", None)
    _salvar(lid, empresa="Pedro Souza", quem="outro", responsavel_nome="Ana Souza",
            responsavel_parentesco="mae", nascimento="2016-03-10")
    assert _linha(clinica, lid, "empresa", "responsavel_nome", "responsavel_parentesco") == (
        "Pedro Souza", "Ana Souza", "mae")
    html = _ficha(lid)
    assert "Ana Souza (mãe) · fala pelo WhatsApp" in html
    # voltar para "quem está falando" limpa o responsável
    _salvar(lid, empresa="Pedro Souza", quem="eu", responsavel_nome="Ana Souza",
            responsavel_parentesco="mae")
    assert _linha(clinica, lid, "responsavel_nome", "responsavel_parentesco") == (None, None)


def test_cidade_fora_da_lista(clinica):
    lid = _lead(clinica, cidade="Teresina")
    with clinica.connection() as c:
        alvo = pp._carrega_alvo(clinica, CONTA, lid)
        ctx = cc.contexto(c, CONTA, alvo)
    assert ctx["cidade_sel"] == cc.OUTRA_CIDADE and ctx["cidade_outra"] == "Teresina"
    _salvar(lid, cidade=cc.OUTRA_CIDADE, cidade_outra="Caxias")
    assert _linha(clinica, lid, "cidade") == ("Caxias",)
    _salvar(lid, cidade="")
    assert _linha(clinica, lid, "cidade") == (None,)


def test_documento_errado_nao_grava(clinica):
    lid = _lead(clinica)
    assert "CPF" in _salvar(lid, documento="11.222.333/0001-81", cidade="Codó")
    assert "não existe" in _salvar(lid, documento="111.111.111-12", cidade="Codó")
    assert "futuro" in _salvar(lid, nascimento="2999-01-01", cidade="Codó")
    assert _linha(clinica, lid, "cidade", "cpf") == (None, None)


def test_cpf_de_outro_cartao_avisa_em_vez_de_quebrar(clinica):
    _lead(clinica, empresa="Ana Souza", cpf="52998224725", whatsapp="86999990009")
    lid = _lead(clinica)
    aviso = _salvar(lid, documento="529.982.247-25")
    assert "Ana Souza" in aviso and "CPF 529.982.247-25" in aviso
    assert _linha(clinica, lid, "cpf") == (None,)


def test_a_rota_da_clinica_nao_grava_em_conta_de_outro_nicho(monkeypatch, clinica):
    monkeypatch.setattr(pp._fr, "perfil_da_conta", lambda c, conta: "eventos")
    lid = _lead(clinica, evento_tipo="Casamento")
    _salvar(lid, cidade="Codó", tipo_atendimento="consulta")
    assert _linha(clinica, lid, "cidade", "tipo_atendimento", "evento_tipo") == (None, None, "Casamento")


def test_a_rota_nao_grava_cartao_de_outra_conta_nem_de_outro_vendedor(monkeypatch, clinica):
    with clinica.connection() as c:
        de_fora = c.execute("""insert into prospeccao (conta_id, empresa, status) values (40, 'Outra conta', 'novo')
                               returning id""").fetchone()[0]
        c.commit()
    _salvar(de_fora, cidade="Codó")
    assert _linha(clinica, de_fora, "cidade") == (None,)
    lid = _lead(clinica, vendedor_id=3)
    monkeypatch.setattr(pp, "_acesso", lambda req: (
        {"conta_id": CONTA, "membro_id": 7, "gerencia": False, "pode_atribuir": False}, None))
    _salvar(lid, cidade="Codó")
    assert _linha(clinica, lid, "cidade") == (None,)


def test_origem_fora_da_lista_da_clinica_fica(clinica):
    lid = _lead(clinica, origem_cliente="manual", tipo_atendimento="consulta")
    html = _ficha(lid)
    assert 'value="manual" checked' in html and "Manual" in html
    # a tela manda a opção marcada; e em branco (ninguém escolheu) não apaga
    _salvar(lid, cidade="Codó", origem_cliente="manual")
    assert _linha(clinica, lid, "origem_cliente", "cidade") == ("manual", "Codó")
    _salvar(lid, cidade="Codó", origem_cliente="", tipo_atendimento="")
    assert _linha(clinica, lid, "origem_cliente", "tipo_atendimento") == ("manual", "consulta")
    assert rxd.rotulo_origem("whatsapp") == "WhatsApp"      # as outras contas não mudam


def test_cartao_com_cnpj_continua_pj_e_mostra_o_cpf(clinica):
    lid = _lead(clinica, tipo="pj", cnpj="11222333000181")
    _salvar(lid, documento="529.982.247-25", cidade="Codó")
    assert _linha(clinica, lid, "tipo", "cnpj", "cpf") == ("pj", "11222333000181", "52998224725")
    html = _ficha(lid)
    assert 'value="529.982.247-25"' in html
    # salvar de novo com o CPF que a tela mostra não perde o CPF
    _salvar(lid, documento="529.982.247-25", cidade="Bacabal")
    assert _linha(clinica, lid, "cpf", "cidade") == ("52998224725", "Bacabal")


def test_valor_e_motivo_da_perda_tambem_na_ficha_da_clinica(clinica):
    lid = _lead(clinica, status="perdido", perda_motivo="outro")
    _salvar(lid, valor="1.500,00", perda_motivo="achou_caro")
    assert _linha(clinica, lid, "valor_estimado_centavos", "perda_motivo") == (150000, "achou_caro")
    _salvar(lid, valor="1.500,00", perda_motivo="")
    assert _linha(clinica, lid, "perda_motivo") == ("achou_caro",)


def test_o_js_da_ficha_da_clinica_compila(clinica, tmp_path):
    """O JS do "outra pessoa" só existe na ficha da clínica, e o teste geral de
    sintaxe (test_painel_js_sintaxe) renderiza a ficha sem conta de clínica."""
    if not shutil.which("node"):
        pytest.skip("sem node no ambiente")
    html = _ficha(_lead(clinica))
    bloco = next(b for b in re.findall(r"<script[^>]*>(.*?)</script>", html, re.S) if "function clQuem" in b)
    for nome in ("clPill", "clMoverNome", "clDevolver", "clCidade"):
        assert f"function {nome}" in bloco
    alvo = tmp_path / "clinica.js"
    alvo.write_text(bloco, encoding="utf-8")
    r = subprocess.run(["node", "--check", str(alvo)], capture_output=True, text=True,
                       stdin=subprocess.DEVNULL, timeout=60)
    assert r.returncode == 0, r.stderr


def test_nome_com_aspas_e_tags_sai_escapado(clinica):
    lid = _lead(clinica, empresa='Maria "Nena" <b>Souza</b>', responsavel_nome='Ana <i>mãe</i>',
                obs='prefere "manhã"')
    html = _ficha(lid)
    assert 'value="Maria &#34;Nena&#34; &lt;b&gt;Souza&lt;/b&gt;"' in html
    assert "Ana &lt;i&gt;mãe&lt;/i&gt; · fala pelo WhatsApp" in html
    assert 'value="prefere &#34;manhã&#34;"' in html


def test_o_que_falta_e_a_proxima_acao():
    agora = datetime(2026, 10, 3, 12, 11, tzinfo=timezone.utc)
    assert cc.falta("agendado", "", None, None, None) is None
    f = cc.falta("contatado", "", None, None, None)
    assert f["proxima"] == "perguntar a cidade" and not f["completo"]
    assert cc.falta("contatado", "Codó", None, "radio", None)["proxima"] == "perguntar o que a pessoa procura"
    assert cc.falta("novo", "Codó", "consulta", None, None)["proxima"] == "passar o preço"
    f = cc.falta("contatado", "Codó", "consulta", "radio", agora)
    assert f["proxima"] == "oferecer uma data em Codó" and f["completo"]


def test_o_preco_passado_vem_da_conversa(clinica):
    lid = _lead(clinica, cidade="Codó", tipo_atendimento="consulta", origem_cliente="instagram")
    with clinica.connection() as c:
        cv = c.execute("insert into conversas (conta_id, prospeccao_id, canal) values (%s,%s,'whatsapp') returning id",
                       (CONTA, lid)).fetchone()[0]
        c.execute("insert into mensagens (conversa_id, direcao, texto, criado_em) values (%s,'in','quanto custa?',%s)",
                  (cv, datetime(2026, 10, 3, 12, 5, tzinfo=timezone.utc)))
        c.execute("insert into mensagens (conversa_id, direcao, texto, criado_em) values (%s,'out','A consulta é R$ 300.',%s)",
                  (cv, datetime(2026, 10, 3, 12, 11, tzinfo=timezone.utc)))
        c.commit()
    html = _ficha(lid)
    assert "por escrito 03/10 09:11" in html          # hora de Brasília
    assert "oferecer uma data em Codó" in html
