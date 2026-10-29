CREATE POLICY rls_audit_owner_client_read ON __TENANT_SCHEMA__.audit_logs FOR SELECT USING (((patient_id IS NOT NULL) AND (EXISTS ( SELECT 1
   FROM platform.practices p
  WHERE ((to_regclass(((p.schema_name)::text || '.audit_logs'::text)))::oid = ('__TENANT_SCHEMA__.audit_logs'::regclass)::oid)))));
