"""Base de clientes do lojista — modelo PESSOA + RELACAO (Zaq Vendas).

DESENHO (pos-migracao 066):
- IDENTIDADE vive em `pessoas` (cpf/celular/nome/email/conta_zaq_id) e e' UNICA no
  sistema. CPF e' chave forte (funde na igualdade); celular SUGERE (familia divide
  numero) e NUNCA funde sozinho.
- RELACAO loja<->pessoa vive em `clientes` (dono_id + pessoa_id + obs/aniversario/ativo).
  Multi-tenant: um lojista so' ve a SUA relacao. `clientes.nome/telefone/email` sao
  mantidos como CACHE (a leitura sempre prefere `pessoas` via coalesce).
- Portabilidade: se a pessoa virar conta Zaq, `pessoas.conta_zaq_id` liga — e a loja
  nunca ve isso, nem o cliente ve a base da loja.

Compatibilidade: todas as funcoes publicas antigas seguem existindo com a mesma
assinatura (criar_cliente, listar_clientes, obter_cliente, buscar_por_telefone,
atualizar_cliente, arquivar_cliente, achar_ou_criar, contar_clientes). Novas:
resolver_pessoa, puxar_ou_criar_cliente, sugerir_pessoas_por_celular.
"""
from __future__ import annotations

from datetime import date, datetime


def _so_digitos(s: str | None) -> str | None:
    if not s:
        return None
    d = "".join(c for c in s if c.isdigit())
    return d or None


def _garantir_cols(pool) -> None:
    """Garante cidade/uf/endereco/cep/papel em `clientes` (migracoes 149, 152 e
    182) em runtime, UMA VEZ POR PROCESSO E POR BANCO.

    O "e por banco" nao e' detalhe. Ate 31/08/2026 a marca era um booleano solto
    (`_COLS_GARANTIDAS`), e o primeiro banco que passasse por aqui marcava pra
    todos: num processo que fala com dois bancos — o caso da suite, onde varios
    modulos criam banco proprio — o segundo nunca recebia os ALTER e quebrava com
    "column endereco does not exist". Sessenta e um testes cairam assim de uma
    vez, e nenhum deles tinha nada a ver com o assunto.

    `core.esquema_runtime` existe pra isso: a chave carrega host/porta/nome do
    banco, a marca so' e' gravada depois do sucesso, e os testes a limpam entre
    si (fixture autouse do conftest). Mesmo mecanismo que `contas.equipe` e o
    painel de servicos ja' usam desde a lentidao de 22/08."""
    from core import esquema_runtime

    def _aplicar():
        try:
            with pool.connection() as c:
                c.execute("alter table clientes add column if not exists cidade text")
                c.execute("alter table clientes add column if not exists uf varchar(2)")
                c.execute("alter table clientes add column if not exists endereco text")
                c.execute("alter table clientes add column if not exists cep text")
                c.execute("alter table clientes add column if not exists "
                          "eh_cliente boolean not null default true")
                c.execute("alter table clientes add column if not exists "
                          "eh_fornecedor boolean not null default false")
                c.commit()
        except Exception:  # noqa: BLE001 — sem permissao de DDL, segue o jogo
            pass

    esquema_runtime.garantir(esquema_runtime.chave(pool, "clientes_cols"), _aplicar)


# Casar telefone pelos ULTIMOS 8 DIGITOS, nao pelo texto inteiro. O mesmo numero
# chega em formatos diferentes conforme a porta de entrada: digitado a mao vem
# "86998280472" (11 digitos) e vindo do WhatsApp vem "558698280472" (12, com o
# 55). Medido na Prime em 29/08/2026: 13 registros num formato e 6 no outro —
# comparando texto exato eles NUNCA se encontram, e a mesma pessoa duplica.
# Os 8 finais sao estaveis: nao carregam pais, DDD nem o nono digito. E' a mesma
# regua que o WhatsApp ja usa em wa_contatos.numero8.
_MIN_DIGITOS_TEL = 8


def _n8(v: str | None) -> str | None:
    """Os ultimos 8 digitos do telefone. None se nao der 8 — comparar menos que
    isso casaria gente diferente, e duplicar e' menos grave que fundir errado."""
    d = _so_digitos(v)
    return d[-8:] if d and len(d) >= _MIN_DIGITOS_TEL else None


