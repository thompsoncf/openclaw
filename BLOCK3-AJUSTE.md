# Block 3: Ajuste de Produto por Variante SEFAZ

## Resumo
Implementado fluxo completo de refinamento de matching de preços. Usuários podem agora:
1. Ver comparação de preços de suas listas de compras
2. Clicar em "Ajuste" para qualquer produto que "não bateu"
3. Escolher a variante exata do SEFAZ
4. Ver a comparação recalculada com o termo ajustado

## Arquivos Modificados

### 1. web/portal.py
**3.1) compras_precos()**
- Extrai parâmetros `?ajuste=item||termo` via `request.query_params.getlist("ajuste")`
- Constrói dict `escolhas: {item: termo}`
- Passa ao `comparar_separado(pool, pendentes, cidade, escolhas)`

**3.2) compras_opcoes() (novo endpoint)**
- GET `/painel/compras/opcoes?item=<produto>`
- Verifica se SEFAZ disponível para cidade do usuário
- Chama `SefazMenorPreco().opcoes_produto(item, lat, lon, raio)`
- Retorna JSON com lista de variantes:
  ```json
  {
    "opcoes": [
      {"descricao": "ARROZ TIPO 1 5KG", "faixa": "R$ 24,90"},
      {"descricao": "ARROZ INTEGRAL 5KG", "faixa": "R$ 29,90–R$ 34,90"}
    ]
  }
  ```

**3.3) Template _COMPRAS (JS novo)**
- Estado local `ajustesAtivos` para rastrear ajustes
- Função `recarregar()` que reconstrói URL com `?ajuste=` params
- Função `renderizar()` que renderiza comparação + botões de ajuste
- Modal criada dinamicamente ao clicar em "Ajuste"
- Fetch de `/painel/compras/opcoes` para listar variantes
- Recálculo automático ao selecionar variante

### 2. finance/banco_precos.py
**comparar_separado() (já pronto)**
- Aceita parâmetro `escolhas: dict[str, str] | None`
- Substitui itens pelos termos escolhidos antes de buscar
- `termos = [escolhas.get(i, i) for i in itens]`

### 3. finance/sefaz_precos.py
**opcoes_produto() (já pronto)**
- Busca produtos no SEFAZ com `buscar(termo, ...)`
- Agrupa por núcleo normalizado (2 primeiras palavras)
- Rastreia min/max por grupo
- Retorna até 8 variantes ordenadas por preço

## Fluxo End-to-End

```
1. Usuário acessa /painel/compras
   ↓
2. JS chama /painel/compras/precos (sem ajustes)
   ↓
3. Comparação renderiza + botões "Ajuste o produto" aparecem
   ↓
4. Usuário clica "Ajuste" em um item
   ↓
5. Modal abre + fetch /painel/compras/opcoes?item=<produto>
   ↓
6. Variantes aparecem na modal (descricao + faixa de preco)
   ↓
7. Usuário seleciona uma variante
   ↓
8. ajustesAtivos[item] = termo_selecionado
   ↓
9. recarregar() chama /painel/compras/precos?ajuste=item||termo
   ↓
10. Comparação recalcula com termo ajustado
    ↓
11. Resultado mostra comparação mais precisa
```

## Testes

### Estrutura
- `test-block3-logic.py`: Valida parsing, formatação, lógica de fluxo
- `test-block3-ajuste.js`: Testa endpoints em tempo real (quando servidor está rodando)

### Resultado
```
[OK] Test 1: Parsing de ?ajuste= parameters
[OK] Test 2: Formatacao de faixa de preco
[OK] Test 3: Escolhas passthrough a comparar_separado
[OK] Test 4: Estrutura de opcoes_produto()
[OK] Test 5: Logica do JS (pseudocodigo)
[OK] Test 6: Fluxo completo
```

## Validação Manual

Para testar end-to-end:

1. Iniciar servidor
```bash
python web/app.py
```

2. Acessar portal em /painel/compras

3. Adicionar itens (ex: "arroz", "feijao", "sal")

4. Verificar:
   - [ ] Comparação carrega
   - [ ] Botões "Não bateu? Ajuste o produto" aparecem
   - [ ] Click abre modal
   - [ ] Variantes carregam
   - [ ] Seleção recalcula comparação
   - [ ] URL mostra ?ajuste=item||termo

## Edge Cases Cobertos

- **Sem SEFAZ**: compras_opcoes retorna `{"opcoes": []}` se região não cobre
- **Item vazio**: `?ajuste=||termo` ignorado (validação de item.strip())
- **Múltiplos ajustes**: `?ajuste=arroz||A&ajuste=feijao||B` suportado
- **Cancelamento**: Usuário fecha modal sem selecionar
- **Erro de rede**: Fallback de erro em fetch suavemente

## Próximas Etapas (Opcional)

- Persistir ajustes em localStorage (survive refresh)
- Salvar preferências por lista
- Analytics de qual termo foi escolhido
- Sugestões automáticas baseadas em histórico
