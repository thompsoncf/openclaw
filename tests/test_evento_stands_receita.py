"""O botão "Receita" dos formulários do cliente do stand (painel, app e link do contrato).

O dono (02/10/2026): "o botão de buscar na Receita não está trazendo os dados". A
consulta funcionava; o que chegava no formulário era pouco e errado — o nome
FANTASIA entrava no campo Razão social, e endereço, CEP e representante, que a
Receita devolve, eram jogados fora.

Sem banco e sem rede: a BrasilAPI é trocada por uma resposta de mentira.
"""
import io
import json
import shutil
import subprocess

import pytest

from finance import cnpj_info
from finance import evento_stands as es
from web.stands_receita import RECEITA_JS

CNPJ = "11.222.333/0001-81"

LTDA = {
    "razao_social": "T C FERNANDES LTDA", "nome_fantasia": "ALADDIN CONSULTORIA E TECNOLOGIA",
    "descricao_tipo_de_logradouro": "RUA", "logradouro": "VETERINARIO BUGYJA BRITTO",
    "numero": "1229", "complemento": "EDIF EMPRESARIAL   SALA 208", "bairro": "HORTO",
    "cep": "64052410", "municipio": "TERESINA", "uf": "PI", "email": "CONTATO@ALADDIN.COM",
    "ddd_telefone_1": "8632220000", "codigo_natureza_juridica": 2062,
    "cnae_fiscal": 6204000, "cnae_fiscal_descricao": "Consultoria em tecnologia da informação",
    "qsa": [{"nome_socio": "FULANO INVESTIDOR", "qualificacao_socio": "Sócio"},
            {"nome_socio": "THOMPSON CAVALCANTE FERNANDES", "qualificacao_socio": "Sócio-Administrador"}],
}
MEI = {
    "razao_social": "53.489.737 IARA ARCANJA G SILVA", "nome_fantasia": "PEGA PEGA THE",
    "descricao_tipo_de_logradouro": "", "logradouro": "", "numero": "", "complemento": "",
    "bairro": "HORTO", "cep": "64052535", "municipio": "TERESINA", "uf": "PI", "email": None,
    "codigo_natureza_juridica": 2135, "qsa": [],
}


class _Resposta(io.BytesIO):
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _brasilapi(monkeypatch, dados):
    def urlopen(req, timeout=None):
        if dados is None:
            raise OSError("fora do ar")
        return _Resposta(json.dumps(dados).encode())
    monkeypatch.setattr(cnpj_info.urllib.request, "urlopen", urlopen)


# ------------------------------------------------------------ a consulta

def test_a_consulta_separa_razao_social_de_nome_fantasia_e_monta_o_endereco(monkeypatch):
    _brasilapi(monkeypatch, LTDA)
    info = cnpj_info.consultar_cnpj(CNPJ)
    assert info["razao_social"] == "T C FERNANDES LTDA"
    assert info["fantasia"] == "ALADDIN CONSULTORIA E TECNOLOGIA"
    assert info["endereco_completo"] == "RUA VETERINARIO BUGYJA BRITTO, 1229, EDIF EMPRESARIAL SALA 208, HORTO"
    assert info["representante"] == "THOMPSON CAVALCANTE FERNANDES"       # o sócio-ADMINISTRADOR, não o 1º da lista
    # o que as outras telas já usavam não mudou de sentido
    assert info["nome"] == "ALADDIN CONSULTORIA E TECNOLOGIA"              # fantasia primeiro
    assert info["endereco"] == "VETERINARIO BUGYJA BRITTO, 1229"
    assert info["cep"] == "64052410" and info["cidade"] == "TERESINA" and info["uf"] == "PI"
    assert info["email"] == "contato@aladdin.com" and info["telefone"] == "(86) 3222-0000"


def test_mei_nao_tem_socios_o_representante_e_o_proprio_titular(monkeypatch):
    _brasilapi(monkeypatch, MEI)
    info = cnpj_info.consultar_cnpj(CNPJ)
    assert info["representante"] == "IARA ARCANJA G SILVA"        # sem o número do CNPJ na frente
    assert info["endereco_completo"] is None                      # a Receita não publica a rua do MEI
    assert info["razao_social"] == "53.489.737 IARA ARCANJA G SILVA"


def test_representante_so_sugere_quando_da_pra_saber():
    r = cnpj_info._representante
    assert r({"qsa": [{"nome_socio": "ANA", "qualificacao_socio": "Sócio"}]}) == "ANA"           # sócio único
    assert r({"qsa": [{"nome_socio": "ANA", "qualificacao_socio": "Sócio"},
                      {"nome_socio": "BIA", "qualificacao_socio": "Sócio"}]}) is None             # dois iguais: não chuta
    assert r({"qsa": [{"nome_socio": "ANA", "qualificacao_socio": "Sócio"},
                      {"nome_socio": "BIA", "qualificacao_socio": "Diretor"}]}) == "BIA"
    assert r({"qsa": None, "razao_social": "EMPRESA X LTDA", "codigo_natureza_juridica": 2062}) is None
    assert r({}) is None


# ------------------------------------------------- o que o botão devolve