def _cep(v: str | None) -> str | None:
    """CEP guardado só em dígitos quando vier completo (a folha é quem põe a
    máscara). O que não tiver 8 dígitos fica como o lojista digitou — melhor um
    CEP torto salvo do que um CEP perdido."""
    d = _so_digitos(v)
    if d and len(d) == 8:
        return d
    return (v or "").strip() or None


def _parse_data(s):
    """Aceita 'AAAA-MM-DD' ou date; devolve date ou None."""
    if not s:
        return None
    if isinstance(s, date):
        return s
    try:
        return datetime.strptime(str(s).strip(), "%Y-%m-%d").date()
    except (ValueError, TypeError):
        return None


# ---------------------------------------------------------------------------
# IDENTIDADE (pessoas)
# ---------------------------------------------------------------------------
def resolver_pessoa(pool, *, cpf: str | None = None, cnpj: str | None = None,
                    celular: str | None = None, nome: str | None = None,
                    email: str | None = None, conta_zaq_id: int | None = None,
                    tipo: str | None = None) -> int:
    """Acha/cria a IDENTIDADE e retorna pessoa_id.

    Regra de dedup: CPF e CNPJ FUNDEM (se ja existe pessoa com esse documento,
    reusa). O documento e' VALIDADO (digito verificador) — invalido levanta
    ValueError. Celular NAO funde sozinho (so' sugere). Se nao achou por documento,
    cria uma pessoa nova (nome obrigatorio). `tipo` (pf/pj) e' derivado do documento
    quando nao informado.
    """
    from finance import validadoc
    cpf_d = validadoc.so_digitos(cpf) or None
    cnpj_d = validadoc.so_digitos(cnpj) or None
    if cpf_d and not validadoc.valida_cpf(cpf_d):
        raise ValueError("CPF invalido")
    if cnpj_d and not validadoc.valida_cnpj(cnpj_d):
        raise ValueError("CNPJ invalido")
    tp = tipo or ("pj" if cnpj_d else "pf")
    with pool.connection() as c:
        if cpf_d:
            r = c.execute("select id from pessoas where cpf=%s", (cpf_d,)).fetchone()
            if r:
                return int(r[0])
        if cnpj_d:
            r = c.execute("select id from pessoas where cnpj=%s", (cnpj_d,)).fetchone()
            if r:
                return int(r[0])
        nome_l = (nome or "").strip()
        if not nome_l:
            raise ValueError("nome e' obrigatorio pra criar a pessoa")
        row = c.execute(
            """insert into pessoas (cpf, cnpj, tipo, celular, nome, email, conta_zaq_id)
               values (%s, %s, %s, %s, %s, %s, %s) returning id""",
            (cpf_d, cnpj_d, tp, validadoc.so_digitos(celular) or None, nome_l,
             (email or "").strip().lower() or None, conta_zaq_id),
        ).fetchone()
        c.commit()
        return int(row[0])


def sugerir_pessoas_por_celular(pool, celular: str | None) -> list[dict]:
    """Celular SUGERE (nao funde): pode haver mais de uma pessoa no mesmo numero
    (familia). Retorna candidatos pra o lojista escolher/confirmar."""
    tel = _so_digitos(celular)
    if not tel:
        return []
    with pool.connection() as c:
        rows = c.execute(
            "select id, nome, cpf, celular from pessoas where celular=%s order by nome limit 10",
            (tel,),
        ).fetchall()
    return [{"id": r[0], "nome": r[1], "cpf": r[2], "celular": r[3]} for r in rows]


