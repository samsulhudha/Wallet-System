# Architectural Design Decisions & Trade-Off Analysis

This document outlines the architectural patterns, database modeling choices, locking strategies, and engineering trade-offs made in the **Enterprise Wallet Service**.

---

## 1. Database Choice: PostgreSQL 17

### Rationale
- **ACID Guarantees**: Financial systems cannot tolerate partial transaction commits or dirty reads. PostgreSQL provides strict Serializable or Read Committed isolation levels with reliable row locking (`SELECT FOR UPDATE`).
- **PostgreSQL Row-Level Locking (`SKIP LOCKED`)**: Enables implementing high-throughput asynchronous job queues (outbox pattern) directly inside the database without introducing third-party broker dependencies like Redis or RabbitMQ.
- **Declarative Database Constraints**: Check constraints (`CHECK (available_balance >= 0)`) and unique constraints enforce non-negotiable financial rules directly at the database engine level, preventing corrupt states even if an application bug bypasses service validation.

### Trade-offs & Alternatives
| Alternative | Reason Rejected / Trade-off |
| :--- | :--- |
| **MySQL / MariaDB** | Less mature concurrency semantics around multi-table row-locking order and weaker check constraint history in earlier versions. |
| **MongoDB / NoSQL** | Lack of multi-document relational transactional integrity across separate collections without significant performance and operational penalties. |

---

## 2. Ledger Architecture: Immutable Double-Entry Bookkeeping

### Accounting Invariant
Every financial transaction consists of a balanced `LedgerTransaction` containing two or more `LedgerEntry` records such that:
$$\sum \text{Debits} \equiv \sum \text{Credits}$$

### Account Classification
- **Customer Wallets (Available & Reserved)** are classified as **Liabilities** (funds the platform holds on behalf of users):
  - **Credit (+)** increases liability (increases wallet balance).
  - **Debit (-)** decreases liability (decreases wallet balance).
- **System Clearing Accounts (Gateway & Bank)** are classified as **Assets**:
  - **Debit (+)** increases asset (receivable from payment processor).
  - **Credit (-)** decreases asset (cash disbursed through banking partner).

### Immutability Implementation
- `LedgerEntry` models override both `.save()` and `.delete()`:
  ```python
  def save(self, *args, **kwargs):
      if self.pk is not None:
          raise PermissionDenied("Ledger entries are strictly immutable and cannot be updated.")
      super().save(*args, **kwargs)

  def delete(self, *args, **kwargs):
      raise PermissionDenied("Ledger entries are strictly immutable and cannot be deleted.")
  ```
- **Correction Strategy**: Financial mistakes or cancellations are never corrected by editing past entries; they are resolved exclusively through **compensating transactions** (e.g., reversals and failure refunds).

---

## 3. Concurrency Control & Deadlock Prevention

### Problem: High-Contention P2P Transfers
When User A transfers to User B, and simultaneously User B transfers to User A in another thread, random lock acquisition leads to circular wait states:
- Thread 1 locks Wallet A and requests lock on Wallet B.
- Thread 2 locks Wallet B and requests lock on Wallet A.
- **Result:** PostgreSQL Deadlock (`40P01: deadlock detected`).

### Solution: Deterministic Ascending Primary-Key Ordering
Prior to acquiring database locks, the service sorts all participating wallet IDs in ascending primary-key order:
```python
ordered_ids = sorted([source_wallet_id, dest_wallet_id])
wallets = {
    w.id: w
    for w in Wallet.objects.filter(id__in=ordered_ids).select_for_update().order_by('id')
}
```
Because every transaction in the system acquires locks in the exact same order ($A$ before $B$), circular wait conditions are mathematically impossible.

### Pessimistic vs Optimistic Locking
- **Chosen: Pessimistic Row Locking (`select_for_update`)**:
  - *Rationale*: Active wallets experience high write contention. Optimistic Concurrency Control (OCC) using version numbers causes massive transaction retry storms and high abort rates under load. Pessimistic locking queues requests cleanly with bounded wait times.

---

## 4. Idempotency Strategy

### Key Scope & Storage
Idempotency records are scoped to:
$$\text{Scope} = (\text{user\_id}, \text{idempotency\_key}, \text{endpoint})$$
Enforced by a database unique constraint on `IdempotencyRecord`.

### Request Payload Hashing (SHA-256)
- The request body is canonicalized (keys sorted recursively, separators normalized) and hashed with SHA-256.
- If a client retries with the same `Idempotency-Key` but changes the amount or recipient, the service returns **HTTP 409 Conflict** (`"Idempotency key was already used with a different request payload."`).
- Identical retries immediately return the cached status code and response payload without re-executing any business logic or moving funds twice.

---

## 5. Asynchronous Banking: PostgreSQL Outbox Pattern

### Why Avoid Celery & Redis for this Assignment?
- **Broker Overhead**: Celery and Redis introduce distributed dual-write problems. If the database transaction commits but Redis crashes before the task is enqueued, funds are reserved forever without a withdrawal being dispatched.
- **PostgreSQL Outbox Table (`WithdrawalJob`)**:
  - The withdrawal record, fund reservation, and outbox job are committed in the **exact same ACID database transaction**.
  - Worker processes claim jobs using:
    ```sql
    SELECT * FROM withdrawal_jobs 
    WHERE status = 'PENDING' AND next_attempt_at <= NOW()
    FOR UPDATE SKIP LOCKED 
    LIMIT 10;
    ```
  - Multiple worker threads or pods run concurrently without lock contention or duplicate dispatches.

---

## 6. Fraud & Risk Rule Engine

### Design: Pluggable Pipeline with Precedence Hierarchy
- Each rule inherits from `BaseRiskRule` and implements `evaluate(wallet, amount, currency, context)`.
- Decisions follow strict precedence:
  $$\textbf{BLOCK} > \textbf{FLAG} > \textbf{ALLOW}$$
- Adding a new fraud rule (e.g., Geolocation check, Device fingerprinting) requires zero modifications to `PaymentService` or transfer views; the rule is simply registered in the engine.
- Every decision produces a persistent `RiskDecision` audit record, and flagged transactions are exposed to compliance officers at `GET /admin/flags/`.

---

## 7. Failure Recovery & Solvency Guards

### Reversal Solvency Guard
- If an administrator reverses a transfer, funds must be clawed back from the recipient's available balance.
- **Invariant**: A balance can **never become negative**.
- **Guard**: If the recipient has already spent or withdrawn the money (`available_balance < transfer.amount`), the reversal is **rejected with HTTP 400**. An administrative alert is logged instead of silently driving the balance negative.

### Ambiguous Timeout Handling
- If the mock bank times out during dispatch, the withdrawal job enters an exponential backoff retry cycle.
- **Crucial Rule**: **Funds are NEVER refunded automatically after a timeout**. An ambiguous timeout means the bank may still process the payout. Auto-refunding could cause a double payout if the bank later settles. Only confirmed failure webhooks (`PAYOUT_REJECTED`) trigger compensating refunds.
