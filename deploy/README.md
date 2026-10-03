# Deploying QA Pilot (free, always on)

QA Pilot runs on one Linux server with Docker Compose (`docker-compose.prod.yml`). The recommended free host is an
**Oracle Cloud "Always Free" ARM VM**: it stays on when your PC is off, costs nothing, and has enough memory for the
worker's Chromium.

After setup you get two HTTPS links. They need no domain, because [sslip.io](https://sslip.io) maps a name to your
server's IP, and Caddy gets Let's Encrypt certificates for them automatically:

- `https://qapilot-<ip-with-dashes>.sslip.io`: the QA Pilot dashboard.
- `https://shop-<ip-with-dashes>.sslip.io`: the demo shop. Use this URL as a project's base URL.

## 1. Create the server (Oracle Cloud, about 15 minutes, once)

1. **Sign up at https://signup.cloud.oracle.com.**
   - A card is needed for identity verification only. Always Free resources are never charged.
   - Pick a home region near you (e.g. India West (Mumbai) or India South (Hyderabad)). It can't be changed later.
2. **Create the VM.** Go to *Compute → Instances → Create instance*:
   - **Image:** Canonical Ubuntu 24.04.
   - **Shape:** *Change shape → Ampere → VM.Standard.A1.Flex*, **2 OCPUs and 12 GB memory** (Always Free allows up to 4 and 24).
   - **Networking:** keep the defaults (new VCN with a public subnet); *Assign a public IPv4 address* = yes.
   - **SSH keys:** *Generate a key pair for me* → **download the private key** (e.g. `qapilot.key`).
   - Then *Create*.

   If Oracle says "Out of capacity", try another availability domain in the same form, or try again later.
3. **Open the web ports.** Go to *Instance → Primary VNIC → Subnet → Default security list → Add ingress rules*:
   - Source `0.0.0.0/0`, TCP, destination port **80**.
   - Same again for port **443**.
4. **Copy the instance's public IP address.**

Oracle may reclaim Always Free VMs that stay almost idle for a week. Upgrading the account to *Pay As You Go*
prevents that, and is still free as long as you stay within the Always Free limits.

## 1b. Or: Azure for Students (college email, no card)

1. **Sign up** at https://azure.microsoft.com/free/students with your college email; it gives about $100 of credit.
   Nothing is ever charged automatically: when the credit or the 12 months run out, the VM is just switched off.
2. **Create the VM:** *Virtual machines → Create → Azure virtual machine*.
   - **Region:** Central India (or South India). If it's not allowed for your subscription, pick another region.
   - **Image:** Ubuntu Server 24.04 LTS (x64).
   - **Size:**
     - **Standard_B2s** (2 vCPU, 4 GB): smooth, and roughly 3 months of credit;
     - **Standard_B1ms** (1 vCPU, 2 GB): slower, but roughly twice as long.
   - **Authentication:** SSH public key, username `azureuser`, *Generate new key pair* → download the `.pem` file.
   - **Inbound ports:** allow **HTTP (80)**, **HTTPS (443)** and **SSH (22)**.
   - **Disk:** Standard SSD, 30 GB is enough. Then *Review + create*.
3. **Copy the VM's public IP** from its *Overview* page, then deploy with
   `bash deploy/push.sh azureuser@<public-ip> <path-to-key.pem>`.

To save credit, stop the VM from the portal when you don't need it (a *Stopped (deallocated)* VM costs almost
nothing), and start it again before a demo. Its IP can change after a stop unless you made it *Static* in its
Public IP settings.

## 2. Deploy (from your PC, Git Bash)

```sh
bash deploy/push.sh ubuntu@<public-ip> ~/Downloads/qapilot.key        # Oracle
bash deploy/push.sh azureuser@<public-ip> ~/Downloads/qapilot_key.pem  # Azure
```

The script:
- uploads the code (never your `.env`);
- copies only the LLM key lines from your local `.env` to the server;
- runs `deploy/setup.sh` on the server, which does the following:
  - installs Docker, opens ports 80/443 in the VM's firewall and adds swap if memory is small;
  - generates fresh secrets in the server's `.env` (JWT secret, credentials key, demo-shop reset token);
  - sets the public limits below and starts the stack.

The first build takes about 10–15 minutes, because the browser image is ~2 GB. When it's done, the script prints the
two URLs. To update later, run the same command again: data in MongoDB and the screenshot volume is kept.

## Public-server limits (server `.env`)

| Setting | Default on the server | Why |
|---|---|---|
| `MAX_AGENT_RUNS_PER_USER_PER_DAY` | 2 | The LLM key's free quota is shared by everyone who signs up |
| `MAX_AGENT_RUNS_PER_DAY` | 6 | Whole-site AI-run budget (Groq free tier: ~200k tokens/day). Replays don't count |
| `MAX_TESTS_LIMIT` | 6 | Max tests per run |
| `REGISTRATION_CODE` | *(empty = open)* | Set it to make sign-up invite-only |
| `ALLOWED_TARGET_HOSTS` | *(empty = any site)* | e.g. `sslip.io` to allow testing only the demo shop |
| `DEMO_SHOP_RESET_TOKEN` | random | Needed for `POST /reset` on the public shop (`X-Reset-Token` header) |

After editing the server's `.env`, run `cd ~/qa-pilot && docker compose -f docker-compose.prod.yml up -d` to apply it.

## Demo shop safeguards

- A banner on every page says the site is intentionally buggy, and no real information should be entered.
- It is hidden from search engines: `robots.txt` disallows everything, plus a `noindex` meta tag and `X-Robots-Tag` header.
- The planted bugs are logic and accessibility bugs only. There's no XSS or SQL injection (no database, and no user
  text is written as HTML); passwords are hashed and card numbers are never stored.
- `/reset` and the UI-variant switch need the reset token. The data also resets automatically every night
  (03:00 UTC).
- **Limits:**
  - 600 requests per minute per IP;
  - 500 accounts, 20 products per cart, quantities up to ±999;
  - 500 characters per field;
  - the most recent 1000 orders and 200 messages are kept.
- Data lives only in memory; the shop doesn't use QA Pilot's MongoDB.

## Operating it

```sh
ssh -i qapilot.key ubuntu@<ip>
cd ~/qa-pilot
docker compose -f docker-compose.prod.yml ps              # status
docker compose -f docker-compose.prod.yml logs -f worker  # watch runs
docker compose -f docker-compose.prod.yml restart api     # restart one service
```

Backup the database: `docker compose -f docker-compose.prod.yml exec mongo mongodump --archive > backup.archive`.
