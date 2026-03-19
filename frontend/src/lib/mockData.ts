// Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

/**
 * Mock data for local development
 * HIPAA Compliance Note: This data is fictional and for development only
 */

import type {
  SessionResponse,
  SessionListResponse,
  StructuredSOAPNoteModel,
} from "@/types/sessions"

export const mockUser = {
  id: "dev-user-001",
  name: "Dr. Sarah Johnson",
  email: "sarah.johnson@therapyassistant.dev",
  image: null,
  role: "therapist",
}

export const mockPatients = [
  {
    id: "patient-001",
    firstName: "Alex",
    lastName: "Chen",
    dateOfBirth: "1985-03-15",
    email: "alex.chen@example.com",
    phone: "(555) 123-4567",
    status: "active",
    lastSession: "2026-01-02",
    nextSession: "2026-01-09",
    totalSessions: 12,
    createdAt: "2025-06-15T10:00:00Z",
    updatedAt: "2026-01-02T14:30:00Z",
  },
  {
    id: "patient-002",
    firstName: "Jamie",
    lastName: "Rivera",
    dateOfBirth: "1992-07-22",
    email: "jamie.rivera@example.com",
    phone: "(555) 234-5678",
    status: "active",
    lastSession: "2025-12-28",
    nextSession: "2026-01-11",
    totalSessions: 8,
    createdAt: "2025-09-01T09:00:00Z",
    updatedAt: "2025-12-28T11:15:00Z",
  },
  {
    id: "patient-003",
    firstName: "Morgan",
    lastName: "Taylor",
    dateOfBirth: "1978-11-30",
    email: "morgan.taylor@example.com",
    phone: "(555) 345-6789",
    status: "active",
    lastSession: "2026-01-03",
    nextSession: null,
    totalSessions: 24,
    createdAt: "2024-03-10T13:00:00Z",
    updatedAt: "2026-01-03T16:45:00Z",
  },
  {
    id: "patient-004",
    firstName: "Casey",
    lastName: "Kim",
    dateOfBirth: "1995-05-18",
    email: "casey.kim@example.com",
    phone: "(555) 456-7890",
    status: "inactive",
    lastSession: "2025-11-15",
    nextSession: null,
    totalSessions: 6,
    createdAt: "2025-08-01T10:30:00Z",
    updatedAt: "2025-11-15T14:00:00Z",
  },
]

export const mockSessions = [
  {
    id: "session-001",
    patientId: "patient-001",
    patientName: "Alex Chen",
    date: "2026-01-02",
    startTime: "14:00",
    endTime: "15:00",
    duration: 60,
    type: "individual",
    status: "completed",
    notes: "Client reported improved mood and coping strategies...",
    createdAt: "2026-01-02T14:00:00Z",
  },
  {
    id: "session-002",
    patientId: "patient-002",
    patientName: "Jamie Rivera",
    date: "2025-12-28",
    startTime: "10:00",
    endTime: "11:00",
    duration: 60,
    type: "individual",
    status: "completed",
    notes: "Discussed progress on anxiety management techniques...",
    createdAt: "2025-12-28T10:00:00Z",
  },
  {
    id: "session-003",
    patientId: "patient-001",
    patientName: "Alex Chen",
    date: "2026-01-09",
    startTime: "14:00",
    endTime: "15:00",
    duration: 60,
    type: "individual",
    status: "scheduled",
    notes: null,
    createdAt: "2026-01-02T14:30:00Z",
  },
  {
    id: "session-004",
    patientId: "patient-003",
    patientName: "Morgan Taylor",
    date: "2026-01-03",
    startTime: "16:00",
    endTime: "17:00",
    duration: 60,
    type: "couples",
    status: "completed",
    notes: "Joint session focused on communication patterns...",
    createdAt: "2026-01-03T16:00:00Z",
  },
]

function s(text: string, ids: number[] = []) {
  return { text, source_segment_ids: ids, confidence_score: 0.0, confidence_level: "", possible_match_segment_ids: [] as number[], signal_used: "" }
}

/**
 * Structured SOAP note for session-001 with a mix of verified (sourced)
 * and unverified (no source_segment_ids) claims for testing source indicators.
 */
