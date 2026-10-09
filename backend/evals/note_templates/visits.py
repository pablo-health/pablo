# Copyright (c) 2026 Pablo Health, LLC. Licensed under AGPL-3.0.

"""Synthetic visits written for the eval, about no one.

Each is in the templates' own sample format: one line per turn, a timestamp,
the speaker (Therapist is the clinician, Client the client), and whatever the
clinician dictates after the client's last line. Generation numbers the lines
as transcript segments, exactly as it numbers a recorded session's.

Each visit exists to put one chart rule under pressure, named above it.
"""

from __future__ import annotations

# A chart with every field filled, and a visit that changes none of it.
# Alcohol, cannabis and nicotine are asked by name; nothing else is.
FULL_CHART = """\
[00:00:04] Therapist: Hi Avery, come on in. How have the last two months been?
[00:00:09] Client: Steady, honestly. Mood has been good, and I'm sleeping through most nights.
[00:00:16] Therapist: Are you taking the sertraline every morning?
[00:00:19] Client: Every morning. And the trazodone maybe twice a week, when I can't settle.
[00:00:26] Therapist: Any nausea, headaches, feeling groggy in the morning after the trazodone?
[00:00:31] Client: A little groggy the morning after, but it wears off by nine.
[00:00:37] Therapist: How's the worry?
[00:00:40] Client: It's there, but I can talk myself down. Nothing like last year.
[00:00:46] Therapist: Alcohol, same as before?
[00:00:49] Client: Same as always. A glass of wine with dinner a couple of times a week.
[00:00:55] Therapist: Any cannabis?
[00:00:57] Client: No.
[00:00:59] Therapist: Do you smoke or vape?
[00:01:01] Client: No, never have.
[00:01:04] Therapist: Any thoughts of hurting yourself or anyone else?
[00:01:07] Client: No.
[00:01:09] Therapist: Good. Then we keep everything the same, and I'll see you in eight weeks.
[00:01:14] Client: Sounds good.
[00:01:31] Therapist: Note for the record. Client alert and oriented times four, well groomed, \
good eye contact. Speech normal. Mood "steady", affect full and congruent. Thought process \
linear. No hallucinations or delusions. Denies SI and HI. Insight and judgment good. Major \
depression, recurrent, stable. Generalized anxiety stable. Continue sertraline 100 milligrams \
every morning and trazodone 50 at bedtime as needed. Return in eight weeks.
"""

# The client was laid off since the chart's work history was written, and
# reports a medication another doctor started. The clinician starts one.
STATED_CHANGE = """\
[00:00:05] Therapist: Hi Morgan. Are you at home today?
[00:00:08] Client: Yes, at home.
[00:00:10] Therapist: How have things been since last month?
[00:00:14] Client: Not great. I got laid off from the bank three weeks ago. They closed my branch.
[00:00:22] Therapist: I'm sorry. How has your mood been since then?
[00:00:26] Client: Low. I'm sleeping until noon some days and I don't want to do anything.
[00:00:33] Therapist: Are you still taking the escitalopram every morning?
[00:00:36] Client: Yes, every day. Oh, and my primary care doctor put me on omeprazole, \
20 milligrams every morning, for heartburn.
[00:00:45] Therapist: Thanks, good to know. Any side effects from the escitalopram?
[00:00:49] Client: No.
[00:00:51] Therapist: Any alcohol?
[00:00:53] Client: A bit more than usual. Maybe three beers on a weekend night.
[00:00:59] Therapist: Any thoughts of hurting yourself or anyone else?
[00:01:02] Client: No. I'm frustrated, but no.
[00:01:06] Therapist: Okay. I'd like to add bupropion XL, 150 milligrams in the morning, \
for energy and motivation. It can cause trouble sleeping or some jitteriness at first, and \
rarely seizures, so no binge drinking on it. The alternative is staying where we are. What \
do you think?
[00:01:24] Client: Let's try it.
[00:01:26] Therapist: Okay. Keep the escitalopram the same. Call the office if anything \
gets worse, and 988 if it's urgent. I'll see you in four weeks.
[00:01:34] Client: Thanks.
[00:01:52] Therapist: Note for the record. Client alert and oriented times four, casually \
dressed, fair eye contact. Speech soft, normal rate. Mood "low", affect constricted. Thought \
process linear. No hallucinations or delusions. Denies SI and HI. Insight and judgment good. \
Major depression worsening after job loss. Continue escitalopram 10 milligrams every morning. \
Start bupropion XL 150 milligrams every morning; risks, benefits and the alternative of no \
change discussed, client agreed. Return in four weeks. Billing 99214.
"""

