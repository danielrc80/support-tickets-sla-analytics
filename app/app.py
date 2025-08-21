from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.responses import JSONResponse
from contextlib import asynccontextmanager
import pandas as pd
import numpy as np
import io, os, re
from pathlib import Path

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup logic
    print("Application startup: Initializing resources...")
    # e.g., establish database connections, load configurations
    yield
    # Shutdown logic
    print("Application shutdown: Cleaning up resources...")
    # e.g., close database connections, release resources

app = FastAPI(lifespan=lifespan, title="Support Tickets SLA Analytics", version="2.1.0")

DATA_DIR = Path(os.getenv("DATA_DIR", "/data"))
TICKETS_PATH = DATA_DIR / "tickets.parquet"
SLA_PATH = DATA_DIR / "sla.parquet"

# CSV columns (verbatim from your export)
EXPECTED_TICKET_COLS = [
    "Issue key","Issue id","Custom field (Severity)","Status",
    "Custom field (First Response SLA Target Date)",
    "Custom field (First Response SLA Actual Date)",
    "Created","Updated","Resolved","Assignee",
    "Custom field (Environment)","Custom field (Product)",
    "Summary","Custom field (CRM Company)","Custom field (Reopen Count)"
]
REQUIRED_TICKET_COLS = [
    "Issue key","Custom field (Severity)","Status",
    "Custom field (First Response SLA Target Date)",
    "Custom field (First Response SLA Actual Date)",
    "Created","Resolved","Assignee",
    "Custom field (Product)","Custom field (CRM Company)",
    "Custom field (Reopen Count)"
]
NORMALIZE_MAP = {
    "Issue key": "issue_key",
    "Custom field (Severity)": "severity",
    "Status": "status",
    "Custom field (First Response SLA Target Date)": "fr_target",
    "Custom field (First Response SLA Actual Date)": "fr_actual",
    "Created": "created",
    "Updated": "updated",
    "Resolved": "resolved",
    "Assignee": "assignee",
    "Custom field (Environment)": "environment",
    "Custom field (Product)": "product",
    "Summary": "summary",
    "Custom field (CRM Company)": "crm_company",
    "Custom field (Reopen Count)": "reopen_count",
}

@app.get("/")
def init_storage():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    return {"message": "dir structure is correct"}

@app.get("/health")
def health():
    return {"status": "ok"}

