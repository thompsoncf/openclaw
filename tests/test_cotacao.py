"""A COTAÇÃO (migração 304) — o passo antes da apólice existir.

As decisões do dono de 21/09/2026 ("camada agnóstica primeiro", "três portas",
"emissão só até a proposta, com fallback", "começa por auto") são regras de
negócio, não preferência de tela. As que podem voltar em silêncio têm teste aqui:

* `test_a_comissao_da_oferta_sai_do_LIQUIDO` e
  `test_oferta_sem_IOF_separado_nao_estima_comissao` — o erro de conta do mockup
  das apólices (R$ 817,71 onde o certo era R$ 761,51), agora com um caminho novo
  pra voltar: o provedor que manda só o total. A resposta certa é NÃO ESTIMAR.
* `test_o_provedor_que_cai_nao_derruba_a_cotacao` — falha de terceiro é o erro
  mais comum de todos, e as três portas dependem de ela virar dado, não exceção.
* `test_a_escolhida_vira_proposta_na_carteira` — é o elo com a migração 278. Sem
  ele, a corretora redigita a apólice que acabou de fechar, e a carteira diverge
  do que foi vendido.
"""
import os
import re
from datetime import date
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from finance import apolices as ap
from finance import cotacao as ct
from finance import cotacao_provedores as cp

MIG = Path(__file__).resolve().parent.parent / "db" / "migracoes"
CONTA = 37          # a Liberal Seguros, de novo: é a corretora da base
OUTRA = 38

_BASE = """
create table contas (id bigint primary key, nome text);
create table membros (id bigserial primary key, conta_id bigint references contas(id),
  nome text, email text, papel text not null default 'membro', ativo boolean not null default true);
create table pessoas (id bigserial primary key, cpf text, cnpj text, tipo text, celular text,
  nome text, email text);
create table clientes (id bigserial primary key, dono_id bigint references contas(id),
  pessoa_id bigint references pessoas(id), nome text not null, telefone text, email text,
  endereco text, cidade text, uf text, cep text, obs text,
  ativo boolean not null default true, criado_em timestamptz not null default now());
create table conversas (id bigserial primary key, conta_id bigint, contato_ref text,
  contato_nome text, ultima_msg_em timestamptz);
create table mensagens (id bigserial primary key, conversa_id bigint, direcao text, texto text,
  criado_em timestamptz not null default now(),
  midia_ref jsonb, midia_tipo text, midia_meta jsonb, midia_arquivo text);
create table lembretes_enviados (id bigserial primary key,
  conta_id bigint not null references contas(id) on delete cascade,
  tipo text not null check (tipo in ('resumo','aviso')),
  chave text not null, enviado_em timestamptz not null default now(),
  unique (conta_id, tipo, chave));
insert into contas (id, nome) values (37,'Liberal Seguros'), (38,'Outra Corretora');
"""

# um CPF que passa no dígito verificador — o normalizador recusa o que não passa
CPF = "52998224725"


def _risco(**extra) -> dict:
    d = {"nome": "Fulano de Tal", "cpf": CPF, "nascimento": "1985-04-12",
         "cep": "64000-000", "placa": "ABC1D23", "marca_modelo": "FIAT ARGO 1.0",
         "ano_modelo": 2022, "uso": "particular", "garagem": "residencia", "bonus": 5}
    d.update(extra)
    return d


@pytest.fixture(scope="module")
def pool():
    admin = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=1, open=True)
    dbname = "zaq_cotacao_test"
    with admin.connection() as c:
        c.autocommit = True
        c.execute(f"drop database if exists {dbname}")
        c.execute(f"create database {dbname}")
    admin.close()
    url = os.environ["TEST_DATABASE_URL"].rsplit("/", 1)[0] + "/" + dbname
    p = ConnectionPool(url, min_size=1, max_size=3, open=True, kwargs={"prepare_threshold": None})
    with p.connection() as c:
        c.execute(_BASE)
        for m in ("278_apolices.sql", "286_apolices_pdf.sql", "287_apolice_perdida.sql",
                  "304_cotacoes.sql"):
            c.execute((MIG / m).read_text(encoding="utf-8"))
        c.commit()
    yield p
    p.close()


