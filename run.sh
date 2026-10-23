#!/bin/bash
# Null Limite - S1 Fundação - Script de inicialização

set -e

echo "=== Null Limite S1 - Fundação ==="
echo ""

# Verifica Python
if ! command -v python3 &> /dev/null; then
    echo "❌ Python 3 não encontrado. Instale python3."
    exit 1
fi

PYTHON_VERSION=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "✅ Python $PYTHON_VERSION encontrado"

# Verifica dependências
echo "📦 Verificando dependências..."
python3 -c "import fastapi, uvicorn, sqlalchemy, pydantic, jose, passlib, aiofiles" 2>/dev/null || {
    echo "⚠️  Dependências faltando. Instalando..."
    pip3 install --break-system-packages -r requirements.txt -q
}

# Cria diretório de uploads
mkdir -p uploads

# Inicia servidor
echo "🚀 Iniciando servidor em http://localhost:8000"
echo "   Swagger UI: http://localhost:8000/docs"
echo "   Health:     http://localhost:8000/health"
echo ""
echo "Pressione Ctrl+C para parar"
echo ""

uvicorn main:app --reload --port 8000 --host 0.0.0.0