# ---------------------------------------------------------------------------
# RELACAO (clientes)
# ---------------------------------------------------------------------------
def puxar_ou_criar_cliente(pool, dono_id: int, *, cpf: str | None = None,
                           cnpj: str | None = None,
                           celular: str | None = None, nome: str | None = None,
                           email: str | None = None, pessoa_id: int | None = None,
                           aniversario=None, obs: str | None = None,
                           cidade: str | None = None, uf: str | None = None,
                           endereco: str | None = None, cep: str | None = None,
                           eh_cliente: bool = True, eh_fornecedor: bool = False) -> int:
    """Fluxo de cadastro/venda: resolve a PESSOA (por cpf/cnpj, ou pessoa_id dado) e
    garante a RELACAO (clientes) deste lojista com ela. Retorna cliente_id.

    Se o lojista ja tem uma relacao ativa com essa pessoa, reusa (nao duplica) —
    e o papel (eh_cliente/eh_fornecedor) so' vale na CRIACAO; reusar uma relacao
    existente nao muda o papel que ja estava marcado (use atualizar_cliente pra isso).
    """
    if pessoa_id is None:
        pessoa_id = resolver_pessoa(pool, cpf=cpf, cnpj=cnpj, celular=celular,
                                    nome=nome, email=email)
    _garantir_cols(pool)
    with pool.connection() as c:
        r = c.execute(
            "select id from clientes where dono_id=%s and pessoa_id=%s and ativo limit 1",
            (dono_id, pessoa_id),
        ).fetchone()
        if r:
            return int(r[0])
        p = c.execute(
            "select nome, celular, email from pessoas where id=%s", (pessoa_id,)
        ).fetchone()
        pn = p[0] if p else (nome or "").strip()
        pc = p[1] if p else _so_digitos(celular)
        pe = p[2] if p else ((email or "").strip().lower() or None)
        row = c.execute(
            """insert into clientes
                 (dono_id, pessoa_id, nome, telefone, email, aniversario, obs,
                  cidade, uf, endereco, cep, eh_cliente, eh_fornecedor)
               values (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) returning id""",
            (dono_id, pessoa_id, pn, pc, pe, _parse_data(aniversario),
             (obs or "").strip() or None, (cidade or "").strip() or None,
             (uf or "").strip()[:2].upper() or None,
             (endereco or "").strip() or None, _cep(cep),
             bool(eh_cliente), bool(eh_fornecedor)),
        ).fetchone()
        c.commit()
        return int(row[0])


def criar_cliente(pool, dono_id: int, nome: str, *, telefone: str | None = None,
                  email: str | None = None, aniversario=None,
                  conta_zaq_id: int | None = None, obs: str | None = None,
                  cpf: str | None = None, cnpj: str | None = None,
                  cidade: str | None = None, uf: str | None = None,
                  endereco: str | None = None, cep: str | None = None,
                  eh_cliente: bool = True, eh_fornecedor: bool = False) -> int:
    """Cadastra um cliente (identidade + relacao). Retorna o cliente_id. nome
    obrigatorio. Compat: mesma assinatura de antes, agora com `cpf`/`cnpj`
    opcionais; o telefone entra como `celular` da pessoa.

    Sem CPF/CNPJ, `resolver_pessoa` nao tem chave forte pra casar — por isso,
    nesse caso, primeiro tenta reusar um cliente do MESMO lojista com o mesmo
    telefone (igual `achar_ou_criar` ja faz no PDV), senao todo orcamento sem
    documento (o lead tipico do WhatsApp) criaria um cliente novo a cada save.

    `eh_cliente`/`eh_fornecedor` sao o PAPEL da relacao — nao sao exclusivos (uma
    mesma pessoa pode comprar de voce E vender pra voce). Default preserva o
    comportamento de sempre: todo cadastro novo e' cliente, a nao ser que quem
    chamou diga o contrario."""
    r = salvar_cliente(pool, dono_id, nome, telefone=telefone, email=email,
                       aniversario=aniversario, conta_zaq_id=conta_zaq_id, obs=obs,
                       cpf=cpf, cnpj=cnpj, cidade=cidade, uf=uf, endereco=endereco,
                       cep=cep, eh_cliente=eh_cliente, eh_fornecedor=eh_fornecedor)
    return r["id"]


# Campos que o REUSO pode preencher. Regra: so' preenche o que esta VAZIO —
# nunca sobrescreve, e nunca apaga. Salvar de novo com o campo em branco tem que
# ser inofensivo, senao um segundo salvamento incompleto destruiria o cadastro
# bom feito no primeiro (regra 0 do CLAUDE.md).
_ENRIQUECIVEIS = ("email", "cidade", "uf", "endereco", "cep", "obs")


