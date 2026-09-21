"""A cotação de seguro — o que acontece ANTES de a apólice existir (migração 304).

O PEDIDO (dono, 21/09/2026): "quero implementar uma API pra Liberal Seguros pra
cotação de seguros e, caso tenha, o envio de dados pra apólice por seguradora."

ONDE ISTO SE ENCAIXA. `finance/apolices.py` começa na PROPOSTA — o papel que a
seguradora já emitiu. Este módulo é o passo anterior: o risco que o cliente
passou, os preços que voltaram, a oferta que ele escolheu. Quando ele escolhe,
`virar_proposta` cria a linha em `apolices` com situação 'proposta' — que já era o
primeiro estado do ciclo lá desde o primeiro dia. Nada é redigitado, e a carteira
continua sendo a única dona da apólice.

AS QUATRO DECISÕES DO DONO (21/09/2026), que este módulo implementa:

1. **Camada agnóstica primeiro.** O conector (InsureMO, Segfy, Quiver, TEx ou
   seguradora direta) é um arquivo em `cotacao_provedores.py`; nada aqui o
   conhece. O motivo é que o contrato com o provedor ainda não existe — e uma
   camada modelada no formato do primeiro provedor teria que ser reescrita no dia
   em que ele trocasse.
2. **Três portas de entrada**, porque a corretora trabalha nas três: a tela do
   corretor (`web/painel_cotacao.py`), a API que o site dela chama
   (`web/api_cotacao.py`) e o WhatsApp (a ferramenta em `finance/tools_pj.py`).
   As três chamam as funções deste arquivo — uma segunda implementação seria uma
   segunda verdade sobre o mesmo preço.
3. **Emissão vai só até a PROPOSTA, com fallback.** Onde o provedor aceita
   receber os dados, manda e guarda o número da proposta; onde não aceita —
   que é o caso de toda seguradora brasileira hoje pro corretor — `roteiro_do_portal`
   devolve o resumo pronto pra digitar no portal dela. Fallback não é desistência:
   é o que faz o recurso servir hoje, no mundo em que a API não existe.
4. **Começa por auto**, o forte da Liberal e o único ramo com formulário na
   carteira. `risco` é jsonb: os outros ramos entram sem migração, com formulário
   próprio, quando a corretora pedir.

O CUIDADO COM O PRÊMIO, QUE JÁ CUSTOU UM ERRO DE CONTA. Em `apolices`,
`premio_centavos` é SEMPRE o líquido (sem IOF), porque a comissão incide sobre o
líquido. Aqui a oferta guarda os dois separados — e quando o provedor manda só o
total, `premio_liquido_centavos` fica NULO. `comissao_estimada` então devolve
None, e a tela diz que não dá pra estimar. É a resposta honesta: rachar o total
por um IOF chutado seria inventar comissão, que é exatamente o erro que o mockup
das apólices cometeu (R$ 817,71 onde o certo era R$ 761,51).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, ROUND_HALF_UP

from . import apolices as _ap
from . import validadoc as _doc

_log = logging.getLogger("openclaw.cotacao")

#: Os estados de uma cotação. Espelham o check da migração 304 — o teste
#: `test_o_banco_e_o_python_conhecem_as_mesmas_situacoes` cobra os dois juntos.
SITUACOES = (
    ("rascunho", "Rascunho"),      # o risco está gravado, ninguém cotou ainda
    ("cotada", "Cotada"),          # voltou pelo menos uma oferta
    ("falhou", "Falhou"),          # o provedor recusou ou caiu — e o porquê ficou em `erro`
    ("escolhida", "Escolhida"),    # o corretor escolheu uma oferta
    ("proposta", "Virou proposta"),  # nasceu a apólice em situação 'proposta'
    ("perdida", "Perdida"),        # o cliente não fechou
)
_SITUACOES = dict(SITUACOES)

#: De onde a cotação entrou. São as três portas da decisão 2.
ORIGENS = (("painel", "Painel"), ("api", "API"), ("whatsapp", "WhatsApp"))
_ORIGENS = dict(ORIGENS)

#: Pra que o carro é usado. Muda o preço em toda seguradora, e é a pergunta que o
#: corretor faz sem pensar — por isso ela está no risco mínimo e não no "extra".
USOS_AUTO = (("particular", "Particular"), ("comercial", "Comercial"),
             ("app", "Aplicativo (Uber/99)"), ("taxi", "Táxi"))

#: O que a garagem responde. Três estados, não dois: "tem garagem em casa" e "tem
#: no trabalho" são descontos diferentes, e juntar os dois num booleano perde o
#: que a seguradora pergunta.
GARAGENS = (("nao", "Não"), ("residencia", "Na residência"),
            ("residencia_trabalho", "Residência e trabalho"))


def rotulo_situacao(chave: str | None) -> str:
    return _SITUACOES.get((chave or "").strip().lower(), (chave or "").strip() or "—")


def rotulo_origem(chave: str | None) -> str:
    return _ORIGENS.get((chave or "").strip().lower(), (chave or "").strip() or "—")


# ───────────────────────────────────────────────────────────────── a oferta

@dataclass
class Oferta:
    """Um preço de uma seguradora. É o que todo provedor tem que devolver.

    `premio_liquido_centavos` e `iof_centavos` são opcionais DE PROPÓSITO (ver o
    cabeçalho do módulo): provedor que manda só o total deixa os dois nulos, e a
    comissão fica sem estimativa em vez de sair errada.
    """
    seguradora: str
    premio_total_centavos: int
    produto: str = ""
    premio_liquido_centavos: int | None = None
    iof_centavos: int | None = None
    franquia_centavos: int | None = None
    comissao_pct: Decimal | None = None
    parcelas: int | None = None
    coberturas: list = field(default_factory=list)
    validade: date | None = None
    ref_externa: str | None = None
    bruto: dict = field(default_factory=dict)

    def __post_init__(self):
        self.seguradora = (self.seguradora or "").strip()
        if not self.seguradora:
            raise ValueError("oferta sem seguradora")
        self.premio_total_centavos = int(self.premio_total_centavos or 0)
        if self.premio_total_centavos <= 0:
            raise ValueError(f"oferta da {self.seguradora} sem prêmio total")
        # o líquido só é aceito junto com o IOF: metade da separação não separa
        # nada, e um líquido sozinho seria lido como "o IOF é zero".
        if self.premio_liquido_centavos is not None and self.iof_centavos is None:
            self.iof_centavos = self.premio_total_centavos - int(self.premio_liquido_centavos)

    def comissao_estimada(self, pct=None) -> int | None:
        """A comissão em centavos — sobre o LÍQUIDO, nunca sobre o total com IOF.

        Devolve None quando o líquido não é conhecido, e é isso que impede o erro
        de conta do mockup das apólices de voltar por outro caminho.
        """
        p = pct if pct is not None else self.comissao_pct
        if p is None or self.premio_liquido_centavos is None:
            return None
        base = Decimal(int(self.premio_liquido_centavos))
        v = (base * Decimal(str(p)) / Decimal(100)).quantize(Decimal(1), rounding=ROUND_HALF_UP)
        return int(v)


class CotacaoErro(Exception):
    """O risco não serve, ou o provedor não deu preço. A mensagem vai pra tela."""


# ─────────────────────────────────────────────────────── o risco (auto)

def _digitos(v) -> str:
    return "".join(ch for ch in str(v or "") if ch.isdigit())


def _data(v):
    """Aceita date, 'AAAA-MM-DD' e 'DD/MM/AAAA' — é o que chega das três portas."""
    if v is None or v == "":
        return None
    if hasattr(v, "toordinal"):
        return v if not isinstance(v, datetime) else v.date()
    s = str(v).strip()
    for f in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, f).date()
        except ValueError:
            continue
    return None


def normalizar_placa(v) -> str:
    """Tira pontuação e sobe pra maiúscula. Aceita o padrão antigo (ABC1234) e o
    Mercosul (ABC1D23) — e não recusa o que não casar: placa mal digitada não pode
    impedir a cotação de existir, o provedor é quem diz se serve."""
    s = "".join(ch for ch in str(v or "") if ch.isalnum()).upper()
    return s[:7]


def normalizar_risco(dados: dict, ramo: str = "auto") -> dict:
    """Valida o mínimo e devolve o risco limpo, pronto pra ir pro provedor.

    O MÍNIMO É O QUE TODA SEGURADORA PERGUNTA — não é lista de desejo. Sem CPF,
    sem data de nascimento, sem CEP de pernoite e sem identificação do veículo,
    nenhum provedor no Brasil dá preço; recusar aqui é devolver o erro em uma
    linha em vez de gastar uma chamada de rede pra receber o mesmo não.

    O veículo pode vir por TRÊS caminhos, e qualquer um serve: código FIPE (o
    melhor, é o que o provedor quer), placa (o provedor consulta) ou
    marca/modelo + ano. Exigir os três seria exigir que o corretor fizesse o
    trabalho que a API faz.
    """
    ramo = (ramo or "auto").strip().lower()
    if ramo != "auto":
        # a tabela aguenta qualquer ramo (jsonb); o que não existe ainda é o
        # formulário e a validação dele. Melhor recusar do que fingir que valida.
        raise CotacaoErro(f"por enquanto só cotamos auto — {ramo} ainda não tem formulário")

    d = dados or {}
    cpf = _digitos(d.get("cpf"))
    if not _doc.valida_cpf(cpf):
        raise CotacaoErro("CPF do segurado inválido ou faltando")
    nasc = _data(d.get("nascimento"))
    if nasc is None:
        raise CotacaoErro("data de nascimento do segurado é obrigatória")
    if nasc >= date.today():
        raise CotacaoErro("data de nascimento no futuro")
    cep = _digitos(d.get("cep"))
    if len(cep) != 8:
        raise CotacaoErro("CEP de pernoite é obrigatório (8 dígitos)")

    fipe = str(d.get("fipe") or "").strip()
    placa = normalizar_placa(d.get("placa"))
    marca_modelo = str(d.get("marca_modelo") or "").strip()
    ano_modelo = d.get("ano_modelo")
    try:
        ano_modelo = int(ano_modelo) if ano_modelo not in (None, "") else None
    except (TypeError, ValueError):
        ano_modelo = None
    if not fipe and not placa and not (marca_modelo and ano_modelo):
        raise CotacaoErro("identifique o veículo: código FIPE, placa ou marca/modelo + ano")

    uso = str(d.get("uso") or "particular").strip().lower()
    if uso not in dict(USOS_AUTO):
        uso = "particular"
    garagem = str(d.get("garagem") or "nao").strip().lower()
    if garagem not in dict(GARAGENS):
        garagem = "nao"
    try:
        bonus = int(d.get("bonus") or 0)
    except (TypeError, ValueError):
        bonus = 0
    bonus = max(0, min(10, bonus))  # a classe de bônus vai de 0 a 10 e nada mais

    risco = {
        "segurado": {
            "nome": str(d.get("nome") or "").strip(),
            "cpf": cpf,
            "nascimento": nasc.isoformat(),
            "sexo": (str(d.get("sexo") or "").strip().upper()[:1] or None),
            "estado_civil": str(d.get("estado_civil") or "").strip().lower() or None,
            "telefone": _digitos(d.get("telefone")) or None,
            "email": str(d.get("email") or "").strip() or None,
            "cep": cep,
        },
        "veiculo": {
            "fipe": fipe or None,
            "placa": placa or None,
            "chassi": str(d.get("chassi") or "").strip().upper() or None,
            "marca_modelo": marca_modelo or None,
            "ano_modelo": ano_modelo,
            "ano_fabricacao": (int(d["ano_fabricacao"]) if str(d.get("ano_fabricacao") or "").isdigit()
                               else ano_modelo),
            "zero_km": bool(d.get("zero_km")),
            "uso": uso,
            "garagem": garagem,
            "cep_pernoite": _digitos(d.get("cep_pernoite")) or cep,
        },
        "condutor": {
            # o condutor principal é o próprio segurado NA MAIORIA das vezes, e é
            # por isso que ele é o padrão: perguntar sempre faria o corretor
            # repetir o CPF que acabou de digitar.
            "e_o_segurado": bool(d.get("condutor_e_o_segurado", True)),
            "nascimento": (_data(d.get("condutor_nascimento")).isoformat()
                           if _data(d.get("condutor_nascimento")) else nasc.isoformat()),
            "sexo": (str(d.get("condutor_sexo") or d.get("sexo") or "").strip().upper()[:1] or None),
            "jovem_em_casa": bool(d.get("jovem_em_casa")),
        },
        "cobertura": {
            "classe_bonus": bonus,
            "tipo": str(d.get("tipo_cobertura") or "compreensiva").strip().lower(),
            "vigencia_inicio": (_data(d.get("vigencia_inicio")).isoformat()
                                if _data(d.get("vigencia_inicio")) else None),
        },
        "obs": str(d.get("obs") or "").strip() or None,
    }
    return risco


def resumo_do_risco(risco: dict) -> str:
    """Uma linha pra lista e pro WhatsApp: quem, o quê e onde."""
    seg = (risco or {}).get("segurado") or {}
    vei = (risco or {}).get("veiculo") or {}
    carro = vei.get("marca_modelo") or vei.get("placa") or vei.get("fipe") or "veículo"
    ano = vei.get("ano_modelo")
    nome = seg.get("nome") or _doc.formatar(seg.get("cpf")) or "segurado"
    return f"{nome} · {carro}{f'/{ano}' if ano else ''}"


# ────────────────────────────────────────────────────────── persistência

_COLS = ("c.id, c.cliente_id, c.corretor_id, c.ramo, c.origem, c.provedor, "
         "c.situacao, c.risco, c.erro, c.perda_motivo, c.apolice_id, c.criado_em, "
         "c.cotado_em")


def _linha(r, *, cliente_nome=None) -> dict:
    (cid, cli, corr, ramo, origem, prov, sit, risco, erro, perda, apol,
     criado, cotado) = r
    return {
        "id": cid, "cliente_id": cli, "corretor_id": corr, "ramo": ramo,
        "ramo_txt": _ap.rotulo_ramo(ramo), "origem": origem,
        "origem_txt": rotulo_origem(origem), "provedor": prov,
        "situacao": sit, "situacao_txt": rotulo_situacao(sit),
        "risco": risco or {}, "erro": erro, "perda_motivo": perda,
        "apolice_id": apol,
        "criado_em": criado, "cotado_em": cotado,
        "cliente_nome": cliente_nome,
        "resumo": resumo_do_risco(risco or {}),
    }


def criar(pool, conta_id: int, risco: dict, *, cliente_id: int | None = None,
          corretor_id: int | None = None, ramo: str = "auto",
          origem: str = "painel", provedor: str = "manual") -> int:
    """Grava a cotação em rascunho e devolve o id. O risco já vem normalizado."""
    if origem not in _ORIGENS:
        origem = "painel"
    with pool.connection() as c:
        with c.transaction():
            r = c.execute(
                """insert into cotacoes (conta_id, cliente_id, corretor_id, ramo,
                                         origem, provedor, risco)
                   values (%s,%s,%s,%s,%s,%s,%s) returning id""",
                (conta_id, cliente_id, corretor_id, (ramo or "auto").lower(),
                 origem, (provedor or "manual"), json.dumps(risco or {}))).fetchone()
    return int(r[0])


def _inserir_oferta(c, cotacao_id: int, o: Oferta) -> int:
    """O insert de UMA oferta. Existe separado porque há dois caminhos que o usam
    — o provedor (que substitui tudo) e o corretor digitando (que acrescenta) — e
    duas cópias do mesmo insert divergem na primeira coluna nova."""
    r = c.execute(
        """insert into cotacao_ofertas
             (cotacao_id, seguradora, produto, premio_liquido_centavos,
              iof_centavos, premio_total_centavos, franquia_centavos,
              comissao_pct, parcelas, coberturas, validade, ref_externa, bruto)
           values (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) returning id""",
        (cotacao_id, o.seguradora, o.produto or "",
         o.premio_liquido_centavos, o.iof_centavos, o.premio_total_centavos,
         o.franquia_centavos, o.comissao_pct, o.parcelas,
         json.dumps(o.coberturas or []), o.validade, o.ref_externa,
         json.dumps(o.bruto or {}))).fetchone()
    return int(r[0])


def acrescentar_oferta(pool, conta_id: int, cotacao_id: int, oferta: Oferta) -> int:
    """Põe MAIS uma oferta na cotação, sem tocar nas que já estão. Devolve o id.

    É o caminho do corretor digitando o que levantou em cada seguradora — uma de
    cada vez. Passar por `gravar_ofertas` aqui apagaria as anteriores e, pior,
    desfaria a ESCOLHA já feita em silêncio: quem lançasse um preço novo depois de
    escolher perderia a escolha sem ver, e a proposta sairia da oferta errada.
    """
    with pool.connection() as c:
        with c.transaction():
            dono = c.execute("select situacao from cotacoes where id=%s and conta_id=%s",
                             (cotacao_id, conta_id)).fetchone()
            if not dono:
                raise CotacaoErro("cotação não encontrada")
            oid = _inserir_oferta(c, cotacao_id, oferta)
            if dono[0] in ("rascunho", "falhou"):
                # a primeira oferta digitada é o que faz a cotação virar 'cotada';
                # 'escolhida' e 'proposta' não regridem por causa de mais um preço
                c.execute("""update cotacoes set situacao='cotada', erro=null,
                                    cotado_em=coalesce(cotado_em, now()), atualizado_em=now()
                              where id=%s and conta_id=%s""", (cotacao_id, conta_id))
    return oid


def gravar_ofertas(pool, conta_id: int, cotacao_id: int, ofertas) -> int:
    """Substitui as ofertas da cotação e marca como 'cotada'. Devolve quantas.

    SUBSTITUI, não acrescenta: cotar de novo é pedir preço de hoje, e misturar com
    o de ontem faria a tela comparar preços de datas diferentes lado a lado sem
    dizer. Zero oferta não é erro — é "ninguém aceitou este risco", que a tela
    precisa poder mostrar; quem falhou de verdade passa por `marcar_falha`.
    """
    ofertas = list(ofertas or [])
    with pool.connection() as c:
        with c.transaction():
            dono = c.execute("select 1 from cotacoes where id=%s and conta_id=%s",
                             (cotacao_id, conta_id)).fetchone()
            if not dono:
                raise CotacaoErro("cotação não encontrada")
            c.execute("delete from cotacao_ofertas where cotacao_id=%s", (cotacao_id,))
            for o in ofertas:
                _inserir_oferta(c, cotacao_id, o)
            # `situacao not in (...)`: cotação que JÁ virou proposta (ou que o
            # cliente já não fechou) não volta pra 'cotada' porque alguém lançou
            # mais uma oferta. O estado final é um fato — o preço novo não o apaga.
            c.execute("""update cotacoes set situacao='cotada', erro=null,
                                cotado_em=now(), atualizado_em=now()
                          where id=%s and conta_id=%s
                            and situacao not in ('proposta','perdida')""",
                      (cotacao_id, conta_id))
    return len(ofertas)


def marcar_falha(pool, conta_id: int, cotacao_id: int, erro: str) -> None:
    """Guarda o porquê na linha. Falha de provedor que só vai pro log é falha que
    ninguém conserta — e a corretora fica achando que o sistema 'não cotou'."""
    with pool.connection() as c:
        with c.transaction():
            c.execute("""update cotacoes set situacao='falhou', erro=%s, atualizado_em=now()
                          where id=%s and conta_id=%s
                            and situacao not in ('proposta','perdida')""",
                      (str(erro or "")[:500], cotacao_id, conta_id))


def ofertas(pool, conta_id: int, cotacao_id: int) -> list[dict]:
    """As ofertas, da mais barata pra mais cara — que é a ordem em que se compara.

    A comissão estimada sai do cadastro da conta (`seguros_comissao`, migração
    278) quando o provedor não mandou o percentual: é o mesmo número que a
    carteira usa, então a estimativa da cotação e a da apólice nunca divergem.
    """
    with pool.connection() as c:
        rows = c.execute(
            """select o.id, o.seguradora, o.produto, o.premio_liquido_centavos,
                      o.iof_centavos, o.premio_total_centavos, o.franquia_centavos,
                      o.comissao_pct, o.parcelas, o.coberturas, o.validade,
                      o.ref_externa, o.escolhida, c.ramo
                 from cotacao_ofertas o
                 join cotacoes c on c.id = o.cotacao_id
                where o.cotacao_id = %s and c.conta_id = %s
                order by o.premio_total_centavos asc, lower(o.seguradora)""",
            (cotacao_id, conta_id)).fetchall()
        out = []
        for (oid, seg, prod, liq, iof, total, franq, pct, parc, cobs, val,
             ref, esc, ramo) in rows:
            if pct is None:
                pct = _ap.pct_padrao(c, conta_id, seg, ramo)
            o = Oferta(seguradora=seg, produto=prod or "", premio_total_centavos=total,
                       premio_liquido_centavos=liq, iof_centavos=iof,
                       franquia_centavos=franq, comissao_pct=pct, parcelas=parc,
                       coberturas=cobs or [], validade=val, ref_externa=ref)
            out.append({"id": oid, "seguradora": seg, "produto": prod or "",
                        "premio_liquido_centavos": liq, "iof_centavos": iof,
                        "premio_total_centavos": total, "franquia_centavos": franq,
                        "comissao_pct": pct, "parcelas": parc,
                        "coberturas": cobs or [], "validade": val,
                        "ref_externa": ref, "escolhida": bool(esc),
                        "comissao_centavos": o.comissao_estimada()})
    return out


def ler(pool, conta_id: int, cotacao_id: int) -> dict | None:
    """A cotação com as ofertas dentro. None quando não é desta conta — e é o
    mesmo recorte de multi-tenant do resto da base: o id sozinho nunca basta."""
    with pool.connection() as c:
        r = c.execute(
            f"""select {_COLS}, coalesce(cl.nome,'') from cotacoes c
                  -- `cl.dono_id = c.conta_id` no JOIN e não só o `where` de baixo:
                  -- o recorte de dono tem que estar em TODA tabela multi-tenant que
                  -- a consulta toca (tests/test_escopo_conta), senão um cliente_id
                  -- de outra conta traria o nome dela pra esta tela
                  left join clientes cl on cl.id = c.cliente_id and cl.dono_id = c.conta_id
                 where c.id = %s and c.conta_id = %s""",
            (cotacao_id, conta_id)).fetchone()
    if not r:
        return None
    d = _linha(r[:-1], cliente_nome=(r[-1] or None))
    d["ofertas"] = ofertas(pool, conta_id, cotacao_id)
    return d


def listar(pool, conta_id: int, *, corretor_id: int | None = None,
           situacao: str | None = None, limite: int = 50) -> list[dict]:
    """As cotações da conta, mais nova primeiro. `corretor_id` recorta a fila do
    corretor — mesmo desenho da carteira de apólices e da Fila do vendedor."""
    sql = (f"select {_COLS}, coalesce(cl.nome,'') from cotacoes c "
           "left join clientes cl on cl.id = c.cliente_id and cl.dono_id = c.conta_id "
           "where c.conta_id = %s")
    args = [conta_id]
    if corretor_id is not None:
        sql += " and c.corretor_id = %s"
        args.append(corretor_id)
    if situacao:
        sql += " and c.situacao = %s"
        args.append(situacao)
    sql += " order by c.criado_em desc limit %s"
    args.append(int(limite))
    with pool.connection() as c:
        rows = c.execute(sql, tuple(args)).fetchall()
    return [_linha(r[:-1], cliente_nome=(r[-1] or None)) for r in rows]


def escolher(pool, conta_id: int, cotacao_id: int, oferta_id: int) -> dict:
    """O corretor escolheu esta oferta. Devolve a oferta escolhida.

    Desmarca as outras na MESMA transação: o índice único parcial da 304 recusaria
    a segunda marcação, e um erro de banco aqui seria a tela dizendo "não deu pra
    escolher" quando o que houve foi ela já ter uma escolha.
    """
    with pool.connection() as c:
        with c.transaction():
            r = c.execute(
                """select o.id from cotacao_ofertas o join cotacoes c on c.id=o.cotacao_id
                    where o.id=%s and o.cotacao_id=%s and c.conta_id=%s""",
                (oferta_id, cotacao_id, conta_id)).fetchone()
            if not r:
                raise CotacaoErro("oferta não encontrada nesta cotação")
            c.execute("update cotacao_ofertas set escolhida=false where cotacao_id=%s",
                      (cotacao_id,))
            c.execute("update cotacao_ofertas set escolhida=true where id=%s", (oferta_id,))
            c.execute("""update cotacoes set situacao='escolhida', atualizado_em=now()
                          where id=%s and conta_id=%s and situacao <> 'proposta'""",
                      (cotacao_id, conta_id))
    return [o for o in ofertas(pool, conta_id, cotacao_id) if o["id"] == oferta_id][0]


def perder(pool, conta_id: int, cotacao_id: int, motivo: str = "") -> None:
    """O cliente não fechou. Guardar isto é o que permite um dia responder 'perdi
    pra qual preço' — a pergunta que a corretora faz e ninguém sabe responder."""
    with pool.connection() as c:
        with c.transaction():
            c.execute("""update cotacoes set situacao='perdida', perda_motivo=%s,
                                atualizado_em=now()
                          where id=%s and conta_id=%s""",
                      (str(motivo or "")[:500] or None, cotacao_id, conta_id))


