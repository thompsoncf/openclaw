"""O CARTÃO DE CLÍNICA (entrega 3a): os campos do paciente na ficha do cartão.

Desenho: "Cartão de clínica", aprovado pelo dono em 03/10/2026, com a decisão E (um
cartão por paciente) e a K (o que falta para sair de Em conversa AVISA, não trava).
A tela é a ficha do cartão (web/painel_prospeccao.py) nas contas do nicho clínica;
nas outras nada muda.

O QUE O CARTÃO GUARDA (migração 202610031502)
  paciente      o nome do cartão (`empresa`), o CPF e o nascimento
  outra pessoa  `responsavel_nome` e `responsavel_parentesco`: quem fala pelo WhatsApp
                quando o paciente é outra pessoa (a mãe que marca para o filho)
  cidade        `cidade`, escolhida da lista de Clínica › Locais, ou "outra" escrita
  tipo          `tipo_atendimento`: consulta, procedimento, serviço ou outro
  como chegou   `origem_cliente`, com a lista da clínica (raio_x_dono.ORIGENS_CLINICA)

O SALVAR SÓ MEXE NO QUE ESTÁ NA TELA. O formulário geral da ficha grava todos os
campos e limpa o que vem em branco. Na clínica os campos de festa e de empresa nem
aparecem, e um salvar que os mandasse em branco apagaria o que estivesse lá: é o
defeito do #970 (campo vazio do formulário apagando dado). Aqui a lista de colunas é
fechada, e só ela muda.
"""
from __future__ import annotations

from datetime import date, datetime

from finance import clinica_config as ccfg
from finance import funil_regua as fr
from finance import validadoc
from finance.raio_x_dono import ORIGENS_CLINICA

PERFIL = "clinica"
TIPOS = (
    ("consulta", "Consulta"),
    ("procedimento", "Procedimento"),
    ("servico", "Serviço (vacina, teste, coleta)"),
    ("outro", "Outro"),
)
PARENTESCOS = (
    ("mae", "Mãe"),
    ("pai", "Pai"),
    ("conjuge", "Cônjuge"),
    ("filho", "Filho ou filha"),
    ("avo", "Avó ou avô"),
    ("outro", "Outro"),
)
#: o valor do <select> de cidade que abre o campo "outra cidade"
OUTRA_CIDADE = "_outra"
#: as colunas antes do dia marcado: é nelas que aparece "para sair de Em conversa"
ANTES_DO_DIA = ("novo", "contatado")


def e_clinica(c, conta_id: int) -> bool:
    return fr.perfil_da_conta(c, conta_id) == PERFIL


def cidades(c, conta_id: int) -> list[str]:
    """As cidades atendidas, na ordem de Clínica › Locais (a sede primeiro), sem repetir.
    Dois locais na mesma cidade (duas salas em Codó) viram uma linha só."""
    try:
        with c.transaction():
            locais = ccfg.listar_locais(c, conta_id)
    except Exception:  # noqa: BLE001 — conta sem a 348 não tem lista: o campo fica livre
        return []
    vistas: set[str] = set()
    out: list[str] = []
    for loc in locais:
        cid = (loc.get("cidade") or "").strip()
        if cid and cid.casefold() not in vistas:
            vistas.add(cid.casefold())
            out.append(cid)
    return out


def preco_passado(c, conta_id: int, lead_id: int) -> datetime | None:
    """A primeira mensagem nossa com preço na conversa do cartão. É a mesma leitura
    que a régua faz (`funil_regua.RE_PRECO`), para o cartão e a régua não discordarem
    sobre o que é "passar o preço"."""
    try:
        with c.transaction():
            r = c.execute(
                """select min(m.criado_em) from mensagens m
                     join conversas cv on cv.id = m.conversa_id
                    where cv.conta_id=%s and cv.prospeccao_id=%s
                      and m.direcao='out' and m.texto ~* %s""",
                (conta_id, lead_id, fr.RE_PRECO)).fetchone()
    except Exception:  # noqa: BLE001 — banco sem conversas: a linha só não aparece
        return None
    return r[0] if r else None


def _ler(c, conta_id: int, lead_id: int) -> dict:
    r = c.execute(
        """select tipo_atendimento, responsavel_nome, responsavel_parentesco
             from prospeccao where id=%s and conta_id=%s""", (lead_id, conta_id)).fetchone()
    tipo, resp, parent = r if r else (None, None, None)
    return {"tipo_atendimento": tipo, "responsavel_nome": resp, "responsavel_parentesco": parent}


def falta(status: str | None, cidade: str, tipo: str | None, origem: str | None,
          preco_em: datetime | None) -> dict | None:
    """O que falta para o cartão sair de Em conversa (decisão K: aviso, não trava).
    Só nas colunas antes do dia marcado; depois disso a pergunta já foi respondida."""
    if status not in ANTES_DO_DIA:
        return None
    tipos, origens = dict(TIPOS), dict(ORIGENS_CLINICA)
    linhas = [
        {"rotulo": "Cidade", "ok": bool(cidade), "texto": cidade or "falta"},
        {"rotulo": "Tipo de atendimento", "ok": bool(tipo),
         "texto": tipos.get(tipo or "", "") or "falta"},
        {"rotulo": "Como chegou", "ok": bool(origem),
         "texto": origens.get(origem or "", "") or "falta"},
    ]
    if not cidade:
        proxima = "perguntar a cidade"
    elif not tipo:
        proxima = "perguntar o que a pessoa procura"
    elif not preco_em:
        proxima = "passar o preço"
    else:
        proxima = f"oferecer uma data em {cidade}"
    return {"linhas": linhas, "preco_em": preco_em, "proxima": proxima,
            "completo": all(x["ok"] for x in linhas)}


