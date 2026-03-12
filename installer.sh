#!/bin/bash
echo "==============================="
echo "  PROJECT INSTALLER (Linux/Mac)"
echo "==============================="

# ---- NPM INSTALL ----
if [ -f "package.json" ]; then
    echo "🔧 Menjalankan npm install ..."
    npm install
else
    echo "⚠️ package.json tidak ditemukan, skip npm install"
fi

# ---- PYTHON VENV ----
if [ ! -d "venv" ]; then
    echo "🐍 Membuat virtual environment ..."
    python3 -m venv venv
else
    echo "✔ venv sudah ada, skip pembuatan"
fi

echo "🔌 Mengaktifkan virtual environment ..."
source venv/bin/activate

# ---- PIP INSTALL ----
if [ -f "requirements.txt" ]; then
    echo "📦 Menjalankan pip install -r requirements.txt ..."
    pip install -r requirements.txt
else
    echo "⚠️ requirements.txt tidak ditemukan, skip pip install"
fi

echo "==============================="
echo "Instalasi selesai!"
echo "==============================="
