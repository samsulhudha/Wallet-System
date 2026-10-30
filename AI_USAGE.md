# AI Usage & Collaborative Engineering Disclosure

**Assignment:** Enterprise Wallet & Double-Entry Ledger Service  
**Author:** Samsul Hudha  
**AI Assistant:** Google Antigravity (Advanced Agentic Pair Programming)  
**Date:** October 2026  

---

## 1. Overview of AI Assistance

In accordance with assignment guidelines permitting AI tools, **Google Antigravity** was utilized as an agentic pair-programmer, financial systems reviewer, and QA automation engineer.

The collaboration was governed by strict architectural constraints:
- **Zero Hallucination Policy**: No mock assumptions; all financial, concurrency, and double-entry invariants had to be mathematically verified against live PostgreSQL.
- **Financial Rigor**: Never permit floating-point arithmetic for money. All values are represented as integer minor units (`BigIntegerField` in paise/cents).
- **Correctness Over Code Quantity**: Deeply tested core logic (concurrency, row-locking order, double-entry balancing) was prioritized over bloated features.

---

## 2. Prompt Evolution & Engineering Stages

### Stage 1: Core Domain Modeling & Double-Entry Ledger
- **Prompts**: Defined requirements for clean modular Django apps (`accounts`, `wallets`, `ledger`, `payments`, `risk`, `audit`, `common`).
- **AI Output & Iteration**: Created `LedgerAccount`, `LedgerTransaction`, and immutable `LedgerEntry` models overriding `.save()` and `.delete()`. Applied non-negative database check constraints on `Wallet`.

### Stage 2: Concurrency & Deadlock Elimination
- **Prompts**: Designed tests for 100 simultaneous transfers out of a single wallet without overdrawing, and simultaneous bidirectional transfers ($A \to B$ and $B \to A$).
- **AI Output & Iteration**: Identified PostgreSQL circular wait conditions. Implemented deterministic ascending primary-key lock ordering (`sorted([source_id, dest_id])`) prior to `select_for_update()`, mathematically eliminating deadlocks.

### Stage 3: Asynchronous Banking Outbox Pattern
- **Prompts**: Implemented asynchronous mock banking without Redis or Celery to avoid distributed dual-write inconsistencies.
- **AI Output & Iteration**: Engineered a PostgreSQL-backed outbox queue (`WithdrawalJob`) claimed by workers using `SELECT ... FOR UPDATE SKIP LOCKED`. Implemented HMAC-SHA256 callback verification and callback deduplication (`BankCallbackLog`).

### Stage 4: Critical 10-Step Refund & Reconciliation
- **Prompts**: Hardened withdrawal failure refund mechanics and automated reconciliation across all 6 financial lifecycle events.
- **AI Output & Iteration**: Developed `tests/e2e/test_critical_withdrawal_refund.py` and `tests/reconciliation/test_reconciliation_all_six_lifecycle_events.py` proving that $\sum \text{Debits} == \sum \text{Credits}$ at every step with 0 discrepancy.

### Stage 5: Security Review & Test Suite Expansion
- **Prompts**: Conducted security audit for token tampering, expiration, cross-tenant wallet access, privilege escalation, and SQL injection.
- **AI Output & Iteration**: Built `tests/security/test_security_and_auth.py` and expanded the automated test suite to 69 tests across all 6 domain directories.

---

## 3. Human Oversight & Architectural Decisions

Every AI proposal underwent senior-level engineering scrutiny:
1. **Integer Minor Units**: Rejected initial suggestions of `DecimalField` in favor of `BigIntegerField` minor units (paise) to guarantee identical representation in database constraints and application memory.
2. **Deterministic Locking vs Retries**: Rejected retry loops for deadlocks in favor of sorting primary keys before acquiring row locks.
3. **Reversal Solvency Guard**: Explicitly mandated that transfer reversals fail with HTTP 400 if the recipient lacks sufficient available funds, preventing the system from driving customer balances negative.
4. **Outbox Pattern over Celery**: Mandated PostgreSQL transactional outbox to eliminate distributed dual-write failure modes.

The complete chronological conversation transcript is preserved in [`docs/AI_CONVERSATIONS.md`](docs/AI_CONVERSATIONS.md).
