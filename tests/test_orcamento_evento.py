"""Regressão do MODO EVENTO do orçamento (migração 147).

O módulo nasceu pra venda recorrente (setup + mensalidade). No nicho 'eventos'
o orçamento é outro bicho: data da festa, número de convidados, horário de
início/encerramento, quantidade × valor unitário e parcelas com vencimento.
Aqui testamos as três pontas disso:

  • agenda.janela_evento — a festa que "encerra às 24" acaba 00:00 do DIA
    SEGUINTE (virar a noite é a regra do ramo);
  • proposta — carrega/renderiza o orçamento de evento e reserva a data na
    agenda quando o cliente assina (idempotente);
  • vendas.fechar_orcamento — cada parcela vira um título a receber.

Banco de TESTE separado (ver tests/conftest.py).
"""
import json
import os
import secrets
from datetime import date, datetime, timedelta
from pathlib import Path

import pytest
from psycopg_pool import ConnectionPool

from psycopg.errors import UniqueViolation

from db.conexao import init_schema
from finance import agenda as ag, vendas
from web import proposta as prop
from web.painel_servicos import _com_retry_numero, _garantir_tabela

BASE = Path(__file__).resolve().parent.parent / "db" / "migracoes"


def _sql(nome: str) -> str:
    return (BASE / nome).read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def pool():
    p = ConnectionPool(os.environ["TEST_DATABASE_URL"], min_size=1, max_size=4,
                       open=True, kwargs={"prepare_threshold": None})
    init_schema(p)
    with p.connection() as c:
        c.execute(_sql("053_modulo_pj.sql"))        # titulos
        # a baixa do título lança no livro-caixa; `lancamentos` precisa das
        # colunas que o LivroCaixa escreve. Sem elas o arquivo só passava
        # quando outro teste tinha criado a coluna antes, no banco compartilhado.
        c.execute(_sql("018_chave_nfce_lancamentos.sql"))   # lancamentos.chave
        c.execute(_sql("057_natureza_lancamento.sql"))      # lancamentos.natureza
        c.execute(_sql("098_agenda.sql"))           # eventos_agenda
        c.execute(_sql("099_agenda_tipo.sql"))      # eventos_agenda.tipo
        c.execute(_sql("130_evento_desfecho.sql"))      # .desfecho
        c.execute(_sql("131_evento_link_online.sql"))   # .link_online
        # agenda_config completa: _reservar_na_agenda lê `pre_reserva_dias` por
        # get_config, que seleciona todas as colunas do card de lembrete.
        c.execute(_sql("101_agenda_lembretes.sql"))
        c.execute(_sql("126_agenda_avisar_convidados.sql"))
        c.execute(_sql("146_agenda_enviar_confirmacao.sql"))
        # dados da empresa que o cabeçalho do orçamento usa. Vêm das migrações
        # 038/045/049/058/059, que arrastam junto loja/catálogo — aqui só as
        # colunas, que é o que o teste precisa.
        c.execute("""
            alter table contas add column if not exists endereco      text;
            alter table contas add column if not exists cep           text;
            alter table contas add column if not exists bairro        text;
            alter table contas add column if not exists cidade        text;
            alter table contas add column if not exists uf            varchar(2);
            alter table contas add column if not exists telefone      text;
            alter table contas add column if not exists email_empresa text;
            alter table contas add column if not exists logo_url      text;
            alter table contas add column if not exists nome_fantasia text;
            alter table contas add column if not exists cnae          text;
        """)
        c.execute(_sql("064_clientes_lojista.sql"))     # clientes do lojista
        c.execute(_sql("066_pessoas_identidade.sql"))   # identidade (pessoas)
        c.execute(_sql("131_pessoa_cnpj.sql"))          # pessoas.cnpj/tipo
        c.execute(_sql("149_cliente_cidade_uf.sql"))    # clientes.cidade/uf
        c.execute("""create table if not exists nichos (
            id bigserial primary key, nome text, slug text unique, tipo text,
            ativo boolean not null default true)""")
        c.execute("alter table contas add column if not exists nicho_id bigint references nichos(id)")
        c.execute("""create table if not exists orcamentos (
            id bigserial primary key, cliente text, empresa text, segmento text,
            setup_centavos bigint default 0, mensal_centavos bigint default 0,
            primeiro_ano_centavos bigint default 0, n_modulos int default 0,
            criado_em timestamptz default now())""")
        _garantir_tabela(c)     # espelha 068/069/070/074/147 (inclusive modo/evento/parcelas)
        # DEPOIS de orcamentos existir: a 152 altera as DUAS tabelas (o vínculo
        # em orcamentos, endereço/CEP em clientes).
        c.execute(_sql("152_orcamento_cliente_vinculo.sql"))
        # pré-reserva por sinal: 160 é a agenda (status novo + prazo por conta),
        # 161 é o orçamento (valor do sinal + carimbo de pago).
        c.execute(_sql("160_agenda_pre_reserva.sql"))
        c.execute(_sql("161_orcamento_sinal.sql"))
        # 162: o vínculo título↔parcela, que a baixa do sinal usa pra achar o título
        c.execute(_sql("162_titulo_parcela_do_orcamento.sql"))
        c.execute(_sql("163_evento_sinal_esperado.sql"))
        c.commit()
    yield p
    p.close()


@pytest.fixture()
def conta_id(pool):
    with pool.connection() as c:
        cid = c.execute(
            """insert into contas (tipo, nome, razao_social, documento, endereco,
                                   bairro, cep, cidade, uf, telefone, email_empresa)
               values ('pj','Prime Eventos','M S de Sousa Junior Festas e Eventos LTDA',
                       '52.752.898/0001-58','Rua Deoclécio Brito, 3399','Planalto',
                       '64050-050','Teresina','PI','(86) 99409-5516',
                       'primeeventosthe@gmail.com')
               returning id""").fetchone()[0]
        c.execute("insert into membros (conta_id, nome, papel) values (%s,%s,'dono')",
                  (cid, "Manoel Soares de Sousa Junior"))
        c.commit()
    return cid


def _conta_do_nicho(pool, slug: str) -> int:
    with pool.connection() as c:
        c.execute("insert into nichos (nome, slug, tipo) values (%s,%s,'produto') "
                  "on conflict (slug) do nothing", (slug, slug))
        cid = c.execute(
            """insert into contas (tipo, nome, nicho_id)
               values ('pj', %s, (select id from nichos where slug=%s)) returning id""",
            (f"Conta {slug}", slug)).fetchone()[0]
        c.commit()
    return cid


EVENTO = {"data": "2025-11-18", "convidados": 50, "inicio": "19:00", "fim": "24:00",
          "tipo": "Aniversário", "contratos": ["Locação de espaço"],
          "local": "Espaço 01"}
ITENS = [{"nome": "Espaço 01 — completo", "desc": "O espaço inclui: mesas, 60 cadeiras…",
          "setup": 7200, "mensal": 0, "qtd": 1, "unitario": 7200,
          "categoria": "Locação de espaço", "foto_url": "https://ex.com/espaco.jpg"},
         {"nome": "Cadeira Modway Entreat", "desc": "Além das 60 inclusas.",
          "setup": 250, "mensal": 0, "qtd": 10, "unitario": 25,
          "categoria": "Locação de móveis e utensílios", "foto_url": ""}]
PARCELAS = [{"venc": "2025-11-13", "valor_centavos": 181000, "forma": "Pix",
             "obs": "Sinal — confirma a reserva da data"},
            {"venc": "2025-12-13", "valor_centavos": 600000, "forma": "Cartão de crédito",
             "obs": "12 parcelas de R$ 500,00"}]


def _semear(pool, conta_id, *, status="enviado", evento=EVENTO, parcelas=PARCELAS,
            numero=60, total_centavos=745000, com_desconto=None, criado_por=None,
            desconto_tipo="pct", desconto_pct=0, desconto_centavos=0, itens=None):
    """Cria um orçamento de evento e devolve (id, token). O token é sorteado: o
    banco de teste é compartilhado entre módulos e não é truncado entre runs."""
    token = "EV" + secrets.token_hex(6)
    with pool.connection() as c:
        oid = c.execute(
            """insert into orcamentos (conta_id, empresa, cliente, escopo, itens,
                   setup_centavos, primeiro_ano_centavos, status, token, modo,
                   evento, parcelas, numero, endereco, cep, cidade, uf, cnpj,
                   email, telefone, criado_por,
                   desconto_tipo, desconto_pct, desconto_centavos)
               values (%s,'Maria Teste','Maria Teste','Reserva com o sinal.',%s::jsonb,
                       %s,%s,%s,%s,'evento',%s::jsonb,%s::jsonb,%s,
                       'Rua das Flores, 120','64049-000','Teresina','PI',
                       '000.000.000-00','maria@teste.com','(86) 99999-0000',
                       coalesce(%s, (select min(id)::text from membros where conta_id=%s)),
                       %s,%s,%s)
               returning id""",
            (conta_id, json.dumps(ITENS if itens is None else itens), total_centavos,
             (com_desconto if com_desconto is not None else total_centavos), status, token,
             json.dumps(evento) if evento is not None else None,
             json.dumps(parcelas), numero, criado_por, conta_id,
             desconto_tipo, desconto_pct, desconto_centavos)).fetchone()[0]
        c.commit()
    return oid, token


