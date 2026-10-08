# CollabDocs API

Backend API for CollabDocs: workspaces, collaborators, versioned documents, threaded comments, tags,
role-based permissions and audit logging. Built with Django REST Framework and PostgreSQL. API only;
use Postman as the client.

## Setup

Requirements: Python 3.11+

By default the project uses SQLite for local development so it can run without a PostgreSQL server.
If you want to use PostgreSQL instead, set `DB_ENGINE=postgresql` and populate the `DB_*` values.

```bash
# 1. Create a virtual environment and install dependencies
python -m venv venv
source venv/bin/activate          # Windows: venv\Scripts\activate
pip install -r requirements.txt

# 2. Optional: create a PostgreSQL database for production-like local testing
psql -U postgres -c "CREATE USER collabdocs WITH PASSWORD 'your-password';"
psql -U postgres -c "CREATE DATABASE collabdocs OWNER collabdocs;"

# 3. Configure environment variables
cp .env.example .env              # then edit .env with your real values
```

`.env` holds `SECRET_KEY`, `DEBUG`, `ALLOWED_HOSTS` and the `DB_*` connection settings. It is
git-ignored; never commit it.

## Apply migrations

```bash
python manage.py migrate
```

## Run the server

```bash
python manage.py runserver
```

The API is served at `http://127.0.0.1:8000/api/`. The request-logging middleware prints
`METHOD /path status time-in-ms` to this console for every request.

## Authentication

HTTP Basic auth. `POST /api/users/` (registration) is open; every other endpoint needs a username and
password. In Postman use the Authorization tab → Basic Auth, or set the `username` / `password`
collection variables.

## Roles

| Role   | Can do                                                        |
|--------|---------------------------------------------------------------|
| admin  | everything in the workspace, including adding members         |
| editor | create/update documents, add tags, comment                    |
| viewer | read documents, comment                                       |

Users only see workspaces (and their documents, comments) they belong to.

## Endpoints (17)

| Folder     | Method | Path                                   | Notes                                              |
|------------|--------|----------------------------------------|----------------------------------------------------|
| Users      | POST   | `/api/users/`                          | Register                                           |
| Users      | GET    | `/api/users/`                          | Filters: `username`, `email`, `ids`                |
| Workspaces | POST   | `/api/workspaces/`                     | Atomic: workspace + admin member + audit log       |
| Workspaces | GET    | `/api/workspaces/`                     | Filters: `name`, `is_active`, `ids`, `created_after/before` |
| Workspaces | GET    | `/api/workspaces/{id}/`                |                                                    |
| Workspaces | POST   | `/api/workspaces/{id}/members/`        | 409 if already a member                            |
| Workspaces | GET    | `/api/workspaces/{id}/stats/`          | Aggregation (counts by status/role)                |
| Documents  | POST   | `/api/documents/`                      | Atomic: document + version + audit log             |
| Documents  | GET    | `/api/documents/`                      | `q` (title OR content), `status__in`, `tag`, `workspace`, `title`, dates |
| Documents  | GET    | `/api/documents/{id}/`                 |                                                    |
| Documents  | PUT    | `/api/documents/{id}/`                 | Atomic: save + new version; signal writes audit    |
| Documents  | GET    | `/api/documents/{id}/versions/`        |                                                    |
| Comments   | POST   | `/api/comments/`                       | Optional `parent` for threaded replies             |
| Comments   | GET    | `/api/comments/`                       | Filters: `document`, `author`, `q`, `top_level`, dates |
| Tags       | POST   | `/api/documents/{id}/tags/`            | Creates the tag if needed and attaches it          |
| Tags       | GET    | `/api/tags/`                           | Filters: `name`, `name__in`                        |
| Audit Logs | GET    | `/api/audit-logs/`                     | Filters: `model_name`, `action__in`, `object_id__in`, dates |

Standard ModelViewSet routes (retrieve, PATCH, DELETE where enabled) also exist. Error responses use
400 (validation), 401/403 (auth/role), 404 (not found) and 409 (conflict, e.g. duplicate member or tag).

Creating a document also accepts an optional write-only `tag_names` list of existing tags. If any
name does not exist the request returns 404 and the whole transaction (document, version, audit log)
is rolled back.

## Postman

Import `CollabDocs.postman_collection.json` from the repository root. Run the requests top to
bottom; test scripts store the created ids in collection variables. Register a second user (edit the
body of the Register request) before running List users so a member id is available for "Add member".

## Demo video

Link: _add your Loom / Google Drive link here_
