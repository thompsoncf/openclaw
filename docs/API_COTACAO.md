# Cotação de seguro — a API, o conector e o que falta pra plugar um provedor

Escrito em 21/09/2026, com a entrega das migrações 304/305.

## O que existe

Três portas, um miolo só (`finance/cotacao.py`). Nenhuma delas tem regra própria —
duas implementações seriam dois preços pro mesmo risco.

| Porta | Onde | Pra quem |
|---|---|---|
| Tela | `/painel/cotacoes` (`web/painel_cotacao.py`) | corretor, gestor, dono |
| API | `POST /api/v1/cotacoes` (`web/api_cotacao.py`) | o site/landing/parceiro da corretora |
| WhatsApp | ferramentas `cotar_seguro` / `ver_cotacao` (`finance/cotacao_tools.py`) | o corretor, no fio em que já trabalha |

O caminho é sempre o mesmo: **risco → ofertas → escolher → proposta na carteira**
(`apolices`, migração 278, situação `proposta`).

## A API pública

Autenticação por chave da conta, emitida em **Cotações › Chaves da API** (só
gerência). O banco guarda o `sha256`; o token aparece **uma vez**.

```bash
curl -X POST https://<host>/api/v1/cotacoes \
  -H "Authorization: Bearer zaq_cot_..." \
  -H "Content-Type: application/json" \
  -d '{
    "nome": "Fulano de Tal",
    "cpf": "52998224725",
    "nascimento": "1985-04-12",
    "telefone": "5586999998888",
    "cep": "64000000",
    "placa": "ABC1D23",
    "marca_modelo": "FIAT ARGO 1.0",
    "ano_modelo": 2022,
    "uso": "particular",
    "garagem": "residencia",
    "bonus": 5
  }'
```

**O mínimo**: `cpf`, `nascimento`, `cep` e o veículo por **um** destes: `fipe`,
`placa`, ou `marca_modelo` + `ano_modelo`. Sem isso nenhum provedor brasileiro dá
preço, e a API devolve `422` com a frase do que falta, em português.

Resposta (`201`):

```json
{
  "id": 42, "situacao": "cotada", "ramo": "auto", "erro": null,
  "ofertas": [
    {"seguradora": "HDI", "produto": "Auto compreensiva",
     "premio_total_centavos": 280000, "premio_liquido_centavos": 260500,
     "iof_centavos": 19500, "franquia_centavos": 250000, "parcelas": 10,
     "coberturas": [], "validade": null}
  ]
}
```

Três coisas que o integrador precisa saber:

1. **`201` mesmo quando o provedor falha.** Aí vem `"situacao": "falhou"` e um
   `erro` genérico. A cotação existe na conta da corretora — é um lead, e
   devolver `500` faria o site apagar o que ela acabou de ganhar.
2. **Três coisas ficam dentro do sistema**: a comissão (é quanto a corretora
   ganha, e o segurado não vê isso em lugar nenhum do mercado), o nome do
   provedor (é fornecedor dela) e o texto cru do erro, que costuma nomear o
   multicálculo e devolver pedaço da resposta dele. O corretor vê os três na
   tela; o visitante do site, nenhum.
3. **A API não escolhe oferta nem emite.** Escolher é ato comercial, com comissão
   e responsabilidade — acontece na tela de quem responde por ele.

`GET /api/v1/cotacoes/{id}` lê a mesma cotação (mesma chave). Cotação de outra
conta responde `404`, não `403`: `403` já contaria que ela existe.

## O conector

`finance/cotacao_provedores.py`. O padrão é `manual` — sem API contratada, o
corretor digita as ofertas na tela e o resto do fluxo é idêntico. Trocar é
variável de ambiente:

```
COTACAO_PROVEDOR=segfy
COTACAO_SEGFY_BASE_URL=https://...
COTACAO_SEGFY_API_KEY=...            # ou o trio OAuth2 abaixo
COTACAO_SEGFY_TOKEN_URL=...
COTACAO_SEGFY_CLIENT_ID=...
COTACAO_SEGFY_CLIENT_SECRET=...
COTACAO_SEGFY_TENANT=...             # quando o provedor exigir
```