def salvar_cliente(pool, dono_id: int, nome: str, *, telefone: str | None = None,
                   email: str | None = None, aniversario=None,
                   conta_zaq_id: int | None = None, obs: str | None = None,
                   cpf: str | None = None, cnpj: str | None = None,
                   cidade: str | None = None, uf: str | None = None,
                   endereco: str | None = None, cep: str | None = None,
                   eh_cliente: bool = True, eh_fornecedor: bool = False) -> dict:
    """Cadastra OU atualiza — e diz qual dos dois fez.

    Devolve {"id", "acao", "papel_mudou", "nome"}, com acao em:
        criado      nasceu agora
        atualizado  ja' existia e algum campo vazio foi preenchido
        inalterado  ja' existia e nada mudou

    Por que existe: `criar_cliente` devolvia so' o id, e a tela dizia "Cliente
    cadastrado." nos tres casos. Sem saber se salvou, a vendedora salvava de
    novo — e como o reuso NAO aplicava nada, cada tentativa cunhava um registro.
    Foi assim que a Ana Clara virou tres cadastros em cinco minutos (25/08/2026)
    e o Gilvan virou dois, um como cliente e outro como fornecedor vazio.

    Reusar agora ENRIQUECE (preenche o que esta vazio, nunca sobrescreve) e
    APLICA O PAPEL: marcar "fornecedor" numa pessoa que ja' e' cliente liga a
    marca na linha que existe, em vez de criar outra. Papel so' e' LIGADO aqui;
    desligar e' explicito, pelo atualizar_cliente.
    """
    nome = (nome or "").strip()
    if not nome:
        raise ValueError("nome do cliente e' obrigatorio")
    tem_doc = bool(_so_digitos(cpf) or _so_digitos(cnpj))
    existente = None
    if not tem_doc and telefone:
        existente = buscar_por_telefone(pool, dono_id, telefone)
    if existente is None:
        pessoa_id = resolver_pessoa(pool, cpf=cpf, cnpj=cnpj, celular=telefone,
                                    nome=nome, email=email, conta_zaq_id=conta_zaq_id)
        _garantir_cols(pool)
        with pool.connection() as c:
            achado = c.execute(
                "select id from clientes where dono_id=%s and pessoa_id=%s and ativo limit 1",
                (dono_id, pessoa_id)).fetchone()
        if achado:
            existente = obter_cliente(pool, dono_id, int(achado[0]))
        else:
            novo_id = puxar_ou_criar_cliente(
                pool, dono_id, pessoa_id=pessoa_id, nome=nome, email=email,
                aniversario=aniversario, obs=obs, cidade=cidade, uf=uf,
                endereco=endereco, cep=cep, eh_cliente=eh_cliente,
                eh_fornecedor=eh_fornecedor)
            return {"id": novo_id, "acao": "criado", "papel_mudou": False, "nome": nome}

    # --- daqui pra baixo: JA' EXISTE. Enriquece e aplica o papel. ---
    entrando = {"email": email, "cidade": cidade, "uf": uf,
                "endereco": endereco, "cep": cep, "obs": obs}
    mudar = {k: v for k, v in entrando.items()
             if k in _ENRIQUECIVEIS and (v or "").strip()
             and not (existente.get(k) or "").strip()}
    papel_mudou = False
    if eh_cliente and not existente.get("eh_cliente"):
        mudar["eh_cliente"] = True
        papel_mudou = True
    if eh_fornecedor and not existente.get("eh_fornecedor"):
        mudar["eh_fornecedor"] = True
        papel_mudou = True
    if mudar:
        atualizar_cliente(pool, dono_id, existente["id"], **mudar)
    return {"id": existente["id"],
            "acao": "atualizado" if mudar else "inalterado",
            "papel_mudou": papel_mudou,
            "nome": existente.get("nome") or nome}


_SEL = """select c.id,
                 coalesce(p.nome, c.nome)        as nome,
                 coalesce(p.celular, c.telefone) as telefone,
                 coalesce(p.email, c.email)      as email,
                 c.aniversario,
                 p.conta_zaq_id,
                 c.obs,
                 p.cpf,
                 c.pessoa_id,
                 p.cnpj,
                 p.tipo,
                 c.cidade,
                 c.uf,
                 c.endereco,
                 c.cep,
                 c.eh_cliente,
                 c.eh_fornecedor
            from clientes c
            left join pessoas p on p.id = c.pessoa_id"""