# --------------------------------------------------------------- janela_evento
def test_festa_que_encerra_as_24_termina_no_dia_seguinte():
    ini, fim = ag.janela_evento("2025-11-18", "19:00", "24:00")
    assert (ini.day, ini.hour) == (18, 19)
    assert (fim.day, fim.hour) == (19, 0)      # 00:00 do dia seguinte, não 24h do mesmo
    assert fim > ini


def test_festa_que_vira_a_noite_tambem_rola_o_dia():
    _, fim = ag.janela_evento("2025-11-18", "20:00", "02:00")
    assert (fim.day, fim.hour) == (19, 2)


def test_festa_que_acaba_no_mesmo_dia_nao_rola():
    ini, fim = ag.janela_evento("18/11/2025", "14:00", "23:00")
    assert fim.day == ini.day == 18 and fim.hour == 23


def test_horario_tolerante_e_faltando():
    ini, fim = ag.janela_evento("2025-11-18", "19", "19h30")
    assert (ini.hour, ini.minute) == (19, 0)
    assert (fim.day, fim.hour, fim.minute) == (18, 19, 30)
    assert ag.janela_evento("2025-11-18", "", "24:00") == (None, None)   # sem início, não marca
    assert ag.janela_evento("", "19:00", "24:00") == (None, None)        # sem data, não marca
    ini2, fim2 = ag.janela_evento("2025-11-18", "19:00", "")             # sem fim: só o início
    assert ini2 is not None and fim2 is None


# --------------------------------------------------------------- proposta
def test_carregar_traz_evento_parcelas_e_emitente(pool, conta_id):
    _, tok = _semear(pool, conta_id)
    d = prop._carregar(tok, pool=pool)
    assert d["modo"] == "evento"
    assert d["evento"]["convidados"] == 50 and d["evento"]["fim"] == "24:00"
    assert d["numero"] == 60 and d["doc_num"] == "Nº 60"
    assert d["total"] == "R$ 7.450,00"                       # com centavos, como orçamento
    assert d["validade"] == date(2025, 11, 18)               # vale até o dia da festa
    assert d["emitente"]["doc"] == "52.752.898/0001-58"
    assert d["emitente"]["cidade"] == "Teresina"
    assert d["cliente"]["endereco"] == "Rua das Flores, 120"
    assert [p["valor_centavos"] for p in d["parcelas"]] == [181000, 600000]


def test_carregar_sem_evento_segue_recorrente(pool, conta_id):
    tok = "RC" + secrets.token_hex(6)
    with pool.connection() as c:
        c.execute(
            """insert into orcamentos (conta_id, empresa, cliente, itens, setup_centavos,
                   mensal_centavos, primeiro_ano_centavos, status, token)
               values (%s,'Clínica','Ana','[]'::jsonb,920000,210000,3440000,'enviado',%s)""",
            (conta_id, tok))
        c.commit()
    d = prop._carregar(tok, pool=pool)
    assert d["modo"] == "recorrente"
    assert d["validade"] == d["criado"] + timedelta(days=15)   # os 15 dias de sempre
    assert d["doc_num"].startswith("PR-")


def test_pagina_do_evento_mostra_qtd_valor_unitario_e_parcelas(pool, conta_id, monkeypatch):
    _, tok = _semear(pool, conta_id)
    # a rota chama _carregar() com o pool de produção; aqui ela usa o de teste.
    carregar, pool_teste = prop._carregar, pool
    monkeypatch.setattr(prop, "_carregar", lambda t, pool=None: carregar(t, pool=pool_teste))
    html = prop.proposta_publica(None, tok).body.decode()
    assert "Orçamento de evento" in html and "Nº 60" in html
    assert "50" in html and "19:00" in html and "24:00" in html      # bloco do evento
    assert "Aniversário" in html and "Locação de espaço" in html
    # linha de item: número puro (o R$ fica nos totais e nas parcelas)
    assert ">25,00<" in html and ">250,00<" in html                  # 10 × 25 = 250
    assert html.count("<svg") >= len(ITENS)          # cada item leva seu ícone
    assert html.count("Locação de espaço") >= 2   # categoria do item + subtotal por categoria
    assert "Aniversário" in html                       # o tipo escolhido
    assert "Casamento" not in html and "Corporativo" not in html   # o cardápio de opções, não
    assert "Locação de espaço" in html                 # o contrato marcado
    assert "Vendedor: Manoel" in html
    assert "R$ 7.450,00" in html                                     # total do evento
    assert "Plano de pagamento" in html and "R$ 1.810,00" in html
    assert "52.752.898/0001-58" in html                              # emitente no cabeçalho
    assert "Mensalidade" not in html and "1º ano" not in html
    # bloco de aprovação: caixinha de aceite marcada pelo cliente + o botão que
    # diz o que acontece ao aprovar.
    assert "✓ Aprovar e reservar a data" in html
    assert 'type="checkbox" name="aceite"' in html
    assert "Li e concordo com os termos e valores deste orçamento" in html
    assert "nome, CPF, data/hora e IP" in html


def test_subtotal_por_categoria(pool, conta_id):
    from web.proposta import _subtotais
    assert _subtotais(ITENS) == [{"nome": "Locação de espaço", "valor": "R$ 7.200,00"},
                                 {"nome": "Locação de móveis e utensílios", "valor": "R$ 250,00"}]
    # uma categoria só repetiria o total, e item sem categoria mentiria a soma:
    # nos dois casos o bloco não aparece.
    assert _subtotais([ITENS[0]]) == []
    assert _subtotais([ITENS[0], {"setup": 100}]) == []


def test_documento_cep_e_telefone_saem_com_mascara(pool, conta_id):
    """O cadastro guarda o que a empresa digitou (52752898000158); a folha
    mostra 52.752.898/0001-58. Idem CPF, CEP e telefone — e endereço gritando
    em caixa alta é capitalizado."""
    with pool.connection() as c:
        c.execute("""update contas set documento='52752898000158', cep='64050050',
                            telefone='86994171008', endereco='DEOCLECIO BRITO, 3399',
                            bairro='PLANALTO'
                      where id=%s""", (conta_id,))
        c.commit()
    _, tok = _semear(pool, conta_id)
    d = prop._carregar(tok, pool=pool)
    assert d["emitente"]["doc"] == "52.752.898/0001-58"
    assert d["emitente"]["cep"] == "64050-050"
    assert d["emitente"]["telefone"] == "(86) 99417-1008"
    assert d["emitente"]["endereco"] == "Deoclecio Brito, 3399"
    assert d["emitente"]["bairro"] == "Planalto"
    assert d["cliente"]["doc"] == "000.000.000-00"      # já veio mascarado, fica igual
    assert d["cliente"]["telefone"] == "(86) 99999-0000"


def test_vendedor_sai_na_folha_mesmo_quando_quem_vendeu_e_o_dono(pool, conta_id):
    """criado_por guarda o id do membro OU a palavra 'dono'. No segundo caso o
    nome sumia da folha — e quem vende, no cliente pequeno, é justamente o dono."""
    _, tok = _semear(pool, conta_id, criado_por="dono")
    d = prop._carregar(tok, pool=pool)
    assert d["vendedor_nome"] == "Prime Eventos"          # o nome da conta
    _, tok2 = _semear(pool, conta_id, numero=61)          # criado por um membro
    assert prop._carregar(tok2, pool=pool)["vendedor_nome"] == "Manoel Soares de Sousa Junior"


def test_desconto_aparece_e_manda_no_total(pool, conta_id, monkeypatch):
    """O desconto era aplicado na tela, gravado em primeiro_ano_centavos e
    ignorado pela folha, que mostrava a soma bruta dos itens.

    Com a 172 ele saiu do jsonb do evento e virou coluna — a folha lê de lá, e o
    rótulo só anuncia a porcentagem quando foi percentual que se digitou."""
    _, tok = _semear(pool, conta_id, total_centavos=745000, com_desconto=670500,
                     parcelas=[], desconto_tipo="pct", desconto_pct=10)
    d = prop._carregar(tok, pool=pool)
    assert d["subtotal_itens"] == "R$ 7.450,00"
    assert d["desconto_pct"] == "10" and d["desconto_final"] == "R$ 745,00"
    assert d["total"] == "R$ 6.705,00"                    # é o total COM desconto

    carregar, pool_teste = prop._carregar, pool
    monkeypatch.setattr(prop, "_carregar", lambda t, pool=None: carregar(t, pool=pool_teste))
    html = prop.proposta_publica(None, tok).body.decode()
    assert "Desconto (10%)" in html and "− R$ 745,00" in html


