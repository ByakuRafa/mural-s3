# Mural de Notas com Foto (Amazon S3 + FastAPI + PostgreSQL)

Projeto de portfólio que implementa um **Mural de Notas com Foto** estilo *post-it / polaroid*, utilizando **FastAPI** no backend, **Amazon S3** (via `boto3`) para armazenamento seguro de imagens com **URLs pré-assinadas**, e **PostgreSQL** (Amazon RDS) para persistência dos metadados.

---

## Arquitetura

```
Usuário (Navegador)
   │
   ├── GET /                    ──▶ FastAPI consulta PostgreSQL/SQLite
   │                                e gera Presigned URLs no S3 (ExpiresIn=3600)
   ├── POST /posts              ──▶ Valida imagem (magic bytes + tamanho),
   │                                envia objeto ao S3 e grava chave no banco
   └── POST /posts/{id}/delete  ──▶ Remove registro no banco e exclui objeto no S3
```

### Principais recursos implementados

- **Upload seguro para S3**: validação de tamanho máximo (`MAX_UPLOAD_MB`) e verificação da assinatura binária (*magic bytes*) para aceitar apenas `JPG`, `PNG`, `WEBP` e `GIF`, salvando com chave única `uploads/<uuid>.<ext>` e `ContentType` correto.
- **URLs pré-assinadas (`Presigned URLs`)**: o bucket S3 permanece **100% privado**; o servidor gera URLs temporárias de leitura para exibir as imagens no mural.
- **CRUD completo no S3 + Banco**: criação de notas (título, autor, cor do cartão, texto e foto), busca por texto, filtro por cor e remoção sincronizada (apaga do banco e executa `delete_object` no S3).
- **Proteção contra XSS**: renderização com templates Jinja2 (*auto-escape* ativo) em vez de interpolação direta em HTML.
- **Observabilidade**: endpoint `GET /health` verificando conectividade real com banco de dados e bucket S3, além de `GET /api/posts` em JSON.
- **Interface acessível**: modal nativo `<dialog>` para confirmação de remoção, pré-visualização da imagem antes do envio e validação progressiva de formulário.

---

## Como testar e simular localmente (sem conta AWS)

O projeto foi preparado para rodar e ser testado localmente de **3 formas diferentes**:

### 1. Testes automatizados com `pytest` + `moto` (zero infraestrutura)

A biblioteca `moto` intercepta chamadas do `boto3` em memória simulando a API real do Amazon S3, enquanto os testes usam um banco SQLite temporário.

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements-dev.txt
pytest -v
```

### 2. Simulação completa da AWS localmente com Podman (LocalStack + PostgreSQL)

Você pode subir um simulador real de **S3 (LocalStack)** e um **PostgreSQL** em containers usando `podman` (ou `docker`) e rodar a aplicação conectada a eles:

```bash
# Suba os serviços de simulação (LocalStack na porta 4566 e Postgres na porta 5432)
podman run -d --name mural-localstack -p 4566:4566 -e SERVICES=s3 docker.io/localstack/localstack:3.8
podman run -d --name mural-postgres -p 5432:5432 -e POSTGRES_DB=mural -e POSTGRES_USER=postgres -e POSTGRES_PASSWORD=postgres docker.io/library/postgres:16-alpine
```

Ou usando `compose.yaml` com `podman compose up -d` / `docker compose up -d`.

Em seguida, rode o servidor apontando para os simuladores:

```bash
export STORAGE_BACKEND=s3
export S3_BUCKET=mural-notas-dev
export S3_ENDPOINT_URL=http://localhost:4566
export S3_PUBLIC_ENDPOINT_URL=http://localhost:4566
export S3_AUTO_CREATE_BUCKET=true
export AWS_ACCESS_KEY_ID=test
export AWS_SECRET_ACCESS_KEY=test
export DB_BACKEND=postgres
export DB_HOST=localhost
export DB_NAME=mural
export DB_USER=postgres
export DB_PASS=postgres

.venv/bin/uvicorn app:app --reload
```

Acesse `http://127.0.0.1:8000` no navegador. O bucket é criado automaticamente no startup e as imagens enviadas ficam armazenadas no S3 simulado pelo LocalStack.

### 3. Modo Preview Offline (sem containers e sem AWS)

Para visualizar rapidamente a interface e testar o fluxo sem subir LocalStack/Postgres:

```bash
STORAGE_BACKEND=local DB_BACKEND=sqlite .venv/bin/uvicorn app:app --reload
```

---

## Variáveis de ambiente para produção (AWS EC2 + RDS + S3)

Na AWS, basta configurar as variáveis abaixo (usando IAM Role na EC2, sem precisar de chaves estáticas):

```bash
STORAGE_BACKEND=s3
S3_BUCKET=seu-bucket-privado
AWS_REGION=us-east-1
DB_BACKEND=postgres
DB_HOST=seu-endpoint.rds.amazonaws.com
DB_NAME=postgres
DB_USER=seu_usuario
DB_PASS=sua_senha
```
