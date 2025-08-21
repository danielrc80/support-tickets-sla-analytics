# Support Tickets SLA Analytics (FastAPI + Pandas)

A small but advanced analytics service that ingests **support tickets** and **client SLA matrices** and produces:

- SLA compliance (First Response + Resolution)
- Average resolution time by **assignee** and by **product**
- SLA violations (with % overrun)
- “Reopen-heavy” tickets
- Summary metrics (compliance %, median/P90, breakdown by severity)

**Tech:** Python 3.11, FastAPI, Pandas, Parquet, Docker, Kubernetes-ready.

---

## Features

- Upload **Tickets CSV** and **SLA CSV** (per company × severity).
- Handles Jira-style datetimes like `18/Aug/25 6:00 PM`.
- Robust trimming/normalization of `CRM Company` for clean joins.
- Outputs JSON (ISO datetimes, JSON-safe `null`s).
- Local dev, Docker, and K8s manifests included.

---

## Endpoints

- `POST /upload/tickets` — upload tickets CSV
- `POST /upload/sla` — upload SLA CSV
- `GET /reports/assignee_avg` — avg resolution minutes by assignee
- `GET /reports/product_avg` — avg resolution minutes by product
- `GET /reports/violations` — tickets missing FR and/or resolution SLA (with % exceeded)
- `GET /reports/reopens` — tickets with `reopen_count > 1`
- `GET /reports/summary` — dashboard-style stats
- `GET /health` — health check  
Interactive docs: **`/docs`**

---

## CSV Schemas

### Tickets CSV (headers required)
Required columns (verbatim):

```
Issue key
Custom field (Severity)        # "Severity 1" .. "Severity 5"
Status                         # only "Permanently Closed" are considered
Custom field (First Response SLA Target Date)
Custom field (First Response SLA Actual Date)
Created
Resolved
Assignee
Custom field (Product)
Custom field (CRM Company)
Custom field (Reopen Count)
```

**Datetime format:** `18/Aug/25 6:00 PM` (parsed with `%d/%b/%y %I:%M %p`).

### SLA CSV (minutes)
```
CRM Company,
Severity 1 First Response,Severity 1 Resolution,
Severity 2 First Response,Severity 2 Resolution,
Severity 3 First Response,Severity 3 Resolution,
Severity 4 First Response,Severity 4 Resolution,
Severity 5 First Response,Severity 5 Resolution
```

See `samples/` for working examples.

---

## Quickstart (Local)

```bash
python -m venv .venv && . .venv/Scripts/activate  # Windows
# or: source .venv/bin/activate                    # macOS/Linux

pip install -r requirements.txt
uvicorn app.app:app --reload --port 8080
```

Upload sample files and explore:

```bash
# in another terminal
curl -F "file=@samples/tickets_sample.csv" http://127.0.0.1:8080/upload/tickets
curl -F "file=@samples/slas_sample.csv"    http://127.0.0.1:8080/upload/sla
```

Open docs: http://127.0.0.1:8080/docs

---

## Docker

```bash
docker build -t sla-analytics:local .
docker run --rm -p 8080:8080 -v "$(pwd)/data:/data" sla-analytics:local
# Windows PowerShell: -v "${PWD}\data:/data"
```

---

## Kubernetes (example)

```bash
kubectl apply -f k8s/pvc.yaml
kubectl apply -f k8s/deployment.yaml
kubectl apply -f k8s/service.yaml
kubectl get svc sla-analytics-svc -w
```

When the external IP is ready -> `http://<EXTERNAL-IP>/docs`.

> Note: `pvc.yaml` uses EBS-style `ReadWriteOnce`. For multiple replicas, use EFS (RWX) or write Parquet to S3.

---

## Sample Requests

**Average by assignee**
```bash
curl http://127.0.0.1:8080/reports/assignee_avg | jq
```

**Violations**
```bash
curl http://127.0.0.1:8080/reports/violations | jq
```

**Summary**
```bash
curl http://127.0.0.1:8080/reports/summary | jq
```

---

## Project Structure

```
support-tickets-sla-analytics/
├─ app/
│  ├─ __init__.py
│  └─ app.py                  # FastAPI service (upload + reports)
├─ samples/
│  ├─ tickets_sample.csv
│  └─ slas_sample.csv
├─ k8s/
│  ├─ pvc.yaml
│  ├─ deployment.yaml
│  └─ service.yaml
├─ .github/workflows/ci.yml   # (optional) GH Actions → build & push image to GHCR
├─ .gitignore
├─ .dockerignore
├─ Dockerfile
├─ requirements.txt
└─ README.md
```

---

## Tech Notes

- **First Response SLA**: compliant if `fr_actual <= fr_target`. `% exceeded` computed vs window `(fr_target - created)`.
- **Resolution SLA**: compares `(resolved - created)` minutes vs company×severity SLA minutes.
- **Normalization**: `CRM Company` values are trimmed/collapsed to avoid join mismatches.
- **Persistence**: CSVs are converted to Parquet and stored under `/data` (mount a volume in Docker/K8s).

---

## Troubleshooting

- **CSV upload 422 / “Field required”**: ensure Body → *form-data*, key name **`file`**, type **File**. Install `python-multipart`.
- **Multipart boundary error**: don’t set `Content-Type` manually; let the client set it.
- **Datetime parsing errors**: confirm format matches `18/Aug/25 6:00 PM`.
- **Join mismatch on company names**: leading/trailing spaces → trim in both files (handled by app on upload).
- **JSON serialization errors**: all datetimes are output as ISO; NaN/±inf are converted to `null`.
