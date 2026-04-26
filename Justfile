set shell := ["zsh", "-cu"]

venv_python := ".venv/bin/python"

default:
  @just --list

sync-ui-env:
  #!/usr/bin/env python3
  from pathlib import Path


  def parse_env(path: Path) -> dict[str, str]:
      values: dict[str, str] = {}
      if not path.exists():
          return values
      for raw in path.read_text(encoding='utf-8').splitlines():
          line = raw.strip()
          if not line or line.startswith('#'):
              continue
          if line.startswith('export '):
              line = line[len('export '):]
          if '=' not in line:
              continue
          key, value = line.split('=', 1)
          values[key.strip()] = value.strip()
      return values


  root_env = parse_env(Path('.env'))
  ui_env_path = Path('app-ui/.env.local')
  ui_env = parse_env(ui_env_path)

  for key, value in root_env.items():
      if key.startswith('VITE_'):
          ui_env[key] = value

  if 'CLERK_PUBLISHABLE_KEY' in root_env and 'VITE_CLERK_PUBLISHABLE_KEY' not in ui_env:
      ui_env['VITE_CLERK_PUBLISHABLE_KEY'] = root_env['CLERK_PUBLISHABLE_KEY']

  lines = [
      '# Auto-synced from ../.env via `just sync-ui-env`',
      '# You can keep extra UI-only values here as well.',
  ]
  for key in sorted(ui_env.keys()):
      lines.append(f"{key}={ui_env[key]}")

  ui_env_path.write_text('\n'.join(lines) + '\n', encoding='utf-8')
  print(f'Synced {len(ui_env)} UI env keys to {ui_env_path}')

setup:
  just sync-ui-env
  test -d ".venv" || python3 -m venv .venv
  {{venv_python}} -m pip install -e .
  npm --prefix app-ui install

api:
  {{venv_python}} -m core.api

web:
  just sync-ui-env
  npm --prefix app-ui run dev

desktop:
  just sync-ui-env
  npm --prefix app-ui run tauri dev

build:
  just sync-ui-env
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
