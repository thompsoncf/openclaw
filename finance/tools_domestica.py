"""Persona e ferramentas do Módulo Doméstica (eSocial Doméstico) — lado PF.

Plugado SÓ quando a conta tem o módulo doméstica ativo (gate por conta_modulos).
Não altera a persona PF nem o núcleo: ADICIONA um bloco ao prompt e ferramentas
à lista, no mesmo molde do tools_pj.py — sem importar de empresa.py.

Honestidade de copy (igual "não substitui o contador" do PJ): é CÁLCULO DE
CONFERÊNCIA. Não há API no eSocial Doméstico — o fechamento continua no portal
gov.br; aqui a gente antecipa a DAE, confere a guia e lança no livro-caixa.
"""
from __future__ import annotations

from datetime import date

from core.agent import Ferramenta
from .models import formatar_brl
from . import domestica_repo as dom
from .domestica import texto_resumo


def _reais_para_centavos(valor) -> int:
    return int(round(float(valor) * 100))


def _acha_vinculo(pool, conta_id: int, nome: str) -> dict | None:
    """Casa um vínculo pelo nome (parcial, sem caixa). None se ambíguo/ausente."""
    alvo = (nome or "").strip().casefold()
    if not alvo:
        return None
    vs = dom.listar_vinculos(pool, conta_id, so_ativos=True)
    exatos = [v for v in vs if v["nome"].casefold() == alvo]
    if exatos:
        return exatos[0]
    cand = [v for v in vs if alvo in v["nome"].casefold()]
    return cand[0] if len(cand) == 1 else None


# ─────────────────────────────────────────────────────────────────────────
# Persona: bloco anexado ao prompt quando o módulo doméstica está ativo
# ─────────────────────────────────────────────────────────────────────────
def bloco_persona_domestica(pool, conta_id: int) -> str:
    try:
        vinc = dom.listar_vinculos(pool, conta_id, so_ativos=True)
    except Exception:
        vinc = []
    nomes = ", ".join(f"{v['nome']} ({v['cargo'] or 'doméstica'})"
                      for v in vinc[:6]) or "nenhum cadastrado"
    return (
        "\n\n[MÓDULO DOMÉSTICA — eSocial Doméstico]\n"
        "A conta emprega em casa (empregado doméstico da LC 150/2015: babá, "
        "caseiro, cuidador, motorista particular, cozinheira, jardineiro, "
        f"diarista mensalista...). Empregados: {nomes}.\n"
        "Você AJUDA com o cálculo de CONFERÊNCIA da folha doméstica: antecipa a "
        "DAE do mês, confere a guia que o dono emitiu no portal e lança o custo "
        "no livro-caixa PESSOAL. IMPORTANTE, seja honesto: NÃO existe API no "
        "eSocial Doméstico e você NÃO transmite nada — o fechamento continua no "
        "portal gov.br. Você é a calculadora de conferência, não o portal.\n"
        "Mostre sempre, além do desembolso, o CUSTO REAL com provisões (13º e "
        "férias) — é o que o dono só sente em dezembro."
    )


