# Soul Stage 2 GO/NO-GO Gate

## Status

Prepared for manual evaluation.

## Required Scenarios

The Stage 2 gate is passed only when all scenarios below hold in real use:

1. `Silence -> outreach`
   After several hours of silence, the companion sends at most one proactive
   message when `social_hunger` crosses threshold and the boundary governor
   allows it.

2. `Response -> settle`
   If the user replies inside the outreach window, the result is `response`,
   `social_hunger` drops, and no repeat outreach happens the same day.

3. `Daytime ignore -> cooldown`
   If the user does not reply during a high-availability daytime outreach
   window, the result is `ignored` and ignored-cooldown is applied.

4. `Nighttime silence -> no outreach`
   At night, outreach must be blocked even if `social_hunger` is high.

5. `Daily budget hard limit`
   The companion must not send more than one outreach per day, even if a later
   tick would otherwise qualify.

6. `Debug surface`
   `get_soul_state` must expose initiative budget, last outreach result,
   cooldown, and presence/phase data needed to inspect failures.

## GO Criteria

Stage 2 may move forward when:

- all six scenarios pass in automated scenario tests
- all six scenarios are also observed in manual use over 3-5 days
- no proactive behavior is perceived as spammy or obviously mistimed

## NO-GO Criteria

Do not proceed to Stage 3 if any of the following are observed:

- more than one outreach per day
- outreach at night
- repeated outreach after a same-day response
- ignored vs timing_miss misclassification
- missing or misleading debug state
