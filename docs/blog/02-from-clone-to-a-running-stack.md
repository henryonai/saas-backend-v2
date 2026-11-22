<!--
Before publishing:
- [ ] Update "Cleaning up" if you delete the acme-dev stack first. A checklist you've actually run is more useful, and you may find a step that's missing.
- [ ] Change the date to the day you publish, and replace the relative post links with published URLs.
-->

# From Clone to a Running Stack: Deploying the SaaS Template on AWS

Oct 30, 2026

I took a fresh copy of [saas-backend-v2](https://github.com/henryonai/saas-backend-v2), renamed it and deployed it to an empty AWS account. It worked: the smoke tests passed 161 checks on the backend and 156 on the assistant. It only worked after six small fixes, though, and all of them are now in the repository.

This post gives you the exact steps, what broke on the way, and what the test didn't cover. If you're new to the template, start with [what's in it and who it's for](./01-whats-in-the-template.md).

## What you need

These steps create real AWS resources in your account, so use a dev or sandbox account and set a budget alert.

| You need | Why |
| --- | --- |
| An AWS account, with the AWS CLI signed in | Set `AWS_PROFILE` and `AWS_REGION`, because the smoke tests read the region from your session |
| AWS SAM CLI | Builds and deploys the stack |
| `uv` and Python 3.13 | Installs the Python packages and runs the scripts |
| Node.js 22 and `pnpm` | Builds the web app |
| A Stripe test key | Billing needs it, and the smoke test refuses a live key |
| An OpenRouter key | Only for the assistant, whose default model runs through OpenRouter |

## The steps

These examples name the project `acme`, so use your own name throughout.

1. **Copy the repository and rename it.** This renames the packages, stacks, SSM parameters and event source, so two copies never collide in one account. The template is MIT-licensed: keep the `LICENSE` notice in your copy, and add your own beside it if you like.

   ```bash
   git clone https://github.com/henryonai/saas-backend-v2 acme && cd acme
   uv run python scripts/rename-project.py acme --title "Acme"
   uv sync --all-packages && (cd web && pnpm install)
   git diff    # review, then commit
   ```
2. **Run the checks.** None of them need an AWS account, so a failure here costs you nothing.

   ```bash
   uv run pytest
   ./scripts/export-requirements.sh --check
   uvx ruff check .
   uvx cfn-lint template.yaml .github/deploy-role.yaml
   (cd web && pnpm gen:api && pnpm typecheck && pnpm lint && pnpm test && pnpm build)
   ```
3. **Put a Python with pip on your `PATH`.** `sam build` needs Python 3.13 with pip. The macOS Python and the project's `.venv` both fail, so use uv's own Python, or run `sam build --use-container` in step 5.

   ```bash
   export PATH="$(dirname "$(uv python find --system 3.13)"):$PATH"
   ```
4. **Store your Stripe test key.** The command waits silently for you to paste the key and press Enter, so the key never appears in your shell history.

   ```bash
   read -rs STRIPE_KEY && aws ssm put-parameter --name /acme/dev/stripe-secret-key \
       --type SecureString --value "$STRIPE_KEY"; unset STRIPE_KEY
   ```
5. **Deploy the backend.** This first deploy leaves out the assistant, because the assistant's code goes into a bucket that this deploy creates.

   ```bash
   ./scripts/export-requirements.sh && sam build
   sam deploy --config-env dev
   ```
6. **Set up Stripe.** The script creates the prices, the customer portal and the webhook, then saves the webhook's signing secret in SSM. You don't create that secret yourself.

   ```bash
   uv run python scripts/stripe-setup.py --stack acme-dev
   ```
7. **Publish the web app.** It goes to the address in the stack's `WebUrl` output.

   ```bash
   (cd web && ./scripts/deploy.sh acme-dev)
   ```
8. **Run the smoke test.** It creates three test users and calls every endpoint. It passed 161 checks on my copy.

   ```bash
   uv run scripts/smoke-test.py --stack acme-dev
   ```
9. **Add the assistant, if you want it.** Store the OpenRouter key, deploy again with the agent's code, and test it. This smoke test adds three more users and costs a few cents of model tokens. It passed 156 checks on my copy.

   ```bash
   read -rs OPENROUTER_KEY && aws ssm put-parameter --name /acme/dev/openrouter-api-key \
       --type SecureString --value "$OPENROUTER_KEY"; unset OPENROUTER_KEY
   sam deploy --config-env dev \
     --parameter-overrides "$(uv run python scripts/package-assistant.py --config-env dev)"
   uv run scripts/smoke-assistant.py --stack acme-dev
   ```

That's a working stack. Open `WebUrl` and sign up. Stripe's test card, `4242 4242 4242 4242`, works at checkout.

## What broke on a clean copy

The application itself was fine. All six problems were in the setup scripts and instructions, in places I had stopped noticing because I never run them from zero.

| What went wrong | Why | What happens now |
| --- | --- | --- |
| The rename script's next steps were wrong | They said to create a random webhook secret by hand, but `stripe-setup.py` writes that secret now | It prints the real steps, in order |
| The README's setup step led nowhere | It pointed to a webhook secret that its own section didn't describe | It lists the first deploy's commands |
| `sam build` failed | It needs Python 3.13 with pip, and the macOS Python and `.venv` don't qualify | The README shows how to fix your `PATH`, or to use `--use-container` |
| `package-assistant.py` crashed with a traceback | An account where `sam deploy` has never run has no bucket for the zip | It says to deploy once without the assistant, or to pass `--bucket` |
| The smoke test's web check failed with `AccessDenied` | The web app hadn't been published yet | It says to run `web/scripts/deploy.sh` |
| The prod instructions were out of date | They still said to create the webhook secret | They say to store the Stripe key, and run `stripe-setup.py` after the first deploy |

Three of these came from the same change: billing moved to Stripe only, and the old setup instructions stayed behind. Tests can't catch that, because no test reads the README. If you publish a template, deploy it from an empty account before anyone else has to.

## What this test didn't cover

I deployed from a terminal, to one dev stack. Three things are still unproven on a clean copy.

- **The GitHub deploy workflow.** I didn't create a new repository, so a new copy's deploy role, GitHub environments and `deploy.yml` weren't exercised. The workflow does run on the original repository, and the README's "CI/CD" section explains how to set it up.
- **Production.** The two-run prod deploy and the canary rollback have never run on a real prod stack.
- **Someone else's machine.** I ran this on my own setup, so you may find a seventh problem. If you do, please [open an issue](https://github.com/henryonai/saas-backend-v2/issues).

## Cleaning up

Deleting the stack doesn't remove everything, on purpose: the template keeps data so that a mistake can't destroy it. My test stack is still running, so treat this as a checklist I haven't run yet. Before you delete the stack, empty the web and exports buckets, because CloudFormation can't delete a bucket that still has objects in it.

Delete these by hand afterward:

| What stays behind | Where |
| --- | --- |
| The DynamoDB tables | DynamoDB console |
| The Cognito user pool | Cognito console |
| The assistant's files and checkpoint buckets | S3 console |
| Your keys and the webhook secret | SSM Parameter Store, under `/acme/dev/` |
| The test prices, portal configuration and webhook | Your Stripe dashboard, in test mode |

## What's next

You now have a running stack, and the next posts show how to build on it: [how multi-tenancy works here](./03-how-multi-tenancy-works.md), [how to build your first module](./04-build-your-first-module.md), and [how to add your own AI agent](./05-add-your-own-ai-agent.md).
