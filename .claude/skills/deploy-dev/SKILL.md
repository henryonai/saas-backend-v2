---
name: deploy-dev
description: Slipway step 3. Deploy the current branch to the dev stack (saas-dev) through the deploy workflow, then run the e2e check.
disable-model-invocation: true
---

# Deploy to dev (Slipway step 3)

Only `saas-dev`. Never start a prod deploy: that's the user's.

1. **Uncommitted work.** Run `git status`. The workflow deploys the pushed branch, so uncommitted changes won't be in it. If there are any, ask the user whether to commit them first (a work-in-progress commit is fine for a dev try-out if they agree).
2. **Behind main.** Run `git fetch origin` and `git merge-base --is-ancestor origin/main HEAD`. If the branch doesn't contain `origin/main`, deploying it removes from dev whatever `main` added since. Ask the user to merge `origin/main` first, or to confirm deploying anyway.
3. **Another deploy running.** Run `gh run list --workflow deploy.yml --limit 5`. Other people and sessions share `saas-dev`. If a run is queued or in progress, tell the user: a new run waits for it, and CloudFormation won't take a second update meanwhile.
4. **Push.** If the branch has commits the remote doesn't, run `git push -u origin <branch>`.
5. **Deploy.** Run `gh workflow run deploy.yml --ref <branch> -f environment=dev`. Find the run with `gh run list --workflow deploy.yml --branch <branch> --limit 1`, and give the user its URL.
6. **Wait.** Watch it in the background with `gh run watch <id> --exit-status`. It runs CI first, then deploys the backend and the web app.
7. **If it fails**, read the failed step's log (`gh run view <id> --log-failed`), explain the cause, and suggest the fix. Don't retry blindly.
8. **When it succeeds**, run the e2e skill.
