## Agent skills

### Issue tracker

Issues and specs live as GitHub issues, managed with the `gh` CLI. See `docs/agents/issue-tracker.md`.

### Domain docs

Single-context: one `CONTEXT.md` plus ADRs in `docs/adr/` at the repo root. See `docs/agents/domain.md`.

### Project knowledge wiki

For project knowledge queries, source ingestion, or wiki maintenance, read `docs/agentwiki/AGENTS.md` and `docs/agentwiki/index.md`. Issues and domain docs remain authoritative.

## Documentation and decisions (required)

### Documentation

- For project requirements, designs, standards, business changes, and acceptance criteria or results, including new conclusions from Q&A, create or update Chinese Markdown in UTF-8 under `docs/agentwiki/wiki/` within the current task. No additional save confirmation is needed. For questions already covered without new findings, reference the existing page instead of duplicating it.
- Reuse existing topic pages. Follow the vault's `AGENTS.md` for sources, metadata, links, index updates, and append-only logging. Keep canonical requirements, domain definitions, and architectural decisions in the authoritative locations defined above; the Wiki records linked summaries and synthesis.
- Before implementation, record context, scope, constraints, proposed changes, rationale, and open questions, with detail proportional to the task. Afterward, add actual results and acceptance evidence. Distinguish proposals from approved decisions, identify approval evidence, and label failed checks and untested items. Documentation does not itself grant implementation approval.
- Documentation is complete only when files are saved, UTF-8 decoding and new or changed links are checked, and the index and log are updated as needed. Append a log entry for each documentation change; keep the index accurate without adding redundant entries. Provide changed document links in the final response and explicitly report incomplete work or checks.

### Decisions

- Before project recommendations or actions, consult relevant Wiki pages and sources. If coverage is missing, inspect the authoritative sources directly and record material gaps. Verify current code, configuration, tests, or reliable external evidence when needed; revise outdated Wiki claims based on evidence.
- Separate facts, preferences, assumptions, and unknowns. Exercise independent judgment: respectfully challenge unsupported proposals, explain tradeoffs, and recommend evidence-based alternatives rather than agree to please the user.
- Clarify gaps that materially affect scope, correctness, or irreversible outcomes before dependent actions. Continue authorized, reversible work unaffected by those gaps, stating any assumptions. Reuse prior authorization; ask only for missing information or necessary approval.
- Record decisions, evidence, rationale, and superseded conclusions in the Wiki, linking to canonical sources instead of maintaining conflicting copies. Preserve the history and reasons for supersession; an Agent recommendation is a proposal until supported by the applicable approval or decision record.