@pytest.fixture
def limpo(pool):
    with pool.connection() as c:
        c.execute("delete from cotacao_ofertas")
        c.execute("delete from cotacao_chaves")
        c.execute("delete from cotacoes")
        c.execute("delete from apolices")
        c.execute("delete from seguros_comissao")
        c.commit()
    return pool


def _cotacao(pool, risco=None, **kw) -> int:
    return ct.criar(pool, CONTA, ct.normalizar_risco(risco or _risco()), **kw)


# ───────────────────────────────────────────── o banco e o python de acordo

def test_o_banco_e_o_python_conhecem_as_mesmas_situacoes():
    """O check da 304 e `SITUACOES` têm que listar o mesmo. Divergiram, a situação
    nova grava no python e a INSERT estoura no banco — em produção, na primeira
    cotação que chegar nela."""
    sql = (MIG / "304_cotacoes.sql").read_text(encoding="utf-8")
    trecho = sql.split("situacao     text not null default 'rascunho'", 1)[1]
    no_banco = set(re.findall(r"'(\w+)'", trecho.split(")", 1)[0]))
    assert no_banco == {c for c, _ in ct.SITUACOES}


def test_o_banco_e_o_python_conhecem_as_mesmas_origens():
    sql = (MIG / "304_cotacoes.sql").read_text(encoding="utf-8")
    trecho = sql.split("check (origem in", 1)[1]
    no_banco = set(re.findall(r"'(\w+)'", trecho.split(")", 1)[0]))
    assert no_banco == {c for c, _ in ct.ORIGENS}


# ─────────────────────────────────────────────────────────── o risco mínimo

def test_cpf_invalido_nao_vira_cotacao():
    with pytest.raises(ct.CotacaoErro, match="CPF"):
        ct.normalizar_risco(_risco(cpf="11111111111"))


def test_sem_cep_nao_vira_cotacao():
    """Sem CEP de pernoite nenhum provedor brasileiro dá preço. Recusar aqui é
    devolver o erro em uma linha em vez de gastar a chamada de rede."""
    with pytest.raises(ct.CotacaoErro, match="CEP"):
        ct.normalizar_risco(_risco(cep=""))


def test_o_veiculo_entra_por_fipe_placa_OU_marca_modelo():
    """Qualquer um dos três serve. Exigir os três seria exigir do corretor o
    trabalho que a API faz."""
    so_fipe = ct.normalizar_risco(_risco(placa="", marca_modelo="", ano_modelo=None,
                                         fipe="004445-0"))
    assert so_fipe["veiculo"]["fipe"] == "004445-0"
    so_placa = ct.normalizar_risco(_risco(marca_modelo="", ano_modelo=None))
    assert so_placa["veiculo"]["placa"] == "ABC1D23"
    with pytest.raises(ct.CotacaoErro, match="veículo"):
        ct.normalizar_risco(_risco(placa="", marca_modelo="", ano_modelo=None))


def test_a_placa_perde_a_pontuacao_e_sobe_pra_maiuscula():
    assert ct.normalizar_placa("abc-1d23") == "ABC1D23"
    assert ct.normalizar_placa(" abc1234 ") == "ABC1234"


def test_o_condutor_e_o_segurado_por_padrao():
    """Perguntar sempre faria o corretor repetir o CPF que acabou de digitar."""
    r = ct.normalizar_risco(_risco())
    assert r["condutor"]["e_o_segurado"] is True
    assert r["condutor"]["nascimento"] == "1985-04-12"


def test_ramo_sem_formulario_e_recusado_em_vez_de_fingir():
    with pytest.raises(ct.CotacaoErro, match="auto"):
        ct.normalizar_risco(_risco(), ramo="residencial")


# ──────────────────────────────────────────────── o dinheiro: líquido × total

def test_a_comissao_da_oferta_sai_do_LIQUIDO():
    """O erro do mockup das apólices, agora na cotação: 20% sobre 3.807,57 é
    761,51 — não 817,71, que é 20% do total com IOF."""
    o = ct.Oferta(seguradora="Allianz", premio_total_centavos=408857,
                  premio_liquido_centavos=380757, iof_centavos=28100,
                  comissao_pct=20)
    assert o.comissao_estimada() == 76151


def test_oferta_sem_IOF_separado_nao_estima_comissao():
    """Provedor que manda só o total deixa a comissão SEM estimativa. Rachar o
    total por um IOF chutado seria inventar comissão — o mesmo erro, automatizado."""
    o = ct.Oferta(seguradora="Porto", premio_total_centavos=408857, comissao_pct=20)
    assert o.premio_liquido_centavos is None
    assert o.comissao_estimada() is None


