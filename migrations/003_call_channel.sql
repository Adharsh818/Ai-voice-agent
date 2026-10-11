-- Browser or phone, per call (dashboard Calls tab, 11 Oct). Only an added column.
ALTER TABLE calls ADD COLUMN channel TEXT;

-- Calls from before: only a phone call has a caller ID, so only a phone call hears Emma ask
-- "Is the number you're calling from the best one?", or has a number before Emma asked for one.
UPDATE calls SET channel = 'phone'
WHERE channel IS NULL AND (
    id IN (SELECT call_id FROM call_turns WHERE role = 'emma' AND text LIKE '%number you''re calling from%')
    OR (caller_phone_e164 IS NOT NULL AND id NOT IN (
        SELECT call_id FROM call_turns WHERE role = 'emma'
        AND (text LIKE '%your number%' OR text LIKE '%phone number%' OR text LIKE '%best number%'
             OR text LIKE '%number to reach%'))));
