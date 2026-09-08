# EC2 deployment

The simplest possible setup: one Amazon Linux 2023 box, no load balancer, no containers,
no reverse proxy or TLS. `api`/`worker`/`scheduler`/`web` run as plain systemd
services and are reached directly on their ports. Mirrors the pattern the
sibling `lp-deploy` project already uses for its own EC2 box.

Postgres and blob storage are unchanged -- this points at the same Supabase
project your local `.env` already uses. Nothing AWS-native is required beyond
the instance itself.

## 1. Launch the instance

In the EC2 console, **Launch instance**:

- **AMI**: Amazon Linux 2023 (the console default)
- **Instance type**: `t3.small` (2 vCPU / 2 GB RAM -- `t3.micro`'s 1 GB is
  tight once `next build`, three Python processes, and ffmpeg are all
  competing for memory)
- **Key pair**: create or select one you can SSH in with
- **Network settings / security group**: allow inbound
  - SSH (22) from your IP only
  - Custom TCP 3000 (web) from anywhere
  - Custom TCP 8000 (api) from anywhere
- **Storage**: bump the default 8 GB gp3 volume to 20 GB

After it's running, **allocate an Elastic IP and associate it with the
instance** -- a plain public IP changes on stop/start, and both
`web/.env.local` and `backend.env` below need a stable one.

## 2. First-time setup

SSH in as `ec2-user` (Amazon Linux's default login, not `ubuntu`) and create
the two `/etc` files `setup.sh` refuses to run without
(copy real values from your local `.env`, but point every URL at the Elastic
IP from step 1 instead of `localhost`):

```bash
sudo mkdir -p /etc/creative-strategist
sudo nano /etc/creative-strategist/backend.env   # from backend.env.example
sudo nano /etc/creative-strategist/web.env       # from web.env.example
sudo chmod 600 /etc/creative-strategist/*.env
```

Then bootstrap the script itself (it hasn't cloned the repo yet, so it isn't
on the box until this first run) and run it:

```bash
git clone https://github.com/marketing-brain/creative-strategist.git /tmp/cs-bootstrap
sudo bash /tmp/cs-bootstrap/deploy/ec2/setup.sh
```

This first run clones the real repo into `/opt/creative-strategist` (owned by
the `cs` system user it creates) and then **stops with an error**, because
`web/.env.local` -- which must live *inside* that checkout, so it can only be
created after the clone exists -- isn't there yet:

```bash
sudo nano /opt/creative-strategist/web/.env.local
# NEXT_PUBLIC_API_URL=http://<elastic-ip>:8000
# NEXT_PUBLIC_CLERK_PUBLISHABLE_KEY=<production Clerk publishable key>
# NEXT_PUBLIC_CLERK_SIGN_IN_URL=/sign-in
# NEXT_PUBLIC_CLERK_SIGN_IN_FALLBACK_REDIRECT_URL=/
# NEXT_PUBLIC_CLERK_SIGN_UP_FALLBACK_REDIRECT_URL=/
```

The three `NEXT_PUBLIC_CLERK_SIGN_IN*`/`SIGN_UP*` lines matter: without them Clerk
doesn't know the app has its own `/sign-in` page and falls back to redirecting
through its hosted Account Portal, which can't complete the round trip back to
a raw IP -- users get stuck on Clerk's own UI after logging in instead of
landing back in the app.

Run the same command again to finish (it's idempotent -- the clone is
already done, so this pass installs deps, migrates, builds, and starts all
four services):

```bash
sudo bash /opt/creative-strategist/deploy/ec2/setup.sh
```

## 3. Verify

```bash
sudo systemctl status cs-api cs-worker cs-scheduler cs-web
curl http://localhost:8000/health
```

Then from your own machine: `http://<elastic-ip>:3000`.

## 4. Deploying an update

```bash
ssh into the box
sudo bash /opt/creative-strategist/deploy/ec2/setup.sh
```

Same script, idempotent: pulls latest `main`, reinstalls deps, re-migrates,
rebuilds the web bundle, restarts all four services.

## Known limitations of "simplest possible"

- **No TLS.** Everything is plain HTTP on raw ports -- fine for testing with
  real data, not something to hand out as a public product URL. Adding a
  domain + Nginx + certbot is the natural next step if this needs to look
  like a real product later, not required to get it running today.
- **No auto-restart on instance failure, no scaling, no zero-downtime
  deploys.** `setup.sh restart`s all four services, so an update causes a
  few seconds of downtime. Fine for a single low-traffic box.
- **The Elastic IP is the only piece of AWS-native infra involved** -- no
  ALB, no ECS, no VPC design beyond the security group. That's the entire
  point of this setup versus the App Runner/Fargate sketch from before.