def test_o_liquido_sozinho_calcula_o_iof_em_vez_de_fingir_que_e_zero():
    o = ct.Oferta(seguradora="HDI", premio_total_centavos=408857,
                  premio_liquido_centavos=380757)
    assert o.iof_centavos == 28100


def test_oferta_sem_premio_nao_existe():
    with pytest.raises(ValueError):
        ct.Oferta(seguradora="Tokio", premio_total_centavos=0)


def test_a_comissao_cai_no_cadastro_da_conta_quando_o_provedor_nao_manda(limpo):
    """O percentual da `seguros_comissao` (migração 278) é o MESMO que a carteira
    usa. Duas fontes dariam duas comissões pro mesmo negócio."""
    ap.salvar_comissao(limpo, CONTA, "Allianz", "", 20)
    cid = _cotacao(limpo)
    ct.gravar_ofertas(limpo, CONTA, cid, [
        ct.Oferta(seguradora="Allianz", premio_total_centavos=408857,
                  premio_liquido_centavos=380757, iof_centavos=28100)])
    o = ct.ofertas(limpo, CONTA, cid)[0]
    assert o["comissao_pct"] == 20
    assert o["comissao_centavos"] == 76151


# ───────────────────────────────────────────────────────────── as ofertas

def test_gravar_ofertas_substitui_as_de_ontem(limpo):
    """Cotar de novo é pedir o preço de HOJE. Misturar com o de ontem faria a tela
    comparar preços de datas diferentes lado a lado sem dizer."""
    cid = _cotacao(limpo)
    ct.gravar_ofertas(limpo, CONTA, cid, [ct.Oferta(seguradora="Porto", premio_total_centavos=300000)])
    ct.gravar_ofertas(limpo, CONTA, cid, [ct.Oferta(seguradora="HDI", premio_total_centavos=280000)])
    achadas = ct.ofertas(limpo, CONTA, cid)
    assert [o["seguradora"] for o in achadas] == ["HDI"]


def test_as_ofertas_saem_da_mais_barata_pra_mais_cara(limpo):
    cid = _cotacao(limpo)
    ct.gravar_ofertas(limpo, CONTA, cid, [
        ct.Oferta(seguradora="Porto", premio_total_centavos=410000),
        ct.Oferta(seguradora="HDI", premio_total_centavos=280000),
        ct.Oferta(seguradora="Tokio", premio_total_centavos=350000)])
    assert [o["seguradora"] for o in ct.ofertas(limpo, CONTA, cid)] == ["HDI", "Tokio", "Porto"]


def test_zero_oferta_nao_e_falha(limpo):
    """"Ninguém aceitou este risco" é uma resposta, e a tela precisa poder mostrá-la
    diferente de "o provedor caiu"."""
    cid = _cotacao(limpo)
    ct.gravar_ofertas(limpo, CONTA, cid, [])
    assert ct.ler(limpo, CONTA, cid)["situacao"] == "cotada"


def test_a_oferta_escolhida_e_uma_so(limpo):
    """Duas escolhidas seria a corretora tendo fechado o mesmo seguro duas vezes."""
    cid = _cotacao(limpo)
    ct.gravar_ofertas(limpo, CONTA, cid, [
        ct.Oferta(seguradora="Porto", premio_total_centavos=410000),
        ct.Oferta(seguradora="HDI", premio_total_centavos=280000)])
    a, b = ct.ofertas(limpo, CONTA, cid)
    ct.escolher(limpo, CONTA, cid, a["id"])
    ct.escolher(limpo, CONTA, cid, b["id"])
    escolhidas = [o for o in ct.ofertas(limpo, CONTA, cid) if o["escolhida"]]
    assert [o["id"] for o in escolhidas] == [b["id"]]
    assert ct.ler(limpo, CONTA, cid)["situacao"] == "escolhida"


def test_a_cotacao_de_outra_conta_nao_e_lida(limpo):
    """Multi-tenant: o id sozinho nunca basta, aqui como no resto da base."""
    cid = _cotacao(limpo)
    assert ct.ler(limpo, OUTRA, cid) is None
    with pytest.raises(ct.CotacaoErro):
        ct.gravar_ofertas(limpo, OUTRA, cid, [])


