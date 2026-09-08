# Eaststone eQMS + Stock Control Repository

This repository now contains the existing stock-control application plus a new full-stack **Eaststone electronic Quality Management System (eQMS)** under `eqms/`.

## eQMS technology

- Frontend: Vite + React + TypeScript
- Backend: Python + FastAPI
- ORM/database: SQLAlchemy; SQLite for local development, PostgreSQL/SQL Server target for production
- Authentication: JWT development authentication; designed for Microsoft Entra ID/SSO in production
- Document source: read-only synchronisation from Eaststone controlled-document network shares

## eQMS modules

The foundation includes:

- users, roles and permissions
- audit trail
- controlled documents and revision synchronisation
- training assignments and Read & Understood acknowledgements
- complaints
- deviations
- CAPA
- OOS
- risk assessments
- dashboards and metrics
- QA/admin system controls

## Eaststone approved SOP source

Development can use a local test folder. Production should use the UNC path behind the mapped drive:

`N:\Controlled Documents\Approved Documents\Approved SOPs`

The eQMS backend is deliberately read-only against the approved source folder. It copies approved revisions into its controlled repository and records changes in the audit trail; it does not edit or delete source files.

## Run eQMS locally

Backend:

```bash
cd eqms/api
python -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
uvicorn app.main:app --reload --host 0.0.0.0 --port 8000
```

Frontend:

```bash
cd eqms/web
npm install
npm run dev -- --host 0.0.0.0
```

Open port `5173` for the React portal. The frontend proxies `/api` to the FastAPI service on port `8000`.

## Production direction

For authoritative GMP/GxP use, deploy on an Eaststone-accessible server with HTTPS, Entra ID/MFA, PostgreSQL or SQL Server, server-side access to the controlled document share, central logging, backups, disaster recovery, formal validation, change control and data-integrity controls.
