"""Painel "Doméstica" (eSocial Doméstico) — lado PF.

Cálculo de CONFERÊNCIA: cadastra empregados domésticos, lança as variáveis do
mês, mostra a DAE prevista, fecha a folha no livro-caixa PESSOAL e confere a
guia oficial. Não transmite nada — o fechamento continua no portal gov.br.

Reusa _render/_env/conta_logada do portal (base/nav/tokens do _BASE). Gate por
conta_modulos (só dono). Escopo por conta[0]. Sem location.reload() — as abas
trocam no cliente; as ações redirecionam com flash.
"""
from datetime import date

from fastapi import APIRouter, Request, Form
from fastapi.responses import HTMLResponse, RedirectResponse

from db.conexao import get_pool
from web.portal import _render, _env, conta_logada
from finance import domestica_repo as dom
from finance.domestica import texto_resumo  # noqa: F401 (paridade de API)

router = APIRouter()

_MESES = ("", "Janeiro", "Fevereiro", "Março", "Abril", "Maio", "Junho",
          "Julho", "Agosto", "Setembro", "Outubro", "Novembro", "Dezembro")


def _gate(request: Request):
    """(conta, pool) se dono + módulo ativo; senão (None, redirect)."""
    conta = conta_logada(request)
    if conta is None:
        return None, RedirectResponse("/login", status_code=303)
    if request.session.get("papel", "dono") != "dono":
        return None, RedirectResponse("/painel", status_code=303)
    pool = get_pool()
    if not dom.modulo_domestica_ativo(pool, conta[0]):
        return None, RedirectResponse("/painel", status_code=303)
    return (conta, pool), None


def _reais(v) -> int:
    return int(round(float(v) * 100))


@router.get("/painel/domestica", response_class=HTMLResponse)
def painel_domestica(request: Request, aba: str = "empregados"):
    g, redir = _gate(request)
    if redir is not None:
        return redir
    conta, pool = g
    hoje = date.today()
    vinculos = dom.listar_vinculos(pool, conta[0], so_ativos=True)
    folha = dom.calcular_competencia(pool, conta[0])
    aba = aba if aba in ("empregados", "folha", "conferencia") else "empregados"
    return _render(
        "domestica", request, titulo="Doméstica", secao_ativa="domestica",
        tem_domestica=True, aba=aba, vinculos=vinculos, folha=folha,
        comp_label=f"{_MESES[hoje.month]}/{hoje.year}",
        flash=request.session.pop("dom_flash", None),
        erro=request.session.pop("dom_erro", None),
        conferido=request.session.pop("dom_conferido", None))


@router.post("/painel/domestica/vinculo")
def dom_cadastrar(request: Request, nome: str = Form(...), salario: str = Form(...),
                  cargo: str = Form(""), cpf: str = Form(""),
                  matricula: str = Form(""), admitido_em: str = Form("")):
    g, redir = _gate(request)
    if redir is not None:
        return redir
    conta, pool = g
    try:
        adm = None
        if admitido_em.strip():
            d, m, a = [int(x) for x in admitido_em.split("/")]
            adm = date(a, m, d)
        dom.cadastrar_vinculo(pool, conta[0], nome, _reais(salario),
                              cargo=cargo, cpf=cpf, matricula=matricula,
                              admitido_em=adm)
        request.session["dom_flash"] = f"Empregado '{nome.strip()}' cadastrado."
    except Exception:
        request.session["dom_erro"] = "Não consegui cadastrar. Confere os dados."
    return RedirectResponse("/painel/domestica?aba=empregados", status_code=303)


@router.post("/painel/domestica/vinculo/{vinculo_id}/desativar")
def dom_desativar(request: Request, vinculo_id: int):
    g, redir = _gate(request)
    if redir is not None:
        return redir
    conta, pool = g
    dom.desativar_vinculo(pool, conta[0], vinculo_id)
    request.session["dom_flash"] = "Empregado arquivado."
    return RedirectResponse("/painel/domestica?aba=empregados", status_code=303)


