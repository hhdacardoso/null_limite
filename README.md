# Null Limite

Solução para o **Case B da EloGroup (HackatOhm CEFET-MG 2026)**: gestão inteligente de currículos da **Nexora**, uma consultoria fictícia com mais de 1.000 profissionais.

## O problema

Boa parte da receita nova da Nexora vem de **licitações e concorrências privadas**. A nota técnica da proposta depende da **equipe indicada**: formação, certificações e experiência somam pontos, desde que **comprovadas** (diploma, certificado, atestado). Exemplo de critérios de um edital:

| Critério | Pontos |
|---|---|
| Gerente de projeto com certificação PMP | até 10 |
| Experiência comprovada em projetos do setor de energia | 2 por projeto, até 8 |
| Pós-graduação na área do objeto | 5 |
| Mais de 10 anos de experiência profissional | 5 |

Hoje os currículos estão espalhados, sem padrão e desatualizados. São cerca de 40 disputas por ano; em cada uma, 2 a 3 gestores gastam de 15 a 25 horas só localizando e ajustando currículos.

**Pergunta-desafio:** como encontrar rápido os profissionais mais aderentes a cada disputa, apresentar a experiência deles da melhor forma e manter a base **viva e atualizada**?

## A proposta

1. **Perfil estruturado e comprovado:** cada profissional mantém certificações, formação e projetos (com setor), e anexa comprovantes que um gestor valida.
2. **Matching com editais:** o edital vira critérios com pesos; o sistema simula a nota técnica e recomenda a equipe que soma mais pontos, mostrando o detalhamento por critério.
3. **Gamificação para manter a base viva:** o profissional atualiza porque vê o efeito (perfil atualizado, comprovado, que entra em propostas), não por obrigação.
4. **LGPD por padrão:** dados de demo anonimizados, arquivos nunca públicos, acesso só do dono e de gestores.

## Status

| Etapa | Conteúdo | Status |
|---|---|---|
| S1 | Auth (JWT), perfil, upload de currículo, certificados | ✅ |
| S1.5 | Modelo de domínio: projetos, formação, comprovantes e revisão por gestor | ✅ |
| S2 | Base de demo: anonimização, estruturação com LLM, seed | ✅ (o parser de currículo enviado pelo app ainda não existe) |
| S3 | Editais com critérios e pesos, motor de pontuação, ranking e equipe recomendada | ⏳ |
| S4 | Gamificação: atualização do perfil, pontos, badges, ranking por equipe | ⏳ |
| S5 | Explicabilidade, resumo de 2 páginas e vídeo-pitch | ⏳ |

## Como rodar

Precisa de Python 3.10+ (testado em 3.14). Sempre use a `.venv` do projeto: o `uvicorn` do sistema não tem as dependências.

```bash
./run.sh
```

O script cria a `.venv`, instala as dependências, roda o seed na primeira vez e sobe o servidor. O passo a passo manual é este:

```bash
python -m venv .venv
source .venv/bin/activate          # em todo terminal novo
pip install -r requirements.txt
python scripts/seed.py             # dados de demo (apaga o banco atual)
uvicorn main:app --reload --port 8000
```

- Swagger UI: http://localhost:8000/docs
- Health: http://localhost:8000/health

### Logins de demo (depois do seed)

| Papel | Login | Senha |
|---|---|---|
| Gestor | `gestor1@nexora.demo`, `gestor2@nexora.demo` | `demo123` |
| Profissional | `nome.sobrenome@nexora.demo` (100 perfis; veja em `data/profiles/` o campo `full_name`) | `demo123` |

No Swagger, clique em **Authorize** e entre com o e-mail no campo `username`.

## Endpoints

### Auth
- `POST /auth/register`: cadastra profissional (email, nome, cargo, anos de experiência, senha de 6 a 72 caracteres)
- `POST /auth/login`: login (form OAuth2, retorna JWT)
- `GET /auth/me`: perfil do usuário logado

### Perfil do profissional (`/professionals/me`)
- `PATCH /professionals/me`: atualiza nome, cargo, anos de experiência
- `POST|GET /professionals/me/resumes`: upload de currículo em PDF (retorna 202) e listagem
- `POST|GET /professionals/me/certificates`: certificações (PMP, ITIL...)
- `POST|GET /professionals/me/projects`: projetos (cliente, setor, papel, período)
- `POST|GET /professionals/me/education`: formação (`technical`, `bachelor`, `specialization`, `mba`, `master`, `doctorate`)
- `POST|GET /professionals/me/evidences`: comprovantes (PDF/PNG/JPG), upload multipart com `target_type` (`certificate`|`project`|`education`), `target_id` e `file`

