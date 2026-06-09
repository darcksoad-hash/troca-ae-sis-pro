# Troca Ae SIS PRO

Base do ERP com banco de dados, login, permissoes, backup e preparacao para producao.

## O que esta versao entrega

- Servidor local em Python.
- SQLite para uso local e desenvolvimento.
- Modo PostgreSQL para producao usando `DATABASE_URL`.
- Login com sessao, troca de senha, bloqueio por tentativas e auditoria.
- Cadastros de clientes, fabricantes, modelos, pecas, fornecedores, servicos, usuarios e perfis.
- Ordens de servico com dados do aparelho, pecas, servicos, fotos, pagamento, finalizacao e impressao.
- Financeiro, relatorios, estoque e exportacao.
- Backup automatico diario ao iniciar o sistema.
- Backup manual pela tela de Configuracoes.
- Restauracao de backup pela tela de Configuracoes.

## Como abrir local

No terminal, dentro desta pasta:

```powershell
.\start.ps1
```

Depois abra:

```text
http://127.0.0.1:5050
```

## Acesso inicial

```text
E-mail: admin@troca-ae.local
Senha: admin123
```

## Produção com PostgreSQL

Instale as dependencias:

```powershell
pip install -r requirements.txt
```

Configure a variavel:

```text
DATABASE_URL=postgresql://usuario:senha@host:5432/troca_ae
```

Para backup e restauracao em PostgreSQL, o servidor precisa ter `pg_dump` e `pg_restore` instalados. Se eles nao estiverem no PATH, configure `PG_DUMP_PATH` e `PG_RESTORE_PATH`.

O arquivo `.env.example` mostra os campos recomendados para a hospedagem.

## Proximo passo recomendado

1. Hospedar como Web App/PWA com HTTPS.
2. Usar PostgreSQL com `DATABASE_URL`.
3. Criar rotina externa de backup em armazenamento separado.
4. Empacotar como app desktop ou mobile depois que o PWA estiver aprovado.

O roteiro completo esta em `APP_PRODUCAO.md`.

## Monitoramento

Use a rota abaixo para verificar se o app e o banco estao ativos:

```text
GET /api/health
```
