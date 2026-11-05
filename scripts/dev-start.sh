#!/usr/bin/env bash
# Arranca/para el entorno de desarrollo de captura_productos (stack Docker "erpnext-dev").
# Se ejecuta desde el HOST (Git Bash en Windows), no dentro del contenedor.
#
#   scripts/dev-start.sh            # = start: levanta el stack y "bench start" en segundo plano
#   scripts/dev-start.sh start
#   scripts/dev-start.sh stop       # detiene "bench start" (los contenedores siguen arriba)
#   scripts/dev-start.sh down       # detiene "bench start" y los contenedores (sin borrar volumenes)
#   scripts/dev-start.sh status
#   scripts/dev-start.sh logs       # sigue logs/bench-start.log
#   scripts/dev-start.sh shell      # bash dentro del contenedor, en el bench
#
# Variables opcionales: COMPOSE_FILE_PATH, CONTAINER, SITE, PORT.
set -euo pipefail

# Git Bash convierte rutas tipo /workspace a C:/Program Files/Git/...; lo desactivamos.
export MSYS_NO_PATHCONV=1
export MSYS2_ARG_CONV_EXCL="*"

COMPOSE_FILE_PATH="${COMPOSE_FILE_PATH:-$HOME/OneDrive/Documentos/ERPCOCINAESTACIONTRADICION/local-dev/docker-compose.yml}"
# Con la conversion desactivada, docker.exe necesita la ruta en formato Windows (C:/...).
if command -v cygpath >/dev/null 2>&1; then
  COMPOSE_FILE_PATH="$(cygpath -m "$COMPOSE_FILE_PATH")"
fi
CONTAINER="${CONTAINER:-erpnext-dev-frappe-1}"
SITE="${SITE:-dev.localhost}"
PORT="${PORT:-8000}"
BENCH_DIR=/workspace/frappe-bench
LOG="logs/bench-start.log"

in_bench() { docker exec -w "$BENCH_DIR" "$CONTAINER" bash -lc "$1"; }

is_running() { in_bench "pgrep -f '[h]oncho start' >/dev/null"; }

wait_http() {
  echo -n "Esperando http://localhost:$PORT "
  for _ in $(seq 1 90); do
    if curl -fsS "http://localhost:$PORT/api/method/ping" 2>/dev/null | grep -q pong; then
      echo " OK"
      return 0
    fi
    echo -n "."
    sleep 2
  done
  echo " sin respuesta (revisa: $0 logs)"
  return 1
}

cmd_start() {
  docker compose -f "$COMPOSE_FILE_PATH" up -d
  if is_running; then
    echo "bench start ya estaba corriendo en $CONTAINER"
  else
    echo "Iniciando bench start en segundo plano (log: $BENCH_DIR/$LOG)"
    docker exec -d -w "$BENCH_DIR" "$CONTAINER" bash -lc "exec bench start >> $LOG 2>&1"
  fi
  wait_http
  echo "Desk: http://localhost:$PORT  (Administrator / admin, sitio $SITE)"
}

cmd_stop() {
  if is_running; then
    # honcho reenvia SIGTERM a web, socketio, watch, schedule y worker
    in_bench "pkill -TERM -f '[h]oncho start'" || true
    sleep 3
    in_bench "pkill -TERM -f '[f]rappe.utils.bench_helper|[s]ocketio.js|[e]sbuild' || true"
    echo "bench start detenido"
  else
    echo "bench start no estaba corriendo"
  fi
}

cmd_status() {
  docker compose -f "$COMPOSE_FILE_PATH" ps
  if is_running; then echo "bench start: corriendo"; else echo "bench start: detenido"; fi
  if curl -fsS "http://localhost:$PORT/api/method/ping" 2>/dev/null | grep -q pong; then
    echo "HTTP $PORT: responde (pong)"
  else
    echo "HTTP $PORT: sin respuesta"
  fi
}

case "${1:-start}" in
  start)  cmd_start ;;
  stop)   cmd_stop ;;
  down)   cmd_stop || true; docker compose -f "$COMPOSE_FILE_PATH" stop ;;
  status) cmd_status ;;
  logs)   in_bench "tail -n 100 -f $LOG" ;;
  shell)  docker exec -it -w "$BENCH_DIR" "$CONTAINER" bash ;;
  *) echo "Uso: $0 [start|stop|down|status|logs|shell]"; exit 2 ;;
esac