@router.post("/painel/domestica/variavel")
def dom_variavel(request: Request, vinculo_id: int = Form(...),
                 tipo: str = Form(...), valor: str = Form(...),
                 descricao: str = Form("")):
    g, redir = _gate(request)
    if redir is not None:
        return redir
    conta, pool = g
    try:
        dom.lancar_item(pool, conta[0], vinculo_id, tipo, _reais(valor),
                        descricao=descricao)
        request.session["dom_flash"] = "Variável do mês lançada."
    except ValueError:
        request.session["dom_erro"] = "Tipo de variável inválido."
    except Exception:
        request.session["dom_erro"] = "Não consegui lançar a variável."
    return RedirectResponse("/painel/domestica?aba=folha", status_code=303)


@router.post("/painel/domestica/fechar")
def dom_fechar(request: Request):
    g, redir = _gate(request)
    if redir is not None:
        return redir
    conta, pool = g
    try:
        r = dom.fechar_folha(pool, conta[0])
        request.session["dom_flash"] = (
            "Folha fechada e lançada no caixa pessoal. A guia oficial você "
            "emite/paga no portal gov.br.")
    except Exception:
        request.session["dom_erro"] = "Não consegui fechar a folha agora."
    return RedirectResponse("/painel/domestica?aba=folha", status_code=303)


@router.post("/painel/domestica/conferir")
def dom_conferir(request: Request, vinculo_id: int = Form(...),
                 valor_guia: str = Form(...)):
    g, redir = _gate(request)
    if redir is not None:
        return redir
    conta, pool = g
    try:
        r = dom.conferir_dae(pool, conta[0], vinculo_id, _reais(valor_guia))
        request.session["dom_conferido"] = r if r.get("ok") else None
        if not r.get("ok"):
            request.session["dom_erro"] = "Não achei esse empregado."
    except Exception:
        request.session["dom_erro"] = "Não consegui conferir agora."
    return RedirectResponse("/painel/domestica?aba=conferencia", status_code=303)