def listar_clientes(pool, dono_id: int, busca: str | None = None,
                    limite: int = 200, papel: str | None = None) -> list[dict]:
    """Lista os clientes do lojista. Se `busca`, filtra por nome, telefone ou cpf.

    `papel='cliente'` ou `papel='fornecedor'` filtra pelo papel marcado no
    cadastro — os dois nao sao exclusivos, uma pessoa pode ter os dois marcados."""
    _garantir_cols(pool)
    sql = _SEL + " where c.dono_id=%s and c.ativo"
    params: list = [dono_id]
    if papel == "cliente":
        sql += " and c.eh_cliente"
    elif papel == "fornecedor":
        sql += " and c.eh_fornecedor"
    if busca and busca.strip():
        termo = f"%{busca.strip()}%"
        dig = _so_digitos(busca) or ""
        termo_dig = f"%{dig}%"
        sql += (" and (coalesce(p.nome,c.nome) ilike %s"
                " or coalesce(p.celular,c.telefone,'') like %s"
                " or coalesce(p.cpf,'') like %s"
                " or coalesce(p.cnpj,'') like %s)")
        params += [termo, termo_dig, termo_dig, termo_dig]
    sql += " order by coalesce(p.nome, c.nome) limit %s"
    params.append(limite)
    with pool.connection() as c:
        rows = c.execute(sql, params).fetchall()
    return [_row_para_dict(r) for r in rows]


def obter_cliente(pool, dono_id: int, cliente_id: int) -> dict | None:
    """Um cliente da base do lojista (isolado por dono_id). None se nao for dele."""
    _garantir_cols(pool)
    with pool.connection() as c:
        r = c.execute(_SEL + " where c.id=%s and c.dono_id=%s and c.ativo",
                      (cliente_id, dono_id)).fetchone()
    return _row_para_dict(r) if r else None


def achar_cliente_por_nome(pool, dono_id: int, nome: str,
                          papel: str | None = None) -> int | None:
    """Acha o cliente do lojista pelo NOME (sem criar). Prioriza match EXATO;
    senao, um unico match parcial. Retorna o id, ou None se nao achar OU se for
    ambiguo (dois com o mesmo nome — nao chuta). Usado pra LIGAR um titulo/
    honorario ao cliente sem duplicar cadastro.

    `papel='cliente'` ou `papel='fornecedor'` restringe a busca a quem tem
    aquele papel marcado — um titulo A PAGAR nao deve casar com alguem que e'
    so' cliente (nunca marcado como fornecedor), e vice-versa."""
    n = (nome or "").strip()
    if not n:
        return None
    filtro = ""
    if papel == "cliente":
        filtro = " and c.eh_cliente"
    elif papel == "fornecedor":
        filtro = " and c.eh_fornecedor"
    with pool.connection() as c:
        exato = c.execute(
            "select c.id from clientes c left join pessoas p on p.id=c.pessoa_id "
            "where c.dono_id=%s and c.ativo" + filtro + " "
            "and lower(coalesce(p.nome,c.nome))=lower(%s) order by c.id limit 2",
            (dono_id, n)).fetchall()
        if len(exato) == 1:
            return exato[0][0]
        if len(exato) >= 2:
            return None  # ambiguo: nao arrisca ligar no errado
        parcial = c.execute(
            "select c.id from clientes c left join pessoas p on p.id=c.pessoa_id "
            "where c.dono_id=%s and c.ativo" + filtro + " "
            "and coalesce(p.nome,c.nome) ilike %s limit 2",
            (dono_id, f"%{n}%")).fetchall()
        return parcial[0][0] if len(parcial) == 1 else None


def buscar_por_telefone(pool, dono_id: int, telefone: str) -> dict | None:
    """Acha um cliente do lojista pelo telefone (so digitos). None se nao achar.
    Util pra nao duplicar na hora de vender (dedup dentro da propria loja)."""
    n8 = _n8(telefone)
    if not n8:
        return None
    _garantir_cols(pool)
    with pool.connection() as c:
        r = c.execute(
            _SEL + " where c.dono_id=%s and c.ativo"
                   "   and right(regexp_replace(coalesce(p.celular, c.telefone),"
                   "                            '[^0-9]', '', 'g'), 8) = %s"
                   " order by c.id limit 1",
            (dono_id, n8),
        ).fetchone()
    return _row_para_dict(r) if r else None