# ────────────────────────────────────────── da oferta escolhida pra carteira

def test_a_escolhida_vira_proposta_na_carteira(limpo):
    """O elo com a migração 278 — e é ele que impede a redigitação.

    A apólice nasce 'proposta' (o primeiro estado do ciclo), com o LÍQUIDO em
    `premio_centavos`, o IOF na coluna dele e a vigência de 12 meses.
    """
    cid = _cotacao(limpo, _risco(vigencia_inicio="2026-10-01"))
    ct.gravar_ofertas(limpo, CONTA, cid, [
        ct.Oferta(seguradora="Allianz", produto="Auto Compreensiva",
                  premio_total_centavos=408857, premio_liquido_centavos=380757,
                  iof_centavos=28100, franquia_centavos=250000, parcelas=10,
                  comissao_pct=20, coberturas=["casco", "RCF-V"],
                  ref_externa="COT-99")])
    o = ct.ofertas(limpo, CONTA, cid)[0]
    ct.escolher(limpo, CONTA, cid, o["id"])
    apolice_id = ct.virar_proposta(limpo, CONTA, cid)

    with limpo.connection() as c:
        row = c.execute(
            "select seguradora, ramo, situacao, premio_centavos, iof_centavos, "
            "franquia_centavos, vigencia_inicio, vigencia_fim, numero_proposta, "
            "bem->>'placa', parcelas from apolices where id=%s", (apolice_id,)).fetchone()
    (seg, ramo, sit, premio, iof, franq, ini, fim, prop, placa, parc) = row
    assert (seg, ramo, sit) == ("Allianz", "auto", "proposta")
    assert (premio, iof, franq) == (380757, 28100, 250000)     # líquido, não o total
    assert (ini, fim) == (date(2026, 10, 1), date(2027, 10, 1))
    assert prop == "COT-99" and placa == "ABC1D23" and parc == 10
    assert ct.ler(limpo, CONTA, cid)["situacao"] == "proposta"
    assert ct.ler(limpo, CONTA, cid)["apolice_id"] == apolice_id


def test_sem_escolher_nao_ha_proposta(limpo):
    cid = _cotacao(limpo)
    ct.gravar_ofertas(limpo, CONTA, cid, [ct.Oferta(seguradora="Porto", premio_total_centavos=300000)])
    with pytest.raises(ct.CotacaoErro, match="escolha"):
        ct.virar_proposta(limpo, CONTA, cid)


def test_oferta_sem_iof_leva_o_total_pro_premio_e_o_iof_zero(limpo):
    """O provedor omitiu a separação: a apólice nasce com o total e IOF zero, e a
    corretora corrige quando o papel chegar. O contrário — rachar por um IOF
    chutado — inventaria comissão na carteira, que é onde ela vira dinheiro."""
    cid = _cotacao(limpo)
    ct.gravar_ofertas(limpo, CONTA, cid, [
        ct.Oferta(seguradora="Porto", premio_total_centavos=408857)])
    o = ct.ofertas(limpo, CONTA, cid)[0]
    ct.escolher(limpo, CONTA, cid, o["id"])
    apolice_id = ct.virar_proposta(limpo, CONTA, cid)
    with limpo.connection() as c:
        premio, iof = c.execute("select premio_centavos, iof_centavos from apolices where id=%s",
                                (apolice_id,)).fetchone()
    assert (premio, iof) == (408857, 0)


# ──────────────────────────────────────────────────────────── os provedores

class _ProvedorFalso(cp.Provedor):
    chave = "falso"
    nome = "Falso"
    suporta_emissao = True

    def __init__(self, ofertas=None, quebra=False, emissao=None):
        self._ofertas, self._quebra, self._emissao = ofertas or [], quebra, emissao or {}

    def cotar(self, risco):
        if self._quebra:
            raise cp.ProvedorErro("a seguradora não respondeu a tempo")
        return self._ofertas

    def emitir(self, risco, oferta):
        return self._emissao


def test_o_provedor_que_cai_nao_derruba_a_cotacao(limpo):
    """Falha de terceiro vira DADO, não exceção: as três portas não podem precisar
    inventar cada uma o seu tratamento de erro."""
    cid = _cotacao(limpo)
    cot = ct.cotar(limpo, CONTA, cid, provedor=_ProvedorFalso(quebra=True))
    assert cot["situacao"] == "falhou"
    assert "não respondeu" in cot["erro"]