Um conector novo é uma subclasse de `ProvedorHTTP` com dois métodos —
`_payload(risco)` e `_ofertas(resposta)` — e `registrar(MeuProvedor())`. A
mecânica comum (token, cabeçalhos, tempo limite, erro) já está na base.

## A decisão: Segfy (21/09/2026)

**A Liberal não tem tenant na InsureMO**, e sem tenant aquela API não dá preço
nenhum: ela é o núcleo da seguradora, e precisa dos produtos configurados lá
dentro. Decisão do dono, 21/09/2026: **multicálculo brasileiro, e o escolhido é a
Segfy.**

O que foi comparado antes de escolher:

| | Seguradoras | API | Observação |
|---|---|---|---|
| **Segfy** ← escolhido | 19 | API, extensão e robô | Diz não exigir autorização das seguradoras |
| TEx / Teleport | 20+, 50+ produtos | API oficial homologada, precisão garantida em contrato | Serasa Experian; homologação mais lenta |
| Quiver / Agger (ONE) | 40+ | existe, parte dela anunciada como "prevista" | confirmar antes de contratar pensando na API |

**E o plano B já está decidido**: se a Segfy só oferecer API em plano caro, ou só
extensão/robô (que roda no navegador e não dá pra chamar de servidor), **a
corretora fica no modo manual e a gente espera**. A tela funciona hoje: o corretor
digita as ofertas, compara, escolhe e vira proposta na carteira. Quando a API
chegar, nada muda pra ele — as ofertas só passam a chegar sozinhas.

O que falta é fora do código: **contrato, credencial e a documentação da API**.
Nenhum multicálculo brasileiro publica isso aberto. O conector vira
`finance/cotacao_segfy.py` no dia em que a doc chegar, e `COTACAO_PROVEDOR=segfy`
o liga — o resto da base não muda uma linha.

### O que a InsureMO continua fazendo aqui

O conector dela (`finance/cotacao_insuremo.py`) fica, e não custa nada: só
acorda com `COTACAO_PROVEDOR=insuremo`. Ele já autentica (CAS), cota e cria
proposta, e está testado contra as amostras da doc — se um dia uma seguradora ou
MGA da carteira rodar em InsureMO, está pronto. O que falta nele é só a
configuração de produto do tenant.

## InsureMO — o que já está ligado, e o que falta

Em 21/09/2026 a página **Policy Rating API** foi lida (colada à mão: o domínio
está bloqueado pela política de egresso desta máquina). Com ela, o conector
`finance/cotacao_insuremo.py` existe e cobre a cotação.

### A chamada que dá preço

```
POST {server}/quotation/core/quotation/v1/calculate   (sem persistir cotação)
POST {server}/proposal/core/proposal/v1/calculateEx   (sem persistir proposta)
```

O pedido é um objeto de apólice (`ProductCode`, vigência, moeda, `OrgCode`,
`AgentCode`) com o risco em `PolicyLobList[].PolicyRiskList[]`. A resposta é o
mesmo objeto com os prêmios preenchidos em cada nível.

### O mapeamento do dinheiro — e a armadilha

A InsureMO **separa o imposto**, então a comissão sai exata, sem estimativa:

| Campo da InsureMO | Vira | Observação |
|---|---|---|
| `BeforeVatPremium` | `premio_liquido_centavos` | é sobre ele que a comissão incide |
| `Vat` | `iof_centavos` | o imposto |
| `DuePremium` | `premio_total_centavos` | **o total** |
| `CommissionRate` | `comissao_pct` | `0.1` = 10% |
| `ProposalNo` | `ref_externa` | o que a emissão manda de volta |

⚠️ **Não leia o total de `GrossPremium`.** Na mesma página da doc ele troca de
significado: 10.8 **com** imposto no exemplo de proposta, 300 **sem** imposto no
de endosso (onde `Vat` é 24 e `DuePremium` 324). Lê-lo daria prêmio 8% menor e
comissão 8% maior, sem nenhum sinal na tela. `TotalPremium` também não serve:
traz o **juro de parcelamento** (340.2 = 324 + 16.2), e juro não é prêmio.

`tests/test_cotacao_insuremo.py` prende isso com as amostras da própria doc.

### Não é multicálculo — e isso muda a configuração

