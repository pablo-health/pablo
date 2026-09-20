# Video-service response fixtures

Captured from each vendor's own published contract and then scrubbed. Nothing
here was written from memory of an API, which is the point: a fixture that
only ever agreed with its author's model of a format cannot notice the format
changing.

Every value is synthetic. No account, meeting, calendar or person in these
files exists.

| File | Where it came from |
|---|---|
| `zoom_create_meeting_201.json` | Key set and types taken from Zoom's published OpenAPI document, `POST /users/{userId}/meetings` → `201` (`allOf` of an inline `{uuid, id, host_id}` object and the `MeetingInfo` definition), plus the `MeetingSettings` definition for the nested `settings` object. Source: <https://raw.githubusercontent.com/zoom/api/master/openapi.v2.json>, linked from <https://developers.zoom.us/docs/api/>. Identifiers, URLs and the topic are replaced. |
| `google_calendar_event_with_conference.json` | Shape of `conferenceData` and `hangoutLink` on an Events resource, from the Calendar API reference: <https://developers.google.com/workspace/calendar/api/v3/reference/events>. Meeting codes, ids and the signature are replaced. |

Two things the tests read these for, and neither is the values:

* **The key names.** `join_url` is not `joinUrl`, the meeting id is `id` and
  not `meeting_id`, and Google puts the video URL under
  `conferenceData.entryPoints[].uri` with `entryPointType: "video"` — not
  only under `hangoutLink`.
* **The nesting.** Zoom's booking guarantees live under `settings`, which is
  where the waiting-room assertion has to look.

If a vendor changes either, the fixture is what has to be re-captured — not
the parser bent to fit the old shape.