def test_a_folha_mostra_o_desconto_de_cada_item_riscado(pool, conta_id, monkeypatch):
    """O desconto por linha na folha do CLIENTE. Ele vê o cheio riscado e o que
    paga — desconto que o cliente não vê some da conta dele sem explicação.

    E o "Subtotal dos itens" tem que somar o LÍQUIDO: somando o bruto, a conta
    não fecharia com a tabela que o cliente acabou de ler logo acima."""
    itens = [dict(ITENS[0], desc_tipo="pct", desc_val=10),      # 7.200 −10% = 6.480
             dict(ITENS[1], desc_tipo="valor", desc_val=50)]    #   250 −50  =   200
    _, tok = _semear(pool, conta_id, itens=itens, total_centavos=745000,
                     com_desconto=634800, parcelas=[],
                     desconto_tipo="pct", desconto_pct=5)
    d = prop._carregar(tok, pool=pool)
    l1, l2 = prop._linhas_evento(d)
    assert l1["subtotal"] == "6.480,00" and l1["cheio"] == "7.200,00"
    assert l2["subtotal"] == "200,00" and l2["cheio"] == "250,00"
    # 6.480 + 200 = 6.680, e é ISSO que o subtotal soma — não os 7.450 brutos
    assert d["subtotal_itens"] == "R$ 6.680,00"

    carregar, pool_teste = prop._carregar, pool
    monkeypatch.setattr(prop, "_carregar", lambda t, pool=None: carregar(t, pool=pool_teste))
    html = prop.proposta_publica(None, tok).body.decode()
    assert 'class="risc">7.200,00' in html and "6.480,00" in html
    assert "R$ 6.680,00" in html


def test_desconto_em_reais_nao_anuncia_porcentagem_que_ninguem_digitou(pool, conta_id,
                                                                      monkeypatch):
    """O rótulo diz só "Desconto" quando o valor foi em reais. Inventar uma
    porcentagem que o dono não digitou seria informação que ninguém deu."""
    _, tok = _semear(pool, conta_id, total_centavos=745000, com_desconto=670500,
                     parcelas=[], desconto_tipo="valor", desconto_centavos=74500)
    d = prop._carregar(tok, pool=pool)
    assert d["desconto_pct"] == "" and d["desconto_final"] == "R$ 745,00"
    carregar, pool_teste = prop._carregar, pool
    monkeypatch.setattr(prop, "_carregar", lambda t, pool=None: carregar(t, pool=pool_teste))
    html = prop.proposta_publica(None, tok).body.decode()
    assert "− R$ 745,00" in html and "Desconto (" not in html
    assert "R$ 6.705,00" in html


def test_fechar_evento_sem_parcelas_usa_o_total_com_desconto(pool, conta_id):
    oid, _ = _semear(pool, conta_id, parcelas=[], total_centavos=745000, com_desconto=670500)
    r = vendas.fechar_orcamento(pool, conta_id, oid)
    with pool.connection() as c:
        valor = c.execute("select valor_centavos from titulos where id=%s",
                          (r["titulos"][0],)).fetchone()[0]
    assert valor == 670500      # o que o cliente vai pagar, não a soma dos itens


def test_cliente_do_orcamento_entra_na_base_de_clientes(pool, conta_id):
    """O vendedor puxa o lead, gera o orçamento — e o cliente passa a existir na
    aba Clientes, pra dar pra corrigir um telefone errado. Salvar de novo não
    duplica: a identidade funde pelo documento."""
    from types import SimpleNamespace
    from finance import clientes as cli
    from web.painel_servicos import _espelhar_cliente

    dados = SimpleNamespace(empresa="Maria Teste da Silva", cliente="Maria",
                            cnpj="529.982.247-25", whatsapp="(86) 99999-0000",
                            telefone="", email="maria@teste.com")
    cid1 = _espelhar_cliente(pool, conta_id, dados)
    cid2 = _espelhar_cliente(pool, conta_id, dados)      # salvou o orçamento de novo
    assert cid1 and cid1 == cid2

    achados = cli.listar_clientes(pool, conta_id, busca="Maria")
    assert len(achados) == 1
    assert achados[0]["nome"] == "Maria Teste da Silva"
    assert achados[0]["email"] == "maria@teste.com"

    # documento inválido não impede: o cliente entra sem documento
    outro = _espelhar_cliente(pool, conta_id, SimpleNamespace(
        empresa="Cliente Sem Doc", cliente="", cnpj="111.111.111-11",
        whatsapp="86988887777", telefone="", email=""))
    assert outro

    # sem nome não há o que salvar
    assert _espelhar_cliente(pool, conta_id, SimpleNamespace(
        empresa="", cliente="", cnpj="", whatsapp="", telefone="", email="")) is None


def test_cidade_e_uf_do_orcamento_ficam_editaveis_na_aba_clientes(pool, conta_id):
    """Cidade/UF vêm do orçamento pra base de Clientes — e de lá dá pra corrigir.

    Sem isso um "Teresina/OI" digitado errado no orçamento não tinha onde ser
    consertado: a tela de Clientes não tinha os campos."""
    from types import SimpleNamespace
    from finance import clientes as cli
    from web.painel_servicos import _espelhar_cliente

    cid = _espelhar_cliente(pool, conta_id, SimpleNamespace(
        empresa="Joana Ribeiro", cliente="", cnpj="529.982.247-25",
        whatsapp="(86) 98888-1111", telefone="", email="joana@teste.com",
        cidade="Teresina", uf="pi"))
    assert cid
    salvo = cli.obter_cliente(pool, conta_id, cid)
    assert (salvo["cidade"], salvo["uf"]) == ("Teresina", "PI")   # UF sobe pra caixa alta

    assert cli.atualizar_cliente(pool, conta_id, cid, cidade="Timon", uf="ma")
    corrigido = cli.obter_cliente(pool, conta_id, cid)
    assert (corrigido["cidade"], corrigido["uf"]) == ("Timon", "MA")


def _cpf_novo() -> str:
    """Um CPF válido e inédito a cada chamada.

    O CPF FUNDE a identidade, e o banco de teste é compartilhado entre módulos e
    não é truncado entre runs: CPF fixo faria um teste enxergar o cliente (já
    renomeado) que outro deixou pra trás."""
    base = "".join(secrets.choice("0123456789") for _ in range(9))

    def dv(parcial: str) -> str:
        soma = sum(int(d) * (len(parcial) + 1 - i) for i, d in enumerate(parcial))
        resto = (soma * 10) % 11
        return "0" if resto == 10 else str(resto)

    d1 = dv(base)
    return base + d1 + dv(base + d1)


def _vincular(pool, oid: int, cliente_id: int) -> None:
    with pool.connection() as c:
        c.execute("update orcamentos set cliente_id=%s where id=%s", (cliente_id, oid))
        c.commit()


def test_folha_rele_o_cadastro_enquanto_o_orcamento_nao_foi_assinado(pool, conta_id):
    """Corrigiu na aba Clientes, reimprimiu, saiu certo — sem refazer a proposta.

    O orçamento guardou 'Maria Teste' e 'Teresina/OI' (errado). O lojista arruma
    o cadastro; a folha, que agora tem o VÍNCULO, lê de lá."""
    from finance import clientes as cli

    oid, tok = _semear(pool, conta_id)
    cid = cli.criar_cliente(pool, conta_id, "Joana Ribeiro",
                            cpf=_cpf_novo(), telefone="86988881111")
    _vincular(pool, oid, cid)

    cli.atualizar_cliente(pool, conta_id, cid, nome="Joana Ribeiro de Sousa",
                          cidade="Teresina", uf="PI",
                          endereco="Rua das Flores, 120 · Bairro Jóquei",
                          cep="64049-000")
    d = prop._carregar(tok, pool=pool)
    assert d["empresa"] == "Joana Ribeiro de Sousa"
    assert d["cliente"]["cidade"] == "Teresina" and d["cliente"]["uf"] == "PI"
    assert "Rua das Flores" in d["cliente"]["endereco"]
    assert d["cliente"]["cep"] == "64049-000"       # máscara vem da folha


def test_orcamento_assinado_congela_o_que_o_cliente_aprovou(pool, conta_id):
    """Assinou, congelou. Corrigir o cadastro depois NÃO mexe no documento
    aprovado — o que o cliente assinou é o que fica no papel; erro em documento
    assinado se conserta emitindo outro."""
    from finance import clientes as cli

    for status in ("aprovada", "fechado"):
        oid, tok = _semear(pool, conta_id, status=status, numero=None)
        cid = cli.criar_cliente(pool, conta_id, f"Carlos Assinado {status}",
                                cpf=_cpf_novo(), telefone="86977772222")
        _vincular(pool, oid, cid)
        cli.atualizar_cliente(pool, conta_id, cid, nome="Nome Trocado Depois")
        d = prop._carregar(tok, pool=pool)
        assert d["empresa"] == "Maria Teste", status