# The chart says no known drug allergies; the client now reports one.
ALLERGY_STATED = """\
[00:00:04] Therapist: Hi Quinn. How have you been?
[00:00:07] Client: Pretty even. No highs, no big lows.
[00:00:11] Therapist: Taking the lamotrigine twice a day?
[00:00:14] Client: Yes. Oh, I should tell you, I found out I'm allergic to amoxicillin. \
Urgent care gave it to me last month and I broke out in a rash all over.
[00:00:25] Therapist: Thank you for telling me. Any rash from the lamotrigine itself, or \
anything new on your skin since then?
[00:00:31] Client: No, it cleared up when I stopped the amoxicillin.
[00:00:35] Therapist: Good. Any alcohol or cannabis?
[00:00:38] Client: Neither.
[00:00:40] Therapist: Any thoughts of hurting yourself or anyone else?
[00:00:43] Client: No.
[00:00:45] Therapist: Then we'll keep the lamotrigine the same. See you in six weeks.
[00:00:50] Client: Okay, thanks.
[00:01:06] Therapist: Note for the record. Client alert and oriented, well groomed. Speech \
normal. Mood "even", affect euthymic. Thought process linear. Denies SI and HI. Insight and \
judgment good. Bipolar II, stable. Continue lamotrigine 100 milligrams twice daily. Return \
in six weeks.
"""

# The chart records a penicillin allergy; the client says it was never theirs.
ALLERGY_DISPUTED = """\
[00:00:05] Therapist: Hi Drew. You're at your apartment today?
[00:00:08] Client: Yep.
[00:00:10] Therapist: How's the focus been on the atomoxetine?
[00:00:14] Client: Better. I'm finishing my problem sets on time. One thing, though. That \
penicillin allergy on my record? My mom says that was actually my brother. I've never \
reacted to anything.
[00:00:27] Therapist: Okay. Let's leave it on your chart until your primary care doctor \
can confirm it, just to be safe.
[00:00:33] Client: That's fine.
[00:00:35] Therapist: Any stomach upset or trouble sleeping on the atomoxetine?
[00:00:39] Client: Some stomach upset if I take it without breakfast.
[00:00:44] Therapist: Take it with food, then. Any alcohol?
[00:00:47] Client: Not really. A beer at a party once a month, maybe.
[00:00:52] Therapist: Any thoughts of hurting yourself or anyone else?
[00:00:55] Client: No.
[00:00:57] Therapist: Good. Same dose, and I'll see you in two months.
[00:01:01] Client: Thanks.
[00:01:18] Therapist: Note for the record. Client alert and oriented, casually dressed. \
Speech normal. Mood "good", affect bright. Thought process linear. Denies SI and HI. Insight \
and judgment good. ADHD improving. Continue atomoxetine 40 milligrams every morning, with \
food. Return in two months.
"""

# A client whose chart has nothing on it yet. The visit talks only about
# sleep: no medication, allergy or history comes up, and no diagnosis is named.
EMPTY_CHART = """\
[00:00:04] Therapist: Hi Taylor. Last time we agreed to try the sleep changes before any \
medication. How did that go?
[00:00:11] Client: Better than I expected. I've been off my phone by ten and getting up at \
the same time every day.
[00:00:19] Therapist: How long to fall asleep now?
[00:00:22] Client: Maybe half an hour. It used to be two hours.
[00:00:26] Therapist: And waking in the night?
[00:00:29] Client: Once, but I get back to sleep.
[00:00:32] Therapist: Any alcohol?
[00:00:34] Client: A beer or two on weekends.
[00:00:37] Therapist: Cannabis, to help you sleep?
[00:00:40] Client: No.
[00:00:42] Therapist: Any thoughts of hurting yourself or anyone else?
[00:00:45] Client: No.
[00:00:47] Therapist: Great. Let's keep going without medication and check in in four weeks.
[00:00:52] Client: Sounds good.
[00:01:08] Therapist: Note for the record. Client alert and oriented, well groomed. Speech \
normal. Mood "better", affect full. Thought process linear. Denies SI and HI. Insight and \
judgment good. Sleep onset improved with the sleep schedule. No medication started. Return \
in four weeks.
"""