# ──────────────────────────────────────────── da oferta escolhida pra proposta

def virar_proposta(pool, conta_id: int, cotacao_id: int, *,
                   vigencia_inicio=None, vigencia_fim=None,
                   numero_proposta: str | None = None,
                   cliente_id: int | None = None) -> int:
    """Cria a apólice em situação 'proposta' a partir da oferta escolhida.

    É AQUI QUE A COTAÇÃO ENCONTRA A CARTEIRA. A apólice nasce com o prêmio, a
    franquia, o veículo e o condutor que já estão na cotação — o corretor não
    redigita nada, e é justamente a redigitação que faz a carteira divergir do
    que foi vendido.

    A VIGÊNCIA É DE 12 MESES quando ninguém disser o contrário, porque apólice de
    auto é anual e `vigencia_fim` é NOT NULL na 278 (sem ela a apólice não entra
    em régua nenhuma e ficaria parecendo coberta pra sempre).

    SÓ O LÍQUIDO VAI PRO `premio_centavos`. Quando a oferta não separou o IOF, vai
    o total e o IOF fica zero — e isso está explicitamente ERRADO por omissão do
    provedor, não por conta nossa: a corretora corrige na tela da apólice quando o
    papel chegar. O contrário (rachar por um IOF chutado) inventaria comissão.
    """
    cot = ler(pool, conta_id, cotacao_id)
    if not cot:
        raise CotacaoErro("cotação não encontrada")
    esc = [o for o in cot["ofertas"] if o["escolhida"]]
    if not esc:
        raise CotacaoErro("escolha uma oferta antes de gerar a proposta")
    o = esc[0]
    ini = _data(vigencia_inicio) or _data((cot["risco"].get("cobertura") or {}).get("vigencia_inicio")) \
        or date.today()
    fim = _data(vigencia_fim)
    if fim is None:
        try:
            fim = ini.replace(year=ini.year + 1)
        except ValueError:      # 29/02 -> 28/02 do ano seguinte
            fim = ini.replace(year=ini.year + 1, day=28)
    vei = cot["risco"].get("veiculo") or {}
    cond = cot["risco"].get("condutor") or {}
    liq = o["premio_liquido_centavos"]
    dados = {
        "cliente_id": cliente_id if cliente_id is not None else cot["cliente_id"],
        "corretor_id": cot["corretor_id"],
        "seguradora": o["seguradora"],
        "ramo": cot["ramo"],
        "numero_proposta": (numero_proposta or o["ref_externa"] or None),
        "vigencia_inicio": ini,
        "vigencia_fim": fim,
        "situacao": "proposta",
        "premio_centavos": int(liq if liq is not None else o["premio_total_centavos"]),
        "iof_centavos": int(o["iof_centavos"] or 0),
        "franquia_centavos": int(o["franquia_centavos"] or 0),
        "comissao_pct": o["comissao_pct"],
        "parcelas": o["parcelas"],
        "classe_bonus": str((cot["risco"].get("cobertura") or {}).get("classe_bonus") or "") or None,
        "bem": {k: v for k, v in vei.items() if v is not None},
        "condutor": {k: v for k, v in cond.items() if v is not None},
        "coberturas": o["coberturas"],
        "obs": f"Gerada da cotação #{cotacao_id}"
               + (f" · {o['produto']}" if o["produto"] else ""),
    }
    apolice_id = _ap.salvar(pool, conta_id, dados)
    with pool.connection() as c:
        with c.transaction():
            c.execute("""update cotacoes set situacao='proposta', apolice_id=%s,
                                atualizado_em=now()
                          where id=%s and conta_id=%s""", (apolice_id, cotacao_id, conta_id))
    _log.info("cotação %s da conta %s virou a apólice %s (%s)",
              cotacao_id, conta_id, apolice_id, o["seguradora"])
    return apolice_id


