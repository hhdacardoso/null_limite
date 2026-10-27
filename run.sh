#!/bin/bash
# Null Limite - script de inicialização: cria a .venv, instala dependências e sobe o servidor

set -e
cd "$(dirname "$0")"

echo "=== Null Limite ==="
echo ""

# Verifica Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 não encontrado. Instale python3."
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "✅ Python $PYTHON_VERSION encontrado"

# Cria a venv do projeto (não instala nada no Python do sistema)
if [ ! -d .venv ]; then
    echo "📦 Criando .venv..."
    python3 -m venv .venv
fi

# Verifica dependências
echo "📦 Verificando dependências..."
.venv/bin/python -c "import fastapi, uvicorn, sqlalchemy, pydantic, jose, bcrypt, aiofiles, dotenv" 2>/dev/null || {
    echo "⚠️  Dependências faltando. Instalando..."
    .venv/bin/pip install -q -r requirements.txt
}

# Popula o banco com os dados de demo na primeira execução
if [ ! -f null_limite.db ]; then
    echo "🌱 Banco não encontrado, rodando seed de demo..."
    .venv/bin/python scripts/seed.py
fi

# Inicia servidor
echo "🚀 Iniciando servidor em http://localhost:8000"
echo "   Swagger UI: http://localhost:8000/docs"
echo "   Health:     http://localhost:8000/health"
echo ""
echo "Pressione Ctrl+C para parar"
echo ""

.venv/bin/uvicorn main:app --reload --port 8000 --host 0.0.0.0