### Validação de comprovantes (gestor)
- `GET /evidences?status=pending`: fila de comprovantes para revisar
- `POST /evidences/{id}/review`: aprova ou rejeita (`{"status": "approved"|"rejected", "note": "..."}`); o gestor não revisa o próprio comprovante
- `GET /evidences/{id}/file`: baixa o arquivo (só o dono ou um gestor)

Fora do seed, viram gestores os e-mails listados em `MANAGER_EMAILS` no momento do cadastro.

## Banco de dados

SQLite local (`null_limite.db`), criado na primeira execução.

| Tabela | Conteúdo |
|---|---|
| `professionals` | usuários, com papel `professional` ou `manager` e anos de experiência |
| `resumes` | PDFs enviados |
| `certificates`, `projects`, `education` | itens do perfil que pontuam em editais |
| `evidences` | comprovante de um item do perfil, com status `pending`/`approved`/`rejected` |

Não há migrations: ao mudar o modelo, rode `python scripts/seed.py` (ou apague o `null_limite.db`).

Para mandar o banco a um colega, prefira que ele rode o seed. Se mandar o `.db`, mande junto a pasta `uploads/`, onde estão os PDFs dos comprovantes.

## Dados de demo

A base recebida (228 currículos `.docx`, em inglês) contém **dados pessoais reais**. Ela foi transformada em dados anonimizados; os `.docx` originais **nunca** entram no repositório.

```bash
# 1. Extrai o texto e anonimiza (offline). Aborta sem gravar se sobrar e-mail, telefone, SSN, URL ou nome original
python scripts/ingest/anonymize.py "<pasta com os .docx>"     # -> data/anon/ (fora do git)

# 2. Estrutura em JSON com LLM (Groq, plano gratuito). Precisa de GROQ_API_KEY no .env
python scripts/ingest/structure.py --limit 100               # -> data/profiles/*.json (versionado)

# 3. Recria o banco com os perfis (APAGA todos os dados do banco)
python scripts/seed.py
```

Para rodar a demo, só o passo 3 é necessário: os JSONs já estão no git.

- **Anonimização:** o nome vira um nome fictício brasileiro; contato, links, SSN, data de nascimento, visto, endereço e seção de referências são removidos. A correspondência com o nome original não é salva.
- **Estruturação:** só o texto anonimizado é enviado ao Groq. Por causa da cota diária gratuita, são 100 perfis equilibrados entre BA, PM e Dev, priorizando certificação e experiência em energia. Se for interrompido, retoma de onde parou.
- **Seed:** 100 profissionais e 2 gestores. Os currículos originais param em 2016-2017, então as datas são deslocadas: cerca de 70% dos perfis parecem atualizados recentemente e 30% parecem desatualizados há 2-4 anos. Os comprovantes são PDFs fictícios, parte aprovada, parte pendente e parte faltando.

## Privacidade (LGPD)

- Uploads não são servidos publicamente; comprovantes só são baixados pelo dono ou por gestores, via endpoint autenticado.
- Os dados de demo são anonimizados de forma irreversível antes de qualquer processamento externo.
- Nunca versione `.docx`, `data/anon/`, `.env`, `null_limite.db` ou `uploads/` (já estão no `.gitignore`).

## Estrutura

```
null_limite/
├── main.py                  # App completo (models, schemas, rotas)
├── scripts/
│   ├── ingest/anonymize.py  # .docx -> texto anonimizado
│   ├── ingest/structure.py  # texto -> JSON via LLM (Groq)
│   └── seed.py              # JSON -> banco de demo
├── data/profiles/           # 100 perfis anonimizados (versionado)
├── requirements.txt
├── run.sh                   # venv + dependências + seed + servidor
├── AGENTS.md                # instruções para agentes de IA (Claude Code, Copilot, Cursor...)
└── README.md
```

## Variáveis de ambiente (opcional)

Crie um `.env` na raiz:

```env
DATABASE_URL=sqlite:///./null_limite.db
SECRET_KEY=sua-chave-secreta-forte
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=10080
UPLOAD_DIR=./uploads
MAX_FILE_SIZE=10485760
MANAGER_EMAILS=gestor@ex.com,outro@ex.com
GROQ_API_KEY=gsk_...   # só para scripts/ingest/structure.py
```

## Como contribuir

- Uma branch curta por funcionalidade (`feat/matching`, `fix/...`, `docs/...`), aberta como PR para o `main`.
- O `main` precisa sempre subir funcionando: é dele que sai a demo.
- Prefira merge commit no PR, e apague a branch depois do merge.
- Mensagens de commit no padrão Conventional Commits (`feat:`, `fix:`, `docs:`...).