# A first visit for a client with nothing on the chart. They list what they
# take and deny allergies. The clinician states two diagnoses with codes.
INTAKE = """\
[00:00:04] Therapist: Hi Jamie, thanks for coming in. Can you confirm where you are right now?
[00:00:09] Client: At home, in my bedroom.
[00:00:12] Therapist: What brings you in?
[00:00:15] Client: Panic attacks. I've had four this month, two of them at work. I thought \
I was having a heart attack the first time.
[00:00:25] Therapist: When did they start?
[00:00:28] Client: The first one was in 2019, but they went away. They came back in August.
[00:00:35] Therapist: Has anyone diagnosed you before?
[00:00:38] Client: My primary care doctor said panic disorder, back in 2019.
[00:00:44] Therapist: Any therapy?
[00:00:46] Client: I did CBT for about six months in 2020. It helped a lot.
[00:00:52] Therapist: Any medications for it before?
[00:00:55] Client: Sertraline, 25 milligrams. It made me so nauseous I stopped after two weeks.
[00:01:02] Therapist: Any hospital stays or programs for mental health?
[00:01:05] Client: No, never.
[00:01:07] Therapist: Have you ever tried to hurt yourself or end your life, or hurt someone else?
[00:01:12] Client: No, never.
[00:01:14] Therapist: Any legal trouble or custody issues?
[00:01:17] Client: No.
[00:01:19] Therapist: Anything you've been through that you'd call traumatic?
[00:01:23] Client: A car accident when I was seventeen. I was okay, but I don't like highways.
[00:01:30] Therapist: Who do you live with?
[00:01:32] Client: A roommate, in an apartment.
[00:01:35] Therapist: Relationships, family?
[00:01:38] Client: Single right now. I'm close with my dad.
[00:01:42] Therapist: And work?
[00:01:44] Client: I'm a nurse, on night shifts at the hospital.
[00:01:48] Therapist: Who do you lean on?
[00:01:50] Client: My dad and my roommate.
[00:01:53] Therapist: Anything about your culture, faith or background you'd like me to know?
[00:01:58] Client: I'm Catholic, and church on Sundays matters to me.
[00:02:03] Therapist: Any medical problems?
[00:02:05] Client: Hypothyroidism.
[00:02:07] Therapist: What medications do you take now?
[00:02:10] Client: Levothyroxine, 75 micrograms every morning. And omeprazole, 20 \
milligrams before breakfast.
[00:02:18] Therapist: Any allergies to medications?
[00:02:21] Client: No, none that I know of.
[00:02:24] Therapist: Anyone in your family with anxiety, depression, or other mental health \
problems?
[00:02:29] Client: My aunt has panic attacks too.
[00:02:32] Therapist: Medical problems in the family?
[00:02:34] Client: My dad has heart disease.
[00:02:37] Therapist: How much alcohol do you drink?
[00:02:40] Client: Hardly any. A glass of wine at a holiday.
[00:02:44] Therapist: Cannabis?
[00:02:46] Client: No.
[00:02:48] Therapist: Do you smoke or vape?
[00:02:50] Client: No.
[00:02:52] Therapist: Any cocaine, opioids, or anything like Xanax that wasn't prescribed to you?
[00:02:57] Client: No, none of that.
[00:03:00] Therapist: Any thoughts of hurting yourself or anyone else right now?
[00:03:04] Client: No.
[00:03:06] Therapist: You did the GAD-7 before the visit and scored 16.
[00:03:10] Client: That sounds right.
[00:03:12] Therapist: I'd like to start escitalopram, 5 milligrams daily for a week, then 10. \
Nausea is possible early on, usually milder than with sertraline. Some people feel more \
anxious the first week, and rarely mood gets worse or thoughts of self-harm appear, so call \
me if that happens. The alternative is going back to CBT on its own. What do you think?
[00:03:31] Client: I'll try it, and I'd like to get back into therapy too.
[00:03:35] Therapist: Good. I'll send you some therapist names. I'll see you in four weeks, \
and call the office or 988 if anything gets worse.
[00:03:42] Client: Thank you.
[00:04:01] Therapist: Note for the record. Visit from 2:00 to 2:55 by video. Client well \
groomed, good eye contact, mildly restless. Alert and oriented times four. Speech normal \
rate, slightly fast. Mood "on edge", affect anxious. Thought process linear. No \
hallucinations or delusions. Denies SI and HI, no past attempts. Cognition intact. Insight \
and judgment good. Protective factors: employed, close to father, engaged in treatment. \
Overall acute risk is low. Diagnoses: panic disorder, F41.0, recurrent panic attacks since \
August with fear of dying and avoidance at work. Generalized anxiety disorder, F41.1, GAD-7 \
16. Start escitalopram 5 milligrams daily for seven days, then 10 milligrams daily; side \
effects, the activation and suicidality warning, and the alternative of CBT alone \
discussed; client agreed. Referral to a CBT therapist. Return in four weeks. Billing 90792.
"""

