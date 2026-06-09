# Troca Ae SIS PRO - caminho para app de producao

Este arquivo define a proxima etapa para transformar o ERP em app instalavel e pronto para producao.

## Etapa 1 - Web App/PWA

Objetivo: usar o sistema pelo navegador e permitir instalacao como app no desktop/celular.

Entregue nesta base:

- Manifest PWA com nome, tema, icone e atalhos.
- Service worker para cache da interface.
- Layout responsivo.
- API de saude em `/api/health`.
- Configuracao por variaveis `DATABASE_URL`, `HOST` e `PORT`.

Para instalar no celular ou desktop:

1. Abrir o sistema em HTTPS quando estiver hospedado.
2. No Chrome/Edge, escolher "Instalar app" ou "Adicionar a tela inicial".
3. Usar o app como janela independente.

## Etapa 2 - Backend profissional

Objetivo: separar melhor a API, regras de negocio e banco.

Prioridade recomendada:

1. PostgreSQL em producao.
2. HTTPS e dominio proprio.
3. Autenticacao por token com expiracao e renovacao.
4. Rotas por modulo:
   - `/api/clients`
   - `/api/orders`
   - `/api/parts`
   - `/api/finance`
   - `/api/reports`
   - `/api/users`
5. Logs e auditoria centralizados.
6. Backup automatico externo.

## Etapa 3 - Hospedagem

Opcoes boas:

- Servidor VPS Windows/Linux.
- Render, Railway, Fly.io ou similar.
- Docker com PostgreSQL separado.

## Deploy gratuito recomendado na Render

Arquivos ja preparados:

- `render.yaml`: cria somente o Web Service gratuito.
- `runtime.txt`: fixa a versao do Python.
- `.env.example`: mostra as variaveis para local/producao.
- `/api/health`: rota de monitoramento usada pela Render.

Para manter sem pagamento na Render, o banco deve ficar fora da Render, em um PostgreSQL gratuito como Neon ou Supabase. Nao use disco persistente da Render no plano gratis.

Na Render:

1. Conectar um repositorio Git com este projeto.
2. Usar "New" > "Blueprint".
3. Selecionar o repositorio.
4. Confirmar o `render.yaml`.
5. Informar a variavel `DATABASE_URL` com a conexao do PostgreSQL externo.
6. Aguardar criar o Web Service `troca-ae-sis-pro`.

Variaveis que o Blueprint configura:

```text
DATABASE_URL=preencher com a conexao do PostgreSQL externo
HOST=0.0.0.0
APP_ENV=production
```

Depois do primeiro deploy, abrir:

```text
https://seu-servico.onrender.com/api/health
```

Se responder `ok: true`, o app e o banco estao ativos.

Variaveis principais:

```text
DATABASE_URL=postgresql://usuario:senha@host:5432/troca_ae
HOST=0.0.0.0
PORT=5050
APP_STORAGE_ROOT=
```

Observacao importante: no plano gratuito da Render, os arquivos enviados para a pasta local do servidor nao sao armazenamento definitivo. O banco PostgreSQL externo guarda os dados principais. Para fotos de OS, backups e anexos em producao, a etapa correta e ligar um armazenamento externo, como Supabase Storage, Cloudinary, S3 ou outro servico equivalente.

## Etapa 4 - App desktop ou mobile

Depois do PWA estabilizado:

- Desktop: empacotar com Tauri ou Electron.
- Android/iOS: empacotar com Capacitor.

Recomendacao: manter o PWA como base principal e empacotar o mesmo sistema. Assim o ERP nao precisa ser refeito para cada plataforma.

## Checklist antes de vender/usar em producao

- Trocar a senha do administrador inicial.
- Usar PostgreSQL.
- Configurar HTTPS.
- Fazer backup diario externo.
- Testar restauracao de backup.
- Testar permissoes por perfil.
- Testar impressao de OS, garantia e relatórios.
- Criar ambiente de teste separado do ambiente real.
