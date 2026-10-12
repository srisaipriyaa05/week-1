# Acme Appliances Order Ledger Agent

Deploy the contents of this folder at the root of a Vercel project.
Public endpoint: `POST https://YOUR-DOMAIN/` with `{"question":"..."}`.
Returns `{"answer": ...}`.

The server fetches the orders, rates, and export resources from the provided challenge API and caches data for 60 seconds. Business dates are interpreted in Asia/Kolkata. Duplicate order IDs are resolved using the latest `updated_at`, and revenue includes only `paid` orders.