# The client reports passive suicidal thoughts; the clinician states the risk
# level and the safety plan only in the dictated addendum.
RISK_LANGUAGE = """\
[00:00:05] Therapist: Hi Sky. Are you at home today?
[00:00:08] Client: Yeah.
[00:00:10] Therapist: How have the last two weeks been?
[00:00:13] Client: Worse. The dark mornings are getting to me. I'm dragging myself through work.
[00:00:20] Therapist: Taking the sertraline every morning?
[00:00:23] Client: Every morning.
[00:00:25] Therapist: Any thoughts of hurting yourself or that you'd be better off dead?
[00:00:30] Client: Some nights I think everyone would be better off without me.
[00:00:35] Therapist: Thank you for telling me. Have you thought about how you might do it, \
or about acting on it?
[00:00:40] Client: No. I wouldn't do anything. I don't have a plan.
[00:00:44] Therapist: Have you done anything to hurt yourself, like cutting?
[00:00:47] Client: No, I haven't done anything like that.
[00:00:50] Therapist: Any thoughts of hurting anyone else?
[00:00:53] Client: No.
[00:00:55] Therapist: Are there any guns in the house?
[00:00:57] Client: No.
[00:00:59] Therapist: What tends to come right before those nights?
[00:01:03] Client: Lying awake going over everything I did wrong that day.
[00:01:07] Therapist: When that starts, what could you do instead?
[00:01:10] Client: Take the dog out. Call my sister, she's always up late.
[00:01:15] Therapist: Good. And if it gets stronger, or you start thinking about acting on \
it, you call or text 988, or call the office. Can you do that?
[00:01:22] Client: Yes.
[00:01:24] Therapist: I'd like to go up on the sertraline from 150 to 200 milligrams. \
Some stomach upset is possible. The alternative is keeping the dose and adding a light box. \
What do you think?
[00:01:35] Client: Let's go up.
[00:01:37] Therapist: Okay. I'll see you in two weeks.
[00:01:40] Client: Okay.
[00:01:58] Therapist: Note for the record. Client alert and oriented times four, \
casually dressed, poor eye contact. Speech slow, soft. Mood "worse", affect constricted. \
Thought process linear. No hallucinations or delusions. Passive suicidal ideation, no \
intent, no plan, no self-harm behaviors, no firearms in the home. Denies HI. Risk factors: \
recurrent depression, passive suicidal ideation, seasonal worsening. Protective factors: \
sister, dog, engaged in treatment. Overall acute risk is moderate. Safety plan completed: \
warning sign is late-night rumination, coping is walking the dog and calling their sister, \
crisis contacts 988 and the office. Major depression, recurrent, worsening. Increase \
sertraline to 200 milligrams every morning; side effects and the alternative of a light box \
discussed, client agreed. Return in two weeks.
"""