def roteiro_do_portal(cot: dict, oferta: dict | None = None) -> str:
    """O FALLBACK DA EMISSÃO: o resumo pronto pra digitar no portal da seguradora.

    Por que isto existe, e por que não é um consolo. Nenhuma seguradora brasileira
    abre hoje uma API de emissão pro corretor: o caminho real é o portal dela, à
    mão. O que o sistema pode tirar do caminho é a parte chata — procurar o CPF,
    o chassi e o CEP em três lugares e copiar errado. Este texto é feito pra ser
    copiado inteiro e lido de cima pra baixo enquanto se preenche a tela do outro
    lado; a ordem dos campos é a ordem em que os portais perguntam.
    """
    r = cot.get("risco") or {}
    seg = r.get("segurado") or {}
    vei = r.get("veiculo") or {}
    cond = r.get("condutor") or {}
    cob = r.get("cobertura") or {}
    o = oferta or next((x for x in cot.get("ofertas") or [] if x["escolhida"]), None)
    L = [f"COTAÇÃO #{cot['id']} — {resumo_do_risco(r)}"]
    if o:
        L.append(f"Seguradora: {o['seguradora']}"
                 + (f" · {o['produto']}" if o.get("produto") else ""))
        L.append(f"Prêmio total: {_brl(o['premio_total_centavos'])}"
                 + (f" em {o['parcelas']}x" if o.get("parcelas") else ""))
        if o.get("franquia_centavos"):
            L.append(f"Franquia: {_brl(o['franquia_centavos'])}")
        if o.get("ref_externa"):
            L.append(f"Nº da cotação na seguradora: {o['ref_externa']}")
    L += [
        "",
        "SEGURADO",
        f"Nome: {seg.get('nome') or '—'}",
        f"CPF: {_doc.formatar(seg.get('cpf')) or '—'}",
        f"Nascimento: {_br(seg.get('nascimento'))}",
        f"CEP: {_cep(seg.get('cep'))}",
        f"Telefone: {seg.get('telefone') or '—'}",
        "",
        "VEÍCULO",
        f"Placa: {vei.get('placa') or '—'} · Chassi: {vei.get('chassi') or '—'}",
        f"FIPE: {vei.get('fipe') or '—'} · {vei.get('marca_modelo') or '—'}",
        f"Ano: {vei.get('ano_fabricacao') or '—'}/{vei.get('ano_modelo') or '—'}"
        + (" · ZERO KM" if vei.get("zero_km") else ""),
        f"Uso: {dict(USOS_AUTO).get(vei.get('uso'), '—')} · "
        f"Garagem: {dict(GARAGENS).get(vei.get('garagem'), '—')}",
        f"CEP de pernoite: {_cep(vei.get('cep_pernoite') or seg.get('cep'))}",
        "",
        "CONDUTOR",
        ("É o próprio segurado" if cond.get("e_o_segurado") else "Outro condutor"),
        f"Nascimento: {_br(cond.get('nascimento'))}",
        f"Jovem em casa (17–25): {'sim' if cond.get('jovem_em_casa') else 'não'}",
        "",
        f"Classe de bônus: {cob.get('classe_bonus', 0)}",
        f"Cobertura: {cob.get('tipo') or 'compreensiva'}",
    ]
    if r.get("obs"):
        L += ["", f"Observação: {r['obs']}"]
    return "\n".join(L)


