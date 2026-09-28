# agent-bridge

Buzón MCP para que dos agentes Codex, cada uno en su computadora, se hagan preguntas entre sí.
Cada agente tiene su propio token: el servidor sabe quién es quién por el token, no por lo que el agente diga.

## Elige dónde vive el servidor

**Opción 1 — Tailscale (recomendada para empezar).** El servidor corre en una de las dos computadoras
(o en cualquier máquina de la red) y la otra se conecta por la red privada de Tailscale.
No hace falta abrir puertos ni tener dominio, y el tráfico va cifrado.

**Opción 2 — VPS con HTTPS.** El servidor corre en un VPS con un dominio y Caddy genera el certificado automáticamente.
Útil si alguno de los devs no puede instalar Tailscale.

---

## 1. Preparar el servidor (en la máquina que lo va a alojar)

Requisitos: Docker con Docker Compose.

```bash
cd agent-bridge
./generar-tokens.sh          # crea .env con un token para frontend y otro para backend
```
En Windows sin `sh`, copia `.env.example` a `.env` y pega dos tokens generados con
`python -c "import secrets;print(secrets.token_hex(32))"`.

### Opción 1: Tailscale
1. Instala Tailscale en las dos computadoras (y en el servidor si es otra) e inicia sesión con la misma cuenta
   (o invita al otro dev a tu tailnet).
2. Arranca: `docker compose up -d --build`
3. Averigua el nombre o IP de la máquina: `tailscale status` (IP tipo `100.x.x.x`).
4. URL del MCP: `http://NOMBRE-MAQUINA:8000/mcp`

> Si el servidor está en tu red pero no quieres que nadie de tu wifi vea el puerto,
> pon en `.env` `BIND_ADDR=` seguido de la IP de Tailscale de esa máquina.

### Opción 2: VPS con HTTPS
1. Apunta un registro DNS (A) de tu dominio a la IP del VPS.
2. En `.env`: `DOMAIN=bridge.tudominio.com` y `BIND_ADDR=127.0.0.1`.
3. Arranca: `docker compose --profile https up -d --build`
4. URL del MCP: `https://bridge.tudominio.com/mcp`

### Comprobar
```bash
curl http://NOMBRE-MAQUINA:8000/health                     # {"ok": true}
curl -i -X POST http://NOMBRE-MAQUINA:8000/mcp              # 401 sin token: bien
docker compose logs -f agent-bridge                         # verás cada pregunta/respuesta
```

## 2. Configurar cada computadora de dev

1. Guarda el token de ESE dev en la variable `AGENT_BRIDGE_TOKEN`
   - macOS/Linux: `echo 'export AGENT_BRIDGE_TOKEN=el_token' >> ~/.zshrc` (o `~/.bashrc`) y abre otra terminal.
   - Windows (PowerShell): `setx AGENT_BRIDGE_TOKEN "el_token"` y abre otra terminal.
2. Copia el bloque de `ejemplos/codex-config.toml` en `~/.codex/config.toml` cambiando la URL.
3. Copia `ejemplos/frontend/AGENTS.md` a la raíz del repo frontend y `ejemplos/backend/AGENTS.md` a la del backend
   (si ya tienen un `AGENTS.md`, añade el contenido al final).
4. Verifica: `codex mcp list` y dentro de Codex pide "llama a whoami de agent_bridge".
   Debe responder con el nombre correcto (`frontend` o `backend`).
5. Suscríbete a tu tema de ntfy (lo imprime `generar-tokens.sh`; también está en `NTFY_TOPICS` del `.env`):
   - Móvil: instala la app **ntfy** (Android/iOS), pulsa **+** y escribe el nombre del tema.
   - Escritorio: abre https://ntfy.sh/app, añade el tema y permite las notificaciones del navegador
     (o instálala como app desde el navegador para recibirlas sin tener la pestaña abierta).
   - Prueba: `curl -d "hola" https://ntfy.sh/TU_TEMA` y debe llegarte el aviso.

## 3. Uso diario
1. El agente del otro lado te hace una pregunta con `ask_peer`.
2. El servidor te manda un aviso por ntfy (móvil o escritorio), algo como
   "📬 1 pregunta pendiente — #4 de backend. Abre Codex y dile "revisa el buzón"."
   Con `NTFY_INCLUDE_TEXT=true` los avisos incluyen el texto de la pregunta o respuesta: "#4 de backend: ¿el campo es created_at…?".
3. Abres Codex y le dices **"revisa el buzón"**.
4. Codex te muestra la pregunta, propone una respuesta basada en tu código y te pregunta **"¿Envío esta respuesta?"**.
5. Tú decides: sí, la corriges, o no. Solo con tu confirmación se llama a `reply`.
6. Al que preguntó le llega otro aviso: "✅ backend respondió tu pregunta #4". Le dice a su Codex
   **"revisa las respuestas"** y este recoge la respuesta con `my_questions` y sigue con lo que quedó bloqueado.

Nada se responde solo: la confirmación la pide el `AGENTS.md`, así que ninguna respuesta sale sin que la veas.

## Mantenimiento
- Ver mensajes:
  `docker exec -it agent-bridge python -c "import sqlite3;[print(tuple(r)) for r in sqlite3.connect('/data/bridge.db').execute('select * from messages')]"`
- Cambiar un token (si se filtra): edítalo en `.env` y `docker compose up -d`.
- Añadir más agentes: añade `nombre:token` a `AGENT_TOKENS` (p. ej. `mobile:...`).
- Si un tema de ntfy se filtra, cámbialo en `NTFY_TOPICS`, `docker compose up -d` y vuelve a suscribirte.
- Al añadir un agente, añade también su tema a `NTFY_TOPICS` (sin tema, simplemente no recibe avisos).
- Nunca subas `.env` a git (ya está en `.gitignore`).
# agents_bridge