def test_sem_vinculo_a_folha_segue_o_texto_congelado(pool, conta_id):
    """Orçamento antigo (anterior ao vínculo) não pode quebrar nem mudar."""
    _, tok = _semear(pool, conta_id, numero=None)
    d = prop._carregar(tok, pool=pool)
    assert d["empresa"] == "Maria Teste"
    assert d["cliente"]["cidade"] == "Teresina"


def test_campo_vazio_no_cadastro_nao_apaga_o_do_orcamento(pool, conta_id):
    """O lojista pode não ter preenchido tudo na aba Clientes: cada campo cai no
    valor congelado em vez de sair em branco na folha."""
    from finance import clientes as cli

    oid, tok = _semear(pool, conta_id, numero=None)
    cid = cli.criar_cliente(pool, conta_id, "Bruno Meio Cadastro",
                            cpf=_cpf_novo())   # sem endereço, sem cidade
    _vincular(pool, oid, cid)
    d = prop._carregar(tok, pool=pool)
    assert d["empresa"] == "Bruno Meio Cadastro"      # veio do cadastro
    assert d["cliente"]["cidade"] == "Teresina"       # seguiu o do orçamento
    assert "Rua das Flores" in d["cliente"]["endereco"]


def test_cliente_arquivado_nao_derruba_a_folha(pool, conta_id):
    """O link está com o CLIENTE: a folha tem que abrir de qualquer jeito."""
    from finance import clientes as cli

    oid, tok = _semear(pool, conta_id, numero=None)
    cid = cli.criar_cliente(pool, conta_id, "Ana Arquivada",
                            cpf=_cpf_novo())
    _vincular(pool, oid, cid)
    assert cli.arquivar_cliente(pool, conta_id, cid)
    d = prop._carregar(tok, pool=pool)
    assert d is not None and d["empresa"] == "Maria Teste"


def test_contato_do_cliente_nao_sai_repetido(pool, conta_id):
    """WhatsApp e telefone são o mesmo número: o cliente lia o próprio número
    duas vezes na folha. Sai uma vez — e a máscara vence o número cru."""
    with pool.connection() as c:
        oid, tok = _semear(pool, conta_id, numero=None)
        c.execute("update orcamentos set whatsapp='86999990000' where id=%s", (oid,))
        c.commit()
    d = prop._carregar(tok, pool=pool)
    assert d["contatos"] == ["(86) 99999-0000", "maria@teste.com"]

    from web.proposta import _contatos
    # números diferentes seguem os dois; e-mail não some
    assert _contatos("(86) 98188-5930", "(86) 3221-1234", "x@y.com") == \
        ["(86) 98188-5930", "(86) 3221-1234", "x@y.com"]
    assert _contatos("", "", "") == []


def test_local_do_evento_ja_vem_com_o_endereco_da_empresa():
    """A festa quase sempre é no salão da própria empresa: o campo Local nasce
    preenchido com o endereço dela (evento fora, o vendedor troca)."""
    from web.painel_servicos import _local_padrao

    assert _local_padrao({"endereco": "Rua Deoclécio Brito, 3399", "bairro": "Planalto",
                          "cidade": "Teresina", "uf": "pi"}) == \
        "Rua Deoclécio Brito, 3399 · Planalto · Teresina/PI"
    # empresa que só tem a rua cadastrada não vira endereço com separador solto
    assert _local_padrao({"endereco": "Rua A, 1"}) == "Rua A, 1"
    # sem endereço, campo vazio — melhor branco do que "· Teresina/PI"
    assert _local_padrao({"cidade": "Teresina", "uf": "PI"}) == ""


# ------------------------------------------------- o modo vem do nicho, sempre
def test_modo_por_nicho():
    assert vendas.modo_por_nicho("eventos") == "evento"
    assert vendas.modo_por_nicho("tecnologia") == "recorrente"
    assert vendas.modo_por_nicho(None) == "recorrente"


def test_modo_do_orcamento_pela_conta(pool):
    """Orçamento nasce em quatro portas (painel, cockpit, prospecção, agente) e
    todas perguntam à conta — senão a empresa de eventos manda folha de
    mensalidade dependendo de onde o vendedor clicou."""
    assert vendas.modo_do_orcamento(pool, _conta_do_nicho(pool, "eventos")) == "evento"
    assert vendas.modo_do_orcamento(pool, _conta_do_nicho(pool, "tecnologia")) == "recorrente"
    assert vendas.modo_do_orcamento(pool, 10 ** 9) == "recorrente"   # conta inexistente


def test_numero_repete_quando_dois_salvam_junto():
    """A série por conta é garantida pelo índice único: quem perde a corrida
    refaz o cálculo em vez de estourar na cara do vendedor."""
    class FakeCursor:
        def __init__(self): self.rollbacks = 0
        def rollback(self): self.rollbacks += 1

    c = FakeCursor()
    tentativas = []

    def uma_colisao():
        tentativas.append(1)
        if len(tentativas) == 1:
            raise UniqueViolation("colidiu")
        return ("ok",)

    assert _com_retry_numero(c, uma_colisao) == ("ok",)
    assert c.rollbacks == 1

    def sempre_colide():
        raise UniqueViolation("colidiu")

    assert _com_retry_numero(c, sempre_colide) is None    # desiste e devolve vazio


def test_folha_sem_dados_do_evento_nao_imprime_bloco_vazio(pool, conta_id, monkeypatch):
    """Conta de eventos que gerou a proposta pelo cockpit não tem data nem
    convidados: a folha vai direto pros itens, sem quatro travessões."""
    _, tok = _semear(pool, conta_id, evento={})
    carregar, pool_teste = prop._carregar, pool
    monkeypatch.setattr(prop, "_carregar", lambda t, pool=None: carregar(t, pool=pool_teste))
    html = prop.proposta_publica(None, tok).body.decode()
    assert "O evento" not in html and "Convidados" not in html
    assert "Itens do orçamento" in html and "R$ 7.450,00" in html   # o resto sai igual


# --------------------------------------------------------------- reserva na agenda
def test_assinar_reserva_a_data_na_agenda(pool, conta_id):
    _, tok = _semear(pool, conta_id)
    assert prop.registrar_assinatura(pool, tok, "Maria Teste", "", "1.2.3.4")
    d = prop._carregar(tok, pool=pool)
    ev_id = prop._reservar_na_agenda(d, pool=pool)
    assert ev_id
    with pool.connection() as c:
        titulo, inicio, fim, local, desc = c.execute(
            "select titulo, inicio, fim, local, descricao from eventos_agenda where id=%s",
            (ev_id,)).fetchone()
        gravado = c.execute("select evento_agenda_id from orcamentos where token=%s",
                            (tok,)).fetchone()[0]
    assert titulo == "Aniversário — Maria Teste"
    assert (inicio.astimezone(ag.BRT).day, inicio.astimezone(ag.BRT).hour) == (18, 19)
    assert (fim.astimezone(ag.BRT).day, fim.astimezone(ag.BRT).hour) == (19, 0)
    assert local == "Espaço 01" and "50 convidados" in desc
    assert gravado == ev_id


# ------------------------------------------------------- pré-reserva pelo sinal
def test_sinal_do_orcamento_sai_da_primeira_parcela():
    """O valor do sinal já existia — na `obs` que o gerador escreve. Só ali: parcela
    sem essa marca é parcela normal, e orçamento montado na mão não tem sinal."""
    assert prop.sinal_do_orcamento({"parcelas": PARCELAS}) == 181000
    assert prop.sinal_do_orcamento({"parcelas": [{"valor_centavos": 5000, "obs": "1ª de 3"}]}) == 0
    assert prop.sinal_do_orcamento({"parcelas": []}) == 0
    assert prop.sinal_do_orcamento({}) == 0


def test_com_sinal_a_data_nasce_segurada_e_nao_firme(pool, conta_id):
    """O buraco que essa mudança fecha: a folha dizia "Sinal — confirma a reserva da
    data" e a assinatura marcava compromisso firme mesmo assim, sem pagamento nenhum."""
    _, tok = _semear(pool, conta_id)
    prop.registrar_assinatura(pool, tok, "Maria Teste", "", "1.2.3.4")
    ev_id = prop._reservar_na_agenda(prop._carregar(tok, pool=pool), pool=pool)
    with pool.connection() as c:
        st, ate, desc = c.execute(
            "select status, pre_reserva_ate, descricao from eventos_agenda where id=%s",
            (ev_id,)).fetchone()
        sinal = c.execute("select sinal_centavos from orcamentos where token=%s",
                          (tok,)).fetchone()[0]
    assert st == ag.PRE_RESERVADO and "aguardando sinal" in desc
    assert sinal == 181000                     # congelado no momento da reserva
    dias = (ate - ag.agora_brt()).days
    assert dias == ag.PRE_RESERVA_DIAS - 1     # 3 dias cheios, arredondando pra baixo