@app.post("/upload/tickets")
async def upload_tickets(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Only CSV supported")

    raw = await file.read()
    df = pd.read_csv(io.BytesIO(raw))

    # --- Normalize CRM company BEFORE any merge/rename ---
    if "Custom field (CRM Company)" in df.columns:
        df["Custom field (CRM Company)"] = (
            df["Custom field (CRM Company)"]
            .astype(str)
            .str.strip()                      # trim leading/trailing spaces
            .str.replace(r"\s+", " ", regex=True)  # collapse double spaces
        )

    # --- Parse JIRA-like datetime format: "18/Aug/25 6:00 PM" ---
    date_cols = [
        "Custom field (First Response SLA Target Date)",
        "Custom field (First Response SLA Actual Date)",
        "Created", "Updated", "Resolved",
    ]
    for col in date_cols:
        if col in df.columns:
            df[col] = pd.to_datetime(
                df[col], format="%d/%b/%y %I:%M %p", errors="coerce"
            )

    # Validate required headers exist
    missing = set(REQUIRED_TICKET_COLS) - set(df.columns)
    if missing:
        raise HTTPException(400, f"Missing columns: {sorted(missing)}")

    # Keep known cols & normalize names used by the app
    df = df[[c for c in EXPECTED_TICKET_COLS if c in df.columns]].rename(columns=NORMALIZE_MAP)

    # Only Permanently Closed tickets are considered
    df = df[df["status"].eq("Permanently Closed")].copy()

    # Types & derived fields
    df["reopen_count"] = pd.to_numeric(df["reopen_count"], errors="coerce").fillna(0).astype(int)
    df["resolution_minutes"] = (df["resolved"] - df["created"]).dt.total_seconds() / 60.0

    # Upsert by issue_key (keep latest by resolved)
    cur = pd.read_parquet(TICKETS_PATH) if TICKETS_PATH.exists() else pd.DataFrame()
    all_ = pd.concat([cur, df], ignore_index=True)
    all_ = all_.sort_values(["issue_key","resolved"]).drop_duplicates(["issue_key"], keep="last")
    all_.to_parquet(TICKETS_PATH)
    return {"tickets_stored": int(len(all_)), "new_rows": int(len(df))}

# ---- Upload SLA matrix (First Response + Resolution per severity) ----
@app.post("/upload/sla")
async def upload_sla(file: UploadFile = File(...)):
    if not file.filename.lower().endswith(".csv"):
        raise HTTPException(400, "Only CSV supported")

    raw = await file.read()
    s = pd.read_csv(io.BytesIO(raw))

    # --- Normalize CRM company BEFORE melting/pivoting ---
    if "CRM Company" in s.columns:
        s["CRM Company"] = (
            s["CRM Company"]
            .astype(str)
            .str.strip()
            .str.replace(r"\s+", " ", regex=True)
        )
    else:
        raise HTTPException(400, "Missing required column: 'CRM Company'")

    # Expect headers like: Severity N First Response / Severity N Resolution
    cols = []
    for c in s.columns:
        if c == "CRM Company":
            continue
        m = re.match(r"Severity\s*(\d)\s*(First Response|Resolution)", str(c), flags=re.I)
        if m:
            sev = f"Severity {m.group(1)}"
            kind = "first_response" if m.group(2).lower().startswith("first") else "resolution"
            cols.append((c, sev, kind))

    if not cols:
        raise HTTPException(
            400,
            "Expected columns like 'Severity 1 First Response' and 'Severity 1 Resolution' (minutes)."
        )

    # Melt to tidy; then pivot to columns first_response / resolution
    tidy = []
    for _, row in s.iterrows():
        company = row.get("CRM Company")
        for c, sev, kind in cols:
            tidy.append({
                "crm_company": company,
                "severity": sev,
                "kind": kind,
                "minutes": pd.to_numeric(row.get(c), errors="coerce")
            })
    sla = pd.DataFrame(tidy).dropna(subset=["minutes"]).astype({"minutes": float})
    sla = sla.pivot_table(index=["crm_company","severity"], columns="kind", values="minutes").reset_index()
    sla.columns.name = None

    sla.to_parquet(SLA_PATH)
    return {"companies": int(sla['crm_company'].nunique()), "rows": int(len(sla))}

# ---- Helpers ----
def _load_joined() -> pd.DataFrame:
    if not (TICKETS_PATH.exists() and SLA_PATH.exists()):
        raise HTTPException(400, "Upload both tickets CSV and SLA CSV first.")
    t = pd.read_parquet(TICKETS_PATH)
    s = pd.read_parquet(SLA_PATH)
    d = t.merge(s, how="left", on=["crm_company","severity"])

    # First Response compliance & exceed %
    d["first_response_within_sla"] = (d["fr_actual"] <= d["fr_target"]) & d["fr_actual"].notna() & d["fr_target"].notna()
    delta_fr = (d["fr_actual"] - d["fr_target"]).dt.total_seconds() / 60.0
    # Use window length from Created->Target as the reference; avoid divide-by-0
    target_fr = (d["fr_target"] - d["created"]).dt.total_seconds() / 60.0
    d["first_response_exceed_pct"] = np.where(
        delta_fr > 0,
        (delta_fr / np.clip(target_fr, 1.0, None)) * 100.0,
        0.0
    )

    # Resolution compliance & exceed %
    # 'resolution' is SLA minutes from the SLA CSV for that company+severity
    d["resolution_within_sla"] = d["resolution_minutes"] <= d["resolution"].astype(float)
    delta_res = d["resolution_minutes"] - d["resolution"].astype(float)
    d["resolution_exceed_pct"] = np.where(
        delta_res > 0,
        (delta_res / np.clip(d["resolution"].astype(float), 1.0, None)) * 100.0,
        0.0
    )

    return d

def _json_records(df: pd.DataFrame):
    """Make a DataFrame JSON-safe:
       - datetimes → ISO-8601 strings
       - ±inf → NaN → None
       - pandas NA/NaN → None
    """
    out = df.copy()

    # Datetimes → ISO strings
    dt_cols = out.select_dtypes(include=["datetime64[ns]", "datetime64[ns, UTC]"]).columns
    for c in dt_cols:
        out[c] = out[c].apply(lambda x: x.isoformat() if pd.notna(x) else None)

    # ±inf → NaN (use assign back; no inplace on slice)
    out = out.replace([np.inf, -np.inf], np.nan)

    # Nullable booleans → object with None
    for c in out.select_dtypes(include=["boolean"]).columns:
        out[c] = out[c].astype(object).where(out[c].notna(), None)

    # NaN → None everywhere
    out = out.where(pd.notna(out), None)
    return out.to_dict(orient="records")

# ---- Reports ----
@app.get("/reports/assignee_avg")
def assignee_avg():
    d = _load_joined()
    g = d.groupby("assignee", as_index=False)["resolution_minutes"].mean()
    g["resolution_minutes"] = g["resolution_minutes"].round(2)
    return JSONResponse(g.sort_values(["resolution_minutes","assignee"]).to_dict(orient="records"))

@app.get("/reports/product_avg")
def product_avg():
    d = _load_joined()
    g = d.groupby("product", as_index=False)["resolution_minutes"].mean()
    g["resolution_minutes"] = g["resolution_minutes"].round(2)
    return JSONResponse(g.sort_values(["resolution_minutes","product"]).to_dict(orient="records"))

@app.get("/reports/violations")
def violations():
    d = _load_joined()
    v = d[(~d["first_response_within_sla"]) | (~d["resolution_within_sla"])].copy()

    cols = [
        "issue_key","created","assignee","product","reopen_count",
        "first_response_within_sla","first_response_exceed_pct",
        "resolution_within_sla","resolution_exceed_pct"
    ]
    out = v[cols].copy()

    # Make percent columns numeric, clean infinities, round — always assign back
    for c in ["first_response_exceed_pct", "resolution_exceed_pct"]:
        out[c] = pd.to_numeric(out[c], errors="coerce")
        out[c] = out[c].replace([np.inf, -np.inf], np.nan)
        out[c] = out[c].round(2)

    # Sort with NaNs last
    out = out.sort_values(
        by=["resolution_exceed_pct","first_response_exceed_pct","created"],
        ascending=[False, False, True],
        na_position="last"
    )

    return JSONResponse(_json_records(out))

@app.get("/reports/reopens")
def reopens():
    d = _load_joined()
    r = d[d["reopen_count"] > 1].copy()
    cols = [
        "reopen_count","summary","issue_key","created","assignee","product",
        "first_response_exceed_pct","resolution_exceed_pct"
    ]
    r = r[cols].sort_values(
        ["reopen_count","resolution_exceed_pct","first_response_exceed_pct"],
        ascending=[False, False, False]
    )
    for c in ["first_response_exceed_pct","resolution_exceed_pct"]:
        r[c] = r[c].round(2)
        r[c] = r[c].where(r[c].notna(), None)

    return JSONResponse(_json_records(r))