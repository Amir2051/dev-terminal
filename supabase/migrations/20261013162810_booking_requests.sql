-- Reconstructed from the live development database schema and constraints.
-- Original migration source was absent from repository history.
CREATE TABLE IF NOT EXISTS public.booking_requests (
    id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    customer_name text NOT NULL CHECK (length(trim(customer_name)) BETWEEN 1 AND 160),
    customer_email text NOT NULL CHECK (length(trim(customer_email)) BETWEEN 3 AND 320),
    customer_phone text NOT NULL CHECK (length(trim(customer_phone)) BETWEEN 7 AND 40),
    zip_code text NOT NULL CHECK (zip_code ~ '^[0-9]{5}$'),
    status text NOT NULL DEFAULT 'pending_review'
        CHECK (status IN ('pending_review', 'confirmed', 'quote_dispatched', 'contacted')),
    payload jsonb NOT NULL,
    created_at timestamptz NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS booking_requests_created_at_idx
    ON public.booking_requests (created_at DESC);

ALTER TABLE public.booking_requests ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "admins can read booking requests" ON public.booking_requests;
CREATE POLICY "admins can read booking requests"
    ON public.booking_requests FOR SELECT TO authenticated
    USING (((auth.jwt() -> 'app_metadata') ->> 'role') = 'admin');

DROP POLICY IF EXISTS "admins can update booking requests" ON public.booking_requests;
CREATE POLICY "admins can update booking requests"
    ON public.booking_requests FOR UPDATE TO authenticated
    USING (((auth.jwt() -> 'app_metadata') ->> 'role') = 'admin')
    WITH CHECK (((auth.jwt() -> 'app_metadata') ->> 'role') = 'admin');

DROP POLICY IF EXISTS "public can submit booking requests" ON public.booking_requests;
CREATE POLICY "public can submit booking requests"
    ON public.booking_requests FOR INSERT TO anon, authenticated
    WITH CHECK (
        status = 'pending_review'
        AND jsonb_typeof(payload) = 'object'
        AND payload ->> 'customerName' = customer_name
        AND payload ->> 'customerEmail' = customer_email
        AND payload ->> 'customerPhone' = customer_phone
        AND payload ->> 'zipCode' = zip_code
    );

REVOKE ALL ON TABLE public.booking_requests FROM PUBLIC;
GRANT ALL ON TABLE public.booking_requests TO service_role;
GRANT INSERT ON TABLE public.booking_requests TO anon;
GRANT SELECT, INSERT, UPDATE ON TABLE public.booking_requests TO authenticated;
