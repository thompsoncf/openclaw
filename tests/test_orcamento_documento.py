"""O documento do orçamento: CPF e CNPJ em gavetas separadas.

Até 29/08/2026 `orcamentos` tinha só a coluna `cnpj`, e o documento inteiro caía
nela. Medido na Prime Eventos: os 12 orçamentos com documento tinham 11 DÍGITOS
— eram todos CPF, guardados num campo chamado cnpj. E não era exceção: os 23
clientes da conta são tipo='pf', nenhum com CNPJ, porque quem aluga salão pra
casamento e formatura é pessoa, não empresa.

Em `pessoas` já estava certo (o código roteia por tamanho). O buraco era só no
orçamento — e o efeito aparece no contrato de pessoa física, que sai lendo um
campo com o nome errado.
"""
import re
from pathlib import Path

_SRC = (Path(__file__).resolve().parent.parent / "web" / "painel_servicos.py").read_text(encoding="utf-8")


def test_a_coluna_cpf_existe_na_migracao_e_no_runtime():
    mig = (Path(__file__).resolve().parent.parent / "db" / "migracoes"
           / "190_orcamento_cpf.sql").read_text(encoding="utf-8")
    assert "add column if not exists cpf" in mig
    # o bloco de runtime espelha as migrações — se divergir, o deploy de um
    # worker novo encontra tabela sem a coluna
    assert "alter table orcamentos add column if not exists cpf" in _SRC


def test_insert_do_orcamento_tem_colunas_e_placeholders_na_mesma_conta():
    """Guarda contra o erro clássico de tupla posicional: acrescentar coluna e
    esquecer o %s (ou o contrário) só estoura em produção, no save."""
    i = _SRC.index("sql_ins = ")
    bloco = _SRC[i:i + 1600]
    cols = bloco[bloco.index("(conta_id"):bloco.index("values")]
    n_cols = len([c for c in cols.replace("(", "").replace(")", "").split(",") if c.strip()])
    vals = bloco[bloco.index("values"):bloco.index("returning")]
    assert n_cols == vals.count("%s"), f"{n_cols} colunas para {vals.count('%s')} placeholders"


def test_insert_e_update_gravam_cpf_e_cnpj_separados():
    assert "(conta_id, cliente, empresa, cpf, cnpj, segmento" in _SRC
    assert "update orcamentos set cliente=%s, empresa=%s, cpf=%s, cnpj=%s" in _SRC


def test_documento_e_roteado_por_tamanho():
    """11 dígitos vai pro cpf, 14 pro cnpj — mesma régua de `criar_cliente`."""
    assert "_cpf_val = (dados.cnpj or \"\").strip() if len(_doc) == 11 else None" in _SRC
    assert "_cnpj_val = None if len(_doc) == 11 else" in _SRC


def test_documento_de_tamanho_estranho_nao_e_descartado():
    """Digitado pela metade continua indo pra `cnpj`, como antes — guardar no
    lugar antigo é melhor que jogar fora o que o vendedor digitou."""
    trecho = _SRC[_SRC.index("_cnpj_val ="):]
    trecho = trecho[:trecho.index("\n")]
    assert "or None" in trecho and "dados.cnpj" in trecho


# --- a tela -----------------------------------------------------------------

def test_rotulo_nunca_mostra_os_dois_documentos_juntos():
    """Era 'CNPJ / CPF'. Um campo pede UM documento; o rótulo tem que dizer qual."""
    assert "'CNPJ / CPF'" not in _SRC
    assert 'id="oc-cnpj-label">CNPJ' in _SRC


def test_o_js_troca_o_rotulo_conforme_o_tipo():
    assert "nodeValue=pj?'CNPJ ':'CPF '" in _SRC


def test_a_dica_do_preenchimento_automatico_some_no_cpf():
    """'preenche empresa, segmento e contato automaticamente' só vale pra CNPJ —
    a consulta é de CNPJ. Pra CPF era promessa vazia."""
    assert 'id="oc-cnpj-dica"' in _SRC
    assert "dica.style.display=pj?'inline':'none'" in _SRC


def test_a_tela_abre_no_tipo_que_a_empresa_mais_usa():
    assert "tipo_predominante(pool, conta[0])" in _SRC
    assert re.search(r"aplicaTipoCliente\('\{\{ tipo_padrao", _SRC)
