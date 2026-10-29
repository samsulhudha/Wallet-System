# Enterprise Wallet & Double-Entry Ledger Service

Production-quality, high-concurrency digital wallet service built with **Django 5.1**, **Django REST Framework**, and **PostgreSQL 17**.

This system implements financial-grade double-entry accounting, row-level concurrency control, idempotency protection, asynchronous banking with durable PostgreSQL outbox queues, HMAC-signed webhook verification, pluggable fraud detection, and mathematical ledger reconciliation.

---

## Table of Contents

1. [Architecture & Project Structure](#1-architecture--project-structure)
2. [Accounting Model & Conventions](#2-accounting-model--conventions)
3. [Concurrency Control & Deadlock Prevention](#3-concurrency-control--deadlock-prevention)
4. [Idempotency Strategy](#4-idempotency-strategy)
5. [Withdrawal State Machine & Mock Banking](#5-withdrawal-state-machine--mock-banking)
6. [Daily Limits & Pluggable Fraud Engine](#6-daily-limits--pluggable-fraud-engine)
7. [Wallet States, Admin Actions & Reversals](#7-wallet-states-admin-actions--reversals)
8. [Reconciliation Architecture](#8-reconciliation-architecture)
9. [API Documentation & Security](#9-api-documentation--security)
10. [Local Development & Docker Setup](#10-local-development--docker-setup)
11. [Running Automated Tests](#11-running-automated-tests)
12. [Trade-offs & Production Considerations](#12-trade-offs--production-considerations)

---

## 1. Architecture & Project Structure

The project follows domain-driven modular boundaries with zero financial logic in views or serializers:

```
Wallet-System/
├── apps/
│   ├── accounts/             # Custom User model, JWT authentication, RBAC permissions
│   ├── wallets/              # Wallets, balances, state machine, statements API
│   ├── ledger/               # LedgerAccount, LedgerTransaction, LedgerEntry, Reconciliation
│   │   └── management/       # reconcile_ledger command
│   ├── payments/             # Top-ups, P2P transfers, Withdrawals, Idempotency, Mock Bank
│   │   └── management/       # process_withdrawals durable worker command
│   ├── risk/                 # Pluggable Fraud Rule Engine, RuleEvaluations, RiskDecision
│   │   └── rules/            # Velocity, AccountAge, RapidWithdrawal rules
│   ├── audit/                # Immutable append-only audit logs (WalletAuditLog, AdminAuditLog)
│   └── common/               # Money formatting (minor units), custom exceptions, pagination
├── wallet_project/           # Settings, root URLs, WSGI/ASGI configurations
├── tests/
│   ├── unit/                 # Money, ledger balancing, risk rules, idempotency hashing
│   ├── integration/          # Wallets, topups, transfers, withdrawals, reversals, limits
│   ├── concurrency/          # Real PostgreSQL tests (100 parallel transfers, deadlocks, etc.)
│   └── reconciliation/       # Balance audit & tamper detection tests
├── docker-compose.yml        # PostgreSQL, Web (Gunicorn), and Worker containers
├── Dockerfile                # Multi-stage Python 3.12-slim container
├── requirements.txt          # Pinned production dependencies
└── manage.py
```

---

## 2. Accounting Model & Conventions

### Money Representation
- All monetary amounts are internally represented as **integer minor units** (`BigIntegerField`, e.g., paise for INR, cents for USD).
- Floating-point representations are strictly forbidden across models and service calculations to eliminate IEEE 754 precision inaccuracies.
- Non-negative balances are guaranteed via PostgreSQL check constraints:
  ```sql
  CONSTRAINT check_wallet_available_balance_non_negative CHECK (available_balance >= 0)
  CONSTRAINT check_wallet_reserved_balance_non_negative CHECK (reserved_balance >= 0)
  ```

### Double-Entry Bookkeeping Conventions
In accordance with standard financial accounting:
- **Customer Wallet Accounts (Available & Reserved)** are classified as **Liabilities** (the platform owes funds to the user):
  - **Credit (+)** increases customer liability (increases wallet balance).
  - **Debit (-)** decreases customer liability (decreases wallet balance).
- **System Clearing Accounts (Gateway & Bank)** are classified as **Assets**:
  - **Debit (+)** increases assets (receivables from payment gateway).
  - **Credit (-)** decreases assets (cash paid out through bank).

For every `LedgerTransaction`, the fundamental accounting invariant holds:
$$\sum \text{Debits} = \sum \text{Credits}$$

### Ledger Entries per Transaction Type

| Transaction Type | Debited Account (Debit) | Credited Account (Credit) | Invariant Effect |
| :--- | :--- | :--- | :--- |
| **Top-up** | `PAYMENT_GATEWAY_CLEARING` (Asset +) | `CUSTOMER_WALLET_AVAILABLE` (Liability +) | Available funds increase |
| **Transfer** | Source `CUSTOMER_WALLET_AVAILABLE` (Liability -) | Dest `CUSTOMER_WALLET_AVAILABLE` (Liability +) | Funds shift between users |
| **Withdrawal Reserve**| `CUSTOMER_WALLET_AVAILABLE` (Liability -) | `CUSTOMER_WALLET_RESERVED` (Liability +) | Funds locked pending bank |
| **Withdrawal Settle** | `CUSTOMER_WALLET_RESERVED` (Liability -) | `BANK_SETTLEMENT_CLEARING` (Asset -) | Reserved cleared, payout sent |
| **Withdrawal Refund** | `CUSTOMER_WALLET_RESERVED` (Liability -) | `CUSTOMER_WALLET_AVAILABLE` (Liability +) | Compensating refund on failure |
| **Transfer Reversal** | Dest `CUSTOMER_WALLET_AVAILABLE` (Liability -) | Source `CUSTOMER_WALLET_AVAILABLE` (Liability +) | Compensating clawback to sender |

`LedgerEntry` models override `.save()` and `.delete()` to prevent updates or deletions.

---

## 3. Concurrency Control & Deadlock Prevention

### Row-Level Locking (`select_for_update`)
Financial balance operations must never rely on application-level checks. All balance-mutating workflows acquire PostgreSQL row locks inside `transaction.atomic()`:
```python
wallet = Wallet.objects.select_for_update().get(id=wallet_id)
```

### Deterministic Ascending Lock Ordering
Bidirectional transfers (User A $\rightarrow$ User B and User B $\rightarrow$ User A) can trigger database deadlocks if locks are acquired arbitrarily. We prevent deadlocks mathematically by sorting primary keys in ascending order prior to lock acquisition:
```python
ordered_ids = sorted([source_wallet_id, dest_wallet_id])
wallets = {
    w.id: w
    for w in Wallet.objects.filter(id__in=ordered_ids).select_for_update().order_by('id')
}
```
Because all concurrent transactions acquire row locks in the exact same order ($A$ then $B$), circular wait conditions are impossible.

---

## 4. Idempotency Strategy

All money-moving endpoints require an `Idempotency-Key` HTTP header.

### Scope & Database Uniqueness
Idempotency records are scoped to:
$$\text{Scope} = (\text{user\_id}, \text{idempotency\_key}, \text{endpoint})$$
Enforced by a database unique constraint in `IdempotencyRecord`.

### Request Payload Validation
1. Client payload is canonicalized (keys sorted recursively, whitespace stripped) and hashed via **SHA-256**.
2. If an existing record is found:
   - **Identical Hash & Resolved**: Returns cached HTTP status code and response payload immediately.
   - **Different Hash**: Returns **HTTP 409 Conflict** (`"Idempotency key was already used with a different request payload."`).
   - **Pending State**: Returns **HTTP 409 Conflict** (`"Request currently being processed. Please retry."`).
3. For asynchronous withdrawals, retrying with the original key returns the current live state of the withdrawal resource.

---

## 5. Withdrawal State Machine & Mock Banking

### State Machine Transitions
```
              ┌──────────────┐
              │  REQUESTED   │
              └──────┬───────┘
                     │ (Worker picks up)
              ┌──────▼───────┐
              │  PROCESSING  │
              └──────┬───────┘
         ┌───────────┴───────────┐
         │ (Bank Success)        │ (Bank Failure)
  ┌──────▼──────┐         ┌──────▼──────┐
  │  COMPLETED  │         │   FAILED    │
  │ (Settled)   │         │ (Refunded)  │
  └─────────────┘         └─────────────┘
```

1. **Immediate Fund Reservation**: Available balance is debited and reserved balance is credited immediately upon request. Funds cannot be spent twice.
2. **PostgreSQL Outbox Queue (`WithdrawalJob`)**:
   Jobs are enqueued durably in `withdrawal_jobs` and claimed by workers using `SELECT ... FOR UPDATE SKIP LOCKED`, allowing horizontally scalable worker instances without lock contention.
3. **Ambiguous Timeouts**: If the mock bank times out or returns an unknown outcome, the job is rescheduled or marked for reconciliation. **Funds are never refunded automatically after an ambiguous timeout**, preventing double payouts if the bank eventually executes the transfer.
4. **Callback Verification & Deduplication**:
   - Webhooks to `/api/payments/callbacks/bank/` require an HMAC-SHA256 signature in the `X-Bank-Signature` header.
   - Deduplication is guaranteed via unique database constraints on `BankCallbackLog.provider_event_id`. Duplicate callbacks are safely acknowledged without re-processing.
   - On confirmed rejection (`PAYOUT_REJECTED`), a compensating ledger entry refunds the reserved balance to available balance exactly once.

---

## 6. Daily Limits & Pluggable Fraud Engine

### Daily Limits Policy
- Outgoing limits operate on a **rolling 24-hour window** (`timezone.now() - timedelta(hours=24)`).
- Considers:
  $$\text{Outgoing Total} = \sum \text{Completed Transfers} + \sum \text{Pending/Completed Withdrawals}$$
- Enforced atomically under the source wallet's row lock.

### Pluggable Risk Engine
Precedence hierarchy:
$$\textbf{BLOCK} > \textbf{FLAG} > \textbf{ALLOW}$$

Built-in rules:
1. **`VelocityRule`**: Flags/blocks if more than $N$ transfers occur within $M$ minutes (default: > 5 transfers in 5 minutes). Safe under concurrency due to source wallet row locking.
2. **`AccountAgeRule`**: Flags/blocks high-value transfers ($> 50,000$ INR) on accounts younger than $Y$ days (default: 7 days).
3. **`RapidWithdrawalAfterTopUpRule`**: Flags rapid withdrawals requested within 30 minutes of a large incoming top-up ($> 10,000$ INR).

Dynamic configuration is supported via `RiskRuleConfig` in the database. New rules can be plugged in by inheriting `BaseRiskRule` without modifying transfer or withdrawal services.

---

## 7. Wallet States, Admin Actions & Reversals

### Wallet Lifecycle
- **`ACTIVE`**: Unrestricted sending and receiving.
- **`FROZEN`**: Can receive transfers and top-ups, but **cannot transfer out or withdraw**.
- **`CLOSED`**: Cannot send, receive, or accept top-ups.
  - **Prerequisites for closure**: Available balance $= 0$, reserved balance $= 0$, and no pending withdrawals.

Admin endpoints:
- `POST /api/admin/wallets/{id}/freeze/` (requires reason)
- `POST /api/admin/wallets/{id}/unfreeze/` (requires reason)
- `POST /api/admin/wallets/{id}/close/` (requires reason)

### Transfer Reversals
- Endpoint: `POST /api/admin/transfers/{id}/reverse/` (Admin-only, requires reason and idempotency key).
- Only completed, unreversed transfers can be reversed.
- **Insufficient Funds Policy**: If the recipient has already withdrawn or spent the funds (`available_balance < transfer.amount`), the reversal is **rejected with HTTP 400** to uphold the invariant that balances can never become negative.
- Creates a compensating ledger transaction: Debits recipient available balance, credits source available balance.

---

## 8. Reconciliation Architecture

The service includes an automated reconciliation engine in `apps.ledger.reconciliation.ReconciliationService` and a management command:
```bash
python manage.py reconcile_ledger
```

### Audits Performed:
1. **Wallet-to-Ledger Consistency**:
   Calculates expected balances from immutable `LedgerEntry` records:
   $$\text{Expected Available} = \sum \text{Credits}_{\text{Available}} - \sum \text{Debits}_{\text{Available}}$$
   $$\text{Expected Reserved} = \sum \text{Credits}_{\text{Reserved}} - \sum \text{Debits}_{\text{Reserved}}$$
   Compares with cached balances on `Wallet`.
2. **Transaction Zero-Sum Balancing**:
   Validates every `LedgerTransaction` individually to ensure $\sum \text{Debits} == \sum \text{Credits}$.
3. **Global Currency Integrity**:
   Confirms total debits equal total credits across all entries in the entire system.

Financial history is **never silently modified**. Any discrepancy halts reconciliation and produces a forensic audit report.

---

## 9. API Documentation & Security

### OpenAPI & Swagger UI
- Interactive Swagger UI: `http://localhost:8000/api/docs/`
- ReDoc UI: `http://localhost:8000/api/redoc/`
- Raw OpenAPI 3.0 Schema: `http://localhost:8000/api/schema/`

### Authentication & Authorization
- **JWT Authentication** (`djangorestframework-simplejwt`): Bearer tokens on `Authorization: Bearer <token>`.
- Object-level wallet permissions: Customers can only query their own wallets. Staff/Admin users have access to administrative operations.
- Dedicated endpoints:
  - `POST /api/auth/register/` - Register account
  - `POST /api/auth/token/` - Obtain JWT access/refresh pair
  - `POST /api/auth/token/refresh/` - Refresh JWT token
  - `GET /api/auth/me/` - Current user profile
  - `GET /api/wallets/` & `POST /api/wallets/` - List/create wallets
  - `GET /api/wallets/{id}/` - Retrieve wallet & balances
  - `POST /api/wallets/{id}/topups/` - Fund wallet
  - `POST /api/transfers/` - Atomic P2P transfer
  - `POST /api/wallets/{id}/withdrawals/` - Request bank withdrawal
  - `GET /api/withdrawals/{id}/` - Query withdrawal status
  - `GET /api/wallets/{id}/transactions/` - Paginated statement history
  - `POST /api/payments/callbacks/bank/` - Mock bank webhook
  - `POST /api/admin/wallets/{id}/freeze/` - Admin freeze
  - `POST /api/admin/wallets/{id}/unfreeze/` - Admin unfreeze
  - `POST /api/admin/wallets/{id}/close/` - Admin close
  - `POST /api/admin/transfers/{id}/reverse/` - Admin reversal
  - `GET /api/admin/risk/flagged/` - Flagged fraud reviews

---

## 10. Local Development & Docker Setup

### Running with Docker Compose

The complete system (PostgreSQL 17, Web API, and Async Outbox Worker) runs via:

```bash
docker compose up --build
```

To run in the background:
```bash
docker compose up -d --build
```

Create a superuser inside the container:
```bash
docker compose exec web python manage.py createsuperuser
```

Run migrations manually if needed:
```bash
docker compose exec web python manage.py migrate
```

Trigger reconciliation inside Docker:
```bash
docker compose exec web python manage.py reconcile_ledger
```

---

## 11. Running Automated Tests

The test suite covers unit tests, integration tests, reconciliation audits, and real PostgreSQL concurrency benchmarks.

### Run All Tests
```bash
pytest
```

### Run Concurrency Tests
Verified against PostgreSQL with multi-threaded connection pools:
```bash
pytest tests/concurrency/
```

### Run Reconciliation Tests
```bash
pytest tests/reconciliation/
```

### Run Unit & Integration Tests
```bash
pytest tests/unit/ tests/integration/
```

---

## 12. Trade-offs & Production Considerations

1. **PostgreSQL Outbox vs Celery/Kafka**:
   - *Design Choice*: A PostgreSQL-backed job queue with `SELECT ... FOR UPDATE SKIP LOCKED` eliminates Celery, Redis, and broker failure modes while providing transactional atomicity between business operations and job creation.
   - *Trade-off*: Suitable for thousands of transactions per minute. At ultra-high hyperscale ($> 50,000$ tx/sec), moving queue operations to an external event log (Kafka) with Debezium CDC is recommended.
2. **Denormalized Balances with Invariant Auditing**:
   - *Design Choice*: Storing cached `available_balance` and `reserved_balance` on the `Wallet` model enables sub-millisecond balance checks and row locks, backed by the immutable ledger as the authoritative source of truth.
   - *Protection*: The continuous reconciliation service detects any balance cache discrepancy immediately.
3. **Pessimistic Row Locking vs Optimistic Concurrency Control (OCC)**:
   - *Design Choice*: High-velocity money transfers exhibit high write contention on active wallets. Pessimistic row locking (`select_for_update`) prevents transaction retry storms and guarantees non-negative balances.