def test_o_botao_devolve_o_cadastro_que_o_contrato_pede(monkeypatch):
    _brasilapi(monkeypatch, LTDA)
    j = es.receita_do_cnpj(CNPJ)
    assert j == {"ok": True, "razao": "T C FERNANDES LTDA",
                 "fantasia": "ALADDIN CONSULTORIA E TECNOLOGIA",
                 "rep": "THOMPSON CAVALCANTE FERNANDES",
                 "end": "RUA VETERINARIO BUGYJA BRITTO, 1229, EDIF EMPRESARIAL SALA 208, HORTO",
                 "cep": "64052-410", "cidade": "TERESINA", "uf": "PI", "email": "contato@aladdin.com"}


def test_o_botao_explica_quando_nao_da(monkeypatch):
    _brasilapi(monkeypatch, None)                                  # Receita fora do ar
    j = es.receita_do_cnpj(CNPJ)
    assert j["ok"] is False and "tente de novo" in j["erro"]
    assert "CNPJ inválido" in es.receita_do_cnpj("123")["erro"]
    assert "só pra CNPJ" in es.receita_do_cnpj("529.982.247-25")["erro"]     # CPF válido
    assert es.receita_do_cnpj("")["ok"] is False


# ------------------------------------------- o que entra no formulário

def _roda(js_corpo, tmp_path):
    if not shutil.which("node"):
        pytest.skip("node não instalado")
    arq = tmp_path / "receita.js"
    arq.write_text("(function(){" + RECEITA_JS + js_corpo + "})();", encoding="utf-8")
    # stdin fechado: no Windows o node trava esperando o terminal
    r = subprocess.run(["node", str(arq)], capture_output=True, text=True, encoding="utf-8",
                       stdin=subprocess.DEVNULL, timeout=60)
    assert r.returncode == 0, r.stderr[:800]
    return json.loads(r.stdout)


def test_a_receita_nao_apaga_o_que_a_pessoa_digitou(tmp_path):
    out = _roda(r"""
  var j = {ok:true, razao:'T C FERNANDES LTDA', fantasia:'ALADDIN', rep:'THOMPSON', end:'RUA X, 1, HORTO',
           cep:'64052-410', cidade:'TERESINA', uf:'PI', email:null};
  function form(v){ var e = {}; ['fantasia','razao','rep','end','cep','cidade','uf','email'].forEach(function(k){ e[k] = {value: v[k] || ''}; }); return {elements: e}; }
  function vals(f){ var o = {}; Object.keys(f.elements).forEach(function(k){ o[k] = f.elements[k].value; }); return o; }
  var vazio = form({}), cheio = form({fantasia:'Loja da Ana', razao:'ALADDIN', rep:'Ana', end:'Rua Y', cep:'64000-000', cidade:'Timon', uf:'MA', email:'a@b.c'});
  var semCampos = {elements: {razao: {value: ''}}};
  var f1 = receitaPreenche(vazio, j), f2 = receitaPreenche(cheio, j), f3 = receitaPreenche(semCampos, j);
  var f4 = receitaPreenche(form({razao:'T C FERNANDES LTDA', cidade:'TERESINA', uf:'PI', fantasia:'x', rep:'x', end:'x', cep:'x'}), j);
  console.log(JSON.stringify({vazio: vals(vazio), f1: f1, cheio: vals(cheio), f2: f2, f3: f3, f4: f4, m1: receitaMsg(f1), m4: receitaMsg(f4)}));
""", tmp_path)
    # formulário vazio: entra tudo o que a Receita tem
    assert out["vazio"] == {"fantasia": "ALADDIN", "razao": "T C FERNANDES LTDA", "rep": "THOMPSON",
                            "end": "RUA X, 1, HORTO", "cep": "64052-410", "cidade": "TERESINA",
                            "uf": "PI", "email": ""}
    assert out["f1"] == ["razão social", "nome fantasia", "representante legal", "endereço", "CEP", "cidade", "UF"]
    # já preenchido: razão social, cidade e UF são da Receita; o resto fica como a pessoa digitou
    assert out["cheio"] == {"fantasia": "Loja da Ana", "razao": "T C FERNANDES LTDA", "rep": "Ana",
                            "end": "Rua Y", "cep": "64000-000", "cidade": "TERESINA", "uf": "PI",
                            "email": "a@b.c"}
    assert out["f2"] == ["razão social", "cidade", "UF"]
    assert out["f3"] == ["razão social"]                 # formulário sem os outros campos não quebra
    assert out["f4"] == []                               # nada novo: diz isso, sem inventar
    assert "razão social, nome fantasia" in out["m1"] and "Confira" in out["m1"]
    assert "nada novo" in out["m4"]


def test_os_quatro_formularios_usam_a_mesma_regra():
    import web.contrato_publico as contrato
    import web.painel_cockpit as app
    import web.painel_eventos_stands as painel

    for marca in ("{{", "{%", "{#"):                     # o painel injeta dentro de template Jinja
        assert marca not in RECEITA_JS
    fontes = {"painel": painel._TPL, "app-stand": app._STANDS_JS, "app-novo-cliente": app._CLI_NOVO_JS,
              "contrato": contrato._TPL}
    for nome, fonte in fontes.items():
        assert "receitaMsg(receitaPreenche(" in fonte, nome
        # a frase antiga prometia pouco ("Endereço e CEP você digita") e punha fantasia na razão
        assert "Endereço e CEP você digita" not in fonte, nome
        assert "value=j.nome" not in fonte.replace(" ", ""), nome
    assert "function receitaPreenche(" in painel._TPL and "function receitaPreenche(" in contrato._TPL
    assert "function receitaPreenche(" in app._receita_js()
    assert "__RECEITA_JS__" in app._CLI_NOVO_JS
