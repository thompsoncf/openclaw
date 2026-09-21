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

## ⚠️ O que falta pra plugar a InsureMO

Em 21/09/2026 `docs.insuremo.com` e `insuremo.com` estavam **bloqueados pela
política de egresso** da máquina em que isto foi escrito (`EGRESS_BLOCKED` no
proxy), então a documentação não pôde ser lida e **o conector dela não foi
escrito**: mapa de campos de memória é contrato inventado, que passa no teste e
falha na primeira chamada real.

As quatro perguntas que a doc precisa responder — com elas, o conector é um
arquivo:

1. **Autenticação** — URL do token, se é OAuth2 `client_credentials`, e quais
   cabeçalhos de tenant/produto acompanham cada chamada.
2. **Cotação** — caminho do endpoint e o JSON do risco. Em especial: como o
   veículo é identificado (FIPE? placa? código interno do produto?).
3. **Resposta** — onde está o prêmio e **se o IOF vem separado**. Isto decide se
   a comissão pode ser estimada: sem a separação, `finance/cotacao.py` deixa a
   estimativa em branco de propósito, porque rachar o total por um IOF chutado
   inventaria comissão.
4. **Emissão** — se existe endpoint de proposta e o que ele devolve.

Vale o mesmo pra Segfy, Quiver e TEx/Teleport, que são o caminho mais provável no
Brasil: a InsureMO é middleware vendido pra seguradora/MGA, e pra corretora usar
alguém precisa ter os produtos configurados lá dentro.

## Emissão: o que o botão faz hoje

Onde o provedor aceita receber os dados, manda e guarda o número da proposta.
Onde não aceita — que é o caso de toda seguradora brasileira pro corretor hoje —
`cotacao.roteiro_do_portal` monta o resumo pronto pra digitar no portal dela, na
ordem em que os portais perguntam. Os dois caminhos terminam na MESMA carteira:
não existe "carteira das automáticas" e outra das manuais.
