--
-- Name: notes; Type: TABLE; Schema: practice_template_capture; Owner: -
--

ALTER TABLE ONLY practice_template_capture.notes FORCE ROW LEVEL SECURITY;

--
-- Name: notes rls_note_access; Type: POLICY; Schema: practice_template_capture; Owner: -
--

CREATE POLICY rls_note_access ON practice_template_capture.notes USING ((((NOT restricted) AND practice_template_capture.has_patient_access(patient_id, (current_setting('app.current_user_id'::text, true))::character varying)) OR (restricted AND ((author_user_id)::text = current_setting('app.current_user_id'::text, true)))));


--
-- Name: POLICY rls_note_access ON notes; Type: COMMENT; Schema: practice_template_capture; Owner: -
--

COMMENT ON POLICY rls_note_access ON practice_template_capture.notes IS 'rls-fingerprint-v1-aaaa';


--
-- Name: audit_logs rls_audit_owner_client_read; Type: POLICY; Schema: practice_template_capture; Owner: -
--

CREATE POLICY rls_audit_owner_client_read ON practice_template_capture.audit_logs FOR SELECT USING (((patient_id IS NOT NULL) AND (EXISTS ( SELECT 1
   FROM platform.practices p
  WHERE ((to_regclass(((p.schema_name)::text || '.audit_logs'::text)))::oid = ('practice_template_capture.audit_logs'::regclass)::oid)))));


COMMENT ON POLICY rls_audit_owner_client_read ON practice_template_capture.audit_logs IS 'rls-fingerprint-v1-bbbb';


CREATE POLICY rls_practice_directory_read ON practice_template_capture.patients FOR SELECT TO pablo_practice_directory USING ((COALESCE(current_setting('app.current_user_id'::text, true), ''::text) <> ''::text));


ALTER TABLE practice_template_capture.notes ENABLE ROW LEVEL SECURITY;