def _brl(centavos) -> str:
    v = int(centavos or 0)
    return f"R$ {v // 100:,}".replace(",", ".") + f",{v % 100:02d}"


def _br(iso) -> str:
    d = _data(iso)
    return d.strftime("%d/%m/%Y") if d else "—"


def _cep(v) -> str:
    s = _digitos(v)
    return f"{s[:5]}-{s[5:]}" if len(s) == 8 else (s or "—")


# ──────────────────────────────────────────────────────── orquestração

def cotar(pool, conta_id: int, cotacao_id: int, provedor=None) -> dict:
    """Manda o risco pro provedor, grava o que voltou e devolve a cotação lida.

    NUNCA LEVANTA POR FALHA DO PROVEDOR: a falha vira situação 'falhou' com o
    motivo na linha, e quem chamou continua tendo uma cotação pra mostrar. As três
    portas (tela, API e WhatsApp) dependem disso pra não precisar cada uma inventar
    o seu tratamento de erro — e erro de rede de terceiro é o mais comum de todos.
    """
    from . import cotacao_provedores as _prov
    cot = ler(pool, conta_id, cotacao_id)
    if not cot:
        raise CotacaoErro("cotação não encontrada")
    p = provedor if provedor is not None else _prov.provedor_ativo(pool, conta_id)
    try:
        achadas = list(p.cotar(cot["risco"]))
    except Exception as e:  # noqa: BLE001 — a falha do terceiro é dado, não crash
        _log.warning("cotação %s (conta %s, provedor %s) falhou: %s: %s",
                     cotacao_id, conta_id, p.chave, type(e).__name__, e)
        marcar_falha(pool, conta_id, cotacao_id, str(e))
        return ler(pool, conta_id, cotacao_id)
    with pool.connection() as c:
        with c.transaction():
            c.execute("update cotacoes set provedor=%s where id=%s and conta_id=%s",
                      (p.chave, cotacao_id, conta_id))
    gravar_ofertas(pool, conta_id, cotacao_id, achadas)
    return ler(pool, conta_id, cotacao_id)


