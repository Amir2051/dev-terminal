-- Cache JWT evaluation per statement for booking administrator policies.
DROP POLICY IF EXISTS "admins can read booking requests" ON public.booking_requests;
CREATE POLICY "admins can read booking requests"
    ON public.booking_requests FOR SELECT TO authenticated
    USING ((((SELECT auth.jwt()) -> 'app_metadata') ->> 'role') = 'admin');

DROP POLICY IF EXISTS "admins can update booking requests" ON public.booking_requests;
CREATE POLICY "admins can update booking requests"
    ON public.booking_requests FOR UPDATE TO authenticated
    USING ((((SELECT auth.jwt()) -> 'app_metadata') ->> 'role') = 'admin')
    WITH CHECK ((((SELECT auth.jwt()) -> 'app_metadata') ->> 'role') = 'admin');