def atualizar_cliente(pool, dono_id: int, cliente_id: int, **campos) -> bool:
    """Atualiza campos do cliente. Identidade (nome/telefone/email/cpf/conta_zaq_id)
    vai pra `pessoas`; relacao (aniversario/obs) vai pra `clientes`. So altera se o
    cliente for do lojista. Retorna True se algo mudou."""
    id_map = {"nome": "nome", "telefone": "celular", "email": "email",
              "cpf": "cpf", "cnpj": "cnpj", "conta_zaq_id": "conta_zaq_id"}
    rel_permit = {"aniversario", "obs", "cidade", "uf", "endereco", "cep",
                  "eh_cliente", "eh_fornecedor"}
    mudou = False
    _garantir_cols(pool)
    with pool.connection() as c:
        row = c.execute(
            "select pessoa_id from clientes where id=%s and dono_id=%s and ativo",
            (cliente_id, dono_id),
        ).fetchone()
        if not row:
            return False
        pessoa_id = row[0]

        from finance import validadoc
        isets, ivals = [], []
        for k, col in id_map.items():
            if k not in campos:
                continue
            v = campos[k]
            if k in ("telefone", "cpf", "cnpj"):
                v = validadoc.so_digitos(v) or None
                if k == "cpf" and v and not validadoc.valida_cpf(v):
                    raise ValueError("CPF invalido")
                if k == "cnpj" and v and not validadoc.valida_cnpj(v):
                    raise ValueError("CNPJ invalido")
            elif k == "email":
                v = (v or "").strip().lower() or None
            elif k == "nome":
                v = (v or "").strip() or None
            isets.append(f"{col}=%s")
            ivals.append(v)
        # o tipo (pf/pj) acompanha o documento editado
        if "cnpj" in campos or "cpf" in campos:
            _cnpj = validadoc.so_digitos(campos.get("cnpj")) if "cnpj" in campos else ""
            _cpf = validadoc.so_digitos(campos.get("cpf")) if "cpf" in campos else ""
            _tp = "pj" if _cnpj else ("pf" if _cpf else None)
            if _tp:
                isets.append("tipo=%s")
                ivals.append(_tp)
        if isets and pessoa_id is not None:
            isets.append("atualizado_em=now()")
            ivals.append(pessoa_id)
            cur = c.execute(f"update pessoas set {', '.join(isets)} where id=%s", ivals)
            mudou = mudou or cur.rowcount > 0
            csets, cvals = [], []
            for k, col in (("nome", "nome"), ("telefone", "telefone"), ("email", "email")):
                if k in campos:
                    v = campos[k]
                    if k == "telefone":
                        v = _so_digitos(v)
                    elif k == "email":
                        v = (v or "").strip().lower() or None
                    else:
                        v = (v or "").strip() or None
                    csets.append(f"{col}=%s")
                    cvals.append(v)
            if csets:
                cvals += [cliente_id, dono_id]
                c.execute(
                    f"update clientes set {', '.join(csets)} where id=%s and dono_id=%s",
                    cvals,
                )

        rsets, rvals = [], []
        for k in rel_permit:
            if k not in campos:
                continue
            v = campos[k]
            if k == "aniversario":
                v = _parse_data(v)
            elif k == "uf":
                v = (v or "").strip()[:2].upper() or None
            elif k == "cep":
                v = _cep(v)
            elif k in ("eh_cliente", "eh_fornecedor"):
                v = bool(v)
            else:                       # obs, cidade, endereco
                v = (v or "").strip() or None
            rsets.append(f"{k}=%s")
            rvals.append(v)
        if rsets:
            rsets.append("atualizado_em=now()")
            rvals += [cliente_id, dono_id]
            cur = c.execute(
                f"update clientes set {', '.join(rsets)} where id=%s and dono_id=%s and ativo",
                rvals,
            )
            mudou = mudou or cur.rowcount > 0

        c.commit()
    return mudou


def arquivar_cliente(pool, dono_id: int, cliente_id: int) -> bool:
    """Soft delete: marca ativo=false. So se for do lojista. (A pessoa/identidade
    permanece — outras lojas podem ter relacao com ela.)"""
    with pool.connection() as c:
        cur = c.execute(
            "update clientes set ativo=false, atualizado_em=now() "
            "where id=%s and dono_id=%s and ativo",
            (cliente_id, dono_id),
        )
        c.commit()
        return cur.rowcount > 0