def enviar_para_emissao(pool, conta_id: int, cotacao_id: int, provedor=None) -> dict:
    """O "envio de dados pra apólice" do pedido — até a PROPOSTA, e só.

    Devolve sempre um dicionário com `automatico` (o provedor aceitou e devolveu
    número de proposta) ou `roteiro` (o texto pra digitar no portal). Os dois
    caminhos terminam no mesmo lugar: `virar_proposta` cria a apólice, e a
    corretora enxerga uma carteira só — não uma "carteira das automáticas" e outra
    das manuais.
    """
    from . import cotacao_provedores as _prov
    cot = ler(pool, conta_id, cotacao_id)
    if not cot:
        raise CotacaoErro("cotação não encontrada")
    esc = next((o for o in cot["ofertas"] if o["escolhida"]), None)
    if not esc:
        raise CotacaoErro("escolha uma oferta antes de enviar pra emissão")
    p = provedor if provedor is not None else _prov.provedor_ativo(pool, conta_id)
    if getattr(p, "suporta_emissao", False):
        try:
            r = p.emitir(cot["risco"], esc) or {}
            numero = str(r.get("numero_proposta") or "").strip() or None
            apolice_id = virar_proposta(pool, conta_id, cotacao_id, numero_proposta=numero)
            return {"automatico": True, "numero_proposta": numero,
                    "apolice_id": apolice_id, "provedor": p.chave, "bruto": r}
        except Exception as e:  # noqa: BLE001 — cai no manual, que sempre funciona
            _log.warning("emissão da cotação %s falhou no provedor %s: %s: %s",
                         cotacao_id, p.chave, type(e).__name__, e)
            return {"automatico": False, "erro": str(e),
                    "roteiro": roteiro_do_portal(cot, esc)}
    return {"automatico": False, "roteiro": roteiro_do_portal(cot, esc)}


