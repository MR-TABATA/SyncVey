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

## Translations

The UI ships in English and Japanese. If you add or change a translatable
string, run the extraction and translate it — CI fails otherwise:

```bash
docker compose exec app python manage.py makemessages -l ja --no-obsolete
# Review every "#, fuzzy" entry. Those are machine guesses copied from the
# nearest existing string and they are frequently wrong ("Auto Scaling" once
# came out as "自動スキャン"). Fix the msgstr, then delete the fuzzy line.
docker compose exec app python manage.py compilemessages -l ja
```

Two gates run in CI, because gettext fails silently in two different ways:

| Gate | Catches |
| --- | --- |
| `python -m asset_manager.i18n_check` | Entries in the catalogue that are `#, fuzzy` or have an empty `msgstr` |
| `python3 scripts/check_i18n_extraction.py` | Strings in the code that were never extracted into the catalogue at all |

The second one runs `makemessages` against a throwaway copy and compares msgid
sets, so it never leaves your working tree modified.

## Development history page

`docs/history.{ja,en}.html` is generated, not written. A workflow rebuilds it on
every push to `main` and commits the result, so merged work appears there on its
own — a pull request with no entry in `PHASES` still shows up under "recent
changes" with its title as the summary.

To give it a proper one-line summary, edit `PHASES` / `SUMMARIES` in
`scripts/build_history.py` (both languages live there, side by side) and run:

```bash
python3 scripts/build_history.py
```

## Git workflow

- **Branches:** use a `feature/` or `fix/` prefix.
- **Pull requests:**
  - Title example: `fix: correct asset list sorting (#123)`
  - Describe the scope of impact and how you tested it.
