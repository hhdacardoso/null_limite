# Null Limite - S1 Fundação

Backend mínimo para o HackatOhm CEFET-MG. Sprint 1: Auth, cadastro candidato, upload PDF, storage, modelo DB.

## Stack
- **FastAPI** 0.162 (Python 3.10+, testado em 3.14)
- **SQLAlchemy** 2.1 (sync, SQLite)
- **Pydantic** 2.14
- **python-jose** + **bcrypt** (JWT + hash de senha)
- **aiofiles** (async file upload)
- **python-dotenv** (lê `.env`)

## Como rodar

```bash
# 1. Clone/entre na pasta
cd null_limite

# 2. Instale dependências (use venv recomendado)
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# 3. Rode o servidor
uvicorn main:app --reload --port 8000

# 4. Acesse
# - Swagger UI: http://localhost:8000/docs
# - ReDoc: http://localhost:8000/redoc
# - Health: http://localhost:8000/health
```

## Endpoints

### Auth
- `POST /auth/register` - Cadastra profissional (email, nome, cargo opcional, senha)
- `POST /auth/login` - Login (form OAuth2, retorna JWT)
- `GET /auth/me` - Perfil do usuário logado (Bearer token)

### Perfil do profissional (`/professionals/me`)
- `PATCH /professionals/me` - Atualiza nome/cargo
- `POST|GET /professionals/me/resumes` - Upload (PDF, retorna 202) / lista currículos
- `POST|GET /professionals/me/certificates` - Certificações (PMP, ITIL...)
- `POST|GET /professionals/me/projects` - Experiência em projetos (cliente, setor, papel, período)
- `POST|GET /professionals/me/education` - Formação (`technical`, `bachelor`, `specialization`, `mba`, `master`, `doctorate`)
- `POST|GET /professionals/me/evidences` - Comprovantes (PDF/PNG/JPG). Upload multipart com `target_type` (`certificate`|`project`|`education`), `target_id` e `file`

### Validação de comprovantes (gestor)
- `GET /evidences?status=pending` - Fila de comprovantes para revisar
- `POST /evidences/{id}/review` - Aprova ou rejeita (`{"status": "approved"|"rejected", "note": "..."}`). Gestor não revisa o próprio comprovante
- `GET /evidences/{id}/file` - Baixa o arquivo (só o dono ou gestor)

Gestores são os emails listados em `MANAGER_EMAILS` no momento do cadastro.

### Health
- `GET /health` - Status do serviço

## Banco de dados
SQLite local (`null_limite.db`) criado automaticamente na primeira execução. Tabelas:
- `professionals` - usuários (papel `professional` ou `manager`)
- `resumes` - PDFs uploadados
- `certificates`, `projects`, `education` - itens do perfil que pontuam em editais
- `evidences` - comprovante de um item do perfil, com status `pending`/`approved`/`rejected`

Não há migrations: ao mudar o modelo, apague `null_limite.db` para recriar.

## Privacidade (LGPD)
Uploads não são servidos publicamente. Comprovantes só são baixados pelo dono ou por gestores via endpoint autenticado.

## Estrutura
```
null_limite/
├── main.py              # App completo (single-file)
├── requirements.txt     # Dependências
├── run.sh               # Script de inicialização
├── .gitignore
├── null_limite.db       # SQLite (criado em runtime)
├── uploads/             # PDFs salvos (criado em runtime)
└── README.md
```

## Variáveis de ambiente (opcional)
Crie `.env`:
```env
DATABASE_URL=sqlite:///./null_limite.db
SECRET_KEY=sua-chave-secreta-forte
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=10080
UPLOAD_DIR=./uploads
MAX_FILE_SIZE=10485760
MANAGER_EMAILS=gestor@ex.com,outro@ex.com
```

## Testando rápido (curl)

```bash
# Registra
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"test@ex.com","full_name":"Teste","job_title":"Gerente de Projetos","password":"123456"}'

# Login (pega token)
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=test@ex.com&password=123456"

# Upload PDF (use token do login)
curl -X POST http://localhost:8000/professionals/me/resumes \
  -H "Authorization: Bearer SEU_TOKEN" \
  -F "file=@curriculo.pdf"

# Lista
curl -H "Authorization: Bearer SEU_TOKEN" \
  http://localhost:8000/professionals/me/resumes
```

## Próximas sprints (não implementadas)
- **S2**: Parser IA (pdfplumber/spaCy/LLM) → extração JSON + vetorização
- **S3**: CRUD vagas + motor de matching + ranking + breakdown
- **S4**: Gamificação (pontos, badges, leaderboard, freshness no score)
- **S5**: Explicabilidade do score, seed demo, pitch