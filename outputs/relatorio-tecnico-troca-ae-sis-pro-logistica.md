# Relatorio tecnico - Troca Ae SIS PRO

## Estrutura de pastas

- `api/`: adaptador serverless do Vercel. O arquivo `api/index.py` carrega o backend em Python e aponta a pasta publica raiz para o deploy.
- `public/`: frontend publicado no Vercel, com `index.html`, `service-worker.js`, `manifest.json` e logo.
- `outputs/troca-ae-sis-pro-app/`: aplicacao principal de producao, contendo `server.py`, schemas SQL, assets publicos e arquivos de apoio.
- `outputs/troca-ae-sis-pro-app/data/`: banco SQLite legado local e arquivos de restore locais.
- `outputs/troca-ae-sis-pro-app/backups/`: backups gerados pelo sistema.
- `.vercel/`: vinculacao local do projeto no Vercel.
- `.github/`: configuracoes de repositorio.
- Arquivos raiz como `vercel.json`, `requirements.txt`, `Dockerfile`, `render.yaml` e `cloudflare-worker.js` registram as tentativas/estrategias de deploy.

## Banco de dados

O sistema suporta dois motores:

- SQLite local: `outputs/troca-ae-sis-pro-app/data/troca_ae.db`.
- PostgreSQL em producao: ativado quando `DATABASE_URL` comeca com `postgres://` ou `postgresql://`.

O schema principal fica em:

- `outputs/troca-ae-sis-pro-app/schema.sql`
- `outputs/troca-ae-sis-pro-app/schema.postgresql.sql`

Tabelas existentes:

- `roles`, `users`
- `company_settings`
- `clients`
- `manufacturers`, `product_models`
- `suppliers`
- `parts`
- `purchase_entries`, `purchase_items`
- `services`
- `service_orders`
- `order_parts`, `order_services`, `order_status_history`, `order_photos`
- `finance_entries`, `cash_sessions`
- `stock_movements`
- `pos_sales`, `pos_sale_items`
- `audit_logs`

## Framework utilizado

O backend nao usa Flask/Django/FastAPI. Ele usa `http.server.BaseHTTPRequestHandler` em Python, com uma classe `App` que implementa `GET`, `POST` e `DELETE`.

O frontend e HTML/CSS/JavaScript puro em `public/index.html`, sem React/Vue/Angular.

No Vercel, `api/index.py` transforma a classe `App` em handler serverless.

## Rotas existentes

Rotas publicas principais:

- `GET /api/health`
- `POST /api/login`
- `POST /api/logout`
- `POST /api/auth/request-password-reset`
- `POST /api/auth/reset-password`
- `POST /api/auth/verify-email`
- `POST /api/auth/resend-verification`

Rotas protegidas:

- `GET /api/me`
- `GET /api/dashboard`
- `GET /api/bootstrap` e `/api/bootstrap-lite`
- `GET /api/page-data/<pagina>`
- `GET /api/orders`, `GET /api/orders/<id>`
- `GET /api/finance`
- `GET /api/parts-list`
- `GET /api/order-parts`
- `GET /api/backups`, `GET /api/backups/download`
- `POST /api/clients`, `/api/manufacturers`, `/api/models`, `/api/parts`, `/api/services`
- `POST /api/orders`, `/api/orders/<id>`, `/api/orders/<id>/payment`, `/api/orders/<id>/finish`, `/api/orders/<id>/photos`, `/api/orders/<id>/approve`, `/api/orders/<id>/deliver`
- `POST /api/purchases`, `/api/pos-sales`, `/api/finance`, `/api/cash-sessions`
- `POST /api/users`, `/api/users/<id>/unlock`, `/api/roles`, `/api/company`
- `POST /api/backups`, `/api/backups/restore`
- `DELETE /api/<modulo>/<id>`

## Modulos existentes

No menu lateral:

- Painel
- Clientes
- Ordens de servico
- Fabricantes
- Pecas
- Servicos
- PDV
- Financeiro
- Relatorios
- Configuracoes

## Modelo de autenticacao

O login valida e-mail e senha contra `users`.

Senha:

- Hash PBKDF2-HMAC-SHA256 com salt.
- Politica minima de senha no backend.

Sessao:

- Em producao, token assinado HMAC com `SESSION_SECRET` ou `DATABASE_URL`.
- O token contem `user_id` e expiracao.
- Em desenvolvimento, tambem existe armazenamento em memoria `SESSIONS`.

Seguranca operacional:

- Bloqueio por tentativas falhas.
- Confirmacao de e-mail por token.
- Redefinicao de senha por token.
- Auditoria de login, alteracoes e acoes importantes.

Permissoes:

- `roles.permissions` guarda JSON por modulo.
- Administrador tem nivel 100 e acesso total.
- `require_permission(user, modulo, acao)` bloqueia chamadas nao permitidas.

## Estrutura das Ordens de Servico

Tabela principal: `service_orders`.

Campos principais:

- Numero da OS: `number`
- Cliente: `client_id`
- Aparelho: fabricante/modelo cadastrados e campos livres de marca/modelo
- IMEI, cor, senha e estado de entrada
- Status e status de aprovacao
- Prioridade, abertura e prazo
- Tecnico
- Defeito, diagnostico, solucao
- Observacoes/fotos, termo de garantia, termo de entrega e pos-venda
- Assinatura de aprovacao e entrega
- Desconto, valor pago e timestamps

Tabelas relacionadas:

- `order_parts`: pecas usadas, preco, custo, garantia por peca.
- `order_services`: servicos executados, mao de obra, garantia.
- `order_status_history`: historico de status.
- `order_photos`: fotos anexadas.
- `finance_entries`: pagamentos vinculados a OS.
- `stock_movements`: baixa de estoque ao finalizar OS.

## Melhor estrategia para logistica e coleta/entrega

O melhor caminho e criar um modulo independente, mas integrado por chave opcional `order_id` em cada solicitacao logistica.

Modelo recomendado:

- `delivery_drivers`: motoboys/entregadores.
- `delivery_requests`: solicitacoes de coleta, devolucao e entrega.
- `delivery_events`: historico de status e eventos da entrega.
- `delivery_settings`: configuracao de frete por km.

Integracao com OS:

- A OS continua sendo a fonte do reparo.
- A entrega referencia a OS quando existir.
- O modulo logistico acompanha endereco, cliente, prazo, motoboy, distancia, valor do frete, status e comprovante/documentos.
- Historico logistico fica separado do `order_status_history`, mas pode ser exibido no detalhe da OS futuramente.

Vantagens:

- Nao altera Clientes, OS, Financeiro, Pecas e Servicos.
- Evita acoplar logistica diretamente no fluxo de reparo.
- Permite entregas sem OS no futuro, se a loja vender acessorios pelo PDV.
- Permite faturamento de frete futuramente no Financeiro sem obrigar isso agora.

Etapas sugeridas:

1. Criar tabelas e permissao `logistica`.
2. Criar rotas `/api/delivery` e `/api/page-data/delivery`.
3. Adicionar tela "Coleta e Entrega" no menu.
4. Mostrar dashboard, solicitacoes, motoboys, documentos, frete por km e historico.
5. Em uma etapa futura, adicionar botao dentro da OS para "Solicitar coleta/entrega".
