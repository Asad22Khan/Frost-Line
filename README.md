# FrostLine

**AI copilot for cold-chain logistics. See every shipment's temperature, risk and rule in one chat.**

FrostLine lets dispatchers and operations managers talk to their fleet data in plain English. You don't need to learn a dashboard. It connects to a legacy SQL Server telemetry database, checks live weather along the route, and looks up the company's standard operating procedures (SOPs). Every answer cites the rule it came from.

> 🚧 Work in progress. This README grows as each phase is built.

## The problem

A Southern California logistics company stores years of fleet telemetry in a legacy SQL Server database: GPS, IoT cargo temperature, risk scores and port congestion. Today the only way in is a static BI dashboard with a steep learning curve. It can't see outside conditions such as weather, and it can't tell staff what the SOP says to do about what they're looking at.

## Roadmap

- [ ] **Phase 0:** Legacy system: Azure SQL Database with fleet telemetry loaded
- [ ] **Phase 1:** SOP ingestion into a vector database (RAG)
- [ ] **Phase 2:** Data security: read-only agent role over a semantic view
- [ ] **Phase 3:** Agent tools: text-to-SQL, live corridor weather, SOP search
- [ ] **Phase 4:** LangGraph ReAct orchestrator
- [ ] **Phase 5:** Streamlit dispatch console with an in-database audit log
- [ ] **Phase 6:** Free cloud deployment with CI/CD

## Data

- **Telemetry:** about 32k hourly records (Jan 2021 – Aug 2024). See [data/source/data.txt](data/source/data.txt).
- **Policy:** [data/policy/Cold_Chain_Incident_SOP_v2.md](data/policy/Cold_Chain_Incident_SOP_v2.md)

## License

[Apache 2.0](LICENSE)