# ──────────────────────────────────────────────────── as chaves da API

#: O prefixo do token. Serve pra pessoa reconhecer o que é aquilo quando aparece
#: colado num chamado de suporte — e pra um scanner de segredo ter o que casar.
PREFIXO_CHAVE = "zaq_cot_"


def _hash(token: str) -> str:
    import hashlib
    return hashlib.sha256((token or "").encode("utf-8")).hexdigest()


def criar_chave(pool, conta_id: int, rotulo: str = "", criado_por: int | None = None) -> str:
    """Emite uma chave de API e devolve o TOKEN — a única vez em que ele existe.

    O banco guarda o sha256 e os 12 primeiros caracteres (só pra tela dizer qual
    chave é qual). Vazado o banco, ninguém cota no nome da corretora; perdido o
    token, não há como recuperar — emite-se outro e revoga-se o antigo. É a mesma
    troca que todo provedor sério faz, e é de propósito.
    """
    import secrets
    token = PREFIXO_CHAVE + secrets.token_urlsafe(32)
    with pool.connection() as c:
        with c.transaction():
            c.execute("""insert into cotacao_chaves (conta_id, rotulo, prefixo, token_hash, criado_por)
                         values (%s,%s,%s,%s,%s)""",
                      (conta_id, (rotulo or "").strip()[:80], token[:12], _hash(token), criado_por))
    return token


