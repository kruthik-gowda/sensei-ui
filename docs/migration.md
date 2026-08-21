# Migrating from the scheduled reviewer

The launchd job `com.kruthikgowda.sensei-review` posted reviews autonomously at
11:00 daily. It is replaced by `sensei-ui`, which is on-demand and posts only
what a reviewer has approved.

Its Claude adjudication pass survives as `sensei_ui/verify.py`, changed in one
way: it annotates findings rather than dropping them, so a wrong rejection stays
visible and recoverable.

To retire it:

    launchctl unload ~/Library/LaunchAgents/com.kruthikgowda.sensei-review.plist
    rm ~/Library/LaunchAgents/com.kruthikgowda.sensei-review.plist

Existing snapshots in `~/.sensei/reviews/` are unaffected and remain readable.

## Before you retire it

`sensei-ui` has not yet been exercised end-to-end against a live merge request. While generation and the HTTP API are covered by tests, no review comment has actually been posted to GitLab through the new system yet. To de-risk retirement, run at least one complete review cycle through `sensei-ui` on a real merge request, confirm that the thread and comments appear correctly on GitLab, and verify that editing/dismissing findings works as expected. Only after confirming this flow works reliably should you unload and remove the scheduled job.