# Therapy, a medication check in the middle of it, more therapy, then the
# risk screen near the end, where it fits the visit. The therapy runs from
# 0:14 to 7:40 and from 10:05 to 24:50: 1331 seconds, 22 minutes. The
# clinician dictates the codes but no minutes.
INTERLEAVED_MEDICATION_CHECK = """\
[00:00:04] Therapist: Hi Riley, good to see you. Are you at home today?
[00:00:09] Client: Yes, at home, in my kitchen.
[00:00:14] Therapist: Last time you were going to try the activity schedule. How did that go?
[00:00:22] Client: I did it four days out of seven. I walked twice and called my sister once.
[00:01:30] Therapist: What did you notice about your mood on the days you walked?
[00:01:41] Client: It was better in the afternoon. I didn't expect that.
[00:03:10] Therapist: And on the days you skipped it?
[00:03:18] Client: I told myself it wouldn't help, so why bother.
[00:05:02] Therapist: That's the thought we've been tracking. How much did you believe it \
right then?
[00:05:12] Client: Maybe eighty percent.
[00:07:40] Therapist: Let me pause there and check on the medication. Are you taking the \
sertraline 100 every morning?
[00:07:49] Client: Every morning. I missed one day last week.
[00:08:05] Therapist: Any nausea, headaches, trouble sleeping?
[00:08:11] Client: Some trouble falling asleep, but that's been better.
[00:09:30] Therapist: Okay. We'll keep the sertraline at 100 milligrams. I'll send the refill today.
[00:09:42] Client: Thank you, that works for me.
[00:10:05] Therapist: Back to that thought, "it won't help." What would you say to a friend \
who told you that?
[00:10:20] Client: I'd tell them to try it anyway and see.
[00:13:45] Therapist: So what's a more balanced version you could tell yourself?
[00:13:58] Client: Maybe it helps a little, and a little is still something.
[00:17:30] Therapist: How much do you believe the original thought now?
[00:17:38] Client: Maybe forty percent now.
[00:20:15] Therapist: Let's plan the coming week. Which days could you schedule the walk?
[00:20:25] Client: Monday, Wednesday and Saturday, before lunch.
[00:24:50] Therapist: Before we finish, I ask this every time. Any thoughts of hurting \
yourself, or that you'd be better off dead?
[00:25:00] Client: No. Nothing like that.
[00:25:10] Therapist: And the PHQ-9 you filled in this morning was 11, down from 16.
[00:25:20] Client: That sounds about right to me.
[00:27:00] Therapist: Good. I'll see you in four weeks. Call the office if anything changes.
[00:27:10] Client: Okay, thanks. See you then.
[00:27:30] Therapist: Note for the record. Client alert and oriented, casually dressed, \
good eye contact. Speech normal. Mood "better", affect brighter than last visit. Thought \
process linear. No hallucinations or delusions. Denies SI and HI. Insight and judgment \
fair. Major depression, recurrent, improving. Continue sertraline 100 milligrams every \
morning, refill sent. Return in four weeks. Billing 99214 plus 90833.
"""