# ─────────────────────────────────────────────────────────────────────────
# Ferramentas
# ─────────────────────────────────────────────────────────────────────────
def construir_ferramentas_domestica(pool, conta_id: int,
                                    membro_id: int | None = None) -> list:
    valor_s = {"type": "number", "description": "valor em reais, ex 1500.50"}

    def cadastrar_vinculo(e: dict) -> str:
        nome = (e.get("nome") or "").strip()
        salario = e.get("salario")
        if not nome or salario is None:
            return "Preciso do nome e do salário pra cadastrar o empregado."
        adm = None
        if e.get("admitido_em"):
            try:
                adm = date(*reversed([int(x) for x in
                                      str(e["admitido_em"]).split("/")]))
            except Exception:
                adm = None
        dom.cadastrar_vinculo(
            pool, conta_id, nome, _reais_para_centavos(salario),
            cargo=(e.get("cargo") or "").strip(),
            cpf=(e.get("cpf") or "").strip(),
            matricula=(e.get("matricula") or "").strip(), admitido_em=adm)
        return (f"Empregado '{nome}' cadastrado (salário "
                f"{formatar_brl(_reais_para_centavos(salario))}). ✅ "
                "Agora dá pra lançar as variáveis do mês e ver a DAE prevista.")

    def lancar_variaveis_mes(e: dict) -> str:
        nome = (e.get("empregado") or "").strip()
        tipo = (e.get("tipo") or "").strip()
        valor = e.get("valor")
        if not nome or not tipo or valor is None:
            return ("Preciso do empregado, do tipo (extra/adiantamento_13/falta/"
                    "desconto/beneficio) e do valor.")
        v = _acha_vinculo(pool, conta_id, nome)
        if not v:
            return f"Não achei '{nome}' entre os empregados. Confere o nome?"
        try:
            dom.lancar_item(pool, conta_id, v["id"], tipo,
                            _reais_para_centavos(valor),
                            descricao=(e.get("descricao") or "").strip())
        except ValueError:
            return ("Tipo inválido. Use: extra, adiantamento_13, falta, "
                    "desconto ou beneficio.")
        return (f"Lancei {tipo} de {formatar_brl(_reais_para_centavos(valor))} "
                f"pro {v['nome']} neste mês. ✅")

    def mostrar_dae_prevista(e: dict) -> str:
        folha = dom.calcular_competencia(pool, conta_id)
        if not folha["empregados"]:
            return ("Nenhum empregado doméstico cadastrado ainda. "
                    "Cadastre um pra eu calcular a DAE.")
        return texto_resumo(folha)

    def fechar_folha(e: dict) -> str:
        folha = dom.calcular_competencia(pool, conta_id)
        if not folha["empregados"]:
            return "Não há empregado pra fechar a folha."
        r = dom.fechar_folha(pool, conta_id, membro_id=membro_id)
        return (f"Folha doméstica fechada e lançada no caixa pessoal: "
                f"{formatar_brl(r['total_caixa_centavos'])} de desembolso "
                f"(DAE {formatar_brl(r['dae_total_centavos'])}). "
                f"Custo real com provisões: "
                f"{formatar_brl(r['custo_real_total_centavos'])}. "
                "Lembrando: a guia oficial você emite/paga no portal gov.br.")

    def conferir_dae_oficial(e: dict) -> str:
        nome = (e.get("empregado") or "").strip()
        valor = e.get("valor_guia")
        if not nome or valor is None:
            return "Preciso do empregado e do valor da DAE da guia oficial."
        v = _acha_vinculo(pool, conta_id, nome)
        if not v:
            return f"Não achei '{nome}' entre os empregados. Confere o nome?"
        r = dom.conferir_dae(pool, conta_id, v["id"],
                             _reais_para_centavos(valor))
        if not r.get("ok"):
            return f"Não consegui conferir: {r.get('erro')}"
        if r["bateu"]:
            return (f"✅ Bateu certinho: DAE de {formatar_brl(r['oficial_centavos'])} "
                    f"pro {v['nome']} confere com o previsto.")
        dif = r["divergencia_centavos"]
        sinal = "a mais" if dif > 0 else "a menos"
        return (f"⚠️ Divergência: a guia veio {formatar_brl(abs(dif))} {sinal} "
                f"que o previsto ({formatar_brl(r['previsto_centavos'])} previsto × "
                f"{formatar_brl(r['oficial_centavos'])} na guia). "
                "Vale conferir as variáveis do mês (extra, falta, adiantamento).")

    return [
        Ferramenta(
            nome="cadastrar_vinculo_domestico",
            descricao=("Cadastra um empregado doméstico (babá, caseiro, cuidador, "
                       "motorista particular, cozinheira, etc.). Use depois de "
                       "confirmar nome e salário com o dono."),
            parametros={
                "type": "object",
                "properties": {
                    "nome": {"type": "string"},
                    "salario": valor_s,
                    "cargo": {"type": "string", "description": "babá, caseiro... (opcional)"},
                    "cpf": {"type": "string", "description": "opcional"},
                    "matricula": {"type": "string", "description": "ED001... (opcional)"},
                    "admitido_em": {"type": "string", "description": "dd/mm/aaaa (opcional)"},
                },
                "required": ["nome", "salario"],
            },
            executar=cadastrar_vinculo,
        ),
        Ferramenta(
            nome="lancar_variaveis_domestica",
            descricao=("Lança uma variável do mês de um empregado doméstico: "
                       "extra (hora-extra/adicional), adiantamento_13 (1ª parcela "
                       "do 13º), falta, desconto ou beneficio (VR/VA)."),
            parametros={
                "type": "object",
                "properties": {
                    "empregado": {"type": "string"},
                    "tipo": {"type": "string",
                             "enum": ["extra", "adiantamento_13", "falta",
                                      "desconto", "beneficio"]},
                    "valor": valor_s,
                    "descricao": {"type": "string", "description": "opcional"},
                },
                "required": ["empregado", "tipo", "valor"],
            },
            executar=lancar_variaveis_mes,
        ),
        Ferramenta(
            nome="mostrar_dae_prevista",
            descricao=("Mostra a DAE prevista do mês (cálculo de conferência) de "
                       "todos os empregados domésticos: líquido, tributos, "
                       "vencimento, desembolso e custo real com provisões."),
            parametros={"type": "object", "properties": {}},
            executar=mostrar_dae_prevista,
        ),
        Ferramenta(
            nome="fechar_folha_domestica",
            descricao=("Fecha a folha doméstica do mês e lança o custo no "
                       "livro-caixa PESSOAL (líquido + adiantamento 13º + DAE). "
                       "A guia oficial continua sendo emitida/paga no portal."),
            parametros={"type": "object", "properties": {}},
            executar=fechar_folha,
        ),
        Ferramenta(
            nome="conferir_dae_oficial",
            descricao=("Confere o valor da DAE da guia oficial (emitida no portal) "
                       "contra o previsto pelo cálculo, apontando divergência."),
            parametros={
                "type": "object",
                "properties": {
                    "empregado": {"type": "string"},
                    "valor_guia": {"type": "number",
                                   "description": "valor total da DAE na guia, em reais"},
                },
                "required": ["empregado", "valor_guia"],
            },
            executar=conferir_dae_oficial,
        ),
    ]
