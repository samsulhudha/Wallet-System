# AI Conversation & Engineering Trajectory Log

This document records the AI pair-programming conversation history, prompt trajectory, architectural trade-offs, and engineering milestones undertaken during the design, development, and testing of the Enterprise Wallet Service.

---

## 1. Conversation Trajectory & Prompts

### Prompt 1: Master Specification & Production Architecture
**User Request:**
> "Build a Production-Quality Wallet Service Using Django REST Framework. Act as a Senior Backend Engineer and Software Architect with expertise in Django, Django REST Framework, PostgreSQL, financial systems, double-entry accounting, concurrency control, API security, and automated testing. Build a complete, runnable backend project satisfying:
> - Technology Stack: Python 3.12, Django, DRF, PostgreSQL, SimpleJWT, drf-spectacular, pytest, Docker Compose.
> - Modular architecture: `accounts`, `wallets`, `ledger`, `payments`, `risk`, `audit`, `common`.
> - Minor unit integer representation (paise/cents). Never floating-point.
> - Immutable double-entry ledger with zero-sum invariant and mathematical reconciliation.
> - Idempotency-Key support on all money-moving endpoints.
> - Atomic transfers with PostgreSQL `select_for_update()` row locking in deterministic ascending PK order.
> - Asynchronous mock bank integration via durable PostgreSQL outbox queue (`SKIP LOCKED`).
> - Daily limits, pluggable fraud rule engine, wallet state machine (`ACTIVE`, `FROZEN`, `CLOSED`), and transfer reversals."

**Engineering Execution:**
- Configured clean modular architecture with separate domain apps.
- Created `apps/ledger/models.py` with `LedgerAccount`, `LedgerTransaction`, and immutable `LedgerEntry` (overriding `.save()` and `.delete()` to prevent mutations).
- Created `apps/wallets/models.py` with PostgreSQL non-negative check constraints on `available_balance` and `reserved_balance`.
- Implemented `PaymentService` in `apps/payments/services.py` with deterministic primary-key sorting (`sorted([source_id, dest_id])`) before executing `select_for_update()`.
- Implemented PostgreSQL-backed outbox worker command `process_withdrawals` using `SELECT ... FOR UPDATE SKIP LOCKED`.
- Built the pluggable `RiskEngine` with `VelocityRule`, `AccountAgeRule`, and `RapidWithdrawalAfterTopUpRule`.
- Created management command `reconcile_ledger` and `ReconciliationService` computing ledger sum vs. wallet balance.

---

### Prompt 2: Core Requirements Alignment & Edge-Case Hardening
**User Request:**
> "Core requirements:
> - Wallets: one per user and currency. `GET /wallets/{id}` returns the balance.
> - Top-up: `POST /wallets/{id}/topups` adds funds (mock payment provider).
> - Transfer: `POST /transfers` moves money atomically between two wallets. A balance never goes negative.
> - Ledger: immutable double-entry records (debit and credit). Balances must be reconcilable from the ledger.
> - Idempotency: retry with same Idempotency-Key returns original result.
> - Concurrency: 100 simultaneous transfers out of one wallet must not overdraw it, and A to B plus B to A must not deadlock.
> - Statement: `GET /wallets/{id}/transactions` with date filters and pagination.
> - Reversals: admin can reverse a transfer with compensating entries. History is never edited or deleted.
> - Daily limit: configurable per wallet.
> - Auth: JWT authentication.
> - Extended features: Asynchronous mock bank with callback and failure refund, Velocity/Fraud rules, Wallet state machine."

**Engineering Execution:**
- Added 100-thread multi-threaded concurrency test (`test_100_simultaneous_transfers_insufficient_aggregate_funds`) verifying that from a 1,000 INR wallet with 100 threads attempting 50 INR transfers, exactly 20 succeed and 80 fail, leaving an ending balance of exactly 0.
- Implemented bidirectional deadlock test (`test_bidirectional_transfers_no_deadlock`) running concurrent $A \to B$ and $B \to A$ transfers across threads with zero deadlocks.
- Implemented withdrawal failure refund test verifying compensating ledger entries restore reserved balance to available balance on mock bank rejection.
- Implemented admin reversal endpoint `POST /api/admin/transfers/{id}/reverse/` with check preventing reversal if recipient balance is insufficient.

---

### Prompt 3: Statement-Based Balance Checking & E2E Tests
**User Request:**
> "this statement based checking and e2e test"

**Engineering Execution:**
- Created `tests/integration/test_statement_checking.py`:
  - Verified that summing `Credits - Debits` from `GET /api/wallets/{id}/transactions/` matches the wallet's `available_balance` and `reserved_balance` to the exact paise.
  - Tested datetime boundary filtering (`start_date`, `end_date`) and cursor/page pagination.