def test_prazo_da_pre_reserva_respeita_a_config_da_empresa(pool, conta_id):
    ag.salvar_pre_reserva_dias(pool, conta_id, 7)
    _, tok = _semear(pool, conta_id)
    ev_id = prop._reservar_na_agenda(prop._carregar(tok, pool=pool), pool=pool)
    with pool.connection() as c:
        ate = c.execute("select pre_reserva_ate from eventos_agenda where id=%s",
                        (ev_id,)).fetchone()[0]
    assert (ate - ag.agora_brt()).days == 6
    ag.salvar_pre_reserva_dias(pool, conta_id, ag.PRE_RESERVA_DIAS)   # devolve o padrão


def test_sem_sinal_a_data_e_reservada_na_hora(pool, conta_id):
    """Regressão do fluxo antigo: orçamento montado na mão, sem linha de sinal, não
    tem o que esperar — a data é do cliente na assinatura, como sempre foi."""
    _, tok = _semear(pool, conta_id, parcelas=[{"venc": "2025-12-13",
                                                "valor_centavos": 745000,
                                                "forma": "Pix", "obs": "À vista"}])
    ev_id = prop._reservar_na_agenda(prop._carregar(tok, pool=pool), pool=pool)
    with pool.connection() as c:
        st, ate, desc = c.execute(
            "select status, pre_reserva_ate, descricao from eventos_agenda where id=%s",
            (ev_id,)).fetchone()
    assert st == "ativo" and ate is None and "aprovado pelo cliente" in desc


def test_sinal_confirmado_firma_a_data_e_e_idempotente(pool, conta_id):
    """O caminho do botão "Sinal recebido" (web/painel_servicos), pelo modelo."""
    _, tok = _semear(pool, conta_id)
    ev_id = prop._reservar_na_agenda(prop._carregar(tok, pool=pool), pool=pool)
    with pool.connection() as c:
        c.execute("update orcamentos set sinal_pago_em=now() where token=%s", (tok,))
        c.commit()
    assert ag.confirmar_pre_reserva(pool, conta_id, ev_id) is True
    with pool.connection() as c:
        assert c.execute("select status, pre_reserva_ate from eventos_agenda where id=%s",
                         (ev_id,)).fetchone() == ("ativo", None)
    assert ag.confirmar_pre_reserva(pool, conta_id, ev_id) is False
    # e o orçamento com sinal JÁ PAGO não volta a segurar a data se for reprocessado
    d = prop._carregar(tok, pool=pool)
    assert d["sinal_pago_em"] is not None
    assert prop.sinal_do_orcamento(d) == 181000     # o valor continua lá, na parcela


def test_folha_do_cliente_avisa_que_a_data_esta_segurada(pool, conta_id, monkeypatch):
    """A proposta prometia "a data está reservada" na hora da aprovação. Com sinal
    pendente ela precisa dizer a verdade: segurada, até quando, e por quê."""
    carregar, pool_teste = prop._carregar, pool
    monkeypatch.setattr(prop, "_carregar", lambda t, pool=None: carregar(t, pool=pool_teste))
    _, tok = _semear(pool, conta_id)
    html = prop.proposta_publica(None, tok).body.decode()
    assert "fica <b>segurada</b>" in html and "R$ 1.810,00" in html
    prop.registrar_assinatura(pool, tok, "Maria Teste", "", "1.2.3.4")
    prop._reservar_na_agenda(prop._carregar(tok, pool=pool), pool=pool)
    html2 = prop.proposta_publica(None, tok).body.decode()
    assert "está segurada pra você até" in html2
    assert "está reservada" not in html2          # a promessa velha não sobra na folha


def test_a_mensagem_de_erro_da_url_nao_vira_html_na_folha(pool, conta_id, monkeypatch):
    """XSS refletido na folha PÚBLICA. Ela devolve `?erro=` na tela, e o link é
    justamente o que o dono manda por WhatsApp — basta o atacante reenviar o
    mesmo link com a query trocada.

    O autoescape não cobria isto: o `_env` do portal usa `select_autoescape()`,
    que liga o escape pela EXTENSÃO do nome do template, e os templates daqui
    eram registrados no DictLoader sem extensão ('proposta') — caindo no
    `default=False`. Por isso o nome passou a terminar em .html."""
    carregar, pool_teste = prop._carregar, pool
    monkeypatch.setattr(prop, "_carregar", lambda t, pool=None: carregar(t, pool=pool_teste))
    _, tok = _semear(pool, conta_id)
    payload = '"><script>alert(1)</script>'
    html = prop.proposta_publica(None, tok, erro=payload).body.decode()
    assert payload not in html, "a mensagem de erro da URL entrou como HTML"
    assert "&lt;script&gt;" in html, "a mensagem devia aparecer, só que escapada"


def test_o_token_so_e_ecoado_depois_de_casar_com_o_banco(pool, conta_id, monkeypatch):
    """O outro valor da URL que entra no HTML é {{ token }}, no action do form.
    Ele NÃO é vetor, e é esta a razão: token que não casa com nenhuma linha cai no
    404, que não tem formulário — o que a folha ecoa é sempre um token gerado pelo
    sistema. Prendo a razão, não a conclusão."""
    carregar, pool_teste = prop._carregar, pool
    monkeypatch.setattr(prop, "_carregar", lambda t, pool=None: carregar(t, pool=pool_teste))
    _, tok = _semear(pool, conta_id)
    r = prop.proposta_publica(None, tok + '" onload="alert(1)')
    assert r.status_code == 404
    assert 'action="/proposta/' not in r.body.decode()


def test_folha_para_de_avisar_quando_o_sinal_cai(pool, conta_id, monkeypatch):
    carregar, pool_teste = prop._carregar, pool
    monkeypatch.setattr(prop, "_carregar", lambda t, pool=None: carregar(t, pool=pool_teste))
    _, tok = _semear(pool, conta_id)
    prop.registrar_assinatura(pool, tok, "Maria Teste", "", "1.2.3.4")
    ev_id = prop._reservar_na_agenda(prop._carregar(tok, pool=pool), pool=pool)
    ag.confirmar_pre_reserva(pool, conta_id, ev_id)
    html = prop.proposta_publica(None, tok).body.decode()
    assert "está segurada pra você até" not in html and "está reservada" in html


def test_folha_nao_diz_reservada_depois_que_a_data_foi_liberada(pool, conta_id, monkeypatch):
    """O prazo venceu (ou a empresa soltou) e a folha continuava dizendo "a data
    está reservada" — calada e errada. Agora ela diz o que aconteceu."""
    carregar, pool_teste = prop._carregar, pool
    monkeypatch.setattr(prop, "_carregar", lambda t, pool=None: carregar(t, pool=pool_teste))
    _, tok = _semear(pool, conta_id)
    prop.registrar_assinatura(pool, tok, "Maria Teste", "", "1.2.3.4")
    ev_id = prop._reservar_na_agenda(prop._carregar(tok, pool=pool), pool=pool)
    assert ag.liberar_pre_reserva(pool, conta_id, ev_id) is True
    html = prop.proposta_publica(None, tok).body.decode()
    assert "não está mais segurada" in html
    assert "está reservada" not in html and "está segurada pra você" not in html


def test_orcamento_do_evento_liga_a_agenda_de_volta_na_proposta(pool, conta_id):
    """É esse vínculo que faz o botão "Sinal recebido" da caixa do dia usar a mesma
    regra do funil — e o "Ver orçamento" saber pra onde ir."""
    oid, tok = _semear(pool, conta_id)
    ev_id = prop._reservar_na_agenda(prop._carregar(tok, pool=pool), pool=pool)
    assert ag.orcamento_do_evento(pool, conta_id, ev_id) == oid
    assert ag.orcamento_do_evento(pool, conta_id + 999, ev_id) is None   # escopo


def test_sinal_pela_agenda_faz_o_mesmo_que_pelo_funil(pool, conta_id):
    """Dois botões, uma regra só: firma a data E dá baixa no título, na data do
    sinal. Duas cópias da regra seria o começo de dois comportamentos."""
    oid, tok = _semear(pool, conta_id)
    ev_id = prop._reservar_na_agenda(prop._carregar(tok, pool=pool), pool=pool)
    r = vendas.fechar_orcamento(pool, conta_id, oid)         # contrato fechado antes
    assert r["sinal_titulo_id"] is None
    # o que a rota da Agenda faz: acha o orçamento pelo evento e confirma
    achado = ag.orcamento_do_evento(pool, conta_id, ev_id)
    res = vendas.confirmar_sinal(pool, conta_id, achado)
    assert res["ok"] and res["reserva_firmada"] and res["titulo_baixado"]
    with pool.connection() as c:
        assert c.execute("select status from eventos_agenda where id=%s",
                         (ev_id,)).fetchone()[0] == "ativo"
        assert c.execute("select status from titulos where orcamento_id=%s and parcela_idx=0",
                         (oid,)).fetchone()[0] == "pago"


