# Plano para transformar o prototipo em ERP de producao

## Objetivo
Transformar o prototipo atual, que salva dados no navegador, em um ERP real para assistencia tecnica de smartphones, com banco de dados, usuarios, permissoes, backup, integracoes, emissao de documentos e hospedagem segura.

## Arquitetura recomendada
- Frontend web responsivo para uso em computador, tablet e celular.
- Backend com API para regras de negocio, seguranca e integracoes.
- Banco de dados PostgreSQL.
- Armazenamento de arquivos para fotos de entrada, anexos, termos e comprovantes.
- Login com usuarios, perfis e permissoes.
- Auditoria de acoes importantes.
- Rotina de backup automatico.
- Impressao/exportacao de OS, orcamento, recibo e termo de garantia.

## Banco de dados
Tabelas principais:
- usuarios
- perfis
- permissoes
- auditoria
- clientes
- fabricantes
- modelos
- fornecedores
- pecas
- movimentacoes_estoque
- servicos
- ordens_servico
- os_pecas
- os_servicos
- pagamentos
- financeiro
- anexos
- documentos_emitidos

Campos importantes:
- Clientes: nome, telefone, email, documento, CEP, logradouro, numero, bairro, cidade, UF, complemento e observacoes.
- OS: cliente, fabricante, modelo, IMEI, cor, senha/padrao, estado de entrada, defeito, fotos, diagnostico, aprovacao, laudo, status, prioridade, tecnico, garantia, termo de entrega, pagamento e saldo.
- Estoque: SKU, fabricante, modelos compativeis, fornecedor, custo medio, preco, estoque atual, estoque minimo e historico.
- Financeiro: tipo, categoria, vencimento, pagamento, status, forma de pagamento, valor e vinculo com OS.

## Login e permissoes reais
Perfis recomendados:
- Administrador: acesso total.
- Atendente: clientes, abertura de OS e acompanhamento.
- Tecnico: diagnostico, laudo, execucao, pecas usadas e finalizacao tecnica.
- Financeiro: pagamentos, contas a pagar, contas a receber e relatorios financeiros.

Permissoes recomendadas:
- Ver, criar, editar e excluir clientes.
- Abrir, editar, aprovar, reprovar, finalizar e entregar OS.
- Registrar diagnostico e laudo.
- Movimentar estoque.
- Ver custo de pecas.
- Registrar pagamentos.
- Ver relatorios financeiros.
- Gerenciar usuarios e permissoes.
- Acessar auditoria.

## Auditoria
Registrar:
- Login e logout.
- Criacao, edicao e exclusao de clientes.
- Criacao, edicao, aprovacao, reprovacao, finalizacao e entrega de OS.
- Alteracao de valores, descontos e pagamentos.
- Entrada e baixa de estoque.
- Alteracao de permissoes.
- Emissao de documentos.

Cada registro deve guardar:
- Usuario.
- Data e hora.
- Acao executada.
- Tela/modulo.
- Registro afetado.
- Valor anterior e valor novo, quando aplicavel.

## Backup automatico
Rotina minima:
- Backup diario do banco de dados.
- Backup dos anexos e fotos.
- Retencao de 7 dias para backups diarios.
- Retencao de 4 semanas para backups semanais.
- Retencao de 12 meses para backups mensais.
- Teste de restauracao periodico.

Destinos possiveis:
- Servidor local.
- Google Drive, OneDrive ou S3.
- Servidor em nuvem.

## API e integracoes
APIs recomendadas:
- API interna para o frontend.
- API de CEP para preencher endereco.
- API de WhatsApp para envio de orcamento, status da OS e aviso de retirada.
- API para emissao de documentos em PDF.
- API para relatorios e exportacao.

Mensagens WhatsApp sugeridas:
- OS aberta.
- Orcamento aguardando aprovacao.
- Orcamento aprovado.
- Aparelho em execucao.
- Aparelho finalizado.
- Aparelho pronto para retirada.
- Pos-venda.

## Impressao e documentos
Documentos necessarios:
- Ordem de servico.
- Orcamento.
- Recibo.
- Termo de garantia.
- Termo de entrega.
- Etiqueta interna da OS.

Cada documento deve permitir:
- Impressao.
- PDF.
- Numero unico.
- Data de emissao.
- Usuario emissor.
- Registro em auditoria.

## Hospedagem
Opcoes:
- Local: servidor na loja, acesso pela rede interna.
- Nuvem: acesso de qualquer lugar, com backup e seguranca melhores.
- Hibrido: sistema em nuvem com rotina local de contingencia.

Recomendacao inicial:
- Hospedar em nuvem com banco PostgreSQL, HTTPS, backup automatico e acesso por login.

## Fases de desenvolvimento
### Fase 1: Base real do sistema
- Criar backend.
- Criar banco de dados.
- Migrar o prototipo para frontend conectado a API.
- Implementar login.
- Implementar usuarios, perfis e permissoes.

### Fase 2: Operacao da assistencia
- Clientes.
- Fabricantes e modelos.
- Pecas e fornecedores.
- Servicos.
- Ordens de servico completas.
- Fotos/anexos.
- Aprovacao de orcamento.
- Termos e recibos.

### Fase 3: Estoque e financeiro
- Entrada de compra.
- Baixa automatica por OS.
- Custo medio.
- Contas a pagar.
- Contas a receber.
- Pagamentos parciais.
- Caixa diario.

### Fase 4: Relatorios e documentos
- Relatorios operacionais.
- Relatorios financeiros.
- Lucro bruto por OS.
- Resultado mensal.
- Impressao e PDF.
- Exportacao.

### Fase 5: Integracoes e producao
- WhatsApp.
- Backup automatico.
- Auditoria completa.
- Hospedagem.
- Testes.
- Treinamento de uso.

## Prioridade recomendada
1. Banco de dados.
2. Login e permissoes.
3. OS completa.
4. Estoque.
5. Financeiro.
6. Impressao/PDF.
7. Backup.
8. WhatsApp.
9. Hospedagem final.