const session001Structured: StructuredSOAPNoteModel = {
  subjective: {
    chief_complaint: s("Ongoing anxiety with panic attacks, reporting improvement", [1, 2]),
    mood_affect: s("Improved mood, feeling proud of coping progress", [5, 6]),
    symptoms: [
      s("Panic attacks (reduced from 3-4/week to 1/week)", [2, 3]),
      s("Heart racing and sweaty palms during attacks", [4]),
      s("Work-related anxiety triggers", []),
    ],
    client_narrative: s(
      "Client reports significant reduction in panic attack frequency. Used grounding techniques during a work meeting to manage symptoms. Felt proud of being able to stay and participate rather than leaving.",
      [2, 3, 4, 5, 6],
    ),
  },
  objective: {
    appearance: s("Well-groomed, appropriately dressed", []),
    behavior: s("Cooperative and engaged throughout session", []),
    speech: s("Normal rate, clear articulation", []),
    thought_process: s("Linear and goal-directed", []),
    affect_observed: s("Congruent with reported mood; brightened when discussing progress", [5]),
  },
  assessment: {
    clinical_impression: s(
      "Generalized Anxiety Disorder with Panic Attacks, showing meaningful improvement",
      [1, 2, 3],
    ),
    progress: s(
      "Significant — panic frequency reduced by 75%, client successfully using coping tools in real-world situations",
      [2, 3, 5],
    ),
    risk_assessment: s("No acute safety concerns; no suicidal or homicidal ideation", []),
    functioning_level: s("Moderate-Good; maintaining employment and social engagement", [4, 6]),
  },
  plan: {
    interventions_used: [
      s("CBT cognitive restructuring", []),
      s("Reviewed grounding technique application", [4]),
      s("Reinforced breathing exercises", [2]),
    ],
    homework_assignments: [
      s("Continue daily breathing practice (5 min morning/evening)", []),
      s("Journal panic episodes with trigger/response/outcome", []),
    ],
    next_steps: [
      s("Introduce exposure hierarchy for work meeting anxiety", []),
      s("Explore additional workplace coping strategies", []),
    ],
    next_session: s("One week — February 17, 2026", []),
  },
  narrative: {
    subjective:
      "**Chief Complaint:** Ongoing anxiety with panic attacks, reporting improvement\n\n**Mood/Affect:** Improved mood, feeling proud of coping progress\n\n**Symptoms:**\n- Panic attacks (reduced from 3-4/week to 1/week)\n- Heart racing and sweaty palms during attacks\n- Work-related anxiety triggers\n\n**Client Narrative:** Client reports significant reduction in panic attack frequency. Used grounding techniques during a work meeting to manage symptoms. Felt proud of being able to stay and participate rather than leaving.",
    objective:
      "**Appearance:** Well-groomed, appropriately dressed\n\n**Behavior:** Cooperative and engaged throughout session\n\n**Speech:** Normal rate, clear articulation\n\n**Thought Process:** Linear and goal-directed\n\n**Affect Observed:** Congruent with reported mood; brightened when discussing progress",
    assessment:
      "**Clinical Impression:** Generalized Anxiety Disorder with Panic Attacks, showing meaningful improvement\n\n**Progress:** Significant — panic frequency reduced by 75%, client successfully using coping tools in real-world situations\n\n**Risk Assessment:** No acute safety concerns; no suicidal or homicidal ideation\n\n**Functioning Level:** Moderate-Good; maintaining employment and social engagement",
    plan:
      "**Interventions Used:**\n- CBT cognitive restructuring\n- Reviewed grounding technique application\n- Reinforced breathing exercises\n\n**Homework Assignments:**\n- Continue daily breathing practice (5 min morning/evening)\n- Journal panic episodes with trigger/response/outcome\n\n**Next Steps:**\n- Introduce exposure hierarchy for work meeting anxiety\n- Explore additional workplace coping strategies\n\n**Next Session:** One week — February 17, 2026",
  },
}

/**
 * Mock SessionResponse objects matching the API contract.
 * Includes structured SOAP notes for rendering the full session detail page.
 */