def test_cotar_guarda_as_ofertas_e_o_provedor(limpo):
    cid = _cotacao(limpo)
    p = _ProvedorFalso(ofertas=[ct.Oferta(seguradora="HDI", premio_total_centavos=280000)])
    cot = ct.cotar(limpo, CONTA, cid, provedor=p)
    assert cot["situacao"] == "cotada" and cot["provedor"] == "falso"
    assert [o["seguradora"] for o in cot["ofertas"]] == ["HDI"]


def test_o_manual_nao_falha_e_nao_inventa_oferta(limpo):
    """O padrão do dia 1: sem API contratada, o corretor digita — e o comparativo,
    a escolha e a proposta funcionam igual."""
    cid = _cotacao(limpo)
    cot = ct.cotar(limpo, CONTA, cid, provedor=cp.ProvedorManual())
    assert cot["situacao"] == "cotada" and cot["ofertas"] == []


def test_provedor_desconhecido_cai_no_manual_em_vez_de_quebrar(monkeypatch):
    monkeypatch.setenv("COTACAO_PROVEDOR", "nao_existe")
    assert cp.provedor_ativo().chave == "manual"


def test_provedor_http_sem_base_url_diz_qual_variavel_falta():
    class _X(cp.ProvedorHTTP):
        chave = "segfy"
        nome = "Segfy"
    with pytest.raises(cp.ProvedorErro, match="COTACAO_SEGFY_BASE_URL"):
        _X()._post("/quote", {})


# ──────────────────────────────────────────────────────────── a emissão

def test_sem_api_de_emissao_o_envio_devolve_o_roteiro_do_portal(limpo):
    """O fallback da decisão 3 do dono. Nenhuma seguradora brasileira abre API de
    emissão pro corretor hoje; o que o sistema tira do caminho é procurar CPF,
    chassi e CEP em três lugares e copiar errado."""
    cid = _cotacao(limpo)
    ct.gravar_ofertas(limpo, CONTA, cid, [
        ct.Oferta(seguradora="Allianz", premio_total_centavos=408857, parcelas=10)])
    o = ct.ofertas(limpo, CONTA, cid)[0]
    ct.escolher(limpo, CONTA, cid, o["id"])
    r = ct.enviar_para_emissao(limpo, CONTA, cid, provedor=cp.ProvedorManual())
    assert r["automatico"] is False
    texto = r["roteiro"]
    for esperado in ("529.982.247-25", "ABC1D23", "64000-000", "Allianz", "12/04/1985"):
        assert esperado in texto


def test_com_api_de_emissao_a_proposta_nasce_com_o_numero(limpo):
    cid = _cotacao(limpo)
    ct.gravar_ofertas(limpo, CONTA, cid, [
        ct.Oferta(seguradora="Allianz", premio_total_centavos=408857,
                  premio_liquido_centavos=380757, iof_centavos=28100)])
    o = ct.ofertas(limpo, CONTA, cid)[0]
    ct.escolher(limpo, CONTA, cid, o["id"])
    p = _ProvedorFalso(emissao={"numero_proposta": "139041981"})
    r = ct.enviar_para_emissao(limpo, CONTA, cid, provedor=p)
    assert r["automatico"] is True and r["numero_proposta"] == "139041981"
    with limpo.connection() as c:
        num, sit = c.execute("select numero_proposta, situacao from apolices where id=%s",
                             (r["apolice_id"],)).fetchone()
    assert (num, sit) == ("139041981", "proposta")


def test_emissao_que_falha_cai_no_roteiro_em_vez_de_travar(limpo):
    """O caminho manual sempre funciona — então falhar no automático nunca pode
    deixar o corretor sem saída."""
    class _Quebra(_ProvedorFalso):
        def emitir(self, risco, oferta):
            raise cp.ProvedorErro("500 no emissor")
    cid = _cotacao(limpo)
    ct.gravar_ofertas(limpo, CONTA, cid, [ct.Oferta(seguradora="Porto", premio_total_centavos=300000)])
    o = ct.ofertas(limpo, CONTA, cid)[0]
    ct.escolher(limpo, CONTA, cid, o["id"])
    r = ct.enviar_para_emissao(limpo, CONTA, cid, provedor=_Quebra())
    assert r["automatico"] is False and "500 no emissor" in r["erro"]
    assert "COTAÇÃO #" in r["roteiro"]


