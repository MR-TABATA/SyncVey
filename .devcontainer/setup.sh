#!/bin/bash
# Backend setup (DRF)
cd /app
python -m venv .venv
source .venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt

# Frontend setup (React)
cd ../frontend # frontendコンテナ側のパスに合わせて調整
npm install