- Created `tests/integration/test_e2e_workflow.py`:
  - Executed a complete 12-step end-to-end journey from user registration, JWT issue, wallet creation, top-up, P2P transfer, mock bank withdrawal, admin freeze blocking transfers, admin transfer reversal, to full ledger reconciliation.

---

### Prompt 4: Swagger Documentation & JWT Token Lifecycle Verification
**User Request:**
> "e2e test given list check swagger and jwt token"

**Engineering Execution:**
- Created `tests/integration/test_swagger_and_jwt.py`:
  - Validated OpenAPI 3.0 schema generation at `GET /api/schema/`.
  - Verified Swagger UI (`/api/docs/`) and ReDoc (`/api/redoc/`) HTTP 200 responses.
  - Verified JWT security scheme `jwtAuth` (HTTP Bearer).
  - Verified SimpleJWT token lifecycle: issue (`/api/auth/token/`), refresh (`/api/auth/token/refresh/`), token payload claims (`user_id`), authorization header enforcement, and rejection of tampered tokens.
- Exported static OpenAPI specification to `openapi-schema.yaml` (1,909 lines).

---

### Prompt 5: Visual Testing & Interactive Ledger Simulator
**User Request:**
> "i want visual test"

**Engineering Execution:**
- Designed an interactive visual dashboard and simulator:
  - Embedded inline widget: `visual_wallet_widget.html`
  - Standalone full-screen dashboard: `visual_test_dashboard.html`
- Features:
  - Interactive wallet balances (Alice ₹1,000.00 / Bob ₹200.00) in minor units (paise).
  - Live simulation of Top-up, Transfer, and Withdrawal reservation.
  - Asynchronous Mock Bank callback trigger (Success Payout Settlement vs. Rejection Refund).
  - Live immutable double-entry ledger stream updating with zero-sum invariant check ($\sum \text{Debits} = \sum \text{Credits}$).
  - Live Wallet Freeze / Unfreeze toggle demonstrating race-safe transfer blockage.
  - Filterable test matrix of all 50 passing automated tests.

---

## 2. Key Architectural Decisions & Engineering Trade-Offs

| Decision | Selected Strategy | Alternatives Considered | Rationale & Trade-offs |
| :--- | :--- | :--- | :--- |
| **Money Storage** | `BigIntegerField` minor units (paise/cents) | `DecimalField`, `FloatField` | Eliminates all IEEE 754 precision issues and rounding errors. Integer math is exact, deterministic, and fast in database queries and constraints. |
| **Double-Entry Ledger** | Immutable append-only `LedgerEntry` rows | Mutable balance column only | Financial accountability. Historical entries can never be edited or deleted. Discrepancies require compensating entries. |
| **Deadlock Prevention** | Deterministic Ascending PK Sorting | Random lock order, retry loops | By acquiring locks in ascending ID order (`sorted([source_id, dest_id])`), circular wait states are mathematically prevented. |
| **Async Outbox** | PostgreSQL table + `SKIP LOCKED` worker | Celery + Redis / RabbitMQ | Zero additional infrastructure. Transactional boundary guarantees job is enqueued if and only if funds reservation succeeds. |
| **Idempotency** | Scoped `(user, key, endpoint)` + SHA-256 body hash | Ephemeral Redis cache | Durable PostgreSQL record ensures idempotency survives service restarts and cache evictions. Hash detects payload mismatches (HTTP 409). |
| **Fraud Engine** | Pluggable Rule Pipeline with Precedence | Hardcoded `if/else` checks | Allows adding new compliance rules without touching payment or transfer logic. Precedence: `BLOCK > FLAG > ALLOW`. |
| **Reversals** | Compensating transaction with solvency check | Modifying old rows / negative balance | History is preserved. If recipient lacks funds to reverse, reversal is rejected with HTTP 400 to prevent negative balances. |

---

## 3. Automated Verification Summary

- **Total Tests Executed:** 50
- **Total Tests Passed:** 50 (100%)
- **Test Categories:**
  - 18 Unit Tests (`test_money`, `test_ledger`, `test_risk_rules`, `test_idempotency`)
  - 20 Integration Tests (`test_wallets`, `test_topups`, `test_transfers`, `test_withdrawals`, `test_callbacks`, `test_reversals`, `test_statements`, `test_limits`, `test_admin_states`, `test_swagger_and_jwt`)
  - 6 PostgreSQL Concurrency Tests (`test_concurrent_transfers`, `test_bidirectional_transfers`, `test_concurrent_velocity`, `test_concurrent_idempotency`, `test_concurrent_callbacks`, `test_concurrent_freeze`)
  - 3 Ledger Reconciliation Tests (`test_reconciliation`)
  - 3 E2E & Statement Audit Tests (`test_statement_checking`, `test_e2e_workflow`)