export const mockSessionResponses: SessionResponse[] = [
  {
    id: "session-001",
    user_id: "dev-user-001",
    patient_id: "patient-001",
    patient_name: "Chen, Alex",
    session_date: "2026-02-10T14:00:00Z",
    session_number: 12,
    status: "pending_review",
    transcript: {
      format: "vtt",
      content:
        "WEBVTT\n\n00:00:01.000 --> 00:00:05.000\nTherapist: How have you been feeling since our last session?\n\n00:00:06.000 --> 00:00:15.000\nClient: Overall better. The breathing exercises have been helping with the panic attacks. I only had one this week instead of the usual three or four.\n\n00:00:16.000 --> 00:00:25.000\nTherapist: That's significant progress. Can you tell me more about the one panic attack that did occur?\n\n00:00:26.000 --> 00:00:40.000\nClient: It happened at work during a team meeting. I felt my heart racing and my palms got sweaty. But I used the grounding technique we practiced and it passed within a few minutes instead of lasting half an hour.\n\n00:00:41.000 --> 00:00:50.000\nTherapist: Excellent use of your coping tools. How did you feel afterward?\n\n00:00:51.000 --> 00:01:00.000\nClient: Honestly, I felt proud of myself. Before therapy, I would have left the meeting entirely. This time I stayed and even contributed to the discussion.",
    },
    created_at: "2026-02-10T14:00:00Z",
    soap_note: {
      subjective:
        "**Chief Complaint:** Ongoing anxiety with panic attacks, reporting improvement\n\n**Mood/Affect:** Improved mood, feeling proud of coping progress\n\n**Symptoms:**\n- Panic attacks (reduced from 3-4/week to 1/week)\n- Heart racing and sweaty palms during attacks\n- Work-related anxiety triggers\n\n**Client Narrative:** Client reports significant reduction in panic attack frequency. Used grounding techniques during a work meeting to manage symptoms. Felt proud of being able to stay and participate rather than leaving.",
      objective:
        "**Appearance:** Well-groomed, appropriately dressed\n\n**Behavior:** Cooperative and engaged throughout session\n\n**Speech:** Normal rate, clear articulation\n\n**Thought Process:** Linear and goal-directed\n\n**Affect Observed:** Congruent with reported mood; brightened when discussing progress",
      assessment:
        "**Clinical Impression:** Generalized Anxiety Disorder with Panic Attacks, showing meaningful improvement\n\n**Progress:** Significant — panic frequency reduced by 75%, client successfully using coping tools in real-world situations\n\n**Risk Assessment:** No acute safety concerns; no suicidal or homicidal ideation\n\n**Functioning Level:** Moderate-Good; maintaining employment and social engagement",
      plan:
        "**Interventions Used:**\n- CBT cognitive restructuring\n- Reviewed grounding technique application\n- Reinforced breathing exercises\n\n**Homework Assignments:**\n- Continue daily breathing practice (5 min morning/evening)\n- Journal panic episodes with trigger/response/outcome\n\n**Next Steps:**\n- Introduce exposure hierarchy for work meeting anxiety\n- Explore additional workplace coping strategies\n\n**Next Session:** One week — February 17, 2026",
    },
    soap_note_edited: null,
    soap_note_structured: session001Structured,
    transcript_segments: [
      { index: 0, speaker: "Therapist", text: "How have you been feeling since our last session?", start_time: 1, end_time: 5 },
      { index: 1, speaker: "Client", text: "Overall better. The breathing exercises have been helping with the panic attacks.", start_time: 6, end_time: 11 },
      { index: 2, speaker: "Client", text: "I only had one this week instead of the usual three or four.", start_time: 11, end_time: 15 },
      { index: 3, speaker: "Therapist", text: "That's significant progress. Can you tell me more about the one panic attack that did occur?", start_time: 16, end_time: 25 },
      { index: 4, speaker: "Client", text: "It happened at work during a team meeting. I felt my heart racing and my palms got sweaty. But I used the grounding technique we practiced and it passed within a few minutes.", start_time: 26, end_time: 40 },
      { index: 5, speaker: "Therapist", text: "Excellent use of your coping tools. How did you feel afterward?", start_time: 41, end_time: 50 },
      { index: 6, speaker: "Client", text: "Honestly, I felt proud of myself. Before therapy, I would have left the meeting entirely. This time I stayed and even contributed to the discussion.", start_time: 51, end_time: 60 },
    ],
    quality_rating: null,
    quality_rating_reason: null,
    quality_rating_sections: null,
    processing_started_at: "2026-02-10T14:01:00Z",
    processing_completed_at: "2026-02-10T14:01:12Z",
    finalized_at: null,
    error: null,
    redacted_transcript: null,
    naturalized_transcript: null,
    redacted_soap_note: null,
    naturalized_soap_note: null,
    export_status: "not_queued",
    export_queued_at: null,
    export_reviewed_at: null,
    export_reviewed_by: null,
    exported_at: null,
  },
  {
    id: "session-002",
    user_id: "dev-user-001",
    patient_id: "patient-002",
    patient_name: "Rivera, Jamie",
    session_date: "2026-02-08T10:00:00Z",
    session_number: 8,
    status: "finalized",
    transcript: {
      format: "vtt",
      content:
        "WEBVTT\n\n00:00:01.000 --> 00:00:06.000\nTherapist: Jamie, it's good to see you. How has your week been?\n\n00:00:07.000 --> 00:00:18.000\nClient: It's been a tough one. I had a disagreement with my partner about finances and it brought up a lot of old feelings.\n\n00:00:19.000 --> 00:00:28.000\nTherapist: I'm sorry to hear that. Can you walk me through what happened and what feelings came up for you?",
    },
    created_at: "2026-02-08T10:00:00Z",
    soap_note: {
      subjective:
        "**Chief Complaint:** Relationship conflict triggering past emotional patterns\n\n**Mood/Affect:** Distressed, tearful at times\n\n**Symptoms:**\n- Emotional reactivity to financial disagreements\n- Rumination about past relationship patterns\n- Disrupted sleep (2 nights)\n\n**Client Narrative:** Client reports a significant argument with partner about finances that activated old attachment wounds. Describes feeling unheard and dismissed, connecting these feelings to childhood experiences.",
      objective:
        "**Appearance:** Casually dressed, appeared tired\n\n**Behavior:** Engaged but emotionally activated\n\n**Speech:** Occasionally tremulous when discussing conflict\n\n**Thought Process:** Somewhat tangential when emotionally activated, refocused with prompting\n\n**Affect Observed:** Labile; shifted between sadness and frustration",
      assessment:
        "**Clinical Impression:** Adjustment Disorder with Mixed Anxiety and Depressed Mood; attachment pattern activation\n\n**Progress:** Moderate — client showing improved insight into patterns but still reactive\n\n**Risk Assessment:** No safety concerns\n\n**Functioning Level:** Moderate; relationship stress impacting sleep and daily functioning",
      plan:
        "**Interventions Used:**\n- Emotion-focused therapy techniques\n- Attachment pattern psychoeducation\n- Communication skills practice\n\n**Homework Assignments:**\n- Practice \"I feel\" statements with partner\n- Complete attachment style worksheet\n\n**Next Steps:**\n- Consider couples session if partner willing\n- Continue individual work on emotional regulation\n\n**Next Session:** One week — February 15, 2026",
    },
    soap_note_edited: null,
    soap_note_structured: null,
    transcript_segments: null,
    quality_rating: 4,
    quality_rating_reason: "Accurate capture of session themes",
    quality_rating_sections: [],
    processing_started_at: "2026-02-08T10:01:00Z",
    processing_completed_at: "2026-02-08T10:01:15Z",
    finalized_at: "2026-02-08T10:30:00Z",
    error: null,
    redacted_transcript: null,
    naturalized_transcript: null,
    redacted_soap_note: null,
    naturalized_soap_note: null,
    export_status: "not_queued",
    export_queued_at: null,
    export_reviewed_at: null,
    export_reviewed_by: null,
    exported_at: null,
  },
  {
    id: "session-003",
    user_id: "dev-user-001",
    patient_id: "patient-003",
    patient_name: "Taylor, Morgan",
    session_date: "2026-02-07T16:00:00Z",
    session_number: 24,
    status: "finalized",
    transcript: {
      format: "txt",
      content: "Therapist: Morgan, welcome back. How are things going with the new medication?\n\nClient: The side effects have mostly settled down. I'm sleeping better and my mood feels more stable overall.",
    },
    created_at: "2026-02-07T16:00:00Z",
    soap_note: {
      subjective: "Client reports medication side effects have subsided. Sleep quality improved. Mood feels more stable.",
      objective: "Patient appeared well-rested and calm. Good eye contact. Speech normal rate and volume. Thought process organized.",
      assessment: "Major Depressive Disorder, recurrent, in partial remission. Medication adjustment showing positive response.",
      plan: "Continue current medication regimen. Follow up with psychiatrist in 2 weeks. Resume behavioral activation goals.",
    },
    soap_note_edited: null,
    soap_note_structured: null,
    transcript_segments: null,
    quality_rating: 5,
    quality_rating_reason: null,
    quality_rating_sections: null,
    processing_started_at: "2026-02-07T16:01:00Z",
    processing_completed_at: "2026-02-07T16:01:08Z",
    finalized_at: "2026-02-07T16:20:00Z",
    error: null,
    redacted_transcript: null,
    naturalized_transcript: null,
    redacted_soap_note: null,
    naturalized_soap_note: null,
    export_status: "not_queued",
    export_queued_at: null,
    export_reviewed_at: null,
    export_reviewed_by: null,
    exported_at: null,
  },
  {
    id: "session-004",
    user_id: "dev-user-001",
    patient_id: "patient-001",
    patient_name: "Chen, Alex",
    session_date: "2026-02-03T14:00:00Z",
    session_number: 11,
    status: "finalized",
    transcript: {
      format: "vtt",
      content: "WEBVTT\n\n00:00:01.000 --> 00:00:08.000\nTherapist: Alex, how have things been since we last spoke?\n\n00:00:09.000 --> 00:00:20.000\nClient: I had three panic attacks this week. Two at work and one at the grocery store.",
    },
    created_at: "2026-02-03T14:00:00Z",
    soap_note: {
      subjective:
        "**Chief Complaint:** Increased panic attack frequency\n\n**Mood/Affect:** Anxious and frustrated\n\n**Symptoms:**\n- 3 panic attacks this week\n- 2 at work, 1 at grocery store\n- Avoidance behaviors emerging\n\n**Client Narrative:** Client reports increased panic frequency. Feeling discouraged about lack of progress.",
      objective:
        "**Appearance:** Well-groomed\n\n**Behavior:** Fidgety, difficulty maintaining stillness\n\n**Speech:** Slightly rapid\n\n**Thought Process:** Linear but preoccupied with worry\n\n**Affect Observed:** Anxious, congruent with report",
      assessment:
        "**Clinical Impression:** GAD with Panic Attacks — temporary setback likely related to work stressor\n\n**Progress:** Slight regression; introduced new coping strategies\n\n**Risk Assessment:** No safety concerns\n\n**Functioning Level:** Moderate",
      plan:
        "**Interventions Used:**\n- Breathing exercises (4-7-8 technique)\n- Grounding technique introduction\n\n**Homework Assignments:**\n- Practice grounding daily\n- Track panic triggers in journal\n\n**Next Steps:**\n- Review trigger patterns next session\n\n**Next Session:** One week",
    },
    soap_note_edited: null,
    soap_note_structured: null,
    transcript_segments: null,
    quality_rating: 4,
    quality_rating_reason: null,
    quality_rating_sections: null,
    processing_started_at: "2026-02-03T14:01:00Z",
    processing_completed_at: "2026-02-03T14:01:10Z",
    finalized_at: "2026-02-03T14:25:00Z",
    error: null,
    redacted_transcript: null,
    naturalized_transcript: null,
    redacted_soap_note: null,
    naturalized_soap_note: null,
    export_status: "not_queued",
    export_queued_at: null,
    export_reviewed_at: null,
    export_reviewed_by: null,
    exported_at: null,
  },
]

export const mockSessionListResponse: SessionListResponse = {
  data: mockSessionResponses,
  total: mockSessionResponses.length,
  page: 1,
  page_size: 20,
}

export const mockDashboardStats = {
  totalPatients: 12,
  activePatients: 9,
  sessionsThisWeek: 8,
  sessionsThisMonth: 28,
  upcomingSessions: 5,
  recentActivity: [
    {
      id: "1",
      type: "session",
      description: "Session completed with Alex Chen",
      timestamp: "2026-01-02T14:00:00Z",
    },
    {
      id: "2",
      type: "patient",
      description: "New patient enrolled: Jordan Smith",
      timestamp: "2026-01-01T09:30:00Z",
    },
    {
      id: "3",
      type: "session",
      description: "Session scheduled with Jamie Rivera",
      timestamp: "2025-12-30T11:00:00Z",
    },
  ],
}
