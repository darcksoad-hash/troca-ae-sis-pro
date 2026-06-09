# Escopo do ERP para Assistencia Tecnica de Smartphones

## Objetivo
Criar um sistema completo para gerenciar a operacao de uma assistencia tecnica: clientes, smartphones, fabricantes, modelos, pecas, servicos, ordens de servico, estoque, financeiro, relatorios, usuarios, permissoes e auditoria.

## Modulos principais
- Painel geral com OS abertas, valores a receber, saldo financeiro e alertas de estoque.
- Clientes com cadastro, contato, documento, CEP, logradouro, numero, bairro, cidade, UF, complemento e observacoes.
- Dados do smartphone preenchidos diretamente na ordem de servico, com marca, modelo, IMEI, cor, senha/padrao e estado de entrada.
- Fabricantes e modelos de produtos, para vincular cada peca aos aparelhos compativeis.
- Pecas com SKU, categoria, custo medio, preco de venda, fornecedor, estoque atual e estoque minimo.
- Servicos com nome, categoria, valor de mao de obra, prazo medio e garantia.
- Ordens de servico com defeito relatado, fotos/anexos, diagnostico, aprovacao, laudo, pecas usadas, servicos executados, tecnico, status, prioridade, desconto, pagamento, saldo, termo de entrega, garantia e pos-venda.
- Financeiro com entradas, saidas, contas pagas, pendentes, contas a pagar, contas a receber, vencimento e forma de pagamento.
- Relatorios de status de OS, receita por servico, estoque baixo, lucratividade, lucro bruto por OS, resultado mensal e fluxo de caixa.
- Configuracoes com usuarios, perfis, escala de permissoes e auditoria.

## Fluxo operacional recomendado
1. Cadastrar ou localizar o cliente.
2. Abrir a ordem de servico e preencher os dados do aparelho recebido.
3. Registrar defeito relatado e fotos, se houver.
4. Fazer diagnostico e montar orcamento com pecas e servicos.
5. Aprovar ou reprovar o orcamento.
6. Executar o reparo com baixa automatica de estoque.
7. Registrar pagamento total ou parcial.
8. Emitir termo de entrega e garantia.
9. Acompanhar pos-venda e historico do cliente.

## Status sugeridos para OS
- Aberta
- Aguardando diagnostico
- Aguardando aprovacao
- Aprovada
- Em execucao
- Aguardando peca
- Finalizada
- Entregue
- Cancelada

## Financeiro necessario
- Caixa diario.
- Contas a pagar.
- Contas a receber.
- Formas de pagamento: dinheiro, PIX, cartao, boleto e transferencia.
- Registro de entrada por OS.
- Registro de despesas fixas e variaveis.
- Relatorio de lucro bruto por OS.
- Relatorio de resultado mensal.

## Estoque necessario
- Cadastro de fornecedores.
- Entrada de compra.
- Baixa automatica por peca usada em OS.
- Custo medio.
- Estoque minimo.
- Alerta de reposicao.
- Historico de movimentacao.

## Usuarios e permissoes
- Administrador: acesso total.
- Atendente: clientes e abertura de OS com dados do aparelho.
- Tecnico: diagnostico, laudo, execucao e pecas usadas.
- Financeiro: pagamentos, contas e relatorios financeiros.
- Auditoria: registro de criacao, edicao, exclusao, pagamentos, finalizacao de OS e alteracoes de permissoes.

## Fabricantes, modelos e pecas
- Cadastro de fabricantes atendidos.
- Cadastro de modelos por fabricante.
- Vinculacao de pecas ao fabricante.
- Vinculacao de pecas aos modelos compativeis.
- Uso de fabricante e modelo cadastrado dentro da ordem de servico.

## Evolucao tecnica
O prototipo atual salva dados no navegador. Para virar um ERP de producao, o proximo passo e criar:
- Banco de dados.
- Login e permissoes reais.
- Backup automatico.
- API para integracao com WhatsApp, emissao de documentos e relatorios.
- Hospedagem local ou em nuvem.
- Impressao de OS, orcamento, recibo e termo de garantia.

O plano detalhado de producao esta no arquivo `plano-producao-erp-assistencia.md`.
