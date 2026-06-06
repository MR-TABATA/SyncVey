# Docker Setup Guide

**English** | [日本語](docker-setup.ja.md)

## Layout

```
syncvey/
├── docker-compose.yml
├── Dockerfile
└── manage.py
```

### Containers

| Container       | Image                | Ports       | Role          |
|-----------------|----------------------|-------------|---------------|
| `syncvey-app`   | local build          | 8000 → 8000 | Django app    |
| `syncvey-db`    | postgres:18.3-alpine | 5432 → 5432 | PostgreSQL    |

---

## Running

### First run / clean start

```bash
# Build the image and start the containers
docker compose up --build -d
```

On startup the following run automatically:

1. `migrate` — create tables
2. `createsuperuser` — create the admin account (using values from `.env`)
3. `seed` — load sample data

### First login

The app (asset ledger) and the Django admin site use separate accounts.
**Superusers (e.g. `admin`) are management-only** — visiting the app redirects them to `/admin/`. Log in to the app with an organization member.

| Purpose | URL | Username | Password |
|---------|-----|----------|----------|
| App (asset ledger) | http://localhost:8000 | `tabata_hiroshi` | `Passw0rd!` |
| Django admin | http://localhost:8000/admin/ | `admin` (configurable in `.env`) | `admin` |

> The seed also creates organization members with different roles (e.g. `tanaka_kenji` = infra admin, `yamada_yuki` = app admin, `sato_mai` = viewer — all with `Passw0rd!`), so you can try how permissions change what's visible.

### Subsequent runs

```bash
docker compose up -d
```

`migrate`, `createsuperuser`, and `seed` are idempotent, so they run every time but never overwrite existing data.

---

## Stopping / removing

```bash
# Stop (data is kept)
docker compose down

# Stop and also delete the DB data
docker compose down -v
```

---

## Common commands

### Logs

```bash
# All containers
docker compose logs -f

# Individual
docker compose logs -f app
docker compose logs -f db
```

### Shell into a container

```bash
# App
docker exec -it syncvey-app bash

# DB (PostgreSQL)
docker exec -it syncvey-db psql -U user -d asset_manager
```

### Migrations (manual)

```bash
docker exec syncvey-app python manage.py makemigrations
docker exec syncvey-app python manage.py migrate
```

### Adding a package

```bash
# After adding it to requirements.txt (production), rebuild the image
docker compose build app
docker compose up -d app
```

### Installing test dependencies

```bash
# Install requirements-dev.txt (moto / pytest, etc.) into the container
docker compose exec app pip install -r requirements-dev.txt
```

---

## Environment variables (`.env`)

| Variable                     | Description                       | Default             |
|------------------------------|-----------------------------------|---------------------|
| `DB_NAME`                    | Database name                     | `asset_manager`     |
| `DB_USER`                    | Database user                     | `user`              |
| `DB_PASSWORD`                | Database password                 | `password`          |
| `DB_PORT_EXTERNAL`           | Host-side DB port                 | `5432`              |
| `DATABASE_URL`               | Connection URL                    | —                   |
| `DJANGO_SUPERUSER_USERNAME`  | Initial admin username            | `admin`             |
| `DJANGO_SUPERUSER_EMAIL`     | Initial admin email               | `admin@example.com` |
| `DJANGO_SUPERUSER_PASSWORD`  | Initial admin password            | `admin`             |
| `SECRET_KEY`                 | Django secret key                 | — (required in prod) |
| `AWS_ACCESS_KEY_ID`          | AWS access key                    | —                   |
| `AWS_SECRET_ACCESS_KEY`      | AWS secret key                    | —                   |
| `AWS_SCAN_REGIONS`           | Regions to scan                   | `ap-northeast-1,...`|
| `SLACK_WEBHOOK_URL`          | Slack notifications (shared channel) | — (optional)     |

---

## Troubleshooting

### The app crashes before the DB is ready

The app waits until the `healthcheck` in `docker-compose.yml` passes.
If it still fails, restart it manually:

```bash
docker compose restart app
```

### Port conflicts

Change `APP_PORT_EXTERNAL` or `DB_PORT_EXTERNAL` in `.env`:

```
APP_PORT_EXTERNAL=8001
DB_PORT_EXTERNAL=5433
```