def test_pre_reserva_da_agenda_mostra_o_orcamento_de_origem(pool, conta_id):
    """A lista ⏳ Datas seguradas traz o número do orçamento e o valor do sinal —
    é o que deixa decidir sem sair da agenda."""
    oid, tok = _semear(pool, conta_id)
    prop._reservar_na_agenda(prop._carregar(tok, pool=pool), pool=pool)
    linha = ag.pre_reservas(pool, conta_id)[0]
    assert linha["orcamento_id"] == oid and linha["orcamento_numero"] == 60
    assert linha["sinal_centavos"] == 181000


# ------------------------------------------- a ficha do evento na agenda
def test_ficha_traz_o_que_a_agenda_nao_sabia(pool, conta_id):
    """A agenda guardava uma FRASE colada na aprovação ("Orçamento Nº 1 · 100
    convidados · aprovado pelo cliente") — não é dado, não dá pra somar nem filtrar,
    e envelhece. Tudo que a ficha mostra já estava gravado; faltava alguém ler."""
    oid, tok = _semear(pool, conta_id)
    ev_id = prop._reservar_na_agenda(prop._carregar(tok, pool=pool), pool=pool)
    f = vendas.fichas_de_eventos(pool, conta_id, [ev_id])[ev_id]
    assert f["orcamento_id"] == oid and f["numero"] == 60
    assert f["tipo"] == "Aniversário" and f["convidados"] == 50
    assert f["cliente"] == "Maria Teste" and f["contato"] == "(86) 99999-0000"
    assert f["total_centavos"] == 745000
    assert [i["nome"] for i in f["itens"]] == ["Espaço 01 — completo", "Cadeira Modway Entreat"]
    assert [i["qtd"] for i in f["itens"]] == [1, 10]


def test_ficha_sem_contrato_fechado_mostra_plano_e_nao_inventa_percentual(pool, conta_id):
    """Orçamento só aprovado não tem título nenhum: ninguém cobrou. Dizer "0%
    recebido" seria mentira com cara de número — então `pct` vem None e o que
    aparece é o PLANO que o cliente aceitou."""
    _oid, tok = _semear(pool, conta_id)
    ev_id = prop._reservar_na_agenda(prop._carregar(tok, pool=pool), pool=pool)
    f = vendas.fichas_de_eventos(pool, conta_id, [ev_id])[ev_id]
    assert f["tem_titulos"] is False and f["pct"] is None
    assert f["plano_centavos"] == 781000            # 181000 + 600000
    assert f["proxima"] is None and f["vencidas"] == 0


def test_ficha_conta_o_recebido_a_partir_dos_titulos(pool, conta_id):
    """Fechado o contrato, quem manda no dinheiro são os títulos — é neles que a
    baixa acontece e é deles que sai o livro-caixa."""
    oid, tok = _semear(pool, conta_id)
    ev_id = prop._reservar_na_agenda(prop._carregar(tok, pool=pool), pool=pool)
    r = vendas.fechar_orcamento(pool, conta_id, oid)
    f = vendas.fichas_de_eventos(pool, conta_id, [ev_id])[ev_id]
    assert f["tem_titulos"] is True and f["pct"] == 0
    assert f["titulos_centavos"] == 781000 and f["aberto_centavos"] == 781000
    # a PRÓXIMA é a primeira em aberto por vencimento — o sinal, de 13/11
    assert f["proxima"]["valor_centavos"] == 181000
    assert f["proxima"]["vencimento"] == date(2025, 11, 13)
    assert f["proxima"]["vencida"] is True          # data no passado, no fixture
    assert f["vencidas"] == 2 and f["vencidas_centavos"] == 781000
    # dá baixa no sinal: o percentual anda e a próxima passa a ser a 2ª parcela
    from finance import empresa as emp
    emp.dar_baixa_titulo(pool, conta_id, r["titulos"][0], data_pagto=date(2025, 11, 13))
    f2 = vendas.fichas_de_eventos(pool, conta_id, [ev_id])[ev_id]
    assert f2["pago_centavos"] == 181000 and f2["pct"] == 23
    assert f2["proxima"]["valor_centavos"] == 600000


def test_ficha_nao_atravessa_conta_nem_inventa_evento(pool, conta_id):
    _oid, tok = _semear(pool, conta_id)
    ev_id = prop._reservar_na_agenda(prop._carregar(tok, pool=pool), pool=pool)
    assert vendas.fichas_de_eventos(pool, conta_id + 999, [ev_id]) == {}
    assert vendas.fichas_de_eventos(pool, conta_id, []) == {}
    # compromisso marcado na mão não tem orçamento — e não tem ficha
    solto = ag.criar_evento(pool, conta_id, "Reunião", ag.agora_brt() + timedelta(days=3))
    assert solto["id"] not in vendas.fichas_de_eventos(pool, conta_id, [solto["id"]])


def test_ficha_de_varios_eventos_numa_consulta_so(pool, conta_id):
    """A tela do mês pede a lista inteira: uma consulta por festa não escala."""
    ids = []
    for n in (71, 72, 73):
        _o, tok = _semear(pool, conta_id, numero=n)
        ids.append(prop._reservar_na_agenda(prop._carregar(tok, pool=pool), pool=pool))
    fichas = vendas.fichas_de_eventos(pool, conta_id, ids)
    assert sorted(fichas) == sorted(ids)
    assert {f["numero"] for f in fichas.values()} == {71, 72, 73}


# ------------------------------- reabrir a proposta e a data na agenda
def test_reabrir_sem_sinal_pago_libera_a_data(pool, conta_id):
    """Editar uma proposta aprovada desfaz a assinatura. Se ninguém pagou nada, não
    há o que sustente a reserva — a data volta pro calendário e o vínculo se desfaz,
    pra a próxima aprovação criar do zero (com a data nova, se mudou)."""
    oid, tok = _semear(pool, conta_id)
    prop.registrar_assinatura(pool, tok, "Maria Teste", "", "1.2.3.4")
    ev_id = prop._reservar_na_agenda(prop._carregar(tok, pool=pool), pool=pool)
    r = vendas.reabrir_proposta(pool, conta_id, oid, ev_id, None, EVENTO)
    assert r == {"liberou": True, "remarcou": False}
    with pool.connection() as c:
        assert c.execute("select status from eventos_agenda where id=%s",
                         (ev_id,)).fetchone()[0] == "cancelado"
        assert c.execute("select evento_agenda_id, sinal_centavos from orcamentos "
                         "where id=%s", (oid,)).fetchone() == (None, None)
    # e a próxima aprovação marca de novo, na data que estiver valendo agora
    d = prop._carregar(tok, pool=pool)
    novo = prop._reservar_na_agenda(d, pool=pool)
    assert novo and novo != ev_id


def test_reabrir_com_sinal_pago_nao_solta_a_data(pool, conta_id):
    """O cliente pagou. Perder a data de quem pagou seria o pior erro possível
    aqui — então ela fica, mesmo com a assinatura desfeita."""
    oid, tok = _semear(pool, conta_id)
    ev_id = prop._reservar_na_agenda(prop._carregar(tok, pool=pool), pool=pool)
    vendas.confirmar_sinal(pool, conta_id, oid)
    with pool.connection() as c:
        pago = c.execute("select sinal_pago_em from orcamentos where id=%s",
                         (oid,)).fetchone()[0]
    r = vendas.reabrir_proposta(pool, conta_id, oid, ev_id, pago, EVENTO)
    assert r == {"liberou": False, "remarcou": False}     # mesma data: nada a fazer
    with pool.connection() as c:
        assert c.execute("select status from eventos_agenda where id=%s",
                         (ev_id,)).fetchone()[0] == "ativo"
        assert c.execute("select evento_agenda_id from orcamentos where id=%s",
                         (oid,)).fetchone()[0] == ev_id


def test_reabrir_com_sinal_pago_e_data_nova_remarca(pool, conta_id):
    """Se a edição mudou o dia da festa, o compromisso acompanha — o vínculo
    continua, então uma re-aprovação não criaria outro."""
    oid, tok = _semear(pool, conta_id)
    ev_id = prop._reservar_na_agenda(prop._carregar(tok, pool=pool), pool=pool)
    vendas.confirmar_sinal(pool, conta_id, oid)
    with pool.connection() as c:
        pago = c.execute("select sinal_pago_em from orcamentos where id=%s",
                         (oid,)).fetchone()[0]
    novo_evento = dict(EVENTO, data="2025-12-06", inicio="20:00", fim="02:00")
    r = vendas.reabrir_proposta(pool, conta_id, oid, ev_id, pago, novo_evento)
    assert r == {"liberou": False, "remarcou": True}
    with pool.connection() as c:
        ini, fim, st = c.execute(
            "select inicio, fim, status from eventos_agenda where id=%s", (ev_id,)).fetchone()
    assert st == "ativo"
    assert ini.astimezone(ag.BRT).strftime("%d/%m %H:%M") == "06/12 20:00"
    assert fim.astimezone(ag.BRT).day == 7          # 02:00 vira a noite


