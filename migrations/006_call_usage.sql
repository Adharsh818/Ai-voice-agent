-- Cost metering per call (docs/REAL_CLINIC_GAPS.md, 11 Oct audit): usage.py's totals and
-- estimated cost, written once at hang-up. Only a new nullable column.
ALTER TABLE calls ADD COLUMN usage_json TEXT;