# Therapy, a screen, therapy, a medication check, therapy: four portions
# besides the greeting. The therapy runs 0:18-7:55, 9:40-18:40 and
# 21:10-29:30: 1497 seconds, 24 minutes. The clinician dictates thirty
# minutes, which the note carries.
INTERLEAVED_DICTATED_MINUTES = """\
[00:00:05] Therapist: Hello Sam. You're at home and okay to talk by video?
[00:00:12] Client: Yes, at home, and that's fine.
[00:00:18] Therapist: You said on the phone the panic came back at work. Tell me about the last one.
[00:00:30] Client: Tuesday, in a meeting. My chest got tight and I was sure everyone could see.
[00:02:40] Therapist: What did you do when it started?
[00:02:48] Client: I left the room and sat in my car for twenty minutes.
[00:05:15] Therapist: Leaving brought the fear down fast, and that teaches your body the \
meeting was dangerous.
[00:05:30] Client: So staying would have been better?
[00:07:55] Therapist: Let me ask the screening questions now. The GAD-7 you did today was 15. \
Does that fit?
[00:08:05] Client: Yes, the last two weeks were bad.
[00:08:20] Therapist: Any thoughts of hurting yourself, or that you'd be better off dead?
[00:08:28] Client: No, never. I just want it to stop.
[00:09:40] Therapist: Thanks. Back to the meeting. If you had stayed, what did you expect \
would happen?
[00:09:52] Client: That I'd pass out or have to run out in front of everyone.
[00:12:30] Therapist: Has that ever happened in a panic attack?
[00:12:36] Client: No. Not once, actually.
[00:15:10] Therapist: Let's try something now. Breathe through a straw for one minute and \
notice the tightness without leaving.
[00:15:22] Client: Okay. It's uncomfortable, but I can stay with it.
[00:18:40] Therapist: Before we keep going, the propranolol. Are you using it before meetings?
[00:18:48] Client: I took it twice. It helped my hands stop shaking.
[00:19:30] Therapist: Any dizziness or feeling faint with it?
[00:19:36] Client: No, nothing like that.
[00:20:20] Therapist: Then keep the propranolol 10 milligrams as needed before meetings, and \
continue the escitalopram 10 every morning.
[00:20:35] Client: Got it, both the same.
[00:21:10] Therapist: For practice this week, stay in one meeting through the tightness, and \
write down what you predicted and what happened.
[00:21:25] Client: I can do that on Thursday, there's a team meeting.
[00:26:40] Therapist: Good. How confident are you, zero to ten?
[00:26:46] Client: About a six. That's better than I'd have said an hour ago.
[00:29:30] Therapist: I'll see you in three weeks. Call the office if the panic gets worse.
[00:29:38] Client: Thanks, I will call if I need to.
[00:30:05] Therapist: Note for the record. Client alert and oriented, well groomed. Speech \
normal rate. Mood "anxious", affect anxious and congruent. Thought process linear. No \
hallucinations or delusions. Denies SI and HI. Insight and judgment good. Panic disorder, \
worsening at work. Continue escitalopram 10 milligrams every morning and propranolol 10 \
milligrams as needed before meetings. Return in three weeks. Psychotherapy thirty minutes. \
Billing 99214 plus 90833.
"""

