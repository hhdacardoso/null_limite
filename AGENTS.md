# AGENTS.md

Instruções para agentes de IA (Claude Code, Copilot, Cursor, Codex...) neste repositório. Contexto do produto, endpoints e como rodar estão no `README.md`; leia-o primeiro.

## Projeto em uma frase

Backend FastAPI para a Nexora (case de hackathon) montar a equipe que mais pontua em cada edital de licitação, a partir de perfis com itens comprovados, e manter a base atualizada com gamificação.

## Comandos

```bash
source .venv/bin/activate              # sempre; o Python do sistema não tem as dependências
pip install -r requirements.txt
python scripts/seed.py                 # recria o banco com dados de demo (apaga tudo)
uvicorn main:app --reload --port 8000  # Swagger em /docs
```

Testes com pytest (usam banco e uploads temporários, nunca o `null_limite.db`):

```bash
pip install -r requirements-dev.txt
python -m pytest -q tests
```

Para validar algo fora dos testes, use `fastapi.testclient.TestClient` com `DATABASE_URL` e `UPLOAD_DIR` apontando para um diretório temporário.

## Arquitetura

- `main.py` concentra tudo (config, models SQLAlchemy, schemas Pydantic, dependências, rotas), nessa ordem. Siga o mesmo padrão até o arquivo ser dividido em módulos.
- SQLite com `Base.metadata.create_all`; **não há migrations**. Mudou um model: rode o seed.
- Auth: JWT com `sub` = e-mail. `get_current_user` para qualquer usuário logado, `require_manager` para rotas de gestor.
- Itens do perfil (`Certificate`, `Project`, `Education`) pertencem a um `Professional`. `Evidence` aponta para exatamente um deles (FK `certificate_id`, `project_id` ou `education_id`) e tem status `pending`/`approved`/`rejected`.
- No matching, **só item com comprovante aprovado pontua**; item pendente pode aparecer como pontuação potencial.
- Matching (`/bids`): edital tem vagas (`BidRole`) e cada vaga tem critérios (`BidCriterion`, tipos `certification`, `sector_projects`, `education`, `years_experience`). Anos de experiência comprovados vêm dos projetos aprovados; o `years_experience` declarado só conta como potencial. A equipe é escolhida por busca exata sem repetir pessoa (`best_team`), limitada a `MAX_TEAM_SLOTS` vagas.
- Comprovante sem arquivo (`file_path` nulo) só existe para formação: é o pedido de validação pelo gestor.
- Gamificação: pontos são derivados do estado atual (`profile_progress` em `main.py`), sem tabela de eventos. Ranking individual só para gestor; profissional vê os próprios pontos e o agregado por equipe.
- Setores de projeto são normalizados: `energia`, `financeiro`, `seguros`, `saude`, `governo`, `varejo`, `telecom`, `tecnologia`, `industria`, `logistica`, `educacao`, `outros`.
- Datas são UTC naive (`utcnow()` em `main.py`); não use `datetime.utcnow()`.

## Convenções

- Código, identificadores e mensagens de commit em inglês; README, docs e textos para o usuário final em português.
- Pydantic v2 (`model_config = ConfigDict(...)`, `model_dump()`), SQLAlchemy 2.x.
- Commits no padrão Conventional Commits (`feat:`, `fix:`, `docs:`, `refactor:`).
- **Não adicione atribuição de IA** em lugar nenhum: nada de `Co-Authored-By`, "Generated with ..." ou similares em commits, PRs, comentários ou docs.
- Uma branch por funcionalidade, PR para o `main`; não faça commit direto no `main`.

## Dados e LGPD (regras rígidas)

- Os currículos originais (`.docx`) contêm dados pessoais reais. **Nunca** os versione, copie para o repositório ou envie a serviços externos.
- Só o texto gerado por `scripts/ingest/anonymize.py` pode ir para uma LLM externa.
- Nunca imprima em log, terminal ou PR conteúdo que possa ser dado pessoal (e-mail, telefone, nome original). Ao depurar a anonimização, mascare os valores.
- Nunca versione `.env`, `data/anon/`, `null_limite.db` ou `uploads/`.
- Arquivos enviados (currículos, comprovantes) não podem ser servidos publicamente; acesso só via endpoint autenticado, pelo dono ou por gestores.