def achar_ou_criar(pool, dono_id: int, nome: str,
                   telefone: str | None = None, cpf: str | None = None,
                   cnpj: str | None = None) -> int:
    """Fluxo do PDV: na venda, o lojista informa nome (+ telefone e/ou cpf/cnpj).
    Reusa se ja existe (por documento na identidade, ou por telefone na propria
    loja); senao cria. Retorna o cliente_id."""
    if (cpf and _so_digitos(cpf)) or (cnpj and _so_digitos(cnpj)):
        return puxar_ou_criar_cliente(pool, dono_id, cpf=cpf, cnpj=cnpj,
                                      celular=telefone, nome=nome)
    if telefone:
        existente = buscar_por_telefone(pool, dono_id, telefone)
        if existente:
            return existente["id"]
    return criar_cliente(pool, dono_id, nome, telefone=telefone)


def tipo_predominante(pool, dono_id: int) -> str:
    """'pf' ou 'pj' — o que esta empresa mais cadastra. Serve pra tela ja' abrir
    no caso comum dela.

    Motivo: a tela de orcamento abria sempre em Pessoa Juridica, com mascara de
    CNPJ. Na Prime Eventos isso e' 100% errado — os 23 clientes sao tipo='pf' e
    nenhum tem CNPJ, porque quem aluga salao pra casamento e' pessoa. A vendedora
    trocava o botao TODA vez. Empatou ou nao ha' cadastro: 'pj', que era o padrao
    de antes."""
    with pool.connection() as c:
        r = c.execute(
            """select p.tipo, count(*) n
                 from clientes c join pessoas p on p.id = c.pessoa_id
                where c.dono_id=%s and c.ativo and p.tipo in ('pf','pj')
                group by p.tipo order by n desc, p.tipo limit 1""",
            (dono_id,)).fetchone()
    return (r[0] if r else None) or "pj"


def contar_clientes(pool, dono_id: int) -> int:
    """Quantos clientes ativos o lojista tem na base."""
    with pool.connection() as c:
        r = c.execute(
            "select count(*) from clientes where dono_id=%s and ativo",
            (dono_id,),
        ).fetchone()
    return int(r[0]) if r else 0


def historico_cliente(pool, dono_id: int, cliente_id: int,
                      limite: int = 50) -> list[dict]:
    """Historico de VENDAS e SERVICOS do cliente (lancamentos de receita ligados
    a ele, via cliente_id). Isolado por dono_id. Devolve os mais recentes primeiro."""
    with pool.connection() as c:
        ok = c.execute(
            "select 1 from clientes where id=%s and dono_id=%s and ativo",
            (cliente_id, dono_id),
        ).fetchone()
        if not ok:
            return []
        rows = c.execute(
            """select data, valor_centavos, coalesce(pagamento,''), coalesce(descricao,'')
                 from lancamentos
                where cliente_id=%s and conta_id=%s and tipo='receita'
                order by data desc, id desc limit %s""",
            (cliente_id, dono_id, limite),
        ).fetchall()
    return [{"data": r[0], "valor_centavos": r[1], "pagamento": r[2],
             "descricao": r[3]} for r in rows]


def _row_para_dict(r) -> dict:
    from finance import validadoc
    cpf, cnpj = r[7], r[9]
    tipo = r[10] or ("pj" if cnpj else "pf")
    doc = cnpj or cpf
    return {
        "id": r[0],
        "nome": r[1],
        "telefone": r[2],
        "email": r[3],
        "aniversario": r[4],
        "conta_zaq_id": r[5],
        "obs": r[6],
        "cpf": cpf,
        "pessoa_id": r[8],
        "cnpj": cnpj,
        "tipo": tipo,
        "documento": doc,
        "documento_fmt": validadoc.formatar(doc) if doc else None,
        "cidade": r[11] if len(r) > 11 else None,
        "uf": r[12] if len(r) > 12 else None,
        "endereco": r[13] if len(r) > 13 else None,
        "cep": r[14] if len(r) > 14 else None,
        "eh_cliente": bool(r[15]) if len(r) > 15 and r[15] is not None else True,
        "eh_fornecedor": bool(r[16]) if len(r) > 16 and r[16] is not None else False,
    }
