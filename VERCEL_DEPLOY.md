# Deploy no Vercel

Este projeto esta preparado para rodar no Vercel usando:

- Python Function em `api/index.py`
- Banco PostgreSQL externo, recomendado Neon
- Token de sessao assinado, compativel com ambiente serverless

## Variaveis obrigatorias

Configure no Vercel, em Project Settings > Environment Variables:

```text
DATABASE_URL=postgresql://...
APP_ENV=production
APP_URL=https://seu-projeto.vercel.app
SESSION_SECRET=gere-uma-chave-grande-e-segura
```

## Variaveis opcionais para e-mail

```text
SMTP_HOST=
SMTP_PORT=587
SMTP_USER=
SMTP_PASSWORD=
SMTP_FROM=
SMTP_SSL=false
```

## Observacoes importantes

- Uploads e backups locais em Vercel usam armazenamento temporario. Para producao final, fotos e backups devem ir para storage externo, como Cloudflare R2, S3 ou Supabase Storage.
- O banco deve continuar no Neon PostgreSQL.
- Depois do deploy, abra `/api/health` para validar o banco.
