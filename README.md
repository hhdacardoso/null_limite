# Null Limite - S1 Fundação

Backend mínimo para o HackatOhm CEFET-MG. Sprint 1: Auth, cadastro candidato, upload PDF, storage, modelo DB.

## Stack
- **FastAPI** 0.109 (Python 3.10+)
- **SQLAlchemy** 1.4 (sync, SQLite)
- **Pydantic** 1.10
- **python-jose** + **passlib** (JWT + bcrypt)
- **aiofiles** (async file upload)

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

## Endpoints (S1)

### Auth
- `POST /auth/register` - Cadastra candidato (email, nome, senha)
- `POST /auth/login` - Login (form OAuth2, retorna JWT)
- `GET /auth/me` - Perfil do usuário logado (Bearer token)

### Currículos (Resumes)
- `POST /candidates/me/resumes` - Upload PDF (multipart/form-data, retorna 202)
- `GET /candidates/me/resumes` - Lista currículos do usuário

### Certificados
- `POST /candidates/me/certificates` - Adiciona certificado
- `GET /candidates/me/certificates` - Lista certificados

### Health
- `GET /health` - Status do serviço

## Banco de dados
SQLite local (`null_limite.db`) criado automaticamente na primeira execução. Tabelas:
- `candidates` - usuários
- `resumes` - PDFs uploadados
- `certificates` - certificados do candidato

## Estrutura
```
null_limite/
├── main.py              # App completo (single-file)
├── requirements.txt     # Dependências
├── run.sh               # Script de inicialização
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
```

## Testando rápido (curl)

```bash
# Registra
curl -X POST http://localhost:8000/auth/register \
  -H "Content-Type: application/json" \
  -d '{"email":"test@ex.com","full_name":"Teste","password":"123456"}'

# Login (pega token)
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/x-www-form-urlencoded" \
  -d "username=test@ex.com&password=123456"

# Upload PDF (use token do login)
curl -X POST http://localhost:8000/candidates/me/resumes \
  -H "Authorization: Bearer SEU_TOKEN" \
  -F "file=@curriculo.pdf"

# Lista
curl -H "Authorization: Bearer SEU_TOKEN" \
  http://localhost:8000/candidates/me/resumes
```

## Próximas sprints (não implementadas)
- **S2**: Parser IA (pdfplumber/spaCy/LLM) → extração JSON + vetorização
- **S3**: CRUD vagas + motor de matching + ranking + breakdown
- **S4**: Gamificação (pontos, badges, leaderboard, freshness no score)
- **S5**: Explicabilidade do score, seed demo, pitch