# The follow-up's therapy sample as a clinician in a state with its own name
# for the monitoring program would dictate it, with the client at home. The
# clinician dictates the mental status, the risk finding and the protective
# factors, which the note writes as the clinician's findings; the client's
# answer about thoughts of harm is the only thing in the risk fields to quote.
# "Nicotine, cannabis, anything else?" gets one answer for all three.
WITH_THERAPY_AT_HOME = """\
[00:00:05] Therapist: Hi Jordan, good to see you. Before we start, are you at home today?
[00:00:09] Client: Yeah, I'm at home.
[00:00:12] Therapist: Great. So how have things been since we last met?
[00:00:18] Client: Honestly, better on the focus side. The Adderall is working. I'm getting \
through my reports at work. But the worry has been bad the last couple of weeks. Like, lying \
in bed running through everything that could go wrong.
[00:00:41] Therapist: Okay. Let's start with the medication and then spend most of our time on \
the worry. You're on the Adderall XR 20 milligrams in the morning and the sertraline 50, \
right?
[00:00:50] Client: Yes, both in the morning.
[00:00:53] Therapist: Any missed doses?
[00:00:55] Client: Maybe one sertraline last week, I forgot it on the weekend.
[00:01:01] Therapist: Okay. Any trouble falling asleep, appetite changes, heart racing, \
headaches?
[00:01:07] Client: Appetite's a little lower at lunch but I eat a big dinner. Sleep is mostly \
the worrying, not the medication I think. No heart stuff.
[00:01:19] Therapist: Any alcohol?
[00:01:21] Client: A glass of wine on weekends, maybe two.
[00:01:25] Therapist: Nicotine, cannabis, anything else?
[00:01:27] Client: No, none of that.
[00:01:30] Therapist: And I always ask: any thoughts of hurting yourself or anyone else, or \
that you'd be better off dead?
[00:01:36] Client: No. Nothing like that.
[00:01:39] Therapist: Okay. You did the GAD-7 on Friday and it came in at 13, up from 8 last \
time. That fits what you're describing.
[00:01:48] Client: Yeah, that sounds right.
[00:01:52] Therapist: Mood otherwise? Any periods where you're not sleeping and don't need it, \
racing, spending a lot?
[00:01:58] Client: No, nothing like that. Mood is okay, just the worry.
[00:02:04] Therapist: So here's what I'm thinking. The Adderall is doing its job, so we'll \
keep that the same. For the anxiety, I'd like to go up on the sertraline from 50 to 75 \
milligrams. Same timing. You might get some stomach upset or a bit of jitteriness the first \
week, and it can take a few weeks to see the change. The other option is staying put and \
leaning on therapy alone for a month. What do you think?
[00:02:31] Client: I'd rather try the increase. The worry's getting in the way.
[00:02:35] Therapist: Okay, we'll do 75. If you have anything like worsening mood or new \
thoughts of self-harm, call the office, and if it's urgent, 988 or 911. I'll see you back in \
four weeks.
[00:02:47] Client: Sounds good.
[00:02:50] Therapist: Alright. Let's spend the rest of our time on the worry. Tell me about \
last night.
[00:02:56] Client: I got into bed at eleven and just started going over the quarterly review. \
What if my numbers are wrong, what if my manager thinks I'm not cut out for this. I looked at \
the clock and it was one thirty.
[00:03:12] Therapist: When you notice the thought "my manager thinks I'm not cut out for \
this," how strongly do you believe it right then, zero to a hundred?
[00:03:20] Client: In the moment, like ninety.
[00:03:23] Therapist: And what's the evidence for it?
[00:03:27] Client: She asked me to redo a slide last month.
[00:03:31] Therapist: And evidence against?
[00:03:35] Client: She gave me the new account. She said my last report was clear.
[00:03:41] Therapist: So if you put those side by side, what would be a more balanced thought?
[00:03:47] Client: Maybe... she asks everybody to redo things, and she trusts me with the \
bigger account.
[00:03:55] Therapist: How much do you believe the original thought now?
[00:03:58] Client: Maybe forty.
[00:04:02] Therapist: That's a real shift. Let's also set up a worry window: fifteen minutes \
at six in the evening where you write down every worry, and when one shows up at night you \
tell yourself it goes in tomorrow's window. Want to try it?
[00:04:15] Client: I can try that. I did the time-blocking thing you suggested before and it \
worked most days.
[00:04:21] Therapist: How many days did you use the time blocks?
[00:04:24] Client: Four out of five workdays, most weeks.
[00:04:28] Therapist: That's great follow-through. Let's add the worry window and the thought \
record twice this week, and we'll look at them next time. I'd like to keep doing this kind of \
work at each visit.
[00:04:39] Client: Okay. Thanks, this helped.
[00:04:42] Therapist: Take care, Jordan.
[00:04:58] Therapist: Note for the record. Client alert and oriented times four, well groomed, \
good eye contact. Speech normal rate and volume. Mood anxious, affect congruent, mildly \
constricted. Thought process linear and goal directed. No hallucinations or delusions. Denies \
SI and HI. Cognition intact. Insight and judgment good. Protective factors: employed, \
supportive partner, engaged in treatment. Overall acute risk is low. Generalized anxiety \
worsening, ADHD stable. Sertraline increase discussed, benefits, side effects, and the \
alternative of no change; client agreed. Continue Adderall XR 20. MAPS checked today before \
the refill, no early fills, no other prescribers. No labs today. Psychotherapy from 10:14 to \
10:55, 41 minutes. Billing 99214 plus 90836.
"""