def chaves(pool, conta_id: int) -> list[dict]:
    with pool.connection() as c:
        rows = c.execute(
            "select id, rotulo, prefixo, ativa, criado_em, ultimo_uso_em "
            "from cotacao_chaves where conta_id=%s order by criado_em desc",
            (conta_id,)).fetchall()
    return [{"id": i, "rotulo": r, "prefixo": p, "ativa": bool(a),
             "criado_em": ce, "ultimo_uso_em": uu} for (i, r, p, a, ce, uu) in rows]


def revogar_chave(pool, conta_id: int, chave_id: int) -> None:
    """Desliga a chave. NÃO apaga a linha: quem usou a API e quando é trilha, e
    apagar a chave apagaria junto a resposta pra "de onde veio esta cotação"."""
    with pool.connection() as c:
        with c.transaction():
            c.execute("update cotacao_chaves set ativa=false where id=%s and conta_id=%s",
                      (chave_id, conta_id))


def conta_da_chave(pool, token: str) -> int | None:
    """De qual conta é este token — ou None. Marca o uso na mesma ida ao banco.

    A busca é pelo HASH e nunca pelo prefixo: prefixo é rótulo de tela, e
    autenticar por ele daria acesso a quem lesse a tela por cima do ombro.
    """
    t = (token or "").strip()
    if not t:
        return None
    with pool.connection() as c:
        with c.transaction():
            r = c.execute(
                "update cotacao_chaves set ultimo_uso_em = now() "
                "where token_hash = %s and ativa returning conta_id", (_hash(t),)).fetchone()
    return int(r[0]) if r else None


