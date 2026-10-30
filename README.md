# Enterprise Wallet & Double-Entry Ledger Service

[![Python](https://img.shields.io/badge/Python-3.12-blue.svg)](https://www.python.org/)
[![Django](https://img.shields.io/badge/Django-5.1-success.svg)](https://www.djangoproject.com/)
[![DRF](https://img.shields.io/badge/DRF-3.15-red.svg)](https://www.django-rest-framework.org/)
[![PostgreSQL](https://img.shields.io/badge/PostgreSQL-17-blue.svg)](https://www.postgresql.org/)
[![Tests](https://img.shields.io/badge/Tests-50%2F50%20Passing%20(100%25)-brightgreen.svg)]()
[![OpenAPI](https://img.shields.io/badge/OpenAPI-3.0%20(Swagger)-teal.svg)](http://localhost:8000/api/docs/)

Production-quality, high-concurrency digital wallet service built with **Django 5.1**, **Django REST Framework**, and **PostgreSQL 17**.

This system implements financial-grade double-entry accounting, row-level concurrency control, idempotency protection, asynchronous banking with durable PostgreSQL outbox queues, HMAC-signed webhook verification, pluggable fraud detection, and mathematical ledger reconciliation.

---

## Table of Contents

1. [Executive Summary & Capabilities](#1-executive-summary--capabilities)
2. [Architecture & Project Structure](#2-architecture--project-structure)
3. [Double-Entry Accounting Model](#3-double-entry-accounting-model)
4. [Concurrency Control & Deadlock Freedom](#4-concurrency-control--deadlock-freedom)
5. [Idempotency Strategy](#5-idempotency-strategy)
6. [Withdrawal State Machine & Mock Banking](#6-withdrawal-state-machine--mock-banking)
7. [Daily Limits & Pluggable Fraud Engine](#7-daily-limits--pluggable-fraud-engine)
8. [Wallet States, Admin Actions & Reversals](#8-wallet-states-admin-actions--reversals)
9. [Automated Reconciliation & Invariants](#9-automated-reconciliation--invariants)
10. [API Specification & Swagger Guide](#10-api-specification--swagger-guide)
11. [Setup & Running the Project (Step-by-Step)](#11-setup--running-the-project-step-by-step)
12. [Pre-Seeded Demo Accounts](#12-pre-seeded-demo-accounts)
13. [Running Automated Tests & Verifications](#13-running-automated-tests--verifications)
14. [Design Decisions & Trade-Offs](#14-design-decisions--trade-offs)

---

## 1. Executive Summary & Capabilities

The **Enterprise Wallet Service** provides digital money management for users and platforms with financial guarantees:

```
                  ┌──────────────────────────────────────────────┐
                  │          Client / Swagger UI / Web           │
                  └──────────────────────┬───────────────────────┘
                                         │  JWT Bearer + Idempotency-Key
                                         ▼
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │                         Django REST Framework API                           │
 │  ┌───────────────┐ ┌────────────────┐ ┌────────────────┐ ┌───────────────┐  │
 │  │ Authentication│ │ Wallet Balances│ │ Payments Layer │ │ Risk Engine   │  │
 │  │ (SimpleJWT)   │ │ & Statements   │ │ & Idempotency  │ │ (Pluggable)   │  │
 │  └───────┬───────┘ └────────┬───────┘ └────────┬───────┘ └───────┬───────┘  │
 └──────────┼──────────────────┼──────────────────┼─────────────────┼──────────┘
            │                  │                  │                 │
            ▼                  ▼                  ▼                 ▼
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │                         Authoritative Domain Services                       │
 │        PaymentService  •  LedgerService  •  ReconciliationService           │
 └──────────────────────────────────────┬──────────────────────────────────────┘
                                        │ ACID Transaction + select_for_update()
                                        ▼
 ┌─────────────────────────────────────────────────────────────────────────────┐
 │                         PostgreSQL 17 Transactional DB                      │
 │  ┌────────────────────────┐  ┌──────────────────────┐  ┌─────────────────┐  │
 │  │ Non-Negative Balances  │  │ Immutable Ledger     │  │ Outbox Worker   │  │
 │  │ CHECK (available >= 0) │  │ Sum(Debits)=Sum(Cred)│  │ SKIP LOCKED     │  │
 │  └────────────────────────┘  └──────────────────────┘  └─────────────────┘  │
 └─────────────────────────────────────────────────────────────────────────────┘
```

### Key Engineering Guarantees:
- **No Float Math**: All money is stored as integer minor units (`BigIntegerField` in paise/cents). Floating-point arithmetic is strictly prohibited across models and services.
- **Zero-Sum Ledger**: Every transaction posts balanced double-entry entries: $\sum \text{Debits} = \sum \text{Credits}$.
- **Tamper-Evident Ledger**: `LedgerEntry` models override `.save()` and `.delete()` to prevent mutations. History cannot be modified or deleted.
- **Deadlock-Free Row Locks**: Simultaneous bidirectional transfers ($A \to B$ and $B \to A$) lock rows in deterministic ascending primary-key order (`sorted([source_id, dest_id])`), mathematically eliminating circular wait conditions.
- **Zero Infrastructure Bloat**: Asynchronous bank withdrawals use a durable PostgreSQL outbox table with `SELECT ... FOR UPDATE SKIP LOCKED` (no Celery, Redis, or Kafka required).
- **Cryptographic Idempotency**: Scoped to `(user_id, idempotency_key, endpoint)` with SHA-256 payload hashing and HTTP 409 conflict detection.
- **Pluggable Risk Engine**: Evaluates configurable velocity and fraud rules (`BLOCK > FLAG > ALLOW`) with complete audit trails.

---

## 2. Architecture & Project Structure

The project strictly decouples views, serializers, domain services, and database models. **Zero financial logic exists in views or serializers**:

```
Wallet-System/
├── apps/
│   ├── accounts/             # Custom User model, JWT authentication, RBAC permissions, Admin
│   ├── wallets/              # Wallets, balances, state machine, statements API, Admin
│   ├── ledger/               # LedgerAccount, LedgerTransaction, LedgerEntry, Reconciliation
│   │   └── management/       # reconcile_ledger management command
│   ├── payments/             # Top-ups, P2P transfers, Withdrawals, Idempotency, Mock Bank
│   │   └── management/       # process_withdrawals durable outbox worker command
│   ├── risk/                 # Pluggable Fraud Rule Engine, RuleEvaluations, RiskDecision
│   │   └── rules/            # Velocity, AccountAge, RapidWithdrawal rules
│   ├── audit/                # Immutable append-only audit logs (WalletAuditLog, AdminAuditLog)
│   └── common/               # Money formatting (minor units), custom exceptions, pagination
│       └── management/       # seed_demo_data command
├── wallet_project/           # Settings, root URLs, WSGI/ASGI configurations
├── tests/
│   ├── unit/                 # Money conversions, ledger balancing, risk rules, idempotency hashing
│   ├── integration/          # Wallets, topups, transfers, withdrawals, reversals, statements, JWT
│   ├── concurrency/          # Multi-threaded tests (100 parallel transfers, deadlocks, freeze)
│   └── reconciliation/       # Balance audit & tamper detection tests
├── scripts/
│   └── verify_all_swagger_endpoints.py  # Automated verification testing all 18 API operations
├── docs/
│   └── AI_CONVERSATIONS.md   # Chronological AI pair-programming trajectory & decisions
├── openapi-schema.yaml       # 1,909-line static OpenAPI 3.0 specification export
├── docker-compose.yml        # PostgreSQL 17, Web (Gunicorn), and Outbox Worker containers
├── Dockerfile                # Multi-stage Python 3.12-slim production container
├── requirements.txt          # Pinned, tested dependencies
├── manage.py                 # Root management entrypoint
└── README.md
```

---

## 3. Double-Entry Accounting Model

### Money Representation
- All balances and amounts are stored in **minor units** (e.g., paise for INR, cents for USD).
  - Example: `₹1,000.00` is stored as `100000` paise.
- Negative balances are prevented at the database layer via PostgreSQL check constraints:
  ```sql
  CONSTRAINT check_wallet_available_balance_non_negative CHECK (available_balance >= 0)
  CONSTRAINT check_wallet_reserved_balance_non_negative CHECK (reserved_balance >= 0)
  ```

### Accounting Classification & Conventions
- **Customer Wallets (Available & Reserved)** are classified as **Liabilities** (money owed to users):
  - **Credit (+)** increases liability (adds funds to customer wallet).
  - **Debit (-)** decreases liability (deducts funds from customer wallet).
- **System Clearing Accounts (Gateway & Bank)** are classified as **Assets**:
  - **Debit (+)** increases assets (receivables from payment gateway).
  - **Credit (-)** decreases assets (cash dispatched through bank).

### Ledger Entries per Transaction Type

| Transaction Type | Debited Account (Debit) | Credited Account (Credit) | Invariant Effect |
| :--- | :--- | :--- | :--- |
| **Top-up** | `PAYMENT_GATEWAY_CLEARING` (Asset +) | `CUSTOMER_WALLET_AVAILABLE` (Liability +) | Available funds increase |
| **P2P Transfer** | Source `CUSTOMER_WALLET_AVAILABLE` (Liability -) | Dest `CUSTOMER_WALLET_AVAILABLE` (Liability +) | Funds shift atomically between users |
| **Withdrawal Reserve**| `CUSTOMER_WALLET_AVAILABLE` (Liability -) | `CUSTOMER_WALLET_RESERVED` (Liability +) | Funds locked pending bank dispatch |
| **Withdrawal Settle** | `CUSTOMER_WALLET_RESERVED` (Liability -) | `BANK_SETTLEMENT_CLEARING` (Asset -) | Reserved cleared, payout confirmed |
| **Withdrawal Refund** | `CUSTOMER_WALLET_RESERVED` (Liability -) | `CUSTOMER_WALLET_AVAILABLE` (Liability +) | Compensating refund on failure |
| **Transfer Reversal** | Dest `CUSTOMER_WALLET_AVAILABLE` (Liability -) | Source `CUSTOMER_WALLET_AVAILABLE` (Liability +) | Compensating clawback to sender |

Every transaction guarantees:
$$\sum \text{Debits} = \sum \text{Credits}$$

---

## 4. Concurrency Control & Deadlock Freedom

### Pessimistic Row Locking (`select_for_update()`)
Financial balance operations must never rely on application-level checks. All balance-mutating workflows acquire PostgreSQL row locks inside `transaction.atomic()`:
```python
wallet = Wallet.objects.select_for_update().get(id=wallet_id)
```

### Deterministic Ascending Lock Ordering
Bidirectional transfers (User A $\rightarrow$ User B and User B $\rightarrow$ User A simultaneously) can cause deadlocks if locks are acquired arbitrarily. We prevent deadlocks mathematically by sorting primary keys in ascending order:
```python
ordered_ids = sorted([source_wallet_id, dest_wallet_id])
wallets = {
    w.id: w
    for w in Wallet.objects.filter(id__in=ordered_ids).select_for_update().order_by('id')
}
```
Because all concurrent transactions acquire row locks in the exact same order ($A$ then $B$), circular wait conditions are impossible.

---

## 5. Idempotency Strategy

All money-moving endpoints require an `Idempotency-Key` HTTP header.

### Scope & Database Uniqueness
Idempotency records are scoped to:
$$\text{Scope} = (\text{user\_id}, \text{idempotency\_key}, \text{endpoint})$$
Enforced by a database unique constraint on `IdempotencyRecord`.

### Request Validation Lifecycle:
1. The client request body is canonicalized (keys sorted recursively, whitespace stripped) and hashed with **SHA-256**.
2. If an existing record exists:
   - **Identical Hash & Resolved**: Returns cached HTTP status code and response payload immediately (zero double-debit).
   - **Different Hash**: Returns **HTTP 409 Conflict** (`"Idempotency key was already used with a different request payload."`).
   - **In-Flight State**: Returns **HTTP 409 Conflict** (`"Request currently being processed. Please retry."`).
3. For asynchronous withdrawals, retrying with the original key returns the current live state of the withdrawal resource.

---

## 6. Withdrawal State Machine & Mock Banking

### State Machine Lifecycle
```
               ┌──────────────┐
               │  REQUESTED   │  (Funds reserved: Available -> Reserved)
               └──────┬───────┘
                      │ (Outbox worker claims with SKIP LOCKED)
               ┌──────▼───────┐
               │  PROCESSING  │
               └──────┬───────┘
          ┌───────────┴───────────┐
          │ (Bank Success)        │ (Bank Failure Callback)
   ┌──────▼──────┐         ┌──────▼──────┐
   │  COMPLETED  │         │   FAILED    │
   │ (Settled)   │         │ (Refunded)  │
   └─────────────┘         └─────────────┘
```

1. **Immediate Fund Reservation**: Available balance is debited and reserved balance is credited immediately upon request. Funds cannot be spent twice.
2. **PostgreSQL Outbox Queue (`WithdrawalJob`)**:
   Jobs are enqueued durably in `withdrawal_jobs` and claimed by workers using `SELECT ... FOR UPDATE SKIP LOCKED`.
3. **Webhook Verification & Deduplication**:
   - Webhooks to `/api/payments/callbacks/bank/` require an HMAC-SHA256 signature in the `X-Bank-Signature` header.
   - Deduplication is guaranteed via unique constraints on `BankCallbackLog.provider_event_id`. Duplicate callbacks are safely acknowledged without re-processing.
   - On confirmed rejection (`PAYOUT_REJECTED`), a compensating ledger entry refunds the reserved balance to available balance exactly once.

---

## 7. Daily Limits & Pluggable Fraud Engine

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

## 8. Wallet States, Admin Actions & Reversals

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

## 9. Automated Reconciliation & Invariants

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

## 10. API Specification & Swagger Guide

### Interactive Web Interfaces
- **Interactive Swagger UI**: [http://localhost:8000/api/docs/](http://localhost:8000/api/docs/)
- **ReDoc Documentation**: [http://localhost:8000/api/redoc/](http://localhost:8000/api/redoc/)
- **Raw OpenAPI 3.0 Schema**: [http://localhost:8000/api/schema/](http://localhost:8000/api/schema/)
- **Django Admin Portal**: [http://localhost:8000/admin/](http://localhost:8000/admin/)

### How to Authenticate in Swagger UI
1. Navigate to `POST /api/auth/token/`, click **Try it out**, and submit:
   ```json
   {
     "username": "admin",
     "password": "AdminPassword123!"
   }
   ```
2. Copy the string value in `"access"`.
3. Click the green **Authorize 🔓** button at the top right of the Swagger UI page.
4. Paste the token in the `jwtAuth (http, Bearer)` field and click **Authorize**.
5. All subsequent requests in Swagger UI will now be authenticated.

---

## 11. Setup & Running the Project (Step-by-Step)

You can run the project using **Docker Compose (recommended)** or via **Local Virtual Environment**.

### Method A: Docker Compose (Quickest & Recommended)

Requirements: Docker & Docker Compose installed.

1. **Clone the repository**:
   ```bash
   git clone https://github.com/samsulhudha/Wallet-System.git
   cd Wallet-System
   ```

2. **Start all services**:
   ```bash
   docker compose up --build
   ```
   *This automatically starts PostgreSQL 17, applies migrations, runs Gunicorn API server on port 8000, and starts the asynchronous banking outbox worker.*

3. **Seed demo users & rules** (in a separate terminal):
   ```bash
   docker compose exec web python manage.py seed_demo_data
   ```

4. **Verify Swagger UI**:
   Open [http://localhost:8000/api/docs/](http://localhost:8000/api/docs/) in your browser.

---

### Method B: Local Environment Setup (Python & PostgreSQL)

Requirements: Python 3.12+, PostgreSQL 14+ installed and running.

#### Step 1: Clone and Navigate to Root Directory
```bash
git clone https://github.com/samsulhudha/Wallet-System.git
cd Wallet-System
```
> [!IMPORTANT]
> Always execute commands from the **root directory** `Wallet-System/` where `manage.py` resides. Do not `cd` into `wallet_project/`.

#### Step 2: Create & Activate Virtual Environment
- **Windows (PowerShell)**:
  ```powershell
  python -m venv .venv
  .\.venv\Scripts\activate
  ```
- **macOS / Linux (Bash)**:
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  ```

#### Step 3: Install Pinned Dependencies
```bash
pip install -r requirements.txt
```

#### Step 4: Configure Environment Variables
Copy `.env.example` (or edit `.env`):
```ini
SECRET_KEY=local-dev-secret-key-wallet-2026
DEBUG=True
ALLOWED_HOSTS=*

# PostgreSQL Credentials
DB_NAME=wallet_db
DB_USER=postgres
DB_PASSWORD=your_postgres_password
DB_HOST=127.0.0.1
DB_PORT=5432

BANK_WEBHOOK_SECRET=super-secret-bank-callback-key-2026
```

#### Step 5: Run Database Migrations
```bash
python manage.py migrate
```

#### Step 6: Seed Demo Accounts, Wallets & Fraud Rules
```bash
python manage.py seed_demo_data
```
*Creates `admin`, `alice` (₹5,000.00), `bob` (₹1,000.00), configures risk rules, and verifies ledger reconciliation.*

#### Step 7: Start the Django API Server
```bash
python manage.py runserver 0.0.0.0:8000
```
Open **[http://127.0.0.1:8000/api/docs/](http://127.0.0.1:8000/api/docs/)** to access Swagger UI.

#### Step 8: Start the Outbox Banking Worker (In a New Terminal)
To process asynchronous mock bank withdrawals:
```bash
# In a second terminal (activate .venv first):
python manage.py process_withdrawals --interval 1.0
```

---

## 12. Pre-Seeded Demo Accounts

Running `python manage.py seed_demo_data` automatically configures:

| Role | Username | Password | Email | Initial Balance | Permissions |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Administrator** | `admin` | `AdminPassword123!` | `admin@wallet.local` | — | Superuser, Staff, Admin Portal, Freeze/Unfreeze/Reversals |
| **Customer (Alice)** | `alice` | `AlicePassword123!` | `alice@wallet.local` | **₹5,000.00** (`500,000 paise`) | Standard Customer |
| **Customer (Bob)** | `bob` | `BobPassword123!` | `bob@wallet.local` | **₹1,000.00** (`100,000 paise`) | Standard Customer |

---

## 13. Running Automated Tests & Verifications

### 1. Run Complete Test Suite (50 Tests)
```bash
pytest
```
*Output: `======================== 50 passed in 74s ========================`*

### 2. Run Concurrency Benchmarks (Live PostgreSQL)
Tests 100 simultaneous transfers, deadlock-free bidirectional locking, and concurrent velocity thresholds:
```bash
pytest tests/concurrency/
```

### 3. Run Mathematical Ledger Reconciliation Audit
```bash
python manage.py reconcile_ledger
```
*Output: `RECONCILIATION SUCCESS: All balances match immutable ledger!`*

### 4. Run Automated Swagger Endpoint Verification
Tests all 18 documented Swagger operations sequentially with live HTTP assertions:
```bash
python scripts/verify_all_swagger_endpoints.py
```
*Output: `SUMMARY: 18 / 18 ENDPOINTS VERIFIED SUCCESSFULLY (100% PASS)`*

---

## 14. Design Decisions & Trade-Offs

| Area | Chosen Solution | Alternative Considered | Trade-off / Architectural Rationale |
| :--- | :--- | :--- | :--- |
| **Money Representation** | Integer minor units (`BigIntegerField`, paise/cents) | Float / DecimalField | Eliminates IEEE 754 precision inaccuracies. Integer operations are deterministic, exact, and fast in database indexes and constraints. |
| **Ledger Immutability** | Overridden `.save()` & `.delete()` on `LedgerEntry` | Soft deletes / editable tables | Financial records cannot be altered or deleted. Discrepancies require compensating ledger entries. |
| **Deadlock Prevention** | Ascending primary-key row-locking | Arbitrary lock acquisition / retry loops | Deterministically sorting wallet IDs (`sorted([source_id, dest_id])`) before executing `select_for_update()` eliminates circular wait conditions. |
| **Asynchronous Banking** | PostgreSQL table + `SKIP LOCKED` worker | Celery + Redis / RabbitMQ | Zero infrastructure overhead. Transactional boundaries guarantee the withdrawal job is enqueued if and only if the reservation transaction commits. |
| **Idempotency** | Database uniqueness `(user, key, endpoint)` + SHA-256 | In-memory Redis keys | PostgreSQL persistence guarantees idempotency survives process restarts, container redeployments, and cache evictions. |
| **Transfer Reversals** | Compensating transaction with solvency check | Mutating original records / negative balances | Preserves historical audit integrity. Reversals fail with HTTP 400 if the recipient lacks sufficient funds, upholding the non-negative balance invariant. |
| **Balances** | Denormalized cached balances + Reconciliation service | Pure on-the-fly ledger sums | Instant $O(1)$ balance queries and row locking. The continuous reconciliation engine audits and catches any discrepancy. |