# ─────────────────────────────────────────────────────────────────────────
# Template (extends base do portal; tokens do _BASE; abas no cliente)
# ─────────────────────────────────────────────────────────────────────────
_DOMESTICA_TPL = """{% extends "base" %}{% block conteudo %}
<div class="card larga">
  <h1>🏠 Doméstica <span style="font-size:.8rem;color:var(--txt-mut);font-weight:400">· {{ comp_label }}</span></h1>
  <p class="dica-toque">Cálculo de <b>conferência</b> — o fechamento continua no
     portal gov.br (eSocial Doméstico). Aqui a gente antecipa a DAE, confere a
     guia e lança o custo no seu caixa pessoal.</p>

  {% if flash %}<div class="tag" style="background:rgba(29,158,117,.16);color:var(--verde-claro);display:block;margin:.4rem 0">{{ flash }}</div>{% endif %}
  {% if erro %}<div class="tag" style="background:#6e2b2b;color:#fff;display:block;margin:.4rem 0">{{ erro }}</div>{% endif %}

  <div class="abas" role="tablist">
    <button class="aba{% if aba=='empregados' %} on{% endif %}" onclick="domAba('empregados',this)">Empregados</button>
    <button class="aba{% if aba=='folha' %} on{% endif %}" onclick="domAba('folha',this)">Folha do mês</button>
    <button class="aba{% if aba=='conferencia' %} on{% endif %}" onclick="domAba('conferencia',this)">Conferência</button>
  </div>

  {# ───────── ABA EMPREGADOS ───────── #}
  <section data-aba="empregados" {% if aba!='empregados' %}style="display:none"{% endif %}>
    {% if vinculos %}
      {% for v in vinculos %}
      <div style="display:flex;justify-content:space-between;align-items:center;padding:.6rem 0;border-bottom:1px solid var(--borda)">
        <div><b>{{ v.nome }}</b>{% if v.cargo %} <span style="color:var(--txt-mut)">· {{ v.cargo }}</span>{% endif %}
          <div style="font-size:.8rem;color:var(--txt-mut)">{{ v.salario_centavos|brl }}/mês{% if v.matricula %} · {{ v.matricula }}{% endif %}</div></div>
        <form method="post" action="/painel/domestica/vinculo/{{ v.id }}/desativar" onsubmit="return confirm('Arquivar {{ v.nome }}?')">
          <button class="aba" style="color:#c98">Arquivar</button></form>
      </div>
      {% endfor %}
    {% else %}
      <p style="color:var(--txt-mut)">Nenhum empregado cadastrado ainda.</p>
    {% endif %}

    <form method="post" action="/painel/domestica/vinculo" style="margin-top:1rem">
      <label>Nome</label><input name="nome" required placeholder="ex. Maria da Silva">
      <div style="display:grid;grid-template-columns:1fr 1fr;gap:.6rem">
        <div><label>Salário (R$)</label><input name="salario" required inputmode="decimal" placeholder="1518,00"></div>
        <div><label>Cargo</label><input name="cargo" placeholder="babá, caseiro..."></div>
      </div>
      <div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:.6rem">
        <div><label>CPF</label><input name="cpf" placeholder="opcional"></div>
        <div><label>Matrícula</label><input name="matricula" placeholder="ED001"></div>
        <div><label>Admissão</label><input name="admitido_em" placeholder="dd/mm/aaaa"></div>
      </div>
      <button class="btn-finalizar" type="submit">Cadastrar empregado</button>
    </form>
  </section>

  {# ───────── ABA FOLHA DO MÊS ───────── #}
  <section data-aba="folha" {% if aba!='folha' %}style="display:none"{% endif %}>
    {% if folha.empregados %}
      {% for e in folha.empregados %}
      <div style="padding:.7rem 0;border-bottom:1px solid var(--borda)">
        <div style="display:flex;justify-content:space-between"><b>{{ e.nome }}</b>
          <span>líquido {{ e.liquido_centavos|brl }}</span></div>
        <div style="font-size:.82rem;color:var(--txt-mut);margin-top:.25rem">
          DAE prevista <b style="color:var(--txt)">{{ e.dae_centavos|brl }}</b>
          (vence {{ e.vencimento_dae.strftime('%d/%m/%Y') }}) ·
          FGTS {{ e.fgts_mensal_centavos|brl }} + comp. {{ e.fgts_compensatorio_centavos|brl }} ·
          INSS patr. {{ e.inss_patronal_centavos|brl }} · GILRAT {{ e.gilrat_centavos|brl }} ·
          INSS empr. {{ e.inss_empregado_centavos|brl }} · IRRF {{ e.irrf_centavos|brl }}
          {% if e.abaixo_do_minimo %}<br><span style="color:#e0b34a">⚠️ remuneração abaixo do salário mínimo</span>{% endif %}
        </div>
      </div>
      {% endfor %}
      <div style="margin-top:.9rem;padding:.8rem;background:var(--card-2,rgba(255,255,255,.03));border-radius:10px">
        <div style="display:flex;justify-content:space-between"><span>DAE total</span><b>{{ folha.dae_total_centavos|brl }}</b></div>
        <div style="display:flex;justify-content:space-between"><span>Desembolso do mês</span><b>{{ folha.desembolso_total_centavos|brl }}</b></div>
        <div style="display:flex;justify-content:space-between;color:var(--verde-claro)"><span>Custo real c/ provisões (13º + férias)</span><b>{{ folha.custo_real_total_centavos|brl }}</b></div>
        <div style="font-size:.78rem;color:var(--txt-mut);margin-top:.3rem">A diferença ({{ (folha.custo_real_total_centavos - folha.desembolso_total_centavos)|brl }}) é o que estoura em dezembro/nas férias — por isso entra no seu raio-x.</div>
      </div>
      <form method="post" action="/painel/domestica/fechar" style="margin-top:.8rem"
            onsubmit="return confirm('Fechar a folha e lançar no caixa pessoal?')">
        <button class="btn-finalizar" type="submit">Fechar folha e lançar no caixa</button></form>
    {% else %}
      <p style="color:var(--txt-mut)">Cadastre um empregado pra calcular a folha.</p>
    {% endif %}

    {% if vinculos %}
    <form method="post" action="/painel/domestica/variavel" style="margin-top:1.2rem">
      <div style="font-size:.85rem;color:var(--txt-mut);margin-bottom:.2rem">Lançar variável do mês</div>
      <div style="display:grid;grid-template-columns:1.4fr 1fr 1fr;gap:.6rem">
        <div><label>Empregado</label><select name="vinculo_id" required>
          {% for v in vinculos %}<option value="{{ v.id }}">{{ v.nome }}</option>{% endfor %}
        </select></div>
        <div><label>Tipo</label><select name="tipo">
          <option value="extra">Hora-extra/adicional</option>
          <option value="adiantamento_13">Adiantamento 13º</option>
          <option value="falta">Falta</option>
          <option value="desconto">Desconto</option>
          <option value="beneficio">Benefício (VR/VA)</option>
        </select></div>
        <div><label>Valor (R$)</label><input name="valor" required inputmode="decimal" placeholder="500,00"></div>
      </div>
      <input name="descricao" placeholder="descrição (opcional)" style="margin-top:.5rem">
      <button class="btn-finalizar" type="submit">Lançar variável</button>
    </form>
    {% endif %}
  </section>

  {# ───────── ABA CONFERÊNCIA ───────── #}
  <section data-aba="conferencia" {% if aba!='conferencia' %}style="display:none"{% endif %}>
    <p style="color:var(--txt-mut);font-size:.85rem">Emitiu a guia no portal? Digite o valor da DAE oficial pra bater com o previsto.</p>
    {% if conferido %}
      {% if conferido.bateu %}
        <div class="tag" style="background:rgba(29,158,117,.16);color:var(--verde-claro);display:block;margin:.4rem 0">✅ Bateu certinho: DAE de {{ conferido.oficial_centavos|brl }} confere com o previsto.</div>
      {% else %}
        <div class="tag" style="background:#6e2b2b;color:#fff;display:block;margin:.4rem 0">⚠️ Divergência de {{ conferido.divergencia_centavos|abs|brl }}: previsto {{ conferido.previsto_centavos|brl }} × guia {{ conferido.oficial_centavos|brl }}. Confere as variáveis do mês.</div>
      {% endif %}
    {% endif %}
    {% if vinculos %}
    <form method="post" action="/painel/domestica/conferir" style="margin-top:.6rem">
      <div style="display:grid;grid-template-columns:1.6fr 1fr;gap:.6rem">
        <div><label>Empregado</label><select name="vinculo_id" required>
          {% for v in vinculos %}<option value="{{ v.id }}">{{ v.nome }}</option>{% endfor %}
        </select></div>
        <div><label>DAE da guia (R$)</label><input name="valor_guia" required inputmode="decimal" placeholder="467,36"></div>
      </div>
      <button class="btn-finalizar" type="submit">Conferir DAE</button>
    </form>
    {% else %}
      <p style="color:var(--txt-mut)">Cadastre um empregado primeiro.</p>
    {% endif %}
  </section>
</div>
<script>
function domAba(nome, btn){
  document.querySelectorAll('[data-aba]').forEach(function(s){ s.style.display = (s.dataset.aba===nome)?'':'none'; });
  document.querySelectorAll('.abas .aba').forEach(function(b){ b.classList.remove('on'); });
  btn.classList.add('on');
  history.replaceState(null,'', '/painel/domestica?aba='+nome);
}
</script>
{% endblock %}"""

_env.loader.mapping["domestica"] = _DOMESTICA_TPL
_env.filters.setdefault("abs", abs)   # usado na divergência da conferência