def vincular_cliente(pool, conta_id: int, cotacao_id: int) -> int | None:
    """Põe o segurado da cotação na carteira de clientes e amarra os dois.

    POR QUE ISTO É UM TOQUE DO CORRETOR E NÃO ACONTECE SOZINHO NA API. A cotação
    que entra pelo site é um LEAD: vira cliente quando a corretora decide que
    virou. Criar cadastro a cada formulário preenchido encheria a carteira de
    gente que só estava olhando preço — e a carteira é onde ela procura quem já é
    dela. A cotação em si já é a fila ("Cotações do site"); promover é um toque.

    Reusa `clientes.puxar_ou_criar_cliente`, que resolve a PESSOA pelo CPF: o
    mesmo segurado cotando pela terceira vez não vira o terceiro cadastro.
    """
    cot = ler(pool, conta_id, cotacao_id)
    if not cot:
        raise CotacaoErro("cotação não encontrada")
    if cot["cliente_id"]:
        return cot["cliente_id"]
    seg = (cot["risco"].get("segurado") or {})
    if not seg.get("cpf"):
        raise CotacaoErro("a cotação não tem CPF pra identificar o segurado")
    from . import clientes as _cli
    cliente_id = _cli.puxar_ou_criar_cliente(
        pool, conta_id, cpf=seg.get("cpf"), nome=seg.get("nome") or None,
        celular=seg.get("telefone") or None, email=seg.get("email") or None,
        cep=seg.get("cep") or None)
    with pool.connection() as c:
        with c.transaction():
            c.execute("update cotacoes set cliente_id=%s, atualizado_em=now() "
                      "where id=%s and conta_id=%s", (cliente_id, cotacao_id, conta_id))
    return cliente_id