A Calculate API precifica **um produto de um tenant**. Não existe chamada que
devolva Porto, Allianz e HDI lado a lado. Por isso
`COTACAO_INSUREMO_PRODUTOS` é uma **lista**: o conector faz uma chamada por
produto e cada uma vira uma oferta; falha de um não derruba os outros.

```
COTACAO_PROVEDOR=insuremo
COTACAO_INSUREMO_BASE_URL=https://...
COTACAO_INSUREMO_API_KEY=...          # ou o trio OAuth2 (TOKEN_URL/CLIENT_ID/CLIENT_SECRET)
COTACAO_INSUREMO_ORG=10002
COTACAO_INSUREMO_AGENTE=...           # AgentCode da corretora
COTACAO_INSUREMO_MOEDA=BRL
COTACAO_INSUREMO_PRODUTOS=[{"codigo":"AUTO_BR","versao":"1.0","seguradora":"...",
  "risco":"R10007","coberturas":[{"codigo":"C100692","soma_segurada":100000}],
  "campos_veiculo":{"placa":"LicensePlateNo","fipe":"FipeCode","ano_modelo":"ModelYear"}}]
```

### A autenticação (CAS)

```
POST {server}/cas/ebao/v2/json/tickets    ← usuário e senha, devolve o token
```

É CAS do eBao: **usuário e senha**, não `client_id`/`client_secret`. O token vale
para todas as chamadas do gateway e fica guardado em memória até expirar.

⚠️ A doc diz que o token é *"appended"* às chamadas e **não dá o nome do
cabeçalho**. O conector usa `Authorization: Bearer <token>`, que é o padrão do
gateway — e deixa isso em variável (`COTACAO_INSUREMO_HEADER_TOKEN` e
`_PREFIXO_TOKEN`), então confirmar na coleção do Postman é trocar uma variável,
não mexer em código.

### A emissão — até a proposta, e só

```
POST {server}/proposal/core/proposal/v1/validate    204 = passou · 422 = as mensagens
POST {server}/proposal/core/proposal/v1/createEx    monta a proposta (devolve ProposalNo)
POST {server}/proposal/core/proposal/v1/updateEx    SALVA e sobe a versão
```

`issuePolicyEx` **existe e não é chamado**: emitir a apólice em nome da corretora
é decisão que o dono ainda não tomou ("só proposta, com fallback"). A linha está
a um método de distância.

A validação vem primeiro porque a doc garante o que ela devolve: **204 sem corpo**
quando passa, **422 com as mensagens campo a campo** quando não ("field
EffectiveDate is mandatory"). Uma proposta recusada chega na tela do corretor
dizendo o que falta — e `enviar_para_emissao` cai no roteiro pro portal.

### Por que a cotação usa `calculate` e não `calculateWithPersistence`

A página Quotation dá três caminhos: `calculate` (só preço), `create` (só salva) e
`calculateWithPersistence` (preço + salva + **converte pra apólice**). A tela usa
o primeiro: quem guarda a cotação é esta base, e converter pra apólice a cada
preço consultado criaria lixo no sistema da seguradora toda vez que o corretor
compara opções.

### O que ainda falta

**Uma coisa só: os códigos do produto de auto.** `ProductCode`, o
`ProductElementCode` do risco e os das coberturas saem da configuração do tenant
— a doc é explícita ("you must create your own policy model in data dictionary").
O schema genérico não tem placa, chassi nem FIPE: por isso `campos_veiculo` é um
mapa, e sem ele o conector **avisa no log** que o prêmio não vale pra auto.

O caminho pra obtê-los está na própria doc: **"Create the Data Model for Technical
Product"** e **"Generating Policy API Request Payload"**. Com o payload gerado do
produto de auto em mãos, é preencher um JSON de configuração — nada de código.

## Emissão: o que o botão faz hoje

Onde o provedor aceita receber os dados, manda e guarda o número da proposta.
Onde não aceita — que é o caso de toda seguradora brasileira pro corretor hoje —
`cotacao.roteiro_do_portal` monta o resumo pronto pra digitar no portal dela, na
ordem em que os portais perguntam. Os dois caminhos terminam na MESMA carteira:
não existe "carteira das automáticas" e outra das manuais.