def test_reabrir_sem_data_nova_nao_remarca_no_escuro(pool, conta_id):
    """Orçamento que perdeu a data na edição não pode arrastar o compromisso pra
    lugar nenhum — melhor deixar onde está e o dono resolver."""
    oid, tok = _semear(pool, conta_id)
    ev_id = prop._reservar_na_agenda(prop._carregar(tok, pool=pool), pool=pool)
    vendas.confirmar_sinal(pool, conta_id, oid)
    with pool.connection() as c:
        pago = c.execute("select sinal_pago_em from orcamentos where id=%s",
                         (oid,)).fetchone()[0]
        antes = c.execute("select inicio from eventos_agenda where id=%s", (ev_id,)).fetchone()[0]
    assert vendas.reabrir_proposta(pool, conta_id, oid, ev_id, pago,
                                   {"convidados": 50}) == {"liberou": False, "remarcou": False}
    with pool.connection() as c:
        assert c.execute("select inicio from eventos_agenda where id=%s",
                         (ev_id,)).fetchone()[0] == antes


def test_reabrir_libera_tambem_a_data_so_segurada(pool, conta_id):
    """A pré-reserva é o caso mais comum de reabertura: o cliente aprovou, o sinal
    não caiu, e a empresa mexe na proposta enquanto negocia."""
    oid, tok = _semear(pool, conta_id)
    ev_id = prop._reservar_na_agenda(prop._carregar(tok, pool=pool), pool=pool)
    with pool.connection() as c:
        assert c.execute("select status from eventos_agenda where id=%s",
                         (ev_id,)).fetchone()[0] == ag.PRE_RESERVADO
    assert vendas.reabrir_proposta(pool, conta_id, oid, ev_id, None, EVENTO)["liberou"] is True
    with pool.connection() as c:
        assert c.execute("select status from eventos_agenda where id=%s",
                         (ev_id,)).fetchone()[0] == "cancelado"


# ---------------------------- o documento tem que somar
def test_folha_mostra_o_total_do_plano_de_pagamento(pool, conta_id, monkeypatch):
    """A tabela de parcelas nunca teve linha de total, então "Total do evento" podia
    conviver com parcelas somando outra coisa sem nada apontar — e quem não somasse
    na mão não via. Aconteceu no primeiro orçamento de evento real."""
    carregar, pool_teste = prop._carregar, pool
    monkeypatch.setattr(prop, "_carregar", lambda t, pool=None: carregar(t, pool=pool_teste))
    _, tok = _semear(pool, conta_id)                 # parcelas: 181000 + 600000
    html = prop.proposta_publica(None, tok).body.decode()
    assert "Total do plano de pagamento" in html and "R$ 7.810,00" in html


def test_folha_diz_a_diferenca_quando_o_plano_nao_fecha(pool, conta_id, monkeypatch):
    """Divergir tem caso legítimo (juros de cartão), então não se bloqueia nada — mas
    o cliente tem que ver o número antes de assinar."""
    carregar, pool_teste = prop._carregar, pool
    monkeypatch.setattr(prop, "_carregar", lambda t, pool=None: carregar(t, pool=pool_teste))
    # total 745000, parcelas somando 1210500 — o caso real da produção
    _, tok = _semear(pool, conta_id, parcelas=[
        {"venc": "2025-11-13", "valor_centavos": 540500, "forma": "Pix",
         "obs": "Sinal — confirma a reserva da data"},
        {"venc": "2025-12-13", "valor_centavos": 670000, "forma": "Pix", "obs": ""}])
    html = prop.proposta_publica(None, tok).body.decode()
    assert "R$ 12.105,00" in html
    assert "+ R$ 4.655,00 em relação ao total do evento" in html


def test_folha_nao_fala_de_diferenca_quando_o_plano_fecha(pool, conta_id, monkeypatch):
    carregar, pool_teste = prop._carregar, pool
    monkeypatch.setattr(prop, "_carregar", lambda t, pool=None: carregar(t, pool=pool_teste))
    _, tok = _semear(pool, conta_id, parcelas=[
        {"venc": "2025-11-13", "valor_centavos": 245000, "forma": "Pix",
         "obs": "Sinal — confirma a reserva da data"},
        {"venc": "2025-12-13", "valor_centavos": 500000, "forma": "Pix", "obs": ""}])
    html = prop.proposta_publica(None, tok).body.decode()
    assert "Total do plano de pagamento" in html and "R$ 7.450,00" in html
    assert "em relação ao total do evento" not in html


def test_dif_plano_mede_a_divergencia():
    """O número que o botão "Fechar contrato" usa pra perguntar. Vem do servidor
    porque o botão age numa linha do funil, não no orçamento aberto no editor."""
    from web.painel_servicos import _dif_plano
    parc = [{"valor_centavos": 540500}, {"valor_centavos": 670000}]
    assert _dif_plano(parc, 940500, 0) == 270000        # passa do total
    assert _dif_plano(parc, 1210500, 0) == 0            # fecha
    assert _dif_plano(parc, 1500000, 0) == -289500      # falta
    # sem plano, sem total, ou lixo: não inventa divergência
    assert _dif_plano([], 940500, 0) == 0
    assert _dif_plano(None, 940500, 0) == 0
    assert _dif_plano(parc, 0, 0) == 0
    assert _dif_plano("nao é json", 940500, 0) == 0
    # aceita o jsonb vindo como string (driver/banco antigo)
    assert _dif_plano('[{"valor_centavos": 540500}]', 440500, 0) == 100000
    # parcela zerada não conta (é linha em branco no gerador)
    assert _dif_plano([{"valor_centavos": 0}, {"valor_centavos": 100}], 100, 0) == 0


def test_choque_de_data_avisa_o_dono_sem_bloquear(pool, conta_id, monkeypatch):
    """Dois orçamentos aprovados pro mesmo horário viravam dois compromissos calados.
    Quem decide se cabe é a empresa (buffet com dois salões cabe) — mas ela tem que
    saber na hora."""
    from finance import notificar
    saidas = []
    monkeypatch.setattr(notificar, "enviar_para_dono",
                        lambda pool, cid, texto: (saidas.append(texto) or True))
    _, tok1 = _semear(pool, conta_id)
    prop._reservar_na_agenda(prop._carregar(tok1, pool=pool), pool=pool)
    assert saidas == []                       # o primeiro não choca com nada
    _, tok2 = _semear(pool, conta_id, numero=61)   # mesma data/horário do EVENTO fixo
    ev2 = prop._reservar_na_agenda(prop._carregar(tok2, pool=pool), pool=pool)
    assert ev2                                # NÃO bloqueia: marca e avisa
    assert len(saidas) == 1 and "Choque de data" in saidas[0]
    assert "data segurada" in saidas[0]        # o que já estava lá era pré-reserva


def test_reserva_nao_duplica(pool, conta_id):
    _, tok = _semear(pool, conta_id)
    d = prop._carregar(tok, pool=pool)
    primeiro = prop._reservar_na_agenda(d, pool=pool)
    d2 = prop._carregar(tok, pool=pool)          # já tem evento_agenda_id
    assert prop._reservar_na_agenda(d2, pool=pool) is None
    with pool.connection() as c:
        n = c.execute("select count(*) from eventos_agenda where id=%s", (primeiro,)).fetchone()[0]
    assert n == 1


def test_evento_sem_data_nao_marca_nada(pool, conta_id):
    _, tok = _semear(pool, conta_id, evento={"convidados": 30, "tipo": "Casamento"})
    d = prop._carregar(tok, pool=pool)
    assert prop._reservar_na_agenda(d, pool=pool) is None


def test_recorrente_nao_vai_pra_agenda(pool, conta_id):
    tok = "RC" + secrets.token_hex(6)
    with pool.connection() as c:
        c.execute(
            """insert into orcamentos (conta_id, empresa, itens, setup_centavos, status, token)
               values (%s,'Clínica','[]'::jsonb,920000,'enviado',%s)""", (conta_id, tok))
        c.commit()
    d = prop._carregar(tok, pool=pool)
    assert prop._reservar_na_agenda(d, pool=pool) is None


# --------------------------------------------------------------- fechar contrato
def test_fechar_evento_gera_um_titulo_por_parcela(pool, conta_id):
    oid, _tok = _semear(pool, conta_id)
    r = vendas.fechar_orcamento(pool, conta_id, oid)
    assert r["ok"] and r["modo"] == "evento" and len(r["titulos"]) == 2
    with pool.connection() as c:
        linhas = c.execute(
            """select descricao, valor_centavos, vencimento, recorrente, tipo
                 from titulos where id = any(%s) order by vencimento""",
            (r["titulos"],)).fetchall()
    assert [l[1] for l in linhas] == [181000, 600000]
    assert [l[2] for l in linhas] == [date(2025, 11, 13), date(2025, 12, 13)]
    assert all(l[3] is False and l[4] == "receber" for l in linhas)   # evento não é recorrente
    assert "Sinal" in linhas[0][0] and "Maria Teste" in linhas[0][0]


