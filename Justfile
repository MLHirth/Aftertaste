set shell := ["zsh", "-cu"]

venv_python := ".venv/bin/python"

default:
  @just --list

setup:
  test -d ".venv" || python3 -m venv .venv
  {{venv_python}} -m pip install -e .
  npm --prefix app-ui install

api:
  {{venv_python}} -m core.api

web:
  npm --prefix app-ui run dev

desktop:
  npm --prefix app-ui run tauri dev

build:
  npm --prefix app-ui run build

sync-cloud:
  curl -s -X POST "http://127.0.0.1:8765/sync/cloud-now"

docker-build:
  docker compose build

docker-up:
  docker compose up -d --build

docker-down:
  docker compose down

docker-logs:
  docker compose logs -f aftertaste
