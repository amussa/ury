# Preservação do total comercial no fecho POS

A Sales Invoice consolidada conserva a soma dos totais finais das POS Invoices
de origem. Diferenças decorrentes do arredondamento das bases/impostos usam os
campos nativos `rounded_total`, `rounding_adjustment` e respectivos valores na
moeda da empresa. O ERPNext continua responsável pelo GL, troco, saldo pendente,
cancelamento e stock.

Exemplo que motivou a correcção: 23 vendas, 79 linhas, total comercial 21.210 MT;
ao separar IVA incluído, a soma das bases/impostos ficou em 21.209,94 MT. O
arredondamento nativo de imposto incluído só compensava até 0,05 MT e a diferença
de 0,06 MT acabava como troco. Agora o total final é 21.210 MT, com ajuste 0,06 MT
na conta de arredondamentos e troco zero quando recebido exactamente esse valor.

## Implementação

`ury.ury_pos.consolidation_rounding.URYSalesInvoice` estende Sales Invoice através
de `override_doctype_class`. Só altera o cálculo para `is_consolidated` e `is_pos`.
As restantes facturas delegam integralmente no método herdado.

O calculador especializado define o total arredondado antes de o fluxo nativo
calcular recebimentos, troco, saldo pendente e write-off. Não usa o pagamento
como total, preservando vendas a crédito e troco legítimo.

Guardas:

- documentos de origem submetidos, da mesma empresa, moeda, câmbio e tipo de retorno;
- todas as linhas de origem presentes exactamente uma vez, com o mesmo artigo,
  quantidade e valor líquido usado pelo mapeamento nativo;
- diferença limitada ao arredondamento já existente nas origens mais o erro
  máximo das precisões por linha e imposto;
- diferenças superiores à tolerância são rejeitadas, nunca absorvidas;
- o cálculo mantém preços decimais legítimos: não arredonda arbitrariamente o
  total ao inteiro mais próximo;
- não altera a configuração global de impostos nem o perfil POS.

## Validação

Unitários: `ury.ury_pos.test_consolidation_rounding`, incluindo o caso 0,06,
residual negativo, retorno, câmbio, oferta zero, preço decimal, crédito e
rejeição de linhas alteradas/duplicadas/omitidas ou origem cancelada.

Regressões: `test_credit_consolidation`, `test_settlement`,
`ury.ury.hooks.test_ury_sales_invoice_credit` e
`ury.ury.hooks.test_ury_pos_closing_entry`.

Antes da publicação foi restaurado um backup de produção num site descartável
`rounding.localhost`, numa rede interna sem scheduler periódico. O calculador de
impostos do clone foi alinhado byte a byte com o de produção. O fecho afectado
foi cancelado e emendado pelo fluxo nativo, com consolidação real em background:
24 referências únicas, total final 21.420 MT, troco zero, GL equilibrado,
Numerário sem saída artificial, ajuste na conta Arredondar e reprodução exacta
das quantidades e valorização de stock da consolidação original.

Não são necessárias migrações de schema. Aplicar imagem imutável, limpar cache
de hooks e recriar os processos da aplicação. Facturas antigas não são alteradas
automaticamente pela instalação; qualquer regularização exige backup e análise
das dependências, seguida dos fluxos nativos de cancelamento/emenda.
