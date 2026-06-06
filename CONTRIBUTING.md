# Contributing

**English** | [日本語](CONTRIBUTING.ja.md)

Thanks for your interest in improving SyncVey!

## Development environment

**Dev Containers (recommended).** Open the repository in VS Code and choose
**Reopen in Container** — the required extensions and the virtual environment
(`.venv`) are set up automatically.

**Or Docker Compose.** `docker compose up --build -d` (see the
[Docker Setup Guide](docker-setup.md)).

## Tech stack

You'll be working with a server-rendered Django app — there is **no SPA and no
REST framework**.

- **Backend:** Python 3.12 / Django. Views return rendered **HTML** (full pages
  or htmx partials), not JSON.
- **Frontend:** htmx + Tailwind CSS inside Django templates. **No build step, no
  Node toolchain** (Tailwind is loaded via the Play CDN).
- **Database:** PostgreSQL.

## Coding conventions

- **Python:** `snake_case` for variables and functions, `PascalCase` for classes.
  Follow standard Django conventions and keep views thin.
- **Templates:** use Tailwind utility classes and reuse the existing partials
  (`templates/_*.html`) and htmx patterns (`hx-get` / `hx-post` targeting
  `#main-content`).
- Match the style of the surrounding code — naming, comment density, and idioms.

## Tests

```bash
docker compose exec app pip install -r requirements-dev.txt
docker compose exec app pytest
```

`moto` mocks AWS; `pytest-django` and `pytest-playwright` cover unit and E2E tests.

## Git workflow

- **Branches:** use a `feature/` or `fix/` prefix.
- **Pull requests:**
  - Title example: `fix: correct asset list sorting (#123)`
  - Describe the scope of impact and how you tested it.
