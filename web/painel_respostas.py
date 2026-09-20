"""A tela das RESPOSTAS RÁPIDAS DA EQUIPE: /painel/respostas.

POR QUE EXISTE, e por que no painel e não só no app. As respostas da equipe são o
que a empresa diz ao cliente — a abertura, o que está incluso, o que a Prime não
faz. Quem escreve isso é o dono, e escrever 300 caracteres no teclado do celular é
o que faz ninguém escrever. Aqui ele senta no computador e resolve.

O QUE ESTA TELA NÃO MOSTRA, de propósito: o texto das respostas PESSOAIS de cada
vendedor. A tela do app promete, com estas palavras, "as sem selo são suas: só
você vê" — e promessa de privacidade que o dono fura pelo painel não era
promessa. O que aparece aqui é a CONTAGEM por pessoa, que é o que responde "a
equipe está usando isto?".

As variáveis (`{nome}`, `{vendedor}`, `{empresa}`) são trocadas na hora em que o
vendedor toca, dentro da conversa — ver `finance/respostas_rapidas.aplicar`.
"""
from __future__ import annotations

from fastapi import APIRouter, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse

from db.conexao import get_pool
from finance import respostas_rapidas as rr
from web.portal import _env, _render, conta_logada

router = APIRouter()


def _quem_gere(request: Request):
    """Dono ou gestor. Devolve (conta, membro_id, None) ou (None, None, redirect).

    Gestor entra porque ele responde pela equipe no dia a dia; vendedor não, pelo
    mesmo motivo do cadeado no app: a resposta da equipe é a mesma pros quatro, e
    apagá-la some com ela pra todo mundo.
    """
    conta = conta_logada(request)
    if conta is None:
        return None, None, RedirectResponse("/login", status_code=303)
    if (request.session.get("papel") or "dono") not in ("dono", "gestor"):
        return None, None, RedirectResponse("/painel", status_code=303)
    return conta, request.session.get("membro_id"), None


@router.get("/painel/respostas", response_class=HTMLResponse)
def painel_respostas(request: Request, erro: str = "", ok: str = ""):
    conta, _membro, fora = _quem_gere(request)
    if fora is not None:
        return fora
    pool = get_pool()
    equipe = rr.da_equipe(pool, conta[0])
    contagem = rr.contagem_por_membro(pool, conta[0])
    with pool.connection() as c:
        pessoas = c.execute(
            """select id, coalesce(nullif(nome,''), email, 'Sem nome'), papel
                 from membros where conta_id=%s and ativo order by nome""",
            (conta[0],)).fetchall()
    vendedores = [{"nome": p[1], "papel": p[2], "quantas": contagem.get(p[0], 0)}
                  for p in pessoas]
    return _render("respostas", request, titulo="Respostas rápidas", tem_pj=True,
                   secao_ativa="respostas", equipe=equipe, vendedores=vendedores,
                   erro=erro, ok=ok, teto=rr.TETO_POR_DONO)


@router.post("/painel/respostas/nova")
def painel_respostas_nova(request: Request, texto: str = Form(""), titulo: str = Form("")):
    conta, membro, fora = _quem_gere(request)
    if fora is not None:
        return fora
    r = rr.criar(get_pool(), conta[0], membro or 0, texto, titulo=titulo, da_equipe=True)
    if not r.get("ok"):
        recado = {"vazio": "Escreva a mensagem antes de guardar.",
                  "cheio": f"A lista da equipe chegou ao teto ({rr.TETO_POR_DONO})."}
        return RedirectResponse(
            "/painel/respostas?erro=" + recado.get(r.get("erro"), "Não deu pra guardar."),
            status_code=303)
    if r.get("repetida"):
        return RedirectResponse("/painel/respostas?ok=Essa frase já estava na lista.",
                                status_code=303)
    return RedirectResponse("/painel/respostas?ok=Guardada — a equipe já vê.",
                            status_code=303)


@router.post("/painel/respostas/apagar")
def painel_respostas_apagar(request: Request, resposta_id: int = Form(...)):
    conta, membro, fora = _quem_gere(request)
    if fora is not None:
        return fora
    rr.apagar(get_pool(), conta[0], membro or 0, resposta_id, manda_na_conta=True)
    return RedirectResponse("/painel/respostas?ok=Apagada.", status_code=303)