# A visit that is mostly listening. After a short medication check and the
# risk screen, the clinician asks two open questions about the client's
# mother, who died, and otherwise reflects what the client says. No technique
# is named, no rating is asked for, no assignment is given. The therapy runs
# from 1:40 to 40:30: 2330 seconds, 39 minutes. The clinician dictates the
# window and the codes, no minutes.
SUPPORTIVE_ONLY = """\
[00:00:04] Therapist: Hi Casey. Are you at home today?
[00:00:09] Client: Yes, at home.
[00:00:12] Therapist: Are you still taking the sertraline every morning?
[00:00:16] Client: Every morning. No problems with it.
[00:00:21] Therapist: Any nausea, headaches, trouble sleeping from it?
[00:00:26] Client: No. Sleep is bad, but that isn't the medicine.
[00:00:58] Therapist: Okay, we'll keep it the same. Any thoughts of hurting yourself or \
anyone else, or that you'd be better off dead?
[00:01:05] Client: No. I miss her, but no.
[00:01:40] Therapist: You said you miss her. What has it been like since your mother died?
[00:01:52] Client: Quiet. Too quiet. I keep picking up the phone on Sunday mornings to call \
her, and then I remember.
[00:08:30] Therapist: Sunday mornings were your time with her.
[00:08:38] Client: Every week. She'd tell me about her garden and I'd pretend to care about \
the tomatoes. Now I'd give anything to hear about the tomatoes.
[00:15:20] Therapist: It sounds like you're carrying a lot of this on your own.
[00:15:28] Client: My brother doesn't want to talk about her. He says it's better to keep \
busy. So I don't bring it up.
[00:22:10] Therapist: That sounds lonely.
[00:22:16] Client: It is. Most nights I lie there and go over the hospital, what I should \
have asked the doctors.
[00:27:40] Therapist: What do you miss most about her?
[00:27:48] Client: Her laugh. She laughed at her own jokes before she got to the end of \
them. Nobody else does that.
[00:34:50] Therapist: You smiled just now, talking about her laugh.
[00:34:58] Client: I did. I kept some of her tomato plants going on my balcony. It felt good \
to talk about her today. I haven't said her name out loud in a while.
[00:40:30] Therapist: I'm glad you did. Let's keep some time for this at each visit. I'll see \
you in four weeks, and call the office if anything changes.
[00:40:40] Client: Thank you. I will.
[00:41:05] Therapist: Note for the record. Client alert and oriented, casually dressed, \
tearful at times. Speech normal. Mood "quiet", affect tearful and congruent. Thought process \
linear. No hallucinations or delusions. Denies SI and HI. Insight and judgment good. Grief \
after the death of their mother. Depression stable on sertraline. Continue sertraline 50 \
milligrams every morning. Return in four weeks. Psychotherapy from 3:02 to 3:41. Billing \
99214 plus 90836.
"""

# Therapy that is brief psychoeducation on sleep hygiene, named as such, with
# one thing to try. The clinician says nothing about a goal or about how often
# therapy will continue.
THERAPY_PLAN_NOT_STATED = """\
[00:00:04] Therapist: Hi Alex. Are you at home today?
[00:00:07] Client: Yes, at home.
[00:00:10] Therapist: How has the escitalopram been?
[00:00:13] Client: Fine. Mood is better than it was. It's the sleep that's bad. It takes me \
forever to fall asleep.
[00:00:21] Therapist: Any side effects from it, nausea, headaches?
[00:00:25] Client: No.
[00:00:28] Therapist: Okay, we'll keep it the same. Tell me about a usual night.
[00:00:33] Client: I get into bed, scroll on my phone until I'm tired, and then I'm wide \
awake. On weekends I sleep in until whenever.
[00:03:10] Therapist: Let me give you some psychoeducation on sleep hygiene, because a few \
of those habits work against you. Your body clock sets itself by when you get up, so a wake \
time that moves around on weekends keeps resetting it.
[00:03:30] Client: So sleeping in makes it worse?
[00:06:40] Therapist: It can. The light from the phone also tells your brain it's daytime, \
and scrolling keeps you alert. The bed works best when it's only for sleep.
[00:06:55] Client: I didn't know the phone thing mattered that much.
[00:12:20] Therapist: It matters more than most people think. What questions do you have?
[00:12:28] Client: What if I can't fall asleep without it?
[00:16:45] Therapist: Then get up, sit somewhere dim, and go back to bed when you feel \
sleepy. This week, try getting up at the same time every day, weekends too, and leave the \
phone charging in the kitchen.
[00:17:02] Client: Okay. I'll try it.
[00:21:00] Therapist: Good. Any thoughts of hurting yourself or anyone else?
[00:21:05] Client: No.
[00:21:08] Therapist: I'll see you in six weeks. Call the office if anything gets worse.
[00:21:14] Client: Thanks.
[00:21:40] Therapist: Note for the record. Client alert and oriented, casually dressed. \
Speech normal. Mood "better", affect full. Thought process linear. No hallucinations or \
delusions. Denies SI and HI. Insight and judgment good. Depression improving. Insomnia, \
trouble falling asleep. Continue escitalopram 10 milligrams every morning. Psychoeducation \
on sleep hygiene, eighteen minutes. Return in six weeks. Billing 99214 plus 90833.
"""