def test_titulo_guarda_de_qual_parcela_veio(pool, conta_id):
    """Sem esse vínculo, voltar do título pra parcela só dava casando TEXTO de
    descrição — e a descrição do título é editável na tela."""
    oid, _tok = _semear(pool, conta_id)
    r = vendas.fechar_orcamento(pool, conta_id, oid)
    with pool.connection() as c:
        linhas = c.execute(
            "select orcamento_id, parcela_idx from titulos where id = any(%s) "
            "order by parcela_idx", (r["titulos"],)).fetchall()
    assert linhas == [(oid, 0), (oid, 1)]


# --------------------------------------------- o sinal recebido e o dinheiro
def _sinal_pago_em(pool, oid, quando):
    with pool.connection() as c:
        c.execute("update orcamentos set sinal_pago_em=%s where id=%s", (quando, oid))
        c.commit()


def _titulo_do_sinal(pool, oid):
    with pool.connection() as c:
        return c.execute(
            """select t.status, t.pago_em, l.data, l.valor_centavos, l.tipo
                 from titulos t left join lancamentos l on l.id = t.lancamento_id
                where t.orcamento_id=%s and t.parcela_idx=0""", (oid,)).fetchone()


def test_sinal_ja_confirmado_nao_vira_titulo_em_aberto(pool, conta_id):
    """O caminho normal: confirma o sinal, fecha o contrato depois. O título dessa
    parcela não pode nascer em aberto — o dinheiro está no bolso desde que o Pix
    caiu, e é NA DATA DO PIX que a receita entra no livro-caixa (é ela que decide
    o mês). Antes, o título nascia aberto e o dono dava baixa na mão, lembrando."""
    oid, _tok = _semear(pool, conta_id)
    caiu = datetime(2025, 10, 3, 14, 30, tzinfo=ag.BRT)
    _sinal_pago_em(pool, oid, caiu)
    r = vendas.fechar_orcamento(pool, conta_id, oid)
    assert r["ok"] and r["sinal_titulo_id"]
    status, pago_em, data_lanc, valor, tipo = _titulo_do_sinal(pool, oid)
    assert status == "pago" and pago_em == date(2025, 10, 3)
    assert data_lanc == date(2025, 10, 3)        # o mês da receita é o do sinal
    assert valor == 181000 and tipo == "receita"


def test_as_outras_parcelas_seguem_em_aberto(pool, conta_id):
    """Só o sinal foi pago. O resto do plano continua a receber — senão fechar o
    contrato de um evento daria por recebido o que ainda vai ser cobrado."""
    oid, _tok = _semear(pool, conta_id)
    _sinal_pago_em(pool, oid, datetime(2025, 10, 3, 14, 30, tzinfo=ag.BRT))
    r = vendas.fechar_orcamento(pool, conta_id, oid)
    with pool.connection() as c:
        linhas = c.execute(
            "select parcela_idx, status from titulos where id = any(%s) "
            "order by parcela_idx", (r["titulos"],)).fetchall()
    assert linhas == [(0, "pago"), (1, "aberto")]


def test_sinal_confirmado_depois_do_contrato_fechado_da_baixa(pool, conta_id):
    """O caminho invertido: fechou o contrato antes de o sinal cair. Quando o dono
    confirma, o título que JÁ EXISTE recebe a baixa — e com a mesma data."""
    oid, _tok = _semear(pool, conta_id)
    r = vendas.fechar_orcamento(pool, conta_id, oid)
    assert r["sinal_titulo_id"] is None                 # nada a baixar ainda
    assert _titulo_do_sinal(pool, oid)[0] == "aberto"
    caiu = datetime(2025, 10, 9, 9, 0, tzinfo=ag.BRT)
    _sinal_pago_em(pool, oid, caiu)
    with pool.connection() as c:
        parcelas = c.execute("select parcelas from orcamentos where id=%s",
                             (oid,)).fetchone()[0]
    assert vendas.baixar_titulo_do_sinal(pool, conta_id, oid, parcelas, caiu)
    status, pago_em, data_lanc, _v, _t = _titulo_do_sinal(pool, oid)
    assert status == "pago" and pago_em == date(2025, 10, 9) and data_lanc == date(2025, 10, 9)


def test_baixa_do_sinal_nao_lanca_duas_vezes(pool, conta_id):
    """Os dois caminhos podem se cruzar (fechou, confirmou, e o botão foi clicado de
    novo). A segunda passada não pode gerar outro lançamento no caixa."""
    oid, _tok = _semear(pool, conta_id)
    caiu = datetime(2025, 10, 3, 14, 30, tzinfo=ag.BRT)
    _sinal_pago_em(pool, oid, caiu)
    vendas.fechar_orcamento(pool, conta_id, oid)
    with pool.connection() as c:
        parcelas = c.execute("select parcelas from orcamentos where id=%s",
                             (oid,)).fetchone()[0]
        antes = c.execute("select count(*) from lancamentos where conta_id=%s",
                          (conta_id,)).fetchone()[0]
    assert vendas.baixar_titulo_do_sinal(pool, conta_id, oid, parcelas, caiu) is None
    with pool.connection() as c:
        depois = c.execute("select count(*) from lancamentos where conta_id=%s",
                           (conta_id,)).fetchone()[0]
    assert depois == antes


def test_linha_vazia_no_plano_nao_desalinha_o_indice_do_sinal(pool, conta_id):
    """`_parcelas` descarta linha de valor zero — é ela que fechar_orcamento percorre
    pra numerar `parcela_idx`. Se o índice do sinal fosse contado no jsonb CRU, uma
    primeira linha vazia (o dono apagou o valor e deixou a linha) deslocaria tudo e a
    baixa cairia na parcela errada: dar por recebido o que ninguém pagou."""
    oid, _tok = _semear(pool, conta_id, parcelas=[
        {"venc": "", "valor_centavos": 0, "forma": "", "obs": ""},
        {"venc": "2025-11-13", "valor_centavos": 181000, "forma": "Pix",
         "obs": "Sinal — confirma a reserva da data"},
        {"venc": "2025-12-13", "valor_centavos": 564000, "forma": "Pix", "obs": "Restante"}])
    _sinal_pago_em(pool, oid, datetime(2025, 10, 3, 14, 30, tzinfo=ag.BRT))
    r = vendas.fechar_orcamento(pool, conta_id, oid)
    assert len(r["titulos"]) == 2 and r["sinal_titulo_id"]
    with pool.connection() as c:
        linhas = c.execute(
            "select parcela_idx, valor_centavos, status from titulos where id = any(%s) "
            "order by parcela_idx", (r["titulos"],)).fetchall()
    assert linhas == [(0, 181000, "pago"), (1, 564000, "aberto")]


def test_orcamento_sem_sinal_nao_baixa_parcela_nenhuma(pool, conta_id):
    """Parcela normal na primeira linha não é sinal. Baixar por engano a primeira
    parcela de um plano seria dar por recebido o que não entrou."""
    oid, _tok = _semear(pool, conta_id, parcelas=[
        {"venc": "2025-11-13", "valor_centavos": 300000, "forma": "Pix", "obs": "1ª de 2"},
        {"venc": "2025-12-13", "valor_centavos": 445000, "forma": "Pix", "obs": "2ª de 2"}])
    _sinal_pago_em(pool, oid, datetime(2025, 10, 3, 14, 30, tzinfo=ag.BRT))
    r = vendas.fechar_orcamento(pool, conta_id, oid)
    assert r["sinal_titulo_id"] is None
    assert _titulo_do_sinal(pool, oid)[0] == "aberto"


def test_fechar_evento_sem_parcelas_gera_titulo_do_total(pool, conta_id):
    oid, _tok = _semear(pool, conta_id, parcelas=[])
    r = vendas.fechar_orcamento(pool, conta_id, oid)
    assert r["ok"] and len(r["titulos"]) == 1
    with pool.connection() as c:
        valor, recorrente = c.execute(
            "select valor_centavos, recorrente from titulos where id=%s",
            (r["titulos"][0],)).fetchone()
    assert valor == 745000 and recorrente is False


def test_fechar_evento_duas_vezes_nao_duplica_titulos(pool, conta_id):
    oid, _tok = _semear(pool, conta_id)
    assert vendas.fechar_orcamento(pool, conta_id, oid)["ok"]
    segundo = vendas.fechar_orcamento(pool, conta_id, oid)
    assert not segundo["ok"] and "fechado" in segundo["erro"]
    with pool.connection() as c:
        n = c.execute("select count(*) from titulos where conta_id=%s and descricao like 'Evento%%'",
                      (conta_id,)).fetchone()[0]
    assert n == 2      # os 2 da primeira vez; a segunda não somou nada
