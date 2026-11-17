# FrostLine

**AI copilot for cold-chain logistics. See every shipment's temperature, risk and rule in one chat.**

FrostLine lets dispatchers and operations managers talk to their fleet data in plain English. You don't need to learn a dashboard. It connects to a legacy SQL Server telemetry database, checks live weather along the route, and looks up the company's standard operating procedures (SOPs). Every answer cites the rule it came from.

> 🚧 Work in progress. This README grows as each phase is built.

## The problem

A Southern California logistics company stores years of fleet telemetry in a legacy SQL Server database: GPS, IoT cargo temperature, risk scores and port congestion. Today the only way in is a static BI dashboard with a steep learning curve. It can't see outside conditions such as weather, and it can't tell staff what the SOP says to do about what they're looking at.

## Roadmap

- [x] **Phase 0:** Legacy system: serverless PostgreSQL (Neon) with fleet telemetry loaded
- [x] **Phase 1:** SOP ingestion into a vector database (RAG)
- [x] **Phase 2:** Data security: read-only agent role over a semantic view
- [x] **Phase 3:** Agent tools: text-to-SQL, live corridor weather, SOP search
- [ ] **Phase 4:** LangGraph ReAct orchestrator
- [ ] **Phase 5:** Streamlit dispatch console with an in-database audit log
- [ ] **Phase 6:** Free cloud deployment with CI/CD

## Getting started

**Prerequisites:** Python 3.12 and a free [Neon](https://neon.tech) PostgreSQL project.

```bash
python -m venv .venv
.venv\Scripts\activate          # Windows  (macOS/Linux: source .venv/bin/activate)
pip install -r requirements.txt
cp .env.example .env            # then paste your Neon connection string into DATABASE_URL
```

### Phase 0: load the legacy telemetry system

```bash
python scripts/ingest_legacy_data.py
```

This loads all 32,065 records into `public.tbl_sc_fleet_hist_raw`, using terse legacy column names (`iot_temp_val_c`, `risk_cls_txt`, `prt_cng_lvl`, …) that mirror what a real client's system looks like. Rows are streamed with PostgreSQL `COPY`, which takes about 10 seconds.

### Phase 1: build the SOP knowledge base

```bash
python scripts/ingest_sop_pinecone.py                    # sync data/policy into Pinecone
python scripts/search_sop.py "What is the temperature rule for fresh perishables?"
```

Policy documents in `data/policy/` are chunked according to their format:

| Format | Chunking |
|---|---|
| `.md` | Split by headings, then into ~600-character pieces. The heading path is embedded with each chunk. |
| `.txt` | ~600-character pieces with 60-character overlap |
| `.pdf` | Page by page, with the page number kept |
| `.csv` / `.xlsx` | One chunk per row |

- **Embeddings:** Pinecone embeds the text server-side (`llama-text-embed-v2`), so no embedding model runs locally or on the app server.
- **Reranking:** search fetches extra candidates, reranks them with `bge-reranker-v2-m3`, and drops anything below a relevance cutoff. An off-topic question returns nothing instead of an unrelated clause.
- **Sync:** each file is fingerprinted with SHA-256. Unchanged files are skipped, edited files have their chunks replaced, and files removed from `data/policy/` are removed from the index.

### Phase 2: lock the database down for the AI agent

```bash
python scripts/setup_security.py
```

The agent writes its own SQL, so the database itself has to enforce what the agent can and can't do. Checks in application code alone aren't enough.

| Layer | Enforcement |
|---|---|
| Semantic view | `fde_views.vw_active_fleet` exposes 25 documented, plain-English columns. The agent never sees the raw legacy table. |
| Dedicated role | The agent connects as `fde_agent_ro`, never as the database owner. The password is generated and stored only in `.env`. |
| Least privilege | `SELECT` on the view only. No access to the raw table and no `CREATE` rights. |
| Guard rails | `default_transaction_read_only`, a 15s `statement_timeout`, an idle-transaction timeout and a connection limit |

The script then connects **as the agent** and attempts reads of the raw table, deletes, updates, drops, table creation, escaping read-only mode and a long-running query. It fails loudly unless every forbidden action is blocked.

### Phase 3: agent tools

```bash
python scripts/check_agent_tools.py      # live smoke test of all three tools
pip install -r requirements-dev.txt
pytest                                   # SQL firewall unit tests (offline)
```

| Tool | What it does |
|---|---|
| `query_telemetry_db(sql_query)` | Text-to-SQL over the read-only view. The tool description lists every column, generated at runtime from the `COMMENT`s stored in the database. |
| `fetch_corridor_conditions(latitude, longitude)` | Live weather from Open-Meteo (free, no key): conditions, temperature, wind, gusts, precipitation, and a transit weather-risk estimate. |
| `search_compliance_sop(question)` | Reranked SOP search. It returns cited clauses, or an explicit "no matching clause". |

**SQL firewall** (`src/sql_guard.py`): this is the application-level layer on top of the database permissions. Every query the LLM writes is parsed with `sqlglot` and must be exactly one `SELECT`, may only read `vw_active_fleet`, may not call server functions (`pg_sleep`, `pg_read_file`, `set_config`, …), and is capped at 50 rows. Rejections and database errors go back to the agent as text, so it can correct its own query.

> **Data note:** the dataset is described as Southern California, but its GPS points span the continental US. Only about 2% fall in Southern California. The view's description in the database says this, so the agent always filters locations by coordinates.

## Data

- **Telemetry:** about 32k hourly records (Jan 2021 – Aug 2024). See [data/source/data.txt](data/source/data.txt).
- **Policy:** [data/policy/Cold_Chain_Incident_SOP_v2.md](data/policy/Cold_Chain_Incident_SOP_v2.md)

## License

[Apache 2.0](LICENSE)