def contexto(c, conta_id: int, alvo: dict) -> dict:
    """Tudo o que a ficha da clínica mostra além do que a ficha geral já carrega."""
    extra = _ler(c, conta_id, alvo["id"])
    lista = cidades(c, conta_id)
    cidade = (alvo.get("cidade") or "").strip()
    na_lista = next((x for x in lista if x.casefold() == cidade.casefold()), None)
    nasc = alvo.get("nascimento")
    tipos, parentescos, origens = dict(TIPOS), dict(PARENTESCOS), dict(ORIGENS_CLINICA)
    return {
        **extra,
        "outra_pessoa": bool(extra["responsavel_nome"]),
        "parentesco_rot": parentescos.get(extra["responsavel_parentesco"] or "", ""),
        "tipo_rot": tipos.get(extra["tipo_atendimento"] or "", ""),
        "origem_rot": origens.get(alvo.get("origem_cliente") or "", ""),
        "cidades": lista,
        # a cidade gravada fora da lista (escrita antes da lista existir, ou "outra")
        # continua aparecendo: o select abre em "outra" com ela escrita
        "cidade_sel": na_lista or (OUTRA_CIDADE if cidade else ""),
        "cidade_outra": "" if na_lista else cidade,
        "nascimento_iso": nasc.isoformat() if isinstance(nasc, date) else "",
        "idade": _idade(nasc),
        "falta": falta(alvo.get("status"), cidade, extra["tipo_atendimento"],
                       alvo.get("origem_cliente"), preco_passado(c, conta_id, alvo["id"])),
        "tipos": TIPOS, "parentescos": PARENTESCOS, "origens": ORIGENS_CLINICA,
        "outra_cidade": OUTRA_CIDADE,
    }


def _idade(nasc, hoje: date | None = None) -> int | None:
    if not isinstance(nasc, date):
        return None
    from finance import relogio
    hoje = hoje or relogio.hoje()
    return hoje.year - nasc.year - ((hoje.month, hoje.day) < (nasc.month, nasc.day))


def _data(s: str) -> date | None | str:
    s = (s or "").strip()
    if not s:
        return None
    try:
        return date.fromisoformat(s)
    except ValueError:
        return "erro"


def salvar(c, conta_id: int, lead_id: int, f: dict) -> str | None:
    """Grava a ficha da clínica. Devolve o erro, ou None. Só as colunas desta tela
    mudam; em branco limpa (o campo está na tela), menos o nome, que em branco
    fica como está."""
    nome = (f.get("empresa") or "").strip()
    # o documento da clínica é o CPF do paciente
    achado, digitos = validadoc.classificar((f.get("documento") or "").strip())
    if digitos and achado != "pf":
        return "Na clínica o documento é o CPF do paciente (11 números)."
    if digitos and not validadoc.valida_cpf(digitos):
        return "Esse CPF não existe: confira os números."
    nasc = _data(f.get("nascimento") or "")
    if nasc == "erro":
        return "Data de nascimento inválida."
    if isinstance(nasc, date):
        from finance import relogio
        if nasc > relogio.hoje():
            return "A data de nascimento está no futuro."
    outra = (f.get("quem") or "") == "outro"
    resp = (f.get("responsavel_nome") or "").strip() if outra else ""
    parent = (f.get("responsavel_parentesco") or "") if outra else ""
    if outra and not resp:
        return "O paciente é outra pessoa: diga quem fala pelo WhatsApp (o responsável)."
    if parent and parent not in dict(PARENTESCOS):
        parent = ""
    cid_sel = (f.get("cidade") or "").strip()
    cidade = (f.get("cidade_outra") or "").strip() if cid_sel == OUTRA_CIDADE else cid_sel
    tipo = f.get("tipo_atendimento") or ""
    tipo = tipo if tipo in dict(TIPOS) else None
    origem = f.get("origem_cliente") or ""
    origem = origem if origem in dict(ORIGENS_CLINICA) else None
    # o paciente é pessoa física; um cartão antigo com CNPJ (cadastro de empresa) só
    # vira PF quando ganha um CPF, para o CNPJ não ficar escondido numa ficha de PF
    c.execute(
        """update prospeccao
              set empresa = coalesce(nullif(%s, ''), empresa),
                  tipo = case when %s::text is not null or cnpj is null then 'pf' else tipo end,
                  cpf=%s, nascimento=%s, responsavel_nome=%s, responsavel_parentesco=%s,
                  whatsapp=%s, telefone=%s, email=%s, instagram=%s,
                  cidade=%s, tipo_atendimento=%s, origem_cliente=%s, obs=%s,
                  atualizado_em=now()
            where id=%s and conta_id=%s""",
        (nome, digitos or None, digitos or None, nasc, resp or None, parent or None,
         (f.get("whatsapp") or "").strip() or None, (f.get("telefone") or "").strip() or None,
         (f.get("email") or "").strip().lower() or None, (f.get("instagram") or "").strip() or None,
         cidade[:80] or None, tipo, origem, (f.get("obs") or "").strip() or None,
         lead_id, conta_id))
    return None