# ─────────────────────────────────────────────────────────────── a lista

def test_a_fila_do_corretor_e_so_dele(limpo):
    """Mesmo recorte da carteira de apólices e da Fila do vendedor."""
    with limpo.connection() as c:
        m1 = c.execute("insert into membros (conta_id, nome) values (%s,'Corretor 1') returning id",
                       (CONTA,)).fetchone()[0]
        m2 = c.execute("insert into membros (conta_id, nome) values (%s,'Corretor 2') returning id",
                       (CONTA,)).fetchone()[0]
        c.commit()
    _cotacao(limpo, corretor_id=m1)
    _cotacao(limpo, corretor_id=m2)
    assert len(ct.listar(limpo, CONTA)) == 2
    assert len(ct.listar(limpo, CONTA, corretor_id=m1)) == 1


def test_a_origem_diz_por_onde_entrou(limpo):
    cid = _cotacao(limpo, origem="whatsapp")
    assert ct.ler(limpo, CONTA, cid)["origem_txt"] == "WhatsApp"
    cid2 = _cotacao(limpo, origem="marciano")   # valor inválido não derruba
    assert ct.ler(limpo, CONTA, cid2)["origem"] == "painel"


def test_perder_guarda_o_motivo_sem_apagar_a_falha_do_provedor(limpo):
    """Duas perguntas diferentes, duas colunas: "o provedor caiu" não pode ser
    sobrescrito por "perdi pro preço da concorrente" — é a resposta que um dia
    responde 'perco pra qual preço'."""
    cid = _cotacao(limpo)
    ct.marcar_falha(limpo, CONTA, cid, "a seguradora não respondeu")
    ct.perder(limpo, CONTA, cid, "cliente ficou com a renovação da Porto")
    cot = ct.ler(limpo, CONTA, cid)
    assert cot["situacao"] == "perdida"
    assert cot["perda_motivo"] == "cliente ficou com a renovação da Porto"
    assert cot["erro"] == "a seguradora não respondeu"


def test_cotacao_que_ja_virou_proposta_nao_volta_pra_cotada(limpo):
    """O estado final é um fato: lançar mais uma oferta (ou recotar) não apaga que
    esta cotação já virou apólice na carteira."""
    cid = _cotacao(limpo)
    ct.gravar_ofertas(limpo, CONTA, cid, [ct.Oferta(seguradora="Porto", premio_total_centavos=300000)])
    o = ct.ofertas(limpo, CONTA, cid)[0]
    ct.escolher(limpo, CONTA, cid, o["id"])
    ct.virar_proposta(limpo, CONTA, cid)
    ct.gravar_ofertas(limpo, CONTA, cid, [ct.Oferta(seguradora="HDI", premio_total_centavos=280000)])
    assert ct.ler(limpo, CONTA, cid)["situacao"] == "proposta"
    ct.marcar_falha(limpo, CONTA, cid, "provedor caiu")
    assert ct.ler(limpo, CONTA, cid)["situacao"] == "proposta"


def test_acrescentar_oferta_nao_desfaz_a_escolha(limpo):
    """O corretor digita uma seguradora de cada vez. Se lançar um preço novo depois
    de escolher apagasse a escolha em silêncio, a proposta sairia da oferta errada
    — e ninguém veria, porque a tela continuaria parecendo certa."""
    cid = _cotacao(limpo)
    ct.acrescentar_oferta(limpo, CONTA, cid, ct.Oferta(seguradora="Porto", premio_total_centavos=410000))
    assert ct.ler(limpo, CONTA, cid)["situacao"] == "cotada"
    o = ct.ofertas(limpo, CONTA, cid)[0]
    ct.escolher(limpo, CONTA, cid, o["id"])
    ct.acrescentar_oferta(limpo, CONTA, cid, ct.Oferta(seguradora="HDI", premio_total_centavos=280000))
    achadas = ct.ofertas(limpo, CONTA, cid)
    assert [x["seguradora"] for x in achadas] == ["HDI", "Porto"]
    assert [x["seguradora"] for x in achadas if x["escolhida"]] == ["Porto"]
    assert ct.ler(limpo, CONTA, cid)["situacao"] == "escolhida"
