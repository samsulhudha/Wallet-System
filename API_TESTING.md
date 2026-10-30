# API Testing Guide & Operational Walkthrough

This document provides sample HTTP requests, headers, expected response payloads, and common error scenarios for testing the **Enterprise Wallet Service**.

---

## Base URL & Interactive Docs
- **Base API URL**: `http://localhost:8000/api/`
- **Interactive Swagger UI**: [http://localhost:8000/api/docs/](http://localhost:8000/api/docs/)
- **Interactive ReDoc UI**: [http://localhost:8000/api/redoc/](http://localhost:8000/api/redoc/)

---

## 1. Authentication Endpoints

### 1.1 Obtain JWT Access & Refresh Token
- **Method / URL**: `POST /api/auth/token/`
- **Request Body**:
```json
{
  "username": "alice",
  "password": "AlicePassword123!"
}
```
- **Success Response (HTTP 200 OK)**:
```json
{
  "refresh": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...",
  "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

### 1.2 Refresh Access Token
- **Method / URL**: `POST /api/auth/token/refresh/`
- **Request Body**:
```json
{
  "refresh": "<YOUR_REFRESH_TOKEN>"
}
```
- **Success Response (HTTP 200 OK)**:
```json
{
  "access": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9..."
}
```

---

## 2. Wallet Endpoints

### 2.1 Create Wallet
- **Method / URL**: `POST /api/wallets/`
- **Headers**: `Authorization: Bearer <ACCESS_TOKEN>`
- **Request Body**:
```json
{
  "currency": "USD"
}
```
- **Success Response (HTTP 201 Created)**:
```json
{
  "id": 14,
  "owner_username": "alice",
  "currency": "USD",
  "state": "ACTIVE",
  "available_balance": 0,
  "available_balance_decimal": "0.00",
  "reserved_balance": 0,
  "reserved_balance_decimal": "0.00",
  "total_balance_decimal": "0.00",
  "created_at": "2026-10-30T10:00:00Z"
}
```

### 2.2 Retrieve Wallet & Balance
- **Method / URL**: `GET /api/wallets/4/`
- **Headers**: `Authorization: Bearer <ACCESS_TOKEN>`
- **Success Response (HTTP 200 OK)**:
```json
{
  "id": 4,
  "owner_username": "alice",
  "currency": "INR",
  "state": "ACTIVE",
  "available_balance": 500000,
  "available_balance_decimal": "5000.00",
  "reserved_balance": 0,
  "reserved_balance_decimal": "0.00",
  "total_balance_decimal": "5000.00",
  "created_at": "2026-10-30T09:00:00Z"
}
```

---

## 3. Top-Up (Funding) Endpoint

### 3.1 Fund Wallet
- **Method / URL**: `POST /api/wallets/4/topups/`
- **Headers**:
  - `Authorization: Bearer <ACCESS_TOKEN>`
  - `Idempotency-Key: topup-alice-test-001`
- **Request Body**:
```json
{
  "amount": "1000.00",
  "currency": "INR",
  "description": "Salary top-up"
}
```
- **Success Response (HTTP 201 Created)**:
```json
{
  "id": 15,
  "wallet": 4,
  "amount": 100000,
  "amount_decimal": "1000.00",
  "currency": "INR",
  "status": "COMPLETED",
  "provider_reference": "topup_b88b0a99ff1e42",
  "created_at": "2026-10-30T10:05:00Z"
}
```

---

## 4. Atomic P2P Transfers

### 4.1 Execute Transfer
- **Method / URL**: `POST /api/transfers/`
- **Headers**:
  - `Authorization: Bearer <ACCESS_TOKEN>`
  - `Idempotency-Key: transfer-alice-bob-001`
- **Request Body**:
```json
{
  "source_wallet_id": 4,
  "destination_wallet_id": 5,
  "amount": "250.00",
  "currency": "INR",
  "description": "Dinner split"
}
```
- **Success Response (HTTP 201 Created)**:
```json
{
  "id": 16,
  "source_wallet_id": 4,
  "destination_wallet_id": 5,
  "amount": 25000,
  "amount_decimal": "250.00",
  "currency": "INR",
  "status": "COMPLETED",
  "is_reversed": false,
  "description": "Dinner split",
  "created_at": "2026-10-30T10:07:00Z"
}
```

---

## 5. Transaction Statement & Audit

### 5.1 Query Statement
- **Method / URL**: `GET /api/wallets/4/transactions/?page=1&page_size=20`
- **Headers**: `Authorization: Bearer <ACCESS_TOKEN>`
- **Success Response (HTTP 200 OK)**:
```json
{
  "count": 2,
  "total_pages": 1,
  "current_page": 1,
  "results": [
    {
      "id": 25,
      "transaction_id": 16,
      "transaction_type": "TRANSFER",
      "direction": "DEBIT",
      "amount": 25000,
      "amount_decimal": "250.00",
      "balance_after_decimal": "4750.00",
      "created_at": "2026-10-30T10:07:00Z"
    },
    {
      "id": 24,
      "transaction_id": 15,
      "transaction_type": "TOPUP",
      "direction": "CREDIT",
      "amount": 100000,
      "amount_decimal": "1000.00",
      "balance_after_decimal": "5000.00",
      "created_at": "2026-10-30T10:05:00Z"
    }
  ]
}
```

---

## 6. Asynchronous Bank Withdrawals

### 6.1 Initiate Withdrawal Request
- **Method / URL**: `POST /api/wallets/4/withdrawals/`
- **Headers**:
  - `Authorization: Bearer <ACCESS_TOKEN>`
  - `Idempotency-Key: wdr-alice-001`
- **Request Body**:
```json
{
  "amount": "150.00",
  "currency": "INR",
  "destination_bank_account": "HDFC0001234_9876543210"
}
```
- **Success Response (HTTP 202 Accepted)**:
```json
{
  "id": 17,
  "wallet": 4,
  "amount": 15000,
  "amount_decimal": "150.00",
  "currency": "INR",
  "status": "REQUESTED",
  "provider_reference": "wdr_1e1eff022a41406392f5",
  "created_at": "2026-10-30T10:10:00Z"
}
```

### 6.2 Check Withdrawal Status
- **Method / URL**: `GET /api/withdrawals/17/`
- **Headers**: `Authorization: Bearer <ACCESS_TOKEN>`
- **Response**:
```json
{
  "id": 17,
  "status": "COMPLETED",
  "amount_decimal": "150.00"
}
```

---

## 7. Administrative APIs (Admin Authorization Required)

### 7.1 Freeze Wallet
- **Method / URL**: `POST /api/admin/wallets/4/freeze/`
- **Headers**: `Authorization: Bearer <ADMIN_TOKEN>`
- **Request Body**:
```json
{
  "reason": "Temporary compliance audit freeze"
}
```
- **Success Response (HTTP 200 OK)**:
```json
{
  "status": "success",
  "message": "Wallet #4 frozen successfully.",
  "wallet": {
    "id": 4,
    "state": "FROZEN"
  }
}
```

### 7.2 Reverse Completed Transfer
- **Method / URL**: `POST /api/admin/transfers/16/reverse/`
- **Headers**:
  - `Authorization: Bearer <ADMIN_TOKEN>`
  - `Idempotency-Key: rev-16-test`
- **Request Body**:
```json
{
  "reason": "Customer accidental duplicate charge"
}
```
- **Success Response (HTTP 200 OK)**:
```json
{
  "id": 16,
  "status": "REVERSED",
  "is_reversed": true
}
```

---

## 8. Common Error Responses

### 8.1 Idempotency Payload Conflict (HTTP 409 Conflict)
Occurs when an existing `Idempotency-Key` is re-sent with an altered request body:
```json
{
  "error": {
    "code": "IDEMPOTENCY_CONFLICT",
    "message": "Idempotency key was already used with a different request payload.",
    "details": {}
  }
}
```

### 8.2 Insufficient Funds (HTTP 400 Bad Request)
```json
{
  "error": {
    "code": "INSUFFICIENT_FUNDS",
    "message": "Insufficient available balance for transfer. Available: 20000, Requested: 50000",
    "details": {}
  }
}
```

### 8.3 Unauthorized Cross-Tenant Access (HTTP 403 Forbidden)
Occurs when User A attempts to view or withdraw from User B's wallet:
```json
{
  "error": {
    "code": "PERMISSION_DENIED",
    "message": "You do not have permission to perform this action.",
    "details": {}
  }
}
```