_TPL = r"""{% extends "base" %}{% block conteudo %}
<style>
.rr-topo{max-width:70ch}
.rr-topo p{color:var(--txt-mut);font-size:.9rem;line-height:1.55}
.rr-nova{background:var(--card);border:1px solid var(--borda);border-radius:14px;
  padding:.9rem 1rem;margin:1rem 0 1.4rem;max-width:70ch}
.rr-nova label{display:block;font-size:.78rem;color:var(--txt-mut);margin:.2rem 0 .25rem}
.rr-nova input,.rr-nova textarea{width:100%;background:var(--bg);border:1px solid var(--borda);
  border-radius:9px;padding:.55rem .7rem;color:var(--txt);font-family:inherit;font-size:.9rem}
.rr-nova textarea{min-height:7.5rem;resize:vertical;line-height:1.5}
.rr-nova .bt{margin-top:.7rem;background:var(--verde);color:var(--sobre-verde);border:0;
  border-radius:9px;padding:.5rem 1.1rem;font-size:.88rem;font-weight:700;cursor:pointer}
.rr-dica{font-size:.76rem;color:var(--txt-mut);margin-top:.5rem;line-height:1.5}
.rr-dica code{background:var(--bg);border:1px solid var(--borda);border-radius:5px;
  padding:0 .3rem;font-size:.74rem}
.rr-lista{display:grid;gap:.6rem;max-width:70ch}
.rr-it{background:var(--card);border:1px solid var(--borda);border-radius:12px;
  padding:.7rem .9rem;display:grid;grid-template-columns:1fr auto;gap:.7rem;align-items:start}
.rr-it h3{margin:0 0 .2rem;font-size:.95rem}
.rr-it pre{margin:0;white-space:pre-wrap;word-break:break-word;font-family:inherit;
  font-size:.84rem;color:var(--txt-mut);line-height:1.5}
.rr-usos{font-size:.72rem;color:var(--txt-mut);margin-top:.4rem;display:block}
.rr-del{background:none;border:1px solid var(--borda);border-radius:9px;color:var(--txt-mut);
  padding:.35rem .6rem;font-size:.76rem;cursor:pointer}
.rr-del:hover{border-color:var(--coral-borda,#6b3b34);color:#e0574f}
.rr-vazio{background:var(--card);border:1px dashed var(--borda);border-radius:12px;
  padding:1.1rem;color:var(--txt-mut);font-size:.88rem;line-height:1.6;max-width:70ch}
.rr-pessoas{margin:1.6rem 0 0;max-width:70ch}
.rr-pessoas table{width:100%;border-collapse:collapse;font-size:.86rem}
.rr-pessoas td{padding:.4rem 0;border-top:1px solid var(--borda)}
.rr-pessoas td:last-child{text-align:right;color:var(--txt-mut)}
.rr-aviso{font-size:.76rem;color:var(--txt-mut);line-height:1.55;margin-top:.5rem}
.rr-msg{border-radius:10px;padding:.55rem .8rem;font-size:.85rem;margin-bottom:1rem;max-width:70ch}
.rr-msg.ok{background:var(--neon-fundo);border:1px solid var(--neon-borda)}
.rr-msg.err{background:#2a1512;border:1px solid #6b3b34;color:#f0b8b2}
</style>

<div class="rr-topo">
  <h2>Respostas rápidas da equipe</h2>
  <p>São as frases que todo vendedor vê no ⚡ dentro da conversa: a abertura, o que
  está incluso, o que a empresa não faz. Ele toca e o texto entra na caixa — nada é
  enviado sem ele conferir.</p>
</div>

{% if ok %}<div class="rr-msg ok">{{ ok }}</div>{% endif %}
{% if erro %}<div class="rr-msg err">{{ erro }}</div>{% endif %}

<form class="rr-nova" method="post" action="/painel/respostas/nova">
  <label for="rr-t">Título (opcional — some se ficar vazio)</label>
  <input id="rr-t" name="titulo" maxlength="60" placeholder="Ex.: Abertura do atendimento">
  <label for="rr-x">A mensagem</label>
  <textarea id="rr-x" name="texto" required
            placeholder="Olá, tudo bem? Me chamo {vendedor} e faço parte da equipe da {empresa}…"></textarea>
  <button class="bt" type="submit">Guardar pra equipe</button>
  <div class="rr-dica">Você pode usar <code>{nome}</code> (primeiro nome do cliente),
  <code>{vendedor}</code> e <code>{empresa}</code>. Eles são trocados na hora em que
  o vendedor toca — a mesma frase chega com o nome de quem está atendendo.</div>
</form>

<div class="rr-lista">
{% for r in equipe %}
  <div class="rr-it">
    <div>
      <h3>{{ r.titulo or 'Sem título' }}</h3>
      <pre>{{ r.texto }}</pre>
      <span class="rr-usos">{% if r.usos %}usada {{ r.usos }}x{% else %}ainda não usada{% endif %}</span>
    </div>
    <form method="post" action="/painel/respostas/apagar"
          onsubmit="return confirm('Apagar esta resposta? Ela some para todos os vendedores.')">
      <input type="hidden" name="resposta_id" value="{{ r.id }}">
      <button class="rr-del" type="submit">Apagar</button>
    </form>
  </div>
{% else %}
  <div class="rr-vazio">Nenhuma resposta da equipe ainda.<br>
  Escreva a primeira acima — a abertura do atendimento costuma ser a que mais se
  repete no dia.</div>
{% endfor %}
</div>

<div class="rr-pessoas">
  <h3>As pessoais de cada um</h3>
  <table>
    {% for v in vendedores %}
    <tr><td>{{ v.nome }}{% if v.papel != 'vendedor' %} <span style="color:var(--txt-mut);font-size:.78rem">({{ v.papel }})</span>{% endif %}</td>
        <td>{{ v.quantas }} {% if v.quantas == 1 %}resposta{% else %}respostas{% endif %}</td></tr>
    {% endfor %}
  </table>
  <p class="rr-aviso">Aqui aparece só a contagem. O que cada vendedor guarda pra si
  não é mostrado nem pro dono — é o que o app promete a ele na mesma tela
  ("as sem selo são suas: só você vê").</p>
</div>
{% endblock %}"""

_env.loader.mapping["respostas"] = _TPL
