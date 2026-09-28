#!/usr/bin/env sh
# Crea .env con dos tokens aleatorios (frontend y backend) si no existe.
set -e
cd "$(dirname "$0")"
if [ -f .env ]; then echo ".env ya existe, no lo toco."; exit 0; fi
FT=$(openssl rand -hex 32)
BT=$(openssl rand -hex 32)
FN="agent-bridge-frontend-$(openssl rand -hex 12)"
BN="agent-bridge-backend-$(openssl rand -hex 12)"
sed -e "s/PEGA_AQUI_TOKEN_FRONTEND/$FT/" -e "s/PEGA_AQUI_TOKEN_BACKEND/$BT/" \
    -e "s/PEGA_AQUI_TEMA_FRONTEND/$FN/" -e "s/PEGA_AQUI_TEMA_BACKEND/$BN/" .env.example > .env
echo "Tokens y temas de ntfy creados en .env"
echo "  frontend: token $FT"
echo "            tema ntfy $FN"
echo "  backend:  token $BT"
echo "            tema ntfy $BN"
echo "Pasa a cada dev SOLO su token y su tema, por un canal privado."
