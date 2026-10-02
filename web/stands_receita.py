"""O botão "Receita" dos formulários do cliente do stand — o que ele preenche.

São quatro formulários (painel do gestor, app da vendedora: stand e "+ Novo
cliente", e o link do contrato que o lojista abre) e uma regra só. Vive aqui,
sem import nenhum, pra as três telas usarem o MESMO JS: a primeira versão tinha
quatro cópias e todas com o mesmo defeito (02/10/2026) — punham o nome FANTASIA
no campo Razão social e jogavam fora endereço, CEP e representante que a
Receita devolve. O dono: "o botão não está trazendo os dados".

A resposta que este JS espera é a de finance.evento_stands.receita_do_cnpj.
"""

# Sem `{{`, `{%` nem `{#`: o painel injeta isto dentro de um template Jinja.
RECEITA_JS = r"""
  // Preenche o formulário do cliente com o que a Receita devolveu.
  // Razão social, cidade e UF são da Receita: sempre entram. O resto só entra em
  // campo VAZIO — o que a pessoa já digitou não se perde. Devolve o que preencheu.
  function receitaPreenche(form, j){
    var feito = [];
    function poe(nome, valor, rotulo, sempre){
      var el = form.elements[nome];
      if (!el || !valor) return;
      var atual = String(el.value || '').trim();
      if (atual === String(valor)) return;
      if (atual && !sempre) return;
      el.value = valor;
      feito.push(rotulo);
    }
    poe('razao', j.razao, 'razão social', true);
    poe('fantasia', j.fantasia, 'nome fantasia');
    poe('rep', j.rep, 'representante legal');
    poe('end', j.end, 'endereço');
    poe('cep', j.cep, 'CEP');
    poe('cidade', j.cidade, 'cidade', true);
    poe('uf', j.uf, 'UF', true);
    poe('email', j.email, 'e-mail');
    return feito;
  }
  function receitaMsg(feito){
    return feito.length
      ? '✓ A Receita preencheu: ' + feito.join(', ') + '. Confira antes de salvar.'
      : '✓ Consultei a Receita: não há nada novo além do que já está preenchido.';
  }
"""
